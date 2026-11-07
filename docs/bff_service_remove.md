# BFF remove events：删除 video 事件接口（分析与设计）

> 状态：**已实施（2026-10-09，见 §9）**：S1–S4 完成、测试全绿；S5 沙箱核对通过
> （真实库零写入；真机实删待拍板）。评审记录：D7 = 逐条部分成功（用户指定），
> 其余按建议值。核实日期 2026-10-09；关键断言全部附 file:line（逐文件核实本 worktree）。
> 需求来源（用户，2026-10-09）：「增加新的接口 remove events：1) 目前只支持 remove
> video events；2) 删除 video events 时删除对应 video image，如果 video window 只有
> 这一个 video event 则删除 video window；3) 更新受影响的 FTS、向量索引，更新
> day_stats 相关统计；4) 以 codebase 为基础，不要猜测」。
> 范围：BFF 对外面 `POST /api/mylife/events/remove`（§2.1）+ 执行面
> `POST /api/events/remove`（§2.2，lvdata-mcp）；级联面 = events 行 / jpg + files 行 /
> 空 window 事件行 / `search_docs` / `vec_chunks`+`vec_state` / `day_summary.day_stats`。
> 客户端（Android / Web SPA）接入与 UI 不在本文范围。
> 关系：删除面 ↔ `docs/bff_service_interfaces.md`（§7 overview 只读契约）、
> `docs/data_clean.md`（video prune 先例）、`docs/video_analysis.md` §10.8（真实库删事件先例）。

## 0. 需求映射

| 需求 | 落点 |
|---|---|
| ① 只支持 video | §2.1：非 video 逐条 skip（reason=`not_video`）+ 记 log，不删不阻塞批内其余；决策 D8 |
| ② 删 video image | §3.3（unlink jpg + 删 files 行；先文件后行，与 prune 同序） |
| ② 空 window 删 window | §3.1-③（按 payload.window_id 跨 session 计数，归零则删 window 事件行） |
| ③ 更新 FTS / 向量 / day_stats | §3.1-④⑤（FTS 与 day_stats 在**同一主事务**内）、§3.2（向量） |
| ④ 以 codebase 为基础 | §1 现状盘点（全 file:line）；§4 架构决策逐条给出与现状的衔接 |

---

## 1. 现状盘点（codebase 事实）

### 1.1 video 数据模型

| 事实 | 位置 |
|---|---|
| `events` 表：`event_ref TEXT NOT NULL UNIQUE`；列 = device_uuid / rsession_id / type / ts_ms / ts_utc / source / summary / payload(JSON) / created_at / updated_at；无外键；索引 = (rsession_id, ts_ms) / ts / type / updated_at | `tools/lvdata-mcp/src/lvdata_mcp/ddl.py:20-39` |
| 写入事件类型共 7 种（`chat/transcribe/translate/agent_job` + `audio` + `window/video`） | `analysis/chatlog.py`、`analysis/audio.py:558-564`、`analysis/vision.py:448,577` |
| **video 事件**：`event_ref = f"{rsession_id}:video:{frame_id}"`；`frame_id = Path(file_path).stem`（uuid7）；type=`video`；ts_ms=files.uuid_ts_ms；summary=caption+place+ocr；payload = 归一的 extract 键（caption/place/objects/ocr/people）+ `frame_id` + `window_id` | `analysis/vision.py:556-587`（upsert 原文 :577-583） |
| **window 实体 = `type='window'` 的 events 行，无独立表/表结构**（全库无 window 表；关联键 = payload 的 `window_id`）。`event_ref = f"{rsid}:window:{window_id}"`；ts_ms = 窗口首帧时刻；payload = `window_id/window_start_ms/window_end_ms/frame_count/window_caption/key_frame_count`；**关键帧集为空则不产 window 行**（写入方保证「window 行存在 ⟺ 该窗至少一个关键帧」；DB 无约束） | `analysis/vision.py:445-456`；窗口定义 `:145-151` |
| 窗口分组**按 device、session 不参与**（跨 session 连续帧合并成同一窗口；window 行的 device/rsession = 首帧所属）→ 统计「该 window 还剩几条 video event」必须按 `payload.window_id` 跨 session 计数 | `analysis/vision.py:128-142`；docstring `:130-135` |
| jpg 落盘路径 = `<data_root>/<device_uuid>/<rsession_id>/video/<uuid7>.jpg`（录制侧写） | `services/recording/session.py:381-388`；`services/recording/service.py:187-190` |
| files 行登记：`file_type='video'`、`file_ext='jpg'`、`file_path`（绝对路径，UNIQUE）、`uuid_ts_ms`、`analysis_status`；files DDL = | `tools/lvdata-mcp/src/lvdata_mcp/recording_watch.py:43,65-71`；`ddl.py:211-224` + 增强列 ALTER `ddl.py:159-171` |
| 消费路径：BFF media 按 `device/session/video/<filename>` 解析（无 files 表校验）；MCP `get_event` 帧图 = `<root>/<device>/<rsession>/video/{frame_id}.jpg`（frame_id 取自 event_ref 后缀） | `services/bff/common/store.py:448-470`；`tools/lvdata-mcp/src/lvdata_mcp/store.py:279-281` |

### 1.2 既有清理机制（可复用的先例）

| 事实 | 位置 |
|---|---|
| **video prune**（每日随 `_prune_loop`）：候选 = `files.file_type='video' AND analysis_status='ok' AND uuid_ts_ms < cutoff` 且**无** `{rsid}:video:{stem}` 事件；逐候选在同一写连接复核（status 仍 ok ∧ 仍无事件）→ `unlink(missing_ok)` → `DELETE FROM files`（**先文件后行**，崩溃自愈：行在文件不在 → 下轮重删）。保留期 `max(24, video_retention_hours)`（配置默认 72、当前 dev yaml `:42` = 72；运行时下限 24） | `tools/lvdata-mcp/src/lvdata_mcp/watcher.py:341-410`（候选 :355-363，复核 :374-390，护栏 :391-396） |
| 审计清理 `_prune_once`：mcp_pipeline_runs / analysis_runs 按龄、index_state 孤儿水位；**不动 events/search_docs/vec/day_summary** | `watcher.py:305-337`；范围声明 `docs/audit_tables_retention_plan.md` |
| **真实库删事件先例**（filter 迁移清理 162 stale video + 58 stale window）：备份 JSON 后直删行 → `force reindex`（FTS docs 2712→2492）+ `force embed`（向量孤儿 220→0）；并记录口径：「FTS 增量重建不清理已删行、向量孤儿仅全量重建清扫、day_stats 对删除不可见——删改事件后的对账需 force 触发 reindex+embed+day_stats」 | `docs/video_analysis.md:583-587` |
| video prune 的既有边界：不触 events、不触 FTS/向量、不触 derived 审计 JSON、cs.recording_files 只读 | `docs/data_clean.md` §4.5（:119-126） |

### 1.3 FTS（search_docs）

| 事实 | 位置 |
|---|---|
| `search_docs` = FTS5 虚表（`text` 索引列 = events.summary 经 CJK bigram 预切；event_ref/session_id/device_uuid/type/ts_ms 为 UNINDEXED 元数据）；**无类型过滤**（video/window 行都在内） | `tools/lvdata-mcp/src/lvdata_mcp/index.py:16-24`、`:98-108` |
| 构建原语 = session 级 `rebuild_session`（`DELETE ... WHERE session_id=?` + 从 events 重建）；**无按 event_ref 的删除函数** | `index.py:88-114` |
| 增量调度 = 每轮 `reindex` stage，取件口径 `lag_sessions`：`events.max(updated_at) > 水位(index_state)` 的会话才重建 | `index.py:147-163`；stage 序列 `watcher.py:42` |
| **删除不可见**：删 events 行不 bump 任何 `updated_at` → 会话不再判滞后 → 陈旧 FTS 行永久残留（除非该会话后续有新事件触发整会话重建，或 force 全量） | `index.py:147-163`；实测口径 `docs/video_analysis.md:587` |
| 水位语义：`index_state.target`（`''`= 全局 / `rsession_id`），值 = 快照 −1ms；重建从 events 读 → 后续任何重建**不会复活**已删 ref | `index.py:117-144` |

### 1.4 向量索引

| 事实 | 位置 |
|---|---|
| 独立库 `<root>/lvdata_index.db`；`vec_chunks` = sqlite-vec `vec0` 虚表（chunk 粒度 = event，chunk_id = event_ref，text = events.summary）；`vec_state(event_ref PRIMARY KEY, updated_at)`；连接需 `enable_load_extension + sqlite_vec.load` | `tools/lvdata-mcp/src/lvdata_mcp/vec.py:1-3,92-113,145` |
| embedding provider（dashscope，模型/dim 配置）；**未配置 provider 则整个向量面关闭**（`build_provider → None`） | `vec.py:22,42-87`；`configs/dev/lvdata_mcp.yaml:65-72` |
| `sync_changed` 增量同步**含孤儿清扫**：`orphans = state 中不在 events 的 ref` → 删 `vec_chunks` + `vec_state`（`vec.py:258-264`）；`sweep_orphans` 同逻辑全库版（`:299-326`）。注意它只在 embed stage（flush 门控，`watcher.py:43,204-221`）运行 | `vec.py:239-287,299-326` |
| **无公开的按 event_ref 删除函数**（删除语句内联在方法体内）；`rebuild_session` 路径**不删** vec_state 中被删事件的行（`:214-237`） | `vec.py:214-326` |

### 1.5 day_stats 与 LLM 日总结对删除的可见性

| 事实 | 位置 |
|---|---|
| day_stats stage 候选 = `events WHERE updated_at > 游标` → **删除不可见**；force 也不能救被删空的日（`days` 由**现存**事件构造）；游标只在完成时推进 | `summary/day_stats.py:197-212` |
| `_run_day` 空日行为：候选日事件被删空时仅删 `status != 'ok'` 的残留行；ok 行**不重算不清理**（注释明示「纯删除产生的残留行不在本 stage 职责内」） | `day_stats.py:224-231` |
| 派生单源：`derive_counts/derive_hot_slots/derive_images/derive_day_stats`（counts 逐类原样、hot_slots 五类、images 取 video 事件按 window 轮转） | `day_stats.py:60-147` |
| LLM 日总结 dirty 发现三来源（事件游标 / pending-failed 兜底 / day_deferred）**全部依赖存活事件**；删除不产生任何信号 → **不触发重生成**；ok 行的旧 doc 永久 stale（`status` 不翻转；`_finish_failed` 也不清 doc，见 `bff_service_interfaces.md` §7.3 E11） | `summary/day.py:178-261`（游标 :186-193、deferred 兜底 :197-218、`_day_up_to_date` :309-320、待生成/清 pending :227-249）、`:577`（`_finish_failed`） |
| 活跃时区集合（≤2，day_summary 与 day_stats 共用单一出处） | `summary/timezones.py:13-29` |

### 1.6 写入路径与进程模型（为什么执行面必须在 lvdata-mcp）

| 事实 | 位置 |
|---|---|
| BFF 数据访问 = **只读连接**（`file:...?mode=ro`）；services 全仓**零写端点**（BFF 5 面全 GET + app 侧 healthz/readyz/metrics/WS）；唯一文件系统写先例 = m4a 转码缓存 | `services/bff/common/store.py:90-96`；`services/bff/router.py:11-16`；`services/bff/common/media.py:48-68` |
| **services 不能 import lvdata_mcp**：`services/pyproject.toml` 无该依赖；测试用 `pytest.importorskip("lvdata_mcp.index")` 表明跨包依赖被刻意避免；既有跨包代码靠「双副本同步」纪律 | `services/tests/unit/bff/test_search_api.py:448`；`services/bff/common/store.py:49`（「改动须双副本同步」注释） |
| 向量的 sqlite_vec 扩展加载、embedding provider 配置、day_stats 派生函数都在 lvdata-mcp 进程侧（BFF 的 python 环境不具备） | `vec.py:92-99`；`day_stats.py:60-147` |
| lvdata-mcp = lvdata DB 数据面的**唯一写入方**（events/day_summary/索引；例外 = services/session 直写 `timezones` 表，见下行）；进程内 watcher 串行 stage；prune 是唯一并发的自有写连接线程，先例 `_video_prune_once` 经 `asyncio.to_thread`；写连接统一 `WAL + busy_timeout=5000` | `watcher.py:291-303,341-410`；`main.py:77-82` 等 |
| lvdata-mcp **已有 HTTP 运维面**：`POST /api/analysis/trigger`（先例：pydantic 校验→400/404/503 + 入队响应）；Bearer 中间件（无 token 配置则放行）；见 `/api/status`、`/readyz` | `server.py:99-130,148-181`（routes :152-162） |
| 服务端口/令牌/路径：dev = `127.0.0.1:8090`、`token: lvdata_dev_token`、`data.root_dir=/opt/lvdata`（BFF 侧 `recording.base_dir` 同路径） | `configs/dev/lvdata_mcp.yaml:5-16`；`services/shared/config/models.py:266-275` |
| 跨进程写 lvdata DB 已有先例：services/session 直写 `timezones` 表（busy_timeout，**不重复设 journal_mode**——争锁教训） | `services/session/manager.py:36-41`（引 `job_persistence.py:103-111` 教训） |

---

## 2. 接口契约

### 2.1 BFF 对外：`POST /api/mylife/events/remove`

沿用 §0.2 惯例：query `token`（Phase 1 透传不校验）+ `device_id`（可见性收窄）。

**请求体**：

```json
{"event_refs": ["<rsession_id>:video:<frame_id>", "…"]}
```

| 项 | 约束 | 说明 |
|---|---|---|
| `event_refs` | 必填，1–50 个非空字符串 | 批量上限 50（决策 D7）；同一 window 的多条 video 可一次删 |
| `device_id` | 可选 query | 与 `bff.device_uuids` 白名单取交（镜像 timeline 的设备交集语义，`services/bff/timeline/service.py:188-191`）；**不得**用 overview 的 E2 例外——删除必须带可见性校验 |
| `token` | 可选 query | 已经 `get_principal` 校验（dev-token，失败 401 `unauthorized`；2026-10-13 启用） |

**校验与逐条语义**（决策 D7：部分成功即成功，不做 all-or-nothing；请求级只有「格式非法」是硬错误）：

1. `event_refs` 为空 / 超过 50 / 元素非字符串 → 400 `bad_request`（唯一整批拒绝条件）；
2. 可见设备集 = `list_sessions(device_id)` 的 device_uuid 集合（白名单 ∩ 请求值；**空集 = 无可见设备**）——新增只读 helper `LvdataStore.events_by_refs(refs)`（`SELECT event_ref, type, device_uuid FROM events WHERE event_ref IN (...)`）取行，逐条分派：
   - ref 不存在，或事件 device_uuid ∉ 可见设备集 → 记 `skipped: {reason: "not_found"}`（两种情况合并同码，不泄漏存在性）；
   - 可见但 `type != 'video'` → 记 `skipped: {reason: "not_video"}`（不删、不影响批内其余 ref）；
   - 可见 video → 进入待执行集合；
3. 待执行集合为空 → **不调用执行面**，直接 200 返回（`removed=[]` + 全部 skipped）；
4. 非空 → 调执行面（§2.2）；执行面复核期新发现的缺失（并发竞态）返回 `skipped: not_found`，与第 2 步结果合并输出。

（另：重复 ref **保序去重**——每个 ref 在 `removed` / `skipped` 中至多出现一次，两层同款实现。）

**响应（200）**：

```json
{
  "schema_version": 1,
  "removed": ["<rsid>:video:<frame_id>", "…"],
  "window_events_removed": ["<rsid>:window:<window_id>", "…"],
  "images_removed": ["<frame_id>", "…"],
  "images_pending": ["<frame_id>", "…"],
  "days_recomputed": [{"timezone": "Asia/Shanghai", "local_date": "2026-10-07"}],
  "skipped": [{"event_ref": "<rsid>:video:<frame_id>", "reason": "not_found"}]
}
```

**HTTP 状态**：请求格式合法即 **200**（含全部 skipped、`removed` 为空的情形）——部分成功即成功、重试友好；客户端按 `removed` / `skipped` 逐条处理。

| 字段 | 语义 |
|---|---|
| `removed` | 实际删除成功的 video 事件 event_ref（请求集合的子集；其余见 `skipped`——部分成功语义，D7） |
| `window_events_removed` | 因归零被连带删除的 window 事件 event_ref（可能为空；一个 window 只出现一次） |
| `images_removed` / `images_pending` | jpg 删除成功 / 失败（失败 → 保留 files 行，交给既有 video prune 自愈；见 §3.3） |
| `days_recomputed` | 已重算 day_stats 的 (时区, 本地日)（见 §3.1-5：存在受影响日日行的**所有**时区 × 受影响日） |
| `skipped` | 未删除的 ref 及原因：`not_found`（不存在 / 不可见 / 执行前已被并发删除）/ `not_video`（类型不符）；每个请求 ref 恰好出现在 `removed` 或 `skipped` 之一 |
| `vectors_pending` | **仅在 T2（向量删除）进程内异常时出现**（`true`）——残留由 embed `sync_changed` 孤儿清扫收敛（§3.2，D10）；追加式可选字段（先例：overview 的 `next_cursor`/`total`，`bff_service_interfaces.md` §8.2.1——客户端可忽略、不 bump `schema_version`） |

**错误映射**：

| 场景 | HTTP / code |
|---|---|
| 请求格式非法（空 / 超 50 / 元素非字符串） | 400 `bad_request`（唯一整批拒绝条件） |
| ref 级失败（不存在 / 不可见 / 非 video / 执行期竞态被删） | **200**，逐条见 `skipped`（不用 404——部分成功即成功，重试幂等） |
| `bff.lvdata_api_url` 未配置（端点禁用） | 503 `removal_disabled` |
| 执行面连接失败 / 超时 / 5xx（待执行集合非空时） | 502 `upstream_unavailable`（新码，加入 `router.py:18` 映射） |

### 2.2 执行面（lvdata-mcp）：`POST /api/events/remove`

- 与 `POST /api/analysis/trigger` 同面：Bearer 同中间件、错误体同形 `{"error":{code,message}}`（`server.py:165-181`）。
- **请求体同 §2.1（`event_refs`）**；参数校验 `RemoveParams`（新 schema，风格仿 `schemas.py:50-66` TriggerParams）。
- 响应 200 = §2.1 响应字段（不含 `schema_version`），即 BFF 直接透传。
- 错误：400 `invalid_param`（仅格式：空/超限/元素非字符串）；复核期发现的缺失（竞态）或非 video ref → 进 `skipped`（200，不阻塞其余）；500 仅内部异常。
- T2（向量删除）进程内异常**不改变 HTTP 语义**：吞掉 + warning，200 + `vectors_pending: true`，后续文件面照常执行（§3.2，D10——T1 已提交，不得以 500 诱导客户端重试）。
- 这是**唯一执行点**（BFF 只做转发与可见性校验）；同步执行（决策 D2），无入队。

---

## 3. 执行设计（级联语义与事务边界）

执行顺序按「原子性优先 + 残余面交给既有自愈机制」排列；崩溃点收敛见 §3.4。

**执行载体（硬约束）**：`EventRemover.remove` 是同步阻塞方法（T1 SQL 事务 + T2 + 文件 IO），HTTP handler 必须以 `await asyncio.to_thread(remover.remove, refs)` 卸载执行——直接内联会阻塞 lvdata-mcp 事件循环（healthz/`/api/status`/MCP 全停）。仓内先例：纯 SQL 的 day_stats 也走 `to_thread`（`pipeline.py:130-131`）、prune 同款（`watcher.py:295-299`）。注意 `api_trigger` 是**入队语义**（非阻塞），其内联写法**不可照抄**。

### 3.1 主事务 T1（`lvdata_inventory.db`，单写连接，WAL）

1. **同连接逐条复核与分区**：`SELECT event_ref, device_uuid, rsession_id, type, ts_ms, payload FROM events WHERE event_ref IN (...)`；逐条判「可删（存在且 `type='video'`）」vs「skip（缺失 → `not_found` / 非 video → `not_video`）」——这也是与 vision 并发重跑的最后一道复核，同 prune :374-390 精神。可删集合为空 → 不开事务、直接 200（全 skipped）。
2. **删 video 事件行（可删子集）**：`DELETE FROM events WHERE event_ref IN (<可删子集>)`——事务对子集整体原子（任一步异常 → 回滚，已判定 skip 的 ref 不受影响）。
3. **空 window 判定与连带删除**：对本次删除集合涉及的每个 `window_id`（取自步骤 1 的 payload；payload 无 window_id 的旧库存量行跳过），在步骤 2 之后、事务内计数（可见自身删除）：

   ```sql
   -- 剩余计数（跨 session；video 与 window 的唯一关联键 = payload.window_id）
   SELECT COUNT(*) FROM events
   WHERE type='video' AND json_extract(payload, '$.window_id') = ?;   -- == 0 时：
   SELECT event_ref FROM events
   WHERE type='window' AND json_extract(payload, '$.window_id') = ?;  -- 删除（收集其 ref/ts_ms）
   ```

   （window 行若已不存在——如历史上先被删——跳过，幂等。实现注意：先 `fetchall` 物化
   window 行再删——遍历同表游标期间 DELETE 是 SQLite 未定义行为，可能跳过行致漏删。）
4. **清 FTS 行**（同一事务、同一库）：`DELETE FROM search_docs WHERE event_ref IN (<删掉的 video + window refs>)`；`search_docs` 表缺失（索引未建）→ 跳过 + warning。水位 `index_state` **不动**：删除不产生滞后信号，且任何后续重建都从 events 读（§1.3），不会复活。
5. **day_stats 重算**（同一事务、同一连接；这是「同事务」而非事后重算的原因：day_stats 与 events 同库，进 T1 后**崩溃即回滚**，不存在「事件已删、统计 stale 且永不收敛」的窗口）：
   - 受影响日集合 = 步骤 2/3 删除事件的 `ts_ms` 折算的 `(tz, local_date)`，**目标时区 = 在 `day_summary` 中存在行的所有时区**（`SELECT DISTINCT timezone FROM day_summary`——量级 = 曾用过的时区数，实测小；window 行 ts = 窗口起点，可能落在与帧不同的日）。
     **有意不限于 `active_timezones`（≤2）**：删除对 stage 不可见，只重算活跃时区会让**非活跃时区**的残留行永久 stale——用户切回旧时区时（`bff_service_interfaces.md` §7.3 E14：overview 立即改读该时区行）会透出已删帧的缩略图（media 404）与旧计数。重算只写 `day_stats` 列、不碰 status/doc，与「非活跃时区不遍历」（`timezones.py:16`，stage 游标语义）不冲突；
   - 新增 `DayStats.recompute_days(conn, tz, days, t0)`（公开方法，复用现有派生单源）：
     - 日仍有剩余事件 → `UPDATE day_summary SET day_stats = derive_day_stats(events, t0)`（`_run_day` 同款日窗查询与排序）；
     - 日已删空 且 行存在：
       - `status=='ok'` → UPDATE 为**空派生**（`counts={}, hot_slots=[], images=[]`）——与 timeline/overview 一致，不残留已删帧缩略图（决策 D3）；
       - `status!='ok'` → `DELETE` 该行（镜像 `day_stats.py:224-231` 竞态兜底）；
     - 行不存在 → 跳过（该日从未出卡）；
     - **不推进 `day_stats_state` 游标**（游标只由 stage 的 `_run_tz` 推进，`day_stats.py:207-211`——否则会把其它 dirty 日标记成已覆盖）。
6. `commit`。

### 3.2 向量删除（T2，`lvdata_index.db`，独立连接 + sqlite_vec 扩展）

- 新增 `VecIndex.remove_refs(refs) -> int`：`DELETE FROM vec_chunks WHERE event_ref IN (...)` + `DELETE FROM vec_state WHERE event_ref IN (...)`（语句与孤儿清扫同款，`vec.py:258-264`）；向量库不存在 / `vec_index is None`（未配 embedding）→ 跳过 + warning。
- 执行顺序 = T1 之后（此时 refs 已确定含 window 行）。
- 崩溃残留（T1 后、T2 前）→ 下轮 embed 的 `sync_changed` 孤儿清扫兜底（`vec.py:258-264`；注意它在 flush 门控内，可能延迟到下一次 flush）。
- **进程内异常语义（D10，与 D9 同哲学）**：`remove_refs` 抛异常（向量库损坏 / sqlite_vec 扩展加载失败等）→ **吞掉 + warning log**（流程继续执行文件面），HTTP 仍 200、响应置 `vectors_pending: true`；T1 已提交不可回滚，残留孤儿由下轮 embed `sync_changed` 孤儿清扫收敛。**不返回 500**：客户端重试只会拿到全 `skipped: not_found`（事件已删），语义混乱且违背 D7 的重试友好承诺。

### 3.3 文件面（jpg + files 行，逐帧；与 prune 同序「先文件后行」）

- 定位 files 行：`SELECT file_id, file_path FROM files WHERE file_type='video' AND rsession_id=? AND file_path LIKE '%/' || ? || '.jpg'`（frame_id 取自 event_ref 后缀；一帧至多一行，命中 0 行 → 跳过）；
- 路径护栏：`Path(file_path).resolve()` 必须 `is_relative_to(root)`，否则跳过 + warning（镜像 prune :391-396）；
- `unlink(missing_ok=True)` 成功 → `DELETE FROM files WHERE file_id=?`（提交）；失败（OSError）→ **保留 files 行**、计入 `images_pending` + warning。
- 自愈：事件行已删 → 该帧成为既有 video prune 的候选（`type='video' ∧ ok ∧ 超期 ∧ 无事件`，`watcher.py:355-363`）→ 下一个清理周期 unlink(missing_ok) + 删行兜底（延迟 ≤ 保留期 + 清理周期；按当前配置 ≈ 72h + 24h）。
- 明确不动：window 内其余非关键帧的 jpg/files 行（无事件、与本次删的帧无关；由既有 prune 按其保留期处理）。
- 整段容错（D9/D10 同哲学扩展）：T1 已提交后文件面**任何异常**（files 行删除失败 /
  路径解析异常等）不 500——未确证帧全部计入 `images_pending`，交既有 video prune 自愈。

### 3.4 崩溃自愈矩阵

| 崩溃点 | 残留 | 收敛机制 |
|---|---|---|
| T1 内任意一步 | 回滚，零副作用（原子：events + search_docs + day_stats 同事务） | — |
| T1 提交后、T2 前 | 向量孤儿（vec_chunks/vec_state 残留行） | 下轮 embed `sync_changed` 孤儿清扫（`vec.py:258-264`） |
| T2 进程内抛异常（进程存活） | 同「T1 后 T2 前」：向量孤儿 | 同上；响应 200 + `vectors_pending: true`（D10） |
| unlink 前 | files 行 + jpg 均在、事件已删 | video prune（无事件 → 候选）unlink + 删行 |
| unlink 后、删 files 行前 | 行在文件不在 | 同上，`missing_ok` 幂等 |
| 全部完成 | 无 | — |

### 3.5 并发 / TOCTOU 口径（与 prune 同款，显式接受）

- **与 `video --force` 重跑**：force 把现存 files 行重置 pending 后整窗重跑；若其已选中目标帧、我们又在它 upsert 前提交 T1，事件会短暂"复活"（随后 jpg 已删 → 破图）。窗口为微秒级（我们在 T1 内基于最新已提交态复核，同 prune 收窄口径）；且 files 行删除后该帧不再进入 `select_pending_files`（`common.py:28-61`），后续 force 无法重跑。记录为残余竞态。
- **与 reindex/embed stage**：WAL 串行写；即使交错，FTS/向量重建从 events 读（删除后的集合）→ 不会复活已删 ref。
- **与 `inventory --force`**：该操作会把被删 files 行以 pending 复活 → 分析因 jpg 缺失判 failed 常驻——与 video prune 完全同口径（`docs/data_clean.md` §3.5），不做 tombstone（决策 D6）。
- **同事务 day_stats 与 stage 并发**：stage 只对"dirty 日"（`updated_at > 游标`）重算；被删日不再 dirty，互不干扰；若同时恰有新增事件使该日 dirty，stage 以更新快照重算 → 两个方向都收敛到最后一次写（WAL 串行）。

### 3.6 日志口径（skipped 记录 log）

- 执行面（lvdata-mcp）：info `events remove done`（removed/skipped/windows/images/days 汇总，仿 `video prune done` 风格）；每条 skip 打 warning（含 `event_ref` + `reason`，可检索）；T2 异常另打 warning（异常摘要），响应以 `vectors_pending` 标记（D10）。
- BFF：`skipped` 非空 → warning（各 reason 计数 + refs 明细截断）；请求本身照常进访问日志中间件（`services/app/access_log.py`，§0.1）。

---

## 4. 架构决策

### 4.1 执行层选择（核心决策，3 备选）

| 方案 | 内容 | 评估 |
|---|---|---|
| **A（推荐）lvdata-mcp 新增 REST 执行面，BFF 转发** | BFF 做可见性/类型前置校验 + HTTP 调用；写逻辑全在 lvdata-mcp（复用 vec 扩展加载、day_stats 派生单源、单写者纪律） | 与现状一致：运维面已有 trigger REST 先例（`server.py:99-130`）、Bearer、端口/令牌配置；BFF 保持只读 |
| B BFF 直写 DB | BFF 开 rw 连接执行全部级联 | 被否：services **不能 import lvdata_mcp**（0 跨包依赖；§7 迁移正是为消灭双副本派生）；vec0 需 sqlite_vec 扩展（BFF 环境没有）；day_stats 派生函数在 tools 侧单源，复制即重新引入已消灭的双份代码；破坏「BFF 只读 facade」与单写者纪律 |
| C BFF spawn CLI 子进程 | 新增 `lvdata-mcp remove …` 子命令，BFF `subprocess` | 被否：每请求一次进程启动开销/venv 路径/结果解析脆弱；与 trigger REST 的既有 ops 通道重复；CLI 与 watcher 的并发写虽已被接受（审计 doc 备注），但不必要 |

### 4.2 决策表（2026-10-09 已拍板：D7 用户指定「部分成功」，其余按建议值采纳）

| # | 问题 | 建议 | 备选 / 理由 |
|---|---|---|---|
| D1 | 执行层 | **A：lvdata-mcp REST 执行面** | B/C 见 §4.1 否决理由 |
| D2 | 同步 vs 异步 | **同步 200**（零 LLM 调用、行级 SQL + unlink，毫秒级；客户端可即时移除 UI） | 异步 202+轮询（trigger 模式）：多一条状态/轮询链路，删除是轻操作，不必要 |
| D3 | 删空日的 day_stats | **清零**（UPDATE 空派生；日卡保留——E6 先例，ok 行不删） | 保留 stale 计数：与 timeline 不一致且残留已删帧缩略图（overview images 会指到已删 media → 404），不推荐 |
| D4 | LLM 日总结 doc（可能提及被删内容） | **不重生成**（E6 先例：ok 残留行不清理；零成本；后续该日有新事件时 LLM 会用现存事件自然重生成） | 备选：把受影响 ok 日置 `status='pending'` → 走 `_check_pending_retry`（`day.py:335-348`）触发重生成——额外 LLM 成本 + 客户端「分析中」徽章亮起；如需另行拍板 |
| D5 | 端点默认状态 | `bff.lvdata_api_url` 未配置 → 503 `removal_disabled`（安全默认，显式开启） | 默认指向 `127.0.0.1:8090`：dev 少一行配置，但生产易误开 |
| D6 | `inventory --force` 复活被删帧 | **接受**（与 video prune 完全同口径，`data_clean.md` §3.5；不做 tombstone） | tombstone 表：新增状态面/清理面，超出需求 |
| D7 | 批量语义 | **1–50 条、逐条部分成功**（用户拍板）：请求级仅「格式非法」→ 400；ref 级失败进 `skipped`（不阻塞其余）+ 记录 log；合法请求恒 200，重试幂等（已删 ref 下次只是 `skipped: not_found`） | all-or-nothing / 缺失 404：已否决（一条坏 ref 拖垮整批，且重试还得用户自己挑） |
| D8 | 类型白名单 | 目前只删 `video`；非 video ref 逐条 skip（`not_video`，不整批拒绝——随 D7 部分成功语义），未来扩 window/audio 只改白名单 | — |
| D9 | unlink 失败 | 200 + `images_pending` 列出（不视为整体失败），prune 兜底 | 500 整体失败：事件已删无法重放，语义更差 |
| D10 | T2（向量删除）进程内异常 | **吞掉 + warning + 200 + `vectors_pending: true`**（2026-10-09 review 补充，与 D9 同哲学：T1 已提交，残留交给 `sync_changed` 孤儿清扫收敛） | 500：事件已删，重试只能得全 `skipped`——语义混乱、违背 D7 重试友好承诺 |

---

## 5. 落点清单（文件级）

**lvdata-mcp（执行面）**

| 文件 | 内容 |
|---|---|
| `src/lvdata_mcp/remove.py`（新增） | `EventRemover(cfg, vec_index)`：§3 全流程（T1 → T2 → 文件面）；内部自建 `DayStats(cfg)`、`sqlite3` 写连接（`_conn` 同款 WAL+busy_timeout，仿 `main.py:77-82`） |
| `src/lvdata_mcp/schemas.py` | 新增 `RemoveParams`（`event_refs: list[str]`，长度 1–50；风格仿 `TriggerParams` :50-66） |
| `src/lvdata_mcp/server.py` | `create_app(..., remover=None)` + `Route("/api/events/remove", api_remove, methods=["POST"])`（校验/错误体风格仿 `api_trigger` :99-130）；**`api_remove` 内必须 `await asyncio.to_thread(remover.remove, refs)`**（§3 执行载体；`api_trigger` 的非阻塞入队写法不可照抄）；`run_serve`（`main.py:141-172`）装配注入 |
| `src/lvdata_mcp/summary/day_stats.py` | 新增 `recompute_days(conn, tz, days, t0)`（复用 derive 单源；空日语义见 §3.1-5；不碰游标） |
| `src/lvdata_mcp/vec.py` | 新增 `VecIndex.remove_refs(refs)`（`vec.py:258-264` 同款语句封装） |
| `tests/test_remove.py`（新增） | §6.1 |

**services / BFF（对外面）**

| 文件 | 内容 |
|---|---|
| `services/bff/remove/router.py` + `service.py`（新增） | `POST /api/mylife/events/remove`：§2.1 校验 → 转发 → 错误映射；路由注册进 `services/bff/router.py:13-16`，`_STATUS_BY_CODE`（:18）加 `upstream_unavailable: 502`、`removal_disabled: 503` |
| `services/bff/common/store.py` | 新增只读 helper `events_by_refs(refs)`（`SELECT event_ref, type, device_uuid FROM events WHERE event_ref IN (...)`；ro 连接不变） |
| `services/bff/common/lvdata_api.py`（新增） | httpx 客户端：`POST {lvdata_api_url}/api/events/remove`，Bearer `lvdata_api_token`，超时可配；无 url → 抛 `removal_disabled` |
| `services/shared/config/models.py` | `BffConfig`（:266-275）新增：`lvdata_api_url: str = ""`、`lvdata_api_token: str = ""`、`lvdata_api_timeout_s: float = 30` |
| `configs/dev/server.yaml` | 新增 `bff:` 段（`lvdata_api_url: http://127.0.0.1:8090`、token 对齐 dev yaml :8） |
| `services/tests/unit/bff/test_remove_api.py`（新增） | §6.2 |

---

## 6. 测试计划

### 6.1 lvdata-mcp（`tests/test_remove.py`，仿 `test_day_stats.py`/`test_retention.py` 底座）

1. **主路径**：一窗 3 条 video（含跨 session 1 条）+ window 行 + files 行 + 真实 tmp jpg + search_docs + （有 provider 时）vec 行 → 删 1 条：事件/jpg/files 行/FTS 行/vec 行消失，**window 行保留**，day_stats counts 更新；
2. **空窗连带**：删到该 window 最后一条 → window 行被删（`window_events_removed` 命中），其 FTS/vec 行一并消失；
3. **跨 session 计数**：同 window 两 session 各 1 条，先删其一 → window 保留；再删其二 → 删除；
4. **day_stats**：part-delete 后 counts/hot_slots/images 按剩余事件重算；全删空 → 空派生清零（ok 行）、`status!='ok'` 行被删；**非活跃时区**（旧 tz，有 day_summary 行）同样被重算；**`day_stats_state` 游标不变**（断言）；
5. **window ts 跨日**：window 行 ts 与帧不同日 → `days_recomputed` 覆盖两日；
6. **部分成功**（D7）：混合批次（1 有效 video + 1 缺失 + 1 非 video）→ 200：`removed=1`、`skipped=[not_found, not_video]`，有效那条的级联（jpg/files/FTS/vec/day_stats）全部生效，无效两条零副作用；请求级 400 仅空/超 50/非字符串；
7. **重试幂等**：连续两次调同一批，第二次 200 + 全部 `skipped: not_found`、零副作用；
8. **无 provider / 无 vec 库**：跳过向量步不报错；
9. **文件面**：jpg 已缺失 → `missing_ok` 仍删 files 行；unlink 抛 OSError（monkeypatch）→ files 行保留 + 进 `images_pending`；
10. **路径护栏**：files.file_path 越 root → 跳过 + warning；
11. **search_docs 缺失**（未 reindex）→ 跳过不报错；
12. **T2 异常**（D10）：monkeypatch `remove_refs` 抛异常 → 200 + `vectors_pending: true` + warning；T1 级联（事件/FTS/day_stats）与文件面全部已生效；vec 孤儿仍在，且可被 `sync_changed` 孤儿清扫收敛（有 provider 底座时端到端断言，无则断言孤儿行仍在）；
13. **执行载体（防回归）**：`api_remove` 必须经 `asyncio.to_thread` 卸载——spy `asyncio.to_thread`（或记录 `remove` 执行线程 ≠ 事件循环线程）断言被调用；内联阻塞事件循环即失败。

### 6.2 services（`tests/unit/bff/test_remove_api.py`）

1. 请求体校验（空/超限/非字符串 → 400；错误体同 `{error:{code,message}}` 信封）；
2. 可见性：白名单外设备 / `device_id` 不匹配 → 200 + `skipped: not_found`（不泄漏）；`device_id` 为空 + 白名单空 → 全可见（镜像 timeline 测试写法）；
3. 非 video 类型 → 200 + `skipped: not_video`（不整批拒绝）；
4. 部分成功转运：混合批次只把「可见 video 子集」转发上游（断言 body），本地 skipped 与上游返回的 skipped 合并输出；全 skip 时不发起上游调用；
5. 转发：mock httpx（`MockTransport`）断言 URL/Bearer/body；上游 200 → 透传；连接错误/超时/上游 5xx → 502 `upstream_unavailable`；url 未配置 → 503 `removal_disabled`；
6. 不写 DB：断言测试库零变更（只读约束守护）。

### 6.3 真机 e2e（dev，仿 `data_clean.md` §9 核对流程）

1. 选 1 条真实 video 事件（单帧窗）→ dry-run 记录（事件/帧文件/files 行/FTS 行/vec 行/day_stats 中该帧）→ 实调 → 逐项核对：`/api/mylife/timeline` 无该事件、`media` URL 404、`/search` 无该 ref、`overview` 该日 images 不含该帧、`day_stats.counts.video` 减 1；
2. 复核 `day_stats_state` 未变、prune 下一个周期对同帧零操作（已删）；`cs.recording_files` 行仍在（只读约束）；
3. 单窗多帧场景同上（window 保留/删除两分支各一次）。

---

## 7. 明确不做

| # | 不做 | 依据 |
|---|---|---|
| 1 | 不删 derived 审计 JSON（`windows/<id>.json`、`frames/<id>.json`） | 与 video prune 同口径（`data_clean.md` §3.8/§4.5：人工核查用，保留） |
| 2 | 不动 `cs.recording_files`（chat_server.db） | lvdata-mcp 只读 ATTACH（`recording_watch.py:107-129`；`data_clean.md` §3.3） |
| 3 | 不重生成 LLM day_summary doc | 决策 D4（E6 先例；如需要以 status=pending 复用既有重试链） |
| 4 | 不删窗口其余非关键帧的 jpg/files 行 | 无事件帧由既有 prune 按保留期处理（`watcher.py:355-363`） |
| 5 | 不更新 `recording_sessions.cnt_video_frames` | 静态 session.json 拷贝（列定义 `ddl.py:204`）；BFF/mylife 读取面已不读 `recording_sessions`（`bff_service_interfaces.md` §0.3/§6） |
| 6 | 不触 `index_state` 水位 | 删除不产生滞后信号；重建从 events 读不会复活（§1.3） |
| 7 | 不做 tombstone / 不防 `inventory --force` 复活 | 决策 D6（与 prune 同口径，异常操作场景） |
| 8 | 不删非 video 类型（逐条 skip 报告，不整批拒绝）/ 不做按时间窗批量删 / 不做恢复（undo） | 需求边界；扩展位见 D8 |
| 9 | 客户端（Android/Web）删除 UI 与接入 | 本文范围外，另行排期 |

## 8. 实施步骤（拍板后）

1. **S1 lvdata-mcp**：`remove.py` + `schemas.py` + `server.py` 路由/装配 + `day_stats.recompute_days` + `vec.remove_refs`；
2. **S2 测试**：§6.1 全绿（含空窗/跨 session/游标不变/护栏用例）；
3. **S3 BFF**：`bff/remove/*` + `store.events_by_refs` + `lvdata_api.py` + 配置字段与 dev yaml + `_STATUS_BY_CODE` 扩展；
4. **S4 测试**：§6.2 全绿（services 测试入口按 `reference_services_test_invocation` 约定：仓库内 `cd services` + venv 运行）；
5. **S5 真机核对**：§6.3 三项；按 §6.3-1 的逐项数据核对记录本文档收尾；
6. **S6 文档收尾**：`bff_service_interfaces.md` 增「删除面」引用指针；本文档状态翻转 + 拍板记录。

---

## 9. 实施记录（2026-10-09）

**S1–S2 lvdata-mcp**：`remove.py`（EventRemover：T1 [events + 空窗连带 + search_docs +
day_stats 重算同事务] → T2 → 文件面）、`schemas.RemoveParams`、`server.api_remove`
（`asyncio.to_thread(remover.remove, refs)` 卸载 + `Route /api/events/remove`）、
`day_stats.recompute_days`（不碰游标）、`vec.remove_refs`。`tests/test_remove.py`
**20 例全绿**（主路径/空窗连带/跨 session/部分成功/重试幂等/无 provider/文件面/
护栏/T2 异常收敛/to_thread 防回归/重复 ref 去重/同批双窗归零/文件面 DB 异常降级）。
lvdata 全量 313 passed；2 例
`test_rebuild_dev`/`test_rebuild_flow` 失败为**预存环境问题**（lvdata venv 未装
services 的 `recording` 包——stash 对照验证与本改动无关）。

**S3–S4 services/BFF**：`bff/remove/{router,service}.py`（请求体手动解析 → 格式非法
400；可见性/类型预检 → 转发可见 video 子集 + 合并 skipped）、`common/lvdata_api.py`
（httpx + MockTransport 可注入）、`common/store.events_by_refs`、`BffConfig` 三字段、
`configs/dev/server.yaml` `bff:` 段、`_STATUS_BY_CODE` 增 `upstream_unavailable:502`/
`removal_disabled:503`。`tests/unit/bff/test_remove_api.py` **18 例全绿**；services
全量 **1513 passed / 2 skipped**；dev 配置加载验证通过
（`bff.lvdata_api_url=http://127.0.0.1:8090`）。

**Review 修复（2026-10-09，实现后自查）**：① `_delete_empty_windows` 曾遍历 events
游标期间对同表 DELETE（SQLite 未定义行为，可能跳过行致 window 漏删）→ 先 `fetchall`
物化 + 同批双窗回归用例；② 重复 ref 未去重（`removed`/本地 `skipped` 出现重复条目，
违反「每 ref 至多出现一次」）→ 两层保序去重；③ 文件面 DB/OS 异常曾冒泡为 T1 之后
的 500（与 D9/D10 冲突）→ 整段降级：未确证帧计入 `images_pending` 交 prune 自愈
（触发器模拟 DELETE 失败用例守护）。修复后两套全量重跑全绿、沙箱真实库快照复跑
两分支结果不变（`run_20261009_123408/report.json`）。

**S5 沙箱核对（真实库零写入，按「破坏性验证先走副本沙箱」约定）**：
`/tmp/lvdata_remove_sandbox/`（sqlite `backup` 一致性快照 + 独立 root + 路径重基）
对真实库 2026-10-09 快照执行两分支，逐项前后对比：
- **A 多帧窗**（窗内 2 条 video）：删 1 → 该 video 的事件/FTS/vec_chunks/vec_state
  全清；**window 行及其 FTS/vec 保留**；day 2026-09-15 `counts.video` 4→3、目标帧
  从 `images` 移除；files 行删除、jpg 删除；`day_stats_state` 游标不变；
- **B 单帧窗**：video + window 连锁删除（含各自 FTS/vec）；day 2026-09-19
  `counts.video` 6→5、`images` 移除；files 行/jpg 删；游标不变。

报告：`/tmp/lvdata_remove_sandbox/run_20261009_122608/report.json`（脚本
`/tmp/lvdata_remove_sandbox/verify.py`）。附注：首跑以未重基副本意外验证了**路径护栏**
（files 行越 root → 跳过 + 保留，§3.3 生效）。

**待办（人工拍板/操作）**：
1. **真机实删**（经 BFF 或直调执行面）——破坏性操作待用户确认；`lvdata-mcp` /
   services 需重启加载新代码（当前运行进程仍是旧代码）；
2. ~~Android 客户端接入与删除 UI~~ **已实施（2026-10-09）**：Gallery 编辑模式 + 批量删除
   （设计/实施记录见 `docs/android_timeline_image_gallery.md` §9；真机手测待办）；Web SPA 待排期。
