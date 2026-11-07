"""JobStore — Redis-persisted state for agent jobs.

See design doc §4.4 / §5.1 (fd_agent_relay_service_v2.md). Features:
result_text storage (truncated), delivered status, per-device index for
48h redelivery on device reconnect. A job is created in status=requested
when the main agent asks for one; the relay dispatches it into a dedicated
job session and marks it running; the job session's terminal
message.complete marks it done and stores the result; delivery marks it
delivered. A watchdog converges stale jobs to failed.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

import redis.asyncio as aioredis

logger = logging.getLogger("livachat.agent_relay.job_store")

# Job 超时与结果可投递窗口（超 48h 未投递即随 TTL 过期丢弃）。
JOB_TTL_S = 2 * 24 * 3600

# summary 硬上界（fd_job_status.md §2.3）：契约摘要"一行语义"的硬约束。
# 存储层即截断——否则异常/恶意 agent 可让 job_status 载荷与指纹无限膨胀，
# 文档"≤10 jobs × 一行摘要，有界"的断言落空。按字符截断（多字节安全）。
JOB_SUMMARY_MAX_CHARS = 256

STATUS_REQUESTED = "requested"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"

ACTIVE_STATUSES = frozenset({STATUS_REQUESTED, STATUS_RUNNING})


@dataclass
class JobRecord:
    job_key: str            # relay-generated uuid (idempotency key, primary key)
    hermes_task_id: str     # 外部关联键：hermes session:<stored_session_id> / openclaw runId
    session_id: str         # 创建时的 livachat session（仅追溯用）
    device_id: str          # 投递与索引主键
    status: str             # requested|running|done|delivered|failed
    summary: str            # one-line summary from the main agent
    fail_reason: str = ""   # timeout|orphaned|rpc_error|session_lost|run_error|aborted
                            # |interrupted|cancelled|create_error|submit_error|session_cap
                            # |dispatch_aborted|subscribe_error
    result_text: str = ""   # job 成品文案（截断 max_result_bytes）
    agent_name: str = "hermes"  # 归属 adapter（hermes | openclaw；§5.3 D11）
    recording_session_id: str | None = None  # create 时尽力解析（§3.5.3）；NULL → enrich 兜底
    completed_at: float = 0.0   # 完成时间（补投排序键）
    delivered_at: float = 0.0   # 投递成功时间（0 = 未投递）
    started_at: float = 0.0     # 进入 running 的时刻（epoch s）；0 = 未进入/存量数据
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def from_mapping(cls, m: dict) -> JobRecord:
        data = dict(m)
        for key in ("created_at", "updated_at", "completed_at", "delivered_at", "started_at"):
            if data.get(key):
                data[key] = float(data[key])
        return cls(**data)


class JobStore:
    """Redis-backed store for Hermes background job lifecycle."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        *,
        key_prefix: str = "livachat",
        max_result_bytes: int = 16384,
    ) -> None:
        self._redis = redis_client
        self._prefix = key_prefix
        self._max_result_bytes = max_result_bytes
        self._change_listener: Callable[[str], Awaitable[None]] | None = None
        self._record_listener: Callable[[JobRecord], None] | None = None

    # -- key helpers ----------------------------------------------------------

    def _job_key(self, job_key: str) -> str:
        return f"{self._prefix}:agent:hermes:job:{job_key}"

    def _session_jobs_key(self, session_id: str) -> str:
        return f"{self._prefix}:agent:hermes:session:{session_id}:jobs"

    def _device_jobs_key(self, device_id: str) -> str:
        return f"{self._prefix}:agent:hermes:device:{device_id}:jobs"

    def _task_key(self, task_id: str) -> str:
        return f"{self._prefix}:agent:hermes:task:{task_id}"

    def _inflight_key(self) -> str:
        return f"{self._prefix}:agent:hermes:inflight"

    # -- change notification ---------------------------------------------------

    def set_change_listener(
        self, cb: Callable[[str], Awaitable[None]] | None
    ) -> None:
        """Register a fire-and-forget callback invoked after transitions that
        change the running snapshot: mark_running (requested→running, +1),
        mark_done (running→done, -1), _finish (active→failed, covers watchdog
        convergence / session_lost / rpc_error; -1 when running). Invoked with
        device_id. Never blocks or raises into the transition path."""
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
        """消费 listener task 异常，避免 unretrieved task error；契约是
        fire-and-forget，异常只记录不扩散（§4.2b）。"""
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
        """迁移成功后的记录级钩子（D2）：create/mark_running/mark_done/
        mark_delivered/_finish 五入口，以迁移后的最新 JobRecord 同步回调。
        与 set_change_listener 不同：携带完整 record，供 PG 镜像等持久化
        消费者使用。契约：fire-and-forget，cb 必须 O(1) 非阻塞；JobStore
        侧 try/except 兜底，异常只 log 不扩散。"""
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
        """Create a job in status=requested. Returns the new record."""
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
        pipe = self._redis.pipeline()
        pipe.hset(self._job_key(job.job_key), mapping=self._to_mapping(job))
        pipe.expire(self._job_key(job.job_key), JOB_TTL_S)
        pipe.sadd(self._session_jobs_key(session_id), job.job_key)
        pipe.expire(self._session_jobs_key(session_id), JOB_TTL_S)
        pipe.sadd(self._device_jobs_key(device_id), job.job_key)
        pipe.expire(self._device_jobs_key(device_id), JOB_TTL_S)
        pipe.sadd(self._inflight_key(), job.job_key)
        pipe.expire(self._inflight_key(), JOB_TTL_S)
        await pipe.execute()
        logger.info("job created", extra={"job_key": job.job_key, "session_id": session_id})
        self._notify_record(job)
        return job

    async def bind_external_ref(self, job_key: str, external_ref: str) -> bool:
        """Write the external correlation key while the job is still requested.

        v3 job-session carrier: the stored session id must be persisted before
        ``prompt.submit`` so a relay crash can be recovered. Status is unchanged
        (requested); CAS miss means the watchdog already converged the job.
        """
        key = self._job_key(job_key)
        ok = await self._cas_transition(
            key, (STATUS_REQUESTED,),
            ("hermes_task_id", external_ref),
            ("updated_at", time.time()),
        )
        if ok != 1:
            return False
        if external_ref:
            await self._redis.set(self._task_key(external_ref), job_key, ex=JOB_TTL_S)
        record = await self.get(job_key)
        if record is not None:
            self._notify_record(record)
        return True

    async def mark_running(self, job_key: str, hermes_task_id: str) -> bool:
        """Transition requested→running (CAS). Returns False if the job was
        already converged by the watchdog (caller should abandon the task)."""
        key = self._job_key(job_key)
        ok = await self._cas_transition(
            key, (STATUS_REQUESTED,),
            ("status", STATUS_RUNNING),
            ("hermes_task_id", hermes_task_id),
            ("started_at", time.time()),
            ("updated_at", time.time()),
        )
        if ok != 1:
            return False
        if hermes_task_id:
            await self._redis.set(self._task_key(hermes_task_id), job_key, ex=JOB_TTL_S)
        record = await self.get(job_key)
        if record is not None:
            self._notify_change(record.device_id)
            self._notify_record(record)
        return True

    async def _finish(self, job_key: str, status: str, fail_reason: str = "") -> JobRecord | None:
        """Transition from an active state to a terminal failed state (CAS)
        and remove from the inflight set. Non-active jobs (done/delivered)
        are untouched."""
        key = self._job_key(job_key)
        ok = await self._cas_transition(
            key, tuple(sorted(ACTIVE_STATUSES)),
            ("status", status),
            ("fail_reason", fail_reason),
            ("updated_at", time.time()),
        )
        if ok != 1:
            return await self.get(job_key)
        await self._redis.srem(self._inflight_key(), job_key)
        record = await self.get(job_key)
        if record is not None:
            self._notify_change(record.device_id)
            self._notify_record(record)
        return record

    async def mark_done(self, job_key: str, result_text: str = "") -> JobRecord | None:
        """running → done (CAS). Stores the truncated result_text and
        completed_at (the redelivery sort key), removes the job from the
        inflight set, and refreshes the 48h TTL of the job hash + indexes so
        the redelivery window starts at completion, not creation (R2).
        Returns None only when the job hash is gone; a job already
        converged by the watchdog is returned unchanged."""
        key = self._job_key(job_key)
        ok = await self._cas_transition(
            key, (STATUS_RUNNING,),
            ("status", STATUS_DONE),
            ("result_text", self._truncate(result_text)),
            ("completed_at", time.time()),
            ("updated_at", time.time()),
        )
        if ok != 1:
            return await self.get(job_key)
        record = await self.get(job_key)
        if record is None:
            await self._redis.srem(self._inflight_key(), job_key)
            return None
        self._notify_change(record.device_id)
        self._notify_record(record)
        pipe = self._redis.pipeline()
        pipe.srem(self._inflight_key(), job_key)
        pipe.expire(self._job_key(job_key), JOB_TTL_S)
        pipe.expire(self._device_jobs_key(record.device_id), JOB_TTL_S)
        pipe.expire(self._session_jobs_key(record.session_id), JOB_TTL_S)
        await pipe.execute()
        logger.info("job done", extra={"job_key": job_key})
        return record

    async def mark_delivered(self, job_key: str) -> JobRecord | None:
        """done → delivered (CAS, idempotent). Writes delivered_at; jobs
        already delivered are returned as-is, non-done jobs are untouched."""
        key = self._job_key(job_key)
        ok = await self._cas_transition(
            key, (STATUS_DONE,),
            ("status", STATUS_DELIVERED),
            ("delivered_at", time.time()),
            ("updated_at", time.time()),
        )
        if ok != 1:
            record = await self.get(job_key)
            if record is not None and record.status == STATUS_DELIVERED:
                return record
            return None
        record = await self.get(job_key)
        if record is not None:
            logger.info("job delivered", extra={"job_key": job_key})
            self._notify_record(record)
        return record

    async def mark_failed(self, job_key: str, reason: str) -> JobRecord | None:
        record = await self._finish(job_key, STATUS_FAILED, reason)
        if record is not None:
            logger.warning("job failed", extra={"job_key": job_key, "reason": reason})
        return record

    async def mark_orphaned(self, job_key: str) -> JobRecord | None:
        """Ops-only convergence: failed status with fail_reason=orphaned (§4.4:
        orphaned is not a standalone status). Not called from the
        session-ended path anymore."""
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
        raw = await self._redis.hgetall(self._job_key(job_key))
        if not raw:
            return None
        return JobRecord.from_mapping(raw)

    async def find_by_task_id(self, task_id: str) -> JobRecord | None:
        if not task_id:
            return None
        job_key = await self._redis.get(self._task_key(task_id))
        if not job_key:
            return None
        return await self.get(str(job_key))

    async def session_job_keys(self, session_id: str) -> list[str]:
        raw = await self._redis.smembers(self._session_jobs_key(session_id))
        return sorted(raw)

    async def device_job_keys(self, device_id: str) -> list[str]:
        raw = await self._redis.smembers(self._device_jobs_key(device_id))
        return sorted(raw)

    async def outstanding_jobs(self, session_id: str) -> list[dict]:
        """In-flight jobs for a session: [{job_key, task_id, summary, agent_name}]."""
        out: list[dict] = []
        for job_key in await self.session_job_keys(session_id):
            record = await self.get(job_key)
            if record is not None and record.status in ACTIVE_STATUSES:
                out.append({
                    "job_key": record.job_key,
                    "task_id": record.hermes_task_id,
                    "summary": record.summary,
                    "agent_name": record.agent_name or "",
                })
        return out

    async def outstanding_jobs_for_device(self, device_id: str) -> list[dict]:
        """In-flight jobs for a device: [{job_key, task_id, summary, agent_name}].

        Device-dimension query (R3): a new chat session must see jobs
        dispatched by older sessions of the same device.
        """
        out: list[dict] = []
        for job_key in await self.device_job_keys(device_id):
            record = await self.get(job_key)
            if record is not None and record.status in ACTIVE_STATUSES:
                out.append({
                    "job_key": record.job_key,
                    "task_id": record.hermes_task_id,
                    "summary": record.summary,
                    "agent_name": record.agent_name or "",
                })
        return out

    async def list_pending_delivery(self, device_id: str) -> list[JobRecord]:
        """done jobs of a device that were never delivered, by completed_at
        ascending (redelivery order guarantee)."""
        out: list[JobRecord] = []
        for job_key in await self.device_job_keys(device_id):
            record = await self.get(job_key)
            if record is not None and record.status == STATUS_DONE:
                out.append(record)
        out.sort(key=lambda r: r.completed_at)
        return out

    async def list_active_for_device(self, device_id: str) -> list[JobRecord]:
        """设备维度 active（requested/running）完整记录。

        job_status._evaluate / hermes job recovery 热路径的单语句查询替代品
        （SQLite 版为单语句，job_store_sqlite.py）；本 Redis 版保留为接口
        兼容实现（device_job_keys + 逐 key get）。
        """
        out: list[JobRecord] = []
        for job_key in await self.device_job_keys(device_id):
            record = await self.get(job_key)
            if record is not None and record.status in ACTIVE_STATUSES:
                out.append(record)
        return out

    async def list_active_by_device(self) -> dict[str, list[JobRecord]]:
        """Startup recovery input: list_active() grouped by device_id."""
        groups: dict[str, list[JobRecord]] = {}
        for record in await self.list_active():
            groups.setdefault(record.device_id, []).append(record)
        return groups

    async def list_pending_delivery_all(self) -> list[JobRecord]:
        """All done-undelivered jobs across devices (shutdown `deferred` count)."""
        out: list[JobRecord] = []
        prefix = f"{self._prefix}:agent:hermes:device:"
        suffix = ":jobs"
        try:
            async for key in self._redis.scan_iter(f"{prefix}*{suffix}"):
                device_id = key[len(prefix):-len(suffix)]
                out.extend(await self.list_pending_delivery(device_id))
        except Exception:
            logger.warning("failed to scan device job indexes", exc_info=True)
        return out

    async def in_flight_count(self) -> int:
        count = await self._redis.scard(self._inflight_key())
        return int(count or 0)

    async def list_active(self) -> list[JobRecord]:
        """All jobs currently in requested/running state (watchdog scan)."""
        out: list[JobRecord] = []
        for job_key in await self._redis.smembers(self._inflight_key()):
            record = await self.get(str(job_key))
            if record is not None and record.status in ACTIVE_STATUSES:
                out.append(record)
        return out

    async def scan_stale(
        self, now: float, *, running_timeout_s: int, requested_timeout_s: int,
        agent_name: str | None = None,
    ) -> list[JobRecord]:
        """Watchdog convergence pass. Returns the stale jobs that were
        transitioned to failed(timeout) / failed(orphaned).

        agent_name：只收敛该归属的 job（多 adapter 各自看门狗；None = 全部）。
        """
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

    @staticmethod
    def _to_mapping(record: JobRecord) -> dict:
        data = asdict(record)
        # recording_session_id 默认 None（§3.5.3）；Redis hash 值不可为 None
        return {k: (v if v is not None else "") for k, v in data.items()}

    def _truncate(self, text: str) -> str:
        return (text or "")[: self._max_result_bytes]

    async def _cas_transition(
        self,
        key: str,
        expected: tuple[str, ...],
        *fields: tuple[str, object],
    ) -> int:
        """Atomically write hash fields if current status ∈ expected (A3).

        WATCH-based optimistic CAS: eliminates the check-then-act race
        between the watchdog and a live job completion (no failed→done
        reverse transition, §4.4). Works on real Redis (WATCH) and on
        fakeredis (no EVAL support; single-threaded, so the WATCH never
        fires and the loop passes once).

        Returns 1 on success, 0 on status mismatch, -1 when the key is
        gone. field values are coerced to str (Redis hash values)."""
        while True:
            pipe = self._redis.pipeline()
            try:
                await pipe.watch(key)
                current = await pipe.hget(key, "status")
                if current is None:
                    return -1
                if current not in expected:
                    return 0
                pipe.multi()
                for name, value in fields:
                    pipe.hset(key, name, str(value))
                await pipe.execute()
                return 1
            except aioredis.WatchError:
                continue
            finally:
                await pipe.reset()
