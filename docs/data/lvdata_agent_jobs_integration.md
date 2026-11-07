# Agent Jobs (PG) 接入 LvData 数据分析管线 — 调研与方案设计

> **状态**：设计定稿 v2.0（合并稿，已通过评审）
> **日期**：2026-08-18
> **前置文档**：[`fd_agent_job_pg_persistence.md`](../fd_agent_job_pg_persistence.md)（agent_jobs 表来源）、
> [`lvdata_inventory.md`](lvdata_inventory.md) / [`lvdata_audio_analysis.md`](lvdata_audio_analysis.md) /
> [`lvdata_video_analysis.md`](lvdata_video_analysis.md) / [`lvdata_summary_analysis.md`](lvdata_summary_analysis.md)（管线 Phase 1-5）、
> [`lvdata_mcp_service.md`](lvdata_mcp_service.md)（Phase 6 服务化）
> **范围**：调研结论 + 接入方案选型 + 详细设计；不含实现代码
> **说明**：本文以 k3 调研稿为基线，合并 d4 调研稿的实证验证、`list_jobs`、禁用态设计与 env 命名；
> 全部源码锚点已于 2026-08-18 逐一核对（合并记录见附录 A）。
> **2026-09-27 注**：本文为 v1 期历史设计稿。其后工具集已收敛——`list_jobs` 彻底删除
> （未关联 job 不再可查）、`get_status`/`trigger_analysis` 迁 REST、`get_day_summary` 由
> `get_recent_events` 取代；见 [`lvdata_mcp_tools.md`](./lvdata_mcp_tools.md)。文中相关
> 描述与源码锚点不再维护。

---

## 1. 背景与目标

`agent_jobs` 表（services PG，migration `0003_agent_jobs.py`）已于 2026-08-17 落地：Hermes 后台
job 的全生命周期（requested → running → done/delivered/failed）以**单行一 job 最新状态**镜像到
PostgreSQL，带 `summary` / `result_text` / `fail_reason` / 全量时间戳。fd 文档 §1.3 明确将
"接入 `tools/data-analysis/`（LvData SQLite 管线）"列为**后续工作**——本文即该后续工作的调研与方案。

**目标**：让 agent job 数据进入 LvData 分析管线，使 Agent（经 lvdata-mcp）与分析者能够：

1. **检索**：job 的 summary / result_text / fail_reason 进入全文（及向量）索引，`search` 可查；
2. **对齐**：job 事件进入 session 统一时间线（`timeline.json`），与 chat/audio/video 事件同轴回放；
3. **关联**：job ↔ recording session ↔ device 三方关联可查询、可追溯；
4. **统计**：支撑 fd 文档 §6 的失败率、耗时分布、按设备聚合等离线分析。

**非目标**（与 fd 文档非目标对齐，不扩大范围）：

- 不改 `services/agent_relay/` 任何写入路径（PG 镜像仍是 relay 侧唯一写入方，管线只读消费）；
- 不做实时推送（job 状态变化经轮询同步，分钟级延迟可接受）；
- 不做强一致（同步是 best-effort 镜像的镜像，最终一致）；
- 不改 MCP 查询面的"只读 SQLite + derived JSON"架构（见 §4 选型论证）。

---

## 2. 现状梳理（源码级，已逐条验证）

### 2.1 LvData 管线架构（现状）

```
/opt/lvdata/                                ← 单一事实来源（文件树 + SQLite + derived/）
├── lvdata_inventory.db                     ← SQLite 主索引（WAL）
├── <device_uuid>/<session_id>/             ← 源数据（session.json / audio / video / chat）
└── derived/<device_uuid>/<session_id>/     ← AI 衍生数据（分析 JSON / timeline.json / summary）
```

| 层 | 组件 | 职责 | 关键约定 |
|----|------|------|---------|
| Phase 1 | `lvdata_inventory.py` | 扫文件树 → `sessions/files/chat_messages` 表 | 幂等 upsert、mtime 增量跳过 |
| Phase 2 | `lvdata_analyze_audio.py` | OGG → Qwen Omni → `audio_analyses` 表 + derived JSON | pending 检测、`--force` 重跑 |
| Phase 3 | `lvdata_analyze_video.py` | JPG → Qwen VL 两步法 → `video_analyses` 表 + derived JSON | 窗口化批量、关键帧筛选 |
| Phase 4-5 | `lvdata_summary.py` | 对齐（`build_timeline`）→ 摘要 → 跨 session 报告 | align 零 LLM 成本；`multi_summary_status` 幂等 |
| Phase 6 | `tools/lvdata-mcp/` | 常驻 MCP 服务：watcher 60s 轮询编排上四者 + 9 个 MCP tools（8 只读 + `trigger_analysis`） | subprocess 编排；FTS5/向量索引按 session 重建 |

**对本次接入最关键的三条架构约定**：

1. **查询侧只读 SQLite + derived JSON**（`store.py`），MCP tools 不直连任何外部数据源；
2. **pipeline 一律 subprocess 调 CLI**（`pipeline.py` `STAGE_SCRIPT` 表），watcher 只做编排；
3. **索引按 session 重建**（`index.py::rebuild_session` / `vec.py::rebuild_session`，
   `DELETE FROM ... WHERE session_id=?` + `INSERT`，幂等），`event_ref =
   "{session_id}:{type}:{source_id}"` 是跨工具的稳定句柄。

另有两处排序契约双处定义、改事件类型时必须同步（§5.4/§5.6 均依赖）：

- `lvdata_summary.py::EVENT_PRIORITY`（`:115`）与 `store.py::EVENT_TYPE_ORDER`（`:22`）——同 ts_ms 稳定排序；
- `store.py::EVENT_TYPES`（`:20`）——`get_timeline` 的默认类型过滤（`store.py:293`）。

### 2.2 agent_jobs 数据模型（PG 侧事实）

表结构（`services/migrations/versions/0003_agent_jobs.py`，ORM `AgentJob`）：

| 列 | 类型 | 语义 | 管线视角的关键性 |
|----|------|------|----------------|
| `job_key` | Text UNIQUE | relay 幂等键（uuid4 hex） | **主键 / event_ref source_id** |
| `agent_name` | Text | 恒 `'hermes'`（预留多 adapter） | 过滤维度 |
| `device_uuid` | Text | 客户端 UUID v4 | **关联键 ①（= lvdata `sessions.device_uuid`）** |
| `session_id` | Text | **livachat chat session id**（仅追溯） | **关联键 ②（≠ recording session id，见 §3）** |
| `hermes_task_id` | Text NULL | prompt.background task id | 追溯用 |
| `status` | Text | requested/running/done/delivered/failed | 分析主维度 |
| `summary` | Text ≤256 字符 | 一行摘要 | **检索文本** |
| `result_text` | Text NULL ≤16KB | 成品文案；NULL = NOOP/error 消费 | **检索文本（含用户内容，隐私同级）** |
| `fail_reason` | Text NULL | timeout/orphaned/rpc_error/session_lost | **检索文本 + 失败分析** |
| `requested_at` | timestamptz | = record.created_at | **timeline 事件时间戳（主排序键）** |
| `started_at` / `finished_at` / `delivered_at` | timestamptz NULL | 各转换时刻；`finished_at` 对 failed 已派生非空（D6） | 耗时统计 |
| `created_at` | timestamptz | PG 行首插（DEFAULT now()，冲突不覆盖） | 同步水位备选 |
| `updated_at` | timestamptz | = 源 record.updated_at，**每次 upsert 显式写入，单调守卫基准（D3）** | **增量同步水位（主）** |

写入路径（只读消费方只需了解其语义）：`JobStore` 五转换方法 CAS 成功 →
`set_record_listener` 同步钩子 → `JobPgWriter` 有界队列（1024）+ 单 writer →
`INSERT .. ON CONFLICT (job_key) DO UPDATE .. WHERE agent_jobs.updated_at <= excluded.updated_at`。
best-effort 边界（fd §5）：终态写入被丢弃则行永久停留在前一状态；队列满丢弃有
`queue_dropped` 指标。→ **管线侧不能假设 PG 行必然到达终态**，分析查询需容忍"活跃态滞留"。

数据量级（fd §8）：每活跃用户每设备日 ~几十条，单行 ~2-8KB，日增万级行 ≈ 百 MB 级/年。
→ **同步与索引成本可忽略**，无需分区/外置存储；但**设计量级以 fd §8 为准**
（当前单设备实测日个位数只是部署现状，不作为简化同步设计的依据）。

### 2.3 PG 侧其余相关表（同库可 join）

- `recording_sessions`（0002）：`id` = recording session UUID（= lvdata 目录与
  `sessions.session_id`），`device_uuid`（Text 无 FK）、`session_date`、`started_at` /
  `finalized_at`、各模态计数、`analysis_status`——**与 lvdata SQLite `sessions` 表同源镜像**；
- `recording_files`（0002）：文件级清单（kind/rel_path/size）；
- `sessions` / `messages`（0001）：livachat chat 会话与消息。

> 注：lvdata SQLite 的数据源头是**文件树**（recording 服务写盘），PG `recording_sessions`
> 是同一实体的服务侧镜像。两套库存量理论上等价，但 lvdata 管线以文件树为准。
> 本期只接 `agent_jobs`，不接 `recording_sessions`（避免双事实源冲突，见 §8 开放问题 4）。

> **2026-09-15 裁决反转（见 `docs/recording_data_process.md`）**：录制数据事实源已从
> 文件树反转为 **chat_server.db 的 `recording_files` 表**（服务端 recording 与文件写
> 同点登记、lvdata ATTACH 只读轮询；文件树仅存媒体本体）。lvdata 侧 fs 扫盘（scanner）
> 与 agent_jobs 同步（jobs_watch/agent_job_links）已删除——agent job 事件改由
> `chat/history.jsonl` 的 `source="agent_job"` 行铸造（行内携带 job_key/agent_name/
> agent_id）。原文「以文件树为准」不再适用，保留作历史决策记录。

### 2.4 实测关联验证（2026-08-18）

对运行数据（device `6e8f80d3-…`，jobs `a44ca4b0…` / `0beda0a9…`，recording session `01a012a3-…`）实测确认：

```
PG agent_jobs
  ├─ device_uuid = 6e8f80d3-…      ←── 与 /opt/lvdata/<device_uuid>/ 顶层一致 ✓
  └─ session_id  = sess_695uphcqznvhc8x2vh
       │
       └─ chatlog 每行 chat_session_id = "sess_695uphcqznvhc8x2vh" ✓
            （/opt/lvdata/6e8f80d3-…/01a012a3-…/chat/history.jsonl）
```

⚠️ **重要实证发现**：当前 `session.json` 的 `chat_session_ids` 数组**为空**——
该字段仅经 `on_session_start` 回调路径写入（`services/recording/session.py:172-175`，
写出在 `:371`），存在 chat session 先于 recording  attach 的窗口。
**关联必须以 `chat_messages.chat_session_id`（inventory 已从 chatlog 行解析入库）为准，
不依赖 `session.json.chat_session_ids`**。修复写入路径另立任务（见 §8 开放问题 7），
修复后 enrich 可将其作为第三映射源。

---

## 3. 数据关联分析（核心调研发现）

两个系统的 ID 空间不同，**关联键的确认是接入可行性的前提**。逐条验证结论：

### 3.1 ID 血缘图

```
services PG (livachat DB)                          lvdata (文件树 + SQLite)
─────────────────────────                          ───────────────────────
agent_jobs.device_uuid ────── 客户端 UUID v4 ──────▶ sessions.device_uuid        ✅ 同值
agent_jobs.session_id  ─── livachat chat sid ────▶ chat_messages.chat_session_id ✅ 同值
                                                    （inventory 已从 history.jsonl 解析入库——
                                                     关联以此为准；session.json 的
                                                     chat_session_ids 实测为空，不作映射源，§2.4）
recording_sessions.id  ─── recording UUIDv7 ─────▶ sessions.session_id           ✅ 同值
                                                    （= 目录名 <device>/<session_id>）
agent_jobs.requested_at ─── timestamptz ─────────▶ 各事件 ts_ms（epoch ms）       ✅ 可换算
```

### 3.2 关联策略（job → recording session）

一个 agent job 归属于哪个 lvdata session，按优先级两条路径：

| # | 路径 | 命中条件 | 可靠性 |
|---|------|---------|--------|
| 1 | **chat session 直连**：`agent_jobs.session_id` = `chat_messages.chat_session_id` → 取该消息的 `session_id`（recording session） | 该 chat session 有消息落盘且被 inventory 解析 | 高（精确） |
| 2 | **device + 时间窗兜底**：同 `device_uuid` 下 `requested_at ∈ [session 起, session 止]` 的 recording session | 路径 1 未命中（如该 chat 无消息记录、inventory 未扫到） | 中（近似，session 时间窗不交叠时精确） |

剩余未关联 job（device 无任何 recording session、或落在所有时间窗外）：
**保留在镜像表中，按 device 维度可查（`list_jobs`，§5.6），不进任何 session 的 timeline/索引**。
这是正常情形（lvdata 采集与 agent 使用未必同步开启），不是错误。

### 3.3 时间轴对齐

- job 事件在 timeline 中的排序键：`requested_at` → `epoch ms`（与 lvdata 全部事件的
  `ts_ms` 同单位、同时区基准 UTC）；
- 单个 job 的多次状态转换**不拆成多个 timeline 事件**——`agent_jobs` 是单行最新状态，
  镜像同步天然只携带最新态；timeline 事件表示"此刻有一个 job，其最新状态为 X"。
  若未来需要转换级历史，需改 fd 设计为追加事件表（本期明确不做，fd D3）。

---

## 4. 接入方案选型

### 4.1 候选方案

| | 方案 A：PG → SQLite 镜像同步（**推荐**） | 方案 B：MCP 服务直连 PG 查询 | 方案 C：relay 侧双写文件树 |
|---|---|---|---|
| 做法 | 新 CLI `lvdata_sync_agent_jobs.py`：增量 SELECT PG → upsert 进 `lvdata_inventory.db` 的 `agent_jobs` 镜像表；timeline/索引/检索全部复用现有机制 | `store.py` 增加 asyncpg 连接，tools 查询时实时查 PG | 改 `JobPgWriter` 额外写 `derived/` JSON |
| 架构契合 | ✅ 完全遵循"查询侧只读 SQLite/derived"与"pipeline subprocess CLI"两条约定 | ❌ 破坏单一事实来源；MCP 服务强依赖 PG 可用性；FTS（SQLite）与 PG 跨库无法 join | ❌ 改 services 写入路径，违反"最小侵入"；fd 明确 PG 镜像为 best-effort，双写加剧不一致 |
| PG 不可用 | 已同步数据照常可查（离线容忍） | 相关 tools 全挂 | 同 A（但写入路径有风险） |
| 实时性 | 轮询延迟（≤60s，与 watcher 同拍） | 实时 | 近实时 |
| 数据重复 | 镜像一份（日万级行 × 数 KB，可忽略） | 无 | 双写 |
| 工作量 | 1 个新 CLI + 索引/timeline/tools 扩展 | store/tools 改造 + 跨库检索重设计 | services 改动 + 文件格式新约定 |

> 另评估过"把 job 创建/完成写回 chatlog（assistant 行）"的零管线改动方案：丢结构化状态
> （status/fail_reason/耗时）、污染对话语义、违反"服务侧不写源文件"约定且不可逆，排除（记录备查）。

### 4.2 结论

**选方案 A**。理由：LvData 管线的全部价值（对齐、FTS/向量索引、9 个 MCP tools、watcher
编排）都建立在"SQLite + derived JSON 单一事实来源"之上；把 agent_jobs 镜像进同一 SQLite，
等于零新架构地获得检索/对齐/统计能力。方案 B 在 v1 引入跨库依赖代价远大于省一份镜像；
方案 C 侵入实时链路，直接排除。

这与 fd 文档 §8"后续接入 LvData：……可增加 PG 数据源的分析脚本"的预设一致。

---

## 5. 详细设计（方案 A）

### 5.1 总览：数据流

```
services PG.agent_jobs
      │  增量 SELECT（水位：(updated_at, job_key) 键集分页）
      ▼
lvdata_sync_agent_jobs.py（新 CLI，Phase 1 式工具）
      │  upsert → lvdata_inventory.db.agent_jobs（镜像表）
      │  enrich → recording_session_id 关联解析（§3.2，可收敛重试）
      ▼
lvdata_inventory.db.agent_jobs ──┬──▶ build_timeline() 增加 agent_job 事件（§5.4）
                                 ├──▶ index.py / vec.py 增加 type='agent_job'（§5.5）
                                 └──▶ MCP tools：search / get_event / get_timeline / list_jobs（§5.6）
      ▲
watcher 每轮 poll 编排（§5.7）：inventory → agent_jobs →（受影响 session）align → reindex
```

### 5.2 新工具 `tools/data-analysis/lvdata_sync_agent_jobs.py`

**定位**：与 `lvdata_inventory.py` 同级的"数据源接入"工具——inventory 接文件树，本工具接 PG。
零 LLM 调用、零外部服务（除 PG）、秒级完成，可每轮 poll 跑。

**CLI**：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--db-path` | `/opt/lvdata/lvdata_inventory.db` | 与 Phase 1-5 相同 |
| `--pg-dsn` | env `LVDATA_MCP_PG_DSN`，回落 `LIVACHAT__POSTGRES__DSN`（**自动剥 `+asyncpg` 驱动后缀**——该变量实测值为 `postgresql+asyncpg://…`，psycopg 不能直接使用） | libpq 格式 |
| `--batch-size` | `5000` | 每批拉取行数 |
| `--full-sync` | flag | 忽略水位全量重同步（幂等，修复/校准用） |
| `--device-uuid` | (none) | 可选过滤维度（job 数据天然全局，无 `--session-id` 维度） |
| `--dry-run` / `--verbose` | — | 与现有工具一致 |

**镜像表 Schema**（`CREATE TABLE IF NOT EXISTS`，不动既有表）：

```sql
CREATE TABLE IF NOT EXISTS agent_jobs (
    job_key              TEXT PRIMARY KEY,        -- PG upsert 冲突键原样作主键
    agent_name           TEXT NOT NULL DEFAULT 'hermes',
    device_uuid          TEXT NOT NULL,
    session_id           TEXT NOT NULL,           -- livachat chat session id（原文保留）
    recording_session_id TEXT,                    -- enrich 产物：关联到的 lvdata session
    hermes_task_id       TEXT,
    status               TEXT NOT NULL,           -- requested|running|done|delivered|failed
    summary              TEXT NOT NULL DEFAULT '',
    result_text          TEXT,
    fail_reason          TEXT,
    requested_ts_ms      INTEGER NOT NULL,        -- requested_at 换算 epoch ms（timeline 排序键）
    requested_at         TEXT NOT NULL,           -- ISO 8601（原文保留）
    started_at           TEXT,
    finished_at          TEXT,
    delivered_at         TEXT,
    pg_updated_at        TEXT NOT NULL,           -- 源 updated_at（增量水位 + 乱序守卫）
    synced_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_aj_device_time ON agent_jobs(device_uuid, requested_ts_ms);
CREATE INDEX IF NOT EXISTS idx_aj_rec_session ON agent_jobs(recording_session_id);
CREATE INDEX IF NOT EXISTS idx_aj_status_finished ON agent_jobs(status, finished_at);

CREATE TABLE IF NOT EXISTS agent_jobs_sync_state (   -- 单行水位表（与工具自有状态约定一致）
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_pg_updated_at TEXT,
    last_job_key       TEXT,
    last_run_at        TEXT,
    rows_upserted      INTEGER DEFAULT 0
);
```

**增量同步逻辑**：

```sql
-- PG 侧（键集分页，与 updated_at 单调守卫同向，耐受同步期间的并发 upsert；
-- 不用 MAX(updated_at) 朴素水位——同时间戳的行会被跳过）
SELECT * FROM agent_jobs
WHERE (updated_at, job_key) > (%s, %s)
ORDER BY updated_at, job_key
LIMIT %s;
```

- SQLite 侧 upsert：`INSERT .. ON CONFLICT(job_key) DO UPDATE .. WHERE
  agent_jobs.pg_updated_at <= excluded.pg_updated_at`——**复刻 fd D3 守卫语义**，
  容忍全量重同步与并发写乱序；
- 活跃 job（requested/running）会随状态推进被反复同步（`updated_at` 前进即被水位捕获），
  **无需额外 reconcile**；终态 job 不再变化，天然只同步一次；
- 单批失败记 warning 并续跑下一批；PG 连接失败整体报错退出（watcher 记录
  `mcp_pipeline_runs`，按 §5.7 退避重试）。

**关联 enrich**（每批 upsert 后对未关联行执行）：

```sql
-- 路径 1：chat session 直连（§3.2-1）
UPDATE agent_jobs
SET recording_session_id = (
    SELECT cm.session_id FROM chat_messages cm
    WHERE cm.chat_session_id = agent_jobs.session_id LIMIT 1)
WHERE recording_session_id IS NULL
  AND EXISTS (SELECT 1 FROM chat_messages cm
              WHERE cm.chat_session_id = agent_jobs.session_id);

-- 路径 2：device + 时间窗兜底（§3.2-2）
-- requested_ts_ms ∈ [session 首事件 ts, 末事件 ts]（sessions 表 started_at/finalized_at
-- 或 files.uuid_ts_ms 最值；active session 末事件持续前进，后续批次可补关联）
```

注意 **enrich 是可收敛的重复过程**：本轮未关联的 job，下轮 inventory 扫到新数据后可能
变得可关联——因此 enrich 每轮对 `recording_session_id IS NULL` 的行重试，而非一次性。

**输出约定**：进程 stdout 末尾打印一行机器可读摘要（供 watcher 解析，日志风格与现有
工具一致），含 `synced_rows` / `affected_recording_sessions` 列表（本轮新关联或状态变化的
job 所归属的 session，供 §5.7 定向 align+reindex）。

### 5.3 依赖、配置与禁用态

- 新依赖：**`psycopg[binary]>=3.1`**（同步驱动，CLI 最简；tools/data-analysis 现有
  openai/Pillow 之外唯一新增）；
- PG 账号：建议为管线建**只读账号**（`GRANT SELECT ON agent_jobs`），DSN 经 env 注入，
  不落盘；lvdata-mcp 与 services 同网段部署为前置条件（见 §8 开放问题 1）；
- 隐私：`result_text`/`summary` 含用户内容，同步后与 lvdata 音视频数据**同级隐私**，
  沿用管线既有约定（fd §8 的留存决策同样覆盖镜像副本——镜像不扩大暴露面，但
  `derived/_reports` 打包迁移时会随之携带，运维须知）。

`lvdata-mcp` 的 `Settings`（`config.py`，env 前缀 `LVDATA_MCP_` 既有约定）增加：

| 配置 | 默认 | 说明 |
|------|------|------|
| `pg_dsn`（env `LVDATA_MCP_PG_DSN`） | (none) | **未配置 = jobs 同步禁用**：服务照常启动，watcher 跳过 `agent_jobs` stage，`list_jobs` / `search type=agent_job` / `get_event …:agent_job:…` 返回 `jobs_disabled` 错误码 + 配置指引。可回落读 `LIVACHAT__POSTGRES__DSN`（剥 `+asyncpg` 后缀） |
| `jobs_sync_interval_s` | 60（= poll 间隔） | 与 watcher 同频，无需独立配置，保留扩展位 |

### 5.4 timeline 集成（`lvdata_summary.py`）

`build_timeline()` 增加第 5 类事件源（从镜像表读，与 chat 同源模式）：

```python
# 5) agent job（镜像表；仅已关联到本 session 的 job）
for row in conn.execute(
        "SELECT job_key, status, summary, fail_reason, result_text, requested_ts_ms "
        "FROM agent_jobs WHERE recording_session_id = ?", [session_id]):
    events.append({
        "ts_ms": row["requested_ts_ms"], "type": "agent_job",
        "job_key": row["job_key"], "status": row["status"],
        "summary": row["summary"], "fail_reason": row["fail_reason"],
        "has_result": row["result_text"] is not None,
    })
```

- **三处排序/过滤契约同步修改**（漏任何一处都会导致事件不可见或分页错乱）：
  `lvdata_summary.py::EVENT_PRIORITY`（`:115`）追加 `"agent_job": 4`（同毫秒排最末——系统侧事件）；
  `store.py::EVENT_TYPE_ORDER`（`:22`）同步追加；`store.py::EVENT_TYPES`（`:20`，
  `get_timeline` 默认过滤，`store.py:293`）同步追加；
- 事件**不带 `result_text` 全文**（≤16KB，timeline 膨胀）；全文经 `get_event` 按需取
  （§5.6），与 timeline 现有"轻引用、重详情"风格一致；
- align 阶段零 LLM 成本，job 状态变化触发重跑 align 无费用顾虑。

> **摘要隔离的显式契约**：`build_session_block`（`lvdata_summary.py:502-535`）按事件类型
> 分支且**无 else**——`agent_job` 事件当前被**静默跳过**，不会进入 LLM 摘要 prompt，
> "摘要 P2 不纳入 job 维度"因此成立。但该安全是隐式的：P2 交付须包含针对
> `build_session_block` / `compute_session_stats` 的"未知事件类型忽略"测试，
> 把隐式行为固化为契约（防后续重构加兜底分支导致 job 内容无声泄入摘要）。

### 5.5 索引集成（`index.py` FTS + `vec.py` 向量）

- 新 type `'agent_job'`；`event_ref = "{recording_session_id}:agent_job:{job_key}"`；
- 索引文本拼装（与现有 doc 同款风格）：
  `summary + result_text(截断 2KB) + fail_reason + status`；
- `rebuild_session()` 在 session 粒度 DELETE+INSERT 时自然纳入（同源数据已在 SQLite，
  无需读 derived JSON——`_text_for` 直接组 SQL 行字段）；`search` 的 `searched_types`
  输出列表（`tools.py:108`）同步扩展；
- **未关联 job 不进 session 索引**：`search` 的 session 维度检索覆盖已关联 job；
  未关联 job 的检索由 §5.6 的 `list_jobs` 覆盖，避免引入"伪 session"污染现有索引水位逻辑；
- 向量索引（`vec.py::_iter_session_chunks`）同型扩展：1 job = 1 chunk，复用
  embedding 增量调度点。

### 5.6 MCP tools 扩展（`tools.py` / `store.py`）

| Tool | 改动 |
|------|------|
| `search` / `semantic_search` | `type` 枚举加 `agent_job`；FTS 查询零改动（type 过滤现成）；`searched_types` 输出同步 |
| `get_event` | 解析 `…:agent_job:<job_key>` → 查镜像表返回全量字段（含 `result_text` 全文、`started/finished/delivered_at` 耗时三元组）；`related` 给出 `session_id`（recording）与 `chat_session_id` 双向引用。**event_ref 仅对已关联 job 签发**——未关联 job 在 `sessions` 表无行，`_session_or_error` 无法寻址，其检索与详情一律经 `list_jobs` |
| `get_timeline` | **非零改动**：扩展 `EVENT_TYPES` + `EVENT_TYPE_ORDER`（§5.4）；此后 timeline.json 已含 agent_job 事件，现算路径复用更新后的 `build_timeline` |
| `list_sessions` | `counts` 增加 `agent_jobs` 计数（镜像表按 `recording_session_id` 聚合） |
| `list_jobs`（**新增，v1，只读**） | 按 `device_uuid? / session_id?(chat) / recording_session_id? / status? / date_from? / date_to? / limit / offset` 直查镜像表，返回结构化全字段列表（含 `result_text`）——**覆盖未关联 job 的检索**、运维查询与 fd §6 统计型查询，也是 job 数据的 discovery 入口 |
| `trigger_analysis` | `stages` 白名单（`TriggerParams`，现仅 audio/video/summary，`tools.py:259`）加 `agent_jobs` 与 `align` |

### 5.7 watcher 编排（`watcher.py` / `pipeline.py`）

- `STAGE_SCRIPT` 加两项：`"agent_jobs": "lvdata_sync_agent_jobs.py"`（全局）、
  `"align": "lvdata_summary.py"`（per-session）；`_cmd` 为两者各加分支——
  `agent_jobs` 追加 `--pg-dsn`（从 settings 注入）；`align` 为
  `--stage align --session-id <sid> --timezone …`（与现有 `summary` stage 的
  `--stage all` 区分；`--stage align` 是 `lvdata_summary.py` 现成 CLI 入口）；
- `poll_once` 主循环：`inventory` 之后立即跑 `agent_jobs`（全局级，与 inventory 同级——
  无 session 参数；秒级本地操作，每轮必跑）；`pg_dsn` 未配置时跳过（§5.3）；
- **失败退避**：`_run_with_backoff` 的退避键为 `(sid, stage)`（`watcher.py:323`），
  全局 stage 以 `None` 作 sid 即 `(None, "agent_jobs")`——`run_stage(stage, session_id=None)`
  与 `reset_backoff` / `status()` 均天然兼容，仅需小改；（现状全局 stage inventory/report
  无退避、每轮重试，jobs 引入退避是因为 PG 故障是持续态，无退避会每 60s 空打日志）；
- **定向 align + reindex**：解析 sync 输出的 `affected_recording_sessions`，对每个受影响
  session 调既有 `watcher.enqueue(sid, stages=["align"])`（override 机制现成，
  `watcher.py:358-372`）；`_process_session` 新增 `want("align")` 分支
  （`_run_with_backoff(sid, "align")`，置于 reindex 之前），随后既有无条件
  `reindex` + `embed` 完成索引更新。override 为 `["align"]` 时
  `want("audio"/"video"/"summary")` 均为 False，**不触发** audio/video/summary——
  job 事件不影响既有模态分析；session 摘要（LLM 叙事）P2 不纳入 job 维度
  （见 §6 P3 可选项），避免 job 高频状态推进反复打爆摘要调用；
- 注意：现有 `detect_dirty` 完全基于 files 表 count/max_ms（`watcher.py:146-215`），
  **纯 job 变化不会弄脏任何 session**——上述"sync 输出 → 定向 enqueue"是 job 变化
  触发重索引的唯一链路，不可省略。

### 5.8 分析侧收益（fd §6 查询在镜像表上原样成立）

镜像表列名/语义与 PG 一致，fd §6 的全部示例 SQL（失败原因分布、耗时分位数、done 滞留
聚合、会话级关联）可直接在 `lvdata_inventory.db` 执行（`date_trunc` 等 PG 方言换 SQLite
等价写法）。**新增**独有能力是跨模态时间线回放：

> "用户在 15:08 抱怨响应慢（chat）→ 同一分钟 agent_jobs 显示一个 job 因 `timeout`
> failed → 画面显示用户在 IDE 前等待（video 关键帧）" —— 这是 fd 文档落地时无法
> 独立提供的、管线特有的关联洞察。

---

## 6. 分阶段实施计划

| 阶段 | 内容 | 交付物 | 验证 |
|------|------|--------|------|
| **P1 同步** | `lvdata_sync_agent_jobs.py`：镜像表 + 键集水位增量 + D3 守卫 upsert + enrich + 单测 | 新 CLI + `tests/test_lvdata_sync_agent_jobs.py` | 测试 PG（docker-compose）造 job 全生命周期，断言镜像表终态一致；同 `updated_at` 多行不丢（键集水位）；`--full-sync` 幂等 |
| **P2 可查** | timeline 事件 + 三处契约同步（§5.4）+ FTS/向量 type + `search`/`get_event`/`get_timeline`/`list_sessions`/`list_jobs` 扩展 + align-only stage 链（§5.7）+ watcher 编排 + 禁用态（§5.3） | lvdata-mcp 侧改动 + 集成测试 | `search type=agent_job` 命中 fail_reason；`get_timeline` 默认 types 含 agent_job 事件且排序/游标正确；`list_jobs` 覆盖未关联 job；`build_session_block`/`compute_session_stats` 忽略 agent_job 事件的契约测试；job 状态变化 → 定向 align+reindex 全链路；`pg_dsn` 未配置时 `jobs_disabled` 行为 |
| **P3 可洞察**（可选，独立评估） | session 摘要/跨 session 报告纳入 job 维度（成功率、失败模式入 LLM 叙事）；report.json 加 `agent_jobs` 统计段 | `lvdata_summary.py` 扩展 | 报告中如实标注 job 统计，无证据不下结论（沿用现有 prompt 纪律） |

P1→P2 是核心价值路径；P3 涉及 LLM 调用策略（job 状态高频变化 vs 摘要失效策略），
建议在 P2 稳定运行、积累真实数据后再定。

---

## 7. 成本与性能估算

| 项 | 估算 |
|----|------|
| 同步开销 | 日万级行 × 键集分页 SELECT + SQLite upsert，秒级；每轮 poll 跑无压力 |
| 存储 | 镜像 ≈ PG 表本体（百 MB/年级），相对 lvdata 音视频体量可忽略 |
| LLM 成本 | **P1+P2 全程零 LLM 调用**（同步/对齐/FTS 均本地确定性计算）；向量 embed 仅 job 文本（百 token 级/条），成本可忽略 |
| 索引膨胀 | `search_docs`/`vec_chunks` 每 session 增数十条，重建逻辑零改动 |

---

## 8. 风险与开放问题

1. **部署拓扑前提**：lvdata-mcp 需网络可达 services PG。当前 lvdata 部署形态（本机/
   挂载卷，mcp 设计文档 §11 开放问题 1）与 PG 的网络关系需确认；跨网段时只读账号 +
   TLS 是必要的。
2. **best-effort 的平方**：PG 镜像本身可能丢终态（fd §5），镜像的镜像进一步放大滞后；
   分析结论需注明"截至 sync 水位"。如需校准，可周期性 `--full-sync`（幂等）兜底。
3. **活跃 job 的 timeline 抖动**：job 从 running → failed 时，timeline 中同一 `job_key`
   事件的状态随 align 重跑而更新——这是"单行最新状态"模型的固有表现（事件内容会变，
   事件本身不增不减），Agent 侧需理解该语义（在 tool 描述中写明）。
4. **`recording_sessions` 双事实源**：PG `recording_sessions` 与 lvdata `sessions` 是同一
   实体的两套镜像。本期只接 `agent_jobs` 刻意回避该冲突；若未来接 PG 侧 recording 元
   数据，需先裁定以哪边为准（建议仍文件树为准，PG 仅校验）。
5. **enrich 路径 2 的误判**：device 时间窗兜底在"同设备多 session 时间窗交叠"时可能错
   配。当前 recording session 为串行录制（一个设备同一时刻一个 active session），风险
   低；若未来支持并行 session，路径 2 需加 chat_session 校验或降级为不关联。
6. **fd 开放问题 1 的继承**：NOOP 与 hermes `error:` 在 PG 中不可区分（均为
   `done + result_text IS NULL`），镜像同样不可区分；待上游加 `result_kind` 列后镜像
   加列即可（`_ensure_column` 模式现成）。
7. **`session.json.chat_session_ids` 写入缺失**（§2.4 实证）：当前关联不依赖该字段；
   是否修复 `recording/session.py` 的 `on_session_start` 写入路径另立任务——修复后
   enrich 可将其作为第三映射源（优先级仍低于 chatlog 行映射），本方案自动获益。

---

## 9. 调研参考（源码锚点）

| 事实 | 锚点 |
|------|------|
| agent_jobs 表结构与索引 | `services/migrations/versions/0003_agent_jobs.py`、`services/shared/models/orm.py:256` |
| upsert 单调守卫（D3） | `services/shared/persistence/repositories/agent_jobs.py` |
| 写入链路（钩子→队列→writer） | `services/agent_relay/job_persistence.py`、`docs/fd_agent_job_pg_persistence.md` §4.4-4.5 |
| PG DSN 配置与格式 | `services/app/application.py:46`（`create_engine(cfg.postgres.dsn)`）；实测值 `postgresql+asyncpg://…`（`services/tests/integration/conftest.py:19` 等） |
| lvdata SQLite schema | `docs/data/lvdata_inventory.md` §2.4 |
| timeline 构建与事件优先级 | `tools/data-analysis/lvdata_summary.py` `build_timeline()` / `EVENT_PRIORITY`（`:115`） |
| 摘要 prompt 事件格式化（未知类型静默跳过） | `tools/data-analysis/lvdata_summary.py:502-535`（`build_session_block`） |
| get_timeline 默认过滤与游标排序契约 | `tools/lvdata-mcp/src/lvdata_mcp/store.py:20-22`（`EVENT_TYPES`/`EVENT_TYPE_ORDER`）、`:293-308` |
| FTS 索引按 session 重建 | `tools/lvdata-mcp/src/lvdata_mcp/index.py::rebuild_session` |
| 向量 chunk 与索引 | `tools/lvdata-mcp/src/lvdata_mcp/vec.py::_iter_session_chunks` |
| watcher 编排与 stage 表 | `tools/lvdata-mcp/src/lvdata_mcp/pipeline.py::STAGE_SCRIPT`、`watcher.py::poll_once` |
| watcher 退避键 / stages override | `watcher.py:54,323`（`(sid, stage)`）、`watcher.py:358-372`（`enqueue` stages override） |
| MCP tools 清单与 searched_types | `tools/lvdata-mcp/src/lvdata_mcp/tools.py`（9 个 `@mcp.tool` = 8 只读 + `trigger_analysis`；`:108` `searched_types`；`:259` stages 白名单说明） |
| MCP Settings env 前缀 | `tools/lvdata-mcp/src/lvdata_mcp/config.py:12`（`env_prefix="LVDATA_MCP_"`） |
| chat_session_id 关联键 | `docs/data/lvdata_inventory.md` §2.3-2.4；`tools/data-analysis/lvdata_inventory.py:109`（`chat_messages.chat_session_id`） |
| `session.json.chat_session_ids` 写入路径 | `services/recording/session.py:172-175`（`on_session_start`）、`:371`（写出） |
| device_uuid 同值性 | `docs/fd_agent_job_pg_persistence.md` §2.3；`docs/data/lvdata_inventory.md` §2.1 |
| 实际数据关联验证 | 2026-08-18 运行（device `6e8f80d3-…`，jobs `a44ca4b0…` / `0beda0a9…`，recording session `01a012a3-…`），§2.4 |

---

## 附录 A：合并记录（2026-08-18 评审）

本文合并两份调研稿，裁决如下：

- **基线 = k3 稿**（键集水位 + D3 守卫同步、`requested_ts_ms` 预计算、定向 align+reindex
  触发链、asyncpg 后缀处理、`(None, stage)` 退避观察、源码锚点经核实全部成立）；
- **并入 d4 稿**：① §2.4 实测关联验证与"`session.json.chat_session_ids` 不可依赖"警示；
  ② `list_jobs` 提升为 v1 新增 tool（覆盖未关联 job 检索，k3 原置于 v2 导致 v1 覆盖缺口）；
  ③ `pg_dsn` 未配置的禁用态设计（`jobs_disabled` + 指引）；④ env 命名
  `LVDATA_MCP_PG_DSN`（符合 `config.py` 的 `LVDATA_MCP_` 前缀约定）；⑤ "写回 chatlog"
  方案的排除记录（§4.1 注）；
- **评审修正 k3**：① `get_timeline` 并非"零改动"——`EVENT_TYPES`/`EVENT_TYPE_ORDER`
  必须同步扩展（`store.py:20-22,293`），已写入 §5.4/§5.6；② align-only stage 实现链补全
  （`STAGE_SCRIPT["align"]` + `_cmd` 分支 + `_process_session want("align")` + `TriggerParams`
  白名单 + `enqueue` override），已写入 §5.7；③ 未关联 job 的 v1 检索路径明确为
  `list_jobs`（原稿 §5.5/§5.6 自相矛盾）；④ 事件/索引类型命名统一为 `agent_job`
  （d4 稿 FTS 用 `job`、timeline 用 `agent_job`，前后不一）；
- **新增契约测试**：`build_session_block`/`compute_session_stats` 对未知事件类型的忽略
  （§5.4），把"摘要不泄漏 job 内容"的隐式安全固化为显式契约。

两份草稿（`lvdata_agent_jobs_integration-k3.md` / `-d4.md`）内容已由本文完全覆盖，不再保留。

---

*Design version: 2.0（合并定稿，supersedes k3/d4 调研稿）| Created: 2026-08-18*
