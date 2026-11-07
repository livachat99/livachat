"""LLM 活跃时区集合（§8.2）：最近使用的 enabled 时区 ∪ 配置默认时区（≤2）。

单一出处：day_summary 的 dirty 发现（`DaySummary._active_timezones`）与 day_stats
stage 的目标时区集合都必须用同一判定——两处结果不同会让「BFF 读到的最新时区」与
「实际生成 day_summary 行的时区」脱节（docs/bff_service_interfaces.md §7.2.3-1）。
"""

from __future__ import annotations

import sqlite3


def active_timezones(conn: sqlite3.Connection, default_tz: str | None) -> list[str]:
    """最近使用的 enabled 时区 ∪ 默认时区（若 enabled），有序去重，至多 2 个。

    非活跃时区不被遍历——游标/占位行原样保留，再激活续跑（§8.3）。
    """
    recent = conn.execute(
        "SELECT timezone FROM timezones WHERE enabled = 1 "
        "ORDER BY updated_at DESC, timezone LIMIT 1").fetchone()
    active: list[str] = []
    if recent is not None:
        active.append(recent[0])
    default = default_tz
    if default and default not in active and conn.execute(
            "SELECT 1 FROM timezones WHERE timezone = ? AND enabled = 1",
            (default,)).fetchone() is not None:
        active.append(default)
    return active
