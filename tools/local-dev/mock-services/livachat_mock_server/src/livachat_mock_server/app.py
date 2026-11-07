from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from livachat_protocol.generated.client_message import (
    ClientAudio,
    ClientInterrupt,
    ClientPing,
    ClientPong,
    ClientSessionEnd,
    ClientSessionStart,
    ClientVideo,
)
from livachat_protocol.generated import (
    SessionStatusCode,
    StatusReasonCode,
    SessionEndReason,
    VadState,
)

from .config import Scenario, ServerConfig
from .protocol import (
    parse_client_message,
    dump_server_message,
    make_session_id,
    build_session_ready,
    build_status,
    build_ai_video_ack,
    build_pong,
    build_error,
    build_session_ended,
    build_gallery_ai_text,
    make_message_id,
)
from .media_fixtures import MOCK_GALLERY_JPEG_B64
from .scenarios import run_response_flow, send_interim_transcriptions
from .session_store import SessionContext, SessionStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
logger.addHandler(_handler)
logger.propagate = False


def _ts() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def create_app(config: ServerConfig | None = None) -> FastAPI:
    if config is None:
        config = ServerConfig()

    app = FastAPI(title="LivaChat Mock Server", version="0.1.0")
    store = SessionStore(ttl_s=config.session_ttl_s)
    scenario = config.scenario

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/healthz_token")
    async def healthz_token(token: str = Query(...), device_id: str = Query(...)):
        if token != config.dev_token:
            return JSONResponse({"ok": False}, status_code=401)
        try:
            uuid.UUID(device_id)
        except ValueError:
            return JSONResponse({"ok": False}, status_code=401)
        return {"ok": True}

    @app.post("/v1/auth/token")
    async def auth_token():
        return {
            "token": config.dev_token,
            "token_type": "bearer",
            "expires_in": 86400,
        }

    @app.websocket("/v1/ws/stream")
    async def ws_stream(
        websocket: WebSocket,
        token: str = Query(...),
        device_id: str = Query(...),
    ):
        # --- auth ---
        if scenario == Scenario.AUTH_FAILED:
            await websocket.close(code=4001, reason="auth_failed")
            return

        if token != config.dev_token:
            await websocket.close(code=4001, reason="invalid_token")
            return

        try:
            uuid.UUID(device_id)
        except ValueError:
            await websocket.close(code=4001, reason="invalid_device_id")
            return

        await websocket.accept()
        logger.info("[%s] CONNECT device_id=%s scenario=%s", _ts(), device_id, scenario)

        ctx: SessionContext | None = None

        async def _send_msg(msg) -> None:
            raw = dump_server_message(msg)
            logger.info("[%s] SEND  %s seq=%s", _ts(), type(msg).__name__, getattr(msg, "seq", "-"))
            if scenario == Scenario.SLOW_NETWORK:
                import random
                ms = random.randint(
                    config.slow_network_min_delay_ms,
                    config.slow_network_max_delay_ms,
                )
                await asyncio.sleep(ms / 1000.0)
            await websocket.send_text(raw)

        try:
            # --- wait for session.start, discard everything else ---
            while True:
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                except asyncio.TimeoutError:
                    await websocket.close(code=4003, reason="session_start_timeout")
                    return

                msg = parse_client_message(raw)
                if not isinstance(msg, ClientSessionStart):
                    logger.info("[%s] DISCARD %s seq=%s (waiting for session.start)",
                                _ts(), type(msg).__name__, getattr(msg, "seq", "-"))
                    continue
                logger.info("[%s] RECV  %s seq=%s", _ts(), type(msg).__name__, getattr(msg, "seq", "-"))
                break

            restored = False
            session_id = make_session_id()

            if msg.payload.session_id:
                existing = store.get(msg.payload.session_id)
                if existing and scenario == Scenario.RESUME_SUCCESS:
                    restored = True
                    session_id = msg.payload.session_id
                    ctx = existing
                    ctx.touch()
                elif scenario == Scenario.RESUME_EXPIRED:
                    restored = False
                elif existing:
                    restored = True
                    session_id = msg.payload.session_id
                    ctx = existing
                    ctx.touch()
                else:
                    restored = False

            if ctx is None:
                ctx = SessionContext(
                    session_id=session_id,
                    device_id=device_id,
                    scenario=scenario,
                )
            else:
                ctx.scenario = scenario

            ctx.update_c2s_seq(msg.seq)
            store.put(ctx)

            # --- send session.ready + initial status ---
            await _send_msg(build_session_ready(ctx.next_s2c_seq, ctx.session_id, restored))

            if scenario == Scenario.SERVER_ERROR:
                await _send_msg(build_error(
                    ctx.next_s2c_seq, 1001, "ASR service unavailable", fatal=False, recoverable=True,
                ))

            await _send_msg(build_status(
                ctx.next_s2c_seq, SessionStatusCode.IDLE, StatusReasonCode.CONNECTED,
            ))
            await _send_msg(build_status(
                ctx.next_s2c_seq, SessionStatusCode.LISTENING, StatusReasonCode.CONNECTED,
            ))
            ctx.session_status = SessionStatusCode.LISTENING

            if scenario == Scenario.TEXT_ON_START:
                ctx.streaming_task = asyncio.create_task(
                    run_response_flow(
                        ctx=ctx,
                        scenario=Scenario.TEXT_ONLY,
                        config=config,
                        websocket_send=_send_msg,
                    )
                )

            if scenario == Scenario.RESUME_DROP and not ctx.dropped_once:
                ctx.dropped_once = True
                store.put(ctx)
                logger.info("resume_drop: closing first connection session_id=%s", ctx.session_id)
                await websocket.close(code=1012, reason="resume_drop")
                return

            if scenario == Scenario.RESUME_DROP and ctx.dropped_once:
                ctx.streaming_task = asyncio.create_task(
                    run_response_flow(
                        ctx=ctx,
                        scenario=Scenario.TEXT_ONLY,
                        config=config,
                        websocket_send=_send_msg,
                    )
                )

            # --- main message loop ---
            while True:
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=config.idle_timeout_s if scenario == Scenario.IDLE_TIMEOUT else config.ping_timeout_s,
                    )
                except asyncio.TimeoutError:
                    if scenario == Scenario.IDLE_TIMEOUT:
                        await websocket.close(code=4003, reason="idle_timeout")
                    elif scenario == Scenario.PING_TIMEOUT:
                        await websocket.close(code=4002, reason="ping_timeout")
                    else:
                        await websocket.close(code=4002, reason="ping_timeout")
                    return

                ctx.touch()
                msg = parse_client_message(raw)
                if not isinstance(msg, ClientAudio):
                    logger.info("[%s] RECV  %s seq=%s", _ts(), type(msg).__name__, getattr(msg, "seq", "-"))

                if not ctx.update_c2s_seq(msg.seq):
                    logger.warning("duplicate/out-of-order seq=%s last=%s", msg.seq, ctx.c2s_seq)
                    continue

                match msg:
                    case ClientPing():
                        if scenario != Scenario.PING_TIMEOUT:
                            await _send_msg(build_pong(ctx.next_s2c_seq, msg.seq))

                    case ClientPong():
                        logger.debug("received pong ping_seq=%s", msg.payload.ping_seq)

                    case ClientAudio():
                        await _handle_audio(ctx, msg, config, _send_msg, websocket)

                    case ClientVideo():
                        ctx.ack_frame_id = max(ctx.ack_frame_id, msg.payload.frame_id)
                        await _send_msg(build_ai_video_ack(
                            ctx.next_s2c_seq, msg.payload.frame_id, ctx.ack_frame_id,
                        ))
                        if scenario == Scenario.VIDEO_GALLERY and msg.payload.text:
                            await _send_msg(build_status(
                                ctx.next_s2c_seq,
                                SessionStatusCode.THINKING,
                                StatusReasonCode.ASR_FINAL,
                            ))
                            await _send_msg(build_gallery_ai_text(
                                ctx.next_s2c_seq,
                                "这是一张图片。",
                                make_message_id(),
                                MOCK_GALLERY_JPEG_B64,
                            ))
                            await _send_msg(build_status(
                                ctx.next_s2c_seq,
                                SessionStatusCode.LISTENING,
                                StatusReasonCode.TTS_FINISH,
                            ))

                    case ClientInterrupt():
                        logger.info("interrupt received reason=%s", msg.payload.reason)
                        # Signal any running streaming task to short-circuit, then
                        # await its cancellation so we observe its termination
                        # before resetting state for the next turn.
                        ctx.interrupted = True
                        await _cancel_streaming_task(ctx)
                        # Fully reset turn-scoped state so the next user utterance
                        # starts a fresh interim → response cycle. Without this
                        # reset, ctx.interrupted stays True (the in-flight task
                        # was cancelled before it could clear the flag) and every
                        # subsequent streaming loop short-circuits.
                        ctx.interrupted = False
                        ctx.silence_count = 0
                        ctx.recognised = False
                        ctx.session_status = SessionStatusCode.LISTENING
                        await _send_msg(build_status(
                            ctx.next_s2c_seq, SessionStatusCode.LISTENING, StatusReasonCode.INTERRUPTED,
                        ))

                    case ClientSessionEnd():
                        await _send_msg(build_session_ended(
                            ctx.next_s2c_seq, msg.payload.reason, ctx.session_id,
                        ))
                        store.remove(ctx.session_id)
                        logger.info("session ended session_id=%s reason=%s", ctx.session_id, msg.payload.reason)
                        return

                    case _:
                        logger.warning("unhandled message type: %s", type(msg).__name__)

        except WebSocketDisconnect:
            logger.info("[%s] DISCONNECT device_id=%s", _ts(), device_id)
        except Exception:
            logger.exception("ws handler error device_id=%s", device_id)
        finally:
            if ctx:
                await _cancel_streaming_task(ctx)
            # Keep session in store for resume within TTL; SessionStore.get()
            # handles expiry. Only remove on explicit session.end.
            if ctx and scenario == Scenario.RESUME_EXPIRED:
                store.remove(ctx.session_id)

    return app


async def _cancel_streaming_task(ctx: SessionContext) -> None:
    """Cancel ctx.streaming_task (if any) and await its termination.

    Awaiting the cancelled task ensures we don't leave a zombie coroutine
    racing the next streaming task we are about to create. Without this,
    an in-flight interim-transcription task could continue emitting partials
    after the response flow has already sent the final transcription,
    violating the protocol ordering guarantee.
    """
    task = ctx.streaming_task
    ctx.streaming_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        # Cancellation is the expected path; other exceptions from the
        # background flow are already logged inside the task itself.
        pass


async def _handle_audio(
    ctx: SessionContext,
    msg: ClientAudio,
    config: ServerConfig,
    send_msg,
    websocket: WebSocket,
) -> None:
    """Accumulate silence frames and trigger response flow on turn end."""
    ctx.audio_recv_count += 1
    if ctx.audio_recv_count % 100 == 0:
        logger.info("[%s] RECV  ClientAudio seq=%s (count=%s)", _ts(), msg.seq, ctx.audio_recv_count)

    if msg.payload.vad_state == VadState.SPEECH:
        ctx.silence_count = 0
        if not ctx.recognised:
            ctx.recognised = True
            # Cancel any prior streaming task (e.g., a lingering response flow
            # from the previous turn) before launching the new interim stream,
            # so we never have two concurrent S2C producers on this socket.
            await _cancel_streaming_task(ctx)
            ctx.streaming_task = asyncio.create_task(
                send_interim_transcriptions(
                    ctx=ctx, config=config, websocket_send=send_msg,
                )
            )
    elif msg.payload.vad_state == VadState.SILENCE:
        ctx.silence_count += 1
        if ctx.silence_count >= ctx.silence_threshold_frames and ctx.recognised:
            ctx.recognised = False
            ctx.silence_count = 0
            # End of user turn: cancel the in-flight interim task before
            # starting the response flow. Otherwise the interim partials
            # could be emitted *after* the final transcription, breaking
            # the protocol invariant that interim never follows is_final=true.
            await _cancel_streaming_task(ctx)
            ctx.streaming_task = asyncio.create_task(
                run_response_flow(
                    ctx=ctx,
                    scenario=ctx.scenario,
                    config=config,
                    websocket_send=send_msg,
                )
            )
