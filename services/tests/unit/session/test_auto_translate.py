"""Unit tests for translate loc-auto mode（local_language="auto"）in DialogOrchestrator.

语义：LLM 逐句回传严格 JSON {source_language, translation}；服务端以 source 判定
说话人——≠ my 即对方发言 → 更新 effective 对方语言（latest-wins）；== my 不动。
译文纯文本上屏/TTS/recording tap。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from session.orchestrator import (
    DialogOrchestrator,
    _normalize_lang,
    _parse_auto_translate_json,
)
from tests.unit.session.conftest import FakeChatStore


def _make_orch(*, pipeline_mode: str = "translate",
               my_language: str = "zh", local_language: str = "auto") -> DialogOrchestrator:
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
        translate_prompt="You are a real-time voice translator.",
        generation_opts={},
        chat_store=FakeChatStore(),
        pipeline_mode=pipeline_mode,
    )
    orch._my_language = my_language
    orch._local_language = local_language
    orch._loc_auto = local_language == "auto"
    orch._effective_local_language = None
    return orch


def _make_ctx(orch: DialogOrchestrator) -> None:
    orch._ctx = MagicMock()
    orch._ctx.session_id = "test-sid"
    orch._ctx.enable_video = False
    orch._ctx.latest_video_frames = []
    orch._ctx.llm_provider = "test-llm"
    orch._ctx.tts_provider = ""
    orch._interrupt = MagicMock()
    orch._interrupt.should_yield.return_value = True


def _as_thinking_env(orch: DialogOrchestrator) -> tuple[AsyncMock, AsyncMock, list]:
    """Patch send side effects；返回 (ai_text_mock, status_mock, llm_seen)。"""
    ai_text = AsyncMock()
    orch._send_ai_text = ai_text
    orch._send_status = AsyncMock()
    orch._tts = None
    return ai_text, orch._send_status, orch._llm.seen


class _FakeLLM:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.seen: list = []

    def stream(self, *, messages, images=None, **opts):
        self.seen.extend(messages)

        async def gen():
            for t in self.tokens:
                yield t

        return gen()


def _install_llm(orch: DialogOrchestrator, raw_json: str | None = None,
                 raw_text: str | None = None) -> None:
    if raw_json is not None:
        tokens = [raw_json[:8], raw_json[8:]]
    else:
        tokens = [raw_text or "plain"]
    orch._llm = _FakeLLM(tokens)


def _text_payloads(ai_text: AsyncMock) -> list[str]:
    """非 final 的 ai_text 负载序列（text 为位置参数）。"""
    return [
        (c.args[0] if c.args else c.kwargs["text"]) for c in ai_text.call_args_list
        if not (c.kwargs.get("is_final") if c.kwargs else c.args[1])
    ]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_normalize_lang(self):
        assert _normalize_lang("zh-CN") == "zh"
        assert _normalize_lang("JA") == "ja"
        assert _normalize_lang("fr") == "fr"
        assert _normalize_lang("en_US") == "en"
        # 任意疑似 ISO（2-3 位小写）可判——设备语言可能不在常用枚举内（id/vi…）
        assert _normalize_lang("id") == "id"
        assert _normalize_lang("vi") == "vi"
        # 垃圾值拒绝
        assert _normalize_lang("") is None
        assert _normalize_lang("zzzz") is None
        assert _normalize_lang("zh2") is None
        assert _normalize_lang("12") is None

    @pytest.mark.asyncio
    async def test_my_language_outside_common_whitelist(self):
        """my=id（非常用枚举）：我说 id → 判我方、effective 不动；对方 en → 更新。"""
        orch = _make_orch(my_language="id")
        _make_ctx(orch)
        _install_llm(
            orch,
            raw_json=json.dumps(
                {"source_language": "id", "translation": "terjemahan saya"}, ensure_ascii=False,
            ),
        )
        _as_thinking_env(orch)
        await orch._start_thinking("saya bicara")
        assert orch._effective_local_language is None

        _install_llm(
            orch,
            raw_json=json.dumps(
                {"source_language": "en", "translation": "hello"}, ensure_ascii=False,
            ),
        )
        _as_thinking_env(orch)
        await orch._start_thinking("hello there")
        assert orch._effective_local_language == "en"

    def test_parse_json_strict(self):
        obj = _parse_auto_translate_json(
            '{"source_language": "ja", "translation": "こんにちは"}'
        )
        assert obj == {"source_language": "ja", "translation": "こんにちは"}

    def test_parse_json_with_fences(self):
        obj = _parse_auto_translate_json(
            '```json\n{"source_language":"en","translation":"hi"}\n```'
        )
        assert obj == {"source_language": "en", "translation": "hi"}

    def test_parse_rejects_invalid(self):
        assert _parse_auto_translate_json("plain text") is None
        assert _parse_auto_translate_json('{"source_language":"ja"}') is None
        assert _parse_auto_translate_json("[]") is None
        assert _parse_auto_translate_json("") is None

    def test_extract_json_object(self):
        from session.orchestrator import _extract_json_object

        assert _extract_json_object('Sure! {"a":1} thanks') == '{"a":1}'
        assert _extract_json_object('{"a":1}') == '{"a":1}'
        assert _extract_json_object('prefix {"a": {"b": 2}} tail') == '{"a": {"b": 2}}'
        # 残缺：有 '{' 无 '}'
        assert _extract_json_object('{"source_language":"ja","translation":"こんにち') is None
        assert _extract_json_object("no braces") is None


# ---------------------------------------------------------------------------
# session.change + 指令
# ---------------------------------------------------------------------------


class TestSessionChangeAuto:
    @pytest.mark.asyncio
    async def test_change_translate_auto_enters_auto_mode(self):
        orch = _make_orch(pipeline_mode="full", local_language=None)
        orch._loc_auto = False
        orch._handler.enqueue_outbound = AsyncMock()
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.state.value = "listening"
        orch._ctx.sm = MagicMock()
        orch._cancel_pipeline = AsyncMock()
        orch._send_status = AsyncMock()

        msg = MagicMock()
        msg.type = "session.change"
        msg.payload.mode = "translate"
        msg.payload.my_language = "zh"
        msg.payload.local_language = "auto"
        msg.payload.usecase = "dialogue"
        await orch._handle_session_change(msg)

        assert orch._loc_auto is True
        assert orch._local_language == "auto"
        assert orch._effective_local_language is None
        orch._codec.make_session_changed.assert_called_once_with(
            mode="translate", my_language="zh", local_language="auto", usecase="dialogue",
        )

    def test_auto_instruction_uses_effective_default_en(self):
        orch = _make_orch()
        instr = orch._build_auto_translate_instruction()
        assert 'translate the text to "en"' in instr
        assert '"source_language"' in instr
        assert "STRICT JSON" in instr

    def test_auto_instruction_uses_latest_effective(self):
        orch = _make_orch()
        orch._effective_local_language = "ja"
        instr = orch._build_auto_translate_instruction()
        assert 'translate the text to "ja"' in instr
        assert 'translate the text to "en"' not in instr

    def test_auto_instruction_quotes_italian(self):
        orch = _make_orch(my_language="it")
        instr = orch._build_auto_translate_instruction()
        assert 'NOT "it"' in instr
        assert 'translate the text to "en"' in instr


# ---------------------------------------------------------------------------
# _start_thinking：流式 JSON → 纯译文 + effective 更新
# ---------------------------------------------------------------------------


class TestAutoTranslateThinking:
    @pytest.mark.asyncio
    async def test_counterpart_utterance_updates_effective_and_emits_plain(self):
        """对方发言（source=ja ≠ my zh）→ effective=ja；上屏仅纯译文。"""
        orch = _make_orch()
        _make_ctx(orch)
        _install_llm(
            orch,
            raw_json=json.dumps(
                {"source_language": "ja", "translation": "こんにちは世界"}, ensure_ascii=False,
            ),
        )
        ai_text, _status, seen = _as_thinking_env(orch)

        await orch._start_thinking("こんにちは")

        assert orch._effective_local_language == "ja"
        payloads = _text_payloads(ai_text)
        assert payloads == ["こんにちは世界"]  # 无裸 JSON
        final = ai_text.call_args_list[-1]
        assert final.kwargs["is_final"] is True
        assert final.kwargs["index"] == 1
        # system 指令为 auto JSON 契约
        assert '"source_language"' in seen[0].content

    @pytest.mark.asyncio
    async def test_my_utterance_keeps_effective(self):
        """我说（source=zh == my）→ effective 不变（None≈en）。"""
        orch = _make_orch()
        _make_ctx(orch)
        _install_llm(
            orch,
            raw_json=json.dumps(
                {"source_language": "zh", "translation": "你好世界"}, ensure_ascii=False,
            ),
        )
        ai_text, _status, _ = _as_thinking_env(orch)

        await orch._start_thinking("你好")

        assert orch._effective_local_language is None
        assert _text_payloads(ai_text) == ["你好世界"]

    @pytest.mark.asyncio
    async def test_latest_wins_across_turns(self):
        """对方语种中途变化 → 以最新 source 覆盖。"""
        orch = _make_orch()
        _make_ctx(orch)

        _install_llm(
            orch,
            raw_json=json.dumps(
                {"source_language": "ja", "translation": "ja text"}, ensure_ascii=False,
            ),
        )
        _as_thinking_env(orch)
        await orch._start_thinking("ja speech")
        assert orch._effective_local_language == "ja"

        _install_llm(
            orch,
            raw_json=json.dumps(
                {"source_language": "ko", "translation": "ko text"}, ensure_ascii=False,
            ),
        )
        _as_thinking_env(orch)
        await orch._start_thinking("ko speech")
        assert orch._effective_local_language == "ko"

    @pytest.mark.asyncio
    async def test_region_suffix_normalized(self):
        """source 带区域后缀（ja-JP）→ 归一化后仍更新。"""
        orch = _make_orch()
        _make_ctx(orch)
        _install_llm(
            orch,
            raw_json=json.dumps(
                {"source_language": "ja-JP", "translation": "ja text"}, ensure_ascii=False,
            ),
        )
        _as_thinking_env(orch)
        await orch._start_thinking("x")
        assert orch._effective_local_language == "ja"

    @pytest.mark.asyncio
    async def test_non_json_reply_forwards_raw_no_update(self):
        """LLM 未守约（纯 prose，无 '{'）→ 原样上屏走 TTS、不更新 effective。"""
        orch = _make_orch()
        _make_ctx(orch)
        _install_llm(orch, raw_text="こんにちは")
        ai_text, _status, _ = _as_thinking_env(orch)

        await orch._start_thinking("こんにちは")

        assert orch._effective_local_language is None
        assert _text_payloads(ai_text) == ["こんにちは"]
        final = ai_text.call_args_list[-1]
        assert final.kwargs["index"] == 1

    @pytest.mark.asyncio
    async def test_prose_prefix_json_recovers_via_extraction(self):
        """LLM 在 JSON 前加 prose → '{'..'}' 截取重试成功，行为与守约一致。"""
        orch = _make_orch()
        _make_ctx(orch)
        _install_llm(
            orch,
            raw_text='Sure! Here it is: '
            + json.dumps(
                {"source_language": "ja", "translation": "こんにちは"}, ensure_ascii=False,
            ),
        )
        ai_text, _status, _ = _as_thinking_env(orch)

        await orch._start_thinking("こんにちは")

        assert orch._effective_local_language == "ja"
        assert _text_payloads(ai_text) == ["こんにちは"]

    @pytest.mark.asyncio
    async def test_partial_json_suppresses_tts_and_tap(self):
        """残缺 JSON（ProviderError 型，有 '{' 无 '}'）→ 上屏原样，但不喂 TTS、
        不进 recording tap（JSON 残片嫌疑）。"""
        from unittest.mock import AsyncMock as _AsyncMock

        orch = _make_orch()
        _make_ctx(orch)
        partial = '{"source_language":"ja","translation":"こんにち'
        _install_llm(orch, raw_text=partial)

        sink = MagicMock()
        sink.on_chat_text = MagicMock()
        orch._recording_sink = sink
        tts_stream = _AsyncMock()
        tts_stream.feed_text = _AsyncMock()
        orch._tts = MagicMock()
        orch._tts_stream = tts_stream
        ai_text, status, _ = _as_thinking_env(orch)
        # _as_thinking_env 把 orch._tts 置 None；此处重新启用 TTS 以断言抑制
        orch._tts = MagicMock()
        orch._tts_stream = tts_stream

        await orch._start_thinking("x")

        assert orch._effective_local_language is None
        assert _text_payloads(ai_text) == [partial]  # 仅上屏
        tts_stream.feed_text.assert_not_awaited()
        sink.on_chat_text.assert_not_called()
        # 无 THINKING→SPEAKING 迁移（will_speak=false）
        assert not any(c.kwargs.get("session_status") == "speaking"
                       for c in status.call_args_list)

    @pytest.mark.asyncio
    async def test_missing_translation_field_forwards_raw(self):
        orch = _make_orch()
        _make_ctx(orch)
        _install_llm(orch, raw_json='{"source_language":"ja"}')
        ai_text, _status, _ = _as_thinking_env(orch)

        await orch._start_thinking("x")

        assert orch._effective_local_language is None
        assert _text_payloads(ai_text) == ['{"source_language":"ja"}']

    @pytest.mark.asyncio
    async def test_fixed_translate_streams_raw_tokens(self):
        """非 auto 固定 loc：既有逐 token 上屏不变（回归）。"""
        orch = _make_orch(local_language="en")
        orch._loc_auto = False
        _make_ctx(orch)
        _install_llm(orch, raw_text="hello")
        ai_text, _status, _ = _as_thinking_env(orch)

        await orch._start_thinking("hello")

        assert orch._effective_local_language is None
        # 逐 token：首段 index0 文本 hello、final index1
        payloads = _text_payloads(ai_text)
        assert payloads == ["hello"]
        assert ai_text.call_args_list[0].kwargs["index"] == 0


# ---------------------------------------------------------------------------
# lecture 直连 auto 兜底归一
# ---------------------------------------------------------------------------


class TestLectureAutoGuard:
    @pytest.mark.asyncio
    async def test_lecture_auto_normalized_to_en(self):
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
        orch = _make_orch(pipeline_mode="full", local_language=None)
        orch._translate_cfg = cfg
        orch._lecture_active = False
        orch._handler.enqueue_outbound = AsyncMock()
        orch._enter_lecture_mode = AsyncMock()  # 不真连 LiveTranslate
        orch._ctx = MagicMock()
        orch._ctx.session_id = "test-sid"
        orch._ctx.state.value = "listening"
        orch._ctx.sm = MagicMock()
        orch._cancel_pipeline = AsyncMock()
        orch._send_status = AsyncMock()

        msg = MagicMock()
        msg.type = "session.change"
        msg.payload.mode = "translate"
        msg.payload.my_language = "zh"
        msg.payload.local_language = "auto"
        msg.payload.usecase = "lecture"
        await orch._handle_session_change(msg)

        # lecture 直连兜底：auto → en 固定源语言，不进 auto 模式
        assert orch._loc_auto is False
        assert orch._local_language == "en"
