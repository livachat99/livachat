"""SqliteJobStore 集成测试（chatserver_no_redis.md §3.5；替代原 fakeredis
JobStore + JobLvdataWriter 镜像链路）。

全链路终态正确性、watchdog 收敛、失败原因分布 SQL 冒烟（P0）、写后读
一致性（直写模式无「迁移后落盘前」崩溃窗口）、与 ChatStore 并存同库。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import UTC, datetime

import pytest

from agent_relay.job_store_sqlite import SqliteJobStore
from shared.persistence.chat_store import ChatStore

pytestmark = pytest.mark.integration


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def chat_store(tmp_path):
    cs = ChatStore(str(tmp_path / "chat_server.db"))
    await cs.start()
    yield cs
    await cs.stop()


def _db_path(tmp_path) -> str:
    return str(tmp_path / "chat_server.db")


def _fetch_row(db: str, job_key: str) -> dict | None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM agent_jobs WHERE job_key = ?", (job_key,)).fetchone()
    conn.close()
    return dict(row) if row else None


class TestFullLifecycle:
    async def test_create_running_done_delivered(self, chat_store, tmp_path):
        """全链路直写：每条迁移立即持久（写后读），无需队列排空等待。"""
        db = _db_path(tmp_path)
        store = SqliteJobStore(db)

        job = await store.create(session_id="s1", device_id="dev-1", summary="查机票")
        assert await store.mark_running(job.job_key, "task-1")
        done = await store.mark_done(job.job_key, "往返机票已订好")
        delivered = await store.mark_delivered(job.job_key)

        row = _fetch_row(db, job.job_key)
        assert row["status"] == "delivered"
        assert row["device_uuid"] == "dev-1"
        assert row["session_id"] == "s1"
        assert row["agent_name"] == "hermes"
        assert row["result_text"] == "往返机票已订好"
        assert row["summary"] == "查机票"
        assert row["finished_at"] is not None
        assert row["delivered_at"] is not None
        # finished_at = completed_at（done 时刻，非 delivered 时刻）
        assert (_epoch(row["finished_at"]) - _epoch(done.completed_at)).total_seconds() < 0.001
        assert (_epoch(row["delivered_at"]) - _epoch(delivered.delivered_at)).total_seconds() < 0.001

    async def test_watchdog_failed_row(self, chat_store, tmp_path):
        """watchdog 收敛：failed + fail_reason + 非空 finished_at（D6 P0）。"""
        db = _db_path(tmp_path)
        store = SqliteJobStore(db)
        job = await store.create(session_id="s1", device_id="dev-2", summary="s")
        stale = await store.scan_stale(
            time.time() + 1000, running_timeout_s=10, requested_timeout_s=10)
        assert stale and stale[0].job_key == job.job_key
        row = _fetch_row(db, job.job_key)
        assert row["status"] == "failed"
        assert row["fail_reason"] == "orphaned"
        assert row["finished_at"] is not None

    async def test_fail_reason_distribution_sql_smoke(self, chat_store, tmp_path):
        """③ §6 失败原因分布 SQL 冒烟（P0 回归）：failed 行必须出现在结果中——
        D6 派生（failed finished_at 非空）的直接验证。"""
        db = _db_path(tmp_path)
        store = SqliteJobStore(db)
        for i in range(2):
            job = await store.create(session_id="s1", device_id="dev-3", summary=f"s{i}")
            await store.mark_running(job.job_key, f"task-{i}")
            await store.mark_failed(job.job_key, "timeout")

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT fail_reason, count(*) AS n FROM agent_jobs "
            "WHERE status = 'failed' AND finished_at >= "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 days') "
            "GROUP BY 1 ORDER BY 2 DESC").fetchall()
        conn.close()
        assert rows, "failed 行必须出现在失败原因分布查询结果中（D6）"
        assert any(r[0] == "timeout" and r[1] >= 2 for r in rows)

    async def test_write_after_read_immediately_consistent(self, chat_store, tmp_path):
        """直写模式：mark_running 成功后立即 get 可见（无队列延迟）。"""
        db = _db_path(tmp_path)
        store = SqliteJobStore(db)
        job = await store.create(session_id="s1", device_id="dev-4", summary="s")
        await store.mark_running(job.job_key, "task-1")
        rec = await store.get(job.job_key)
        assert rec.status == "running" and rec.hermes_task_id == "task-1"
        # 与 ChatStore 并存：messages 写入互不干扰
        chat_store.on_message(
            device_uuid="dev-4", session_id="s1", kind="asr", role="user",
            content="hi", ts_ms=1000)
        await asyncio.sleep(0.05)
        assert _fetch_row(db, job.job_key)["status"] == "running"

    async def test_create_with_recording_lookup(self, chat_store, tmp_path):
        """create 时 rsession 解析（§3.5.3）：命中直填，未命中留 NULL。"""
        db = _db_path(tmp_path)
        today = datetime.now(tz=UTC).date().isoformat()
        await chat_store.get_or_create_recording_session(
            device_uuid="dev-5", session_date=today,
            recording_session_id="rs-5", base_path="/tmp/rec")
        store = SqliteJobStore(db, recording_lookup=chat_store.get_recording_session)
        hit = await store.create(session_id="s1", device_id="dev-5", summary="s")
        assert hit.recording_session_id == "rs-5"
        miss = await store.create(session_id="s2", device_id="no-rec", summary="s")
        assert miss.recording_session_id is None
        assert _fetch_row(db, miss.job_key)["recording_session_id"] is None


def _epoch(value: float | str | None) -> datetime:
    """epoch 秒或 SQLite 列（规范 ISO 字符串）→ aware datetime。"""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return datetime.min.replace(tzinfo=UTC)
