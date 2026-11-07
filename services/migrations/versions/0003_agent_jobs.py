"""Agent jobs — PG 镜像表（Job 生命周期持久化，fd_agent_job_pg_persistence.md §4.1）。

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        # relay uuid（upsert 冲突键、跨系统关联键）
        sa.Column("job_key", sa.Text(), nullable=False),
        # 预留多 adapter；当前恒 hermes
        sa.Column("agent_name", sa.Text(), server_default="hermes", nullable=False),
        # = relay device_id（客户端 UUID v4）
        sa.Column("device_uuid", sa.Text(), nullable=False),
        # livachat session（追溯/关联）
        sa.Column("session_id", sa.Text(), nullable=False),
        # 外部关联键：hermes session:<stored_session_id> / openclaw runId；NULL = 未 running
        sa.Column("hermes_task_id", sa.Text(), nullable=True),
        # requested|running|done|delivered|failed
        sa.Column("status", sa.Text(), nullable=False),
        # 一行摘要 ≤256 字符（源头已截断）
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        # done 成品文案 ≤16KB；NULL = 无结果（NOOP/error 消费）
        sa.Column("result_text", sa.Text(), nullable=True),
        # timeout|orphaned|rpc_error|session_lost
        sa.Column("fail_reason", sa.Text(), nullable=True),
        # 进入 requested（= record.created_at）
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        # 进入 running；NULL = 从未 running
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        # 终态时刻（D6 派生：completed_at > 0 取之；failed 取失败转换的 updated_at）
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # 投递成功；NULL = 未投递
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        # 行首次插入时间（PG 侧，DEFAULT now()；upsert 冲突时不得覆盖）
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # = 源 record.updated_at（upsert 每次显式写入，是 D3 守卫的比较基准）
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint("uq_agent_jobs_job_key", "agent_jobs", ["job_key"])
    op.create_index("idx_agent_jobs_device_time", "agent_jobs", ["device_uuid", "requested_at"])
    op.create_index("idx_agent_jobs_status_finished", "agent_jobs", ["status", "finished_at"])
    op.create_index("idx_agent_jobs_task", "agent_jobs", ["hermes_task_id"])
    op.create_index("idx_agent_jobs_session", "agent_jobs", ["session_id"])


def downgrade() -> None:
    op.drop_table("agent_jobs")
