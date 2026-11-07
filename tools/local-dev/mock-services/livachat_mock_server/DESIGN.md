# LivaChat Mock Server — 设计文档

## 1. 概述

`livachat-mock-server` 是一个本地开发用的 WebSocket 模拟服务，为 iOS / Android 客户端提供完整的 LivaChat 协议测试环境。它不依赖真实的 ASR / LLM / TTS 服务，而是根据预设场景（scenario）自动生成协议消息流。

**设计原则**：

- **严格复用协议 DTO**：所有消息的解析和构建均通过 `packages/sdk-python` 中 `livachat_protocol.generated` 的 Pydantic 模型完成，确保 mock server 产出的消息与真实 Gateway 格式一致
- **可配置场景**：通过环境变量切换 11 种内置场景，覆盖正常对话、异常、超时、断线重连等关键路径
- **无状态 + 轻量**：纯内存会话存储，零外部依赖，秒级启动

## 2. 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| Web 框架 | FastAPI + Uvicorn | 同时支持 REST 和 WebSocket；与文档中 "Python FastAPI gateway" 一致 |
| 协议模型 | Pydantic (livachat-protocol) | 从 `packages/api-schema` JSON Schema 自动生成，SSOT |
| 测试 | pytest + websockets + httpx | 异步 WS 客户端 + REST 请求一体化测试 |

## 3. 模块结构

```
src/livachat_mock_server/
├── config.py          # ServerConfig dataclass + Scenario enum
├── protocol.py        # 消息构建/解析 helper，复用 livachat_protocol DTO
├── session_store.py   # SessionContext + SessionStore (内存 TTL)
├── scenarios.py       # 场景引擎：转写流、AI 文本流、打断等
├── app.py             # FastAPI create_app 工厂（REST + WS 路由）
├── main.py            # 公开导出的入口
└── media_fixtures.py  # Mock Opus 音频 base64 fixture
```

### 3.1 config.py

- `Scenario`：枚举 11 种内置场景
- `ServerConfig`：dataclass，所有参数支持环境变量覆盖（`MOCK_HOST`, `MOCK_PORT`, `MOCK_SCENARIO` 等）

### 3.2 protocol.py

消息层薄封装：

- `parse_client_message(raw)` → 调用 `ClientMessageAdapter.validate_json()`
- `build_session_ready(seq, session_id, restored)` → 返回 `ServerSessionReady` 模型
- `build_*()` 系列函数：每种 S2C 消息一个 builder，保证类型安全
- `dump_server_message(msg)` → 调用 `ServerMessageAdapter.dump_json()`

### 3.3 session_store.py

- `SessionContext`：per-connection 状态（session_id, seq 计数, VAD silence 计数, 打断标记, streaming task 引用）
- `SessionStore`：内存字典，`get()` 自动检查 TTL 过期

### 3.4 scenarios.py

场景运行逻辑，提供两个异步入口：

- `send_interim_transcriptions()`：周期性发送 ASR 中间结果（4 条，每条间隔 150ms）
- `run_response_flow()`：完整话轮结束后的响应流
  - 最终转录 → status(thinking) → status(speaking) → ai_text 流式 → ai_audio 流式 → status(listening)

两个函数均接收 `SendFunc` 回调用于消息发送，并检查 `ctx.interrupted` 以支持打断取消。

### 3.5 app.py

FastAPI 应用工厂 `create_app(config) -> FastAPI`：

**REST 路由**：
- `GET /healthz` → `{"ok": true}`
- `POST /v1/auth/token` → 返回 `dev-token`

**WebSocket 路由** `GET /v1/ws/stream`：
1. 校验 token 与 device_id（auth_failed 场景直接拒绝）
2. 等待第一条消息（必须是 `session.start`）
3. 处理会话恢复（resume_success / resume_expired）
4. 发送 `session.ready` + 初始 status
5. 消息循环：模式匹配分发到各消息类型处理器
6. server_error / idle_timeout / ping_timeout 场景在此阶段生效

## 4. 状态机

```
idle → listening → (ASR final) → thinking → (TTS start) → speaking → (TTS finish) → listening → ...
                                                    ↑ interrupt ↓
                                                    └────────────┘
```

服务端主导状态变更，客户端收到 `status` 消息后更新本地 UI。

## 5. 场景矩阵

| 场景 | 建连 | 转写 | AI 文本 | AI 音频 | 视频 ack | 特殊行为 |
|------|------|------|---------|---------|----------|----------|
| default | Y | Y | Y | Y | Y | — |
| text_only | Y | Y | Y | N | Y | 适合早期 UI 联调 |
| with_audio | Y | Y | Y | Y | Y | 音频播放队列联调 |
| slow_network | Y | Y | Y | Y | Y | 每条 S2C 延迟 500-2000ms |
| server_error | Y | N | N | N | N | 建连后立即推送 error 1001 |
| auth_failed | N | — | — | — | — | 握手阶段 4001 |
| idle_timeout | Y | N | N | N | N | 无消息后 4003 |
| ping_timeout | Y | N | N | N | N | 不响应 ping，超时 4002 |
| resume_success | Y | Y | Y | Y | Y | session_restored=true |
| resume_expired | Y | Y | Y | Y | Y | session_restored=false, 新 session_id |
| interrupt | Y | Y | Y | Y | Y | SPEAKING 状态可打断 |

## 6. 客户端接入指引

### iOS 模拟器
```
ws://localhost:8080/v1/ws/stream?token=dev-token&device_id=00000000-0000-0000-0000-000000000001
```

### Android 模拟器
```
ws://10.0.2.2:8080/v1/ws/stream?token=dev-token&device_id=00000000-0000-0000-0000-000000000001
```

### 真机
```
ws://<电脑局域网 IP>:8080/v1/ws/stream?token=dev-token&device_id=<UUID>
```

## 7. 测试策略

24 个 pytest 用例覆盖：

- **协议流测试** (`test_protocol_flow.py`)：建连、session 生命周期、ping/pong、audio → transcription、video ack、interrupt、seq 单调性、非法 device_id、首消息校验
- **场景测试** (`test_scenarios.py`)：全部 11 个 scenario，每个一条独立测试
- **契约测试**：`test_contract_all_s2c_messages_valid` — 完整对话流程中所有 S2C 消息均通过 `ServerMessageAdapter.validate_json()` 校验

所有测试使用真实的 WS 连接（uvicorn 线程 + websockets 库），无需 mock。
