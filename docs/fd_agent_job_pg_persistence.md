# Agent Job PG 持久化 — Job 生命周期镜像存储（评审合并稿，定稿）

> **取代公告（2026-08）**：本文件描述的 PG 镜像写入已被
> [`chatserver_sqlite_store.md`](chatserver_sqlite_store.md) 取代——PG 存储模块
> 保留未用，agent_jobs 由 Agent Relay 直写 lvdata SQLite（`agent_relay/job_persistence.py`
> JobLvdataWriter），chat server 持久化收敛为 SQLite 三表。本文件仅作历史记录。

> **状态**：定稿（评审合并版）
> **日期**：2026-08-17
> **取代**：[`fd_agent_job_pg_store.md`](fd_agent_job_pg_store.md)（下称"原 A 稿"）、
> [`fd_agent_job_persistence.md`](fd_agent_job_persistence.md)（下称"原 B 稿"）
> **前置文档**：[`fd_agent_relay_service_v2.md`](fd_agent_relay_service_v2.md)（Job 生命周期与
> JobStore 语义）、[`fd_job_status.md`](fd_job_status.md)（running 快照推送）
> **范围**：`services/agent_relay/job_store.py`（钩子）、
> `services/agent_relay/job_persistence.py`（新，JobPgWriter）、
> `services/shared/models/orm.py`（新 ORM）、
> `services/shared/persistence/repositories/agent_jobs.py`（新）、
> `services/migrations/versions/0003_agent_jobs.py`（新）、
> `services/shared/observability/metrics.py` + `services/agent_relay/metrics.py`（指标）、
> `services/agent_relay/config.py` + `services/app/application.py`（接线）

**合并要点**（相对两原稿的修订，逐条对应评审结论）：

1. **修正原 B 稿的阻塞级错误**：`_finish()` 不为 failed job 写 `completed_at`
   （job_store.py:192-209），`finished_at` 直接映射 `completed_at` 会让 failed 行恒为
   NULL，失败分析查询全部落空。采用派生规则（D6）。
2. **upsert 增加 `updated_at` 单调守卫**（原 A 稿方案，D3），兜住一切乱序写入方，
   包括启动 reconcile 与在线迁移的竞态窗口。
3. **写入机制采用原 B 稿**：有界队列 + 单 writer task + 启动 reconcile + 专用指标 +
   停机排空（`persist_async` 的 200 上限是软上限，PG 挂起时在途任务无界且无 drop 指标）。
4. **接线点固定在 `job_store` 创建后、adapter connect 前**（消除 reconcile 竞态）；
   **shutdown 顺序修正为"先停迁移来源，后停 writer"**（原 B 稿顺序相反且论证有误）。
5. Repository 显式 `commit()`（两原稿伪码均缺：`async with session` 退出即回滚）。
6. 索引取两稿并集；主键采用 BigInteger 代理键 + `job_key` unique（对齐 0001 惯例，D8）。

---

## 1. 背景与需求

### 1.1 现状行为

Hermes 后台 job 的全部状态只存在 **Redis JobStore**（`services/agent_relay/job_store.py`），
所有 key 带 **48h TTL**（`JOB_TTL_S = 2 * 24 * 3600`，job_store.py:26）：

- 每个 job 一个 hash（`livachat:agent:hermes:job:{job_key}`）+ session/device 两个 set
  索引 + 全局 `inflight` set，均 48h TTL。Redis 是**运行时状态**：支撑 job_status 快照
  推送、设备重连补投（48h 窗口）、watchdog 收敛；**不是**可查询的持久存储。
- job 终态随 TTL 过期后**彻底消失**：`result_text`、`fail_reason`、时长等无法回查，
  无法做失败率、耗时分布、按设备聚合等离线分析。
- PG 已有 `sessions` / `messages` / `recording_sessions` 等分析表，唯独缺 agent job 维度。

### 1.2 需求

| # | 需求 | 概述 |
|---|------|------|
| R1 | **全生命周期落库** | requested → running → done/delivered/failed 每次转换写入 PG `agent_jobs`（upsert，单行追踪一个 job） |
| R2 | **字段覆盖** | job id、started time、finished time、status（含 running/done/failed）、result 或 failed reason、device_uuid |
| R3 | **不影响实时链路** | PG 写入 fire-and-forget；任何 PG 故障不得影响 Redis JobStore 的投递/补投语义 |
| R4 | **可分析** | 按 device_uuid / status / 时间范围查询，支撑失败分析与数据统计（示例 SQL 见 §6） |

### 1.3 非目标（本期不做）

- **不改 Redis 运行时语义**：投递、补投、job_status 通知、watchdog 收敛仍以 Redis 为准，
  PG 只做镜像，不参与任何判定。
- **历史回填**：Redis 只有 48h 存量，不做全量回填；启动 reconcile（D5）顺带覆盖活跃 job。
- **查询 API / Admin 后台 / 数据分析管线**：本期只落库 + 示例 SQL；聚合报表、
  `tools/data-analysis/`（LvData SQLite 管线）接入留待后续。
- **精确一次语义**：PG 是**尽力而为的分析镜像**，一致性边界见 §5。
- **`devices.id` / `users.id` 外键解析**：只存 `device_uuid` 原文（跟随
  `recording_sessions.device_uuid` 现状，0002 迁移），关联由后续数据管线处理。
- **分区分表**：量级估算见 §8，本期单表 + 索引足够。

---

## 2. 调研结论（源码级，已逐条验证）

### 2.1 Job 状态机与转换点（全部收敛于 `JobStore`）

```
requested ──mark_running──▶ running ──mark_done──▶ done ──mark_delivered──▶ delivered
    │                        │  ▲
    │                        ▼  │
    └──mark_failed──▶ failed ──┘
        (watchdog: orphaned)   (watchdog: timeout / session_lost / rpc_error)
```

所有状态转换都经过 `JobStore` 的 5 个公开方法（`_finish` 收敛全部失败路径），
**没有任何绕过 JobStore 的写入路径**：

| 转换 | JobStore 方法 | 触发点（已验证） | 写入字段 |
|------|--------------|------------------|---------|
| → `requested` | `create()`（:140-170） | `hermes.py:1211` `_handle_job_request` | `created_at`, `updated_at` |
| `requested→running` | `mark_running()`（:172-190） | `_dispatch_job_session`（bind 后、submit 前）；job recovery | `started_at`, `hermes_task_id`, `updated_at` |
| `running→done` | `mark_done()`（:211-240） | `_handle_job_message_complete`（正常完成带 result）；`_resume_job_session`（transcript 恢复） | `result_text`（截断 16KB）, `completed_at`, `updated_at` |
| `done→delivered` | `mark_delivered()`（:242-260） | `delivery.py`（投递/补投成功）；live/recovery 的 NOOP/error/空输出（直接视为已消费） | `delivered_at`, `updated_at` |
| active→`failed` | `_finish()`（:192-209，经 `mark_failed` / `mark_orphaned`） | dispatch/gateway 失败（`create_error`/`submit_error`/`session_cap`/`rpc_error`）；`cancel_job`/watchdog `interrupt`；`_handle_job_error`；job recovery（`session_lost`/`orphaned`）；`scan_stale()`（watchdog：running 超 `background_timeout_s`→`timeout`、requested 超 60s→`orphaned`） | `fail_reason`, `updated_at` |

关键事实：

1. **`fail_reason` 词表**（job_store.py:50）：`timeout | orphaned | rpc_error | session_lost`。
2. **failed 没有 `completed_at`**：`_finish()` 只写 `status / fail_reason / updated_at`
   （job_store.py:192-209）。⚠️ **PG 的 `finished_at` 对 failed job 必须取失败转换时刻的
   `updated_at`，直接映射 `completed_at` 会得到 NULL**（原 B 稿在此出错，见 D6）。
3. **NOOP / `error:` 完成路径**（hermes.py:878-895）：`mark_done("")` + `mark_delivered()`
   连续调用，result 为空文本——PG 会如实看到 `done→delivered` 且 `result_text` 为空。
4. **配额拒绝的 job 不入库**（hermes.py:1198-1209）：per-device / global in-flight 超限在
   `create()` 之前直接 return，不产生 job 记录（它们是拒绝事件，不是 job，已有
   `inc_job_event("rejected")` metric 覆盖）。
5. **状态回退已被 Redis CAS 杜绝**（`_cas_transition`，:411-444，WATCH/MULTI）：
   failed→done 等逆向转换在源头不可能发生；每次转换都刷新 `updated_at`
   （relay 单进程 `time.time()`，作为 D3 单调守卫的比较基准）。
6. **全部转换方法在 CAS 成功后已 fetch 最新 `JobRecord`**（mark_running :187、
   mark_done :228、mark_delivered :257、_finish :206）——钩子复用该 record，零额外读。

### 2.2 `JobRecord` 字段与值域（job_store.py:42-56）

| 字段 | 含义 | 值域/约束 |
|------|------|-----------|
| `job_key` | relay 生成的幂等键 | `uuid.uuid4().hex` |
| `hermes_task_id` | `prompt.background` 返回的关联键 | 空 = 未进入 running |
| `session_id` | 创建时的 livachat chat session（仅追溯） | `create()` 必传，恒非空 |
| `device_id` | 投递与索引主键 | **= 客户端 UUID v4（device_uuid）**，见 §2.3 |
| `status` | requested/running/done/delivered/failed | 与 §2.1 一致 |
| `summary` | 主 agent 一行摘要 | 硬上界 256 字符（`JOB_SUMMARY_MAX_CHARS`，:31），存储层截断 |
| `fail_reason` | timeout/orphaned/rpc_error/session_lost | 仅 failed 非空 |
| `result_text` | job 成品文案 | 截断 `max_result_bytes`=16384（:75） |
| `completed_at` | 完成时间（补投排序键） | **仅 done 写入**；failed/活跃 job 恒 0 |
| `delivered_at` | 投递成功时间 | 0 = 未投递 |
| `started_at` | 进入 running 的时刻 | 0 = 未进入（如 requested 被 watchdog 直接收敛） |
| `created_at` / `updated_at` | 创建/最近更新 | epoch 秒 float；每次转换刷新 `updated_at` |

### 2.3 `device_id` 即 `device_uuid`

`services/gateway/endpoint.py:32-52`：WS 握手携带 `device_id` query 参数，注释明确
"client UUID v4"，`uuid.UUID(device_id, version=4)` 校验通过才放行（:48-52）。该值贯穿
gateway → session → agent events → JobStore。DB 侧 `devices.device_uuid`（0001，
`unique=True`）与 `recording_sessions.device_uuid`（0002，Text 无 FK）存同一值。
→ 直接落 `device_uuid` 列，无需映射。

### 2.4 PG 接入的既有模式（可复用资产）

| 设施 | 位置 | 事实 |
|------|------|------|
| 异步引擎/会话工厂 | `shared/persistence/engine.py` | SQLAlchemy 2.x + asyncpg；`create_engine(cfg.postgres.dsn)` → `create_session_factory` |
| ORM Base | `shared/models/orm.py` | 声明式 `Mapped`/`mapped_column`；alembic `env.py` 以 `Base.metadata` 为 target；分析表惯例为 `BIGINT id` 代理键 + Text 业务键 unique（sessions/messages） |
| 迁移 | `services/migrations/versions/` | 手写 alembic，序号递增（0001、0002 已存在，**下一个为 0003**）；`DateTime(timezone=True)` + `server_default=now()`、`idx_` 索引前缀、`uq_` 约束前缀 |
| Repository | `shared/persistence/repositories/` | 构造函数收 `AsyncSession`；upsert 用 `insert(...).on_conflict_do_nothing/update`（recording.py:49-81）；**写方法内显式 `await session.commit()`**（recording.py:66/92/106——`async with session` 退出 close 会回滚未提交事务） |
| 非阻塞写先例 | `shared/persistence/helpers.py::persist_async` | fire-and-forget、异常只 log + metric。但其"有界 200"是**软上限**：满员仅剔除已完成 task（helpers.py:51-55），PG 挂起时在途任务照样无界增长，且无 drop 指标——本特性采用自有队列（D4） |
| 应用接线 | `services/app/application.py` | `pg_session_factory` 在 lifespan :43-49 创建，PG 不可用时为 `None` 仅告警；relay 块 :121 起，`job_store` 创建于 :150-154、adapter connect :162-174，`set_change_listener` 先例 :199；RecordingService 注入 `pg_session_factory` 先例 :243-248 |
| 配置 | `agent_relay/config.py` | `AgentRelayConfig`（pydantic-settings，env 前缀 `LIVACHAT__AGENT_RELAY__`，`env_nested_delimiter="__"`）；开关先例 `job_status_enabled: bool = True`（:113）；嵌套子配置先例 `AdaptersConfig` / `ProactiveConfig` |
| 指标 | `shared/observability/metrics.py` + `agent_relay/metrics.py` | registry `_get_or_create` 计数器模式（如 `agent_job_events_total`，metrics.py:204-209）+ relay 侧薄封装（`inc_job_event` 等） |

### 2.5 现成的钩子先例

`JobStore.set_change_listener` / `_notify_change`（job_store.py:101-136）就是"转换成功后
fire-and-forget 通知"的既有模式（驱动 job_status 推送，接线于 application.py:199）。
PG 历史写入复用同一模式并列挂载，与 `_notify_change` 互不影响；区别是携带完整
`JobRecord` 而非仅 `device_id`（§2.1-6 使这零成本）。

### 2.6 需求 → 差距映射

| 需求 | 现状差距 | 落点 |
|------|---------|------|
| 存 failed + done（含 running 中间态） | Redis 过期即丢 | 五态全量镜像（D1），`agent_jobs` 表（§4.1） |
| job id | `job_key` 已有 | 列 `job_key`，unique（upsert 冲突键） |
| started / finished time | `started_at` 已有；**failed 无 `completed_at`** | `started_at` + 派生 `finished_at`（D6） |
| status | 五态状态机 | 列 `status` 全值域 |
| result / failed reason | `result_text`（≤16KB）/ `fail_reason` | 两列，互斥出现，NULL = 无 |
| device_uuid | = `device_id`（§2.3） | 列 `device_uuid`，索引 |
| 查询/分析 | 无 | 索引（§4.1）+ 示例 SQL（§6） |

---

## 3. 总体设计

### 3.1 核心决策（定稿）

| # | 决策 | 备选 | 理由 |
|---|------|------|------|
| D1 | **PG 是异步镜像，Redis 仍是运行时唯一事实源**；五态全量镜像（含 requested/delivered） | 只存终态；PG 参与投递判定 | R3 要求 PG 故障不影响实时链路；`requested` 覆盖 rpc_error 前窗口、`delivered` 区分"完成"与"送达"，如实映射成本仅列宽 |
| D2 | **单漏斗同步钩子** `set_record_listener`：5 个转换方法成功分支内以最新 `JobRecord` 直接同步回调（O(1) 入队，不创建 task） | adapter / delivery / watchdog 各调用点分别写；仿 `_notify_change` 每次 create_task | 全部转换（含 watchdog、`session_lost`、ops 清理）收敛于 JobStore 5 方法（§2.1），单点挂载零遗漏、调用方零改动；回调仅 `put_nowait`，同步接口比每次 `asyncio.create_task` 更轻，契约同为 fire-and-forget（异常只 log 不扩散） |
| D3 | **full-state upsert + `updated_at` 单调守卫**：`ON CONFLICT (job_key) DO UPDATE ... WHERE agent_jobs.updated_at <= excluded.updated_at` | 追加事件表；无守卫裸 upsert | 一行一 job 最新状态直接满足查询诉求；守卫兜住**一切**乱序写入方（队列外调度、reconcile 竞态、未来多实例），旧转换永不覆盖新状态；`updated_at` 源自由 CAS 保证顺序的单进程时钟（§2.1-5） |
| D4 | **有界队列 + 单 writer task**，best-effort 不阻塞热路径；队列满 → 丢弃 + `queue_dropped` 指标 | 复用 `persist_async`；迁移路径同步 await PG | `persist_async` 200 上限是软上限、PG 挂起时任务无界且无 drop 指标（§2.4）；单 writer 串行消费天然保序、背压明确、可停机排空；同步 await 违反 R3 |
| D5 | **启动 reconcile 活跃 job**：relay 启动时对 `list_active()` 全量 upsert；**接线点固定在 adapter connect 前** | 不 reconcile；reconcile 含终态 | 兜住「迁移后、PG 写入前」崩溃窗口与 PG 故障窗口内的 active job；接线时序保证 reconcile 期间无在线迁移（竞态消除），D3 守卫兜底残余窗口；终态 reconcile 需扫全部 device 索引，成本高收益低（边界见 §5） |
| D6 | **`finished_at` 派生**：`completed_at > 0` → 取 `completed_at`；`failed` → 取失败转换的 `updated_at`；其余 NULL | 改 JobStore 为 failed 补写 `completed_at`；直接映射 `completed_at` | Redis 侧 failed 无 `completed_at`（§2.1-2）——直接映射使 failed 行 `finished_at` 恒 NULL，§6 的失败分析查询全部落空（原 B 稿的阻塞级错误）；不动 Redis 既有语义与测试，派生规则集中在 writer 一处 |
| D7 | **无 TTL 永久保留**（本期显式决策，**上线前需合规/产品书面确认**） | PG 侧 TTL / 立即分区 | PG 是分析数据；量级估算（§8）前期单表足够；`result_text` 含用户内容，长期留存必须是有记录的决定而非默认；膨胀后演进：按 `created_at` 月分区（pg_partman）、`result_text` 外置对象存储 |
| D8 | **主键 `id` BigInteger 代理键 + `uq_agent_jobs_job_key` unique 约束** | `job_key` TEXT 直接 PK | 对齐 0001 分析表惯例（sessions/messages 均为代理键 + Text 业务键 unique）；`job_key` 仍是 upsert 冲突键与跨系统关联键（§8 血缘）；本表无被 FK 引用需求，两案皆可，取惯例一致性 |
| D9 | **不回填 Redis 存量终态、不接 LvData** | 启动全量回填；同步进 SQLite 管线 | Redis 仅 48h 窗口价值低，D5 已顺带覆盖存量 active；LvData 是独立 DB，后续按需消费本表 |

### 3.2 目标架构

```
                         ┌─────────────── Redis（运行时事实源，48h TTL）───────────────┐
                         │  JobStore hash + session/device/inflight 索引                │
                         │    │ CAS 迁移成功后（复用方法内已 fetch 的最新 record）       │
  hermes.py 调用方 ──▶ JobStore ──_notify_change(device_id)──▶ JobStatusNotifier        │
  (ask/watchdog/               │ _notify_record(JobRecord)   ← 新增，同步 O(1)          │
   complete/                   ▼                                                       │
   session_lost)         JobPgWriter（新，agent_relay/job_persistence.py）             │
                                 │ 有界 asyncio.Queue（默认 1024，满 → 丢弃 + 指标）   │
                                 ▼ 单 writer task（串行，best-effort）                 │
                         AgentJobsRepository（shared/persistence/repositories/）       │
                                 │ INSERT..ON CONFLICT (job_key) DO UPDATE             │
                                 │   WHERE agent_jobs.updated_at <= excluded.updated_at│
                                 ▼                                                     │
                         ┌───────────────── PostgreSQL ─────────────────┐              │
                         │  agent_jobs 表（无 TTL，索引见 §4.1）         │              │
                         └────────────── 查询 / 数据分析 ◀───────────────┘
```

- **Redis 仍是唯一权威**：投递、补投、watchdog 语义零改动；PG 只是镜像。
- **单点挂载**：钩子只加在 `JobStore` 内部 5 个转换方法里，adapter / delivery /
  watchdog 的调用方代码一行不改。
- **降级**：PG 不可用 / 开关关闭 → 不创建 writer、不挂 listener，`JobStore` 行为与
  现状完全一致。

### 3.3 关键时序

**正常完成（result_text 入库）：**

```
1. _handle_job_request → job_store.create()     → Redis requested → PG upsert(requested)
2. prompt.background RPC 返回 task_id
   → job_store.mark_running()                   → Redis running   → PG upsert(running, started_at)
3. background.complete → mark_done(text)        → Redis done      → PG upsert(done, result_text, finished_at=completed_at)
4. deliverer.deliver() 成功 → mark_delivered    → Redis delivered → PG upsert(delivered, delivered_at)
```

**失败（finished_at = 失败转换时刻，D6）：**

```
watchdog scan_stale / session_lost / rpc_error → mark_failed(reason)
  → Redis failed(fail_reason)                  → PG upsert(failed, fail_reason, finished_at=updated_at)
```

**启动 reconcile（D5）：**

```
relay 启动 →（adapter connect 前）list_active() → 逐个全量 upsert（不改 Redis 状态）
           → 启动 writer task → set_record_listener
```

---

## 4. 详细设计

### 4.1 迁移 `0003_agent_jobs.py`

新建 `services/migrations/versions/0003_agent_jobs.py`（沿用 0001/0002 风格，
`revision="0003"`，`down_revision="0002"`）：

```python
def upgrade() -> None:
    op.create_table(
        "agent_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_key", sa.Text(), nullable=False),            # relay uuid（upsert 冲突键、跨系统关联键）
        sa.Column("agent_name", sa.Text(), server_default="hermes", nullable=False),  # 预留多 adapter；当前恒 hermes
        sa.Column("device_uuid", sa.Text(), nullable=False),        # = relay device_id（客户端 UUID v4）
        sa.Column("session_id", sa.Text(), nullable=False),         # livachat session（追溯/关联）
        sa.Column("hermes_task_id", sa.Text(), nullable=True),      # prompt.background task id；NULL = 未 running
        sa.Column("status", sa.Text(), nullable=False),             # requested|running|done|delivered|failed
        sa.Column("summary", sa.Text(), server_default="", nullable=False),  # 一行摘要 ≤256 字符（源头已截断）
        sa.Column("result_text", sa.Text(), nullable=True),         # done 成品文案 ≤16KB；NULL = 无结果（NOOP/error 消费）
        sa.Column("fail_reason", sa.Text(), nullable=True),         # timeout|orphaned|rpc_error|session_lost
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),  # 进入 requested（= record.created_at）
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),     # 进入 running；NULL = 从未 running
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),    # 终态时刻（D6 派生）
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),   # 投递成功；NULL = 未投递
        # 行首次插入时间（PG 侧，DEFAULT now()；upsert 冲突时不得覆盖）：
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # = 源 record.updated_at（upsert 每次显式写入，是 D3 守卫的比较基准）：
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint("uq_agent_jobs_job_key", "agent_jobs", ["job_key"])
    op.create_index("idx_agent_jobs_device_time", "agent_jobs", ["device_uuid", "requested_at"])
    op.create_index("idx_agent_jobs_status_finished", "agent_jobs", ["status", "finished_at"])
    op.create_index("idx_agent_jobs_task", "agent_jobs", ["hermes_task_id"])
    op.create_index("idx_agent_jobs_session", "agent_jobs", ["session_id"])


def downgrade() -> None:
    op.drop_table("agent_jobs")
```

设计说明：

1. **`job_key` unique**：upsert 冲突键；跨系统关联键（补投 message_id 前缀
   `msg_agent_job_{job_key}`、job_status 载荷 `job_id` 都用它，§8 血缘）。
2. **索引**（两原稿并集，覆盖 §6 全部查询）：
   - `(device_uuid, requested_at)`：某设备按请求时间的 job 列表/筛选（`requested_at`
     恒非空，优于以可空的 `started_at` 排序）；
   - `(status, finished_at)`：按状态的时间窗聚合（成功率、失败原因分布、耗时统计）——
     failed 行 `finished_at` 非空（D6），此类查询对 failed 同样成立；
   - `(hermes_task_id)`：按 hermes 事件关联键反查（非 unique，仅索引）；
   - `(session_id)`：关联 `sessions` / `messages` 做会话级分析。
3. **不加 FK**：job 生命周期可跨 session 长期存续，session 可能已删、device 行创建时机
   不确定（与 `recording_sessions.device_uuid` 用 Text 无 FK 的做法一致，0002）；解耦
   写入路径。
4. **NULL vs 空串**：`result_text` / `fail_reason` / `hermes_task_id` / 时间列（除
   `requested_at`）允许 NULL，"无值"语义明确；`status='done' AND result_text IS NOT NULL`
   区分「有结果」与「NOOP/error 消费」（但 NOOP 与 error 之间不可区分，见 §9）。
   `summary` / `session_id` / `agent_name` 恒有值，`NOT NULL DEFAULT`。
5. **`created_at` 与 `updated_at` 语义分离**：`created_at` 是 PG 行首插时间（DEFAULT
   now()，不在 upsert 的 `set_` 中，冲突时保留）；`updated_at` 总是显式写入**源**
   `record.updated_at`，作为 D3 守卫的比较基准。
6. **`agent_name` 预留**：`JobRecord` 无此字段、JobStore key 前缀硬编码 `hermes`，
   writer 当前恒写 `'hermes'`；多 adapter 接入时该列直接可用。

### 4.2 ORM 模型 `AgentJob`

`services/shared/models/orm.py` 新增（与迁移逐列对应，沿用 `Mapped`/`mapped_column`
与 `__table_args__` 风格，参考 `Session` / `RecordingSession`）：

```python
class AgentJob(Base):
    __tablename__ = "agent_jobs"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    job_key: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, default="hermes")
    device_uuid: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    hermes_task_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    result_text: Mapped[str | None] = mapped_column(Text)
    fail_reason: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("job_key", name="uq_agent_jobs_job_key"),
        Index("idx_agent_jobs_device_time", "device_uuid", "requested_at"),
        Index("idx_agent_jobs_status_finished", "status", "finished_at"),
        Index("idx_agent_jobs_task", "hermes_task_id"),
        Index("idx_agent_jobs_session", "session_id"),
    )
```

### 4.3 `AgentJobsRepository`

新建 `services/shared/persistence/repositories/agent_jobs.py`（沿用 recording repo
风格；参照 recording 先例直接从模块路径 import，不改 `repositories/__init__.py`）：

```python
class AgentJobsRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, row: dict) -> None:
        """full-state upsert + 单调守卫（D3）：
        INSERT .. ON CONFLICT (job_key) DO UPDATE .. WHERE agent_jobs.updated_at <= excluded.updated_at
        row 为 JobPgWriter._to_row 产物（不含 id/created_at——created_at 由 DEFAULT
        now() 首插、冲突时保留）。守卫丢弃后到的旧转换写入。"""
        stmt = postgresql.insert(AgentJob).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["job_key"],
            set_={k: stmt.excluded[k] for k in row if k != "job_key"},
            where=(AgentJob.updated_at <= stmt.excluded.updated_at),
        )
        await self._session.execute(stmt)
        await self._session.commit()   # 必须显式提交：async with session 退出 close 即回滚
```

- **单实例写 PG 即可**：relay 与 gateway 同进程单实例部署；`ON CONFLICT` + 守卫在多
  实例下同样幂等抗乱序。
- 查询侧（`list_by_device` / `list_by_status_window` 等）本期不实现——分析师直接
  SQL（§6），需要时再加，避免无调用方的死代码。

### 4.4 `JobStore` 记录级钩子

`services/agent_relay/job_store.py` 新增（与 `set_change_listener` 并存，互不干扰）：

```python
def set_record_listener(self, cb: Callable[[JobRecord], None] | None) -> None:
    """迁移成功后的记录级钩子（D2）：create/mark_running/mark_done/mark_delivered/
    _finish 五入口，以迁移后的最新 JobRecord 同步回调。与 set_change_listener
    不同：携带完整 record，供 PG 镜像等持久化消费者使用。契约：fire-and-forget，
    cb 必须 O(1) 非阻塞；JobStore 侧 try/except 兜底，异常只 log 不扩散。"""
    self._record_listener = cb

def _notify_record(self, record: JobRecord | None) -> None:
    if self._record_listener is None or record is None:
        return
    try:
        self._record_listener(record)
    except Exception:
        logger.warning("job record listener raised", exc_info=True)
```

调用点（均为**转换成功后**，与 `_notify_change` 并列；复用方法内已 fetch 的 record）：

| 方法 | 位置 | 触发条件 |
|------|------|---------|
| `create()` | :168 `pipe.execute()` 后、返回前 | 总是 |
| `mark_running()` | :187-189 处 | `ok == 1`（已被 watchdog 收敛返回 False，不调） |
| `mark_done()` | :228-232 处 | `ok == 1` 且 record 非 None（CAS 失败分支 :226-227 不调） |
| `mark_delivered()` | :257-260 处 | `ok == 1`（:252-256 的重复 delivered / 非 done 分支不调，不重复写） |
| `_finish()` | :206-208 处 | `ok == 1`（:203-204 CAS 失败返回现状 record 时不调——只有真实迁移才写）；覆盖 `mark_failed` / `mark_orphaned` / `scan_stale` / `orphan_session_jobs` / `session_lost` 全部失败路径 |

钩子本身只做一次同步方法调用（writer 内 `queue.put_nowait`），不创建 task、不 await，
对实时链路的开销低于既有 `_notify_change`。

### 4.5 新组件 `JobPgWriter`

新建 `services/agent_relay/job_persistence.py`：

```python
class JobPgWriter:
    """JobStore 记录级钩子的 PG 消费端（D4：有界队列 + 单 writer，best-effort）。"""

    def __init__(self, pg_session_factory, job_store, *, max_queue: int = 1024) -> None:
        self._factory = pg_session_factory
        self._job_store = job_store
        self._queue: asyncio.Queue[JobRecord] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """先 reconcile（D5）再启动 writer task。调用方保证在 adapter.connect()
        之前调用（此时无在线迁移，reconcile 与迁移写入无竞态；D3 守卫兜底）。"""
        await self._reconcile()
        self._task = asyncio.create_task(self._run(), name="job-pg-writer")

    async def stop(self, *, drain_timeout_s: float = 5.0) -> None:
        """排空剩余队列（限时）后退出。必须在所有迁移来源（SM / adapters）停止
        之后调用（§4.6），否则 stop 后的新迁移将无人消费。"""
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=drain_timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "job pg writer drain timeout", extra={"pending": self._queue.qsize()}
            )
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def on_job_record(self, record: JobRecord) -> None:
        """JobStore 同步钩子（D2）：O(1) 入队；队列满 → 丢弃 + 指标，永不抛。"""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            inc_job_pg("queue_dropped")
            logger.warning(
                "job pg queue full, record dropped",
                extra={"job_key": record.job_key, "status": record.status},
            )

    async def _run(self) -> None:
        while True:
            record = await self._queue.get()
            try:
                async with self._factory() as session:
                    await AgentJobsRepository(session).upsert(self._to_row(record))
                inc_job_pg("written")
            except asyncio.CancelledError:
                raise
            except Exception:
                inc_job_pg("upsert_error")
                logger.warning(
                    "agent job pg upsert failed",
                    extra={"job_key": record.job_key},
                    exc_info=True,
                )
            finally:
                self._queue.task_done()

    async def _reconcile(self) -> None:
        """活跃 job 全量 upsert（D5）：兜「迁移后、PG 写入前」崩溃窗口与 PG 故障
        窗口内仍为 active 的 job。终态 job 不 reconcile（边界见 §5）。逐条
        best-effort，单条失败不中断。"""
        try:
            actives = await self._job_store.list_active()
        except Exception:
            inc_job_pg("reconcile_error")
            logger.warning("job pg reconcile: list_active failed", exc_info=True)
            return
        for record in actives:
            try:
                async with self._factory() as session:
                    await AgentJobsRepository(session).upsert(self._to_row(record))
                inc_job_pg("reconcile_ok")
            except Exception:
                inc_job_pg("reconcile_error")
                logger.warning(
                    "job pg reconcile upsert failed",
                    extra={"job_key": record.job_key},
                    exc_info=True,
                )

    @staticmethod
    def _to_row(record: JobRecord) -> dict:
        """JobRecord → agent_jobs 行。epoch s → timestamptz；0/空串 → None。
        finished_at 派生（D6）：completed_at > 0 → completed_at；failed（其
        completed_at 恒 0，job_store.py:192-209）→ 失败转换的 updated_at。
        不含 id / created_at（created_at 由 PG DEFAULT now() 首插、冲突保留）。"""
        if record.completed_at > 0:
            finished_at = _dt(record.completed_at)
        elif record.status == STATUS_FAILED:
            finished_at = _dt(record.updated_at)
        else:
            finished_at = None
        return {
            "job_key": record.job_key,
            "agent_name": "hermes",   # JobRecord 无此字段；当前唯一 adapter
            "device_uuid": record.device_id,
            "session_id": record.session_id,
            "hermes_task_id": record.hermes_task_id or None,
            "status": record.status,
            "summary": record.summary,
            "result_text": record.result_text or None,
            "fail_reason": record.fail_reason or None,
            "requested_at": _dt(record.created_at),
            "started_at": _dt(record.started_at),
            "finished_at": finished_at,
            "delivered_at": _dt(record.delivered_at),
            "updated_at": _dt(record.updated_at),
        }


def _dt(epoch_s: float) -> datetime | None:
    """epoch 秒 → aware datetime；0（未设置）→ None。"""
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc) if epoch_s > 0 else None
```

### 4.6 `application.py` 接线

**启动**（relay 组装块内，`job_store` 创建 :150-154 之后、adapter connect :162-174
**之前**——此位置保证 reconcile 期间无在线迁移，D5）：`job_pg_writer = None` 与
`job_status_notifier = None` 同处（:120 附近）声明，供 shutdown 访问：

```python
job_pg_writer = None
if pg_session_factory is not None and relay_cfg.job_pg.enabled:
    from agent_relay.job_persistence import JobPgWriter

    job_pg_writer = JobPgWriter(
        pg_session_factory, job_store, max_queue=relay_cfg.job_pg.max_queue_size
    )
    await job_pg_writer.start()   # reconcile → writer task（均在 adapter connect 前）
    job_store.set_record_listener(job_pg_writer.on_job_record)
    logger.info("agent job pg persistence started")
# pg_session_factory 为 None（PG 未部署）或开关关闭时静默跳过，现状行为不变
```

**Shutdown**（:284-306 段）：**先停迁移来源，后停 writer**——`event_router.stop()` →
`job_status_notifier.stop()` → `agent_session_mgr.shutdown()` / adapter close 全部完成
后（此时 JobStore 不再有任何新迁移），再排空 writer：

```python
# agent_session_mgr.shutdown() / adapters close 之后：
if job_pg_writer is not None:
    try:
        await job_pg_writer.stop()   # 排空剩余队列（限时 5s）再退出
    except Exception:
        logger.warning("job pg writer stop failed", exc_info=True)
```

> 顺序不可反转：若先停 writer，shutdown 期间产生的迁移仍会 `put_nowait` 进一个
> 无人消费的队列，静默丢失且无指标。（原 B 稿在此顺序与论证均有误。）

### 4.7 配置变更

`services/agent_relay/config.py`，`AgentRelayConfig` 新增嵌套子配置（沿用
`AdaptersConfig` / `ProactiveConfig` 先例）：

```python
class JobPgConfig(BaseModel):
    enabled: bool = True        # 总开关；pg_session_factory 为 None 时自动降级（不创建 writer）
    max_queue_size: int = 1024  # writer 队列上限（D4 背压；满 → 丢弃 + queue_dropped 指标）

class AgentRelayConfig(BaseSettings):
    ...
    job_pg: JobPgConfig = JobPgConfig()
```

环境变量：`LIVACHAT__AGENT_RELAY__JOB_PG__ENABLED`、
`LIVACHAT__AGENT_RELAY__JOB_PG__MAX_QUEUE_SIZE`。默认 `enabled=True`——只要 PG 可用即
开启；纯 Redis 联调 / ASR-only 等无 PG 环境自动跳过，不影响 relay 运行。

### 4.8 指标

`shared/observability/metrics.py` registry 新增（`_get_or_create` 模式）：

```python
def agent_job_pg_total(self) -> Counter:
    return cast(Counter, self._get_or_create(
        "livachat_agent_job_pg_total", Counter,
        "Agent job PG mirror writes, partitioned by result.",
        ["result"],
    ))
```

`agent_relay/metrics.py` 薄封装：

```python
def inc_job_pg(result: str) -> None:
    """result: written | upsert_error | queue_dropped | reconcile_ok | reconcile_error"""
    get_registry().agent_job_pg_total().labels(result=result).inc()
```

不复用 `livachat_errors_total{code="persist"}`：那是全模块共用的错误计数，无法区分
job 历史写入与其他持久化失败，且没有 drop / reconcile 维度。

---

## 5. 失败语义与一致性边界

| 场景 | 行为 |
|------|------|
| PG 宕机 / 写入异常 | 队列缓冲至 1024 条；满 → 丢弃 + `queue_dropped` 指标 + log；写入异常 → `upsert_error` 指标 + log，后续记录继续消费。job 生命周期与投递**不受任何影响** |
| **终态写入被丢弃/失败** | 行**永久**停留在前一状态（或缺失）——终态之后没有更多迁移可触发"下一次写入自愈"。这是明确的 best-effort 边界，靠 `queue_dropped` / `upsert_error` 指标可观测 |
| done 写入丢失、其后补投成功 | `mark_delivered` 的 full-state upsert 自愈（携带 `result_text` 与 `completed_at`）——仅当设备在 48h 窗口内重连并投递成功时成立 |
| relay 进程崩溃 | 在途队列丢失；重启时 reconcile 兜住仍为 active 的 job（D5）；崩溃窗口内已进入终态且再未投递的 job 缺失（接受） |
| reconcile 与在线迁移竞态 | 接线时序已消除（reconcile 在 adapter connect 前，§4.6）；残余窗口（未来多实例、接线变动）由 D3 守卫兜底：旧快照永不覆盖新状态 |
| Redis job 超 48h TTL 过期 | PG 行**不受影响**，永久保留（这正是本特性的目的） |
| 多实例部署 | 当前单进程；`ON CONFLICT` + 守卫天然抗重复/乱序，未来多实例同样安全（`updated_at` 跨进程时钟偏差是已知近似，单 clock source 内严格单调） |

---

## 6. 数据分析查询示例

```sql
-- 某设备近 30 天 job 数与状态分布（走 idx_agent_jobs_device_time）
SELECT status, count(*) FROM agent_jobs
WHERE device_uuid = '<uuid>' AND requested_at >= now() - interval '30 days'
GROUP BY status;

-- 每日完成/失败趋势（走 idx_agent_jobs_status_finished；failed 的 finished_at 非空，D6）
SELECT date_trunc('day', finished_at) AS day,
       count(*) FILTER (WHERE status IN ('done','delivered')) AS done,
       count(*) FILTER (WHERE status = 'failed')              AS failed
FROM agent_jobs
WHERE finished_at >= now() - interval '30 days'
GROUP BY 1 ORDER BY 1;

-- 失败原因分布（同上索引；本查询对 failed 有效是 D6 的直接验证）
SELECT fail_reason, count(*) FROM agent_jobs
WHERE status = 'failed' AND finished_at >= now() - interval '30 days'
GROUP BY 1 ORDER BY 2 DESC;

-- job 耗时（finished - started）分位数
SELECT percentile_cont(0.5)  WITHIN GROUP (ORDER BY finished_at - started_at) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY finished_at - started_at) AS p95
FROM agent_jobs
WHERE status IN ('done','delivered') AND started_at IS NOT NULL
  AND finished_at >= now() - interval '30 days';

-- 有结果 vs 被消费（NOOP/error）的完成 job
SELECT count(*) FILTER (WHERE result_text IS NOT NULL) AS with_result,
       count(*) FILTER (WHERE result_text IS NULL)     AS consumed_noop_or_error
FROM agent_jobs WHERE status IN ('done','delivered');

-- 完成但未送达（done 滞留，补投未成功）按设备聚合
SELECT device_uuid, count(*) FROM agent_jobs
WHERE status = 'done' AND finished_at >= now() - interval '7 days'
GROUP BY 1 ORDER BY 2 DESC;

-- 会话级关联（走 idx_agent_jobs_session）
SELECT j.session_id, count(*) AS jobs,
       count(*) FILTER (WHERE j.status = 'failed') AS failed
FROM agent_jobs j
WHERE j.requested_at >= now() - interval '30 days'
GROUP BY 1 ORDER BY 2 DESC LIMIT 50;
```

---

## 7. 测试计划

| 层 | 用例 |
|----|------|
| 迁移 | `alembic upgrade head` / `downgrade` 往返；`0003` 与 `Base.metadata` 一致（CI 既有校验） |
| Repository（PG 集成） | ① upsert 插入 → 各状态转换覆盖；② **乱序守卫**：先写 `delivered`（新 `updated_at`）再写 `done`（旧 `updated_at`），行保持 `delivered`；③ **failed 行 `finished_at` 非空且等于失败转换时刻**（D6，P0 回归）；④ `started_at=0 → NULL` 等 0/空映射；⑤ **commit 后另一会话可见**（防回滚回归）；⑥ `created_at` 首插后不被后续 upsert 覆盖 |
| JobStore 钩子（fakeredis + fake listener） | ① `create`/`mark_running`/`mark_done`/`mark_delivered`/`mark_failed`/`scan_stale`/`orphan_session_jobs` 各触发一次且携带迁移后最新 record；② CAS 失败（watchdog 已收敛）不触发；③ 重复 `mark_delivered` 不触发；④ listener 抛异常不扩散到迁移路径；⑤ 未配置 listener 时零调用 |
| JobPgWriter | ① `_to_row`：epoch→timestamptz、0/空→None、`finished_at` 派生（done 取 `completed_at`、failed 取 `updated_at`、active 为 None）；② 队列满 → 丢弃 + `queue_dropped` 指标；③ PG 异常 → `upsert_error` + 不崩溃、后续记录继续消费；④ `stop()` 排空队列、超时兜底；⑤ reconcile 对 active job 全量 upsert，单条失败不中断；⑥ **reconcile 旧快照不覆盖迁移后的新状态**（守卫端到端） |
| 接线 | ① `job_pg.enabled=False` 或 `pg_session_factory=None` 时无 listener，现状行为不变；② writer 接线点在 adapter connect 前（启动顺序断言）；③ shutdown 先停 SM/adapters 后排空 writer |
| 集成（fakeredis + 真 PG / testcontainers） | ① create→running→done→delivered 全链路后 `agent_jobs` 终态正确（含 `result_text` / `finished_at` / `delivered_at`）；② watchdog 收敛后 `failed` + `fail_reason` + 非空 `finished_at` 正确；③ **§6 失败原因分布 SQL 冒烟：failed 行必须出现在结果中**（P0 回归） |

---

## 8. 运维与演进

- **数据量与保留（D7）**：无 TTL 长期保留。量级估算：每活跃用户每设备日 ~几十条 job，
  单行 ~2-8KB（`result_text` ≤16KB），日增万级行 ≈ 百 MB 级/年，远低于 recording 的
  音频体量，前期单表 + 4 索引足够。膨胀后演进路径：按 `created_at` 月分区
  （pg_partman 或手工分区）+ 归档任务，或 `result_text` 外置对象存储。本期不做。
  **`result_text` 含用户生成内容，无限期留存是本期显式决策（D7），上线前需
  合规/产品书面确认。**
- **可观测**：`livachat_agent_job_pg_total{result}` 五值看板（written / upsert_error /
  queue_dropped / reconcile_ok / reconcile_error）；`queue_dropped > 0` 或
  `upsert_error` 持续非零即 PG 镜像健康告警。best-effort 边界见 §5；如需强一致，
  演进路径为迁移路径同步写 PG（以延迟换一致性）或 outbox 表，本期明确不做。
- **数据血缘**：`job_key` 与运行时补投 message_id（`msg_agent_job_{job_key}`，
  delivery.py:93）、job_status 载荷 `job_id`、Hermes task id（`hermes_task_id`）一致，
  可跨 Redis / PG / 客户端三方关联；`session_id` 可关联 `sessions` /
  `recording_sessions` 做会话级分析（§6 示例）。
- **后续接入 LvData**：`tools/data-analysis/` 现有管线以文件/SQLite 为主；`agent_jobs`
  表稳定后可增加 PG 数据源的分析脚本，本期不涉及。

---

## 9. 开放问题

1. **NOOP 与 hermes 侧 `error:` 不可区分**（§2.1-3）：两者均落库为
   `done + delivered + result_text IS NULL`。`result_text IS NULL` 可区分「有结果」
   与「被消费」，但若分析侧需要区分「hermes 执行失败」，需后续加 `result_kind` 列
   （先改 JobStore 记录该信息）——本期不做。
2. **`agent_name` 当前恒为 `'hermes'`**（§4.1-6）：多 adapter 接入时由 writer 按
   adapter 注入，届时无需改表。
