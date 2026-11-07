"""chat mode 输出语言规则注入（docs/server_multilingual.md §4.12）。"""

from __future__ import annotations

import pytest

from session.context import SessionContext
from session.history import WorkingMemory
from session.interrupt import InterruptController

pytestmark = pytest.mark.integration


def _prepare(orch, language: str, *, pipeline_mode: str = "full") -> SessionContext:
    ctx = SessionContext(
        session_id="sess-lang",
        device_id="device-1",
        user_id="u",
        language=language,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory()
    orch._history.add_message("user", "你好")
    orch._interrupt = InterruptController(ctx)
    orch._pipeline_mode = pipeline_mode
    return ctx


class TestChatLanguageRule:
    async def test_ordinary_chat_injects_rule(self, fake_orchestrator):
        _prepare(fake_orchestrator, "ja")
        await fake_orchestrator._start_thinking("你好")

        system = fake_orchestrator._llm.calls[-1]["messages"][0]
        assert system.role == "system"
        assert "You are helpful." in system.content
        assert "Reply language rules" in system.content
        assert "Japanese (ja)" in system.content

    async def test_proactive_note_skips_rule(self, fake_orchestrator):
        _prepare(fake_orchestrator, "ja")
        note = fake_orchestrator._build_av_event_note("normal", "ja")
        await fake_orchestrator._start_thinking("", event_note=note)

        system = fake_orchestrator._llm.calls[-1]["messages"][0]
        assert note in system.content
        assert "请使用 Japanese (ja) 回复。" in system.content
        assert "Reply language rules" not in system.content

    async def test_translate_mode_skips_rule(self, fake_orchestrator):
        _prepare(fake_orchestrator, "ja", pipeline_mode="translate")
        fake_orchestrator._translate_prompt = "You are a translator."
        fake_orchestrator._my_language = "zh"
        fake_orchestrator._local_language = "en"
        await fake_orchestrator._start_thinking("hello")

        system = fake_orchestrator._llm.calls[-1]["messages"][0]
        assert "You are a translator." in system.content
        assert "Reply language rules" not in system.content
