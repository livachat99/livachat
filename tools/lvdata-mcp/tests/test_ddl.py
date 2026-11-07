"""P0：v2 DDL（§5）+ §7 幂等改名迁移 + agent_jobs 时间戳归一化。"""

from __future__ import annotations

import sqlite3

import pytest

from lvdata_mcp.ddl import (
    ensure_schema,
    migrate,
    normalize_ts,
    TS_RE,
)


def _v1_db(path, with_jobs: bool = True) -> sqlite3.Connection:
    """构造 v1 存量库（sessions/files/chat_messages/agent_jobs 旧名）。"""
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY, device_uuid TEXT NOT NULL,
        session_date TEXT NOT NULL, status TEXT NOT NULL, root_path TEXT NOT NULL,
        timezone TEXT DEFAULT 'UTC', started_at TEXT, last_active_at TEXT,
        finalized_at TEXT, duration_ms INTEGER, analysis_status TEXT DEFAULT 'pending',
        analyzed_at TEXT, scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
        scan_version INTEGER DEFAULT 1
    );
    CREATE TABLE files (
        file_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        file_type TEXT NOT NULL, file_ext TEXT NOT NULL,
        file_path TEXT NOT NULL UNIQUE, file_size INTEGER NOT NULL,
        uuid_ts_ms INTEGER, uuid_ts_utc TEXT,
        scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE chat_messages (
        msg_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
        chat_session_id TEXT, ts_ms INTEGER NOT NULL, ts_utc TEXT,
        role TEXT NOT NULL, text_content TEXT NOT NULL, text_length INTEGER,
        scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)
    if with_jobs:
        conn.executescript("""
        CREATE TABLE agent_jobs (
            job_key TEXT PRIMARY KEY, agent_name TEXT NOT NULL DEFAULT 'hermes',
            device_uuid TEXT NOT NULL, session_id TEXT NOT NULL,
            recording_session_id TEXT, hermes_task_id TEXT,
            status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
            result_text TEXT, fail_reason TEXT,
            requested_ts_ms INTEGER NOT NULL, requested_at TEXT NOT NULL,
            started_at TEXT, finished_at TEXT, delivered_at TEXT,
            pg_updated_at TEXT NOT NULL, synced_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE agent_jobs_sync_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_pg_updated_at TEXT, last_job_key TEXT, last_run_at TEXT,
            rows_upserted INTEGER DEFAULT 0, pending_affected_sessions TEXT
        );
        """)
    conn.commit()
    return conn


def test_ensure_schema_creates_v2_tables(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    ensure_schema(conn, "Asia/Shanghai", ["America/New_York"])
    conn.commit()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for t in ("events", "analysis_runs", "day_summary", "timezones",
              "day_scan_state", "day_deferred", "period_summary",
              "flush_state", "recording_sessions", "files", "index_state"):
        assert t in tables, t
    assert "chat_messages" not in tables  # 2026-09-15 修订裁定 G：已删除
    assert "day_force" not in tables      # session_json_refactor 裁决 A：已删除
    # events 索引
    idx = {r[1] for r in conn.execute("PRAGMA index_list(events)").fetchall()}
    assert {"idx_events_rsession_ts", "idx_events_ts", "idx_events_type"} <= idx
    # files 增强列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
    assert {"device_uuid", "analysis_status", "fail_count", "analyzed_at", "error"} <= cols
    # timezones seed：watcher.timezone 兜底 + 显式列表
    tzs = {r[0] for r in conn.execute("SELECT timezone FROM timezones").fetchall()}
    assert tzs == {"Asia/Shanghai", "America/New_York"}
    conn.close()


def test_ensure_schema_day_stats_column_and_state_table(tmp_path):
    """§7.2.1：新库含 day_stats 列 + day_stats_state 表；存量库走幂等 ALTER。"""
    conn = sqlite3.connect(tmp_path / "new.db")
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "day_stats_state" in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(day_summary)").fetchall()}
    assert "day_stats" in cols
    conn.close()

    # 存量库：旧形态 day_summary（无 day_stats）+ 既有行 → ALTER 补列，行取默认值
    legacy = sqlite3.connect(tmp_path / "legacy.db")
    legacy.executescript("""
    CREATE TABLE day_summary (
        timezone        TEXT NOT NULL,
        local_date      TEXT NOT NULL,
        utc_offset_min  INTEGER NOT NULL,
        day_start_ms    INTEGER NOT NULL,
        day_end_ms      INTEGER NOT NULL,
        day_summary     TEXT NOT NULL DEFAULT '{}',
        model           TEXT,
        status          TEXT NOT NULL DEFAULT 'pending',
        event_count     INTEGER NOT NULL DEFAULT 0,
        fail_count      INTEGER NOT NULL DEFAULT 0,
        error           TEXT,
        generated_at    TEXT,
        PRIMARY KEY (timezone, local_date)
    );
    INSERT INTO day_summary (timezone, local_date, utc_offset_min, day_start_ms,
        day_end_ms, day_summary, status, event_count, fail_count)
        VALUES ('Asia/Shanghai', '2026-08-17', 480, 0, 0, '{}', 'pending', 0, 0);
    """)
    for _ in range(2):                      # 幂等：连跑两次不报错、不重复加列
        ensure_schema(legacy, "Asia/Shanghai", [])
        legacy.commit()
    cols = {r[1] for r in legacy.execute("PRAGMA table_info(day_summary)").fetchall()}
    assert "day_stats" in cols
    assert legacy.execute(
        "SELECT day_stats FROM day_summary WHERE local_date = '2026-08-17'"
    ).fetchone()[0] == "{}"
    legacy.close()


def test_ensure_schema_idempotent(tmp_path):
    p = tmp_path / "t.db"
    for _ in range(2):
        conn = sqlite3.connect(p)
        ensure_schema(conn, "Asia/Shanghai", [])
        conn.commit()
        conn.close()
    conn = sqlite3.connect(p)
    n = conn.execute("SELECT COUNT(*) FROM timezones").fetchone()[0]
    assert n == 1  # seed 不重复
    conn.close()


def test_seed_does_not_overwrite_existing(tmp_path):
    conn = _v1_db(tmp_path / "t.db")
    conn.execute(
        "CREATE TABLE timezones (timezone TEXT PRIMARY KEY, enabled INTEGER NOT NULL "
        "DEFAULT 1, priority INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL "
        "DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), updated_at TEXT NOT NULL "
        "DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))")
    conn.execute("INSERT INTO timezones (timezone, priority) VALUES ('Asia/Shanghai', 5)")
    conn.commit()
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()
    prio = conn.execute(
        "SELECT priority FROM timezones WHERE timezone='Asia/Shanghai'").fetchone()[0]
    assert prio == 5  # INSERT OR IGNORE 不覆盖
    conn.close()


def test_migrate_renames_v1_tables_and_columns(tmp_path):
    conn = _v1_db(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO sessions (session_id, device_uuid, session_date, status, root_path) "
        "VALUES ('s1', 'd1', '2026-08-01', 'finalized', '/r')")
    conn.execute(
        "INSERT INTO files (session_id, file_type, file_ext, file_path, file_size) "
        "VALUES ('s1', 'audio', 'ogg', '/r/d1/s1/a.ogg', 10)")
    conn.execute(
        "INSERT INTO chat_messages (msg_id, session_id, chat_session_id, ts_ms, role, text_content) "
        "VALUES ('m1', 's1', 'cs1', 1, 'user', 'hi')")
    conn.execute(
        "INSERT INTO agent_jobs (job_key, agent_name, device_uuid, session_id, status, "
        "summary, requested_ts_ms, requested_at, pg_updated_at) "
        "VALUES ('j1', 'hermes', 'd1', 'cs1', 'done', 's', 1, '2026-08-01T00:00:00Z', "
        "'2026-08-01T00:00:00.123456+00:00')")
    conn.commit()
    migrate(conn)
    conn.commit()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "sessions" not in tables and "recording_sessions" in tables
    for t in ("recording_sessions", "files"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
        assert "rsession_id" in cols and "session_id" not in cols, t
    # chat_messages：不再改名维护，直接清理删除（2026-09-15 修订裁定 G）
    assert "chat_messages" not in tables
    # 数据保留 + FK 随改名更新（指向新表新列）
    assert conn.execute(
        "SELECT rsession_id FROM recording_sessions").fetchone()[0] == "s1"
    fks = conn.execute("PRAGMA foreign_key_list(files)").fetchall()
    assert fks and fks[0][2] == "recording_sessions" and fks[0][3] == "rsession_id"
    # agent_jobs：pg_updated_at → updated_at + 归一化（微秒 +00:00 → 毫秒 Z）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_jobs)").fetchall()}
    assert "updated_at" in cols and "pg_updated_at" not in cols
    v = conn.execute("SELECT updated_at FROM agent_jobs WHERE job_key='j1'").fetchone()[0]
    assert v == "2026-08-01T00:00:00.123Z"
    # agent_jobs.session_id（chat session id）不动
    sid = conn.execute("SELECT session_id FROM agent_jobs").fetchone()[0]
    assert sid == "cs1"
    conn.close()


def test_migrate_idempotent(tmp_path):
    conn = _v1_db(tmp_path / "t.db")
    migrate(conn)
    conn.commit()
    migrate(conn)  # 二次执行无错
    conn.commit()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "recording_sessions" in tables and "sessions" not in tables
    conn.close()


def test_migrate_drops_legacy_day_force(tmp_path):
    """存量库孤儿 day_force 表被清理（session_json_refactor 裁决 A；幂等）。"""
    conn = _v1_db(tmp_path / "t.db")
    conn.execute("CREATE TABLE day_force (timezone TEXT NOT NULL, "
                 "local_date TEXT NOT NULL, "
                 "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), "
                 "PRIMARY KEY (timezone, local_date))")
    conn.commit()
    migrate(conn)
    migrate(conn)  # 二次执行无错
    conn.commit()
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='day_force'").fetchone() is None
    conn.close()


def test_fresh_deploy_no_rename_needed(tmp_path):
    """新部署：直接按新名建表（§7），无 sessions 表可改名。"""
    conn = sqlite3.connect(tmp_path / "t.db")
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='recording_sessions'").fetchone()
    conn.close()


# ── index_state 回填（retention 方案 §2.4）───────────────────────────────

_RUNS_DDL = (
    "CREATE TABLE mcp_pipeline_runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "{col} TEXT, stage TEXT NOT NULL, started_at TEXT NOT NULL, "
    "finished_at TEXT, status TEXT NOT NULL DEFAULT 'running', "
    "exit_code INTEGER, error TEXT)")


def test_index_state_backfill_from_legacy_runs(tmp_path):
    """存量库（旧列名 session_id）：migrate 改名 + index_state 空 → 按 target
    回填最新 ok 水位（MAX(finished_at)；非 ok / NULL finished 不计）。"""
    conn = _v1_db(tmp_path / "t.db")
    conn.execute(_RUNS_DDL.format(col="session_id"))
    conn.executemany(
        "INSERT INTO mcp_pipeline_runs (session_id, stage, started_at, "
        "finished_at, status) "
        "VALUES (?, 'reindex', '2000-01-01T00:00:00.000Z', ?, ?)",
        [("s1", "2026-09-01T00:00:00.000Z", "ok"),
         ("s1", "2026-09-02T00:00:00.000Z", "ok"),       # 同组取 MAX
         ("s1", "2026-09-03T00:00:00.000Z", "failed"),   # 非 ok 不计
         (None, "2026-09-04T00:00:00.000Z", "ok"),       # 全局 → ''
         (None, None, "ok"),                             # finished NULL 防御
         ("s2", "2026-09-05T00:00:00.000Z", "running"),  # running 不计
         ("s3", None, "ok")])                            # 组内全 NULL → 不写行
    conn.commit()
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT target, last_reindex_at FROM index_state").fetchall()}
    assert rows == {"s1": "2026-09-02T00:00:00.000Z",
                    "": "2026-09-04T00:00:00.000Z"}
    conn.close()


def test_index_state_backfill_skips_nonempty(tmp_path):
    """已有行不覆盖：非空表跳过回填（二次启动幂等，值保持）。"""
    conn = _v1_db(tmp_path / "t.db")
    conn.execute(_RUNS_DDL.format(col="target"))
    conn.execute(
        "INSERT INTO mcp_pipeline_runs (target, stage, started_at, finished_at, "
        "status) VALUES ('s1', 'reindex', '2000-01-01T00:00:00.000Z', "
        "'2026-09-02T00:00:00.000Z', 'ok')")
    conn.commit()
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()
    conn.execute(
        "UPDATE index_state SET last_reindex_at = '2099-01-01T00:00:00.000Z'")
    conn.commit()
    ensure_schema(conn, "Asia/Shanghai", [])   # 二次启动：不覆盖
    conn.commit()
    v = conn.execute(
        "SELECT last_reindex_at FROM index_state WHERE target = 's1'").fetchone()[0]
    assert v == "2099-01-01T00:00:00.000Z"
    conn.close()


def test_migrate_normalizes_space_and_naive_formats(tmp_path):
    conn = _v1_db(tmp_path / "t.db")
    conn.executemany(
        "INSERT INTO agent_jobs (job_key, agent_name, device_uuid, session_id, status, "
        "summary, requested_ts_ms, requested_at, pg_updated_at) "
        "VALUES (?, 'hermes', 'd1', 'cs1', 'done', 's', 1, '2026-08-01T00:00:00Z', ?)",
        [("j1", "2026-08-01 00:00:00.123456"),       # 空格 + 微秒
         ("j2", "2026-08-01T00:00:00.123+00:00"),    # T + +00:00
         ("j3", "2026-08-01T00:00:00.123Z"),         # 已是规范格式
         ])
    conn.commit()
    migrate(conn)
    conn.commit()
    rows = {r[0]: r[1] for r in conn.execute(
        "SELECT job_key, updated_at FROM agent_jobs").fetchall()}
    assert rows["j1"] == "2026-08-01T00:00:00.123Z"
    assert rows["j2"] == "2026-08-01T00:00:00.123Z"
    assert rows["j3"] == "2026-08-01T00:00:00.123Z"


# ── 时间戳归一化单元 ────────────────────────────────────────────────────

def test_normalize_ts():
    assert normalize_ts("2026-08-01T00:00:00.123Z") == "2026-08-01T00:00:00.123Z"
    assert normalize_ts("2026-08-01T00:00:00.123456+00:00") == "2026-08-01T00:00:00.123Z"
    assert normalize_ts("2026-08-01 00:00:00") == "2026-08-01T00:00:00.000Z"
    assert normalize_ts(None) is None
    assert normalize_ts("not-a-date") == "not-a-date"  # 无法解析原样保留
    assert normalize_ts("") == ""


def test_ts_re_matches_canonical():
    assert TS_RE.match("2026-08-01T00:00:00.123Z")
    assert not TS_RE.match("2026-08-01T00:00:00.123456+00:00")
    assert not TS_RE.match("2026-08-01 00:00:00.123Z")
