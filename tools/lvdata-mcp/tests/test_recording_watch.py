"""recording_watch 单测（recording_data_process.md §2.2）：cs 行 → lvdata golden 映射。

cs 侧 schema 在本文件内复制一份——同时充当两服务的**契约测试**：
服务端 recording_files DDL 变更时此文件必须同步。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.ddl import ensure_schema
from lvdata_mcp.recording_watch import RecordingWatch

DEV = "b53985c7-0000-4000-8000-000000000001"
RS = "019fabe0-3b0f-7947-8000-1e567def787d"
TS = 1784876900000  # base ts_ms
T1 = "2026-09-15T10:00:00.000Z"
T2 = "2026-09-15T10:05:00.000Z"
T3 = "2026-09-15T10:10:00.000Z"

_CS_SCHEMA = """
CREATE TABLE IF NOT EXISTS recording_files (
    file_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_uuid          TEXT NOT NULL,
    recording_session_id TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    rel_path             TEXT NOT NULL UNIQUE,
    size_bytes           INTEGER NOT NULL,
    ts_ms                INTEGER,
    duration_ms          INTEGER,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
"""


def _cfg(root: Path) -> LvdataMcpConfig:
    return LvdataMcpConfig(data={"root_dir": str(root)},
                           watcher={"timezone": "Asia/Shanghai"})


def _setup(tmp_path: Path, *, write_session_json: bool = True):
    db = tmp_path / "lvdata_inventory.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn, default_timezone="Asia/Shanghai",
                  timezones=["Asia/Shanghai"])
    conn.commit()
    conn.close()

    cs = tmp_path / "chat_server.db"
    conn = sqlite3.connect(cs)
    conn.executescript(_CS_SCHEMA)
    conn.commit()
    conn.close()

    sess = tmp_path / DEV / RS
    (sess / "chat").mkdir(parents=True)
    (sess / "audio").mkdir()
    (sess / "video").mkdir()
    if write_session_json:
        # 静态 7 键格式（session_json_refactor §1.1）：无 timezone/status/
        # last_active_at/finalized_at
        (sess / "session.json").write_text(json.dumps({
            "schema_version": 1, "recording_session_id": RS,
            "device_uuid": DEV, "user_uuid": None,
            "session_date": "2026-09-15",
            "started_at": "2026-09-15T09:00:00.000Z",
            "config": {"audio_codec": "opus", "audio_container": "ogg",
                       "audio_segment_s": 60, "video_format": "jpeg-sequence",
                       "image_enabled": False, "ai_audio_recorded": False},
        }, ensure_ascii=False))
    for name in ("history.jsonl", "transcribe.jsonl", "translate.jsonl"):
        (sess / "chat" / name).write_text("")
    return tmp_path, cs, db


def _add(cs: Path, kind: str, rel: str, size: int, *, ts_ms=None,
         updated_at: str = T1) -> None:
    with sqlite3.connect(cs) as conn:
        conn.execute(
            "INSERT INTO recording_files (device_uuid, recording_session_id, "
            "kind, rel_path, size_bytes, ts_ms, duration_ms, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(rel_path) DO UPDATE SET size_bytes = excluded.size_bytes, "
            "updated_at = excluded.updated_at, ts_ms = excluded.ts_ms",
            (DEV, RS, kind, rel, size, ts_ms, None, T1, updated_at))


def _add_all(cs: Path) -> None:
    _add(cs, "session", f"{DEV}/{RS}/session.json", 400)
    _add(cs, "audio", f"{DEV}/{RS}/audio/seg1.ogg", 2000, ts_ms=TS + 5000)
    _add(cs, "video", f"{DEV}/{RS}/video/f1.jpg", 300, ts_ms=TS + 10000)
    _add(cs, "chat_history", f"{DEV}/{RS}/chat/history.jsonl", 100)
    _add(cs, "chat_transcribe", f"{DEV}/{RS}/chat/transcribe.jsonl", 50)
    _add(cs, "chat_translate", f"{DEV}/{RS}/chat/translate.jsonl", 60)


def _files(db: Path) -> dict[str, sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = {r["file_type"]: r for r in conn.execute("SELECT * FROM files")}
    conn.close()
    return rows


def _session(db: Path) -> sqlite3.Row:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM recording_sessions WHERE rsession_id=?",
                       (RS,)).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------


def test_golden_mapping(tmp_path):
    root, cs, db = _setup(tmp_path)
    _add_all(cs)
    stats = RecordingWatch(_cfg(root)).check()
    assert stats == {"files": 6, "sessions": 1,
                     "chat_files": 3, "skipped_kinds": 0}

    files = _files(db)
    assert set(files) == {"meta", "audio", "video", "chat_history",
                          "chat_transcribe", "chat_translate"}
    audio = files["audio"]
    assert audio["analysis_status"] == "pending"          # 新行初始状态
    assert audio["uuid_ts_ms"] == TS + 5000               # uuid7 ts 透传
    assert audio["src_updated_at"] == T1
    assert audio["file_path"] == f"{root}/{DEV}/{RS}/audio/seg1.ogg"
    assert audio["processed_offset"] == 0
    for chat_type in ("chat_history", "chat_transcribe", "chat_translate"):
        row = files[chat_type]
        assert row["analysis_status"] == "na"             # chat 类恒 'na'
        assert row["uuid_ts_ms"] is None                  # 可分析产物时间戳语义（§7）
    assert files["meta"]["analysis_status"] == "na"

    sess = _session(db)
    assert sess["status"] == ""              # 无 status 字段 → `.get` 兜底空串
    assert sess["timezone"] == "UTC"         # 无 timezone 字段 → 兜底 UTC
    assert sess["started_at"] == "2026-09-15T09:00:00.000Z"  # 规范格式透传
    assert sess["audio_segment_s"] == 60


def test_legacy_resync_ingests_fields_without_consumption(tmp_path):
    """旧格式文件重同步（§3.B.9 事实①）：4 动态字段照常摄入（历史会话保留
    原 containment 上界）但不消费——无 day_force 表、sessions 计数照常；
    started_at 经 normalize_ts 收敛为 §5.1 规范格式。"""
    root, cs, db = _setup(tmp_path)
    _add_all(cs)
    w = RecordingWatch(_cfg(root))
    assert w.check()["sessions"] == 1

    # 旧服务端格式（微秒 + +00:00，含 4 动态字段）：cs updated_at 被 bump 后重放
    (Path(root) / DEV / RS / "session.json").write_text(json.dumps({
        "schema_version": 1, "recording_session_id": RS,
        "device_uuid": DEV, "user_uuid": None,
        "session_date": "2026-09-15",
        "started_at": "2026-09-15T09:00:00.006182+00:00",
        "timezone": "Asia/Shanghai", "status": "finalized",
        "last_active_at": "2026-09-15T10:00:00+00:00",
        "finalized_at": "2026-09-15T10:30:00+00:00",
        "config": {},
    }, ensure_ascii=False))
    _add(cs, "session", f"{DEV}/{RS}/session.json", 500, updated_at=T2)

    stats = w.check()
    assert stats == {"files": 1, "sessions": 1, "chat_files": 0,
                     "skipped_kinds": 0}
    sess = _session(db)
    assert sess["status"] == "finalized"                       # 摄入
    assert sess["timezone"] == "Asia/Shanghai"                 # 摄入
    assert sess["finalized_at"] == "2026-09-15T10:30:00+00:00"  # 摄入
    assert sess["started_at"] == "2026-09-15T09:00:00.006Z"    # normalize_ts
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "day_force" not in tables                           # 不消费


def test_incremental_watermark_and_status_preserved(tmp_path):
    root, cs, db = _setup(tmp_path)
    _add_all(cs)
    w = RecordingWatch(_cfg(root))
    assert w.check()["files"] == 6

    # 第二轮无新行 → 水位生效
    assert w.check()["files"] == 0

    # 分析进度与游标不允许被重扫覆盖
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE files SET analysis_status='ok', fail_count=0, "
                     "processed_updated_at=? WHERE file_type='audio'", (T1,))

    _add(cs, "audio", f"{DEV}/{RS}/audio/seg1.ogg", 3000, ts_ms=TS + 5000,
         updated_at=T3)
    stats = w.check()
    assert stats["files"] == 1  # 只取到变更行
    audio = _files(db)["audio"]
    assert audio["file_size"] == 3000
    assert audio["src_updated_at"] == T3
    assert audio["analysis_status"] == "ok"          # 不被覆盖
    assert audio["processed_updated_at"] == T1       # 游标不被清


def test_session_row_bootstrap_without_session_json(tmp_path):
    """仅 cs 文件行先到（session.json 尚未落盘 / 已被删）也不断链。"""
    root, cs, db = _setup(tmp_path, write_session_json=False)
    _add(cs, "audio", f"{DEV}/{RS}/audio/seg1.ogg", 2000, ts_ms=TS + 5000)
    stats = RecordingWatch(_cfg(root)).check()
    assert stats["files"] == 1
    assert _files(db)["audio"]["analysis_status"] == "pending"
    sess = _session(db)
    assert sess is not None and sess["status"] == "active"


def test_missing_cs_db_is_graceful(tmp_path):
    root, cs, _db = _setup(tmp_path)
    cs.unlink()
    stats = RecordingWatch(_cfg(root)).check()
    assert stats["files"] == 0


def test_health_reports_backlog_and_lag(tmp_path):
    root, cs, db = _setup(tmp_path)
    _add_all(cs)
    w = RecordingWatch(_cfg(root))
    w.check()
    health = w.health()
    assert health["chat_backlog"] == 3          # 三个 chat jsonl 待处理
    assert health["sync_lag_s"] is not None     # 有未处理文件 → 有滞后

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE files SET processed_updated_at=src_updated_at, "
                     "analysis_status='ok' WHERE file_type IN "
                     "('chat_history','chat_transcribe','chat_translate','audio','video')")
    health = w.health()
    assert health["chat_backlog"] == 0
