"""Unit tests: OpenClawAdapter pure logic (no network).

Covers message-text extraction, delta accumulation/replace + terminal
routing, session key planning, nudge payload layout, and mode flags.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from agent_relay.adapters.base import ChatlogLine, ChatlogUpdate
from agent_relay.adapters.openclaw import (
    OpenClawAdapter,
    _Connection,
    _extract_message_text,
)
from agent_relay.config import OpenClawAdapterConfig
from agent_relay.job_store import (
    STATUS_DONE,
    STATUS_REQUESTED,
    STATUS_RUNNING,
    JobRecord,
)


def _adapter(**overrides) -> OpenClawAdapter:
    return OpenClawAdapter(OpenClawAdapterConfig(**overrides))


def test_nickname_from_config():
    assert _adapter(nickname="小龙虾").nickname == "小龙虾"
    assert _adapter().nickname == ""


class TestExtractMessageText:
    def test_blocks_with_thinking(self):
        message = {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "答案"},
            ],
        }
        assert _extract_message_text(message) == "答案"

    def test_plain_string_content(self):
        assert _extract_message_text({"content": "纯文本"}) == "纯文本"

    def test_message_is_str(self):
        assert _extract_message_text("直接字符串") == "直接字符串"

    def test_missing(self):
        assert _extract_message_text(None) == ""
        assert _extract_message_text({}) == ""
        assert _extract_message_text({"content": [{"type": "text"}]}) == ""

    def test_multiple_text_blocks_joined(self):
        message = {"content": [{"type": "text", "text": "a"},
                               {"type": "text", "text": "b"}]}
        assert _extract_message_text(message) == "ab"


class TestChatEventRouting:
    def test_delta_accumulation_and_replace(self):
        conn = _Connection()
        adapter = _adapter()
        adapter._handle_chat_event(conn, {"runId": "r1", "state": "delta", "deltaText": "你"})
        adapter._handle_chat_event(conn, {"runId": "r1", "state": "delta", "deltaText": "好"})
        assert conn.run_deltas["r1"] == "你好"
        adapter._handle_chat_event(conn, {"runId": "r1", "state": "delta",
                                          "deltaText": "重来", "replace": True})
        assert conn.run_deltas["r1"] == "重来"

    async def test_final_resolves_run_wait_with_message_snapshot(self):
        conn = _Connection()
        adapter = _adapter()
        fut = adapter._register_run_wait(conn, "r1")
        adapter._handle_chat_event(conn, {
            "runId": "r1", "state": "final",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "完成"}]},
        })
        assert fut.done() and fut.result() == "完成"
        assert "r1" not in conn.run_waits

    async def test_final_falls_back_to_deltas(self):
        conn = _Connection()
        adapter = _adapter()
        fut = adapter._register_run_wait(conn, "r1")
        adapter._handle_chat_event(conn, {"runId": "r1", "state": "delta", "deltaText": "增量结果"})
        adapter._handle_chat_event(conn, {"runId": "r1", "state": "final"})
        assert fut.result() == "增量结果"

    async def test_error_and_aborted_resolve_none(self):
        conn = _Connection()
        adapter = _adapter()
        f1 = adapter._register_run_wait(conn, "r1")
        f2 = adapter._register_run_wait(conn, "r2")
        adapter._handle_chat_event(conn, {"runId": "r1", "state": "error"})
        adapter._handle_chat_event(conn, {"runId": "r2", "state": "aborted",
                                          "stopReason": "rpc"})
        assert f1.result() is None and f2.result() is None

    def test_non_dict_frame_skipped(self):
        """非对象帧（列表/字符串/数字）静默跳过，不进 handler（P2-1）。"""
        a = _adapter()
        conn = _Connection()
        a._handle_message(conn, ["not", "a", "dict"])
        a._handle_message(conn, "garbage")
        a._handle_message(conn, 42)
        assert conn.pending == {} and conn.run_waits == {}

    def test_non_dict_payload_coerced(self):
        """payload 非 dict（字符串/None/列表）按空载荷处理，不抛异常（P2-1）。"""
        a = _adapter()
        conn = _Connection()
        a._handle_message(conn, {"type": "event", "event": "chat", "payload": "oops"})
        a._handle_message(conn, {"type": "event", "event": "chat", "payload": None})
        a._handle_message(conn, {"type": "event", "event": "chat", "payload": [1, 2]})
        assert conn.run_deltas == {}

    def test_status_events_ignored(self):
        conn = _Connection()
        adapter = _adapter()
        adapter._handle_chat_event(conn, {"runId": "r1", "state": "status",
                                          "phase": "starting_model"})
        assert conn.run_deltas == {} and conn.run_waits == {}


class TestSessionKeys:
    def test_key_planning(self):
        a = _adapter()
        assert a._session_key("livachat-dev-", "dev-1") == "livachat-dev-dev-1"
        assert a._session_key("livachat-req-", "dev-1") == "livachat-req-dev-1"
        assert a._session_key("livachat-job-", "abc123") == "livachat-job-abc123"


class TestPayloadRendering:
    def test_nudge_two_part_layout(self):
        a = _adapter(nickname="小黑")
        a.webroot_dir = ""
        update = ChatlogUpdate(
            path="/tmp/dev-1/rec-1/chat/history.jsonl",
            chat_session_id="cs-1",
            new_lines=[ChatlogLine(role="user", text="问题", ts_ms=1)],
            truncated=True,
            outstanding_jobs=[{"job_key": "jk", "task_id": "t1", "summary": "进行中"}],
        )
        message = a._render_nudge(update, "Asia/Shanghai")
        assert "只允许三种响应" in message
        assert "小黑" in message                       # 契约块渲染 nickname
        assert "timezone" in message and "Asia/Shanghai" in message
        marker = '{"type": "chatlog_update"'
        data = json.loads(message[message.index(marker):])
        assert data == {
            "type": "chatlog_update",
            "chatlog_path": update.path,
            "chat_session_id": "cs-1",
            "new_lines": [{"role": "user", "text": "问题"}],
            "truncated": True,
            "outstanding_jobs": [{"job_key": "jk", "task_id": "t1", "summary": "进行中"}],
            "timezone": "Asia/Shanghai",
        }

    def test_nudge_no_nickname_no_placeholder(self):
        a = _adapter()
        update = ChatlogUpdate(path="/p", chat_session_id="cs", new_lines=[])
        message = a._render_nudge(update, "")
        assert "$agent_name_hint" not in message and "# 你的名字" not in message
        assert "未上报时区" in message

    def test_background_task_renders_noop_contract(self):
        a = _adapter()
        rendered = a._render_background_task("查机票", "cs-1", "/tmp/history.jsonl",
                                             timezone="Asia/Shanghai")
        assert "查机票" in rendered and "cs-1" in rendered
        assert "NOOP" in rendered and "Asia/Shanghai" in rendered
        assert "$task" not in rendered and "$timezone_hint" not in rendered

    def test_nudge_carries_language_hint(self):
        """docs/agent_multilingual.md：契约块末尾追加目标语言指令。"""
        a = _adapter()
        update = ChatlogUpdate(path="/p", chat_session_id="cs", new_lines=[])
        message = a._render_nudge(update, "", language="zh-Hant")
        assert '`"zh-Hant"`' in message
        assert "JSON 字段名和 action 值保持英文不变" in message

    def test_background_task_carries_language_hint(self):
        rendered = _adapter()._render_background_task(
            "查机票", "cs-1", "/tmp/history.jsonl", language="ja")
        assert '`"ja"`' in rendered
        assert "用户消息本身的语言不算要求" in rendered

    def test_background_task_empty_language_defaults_to_en(self):
        rendered = _adapter()._render_background_task(
            "查机票", "cs-1", "/tmp/history.jsonl", language="")
        assert '`"en"`' in rendered

    def test_background_task_unknown_language_skips_hint(self):
        """崩溃恢复重发（原 job 语言未知）不注入语言指令。"""
        rendered = _adapter()._render_background_task("查机票", "cs-1", "/tmp/history.jsonl")
        assert "面向用户输出语言" not in rendered


class TestAskLanguageHint:
    async def test_ask_appends_language_hint(self, monkeypatch):
        from agent_relay.adapters.base import AgentContext

        adapter = _adapter(mode="both")
        captured: dict[str, str] = {}

        async def fake_ready():
            return _Connection()

        async def fake_ensure_session(conn, cache_key, raw_key):
            return "sess"

        async def fake_send_turn(conn, session, text, run_id=""):
            captured["text"] = text

        async def fake_wait_run(conn, future, timeout_s):
            return ""

        monkeypatch.setattr(adapter, "_ensure_ready", fake_ready)
        monkeypatch.setattr(adapter, "_ensure_session", fake_ensure_session)
        monkeypatch.setattr(adapter, "_send_turn", fake_send_turn)
        monkeypatch.setattr(adapter, "_wait_run", fake_wait_run)

        ctx = AgentContext(session_id="cs-1", device_id="dev-1", language="ar")
        chunks = [c async for c in adapter.ask("hello", ctx)]
        assert chunks == []
        assert captured["text"].startswith("hello\n\n")
        assert '`"ar"`' in captured["text"]
        assert "用户消息本身的语言不算要求" in captured["text"]


class TestModeFlags:
    def test_mode_flags(self):
        assert _adapter(mode="proactive").proactive_enabled is True
        assert _adapter(mode="proactive").reactive_enabled is False
        assert _adapter(mode="reactive").reactive_enabled is True
        assert _adapter(mode="both").proactive_enabled is True
        assert _adapter().proactive_enabled is True    # 默认 proactive


class TestInteractionOwnership:
    def test_belongs_to_relay(self):
        assert OpenClawAdapter._belongs_to_relay(
            {"sessionKey": "agent:main:livachat-job-abc"})
        assert OpenClawAdapter._belongs_to_relay(
            {"sessionId": "livachat-dev-dev-1"})
        assert not OpenClawAdapter._belongs_to_relay({"sessionKey": "agent:main:main"})
        assert not OpenClawAdapter._belongs_to_relay({})


class TestReconnectRequestedHandling:
    async def test_seq_gap_requests_reconnect(self):
        """seq 空洞：关连接（recv 抛 ConnectionClosed → 重连路径）。
        用假 ws 记录 close 调用。"""
        class _FakeWs:
            closed = False

            async def close(self):
                self.closed = True

        a = _adapter()
        conn = _Connection()
        conn.ws = _FakeWs()
        conn.seq_last = 5
        a._handle_message(conn, {
            "type": "event", "event": "tick", "payload": {}, "seq": 9,
        })
        await asyncio.sleep(0.05)     # close 以 create_task 发起
        assert conn.ws.closed is True

    def test_seq_continues_no_action(self):
        a = _adapter()
        conn = _Connection()
        conn.seq_last = 5
        a._handle_message(conn, {
            "type": "event", "event": "tick", "payload": {}, "seq": 6,
        })
        assert conn.seq_last == 6


class TestCancelJob:
    """cancel_job：abort + 宽限收敛。"""

    class _Store:
        def __init__(self, job: JobRecord, *, raise_get: bool = False,
                     raise_mark: bool = False) -> None:
            self.job = job
            self.raise_get = raise_get
            self.raise_mark = raise_mark
            self.mark_calls: list[tuple[str, str]] = []

        async def get(self, job_key: str):
            if self.raise_get:
                raise RuntimeError("store down")
            return self.job if job_key == self.job.job_key else None

        async def mark_failed(self, job_key: str, reason: str):
            self.mark_calls.append((job_key, reason))
            if self.raise_mark:
                raise RuntimeError("write down")
            self.job = replace(self.job, status="failed", fail_reason=reason)
            return self.job

    @staticmethod
    def _job(status: str = STATUS_RUNNING, task_id: str = "run-1",
             key: str = "job-1") -> JobRecord:
        return JobRecord(
            job_key=key,
            hermes_task_id=task_id,
            session_id="sess-1",
            device_id="dev-1",
            status=status,
            summary="查询机票",
        )

    def _wire(self, adapter: OpenClawAdapter, store, *, ready_error=None,
              abort_effect=None):
        conn = _Connection()
        adapter.job_store = store
        aborts: list[str] = []

        async def ensure_ready():
            if ready_error is not None:
                raise ready_error
            return conn

        async def ensure_subscribed(_conn, key):
            return key

        async def abort(_conn, run_id):
            aborts.append(run_id)
            if abort_effect is not None:
                abort_effect()

        async def unsubscribe(_conn, _key):
            return None

        adapter._ensure_ready = ensure_ready          # type: ignore[method-assign]
        adapter._ensure_subscribed = ensure_subscribed  # type: ignore[method-assign]
        adapter._abort_run = abort                    # type: ignore[method-assign]
        adapter._unsubscribe = unsubscribe            # type: ignore[method-assign]
        return conn, aborts

    async def test_running_job_aborted_event_converges(self):
        store = self._Store(self._job())

        def effect():
            store.job = replace(store.job, status="failed", fail_reason="aborted")

        adapter = _adapter(job_cancel_grace_s=1.0)
        self._wire(adapter, store, abort_effect=effect)
        assert await adapter.cancel_job("job-1") is True
        assert store.job.fail_reason == "aborted"

    async def test_requested_job_uses_deterministic_run_id(self):
        store = self._Store(self._job(status=STATUS_REQUESTED, task_id=""))
        adapter = _adapter(job_cancel_grace_s=0.0)
        _, aborts = self._wire(adapter, store, abort_effect=lambda: None)
        assert await adapter.cancel_job("job-1") is True
        assert aborts == ["livachat:job:job-1"]
        assert store.mark_calls == [("job-1", "cancelled")]

    async def test_running_job_uses_stored_run_id_and_falls_back(self):
        store = self._Store(self._job(task_id="run-abc"))
        adapter = _adapter(job_cancel_grace_s=0.0)
        _, aborts = self._wire(adapter, store)
        assert await adapter.cancel_job("job-1") is True
        assert aborts == ["run-abc"]
        assert store.job.status == "failed"
        assert store.job.fail_reason == "cancelled"

    async def test_terminal_job_returns_false_without_abort(self):
        store = self._Store(self._job(status=STATUS_DONE))
        adapter = _adapter()
        _, aborts = self._wire(adapter, store)
        assert await adapter.cancel_job("job-1") is False
        assert aborts == []

    async def test_unknown_job_returns_false(self):
        store = self._Store(self._job())
        adapter = _adapter()
        self._wire(adapter, store)
        assert await adapter.cancel_job("missing") is False

    async def test_store_read_failure_returns_false(self):
        store = self._Store(self._job(), raise_get=True)
        adapter = _adapter()
        self._wire(adapter, store)
        assert await adapter.cancel_job("job-1") is False

    async def test_no_job_store_returns_false(self):
        adapter = _adapter()
        assert await adapter.cancel_job("job-1") is False

    async def test_connect_failure_marks_failed_immediately(self):
        store = self._Store(self._job())
        adapter = _adapter(job_cancel_grace_s=5.0)
        self._wire(adapter, store, ready_error=RuntimeError("no conn"))
        assert await adapter.cancel_job("job-1") is True
        assert store.mark_calls == [("job-1", "cancelled")]

    async def test_connect_and_mark_failure_returns_false(self):
        store = self._Store(self._job(), raise_mark=True)
        adapter = _adapter(job_cancel_grace_s=5.0)
        self._wire(adapter, store, ready_error=RuntimeError("no conn"))
        assert await adapter.cancel_job("job-1") is False


class TestDispatchCancelledClosure:
    """dispatch 的 mark_running CAS 失败必须 abort 已提交的 run（§3.3.4 竞态收口）。"""

    class _Store:
        def __init__(self, job):
            self.job = job

        async def outstanding_jobs_for_device(self, _device_id):
            return []

        async def in_flight_count(self):
            return 0

        async def create(self, **_kwargs):
            return self.job

        async def mark_running(self, _job_key, _run_id):
            return False

    async def test_cas_failure_aborts_run_before_cleanup(self):
        from agent_relay.adapters.base import AgentContext
        from agent_relay.job_store import STATUS_REQUESTED, JobRecord

        job = JobRecord(
            job_key="job-1",
            hermes_task_id="",
            session_id="sess-1",
            device_id="dev-1",
            status=STATUS_REQUESTED,
            summary="查询机票",
        )
        store = self._Store(job)
        adapter = _adapter(job_parent_link=False)
        conn = _Connection()
        adapter.job_store = store
        aborts: list[str] = []
        unsubscribed: list[str] = []

        async def rpc(_conn, method, params=None):
            assert method == "sessions.create"
            return {"key": "canonical"}

        async def subscribed(_conn, key):
            return key

        async def send_turn(_conn, _key, _text, run_id):
            return None

        async def abort(_conn, run_id):
            aborts.append(run_id)

        async def unsubscribe(_conn, key):
            unsubscribed.append(key)

        adapter._rpc = rpc  # type: ignore[method-assign]
        adapter._ensure_subscribed = subscribed  # type: ignore[method-assign]
        adapter._send_turn = send_turn  # type: ignore[method-assign]
        adapter._abort_run = abort  # type: ignore[method-assign]
        adapter._unsubscribe = unsubscribe  # type: ignore[method-assign]

        context = AgentContext(session_id="sess-1", device_id="dev-1", language="")
        chunks = [
            c async for c in adapter._dispatch_job(
                conn, context, {"spec": {"task": "查机票"}, "summary": "查机票"}
            )
        ]
        assert chunks == []
        assert aborts == ["livachat:job:job-1"]
        assert unsubscribed == ["canonical"]
        assert "livachat:job:job-1" not in conn.job_runs
        assert "job-1" not in conn.job_sessions
