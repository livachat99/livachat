"""本地日界工具（对齐 lvdata_mcp.timeutil，§6.5/§9.1）。

时区统一为规范形（timezone-support.md §5.1）：_tz 经 resolve_tzinfo 解析，
固定偏移规范形（+08:00）透明支持，与 lvdata-mcp 分日结果一致。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from shared.timeutil import resolve_tzinfo


def _tz(tz_name: str):
    return resolve_tzinfo(tz_name)


def day_bounds_ms(date_str: str, tz_name: str) -> tuple[int, int]:
    """'YYYY-MM-DD' → 该日期在 tz_name 时区的 [日首, 日尾] epoch ms（闭区间）。"""
    tz = _tz(tz_name)
    y, m, d = (int(x) for x in date_str.split("-"))
    start = datetime(y, m, d, tzinfo=tz)
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.replace(tzinfo=tz).timestamp() * 1000) - 1
    return start_ms, end_ms


def ms_to_local_date(ts_ms: int, tz_name: str) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, _tz(tz_name)).strftime("%Y-%m-%d")


def ms_to_local(ts_ms: int, tz_name: str) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, _tz(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
