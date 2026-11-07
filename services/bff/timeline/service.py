from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from bff.common.deps import resolve_request_tz
from bff.common.schemas import (
    DayImage,
    DayTopicItem,
    HotSlot,
    OverviewDay,
    OverviewResponse,
    TimelineDay,
    TimelineEvent,
    TimelineResponse,
)
from bff.common.store import EVENT_TYPES, BffError, LvdataStore
from bff.common.timeutil import day_bounds_ms
from bff.common.topics import topic_items
from shared.timeutil import canonicalize_timezone, resolve_tzinfo

# has_pending 的失败上限（§7.3 E1）：`failed` 且 fail_count 未达上限 → 仍会重试，算 pending。
# 与 lvdata-mcp 的 summary/day.py、watcher.py 各一份（跨包无法 import）——**三处需同步**。
MAX_FAILURES = 3

# 全量模式（不传 limit）的防呆上限（§8 E2）：days 随历史线性增长，超限截断 + 给 next_cursor；
# 老客户端忽略 next_cursor → 仅超长历史（> 此天数）时被静默截断
FULL_CAP_DAYS = 1000


def _local_date(ts_ms: int, tz: str) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, resolve_tzinfo(tz)).strftime("%Y-%m-%d")


# -- overview（§7：日卡 = day_summary 单表；派生字段由 lvdata-mcp day_stats stage 预计算）--


def resolve_overview_tz(store: LvdataStore, timezone: str | None,
                        default_tz: str) -> str:
    """overview 生效时区（§7.3 E3-D″）：请求时区恰是「最新时区」则用它，否则一律用最新。

    「最新」= `timezones` 表 enabled 行按 (updated_at DESC, timezone) 取首——即
    lvdata-mcp 的「最近使用」判定（day_summary / day_stats 实际生成行的时区），
    因此生效时区稳态必有数据。表缺失/空表/DB 异常 → fail-open 放行请求值（现状语义）。

    注意：timeline/search 仍走 `resolve_request_tz`（语义不同：known 判定不看 recency，
    §7.3 E10 明示两套规则勿统一）。
    """
    if timezone:
        tz = canonicalize_timezone(timezone)
        if tz is None:
            raise BffError("bad_request", f"invalid timezone: {timezone!r}")
    else:
        tz = default_tz
    latest = store.latest_timezone()
    if latest is None:
        return tz
    return tz if tz == latest else latest


def _validate_cursor(cursor: str) -> str:
    """keyset 游标 = `YYYY-MM-DD` 且为合法日历日，否则 400（§8 E5；对齐 /timeline 语义）。"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cursor):
        raise BffError("bad_request", "invalid cursor")
    try:
        date.fromisoformat(cursor)
    except ValueError:
        raise BffError("bad_request", "invalid cursor")
    return cursor


def build_overview(
    store: LvdataStore, device_id: str | None, timezone: str | None,
    default_tz: str = "Asia/Shanghai",
    limit: int | None = None, cursor: str | None = None,
) -> OverviewResponse:
    """日卡总览：**单表**读 day_summary（§7），keyset 分页见 §8。

    - counts/hot_slots/images 取自 `day_stats` 预计算列（全设备合并、全类型原始计数）；
    - summary/topics 仅 `status='ok'` 透出（E11：`failed` 行不清旧 doc，无门控会透出陈旧内容）；
    - has_pending 由行 status/fail_count 派生（E1）；
    - `device_id` 保留但**不再生效**（E2：day_stats 为全设备合并口径，`bff.device_uuids`
      需为空/单设备部署）。timeline/search 的设备过滤不受影响。
    - 分页（§8 E1/E3）：`limit` 已传 = 分页模式（`cursor` = 上一页最后一天，`local_date <`
      严格早于）；`limit` 缺省 = 全量（老客户端零行为变化），仅超 `FULL_CAP_DAYS` 时截断。
    """
    del device_id  # E2：overview 不再做设备过滤（参数保留，语义失效）
    tz = resolve_overview_tz(store, timezone, default_tz)

    if limit is None:
        if cursor is not None:
            # E3：不回落全量——避免「想翻页却拿到全表」
            raise BffError("bad_request", "cursor requires limit")
        rows = store.day_summaries(tz, None, None, limit=FULL_CAP_DAYS + 1)
        has_more = len(rows) > FULL_CAP_DAYS
        rows = rows[:FULL_CAP_DAYS]
        # 未触防呆时 total = 行数（0 额外查询，保住 §7 的「整请求 1 次查询」）
        total = store.count_days(tz) if has_more else len(rows)
    else:
        before = _validate_cursor(cursor) if cursor is not None else None
        rows = store.day_summaries(tz, None, None, before=before, limit=limit + 1)
        has_more = len(rows) > limit
        rows = rows[:limit]
        total = store.count_days(tz)

    days = [_to_overview_day(r) for r in rows]
    days.sort(key=lambda d: d.date, reverse=True)
    # 多取的 1 行只用于判 has_more；游标取「截断后」的最后一天（§8.2.2：否则会漏该天）
    next_cursor = rows[-1]["local_date"] if has_more else None
    return OverviewResponse(timezone=tz, days=days, next_cursor=next_cursor, total=total)


def _to_overview_day(row: dict) -> OverviewDay:
    stats = row.get("day_stats") or {}
    doc = row.get("doc") or {}
    ok = row.get("status") == "ok"
    return OverviewDay(
        date=row["local_date"],
        counts=stats.get("counts") or {},
        summary=doc.get("summary") if ok else None,
        hot_slots=_to_hot_slots(stats.get("hot_slots")),
        images=_to_day_images(stats.get("images")),
        has_pending=_has_pending(row),
        topics=[DayTopicItem(topic=name, search_words=sw)
                for name, sw in topic_items(doc.get("topics") if ok else None)],
    )


def _has_pending(row: dict) -> bool:
    """E1（二轮修订）：`pending` 恒真；`failed` 仅在未达失败上限（还会重试）时为真——
    达上限的行会被 `_check_pending_retry` 放弃重试，不该让「分析中…」常亮。"""
    status = row.get("status")
    if status == "pending":
        return True
    return status == "failed" and (row.get("fail_count") or 0) < MAX_FAILURES


def _to_hot_slots(items: list | None) -> list[HotSlot]:
    out: list[HotSlot] = []
    for s in items or []:
        try:
            out.append(HotSlot(**s))
        except (TypeError, ValueError):  # 坏条目丢弃，不整体 500
            continue
    return out


def _to_day_images(items: list | None) -> list[DayImage]:
    out: list[DayImage] = []
    for it in items or []:
        try:
            out.append(DayImage(
                frame_id=it["frame_id"],
                ts_ms=it["ts_ms"],
                event_ref=it["event_ref"],
                media_url=_video_media_url(it["device_uuid"], it["rsession_id"],
                                           it["frame_id"]),
                window_id=it.get("window_id"),
            ))
        except (KeyError, TypeError, AttributeError, ValueError):
            # ValueError 含 pydantic ValidationError（如 day_stats 条目字段类型不符）
            continue
    return out


# -- timeline（§9.1.1：直查 events 表）--------------------------------------


def build_timeline(
    store: LvdataStore,
    device_id: str | None,
    date_from: str | None,
    date_to: str | None,
    session_id: str | None,
    types: str | None,
    limit: int,
    cursor: str | None,
    timezone: str | None,
    default_tz: str = "Asia/Shanghai",
) -> TimelineResponse:
    tz = resolve_request_tz(store, timezone, default_tz)
    type_set = set(types.split(",")) if types else set(EVENT_TYPES)
    invalid = type_set - set(EVENT_TYPES)
    if invalid:
        raise BffError("bad_request", f"unknown event type(s): {','.join(sorted(invalid))}")

    # device 过滤：白名单 ∩ 请求 device（§5.5 安全语义）
    devices: list[str] | None = None
    if device_id or store.device_uuids:
        scoped = store.list_sessions(device_id)
        devices = list(dict.fromkeys(s["device_uuid"] for s in scoped))

    # 本地日期过滤下推 SQL（§9.1.1：date_from/date_to → epoch ms 窗口）
    start_ms: int | None = None
    end_ms: int | None = None
    if date_from or date_to:
        try:
            if date_from:
                start_ms = day_bounds_ms(date_from, tz)[0]
            if date_to:
                end_ms = day_bounds_ms(date_to, tz)[1]
        except ValueError:
            raise BffError("bad_request", "date_from/date_to 应为 YYYY-MM-DD")

    start = 0
    if cursor:
        try:
            _, idx = cursor.rsplit("|", 1)
            start = int(idx)
        except (ValueError, AttributeError):
            raise BffError("bad_request", "invalid cursor")

    if session_id:
        # session 维度不再暴露给上层；兼容内部按 rsession 过滤
        events = store.timeline_events(session_id, start_ms, end_ms) or []
        if not events and store.get_session(session_id) is None:
            raise BffError("not_found", f"session not found: {session_id}")
        events = [e for e in events if e.get("type") in type_set]
        total = len(events)
        start = min(start, total)
        page = events[start : start + limit]
    else:
        # 时间窗 + 类型 + 设备白名单全部下推 SQL；分页用 LIMIT/OFFSET
        total = store.count_events(start_ms, end_ms, type_set, devices=devices)
        start = min(start, total)
        page = store.events_in_range(start_ms, end_ms, type_set,
                                     devices=devices, limit=limit, offset=start)
    next_cursor = None
    if start + limit < total:
        next_cursor = f"{_local_date(page[-1]['ts_ms'], tz)}|{start + limit}"

    days: dict[str, TimelineDay] = {}
    for e in page:
        d = _local_date(e["ts_ms"], tz)
        day = days.setdefault(d, TimelineDay(date=d))
        day.events.append(_to_timeline_event(e, tz))

    return TimelineResponse(
        timezone=tz,
        days=[days[k] for k in sorted(days, reverse=True)],
        next_cursor=next_cursor,
        total=total,
    )


def _to_timeline_event(e: dict, tz: str) -> TimelineEvent:
    # session 维度不暴露（§9.1.1）：rsession_id 列与 session_id 别名均不透出
    # payload_dict = store 侧保留的 payload 原始 dict（列同名遮蔽时取真值），永不上 wire
    raw_payload = e.get("payload_dict") or {}
    payload = {k: v for k, v in e.items()
               if k not in ("event_id", "payload", "payload_dict", "created_at", "updated_at",
                            "rsession_id", "session_id")}
    if e["type"] == "audio":
        # audio 的列 summary 是「转写+LLM摘要+情绪名」检索拼接串（analysis/audio.py:384-394），
        # LLM 摘要只能从 payload 取——audio_summary（docs/android_timeline_audio_gallery.md §3.2）
        audio_summary = raw_payload.get("summary")
        if isinstance(audio_summary, str) and audio_summary:
            payload["audio_summary"] = audio_summary
    payload["ts_utc"] = _ts_utc(e["ts_ms"])
    payload["device_uuid"] = e["device_uuid"]
    payload["type"] = e["type"]
    payload["media_url"] = _media_url(e)
    return TimelineEvent(**payload)


def _ts_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, ZoneInfo("UTC")).isoformat()


def _video_media_url(device_uuid: str, rsession_id: str, frame_id: str) -> str:
    return f"/api/mylife/media/{device_uuid}/{rsession_id}/video/{frame_id}.jpg"


def _audio_media_url(device_uuid: str, rsession_id: str, segment_id: str) -> str:
    return f"/api/mylife/media/{device_uuid}/{rsession_id}/audio/{segment_id}.ogg"


def _media_url(e: dict) -> str | None:
    """事件 media URL（§4.4；与 overview images 共用同一构造点——§7.3 E5 单 helper）。"""
    if e["type"] == "audio" and e.get("segment_id"):
        return _audio_media_url(e["device_uuid"], e["rsession_id"], e["segment_id"])
    if e["type"] == "video" and e.get("frame_id"):
        return _video_media_url(e["device_uuid"], e["rsession_id"], e["frame_id"])
    return None
