"""Unit tests for transcribe mode in DialogOrchestrator.

对照 docs/fd_pipeline_transcribe_mode.md §10 服务端单测用例 1-11。
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from session.orchestrator import DialogOrchestrator
from session.state_machine import SessionState
from tests.unit.session.conftest import FakeChatStore

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _handler_mock():
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._inbound = MagicMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _make_orch_with_ctx(
    *, pipeline_mode: str = "full", chat_store: FakeChatStore | None = None,
) -> DialogOrchestrator:
    """Create a DialogOrchestrator with a real ctx (LISTENING) + WorkingMemory."""
    from session.context import SessionContext
    from session.history import WorkingMemory
    from session.state_machine import SessionStateMachine

    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    ctx = SessionContext(
        session_id="test-sid",
        device_id="dev-1",
        user_id="user-1",
        sm=sm,
        enable_video=False,
        audio_sample_rate=16000,
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
        translate_prompt="",
        generation_opts={},
        chat_store=chat_store if chat_store is not None else FakeChatStore(),
        pipeline_mode=pipeline_mode,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._turn_detector = MagicMock()
    orch._interrupt = MagicMock()
    orch._recording_sink = None
    orch._agent_publisher = None
    return orch


def _change_msg(mode: str, my_lang=None, local_lang=None, usecase=None):
    msg = MagicMock()
    msg.type = "session.change"
    msg.payload.mode = mode
    msg.payload.my_language = my_lang
    msg.payload.local_language = local_lang
    msg.payload.usecase = usecase
    return msg


async def _run_turn(orch: DialogOrchestrator, final_text: str, *, vad: str = "speech") -> None:
    """Drive one full turn through _handle_audio (turn detector reports end on first frame).

    ASR feed is bypassed (_asr=None); _asr_stream is faked for the turn-end read.
    """
    orch._asr_stream = MagicMock()
    orch._asr_stream.final_text = final_text
    orch._asr_stream.reset = AsyncMock()
    orch._turn_detector.on_frame.return_value = True
    orch._asr_final_sent = False

    from unittest.mock import patch

    msg = MagicMock()
    msg.type = "audio"
    msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
    msg.payload.vad_state.value = vad
    with patch("session.orchestrator.generate_transcription_id", return_value="trans_abcd12345678"):
        await orch._handle_audio(msg)
    # 让 fire-and-forget 的 agent relay / thinking task 有机会落地
    await asyncio.sleep(0.05)


def _session_change_sent(orch: DialogOrchestrator) -> bool:
    return orch._codec.make_session_changed.called


def patch_send_status(orch: DialogOrchestrator) -> AsyncMock:
    """Patch _send_status to an AsyncMock and return it for assertions."""
    m = AsyncMock()
    orch._send_status = m
    return m


# ---------------------------------------------------------------------------
# 用例 1 — session.change(mode=transcribe) 回声
# ---------------------------------------------------------------------------


class TestSessionChangeToTranscribe:
    @pytest.mark.asyncio
    async def test_echoes_mode_without_language_usecase(self):
        orch = _make_orch_with_ctx(pipeline_mode="full")
        orch._cancel_pipeline = AsyncMock()
        orch._send_status = AsyncMock()

        msg = _change_msg("transcribe")
        await orch._handle_session_change(msg)

        assert orch._pipeline_mode == "transcribe"
        assert orch._my_language is None
        assert orch._local_language is None
        orch._cancel_pipeline.assert_awaited_once()
        orch._send_status.assert_awaited_once_with("listening", "mode_changed")
        orch._codec.make_session_changed.assert_called_once_with(
            mode="transcribe", my_language=None, local_language=None, usecase=None,
        )

    @pytest.mark.asyncio
    async def test_resets_sm_from_speaking(self):
        """SPEAKING/THINKING 中切入 → SM 复位 LISTENING（D3）。"""
        orch = _make_orch_with_ctx()
        orch._ctx.sm.state = SessionState.SPEAKING
        orch._cancel_pipeline = AsyncMock()
        orch._send_status = AsyncMock()

        await orch._handle_session_change(_change_msg("transcribe"))

        assert orch._ctx.state == SessionState.LISTENING

    @pytest.mark.asyncio
    async def test_sm_reset_rejected_status_carries_current_state(self):
        """SM 复位被 reject（注入：transition_or_reject 不改变状态）→ status
        携带当前 state 而非 listening（§4.6.7 行为记录）。"""
        orch = _make_orch_with_ctx()
        orch._ctx.sm.state = SessionState.SPEAKING
        orch._ctx.sm.transition_or_reject = lambda to: None  # reject: 不改状态
        orch._cancel_pipeline = AsyncMock()
        orch._send_status = AsyncMock()

        await orch._handle_session_change(_change_msg("transcribe"))

        assert orch._ctx.state == SessionState.SPEAKING
        orch._send_status.assert_awaited_once_with("speaking", "mode_changed")


# ---------------------------------------------------------------------------
# 用例 2/3 — transcribe 轮末：仅 asr_final；静音轮静默；不写 WM/chat_store
# ---------------------------------------------------------------------------


class TestTranscribeTurnEnd:
    @pytest.mark.asyncio
    async def test_turn_sends_asr_final_only_no_thinking(self):
        orch = _make_orch_with_ctx(pipeline_mode="transcribe")
        orch._send_status = AsyncMock()
        orch._send_transcription = AsyncMock()

        await _run_turn(orch, "今天会议讨论了新方案")

        # 轮末仅 status(listening, asr_final)——无 THINKING 迁移、无 thinking 任务
        orch._send_status.assert_awaited_once_with("listening", "asr_final")
        orch._send_transcription.assert_awaited_once_with(
            "今天会议讨论了新方案", is_final=True, transcription_id="trans_abcd12345678",
            images=[],  # transcribe 门控排除挂图（fd_pipeline_llm_v2）
        )
        assert orch._ctx.state == SessionState.LISTENING
        assert orch._pipeline_task is None

    @pytest.mark.asyncio
    async def test_silent_turn_sends_no_status(self):
        orch = _make_orch_with_ctx(pipeline_mode="transcribe")
        orch._send_status = AsyncMock()
        orch._send_transcription = AsyncMock()

        await _run_turn(orch, "")  # 静音轮：空 final_text

        orch._send_status.assert_not_awaited()
        orch._send_transcription.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_history_no_chat_store_write(self):
        """transcribe 下 user 文本不写 WorkingMemory / chat_store（D4）。"""
        chat_store = FakeChatStore()
        orch = _make_orch_with_ctx(pipeline_mode="transcribe", chat_store=chat_store)
        orch._send_status = AsyncMock()
        orch._send_transcription = AsyncMock()

        await _run_turn(orch, "现场会议记录内容")

        assert len(orch._history.get_recent()) == 0
        assert chat_store.messages == []


# ---------------------------------------------------------------------------
# 用例 4 — 语音中段切换（D10：轮末按"当时值"收尾）
# ---------------------------------------------------------------------------


class TestMidTurnSwitch:
    @pytest.mark.asyncio
    async def test_chat_to_transcribe_mid_turn_ends_as_transcribe(self):
        """chat 语音中段切到 transcribe → 该 turn 收尾按 transcribe：不写
        history、不启 thinking、仅 asr_final。"""
        chat_store = FakeChatStore()
        orch = _make_orch_with_ctx(pipeline_mode="full", chat_store=chat_store)
        orch._send_status = AsyncMock()
        orch._send_transcription = AsyncMock()
        orch._cancel_pipeline = AsyncMock()

        # 第一帧（轮中，turn detector 未收尾）
        orch._turn_detector.on_frame.return_value = False
        first = MagicMock()
        first.type = "audio"
        first.payload.data = base64.b64encode(b"\x00" * 320).decode()
        first.payload.vad_state.value = "speech"
        await orch._handle_audio(first)

        # 中段切到 transcribe
        await orch._handle_session_change(_change_msg("transcribe"))
        assert orch._pipeline_mode == "transcribe"
        orch._send_status.reset_mock()  # 回声 status(mode_changed) 不计入轮末断言

        # 第二帧（收尾帧）——turn-end 按当时的 transcribe 处理
        await _run_turn(orch, "半截语音内容")

        orch._send_status.assert_awaited_once_with("listening", "asr_final")
        assert len(orch._history.get_recent()) == 0
        assert chat_store.messages == []
        assert orch._pipeline_task is None

    @pytest.mark.asyncio
    async def test_transcribe_to_chat_mid_turn_ends_as_chat(self):
        """transcribe→chat 中段切换 → 该 turn 按 chat 收尾：写入 history
        （§4.6.2 记录的有意行为）并启 thinking。"""
        chat_store = FakeChatStore()
        orch = _make_orch_with_ctx(pipeline_mode="transcribe", chat_store=chat_store)
        orch._send_status = AsyncMock()
        orch._send_transcription = AsyncMock()
        orch._cancel_pipeline = AsyncMock()

        orch._turn_detector.on_frame.return_value = False
        first = MagicMock()
        first.type = "audio"
        first.payload.data = base64.b64encode(b"\x00" * 320).decode()
        first.payload.vad_state.value = "speech"
        await orch._handle_audio(first)

        await orch._handle_session_change(_change_msg("chat"))
        assert orch._pipeline_mode == "full"
        orch._send_status.reset_mock()  # 回声 status(mode_changed) 不计入轮末断言

        await _run_turn(orch, "这段按对话收尾")

        assert [m["content"] for m in orch._history.get_recent()] == ["这段按对话收尾"]
        assert len(chat_store.messages) == 1
        # 收尾按 chat：启 thinking（llm=None → 任务立即返回，但已被调度）
        assert orch._pipeline_task is not None
        assert orch._ctx.state == SessionState.LISTENING


# ---------------------------------------------------------------------------
# 用例 5 — translate(speaking/thinking 中) → transcribe：在途 pipeline 取消
# ---------------------------------------------------------------------------


class TestCancelInFlightOnEnterTranscribe:
    @pytest.mark.asyncio
    async def test_inflight_pipeline_cancelled_no_residual(self):
        orch = _make_orch_with_ctx(pipeline_mode="translate")
        orch._my_language = "zh"
        orch._local_language = "ja"
        orch._send_status = AsyncMock()

        started = asyncio.Event()

        hang = asyncio.Event()

        async def _inflight():
            started.set()
            await hang.wait()  # 一直挂起直到被取消（CancelledError 自然传播）

        orch._pipeline_task = asyncio.create_task(_inflight())
        await started.wait()
        assert not orch._pipeline_task.done()

        await orch._handle_session_change(_change_msg("transcribe"))

        assert orch._pipeline_task is None
        assert orch._ctx.state == SessionState.LISTENING
        orch._send_status.assert_awaited_once_with("listening", "mode_changed")


# ---------------------------------------------------------------------------
# 用例 6 — transcribe → chat / translate
# ---------------------------------------------------------------------------


class TestExitTranscribe:
    @pytest.mark.asyncio
    async def test_back_to_chat_restores_thinking_and_history(self):
        orch = _make_orch_with_ctx(pipeline_mode="transcribe")
        orch._send_status = AsyncMock()
        orch._send_transcription = AsyncMock()

        await orch._handle_session_change(_change_msg("chat"))
        assert orch._pipeline_mode == "full"

        await _run_turn(orch, "恢复对话后的发言")

        # history 写入 gate 恢复 + thinking 恢复调度
        assert len(orch._history.get_recent()) == 1
        assert orch._pipeline_task is not None

    @pytest.mark.asyncio
    async def test_back_to_translate_sets_translate_prefs(self):
        orch = _make_orch_with_ctx(pipeline_mode="transcribe")
        orch._send_status = AsyncMock()

        msg = _change_msg("translate", my_lang="zh", local_lang="ja")
        await orch._handle_session_change(msg)

        assert orch._pipeline_mode == "translate"
        assert orch._my_language == "zh"
        assert orch._local_language == "ja"
        orch._codec.make_session_changed.assert_called_once_with(
            mode="translate", my_language="zh", local_language="ja", usecase=None,
        )


# ---------------------------------------------------------------------------
# 用例 7 — lecture → transcribe：先走 lecture 切出
# ---------------------------------------------------------------------------


class TestLectureToTranscribe:
    @pytest.mark.asyncio
    async def test_exits_lecture_engine_then_enters_transcribe(self):
        orch = _make_orch_with_ctx(pipeline_mode="translate")
        orch._lecture_active = True
        mock_engine = MagicMock()
        mock_engine.close = AsyncMock()
        orch._lecture_engine = mock_engine
        orch._ctx.sm.state = SessionState.SPEAKING
        orch._cancel_pipeline = AsyncMock()
        orch._send_status = AsyncMock()

        await orch._handle_session_change(_change_msg("transcribe"))

        mock_engine.close.assert_awaited_once()
        assert orch._lecture_active is False
        assert orch._lecture_engine is None
        assert orch._pipeline_mode == "transcribe"
        assert orch._ctx.state == SessionState.LISTENING


# ---------------------------------------------------------------------------
# 用例 8 — 边角：transcribe+usecase=lecture 不进 lecture；1009
# ---------------------------------------------------------------------------


class TestTranscribeEdgeCases:
    @pytest.mark.asyncio
    async def test_usecase_lecture_ignored_for_transcribe(self):
        from shared.config.models import TranslateConfig

        cfg = TranslateConfig(
            enabled=True,
            base_url="wss://test.example.com",
            api_key="sk-test",
            model="m",
            voice="V",
            source_language="en",
            target_language="zh",
            input_sample_rate=16000,
            output_sample_rate=24000,
        )
        orch = _make_orch_with_ctx(pipeline_mode="full")
        orch._translate_cfg = cfg
        orch._cancel_pipeline = AsyncMock()
        orch._send_status = AsyncMock()

        msg = _change_msg("transcribe", usecase="lecture")
        await orch._handle_session_change(msg)

        assert orch._pipeline_mode == "transcribe"
        assert orch._lecture_active is False
        assert orch._lecture_engine is None
        orch._codec.make_session_changed.assert_called_once_with(
            mode="transcribe", my_language=None, local_language=None, usecase=None,
        )

    @pytest.mark.asyncio
    async def test_invalid_mode_returns_1009(self):
        orch = _make_orch_with_ctx()
        orch._codec.make_error.return_value = '{"type":"error"}'

        await orch._handle_session_change(_change_msg("invalid_mode"))

        orch._codec.make_error.assert_called_once_with(
            code=1009, message="invalid mode: invalid_mode", fatal=False,
        )
        assert _session_change_sent(orch) is False

    @pytest.mark.asyncio
    async def test_change_without_start_returns_1009(self):
        orch = _make_orch_with_ctx()
        orch._ctx = None
        orch._codec.make_error.return_value = '{"type":"error"}'

        await orch._handle_session_change(_change_msg("transcribe"))

        orch._codec.make_error.assert_called_once_with(
            code=1009, message="session not started", fatal=False,
        )


# ---------------------------------------------------------------------------
# 用例 9 — agent relay：transcribe 不发布；asr_only 保持发布（D7）
# ---------------------------------------------------------------------------


class TestAgentRelayPublish:
    def _make_orch(self, pipeline_mode: str) -> DialogOrchestrator:
        orch = _make_orch_with_ctx(pipeline_mode=pipeline_mode)
        orch._send_status = AsyncMock()
        orch._send_transcription = AsyncMock()
        orch._agent_publisher = MagicMock()
        orch._agent_publisher.publish_utterance = AsyncMock()
        return orch

    @pytest.mark.asyncio
    async def test_transcribe_does_not_publish(self):
        orch = self._make_orch("transcribe")
        await _run_turn(orch, "现场会议内容不该流向 agent")
        orch._agent_publisher.publish_utterance.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_asr_only_still_publishes(self):
        """防误伤回归：asr_only 现状即发布，保持不动。"""
        orch = self._make_orch("asr_only")
        await _run_turn(orch, "asr_only 照常发布")
        orch._agent_publisher.publish_utterance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chat_still_publishes(self):
        orch = self._make_orch("full")
        await _run_turn(orch, "对话发言照常发布")
        orch._agent_publisher.publish_utterance.assert_awaited_once()


# ---------------------------------------------------------------------------
# 用例 10 — recording tap 分流
# ---------------------------------------------------------------------------


class TestRecordingTapRouting:
    def _make_orch(self, pipeline_mode: str) -> DialogOrchestrator:
        orch = _make_orch_with_ctx(pipeline_mode=pipeline_mode)
        orch._send_status = AsyncMock()
        # recording tap 是同步非阻塞调用（sink 吞掉错误），fake 用 MagicMock
        orch._recording_sink = MagicMock()
        orch._recording_sink.on_transcribe_text = MagicMock()
        orch._recording_sink.on_chat_text = MagicMock()
        return orch

    @pytest.mark.asyncio
    async def test_transcribe_routes_to_on_transcribe_text(self):
        """transcribe 下 final → on_transcribe_text，不调 on_chat_text。"""
        orch = self._make_orch("transcribe")
        await _run_turn(orch, "录音分流测试文本")

        orch._recording_sink.on_transcribe_text.assert_called_once()
        args = orch._recording_sink.on_transcribe_text.call_args.kwargs
        assert args["text"] == "录音分流测试文本"
        assert args["chat_session_id"] == "test-sid"
        assert args["ref_id"] == "trans_abcd12345678"
        orch._recording_sink.on_chat_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_routes_to_on_chat_text(self):
        orch = self._make_orch("full")
        await _run_turn(orch, "对话落盘文本")

        orch._recording_sink.on_chat_text.assert_called_once()
        args = orch._recording_sink.on_chat_text.call_args.kwargs
        assert args["role"] == "user"
        assert args["text"] == "对话落盘文本"
        assert args["ref_id"] == "trans_abcd12345678"
        assert args["source"] is None  # chat 不带 source → history.jsonl
        orch._recording_sink.on_transcribe_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_translate_routes_to_on_chat_text_with_translate_source(self):
        """translate 下 final → on_chat_text(source="translate") → 落 translate.jsonl。"""
        orch = self._make_orch("translate")
        orch._my_language = "zh"
        orch._local_language = "en"
        await _run_turn(orch, "需要翻译的原文")

        orch._recording_sink.on_chat_text.assert_called_once()
        args = orch._recording_sink.on_chat_text.call_args.kwargs
        assert args["role"] == "user"
        assert args["text"] == "需要翻译的原文"
        assert args["source"] == "translate"
        orch._recording_sink.on_transcribe_text.assert_not_called()


# ---------------------------------------------------------------------------
# 用例 11 — 新 session.start 重置 full（上个会话为 transcribe 亦然）
# ---------------------------------------------------------------------------


class TestSessionStartResetsMode:
    @pytest.mark.asyncio
    async def test_new_session_resets_transcribe_to_full(self):
        orch = _make_orch_with_ctx(pipeline_mode="transcribe")
        orch._session_mgr = MagicMock()
        orch._codec.make_session_ready.return_value = '{"type":"session.ready"}'

        mock_ctx = MagicMock()
        mock_ctx.session_id = "new-sid"
        mock_ctx.user_id = "user-1"
        mock_ctx.device_id = "dev-1"
        mock_ctx.device_db_id = None
        mock_ctx.restored = False
        mock_ctx.enable_video = False
        mock_ctx.audio_sample_rate = 24000
        mock_ctx.vad_sensitivity = "medium"
        mock_ctx.client_version = "android-1.0.0"
        mock_ctx.asr_provider = ""
        mock_ctx.llm_provider = ""
        mock_ctx.tts_provider = ""
        mock_ctx.resumed_count = 0
        orch._session_mgr.start = AsyncMock(return_value=(mock_ctx, MagicMock()))

        msg = MagicMock()
        msg.type = "session.start"
        msg.payload.config.enable_video = False
        msg.payload.config.audio_sample_rate = 24000
        msg.payload.config.vad_sensitivity = "medium"
        msg.payload.config.client_version = "android-1.0.0"
        msg.payload.session_id = None

        with patch_send_status(orch):
            await orch._handle_session_start(msg)

        assert orch._pipeline_mode == "full"
        assert orch._my_language is None
        assert orch._local_language is None
