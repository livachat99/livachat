# LvData Inventory Tool Design

> **Phase 1 数据盘点** — 遍历 lvdata 目录，解析 metadata，建立可查询的数据索引，为下游分析工具提供数据基础。

---

## 1. Overview

`lvdata_inventory.py` 是一个 Python CLI 工具，扫描 lvdata 目录树，提取所有 session 的结构化信息并存入 SQLite（或 PostgreSQL）数据库。下游工具（`lvdata_analyze_audio.py`、`lvdata_analyze_video.py`、`lvdata_summary.py`）直接查询该数据库，无需重复遍历文件系统。

### 设计原则

- **单次扫描，多次查询** — inventory 跑一次建好索引，下游分析只读 DB
- **SQLite 优先** — 零依赖、零配置、便携；可选 PostgreSQL 支持大规模部署
- **幂等扫描** — 重复执行不产生重复记录（基于 UUIDv7 主键 upsert）
- **UUIDv7 时间戳** — 从文件名解析毫秒级时间戳，提供时序排序能力
- **分层过滤** — 支持 --device-uuid / --user-uuid 缩小扫描范围

---

## 2. Data Model

### 2.1 目录结构约定

```
<root_dir>/                          # 默认 /opt/lvdata
  <device_uuid>/                     # e.g. b53985c7-cb7f-4382-95c9-18d33a9bc36a
    <session_id>/                    # e.g. 019f92f2-5b73-7886-8000-576e09d3f328
      session.json                  # session metadata
      audio/
        <uuidv7>.ogg                # OGG Opus, 48kHz, 60s segments
      video/
        <uuidv7>.jpg                # JPEG screenshots
      chat/
        history.jsonl               # timestamped chat turns
```

### 2.2 UUIDv7 时间戳解析

UUIDv7 前 48 bits（6 bytes）为 big-endian 编码的 Unix 毫秒时间戳。

UUIDv7 格式（RFC 9562）：

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                           unix_ts_ms                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          unix_ts_ms           |  ver  |       rand_a          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|var|                        rand_b                             |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                            rand_b                             |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

解析方式：去掉 UUID 中的 `-`，取前 12 个 hex 字符（6 bytes），按 big-endian 转为整数：

```python
import uuid

def uuidv7_to_ms(uuid_str: str) -> int:
    """Extract Unix ms timestamp from UUIDv7 string."""
    u = uuid.UUID(uuid_str)
    # bytes[0:6] = 48-bit big-endian timestamp
    ts_bytes = u.bytes[:6]
    return int.from_bytes(ts_bytes, byteorder='big')

def uuidv7_to_utc(uuid_str: str) -> str:
    ms = uuidv7_to_ms(uuid_str)
    return datetime.utcfromtimestamp(ms / 1000).isoformat() + "Z"
```

示例：`019f92f2-5cad-7490-8000-1914e1d8b908`
→ bytes[0:6] = `01 9f 92 f2 5c ad`
→ timestamp = 1,784,876,784,813 ms
→ 2026-07-24T07:06:24.813Z

### 2.3 session.json Schema（已有字段）

> **2026-09-15 更新（session_json_refactor）**：`timezone`、`status`、
> `last_active_at`、`finalized_at` 已从 session.json 移除——文件静态化，仅
> `start()` 首写一次（幂等，已存在跳过），不再周期刷新/finalize 重写。
> 更早的 `stats`/`chat_session_ids`/`analysis_status`/`analyzed_at` 亦已不再写出。
> 本节表格描述 v1 存量文件格式；lvdata 摄入侧对旧文件的动态字段照常摄入但
> 不消费（无 finalize 转换、无 day_force）。

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | schema 版本号 |
| `recording_session_id` | string (UUIDv7) | session 唯一 ID |
| `device_uuid` | string (UUID) | 设备标识 |
| `user_uuid` | string (UUID) or null | 用户标识 |
| `session_date` | date string | 会话日期 "YYYY-MM-DD" |
| `timezone` | string | 时区 |
| `status` | string | "active" / "finalized" |
| `started_at` | ISO 8601 | 开始时间 |
| `last_active_at` | ISO 8601 | 最后活跃时间 |
| `finalized_at` | ISO 8601 or null | 结束时间 |
| `config` | object | 音频/视频/图片采集配置 |
| `stats` | object | 各类型计数器 |
| `chat_session_ids` | string[] | 关联的 chat session ID 列表 |
| `analysis_status` | string | "pending" / "in_progress" / "completed" |
| `analyzed_at` | ISO 8601 or null | 分析完成时间 |

### 2.4 Database Schema (SQLite)

```sql
-- Session 主表：从 session.json 提取
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,          -- recording_session_id (UUIDv7)
    device_uuid     TEXT NOT NULL,
    user_uuid       TEXT,
    session_date    TEXT NOT NULL,
    timezone        TEXT DEFAULT 'UTC',
    status          TEXT NOT NULL,             -- active | finalized
    started_at      TEXT,                      -- ISO 8601
    last_active_at  TEXT,
    finalized_at    TEXT,
    duration_ms     INTEGER,                   -- computed: finalized_at - started_at
    analysis_status TEXT DEFAULT 'pending',    -- pending | in_progress | completed
    analyzed_at     TEXT,
    root_path       TEXT NOT NULL,             -- absolute path to session dir on disk

    -- profile config
    audio_codec     TEXT,                      -- from config.audio_codec
    audio_container TEXT,                      -- from config.audio_container
    audio_segment_s INTEGER,                   -- from config.audio_segment_s
    video_format    TEXT,                      -- from config.video_format
    image_enabled   INTEGER DEFAULT 0,         -- from config.image_enabled
    ai_audio_recorded INTEGER DEFAULT 0,       -- from config.ai_audio_recorded

    -- counters (from stats, denormalized for fast queries)
    cnt_audio_segments INTEGER DEFAULT 0,
    cnt_audio_frames   INTEGER DEFAULT 0,
    cnt_video_frames   INTEGER DEFAULT 0,
    cnt_image_count    INTEGER DEFAULT 0,
    cnt_chat_messages  INTEGER DEFAULT 0,
    cnt_total_bytes    INTEGER DEFAULT 0,

    -- bookkeeping
    scanned_at      TEXT NOT NULL DEFAULT (datetime('now')),
    scan_version    INTEGER DEFAULT 1
);

-- 文件清单表：每个磁盘文件一行
CREATE TABLE IF NOT EXISTS files (
    file_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    file_type       TEXT NOT NULL,             -- audio | video | chat | meta
    file_ext        TEXT NOT NULL,             -- ogg | jpg | jsonl | json
    file_path       TEXT NOT NULL UNIQUE,      -- absolute path
    file_size       INTEGER NOT NULL,          -- bytes
    uuid_ts_ms      INTEGER,                  -- parsed UUIDv7 timestamp (null for session.json)
    uuid_ts_utc     TEXT,                     -- ISO 8601 of uuid_ts_ms (null for session.json)

    scanned_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_files_session ON files(session_id);
CREATE INDEX IF NOT EXISTS idx_files_type    ON files(file_type);
CREATE INDEX IF NOT EXISTS idx_files_ts      ON files(uuid_ts_ms);

-- 聊天消息明细表：从 history.jsonl 逐行解析
CREATE TABLE IF NOT EXISTS chat_messages (
    msg_id          TEXT PRIMARY KEY,          -- ref_id from JSONL
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    chat_session_id TEXT,                      -- from JSONL
    ts_ms           INTEGER NOT NULL,         -- from JSONL 'ts' field
    ts_utc          TEXT,                      -- from JSONL 'utc' field
    role            TEXT NOT NULL,             -- user | assistant
    text_content    TEXT NOT NULL,
    text_length     INTEGER,                   -- len(text_content)

    scanned_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_ts      ON chat_messages(ts_ms);
CREATE INDEX IF NOT EXISTS idx_chat_role    ON chat_messages(role);

-- Inventory 扫描记录（跟踪扫描历史）
CREATE TABLE IF NOT EXISTS inventory_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    root_dir        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    sessions_found  INTEGER DEFAULT 0,
    files_found     INTEGER DEFAULT 0,
    messages_found  INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running'    -- running | completed | failed
);
```

### 2.5 为什么 SQLite 优先

| 维度 | SQLite | PostgreSQL |
|------|--------|------------|
| 部署复杂度 | 零（单文件） | 需要运行中的 PG 实例 |
| 便携性 | 可随数据一起打包 | 绑定特定服务器 |
| 并发读取 | 无限并发读 | 无限并发读 |
| 当前数据量 | ~100 文件 / ~30 条消息 | 完全不需要 PG |
| 下游工具查询 | `sqlite3 /opt/lvdata/lvdata_inventory.db "SELECT ..."` | 需要 psql + 连接串 |

如需 PostgreSQL，通过 `--db-url postgresql://...` 切换，schema 一致。

---

## 3. CLI Interface

### 3.1 命令签名

```
lvdata_inventory.py [OPTIONS]
```

### 3.2 参数列表

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--root-dir` | PATH | `/opt/lvdata` | lvdata 数据根目录 |
| `--device-uuid` | UUID | (none) | 仅扫描指定设备 |
| `--user-uuid` | UUID | (none) | 仅扫描匹配 user_uuid 的 session |
| `--db-path` | PATH | `/opt/lvdata/lvdata_inventory.db` | SQLite 数据库文件路径 |
| `--db-url` | URL | (none) | PostgreSQL 连接串（替代 SQLite） |
| `--full-scan` | flag | false | 全量重扫（默认增量：跳过 unchanged session） |
| `--verbose` / `-v` | flag | false | 详细输出 |

### 3.3 使用示例

```bash
# 基础扫描（默认 /opt/lvdata → /opt/lvdata/lvdata_inventory.db）
python lvdata_inventory.py

# 指定数据目录和 DB 路径
python lvdata_inventory.py --root-dir /data/lvdata --db-path /data/lvdata_inventory.db

# 只扫描特定设备
python lvdata_inventory.py --device-uuid b53985c7-cb7f-4382-95c9-18d33a9bc36a

# 按用户过滤（需要 session.json 中有 user_uuid）
python lvdata_inventory.py --user-uuid <uuid>

# 全量重扫
python lvdata_inventory.py --full-scan -v

# PostgreSQL 模式
python lvdata_inventory.py --db-url postgresql://user:pass@localhost:5432/lvdata

# 扫描后直接查询
python lvdata_inventory.py --root-dir /opt/lvdata && \
  sqlite3 /opt/lvdata/lvdata_inventory.db "SELECT session_date, status, cnt_audio_segments, cnt_video_frames, cnt_chat_messages FROM sessions"
```

---

## 4. Processing Logic

### 4.1 主流程

```
main()
  ├── parse_args()
  ├── connect_db()                          # SQLite or PG via --db-url
  ├── ensure_schema()                       # CREATE TABLE IF NOT EXISTS
  ├── discover_sessions(root_dir)           # walk dirs, yield (device_uuid, session_id, path)
  │     └── filter by --device-uuid / --user-uuid
  ├── for each session:
  │     ├── should_skip?(session_path)      # incremental: check scanned_at + file mtimes
  │     ├── parse_session_json(path)        # → dict
  │     ├── upsert_session(parsed)          # INSERT OR REPLACE
  │     ├── scan_files(session_path)        # walk audio/, video/, chat/, session.json
  │     │     ├── parse UUIDv7 timestamp from filename
  │     │     └── upsert_file(record)       # INSERT OR REPLACE
  │     └── scan_chat(session_path)         # read history.jsonl line by line
  │           └── upsert_message(record)    # INSERT OR REPLACE
  ├── update_inventory_run(run_id, 'completed')
  └── print_summary()
```

### 4.2 增量扫描逻辑

```python
def should_skip(session_path: str, db: Connection) -> bool:
    """跳过自上次扫描后未变化的 session"""
    row = db.execute(
        "SELECT scanned_at FROM sessions WHERE session_id = ?", [session_id]
    ).fetchone()
    if row is None:
        return False  # 新 session，不跳过

    # 检查关键文件 mtime
    key_files = [
        os.path.join(session_path, "session.json"),
        os.path.join(session_path, "chat", "history.jsonl"),
    ]
    scanned_at = datetime.fromisoformat(row[0])
    for f in key_files:
        if os.path.exists(f):
            mtime = datetime.fromtimestamp(os.path.getmtime(f))
            if mtime > scanned_at:
                return False  # 有更新
    return True
```

### 4.3 UUIDv7 时间戳解析

```python
import uuid
from datetime import datetime, timezone

def uuidv7_to_ms(uuid_str: str) -> int:
    """Extract Unix ms timestamp from the first 6 bytes of a UUIDv7."""
    u = uuid.UUID(uuid_str)
    return int.from_bytes(u.bytes[:6], byteorder='big')

def uuidv7_to_utc(uuid_str: str) -> str:
    ms = uuidv7_to_ms(uuid_str)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
```

### 4.4 扫描进度与容错

- 单个 session 解析失败 **不中断** 整体扫描 — 记录错误日志并继续
- `session.json` 缺失 → 跳过该 session，打印 warning
- `history.jsonl` 缺失 → 正常（非 chat 型 session），`cnt_chat_messages = 0`
- 空目录 / 无效 UUIDv7 文件名 → skip，打印 verbose 信息

---

## 5. Downstream Tool Queries

下游工具通过查询 inventory DB 获取所需数据，无需直接操作文件系统。

### 5.1 lvdata_analyze_audio.py 需要的查询

```sql
-- 获取所有待转写的音频文件
SELECT f.file_path, f.file_id, f.file_size, f.uuid_ts_ms,
       s.session_id, s.session_date, s.audio_codec, s.audio_container
FROM files f
JOIN sessions s ON f.session_id = s.session_id
WHERE f.file_type = 'audio'
  AND s.analysis_status = 'pending'
ORDER BY s.session_date, f.uuid_ts_ms;
```

### 5.2 lvdata_analyze_video.py 需要的查询

```sql
-- 获取所有待分析的截图
SELECT f.file_path, f.file_id, f.file_size, f.uuid_ts_ms,
       s.session_id, s.session_date
FROM files f
JOIN sessions s ON f.session_id = s.session_id
WHERE f.file_type = 'video'
ORDER BY s.session_date, f.uuid_ts_ms;
```

### 5.3 lvdata_summary.py 需要的查询

```sql
-- Session 汇总信息
SELECT session_date, status, duration_ms,
       cnt_audio_segments, cnt_video_frames, cnt_chat_messages, cnt_total_bytes
FROM sessions
ORDER BY session_date;

-- 每个 session 的对话时间线
SELECT c.ts_ms, c.role, c.text_content, c.chat_session_id
FROM chat_messages c
WHERE c.session_id = ?
ORDER BY c.ts_ms;

-- 跨 session 统计
SELECT
  COUNT(*) AS total_sessions,
  SUM(cnt_audio_segments) AS total_audio,
  SUM(cnt_video_frames) AS total_video,
  SUM(cnt_chat_messages) AS total_messages,
  SUM(cnt_total_bytes) AS total_bytes
FROM sessions;
```

---

## 6. File Layout

```
tools/data-analysis//
  lvdata_inventory.py          # Phase 1: CLI tool, this design
  lvdata_analyze_audio.py      # Phase 2: ASR pipeline (future)
  lvdata_analyze_video.py      # Phase 3: vision analysis (future)
  lvdata_summary.py            # Phase 4-5: cross-session report (future)
```

每个工具共享同一 inventory DB，通过 `--db-path` 参数指定。

---

## 7. Dependencies

```python
# stdlib only — sqlite3, json, os, argparse, datetime, glob
# PostgreSQL 模式下需要 psycopg2（可选依赖）
```

SQLite 模式零外部依赖。PostgreSQL 模式通过 `--db-url` 可选启用。

---

## 8. Open Questions

1. **user_uuid 过滤时机** — session.json 中 `user_uuid` 可能为 null（当前数据中三个 session 的 user_uuid 都是 null）。如果未来 lvdata 根目录层级变化（例如 `<user_uuid>/<device_uuid>/<session_id>`），需要在 `discover_sessions()` 中调整遍历逻辑。当前设计假设 `user_uuid` 来自 session.json 字段，筛选在 parse 阶段生效。

2. **PostgreSQL 是否必需？** — 当前数据量（3 session, ~100 files, ~30 messages）完全不需要 PG。设计保留 `--db-url` 入口但 Phase 1 仅实现 SQLite。

3. **analysis_status 字段** — session.json 中已有 `analysis_status` 字段（当前全部 "pending"）。下游分析工具完成后应更新此字段，使 inventory 工具可追踪分析进度。是否由 inventory 工具提供 `--mark-analyzed` 子命令，还是由下游工具自行更新 DB？

---

*Design version: 1.0 | Created: 2026-07-31*
