# lvdata-mcp 工具集重构：公开工具收敛（第二轮 6 个 → §8 后 5 个）+ 运维 REST API（设计与实施清单）

> 状态：**已实施**（2026-09-27 第二轮决策，取代同日第一轮的 `internal_tools` 门控方案；
> 实施记录见 §7）；**§8（删 `get_key_image`，图片并入 `get_event`）、§9（`get_timeline` 日期窗口
> + 按日分组）与 §10（`search` 对齐日期窗/`timezone`/可读时间）均已实施**（记录见 §7 末三条）；
> **§11（`semantic_search` 与 `search` 接口对齐）：已实施（D1–D6 全按推荐，记录见 §7 末条）**；
> **§12（`context_events` 改轻量投影 + 删 `_event_out`）已实施（记录见 §7 末条）**；
> **§13（`semantic_search` 查询向量进程内缓存）：已实施（记录见 §7 末条）**；
> **§14（MCP server 元数据：`instructions` + 真实版本号）：已实施（D1–D5 全按推荐，记录见 §7 末条）**；
> **§15（`semantic_search` 相关度可见 + 下限）：已实施（D1–D6 全按推荐，记录见 §7 末条）**；
> **§16（工具描述的共同块上移 `instructions`）：已实施（D1–D6 全按推荐 + 用户定稿文案，记录见 §7 末条）**；
> **§17（`timestamp` 改 ISO 8601 带偏移）：已实施（17.2-b，记录见 §7 末条）**；
> **§18 性能复审（2026-09-28）：已完成——无严重性能问题、无待办**（3 处随规模增长点均已登记；
> 唯一候选动作是 10 万 chunk 之后评估 vec ANN）。
> 前置：[lvdata_mcp_service_v2.md](./lvdata_mcp_service_v2.md) §12（MCP tools v2 契约）、
> [tools/lvdata-mcp/README.md](../../tools/lvdata-mcp/README.md)、
> [hermes_adaptor_design_v2.md](../hermes_adaptor_design_v2.md) §3.3
> 范围：`tools/lvdata-mcp/src/lvdata_mcp/{tools,schemas,store,server}.py`、`tests/`、相关文档

## 0. 结论

**对外 MCP 工具 5 个**（全部只读、面向 agent；§8 变更后）：

| # | Tool | 变化 |
|---|------|---------|
| 1 | `search` | 不变（**§10 追加**：去 `session_id`、日期窗 + `timezone`、输出可读 `timestamp`） |
| 2 | `semantic_search` | 不变（**§11 追加（已实施）**：去 `session_id`、日期窗 + `timezone`、输出可读 `timestamp`） |
| 3 | `get_timeline` | 不变（**§9 追加**：入参 `start_ms`/`end_ms` → `start_date`/`end_date` + `timezone`；输出改按日分组轻量投影、`total` = 窗口内总数） |
| 4 | `get_event` | **§8**：加 `timezone` 入参；输出 `timestamp`/`timezone`、删 `ts_ms`/`session_id`；video 附关键帧图片 |
| 5 | `get_recent_events(days, timezone)` | 新增（取代 `get_day_summary`） |

**运维接口迁 REST**（同一进程、同一 token）：`GET /api/status`、`POST /api/analysis/trigger`
（原 8/9 号工具）。

**彻底删除**：`list_jobs`（工具 + store 实现 + 连带 ATTACH 死代码）、`get_day_summary` 工具
（+ 无使用者的 `store.get_day_summaries`）。

**废弃**：第一轮设计的 `server.internal_tools` 门控机制——4 个原"内部工具"各有去向
（REST / 删除 / 改造为公开工具），MCP 侧不再有内部工具，无需门控。

动机（上一轮 agent 可用性评审的结论）：5 个公开工具缺"最近/今天"入口、缺日粒度概览、
无发现型工具 → `get_recent_events` 承接；`get_status`/`trigger_analysis` 是运维件、
`list_jobs` 直查 job 表，对 agent 无用 → 移出 MCP，agent 工具列表更短更聚焦。

## 1. 现状（代码事实）

### 1.1 工具清单与本次去向

9 个工具均定义在 `tools/lvdata-mcp/src/lvdata_mcp/tools.py` 的 `register_tools()`（:41）内。

| # | Tool | tools.py | 现状定位 | 本次去向 |
|---|------|----------|----------|----------|
| 1 | `search` | :46 | agent 检索主入口 | 保留 |
| 2 | `semantic_search` | :102 | 语义检索（FTS+向量 RRF） | 保留 |
| 3 | `get_timeline` | :154 | 时间窗浏览 | 保留 |
| 4 | `get_event` | :178 | 事件详情 | 保留 |
| 5 | `get_key_image` | :192 | 关键帧图片 | ~~保留~~ **§8 删除**（图片并入 `get_event`） |
| 6 | `get_day_summary` | :212 | 读 day_summary 表 | **改造为 `get_recent_events` 公开**（§3.2） |
| 7 | `list_jobs` | :232 | 直查 `cs.agent_jobs`；未关联 job 唯一入口（`docs/recording_data_process.md:344` 裁定 D） | **删除**（§3.4） |
| 8 | `get_status` | :267 | 运维可观测性；`docs/bff_search_fts.md:180` 建议接入部署告警 | **迁 REST**（§3.3） |
| 9 | `trigger_analysis` | :281 | 唯一写入口（仅入队） | **迁 REST**（§3.3） |

### 1.2 暴露路径与鉴权

- FastMCP 实例（`server.py:37`）+ `streamable_http_app()`（:39）挂 `/mcp`；`/healthz`/`/readyz`
  为仅有的另外两条路由（`server.py:74-82`）——**当前无 `/api` 路由**，REST 为新增。
- bearer 鉴权（`server.py:85-96`）、访问日志（:99-134）、每 IP 限流（:137-159）的路径判定都是
  `startswith("/mcp")`（:91/:107/:144）——扩 REST 需同步这三个前缀。
- 注册全部发生在 uvicorn 启动前（`main.py:140-171` run_serve），运行期无工具增删；
  `tools/list` 由 ToolManager 实时枚举（`.../fastmcp/tools/tool_manager.py:41-43`）。

### 1.3 消费方（影响面来源）

- **外部 agent**：Hermes——`docs/hermes_adaptor_design_v2.md:168-175`（§3.3，清单还残留 v1 已删的
  `list_sessions`/`get_session_summary`，属 stale）；`tools/lvdata-mcp/README.md:77-89` 的
  `mcpServers.lvdata` 接入模板。
- **测试**：`tests/test_tools.py` 经 HTTP 覆盖全部 9 个工具（工具集合断言 :68；:174/:264 day
  summary、:193 status、:312 trigger）。
- **运维/开发**：`main.py:44-50` `stage` 子命令 help 把分析类全量指向 MCP `trigger_analysis`；
  dev 实跑步骤见 `docs/video_analysis.md:173/356`、`docs/video_analysis_plan.md:38`、
  `docs/audio_analysis_plan.md:42`、`docs/day_summary.md` §7（手工置 pending + 正常增量重生成）。
- **无自动化/脚本经 MCP 调用**：`services/` 无 MCP 客户端；`tools/lvdata-mcp/scripts/` 两个探针
  直调 Python 模块；BFF/App 走 BFF HTTP 直读同一 SQLite（`services/bff/common/store.py`）。

### 1.4 库能力边界（mcp 1.29.1，`pyproject.toml` 约束 `>=1.12,<2`）

`FastMCP.tool()` 仅支持 `name/title/description/annotations/icons/meta/structured_output`
（`.venv/.../mcp/server/fastmcp/server.py:450-465`），无 tags/可见性开关，包内也无 MCP 层中间件
API → 工具集调整只能在**注册期增删**（本次全部改动都属此类，无运行期过滤）。

## 2. 决策沿革（2026-09-27）

| 轮次 | 决策 | 现状 |
|------|------|------|
| 一轮 D1 | 生产实例不提供任何运维通道 | **被 R1 取代**：运维改走 REST |
| 一轮 D2 | `get_day_summary` 隐藏至内部 | **被 R3 取代**：改造为 `get_recent_events` 公开 |
| 一轮 D3 | `server.internal_tools` 门控注册 | **废弃**：无 MCP 内部工具残留 |
| 二轮 R1 | `get_status` / `trigger_analysis` 移到 REST API | §3.3 |
| 二轮 R2 | `list_jobs` 彻底删除 | §3.4 |
| 二轮 R3 | `get_day_summary` → `get_recent_events(days, timezone)`，days=1 今天、上限 31 | §3.2 |

第一轮中不受影响的部分保持：单一 bearer token 不变（"对外"= MCP 客户端可见面）；REST 是同一
服务的第二个面，同 token、同限流。

## 3. 目标形态

### 3.1 MCP 工具（5 个，§8 后终态）

`tools/list` 恒返回 5 个（无配置开关）：`search` / `semantic_search` / `get_timeline`
（§9.2 契约，已实施）/ `get_event`（§8.6 契约）/ `get_recent_events`（§3.2）。上一轮评审遗留的
agent 体验项——`semantic_search` 补时间过滤、列表工具加字段裁剪——**不在本次范围**，另立项
（§5）；`get_timeline` 的 order/可读化诉求由 §9 承接（**`semantic_search` 的时间过滤由 §11
承接并已实施**：`start_date`/`end_date` 含端点 + `timezone`，而非原议的 `start_ms`/`end_ms`）。

### 3.2 `get_recent_events(days, timezone)` 规格

```
get_recent_events(days: int = 1, timezone: str | None = None) -> dict
```

- `days`：1..31（越界 → `invalid_param`）。1 = 今天；N = 今天起回溯 N 个本地日（含今天）。
- `timezone`：**缺省/无效 → `watcher.timezone`**（§8.7 规则）；输出 `timezone` 为**真实生效标识**
  （`str(resolve_timezone(生效串))`：IANA 名 / `UTC` / `UTC+08:00`）。
- 窗口：`date_to` = 今天的本地日、`date_from` = 今天 −(days−1)；ms 边界用既有
  `day_bounds_ms(date, tz)`（`timeutil.py:57-68`），区间 `[start_of(date_from), end_of(date_to)]`。
- 返回：

```json
{"timezone": "Asia/Shanghai", "date_from": "2026-09-26", "date_to": "2026-09-27",
 "total": 1234, "truncated": true,
 "days": [
   {"date": "2026-09-27",
    "events": [{"event_ref": "…:chat:…", "type": "chat",
                "timestamp": "2026-09-27T09:12:00+08:00", "source": "chat_messages",
                "summary": "…"}]},
   {"date": "2026-09-26", "events": ["…"]}]}
```

- 查询排序 `ts_ms DESC, event_id DESC`（取最新 limit 条；event_id 为 AUTOINCREMENT，提供同毫秒
  全序兜底，`ddl.py:22`。不用 `EVENT_PRIORITY`——降序下"同刻优先级"语义不适用）。
- **按日组织**（2026-09-27 追加）：输出 `days: [{date, events}]` 数组——**日倒序**
  （最新在前，dict 插入序即降序）、**日内时间正序**（早的在先）；**无事件的日不出现**；
  截断发生在取数阶段，被截掉的总是更早的事件。日期本身即语义（无 label 字段）。
- 页大小 200（与 `get_timeline` 默认一致）；`total` = 窗口内事件总数（COUNT 走 `idx_events_ts`，
  `ddl.py:36`）；`truncated = total > len(events)`——agent 据此缩小 days 或改用 `get_timeline` 翻页。
- **轻量投影**：不含 payload 展开，`summary` 截断（≤256 字，2026-09-27 由 100 上调）。理由：31 天窗口若照 `get_timeline`
  的 payload 全展开（`store.py:236-255`）会瞬间爆 agent 上下文；详情按 `event_ref` 走
  `get_event`（video 的关键帧图随详情自带；~~图走 `get_key_image`~~——该工具 §8 已删除）。
- `timestamp` 按**请求 timezone** 渲染（`ms_to_local(ms, tz)`，`timeutil.py:79-81`）——口径与
  `get_event` 的 `ts_local`（固定服务默认时区，`store.py:302`）不同，实施时不要照抄后者。
- **时间字段全可读化**（AI 可读性，2026-09-27 追加）：不输出 epoch ms（顶层 `start_ms`/`end_ms`、
  事件 `ts_ms` 已去掉，窗口的 ms 边界仅作内部查询参数）；字段名用 `timestamp`（原 `ts_local`，
  同日改名）。~~其余工具（`get_timeline`/`search`/`semantic_search`）仍返回 `*_ms`/`ts_utc`~~——
  其中 `get_event` 由 §8 跟进、`get_timeline` 由 §9 跟进、`search` 由 §10 跟进（均改
  `timestamp` + `timezone`）；`semantic_search` 由 §11 承接并已实施——**全部 5 个工具现均输出
  本地可读时间**。
- 数据源：events 表直查（新增 store 方法）。**不读 day_summary 表**——表的生成链与 BFF 读路径
  不受影响（`services/bff/common/store.py:259-316`）。

### 3.3 REST API 规格（运维面）

| 端点 | 方法 | 请求 | 成功响应（200） |
|------|------|------|------------------|
| `/api/status` | GET | — | 与现 `get_status` 工具同构：`{service, watcher, data, recent_runs, errors}` |
| `/api/analysis/trigger` | POST | JSON `{session_id?, stages?, force?}`（复用 `TriggerParams`，`schemas.py:40-56`） | `{"accepted": true, "stages": [...]|"all", "reason": "enqueued"}` |

- 错误：沿用 `{"error": {"code", "message", ...}}` 结构 + HTTP 语义码——`invalid_param`→400、
  `not_found`（session 不存在）→404、`watcher_not_running`→503、无/错 token→401（中间件层）。
- 鉴权/日志/限流：bearer（`server.py:85-96`）、访问日志（:99-134）、限流（:137-159）的路径判定
  从 `startswith("/mcp")` 扩为 `("/mcp", "/api")`；`/healthz`/`/readyz` 维持免鉴权免限流。
- 落点：`server.py` 的 Starlette 装配（route 表 :74-82），handler 与现工具实现同源平移——
  `store.get_status` + `watcher.status()` 组装（`tools.py:275-279`）、`TriggerParams` 校验 +
  `session_exists` 预检 + `watcher.trigger`（`tools.py:298-315`）。

### 3.4 删除清单

| 项 | 位置 | 连带（删工具后无使用者） |
|----|------|--------------------------|
| `list_jobs` | `tools.py:232-265` | `ListJobsParams`（`schemas.py:59-68`）；`store.list_jobs` + `_job_out`（`store.py:114-184`）；`store._attach_cs`/`_cs_table_ok`/`self.cs_db_path`（`store.py:54/98-113`，唯一使用者是 list_jobs） |
| `get_day_summary` | `tools.py:212-230` | `store.get_day_summaries`（`store.py:374-416`） |
| `get_status` / `trigger_analysis` | `tools.py:267-315` | 无（实现平移到 REST 侧） |
| `get_key_image`（§8） | `tools.py:185-203` | `KeyImageParams`（`schemas.py:35`）；`store.get_key_image` + `_downscale`（缩放缓与缩略图缓存一并删；图片改为 `read_key_image` 并入 get_event） |

**保留勿连带删**：`recording_watch.py:121-133` 自己的 `_attach_cs`（ingress 轮询 `recording_files`
用）与 `data.chat_server_db_path`（`config.py:121`，同属 ingress）；`day_summary` 表与
`summary/day.py` 生成链（BFF 日卡数据源）；`schemas.TriggerParams`（REST 复用）。

**能力损失（需接受）**：`rsession` 未确定的 job（未关联 recording session）不再有任何查询入口
——原为 `list_jobs` 独有覆盖（`docs/recording_data_process.md:344` 裁定 D 反转）；已关联 job 的
`agent_job` 事件不受影响（search / get_timeline / get_event 可达）。

## 4. 实施清单

> **2026-09-28 注**：本节是第二轮（2026-09-27）的实施清单，其中"6 个工具"的计数已被 §8
> 收敛为 **5 个**（`get_key_image` 并入 `get_event`）；`search`/`get_timeline`/`semantic_search`
> 的入参与输出另由 §10/§9/§11 更新（均含日期窗 + `timezone` + 可读 `timestamp`）。
> 以 §7 各条实施记录与 §0 的当前结论为准。

### 4.1 代码

| 文件 | 改动 |
|------|------|
| `src/lvdata_mcp/tools.py` | 删 :212-315 四块；新增 `get_recent_events`；imports 调整（去 `ListJobsParams`；`TriggerParams` 改在 server 侧引用） |
| `src/lvdata_mcp/schemas.py` | 删 `ListJobsParams`；新增 `RecentEventsParams(days: int = Field(1, ge=1, le=31), timezone: str | None = None)` |
| `src/lvdata_mcp/store.py` | 删 §3.4 所列；新增 recent events 查询（`ts_ms BETWEEN` 走 `idx_events_ts`、降序、COUNT、轻量投影） |
| `src/lvdata_mcp/server.py` | +2 路由与 handler；中间件路径前缀扩为 `("/mcp", "/api")`（鉴权/访问日志/限流三处） |
| `main.py:44-50` | `stage` help 文案「分析类全量请用 MCP trigger_analysis」改指 REST 端点 |
| `config.py` / `recording_watch.py` / `summary/day.py` | 不动（`internal_tools` 不实施） |

### 4.2 测试

| 位置 | 改动 |
|------|------|
| `tests/test_tools.py:68` | 工具集合断言改为恰为 6 个（含 `get_recent_events`；不含 4 个删除项） |
| `:174` / `:264`（get_day_summary 用例） | 改写为 `get_recent_events`：days=1 今天窗口、days=31 上限、days=0/32 → `invalid_param`、降序 + `truncated` 正确 |
| `:193`（get_status） | 改为 REST：`urllib` 直调 `GET /api/status`（参照 `:283-309` 的 urllib 模式） |
| `:312`（trigger_analysis） | 改为 REST：`POST /api/analysis/trigger`。503 用现有 env fixture（无 watcher；`watcher_not_running` 先于 session 校验，`tools.py:298-299`）；**200/404 需自建带 watcher 的 app**（仿 `:283-309` 的最小 Pipeline+Watcher——分析器缺省 None，触发阶段被跳过不调 LLM，`pipeline.py:112-144`） |
| 新增（自建 app） | 401 用例需 app **显式配 `server.token`**——bearer 中间件仅在 token 非空时安装（`server.py:70`），现有 env fixture 未配 token（`conftest.py:223-226`）；同时断言 `/healthz`/`/readyz` 仍免鉴权 |
| 不受影响 | `:200` 的 `store.get_status` 语义测试、`:271`/`:320` 的 `TriggerParams` 校验（REST 复用同一 schema） |

### 4.3 文档

| 文档 | 改动 |
|------|------|
| `tools/lvdata-mcp/README.md` | 工具表 :94-106 改 6 个（含 `get_recent_events` 用法）；:91-92 工作流改为 `get_recent_events` 起步；:11-12 "9 个 tools（只读）+ 1 个触发工具"计数有误（9 = 8 只读 + 1 触发），顺带修正；新增 REST 端点小节（含 token 用法） |
| `docs/data/lvdata_mcp_service_v2.md:773-787`（§12） | 工具集更新（删 4 加 1）+ REST 端点说明 |
| `docs/hermes_adaptor_design_v2.md:168-175`（§3.3） | 对外 6 个；清理 v1 残留的 `list_sessions`/`get_session_summary` |
| `docs/recording_data_process.md:344/412` | 裁定 D 反转：list_jobs 删除，未关联 job 不可查 |
| `docs/bff_search_fts.md:180` | get_status 迁 REST 后，部署告警建议恢复可行（轮询 `GET /api/status`） |
| `docs/data/lvdata_agent_jobs_integration.md:392/497`、`docs/chatserver_no_redis.md:623-625/787/790` | 历史稿加注（`store.list_jobs` 已删） |

## 5. 可复议小项

1. REST 路径命名（`/api/status`、`/api/analysis/trigger`）与是否加版本号；
2. REST 是否复用 `server.token`（当前方案）还是独立 token；
3. `get_recent_events` 的页大小（200）、`summary` 截断（256 字）、降序——均可调；是否补
   `types` 过滤/游标续页（当前签名严格按 `(days, timezone)`）；
4. 不在本次范围（上轮评审列出、另立项）：~~`semantic_search` 补 `start_ms/end_ms`~~（**已由 §11
   承接并实施，口径为 `start_date`/`end_date` 含端点 + `timezone`**）、列表工具加 `fields`/`summary_only`。
   （`get_timeline` 的 order/可读化诉求由 §9 承接。）

## 6. 验收（第二轮；工具计数见注）

> **2026-09-28 注**：第 1、5 条的"6 个/6 工具表"为第二轮口径；§8 后为 **5 个**（见 §7 记录）。

1. ~~`tools/list` 恰为 6 个~~（§8 后：恰为 **5 个**），4 个删除项调用返回 `Unknown tool`；
2. `GET /api/status` 200 同构返回、无 token 401；`POST /api/analysis/trigger` 覆盖
   200 / 400 / 404 / 503，且不启动 watcher 时返回 `watcher_not_running`；
3. `get_recent_events`：days=1 只含今天、days=31 上限、越界报错、`truncated`/`total` 正确、
   输出不含 payload；
4. `tools/lvdata-mcp/tests/` 全量 pytest 全绿；
5. README / v2 §12 / hermes §3.3 同步（README 含~~6~~ 工具表与 REST 用法）。

## 7. 实施记录（2026-09-27）

- **代码**：`tools.py`（6 工具；`register_tools` 签名收敛为 `(mcp, store, vec_index)`——
  `settings`/`watcher`/`started_at` 随四工具删除而失去使用者）、`schemas.py`
  （`ListJobsParams` → `RecentEventsParams`）、`store.py`（新增
  `get_recent_events(days, timezone, limit=200)`；删 list_jobs/\_job_out/\_attach_cs/
  \_cs_table_ok/`cs_db_path`/get_day_summaries）、`server.py`（`/api/status` +
  `/api/analysis/trigger`，三处中间件前缀扩 `("/mcp", "/api")`）、`main.py`（help 文案改指 REST）。
- **测试**：`tests/test_tools.py`（工具集恰 6；get_recent_events 三例含越界/降序/截断；
  REST status/trigger 四例：503、400（坏 body）、200/404（自建 watcher、`start_watcher=False`）、
  401 + healthz/readyz 免鉴权（自建带 token app））。
- **验证**：仓库根 `.venv` 全量 `pytest tools/lvdata-mcp/tests/` —— **243 passed / 0 failed**。
  （注：tools 侧 `.venv` 未装 services 依赖，`test_rebuild_dev`/`test_rebuild_flow` 两个用例
  在该 venv 下必然失败，与本次改动无关。）
- **文档**：README（6 工具表 + REST 小节 + 计数修正）、v2 §12、hermes §3.3、
  recording_data_process 裁定 D 反转、bff_search_fts 告警建议改指 REST、
  agent_jobs_integration / chatserver_no_redis 历史加注。
- **追加（同日）**：`get_recent_events` 响应去掉全部 epoch ms 字段
  （顶层 `start_ms`/`end_ms`、事件 `ts_ms`），并把时间字段名 `ts_local` 改为 `timestamp`
  （AI 可读性）；其余工具的 `*_ms`/`ts_local` 当时未动，另行立项（`get_event` 后由 §8 跟进）。
- **追加（同日，输出改按日组织）**：平铺 `events` → `days:[{date, events}]`——
  日倒序、日内正序、无事件的日不出现；顶层 `total`/`truncated` 语义不变。
  （曾试加 today/yesterday 的 `label` 字段，同日按评审去掉。）
- **§8 实施（同日）**：`get_key_image` 工具删除（工具集 6 → 5），图片并入 `get_event`
  （video 且关键帧存在时 `[TextContent(JSON), ImageContent]` 两段；恒显式 `CallToolResult`）；
  `get_event` 加 `timezone` 入参、输出 `timestamp`/`timezone`、删 `ts_ms`/`session_id`；
  `store.get_key_image` + `_downscale`（缩放缓/缩略图缓存）删除，改 `read_key_image`
  （非 video / 缺图 → None）；`timeutil` 增 `try_resolve_timezone`（`resolve_timezone` 改薄包装），
  store 增 `_effective_tz`（请求时区无效/为空 → 服务配置；输出真实生效标识，§8.7），
  `get_recent_events` 同步升级。验证：`tools/lvdata-mcp/tests` 全量 **246 passed / 0 failed**。
- **§9 实施（同日）**：`get_timeline` 入参改 `start_date`/`end_date`（`YYYY-MM-DD` 含端点、
  格式错/逆序 → `invalid_param`）+ `timezone`；输出改按日分组（日升序、日内升序、空日不出现），
  事件为轻量投影（抽 `store._event_brief` 共用，`get_recent_events` 一并复用；无 payload/`ts_ms`/
  `ts_utc`，有 `timestamp`），`total` 改为窗口内总数（COUNT，翻页不变）；游标 keyset 不变
  （分页可跨天）；`_event_out` 保留（`vec.expand_context` 的 energy 护栏仍用它）。连带修
  `test_energy_guard`（timeline 改轻量投影后能量断言移到 `_event_out`）与
  `test_event_types`（按 days 取序）。验证：全量 **249 passed / 0 failed**。
- **§10 实施（2026-09-28）**：`search` 对齐（D1–D4 拍板见 §10.3）——`schemas.py`：
  `SearchParams` 去 `session_id`、`start_ms`/`end_ms` → `start_date`/`end_date`、加 `timezone`；
  新增 `SemanticSearchParams`（`query`/`type`/`session_id`/`limit`，字段原样）供 `semantic_search`
  复用；`timeutil` 增 `date_window_ms()`（格式错/逆序 → ValueError）；`store` 的 `_effective_tz`
  提为公开 `effective_tz`，`get_timeline` 改走 `date_window_ms`（三处调用点同步）；
  `tools.py` 的 `search` 换签名/描述并输出 `{query, timezone, searched_types, hits[{event_ref,
  type, timestamp, snippet, text, score}]}`；`index.py` 签名与 SQL 不动（`semantic_search` 与
  eval 子命令仍传 `session_id`/ms，search 侧传 `None`）。测试：`test_search_end_to_end` 加
  timezone/可读时间/字段集断言，新增 `test_search_date_window` / `test_search_bad_dates` /
  `test_search_timezone`。验证：`tools/lvdata-mcp/tests` 全量 **252 passed / 0 failed**；
  `tools/list` 中 `search` 属性为 `query/type/start_date/end_date/limit/timezone`，
  `semantic_search` 属性不变。文档：README 工具表、v2 §12 更新块加注、issue_list I-11 收窄。
- **I-1 修复（2026-09-28）**：`get_event` 的"有无图"两条推导路径合一——`store.get_event`
  改返回 `(out, img)`，`related.image_available = img is not None`（同一次 stat/read），
  定位只用 `event_ref` 后缀（不读 `payload.frame_id`，老行残留局部索引会致矛盾），
  路径解析/读取异常（越界、stat 与 read 之间文件被删）按"无图"处理、不阻断详情；
  删 `store.read_key_image`（唯一调用方是 tools 层）。
  旧实现两处矛盾的实测复现：老行（payload.frame_id="0"，真帧按 ref 存在）→ JSON 报
  `false` 却附图；非 UUID 形状 ref 后缀 → JSON 报 `true` 却无图。测试：新增
  `test_get_event_image_available_single_source`（两种情形 + 缺图同判），
  `test_energy_guard` 适配新返回形状。验证：全量 **253 passed / 0 failed**。
- **§11 实施（2026-09-28）**：`semantic_search` 接口对齐（D1–D6 全按推荐，见 §11.3）——
  `schemas.py`：`SemanticSearchParams` 去 `session_id`、加 `start_date`/`end_date`/`timezone`；
  `vec.py`：`VecIndex.search` 加 `ts_min`/`ts_max`（vec0 元数据范围约束，实测**先筛后取 k**）、
  `rrf_merge` 分开跟踪 `snippet`（FTS 高亮 `snip`）与 `text`（原文 ≤200 字）；`timeutil` 增
  `ms_to_local_or_none`（D6：`ts_ms=0` → `null`，`_event_brief`/`get_event`/`search`/
  `semantic_search` 四处统一，收口 I-8）；`tools.py` 的 `semantic_search` 重写签名/守卫/两路
  日期窗/输出组装（`expand_context` 在剥离 `session_id` 前解析），描述同步（入参同构 + D5 降级说明）。
  **顺带修一个真 bug**：FTS 路原本只 `except StoreError`，而"索引表未建"抛的是
  `sqlite3.OperationalError` → 未捕获异常直抛调用方，D5 的"静默降级"名存实亡；改 `except
  Exception` + `log.warning`。测试：`test_vec.py` 新增窗口下推（含"最近但在窗外"）与
  `rrf_merge` 字段语义两例；`test_tools.py` 新增 `semantic_env`（注入 `FakeEmbeddingProvider`
  + 临时向量库）与 6 例（对齐输出/日期窗/with_context/时区/**FTS 降级**/`embedding_disabled`）
  以及 `ts_ms=0 → timestamp null`（D6）——**semantic_search 查询路径此前零覆盖**。验证：全量
  **262 passed / 0 failed**；`tools/list` 中 `semantic_search` 与 `search` 的共享入参**逐字相同**、
  仅多 `with_context`，无 `session_id`。文档：README 工具表、v2 §12 更新块、issue_list I-11/I-8 关闭。
- **§12 实施（2026-09-28）**：`semantic_search` 的 `context_events` 改轻量投影——`vec.expand_context`
  加 `tz` 参数（生效时区串，§8.7）并改投 `store._event_brief`，`tools.py` 调用点透传；输出与
  `get_timeline` 的 `days[].events[]` 逐字同形（`{event_ref, type, timestamp, source, summary≤256}`），
  邻域规模不变（`limit=10`、±2min、`ts_ms` 升序、含命中自身）。**`_event_out` 删除**（唯一生产
  消费者是邻域），`strip_energy` 的护栏收缩为**单出口**（`get_event.detail`）；相应更新
  `store.py` 注释、`test_energy_guard.py`（去掉直调 `_event_out` 的断言、说明改单出口）、
  `docs/android_timeline_audio_gallery.md §10 E3/§10.8`（两处出口 → 一处）。测试：`test_vec.py`、
  `test_tools.py::test_semantic_search_with_context` 适配新形状（含可读 timestamp 升序断言）。
  验证：`tools/lvdata-mcp/tests` 全量 **262 passed / 0 failed**；`src`/`tests` 中 `_event_out`
  零引用（仅 `fixtures/gen_day_stats_golden.py` 的注释指向 services 侧同名方法）。真机：邻域
  10 条由 4556 B → 2618 B（1.7×，chat 为主的保守数据）。
- **§13 实施（2026-09-28）**：`semantic_search` 查询向量进程内缓存（D1–D4 全按推荐）——
  `vec.py`：`VecIndex(..., query_cache_size=500)`（带默认值，4 处调用点零改动）+ `_norm_query`
  （strip + 空白折叠，键 = embedding 输入）+ `embed_query()`（`OrderedDict` LRU、值存
  **`array('d')`** float64、命中/未命中计数、**失败不缓存**）+ `query_cache_stats()`；
  `tools.py`：`semantic_search` 改走 `await vec_index.embed_query(p.query)`，描述加"相同 query
  命中进程内缓存"；`server.py`：`api_status` 合并 `cache.query_embeddings`（`vec_index is None`
  时不出现该字段）。**测试**：`conftest` 增 `CountingEmbeddingProvider`；`test_vec.py` 增 3 例
  （命中/归一化 + 向量逐字相同、LRU 逐出、失败不缓存）；`test_tools.py` 增 `cache_env` fixture 与
  3 例（同 query 换 limit/日期窗/type/with_context 只嵌一次、`/api/status` 的 `cache` 字段、
  未配 embedding 时无该字段）。验证：全量 **268 passed / 0 failed**。文档：README（`semantic_search`
  行 + REST 小节 `cache` 字段）、v2 §12 更新块。
  ⚠️ 实施中修掉测试自身的两处误判：模块级 fixture 里 `rebuild_session` 会批量嵌入 5 条文档
  （1 次 provider 调用），断言须在 fixture 内重置计数、并以"`misses` ⇔ provider 调用数"为不变量。
- **§14 实施（2026-09-28）**：`server.py` 增模块常量 `INSTRUCTIONS`（§14.2 文案逐字）+ 
  `FastMCP("lvdata", instructions=INSTRUCTIONS, ...)`；因 **FastMCP（mcp 1.29.1）无 `version` 参数**
  （serverInfo 会回落成 SDK 版本），改为构造后补 `mcp._mcp_server.version = VERSION`（低层 `Server`
  支持 `version`，注释说明缘由）；`store.get_status` 的 `"0.1.0"` 字面量改用 `VERSION`——版本号
  三处（pyproject / `__init__.VERSION` / status 字面量）收敛为**一处**。测试：新增
  `test_initialize_metadata`（urllib 直发 initialize：`name`/`version == VERSION`/`instructions`
  **逐字**等于测试内同字面量——措辞即契约）、`test_version_single_source`（`/api/status` 同值 +
  `VERSION == pyproject.toml [project].version == importlib.metadata.version("lvdata-mcp")`；安装是前提，无条件断言）。验证：全量
  **270 passed / 0 failed**。真机：重启后 initialize 实测见 §7 同批记录 / 服务器实测输出。
- **§15 实施（2026-09-28）**：`semantic_search` 相关度可见 + 下限（D1–D6 全按推荐）——
  `config.py`：`ProviderConfig.min_vec_score: float = 0.50`（与 `dim` 同款"仅 embedding 使用"）
  + **`resolved()` 显式透传**（白名单陷阱）；`vec.py`：`rrf_merge(..., min_vec_score=None)` 为命中
  补 `fts_score`（`-bm25`）/`vec_score`（余弦相似度），过滤**保留腿内原始名次**；
  `tools.py`：从 `store.settings.providers.embedding.min_vec_score` 取 τ → 过滤向量路
  （**只减不增，不回填**）→ 丢弃时响应加 `filtered: {min_score, best_dropped}` → 描述同步；
  `schemas.py` **未动**（无新入参，§11 对称保持）。测试：`test_vec.py` 增 2 例（腿级分数暴露、
  过滤保留原始名次 + FTS 不受阈值影响）；`test_tools.py` 增 4 例（无对应查询 0 条 + `filtered`
  恰两键、yaml 设 0 的回归护栏、阈值拉高时 FTS 命中不受影响且不回填、无丢弃时不出现 `filtered`
  且双路命中同时带两个分数）；**改 2 例既有断言**（`test_semantic_search_aligned_output` 的字段集
  允许两个新分数；`test_semantic_search_fts_degraded` 改用与 summary 相同的 query——否则
  §15 阈值会把它的向量命中全滤掉，"降级仍可用"无从断言）。验证：全量 **276 passed / 0 failed**。
  文档：README（`semantic_search` 行 + 配置表 `min_vec_score`）、v2 §12 更新块。
  **追加（同日）**：`effective_summary` 为 embedding 补 `dim`/`min_vec_score`（启动日志可见生效
  检索参数；原先两者都不可见，是我在 §15.5 里写错后核实出来的盲区），测试
  `test_config.py::test_effective_summary_exposes_embedding_retrieval_params` 钉住；全量
  **277 passed / 0 failed**。**再追加（同日）**：应要求从启动摘要移除 `base_url`/`api_key`/
  `workspace_id`（provider 段只剩 `vendor`/`model` + embedding 的 `dim`/`min_vec_score`），
  原断言改为"三者在摘要里以任何形式（含打码）都不出现"；历史决策反转在
  `docs/lvdata_workspace_extend.md` §3.1c 加注。
- **§17 实施（2026-09-28，17.2-b）**：`timestamp` 改**带偏移的 ISO 8601**——渲染单点
  `timeutil.ms_to_local` 由 `strftime("%Y-%m-%d %H:%M:%S")` 改 `isoformat(timespec="seconds")`
  （偏移按**各时刻** zone 规则算），5 个工具全部出口随之生效；`timezone` 字段保持真实生效
  zone 名（17.2-b）。`instructions` 时间口径句改 ISO 版并并入 B3 排序提醒（319 → **326** 字）。
  测试：硬编码字面量改 ISO、新增 `test_timestamp_iso_format`（`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$`）
  与 `test_dst_offsets_follow_zone_rules`（美东 03-08 换季：06:10Z → `-05:00`、07:10Z → `-04:00`）。
  验证：全量 **281 passed / 0 failed**。
  **再追加（同日，用户要求）**：`semantic_search` 描述再精简 404 → **240** 字——移除
  阈值/`filtered` 的说明、`snippet`/`text` 语义说明、进程内缓存说明（**行为均未变**，仅不再
  在工具描述里说明；`vec_score`/`fts_score` 两个字段的存在保留）。描述合计 781 → **617**，
  常驻+按需合计 1061 → **897（−44% vs §16 前）**；护栏阈值随实测收紧（< 690）。文档：§14.2 文案留档第三版、§16.2 净账回填（合计 1107）、
  v2 §12 七追加。**文档示例同步**：§3.2/§8.6/§10.2/§11.2/§12.2 五处**现行契约示例**的
  `timestamp` 由旧格式改为 ISO（`…T…+08:00`）；§17.1 的"现在"行有意保留旧格式（那是本次变更
  的 before 对照）。
  **同日追加（instructions 第 4 版，用户定稿）**：时间口径句替换为「timezone是当前查询所在的
  时区；」——`instructions` 326 → **280** 字（合计 1107 → **1061**）；ISO 格式说明与 DST 排序
  提醒随之移出 agent 可见面（格式仍自描述、排序提醒在本部署无实害，见 §17.3）；`server.py`
  与 `test_tools.py` 的字面量同步，`test_tool_descriptions_delegate_conventions` 的约定清单
  去掉"服务配置时区"（该句已不在 instructions；回落行为仍可从响应 `timezone` 字段读出）。
  验证：全量 **281 passed / 0 failed**。
  **再追加（同日，用户要求）**：`semantic_search` 描述再精简 404 → **240** 字——移除
  阈值/`filtered` 的说明、`snippet`/`text` 语义说明、进程内缓存说明（**行为均未变**，仅不再
  在工具描述里说明；`vec_score`/`fts_score` 两个字段的存在保留）。描述合计 781 → **617**，
  常驻+按需合计 1061 → **897（−44% vs §16 前）**；护栏阈值随实测收紧（< 690）。
- **§16 实施（2026-09-28）**：工具描述共同块上移 `instructions`（D1–D6 全按推荐 + 用户定稿文案）——
  `server.py`：`INSTRUCTIONS` 换为定稿 + D6-a 时间口径（319 字）；`tools.py`：5 处 `description=`
  精简（**逻辑一行未动**）；`search`/`semantic_search` 保留"详情按 `event_ref` 调 `get_event`"指针句
  （D2），`semantic_search` 保留 `filtered` 的用法解释。测试：`INSTRUCTIONS` 字面量副本同步；
  新增 `test_tool_descriptions_delegate_conventions`（D5 防重复：5 条描述不得出现"含端点/
  服务配置时区/轻量投影"，指针句在 search/semantic、约定在 instructions，且描述合计 < 860 字）。
  验证：全量 **279 passed / 0 failed**（278 → 279）。**实测字数（回填 §16.2）**：描述 1416 → 781
  （`search` 99 / `semantic_search` 404 / `get_timeline` 92 / `get_event` 96 / `get_recent_events` 90）、
  `instructions` 178 → 319、合计 1594 → **1100（−31%）**；比草案表多 109 字 = D2 指针句 ×2 +
  `filtered` 解释句（草案表把它们压掉了）。文档：§14.2 文案留档更新（§16 修订沿革）、v2 §12。

## 8. 变更计划：删除 `get_key_image`，关键帧并入 `get_event`（已实施 2026-09-27）

**目标**：工具集 6 → 5。`get_event` 在事件为 video 且关键帧文件存在时，直接把 jpeg 并入返回值；
`get_key_image` 工具删除。R1–R3 已拍板（§8.5），按推荐方案实施。

### 8.1 现状（代码事实）

| 项 | 位置 |
|----|------|
| `get_key_image` 工具 | `tools.py:185-203`（返回 `[Image(jpeg), json(meta)]`，无返回注解） |
| `store.get_key_image` | `store.py:223-259`：校验 ref 属 video → 查行定位 jpg → 读字节/缩放 → meta 组装 |
| `_downscale` + 缩略图缓存 | `store.py:261-276`（Pillow，缓存 `derived/_cache/thumbs/{frame_id}_{w}.jpg`） |
| `KeyImageParams` | `schemas.py:35`（event_ref + max_width 64..4096） |
| `get_event` 现状 | `store.py:179-215` 返回 dict（`detail`=payload 去能量；`related.image_available`=磁盘判定）；工具注解 `-> dict`（FastMCP 据此生成 outputSchema） |
| 图片路径 | `_video_image_path`（`store.py:217`）= `<root>/<device>/<rsession>/video/<frame_id>.jpg`；入库时 watcher 已按 `max_image_size`（默认 1280）限长边 |

两个实现约束：
- FastMCP 的结构化输出按**返回注解**推导：`-> dict` 的函数返回列表会触发 outputSchema 校验失败
  （`func_metadata.py:331-345`）；`mcp.types.CallToolResult` 注解是 SDK 明确的"不做输出校验"路径。
- Pillow 不随本变更移除：`analysis/vision.py:86` 入库分析仍必用（`[image]` extra 保留）。

### 8.2 方案

1. **图片形态（R1 已拍板：image content block）**：把 `get_key_image` 的第一段 content 原样并入
   `get_event` 的返回值——`ImageContent` 载荷本就是 base64 jpeg。agent 客户端只会把 image block
   作为图片喂给模型（真正"看得见"）；JSON 字段里的 base64 是纯文本，吃 token 且无视觉收益。
   - 实现：`get_event` 返回注解改 `-> CallToolResult`，**恒返回显式 `CallToolResult`**——
     `content = [TextContent(JSON), ImageContent?]`，错误也包成 TextContent、`isError=False`
     （与其余工具的结构化错误约定一致）；`tools.py` 的 `Image` 导入换成 `mcp.types` 的
     `CallToolResult/TextContent/ImageContent`。
2. **尺寸与开关（R2 已拍板：不加参数）**：随 `max_width` 删除缩放与缩略图缓存，直接返回存储原图
   （长边 ≤1280）。代价是高频详情接口对每个 video 事件都带图；如需退路再议
   `include_image: bool = True` 或 `image_max_width: int | None`。
3. **`related.image_available` 保留（R3 已拍板）**：图片缺失（`false`）时是唯一解释；有图时冗余但无害。
   原 `get_key_image` 的 meta（frame_id/is_key_frame/caption/scene）与 `detail` 重叠，不再单独输出
   ——image block 存在与否即信号。
4. **store 侧**：`get_key_image` 改造为内部 helper `read_key_image(event_ref) -> tuple[bytes, str] | None`
   （非 video / 文件缺失一律 `None`，不再抛 `not_found`/`image_missing`——"没有图"从错误变成常态）；
   删 `_downscale`。**2026-09-28 再改**：该 helper 并入 `get_event`（返回 `(out, img)`）——
   图片有无与字节单一出处，见 §7 末条与 `docs/issue_list.md` I-1。
5. **描述同步**：`get_event` 描述加"video 事件且关键帧存在时附关键帧图片"；`get_recent_events`
   描述中"图片调 get_key_image"改为"video 详情自带关键帧图"。
6. **参数与输出可读化（追加拍板）**：`get_event` 新增可选 `timezone: str | None = None`
   （**无效/为空 → 回落服务配置时区**，§8.7）；输出**删 `ts_ms`、删 `session_id`**（后者只是
   `event_ref` 前缀的冗余，需要时从 ref 解析），**`ts_local` 改名 `timestamp`** 并按生效时区渲染
   （顺带修掉原来固定用服务默认时区的口径），**并加入 `timezone` 字段**（真实生效标识，否则
   timestamp 无时区语义）。`EventParams` 加字段；`get_recent_events` 的 timezone 处理同步升级
   （旧实现未校验、直接回显请求串）。

### 8.3 影响面

| 类别 | 项 |
|------|-----|
| 代码 | `tools.py`（删 get_key_image 工具；get_event 改混合 content、加 `timezone` 入参；两处描述）· `store.py`（helper 化 + 删 `_downscale`；`get_event` 加 `timezone`、输出删 `ts_ms`/`session_id`、`ts_local`→`timestamp`；新增 `_effective_tz`，`get_recent_events` 同规则升级）· `schemas.py`（删 `KeyImageParams`；`EventParams` 加 `timezone`）· `timeutil.py`（`try_resolve_timezone` + `resolve_timezone` 薄包装） |
| 测试 | 删 `test_get_key_image_by_event_ref`；`test_get_event_video_with_image_flag` 改为断言 `[text, image]` 两块；新增"jpg 缺失 → 仅 text 块 + `related.image_available=false`"、"`timezone` 生效 + 输出无 `ts_ms`/`session_id`"、"**无效 `timezone` → 回落配置时区且输出为生效标识**"；`test_get_event_chat` 的 `ts_ms` 断言改 `timestamp`；`test_tool_set` 6 → 5 |
| 文档 | `README.md`（6 → 5 工具表、工作流、`:33` Pillow 说明改为"入库缩放"）· `v2 §12` · `hermes §3.3` · 本文档 §0/§3.1/§3.4/§7 · 历史稿加注（`video_analysis.md`、`install_design.md`） |

### 8.4 验收

1. `tools/list` 恰 5 个；调用 `get_key_image` → `Unknown tool`；
2. `get_event(video, 有图)` → 两段 content：JSON + `image/jpeg`；`get_event(video, 无图)` /
   非 video → 仅 JSON，且 video 无图时 `related.image_available=false`；
3. `get_event` 输出无 `ts_ms`/`session_id`，`timestamp` 按生效时区渲染且输出 `timezone` 字段 =
   真实生效标识（显式 `timezone` 无效/为空 → 回落配置值，输出如实反映）；`get_recent_events`
   同规则；
4. `tools/lvdata-mcp/tests` 全量 pytest 全绿。

### 8.5 已拍板决策（2026-09-27）

| # | 决策 | 结论 |
|---|------|------|
| R1 | 图片形态 | **MCP image content block**（`ImageContent`，等价于"base64 jpeg 并入返回值"的 MCP 标准形态）；`get_event` 注解改 `-> CallToolResult` |
| R2 | 尺寸/开关参数 | **不加**——`max_width`/缩放缓存在内删除，返回存储原图（≤1280 长边）；需要退路时再议 |
| R3 | `related.image_available` | **保留** |

否决/搁置记录：

- R1 备选"JSON 加 `image_base64`/`image_mime` 字段"——实现更简单，但 agent 只能看到文本、无视觉收益，否决；
- R1 兜底备选"无返回注解，有图返回 `[json文本, Image]`、无图返回 dict"——调用形状不一致，否决；
- R2 备选 `include_image` / `image_max_width` 参数——YAGNI，搁置（如需再立项）。

### 8.6 `get_event` 目标契约（最终）

**输入**：`get_event(event_ref: str, timezone: str | None = None)`

**返回**：content 1–2 段——第 0 段恒为 JSON 文本（下）；第 1 段 `ImageContent`（`image/jpeg`）
仅当 `type=video` 且关键帧文件存在。

```json
{"event_ref": "…:video:019fabe5-…",
 "type": "video",
 "timestamp": "2026-07-24T15:08:30+08:00",
 "timezone": "Asia/Shanghai",
 "detail": {"frame_id": "…", "window_id": "w1", "caption": "IDE 报错页面", "place": "办公室",
            "objects": [], "ocr": [{"text": "Traceback"}], "people": []},
 "related": {"window_id": "w1", "image_available": true}}
```

- 相对现状：**删 `ts_ms`、删 `session_id`**（后者可从 `event_ref` 前缀解析），**`ts_local` →
  `timestamp`**（按生效时区渲染），新增可选入参 `timezone`（**无效/为空 → 服务配置时区**）与
  输出字段 `timezone`（**真实生效标识**：IANA 名 / `UTC` / `UTC+08:00`，§8.7）；
- video 无图 → 仅 JSON 且 `related.image_available=false`；非 video → 仅 JSON、`related={}`；
- 错误仍为结构化 error JSON（`invalid_param` / `not_found`）包在 TextContent 里，不置 `isError`；
- **恒返回显式 `CallToolResult`**（不依赖 FastMCP 对 dict/list 的隐式包装）；无效 `timezone`
  的行为见 §8.7。

### 8.7 无效 `timezone` 的处理（本轮 review 发现 → 已拍板）

**规则**：显式传入的 `timezone` **无效或为空 → 用服务配置 `watcher.timezone`**（不报错）；
返回结果携带**真实生效时区**——`str(resolve_timezone(生效串))`（ZoneInfo → IANA 名如
`Asia/Shanghai`；UTC → `UTC`；固定偏移 → 规范形式 `UTC+08:00`）。两个工具同规则
（`get_event` / `get_recent_events`）。

背景：`resolve_timezone`（`timeutil.py:31`）对不可解析的串**静默回落 UTC**；旧口径下若请求传
`"Asia/Shanghi"`，会 timestamp 按 UTC 渲染而字段回显请求串（字段说谎），`get_recent_events` 的
日界窗口也会平移。

实现：

- `timeutil` 增 `try_resolve_timezone(tz) -> tzinfo | None`（严格版：IANA / 固定偏移 → tzinfo，
  否则 None）；`resolve_timezone` 改为其 `or timezone.utc` 薄包装（其余调用方零改动）。
- store 增私有 helper `_effective_tz(timezone) -> (渲染用串, 输出用标识)`：请求串非空且
  `try_resolve_timezone` 成功才用请求值，否则回落 `settings.watcher.timezone`；输出标识
  `str(resolve_timezone(串))`——即使**配置串本身**不可解析（静默回落 UTC），输出也是诚实的 `UTC`。
- ⚠️ 实现陷阱：`str(tzinfo)` 对固定偏移产出 `UTC+08:00`，该串**不能**回喂 `resolve_timezone`
  （解析失败会再回落 UTC）；**渲染与窗口计算始终用原始生效串**，仅输出字段用规范标识。

## 9. 变更计划：`get_timeline` 改日期窗口 + 按日分组（已实施 2026-09-27）

**目标**：入参 `start_ms`/`end_ms` → `start_date`/`end_date`（`YYYY-MM-DD`）+ `timezone`；
输出与 `get_recent_events` 同构——`days: [{date, events}]`，**日升序、日内升序**。

### 9.1 现状（代码事实）

| 项 | 位置 |
|----|------|
| 工具定义 | `tools.py:156-176`（装饰器起） |
| `TimelineParams` | `schemas.py:23-28`（start_ms / end_ms / types / limit=200 / cursor） |
| `store.get_timeline` | `store.py:108-153`：直查 events，`(ts_ms, EVENT_PRIORITY, event_id)` 升序 + `[ts_ms,type,event_id]` keyset 游标；输出 `{events:[_event_out 展开 payload], next_cursor, total}`（`total` 为"本页条数"标记，`store.py:152`） |
| `_event_out` 其它使用者 | `vec.expand_context`（`vec.py:351`，semantic_search 的 `with_context`）——**不能删** |
| 现有测试 | `test_get_timeline` / `test_get_timeline_filters_and_pagination` / `test_get_timeline_same_ts_pagination`（`tests/test_tools.py:81/93/111`） |

对照 `get_recent_events`（§3.2）：本变更把 get_timeline 拉到同一约定（日期入参、§8.7 时区规则、
`days` 分组、轻量投影、`timestamp` 可读时间）。两者分工：`get_recent_events` = 最近 N 天、
**日倒序**（概览）；`get_timeline` = 指定日期段、**日升序**（按时间翻阅）。

### 9.2 方案

1. **入参**：`get_timeline(start_date?, end_date?, types?, limit=200, cursor?, timezone?)`
   - `start_date`/`end_date`：`YYYY-MM-DD`，**含端点**（起始日 00:00:00 ~ 结束日 23:59:59.999，
     即 `day_bounds_ms` 语义；其 ValueError → `invalid_param`）；`start_date > end_date` →
     `invalid_param`（R3 已拍板）；两者缺省 = 不设边界（保持现"全量"能力）。
   - `timezone`：同 §8.7（无效/为空 → 服务配置；输出为真实生效标识）。
2. **输出**（与 get_recent_events 同构）：

```json
{"timezone": "Asia/Shanghai", "date_from": "2026-07-24", "date_to": "2026-07-25",
 "total": 5, "next_cursor": null,
 "days": [{"date": "2026-07-24", "events": [
    {"event_ref": "…", "type": "chat", "timestamp": "2026-07-24T15:08:20+08:00",
     "source": "chat_messages", "summary": "…（≤256 字）"}]}]}
```

   - **日升序**（与 get_recent 的日倒序相对）、**日内升序**；排序键沿用
     `(ts_ms, EVENT_PRIORITY, event_id)`（同刻文本类靠前；keyset 游标语义不变）；
   - **无事件的日不出现**（同 §3.2；窗口内空日不产条目）；
   - **轻量投影（R1 已拍板）**：不含 payload、summary 截断 256 字——与 get_recent 完全一致
     （**抽共用投影 helper，get_recent_events 一并改为复用它**，避免两处各写一遍后漂移）；
     详情按 `event_ref` 走 `get_event`（含 video 关键帧）。`ts_ms`/`ts_utc` 随投影移除，
     时间统一 `timestamp` + `timezone`；
   - `total` = **窗口内事件总数**（COUNT）——**语义变更**：现实现是"本页条数"标记
     （`store.py:152`：`limit if 还有更多 else len(page)`），按 get_recent 口径（§3.2）改；
   - `date_from`/`date_to`：回显**生效窗口**（显式传入的日期；未传则 null）；
   - **游标保留（R2 已拍板）**：`next_cursor`（keyset 不变）。代价：分页可能把同一天切成两页
     （输出形状不变，语义照旧）；不引入 `truncated`（有游标即"还有更多"）。
3. **store**：`get_timeline(types, start_date, end_date, timezone, limit, cursor)`——日期→ms 换算
   用 `_effective_tz` + `day_bounds_ms`（store 内完成）；查询/COUNT/游标逻辑沿用，输出改
   days 分组 + 轻量投影；`_event_out` 保留（`vec.py:351` 仍用）。

### 9.3 影响面

| 类别 | 项 |
|------|-----|
| 代码 | `schemas.py`（`TimelineParams`：start_date/end_date/timezone）；`store.py`（get_timeline 签名与输出改造 + 轻量投影；`_event_out` 保留）；`tools.py`（透传新参数、描述更新） |
| 测试 | `:81` 改 days 断言 + 轻量字段；`:93` 改日期窗 + 游标翻页；`:111`（store 级同刻分页）改用日期参数；新增"逆序日期 → invalid_param""timezone 回落""日升序" |
| 文档 | README（get_timeline 行）；v2 §12（get_timeline 条目批注）；本文档 §3.x/§5（"get_timeline 加 order / `ts_utc` 可读化"从待办移除，剩 search/semantic_search）；§7 记录 |

### 9.4 验收

1. `days` 日升序、日内升序、空日不出现；`total` = 窗口内事件总数（COUNT，翻页不变）；
   事件为轻量投影（无 payload/`ts_ms`/`ts_utc`，有 `timestamp`）；
2. `start_date`/`end_date` 含端日；格式错 / 逆序 → `invalid_param`；缺省 = 全量；
3. `timezone` 生效与回落同 §8.7；
4. 游标跨页不重不漏（沿用同刻分页用例扩展）；
5. `tools/lvdata-mcp/tests` 全量 pytest 全绿。

### 9.5 已拍板决策（2026-09-27）

| # | 决策 | 结论 |
|---|------|------|
| R1 | 事件投影 | **轻量**（与 `get_recent_events` 完全一致：无 payload、`summary` ≤256 字、`timestamp` 可读时间；详情走 `get_event`） |
| R2 | 游标 | **保留 `next_cursor`**（keyset 不变；不引入 `truncated`，有游标即"还有更多"） |
| R3 | `start_date > end_date` | **`invalid_param`** |

否决/搁置记录：

- R1 备选"保留 payload 展开"——两块工具将只是外壳一致、token 成本高，否决；
- R2 备选"get_recent 式 200 条上限 + `truncated`"——日期粒度窗口下，单日超 200 条即无法读全，否决。

## 10. 变更计划：`search` 参数/输出对齐（日期窗 + `timezone` + 可读时间）（已实施 2026-09-28）

**目标**：`search` 与 §9 后的 `get_timeline`/`get_recent_events` 同口径——入参去 `session_id`，
`start_ms`/`end_ms` → `start_date`/`end_date`（`YYYY-MM-DD` 含端点）+ `timezone`；输出加
`timezone`、`ts_ms` → `timestamp`。工具集仍 5 个，仅 `search` 一个工具变化。

### 10.1 现状（代码事实）

| 项 | 位置 |
|----|------|
| 工具签名 | `tools.py:53-60`：`search(query, type?, session_id?, start_ms?, end_ms?, limit=10)` |
| 入参模型 | `schemas.py:14-20` `SearchParams`：`session_id` + `start_ms`/`end_ms`（epoch ms，`ge=0`）+ `limit` 1..100；`query` 1..200 |
| FTS 查询 | `index.py:177-205` `search(conn, fts_query, scope, session_id, start_ms, end_ms, limit)`——`WHERE search_docs MATCH ? AND session_id = ? AND ts_ms >= ? AND ts_ms <= ?`，`score = -bm25` |
| 输出 | `tools.py:85-100`：`{query, searched_types, hits[{event_ref, session_id, type, ts_ms, snippet, text, score}]}`（无时区字段、epoch ms） |
| 同模型复用 | `semantic_search`（`tools.py:118`）用同一 `SearchParams`，仍用 `session_id`、无日期参数 |
| 其他调用点 | `main.py:240` eval 子命令 `index_mod.search(conn, fts_q, limit=topk)`（kwargs）；`tests/test_index.py:83/89` 同理——均不受本变更影响 |
| bff 侧 | `/search` 是独立副本（`services/bff/search/fts.py`）；契约测试（`services/tests/unit/bff/test_search_api.py`）守护的是**查询串构造**等价（`build_fts_query`），非 `index.search` 签名 → 本变更不触 |
| 消费者 | repo 内无硬编码参数调用点：Hermes 提示词只列工具名（`services/agent_relay/prompts/job_task.md:13`）、README:99 工具表无参数描述 |

口径参照：§9（`get_timeline`）已完成同款改造；时区回落与输出标识沿用 §8.7 规则。

### 10.2 目标契约

```
search(query: str, type?: EventType, start_date?: str, end_date?: str,
       limit: int = 10, timezone?: str) -> dict
```

- `start_date`/`end_date`：`YYYY-MM-DD` **含端点**（本地日界，`day_bounds_ms` 语义，同 §9.2）；
  缺省 = 不限；格式错 / 逆序 → `invalid_param`（同 §9.5 R3）。
- `timezone`：缺省/无效 → 服务配置时区（§8.7，不报错）；输出 `timezone` 为**真实生效标识**
  （IANA 名 / `UTC` / `UTC+08:00`）。
- 返回：

```json
{"query": "狐狸", "timezone": "Asia/Shanghai",
 "searched_types": ["chat", "transcribe", "translate", "window", "video", "audio", "agent_job"],
 "hits": [{"event_ref": "…:chat:…", "type": "chat",
           "timestamp": "2026-09-27T13:53:49+08:00", "snippet": "…<b>狐狸</b>…",
           "text": "…（原文 ≤200 字）", "score": 7.8011}]}
```

- **移除**：入参 `session_id`；命中 `ts_ms`（→ `timestamp`，按生效时区渲染）；命中 `session_id`
  （见 D1）。
- **不变**：`query`/`searched_types`/`snippet`（bigram 高亮文本，机器可读）/`text`/`score`/
  `limit` 1..100/停用词与纯分隔符守卫/`index_not_built` 兜底。
- **实现口径**：日期→ms 用 `[day_bounds_ms(start_date, tz)[0], day_bounds_ms(end_date, tz)[1]]`
  传给 `index.search`（索引侧 SQL 与 `ts_ms` 索引不变）。

### 10.3 已拍板决策（2026-09-28）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 命中里的 `session_id` | **删除**——`event_ref` 前缀已含 session（同 §8.2.6 对 `get_event` 的裁定） |
| D2 | 旧参数（`start_ms`/`end_ms`/`session_id`） | **不支持**：直接删、无兼容层（同 §9 做法）。已知代价：FastMCP 对未声明参数**静默忽略**（pydantic `extra='ignore'`，实测 `tool.run({"a":5,"b":2})` 正常返回、多余键被丢弃）→ 老客户端传旧参数会"过滤被静默丢弃、结果集变大"且不报错；因 MCP 客户端按 `tools/list` 动态发现、repo 内无硬编码调用点，接受该风险 |
| D3 | 回显 `date_from`/`date_to` | **不加**——窗口由调用者给出，无回显必要 |
| D4 | `semantic_search` 同批统一 | **先不做**（另立项；本变更后 `docs/issue_list.md` I-11 收窄为它一处） |

其余按 §10.4 推荐执行：时区换算取 **c2**（`timeutil.date_window_ms()` 共用 + `_effective_tz` 提为公开
`effective_tz`），`index.search` 签名不动。

### 10.4 实施清单（已执行，见 §7 末条）

**代码**

| 文件 | 改动 |
|------|------|
| `schemas.py` | `SearchParams` 去 `session_id`、`start_ms`/`end_ms` → `start_date`/`end_date`、加 `timezone`；**新增 `SemanticSearchParams`**（`query`/`type`/`session_id`/`limit`，现状字段原样平移）供 `semantic_search` 复用 |
| `tools.py` | `search` 签名/描述/输出改造；日期→ms 换算；调用点改 `_search(conn, fts, p.type, None, start_ms, end_ms, p.limit)`；`semantic_search` 换用新模型 |
| 时区换算落点 | 采用 c2：`DataStore._effective_tz` 提为公开 `effective_tz`（3 处调用点同步：`get_timeline`/`get_recent_events`/`get_event`）；新增 `timeutil.date_window_ms(start_date, end_date, tz_name) -> tuple[int\|None, int\|None]`（含格式错/逆序校验，抛 ValueError），**`get_timeline` 一并改用它**，保证两处日期窗语义不漂移 |
| `index.py` | **签名不动**（`semantic_search` 与 eval 子命令仍传 `session_id`/ms） |

**测试**（`tests/test_tools.py`）

- 改：`test_search_end_to_end`——加断言：命中带 `timestamp`、无 `ts_ms`、顶层 `timezone`、日期窗生效
  （沿用 `ms_to_local_date(TS, "Asia/Shanghai")` 锚定日，避免跨日不确定）。
- 新增：`start_date`/`end_date` 含端点；格式错 / 逆序 → `invalid_param`；`timezone` 显式生效 +
  无效回落（仿 `test_get_timeline_timezone` 写法）；无命中仍为 `hits: []`（非错误）。
- 不受影响：`test_index.py`（直调 `index.search`，全 kwargs）、`test_tool_set`（仍 5 个）、
  `tests/test_vec.py`（semantic_search 走 `SemanticSearchParams`，字段等价）。

**文档**

- 本文档：§10 状态与 §7 实施记录追加；
- `docs/issue_list.md`：I-11 收窄为 `semantic_search`（含 `with_context` 的 `_event_out`）；
- `tools/lvdata-mcp/README.md`：工具表 `search` 行补"日期窗 + `timezone` + 可读 `timestamp`"；
- `docs/data/lvdata_mcp_service_v2.md` §12 更新块：加一条 `search` 批注。

### 10.5 验收

1. `tools/list` 中 `search` 的 inputSchema 无 `session_id`/`start_ms`/`end_ms`，有
   `start_date`/`end_date`/`timezone`；
2. 日期窗含端点、逆序 / 格式错 → `invalid_param`；`timezone` 无效/为空 → 回落服务配置且输出为
   真实生效标识（§8.7）；
3. 命中含 `timestamp`、不含 `ts_ms`（D1 通过则也不含 `session_id`）；顶层含 `timezone`；
4. `tools/lvdata-mcp/tests` 全量 pytest 全绿；
5. README / v2 §12 / issue_list I-11 同步。

## 11. 变更计划：`semantic_search` 与 `search` 接口对齐（已实施 2026-09-28）

**目标**：`semantic_search` 拉齐到 §10 后的 `search` 同一套入参/输出约定（日期窗 + `timezone` +
可读 `timestamp`、去 `session_id`、`snippet`/`text` 同义），保留其独有的双路召回与 `with_context`。
即 `docs/issue_list.md` I-11 的收口。

### 11.1 现状（代码事实）

| 项 | 位置 |
|----|------|
| 工具签名 | `tools.py:118-124`：`semantic_search(query, type?, session_id?, limit=10, with_context=false)` |
| 入参模型 | `schemas.py:26-32` `SemanticSearchParams`（无日期、无 `timezone`） |
| 双路召回 | `tools.py:144-153`：vec 路 `vec_index.search(qv, session_id, type, limit*3)`（`vec.py:286-308`，vec0 KNN、余弦距离 → `1-distance`）；FTS 路 `_search(conn, fts, type, session_id, None, None, limit*3)`（**无日期**） |
| FTS 缺索引 | `tools.py:152-153` `except StoreError: fts_hits = []`（**静默降级为纯向量**，与 `search` 的 `index_not_built` 不同） |
| 融合 | `vec.py:313-333` RRF k=60；`via` 标注来源；`snippet` = FTS 原文 或 vec chunk 文本 |
| 邻域 | `vec.py:336-350` `expand_context`：用**命中里的** `session_id` + `ts_ms` 取 ±2min |
| 输出 | `{query, mode:"hybrid", hits[{event_ref, session_id, type, ts_ms, score, via[], snippet, context_events?}]}`——epoch ms + `session_id` |
| 向量库维护 | `pipeline.py:132-139` embed stage：`sync_changed()`（per-event `vec_state` 水位）/ `rebuild_all` / `rebuild_session` |
| 消费者 | repo 内**无代码调用点**（Hermes 提示词只列工具名）；`main.py` eval 只用 `rrf_merge` 的 `event_ref` |

对照 `search`（§10 后）：入参 `query/type/start_date/end_date/limit/timezone`；输出
`{query, timezone, searched_types, hits[{event_ref, type, timestamp, snippet, text, score}]}`。

### 11.2 目标契约

```
semantic_search(query, type?, start_date?, end_date?, limit=10,
                timezone?, with_context=false) -> dict

{"query": "...", "timezone": "Asia/Shanghai", "mode": "hybrid",
 "searched_types": [7 类],
 "hits": [{"event_ref", "type", "timestamp", "score", "via": ["fts", "vec"],
           "snippet": "…", "text": "…（原文 ≤200 字）",
           "context_events": [ …（仅 with_context=true）]}]}
```

- **入参**：除 `with_context` 外与 `search` 逐字相同——`start_date`/`end_date`（`YYYY-MM-DD`
  含端点、格式错/逆序 → `invalid_param`，复用 `timeutil.date_window_ms`）、`timezone`（§8.7
  回落规则）；去 `session_id`；`type`/`limit` 不变。
- **输出**：`ts_ms` → `timestamp`（按生效时区渲染）、顶层加 `timezone` 与 `searched_types`、
  命中删 `session_id`；`snippet` = FTS 高亮片段（**纯向量命中为空串**）、`text` = 可读原文
  ≤200 字——与 `search` 同名同义（`rrf_merge` 需分开跟踪两个字段，见 D2）。
- **日期窗作用于两路**：FTS 路按 `date_window_ms` 换算后传入（与 `search` 同一路径）；vec 路
  给 `VecIndex.search` 加 `ts_min`/`ts_max`，追加到 vec0 的 KNN `WHERE`。
- **`with_context`**：命中的 `session_id` 由服务端**在剥离前**消费（`expand_context` 仍按内部
  命中的 session 取邻域），不进输出。
- 无时间戳事件（`vec_chunks.ts_ms = 0` / `search_docs.ts_ms = 0`）在任何日期窗下都不可召回
  （与 §10 的 `search` 行为一致；不带窗口时照旧可召回）。

### 11.3 决策（2026-09-28：D1–D6 全按推荐拍板）

| # | 决策 | 结论 |
|---|------|------|
| D1 | `session_id`（入参与命中） | **删除**（同 §10 D1）。`VecIndex.search` 保留 session 参数（eval/未来复用不传），工具侧传 `None` |
| D2 | 命中字段语义 | **与 `search` 完全同名同义**：`snippet` = FTS 高亮片段（纯向量命中为空串）、`text` = 可读原文 ≤200 字——`rrf_merge` 分开跟踪两字段（eval 只读 `event_ref`，不受影响） |
| D3 | 顶层字段 | 加 `searched_types`（与 `search` 对称）；`mode: "hybrid"` **保留**（加法，指明融合来源） |
| D4 | `with_context` | **保留**——语义检索独有的"片段→时间上下文"能力 |
| D5 | FTS 缺索引的静默降级 | **保持**（向量路仍可用，可用性优先）；**工具描述里写明**"FTS 索引不可用时不报错、结果仅来自向量路"，不做 `index_not_built` |
| D6 | `ts_ms = 0`（无时间戳事件）的对外表示（review 本轮发现，属顺带收口 I-8） | **采用 a**：`timestamp: null`（与 `get_event` 现状一致），`_event_brief`（`get_timeline`/`get_recent_events`）与 `search`/`semantic_search` 命中一并统一（`timeutil.ms_to_local_or_none`）——`ms_to_local(0)` 不再渲染 `1970-01-01 08:00:00`（哨兵值伪装成数据）；过滤/排序行为不变，`ts_ms=0` 仍只在"无日期窗"查询下可见。**残留（已知、可接受）**：`get_timeline` 的**按日分组**仍以 `ts_ms` 取日 → 这类事件落在 `days[].date = "1970-01-01"` 桶里（其 `timestamp` 为 `null`）——dev 库当前 0 条；若要连桶一起剔除需另立项 |

否决/搁置记录：

- D2 备选"保持现状（`snippet` = 可读原文、不加 `text`）"——两工具命中字段不同义，agent 要两套
  解析，与"接口对齐"目标相悖，否决；
- D4 备选"删除 `with_context`"——为逐字相同而丢能力，否决；
- D5 备选"改报 `index_not_built`"——FTS 未建时会让向量检索整体不可用（可用性倒退），否决；
- `with_context` 的落地约束（实现时不可漏）：`expand_context` 依赖命中里的 `session_id`，
  必须在**剥离 `session_id` 之前**、于服务端内部完成邻域解析。

### 11.4 影响面（已拍板，待执行）

**代码**

| 文件 | 改动 |
|------|------|
| `schemas.py` | `SemanticSearchParams` → `query/type/start_date/end_date/limit/timezone`（`with_context` 留在函数签名，无需校验）；docstring 去掉 I-11 引用 |
| `tools.py` | `semantic_search` 签名/守卫/日期换算（`store.effective_tz` + `date_window_ms`）/输出组装；`expand_context` 处理移到剥离 `session_id` 之前；**工具描述同步**（日期窗 + `timezone` + 可读时间措辞，同 `search`；并写明 D5 的 FTS 降级行为） |
| `vec.py` | `VecIndex.search(..., ts_min=None, ts_max=None)`：WHERE 追加 `ts_ms >= ? / <= ?`；`rrf_merge` 分开跟踪 `snippet`（FTS 高亮）与 `text`（原文 ≤200 字） |
| `index.py` | 不动 |
| `timeutil.py` + `store.py` | D6（已采用 a）：`timeutil.ms_to_local_or_none`；`_event_brief`/`get_event` 与 `tools.search`/`semantic_search` 命中统一用它（`ts_ms=0` → `timestamp: null`） |

**测试**（当前**无**查询路径覆盖，本次补上）

- 工具级（**注入 `FakeEmbeddingProvider`**——`tests/conftest.py` 已有，`create_app(vec_index=...)`
  支持注入；临时 `rag_db_path`）：`timestamp`/`timezone` 断言、命中无 `session_id`、日期窗含端点、
  窗外近邻不入选（向量路先筛后取 k）、`with_context` 的 `context_events` 仍正确（`_event_out` 全量——**§12 起改轻量投影**）。
- vec 层：`VecIndex.search(ts_min/ts_max)` 过滤正确性（含"距离最近但在窗外"用例）。
- 回归：`test_vec.py` 现有调用（不传新参数）不受影响；`test_tool_set` 不变（仍 5 个）。

**文档**：README 工具表 `semantic_search` 行；v2 §12 更新块加条目；`docs/issue_list.md` I-11
标已修（D6 通过则 I-8 一并关闭）；本文档 §0/§3.1/§3.2/§5.4 的批注——**已在本轮 review 先行修正**
（`semantic_search` 的"仍返回 `*_ms`""补 `start_ms/end_ms`"等表述均改为"由 §11 承接"）。

### 11.5 证据（本轮实测，支撑方案可行性）

- **sqlite-vec 0.1.9 支持 vec0 元数据列的范围约束**（`>=`/`<=`、`BETWEEN`，可与 `=` 混用），
  且 **KNN 是"先筛后取 k"**：构造"最近两行在窗外、第三近在窗内"，`k=1 + 窗口[700,1000]`
  返回窗内那条（若为"先取 k 后筛"会返回空）→ 日期窗不会丢窗口内召回。
- 测试环境已有 `FakeEmbeddingProvider`（`tests/test_vec.py:11,20`），工具级用例可覆盖查询路径。

### 11.6 验收

1. `tools/list` 中 `semantic_search` 与 `search` 的入参除 `with_context` 外逐字相同，无 `session_id`；
2. 日期窗含端点、逆序/格式错 → `invalid_param`；窗口同时过滤两路（窗外近邻不入选）；
3. 返回含 `timezone`（真实生效标识）、`mode`、`searched_types`；命中含可读 `timestamp`，无 `ts_ms`/`session_id`；
4. `with_context=true` 时 `context_events` 正确（服务端在剥离 `session_id` 前解析；投影口径见 §12）；
5. （D6 按 a 通过时）`ts_ms=0` 的事件在 `search`/`semantic_search`/`get_timeline`/`get_recent_events`
   一律输出 `timestamp: null`，不再出现 `1970-01-01 …`；过滤/排序行为不变；
6. `tools/lvdata-mcp/tests` 全量 pytest 全绿。

## 12. 变更计划：`context_events` 改用轻量投影（`_event_brief`）（已实施 2026-09-28）

**目标**：`semantic_search(with_context=true)` 的邻域事件从 `_event_out`（payload 全展开 + epoch ms）
改为与 `get_timeline`/`get_recent_events` 相同的 `_event_brief`（轻量、可读时间）；`_event_out`
随之成为死代码删除，能量护栏收缩为**单出口**（`get_event.detail`）。邻域规模 `limit=10`
与 ±2min 窗**保持不变**（已拍板）。动因：投影口径与 `get_timeline` 统一（MCP 面最后一处
epoch ms 消失）、token 收益实测 1.7×（payload 重的行更大）、护栏点 2 → 1。

### 12.1 现状（代码事实）

| 项 | 位置 |
|----|------|
| 邻域构造 | `vec.py:350-365` `expand_context(store, hit, window_ms=120_000, limit=10)` → `[store._event_out(r) …]`，`ts_ms` 升序、含命中自身 |
| `_event_out` | `store.py:193-212`：`event_ref/type/ts_ms/ts_utc/source/summary` + payload 全展开（剔能量）——**生产代码唯一消费者是 `expand_context`**。测试侧引用两处：`test_energy_guard.py`（改投 `get_event` 后即可删该断言）与 `test_vec.py` 的直接调用；⚠️ **`tests/fixtures/gen_day_stats_golden.py` 的 `StubStore` 是 services 侧 day_stats 出库语义的镜像**（`_derive_images` 读 payload 的 `frame_id`/`window_id`，对应的是 services 的 store），**与本变更无关、不需重生成 golden** |
| `_event_brief` | `store.py:179`：`event_ref/type/timestamp(可读)/source/summary[:256]`；`get_timeline`/`get_recent_events` 共用（§3.2/§9 R1）——邻域改投它即可与两者逐字同形 |
| 调用点 | `tools.py:175` `ctx = expand_context(store, h) if with_context else None`——**在剥离 `session_id` 前**（§11.3 约束） |
| 能量护栏 | `store.py:29-37 strip_energy` + `tests/test_energy_guard.py`：现约定"两处出口"（`_event_out` 与 `get_event.detail`）；出处 `docs/android_timeline_audio_gallery.md §10 E3` |
| 实测规模（2026-09-28 review 修正） | 同一 10 条邻域（dev 真实数据）：`_event_out` **4556 B** → `_event_brief` **2618 B**，比值 **1.7×**。dev 数据以 chat 为主（`summary` 本身较长、payload 近空），故收益偏保守；**audio（`transcript`/`emotions`）、video（`ocr`/`objects`）这类 payload 重的行比值显著更大**。 |

### 12.2 目标契约

```jsonc
// semantic_search(with_context=true) 的 hits[i].context_events[j]
{"event_ref": "…:chat:…", "type": "chat",
 "timestamp": "2026-09-27T13:53:49+08:00", "source": "chat_messages",
 "summary": "…（≤256 字）"}
```

- 与 `get_timeline` 的 `days[].events[]` **逐字同形**；`timestamp` 按生效时区渲染（时区语义同
  响应顶层 `timezone`）；无 payload / `ts_ms` / `ts_utc`。
- **不变**：`limit=10`、`window_ms=120_000`（±2min）、`ts_ms` 升序、含命中自身、仅在
  `with_context=true` 时出现。
- 细节获取路径：邻域事件的 payload（audio `transcript`、video `ocr`/关键帧等）按 `event_ref`
  调 `get_event`——与 §9 R1 对 `get_timeline` 的裁定同构。

### 12.3 影响面（拍板后执行）

| 类别 | 项 |
|------|-----|
| 代码 | `vec.py`：`expand_context(store, hit, tz, window_ms=120_000, limit=10)` → `[store._event_brief(r, tz) …]`，docstring 指明"细节走 get_event" · `store.py`：删 `_event_out`，`strip_energy` 注释改为"唯一出口 = `get_event.detail`" · `tools.py:175`：调用点透传生效时区串（`store.effective_tz` 的第一个返回值，§8.7 的"渲染用原始生效串"约定） |
| 测试 | `test_tools.py` 的 `test_semantic_search_with_context`：断言字段集 `{event_ref,type,timestamp,source,summary}`、`timestamp` 可读、无 `ts_ms`/`ts_utc` · `test_vec.py::test_expand_context_from_events`：适配新签名/字段 · `test_energy_guard.py`：删直接调 `store._event_out` 的断言（改由 `get_event` 单出口覆盖），注释与文档字符串同步 |
| 文档 | `docs/android_timeline_audio_gallery.md §10 E3`（"两处出口"→单出口）加注 · `lvdata_mcp_service_v2.md §12` 更新块加条目 · `README.md` 的 `semantic_search`/工作流行（`with_context` 邻域为轻量投影）· 本文档 §7 实施记录 + §11.4/§11.6 已加注（§12 承接） |
| 连带的删减 | `_event_out` 删除后，"能量只可能出现在 payload 里"的出口只剩 `get_event` 一处——护栏点从 2 → 1（**风险下降**，但 `test_db_row_keeps_energy`（数据源保留）与 `strip_energy` 幂等用例保留） |

### 12.4 验收

1. `with_context=true` 的 `context_events` 字段集恰为 `{event_ref, type, timestamp, source, summary}`；
   `timestamp` 按生效时区渲染、无 `ts_ms`/`ts_utc`、无 payload 展开；
2. 邻域语义不变：±2min、同会话、`ts_ms` 升序、最多 10 条、含命中自身；
3. `_event_out` 在 `src/` 与 `tests/` 中零引用（grep 干净）；
4. `tools/lvdata-mcp/tests` 全量 pytest 全绿。

## 13. 变更计划：`semantic_search` 查询向量的进程内缓存（LRU 500）（已实施 2026-09-28）

**目标**：缓存 query string → embedding（最近 500 条，进程内 LRU），消除重复查询的
embedding API 往返（~93 ms p50 / 461 ms tail）与相应的失败面；**只作用于查询侧**，文档
（events.summary）嵌入路径（`sync_changed`/`rebuild_*`）不变。

### 13.1 现状与动因（2026-09-28 真机基准，`docs/... §11/§12` 后）

| 事实 | 数值 |
|------|------|
| `semantic_search` 端到端（线上 server，服务端自记） | p50 **114 ms** / min 78 / max 458 |
| 其中 embedding API 往返（本地拆解，连接复用） | p50 **93 ms**（p90 219 / max 498）→ **占端到端 ~82%** |
| 本地检索（vec KNN 5.6 + FTS 0.8 + RRF ≈ 7 ms） | p50 7.2 ms |
| 缓存命中后的预期端到端 | ≈ **8–15 ms**（只剩本地链路 + MCP 层；tail 也无 API 抖动） |

**重复嵌入确实存在**：embedding 只依赖 `query` 文本——`limit`/`type`/`start_date`/`end_date`/
`timezone`/`with_context` 都**不影响**向量。同一 query 换参数重查（agent 常见：先 `limit=5`
看概览、再 `limit=20`、或加日期窗、或开 `with_context`）会**重复调用 API**。本次基准测试
的调用序列里 32 次 `semantic_search` 只有 3 个不同 query。

### 13.2 方案

1. **落点**：`VecIndex.embed_query(text) -> list[float]`（`vec.py`），内部 LRU；`tools.py` 的
   embedding 调用改走它（`main.py eval` 可选同样改走，收益为零、不做）。**不放在 provider 上**
   ——provider 同时服务文档批量嵌入（`sync_changed` 每批 64 条），共享缓存会被索引写入冲刷、
   且把"查询缓存"与"文档嵌入"两件事耦在一起。
2. **LRU 实现**：`collections.OrderedDict` + `move_to_end`，容量 = 构造参数
   `VecIndex(..., query_cache_size: int = 500)`（**带默认值的关键字参数 → 现有 4 处调用点
   `VecIndex(cfg, store, provider)` 零改动**；测试注入小容量验证逐出）。
   ⚠️ **不要用 `functools.lru_cache` 包 async 函数**——它缓存的是协程对象而非结果（经典坑）。
3. **键**：query 归一化（`strip()` + 内部空白折叠；**不折大小写**——英文大小写对向量有微小
   语义差，保守不合并）。归一化后的串既作 key 也作 embedding 输入（避免"key 相同但输入不同"）。
4. **存什么**：`array('d', vec)`（float64，1024 维 ≈ 8.1 KB/条，**500 条 ≈ 3.9 MB**；取用处
   `list()` 还原成 `vec.search` 需要的形状）。
   ⚠️ **不可用 `array('f')`**（float32，省一半内存）：实测 float32 往返**不逐位还原**（`json.dumps`
   都不同）→ 命中与未命中会喂给 KNN 两个略有差异的向量 → 近并列（near-tie）排序可能翻转，
   "同 query 同参数结果稳定"这条不变量会变成数据相关的 flaky。备选"把未命中路径也统一 round
   到 float32"同样被拒——为省 2 MB 去改未命中路径的检索向量口径，不值得。
5. **失败不缓存**（无负缓存）：API 异常照旧返回 `embedding_failed`，不写缓存——否则一次
   瞬时失败会粘住 500 条窗口内的该 query。
6. **不落盘、不跨进程**：uvicorn 单 worker（当前部署形态）→ 缓存随进程生命周期；模型/维度
   切换天然靠重启隔离（**这正是选进程内的原因**：跨模型复用向量是错的，落盘方案必须额外
   处理失效）。多 worker 部署时每 worker 各持一份（可接受，需在文档写明）。
7. **并发**：不做 single-flight（并发相同 query 各嵌一次、幂等覆盖）；如日后发现 agent 会
   并发发同一 query，再补"in-flight 去重"。缓存的读写只发生在 `embed_query` 内、且全部在
   **单事件循环**上（`semantic_search` 是 async；`VecIndex` 的其它路径 `sync_changed`/
   `rebuild_*` 不碰这个缓存）→ **无需加锁**（实施时不必引入 `asyncio.Lock`）。命中返回的是
   `list(...)` 副本，逐出/复用不会让调用方拿到被共享的可变对象。
8. **可观测**：命中/未命中/当前容量计数（见 D2）。
9. **工具描述**：加一句"相同 query 命中进程内缓存"，暗示 agent **复用同一 query 串**更划算
   （行为引导，零成本）。

### 13.3 决策（2026-09-28：D1–D4 全按推荐拍板）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 缓存落点 | **`VecIndex.embed_query()`**——查询专属、与 provider 的文档嵌入隔离、per-app 生命周期 |
| D2 | 指标暴露 | **暴露**：`GET /api/status` 增 `cache: {query_embeddings: {size, hits, misses}}`——命中率是"500 是否合适、机制是否值得留"的唯一判据 |
| D3 | 容量可配置？ | **常量 + 构造参数**：`VecIndex(..., query_cache_size=500)`（测试注入小容量）；yaml 暂不加键（YAGNI；将来要"生产可关"再加 `providers.embedding.query_cache_size`，0 = 关闭） |
| D4 | single-flight（并发去重） | **不做**（YAGNI；如 agent 出现并发同 query 再立项） |
| D5 | 语义影响 | **无**：同一 query 文本 → 同一向量（embedding 是纯函数）；`via`/排序/截断/错误码全不变 |
| D6 | 文档嵌入缓存 | **不做**：文档嵌入按 `vec_state.updated_at` 水位增量、重复率低，且缓存会被索引写入自身冲刷 |

**已否决/搁置**：落盘（SQLite 表）共享缓存——查询平面刻意只读（`DataStore.connect(readonly=True)`），
为缓存引入写路径不划算，且要处理模型切换失效；**结果级缓存**（query+参数 → hits）——能省 7 ms
本地开销但引入陈旧性（新事件/新索引 → 结果过期），本方案的瓶颈是 93 ms 的 API 往返，非本地检索。

### 13.4 影响面（已执行，见 §7 末条）

| 类别 | 项 |
|------|-----|
| 代码 | `vec.py`：`VecIndex.__init__(..., query_cache_size=500)` + `embed_query()`（归一化、LRU、计数）+ 命中即跳过 API；`tools.py`：`semantic_search` 的 `vec_index.provider.embed([p.query])` → `await vec_index.embed_query(p.query)`，描述加一句；`server.py`：`api_status` handler 里合并 `out["cache"] = {"query_embeddings": vec_index.query_cache_stats()}`（**同该 handler 合并 `watcher.status()` 的既有做法**；`vec_index is None`（未配 embedding）→ 不出现 `cache` 字段，与 `watcher is None` 的处理一致） |
| 测试 | 用计数版 `FakeEmbeddingProvider`（或包装现有 fake）+ 小容量注入：① 同 query 两次 → provider 只被调 1 次，且命中/未命中返回的向量 `json.dumps` **逐字相同**（`array('d')` 的确定性护栏）；② 同 query 不同参数（limit/日期窗/type/with_context）→ 仍只 1 次；③ 归一化（`" 狐狸 "` 与 `"狐狸"`）→ 1 次；④ 逐出（容量 2 + 3 个 query）→ 最旧被重算；⑤ 失败不缓存（provider 抛错后再调成功 → 第二次真的调了 API）；⑥ 现有 semantic_search 用例全绿（行为不变）；⑦ `/api/status` 的 `cache.query_embeddings` 随调用变化（hits/misses/size），未配 embedding 时无该字段 |
| 文档 | README（`semantic_search` 行加"相同 query 进程内缓存"；REST 小节补 `/api/status` 的 `cache` 字段）；v2 §12 更新块加条目；本文档 §7 实施记录 |

### 13.5 收益与风险

- **收益**：命中时端到端 ≈ 8–15 ms（vs p50 114 ms / tail 458 ms），并少一次外部 API 依赖
  （`embedding_failed` 面减小）。**命中率未知**——取决于 agent 是否复用 query 串；D2 的指标
  就是为量化它（若长期 <5%，可把容量降到 100 或直接撤掉本机制）。
- **风险/代价**：内存 ~3.9 MB（`array('d')` × 500，见 §13.2 第 4 条为何不用 float32）；缓存
  错误向量不可能（同进程模型固定 + 输入归一化一致 + float64 无精度损失）；重启即失效（可接受）；
  **不改** `/opt/lvdata` 任何文件（纯内存，查询平面仍只读）。

### 13.6 验收

1. 同 query 重复调用时，provider 只被调用一次（计数断言），**命中与未命中喂给 KNN 的向量逐位
   相同**（`array('d')` + 归一化保证），两次返回结果逐字相同；
2. `limit`/`type`/`start_date`/`end_date`/`timezone`/`with_context` 变化不触发新 embedding；
3. `" 狐狸 "` 与 `"狐狸"` 共用一条缓存；容量逐出按 LRU；失败不写缓存；
4. `GET /api/status` 能看到 `cache.query_embeddings` 的 `size/hits/misses`，且随调用变化；
5. `tools/lvdata-mcp/tests` 全量 pytest 全绿；真机复测：同 query 第二次调用的服务端
   `latency_ms` 从 ~110 ms 降到 ~10 ms 量级。

## 14. 变更计划：MCP server 元数据——`instructions` + 真实版本号（已实施 2026-09-28）

**目标**：agent `initialize` 时能读到"这是什么服务、怎么用"的一段文字（MCP 的
`InitializeResult.instructions`，目前为空），并把 `serverInfo.version` 从 SDK 版本改为本服务版本。

### 14.1 现状（2026-09-28 实测，代码事实）

`curl POST /mcp initialize`（同一 token）实测原文：

```json
{"result": {"protocolVersion": "2025-06-18",
  "capabilities": {"experimental": {}, "prompts": {"listChanged": false},
                   "resources": {"subscribe": false, "listChanged": false},
                   "tools": {"listChanged": false}},
  "serverInfo": {"name": "lvdata", "version": "1.29.1"}}}
```

| 项 | 事实 |
|----|------|
| `instructions` | **不存在**（FastMCP 构造未传，`server.py:40` `FastMCP("lvdata", stateless_http=True, json_response=True)`）——agent 对"这是什么/怎么用"的理解完全靠 5 条 tool description 拼出来 |
| `serverInfo.version` | **`1.29.1` = mcp SDK 版本**（FastMCP 默认）；本服务真实版本在 `__init__.py` 的 `VERSION`（2026-10-17 起改为 `importlib.metadata.version("lvdata-mcp")`，见 `docs/version_management.md` §10） |
| 版本号现有三处 | `pyproject.toml:7`（0.1.0）、`__init__.py:3`（VERSION，**已被 importlib.metadata 检为 0.1.0**）、`store.py:312`（字面量 `"0.1.0"`，**未用 VERSION** → 漂移风险） |
| `capabilities` 的多余声明 | `prompts`/`resources` 无实现（实测 `prompts: 0 条`、`resources: 0 条`）——SDK 的 `get_capabilities()` 无条件带上，FastMCP 无开关 |
| agent 可见的语义面 | 仅 `serverInfo.name`（`lvdata`）+ 5 条 tool description + `annotations.readOnlyHint` |

### 14.2 目标

1. **`instructions`**（2026-10-28 起按 D9 统一英文）——一次性进入每个连上的 agent
   上下文（常驻）。**当前逐字如下，措辞即契约（改文案 = 改 agent 行为，需评审）**：

```text
LivaChat's recorded multimodal lifelog data. Read-only.
Locate with get_recent_events / get_timeline; search with search (fast, preferred) / semantic_search (alternative wording); fetch details with get_event by event_ref.
Conventions: start_date/end_date are YYYY-MM-DD inclusive; timezone is the timezone of the current query; list/search results are lightweight projections; fetch details and keyframe images with get_event by event_ref; failures return error{code,message} (do not rely only on isError).
```

   历史中文版本（§14/§16/§17）已被 D9 取代；工具描述里逐字重复的共同约定收拢进
   instructions，描述只留各工具特有行为（净账见 §16.2）。
2. **`version`**：FastMCP 传 `VERSION`（`lvdata_mcp.__init__`），`store.get_status` 的字面量改用同一
   常量——三处版本号收敛为**一处**（`__init__.py`），并加一条与 `pyproject`/安装元数据的一致性测试。

### 14.3 决策（2026-09-28：D1–D5 全按推荐拍板）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 版本号单一出处 | **`lvdata_mcp.VERSION`**（已存在、无新常量）：`server.py` 传 `version=VERSION`、`store.get_status` 的字面量改用它——三处版本号收敛为一处。备选 `importlib.metadata` 仅用于测试侧一致性校验（2026-10-17 更新：改为 `VERSION = importlib.metadata.version("lvdata-mcp")`，pyproject 为唯一源，见 `docs/version_management.md` §10） |
| D2 | `instructions` 语言与文案 | **英文 only**（2026-10-28 D9 修订，取代原中文 only 口径；见 §14.2 当前文案）；错误/hint/工具描述同步英文 |
| D3 | "版本一致性测试" | **补**：`VERSION == pyproject.toml [project].version == importlib.metadata.version("lvdata-mcp")`（安装是前提，无条件断言；2026-10-17 起不再 skip，并增加 pyproject 对照以捕获未重装的 editable）——本节的动因就是"版本号三处"，漂移必须被测试钉住 |
| D4 | 空的 `prompts`/`resources` 声明 | **不修**：SDK `get_capabilities()` 无条件带上、FastMCP 无开关，改需 monkeypatch low-level Server，收益仅是"客户端少看到两个空节" |
| D5 | instructions 加隐私/用途约束 | **不加**：agent 无转发链路，写多了挤占常驻上下文 |

### 14.4 影响面（已执行，见 §7 末条）

| 类别 | 项 |
|------|-----|
| 代码 | `server.py`：`FastMCP("lvdata", instructions=..., version=VERSION, stateless_http=True, json_response=True)`；`store.py:312` 的 `"version": "0.1.0"` → `VERSION`；`__init__.py` 视 D1 结论决定是否补注释说明"单一出处" |
| 测试 | 新增：`urllib` 直发 `initialize`（照 `test_tools.py` 现有 urllib 先例）断言 `serverInfo.name == "lvdata"`、`serverInfo.version == VERSION`（**不是 SDK 版本**）、`instructions` 非空且包含关键短语（"只读"、`get_event`、`timestamp`/`timezone`）；`VERSION`、`pyproject` 声明版本与安装元数据三者一致（D3，安装是前提、无条件断言）；`/api/status` 的 `service.version` == `VERSION` |
| 文档 | README「Agent 客户端配置」节补一句：agent 连上会收到 `instructions`（摘要 + 指向本文档）；v2 §12 更新块加条目；本文档 §7 实施记录。**措辞本身即契约**——instructions 最终文案进本文档留档，改文案 = 改 agent 行为，需评审 |
| 风险 | instructions 是**常驻**上下文，长度失控会挤占每个 agent 的预算 → 文案已定 178 字，**改动需评审**；`version` 改变对客户端无破坏性（纯展示字段） |

### 14.5 验收

1. `initialize` 实测：`serverInfo.version` == `0.1.0`（不再是 `1.29.1`）、`instructions` 非空且与
   本文档留档文案逐字一致；
2. `/api/status` 的 `service.version` 与 `initialize` 的 `serverInfo.version` **同值**，且均来自
   `lvdata_mcp.VERSION`（单一出处）；
3. `tools/lvdata-mcp/tests` 全量 pytest 全绿（含新增的 initialize 断言与版本一致性用例）；
4. 文档同步（README / v2 §12 / §7）。

## 15. 变更计划：`semantic_search` 相关度可见 + 下限（已实施 2026-09-28）

**动因**（agent 视角评审，2026-09-28）：`semantic_search` 结构上"只要索引非空就必给 `limit` 条"，
命中里的 `score` 是 **RRF 名次分**（≈0.016x），与相关性无关 → agent 无法区分"真命中"与"兜底最近邻"。
实测（真实 server）：`zzzqqq vvvww`、`量子纠缠与超导磁悬浮` 这类查询 `search` 返回 0 条，
`semantic_search` 仍返回 **3 条**（`via=["vec"]`，score 0.0164/0.0161/0.0159）。

### 15.1 现状（代码事实）

| 项 | 事实 |
|----|------|
| 腿级原生分早就存在 | `VecIndex.search` 已算 `score = round(1 - distance, 4)`（**余弦相似度**，`vec.py:357`）；`index.search` 已算 `score = -bm25`（`index.py` 末尾） |
| 融合时被丢弃 | `rrf_merge`（`vec.py:364-`）为每条命中新建 dict，只保留 `event_ref/session_id/type/ts_ms/via/snippet/text`，`score` 重置为 RRF 累加值——**两个腿的原生分都没进输出**（`tools.py:174` 调用点） |
| 无过滤 | vec 腿按 `k = limit*3` 直接取 top-k，**无相似度下限**；FTS 腿同理无阈值 |
| 结果 | agent 只能靠 `via` 猜（含 `fts` 的更可信），无法量化"有多像" |

### 15.2 标定证据（2026-09-28，dev 库只读副本 + 真实 provider `qwen3.7-text-embedding`）

15 个查询、各取 top-1 余弦相似度（即 `1 - distance`）：

| 查询分组 | n | top1 相似度区间 |
|---------|---|----------------|
| **无对应**（`zzzqqq vvvww` / 量子纠缠与超导磁悬浮 / 宠物狗美容 / 深海潜水装备） | 4 | 0.297 – **0.455** |
| **语义专属**（FTS 0 命中、向量路是其唯一价值："小动物想要天上的东西" / 睡前故事 / 手机卡顿抱怨 / 会议吵架 / 银行理财广告） | 5 | **0.527** – 0.713 |
| **有字面对应**（FTS 命中："狐狸/股票行情/孙悟空/白骨精/杯子/蚂蚁"） | 6 | **0.601** – 0.979 |

→ **可分**：阈值落在 **(0.455, 0.527]** 即可做到"真实查询全保留、无对应查询全拒绝"；取
**τ = 0.50**（低侧余量 +0.045、高侧 −0.027，**余量紧**）。样本小（15 查询 / 1 库 / 1 模型），
换 embedding 模型或数据分布会漂移 → 阈值必须**可配置、可复标**（标定脚本与上表进文档，
复标方法即本节口径）。

### 15.3 方案

1. **暴露腿级原生分**（无行为变化）：命中新增
   - `vec_score`：余弦相似度（4 位小数），**仅向量路命中时出现**；
   - `fts_score`：`-bm25`（越大越相关，无界），**仅 FTS 路命中时出现**。
   `score` 保持 RRF 排序分不变。
2. **阈值只走配置，不加 `min_score` 入参**（2026-09-28 review 定）：
   - `providers.embedding.min_vec_score`，**默认 0.50**（§15.2 标定值），`0` = 关闭过滤；
   - 只作用于**向量路**命中（`vec_score < τ` 丢弃）；**FTS 命中不受影响**——bm25 无界不适合作阈值，
     且字面命中天然相关；
   - 不加参数的理由：**τ 是模型标定值、不是每次查询的意图**；agent 看不到模型相似度尺度、无从选值
     （易一刀切死或形同没加）；运维/标定者改 yaml 即可换模型复标；且**保持 §11 的入参对称**
     （`semantic_search` 与 `search` 的差异仍只有 `with_context`）。
3. **过滤位置与"只减不增"**：在 `rrf_merge` 之前过滤 `vec_hits`，但**保留腿内原始名次**参与 RRF
   （`1/(k+rank+1)` 仍用原始 rank，不为幸存者重排）——融合分值语义不漂。过滤**只做减法**：
   幸存数少于 `limit` 时就少返回，**不得为凑满 `limit` 回填低相关命中**（那等于没过滤；
   向量路 over-fetch 仍是 `limit*3`；若实测"命中数经常远小于 limit"，再议提高 over-fetch——可复议）。
4. **响应侧可解释信号 `filtered`**（补"`hits: []` 分不清是没数据还是被阈值吃掉"这个缺口）：
   仅当**有向量命中被丢弃**时出现，**两个字段**（2026-09-28 review 定）：

```jsonc
"filtered": {"min_score": 0.5, "best_dropped": 0.47}
```
   - `best_dropped` = 被丢弃命中里的最高相似度——告诉 agent"你差 0.03，改个说法再试"，而不是
     让它以为库里没有；**它的出现本身就是"过滤发生了"的信号**（恒有 `best_dropped < min_score`）；
   - `min_score` = 本次生效阈值（自描述，配置改过也能对照）；
   - **不报丢弃/候选条数**：agent 看 `len(hits)` 即知实际命中数，丢弃计数无行动价值（取舍已定）。
5. **描述同步**（**2026-09-28 用户要求再精简**）：曾写明"`vec_score` = 余弦相似度；低于配置阈值
   的向量命中不返回，此时响应含 `filtered`"；现**只保留 `vec_score`/`fts_score` 两个字段的存在**，
   阈值/`filtered`/`snippet`·`text` 语义与缓存说明从描述移除——这些行为**未变**，语义以本仓库文档
   （本节与 §16.2）为准。

**接口变更性质**：纯加法（命中 +`vec_score`/`fts_score`，顶层**条件性** +`filtered`；入参不变）；
**行为收窄**：默认阈值下向量路的低相关命中不再返回——这正是本变更的目的，且可解释（`filtered`）、
可运维关闭（yaml 设 0）。

### 15.4 决策（2026-09-28：D1–D6 全按推荐拍板）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 阈值来源与默认值 | **只走配置** `providers.embedding.min_vec_score`，默认 **0.50**、`0` = 关闭；**不加 `min_score` 入参**（理由见 §15.3 第 2 条）。⚠️ 实现陷阱：`ProviderConfig.resolved()` 是**白名单构造**（`config.py:55-68` 自带"新增字段必须显式透传，否则静默丢"的教训注释）——新字段必须在 `resolved()` 里透传 |
| D2 | ~~阈值是否进 yaml~~ | **并入 D1**（阈值即配置） |
| D3 | 暴露 `fts_score` | **暴露**（与 `vec_score` 对称，agent 可判断"字面命中有多强"） |
| D4 | instructions 提阈值 | **不提**（§14.2 文案已定 178 字，改动需评审；阈值信息在工具描述里已够） |
| D5 | `main.py eval` 复用阈值 | **不复用**：eval 目的是量 recall，过滤会污染指标（保持直调 `provider.embed` + 无阈值的现状） |
| D6 | `filtered` 字段集 | **已定（2026-09-28）：`{min_score, best_dropped}` 两字段**——`best_dropped` 的出现即"有丢弃"的信号，实际命中数看 `len(hits)`，不报丢弃/候选计数（无行动价值）；"累计丢弃数进 `/api/status`"作为运营侧观测仍列为可复议 |

**否决/搁置**：`min_score` 入参（原 D1 备选）——阈值是模型标定值而非每查询意图，agent 无从选值，
且会打破 §11 的入参对称；硬编码且不可配置的阈值（换模型即错，且静默改变召回）；只加阈值不暴露
`vec_score`（agent 仍无法自查"为什么被滤"）；改 RRF 为分数归一化融合（排序是既有设计，本变更
只解决"可解释性"，不动排序）。

### 15.5 影响面（已执行，见 §7 末条）

| 类别 | 项 |
|------|-----|
| 代码 | `vec.py`：`VecIndex.search` 的 `score`（相似度）由 `rrf_merge` 承接为 `vec_score`（leg 内部字段名不动，避免影响 `main.py eval` 复用）；`rrf_merge` 为命中补 `vec_score`/`fts_score`；`tools.py`：`semantic_search` 从 `store.settings.providers.embedding.min_vec_score`（**既有 settings 链路，无新管道**）取 τ → 在 merge 前过滤 `vec_hits`（保留原始名次）、组装顶层条件性 `filtered`、更新描述；`config.py`：`ProviderConfig.min_vec_score: float = 0.50`（与既有 `dim` 同款"仅 embedding 使用"字段）+ **`resolved()` 显式透传**；`schemas.py` **不动**（无新入参）。⚠️ 字段位置说明：`ProviderConfig` 由 audio/vision/summary/embedding 共用、`extra="forbid"`——加在它上面是既有风格（`dim` 即先例），实施时**勿新建 retrieval 配置块**（多一处散落点）。**生效值可见性**（实施时核实并补）：启动日志的 providers 段是**手工白名单**（`config.py:297-317` `effective_summary`），原本不含 `dim`/`min_vec_score`——已为 embedding 补上这两个字段（见本节「观测」①） |
| 测试 | ① 无对应查询在默认阈值下 `hits == []` 且带 `filtered`（恰两键 `{min_score, best_dropped}`、`best_dropped < min_score`）——fake provider 下**用样本语料里不存在的字符**（如 "股票"：字符 one-hot 与所有 summary 无交集 → 余弦 0）构造低于 τ 的向量；② 语义专属查询仍返回命中（≥τ 保留）；③ **yaml 设 `min_vec_score: 0` → 命中集合与排序与现状一致**（回归护栏；字段集按设计多出 `vec_score`/`fts_score`，非逐字相同——见验收 2）；④ 命中带 `vec_score`/`fts_score`（仅对应腿出现）；⑤ FTS 命中不受阈值影响（构造 vec 相似度 < τ 但 FTS 命中的用例）；⑥ 未丢弃时不出现 `filtered`；⑦ 过滤只减不增：故意把 limit 设大 + 大量低相似度候选 → 返回数 < limit 且**不回填**；⑧ 现有 270 例全绿 |
| 文档 | README（`semantic_search` 行 + 工具表 + 配置表加 `min_vec_score`）；v2 §12 更新块；本文档 §7 记录 + **§15.2 标定表与复标方法留档**（换模型时重跑同一实验、更新 τ） |
| 观测 | ①（**已实施，2026-09-28**）embedding-only 生效参数进启动摘要：`effective_summary` 的 providers 段为 embedding 补 `dim`/`min_vec_score`（其余能力不出现）——复标 τ 后可直接从启动日志核对生效值；测试 `test_config.py::test_effective_summary_exposes_embedding_retrieval_params` 钉住。**同日另删**：该摘要里 `base_url`/`api_key`/`workspace_id` 一并移除（用户决定；日志会被采集，端点/账号标识/凭据前后缀不外泄）——provider 段只剩 `vendor`/`model`（+embedding 两项），护栏 `test_effective_summary_omits_secrets_and_endpoints`；历史决策反转见 `docs/lvdata_workspace_extend.md` §3.1c 注。<br>②（可复议）`/api/status` 加"被阈值丢弃的向量命中累计数"——判断 τ 是否过严 |

### 15.6 验收

1. 默认阈值下，明显无对应的查询返回 `hits: []`（不再"永远 limit 条"）**且带 `filtered`**
   （恰 `{min_score, best_dropped}`，且 `best_dropped < min_score`）；
   有字面对应或相似度 ≥ τ 的语义命中照常返回；
2. yaml 设 `providers.embedding.min_vec_score: 0` 时，**命中集合与排序**与 §15 之前一致
   （回归护栏），且不出现 `filtered`——⚠️ 字段集**不**逐字相同：命中按设计多出
   `vec_score`/`fts_score`（这是本变更的加法部分，与 τ 无关）；
3. 命中含 `vec_score`（向量路）与 `fts_score`（FTS 路），且 `vec_score` ≥ τ（过滤开启时）；
4. **过滤只减不增**：幸存数 < `limit` 时按实返回，不为凑数回填低相关命中；
5. **入参不变**（`semantic_search` 与 `search` 的差异仍只有 `with_context`——§11 对称保持）；
6. 换 embedding 模型时，按 §15.2 方法可复标并只改 yaml（不改代码）；
7. `tools/lvdata-mcp/tests` 全量 pytest 全绿；真机复测 `zzzqqq vvvww` → 0 条 + `filtered`、
   "小动物想要天上的东西" → 仍有命中。

## 16. 变更计划：工具描述的共同块上移 `instructions`（已实施 2026-09-28）

**目标**：5 个工具描述里**逐字重复**的约定只写一处（`instructions`），描述只留各工具特有行为；
净上下文下降、口径单点可改（改一次全体一致）。**这会修订 §14.2 已留档的 instructions 文案**
（§14.4 写明"改文案须评审"——本方案即该评审）。

### 16.1 现状与重复量（2026-09-28 实测）

| 工具 | 描述字数 | 其中"共同块" |
|------|---------|-------------|
| `search` | 203 | 日期窗含端点 / timezone 回落 / timestamp 含义 |
| `semantic_search` | 563 | 同上（+ "入参与 search 同构"）+ 轻量投影 |
| `get_timeline` | 244 | 同上 + 轻量投影 + "详情按 event_ref 调 get_event" |
| `get_event` | 203 | timezone 回落 / timestamp 含义 |
| `get_recent_events` | 203 | timezone 回落 + 轻量投影 + 详情链 |
| **合计** | **1416** | **共同块约 480–520 字（≈35%）** |

共同块三类：**① 日期窗格式**（`YYYY-MM-DD` 含端点）· **② 时区与时间口径**（`timezone` 缺省/无效 =
服务配置；`timestamp` 可读、含义以同返 `timezone` 为准）· **③ 轻量投影 + 详情链**（列表/检索不含
payload、summary 截断；细节与关键帧图按 `event_ref` 调 `get_event`）。另有一句**所有工具都适用
却任何地方都没写**的约定：**失败时错误在返回体 `error` 字段**（`isError=false`；agent 视角评审
的问题 3）。

### 16.2 目标文案

**新 `instructions`（用户定稿，2026-09-28，261 字）**：

```
LivaChat 录制的多模态生活数据，只读。定位用 get_recent_events / get_timeline，检索用 search（快，优先）/ semantic_search（换词可召回），详情用 get_event 取（按 event_ref）。通用约定：日期窗 start_date/end_date 为 YYYY-MM-DD 含端点；列表与检索返回轻量投影，详情与关键帧图按 event_ref 调 get_event；失败时返回体带 error{code,message}（不要只看 isError）。
```

**D6 已定：a** —— 该定稿补入"时间口径"一句（否则按本表精简后该约定会从 agent 可见面消失，
而 `timestamp` 无时区口径是经典误读源）。**最终 instructions（319 字，逐字留档于 §14.2 / 测试锁定）**：

```
LivaChat 录制的多模态生活数据，只读。定位用 get_recent_events / get_timeline，检索用 search（快，优先）/ semantic_search（换词可召回），详情用 get_event 取（按 event_ref）。通用约定：日期窗 start_date/end_date 为 YYYY-MM-DD 含端点；时间为本地可读 timestamp（含义见同返 timezone，timezone 缺省/无效 = 服务配置时区）；列表与检索返回轻量投影，详情与关键帧图按 event_ref 调 get_event；失败时返回体带 error{code,message}（不要只看 isError）。
```

选项对照（选 a 的理由）：

| 选项 | 做法 | 合计字数 |
|------|------|---------|
| **a（推荐）** | 在定稿的"列表与检索返回轻量投影"**前插一句**：「时间为本地可读 timestamp（含义见同返 timezone，timezone 缺省/无效 = 服务配置时区）；」（+58 字，instructions → 319） | **991** |
| b | 不进 instructions，改为 5 条描述各自保留该句（~44 字/条） | 1213 |
| c | 两边都不写（接受该约定只在文档里） | 933 |

方案 a 与 b 都保住信息，a 比 b 省 ~220 字（单份 vs 五份）——重复量本该只写一处，a 与 §16 的目标自洽。

**精简后的工具描述**（只留特有行为，逐字待评审）：

| 工具 | 改后描述 | Δ |
|------|---------|---|
| `search` | 跨维度全文检索：chat 文本 / 音频转写 / 画面 caption+OCR / agent job。可按日期窗与 type 收窄，命中带 event_ref。 | −122 |
| `semantic_search` | 语义检索（RAG v2）：FTS + 向量双路召回，RRF 融合，命中带 via。日期窗与 type 同时作用于两路；命中带 `vec_score`（余弦相似度）/`fts_score`（-bm25）；相似度低于配置阈值（默认 0.50）的向量命中不返回，此时响应带 `filtered{min_score, best_dropped}`。`snippet` 为 FTS 高亮片段（纯向量命中为空串）。`with_context=true` 附 ±2min 邻域；未配 embedding 返回 `embedding_disabled`、调用失败返回 `embedding_failed`；FTS 索引不可用时仅返回向量路。相同 query 命中进程内缓存。 | −248 |
| `get_timeline` | 统一多模态时间线（events 表查询视图）：按日分组（日升序、日内时间升序）；游标为 `[ts_ms, type, event_id]` 三元组 keyset，分页可能把同一天切成两页。 | −152 |
| `get_event` | 单个事件完整详情（`event_ref` 形如 `{session_id}:{type}:{source_id}`）；video 事件且关键帧存在时，返回值附关键帧图片（image content）。 | −107 |
| `get_recent_events` | 近 N 个本地日的事件清单（days=1 即今天，上限 31 天）：按日分组、日倒序、日内时间正序；`truncated=true` 表示被页大小截断（total 为窗口内总数）。 | −115 |

**净账**（D6-a；**2026-09-28 实施后按实测回填**；`semantic_search` 同日按用户要求再精简
404 → 240）：描述 1416 → **617**（逐工具实测：
`search` 99 / `semantic_search` 240 / `get_timeline` 92 / `get_event` 96 / `get_recent_events` 90）；
`instructions` 178 → **319**（§17 后 326；**用户第 4 版定稿 280**）；**合计 1594 → 1100 → 1107 → 1061 → 现 **897** 字（−44%）**。
比草案表（672/991）多出的 **109 字**来自实施时补齐的两类内容（不是漂移）：**D2 要求的
"详情按 event_ref 调 get_event"指针句**（`search`/`semantic_search` 各一句）与
**`filtered` 的用法解释**（"best_dropped 是被丢弃里的最高分…"——草案表把它压成字段名，
agent 拿到会不知道拿它干什么）。护栏：`test_tool_descriptions_delegate_conventions`
断言描述合计 < 860（超了即说明又抄回共同约定或塞了新长文）。

### 16.3 决策（2026-09-28：D1–D6 已定，全按推荐）

| # | 决策 | 备选与倾向 |
|---|------|-----------|
| D1 | 是否把"错误在返回体 `error`（`isError=false`）"写进 `instructions` | **已定（写）**：用户定稿已含「失败时返回体带 error{code,message}（不要只看 isError）」——关闭 agent 评审的问题 3 |
| D2 | 各工具是否保留一句"详情按 `event_ref` 调 `get_event`" | **已定（保留）**：`search`/`semantic_search` 各一句——MCP 的 `instructions` 是**可选字段**，客户端可能不注入，关键链路不能只靠它 |
| D3 | `instructions` 文案定稿 | **已定（2026-09-28 用户逐字稿，261 字，见 §16.2）**；定稿替换 §14.2 的 C 版留档并注明"§16 修订" |
| D6 | **时间口径放哪**（review 发现：用户定稿未含该约定） | **已定：a**——补进 instructions（+58 字 → **319**，合计 991）。⚠️ 与 **§17**（17.2 已定采 b）相互作用：入参回落那句两种格式下都必需；出参解释那句在 §17 后可简化为「时间为带偏移的 ISO 8601；timezone 缺省/无效 = 服务配置时区；」（41 字，instructions → 302）。本变更先落地用 58 字全句（§17 后仅多 17 字、不返工） |
| D4 | 描述里的具体数值（"默认 0.50"） | **已定（保留 + 注明可配置）**——agent 需要量级感 |
| D5 | 是否加"防重新重复"的测试 | **已定（加）**：断言 5 个工具描述里不再出现"含端点"/"服务配置时区"/"轻量投影"等已上移字样（只允许出现在 `instructions`） |

### 16.4 影响面（已执行，见 §7 末条）

| 类别 | 项 |
|------|-----|
| 代码 | `server.py`：`INSTRUCTIONS` 文案替换（逐字）；`tools.py`：5 处 `description=` 精简（**逻辑一行不动**） |
| 测试 | `test_tools.py` 的 `INSTRUCTIONS` 字面量副本同步（现有护栏会强制同步）；D5 的防重复断言（新）；其余 278 例须全绿（描述文本除 `test_semantic_search_*` 未断言描述内容外，`test_initialize_metadata` 会锁定 instructions 逐字） |
| 文档 | §14.2 留档文案加注"§16 修订（2026-09-28）"并给出新文案；README 工具表保持摘要口径（不逐字抄描述）；v2 §12 更新块；本文档 §7 记录 |
| 风险 | ①**客户端忽略 `instructions`**（它是可选字段）→ 缓解 = D2 保留关键指针句 + 本方案不动任何工具**行为**（仅文本），最坏情况是 agent 少知道几条约定，不会误用参数；②`instructions` 变长（+105 字）是**每会话常驻**成本，与描述合计 −744 相抵后仍净降 639 字 |

### 16.5 验收

1. `tools/list` 实测：5 个工具描述为 §16.2 表的文案（逐字），且**不含**"含端点 / 服务配置时区 /
   轻量投影 / 按 event_ref 调 get_event（仅允许保留在 search 与 semantic_search 各一句）"；
2. `initialize` 的 `instructions` 逐字等于 §16.2 定稿文案（按 D6 结论决定是否含时间口径那句；测试锁定）；
3. 常驻+按需合计字数 = **897 ±10**（描述 617 / instructions 280，脚本可复算；
   与草案表 991 的差额来源见 §16.2 净账）；
4. 工具入参/返回/行为**零变化**（同一批工具级用例全绿，测试数不减）；
5. `tools/lvdata-mcp/tests` 全量 pytest 全绿。

## 17. 变更登记：`timestamp` 改 ISO 8601（带偏移）＋ `timezone` 由实际结果计算（已实施 2026-09-28）

**需求原文**（用户）：

> timestamp 使用 iso 的格式: 例如 `2026-09-28T18:01:30+08:00`；timezone 根据实际结果返回的值计算。

### 17.1 现状与影响点

渲染是**单点**：`timeutil.ms_to_local(ms, tz)`（`strftime("%Y-%m-%d %H:%M:%S")`）——改它即
5 个工具的**全部** `timestamp` 一起变（`search`/`semantic_search` 命中、`get_timeline`/
`get_recent_events` 的 `days[].events[]`、`context_events`、`get_event`）。样例（实测同一毫秒）：

| | 渲染 |
|---|---|
| 现在 | `2026-09-27 13:53:49` |
| §17 后 | `2026-09-27T13:53:49+08:00` |

**不受影响**：`date_from`/`date_to`（仍是 `YYYY-MM-DD`）、按日分组的 `days[].date`、
`get_event` 的 `related`、`filtered`、排序（`days[].events` 与 `context_events` 都按 `ts_ms`
从 SQL 出来，不依赖字符串序）。

### 17.2 `timezone` 字段语义（**已定 2026-09-28：采 b**）

| # | 读法 | 结论 |
|---|------|------|
| a | `timezone` 字段 = 实际渲染用的偏移（`"+08:00"`，不再有 zone 名） | **否决**：丢失 zone 名后"本地日"边界不可重建（`get_recent_events` 的日界由 zone 规则决定，DST 日 23/25 小时），且与 §8.7 的"真实生效标识"口径倒退 |
| **b** | **保持真实生效 zone 名（现口径 §8.7）**；偏移按**各事件时刻**的 zone 规则算进 ISO | **采用**：信息不丢、改动最小；`timestamp` 自带偏移后已自描述，字段继续承担"日界/时区名"的解释职责。注意 DST 场景下**同一响应内的偏移可能不止一种**（实测 `-05:00` / `-04:00`），这属正确行为 |
| c | `timezone` 字段 = 实际出现过的偏移集合（数组） | **否决**：字段类型复杂化，agent 解析成本上升，且同样丢 zone 名 |

### 17.3 实施时要注意（防坑清单）

- **DST 混合偏移**：同一响应里可能出现 `-05:00` 与 `-04:00` 两种偏移（见上表实测）；**字符串字典序
  不再等于时间序**——需在描述里提醒 agent 按 `ts_ms`…（`ts_ms` 已不对外）→ 改为"若要自己排序，
  请解析 ISO 而非字符串比较"；或直接在描述里说明"时间已按时间序返回"。
  **已实施后又被用户改口径（2026-09-28，第 4 版 instructions）**：时间口径句整体替换为
  「timezone是当前查询所在的时区；」——**ISO 格式说明与 DST 排序提醒随之移出 agent 可见面**
  （格式仍自描述：时间戳自带偏移；排序提醒在本部署下无实害，仅当客户端主动请求 DST 时区才相关）。
  若日后要恢复，可放进 `instructions` 或某条工具描述（本地保留为可复议项）。
- **格式声明**（已实施）：instructions 的时间口径句改 ISO 版（见 §14.2 现文案）；
  **入参**那句（`timezone` 缺省/无效 = 服务配置时区）按 17.2-b 保留（入参语义 ISO 覆盖不到）；
  `instructions` 319 → **326** 字。
- **测试与文档**（已实施）：断言里唯一硬编码字面量（`test_tools.py` 的 `"… 12:00:02"`）改
  `"…T12:00:02+08:00"`，其余断言走 `ms_to_local(...)` 自动跟随；新增
  `test_timestamp_iso_format`（正则护栏）与 `test_dst_offsets_follow_zone_rules`（美东换季双偏移）。
  README/v2 的示例时间戳随下批文档同步（见 §7 记录）。
- **`ms_to_local_or_none`（D6）语义不变**：`ts_ms=0` 仍 → `null`（ISO 也一样）。

### 17.4 验收（已实施，2026-09-28）

1. 5 个工具的 `timestamp` 均为带偏移 ISO 8601（正则校验），且与 `timezone` 字段语义自洽
   （按所选 17.2 读法）；
2. `ts_ms=0` → `null` 不变；`date_from`/`date_to`/`days[].date` 不变；
3. DST 时区（如 `America/New_York`）下同一响应内的偏移按各事件时刻正确（跨换季用例）；
4. 全量 pytest 全绿；真机复测 `search`/`semantic_search`/`get_timeline` 各一条时间字段。


## 18. 性能复审：5 个 MCP 工具的内部实现（2026-09-28，**无严重问题、无待办**）

> 方法：合成库（200k docs FTS / 200k events）+ 真机（1088 docs，服务端自记 `latency_ms`）实测；
> 只读、未改任何代码。结论先行：**没有严重性能问题**；3 处"随规模增长"的点中 2 处已在本文档
> 登记并有方案，唯一候选动作是 10 万 chunk 之后评估向量 ANN。

### 18.1 逐工具成本结构

| 工具 | 关键路径 | 复杂度 | 实测 |
|------|---------|--------|------|
| `search` | FTS5 `MATCH` + `ORDER BY bm25(...)` + `LIMIT` | **∝ MATCH 命中面**（非库大小） | 200k docs：命中 2% → **0.1 ms**；命中 100% → **117 ms**；真机（1088 docs）服务端 p50 **4.1 ms** / max 6.1 |
| `semantic_search` | embedding（外部）+ vec KNN + FTS + RRF | embedding 常数（~93 ms，§13 起有缓存）；**vec KNN ∝ chunk 数（暴力扫描）** | 拆解（§13.1）：embed 93 + 本地 ~7 ms（KNN 5.6 / FTS 0.8 / RRF）；KNN 规模：1k=2.3 / 5k=11.2 / **20k=43.9 ms** |
| `get_timeline` | `ORDER BY (ts_ms, PRIORITY_SQL, event_id)` | 带日期窗 → 索引（§A 优化）；**无窗 → TEMP B-TREE 整段排序** | 200k 行：带窗 **0.1 ms** / 无窗 **113 ms**（A 方案有意只对带窗加提示）；真机无窗 limit=200 → 服务端 **9.6 ms** |
| `get_recent_events` | `ts_ms BETWEEN` + `ORDER BY ts_ms DESC, event_id DESC` | **索引完美匹配**（`event_id` = rowid，逆扫 `idx_events_ts` 恰为所需序，无 temp b-tree） | 200k 行、days=31（窗口 4.5 万行）**0.3 ms**；真机 days=7 → 服务端 **5.6 ms** |
| `get_event` | UNIQUE 索引单行 + payload 解析（+ 可选读图，§I-1 已单次 stat/read） | O(1) | 真机 **3.2 ms** |

### 18.2 三处随规模增长的点（按严重度）

1. **`semantic_search` 的向量 KNN 是暴力扫描（唯一会真正恶化的点）**：`sqlite-vec vec0` 无 ANN，
   耗时 ∝ chunk 数（20k → 44 ms），**外推 10 万 chunk ≈ 200 ms**，届时与 embedding 往返同量级。
   缓解路径：ANN 索引 / 按会话或时间 `partition key` 分片——**新立项，未做**（触发线：chunk 数达
   数万级或 `semantic_search` 本地段 p50 超 ~50 ms）。
2. **`search` 的排序成本 ∝ MATCH 命中面（FTS5 语义，非缺陷）**：`ORDER BY bm25(...)` 必须给全部
   命中打分后排序，"常见词 × 大索引"最坏 ~117 ms @200k docs。**注意：加日期窗不降低这部分**
   （实测窄窗 126.8 ms ≈ 无窗 117 ms）——`search_docs.ts_ms` 是 FTS5 `UNINDEXED` 列，过滤发生在
   MATCH 之后；§10 的日期窗解决的是正确性、不是性能。要压需把 `ts_ms` 升级为可过滤列（改索引
   schema + 重建）——**不建议现在做**。
3. **`get_timeline` 无日期窗的整段排序**：已在 [get_timeline_optimize.md](../get_timeline_optimize.md)
   §2.2 登记（200k 行 113 ms）；A 方案有意只对"带窗"加索引提示（无窗加提示会牺牲稀有类型查询：
   `agent_job` 0.08 → 20.9 ms）。属"已知、已定、可复议"。

### 18.3 可疑但实测**不是**问题

| 项 | 实测 | 判定 |
|----|------|------|
| `expand_context` 的 N+1（`with_context` 每命中一次 connect+查询，≤10 次） | 10 次 ≈ **3.5 ms**（单次 0.4 ms） | 无需优化 |
| 每次工具调用新开 SQLite 连接（读路径统一 `DataStore.connect()`） | 0.4 ms/次 | 无需连接池 |
| `semantic_search` 每路 over-fetch `limit*3`（≤300 条） | 与主路径同量级 | 无 |
| `get_recent_events` 的 COUNT + 页查询（两次查询） | covering index，**0.0 ms** @200k | 索引完美 |
| `search_docs.orig_text` 全文副存（磁盘 ≈ 摘要再存一份） | 磁盘成本、非延迟 | 可接受（snippet 需要原文） |
| 访问日志中间件读取每个请求 body（取 tool 名） | MCP 载荷小（仅工具参数） | 无 |

### 18.4 优先级建议（现在都不用做）

1. **10 万 chunk 之后**再评估 vec 的 ANN/分片（唯一会拖慢 `semantic_search` 的点）；
2. `search` 若在大索引上出现"常见词变慢"的体感，再评估把 `ts_ms` 从 `UNINDEXED` 升级为可过滤列；
3. `get_timeline` 无窗场景维持现状（A 方案取舍已记录于 `get_timeline_optimize.md` §2.3）。

### 18.5 复现口径

- 合成库：临时目录按 `ddl.py` 的 `EVENTS_SQL` 建表 200k 行（events）；按 `index.py` 的 `INDEX_SQL`
  建 FTS 表 200k 行（词命中面可调），3–5 次取 p50；
- 真机：`127.0.0.1:8090` 用 `search`（宽/窄命中词）、`get_timeline`（无窗 limit=200）、
  `get_recent_events(days=7)`、`get_event` 各调一次，取访问日志的 `latency_ms`；
- 关键 SQL/调用：`fts_search`（bm25 排序）、`store.get_recent_events(days=31)`、
  `store.get_timeline(...)`、`VecIndex.search(k=30)`。
