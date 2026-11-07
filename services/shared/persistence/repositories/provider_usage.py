from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import ProviderUsage


class ProviderUsageRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def insert(self, usage: ProviderUsage) -> ProviderUsage:
        self._session.add(usage)
        await self._session.flush()
        return usage
