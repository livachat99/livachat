"""AgentJobsRepository — full-state upsert into agent_jobs (PG 镜像).

fd_agent_job_pg_persistence.md §4.3: 单行一 job 最新状态；`updated_at` 单调守卫
（D3）兜住一切乱序写入方（队列外调度、reconcile 竞态、未来多实例）。
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import AgentJob


class AgentJobsRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, row: dict) -> None:
        """full-state upsert + 单调守卫（D3）：
        INSERT .. ON CONFLICT (job_key) DO UPDATE ..
            WHERE agent_jobs.updated_at <= excluded.updated_at
        row 为 JobPgWriter._to_row 产物（不含 id/created_at——created_at 由
        DEFAULT now() 首插、冲突时保留）。守卫丢弃后到的旧转换写入。"""
        stmt = insert(AgentJob).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["job_key"],
            set_={k: stmt.excluded[k] for k in row if k != "job_key"},
            where=(AgentJob.updated_at <= stmt.excluded.updated_at),
        )
        await self._session.execute(stmt)
        # 必须显式提交：async with session 退出 close 即回滚
        await self._session.commit()
