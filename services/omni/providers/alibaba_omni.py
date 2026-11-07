from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from media.jpeg import image_resize
from omni.base import OmniEvent, OmniEventType, OmniStream
from shared.observability.logging import get_logger
from shared.utils.errors import ProviderError

logger = logging.getLogger("livachat.omni.alibaba")
msg_logger = get_logger("livachat.omni.alibaba.messages")

_SENTINEL = object()

# 对端（DashScope realtime 网关）单条 WS 帧硬限 262144B（=256KB）：超限帧被传输层
# close 1009（探针实测见 docs/fd_pipeline_avdetector.md §10 条目 28）。发送阈值留 1KB 余量。
_MAX_WS_FRAME_BYTES = 262144
_SEND_LIMIT = _MAX_WS_FRAME_BYTES - 1024
# 超限上行副本重压档位（同条目 28：最多两档，quality 固定 80）
_RESIZE_EDGE_LADDER = (960, 640)
# 上传帧长上界：event_id 取 "evt_"+12 位数字上界；JSON 结构固定，实际帧长恒 ≤ 该估算
_EVENT_ID_UPPER = "evt_" + "0" * 12
_FRAME_OVERHEAD = len(json.dumps({
    "event_id": _EVENT_ID_UPPER,
    "type": "input_image_buffer.append",
    "image": "",
}))


def _log_msg(direction: str, msg_type: str, **extra: object) -> None:
    msg_logger.debug(f"{direction} {msg_type}", direction=direction, type=msg_type, **extra)


def _get_response_id(msg: dict) -> str | None:
    value = msg.get("response_id") or msg.get("response", {}).get("id")
    return value if isinstance(value, str) else None


def _build_headers(api_key: str, workspace_id: str | None) -> dict[str, str]:
    """workspace_id 存在时附加 X-DashScope-WorkSpace 头（与 asr/tts/llm/translate 同一约定：
    base_url 指 workspace 专属域名、本头为配套，互不派生；realtime 族文档中该头为可选项，
    按官方推荐路径保留）。open 与重连必须同源，否则重连后丢 workspace 路由。
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    if workspace_id:
        headers["X-DashScope-WorkSpace"] = workspace_id
    return headers


class _RealtimeOmniStream:
    def __init__(
        self,
        ws: ClientConnection,
        update_payload: dict,
        base_url: str,
        api_key: str,
        *,
        workspace_id: str | None = None,
    ) -> None:
        self._ws = ws
        self._update_payload = update_payload
        self._base_url = base_url
        self._api_key = api_key
        self._workspace_id = workspace_id
        self._queue: asyncio.Queue[OmniEvent | object] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._event_id = 0
        self._current_response_id: str | None = None
        self._audio_in_count: int = 0
        self._audio_in_response_id: str | None = None
        self._audio_out_count: int = 0
        self._audio_out_response_id: str | None = None
        self._image_disabled: bool = False
        self._on_reconnected: Callable[[], None] | None = None

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
        headers = _build_headers(self._api_key, self._workspace_id)
        self._ws = await websockets.connect(
            self._base_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            # close 握手限时（端点不回 close 帧时默认 10s 卡模式切换/收尾，
            # 见 docs/translate_lecture_improvements.md）
            close_timeout=1,
        )
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.created":
                raise ProviderError(
                    f"expected session.created, got {msg.get('type')}",
                    provider="alibaba_omni",
                )
            session_id = msg.get("session", {}).get("session_id", "")
            _log_msg("recv", "session.created", session_id=session_id)
            _log_msg("send", "session.update", event_id=self._update_payload.get("event_id", ""))
            await self._ws.send(json.dumps(self._update_payload))
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.updated":
                raise ProviderError(
                    f"expected session.updated, got {msg.get('type')}",
                    provider="alibaba_omni",
                )
            session_id = msg.get("session", {}).get("session_id", "")
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
        logger.info("alibaba omni reconnected")
        if self._on_reconnected:
            self._on_reconnected()

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
                            # Drain stale OUTPUT events but preserve INPUT events
                            # (speech, transcription). The TTS provider clears the
                            # whole queue, but omni's queue carries mixed event types.
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
                        # 实测事件名为 .delta，文本 = text(已确认前缀) + stash(待修正后缀)；
                        # 旧名 .text 与 delta 字段保留兜底（历史写法，两代实测都不再发送）
                        response_id = _get_response_id(msg)
                        text = (msg.get("text", "") + msg.get("stash", "")) or msg.get("delta", "")
                        await self._queue.put(
                            OmniEvent(
                                type=OmniEventType.INPUT_TRANSCRIPTION_DELTA,
                                text=text,
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
                            "omni provider error",
                            extra={"error_message": error_msg, "error_code": error_code},
                        )
                        _log_msg("recv", "error", error_message=error_msg, error_code=error_code)
                        if "Invalid video" in error_msg or "Invalid image" in error_msg:
                            self._image_disabled = True
                            logger.warning("image feed disabled for this session due to API rejection")
                        else:
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
                "alibaba omni receive loop crashed",
                extra={"response_id": self._current_response_id},
            )
            if self._current_response_id is not None:
                await self._queue.put(_SENTINEL)
        finally:
            logger.info(
                "alibaba omni receive loop exited",
                extra={"reason": exit_reason, "response_id": self._current_response_id},
            )

    # -- OmniStream interface ------------------------------------------------

    async def feed_audio(self, pcm: bytes) -> None:
        try:
            await self._send_audio_append(pcm)
        except Exception:
            logger.warning("omni feed_audio failed, attempting reconnect")
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError("omni reconnect failed", provider="alibaba_omni")
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
        if self._image_disabled:
            return
        upload = await self._fit_upload_jpeg(jpeg, width=width, height=height)
        if upload is None:
            return  # 附帧超限且重压不可达 → 跳过本帧（不抛错：引擎会据此关整个流）
        try:
            await self._send_image_item(upload, width=width, height=height)
        except Exception:
            logger.warning("omni feed_image failed, attempting reconnect")
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError("omni reconnect failed", provider="alibaba_omni")
            await self._send_image_item(upload, width=width, height=height)

    async def _fit_upload_jpeg(
        self, jpeg: bytes, *, width: int, height: int
    ) -> bytes | None:
        """超限上行副本两档重压（960 → 640），首个达标档采用（条目 28 同款防护）。

        网关对 WS 单帧 >262144B 直接 close 1009，而 omni 模式每帧都附图、暴露高于
        按节拍检查的 avdetector。重压失败/两档仍超 → 记 warning 并返回 None（调用方
        跳过本帧）——**不抛异常**：`OmniModeEngine` 对 feed_image 异常会 `_drop_stream`
        关掉整个 omni 会话流，单帧超限不应付此代价。
        """
        frame_len = _FRAME_OVERHEAD + 4 * ((len(jpeg) + 2) // 3)
        if frame_len <= _SEND_LIMIT:
            return jpeg
        jpeg_budget = 3 * ((_SEND_LIMIT - _FRAME_OVERHEAD) // 4)
        resized = None
        edge = 0
        for edge in _RESIZE_EDGE_LADDER:
            try:
                resized = await asyncio.to_thread(
                    image_resize, jpeg, max_edge=edge, max_bytes=jpeg_budget, quality=80
                )
            except Exception:
                logger.warning(
                    "omni image resize unusable (decode failed): jpeg %dB frame %dB",
                    len(jpeg), frame_len, exc_info=True,
                )
                return None
            if resized is not None:
                break
        if resized is None:
            logger.warning(
                "omni image still over frame limit after %d attempts: frame %dB > %dB",
                len(_RESIZE_EDGE_LADDER), frame_len, _SEND_LIMIT,
            )
            return None
        logger.debug(
            "omni image resized for upload: %dx%d jpeg %dB -> %dB (edge %d)",
            width, height, len(jpeg), len(resized), edge,
        )
        return resized

    async def _send_image_item(self, jpeg: bytes, *, width: int, height: int) -> None:
        # Qwen-Omni uses input_image_buffer.append, not conversation.item.create.
        # The image must be raw base64 JPEG (NOT a data:image/jpeg;base64, URL).
        b64 = base64.b64encode(jpeg).decode()
        eid = self._next_event_id()
        _log_msg("send", "input_image_buffer.append", event_id=eid, width=width, height=height)
        await self._ws.send(
            json.dumps({
                "event_id": eid,
                "type": "input_image_buffer.append",
                "image": b64,
            })
        )

    async def commit_user_turn(self) -> None:
        # VAD 模式（semantic_vad / server_vad）下服务端自动提交，no-op；
        # 手动 turn 模式钩子留待后续
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
            logger.warning("omni cancel_response failed, attempting reconnect")
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError("omni reconnect failed", provider="alibaba_omni")
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
                "omni receiver task dead, restarting",
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


class AlibabaOmniProvider:
    name = "alibaba_omni"
    supports_vision = True

    def __init__(self, base_url: str, api_key: str, model: str, voice: str,
                 workspace_id: str | None = None) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._workspace_id = workspace_id

    async def open(
        self,
        *,
        sample_rate: int,
        system_prompt: str = "",
        voice: str = "",
        modalities: tuple[str, ...] = ("text", "audio"),
        generation_opts: dict | None = None,
    ) -> OmniStream:
        headers = _build_headers(self._api_key, self._workspace_id)
        try:
            ws = await websockets.connect(
                self._base_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"alibaba omni connect failed: {exc}", provider="alibaba_omni") from exc

        # stdlib logging 的 extra= 会被服务端 structlog 渲染丢弃，关键信息写进消息体
        logger.info("alibaba omni open model=%s workspace=%s",
                    self._model, self._workspace_id or "-")

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.created":
                raise ProviderError(
                    f"expected session.created, got {msg.get('type')}", provider="alibaba_omni"
                )
            session_id = msg.get("session", {}).get("session_id", "")
            _log_msg("recv", "session.created", session_id=session_id)
        except TimeoutError:
            await ws.close()
            raise ProviderError("timeout waiting for session.created", provider="alibaba_omni")
        except ProviderError:
            await ws.close()
            raise
        except Exception as exc:
            # 非 session.created（错误帧/JSON 解析失败等）同样必须关连接，否则 WS 泄漏
            await ws.close()
            raise ProviderError(f"alibaba omni init failed: {exc}", provider="alibaba_omni") from exc

        session_cfg: dict = {
            "modalities": list(modalities),
            "voice": voice or self._voice,
            # flat 字段为历史兼容（服务端回显但不生效：flat sample_rate 实测被忽略），
            # 真实生效的是下方 nested audio.*；两代模型都接受同发
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "sample_rate": sample_rate,
            "audio": {
                "input": {"format": {"type": "pcm", "sample_rate": sample_rate}},
                "output": {
                    "format": {"type": "pcm", "sample_rate": sample_rate},
                    "voice": voice or self._voice,
                },
            },
            # semantic_vad：按语义有效性判停，过滤附和声/背景音（qwen3.8/3.5-omni-realtime 均支持）
            "turn_detection": {"type": "semantic_vad"},
            "instructions": system_prompt,
            "enable_search": True,
            "search_options": {"enable_source": True},
        }
        if generation_opts:
            session_cfg.update(generation_opts)
        update = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": session_cfg,
        }
        _log_msg("send", "session.update", event_id="evt_session_update",
                 modalities=list(modalities), voice=voice or self._voice, sample_rate=sample_rate)

        try:
            await ws.send(json.dumps(update))
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.updated":
                raise ProviderError(
                    f"expected session.updated, got {msg.get('type')}", provider="alibaba_omni"
                )
            _log_msg("recv", "session.updated", session_id=session_id)
        except ProviderError:
            await ws.close()
            raise
        except Exception as exc:
            await ws.close()
            raise ProviderError(f"alibaba omni init failed: {exc}", provider="alibaba_omni") from exc

        stream = _RealtimeOmniStream(
            ws, update, self._base_url, self._api_key, workspace_id=self._workspace_id,
        )
        await stream._start_receiver()
        return stream
