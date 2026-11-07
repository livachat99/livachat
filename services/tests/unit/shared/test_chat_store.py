"""ChatStore 单元测试（chatserver_sqlite_store.md §3.3/§3.5）。

用临时 SQLite 库验证：fail-fast 启动各分支、同步钩子写路径、队列背压、
prune cutoff（时区换算）、on_message 随行挂图、load_day 时区换算与顺序、
recording get-or-create 幂等、stop 排空。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from shared.persistence.chat_store import ChatStore, local_day_ms_range
from shared.utils.time import now_ms

# 固定的"昨天"时间戳（1970-01-02 00:00:00 UTC），必在任一"今天"之前
_OLD_TS_MS = 86_400_000


@pytest.fixture(autouse=True)
def _clear_metrics():
    import shared.observability.metrics as m

    m._registry = None


def _metric_value(result: str) -> int:
    from shared.observability.metrics import get_registry

    counter = get_registry()._metrics["livachat_chat_store_total"]
    return int(counter.labels(result=result)._value.get())


def _db(tmp_path) -> str:
    return str(tmp_path / "chat_server.db")


def _rows(db: str, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def _messages(db: str) -> list[sqlite3.Row]:
    return _rows(db, "SELECT * FROM messages ORDER BY id")


# ---------------------------------------------------------------------------
# fail-fast 启动（§3.1）
# ---------------------------------------------------------------------------


class TestFailFast:
    async def test_empty_path_raises(self):
        store = ChatStore("")
        with pytest.raises(ValueError):
            await store.start()

    async def test_missing_parent_dir_raises(self):
        store = ChatStore("/nonexistent-dir-xyz/chat_server.db")
        with pytest.raises(FileNotFoundError):
            await store.start()

    async def test_start_creates_schema(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        await store.stop()
        tables = {
            r[0] for r in _rows(db, "SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"devices", "messages", "recording_sessions"} <= tables
        # 契约 2：WAL 是库级属性（init 时设置一次）
        journal = _rows(db, "PRAGMA journal_mode")
        assert journal[0][0] == "wal"


# ---------------------------------------------------------------------------
# 同步钩子写路径
# ---------------------------------------------------------------------------


class TestSyncHooks:
    async def test_on_device_upsert(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        store.on_device(device_uuid="dev-1", user_id="u1",
                        client_version="android-1", timezone="Asia/Shanghai")
        await store._queue.join()
        store.on_device(device_uuid="dev-1", user_id="u2",
                        client_version="android-2", timezone="Asia/Shanghai")
        await store._queue.join()
        await store.stop()
        rows = _rows(db, "SELECT * FROM devices")
        assert len(rows) == 1  # upsert 不产生重复行
        assert rows[0]["user_id"] == "u2"
        assert rows[0]["client_version"] == "android-2"
        assert rows[0]["timezone"] == "Asia/Shanghai"

    async def test_on_message_inserts_row(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        ts = now_ms()
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="你好", ts_ms=ts)
        await store._queue.join()
        await store.stop()
        rows = _messages(db)
        assert len(rows) == 1
        r = rows[0]
        assert r["kind"] == "asr" and r["role"] == "user"
        assert r["content"] == "你好"
        assert r["ts_ms"] == ts
        # 契约 4：规范时间戳（'T'、毫秒、'Z'）
        assert r["utc"].endswith("Z") and "T" in r["utc"]
        assert r["utc"] == datetime.fromtimestamp(ts / 1000, tz=UTC) \
            .isoformat(timespec="milliseconds").replace("+00:00", "Z")

    async def test_queue_full_drops_with_metric(self, tmp_path):
        store = ChatStore(_db(tmp_path), max_queue=1)
        await store.start()
        store.on_message(device_uuid="d", session_id="s", kind="asr",
                         role="user", content="1", ts_ms=now_ms())
        store.on_message(device_uuid="d", session_id="s", kind="asr",
                         role="user", content="2", ts_ms=now_ms())
        assert _metric_value("queue_dropped") == 1
        await store._queue.join()
        await store.stop()

    async def test_hooks_never_raise_when_stopped(self, tmp_path):
        """start 前调用钩子（热路径永远可调）→ 入队不抛。"""
        store = ChatStore(_db(tmp_path))
        store.on_device(device_uuid="d", user_id=None, client_version="",
                        timezone="")
        store.on_message(device_uuid="d", session_id="s", kind="asr",
                         role="user", content="x", ts_ms=now_ms())
        assert store._queue.qsize() == 2


# ---------------------------------------------------------------------------
# on_message 随行挂图（fd_pipeline_llm_v2：INSERT 即含 images_json）
# ---------------------------------------------------------------------------


class TestMessageImages:
    async def test_message_insert_carries_images_json(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="u1", ts_ms=now_ms(),
                         images=["data:image/jpeg;base64,AAA"])
        store.on_message(device_uuid="dev-1", session_id="s1", kind="llm",
                         role="assistant", content="a1", ref_id="m1", ts_ms=now_ms())
        await store._queue.join()
        await store.stop()
        rows = _messages(db)
        assert rows[0]["images_json"] == '["data:image/jpeg;base64,AAA"]'
        assert rows[1]["images_json"] is None  # 无图行images_json 为 NULL

    async def test_message_without_images_is_null(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="u1", ts_ms=now_ms())
        await store._queue.join()
        await store.stop()
        assert _messages(db)[0]["images_json"] is None


# ---------------------------------------------------------------------------
# message_delete（fd_pipeline_llm_v2.md：4xx 回滚本轮 user 记录）
# ---------------------------------------------------------------------------


class TestMessageDelete:
    async def test_delete_after_insert_removes_row(self, tmp_path):
        """FIFO 保序：insert 后 delete → 行（含图）消失，其它行不受影响。"""
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        ts = now_ms()
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="u1", ts_ms=ts,
                         images=["data:image/jpeg;base64,AAA"])
        store.on_message(device_uuid="dev-1", session_id="s1", kind="llm",
                         role="assistant", content="a1", ts_ms=ts + 1)
        store.delete_message(device_uuid="dev-1", session_id="s1", kind="asr",
                             role="user", ts_ms=ts)
        await store._queue.join()
        await store.stop()
        assert [r["content"] for r in _messages(db)] == ["a1"]

    async def test_delete_without_insert_is_noop(self, tmp_path):
        """insert 被丢弃/未发生：精确匹配删 0 行，幂等无副作用。"""
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        store.delete_message(device_uuid="dev-1", session_id="s1", kind="asr",
                             role="user", ts_ms=now_ms())
        await store._queue.join()
        await store.stop()
        assert _messages(db) == []

    async def test_delete_matches_exact_5tuple(self, tmp_path):
        """不同 ts / session / kind+role 的行不受影响。"""
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        ts = now_ms()
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="target", ts_ms=ts)
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="other-ts", ts_ms=ts + 1)
        store.on_message(device_uuid="dev-1", session_id="s2", kind="asr",
                         role="user", content="other-session", ts_ms=ts)
        store.on_message(device_uuid="dev-1", session_id="s1", kind="llm",
                         role="assistant", content="other-kind-role", ts_ms=ts)
        store.delete_message(device_uuid="dev-1", session_id="s1", kind="asr",
                             role="user", ts_ms=ts)
        await store._queue.join()
        await store.stop()
        contents = [r["content"] for r in _messages(db)]
        assert contents == ["other-ts", "other-session", "other-kind-role"]


# ---------------------------------------------------------------------------
# prune（§3.5）
# ---------------------------------------------------------------------------


class TestPrune:
    async def _seed(self, db: str, store: ChatStore, *, yesterday: int = 1) -> None:
        for i in range(yesterday):
            store.on_message(device_uuid="dev-1", session_id="s0", kind="asr",
                             role="user", content=f"old{i}",
                             ts_ms=_OLD_TS_MS + i * 1000)
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="new", ts_ms=now_ms())
        await store._queue.join()

    async def test_prune_removes_before_local_midnight_utc(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        await self._seed(db, store)
        store._prune()  # _last_tz='' → UTC 今天
        await store._queue.join()
        rows = _messages(db)
        assert [r["content"] for r in rows] == ["new"]

    async def test_prune_keeps_yesterday_local_in_positive_tz(self, tmp_path):
        """Asia/Shanghai 时区下：切点 = 上海今天 00:00；上海今天 00:01 保留、
        上海昨天 23:59 删除。相对**本地**日起点 ±1min 构造——跨午夜时刻稳定
        （原实现用 UTC 日推"上海今天 01:00"，UTC≥16:00（沪已跨日）后恒失败）。"""
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        store.on_device(device_uuid="dev-1", user_id=None, client_version="",
                        timezone="Asia/Shanghai")
        sh_start, _ = local_day_ms_range("Asia/Shanghai")
        keep_ts = sh_start + 60_000  # 上海今天 00:01
        drop_ts = sh_start - 60_000  # 上海昨天 23:59
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="keep", ts_ms=keep_ts)
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="drop", ts_ms=drop_ts)
        await store._queue.join()
        store._prune()
        await store._queue.join()
        rows = _messages(db)
        assert [r["content"] for r in rows] == ["keep"]

    async def test_prune_error_isolated(self, tmp_path, monkeypatch):
        """prune 失败只 log + metric，不抛出（best-effort）。"""
        store = ChatStore(_db(tmp_path))
        await store.start()
        monkeypatch.setattr(store, "_db_path", "/nonexistent/chat.db")
        store._prune()  # 不抛
        assert _metric_value("prune_error") == 1
        await store.stop()


# ---------------------------------------------------------------------------
# load_day（§3.3/§3.6）
# ---------------------------------------------------------------------------


class TestLoadDay:
    async def test_timezone_day_window(self, tmp_path):
        """各时区相对**自己的**本地日 00:00 构造：+1min 属今天、-1min 属昨天——
        跨午夜/墙钟时刻稳定（原实现用 UTC 日推"上海今天 01:00"，UTC≥16:00
        （沪已跨日）后恒失败）。"""
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        sh_start, _ = local_day_ms_range("Asia/Shanghai")
        utc_start, _ = local_day_ms_range("UTC")
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="sh_in", ts_ms=sh_start + 60_000)
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="sh_out", ts_ms=sh_start - 60_000)
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="utc_in", ts_ms=utc_start + 60_000)
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="utc_out", ts_ms=utc_start - 60_000)
        await store._queue.join()
        await store.stop()
        rows = store.load_day(device_uuid="dev-1", timezone="Asia/Shanghai", limit=20)
        contents = [r["content"] for r in rows]
        assert "sh_in" in contents and "sh_out" not in contents
        rows_utc = store.load_day(device_uuid="dev-1", timezone="UTC", limit=20)
        contents_utc = [r["content"] for r in rows_utc]
        assert "utc_in" in contents_utc and "utc_out" not in contents_utc

    async def test_load_day_returns_latest_n_ascending(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        for i in range(5):
            store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                             role="user", content=f"m{i}", ts_ms=now_ms())
        await store._queue.join()
        await store.stop()
        rows = store.load_day(device_uuid="dev-1", timezone="", limit=3)
        assert [r["content"] for r in rows] == ["m2", "m3", "m4"]  # 最新 3 条、升序

    async def test_load_day_returns_images(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="u", ts_ms=now_ms(),
                         images=["data:image/jpeg;base64,AAA"])
        await store._queue.join()
        await store.stop()
        rows = store.load_day(device_uuid="dev-1", timezone="", limit=20)
        assert rows[0]["images"] == ["data:image/jpeg;base64,AAA"]


class TestLocalDayRange:
    def test_utc_fallback(self):
        start_ms, end_ms = local_day_ms_range("")
        assert end_ms - start_ms == 24 * 3600 * 1000
        now = datetime.now(tz=UTC)
        assert start_ms <= int(now.timestamp() * 1000) < end_ms

    def test_fixed_offset(self):
        start_ms, _end_ms = local_day_ms_range("+08:00")
        start_utc = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
        assert start_utc.hour == 16  # 上海/东八区今天起点 = UTC 昨天 16:00

    def test_invalid_falls_back_utc(self):
        start_a, _ = local_day_ms_range("Not/AZone")
        start_b, _ = local_day_ms_range("")
        assert start_a == start_b


# ---------------------------------------------------------------------------
# recording_sessions（§4.3）
# ---------------------------------------------------------------------------


class TestRecordingSessions:
    async def test_get_or_create_idempotent(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        r1 = await store.get_or_create_recording_session(
            device_uuid="dev-1", session_date="2026-08-31",
            recording_session_id="uuid-1", base_path="/base/dev-1/uuid-1",
        )
        assert r1["recording_session_id"] == "uuid-1"
        # 同日再调：返回既有行（INSERT OR IGNORE），不覆盖
        r2 = await store.get_or_create_recording_session(
            device_uuid="dev-1", session_date="2026-08-31",
            recording_session_id="uuid-2", base_path="/base/dev-1/uuid-2",
        )
        assert r2["recording_session_id"] == "uuid-1"
        assert r2["base_path"] == "/base/dev-1/uuid-1"
        # 次日：新行
        r3 = await store.get_or_create_recording_session(
            device_uuid="dev-1", session_date="2026-09-01",
            recording_session_id="uuid-3", base_path="/base/dev-1/uuid-3",
        )
        assert r3["recording_session_id"] == "uuid-3"
        await store.stop()
        rows = _rows(db, "SELECT * FROM recording_sessions ORDER BY session_date")
        assert len(rows) == 2

    async def test_get_recording_session_missing_returns_none(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        assert await store.get_recording_session(
            device_uuid="dev-1", session_date="2026-08-31") is None
        await store.stop()


# ---------------------------------------------------------------------------
# stop 排空
# ---------------------------------------------------------------------------


class TestStop:
    async def test_stop_drains_queue(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        for i in range(3):
            store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                             role="user", content=f"m{i}", ts_ms=now_ms())
        await store.stop()  # 排空后退出
        assert len(_messages(db)) == 3

    async def test_writer_error_isolated(self, tmp_path, monkeypatch):
        """单条写失败不中断 writer；指标计数（运行时 best-effort）。"""
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        orig = store._apply
        calls = {"n": 0}

        def flaky(item):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return orig(item)

        monkeypatch.setattr(store, "_apply", flaky)
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="1", ts_ms=now_ms())
        store.on_message(device_uuid="dev-1", session_id="s1", kind="asr",
                         role="user", content="2", ts_ms=now_ms())
        await store._queue.join()
        await store.stop()
        assert _metric_value("write_error") == 1
        assert len(_messages(db)) == 1  # 第二条正常落库


# ---------------------------------------------------------------------------
# recording_files 登记（recording_data_process.md §2.1）
# ---------------------------------------------------------------------------


def _recording_files(db: str) -> list[sqlite3.Row]:
    return _rows(db, "SELECT * FROM recording_files ORDER BY file_id")


class TestRecordingFiles:
    async def test_record_file_inserts_canonical_row(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        await store.record_file(
            device_uuid="dev-1", recording_session_id="rs-1", kind="audio",
            rel_path="dev-1/rs-1/audio/seg1.ogg", size_bytes=123,
            ts_ms=1_700_000_000_000, duration_ms=60_000)
        await store._queue.join()
        await store.stop()
        rows = _recording_files(db)
        assert len(rows) == 1
        r = rows[0]
        assert r["kind"] == "audio" and r["size_bytes"] == 123
        assert r["duration_ms"] == 60_000 and r["ts_ms"] == 1_700_000_000_000
        # 契约 4：规范时间戳（'T'、毫秒、'Z'）
        for ts in (r["created_at"], r["updated_at"]):
            assert "T" in ts and ts.endswith("Z") and "." in ts

    async def test_record_file_duplicate_rel_path_idempotent(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        base = dict(device_uuid="dev-1", kind="video",
                    rel_path="dev-1/rs-1/video/f1.jpg", ts_ms=1_700_000_000_000)
        await store.record_file(**base, recording_session_id="rs-1", size_bytes=10)
        await store.record_file(**base, recording_session_id="rs-2", size_bytes=999)
        await store._queue.join()
        await store.stop()
        rows = _recording_files(db)
        assert len(rows) == 1
        # INSERT OR IGNORE：首行原样保留
        assert rows[0]["size_bytes"] == 10
        assert rows[0]["recording_session_id"] == "rs-1"

    async def test_record_file_unknown_kind_raises(self, tmp_path):
        store = ChatStore(_db(tmp_path))
        with pytest.raises(ValueError):
            await store.record_file(
                device_uuid="d", recording_session_id="rs", kind="image",
                rel_path="d/rs/image/i.jpg", size_bytes=1)

    async def test_touch_file_updates_size_and_bumps_monotonic(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        rel = "dev-1/rs-1/chat/history.jsonl"
        await store.record_file(device_uuid="dev-1", recording_session_id="rs-1",
                                kind="chat_history", rel_path=rel, size_bytes=1)
        await store._queue.join()
        first = _recording_files(db)[0]["updated_at"]
        # 连续两次 append（时钟毫秒可能不变）→ updated_at 必须严格递增
        await store.touch_file(rel_path=rel, size_bytes=2)
        await store._queue.join()
        second = _recording_files(db)[0]["updated_at"]
        await store.touch_file(rel_path=rel, size_bytes=3)
        await store._queue.join()
        await store.stop()
        rows = _recording_files(db)
        assert rows[0]["size_bytes"] == 3
        assert first < second < rows[0]["updated_at"]

    async def test_touch_file_missing_row_warns_without_crash(self, tmp_path):
        db = _db(tmp_path)
        store = ChatStore(db)
        await store.start()
        await store.touch_file(rel_path="dev-1/rs-1/chat/none.jsonl", size_bytes=5)
        await store._queue.join()
        await store.stop()
        assert _recording_files(db) == []
        assert _metric_value("touch_missing_row") == 1

    async def test_enqueue_timeout_raises_with_metric(self, tmp_path):
        """writer 未运行（队列无消费者）→ 登记阻塞入队超时抛错 + 指标。"""
        store = ChatStore(_db(tmp_path), max_queue=1)
        base = dict(device_uuid="d", recording_session_id="rs", kind="audio",
                    size_bytes=1, enqueue_timeout_s=0.01)
        await store.record_file(**base, rel_path="d/rs/audio/a.ogg")  # 占满队列
        with pytest.raises(TimeoutError):
            await store.record_file(**base, rel_path="d/rs/audio/b.ogg")
        assert _metric_value("enqueue_timeout") == 1
