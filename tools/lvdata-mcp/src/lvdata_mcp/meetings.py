"""Agent 会议持久化（docs/livachat_meeting.md）。

与 events 的只读 DataStore 分开：会议是 lvdata-mcp 的写域，使用独立可写连接；
读路径用 mode=ro，避免误创建缺失的数据库文件。会议号是 8 位数字，硬删除。
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from .config import LvdataMcpConfig
from .store import StoreError
from .timeutil import try_resolve_timezone

log = structlog.get_logger("lvdata_mcp.meetings")

MEETING_NUMBER_DIGITS = 8
DEFAULT_DURATION_MS = 60 * 60 * 1000
MAX_DURATION_MS = 24 * 60 * 60 * 1000
MAX_TOPIC_CHARS = 100
NUMBER_RETRIES = 5

_MIN_MS = int(datetime(2000, 1, 1, tzinfo=UTC).timestamp() * 1000)
_MAX_MS = int(datetime(2100, 1, 1, tzinfo=UTC).timestamp() * 1000)


class MeetingError(StoreError):
    """会议域错误；继承 StoreError 以复用 tools/REST 的 {code,message,extra} 信封。"""


@dataclass(frozen=True)
class Meeting:
    meeting_number: str
    topic: str
    start_at_ms: int
    end_at_ms: int
    timezone: str
    created_at: str


def now_ms() -> int:
    return int(time.time() * 1000)


def normalize_meeting_number(raw: str) -> str:
    """NFKC 全角转半角，去空白/连字符；必须是 8 位数字。"""
    if not isinstance(raw, str):
        raise MeetingError("invalid_param", "meeting_number must be a string")
    value = unicodedata.normalize("NFKC", raw).strip()
    value = re.sub(r"[\s-]+", "", value)
    # `\d` in Python also matches non-ASCII decimal digits; only [0-9] is a meeting number.
    if not re.fullmatch(r"[0-9]{8}", value):
        raise MeetingError("invalid_param", "meeting_number must be 8 digits")
    return value


def generate_meeting_number() -> str:
    """8 位数字，首位非 0；约 9000 万种可能。"""
    return str(secrets.randbelow(90_000_000) + 10_000_000)


def _validate_topic(topic: str) -> str:
    if not isinstance(topic, str):
        raise MeetingError("invalid_param", "topic must be a string")
    value = topic.strip()
    if not value or len(value) > MAX_TOPIC_CHARS:
        raise MeetingError("invalid_param", f"topic must be 1..{MAX_TOPIC_CHARS} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise MeetingError("invalid_param", "topic must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise MeetingError("invalid_param", "topic must be valid Unicode text") from exc
    return value


def _parse_iso(value: str, timezone_name: str | None, field: str) -> int:
    if not isinstance(value, str) or not value.strip():
        raise MeetingError("invalid_param", f"{field} must be an ISO 8601 string")
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise MeetingError("invalid_param", f"{field} must be ISO 8601: {exc}") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        if not timezone_name:
            raise MeetingError(
                "invalid_param", f"{field} requires timezone when it has no offset")
        tz = try_resolve_timezone(timezone_name)
        if tz is None:
            raise MeetingError("invalid_param", f"invalid timezone: {timezone_name}")
        dt = dt.replace(tzinfo=tz)
    return int(dt.timestamp() * 1000)


def _row_to_meeting(row: sqlite3.Row) -> Meeting:
    return Meeting(
        meeting_number=row["meeting_number"],
        topic=row["topic"],
        start_at_ms=row["start_at_ms"],
        end_at_ms=row["end_at_ms"],
        timezone=row["timezone"] or "",
        created_at=row["created_at"],
    )


class MeetingStore:
    """会议表的写/读入口；构造不连库（避免测试意外创建空 DB）。"""

    def __init__(self, cfg: LvdataMcpConfig):
        self.cfg = cfg
        self.db_path = Path(cfg.data.resolved_db_path)

    # -- connections -------------------------------------------------------

    def _ensure_db(self) -> None:
        if not self.db_path.is_file():
            raise MeetingError(
                "database_not_initialized", f"Database not initialized: {self.db_path}")

    def _read_conn(self) -> sqlite3.Connection:
        self._ensure_db()
        uri = self.db_path.absolute().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _write_conn(self) -> sqlite3.Connection:
        self._ensure_db()
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _map_sqlite(exc: sqlite3.OperationalError) -> MeetingError:
        if "no such table" in str(exc).lower():
            return MeetingError("database_not_initialized", "meetings table is not initialized")
        return MeetingError("internal", str(exc)[:300])

    # -- writes ------------------------------------------------------------

    def create(self, *, topic: str, start_time: str,
               end_time: str | None = None, timezone: str | None = None) -> Meeting:
        topic = _validate_topic(topic)
        timezone_name = timezone.strip() if isinstance(timezone, str) and timezone.strip() else None
        if timezone_name is not None and try_resolve_timezone(timezone_name) is None:
            raise MeetingError("invalid_param", f"invalid timezone: {timezone_name}")

        start_ms = _parse_iso(start_time, timezone_name, "start_time")
        end_ms = (_parse_iso(end_time, timezone_name, "end_time")
                  if end_time is not None else start_ms + DEFAULT_DURATION_MS)
        if end_ms <= start_ms:
            raise MeetingError("invalid_param", "end_time must be after start_time")
        if end_ms - start_ms > MAX_DURATION_MS:
            raise MeetingError("invalid_param", "meeting duration must be at most 24 hours")
        if end_ms <= now_ms():
            raise MeetingError("invalid_param", "meeting end_time must be in the future")
        if not (_MIN_MS <= start_ms <= _MAX_MS and _MIN_MS <= end_ms <= _MAX_MS):
            raise MeetingError("invalid_param", "meeting time must be between 2000 and 2100")

        display_tz = timezone_name or self.cfg.watcher.timezone
        for _ in range(NUMBER_RETRIES):
            number = generate_meeting_number()
            try:
                with closing(self._write_conn()) as conn, conn:
                    conn.execute(
                        "INSERT INTO meetings "
                        "(meeting_number, topic, start_at_ms, end_at_ms, timezone) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (number, topic, start_ms, end_ms, display_tz),
                    )
                    row = conn.execute(
                        "SELECT * FROM meetings WHERE meeting_number = ?", (number,)
                    ).fetchone()
            except sqlite3.IntegrityError:
                log.warning("meeting number collision", meeting_number=number)
                continue
            except sqlite3.OperationalError as exc:
                raise self._map_sqlite(exc) from exc
            except sqlite3.Error as exc:
                raise MeetingError("internal", str(exc)[:300]) from exc
            if row is None:
                raise MeetingError("internal", "meeting insert did not return a row")
            meeting = _row_to_meeting(row)
            log.info("meeting created", meeting_number=meeting.meeting_number,
                     start_at_ms=meeting.start_at_ms, end_at_ms=meeting.end_at_ms)
            return meeting
        raise MeetingError("internal", "could not allocate a unique meeting number")

    def delete(self, meeting_number: str) -> Meeting | None:
        number = normalize_meeting_number(meeting_number)
        try:
            with closing(self._write_conn()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM meetings WHERE meeting_number = ?", (number,)
                ).fetchone()
                if row is None:
                    return None
                conn.execute("DELETE FROM meetings WHERE meeting_number = ?", (number,))
        except sqlite3.OperationalError as exc:
            raise self._map_sqlite(exc) from exc
        except sqlite3.Error as exc:
            raise MeetingError("internal", str(exc)[:300]) from exc
        meeting = _row_to_meeting(row)
        log.info("meeting deleted", meeting_number=meeting.meeting_number)
        return meeting

    # -- reads -------------------------------------------------------------

    def get(self, meeting_number: str) -> Meeting | None:
        number = normalize_meeting_number(meeting_number)
        try:
            with closing(self._read_conn()) as conn:
                row = conn.execute(
                    "SELECT * FROM meetings WHERE meeting_number = ?", (number,)
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise self._map_sqlite(exc) from exc
        except sqlite3.Error as exc:
            raise MeetingError("internal", str(exc)[:300]) from exc
        return _row_to_meeting(row) if row is not None else None

    def list(self, *, start_ms: int | None, end_ms: int | None,
             include_past: bool, limit: int) -> tuple[list[Meeting], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if start_ms is not None:
            clauses.append("start_at_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("start_at_ms <= ?")
            params.append(end_ms)
        if start_ms is None and end_ms is None and not include_past:
            clauses.append("end_at_ms >= ?")
            params.append(now_ms())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order = ("start_at_ms DESC"
                 if include_past and start_ms is None and end_ms is None
                 else "start_at_ms ASC")
        try:
            with closing(self._read_conn()) as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM meetings{where}", params
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT * FROM meetings{where} ORDER BY {order} LIMIT ?",
                    [*params, limit],
                ).fetchall()
        except sqlite3.OperationalError as exc:
            raise self._map_sqlite(exc) from exc
        except sqlite3.Error as exc:
            raise MeetingError("internal", str(exc)[:300]) from exc
        return [_row_to_meeting(row) for row in rows], int(total)
