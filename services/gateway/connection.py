from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from fastapi import WebSocket
from livachat_protocol.generated.client_message import (
    ClientAudio,
    ClientPing,
    ClientPong,
    ClientSessionStart,
    ClientVideo,
)
from livachat_protocol.generated.client_message import (
    ClientMessage as ClientMsg,
)

from gateway.auth import Principal
from gateway.codec import InboundDecodeGuard, WireCodec
from gateway.heartbeat import HeartbeatTracker
from gateway.seq import InboundSeqGuard, OutboundSeqAllocator
from shared.observability.logging import get_logger
from shared.timeutil import canonicalize_timezone
from shared.utils.errors import ProtocolError

logger = logging.getLogger("livachat.gateway.connection")
msg_logger = get_logger("livachat.gateway.messages")

_WS_CLOSE_NORMAL = 1000


class ConnectionHandler:
    """Per-connection coroutine tree.

    Spawns 4 tasks inside an ``asyncio.TaskGroup``:

    * **reader** — inbound raw JSON → parse → seq-guard → heartbeat → inbound queue
    * **writer** — outbound queue → encode → WS send
    * **dialog** — (Phase 3 stub) echo unsupported error for business messages
    * **heartbeat** — periodic ping / timeout detection
    """

    def __init__(
        self,
        ws: WebSocket,
        principal: Principal,
        codec: WireCodec,
        seq_guard: InboundSeqGuard,
        outbound_seq: OutboundSeqAllocator,
        heartbeat: HeartbeatTracker,
        decode_guard: InboundDecodeGuard,
        orchestrator: Any | None = None,
        sink: Any | None = None,
    ) -> None:
        self.ws = ws
        self.principal = principal
        self._codec = codec
        self._seq_guard = seq_guard
        self._outbound_seq = outbound_seq
        self._heartbeat = heartbeat
        self._decode_guard = decode_guard
        self._orchestrator = orchestrator
        self._sink = sink

        self._inbound: asyncio.Queue[ClientMsg] = asyncio.Queue(maxsize=256)
        self._outbound: asyncio.Queue[str] = asyncio.Queue(maxsize=256)

        self._stopped = False

        # Per-connection counters for sampled audio logging
        self._audio_in_count: int = 0
        self._audio_out_count: int = 0

    # -- message logging --------------------------------------------------

    def _log_inbound(self, msg: ClientMsg) -> None:
        msg_type: str = getattr(msg, "type", "?")
        if msg_type == "audio":
            self._audio_in_count += 1
            n = self._audio_in_count
            if n != 1 and n % 50 != 0:
                return
            msg_logger.debug(
                f"recv {msg_type}",
                type=msg_type,
                seq=msg.seq,
                device_id=self.principal.device_id,
                audio_n=n,
            )
        elif msg_type == "session.start":
            # 时区诊断（timezone-support.md）：原始值 + 规范形，缺省/非法均可见
            # resume 诊断：payload.session_id 即 resume_id，空=客户端未请求恢复
            cfg = getattr(msg, "payload", None)
            raw_tz = getattr(getattr(cfg, "config", None), "timezone", "") or ""
            resume_sid = getattr(cfg, "session_id", None) or ""
            msg_logger.debug(
                f"recv {msg_type}",
                type=msg_type,
                seq=msg.seq,
                device_id=self.principal.device_id,
                resume_session_id=resume_sid,
                timezone_raw=raw_tz,
                timezone_canonical=canonicalize_timezone(raw_tz),
            )
        else:
            msg_logger.debug(
                f"recv {msg_type}",
                type=msg_type,
                seq=msg.seq,
                device_id=self.principal.device_id,
            )

    def _log_outbound(self, raw_frame: str) -> None:
        # Extract type from JSON without full re-parse
        msg_type = "?"
        idx = raw_frame.find('"type":"')
        if idx != -1:
            start = idx + 8
            end = raw_frame.find('"', start)
            if end != -1:
                msg_type = raw_frame[start:end]
        if msg_type == "ai_audio":
            self._audio_out_count += 1
            n = self._audio_out_count
            if n != 1 and n % 50 != 0:
                return
            msg_logger.debug(
                f"send {msg_type}",
                type=msg_type,
                device_id=self.principal.device_id,
                audio_n=n,
            )
        elif msg_type == "status":
            session_status = "?"
            ss_idx = raw_frame.find('"session_status":"')
            if ss_idx != -1:
                ss_start = ss_idx + 18
                ss_end = raw_frame.find('"', ss_start)
                if ss_end != -1:
                    session_status = raw_frame[ss_start:ss_end]
            msg_logger.debug(
                f"send {msg_type}",
                type=msg_type,
                device_id=self.principal.device_id,
                status=session_status,
            )
        elif msg_type == "error":
            # Errors are rare in normal operation; parse the frame so the log
            # carries the code clients branch on (message is free-form and may
            # contain escaped quotes, so string.find is not safe here).
            code: Any = "?"
            message: Any = "?"
            try:
                payload = json.loads(raw_frame).get("payload") or {}
                code = payload.get("code", "?")
                message = payload.get("message", "?")
            except (ValueError, AttributeError):
                pass
            msg_logger.debug(
                f"send {msg_type}",
                type=msg_type,
                device_id=self.principal.device_id,
                code=code,
                message=message,
            )
        else:
            msg_logger.debug(
                f"send {msg_type}",
                type=msg_type,
                device_id=self.principal.device_id,
            )

    # -- public API -------------------------------------------------------

    async def enqueue_outbound(self, raw: str) -> None:
        """Thread-safe: push a serialised frame onto the writer queue."""
        await self._outbound.put(raw)

    def stop(self) -> None:
        self._stopped = True

    # -- runner -----------------------------------------------------------

    async def run(self) -> None:
        logger.info("connection started", extra={"device_id": self.principal.device_id})
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._reader(), name=f"reader-{self.principal.device_id}")
                tg.create_task(self._writer(), name=f"writer-{self.principal.device_id}")
                tg.create_task(self._dialog_stub(), name=f"dialog-{self.principal.device_id}")
                heartbeat_task = tg.create_task(self._heartbeat_task(), name=f"heartbeat-{self.principal.device_id}")
        except* Exception as eg:
            import traceback

            logger.warning(
                "connection task group ended",
                extra={
                    "device_id": self.principal.device_id,
                    "errors": repr(eg),
                    "traceback": "".join(traceback.format_exception(type(eg), eg, eg.__traceback__)),
                },
            )
        finally:
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
            # Notify recording sink of connection close
            if self._sink is not None:
                try:
                    await self._sink.on_connection_closed()
                except Exception:
                    pass
            # Notify orchestrator: abnormal disconnect without session.end must
            # still reach the agent relay so watchers/Hermes sessions are reaped.
            if self._orchestrator is not None:
                try:
                    await self._orchestrator.on_connection_closed()
                except Exception:
                    logger.warning("orchestrator connection-closed hook failed", exc_info=True)
            logger.info("connection stopped", extra={"device_id": self.principal.device_id})

    # -- private tasks ----------------------------------------------------

    async def _reader(self) -> None:
        try:
            while not self._stopped:
                try:
                    raw = await self.ws.receive_text()
                except Exception:
                    self.stop()
                    return

                self._heartbeat.on_any_frame()

                try:
                    msg = self._codec.parse(raw)
                except ProtocolError:
                    self._decode_guard.on_error()
                    if self._decode_guard.should_close:
                        await self._close(4001, "too many invalid frames")
                        return
                    err_frame = self._codec.make_error(code=1006, message="invalid JSON", fatal=False)
                    await self._outbound.put(err_frame)
                    continue

                self._decode_guard.on_ok()

                if isinstance(msg, (ClientPing,)):
                    self._log_inbound(msg)
                    pong = self._codec.make_pong(msg.seq)
                    await self._outbound.put(pong)
                    continue
                if isinstance(msg, (ClientPong,)):
                    self._log_inbound(msg)
                    continue

                # Always accept session.start — resets seq guard for new session
                if isinstance(msg, ClientSessionStart):
                    self._seq_guard.reset()
                if not self._seq_guard.check(msg.seq, msg_type=getattr(msg, "type", "?")):
                    continue

                self._log_inbound(msg)

                # -- recording tap (non-blocking, best-effort) ---------
                if self._sink is not None:
                    try:
                        if isinstance(msg, ClientAudio):
                            opus_bytes = base64.b64decode(msg.payload.data)
                            self._sink.on_media(
                                kind="audio", data=opus_bytes,
                                duration_ms=getattr(msg.payload, "duration_ms", 20),
                                ts_ms=getattr(msg, "timestamp", 0),
                            )
                        elif isinstance(msg, ClientVideo):
                            jpeg_bytes = base64.b64decode(msg.payload.data)
                            self._sink.on_media(
                                kind="video", data=jpeg_bytes,
                                ts_ms=getattr(msg, "timestamp", 0),
                            )
                        elif isinstance(msg, ClientSessionStart):
                            sample_rate = getattr(msg.payload.config, "audio_sample_rate", 16000) if hasattr(msg.payload, "config") else 16000
                            raw_tz = getattr(msg.payload.config, "timezone", "") if hasattr(msg.payload, "config") else ""
                            self._sink.on_session_config(
                                audio_sample_rate=sample_rate,
                                timezone=canonicalize_timezone(raw_tz) or "UTC",
                            )
                            self._sink.on_session_start(
                                chat_session_id=getattr(msg.payload, "session_id", ""),
                            )
                    except Exception:
                        pass  # recording failures must never affect the main pipeline

                await self._inbound.put(msg)

        except asyncio.CancelledError:
            self.stop()
            raise
        except Exception:
            logger.exception("reader task crashed")
            raise
        finally:
            self.stop()

    async def _writer(self) -> None:
        try:
            while not self._stopped:
                try:
                    frame = await asyncio.wait_for(self._outbound.get(), timeout=0.5)
                except TimeoutError:
                    continue
                # Allocate seq at dequeue time to guarantee monotonic ordering
                # across concurrent producers (design §4.7 / review #7).
                seq = await self._outbound_seq.next()
                frame = self._codec.finalize_seq(frame, seq)
                self._log_outbound(frame)
                await self.ws.send_text(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "writer task error",
                extra={"device_id": self.principal.device_id},
                exc_info=True,
            )

    async def _dialog_stub(self) -> None:
        """Phase 9: dispatch to orchestrator, or echo unsupported if not wired."""
        try:
            if self._orchestrator is not None:
                await self._orchestrator.run()
            else:
                while not self._stopped:
                    try:
                        msg = await asyncio.wait_for(self._inbound.get(), timeout=0.5)
                    except TimeoutError:
                        continue
                    err = self._codec.make_error(
                        code=1006,
                        message=f"unsupported message type: {msg.type}",
                        fatal=False,
                    )
                    await self._outbound.put(err)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "dialog task error",
                extra={"device_id": self.principal.device_id},
                exc_info=True,
            )

    async def _heartbeat_task(self) -> None:
        async def _send_ping() -> None:
            frame = self._codec.make_ping()
            await self._outbound.put(frame)

        async def _close(code: int, reason: str) -> None:
            await self._close(code, reason)

        # stopped: reader 检测到 WS 关闭后 stop() 会置位，心跳立即退出，
        # 否则 TaskGroup 会被心跳拖到 heartbeat_timeout_s 才收尾，导致
        # conn unregister / session snapshot 晚 35s（P1）。
        await self._heartbeat.run(_send_ping, _close, stopped=lambda: self._stopped)

    # -- agent job result ingest (fd_pipeline_llm_v2) -------------------

    def record_agent_job_result(self, *, text: str, message_id: str,
                                agent_name: str = "", agent_id: str = "",
                                job_key: str = "") -> None:
        """同步委托（无 await 阻塞点，sink 契约见 recording/sink.py）；失败只
        warning，不影响已投递帧。

        agent_name/agent_id/job_key 与 orchestrator 同签名透传
        （recording_data_process.md §2.5：随行落 chatlog，供 lvdata 铸造
        agent_job 事件）。签名必须与 publisher 调用及两处 orchestrator 实现
        保持一致——曾因委托层漏改导致 TypeError（job 卡 done 重投 + 结果未入史）。
        """
        orch = self._orchestrator
        if orch is None or not text:
            return
        try:
            orch.record_agent_job_result(
                text=text, message_id=message_id, agent_name=agent_name,
                agent_id=agent_id, job_key=job_key)
        except Exception:
            logger.warning(
                "agent job result ingest failed",
                extra={"device_id": self.principal.device_id, "message_id": message_id},
                exc_info=True,
            )

    @property
    def current_session_id(self) -> str | None:
        """设备当前 live chat session id（§3.3）；无编排上下文/无会话 → None。

        派生自既有编排状态，不引入第二事实源（_kick_old 原私有链访问
        一并改走本 property）。
        """
        orch = self._orchestrator
        if orch is None:
            return None
        ctx = getattr(orch, "_ctx", None)
        return getattr(ctx, "session_id", None) if ctx is not None else None

    # -- internal ---------------------------------------------------------

    async def _close(self, code: int, reason: str) -> None:
        self.stop()
        try:
            await self.ws.close(code=code, reason=reason)
        except Exception:
            logger.debug(
                "WS close failed (already closed)",
                extra={"device_id": self.principal.device_id},
            )


class ConnectionManager:
    """进程内活跃连接注册表（§3.3；替代 conn:{device_id} Redis key）。"""

    def __init__(self) -> None:
        self._conns: dict[str, ConnectionHandler] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: str, handler: ConnectionHandler) -> None:
        async with self._lock:
            old = self._conns.pop(device_id, None)
            if old is not None:
                await self._kick_old(old, "server_shutdown")
            self._conns[device_id] = handler

    async def unregister(self, device_id: str, handler: ConnectionHandler | None = None) -> None:
        async with self._lock:
            if handler is not None:
                current = self._conns.get(device_id)
                if current is not handler:
                    return
            self._conns.pop(device_id, None)

    async def shutdown(self) -> None:
        """Gracefully close every active connection (SIGTERM handler)."""
        async with self._lock:
            handlers = list(self._conns.values())
            self._conns.clear()
        for handler in handlers:
            await self._kick_old(handler, "server_shutdown")

    def get_handler(self, device_id: str) -> ConnectionHandler | None:
        """Return the active connection handler for *device_id*, or None."""
        return self._conns.get(device_id)

    def is_online(self, device_id: str) -> bool:
        """True if a live connection handler for *device_id* exists in-process."""
        return self._conns.get(device_id) is not None

    def get_session_id(self, device_id: str) -> str | None:
        """设备当前 live chat session id（§3.3，承接 device:{dev}:session 功能）；
        无连接/无会话 → None。当前无生产消费方——保留为功能对齐与 BFF/诊断。"""
        handler = self._conns.get(device_id)
        return handler.current_session_id if handler is not None else None

    async def _kick_old(self, handler: ConnectionHandler, reason: str) -> None:
        try:
            session_id = handler.current_session_id or ""
            frame = handler._codec.make_session_ended(
                reason=reason,
                session_id=session_id,
            )
            await handler.enqueue_outbound(frame)
            handler.stop()
        except Exception:
            logger.warning(
                "Failed to kick old connection",
                extra={"device_id": handler.principal.device_id},
                exc_info=True,
            )
