"""SqliteJobStore 单元测试（chatserver_no_redis.md §3.5）。

覆盖：完整状态流转 CAS 语义、补投 48h 窗口谓词、watchdog 收敛、行映射
（_to_row/_row_to_record）、updated_at 单调性与防御子句、create 时
recording_session_id 解析（命中直填/未命中留 NULL/大小写归一/只读不创建）、
hermes_device_sessions 存取（UPSERT 覆盖 + 大小写归一）。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from agent_relay.job_store import (
    STATUS_DELIVERED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_REQUESTED,
    JobRecord,
)
from agent_relay.job_store_sqlite import SqliteJobStore
from shared.persistence.chat_store import ChatStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _db_path(tmp_path) -> str:
    return str(tmp_path / "chat_server.db")


def _init_db(db: str) -> None:
    """表 schema 由 ChatStore 统一管理（§3.5.1 单写者纪律）；SqliteJobStore
    不建表，测试侧按 ChatStore 语义幂等建（对齐 _init_db 的 WAL + schema）。"""
    from shared.persistence.chat_store import CHAT_STORE_SCHEMA_SQL
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(CHAT_STORE_SCHEMA_SQL)


def _store(tmp_path, **kwargs) -> SqliteJobStore:
    db = _db_path(tmp_path)
    _init_db(db)
    return SqliteJobStore(db, **kwargs)


def _conn(db: str):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


async def _chat_store(db: str) -> ChatStore:
    cs = ChatStore(db)
    await cs.start()
    return cs


def _row(db: str, job_key: str) -> dict | None:
    with _conn(db) as conn:
        row = conn.execute(
            "SELECT * FROM agent_jobs WHERE job_key = ?", (job_key,)).fetchone()
    return dict(row) if row else None


class TestLifecycle:
    async def test_create_running_done_delivered(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="查机票")
        assert job.status == STATUS_REQUESTED
        assert job.hermes_task_id == ""
        row = _row(_db_path(tmp_path), job.job_key)
        assert row["status"] == "requested"
        assert row["recording_session_id"] is None
        assert row["updated_at"] is not None

        assert await store.mark_running(job.job_key, "task-1")
        row = _row(_db_path(tmp_path), job.job_key)
        assert row["status"] == "running"
        assert row["hermes_task_id"] == "task-1"
        assert row["started_at"] is not None

        done = await store.mark_done(job.job_key, "往返机票已订好")
        assert done is not None and done.status == STATUS_DONE
        row = _row(_db_path(tmp_path), job.job_key)
        assert row["status"] == "done"
        assert row["result_text"] == "往返机票已订好"
        assert row["finished_at"] is not None
        # finished_at = completed_at（done 时刻）
        assert abs(_epoch_s(row["finished_at"]) - done.completed_at) < 0.001

        delivered = await store.mark_delivered(job.job_key)
        assert delivered is not None and delivered.status == STATUS_DELIVERED
        row = _row(_db_path(tmp_path), job.job_key)
        assert row["status"] == "delivered"
        assert row["delivered_at"] is not None
        # 幂等：已 delivered 返回现状
        again = await store.mark_delivered(job.job_key)
        assert again is not None and again.status == STATUS_DELIVERED

    async def test_cas_prevents_reverse_transition(self, tmp_path):
        """watchdog 与 job 完成事件竞态：running 被收敛为 failed 后，
        mark_done 不得回退（§3.5.2 CAS 等价语义）。"""
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        await store.mark_running(job.job_key, "task-1")
        await store.mark_failed(job.job_key, "timeout")
        done = await store.mark_done(job.job_key, "迟到结果")
        # 原语义：非 running 的 job 原样返回（不被迁移）
        assert done is not None and done.status == STATUS_FAILED
        row = _row(_db_path(tmp_path), job.job_key)
        assert row["status"] == "failed"
        assert row["result_text"] is None

    async def test_mark_running_cas_miss(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        assert await store.mark_running(job.job_key, "task-1")
        assert not await store.mark_running(job.job_key, "task-again")

    async def test_bind_external_ref_keeps_requested(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        assert await store.bind_external_ref(job.job_key, "session:stored-1")
        row = _row(_db_path(tmp_path), job.job_key)
        assert row["status"] == STATUS_REQUESTED
        assert row["hermes_task_id"] == "session:stored-1"
        # 已 running 后不可再 bind
        assert await store.mark_running(job.job_key, "session:stored-1")
        assert not await store.bind_external_ref(job.job_key, "session:other")
        row = _row(_db_path(tmp_path), job.job_key)
        assert row["status"] == "running"
        assert row["hermes_task_id"] == "session:stored-1"

    async def test_bind_external_ref_rejects_terminal_job(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        await store.mark_failed(job.job_key, "orphaned")
        assert not await store.bind_external_ref(job.job_key, "session:stored-1")
        assert _row(_db_path(tmp_path), job.job_key)["hermes_task_id"] is None

    async def test_watchdog_scan_stale(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-2", summary="s")
        stale = await store.scan_stale(
            time.time() + 1000, running_timeout_s=10, requested_timeout_s=10)
        assert stale and stale[0].job_key == job.job_key
        row = _row(_db_path(tmp_path), job.job_key)
        assert row["status"] == "failed"
        assert row["fail_reason"] == "orphaned"
        assert row["finished_at"] is not None  # D6：failed 派生 finished_at

    async def test_watchdog_scan_stale_agent_filter(self, tmp_path):
        """多 adapter：scan_stale(agent_name=...) 只收敛该归属的 job。"""
        store = _store(tmp_path)
        hermes_job = await store.create(
            session_id="s1", device_id="dev-1", summary="h", agent_name="hermes")
        oc_job = await store.create(
            session_id="s1", device_id="dev-1", summary="o", agent_name="openclaw")
        stale = await store.scan_stale(
            time.time() + 1000, running_timeout_s=10, requested_timeout_s=10,
            agent_name="openclaw")
        assert [j.job_key for j in stale] == [oc_job.job_key]
        assert _row(_db_path(tmp_path), hermes_job.job_key)["status"] == "requested"
        assert _row(_db_path(tmp_path), oc_job.job_key)["status"] == "failed"

    async def test_agent_name_roundtrip(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(
            session_id="s1", device_id="dev-1", summary="s", agent_name="openclaw")
        assert job.agent_name == "openclaw"
        fetched = await store.get(job.job_key)
        assert fetched.agent_name == "openclaw"

    async def test_find_by_task_id_and_queries(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        await store.mark_running(job.job_key, "task-x")
        assert (await store.find_by_task_id("task-x")).job_key == job.job_key
        assert await store.find_by_task_id("") is None
        assert await store.find_by_task_id("missing") is None
        assert job.job_key in await store.session_job_keys("s1")
        assert job.job_key in await store.device_job_keys("dev-1")
        assert await store.in_flight_count() == 1
        out = await store.outstanding_jobs("s1")
        assert out[0]["job_key"] == job.job_key
        assert out[0]["agent_name"] == "hermes"
        out = await store.outstanding_jobs_for_device("dev-1")
        assert out[0]["job_key"] == job.job_key
        assert out[0]["agent_name"] == "hermes"
        groups = await store.list_active_by_device()
        assert groups["dev-1"][0].job_key == job.job_key

    async def test_outstanding_jobs_carry_agent_name(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1",
                                 summary="s", agent_name="openclaw")
        await store.mark_running(job.job_key, "task-o")
        by_session = await store.outstanding_jobs("s1")
        by_device = await store.outstanding_jobs_for_device("dev-1")
        assert by_session[0]["agent_name"] == "openclaw"
        assert by_device[0]["agent_name"] == "openclaw"

    async def test_pending_delivery_48h_window(self, tmp_path):
        """done 未投递按 finished_at 升序；48h 窗口外（原 TTL 过期语义）不补投。"""
        store = _store(tmp_path)
        j_old = await store.create(session_id="s1", device_id="dev-3", summary="old")
        j_new = await store.create(session_id="s1", device_id="dev-3", summary="new")
        await store.mark_running(j_old.job_key, "t1")
        await store.mark_running(j_new.job_key, "t2")
        # 把 j_old 的 finished_at 改为 48h 外（模拟旧 TTL 过期）
        await store.mark_done(j_new.job_key, "新结果")
        with _conn(_db_path(tmp_path)) as conn:
            conn.execute(
                "UPDATE agent_jobs SET status='done', finished_at=?, updated_at=? "
                "WHERE job_key=?",
                ("2020-01-01T00:00:00.000Z", "2020-01-01T00:00:00.000Z", j_old.job_key))
        pend = await store.list_pending_delivery("dev-3")
        assert [r.job_key for r in pend] == [j_new.job_key]
        all_pend = await store.list_pending_delivery_all()
        assert [r.job_key for r in all_pend] == [j_new.job_key]


class TestHotPathSingleStatement:
    """§3.5.2 热路径修复：outstanding_jobs* / list_active_for_device 为单语句
    查询（一次连接），不做 device_job_keys + 逐 key get 的 N+1 扇出——D5 历史
    永久保留下全历史扇出随年限无界增长（单设备一年 ~10⁴ 行）。"""

    async def _seeded(self, tmp_path, n_history: int = 150):
        store = _store(tmp_path)
        for i in range(n_history):
            j = await store.create(session_id=f"old-{i}", device_id="dev-1",
                                   summary="s")
            await store.mark_running(j.job_key, f"t-{i}")
            await store.mark_done(j.job_key, "r")
        return store

    def _counting(self, store):
        calls = {"n": 0}
        orig = store._conn

        @contextmanager
        def counting_conn():
            calls["n"] += 1
            with orig() as conn:
                yield conn

        store._conn = counting_conn
        return calls

    async def test_outstanding_jobs_for_device_single_query(self, tmp_path):
        store = await self._seeded(tmp_path)
        active = await store.create(session_id="s1", device_id="dev-1", summary="a")
        await store.mark_running(active.job_key, "task-active")
        calls = self._counting(store)

        out = await store.outstanding_jobs_for_device("dev-1")
        assert [o["job_key"] for o in out] == [active.job_key]
        assert out[0]["task_id"] == "task-active"
        assert out[0]["agent_name"] == "hermes"
        assert calls["n"] == 1, f"单语句一次连接, got {calls['n']}"

    async def test_outstanding_jobs_session_single_query(self, tmp_path):
        store = await self._seeded(tmp_path)
        active = await store.create(session_id="sess-x", device_id="dev-2",
                                    summary="a")
        await store.mark_running(active.job_key, "task-x")
        calls = self._counting(store)

        out = await store.outstanding_jobs("sess-x")
        assert [o["job_key"] for o in out] == [active.job_key]
        assert out[0]["agent_name"] == "hermes"
        assert calls["n"] == 1

    async def test_list_active_for_device_single_query(self, tmp_path):
        store = await self._seeded(tmp_path)
        running = await store.create(session_id="s1", device_id="dev-1",
                                     summary="r")
        await store.mark_running(running.job_key, "task-r")
        requested = await store.create(session_id="s1", device_id="dev-1",
                                       summary="q")
        calls = self._counting(store)

        actives = await store.list_active_for_device("dev-1")
        assert {r.job_key for r in actives} == {running.job_key, requested.job_key}
        assert {r.status for r in actives} == {"running", "requested"}
        assert calls["n"] == 1

    async def test_list_active_for_device_excludes_done_history(self, tmp_path):
        """历史 done 行（n_history 条）不在 active 结果中。"""
        store = await self._seeded(tmp_path, n_history=50)
        actives = await store.list_active_for_device("dev-1")
        assert actives == []


class TestCreateWatermark:
    """§3.5.1 契约：create 的 updated_at 必须在 writer 线程内生成（与状态
    迁移一致）——事件循环线程取 time.time() 经 to_thread 调度乱序后可能出现
    「updated_at 更小但 commit 更晚」的行，scanner keyset 水位推进后永久漏扫。"""

    async def test_updated_at_writer_thread_and_row_consistent(self, tmp_path):
        store = _store(tmp_path)
        jobs = await asyncio.gather(*[
            store.create(session_id=f"s{i}", device_id="dev-1", summary=f"j{i}")
            for i in range(20)
        ])
        with _conn(_db_path(tmp_path)) as conn:
            rows = conn.execute(
                "SELECT job_key, updated_at, requested_at FROM agent_jobs "
                "ORDER BY updated_at, job_key").fetchall()
        assert len(rows) == 20
        # 返回对象 updated_at 与落库行一致（同步为 writer 线程值）
        for j in jobs:
            db_row = next(r for r in rows if r[0] == j.job_key)
            assert abs(_epoch_s(db_row[1]) - j.updated_at) < 0.001
        # keyset（updated_at, job_key）水位单调可全量遍历——无漏扫窗口
        wm_ts, wm_key = "", ""
        visited: list[str] = []
        while True:
            page = [r for r in rows if (r[1], r[0]) > (wm_ts, wm_key)][:5]
            if not page:
                break
            visited.extend(r[0] for r in page)
            wm_ts, wm_key = page[-1][1], page[-1][0]
        assert len(visited) == 20 and set(visited) == {j.job_key for j in jobs}


class TestGuard:
    async def test_guard_rejects_clock_regression(self, tmp_path):
        """防御子句：stored updated_at 已是未来（时钟回拨）→ rowcount=0 → cas_miss，
        状态不迁移。"""
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        with _conn(_db_path(tmp_path)) as conn:
            conn.execute(
                "UPDATE agent_jobs SET updated_at = ? WHERE job_key = ?",
                ("9999-01-01T00:00:00.000Z", job.job_key))
        assert not await store.mark_running(job.job_key, "t1")
        rec = await store.get(job.job_key)
        assert rec.status == STATUS_REQUESTED  # 未被迁移

    async def test_cas_failure_does_not_notify(self, tmp_path):
        """与 Redis 版语义对齐：CAS 失败（watchdog 已收敛/幂等）不触发
        listener、不打误导日志——mark_done 对 failed 行、mark_failed 对 done
        行、mark_delivered 幂等均静默返回现状。"""
        store = _store(tmp_path)
        events = {"change": 0, "record": 0}
        store.set_change_listener(
            lambda d: events.__setitem__("change", events["change"] + 1) or _noop())
        store.set_record_listener(
            lambda r: events.__setitem__("record", events["record"] + 1))

        # mark_done 对 failed 行：CAS 失败，返回现状，不 notify
        j = await store.create(session_id="s1", device_id="d1", summary="s")
        await store.mark_running(j.job_key, "t1")
        await store.mark_failed(j.job_key, "timeout")
        before = dict(events)
        rec = await store.mark_done(j.job_key, "迟到结果")
        assert rec.status == STATUS_FAILED
        assert dict(events) == before

        # mark_failed 对 done 行：CAS 失败，返回现状，不 notify
        j2 = await store.create(session_id="s2", device_id="d2", summary="s")
        await store.mark_running(j2.job_key, "t2")
        await store.mark_done(j2.job_key, "ok")
        before = dict(events)
        rec2 = await store.mark_failed(j2.job_key, "timeout")
        assert rec2.status == STATUS_DONE
        assert dict(events) == before

        # mark_delivered 幂等：首次 notify，幂等静默
        before = dict(events)
        await store.mark_delivered(j2.job_key)
        assert events["record"] == before["record"] + 1
        again = await store.mark_delivered(j2.job_key)
        assert again is not None and again.status == STATUS_DELIVERED
        assert dict(events)["record"] == before["record"] + 1

        # mark_delivered 非 done：返回 None
        j3 = await store.create(session_id="s3", device_id="d3", summary="s")
        assert await store.mark_delivered(j3.job_key) is None

    async def test_updated_at_monotonic_across_transitions(self, tmp_path):
        """每次迁移 updated_at 递增（scanner 水位契约依赖单调性）。"""
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        t_create = _row(_db_path(tmp_path), job.job_key)["updated_at"]
        await store.mark_running(job.job_key, "t1")
        t_running = _row(_db_path(tmp_path), job.job_key)["updated_at"]
        await store.mark_done(job.job_key, "r")
        t_done = _row(_db_path(tmp_path), job.job_key)["updated_at"]
        assert t_running > t_create and t_done > t_running


class TestRowMapping:
    def test_to_row_mapping(self, tmp_path):
        store = _store(tmp_path)
        rec = JobRecord(
            job_key="k1", hermes_task_id="", session_id="s1", device_id="d1",
            status=STATUS_REQUESTED, summary="summary",
            created_at=1000.0, updated_at=1000.0,
            recording_session_id="rs-9",
        )
        row = store._to_row(rec)
        assert row["job_key"] == "k1"
        assert row["agent_name"] == "hermes"
        assert row["device_uuid"] == "d1"
        assert row["recording_session_id"] == "rs-9"
        assert row["requested_at"] == "1970-01-01T00:16:40.000Z"
        assert row["requested_ts_ms"] == 1_000_000
        assert row["started_at"] is None and row["finished_at"] is None

    def test_done_finished_at_takes_completed_at(self, tmp_path):
        store = _store(tmp_path)
        rec = JobRecord(
            job_key="k1", hermes_task_id="t", session_id="s1", device_id="d1",
            status=STATUS_DONE, summary="s", result_text="结果",
            started_at=1100.0, completed_at=1200.0, updated_at=1200.0,
        )
        row = store._to_row(rec)
        assert row["finished_at"] == "1970-01-01T00:20:00.000Z"
        assert row["started_at"] == "1970-01-01T00:18:20.000Z"

    def test_failed_finished_at_takes_updated_at(self, tmp_path):
        """D6：failed 的 completed_at 恒 0，finished_at 取失败转换的 updated_at。"""
        store = _store(tmp_path)
        rec = JobRecord(
            job_key="k1", hermes_task_id="", session_id="s1", device_id="d1",
            status=STATUS_FAILED, summary="s", fail_reason="timeout",
            started_at=1100.0, completed_at=0.0, updated_at=1300.0,
        )
        row = store._to_row(rec)
        assert row["finished_at"] == "1970-01-01T00:21:40.000Z"
        assert row["fail_reason"] == "timeout"

    async def test_row_to_record_roundtrip(self, tmp_path):
        store = _store(tmp_path)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        await store.mark_running(job.job_key, "task-1")
        await store.mark_done(job.job_key, "结果")
        rec = await store.get(job.job_key)
        assert rec.device_id == "dev-1"
        assert rec.hermes_task_id == "task-1"
        assert rec.status == STATUS_DONE
        assert rec.completed_at > 0
        assert rec.updated_at >= rec.created_at
        assert abs(rec.completed_at - _epoch_s(_row(_db_path(tmp_path), job.job_key)["finished_at"])) < 0.001


class TestCreateRecordingLookup:
    async def test_lookup_hit_fills_rsession(self, tmp_path):
        """create 时命中（device_uuid, 当日 UTC）→ recording_session_id 直填。"""
        db = _db_path(tmp_path)
        cs = await _chat_store(db)
        today = datetime.now(tz=UTC).date().isoformat()
        await cs.get_or_create_recording_session(
            device_uuid="dev-1", session_date=today,
            recording_session_id="rs-1", base_path="/tmp/rec")
        store = _store(tmp_path, recording_lookup=cs.get_recording_session)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        assert job.recording_session_id == "rs-1"
        assert _row(_db_path(tmp_path), job.job_key)["recording_session_id"] == "rs-1"
        await cs.stop()

    async def test_lookup_miss_leaves_null(self, tmp_path):
        db = _db_path(tmp_path)
        cs = await _chat_store(db)
        store = _store(tmp_path, recording_lookup=cs.get_recording_session)
        job = await store.create(session_id="s1", device_id="no-rec-dev", summary="s")
        assert job.recording_session_id is None
        assert _row(_db_path(tmp_path), job.job_key)["recording_session_id"] is None
        await cs.stop()

    async def test_lookup_case_normalization(self, tmp_path):
        """写入小写、job device 大写（或反之）→ lower 归一命中。"""
        db = _db_path(tmp_path)
        cs = await _chat_store(db)
        today = datetime.now(tz=UTC).date().isoformat()
        await cs.get_or_create_recording_session(
            device_uuid="dev-upper", session_date=today,
            recording_session_id="rs-2", base_path="/tmp/rec")
        store = _store(tmp_path, recording_lookup=cs.get_recording_session)
        job = await store.create(session_id="s1", device_id="DEV-UPPER", summary="s")
        assert job.recording_session_id == "rs-2"
        await cs.stop()

    async def test_lookup_uppercase_write_normalized(self, tmp_path):
        """§3.5.3/§3.6 对称纪律：写入大写（iOS UUID().uuidString 形态）、读取
        小写命中；表内行已归一为小写；同设备大小写变体合并到同一行。"""
        db = _db_path(tmp_path)
        cs = await _chat_store(db)
        today = datetime.now(tz=UTC).date().isoformat()
        await cs.get_or_create_recording_session(
            device_uuid="ABCD-1234", session_date=today,
            recording_session_id="rs-upper", base_path="/tmp/rec")

        # 表内行归一为小写（写侧 lower，与读侧同一归一点）
        with _conn(db) as conn:
            row = conn.execute(
                "SELECT device_uuid FROM recording_sessions").fetchone()
            assert row[0] == "abcd-1234"

        # 小写读取命中（job create 路径，§3.5.3 基线行为不落空）
        store = _store(tmp_path, recording_lookup=cs.get_recording_session)
        job = await store.create(session_id="s1", device_id="abcd-1234", summary="s")
        assert job.recording_session_id == "rs-upper"
        # 大写读取同样命中（RecordingService.current_recording_info 兜底分支）
        row2 = await cs.get_recording_session(device_uuid="ABCD-1234", session_date=today)
        assert row2 is not None and row2["recording_session_id"] == "rs-upper"

        # 同设备大小写变体 → 合并到同一行（INSERT OR IGNORE 冲突，先插入者胜），
        # SELECT 同归一点不 miss
        await cs.get_or_create_recording_session(
            device_uuid="AbCd-1234", session_date=today,
            recording_session_id="rs-other", base_path="/tmp/rec2")
        row3 = await cs.get_recording_session(device_uuid="abcd-1234", session_date=today)
        assert row3 is not None
        assert row3["recording_session_id"] == "rs-upper"  # 先插入者胜
        with _conn(db) as conn:
            n = conn.execute("SELECT COUNT(*) FROM recording_sessions").fetchone()[0]
            assert n == 1
        await cs.stop()

    async def test_lookup_readonly_no_side_effect(self, tmp_path):
        """job create 不得副作用创建 recording 行（只读 SELECT，§3.5.3）。"""
        db = _db_path(tmp_path)
        cs = await _chat_store(db)
        store = _store(tmp_path, recording_lookup=cs.get_recording_session)
        await store.create(session_id="s1", device_id="new-dev", summary="s")
        with _conn(_db_path(tmp_path)) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM recording_sessions WHERE device_uuid='new-dev'"
            ).fetchone()[0]
        assert n == 0
        await cs.stop()

    async def test_lookup_error_does_not_block_create(self, tmp_path):
        """解析失败（异常）→ 留 NULL，不阻断 create。"""

        async def boom(**kw):
            raise RuntimeError("db down")

        store = _store(tmp_path, recording_lookup=boom)
        job = await store.create(session_id="s1", device_id="dev-1", summary="s")
        assert job.recording_session_id is None
        assert job.status == STATUS_REQUESTED


class TestHermesDeviceSessions:
    async def test_save_load_delete_roundtrip(self, tmp_path):
        db = _db_path(tmp_path)
        cs = await _chat_store(db)
        assert await cs.load_hermes_session("dev-1") is None
        await cs.save_hermes_session(
            device_uuid="dev-1", hermes_session_id="h1", stored_session_id="st1")
        m = await cs.load_hermes_session("dev-1")
        assert m["hermes_session_id"] == "h1"
        assert m["stored_session_id"] == "st1"
        assert m["updated_at"] is not None
        await cs.delete_hermes_session("dev-1")
        assert await cs.load_hermes_session("dev-1") is None
        await cs.stop()

    async def test_upsert_overwrites(self, tmp_path):
        db = _db_path(tmp_path)
        cs = await _chat_store(db)
        await cs.save_hermes_session(
            device_uuid="dev-1", hermes_session_id="h1", stored_session_id="st1")
        await cs.save_hermes_session(
            device_uuid="dev-1", hermes_session_id="h2", stored_session_id="st2")
        m = await cs.load_hermes_session("dev-1")
        assert m["hermes_session_id"] == "h2"
        assert m["stored_session_id"] == "st2"
        await cs.stop()

    async def test_case_normalization(self, tmp_path):
        """写入大写、读取小写（或反之）命中（§3.6 归一纪律）。"""
        db = _db_path(tmp_path)
        cs = await _chat_store(db)
        await cs.save_hermes_session(
            device_uuid="DEV-9", hermes_session_id="h1", stored_session_id="st1")
        assert (await cs.load_hermes_session("dev-9"))["hermes_session_id"] == "h1"
        await cs.save_hermes_session(
            device_uuid="dev-9", hermes_session_id="h2", stored_session_id="st2")
        assert (await cs.load_hermes_session("DEV-9"))["hermes_session_id"] == "h2"
        await cs.delete_hermes_session("DEV-9")
        assert await cs.load_hermes_session("dev-9") is None
        await cs.stop()


def _noop() -> None:
    return None


def _epoch_s(iso: str | None) -> float:
    if not iso:
        return 0.0
    dt = datetime.fromisoformat(iso)
    return dt.timestamp()
