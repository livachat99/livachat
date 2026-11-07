"""Unit tests for LectureTranslateEngine and DialogOrchestrator lecture mode."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from session.orchestrator import DialogOrchestrator
from session.state_machine import SessionState
from tests.unit.session.conftest import FakeChatStore

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _handler_mock():
    """Create a handler mock with AsyncMock for enqueue_outbound (same pattern as test_translate_mode.py)."""
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._inbound = MagicMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _make_orch_with_lecture_cfg(
    *,
    pipeline_mode: str = "full",
    translate_enabled: bool = True,
    my_language: str | None = None,
    local_language: str | None = None,
) -> DialogOrchestrator:
    """Create a DialogOrchestrator with translate config for testing."""
    from shared.config.models import TranslateConfig

    cfg = TranslateConfig(
        enabled=translate_enabled,
        base_url="wss://test.example.com",
        api_key="sk-test",
        model="test-model",
        voice="Test",
        source_language="en",
        target_language="zh",
        input_sample_rate=16000,
        output_sample_rate=24000,
    )

    orch = DialogOrchestrator(
        handler=_handler_mock(),
        codec=MagicMock(),
        session_mgr=None,
        asr_service=None,
        llm_service=None,
        tts_service=None,
        opus_encoder=None,
        opus_decoder=None,
        system_prompt="You are a companion.",
        translate_prompt="You are a translator.",
        generation_opts={},
        chat_store=FakeChatStore(),
        pipeline_mode=pipeline_mode,
        translate_cfg=cfg,
    )
    orch._my_language = my_language
    orch._local_language = local_language
    return orch


def _make_orch_session_started(
    orch: DialogOrchestrator,
    *,
    session_id: str = "test-sid",
    user_id: str = "dev-user",
    device_id: str = "550e8400-e29b-41d4-a716-446655440000",
    state_value: str = "listening",
) -> None:
    """Set up orch with a mock ctx and history to simulate post-session-start state."""
    from session.context import SessionContext
    from session.history import WorkingMemory
    from session.state_machine import SessionState, SessionStateMachine

    sm = SessionStateMachine()
    sm.state = SessionState(state_value)
    ctx = SessionContext(
        session_id=session_id,
        device_id=device_id,
        user_id=user_id,
        sm=sm,
        enable_video=False,
        audio_sample_rate=16000,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._interrupt = MagicMock()
    orch._turn_detector = MagicMock()


# ---------------------------------------------------------------------------
# Lecture Translate Engine — unit tests
# ---------------------------------------------------------------------------


class TestLectureTranslateEngine:
    """Test LectureTranslateEngine in isolation."""

    def _make_engine(self, *, enabled: bool = True, workspace_id: str | None = None):
        from session.lecture_translate import LectureTranslateEngine
        from shared.config.models import TranslateConfig

        cfg = TranslateConfig(
            enabled=enabled,
            base_url="wss://test.example.com",
            api_key="sk-test",
            model="test-model",
            voice="Test",
            workspace_id=workspace_id,
            source_language="en",
            target_language="zh",
            input_sample_rate=16000,
            output_sample_rate=24000,
        )
        handler = MagicMock()
        handler.enqueue_outbound = AsyncMock()
        codec = MagicMock()

        from session.context import SessionContext
        from session.state_machine import SessionState, SessionStateMachine

        sm = SessionStateMachine()
        sm._state = SessionState("listening")
        ctx = SessionContext(
            session_id="eng-sid",
            device_id="550e8400-e29b-41d4-a716-446655440000",
            user_id="dev-user",
            sm=sm,
            enable_video=False,
            audio_sample_rate=16000,
        )

        engine = LectureTranslateEngine(
            handler=handler,
            codec=codec,
            ctx=ctx,
            interrupt=MagicMock(),
            cfg=cfg,
        )
        return engine, handler, codec, ctx

    @pytest.mark.asyncio
    async def test_open_creates_provider_and_starts_pump(self):
        """open() creates a LiveTranslateProvider, opens stream, starts event pump."""
        engine, _handler, _codec, ctx = self._make_engine()

        mock_stream = _make_fake_stream([])

        # Patch LiveTranslateProvider (lazy-imported inside open())
        mock_provider = MagicMock()
        mock_provider.open = AsyncMock(return_value=mock_stream)
        with patch(
            "omni.providers.alibaba_livetranslate.LiveTranslateProvider",
            return_value=mock_provider,
        ):
            await engine.open(my_language="zh", local_language="en")

        mock_provider.open.assert_called_once()
        assert ctx.asr_provider == "alibaba_livetranslate"
        assert ctx.llm_provider == "alibaba_livetranslate"
        assert ctx.tts_provider == "alibaba_livetranslate"
        assert engine._event_pump_task is not None

        await engine.close()

    @pytest.mark.asyncio
    async def test_open_passes_workspace_id(self):
        """cfg.workspace_id 透传 provider 构造（仅驱动 X-DashScope-WorkSpace 头）。"""
        engine, _handler, _codec, _ctx = self._make_engine(workspace_id="llm-x")

        mock_stream = _make_fake_stream([])
        mock_provider = MagicMock()
        mock_provider.open = AsyncMock(return_value=mock_stream)
        with patch(
            "omni.providers.alibaba_livetranslate.LiveTranslateProvider",
            return_value=mock_provider,
        ) as provider_cls:
            await engine.open(my_language="zh", local_language="en")

        assert provider_cls.call_args.kwargs["workspace_id"] == "llm-x"
        await engine.close()

    @pytest.mark.asyncio
    async def test_stream_end_reports_error_once_and_feed_drops(self):
        """流终结（哨兵/泵退出）：仅向客户端报错一次；此后喂帧静默丢弃。
        失败不降级（docs/android_audio_capture.md §5.2）——不关引擎、不回落逐轮。"""
        from shared.utils.errors import ErrorCode

        engine, handler, codec, _ctx = self._make_engine()
        codec.make_error.return_value = "ERRFRAME"
        engine._stream = _make_fake_stream([])  # events() 立即结束 = 流终结

        await engine._event_pump()  # 泵自然退出（非 cancel）

        assert engine._stream_ended is True
        codec.make_error.assert_called_once()
        assert codec.make_error.call_args.kwargs["code"] == ErrorCode.TRANSLATE_UNAVAILABLE
        assert codec.make_error.call_args.kwargs["fatal"] is False
        handler.enqueue_outbound.assert_awaited_once_with("ERRFRAME")

        await engine.feed_pcm(b"\x00" * 640)  # 不抛、不重报
        handler.enqueue_outbound.assert_awaited_once_with("ERRFRAME")

    @pytest.mark.asyncio
    async def test_feed_pcm_failure_reports_once_no_raise(self):
        """喂帧失败（流已死）：内部消化并报错一次、置失败态；不上抛（不降级前提）。"""
        engine, handler, codec, _ctx = self._make_engine()
        codec.make_error.return_value = "ERRFRAME"

        class _DeadStream:
            async def feed_audio(self, pcm):
                raise RuntimeError("stream dead")

        engine._stream = _DeadStream()

        await engine.feed_pcm(b"\x00" * 640)  # 不抛

        assert engine._stream_ended is True
        handler.enqueue_outbound.assert_awaited_once_with("ERRFRAME")

    @pytest.mark.asyncio
    async def test_close_cancel_does_not_report_failure(self):
        """close() 取消泵（CancelledError 分支）不是流终结 → 不报错。"""
        engine, handler, _codec, _ctx = self._make_engine()

        class _HangingStream:
            def events(self):
                async def _gen():
                    await asyncio.Event().wait()  # 永不产出：泵 park 在此
                    yield  # pragma: no cover

                return _gen()

            async def close(self):
                pass

        engine._stream = _HangingStream()
        engine._event_pump_task = asyncio.create_task(engine._event_pump())
        await asyncio.sleep(0.05)

        await engine.close()

        assert engine._stream_ended is False
        handler.enqueue_outbound.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_close_does_not_mark_stream_ended(self):
        """close() 取消泵不算流终结（CancelledError 分支直接 return）。"""
        engine, _handler, _codec, _ctx = self._make_engine()

        class _HangingStream:
            def events(self):
                async def _gen():
                    await asyncio.Event().wait()  # 永不产出：泵 park 在此
                    yield  # pragma: no cover

                return _gen()

            async def close(self):
                pass

        engine._stream = _HangingStream()
        engine._event_pump_task = asyncio.create_task(engine._event_pump())
        await asyncio.sleep(0.05)
        assert not engine._event_pump_task.done()

        await engine.close()

        assert engine._stream_ended is False

    @pytest.mark.asyncio
    async def test_open_uses_dynamic_languages(self):
        """open() passes my_language→target, local_language→source."""
        engine, _handler, _codec, _ctx = self._make_engine()

        mock_stream = _make_fake_stream([])
        mock_provider = MagicMock()
        mock_provider.open = AsyncMock(return_value=mock_stream)

        with patch(
            "omni.providers.alibaba_livetranslate.LiveTranslateProvider",
            return_value=mock_provider,
        ):
            await engine.open(my_language="ja", local_language="en")

        mock_provider.open.assert_called_once()
        await engine.close()

    @pytest.mark.asyncio
    async def test_feed_pcm_delegates_to_stream(self):
        """feed_pcm() sends PCM to the translate stream."""
        engine, _handler, _codec, _ctx = self._make_engine()

        mock_stream = AsyncMock()
        mock_stream.feed_audio = AsyncMock()
        engine._stream = mock_stream

        pcm = b"\x00\x00" * 480  # 20ms of 16kHz PCM16
        await engine.feed_pcm(pcm)

        mock_stream.feed_audio.assert_called_once_with(pcm)

    @pytest.mark.asyncio
    async def test_feed_pcm_no_stream_is_noop(self):
        """feed_pcm() is a no-op when no stream is open."""
        engine, _handler, _codec, _ctx = self._make_engine()
        # No stream set — should not raise
        await engine.feed_pcm(b"\x00\x00" * 480)

    @pytest.mark.asyncio
    async def test_close_cancels_pump_and_closes_stream(self):
        """close() cancels event pump and closes provider stream."""
        engine, _handler, _codec, _ctx = self._make_engine()

        mock_stream = AsyncMock()
        mock_stream.close = AsyncMock()
        engine._stream = mock_stream

        # Simulate event pump task
        import asyncio
        async def _fake_pump():
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                pass
        engine._event_pump_task = asyncio.create_task(_fake_pump())

        await engine.close()

        assert engine._event_pump_task is None
        assert engine._stream is None
        mock_stream.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_interrupt_resets_buffers_and_sends_status(self):
        """interrupt() resets audio buffer, sends status(listening, interrupted)."""
        engine, handler, codec, _ctx = self._make_engine()

        mock_stream = AsyncMock()
        mock_stream.cancel_response = AsyncMock()
        engine._stream = mock_stream
        codec.make_status.return_value = '{"type":"status"}'

        await engine.interrupt("user_voice")

        codec.make_status.assert_called_with(session_status="listening", reason="interrupted")
        handler.enqueue_outbound.assert_called()

    @pytest.mark.asyncio
    async def test_send_transcription_constructs_frame(self):
        """_send_transcription builds and enqueues a transcription frame."""
        engine, handler, codec, _ctx = self._make_engine()
        codec.build_typed.return_value = '{"type":"transcription"}'

        with patch("session.lecture_translate.generate_transcription_id", return_value="trans_abc123456789"):
            await engine._send_transcription("Hello", is_final=False)

        codec.build_typed.assert_called_once()
        handler.enqueue_outbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_ai_text_constructs_frame(self):
        """_send_ai_text builds and enqueues an ai_text frame."""
        engine, handler, codec, _ctx = self._make_engine()
        codec.build_typed.return_value = '{"type":"ai_text"}'

        await engine._send_ai_text("Bonjour", message_id="msg_abc123456789", index=0, is_final=False)

        codec.build_typed.assert_called_once()
        handler.enqueue_outbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_ai_audio_constructs_frame(self):
        """_send_ai_audio builds and enqueues an ai_audio frame with base64 opus."""
        engine, handler, codec, _ctx = self._make_engine()
        codec.build_typed.return_value = '{"type":"ai_audio"}'

        opus_frame = b"\xfc\xff\xfe"  # fake opus
        await engine._send_ai_audio(opus_frame, message_id="msg_abc123456789", index=0, is_final=False)

        codec.build_typed.assert_called_once()
        handler.enqueue_outbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_pump_transcription_delta(self):
        """Event pump: INPUT_TRANSCRIPTION_DELTA → _send_transcription(is_final=False)."""
        engine, _handler, _codec, _ctx = self._make_engine()

        from omni.base import OmniEvent, OmniEventType

        events = [
            OmniEvent(type=OmniEventType.INPUT_TRANSCRIPTION_DELTA, text="Hello wor"),
        ]

        mock_stream = _make_fake_stream(events)
        engine._stream = mock_stream

        with patch.object(engine, "_send_transcription", new_callable=AsyncMock) as mock_send:
            await engine._event_pump()

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][0] == "Hello wor"
            assert call_args[1]["is_final"] is False

    @pytest.mark.asyncio
    async def test_event_pump_response_started_done_cycle(self):
        """Event pump: RESPONSE_STARTED → TEXT_DELTA → AUDIO_DELTA → RESPONSE_DONE."""
        engine, _handler, _codec, _ctx = self._make_engine()

        from omni.base import OmniEvent, OmniEventType

        events = [
            OmniEvent(type=OmniEventType.RESPONSE_STARTED),
            OmniEvent(type=OmniEventType.TEXT_DELTA, text="Hello"),
            OmniEvent(type=OmniEventType.RESPONSE_DONE),
        ]

        engine._stream = _make_fake_stream(events)

        with patch.object(engine, "_send_status", new_callable=AsyncMock) as mock_status, \
             patch.object(engine, "_send_ai_text", new_callable=AsyncMock) as mock_text, \
             patch.object(engine, "_send_ai_audio", new_callable=AsyncMock):
            await engine._event_pump()

            # RESPONSE_STARTED → status(thinking)
            thinking_calls = [c for c in mock_status.call_args_list if c[0][0] == "thinking"]
            assert len(thinking_calls) == 1

            # TEXT_DELTA → ai_text
            mock_text.assert_called()

            # RESPONSE_DONE → status(listening)
            listening_calls = [c for c in mock_status.call_args_list if c[0][0] == "listening"]
            assert len(listening_calls) >= 1

    @pytest.mark.asyncio
    async def test_event_pump_drops_stale_response_after_interrupt(self):
        """epoch 递增后，在途 response 的 TEXT/final 必须被丢弃（设计 §6.4）。"""
        from omni.base import OmniEvent, OmniEventType
        from session.interrupt import InterruptController

        engine, _handler, _codec, ctx = self._make_engine()
        engine._interrupt = InterruptController(ctx)

        gate = asyncio.Event()

        async def _events():
            yield OmniEvent(type=OmniEventType.RESPONSE_STARTED)
            await gate.wait()  # 等测试在 RESPONSE_STARTED 之后触发打断
            yield OmniEvent(type=OmniEventType.TEXT_DELTA, text="stale")
            yield OmniEvent(type=OmniEventType.RESPONSE_DONE)

        class _GatedStream:
            def events(self):
                return _events()

        engine._stream = _GatedStream()

        with patch.object(engine, "_send_status", new_callable=AsyncMock), \
             patch.object(engine, "_send_ai_text", new_callable=AsyncMock) as mock_text:
            pump = asyncio.create_task(engine._event_pump())
            await asyncio.sleep(0.05)  # 泵处理完 RESPONSE_STARTED，park 在 gate

            await engine._interrupt.interrupt("user_tap")
            gate.set()
            await pump

            mock_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_pump_audio_delta_first_frame_transitions_speaking(self):
        """First AUDIO_DELTA transitions SM to SPEAKING and sends status."""
        engine, _handler, _codec, ctx = self._make_engine()
        ctx.sm.state = SessionState("thinking")

        import struct

        from omni.base import OmniEvent, OmniEventType

        # 480 samples * 2 bytes = 960 bytes = one 20ms frame at 24kHz
        pcm_frame = struct.pack("<" + "h" * 480, *([0] * 480))

        events = [
            OmniEvent(type=OmniEventType.RESPONSE_STARTED),
            OmniEvent(type=OmniEventType.AUDIO_DELTA, audio=pcm_frame),
            OmniEvent(type=OmniEventType.RESPONSE_DONE),
        ]

        engine._stream = _make_fake_stream(events)

        # Replace real OpusEncoder with mock to avoid actual opus dependency
        mock_enc = MagicMock()
        mock_enc.encode.return_value = b"\xfc\xff"
        mock_enc.frame_size = 480
        engine._opus_enc = mock_enc

        with patch.object(engine, "_send_status", new_callable=AsyncMock) as mock_status, \
             patch.object(engine, "_send_ai_text", new_callable=AsyncMock), \
             patch.object(engine, "_send_ai_audio", new_callable=AsyncMock) as mock_audio:
            await engine._event_pump()

            # First audio frame → status(speaking)
            speaking_calls = [c for c in mock_status.call_args_list if c[0][0] == "speaking"]
            assert len(speaking_calls) == 1

            # ai_audio was called
            mock_audio.assert_called()

    @pytest.mark.asyncio
    async def test_event_pump_error_event_sends_error_frame(self):
        """ERROR event → codec.make_error → enqueue."""
        engine, handler, codec, _ctx = self._make_engine()

        from omni.base import OmniEvent, OmniEventType

        events = [
            OmniEvent(type=OmniEventType.ERROR, error_message="Provider timeout"),
        ]

        engine._stream = _make_fake_stream(events)
        codec.make_error.return_value = '{"type":"error"}'

        with patch.object(engine, "_send_status", new_callable=AsyncMock), \
             patch.object(engine, "_send_ai_text", new_callable=AsyncMock):
            await engine._event_pump()

            # ERROR 事件帧（原样，code=1008 字面量）
            codec.make_error.assert_any_call(
                code=1008, message="Provider timeout", fatal=False,
            )
            # 事件流随即终结 → 失败不降级仅再报一次（TRANSLATE_UNAVAILABLE，docs/android_audio_capture.md §5.2）
            from shared.utils.errors import ErrorCode
            assert codec.make_error.call_count == 2
            assert codec.make_error.call_args.kwargs["code"] == ErrorCode.TRANSLATE_UNAVAILABLE
            handler.enqueue_outbound.assert_called()

    @pytest.mark.asyncio
    async def test_event_pump_speech_started_is_ignored_in_lecture(self):
        """SPEECH_STARTED is ignored in lecture mode (no VAD barge-in, design D7)."""
        engine, _handler, _codec, _ctx = self._make_engine()

        from omni.base import OmniEvent, OmniEventType

        events = [
            OmniEvent(type=OmniEventType.SPEECH_STARTED),
        ]

        engine._stream = _make_fake_stream(events)

        # Should not call interrupt or send_status for SPEECH_STARTED
        with patch.object(engine, "_send_status", new_callable=AsyncMock) as mock_status:
            await engine._event_pump()
            # No status sent for SPEECH_STARTED alone
            mock_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_pump_user_final_tap_source_lecture(self):
        """INPUT_TRANSCRIPTION_DONE → recording tap role=user source="lecture"。"""
        engine, _handler, _codec, _ctx = self._make_engine()
        sink = MagicMock()
        sink.on_chat_text = MagicMock()
        engine._recording_sink = sink

        from omni.base import OmniEvent, OmniEventType

        events = [
            OmniEvent(type=OmniEventType.INPUT_TRANSCRIPTION_DONE, text="演讲原文内容"),
        ]
        engine._stream = _make_fake_stream(events)

        await engine._event_pump()

        sink.on_chat_text.assert_called_once()
        args = sink.on_chat_text.call_args.kwargs
        assert args["role"] == "user"
        assert args["text"] == "演讲原文内容"
        assert args["source"] == "lecture"

    @pytest.mark.asyncio
    async def test_event_pump_assistant_tap_source_lecture(self):
        """RESPONSE_DONE → recording tap role=assistant source="lecture"（译文全文）。"""
        engine, _handler, _codec, _ctx = self._make_engine()
        sink = MagicMock()
        sink.on_chat_text = MagicMock()
        engine._recording_sink = sink

        from omni.base import OmniEvent, OmniEventType

        events = [
            OmniEvent(type=OmniEventType.RESPONSE_STARTED),
            OmniEvent(type=OmniEventType.TEXT_DELTA, text="同传译文全文"),
            OmniEvent(type=OmniEventType.RESPONSE_DONE),
        ]
        engine._stream = _make_fake_stream(events)

        await engine._event_pump()

        sink.on_chat_text.assert_called_once()
        args = sink.on_chat_text.call_args.kwargs
        assert args["role"] == "assistant"
        assert args["text"] == "同传译文全文"
        assert args["source"] == "lecture"


# ---------------------------------------------------------------------------
# DialogOrchestrator lecture mode — unit tests
# ---------------------------------------------------------------------------


class TestOrchestratorLectureMode:
    """Test DialogOrchestrator lecture mode switching."""

    @pytest.mark.asyncio
    async def test_target_lecture_false_when_config_disabled(self):
        """target_lecture is False when translate_cfg.enabled=False."""
        orch = _make_orch_with_lecture_cfg(translate_enabled=False)
        _make_orch_session_started(orch)
        orch._codec.make_session_changed.return_value = '{}'
        orch._codec.make_status.return_value = '{}'

        msg = _make_change_msg("translate", "zh", "en", "lecture")
        orch._send_status = AsyncMock()

        await orch._handle_session_change(msg)

        assert orch._lecture_active is False
        assert orch._pipeline_mode == "translate"

    @pytest.mark.asyncio
    async def test_target_lecture_true_when_cfg_enabled(self):
        """target_lecture is True → _enter_lecture_mode is called."""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._codec.make_session_changed.return_value = '{}'
        orch._codec.make_status.return_value = '{}'

        entered = False
        async def _fake_enter(my_lang, loc_lang):
            nonlocal entered
            entered = True
            orch._lecture_engine = MagicMock()
            orch._lecture_active = True
            orch._pipeline_mode = "translate"
        orch._enter_lecture_mode = _fake_enter

        orch._send_status = AsyncMock()

        msg = _make_change_msg("translate", "zh", "en", "lecture")
        await orch._handle_session_change(msg)

        assert entered is True
        assert orch._lecture_active is True

    @pytest.mark.asyncio
    async def test_enter_lecture_closes_streams_concurrently(self):
        """入口关闭 ASR/TTS 必须并发：两条 close 以 rendezvous 互等（各自开始后才
        允许完成）。串行实现里第一条等不到第二条 → 1s 超时记名，断言暴露。
        日志实证串行 = ASR 10s + TTS 5s 卡死派发循环 15s
        （docs/translate_lecture_improvements.md §2）。"""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)

        started = {"asr": False, "tts": False}
        both_started = asyncio.Event()
        not_overlapped: list[str] = []

        def _make_stream(name: str):
            class _S:
                async def close(self) -> None:
                    started[name] = True
                    if started["asr"] and started["tts"]:
                        both_started.set()
                    try:
                        await asyncio.wait_for(both_started.wait(), timeout=1.0)
                    except TimeoutError:
                        not_overlapped.append(name)

            return _S()

        orch._asr_stream = _make_stream("asr")
        orch._tts_stream = _make_stream("tts")

        mock_engine = MagicMock()
        mock_engine.open = AsyncMock()
        with patch("session.lecture_translate.LectureTranslateEngine", return_value=mock_engine):
            await asyncio.wait_for(orch._enter_lecture_mode("zh", "en"), timeout=5.0)

        assert not_overlapped == []
        assert orch._asr_stream is None
        assert orch._tts_stream is None

    @pytest.mark.asyncio
    async def test_lecture_does_not_write_history_or_chat_store(self):
        """lecture 事件泵不新增 WorkingMemory / chat_store 行；recording 仍写 lecture。"""
        from omni.base import OmniEvent, OmniEventType

        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._history.add_message("user", "before lecture")
        before_history = list(orch._history.get_recent())
        before_store = list(orch._chat_store.messages)

        sink = MagicMock()
        sink.on_chat_text = MagicMock()
        orch._recording_sink = sink

        events = [
            OmniEvent(type=OmniEventType.INPUT_TRANSCRIPTION_DONE, text="演讲原文"),
            OmniEvent(type=OmniEventType.RESPONSE_STARTED),
            OmniEvent(type=OmniEventType.TEXT_DELTA, text="lecture translation"),
            OmniEvent(type=OmniEventType.RESPONSE_DONE),
        ]
        provider = MagicMock()
        provider.open = AsyncMock(return_value=_make_fake_stream(events))
        with patch(
            "omni.providers.alibaba_livetranslate.LiveTranslateProvider",
            return_value=provider,
        ):
            await orch._enter_lecture_mode("zh", "en")
            for _ in range(50):
                if sink.on_chat_text.call_count >= 2:
                    break
                await asyncio.sleep(0.01)

        assert orch._history.get_recent() == before_history
        assert orch._chat_store.messages == before_store
        assert [c.kwargs["source"] for c in sink.on_chat_text.call_args_list] == [
            "lecture", "lecture",
        ]
        await orch._close_lecture_engine()

    @pytest.mark.asyncio
    async def test_reenter_lecture_closes_old_engine(self):
        """lecture→lecture（切目标语言会重发 session.change(translate, lecture)）必须先关
        旧引擎：否则旧 WS/事件泵被赋值覆盖永久泄漏（泵任务持有 engine 引用，回收不掉）。"""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._lecture_active = True

        old_engine = MagicMock()
        old_engine.close = AsyncMock()
        orch._lecture_engine = old_engine

        mock_provider = MagicMock()
        mock_provider.open = AsyncMock(return_value=_make_fake_stream([]))
        with patch(
            "omni.providers.alibaba_livetranslate.LiveTranslateProvider",
            return_value=mock_provider,
        ):
            await orch._enter_lecture_mode("ja", "en")

        old_engine.close.assert_awaited_once()
        assert orch._lecture_engine is not old_engine
        assert orch._lecture_active is True
        await orch._close_lecture_engine()

    @pytest.mark.asyncio
    async def test_chat_mode_exits_lecture(self):
        """chat mode closes engine and sets _lecture_active=False."""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._codec.make_session_changed.return_value = '{}'
        orch._codec.make_status.return_value = '{}'

        async def _fake_close():
            pass
        mock_engine = MagicMock()
        mock_engine.close = _fake_close
        orch._lecture_engine = mock_engine
        orch._lecture_active = True

        orch._send_status = AsyncMock()

        msg = _make_change_msg("chat")
        await orch._handle_session_change(msg)

        assert orch._lecture_active is False
        assert orch._pipeline_mode == "full"

    @pytest.mark.asyncio
    async def test_translate_dialogue_stays_llm(self):
        """session.change(translate, usecase=dialogue) → normal LLM translate."""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._codec.make_session_changed.return_value = '{}'
        orch._codec.make_status.return_value = '{}'

        orch._send_status = AsyncMock()

        msg = _make_change_msg("translate", "ja", "en", "dialogue")
        await orch._handle_session_change(msg)

        assert orch._lecture_active is False
        assert orch._pipeline_mode == "translate"

    @pytest.mark.asyncio
    async def test_interrupt_forwarded_to_engine(self):
        """In lecture mode, interrupt → engine.interrupt() 且 epoch 递增。

        epoch 递增不可省：事件泵的 should_yield(_response_epoch) 守卫靠它作废在途
        response——早退分支漏掉递增时，被打断的 TEXT/AUDIO 会继续下发（§6.4 / D7）。
        """
        from session.interrupt import InterruptController

        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._interrupt = InterruptController(orch._ctx)  # 真实控制器（非 MagicMock）
        orch._lecture_active = True

        mock_engine = MagicMock()
        mock_engine.interrupt = AsyncMock()
        orch._lecture_engine = mock_engine

        epoch_before = orch._ctx.epoch

        msg = MagicMock()
        msg.type = "interrupt"
        msg.payload.reason = "user_tap"

        await orch._handle_interrupt(msg)

        mock_engine.interrupt.assert_called_once_with("user_tap")
        assert orch._ctx.epoch == epoch_before + 1

    @pytest.mark.asyncio
    async def test_old_client_no_usecase_defaults_dialogue(self):
        """Old client without usecase → treated as dialogue."""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._codec.make_session_changed.return_value = '{}'
        orch._codec.make_status.return_value = '{}'

        orch._send_status = AsyncMock()

        msg = MagicMock()
        msg.type = "session.change"
        msg.payload.mode = "translate"
        msg.payload.my_language = "zh"
        msg.payload.local_language = "en"
        del msg.payload.usecase

        await orch._handle_session_change(msg)

        assert orch._lecture_active is False
        assert orch._pipeline_mode == "translate"

    @pytest.mark.asyncio
    async def test_handle_audio_fed_in_speaking_during_lecture(self):
        """In lecture mode, audio is fed continuously regardless of session
        state (30411b5: LiveTranslate manages its own turn boundaries; VAD
        barge-in is suppressed in the event pump)."""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch, state_value="speaking")
        orch._lecture_active = True

        mock_engine = MagicMock()
        mock_engine.feed_pcm = AsyncMock()
        orch._lecture_engine = mock_engine

        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = "ZmFrZQ=="

        await orch._handle_audio(msg)

        mock_engine.feed_pcm.assert_called_once()

    @pytest.mark.asyncio
    async def test_feed_failure_does_not_degrade(self):
        """喂帧失败不降级（docs/android_audio_capture.md §5.2）：引擎不被关闭、模式不回落——
        失败由引擎内部消化并只报错一次（引擎侧 `_mark_failed` 用例已覆盖）。"""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._lecture_active = True
        orch._pipeline_mode = "translate"  # lecture 激活态的真实取值（_enter_lecture_mode 设置）

        mock_engine = MagicMock()
        mock_engine.feed_pcm = AsyncMock()  # 新契约：feed_pcm 不上抛
        orch._lecture_engine = mock_engine

        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = "ZmFrZQ=="

        await orch._handle_audio(msg)

        mock_engine.feed_pcm.assert_awaited_once()
        assert orch._lecture_active is True           # 不关闭
        assert orch._lecture_engine is mock_engine    # 不置空
        orch._handler.enqueue_outbound.assert_not_awaited()

    # -- muted 帧替换（docs/android_audio_capture.md §5.2）----------------

    async def test_resolve_audio_pcm_muted_returns_zero_frame(self):
        """muted 帧 → 20ms 全零 PCM（16k ⇒ 640B），不做 Opus 解码（data 内容被替换）。"""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)

        msg = MagicMock()
        msg.payload.data = "Z2FyYmFnZQ=="  # 任意内容：不参与输出
        msg.payload.vad_state.value = "muted"

        assert orch._resolve_audio_pcm(msg) == b"\x00" * 640

    async def test_resolve_audio_pcm_regular_decodes(self):
        """非 muted 帧 → 原解码路径（测试环境 opus_decoder=None ⇒ base64 透传）。"""
        import base64

        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)

        msg = MagicMock()
        msg.payload.data = base64.b64encode(b"real-pcm").decode()
        msg.payload.vad_state.value = "silence"

        assert orch._resolve_audio_pcm(msg) == b"real-pcm"

    async def test_lecture_branch_feeds_muted_zero_frame(self):
        """lecture 分支：收到 muted 帧 → 以 5×640B 全零喂引擎（5 帧/条；
        5× 压缩投递凑够云端端点阈值，docs/android_audio_capture.md §5.2）。"""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch)
        orch._lecture_active = True

        mock_engine = MagicMock()
        mock_engine.feed_pcm = AsyncMock()
        orch._lecture_engine = mock_engine

        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = "ZmFrZQ=="
        msg.payload.vad_state.value = "muted"

        await orch._handle_audio(msg)

        mock_engine.feed_pcm.assert_awaited_once_with(b"\x00" * 3200)

    async def test_dialogue_branch_treats_muted_as_non_speech(self):
        """dialogue 路径：muted ≠ speech → TurnDetector 收到 False（静音计数照常）。"""
        orch = _make_orch_with_lecture_cfg()
        _make_orch_session_started(orch, state_value="listening")

        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = "ZmFrZQ=="
        msg.payload.vad_state.value = "muted"

        await orch._handle_audio(msg)

        orch._turn_detector.on_frame.assert_called_once_with(False)


# -- helpers ----------------------------------------------------------------


def _make_change_msg(mode: str, my_lang: str = "", local_lang: str = "", usecase: str = ""):
    msg = MagicMock()
    msg.type = "session.change"
    msg.payload.mode = mode
    msg.payload.my_language = my_lang
    msg.payload.local_language = local_lang
    msg.payload.usecase = usecase
    return msg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_fake_stream(events: list):
    """Create a fake OmniStream that yields the given events then stops."""

    class _FakeEvents:
        def __init__(self, evs):
            self._evs = iter(evs)
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(self._evs)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeStream:
        def __init__(self, evs):
            self._evs = evs

        def events(self):
            return _FakeEvents(self._evs)

        async def feed_audio(self, pcm):
            pass

        async def cancel_response(self):
            pass

        async def close(self):
            pass

    return _FakeStream(events)


async def _never_yield():
    """Async generator that never yields — used to keep event pump alive."""
    import asyncio
    try:
        await asyncio.sleep(999999)
    except asyncio.CancelledError:
        pass
