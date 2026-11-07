"""Unit tests: ChatlogWatcher trigger rules, parsing, truncation (no Redis)."""

from __future__ import annotations

import asyncio
import json

from agent_relay.adapters.base import (
    AgentAdapter,
    AgentContext,
    AgentResponseChunk,
    ChatlogUpdate,
)
from agent_relay.chatlog_watcher import ChatlogWatcher


def _append_lines(path, *texts):
    """同步追加文本行（测试写夹具用，供 async 测试调用）。"""
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(texts)

def _append_bytes(path, *chunks):
    with open(path, "ab") as f:
        f.writelines(chunks)


class RecordingAdapter(AgentAdapter):
    """Proactive mock adapter that records nudges."""

    name = "mock"

    def __init__(self):
        self.nudges: list[ChatlogUpdate] = []
        self.reactive_enabled = False
        self.proactive_enabled = True

    async def nudge(self, update: ChatlogUpdate, context: AgentContext):
        self.nudges.append(update)
        if False:
            yield  # keep async generator

    async def ask(self, text: str, context: AgentContext):
        if False:
            yield


def _line(role: str, text: str, sid: str = "sess-1", ts: int = 1000) -> str:
    return json.dumps({
        "ts": ts, "role": role, "text": text, "ref_id": "tr-1", "chat_session_id": sid,
    }, ensure_ascii=False)


def _make_watcher(adapter=None, recording_info=None, **kwargs) -> ChatlogWatcher:
    adapter = adapter or RecordingAdapter()
    ctx = AgentContext(session_id="sess-1", device_id="dev-1", language="en")

    async def lookup(device_uuid=None, **kw):
        return recording_info  # None → 未命中（watcher 禁用）

    defaults = {
        "session_id": "sess-1",
        "device_id": "dev-1",
        "adapters": [adapter],
        "context": ctx,
        "recording_lookup": lookup,
        "nudge_coalesce_s": 0.01,
        "nudge_min_interval_s": 0.0,
    }
    defaults.update(kwargs)
    return ChatlogWatcher(**defaults)


class TestParseLines:
    def test_filters_by_chat_session_id(self):
        watcher = _make_watcher()
        raw = "\n".join([
            _line("user", "mine", sid="sess-1"),
            _line("user", "other", sid="sess-2"),
            _line("user", "unattributed", sid=""),
        ])
        lines = watcher._parse_lines(raw)
        assert [line.text for line in lines] == ["mine"]

    def test_ignores_unknown_roles(self):
        watcher = _make_watcher()
        raw = _line("system", "meta", sid="sess-1") + "\n" + _line("user", "hello", sid="sess-1")
        lines = watcher._parse_lines(raw)
        assert [line.role for line in lines] == ["user"]

    def test_skips_partial_trailing_line(self):
        watcher = _make_watcher()
        raw = _line("user", "ok", sid="sess-1") + "\n{\"ts\": 1,"
        lines = watcher._parse_lines(raw)
        assert len(lines) == 1

    def test_user_line_with_images_field_is_ignored(self):
        """fd_pipeline_llm_v2 回归：user 行新增 images 字段被忽略，行为不变。"""
        watcher = _make_watcher()
        raw = json.dumps({
            "ts": 1757500000123, "utc": "2026-09-11T08:26:40.123Z", "role": "user",
            "text": "看看这个东西", "ref_id": "trans_ab12cd34ef56",
            "chat_session_id": "sess-1",
            "images": ["../image/0193f2c1-7a2b-7e3d-9c4a-1b2c3d4e5f60.jpg"],
        }, ensure_ascii=False)
        lines = watcher._parse_lines(raw)
        assert [(line.role, line.text) for line in lines] == [("user", "看看这个东西")]


class TestTriggerRules:
    async def test_user_line_triggers_nudge(self):
        adapter = RecordingAdapter()
        watcher = _make_watcher(adapter, nudge_coalesce_s=0.0, nudge_min_interval_s=0.0)
        watcher._path = "/tmp/x.jsonl"
        watcher._on_new_lines([__import__("agent_relay.adapters.base", fromlist=["ChatlogLine"]).ChatlogLine(role="user", text="hi", ts_ms=1)])
        await asyncio.sleep(0.02)
        assert len(adapter.nudges) == 1
        assert adapter.nudges[0].new_lines[0].text == "hi"

    async def test_push_callback_receives_adapter_name(self):
        class PushingAdapter(RecordingAdapter):
            async def nudge(self, update: ChatlogUpdate, context: AgentContext):
                self.nudges.append(update)
                yield AgentResponseChunk(text="hi", is_final=True)

        from agent_relay.adapters.base import ChatlogLine

        adapter = PushingAdapter()
        watcher = _make_watcher(adapter, nudge_coalesce_s=0.0, nudge_min_interval_s=0.0)
        watcher._path = "/tmp/x.jsonl"
        pushes: list[tuple[str, str, str, str]] = []

        async def push(adapter_name, device_id, session_id, chunk):
            pushes.append((adapter_name, device_id, session_id, chunk.text))

        watcher.set_push_callback(push)
        watcher._on_new_lines([ChatlogLine(role="user", text="hi", ts_ms=1)])
        await asyncio.sleep(0.02)
        assert pushes == [("mock", "dev-1", "sess-1", "hi")]

    async def test_assistant_only_does_not_trigger(self):
        adapter = RecordingAdapter()
        watcher = _make_watcher(adapter, nudge_coalesce_s=0.01, nudge_min_interval_s=0.0)
        watcher._path = "/tmp/x.jsonl"
        from agent_relay.adapters.base import ChatlogLine
        watcher._on_new_lines([ChatlogLine(role="assistant", text="hi", ts_ms=1)])
        await asyncio.sleep(0.1)
        assert adapter.nudges == []

    async def test_assistant_lines_batch_with_next_user_line(self):
        adapter = RecordingAdapter()
        watcher = _make_watcher(adapter, nudge_coalesce_s=0.0, nudge_min_interval_s=0.0)
        watcher._path = "/tmp/x.jsonl"
        from agent_relay.adapters.base import ChatlogLine
        watcher._on_new_lines([ChatlogLine(role="assistant", text="a1", ts_ms=1)])
        watcher._on_new_lines([ChatlogLine(role="user", text="u1", ts_ms=2)])
        await asyncio.sleep(0.02)
        assert len(adapter.nudges) == 1
        texts = [line.text for line in adapter.nudges[0].new_lines]
        assert texts == ["a1", "u1"]

    async def test_min_interval_batches_rapid_user_lines(self):
        adapter = RecordingAdapter()
        watcher = _make_watcher(adapter, nudge_coalesce_s=0.02, nudge_min_interval_s=10.0)
        watcher._path = "/tmp/x.jsonl"
        from agent_relay.adapters.base import ChatlogLine
        watcher._on_new_lines([ChatlogLine(role="user", text="u1", ts_ms=1)])
        watcher._on_new_lines([ChatlogLine(role="user", text="u2", ts_ms=2)])
        await asyncio.sleep(0.15)
        assert len(adapter.nudges) == 1
        assert [line.text for line in adapter.nudges[0].new_lines] == ["u1", "u2"]

    async def test_inflight_batches_until_complete(self):
        adapter = RecordingAdapter()
        watcher = _make_watcher(adapter, nudge_coalesce_s=0.0, nudge_min_interval_s=0.0)
        watcher._path = "/tmp/x.jsonl"
        from agent_relay.adapters.base import ChatlogLine

        # Simulate an in-flight nudge: second user line must wait.
        watcher._nudge_in_flight = True
        watcher._on_new_lines([ChatlogLine(role="user", text="u1", ts_ms=1)])
        watcher._nudge_in_flight = False
        await asyncio.sleep(0.15)
        assert len(adapter.nudges) == 1
        assert adapter.nudges[0].new_lines[0].text == "u1"


class TestTruncate:
    def test_line_limit(self):
        watcher = _make_watcher(nudge_max_lines=2)
        from agent_relay.adapters.base import ChatlogLine
        lines = [ChatlogLine(role="user", text=f"l{i}", ts_ms=i) for i in range(5)]
        kept, truncated = watcher._truncate(lines)
        assert truncated is True
        assert [line.text for line in kept] == ["l3", "l4"]

    def test_byte_limit(self):
        watcher = _make_watcher(nudge_max_bytes=10)
        from agent_relay.adapters.base import ChatlogLine
        lines = [ChatlogLine(role="user", text="x" * 8, ts_ms=i) for i in range(3)]
        kept, truncated = watcher._truncate(lines)
        assert truncated is True
        assert len(kept) == 1

    def test_within_limits_no_truncation(self):
        watcher = _make_watcher(nudge_max_lines=10, nudge_max_bytes=1000)
        from agent_relay.adapters.base import ChatlogLine
        lines = [ChatlogLine(role="user", text="hi", ts_ms=i) for i in range(3)]
        kept, truncated = watcher._truncate(lines)
        assert truncated is False
        assert len(kept) == 3


class TestPathResolution:
    async def test_resolve_failed_disables_watcher(self):
        watcher = _make_watcher()  # lookup 返回 None → 未命中
        started = await watcher.start()
        assert started is False
        await watcher.stop()

    async def test_resolve_success_starts(self, tmp_path):
        base = tmp_path / "lvdata"
        (base / "dev-1" / "rec-2026-08-09" / "chat").mkdir(parents=True)
        watcher = _make_watcher(
            recording_info={
                "recording_session_id": "rec-2026-08-09",
                "base_path": str(base / "dev-1" / "rec-2026-08-09"),
            },
            base_dir=str(base),
            poll_interval_s=0.01,
        )
        started = await watcher.start()
        assert started is True
        assert watcher._path == str(base / "dev-1" / "rec-2026-08-09" / "chat" / "history.jsonl")
        await watcher.stop()

    async def test_partial_trailing_line_kept_for_next_poll(self, tmp_path):
        """§5.3: an incomplete trailing line must not be skipped — the offset
        advances only past complete lines, so the tail is re-read next poll."""
        base = tmp_path / "lvdata"
        chat_dir = base / "dev-1" / "rec-1" / "chat"
        chat_dir.mkdir(parents=True)
        path = chat_dir / "history.jsonl"

        adapter = RecordingAdapter()
        watcher = _make_watcher(
            adapter,
            recording_info={"recording_session_id": "rec-1", "base_path": str(chat_dir)},
            base_dir=str(base),
        )
        started = await watcher.start()
        assert started is True

        # Write a complete line + a partial line WITHOUT trailing newline.
        _append_lines(path, _line("user", "full-line", sid="sess-1") + "\n", '{"ts": 99, "role": "user", "text": "par')
        await watcher._poll_once()
        await asyncio.sleep(0.05)  # flush is a task

        # Complete line nudged; partial tail must NOT be skipped.
        assert len(adapter.nudges) == 1
        assert adapter.nudges[0].new_lines[0].text == "full-line"
        assert watcher._offset < path.stat().st_size  # offset behind the tail

        # Complete the partial line → it is picked up on the next poll.
        _append_lines(path, 'tial", "chat_session_id": "sess-1"}\n')
        await watcher._poll_once()
        await asyncio.sleep(0.05)  # flush is a task

        assert len(adapter.nudges) == 2
        assert adapter.nudges[1].new_lines[0].text == "partial"
        assert watcher._offset == path.stat().st_size
        await watcher.stop()

    async def test_half_multibyte_char_does_not_wedge(self, tmp_path):
        """Non-atomic writes can leave a multi-byte char split at the read
        boundary. The watcher must rewind to the char boundary and keep
        advancing, not wedge in a decode-error loop."""
        base = tmp_path / "lvdata"
        chat_dir = base / "dev-1" / "rec-1" / "chat"
        chat_dir.mkdir(parents=True)
        path = chat_dir / "history.jsonl"

        adapter = RecordingAdapter()
        watcher = _make_watcher(
            adapter,
            recording_info={"recording_session_id": "rec-1", "base_path": str(chat_dir)},
            base_dir=str(base),
        )
        started = await watcher.start()
        assert started is True

        # Advance the offset past a complete line first.
        _append_lines(path, _line("user", "first", sid="sess-1") + "\n")
        await watcher._poll_once()
        await asyncio.sleep(0.05)
        assert len(adapter.nudges) == 1

        # Writer splits a Chinese char mid-write: "话" (E8 AF BD) with only
        # E8 AF flushed so far, followed by a complete line.
        _append_bytes(path, b"\xe8\xaf",
                      _line("user", "second", sid="sess-1").encode("utf-8") + b"\n")

        # Must not raise and must not wedge: complete line parsed; the
        # 2 stray half-char bytes are skipped (before the offset).
        await watcher._poll_once()
        await asyncio.sleep(0.05)
        assert len(adapter.nudges) == 2
        assert adapter.nudges[1].new_lines[0].text == "second"
        assert watcher._offset == path.stat().st_size - 2
        await watcher.stop()

    async def test_poll_once_reads_new_lines(self, tmp_path):
        base = tmp_path / "lvdata"
        chat_dir = base / "dev-1" / "rec-1" / "chat"
        chat_dir.mkdir(parents=True)
        path = chat_dir / "history.jsonl"
        path.write_text(_line("user", "first", sid="sess-1") + "\n", encoding="utf-8")

        adapter = RecordingAdapter()
        watcher = _make_watcher(
            adapter,
            recording_info={"recording_session_id": "rec-1", "base_path": str(chat_dir)},
            base_dir=str(base),
        )
        started = await watcher.start()
        assert started is True

        # Append a user line, then poll once.
        _append_lines(path, _line("user", "second", sid="sess-1") + "\n")
        await watcher._poll_once()
        await asyncio.sleep(0.05)  # flush runs as a task

        assert len(adapter.nudges) == 1
        assert adapter.nudges[0].new_lines[0].text == "second"
        await watcher.stop()
