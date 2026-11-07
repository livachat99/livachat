from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SchemaVersion = Literal[1]


class FlatEventModel(BaseModel):
    """事件 DTO：extra 字段直通扁平化（响应与 timeline.json 字段同构，前端无需拼路径）。"""

    model_config = ConfigDict(extra="allow")


class ErrorEnvelope(BaseModel):
    error: dict[str, str] = Field(default_factory=dict)


# -- shared -------------------------------------------------------------------


class DayTopicItem(BaseModel):
    """day_summary JSON topics 元素（§9.1.2 / §10：{topic, detail, search_words}）。

    `detail` 为历史字段：读侧 `topic_items` 不再取值，本响应**恒 null**（保留字段零风险，
    §10.5 S2）；`search_words` 是可直接搜到该主题重点事件的原文片段，legacy（str/缺省）
    为 None，客户端回退用 topic 当搜索词。
    """

    topic: str
    detail: str | None = None
    search_words: str | None = None


# -- overview -----------------------------------------------------------------


class HotSlot(BaseModel):
    """活跃时段（§9.1.2；docs/android_timeline_hotslot.md §3.1）：五类可展示事件按 60min
    间隔切段、超 3 段合并至 ≤3（服务端派生，不落库）。

    `count` 保留在 wire 仅为兼容旧 app 的非可选字段（新 app 忽略，chip 只显示 start_ms）。
    """

    start_ms: int
    end_ms: int
    count: int


class DayImage(BaseModel):
    """日卡缩略图（§9.1.2：window 分组轮转选取 → ts 升序输出）。"""

    frame_id: str
    ts_ms: int
    event_ref: str
    media_url: str
    # 帧的窗口归属（Android 跨日轮转的分组键 = window_id ?: event_ref，缺省 null 兼容旧行）
    window_id: str | None = None


class OverviewDay(BaseModel):
    date: str
    counts: dict[str, int] = Field(default_factory=dict)
    # §9.1.2 日卡确定性字段（服务端派生，不落库）
    summary: str | None = None
    hot_slots: list[HotSlot] = Field(default_factory=list)
    images: list[DayImage] = Field(default_factory=list)
    has_pending: bool = False
    topics: list[DayTopicItem] = Field(default_factory=list)  # day_summary JSON topics 透出


class OverviewResponse(BaseModel):
    schema_version: SchemaVersion = 1
    timezone: str
    days: list[OverviewDay] = Field(default_factory=list)
    # §8 分页：next_cursor = 本页最后一天的 local_date（无更早 → None）；total = 该时区天卡总数
    next_cursor: str | None = None
    total: int = 0


# -- timeline -----------------------------------------------------------------


class TimelineEvent(FlatEventModel):
    ts_ms: int
    ts_utc: str | None = None
    type: str
    device_uuid: str
    media_url: str | None = None


class TimelineDay(BaseModel):
    date: str
    events: list[TimelineEvent] = Field(default_factory=list)


class TimelineResponse(BaseModel):
    schema_version: SchemaVersion = 1
    timezone: str
    days: list[TimelineDay] = Field(default_factory=list)
    next_cursor: str | None = None
    total: int = 0


# -- search --------------------------------------------------------------------


class SearchHit(BaseModel):
    date: str
    ts_ms: int | None = None  # FTS 命中恒有 ts；可空仅作防御/兼容旧响应
    kind: str  # 可搜=可展示五类：chat | transcribe | translate | agent_job | video（docs/bff_search_fts.md）
    snippet: str  # 含命中子串的上下文片段（~120 字符）


class SearchResponse(BaseModel):
    schema_version: SchemaVersion = 1
    query: str
    total: int = 0
    next_cursor: str | None = None
    hits: list[SearchHit] = Field(default_factory=list)


# -- graph --------------------------------------------------------------------


class GraphNode(BaseModel):
    id: str
    kind: str
    label: str
    weight: int
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    weight: int
    kind: str = "session_cooccur"


class GraphStats(BaseModel):
    sessions: int = 0
    date_range: list[str] = Field(default_factory=list)


class GraphResponse(BaseModel):
    schema_version: SchemaVersion = 1
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    stats: GraphStats = Field(default_factory=GraphStats)


class EntityAppearance(BaseModel):
    session_date: str
    snippet: str | None = None


class EntityResponse(BaseModel):
    schema_version: SchemaVersion = 1
    id: str
    kind: str
    label: str
    weight: int
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None
    appearances: list[EntityAppearance] = Field(default_factory=list)


# -- remove -------------------------------------------------------------------


class RemoveRequest(BaseModel):
    """POST /api/mylife/events/remove 请求体（docs/bff_service_remove.md §2.1）。

    请求级唯一硬错误 = 格式非法（空 / 超 50 / 元素非字符串）；ref 级失败逐条
    skip（部分成功语义，D7）——目前只支持 video 事件。strict=True：元素必须
    是字符串本身（拒绝 int→str 宽松转换）。
    """

    model_config = ConfigDict(strict=True)

    event_refs: list[str] = Field(min_length=1, max_length=50)

    @field_validator("event_refs")
    @classmethod
    def _nonempty_refs(cls, v: list[str]) -> list[str]:
        for ref in v:
            if not ref.strip():
                raise ValueError("event_refs 元素应为非空字符串")
        return v


# -- agent jobs ---------------------------------------------------------------


class CancelAgentJobRequest(BaseModel):
    """POST /api/mylife/agent-jobs/cancel 请求体（docs/android_agent_job_status.md §7.2）。

    单对象取消；strict=True 拒绝非字符串 job_id。
    """

    model_config = ConfigDict(strict=True)

    job_id: str = Field(min_length=1, max_length=64)

    @field_validator("job_id")
    @classmethod
    def _nonempty_job_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("job_id 应为非空字符串")
        return v
