# Chat Server SQLite 存储重设计

> 状态：设计（reviewed）
> 目标：chat server 的持久存储从 PostgreSQL 切换为单文件 SQLite，只保留
> `devices` / `messages` / `recording_sessions` 三张表；PG 存储模块源码保留但不再
> 使用，仅使用 PG 模块的调用方代码移除。

> **修订（2026-09-15，见 `docs/recording_data_process.md`）**：SQLite 新增
> `recording_files` 表（录制文件登记，lvdata 唯一事实源）——此前「文件级清单由
> 文件树 + lvdata 管线重建」的决策反转：文件树扫描已从 lvdata 删除，登记改由
> chat server 在文件写点经 ChatStore 写队列产生。PG 侧同名的
> `RecordingFile`/`RecordingSession` **死 ORM 与 repository 已删除**；migration
> 0002 因 alembic 链（0001→0002→0003）保留。

## 1. 背景与目标

现状：chat server 通过 `shared/persistence/engine.py`（SQLAlchemy async + asyncpg）写
PostgreSQL 17，覆盖 users/devices/sessions/messages/transcriptions/session_events/
provider_usage/recording_*/agent_jobs 等 12 张表（`shared/models/orm.py`）。

目标：

1. **改用 SQLite** 作为 chat server 唯一 DB 存储；新增独立配置项指定 db 路径
   （如 `chat_server_db_path: /opt/lvdata/chat_server.db`）。**db 路径必须配置、数据
   库必须初始化成功，否则 server 报错退出**（fail-fast，不做降级运行）。
2. **只保留 3 张表**，无其他表、无其他功能：
   - `devices`：`user_id` 可空，TEXT。
   - `messages`：只保存**今天**（本地日）的消息——ASR 结果、LLM 回复、（提交给 LLM
     的）image、agent_job 结果。每行保存消息产生的 UTC 时间戳（`ts_ms`）；"是不是今
     天"由 app 连接后通过 `session.start` 上报的 timezone + `ts_ms` 计算得出（时间
     序列即对话序列）。该表的数据足以 **restore 最近一天的 WorkingMemory
     history**。
   - `recording_sessions`：获取并保持每设备每 UTC 日的 recording session uuid
     （替代现有 PG `recording_sessions` 的唯一职责）。
3. **PG 存储模块退役**：`shared/persistence/` 下 PG 相关源码保留在仓库中但不再被调
   用；只使用 PG 存储模块的调用方代码移除。

非目标：PG 历史数据迁移（messages 只存当天，无迁移价值）；lvdata 底座
（`lvdata_inventory.db`，agent_jobs 直写、BFF 只读）不在本次范围内，保持不变。

---

## 2. 现状盘点（代码调查结论）

### 2.1 PG 写入点（生产代码，全部清单）

| # | 位置 | 写入内容 | 方式 |
|---|------|---------|------|
| 1 | `services/app/application.py:43-49,291-298` | lifespan 创建 `pg_engine` / `pg_session_factory`，挂 `app.state`，attach shutdown | 启动接线 |
| 2 | `services/app/routes.py:32-42` | `/readyz` PG `SELECT 1` 健康检查 | 每请求 |
| 3 | `services/app/lifespan.py:34-39,67-70` | shutdown 时 `pg_engine.dispose()` | 停机 |
| 4 | `services/gateway/endpoint.py:117,138` | 把 `pg_session_factory` 注入 Dialog/Omni orchestrator | 每连接 |
| 5 | `services/session/orchestrator.py:218-249` | session.start 时 **await** `UsersDevicesRepository.upsert_user/upsert_device`，写 users+devices，设置 `ctx.device_db_id`，dev 模式下改写 `ctx.user_id` 为 PG user UUID | 同步 await |
| 6 | `services/session/orchestrator.py:1411-1448` (`_persist_session`) | sessions 行（session.start 后，L274 调用） | `persist_async` fire-and-forget |
| 7 | `services/session/orchestrator.py:1450-1477` (`_persist_transcription`) | transcriptions 行/ASR final（L721、L1349 调用） | 同上 |
| 8 | `services/session/orchestrator.py:1552-1585` (`_persist_messages`) | messages 2 行/轮（user+assistant，LLM final 时 L969 调用） | 同上 |
| 9 | `services/session/orchestrator.py:1479-1510` (`_persist_event`) | session_events 行（status_change/interrupt，L457/762/795/1061/1093/1193 等调用） | 同上 |
| 10 | `services/session/orchestrator.py:1512-1541` (`_persist_usage`) | provider_usage 行（asr/llm/tts，L726/972/1054/1351 等调用） | 同上 |
| 11 | `services/session/omni_orchestrator.py:156-185,747-910` | 同 5-10（omni 管线自己的一套 `_persist_*` + device upsert） | 同上 |
| 12 | `services/session/lecture_translate.py:217-308,417-530` | lecture 翻译管线的 `_persist_transcription/_event/_usage/_messages`（assistant only） | 同上 |
| 13 | `services/recording/service.py:77,89,150-203,239-310` | `RecordingRepository`：`_find_or_create_in_pg`（recording_session_id 的**事实来源**）、`_recover_from_pg`（Redis 命中后的恢复）、`_sync_pg_stats`/`_sync_pg_finalize`（周期/收尾统计） | await |
| 14 | `tools/data-analysis/lvdata_sync_agent_jobs.py:156-165` | 旧 agent_jobs PG→lvdata 同步工具（读 `LIVACHAT__POSTGRES__DSN`）；**已被** `agent_relay/job_persistence.py` 的 lvdata 直写取代 | 离线工具 |

### 2.2 Repository 真实使用情况（重要）

`shared/persistence/repositories/` 共 8 个 repository，**生产代码只使用其中 2 个**：

- 生产使用：`UsersDevicesRepository`（orchestrator ×2）、`RecordingRepository`（recording service）
- **仅测试使用**：`SessionRepository`、`MessageRepository`、`TranscriptionsRepository`、
  `SessionEventsRepository`、`ProviderUsageRepository`（`tests/integration/test_pg_repositories.py`）、
  `AgentJobsRepository`（`tests/integration/test_agent_jobs_repository.py`）

orchestrator 的 `_persist_*` 直接实例化 ORM 类 + `persist_async()`（`shared/persistence/
helpers.py`，有界任务集上限 200，失败只 log + `livachat_errors_total{code="persist"}`），
不经过 repository。

### 2.3 与 history 的一致性语义（新表设计的事实依据）

- `WorkingMemory` 条目结构（`session/history.py:19-30`）：
  `{role, content, images?, source?, ref_id?}`。
- **images 挂图点**〔2026-09-11 变更，原为"LLM 首 token 提交点"〕：**ASR final（AF）
  分发处理时刻**把 `data:image/jpeg;base64,...` data URL 列表写入**本轮 user 条目**的
  `images`——user input 含画面是独立事实，与请求成败解耦（docs/fd_pipeline_llm_v2.md
  §9.2）。restore 一致性 ⇒ messages 表的 user 行在同一点随 INSERT 带上同样的
  images（不再有二次 UPDATE）。
- **agent_job 结果**（`session/agent_job_ingest.py:61-72`）：history 注入形态为
  `role="assistant"`、`content = "[Agent job result]\n" + 截断文本`、`source="agent_job"`、
  `ref_id=message_id`，并以 `has_agent_job(message_id)` 去重。
- 当前 restore 路径：`SessionManager.start` 的 resume 分支从 Redis snapshot（30s TTL）
  恢复最近 6 条（`session/manager.py:75-90`）。新表提供的是**跨进程、跨天的补充
  restore 来源**，不改变 Redis resume 语义。
- timezone：`session.start` 上报 → `canonicalize_timezone` 规范形 →
  `SessionContext.timezone`（无效/缺失为空串）（`session/manager.py:93-113`、
  `shared/timeutil.py`）。"今天"的判定在**读取/prune 侧**用该时区 + `ts_ms` 换算
  （`resolve_tzinfo`，空串回退 UTC），messages 行不存时区/本地日。
- recording 的日界是 **UTC 自然日**（`recording/service.py:18-20` `_utc_today`，与客
  户端时区无关），`recording_sessions` 表沿用同一语义。

### 2.4 现有 SQLite 工程契约（新代码必须遵循）

`agent_relay/job_persistence.py`（JobLvdataWriter）已确立仓库内 SQLite 写入契约：

1. 有界 `asyncio.Queue` + 单 writer task；同步钩子 `put_nowait` O(1)；队列满 → 丢弃 +
   metric，永不阻塞热路径；
2. `PRAGMA journal_mode=WAL` + `busy_timeout ≥ 5000`：WAL 是库级持久属性，**只在 init
   时设一次**（重复设置会与其他写者争抢独占锁，`job_persistence.py:103-111` 教训）；
   每个写连接都设 `busy_timeout`；
3. schema 用 `CREATE TABLE/INDEX IF NOT EXISTS` 幂等建；
4. 时间戳一律规范格式：ISO 8601 UTC、`'T'`、毫秒、`'Z'`（`_dt()`，L225-230）；
5. 停机限时排空（`stop(drain_timeout_s=5.0)`）。

### 2.5 配置现状

- `AppConfig.sqlite`（`shared/config/models.py:136-143`）已有：`enabled` /
  `lvdata_db_path` / `max_queue_size`，对应 `configs/dev/server.yaml:209-213`。
- `AppConfig.postgres`（`shared/config/models.py:125-128`）：`dsn/pool_min/pool_max`，env
  `LIVACHAT__POSTGRES__DSN`。

---

## 3. 目标设计

### 3.1 配置（fail-fast）

在**现有 `sqlite:` 段**新增一项（命名对齐 `lvdata_db_path`；即需求中的
`chat_server_dbpath`）：

```yaml
# configs/dev/server.yaml
sqlite:
  enabled: true
  lvdata_db_path: /opt/lvdata/lvdata_inventory.db
  chat_server_db_path: /opt/lvdata/chat_server.db   # 新增，必填
  max_queue_size: 1024
```

```python
# shared/config/models.py — SqliteConfig 新增字段
chat_server_db_path: str = ""   # chat server SQLite 路径；空 = 启动报错
```

env 覆盖：`LIVACHAT__SQLITE__CHAT_SERVER_DB_PATH`（BaseSettings 嵌套分隔符 `__` 已有）。

**启动纪律（fail-fast）**：lifespan 启动时校验并初始化，任一失败即抛异常，uvicorn
启动失败、进程退出：

1. `chat_server_db_path` 为空 → 报错退出；
2. db **父目录不存在** → 报错退出（不自动 `mkdir`：防止 lvdata 挂载缺失时静默写到
   错误文件系统）；
3. db 初始化（WAL + busy_timeout + schema）失败 → 报错退出。

运行时写路径仍为 best-effort（有界队列，见 §3.3）：启动时已验证 db 可写，运行期单
条失败只 log + metric，不阻断实时链路。

### 3.2 Schema（仅 3 表）

```sql
CREATE TABLE IF NOT EXISTS devices (
    device_uuid    TEXT PRIMARY KEY,        -- 客户端 UUID v4（ws 连接 query 参数）
    user_id        TEXT,                    -- 可空；principal.user_id 原文，不做 UUID 改写
    platform       TEXT,
    client_version TEXT,
    timezone       TEXT NOT NULL DEFAULT '', -- 最近一次 session.start 的规范形时区（诊断 + prune 缺省时区来源）
    created_at     TEXT NOT NULL,            -- ISO 8601 UTC 'T' ms 'Z'
    last_seen_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- 插入序即对话序（同日跨 session 全局有序）
    device_uuid TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,               -- 'user' | 'assistant'
    kind        TEXT NOT NULL,               -- 'asr' | 'llm' | 'agent_job'
    content     TEXT NOT NULL DEFAULT '',    -- asr: final 文本; llm: full_text; agent_job: history 形态（前缀+截断）
    images_json TEXT,                        -- JSON array of data URL；仅 user 行随行挂图（AF 挂图）；无图 NULL
    ref_id      TEXT NOT NULL DEFAULT '',    -- llm: message_id; agent_job: message_id（restore 去重）; asr: ''
    ts_ms       INTEGER NOT NULL,            -- 消息产生的 UTC epoch 毫秒（now_ms()）
    utc         TEXT NOT NULL                -- ts_ms 的规范时间戳（ISO 8601 UTC 'T' ms 'Z'）
);
CREATE INDEX IF NOT EXISTS idx_messages_device_ts ON messages(device_uuid, ts_ms, id);
CREATE INDEX IF NOT EXISTS idx_messages_session   ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS recording_sessions (
    device_uuid          TEXT NOT NULL,
    session_date         TEXT NOT NULL,     -- UTC 日 'YYYY-MM-DD'（沿用 recording UTC 日界语义）
    recording_session_id TEXT NOT NULL,     -- uuid7
    base_path            TEXT NOT NULL,     -- <base_dir>/<device_uuid>/<recording_session_id>
    created_at           TEXT NOT NULL,     -- ISO 8601 UTC 'T' ms 'Z'
    PRIMARY KEY (device_uuid, session_date)
);
```

设计要点：

- **messages 不存 timezone / local_date**：app 连接后由 `session.start` 的 timezone
  （`SessionContext.timezone`，规范形）+ `ts_ms` 在查询/prune 时换算"今天"；行数据
  保持与 history 条目一一对应的最小集合。
- **role 与 kind 冗余存一份**：`asr→user`、`llm/agent_job→assistant` 可互相推导，但
  restore 时直接映射 `WorkingMemory.add_message(role, ...)`，省一层分支。
- **images 内联 data URL**（与 history 逐字一致）：history 的 `images` 就是
  `data:image/jpeg;base64,...` 列表，restore 要求原样返回，故 `images_json` 直接存
  JSON 数组。当前 `session.max_video_frames=1`（`SessionConfig`），单条约几十~两百
  KB，且只存当天，体量可接受。备选（不采纳）：存 image_id 引用 recording
  `video/<uuid7>.jpg`——restore 需二次 IO 且与 history 不一致。
- 无 `users` 表：`devices.user_id` 直接存 `principal.user_id` 原文（dev 模式
  `"dev-user"`、JWT sub 或空 → NULL）。现有"dev 模式下把 `ctx.user_id` 改写为 PG
  user UUID"的逻辑（`orchestrator.py:230-235`）随之删除。
- `recording_sessions` 只承担"获取并保持 recording session uuid"（get-or-create，
  按 `(device_uuid, session_date)` 幂等）；统计/状态字段不落此表（权威侧是
  `session.json` + lvdata-mcp inventory，见 §4.3），行不 prune（每设备每日 1 行，
  体量可忽略）。

### 3.3 ChatStore（新模块）

新文件 `services/shared/persistence/chat_store.py`，遵循 §2.4 契约：

```python
class ChatStore:
    def __init__(self, db_path: str, *, max_queue: int = 1024): ...

    async def start(self) -> None:
        """校验父目录 → _init_db（WAL+busy_timeout+schema，一次）→ _prune() → writer task。
        任一失败抛异常（fail-fast，§3.1）。"""

    async def stop(self, *, drain_timeout_s: float = 5.0) -> None:
        """限时排空 → cancel writer。"""

    # ---- 同步钩子（热路径调用，O(1)，永不抛）----
    def on_device(self, *, device_uuid: str, user_id: str | None,
                  client_version: str, timezone: str) -> None: ...
    """devices upsert；同时更新 self._last_tz（prune 缺省时区来源）。"""

    def on_message(self, *, device_uuid: str, session_id: str, kind: str,
                   role: str, content: str, ref_id: str = "", ts_ms: int,
                   images: list[str] | None = None) -> None: ...
    """images（可选，仅 user 行）：随 INSERT 写 images_json；None/空 → NULL。
       〔2026-09-11 变更〕原 on_images_attached（提交点 UPDATE 最新 user 行）
       已删除——挂图点前移至 AF（ASR final）分发处理时刻，行与图同刻写入
       （docs/fd_pipeline_llm_v2.md §9.2）。"""

    # ---- recording_sessions（await，非热路径；每 WS 连接至多一次）----
    async def get_or_create_recording_session(
        self, *, device_uuid: str, session_date: str, recording_session_id: str,
        base_path: str) -> str:
        """按 (device_uuid, session_date) INSERT OR IGNORE 后 SELECT，
        返回权威 recording_session_id。to_thread 执行。"""

    async def get_recording_session(self, *, device_uuid: str,
                                    session_date: str) -> dict | None:
        """Redis 恢复分支的校验查询（§4.3）。"""

    # ---- 读取（restore，Phase 2；同步 sqlite3 ro 连接，调用方走 to_thread）----
    def load_day(self, *, device_uuid: str, timezone: str,
                 limit: int = 20) -> list[dict]: ...
    """今天 = timezone（规范形，'' 回退 UTC）下的本地日；换算 [start_ms, end_ms)
    后按 ts_ms 范围查询：SELECT ... WHERE device_uuid=? AND ts_ms>=? AND ts_ms<?
    ORDER BY id。"""
```

- writer task 循环用 `asyncio.wait_for(queue.get(), timeout=3600)` 实现每小时 prune
  tick（超时不视为错误）。
- 运行时失败一律 log + metric（复用 `livachat_errors_total{code="persist"}` 或新增
  `chat_store` label），不抛出、不影响实时链路。
- 读连接：`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`（对齐 BFF 只读模式）。

### 3.4 写入接线（替换 §2.1 的 PG 写入点）

| 时机（与 history 同点） | 调用 | 说明 |
|---|---|---|
| session.start 完成后（`orchestrator.py` 原 device upsert 块 L218-249、`omni_orchestrator.py` L156-185） | `chat_store.on_device(...)` | 替换原 **await** 的 PG upsert；不再需要同步等待 |
| ASR final，`history.add_message("user", text, images=data_urls)` 同点（`orchestrator.py` 路径 A/B、`omni_orchestrator.py`） | `on_message(kind='asr', role='user', content=final_text, ts_ms=now_ms(), images=data_urls)` | 打断（LLM 未完成）时 user 消息仍在——与 history 一致；图随行写入（AF 挂图，〔2026-09-11〕原为提交点 UPDATE） |
| LLM final，`history.add_message("assistant", full_text)` 同点（`orchestrator.py:968`、`omni_orchestrator.py:595`） | `on_message(kind='llm', role='assistant', content=full_text, ref_id=message_id)` | |
| agent_job 结果注入成功（`agent_job_ingest.py:68-72`，dedup 通过的分支） | `on_message(kind='agent_job', role='assistant', content=前缀+截断形态, ref_id=message_id)` | 存 history 形态；restore 可直接重放且 `has_agent_job` 去重语义不变 |

> **lecture translate（2026-10-27 起）不写 `messages`**：`LectureTranslateEngine` 不再接收
> `history` / `chat_store`，只保留 `chat/translate.jsonl`（`source="lecture"`）。见
> [`fd_pipeline_translate_lecture.md`](fd_pipeline_translate_lecture.md) §8.1。

orchestrator 持有 `chat_store`（构造函数注入，替换 `pg_session_factory` 参数），
`agent_job_ingest.ingest_agent_job_result` 增加可选 `chat_store` 参数（由
`record_agent_job_result` 传入）。

### 3.5 保留策略（"只存今天"）

`local_date` 不物化，prune 在运行时换算：

```sql
-- 启动时 + writer 每小时 tick：
--   cutoff = 今天 00:00（本地日起点）的 UTC ms，时区取 self._last_tz
--   （最近一次 on_device 上报的 session.start timezone；无任何连接前回退 UTC）
DELETE FROM messages WHERE ts_ms < ?;
```

时区切换的边界语义：设备跨时区后，"今天"按最新 session.start 的时区判定，旧时区下
属于"今天"但新时区下属于"昨天"的消息会被提前 prune——可接受（工作记忆只需最近一
天，且 `devices.timezone` 保留最后上报值可作诊断对照）。

`devices` / `recording_sessions` 不 prune。

### 3.6 读取：restore 最近一天 history（Phase 2 预留）

`messages` 表的设计使以下 restore 成为可能（本期只做存储，接线留待 Phase 2）：

- Hook：`SessionManager.start` 非 resume 分支（Redis snapshot 恢复优先，语义不变）。
- 查询：`load_day(device_uuid=..., timezone=ctx.timezone, limit=working_memory_turns*2)`
  ——今日范围由 session.start 的 timezone + `ts_ms` 现算（§3.3）。
- 映射：
  - `kind='asr'` → `history.add_message("user", content, images=json.loads(images_json) or None)`
  - `kind='llm'` → `history.add_message("assistant", content)`
  - `kind='agent_job'` → `history.add_message("assistant", content, source="agent_job", ref_id=ref_id)`

### 3.7 lifespan / readyz 接线

- `application.py`：删除 `create_engine/create_session_factory` 块与
  `app.state.pg_engine/pg_session_factory`；新增
  `chat_store = ChatStore(cfg.sqlite.chat_server_db_path, max_queue=cfg.sqlite.max_queue_size)`
  + `await chat_store.start()`（**不 try/except**：失败即启动失败退出，§3.1），挂
  `app.state.chat_store`。shutdown 顺序：先停写入来源（连接随 `conn_mgr.shutdown()`
  关闭），再 `chat_store.stop()` 排空——与 `job_pg_writer` 的顺序纪律一致
  （`application.py:327-332` 注释）。
- `routes.py /readyz`：移除 postgres 检查块，**不新增** sqlite 检查（已决：不需要）。
- `lifespan.py`：`GracefulShutdown` 删除 `pg_engine` attach/dispose。

---

## 4. PG 退役方案

### 4.1 保留但不再使用的源码（pgsql 存储模块）

| 保留项 | 说明 |
|---|---|
| `services/shared/persistence/engine.py` | `create_engine/create_session_factory` |
| `services/shared/persistence/helpers.py` | `persist_async`（唯一消费者是 PG `_persist_*`，随之闲置） |
| `services/shared/persistence/repositories/`（8 个） | 含生产在用的 `users_devices.py`、`recording.py`——接线移除后整体闲置 |
| `services/shared/models/orm.py` | 12 表 ORM |
| `services/migrations/`、`services/alembic.ini` | Alembic 迁移 |
| `PostgresConfig`（`shared/config/models.py:125-128`）及 `server.yaml` 的 `postgres:` 段 | 保留解析，不再消费（避免配置-breaking） |
| `infra/docker/docker-compose.dev.yml` 的 postgres 服务、`Makefile server-infra-up` | infra 源码保留原样（已决）；dev 不再需要 PG 容器，文档注明即可 |
| `tools/data-analysis/lvdata_sync_agent_jobs.py` | PG→lvdata 同步工具：保留源码，PG 停写后无数据源，自然废弃 |

`shared/persistence/__init__.py` 的导出保留（其中 `redis_client/redis_store` 仍在使
用）。

### 4.2 移除的调用方代码（仅使用 PG 存储模块的部分）

**生产代码：**

1. `app/application.py`：§3.7 所述 engine/factory 创建、`app.state.pg_*`、
   `handler.attach(pg_engine=...)`、`RecordingService(pg_session_factory=...)` 参数。
2. `app/routes.py`：`/readyz` postgres 检查块（L32-42）。
3. `app/lifespan.py`：`_pg_engine` 字段、attach 参数、dispose 块（L34-39,67-70）。
4. `gateway/endpoint.py`：orchestrator 构造的 `pg_session_factory=` 实参（L117,138），
   改为 `chat_store=ws.app.state.chat_store`（fail-fast 后必然存在，无需 getattr 兜
   底）。
5. `session/orchestrator.py`：device upsert 块（L215-249）替换为 `on_device`；
   `_persist_session/_persist_transcription/_persist_event/_persist_usage/_persist_messages`
   五个方法及其全部调用点删除，按 §3.4 替换为 chat_store 钩子；
   `UsersDevicesRepository` import 删除。
6. `session/omni_orchestrator.py`：同上（L156-185 upsert；L747-910 五个 `_persist_*`）。
7. `session/lecture_translate.py`：`pg_session_factory` 构造函数参数 + 四个
   `_persist_*`（L417-530）及调用点，按 §3.4 替换。
8. `session/context.py`：`device_db_id` 字段删除（唯一写入方是 PG device upsert）。
9. `recording/service.py`：`pg_session_factory` 构造参数改为 `chat_store`；
   `_find_or_create_in_pg`、`_recover_from_pg`、`_sync_pg_stats`、`_sync_pg_finalize`
   按 §4.3 替换/删除。

**测试代码：**

10. 删除（只测 PG 模块）：`tests/integration/test_pg_repositories.py`、
    `tests/integration/test_agent_jobs_repository.py`、
    `tests/integration/test_alembic_upgrade.py`。
11. `tests/integration/conftest.py`：`pg_dsn/pg_engine/pg_session` fixtures；
    `tests/integration/agent_relay/test_application_wiring.py:139` 的 dsn 构造（且
    wiring 测试需改为传入 tmp 路径的 `chat_server_db_path`，否则启动即退出）；
    `tests/e2e-backend/conftest.py:41`、`tests/integration/gateway/conftest.py:27` 的
    `LIVACHAT__POSTGRES__DSN` 环境变量（保留无害，随接线清理）。
12. 传 `pg_session_factory=None` 的单测随构造函数签名更新：
    `tests/unit/session/test_translate_mode.py`、`test_image_history.py`、
    `test_lecture_translate.py`、`test_agent_job_ingest.py`。

### 4.3 recording service 的 PG 依赖替换（recording_sessions 表）

recording 当前把 PG 当作 `recording_session_id` 的事实来源与跨重启恢复手段；改由
chat server SQLite 的 `recording_sessions` 表承担（§3.2）：

| 原 PG 逻辑 | 替换 |
|---|---|
| `_find_or_create_in_pg`（service.py:263-310） | `chat_store.get_or_create_recording_session(device_uuid, session_date=str(_utc_today()), recording_session_id=uuid7(), base_path=...)`：表内已有今日行 → 返回权威 id 重建 `RecordingSession`；无 → 插入并返回新 id。目录命名规则不变 |
| `_recover_from_pg`（L239-261，Redis L1 ② 命中后） | `chat_store.get_recording_session(device_uuid, session_date=today)`，校验表行 `recording_session_id` 与 Redis 值一致后按 `base_path` 重建；不一致/无行 → 走 get-or-create 分支 |
| `_sync_pg_stats` / `_sync_pg_finalize`（L166-203） | **删除**。`_write_session_json` 已是周期（ticker）与 finalize 的本地落盘；stats 的权威分析侧是 lvdata-mcp inventory（`session.py:399-400` 注释已声明此处不再记录） |

Redis 缓存层（`_redis_get/_update_redis`，L1 ②）与 ticker 的 rollover/idle 逻辑不变；
finalize 时不回写 `recording_sessions` 表（表只负责 uuid 的获取与保持，不跟踪状态）。

---

## 5. 兼容、回滚与风险

- **数据迁移**：不做。messages 只存当天，切换日即自然生效；PG 旧数据留在原库，pg
  存储模块源码未动。
- **回滚**：接线改动为纯调用方变更，`git revert` 即恢复 PG 写入（PG 模块/迁移/compose
  均未删除）。
- **风险与缓解**：
  1. *SQLite 成为启动硬依赖*：db 路径未配/父目录缺失/init 失败 → server 退出（设
     计意图，fail-fast）。部署需保证 `/opt/lvdata` 已挂载（与 `recording.base_dir`
     同一前提）。启动成功后运行期单条写入失败仍为 best-effort。
  2. *多进程写同一 SQLite 文件*：chat server 单进程（`server.workers=1`），writer 单
     task；WAL + busy_timeout 覆盖 lvdata-mcp 等外部只读者的并发。
  3. *images 内联体积*：单帧上限已限定（`max_video_frames=1`），prune 保证只留当
     天；DB 体积量级 ≈ 消息文本 + 当日图片，可接受。
  4. *prune 时区*：取最近一次 session.start 上报时区（`devices.timezone` 同值可
     对照）；长时间无连接时回退 UTC，边界最多偏差 ±14h 内的一批消息——可接受
     （保留语义本就是"最近一天"而非精确日历日）。

## 6. 实施步骤

1. **P1 存储切换**：`SqliteConfig.chat_server_db_path` + yaml；`ChatStore`（含
   fail-fast start、prune、recording_sessions get-or-create）；orchestrator/omni/
   lecture/agent_job_ingest 写入接线；recording service 改造（§4.3）；
   lifespan/readyz/endpoint 接线；§4.2 调用方与测试清理；`ChatStore` 单测（启动
   fail-fast 各分支、队列背压、prune cutoff、随行挂图（on_message images）、load_day 时区换算、
   recording get-or-create 幂等）。
2. **P2 restore**：`SessionManager.start` 非 resume 分支加载 `load_day` →
   WorkingMemory（`load_day` 已在 P1 交付并单测）。
3. **P3 文档收尾**：`docs/server_dev.md`、`REPO.md`、`services/README.md` 中 PG/alembic
   相关段落标注"PG 模块保留未用、dev 无需起 PG 容器"；
   `docs/fd_agent_job_pg_persistence.md` 标注被本文件取代。

## 7. 已决事项

1. `devices.timezone`：**保留**（诊断 + prune 缺省时区来源）。
2. `/readyz`：**不新增** sqlite 检查，只删除 postgres 检查。
3. `Makefile server-infra-up` / compose 文件：**保留原样**，仅文档注明 dev 可不再
   起 PG 容器。
