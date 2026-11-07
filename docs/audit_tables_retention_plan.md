# 运行审计表清理方案 — mcp_pipeline_runs / analysis_runs

> 状态：**待 review**（四稿；实施前等本稿过审）。
> **2026-09-28 注（I-4）**：本文以 `get_status` 指代运维状态接口——该 tool 已于 2026-09-27 迁为
> REST `GET /api/status`（输出同构，`recent_runs`/`last_rebuild_at`/`lag_sessions` 字段不变）。
> 文中 `store.get_status` 的内部实现与口径讨论仍有效，仅"接口形态"表述过时。
> 修订（2026-09-19 四稿，实现审查）：水位写入改「**快照−1ms**」——闭合毫秒
> 粒度下「同毫秒（读取后）更新 == 水位 → strict `>` 判不出滞后」的确定性漏
> 索引边界（已复现 + 已加回归测试）；声称覆盖「≤ 快照−1ms」（必然先于快照
> 存在、读取必见），读比较不动；代价 = 同毫秒已索引事件多发一次重建（保守）。
> 修订（2026-09-19 三稿）：采纳 review 建议——force/CLI 全量重建同事务清会话行
> （§2.2），冗余重建在写侧消除；§2.3 明确读比较不动、拒绝理由改写为针对 MAX
> 变体本身。
> 修订（2026-09-19 二稿，并入 review 意见）：§2.2 两条规则理由收敛为不变式
> 表述；§2.4 补回填残留语义；§1 补 analysis_runs 手工工具读者；§2.5 清理 SQL
> 的 running 护栏改独立析取（一稿写法下 `zombie_cutoff` 是死分支）、僵尸来源
> 补 stage 超时、补孤儿清理开关与 NULL 龄防御；§4 补僵尸行测试构造约束；§5 补
> force+target 误标修正。
> 已拍板（2026-09-19）：① 方案 B——水位独立表 + 审计表纯按龄清理；
> ② 保留期 `mcp_pipeline_runs` **14 天** / `analysis_runs` 30 天；③ 清理载体 =
> **进程内定时**（不做 CLI）；④ inventory 每轮双跑不动；⑤ CLI `lvdata-mcp reindex`
> 也写 `index_state['']` 水位（与向量侧 `vec_state` 的既有纪律对齐，消除手动
> 重建后的重复自动重建）。
> 范围：lvdata-mcp。不动 events / search_docs / vec / day_summary 等数据表与 stage 语义。
> 依据：`ddl.py` / `pipeline.py` / `index.py` / `watcher.py` / `store.py` /
> `analysis/common.py`；实测数据来自 dev 库 `/opt/lvdata/lvdata_inventory.db`
> （2026-09-15 06:46 ~ 09-19 07:04，4 天）。

## 1. 背景与问题（实测）

| 表 | 行数 | 增速 | 空间 | 写入方 | 读者 |
|---|---|---|---|---|---|
| `mcp_pipeline_runs` | 28,348 | ~7,087 行/天（≈0.54 MB/天） | 2.15 MB（主库 ~3 MB 的 **70%**） | 每轮 poll 每 stage 一行（`pipeline.py:166/174`） | `index.lag_sessions`（水位）、`get_status`（recent_runs 最近 10 行 + last_rebuild_at） |
| `analysis_runs` | 383 | ~96 行/天 | 45 KB | 每次 LLM 调用一行（`analysis/common.py:85`，含 model/usage/latency/error） | 无生产读者；手工工具 `tools/data-analysis/compare_omni_audio.py:76` 读取（取每 file 最新 ok 行——30 天窗口对久未重跑的文件无参照行，影响小） |

问题不是"删多少"，而是：**reindex 的水位借宿在审计表里**——`lag_sessions`
（`index.py:114`）取 `stage='reindex' AND status='ok'` 行的
`MAX(finished_at)`（per target + 全局两组合并）。审计表因此永远清不空，
"哪几行不能删"成为隐性不变式。本方案把水位抽成专用状态表，审计表退化为
纯审计、一条按龄 DELETE 即可（与项目既有惯例一致：`day_scan_state` /
`vec_state` / `flush_state` / `recording_files_sync_state` 均为专用状态表）。

## 2. 设计（方案 B）

### 2.1 新增 `index_state` 表（ddl §5.10）

```sql
CREATE TABLE IF NOT EXISTS index_state (
    target          TEXT PRIMARY KEY,   -- rsession_id；'' = 全局（全量重建）
    last_reindex_at TEXT NOT NULL       -- 成功重建水位（快照语义见 2.2，§5.1 时间戳规范）
);
```

- 全局键用 `''` 而非 NULL：SQLite 非 INTEGER 主键允许 NULL 且 UNIQUE 视多个 NULL
  互不相同（可插多行）——显式哨兵值规避。
- 加入 `V2_DDL`；查询按主键，无需额外索引。

### 2.2 写路径（唯一写入点 = `pipeline._reindex`，成功才写）

- `force`（全量）→ 写 `target=''`，**并同事务清会话行**（`DELETE FROM index_state
  WHERE target != ''`）；增量（`target=None`）→ 为本次实际重建的每个会话写各自
  行；`target=session` → 写该会话行。空跑（无滞后会话）不写、不推进。
  清会话行的理由：旧行 (s, t_s) 的覆盖 ⊆ 本轮全量快照（读得晚且读全），
  `'' = T0_force` 对每个会话都是严格更准的水位——删行只去冗余、不丢覆盖
  （"上次增量重建时刻"的审计信息不在状态表义务内）。不清的话，force/CLI 之后
  下一轮增量会把"自各会话上次重建以来有活动"的会话成批重复重建——换形态的
  "CLI 重复重建"，与 §5 价值相抵。
- **不变式（实现者的心智模型）**：写出的任何水位 W 必须 ≤ **该水位声称覆盖的
  所有读取时刻**；`''` 只由全量重建写。由此得两条实现规则：
  1. **增量路径绝不写 `''`**：`''` 语义 = "一次全量重建已覆盖到 T0"；增量只
     重建滞后会话，写 `''` 会破坏该不变式——未重建会话的覆盖只能靠旧水位传递，
     而升级后回填的旧值是完成时刻语义、传递性不可靠（见 2.4）。迁移陷阱：旧
     实现里增量运行写的正是 `target IS NULL` 行（今天的全局水位），照搬即错。
  2. **T0 必须取在 `_reindex` 入口**（先于 `lag_sessions` 取件，也先于任何
     events 读取）：名单内会话若 T0 取在其读取之后（如取"完成时刻"），
     (read, T0] 间更新的事件不被索引却 ≤ 水位 → 永久漏索引。
- **快照语义**：水位 = T0（读取之前取定，与 day_summary `generated_at` 同款原则
  §5.4/§6.5），不是重建完成时刻。这顺带修复一个既有竞态：`rebuild_session`
  先读 events（`index.py:95`）后写索引，期间被重分析更新的 event 会落进
  "完成时刻"水位内 → 永久漏索引；T0 语义下该 event > 水位 → 下轮重判滞后 →
  自愈。代价方向保守（可能多发一次重建，不漏）。
  **写入值 = T0−1ms**（四稿，实现审查补充）：毫秒粒度 + strict `>` 下，与 T0
  **同毫秒**（读取之后）到达的更新 updated_at == 水位 → 判不出滞后 → 仍会
  永久漏（确定性复现：`test_watermark_same_ms_boundary_self_heals`）；减 1ms
  后声称覆盖「updated_at ≤ T0−1ms」——必然先于快照存在、读取必见，语义严格
  成立；同毫秒更新 > 水位 → 下轮自愈。代价 = 同毫秒且已索引的事件多发一次
  重建（保守）。
- 事务：水位写入与 FTS 写入同一 `with conn:` 块提交；force 的清会话行与 `''`
  写入同事务（同生共死，不会出现"水位已推进但会话行未清"或反之）。`rebuild_all`
  内部自带 `conn.commit()`（`index.py:141`），崩在两次 commit 之间时水位未推进
  → 下轮重来（安全方向）。失败/异常不写 → 水位不动（与 `flush_state`
  "组内全部成功才推进"同纪律）。
- CLI `main.py run_reindex`（`main.py:90`，全量重建）同样写 `''` 水位并清会话行
  （与 force 同）：否则 CLI 重建过的会话会被 stage 判滞后并再重建一遍（现状浪费；向量侧的
  `vec.rebuild_session` 早已写 `vec_state`、无此问题——FTS 侧补上后两索引
  水位纪律一致）。

### 2.3 读路径

- `index.lag_sessions`：会话 `MAX(events.updated_at)` vs
  `COALESCE(index_state[rsession], index_state[''])`。**读比较逐字保持 COALESCE
  不变**。全量重建后"旧会话行盖过新全局水位 → 多发一次重建"的冗余，在**写侧**
  消除（force/CLI 同事务清会话行，2.2）。改 MAX（读侧取新者）是另一条路：会
  引入读侧语义变化并外溢到 get_status 口径；写侧清理已覆盖其收益，不需要。
- `store.get_status`：`last_rebuild_at` = `MAX(last_reindex_at) FROM index_state`；
  `lag_sessions` 复用同一函数（单一出处不变）；表缺失（老库只读打开）仍
  try/except 不报滞后。
- 空 `index_state`（全新库）= 全部会话滞后 = 现状首轮全量重建行为，不变。
- **两处语义变化（方向均保守，需在 S5 文档同步写明）**：
  1. 全局行：旧语义 = 每轮增量运行都会推进（增量写 `target IS NULL` 行）；新语义
     = 仅全量重建（force / CLI）推进 → 全局行只会更旧 → 判定只会更保守。
  2. `last_rebuild_at`：旧 = 最后一次 reindex 运行成功时刻（空跑也推进）；新 =
     最后一次**实际重建**时刻。消费方仅 lvdata-mcp `get_status`（bff 不读，
     已核实），文档描述为"数据有多新"指标，语义变化后可读性不降。

### 2.4 回填（存量库启动一次，幂等）

`ensure_schema` 内新增 `_backfill_index_state(conn)`：仅当 `index_state` 为空、
且 `mcp_pipeline_runs` 存在时执行

```sql
INSERT OR IGNORE INTO index_state (target, last_reindex_at)
SELECT COALESCE(target, ''), MAX(finished_at)
FROM mcp_pipeline_runs
WHERE stage='reindex' AND status='ok' AND finished_at IS NOT NULL
GROUP BY COALESCE(target, '');
```

已在表内的行不覆盖（`INSERT OR IGNORE`）；`finished_at IS NOT NULL` 是防御
（ok 行的 finished_at 理论恒非空，但 `NOT NULL` 列撞 NULL 会整体回滚）。
漏/未回填的后果 = 首次判全量滞后一次（自愈）。回填属可选优化——收益仅为省
一次本地、幂等的 FTS 全量重建；本稿保留，但认知上它是可去掉的复杂度。
**语义残留（接受，需知悉）**：回填值取自旧表 `finished_at`（完成时刻语义，非
T0）——升级前最后一轮 reindex 的读-写竞态窗口（会话 read → run finish）内更新
的 event 仍可能被吞，待该会话下次 event 更新自愈。T0 修复只对升级后的重建
生效，此残留是一次性过渡态。
**时序约束**：回填在 `ensure_schema` 内 → 天然先于 watcher 首轮清理；实现时
不得把回填挪到清理之后（否则审计行可能已被删，回填出空水位）。

### 2.5 清理（进程内定时）

- watcher 新增第三个后台任务 `_prune_loop`（对齐 `_poll_loop`/`_work_loop` 的
  `start`/`stop`/`_task_done` 结构），周期 `audit_prune_interval_s`（默认 86400s），
  **启动后立即跑一次**（存量仅 4 天数据、全在 14 天窗口内 → 首轮实际删 0 行，
  属预期；验收需构造窗口外老行，见 §4）。
- 每轮单事务执行：

```sql
-- 14 天；running 行另按僵尸窗口裁——独立析取，两条件不得合并（见下）
DELETE FROM mcp_pipeline_runs
WHERE (status != 'running' AND started_at < :cutoff_runs)
   OR (status = 'running' AND started_at < :zombie_cutoff);
-- 30 天；started_at 可空（ddl §5.3），NULL 行无法判龄、不清理（record_run 恒写值）
DELETE FROM analysis_runs
WHERE started_at IS NOT NULL AND started_at < :cutoff_analysis;
DELETE FROM index_state
WHERE target != '' AND target NOT IN (SELECT DISTINCT rsession_id FROM events);           -- 孤儿水位
```

- `running` 行护栏**不能只写 `status != 'running'`**：僵尸 running 行没有任何
  收尾方（`_record_end` 不再执行），会被永久保留，且 `get_status.recent_runs`
  永远显示 running。僵尸来源**不止崩溃**——stage 超时同样产生：`wait_for` 取消
  时向 `run_stage` 注入 `CancelledError`（BaseException，`except Exception` 不
  捕获）→ `_record_end` 不执行（`pipeline.py:95`）。正确形态 = 独立析取：非
  running 行按保留期删；running 行按 `:zombie_cutoff` =
  `now - max(2×stage_timeout_s, 3600s)` 删（真实 run 超不过 stage 超时；
  `stage_timeout_s=0` 禁用超时时退化为 3600s，真有 >1h 的 run 仅丢审计行、
  无功能影响）。**两条件不得合并**：写成 `started_at < :cutoff_runs AND (...)`
  时 `started_at < :zombie_cutoff` 恒真（14 天前必早于 1 小时前）→ 僵尸行要活到
  14 天才被删，护栏成死分支（一稿缺陷）。
- `analysis_runs` 无此问题：`record_run` 在调用**结束后**一次性 INSERT（无
  running 中间态），按龄删即可。
- 删除量级：稳态每轮 ≈ 1 天行数（~7k 行 / ~0.5 MB），单条 DELETE 数十 ms，
  无需分片；WAL 下短事务不阻塞 watcher 写与 bff 读。
- 不做 VACUUM：DELETE 空间进 freelist 被新行复用，文件大小稳定在上限附近；
  `auto_vacuum` 需建库期设定、不可事后改。
- 保留期 0 = 关闭该项清理；**`audit_retention_days=0` 同时跳过孤儿水位清理**
  （"0 = 本方案全部 DELETE 停做"语义，避免"停删"语境下仍删表的意外），
  `analysis_runs_retention_days=0` 只跳过 analysis_runs；计时器在内存，崩溃/
  重启后启动即清一次，幂等无害。
- 健壮性：三类 DELETE 同事务；清理循环外层 try/except（单次失败不让任务死掉，
  下轮重试）；连接使用与其他写入方一致的 PRAGMA（WAL + busy_timeout），
  `_prune_loop` 纳入 `start`/`stop`/`_task_done` 三处（对齐现有两任务）。

### 2.6 配置（`watcher` 段，默认值即拍板值）

| 键 | 默认 | 说明 |
|---|---|---|
| `audit_retention_days` | 14 | `mcp_pipeline_runs` 保留天数；0 = 关闭 |
| `analysis_runs_retention_days` | 30 | `analysis_runs` 保留天数；0 = 关闭 |
| `audit_prune_interval_s` | 86400 | 清理周期（秒） |

同步更新 `configs/dev/lvdata_mcp.yaml` 注释示例。窗口可随时用既有 env override
调整（`LVDATA_MCP__WATCHER__AUDIT_RETENTION_DAYS`），不必改配置文件。
14 天稳态 ≈ 10 万行 / 7.6 MB（原 7 天 ≈ 5 万行 / 3.8 MB）。

## 3. 工作分解

| # | 任务 | 文件 | 说明 |
|---|---|---|---|
| S1 | `index_state` DDL + 回填 | `ddl.py` | 表入 `V2_DDL`；`_backfill_index_state` 幂等；`test_ddl` 期望表集合更新 |
| S2 | 水位写路径 | `pipeline.py:144`、`main.py:90` | force→`''`+清会话行 / 增量→逐会话 / target→单会话；CLI 全量→`''`+清会话行；T0 先于读取；与 FTS 同 `with conn:` 提交 |
| S3 | 水位读路径 | `index.py:114`、`store.py:435` | `lag_sessions` 切 `index_state`（COALESCE 等价改写）；`last_rebuild_at` 同源 |
| S4 | 清理任务 | `watcher.py`、`config.py`、`configs/dev/lvdata_mcp.yaml` | `_prune_loop` 第三任务；3 个配置键 |
| S5 | 文档同步 | `docs/data/lvdata_mcp_service_v2.md` | §5 新增 `index_state`（`''` 键 / 快照水位 / 回填三要点）；§6.6 补水位一句；`mcp_pipeline_runs` 加保留期注记 |
| S6 | 测试 | `tools/lvdata-mcp/tests/` | 见 §4 |

## 4. 测试与验收

- 单测（新增/改写）：
  - **lag 正确性**：三种形态（仅全局行 / 仅会话行 / 两者都有）输出与旧实现一致；
    空表 → 全部滞后；表缺失不报滞后（改写 `test_tools.py:201` 夹具为 `index_state`）。
  - **写路径**：force / 增量 / target 三形态各写对行；CLI `reindex` 写 `''`；
    force / CLI 同事务清会话行（跑完 `index_state` 仅剩 `''`，会话比对落空取新
    `''`、不误判滞后）；增量**不**写 `''`、**不**清会话行；空跑不写；水位取 T0
    （取件之前）、写入值 = T0−1ms；同毫秒边界（冻结时钟确定性复现）下轮判
    滞后自愈；失败不写。
  - **回填**：构造带 `mcp_pipeline_runs` 的旧库 → `index_state` = 每组最新 ok 水位；
    已有行不被覆盖（幂等）；组内 finished_at 全 NULL 时不写坏行。
  - **清理**：幂等（连跑两次 = 一次）；在途 `running` 行保留、僵尸 `running` 行被删
    （**僵尸行须用保留期内的构造**——>14 天的行会因保留期条件被删、测不到僵尸逻辑）；
    cutoff 边界（等于/早于/晚于）；`0` = 关闭（含孤儿清理同步跳过）；`analysis_runs`
    NULL `started_at` 行不被清；孤儿水位行被清且 `''` 行保留；单次失败不杀任务。
- dev 验收：升级启动（回填 + 首轮清理）后 `get_status.lag_sessions` 与清理前一致；
  随后一轮 reindex 不出现全量重建。清理效果需**构造窗口外老行**验证（现存数据仅
  4 天，14 天窗口内首轮删 0 行——不是故障）：临时 `audit_retention_days=1` 或
  造几行 `started_at` 为 30 天前的审计行，断言被删、窗口内保留。

## 5. 记录与风险

- **被否方案 A**（按龄清理 + 用 `GROUP BY target` 保护审计表内的水位行）：审计与
  正确性耦合、表永不清空、"哪几行不能删"是隐性不变式，与项目专用状态表惯例不一致。
- **风险与回滚**：唯一语义敏感点是 lag 判定，以"清理/切换前后 lag 输出一致"为
  回归断言；回滚 = `lag_sessions` 改读回 `mcp_pipeline_runs`（`index_state`
  保留不删，无数据损失）。
- **原遗留已一并修复**：`rebuild_session` 读-写竞态（旧 §8）由 2.2 的 T0 快照水位
  消除（仅对升级后的重建生效；回填残留见 2.4）。
- **顺带修正（实现时知悉）**：旧实现 `force=True` + 指定 target 时实际执行的是
  `rebuild_all`（`pipeline.py:149` 忽略 target），运行行却记在 target 名下——
  一次全量重建被误记成单会话水位；新写路径 force 一律记 `''`，与真实覆盖面一致。
- **已拍板不改**：inventory stage 每轮双跑（`watcher.py:105` 显式 + `ROUND_STAGES`
  首项，审计行 2×、cs 同步空跑一次）保持现状。
- **并发写竞态（接受）**：CLI 与 watcher 可能同时重建（两进程写 search_docs /
  `index_state`）；水位方向保守（旧 T0 覆盖新 T0 → 多发一次重建），无漏索引。
- **邻域遗留（不在本方案）**：`search_docs` 没有 vec 那样的 `sweep_orphans`
  ——events 被删的会话不再进滞后名单，其 FTS 文档会永久残留；是否补扫另议。
- **优先级**：不清理仅约 190 MB/年，不属紧急事故；本方案的额外价值在解耦
  （审计表可随时裁剪）+ 修复读-写竞态 + 消除 CLI 重复重建，建议按 P2 排期。
