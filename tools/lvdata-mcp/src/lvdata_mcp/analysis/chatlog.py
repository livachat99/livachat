"""chatlog pass（recording_data_process.md §2.3/§2.5）：chat 三 jsonl → events。

- 取件（§2.2 规则）：chat_history/chat_transcribe/chat_translate、fail_count<3、
  processed_updated_at 落后（NULL 或 < src_updated_at）；
- 从 processed_offset 读**增量字节**，只处理完整行：尾部半行不推进（下轮续读）；
  单行超上限（无换行的满块）跳过并告警，防坏数据卡死；
- 事件映射：chat / agent_job（history 的 source=agent_job 行，event_ref 用
  job_key）/ transcribe / translate；ref_id 入口清洗（含 ':' → '_'，空 → 确定性
  兜底），保证 event_ref 三段可查询；
- 幂等：内容比对 upsert（summary 变化才刷 updated_at，day_summary 水位纪律）；
- chat_messages 表已删除（2026-09-15 修订裁定 G：无读写方；原文归档 =
  history.jsonl 本身）；
- 状态列：成功回 'na'（保持 'na' 不变量，不写 'ok'）；失败 fail_count+1 由
  取件阈值停止；processed_updated_at 仅在**读到文件末尾**时推进，否则下轮续读。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import structlog

from ..config import LvdataMcpConfig
from .common import mark_file_failed, upsert_event_if_changed

log = structlog.get_logger("lvdata_mcp.chatlog")

CHAT_FILE_TYPES = ("chat_history", "chat_transcribe", "chat_translate")
MAX_FAILURES = 3

# 单行硬上限：cap 内无行界时继续读到行界，超过此值判坏行跳过（防坏数据卡死）
_MAX_LINE_BYTES = 8 * 1024 * 1024

_SELECT_SQL = """
SELECT f.* FROM files f
WHERE f.file_type IN ('chat_history', 'chat_transcribe', 'chat_translate')
  AND f.fail_count < ?
  AND (f.processed_updated_at IS NULL OR f.src_updated_at > f.processed_updated_at)
"""


def sanitize_ref_id(ref_id: str | None, *, kind: str, ts_ms: int,
                    role: str) -> str:
    """ref_id 入口清洗（§2.5）：含 ':' → '_'；空 → 确定性兜底
    `{kind}_{ts_ms}_{role}`（一行一事件，重放稳定）。"""
    cleaned = (ref_id or "").replace(":", "_").strip()
    if cleaned:
        return cleaned
    return f"{kind}_{ts_ms}_{role or 'unknown'}"


def line_to_event(file_type: str, rsession_id: str, msg: dict) -> dict | None:
    """jsonl 行 → 事件字段（§2.5 映射表）；无法映射 → None。

    三 jsonl 行 schema 共用（chatlog.py:45-58）：ts/utc/role/text/ref_id/
    chat_session_id + 可选 source/images/interrupted/agent_name/agent_id/job_key。
    """
    text = msg.get("text", "")
    ts_ms = msg.get("ts") or 0
    ts_utc = msg.get("utc")
    role = msg.get("role", "unknown")
    raw_source = msg.get("source")
    chat_session_id = msg.get("chat_session_id", "")

    if file_type == "chat_history":
        if raw_source == "agent_job":
            job_key = msg.get("job_key")
            ref = sanitize_ref_id(job_key or msg.get("ref_id"), kind="agent_job",
                                  ts_ms=ts_ms, role=role)
            return {
                "event_ref": f"{rsession_id}:agent_job:{ref}",
                "type": "agent_job", "source": "chat_messages",
                "ts_ms": ts_ms, "ts_utc": ts_utc, "summary": text,
                "payload": {
                    "job_key": job_key or "",
                    "agent_name": msg.get("agent_name", ""),
                    "agent_id": msg.get("agent_id", ""),
                    "role": role, "text": text,
                },
            }
        ref = sanitize_ref_id(msg.get("ref_id"), kind="chat", ts_ms=ts_ms,
                              role=role)
        return {
            "event_ref": f"{rsession_id}:chat:{ref}",
            "type": "chat", "source": "chat_messages",
            "ts_ms": ts_ms, "ts_utc": ts_utc, "summary": text,
            "payload": {"role": role, "text": text,
                        "chat_session_id": chat_session_id},
        }

    if file_type == "chat_transcribe":
        ref = sanitize_ref_id(msg.get("ref_id"), kind="transcribe",
                              ts_ms=ts_ms, role=role)
        return {
            "event_ref": f"{rsession_id}:transcribe:{ref}",
            "type": "transcribe", "source": "chat_transcribe",
            "ts_ms": ts_ms, "ts_utc": ts_utc, "summary": text,
            "payload": {"role": role, "text": text,
                        "chat_session_id": chat_session_id},
        }

    if file_type == "chat_translate":
        ref = sanitize_ref_id(msg.get("ref_id"), kind="translate",
                              ts_ms=ts_ms, role=role)
        return {
            "event_ref": f"{rsession_id}:translate:{ref}",
            "type": "translate", "source": "chat_translate",
            "ts_ms": ts_ms, "ts_utc": ts_utc, "summary": text,
            # 行级 source 原值（"translate" | "lecture"）入 payload，后续拆型不返工
            "payload": {"role": role, "text": text,
                        "chat_session_id": chat_session_id,
                        "source": raw_source or ""},
        }

    return None


class ChatlogProcessor:
    def __init__(self, cfg: LvdataMcpConfig):
        self.cfg = cfg
        self.db_path = cfg.data.resolved_db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    # ── 主入口（同步；pipeline 经 to_thread 调用）────────────────────────

    def process(self, session_id: str | None = None, force: bool = False) -> dict:
        stats = {"processed": 0, "events": 0, "failed": 0, "bad_lines": 0,
                 "partial": 0}
        with self._conn() as conn:
            if force:
                self._reset(conn, session_id)
                conn.commit()
            sql = _SELECT_SQL
            params: list = [MAX_FAILURES]
            if session_id:
                sql += " AND f.rsession_id = ?"
                params.append(session_id)
            sql += " ORDER BY f.file_id"
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        for row in rows:
            with self._conn() as conn:
                res = self._process_file(conn, row)
                conn.commit()
            stats["processed"] += 1
            stats["events"] += res.get("events", 0)
            stats["bad_lines"] += res.get("bad_lines", 0)
            if res.get("failed"):
                stats["failed"] += 1
            if res.get("partial"):
                stats["partial"] += 1
        if rows:
            log.info("chatlog pass done", session_id=session_id, force=force,
                     **stats)
        return stats

    def _reset(self, conn: sqlite3.Connection, session_id: str | None) -> None:
        """force：整文件重放（offset/水位/fail_count 全重置）。"""
        sql = ("UPDATE files SET processed_offset = 0, processed_updated_at = NULL, "
               "fail_count = 0, analysis_status = 'na', error = NULL "
               "WHERE file_type IN ('chat_history', 'chat_transcribe', "
               "'chat_translate')")
        params: list = []
        if session_id:
            sql += " AND rsession_id = ?"
            params.append(session_id)
        conn.execute(sql, params)

    def _process_file(self, conn: sqlite3.Connection, row: dict) -> dict:
        path = Path(row["file_path"])
        offset = row["processed_offset"] or 0
        try:
            size = path.stat().st_size
        except OSError as e:
            return self._fail(conn, row, f"stat: {e}")
        if size < offset:
            # 文件变小（重写）：offset 越界 → 重置全量重读（幂等）
            offset = 0
        cap = self.cfg.watcher.chatlog_max_bytes_per_round
        hard_hit = False
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read(cap if cap and cap > 0 else -1)
                if chunk and b"\n" not in chunk:
                    # cap 内无完整行：单行可能长于 cap，继续读到行界；
                    # 硬上限内仍无行界 → 真·坏行（下面跳过）
                    extra = fh.read(_MAX_LINE_BYTES)
                    idx = extra.find(b"\n")
                    if idx >= 0:
                        chunk += extra[: idx + 1]
                    else:
                        chunk += extra
                        hard_hit = len(extra) >= _MAX_LINE_BYTES
        except OSError as e:
            return self._fail(conn, row, f"read: {e}")
        if not chunk:
            self._advance(conn, row, offset, fully=True)
            return {"events": 0}

        nl = chunk.rfind(b"\n")
        if nl < 0:
            if hard_hit:
                # 单行超硬上限：跳过整块（防坏数据卡死），下轮续读
                log.warning("chatlog oversized line skipped",
                            file=row["file_path"], bytes=len(chunk))
                self._advance(conn, row, offset + len(chunk), fully=False)
                return {"events": 0, "oversized": 1}
            # 尾部半行（崩溃截断）：不推进，等下轮补齐
            return {"events": 0, "partial": 1}

        body = chunk[: nl + 1]
        new_offset = offset + len(body)
        events = 0
        bad = 0
        for raw in body.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                bad += 1
                log.warning("invalid JSON line in chatlog; skipped", file=path.name,
                            session_id=row["rsession_id"])
                continue
            ev = line_to_event(row["file_type"], row["rsession_id"], msg)
            if ev is None:
                continue
            upsert_event_if_changed(
                conn, ev["event_ref"], row["device_uuid"], row["rsession_id"],
                ev["type"], ev["ts_ms"], ev["source"], ev["summary"],
                ev["payload"], ts_utc=ev["ts_utc"])
            events += 1
        self._advance(conn, row, new_offset, fully=new_offset >= size)
        return {"events": events, "bad_lines": bad,
                "partial": 0 if new_offset >= size else 1}

    def _advance(self, conn: sqlite3.Connection, row: dict, new_offset: int,
                 *, fully: bool) -> None:
        """推进 offset；仅在读到文件末尾时推进 processed_updated_at。

        未读完（cap 截断/超长行跳过）时水位留在原位 → 下一轮继续取件续读。
        """
        if fully:
            conn.execute(
                "UPDATE files SET processed_offset = ?, processed_updated_at = ?, "
                "analysis_status = 'na', error = NULL WHERE file_id = ?",
                (new_offset, row["src_updated_at"], row["file_id"]))
        else:
            conn.execute(
                "UPDATE files SET processed_offset = ?, analysis_status = 'na', "
                "error = NULL WHERE file_id = ?",
                (new_offset, row["file_id"]))

    def _fail(self, conn: sqlite3.Connection, row: dict, error: str) -> dict:
        mark_file_failed(conn, row["file_id"], error)
        log.warning("chatlog file failed", file=row["file_path"],
                    error=error[:200])
        return {"failed": 1}
