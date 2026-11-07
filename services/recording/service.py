"""RecordingService — process-wide registry + session lifecycle + ticker."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime
from typing import Any

from pathlib import Path

from recording.file_registry import RecordingFileRegistry
from recording.session import RecordingSession
from shared.config.models import RecordingConfig

logger = logging.getLogger("livachat.recording")


def _utc_today() -> date:
    """Return the current UTC date (design §1.2: UTC natural day boundary)."""
    return datetime.now(tz=UTC).date()


class RecordingService:
    """Process-wide registry of per-device RecordingSessions.

    Coordinates session creation (L1 flow), day rollover, idle cleanup,
    and graceful shutdown.
    """

    def __init__(
        self,
        *,
        chat_store: Any = None,
        config: RecordingConfig,
        agent_publisher: Any = None,
    ):
        self._chat_store = chat_store
        self._cfg = config
        self._agent_publisher = agent_publisher
        self._sessions: dict[str, RecordingSession] = {}  # device_uuid → session
        self._ticker_task: asyncio.Task[None] | None = None
        self._started = False

    # -- public API -------------------------------------------------------

    async def start(self) -> None:
        self._started = True
        self._ticker_task = asyncio.create_task(self._ticker())

    async def acquire(self, device_uuid: str) -> RecordingSession:
        """Get or create today's recording session for device_uuid (L1 flow)."""
        today = _utc_today()

        # ① Try memory cache
        existing = self._sessions.get(device_uuid)
        if existing is not None:
            # Cross-day: finalize old session before creating a new one
            if existing.session_date != str(today):
                logger.info(
                    "cross-day reconnect, finalizing old recording session",
                    extra={"recording_session_id": existing.recording_session_id},
                )
                await existing.finalize()
                self._sessions.pop(device_uuid, None)
            else:
                if existing.status == "idle":
                    await existing.attach()
                return existing

        # ② Try chat store（recording_session_id 事实来源；get-or-create 返回
        #    权威行——断线重连自动命中既有行，原 Redis L1② 恢复分支冗余删除）
        session = await self._find_or_create(device_uuid, today)
        session.agent_publisher = self._agent_publisher
        if self._chat_store is not None:
            session.file_registry = RecordingFileRegistry(
                self._chat_store, Path(self._cfg.base_dir),
                device_uuid=device_uuid,
                recording_session_id=session.recording_session_id,
            )
        self._sessions[device_uuid] = session
        await session.start()
        return session

    async def shutdown(self) -> None:
        """Flush and finalize all sessions (called during graceful shutdown)."""
        self._started = False
        if self._ticker_task is not None:
            self._ticker_task.cancel()
            try:
                await self._ticker_task
            except asyncio.CancelledError:
                pass

        for device_uuid, session in list(self._sessions.items()):
            try:
                await session.finalize()
            except Exception:
                logger.warning(
                    "recording shutdown flush failed",
                    extra={"device_uuid": device_uuid},
                    exc_info=True,
                )

        logger.info("recording service shut down")

    # -- ticker -----------------------------------------------------------

    async def _ticker(self) -> None:
        """周期清理任务（跨天 finalize + idle TTL）。

        session.json 静态化后无 meta flush 职责（session_json_refactor §3.A.6），
        `meta_flush_interval_s` 现语义 = ticker 周期。
        """
        while self._started:
            await asyncio.sleep(self._cfg.meta_flush_interval_s)
            try:
                await self._tick()
            except Exception:
                logger.warning("recording ticker error", exc_info=True)

    async def _tick(self) -> None:
        today = _utc_today()
        now_mono = time.monotonic()

        to_remove: list[str] = []
        for device_uuid, session in list(self._sessions.items()):
            # Day rollover
            if session.session_date != str(today):
                logger.info(
                    "day rollover detected, finalizing recording session",
                    extra={"recording_session_id": session.recording_session_id},
                )
                await session.finalize()
                to_remove.append(device_uuid)
                continue

            # Idle session TTL (use monotonic clock delta from detach time)
            if session.status == "idle" and session._detached_at is not None \
                    and (now_mono - session._detached_at) > self._cfg.idle_session_ttl_s:
                    logger.info(
                        "idle recording session expired",
                        extra={"recording_session_id": session.recording_session_id},
                    )
                    await session.finalize()
                    to_remove.append(device_uuid)

        for device_uuid in to_remove:
            self._sessions.pop(device_uuid, None)

    # -- chat store helpers（recording_session_id 事实来源，chatserver_sqlite_store.md §4.3）--

    async def current_recording_info(self, device_uuid: str) -> dict | None:
        """{recording_session_id, base_path}；内存 registry 优先，recording_sessions
        表只读兜底（§3.7）。只读方法——不得 get-or-create。"""
        session = self._sessions.get(device_uuid)
        if session is not None and session.session_date == str(_utc_today()):
            return {
                "recording_session_id": session.recording_session_id,
                "base_path": str(session.storage_path),
            }
        if self._chat_store is None:
            return None
        try:
            row = await self._chat_store.get_recording_session(
                device_uuid=device_uuid, session_date=str(_utc_today()),
            )
        except Exception:
            logger.warning("recording lookup failed", exc_info=True)
            return None
        if row is None:
            return None
        return {
            "recording_session_id": row["recording_session_id"],
            "base_path": row["base_path"],
        }

    async def _find_or_create(
        self, device_uuid: str, session_date: date,
    ) -> RecordingSession:
        """按 (device_uuid, session_date) get-or-create；表内已有今日行 → 返回权威
        id 重建；无 → 插入并返回新 id。目录命名规则不变（§4.3）。"""
        from pathlib import Path

        from shared.utils.ids import uuid7

        base_path = Path(self._cfg.base_dir) / device_uuid
        recording_id = uuid7()
        storage_path = base_path / recording_id

        if self._chat_store is None:
            logger.warning("chat store unavailable — running in local-only mode")
            return RecordingSession(
                recording_session_id=recording_id,
                device_uuid=device_uuid,
                session_date=str(session_date),
                storage_path=storage_path,
                config=self._cfg.model_dump(),
            )

        try:
            row = await self._chat_store.get_or_create_recording_session(
                device_uuid=device_uuid,
                session_date=str(session_date),
                recording_session_id=recording_id,
                base_path=str(storage_path),
            )
        except Exception:
            logger.warning("recording store get-or-create failed — running in local-only mode", exc_info=True)
            row = {"recording_session_id": recording_id, "base_path": str(storage_path)}
        return RecordingSession(
            recording_session_id=row["recording_session_id"],
            device_uuid=device_uuid,
            session_date=str(session_date),
            storage_path=Path(row["base_path"]),
            config=self._cfg.model_dump(),
        )
