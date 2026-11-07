"""全模态（omni）模式子引擎 —— DialogOrchestrator 的第四模式引擎。

生命周期由 DialogOrchestrator 驱动：进入 omni 模式 `open()`，退出 / 会话结束 `close()`。
事件映射与下行 / 落库 helper 移植自原 `OmniOrchestrator`（连接级路由已废弃，该文件删除，
git history 存档，见 `docs/fd_pipeline_omni_mode.md` §4 D2）；结构对齐 `LectureTranslateEngine`。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

from media.buffer import AudioFrameBuffer
from omni.base import OmniEventType
from session.state_machine import SessionState
from shared.utils.errors import ProviderError
from shared.utils.ids import generate_message_id, generate_transcription_id
from shared.utils.time import now_ms

logger = logging.getLogger("livachat.omni_mode")


class OmniModeEngine:
    """Owns handler/codec/ctx directly for frame construction (与 lecture 引擎同款)。

    * PCM 输入由编排器解码后喂入（`feed_pcm`）；图片由编排器 best-effort 喂入（`feed_image`）。
    * 下行音频复用会话级 Opus 编码器（16 kHz，与 provider nested `audio.output.format` 一致）。
    """

    _RETRY_COOLDOWN_S = 5.0

    def __init__(
        self,
        *,
        handler: Any,
        codec: Any,
        ctx: Any,
        history: Any | None,
        interrupt: Any,
        omni_service: Any,
        system_prompt: str = "",
        voice: str = "",
        generation_opts: dict[str, Any] | None = None,
        sample_rate: int = 16000,
        opus_encoder: Any = None,
        chat_store: Any = None,
        recording_sink: Any = None,
        agent_publisher: Any = None,
    ) -> None:
        self._handler = handler
        self._codec = codec
        self._ctx = ctx
        self._history = history
        self._interrupt = interrupt
        self._omni_service = omni_service
        self._system_prompt = system_prompt
        self._voice = voice
        self._gen_opts = generation_opts or {}
        self._sample_rate = sample_rate
        self._opus_enc = opus_encoder
        self._chat_store = chat_store
        self._recording_sink = recording_sink
        self._agent_publisher = agent_publisher

        self._stream: Any = None
        self._pump_task: asyncio.Task[None] | None = None
        self._open_fail_time: float = 0.0

        frame_samples = opus_encoder.frame_size if opus_encoder else int(sample_rate * 20 / 1000)
        self._audio_out_buf = AudioFrameBuffer(frame_samples)

        # Per-response state
        self._message_id: str = ""
        self._full_text: str = ""
        self._text_index: int = 0
        self._audio_index: int = 0
        self._tid: str = ""
        self._response_epoch: int = 0
        # 响应级图像快照：RESPONSE_STARTED 从 buffer 快照（= 本响应最后 feed 的帧），
        # RESPONSE_DONE 回带该引用；绝不取回复时刻的 buffer 最新帧（与响应内容不匹配）。
        self._response_image_payload: Any = None

    # -- public API --------------------------------------------------------

    async def open(self) -> None:
        """开流并启动事件泵；失败抛 ProviderError（由编排器转 4001，见设计 D4）。"""
        stream, provider_name = await self._omni_service.open(
            sample_rate=self._sample_rate,
            system_prompt=self._system_prompt,
            voice=self._voice,
            modalities=("text", "audio"),
            generation_opts=self._gen_opts,
        )
        self._stream = stream
        self._open_fail_time = 0.0
        if self._ctx is not None:
            self._ctx.llm_provider = provider_name
        self._wire_pump()
        logger.info(
            "omni mode stream opened",
            extra={"session_id": getattr(self._ctx, "session_id", "none"), "provider": provider_name},
        )

    async def feed_pcm(self, pcm: bytes) -> None:
        if not await self._ensure_stream():
            return
        try:
            await self._stream.feed_audio(pcm)
        except ProviderError:
            await self._drop_stream("omni feed_audio failed", send_error=True)
        except Exception:
            await self._drop_stream("omni feed_audio failed (unexpected)", send_error=True, exc_info=True)

    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None:
        if not await self._ensure_stream():
            return
        try:
            await self._stream.feed_image(jpeg, width=width, height=height)
        except ProviderError:
            await self._drop_stream("omni feed_image failed", send_error=False)
        except Exception:
            await self._drop_stream("omni feed_image failed (unexpected)", send_error=False, exc_info=True)

    async def interrupt(self, reason: str) -> None:
        """客户端 interrupt：SM/epoch 已由编排器推进（与 lecture 分流同款），此处只停播复位。"""
        await self._cancel_response_and_reset(reason)

    async def close(self) -> None:
        await self._cancel_pump()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                await stream.close()
            except Exception:
                pass
        self._reset_turn()
        logger.info(
            "omni mode engine closed",
            extra={"session_id": getattr(self._ctx, "session_id", "none")},
        )

    # -- stream lifecycle --------------------------------------------------

    async def _ensure_stream(self) -> bool:
        """懒重开：流被服务端终结 / feed 失败后，下一帧按冷却窗口重开（旧实现同款）。"""
        if self._stream is not None:
            return True
        now = time.monotonic()
        if now - self._open_fail_time < self._RETRY_COOLDOWN_S:
            return False
        try:
            await self.open()
        except ProviderError:
            self._open_fail_time = now
            logger.warning(
                "omni reopen failed, entering cooldown",
                extra={"session_id": getattr(self._ctx, "session_id", "none")},
            )
            await self._send_error(1007, "omni unavailable")
            return False
        except Exception:
            self._open_fail_time = now
            logger.warning(
                "omni reopen failed (unexpected)",
                extra={"session_id": getattr(self._ctx, "session_id", "none")},
                exc_info=True,
            )
            await self._send_error(1007, "omni unavailable")
            return False
        logger.info(
            "omni stream reopened on next audio",
            extra={"session_id": getattr(self._ctx, "session_id", "none")},
        )
        return True

    async def _drop_stream(self, message: str, *, send_error: bool, exc_info: bool = False) -> None:
        logger.warning(
            message,
            extra={"session_id": getattr(self._ctx, "session_id", "none")},
            exc_info=exc_info,
        )
        # 先停旧泵再关流：close() 只取消 provider 接收任务，不向事件队列投哨兵——
        # 留着旧泵会永久 park 在旧队列上（任务 + 流对象泄漏）；若由泵内路径（自动打断
        # 的 cancel_response 失败）触发则跳过，避免自我 await 取消态。
        if self._pump_task is not None and self._pump_task is not asyncio.current_task():
            await self._cancel_pump()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                await stream.close()
            except Exception:
                pass
        self._open_fail_time = time.monotonic()
        if send_error:
            await self._send_error(1007, "omni unavailable")

    async def _cancel_response_and_reset(self, reason: str) -> None:
        # 旧响应的快照清理必须在任何 await 之前同步完成：事件泵是独立 task，
        # 下方 cancel_response 的 WS 往返期间，新一轮响应的 RESPONSE_STARTED
        # 可能已赋好新快照——事后再清理会误杀新快照（该轮 DONE 回带 image=None）。
        self._response_image_payload = None

        if self._stream is not None:
            try:
                await self._stream.cancel_response()
            except ProviderError:
                await self._drop_stream("omni cancel_response failed", send_error=True)
            except Exception:
                logger.warning(
                    "omni cancel_response failed during interrupt",
                    extra={"session_id": getattr(self._ctx, "session_id", "none")},
                    exc_info=True,
                )

        self._audio_out_buf.reset()
        self._tid = ""
        await self._send_status("listening", "interrupted")
        logger.info(
            "omni mode interrupted",
            extra={"session_id": getattr(self._ctx, "session_id", "none"), "reason": reason},
        )

    async def _auto_interrupt(self, reason: str) -> None:
        """引擎自发打断（VAD barge-in）：epoch/SM 由引擎推进（无人代劳）。"""
        await self._interrupt.interrupt(reason)
        await self._cancel_response_and_reset(reason)

    # -- event pump --------------------------------------------------------

    def _wire_pump(self) -> None:
        if self._stream is None:
            return

        def _on_reconnected() -> None:
            # 重连产生新队列：旧泵所 park 的队列已作废，必须重启泵（旧实现同款）
            old = self._pump_task
            if old is not None and not old.done():
                old.cancel()
            self._pump_task = asyncio.create_task(self._event_pump(), name=self._pump_name())

        self._stream._on_reconnected = _on_reconnected
        self._pump_task = asyncio.create_task(self._event_pump(), name=self._pump_name())

    def _pump_name(self) -> str:
        return f"omni-mode-pump-{getattr(self._ctx, 'session_id', 'none')}"

    async def _cancel_pump(self) -> None:
        task = self._pump_task
        self._pump_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _event_pump(self) -> None:
        if self._stream is None:
            return
        try:
            async for ev in self._stream.events():
                if self._ctx is None:
                    continue

                if ev.type == OmniEventType.INPUT_TRANSCRIPTION_DELTA:
                    if not self._tid:
                        self._tid = generate_transcription_id()
                    await self._send_transcription(ev.text, is_final=False, transcription_id=self._tid)

                elif ev.type == OmniEventType.INPUT_TRANSCRIPTION_DONE:
                    if not self._tid:
                        self._tid = generate_transcription_id()
                    await self._send_transcription(ev.text, is_final=True, transcription_id=self._tid)
                    if ev.text and self._history is not None:
                        self._history.add_message("user", ev.text)
                        if self._chat_store is not None:
                            self._chat_store.on_message(
                                device_uuid=self._ctx.device_id,
                                session_id=self._ctx.session_id,
                                kind="asr", role="user",
                                content=ev.text,
                                ts_ms=now_ms(),
                            )

                    # Agent Relay: fire-and-forget publish
                    if self._agent_publisher is not None and ev.text:
                        asyncio.create_task(
                            self._agent_publisher.publish_utterance(
                                session_id=self._ctx.session_id,
                                device_id=self._handler.principal.device_id,
                                text=ev.text,
                                transcription_id=self._tid,
                                language="",
                            )
                        )

                    self._tid = ""

                elif ev.type == OmniEventType.SPEECH_STARTED:
                    if self._ctx.state == SessionState.SPEAKING:
                        await self._auto_interrupt("user_voice")

                elif ev.type == OmniEventType.SPEECH_STOPPED:
                    pass  # informational; RESPONSE_STARTED follows

                elif ev.type == OmniEventType.RESPONSE_STARTED:
                    self._response_epoch = self._ctx.epoch
                    # 请求边界快照须先于本分支任何 await：send_status 让出控制权期间，
                    # dispatch 循环可能把响应开始后才到达的帧 append+feed 进 buffer，
                    # 事后快照会把它错捕（回带与响应内容不符）。总是赋值——
                    # 空 buffer → None，防上一响应的陈旧快照泄漏到本响应。
                    self._response_image_payload = self._snapshot_latest_frame()
                    self._ctx.sm.transition_or_reject(SessionState.THINKING)
                    await self._send_status("thinking", "asr_final")
                    self._message_id = generate_message_id()
                    self._full_text = ""
                    self._text_index = 0
                    self._audio_index = 0

                elif ev.type == OmniEventType.TEXT_DELTA:
                    if not self._interrupt.should_yield(self._response_epoch):
                        continue
                    self._full_text += ev.text
                    await self._send_ai_text(ev.text, message_id=self._message_id, index=self._text_index, is_final=False)
                    self._text_index += 1

                elif ev.type == OmniEventType.AUDIO_DELTA:
                    if not self._interrupt.should_yield(self._response_epoch):
                        continue
                    first_audio = (self._audio_index == 0)
                    if first_audio and self._ctx.state != SessionState.SPEAKING:
                        self._ctx.sm.transition_or_reject(SessionState.SPEAKING)
                        await self._send_status("speaking", "tts_start")
                    for frame in self._audio_out_buf.feed(ev.audio):
                        opus_frame = self._opus_enc.encode(frame) if self._opus_enc else frame
                        await self._send_ai_audio(
                            opus_frame,
                            message_id=self._message_id,
                            index=self._audio_index,
                            is_final=False,
                            duration_ms=20,
                        )
                        self._audio_index += 1

                elif ev.type == OmniEventType.RESPONSE_DONE:
                    if not self._interrupt.should_yield(self._response_epoch):
                        continue
                    # Flush remaining audio in buffer
                    remaining = self._audio_out_buf.flush()
                    if remaining:
                        opus_frame = self._opus_enc.encode(remaining) if self._opus_enc else remaining
                        await self._send_ai_audio(
                            opus_frame,
                            message_id=self._message_id,
                            index=self._audio_index,
                            is_final=False,
                            duration_ms=20,
                        )
                        self._audio_index += 1

                    # 回带请求边界（RESPONSE_STARTED）快照的引用；不从 buffer 取
                    # 回复时刻最新帧——响应播放期间到达的帧属于下一轮，与本响应
                    # 内容无关。buffer 不在此清空：期间帧已 feed 给模型，是下一
                    # 响应快照的合法候选（K=1 + 5s 空闲清空约束不变）。
                    await self._send_ai_text("", message_id=self._message_id, index=self._text_index, is_final=True,
                                             image=self._response_image_payload)
                    if self._full_text and self._history is not None:
                        self._history.add_message("assistant", self._full_text)
                        if self._chat_store is not None:
                            self._chat_store.on_message(
                                device_uuid=self._ctx.device_id,
                                session_id=self._ctx.session_id,
                                kind="llm", role="assistant",
                                content=self._full_text,
                                ref_id=self._message_id,
                                ts_ms=now_ms(),
                            )

                    # -- recording tap (non-blocking) --
                    if self._full_text and self._recording_sink is not None:
                        try:
                            self._recording_sink.on_chat_text(
                                role="assistant", text=self._full_text, ts_ms=now_ms(),
                                ref_id=self._message_id, chat_session_id=self._ctx.session_id,
                            )
                        except Exception:
                            pass

                    self._ctx.sm.transition_or_reject(SessionState.LISTENING)
                    await self._send_status("listening", "tts_finish")
                    self._reset_turn()

                elif ev.type == OmniEventType.ERROR:
                    logger.warning(
                        "omni event pump received error",
                        extra={"session_id": self._ctx.session_id, "error": ev.error_message},
                    )
                    await self._send_error(1007, ev.error_message)

        except asyncio.CancelledError:
            return  # 编排器主动关闭/重启泵，非流终结
        except Exception:
            if self._ctx is not None:
                logger.exception(
                    "omni mode event pump crashed",
                    extra={"session_id": self._ctx.session_id},
                )

        # 流终结（服务端 session.finished 哨兵 / 接收循环异常）：置空流 + 冷却，
        # 下一帧由 feed_* → _ensure_stream 懒重开；重开失败才发 1007。
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                await stream.close()
            except Exception:
                pass
        self._open_fail_time = time.monotonic()
        logger.warning(
            "omni mode stream ended; will reopen on next audio",
            extra={"session_id": getattr(self._ctx, "session_id", "none")},
        )

    # -- per-turn helpers --------------------------------------------------

    def _snapshot_latest_frame(self) -> Any | None:
        """从 buffer 最新帧构建 ServerAiTextImage 快照；无帧/未开视频 → None。"""
        if self._ctx is None or not self._ctx.enable_video or not self._ctx.latest_video_frames:
            return None
        from livachat_protocol.generated.protocol_models import ServerAiTextImage

        _, raw_jpeg, _, width, height, orientation, camera_facing = self._ctx.latest_video_frames[-1]
        return ServerAiTextImage(
            data=base64.b64encode(raw_jpeg).decode(),
            mime="image/jpeg",
            width=width,
            height=height,
            orientation=orientation,
            camera_facing=camera_facing,
        )

    def _reset_turn(self) -> None:
        self._message_id = ""
        self._full_text = ""
        self._text_index = 0
        self._audio_index = 0
        self._tid = ""
        self._response_image_payload = None
        self._audio_out_buf.reset()

    # -- send helpers (移植自原 OmniOrchestrator) --------------------------

    async def _send_transcription(self, text: str, *, is_final: bool, transcription_id: str = "") -> None:
        from livachat_protocol.generated.protocol_models import ServerTranscriptionPayload
        from livachat_protocol.generated.server_message import ServerTranscription

        tid = transcription_id or generate_transcription_id()
        frame = self._codec.build_typed(
            ServerTranscriptionPayload(text=text, is_final=is_final, transcription_id=tid),
            msg_cls=ServerTranscription,
        )
        await self._handler.enqueue_outbound(frame)

        # -- recording tap (non-blocking) --
        if is_final and self._recording_sink is not None and self._ctx is not None:
            try:
                self._recording_sink.on_chat_text(
                    role="user", text=text, ts_ms=now_ms(),
                    ref_id=tid, chat_session_id=self._ctx.session_id,
                )
            except Exception:
                pass

    async def _send_ai_text(self, text: str, *, message_id: str, index: int, is_final: bool,
                            image: Any | None = None) -> None:
        from livachat_protocol.generated.protocol_models import ServerAiTextPayload
        from livachat_protocol.generated.server_message import ServerAiText

        kwargs: dict[str, Any] = {"text": text, "message_id": message_id, "index": index, "is_final": is_final}
        if image is not None:
            kwargs["image"] = image
        payload = ServerAiTextPayload(**kwargs)
        frame = self._codec.build_typed(payload, msg_cls=ServerAiText)
        await self._handler.enqueue_outbound(frame)

    async def _send_ai_audio(self, data: bytes, *, message_id: str, index: int, is_final: bool,
                             duration_ms: int = 20) -> None:
        from livachat_protocol.generated.protocol_models import ServerAiAudioPayload
        from livachat_protocol.generated.server_message import ServerAiAudio

        frame = self._codec.build_typed(
            ServerAiAudioPayload(
                data=base64.b64encode(data).decode() if data else "",
                codec="opus",
                message_id=message_id,
                index=index,
                duration_ms=duration_ms,
                is_final=is_final,
            ),
            msg_cls=ServerAiAudio,
        )
        await self._handler.enqueue_outbound(frame)

    async def _send_status(self, session_status: str, reason: str) -> None:
        frame = self._codec.make_status(session_status=session_status, reason=reason)
        await self._handler.enqueue_outbound(frame)

    async def _send_error(self, code: int, message: str) -> None:
        frame = self._codec.make_error(code=code, message=message, fatal=False)
        await self._handler.enqueue_outbound(frame)
