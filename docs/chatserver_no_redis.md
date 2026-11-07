# Chat Server 去 Redis 化重设计

> 状态：已定稿（A/B 两版评审合并，替代 `chatserver_no_redis-A.md` / `chatserver_no_redis-B.md`）
> 日期：2026-09-01
> 目标：移除 Redis 依赖，全部跨组件协调改为**进程内 cache / asyncio 机制**，
> 持久状态统一落 SQLite。Redis 存储模块源码保留但不再接线，使用 Redis 的
> 调用方代码删除或改写。
> 范围：`services/` 后端（gateway / session / recording / agent_relay / shared）+
> `tools/lvdata-mcp`（agent_jobs 消费端）
> 相关文档：[`chatserver_sqlite_store.md`](chatserver_sqlite_store.md) · [`fd_agent_relay_service_v2.md`](fd_agent_relay_service_v2.md) · [`fd_job_status.md`](fd_job_status.md) · [`docs/data/lvdata_mcp_service_v2.md`](data/lvdata_mcp_service_v2.md)

---

## 1. 背景与目标

现状：chat server 依赖 Redis 8 承担五类职责（详见 §2 盘点）：

1. 会话快照/历史（30s TTL 断线恢复）；
2. 连接注册表与跨实例路由（`conn:{device_id}` → instance_id）；
3. Pub/Sub 事件总线（agent events）与推送通道（per-instance push）；
4. agent job 状态机（JobStore，Redis 主存 + lvdata SQLite 镜像双写）；
5. hermes device→session 映射（跨重启 resume）。

部署形态上 relay 与 gateway **本就同进程**（`services/app/application.py`
lifespan 单组合根，`delivery.py:43-47` 注释亦明确「relay 与 gateway 同进程」），
Redis 的跨进程能力实际未被使用；job 状态已有完整的 SQLite 镜像
（`agent_relay/job_persistence.py` → lvdata `agent_jobs` 表，
`lvdata_mcp/jobs_watch.py` 消费）。

目标：

1. **简化架构，不使用 Redis**。`shared/persistence/redis_client.py` /
   `redis_store.py` / `agent_relay/job_store.py`（Redis 版）源码保留在仓库中
   但不再被调用；使用 Redis 的调用方代码删除或改写。
2. **跨实例协调 + 会话状态**：只保留 `conn:{device_id}`（设备在线/连接注册）
   与 device→session 两个**功能**，以进程内 registry 承接；其余（session
   snapshot/history、rate limit、分布式锁、provider usage/health）不再保留。
3. **Pub/Sub 事件总线**：改为进程内 asyncio 机制（有界队列 + 任务分发）。
4. **agent job 状态存储与通知**：只用 SQLite，且 `agent_jobs` 表从 lvdata DB
   **移至 chat_server.db**（chat server 单写者）；lvdata-mcp scanner 改为从
   chat_server.db 扫描 agent jobs 数据。通知与投递逻辑直接查 SQLite，不再需
   要 Redis。
5. **hermes session 跨重启恢复**：持久化 device→hermes session 映射，保证
   relay 重启后进行中的 background job 仍能 resume 会话并收到
   `background.complete`（否则结果无法通知）。
6. Redis 不可用的降级分支（`redis_store is None` 的一整套 if-else）随之消
   失，代码路径唯一化。

非目标：

- 多实例部署（负载均衡/跨实例路由）能力——本设计**主动放弃**多实例协
  调，部署形态固定为单进程单副本；未来如需多实例再引入外部协调组件。
- lvdata 底座其余表（events/chat_messages/recording_sessions/day_summary 等）
  与 lvdata-mcp 消费链路（agent_job events 铸造、affected 交付、reindex/embed
  补跑）语义不变；仅 `agent_jobs` **源表**换位置（lvdata DB → chat_server.db），
  scanner 适配（§3.8）。
- chat store（chat_server.db 既有三表）职责不变，新增两张表：`agent_jobs`
  （§3.5）与 `hermes_device_sessions`（§3.6）。

---

## 2. 现状盘点（代码调查结论）

### 2.1 Redis 触点总览（生产代码，全部清单）

| # | 位置 | 用途 | 读写方 |
|---|------|------|--------|
| 1 | `services/app/application.py:29-39,293-298` | lifespan 创建 redis_client/redis_store、挂 app.state、attach shutdown | 启动接线 |
| 2 | `services/app/routes.py:32-39` | `/readyz` redis ping 健康检查 | 每请求 |
| 3 | `services/app/lifespan.py:34,57-62` | shutdown `redis_client.aclose()` | 停机 |
| 4 | `shared/config/models.py:131-133` + `configs/dev/server.yaml:205-207` | `RedisConfig{url,key_prefix}`，env `LIVACHAT__REDIS__URL` | 配置 |
| 5 | `services/session/manager.py:81-96,175-176,180-198` | session snapshot/history 读写（resume、end、snapshot_on_disconnect） | SessionManager |
| 6 | `services/gateway/connection.py:368-381,403-452` | `_snapshot_session` 钩子；`register/unregister` 写 `conn:{device_id}` | ConnectionHandler/Manager |
| 7 | `services/recording/service.py:73-83,92,161-191` | `recording:device:{id}:current` 读写（acquire L1② 恢复分支） | RecordingService |
| 8 | `services/session/agent_publisher.py` | events 总线生产端（session.started/ended、user.utterance、recording.ready） | orchestrator×2、recording/session.py |
| 9 | `services/agent_relay/router.py` | events 总线消费端 EventRouter → AgentSessionManager | agent relay |
| 10 | `services/agent_relay/publisher.py` | push 通道生产端 PushPublisher（含 `conn:{device_id}` 读 `resolve_instance`） | session_manager、delivery、job_status |
| 11 | `services/gateway/push_subscriber.py` | push 通道消费端 PushSubscriber → ConnectionManager | gateway |
| 12 | `services/agent_relay/job_store.py` | JobStore：job HASH/SET 索引/inflight/task 映射，WATCH-MULTI CAS | hermes、delivery、job_status、chatlog_watcher、session_manager、job_persistence |
| 13 | `services/agent_relay/adapters/hermes.py:1513-1551` | `agent:hermes:device:{id}:session` 映射读写删 | HermesAdapter |
| 14 | `services/agent_relay/chatlog_watcher.py:37-65` | `resolve_chatlog_path` 读 `recording:device:{id}:current` | ChatlogWatcher、hermes `_render_mission` |

### 2.2 RedisStore 方法的真实使用情况（重要）

`shared/persistence/redis_store.py` 共 16 个方法，**近半数无生产调用方**：

- 生产使用：`save/load/delete_session_snapshot`、`save/load_session_history`
  （SessionManager）；`register_conn/unregister_conn`（ConnectionManager）。
- **仅测试使用**（`services/tests/integration/test_redis_store.py`，生产零调
  用）：`get_conn_instance`、`set_device_session`、`get_device_session`、
  `try_reconnect_quota`、`check_message_rate`、`acquire_session_lock`、
  `release_session_lock`、`bump_provider_usage`、`set_provider_health`、
  `get_provider_health`。
- `PushPublisher.resolve_instance`（`publisher.py:82-88`）不走 RedisStore，
  直接拼 key 读 `livachat:conn:{device_id}`。

### 2.3 各 Redis key 族语义与消费链路

| key（前缀 `livachat:`） | 数据结构/TTL | 生产端 → 消费端 | 去 Redis 后的承接 |
|---|---|---|---|
| `session:{sid}` / `session:{sid}:history` | HASH/LIST，30s | SessionManager → resume 分支 | **删除**（§3.4，chat store restore 已兜底） |
| `conn:{device_id}` | STRING，无 TTL | ConnectionManager.register → PushPublisher.resolve_instance、deliverer/job_status 在线检查 | **进程内**：`ConnectionManager.is_online/get_handler`（已存在，connection.py:462/466） |
| `device:{dev}:session` | STRING，30s | 无生产调用 | **进程内**：`ConnectionManager.get_session_id()`（§3.3） |
| `ratelimit:*` / `lock:*` / `usage:*` / `health:*` | - | 无生产调用 | 不承接（功能弃用） |
| `recording:device:{id}:current` | STRING JSON，48h | RecordingService → resolve_chatlog_path、hermes mission 渲染 | **进程内**：RecordingService registry 查询（§3.7） |
| `agent:events`（channel） | pub/sub | AgentEventPublisher → EventRouter → AgentSessionManager | **进程内事件总线**（§3.2.1） |
| `push:{instance_id}`（channel） | pub/sub | PushPublisher → PushSubscriber → ConnectionHandler | **进程内直接投递**（§3.2.2） |
| `agent:hermes:job:{key}` 等 5 个 key | HASH/SET/STRING，48h | JobStore 全体调用方 | **SQLite 直写 chat_server.db**（§3.5 SqliteJobStore） |
| `agent:hermes:device:{id}:session` | STRING JSON，48h | HermesAdapter 自读写 | **SQLite 新表**（§3.6） |

### 2.4 agent job 的 SQLite 已有基础（§3.5/§3.8 的事实依据）

- **表已存在且为全量镜像**（当前在 lvdata DB）：`agent_relay/job_persistence.py:30-53`
  `AGENT_JOBS_SCHEMA_SQL`——`agent_jobs(job_key PK, agent_name, device_uuid,
  session_id, recording_session_id, hermes_task_id, status, summary,
  result_text, fail_reason, requested_ts_ms, requested_at, started_at,
  finished_at, delivered_at, updated_at)`，索引覆盖 device/time、
  recording_session、status+finished、updated_at 水位。JobRecord 全部字段
  均有对应列（`completed_at→finished_at` 等派生规则见 `_to_row`，
  `job_persistence.py:191-222`：completed_at>0 → finished_at=completed_at；
  failed → finished_at=失败迁移的 updated_at；其余 NULL）。lvdata-mcp 侧
  `ddl.py:203-224` 有同构 DDL（BASE_TABLES_SQL）。
- **双写链路**：JobStore（Redis）迁移 → record listener → JobLvdataWriter
  有界队列 → UPSERT（单调守卫 `WHERE agent_jobs.updated_at <= excluded.
  updated_at`——防的是异步镜像乱序到达，`job_persistence.py:55` 注释明确）。
  writer 本体为 per-op `sqlite3.connect(timeout=10)` + `busy_timeout=5000`
  （`job_persistence.py:180-189`），WAL 仅 `_init_db` 启动时设置一次
  （库级属性；每连接重复设置会与并发写者争抢短暂独占锁，
  `job_persistence.py:103-111`）。
- **消费契约**（lvdata-mcp `jobs_watch.py`，单事务 5 步）：
  1. 读水位 `agent_jobs_sync_state`（lvdata DB）；
  2. keyset 分页扫 `agent_jobs`（`(updated_at, job_key) > (?, ?)`，batch 5000）；
  3. enrich 兜底：`ENRICH_CHAT_SQL` 以 `chat_messages.chat_session_id` 匹配
     回写 `agent_jobs.recording_session_id`（只 NULL→值、不动 updated_at）+
     `_link_by_time_window`（读 lvdata DB `recording_sessions` 镜像表，Python
     侧 device 小写 + 时间窗匹配回写）；
  4. 已关联行铸 `events`（type=agent_job，lvdata DB）；
  5. 推进水位 + 合并 `pending_affected_sessions`（lvdata DB），`check()`
     返回 affected → `watcher.deliver_affected` → reindex/embed 补跑。
  `chat_messages`/`recording_sessions`/`events`/`agent_jobs_sync_state` 均在
  **lvdata DB**——`agent_jobs` 移至 chat_server.db 后，enrich 的回写目标与
  匹配源将分属两个数据库文件，这是 §3.8 设计的主要约束。
- **affected 判定**（现行逻辑，§3.8.2 需重写部分）：依赖「enrich 直接 UPDATE
  agent_jobs」这一事实——以上一轮未关联行快照（`unlinked_before`，
  jobs_watch.py:106-107）对本轮回写结果做差集（jobs_watch.py:132-142），
  识别「本轮新建立关联」的 job，计入 affected sessions 交付。enrich 落点改
  到独立 links 表后，`agent_jobs` 行不再被回写，该差集恒为空——**affected
  判定必须同步重写**，否则老行新关联永远触发不了 reindex/embed 补跑
  （§3.8.2 步骤 3/4）。
- **list_jobs 直查**（`store.py:86-127`）：MCP tool 只读查询 `agent_jobs`
  （含未关联 job 检索），支持 device/session/recording_session/status/日期过
  滤——移库后读路径同样要适配（§3.8.3）。
- **缺口**：
  1. `agent_jobs` 无 `hermes_task_id` 索引（`find_by_task_id` 需要，§3.5 在
     chat_server.db 补 `CREATE INDEX IF NOT EXISTS`）；
  2. `agent_jobs` **没有 hermes_session_id 列**——「查 agent_jobs 取最新
     hermes session id」的备选方案不成立（且只覆盖有 job 的设备、无
     `stored_session_id`），req 5 采用新表（§3.6）。

### 2.5 hermes session 映射与重启恢复链路（req 5 的现状）

- 映射形态：`{hermes_session_id, stored_session_id}` JSON，48h TTL
  （`hermes.py:1538-1551`）。写点：session.create 成功（L1022）、
  session.resume 成功（L644）；删点：仅 `session_retention=close_on_idle`
  （L367-375，默认 keep 不删）。
- 恢复链路（重启后）：`recover_active_devices`（L677-710）→ JobStore
  `list_active_by_device` 找有 running job 的设备 → `_ensure_device` →
  `_load_device_mapping`（L1516-1536，置 `session_recovered=True` 待验证）→
  `_resume_with_retries` → `session.resume(stored_session_id)`：同 sid = OK
  （job 结果可投递）；新 sid = REAPED，running jobs 按 `session_lost`
  fail-fast（D10）；RPC 失败重试，耗尽同按 session_lost 处置。
- 结论：映射丢失 ≠ 系统错误，但会让全部 running jobs 立即失败、且丢失
  hermes 侧会话上下文（mission memory）。故 req 5 的持久化是**正确性必经
  路径**，且写入必须同步持久（不能走异步队列——保存后崩溃窗口丢映射 =
  running jobs 被 session_lost 误杀）。
- **device_uuid 大小写风险**：enrich 链路到处 `lower(device_uuid)` 匹配
  （§2.4），说明真实数据存在大小写不一致。新映射表（§3.6）为主键精确匹
  配，若写入与读取 casing 不同会**静默 miss → running jobs 被 session_lost
  误杀**——恰好踩中 req 5 要防的事故，访问方法必须统一归一（§3.6）。

### 2.6 进程内已有可复用设施

- `ConnectionManager`（`gateway/connection.py:394-486`）：进程内
  `dict[device_id, ConnectionHandler]` + 锁；`is_online()`/`get_handler()`
  已是 `conn:{device_id}` 的进程内等价；重复登录踢旧连接（`_kick_old`）不
  依赖 Redis。
- `ConnectionHandler._outbound`：**有界队列 maxsize=256**
  （`connection.py:66`），`enqueue_outbound` 为阻塞式 `await put`
  （L156-158）——推送背压设计的既有事实基础（§3.2.2、§4.6）。
- `ConnectionHandler._orchestrator._ctx.session_id`：编排上下文已持有
  live session id（`_kick_old` 正在访问该私有链，connection.py:473-474）——
  §3.3 的 `current_session_id` property 顺势把这处私有访问一并清理。
- `PushSubscriber._build_ai_text_frame/_build_job_status_frame`
  （`push_subscriber.py:114-169`）：帧构造与 `record_agent_job_result` 入史
  逻辑，可整体迁入进程内投递器。
- `RecordingService._sessions`（`recording/service.py:42`）：进程内
  device→RecordingSession registry；且 `recording_sessions` 表
  （chat_server.db，PK `(device_uuid, session_date)`，session_date 为 UTC
  日——`service.py:54` `_utc_today()`）已是 recording_session_id 的**事实
  来源**（`_find_or_create`，service.py:220-258），Redis L1② 恢复分支本就
  冗余。
- SQLite 工程契约（WAL 一次性、busy_timeout≥5000、幂等建表、规范时间戳、
  `asyncio.to_thread` 同步访问模式）：`chat_store.py`（`load_day` /
  `get_or_create_recording_session` 均为 to_thread 同步直连）与
  `job_persistence.py` 已确立，新代码直接沿用。
- Chat store restore（`session/manager.py:111-140`）：非 resume 分支从
  `messages` 表恢复最近一天 ≤20 条 WorkingMemory，生产已启用——session
  snapshot 删除后 resume 语义由它无缝承接。

### 2.7 配置与部署依赖

- `configs/dev/server.yaml:205-207`（redis 段，env `LIVACHAT__REDIS__URL`；
  仅 dev 显式配置，staging/production 未配置该段）；
- `Makefile server-infra-up`（启动 Redis 8 容器，见 REPO.md:53-55）；
  `infra/docker/docker-compose.dev.yml:18-23,37,44`（redis:8 服务 +
  depends_on）、`infra/kubernetes/configmap.yaml:112-113`、
  `secret.example.yaml:11` 含 redis 引用；`infra/docker/server.Dockerfile:19`
  安装 pip `redis` 包；`pyproject.toml` 含 `redis`/`fakeredis` 依赖。
- 测试：fakeredis 出现于 12+ 测试文件（unit/agent_relay×6、
  unit/gateway/test_push_subscriber、unit/session×2、integration conftest、
  test_application_wiring、test_job_pg_integration、e2e test_agent_relay_e2e、
  integration/test_redis_store）。

---

## 3. 目标设计

### 3.1 总体架构

单进程 asyncio 协作图（去 Redis 后）：

```
client WS ── gateway/endpoint ── ConnectionManager（进程内 registry，§3.3）
                    │                    ▲ get_handler / is_online
                    ▼                    │
        Dialog/OmniOrchestrator          │
                    │ publish_*（同步调用，§3.2.1）
                    ▼
        InProcessEventBus（有界 Queue + 单 consumer，§3.2.1）
                    │ dispatch
                    ▼
        AgentSessionManager ──► HermesAdapter ──► SqliteJobStore（§3.5）
                    │                               ▲          │ 同步 to_thread
                    │ _publish_reply/deliver/job_status        ▼
                    ▼                               chat_server.db（chat server 单写者）
        LocalPushPublisher（§3.2.2）────────────────┘   ├─ devices/messages/recording_sessions
                    │ enqueue_outbound（wait_for 兜底）  ├─ agent_jobs（§3.5，新）
                    ▼                                   └─ hermes_device_sessions（§3.6，新）
        ConnectionManager.get_handler ──► client WS            ▲ ATTACH 只读扫描（§3.8）
                                                               │
                                          lvdata-mcp ──────────┘
                                          jobs_watch：读 chat_server.agent_jobs
                                          → enrich → agent_job_links（lvdata DB，新）
                                          → events / agent_jobs_sync_state（lvdata DB）
```

原则：**一切跨组件通信都是同进程直接调用或 asyncio 队列**，无序列化、无网
络、无外部进程；SQLite 是唯一持久层。每个 DB 文件**单一写者**：
chat_server.db 只被 chat server 写，lvdata DB 只被 lvdata-mcp 写（跨进程只
读经 WAL + busy_timeout，与现行 relay→lvdata DB 模式相同）。写路径要么同
步 to_thread（需要写后读/持久性保证），要么有界队列（允许丢弃的镜像类写
入——本设计后此类仅剩 chat store messages）。

### 3.2 进程内事件总线与推送投递（req 3）

#### 3.2.1 InProcessEventBus（替代 `livachat:agent:events` 总线）

新文件 `services/agent_relay/event_bus.py`：

```python
class InProcessEventBus:
    """进程内 agent 事件总线：有界 asyncio.Queue + 单 consumer task。

    语义对齐 Redis pub/sub：FIFO、fire-and-forget（满 → 丢弃 + metric）、
    consumer 异常不扩散（log + 继续）。subscriber 注册为 async callable。
    全部生产/消费均在事件循环线程内，publish_nowait 不支持跨线程调用。
    """
    def __init__(self, *, max_queue: int = 1024) -> None: ...
    def subscribe(self, handler: Callable[[AgentEvent], Awaitable[None]]) -> None: ...
    def publish_nowait(self, event: AgentEvent) -> None:   # O(1)，永不抛
    async def start(self) -> None: ...                     # consumer task
    async def stop(self, *, drain_timeout_s: float = 3.0) -> None: ...
```

- `AgentEventPublisher`（`session/agent_publisher.py`）**接口不变**
  （`publish_utterance/publish_session_started/publish_session_ended/
  publish_recording_ready`，fire-and-forget + `inc_agent_event`），实现从
  `redis.publish` 改为 `bus.publish_nowait`；orchestrator×2、
  recording/session.py 全部调用方**零改动**。
- `EventRouter`（`agent_relay/router.py`）删除，其 `_dispatch` 分派逻辑
  （kind → `AgentSessionManager.on_*`）移为 bus 的订阅 handler（
  `application.py` 接线处一次 `bus.subscribe(sm.dispatch)`；
  `AgentSessionManager` 新增薄方法 `dispatch(event)`，内部即原 router 的
  kind 分派）。
- 顺序性：Redis pub/sub 单 channel FIFO ⇒ 单 consumer Queue FIFO，等价；
  且不再有「relay 重启丢事件」与「事件跨进程乱序」问题。实际只有一个订阅
  者，单队列单 consumer 为最简单等价形态；未来出现多订阅者再演进为每订
  阅者队列。
- 丢弃分级：队列满丢弃统一计数（§5，按 kind）；日志按 kind 分级——
  `session.ended`/`recording.ready` 等驱动 agent 会话生命周期的事件记
  warning，`user.utterance` 等高频事件可采样 info。
- **实施前置（线程约束审计）**：逐一确认现有生产端（orchestrator×2、
  recording/session.py）均在事件循环线程调用 `publish_*`；若存在线程上下
  文调用点，须经 `loop.call_soon_threadsafe` 桥接，禁止跨线程直接
  `publish_nowait`（asyncio.Queue 非线程安全）。

#### 3.2.2 LocalPushPublisher（替代 `push:{instance_id}` 通道）

`agent_relay/publisher.py` 重写（类名保留 `PushPublisher`，构造签名改为
`(conn_mgr, cfg)`，调用方零改动）：

- `publish(msg) -> bool`：`conn_mgr.get_handler(device_id)`；
  - `None` → 返回 False（离线，语义与今 `resolve_instance is None` 相同）；
  - 命中 → 用 `PushSubscriber._build_ai_text_frame/_build_job_status_frame`
    的现成逻辑建帧（迁入 publisher 或提取为共享函数）→
    **背压兜底**后投递（§4.6）；
  - `msg` 为 PushMessage 且 `job_key` 非空且 `is_final` → 帧后
    `handler.record_agent_job_result(...)`（保留 fd_pipeline_llm_v2 §11.1 入史，
    现 push_subscriber.py:105-112 行为）；
  - `max_reply_bytes` 截断逻辑保留（现 publisher.py:50-54）。
- `resolve_instance` **删除**（不再有跨实例路由，无保留价值）；
  `delivery.py:70-89` 与 `job_status.py:231-244` 的「conn key 指向本实例但
  无 handler = 残留」判断在单进程下**逻辑塌陷**为一次 `is_online`——这两
  处改为直接调用注入的 `online_check`（`conn_mgr.is_online`，
  application.py 接线处已注入），`instance_id` 参数删除。
- `gateway/push_subscriber.py` 删除；`application.py` 不再创建
  PushSubscriber、不再读 `LIVACHAT_INSTANCE_ID`。

### 3.3 连接/会话 registry（req 2）

- `ConnectionManager` 构造函数删除 `redis_store` 参数；`register/unregister`
  中的 Redis 写分支删除（含「redis store is None」warning 分支）。进程内
  dict 即注册表，`is_online/get_handler` 不变。
- 承接 `device:{dev}:session` 功能：`ConnectionHandler` 新增公共只读
  property `current_session_id`（返回编排上下文 session id，无会话 →
  None；**派生自既有状态，不引入第二事实源**——`_kick_old` 现正访问
  `handler._orchestrator._ctx.session_id` 私有链，一并改走该 property），
  `ConnectionManager` 加便捷方法：

  ```python
  def get_session_id(self, device_id: str) -> str | None:
      """设备当前 live chat session id（无连接/无会话 → None）。"""
      handler = self._conns.get(device_id)
      return handler.current_session_id if handler is not None else None
  ```

  （当前无生产消费方——push 帧不透传 session_id（delivery.py:92 注释）；
  保留该方法仅为功能对齐与后续 BFF/诊断使用。）
- 弃用且无承接：重连配额、消息限流、会话分布式锁、provider usage/health
  （§2.2 已证实无生产调用）。进程内限流如未来需要，另起 fd 设计。

### 3.4 会话 snapshot/resume（req 2「其他不用」）

- 删除：`SessionManager` 的 `redis` 参数与 `start` 的 snapshot resume 分支
  （manager.py:81-96）、`end` 的 snapshot 删除（L175-176）、
  `snapshot_on_disconnect`（L180-198）；`ConnectionHandler._snapshot_session`
  钩子及其调用点（connection.py:188-189）。
- resume 统一走 **chat store restore**（已实现，manager.py:111-140）：客户
  端带 `resume_id` 重连时 `session_id = resume_id` 保留（L109 现状不变），
  WorkingMemory 从 `messages` 表恢复最近一天 ≤20 条（含 images/agent_job
  source/ref_id）。
- 行为变化（决策点 D1，§4.1）：`restored` 恒 False、`epoch` 恒 0 起。

### 3.5 SqliteJobStore（req 4）

新文件 `services/agent_relay/job_store_sqlite.py`：**与 `JobStore` 完全同接
口**（同名方法、同 `JobRecord` dataclass、同状态常量——从 `job_store.py`
import 复用），实现换成 **chat_server.db `agent_jobs` 表**直读直写。调用
方（hermes、delivery、job_status、chatlog_watcher、session_manager、
application）除构造注入外零改动。

#### 3.5.1 存储与 schema

- 落 **chat_server.db**（`cfg.sqlite.chat_server_db_path`，已 fail-fast 必
  配——**不引入任何新必需配置**，relay 与 lvdata DB 彻底解耦）。表
  schema 由 ChatStore 统一管理（chat_server.db 单写者纪律）：
  `AGENT_JOBS_SCHEMA_SQL`（job_persistence.py:30-53，列定义不变）迁入
  `CHAT_STORE_SCHEMA_SQL`，追加：

  ```sql
  CREATE INDEX IF NOT EXISTS idx_aj_task ON agent_jobs(hermes_task_id);
  ```

  （幂等建表，对存量库无破坏；updated_at 水位扫描用现有
  `idx_aj_updated(updated_at, job_key)`——scanner 侧契约不变。）
- `recording_session_id` 列保留；**relay create 时尽力解析**（§3.5.3，基
  线行为），未知留 NULL——enrich 兜底由 lvdata-mcp 在其自有库的
  `agent_job_links` 承接（§3.8.2）。`agent_jobs` 保持 chat server 单写者，
  lvdata-mcp 不回写该表。契约 4 语义由「relay 恒写 NULL」修正为「relay
  尽力解析、未知留 NULL」。
- 访问模式：每次操作 `sqlite3.connect(timeout=10)` + `busy_timeout=5000`，
  经 `asyncio.to_thread` 包裹（对齐 `_upsert_timezone` /
  `get_or_create_recording_session` 及现行 `JobLvdataWriter._upsert` 模
  式）。**不走队列 writer**：job 迁移链路存在写后读（`mark_running` 成功
  后立即 `get`，job_store.py:205；`deliver` 内 re-read），异步队列会破坏
  该语义并重新引入「迁移后落盘前」崩溃窗口（现行靠启动 reconcile 兜底）
  ——直写模式下该窗口类别整体消失，reconcile 与停机排空随之不需要。QPS
  极低（每 job ≤5 次迁移 + 1s 一次有界扫描），per-op connect 开销可忽略。
- 行 ↔ JobRecord 映射：复用 `job_persistence._to_row` / `_dt` / `_epoch_ms`
  逻辑（迁入；`_to_row` 的 `recording_session_id` 改为取 JobRecord 字段，
  不再写死 None）；读回时 ISO→epoch 解析（`_to_epoch_ms` 风格，jobs_watch
  已有同款）。
- **时间戳与单调性**：`updated_at` 在 writer 线程内、执行 SQL 前生成（消
  除 to_thread 调度乱序——线程池内「调用序 ≠ 执行序」——导致的时间戳回
  退），每次迁移置为规范当前时间——scanner 水位契约依赖其单调性。状态迁
  移 SQL 额外保留防御性子句 `AND updated_at <= ?`（参数即本次写入时间
  戳）：正常路径恒真；一旦时钟回拨等异常使时间戳回退，迁移以 rowcount=0
  失败（可重试、可告警），把「水位契约被静默破坏」转换为「一次显式失
  败」，fail-safe 方向正确。原 UPSERT 单调守卫防异步镜像乱序的职责由该
  子句一并承接。

#### 3.5.2 方法 → SQL 映射

| JobStore 方法 | SQL 语义 |
|---|---|
| `create` | INSERT（status=requested，recording_session_id 按 §3.5.3 解析）；监听钩子不变 |
| `mark_running` | `UPDATE ... SET status='running', hermes_task_id=?, started_at=?, updated_at=? WHERE job_key=? AND status='requested'`；`rowcount==1` 即原 CAS 成功语义 |
| `mark_done` | `UPDATE ... WHERE job_key=? AND status='running'`（result_text 截断不变；finished_at=completed_at） |
| `mark_delivered` | `UPDATE ... WHERE job_key=? AND status='done'`（幂等：已 delivered 返回现状，对齐现语义） |
| `_finish`（failed） | `UPDATE ... WHERE job_key=? AND status IN ('requested','running')`（finished_at=本次 updated_at，对齐 _to_row 派生规则） |
| `get` | `SELECT ... WHERE job_key=?` |
| `find_by_task_id` | `SELECT ... WHERE hermes_task_id=?`（走 idx_aj_task） |
| `session_job_keys` / `device_job_keys` | `SELECT job_key ... WHERE session_id=? / device_uuid=?` |
| `outstanding_jobs[_for_device]` | `... AND status IN ('requested','running')` |
| `list_pending_delivery(device)` | `WHERE device_uuid=? AND status='done' AND finished_at >= ?`（now−48h，原 TTL 语义改为查询谓词——现行 48h 补投窗口由 mark_done 刷新 device 索引 TTL 隐含提供，job_store.py:254-259），按 `finished_at` 升序（原 completed_at 排序语义） |
| `list_active` / `list_active_by_device` | `WHERE status IN ('requested','running')`（全局 in-flight cap 20，结果集天然有界）；by_device 为内存分组，对齐现实现 job_store.py:375-380 |
| `list_pending_delivery_all` | `WHERE status='done' AND finished_at >= ?`（替代现 SCAN 设备索引） |
| `in_flight_count` | `SELECT COUNT(*) WHERE status IN ('requested','running')` |
| `scan_stale` | `WHERE status IN (...) AND updated_at < ?` 按状态分别套用 running/requested 超时 |
| `orphan_session_jobs` | 保留（运维清理），`WHERE session_id=? AND status IN (...)` |

（注：status='done' 本身已排除 delivered——`mark_delivered` 是
done→delivered 状态迁移（job_store.py:36,269），`delivered_at IS NULL`
谓词冗余，不写入 SQL。）

- **CAS 正确性**：原 WATCH/MULTI 防的是 watchdog 与 background.complete 的
  check-then-act 竞态（job_store.py:439-448）。单写者（本进程）+ SQLite 单
  语句 `UPDATE ... WHERE status IN (...)` 原子生效：两个并发 UPDATE 经
  SQLite 写串行化后只有一个 rowcount=1，等价语义成立。
- **TTL 语义变化**：Redis 48h 物理过期 ⇒ SQLite 历史行**永久保留**
  （scanner 需要历史铸造 events；chat store 的 messages 日 prune 只删
  `messages` 表，不触及 `agent_jobs`）。48h 仅作为补投窗口谓词
  （`list_pending_delivery*`）与 `job_status._last` 内存条目淘汰常量
  （JOB_TTL_S 保留）。watchdog 收敛语义不变（`background_timeout_s` 默认即
  48h）。
- **监听器保留**：`set_change_listener`（→ JobStatusNotifier）与
  `set_record_listener` 钩子原样保留，同步 fire-and-forget 语义不变。
  record listener 现消费者 `JobLvdataWriter` **退役删除**（直写即真源，无
  镜像可维）；其 `start` 的 reconcile 与停机排空随之消失（无崩溃窗口——
  写 SQLite 即已持久）。
- **配置变化**：`sqlite.lvdata_db_path` 与 relay **完全解耦**——
  `chat_server_db_path` 本就 fail-fast 必配，JobStore 直接复用。
  `lvdata_db_path` 仅余 `_upsert_timezone`（session/manager.py，经
  `shared/timeutil.lvdata_db_path` 解析 bff/recording 配置）使用，与本设
  计无关。

#### 3.5.3 create 时 recording_session_id 解析（基线行为）

job 关联若完全依赖 lvdata-mcp enrich 兜底，其匹配源是 lvdata DB 的**镜
像**，由文件扫描填充、滞后于 recording finalize——events 铸造最少滞后一
个镜像扫描周期。故 create 时解析设为**基线行为**（非可选增强）：

- `JobRecord` dataclass 增加字段 `recording_session_id: str | None = None`
  （默认 None，不破坏现有构造与调用方）。
- `SqliteJobStore` 构造接受 `recording_lookup` callable（构造注入，调用方
  零改动）；application.py 接线注入 **ChatStore 新增的只读方法**：

  ```python
  async def get_recording_session(
      self, device_uuid: str, session_date: str
  ) -> dict | None:
      """按 PK (device_uuid, session_date) 只读查 recording_sessions 权威行。

      只读 SELECT——不得 get-or-create：job create 不得副作用创建
      recording 行。device_uuid 在方法内 lower() 归一（§3.6 同款纪律）。
      """
  ```

  `create` 时以 `(device_uuid, 当日 UTC)` 同库直查（PK 即
  `(device_uuid, session_date)`，匹配确定性）：
  - 命中 → 行内写入，**首轮 scanner 即可铸 events**，reindex/embed 链路显
    著提速；
  - 未命中（recording 未启用/当日行尚未创建/跨零点 job）→ 留 NULL，
    enrich 兜底全路径保留（§3.8.2：精确匹配覆盖「recording 晚于 job 创
    建」，时间窗路径覆盖「跨零点」），行为与旧契约一致；
- 单写者纪律不受影响——该列仍由 chat server 写入；lvdata-mcp 读侧
  COALESCE（§3.8.1）天然兼容「relay 已填」与「links 兜底」两种来源；
- 解析不命中不重试，不引入额外调用链。

### 3.6 hermes device→session 映射表（req 5）

新表放 **chat_server.db**（ChatStore 管理；运行时状态而非分析数据，且
ChatStore 已 fail-fast 必有，不引入对 lvdata DB 的额外启动依赖）：

```sql
CREATE TABLE IF NOT EXISTS hermes_device_sessions (
    device_uuid       TEXT PRIMARY KEY,
    hermes_session_id TEXT NOT NULL DEFAULT '',
    stored_session_id TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL          -- ISO 8601 UTC 毫秒 Z（规范格式）
);
```

- ChatStore 新增三个方法（同步实现 + `asyncio.to_thread`，对齐
  `get_or_create_recording_session` 形态；**同步直写，不走 queue**——
  §2.5 持久性论证）：
  `load_hermes_session(device_uuid) -> dict | None`、
  `save_hermes_session(device_uuid, hermes_session_id, stored_session_id)`
  （UPSERT）、`delete_hermes_session(device_uuid)`。
- **device_uuid 大小写归一（强制纪律）**：三个访问方法内部统一
  `lower(device_uuid)` 归一后再读写——写入与读取共用同一归一点，防
  「casing 不一致 → 映射静默 miss → running jobs 被 session_lost 误杀」
  （§2.5 风险分析）。`get_recording_session`（§3.5.3）同款归一。
- `HermesAdapter` 注入位从 `session_redis/session_redis_prefix` 改为
  `hermes_store`（即 ChatStore 或薄封装）；`_load_device_mapping /
  _save_device_mapping / _delete_device_mapping` 三个方法改调它，字段与
  `session_recovered=True` 待验证语义不变。
- TTL 取消：旧行无害——resume 失败走 session_lost 收敛（REAPED/RPC 耗尽
  均 fail-fast，§2.5），新 create 覆盖旧行；且 hermes 侧会话保留期若长于
  48h，旧映射反而能多救回一批 running jobs。卫生性 prune（可选实施项）：
  `updated_at` 超 7 天且设备无 active job 的行随 ChatStore prune 周期删除。
- 「从 agent_jobs 反推最新 session id」备选方案的结论（§2.4 缺口 2）：
  `agent_jobs` 无 hermes_session_id 列，加列需动 lvdata 契约且覆盖不了
  「有会话无 job」设备与 `stored_session_id`，**否决**，采用本表。

### 3.7 recording current 的进程内解析（req 3 收尾）

- `RecordingService` 新增：

  ```python
  async def current_recording_info(self, device_uuid: str) -> dict | None:
      """{recording_session_id, base_path}；内存 registry 优先，
      recording_sessions 表只读兜底（acquire L1③ 同语义）。

      只读方法——不得触发 get-or-create（§3.5.3 同款纪律）。"""
  ```

  同时删除 `_redis_key/_redis_get/_update_redis` 及 `acquire` 的 L1② 分支
  （service.py:73-85）——chat store 本就是事实来源，行为不变。
- `chatlog_watcher.resolve_chatlog_path(redis_client, ...)` 改为
  `resolve_chatlog_path(lookup, device_id, base_dir)`（lookup 即上述方
  法）；ChatlogWatcher 构造的 `redis_client/redis_prefix` 参数改为
  `recording_lookup` callable；`hermes._render_mission` 同步改造。
- 注：watcher 与 mission 渲染拿到的是「当前」recording 会话——进程内查询
  与 Redis GET 同为近似实时，语义不变。

### 3.8 lvdata-mcp scanner 适配（agent_jobs 源表换库）

`agent_jobs` 从 lvdata DB 移至 chat_server.db 后，lvdata-mcp 从「读自己库里
的镜像」变为「**ATTACH 只读扫描 chat_server.db**」。核心纪律：**每个 DB
文件单一写者**——lvdata-mcp 对 chat_server.db 只读，enrich 结果不回写
`agent_jobs`，改存 lvdata DB 自有新表。

#### 3.8.1 连接与 schema

- `JobsWatch`/`DataStore` 连接：主连接照常打开 lvdata DB（rw/ro），随后
  `ATTACH DATABASE 'file:<chat_server_db>?mode=ro' AS cs`（uri 模式；
  busy_timeout 为连接级属性，对 attached 库同样生效；chat_server.db 已是
  WAL——ChatStore init 设置——跨进程读写并发无阻塞）。
- **WAL 只读部署约束**：WAL 库的只读打开需要 -shm/-wal 已存在或可创建。
  lvdata-mcp 运行账户必须对 chat_server.db **所在目录有写权限**（或保证
  chat server 先启动创建好文件）；lvdata-mcp 与 chat server **同机部署**。
  两项均写入部署文档（§7）。
- **lvdata DB 新增表**（ddl.py BASE_TABLES_SQL 追加）：

  ```sql
  CREATE TABLE IF NOT EXISTS agent_job_links (
      job_key              TEXT PRIMARY KEY,
      recording_session_id TEXT NOT NULL,
      linked_via           TEXT NOT NULL,   -- chat_messages | time_window
      updated_at           TEXT NOT NULL
  );
  ```

  行的有效 rsession = `COALESCE(cs.agent_jobs.recording_session_id,
  agent_job_links.recording_session_id)`——前者为 relay create 时解析写入
  的兼容位（§3.5.3），miss 时恒 NULL；两种来源对读侧透明。
- `agent_jobs_sync_state`（水位 + pending_affected_sessions）**留在 lvdata
  DB 不动**——它是 scanner 自身状态。
- lvdata ddl 中的 `agent_jobs` 建表语句从 BASE_TABLES_SQL 移除（新部署不再
  在 lvdata DB 建该表；存量部署的旧表原地冻结，事件早已铸完，不删）。
  `ddl.py` §7 迁移（pg_updated_at 改名/时间戳归一化）本就有 table-exists
  守卫（`if "agent_jobs" in tables`），保留不动。
- lvdata-mcp `DataConfig` 新增 `chat_server_db_path: str = ""`（默认
  `<root_dir>/chat_server.db`，加 `resolved_chat_server_db_path`
  property）；文件不存在时 `check()`/`list_jobs` 按现行「表不存在」语义
  优雅返回空（`_jobs_table_ok` 同款守卫）。

#### 3.8.2 jobs_watch.check() 重写（写全部留在 lvdata DB 单库）

单事务 5 步语义不变，仅换源/换 enrich 落点：

1. 读水位（main.`agent_jobs_sync_state`）；
2. keyset 分页扫 **cs.`agent_jobs`**（同 `(updated_at, job_key)` 游标、
   batch 5000；同一 BEGIN 内全部 cs 读共享一个 cs 快照，分页一致性纪律保
   持）；
3. enrich 兜底：**匹配源与匹配逻辑不变**（main.`chat_messages` 的
   chat_session_id 精确匹配 + main.`recording_sessions` 镜像的 device+时间
   窗匹配——**两条路径全保留，关联召回率不变**；时间窗路径是跨零点
   job 的唯一召回手段，不得裁撤）。候选仅为 relay 未解析行
   （`cs.agent_jobs.recording_session_id IS NULL`），结果
   `INSERT OR IGNORE INTO agent_job_links (job_key, recording_session_id,
   linked_via, updated_at)`（**不再 UPDATE agent_jobs**）。本轮新建立关联
   的 job_key 集合 = 候选 (job_key, rsession) 对集合减去 links 表
   pre-image（候选规模小，Python 侧差集即可）；
4. 铸 events：数据源改为 `cs.agent_jobs LEFT JOIN main.agent_job_links
   USING (job_key)`，有效 rsession 取 COALESCE；已关联行铸 `events`
   （main 库，EVENT_UPSERT_SQL 本身不变）。**affected 判定同步重写**（替
   代现行 `unlinked_before` 快照差集——§2.4 已论证旧机制依赖 enrich 回
   写 agent_jobs，落点改 links 表后旧差集恒为空）：本轮 affected =
   ① 本批扫描到的新增/状态推进中有效 rsession 非空者 + ② 步骤 3 新建立
   关联的 job 对应的 rsession；与现行「新增/推进 + 新关联」两并集语义一
   致；
5. 推进水位 + 合并 pending（main），返回 affected 交付 watcher（不变）。

**崩溃原子性分析**：全部写在 main 单库 → 与现行单库事务完全同构（崩溃整
体回滚，下轮按旧水位重扫，events/links 均幂等——events 靠
`ON CONFLICT(event_ref)`，links 靠 `INSERT OR IGNORE`）。跨库的只是**读**
（cs.agent_jobs）——无写原子性问题。残留理论边界：同毫秒、更小 job_key
的并发写入漏扫（现行设计同样存在，非本次引入）；ops 兜底沿用
`pipeline.run(stage="agent_jobs", force=True)` 全量重扫。

#### 3.8.3 list_jobs 直查适配（store.py）

> **2026-09-27 注**：`list_jobs` 工具已**彻底删除**（工具 + `DataStore.list_jobs`/`_job_out`
> + `_attach_cs`/`_cs_table_ok` 连带移除；未关联 job 不再可查）——见
> [`data/lvdata_mcp_tools.md`](data/lvdata_mcp_tools.md)。本节为历史实现记录。

`DataStore.list_jobs` 改为：ro 连接 lvdata DB + ATTACH chat_server ro，查
`cs.agent_jobs LEFT JOIN main.agent_job_links USING (job_key)`，输出行的
`recording_session_id = COALESCE(aj.recording_session_id, l.recording_
session_id)`；`recording_session_id` 过滤条件改按该 COALESCE 表达式。其
余过滤/分页/输出字段（`_job_out`）不变。

#### 3.8.4 存量数据迁移（一次性）

现有部署的 `agent_jobs` 历史在 lvdata DB。切换步骤：

1. 停 chat server（新版未上线）；
2. 运行一次性脚本 `tools/data-analysis/migrate_agent_jobs_to_chat_server.py`
   （新）：ATTACH 两库，**显式列清单**复制（不依赖两表列序一致——存量
   lvdata 库可能经历过 §7 列改名迁移）：
   `INSERT OR IGNORE INTO cs.agent_jobs (job_key, agent_name, ..., updated_at)
   SELECT job_key, agent_name, ..., updated_at FROM main.agent_jobs`
   （含全部历史，保证 in-flight job 的补投/恢复在新库可见）；
3. 上线新 chat server + 新 lvdata-mcp（同版本，组合约束见 §4.7）。

水位兼容：**水位零干预**。`agent_jobs_sync_state` 水位是旧表的 ISO
updated_at——复制来的旧行 ≤ 水位不会被重扫（不会重铸 events），新写入 >
水位正常被扫。**不上调水位**（水位到复制行最大值之间的行是「已写入但尚
未被扫描铸 events」的行——停 server 前的扫描滞后窗口，mcp 若曾停机则窗
口更大——上调会将其永久跳过，造成 events 永久丢失）；**不下调**。不动
水位的代价仅是幂等重铸一轮，events upsert（ON CONFLICT）天然去重。脚本
内置 dry-run/apply 双模式自校验：行数比对 + in-flight 行存在性抽查 + **水
位零干预断言**（迁移前后水位值不变）。

---

## 4. 语义变化与决策点

### 4.1 D1：session resume 的 `restored`/`epoch` 语义

- 现状：30s 内重连且 snapshot 命中 → `restored=True`、`epoch+1`、初始
  status 直发 listening；snapshot 过期 → `restored=False`、先发 idle 再
  listening，session_id 仍延续。
- 去 Redis 后统一为后者（即现行「过期路径」行为）：`restored` 恒 False、
  `epoch` 恒 0（epoch 仅在单连接生命周期内做任务失效比对，归零安全）、
  history 由 chat store 恢复（比 snapshot 的 6 条更多：当天 ≤20 条）。
  `audio_sample_rate`/`enable_video`/`vad_sensitivity`/`user_id` 不再从快
  照恢复——客户端每次 `session.start` 携带完整 config，行为不变。
- 客户端可见差异：重连后 `session.ready.restored=false` + 多一帧 idle
  status。**接受**（与现行 30s 后行为一致，客户端本就必须兼容）；如要保
  留 30s 精确恢复，可在 SessionManager 加 30s TTL 进程内 dict——列为备
  选，不默认实施。

### 4.2 D2：agent_jobs 移库后的 enrich 落点（单写者纪律）

- 备选 A（否决）：lvdata-mcp ATTACH chat_server.db **可写**，沿用现行
  `UPDATE agent_jobs SET recording_session_id`——chat_server.db 出现第二个
  跨进程写者（server 写 job、mcp 写 enrich），与单 writer 纪律冲突：WAL
  单写者锁竞争（mcp 长事务可阻塞 server 的 job 写直至 busy_timeout）、跨
  库写无崩溃原子性、故障归因与备份语义模糊。
- 备选 B（采纳）：enrich 结果写 lvdata DB 自有 `agent_job_links` 表
  （§3.8.1），`agent_jobs` 保持 chat server 单写者。mcp 全部写集中在
  lvdata DB 单库事务，崩溃原子性与现行完全同构；links 通过 COALESCE 对
  读侧透明；两条 enrich 匹配路径全保留，关联召回率不变；**affected 判定
  按 §3.8.2 步骤 4 重写**（旧 `unlinked_before` 差集机制随 enrich 落点迁
  移而失效，必须同步替换——这是本决策点最容易遗漏的连锁改动）。
- 附带收益：relay 与 lvdata DB **零耦合**（不再写 lvdata DB、不需要
  `sqlite.lvdata_db_path`）——`chat_server_db_path` 本就 fail-fast 必配。

### 4.3 D3：agent_relay 配置项清理

`events_channel` / `push_channel_prefix` 失去意义。字段保留（deprecated
注释，忽略不读），避免存量 server.yaml 校验报错；`LIVACHAT_INSTANCE_ID`
env 退役。

### 4.4 D4：多实例能力

明确放弃：单设备双连接踢旧、push 投递、事件总线全部仅本进程有效。k8s
replicas>1 或滚动更新期间的旧实例不再能收到 push（与今无异——今亦靠
conn key 漂移，滚动窗口本就有损）。README/部署文档注明单副本约束；
lvdata-mcp 与 chat server 同机部署约束（§3.8.1）同步注明。

### 4.5 D5：`agent_jobs` 历史膨胀

行不再 48h 过期，且 chat_server.db 的日 prune 只删 `messages` 表、不触及
`agent_jobs`。单设备 job 频率低（每日数十），年量级 <10⁵ 行，SQLite 无压
力；如需要可后续按 `finished_at` 归档（非本次范围）。lvdata-mcp 水位机制
对存量表无影响。

### 4.6 D6：推送背压路径变化

`ConnectionHandler._outbound` 是 maxsize=256 的有界队列，`enqueue_outbound`
为阻塞式 `await put`（connection.py:66,156-158）。现行架构下阻塞 enqueue
的是 PushSubscriber 的独立消费 task，与投递协程隔离；直连后阻塞点移入
delivery / job_status / reply 协程——客户端卡死且出站积满 256 帧时，
job 投递与状态扫描循环会被拖住。处置：LocalPushPublisher 投递统一包
`asyncio.wait_for(handler.enqueue_outbound(frame), timeout=2.0)`；超时 →
记 metric（§5）并按投递失败返回 False——done job 结果后续经
`list_pending_delivery` 补投，ai_text 丢弃（与 fire-and-forget 语义一
致）。该兜底对现行 PushSubscriber 路径同样是改进。

### 4.7 D7：版本混跑兼容矩阵

P1 切流涉及 server 与 lvdata-mcp 两侧的数据源切换，切换窗口内的版本组合
约束：

| chat server | lvdata-mcp | 行为 |
|---|---|---|
| 旧 | 旧 | 现状（Redis + lvdata 镜像） |
| 旧 | 新 | **禁止**：新 mcp 扫 chat_server.db，旧 server 不写该表 → agent_jobs 数据源为空 |
| 新 | 旧 | 避免：agent_jobs 写入 chat_server.db，旧 mcp 读 lvdata DB 冻结旧表 → events 断流；仅允许作为分钟级过渡，且须 §9.2 反向脚本第 1 步持续同步兜底 |
| 新 | 新 | 目标形态 |

部署要求：P1 按「停旧 server → §3.8.4 迁移脚本 → 新 server + 新 mcp **同
版本**上线」执行，不允许跨版本长期混跑。

---

## 5. 观测性

去 Redis 后失去 `MONITOR`/keyspace 等外部 introspection 手段，以下指标补
齐（命名沿用 `shared/observability/metrics.py` 既有风格）：

| 指标 | 类型 | 说明 |
|---|---|---|
| `livachat_agent_event_bus_depth` | Gauge | InProcessEventBus 队列深度（每次 publish/consume 刷新） |
| `livachat_agent_event_bus_dropped_total` | Counter | 队列满丢弃的事件数（按 kind；丢弃日志按 kind 分级，§3.2.1） |
| `livachat_agent_push_total` | Counter | LocalPushPublisher 投递结果：ok / offline / backpressure_timeout / error |
| `livachat_agent_push_latency_seconds` | Histogram | publish 端到端耗时（含 enqueue wait） |
| `livachat_agent_job_store_total` | Counter | SqliteJobStore 写结果：written / cas_miss / error（沿用 inc_job_pg 标签位；防御子句触发的 rowcount=0 单独计 cas_miss 并 warning） |
| `livachat_agent_job_links_total` | Counter | enrich 新建立关联数（按 linked_via=chat_messages/time_window，lvdata-mcp 侧） |
| `livachat_agent_jobs_unlinked` | Gauge | 扫描时有效 rsession 仍为空的 agent_jobs 行数（lvdata-mcp 侧，关联召回健康度） |

日志纪律不变：迁移失败、投递失败、enrich 跳过（缺镜像表）均 warning +
exc_info。

---

## 6. 改造清单（按文件）

### 6.1 新增

| 文件 | 内容 |
|---|---|
| `services/agent_relay/event_bus.py` | `InProcessEventBus`（§3.2.1） |
| `services/agent_relay/job_store_sqlite.py` | `SqliteJobStore`（§3.5；行映射、CAS、updated_at 防御子句、create 时 rsession 解析、监听器钩子） |
| `tools/data-analysis/migrate_agent_jobs_to_chat_server.py` | 一次性存量迁移脚本（§3.8.4，显式列清单 + 自校验 + 水位零干预断言） |

### 6.2 修改

| 文件 | 改动 |
|---|---|
| `services/app/application.py` | 删 redis 创建/app.state/shutdown attach；agent_relay 接线：SqliteJobStore(chat_server_db_path, recording_lookup=chat_store.get_recording_session)、PushPublisher(conn_mgr)、bus.subscribe、hermes_store 注入；删 PushSubscriber/EventRouter/JobLvdataWriter/LIVACHAT_INSTANCE_ID；`cfg.agent_relay.enabled` 不再以 redis_client 为条件 |
| `services/app/routes.py` | 删 /readyz redis 检查；**新增 chat_store 存活检查（必须）**——去 Redis 后 readiness 仅剩的信号面，checks 不得为空恒真（同步直连一次轻量查询，如 `SELECT 1` 经 to_thread） |
| `services/app/lifespan.py` | 删 redis aclose 与 attach 参数 |
| `services/gateway/connection.py` | ConnectionManager 删 redis_store 参数与 register/unregister 写分支；ConnectionHandler 新增 `current_session_id` 公共 property（`_kick_old` 私有链访问一并改走它）、ConnectionManager 新增 `get_session_id`（§3.3）；删 `_snapshot_session` 钩子 |
| `services/session/manager.py` | 删 redis 参数、resume snapshot 分支、`end` snapshot 删除、`snapshot_on_disconnect`；chat store restore 保留为唯一恢复路径 |
| `services/session/agent_publisher.py` | 实现改为 `bus.publish_nowait`（接口/metrics 不变） |
| `services/agent_relay/publisher.py` | 重写为进程内投递（§3.2.2 + §4.6 背压兜底），帧构造逻辑自 push_subscriber 迁入；`resolve_instance` 删除 |
| `services/agent_relay/session_manager.py` | 新增 `dispatch(event)` 薄方法（原 EventRouter._dispatch 逻辑）；redis_client 参数删除 |
| `services/agent_relay/delivery.py` | 在线判断简化为 `online_check`（删 instance 比对与 resolve_instance 调用） |
| `services/agent_relay/job_status.py` | 同上；`JOB_TTL_S` 常量自 job_store 复用保留 |
| `services/agent_relay/adapters/hermes.py` | `session_redis/session_redis_prefix` → `hermes_store`；`_load/_save/_delete_device_mapping` 改调 ChatStore 方法；`_render_mission` 的 chatlog path 改走 recording_lookup |
| `services/agent_relay/chatlog_watcher.py` | `resolve_chatlog_path` 签名改造；构造参数 `redis_client/redis_prefix` → `recording_lookup` |
| `services/recording/service.py` | 删 redis 相关三分支与 helpers；新增 `current_recording_info`（§3.7，只读） |
| `services/shared/persistence/chat_store.py` | schema 加 `agent_jobs`（§3.5.1，含 idx_aj_task）与 `hermes_device_sessions`（§3.6）两表；新增只读 `get_recording_session`（§3.5.3）+ hermes 三个访问方法（§3.6，均含 device_uuid lower 归一）；messages 日 prune 不触及新表 |
| `services/shared/config/models.py` | `RedisConfig` 保留并标 deprecated 注释（yaml 兼容）；agent_relay config 两 channel 字段标 deprecated（D3） |
| `tools/lvdata-mcp/src/lvdata_mcp/jobs_watch.py` | §3.8.2 重写：ATTACH cs 只读扫描、enrich 两匹配路径全保留落 `agent_job_links`、events 铸造与 **affected 判定按 links 重写**、水位/pending 仍在 lvdata DB 单事务 |
| `tools/lvdata-mcp/src/lvdata_mcp/store.py` | `list_jobs` 改查 cs.agent_jobs LEFT JOIN agent_job_links（§3.8.3）。**2026-09-27 该实现已删除** |
| `tools/lvdata-mcp/src/lvdata_mcp/ddl.py` | BASE_TABLES_SQL 加 `agent_job_links`；移除 `agent_jobs` 建表（存量表冻结不删）；§7 迁移代码不动 |
| `tools/lvdata-mcp/src/lvdata_mcp/config.py` | `DataConfig` 加 `chat_server_db_path` + `resolved_chat_server_db_path`（§3.8.1） |
| `tools/lvdata-mcp/src/lvdata_mcp/tools.py` | list_jobs 描述「Agent Relay 直写（§6.2.0）」更新为新契约 |
| `tools/lvdata-mcp/src/lvdata_mcp/pipeline.py`、`scanner.py` | agent_jobs stage 注释更新（源库变更；stage 名/编排不变） |

### 6.3 删除

| 文件 | 说明 |
|---|---|
| `services/gateway/push_subscriber.py` | 功能迁入 publisher.py |
| `services/agent_relay/router.py` | 分派逻辑并入 AgentSessionManager.dispatch |
| `services/agent_relay/job_persistence.py` | JobLvdataWriter 退役（schema SQL 迁入 chat_store.py；enrich 职责移交 §3.8 agent_job_links） |

### 6.4 保留不使用（req 1）

`services/shared/persistence/redis_client.py`、`redis_store.py`、
`services/agent_relay/job_store.py`（Redis 版 JobStore；常量
`JOB_TTL_S/JOB_SUMMARY_MAX_CHARS/STATUS_*` 被新实现 import 复用，故该文件
实际仍被 import——保留即满足）。`shared/persistence/__init__.py` 的
`RedisStore` re-export 保留。纪律：保留文件不得被任何生产路径调用（import
常量除外）；后续若彻底删除，先删配置字段与依赖。

---

## 7. 配置 / 部署 / 文档联动

- `configs/dev/server.yaml`：删 `redis:` 段（或注释保留）；删除
  `LIVACHAT__REDIS__URL` env 引用。lvdata-mcp 配置加
  `data.chat_server_db_path`（§3.8.1）。
- `Makefile` / `infra/docker/docker-compose.dev.yml`：`server-infra-up` 不再
  启动 Redis 8（infra 目标可保留空壳或删除——另起 PR）；k8s
  `configmap.yaml`/`secret.example.yaml` 去 redis 引用；
  `server.Dockerfile` 移除 pip `redis` 依赖；`pyproject.toml` 移除
  `redis`/`fakeredis`（测试迁移完成后）；副本数约束注明 = 1（D4）。
- **lvdata-mcp 部署约束新增**：与 chat server 同机部署；运行账户对
  chat_server.db 所在目录有写权限（WAL 只读打开需要，§3.8.1）；配置
  `data.chat_server_db_path` 指向 chat server DB 文件。
- **版本组合约束**：server 与 lvdata-mcp 同版本上线，禁止跨版本长期混跑
  （D7）。
- `REPO.md`：「启动本地基础设施 (Redis 8…)」段更新为「无外部基础设施依
  赖（SQLite 文件库）」；`services/README.md` 同步。
- 相关设计文档标注：`fd_agent_relay_service_v2.md`、`fd_job_status.md`、
  `docs/data/lvdata_mcp_service_v2.md`——其中 §6.2.0 契约（「agent_jobs 由
  Agent Relay 直写 lvdata DB」）**发生实质变更**：relay 改为写
  chat_server.db，lvdata-mcp ATTACH 扫描 + `agent_job_links` 承接 enrich
  （本文 §3.8）。各文头部加「已更新，见 chatserver_no_redis.md」一行即可，
  不逐一改写。`tools/lvdata-mcp/README.md` 的配置项说明同步补
  `chat_server_db_path`。

---

## 8. 测试迁移

| 测试 | 处置 |
|---|---|
| `tests/integration/test_redis_store.py` | 保留（RedisStore 源码保留）；CI 无 redis 时整体 `pytest.mark.skipif`（无 REDIS_URL 跳过） |
| `tests/unit/agent_relay/test_job_store*.py`、integration `test_job_pg_integration.py` | 改为 SqliteJobStore + `tmp_path` SQLite（fakeredis 移除）；用例语义不变（CAS/补投含 48h 窗口谓词/watchdog/水位兼容）；新增 `updated_at` 单调性与防御子句用例；新增 **create 时 rsession 解析用例**（命中直填/未命中留 NULL/device_uuid 大小写归一/只读不创建 recording 行） |
| `tests/unit/agent_relay/test_router.py`、`test_session_manager_v2.py`、`test_agent_publisher.py` | EventRouter → InProcessEventBus 用例改写（FIFO、满丢弃按 kind 计数、异常不扩散） |
| `tests/unit/agent_relay/test_publisher.py`、`tests/unit/gateway/test_push_subscriber.py` | 合并改写为 LocalPushPublisher 用例（fake ConnectionManager/handler：在线投递、离线 False、job_key 入史调用、截断）；**新增背压用例**：出站队列积满 + enqueue 阻塞 → wait_for 超时按失败返回、metric 计数 |
| `tests/unit/agent_relay/test_delivery.py`、`test_job_status.py` | online_check 直接注入，删 instance 分支用例 |
| `tests/unit/agent_relay/test_chatlog_watcher.py` | resolve_chatlog_path 改注入 lookup callable |
| `tests/unit/session/test_session_manager_restore.py` | 删 snapshot 用例；chat store restore 用例补强（restored 恒 False、resume_id 延续） |
| `tests/integration/agent_relay/test_application_wiring.py` | 接线断言更新（无 redis_client、bus/publisher/jobstore 类型、readyz 含 chat_store 检查） |
| `tests/e2e-backend/scenarios/test_agent_relay_e2e.py` | 去真实 redis：直接构造 bus + publisher + fake conn_mgr 跑全链路 |
| hermes_device_sessions | 新增用例：save/load/delete 往返、UPSERT 覆盖、**device_uuid 大小写归一**（写入大写、读取小写命中） |
| readyz | 新增用例：chat_store 存活检查通过/失败（503）两路径 |
| lvdata-mcp `tests/`（jobs_watch/store 相关用例） | 源表换库：fixture 改为「tmp chat_server.db 写 agent_jobs + tmp lvdata DB」双库；断言 enrich 两路径落 `agent_job_links`（含 relay 已填行被跳过的 IS NULL 候选过滤）、**affected 判定与现行两并集语义一致**（老行新关联计入 affected）、COALESCE 输出、水位/重扫/force 语义不变；新增 ATTACH 缺文件优雅返回用例 |
| 迁移脚本 | 一次性 dry-run/apply 自校验（行数比对 + in-flight 行存在性抽查 + **水位零干预断言**），随脚本内置 |

**实施前审计项（非测试）**：`publish_*` 全部生产端调用点的线程上下文排查
（§3.2.1 线程约束），结果记录于 PR 描述。

---

## 9. 风险汇总与回滚

### 9.1 风险汇总

| 风险 | 等级 | 影响 | 缓解 |
|---|---|---|---|
| 部署约束变化 | 高 | 多实例能力放弃（单副本）；lvdata-mcp 须与 chat server 同机、对 DB 目录有写权限 | 部署文档显式注明（§7）；k8s replicas=1 |
| agent_jobs 消费契约（lvdata-mcp） | 高 | 表结构/时间戳/水位语义任何偏差 → events 漏铸/重铸 | 契约逐字节沿用既有 schema；双库 fixture 测试覆盖（§8）；水位零干预（§3.8.4） |
| affected 判定重写 | 高 | 老行新关联漏计 → reindex/embed 永不补跑 | §3.8.2 步骤 4 两并集重写 + 双库 fixture 断言语义等价（§8） |
| 版本混跑 | 中 | 旧 server + 新 mcp → 数据源为空；新 server + 旧 mcp → events 断流 | D7 部署顺序约束；同版本上线 |
| 推送背压 | 中 | 客户端卡死时投递协程阻塞 | wait_for 2s 兜底 + metric（§4.6）；单测覆盖 |
| 会话恢复语义变化 | 中 | restored 恒 False、epoch 归零、恢复维度为 device×当日 | 客户端本就兼容 30s 过期路径（D1）；恢复条数反增（≤20） |
| hermes 映射丢失 | 中 | running jobs 被 session_lost fail-fast | 映射同步直写 SQLite（§3.6）；device_uuid 归一纪律；resume-verify 收敛链路不变 |
| readyz 恒真 | 低 | 去 Redis 后就绪探针无实际检查项 | chat_store 存活检查列为必须（§6.2 routes.py） |
| enrich 关联召回 | 低 | 两匹配路径全保留 + relay create 时解析（§3.5.3），召回不低于现状 | 双库 fixture 断言两路径（§8）；unlinked Gauge 监控（§5） |
| `agent_jobs` 历史膨胀 | 低 | 年 <10⁵ 行，无压力 | 后续可按 finished_at 归档（D5） |
| WAL 只读打开失败 | 低 | mcp 启动报 SQLITE_CANTOPEN | 目录权限部署要求（§3.8.1/§7）；缺文件优雅返回 |

### 9.2 回滚策略

- **代码面**：Redis 源码文件全部保留未删（§6.4），各阶段独立 commit，
  `git revert` 恢复 application.py 装配与调用方即可回到 Redis 路径。
- **数据面（P1 切流后回滚）**：切流后新 job 写入 chat_server.db，旧 lvdata
  表与 Redis 均已陈旧。提供反向脚本
  `tools/data-analysis/restore_agent_jobs_from_chat_server.py`（回滚演练时
  编写，与迁移脚本同构）：
  1. `agent_jobs` 行显式列清单反向复制回 lvdata DB（INSERT OR IGNORE；
     lvdata-mcp 旧版继续消费，events 不中断）；
  2. 活跃（requested/running）与 done 未投递 job 按行重建 Redis 运行时状
     态（job HASH + device/session/inflight SET + task 映射，全部字段可从
     agent_jobs 列还原：completed_at←finished_at 等派生规则取
     `_to_row` 逆运算）；
  3. `hermes_device_sessions` 行重建 Redis `agent:hermes:device:{id}:session`
     映射键。
  回滚窗口预期很短，活跃 job 量级小，脚本开销可忽略。
- **lvdata-mcp 单独回滚**：旧版读 lvdata DB `agent_jobs`——执行上述第 1
  步反向同步后切回旧版配置即可。
- **P2/P3 阶段**（总线/推送/会话）无持久态迁移，纯代码回滚。

---

## 10. 实施顺序（建议）

1. **P1 存储底座**：ChatStore 加 `agent_jobs` + `hermes_device_sessions` +
   只读 `get_recording_session`（§3.5.1/§3.5.3/§3.6）→ SqliteJobStore（含
   create 时 rsession 解析、updated_at 防御子句）+ 测试迁移（§3.5）→
   hermes adapter 注入切换 → lvdata-mcp scanner 适配（§3.8，可与 server 侧
   并行；同版本上线，D7）。此步完成后 Redis 仅剩「协调/总线」职责，job
   全链路已可独立验证。**上线前执行 §3.8.4 存量迁移**（in-flight job 连续
   性依赖它）。
2. **P2 进程内通信**：InProcessEventBus + AgentEventPublisher 改写（先做
   §3.2.1 线程约束审计）→ LocalPushPublisher（含 §4.6 背压兜底）+
   PushSubscriber 删除 → recording_lookup 改造 →
   ConnectionManager/SessionManager 去 redis。
3. **P3 接线与配置**：application.py/routes.py（含 readyz chat_store 检
   查）/lifespan.py 收尾、yaml/env；全量 `make server-test` + 端到端联调
   （含 relay 重启恢复演练：running job 中重启 server → 验证 resume +
   background.complete 投递；lvdata-mcp 重扫演练：kill mcp → 写 job →
   重启 mcp → 水位续扫无重复 events；**回滚演练**：P1 切流后执行 §9.2
   反向脚本验证 job 链路连续）。
4. **P4 部署与文档**：infra/Makefile/README/pyproject 联动（§7），各 fd
   文档头部加指引行。
