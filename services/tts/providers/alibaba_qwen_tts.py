from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

from shared.observability.logging import get_logger
from shared.utils.errors import ProviderError
from tts.base import TtsStream

logger = logging.getLogger("livachat.tts.qwen")
msg_logger = get_logger("livachat.tts.qwen.messages")

_SENTINEL = object()


def _log_msg(direction: str, msg_type: str, **extra: object) -> None:
    msg_logger.debug(f"{direction} {msg_type}", direction=direction, type=msg_type, **extra)


def _get_response_id(msg: dict) -> str | None:
    """Extract response_id from a Qwen Realtime API message.

    ``response_id`` appears in two locations depending on the message type:
    - ``msg["response_id"]`` for delta/done events (top-level)
    - ``msg["response"]["id"]`` for created/done events (nested)
    """
    return msg.get("response_id") or msg.get("response", {}).get("id")


class _QwenTtsStream:
    def __init__(self, ws: ClientConnection, update_payload: dict, base_url: str, api_key: str) -> None:
        # None = connection dead (receive loop crashed); must reconnect before
        # any send (fix: idle 1007 crash previously left a half-dead socket
        # that made close()/send() hang for 30s+ on shutdown).
        self._ws: ClientConnection | None = ws
        self._update_payload = update_payload
        self._base_url = base_url
        self._api_key = api_key
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._event_id = 0
        self._current_response_id: str | None = None
        self._audio_in_count: int = 0
        self._audio_in_response_id: str | None = None

    def _next_event_id(self) -> str:
        self._event_id += 1
        return f"evt_{self._event_id}"

    async def _start_receiver(self) -> None:
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def _cancel_receiver(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
            self._recv_task = None

    async def _reconnect(self) -> None:
        """Close old WS, open new one, re-establish session."""
        await self._cancel_receiver()
        # Close old WS (already None if the receive loop crashed)
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        # Open new WS
        headers = {"Authorization": f"Bearer {self._api_key}"}
        self._ws = await websockets.connect(
            self._base_url, additional_headers=headers,
            ping_interval=20, ping_timeout=20,
            # Bound the close handshake (provider may not reply promptly);
            # 1s keeps mode-switch/teardown paths from stalling
            # (see docs/translate_lecture_improvements.md).
            close_timeout=1,
        )
        # Wait for session.created
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
        msg = json.loads(raw)
        if msg.get("type") != "session.created":
            raise ProviderError(
                f"expected session.created, got {msg.get('type')}",
                provider="alibaba_qwen_tts",
            )
        session_id = msg.get("session", {}).get("session_id", "")
        _log_msg("recv", "session.created", session_id=session_id)
        # Send session.update
        _log_msg("send", "session.update", event_id=self._update_payload.get("event_id", ""))
        await self._ws.send(json.dumps(self._update_payload))
        # Wait for session.updated
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
        msg = json.loads(raw)
        if msg.get("type") != "session.updated":
            raise ProviderError(
                f"expected session.updated, got {msg.get('type')}",
                provider="alibaba_qwen_tts",
            )
        _log_msg("recv", "session.updated")
        # Reset state for new connection
        self._current_response_id = None
        self._event_id = 0
        self._queue = asyncio.Queue()
        await self._start_receiver()
        logger.info("qwen tts reconnected")

    async def _receive_loop(self) -> None:
        exit_reason = "unknown"
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    if self._current_response_id is not None:
                        # Sample audio chunk logging: 1st + every 50th
                        if self._audio_in_response_id != self._current_response_id:
                            self._audio_in_response_id = self._current_response_id
                            self._audio_in_count = 0
                        self._audio_in_count += 1
                        n = self._audio_in_count
                        if n == 1 or n % 50 == 0:
                            _log_msg("recv", "audio", response_id=self._current_response_id, audio_n=n)
                        await self._queue.put(raw)
                else:
                    try:
                        msg = json.loads(raw)
                        event_type = msg.get("type", "")
                        if event_type:
                            _log_msg("recv", event_type, response_id=self._current_response_id)
                        if event_type == "response.created":
                            response_id = _get_response_id(msg)
                            if response_id and response_id != self._current_response_id:
                                prev = self._current_response_id
                                if prev is not None:
                                    logger.info(
                                        "New TTS response (turn boundary)",
                                        extra={
                                            "prev_response_id": prev,
                                            "response_id": response_id,
                                        },
                                    )
                                # Clear stale audio from the previous response
                                while not self._queue.empty():
                                    self._queue.get_nowait()
                                self._current_response_id = response_id
                        elif event_type == "response.audio.delta":
                            response_id = _get_response_id(msg)
                            if response_id and response_id == self._current_response_id:
                                b64 = msg.get("delta", "")
                                if b64:
                                    await self._queue.put(base64.b64decode(b64))
                        elif event_type == "response.done":
                            response_id = _get_response_id(msg)
                            if response_id and response_id == self._current_response_id:
                                await self._queue.put(_SENTINEL)
                            # continue loop — session still alive for next utterance
                        elif event_type == "session.finished":
                            await self._queue.put(_SENTINEL)
                            exit_reason = "session.finished"
                            return
                    except json.JSONDecodeError:
                        pass
        except asyncio.CancelledError:
            exit_reason = "cancelled"
        except Exception:
            exit_reason = "exception"
            logger.exception(
                "qwen tts receive loop crashed",
                extra={"response_id": self._current_response_id},
            )
            # The socket is no longer usable (e.g. provider closed an idle
            # WS with 1007). Mark it dead so reset()/feed_text() reconnect
            # proactively instead of writing to a broken connection.
            self._ws = None
            if self._current_response_id is not None:
                await self._queue.put(_SENTINEL)
        finally:
            logger.info(
                "qwen tts receive loop exited",
                extra={
                    "reason": exit_reason,
                    "response_id": self._current_response_id,
                },
            )

    async def reset(self) -> None:
        """Reset for a new utterance on the existing WebSocket session.

        Clears the current response_id so stale audio from the previous
        turn is discarded by the receiver.  If the receiver task has died,
        restarts it on the existing connection; if the connection itself is
        dead (receive loop crash), reconnects proactively instead of
        restarting the receiver on a broken socket.
        """
        self._current_response_id = None
        while not self._queue.empty():
            self._queue.get_nowait()

        if self._recv_task is None or self._recv_task.done():
            if self._ws is None:
                logger.warning("TTS connection dead, reconnecting")
                await self._reconnect()
                return
            logger.warning(
                "TTS receiver task dead, restarting",
                extra={"cancelled": self._recv_task.cancelled() if self._recv_task else False},
            )
            self._queue = asyncio.Queue()
            await self._start_receiver()

    async def feed_text(self, text: str) -> None:
        if self._ws is None:
            # Connection died since the last synthesis (receive loop crash):
            # reconnect before sending, so the send below never targets a
            # broken socket.
            logger.warning("qwen tts connection dead, reconnecting before send")
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError(
                    "qwen tts reconnect failed",
                    provider="alibaba_qwen_tts",
                ) from None
        try:
            await self._send_append_commit(text)
        except Exception:
            logger.warning(
                "qwen tts feed_text failed, attempting reconnect"
            )
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError(
                    "qwen tts reconnect failed",
                    provider="alibaba_qwen_tts",
                )
            await self._send_append_commit(text)

    async def _send_append_commit(self, text: str) -> None:
        eid = self._next_event_id()
        _log_msg("send", "input_text_buffer.append", event_id=eid, text_len=len(text))
        await self._ws.send(
            json.dumps(
                {
                    "event_id": eid,
                    "type": "input_text_buffer.append",
                    "text": text,
                }
            )
        )
        eid = self._next_event_id()
        _log_msg("send", "input_text_buffer.commit", event_id=eid)
        await self._ws.send(
            json.dumps(
                {
                    "event_id": eid,
                    "type": "input_text_buffer.commit",
                }
            )
        )

    async def audio(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    async def poll_audio(self) -> AsyncIterator[bytes]:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    async def flush(self) -> None:
        pass  # commit already triggers synthesis; session.finish only in close()

    async def close(self) -> None:
        if self._ws is not None:
            try:
                eid = self._next_event_id()
                _log_msg("send", "session.finish", event_id=eid)
                await self._ws.send(
                    json.dumps(
                        {
                            "event_id": eid,
                            "type": "session.finish",
                        }
                    )
                )
            except Exception:
                pass
        await self._cancel_receiver()
        if self._ws is not None and self._ws.protocol.state.name != "CLOSED":
            try:
                await self._ws.close()
            except Exception:
                pass


class AlibabaQwenTTSProvider:
    name = "alibaba_qwen_tts"

    def __init__(self, base_url: str, api_key: str, model: str, voice: str) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._voice = voice

    async def open(self, *, voice: str, sample_rate: int, audio_format: str = "pcm") -> TtsStream:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            ws = await websockets.connect(
                self._base_url, additional_headers=headers,
                ping_interval=20, ping_timeout=20,
                close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"qwen tts connect failed: {exc}", provider="alibaba_qwen_tts") from exc

        try:
            # Wait for session.created
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                msg = json.loads(raw)
                if msg.get("type") != "session.created":
                    raise ProviderError(
                        f"expected session.created, got {msg.get('type')}", provider="alibaba_qwen_tts"
                    )
                session_id = msg.get("session", {}).get("session_id", "")
                _log_msg("recv", "session.created", session_id=session_id)
            except TimeoutError:
                raise ProviderError("timeout waiting for session.created", provider="alibaba_qwen_tts")

            # Send session.update
            update = {
                "event_id": "evt_session_update",
                "type": "session.update",
                "session": {
                    "voice": voice or self._voice,
                    "mode": "commit",
                    "response_format": audio_format,
                    "sample_rate": sample_rate,
                },
            }
            _log_msg("send", "session.update", event_id="evt_session_update")
            await ws.send(json.dumps(update))

            # Wait for session.updated
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                msg = json.loads(raw)
                if msg.get("type") != "session.updated":
                    raise ProviderError(
                        f"expected session.updated, got {msg.get('type')}", provider="alibaba_qwen_tts"
                    )
                _log_msg("recv", "session.updated")
            except TimeoutError:
                raise ProviderError("timeout waiting for session.updated", provider="alibaba_qwen_tts")

        except ProviderError:
            await ws.close()
            raise
        except Exception as exc:
            await ws.close()
            raise ProviderError(f"qwen tts init failed: {exc}", provider="alibaba_qwen_tts") from exc

        stream = _QwenTtsStream(ws, update, self._base_url, self._api_key)
        await stream._start_receiver()
        return stream
