# LvData MCP Service v2 设计文档（定稿）

> **已被取代（2026-09-15）**：本文的 Ingestion 平面（文件系统扫描 / jobs_watch /
> detect_dirty / per-session 链）已由 [`docs/recording_data_process.md`](../recording_data_process.md)
> 重构取代——事实源改为 chat_server.db 的 `recording_files` 表，处理改为 per-type
> 全局批处理。Query 平面（tools/events/day_summary 契约）仍有效。本文保留作历史
> 决策记录。

> **已更新**：§6.2.0 契约实质变更——agent_jobs 源表移至 chat_server.db，lvdata-mcp ATTACH 只读扫描 + agent_job_links 承接 enrich，见 [chatserver_no_redis.md](../chatserver_no_redis.md)。（其中 agent_job_links/enrich 已随上述重构删除。）

> **修订裁定（2026-09-15，session_json_refactor 后续修订）**：`day_force` 表**废止**
> （§5.7 删表）+ session.json **静态化**——仅 start 首写一次，移除 timezone/status/
> last_active_at/finalized_at，finalize 不再写盘。finalize 信号消失后 day_force 失去
> 写入方，§6.1.8 第 8 条、§6.2 相关描述作废；收尾日生成由 debounce
> （`summarize_quiet_s`）首要保障、flush 门控条件 2 兜底。详见
> [session_json_refactor.md](../session_json_refactor.md)。

> **修订裁定（2026-09-16，音频 schema 两轮调整）**：audio 分析 schema 精简——删
> `background_sounds`/`events`/`quality`/`place`、`objects` 去 `time_s`、新增
> `value`（low|high，总体价值评估）、`people` 改统计对象
> （count/male_count/female_count/description）；静音判定重定义为「transcript
> 词数 > 5（CJK 感知）或 value=high 才落行」。详见
> [audio_analysis.md](../audio_analysis.md)。

> **修订裁定（2026-09-16，day_summary 输入去重）**：§6.5 日总结输入过滤——
> audio 事件且 `payload.transcript` 非空不进入 day_summary（文字已由 chatlog
> 事件覆盖，避免重复喂同一段话）；transcript 空则以其 summary 进入；FTS/向量
> 不受影响。详见 [audio_analysis.md](../audio_analysis.md) §10。

> **修订裁定（2026-09-18，视频 filter schema 精简）**：§6.4 vision filter 阶段
> ——帧键收敛为 `frame_id / quality / duplicate_of / value / reason`（删 blur /
> quality_issues / novelty / is_key_frame / key_rank）；**关键帧改为派生量**
> （`value=high` 且非重复帧）；**无派生关键帧的窗口不产事件**；payload `static`
> 字段移除；bff 日卡选图改为 **window 去重取每窗口首个关键帧 → ts 升序**（V7）。
> 详见 [video_analysis.md](../video_analysis.md)。

> **修订裁定（2026-10-07，视频 filter schema 第二轮）**：§6.4 vision filter 帧键
> → `frame_id / quality / duplicated / key_frame / reason`（`duplicated` 场景重复
> 布尔；`key_frame` 模型判定——同一场景最多一帧、取画质最高帧，过渡/黑屏场景
> 可为零；归一含守卫：quality=low 的 key 降级）；§6.4 正文旧键表（含 extract
> 字段）一并修正为 09-18 以来实际口径。详见
> [video_analysis.md](../video_analysis.md) §10。

> **修订裁定（2026-10-07，视频 filter schema 第三轮）**：§6.4 filter 帧键
> `duplicated` → `scene_id`（整数：同窗同场景同 id、不同场景不同 id；非法值 →
> 本帧独立场景）；新增场景级抑制——同窗同 `scene_id` 多 key 只保留帧索引最小
> 者、其余降级并写 warning log。详见
> [video_analysis.md](../video_analysis.md) §11。

> **修订裁定（2026-09-18，视频 extract schema 精简 + place）**：§6.4 vision
> extract 阶段——字段收敛为 `caption / place / objects / ocr / people`（删
> `scene / apps / events / actions`）；**新增 `place`**（根据图片推测地点，不确定
> 或是屏幕截图 → null）；video 事件 summary 拼装改为 `caption + place + ocr`
> （本文 §行 288/531 的旧口径作废）；图谱 scene/app 节点读取分支**保留**（旧
> payload 兼容），新数据不再产生这两类节点、place 不入图。详见
> [video_analysis.md](../video_analysis.md) §9。

> **修订裁定（2026-09-18，providers workspace_id 接入）**：§4.2/§4.3 provider
> 配置——`ProviderConfig` 增 `workspace_id`（仅 DashScope 语义，驱动
> `X-DashScope-WorkSpace` 头；与业务空间专属域名两值配套、互不派生；未配置/
> 非 DashScope 不带头）；dev yaml audio/vision/embedding/summary 四段统一切
> workspace 专属域名。详见
> [lvdata_workspace_extend.md](../lvdata_workspace_extend.md)。

> **修订裁定（2026-09-19，day_summary prompt 改版）**：§5.4.1/§6.5——LLM 输出
> 收敛为 `summary`/`topics` 两键（移除 LLM 产出 `stats`/`highlights`/`mood`；
> `stats` 仍为**本地计算**口径、落库不变）；user 消息头部注入
> `local_date`/`timezone`；`summary` 单字段承载整体总结（2-3 句）+
> 最重要事件/时刻条目（每条一句，**至少 1 条**、最多 10 条——2026-09-19 由 5 条上调并将"必列"写入 prompt；**无标题行**）；输入行标签优化：
> chat 渲染 `chat(<payload.role>)`、window/video 渲染 `video window`/`video frame`；
> 多设备日按设备分段（段头 `设备 <uuid8>：`、段内时间排序、单设备不变——仅 LLM
> 输入，落库/BFF 无感知）；**结构化改版（同日）**：LLM 输出 `summary`（2-3 句
> 总述）/`events`（条目数组，中间产物不落 doc）/`topics`（无 detail），
> `summary` 由程序拼接 `events` 条目；bff `topic_items()` 归一化兼容新旧 topics
> 形态（**2026-09-21 起 `topics` 元素为对象 `{topic, search_words}`，此前为字符串**）；**summary/events 解耦（同日二次调整）**：`doc.summary` 仅取 LLM 总述
> （取消拼接），`events` 独立落 doc 预留单独消费（当前无消费者）；**命名修订
> （同日三次）**：落库字段定名 `moments`（LLM 契约键不变）。存量行保留旧
> 字段（消费者按 key 读取，新旧共存兼容）；`generate(force)` 语义不变
> （全量重跑走手工置 pending，见方案文档）。详见
> [day_summary.md](../day_summary.md)。

> 状态：定稿（v2 实施基线）。
> 本文以 `lvdata_mcp_service_v2-d4p.md` 为决策基线（其全部架构决策已经评审确认），合并对 d4p/k3 两稿的评审结论；d4p/k3 两稿自此仅作存档。定稿相对 d4p 评审稿的修订逐条列于**附录 B**。
> 前置文档：[lvdata_mcp_service.md](./lvdata_mcp_service.md)（v1 定稿）、lvdata_inventory.md、lvdata_audio_analysis.md、lvdata_video_analysis.md、lvdata_summary_analysis.md、lvdata_agent_jobs_integration.md。本文所有 v1 行为引用均核对过当前代码（tools/lvdata-mcp、tools/data-analysis、services/bff、services/agent_relay、services/shared/config、configs/dev/server.yaml）。
> **v2 为干净切割：不保留 v1 兼容产物，不做数据回填迁移，不设兼容期。bff / Agent Relay / Android 随 v2 同步修改。**

---

## 1. 背景与目标

v1 的 lvdata-mcp 是一个「编排壳」：watcher 通过 subprocess 调用 `tools/data-analysis/` 下的 5 个独立 CLI（inventory / sync_agent_jobs / analyze_audio / analyze_video / summary）完成建档、分析与索引，自身只做轮询、入队、重试与 MCP 查询面。

v2 的核心变化：

1. **完全内部化**：建档（inventory）、agent jobs 变更检测（不再 PG 同步，直接读 `agent_jobs` 表）、音频/视频分析、摘要、索引全部在 lvdata-mcp 进程内以 asyncio 模块完成，不再 fork subprocess。逻辑参考现有 CLI（本文逐条引用其行为），但按进程内模块重新设计，不做逐行照搬。
2. **统一事件模型**：新增 `events` 表作为所有模态事件的单一事实来源（Single Source of Truth）；分析结果**直接写入 events 表**。v1 的查询面产物全部废弃（timeline.json / session_summary.json / summary.json / video_summary.json）；分析级审计 JSON 保留（audio/<segment>.json、video/windows|frames/*.json，与 v1 同格式），仅用于人工核查，不作为任何查询数据源。
3. **day_summary**：新增按本地日（timezone 感知）+ LLM 生成的日摘要表，为 mylife 日卡提供数据。
4. **摘要只做日粒度**：不再生成 session 摘要（`session_summary` 表与 v1 各 session 摘要文件全部废弃）；上层只消费 events + day_summary，recording session（`recording_sessions`/`rsession_id`）仅是存储层内部概念。
5. **YAML 配置**：新增 `lvdata_mcp.yaml`，格式与 livachat server config（`configs/dev/server.yaml`，schema 见 server_design.md §6.2）同构。
6. **bff 直读 sqlite 提前**：由于不保留兼容产物，bff 的查询面迁移（timeline 改读 events、日卡改读 day_summary，session 维度 API 废弃）与 v2 同步上线（见 §9.1）。
7. **agent_jobs 写入路径改道**：现有服务 **Agent Relay**（`services/agent_relay`，当前经 `AgentJobsRepository` 写 PG `agent_jobs`）改为直写 lvdata SQLite `agent_jobs` 表；lvdata-mcp 删除 PG 同步。写入契约见 §6.2.0，跨服务同步发布见 §9.2。

明确不做（本期）：

- 历史数据回填：v2 上线前的历史派生数据不迁移；events 从 v2 上线后的分析开始累积，历史数据如需入 events，通过重跑分析重建（§7）。
- period_summary 的生成逻辑（表结构本期定义，生成逻辑下一阶段，见 §8.2）。
- 升级与向后兼容：无 v1→v2 在线升级路径、无协议兼容层、无双格式服务期。

---

## 2. v1 现状摘要（与本文相关的部分）

> 详细行为见各前置文档；此处只列 v2 重构会触及的事实，均已核对代码。

### 2.1 数据布局

- 原始数据：`<root>/<device_uuid>/<session_id>/`，会话标记为 `session.json`（内含 `meta.recording_session_id`、`meta.device_uuid`、status/timestamps/config/stats），聊天为 `chat/history.jsonl`，媒体为 `audio/*.ogg`、`video/*.jpg`。文件名 UUIDv7，前 48 位 = epoch ms（`files.uuid_ts_ms` 的来源）。
- 派生数据（v2 仅保留 `audio/*.json`、`video/windows/*.json`、`video/frames/*.json` 作为分析审计 JSON 供人工核查，§6.3/§6.4；**其余全部废弃**）：`<root>/derived/<device>/<session>/` 下 `timeline.json`、`session_summary.json`、`session_summary.md`、`summary.json`、`video_summary.json`；跨会话报告 `<root>/derived/_reports/report.{json,md,html}`。
- SQLite（`<root>/lvdata_inventory.db`）：`sessions`（v2 改名 `recording_sessions`，见 §7）、`files`、`chat_messages`、`audio_analyses`、`video_analyses`、`agent_jobs`（v1 为 PG 镜像，v2 起由 Agent Relay 直写，§6.2）、`agent_jobs_sync_state`、`search_docs`（FTS5）、`mcp_watch_state`、`mcp_pipeline_runs`、`inventory_runs`、`summary_runs`。
- RAG 向量库独立：`<root>/lvdata_index.db`（`vec_chunks`，sqlite-vec vec0，cosine）。

### 2.2 分析厂商与模型（现状硬编码/环境变量）

| 能力 | 现状 | 说明 |
|---|---|---|
| 音频分析 | DashScope `qwen3.5-omni-plus`，env `DASHSCOPE_API_KEY` | OpenAI 兼容端点，streaming + json_object；input_audio 传 wav/ogg base64 |
| 视频/图像 | DashScope `qwen3.7-flash`，同上 env | 两阶段：filter（30s 窗口 + 关键帧判定）→ extract（关键帧 chunk=4 提取） |
| 摘要 LLM | DashScope `qwen3.7-flash`，同上 env | session_summary / report 共用 |
| 文本向量 | DashScope `text-embedding-v4`（1024 维），env `LVDATA_MCP_EMBEDDING_*` | 批量上限 10（百炼 API 实测约束，vec.py 注释） |

全部走 OpenAI-compatible 协议（`AsyncOpenAI` SDK）。重试策略一致：429/超时/连接/5xx 重试，指数退避 `2^n + rand(0,1)`，max 3 次；模型输出 JSON 用 `sanitize_json` 去 markdown fence。

### 2.3 v1 watcher 的 stage 链

`inventory → agent_jobs（全局）→ audio → video → align → reindex → summary → embed`，前 7 个阶段 subprocess 调 CLI，`reindex`/`embed` 进程内执行。失败按 (session, stage) 指数退避（`BACKOFF_DELAYS=(60,300,900)`，watcher.py），达上限暂停（`MAX_FAILURES=3`）。

### 2.4 bff 现状（v2 要迁走的读法）

`services/bff` 的 `LvdataStore` 是只读 facade：sqlite `mode=ro`（busy_timeout=5000）+ derived JSON + 媒体文件。day 分组是请求时现场算的：`_local_date(ts_ms, tz)`（`bff/timeline/service.py:22`）把 timeline 事件按 IANA 时区折成本地日期。时区校验 `validate_tz`（`bff/common/deps.py:48`）用 `zoneinfo.ZoneInfo`。日卡内容目前由 Android 端 `PeriodAggregator` 从 bff overview 的各 session 数据聚合（hotSlots/summaryLines/topics/images/hasPending，见 §9.1）。

### 2.5 Agent Relay 现状（v2 要改道的写路）

`services/agent_relay/job_persistence.py` 经 `AgentJobsRepository.upsert` 写 **PG** `agent_jobs`（alembic 0003 建表，`job_key` 唯一），upsert 自带单调守卫 `updated_at <= excluded.updated_at`（乱序到达在源头即存在，守卫必须保留）。lvdata-mcp v1 再经 `lvdata_sync_agent_jobs.py` 把 PG 镜像到 SQLite。v2 删除镜像段，Relay 直写 SQLite（§6.2.0）。

---

## 3. 总体架构 v2

### 3.1 进程内结构（单进程，asyncio）

```
lvdata-mcp (asyncio 单进程)
│
├─ server（Starlette + FastMCP streamable HTTP，v1 不变）
│    /mcp  /healthz  /readyz  + bearer auth + per-IP 限流
│
├─ watcher（v1 双 loop 保留）
│    poll loop:  scanner.scan() → jobs_watch.check() → dirty 判定 → 入队
│                → day-summary dirty 发现（轻量 SQL，§6.5）
│    work loop:  per-session 链（audio → video → reindex → embed）
│                → 全局 stage：day-summary 生成（§6.5）
│
├─ 内部化流水线模块（替代 subprocess CLI；输出落 events 表 + 分析审计 JSON）
│    scanner.py        ← lvdata_inventory.py 逻辑（§6.1）
│    jobs_watch.py     ← 新增：agent_jobs 表变更检测（§6.2）
│    analysis/audio.py ← lvdata_analyze_audio.py 逻辑（§6.3）
│    analysis/vision.py← lvdata_analyze_video.py 逻辑（§6.4）
│    summary/day.py    ← 新增：day_summary 生成与调度（§6.5）
│
├─ 索引（数据源 = events 表）
│    index.py（FTS5 search_docs）  vec.py（sqlite-vec）
│
└─ store（读路径：events / day_summary / files / recording_sessions / agent_jobs）
```

**loop 归属**（统一定稿口径）：dirty *发现*全部在 poll loop（轻量、纯 SQL/Python）；重活（LLM/embedding/重建索引）全部在 work loop。day-summary 的 dirty 发现在 poll loop 尾部，生成执行在 work loop 尾部作为全局 stage。两 loop 并发，所有跨 loop 的一致性靠水位与事务保证（§5.1 时间戳约定、§6.2 单事务、§6.5 快照水位）。

### 3.2 设计原则

1. **events 是 SSOT**：一切分析结果直接写 `events` 表。分析级审计 JSON（audio segment / video window+frame，§6.3/§6.4）保留用于人工核查，但不是查询数据源；v1 的查询面产物（timeline.json、各 session 摘要）全部废弃。timeline 查询 = 查 events 表。
2. **摘要入表**：day_summary 为 SQLite 表，JSON 内容存列内、schema 可扩展（不设 session 粒度摘要）。
3. **事件幂等**：`event_ref` 稳定唯一（沿用 v1 约定 `<rsession_id>:<type>:<source_id>`；agent_job 的铸造规则见 §5.1），重分析 = upsert + `updated_at` 刷新，驱动 day_summary 重生成。
4. **增量判定靠水位**：扫描（files.file_path 水位）、agent jobs（`agent_jobs.updated_at` keyset 水位）、day_summary（`events.updated_at` vs `generated_at`，快照语义 §6.5）三套水位互不耦合。
5. **外部调用即 IO**：LLM/embedding 调用全部 asyncio + 有界并发（semaphore），与 v1 CLI 的 concurrency 语义对齐。
6. **不保留兼容**：不做 v1 → v2 数据回填、不保留 CLI/env 兼容层、不继续写 v1 查询面 derived JSON（分析审计 JSON 除外，见原则 1）。v1 存量表（audio_analyses 等）v2 不读不写，新部署不创建。
7. **存储层与上层解耦**：`recording_sessions`/`rsession_id` 仅存在于数据存储与处理内部（scanner/分析/enrich/索引）；上层应用（bff API）与 MCP 工具只面向 events + day_summary，不暴露 session 维度。
8. **重试状态全部落盘**：失败计数、暂停状态、重试水位一律持久化（files.fail_count / day_summary.fail_count + generated_at / day_scan_state），进程重启不产生重试风暴，也不丢暂停状态。

---

## 4. 配置文件 lvdata_mcp.yaml

### 4.1 定位与加载规则

对齐 livachat 的 `shared/config/loader.py` 模式（§1 已核对）：

1. 路径解析：`--config` CLI 参数 → env `LVDATA_MCP_CONFIG` → `<configs>/<env>/lvdata_mcp.yaml`（跟随 livachat 的 `_project_root()` 约定，先找 `configs/` + `services/` 祖先目录）→ 缺省 `lvdata_mcp.yaml`（CWD）。
2. `${ENV_VAR}` 占位符递归解析（未设置即报错，fail-fast）。
3. env override：`LVDATA_MCP__` 前缀双层下划线路径覆盖（如 `LVDATA_MCP__WATCHER__POLL_INTERVAL_S=30`），语义与 `LIVACHAT__` 一致。
4. pydantic 解析 + 启动校验（`validate_security` 语义保留：非回环绑定必须配 token）。

CLI 仅保留 `--config` 与子命令（`serve` / `reindex` / `eval`），不再提供 v1 的逐项覆盖参数。

### 4.2 完整示例

```yaml
# lvdata_mcp.yaml — LivaChat LvData MCP Service v2 配置
# Schema：本文档 §4.3；占位符 ${ENV_VAR} 启动时解析；LVDATA_MCP__ 前缀 env 可覆盖任意嵌套键

# 分析产物输出语言（单值；默认 en）。MCP/REST 文案固定英文，不受此值影响。
# 支持 App 16 个 canonical code：
# en, zh-Hans, zh-Hant, ja, ko, ru, de, fr, it, es, pt, hi, id, th, vi, ar
language: en

server:
  host: "127.0.0.1"
  port: 8090
  token: "${LVDATA_MCP_TOKEN}"        # 非回环绑定必填
  log_level: INFO
  max_ip_conn: 32                     # 每 IP 并发请求上限（v1 同款限流中间件）

data:
  root_dir: /opt/lvdata               # lv data root（原始媒体 + 会话元数据）
  db_path: ""                         # 默认 <root_dir>/lvdata_inventory.db
  rag_db_path: ""                     # 默认 <root_dir>/lvdata_index.db（向量库）

watcher:
  poll_interval_s: 60                 # 轮询间隔（默认 60s）
  summarize_quiet_s: 600              # 摘要静默窗口：当天最新事件年龄超过该值才（重）生成（day_summary debounce）
  timezone: Asia/Shanghai             # 默认时区；timezones 表为空时的兜底
  timezones: []                       # 可选：显式写入 timezones 表的时区列表（见 §8.1）
  concurrency: 4                      # 分析类 LLM 调用有界并发
  max_files_per_poll: 200             # 单轮 poll 内 audio/vision 各处理的最大文件数（0 = 不限；积压分批，成本护栏 §10）
  day_summary_max_per_poll: 10        # 单轮 poll 内最多生成的 day_summary 份数（成本护栏 §10）
  llm_timeout_s: 300                  # 单次 LLM 调用超时
  # -- 分析参数（与 v1 CLI 默认值对齐）--
  audio_sample_rate: 16000            # ogg→wav 转码采样率；0 = 直传 ogg（--skip-convert 语义）
  video_window_s: 30                  # 窗口分组时长
  video_max_frames_per_call: 24       # 单次 filter 调用最大帧数
  video_extract_chunk_size: 4         # extract 阶段 chunk 大小
  max_image_size: 1280                # 图像长边上限（0 = 不缩放）
  max_chars_per_event: 300            # LLM 输入单事件截断
  stage_timeout_s: 1800               # 单阶段超时（v1 为 30min）
  # -- 审计表保留（方案稿 docs/audit_tables_retention_plan.md，§5.10/§6.6）--
  audit_retention_days: 14            # mcp_pipeline_runs 保留天数；0 = 关闭（含孤儿水位清理）
  analysis_runs_retention_days: 30    # analysis_runs 保留天数；0 = 关闭
  audit_prune_interval_s: 86400       # 清理周期（秒），watcher 启动后立即跑一次
  # -- video 帧保留（docs/data_clean.md）--
  video_retention_hours: 72           # 超期且无 video 事件的 ok 帧删除；下限 24（低于按 24）

providers:
  # workspace_id 与业务空间专属域名两值配套、互不派生（仅 DashScope 语义：驱动
  # X-DashScope-WorkSpace 头）；未配置 = 公网 dashscope 域名路径（不带头）。
  audio:                              # 音频分析：转写/情绪/事件
    vendor: dashscope
    base_url: https://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: llm-1tmsqifo51cwfadr
    model: qwen3.8-omni-flash
  vision:                             # 视频/图像分析：两阶段 filter+extract
    vendor: dashscope
    base_url: https://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: llm-1tmsqifo51cwfadr
    model: qwen3.7-flash
  embedding:                          # 文本向量；整块为空 = 关闭向量检索（离线模式）
    vendor: dashscope
    base_url: https://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: llm-1tmsqifo51cwfadr
    model: qwen3.7-text-embedding
    dim: 1024
  summary:                            # LLM：day_summary（period_summary 下一阶段共用）
    vendor: dashscope
    base_url: https://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: llm-1tmsqifo51cwfadr
    model: qwen3.7-flash
```

### 4.3 Schema（pydantic 模型）

```python
class ProviderConfig(BaseModel):
    vendor: str = "dashscope"   # dashscope|openai|openrouter|deepseek|ollama
    base_url: str = ""          # 空 = 按 vendor 取默认
    api_key: str = ""           # 支持 ${ENV_VAR} 占位符
    model: str = ""
    dim: int | None = None      # 仅 embedding 使用
    workspace_id: str | None = None  # 仅 DashScope：驱动 X-DashScope-WorkSpace 头
                                     # （业务空间专属域名配套，与 chat server 约定一致）

class WatcherConfig(BaseModel): ...   # 上表字段
class ServerConfig(BaseModel): ...
class DataConfig(BaseModel): ...      # root_dir / db_path / rag_db_path
class LvdataMcpConfig(BaseSettings):  # env_prefix="LVDATA_MCP__", env_nested_delimiter="__"
    language: str = "en"              # 分析产物语言；空/非法/未知 → en
    server: ServerConfig
    data: DataConfig
    watcher: WatcherConfig
    providers: ProvidersConfig        # audio/vision/embedding/summary: ProviderConfig
```

**vendor 的作用**：只提供 base_url / model 的默认值（openai-compatible 端点，现状全部走 `AsyncOpenAI`，无需 vendor 专属客户端）：

| vendor | 默认 base_url | 默认 model（按能力） |
|---|---|---|
| dashscope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | audio=qwen3.8-omni-flash；vision/summary=qwen3.7-flash；embedding=text-embedding-v4 |
| openai | `https://api.openai.com/v1` | 无（必须显式配 model） |
| openrouter | `https://openrouter.ai/api/v1` | 无 |
| deepseek | `https://api.deepseek.com` | 无（不支持视觉，仅 summary 可用） |
| ollama | `http://localhost:11434/v1` | 无 |

显式 `base_url`/`model` 优先于 vendor 默认值。api_key 缺失时：各能力启动期 fail-fast（对齐 v1 `build_provider` 行为）。

---

## 5. 数据模型

> 所有 DDL 均为 `IF NOT EXISTS` / 幂等 ALTER，启动时执行。v1 存量表 v2 不读不写（`recording_sessions`（原 `sessions`，启动幂等改名，§7）/`files`/`chat_messages`/`agent_jobs` 四个基础表除外，它们是 v1 与 v2 共用的数据底座）。

### 5.1 events 表（核心，SSOT）

```sql
CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ref   TEXT NOT NULL UNIQUE,           -- 稳定主键 <rsession_id>:<type>:<source_id>
    device_uuid TEXT NOT NULL,
    rsession_id TEXT NOT NULL,                  -- recording session UUID，= recording_sessions.rsession_id
    type        TEXT NOT NULL,                  -- chat|audio|window|video|agent_job
    ts_ms       INTEGER NOT NULL,               -- 事件时刻（epoch ms，UUIDv7 同源）
    ts_utc      TEXT,
    source      TEXT NOT NULL,                  -- 来源表：chat_messages|files|agent_jobs
    summary     TEXT,                           -- 事件文本摘要（LLM 输入 + FTS + embedding 共用）
    payload     TEXT NOT NULL DEFAULT '{}',     -- 完整事件 JSON，schema 按 type（可扩展）
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_events_rsession_ts ON events(rsession_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_ts         ON events(ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_type       ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_updated    ON events(updated_at);
```

> `idx_events_updated` 为 day-summary 增量发现（§6.5 `events.updated_at > last_scan_at`）与 jobs keyset 同源支撑——无此索引时每轮 poll 对 events 全表扫描，代价与表大小成正比（违反 §6.5「代价与增量成正比」）。`agent_jobs` 表同样带 `(updated_at, job_key)` 复合索引（keyset 水位），ddl 与 Agent Relay 建表 SQL 同构维护。

- `rsession_id` 语义 = 录制会话 UUID（recording_session_uuid），与 `recording_sessions.rsession_id`、`agent_jobs.recording_session_id` 一致；改名是为与 `chat_messages.chat_session_id`（聊天会话）明确区分。该列仅用于数据存储与处理内部（scanner/分析/enrich/索引），不对上层应用与 MCP 工具暴露（上层只面向 events + day_summary，§3.2.7）。
- `event_ref` 沿用 v1 约定（`search_docs.event_ref` / `vec_chunks.chunk_id` 同格式），保证 FTS/向量/eval golden 的引用稳定。
- **agent_job 事件的铸造规则**（定稿修订，消除评审发现的三连缺陷）：agent_job 事件**仅在 `rsession_id` 已知时创建**——Agent Relay 直写已带 `recording_session_id`，或 enrich 补填成功（§6.2；enrich 只允许 NULL→值，已填值以 Relay 为准不改写）。ref 于 rsession_id 首次确定时一次性铸造，此后不变。由此：
  - 不产生 NOT NULL 约束冲突（NULL 时不插行）；
  - 不存在 enrich 改值导致的 ref 漂移/孤儿事件（值只填一次）；
  - 始终未关联的 job 不进 events/timeline/day_summary，但仍可由 `list_jobs`（直查 `agent_jobs` 表，§12）检索——与 v1 `list_jobs` 覆盖未关联 job 的语义对齐。**2026-09-27 反转**：`list_jobs` 工具已彻底删除（未关联 job 自此无可查入口），见 [lvdata_mcp_tools.md](./lvdata_mcp_tools.md) 与 `issue_list.md` I-4。
- `summary` = 该事件的检索/LLM 文本，规则与 v1 `index._text_for` / `vec.chunk_text` 完全一致（audio：transcript+summary+emotions；video：caption+scene+apps+ocr+actions；window：window_caption；chat：text_content；agent_job：summary+result_text 截 2KB+fail_reason+status）。
- `payload` 按 type 定义（v1 已核对的字段）：
  - `chat`：`{role, text, chat_session_id}`
  - `audio`：音频分析 JSON 全量（language/transcript/summary/background_sounds/events/emotions/objects/people/quality，见 lvdata_audio_analysis.md）+ `segment_id, duration_s`
  - `window`：`{window_id, window_start_ms, window_end_ms, frame_count, window_caption, key_frame_count, static}`
  - `video`：帧提取全量（scene/caption/apps/objects/events/ocr/people/actions）+ `frame_id, window_id, key_rank`
  - `agent_job`：`{job_key, agent_name, status, summary, result_text(截断), fail_reason, has_result}`
- 重分析 = `INSERT ... ON CONFLICT(event_ref) DO UPDATE`，刷 `updated_at`（day_summary 增量判定的依据）。
- **timeline 排序约定**：timeline 是 events 的查询视图（无产物文件）；查询按 `(ts_ms, EVENT_PRIORITY, event_id)` 排序，`EVENT_PRIORITY = {chat:0, window:1, video:2, audio:3, agent_job:4}`（v1 同款常量，现存于 `tools/data-analysis/lvdata_summary.py` 与 `bff/common/store.py`，v2 迁入 lvdata-mcp 包内单一出处）。
- **时间戳格式约定（所有水位列强制）**：所有参与字符串比较的水位时间戳统一 ISO 8601 UTC——`YYYY-MM-DDTHH:MM:SS.sssZ`（'T' 分隔、毫秒精度、'Z' 后缀），TEXT 列直接字符串比较即得正确时序。**禁止混用** `datetime('now')` 的空格格式（`' '`(0x20) < `'T'`(0x54)）与 v1 `_now_iso()` 的 `+00:00` 微秒格式（`'+'`(0x2B) < `'Z'`(0x5A)，且微秒 6 位与毫秒 3 位不可比）——同一时刻的两种格式会判错方向，水位比较随之失效。Python 写入统一用 `datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")`，与 SQLite `strftime('%Y-%m-%dT%H:%M:%fZ','now')` 输出一致。**该约定同样是对 Agent Relay 的写入契约**（§6.2.0）；改名继承的存量 `agent_jobs` 行在启动改名时一并做格式归一化（§7）。

### 5.2 files 表增强（扫描目标）

存量列保留（file_id/rsession_id/file_type/file_ext/file_path UNIQUE/file_size/uuid_ts_ms/uuid_ts_utc/scanned_at；原 `session_id` 启动改名，§7），新增：

```sql
ALTER TABLE files ADD COLUMN device_uuid TEXT;      -- 冗余 recording_sessions.device_uuid（扫描直写）
ALTER TABLE files ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE files ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE files ADD COLUMN analyzed_at TEXT;
ALTER TABLE files ADD COLUMN error TEXT;
```

- `analysis_status ∈ pending|ok|failed|na`；`chat`/`meta` 类文件扫描即置 `na`（无需分析）。
- 取代 v1 用 `audio_analyses`/`video_analyses` 表判定 pending 的方式（watcher.detect_dirty 的 B2 回归问题随之消失——该逻辑当时为绕开状态列缺标记的空跑问题）。
- `fail_count` 是分析重试的持久化计数（§6.3/§6.4）：失败 +1，`fail_count >= MAX_FAILURES(3)` 即暂停该文件（不再被取批）；`trigger_analysis(force=true)` 重置 `fail_count=0` 并回 `pending`。成功置 `ok` 时清零。
- `audio_analyses`/`video_analyses` 存量表 v2 不读不写（可手动 DROP）；分析过程审计由 `analysis_runs` 承担。

### 5.3 analysis_runs 表（分析审计）

```sql
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL,           -- audio|video
    model         TEXT,
    status        TEXT NOT NULL,           -- ok|failed
    usage_input_tokens  INTEGER,
    usage_output_tokens INTEGER,
    latency_ms    INTEGER,
    error         TEXT,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_file ON analysis_runs(file_id);
```

### 5.4 day_summary 表

```sql
CREATE TABLE IF NOT EXISTS day_summary (
    timezone        TEXT NOT NULL,              -- IANA 名，如 Asia/Shanghai
    local_date      TEXT NOT NULL,              -- 本地日期 YYYY-MM-DD
    utc_offset_min  INTEGER NOT NULL,           -- 该 local_date 日首的 UTC 偏移（分钟；DST 感知，按日期存）
    day_start_ms    INTEGER NOT NULL,           -- 冗余缓存：该本地日 [起, 止] epoch ms（闭区间，= timeutil.day_bounds_ms 返回值）
    day_end_ms      INTEGER NOT NULL,
    day_summary     TEXT NOT NULL DEFAULT '{}', -- JSON，可扩展（§5.4.1）
    model           TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|failed
    event_count     INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,  -- 生成失败计数；>= MAX_FAILURES(3) 暂停，force 重置
    error           TEXT,
    generated_at    TEXT,                       -- 增量+重试水位：events.updated_at > generated_at ⇒ dirty（快照语义 §6.5；格式见 §5.1 约定）
    PRIMARY KEY (timezone, local_date)
);
CREATE INDEX IF NOT EXISTS idx_day_summary_status ON day_summary(status);
```

设计要点：

- **主键 (timezone, local_date)**：同一物理日在不同时区是不同行，各自独立生成。
- **utc_offset_min 按日期存**：夏令时地区同一时区不同日期的偏移不同，day_start/end 必须按「该日期的偏移」计算（`zoneinfo` 对 aware datetime 的 `timestamp()` 天然正确处理），不能只存一个固定 offset。`day_start_ms`/`day_end_ms` 直接复用 `timeutil.day_bounds_ms` 的返回值（注意：该函数返回**闭区间**末端 = 次日 0 点 −1ms，查询用 `BETWEEN`）。
- **generated_at 增量判定**：dirty 判定 = `day 内存在 events.updated_at > day_summary.generated_at 的事件`（或行不存在）。不用 max_event_id——事件可能乱序更新（旧 segment 重分析会 bump updated_at 但 event_id 小）。
- **generated_at 的快照语义**：成功生成时写**输入快照时刻 T0**（读取输入前取定，§6.5），而非写入完成时刻——否则生成期间并发落入的事件（updated_at 小于写时刻）会被水位吞掉、永不再触发重算。失败时写**尝试时刻**（兼作重试水位）——否则 `generated_at` 为 NULL 时 `events.updated_at > NULL` 恒 NULL，失败行永远不再被判定 dirty。
- **失败重试**：`status='failed' AND fail_count < 3 AND now - generated_at ≥ 退避间隔`（(tz, date) 粒度；间隔与 v1 `BACKOFF_DELAYS`(60/300/900s) 对齐，按 fail_count 取档；达 3 次暂停，`trigger_analysis` force 重置）。generated_at 落表保证重启后也不会每轮 poll 重试风暴（原则 8）。
- 当天 0 事件不落行（生成时发现 0 事件即跳过）。

#### 5.4.1 day_summary JSON schema（v1，可扩展）

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
  "summary": "这一天…（LLM 总述 2-3 句；不拼接）",
  "moments": ["最重要的事件或时刻（每条一句，≤10；LLM 输出键 events 的落库名；预留单独消费，当前无消费者）"],
  "topics": [{"topic": "主题名（≤5，可为空数组）", "search_words": "事件摘要里的原文连续片段（搜索用）"}],
  "usage": {"input_tokens": 12345, "output_tokens": 678}
}
```

`stats` 为**本地计算**口径（非 LLM 产出）：`events` = 过滤后进入 LLM 输入的事件数，`audio_segments_total` = 当日 audio 事件真实总数（含因 transcript 非空被排除的段，§6.5 输入去重）；`usage` 取 LLM 响应用量。

`summary` 为 LLM 总述（2-3 句，**不拼接**）；`moments`（LLM 输出键 `events` 的落库名，避与 `stats.events` 计数同名）为条目数组（程序截断 ≤10）**独立落 doc**、当前无消费者（预留以后单独消费）；`topics` 为**对象数组 `[{topic, search_words}]`**（主题名 ≤5、可为空数组；`search_words` = 事件摘要里的原文连续片段，供点击 chip 搜索，缺省/空白为 `null` → 客户端回退用 `topic` 名）。设计动因见 docs/android_timeline_topics.md §10、docs/bff_search_fts.md §10.2。

字段变更约定：新增字段只做**加法**（老版本消费者忽略未知键）；2026-09-19 结构化改版后 LLM 产出 `summary`/`events`/`topics` 三键（`stats` 恒为本地计算）；**2026-09-21 起 `topics` 元素由字符串改为对象**（`{topic, search_words}`）——存量行保留 `highlights`/`mood` 旧字段与字符串/`{topic, detail}` 旧 topics 形态，均经 bff `topic_items()` 归一化按 key 读取，新旧共存兼容。Android 日卡所需的 hotSlots/images/hasPending 为**确定性数据**，不进本 JSON，由 bff 从 events/files 实时派生（§9.1.2）。

### 5.5 timezones 表（本期建表；写入方见 §8.1）

```sql
CREATE TABLE IF NOT EXISTS timezones (
    timezone   TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    priority   INTEGER NOT NULL DEFAULT 0,   -- 生成顺序/优先级
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

- 语义：**需要生成 day_summary 的时区列表**（lvdata-mcp 为每个 enabled 时区独立生成 day_summary）。
- watcher 启动时把 `watcher.timezones` + `watcher.timezone`（兜底）seed 进来（`INSERT OR IGNORE`，不覆盖已有行，不刷 `updated_at`——config seed 不伪装成「最近使用」）。
- **写入方（决策已落地，timezone-support.md §6）**：livachat server session 模块直写——`session.start` 携带客户端时区（`config.timezone`），归一化后 upsert 本表，`updated_at` 即「最近使用」水位；lvdata-mcp 每轮 poll 只遍历**活跃时区集**（最近使用 + 配置默认，至多 2 个，§6.5/§8.2）。
- 与 `recording_sessions.timezone`（session.json 自带）的关系：`recording_sessions.timezone` 是录制时客户端上报的时区；`timezones` 表是「要为哪些时区生成摘要」的独立维度，两者不互写。

### 5.6 day_scan_state 表（day-summary 发现游标）

```sql
CREATE TABLE IF NOT EXISTS day_scan_state (
    timezone     TEXT PRIMARY KEY,
    last_scan_at TEXT NOT NULL DEFAULT ''    -- dirty 发现游标（§5.1 格式；'' = 全量首扫）
);
```

- 每 enabled 时区一行，记录该时区 dirty 发现扫描的水位（§6.5）：每轮只检查 `events.updated_at > last_scan_at` 的事件，代价与增量成正比，不做全历史扫描。
- 时区首次启用（seed/插入）时无行，按 `''` 处理——全历史事件首扫一次，该时区的历史 day 全部进入 dirty（分批消化，§6.5 批限）。
- 扫描结束后写**本轮扫描开始时刻**（不是结束时刻），扫描期间并发落入的事件（updated_at 晚于开始时刻）下一轮必然再见，不漏。

### 5.7 day_force 表（force_day_summary 载体）— **已废止（2026-09-15）**

```sql
CREATE TABLE IF NOT EXISTS day_force (
    timezone   TEXT NOT NULL,
    local_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (timezone, local_date)
);
```

- scanner 检测到 session 转为 `finalized` 时，对**全部 enabled 时区**写入该 session 收尾日的 force 行（`INSERT OR IGNORE`）——绕过 debounce，当天收尾即总结（§6.5）。
- day_summary 生成成功后删除对应行；生成失败保留（重试成功后再删）。

### 5.8 period_summary 表（本期建表；生成逻辑下一阶段）

```sql
CREATE TABLE IF NOT EXISTS period_summary (
    timezone       TEXT NOT NULL,
    period         TEXT NOT NULL,             -- week|month|year
    period_start   TEXT NOT NULL,             -- 周期起始本地日期 YYYY-MM-DD（周一/月首/1月1日）
    period_summary TEXT NOT NULL DEFAULT '{}',-- JSON，结构同 day_summary（可扩展）
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|failed
    model          TEXT,
    error          TEXT,
    generated_at   TEXT,
    PRIMARY KEY (timezone, period, period_start)
);
```

设计要点：

- **day_summary 一定优先存在**；week/month/year 是更高层总结，**不一定存在**（生成失败/未配置时允许缺行）。
- 两种生成路径（下一阶段实现）：① LLM 直接总结该周期内所有 day_summary；② 不调 LLM，直接把 day_summary 组装合并（拼接/规则聚合）。路径 1 质量高有成本，路径 2 零成本可兜底——period_summary JSON 内加 `"generated_by": "llm"|"assemble"` 区分。
- week 起点按 ISO 周（周一）定义，period_start 为周一日期（与 Android `PeriodAggregator` 的 ISO week key 语义一致）。

### 5.9 索引表（沿用，数据源 = events）

- `search_docs`（FTS5）列结构不变；v2 重建时数据源从「derived JSON + 各表」切到 **events 表**（text=`summary`，orig_text 同源）。
- `vec_chunks`（lvdata_index.db）同理，chunk 粒度 = event，`chunk_id`=`event_ref`。
- 好处：单一数据源，FTS/向量/LLM 三面永远一致；`rebuild_session` 从「读 5 类 JSON」简化为「查 events 表」。

### 5.10 index_state 表（reindex 水位；2026-09-19 新增）

```sql
CREATE TABLE IF NOT EXISTS index_state (
    target          TEXT PRIMARY KEY,   -- rsession_id；'' = 全局（全量重建）
    last_reindex_at TEXT NOT NULL       -- 成功重建水位，T0 快照语义（§6.6）
);
```

FTS 重建水位从审计表 `mcp_pipeline_runs` 抽出的专用状态表（与 `day_scan_state` / `flush_state` / `vec_state` 同纪律）；动机 = 审计表可随时按龄裁剪、与正确性解耦（方案稿 `docs/audit_tables_retention_plan.md`）：

- **'' 键 = 全局**：SQLite 非 INTEGER 主键允许多个 NULL 且 UNIQUE 视其互异（可插多行），全局行用 `''` 哨兵规避。
- **快照水位（T0）**：值为重建**入口**时刻（先于 `lag_sessions` 取件与任何 events 读取），写入值再 **−1ms**（毫秒粒度下 strict `>` 无法区分「同毫秒、读取之后」的更新——减 1ms 保证其下轮必判滞后自愈；声称覆盖「≤ 快照−1ms」，必然先于快照存在、读取必见）。`''` 只由全量重建写（增量写 `''` 会破坏不变式）；force / CLI 全量写 `''` 并**同事务清会话行**去冗余（读路径 `COALESCE(会话行, '')` 落空自然取新 `''`）。
- **回填**：存量库启动一次（`ensure_schema` 内，幂等）——表为空且 `mcp_pipeline_runs` 存在时，按 target 取最新 ok reindex 的 `finished_at`。漏回填 = 首次判全量滞后一次（自愈）。
- **读取**：`index.lag_sessions`（增量取件 + `get_status.lag_sessions` 单一出处）；`get_status.last_rebuild_at` = `MAX(last_reindex_at)`。孤儿会话行（events 已消失）由 watcher 清理任务清、`''` 行恒留。

---

## 6. 内部化流水线设计

> 每节标注「逻辑参考」的 CLI 与其行为核对点。所有阶段进程内 asyncio 执行，写库经短事务（WAL）。失败重试：文件/日粒度持久化计数（files.fail_count / day_summary.fail_count，MAX_FAILURES=3 暂停、force 重置，原则 8）——这是 v1 `(session, stage)` 粒度 `_run_with_backoff` 语义在 v2 无 subprocess 形态下的对应物，粒度从 stage 细化到工作单元。

### 6.1 scanner.py — 建档（替代 lvdata_inventory.py）

逻辑参考 `lvdata_inventory.py`（已核对）：

1. 遍历 `<root>/<device>/<session>/`，`session.json` 存在才视为会话；rsession_id/device_uuid 取自 `session.json` 的 `meta.recording_session_id`/`meta.device_uuid`（缺 device_uuid 报错，与 v1 一致）。
2. file_type 由 session 顶层子目录推断：`audio`→audio、`video`→video、`chat`→chat、`session.json`→meta，其余跳过。
3. 文件名 UUIDv7 → `uuid_ts_ms`/`uuid_ts_utc`（解析失败置 NULL，无其他回退——v1 行为）。
4. `files` upsert（`ON CONFLICT(file_path) DO UPDATE SET file_size, scanned_at`）+ 新增写 `device_uuid`、`analysis_status`（chat/meta='na'，audio/video 新文件='pending'，存量文件保留原状态）。
5. `recording_sessions` `INSERT OR REPLACE`（session.json 全量字段）+ `chat/history.jsonl` → `chat_messages`（msg_id 取 `ref_id`，fallback `{rsession_id}:line{n}`）。
6. mtime 跳过优化（session.json + history.jsonl 均 ≤ scanned_at 则跳过该 session，v1 同款）；**不删除**缺失文件/会话的行（v1 行为，保持一致）。
7. 新 chat 消息 → 直接 `INSERT` 对应 chat 事件（§5.1 payload.chat）入 events（chat 无需分析）；**chat 文件（history.jsonl）的 new/updated 与 audio/video 同路径进入本轮 new/updated 列表（dirty 判定输入）→ 该 session 入队**（§6.7）→ reindex/embed 无条件重建 → 新 chat 消息进入检索；day_summary 由 events.updated_at 水位自动重生成（§6.5）。事件先落表、链后执行（同轮 poll 内顺序天然正确）。
8. session 状态转为 `finalized`（session.json status 变化）→ 写 `day_force` 行（§5.7，全部 enabled 时区、收尾日）。

### 6.2 jobs_watch.py — agent jobs 变更检测（替代 PG 同步）

#### 6.2.0 Agent Relay 写入契约（跨服务，随 v2 同步发布）

v2 不再从 PG 同步：`agent_jobs` 表由 **Agent Relay 直接写入**（现有服务 `services/agent_relay` 的写路改造，§2.5），lvdata-mcp 只检测表内变更并消费。写入方配置位于 livachat server config（`configs/dev/server.yaml`）**顶层 `sqlite:` 段**（2026-08-31 层级重构：原 `agent_relay.job_pg` 提升为顶层，对齐 `redis:`/`postgres:`；env 前缀 `LIVACHAT__SQLITE__*`）：`enabled` / `lvdata_db_path`（<root_dir>/lvdata_inventory.db）/ `max_queue_size`。契约：

1. **schema**：按 v1 镜像列写入（改名后 `pg_updated_at` → `updated_at`，§7）；`job_key` 唯一。
2. **单调守卫保留**：upsert 必须为 `INSERT ... ON CONFLICT(job_key) DO UPDATE ... WHERE agent_jobs.updated_at <= excluded.updated_at`（同值重写幂等无害，取 `<=` 即可；与 PG 写路现状同款守卫语义一致）——乱序到达在 Relay 源头即存在，改直写 SQLite 后乱序条件不变，**守卫不得删除**。
3. **时间戳格式**：`updated_at` 及一切时间列必须写 §5.1 规范格式（ISO 8601 UTC、'T'、毫秒、'Z'）。
4. **recording_session_id**：Relay 已知即写；未知留 NULL，由 lvdata enrich 兜底（只允许 NULL→值）。
5. **写入方纪律**：Relay 是 `agent_jobs` 行唯一写入方；lvdata-mcp 仅经 enrich 更新 `recording_session_id` 一列、不 touch `updated_at`。
6. **部署前提**：Relay 与 lvdata-mcp 同机/共享 `<root_dir>` 文件系统；两端均开 WAL + `busy_timeout ≥ 5000ms`。

#### 6.2.1 检测与 enrich

enrich 逻辑参考 `lvdata_sync_agent_jobs.py`（已核对）：

1. 变更检测：keyset 分页读 `agent_jobs`（`WHERE (updated_at, job_key) > (?, ?)`，batch 5000；`updated_at` = 原 `pg_updated_at`，§7 改名并做格式归一化），水位持久化于 `agent_jobs_sync_state`（表结构不变；`last_pg_updated_at`/`last_job_key` 角色由 PG 同步游标改为 SQLite 读取游标）。
2. enrich（兜底）：`recording_session_id` 缺失的行补 enrich——`chat_messages.chat_session_id = agent_jobs.session_id` → `recording_session_id`（`agent_jobs.session_id` 是 chat session id，沿用 PG 命名不改名）；fallback 时间窗匹配（device 小写匹配 + requested_ts_ms ∈ [started_at, finalized_at∨last_active_at∨2^63)，最早开始者胜）。Agent Relay 写入已带 `recording_session_id` 则直接采用；enrich 回写只更新该列、**不动 `updated_at`**（水位只反映 Agent Relay 写入，避免自触发重检测），仍为 NULL 的行每轮重试（v1 行为）。
3. **单事务提交**（定稿修订，消除崩溃窗口）：enrich 回写 + agent_job 事件 upsert（下条）+ `agent_jobs_sync_state` 水位推进 + `pending_affected_sessions` 写入，**同一事务**提交。崩溃即整体回滚，下轮按旧水位重来，无「水位已过、事件未写」的丢失窗。
4. **affected sessions 交付**：保留 `pending_affected_sessions` 持久化（崩溃恢复语义，v1 已论证）；检测完成后进程内直接入队（补跑 reindex/embed；day_summary 由水位自动触发）。
5. 新 upsert / `recording_session_id` 首次确定的 job → 按 §5.1 铸造规则 **upsert agent_job 事件**入 events（type=agent_job，rsession_id=recording_session_id，ts_ms=requested_ts_ms）。仍为 NULL 的 job 不产生事件（§5.1）。
6. 不再连 PG：`postgres.dsn` 配置、psycopg3 依赖、DSN 回落链全部删除。

### 6.3 analysis/audio.py — 音频分析（替代 lvdata_analyze_audio.py）

逻辑参考 `lvdata_analyze_audio.py`（已核对，lvdata_audio_analysis.md）：

1. 取 `files` 中 `file_type='audio' AND analysis_status='pending' AND fail_count < 3`（join recording_sessions status ∈ active/finalized），单轮上限 `watcher.max_files_per_poll`，有界并发 `watcher.concurrency`。
2. `ffmpeg -i <ogg> -ar 16000 -ac 1 -f wav` 临时目录转码（`audio_sample_rate=0` = 直传 ogg；v1 的「400 降级转码重试一次」保留）。
3. provider = `providers.audio`，`AsyncOpenAI` streaming + `response_format=json_object`，`input_audio` base64 data URL + 固定分析提示词，输出 schema = v1 `SEGMENT_KEYS`（language/transcript/summary/background_sounds/events/emotions/objects/people/quality）。单次调用超时 `watcher.llm_timeout_s`。
4. 重试：429/超时/连接/5xx 重试（`2^n+rand(0,1)`，max 3），schema 违规重试、末次归一化（v1 行为）。
5. 结果处理：
   - **审计 JSON**：每个 segment 的分析结果写 `<derived>/<device>/<session>/audio/<segment_id>.json`（与 v1 同格式，仅人工核查用，不作为查询数据源）——静音判定与失败重试的结果同样照写（含判定依据）。
   - **静音过滤**：transcript/summary/events/emotions/objects 等全空 ⇒ 判定为静音/无有效内容，**不写 events**（无可检索事件）；审计 JSON 照写供人工核查。
   - **events upsert**（非静音）：event_ref=`<rsession_id>:audio:<segment_id>`，summary=transcript+summary+emotions 拼装文本，payload=全量 JSON。
   - 不产出 v1 的 `summary.json`（音频会话摘要，会话级摘要已整体移除）。
6. `files.analysis_status='ok'` + `analyzed_at` + `fail_count=0`（静音同样算分析完成）；`analysis_runs` 记审计。失败 → 'failed' + error + `fail_count+1`（达 3 暂停，§5.2）。

### 6.4 analysis/vision.py — 视频/图像分析（替代 lvdata_analyze_video.py）

逻辑参考 `lvdata_analyze_video.py`（已核对，lvdata_video_analysis.md）：

1. 取 `file_type='video' AND analysis_status='pending' AND fail_count < 3`，两阶段（v1 默认 `--pipeline two-step`）：
   - **filter**：按 `uuid_ts_ms` 排序，`video_window_s`(30s) 窗口分组（新窗口触发：`ts - win_start > 30s` 或帧数 ≥ `video_max_frames_per_call`(24)）；`window_id` = 首帧文件名 stem（确定性/可续跑，v1 行为）；单次多图调用（模型以 "frame 0/1…" 文本锚点索引），输出逐帧 `quality/scene_id/key_frame/reason` + `window_caption`（2026-10-07 第三轮：`duplicated` → `scene_id` 整数——同窗同场景同 id；同窗同 id 多 key 只留帧索引最小者并写 warning log；`key_frame` 语义/守卫/静窗防御沿用第二轮）。图像 `data:image/jpeg;base64,…`，长边 > `max_image_size`(1280) 时 Pillow LANCZOS 缩放。
   - **extract**：关键帧按 `video_extract_chunk_size`(4) 分 chunk，提取 `caption/place/objects/ocr/people`。
2. `extra_body={"enable_thinking": False}`（400 时降级重试一次，v1 行为）。
3. 结果处理：
   - **审计 JSON**：窗口 filter 结果写 `<derived>/<device>/<session>/video/windows/<window_id>.json`、关键帧 extract 结果写 `video/frames/<frame_id>.json`（与 v1 同格式，仅人工核查用，不作为查询数据源）。
   - **events upsert**：每个窗口 → `window` 事件（ts=window_start_ms，summary=window_caption）；每个关键帧 → `video` 事件（summary=caption+scene+apps+ocr+actions 拼装）。
   - 不产出 v1 的 `video_summary.json`（会话级摘要已整体移除）。
4. `files.analysis_status` + `fail_count` 按 §6.3.6 同款语义推进；`analysis_runs` 审计。
5. 窗口/帧级并发 = `watcher.concurrency`，逐窗口/逐 chunk 提交（v1 语义）。
6. 中间态（窗口内逐帧判定、关键帧集合）在内存中流转；进程重启后靠「window_id=首帧 stem + events 幂等 upsert」续跑（v1 确定性分组语义保留）。

### 6.5 summary/day.py — day_summary 生成与调度（新增）

这是 v2 的核心新逻辑。

**dirty 发现**（poll loop 尾部，轻量）：

```
for tz in enabled timezones（timezones 表）:
    cur = day_scan_state[tz].last_scan_at          -- 无行按 ''
    T_scan = now()                                  -- 本轮扫描开始时刻（先取定）
    candidates = SELECT ts_ms FROM events WHERE updated_at > cur
    dirty_days = { local_date(ts_ms, tz) for each candidate }
        ∩ { days 无 day_summary 行 OR events.updated_at > generated_at }   -- 逐 day PK 查询核对
    dirty_days ∪= { (tz, d) for day_summary 中 status='failed' 且退避期满的行 }   -- 失败重试（§5.4）
    day_scan_state[tz].last_scan_at = T_scan        -- 写开始时刻，不漏并发（§5.6）
    for d in dirty_days（按日期升序，单轮上限 day_summary_max_per_poll，余者下轮）:
        if (tz, d) ∈ day_force: 跳过 debounce，直接入队
        elif 该 day 内最新事件年龄 < summarize_quiet_s: 跳过（debounce，等数据沉降）
        else: 入队生成
```

- `local_date()` = 现有 `timeutil.ms_to_local` 的日期部分（`zoneinfo` 处理，DST 正确）。
- **debounce 语义**：与 v1 session summary 的静默窗口同哲学——正在产生的当天不急着总结；`summarize_quiet_s`（默认 600s）内无新事件才生成。`day_force` 行（session finalized 时由 scanner 写入，§5.7/§6.1.8）绕过 debounce，当天收尾即总结。

**生成**（work loop 尾部，全局 stage；每 (tz, date) 一份）：

1. **先取输入快照时刻 T0 = now()**（定稿修订，消除 TOCTOU 竞态）。
2. 读输入：该本地日全部 events（跨 session）`WHERE ts_ms BETWEEN day_start_ms AND day_end_ms AND updated_at <= T0 ORDER BY ts_ms, EVENT_PRIORITY, event_id`——`updated_at <= T0` 保证输入集合与水位严格一致。
3. 渲染 LLM 输入：user 消息头部含 `local_date`/`timezone`，随后每事件 `[HH:MM] label: summary（截断 max_chars_per_event）`（label：chat → `chat(<payload.role>)`，缺 role 裸 `chat`；window/video → `video window`/`video frame`；其余类型原样；**多设备日按设备分段——段头 `设备 <uuid8>：`、段间空行、段序=首现序、段内时间排序；单设备日不分段、与旧格式逐字一致**）；无 chunking（单日事件量预估 < v1 单 session 16KB 级别；超限策略见 §14）。system prompt 要求输出 §5.4.1 schema（`summary`/`events`/`topics` 三键；`events` 与 `summary` 由程序拼接为最终 day_summary 后落 doc）。
4. `providers.summary` 调用（json_object，超时 `llm_timeout_s`）→ 校验必需键。
5. upsert `day_summary`：`generated_at = T0`（**快照时刻**，不是写完时刻——生成期间并发落入的事件 updated_at > T0，下轮 dirty 必中，无丢失窗）；`utc_offset_min`/`day_start_ms`/`day_end_ms` 复用 `timeutil.day_bounds_ms`（DST 按日期正确；注意返回值为闭区间）；`event_count`、usage、model 落列；`status='ok'`、`fail_count=0`；删除对应 `day_force` 行。
6. 失败：`status='failed'` + error + `fail_count+1`，`generated_at`=尝试时刻（重试水位，§5.4）。

**DST 边界**：`timeutil.day_bounds_ms` 已核对（naive datetime + `replace(tzinfo=ZoneInfo)`，`.timestamp()` 按该日期生效偏移换算），直接复用；需补 DST 边界测试（如 America/New_York 的 3 月/11 月切换日），极少数午夜发生 DST 跳变的时区（午夜歧义/不存在）作为已知边界记录。

### 6.6 索引与向量（数据源 = events）

- `reindex`（per-session / 全量）：读 events 表 → `search_docs` FTS（CJK bigram 逻辑不动）+ `vec_chunks` embed（embedding provider 批量 10 上限不动——百炼实测约束，换 vendor 后按新 vendor 调整）。
- watcher 的 reindex/embed 调度点不变（session 处理链尾部）。
- **水位（§5.10 index_state）**：成功才写，T0 取在入口（先于取件与任何 events 读取）、写入值 = T0−1ms——快照语义，修复「重建期间更新的 event 被完成时刻水位吞掉」的读-写竞态（方向保守：`events.updated_at > 水位` → 下轮重判滞后、自愈）。force / CLI 全量写 `''` + 清会话行；增量逐会话写各自行、不写 `''`；空跑不推进。`get_status.last_rebuild_at` 随之变化：从「最后一次 reindex 运行成功时刻（空跑也推进）」变为「最后一次实际重建的 T0−1ms」。

### 6.7 watcher stage 链（v2）

```
poll loop:
  scanner.scan()                      # 内部化 inventory（stage: inventory）
  jobs_watch.check() → affected 入队  # 读 agent_jobs 表变更（stage: agent_jobs，全局）
  detect_dirty()                      # 双信号：scanner new/updated 文件（含 chat，§6.1.7）
                                      # + files.analysis_status='pending'（§5.2）
  day-summary dirty 发现（§6.5）       # 全局，无 session 维度
work loop:
  per-session 链: audio → video → reindex → embed
  全局 stage: day_summary 生成（§6.5）
```

`mcp_pipeline_runs` 保留，stage 名称：`inventory / agent_jobs / audio / video / reindex / embed / day_summary`（`align`、`summary`、`report` 移除）；`session_id` 列语义推广为通用 target（rsession_id / `tz:local_date` / NULL），列名随 §7 改 `target`。`mcp_watch_state` 不再使用（v1 文件水位职责由 scanner 的 new/updated 返回 + files 表承担）。运行行为**纯审计**（reindex 水位已抽离至 §5.10）——watcher 进程内定时按龄清理（`audit_retention_days` 默认 14 天；running 僵尸行按 `max(2×stage_timeout_s, 3600s)` 窗口裁，stage 超时同样产生僵尸行）、`analysis_runs` 保留 30 天（`analysis_runs_retention_days`）。

- 入队的 session 链上：audio/video 无 pending 文件即 no-op；reindex/embed 无条件重建——任何事件源（chat / agent_job / 重分析）最终都经「入队 → 链」这条唯一传播路径，无旁路。

---

## 7. 历史数据与初始化

**不做 v1 → v2 数据迁移**：

1. 启动建表（§5 DDL 幂等执行）+ `timezones` seed。
2. v1 存量派生数据（derived JSON 旧内容、audio_analyses/video_analyses、mcp_watch_state、search_docs/vec_chunks 的旧内容）**一律忽略**：v2 不读、不转换（重分析会以 v2 审计 JSON 覆盖同路径文件）；`search_docs`/`vec_chunks` 从 events 全量重建（events 为空时即空索引，随分析推进自然填充）。
3. `files` 存量行：analysis_status 默认 'pending' ⇒ **v2 上线后会重新分析全部历史音视频文件**，events 由此重建完整（原始媒体文件在，重分析即重建；成本与限速见 §10）。
4. 基础表（`recording_sessions`（原 `sessions`）/files/chat_messages/agent_jobs）继续共用，不重建。
5. `tools/data-analysis/` CLI 保留用于离线/排障，但 v2 服务不调用；**禁止** CLI 与 v2 服务同时写同一库（写入方单一原则，运维文档注明）。
6. **表/列改名（一次性、幂等，不视为数据迁移）**：启动时执行 `sessions` → `recording_sessions`，基础表中指代录制会话的 `session_id` 列（`sessions`/`files`/`chat_messages`）→ `rsession_id`，`agent_jobs.pg_updated_at` → `updated_at`（Agent Relay 写入的变更水位，§6.2）、`agent_jobs.synced_at` 废弃（v2 不读不写）（SQLite `ALTER TABLE ... RENAME`；新部署直接按新名建表）。`chat_messages.chat_session_id`、`agent_jobs.session_id`（chat session id，沿用 PG 命名）、`agent_jobs.recording_session_id` 不动。**改名同事务内对 `agent_jobs.updated_at` 存量行做格式归一化**（PG 来源的 `+00:00`/空格/微秒格式 → §5.1 规范格式），保证 keyset 水位字符串比较正确。改名后旧版 CLI/bff 与该库不再兼容（表/列名不匹配），随 v2 同步上线（§9）覆盖，无兼容期。

---

## 8. 下一阶段预留（本期只建表/留接口）

### 8.1 timezones 表写入方（决策已落地：livachat server 直写）

- **决策（timezone-support.md §6）**：采用 ② 变体——livachat server（session 模块）在 `session.start` 时直写 `timezones` 表，仅 upsert 单行；触发点天然在 gateway，避免引入 lvdata-mcp HTTP 依赖（① 被否决的理由：可用性耦合 + 重试复杂度）。
- 纪律（对齐 §6.2.0 agent_jobs 直写契约）：只写 timezones 表；只写**规范形**时区（`shared.timeutil.canonicalize_timezone` 唯一入口）；时间戳用 SQL 侧 strftime（与列 DEFAULT 同源）；DB 文件不存在则跳过不建库；每写连接 `PRAGMA busy_timeout=5000`，不重复设置 `journal_mode=WAL`；异常只 log 不阻断会话。
- 新时区启用即触发该时区全历史 day 的首扫（day_scan_state 无行 → `''` 起扫，§5.6），按 `day_summary_max_per_poll` 分批消化，每历史日每时区一次 LLM 调用。
- lvdata-mcp 每轮 poll 只遍历活跃时区集（最近使用 `ORDER BY updated_at DESC, timezone` + 配置默认 `watcher.timezone`，至多 2 个），非活跃时区停止新生成、行保留、再激活续跑（timezone-support.md §8.2/§8.3）。

### 8.2 period_summary 生成（周/月/年）

- 本期只建表。下一阶段实现两条路径（§5.8）：LLM 总结 / day_summary 组装合并。
- **组装合并是兜底**：day_summary 数据一定存在（§6.5 调度保证），周/月/年卡在无 period_summary 时可由 bff/客户端直接聚合 day_summary 渲染（Android 现有周/月/年卡折叠逻辑由 PeriodAggregator 改读 day_summary 后延续）。

---

## 9. 受影响的周边（随 v2 同步修改，无兼容期）

### 9.1 bff（必改，与 v2 同步上线）

#### 9.1.1 查询面迁移（最小集）

| bff 现状 | v2 改动 |
|---|---|
| `LvdataStore.timeline_events()` 读 timeline.json | 改为查 `events` 表（本地日/时间范围过滤 + `(ts_ms, EVENT_PRIORITY, event_id)` 排序；session 维度不再暴露给上层） |
| `read_derived(sid, "session_summary")` | **删除**——session 摘要不再生成，上层只消费 events + day_summary |
| `build_overview` 请求时现场按 `_local_date` 分 day | 日卡 summary 改读 `day_summary` 表（按请求时区查行）；day 分组可保留现场计算（events 表查询快）或改查 day_summary 主键，实现时择一 |
| `analysis_state()`（timeline/summary 的 ok/pending/missing） | 改查 `files.analysis_status`（session_summary 维度删除） |
| 媒体文件服务（audio/video/jpg） | 不变（原始文件仍在 `<root>/<device>/<session>/` 下；session 路径仅作内部媒体定位，不对上层暴露） |

- 保留 `mode=ro` + busy_timeout（只读方，不引入写冲突）。
- `LvdataStore` 的 session 维度读法（list_sessions/get_session 等）降级为内部实现（媒体路径解析、时间范围定位），不再对上层暴露 session 维度 API/字段；表/列名跟随改名（§7）。

#### 9.1.2 日卡确定性字段的服务端化（对齐 Android `PeriodDigest`）

Android 日卡现有字段中，`summaryLines/topics` 由 day_summary JSON 供数；其余为**确定性数据**，bff 在 overview 响应中实时派生（无需 LLM、不落库、无陈旧问题）：

- **hotSlots**（活跃时段）：当日 events 按 ts 排序、间隔 > 5min 切段（对齐 Android `PeriodAggregator.MERGE_GAP_MS=5min` 语义），合并后取 ≤3 + latest——把现有客户端逻辑服务端化。
- **images**（卡片缩略图）：当日 `type=video` 事件按 `json_extract(payload,'$.key_rank')` 升序取前 N（day3/week6/month9/year15 粒度配额沿用 Android 现状），media URL 由 payload.frame_id 经内部 session 路径解析。
- **hasPending**：当日时间范围内 `files.analysis_status ∈ ('pending','failed')` 计数 > 0（实时查询；落库会随分析推进而陈旧，故不存）。
- **replay 锚定改造**：session 维度不再暴露，现有以 `sessionId` 定位的 replay 入口（HotSlot/PeriodImage 的 sessionId）改为 **ts_ms / event_ref 锚定**；bff 与 Android 同步修改（无兼容期）。

### 9.2 其他组件

| 组件 | 影响 | 动作 |
|---|---|---|
| **Android mylife** | 数据面变 events + day_summary：日卡 summary 数据源变 day_summary，hotSlots/images/hasPending 由 bff 派生（§9.1.2），session 维度字段废弃，replay 重锚 ts_ms；周/月/年卡 → period_summary（下一阶段） | 随 v2 同步修改，无协议兼容期 |
| **lvdata-mcp 自身** | watcher/pipeline/store/tools/index/vec/config 全改 | 本文档 |
| **Agent Relay** | `agent_jobs` 表写入方，写路由 PG 改道 lvdata SQLite | 按 §6.2.0 契约改造（保留单调守卫、时间戳格式契约、同机部署 + WAL/busy_timeout）；与 v2 同步发布 |
| **reindex CLI** | 重建源 = events（FTS + vec） | §6.6 |
| **eval CLI** | golden 的 event_ref 约定不变（v1 与 v2 同格式）→ 直接可用；注意：golden 中若含**当时未关联**的 agent_job 引用，v2 下不产生对应事件（§5.1 铸造规则），需在 golden 集中剔除 | 无改动 |
| **tools/data-analysis CLIs** | 不再被服务调用 | 保留；§7.5 并发写库禁令 |
| **运维/部署** | 启动参数从 env-only 变 yaml + env override；`DASHSCOPE_API_KEY` 等 env 经 `${ENV_VAR}` 占位符沿用 | systemd 单改动：加 `--config` |
| **lvdata_index.db（RAG）** | chunk 源切 events；schema 不变 | 重建即可 |
| **derived 目录** | v2 只写分析审计 JSON（audio/<segment>.json、video/windows|frames/*.json，人工核查）；v1 查询面产物（timeline.json、session 摘要等）不再写入；历史文件保留在盘上（不删除） | 无 |

---

## 10. 成本与限速

v2 的 LLM 成本集中在两处：**上线时一次性历史重分析**（§7.3）与**常态运行的 day_summary**。必须有预算、护栏与灰度步骤。

### 10.1 一次性成本估算（上线前必做）

上线前用 SQL 直接估算并写入运维 runbook：

- 音频：文件数 × 1 次 audio 调用（`SELECT count(*) FROM files WHERE file_type='audio'`）。
- 视频：窗口数 × 1 次 filter 调用（`ceil(视频文件数 / 平均每窗口帧数)` 估算）+ 关键帧数/4 × 1 次 extract 调用。
- 日摘要：历史天数 × enabled 时区数 × 1 次 summary 调用。

### 10.2 限速旋钮（已入配置，§4.2）

| 旋钮 | 作用 |
|---|---|
| `watcher.concurrency` | 分析类 LLM 调用的全局有界并发 |
| `watcher.max_files_per_poll` | 单轮 poll 分析文件上限——历史重分析分批消化，单轮时长有界 |
| `watcher.day_summary_max_per_poll` | 单轮 poll 日摘要生成上限——历史 day（重分析回填产生）与新时区首扫分批消化 |
| `watcher.summarize_quiet_s` | 进行中的当天的重算频度（debounce） |
| `watcher.llm_timeout_s` / `stage_timeout_s` | 单次调用 / 单阶段超时 |

### 10.3 灰度策略

1. 先用独立实例 + `root_dir` 指向数据子集（或单设备软链）做冒烟，验证 events/day_summary 产出质量。
2. 全量上线时保持默认批限，观察 `analysis_runs` 的 usage/latency 与失败率，再调 `max_files_per_poll`/`concurrency` 提速。
3. day_summary 降本（一天多 session 时重复总结相近内容）首版接受；后续优化见 §14。

---

## 11. 实施计划

- **P0 — 配置与骨架**：yaml loader（§4）+ pydantic schema + 建表 DDL + `--config` 参数；启动打印生效配置（对齐 livachat 行为）。
- **P1 — 内部化建档与 jobs 检测**：scanner.py、jobs_watch.py（含 §6.2.1 单事务）替换对应 subprocess stage；events 表 chat/agent_job 事件写入；watcher dirty 判定切 files.analysis_status。**与 Agent Relay 的 §6.2.0 契约改造联调**（Relay 可先双写 PG+SQLite 过渡，切干净后删 PG 写路——过渡形态由 Relay 侧决定，不在本文约束）。
- **P2 — 内部化分析**：analysis/audio.py、analysis/vision.py（events upsert + files 状态机 + analysis_runs）；watcher 链切换。
- **P3 — 日摘要**：summary/day.py（day_scan_state 发现 + 快照水位生成 + day_force）；`get_day_summary` MCP tool。
- **P4 — 索引切换 + bff 迁移 + 收尾**：FTS/vec 数据源切 events；bff 查询面迁移与日卡派生（§9.1）；MCP tools 适配（§12）；下线 derived JSON 读路径；reindex/eval 调整；测试适配（tests/ 下 5 个测试文件）。
- **上线门禁**：P4 完成后先跑 §10.1 成本估算与 §10.3 灰度第 1 步，签字后切正式实例。

## 12. MCP tools（v2 集合）

> **2026-09-27 更新（工具集收敛 + §8 二改）**：对外只保留 **5 个只读 tools**——`search` /
> `semantic_search` / `get_timeline` / `get_event` /
> **`get_recent_events(days, timezone)`**（新增：近 N 个本地日清单，days=1 今天、上限 31；
> 轻量投影、按日分组日倒序、时间可读 `timestamp`）。`get_timeline` 改**日期窗 + 按日分组**
> （日升序；`start_date`/`end_date` + `timezone`，见下方条目）。`get_event` 增 `timezone` 入参（缺省/
> 无效 = 服务配置时区）、输出 `timestamp`/`timezone`、**video 且关键帧存在时返回值附图片
> （image content）**；`get_key_image` 工具删除（并入 get_event）。`get_status` /
> `trigger_analysis` 迁 REST（`GET /api/status`、`POST /api/analysis/trigger`，同 token）；
> `list_jobs` 删除（未关联 job 不再可查）。
> 设计与实施记录见 [lvdata_mcp_tools.md](./lvdata_mcp_tools.md)；下文条目均已按此批注。
>
> **2026-09-28 追加**：`search` 参数/输出对齐——去 `session_id`，`start_ms`/`end_ms` →
> `start_date`/`end_date`（`YYYY-MM-DD` 含端点）+ `timezone`；输出加 `timezone`、
> `ts_ms` → `timestamp`（可读）。**同日 `semantic_search` 同批对齐**（`lvdata_mcp_tools.md` §11）：
> 入参除 `with_context` 外与 `search` 逐字相同（日期窗同时下推两路召回）、命中删 `session_id`、
> `ts_ms` → `timestamp`、`snippet`/`text` 与 `search` 同义、顶层加 `timezone`/`searched_types`
> （保留 `mode`）；另 `ts_ms=0` 的无时间戳事件一律 `timestamp: null`（全部工具统一）。
> 见 [lvdata_mcp_tools.md](./lvdata_mcp_tools.md) §10/§11。
>
> **2026-09-28 二追加**：`semantic_search` 的 `context_events`（`with_context=true`）改**轻量投影**，
> 与 `get_timeline` 的 `days[].events[]` 同形（`{event_ref, type, timestamp, source, summary}`，
> 邻域仍为 ±2min/最多 10 条）；细节按 `event_ref` 调 `get_event`。随之删 `store._event_out`，
> 能量护栏收缩为 `get_event.detail` 单出口。见该文档 §12。
>
> **2026-09-28 三追加**：`semantic_search` 的查询向量加**进程内 LRU 缓存**（500 条，
  `VecIndex.embed_query`；`array('d')` 保 float64 逐位一致、失败不缓存、模型切换靠进程重启隔离）；
  `/api/status` 增 `cache.query_embeddings`（size/hits/misses）。见该文档 §13。
>
> **2026-09-28 四追加**：MCP `initialize` 补 `instructions`（服务身份与用法摘要，中文 178 字；
> 文案逐字留档于该文档 §14.2，**改动需评审**——它是每个 agent 的常驻上下文）与真实
> `serverInfo.version`（原为 mcp SDK 版本）；版本号收敛到 `lvdata_mcp.VERSION` 单一出处，
> `/api/status` 的 `service.version` 同源。见该文档 §14。
>
> **2026-09-28 五追加**：`semantic_search` 的**相关度可见 + 下限**——命中暴露 `vec_score`
> （余弦相似度）/`fts_score`（`-bm25`）；`providers.embedding.min_vec_score`（默认 0.50，`0` = 关闭）
> 以下的向量命中不返回（FTS 命中不受影响），响应此时带 `filtered{min_score, best_dropped}`；
> 入参不变。见该文档 §15（含标定方法：换 embedding 模型须复标）。
>
> **2026-09-28 六追加**：工具描述与 `instructions` 分工重整（**不改变任何工具行为**）——5 条工具
> 描述里逐字重复的共同约定（日期窗含端点 / 时区口径 / 轻量投影 / 详情链 / **错误在返回体
> `error` 字段**）上移进 `instructions`（178 → 319 字，文案逐字留档于该文档 §14.2）；描述只留
> 各工具特有行为，合计 1416 → 781 字。见该文档 §16。
>
> **2026-09-28 七追加**：MCP 面的 `timestamp` 改**带偏移的 ISO 8601**（如
> `2026-09-28T18:01:30+08:00`，偏移按各时刻的 zone 规则算，DST 时区内同一响应可能两种偏移）；
> `timezone` 字段仍为真实生效 zone 名（§17.2-b）。注意与上一条**水位列**（DB 列）的
> `…Z` 毫秒格式是**两套不同口径**：前者是对 agent 的展示格式（无毫秒、带本地偏移），
> 后者是库内字符串比较用的水位格式——勿混用。见 [lvdata_mcp_tools.md](./lvdata_mcp_tools.md) §17。
>
> **2026-09-28 八追加**：`instructions` 第 4 版（用户定稿，280 字）——时间口径句改为
> 「timezone是当前查询所在的时区」（原 ISO 格式说明与 DST 排序提醒移出；格式未变，时间戳仍
> 自带偏移）。见该文档 §14.2 / §17.3。
>
> **2026-09-28 九追加**：`semantic_search` 描述再精简（用户要求，404 → 240 字）——阈值/`filtered`/
> `snippet`·`text`/缓存说明移出描述（**行为未变**，仅文档面收窄；`vec_score`/`fts_score` 保留）。
> 工具描述合计 781 → 617，常驻+按需合计 897 字。见该文档 §15.3 / §16.2。
>
> **2026-10-28 十追加（D9 + language）**：分析产物新增顶层 `language` 配置（默认 `en`，
> App 16 code，空/非法/未知回落 `en`）；MCP `instructions`、tool 描述、error/hint 统一英文。
> 配置见 §4.3；行为见 [lvdata_multilingual.md](../lvdata_multilingual.md)。

上层查询面只面向 events + day_summary（§3.2.7），session 维度工具全部删除：

- 保留（内部读路径切 events/day_summary/agent_jobs）：`search`、`semantic_search`、`get_timeline`、`get_event`、`get_key_image`、`list_jobs`、`get_status`、`trigger_analysis`——2026-09-27 收敛为 5 个只读 tools（见上方更新块）。
- 新增：
  - `get_day_summary(timezone?: str, date_from: str, date_to: str)` — 读 day_summary 表，返回按日排列的摘要（含 stats/status，pending 日返回状态而非内容）。**2026-09-27 由 `get_recent_events` 取代**（day_summary 表与生成链保留，BFF 日卡继续直读）。
  - 下一阶段：`get_period_summary`。
- 删除（session 维度不再暴露）：`list_sessions`、`get_session_summary`。
- 保留工具的参数调整：
  - `get_timeline`：移除 `session_id`，其余参数与 v1 同构（epoch ms 时间窗，与 `events.ts_ms` 同单位）——`get_timeline(start_ms?: int, end_ms?: int, types?: str, limit: int, cursor?: str)`，直查 events（`ts_ms BETWEEN` 走 `idx_events_ts`；`(ts_ms, EVENT_PRIORITY, event_id)` 排序，§5.1）。**游标为 `[ts_ms, type, event_id]` 三元组 keyset**——v1 的 `[ts_ms, type]` 二元组在同毫秒同类型事件下非全序，可能跳行/重复，event_id 提供全序兜底。输出 `ts_ms`/`ts_utc` 直接用表内存储值，不再返回 session 元信息（time_span 等）。**2026-09-27 二改**：入参 `start_ms`/`end_ms` → `start_date`/`end_date`（`YYYY-MM-DD` 含端点）+ `timezone`；输出改与 `get_recent_events` 同构的按日分组（日升序、日内升序、轻量投影 `timestamp`，无 payload/`ts_ms`/`ts_utc`），`total` 改为窗口内总数；游标语义不变（分页可能把同一天切成两页）。
  - `get_key_image`：入参由 `(session_id, frame_id)` 改为 `event_ref`（video 事件，来自 get_event/search 输出）——v2 关键帧即 video 事件，按 payload 定位原始 jpg，媒体路径内部解析。**2026-09-27 工具删除**：图片并入 `get_event` 返回值（video 且关键帧存在时附 image content）。
  - `list_jobs`：直查 `agent_jobs` 表（不变，**含 recording_session_id 未关联的 job**——这是未关联 job 的唯一检索入口，与 §5.1 铸造规则互补）。**2026-09-27 彻底删除**（工具 + store 实现 + ATTACH 辅助；未关联 job 自此无可查入口）。
  - `get_status`：扩展 `pending_files{audio,video}`、`failed_files`、`day_summary{pending,failed}`、`last_day_summary_at`；`recent_runs` 契约不变；`data.index.last_rebuild_at` = 最后一次实际重建的 T0、`lag_sessions` 口径见 §5.10/§6.6。**2026-09-27 迁 REST：`GET /api/status`**（输出同构）。
  - `trigger_analysis`：stages = `inventory | agent_jobs | audio | video | reindex | embed | day_summary`；`force=true` 重置对应 fail_count/退避（§5.2/§5.4）。**2026-09-27 迁 REST：`POST /api/analysis/trigger`**（stages 现为 `inventory | chatlog | audio | video | reindex | embed | day_summary | day_stats`）。

---

## 13. 错误处理

| 场景 | 行为 |
|---|---|
| yaml 缺失/校验失败/`${VAR}` 未设置/api_key 缺失 | 启动 fail-fast（与 services 同约定） |
| LLM 429/超时/连接/5xx | 调用内指数退避 `2^n+rand(0,1)` max 3；耗尽 → files.fail_count+1（达 3 暂停该文件，force 重置） |
| 分析 JSON 解析/schema 缺键 | 计入重试；末次归一化落库（v1 语义） |
| ogg 直送 400 | ffmpeg 转 wav 降级重试一次（v1 语义，不「顺手修复」） |
| vision `enable_thinking` 400 | 降级重试一次（v1 语义） |
| Agent Relay 不可达/停写 | jobs 水位不动，检测自然空转；其余阶段不受影响（无 PG 依赖） |
| enrich 永久 NULL | 每轮重试；该 job 不产生事件（§5.1），list_jobs 仍可见 |
| day_summary LLM 失败 | status='failed' + fail_count+1 + generated_at=尝试时刻；退避期满重试，达 3 暂停（§5.4） |
| 事件乱序/迟到/重分析 | updated_at 水位 + 快照语义天然覆盖（§5.4/§6.5） |
| 进程崩溃 | 全部工作状态在 DB（files 状态机 / 水位 / fail_count / day_scan_state / pending_affected_sessions），重启续跑；jobs_watch 单事务无半截状态（§6.2.1） |
| 磁盘媒体缺失（关键帧） | ~~not_found（v1 语义不变）~~ **2026-09-27 改**：并入 `get_event` 后不再报错——仅 text 段 + `related.image_available=false` |
| 存量文件被删除 | 不处理（v1 行为，§6.1.6） |

---

## 14. 开放问题

1. **day_summary 超长输入策略**：单日事件渲染超过上下文窗口时，是截断（丢尾部）还是分片聚合（多次 LLM）？建议先截断 + 计数告警，观察真实数据量再定。
2. **bff 写 timezones 的形态**（§8.1）推迟决策。
3. **day_summary 降本**：一天多 session 时 day_summary 会重复总结相近内容；首版接受，后续可评估事件预聚合/分段总结。
4. **vendor 扩展**：`providers.*.vendor` 本期只提供 openai-compatible 默认值；未来若有非兼容端点（如百炼 websocket 实时流），Provider 抽象需升级——本期不做。

---

## 附 A：v1 → v2 数据流对照

| 关注点 | v1 | v2 |
|---|---|---|
| 建档 | subprocess lvdata_inventory.py | 进程内 scanner.py |
| agent jobs | subprocess lvdata_sync_agent_jobs.py（PG→SQLite 同步） | 进程内 jobs_watch.py 读表变更（Agent Relay 直写，无 PG 同步；契约 §6.2.0） |
| 音频分析 | subprocess + derived JSON + audio_analyses | 进程内 + **events 表**（静音跳过）+ 审计 JSON + files 状态机 |
| 视频分析 | subprocess 两阶段 + derived JSON + video_analyses | 进程内两阶段 + **events 表** + 审计 JSON（windows/frames）+ files 状态机 |
| 时间线 | 每 session 组装 timeline.json（5 类源） | **events 表查询**（无产物文件） |
| 会话摘要 | session_summary.json / summary.json / video_summary.json | **移除**（只保留 day_summary） |
| 日摘要 | 无（bff 请求时现场分组，客户端聚合） | **day_summary 表 + LLM**（§6.5）；hotSlots/images/hasPending 由 bff 确定性派生（§9.1.2） |
| 跨会话报告 | derived/_reports（report stage） | 移除（由 day/period summary 取代） |
| 检索 | FTS/vec 源 = derived JSON + 各表 | FTS/vec 源 = **events 表** |
| bff 读法 | sqlite + derived JSON | **纯 sqlite**（与 v2 同步迁移） |
| 配置 | CLI/env（LVDATA_MCP_*） | **lvdata_mcp.yaml** + env override |
| 历史数据 | — | 不回填；重分析重建（§7，成本 §10） |
| 表/列命名 | `sessions`、`session_id`（语义即录制会话） | `recording_sessions`、`rsession_id`（启动幂等改名，§7） |
| 重试/暂停状态 | (session,stage) 粒度，部分内存态 | 全部落盘：files.fail_count / day_summary.fail_count + generated_at / day_scan_state |

## 附 B：定稿相对 d4p 评审稿的修订清单

> d4p 的全部架构决策保持不变（干净切割、无回填、Agent Relay 直写、session 维度内部化、表/列改名、时间戳约定、审计 JSON 保留、分析状态机等）。以下为评审发现的缺陷修复与漏洞补全，均不改变既定决策方向。

| # | 位置 | 修订 | 性质 |
|---|---|---|---|
| 1 | §2.5/§6.2.0/§9.2 | Agent Relay 是**现有服务**（services/agent_relay，现写 PG），非「未来组件」；补全写入契约：单调守卫保留、时间戳格式契约、同机部署 + WAL/busy_timeout | 事实修正 + 契约补全 |
| 2 | §5.1 | 新增 **agent_job 事件铸造规则**（rsession_id 首次确定时一次性铸造，NULL 不插行）：消除 NOT NULL 约束排斥未关联 job、enrich 改值致 ref 漂移/孤儿事件两缺陷；未关联 job 由 list_jobs 覆盖 | 缺陷修复 |
| 3 | §5.1 | events 表删除 `status` 列（d4p 稿无任何写入方定义） | 死列清除 |
| 4 | §5.1/§12 | timeline 排序与游标补 `event_id` 全序兜底（`[ts_ms, type, event_id]` 三元组） | 缺陷修复（v1 二元组非全序） |
| 5 | §5.1/§5.4 | `EVENT_PRIORITY` 出处修正（现存于 lvdata_summary.py / bff store.py，v2 迁入包内）；`day_bounds_ms` 返回值实为**闭区间**，文档表述对齐 | 事实修正 |
| 6 | §5.4/§6.5 | **generated_at 快照语义**：成功写输入快照时刻 T0（先取 T0、输入限 `updated_at <= T0`），修复双 loop 并发下「生成期间落入的事件被水位吞掉」的 TOCTOU 竞态 | 正确性修复 |
| 7 | §5.6/§6.5 | 新增 `day_scan_state` 发现游标：d4p 伪码的 dirty 发现每轮需全历史逐日比对，改为增量游标扫描（新时区首扫全历史一次）；游标写扫描开始时刻，不漏并发 | 性能/正确性补全 |
| 8 | §5.7/§6.1.8 | 补 `day_force` 表作为 `force_day_summary` 的持久化载体（d4p 稿载体未定义） | 漏洞补全 |
| 9 | §5.2/§5.4/§13 | 重试与暂停状态全部落盘（files.fail_count、day_summary.fail_count）：d4p 稿未指明 MAX_FAILURES 暂停状态存处，重启即丢 | 漏洞补全（原则 8） |
| 10 | §6.2.1 | jobs_watch 的 enrich 回写 + 事件 upsert + 水位推进 + affected 交付改**单事务**，消除崩溃丢失窗 | 正确性修复 |
| 11 | §3.1/§6.7 | 统一 day-summary 的 loop 归属（d4p 稿 §3.1 与 §6.5 表述矛盾）：发现在 poll loop，生成在 work loop 全局 stage | 矛盾消除 |
| 12 | §4.2/§10 | 新增成本护栏配置（`max_files_per_poll`、`day_summary_max_per_poll`、`llm_timeout_s`）与「成本与限速」整节：d4p 的全量历史重分析无任何节流机构，仅有 30min 粗超时 | 护栏补全 |
| 13 | §5.5 | timezones 表 DDL 时间戳改 §5.1 规范格式（d4p 稿用 `datetime('now')`，违反自家约定） | 一致性修复 |
| 14 | §7 | 改名事务内对 `agent_jobs.updated_at` 存量行做**格式归一化**（PG 来源格式 → 规范格式），否则 keyset 水位字符串比较失效 | 正确性修复 |
| 15 | §9.1.2 | 补日卡确定性字段服务端化（hotSlots/images/hasPending 对齐 Android `PeriodDigest`；replay 重锚 ts_ms/event_ref）——d4p 稿的 day_summary JSON 与现有日卡字段级不对齐 | 漏洞补全 |
| 16 | §6.7 | `mcp_pipeline_runs.session_id` 列语义推广为通用 target 并改名 `target` | 漏洞补全 |
| 17 | §12 | `get_status` 扩展（pending/failed 计数、last_day_summary_at）；eval golden 中未关联 agent_job 引用的处理说明 | 补全 |
| 18 | §5.10/§6.6 | reindex 水位独立成 `index_state`（`''` = 全局；T0 快照语义，修复读-写竞态；回填幂等）+ 审计表（`mcp_pipeline_runs`/`analysis_runs`）按龄清理：审计退化为纯审计、可随时裁剪 | 解耦 + 正确性修复 |
