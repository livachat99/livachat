"""Lecture Translate Engine — LiveTranslate streaming interpretation in pipeline mode.

Wraps the omni LiveTranslateProvider (read-only import, no omni code modified).
Owns its own handler + codec for frame construction, and its own 24 kHz
OpusEncoder + AudioFrameBuffer for output.

See: docs/fd_pipeline_translate_lecture.md
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Literal

from media.buffer import AudioFrameBuffer
from omni.base import OmniEventType
from session.state_machine import SessionState
from shared.utils.errors import ErrorCode
from shared.utils.ids import generate_message_id, generate_transcription_id
from shared.utils.time import now_ms

logger = logging.getLogger("livachat.lecture_translate")


class LectureTranslateEngine:
    """LiveTranslate streaming interpretation sub-engine inside DialogOrchestrator.

    Lifecycle driven by DialogOrchestrator: open() on lecture entry, close() on exit/end.
    Imports (does not modify) omni's LiveTranslateProvider and event types.
    Owns handler + codec directly for frame construction — does not depend on
    orchestrator internal methods.
    """

    def __init__(
        self,
        *,
        handler: Any,               # ConnectionHandler
        codec: Any,                 # WireCodec
        ctx: Any,                   # SessionContext
        interrupt: Any,             # InterruptController
        cfg: Any,                   # TranslateConfig
        recording_sink: Any = None,
    ):
        self._handler = handler
        self._codec = codec
        self._ctx = ctx
        self._interrupt = interrupt
        self._cfg = cfg
        self._recording_sink = recording_sink

        # Own 24 kHz OpusEncoder + AudioFrameBuffer (not the session 16 kHz ones)
        from media.opus import OpusEncoder

        frame_ms = 20
        self._opus_enc = OpusEncoder(
            sample_rate=cfg.output_sample_rate,
            frame_ms=frame_ms,
        )
        self._audio_out_buf = AudioFrameBuffer(self._opus_enc.frame_size)

        # Runtime state
        self._stream: Any = None           # OmniStream
        self._event_pump_task: asyncio.Task[None] | None = None
        self._response_epoch: int = 0
        # 流终结/喂帧失败：已向客户端报错一次、此后 feed_pcm 静默丢弃
        # （失败不降级，docs/android_audio_capture.md §5.2）
        self._stream_ended: bool = False
        self._failure_reported: bool = False

        # Per-response state
        self._message_id: str = ""
        self._current_transcription_id: str = ""
        self._full_text: str = ""
        self._text_index: int = 0
        self._audio_index: int = 0

    # -- public API --------------------------------------------------------

    async def open(self, *, my_language: str, local_language: str) -> None:
        """Open the LiveTranslate stream and start the event pump."""
        from omni.providers.alibaba_livetranslate import LiveTranslateProvider

        provider = LiveTranslateProvider(
            base_url=self._cfg.base_url,
            api_key=self._cfg.api_key,
            model=self._cfg.model,
            voice=self._cfg.voice or "",
            workspace_id=self._cfg.workspace_id,
            source_language=local_language or self._cfg.source_language,
            target_language=my_language or self._cfg.target_language,
            corpus_phrases=self._cfg.corpus_phrases,
            input_sample_rate=self._cfg.input_sample_rate,
            enable_voice_clone=self._cfg.enable_voice_clone,
            voice_clone_frequency=self._cfg.voice_clone_frequency,
        )

        self._stream = await provider.open(
            sample_rate=self._cfg.input_sample_rate,
            modalities=("text",),
        )
        self._stream_ended = False
        self._failure_reported = False

        # Record provider name for usage tracking
        provider_name = "alibaba_livetranslate"
        self._ctx.asr_provider = provider_name
        self._ctx.llm_provider = provider_name
        self._ctx.tts_provider = provider_name

        # Start event pump
        self._event_pump_task = asyncio.create_task(
            self._event_pump(),
            name=f"lecture-pump-{self._ctx.session_id}",
        )

        logger.info(
            "lecture translate engine opened",
            extra={
                "session_id": self._ctx.session_id,
                "source_language": local_language,
                "target_language": my_language,
            },
        )

    async def feed_pcm(self, pcm: bytes) -> None:
        """Feed 16 kHz PCM audio directly to the LiveTranslate stream.

        失败不降级（docs/android_audio_capture.md §5.2）：喂帧失败记为失败态并只向客户端
        报错一次，不上抛；流终结后静默丢弃。
        """
        if self._stream_ended:
            return
        if self._stream is None:
            return
        try:
            await self._stream.feed_audio(pcm)
        except Exception:
            await self._mark_failed("feed_audio failed")

    async def _mark_failed(self, reason: str) -> None:
        """流失败（流终结/喂帧失败）：仅向客户端报错一次，不降级、不关闭引擎
        （docs/android_audio_capture.md §5.2）。"""
        self._stream_ended = True
        if self._failure_reported:
            return
        self._failure_reported = True
        logger.warning(
            "lecture translate stream failed; reported to client",
            extra={"session_id": self._ctx.session_id if self._ctx else "none", "reason": reason},
        )
        frame = self._codec.make_error(
            code=ErrorCode.TRANSLATE_UNAVAILABLE,
            message="lecture translate unavailable",
            fatal=False,
        )
        await self._handler.enqueue_outbound(frame)

    async def interrupt(self, reason: str) -> None:
        """Handle client interrupt: cancel response (no-op), reset buffers, send status."""
        if self._stream:
            try:
                await self._stream.cancel_response()
            except Exception:
                pass

        self._audio_out_buf.reset()
        self._current_transcription_id = ""

        await self._send_status("listening", "interrupted")

        logger.info(
            "lecture translate interrupted",
            extra={"session_id": self._ctx.session_id, "reason": reason},
        )

    async def close(self) -> None:
        """Cancel event pump and close the translate stream."""
        if self._event_pump_task is not None:
            self._event_pump_task.cancel()
            try:
                await self._event_pump_task
            except (asyncio.CancelledError, Exception):
                pass
            self._event_pump_task = None

        if self._stream is not None:
            try:
                await self._stream.close()
            except Exception:
                pass
            self._stream = None

        logger.info(
            "lecture translate engine closed",
            extra={"session_id": self._ctx.session_id if self._ctx else "none"},
        )

    # -- event pump --------------------------------------------------------

    async def _event_pump(self) -> None:
        """Consume OmniEvent from translate stream → protocol messages.

        Copied/adapted from OmniOrchestrator._event_pump (same mapping semantics).
        """
        if self._stream is None:
            return
        try:
            async for ev in self._stream.events():
                if not self._ctx or not self._interrupt:
                    continue

                if ev.type == OmniEventType.INPUT_TRANSCRIPTION_DELTA:
                    if not self._current_transcription_id:
                        self._current_transcription_id = generate_transcription_id()
                    await self._send_transcription(
                        ev.text, is_final=False,
                        transcription_id=self._current_transcription_id,
                    )

                elif ev.type == OmniEventType.INPUT_TRANSCRIPTION_DONE:
                    if not self._current_transcription_id:
                        self._current_transcription_id = generate_transcription_id()
                    await self._send_transcription(
                        ev.text, is_final=True,
                        transcription_id=self._current_transcription_id,
                    )
                    self._current_transcription_id = ""

                elif ev.type == OmniEventType.SPEECH_STARTED:
                    # Lecture mode: disable VAD barge-in (design D7).
                    # API manages its own turn boundaries.
                    pass

                elif ev.type == OmniEventType.SPEECH_STOPPED:
                    pass

                elif ev.type == OmniEventType.RESPONSE_STARTED:
                    self._response_epoch = self._ctx.epoch
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
                    await self._send_ai_text(
                        ev.text,
                        message_id=self._message_id,
                        index=self._text_index,
                        is_final=False,
                    )
                    self._text_index += 1

                elif ev.type == OmniEventType.AUDIO_DELTA:
                    if not self._interrupt.should_yield(self._response_epoch):
                        continue
                    first_audio = (self._audio_index == 0)
                    if first_audio and self._ctx.state != SessionState.SPEAKING:
                        self._ctx.sm.transition_or_reject(SessionState.SPEAKING)
                        await self._send_status("speaking", "tts_start")
                    # PCM(24k) → buffer → 24k Opus encode → ai_audio
                    for frame in self._audio_out_buf.feed(ev.audio):
                        opus_frame = self._opus_enc.encode(frame)
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
                        opus_frame = self._opus_enc.encode(remaining)
                        await self._send_ai_audio(
                            opus_frame,
                            message_id=self._message_id,
                            index=self._audio_index,
                            is_final=False,
                            duration_ms=20,
                        )
                        self._audio_index += 1

                    await self._send_ai_text(
                        "", message_id=self._message_id,
                        index=self._text_index, is_final=True,
                    )

                    # -- recording tap (non-blocking) --
                    if self._full_text and self._recording_sink is not None and self._ctx is not None:
                        try:
                            self._recording_sink.on_chat_text(
                                role="assistant", text=self._full_text, ts_ms=now_ms(),
                                ref_id=self._message_id, chat_session_id=self._ctx.session_id,
                                source="lecture",
                            )
                        except Exception:
                            pass

                    self._ctx.sm.transition_or_reject(SessionState.LISTENING)
                    await self._send_status("listening", "tts_finish")
                    self._reset_turn()

                elif ev.type == OmniEventType.ERROR:
                    logger.warning(
                        "lecture translate event pump received error",
                        extra={
                            "session_id": self._ctx.session_id,
                            "error": ev.error_message,
                        },
                    )
                    frame = self._codec.make_error(
                        code=1008,
                        message=ev.error_message,
                        fatal=False,
                    )
                    await self._handler.enqueue_outbound(frame)

        except asyncio.CancelledError:
            return  # 引擎主动关闭，非流终结
        except Exception:
            if self._ctx:
                logger.exception(
                    "lecture translate event pump crashed",
                    extra={"session_id": self._ctx.session_id},
                )

        # 流终结（服务端 session.finished 哨兵 / 接收循环异常）：消费端已退出且不会自行复活。
        # 失败不降级（docs/android_audio_capture.md §5.2）：只向客户端报错一次；此后 feed_pcm 静默丢弃。
        await self._mark_failed("stream ended")

    def _reset_turn(self) -> None:
        self._message_id = ""
        self._full_text = ""
        self._text_index = 0
        self._audio_index = 0
        self._audio_out_buf.reset()

    # -- send helpers ------------------------------------------------------

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
                # lecture 同传文本轨落 chat/translate.jsonl（source="lecture"，
                # 与 dialogue translate 的 source="translate" 区分）
                self._recording_sink.on_chat_text(
                    role="user", text=text, ts_ms=now_ms(),
                    ref_id=tid, chat_session_id=self._ctx.session_id,
                    source="lecture",
                )
            except Exception:
                pass

    async def _send_ai_text(self, text: str, *, message_id: str, index: int, is_final: bool) -> None:
        from livachat_protocol.generated.protocol_models import ServerAiTextPayload
        from livachat_protocol.generated.server_message import ServerAiText

        payload = ServerAiTextPayload(
            text=text, message_id=message_id, index=index, is_final=is_final,
        )
        frame = self._codec.build_typed(payload, msg_cls=ServerAiText)
        await self._handler.enqueue_outbound(frame)

    async def _send_ai_audio(self, data: bytes, *, message_id: str, index: int, is_final: bool, duration_ms: Literal[20, 40] = 20) -> None:
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
