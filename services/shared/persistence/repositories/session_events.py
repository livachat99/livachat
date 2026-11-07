from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import SessionEvent


class SessionEventsRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def insert(self, event: SessionEvent) -> SessionEvent:
        self._session.add(event)
        await self._session.flush()
        return event
