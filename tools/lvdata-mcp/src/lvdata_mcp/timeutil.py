from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Python datetime 的 UTC 毫秒边界（闭区间）：0001-01-01T00:00:00.000Z ..
# 9999-12-31T23:59:59.999Z。最大日的本地日尾在正/负偏移时区可能越过该范围。
_MIN_EPOCH_MS = -62135596800000
_MAX_EPOCH_MS = 253402300799999
# 边界窗口搜索余量：现代 IANA 最大偏移 ±14h，历史 LMT 实测可达 ~15.2h。
_BOUNDARY_MARGIN_MS = 24 * 3600 * 1000
_UPPER_LIMIT_MS = _MAX_EPOCH_MS + _BOUNDARY_MARGIN_MS


class LocalDateOutOfRange(ValueError):
    """epoch ms 合法，但其本地日期超出 0001-01-01 .. 9999-12-31。"""


def now_iso() -> str:
    """§5.1 规范时间戳：ISO 8601 UTC，'T' 分隔、毫秒精度、'Z' 后缀。

    所有参与字符串比较的水位列统一该格式；与 SQLite
    ``strftime('%Y-%m-%dT%H:%M:%fZ','now')`` 输出一致。
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ms_to_iso(ts_ms: int) -> str:
    """epoch ms → §5.1 规范时间戳（events.ts_utc 的推导来源）。"""
    return (datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def uuid7_to_ms(uuid_str: str) -> int | None:
    """UUIDv7 前 48 位 = unix 毫秒时间戳（与 files.uuid_ts_ms 同源）。"""
    try:
        return uuid.UUID(uuid_str).int >> 80
    except (ValueError, AttributeError):
        return None


def try_resolve_timezone(tz_str: str):
    """IANA 名（Asia/Shanghai）或固定偏移（+08:00 / 8）→ tzinfo；不可解析 → None。

    严格版：调用方需要区分"解析失败"时用它（如请求参数校验/回落），
    `resolve_timezone` 是其回落 UTC 的薄包装（服务内既有约定的单一出处）。
    """
    s = tz_str.strip()
    if s.lower() in ("utc", "gmt"):
        return timezone.utc
    try:
        return ZoneInfo(s)
    except (ZoneInfoNotFoundError, ValueError):
        pass
    sign = 1
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        sign = -1
        s = s[1:]
    try:
        if ":" in s:
            h, m = s.split(":", 1)
            offset = timedelta(hours=int(h), minutes=int(m))
        else:
            offset = timedelta(hours=float(s))
        return timezone(sign * offset)
    except (ValueError, TypeError):
        return None


def resolve_timezone(tz_str: str):
    """`try_resolve_timezone` 的回落 UTC 包装（服务既有约定：不可解析一律 UTC）。"""
    return try_resolve_timezone(tz_str) or timezone.utc


def normalize_date(date_str: str) -> str:
    """`YYYY-MM-DD` 归一为补零规范串（`2026-7-4` → `2026-07-04`）；格式错抛 ValueError。

    `strptime` 对 `%m`/`%d` 接受不补零写法（宽松），解析出的语义正确但**回显**会与
    `days[].date`（`ms_to_local_date` 产出的规范串）不一致——agent 做字符串比对会踩（I-6）。
    """
    return datetime.strptime(date_str, "%Y-%m-%d").date().isoformat()


def day_bounds_ms(date_str: str, tz_name: str) -> tuple[int, int]:
    """'YYYY-MM-DD' → 该日期在 tz_name 时区的 [日首, 日尾] epoch ms（本地日界，闭区间）。

    与 list_sessions 的 session_date（录制本地日期）语义对齐；无效日期抛
    ValueError，调用方转 invalid_param。

    `9999-12-31` 无法构造次日零点，改用日首 + 24h 名义边界。该边界允许越过
    Python datetime 上限（负偏移时区的本地日末事件对应 UTC year 10000）；
    SQLite 的整数比较不受此限制，查询窗口因此不会被截短。
    """
    start = datetime.strptime(date_str, "%Y-%m-%d")
    tz = resolve_timezone(tz_name)
    start_ms = int(start.replace(tzinfo=tz).timestamp() * 1000)
    if start.date() == date.max:
        end_ms = start_ms + 86_400_000 - 1
    else:
        end = start + timedelta(days=1)
        end_ms = int(end.replace(tzinfo=tz).timestamp() * 1000) - 1
    return start_ms, end_ms


def date_window_ms(start_date: str | None, end_date: str | None,
                   tz_name: str) -> tuple[int | None, int | None]:
    """日期窗（`YYYY-MM-DD` 含端点）→ (start_ms, end_ms)；缺省 None = 不限。

    格式错 / 逆序抛 ValueError（调用方转 `invalid_param`）。get_timeline 与 search
    共用，保证两处日期窗语义一致（§9.2/§10.2）。
    """
    for name, d in (("start_date", start_date), ("end_date", end_date)):
        if d is not None:
            try:
                datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"{name} must be YYYY-MM-DD: {d}")
    start_ms = day_bounds_ms(start_date, tz_name)[0] if start_date else None
    end_ms = day_bounds_ms(end_date, tz_name)[1] if end_date else None
    if start_ms is not None and end_ms is not None and start_ms > end_ms:
        raise ValueError(f"start_date is after end_date: {start_date} > {end_date}")
    return start_ms, end_ms


def day_bounds(tz: str, start_ms: int, end_ms: int) -> tuple[str, str]:
    """epoch 毫秒区间 → 本地日期范围 [start_date, end_date]（含边界，DST 正确）。"""
    tzinfo = resolve_timezone(tz)
    start = datetime.fromtimestamp(start_ms / 1000, tzinfo)
    end = datetime.fromtimestamp(end_ms / 1000, tzinfo)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _local_datetime(ms: int, tz_name: str) -> datetime:
    """epoch ms → 本地 datetime（最小/最大日边界安全）。

    正常时刻走 `fromtimestamp`（DST 规则精确）。本地最小/最大日的前后若干小时
    可能对应 UTC year 0/10000，无法由 datetime 直接表示；此时用“本地日首 +
    整数毫秒差”构造。合法 epoch 但本地日期仍超出 0001..9999 →
    `LocalDateOutOfRange`；更远的损坏时间戳 → ValueError。
    """
    tz = resolve_timezone(tz_name)
    if _MIN_EPOCH_MS <= ms <= _MAX_EPOCH_MS:
        try:
            return datetime.fromtimestamp(ms / 1000, tz)
        except (OverflowError, OSError, ValueError):
            pass
    # 只有最小/最大本地日可能越过 Python datetime 的 UTC 表示范围。
    if (_MIN_EPOCH_MS - _BOUNDARY_MARGIN_MS
            <= ms <= _UPPER_LIMIT_MS):
        for day_str, year, month, day in (
                ("9999-12-31", 9999, 12, 31),
                ("0001-01-01", 1, 1, 1)):
            start, end = day_bounds_ms(day_str, tz_name)
            if start <= ms <= end:
                try:
                    return datetime(year, month, day, tzinfo=tz) + timedelta(
                        milliseconds=ms - start)
                except (OverflowError, ValueError):
                    break
        raise LocalDateOutOfRange(
            f"local date out of supported range: {ms} ({tz_name})")
    raise ValueError(f"timestamp out of supported range: {ms}")


def ms_to_local(ms: int, tz_name: str = "Asia/Shanghai") -> str:
    """epoch ms → **带偏移的 ISO 8601**（§17），如 `2026-09-28T18:01:30+08:00`。

    偏移由 tz_name 的时区规则**按该时刻**算出（DST 时区内同一响应可能出现两种偏移，
    见 §17.2-b：`timezone` 字段仍是真实生效 zone 名）。代价：跨偏移时**字符串字典序
    ≠ 时间序**——调用方需自行排序时须解析后比较。
    """
    return _local_datetime(ms, tz_name).isoformat(timespec="seconds")


def ms_to_local_or_none(ms: int | None, tz_name: str) -> str | None:
    """epoch ms → 本地可读时间；缺失（None/0）→ None（§11 D6：哨兵值不伪装成 1970 数据）。"""
    return ms_to_local(ms, tz_name) if ms else None


def ms_to_local_date(ms: int, tz_name: str) -> str:
    """epoch ms → 本地日期 YYYY-MM-DD（§6.5 local_date()；边界安全）。"""
    return _local_datetime(ms, tz_name).strftime("%Y-%m-%d")


def safe_local_date(ms: int, tz_name: str) -> str:
    """边界安全的本地日期归组（`ms_to_local_date` 的语义化别名）。"""
    return ms_to_local_date(ms, tz_name)


def ms_to_local_hhmm(ms: int, tz_name: str) -> str:
    """epoch ms → 本地 HH:MM（日摘要事件块渲染；边界安全）。"""
    return _local_datetime(ms, tz_name).strftime("%H:%M")
