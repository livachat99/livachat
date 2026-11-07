"""recording_files 同步（recording_data_process.md §2.2；cs 单链路，无 fs 扫盘）。

ATTACH chat_server.db 只读，keyset `(updated_at, file_id)` 拉 cs.recording_files
增量，写全部留在 lvdata 库单事务（语义与已删除的 jobs_watch 同款）：

1. files 行 upsert：新行初始状态 audio/video→`pending`、chat/meta→`na`；
   存量行（ON CONFLICT(file_path)）只更新 file_size/scanned_at/src_updated_at，
   **不覆盖** analysis_status/fail_count/error/processed_offset/processed_updated_at
   （重扫不重置分析进度与游标）；
2. chat 类文件不写 `uuid_ts_ms`（保持 NULL）：该列语义 = 可分析产物时间戳，
   chat/meta 无分析产物（analysis_status 恒 'na'）；历史上 bff 的按日 pending
   统计（已随 docs/bff_service_interfaces.md §7 删除）依赖此约束，现由
   「state = status 口径」承接——**勿改为填值**（会污染按日语义）；
3. kind=session（session.json 落盘）行即时处理：读盘 → recording_sessions
   upsert（映射自 v1 scanner.py，已迁移）；session.json 静态化
   （session_json_refactor）后无 finalize 信号与 day_force 写入；
4. session 行存在性兜底：files.rsession_id 有 FK 且下游 JOIN 依赖（分析取批/
   展示），缺行先 INSERT OR IGNORE 最小行；
5. 单事务提交：以上全部 + 水位推进（recording_files_sync_state）；崩溃整体
   回滚，下轮按旧水位重来（upsert 幂等）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog

from .config import LvdataMcpConfig
from .ddl import normalize_ts
from .timeutil import ms_to_iso, now_iso

log = structlog.get_logger("lvdata_mcp.recording_watch")

BATCH_SIZE = 5000

# cs kind → lvdata file_type（session.json → meta；三个 chat jsonl 各自独立类型）
FILE_TYPE_BY_KIND = {
    "session": "meta",
    "chat_history": "chat_history",
    "chat_transcribe": "chat_transcribe",
    "chat_translate": "chat_translate",
    "audio": "audio",
    "video": "video",
}
CHAT_KINDS = ("chat_history", "chat_transcribe", "chat_translate")

# 映射自 v1 scanner.py（scanner 已随 cs 单链路删除）
SESSION_COLUMNS = [
    "rsession_id", "device_uuid", "user_uuid", "session_date",
    "timezone", "status", "started_at", "last_active_at",
    "finalized_at", "duration_ms", "analysis_status", "analyzed_at",
    "root_path", "audio_codec", "audio_container", "audio_segment_s",
    "video_format", "image_enabled", "ai_audio_recorded",
    "cnt_audio_segments", "cnt_audio_frames", "cnt_video_frames",
    "cnt_image_count", "cnt_chat_messages", "cnt_total_bytes",
    "scanned_at", "scan_version",
]

_UPSERT_FILES_SQL = """
INSERT INTO files (rsession_id, device_uuid, file_type, file_ext, file_path,
                   file_size, uuid_ts_ms, uuid_ts_utc, scanned_at,
                   analysis_status, src_updated_at, processed_offset)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
ON CONFLICT(file_path) DO UPDATE SET
    file_size = excluded.file_size,
    scanned_at = excluded.scanned_at,
    src_updated_at = excluded.src_updated_at,
    device_uuid = COALESCE(files.device_uuid, excluded.device_uuid)
"""

_ENSURE_SESSION_SQL = """
INSERT OR IGNORE INTO recording_sessions
    (rsession_id, device_uuid, session_date, status, root_path)
VALUES (?, ?, '', 'active', ?)
"""

_WATERMARK_SQL = """
INSERT INTO recording_files_sync_state
    (id, last_updated_at, last_file_id, last_run_at, rows_upserted)
VALUES (1, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    last_updated_at = excluded.last_updated_at,
    last_file_id = excluded.last_file_id,
    last_run_at = excluded.last_run_at,
    rows_upserted = excluded.rows_upserted
"""


def _normalize_uuid(raw: str) -> str:
    return str(uuid.UUID(raw))


class RecordingWatch:
    def __init__(self, cfg: LvdataMcpConfig):
        self.cfg = cfg
        self.db_path = cfg.data.resolved_db_path
        self.cs_db_path = str(cfg.data.resolved_chat_server_db_path)
        self.root = Path(cfg.data.root_dir)

    def _conn(self) -> sqlite3.Connection:
        # uri=True：ATTACH 'file:...?mode=ro' 按 URI 处理（§3.8.1 只读打开约束）；
        # foreign_keys=ON：files.rsession_id FK 生效（兜底行必需）
        conn = sqlite3.connect(self.db_path, timeout=10, uri=True)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,)).fetchone() is not None

    def _attach_cs(self, conn: sqlite3.Connection) -> bool:
        """ATTACH chat_server.db 只读；缺失/失败 → False（优雅空转，非错误）。"""
        if not os.path.isfile(self.cs_db_path):
            log.warning("chat server db not found, recording_files scan skipped",
                        path=self.cs_db_path)
            return False
        try:
            escaped = self.cs_db_path.replace("'", "''")
            conn.execute(f"ATTACH DATABASE 'file:{escaped}?mode=ro' AS cs")
            return True
        except sqlite3.OperationalError:
            log.warning("failed to attach chat server db (read-only WAL)",
                        path=self.cs_db_path, exc_info=True)
            return False

    def _cs_table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM cs.sqlite_master WHERE type='table' AND name = ?",
            (name,)).fetchone() is not None

    # ── 增量消费 ──────────────────────────────────────────────────────────

    def check(self, force: bool = False) -> dict:
        """消费 cs.recording_files 增量；返回本轮变更简报（统计用）。"""
        stats = {"files": 0, "sessions": 0, "chat_files": 0, "skipped_kinds": 0}
        with self._conn() as conn:
            if not self._table_exists(conn, "recording_files_sync_state"):
                return stats
            if not self._attach_cs(conn):
                return stats
            if not self._cs_table_exists(conn, "recording_files"):
                return stats
            # 显式 BEGIN：分页读与写入同一快照（ATTACH 必须在事务前）
            conn.execute("BEGIN")
            wm = conn.execute(
                "SELECT last_updated_at, last_file_id FROM recording_files_sync_state "
                "WHERE id = 1").fetchone()
            wm_ts = (wm[0] or "") if wm else ""
            wm_id = int(wm[1] or 0) if wm else 0
            if force:
                wm_ts, wm_id = "", 0

            changed = 0
            while True:
                page = conn.execute(
                    "SELECT * FROM cs.recording_files "
                    "WHERE (updated_at, file_id) > (?, ?) "
                    "ORDER BY updated_at, file_id LIMIT ?",
                    (wm_ts, wm_id, BATCH_SIZE)).fetchall()
                if not page:
                    break
                for row in page:
                    self._apply_file_row(conn, row, stats)
                last = page[-1]
                wm_ts, wm_id = last["updated_at"], last["file_id"]
                changed += len(page)

            if changed:
                conn.execute(_WATERMARK_SQL,
                             (wm_ts, wm_id, now_iso(), changed))
        if changed:
            log.info("recording files synced", rows=changed, **stats)
        else:
            log.debug("recording files idle")
        return stats

    # ── 单行处理 ──────────────────────────────────────────────────────────

    def _apply_file_row(self, conn: sqlite3.Connection, row: sqlite3.Row,
                        stats: dict) -> None:
        kind = row["kind"]
        file_type = FILE_TYPE_BY_KIND.get(kind)
        if file_type is None:
            stats["skipped_kinds"] += 1
            log.warning("unknown recording file kind, skipped", kind=kind,
                        rel_path=row["rel_path"])
            return
        device_uuid = row["device_uuid"]
        rsession_id = row["recording_session_id"]
        rel_path = row["rel_path"]
        file_path = str(self.root / rel_path)

        # session 行兜底（FK + 下游 JOIN；行先到也保证可进入处理）
        parts = Path(rel_path).parts
        root_dir = str(self.root / parts[0] / parts[1]) if len(parts) >= 2 else str(self.root)
        conn.execute(_ENSURE_SESSION_SQL, (rsession_id, device_uuid, root_dir))

        if kind == "session":
            self._apply_session_json(conn, file_path, rsession_id, device_uuid,
                                     stats)

        is_chat = kind in CHAT_KINDS
        status = "na" if (is_chat or kind == "session") else "pending"
        # chat/meta 不写 uuid_ts_ms（NULL）：可分析产物时间戳语义（见文件头注 2）
        ts_ms = None if (is_chat or kind == "session") else row["ts_ms"]
        ts_utc = ms_to_iso(ts_ms) if ts_ms else None
        ext = Path(rel_path).suffix.lstrip(".")
        conn.execute(_UPSERT_FILES_SQL, (
            rsession_id, device_uuid, file_type, ext, file_path,
            row["size_bytes"], ts_ms, ts_utc, now_iso(), status,
            row["updated_at"],
        ))
        stats["files"] += 1
        if is_chat:
            stats["chat_files"] += 1

    def _apply_session_json(self, conn: sqlite3.Connection, file_path: str,
                            rsession_id: str, device_uuid: str,
                            stats: dict) -> None:
        """session.json 即时处理：读盘 → recording_sessions upsert。

        静态化（session_json_refactor §3.B）后无 finalize 转换与 day_force 写入。
        """
        try:
            meta = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("session.json 读取失败，本轮跳过",
                        session_id=rsession_id, error=str(e)[:200])
            return
        self._upsert_session(conn, meta, rsession_id, device_uuid,
                             Path(file_path).parent)
        stats["sessions"] += 1

    # ── session.json 映射（迁移自 v1 scanner.py）─────────────────────────

    def _upsert_session(self, conn: sqlite3.Connection, meta: dict,
                        rsession_id: str, device_uuid: str,
                        sess_path: Path) -> None:
        """session.json → recording_sessions 行（映射自 v1 scanner.py）。

        静态化后新文件已无 timezone/status/last_active_at/finalized_at
        （session_json_refactor §1.1），但 `.get` 摄入**保留**——存量旧文件
        `--force` 重同步仍带这些字段，摄入但不消费（无 finalize 转换、
        无 day_force；历史会话保留原 containment 上界，§3.B.9 良性不对称）。
        """
        config = meta.get("config", {})
        stats = meta.get("stats", {})
        raw_user = meta.get("user_uuid")
        try:
            user_uuid = _normalize_uuid(raw_user) if raw_user else None
        except (ValueError, TypeError):
            user_uuid = raw_user
        data = {
            "rsession_id": rsession_id,
            "device_uuid": device_uuid,
            "user_uuid": user_uuid,
            "session_date": meta.get("session_date", ""),
            "timezone": meta.get("timezone", "UTC"),
            "status": meta.get("status", ""),
            "started_at": normalize_ts(meta.get("started_at")),
            "last_active_at": meta.get("last_active_at"),
            "finalized_at": meta.get("finalized_at"),
            "duration_ms": None,  # 计算逻辑已删：全仓无读取方（§3.B.9 事实②）
            "analysis_status": meta.get("analysis_status", "pending"),
            "analyzed_at": meta.get("analyzed_at"),
            "root_path": str(sess_path),
            "audio_codec": config.get("audio_codec"),
            "audio_container": config.get("audio_container"),
            "audio_segment_s": config.get("audio_segment_s"),
            "video_format": config.get("video_format"),
            "image_enabled": int(bool(config.get("image_enabled"))),
            "ai_audio_recorded": int(bool(config.get("ai_audio_recorded"))),
            "cnt_audio_segments": stats.get("audio_segments", 0),
            "cnt_audio_frames": stats.get("audio_frames", 0),
            "cnt_video_frames": stats.get("video_frames", 0),
            "cnt_image_count": stats.get("image_count", 0),
            "cnt_chat_messages": stats.get("chat_messages", 0),
            "cnt_total_bytes": stats.get("total_bytes", 0),
            "scanned_at": now_iso(),
            "scan_version": 1,
        }
        placeholders = ", ".join("?" for _ in SESSION_COLUMNS)
        cols = ", ".join(SESSION_COLUMNS)
        conn.execute(
            f"INSERT OR REPLACE INTO recording_sessions ({cols}) "
            f"VALUES ({placeholders})",
            [data[c] for c in SESSION_COLUMNS])

    # ── 健康口径（get_status 用）──────────────────────────────────────────

    def health(self) -> dict:
        """sync lag（未处理文件 src_updated_at 距 now）+ chat 滞留行数。"""
        out: dict = {"sync_lag_s": None, "chat_backlog": 0}
        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True,
                                 timeout=5) as conn:
                row = conn.execute(
                    "SELECT MAX(src_updated_at) AS m FROM files WHERE "
                    "(file_type IN ('audio','video') AND "
                    " analysis_status IN ('pending','failed') AND fail_count < 3) "
                    "OR (file_type IN ('chat_history','chat_transcribe',"
                    "'chat_translate') AND fail_count < 3 AND "
                    "(processed_updated_at IS NULL OR src_updated_at > processed_updated_at))"
                ).fetchone()
                if row and row[0]:
                    dt = datetime.fromisoformat(row[0])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    out["sync_lag_s"] = max(
                        0.0, (datetime.now(tz=UTC) - dt).total_seconds())
                out["chat_backlog"] = conn.execute(
                    "SELECT COUNT(*) FROM files WHERE file_type IN "
                    "('chat_history','chat_transcribe','chat_translate') "
                    "AND fail_count < 3 AND (processed_updated_at IS NULL "
                    "OR src_updated_at > processed_updated_at)").fetchone()[0]
        except sqlite3.Error:
            pass
        return out
