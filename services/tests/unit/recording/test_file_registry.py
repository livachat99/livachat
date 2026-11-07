"""Unit tests: recording_files 登记接线（recording_data_process.md §2.1）。

覆盖：RecordingFileRegistry 适配器（rel_path 相对化 / 失败吞掉）+ RecordingSession
各写入点的登记/ touch 调用（session.json、音频段 close、video 帧、三个 jsonl）。
"""

from __future__ import annotations

import asyncio
import json
import time

from recording.file_registry import RecordingFileRegistry, uuid7_to_ms
from recording.session import RecordingSession

# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------


class _FakeChatStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def record_file(self, **kw) -> None:
        self.calls.append(("record", kw))

    async def touch_file(self, **kw) -> None:
        self.calls.append(("touch", kw))


class _BoomChatStore:
    async def record_file(self, **kw) -> None:
        raise TimeoutError("queue full")

    async def touch_file(self, **kw) -> None:
        raise RuntimeError("db gone")


async def test_registry_relative_path_and_passthrough(tmp_path):
    store = _FakeChatStore()
    reg = RecordingFileRegistry(store, tmp_path, device_uuid="d1",
                                recording_session_id="r1")
    path = tmp_path / "d1" / "r1" / "audio" / "x.ogg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"123")
    await reg.record(kind="audio", path=path, size_bytes=3, ts_ms=42,
                     duration_ms=60_000)
    await reg.touch(path=path, size_bytes=4)
    op, kw = store.calls[0]
    assert op == "record"
    assert kw["rel_path"] == "d1/r1/audio/x.ogg"
    assert kw["device_uuid"] == "d1" and kw["recording_session_id"] == "r1"
    assert kw["kind"] == "audio" and kw["ts_ms"] == 42
    op, kw = store.calls[1]
    assert op == "touch" and kw["rel_path"] == "d1/r1/audio/x.ogg"


async def test_registry_swallows_errors(tmp_path):
    """登记失败不得打断录制热路径（backfill reconcile 兜底）。"""
    reg = RecordingFileRegistry(_BoomChatStore(), tmp_path, device_uuid="d",
                                recording_session_id="r")
    await reg.record(kind="audio", path=tmp_path / "missing.ogg", size_bytes=1)
    await reg.touch(path=tmp_path / "missing.ogg", size_bytes=1)


async def test_failed_registration_lands_in_reconcile_list(tmp_path):
    """登记/touch 失败落本地 reconcile 清单（doc §2.1）：一行一失败，可人工补。"""
    reg = RecordingFileRegistry(_BoomChatStore(), tmp_path, device_uuid="d",
                                recording_session_id="r")
    await reg.record(kind="audio", path=tmp_path / "a.ogg", size_bytes=1)
    await reg.touch(path=tmp_path / "a.ogg", size_bytes=2)
    lines = (tmp_path / "recording_files_reconcile.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["op"] == "record" and first["rel_path"] == "a.ogg"
    assert "queue full" in first["error"]
    assert json.loads(lines[1])["op"] == "touch"


def test_uuid7_to_ms():
    from shared.utils.ids import uuid7

    ms = uuid7_to_ms(uuid7())
    assert ms is not None and abs(ms - time.time() * 1000) < 5_000
    assert uuid7_to_ms("not-a-uuid") is None


# ---------------------------------------------------------------------------
# 会话写入点接线
# ---------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.touches: list[dict] = []

    async def record(self, *, kind, path, size_bytes, ts_ms=None,
                     duration_ms=None) -> None:
        self.records.append({"kind": kind, "path": path, "size": size_bytes,
                             "ts_ms": ts_ms, "duration_ms": duration_ms})

    async def touch(self, *, path, size_bytes) -> None:
        self.touches.append({"path": path, "size": size_bytes})


def _make_session(tmp_path) -> RecordingSession:
    return RecordingSession(
        recording_session_id="rec-1",
        device_uuid="dev-1",
        session_date="2026-08-10",
        storage_path=tmp_path / "rec-1",
        config={},
    )


async def test_start_registers_session_json_and_chat_logs(tmp_path):
    s = _make_session(tmp_path)
    reg = _FakeRegistry()
    s.file_registry = reg
    await s.start()
    try:
        assert [r["kind"] for r in reg.records] == [
            "session", "chat_history", "chat_transcribe", "chat_translate"]
        assert reg.records[0]["path"].name == "session.json"
        assert reg.records[0]["size"] > 0
        assert [r["path"].name for r in reg.records[1:]] == [
            "history.jsonl", "transcribe.jsonl", "translate.jsonl"]
    finally:
        await s.finalize()


async def test_audio_segment_close_registers(tmp_path):
    s = _make_session(tmp_path)
    reg = _FakeRegistry()
    s.file_registry = reg
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    try:
        s._audio_segment_ms = 59_990  # 下一包触发 60s 轮转
        s.on_media(kind="audio", data=b"\x00" * 20, duration_ms=20, ts_ms=0)
        await asyncio.sleep(0.1)
        assert s.audio_segments == 1
        recs = [r for r in reg.records if r["kind"] == "audio"]
        assert len(recs) == 1
        assert recs[0]["path"].suffix == ".ogg"
        assert recs[0]["duration_ms"] == 60_010
        assert isinstance(recs[0]["ts_ms"], int)  # uuid7 文件名解析
    finally:
        await s.finalize()


async def test_video_frame_registers(tmp_path):
    s = _make_session(tmp_path)
    reg = _FakeRegistry()
    s.file_registry = reg
    await s.start()
    try:
        payload = b"\xff\xd8" * 10
        s.on_media(kind="video", data=payload, ts_ms=0)
        await asyncio.sleep(0.1)
        recs = [r for r in reg.records if r["kind"] == "video"]
        assert len(recs) == 1
        assert recs[0]["path"].suffix == ".jpg"
        assert recs[0]["size"] == len(payload)
    finally:
        await s.finalize()


async def test_chat_append_touches_history_jsonl(tmp_path):
    s = _make_session(tmp_path)
    reg = _FakeRegistry()
    s.file_registry = reg
    await s.start()
    try:
        s.on_chat_text(role="user", text="你好", ts_ms=1_000)
        await asyncio.sleep(0.1)
        hist = [t for t in reg.touches if t["path"].name == "history.jsonl"]
        assert len(hist) == 1
        assert hist[0]["size"] > 0
    finally:
        await s.finalize()


async def test_jsonl_rows_registered_then_touched_end_to_end(tmp_path):
    """回归（真实 ChatStore）：jsonl 行必须先在 start() 里 record——逐条 append
    只做 touch，而 touch 对不存在的行是静默跳过（touch_missing_row）；只有
    fake registry 看不出这一契约差异，此用例锚定真实存储行为。"""
    import sqlite3

    from shared.persistence.chat_store import ChatStore

    db = str(tmp_path / "chat_server.db")
    store = ChatStore(db)
    await store.start()
    s = _make_session(tmp_path)
    s.file_registry = RecordingFileRegistry(store, tmp_path, device_uuid="dev-1",
                                            recording_session_id="rec-1")
    await s.start()
    try:
        s.on_chat_text(role="user", text="你好", ts_ms=1_000)
        await asyncio.sleep(0.1)
        await store.drain()
    finally:
        await s.finalize()
        await store.drain()
        await store.stop()

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = {r["kind"]: r for r in conn.execute("SELECT * FROM recording_files")}
    conn.close()
    assert {"session", "chat_history", "chat_transcribe",
            "chat_translate"} <= set(rows)
    hist = rows["chat_history"]
    assert hist["size_bytes"] > 0  # append 后 touch 生效
    assert hist["updated_at"] >= hist["created_at"]


async def test_attach_reregisters_logs_idempotently(tmp_path):
    """attach 重开 writer 后再次登记（幂等）；不抛错、日志行仍在。"""
    s = _make_session(tmp_path)
    reg = _FakeRegistry()
    s.file_registry = reg
    await s.start()
    try:
        await s.detach()
        await s.attach()
        kinds = [r["kind"] for r in reg.records]
        assert kinds.count("chat_history") == 2  # start + attach 各一次
        assert kinds.count("chat_transcribe") == 2
        assert kinds.count("chat_translate") == 2
    finally:
        await s.finalize()


async def test_finalize_flushes_last_segment_without_touching_meta(tmp_path):
    """静态化（§3.A.4）：finalize 不再写/ touch session.json；收尾未满段照常登记。"""
    s = _make_session(tmp_path)
    reg = _FakeRegistry()
    s.file_registry = reg
    await s.start()
    s.on_session_config(audio_sample_rate=16000)
    s.on_media(kind="audio", data=b"\x00" * 20, duration_ms=20, ts_ms=0)
    await asyncio.sleep(0.05)
    await s.finalize()
    await asyncio.sleep(0.05)  # 让 bg 登记任务跑完
    assert not any(t["path"].name == "session.json" for t in reg.touches)
    assert [r["kind"] for r in reg.records].count("session") == 1  # 仅 start 首登记
    audio_recs = [r for r in reg.records if r["kind"] == "audio"]
    assert len(audio_recs) == 1  # 收尾未满段
    assert audio_recs[0]["duration_ms"] == 20


async def test_start_is_idempotent_existing_session_json_untouched(tmp_path):
    """server 重启后 acquire() 命中同会话再次 start()（§3.A.2a）：跳过写盘 +
    回读 started_at；登记照常（幂等 record）但不新增行、不 bump updated_at。"""
    import json
    import sqlite3

    from shared.persistence.chat_store import ChatStore

    db = str(tmp_path / "chat_server.db")
    store = ChatStore(db)
    await store.start()

    def _make() -> RecordingSession:
        s = _make_session(tmp_path)
        s.file_registry = RecordingFileRegistry(store, tmp_path, device_uuid="dev-1",
                                                recording_session_id="rec-1")
        return s

    s1 = _make()
    await s1.start()
    await s1.finalize()
    await store.drain()

    path = tmp_path / "rec-1" / "session.json"
    content = path.read_text()
    mtime = path.stat().st_mtime_ns

    # 模拟重启：新进程重建同一会话（同 recording_session_id / storage_path）
    s2 = _make()
    await s2.start()
    try:
        await store.drain()
        assert path.read_text() == content          # 未被覆盖
        assert path.stat().st_mtime_ns == mtime
        # 内存回读 = 盘上值（不是重启时刻的假值）
        assert s2._started_at_iso == json.loads(content)["started_at"]
    finally:
        await s2.finalize()
        await store.drain()
        await store.stop()

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT * FROM recording_files WHERE kind='session'"))
    conn.close()
    assert len(rows) == 1                      # 幂等 record：无新增行
    assert rows[0]["created_at"] == rows[0]["updated_at"]  # 无 bump
