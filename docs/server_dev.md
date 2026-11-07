# LivaChat Backend Server 开发计划 (AI Agent 工作手册)

**版本**: v1.0
**日期**: 2026-06-13
**对应设计**: [`docs/server_design.md`](./server_design.md)（Phase 1 / MVP）
**目标读者**: 实施 Backend Server 的 AI Agent / 工程师
**关联协议**: [`docs/ws-protocol-design.md`](./ws-protocol-design.md)（契约标准，不可单方面修改）
**协议 SSOT**: [`packages/api-schema/`](../packages/api-schema/) → 生成 [`packages/sdk-python`](../packages/sdk-python/)

---

## 0. 如何使用本文档

本文档把 `docs/server_design.md`（Phase 1 单体后端设计）拆分为 **可由 AI Agent 顺序执行、逐步验证** 的工程计划。每个 Phase 是一个独立工作单元，包含：

- **目标** —— 一句话说明
- **前置依赖** —— 必须先完成的 Phase
- **交付物** —— 具体文件路径与产物
- **实现要点** —— 给 AI Agent 的关键提示，避免常见坑
- **测试覆盖矩阵** —— 强制要写的测试用例
- **验证命令** —— 可直接 `bash` 执行的检查
- **完成定义 (DoD)** —— 必须全部满足才能进入下一 Phase

### 0.1 AI Agent 通用工作守则

1. **逐 Phase 推进**：不跳步、不并行。每个 Phase 完成后运行该 Phase 的"验证命令"，全绿才进入下一个。
2. **设计文档是契约**：`server_design.md` / `ws-protocol-design.md` / `packages/api-schema/` 三者不可单方面修改。如需偏离，必须先在 `docs/adr/` 写 ADR 并请求人审。
3. **小步提交**：每个 sub-task 一次 commit；commit message 前缀 `[server][phase-N]`。
4. **测试先行**：先写测试，再写实现，再 refactor。所有 I/O（PG / Redis / Provider HTTP）必须有 fake / stub 替身。
5. **协议 DTO 强制复用**：所有 WS 消息的解析/构造**必须**通过 `from livachat_protocol import ...`（`packages/sdk-python`），禁止本地重复定义。
6. **依赖最小化**：仅使用 §1.4 列表中允许的依赖；新增依赖必须在 PR 描述说明理由并经审批。
7. **密钥零硬编码**：所有 provider `api_key` / 数据库 DSN 只从环境变量读取；YAML 中用 `${ENV_VAR}` 占位。日志/错误信息不得出现 API key 明文。
8. **遇到设计模糊**：立刻停下并在 PR/issue 中提问，**不要**自行扩展协议或改 schema。

### 0.2 完成定义 (DoD) 通用条款

每个 Phase 的 DoD 默认包含以下条款，子条款会在 Phase 内额外列出：

- [ ] 所有新代码有对应单元测试，分支覆盖率 ≥ 80%
- [ ] `pytest services/` 全绿（含本 Phase 新增用例）
- [ ] `ruff check services/` 无 E/F 级错误，`ruff format --check services/` 一致
- [ ] `mypy services/` 在 `--strict` 选项关键模块（`gateway/`, `session/`, `asr/`, `llm/`, `tts/`）下无 error
- [ ] 公开类与函数加 docstring（Google style）；`_internal` 不强求
- [ ] 该 Phase 的"验证命令"全部成功
- [ ] 不引入新的依赖（除非在 PR 描述中说明并通过审批）

### 0.3 与设计文档章节对照（速查）

| 设计文档章节 | 实现 Phase |
|--------------|-----------|
| §1 Server 描述 / §1.4 非职责 | 全 Phase 约束 |
| §2 架构、§11 目录结构 | Phase 0 / Phase 9 wiring |
| §3.2 Gateway / §9 鉴权 | Phase 3 |
| §3.3 Session + §4 主流程 | Phase 8 / Phase 9 |
| §3.5 Media | Phase 4 |
| §5.2 ASR Provider | Phase 5 |
| §5.3 LLM Provider | Phase 6 |
| §5.4 TTS Provider | Phase 7 |
| §6 配置 | Phase 1 |
| §7 PG Schema | Phase 2 |
| §8 Redis Key | Phase 2 / Phase 8 |
| §10 非功能 / 可观测性 | Phase 1（基线）/ Phase 11 |
| §12 协议消息映射 | Phase 3（解析）/ Phase 9（编排） |

---

## 1. 项目总览与里程碑

### 1.1 路线图

```
Phase 0   Bootstrap (项目骨架 / 依赖 / 本地基建)         ────► 0.5 day
Phase 1   Config & Observability (配置 / 日志 / metrics) ────► 1   day
Phase 2   Persistence (PG schema + Redis store)         ────► 1.5 day
Phase 3   Gateway (WS 端点 / Auth / Codec / Seq / HB)    ────► 2   day
Phase 4   Media (Opus / Resample / JPEG / Buffer)       ────► 1   day
Phase 5   ASR Providers (接口 + 3 实现 + failover)       ────► 2   day
Phase 6   LLM Providers (接口 + 3 实现 + Vision)         ────► 1.5 day
Phase 7   TTS Providers (接口 + 4 实现)                  ────► 2   day
Phase 8   Session core (Manager / Context / SM / Turn / Interrupt) ────► 2 day
Phase 9   Dialog Orchestrator (核心编排, 端到端可对话)    ────► 2.5 day
Phase 10  E2E with sdk-python client + Load test        ────► 2   day
Phase 11  Hardening / Deploy / Runbook                  ────► 1.5 day
─────────────────────────────────────────────────────────────────────
TOTAL                                                       ≈ 19.5 working days
```

> Phase 4 可与 Phase 3 部分并行（无强依赖），Phase 5/6/7 可三人/三轮并行（接口固化后）。本计划默认单一 AI Agent 串行推进，时间上保守估算。

### 1.2 关键参考资料

| 资源 | 路径 / URL |
|------|-----------|
| 设计契约 | `docs/server_design.md` |
| WebSocket 协议 | `docs/ws-protocol-design.md` |
| API JSON Schema | `packages/api-schema/` |
| 已生成 Python DTO | `packages/sdk-python/src/livachat_protocol/generated/` |
| Mock Server（仅参考 / 不是实现） | `tools/local-dev/mock-services/livachat_mock_server/` |
| PRD | `docs/livachat_prd.md` |
| UX | `docs/livachat_ux.md` |
| Android SDK 设计 | `docs/android_sdk_design_v3.md` |
| Coding standards | `agents/rules/coding-standards.md` |
| Architecture decisions | `agents/rules/architecture-decisions.md` |

### 1.3 工具链版本

| 工具 | 版本 |
|------|------|
| Python | 3.12+ |
| FastAPI | 0.115+ |
| uvicorn[standard] | 0.32+（含 uvloop / httptools / websockets） |
| pydantic | 2.x |
| pydantic-settings | 2.x |
| SQLAlchemy | 2.0+ (async) |
| asyncpg | 0.29+ |
| redis (redis-py) | 5.x（asyncio API） |
| PostgreSQL | 17 |
| Redis | 8.x |
| OpenAI SDK | 1.x（用于 OpenAI 兼容的 LLM provider） |
| pytest / pytest-asyncio | 8.x / 0.23+ |
| ruff | 0.6+ |
| mypy | 1.11+ |

### 1.4 允许的依赖清单

```toml
# services/pyproject.toml (节选)
[project]
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2,<3",
    "pydantic-settings>=2",
    "websockets>=13",
    "httpx>=0.27",                    # 通用 HTTP / SSE / streaming
    "openai>=1.40",                   # OpenAI-compatible LLM 客户端
    "sqlalchemy>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",                  # PG 迁移
    "redis>=5",                       # asyncio
    "pyyaml>=6",
    "structlog>=24",
    "prometheus-client>=0.20",
    "opentelemetry-api>=1.27",        # tracing 预留，可暂不启用
    "soxr>=0.5",                      # 重采样
    "pyogg>=0.6.14a1 ; platform_system != 'Windows'",   # 可选；与 opuslib 二选一
    "opuslib>=3.0",                   # 默认 Opus 实现
    "Pillow>=10",                     # JPEG decode
    "PyJWT>=2.9",                     # jwt 模式预留
    "livachat-protocol",              # editable install from packages/sdk-python
]

[project.optional-dependencies]
test = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5",
    "respx>=0.21",                    # mock httpx
    "fakeredis>=2.24",                # asyncio aware
    "freezegun>=1.5",
    "anyio>=4",
]
dev = [
    "ruff>=0.6",
    "mypy>=1.11",
    "types-PyYAML",
    "ipython",
]
```

> 任何超出本清单的依赖必须在 PR 描述写明理由并经审批。

---

## Phase 0 — Workspace Bootstrap

### 目标
建立 `services/` 单体后端工程骨架，可一键 `pytest`、`uvicorn` 起空 ASGI 应用、本地 PG/Redis 容器跑通。

### 前置依赖
无

### 交付物

```
services/
├── pyproject.toml                   # 见 §1.4 依赖清单
├── README.md                        # 本地起服务步骤
├── alembic.ini                      # Alembic 配置（数据库迁移）
├── .env.example                     # 环境变量模板（不含真值）
│
├── app/
│   ├── __init__.py
│   ├── main.py                      # uvicorn 入口（仅 `from app.application import create_app`）
│   ├── application.py               # FastAPI app 工厂 + lifespan + DI 容器装配（Phase 0 为占位）
│   └── routes.py                    # /healthz / /readyz / /metrics 占位
│
├── gateway/  __init__.py            # 各 module 仅占位 __init__.py
├── session/  __init__.py
├── asr/      __init__.py
├── llm/      __init__.py
├── tts/      __init__.py
├── media/    __init__.py
├── shared/
│   ├── __init__.py
│   ├── config/__init__.py           # 占位
│   ├── persistence/__init__.py      # 占位
│   ├── observability/__init__.py    # 占位
│   ├── models/__init__.py           # 占位
│   └── utils/__init__.py            # 占位
│
└── tests/
    ├── conftest.py                  # 全局 fixture 占位
    ├── unit/
    └── integration/

configs/dev/
├── server.yaml                      # Phase 1 完整化；Phase 0 写最小占位
└── prompts/companion.txt            # LLM system prompt（占位）

infra/docker/
├── server.Dockerfile                # 占位（Phase 11 完善）
└── docker-compose.dev.yml           # postgres:17 + redis:8 + 可选 server
```

### 实现要点

- `services/` 作为单一 Python package 顶层（`pyproject.toml` 的 `[tool.setuptools.packages.find]` 指向 `services/`，安装名 `livachat-server`）。
- `app/application.py` 中先实现最小 `create_app() -> FastAPI`，注册 `/healthz` 返回 `{"ok": true}`，**不**注册业务路由。
- `pyproject.toml` 通过 `[tool.setuptools]` 或 `[tool.uv]`/`pip install -e packages/sdk-python` 引入 `livachat-protocol`（与 `mock-server-install` 风格一致）。
- `Makefile` 顶层补充：
  ```make
  server-install:
  	pip install -e packages/sdk-python
  	pip install -e "services[test,dev]"

  server-dev:
  	uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

  server-test:
  	cd services && pytest -q

  server-infra-up:
  	docker compose -f infra/docker/docker-compose.dev.yml up -d

  server-infra-down:
  	docker compose -f infra/docker/docker-compose.dev.yml down -v
  ```
- `.env.example` 至少列出：`LIVACHAT_PG_DSN`、`LIVACHAT_REDIS_URL`、`LIVACHAT_DEV_TOKEN`、各 provider key 占位。
- `tests/conftest.py` 提供 `pytest_asyncio` 默认 mode 配置（`asyncio_mode = "auto"`），并注入项目根路径。

### 验证命令

```bash
make server-install
make server-infra-up
cd services && pytest -q          # 至少 1 个 sanity test 通过
uvicorn app.main:app --port 8080 &
sleep 1
curl -fsS http://localhost:8080/healthz | grep '"ok": *true'
kill %1
```

### 完成定义

- [ ] 通用 DoD §0.2
- [ ] `make server-infra-up` 起 PG + Redis 容器，`pg_isready` / `redis-cli ping` 通过
- [ ] `curl /healthz` 返回 200
- [ ] `from livachat_protocol import ClientMessageAdapter` 在 services 包内可导入

---

## Phase 1 — Config & Observability

### 目标
实现 `ConfigProvider`（YAML + env 多层覆盖、fail-fast 校验）、结构化日志、Prometheus metrics 基线、ID 生成器、错误层级。

### 前置依赖
Phase 0

### 交付物

```
services/shared/
├── config/
│   ├── __init__.py
│   ├── models.py                    # 全部 pydantic-settings 模型
│   ├── loader.py                    # load_config(env: str|None) -> AppConfig
│   └── validation.py                # active/fallback 引用完整性校验
│
├── observability/
│   ├── __init__.py
│   ├── logging.py                   # structlog 配置（JSON + 关键上下文 binder）
│   ├── metrics.py                   # Prometheus Counter/Histogram/Gauge 注册表
│   └── tracing.py                   # OTel 预留（默认禁用）
│
└── utils/
    ├── ids.py                       # generate_session_id / message_id / transcription_id
    ├── time.py                      # now_ms(), MonotonicClock
    └── errors.py                    # AppError / AuthError / ProtocolError / ProviderError + code 枚举

configs/dev/server.yaml              # 与 server_design.md §6.2 一致的完整示例

services/tests/unit/
├── test_config_loader.py
├── test_logging.py
├── test_metrics_registry.py
└── test_ids.py
```

### 实现要点

- `AppConfig` 顶层字段对应设计 §6.2 一级 key：`server / auth / session / media / asr / llm / tts / postgres / redis / observability`。每个子配置用独立的 `BaseSettings`/`BaseModel`。
- `BaseSettings` 配置 `env_nested_delimiter="__"`、`env_prefix="LIVACHAT__"`。
- YAML 中 `${ENV_VAR}` 占位 → 在 `loader.py` 做一次字符串扫描替换，再交给 pydantic 解析。
- **fail-fast 校验**（`validation.py`）：
  - 各 provider 段：`active` 必须存在于 `providers` 且 `enabled=true`；`fallback` 中每项也必须 `enabled=true`。
  - `media.audio.default_sample_rate ∈ {16000, 24000}`、`opus_frame_ms ∈ {20, 40}`。
  - `auth.mode == "dev"` 时 `dev_token` 不为空；`auth.mode == "jwt"` 时 `jwt_secret` 不为空。
  - 校验失败抛 `ConfigError` 并打印**完整字段路径**与原因（不打印 secret 值）。
- `logging.py` 用 `structlog` 输出 JSON；自动附加 `session_id` / `device_id` / `seq` / `epoch`（通过 `contextvars` 注入）。debug 模式可切换 console renderer。
- `metrics.py` 提供单例 `Registry` 与以下命名（OpenMetrics 规范）：
  - Counter: `livachat_messages_total{direction,type}`、`livachat_errors_total{code}`、`livachat_provider_calls_total{kind,provider,success}`、`livachat_interrupts_total{reason}`
  - Histogram: `livachat_stage_latency_seconds{stage}`（`asr_first_partial / llm_first_token / tts_first_chunk / e2e_turn`）、`livachat_provider_latency_seconds{kind,provider}`
  - Gauge: `livachat_active_connections`、`livachat_active_sessions`
- `ids.py`：
  - `session_id = "sess_" + 18 位 [a-z0-9]`（`secrets.choice`）
  - `message_id = "msg_" + 12 位`
  - `transcription_id = "trans_" + 12 位`
  - 必须实现可注入随机源以便确定性测试。
- `errors.py` 错误码与协议 §4.3 / 设计 §4.8 一一对齐，提供 `to_protocol_error_payload()` 工具，方便 Phase 3 编码。

### 测试覆盖矩阵

| 用例 | 类型 | 期望 |
|------|------|------|
| YAML + env 覆盖（`LIVACHAT__ASR__ACTIVE=alibaba_bailian`） | 配置 | 最终值为 `alibaba_bailian` |
| `${DEEPGRAM_API_KEY}` 占位 + 环境注入 | 配置 | api_key 解析为环境变量值 |
| `${MISSING_VAR}` 未注入 | 配置 | 启动失败并打印明确字段路径 |
| `asr.active=foo`（不在 providers） | 校验 | `ConfigError` |
| `asr.fallback=[bar]` 且 `bar.enabled=false` | 校验 | `ConfigError` |
| `media.audio.default_sample_rate=8000` | 校验 | `ConfigError` |
| 日志输出 JSON 含 `session_id` binder | 日志 | 解析后含字段 |
| 日志默认脱敏 `api_key` 字段 | 日志 | 字符串中不出现真值 |
| `generate_session_id()` 长度与字符集 | ID | 正则 `^sess_[a-z0-9]{18}$` |
| Metrics 注册重复名 | metrics | 同名 metric 不重复创建（idempotent） |

### 验证命令

```bash
cd services && pytest -q tests/unit/test_config_loader.py tests/unit/test_logging.py tests/unit/test_ids.py
LIVACHAT__ASR__ACTIVE=alibaba_bailian python -c "from shared.config.loader import load_config; print(load_config('dev').asr.active)"
```

### 完成定义

- [ ] 通用 DoD
- [ ] `configs/dev/server.yaml` 与 `server_design.md §6.2` 字段完全一致
- [ ] 所有 `${ENV_VAR}` 占位字段都通过 env 注入测试
- [ ] 启动应用时若关键配置缺失，立即退出并打印明确字段路径（不抛栈）

---

## Phase 2 — Persistence (PostgreSQL + Redis)

> **现状标注（2026-08）**：本节的 PG schema + Alembic 迁移已**退役**——chat server
> 存储切换为单文件 SQLite（`devices` / `messages` / `recording_sessions` 三表，
> [`docs/chatserver_sqlite_store.md`](chatserver_sqlite_store.md)）。`shared/persistence/`
> 下 PG 模块（engine/repositories/helpers）与 `migrations/`、`alembic.ini` **保留源码但
> 不再被调用**；dev 环境无需再起 PG 容器（`make server-infra-up` 仅 Redis）。本节仅作
> 历史记录。

### 目标
落地设计 §7（PG schema + Alembic 迁移）与 §8（Redis Key 设计）；提供 `PgRepository` / `RedisStore` 异步门面 + 集成测试。

### 前置依赖
Phase 1

### 交付物

```
services/shared/persistence/
├── __init__.py
├── engine.py                        # async SQLAlchemy engine + session factory
├── redis_client.py                  # redis.asyncio client 工厂
├── repositories/
│   ├── __init__.py
│   ├── sessions.py                  # SessionRepository
│   ├── messages.py                  # MessageRepository
│   ├── transcriptions.py
│   ├── session_events.py
│   ├── provider_usage.py
│   └── users_devices.py             # User / Device upsert
└── redis_store.py                   # RedisStore: 会话恢复 / conn 注册 / 限流 / 健康缓存

services/shared/models/
├── __init__.py
└── orm.py                           # 与 §7.2 DDL 一一对应的 SQLAlchemy 2.0 declarative 模型

services/migrations/                 # Alembic
├── env.py
├── script.py.mako
└── versions/
    └── 0001_initial_schema.py       # 完整 §7.2 DDL（含 Phase 2 预留表）

services/tests/integration/
├── conftest.py                      # PG / Redis 容器或本地实例 fixture
├── test_pg_repositories.py
├── test_redis_store.py
└── test_alembic_upgrade.py          # 全新库可一次升级到 head
```

### 实现要点

- ORM 与 SQL DDL 双向一致：以 Alembic 迁移 SQL 为权威，ORM 仅作为 Python 操作层。`test_alembic_upgrade.py` 用 fresh schema 上跑 metadata diff 确保零漂移。
- 所有 repository 方法 `async`；事务由调用方传入 `AsyncSession`（避免全局上下文管理器吞掉异常）。
- **持久化策略**（设计 §7.1）：**对话主链路调用 repository 必须以 fire-and-forget 包裹**（`asyncio.create_task` + 错误转 metric），不阻塞实时下发。`orchestrator` 调用处给出统一 helper `persist_async(coro)`。
- `RedisStore` 必须实现：
  - `save_session_snapshot(session_id, snapshot, ttl=30)`：HSET + EXPIRE，参考设计 §8 示例
  - `load_session_snapshot(session_id)`
  - `register_conn(device_id, instance_id, ttl_s)` / `unregister_conn(device_id)`
  - `try_reconnect_quota(device_id) -> bool`（5 分钟 ≤ 3 次）
  - `acquire_session_lock(session_id, ttl=10) -> bool`（SET NX + EXPIRE）
  - `bump_provider_usage(date, provider, kind)`
- key 前缀必须从 `config.redis.key_prefix` 取，默认 `livachat`。
- 用 `fakeredis.aioredis` 做单测；用真实 redis 容器做集成测（pytest mark `integration`）。
- PG 测试用 testcontainers-python 可选；若不引入新依赖，可用 docker-compose.dev.yml 启动后通过 env 指向。

### 测试覆盖矩阵

| 用例 | 类型 | 期望 |
|------|------|------|
| Alembic upgrade head 一次成功 | 集成 | metadata diff 为空 |
| 重复 upgrade idempotent | 集成 | 不报错 |
| 创建 session → query by session_id | repo | 字段全等 |
| 同 session 写 N 条 messages，按 turn_index 排序读出 | repo | 顺序正确 |
| `ON DELETE CASCADE`：删除 session 自动清理 messages/transcriptions | repo | 计数为 0 |
| `save_session_snapshot` + `load_session_snapshot` | redis | 字段还原 |
| `save_session_snapshot` 30s 后过期 | redis | freezegun 推进时间，load 为 None |
| `try_reconnect_quota` 第 4 次 5 分钟内 | redis | False |
| `acquire_session_lock` 互斥 | redis | 第二次返回 False |
| `bump_provider_usage` 当日 hash 累计 | redis | 计数 = 调用次数 |
| Repo 写入失败不抛栈到调用栈外 (`persist_async`) | helper | 异常被捕获并打 metric |

### 验证命令

```bash
make server-infra-up
cd services
alembic upgrade head
pytest -q tests/integration/test_pg_repositories.py tests/integration/test_redis_store.py tests/integration/test_alembic_upgrade.py -m integration
```

### 完成定义

- [ ] 通用 DoD
- [ ] `alembic upgrade head` / `alembic downgrade base` 在 clean DB 上往返成功
- [ ] `RedisStore` 全部 key TTL 与设计 §8 一致
- [ ] `persist_async` 帮助函数被全项目统一使用（grep 不出直接 await 的 repo 调用在 orchestrator 中）

---

## Phase 3 — Gateway (Auth · WS · Codec · Seq · Heartbeat)

### 目标
跑通设计 §3.2 的 Gateway：WS 握手 → 鉴权 → 注册连接 → 收发 raw JSON 帧 → seq/timestamp 校验 → 心跳与超时 → 优雅关闭。**本 Phase 不实现业务编排**（Phase 9 才打通），出站只 echo + 心跳即可。

### 前置依赖
Phase 1, Phase 2

### 交付物

```
services/gateway/
├── __init__.py
├── auth.py                          # Authenticator (dev-token + jwt 预留)
├── codec.py                         # WireCodec (ClientMessageAdapter / ServerMessageAdapter)
├── seq.py                           # InboundSeqGuard, OutboundSeqAllocator
├── heartbeat.py                     # HeartbeatTracker
├── connection.py                    # ConnectionHandler, ConnectionManager
└── endpoint.py                      # 注册到 FastAPI 的 /v1/ws/stream 路由

services/app/routes.py               # 接入 endpoint.py + /healthz / /readyz / /metrics
services/app/application.py          # lifespan：启动 ConnectionManager 关闭钩子 / DI 装配

services/tests/unit/gateway/
├── test_auth.py
├── test_codec.py
├── test_seq.py
└── test_heartbeat.py

services/tests/integration/gateway/
├── test_ws_handshake.py             # 401 / 4001 / 4002 / 4003 / 正常握手
├── test_ws_seq_guard.py
└── test_ws_idle_timeout.py
```

### 实现要点

- **WS 路由**：`@router.websocket("/v1/ws/stream")`，query 取 `token`, `device_id`，先校验 `device_id` 为 UUID v4（用 `uuid.UUID(version=4)`）。
- **鉴权失败必须在握手前**返回 HTTP 401（不要 accept 后再 close），客户端 onClose 才能拿到 4001（设计 §9 / 协议 §1.1）。
- `WireCodec`：
  - 入站：`ClientMessageAdapter.validate_json(raw)`；解析失败 → 不直接 close，发送 `error(code=1006)` 并丢帧（防御）。**仅当连续 N=10 帧无效时**才主动断开（避免恶意流量长时间占用）。
  - 出站：业务层只构造 `ServerXxxPayload`，`WireCodec.build(type, payload)` 自动填 seq + timestamp（从 `OutboundSeqAllocator` + `now_ms()`）。
- `OutboundSeqAllocator`：单 `asyncio.Lock` 内单调递增；**所有** S2C 消息必须经唯一 writer task 串行发送（包括 ping/pong）。
- `InboundSeqGuard`：维护 `prev_seq`，回退则告警 + 丢弃 + metric `livachat_errors_total{code="seq_regress"}`。
- `HeartbeatTracker`：
  - `last_recv_at` 每条入站消息刷新
  - 协程 `tick()` 每秒检查；15s 触发主动 `ping`；35s 触发 close 4002；60s 业务空闲触发 `session.ended(idle_timeout)` + close 4003。
  - "业务空闲" 指除 `ping`/`pong` 外无入站。
- `ConnectionHandler`：
  - 启动 4 个协程：`reader_task / writer_task / dialog_task(空实现) / heartbeat_task`，用 `TaskGroup` 管理；任意失败全组取消。
  - 同 `device_id` 重连：通过 `ConnectionManager.replace(device_id, new_handler)` 把旧连接发送 `session.ended(server_shutdown)` 并 close。
- `ConnectionManager`：进程内 dict + Redis `livachat:conn:{device_id}=instance_id`（实例 ID 从 hostname 或 env 取）。`SIGTERM` 时遍历广播 `session.ended(server_shutdown)`。
- **Phase 3 业务范围**：`dialog_task` 仅做 echo——收到任意业务消息回一条 `error(1006, UNSUPPORTED, fatal=false)` 即可；这样可以独立验证 Gateway 层。Phase 9 替换为真正的 `DialogOrchestrator`。

### 测试覆盖矩阵

| 用例 | 类型 | 期望 |
|------|------|------|
| 缺 token → HTTP 401 | 集成 | 不进入 WS OPEN |
| token 非法 → close 4001 | 集成 | onClose code=4001 |
| device_id 非 UUID v4 → 4001 | 集成 | 同上 |
| 入站非法 JSON | 集成 | 收到 error 1006，连接保持 |
| 入站 seq 回退 | 集成 | 该帧被丢弃 + metric +1 |
| OutboundSeqAllocator 并发分配 | 单元 | 串行单调，无重复 |
| 15s 无任何入站 | 集成 | 收到服务端 ping |
| 35s 无任何入站 | 集成 | close 4002 |
| 60s 仅心跳 → idle | 集成 | 收到 session.ended(idle_timeout) + close 4003 |
| 同 device_id 重连踢旧 | 集成 | 旧连接收到 session.ended(server_shutdown) |
| `auth.mode=dev` + 正确 token | 集成 | 握手通过 |
| `auth.mode=jwt` + 过期 token | 集成 | close 4001（用 HS256 jwt fixture） |
| WireCodec roundtrip 所有 C2S/S2C 消息 | 单元 | 序列化前后字段等价 |

### 验证命令

```bash
cd services && pytest -q tests/unit/gateway tests/integration/gateway

# 手工 smoke
uvicorn app.main:app --port 8080 &
python - <<'PY'
import asyncio, hashlib, json, websockets
async def main():
    # wire token = 固定盐摘要（docs/server_auth.md §3.1）；此处 APP_AUTH_TOKEN=dev-livachat-token
    token = hashlib.sha1(b"dev-livachat-tokenLivaChat").hexdigest()
    uri = f"ws://localhost:8080/v1/ws/stream?token={token}&device_id=00000000-0000-0000-0000-000000000001"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type":"ping","seq":1,"timestamp":0,"payload":{}}))
        print(await ws.recv())   # 应收到 pong
asyncio.run(main())
PY
kill %1
```

### 完成定义

- [ ] 通用 DoD
- [ ] §3.2 Gateway 全部子组件实现且各自有单测
- [ ] 协议 §4.3 错误码 1006 / 2001 / 2002 / 4001 / 4002 / 4003 路径有集成测试
- [ ] 优雅关闭：`SIGTERM` 后所有连接 1s 内收到 `session.ended(server_shutdown)`

---

## Phase 4 — Media (Opus · Resample · JPEG · Buffer)

### 目标
实现媒体层：Opus 编/解码、PCM 重采样、JPEG 解码、`AudioFrameBuffer`（保证下行严格 20/40ms 边界）。**不依赖任何业务组件**，与 Provider 解耦。

### 前置依赖
Phase 0

### 交付物

```
services/media/
├── __init__.py
├── opus.py                          # OpusEncoder / OpusDecoder
├── resample.py                      # Resampler (基于 soxr)
├── jpeg.py                          # decode_jpeg(bytes) -> PIL.Image / to_data_url()
├── buffer.py                        # AudioFrameBuffer (拼帧/切帧)
└── executors.py                     # 可选 ThreadPoolExecutor（默认 None，主循环执行）

services/tests/unit/media/
├── test_opus_roundtrip.py
├── test_resampler.py
├── test_jpeg_decode.py
├── test_audio_buffer.py
└── fixtures/
    ├── speech_24k_mono.pcm          # 同步自客户端 fixture 或自合成
    ├── silence_24k_mono.pcm
    └── tiny_640x480.jpg
```

### 实现要点

- **Opus 参数（设计 §3.5 / Android SDK §4.8）**：
  - 编码：`OPUS_APPLICATION_VOIP`, bitrate=32000, CBR (`OPUS_SET_VBR=0`), complexity=5, frame_size ∈ {20ms, 40ms}, mono
  - 解码：自动从首帧探测帧长（容错），但服务端默认按配置 `media.audio.opus_frame_ms` 切分
- **采样率协商**：客户端 `session.start.config.audio_sample_rate ∈ {16000, 24000}`，服务端解码后保持原采样率；按 provider 要求重采样（如 Deepgram 默认 16k）。
- `OpusEncoder.encode(pcm: bytes) -> list[bytes]` 接受任意长度 PCM，内部用 `AudioFrameBuffer` 拼成整 frame，返回 0..N 个 Opus 包；剩余 PCM 缓存。
- `Resampler` 必须为有状态对象（持续流场景），不要每帧新建。
- `decode_jpeg(bytes)` 返回 `PIL.Image`；`to_data_url(img, max_long_side=1024, quality=70) -> str` 供 LLM Vision 注入。
- 不在主事件循环做 > 5ms 的工作；若实测阻塞，通过 `executors.py` 的可注入 executor 卸载（默认仍为 None）。

### 测试覆盖矩阵

| 用例 | 期望 |
|------|------|
| OpusEncoder 24k mono 20ms 帧 | 输出 Opus 包长度 < 200B |
| Encode + Decode roundtrip | 互相关系数 > 0.9 |
| 输入非整数倍 PCM（如 15ms） | 不立即输出；累积到 20ms 才输出 |
| `reset()` 后 pending 清空 | 下次编码不带旧样本 |
| Resampler 24k → 16k | 输出长度 ≈ input * 16/24 ±1 |
| Resampler 16k → 24k roundtrip 失真 | 互相关 > 0.95 |
| JPEG 解码 1080p | 返回 PIL Image 尺寸与 EXIF 一致 |
| `to_data_url(max_long_side=512)` | 长边 = 512 |
| AudioFrameBuffer 跨帧拼接顺序 | 输出 PCM 与拼接顺序一致 |

### 验证命令

```bash
cd services && pytest -q tests/unit/media
```

### 完成定义

- [ ] 通用 DoD
- [ ] Opus 参数与 `docs/android_sdk_design_v3.md` 的编码参数 **完全一致**（防止客户端解码错位）
- [ ] 单帧 codec 操作 p99 < 5ms（在 PR 中附带 benchmark 数字）

---

## Phase 5 — ASR Providers

### 目标
实现 `AsrProvider` / `AsrStream` 接口 + 三家 provider（`deepgram` / `elevenlabs` / `alibaba_bailian`）+ `AsrService`（failover 包装）+ `AsrFactory`（按配置实例化）。

### 前置依赖
Phase 1, Phase 4

### 交付物

```
services/asr/
├── __init__.py
├── base.py                          # AsrProvider / AsrStream Protocol + AsrResult dataclass
├── service.py                       # AsrService（failover 包装）
├── factory.py                       # create_asr(config) -> AsrService
└── providers/
    ├── __init__.py
    ├── deepgram.py
    ├── elevenlabs.py
    └── alibaba_bailian.py

services/tests/unit/asr/
├── test_factory.py
├── test_failover.py
├── providers/
│   ├── test_deepgram.py
│   ├── test_elevenlabs.py
│   └── test_bailian.py
└── fakes/
    └── fake_asr_provider.py         # 可注入序列结果的假 provider

services/tests/integration/asr/      # 可选：打真实 API（用 env gate `RUN_LIVE_ASR=1`）
└── test_live_smoke.py
```

### 实现要点

- 接口完全按设计 §5.2 的 Protocol；`AsrStream` 必须支持 `feed/results/final/reset/close`。
- 三家 provider 都是 **WSS 长连接**：每路 ASR 用一个独立 `websockets` 客户端；上层 `AsrStream.feed()` 把 PCM16 base64/binary 直接送过去；`results()` 是异步生成器，从同一 ws 读结果消息。
- **PCM 来源**：上游传入 PCM16 mono（采样率随 provider 要求由 Media 重采样到位）。
- **failover 包装**（`AsrService`）：
  - `open()` 时按 `[active, *fallback]` 顺序尝试，首个 `open()` 不抛错的胜出；
  - 运行中 `feed/results` 抛 `ProviderError` → 不自动切换（话轮在进行中切换会丢上下文），向 orchestrator 抛错，由 orchestrator 转 `error(1001, fatal=false)`；
  - 但 `open()` 阶段失败可在 open 内部自动重试下一个；
  - `health` 监控写入 `livachat:health:providers`（设计 §8）。
- **Deepgram 注意**：建议 `model=nova-2`, `endpointing=300ms`, `vad_events=true`, `interim_results=true`, `language=zh-CN`；header `Authorization: Token <key>`。
- **ElevenLabs Scribe**：注意 model 名（`scribe_v1`）；流式仍以 WSS 发送 PCM。
- **Bailian (DashScope)**：`model=paraformer-realtime-v2`，`Authorization: Bearer <key>`，注意 sample_rate 必须匹配。
- **统一日志**：每次 `open` 打 `provider / model / sample_rate / language`；不打 api_key。

### 测试覆盖矩阵

| 用例 | 类型 | 期望 |
|------|------|------|
| Factory 按 `active=deepgram` 实例化 | 单元 | 类型正确 |
| `active=foo` | 单元 | `ConfigError` |
| Failover：active open 失败 → fallback 接管 | 单元（fakes） | 实际使用的 provider == fallback |
| 全部 provider open 失败 | 单元 | 抛 `ProviderError` |
| Deepgram provider：mock WS 服务器返回 interim/final | 单元（websockets.serve mock） | results 顺序与 final 文本正确 |
| ElevenLabs / Bailian：同 mock 套路 | 单元 | 同上 |
| `reset()` 后下一话轮独立 | 单元 | 新 transcription_id 序列 |
| `close()` 释放 ws 客户端 | 单元 | 无未关闭 socket 警告 |
| Provider 抛错被包装为 `ProviderError(code=1001)` | 单元 | code 正确 |

### 验证命令

```bash
cd services && pytest -q tests/unit/asr
# 可选 live（需要真实 key）：
RUN_LIVE_ASR=1 DEEPGRAM_API_KEY=... pytest -q tests/integration/asr -m live
```

### 完成定义

- [ ] 通用 DoD
- [ ] 三家 provider 每家至少 3 个单测（含错误路径）
- [ ] `AsrService` failover 策略与设计 §5.5 一致
- [ ] mock WSS 测试中 PCM 编码不会因 base64 / binary 模式错乱

---

## Phase 6 — LLM Providers

### 目标
实现 `LlmProvider` 统一接口 + 三家 provider（`alibaba_bailian` / `openrouter` / `deepseek`）。利用 OpenAI 兼容统一为单一实现，仅 `base_url/api_key/model` 不同；支持 Vision。

### 前置依赖
Phase 1, Phase 4（Vision 用 JPEG decode + data url）

### 交付物

```
services/llm/
├── __init__.py
├── base.py                          # LlmProvider Protocol + ChatMessage / ImageRef
├── service.py                       # LlmService (failover)
├── factory.py
├── prompt.py                        # 加载 system prompt + 拼装 messages
└── providers/
    ├── __init__.py
    ├── openai_compat.py             # 通用 OpenAI-compatible streaming impl
    ├── alibaba_bailian.py           # 仅参数差异（base_url, vision_model 切换逻辑）
    ├── openrouter.py
    └── deepseek.py

services/tests/unit/llm/
├── test_prompt.py
├── test_openai_compat_stream.py
├── test_vision_payload.py
└── test_factory_failover.py
```

### 实现要点

- 用 `openai.AsyncOpenAI(base_url=..., api_key=...)` 客户端；`chat.completions.create(..., stream=True)` 返回 SSE-like 异步迭代。
- `LlmProvider.stream(messages, images=None, **gen_opts) -> AsyncIterator[str]`：每次 yield 增量文本（`delta.content` 非空时）；不要 yield 空串。
- **Vision 注入**（设计 §5.3）：当 `images` 非空且 `supports_vision=true`：把 `user` 的最后一条 message `content` 改为 `[{"type":"text","text":...},{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}]`。Bailian 走 `vision_model` 切换（同一 provider 不同 model）；`deepseek.supports_vision=false` 时**自动忽略 images 并打 warn 日志**。
- `prompt.py` 从 `configs/dev/prompts/companion.txt` 加载 system prompt，可被 env `LIVACHAT__LLM__SYSTEM_PROMPT_PATH` 覆盖。
- `LlmService.failover`：`stream()` 第一次失败 → 切换下一家重新尝试（**只在第一个 token 到达前**允许切换；首 token 后失败不切换，避免消息断裂）。
- 用 `respx` mock httpx；OpenAI SDK 底层走 httpx，可拦截 stream。

### 测试覆盖矩阵

| 用例 | 期望 |
|------|------|
| 文本流式：mock 返回 3 个 delta | 依次 yield 3 段字符串 |
| Vision：images 注入到最后 user message | request body 含 image_url data:url |
| `supports_vision=false` 时传 images | 日志 WARN，不注入 |
| First-token failover：active 在首 token 前 503 → fallback | 实际响应来自 fallback |
| First-token 后 active 中断 | 抛错，不切换 |
| 多轮 messages 顺序拼接 | request body messages 顺序正确 |
| `system_prompt_path` 不存在 | `ConfigError` |
| 单帧超时（5s 无 token） | 抛 `ProviderError(code=1003)` |

### 验证命令

```bash
cd services && pytest -q tests/unit/llm
```

### 完成定义

- [ ] 通用 DoD
- [ ] 三家 provider 通过同一份 `openai_compat` 实现，仅差 wrapper 参数（减少重复）
- [ ] Vision 注入 / 忽略 分支均覆盖

---

## Phase 7 — TTS Providers

### 目标
实现 `TtsProvider` 统一接口 + 四家 provider（`deepgram` / `elevenlabs` / `alibaba_bailian` / `cartesia`）。输出 PCM → 由 Media 模块再 Opus 编码（避免双重有损）。

### 前置依赖
Phase 1, Phase 4

### 交付物

```
services/tts/
├── __init__.py
├── base.py                          # TtsProvider / TtsStream Protocol
├── service.py
├── factory.py
└── providers/
    ├── __init__.py
    ├── cartesia.py
    ├── elevenlabs.py
    ├── alibaba_bailian.py
    └── deepgram.py

services/tts/sentence_splitter.py    # 句子/标点边界增量切分（首句首字延迟优化）

services/tests/unit/tts/
├── test_sentence_splitter.py
├── test_factory_failover.py
└── providers/
    ├── test_cartesia.py
    ├── test_elevenlabs.py
    ├── test_bailian.py
    └── test_deepgram.py
```

### 实现要点

- `TtsStream`：
  - `feed_text(delta: str)`：内部经 `SentenceSplitter` 增量切分，只在到达句子/标点边界（、。！？; ,!?.）才把当前句子送 provider。
  - `audio() -> AsyncIterator[bytes]`：异步 yield **PCM16 chunks**（与 sample_rate 对齐）。
  - `flush()`：把残余文本作为最后一句送 provider 并等待其结束。
  - `close()`：主动断开 provider。
- **优先让 provider 输出 PCM** 而不是 MP3/Opus（设计 §5.4）；server 端最终再走 `MediaCodec.opus_encode` 切 20/40ms 帧。
- Cartesia：`output_format: { container: raw, encoding: pcm_s16le, sample_rate: 24000 }`；headers `X-API-Key`, `Cartesia-Version`。
- ElevenLabs：`stream-input` ws；`output_format=pcm_24000`。
- Bailian (CosyVoice)：注意握手要 send 一个开始包再喂文本。
- Deepgram Aura：`linear16`。
- `LowerBoundLatency`：所有 provider 实现都要支持 **接收第一个文本片段后 < 300ms 内开始 yield 第一个 PCM**（在测试中用 mock 验证）。

### 测试覆盖矩阵

| 用例 | 期望 |
|------|------|
| SentenceSplitter "你好" → 不发 | 缓存 |
| SentenceSplitter "你好。今天" → 发"你好。" | 输出一句 |
| SentenceSplitter `flush()` 后残余被推出 | 完整尾巴 |
| Provider mock：输入文本 → 输出 PCM chunks | byte 序列拼接长度 ≈ TTS 真实时长 |
| `feed_text` + `audio()` 流式交错 | 不阻塞 |
| Cartesia / ElevenLabs / Bailian / Deepgram 各自 ws 协议帧格式正确 | request bytes 与文档样例对齐 |
| Failover：active open 失败 → fallback | 实际使用的 provider 切换 |
| 首 PCM chunk 到达延迟（mock） | < 300ms |

### 验证命令

```bash
cd services && pytest -q tests/unit/tts
```

### 完成定义

- [ ] 通用 DoD
- [ ] 四家 provider 各至少 3 个单测
- [ ] `SentenceSplitter` 中英文标点全覆盖

---

## Phase 8 — Session Core

### 目标
实现 `SessionManager` / `SessionContext` / `SessionStateMachine` / `TurnDetector` / `InterruptController`。**还不接 Provider**（Phase 9 接），用 fake ASR/LLM/TTS 跑通状态机与打断逻辑。

### 前置依赖
Phase 2, Phase 3

### 交付物

```
services/session/
├── __init__.py
├── context.py                       # SessionContext (运行时状态)
├── state_machine.py                 # SessionStateMachine + 迁移合法性校验
├── manager.py                       # SessionManager (start/resume/end)
├── turn_detector.py
├── interrupt.py                     # InterruptController
└── history.py                       # 工作记忆 deque + 序列化 / 反序列化

services/tests/unit/session/
├── test_state_machine.py
├── test_turn_detector.py
├── test_interrupt_controller.py
├── test_session_manager.py
└── test_history.py

services/tests/integration/session/
└── test_resume.py                   # 与 Redis 联调
```

### 实现要点

- `SessionContext` 字段严格按设计 §3.3 列表，包括 `epoch: int`、`asr_session: AsrStream | None`、`latest_video_frames: deque[(frame_id, jpeg_bytes, ts)]`（K=2）。
- `SessionStateMachine`：合法迁移见设计 §4.2 表格；非法迁移抛 `IllegalStateTransition`（debug 模式直接退出，prod 模式仅 metric + 拒绝）。
- `SessionManager.start(client_cfg, resume_id)`：
  - `resume_id` 命中 Redis snapshot 且 TTL > 0 → 恢复 `state`、`history`(最近 3 条)、`config`、`epoch`。
  - 否则新建 → 立即写 PG `sessions` 行（fire-and-forget 不阻塞 ready 下发，但 ready 之前要拿到 `session_id`）。
- `SessionManager.end`：写 PG `ended_at` / `end_reason`，删除 Redis 恢复键。
- `SessionManager.snapshot_on_disconnect`：把 ctx 关键字段 HSET 到 Redis 并 EXPIRE 30s（设计 §4.5）。
- `TurnDetector`：
  - 主依据：连续 `vad_state == silence` 帧达到 `silence_frames`（默认 30 帧 ×20ms = 600ms）→ `on_turn_end()`
  - 辅依据：ASR provider 推送 endpoint/`speech_final` 事件 → 取二者较早者
  - 灵敏度可调（`vad_sensitivity ∈ {low/medium/high}` 映射到 {45/30/20} 帧）
- `InterruptController.interrupt(reason)`：执行设计 §4.4 五步动作，保证内部目标 < 50ms。
- `epoch` 机制：所有从 LLM/TTS 产生的下行 task 在 yield 前必须比对 `ctx.epoch == epoch_snapshot`，不一致就 break（这是打断的关键正确性保证）。

### 测试覆盖矩阵

| 用例 | 期望 |
|------|------|
| StateMachine 合法迁移 idle→listening→thinking→speaking→listening | 全部通过 |
| StateMachine 非法 idle→speaking | 抛 IllegalStateTransition |
| Manager 新建 session：返回新 session_id 且落 PG | repo 计数+1 |
| Manager resume：Redis 命中 → ctx 还原 state/history | 字段相等 |
| Manager resume：Redis 未命中 → 新建 | session_restored=false |
| TurnDetector 30 帧 silence → on_turn_end 触发一次 | 计数 |
| TurnDetector ASR speech_final 早于 30 帧 | 触发更早 |
| InterruptController.interrupt() 五步全部执行 | mock 各 task.cancel 被调用 |
| 多次 interrupt（短时间内 2 次） | epoch +2，无死锁 |
| 旧 epoch 任务尝试 yield → 被丢弃 | 出站队列计数不增 |
| `latest_video_frames` deque 上限 K=2 | 第三帧入队第一帧出队 |
| disconnect 后 snapshot 写 Redis TTL=30 | redis TTL 在 [29, 30] |

### 验证命令

```bash
cd services && pytest -q tests/unit/session tests/integration/session
```

### 完成定义

- [ ] 通用 DoD
- [ ] 所有状态迁移路径有测试
- [ ] 打断 epoch 正确性：多并发 yield + interrupt 不会泄漏旧 epoch 的输出

---

## Phase 9 — Dialog Orchestrator (核心编排，端到端可对话)

### 目标
完成 `DialogOrchestrator`，把 Gateway / Session / Media / ASR / LLM / TTS 全部串成完整对话循环。本 Phase 完成后，服务端**已可对外提供完整对话服务**。

### 前置依赖
Phase 3, Phase 4, Phase 5, Phase 6, Phase 7, Phase 8

### 交付物

```
services/session/
└── orchestrator.py                  # DialogOrchestrator (主循环 + listening/thinking/speaking 子流程)

services/app/application.py          # DI：把 ASR/LLM/TTS Service + DialogOrchestrator 注入 ConnectionHandler

services/tests/integration/orchestrator/
├── conftest.py                      # fake provider fixtures (返回预设结果)
├── test_full_turn.py                # 一轮完整对话
├── test_interrupt_in_speaking.py
├── test_resume_after_disconnect.py
├── test_video_frame_processing.py
└── test_provider_failures.py        # ASR/LLM/TTS 各自挂掉的降级
```

### 实现要点

- **严格按 设计 §4.3 的伪代码** 实现主循环（listening → thinking → speaking）；每个分支都要在 `epoch_snapshot != ctx.epoch` 时立即 break。
- **流水线串联**：LLM 的 `text_delta` 一边 `send ai_text` 一边 `tts_stream.feed_text(delta)`；TTS 的 `audio_chunk` 一边过 `MediaCodec.opus_encode` 一边 `send ai_audio`。**不**等 LLM 全部完才开始 TTS（首字延迟 < 500ms 的关键）。
- 第一次 `ai_audio` 出站前下发 `status(speaking, tts_start)`；所有 `ai_audio` 出站完毕后下发 `status(listening, tts_finish)`。
- 视频帧（设计 §4.6）：
  - `speaking` 态：发 `ai_video_ack(processed=false)`，不入 `latest_video_frames`
  - 非 `speaking` 态：push 入队（队 > 3 帧仅保留最新）+ 发 `ai_video_ack(processed=true)`
- **持久化**：本轮结束后用 `persist_async` 写 `messages`（user + assistant 两行）、`transcriptions`（final）、`session_events`（status_change / interrupt）、`provider_usage`。
- 错误降级（设计 §4.8）：
  - ASR 故障：`error(1001, fatal=false)`；本轮不进 thinking
  - TTS 故障：仅发 `ai_text` 完整文本 + `status(listening)`，不发 `ai_audio`；记 `error(1002, fatal=false)`
  - LLM 故障：`error(1003, fatal=false)`；状态回 listening
- 注入式 ProviderFactory：测试时用 fake provider 替换真实 provider，使集成测试无需外部依赖。

### 测试覆盖矩阵（fake providers）

| 用例 | 期望 |
|------|------|
| 完整一轮：mock ASR/LLM/TTS 出固定结果 | 客户端收到 transcription(final) → status(thinking) → ai_text * N → status(speaking) → ai_audio * M → status(listening)，seq 全单调 |
| 视频帧节流：连续推 5 帧 | 服务端只保留最新 2 帧（设计 K=2），ack 全部回 |
| 打断（用户在 speaking 中说话） | 200ms 内收到 status(listening, interrupted)；后续 ai_audio 不再增加 |
| ASR provider open 失败 → fallback 成功 | 不发 error；用户感知正常 |
| LLM 首 token 后挂 | `error(1003)`，状态回 listening |
| TTS 全部 fallback 失败 | `error(1002)`，但 ai_text 完整下发 |
| 断线后 30s 内重连 + 旧 session_id | `session.ready(restored=true)` + 历史最近 3 条 |
| 断线后 31s 重连 | `restored=false` + 新 session_id |
| 同 device 双连 | 旧连接被踢 |
| seq 与 timestamp：所有 S2C 消息 seq 严格 +1，timestamp 单调（同一秒内允许相等） | 校验 |

### 验证命令

```bash
cd services && pytest -q tests/integration/orchestrator
# 端到端 smoke（fake providers，真实 WS）
uvicorn app.main:app --port 8080 &
python tests/integration/orchestrator/manual_smoke.py        # 启停一轮对话
kill %1
```

### 完成定义

- [ ] 通用 DoD
- [ ] 设计 §4.3 主流程伪代码每个分支都有集成测试
- [ ] 打断目标延迟（内部取消 < 50ms / 端到端 < 200ms）有 PR 内 benchmark 数字
- [ ] 协议附录 §12.1 表内所有消息类型均有"被发送"或"被处理"的测试

---

## Phase 10 — E2E with sdk-python + Load Test

### 目标
跑通基于真实 `livachat-protocol` Python 客户端的端到端测试（覆盖 11 个场景，与 Mock Server 场景矩阵对齐），并完成 1000 并发的负载基线。

### 前置依赖
Phase 9

### 交付物

```
tests/e2e_backend/
├── conftest.py                      # 启动 server (subprocess uvicorn) + fake providers
├── client.py                        # 基于 livachat-protocol 的轻量异步 WS 客户端
├── scenarios/
│   ├── test_default.py
│   ├── test_text_only.py
│   ├── test_with_audio.py
│   ├── test_slow_network.py
│   ├── test_server_error.py
│   ├── test_auth_failed.py
│   ├── test_idle_timeout.py
│   ├── test_ping_timeout.py
│   ├── test_resume_success.py
│   ├── test_resume_expired.py
│   └── test_interrupt.py
└── fixtures/
    ├── pcm_short.wav
    └── jpeg_640x480.bin

tests/load-tests/
├── locustfile.py                    # 1000 并发 ramp-up；每虚拟用户走一轮对话
└── README.md

docs/runbooks/
└── load_test_baseline.md            # 基线指标：CPU / Mem / e2e p50/p95 / 错误率
```

### 实现要点

- 启动 server 进程：`subprocess.Popen(["uvicorn", ...])` + 等待 `/healthz` 200。
- Provider 走 **fake 实现**（不打外网），通过 env `LIVACHAT__ASR__ACTIVE=fake` 切换（在 Phase 9 已经具备注入能力，本 Phase 把 fake provider 注册到 factory）。
- 场景与 Mock Server 11 个场景对齐（详见 `tools/local-dev/mock-services/livachat_mock_server/DESIGN.md §5`）；目的是验证**真实 Server 实现**与协议契约一致——这与 mock 测试方向相反但场景定义可复用。
- Locust 跑 1000 并发：每虚拟用户每 5s 一轮对话；记录 e2e turn 延迟、错误码分布。
- 基线写入 `docs/runbooks/load_test_baseline.md`，作为后续回归对照。

### 测试覆盖矩阵

| 用例 | 期望 |
|------|------|
| 11 个 scenario 全绿 | 与 mock server 一致 |
| 1000 并发持续 5 分钟 | 单实例 CPU < 80%，错误率 < 1% |
| e2e turn p95 | < 1500ms（fake provider，含 100ms 模拟 LLM 延迟） |
| 内存占用 | < 1GB |
| 优雅停机：locust 仍在跑时 `kill -TERM` | 所有客户端收到 session.ended(server_shutdown) |

### 验证命令

```bash
cd services && pytest -q tests/e2e_backend
locust -f tests/load-tests/locustfile.py --headless -u 1000 -r 50 -t 5m --host ws://localhost:8080
```

### 完成定义

- [ ] 通用 DoD
- [ ] 11 个 E2E 场景全绿
- [ ] 负载基线写入 runbook
- [ ] 任一 provider 失败时降级路径 E2E 覆盖

---

## Phase 11 — Hardening / Deploy / Runbook

### 目标
完成 Dockerfile / K8s manifests / 优雅停机 / `/readyz` 真实健康检查 / Grafana 仪表板模板 / 运维 runbook，为 staging 部署做好准备。

### 前置依赖
Phase 10

### 交付物

```
infra/docker/server.Dockerfile       # multi-stage（builder + runtime），基镜像 python:3.12-slim
infra/docker/docker-compose.dev.yml  # 完整 dev 栈
infra/kubernetes/
├── deployment.yaml                  # rollingUpdate maxSurge=1 maxUnavailable=0
├── service.yaml                     # ClusterIP + headless（StatefulSet 可选）
├── ingress.yaml                     # nginx-ingress sticky session + read-timeout 3600s
├── configmap.yaml                   # 挂载 server.yaml
├── secret.example.yaml              # provider keys 占位
└── hpa.yaml                         # 暂用 CPU 指标，预留 custom metric

infra/observability/
├── grafana/livachat-server.json     # 仪表板：活跃连接 / 各阶段延迟 / 错误码 / provider 成功率
└── prometheus/scrape.yaml           # 抓 /metrics

docs/runbooks/
├── deploy.md
├── troubleshooting.md
└── provider_failover.md             # active/fallback 切换演练步骤

services/app/routes.py               # /readyz 真实健康检查（PG ping、Redis ping、可选 provider preflight）
services/app/lifespan.py             # SIGTERM 优雅停机协议
```

### 实现要点

- Dockerfile：
  - builder 阶段 `pip wheel`；runtime 阶段仅复制 wheel + 安装运行时依赖；非 root 用户运行。
  - 镜像体积目标 < 350MB。
- `/readyz` 真实健康检查：依次 ping PG（`SELECT 1`）、Redis（`PING`）、若 `provider preflight` 启用则探活 active provider 的 `/health` 或最小请求。任一失败返回 503。
- 优雅停机：
  1. 收到 `SIGTERM` 立即在 `/readyz` 返回 503（流量摘除）
  2. 不再 accept 新 WS 连接
  3. 给所有在连会话发 `session.ended(server_shutdown)` 并 close 1000
  4. 等待 writer_task flush，最长 10s 超时强关
  5. 关闭 PG / Redis 连接池后退出
- Grafana 仪表板覆盖设计 §10.2 列出的全部指标。
- runbook：
  - `deploy.md`：从 `staging` 到 `prod` 的标准发布流程
  - `troubleshooting.md`：常见 4xx/5xx、provider 429、PG 连接耗尽、Redis 内存压力
  - `provider_failover.md`：手动切换 active provider 的步骤 + 影响面

### 测试覆盖矩阵

| 用例 | 期望 |
|------|------|
| `docker build` 成功且镜像 < 350MB | 检查 `docker images` |
| `docker run` 内 `/healthz` / `/readyz` 200 | 集成 |
| `kill -TERM` 容器 → 客户端收到 `session.ended(server_shutdown)` | E2E |
| 停机超时 10s 强制退出 | E2E |
| K8s manifests `kubectl apply --dry-run=server` | OK |
| Prometheus 抓 metrics 不为空 | scrape test |

### 验证命令

```bash
docker build -f infra/docker/server.Dockerfile -t livachat-server:dev .
docker run --rm -p 8080:8080 --env-file services/.env livachat-server:dev &
sleep 5 && curl -fsS http://localhost:8080/readyz
kill %1
kubectl apply --dry-run=server -f infra/kubernetes/
```

### 完成定义

- [ ] 通用 DoD
- [ ] 镜像可在 staging 环境成功 rolling update
- [ ] Grafana 仪表板能看到所有 §10.2 指标
- [ ] 三本 runbook 完成且经过一次内部 walkthrough

---

## 2. 跨 Phase 工程约束

### 2.1 协议契约不变性

- `packages/api-schema/` 是端到端 SSOT；server 一律 `from livachat_protocol import ...` 消费。
- **任何对协议的扩展（新 type / 新字段）** 必须先：
  1. 在 `packages/api-schema/` 修改 JSON Schema 并跑 `make generate-android-api` / 重新生成 sdk-python
  2. 在 `docs/ws-protocol-design.md` 和 `docs/server_design.md` 同步更新
  3. 在 `docs/adr/` 写 ADR
  4. iOS / Android / Server 三端 review 通过
- 老客户端兼容原则：服务端对未知 `type` 回 `error(code=1006, UNSUPPORTED_MESSAGE_TYPE)`；对未知字段忽略。

### 2.2 Provider 接口稳定

Phase 5/6/7 的 `AsrProvider` / `LlmProvider` / `TtsProvider` 接口一旦在该 Phase 完成，**后续 Phase 不得修改其签名**。新增 provider 只允许实现现有接口。如需变更接口，必须走 ADR。

### 2.3 测试金字塔

| 类型 | 运行环境 | 触发 | 用途 |
|------|----------|------|------|
| Unit (`tests/unit/`) | 无外部依赖 | 每次 commit | 协议 / 状态机 / 算法 / Provider mock |
| Integration (`tests/integration/`) | 本地 PG + Redis | PR | repo / redis / WS gateway / orchestrator + fakes |
| E2E (`tests/e2e_backend/`) | 完整 server + fake providers | PR | 端到端协议合规性 |
| Live (`tests/integration/asr/_live`、`_llm`、`_tts`) | 真实 provider key | nightly | 与外部供应商契约 |
| Load (`tests/load-tests/`) | docker / k8s | release candidate | 并发与稳定性 |

### 2.4 异步纪律

- 严禁同步 I/O（`requests` / `psycopg2` sync / `redis-py` sync）。
- 任何 CPU > 5ms 的操作（Opus 编解码、JPEG decode）若实测阻塞，必须迁移到注入的 `executor`；不可直接 `time.sleep` 或 `requests.get`。
- 长时间运行的 task 必须由 `TaskGroup` 管理；持有可取消句柄；`finally` 中释放外部资源（ws / file）。

### 2.5 安全与密钥

- `api_key` / `jwt_secret` / `PG password` 一律仅来源于环境变量；YAML 中用 `${ENV_VAR}` 占位；日志默认脱敏（structlog processor）。
- WSS 终结由 LB/Ingress 负责；server 内部默认监听明文（dev）/ unix socket（prod 可选）。
- PG 静态加密由基础设施层提供；server 仅保证媒体不落库（设计 §7.3 / §10.3）。

### 2.6 Git 工作流

- 分支命名：`feat/server-phase-N-short-desc`
- 每个 Phase 一个 PR，对应 issue 关闭
- commit 前缀 `[server][phase-N]`
- 强制 squash merge
- PR 描述必须含：
  - Phase 自检报告（§4）
  - benchmark 数字（如 Phase 4/9/10）
  - 偏离设计说明（如无填"无"）

### 2.7 CI 集成

`.github/workflows/ci-backend.yml` 应包含（从 Phase 0 开始逐步启用）：

```yaml
jobs:
  lint:
    - ruff check services/
    - ruff format --check services/
  unit:
    - pip install -e packages/sdk-python -e "services[test]"
    - cd services && pytest -q tests/unit --cov=services --cov-fail-under=80
  integration:
    services:
      postgres: { image: postgres:17, env: ... }
      redis:    { image: redis:8 }
    steps:
      - cd services && alembic upgrade head
      - cd services && pytest -q tests/integration -m integration
  e2e:
    needs: integration
    steps:
      - pytest -q tests/e2e_backend
  typecheck:
    - mypy services/gateway services/session services/asr services/llm services/tts
```

---

## 3. 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| Provider API 文档与实际行为偏差（尤其国内 Bailian） | 高 | 中 | Phase 5/6/7 增加 `_live` smoke 测试（env gated），首联调真接 API |
| Opus 编码参数与客户端 SDK 不一致 → 解码错位 | 中 | 高 | Phase 4 起 fixture 与客户端 SDK 共用 PCM/Opus 样本；CI 跨端 roundtrip 测试 |
| 打断 epoch 漏判，旧 epoch 输出"漏出" | 中 | 高 | Phase 8 epoch 单元测试 + Phase 9 集成 fuzz 测试（短时间多次 interrupt） |
| WebSocket 在弱网下 30s 会话保留窗口太短 | 中 | 中 | 监控重连成功率，必要时 ADR 提议把 TTL 拉长到 60s |
| LLM 首 token 在境内 < 500ms 不达标 | 中 | 高 | 优先 Bailian（同区域），OpenRouter 作 fallback；Phase 10 基线确认 |
| PG 连接耗尽（1000 连接 × N session） | 中 | 高 | repo 全部走 fire-and-forget；pool_max=20 + statement_timeout |
| Redis 单点故障 | 低 | 高 | Phase 11 runbook 指明故障切换；中期演进引入 Sentinel/Cluster |
| 配置 YAML 误写覆盖生产 | 低 | 高 | Phase 1 fail-fast + Phase 11 CI 校验 prod config 模板 |
| 单 instance 不能支撑 1000 并发 | 中 | 中 | Phase 10 提前压测；Phase 11 HPA + sticky session 验证 |
| 协议 schema 变更未同步到 server | 中 | 高 | CI 加 `check-python-api-generated`（仿 android）防漂移 |

---

## 4. AI Agent 自检 Checklist

每个 Phase 完成后 AI Agent 必须输出以下报告：

```markdown
## Server Phase N 完成报告

### 交付物
- [ ] 列出所有新增/修改文件路径

### 验证命令运行结果
(粘贴 pytest / curl / docker 等关键输出)

### 测试覆盖
- 单元 + 集成: X passed, Y skipped, 0 failed
- 行覆盖率: XX% / 分支覆盖率: YY%

### 关键指标（如本 Phase 涉及）
- 例：单帧 Opus 编码 p99 = X ms（目标 < 5ms）
- 例：打断内部取消 p95 = X ms（目标 < 50ms）
- 例：e2e turn p95 = X ms（目标 < 1500ms with fake providers）

### 偏离设计
- (如无填"无")
- 或：在 ADR-NNN 中说明

### 已知问题 / TODO
- (列表)

### 下一 Phase 准备
- [ ] 前置依赖确认
- [ ] 接口稳定声明（适用于 Phase 5/6/7/8）
```

只有 AI Agent 输出该报告且人审确认后，方可启动下一 Phase。

---

## 5. 附录

### 5.1 常用一行命令

```bash
# 安装 server（含 sdk-python editable）
make server-install

# 起本地基建（PG + Redis）
make server-infra-up

# 起服务（dev hot reload）
make server-dev

# 一键测试
make server-test

# Alembic 迁移（已退役：PG 模块保留未用，chat server 存储为 SQLite——
# 见 docs/chatserver_sqlite_store.md；dev 无需 PG 容器）
cd services && alembic upgrade head
cd services && alembic revision --autogenerate -m "msg"

# 单 phase 测试
cd services && pytest -q tests/unit/gateway tests/integration/gateway

# 端到端
cd services && pytest -q tests/e2e_backend

# 重新生成 Python DTO（理论上 schema 不变就不动）
cd packages/sdk-python && python -m setuptools build_meta  # 或运行 scripts/generate_python_api.py
pip install -e packages/sdk-python
```

### 5.2 文件目录速查（最终态）

```
services/
├── pyproject.toml
├── alembic.ini
├── app/
│   ├── main.py                              # Phase 0
│   ├── application.py                       # Phase 0 → Phase 9 完整 DI
│   ├── routes.py
│   └── lifespan.py                          # Phase 11
│
├── gateway/                                 # Phase 3
│   ├── auth.py / codec.py / seq.py
│   ├── heartbeat.py / connection.py / endpoint.py
│
├── session/                                 # Phase 8 / Phase 9
│   ├── context.py / state_machine.py
│   ├── manager.py / turn_detector.py
│   ├── interrupt.py / history.py
│   └── orchestrator.py                      # Phase 9
│
├── asr/                                     # Phase 5
│   ├── base.py / service.py / factory.py
│   └── providers/{deepgram,elevenlabs,alibaba_bailian}.py
│
├── llm/                                     # Phase 6
│   ├── base.py / service.py / factory.py / prompt.py
│   └── providers/{openai_compat,alibaba_bailian,openrouter,deepseek}.py
│
├── tts/                                     # Phase 7
│   ├── base.py / service.py / factory.py / sentence_splitter.py
│   └── providers/{cartesia,elevenlabs,alibaba_bailian,deepgram}.py
│
├── media/                                   # Phase 4
│   ├── opus.py / resample.py
│   ├── jpeg.py / buffer.py
│   └── executors.py
│
├── shared/
│   ├── config/                              # Phase 1
│   ├── persistence/                         # Phase 2
│   ├── models/orm.py
│   ├── observability/                       # Phase 1
│   └── utils/{ids,time,errors}.py
│
├── migrations/                              # Phase 2 Alembic
│   └── versions/0001_initial_schema.py
│
└── tests/
    ├── unit/
    ├── integration/
    └── e2e_backend/                         # Phase 10（也放在仓库根 tests/e2e_backend/，二选一统一）

configs/dev/server.yaml                       # Phase 1
configs/dev/prompts/companion.txt

infra/docker/server.Dockerfile                # Phase 11
infra/docker/docker-compose.dev.yml           # Phase 0 / Phase 11 完整
infra/kubernetes/*.yaml                       # Phase 11
infra/observability/{grafana,prometheus}/*    # Phase 11

docs/runbooks/
├── deploy.md
├── troubleshooting.md
├── provider_failover.md
└── load_test_baseline.md
```

### 5.3 与设计文档章节对照（完整版）

| 设计 § | 实现 Phase | 备注 |
|--------|-----------|------|
| §1 描述与非职责 | 全 Phase 约束 | 不实现登录 / Phase 2 记忆 / WebRTC |
| §2.1 架构风格 | Phase 0 + Phase 9 wiring | 单体多模块 |
| §2.2 分层与依赖规则 | 全 Phase | 单向向下，DI |
| §2.3 并发模型 | Phase 3 + Phase 9 | TaskGroup 协程树 |
| §3.2 Gateway | Phase 3 | |
| §3.3 Session | Phase 8 | |
| §3.4 Provider Modules | Phase 5 / 6 / 7 | |
| §3.5 Media | Phase 4 | |
| §4.1 连接生命周期 | Phase 3 + Phase 8 | |
| §4.2 状态机 | Phase 8 | |
| §4.3 完整一轮对话 | Phase 9 | 核心 |
| §4.4 打断 | Phase 8 + Phase 9 | epoch 是关键 |
| §4.5 会话恢复 | Phase 8 | Redis snapshot |
| §4.6 视频帧处理 | Phase 9 | K=2 deque |
| §4.7 心跳 / seq / timeout | Phase 3 | |
| §4.8 错误处理与降级 | Phase 9 | failover + error 码 |
| §5 Provider 抽象 | Phase 5 / 6 / 7 | 接口稳定后冻结 |
| §6 配置 | Phase 1 | fail-fast |
| §7 PG Schema | Phase 2 | Alembic 0001 |
| §8 Redis Key | Phase 2 / Phase 8 | |
| §9 鉴权 | Phase 3 | dev-token + jwt 预留 |
| §10 NFR 与可观测性 | Phase 1 基线 / Phase 11 仪表板 | |
| §11 目录结构 | Phase 0 骨架 | |
| §12 演进 | 不实现，仅预留 | |

---

*本文档是 LivaChat Backend Server Phase 1 的执行计划。AI Agent 应严格按 Phase 0 → 11 顺序推进，每 Phase 完成后输出 §4 自检报告。如需偏离设计（`docs/server_design.md` 或 `docs/ws-protocol-design.md`），先停下提问并写 ADR，**不要**自行扩展协议或修改 Schema。*
