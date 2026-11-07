# 录制数据处理重构 — 开发与测试计划

> 依据：`docs/recording_data_process.md`（已评审，契约冻结）。
> 2026-09-15 rev：产品未上线、dev 数据可弃 → 取消 `source: fs|cs` 双轨与
> 对拍/灰度，cs 单链路直建；WS4 重定义为 dev 重建验证。
> 原则：每个工作项 = 代码 + 对应测试 + 与设计文档条款对照的验收；按 WS 顺序
> 逐步实施，每步可独立验证、可回退。

## 工作分解（WS = workstream，按依赖排序）

### WS1 服务端：recording_files 表与写入（P0，lvdata 零改动）

| # | 任务 | 产出 | 测试 |
|---|------|------|------|
| 1.1 | ChatStore：`recording_files` DDL + `record_file()`/`touch_file()` API（阻塞入队 + 严格单调 ms bump + trailing-edge 语义） | `chat_store.py` | 新行/重复行/并发 bump/队列灌满阻塞/入队失败告警 |
| 1.2 | 写入点接线：session.json（start/finalize/ticker）、audio close+rename、video 帧、三个 jsonl append（先落盘后登记） | `recording/session.py` | 每种文件写后行存在、size/updated_at 正确、append 后 bump |
| 1.3 | agent job 行字段透传：adapter config `nickname` → delivery → PushMessage(`agent_id`) → publisher → `record_agent_job_result(agent_name, agent_id, job_key)` → `ingest_agent_job_result` → `RecordingSink.on_chat_text` → chatlog 写行 | `agent_relay/*`、`session/agent_job_ingest.py`、`recording/sink.py`、`recording/chatlog.py` | 行含三字段；未配置 nickname 恒 ""；旧调用签名兼容 |
| 1.4 | backfill CLI：扫 data root 生成存量行（INSERT OR IGNORE 幂等；声明离线/在线模式） | 服务端 CLI | 跑两遍行数不变；与实时写入并发安全 |

### WS2 lvdata：同步层（P1a）

| # | 任务 | 产出 | 测试 |
|---|------|------|------|
| 2.1 | `files` 表新列（`src_updated_at`/`processed_updated_at`/`processed_offset`）+ `recording_files_sync_state` 新表 + DDL 清理（删 `agent_job_links`/`agent_jobs_sync_state` 定义；`chat_messages` 表保留、此后无写入方） | `ddl.py` | 幂等；dev 清库重建口径（无存量升级包袱） |
| 2.2 | `recording_watch.py`：ATTACH 只读 + keyset + 单事务；新行初始状态、ON CONFLICT 不覆盖语义、session 兜底行、session.json 即时处理（finalized→day_force）、sync lag。**session.json 映射逻辑自 `scanner._upsert_session`/`_write_day_force` 迁移复用**（不重写） | 新模块 | 字段映射 golden、崩溃重放、FK 路径、sync lag 口径 |
| 2.3 | inventory stage 直接替换为 `recording_watch`（**无 source 开关、无 fs 回退**）；**同批删除** `scanner.py`、`jobs_watch.py` 及各自测试；`detect_dirty` 暂留至 3.5（dirty 会话链在本步仍驱动 audio/video 取件，删除会断中间态） | `watcher.py`/`pipeline.py`/`config.py` | 空表优雅空转；全链（backfill→inventory→audio/video→events）回归绿 |

### WS3 lvdata：处理层（P1b）

| # | 任务 | 产出 | 测试 |
|---|------|------|------|
| 3.1 | 事件类型扩展：consts（`transcribe=1/translate=2` 重排）、store CASE ×2、tools（searched_types/type 校验）、day TYPE_MAP、tests；bff 同步（如在役） | lvdata + bff | golden 排序；新类型时间线/检索可查 |
| 3.2 | chatlog pass：offset 增量（半行不推进）、ref_id 清洗/兜底、三类事件映射、内容比对幂等；`chat_messages` **不写**（停写裁定 G——原写入方 scanner 已删，勿在 pass 中恢复） | 新模块/`pipeline.py` | 续读/半行/清洗/幂等/agent_job 字段 |
| 3.3 | audio/video pass 改造：全局取件、移除 `s.status` 过滤、video 按 `(device_uuid,时间戳)` 分组 | `analysis/*`、`common.py` | 跨 session 合并、多设备隔离、退避保持 |
| 3.4 | index（变化事件→session FTS 重建）+ vector（per-event 增量删除+重插） | `index.py`/`vec.py`/`pipeline.py` | 增量正确性、无孤儿 chunk |
| 3.5 | watcher 重构：round 标记（无数据）、删 per-session 队列/锁/`detect_dirty`、stage 集合变更（-agent_jobs/+chatlog）、force 按类型分派；配置删除 `finalize_stale_s`；**§2.6 fs 扫盘路径至此删除收口** | `watcher.py`/`tools.py`/`config.py` | 崩溃恢复、force 语义、stage 名同步 README |

### WS4 dev 重建与验证（P2）

| # | 任务 | 产出 | 测试 |
|---|------|------|------|
| 4.1 | dev 库重建：清空 `lvdata_inventory.db`（数据可弃）→ backfill 登记存量文件 → 全链处理重建 events/index/vec/day_summary | 重建步骤 | 核数：events 覆盖存量 jsonl、无孤儿、水位/状态正常 |
| 4.2 | 真实录制端到端：音频轮转 / chat 对话 / agent job 投递 / idle finalize 全流程 | — | 事件与产物符合预期；重跑幂等（无新 events） |

## 删除动作归属（cs-only，一次性删除，无回退需求）

| 删除项 | 随 | 前置/说明 |
|--------|-----|----------|
| `scanner.py`（discover/mtime/staleness/`.part`）+ `test_scanner.py` | WS2.3 | session.json 映射逻辑先在 2.2 迁移进 `recording_watch` |
| `jobs_watch.py` + `agent_job_links`/`agent_jobs_sync_state` DDL + `test_jobs_watch.py` | WS2.1/2.3 | agent_job 事件由 3.2 chatlog pass 接管（中间态暂缺，见下） |
| `detect_dirty` / per-session 队列/锁/链 / `finalize_stale_s` 配置 | WS3.5 | 取件改按 `files` 状态全局扫描（3.3）后删 |
| `chat_messages` 写入 | 随 WS2.3 自动达成 | 原写入方 scanner 删除后无写入方；3.2 不恢复写入（裁定 G） |
| fs 相关测试 fixtures / conftest | WS2.3 起随删随改 | golden 全链测试在 WS3/4 新建 |

### WS5 flush 门控（优化 C 定稿，2026-09-15）

| # | 任务 | 产出 | 测试 |
|---|------|------|------|
| 5.1 | `flush_state` 表（`last_flush_at`；flush 组全部成功后写入，崩溃不推进） | `ddl.py` | 幂等建表 |
| 5.2 | watcher：round 拆分**常跑组**（inventory/chatlog/audio/video/reindex）与 **flush 组**（embed/day_summary）；`_flush_due()` 四条件（count≥`flush_min_events` / 最旧 ts 超 `flush_max_delay_s` / day_summary 非 ok 行 / force·target 绕过）；组成功写水印 | `watcher.py`/`config.py` | 门控四分支 + 跳过分支 + 水印崩溃语义 |
| 5.3 | 适配既有测试（round 序列断言、重建全链）+ 两包全量回归 | tests | 全绿 |

要点（与设计文档 §5.C 一致）：FTS 不节流（本地零成本、保检索新鲜）；不设
day_force 专用 bypass（时限覆盖）；门控查询失败宁跑（安全侧）。

## 顺序与关键路径

- 关键路径：**1.1 → 1.2 → 2.1 → 2.2 → 2.3 → WS3 → 4.1**（4.1 重建验证依赖处理层可跑）。
- **中间态可接受**：WS2.3（删 jobs_watch）到 WS3.2（chatlog pass 铸造 agent_job
  事件）之间，agent_job 事件暂缺——dev 分支上不要求该窗口可运行；其余链路
  （audio/video 经 detect_dirty 会话链）保持工作。
- 可并行：1.3（独立于 1.1）、3.1（独立）、bff 同步。
- 每步完成 = 测试绿 + 文档条款对照 + 无回归（两包各自测试套件全绿）。

## 测试策略

| 层 | 手段 |
|----|------|
| 单测 | pytest（`services/tests/` 与 `tools/lvdata-mcp/tests/` 各自套件） |
| 契约 | 服务端：行 schema/不变式（单调 bump、先落盘后登记）；lvdata：事件映射 golden |
| 端到端 | fixtures 数据根 → 全链产出 golden 断言；重建幂等核数 |
| 联调 | 真实录制一段（音频轮转/chat 对话/agent job 投递/idle finalize）端到端符合预期 |
| 回归 | 每次改动跑两包全量测试；bff 如在役跑其套件 |

## 里程碑

| M | 内容 | 出口条件 |
|---|------|----------|
| M1 | WS1 完成 | 服务端可产出完整 recording_files 流；lvdata 无改动、现网行为不变 |
| M2 | WS2+WS3 完成 | 全链（backfill → inventory → passes → events）在 dev 数据上跑通 |
| M3 | WS4 完成 | dev 重建核数通过 + 真实录制端到端通过 |

## 收尾记录（2026-09-15）

**已完成**：WS1 全部｜WS2（含一次性删除：scanner/jobs_watch/chat_messages 停写）｜
WS3.1–3.5（含 review 修复：inventory 入 round 序列、embed force 全量、round 期间
trigger 不吞）｜WS4.1（dev 重建：备份→清库→backfill→全链→核数→幂等，真实环境
通过）｜注册失败本地 reconcile 清单（`<root>/recording_files_reconcile.jsonl`）｜
死 PG ORM 与 repository 清理｜文档修订（两处裁决反转 + v2 文档「已被取代」横幅）｜
开放问题 I 核实结案（连接即建会话，落行路径闭合）。

**剩余**：
1. **WS4.2 真实录制端到端**——前置：部署本分支 `services/` 到 dev chat server
   （新代码才写 recording_files），随后设备录制验证（音频轮转/chat/agent job/
   idle finalize）+ 重跑幂等；
2. **开放问题 C 已定稿（§5.C）→ WS5 实施中**；**E 参数**（bump 防抖窗）仍后置
   （当前逐条 bump 可用）；
3. migration 0002 保留（alembic 链 0001→0002→0003 中间环，见设计文档 §2.6）；
4. bff 已同步（如在役）；Android 事件类型适配按计划后期。
