"""Tool 输入参数 pydantic 模型（§12 v2 集合：上层只面向 events + day_summary）。"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EventType = Literal["chat", "transcribe", "translate", "audio", "window",
                    "video", "agent_job"]


class SearchParams(BaseModel):
    """search：全文检索（§10：日期窗 + timezone，无 session 维度）。"""

    query: str = Field(min_length=1, max_length=200)
    type: EventType | None = None
    start_date: str | None = None          # YYYY-MM-DD（含端点）；缺省 = 不限
    end_date: str | None = None            # YYYY-MM-DD（含端点）；缺省 = 不限
    limit: int = Field(10, ge=1, le=100)
    timezone: str | None = None            # 无效/为空 → 服务配置时区（§8.7）


class SemanticSearchParams(BaseModel):
    """semantic_search：入参与 search 对齐（§11；with_context 在函数签名上）。"""

    query: str = Field(min_length=1, max_length=200)
    type: EventType | None = None
    start_date: str | None = None          # YYYY-MM-DD（含端点）；缺省 = 不限
    end_date: str | None = None            # YYYY-MM-DD（含端点）；缺省 = 不限
    limit: int = Field(10, ge=1, le=100)
    timezone: str | None = None            # 无效/为空 → 服务配置时区（§8.7）


class TimelineParams(BaseModel):
    start_date: str | None = None          # YYYY-MM-DD（含端点）；缺省 = 不限
    end_date: str | None = None            # YYYY-MM-DD（含端点）；缺省 = 不限
    types: list[EventType] | None = Field(None, min_length=1)   # 空数组 ≠ 不传（I-7）
    limit: int = Field(200, ge=1, le=1000)
    cursor: str | None = None
    timezone: str | None = None            # 无效/为空 → 服务配置时区（§8.7）


class EventParams(BaseModel):
    event_ref: str = Field(min_length=10, max_length=200)
    timezone: str | None = None            # 无效/为空 → 服务配置时区（§8.7）


class TriggerParams(BaseModel):
    session_id: str | None = None
    stages: list[Literal["inventory", "chatlog", "audio", "video",
                         "reindex", "embed", "day_summary",
                         "day_stats"]] | None = Field(
        None, min_length=1)
    force: bool = False

    @field_validator("session_id")
    @classmethod
    def _valid_session_id(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                uuid.UUID(v)
            except (ValueError, TypeError, AttributeError):
                raise ValueError(f"session_id must be a UUID: {v}")
        return v


class RecentEventsParams(BaseModel):
    """get_recent_events：近 N 个本地日（含今天），days=1 即今天。"""

    days: int = Field(1, ge=1, le=31)
    timezone: str | None = None            # 缺省 = 服务默认时区


class RemoveParams(BaseModel):
    """events remove：删除事件（docs/bff_service_remove.md §2.2）。

    目前只支持 video（其余逐条 skip，不整批拒绝——部分成功语义 D7）。
    """

    event_refs: list[str] = Field(min_length=1, max_length=50)

    @field_validator("event_refs")
    @classmethod
    def _nonempty_refs(cls, v: list[str]) -> list[str]:
        for ref in v:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("event_refs elements must be non-empty strings")
        return v
