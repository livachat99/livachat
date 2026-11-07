"""v2 启动 schema：§5 全部 DDL + §7 幂等改名迁移 + agent_jobs 时间戳归一化。

所有 DDL 均幂等；改名一次性执行（新部署直接按新名建表）。
v1 存量表 v2 不读不写（recording_sessions（原 sessions）/files/agent_jobs
基础表除外，它们是 v1 与 v2 共用的数据底座）。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from .timeutil import now_iso

# §5.1 规范时间戳格式（'T' 分隔、毫秒、'Z'）
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

# §5.1 events 表（SSOT）
EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ref   TEXT NOT NULL UNIQUE,
    device_uuid TEXT NOT NULL,
    rsession_id TEXT NOT NULL,
    type        TEXT NOT NULL,
    ts_ms       INTEGER NOT NULL,
    ts_utc      TEXT,
    source      TEXT NOT NULL,
    summary     TEXT,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_events_rsession_ts ON events(rsession_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_updated ON events(updated_at);
"""

# §5.3 analysis_runs 表（分析审计）
ANALYSIS_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    model         TEXT,
    status        TEXT NOT NULL,
    usage_input_tokens  INTEGER,
    usage_output_tokens INTEGER,
    latency_ms    INTEGER,
    error         TEXT,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_file ON analysis_runs(file_id);
"""

# §5.4 day_summary 表
DAY_SUMMARY_SQL = """
CREATE TABLE IF NOT EXISTS day_summary (
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
    -- §7.2.1：日卡派生字段预计算（counts/hot_slots/images）的 JSON 载体，
    -- 由 day_stats stage 写、BFF overview 读（docs/bff_service_interfaces.md §7）
    day_stats       TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (timezone, local_date)
);
CREATE INDEX IF NOT EXISTS idx_day_summary_status ON day_summary(status);
"""

# §5.5 timezones 表
TIMEZONES_SQL = """
CREATE TABLE IF NOT EXISTS timezones (
    timezone   TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    priority   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

# §5.6 day_scan_state 表（day-summary dirty 发现游标）
DAY_SCAN_STATE_SQL = """
CREATE TABLE IF NOT EXISTS day_scan_state (
    timezone     TEXT PRIMARY KEY,
    last_scan_at TEXT NOT NULL DEFAULT ''
);
"""

# §5.7b day_deferred 表（ok 日 + debounce 中更新的再发现载体，§6.5）
# 游标恒推进后，debounce 未过的更新靠此表持久化再发现——崩溃重启不丢
# （原则 8：工作状态全部落盘）
DAY_DEFERRED_SQL = """
CREATE TABLE IF NOT EXISTS day_deferred (
    timezone   TEXT NOT NULL,
    local_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (timezone, local_date)
);
"""

# §5.8 period_summary 表（本期建表；生成逻辑下一阶段）
PERIOD_SUMMARY_SQL = """
CREATE TABLE IF NOT EXISTS period_summary (
    timezone       TEXT NOT NULL,
    period         TEXT NOT NULL,
    period_start   TEXT NOT NULL,
    period_summary TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'pending',
    model          TEXT,
    error          TEXT,
    generated_at   TEXT,
    PRIMARY KEY (timezone, period, period_start)
);
"""

# §5.C flush 门控水印（embed/day_summary 节流，recording_data_process.md §5.C）：
# 「待处理」= events.updated_at > last_flush_at；由 watcher 在 flush 组全部成功后
# 写入（半途崩溃不推进 → 下轮重触发）
FLUSH_STATE_SQL = """
CREATE TABLE IF NOT EXISTS flush_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    last_flush_at TEXT
);
"""

# §5.10 index_state 表（reindex 水位：target = rsession_id；'' = 全局全量重建）。
# 值 = T0 快照（重建入口时刻，先于取件与任何 events 读取）——写出的水位必须 ≤
# 其声称覆盖的所有读取时刻；'' 只由全量重建写（retention 方案 §2.2）。
INDEX_STATE_SQL = """
CREATE TABLE IF NOT EXISTS index_state (
    target          TEXT PRIMARY KEY,
    last_reindex_at TEXT NOT NULL
);
"""

# §5.11 day_stats 扫描游标（docs/bff_service_interfaces.md §7.2.3）：键 = timezone，
# 值 = 扫描开始时刻（与 day_scan_state 同约定：扫描期间并发落入的事件下轮必见）。
# 与 day_scan_state 分表：两者独立推进（day_summary discover 与本 stage 互不影响）。
DAY_STATS_STATE_SQL = """
CREATE TABLE IF NOT EXISTS day_stats_state (
    timezone     TEXT PRIMARY KEY,
    last_scan_at TEXT NOT NULL DEFAULT ''
);
"""

# §5.2 files 表增强列（幂等 ALTER）
FILES_ALTERS = (
    ("device_uuid", "TEXT"),
    ("analysis_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("fail_count", "INTEGER NOT NULL DEFAULT 0"),
    ("analyzed_at", "TEXT"),
    ("error", "TEXT"),
    # cs 单链路（recording_data_process.md §2.2）：cs 行 updated_at（增量判定）+
    # 处理进度游标（processed_updated_at 落后 = 待处理；processed_offset 为
    # jsonl 字节偏移，NULL 视同 0）
    ("src_updated_at", "TEXT"),
    ("processed_updated_at", "TEXT"),
    ("processed_offset", "INTEGER"),
)

# §5.4 day_summary 表增强列（幂等 ALTER；docs/bff_service_interfaces.md §7.2.1）
DAY_SUMMARY_ALTERS = (
    ("day_stats", "TEXT NOT NULL DEFAULT '{}'"),
)

# §7.6 四个基础表（新部署直接按 v2 名建表；存量库经 migrate 改名后已存在，跳过）
BASE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS recording_sessions (
    rsession_id     TEXT PRIMARY KEY,
    device_uuid     TEXT NOT NULL,
    user_uuid       TEXT,
    session_date    TEXT NOT NULL,
    -- session_json_refactor（2026-09-15）：静态 session.json 无 timezone/status；
    -- 新行恒默认 'UTC' / ''（旧行保留原值）。两列均无读取方——勿加 SQL 过滤。
    timezone        TEXT DEFAULT 'UTC',
    status          TEXT NOT NULL,
    started_at      TEXT,
    last_active_at  TEXT,
    finalized_at    TEXT,
    duration_ms     INTEGER,
    analysis_status TEXT DEFAULT 'pending',
    analyzed_at     TEXT,
    root_path       TEXT NOT NULL,
    audio_codec     TEXT,
    audio_container TEXT,
    audio_segment_s INTEGER,
    video_format    TEXT,
    image_enabled   INTEGER DEFAULT 0,
    ai_audio_recorded INTEGER DEFAULT 0,
    cnt_audio_segments INTEGER DEFAULT 0,
    cnt_audio_frames   INTEGER DEFAULT 0,
    cnt_video_frames   INTEGER DEFAULT 0,
    cnt_image_count    INTEGER DEFAULT 0,
    cnt_chat_messages  INTEGER DEFAULT 0,
    cnt_total_bytes    INTEGER DEFAULT 0,
    scanned_at      TEXT NOT NULL DEFAULT (datetime('now')),
    scan_version    INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS files (
    file_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rsession_id     TEXT NOT NULL REFERENCES recording_sessions(rsession_id),
    file_type       TEXT NOT NULL,
    file_ext        TEXT NOT NULL,
    file_path       TEXT NOT NULL UNIQUE,
    file_size       INTEGER NOT NULL,
    uuid_ts_ms      INTEGER,
    uuid_ts_utc     TEXT,
    scanned_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_files_session ON files(rsession_id);
CREATE INDEX IF NOT EXISTS idx_files_type    ON files(file_type);
CREATE INDEX IF NOT EXISTS idx_files_ts      ON files(uuid_ts_ms);
-- （chat_messages 已删除：2026-09-15 修订裁定 G——新链路无读写方；原文归档 =
--   history.jsonl 本身。存量库由 migrate() 的 DROP TABLE 清理）
-- （agent_job_links/agent_jobs_sync_state 随 jobs_watch 删除：agent job 事件
--   由 chatlog pass 从 history.jsonl 铸造，不需要跨库关联与 affected 交付）
-- recording_files 同步水位（recording_data_process.md §2.2）：
-- keyset (updated_at, file_id)；状态即恢复载体，无 pending 列表
CREATE TABLE IF NOT EXISTS recording_files_sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_updated_at TEXT,
    last_file_id    INTEGER,
    last_run_at     TEXT,
    rows_upserted   INTEGER DEFAULT 0
);
"""

V2_DDL = (EVENTS_SQL, ANALYSIS_RUNS_SQL, DAY_SUMMARY_SQL, TIMEZONES_SQL,
          DAY_SCAN_STATE_SQL, DAY_DEFERRED_SQL,
          PERIOD_SUMMARY_SQL, FLUSH_STATE_SQL, INDEX_STATE_SQL, DAY_STATS_STATE_SQL)


def normalize_ts(value: str | None) -> str | None:
    """任意来源时间戳 → §5.1 规范格式（ISO 8601 UTC、'T'、毫秒、'Z'）。

    已是规范格式原样返回；无法解析原样保留（不破坏数据）。
    """
    if not value:
        return value
    v = value.strip()
    if TS_RE.match(v):
        return v
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # PG 来源 naive 一律按 UTC
        return (dt.astimezone(timezone.utc)
                .isoformat(timespec="milliseconds").replace("+00:00", "Z"))
    except ValueError:
        return value


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def migrate(conn: sqlite3.Connection) -> None:
    """§7 幂等改名 + agent_jobs.updated_at 存量格式归一化（同一事务）。

    - sessions → recording_sessions；sessions/files 的
      session_id → rsession_id（FK 随 foreign_keys=ON 自动更新）；
    - agent_jobs.pg_updated_at → updated_at（Agent Relay 写入的变更水位）；
      synced_at 废弃（v2 不读不写，列保留）；
    - chat_messages 清理删除（2026-09-15 修订裁定 G：无读写方；幂等）；
    - day_force 清理删除（session_json_refactor 裁决 A：finalize 信号消失后
      无写入方；幂等，存量 dev 库一次清掉）；
    - 改名同事务内对 updated_at 存量行做格式归一化（keyset 水位字符串比较）。
    """
    # foreign_keys=ON：改名时 FK 定义随之更新（§7），不产生悬空引用
    conn.execute("PRAGMA foreign_keys = ON")
    tables = _table_names(conn)
    if "sessions" in tables and "recording_sessions" not in tables:
        conn.execute("ALTER TABLE sessions RENAME TO recording_sessions")
        tables = _table_names(conn)  # 改名后刷新（原名表可能刚消失/新名出现）
    for t in ("recording_sessions", "files"):
        if t in tables:
            cols = _column_names(conn, t)
            if "session_id" in cols and "rsession_id" not in cols:
                conn.execute(f"ALTER TABLE {t} RENAME COLUMN session_id TO rsession_id")
    if "agent_jobs" in tables:
        cols = _column_names(conn, "agent_jobs")
        if "pg_updated_at" in cols and "updated_at" not in cols:
            conn.execute("ALTER TABLE agent_jobs RENAME COLUMN pg_updated_at TO updated_at")
        if "updated_at" in _column_names(conn, "agent_jobs"):
            rows = conn.execute(
                "SELECT job_key, updated_at FROM agent_jobs").fetchall()
            updates = []
            for key, v in rows:
                n = normalize_ts(v)
                if n != v:
                    updates.append((n, key))
            if updates:
                conn.executemany(
                    "UPDATE agent_jobs SET updated_at = ? WHERE job_key = ?", updates)
    if "mcp_pipeline_runs" in tables:
        # §6.7：session_id 列语义推广为通用 target（rsession_id / tz:local_date / NULL）
        cols = _column_names(conn, "mcp_pipeline_runs")
        if "session_id" in cols and "target" not in cols:
            conn.execute("ALTER TABLE mcp_pipeline_runs RENAME COLUMN session_id TO target")
    # chat_messages 清理（2026-09-15 修订裁定 G：无读写方；幂等，历史库一次清掉）
    if "chat_messages" in tables:
        conn.execute("DROP TABLE chat_messages")
    # day_force 清理（session_json_refactor 裁决 A：无写入方；幂等）
    if "day_force" in tables:
        conn.execute("DROP TABLE day_force")


def _backfill_index_state(conn: sqlite3.Connection, tables: set[str]) -> None:
    """存量库一次性回填 index_state（幂等）：仅当表为空且旧审计表存在时，按
    target 取最新 ok reindex 的完成时刻。时序约束：必须在 watcher 首轮清理
    之前执行（本函数在 ensure_schema 内 → 天然满足，retention 方案 §2.4）；
    漏/未回填的后果 = 首次判全量滞后一次（自愈）。回填值是完成时刻语义
    （非 T0）——残留竞态窗口的 event 待该会话下次更新自愈。
    """
    if "index_state" not in tables or "mcp_pipeline_runs" not in tables:
        return
    if conn.execute("SELECT 1 FROM index_state LIMIT 1").fetchone() is not None:
        return
    conn.execute(
        "INSERT OR IGNORE INTO index_state (target, last_reindex_at) "
        "SELECT COALESCE(target, ''), MAX(finished_at) FROM mcp_pipeline_runs "
        "WHERE stage = 'reindex' AND status = 'ok' AND finished_at IS NOT NULL "
        "GROUP BY COALESCE(target, '')")


def ensure_schema(conn: sqlite3.Connection,
                  default_timezone: str | None = None,
                  timezones: list[str] | None = None) -> None:
    """启动执行：改名迁移 + v2 建表 + files 增强列 + timezones seed。

    调用方需开启事务；本函数不 commit（与调用方的工作同事务，崩溃整体回滚）。
    """
    migrate(conn)
    # 新部署：四个基础表按 v2 名直接建（§7.6）；存量库经 migrate 改名后已存在
    conn.executescript(BASE_TABLES_SQL)
    for ddl in V2_DDL:
        conn.executescript(ddl)
    tables = _table_names(conn)
    _backfill_index_state(conn, tables)
    if "files" in tables:
        cols = _column_names(conn, "files")
        for name, decl in FILES_ALTERS:
            if name not in cols:
                conn.execute(f"ALTER TABLE files ADD COLUMN {name} {decl}")
    # day_summary 增强列（§7.2.1）：存量库补 day_stats；新库建表已含 → 空操作
    if "day_summary" in tables:
        cols = _column_names(conn, "day_summary")
        for name, decl in DAY_SUMMARY_ALTERS:
            if name not in cols:
                conn.execute(f"ALTER TABLE day_summary ADD COLUMN {name} {decl}")
    # timezones seed（§5.5）：watcher.timezones + watcher.timezone 兜底；
    # INSERT OR IGNORE 不覆盖已有行
    if "timezones" in tables:
        tzs = list(timezones or [])
        if default_timezone and default_timezone not in tzs:
            tzs.append(default_timezone)
        conn.executemany(
            "INSERT OR IGNORE INTO timezones (timezone) VALUES (?)",
            [(t,) for t in tzs])
