# session.json 静态化重构（一次性站位文件 + 删除 day_force）

日期：2026-09-15 ｜ 状态：**已实施**（2026-09-15，见 session_json_refactor_plan.md 收尾记录；dev E2E 随 WS4.2） ｜ 关联：`docs/recording_data_process.md`（本次为其后续修订）

> **2026-09-15 评审意见已并入**：新增「start() 幂等首写」（§1 现存缺陷、§3.A.2a、§3.D）；
> 裁决 B 影响表述修正 + 记录廉价修法（§2）；裁决 A 兜底机制表述修正（§2）；
> 回滚路径与结束时间取舍（§5）；时间戳规范化（§3.A.1）；命令勘误（§4.3）。

> **2026-09-15 二轮评审意见已并入**：start() 幂等首写定夺「登记照常 + 回读 started_at」（§3.A.2a、§3.D）；
> started_at 格式变更影响限定 + `normalize_ts()` 入库归一（§3.A.1、§3.B.9）；裁决 B 廉价修法补 per-device 分组（§2）；
> 「recording_sessions.timezone」指明 lvdata 侧（§2.1）；旧文件字段重摄入的良性不对称 + duration_ms 无读取方核实（§3.B.9）；
> 回滚补 server 方向、混部双向明示（§5）；静态核数标准写实（§4.2）。

> **2026-09-15 三轮评审意见已并入**：「照常摄入但不消费」措辞统一，消除 §3.B.9 与 §5 的内部矛盾（§3.B.9、§5）；
> 存量 started_at 格式收敛口径收窄——仅覆盖重同步的行，需一次 `stage inventory --force` 全量收敛（§3.A.1）；
> 裁决 B 廉价修法排序键补「按解析后毫秒」（§2）。

## 1. 背景与目的

当前 `session.json` 由服务端反复重写，是录制链路里唯一持续变更的 meta 文件：

| 写点 | 位置 | 内容变化 |
|---|---|---|
| `start()` | `services/recording/session.py:122-123` | 首写 + cs.recording_files 登记（kind=session） |
| ticker `flush_meta()`（60s，仅 active） | `session.py:202-205`，`service.py:146-147` | 重写 `status/last_active_at/timezone` + 登记行 touch（bump updated_at） |
| `finalize()` | `session.py:195-196` | 写 `status=finalized, finalized_at` + touch |

副作用：lvdata 轮询因 updated_at 反复 bump 而反复同步同一文件；文件同时承载"身份"与"在线状态"两种语义。

**本次改为**：session.json 只在 `RecordingSession.start()` 写一次，作为静态"站位文件"（会话身份凭据），不再周期刷新、不在 finalize 重写。

**另有一个现存缺陷须一并修正**（静态化后更显眼）：`start()` 在 **server 重启/长断线后会被再次调用**
（内存 registry 丢失 → `acquire()` 走 `_find_or_create` → `chat_store.get_or_create` 返回**同一
recording_session_id** → `session.start()`），现状会**重写 session.json 并把 `started_at` 重置为
重启时刻**——“只写一次”在跨进程场景下并不成立，且会污染 lvdata/bff 的 `started_at` 语义
（`ORDER BY started_at`、回放解析阶梯）。本次一并改为「**存在即不覆盖**」（见 §3.A.2a）。

### 1.1 目标内容（7 键）

```json
{
  "schema_version": 1,
  "recording_session_id": "...",
  "device_uuid": "...",
  "user_uuid": null,
  "session_date": "2026-09-15",
  "started_at": "2026-09-15T08:11:28.006Z",
  "config": { "audio_container": "ogg", "audio_codec": "opus", "audio_segment_s": 60,
              "video_format": "jpeg-sequence", "image_enabled": false, "ai_audio_recorded": false }
}
```

移除：`timezone`、`status`、`last_active_at`、`finalized_at`。

## 2. 裁决记录（2026-09-15）

- **裁决 A：彻底删除 day_force**。finalize 信号消失后，lvdata `day_force`（finalize → 收尾日强制生成 day summary）失去唯一写入方 → 删除表 + day.py 逻辑 + 测试。
  **收尾日保障（评审修正表述，两级且相互独立）**：**首要保障是每日自身的 debounce**——`_debounce_ok` 按「当日 MAX(ts_ms)」独立计时，停录满 `summarize_quiet_s` 后该日即可生成；**flush 门控条件 2**（全局最旧 pending 事件 ts ≥ `flush_max_delay_s`）只负责让 flush 组放行，不承担单日静默判定。跨天连续录制下：day A 的事件不再新增 → 其 debounce 独立到期 → 跨天后 ~10 分钟内生成（不依赖设备停录）。`trigger_analysis(force)` 的 `generate(force=True)` 路径与该表无关，保留。
- **裁决 B：bff 回归记为已知代价（评审修正表述）**。`resolve_session_at` 步骤② containment 上界（`finalized_at∨last_active_at`）变 ∞ 后，`contained.sort()` 取最早 started——**任何未被步骤①（事件精确匹配）命中的 t_ms，只要存在 ≥2 个会话，就恒归到最早开始的会话**（并非仅"晚时间戳"的边缘场景）。
  **影响半径已核实**：唯一调用方 = `services/bff/replay/service.py:30`（**回放**），属 Android/后期阶段 → 本阶段不动 bff 代码、仅文档标注。
  **已记录的廉价修法（列为回放阶段第一项）**：② 的上界改用「按 `started_at` 排序后**下一个会话的 `started_at`**」（无则 ∞）——纯 bff 侧几行、不需要任何服务端新信号，且比 `finalized_at` 更稳（顺带修掉 finalized_at 缺失时的既有弱点）。实施要点（二轮评审）：「下一个会话」须**按 device_uuid 分组**（白名单内）取——多设备会话 started_at 交错时全局排序会互相误截；started_at 相同的平局按 rsession_id 字典序定界，与步骤①的确定性口径一致。**（三轮评审）排序/定界键须按解析后毫秒（`_parse_iso_ms`）比较**——存量行秒内跨格式混合（`.006182+00:00` vs `.006Z`）时字符串序 ≠ 时间序。

### 2.1 显式保留项

- **客户端时区上报链路保留**（SDK → gateway `canonicalize_timezone` → `sink.on_session_config` → `RecordingSession.timezone` 内存字段），仅不再落 session.json。lvdata `recording_sessions.timezone` 因此恒为默认 `'UTC'`（指 lvdata 侧表，ddl.py:167；chat_server 侧同名表本无此列，chat_store.py:66-73）——全仓库无读取方（bff `graph/service.py:333` 注释即言其"恒落空"），零功能损失。
- `user_uuid`（恒 null）、`config` 块保留，作为身份 schema 稳定性的一部分。

## 3. 改动清单

### A. services/recording（写侧）

1. `services/recording/session.py:606-634` `_write_session_json()`：payload 删除 `timezone(:613)`、`status(:614)`、`last_active_at(:616)`、`finalized_at(:617)`；更新段注释（静态占位语义）。顺带把 `started_at` 规范为 §5.1 口径（`timespec="milliseconds"` + `Z`；现状为微秒 + `+00:00`，趁静态化一次到位）。影响口径（二轮评审修正）：fromisoformat 系解析方（lvdata `_to_epoch_ms`、bff `_parse_iso_ms`）无损；但 SQL 字符串排序（bff `store.py:133` `ORDER BY started_at`）在同秒跨格式下有边缘倒置（`.006182+00:00` vs `.006Z`），影响限于 graph「同日最新 session」选取与回放阶段廉价修法排序，可接受——DB 列由 §B.9 的 `normalize_ts()` 入库归一收敛，但**收敛仅覆盖被重同步的行**（三轮评审）：静态化后旧会话 updated_at 不再 bump → 不会自然重同步，存量列格式需跑一次 `lvdata-mcp stage inventory --force` 全量收敛（§4.3 已含此步）。
2. `session.py:122-123` `start()`：保持写一次 + 登记（唯一写点）。
2a. **`start()` 幂等首写**：`session.json` 已存在 → **跳过写盘**（不覆盖、不 bump `updated_at`）；仅不存在时写首份。覆盖"server 重启 / 长断线后 `acquire()` 对同一会话再次 start"的场景（见 §1）。两点实施定夺（二轮评审）：
    - **登记照常**：`_sync_session_json(register=True)` 不因文件存在而跳过——`record()` 为 INSERT OR IGNORE（chat_store.py:179，冲突不 bump），幂等零副作用；且 `start()` 内写盘(:122) 与登记(:123) 间存在崩溃窗口（已写盘未登记 → cs 无行），跳过登记将使该会话对 lvdata 永久不可见（仅剩 backfill reconcile 兜底）。
    - **回读 `started_at`**：跳过写盘时读盘还原 `_started_at_iso`，保持内存态与盘一致（该值虽目前只用于 payload 组装，但属会话身份字段；不回读则重启后内存留存"重启时刻"的假值，未来基于内存态的功能会静默拿错）。
3. `session.py:497-510` `_sync_session_json(register=)` 改为 `_register_session_json()`：去掉 `register` 参数与 `touch` 分支，只保留首次登记；docstring 注明"此后 session.json 不再变动"。
4. `session.py:188-200` `finalize()`：删除 `self._write_session_json()`(:195) 与 `await self._sync_session_json()`(:196)；保留 `_enqueue_close()`/writer 收尾。
5. `session.py:202-205` `flush_meta()`：整个删除。
6. `services/recording/service.py:145-147` `_tick()`：删除 active session 的 `flush_meta()` 调用块；保留跨天 finalize(:124-132) 与 idle TTL(:134-143)。`_ticker`(:110-116) 与 `meta_flush_interval_s` 保留（现语义 = ticker 周期），同步改 `services/shared/config/models.py:228` 与 `service.py` ticker 注释。
7. 注释措辞同步：`sink.py:39`、`file_registry.py:6`（"session.json 重写"→"不再重写"）、`chat_store.py:368`。

### B. lvdata 同步侧

8. `tools/lvdata-mcp/src/lvdata_mcp/recording_watch.py:247-268` `_apply_session_json`：删除 `prev` 查询(:259-261) 与 finalize 转换块(:265-268) → 只做 读盘 → `_upsert_session` → `stats["sessions"] += 1`。
9. `recording_watch.py:272-319` `_upsert_session`：删除 status/finalized_at 形参与 **finalize 转换逻辑**；对 4 字段的 `.get` 映射**保留**——status/timezone/last_active_at/finalized_at 照常摄入（缺失时默认 status→`''`、timezone→`'UTC'`、其余→None），只是**不再被任何消费方读取**（三轮评审措辞统一：是「照常摄入但不消费」，不是「忽略」——旧文件重同步后字段值仍在行内，这正是本条事实①良性不对称的前提）。`SESSION_COLUMNS` 与 `recording_sessions` 表结构不动（bff/index/store 有 SELECT）。写入前 `started_at` 过 `normalize_ts()`（ddl.py:225，agent_jobs 归一化同先例），DB 列随重同步收敛为 §5.1 规范格式。两点已核实事实（二轮评审）：① 存量盘上旧 session.json 仍带 4 字段，`--force` 重同步会经 `.get` 重新摄入——**历史会话保留原 containment 上界，仅新会话上界变 ∞**（良性不对称，收窄裁决 B 回归面）；② `_compute_duration_ms` 删除后重同步旧会话 `duration_ms` 由计算值变 None——该列全仓无读取方（bff 的 `duration_ms` 为 ReplayResponse 自身字段，与 recording_sessions 列无关），安全。
10. `recording_watch.py:321-336` `_write_day_force` 删除；随之 `_to_epoch_ms`(:93-102)、`_compute_duration_ms`(:109-115) 变为无用 → 删除，并从 :34 import 移除 `ms_to_local_date`。
11. `recording_watch.py:163-165` stats 去掉 `"finalized"`；`main.py:188` 打印行去掉 `finalized={...}`（否则 KeyError）。
12. 模块 docstring :13-15 更新（无 finalize→day_force）。

### C. day_force 彻底删除（裁决 A）

13. `tools/lvdata-mcp/src/lvdata_mcp/summary/day.py`：
    - docstring :8/:14 措辞；
    - `discover()` :128 删 `dirty |= self._check_day_force(...)`；:164-166 删 day_force DELETE（保留 day_summary/day_deferred 清理）；
    - `_day_generatable` :197-213 删 `_in_day_force` 短路(:199-200)，docstring 改"debounce + failed 退避"；
    - 删除 `_in_day_force`(:278-281) 与 `_check_day_force`(:283-299)；
    - `_generate_one` 0 事件分支 :362-371 删 day_force DELETE；成功路径 :431-432 删 day_force DELETE（保留 :433-434 day_deferred）；
    - `day_deferred` 逻辑（day.py:129-150、watcher.py:234-240）**不受影响**。
14. `ddl.py`：删 `DAY_FORCE_SQL`(:98-106) 与 `V2_DDL`(:220-221) 条目；`migrate()` 增加幂等 `DROP TABLE IF EXISTS day_force`（存量 dev 库；先例 chat_messages :295-297）；修掉 :98 "force_day_summary 载体"失效注释与 :110 "与 day_force 同构"措辞。

### D. 测试

**services**（`services/tests/unit/recording/`）

- `test_recording_session.py:121-142`：删两个 timezone 断言测试；新增 `test_session_json_written_once_and_static`——start 后断言 key 集合 == 7 保留键；`on_session_config(timezone="+08:00")` + `finalize()` 后重读文件，内容与 mtime 均不变。
- 新增 `test_start_is_idempotent_existing_session_json_untouched`——同 (device, rsession_id, storage_path) 二次构造 RecordingSession 并 `start()`（模拟 server 重启后 `acquire()` 命中同一会话）→ session.json 内容与 mtime 均不变、内存 `_started_at_iso` 与盘上一致（§3.A.2a 回读）；registry 断言**无 touch、无 updated_at bump、无新增行**——允许幂等 record 调用（§3.A.2a 登记照常，断言"无登记调用"会把实现推向危险的全跳过）。
- `test_file_registry.py:239-253`：断言 finalize **不再** touch session.json（保留 audio segment 断言）；:119-132 start 登记断言不变。
- （可选，或并入回放阶段第一项）bff 侧 characterization test 钉版裁决 B：两个 end（finalized_at/last_active_at）为 NULL 的会话，t_ms 落在后一个会话区间 → 断言解析到最早会话。不动 bff 代码，仅把已知代价以可执行形式固定，廉价修法落地时即为现成红灯。

**lvdata**（`tools/lvdata-mcp/tests/`）

- `test_recording_watch.py`：fixture :63-75 去 4 字段；golden :122-148 改断言（status `''`、timezone `'UTC'`、无 finalized 计数）；**删** :150-173 `test_finalized_transition_writes_day_force`；:201-209 兜底测试不变；新增"静态重同步幂等"用例（cs updated_at 被 bump 后重放同一文件 → sessions=1、meta 行刷新、无 day_force 表）。
- fixture 同步：`conftest.py:124-128`、`test_rebuild_flow.py:95-102`、`test_rebuild_dev.py:20-24`。
- `test_day_summary.py`：删 :289-299、:670-687、:737-750；改 :488-510（去掉 day_force 插入与断言）。
- `test_ddl.py:73`：期望表清单移除 `day_force`；migrate 测试补"存量 day_force 表被 DROP"用例。

### E. 文档

- `docs/recording_data_process.md`：写点表 :56（仅 start）、登记表 :143-144（不再 touch）、读者描述 :187-201（无 finalize/day_force）、测试计划 :373、§5.C 说明 :444-450。
- `docs/server_recording_design.md`：ticker 职责 :183/:212、finalize 写盘 :333-334/:346、§5.3 JSON 结构 :394-417、`meta_flush_interval_s` 注释 :539/:554。
- `docs/timezone-support.md`：:58-63/:134-140 与 §10——时区上报保留、不再落 session.json。
- `docs/data/lvdata_mcp_service_v2.md` 等：按仓库惯例加一条**带日期修订裁定**（day_force 废止、session.json 静态化）；`docs/data/lvdata_inventory.md` §2.3（v1 legacy）标注 4 字段移除。

## 4. 验证

1. 单测：
   - `cd services && python -m pytest tests/unit/recording -q`（再全量 `tests/unit`）
   - `cd tools/lvdata-mcp && python -m pytest tests -q`
2. 静态核数：`grep -rn "day_force\|flush_meta" services tools/lvdata-mcp/src` → 仅剩 ddl.py 的 `DROP TABLE IF EXISTS day_force` 语句与历史沿革注释（对照 chat_messages 先例 ddl.py:205-208）。
3. dev E2E（与既有 WS4.2 部署窗口合并）：
   - 部署本分支 services/ 到 dev chat server；录制一段（跨 ≥1 个 60s ticker 周期）→ session.json mtime 首写后**恒定**；cs.recording_files 该行 updated_at 不再 bump。
   - lvdata：`lvdata-mcp stage inventory --force` 输出无 finalized；`sqlite3 lvdata_inventory.db ".tables"` 无 day_force；files/`meta` 行与 recording_sessions 行（session_date/started_at）正常。
   - 停止录制后等 `summarize_quiet_s`（600s）→ 确认无 force 行也生成当日 `day_summary`（status ok）。

## 5. 已知代价 / 风险

- **bff containment 上界变 ∞**（裁决 B，`services/bff/common/store.py:281-334`）：未命中步骤①的 t_ms、存在 ≥2 会话时恒归最早会话（影响半径 = 回放，`bff/replay/service.py:30` 唯一调用方）；不动代码，在 `store.py:285` docstring 与本文件标注；廉价修法见 §2。
- **回滚路径**：① **lvdata 方向**——新 lvdata 的 `migrate()` 已 DROP `day_force`，若回滚到当前 lvdata（finalize 转换会 INSERT day_force），同步事务将因表不存在报错、同步卡住。方案 = 重建 `day_force`（建表 SQL 取删除前的 `ddl.py` 版本），或把 DROP 挪到确认不回滚之后的手工步骤。② **server 方向**（二轮评审补充）——回滚旧 server 后，旧代码无幂等首写守卫，重启/同日复连会重新写入动态字段并把 `started_at` 重置为重启时刻（§1 缺陷复现）；对新 lvdata 无害（这些字段照常摄入但不消费——无 finalize 转换、不写 day_force，三轮评审措辞统一），记为回滚期已知行为回归。
- **会话「结束时间」不再落盘**（chat_server 的 recording_sessions 无该列；lvdata 侧新会话 `finalized_at` 恒 None）——需要时以「会话最后一条事件 ts」近似；当前唯一消费方是裁决 B 的 containment。
- **混合版本兼容（评审补充，已验证）**：**部署顺序无强约束，双向成立**。① 新 server + 现 lvdata → session.json 无 `status` → lvdata 不判 finalized → 不写 day_force，优雅降级；② 旧 server + 新 lvdata → finalize 信号静默消失（新读侧对 status/finalized_at 照常摄入但不消费——不做 finalize 转换、不写 day_force），收尾日由 debounce 兜底，与全新链路行为一致。
- `recording_sessions.status` 由兜底 `'active'` 变为 `''`（首行 upsert 后）——全仓库无读取方，但未来 SQL 过滤该列会静默不命中；`ddl.py` 加列注释。
- `migrate()` DROP 仅清理存量 dev 库孤儿表；不执行也无读取方。
- 顺带收益：session.json 行成为每个 session 最早且永不变动的登记行，lvdata 同步必先见它，`_ENSURE_SESSION_SQL` 兜底路径几乎不再触发（FK 顺序问题天然消解）。

## 6. 实施顺序

A（写侧，含 2a「start 幂等首写」）→ B/C（读侧与 day_force）→ D（测试）→ E（文档）→ 部署真机验证（与 WS4.2 合并）。
