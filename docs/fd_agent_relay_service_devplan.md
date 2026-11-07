# Agent Relay Service — Development & Test Plan

> **Date**: 2026-07-26
> **Source**: `./fd_agent_relay_service.md`
> **Status**: draft

---

## 1. 开发阶段总览

| 阶段 | 内容 | 预估工期 | 验收方式 |
|------|------|----------|----------|
| **P0 — 基础设施** | 项目骨架、配置、模型、错误类型、Redis Pub/Sub 封装 | 1-2d | unit tests 全部通过 |
| **P1 — Adapter 框架 + HermesAdapter** | base adapter ABC、HermesAdapter (HTTP/SSE)、adapter loader；OpenClawAdapter 仅骨架（stub，不做测试） | 2-3d | unit + mock agent server 集成测试 |
| **P2 — Session Manager + Event Router** | AgentSessionManager、事件路由、幂等、并行调用 | 2-3d | integration tests (fakeredis) |
| **P3 — orchestrator 接入** | AgentEventPublisher、orchestrator ASR final 分支改动 | 1d | unit + e2e (asr_only mode) |
| **P4 — gateway 接入 + Push Publisher** | PushSubscriber、PushPublisher、ai_text schema 扩展、ConnectionManager 集成 | 2d | integration tests |
| **P5 — recording 接入** | recording.ready 事件发布、adapter.analyze_recording | 1-2d | integration tests |
| **P6 — 可观测性 + 限流** | metrics、日志、per-adapter/per-device 限流 | 1d | unit tests |
| **P7 — 全链路联调** | mock agent HTTP server + asr_only 模式端到端验证 | 2d | e2e: 一问两答 |

---

## 2. 测试策略

### 2.1 测试分层

```
┌──────────────────────────────────────────────┐
│  E2E Tests (e2e-backend/)                     │
│  ─ full stack: app + relay + mock agent       │
│  ─ scenarios: reactive, proactive, offline    │
├──────────────────────────────────────────────┤
│  Integration Tests (integration/)              │
│  ─ fakeredis: pub/sub, session manager        │
│  ─ httpx+respx: adapter HTTP/SSE 交互         │
│  ─ test DB: ai_text schema migration          │
├──────────────────────────────────────────────┤
│  Unit Tests (unit/agent_relay/)               │
│  ─ models, config, errors                     │
│  ─ event router, publisher                    │
│  ─ adapter base, hermes                       │
│  ─ session manager (mocked adapters)          │
│  ─ orchestrator agent_publisher               │
│  ─ metrics                                    │
└──────────────────────────────────────────────┘
```

### 2.2 Mock 策略

| 组件 | Mock 方式 | 工具 |
|------|-----------|------|
| Redis Pub/Sub | fakeredis (aioredis compat) | `fakeredis>=2.24` (已有依赖) |
| 外部 Agent HTTP API | respx mock transport | `respx>=0.21` (已有依赖) |
| Mock Agent Server (e2e) | 独立 FastAPI app，模拟 Hermes SSE 行为 | `fastapi` + `sse-starlette` |
| orchestrator | 构造 SessionContext + mock handler | pytest fixtures |
| gateway ConnectionManager | 内存 dict 模拟 | pytest fixtures |

---

## 3. 详细测试用例

### 3.1 P0 — 基础设施

#### 3.1.1 配置加载 (`services/agent_relay/config.py`)

**文件**: `services/tests/unit/agent_relay/test_config.py`

| ID | 测试用例 | 输入 | 预期 |
|----|----------|------|------|
| CFG-01 | 默认配置加载 | 空 env | `enabled=False`, `events_channel="livachat:agent:events"` |
| CFG-02 | env override | `LIVACHAT_AGENT_RELAY__ENABLED=true` | `enabled=True` |
| CFG-03 | adapter 配置嵌套加载 | `LIVACHAT_AGENT_RELAY__ADAPTERS__HERMES__BASE_URL=http://x:8080` | `config.adapters["hermes"].base_url == "http://x:8080"` |
| CFG-04 | 缺失 adapter 配置 | 无 hermes 段 | 启动不报错，adapter 列表为空 |
| CFG-05 | api_key 从 env var 解析 | `HERMES_AUTH_TOKEN=sk-test` | adapter config 中 `api_key="sk-test"` |
| CFG-06 | max_reply_bytes 边界 | `max_reply_bytes: 0` | 拒绝，抛 ValidationError |
| CFG-07 | timeout_s 边界 | `timeout_s: 0` | 拒绝，抛 ValidationError |
| CFG-08 | proactive.debounce_s 默认值 | 未配置 | `debounce_s=30` |

#### 3.1.2 内部消息模型 (`services/agent_relay/models.py`)

**文件**: `services/tests/unit/agent_relay/test_models.py`

| ID | 测试用例 | 输入 | 预期 |
|----|----------|------|------|
| MOD-01 | AgentEvent 反序列化 — user.utterance | 合法 JSON (含所有必填字段) | 解析成功，kind="user.utterance" |
| MOD-02 | AgentEvent 反序列化 — recording.ready | 合法 JSON | 解析成功，kind="recording.ready" |
| MOD-03 | AgentEvent 反序列化 — session.started | 合法 JSON | 解析成功 |
| MOD-04 | AgentEvent 反序列化 — session.ended | 合法 JSON | 解析成功 |
| MOD-05 | AgentEvent 未知 kind | `"kind": "unknown.event"` | 抛 ValidationError |
| MOD-06 | AgentEvent 缺失必填字段 | 无 `device_id` | 抛 ValidationError |
| MOD-07 | AgentEvent ts_ms 类型错误 | `"ts_ms": "not-a-number"` | 抛 ValidationError |
| MOD-08 | PushMessage 序列化 — ai_text | 构造完整 PushMessage | JSON 输出含 `source: "agent"`, `agent_name` 等 |
| MOD-09 | PushMessage message_id 前缀校验 | `message_id: "bad_prefix_01"` | 不强制前缀（仅日志建议），解析通过 |
| MOD-10 | PushMessage text 长度限制 | text 长度 > max_reply_bytes | 不在此层截断（由 publisher 负责） |

#### 3.1.3 错误类型 (`services/agent_relay/errors.py`)

**文件**: `services/tests/unit/agent_relay/test_errors.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| ERR-01 | AgentRelayError 基类 | 可捕获，`str(e)` 正确 |
| ERR-02 | AdapterTimeoutError | 继承 AgentRelayError，含 `adapter_name` |
| ERR-03 | AdapterConnectionError | 继承 AgentRelayError，含 `adapter_name`, `status_code` |
| ERR-04 | DuplicateEventError | 继承 AgentRelayError，含 `event_id` |
| ERR-05 | DeviceOfflineError | 继承 AgentRelayError，含 `device_id` |

---

### 3.2 P1 — Adapter 框架 + HermesAdapter

#### 3.2.1 Adapter Base (`services/agent_relay/adapters/base.py`)

**文件**: `services/tests/unit/agent_relay/adapters/test_base.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| BAS-02 | AgentResponseChunk 默认 | `is_final=False`, `metadata=None` |
| BAS-03 | RecordingInfo 字段完整性 | 所有必填字段可构造 |
| BAS-04 | AgentAdapter ABC 不可直接实例化 | `TypeError` |
| BAS-05 | 子类未实现 ask() | `TypeError` |
| BAS-06 | 子类未实现 name property | `TypeError` |
| BAS-07 | connect() / close() 默认实现 | 调用不报错 (no-op) |
| BAS-08 | analyze_recording() 默认实现 | 空操作，不 yield 任何 chunk |

#### 3.2.2 Adapter Loader (`services/agent_relay/adapters/loader.py`)

**文件**: `services/tests/unit/agent_relay/adapters/test_loader.py`

| ID | 测试用例 | 输入 | 预期 |
|----|----------|------|------|
| LDR-01 | 按 name 加载已知 adapter | `"hermes"` | 返回 HermesAdapter 实例 |
| LDR-02 | 未知 adapter name | `"nonexistent"` | 抛 KeyError 或明确异常 |
| LDR-03 | 加载所有已注册 adapter | `load_all()` | 返回 `dict[name, Adapter]` |
| LDR-04 | 配置注入到 adapter | 传入 config dict | adapter 内部配置正确 |

#### 3.2.3 HermesAdapter (`services/agent_relay/adapters/hermes.py`)

**文件**: `services/tests/unit/agent_relay/adapters/test_hermes.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| HER-01 | ask() — 正常 SSE 流式响应 | 每个 SSE event 映射为一个 AgentResponseChunk |
| HER-02 | ask() — 单次完整响应 (非流式) | 单个 chunk, `is_final=True` |
| HER-03 | ask() — HTTP 4xx 错误 | 抛 AdapterConnectionError，含 status_code |
| HER-04 | ask() — HTTP 5xx 错误 | 抛 AdapterConnectionError |
| HER-05 | ask() — 超时 | 抛 AdapterTimeoutError |
| HER-06 | ask() — 连接失败 (DNS/refused) | 抛 AdapterConnectionError |
| HER-07 | ask() — SSE 连接中断 | 已收到的 chunks 正常 yield，后续抛异常 |
| HER-08 | ask() — 空响应体 | 不 yield 任何 chunk |
| HER-09 | ask() — 正确传递 context | request body 含 `session_id`, `language`, `history` |
| HER-10 | analyze_recording() — 正常响应 | 拉取 transcript → POST /api/analyze → yield chunks |
| HER-11 | analyze_recording() — transcript endpoint 不可达 | 抛异常 |
| HER-12 | ask() — max_retries 重试逻辑 | 首次失败 → 重试 max_retries 次后抛异常 |
| HER-13 | name property | 返回 `"hermes"` |

#### 3.2.4 OpenClawAdapter (`services/agent_relay/adapters/openclaw.py`)

> **Phase 1 仅骨架**：实现 `AgentAdapter` 接口的 stub 类（`ask()` raise `NotImplementedError`，`name="openclaw"`），不做测试。完整实现在 Phase 2 补充。

---

### 3.3 P2 — Session Manager + Event Router

#### 3.3.1 Event Router (`services/agent_relay/router.py`)

**文件**: `services/tests/unit/agent_relay/test_router.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| RTR-01 | user.utterance → AgentSessionManager.on_user_utterance | 调用正确方法 |
| RTR-02 | recording.ready → AgentSessionManager.on_recording_ready | 调用正确方法 |
| RTR-03 | session.started → AgentSessionManager.on_session_started | 调用正确方法 |
| RTR-04 | session.ended → AgentSessionManager.on_session_ended | 调用正确方法 |
| RTR-05 | 未知 kind | 记 warning 日志，不抛异常 |
| RTR-06 | 反序列化失败的 message | 记 error 日志 + metric，不崩溃 |

#### 3.3.2 AgentSessionManager (`services/agent_relay/session_manager.py`)

**文件**: `services/tests/unit/agent_relay/test_session_manager.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| SM-01 | on_session_started — 创建 session | `_sessions` dict 含该 session_id |
| SM-02 | on_session_started — 重复 session_id | 幂等，不创建第二个 |
| SM-03 | on_user_utterance — 调用所有启用 adapter | N 个 adapter 全部收到 `ask()` 调用 |
| SM-04 | on_user_utterance — 单个 adapter 失败不影响其他 | 1 个抛异常，其余正常完成 |
| SM-05 | on_user_utterance — adapter 返回多 chunk | 每个 chunk 单独 publish |
| SM-06 | on_user_utterance — adapter 返回单 chunk | publish 一次，`is_final=True` |
| SM-07 | on_user_utterance — adapter 返回空 | 不 publish |
| SM-08 | on_user_utterance — 幂等 (重复 transcription_id) | 第二次直接丢弃，记 duplicate metric |
| SM-09 | on_user_utterance — 幂等 LRU 容量上限 | 超过 10k 时旧 id 被驱逐，可能重复处理（acceptable） |
| SM-10 | on_recording_ready — 调用 adapter.analyze_recording | adapter 收到 RecordingInfo |
| SM-11 | on_recording_ready — 并行调用多 adapter | N 个 adapter 全部收到 `analyze_recording()` |
| SM-12 | on_recording_ready — 幂等 (重复 recording_id) | 第二次丢弃 |
| SM-13 | on_recording_ready — debounce | 同一 session 30s 内第二次 recording.ready 丢弃 |
| SM-14 | on_recording_ready — 录音时长 < min_segment_duration_s | 不触发分析 |
| SM-15 | on_session_ended — 清理 session | `_sessions` 中移除 |
| SM-16 | on_session_ended — 不存在的 session | 不报错 |
| SM-17 | shutdown — 关闭所有 adapter | 每个 adapter.close() 被调用 |
| SM-18 | shutdown — adapter.close() 失败 | 记 warning，不阻塞其他 adapter 关闭 |

#### 3.3.3 Session Manager 集成测试 (fakeredis)

**文件**: `services/tests/integration/agent_relay/test_session_manager_integration.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| SMI-01 | 从 Redis events channel 收到 user.utterance → 完整链路 | router 解析 → SM 处理 → adapter 调用 → push publisher 发布 |
| SMI-02 | 多实例场景 — 两个 adapter 同时回复 | 两条独立 push message |
| SMI-03 | adapter 超时后 cancel | SM 不崩溃，metric 记录 timeout |
| SMI-04 | 高并发 — 100 个 utterance 快速到达 | 不丢失、不阻塞、内存稳定 |
| SMI-05 | session 不存在时收到 utterance | 自动创建 session（或记 error，取决于策略） |

---

### 3.4 P3 — orchestrator 接入

#### 3.4.1 AgentEventPublisher (`services/session/agent_publisher.py`)

**文件**: `services/tests/unit/session/test_agent_publisher.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| PUB-01 | publish_utterance — 正常发布 | Redis publish 被调用，参数正确 |
| PUB-02 | publish_utterance — Redis 连接失败 | 不抛异常，记 warning 日志 + metric |
| PUB-03 | publish_session_started | Redis publish 被调用 |
| PUB-04 | publish_session_ended | Redis publish 被调用 |
| PUB-05 | publisher 未配置 (None) | orchestrator 不调用 publish，零行为 |
| PUB-06 | fire-and-forget — 不阻塞调用方 | `asyncio.create_task` 包装，调用立即返回 |
| PUB-07 | publish 消息格式校验 | JSON 反序列化后字段完整 |

#### 3.4.2 orchestrator 集成 (`services/session/orchestrator.py`)

**文件**: `services/tests/unit/session/test_orchestrator_agent.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| ORC-01 | ASR final + agent_publisher 存在 → publish | publish_utterance 被调用 |
| ORC-02 | ASR final + agent_publisher=None → 不 publish | 无调用，不影响主链路 |
| ORC-03 | ASR final + meaningful=False + had_asr_final=False → 不 publish | 无调用 |
| ORC-04 | pipeline_mode=asr_only → 同样 publish | publish 被调用 |
| ORC-05 | publish 抛异常 → 不影响 LLM/TTS 链路 | 主链路正常完成 |
| ORC-06 | 同时 LLM + agent publish 并行 | LLM 回答 + agent 发布同时进行 |

---

### 3.5 P4 — gateway 接入 + Push Publisher

#### 3.5.1 PushPublisher (`services/agent_relay/publisher.py`)

**文件**: `services/tests/unit/agent_relay/test_publisher.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| PSH-01 | 查 conn:{device_id} → instance_id 存在 | publish 到 `livachat:push:{instance_id}` |
| PSH-02 | conn:{device_id} → instance_id 不存在 | 丢弃 + metric (dropped_offline) |
| PSH-03 | publish 失败 (Redis 不可用) | 记 error 日志 + metric，不抛异常 |
| PSH-04 | 消息内容正确 | JSON 含 `kind`, `device_id`, `message_id`, `text`, `source`, `agent_name` 等 |
| PSH-05 | text 超过 max_reply_bytes | 截断 + 记 metric |
| PSH-06 | 批量 publish (多 adapter 多 chunk) | 每个 chunk 独立 publish |

#### 3.5.2 PushSubscriber (gateway 侧)

**文件**: `services/tests/unit/gateway/test_push_subscriber.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| SUB-01 | 收到 push message → enqueue_outbound | ConnectionHandler 收到 frame |
| SUB-02 | device 不在本实例 | 丢弃 + metric (dropped_offline) |
| SUB-03 | device 已断开 (conn 不存在) | 丢弃 + metric |
| SUB-04 | 消息 schema 校验失败 | 丢弃 + 记 error 日志 |
| SUB-05 | 多个 device 同时在线 | 正确路由到对应 ConnectionHandler |
| SUB-06 | 高并发 push | 不丢失、不阻塞 |

#### 3.5.3 WireCodec / ai_text schema 扩展

**文件**: `services/tests/unit/gateway/test_codec_agent.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| COD-01 | ai_text with source="agent" 序列化 | JSON 含 `source`, `agent_name`, `reply_to` |
| COD-02 | ai_text with source="llm" (默认) | 与旧格式相同，向后兼容 |
| COD-03 | ai_text 无 source 字段 (旧客户端) | 解析为 `source="llm"` |
| COD-04 | ai_text with proactive=true | JSON 含 `proactive: true` |
| COD-05 | ai_text with reply_to | JSON 含 `reply_to` 字段 |

---

### 3.6 P5 — recording 接入

#### 3.6.1 recording 事件发布

**文件**: `services/tests/unit/recording/test_agent_publisher.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| REC-01 | 录音落盘 → publish recording.ready | events channel 收到 recording.ready 消息 |
| REC-02 | 消息字段完整性 | `recording_id`, `device_id`, `session_id`, `manifest_path`, `duration_ms` |
| REC-03 | publish 失败不影响 recording 流程 | 录音正常完成，记 warning |
| REC-04 | recording.ready 与 user.utterance 共用 channel | 同一 events channel |

---

### 3.7 P6 — 可观测性

#### 3.7.1 Metrics

**文件**: `services/tests/unit/agent_relay/test_metrics.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| MET-01 | agent_events_total{kind, result} | publish 成功后 counter +1 |
| MET-02 | agent_replies_total{adapter, result} | delivered / dropped_offline / duplicate / timeout / error 分别计数 |
| MET-03 | agent_reply_latency_seconds{adapter} | utterance → reply 延迟记录 |
| MET-04 | agent_adapter_errors_total{adapter, error_type} | timeout / connection / http_error 分类计数 |
| MET-05 | MetricsRegistry 复用现有模式 | `_get_or_create` 线程安全 |

#### 3.7.2 日志

**文件**: `services/tests/unit/agent_relay/test_logging.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| LOG-01 | logger namespace | `livachat.agent_relay.*` |
| LOG-02 | utterance 日志含截断文本 | text 超过 200 字符时截断 |
| LOG-03 | adapter 超时日志 | 含完整 request/response metadata |
| LOG-04 | structlog 绑定 | session_id, device_id, adapter 等上下文字段 |

#### 3.7.3 限流

**文件**: `services/tests/unit/agent_relay/test_rate_limiter.py`

| ID | 测试用例 | 预期 |
|----|----------|------|
| RLM-01 | per-adapter 回复频率限制 | 超过上限后丢弃，记 rate_limited metric |
| RLM-02 | per-device push 频率限制 | 超过上限后丢弃 |
| RLM-03 | 单实例 push channel 全局吞吐限制 | 超过上限后排队/丢弃 |
| RLM-04 | 限流窗口重置 | 窗口过后恢复 |

---

### 3.8 P7 — 全链路 E2E 测试

#### 3.8.1 Mock Agent Server

**文件**: `tools/local-dev/mock-services/mock_agent_server/`

```python
# 最小实现：FastAPI + SSE endpoint
# POST /api/chat → SSE stream (模拟 Hermes 行为)
# GET /api/health → 200
```

| ID | 测试用例 | 预期 |
|----|----------|------|
| MCK-01 | mock server 启动 | `/api/health` 返回 200 |
| MCK-02 | POST /api/chat → SSE stream | 返回 text/event-stream，包含 data chunks + [DONE] |
| MCK-03 | 模拟延迟响应 | configurable delay (测试超时场景) |
| MCK-04 | 模拟错误响应 | configurable error rate / status code |

#### 3.8.2 E2E 场景

**文件**: `services/tests/e2e-backend/scenarios/test_agent_relay_e2e.py`

| ID | 场景 | 步骤 | 验收 |
|----|------|------|------|
| E2E-01 | **Reactive: 一问两答** | 1. app 连接 → session.start 2. 发送 audio (触发 ASR final) 3. LLM 生成第一条 ai_text 4. relay → HermesAdapter → 第二条 ai_text | app 收到两条独立 ai_text: source=llm + source=agent |
| E2E-02 | **Reactive: agent 不可用** | 1. app 连接 2. 发送 audio 3. Hermes 500 错误 | LLM ai_text 正常到达；agent 错误仅记 metric，不影响主链路 |
| E2E-03 | **Reactive: agent 超时** | 1. app 连接 2. 发送 audio 3. Hermes 无响应 (timeout) | LLM ai_text 正常；timeout metric 记录 |
| E2E-04 | **Proactive: recording.ready → push** | 1. app 连接 2. 录音落盘 3. relay → analyze_recording 4. agent 返回分析结果 | app 收到 proactive=true 的 ai_text |
| E2E-05 | **Device 离线丢弃** | 1. app 断开 2. 录音落盘 3. agent 分析完成 → push | push 失败，记 dropped_offline metric |
| E2E-06 | **Barge-in 后 agent 迟到回复** | 1. 问 Q1 → ASR final 2. user barge-in → 问 Q2 3. Q1 的 agent 回复到达 | Q2 正常处理；Q1 agent 回复仍推送（app 决定是否展示） |
| E2E-07 | **多 adapter 并行** | (Phase 2) hermes + openclaw 同时回复 | 两条 agent ai_text，不同 agent_name |
| E2E-08 | **ASR only 模式** | 1. `LIVACHAT_PIPELINE_MODE=asr_only` 2. 发送 audio → ASR final | agent 链路正常（无 LLM 回答），收到 agent ai_text |
| E2E-09 | **relay 重启恢复** | 1. relay 正常运行 2. kill relay 3. 重启 relay | events channel 订阅恢复，新 session 正常 |
| E2E-10 | **agent_relay.enabled=false** | 1. 配置关闭 2. 正常对话 | 无 agent 链路行为，零开销 |

---

## 4. 测试基础设施

### 4.1 新增 Fixtures (`services/tests/conftest.py`)

```python
# agent-relay 相关 fixtures

@pytest.fixture
async def fake_redis():
    """fakeredis client for agent-relay tests."""
    ...

@pytest.fixture
async def mock_agent_http(respx_mock):
    """Mock Hermes HTTP API via respx."""
    ...

@pytest.fixture
def agent_relay_config():
    """Minimal valid AgentRelayConfig for tests."""
    ...

@pytest.fixture
async def mock_adapters():
    """Dict of mock AgentAdapter instances."""
    ...

@pytest.fixture
async def session_manager(mock_adapters, mock_publisher):
    """AgentSessionManager with mocked adapters."""
    ...

@pytest.fixture
def mock_orchestrator_ctx():
    """Minimal SessionContext for orchestrator tests."""
    ...
```

### 4.2 新增目录结构

```
services/tests/
├── unit/
│   └── agent_relay/
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_config.py
│       ├── test_models.py
│       ├── test_errors.py
│       ├── test_router.py
│       ├── test_session_manager.py
│       ├── test_publisher.py
│       ├── test_metrics.py
│       ├── test_logging.py
│       ├── test_rate_limiter.py
│       └── adapters/
│           ├── __init__.py
│           ├── test_base.py
│           ├── test_loader.py
│           └── test_hermes.py
├── integration/
│   └── agent_relay/
│       ├── __init__.py
│       ├── conftest.py
│       └── test_session_manager_integration.py
└── e2e-backend/
    └── scenarios/
        └── test_agent_relay_e2e.py

tools/local-dev/mock-services/
└── mock_agent_server/
    ├── __init__.py
    ├── app.py          # FastAPI app
    └── sse_handler.py  # SSE endpoint
```

### 4.3 测试依赖

`pyproject.toml` 已有依赖（无需新增）：
- `pytest>=8` + `pytest-asyncio>=0.23` + `pytest-cov>=5`
- `respx>=0.21` — HTTP mock
- `fakeredis>=2.24` — Redis mock
- `anyio>=4` — async backend

可能需要新增（开发联调用）：
- `sse-starlette>=2` — mock agent server 的 SSE endpoint（仅 dev dependency）

---

## 5. 开发顺序（建议）

```
Day 1-2   ██████  P0: config.py, models.py, errors.py, 目录骨架
Day 3-5   ██████  P1: adapters/base.py, hermes.py, loader.py (openclaw.py 仅 stub)
Day 6-8   ██████  P2: router.py, session_manager.py, publisher.py
Day 9     ██████  P3: services/session/agent_publisher.py, orchestrator.py 改动
Day 10-11 ██████  P4: gateway push subscriber, ai_text schema 扩展, DTO 生成
Day 12-13 ██████  P5: recording 接入
Day 14    ██████  P6: metrics, 日志, 限流
Day 15-16 ██████  P7: mock agent server, e2e 联调, bug fix
```

每个 P 阶段完成后跑对应层级的测试，P7 之前不进行 e2e 验证。

---

## 6. 验收标准 (Phase 1)

### 6.1 功能验收

- [ ] `agent_relay.enabled=false` 时，全线零开销零行为变化
- [ ] `agent_relay.enabled=true` 时，ASR final → agent → ai_text 全链路通
- [ ] Mock agent HTTP server + asr_only 模式下，一问收到两答（两条独立 ai_text）
- [ ] agent 不可用（超时/5xx/断连）不影响 LLM 主链路
- [ ] device 不在线时 push 丢弃 + metric
- [ ] schema 向后兼容：旧客户端正常解析 LLM ai_text（无 source 字段）
- [ ] 多 adapter 并行调用，任一失败不互相影响（Phase 1: HermesAdapter；框架支持多 adapter 并行但实际仅启用 1 个）
- [ ] 幂等：重复 transcription_id/recording_id 不重复处理

### 6.2 质量验收

- [ ] 所有 unit tests 通过 (目标覆盖率 > 85%)
- [ ] 所有 integration tests 通过
- [ ] 所有 e2e scenarios 通过
- [ ] `ruff check` 无新增 error
- [ ] `mypy` 无新增 type error
- [ ] Prometheus metrics 在 `/metrics` endpoint 正确暴露
- [ ] 日志格式符合 structlog 规范

### 6.3 性能验收

- [ ] orchestrator ASR final 路径增加延迟 < 1ms（fire-and-forget）
- [ ] relay 内存 baseline < 50MB（无 session 时）
- [ ] 单 relay 实例支持 1000 并发 session

---

## 7. 未覆盖的边界场景（Phase 2+ 补充）

| 场景 | 处理策略 | 计划阶段 |
|------|----------|----------|
| Redis Pub/Sub 消息丢失 (at-most-once) | Phase 1 接受，Phase 2 换 Redis Stream | Phase 2 |
| Agent 回复持久化（离线消息） | Phase 1 丢弃，Phase 2 评估 | Phase 2 |
| 多 relay 实例负载均衡 | Redis Pub/Sub 天然多播，无需额外处理 | Phase 1 已覆盖 |
| agent 回答 TTS 播报 | Phase 3 评估打断/排队策略 | Phase 3 |
| 富媒体卡片/action 协议 | Phase 3 | Phase 3 |
| APNs/FCM 离线推送 | Phase 3 | Phase 3 |
| Adapter 热加载/热卸载 | 重启 relay | Phase 2 |
| Agent 回复内容审核 | 由外部 agent 自行负责 | Phase 1 不做 |
