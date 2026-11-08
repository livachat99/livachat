# LvData MCP Service 设计文档

> 状态：当前实现（以 `tools/lvdata-mcp` 代码为唯一事实来源）。
>
> 本文描述服务的最终形态与运行契约，不记录方案演进历史。`docs/data/` 下其余文档中
> 与本文或代码冲突的内容（尤其是 v1 CLI、旧表结构、旧 agent_jobs 同步链路）均为历史
> 资料，不作为实现依据。

## 1. 定位与总体架构

LvData MCP Service 是一个**单进程、asyncio** 的生活数据建档、分析与查询服务：

- **Ingestion 平面**：持续消费 LivaChat chat server 的录制文件索引，完成会话建档、
  chat 日志事件化、音频/视频分析、全文与向量索引、日摘要与日卡派生字段预计算。
- **Query 平面**：Streamable HTTP MCP server，对外提供 5 个只读工具；运维面提供
  同 token 的 REST 端点。

### 1.1 进程结构

```
lvdata-mcp（asyncio 单进程）
│
├─ Starlette + FastMCP
│    /mcp                              MCP Streamable HTTP（5 个只读工具）
│    /api/status                      运维状态
│    /api/analysis/trigger            手动触发 pipeline
│    /api/events/remove               删除 video 事件（级联）
│    /healthz /readyz                 探活（免鉴权）
│
├─ Watcher（三个后台 loop）
│    poll loop    每 poll_interval_s：inventory 同步 → 入队 round
│    work loop    消费 round：常跑组 + flush 门控组
│    prune loop   审计表清理 + video 帧保留
│
├─ Pipeline（进程内 stage 编排，无 subprocess）
│    inventory / chatlog / audio / video / day_stats / reindex
│    embed / day_summary（flush 组，门控执行）
│
├─ 分析模块
│    analysis/chatlog.py   chat 三 jsonl → events
│    analysis/audio.py     音频转写/摘要/情绪/能量包络
│    analysis/vision.py    视频帧窗口筛选 + 关键帧提取
│    summary/day.py        日摘要发现与生成
│    summary/day_stats.py  日卡派生字段预计算
│
├─ 索引
│    index.py   FTS5（CJK bigram，数据源 = events.summary）
│    vec.py     sqlite-vec 事件级 chunk + RRF 混合检索
│
└─ Store（只读查询面：events / day_summary / files / recording_sessions）
```

### 1.2 数据流

```
chat_server.db.recording_files
        │  ATTACH 只读 + keyset 增量
        ▼
  files / recording_sessions
        │
        ├── chat_history.jsonl / chat_transcribe.jsonl / chat_translate.jsonl
        │        └── chatlog pass ──► events(chat/transcribe/translate/agent_job)
        │
        ├── audio/*.ogg ──► audio 分析 ──► events(audio)
        │
        └── video/*.jpg ──► vision 两阶段 ──► events(window/video)
                                                  │
                                                  ├── FTS5 search_docs
                                                  ├── sqlite-vec vec_chunks
                                                  └── day_summary / day_stats
```

### 1.3 设计原则

1. **`events` 是唯一事实来源（SSOT）**：所有分析结果直接写入 `events`；FTS、向量、
   日摘要、MCP 查询全部以它为数据源。分析审计 JSON 只供人工核查，不参与查询。
2. **存储层与上层解耦**：`rsession_id` / `recording_sessions` 只存在于摄入、分析与
   内部查询；MCP 工具只面向 `events`，运维 API 仅提供聚合状态/事件操作，不暴露
   session 维度的查询接口。
3. **无 subprocess**：建档、分析、摘要、索引全部在进程内以 asyncio 执行。
4. **状态驱动、可崩溃恢复**：文件状态机、处理游标、失败计数、水位全部落盘；重启后
   从 DB 状态自然续跑，不依赖内存队列内容。
5. **增量处理**：轮询按 keyset / 水位取增量；空闲轮次不产生 LLM 调用。
6. **外部调用即 IO**：LLM / embedding 全部异步且有界并发（`watcher.concurrency`）。
7. **成本护栏**：单轮文件数、单轮日摘要数、flush 门控、并发上限、超时全部可配。
8. **幂等**：`event_ref` 稳定唯一，重分析 = upsert + 刷新 `updated_at`；索引可整体重建。

---

## 2. 数据布局与外部数据源

### 2.1 目录布局

```
<root_dir>/                                   # 默认 /opt/lvdata
  <device_uuid>/
    <rsession_id>/
      session.json                            # 会话静态元数据
      audio/<uuidv7>.ogg                      # Ogg Opus 音频段
      video/<uuidv7>.jpg                      # 屏幕截图
      chat/
        history.jsonl                         # 聊天回合
        transcribe.jsonl                      # 实时转写
        translate.jsonl                       # 实时翻译
  derived/
    <device_uuid>/<rsession_id>/
      audio/<segment_id>.json                 # 音频分析审计 JSON
      video/windows/<window_id>.json          # 窗口筛选审计 JSON
      video/frames/<frame_id>.json            # 关键帧提取审计 JSON
  lvdata_inventory.db                         # 主库（结构化数据 + FTS）
  lvdata_index.db                             # 向量库（可整体重建）
  chat_server.db                              # chat server 写入（只读 ATTACH）
```

- 媒体与 chat 文件路径均来自 chat server 的 `recording_files.rel_path`，服务端不扫描
  文件系统。
- `derived/` 只写分析审计 JSON；关键帧原始 jpg 仍位于源目录
  `<root>/<device>/<rsession>/video/<frame_id>.jpg`。
- `lvdata_index.db` 独立于主库：向量写入不锁主库，索引可单独重建。

### 2.2 chat_server.db 是摄入唯一事实源

`recording_files` 表由 chat server 在录制过程中写入，记录每个已落盘文件的
`rel_path`、`kind`、`size_bytes`、`ts_ms`、`updated_at`、`file_id`、`device_uuid`、
`recording_session_id`。lvdata-mcp：

- 以 `mode=ro` ATTACH `chat_server.db`，只读消费；
- 用 `(updated_at, file_id)` keyset 增量拉取；
- 所有写入（files / recording_sessions / 水位）在 lvdata 主库的**单事务**内提交；
- chat server DB 缺失或不可 ATTACH 时优雅空转（记 warning，不视为服务故障）。

部署前提：lvdata-mcp 与 chat server 同机（或共享同一数据卷），运行账户对
`chat_server.db` 所在目录有写权限（SQLite 只读打开 WAL 库仍需要目录可写）。

### 2.3 下游消费

- **MCP agent**（Hermes / OpenClaw 等）：经 `/mcp` 查询 events。
- **BFF**：直读同一 SQLite（events / day_summary / files 等）；日卡读 `day_summary`
  （含 `day_stats` 列），App 经 BFF 获取。BFF 为只读方（`mode=ro` + `busy_timeout`）。
- **本服务**：数据库写入只落在 `lvdata_inventory.db` 与 `lvdata_index.db`；此外会写
  `derived/` 审计 JSON，并按保留期/删除接口清理过期 video 帧。禁止其他进程与本服务
  同时写同一库。

---

## 3. 配置

### 3.1 加载链

配置主体为 `lvdata_mcp.yaml`（`config.py`）：

1. 路径：`--config` CLI → 环境变量 `LVDATA_MCP_CONFIG` → `configs/<env>/lvdata_mcp.yaml`
   （跟随仓库 `_project_root()` 约定）→ CWD `lvdata_mcp.yaml`；全部缺失即启动失败。
2. 启动时先加载仓库根 `.env`（`setdefault`：shell 注入优先）。
3. 递归解析 `${ENV_VAR}` 占位符；未设置即报错（fail-fast）。
4. 环境变量覆盖任意嵌套键：前缀 `LVDATA_MCP__`，层级用双下划线，例如
   `LVDATA_MCP__WATCHER__POLL_INTERVAL_S=30`。值按现有类型强转（bool/int/float/str）。
5. pydantic 解析 + 启动校验：
   - 绑定非回环地址必须配置 `server.token`；
   - `providers.audio/vision/summary` 的 `api_key` 必须非空；
   - `providers.embedding` 整块为空 = 关闭向量检索（允许离线运行）。

### 3.2 完整示例

```yaml
# 分析产物输出语言（单值；默认 en）。MCP/REST 文案固定英文，不受此值影响。
# 支持 App 16 个 canonical code：
# en, zh-Hans, zh-Hant, ja, ko, ru, de, fr, it, es, pt, hi, id, th, vi, ar
language: en

server:
  host: "127.0.0.1"
  port: 8090
  token: "${LVDATA_MCP_TOKEN}"        # 非回环绑定必填
  log_level: INFO
  max_ip_conn: 32                     # 每 IP 并发请求上限
  log_file:
    enabled: true                     # 仅 serve 生效
    dir: ""                           # 空 = <project_root>/logs
    max_bytes: 52428800               # 50 MB
    backup_count: 3

data:
  root_dir: /opt/lvdata
  db_path: ""                         # 默认 <root>/lvdata_inventory.db
  rag_db_path: ""                     # 默认 <root>/lvdata_index.db
  chat_server_db_path: ""             # 默认 <root>/chat_server.db

watcher:
  poll_interval_s: 60
  summarize_quiet_s: 600              # 日摘要 debounce 静默窗口
  flush_min_events: 10                # flush 门控：待处理事件数阈值
  flush_max_delay_s: 600              # flush 门控：最旧待处理事件时限
  timezone: Asia/Shanghai
  timezones: []                       # 显式 seed 到 timezones 表的时区
  concurrency: 4                      # 分析类 LLM 有界并发
  max_files_per_poll: 200             # 单轮 audio/vision 各处理上限（0 = 不限）
  chatlog_max_bytes_per_round: 1048576
  day_summary_max_per_poll: 10
  llm_timeout_s: 300
  audio_sample_rate: 16000            # 0 = 直传 ogg
  video_window_s: 30
  video_max_frames_per_call: 24
  video_extract_chunk_size: 4
  max_image_size: 1280                # 0 = 不缩放
  max_chars_per_event: 300
  stage_timeout_s: 1800
  audit_retention_days: 14
  analysis_runs_retention_days: 30
  audit_prune_interval_s: 86400
  video_retention_hours: 72           # 运行时下限 24

providers:
  # 全部为 OpenAI-compatible 端点。
  # workspace_id 与业务空间专属域名两值配套、互不派生；仅 DashScope 驱动
  # X-DashScope-WorkSpace 头，未配置/非 DashScope 不带头。
  audio:
    vendor: dashscope
    base_url: "https://${DASHSCOPE_API_HOST}/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: "${DASHSCOPE_WORKSPACE_ID}"
    model: qwen3.8-omni-flash
  vision:
    vendor: dashscope
    base_url: "https://${DASHSCOPE_API_HOST}/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: "${DASHSCOPE_WORKSPACE_ID}"
    model: qwen3.7-flash
  embedding:
    vendor: dashscope
    base_url: "https://${DASHSCOPE_API_HOST}/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: "${DASHSCOPE_WORKSPACE_ID}"
    model: qwen3.7-text-embedding
    dim: 1024
    min_vec_score: 0.50               # semantic_search 向量相似度下限；0 = 关闭
  summary:
    vendor: dashscope
    base_url: "https://${DASHSCOPE_API_HOST}/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: "${DASHSCOPE_WORKSPACE_ID}"
    model: qwen3.7-flash
```

### 3.3 默认值与约束

| 配置 | 默认 | 说明 |
|------|------|------|
| `language` | `en` | 分析产物输出语言；空/非法/未知 → `en` |
| `server.host` / `port` | `127.0.0.1` / `8090` | 非回环绑定必须配 token |
| `server.max_ip_conn` | `32` | 每 IP 并发请求上限 |
| `data.root_dir` | `/opt/lvdata` | 数据根，目录必须存在 |
| `watcher.poll_interval_s` | `60` | 轮询间隔 |
| `watcher.concurrency` | `4` | 分析类 LLM 有界并发 |
| `watcher.max_files_per_poll` | `200` | 单轮 audio/vision 各处理上限 |
| `watcher.flush_min_events` | `10` | 待处理事件数达到即跑 flush 组 |
| `watcher.flush_max_delay_s` | `600` | 最旧待处理事件超时即跑 flush 组 |
| `watcher.stage_timeout_s` | `1800` | work loop 单 stage 超时（超时取消在途协程，状态已落盘） |
| `providers.*.vendor` | `dashscope` | 仅提供 base_url/model 默认值 |
| `providers.embedding.min_vec_score` | `0.50` | 低于该余弦相似度的向量命中不返回；换 embedding 模型须复标 |
| `server.log_file` | 启用，50 MB × 3 | 仅 `serve` 生效；运行态只写文件 |

`vendor` 默认值（`config.py:VENDOR_DEFAULTS`）：

| vendor | 默认 base_url | 默认 model（按能力） |
|--------|---------------|----------------------|
| dashscope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | audio=`qwen3.8-omni-flash`；vision/summary=`qwen3.7-flash`；embedding=`text-embedding-v4` |
| openai | `https://api.openai.com/v1` | 无 |
| openrouter | `https://openrouter.ai/api/v1` | 无 |
| deepseek | `https://api.deepseek.com` | 无 |
| ollama | `http://localhost:11434/v1` | 无 |

显式 `base_url` / `model` 优先于 vendor 默认值。`ProviderConfig` 为
`extra="forbid"`（拼错字段启动即报错）。

### 3.4 CLI

```
lvdata-mcp [--config PATH] [serve]              # 默认子命令
lvdata-mcp --config PATH reindex [--vectors]    # 全量重建 FTS；--vectors 同时重建向量
lvdata-mcp --config PATH eval --golden FILE [--topk N]
lvdata-mcp --config PATH stage {inventory|day_stats} [--force]
```

- `reindex`：数据源 = `events`；FTS 全量重建后写全局水位 `''` 并清会话水位；
  `--vectors` 按会话重建向量并清扫孤儿。
- `eval`：golden queries 的 recall@k（fts / vector / hybrid 三路）。
- `stage`：只允许**零 LLM 用量**的本地 stage：`inventory`（recording_files 全量重同步）
  与 `day_stats`（全量重算日卡派生字段）。分析类全量入口刻意不提供，走 REST
  `POST /api/analysis/trigger` 做增量/force 控制。

---

## 4. 数据模型

### 4.1 时间戳与排序约定

- **水位/内部时间列**：统一 `YYYY-MM-DDTHH:MM:SS.sssZ`（ISO 8601 UTC，毫秒，`Z`），
  字符串比较即时间序。所有参与比较的水位列必须遵守，禁止混用空格格式或 `+00:00`。
- **MCP/REST 输出时间**：`timestamp` 为**带本地偏移的 ISO 8601**，秒精度，例如
  `2026-09-28T18:01:30+08:00`；偏移按该时刻的时区规则计算。`ts_ms=0` / 缺失 → `null`。
- **日期窗**：`start_date` / `end_date` 为 `YYYY-MM-DD`，**含端点**，按请求时区的
  本地日界换算为 epoch ms 闭区间；格式错/逆序 → `invalid_param`。
- **时区**：请求时区为空/非法 → `watcher.timezone`；输出 `timezone` 为真实生效标识
  （IANA 名 / `UTC` / `UTC+08:00`）。
- **同刻排序**：`(ts_ms, EVENT_PRIORITY, event_id)`。优先级：

```
chat 0 < transcribe 1 < translate 2 < window 3 < video 4 < audio 5 < agent_job 6
```

`PRIORITY_SQL` 由 `consts.EVENT_PRIORITY` 单一生成，禁止手写副本。

### 4.2 主库表

#### `events`（SSOT）

```sql
CREATE TABLE events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ref   TEXT NOT NULL UNIQUE,   -- <rsession_id>:<type>:<source_id>
    device_uuid TEXT NOT NULL,
    rsession_id TEXT NOT NULL,
    type        TEXT NOT NULL,          -- chat|transcribe|translate|audio|window|video|agent_job
    ts_ms       INTEGER NOT NULL,
    ts_utc      TEXT,
    source      TEXT NOT NULL,          -- files | chat_messages
    summary     TEXT,                   -- 检索 / LLM 输入文本
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
-- idx_events_rsession_ts(rsession_id, ts_ms)
-- idx_events_ts(ts_ms) / idx_events_type(type) / idx_events_updated(updated_at)
```

- `event_ref` 稳定唯一；重分析 upsert 时刷新 `summary` / `payload` / `updated_at`。
- `summary` 是 FTS、向量与 LLM 输入的共同文本；`payload` 保存完整结构化结果。
- 事件类型与 ref 形态：

| type | event_ref 后缀 | source | payload 要点 |
|------|----------------|--------|--------------|
| `chat` | `chat:<ref_id 清洗值>` | `chat_messages` | `{role, text, chat_session_id}` |
| `transcribe` | `transcribe:<ref_id>` | `chat_messages` | `{role, text, chat_session_id}` |
| `translate` | `translate:<ref_id>` | `chat_messages` | `{role, text, chat_session_id, source}` |
| `agent_job` | `agent_job:<job_key>` | `chat_messages` | `{job_key, agent_name, agent_id, role, text}` |
| `audio` | `audio:<segment_stem>` | `files` | 分析全量 + `segment_id` / `duration_s` + 能量字段 |
| `window` | `window:<window_id>` | `files` | `{window_id, window_start_ms, window_end_ms, frame_count, window_caption, key_frame_count}` |
| `video` | `video:<frame_stem>` | `files` | extract 全量 + `frame_id` / `window_id` |

`ref_id` 入口清洗：`:` → `_`；空值 → `{kind}_{ts_ms}_{role}` 确定性兜底，保证
`event_ref` 恒为三段可解析。

#### `files`

`recording_files` 在 lvdata 侧的镜像与状态机：

```sql
-- 基础列：file_id, rsession_id, file_type, file_ext, file_path UNIQUE,
--         file_size, uuid_ts_ms, uuid_ts_utc, scanned_at
-- 增强列：
device_uuid          TEXT,
analysis_status      TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|failed|na
fail_count           INTEGER NOT NULL DEFAULT 0,
analyzed_at          TEXT,
error                TEXT,
src_updated_at       TEXT,       -- chat server 行的 updated_at（增量判定）
processed_updated_at TEXT,       -- chat 处理水位（= 已消费到的 src_updated_at）
processed_offset     INTEGER     -- chat jsonl 字节偏移（NULL 视同 0）
```

- `file_type`：`audio` / `video` / `meta`（session.json）/ `chat_history` /
  `chat_transcribe` / `chat_translate`。
- `analysis_status`：audio/video 新行 `pending`，成功置 `ok`；chat 新行与成功处理
  后为 `na`；`meta` 恒 `na`。失败一律置 `failed` 并 `fail_count+1`（chat 失败也是如此，
  重试成功后回 `na`）；`fail_count >= 3` 停止取件。
- chat 类文件与 `meta`（session.json）不写 `uuid_ts_ms`（保持 NULL）；该列语义是
  “可分析产物时间戳”。

#### `recording_sessions`

会话元数据底座（session.json 映射）。关键列：`rsession_id`（PK）、`device_uuid`、
`user_uuid`、`session_date`、`root_path`、`started_at`、`analysis_status`、各计数列。
仅用于存储与内部 JOIN，不对外暴露。注意：这是 **lvdata 主库**内的表，由
`session.json` 映射而来；与 `chat_server.db` 里的同名 `recording_sessions` 表是两套
独立数据，本服务不读后者。

#### `recording_files_sync_state`

```sql
CREATE TABLE recording_files_sync_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    last_updated_at TEXT,
    last_file_id    INTEGER,
    last_run_at     TEXT,
    rows_upserted   INTEGER DEFAULT 0
);
```

keyset 水位 `(last_updated_at, last_file_id)`。

#### `analysis_runs`（分析审计）

一次 LLM 调用一行：`file_id`、`kind`（audio|video）、`model`、`status`、
`usage_input_tokens`、`usage_output_tokens`、`latency_ms`、`error`、`started_at`、
`finished_at`。按 `analysis_runs_retention_days` 清理。

#### `day_summary`

```sql
CREATE TABLE day_summary (
    timezone        TEXT NOT NULL,
    local_date      TEXT NOT NULL,
    utc_offset_min  INTEGER NOT NULL,
    day_start_ms    INTEGER NOT NULL,
    day_end_ms      INTEGER NOT NULL,
    day_summary     TEXT NOT NULL DEFAULT '{}',   -- JSON（见下）
    model           TEXT,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending|ok|failed
    event_count     INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    generated_at    TEXT,                          -- 成功=输入快照 T0；失败=尝试时刻
    day_stats       TEXT NOT NULL DEFAULT '{}',    -- day_stats stage 预计算
    PRIMARY KEY (timezone, local_date)
);
-- idx_day_summary_status(status)
```

`day_summary.day_summary` JSON：

```json
{
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "local_date": "2026-08-24",
  "generated_at": "2026-08-25T02:05:11.123Z",
  "model": "qwen3.7-flash",
  "stats": {
    "events": 148,
    "chat_messages": 60,
    "audio_segments": 24,
    "video_frames": 12,
    "video_windows": 8,
    "agent_jobs": 2,
    "audio_segments_total": 30
  },
  "summary": "2-3 句整体总结",
  "moments": ["重要事件/时刻，每条一句，≤10 条"],
  "topics": [{"topic": "主题名", "search_words": "事件摘要中的原文连续片段"}],
  "usage": {"input_tokens": 12345, "output_tokens": 678}
}
```

- `stats` 为本地计算口径；`events` 是**过滤后**进入 LLM 输入的事件数；
  `audio_segments_total` 是当日 audio 事件真实总数（含因 transcript 非空被排除的段）。
- `summary` / `moments` / `topics` 由 LLM 产出；`moments` 目前无消费者，预留。
- `topics[].search_words` 必须是事件摘要中的原文连续片段，不翻译；缺失为 `null`。

#### `day_stats`（`day_summary.day_stats` 列）

```json
{
  "schema_version": 1,
  "computed_at": "2026-09-25T...",
  "counts": {"chat": 12, "audio": 3, "...": 0},
  "hot_slots": [{"start_ms": 0, "end_ms": 0, "count": 0}],
  "images": [{
    "frame_id": "...", "ts_ms": 0, "event_ref": "...",
    "window_id": "...", "device_uuid": "...", "rsession_id": "..."
  }]
}
```

- `counts`：日窗内 events 按 type 原样计数。
- `hot_slots`：仅 `chat/agent_job/video/transcribe/translate`；间隔 > 1h 切段，超过
  3 段时反复合并“相邻间隔最小”的一对（并列取最左）。
- `images`：video 事件按 `window_id ?: event_ref` 分组，各组轮转取帧，最多 15 张，
  按时间升序输出；不存媒体 URL（由 BFF 出口组装）。

#### 发现/水位状态表

| 表 | 键 | 值 | 用途 |
|----|----|----|------|
| `day_scan_state` | `timezone` | `last_scan_at` | 日摘要 dirty 发现游标（写扫描开始时刻） |
| `day_deferred` | `(timezone, local_date)` | `created_at` | ok 日 + debounce 中更新的持久化再发现载体 |
| `day_stats_state` | `timezone` | `last_scan_at` | day_stats 扫描游标（完成时推进） |
| `flush_state` | `id=1` | `last_flush_at` | flush 组水印；“待处理” = `events.updated_at > 水印` |
| `index_state` | `target`（rsession_id / `''`） | `last_reindex_at` | FTS 重建水位；`''` = 全局全量 |

`index_state` 的快照语义：值为重建入口时刻 T0 **减 1ms**，T0 取于取件与任何 events
读取之前。这样同毫秒（读取之后）到达的更新在下轮必判滞后并自愈；全量重建才写 `''`，
且同事务清会话行。

#### `timezones`

```sql
CREATE TABLE timezones (
    timezone   TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    priority   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

启动时把 `watcher.timezones` + `watcher.timezone` `INSERT OR IGNORE` seed 进来。
chat server 在 `session.start` 时直写（归一化后的时区），`updated_at` 即“最近使用”。
本服务每轮只遍历**活跃时区集**：最近使用的 enabled 时区 ∪ 配置默认时区，至多 2 个；
非活跃时区的游标/占位行保留，再激活后继续。

#### `period_summary`（预留）

已建表（`timezone, period, period_start, period_summary, status, model, error,
generated_at`），本期无生成逻辑。周/月/年卡可由 BFF/客户端直接聚合 day_summary 兜底。

#### `mcp_pipeline_runs`（pipeline 审计）

`run_id, target, stage, started_at, finished_at, status, exit_code, error`。
`target` 当前只会是 `rsession_id` 或 `NULL`（`day_summary` / `day_stats` 忽略 target）；
按 `audit_retention_days` 清理，running 僵尸行按 `max(2 × stage_timeout_s, 3600s)` 裁。

#### `search_docs`（FTS5）

```sql
CREATE VIRTUAL TABLE search_docs USING fts5(
    text, orig_text UNINDEXED, event_ref UNINDEXED, session_id UNINDEXED,
    device_uuid UNINDEXED, type UNINDEXED, ts_ms UNINDEXED
);
```

- 数据源 = `events.summary`；`text` 为 CJK bigram 预切分后的检索文本，`orig_text` 为原文。
- CJK 连续串切为重叠 bigram；非 CJK 词保持整词。索引与查询两侧使用同一函数。
- 查询串每个词用双引号包裹（CJK 先 bigram），避免 FTS5 语法歧义；代价是不支持
  FTS5 原生通配与布尔语法。

### 4.3 向量库 `lvdata_index.db`

```sql
CREATE VIRTUAL TABLE vec_chunks USING vec0(
    embedding float[dim] distance_metric=cosine,   -- dim = providers.embedding.dim（默认 1024）
    chunk_id TEXT, event_ref TEXT, session_id TEXT, device_uuid TEXT,
    type TEXT, ts_ms INTEGER, text TEXT
);
CREATE TABLE vec_state (
    event_ref TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL       -- 已嵌入的 events.updated_at
);
```

- chunk 粒度 = event，`chunk_id = event_ref`，文本 = `events.summary`（空文本不产 chunk）。
- `vec_state` 按 `event_ref` 记录已嵌入内容版本；`updated_at` 不一致或缺失 = 待重嵌；
  事件消失 = 孤儿，清 chunk + state。
- embedding 批大小 10（百炼端点实测约束）；查询向量有进程内 LRU 缓存（最多 500 条，
  `array('d')` 保持 float64 逐位一致，失败不缓存）。

### 4.4 历史遗留（不读不写）

v1 的 `audio_analyses`、`video_analyses`、`chat_messages`、`agent_jobs`、
`agent_jobs_sync_state`、`day_force`、`mcp_watch_state` 等表不再参与当前链路：
`chat_messages` / `day_force` 由启动迁移删除；`agent_jobs` 仅在存量库存在时做改名/
时间戳归一化，不读取。agent job 事件由 chat 日志铸造（见 5.3）。

---

## 5. Ingestion 平面

### 5.1 Watcher 调度

#### poll loop

每 `poll_interval_s`：

1. 重置 audio/vision 的单轮文件预算（`max_files_per_poll`）。
2. 执行 `inventory` stage：同步 `recording_files` 增量。
3. 入队一个 **round 标记**（无数据；重复标记合并）。
4. 标记 `first_poll_done`（`/readyz` 从此返回 200）。

#### work loop

消费 round，固定序列：

```
常跑组：inventory → chatlog → audio → video → day_stats → reindex
flush 组：embed → day_summary（门控执行）
```

- 常跑组每轮执行；`day_stats` / `reindex` 为本地零 LLM 成本，不节流。
- **flush 门控**（常跑组结束后评估，任一条件即执行）：
  1. 待处理事件数 `events.updated_at > flush_state.last_flush_at` ≥ `flush_min_events`；
  2. 最旧待处理事件年龄 ≥ `flush_max_delay_s`；
  3. `day_summary.has_actionable_work()`（存在可生成/可清理的日）；
  4. 手动 trigger 且 force。
  查询失败时宁跑（安全侧）。
- flush 组全部成功且为全局（`target=None`）才推进 `flush_state` 水印；会话限定的
  force 重嵌不推进全局水印。
- work loop 中的每个 stage 受 `stage_timeout_s` 约束（poll loop 内的 inventory 同步
  不套该超时）；超时取消在途协程，中间态已落盘，下轮续跑。

#### prune loop

启动后立即执行一次，之后每 `audit_prune_interval_s`：

- `mcp_pipeline_runs`：删除保留期外的非 running 行；running 僵尸行按
  `max(2 × stage_timeout_s, 3600s)` 删除；`audit_retention_days > 0` 时同时清理
  events 已消失的 `index_state` 会话行（`''` 恒留）。
- `analysis_runs`：删除 `started_at` 早于保留期的行（NULL 不清理）。
- video 帧保留：删除超过 `max(24, video_retention_hours)` 小时、状态 `ok` 且**没有**
  对应 video 事件的帧文件与 `files` 行；路径越出 root 跳过；先删文件后删行，
  中途崩溃可自愈。

#### 手动触发

`POST /api/analysis/trigger`：

```json
{"session_id": "<uuid?>", "stages": ["audio", "video"], "force": false}
```

- `stages` **只在 `force=true` 时生效**：按 stage 累积到下一轮。不带 force 时该参数
  不改变执行范围——round 仍执行常跑组全链，flush 组按门控决定。`session_id` 仅作
  force 的作用域限定（`inventory` / `day_stats` 不支持会话限定）。
- force 语义：
  - `inventory`：忽略水位全量重同步；
  - `chatlog`：重置 offset / 水位 / fail_count 后整文件重放（带 session_id 时限定会话）；
  - `audio` / `video`：重置 fail_count 与状态后重跑（带 session_id 时限定会话）；
  - `embed` / `reindex`：全量重建（**忽略 session_id**）；
  - `day_summary`：重置非 ok 行并重生成；force 时连 ok 行也重生成（用于语言迁移；
    忽略 target）；
  - `day_stats`：全量重算（忽略 target）。
- flush 组被强制执行的条件是：force 命中了 `embed` / `day_summary` 本身，或门控命中。
- 入队幂等；`session_id` 不存在 → 404。

### 5.2 recording_files 同步（`recording_watch.py`）

1. ATTACH `chat_server.db` 为只读 `cs`；DB 或表缺失 → 空转。
2. 读取 `recording_files_sync_state` 水位；`force` 时重置为 `('', 0)`。
3. 单事务内按 keyset 分页拉取：
   `WHERE (updated_at, file_id) > (?, ?) ORDER BY updated_at, file_id LIMIT 5000`。
4. 逐行映射（`kind` → `file_type`）：

| cs kind | lvdata file_type | 初始状态 |
|---------|------------------|----------|
| `session` | `meta` | `na` |
| `chat_history` | `chat_history` | `na` |
| `chat_transcribe` | `chat_transcribe` | `na` |
| `chat_translate` | `chat_translate` | `na` |
| `audio` | `audio` | `pending` |
| `video` | `video` | `pending` |

未知 kind 跳过并告警。

5. `files` upsert（`ON CONFLICT(file_path)`）：只更新 `file_size` / `scanned_at` /
   `src_updated_at` / 补齐 `device_uuid`；**不覆盖**分析状态、fail_count、error、
   `processed_offset`、`processed_updated_at`。
6. `kind=session`：读 `session.json` → `INSERT OR REPLACE recording_sessions`
   （静态字段；存量旧文件的动态字段照常摄入但不消费）。
7. 保证 `files.rsession_id` 的 `recording_sessions` 兜底行存在（FK + 下游 JOIN）。
8. 提交事务；有变更时推进水位。崩溃整体回滚，下轮按旧水位重来（upsert 幂等）。

### 5.3 chatlog pass（`analysis/chatlog.py`）

**取件**：`file_type IN (chat_history, chat_transcribe, chat_translate)`、
`fail_count < 3`、`processed_updated_at IS NULL OR src_updated_at > processed_updated_at`，
按 `file_id` 顺序处理；可按 `session_id` 限定（force 场景）。

**读取**：从 `processed_offset` 开始，单轮最多 `chatlog_max_bytes_per_round` 字节；
只处理完整行：

- 尾部半行不推进 offset，下轮继续；
- cap 内无换行则继续读到换行或 8 MB 硬上限；硬上限内仍无换行 → 判坏行，跳过该块
  并推进 offset（不推进 `processed_updated_at`）；
- 文件变小（重写）→ offset 归零全量重读。

**事件映射**：

| 来源 | type | event_ref 后缀 | 说明 |
|------|------|----------------|------|
| `history.jsonl`，`source != agent_job` | `chat` | `chat:<ref_id>` | 普通聊天回合 |
| `history.jsonl`，`source == agent_job` | `agent_job` | `agent_job:<job_key or ref_id>` | agent 任务事件 |
| `transcribe.jsonl` | `transcribe` | `transcribe:<ref_id>` | 实时转写 |
| `translate.jsonl` | `translate` | `translate:<ref_id>` | 实时翻译 |

- 三份 jsonl 共用行 schema：`ts / utc / role / text / ref_id / chat_session_id`，
  可选 `source / images / interrupted / agent_name / agent_id / job_key`。
- `summary` = 行文本；`ts_ms` 缺失按 0。
- **幂等**：`upsert_event_if_changed` 仅在 `summary` 变化时刷新 `updated_at`，避免
  重复读同一文件导致 day_summary 反复重生成。
- 成功处理到文件末尾才推进 `processed_updated_at`；未读完只推进 offset。
- 状态列成功保持 `na`；失败 `fail_count+1`。

> agent job 事件来自 chat 历史中的 `source=agent_job` 行，不再从 `agent_jobs` 表同步，
> 也不再有未关联 job 的查询入口。

### 5.4 音频分析（`analysis/audio.py`）

**取批**：`files.file_type='audio' AND analysis_status IN ('pending','failed') AND
fail_count < 3`，JOIN `recording_sessions` 取设备与段长；按 `uuid_ts_ms` 排序；
受单轮预算 `max_files_per_poll` 限制；`failed` 按 `fail_count` 退避（60s / 300s）；
`session_id` 仅在显式 target 时过滤。

**处理**：

1. 文件 `< 1024` 字节视为空分片：不调 API，写审计 JSON（`silent=true`），状态置 `ok`。
2. 音频加载（`asyncio.to_thread`）：
   - `audio_sample_rate > 0`：ffmpeg 按该采样率转单声道 wav（默认 16k）；
   - `0`：直传 ogg；若 API 返回 400，降级转 wav 重试一次。
3. 调用 `providers.audio`（OpenAI-compatible，streaming + `json_object`）：
   - system prompt 规定 JSON schema：
     `language / transcript / summary / value / emotions / objects / people`；
   - `value ∈ {low, high}`；`people` 为统计对象；
   - 输出语言：`summary / emotions[].evidence / objects[].name /
     objects[].description / people.description` 使用配置语言；`transcript` 保留原语言。
4. 思考控制：显式 `enable_thinking=False`；模型 400 拒收时降级为不传该参数并告警。
5. schema 缺失键重试；重试耗尽 → 该文件失败（不会以归一化结果落库）。归一化
   （缺失字段给默认值、`value` 非法 → `low`）只在 schema 完整时用于落库。
6. **静音判定**：`transcript` 词数（CJK 逐字 + 拉丁词）> 5 **或** `value=high` 才落
   event；否则不写 events（审计 JSON 照写，`silent=true`）。
7. **事件**：
   - ref = `<rsession_id>:audio:<segment_stem>`；`ts_ms = files.uuid_ts_ms`；
   - `summary` = `transcript + summary + emotions[].emotion`；
   - `payload` = 分析结果全量 + `segment_id` / `duration_s` + 能量包络字段
     （`energy` base64、`energy_v`、`frame_ms`、`db_min`、`db_max`）。
8. **能量包络**：仅非静音段计算；20ms 帧 RMS → dBFS，量化到 uint8 后 base64；
   失败不影响主流程。
9. `files` 状态：成功（含静音）→ `ok`；失败 → `failed` + `fail_count+1`。
   `analysis_runs` 记录每次调用（usage/latency）。

### 5.5 视频/图像分析（`analysis/vision.py`）

**取批**：`file_type='video'`、pending/failed、`fail_count < 3`；按设备分组后组内
按 `uuid_ts_ms` 时间窗（`video_window_s`）分组，单窗最多 `video_max_frames_per_call` 帧。

- 多设备并发录制时**按 device_uuid 分组**，避免不同设备的帧混入同一次 filter 调用。
- session 不参与分组：跨 session 的连续帧可合并成同一窗口；窗口/审计产物归属首帧
  所属 session。
- `window_id` = 首帧文件名 stem（确定性，可续跑）。
- 预算按整窗消费；批尾预留 `max_frames_per_call - 1` 帧余量，避免窗口被批界劈开。
- 窗口原子性：整窗（filter + 全部关键帧 extract）完成才把该窗所有文件置 `ok`；
  任一失败 → 整窗所有文件 `failed` + `fail_count+1`，下轮整窗重跑（幂等）。

**第一阶段 filter**：

- 单次多图调用，图片以 `frame 0/1/...` 文本锚点标记；长边超过 `max_image_size`
  时 Pillow LANCZOS 缩放；JPEG base64 data URL。
- 输出 schema：

```json
{
  "window_caption": "该时间窗口内容的一句话概括",
  "frames": [{
    "frame_id": "用户提供的图片索引",
    "quality": "low|high",
    "scene_id": "本帧所属场景的整数编号",
    "key_frame": "是否为该场景选出的代表帧",
    "reason": "一句话理由"
  }]
}
```

- **归一化守卫**：
  - `quality` 非法 → `low`；
  - `scene_id` 非法/负值 → `null`（本帧独立场景）；
  - `key_frame=true` 且 `quality=low` → 强制 `false`（存储中不存在 key ∧ low）。
- **场景级抑制**：同窗同一 `scene_id` 若有多个 key frame，只保留帧索引最小者，
  其余强制降级并写 warning log。
- `frame_id` 容忍模型回显 `"frame N"`/`"00"` 等形态；编造索引或缺索引 → schema
  违规重试。

**第二阶段 extract**：

- 关键帧按 `video_extract_chunk_size` 分 chunk（默认 4 帧/次）。
- 输出 schema：`caption / place / objects / ocr / people`；`place` 不确定或为屏幕
  截图 → `null`；`ocr` 提取显著可见文字（每条 ≤80 字，最多 15 条）。
- 归一化：非字符串 `caption` → `""`；`place` 的空串/`"null"`/`"none"` → `null`；
  数组字段非法 → `[]`。

**事件**：

- 关键帧集为空 → 该窗口**不产任何事件**（窗口审计 JSON 与 `analysis_runs` 照写，
  不写逐帧审计 JSON）。
- 否则：
  - `window` 事件：ref `<rsid>:window:<window_id>`，`ts_ms = window_start_ms`，
    `summary = window_caption`；
  - 每个关键帧一个 `video` 事件：ref `<rsid>:video:<frame_stem>`，
    `ts_ms = uuid_ts_ms`，`summary = caption + place + ocr[].text`，
    `payload = extract 结果 + frame_id + window_id`。

**审计 JSON**：`derived/<device>/<session>/video/windows/<window_id>.json` 与
`video/frames/<frame_id>.json`，仅人工核查。

### 5.6 日摘要（`summary/day.py`）

**发现（discover）**——在 flush 组的 day_summary stage 内运行：

对每个活跃时区：

1. 读 `day_scan_state.last_scan_at`（无行按 `''`）。
2. 候选 = `events.updated_at > 游标` 的事件。
3. 逐日核对：该日无 `day_summary` 行，或 `events.updated_at > generated_at` → dirty。
4. 失败/占位重试：`status IN ('failed','pending') AND fail_count < 3`；failed 按
   `fail_count` 退避（60/300s），pending 立即重试。fail_count ≥ 3 不再进入此扫描；
   若之后有新事件使该日重新 dirty，则由生成条件按 900s 档退避再试。
5. `day_deferred` 兜底：ok 行 + debounce 中的更新不再出现在候选事件里，通过该表
   持久化再发现；已生成且无更新则删除。
6. 无事件的 dirty 日：删除其非 ok 行与 deferred 行（避免永久忙循环）。
7. dirty 日按日期倒序（最近日优先）；先落 `pending` 占位行（崩溃恢复 + 批限溢出），
   再判断是否可入队：
   - **debounce**：该日最新事件年龄 ≥ `summarize_quiet_s`；
   - failed 行需退避期满；
   - 满足则入队（单轮上限 `day_summary_max_per_poll`），否则写入 `day_deferred`。
8. 游标推进到**本轮扫描开始时刻**（扫描期间并发落入的事件下轮必见）。

**生成（generate）**：

1. 取输入快照时刻 `T0 = now()`。
2. 输入 = 该本地日 events，`updated_at <= T0`，按
   `(ts_ms, EVENT_PRIORITY, event_id)` 排序。
3. **输入去重**：`audio` 事件且 `payload.transcript` 非空 → 排除（文字已由
   chatlog 事件覆盖）。过滤同时作用于 LLM 输入、`stats` 与 `event_count`；但
   `audio_segments_total` 记录当日 audio 真实总数。
4. 无事件（过滤后）→ 删除 pending 占位行，不落行。
5. 幂等护栏：若已有 ok 行且所有输入 `updated_at <= generated_at` → 跳过。
6. 渲染 LLM 输入：每事件 `[HH:MM] label: summary`，`summary` 截断
   `max_chars_per_event`；label：chat → `chat(<role>)`，window/video →
   `video window` / `video frame`，其余原样；多设备日按设备分段（段头
   `设备 <uuid8>：`），单设备日不分段。
7. `providers.summary` 调用（`enable_thinking=False`，`json_object`）；必需键
   `summary / events / topics`。
8. 落库：`generated_at = T0`（快照语义）；`utc_offset_min` / `day_start_ms` /
   `day_end_ms` 按该本地日计算；`stats` 本地计算；`moments` = LLM 输出 `events`
   的前 10 条；`topics` 归一化为 `[{topic, search_words}]`；删除该日
   `day_deferred` 行。
9. 失败：`status='failed'` + `fail_count+1`，`generated_at = T0`（重试水位）。

**并发**：多日并发生成，受 `watcher.concurrency` 限制。

### 5.7 日卡派生字段（`summary/day_stats.py`）

**目的**：把 BFF 日卡需要的 `counts / hot_slots / images` 预计算进
`day_summary.day_stats`，使 overview 单表可读。**无 LLM、无外部调用**。

- 目标时区 = 活跃时区集合（与 day_summary 同一判定）。
- 游标 `day_stats_state.last_scan_at`：候选 = `events.updated_at > 游标`；日窗查询
  带 `updated_at <= T0` 上界；完成才推进游标。
- 行已存在：只 `UPDATE day_stats`，不碰 status/doc/model/generated_at/fail_count。
- 行不存在且事件全部是“被日摘要去重的 audio” → 不建行（否则 day_summary 的
  no_events 分支会删除该占位行，日卡消失）。
- 否则 `INSERT OR IGNORE` pending 占位行并写入 `day_stats`。
- `target` 一律忽略（始终全局）。

### 5.8 FTS 与向量索引

**FTS（`index.py`）**：

- 数据源 = `events.summary`；按 session 增量重建，或全量重建。
- 重建水位写 `index_state`：T0 快照 − 1ms；增量逐个会话写，全量写 `''` 并清会话行。
- 滞后判定 `lag_sessions`：会话内 `MAX(events.updated_at)` 晚于其会话水位（或全局
  `''` 水位）即滞后；无水位行视为全部滞后。

**向量（`vec.py`）**：

- `sync_changed`：比较 `events.updated_at` 与 `vec_state.updated_at`；变化或缺失的
  事件重嵌；事件消失 → 清 chunk + state；空文本事件只标 state 不产 chunk。
- 嵌入先于删除执行，瞬时失败不丢旧 chunk。
- `rebuild_session` / `rebuild_all` 用于 force / reindex --vectors；全量后清扫孤儿。
- 查询时 `vec0` KNN 支持 session / type / `ts_ms` 范围约束（先筛后取 k，日期窗不丢
  召回），返回 `score = 1 - cosine_distance`。
- **混合检索**：FTS bm25 与向量余弦两路，`RRF(k=60)` 融合；`min_vec_score` 只过滤
  向量路（保留腿内原始名次），FTS 命中不受影响。
- `with_context=true`：按命中事件取同 session ±2min 的最多 10 条邻域，输出与列表
  工具同形的轻量投影。

### 5.9 事件删除（`remove.py`）

`POST /api/events/remove`，body `{"event_refs": ["..."]}`（1..50 条，去重保序）。
目前只支持 `video` 事件，部分成功语义：

1. **T1 主事务**（同库原子）：
   - 删除 video 事件；
   - 若某 `window_id` 下已无剩余 video 事件，连带删除对应 window 事件；
   - 删除 `search_docs` 中相关 ref；
   - 对所有在 `day_summary` 中存在行的时区，重算受影响日的 `day_stats`
     （日卡保留：ok 行只更新派生字段；非 ok 且已删空 → 删行）。
2. **T2 向量**：`vec_index.remove_refs`；异常吞掉并返回 `vectors_pending=true`，
   残留由下轮 `sync_changed` 孤儿清扫自愈。
3. **文件面**：先删 jpg 再删 `files` 行；失败帧计入 `images_pending`，由 video prune
   自愈。

响应字段：`removed`、`window_events_removed`、`images_removed`、`images_pending`、
`days_recomputed`、`skipped[{event_ref, reason}]`，必要时 `vectors_pending`。

---

## 6. Query 平面

### 6.1 MCP server 装配

- `FastMCP("lvdata", stateless_http=True, json_response=True)`，挂 `/mcp`。
- `serverInfo.version` = `lvdata_mcp.VERSION`（包版本；`/api/status` 的
  `service.version` 同源）。
- `initialize` 返回固定英文 `instructions`（每个 agent 的常驻上下文，改动需评审）：

```
LivaChat's recorded multimodal lifelog data. Read-only.
Locate with get_recent_events / get_timeline; search with search (fast, preferred) /
semantic_search (alternative wording); fetch details with get_event by event_ref.
Conventions: start_date/end_date are YYYY-MM-DD inclusive; timezone is the timezone of
the current query; list/search results are lightweight projections; fetch details and
keyframe images with get_event by event_ref; failures return error{code,message}
(do not rely only on isError).
```

- 5 个工具全部只读，`annotations={"readOnlyHint": True}`；`tools/list` 恒为 5 个，
  无配置开关。
- MCP/REST 文案统一英文，不随 `language` 变化。

### 6.2 公共语义

- 日期窗：`start_date` / `end_date` 含端点；缺省 = 不限；逆序/格式错 → `invalid_param`。
- `timezone`：缺省或无效 → 服务配置时区；响应的 `timezone` 为真实生效标识。
- 列表/时间线结果一律**轻量投影**（不含 payload）：
  `get_timeline` / `get_recent_events` / `semantic_search.context_events` 为
  `{event_ref, type, timestamp, source, summary}`，`summary` 截断 256 字；
  `search` / `semantic_search` 命中为
  `{event_ref, type, timestamp, snippet, text}`。详情按 `event_ref` 调 `get_event`。
- `ts_ms=0` / 无时间戳 → `timestamp: null`。
- 工具错误以返回体中的 `error` 字段表达（不依赖 `isError`）：
  `{"error": {"code": "...", "message": "...", "hint?": "..."}}`。

### 6.3 工具契约

#### `search`

```python
search(query: str, type: EventType | None = None,
       start_date: str | None = None, end_date: str | None = None,
       limit: int = 10, timezone: str | None = None) -> dict
```

- FTS5 全文检索；查询串经 CJK bigram 与引号包裹；纯停用词/纯分隔符 → `invalid_param`。
- 输出：

```json
{
  "query": "...",
  "timezone": "Asia/Shanghai",
  "searched_types": ["chat", "transcribe", "translate", "window", "video", "audio", "agent_job"],
  "hits": [{
    "event_ref": "...", "type": "chat",
    "timestamp": "2026-09-28T18:01:30+08:00",
    "snippet": "bigram 高亮片段",
    "text": "原文前 200 字",
    "score": 1.2345
  }]
}
```

- `score` = `-bm25`；索引缺失 → `index_not_built`（带 hint）。

#### `semantic_search`

```python
semantic_search(query: str, type: EventType | None = None,
                start_date: str | None = None, end_date: str | None = None,
                limit: int = 10, timezone: str | None = None,
                with_context: bool = False) -> dict
```

- FTS + 向量 RRF 融合；两路各取 `limit × 3` 召回，日期窗同时下推两路。
- embedding 未配置 → `embedding_disabled`；embedding 调用失败 → `embedding_failed`
  （不静默回退）；FTS 路失败 → 降级为纯向量并告警。
- 向量相似度低于 `providers.embedding.min_vec_score` 的命中不返回；发生丢弃时响应带：

```json
"filtered": {"min_score": 0.5, "best_dropped": 0.42}
```

- 输出：

```json
{
  "query": "...", "timezone": "...", "mode": "hybrid",
  "searched_types": ["..."],
  "filtered": {"min_score": 0.5, "best_dropped": 0.42},
  "hits": [{
    "event_ref": "...", "type": "audio",
    "timestamp": "...", "score": 0.032,
    "via": ["fts", "vec"],
    "snippet": "...", "text": "...",
    "vec_score": 0.83, "fts_score": 4.21,
    "context_events": [{"event_ref": "...", "type": "...", "timestamp": "...",
                        "source": "...", "summary": "..."}]
  }]
}
```

- `via` 顺序为 `fts`、`vec`；`vec_score` 仅该路命中时出现，`fts_score` 同理。
- `context_events` 仅在 `with_context=true` 时出现，为命中事件同 session ±2min 的
  轻量投影（含命中自身，最多 10 条，时间升序）。

#### `get_timeline`

```python
get_timeline(start_date: str | None = None, end_date: str | None = None,
             types: list[EventType] | None = None, limit: int = 200,
             cursor: str | None = None, timezone: str | None = None) -> dict
```

- 直查 events；`types` 显式空数组 → `invalid_param`（不传 = 全部类型）。
- 按日分组：**日升序、日内升序**；无事件的日不出现；分页可能把同一天切成两页。
- 游标为 `[ts_ms, type, event_id]` 三元组的 base64；`next_cursor` 仅在还有更多时返回。
- `total` = 窗口内事件总数（不随翻页变化）。
- 输出：

```json
{
  "timezone": "...", "date_from": "2026-09-28", "date_to": "2026-09-29",
  "total": 1234, "next_cursor": "base64...",
  "days": [{"date": "2026-09-28",
            "events": [{"event_ref": "...", "type": "chat",
                        "timestamp": "...", "source": "chat_messages",
                        "summary": "..."}]}]
}
```

#### `get_event`

```python
get_event(event_ref: str, timezone: str | None = None) -> CallToolResult
```

- `event_ref` 必须为 `{session_id}:{type}:{source_id}`。
- 返回 `[TextContent(JSON), ImageContent?]`：
  - JSON：`{event_ref, type, timestamp, timezone, detail, related}`；
  - `detail` = payload 反序列化后剔除能量包络字段（`energy/energy_v/frame_ms/
    db_min/db_max`）；
  - video 事件带 `related = {window_id, image_available}`；关键帧文件存在时追加
    JPEG image content；图片有无与 `image_available` 同源判定；
  - 事件不存在 → `not_found`（带 hint：chat 需要 chatlog 处理，音视频需要分析完成）。

#### `get_recent_events`

```python
get_recent_events(days: int = 1, timezone: str | None = None) -> dict
```

- `days` 1..31；窗口 = 今天起回溯 N 个本地日（含今天）。
- 内部页大小 200；按 `ts_ms DESC, event_id DESC` 取最新，**日倒序、日内正序**。
- 输出：

```json
{
  "timezone": "...", "date_from": "...", "date_to": "...",
  "total": 1234, "truncated": true,
  "days": [{"date": "2026-09-28", "events": [{"event_ref": "...", "type": "...",
            "timestamp": "...", "source": "...", "summary": "..."}]}]
}
```

- `truncated = total > len(events)`；被截掉的总是更早的事件。

### 6.4 REST 端点

同一进程、同一 bearer token；`/mcp` 与 `/api` 均需鉴权（配置了 token 时），
`/healthz` / `/readyz` 免鉴权免限流。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 服务与数据新鲜度；DB 未初始化 → 503 |
| `/api/analysis/trigger` | POST | 手动触发 pipeline，幂等入队 |
| `/api/events/remove` | POST | 删除 video 事件（同步执行，`asyncio.to_thread` 卸载） |

`GET /api/status` 主要字段：

- `service`：`uptime_s`、`version`；
- `watcher`：`last_poll_at`、`poll_interval_s`、`pipeline_running`、`started`、
  `round_pending`、`pending_files`、`last_flush_at`、`sync{sync_lag_s, chat_backlog}`；
- `data`：`sessions_total`、`summaries_ok`；
  `index{docs, last_rebuild_at, lag_sessions}`；
  `analysis{pending_files{audio,video}, failed_files{audio,video},
  day_summary{pending,failed}, last_day_summary_at}`；
- `recent_runs`：最近 10 条 pipeline run；
- `errors`：失败 run；
- `cache.query_embeddings{size, hits, misses}`：仅在配置 embedding 时出现。

### 6.5 鉴权、限流与探活

- **Bearer**：`Authorization: Bearer <token>`；仅 `/mcp` 与 `/api` 路径校验。
- **限流**：每 IP 并发请求数上限 `server.max_ip_conn`（默认 32）；超限 429。
- **访问日志**：`/mcp` 与 `/api` 记录 path/method/status/tool/ip/latency；
  ≥400 或 >1s 升 warning。
- **`/healthz`**：liveness，恒 `{"status":"ok"}`。
- **`/readyz`**：DB 未初始化或 watcher 首轮 poll 未完成 → 503；否则 200。
- 错误体统一 `{"error": {"code", "message", ...}}`，REST 同时使用 HTTP 语义码：
  `invalid_param` 400、`not_found` 404、`unauthorized` 401、
  `too_many_requests` 429、`watcher_not_running` / `database_not_initialized` 503、
  其余 500。

### 6.6 LLM 客户端行为

`providers.LlmClient`（所有分析与摘要共用）：

- OpenAI-compatible，streaming，`response_format={"type":"json_object"}`；
- DashScope 额外发送 `modalities=["text"]`；`workspace_id` 驱动
  `X-DashScope-WorkSpace` 头；
- 可重试错误：429 / 超时 / 连接 / 5xx / SSE 内嵌 5xx → 指数退避
  `2^n + rand(0,1)`，最多 3 次；
- 不可重试的 4xx → `ApiError(status)`，由调用方决定降级（ogg→wav、
  `enable_thinking` 降级）或失败；
- 模型输出先经 `sanitize_json` 去 markdown fence，再解析为 JSON object。

---

## 7. 多语言

- 顶层 `language` 控制**分析产物**的输出语言（音频 summary、视频 caption/ocr 位置、
  日摘要等），默认 `en`；支持 16 个 canonical code：
  `en, zh-Hans, zh-Hant, ja, ko, ru, de, fr, it, es, pt, hi, id, th, vi, ar`；
  空/非法/未知回落 `en` 并告警。
- 转写（`transcript`）与 OCR 文字（`ocr[].text`）保留原文，不翻译。
- MCP `instructions`、工具描述、REST/错误文案固定英文，不随 `language` 变化。
- `day_summary` 的 `topics[].search_words` 必须保持事件摘要原文，不翻译。

---

## 8. 可靠性与并发

| 场景 | 行为 |
|------|------|
| LLM 429 / 超时 / 连接 / 5xx | 调用内指数退避，最多 3 次；耗尽后文件失败 |
| 分析 schema 违规 / 坏 JSON | 重试；耗尽 → 失败（不以归一化结果落库） |
| 音频空分片 | 直接判空，状态 `ok`，不调 API |
| ogg 直传 400 | 转 wav 降级重试一次 |
| `enable_thinking` 400 | 降级为不传该参数并告警 |
| 文件失败 | `fail_count+1`；fail_count=1/2 分别退避 60s/300s；≥3 停止取件；force 重置 |
| day_summary 失败 | `status='failed'` + `fail_count+1` + 尝试时刻；按 60/300/900s 退避；≥3 后不再由失败兜底扫描重试（新事件到达且 900s 退避期满仍可触发） |
| 进程崩溃 | 文件状态机/水位/fail_count/`day_scan_state`/`day_deferred`/`flush_state` 全在 DB，重启续跑 |
| stage 超时 | 取消在途协程；中间态已落盘，下轮续跑；产生僵尸 running 行由 prune 清理 |
| 事件乱序/迟到/重分析 | `updated_at` 快照水位覆盖；同毫秒边界由 T0−1ms 水位自愈 |
| 索引表缺失 | 查询返回 `index_not_built`；`semantic_search` 的 FTS 路降级为纯向量 |
| 媒体文件缺失 | `get_event` 不报错，仅无 image content、`image_available=false` |
| 路径越界 | 查询/删除/清理一律跳过并告警（防 `../` 穿越） |

并发边界：

- 分析类 LLM 调用与日摘要生成受 `watcher.concurrency` 限制；
- audio 与 vision 各自的单轮预算独立：每轮各限 `max_files_per_poll`（跨 session 共享）；
- 写入使用短事务 + WAL；只读连接 `mode=ro` + `busy_timeout=5000`。

---

## 9. 部署与运维

### 9.1 前置条件

| 项 | 说明 |
|----|------|
| Python | ≥ 3.12 |
| 数据根 | `data.root_dir` 必须已存在，可为空目录；首次启动自动建表并经 chat server 同步建档 |
| chat server DB | 与 lvdata-mcp 同机/共享卷；`recording_files` 为摄入事实源 |
| 百炼凭证 | `DASHSCOPE_API_KEY` + `DASHSCOPE_API_HOST` + `DASHSCOPE_WORKSPACE_ID`（audio/vision/summary/embedding） |
| ffmpeg | 音频转码需要（`audio_sample_rate: 0` 直传 ogg 时不需要） |
| 可选依赖 | `sqlite-vec`（向量检索必需）、`Pillow`（图片缩放） |

### 9.2 启动与验证

```bash
lvdata-mcp serve
curl -s http://127.0.0.1:8090/healthz        # {"status":"ok"}
curl -s http://127.0.0.1:8090/readyz         # 首轮 poll 完成前 503
```

### 9.3 日志

- structlog → JSON 行；`serve` 运行态只写文件（`server.log_file`，默认
  `<project_root>/logs/lvdata_mcp.log`，50MB × 3 轮转）；CLI/测试输出 stdout。
- 启动完成打印一行 `server started: listening address: <host>:<port>` 到 stdout。
- 启动日志只打印非敏感生效值：`base_url` / `api_key` / `workspace_id` 不打。

### 9.4 部署形态

**systemd**（`deploy/lvdata-mcp.service`）：`--config` 指向部署环境 yaml；凭证经
环境变量注入。

**Docker**（`deploy/Dockerfile`）：挂载数据根，注入凭证与 token，映射端口；对外暴露
时 `server.host: "0.0.0.0"` + 必填 token，前置 TLS 终结。

### 9.5 重建与回填

- `lvdata-mcp reindex [--vectors]`：FTS / 向量全量重建（数据源 = events）。
- `python -m lvdata_mcp.rebuild_dev --dry-run / --yes / --verify`：开发环境全链重建
  （备份清库 → chat server backfill → 重启 watcher 重建 → 核数）；破坏性操作需 `--yes`。
- `lvdata-mcp stage inventory --force`：recording_files 全量重同步。
- `lvdata-mcp stage day_stats --force`：日卡派生字段全量重算（回填入口）。

---

## 10. 测试

```bash
.venv/bin/python -m pytest tools/lvdata-mcp/tests/ -q
```

覆盖范围：配置加载/校验、DDL/迁移、recording_files 同步、chatlog 增量与坏行、
audio/vision 分析（含降级、静默、schema 归一化、场景抑制）、day_summary 调度与
快照语义、day_stats 口径、FTS/向量/RRF/缓存、5 个工具契约、REST 端点、错误模型、
保留期清理、事件删除、语言回退、日志。

`tests/fixtures/day_stats_golden.json` 锁定日卡派生口径（与 BFF 原实现逐字等价），
`golden_queries.json` 用于 `eval` 的 recall 回归。

---

## 11. 代码地图

```
tools/lvdata-mcp/src/lvdata_mcp/
  main.py              入口：serve / reindex / eval / stage；日志与建表
  config.py            yaml 加载、占位符、env override、pydantic schema、安全校验
  server.py            Starlette 装配：/mcp + /api + 探活 + 鉴权 + 限流 + 访问日志
  tools.py             FastMCP 5 个只读工具定义
  schemas.py           工具入参 pydantic 模型
  store.py             只读查询（events / day_summary / files / status）
  ddl.py               建表、幂等改名迁移、时间戳归一化
  pipeline.py          stage 编排、mcp_pipeline_runs、PollBudget
  watcher.py           poll/work/prune 三个 loop、flush 门控、保留期清理
  recording_watch.py   recording_files 增量同步、session.json 映射
  providers.py         OpenAI-compatible LLM 客户端、重试、JSON 解析
  index.py             FTS5（CJK bigram）、水位、滞后判定
  vec.py               sqlite-vec、RRF、查询向量 LRU 缓存
  remove.py            video 事件删除与级联
  language.py          16 语言名称与 prompt 语言规则
  timeutil.py          时区/日期窗/时间戳工具
  consts.py            事件优先级、day_stats 派生常量
  rebuild_dev.py       dev 全链重建/核数 CLI
  analysis/chatlog.py  chat 三 jsonl → events
  analysis/audio.py    音频分析
  analysis/vision.py   视频两阶段分析
  summary/day.py       日摘要发现与生成
  summary/day_stats.py 日卡派生字段预计算
  summary/timezones.py 活跃时区集合
```

---

## 12. 边界与未实现

- **period_summary**：表已建，生成逻辑未实现；周/月/年卡可由 BFF/客户端聚合
  day_summary 兜底。
- **未关联 agent job**：不再有查询入口；只有进入 chat 历史的 `agent_job` 行才会
  成为事件。
- **session 维度**：不对上层暴露；MCP/REST 无任何 session 级工具或字段。
- **v1 派生产物**：`timeline.json` / `session_summary.json` / `summary.json` /
  `video_summary.json` 等不再生成、不再读取；历史文件保留在盘上。
- **FTS 语法**：查询词一律引号包裹，不支持 FTS5 原生通配/布尔语法。
- **向量规模**：当前为精确 KNN；数据量显著增长后再评估 ANN。
