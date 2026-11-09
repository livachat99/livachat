"""`reset` CLI：按日期重置 recording session 的分析产物。

设计文档：docs/data/lvdata_mcp_cli_reset.md。作用域 = recording session；删除
`ok` 且无事件的 audio/video 源文件，purge 已知非 window 事件（未知 type 保留），
清理/重建 FTS、向量、day_summary/day_stats 与 derived 审计 JSON，把保留文件恢复
为 pending（chat 清 replay 水位），由下一次 poll/round 重新分析。

执行前置：默认要求 server 未运行（`GET /healthz` 探测）；`--allow-running-server`
显式覆盖。`--dry-run` 只读，不受该前置限制。
"""

from __future__ import annotations

import calendar
import errno
import json
import re
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from . import ddl
from . import index as index_mod
from .config import LvdataMcpConfig
from .store import DataStore
from .summary.day_stats import DayStats
from .summary.timezones import active_timezones
from .timeutil import (LocalDateOutOfRange, day_bounds_ms, now_iso,
                       safe_local_date as _safe_local_date)
from .vec import VecIndex, build_provider

log = structlog.get_logger("lvdata_mcp.reset")

DATE_RE = re.compile(r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$")
KNOWN_EVENT_TYPES = ("chat", "transcribe", "translate", "agent_job", "audio",
                     "video")
CHAT_FILE_TYPES = ("chat_history", "chat_transcribe", "chat_translate")
MEDIA_FILE_TYPES = ("audio", "video")
CHAT_EVENT_TO_FILE = {"chat": "chat_history",
                      "transcribe": "chat_transcribe",
                      "translate": "chat_translate"}
WINDOW_EVENT_TYPE = "window"
REQUIRED_TABLES = ("events", "files", "recording_sessions", "day_summary",
                   "day_deferred", "index_state", "timezones")
REQUIRED_FTS_COLUMNS = {"text", "orig_text", "event_ref", "session_id",
                        "device_uuid", "type", "ts_ms"}
VECTOR_BATCH = 500
MEDIA_DELETE_BATCH = 500


class ResetError(RuntimeError):
    """reset 前置/结构错误（不可继续执行）。"""


def _emit_error(as_json: bool, code: str, message: str, **extra: Any) -> None:
    """统一的 CLI 错误输出：text → stderr，json → 标准 error{code,message}。"""
    if as_json:
        err = {"code": code, "message": message, **extra}
        print(json.dumps({"error": err}, ensure_ascii=False, indent=2))
    else:
        print(f"reset: {message}", file=sys.stderr)


# ── 日期与选择 ─────────────────────────────────────────────────────────────

def parse_date_spec(spec: str) -> tuple[str, str]:
    """`YYYY` / `YYYY-MM` / `YYYY-MM-DD` → 闭区间 [start_date, end_date]。"""
    if not spec or not DATE_RE.match(spec):
        raise ValueError(f"date must be YYYY, YYYY-MM, or YYYY-MM-DD: {spec!r}")
    parts = spec.split("-")
    try:
        if len(parts) == 1:
            start = datetime.strptime(f"{spec}-01-01", "%Y-%m-%d")
            return start.date().isoformat(), f"{spec}-12-31"
        if len(parts) == 2:
            start = datetime.strptime(f"{spec}-01", "%Y-%m-%d")
            last = calendar.monthrange(start.year, start.month)[1]
            return start.date().isoformat(), f"{spec}-{last:02d}"
        start = datetime.strptime(spec, "%Y-%m-%d")
        return start.date().isoformat(), start.date().isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid date: {spec!r} ({exc})") from exc


def _ms_bounds(start_date: str, end_date: str, tz: str) -> tuple[int, int]:
    """日期区间 → epoch ms 闭区间；最大日由 timeutil 的共享边界实现处理。"""
    try:
        start_ms = day_bounds_ms(start_date, tz)[0]
        end_ms = day_bounds_ms(end_date, tz)[1]
    except ValueError as exc:
        raise ValueError(
            f"date out of supported range: {start_date}..{end_date}") from exc
    return start_ms, end_ms


def select_sessions(conn: sqlite3.Connection, start_date: str, end_date: str,
                    start_ms: int, end_ms: int) -> dict[str, list[str]]:
    """日期范围 → {rsession_id: [命中原因]}（D2）。

    fallback（media/events）只对 `session_date=''` 或 session 行缺失的 session
    生效：非空且在范围外的 session 不能被单条漂移事件拖入整 session reset。
    """
    sessions: dict[str, list[str]] = {}

    def add(sid: str, reason: str) -> None:
        sessions.setdefault(sid, [])
        if reason not in sessions[sid]:
            sessions[sid].append(reason)

    for row in conn.execute(
            "SELECT rsession_id FROM recording_sessions "
            "WHERE session_date != '' "
            "AND session_date BETWEEN ? AND ? ORDER BY rsession_id",
            (start_date, end_date)):
        add(row[0], "primary")

    for row in conn.execute(
            "SELECT DISTINCT f.rsession_id FROM files f "
            "LEFT JOIN recording_sessions s ON s.rsession_id = f.rsession_id "
            "WHERE COALESCE(s.session_date, '') = '' "
            "AND f.file_type IN ('audio','video') "
            "AND f.uuid_ts_ms BETWEEN ? AND ? ORDER BY f.rsession_id",
            (start_ms, end_ms)):
        add(row[0], "media")

    for row in conn.execute(
            "SELECT DISTINCT e.rsession_id FROM events e "
            "LEFT JOIN recording_sessions s ON s.rsession_id = e.rsession_id "
            "WHERE COALESCE(s.session_date, '') = '' "
            "AND e.ts_ms BETWEEN ? AND ? ORDER BY e.rsession_id",
            (start_ms, end_ms)):
        add(row[0], "events")

    return dict(sorted(sessions.items()))


# ── 快照与计划 ─────────────────────────────────────────────────────────────

@dataclass
class SessionData:
    sid: str
    reasons: list[str]
    session_date: str
    files: list[sqlite3.Row]
    events: list[sqlite3.Row]


def _load_session_data(conn: sqlite3.Connection,
                       sessions: dict[str, list[str]]) -> list[SessionData]:
    out: list[SessionData] = []
    for sid, reasons in sessions.items():
        row = conn.execute(
            "SELECT session_date FROM recording_sessions WHERE rsession_id=?",
            (sid,)).fetchone()
        files = conn.execute(
            "SELECT file_id, rsession_id, file_type, file_path, file_size, "
            "uuid_ts_ms, analysis_status FROM files WHERE rsession_id=? "
            "ORDER BY file_id", (sid,)).fetchall()
        events = conn.execute(
            "SELECT event_id, event_ref, type, ts_ms, payload, device_uuid "
            "FROM events WHERE rsession_id=? ORDER BY event_id",
            (sid,)).fetchall()
        out.append(SessionData(sid=sid, reasons=reasons,
                               session_date=(row["session_date"] if row else ""),
                               files=list(files), events=list(events)))
    return out


def _payload_field(payload: str | None, key: str) -> str | None:
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return None
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def _event_ref_for_file(row: sqlite3.Row) -> str:
    return (f"{row['rsession_id']}:{row['file_type']}:"
            f"{Path(row['file_path']).stem}")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def check_tables(conn: sqlite3.Connection) -> None:
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if missing:
        raise ResetError(
            f"required tables missing: {', '.join(missing)}; "
            "start `lvdata-mcp serve` once (or run `lvdata-mcp reindex`) to "
            "initialize/migrate the schema")


def check_fts_schema(conn: sqlite3.Connection) -> str:
    """返回 'missing'（应创建）或 'ok'；旧 schema 直接中止（D7/§5.4）。"""
    if not _table_exists(conn, "search_docs"):
        return "missing"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(search_docs)")}
    missing = sorted(REQUIRED_FTS_COLUMNS - cols)
    if missing:
        raise ResetError(
            "search_docs schema is outdated (missing columns: "
            f"{', '.join(missing)}); run `lvdata-mcp reindex` first "
            "(refusing to DROP the whole FTS table)")
    return "ok"


def _simulate_window_purge(conn: sqlite3.Connection,
                           data: list[SessionData]) -> dict[str, Any]:
    """只读模拟：最终状态的已知事件删除与 window 归零（plan 用）。

    空 scope / 无 video-window 关联时不做全库 video/window 扫描（review 4）。
    """
    target_sids = {d.sid for d in data}
    known = [e for d in data for e in d.events
             if e["type"] in KNOWN_EVENT_TYPES]
    unknown = [e for d in data for e in d.events
               if e["type"] not in KNOWN_EVENT_TYPES
               and e["type"] != WINDOW_EVENT_TYPE]
    target_wids: set[str] = set()
    target_has_malformed = False
    for d in data:
        for e in d.events:
            if e["type"] not in ("video", WINDOW_EVENT_TYPE):
                continue
            wid = _payload_field(e["payload"], "window_id")
            if wid:
                target_wids.add(wid)
            elif e["type"] == WINDOW_EVENT_TYPE:
                target_has_malformed = True
    if not target_wids and not target_has_malformed:
        return {
            "known": known,
            "unknown": unknown,
            "deleted_windows": [],
            "kept_windows": 0,
            "affected_sessions": set(target_sids),
        }

    deleted_video_refs = {e["event_ref"] for e in known
                          if e["type"] == "video"}
    remaining: Counter[str] = Counter()
    for row in conn.execute("SELECT event_ref, payload FROM events "
                            "WHERE type='video'"):
        if row["event_ref"] in deleted_video_refs:
            continue
        wid = _payload_field(row["payload"], "window_id")
        if wid:
            remaining[wid] += 1

    gone = {wid for wid in target_wids if remaining.get(wid, 0) == 0}
    window_rows = conn.execute(
        "SELECT event_ref, ts_ms, rsession_id, payload FROM events "
        "WHERE type='window'").fetchall()
    deleted_windows: list[sqlite3.Row] = []
    for row in window_rows:
        wid = _payload_field(row["payload"], "window_id")
        if (wid and wid in gone) or (
                not wid and row["rsession_id"] in target_sids):
            deleted_windows.append(row)

    affected = set(target_sids)
    affected.update(row["rsession_id"] for row in deleted_windows)
    kept_windows = sum(
        1 for d in data for e in d.events
        if e["type"] == WINDOW_EVENT_TYPE
        and (wid := _payload_field(e["payload"], "window_id"))
        and wid not in gone)
    return {
        "known": known,
        "unknown": unknown,
        "deleted_windows": deleted_windows,
        "kept_windows": kept_windows,
        "affected_sessions": affected,
    }


def _missing_source_count(data: list[SessionData]) -> int:
    """best-effort：事件源文件/源 chat 文件缺失的数量（D13）。"""
    total = 0

    for d in data:
        exists_cache: dict[str, bool] = {}

        def exists(path: str) -> bool:
            if path not in exists_cache:
                exists_cache[path] = Path(path).is_file()
            return exists_cache[path]

        media_by_stem = {(f["file_type"], Path(f["file_path"]).stem): f
                         for f in d.files if f["file_type"] in MEDIA_FILE_TYPES}
        chat_files = {ft: [f for f in d.files if f["file_type"] == ft]
                      for ft in CHAT_FILE_TYPES}
        chat_available = {
            ft: any(exists(f["file_path"]) for f in files)
            for ft, files in chat_files.items()
        }
        for e in d.events:
            etype = e["type"]
            if etype in MEDIA_FILE_TYPES:
                stem = e["event_ref"].split(":", 2)[-1]
                row = media_by_stem.get((etype, stem))
                if row is None or not exists(row["file_path"]):
                    total += 1
            elif etype in CHAT_EVENT_TO_FILE:
                if not chat_available.get(CHAT_EVENT_TO_FILE[etype], False):
                    total += 1
            elif etype == "agent_job":
                if not chat_available.get("chat_history", False):
                    total += 1
    return total


def _day_summary_plan(conn: sqlite3.Connection, cfg: LvdataMcpConfig,
                      ts_list: list[int]) -> tuple[int, int]:
    if not ts_list:
        return 0, 0
    # 目标时区必须能表示事件日期；否则视为超出支持范围（真正的下溢/上溢）。
    for ts in ts_list:
        _safe_local_date(ts, cfg.watcher.timezone)
    active = set(active_timezones(conn, cfg.watcher.timezone))
    tzs = [r[0] for r in conn.execute(
        "SELECT DISTINCT timezone FROM day_summary")]
    active_rows = inactive_rows = 0
    for tz in tzs:
        days: set[str] = set()
        for ts in ts_list:
            try:
                days.add(_safe_local_date(ts, tz))
            except LocalDateOutOfRange:
                # 该时区下事件的本地日期超出 0001..9999，不可能有日卡行
                continue
        for day in days:
            if conn.execute(
                    "SELECT 1 FROM day_summary WHERE timezone=? AND local_date=?",
                    (tz, day)).fetchone() is None:
                continue
            if tz in active:
                active_rows += 1
            else:
                inactive_rows += 1
    return active_rows, inactive_rows


def _fts_doc_count(conn: sqlite3.Connection, sessions: set[str]) -> int:
    if not sessions or not _table_exists(conn, "search_docs"):
        return 0
    return sum(conn.execute(
        "SELECT COUNT(*) FROM search_docs WHERE session_id=?",
        (sid,)).fetchone()[0] for sid in sorted(sessions))


def build_plan(cfg: LvdataMcpConfig, conn: sqlite3.Connection,
               date_spec: str, data: list[SessionData],
               start_date: str, end_date: str,
               start_ms: int, end_ms: int) -> dict[str, Any]:
    if not data:
        # 空 scope 不扫描全库 video/window（review 4）
        return {
            "date": date_spec,
            "timezone": cfg.watcher.timezone,
            "date_from": start_date,
            "date_to": end_date,
            "sessions": [],
            "files_in_scope": {},
            "out_of_range": {"files": 0, "events": 0},
            "delete_candidates": {},
            "delete_bytes": 0,
            "events_to_purge": {},
            "window_events": {"matched": 0, "after_zeroing": 0},
            "unknown_event_types": {},
            "fts_docs": 0,
            "vector_refs": 0,
            "day_summary": {"active_rows": 0, "inactive_rows": 0},
            "reset_files": {},
            "missing_source_events": 0,
            "warnings": [],
        }
    sim = _simulate_window_purge(conn, data)
    known: list[sqlite3.Row] = sim["known"]
    deleted_windows: list[sqlite3.Row] = sim["deleted_windows"]
    unknown: list[sqlite3.Row] = sim["unknown"]

    refs = {e["event_ref"] for e in known}
    ts_list = [int(e["ts_ms"]) for e in known] + \
              [int(w["ts_ms"]) for w in deleted_windows]
    affected = sim["affected_sessions"]

    files_in_scope: Counter[str] = Counter()
    delete_candidates: Counter[str] = Counter()
    reset_files: Counter[str] = Counter()
    delete_bytes = 0
    out_files = out_events = 0
    for d in data:
        session_refs = {e["event_ref"] for e in d.events}
        for f in d.files:
            files_in_scope[f["file_type"]] += 1
            if f["file_type"] in MEDIA_FILE_TYPES:
                if (f["analysis_status"] == "ok"
                        and _event_ref_for_file(f) not in session_refs):
                    delete_candidates[f["file_type"]] += 1
                    delete_bytes += int(f["file_size"] or 0)
                else:
                    reset_files[f["file_type"]] += 1
                ts = f["uuid_ts_ms"]
                if ts is not None and not (start_ms <= ts <= end_ms):
                    out_files += 1
            elif f["file_type"] in CHAT_FILE_TYPES:
                reset_files["chat"] += 1
        out_events += sum(
            1 for e in d.events if not (start_ms <= int(e["ts_ms"]) <= end_ms))

    purge_by_type: Counter[str] = Counter(e["type"] for e in known)
    purge_by_type[WINDOW_EVENT_TYPE] = len(deleted_windows)
    unknown_by_type: Counter[str] = Counter(e["type"] for e in unknown)
    active_rows, inactive_rows = _day_summary_plan(conn, cfg, ts_list)
    missing_source = _missing_source_count(data)

    warnings = [
        "audio/video files with no event and status ok will be deleted "
        "(no backup)",
        "video non-key frames will be deleted: re-analysis is NOT an "
        "equivalent re-run (window regrouping / key-frame selection may change)",
        "chat/meta source files are never deleted",
        "known events and derived audit JSON for the selected sessions "
        "will be purged",
    ]
    unknown_file_types = sorted(
        set(files_in_scope) - set(MEDIA_FILE_TYPES) - set(CHAT_FILE_TYPES)
        - {"meta"})
    if unknown_file_types:
        warnings.append("unknown file types are skipped: "
                        + ", ".join(unknown_file_types))
    if unknown_by_type:
        warnings.append("unknown event types are kept and will not be "
                        "regenerated")
    if sim["kept_windows"]:
        warnings.append("video window events with remaining video events "
                        "outside scope are kept")
    if missing_source:
        warnings.append(f"source files missing for {missing_source} event(s); "
                        "they cannot be regenerated")

    return {
        "date": date_spec,
        "timezone": cfg.watcher.timezone,
        "date_from": start_date,
        "date_to": end_date,
        "sessions": [
            {"rsession_id": d.sid, "session_date": d.session_date,
             "reasons": d.reasons} for d in data
        ],
        "files_in_scope": dict(files_in_scope),
        "out_of_range": {"files": out_files, "events": out_events},
        "delete_candidates": dict(delete_candidates),
        "delete_bytes": delete_bytes,
        "events_to_purge": dict(purge_by_type),
        "window_events": {
            "matched": sum(1 for d in data for e in d.events
                           if e["type"] == WINDOW_EVENT_TYPE),
            "after_zeroing": len(deleted_windows),
        },
        "unknown_event_types": dict(unknown_by_type),
        "fts_docs": _fts_doc_count(conn, affected),
        "vector_refs": len(refs) + len(deleted_windows),
        "day_summary": {"active_rows": active_rows,
                        "inactive_rows": inactive_rows},
        "reset_files": dict(reset_files),
        "missing_source_events": missing_source,
        "warnings": warnings,
    }


def format_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"reset plan: date={plan['date']} timezone={plan['timezone']}",
        f"  date window: {plan['date_from']} .. {plan['date_to']}",
        f"  sessions: {len(plan['sessions'])}",
        "  session hits:",
    ]
    for s in plan["sessions"]:
        session_date = s["session_date"] or "''"
        reasons = "/".join(s["reasons"])
        lines.append(f"    - {s['rsession_id']} session_date={session_date} "
                     f"{reasons}")
    files = plan["files_in_scope"]
    lines.append("  files in scope: " + " ".join(
        f"{k}={v}" for k, v in sorted(files.items())))
    oor = plan["out_of_range"]
    lines.append(f"  out of range (session amplification): "
                 f"files={oor['files']} events={oor['events']}")
    cand = plan["delete_candidates"]
    lines.append("  delete candidates: " + " ".join(
        f"{k}={v}" for k, v in sorted(cand.items())) +
        f" ({plan['delete_bytes']} bytes)")
    purge = plan["events_to_purge"]
    lines.append("  events to purge: " + " ".join(
        f"{k}={v}" for k, v in sorted(purge.items())))
    win = plan["window_events"]
    lines.append(f"  window events: {win['matched']} "
                 f"(after zeroing {win['after_zeroing']})")
    if plan["unknown_event_types"]:
        lines.append("  unknown event types (kept): " + ", ".join(
            f"{k}={v}" for k, v in sorted(
                plan["unknown_event_types"].items())))
    lines.append(f"  fts docs: {plan['fts_docs']}")
    lines.append(f"  vector refs: {plan['vector_refs']} "
                 "(if a local vector database exists)")
    ds = plan["day_summary"]
    lines.append(f"  day_summary affected: active={ds['active_rows']} "
                 f"inactive={ds['inactive_rows']} "
                 "(all -> pending; retried on reactivation)")
    rf = plan["reset_files"]
    lines.append("  reset: " + " ".join(
        f"{k}={v}" for k, v in sorted(rf.items())))
    lines.append(f"  missing_source_events: {plan['missing_source_events']}")
    lines.append("  warnings:")
    for warning in plan["warnings"]:
        lines.append(f"    - {warning}")
    return "\n".join(lines)


# ── server 探测 ────────────────────────────────────────────────────────────

def server_status(cfg: LvdataMcpConfig) -> str:
    """三态探测：running / stopped / unknown。

    只有明确的 connection refused 才视为 stopped；超时、DNS 及其他异常一律
    unknown（fail-closed：破坏性 reset 不得因探测不确定而放行）。
    """
    host = (cfg.server.host or "127.0.0.1").strip()
    if host in ("0.0.0.0", "*", ""):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = f"http://{host}:{cfg.server.port}/healthz"
    # 本地健康检查必须绕过环境代理：HTTP_PROXY 不可用时否则会误判 stopped
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1.0):
            return "running"
    except urllib.error.HTTPError:
        # 401/403/404 等同样证明端口上有服务在响应
        return "running"
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ConnectionRefusedError) or \
                getattr(reason, "errno", None) == errno.ECONNREFUSED:
            return "stopped"
        return "unknown"
    except (socket.timeout, socket.gaierror):
        return "unknown"
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ECONNREFUSED:
            return "stopped"
        return "unknown"
    except ValueError:
        return "unknown"


def server_is_running(cfg: LvdataMcpConfig) -> bool:
    """兼容旧调用点：仅明确 running 返回 True。"""
    return server_status(cfg) == "running"


# ── 执行 ───────────────────────────────────────────────────────────────────

def _ensure_schema(cfg: LvdataMcpConfig) -> None:
    """仅在实际执行前调用（plan/dry-run/拒绝路径不得触发建表/迁移/WAL）。"""
    conn = sqlite3.connect(str(cfg.data.resolved_db_path), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        ddl.ensure_schema(conn, cfg.watcher.timezone, cfg.watcher.timezones)
        conn.commit()
    finally:
        conn.close()


def _open_db(cfg: LvdataMcpConfig, *, readonly: bool = False) -> sqlite3.Connection:
    path = str(cfg.data.resolved_db_path)
    if readonly:
        p = Path(path).absolute()
        if not p.is_file():
            raise sqlite3.OperationalError(f"database not found: {path}")
        # as_uri() 会 percent-encode ?/#/% 等 URI 保留字符；手拼 file: 会截断路径
        conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=10)
    else:
        conn = sqlite3.connect(path, timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    return conn


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _delete_no_event_media(
        conn: sqlite3.Connection, root: Path, sid: str,
        result: dict[str, Any]) -> tuple[list[int], list[int]]:
    """unlink 会话内 `ok` + 无事件的媒体文件（行删除留给主事务）。

    返回 (unlinked_ids, failed_ids)。每成功删除一个物理文件立即累加
    `result["files_unlinked"]`/`bytes_unlinked`，后续 resolve/DB 异常也不会漏账。
    failed_ids 保持 `ok`，下次 reset 才会重新进入删除候选并重试。
    """
    candidates = conn.execute(
        "SELECT file_id FROM files WHERE rsession_id=? "
        "AND file_type IN ('audio','video') AND analysis_status='ok'",
        (sid,)).fetchall()
    unlinked: list[int] = []
    failed: list[int] = []
    for start in range(0, len(candidates), MEDIA_DELETE_BATCH):
        _begin(conn)
        try:
            for cand in candidates[start:start + MEDIA_DELETE_BATCH]:
                row = conn.execute(
                    "SELECT file_type, file_path, file_size, analysis_status "
                    "FROM files WHERE file_id=?", (cand["file_id"],)).fetchone()
                if row is None or row["analysis_status"] != "ok":
                    continue
                ref = (f"{sid}:{row['file_type']}:"
                       f"{Path(row['file_path']).stem}")
                if conn.execute("SELECT 1 FROM events WHERE event_ref=?",
                                (ref,)).fetchone() is not None:
                    continue
                path = Path(row["file_path"]).resolve()
                if not path.is_relative_to(root):
                    log.warning("reset path outside root, skipped",
                                path=row["file_path"])
                    continue
                existed = path.exists()
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("reset unlink failed", path=str(path),
                                error=str(exc))
                    failed.append(cand["file_id"])
                    continue
                unlinked.append(cand["file_id"])
                if existed:
                    result["files_unlinked"] += 1
                    result["bytes_unlinked"] += int(row["file_size"] or 0)
            conn.execute("COMMIT")
        except BaseException:
            _rollback(conn)
            raise
    return unlinked, failed


def _delete_collected_media_rows(conn: sqlite3.Connection,
                                 file_ids: list[int]) -> tuple[int, int]:
    """主事务内删除已 unlink 的 files 行；行与 event purge 一起提交/回滚。"""
    deleted = freed = 0
    for file_id in file_ids:
        row = conn.execute(
            "SELECT rsession_id, file_type, file_path, file_size "
            "FROM files WHERE file_id=?", (file_id,)).fetchone()
        if row is None:
            continue
        conn.execute("DELETE FROM files WHERE file_id=?", (file_id,))
        deleted += 1
        freed += int(row["file_size"] or 0)
    return deleted, freed


def _delete_derived(cfg: LvdataMcpConfig, conn: sqlite3.Connection,
                    sid: str) -> int:
    """删除目标 session 的 derived 审计 JSON（best-effort：失败只告警）。"""
    deleted = 0
    try:
        row = conn.execute(
            "SELECT device_uuid FROM recording_sessions WHERE rsession_id=?",
            (sid,)).fetchone()
        device = row["device_uuid"] if row else None
        if not device:
            row = conn.execute(
                "SELECT device_uuid FROM files WHERE rsession_id=? LIMIT 1",
                (sid,)).fetchone()
            device = row["device_uuid"] if row else None
        if not device:
            log.warning("reset derived skipped: session/device not found",
                        session=sid)
            return 0
        root = Path(cfg.data.root_dir).resolve()
        derived_root = (root / "derived").resolve()
        base = (derived_root / device / sid).resolve()
        if not base.is_relative_to(derived_root):
            log.warning("reset derived path outside root, skipped",
                        path=str(base))
            return 0
        for pattern in ("audio/*.json", "video/windows/*.json",
                        "video/frames/*.json"):
            try:
                paths = list(base.glob(pattern))
            except OSError as exc:
                log.warning("reset derived glob failed", path=str(base),
                            error=str(exc))
                continue
            for path in paths:
                try:
                    resolved = path.resolve()
                    if not resolved.is_relative_to(derived_root):
                        log.warning("reset derived path outside root, skipped",
                                    path=str(resolved))
                        continue
                    resolved.unlink(missing_ok=True)
                    deleted += 1
                except OSError as exc:
                    log.warning("reset derived unlink failed",
                                path=str(path), error=str(exc))
    except Exception:  # noqa: BLE001 — best-effort，不阻塞主事务
        log.warning("reset derived cleanup failed", exc_info=True)
    return deleted


@dataclass
class PurgeResult:
    refs: list[str] = field(default_factory=list)
    ts_list: list[int] = field(default_factory=list)
    affected_sessions: set[str] = field(default_factory=set)
    deleted_by_type: Counter = field(default_factory=Counter)
    window_deleted: int = 0
    kept_windows: int = 0
    unknown_kept: Counter = field(default_factory=Counter)
    cross_window_audits: list[tuple[str, str, str]] = field(
        default_factory=list)
    refs_by_session: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class WindowState:
    """跨 session window 的共享剩余计数（执行期只全表扫描一次）。"""

    remaining: Counter[str] = field(default_factory=Counter)
    rows_by_wid: dict[str, list[dict]] = field(default_factory=dict)
    malformed_by_sid: dict[str, list[dict]] = field(default_factory=dict)


def _build_window_state(conn: sqlite3.Connection) -> WindowState:
    state = WindowState()
    rows = conn.execute(
        "SELECT event_id, event_ref, type, ts_ms, rsession_id, device_uuid, "
        "payload FROM events WHERE type IN ('video','window')").fetchall()
    for r in rows:
        wid = _payload_field(r["payload"], "window_id")
        if r["type"] == "video":
            if wid:
                state.remaining[wid] += 1
        elif wid:
            state.rows_by_wid.setdefault(wid, []).append(dict(r))
        else:
            state.malformed_by_sid.setdefault(
                r["rsession_id"], []).append(dict(r))
    return state


def _purge_events_tx(conn: sqlite3.Connection, sid: str,
                     window_state: WindowState) -> PurgeResult:
    """主事务内：删已知非 window 事件 + window 归零剔除（D6）。

    window 剩余计数由 `WindowState` 跨 session 维护，避免每个 session 全表
    扫描 video/window events（review issue 8）。
    """
    result = PurgeResult()
    rows = conn.execute(
        "SELECT event_id, event_ref, type, ts_ms, payload, device_uuid "
        "FROM events WHERE rsession_id=? ORDER BY event_id", (sid,)).fetchall()
    known = [r for r in rows if r["type"] in KNOWN_EVENT_TYPES]
    unknown = [r for r in rows if r["type"] not in KNOWN_EVENT_TYPES
               and r["type"] != WINDOW_EVENT_TYPE]
    window_sid = [r for r in rows if r["type"] == WINDOW_EVENT_TYPE]

    result.unknown_kept.update(r["type"] for r in unknown)
    if unknown:
        log.warning("reset kept unknown event types",
                    session=sid, types=dict(result.unknown_kept))

    placeholders = ",".join("?" * len(KNOWN_EVENT_TYPES))
    conn.execute(
        f"DELETE FROM events WHERE rsession_id=? AND type IN ({placeholders})",
        (sid, *KNOWN_EVENT_TYPES))

    target_wids: set[str] = set()
    for r in known:
        if r["type"] == "video":
            wid = _payload_field(r["payload"], "window_id")
            if wid:
                target_wids.add(wid)
                if window_state.remaining.get(wid, 0) > 0:
                    window_state.remaining[wid] -= 1
    for r in window_sid:
        wid = _payload_field(r["payload"], "window_id")
        if wid:
            target_wids.add(wid)
    gone = {wid for wid in target_wids
            if window_state.remaining.get(wid, 0) == 0}

    deleted_rows: list[dict] = []
    for wid in gone:
        deleted_rows.extend(window_state.rows_by_wid.pop(wid, []))
    deleted_rows.extend(window_state.malformed_by_sid.pop(sid, []))
    for r in deleted_rows:
        wid = _payload_field(r["payload"], "window_id")
        if wid is None:
            log.warning("reset deleted malformed window event", session=sid,
                        event_ref=r["event_ref"])
        conn.execute("DELETE FROM events WHERE event_id=?", (r["event_id"],))
        ref = r["event_ref"]
        result.window_deleted += 1
        result.refs.append(ref)
        result.ts_list.append(int(r["ts_ms"]))
        result.affected_sessions.add(r["rsession_id"])
        result.refs_by_session.setdefault(r["rsession_id"], []).append(ref)
        if r["rsession_id"] != sid and wid:
            result.cross_window_audits.append(
                (r["device_uuid"], r["rsession_id"], wid))

    result.kept_windows = sum(
        1 for r in window_sid
        if (wid := _payload_field(r["payload"], "window_id")) and wid not in gone)
    result.deleted_by_type.update(r["type"] for r in known)
    result.deleted_by_type[WINDOW_EVENT_TYPE] = result.window_deleted
    known_refs = [r["event_ref"] for r in known]
    result.refs_by_session.setdefault(sid, []).extend(known_refs)
    result.refs = known_refs + result.refs
    result.ts_list = [int(r["ts_ms"]) for r in known] + result.ts_list
    result.affected_sessions.add(sid)
    return result


def _rebuild_fts(conn: sqlite3.Connection, sid: str) -> int:
    """重建 session FTS；缺 recording_sessions 行时用 events.device_uuid 兜底。

    旧实现直接清空该 session 的 search_docs，会把保留的未知事件索引一起删掉，
    且后续 reindex 无法自愈（review issue 5）。
    """
    row = conn.execute(
        "SELECT 1 FROM recording_sessions WHERE rsession_id=?",
        (sid,)).fetchone()
    if row is not None:
        return index_mod.rebuild_session(conn, None, sid)
    events = conn.execute(
        "SELECT event_ref, type, ts_ms, summary, device_uuid FROM events "
        "WHERE rsession_id=? ORDER BY ts_ms", (sid,)).fetchall()
    conn.execute("DELETE FROM search_docs WHERE session_id=?", (sid,))
    docs = []
    for ev in events:
        raw = ev["summary"] or ""
        text = index_mod.cjk_bigram(raw)
        if text:
            docs.append((text, raw, ev["event_ref"], sid, ev["device_uuid"],
                         ev["type"], ev["ts_ms"]))
    if docs:
        conn.executemany(
            "INSERT INTO search_docs (text, orig_text, event_ref, session_id, "
            "device_uuid, type, ts_ms) VALUES (?, ?, ?, ?, ?, ?, ?)", docs)
    return len(docs)


def _reset_days(conn: sqlite3.Connection, cfg: LvdataMcpConfig,
                ts_list: list[int]) -> tuple[int, int, int]:
    """重置受影响日：所有时区统一置 pending，保留再激活后的重试信号（review 3）。

    非活跃时区不能再直接删行（否则游标已过、再激活后无 pending 行可重试）。
    `recompute_days` 对活跃/非活跃时区都执行（仅本地 SQL，无 LLM）。
    """
    if not ts_list:
        return 0, 0, 0
    # 目标时区必须能表示事件日期；其它时区按 LocalDateOutOfRange 跳过。
    for ts in ts_list:
        _safe_local_date(ts, cfg.watcher.timezone)
    tzs = [r[0] for r in conn.execute(
        "SELECT DISTINCT timezone FROM day_summary")]
    day_stats = DayStats(cfg)
    t0 = now_iso()
    reset = deleted = recomputed = 0
    for tz in tzs:
        days_set: set[str] = set()
        for ts in ts_list:
            try:
                days_set.add(_safe_local_date(ts, tz))
            except LocalDateOutOfRange:
                continue
        days = sorted(days_set)
        reset_days: list[str] = []
        for day in days:
            cur = conn.execute(
                "UPDATE day_summary SET status='pending', generated_at=NULL, "
                "fail_count=0, error=NULL, model=NULL, day_summary='{}', "
                "event_count=0, day_stats='{}' "
                "WHERE timezone=? AND local_date=?", (tz, day))
            reset += cur.rowcount
            if cur.rowcount:
                reset_days.append(day)
            conn.execute(
                "DELETE FROM day_deferred WHERE timezone=? AND local_date=?",
                (tz, day))
        recomputed += len(day_stats.recompute_days(conn, tz, days, t0))
        # recompute_days 在“无剩余事件且非 ok”时会删行；计入 deleted
        for day in reset_days:
            if conn.execute(
                    "SELECT 1 FROM day_summary "
                    "WHERE timezone=? AND local_date=?",
                    (tz, day)).fetchone() is None:
                deleted += 1
    return reset, deleted, recomputed


def _reset_files(conn: sqlite3.Connection, sid: str) -> tuple[int, int]:
    """重置保留文件状态（unlink 失败时整个 session 主事务已延后）。"""
    media = conn.execute(
        "UPDATE files SET analysis_status='pending', fail_count=0, error=NULL, "
        "analyzed_at=NULL WHERE rsession_id=? "
        "AND file_type IN ('audio','video')", (sid,)).rowcount
    chat_ph = ",".join("?" * len(CHAT_FILE_TYPES))
    chat = conn.execute(
        f"UPDATE files SET processed_offset=0, processed_updated_at=NULL, "
        f"fail_count=0, analysis_status='na', error=NULL "
        f"WHERE rsession_id=? AND file_type IN ({chat_ph})",
        (sid, *CHAT_FILE_TYPES)).rowcount
    return media, chat


class _LocalEmbeddingProvider:
    """仅用于本地 vec DB 清理：remove_refs/sweep_orphans 不调用 embed。"""

    model = "local"

    def __init__(self, dim: int):
        self.dim = dim


def _build_vec(cfg: LvdataMcpConfig) -> VecIndex | None:
    """本地向量清理不依赖 embedding API key（review issue 6）。

    有 provider 用真实 provider；没有但磁盘上存在 vec DB 时用本地 dim 兜底，
    因为 remove_refs/sweep_orphans 只做 SQL 删除。无 DB 则无需清理。
    """
    provider = build_provider(cfg)
    if provider is None:
        rag_path = Path(cfg.data.resolved_rag_db_path)
        if not rag_path.is_file():
            return None
        provider = _LocalEmbeddingProvider(
            cfg.providers.embedding.dim or 1024)
    return VecIndex(cfg, DataStore(cfg), provider)


def _remove_vectors(vec: VecIndex, refs: list[str]) -> tuple[int, bool]:
    removed = 0
    for i in range(0, len(refs), VECTOR_BATCH):
        try:
            removed += vec.remove_refs(refs[i:i + VECTOR_BATCH],
                                       create_schema=False)
        except Exception:  # noqa: BLE001 — T1 已提交；孤儿交给 sweep_orphans
            log.warning("reset vector removal failed", exc_info=True)
            return removed, True
    return removed, False


def _sweep_vectors(vec: VecIndex) -> tuple[bool, int]:
    try:
        return False, vec.sweep_orphans(create_schema=False)
    except Exception:  # noqa: BLE001
        log.warning("reset vector sweep failed", exc_info=True)
        return True, 0


def _delete_cross_window_audits(
        root: Path, rows: list[tuple[str, str, str]]) -> int:
    """删除 scope 外 session 的跨窗审计 JSON（best-effort）。"""
    derived_root = (root / "derived").resolve()
    deleted = 0
    for device, sid, window_id in dict.fromkeys(rows):
        try:
            path = (derived_root / device / sid / "video" / "windows"
                    / f"{window_id}.json").resolve()
            if not path.is_relative_to(derived_root):
                continue
            existed = path.exists()
            path.unlink(missing_ok=True)
            if existed:
                deleted += 1
        except Exception:  # noqa: BLE001 — 审计清理失败不影响 reset 结果
            log.warning("reset cross-window audit unlink failed",
                        device=device, session=sid, window_id=window_id,
                        exc_info=True)
    return deleted


def _execute(cfg: LvdataMcpConfig, conn: sqlite3.Connection,
             session_ids: list[str], fts_state: str, *,
             allow_running_server: bool = False,
             needs_window_state: bool = True) -> dict[str, Any]:
    root = Path(cfg.data.root_dir).resolve()
    result: dict[str, Any] = {
        "sessions": len(session_ids),
        "sessions_failed": [],
        "files_deleted": 0,
        "bytes_deleted": 0,
        "files_unlinked": 0,
        "bytes_unlinked": 0,
        "failed_unlinks": 0,
        "events_deleted": 0,
        "events_deleted_by_type": {},
        "window_events_deleted": 0,
        "unknown_kept": 0,
        "fts_rebuilt_docs": 0,
        "index_state_deleted": 0,
        "day_summary_reset": 0,
        "day_summary_deleted": 0,
        "day_stats_recomputed": 0,
        "derived_deleted": 0,
        "vectors_removed": 0,
        "vectors_swept": 0,
        "vectors_pending": False,
        "sessions_deferred": [],
        "partial": False,
    }
    deleted_by_type: Counter[str] = Counter()
    unknown_kept: Counter[str] = Counter()

    if not session_ids:
        # 空 scope 也要清理本地向量孤儿（review3 2b）
        vec = _build_vec(cfg)
        if vec is not None:
            pending, swept = _sweep_vectors(vec)
            result["vectors_pending"] = pending
            result["vectors_swept"] += swept
            result["partial"] = pending
        return result

    if fts_state == "missing":
        _begin(conn)
        try:
            index_mod.ensure_index(conn)
            conn.execute("COMMIT")
        except BaseException:
            _rollback(conn)
            raise

    vec = _build_vec(cfg)
    if vec is None:
        log.warning("vector cleanup skipped: no local vector database")

    window_state = (_build_window_state(conn) if needs_window_state
                    else WindowState())
    for sid in session_ids:
        if allow_running_server:
            # 运行中的 server 可能插入新事件；共享状态会过期，按 session 重建
            window_state = _build_window_state(conn)
        try:
            file_ids, failed_ids = _delete_no_event_media(conn, root, sid,
                                                          result)
            if failed_ids:
                # 不提交该 session 主事务：保持 events/files 行原状，下一轮仍能
                # 通过 primary/media/events 任一入口选中并重试（review3 issue 1）。
                result["failed_unlinks"] += len(failed_ids)
                result["sessions_deferred"].append(sid)
                log.warning("reset session deferred due to unlink failures",
                            session=sid, failed=len(failed_ids))
                continue
            result["derived_deleted"] += _delete_derived(cfg, conn, sid)

            _begin(conn)
            try:
                purge = _purge_events_tx(conn, sid, window_state)
                # 跨窗 derived 在主事务内尽早删除：提交后崩溃也不会留下
                # 无法从已删 window event 重建的旧 JSON（review3 issue 4）。
                derived_extra = _delete_cross_window_audits(
                    root, purge.cross_window_audits)
                # 物理删除已发生，即使主事务回滚也计入
                result["derived_deleted"] += derived_extra
                media_deleted, media_freed = _delete_collected_media_rows(
                    conn, file_ids)
                docs = sum(_rebuild_fts(conn, s)
                           for s in purge.affected_sessions)
                cur = conn.execute(
                    "DELETE FROM index_state WHERE target=?", (sid,))
                day_reset, day_deleted, day_recomputed = _reset_days(
                    conn, cfg, purge.ts_list)
                media_reset, chat_reset = _reset_files(conn, sid)
                conn.execute("COMMIT")
            except BaseException:
                _rollback(conn)
                raise

            result["files_deleted"] += media_deleted
            result["bytes_deleted"] += media_freed
            result["fts_rebuilt_docs"] += docs
            result["index_state_deleted"] += cur.rowcount
            result["day_summary_reset"] += day_reset
            result["day_summary_deleted"] += day_deleted
            result["day_stats_recomputed"] += day_recomputed
            deleted_by_type.update(purge.deleted_by_type)
            unknown_kept.update(purge.unknown_kept)
            log.info("reset session done", session=sid,
                     media_reset=media_reset, chat_reset=chat_reset,
                     events=len(purge.refs))
        except Exception:  # noqa: BLE001 — 单 session 失败不阻塞其余
            log.exception("reset session failed", session=sid)
            result["sessions_failed"].append(sid)
            window_state = _build_window_state(conn)
            continue

        if vec is not None and purge.refs:
            removed, pending = _remove_vectors(vec, purge.refs)
            result["vectors_removed"] += removed
            result["vectors_pending"] = result["vectors_pending"] or pending

    if vec is not None:
        # 无条件 sweep：remove_refs 失败时更需要孤儿清扫（review3 2a）
        sweep_pending, swept = _sweep_vectors(vec)
        result["vectors_swept"] += swept
        result["vectors_pending"] = result["vectors_pending"] or sweep_pending
    result["events_deleted"] = sum(deleted_by_type.values())
    result["events_deleted_by_type"] = {
        k: v for k, v in sorted(deleted_by_type.items()) if v}
    result["window_events_deleted"] = deleted_by_type.get(WINDOW_EVENT_TYPE, 0)
    result["unknown_kept"] = sum(unknown_kept.values())
    result["partial"] = bool(
        result["sessions_failed"] or result["failed_unlinks"]
        or result["vectors_pending"])
    return result


def format_summary(result: dict[str, Any]) -> str:
    partial = bool(result.get("partial"))
    lines = [
        "reset partially completed:" if partial else "reset done:",
        f"  sessions={result['sessions']} "
        f"failed_sessions={len(result['sessions_failed'])}",
        f"  files_deleted={result['files_deleted']} "
        f"bytes={result['bytes_deleted']} "
        f"files_unlinked={result['files_unlinked']} "
        f"bytes_unlinked={result['bytes_unlinked']} "
        f"failed_unlinks={result['failed_unlinks']}",
        f"  events_deleted={result['events_deleted']} "
        f"window={result['window_events_deleted']} "
        f"unknown_kept={result['unknown_kept']}",
        f"  fts_rebuilt_docs={result['fts_rebuilt_docs']} "
        f"index_state_deleted={result['index_state_deleted']}",
        f"  day_summary_reset={result['day_summary_reset']} "
        f"day_summary_deleted={result['day_summary_deleted']} "
        f"day_stats_recomputed={result['day_stats_recomputed']}",
        f"  derived_deleted={result['derived_deleted']} "
        f"vectors_removed={result['vectors_removed']} "
        f"vectors_swept={result['vectors_swept']} "
        f"vectors_pending={result['vectors_pending']}",
    ]
    if result["sessions_failed"]:
        lines.append("  failed: " + ", ".join(result["sessions_failed"]))
    if result.get("sessions_deferred"):
        lines.append("  deferred: " + ", ".join(result["sessions_deferred"]))
    if partial:
        lines.append("  rerun reset to retry failed/partial items")
    return "\n".join(lines)


# ── 入口 ───────────────────────────────────────────────────────────────────

def run_reset(cfg: LvdataMcpConfig, *, date_spec: str, dry_run: bool = False,
              yes: bool = False, as_json: bool = False,
              allow_running_server: bool = False) -> int:
    """执行 reset；返回退出码（0 成功 / 1 失败或部分完成 / 2 参数/确认缺失）。

    plan、dry-run、未确认、日期非法、server 拒绝路径都只读：不建表、不迁移、
    不切换 WAL。schema 迁移仅发生在真正执行前且 server 前置检查通过之后。
    """
    try:
        start_date, end_date = parse_date_spec(date_spec)
        tz = cfg.watcher.timezone
        start_ms, end_ms = _ms_bounds(start_date, end_date, tz)
    except ValueError as exc:
        _emit_error(as_json, "invalid_date", str(exc))
        return 2

    # ── 只读路径：dry-run / 未确认。不得建表、迁移或切换 WAL ──────────────
    if dry_run or not yes:
        try:
            plan_conn = _open_db(cfg, readonly=True)
        except sqlite3.Error as exc:
            _emit_error(as_json, "database_unavailable", str(exc))
            return 1
        try:
            check_tables(plan_conn)
            check_fts_schema(plan_conn)
            sessions = select_sessions(plan_conn, start_date, end_date,
                                       start_ms, end_ms)
            data = _load_session_data(plan_conn, sessions)
            plan = build_plan(cfg, plan_conn, date_spec, data, start_date,
                              end_date, start_ms, end_ms)
            del data
        except ResetError as exc:
            _emit_error(as_json, "schema_error", str(exc))
            return 1
        except Exception as exc:  # noqa: BLE001 — 预检/计划阶段的任何失败
            _emit_error(as_json, "plan_failed", str(exc))
            return 1
        finally:
            plan_conn.close()

        if not as_json:
            print(format_plan(plan))
        if dry_run:
            if as_json:
                print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        if as_json:
            print(json.dumps({**plan, "confirmation_required": True},
                             ensure_ascii=False, indent=2))
        else:
            print("re-run with --yes to execute")
        return 2

    # ── 执行路径：先探测 server，再迁移 schema，再写 ─────────────────────
    try:
        status = server_status(cfg)
        override_warning = None
        if status != "stopped":
            if not allow_running_server:
                message = (f"lvdata-mcp server status is {status}; stop it "
                           "first (stop -> reset -> start) or pass "
                           "--allow-running-server")
                code = ("server_running" if status == "running"
                        else "server_status_unknown")
                _emit_error(as_json, code, message, server_status=status)
                return 1
            override_warning = (
                f"server status is {status}; proceeding with "
                "--allow-running-server (stale derived-data risk)")
            if not as_json:
                print(f"WARNING: {override_warning}", file=sys.stderr)

        if not Path(cfg.data.resolved_db_path).is_file():
            _emit_error(as_json, "database_not_found",
                        f"database not found: {cfg.data.resolved_db_path}")
            return 1

        # 真正执行前才允许 schema 迁移/WAL 切换
        _ensure_schema(cfg)
        conn = _open_db(cfg, readonly=False)
        try:
            check_tables(conn)
            fts_state = check_fts_schema(conn)
            sessions = select_sessions(conn, start_date, end_date,
                                       start_ms, end_ms)
            data = _load_session_data(conn, sessions)
            plan = build_plan(cfg, conn, date_spec, data, start_date,
                              end_date, start_ms, end_ms)
            session_ids = [d.sid for d in data]
            del data
            needs_window_state = bool(
                plan["events_to_purge"].get("video")
                or plan["window_events"]["matched"]) or allow_running_server
            if not as_json:
                print(format_plan(plan))
            result = _execute(cfg, conn, session_ids, fts_state,
                              allow_running_server=allow_running_server
                              and override_warning is not None,
                              needs_window_state=needs_window_state)
            if as_json:
                out: dict[str, Any] = {"plan": plan, "result": result}
                if override_warning:
                    out["warnings"] = [override_warning]
                    out["server_status"] = status
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                print(format_summary(result))
            return 1 if result["partial"] else 0
        finally:
            conn.close()
    except ResetError as exc:
        _emit_error(as_json, "schema_error", str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 — 保证 CLI 退出码语义
        log.exception("reset failed")
        _emit_error(as_json, "reset_failed", str(exc))
        return 1
