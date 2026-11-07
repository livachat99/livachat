"""分析模块共享：取批/审计 JSON/文件状态机/analysis_runs（§5.2/§5.3/§6.3）。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from ..timeutil import now_iso

MAX_FAILURES = 3
BACKOFF_DELAYS = (60, 300, 900)  # 失败重试退避（秒），与 v1 BACKOFF_DELAYS 对齐


def atomic_write(path: Path, data: str) -> None:
    """tmp + rename，崩溃不留半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def derived_dir(root: Path, device_uuid: str, rsession_id: str) -> Path:
    return root / "derived" / device_uuid / rsession_id


def select_pending_files(conn: sqlite3.Connection, file_type: str,
                         session_id: str | None,
                         limit: int | None) -> list[sqlite3.Row]:
    """§6.3.1/§6.4.1 取批（cs 单链路）：pending/failed、未暂停、重试窗口已过。

    处理原则（recording_data_process.md §2）：**不按 session 过滤**——idle/未
    finalized 会话的文件不再滞留（移除旧 `s.status IN ('active','finalized')`
    取值门）；session 只在显式 target 时作过滤。JOIN 保留（audio_segment_s
    等会话属性）。failed 文件按 fail_count 取退避档（60/300/900s），退避条件
    **下推 SQL**——积压 + 大量失败时 LIMIT 截断不再饿死 eligible 文件。
    limit <= 0 或 None = 不限（§10.2）。
    """
    now = now_iso()
    sql = (
        "SELECT f.*, s.device_uuid AS s_device_uuid, "
        "s.audio_segment_s AS s_audio_segment_s "
        "FROM files f JOIN recording_sessions s ON s.rsession_id = f.rsession_id "
        "WHERE f.file_type = ? AND f.analysis_status IN ('pending', 'failed') "
        "AND f.fail_count < ? "
        # 退避：failed 按档取阈值（fail_count 1→60s / 2→300s；>=3 已排除）
        "AND (f.analysis_status != 'failed' "
        "     OR (f.analyzed_at IS NOT NULL AND f.analyzed_at <= ("
        "         CASE f.fail_count WHEN 1 THEN ? WHEN 2 THEN ? END))) "
        + ("AND f.rsession_id = ? " if session_id else "")
        + "ORDER BY f.uuid_ts_ms")
    params: list = [file_type, MAX_FAILURES,
                    _shift_iso(now, -BACKOFF_DELAYS[0]),
                    _shift_iso(now, -BACKOFF_DELAYS[1])]
    if session_id:
        params.append(session_id)
    if limit and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _shift_iso(iso: str, delta_s: float) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=delta_s)).astimezone(timezone.utc) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")


def mark_file_ok(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute(
        "UPDATE files SET analysis_status = 'ok', fail_count = 0, "
        "analyzed_at = ?, error = NULL WHERE file_id = ?",
        (now_iso(), file_id))


def mark_file_failed(conn: sqlite3.Connection, file_id: int, error: str) -> None:
    conn.execute(
        "UPDATE files SET analysis_status = 'failed', fail_count = fail_count + 1, "
        "analyzed_at = ?, error = ? WHERE file_id = ?",
        (now_iso(), error[:500], file_id))


def record_run(conn: sqlite3.Connection, file_id: int, kind: str, model: str,
               status: str, usage: dict | None, latency_ms: int | None,
               error: str | None, started_at: str | None = None) -> None:
    """§5.3 analysis_runs 审计（一次调用一组文件共用 usage/latency）。

    started_at 缺省用 now（调用方无真实起始时）；finished_at = started_at +
    latency_ms（latency_ms 携带真实耗时，审计精度）。
    """
    started = started_at or now_iso()
    finished = _shift_iso(started, (latency_ms or 0) / 1000) \
        if latency_ms else started
    conn.execute(
        "INSERT INTO analysis_runs (file_id, kind, model, status, "
        "usage_input_tokens, usage_output_tokens, latency_ms, error, "
        "started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (file_id, kind, model, status,
         (usage or {}).get("input_tokens"),
         (usage or {}).get("output_tokens"), latency_ms, error,
         started, finished))


def upsert_event(conn: sqlite3.Connection, event_ref: str, device_uuid: str,
                 rsession_id: str, etype: str, ts_ms: int, source: str,
                 summary: str, payload: dict,
                 ts_utc: str | None = None) -> None:
    """§5.1 事件 upsert（重分析 = 刷新内容 + updated_at）。

    ts_utc：事件 UTC 时刻（§12 输出用表内存储值）；audio/video 传
    files.uuid_ts_utc，无来源（agent_job）可 NULL。
    """
    conn.execute(
        """INSERT INTO events (event_ref, device_uuid, rsession_id, type,
           ts_ms, ts_utc, source, summary, payload, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(event_ref) DO UPDATE SET
           ts_ms = excluded.ts_ms, ts_utc = excluded.ts_utc,
           summary = excluded.summary, payload = excluded.payload,
           updated_at = excluded.updated_at""",
        (event_ref, device_uuid, rsession_id, etype, ts_ms, ts_utc, source,
         summary, json.dumps(payload, ensure_ascii=False), now_iso()))


def upsert_event_if_changed(conn: sqlite3.Connection, event_ref: str,
                            device_uuid: str, rsession_id: str, etype: str,
                            ts_ms: int, source: str, summary: str,
                            payload: dict,
                            ts_utc: str | None = None) -> None:
    """内容比对 upsert（jsonl 重放/重读专用）。

    与 upsert_event 相同，但 `WHERE excluded.summary != events.summary`——
    重放未变化行不刷 updated_at（day_summary 水位纪律：重分析=内容变化才刷；
    否则 chatlog 每轮重读都会把 day_summary 判成有新事件反复重生成）。
    """
    conn.execute(
        """INSERT INTO events (event_ref, device_uuid, rsession_id, type,
           ts_ms, ts_utc, source, summary, payload, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(event_ref) DO UPDATE SET
           ts_ms = excluded.ts_ms, ts_utc = excluded.ts_utc,
           summary = excluded.summary, payload = excluded.payload,
           updated_at = excluded.updated_at
           WHERE excluded.summary != events.summary""",
        (event_ref, device_uuid, rsession_id, etype, ts_ms, ts_utc, source,
         summary, json.dumps(payload, ensure_ascii=False), now_iso()))
