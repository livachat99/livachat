"""Unit tests for translate mode in DialogOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from session.orchestrator import DialogOrchestrator
from tests.unit.session.conftest import FakeChatStore


def _make_orch(*, translate_prompt: str = "You are a translator.",
               pipeline_mode: str = "translate",
               my_language: str | None = None,
               local_language: str | None = None) -> DialogOrchestrator:
    """Create a minimal DialogOrchestrator for unit testing."""
    orch = DialogOrchestrator(
        handler=MagicMock(),
        codec=MagicMock(),
        session_mgr=None,
        asr_service=None,
        llm_service=None,
        tts_service=None,
        opus_encoder=None,
        opus_decoder=None,
        system_prompt="You are a companion.",
        translate_prompt=translate_prompt,
        generation_opts={},
        chat_store=FakeChatStore(),
        pipeline_mode=pipeline_mode,
    )
    orch._my_language = my_language
    orch._local_language = local_language
    return orch


# ---------------------------------------------------------------------------
# _build_translate_instruction
# ---------------------------------------------------------------------------


class TestBuildTranslateInstruction:
    def test_defaults_when_no_languages_set(self):
        """Default: my_language=None → 'zh', local_language=None → 'en'."""
        orch = _make_orch()  # _my_language=None, _local_language=None
        instr = orch._build_translate_instruction()
        assert "zh" in instr
        assert "en" in instr
        assert "translate to English" in instr  # fallback when same

    def test_zh_in_japan_translates_back_and_forth(self):
        """Chinese tourist in Japan: Japanese→zh, Chinese→ja."""
        orch = _make_orch(my_language="zh", local_language="ja")
        instr = orch._build_translate_instruction()
        assert 'NOT "zh", translate the text to "zh"' in instr
        assert 'IS "zh" and "zh" differs from "ja", translate the text to "ja"' in instr

    def test_en_in_china_translates_back_and_forth(self):
        """American in China: Chinese→en, English→zh."""
        orch = _make_orch(my_language="en", local_language="zh")
        instr = orch._build_translate_instruction()
        assert 'NOT "en", translate the text to "en"' in instr
        assert 'IS "en" and "en" differs from "zh", translate the text to "zh"' in instr

    def test_same_language_falls_back_to_english(self):
        """Both languages same → translate to English."""
        orch = _make_orch(my_language="zh", local_language="zh")
        instr = orch._build_translate_instruction()
        assert "translate to English" in instr

    def test_uses_provided_languages_not_defaults(self):
        orch = _make_orch(my_language="ko", local_language="fr")
        instr = orch._build_translate_instruction()
        assert "ko" in instr
        assert "fr" in instr
        assert 'NOT "ko", translate the text to "ko"' in instr
        assert 'IS "ko" and "ko" differs from "fr", translate the text to "fr"' in instr

    def test_italian_codes_are_quoted(self):
        """`it` is both an Italian code and an English pronoun; quote it."""
        orch = _make_orch(my_language="en", local_language="it")
        instr = orch._build_translate_instruction()
        assert 'NOT "en", translate the text to "en"' in instr
        assert 'and "en" differs from "it", translate the text to "it"' in instr

        orch = _make_orch(my_language="it", local_language="en")
        instr = orch._build_translate_instruction()
        assert 'NOT "it", translate the text to "it"' in instr


# ---------------------------------------------------------------------------
# _start_thinking - translate mode message construction
# ---------------------------------------------------------------------------


class TestTranslateMessageConstruction:
    @pytest.mark.asyncio
    async def test_translate_mode_dynamic_instruction(self):
        """Translate mode uses _build_translate_instruction with dynamic languages."""
        orch = _make_orch(pipeline_mode="translate", my_language="zh", local_language="ja")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = "test-llm"
        orch._ctx.tts_provider = ""
        orch._last_detected_language = None

        llm_messages = []

        class _FakeLLM:
            async def stream(self, *, messages, images=None, **opts):
                llm_messages.extend(messages)
                async def _gen():
                    if False:
                        yield
                return _gen()

        orch._llm = _FakeLLM()
        orch._interrupt = MagicMock()
        orch._interrupt.should_yield.return_value = True

        with patch.object(orch, "_send_status", new_callable=AsyncMock), \
             patch.object(orch, "_send_ai_text", new_callable=AsyncMock), \
             patch("session.orchestrator.generate_message_id", return_value="msg-t1"):
            await orch._start_thinking("你好世界")

        assert len(llm_messages) == 2
        assert llm_messages[0].role == "system"
        assert "You are a translator." in llm_messages[0].content
        assert 'NOT "zh", translate the text to "zh"' in llm_messages[0].content
        assert 'IS "zh" and "zh" differs from "ja", translate the text to "ja"' in llm_messages[0].content
        assert "Detected language:" not in llm_messages[0].content
        assert llm_messages[1].role == "user"
        assert llm_messages[1].content == "你好世界"

    @pytest.mark.asyncio
    async def test_translate_mode_default_instruction(self):
        """When no language prefs, defaults my=zh, loc=en."""
        orch = _make_orch(pipeline_mode="translate")  # no my_language / local_language
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = "test-llm"
        orch._ctx.tts_provider = ""
        orch._last_detected_language = None

        llm_messages = []

        class _FakeLLM:
            async def stream(self, *, messages, images=None, **opts):
                llm_messages.extend(messages)
                async def _gen():
                    if False:
                        yield
                return _gen()

        orch._llm = _FakeLLM()
        orch._interrupt = MagicMock()
        orch._interrupt.should_yield.return_value = True

        with patch.object(orch, "_send_status", new_callable=AsyncMock), \
             patch.object(orch, "_send_ai_text", new_callable=AsyncMock), \
             patch("session.orchestrator.generate_message_id", return_value="msg-t1b"):
            await orch._start_thinking("Hello")

        assert 'NOT "zh", translate the text to "zh"' in llm_messages[0].content
        assert 'IS "zh" and "zh" differs from "en", translate the text to "en"' in llm_messages[0].content

    @pytest.mark.asyncio
    async def test_translate_mode_with_language_hint(self):
        """When ASR detected language, append hint to prompt."""
        orch = _make_orch(pipeline_mode="translate", my_language="zh", local_language="en")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = "test-llm"
        orch._ctx.tts_provider = ""
        orch._last_detected_language = "zh-CN"

        llm_messages = []

        class _FakeLLM:
            async def stream(self, *, messages, images=None, **opts):
                llm_messages.extend(messages)
                async def _gen():
                    if False:
                        yield
                return _gen()

        orch._llm = _FakeLLM()
        orch._interrupt = MagicMock()
        orch._interrupt.should_yield.return_value = True

        with patch.object(orch, "_send_status", new_callable=AsyncMock), \
             patch.object(orch, "_send_ai_text", new_callable=AsyncMock), \
             patch("session.orchestrator.generate_message_id", return_value="msg-t2"):
            await orch._start_thinking("你好世界")

        assert "Detected language: zh-CN" in llm_messages[0].content

    @pytest.mark.asyncio
    async def test_translate_mode_no_hint_when_no_language(self):
        """When _last_detected_language is None, no hint should appear."""
        orch = _make_orch(pipeline_mode="translate", my_language="en", local_language="zh")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = "test-llm"
        orch._ctx.tts_provider = ""
        orch._last_detected_language = None

        llm_messages = []

        class _FakeLLM:
            async def stream(self, *, messages, images=None, **opts):
                llm_messages.extend(messages)
                async def _gen():
                    if False:
                        yield
                return _gen()

        orch._llm = _FakeLLM()
        orch._interrupt = MagicMock()
        orch._interrupt.should_yield.return_value = True

        with patch.object(orch, "_send_status", new_callable=AsyncMock), \
             patch.object(orch, "_send_ai_text", new_callable=AsyncMock), \
             patch("session.orchestrator.generate_message_id", return_value="msg-t3"):
            await orch._start_thinking("Hello")

        assert "Detected language:" not in llm_messages[0].content
        assert 'NOT "en", translate the text to "en"' in llm_messages[0].content

    @pytest.mark.asyncio
    async def test_translate_mode_clears_language_after_use(self):
        """_last_detected_language should be cleared after building messages."""
        orch = _make_orch(pipeline_mode="translate")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = "test-llm"
        orch._ctx.tts_provider = ""
        orch._last_detected_language = "zh-CN"

        class _FakeLLM:
            async def stream(self, *, messages, images=None, **opts):
                async def _gen():
                    if False:
                        yield
                return _gen()

        orch._llm = _FakeLLM()
        orch._interrupt = MagicMock()
        orch._interrupt.should_yield.return_value = True

        assert orch._last_detected_language == "zh-CN"

        with patch.object(orch, "_send_status", new_callable=AsyncMock), \
             patch.object(orch, "_send_ai_text", new_callable=AsyncMock), \
             patch("session.orchestrator.generate_message_id", return_value="msg-t4"):
            await orch._start_thinking("你好")

        assert orch._last_detected_language is None

    @pytest.mark.asyncio
    async def test_translate_mode_skips_history_in_messages(self):
        """Translate mode should NOT include conversation history."""
        orch = _make_orch(pipeline_mode="translate")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = "test-llm"
        orch._ctx.tts_provider = ""
        orch._last_detected_language = "en"

        orch._history = MagicMock()
        orch._history.get_recent.return_value = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
        ]

        llm_messages = []

        class _FakeLLM:
            async def stream(self, *, messages, images=None, **opts):
                llm_messages.extend(messages)
                async def _gen():
                    if False:
                        yield
                return _gen()

        orch._llm = _FakeLLM()
        orch._interrupt = MagicMock()
        orch._interrupt.should_yield.return_value = True

        with patch.object(orch, "_send_status", new_callable=AsyncMock), \
             patch.object(orch, "_send_ai_text", new_callable=AsyncMock), \
             patch("session.orchestrator.generate_message_id", return_value="msg-t5"):
            await orch._start_thinking("new text")

        assert len(llm_messages) == 2
        orch._history.get_recent.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_mode_unaffected(self):
        """Chat mode should still use history and system_prompt."""
        orch = _make_orch(pipeline_mode="full")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = "test-llm"
        orch._ctx.tts_provider = ""
        orch._ctx.language = "en"

        orch._history = MagicMock()
        orch._history.get_recent.return_value = [
            {"role": "user", "content": "hello"},
        ]

        llm_messages = []

        class _FakeLLM:
            async def stream(self, *, messages, images=None, **opts):
                llm_messages.extend(messages)
                async def _gen():
                    if False:
                        yield
                return _gen()

        orch._llm = _FakeLLM()
        orch._interrupt = MagicMock()
        orch._interrupt.should_yield.return_value = True

        with patch.object(orch, "_send_status", new_callable=AsyncMock), \
             patch.object(orch, "_send_ai_text", new_callable=AsyncMock), \
             patch("session.orchestrator.generate_message_id", return_value="msg-c1"):
            await orch._start_thinking("hello")

        assert len(llm_messages) >= 2
        assert llm_messages[0].role == "system"
        assert llm_messages[0].content.startswith("You are a companion.\n\n")
        # chat 普通回合注入输出语言规则（§4.12）
        assert "Reply language rules" in llm_messages[0].content
        assert "English (en)" in llm_messages[0].content
        orch._history.get_recent.assert_called_once()

    @pytest.mark.asyncio
    async def test_translate_mode_builtin_prompt_fallback(self):
        """When translate_prompt is empty, use built-in fallback."""
        orch = _make_orch(pipeline_mode="translate", translate_prompt="", my_language="zh", local_language="en")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = "test-llm"
        orch._ctx.tts_provider = ""
        orch._last_detected_language = None

        llm_messages = []

        class _FakeLLM:
            async def stream(self, *, messages, images=None, **opts):
                llm_messages.extend(messages)
                async def _gen():
                    if False:
                        yield
                return _gen()

        orch._llm = _FakeLLM()
        orch._interrupt = MagicMock()
        orch._interrupt.should_yield.return_value = True

        with patch.object(orch, "_send_status", new_callable=AsyncMock), \
             patch.object(orch, "_send_ai_text", new_callable=AsyncMock), \
             patch("session.orchestrator.generate_message_id", return_value="msg-t6"):
            await orch._start_thinking("Hello")

        assert len(llm_messages) == 2
        assert "real-time voice translator" in llm_messages[0].content
        assert 'NOT "zh", translate the text to "zh"' in llm_messages[0].content


# ---------------------------------------------------------------------------
# Phase 2 — session.change handling
# ---------------------------------------------------------------------------


def _handler_mock():
    """Create a handler mock with AsyncMock for enqueue_outbound."""
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._inbound = MagicMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _make_orch_with_handler(
    *, translate_prompt: str = "You are a translator.",
    pipeline_mode: str = "translate",
    my_language: str | None = None,
    local_language: str | None = None,
) -> DialogOrchestrator:
    """Create a DialogOrchestrator with a properly mocked handler."""
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
        translate_prompt=translate_prompt,
        generation_opts={},
        chat_store=FakeChatStore(),
        pipeline_mode=pipeline_mode,
    )
    orch._my_language = my_language
    orch._local_language = local_language
    return orch


# ---------------------------------------------------------------------------
# Phase 2 — session.change handling
# ---------------------------------------------------------------------------


class TestSessionChangeDispatch:
    """Test that _dispatch routes session.change correctly."""

    @pytest.mark.asyncio
    async def test_dispatch_routes_session_change(self):
        orch = _make_orch_with_handler()
        orch._handle_session_change = AsyncMock()

        msg = MagicMock()
        msg.type = "session.change"

        await orch._dispatch(msg)
        orch._handle_session_change.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_handle_session_change_to_translate(self):
        orch = _make_orch_with_handler(pipeline_mode="full")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.state.value = "listening"
        orch._codec = MagicMock()
        orch._codec.make_session_changed.return_value = '{"type":"session.changed"}'
        orch._codec.make_status.return_value = '{"type":"status"}'

        msg = MagicMock()
        msg.type = "session.change"
        msg.payload.mode = "translate"
        msg.payload.my_language = "zh"
        msg.payload.local_language = "ja"

        with patch.object(orch, "_send_status", new_callable=AsyncMock):
                await orch._handle_session_change(msg)

        assert orch._pipeline_mode == "translate"
        assert orch._my_language == "zh"
        assert orch._local_language == "ja"
        orch._codec.make_session_changed.assert_called_once_with(
            mode="translate", my_language="zh", local_language="ja", usecase=None,
        )

    @pytest.mark.asyncio
    async def test_handle_session_change_to_chat(self):
        orch = _make_orch_with_handler(pipeline_mode="translate", my_language="zh", local_language="ja")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.state.value = "listening"
        orch._codec = MagicMock()
        orch._codec.make_session_changed.return_value = '{"type":"session.changed"}'
        orch._codec.make_status.return_value = '{"type":"status"}'

        msg = MagicMock()
        msg.type = "session.change"
        msg.payload.mode = "chat"

        with patch.object(orch, "_send_status", new_callable=AsyncMock):
                await orch._handle_session_change(msg)

        assert orch._pipeline_mode == "full"
        assert orch._my_language is None
        assert orch._local_language is None
        orch._codec.make_session_changed.assert_called_once_with(
            mode="chat", my_language=None, local_language=None, usecase=None,
        )

    @pytest.mark.asyncio
    async def test_handle_session_change_no_ctx_returns_error(self):
        orch = _make_orch_with_handler()
        orch._ctx = None
        orch._codec = MagicMock()
        orch._codec.make_error.return_value = '{"type":"error"}'

        msg = MagicMock()
        msg.type = "session.change"
        msg.payload.mode = "translate"

        await orch._handle_session_change(msg)

        orch._codec.make_error.assert_called_once_with(
            code=1009, message="session not started", fatal=False
        )

    @pytest.mark.asyncio
    async def test_handle_session_change_invalid_mode_returns_error(self):
        orch = _make_orch_with_handler()
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._codec = MagicMock()
        orch._codec.make_error.return_value = '{"type":"error"}'

        msg = MagicMock()
        msg.type = "session.change"
        msg.payload.mode = "invalid_mode"

        await orch._handle_session_change(msg)

        orch._codec.make_error.assert_called_once_with(
            code=1009, message="invalid mode: invalid_mode", fatal=False
        )

    @pytest.mark.asyncio
    async def test_handle_session_change_translate_missing_languages_defaults(self):
        """When my_language/local_language are missing, defaults are used."""
        orch = _make_orch_with_handler(pipeline_mode="full")
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.state.value = "listening"
        orch._codec = MagicMock()
        orch._codec.make_session_changed.return_value = '{"type":"session.changed"}'
        orch._codec.make_status.return_value = '{"type":"status"}'

        msg = MagicMock()
        msg.type = "session.change"
        msg.payload.mode = "translate"
        msg.payload.my_language = None
        msg.payload.local_language = None

        with patch.object(orch, "_send_status", new_callable=AsyncMock):
                await orch._handle_session_change(msg)

        assert orch._pipeline_mode == "translate"
        assert orch._my_language == "zh"  # default
        assert orch._local_language == "en"  # default


# ---------------------------------------------------------------------------
# Phase 2 — mode reset on session start
# ---------------------------------------------------------------------------


class TestSessionStartModeReset:
    """Test that session.start resets mode to chat."""

    @pytest.mark.asyncio
    async def test_session_start_resets_to_chat(self):
        orch = _make_orch_with_handler(pipeline_mode="translate", my_language="zh", local_language="ja")
        orch._pipeline_mode = "translate"
        orch._my_language = "zh"
        orch._local_language = "ja"
        orch._session_mgr = MagicMock()
        orch._codec = MagicMock()
        orch._codec.make_session_ready.return_value = '{"type":"session.ready"}'
        orch._codec.make_status.return_value = '{"type":"status"}'

        mock_ctx = MagicMock()
        mock_ctx.session_id = "test-sid"
        mock_ctx.user_id = "user-1"
        mock_ctx.device_id = "dev-1"
        mock_ctx.device_db_id = None
        mock_ctx.restored = False
        mock_ctx.enable_video = False
        mock_ctx.audio_sample_rate = 24000
        mock_ctx.vad_sensitivity = "medium"
        mock_ctx.client_version = "ios-1.0.0"
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
        msg.payload.config.client_version = "ios-1.0.0"
        msg.payload.session_id = None

        with patch.object(orch, "_send_status", new_callable=AsyncMock):
                        await orch._handle_session_start(msg)

        # Mode should be reset to chat
        assert orch._pipeline_mode == "full"
        assert orch._my_language is None
        assert orch._local_language is None


# ---------------------------------------------------------------------------
# Phase 2 — codec make_session_changed
# ---------------------------------------------------------------------------


class TestCodecSessionChanged:
    def test_make_session_changed_translate(self):
        import json

        from gateway.codec import WireCodec
        from gateway.seq import OutboundSeqAllocator

        codec = WireCodec(OutboundSeqAllocator())
        frame = codec.make_session_changed(
            mode="translate", my_language="zh", local_language="ja"
        )
        data = json.loads(frame)

        assert data["type"] == "session.changed"
        assert data["payload"]["mode"] == "translate"
        assert data["payload"]["my_language"] == "zh"
        assert data["payload"]["local_language"] == "ja"

    def test_make_session_changed_chat(self):
        import json

        from gateway.codec import WireCodec
        from gateway.seq import OutboundSeqAllocator

        codec = WireCodec(OutboundSeqAllocator())
        frame = codec.make_session_changed(mode="chat")
        data = json.loads(frame)

        assert data["type"] == "session.changed"
        assert data["payload"]["mode"] == "chat"
        # 可选字段无值时省略；schema 定义为 string，不允许显式 null。
        assert "my_language" not in data["payload"]
        assert "local_language" not in data["payload"]
        assert "usecase" not in data["payload"]
        assert "avdetector" not in data["payload"]

    def test_seq_placeholder_in_session_changed(self):
        from gateway.codec import WireCodec
        from gateway.seq import OutboundSeqAllocator

        codec = WireCodec(OutboundSeqAllocator())
        frame = codec.make_session_changed(
            mode="translate", my_language="zh", local_language="ja"
        )
        assert '"seq":1,' in frame, f"seq placeholder format missing in: {frame}"

    def test_c2s_session_change_parseable(self):
        import json

        from gateway.codec import WireCodec
        from gateway.seq import OutboundSeqAllocator

        codec = WireCodec(OutboundSeqAllocator())
        raw = json.dumps({
            "type": "session.change",
            "seq": 1,
            "timestamp": 1000,
            "payload": {
                "mode": "translate",
                "my_language": "zh",
                "local_language": "ja",
            },
        })
        msg = codec.parse(raw)
        assert msg.type == "session.change"
        assert msg.payload.mode.value == "translate"
        assert msg.payload.my_language == "zh"
        assert msg.payload.local_language == "ja"
