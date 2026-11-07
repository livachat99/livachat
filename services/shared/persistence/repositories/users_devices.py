from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import Device, User


class UsersDevicesRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert_user(self, *, external_id: str | None = None, display_name: str | None = None) -> User:
        values: dict[str, Any] = {"display_name": display_name, "locale": "zh-CN"}
        if external_id:
            values["external_id"] = external_id
        stmt = (
            insert(User)
            .values(**values)
            .on_conflict_do_update(index_elements=["external_id"], set_={"display_name": display_name})
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def upsert_device(
        self,
        *,
        device_uuid: str,
        user_id: UUID | None = None,
        platform: str | None = None,
        client_version: str | None = None,
    ) -> Device:
        stmt = (
            insert(Device)
            .values(
                device_uuid=device_uuid,
                user_id=user_id,
                platform=platform,
                client_version=client_version,
                last_seen_at=datetime.now(tz=UTC),
            )
            .on_conflict_do_update(
                index_elements=["device_uuid"],
                set_={"last_seen_at": datetime.now(tz=UTC), "client_version": client_version},
            )
            .returning(Device)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_user_by_external_id(self, external_id: str) -> User | None:
        stmt = select(User).where(User.external_id == external_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_device_by_uuid(self, device_uuid: str) -> Device | None:
        stmt = select(Device).where(Device.device_uuid == device_uuid)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
