# TODO(phase2): 抽取 _BaseRealtimeOmniStream 到 omni/providers/_base.py,
# AlibabaOmniProvider 与 OpenAIRealtimeProvider 共享 WS 管理 / recv loop /
# queue / reconnect / cancel / reset / close 逻辑。当前两个 provider 各自
# 内联实现，减少对稳定 alibaba 代码的扰动。

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

from media.resample import Resampler
from omni.base import OmniEvent, OmniEventType, OmniStream
from shared.observability.logging import get_logger
from shared.utils.errors import ProviderError

logger = logging.getLogger("livachat.omni.openai")
msg_logger = get_logger("livachat.omni.openai.messages")

_SENTINEL = object()
_OPENAI_OUTPUT_RATE = 24000


def _log_msg(direction: str, msg_type: str, **extra: object) -> None:
    msg_logger.debug(f"{direction} {msg_type}", direction=direction, type=msg_type, **extra)


def _get_response_id(msg: dict) -> str | None:
    value = msg.get("response_id") or msg.get("response", {}).get("id")
    return value if isinstance(value, str) else None


class _OpenAIRealtimeStream:
    def __init__(
        self,
        ws: ClientConnection,
        update_payload: dict,
        base_url: str,
        api_key: str,
        sample_rate: int,
    ) -> None:
        self._ws = ws
        self._update_payload = update_payload
        self._base_url = base_url
        self._api_key = api_key
        self._sample_rate = sample_rate
        self._queue: asyncio.Queue[OmniEvent | object] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._event_id = 0
        self._current_response_id: str | None = None
        self._audio_in_count: int = 0
        self._audio_in_response_id: str | None = None
        self._audio_out_count: int = 0
        self._audio_out_response_id: str | None = None
        self._resampler: Resampler | None = None
        if sample_rate != _OPENAI_OUTPUT_RATE:
            self._resampler = Resampler(_OPENAI_OUTPUT_RATE, sample_rate)

    def _next_event_id(self) -> str:
        self._event_id += 1
        return f"evt_{self._event_id}"

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

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

    def _drain_queue(self) -> None:
        """清空待消费事件，但**保留队列对象身份**——消费端（events() 生成器）可能正
        park 在旧队列的 get() 上，换对象（曾用 asyncio.Queue() 重建）会把消费端永久
        留在旧队列：重连后线路上事件齐全、消费端却零事件（livetranslate 同款，
        真机实测 2026-09-14）。
        """
        while not self._queue.empty():
            self._queue.get_nowait()

    async def _reconnect(self) -> None:
        await self._cancel_receiver()
        try:
            await self._ws.close()
        except Exception:
            pass
        self._ws = await websockets.connect(
            self._base_url,
            additional_headers=self._get_headers(),
            ping_interval=20,
            ping_timeout=20,
            # close 握手限时（见 docs/translate_lecture_improvements.md）
            close_timeout=1,
        )
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.created":
                raise ProviderError(
                    f"expected session.created, got {msg.get('type')}",
                    provider="openai_realtime",
                )
            session_id = msg.get("session", {}).get("id", "")
            _log_msg("recv", "session.created", session_id=session_id)
            _log_msg("send", "session.update", event_id=self._update_payload.get("event_id", ""))
            await self._ws.send(json.dumps(self._update_payload))
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.updated":
                raise ProviderError(
                    f"expected session.updated, got {msg.get('type')}",
                    provider="openai_realtime",
                )
            session_id = msg.get("session", {}).get("id", "")
            _log_msg("recv", "session.updated", session_id=session_id)
        except Exception:
            # 握手失败必须关掉刚建立的连接，否则每次重连失败泄漏一条 WS（与 open() 同纪律）
            try:
                await self._ws.close()
            except Exception:
                pass
            raise
        self._current_response_id = None
        self._event_id = 0
        self._drain_queue()
        await self._start_receiver()
        logger.info("openai realtime reconnected")

    async def _receive_loop(self) -> None:
        exit_reason = "unknown"
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    logger.debug("ignoring unexpected binary frame", extra={"size": len(raw)})
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event_type = msg.get("type", "")
                if event_type:
                    _log_msg("recv", event_type, response_id=self._current_response_id)

                    if event_type == "response.created":
                        response_id = _get_response_id(msg)
                        if response_id and response_id != self._current_response_id:
                            prev = self._current_response_id
                            if prev is not None:
                                logger.info(
                                    "New omni response (turn boundary)",
                                    extra={"prev_response_id": prev, "response_id": response_id},
                                )
                            kept: list[OmniEvent] = []
                            while not self._queue.empty():
                                item = self._queue.get_nowait()
                                if isinstance(item, OmniEvent) and item.type in (
                                    OmniEventType.SPEECH_STARTED,
                                    OmniEventType.SPEECH_STOPPED,
                                    OmniEventType.INPUT_TRANSCRIPTION_DELTA,
                                    OmniEventType.INPUT_TRANSCRIPTION_DONE,
                                ):
                                    kept.append(item)
                            for item in kept:
                                await self._queue.put(item)
                            self._current_response_id = response_id
                            await self._queue.put(
                                OmniEvent(type=OmniEventType.RESPONSE_STARTED, response_id=response_id)
                            )
                    elif event_type == "response.text.delta" or event_type == "response.audio_transcript.delta":
                        response_id = _get_response_id(msg)
                        if response_id and response_id == self._current_response_id:
                            delta = msg.get("delta", "")
                            await self._queue.put(
                                OmniEvent(
                                    type=OmniEventType.TEXT_DELTA,
                                    text=delta,
                                    response_id=response_id,
                                )
                            )
                    elif event_type == "response.audio.delta":
                        response_id = _get_response_id(msg)
                        if response_id and response_id == self._current_response_id:
                            b64 = msg.get("delta", "")
                            if b64:
                                pcm = base64.b64decode(b64)
                                if self._resampler is not None:
                                    pcm = self._resampler.process(pcm)
                                if not pcm:
                                    continue
                                if self._audio_out_response_id != self._current_response_id:
                                    self._audio_out_response_id = self._current_response_id
                                    self._audio_out_count = 0
                                self._audio_out_count += 1
                                n = self._audio_out_count
                                if n == 1 or n % 50 == 0:
                                    _log_msg("recv", "response.audio.delta", response_id=response_id, audio_n=n)
                                await self._queue.put(
                                    OmniEvent(
                                        type=OmniEventType.AUDIO_DELTA,
                                        audio=pcm,
                                        response_id=response_id,
                                    )
                                )
                    elif event_type == "response.done":
                        response_id = _get_response_id(msg)
                        if response_id and response_id == self._current_response_id:
                            # Flush any remaining resampler tail
                            if self._resampler is not None:
                                tail = self._resampler.flush()
                                if tail:
                                    await self._queue.put(
                                        OmniEvent(
                                            type=OmniEventType.AUDIO_DELTA,
                                            audio=tail,
                                            response_id=response_id,
                                        )
                                    )
                            await self._queue.put(
                                OmniEvent(type=OmniEventType.RESPONSE_DONE, response_id=response_id)
                            )
                    elif event_type == "input_audio_buffer.speech_started":
                        response_id = _get_response_id(msg)
                        await self._queue.put(
                            OmniEvent(type=OmniEventType.SPEECH_STARTED, response_id=response_id or "")
                        )
                    elif event_type == "input_audio_buffer.speech_stopped":
                        response_id = _get_response_id(msg)
                        await self._queue.put(
                            OmniEvent(type=OmniEventType.SPEECH_STOPPED, response_id=response_id or "")
                        )
                    elif event_type in (
                        "conversation.item.input_audio_transcription.delta",
                        "conversation.item.input_audio_transcription.text",
                    ):
                        response_id = _get_response_id(msg)
                        await self._queue.put(
                            OmniEvent(
                                type=OmniEventType.INPUT_TRANSCRIPTION_DELTA,
                                text=msg.get("delta", ""),
                                response_id=response_id or "",
                            )
                        )
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        response_id = _get_response_id(msg)
                        await self._queue.put(
                            OmniEvent(
                                type=OmniEventType.INPUT_TRANSCRIPTION_DONE,
                                text=msg.get("transcript", ""),
                                is_final=True,
                                response_id=response_id or "",
                            )
                        )
                    elif event_type == "error":
                        error_msg = msg.get("error", {}).get("message", "") if isinstance(msg.get("error"), dict) else str(msg.get("error", ""))
                        error_code = msg.get("error", {}).get("code", "") if isinstance(msg.get("error"), dict) else ""
                        logger.warning(
                            "openai realtime provider error",
                            extra={"error_message": error_msg, "error_code": error_code},
                        )
                        _log_msg("recv", "error", error_message=error_msg, error_code=error_code)
                        await self._queue.put(
                            OmniEvent(type=OmniEventType.ERROR, error_message=error_msg)
                        )
                    elif event_type == "session.finished":
                        await self._queue.put(_SENTINEL)
                        exit_reason = "session.finished"
                        return
        except asyncio.CancelledError:
            exit_reason = "cancelled"
        except Exception:
            exit_reason = "exception"
            logger.exception(
                "openai realtime receive loop crashed",
                extra={"response_id": self._current_response_id},
            )
            if self._current_response_id is not None:
                await self._queue.put(_SENTINEL)
        finally:
            logger.info(
                "openai realtime receive loop exited",
                extra={"reason": exit_reason, "response_id": self._current_response_id},
            )

    # -- OmniStream interface ------------------------------------------------

    async def feed_audio(self, pcm: bytes) -> None:
        try:
            await self._send_audio_append(pcm)
        except Exception:
            logger.warning("openai realtime feed_audio failed, attempting reconnect")
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError("openai realtime reconnect failed", provider="openai_realtime")
            await self._send_audio_append(pcm)

    async def _send_audio_append(self, pcm: bytes) -> None:
        b64 = base64.b64encode(pcm).decode()
        eid = self._next_event_id()
        if self._audio_in_response_id != self._current_response_id:
            self._audio_in_response_id = self._current_response_id
            self._audio_in_count = 0
        self._audio_in_count += 1
        n = self._audio_in_count
        if n == 1 or n % 50 == 0:
            _log_msg("send", "input_audio_buffer.append", event_id=eid, audio_n=n)
        await self._ws.send(
            json.dumps({
                "event_id": eid,
                "type": "input_audio_buffer.append",
                "audio": b64,
            })
        )

    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None:
        # GPT-4o Realtime does not accept realtime video frames.
        logger.debug(
            "openai realtime feed_image ignored (not supported)",
            extra={"width": width, "height": height, "size": len(jpeg)},
        )

    async def commit_user_turn(self) -> None:
        # server_vad mode: no-op; manual turn mode hook reserved for phase 2
        pass

    async def events(self) -> AsyncIterator[OmniEvent]:
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                return
            if not isinstance(item, OmniEvent):
                raise TypeError(f"Unexpected item in omni event queue: {type(item)}")
            yield item

    async def cancel_response(self) -> None:
        try:
            await self._send_cancel()
        except Exception:
            logger.warning("openai realtime cancel_response failed, attempting reconnect")
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError("openai realtime reconnect failed", provider="openai_realtime")
            await self._send_cancel()

    async def _send_cancel(self) -> None:
        eid = self._next_event_id()
        _log_msg("send", "response.cancel", event_id=eid, reason="interrupt")
        await self._ws.send(json.dumps({"event_id": eid, "type": "response.cancel"}))
        eid = self._next_event_id()
        _log_msg("send", "input_audio_buffer.clear", event_id=eid)
        await self._ws.send(json.dumps({"event_id": eid, "type": "input_audio_buffer.clear"}))

    async def reset(self) -> None:
        self._current_response_id = None
        self._drain_queue()
        if self._recv_task is None or self._recv_task.done():
            logger.warning(
                "openai realtime receiver task dead, restarting",
                extra={"cancelled": self._recv_task.cancelled() if self._recv_task else False},
            )
            await self._start_receiver()

    async def close(self) -> None:
        try:
            eid = self._next_event_id()
            _log_msg("send", "session.finish", event_id=eid)
            await self._ws.send(json.dumps({"event_id": eid, "type": "session.finish"}))
        except Exception:
            pass
        await self._cancel_receiver()
        if self._ws.protocol.state.name != "CLOSED":
            try:
                await self._ws.close()
            except Exception:
                pass


class OpenAIRealtimeProvider:
    name = "openai_realtime"
    supports_vision = False

    def __init__(self, base_url: str, api_key: str, model: str, voice: str) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._voice = voice

    async def open(
        self,
        *,
        sample_rate: int,
        system_prompt: str = "",
        voice: str = "",
        modalities: tuple[str, ...] = ("text", "audio"),
        generation_opts: dict | None = None,
    ) -> OmniStream:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        try:
            ws = await websockets.connect(
                self._base_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"openai realtime connect failed: {exc}", provider="openai_realtime") from exc

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.created":
                raise ProviderError(
                    f"expected session.created, got {msg.get('type')}", provider="openai_realtime"
                )
            session_id = msg.get("session", {}).get("id", "")
            _log_msg("recv", "session.created", session_id=session_id)
        except TimeoutError:
            await ws.close()
            raise ProviderError("timeout waiting for session.created", provider="openai_realtime")
        except ProviderError:
            await ws.close()
            raise
        except Exception as exc:
            # 非 session.created（错误帧/JSON 解析失败等）同样必须关连接，否则 WS 泄漏
            await ws.close()
            raise ProviderError(f"openai realtime init failed: {exc}", provider="openai_realtime") from exc

        session_cfg: dict = {
            "modalities": list(modalities),
            "voice": voice or self._voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": {"type": "server_vad"},
            "instructions": system_prompt,
        }
        if generation_opts:
            session_cfg.update(generation_opts)
        update = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": session_cfg,
        }
        _log_msg("send", "session.update", event_id="evt_session_update",
                 modalities=list(modalities), voice=voice or self._voice)

        try:
            await ws.send(json.dumps(update))
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.updated":
                raise ProviderError(
                    f"expected session.updated, got {msg.get('type')}", provider="openai_realtime"
                )
            _log_msg("recv", "session.updated", session_id=session_id)
        except ProviderError:
            await ws.close()
            raise
        except Exception as exc:
            await ws.close()
            raise ProviderError(f"openai realtime init failed: {exc}", provider="openai_realtime") from exc

        stream = _OpenAIRealtimeStream(ws, update, self._base_url, self._api_key, sample_rate)
        await stream._start_receiver()
        return stream
