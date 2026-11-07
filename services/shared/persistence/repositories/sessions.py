from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import Session


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, sess: Session) -> Session:
        self._session.add(sess)
        await self._session.flush()
        return sess

    async def get_by_session_id(self, session_id: str) -> Session | None:
        stmt = select(Session).where(Session.session_id == session_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def end_session(self, session_id: str, end_reason: str, *, ended_at: datetime | None = None) -> None:
        sess = await self.get_by_session_id(session_id)
        if sess is not None:
            sess.status = "ended"
            sess.end_reason = end_reason
            sess.ended_at = ended_at or datetime.now(UTC)
            await self._session.flush()

    async def increment_resumed_count(self, session_id: str) -> None:
        sess = await self.get_by_session_id(session_id)
        if sess is not None:
            sess.resumed_count += 1
            await self._session.flush()
