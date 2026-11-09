"""Pipeline 编排（v2，§6.7）：进程内阶段执行，无 subprocess。

stage 集合：inventory / chatlog / audio / video / reindex / embed / day_summary /
day_stats（docs/bff_service_interfaces.md §7：日卡派生字段预计算，本地零 LLM）。
cs 单链路（recording_data_process.md）：inventory = cs.recording_files 增量同步
（fs 扫盘与 agent_jobs 同步已删除；agent job 事件由 chatlog pass 铸造）。
target 语义推广：rsession_id / "tz:local_date" / None（全局）；day_stats 忽略
target（始终全局，§7.3 E15）。运行记录写 mcp_pipeline_runs（session_id 列随 §7
改名 target）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import structlog

from . import index as index_mod
from .config import LvdataMcpConfig
from .recording_watch import RecordingWatch
from .store import DataStore
from .timeutil import now_iso
from .vec import VecIndex

log = structlog.get_logger("lvdata_mcp.pipeline")

STAGES = ("inventory", "chatlog", "audio", "video", "reindex", "embed",
          "day_summary", "day_stats")


class PollBudget:
    """单轮 poll 的分析文件预算（§10.2 max_files_per_poll 全局护栏）。

    poll loop 每轮重置，audio/vision 取批时消费——护栏语义是「单轮 poll
    内 audio/vision 各处理的最大文件数」，跨 session 共享；0/负 = 不限。
    """

    def __init__(self, limit: int = 0):
        self.reset(limit)

    def reset(self, limit: int) -> None:
        self.limit = limit
        self.remaining: int | None = None if limit <= 0 else limit

    def take(self, n: int) -> int:
        """批准 n 个中的前 k 个（预算不足时截断）；返回实际批准数。"""
        if self.remaining is None:
            return n
        granted = min(self.remaining, n)
        self.remaining -= granted
        return granted


class Pipeline:
    def __init__(self, cfg: LvdataMcpConfig, store: DataStore,
                 recording_watch: RecordingWatch,
                 vec_index: VecIndex | None = None,
                 audio: Any | None = None, vision: Any | None = None,
                 day_summary: Any | None = None, chatlog: Any | None = None,
                 day_stats: Any | None = None):
        self.cfg = cfg
        self.store = store
        self.recording_watch = recording_watch
        self.vec_index = vec_index
        self.audio = audio          # P2 注入
        self.vision = vision        # P2 注入
        self.day_summary = day_summary  # P3 注入
        self.chatlog = chatlog      # chat 三 jsonl → events
        self.day_stats = day_stats  # §7：日卡派生字段预计算（本地零 LLM）

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.store.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mcp_pipeline_runs ("
            "run_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "target TEXT,"
            "stage TEXT NOT NULL,"
            "started_at TEXT NOT NULL,"
            "finished_at TEXT,"
            "status TEXT NOT NULL DEFAULT 'running',"
            "exit_code INTEGER,"
            "error TEXT)")
        return conn

    async def run_stage(self, stage: str, target: str | None = None,
                        force: bool = False) -> dict:
        if stage not in STAGES:
            return {"status": "failed", "error": f"Unknown stage: {stage}"}
        run_id = self._record_start(stage, target)
        try:
            result = await self._dispatch(stage, target, force)
            self._record_end(run_id, 0, None)
            return {"run_id": run_id, "status": "ok", **result}
        except Exception as e:  # noqa: BLE001 — 编排层兜底，任何异常都记录
            self._record_end(run_id, -1, str(e)[:500])
            log.exception("stage failed", stage=stage, target=target)
            return {"run_id": run_id, "status": "failed", "error": str(e)[:500]}

    async def _dispatch(self, stage: str, target: str | None,
                        force: bool) -> dict:
        if stage == "inventory":
            # cs.recording_files 增量同步（ATTACH 只读 + SQLite 写），
            # to_thread 不阻塞事件循环（MCP 请求同进程服务）
            return await asyncio.to_thread(self.recording_watch.check,
                                           force=force)
        if stage == "chatlog":
            if self.chatlog is None:
                return {"processed": 0}
            # target = rsession_id 限定（trigger_analysis 显式指定时）
            return await asyncio.to_thread(self.chatlog.process, target, force)
        if stage == "audio":
            if self.audio is None:
                return {"processed": 0}  # P2 注入分析模块
            return await self.audio.process(target, force)
        if stage == "video":
            if self.vision is None:
                return {"processed": 0}
            return await self.vision.process(target, force)
        if stage == "reindex":
            n = await asyncio.to_thread(self._reindex, target, force)
            return {"docs": n}
        if stage == "day_stats":
            if self.day_stats is None:
                return {"days": 0}  # 未注入（测试/最小装配）
            # 本地 SQL + 纯内存派生：to_thread 不阻塞事件循环；target 忽略（E15）
            return await asyncio.to_thread(self.day_stats.run, target, force)
        if stage == "embed":
            if self.vec_index is None:
                return {"chunks": 0}
            if force:
                # trigger(embed, force) = 全量重嵌（同步 vec_state）
                n = await self.vec_index.rebuild_all()
            elif target is None:
                n = await self.vec_index.sync_changed()
            else:
                n = await self.vec_index.rebuild_session(target)
            return {"chunks": n}
        if stage == "day_summary":
            if self.day_summary is None:
                return {"days": 0}  # P3 注入
            # 先 discover 再 generate：day_summary 位于 round 序列末尾，本步
            # discover 能看到**本轮新产生的 events**（chatlog/音视频）→ 单轮
            # 完整（rebuild 一轮到位）；幂等，重复 discover 无副作用
            await asyncio.to_thread(self.day_summary.discover)
            return await self.day_summary.generate(target, force)
        return {"status": "failed", "error": f"Unknown stage: {stage}"}

    def _reindex(self, target: str | None, force: bool = False) -> int:
        """FTS 重建：target=None → 增量（仅滞后会话，index.lag_sessions 取件）；
        target=session → 指定会话；force=True → 全量（rebuild_all）。

        水位（index_state）唯一写入点，成功才写。T0 取在入口（先于取件与任何
        events 读取）——快照语义：写出的水位必须 ≤ 其声称覆盖的所有读取时刻；
        写入值 = T0−1ms（闭合同毫秒边界，见 index.set_watermark）；'' 只由
        全量重建写，且全量同事务清会话行（retention 方案 §2.2）。
        """
        with self._conn() as conn:
            index_mod.ensure_index(conn)
            t0 = now_iso()
            if force:
                n = index_mod.rebuild_all(conn, self.store)
                index_mod.set_watermark(conn, "", t0)
                index_mod.clear_session_watermarks(conn)
            elif target is None:
                sessions = index_mod.lag_sessions(conn)
                n = sum(index_mod.rebuild_session(conn, self.store, s)
                        for s in sessions)
                for s in sessions:
                    index_mod.set_watermark(conn, s, t0)
            else:
                n = index_mod.rebuild_session(conn, self.store, target)
                index_mod.set_watermark(conn, target, t0)
            conn.commit()
            return n

    def reset_budget(self) -> None:
        """poll loop 每轮调用：重置 audio/vision 共享的文件预算（§10.2）。"""
        for mod in (self.audio, self.vision):
            if mod is not None and hasattr(mod, "budget"):
                mod.budget.reset(self.cfg.watcher.max_files_per_poll)

    def _record_start(self, stage: str, target: str | None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO mcp_pipeline_runs (target, stage, started_at) "
                "VALUES (?, ?, ?)", (target, stage, now_iso()))
            conn.commit()
            return cur.lastrowid

    def _record_end(self, run_id: int, exit_code: int | None,
                    error: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE mcp_pipeline_runs SET finished_at = ?, status = ?, "
                "exit_code = ?, error = ? WHERE run_id = ?",
                (now_iso(), "ok" if exit_code == 0 else "failed",
                 exit_code, error, run_id))
            conn.commit()
