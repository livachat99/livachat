from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from omni.base import OmniEvent, OmniEventType, OmniStream
from shared.observability.logging import get_logger
from shared.utils.errors import ProviderError

logger = logging.getLogger("livachat.omni.livetranslate")
msg_logger = get_logger("livachat.omni.livetranslate.messages")

_SENTINEL = object()


def _log_msg(direction: str, msg_type: str, **extra: object) -> None:
    msg_logger.debug(f"{direction} {msg_type}", direction=direction, type=msg_type, **extra)


def _get_response_id(msg: dict) -> str | None:
    value = msg.get("response_id") or msg.get("response", {}).get("id")
    return value if isinstance(value, str) else None


def _build_headers(api_key: str, workspace_id: str | None) -> dict[str, str]:
    """workspace_id 存在时附加 X-DashScope-WorkSpace 头（与 asr/tts bailian 同一约定：
    base_url 指 workspace 专属域名、本头为配套，互不派生；realtime 族文档中该头为
    可选项，按官方推荐路径保留）。open 与重连必须同源，否则重连后丢 workspace 路由。
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    if workspace_id:
        headers["X-DashScope-WorkSpace"] = workspace_id
    return headers


class _RealtimeTranslateStream:
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

        # 3.8 增量语义：本回合已直传的译文文本（.done 兜底补差用）
        self._resp_text: str = ""
        # 源文识别增量累计（引擎 transcription 为覆盖语义，需累计回全量上抛）
        self._src_text: str = ""
        self._src_item_id: str = ""

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
        留在旧队列：重连后线路上事件齐全、消费端却零事件，lecture 从此静默哑掉。
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
            # 限时 close 握手（端点不回 close 帧；默认 10s 会卡模式切换/收尾，
            # 见 docs/translate_lecture_improvements.md）；到点由库关 socket，无泄漏
            close_timeout=1,
        )
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.created":
                raise ProviderError(
                    f"expected session.created, got {msg.get('type')}",
                    provider="alibaba_livetranslate",
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
                    provider="alibaba_livetranslate",
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
        self._resp_text = ""
        self._src_text = ""
        self._src_item_id = ""
        self._event_id = 0
        self._drain_queue()
        await self._start_receiver()
        logger.info("alibaba livetranslate reconnected")
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
                                    "New translate response (turn boundary)",
                                    extra={"prev_response_id": prev, "response_id": response_id},
                                )
                            # Drain stale OUTPUT events, preserve INPUT events
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
                            self._resp_text = ""
                            await self._queue.put(
                                OmniEvent(type=OmniEventType.RESPONSE_STARTED, response_id=response_id)
                            )

                    elif event_type == "response.audio_transcript.delta":
                        # 3.8: delta 为纯增量，直传（引擎按增量追加）
                        response_id = _get_response_id(msg)
                        if response_id and response_id == self._current_response_id:
                            delta = msg.get("delta", "")
                            if delta:
                                self._resp_text += delta
                                await self._queue.put(
                                    OmniEvent(
                                        type=OmniEventType.TEXT_DELTA,
                                        text=delta,
                                        response_id=response_id,
                                    )
                                )

                    elif event_type == "response.audio_transcript.done":
                        # 兜底：整段比已直传更长（增量缺失/截断）才补差，正常不重复
                        response_id = _get_response_id(msg)
                        if response_id and response_id == self._current_response_id:
                            transcript = msg.get("transcript", "")
                            if len(transcript) > len(self._resp_text):
                                delta = transcript[len(self._resp_text):]
                                self._resp_text = transcript
                                await self._queue.put(
                                    OmniEvent(
                                        type=OmniEventType.TEXT_DELTA,
                                        text=delta,
                                        response_id=response_id,
                                    )
                                )

                    elif event_type == "response.text.delta":
                        # text-only 模式（D2=A）：同 audio_transcript.delta
                        response_id = _get_response_id(msg)
                        if response_id and response_id == self._current_response_id:
                            delta = msg.get("delta", "")
                            if delta:
                                self._resp_text += delta
                                await self._queue.put(
                                    OmniEvent(
                                        type=OmniEventType.TEXT_DELTA,
                                        text=delta,
                                        response_id=response_id,
                                    )
                                )

                    elif event_type == "response.text.done":
                        response_id = _get_response_id(msg)
                        if response_id and response_id == self._current_response_id:
                            text = msg.get("text", "")
                            if len(text) > len(self._resp_text):
                                delta = text[len(self._resp_text):]
                                self._resp_text = text
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

                    elif event_type == "conversation.item.input_audio_transcription.delta":
                        # 3.8: delta 纯增量 → 累计回全量再上抛。引擎 transcription 消息为
                        # 覆盖语义（lecture_translate._send_transcription 直传 ev.text），
                        # 纯增量直传会把客户端原文区打碎成"只剩最后一截"
                        item_id = msg.get("item_id", "")
                        if item_id and item_id != self._src_item_id:
                            self._src_text = ""
                            self._src_item_id = item_id
                        self._src_text += msg.get("delta", "") or ""
                        await self._queue.put(
                            OmniEvent(
                                type=OmniEventType.INPUT_TRANSCRIPTION_DELTA,
                                text=self._src_text,
                                response_id="",
                            )
                        )

                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        response_id = _get_response_id(msg)
                        transcript = msg.get("transcript", "")
                        # 空 transcript（3.8 对静默微回合会发，2026-10-10 实测）不下发
                        # 空 final——避免客户端清空原文区/建空转写组；累计器仍重置
                        if transcript:
                            await self._queue.put(
                                OmniEvent(
                                    type=OmniEventType.INPUT_TRANSCRIPTION_DONE,
                                    text=transcript,
                                    is_final=True,
                                    response_id=response_id or "",
                                )
                            )
                        self._src_text = ""
                        self._src_item_id = ""

                    elif event_type == "input_audio_buffer.speech_started":
                        # best-effort: LiveTranslate may not send this
                        response_id = _get_response_id(msg)
                        await self._queue.put(
                            OmniEvent(type=OmniEventType.SPEECH_STARTED, response_id=response_id or "")
                        )

                    elif event_type == "input_audio_buffer.speech_stopped":
                        response_id = _get_response_id(msg)
                        await self._queue.put(
                            OmniEvent(type=OmniEventType.SPEECH_STOPPED, response_id=response_id or "")
                        )

                    elif event_type == "error":
                        error_msg = msg.get("error", {}).get("message", "") if isinstance(msg.get("error"), dict) else str(msg.get("error", ""))
                        error_code = msg.get("error", {}).get("code", "") if isinstance(msg.get("error"), dict) else ""
                        logger.warning(
                            "livetranslate provider error",
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
                "alibaba livetranslate receive loop crashed",
                extra={"response_id": self._current_response_id},
            )
            if self._current_response_id is not None:
                await self._queue.put(_SENTINEL)
        finally:
            logger.info(
                "alibaba livetranslate receive loop exited",
                extra={"reason": exit_reason, "response_id": self._current_response_id},
            )

    # -- OmniStream interface ------------------------------------------------

    async def feed_audio(self, pcm: bytes) -> None:
        try:
            await self._send_audio_append(pcm)
        except Exception:
            logger.warning("livetranslate feed_audio failed, attempting reconnect")
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError("livetranslate reconnect failed", provider="alibaba_livetranslate")
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
        try:
            await self._send_image_item(jpeg, width=width, height=height)
        except Exception:
            logger.warning("livetranslate feed_image failed, attempting reconnect")
            try:
                await self._reconnect()
            except Exception:
                raise ProviderError("livetranslate reconnect failed", provider="alibaba_livetranslate")
            await self._send_image_item(jpeg, width=width, height=height)

    async def _send_image_item(self, jpeg: bytes, *, width: int, height: int) -> None:
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
        # server auto-VAD; no manual commit needed
        pass

    async def events(self) -> AsyncIterator[OmniEvent]:
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                return
            if not isinstance(item, OmniEvent):
                raise TypeError(f"Unexpected item in translate event queue: {type(item)}")
            yield item

    async def cancel_response(self) -> None:
        # §4.4: LiveTranslate does not support response.cancel / input_audio_buffer.clear
        logger.debug("livetranslate cancel_response called (no-op: API does not support cancel)")

    async def reset(self) -> None:
        self._current_response_id = None
        self._resp_text = ""
        self._src_text = ""
        self._src_item_id = ""
        self._drain_queue()
        if self._recv_task is None or self._recv_task.done():
            logger.warning(
                "livetranslate receiver task dead, restarting",
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


class LiveTranslateProvider:
    name = "alibaba_livetranslate"
    supports_vision = True

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        voice: str,
        source_language: str = "en",
        target_language: str = "en",
        corpus_phrases: dict[str, str] | None = None,
        input_sample_rate: int = 16000,
        enable_voice_clone: bool = False,
        voice_clone_frequency: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._workspace_id = workspace_id
        self._source_language = source_language
        self._target_language = target_language
        self._corpus_phrases = corpus_phrases
        self._input_sample_rate = input_sample_rate
        self._enable_voice_clone = enable_voice_clone
        self._voice_clone_frequency = voice_clone_frequency

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
                # 同 _reconnect：限时 close 握手，避免关闭阻塞 10s
                close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"alibaba livetranslate connect failed: {exc}", provider="alibaba_livetranslate") from exc

        # stdlib logging 的 extra= 会被服务端 structlog 渲染丢弃，关键信息写进消息体
        logger.info("alibaba livetranslate open model=%s workspace=%s",
                    self._model, self._workspace_id or "-")

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.created":
                raise ProviderError(
                    f"expected session.created, got {msg.get('type')}", provider="alibaba_livetranslate"
                )
            session_id = msg.get("session", {}).get("session_id", "")
            _log_msg("recv", "session.created", session_id=session_id)
        except TimeoutError:
            await ws.close()
            raise ProviderError("timeout waiting for session.created", provider="alibaba_livetranslate")
        except ProviderError:
            await ws.close()
            raise
        except Exception as exc:
            # 非 session.created（错误帧/JSON 解析失败等）同样必须关连接，否则 WS 泄漏
            await ws.close()
            raise ProviderError(
                f"alibaba livetranslate init failed: {exc}", provider="alibaba_livetranslate"
            ) from exc

        # §3.3.1: session.update — translation only, no instructions/search/turn_detection.
        # 3.8: output_modalities 替换旧 modalities（旧字段被静默忽略，docs/... §4.2 V1）；
        # ASR 常开（不可关、免费），不再发 input_audio_transcription（§5 D4）。
        session_cfg: dict = {
            "output_modalities": list(modalities),
            "voice": voice or self._voice,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "sample_rate": self._input_sample_rate,
            "translation": {
                "language": self._target_language,
            },
        }

        if self._corpus_phrases:
            session_cfg["translation"]["corpus"] = {"phrases": self._corpus_phrases}

        if self._enable_voice_clone:
            session_cfg["enable_voice_clone"] = True
            if self._voice_clone_frequency:
                session_cfg["voice_clone_options"] = {"frequency": self._voice_clone_frequency}

        update = {
            "event_id": "evt_session_update",
            "type": "session.update",
            "session": session_cfg,
        }
        _log_msg("send", "session.update", event_id="evt_session_update",
                 output_modalities=list(modalities), voice=voice or self._voice,
                 source_language=self._source_language, target_language=self._target_language)

        try:
            await ws.send(json.dumps(update))
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") != "session.updated":
                raise ProviderError(
                    f"expected session.updated, got {msg.get('type')}", provider="alibaba_livetranslate"
                )
            _log_msg("recv", "session.updated", session_id=session_id)
        except ProviderError:
            await ws.close()
            raise
        except Exception as exc:
            await ws.close()
            raise ProviderError(f"alibaba livetranslate init failed: {exc}", provider="alibaba_livetranslate") from exc

        stream = _RealtimeTranslateStream(
            ws, update, self._base_url, self._api_key, workspace_id=self._workspace_id,
        )
        await stream._start_receiver()
        return stream
