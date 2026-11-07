"""SqliteJobStore — SQLite-backed JobStore（chatserver_no_redis.md §3.5）。

与 Redis 版 JobStore（job_store.py）完全同接口：同名方法、同 JobRecord
dataclass、同状态常量（从 job_store.py import 复用），实现换成
chat_server.db `agent_jobs` 表直读直写。调用方除构造注入外零改动。

工程契约：
1. 每次操作 sqlite3.connect(timeout=10) + busy_timeout=5000，经 asyncio.to_thread；
   不走队列 writer —— job 迁移链路存在写后读（mark_running 后 get、
   deliver 内 re-read），直写模式无「迁移后落盘前」崩溃窗口；
2. updated_at 在 writer 线程内、执行 SQL 前生成（消除 to_thread 调度乱序
   导致的时间戳回退）；每次迁移置为规范当前时间——scanner 水位契约依赖
   其单调性；
3. 状态迁移 SQL 带防御性子句 `AND updated_at <= ?`（参数即本次写入时间
   戳）：正常路径恒真；时间戳回退时迁移以 rowcount=0 失败（cas_miss +
   warning），把「水位契约被静默破坏」转换为显式失败；
4. 行 ↔ JobRecord 映射复用 job_persistence._to_row 逻辑（recording_session_id
   改取 JobRecord 字段）；时间戳一律规范格式（ISO 8601 UTC、'T'、毫秒、'Z'）。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from agent_relay.job_store import (
    ACTIVE_STATUSES,
    JOB_SUMMARY_MAX_CHARS,
    STATUS_DELIVERED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_REQUESTED,
    STATUS_RUNNING,
    JobRecord,
)
from agent_relay.metrics import inc_job_store

logger = logging.getLogger("livachat.agent_relay.job_store_sqlite")

_BUSY_TIMEOUT_MS = 5000
_DAY_S = 24 * 3600

_INSERT_SQL = """
INSERT INTO agent_jobs (job_key, agent_name, device_uuid, session_id,
    recording_session_id, hermes_task_id, status, summary, result_text,
    fail_reason, requested_ts_ms, requested_at, started_at, finished_at,
    delivered_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_BIND_REF_SQL = """
UPDATE agent_jobs SET hermes_task_id = ?, updated_at = ?
WHERE job_key = ? AND status = ? AND updated_at <= ?
"""

_MARK_RUNNING_SQL = """
UPDATE agent_jobs SET status = ?, hermes_task_id = ?, started_at = ?,
    updated_at = ?
WHERE job_key = ? AND status = ? AND updated_at <= ?
"""

_MARK_DONE_SQL = """
UPDATE agent_jobs SET status = ?, result_text = ?, finished_at = ?,
    updated_at = ?
WHERE job_key = ? AND status = ? AND updated_at <= ?
"""

_MARK_DELIVERED_SQL = """
UPDATE agent_jobs SET status = ?, delivered_at = ?, updated_at = ?
WHERE job_key = ? AND status = ? AND updated_at <= ?
"""

_FINISH_SQL = """
UPDATE agent_jobs SET status = ?, fail_reason = ?, finished_at = ?,
    updated_at = ?
WHERE job_key = ? AND status IN ('requested', 'running') AND updated_at <= ?
"""

_SELECT_SQL = "SELECT * FROM agent_jobs WHERE job_key = ?"

_SELECT_BY_TASK_SQL = (
    "SELECT * FROM agent_jobs WHERE hermes_task_id = ? LIMIT 1"
)

_ACTIVE_STATUSES_CLAUSE = "status IN ('requested', 'running')"


def _dt(epoch_s: float) -> str | None:
    """epoch 秒 → 规范时间戳（'T'、毫秒、'Z'）；0（未设置）→ None。"""
    if epoch_s <= 0:
        return None
    return (datetime.fromtimestamp(epoch_s, tz=UTC)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _dt_now_ms() -> str:
    """当前 UTC 规范时间戳（updated_at 的源；writer 线程内生成）。"""
    return (datetime.now(tz=UTC)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _epoch_s(iso: str | None) -> float:
    """规范 ISO 时间戳 → epoch 秒；空 → 0（与 JobRecord 的 0 = 未设置 对齐）。"""
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except ValueError:
        return 0.0


class SqliteJobStore:
    """chat_server.db agent_jobs 表直读直写的 JobStore（§3.5）。"""

    def __init__(
        self,
        db_path: str,
        *,
        max_result_bytes: int = 16384,
        recording_lookup: Callable[..., Awaitable[dict | None]] | None = None,
    ) -> None:
        """recording_lookup(device_uuid=..., session_date=...) → {recording_session_id,
        base_path} | None（ChatStore.get_recording_session 注入；create 时尽力
        解析 recording_session_id，未命中留 NULL 由 lvdata-mcp enrich 兜底）。"""
        self._db_path = str(db_path)
        self._max_result_bytes = max_result_bytes
        self._recording_lookup = recording_lookup
        self._change_listener: Callable[[str], Awaitable[None]] | None = None
        self._record_listener: Callable[[JobRecord], None] | None = None

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """连接生命周期：`with conn` 负责事务提交/回滚，finally 显式 close。

        原 `with self._connect() as conn` 只提交事务不关连接，关闭依赖 GC；
        scan 高频（每 ~3s）开连接下对象滞留可耗尽进程 fd（Errno 24）。
        """
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn
        finally:
            conn.close()

    # -- change notification（与 JobStore 同契约）-----------------------------

    def set_change_listener(
        self, cb: Callable[[str], Awaitable[None]] | None
    ) -> None:
        self._change_listener = cb

    def _notify_change(self, device_id: str) -> None:
        if self._change_listener is None:
            return
        try:
            task = asyncio.create_task(
                self._change_listener(device_id),
                name=f"job-store-change-{device_id}",
            )
        except Exception:
            logger.warning("job status change callback failed", exc_info=True)
            return
        task.add_done_callback(lambda t: self._on_change_task_done(t, device_id))

    def _on_change_task_done(self, task: asyncio.Task, device_id: str) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.warning(
                "job status change callback raised",
                extra={"device_id": device_id},
                exc_info=exc,
            )

    def set_record_listener(self, cb: Callable[[JobRecord], None] | None) -> None:
        self._record_listener = cb

    def _notify_record(self, record: JobRecord | None) -> None:
        if self._record_listener is None or record is None:
            return
        try:
            self._record_listener(record)
        except Exception:
            logger.warning("job record listener raised", exc_info=True)

    # -- state transitions ----------------------------------------------------

    async def create(
        self,
        *,
        session_id: str,
        device_id: str,
        summary: str,
        agent_name: str = "hermes",
    ) -> JobRecord:
        """Create a job in status=requested. Returns the new record.

        create 时尽力解析 recording_session_id（§3.5.3 基线行为）：以
        (device_uuid, 当日 UTC) 查 recording_sessions 权威行，命中直填、
        未命中留 NULL（enrich 兜底全路径保留）。解析失败不重试、不阻断。
        """
        now = time.time()
        job = JobRecord(
            job_key=uuid.uuid4().hex,
            hermes_task_id="",
            session_id=session_id,
            device_id=device_id,
            status=STATUS_REQUESTED,
            summary=(summary or "")[:JOB_SUMMARY_MAX_CHARS],
            agent_name=agent_name,
            created_at=now,
            updated_at=now,
        )
        if self._recording_lookup is not None:
            try:
                today = datetime.now(tz=UTC).date().isoformat()
                row = await self._recording_lookup(
                    device_uuid=device_id, session_date=today
                )
                if row is not None:
                    job.recording_session_id = row.get("recording_session_id")
            except Exception:
                logger.warning(
                    "recording lookup failed on job create, leaving NULL",
                    extra={"job_key": job.job_key},
                    exc_info=True,
                )
        await asyncio.to_thread(self._create_sync, job)
        logger.info(
            "job created",
            extra={
                "job_key": job.job_key,
                "session_id": session_id,
                "recording_session_id": job.recording_session_id,
            },
        )
        self._notify_record(job)
        return job

    def _create_sync(self, job: JobRecord) -> None:
        row = self._to_row(job)
        # §3.5.1 契约（create 侧补齐）：updated_at 必须在 writer 线程内、执行
        # SQL 前生成——两个近并发 create 经 to_thread 调度乱序后若沿用事件
        # 循环线程的调用时刻时间戳，可能出现「updated_at 更小但 commit 更晚」
        # 的行，scanner keyset 水位推进后永久漏扫（与状态迁移路径消除的是
        # 同一类 hazard）。requested_at/requested_ts_ms 保持调用时刻语义
        # （created_at 派生）不动；job.updated_at 同步为落库值，返回对象与
        # DB 行一致。
        row["updated_at"] = _dt_now_ms()
        job.updated_at = _epoch_s(row["updated_at"])
        try:
            with self._conn() as conn:
                conn.execute(_INSERT_SQL, (

                    row["job_key"], row["agent_name"], row["device_uuid"],
                    row["session_id"], row["recording_session_id"],
                    row["hermes_task_id"], row["status"], row["summary"],
                    row["result_text"], row["fail_reason"],
                    row["requested_ts_ms"], row["requested_at"],
                    row["started_at"], row["finished_at"],
                    row["delivered_at"], row["updated_at"],
                ))
            inc_job_store("written")
        except Exception:
            inc_job_store("error")
            raise

    async def bind_external_ref(self, job_key: str, external_ref: str) -> bool:
        """requested 状态下写外部关联键（不改状态）；CAS 失败返回 False。

        v3 job 会话：在首个 prompt.submit 前持久化 stored_session_id，供 relay
        崩溃后的启动恢复使用。无 DDL（复用 hermes_task_id 列/索引）。"""
        ok = await asyncio.to_thread(
            self._bind_external_ref_sync, job_key, external_ref
        )
        if not ok:
            return False
        record = await self.get(job_key)
        if record is not None:
            self._notify_record(record)
        return True

    def _bind_external_ref_sync(self, job_key: str, external_ref: str) -> bool:
        now_iso = _dt_now_ms()
        with self._conn() as conn:
            cur = conn.execute(_BIND_REF_SQL, (
                external_ref or None, now_iso, job_key, STATUS_REQUESTED, now_iso,
            ))
            if cur.rowcount == 1:
                inc_job_store("written")
                return True
        inc_job_store("cas_miss")
        return False

    async def mark_running(self, job_key: str, hermes_task_id: str) -> bool:
        """requested→running（CAS）。False = 已被 watchdog 收敛或防御子句触发，
        调用方应放弃任务。"""
        ok = await asyncio.to_thread(
            self._mark_running_sync, job_key, hermes_task_id
        )
        if not ok:
            return False
        record = await self.get(job_key)
        if record is not None:
            self._notify_change(record.device_id)
            self._notify_record(record)
        return True

    def _mark_running_sync(self, job_key: str, hermes_task_id: str) -> bool:
        now_iso = _dt_now_ms()
        with self._conn() as conn:
            cur = conn.execute(_MARK_RUNNING_SQL, (
                STATUS_RUNNING, hermes_task_id or None, now_iso, now_iso,
                job_key, STATUS_REQUESTED, now_iso,
            ))
            if cur.rowcount == 1:
                inc_job_store("written")
                return True
            row = conn.execute(
                "SELECT status, updated_at FROM agent_jobs WHERE job_key = ?",
                (job_key,),
            ).fetchone()
        if row is None:
            inc_job_store("cas_miss")
            return False
        if row[0] == STATUS_REQUESTED:
            # 状态未变但迁移失败 → 防御子句（updated_at <= ?）触发：时间戳回退
            inc_job_store("cas_miss")
            logger.warning(
                "job state transition guard triggered (updated_at regression?)",
                extra={"job_key": job_key, "stored_updated_at": row[1]},
            )
        else:
            inc_job_store("cas_miss")
        return False

    async def _finish(self, job_key: str, status: str, fail_reason: str = "") -> JobRecord | None:
        """active → failed（CAS）。非 active job（done/delivered）不受影响。
        仅 CAS 成功（rowcount=1）触发 listener——对齐 Redis 版（ok!=1 时
        返回现状但不通知）。"""
        record, transitioned = await asyncio.to_thread(
            self._finish_sync, job_key, status, fail_reason
        )
        if record is not None and transitioned:
            self._notify_change(record.device_id)
            self._notify_record(record)
        return record

    def _finish_sync(self, job_key: str, status: str, fail_reason: str) -> tuple[JobRecord | None, bool]:
        now_iso = _dt_now_ms()
        with self._conn() as conn:
            cur = conn.execute(_FINISH_SQL, (
                status, fail_reason, now_iso, now_iso, job_key, now_iso,
            ))
            if cur.rowcount == 1:
                inc_job_store("written")
                transitioned = True
            else:
                inc_job_store("cas_miss")
                transitioned = False
                row = conn.execute(
                    "SELECT status, updated_at FROM agent_jobs WHERE job_key = ?",
                    (job_key,),
                ).fetchone()
                if row is not None and row[0] in ("requested", "running"):
                    logger.warning(
                        "job state transition guard triggered (updated_at regression?)",
                        extra={"job_key": job_key, "stored_updated_at": row[1]},
                    )
            row = conn.execute(_SELECT_SQL, (job_key,)).fetchone()
        record = self._row_to_record(row) if row is not None else None
        return record, transitioned

    async def mark_done(self, job_key: str, result_text: str = "") -> JobRecord | None:
        """running → done（CAS）。存储截断的 result_text 与 completed_at
        （补投排序键）。返回 None 仅当行不存在；已被 watchdog 收敛的 job
        原样返回（CAS 失败不 notify/log——对齐 Redis 版）。"""
        record, transitioned = await asyncio.to_thread(
            self._mark_done_sync, job_key, result_text
        )
        if record is not None and transitioned:
            self._notify_change(record.device_id)
            self._notify_record(record)
            logger.info("job done", extra={"job_key": job_key})
        return record

    def _mark_done_sync(self, job_key: str, result_text: str) -> tuple[JobRecord | None, bool]:
        now_iso = _dt_now_ms()
        with self._conn() as conn:
            cur = conn.execute(_MARK_DONE_SQL, (
                STATUS_DONE, self._truncate(result_text), now_iso, now_iso,
                job_key, STATUS_RUNNING, now_iso,
            ))
            if cur.rowcount == 1:
                inc_job_store("written")
                transitioned = True
            else:
                inc_job_store("cas_miss")
                transitioned = False
                row = conn.execute(
                    "SELECT status, updated_at FROM agent_jobs WHERE job_key = ?",
                    (job_key,),
                ).fetchone()
                if row is not None and row[0] == STATUS_RUNNING:
                    logger.warning(
                        "job state transition guard triggered (updated_at regression?)",
                        extra={"job_key": job_key, "stored_updated_at": row[1]},
                    )
            row = conn.execute(_SELECT_SQL, (job_key,)).fetchone()
        record = self._row_to_record(row) if row is not None else None
        return record, transitioned

    async def mark_delivered(self, job_key: str) -> JobRecord | None:
        """done → delivered（CAS, idempotent）。已 delivered 的 job 原样返回
        （幂等不 notify/log）；非 done 的 job 不受影响（返回 None 对齐现语义）。
        仅 CAS 成功触发 record listener——对齐 Redis 版。"""
        record, transitioned = await asyncio.to_thread(
            self._mark_delivered_sync, job_key
        )
        if record is None:
            return None
        if transitioned:
            logger.info("job delivered", extra={"job_key": job_key})
            self._notify_record(record)
        return record

    def _mark_delivered_sync(self, job_key: str) -> tuple[JobRecord | None, bool]:
        now_iso = _dt_now_ms()
        with self._conn() as conn:
            cur = conn.execute(_MARK_DELIVERED_SQL, (
                STATUS_DELIVERED, now_iso, now_iso, job_key, STATUS_DONE, now_iso,
            ))
            if cur.rowcount == 1:
                inc_job_store("written")
                row = conn.execute(_SELECT_SQL, (job_key,)).fetchone()
                record = self._row_to_record(row) if row is not None else None
                return record, True
            # rowcount == 0：幂等（已 delivered）或非 done 状态（正常跳过，不
            # 计 cas_miss——与 Redis 版 ok!=1 的静默语义一致）
            row = conn.execute(_SELECT_SQL, (job_key,)).fetchone()
            if row is None:
                return None, False
            record = self._row_to_record(row)
            if record.status != STATUS_DELIVERED:
                return None, False
            return record, False

    async def mark_failed(self, job_key: str, reason: str) -> JobRecord | None:
        record = await self._finish(job_key, STATUS_FAILED, reason)
        if record is not None:
            logger.warning("job failed", extra={"job_key": job_key, "reason": reason})
        return record

    async def mark_orphaned(self, job_key: str) -> JobRecord | None:
        record = await self._finish(job_key, STATUS_FAILED, "orphaned")
        if record is not None:
            logger.info("job orphaned", extra={"job_key": job_key})
        return record

    async def orphan_session_jobs(self, session_id: str) -> list[JobRecord]:
        """Ops-only cleanup: mark all active jobs of a session as failed(orphaned)."""
        orphaned: list[JobRecord] = []
        for job_key in await self.session_job_keys(session_id):
            record = await self.get(job_key)
            if record is not None and record.status in ACTIVE_STATUSES:
                converged = await self.mark_orphaned(job_key)
                if converged is not None:
                    orphaned.append(converged)
        return orphaned

    # -- queries --------------------------------------------------------------

    async def get(self, job_key: str) -> JobRecord | None:
        return await asyncio.to_thread(self._get_sync, job_key)

    def _get_sync(self, job_key: str) -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute(_SELECT_SQL, (job_key,)).fetchone()
        return self._row_to_record(row) if row is not None else None

    async def find_by_task_id(self, task_id: str) -> JobRecord | None:
        if not task_id:
            return None
        return await asyncio.to_thread(self._find_by_task_id_sync, task_id)

    def _find_by_task_id_sync(self, task_id: str) -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute(_SELECT_BY_TASK_SQL, (task_id,)).fetchone()
        return self._row_to_record(row) if row is not None else None

    async def session_job_keys(self, session_id: str) -> list[str]:
        return await asyncio.to_thread(self._session_job_keys_sync, session_id)

    def _session_job_keys_sync(self, session_id: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT job_key FROM agent_jobs WHERE session_id = ? ORDER BY job_key",
                (session_id,),
            ).fetchall()
        return [r[0] for r in rows]

    async def device_job_keys(self, device_id: str) -> list[str]:
        return await asyncio.to_thread(self._device_job_keys_sync, device_id)

    def _device_job_keys_sync(self, device_id: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT job_key FROM agent_jobs WHERE device_uuid = ? ORDER BY job_key",
                (device_id,),
            ).fetchall()
        return [r[0] for r in rows]

    async def outstanding_jobs(self, session_id: str) -> list[dict]:
        """In-flight jobs for a session: [{job_key, task_id, summary, agent_name}].

        status 谓词下推单语句（D5 历史永久保留：原 device_job_keys + 逐 key
        get 的 N+1 扇出随历史无界增长，单设备一年 ~10⁴ 行）。
        """
        return await asyncio.to_thread(self._outstanding_jobs_sync, session_id)

    def _outstanding_jobs_sync(self, session_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT job_key, hermes_task_id, summary, agent_name FROM agent_jobs "
                f"WHERE session_id = ? AND {_ACTIVE_STATUSES_CLAUSE}",
                (session_id,),
            ).fetchall()
        return [
            {"job_key": r["job_key"], "task_id": r["hermes_task_id"] or "",
             "summary": r["summary"], "agent_name": r["agent_name"] or ""}
            for r in rows
        ]

    async def outstanding_jobs_for_device(self, device_id: str) -> list[dict]:
        """In-flight jobs for a device: [{job_key, task_id, summary, agent_name}].

        Device-dimension query (R3): a new chat session must see jobs
        dispatched by older sessions of the same device.
        status 谓词下推单语句（同 outstanding_jobs 的 N+1 修复）。
        """
        return await asyncio.to_thread(
            self._outstanding_jobs_for_device_sync, device_id
        )

    def _outstanding_jobs_for_device_sync(self, device_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT job_key, hermes_task_id, summary, agent_name FROM agent_jobs "
                f"WHERE device_uuid = ? AND {_ACTIVE_STATUSES_CLAUSE}",
                (device_id,),
            ).fetchall()
        return [
            {"job_key": r["job_key"], "task_id": r["hermes_task_id"] or "",
             "summary": r["summary"], "agent_name": r["agent_name"] or ""}
            for r in rows
        ]

    async def list_active_for_device(self, device_id: str) -> list[JobRecord]:
        """设备维度的 active（requested/running）完整记录，单语句一次连接。

        替代 device_job_keys + 逐 key get 的 N+1（job_status._evaluate /
        hermes job recovery 热路径；D5 历史永久保留下全历史扇出无界）。
        调用方按需内存过滤（如仅 running）。
        """
        return await asyncio.to_thread(self._list_active_for_device_sync, device_id)

    def _list_active_for_device_sync(self, device_id: str) -> list[JobRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_jobs "
                f"WHERE device_uuid = ? AND {_ACTIVE_STATUSES_CLAUSE}",
                (device_id,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    async def list_pending_delivery(self, device_id: str) -> list[JobRecord]:
        """done jobs of a device that were never delivered, by finished_at
        ascending（补投顺序保证）。48h 补投窗口为查询谓词（原 Redis TTL 语义，
        §3.5.2）。"""
        cutoff = _dt(time.time() - 2 * _DAY_S)
        return await asyncio.to_thread(
            self._list_pending_delivery_sync, device_id, cutoff
        )

    def _list_pending_delivery_sync(
        self, device_id: str, cutoff_iso: str
    ) -> list[JobRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_jobs WHERE device_uuid = ? AND status = ? "
                "AND finished_at >= ? ORDER BY finished_at ASC",
                (device_id, STATUS_DONE, cutoff_iso),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    async def list_active_by_device(self) -> dict[str, list[JobRecord]]:
        """Startup recovery input: list_active() grouped by device_id."""
        groups: dict[str, list[JobRecord]] = {}
        for record in await self.list_active():
            groups.setdefault(record.device_id, []).append(record)
        return groups

    async def list_pending_delivery_all(self) -> list[JobRecord]:
        """All done-undelivered jobs across devices（停机 deferred 计数）。"""
        cutoff = _dt(time.time() - 2 * _DAY_S)
        return await asyncio.to_thread(self._list_pending_delivery_all_sync, cutoff)

    def _list_pending_delivery_all_sync(self, cutoff_iso: str) -> list[JobRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_jobs WHERE status = ? AND finished_at >= ?",
                (STATUS_DONE, cutoff_iso),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    async def in_flight_count(self) -> int:
        return await asyncio.to_thread(self._in_flight_count_sync)

    def _in_flight_count_sync(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM agent_jobs WHERE {_ACTIVE_STATUSES_CLAUSE}"
            ).fetchone()
        return int(row[0])

    async def list_active(self) -> list[JobRecord]:
        """All jobs currently in requested/running state (watchdog scan)."""
        return await asyncio.to_thread(self._list_active_sync)

    def _list_active_sync(self) -> list[JobRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_jobs WHERE {_ACTIVE_STATUSES_CLAUSE}"
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    async def scan_stale(
        self, now: float, *, running_timeout_s: int, requested_timeout_s: int,
        agent_name: str | None = None,
    ) -> list[JobRecord]:
        """Watchdog convergence pass（与 JobStore 同逻辑）。

        agent_name：只收敛该归属的 job（多 adapter 各自看门狗；None = 全部，
        存量行为）。"""
        stale: list[JobRecord] = []
        for record in await self.list_active():
            if agent_name is not None and record.agent_name != agent_name:
                continue
            age = now - record.updated_at
            if record.status == STATUS_RUNNING and age > running_timeout_s:
                converged = await self.mark_failed(record.job_key, "timeout")
                if converged is not None:
                    stale.append(converged)
            elif record.status == STATUS_REQUESTED and age > requested_timeout_s:
                converged = await self.mark_failed(record.job_key, "orphaned")
                if converged is not None:
                    stale.append(converged)
        return stale

    # -- serialization --------------------------------------------------------

    def _to_row(self, record: JobRecord) -> dict:
        """JobRecord → agent_jobs 行（§3.5.1 映射；逻辑自 job_persistence._to_row
        迁入，recording_session_id 改取 JobRecord 字段）。finished_at 派生：
        completed_at > 0 → completed_at；failed（completed_at 恒 0）→ 失败转换
        的 updated_at；其余 NULL。"""
        if record.completed_at > 0:
            finished_at = _dt(record.completed_at)
        elif record.status == STATUS_FAILED:
            finished_at = _dt(record.updated_at)
        else:
            finished_at = None
        return {
            "job_key": record.job_key,
            "agent_name": record.agent_name,
            "device_uuid": record.device_id,
            "session_id": record.session_id,
            "recording_session_id": record.recording_session_id or None,
            "hermes_task_id": record.hermes_task_id or None,
            "status": record.status,
            "summary": record.summary,
            "result_text": record.result_text or None,
            "fail_reason": record.fail_reason or None,
            "requested_ts_ms": int(record.created_at * 1000) if record.created_at > 0 else 0,
            "requested_at": _dt(record.created_at),
            "started_at": _dt(record.started_at),
            "finished_at": finished_at,
            "delivered_at": _dt(record.delivered_at),
            "updated_at": _dt(record.updated_at),
        }

    def _row_to_record(self, row: Any) -> JobRecord:
        """agent_jobs 行 → JobRecord（ISO → epoch 秒）。"""
        return JobRecord(
            job_key=row["job_key"],
            hermes_task_id=row["hermes_task_id"] or "",
            session_id=row["session_id"],
            device_id=row["device_uuid"],
            status=row["status"],
            summary=row["summary"],
            fail_reason=row["fail_reason"] or "",
            result_text=row["result_text"] or "",
            agent_name=row["agent_name"] or "hermes",
            completed_at=_epoch_s(row["finished_at"]),
            delivered_at=_epoch_s(row["delivered_at"]),
            started_at=_epoch_s(row["started_at"]),
            created_at=_epoch_s(row["requested_at"]),
            updated_at=_epoch_s(row["updated_at"]),
            recording_session_id=row["recording_session_id"],
        )

    def _truncate(self, text: str) -> str:
        return (text or "")[: self._max_result_bytes]
