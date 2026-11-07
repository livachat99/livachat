from __future__ import annotations

import uuid as _uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[_uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str | None] = mapped_column(Text, default="zh-CN", nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    devices: Mapped[list[Device]] = relationship(back_populates="user")


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[_uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4)
    device_uuid: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    user_id: Mapped[_uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    platform: Mapped[str | None] = mapped_column(Text)
    client_version: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped[User | None] = relationship(back_populates="devices")

    __table_args__ = (Index("idx_devices_user", "user_id"),)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    user_id: Mapped[_uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    device_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, default="active")
    enable_video: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_sample_rate: Mapped[int] = mapped_column(Integer)
    vad_sensitivity: Mapped[str | None] = mapped_column(Text)
    client_version: Mapped[str | None] = mapped_column(Text)
    asr_provider: Mapped[str | None] = mapped_column(Text)
    llm_provider: Mapped[str | None] = mapped_column(Text)
    tts_provider: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)
    resumed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    messages: Mapped[list[Message]] = relationship(back_populates="session", cascade="all, delete-orphan")
    transcriptions: Mapped[list[Transcription]] = relationship(back_populates="session", cascade="all, delete-orphan")
    events: Mapped[list[SessionEvent]] = relationship(back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_sessions_user_time", "user_id", "started_at"),
        Index("idx_sessions_device", "device_id"),
    )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    has_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    asr_confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[Session] = relationship(back_populates="messages")

    __table_args__ = (Index("idx_messages_session_turn", "session_id", "turn_index"),)


# ---------------------------------------------------------------------------
# Transcriptions
# ---------------------------------------------------------------------------


class Transcription(Base):
    __tablename__ = "transcriptions"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False)
    transcription_id: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[Session] = relationship(back_populates="transcriptions")

    __table_args__ = (Index("idx_trans_session", "session_id"),)


# ---------------------------------------------------------------------------
# Session Events
# ---------------------------------------------------------------------------


class SessionEvent(Base):
    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(Text, ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[Session] = relationship(back_populates="events")

    __table_args__ = (Index("idx_events_session_time", "session_id", "created_at"),)


# ---------------------------------------------------------------------------
# Provider Usage
# ---------------------------------------------------------------------------


class ProviderUsage(Base):
    __tablename__ = "provider_usage"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sessions.session_id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    request_count: Mapped[int] = mapped_column(Integer, default=1)
    input_units: Mapped[int | None] = mapped_column(BIGINT)
    output_units: Mapped[int | None] = mapped_column(BIGINT)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("idx_usage_session", "session_id"),
        Index("idx_usage_kind_time", "kind", "created_at"),
    )


# ---------------------------------------------------------------------------
# Agent jobs (PG 镜像：fd_agent_job_pg_persistence.md §4.2)
# ---------------------------------------------------------------------------


class AgentJob(Base):
    __tablename__ = "agent_jobs"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    job_key: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, default="hermes")
    device_uuid: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    hermes_task_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    result_text: Mapped[str | None] = mapped_column(Text)
    fail_reason: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("job_key", name="uq_agent_jobs_job_key"),
        Index("idx_agent_jobs_device_time", "device_uuid", "requested_at"),
        Index("idx_agent_jobs_status_finished", "status", "finished_at"),
        Index("idx_agent_jobs_task", "hermes_task_id"),
        Index("idx_agent_jobs_session", "session_id"),
    )


# ---------------------------------------------------------------------------
# Phase 2 reserved tables (not written to in Phase 1)
# ---------------------------------------------------------------------------


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    user_id: Mapped[_uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float | None] = mapped_column(Float, default=0.0, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
