# LvData MCP Service v2 开发与测试计划

> 基线：[`lvdata_mcp_service_v2.md`](./lvdata_mcp_service_v2.md)（定稿）。
> 阶段划分沿用定稿 §11（P0→P4），此处补全每阶段的**产出物、测试策略与验收门禁**。
> 实施顺序即阶段顺序；每阶段结束跑该阶段测试 + 既有测试回归。
>
> **状态：已实施完成（2026-08-25）。** 全量回归：lvdata-mcp 105 passed；
> services（含 bff 77 / agent_relay 14）671 passed。真机冒烟（DASHSCOPE_API_KEY）：
> audio input_audio 通道 ✓、day_summary LLM 生成 ✓。

## 总体策略

- **干净切割**：v2 与 v1 不并存。`tools/lvdata-mcp/src/lvdata_mcp/` 下按新架构重写，
  v1 产物（derived 查询 JSON、audio_analyses 等）不读不写。
- **测试双层**：
  - 单元/集成（pytest，mock LLM/embedding provider，全部离线可复现，沿用
    `FakeEmbeddingProvider` 模式）；
  - 真机冒烟（§10.3 灰度第 1 步）：独立实例 + `DASHSCOPE_API_KEY` 真实调
    用 audio/vision/summary，验证 events/day_summary 产出质量。
- **契约先行**：Agent Relay 写入契约（§6.2.0）在 P1 联调，P4 完成 Relay 改道。

## P0 — 配置与骨架

**产出物**

1. `src/lvdata_mcp/config.py` 重写：
   - yaml 加载链：`--config` CLI → env `LVDATA_MCP_CONFIG` → `<configs>/<env>/lvdata_mcp.yaml`
     （跟随 `shared/config/loader.py` 的 `_project_root()` 约定）→ CWD `lvdata_mcp.yaml`；
   - `${ENV_VAR}` 占位符递归解析（未设置 fail-fast）；
   - `LVDATA_MCP__` 双层下划线 env override（类型按现有值强转）；
   - pydantic schema：`ServerConfig` / `DataConfig` / `WatcherConfig` /
     `ProvidersConfig{audio,vision,embedding,summary}` / `ProviderConfig`（vendor 默认
     base_url/model 表 §4.3）；
   - `validate_security` 保留（非回环绑定必须配 token）；api_key 缺失 fail-fast；
   - 启动打印生效配置（对齐 livachat 行为）。
2. 新 `src/lvdata_mcp/ddl.py`：§5 全部 DDL（events/day_summary/timezones/
   day_scan_state/day_force/period_summary/analysis_runs + 索引）+ §7 幂等改名
   （`sessions`→`recording_sessions`、基础表 `session_id`→`rsession_id`、
   `agent_jobs.pg_updated_at`→`updated_at`、`synced_at` 废弃）+ `agent_jobs.updated_at`
   存量行格式归一化（→ `YYYY-MM-DDTHH:MM:SS.sssZ`）+ `timezones` seed。
3. `src/lvdata_mcp/main.py`：CLI 仅保留 `--config` + `serve`/`reindex`/`eval`；
   serve 启动先执行 DDL 再建 store/watcher。
4. 时间戳工具：`_now_iso()` 统一 §5.1 规范格式；`timeutil.day_bounds_ms` 复用。

**测试**

- `test_config.py`：yaml 加载（缺省路径/显式 --config）、占位符解析（缺失报错）、
  `LVDATA_MCP__` override（int/bool/str）、vendor 默认值、api_key 缺失 fail-fast、
  非回环绑定无 token 拒绝。
- `test_ddl.py`：建表幂等（连跑两次）、改名幂等（老库 → 新名）、时间戳归一化
  （`+00:00`/空格/微秒 → 规范格式，字符串比较正确）、timezones seed 不覆盖已有行。

**门禁**：`pytest tools/lvdata-mcp/tests -k "config or ddl"` 绿；`lvdata-mcp serve --config` 可启动到
watcher 首轮 poll 完成。

## P1 — 内部化建档与 jobs 检测

**产出物**

1. `src/lvdata_mcp/scanner.py`（§6.1）：
   - 遍历 `<root>/<device>/<session>/`，`session.json` 判定；rsession_id/device_uuid 取自 meta；
   - file_type 由顶层子目录推断；UUIDv7 → uuid_ts_ms/utc；files upsert + device_uuid +
     analysis_status（chat/meta='na'、audio/video 新='pending'、存量保留）；
   - recording_sessions `INSERT OR REPLACE`；chat/history.jsonl → chat_messages；
   - **chat 事件直写 events**（`<rsession_id>:chat:<msg_id>`，payload 含 role/text/chat_session_id）；
   - mtime 跳过；不删除缺失行；finalized 状态变化 → 全部 enabled 时区写 day_force；
   - 返回 new/updated 列表（dirty 判定输入）。
2. `src/lvdata_mcp/jobs_watch.py`（§6.2）：
   - keyset 分页读 `agent_jobs`（`(updated_at, job_key) > (?, ?)`，batch 5000），水位
     `agent_jobs_sync_state`（角色改为 SQLite 读取游标）；
   - enrich：`recording_session_id` 缺失补填（chat_session_id 匹配 → 时间窗 fallback），
     只允许 NULL→值，不动 `updated_at`；
   - **单事务**：enrich 回写 + agent_job 事件 upsert（铸造规则 §5.1：rsession_id 已知才铸）+
     水位推进 + pending_affected_sessions；
   - 删除 PG 依赖（dsn/psycopg）。
3. `src/lvdata_mcp/watcher.py` 重写（§6.7）：
   - poll loop：scanner.scan() → jobs_watch.check() → detect_dirty（files.analysis_status
     pending/failed 双信号）→ day-summary dirty 发现（P3 的接口先留空实现）；
   - work loop：per-session 链 audio→video→reindex→embed + 全局 day_summary；
   - mcp_pipeline_runs 加 `target` 列；stage 名 v2 集合；mcp_watch_state 不再使用；
   - 失败退避/暂停改持久化（files.fail_count / day_summary.fail_count）。

**测试**

- `test_scanner.py`：新会话建档（files/chat_messages/recording_sessions/chat 事件）、
  增量扫描（mtime 跳过）、chat 追加 → 新 chat 事件 + new 列表、finalized → day_force、
  存量文件 analysis_status 保留。
- `test_jobs_watch.py`：keyset 分页（乱序 updated_at）、enrich 两条路径、单事务原子性
  （中途异常回滚，水位/事件/回写全不落）、未关联 job 不铸事件但 list_jobs 可见、
  `updated_at` 不被 enrich 触碰。
- `test_watcher.py` 重写：detect_dirty 用 analysis_status、链调度、target 记录。

**门禁**：三测试文件绿 + 既有 test_tools/test_index/test_vec 回归通过（读路径未动时）。

## P2 — 内部化分析

**产出物**

1. `src/lvdata_mcp/analysis/audio.py`（§6.3）：
   - 取批 `file_type='audio' AND analysis_status='pending' AND fail_count<3`，上限
     max_files_per_poll，并发 concurrency；
   - ffmpeg 转码（audio_sample_rate；0=直传 ogg；400 降级转码重试一次）；
   - AsyncOpenAI streaming + json_object，schema = v1 SEGMENT_KEYS；重试
     `2^n+rand(0,1)` max 3；schema 违规重试、末次归一化；
   - 静音判定（全空）→ 不写 events；审计 JSON 照写；events upsert（非静音）；
   - files 状态机 ok/failed + fail_count；analysis_runs 审计（usage/latency）。
2. `src/lvdata_mcp/analysis/vision.py`（§6.4）：
   - filter：uuid_ts_ms 排序、30s 窗口分组、window_id=首帧 stem、逐帧判定 + window_caption、
     图像缩放（max_image_size）；extract：关键帧 chunk=4；
   - enable_thinking=False（400 降级重试一次）；events upsert（window + video）；
   - 审计 JSON（windows/*.json、frames/*.json）；files 状态机 + analysis_runs。
3. `src/lvdata_mcp/analysis/__init__.py`：共享 LLM 客户端/重试/JSON sanitize。
4. `src/lvdata_mcp/providers.py`：按 ProviderConfig 构建 AsyncOpenAI 客户端。

**测试**（全部 mock LLM，用可编程 fake client）

- `test_audio.py`：正常分析 → events + 审计 JSON + files ok；静音 → 无事件 + ok；
  LLM 失败 3 次 → failed + fail_count=3 暂停；force 重置；usage/latency 落 analysis_runs；
  ogg 直传 400 → 转码降级。
- `test_vision.py`：窗口分组（确定性 window_id）、filter→extract 两阶段事件、
  失败计数/暂停、enable_thinking 400 降级。

**门禁**：绿 + 真机冒烟（见下）。

## P3 — 日摘要

**产出物**

1. `src/lvdata_mcp/summary/day.py`（§6.5）：
   - dirty 发现（poll loop）：day_scan_state 增量游标（写扫描开始时刻）、逐日核对
     （无行 or events.updated_at>generated_at）、失败重试（退避 60/300/900、fail_count<3）、
     day_force 绕过 debounce；单轮上限 day_summary_max_per_poll；
   - 生成（work loop）：先取 T0 → 读当日 events（ts_ms BETWEEN day bounds AND
     updated_at<=T0，排序 (ts_ms, EVENT_PRIORITY, event_id)）→ 渲染 LLM 输入（每事件
     `[HH:MM] type: summary`，截断 max_chars_per_event）→ summary provider
     （json_object）→ upsert day_summary（generated_at=T0、utc_offset_min、event_count、
     usage/model、status=ok、删 day_force）；失败 → failed + fail_count+1 +
     generated_at=尝试时刻；
   - 0 事件不落行。
2. `get_day_summary(timezone?, date_from, date_to)` MCP tool（§12）。

**测试**

- `test_day_summary.py`：dirty 发现（水位推进、新时区全量首扫、写开始时刻不漏并发）、
  debounce（quiet_s 内跳过 / day_force 绕过）、快照语义（生成期间新事件下轮必中——
  模拟 TOCTOU）、失败重试与暂停、0 事件不落行、DST 边界（America/New_York 3月/11月）、
  JSON schema 校验（§5.4.1）、get_day_summary 工具返回 pending 状态。

**门禁**：绿 + 真机冒烟生成真实日摘要。

## P4 — 索引切换 + 工具适配 + bff + Relay

**产出物**

1. `index.py` / `vec.py`：数据源切 events（FTS text=summary、chunk_id=event_ref、
   per-session 重建 = 查 events）。
2. MCP tools（§12）：get_timeline 去 session_id（三元组 keyset 游标）、get_key_image
   改 event_ref、get_day_summary、list_sessions/get_session_summary 删除、get_status
   扩展（pending_files/failed_files/day_summary/last_day_summary_at）、trigger_analysis
   stages 更新（force 重置持久化计数）。
3. bff（§9.1）：timeline 查 events、日卡读 day_summary、hotSlots/images/hasPending
   服务端派生、analysis_state 改查 files、session 维度 API 内部化。
4. Agent Relay（§6.2.0）：写路 PG → SQLite（单调守卫、时间戳格式、recording_session_id、
   WAL/busy_timeout）。
5. 测试适配：tests/ 下 5 个测试文件更新到 v2 语义。

**门禁**：全量 pytest 绿；`lvdata-mcp reindex` 从 events 重建；bff 冒烟；
Relay 直写后 list_jobs/get_event 正常。

## 真机冒烟（灰度 §10.3）

- 独立实例 + `root_dir` 指向数据子集，env `DASHSCOPE_API_KEY`；
- 冒烟路径：scan → audio/video 分析 → 查 events → day_summary 生成 → search/
  semantic_search → get_key_image；
- 观察 analysis_runs usage/latency 与失败率；按需调 max_files_per_poll/concurrency。

## 明确不做（本期）

- period_summary 生成逻辑（只建表）；timezones 的 bff 写入口；历史数据回填；
  v1 兼容层。

## 实施记录（2026-08-25 完成）

- **P0**：config.py（yaml 加载链/${ENV_VAR}/LVDATA_MCP__ override/vendor 默认值）、
  ddl.py（v2 全表 + §7 幂等改名 + 时间戳归一化 + timezones seed）、main.py（--config）、
  configs/dev/lvdata_mcp.yaml、deploy systemd 加 --config。
- **P1**：scanner.py（建档/chat 事件直写/day_force）、jobs_watch.py（keyset 读表 +
  enrich + 单事务 + 事件铸造）、watcher/pipeline v2 链（inventory/agent_jobs/audio/
  video/reindex/embed/day_summary + mcp_pipeline_runs.target）。
- **P2**：analysis/audio.py（转码/400 降级/静音过滤/events upsert/files 状态机）、
  analysis/vision.py（filter+extract 窗口原子性/审计 JSON）、providers.py（重试客户端）。
- **P3**：summary/day.py（day_scan_state 增量发现/快照水位 T0/debounce/day_force/
  失败退避暂停/DST 边界）、get_day_summary tool。
- **P4**：index/vec 数据源切 events（chunk=event_ref）；MCP tools v2（get_timeline 三元组
  游标、get_key_image event_ref、list_sessions/get_session_summary 删除、get_status 扩展）；
  store 读路径 events；bff 迁移（timeline/overview 查 events+day_summary、
  hotSlots/images/hasPending 派生、analysis_state 查 files、graph/search/replay 适配）；
  Agent Relay JobLvdataWriter 直写 SQLite（§6.2.0 契约：单调守卫/规范时间戳/WAL）。
