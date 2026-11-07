from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import Message


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def insert(self, msg: Message) -> Message:
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def insert_all(self, messages: list[Message]) -> list[Message]:
        self._session.add_all(messages)
        await self._session.flush()
        return messages

    async def get_by_session(self, session_id: str, *, limit: int = 50) -> list[Message]:
        stmt = select(Message).where(Message.session_id == session_id).order_by(Message.turn_index).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
