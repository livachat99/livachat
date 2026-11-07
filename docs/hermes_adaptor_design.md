# Hermes Adapter WebSocket JSON-RPC 2.0 设计

## 背景

当前 `HermesAdapter` (`services/agent_relay/adapters/hermes.py`) 使用 HTTP POST `/api/chat` + SSE 流式响应，但该端点**在真实 Hermes Dashboard 上并不存在**。根据 `docs/hermes_websocket_api.md` 调研，真实协议是 **WebSocket JSON-RPC 2.0** at `/api/ws`。

## 核心设计

**连接模型**：单条 WS 长连接 + `asyncio.Lock` 串行化 `ask()` 调用。

- Token 从 `api_key` config → 作为 `?token=` query param
- URL 从 `base_url` → `ws://host:port/api/ws?token=...`

### `ask()` 流程

```
1. acquire lock
2. connect WS → recv gateway.ready
3. session.create {close_on_disconnect:true, source:"tool"}   # 不带 messages
4. prompt.submit {session_id, text}   # 仅当前 utterance
5. event loop:
   message.delta     → 忽略（见"流式取舍"）
   thinking.delta    → 忽略
   reasoning.delta   → 忽略
   approval.request  → auto-deny (approval.respond choice=deny all=true)
   message.complete  → yield AgentResponseChunk(text=payload.text, is_final=True), break
   error             → raise AdapterConnectionError
6. session.close (in finally)
7. release lock
```

**流式取舍（方案B）**：`AgentSessionManager._call_adapter` 将所有 chunk 的 text **无条件拼接**后 publish（metadata 被忽略），因此 delta 事件一律丢弃，仅在 `message.complete`（`payload.text` 为全量文本）时 yield 唯一一个 final chunk。若同时 yield delta，正文会重复；若 yield thinking/reasoning 的非空 text，思考内容会泄漏进用户回复。

**历史取舍**：Hermes 服务端自带会话历史记录（`stored_session_id` 持久化），目前**不发送 history**——`session.create` 不带 `messages`，`prompt.submit` 仅提交当前最新 utterance。跨轮上下文后续可再评估 `close_on_disconnect=false` + `session.resume` 的持久会话映射。（2026-10-18：`recent_turns` 窗口已移除，relay 不再向 adapter 传递历史；`session_history_turns` 配置字段保留为 deprecated no-op。）

### 审批兜底

无人值守场景，`approval.request` 事件必须自动 `approval.respond(choice="deny", all=true)`，否则 turn 永久阻塞。

### `analyze_recording`

继承 base class 的 no-op 实现（Hermes WS 无对应端点）。

## 需修改的文件

### 1. `services/agent_relay/adapters/hermes.py` — 完整重写

核心结构：

```
HermesAdapter(AgentAdapter):
  __init__: cfg, _ws=None, _lock=asyncio.Lock(), _req_id=0
  name = "hermes"
  connect(): 建立 WS 连接，等待 gateway.ready
  close(): 关闭 WS
  ask(): async with _lock → _ask_impl()
  analyze_recording(): no-op (继承 base)

  _connect_ws(): websockets.connect(ws_url)
  _next_id(): 自增请求 ID
  _ask_impl(): 完整 JSON-RPC 流
```

### 2. `services/tests/unit/agent_relay/adapters/test_hermes.py` — 重写

替换 `respx` HTTP mock → `websockets.serve` 本地 WS mock server。测试覆盖：

- 完整响应（delta 事件被忽略，仅取 `message.complete` 全量文本）
- approval auto-deny
- error event / JSON-RPC error
- connection refused / drops mid-stream / timeout
- retry on connection drop
- empty response
- analyze_recording no-op

### 3. `services/tests/integration/agent_relay/test_hermes_adapter_integration.py` — 更新

对接 mock WS server 做端到端测试。

### 4. `tools/local-dev/mock-services/mock_agent_server/app.py` — 更新

添加 `/api/ws` JSON-RPC WebSocket 端点，模拟 hermes 行为。

### 不修改的文件

- `config.py` — `HermesAdapterConfig` 字段足够（`base_url`, `api_key`, `timeout_s`, `max_retries`）
- `loader.py` — 类名和构造签名不变
- `errors.py` — 错误类型与传输无关
- `base.py` — 接口不变
- `session_manager.py` — 消费者与传输无关

## 依赖

`websockets>=13` 已在 `services/pyproject.toml` 中。

## 验证

1. `python -m pytest services/tests/unit/agent_relay/adapters/test_hermes.py -v`
2. `python -m pytest services/tests/unit/agent_relay/adapters/test_loader.py -v`
3. 启动 mock WS server: `python tools/local-dev/mock-services/mock_agent_server/app.py`
4. `python -m pytest services/tests/integration/agent_relay/test_hermes_adapter_integration.py -v`
5. 手动验证（如有本地 Hermes Dashboard）：dev config 设置 `agent_relay.enabled=true`，启动 server，确认 adapter 连接成功
