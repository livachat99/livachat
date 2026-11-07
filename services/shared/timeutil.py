"""时区归一化（docs/timezone-support.md §5.1）。

不变式：timezones 表、SessionContext、BFF 生效时区只持有规范形——
canonicalize_timezone 是写入 timezones 表的唯一入口；lvdata-mcp
resolve_timezone 对全部规范形已有正确语义（§5.1 不变式）。
"""

from __future__ import annotations

import math
from datetime import UTC, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shared.config.models import BffConfig, RecordingConfig

_MAX_OFFSET = timedelta(hours=14)


def canonicalize_timezone(tz: str | None) -> str | None:
    """客户端上报时区 → 规范形；非法 → None。

    规范形（lvdata-mcp resolve_timezone 已全覆盖的子集）：
    - IANA 名原样："Asia/Shanghai"（ZoneInfo 校验通过即返回，含 Etc/GMT+8 等）
    - 固定偏移："+08:00" / "-05:30"
      （容忍可选 GMT/UTC 前缀、大小写、小时单位写法："GMT+08:00"/"gmt+8"/"8"）
    - "UTC"："utc"/"gmt"/"+00:00"/"-00:00" 归一
    固定偏移取值域 |offset| ≤ 14:00（IANA 实际极值），分钟须为 00–59。
    """
    if tz is None:
        return None
    s = tz.strip()
    if not s:
        return None
    if s.lower() in ("utc", "gmt", "+00:00", "-00:00"):
        return "UTC"
    try:
        ZoneInfo(s)
        return s
    except (ZoneInfoNotFoundError, ValueError):
        pass
    offset = _parse_fixed_offset(s)
    if offset is None:
        return None
    if offset == timedelta(0):
        return "UTC"
    return _format_offset(offset)


def _parse_fixed_offset(s: str) -> timedelta | None:
    """解析 GMT/UTC 前缀 ± 固定偏移："GMT+08:00"/"gmt+8"/"+08:00"/"-05:30"/"8"。"""
    sign = 1
    t = s
    if t.lower().startswith(("gmt", "utc")):
        t = t[3:]
    t = t.strip()
    if not t:
        return None
    if t.startswith("+"):
        t = t[1:]
    elif t.startswith("-"):
        sign = -1
        t = t[1:]
    if not t:
        return None
    try:
        if ":" in t:
            h, m = t.split(":", 1)
            if not h.isdigit() or not m.isdigit():
                return None
            hours, minutes = int(h), int(m)
        else:
            f = float(t)
            if not math.isfinite(f):
                return None  # "inf"/"1e400"/"nan"：非有限值拒绝（int(inf) 会抛 OverflowError）
            hours = int(f)
            minutes = round((f - hours) * 60)
            if abs((f - hours) * 60 - minutes) > 1e-9:
                return None
    except (ValueError, OverflowError):
        return None
    if minutes < 0 or minutes >= 60:
        return None
    offset = sign * timedelta(hours=hours, minutes=minutes)
    if abs(offset) > _MAX_OFFSET:
        return None
    return offset


def _format_offset(offset: timedelta) -> str:
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    h, m = divmod(total // 60, 60)
    return f"{sign}{h:02d}:{m:02d}"


def resolve_tzinfo(tz: str):
    """规范形 → tzinfo（ZoneInfo 或固定偏移）；与 lvdata-mcp resolve_timezone 同语义。"""
    if tz == "UTC":
        return UTC
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        pass
    if tz.startswith(("+", "-")):
        try:
            h, m = tz[1:].split(":", 1)
            sign = -1 if tz.startswith("-") else 1
            return timezone(sign * timedelta(hours=int(h), minutes=int(m)))
        except ValueError:
            pass
    raise ValueError(f"not a canonical timezone: {tz!r}")


def lvdata_db_path(bff: BffConfig, recording: RecordingConfig) -> str:
    """lvdata SQLite 路径，与 LvdataStore.from_config 同规则（timezone-support.md §5.3）。"""
    data_root = bff.data_root or recording.base_dir
    return bff.lvdata_db_path or str(Path(data_root) / "lvdata_inventory.db")
