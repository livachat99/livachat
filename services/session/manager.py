from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from session.context import SessionContext
from session.history import WorkingMemory
from session.state_machine import SessionState
from shared.observability.logging import get_logger
from shared.timeutil import canonicalize_timezone
from shared.utils.ids import generate_session_id

# structlog BoundLogger：kwargs 进事件字典；标准 logging 的 extra 会被 structlog
# foreign_pre_chain 丢弃（restored/resume_id 等诊断字段打不出来，历史教训）
logger = get_logger("livachat.session.manager")

# chat store restore 上限 = WorkingMemory(max_turns=10) × 2（manager.start 内建）
_RESTORE_HISTORY_LIMIT = 20

# timezone-support.md §5.2：时间戳用 SQL 侧 strftime，与列 DEFAULT 同源（ddl.py:80-88）
_UPSERT_TIMEZONE_SQL = (
    "INSERT INTO timezones (timezone) VALUES (?) "
    "ON CONFLICT(timezone) DO UPDATE SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
)


def _upsert_timezone(db_path: str, tz: str) -> None:
    """timezones 表单行 upsert（§5.2/§5.3 纪律）。任何异常只 log，不抛出。"""
    try:
        if not Path(db_path).exists():
            logger.info("lvdata db not found, skip timezone upsert", db_path=db_path)
            return
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            # 不重复设置 journal_mode=WAL：WAL 是库级持久属性，重复设置会与
            # lvdata-mcp 争短暂独占锁（job_persistence.py:103-111 教训）
            conn.execute(_UPSERT_TIMEZONE_SQL, (tz,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("timezone upsert failed", exc_info=True)


class SessionManager:
    """Creates, restores, and terminates sessions (design §4.1, §4.5)."""

    def __init__(self, lvdata_db_path: str | None = None,
                 chat_store: Any | None = None) -> None:
        self._lvdata_db_path = lvdata_db_path
        self._chat_store = chat_store
        # fire-and-forget 任务句柄集合：done callback 移除，防事件循环提前 GC（§11.10）
        self._bg_tasks: set[asyncio.Task] = set()
        # 设备级视频采集轮记忆（fd_pipeline_video_process.md §8.6.3）：跨连接/跨会话延续，
        # 首帧判定基准——turn 变化 = 真实用户动作（开/切相机）。进程内存、键=device_id
        # （App CaptureTurnCounter 为进程级单例、跨会话不重置）
        self._last_video_turn_by_device: dict[str, int] = {}

    def record_video_turn(self, device_id: str, turn_id: int) -> None:
        """更新设备最近视频采集轮（orchestrator 于 turn 变化时写回，与是否触发解耦）。"""
        self._last_video_turn_by_device[device_id] = turn_id

    def last_video_turn(self, device_id: str) -> int | None:
        """设备最近视频采集轮；无记录 → None（orchestrator 首帧按"变化"触发）。"""
        return self._last_video_turn_by_device.get(device_id)

    async def start(
        self,
        *,
        device_id: str,
        user_id: str = "00000000-0000-0000-0000-000000000001",
        enable_video: bool = False,
        audio_sample_rate: int = 16000,
        vad_sensitivity: str = "medium",
        client_version: str = "",
        language: str | None = None,
        timezone: str | None = None,
        resume_id: str | None = None,
        avdetector_enabled: bool = True,
    ) -> tuple[SessionContext, WorkingMemory]:
        """Start a new session or resume an existing one.

        Returns ``(ctx, history)``.

        去 Redis 后（chatserver_no_redis.md §3.4/D1）：resume 统一走 chat store
        restore——`restored` 恒 False、`epoch` 恒 0（epoch 仅在单连接生命周期
        内做任务失效比对，归零安全）；`audio_sample_rate`/`enable_video`/
        `vad_sensitivity`/`user_id`（以及 App UI `language`）不再从快照恢复——
        客户端每次 session.start 携带完整 config，行为不变。
        """
        history = WorkingMemory(max_turns=10)
        epoch = 0
        restored = False

        # timezone 归一化（§5.1）：无效 → 忽略 + info log，不影响会话建立
        canonical_tz = canonicalize_timezone(timezone)
        if timezone and canonical_tz is None:
            logger.info("invalid timezone in session.start, ignored", timezone=timezone)
        if canonical_tz is not None and self._lvdata_db_path:
            task = asyncio.create_task(
                asyncio.to_thread(_upsert_timezone, self._lvdata_db_path, canonical_tz)
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

        session_id: str = resume_id or generate_session_id()

        # chat store restore 最近一天 WorkingMemory history（chatserver_sqlite_store.md
        # §3.6；唯一恢复路径——客户端带 resume_id 时 session_id 延续，WorkingMemory
        # 从 messages 表恢复最近一天 ≤20 条）。best-effort：失败只 log，不阻断。
        if self._chat_store is not None:
            try:
                rows = await asyncio.to_thread(
                    self._chat_store.load_day,
                    device_uuid=device_id,
                    timezone=canonical_tz or "",
                    limit=_RESTORE_HISTORY_LIMIT,
                )
                for row in rows:
                    kind = row.get("kind")
                    if kind == "asr":
                        history.add_message("user", row["content"],
                                            images=row.get("images"))
                    elif kind == "llm":
                        history.add_message("assistant", row["content"])
                    elif kind == "agent_job":
                        history.add_message("assistant", row["content"],
                                            source="agent_job", ref_id=row.get("ref_id", ""))
                if not history.is_empty:
                    logger.info(
                        "restored working memory from chat store",
                        session_id=session_id,
                        device_id=device_id,
                        restored_count=len(history),
                    )
            except Exception:
                logger.warning("chat store history restore failed", exc_info=True)

        ctx = SessionContext(
            session_id=session_id,
            device_id=device_id,
            user_id=user_id,
            enable_video=enable_video,
            audio_sample_rate=audio_sample_rate,
            vad_sensitivity=vad_sensitivity,
            client_version=client_version,
            language=language or "",
            timezone=canonical_tz or "",
            avdetector_enabled=avdetector_enabled,
            epoch=epoch,
            restored=restored,
        )

        # Transition to LISTENING
        ctx.sm.transition(SessionState.LISTENING)

        # Fire-and-forget PG write (handled by orchestrator in Phase 9)
        logger.info(
            "session started",
            session_id=session_id,
            restored=restored,
            resume_id=resume_id or "",
            enable_video=enable_video,
            language=language or "",
            timezone_raw=timezone or "",
            timezone=ctx.timezone,
        )

        return ctx, history

    async def end(self, ctx: SessionContext, reason: str) -> None:
        """Terminate a session."""
        ctx.sm.transition(SessionState.IDLE)

        logger.info("session ended", session_id=ctx.session_id, reason=reason)
