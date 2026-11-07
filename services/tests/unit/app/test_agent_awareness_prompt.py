"""Unit tests: dialog/omni system prompt 的后台 Agent 认知声明（docs/agent_relay_prompts.md §8）。"""

from __future__ import annotations

import logging
import string

from app.application import _named_background_agents, _with_agent_awareness
from session.agent_job_ingest import (
    AGENT_AWARENESS_DECLARATION_TEMPLATE,
    AGENT_AWARENESS_GENERIC_TEMPLATE,
    AGENT_AWARENESS_MULTI_TEMPLATE,
    NamedAgent,
)


def _hermes(nickname: str) -> NamedAgent:
    return NamedAgent(nickname=nickname, adapter_names=("hermes",))


def _declaration(nickname: str) -> str:
    return string.Template(AGENT_AWARENESS_DECLARATION_TEMPLATE).safe_substitute(
        nickname=nickname
    )


class TestWithAgentAwareness:
    def test_appends_hermes_declaration_with_nickname(self):
        out = _with_agent_awareness("You are LivaChat.", [_hermes("小黑")])
        assert out == "You are LivaChat.\n\n" + _declaration("小黑")
        assert "小黑" in out

    def test_strips_trailing_whitespace_before_appending(self):
        out = _with_agent_awareness("You are LivaChat.  \n", [_hermes("小黑")])
        assert out == "You are LivaChat.\n\n" + _declaration("小黑")

    def test_no_agents_unchanged(self):
        assert _with_agent_awareness("You are LivaChat.", []) == "You are LivaChat."

    def test_empty_prompt_unchanged(self):
        assert _with_agent_awareness("", [_hermes("小黑")]) == ""

    def test_placeholder_fully_substituted(self):
        rendered = _declaration("小黑")
        assert "$nickname" not in rendered
        assert "用户点名「小黑」" in rendered

    def test_hermes_declaration_lists_capabilities(self):
        """能力清单：本地历史检索 + 联网查询实时信息（2026-09-30 部署方确认）。"""
        rendered = _declaration("小黑")
        assert "历史对话与长期记忆" in rendered
        assert "联网查询实时信息" in rendered

    def test_openclaw_single_uses_generic_template(self):
        out = _with_agent_awareness(
            "You are LivaChat.",
            [NamedAgent(nickname="小龙虾", adapter_names=("openclaw",))],
        )
        expected = string.Template(AGENT_AWARENESS_GENERIC_TEMPLATE).safe_substitute(
            nickname="小龙虾"
        )
        assert out == "You are LivaChat.\n\n" + expected
        assert "联网查询实时信息" in out

    def test_duplicate_nickname_multiple_backends_uses_generic(self):
        out = _with_agent_awareness(
            "You are LivaChat.",
            [NamedAgent(nickname="小黑", adapter_names=("hermes", "openclaw"))],
        )
        assert "联网查询实时信息" in out
        assert "「小黑」" in out

    def test_multi_agent_uses_multi_template(self):
        agents = [
            _hermes("小黑"),
            NamedAgent(nickname="小龙虾", adapter_names=("openclaw",)),
        ]
        out = _with_agent_awareness("You are LivaChat.", agents)
        expected = string.Template(AGENT_AWARENESS_MULTI_TEMPLATE).safe_substitute(
            nickname_list="「小黑」、「小龙虾」",
            first_nickname="小黑",
        )
        assert out == "You are LivaChat.\n\n" + expected
        assert "联网查询实时信息" in out
        assert "「小黑」、「小龙虾」" in out


class _FakeAdapter:
    def __init__(self, nickname: str, proactive_enabled: bool = True):
        self.nickname = nickname
        self.proactive_enabled = proactive_enabled


class TestNamedBackgroundAgents:
    def test_filters_reactive_only(self):
        agents = _named_background_agents({
            "hermes": _FakeAdapter("小黑", proactive_enabled=False),
        })
        assert agents == []

    def test_omits_empty_nickname_with_warning(self, caplog):
        caplog.set_level(logging.WARNING, logger="livachat.app")
        agents = _named_background_agents({
            "openclaw": _FakeAdapter("   "),
        })
        assert agents == []
        assert "empty nickname" in caplog.text

    def test_dedupes_duplicate_nickname_and_warns(self, caplog):
        caplog.set_level(logging.WARNING, logger="livachat.app")
        agents = _named_background_agents({
            "hermes": _FakeAdapter("小黑"),
            "openclaw": _FakeAdapter("小黑"),
        })
        assert agents == [NamedAgent("小黑", ("hermes", "openclaw"))]
        assert "duplicate agent nickname" in caplog.text

    def test_preserves_first_seen_order(self):
        agents = _named_background_agents({
            "hermes": _FakeAdapter("小黑"),
            "openclaw": _FakeAdapter("小龙虾"),
        })
        assert [a.nickname for a in agents] == ["小黑", "小龙虾"]
