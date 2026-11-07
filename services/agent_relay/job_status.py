"""JobStatusNotifier — running background job status snapshot publisher.

Design doc: docs/fd_job_status.md (§3.1/§3.2/§4.3). Three trigger sources:
- JobStore change hook: on_job_change() → immediate evaluate (D3-1);
- 1s scan tick: fallback + 15s throttle timer + relay restart self-heal (D3-2);
- session.started: on_device_online() → force evaluate, first frame (R4).

Change detection uses a fingerprint of (job_id, summary, started_at_ms)
sorted by started_at ascending; running_time_ms is derived at send time and
excluded from the fingerprint (D2). Only successful sends update last-sent
state (§3.2). Frames are idempotent full replacements (D9).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from agent_relay.job_store import (
    JOB_SUMMARY_MAX_CHARS,
    JOB_TTL_S,
    STATUS_RUNNING,
    JobRecord,
    JobStore,
)
from agent_relay.metrics import inc_job_status
from agent_relay.models import JobStatusItem, JobStatusPushMessage
from agent_relay.publisher import PushPublisher

logger = logging.getLogger("livachat.agent_relay.job_status")


def _fingerprint(records: list[JobRecord]) -> tuple[tuple[str, str, int], ...]:
    """(job_id, summary, started_at_ms) tuples, running only, sorted by
    started_at ascending. started_at falls back to created_at for legacy
    records (no started_at in Redis). running_time_ms is deliberately NOT
    part of the fingerprint — it changes on every frame and would defeat
    throttling (D2)."""
    items: list[tuple[str, str, int]] = []
    for r in records:
        if r.status != STATUS_RUNNING:
            continue
        # summary 截断兜底：存量数据（截断上线前入库）可能超长，与 create 边界
        # 的截断保持一致，保证载荷与指纹在任何数据下都有硬上界。
        items.append(
            (r.job_key, r.summary[:JOB_SUMMARY_MAX_CHARS], int((r.started_at or r.created_at) * 1000))
        )
    items.sort(key=lambda t: t[2])
    return tuple(items)


class JobStatusNotifier:
    """Pushes full running-job snapshots to devices, change-driven with a
    periodic throttle, per-device serialized (D8)."""

    def __init__(
        self,
        job_store: JobStore,
        publisher: PushPublisher,
        *,
        agent_name: str = "hermes",
        interval_s: float = 15.0,          # 周期（R1）
        scan_interval_s: float = 1.0,      # tick 兜底周期
        online_check: Callable[[str], bool] | None = None,   # conn_mgr.is_online
    ) -> None:
        self._job_store = job_store
        self._publisher = publisher
        self._agent_name = agent_name
        self._interval_s = interval_s
        self._scan_interval_s = scan_interval_s
        self._online_check = online_check
        self._device_locks: dict[str, asyncio.Lock] = {}
        # device_id → (last successfully sent fingerprint, monotonic sent time)
        self._last: dict[str, tuple[tuple[tuple[str, str, int], ...], float]] = {}
        self._scan_task: asyncio.Task[None] | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        if self._scan_task is None:
            self._scan_task = asyncio.create_task(self._scan_loop(), name="job-status-scan")

    async def stop(self) -> None:
        if self._scan_task is not None:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None

    # -- trigger sources -----------------------------------------------------

    async def on_job_change(self, device_id: str) -> None:
        """JobStore hook (already scheduled by JobStore.create_task):
        immediate evaluate. Never raises into the transition path."""
        try:
            await self._evaluate(device_id)
        except Exception:
            inc_job_status("error")
            logger.warning(
                "job status evaluate failed",
                extra={"device_id": device_id},
                exc_info=True,
            )

    async def on_device_online(self, device_id: str) -> None:
        """session.started first report: force evaluate (ignores the
        15s throttle timer, R4)."""
        try:
            await self._evaluate(device_id, force=True)
        except Exception:
            inc_job_status("error")
            logger.warning(
                "job status first-report evaluate failed",
                extra={"device_id": device_id},
                exc_info=True,
            )

    # -- scan loop -----------------------------------------------------------

    async def _scan_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._scan_interval_s)
                await self._scan_once()
            except asyncio.CancelledError:
                logger.info("job status scan stopped")
                return
            except Exception:
                inc_job_status("error")
                logger.warning("job status scan tick failed", exc_info=True)

    async def _scan_once(self) -> None:
        """One tick: 1 SMEMBERS + ≤20 HGETALL (bounded, §2.2), build
        candidate fingerprints in memory (no per-device re-read), pre-check
        fingerprint + throttle timer; only suspect devices enter _evaluate
        which re-reads the authoritative snapshot inside the per-device
        lock. Restart self-heal: unknown _last → treated as change → first
        tick re-sends for online devices (no session.started dependency)."""
        now = time.monotonic()
        # 有界性兜底（§3.2 不变式 3）：条目按 job TTL 淘汰。条目比 48h 更旧
        # ⇒ 该设备至少失联 48h ⇒ 其 running job 已随 TTL 过期，旧指纹无保留
        # 价值；重连后由 session.started 首报按当前快照重新评估（在线设备的
        # 条目每 15s 帧刷新，永远不会被此规则波及）。
        for device_id in [d for d, (_, t) in self._last.items() if now - t >= JOB_TTL_S]:
            self._last.pop(device_id, None)

        groups = await self._job_store.list_active_by_device()
        for device_id, records in groups.items():
            fp = _fingerprint(records)
            last = self._last.get(device_id)
            if not fp and last is None:
                continue  # 无 running 且无记录：静默（不变式 2），无 per-device 读
            if last is None or fp != last[0] or now - last[1] >= self._interval_s:
                await self._evaluate(device_id)

    # -- evaluate ------------------------------------------------------------

    async def _evaluate(self, device_id: str, *, force: bool = False) -> None:
        """Per-device lock (D8): online-check first, then re-read authoritative
        snapshot, apply the §3.2 decision table, send; update last-sent state
        only on success. Single tick errors never crash the loop."""
        lock = self._device_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            # 在线检查前置（重读之前）：离线设备无论决策结果如何都不会发送，
            # 提前 return 与「判定后检查」语义等价——离线时无发送、不更新 _last、
            # 脏标记保持（§3.2 不变式 1）。成本从「每 tick 全量历史读（device_job_keys
            # + N×HGETALL，N 含 48h 历史）」降为「每 tick 1 次 resolve_instance GET」。
            # 指标语义随之微调：离线空转评估（如 watchdog 对 requested 的收敛，本
            # 就不会发送）也计 skipped_offline，含义为「离线评估数」而非「发送被阻止数」。
            if not await self._device_online(device_id):
                inc_job_status("skipped_offline")
                return

            # 单语句 active 查询 + 内存过滤 running（替代 device_job_keys +
            # 逐 key get 的 N+1——D5 历史永久保留下全历史扇出无界，§3.5.2 热
            # 路径修复）。active 集有界（in-flight cap 20），过滤开销可忽略。
            records = [
                r for r in await self._job_store.list_active_for_device(device_id)
                if r.status == STATUS_RUNNING
            ]
            records.sort(key=lambda r: r.started_at or r.created_at)
            fp = _fingerprint(records)

            last = self._last.get(device_id)
            last_fp = last[0] if last is not None else ()
            last_sent_at = last[1] if last is not None else 0.0
            now = time.monotonic()

            if fp:
                if force:
                    mode = "sent"                      # 首报：无视计时
                elif fp != last_fp:
                    mode = "sent"                      # 变化帧
                elif now - last_sent_at >= self._interval_s:
                    mode = "sent_throttled"            # 周期帧（R1）
                else:
                    mode = None
            elif last is not None and last_fp:
                mode = "sent"                          # 非空→空清除信号（R2）
            else:
                mode = None                            # 空且无记录：静默

            if mode is None:
                return

            now_ms_val = int(time.time() * 1000)
            jobs = [
                JobStatusItem(
                    job_id=r.job_key,
                    # 归属以 JobRecord 为准（多 adapter 共存）；存量/缺失时回退构造默认
                    agent_name=r.agent_name or self._agent_name,
                    summary=r.summary[:JOB_SUMMARY_MAX_CHARS],
                    started_at_ms=int((r.started_at or r.created_at) * 1000),
                    running_time_ms=max(0, now_ms_val - int((r.started_at or r.created_at) * 1000)),
                )
                for r in records
            ]
            msg = JobStatusPushMessage(device_id=device_id, jobs=jobs)
            if await self._publisher.publish(msg):
                inc_job_status(mode)
                if fp:
                    self._last[device_id] = (fp, now)   # 仅发送成功才更新
                else:
                    self._last.pop(device_id, None)     # 空快照发送成功后删除条目
            else:
                inc_job_status("skipped_offline")       # 保持脏标记，后续触发路径重试

    async def _device_online(self, device_id: str) -> bool:
        """进程内在线检查（§3.2.2）：conn_mgr.is_online 一次调用。未注入
        online_check 时跳过本地检查，直接依赖 publish 返回值。"""
        if self._online_check is None:
            return True
        return self._online_check(device_id)
