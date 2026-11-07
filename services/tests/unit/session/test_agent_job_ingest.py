"""Unit tests: agent job 结果注入（fd_pipeline_llm_v2）。

覆盖：WorkingMemory 条目结构与前缀、下一轮 LLM 组装可见性、截断策略、
chatlog 全文无前缀、ref_id 去重、无 live session 边界、NoOp sink 回归、
omni 只写 chatlog、metrics labels。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recording.sink import NoOpRecordingSink
from session.agent_job_ingest import (
    AGENT_JOB_HISTORY_MAX_CHARS,
    AGENT_JOB_PREFIX,
    _truncate_for_history,
    ingest_agent_job_result,
)
from session.history import WorkingMemory
from session.orchestrator import DialogOrchestrator
from tests.unit.session.conftest import FakeChatStore

MSG_ID = "msg_agent_job_jk1"


class _FakeSink:
    """Duck-typed RecordingSink：记录 on_chat_text 调用。"""

    def __init__(self):
        self.calls: list[dict] = []

    def on_chat_text(self, **kwargs) -> None:
        self.calls.append(kwargs)


class _BoomHistory:
    def has_agent_job(self, message_id: str) -> bool:
        return False

    def add_message(self, *args, **kwargs) -> None:
        raise RuntimeError("boom")


class _BoomSink:
    def on_chat_text(self, **kwargs) -> None:
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch):
    import shared.observability.metrics as metrics_mod

    monkeypatch.setattr(metrics_mod, "_registry", None)


def _make_dialog(*, sink: _FakeSink | None = None,
                 chat_store: FakeChatStore | None = None) -> DialogOrchestrator:
    return DialogOrchestrator(
        handler=MagicMock(),
        codec=MagicMock(),
        session_mgr=None,
        llm_service=None,
        tts_service=None,
        opus_encoder=None,
        opus_decoder=None,
        system_prompt="You are a companion.",
        generation_opts={},
        chat_store=chat_store,
        pipeline_mode="full",
        recording_sink=sink,
    )


def _ingest_count(path: str, result: str) -> int:
    from shared.observability.metrics import get_registry

    return get_registry().agent_job_ingest_total().labels(path=path, result=result)._value.get()


class TestDialogRecordAgentJobResult:
    def test_writes_history_entry_with_prefix_and_marks(self):
        sink = _FakeSink()
        store = FakeChatStore()
        orch = _make_dialog(sink=sink, chat_store=store)
        orch._ctx = MagicMock(session_id="live-1", device_id="dev-1")
        orch._history = WorkingMemory()

        orch.record_agent_job_result(text="明天晴,22 度", message_id=MSG_ID)

        entries = orch._history.get_recent()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["role"] == "assistant"
        assert entry["content"].startswith(AGENT_JOB_PREFIX)
        assert "明天晴,22 度" in entry["content"]
        assert entry["source"] == "agent_job"
        assert entry["ref_id"] == MSG_ID
        # chat store 与 history 同点写 messages 行（history 形态，restore 可重放）
        assert len(store.messages) == 1
        m = store.messages[0]
        assert m["kind"] == "agent_job" and m["role"] == "assistant"
        assert m["content"].startswith(AGENT_JOB_PREFIX)
        assert m["ref_id"] == MSG_ID
        assert m["device_uuid"] == "dev-1" and m["session_id"] == "live-1"

    def test_chatlog_line_carries_agent_identity(self):
        """agent_name/agent_id/job_key 随 on_chat_text 透传（doc §2.5）——
        lvdata 据此铸造 agent_job 事件（event_ref 用 job_key）。"""
        sink = _FakeSink()
        orch = _make_dialog(sink=sink)
        orch._ctx = MagicMock(session_id="live-1", device_id="dev-1")
        orch._history = WorkingMemory()

        orch.record_agent_job_result(text="结果", message_id=MSG_ID,
                                     agent_name="hermes", agent_id="小七",
                                     job_key="jk-1")

        call = sink.calls[0]
        assert call["source"] == "agent_job"
        assert call["ref_id"] == MSG_ID
        assert call["agent_name"] == "hermes"
        assert call["agent_id"] == "小七"
        assert call["job_key"] == "jk-1"

    @pytest.mark.asyncio
    async def test_next_turn_messages_contain_job_result(self):
        """下一轮 _start_thinking 组装的 messages 含该 assistant 文本。"""
        sink = _FakeSink()
        orch = _make_dialog(sink=sink)
        orch._ctx = MagicMock()
        orch._ctx.session_id = "live-1"
        orch._ctx.enable_video = False
        orch._ctx.latest_video_frames = []
        orch._ctx.llm_provider = ""
        orch._ctx.tts_provider = ""
        orch._ctx.epoch = 0
        orch._history = WorkingMemory()
        orch._history.add_message("user", "帮我查下天气")
        orch._history.add_message("assistant", "好的")
        orch.record_agent_job_result(text="明天晴,22 度", message_id=MSG_ID)
        orch._history.add_message("user", "结果呢")

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
            await orch._start_thinking("结果呢")

        roles = [m.role for m in llm_messages]
        assert "assistant" in roles  # job 结果以 assistant 身份进入上下文
        job_entry = next(m for m in llm_messages if m.role == "assistant" and m.content.startswith(AGENT_JOB_PREFIX))
        assert "明天晴,22 度" in job_entry.content
        assert llm_messages[-1].role == "user"
        assert llm_messages[-1].content == "结果呢"

    def test_chatlog_full_text_no_prefix(self):
        sink = _FakeSink()
        orch = _make_dialog(sink=sink)
        orch._ctx = MagicMock(session_id="live-1")
        orch._history = WorkingMemory()

        orch.record_agent_job_result(text="明天晴,22 度", message_id=MSG_ID)

        assert len(sink.calls) == 1
        call = sink.calls[0]
        assert call["role"] == "assistant"
        assert call["text"] == "明天晴,22 度"  # 全文，无前缀
        assert not call["text"].startswith(AGENT_JOB_PREFIX)
        assert call["ref_id"] == MSG_ID
        assert call["chat_session_id"] == "live-1"
        assert call["source"] == "agent_job"

    def test_dedup_same_message_id(self):
        sink = _FakeSink()
        store = FakeChatStore()
        orch = _make_dialog(sink=sink, chat_store=store)
        orch._ctx = MagicMock(session_id="live-1", device_id="dev-1")
        orch._history = WorkingMemory()

        orch.record_agent_job_result(text="r1", message_id=MSG_ID)
        orch.record_agent_job_result(text="r1", message_id=MSG_ID)  # 崩溃重投

        entries = [e for e in orch._history.get_recent() if e.get("ref_id") == MSG_ID]
        assert len(entries) == 1  # WorkingMemory 去重
        assert len(store.messages) == 1  # chat store 同去重（restore 重放不重复）
        assert len(sink.calls) == 2  # chatlog append-only，物理重复行可经 ref_id 识别
        assert _ingest_count("history", "dedup_skip") == 1
        assert _ingest_count("history", "ok") == 1
        assert _ingest_count("chatlog", "ok") == 2

    def test_no_live_session_writes_chatlog_only(self):
        """_ctx=None（connect→session.start 窗口）→ history/chat store 不写、
        chatlog 空 session_id。"""
        sink = _FakeSink()
        store = FakeChatStore()
        orch = _make_dialog(sink=sink, chat_store=store)  # _ctx=None, _history=None

        orch.record_agent_job_result(text="r1", message_id=MSG_ID)

        assert orch._history is None
        assert len(store.messages) == 0
        assert len(sink.calls) == 1
        assert sink.calls[0]["chat_session_id"] == ""
        assert _ingest_count("history", "no_history") == 1

    def test_noop_sink_no_crash(self):
        """recording 关闭时 NoOp sink：签名匹配回归，不抛 TypeError。"""
        orch = _make_dialog(sink=NoOpRecordingSink())
        orch._ctx = MagicMock(session_id="live-1")
        orch._history = WorkingMemory()

        orch.record_agent_job_result(text="r1", message_id=MSG_ID)  # 不抛

        assert _ingest_count("chatlog", "ok") == 1


class TestTruncation:
    def test_truncates_head_and_tail_with_marker(self, monkeypatch):
        monkeypatch.setattr("session.agent_job_ingest.AGENT_JOB_HISTORY_MAX_CHARS", 100)
        text = "x" * 500
        out = _truncate_for_history(text)
        assert len(out) < 500
        assert "\n…[truncated]…\n" in out
        assert out.startswith("x" * 75)
        assert out.endswith("x" * 25)

    def test_no_truncation_when_max_chars_zero(self, monkeypatch):
        monkeypatch.setattr("session.agent_job_ingest.AGENT_JOB_HISTORY_MAX_CHARS", 0)
        text = "x" * 500
        assert _truncate_for_history(text) == text

    def test_no_truncation_when_within_limit(self, monkeypatch):
        monkeypatch.setattr("session.agent_job_ingest.AGENT_JOB_HISTORY_MAX_CHARS", 4096)
        text = "short"
        assert _truncate_for_history(text) == text

    def test_history_content_truncated_via_ingest(self, monkeypatch):
        """ingest 入口生效：WorkingMemory 内为截断 + 标记，chatlog 仍全文。"""
        monkeypatch.setattr("session.agent_job_ingest.AGENT_JOB_HISTORY_MAX_CHARS", 100)
        sink = _FakeSink()
        orch = _make_dialog(sink=sink)
        orch._ctx = MagicMock(session_id="live-1")
        orch._history = WorkingMemory()
        full = "中" * 500

        orch.record_agent_job_result(text=full, message_id=MSG_ID)

        entry = orch._history.get_recent()[0]
        assert "\n…[truncated]…\n" in entry["content"]
        assert len(entry["content"]) < 500
        assert sink.calls[0]["text"] == full  # chatlog 全文

    def test_module_default_constant_is_int(self):
        # 环境变量可覆盖（AGENT_JOB_HISTORY_MAX_CHARS），只断言类型与语义边界
        assert isinstance(AGENT_JOB_HISTORY_MAX_CHARS, int)
        assert AGENT_JOB_HISTORY_MAX_CHARS >= 0


class TestOmniModeRecordAgentJobResult:
    def test_omni_mode_writes_chatlog_only(self):
        """全模态模式下 history=None（realtime API 自持上下文，写 WorkingMemory 是死写
        且污染 turn_idx）——原 OmniOrchestrator 语义由 DialogOrchestrator 的
        `_omni_active` 分支承接（docs/fd_pipeline_omni_mode.md §4 D7）。"""
        sink = _FakeSink()
        orch = _make_dialog(sink=sink)
        orch._ctx = MagicMock(session_id="live-1")
        orch._history = WorkingMemory()
        orch._omni_active = True

        orch.record_agent_job_result(text="r1", message_id=MSG_ID)

        assert len(orch._history) == 0  # _history 无新条目
        assert len(sink.calls) == 1
        assert sink.calls[0]["source"] == "agent_job"
        assert _ingest_count("history", "no_history") == 1
        assert _ingest_count("chatlog", "ok") == 1


class TestIngestMetrics:
    def test_failed_paths_labeled(self):
        ingest_agent_job_result(
            history=_BoomHistory(), recording_sink=None,
            live_session_id="", text="x", message_id=MSG_ID,
        )
        assert _ingest_count("history", "failed") == 1
        assert _ingest_count("chatlog", "ok") == 1

        wm2 = WorkingMemory()
        ingest_agent_job_result(
            history=wm2, recording_sink=_BoomSink(),
            live_session_id="", text="x", message_id=MSG_ID,
        )
        assert _ingest_count("chatlog", "failed") == 1
        assert _ingest_count("history", "ok") == 1
