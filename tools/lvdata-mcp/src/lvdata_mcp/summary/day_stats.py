"""day_stats 预计算 stage（docs/bff_service_interfaces.md §7.2）。

把 BFF `/api/mylife/overview` 的 counts / hot_slots / images 在**常跑组**预计算进
`day_summary.day_stats`（JSON 列），使 overview 单表可读（BFF 不再逐 session 全量读
events、不再逐日 5 次查询）。**无 LLM、无外部调用**，只读 `events`。

约定（同文档 §7.2.3）：
- 目标时区 = LLM 活跃时区集合（E3-D″，`summary/timezones.py`，与 day_summary 生成面一致）；
- 游标 `day_stats_state`：候选 = `events.updated_at > last_scan_at`（本轮扫描开始时刻，
  完成时推进——E13；`target` 一律忽略＝始终全局，E15）；日窗查询带 `updated_at <= T_scan`
  上界（对齐 `day_summary._generate_one` 的 T0 快照纪律）；
- upsert：行已存在**只 UPDATE day_stats**（不碰 status/doc/model/generated_at/fail_count，
  与 LLM 阶段互不覆盖）；行不存在时，若非「去重空日」才 INSERT pending 占位行（E12）；
- 口径与 BFF 原派生逐字等价：基线 `tests/fixtures/day_stats_golden.json`（由 BFF 现实现
  生成、删码前固化，§7.5 P0）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import structlog

from ..config import LvdataMcpConfig
from ..consts import (
    DAY_IMAGE_POOL,
    HOT_SLOT_TYPES,
    MAX_HOT_SLOTS,
    PRIORITY_SQL,
    SPLIT_GAP_MS,
)
from ..timeutil import (LocalDateOutOfRange, day_bounds_ms, now_iso,
                        resolve_timezone, safe_local_date)
from .day import _is_duplicated_audio
from .timezones import active_timezones

log = structlog.get_logger("lvdata_mcp.day_stats")

DAY_STATS_SCHEMA_VERSION = 1


# ── 派生（与 BFF 原实现逐字等价；排序依赖 SQL 的 (ts_ms, PRIORITY, event_id)）────


def _flatten(row: dict) -> dict:
    """镜像事件出库语义：payload JSON 键合并进行字典（列同名以列为准）。"""
    out = dict(row)
    try:
        payload = json.loads(out.get("payload") or "{}")
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k not in out:
                out[k] = v
    return out


def derive_counts(events: list[dict]) -> dict[str, int]:
    """日窗内 events.type 原样逐类计数（不限类型、不去重——E4）。"""
    counts: dict[str, int] = {}
    for e in events:
        t = e["type"]
        counts[t] = counts.get(t, 0) + 1
    return counts


def derive_hot_slots(events: list[dict]) -> list[dict]:
    """活跃时段：间隔 > SPLIT_GAP_MS 切段 → 超 MAX_HOT_SLOTS 反复合并最小间隔对。

    入参应为**已过滤**的 HOT_SLOT_TYPES 事件；入参次序须为 SQL 的
    (ts_ms, PRIORITY, event_id)，此处按 ts_ms 稳定排序（同 ts 保持 SQL 次序）。
    """
    if not events:
        return []
    rows = sorted(events, key=lambda e: e["ts_ms"])
    segments: list[dict] = []
    cur = {"start_ms": rows[0]["ts_ms"], "end_ms": rows[0]["ts_ms"], "count": 0}
    for e in rows:
        if e["ts_ms"] - cur["end_ms"] > SPLIT_GAP_MS:
            segments.append(cur)
            cur = {"start_ms": e["ts_ms"], "end_ms": e["ts_ms"], "count": 0}
        cur["end_ms"] = max(cur["end_ms"], e["ts_ms"])
        cur["count"] += 1
    segments.append(cur)
    while len(segments) > MAX_HOT_SLOTS:
        # 合并相邻间隔最小的一对（min 取首个最小 → 并列时最左，确定性）
        i = min(range(len(segments) - 1),
                key=lambda j: segments[j + 1]["start_ms"] - segments[j]["end_ms"])
        segments[i:i + 2] = [{
            "start_ms": segments[i]["start_ms"],
            "end_ms": segments[i + 1]["end_ms"],
            "count": segments[i]["count"] + segments[i + 1]["count"],
        }]
    return segments


def derive_images(events: list[dict]) -> list[dict]:
    """缩略图：window 分组轮转选取（≤DAY_IMAGE_POOL 张）→ ts 升序输出。

    入参应为**已过滤**的 video 事件；分组键 = `window_id ?: event_ref`，
    窗口序 =（各窗口首帧 ts, 分组键）。**不存 media_url**（E5）：由 BFF 出口组装。
    """
    videos = [e for e in events if e.get("frame_id") and e.get("rsession_id")]
    groups: dict[str, list[dict]] = {}
    for e in sorted(videos, key=lambda x: x["ts_ms"]):
        groups.setdefault(e.get("window_id") or e["event_ref"], []).append(e)
    ordered = sorted(groups.items(), key=lambda kv: (kv[1][0]["ts_ms"], kv[0]))

    picked: list[dict] = []
    round_idx = 0
    while len(picked) < DAY_IMAGE_POOL:
        added = False
        for _, group in ordered:
            if round_idx >= len(group):
                continue
            picked.append(group[round_idx])
            added = True
            if len(picked) >= DAY_IMAGE_POOL:
                break
        if not added:
            break  # 全部窗口取尽仍不足池上限 → 返回全部
        round_idx += 1

    return [
        {
            "frame_id": e["frame_id"],
            "ts_ms": e["ts_ms"],
            "event_ref": e["event_ref"],
            "window_id": e.get("window_id"),
            "device_uuid": e["device_uuid"],
            "rsession_id": e["rsession_id"],
        }
        for e in sorted(picked, key=lambda x: x["ts_ms"])
    ]


def derive_day_stats(events: list[dict], computed_at: str) -> dict:
    """日窗 events → day_stats JSON（§7.2.2）。"""
    return {
        "schema_version": DAY_STATS_SCHEMA_VERSION,
        "computed_at": computed_at,
        "counts": derive_counts(events),
        "hot_slots": derive_hot_slots([e for e in events if e["type"] in HOT_SLOT_TYPES]),
        "images": derive_images([e for e in events if e["type"] == "video"]),
    }


def _utc_offset_min(tz: str, date: str) -> int:
    """该 local_date 日首的 UTC 偏移（分钟；DST 按日期正确）。

    用 `resolve_timezone` 而非 `ZoneInfo`：`timezones` 表的规范形包含**固定偏移**
    （"+08:00"，由 canonicalize_timezone 写入，如客户端上报 "GMT+8"），
    `ZoneInfo("+08:00")` 会抛 ZoneInfoNotFoundError → 整个 stage 每轮失败。
    """
    naive = datetime.strptime(date, "%Y-%m-%d")
    return int(naive.replace(tzinfo=resolve_timezone(tz)).utcoffset().total_seconds() / 60)


# ── stage ────────────────────────────────────────────────────────────────────


class DayStats:
    def __init__(self, cfg: LvdataMcpConfig):
        self.cfg = cfg
        self.db_path = cfg.data.resolved_db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    def run(self, target: str | None = None, force: bool = False) -> dict:
        """一轮 day_stats：活跃时区 × dirty 日 → 重算并写 day_summary.day_stats。

        `target` 忽略（E15：本 stage 无 session 维度，始终全局处理）。
        """
        t0 = now_iso()
        conn = self._conn()
        try:
            tzs = active_timezones(conn, self.cfg.watcher.timezone)
            days = 0
            for tz in tzs:
                days += self._run_tz(conn, tz, t0, force)
            conn.commit()
        finally:
            conn.close()
        if days:
            log.info("day_stats done", days=days, timezones=tzs, force=force)
        return {"days": days, "timezones": tzs}

    # ── 单时区 ────────────────────────────────────────────────────────────

    def _run_tz(self, conn: sqlite3.Connection, tz: str, t0: str,
                force: bool) -> int:
        cursor = "" if force else self._cursor(conn, tz)
        candidates = conn.execute(
            "SELECT ts_ms FROM events WHERE updated_at > ?", (cursor,)).fetchall()
        days_set: set[str] = set()
        for c in candidates:
            try:
                days_set.add(safe_local_date(c["ts_ms"], tz))
            except LocalDateOutOfRange:
                continue
        days = sorted(days_set, reverse=True)
        n = 0
        for d in days:
            n += self._run_day(conn, tz, d, t0)
        # 完成才推进游标（E13）：中途异常/超时取消 → 不 commit，下轮从旧游标重来
        conn.execute(
            "INSERT INTO day_stats_state (timezone, last_scan_at) VALUES (?, ?) "
            "ON CONFLICT(timezone) DO UPDATE SET "
            "last_scan_at = excluded.last_scan_at", (tz, t0))
        return n

    def _run_day(self, conn: sqlite3.Connection, tz: str, d: str, t0: str) -> int:
        start, end = day_bounds_ms(d, tz)
        rows = conn.execute(
            "SELECT * FROM events WHERE ts_ms BETWEEN ? AND ? AND updated_at <= ? "
            f"ORDER BY ts_ms, {PRIORITY_SQL}, event_id", (start, end, t0)).fetchall()
        events = [_flatten(r) for r in rows]
        exists = conn.execute(
            "SELECT status FROM day_summary WHERE timezone = ? AND local_date = ?",
            (tz, d)).fetchone()

        if not events:
            # 竞态兜底（§7.2.3-5）：候选日事件在扫描间隙被删空 → 删非 ok 行，
            # 与 day.py:231-240 同口径（纯删除产生的残留行不在本 stage 职责内）
            if exists is not None and exists["status"] != "ok":
                conn.execute(
                    "DELETE FROM day_summary WHERE timezone = ? AND local_date = ?",
                    (tz, d))
            return 0

        doc = json.dumps(derive_day_stats(events, t0), ensure_ascii=False)
        if exists is not None:
            conn.execute(
                "UPDATE day_summary SET day_stats = ? "
                "WHERE timezone = ? AND local_date = ?", (doc, tz, d))
            return 1

        # E12：当日事件全部是「被 LLM 输入去重的 audio」时不落新行——否则
        # `_generate_one` 的 no_events 分支会删掉该 pending 行，日卡消失且游标
        # 已推进不再重建（day.py:449-456）。去重镜像只用于此 INSERT 门控。
        if all(_is_duplicated_audio(e["type"], e.get("payload")) for e in events):
            return 0
        conn.execute(
            "INSERT OR IGNORE INTO day_summary (timezone, local_date, utc_offset_min, "
            "day_start_ms, day_end_ms, day_summary, status, event_count, fail_count, "
            "generated_at, day_stats) VALUES (?,?,?,?,?,'{}','pending',0,0,NULL,?)",
            (tz, d, _utc_offset_min(tz, d), start, end, doc))
        return 1

    def recompute_days(self, conn: sqlite3.Connection, tz: str,
                       days: list[str], t0: str) -> list[str]:
        """按指定 (tz, local_date) 重算 day_stats（删除接口专用，§3.1-5）。

        - 与 stage 游标**解耦**：不读/不写 `day_stats_state`（游标只由 `_run_tz`
          推进；此处推进会把其它 dirty 日误标为已覆盖）；
        - 有剩余事件 → UPDATE day_stats（复用 derive 单源）；
        - 删空且有行：`status=='ok'` → UPDATE 空派生（日卡保留，D3）；否则
          DELETE 行（镜像 `_run_day` 竞态兜底）；无行 → 跳过；
        - 返回实际改动的日（UPDATE 与 DELETE 均计入，供响应 `days_recomputed`）。
        """
        out: list[str] = []
        for d in days:
            start, end = day_bounds_ms(d, tz)
            exists = conn.execute(
                "SELECT status FROM day_summary WHERE timezone = ? AND local_date = ?",
                (tz, d)).fetchone()
            if exists is None:
                continue
            rows = conn.execute(
                "SELECT * FROM events WHERE ts_ms BETWEEN ? AND ? "
                f"ORDER BY ts_ms, {PRIORITY_SQL}, event_id",
                (start, end)).fetchall()
            events = [_flatten(r) for r in rows]
            if not events and exists["status"] != "ok":
                conn.execute(
                    "DELETE FROM day_summary WHERE timezone = ? AND local_date = ?",
                    (tz, d))
                out.append(d)
                continue
            doc = json.dumps(derive_day_stats(events, t0), ensure_ascii=False)
            conn.execute(
                "UPDATE day_summary SET day_stats = ? "
                "WHERE timezone = ? AND local_date = ?", (doc, tz, d))
            out.append(d)
        return out

    def _cursor(self, conn: sqlite3.Connection, tz: str) -> str:
        row = conn.execute(
            "SELECT last_scan_at FROM day_stats_state WHERE timezone = ?",
            (tz,)).fetchone()
        return (row[0] if row else "") or ""
