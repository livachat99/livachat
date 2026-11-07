"""Unit tests: HermesAdapter contract parsing and prompt building (no network)."""

from __future__ import annotations

import pytest

from agent_relay.adapters.base import AgentContext, ChatlogLine, ChatlogUpdate
from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.config import HermesAdapterConfig
from agent_relay.contract import parse_contract


def _adapter(**overrides) -> HermesAdapter:
    cfg = HermesAdapterConfig(**overrides)
    return HermesAdapter(cfg)


class TestParseContract:
    """§4.3 contract parsing rules."""

    def test_plain_json(self):
        assert parse_contract('{"action": "noop"}') == {"action": "noop"}

    def test_json_with_leading_text(self):
        text = 'Here you go: {"action": "message", "text": "hi"}'
        assert parse_contract(text) == {"action": "message", "text": "hi"}

    def test_code_fence(self):
        text = '```json\n{"action": "job_request", "summary": "s", "spec": {"task": "t"}}\n```'
        assert parse_contract(text)["action"] == "job_request"

    def test_nested_braces_in_string(self):
        text = '{"action": "message", "text": "see {this}"}'
        assert parse_contract(text) == {"action": "message", "text": "see {this}"}

    def test_brackets_do_not_break_brace_match(self):
        text = '{"action": "job_request", "spec": {"task": "use [a, b] and {c}"}}'
        parsed = parse_contract(text)
        assert parsed["spec"]["task"] == "use [a, b] and {c}"

    def test_no_json_returns_none(self):
        assert parse_contract("just some words") is None

    def test_empty_returns_none(self):
        assert parse_contract("") is None
        assert parse_contract(None) is None

    def test_invalid_json_returns_none(self):
        assert parse_contract('{"action": broken') is None

    def test_non_dict_json_returns_none(self):
        assert parse_contract('["noop"]') is None

    def test_multiple_objects_takes_first(self):
        text = '{"action": "noop"} trailing {"action": "message", "text": "x"}'
        assert parse_contract(text) == {"action": "noop"}

    def test_unescaped_quotes_in_value_repaired(self):
        """值内未转义引号（2026-08-28 事故）→ 修复后解析成功，job 不丢失。"""
        text = '{"action": "job_request", "summary": "好呀~我来帮你查查"中国黄金大环线"的介绍", "spec": {"task": "查询"}}'
        parsed = parse_contract(text)
        assert parsed["action"] == "job_request"
        assert parsed["summary"] == '好呀~我来帮你查查"中国黄金大环线"的介绍'

    def test_escaped_quotes_untouched(self):
        text = '{"action": "message", "text": "say \\"hi\\" now"}'
        assert parse_contract(text) == {"action": "message", "text": 'say "hi" now'}

    def test_unfixable_json_returns_none(self):
        assert parse_contract('{"action": "broken"') is None


class TestBuildNudgePrompt:
    def test_structure(self):
        update = ChatlogUpdate(
            path="/opt/lvdata/d1/r1/chat/history.jsonl",
            chat_session_id="sess-1",
            new_lines=[
                ChatlogLine(role="user", text="hello", ts_ms=1000),
                ChatlogLine(role="assistant", text="hi", ts_ms=1100),
            ],
            truncated=True,
            outstanding_jobs=[{"job_key": "jk", "task_id": "t1", "summary": "working"}],
        )
        prompt = _adapter()._build_nudge_prompt(update, "Asia/Shanghai")
        assert prompt["type"] == "chatlog_update"
        assert prompt["chatlog_path"] == update.path
        assert prompt["chat_session_id"] == "sess-1"
        assert prompt["timezone"] == "Asia/Shanghai"      # 设备本机时区（每轮刷新）
        assert prompt["new_lines"] == [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "hi"},
        ]
        assert prompt["truncated"] is True
        assert prompt["outstanding_jobs"] == [{"job_key": "jk", "task_id": "t1", "summary": "working"}]


class TestMissionTemplate:
    def test_mission_template_forbids_unescaped_ascii_quotes(self):
        """§4.3 契约加固：字符串值内禁止未转义英文双引号（2026-08-28 事故回归）。

        事故：模型在 summary 里输出未转义 ASCII 引号 → 整条 reply JSON 非法
        → _parse_contract 返回 None → job_request 被当作 noop 丢弃。
        """
        from agent_relay.adapters import hermes

        text = hermes._MISSION_TEMPLATE.read_text(encoding="utf-8")
        assert "禁止使用未转义的英文双引号" in text

    @pytest.mark.asyncio
    async def test_render_mission_with_path(self):
        adapter = _adapter()
        adapter.base_dir = "/opt/lvdata"
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="en")
        rendered = await adapter._render_mission(ctx)
        # No Redis configured → path unknown, hint included
        assert "chatlog_path" in rendered or "NUDGE" in rendered
        # §5.7: chat_session_id 动态语义（以 NUDGE 通知为准），不写死会话
        assert "NUDGE 通知" in rendered

    def test_render_job_task(self):
        adapter = _adapter()
        rendered = adapter._render_job_task(
            "查用户本周偏好", "sess-1", "/opt/lvdata/d1/r1/chat/history.jsonl"
        )
        assert "查用户本周偏好" in rendered
        assert "/opt/lvdata/d1/r1/chat/history.jsonl" in rendered
        assert "sess-1" in rendered
        # Output contract must be present
        assert "NOOP" in rendered

    @pytest.mark.asyncio
    async def test_render_mission_with_timezone(self):
        """docs/agent_relay_prompts.md D5：有值 → 注入时区 + MCP 入参指引；空值 → 显式"未上报"。"""
        adapter = _adapter()
        adapter.base_dir = "/opt/lvdata"
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="zh",
                           timezone="Asia/Shanghai")
        rendered = await adapter._render_mission(ctx)
        assert "Asia/Shanghai" in rendered
        assert "timezone" in rendered and "MCP" in rendered      # 指引 agent 传参
        # 空值（未上报）→ 不出现空占位，走显式文案
        blank = await adapter._render_mission(
            AgentContext(session_id="sess-1", device_id="dev-1", language="zh")
        )
        assert "未上报时区" in blank
        assert "$timezone_hint" not in blank and "$timezone_hint" not in rendered

    def test_render_job_task_with_timezone(self):
        """后台 job 也要知道设备时区（查"最近动态"必须的入参）。"""
        adapter = _adapter()
        rendered = adapter._render_job_task(
            "查最近动态", "sess-1", "/tmp/history.jsonl", timezone="Asia/Shanghai")
        assert "Asia/Shanghai" in rendered and "timezone" in rendered
        blank = adapter._render_job_task("查最近动态", "sess-1", "/tmp/history.jsonl")
        assert "未上报时区" in blank

    @pytest.mark.asyncio
    async def test_render_mission_with_agent_name(self):
        """docs/prompts_improvements.md §3.2：有名字 → 点名语义 + LivaChat 交接边界（非触发）；
        空（未配置）→ 整段消失且无占位残留。"""
        adapter = _adapter(nickname="小黑")
        adapter.base_dir = "/opt/lvdata"
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="zh")
        rendered = await adapter._render_mission(ctx)
        assert "# 你的名字" in rendered and "小黑" in rendered
        assert "job_request" in rendered and "message" in rendered
        assert "LivaChat" in rendered
        assert "重复派生" in rendered      # 审查修正：交接只是措辞，不是触发条件
        blank = await _adapter()._render_mission(ctx)
        assert "你的名字" not in blank and "小黑" not in blank
        assert "$agent_name_hint" not in blank and "$agent_name_hint" not in rendered

    def test_render_job_task_with_agent_name(self):
        """后台任务书同带名字（短版一行）；空 → 整行消失。"""
        adapter = _adapter(nickname="小黑")
        rendered = adapter._render_job_task("查最近动态", "sess-1", "/tmp/history.jsonl")
        assert "你的名字" in rendered and "小黑" in rendered
        blank = _adapter()._render_job_task("查最近动态", "sess-1", "/tmp/history.jsonl")
        assert "你的名字" not in blank and "小黑" not in blank
        assert "$agent_name_hint" not in blank and "$agent_name_hint" not in rendered


class TestWebrootHint:
    """docs/webroot_feature.md §3.3：webroot 提示开/关两态 + 占位符不残留。"""

    @pytest.mark.asyncio
    async def test_render_mission_with_webroot(self):
        """mission 版 = 能力声明 + 触发场景（执行细节在 job 任务书）。"""
        adapter = _adapter()
        adapter.base_dir = "/opt/lvdata"
        adapter.webroot_dir = "/opt/lvdata/webroot"
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="zh")
        rendered = await adapter._render_mission(ctx)
        assert "# Web 页面" in rendered
        assert "产出一个 Web 页面" in rendered
        assert "你无需关心部署细节" in rendered
        assert "$webroot_hint" not in rendered

    @pytest.mark.asyncio
    async def test_render_mission_webroot_off(self):
        """未启用（webroot_dir 空）→ 整段消失、无占位残留。"""
        adapter = _adapter()
        adapter.base_dir = "/opt/lvdata"
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="zh")
        rendered = await adapter._render_mission(ctx)
        assert "Web 页面" not in rendered
        assert "livachat_url" not in rendered
        assert "$webroot_hint" not in rendered

    def test_render_job_task_with_webroot(self):
        """job 任务书 = 完整执行规范（目录/相对路径/验收门禁/链接格式）。"""
        adapter = _adapter()
        adapter.webroot_dir = "/opt/lvdata/webroot"
        rendered = adapter._render_job_task("做个页面", "sess-1", "/tmp/history.jsonl")
        assert "# Web 页面" in rendered
        assert "/opt/lvdata/webroot/<uuidv7>/" in rendered
        assert "相对路径" in rendered
        assert "playwright-dev" in rendered          # N6 验收门禁依赖（该 server 配置 --ignore-https-errors）
        assert "--ignore-https-errors" in rendered
        assert "livachat_url://webroot/" in rendered
        assert "$webroot_hint" not in rendered

    def test_render_job_task_webroot_off(self):
        adapter = _adapter()
        rendered = adapter._render_job_task("做个页面", "sess-1", "/tmp/history.jsonl")
        assert "Web 页面" not in rendered
        assert "livachat_url" not in rendered
        assert "$webroot_hint" not in rendered


class TestLanguageHint:
    """docs/agent_multilingual.md：mission/job task 末尾追加语言指令（不翻译模板）。"""

    @pytest.mark.asyncio
    async def test_mission_carries_language_hint(self):
        adapter = _adapter()
        adapter.base_dir = "/opt/lvdata"
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="zh-Hant")
        rendered = await adapter._render_mission(ctx)
        assert '`"zh-Hant"`' in rendered
        assert "JSON 字段名和 action 值保持英文不变" in rendered
        assert "用户消息本身使用的语言不算语言要求" in rendered

    def test_job_task_carries_language_hint(self):
        rendered = _adapter()._render_job_task(
            "查最近动态", "sess-1", "/tmp/history.jsonl", language="ja")
        assert '`"ja"`' in rendered
        assert "用户消息本身的语言不算要求" in rendered

    def test_job_task_empty_language_defaults_to_en(self):
        rendered = _adapter()._render_job_task("查最近动态", "sess-1", "/tmp/history.jsonl")
        assert '`"en"`' in rendered


class TestMissionRestateOnLanguageChange:
    """复用 Hermes 会话时 App 语言变化 → mission 重申（D7/D10）。"""

    @staticmethod
    def _conn(language: str | None):
        from agent_relay.adapters import hermes

        conn = hermes._DeviceConnection(device_id="dev-1")
        conn.hermes_session_id = "h-1"
        if language is not None:
            conn.mission_ctx = AgentContext(
                session_id="sess-1", device_id="dev-1", language=language,
            )
        return conn

    @pytest.mark.asyncio
    async def test_language_change_restates_mission(self, monkeypatch):
        adapter = _adapter()
        conn = self._conn("zh-Hans")
        calls: list[str] = []

        async def fake_restate(c, context):
            calls.append(context.language)
            c.mission_ctx = context
            return True

        monkeypatch.setattr(adapter, "_restate_mission", fake_restate)
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="en")
        assert await adapter.ensure_session(conn, ctx) is True
        assert calls == ["en"]

    @pytest.mark.asyncio
    async def test_same_language_reuses_without_restate(self, monkeypatch):
        adapter = _adapter()
        conn = self._conn("zh-Hant")
        calls: list[str] = []

        async def fake_restate(c, context):
            calls.append(context.language)
            return True

        monkeypatch.setattr(adapter, "_restate_mission", fake_restate)
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="zh-Hant")
        assert await adapter.ensure_session(conn, ctx) is True
        assert calls == []

    @pytest.mark.asyncio
    async def test_unknown_previous_language_reuses_without_restate(self, monkeypatch):
        adapter = _adapter()
        conn = self._conn(None)
        calls: list[str] = []

        async def fake_restate(c, context):
            calls.append(context.language)
            return True

        monkeypatch.setattr(adapter, "_restate_mission", fake_restate)
        ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="en")
        assert await adapter.ensure_session(conn, ctx) is True
        assert calls == []


class TestModeFlags:
    def test_mode_proactive(self):
        adapter = _adapter(mode="proactive")
        assert adapter.proactive_enabled is True
        assert adapter.reactive_enabled is False

    def test_mode_reactive(self):
        adapter = _adapter(mode="reactive")
        assert adapter.proactive_enabled is False
        assert adapter.reactive_enabled is True

    def test_mode_both(self):
        adapter = _adapter(mode="both")
        assert adapter.proactive_enabled is True
        assert adapter.reactive_enabled is True

    def test_default_is_proactive(self):
        assert _adapter().proactive_enabled is True
