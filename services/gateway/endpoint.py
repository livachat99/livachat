from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from gateway.auth import Authenticator
from gateway.codec import InboundDecodeGuard, WireCodec
from gateway.connection import ConnectionHandler, ConnectionManager
from gateway.heartbeat import HeartbeatTracker
from gateway.seq import InboundSeqGuard, OutboundSeqAllocator
from shared.utils.errors import AuthError

logger = logging.getLogger("livachat.gateway.endpoint")

router = APIRouter()


def _make_heartbeat(cfg) -> HeartbeatTracker:
    return HeartbeatTracker(
        heartbeat_interval_s=cfg.session.heartbeat_interval_s,
        heartbeat_timeout_s=cfg.session.heartbeat_timeout_s,
    )


@router.websocket("/v1/ws/stream")
async def ws_stream(
    ws: WebSocket,
    token: str = Query(...),
    device_id: str = Query(...),
) -> None:
    """Main WebSocket endpoint for LivaChat streaming.

    Query parameters:
    - ``token``: authentication token
    - ``device_id``: client UUID v4
    """

    # -- auth (must happen BEFORE accept) ---------------------------------
    authenticator: Authenticator = ws.app.state.authenticator
    try:
        principal = authenticator.verify(token, device_id)
    except AuthError:
        raise HTTPException(status_code=401, detail="authentication failed")

    # -- validate device_id is UUID v4 ------------------------------------
    try:
        uuid.UUID(device_id, version=4)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="invalid device_id format")

    # -- build per-connection components ----------------------------------
    outbound_seq = OutboundSeqAllocator()
    seq_guard = InboundSeqGuard()
    codec = WireCodec(outbound_seq)
    cfg = getattr(ws.app.state, "config", None)
    heartbeat = _make_heartbeat(cfg) if cfg else _make_heartbeat(_default_heartbeat_cfg())
    decode_guard = InboundDecodeGuard()

    # -- recording sink (created early, injected into orchestrator) ---------
    recording_service = getattr(ws.app.state, "recording_service", None)
    recording_sink = None
    if recording_service is not None:
        try:
            recording_sink = await recording_service.acquire(device_id)
        except Exception:
            logger.warning("recording sink creation failed", exc_info=True)
    if recording_sink is None:
        from recording import NoOpRecordingSink
        recording_sink = NoOpRecordingSink()

    # -- create orchestrator with DI --------------------------------------
    orchestrator = None
    agent_publisher = getattr(ws.app.state, "agent_publisher", None)
    try:
        from media.opus import OpusDecoder, OpusEncoder

        cfg = ws.app.state.config

        # 输出编码器采样率 = 媒体默认。omni provider 级的 output_sample_rate 覆盖钩子已随
        # livetranslate omni 配置一并移除（2026-09-14）；lecture 的 24kHz 编码器由
        # LectureTranslateEngine 自带（cfg.translate.output_sample_rate），不走这里。
        output_sample_rate = cfg.media.audio.default_sample_rate
        omni_service = getattr(ws.app.state, "omni_service", None)

        opus_enc = OpusEncoder(
            sample_rate=output_sample_rate,
            frame_ms=cfg.media.audio.opus_frame_ms,
            bitrate=cfg.media.audio.opus_bitrate,
            channels=cfg.media.audio.channels,
        )
        opus_dec = OpusDecoder(
            sample_rate=cfg.media.audio.default_sample_rate,
            frame_ms=cfg.media.audio.opus_frame_ms,
            channels=cfg.media.audio.channels,
        )

        # 全模态（omni）是会话内模式（session.change(mode=omni)），不再是连接级路由：
        # 恒建 DialogOrchestrator，把 omni 服务与开流参数一并传入（无人装配即不可切换）。
        # 见 docs/fd_pipeline_omni_mode.md §4 D3/D5。
        import os

        from session.orchestrator import DialogOrchestrator

        orchestrator = DialogOrchestrator(
            handler=None,
            codec=codec,
            session_mgr=getattr(ws.app.state, "session_mgr", None),
            asr_service=getattr(ws.app.state, "asr_service", None),
            llm_service=getattr(ws.app.state, "llm_service", None),
            tts_service=getattr(ws.app.state, "tts_service", None),
            opus_encoder=opus_enc,
            opus_decoder=opus_dec,
            system_prompt=getattr(ws.app.state, "system_prompt", ""),
            translate_prompt=getattr(ws.app.state, "translate_prompt", ""),
            generation_opts=cfg.llm.generation.model_dump(),
            chat_store=ws.app.state.chat_store,
            pipeline_mode=os.environ.get("LIVACHAT_PIPELINE_MODE", "full"),
            translate_cfg=getattr(ws.app.state, "translate_cfg", None),
            recording_sink=recording_sink,
            agent_publisher=agent_publisher,
            omni_service=omni_service,
            omni_system_prompt=getattr(ws.app.state, "omni_system_prompt", ""),
            # 取值迁移自原连接级 omni 分支（不可用 cfg.llm.generation）
            omni_generation_opts=cfg.omni.generation.model_dump(),
            omni_voice=(cfg.omni.providers[cfg.omni.active].voice or "")
            if cfg.omni.active and cfg.omni.active in cfg.omni.providers
            else "",
            pipeline_sample_rate=output_sample_rate,
            # avdetector（chat 模式旁路监测；未启用时 None → 路径全不激活）
            avdetector_service=getattr(ws.app.state, "avdetector_service", None),
            avdetector_system_prompt=getattr(ws.app.state, "avdetector_system_prompt", ""),
            avdetector_cfg=getattr(ws.app.state, "avdetector_cfg", None),
        )
    except Exception:
        logger.warning("orchestrator creation failed — running in stub mode", exc_info=True)

    handler = ConnectionHandler(
        ws=ws,
        principal=principal,
        codec=codec,
        seq_guard=seq_guard,
        outbound_seq=outbound_seq,
        heartbeat=heartbeat,
        decode_guard=decode_guard,
        orchestrator=orchestrator,
        sink=recording_sink,
    )

    if orchestrator is not None:
        orchestrator._handler = handler

    # -- register ---------------------------------------------------------
    conn_mgr: ConnectionManager = ws.app.state.connection_manager
    await conn_mgr.register(device_id, handler)

    try:
        await ws.accept()
        await handler.run()
    except WebSocketDisconnect:
        logger.info("client disconnected", extra={"device_id": device_id})
    except Exception:
        logger.exception("unhandled exception in ws_stream", extra={"device_id": device_id})
    finally:
        await conn_mgr.unregister(device_id, handler)


def _default_heartbeat_cfg():
    """Fallback config for heartbeat when app config is unavailable."""
    from shared.config.models import SessionConfig

    return SessionConfig()
