from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import Transcription


class TranscriptionsRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def insert(self, trans: Transcription) -> Transcription:
        self._session.add(trans)
        await self._session.flush()
        return trans
