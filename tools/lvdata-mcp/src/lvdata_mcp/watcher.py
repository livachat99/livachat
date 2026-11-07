"""Watcher（cs 单链路，recording_data_process.md §2.4）：poll 同步 + round 处理。

poll loop（轻量，纯 SQL/Python）：
  inventory stage（recording_watch）→ cs.recording_files 增量同步
  → 入队 round 标记（**无数据**；重复标记等价，未消化完自然合并）

work loop（重活）：消费 round → 常跑组 + flush 组门控（§5.C）
  常跑组：inventory → chatlog → audio → video → day_stats → reindex
  （day_stats/reindex 本地零成本，不节流；day_stats 见 docs/bff_service_interfaces.md §7）
  flush 组：embed → day_summary（数量/时限/失败重试/force 触发；组成功才推水印）
  day-summary dirty 发现（discover）在 flush 组 day_summary stage 内执行
  （round 末尾，可见本轮新 events；门控放行见 _flush_due 的待办判定）
  全部为全局批处理、**状态驱动取件**（files 状态机 / processed_offset /
  vec_state）——session 不参与取件与调度（存储语义，见 §2 处理原则）。

trigger_analysis → trigger()：stages 缺省全链（含 flush 组，force 绕过门控）；
force 按 stage 累积，session_id 仅作 force 的作用域限定。

崩溃恢复：全部工作状态在 DB，重启首轮 poll 自然续跑（原则 8）。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from .config import LvdataMcpConfig
from .pipeline import Pipeline
from .store import DataStore, StoreError
from .timeutil import now_iso

log = structlog.get_logger("lvdata_mcp.watcher")

# round 序列（§2.3/§5.C）：常跑组每轮执行；flush 组（embed/day_summary）走门控。
# inventory 在列首：poll 已同步一次（readyz 语义），round 内再同步是幂等空查
# ——保证 trigger_analysis(["inventory"]) 有执行载体
ROUND_STAGES = ("inventory", "chatlog", "audio", "video", "day_stats", "reindex")
FLUSH_STAGES = ("embed", "day_summary")
ALL_STAGES = ROUND_STAGES + FLUSH_STAGES

BACKOFF_DELAYS = (60, 300, 900)
MAX_FAILURES = 3


def _iso_ago(seconds: float) -> str:
    """now_iso 同口径的过去时刻（§5.1 规范格式，供按龄 DELETE 字符串比较）。"""
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Watcher:
    def __init__(self, cfg: LvdataMcpConfig, store: DataStore,
                 pipeline: Pipeline):
        self.cfg = cfg
        self.store = store
        self.pipeline = pipeline
        self.queue: asyncio.Queue[bool] = asyncio.Queue()
        self._round_queued = False
        self._lock = asyncio.Lock()
        # trigger_analysis 显式参数：force 按 stage 累积；target 为 force 的
        # session 作用域（None = 全局）
        self._stage_force: dict[str, bool] = {}
        self._force_target: str | None = None
        self.last_poll_at: str | None = None
        self.first_poll_done = False
        self.pipeline_running: dict | None = None
        self.started = False
        self._poll_task: asyncio.Task | None = None
        self._worker_task: asyncio.Task | None = None
        self._prune_task: asyncio.Task | None = None

    # ── 生命周期 ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.started = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="watcher-poll")
        self._worker_task = asyncio.create_task(self._work_loop(), name="watcher-worker")
        self._prune_task = asyncio.create_task(self._prune_loop(), name="watcher-prune")
        for t in (self._poll_task, self._worker_task, self._prune_task):
            t.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        """任务异常退出即告警——create_task 的异常默认静默无人知晓，
        worker 死亡会表现为 poll 照跑、队列积压空转。"""
        if task.cancelled() or task.exception() is None:
            return
        log.error("watcher task died", task=task.get_name(),
                  error=str(task.exception())[:500])

    async def stop(self) -> None:
        for t in (self._poll_task, self._worker_task, self._prune_task):
            if t:
                t.cancel()
        await asyncio.gather(
            *(t for t in (self._poll_task, self._worker_task, self._prune_task) if t),
            return_exceptions=True)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("poll failed")
            await asyncio.sleep(self.cfg.watcher.poll_interval_s)

    # ── 轮询（§2.4 poll loop）─────────────────────────────────────────────

    async def poll_once(self) -> dict:
        self.last_poll_at = now_iso()
        # 每轮 poll 重置分析文件预算（§10.2 全局护栏，跨 session 共享）
        self.pipeline.reset_budget()
        # 1) cs.recording_files 增量同步（stage: inventory；mcp_pipeline_runs 记录）
        sync_res = await self.pipeline.run_stage("inventory")
        # 失败标记：sync 压成 {} 后 poll done 看不出失败（如启动期 prune 竞态
        # 的 database is locked，靠 round 重跑自愈——见 run_stage 的 traceback）
        if sync_res["status"] != "ok":
            log.warning("inventory sync failed, retried by round",
                        error=sync_res.get("error"), run_id=sync_res.get("run_id"))
        sync_stats = sync_res if sync_res["status"] == "ok" else {}
        # 2) 入队 round 标记（处理层全部状态驱动，无数据可携带）；
        #    day-summary 的 dirty 发现在其 stage 内执行（round 末尾，可含本轮新 events）
        await self.enqueue_round()
        self.first_poll_done = True
        log.info("poll done", sync=sync_stats, queue_size=self.queue.qsize(),
                 pending_files=self._count_pending_files())
        return sync_stats

    async def enqueue_round(self) -> None:
        """入队 round 标记（无数据；已排队时重复标记等价合并）。"""
        async with self._lock:
            self._enqueue_round_locked()

    def _enqueue_round_locked(self) -> None:
        if not self._round_queued:
            self._round_queued = True
            self.queue.put_nowait(True)

    async def trigger(self, stages: list[str] | None = None, *,
                      force: bool = False, session_id: str | None = None) -> None:
        """trigger_analysis 载体：标记 force（按 stage）并入队 round。

        stages=None → 全链；session_id 仅在 force 时作作用域限定。
        """
        async with self._lock:
            targets = list(stages) if stages else list(ALL_STAGES)
            if force:
                for s in targets:
                    self._stage_force[s] = True
                if session_id:
                    self._force_target = session_id
            self._enqueue_round_locked()

    def _count_pending_files(self) -> int:
        """health：当前 pending/failed（可重试）文件积压数；查询失败返回 -1。"""
        try:
            with self.store.connect() as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM files "
                    "WHERE analysis_status IN ('pending', 'failed') "
                    "AND fail_count < ?", (MAX_FAILURES,)).fetchone()[0]
        except (sqlite3.OperationalError, StoreError):
            return -1

    # ── 消费 round（§2.3 work loop）──────────────────────────────────────

    async def _work_loop(self) -> None:
        while True:
            await self.queue.get()
            try:
                # 先清标记再执行：round 进行中到达的 poll/trigger 可重新入队，
                # 不被「已排队」守卫吞掉（旧实现 finally 才清 → 期间触发丢失）
                async with self._lock:
                    self._round_queued = False
                await self._run_round()
            except Exception:
                log.exception("round failed")
            finally:
                self.queue.task_done()

    async def _run_round(self) -> dict:
        """常跑组固定序列 + flush 组门控（§5.C）。"""
        # force 快照（本轮生效；快照后新到的 trigger 标记归下一轮）
        async with self._lock:
            stage_force = dict(self._stage_force)
            self._stage_force.clear()
            target = self._force_target
            self._force_target = None
        results: dict[str, dict] = {}
        for stage in ROUND_STAGES:
            self.pipeline_running = {"stage": stage, "since": now_iso(),
                                     "target": target}
            try:
                results[stage] = await self._run_stage(
                    stage, target, stage_force.get(stage, False))
            finally:
                self.pipeline_running = None
        # 门控在常跑组**之后**评估：chatlog/音视频本轮新产出的 events 立即计入
        # （「每轮产出新 events → 触发 index/vector/day_summary」的会话粒度）
        flush_forced = any(stage_force.get(s) for s in FLUSH_STAGES)
        ran_flush = False
        if flush_forced or self._flush_due():
            ran_flush = True
            for stage in FLUSH_STAGES:
                self.pipeline_running = {"stage": stage, "since": now_iso(),
                                         "target": target}
                try:
                    results[stage] = await self._run_stage(
                        stage, target, stage_force.get(stage, False))
                finally:
                    self.pipeline_running = None
            # 全量 flush 且组内全部成功才推进水印（失败/崩溃不推进 → 下轮重触发）。
            # target 限定（trigger force+session_id）只重建单个会话，**不得**推进
            # 全局水印——否则其它会话的待处理事件（updated_at ≤ 新水印）永久漏嵌。
            if target is None and all(results[s].get("status") == "ok"
                                      for s in FLUSH_STAGES):
                self._mark_flush()
        else:
            log.debug("flush skipped (gate not due)")
        log.info("round done", flush=ran_flush,
                 **{k: v.get("status", "?") for k, v in results.items()})
        return results

    # ── flush 门控（§5.C：embed/day_summary 节流）─────────────────────────

    def _flush_due(self) -> bool:
        """门控四条件（任一即跑）：数量阈值 / 时限兜底 / day_summary 待办 / force。

        待办判定（失败重试 + 待生成日）委托 DaySummary.has_actionable_work：
        discover 只在本 stage 内运行，若门控只看"存在非 ok 行"会把 debounce/
        退避窗口内的日也算进来（活跃录制期间恒放行，节流失效），而只排除
        deferred 行又会让事件停流后的待生成日永久 starvation（09-20 案例：
        卡 3.5h 直到新事件到达）。查询失败宁跑（安全侧：宁可多一次 flush）。
        """
        try:
            with self.store.connect() as conn:
                row = conn.execute(
                    "SELECT last_flush_at FROM flush_state WHERE id = 1"
                ).fetchone()
                since = (row["last_flush_at"] if row else None) or ""
                pending = conn.execute(
                    "SELECT COUNT(*) AS n, MIN(ts_ms) AS oldest FROM events "
                    "WHERE updated_at > ?", (since,)).fetchone()
                n = pending["n"] or 0
                if n >= self.cfg.watcher.flush_min_events:
                    return True
                if n > 0 and pending["oldest"]:
                    age_s = time.time() - pending["oldest"] / 1000
                    if age_s >= self.cfg.watcher.flush_max_delay_s:
                        return True
            ds = self.pipeline.day_summary
            return ds is not None and ds.has_actionable_work()
        except (sqlite3.Error, ValueError, StoreError):
            return True

    def _mark_flush(self) -> None:
        """flush 组完成 → 推进水印（best-effort；写失败下轮重触发）。"""
        try:
            with self.store.connect(readonly=False) as conn:
                conn.execute(
                    "INSERT INTO flush_state (id, last_flush_at) VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "last_flush_at = excluded.last_flush_at", (now_iso(),))
                conn.commit()
        except (sqlite3.OperationalError, StoreError):
            log.warning("flush watermark write failed", exc_info=True)

    async def _run_stage(self, stage: str, target: str | None = None,
                         force: bool = False) -> dict:
        """work loop stage 执行 + 单阶段超时（§10.2 stage_timeout_s）。

        超时取消在途协程：分析类 stage 的中间态（files 状态机/offset 水位）
        全部落盘，下轮续跑（原则 8）。
        """
        timeout = self.cfg.watcher.stage_timeout_s
        if timeout and timeout > 0:
            try:
                return await asyncio.wait_for(
                    self.pipeline.run_stage(stage, target, force), timeout=timeout)
            except TimeoutError:
                return {"status": "failed",
                        "error": f"stage timeout after {timeout}s"}
        return await self.pipeline.run_stage(stage, target, force)

    # ── 审计清理（retention 方案 §2.5）────────────────────────────────────

    async def _prune_loop(self) -> None:
        """按周期清理审计表与过期 video 帧；启动后立即跑一次，单项失败不杀任务。"""
        while True:
            try:
                await asyncio.to_thread(self._prune_once)
            except Exception:
                log.exception("audit prune failed")
            try:
                await asyncio.to_thread(self._video_prune_once)
            except Exception:
                log.exception("video prune failed")
            interval = self.cfg.watcher.audit_prune_interval_s
            await asyncio.sleep(interval if interval > 0 else 86400)

    def _prune_once(self) -> None:
        """单事务按龄清理审计表 + 孤儿水位（§2.5）。

        保留期 0 = 关闭该项；`audit_retention_days=0` 同时跳过孤儿清理
        （"0 = 本方案全部 DELETE 停做"语义）。running 护栏为独立析取——
        合并写则 `started_at < :zombie_cutoff` 恒真、成死分支（僵尸行要活到
        保留期才被删）。僵尸窗口 = max(2×stage_timeout_s, 3600s)：stage 超时
        取消时 `CancelledError` 穿透 `except Exception`、`_record_end` 不执行
        （pipeline.py:95），故超时同样产生僵尸 running 行。
        """
        w = self.cfg.watcher
        if w.audit_retention_days <= 0 and w.analysis_runs_retention_days <= 0:
            return
        with self.store.connect(readonly=False) as conn:
            if w.audit_retention_days > 0:
                cutoff = _iso_ago(w.audit_retention_days * 86400)
                zombie = _iso_ago(max(2 * w.stage_timeout_s, 3600))
                conn.execute(
                    "DELETE FROM mcp_pipeline_runs "
                    "WHERE (status != 'running' AND started_at < ?) "
                    "OR (status = 'running' AND started_at < ?)",
                    (cutoff, zombie))
                # 孤儿水位（events 已消失的会话行）；'' 全局行恒留
                conn.execute(
                    "DELETE FROM index_state WHERE target != '' AND target "
                    "NOT IN (SELECT DISTINCT rsession_id FROM events)")
            if w.analysis_runs_retention_days > 0:
                # started_at 可空（ddl §5.3）：NULL 行无法判龄、不清理
                conn.execute(
                    "DELETE FROM analysis_runs WHERE started_at IS NOT NULL "
                    "AND started_at < ?",
                    (_iso_ago(w.analysis_runs_retention_days * 86400),))
            conn.commit()

    # ── video 帧保留（docs/data_clean.md §4）───────────────────────────────

    def _video_prune_once(self) -> None:
        """删除超保留期、无 video 事件的 ok 帧（jpg + files 行）。

        顺序：逐候选「复核 → 删文件（missing_ok）→ 删行」——中途崩溃自愈
        （行在、文件不在 → 下轮重删）。复核在同一写连接上重读最新已提交态
        （status 仍 ok 且仍无事件），收窄与 force 重跑并发的 TOCTOU 窗口。
        cs.recording_files 不动（只读，§3.3）；pending/failed 不删（D1 拍板）；
        保留期运行时 clamp ≥24h（D2）；路径越 root 跳过（§4.4）。
        """
        w = self.cfg.watcher
        hours = max(24, w.video_retention_hours)
        cutoff_ms = int((time.time() - hours * 3600) * 1000)
        root = Path(self.cfg.data.root_dir).resolve()
        with self.store.connect() as conn:
            key_refs = {r[0] for r in conn.execute(
                "SELECT event_ref FROM events WHERE type='video'")}
            rows = list(conn.execute(
                "SELECT file_id, file_path, file_size, rsession_id FROM files "
                "WHERE file_type='video' AND analysis_status='ok' "
                "AND uuid_ts_ms < ?", (cutoff_ms,)))
        cand = [r for r in rows
                if f"{r['rsession_id']}:video:{Path(r['file_path']).stem}"
                not in key_refs]
        if not cand:
            log.debug("video prune idle", cutoff_hours=hours)
            return
        t0 = time.perf_counter()
        deleted: list[int] = []
        freed, n_skip = 0, 0
        with self.store.connect(readonly=False) as conn:
            for i in range(0, len(cand), 500):
                for r in cand[i:i + 500]:
                    # 复核（同连接读最新已提交态）：候选在选中后被改判 → 跳过
                    now = conn.execute(
                        "SELECT analysis_status FROM files WHERE file_id=?",
                        (r["file_id"],)).fetchone()
                    if now is None or now[0] != "ok":
                        n_skip += 1
                        log.debug("video prune candidate changed, skipped",
                                  file_id=r["file_id"],
                                  status=now[0] if now else None)
                        continue
                    ref = (f"{r['rsession_id']}:video:"
                           f"{Path(r['file_path']).stem}")
                    if conn.execute("SELECT 1 FROM events WHERE event_ref=?",
                                    (ref,)).fetchone():
                        n_skip += 1
                        log.debug("video prune candidate keyed, skipped",
                                  file_id=r["file_id"])
                        continue
                    p = Path(r["file_path"]).resolve()
                    if not p.is_relative_to(root):
                        n_skip += 1
                        log.warning("video prune path outside root, skipped",
                                    path=r["file_path"])
                        continue
                    try:
                        p.unlink(missing_ok=True)
                    except OSError as e:
                        log.warning("video prune unlink failed", path=str(p),
                                    error=str(e))
                        continue
                    conn.execute("DELETE FROM files WHERE file_id = ?",
                                 (r["file_id"],))
                    deleted.append(r["file_id"])
                    freed += r["file_size"] or 0
                conn.commit()
        log.info("video prune done", files=len(deleted), bytes=freed,
                 skipped=n_skip, cutoff_hours=hours,
                 elapsed_s=round(time.perf_counter() - t0, 1))

    # ── 对外状态（get_status 数据源）───────────────────────────────────────

    def status(self) -> dict:
        try:
            sync_health = self.pipeline.recording_watch.health()
        except Exception:  # noqa: BLE001 — health 失败不阻断状态输出
            sync_health = {}
        try:
            with self.store.connect() as conn:
                row = conn.execute(
                    "SELECT last_flush_at FROM flush_state WHERE id = 1"
                ).fetchone()
                last_flush = row["last_flush_at"] if row else None
        except (sqlite3.OperationalError, StoreError):
            last_flush = None
        return {
            "last_poll_at": self.last_poll_at,
            "poll_interval_s": self.cfg.watcher.poll_interval_s,
            "pipeline_running": self.pipeline_running,
            "started": self.started,
            "round_pending": self._round_queued,
            "pending_files": self._count_pending_files(),
            "last_flush_at": last_flush,  # flush 门控水印（§5.C）
            "sync": sync_health,  # sync lag + chat 滞留（§2.2 健康口径）
        }
