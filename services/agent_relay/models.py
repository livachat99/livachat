"""Internal message models for Redis Pub/Sub payloads.

See §4.3 of the design doc for the wire schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Events: orchestrator/recording → relay (livachat:agent:events)
# ---------------------------------------------------------------------------


class AgentEvent(BaseModel):
    """Wire payload published to the agent events channel."""

    kind: Literal["user.utterance", "recording.ready", "session.started", "session.ended"]
    device_id: str
    session_id: str
    ts_ms: int

    # -- user.utterance -------------------------------------------------------
    transcription_id: str | None = None
    text: str | None = None
    # user.utterance：ASR 检测语言（诊断元数据，不作为 agent 目标语言）；
    # session.started：App UI 语言（空/未知由 relay 归一为 "en"，agent 用户
    # 可见输出的基线语言；docs/agent_multilingual.md）。
    language: str | None = None

    # -- session.started ------------------------------------------------------
    # 设备本机时区（`session.start` 上报、服务端 canonicalize 后的规范形；未上报/非法 → None）
    # ——relay 用它渲染 mission/background prompt 的时区提示（docs/agent_relay_prompts.md）。
    timezone: str | None = None

    # -- recording.ready ------------------------------------------------------
    recording_id: str | None = None
    manifest_path: str | None = None
    duration_ms: int | None = None

    @classmethod
    def from_json(cls, raw: str | bytes) -> AgentEvent:
        return cls.model_validate_json(raw)


# ---------------------------------------------------------------------------
# Push messages: relay → gateway (livachat:push:{instance_id})
# ---------------------------------------------------------------------------


class PushMessage(BaseModel):
    """Wire payload published to a gateway instance's push channel."""

    kind: Literal["ai_text"] = "ai_text"
    device_id: str
    session_id: str
    message_id: str
    text: str
    index: int = 0
    is_final: bool = True
    source: Literal["agent"] = "agent"
    agent_name: str = ""
    agent_id: str = ""   # Agent 昵称（adapter 配置 nickname）；未配置恒 ""
    reply_to: str | None = None
    proactive: bool = False
    job_key: str | None = None   # 存在 ⇒ agent job 结果（D6 message_id 的来源 job）

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


class JobStatusItem(BaseModel):
    """One running background job in a job_status snapshot."""

    job_id: str
    agent_name: str = "hermes"
    summary: str = ""
    started_at_ms: int = 0
    running_time_ms: int = 0


class JobStatusPushMessage(BaseModel):
    """job_status snapshot payload (kind=job_status) — distinct from
    PushMessage: carries the running-jobs array, no session/text fields."""

    kind: Literal["job_status"] = "job_status"
    device_id: str
    jobs: list[JobStatusItem] = []

    def to_json(self) -> str:
        return self.model_dump_json()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_user_utterance_event(
    *,
    device_id: str,
    session_id: str,
    ts_ms: int,
    transcription_id: str,
    text: str,
    language: str = "",
) -> AgentEvent:
    return AgentEvent(
        kind="user.utterance",
        device_id=device_id,
        session_id=session_id,
        ts_ms=ts_ms,
        transcription_id=transcription_id,
        text=text,
        language=language,
    )


def make_recording_ready_event(
    *,
    device_id: str,
    session_id: str,
    ts_ms: int,
    recording_id: str,
    manifest_path: str,
    duration_ms: int,
) -> AgentEvent:
    return AgentEvent(
        kind="recording.ready",
        device_id=device_id,
        session_id=session_id,
        ts_ms=ts_ms,
        recording_id=recording_id,
        manifest_path=manifest_path,
        duration_ms=duration_ms,
    )


def make_session_started_event(
    *,
    device_id: str,
    session_id: str,
    ts_ms: int,
    timezone: str | None = None,
    language: str = "",
) -> AgentEvent:
    return AgentEvent(
        kind="session.started",
        device_id=device_id,
        session_id=session_id,
        ts_ms=ts_ms,
        timezone=timezone,
        language=language,
    )


def make_session_ended_event(
    *,
    device_id: str,
    session_id: str,
    ts_ms: int,
) -> AgentEvent:
    return AgentEvent(
        kind="session.ended",
        device_id=device_id,
        session_id=session_id,
        ts_ms=ts_ms,
    )
