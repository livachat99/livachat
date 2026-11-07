# Agent Relay Service — Design

> **Status**: reviewed
> **Date**: 2026-07-26
> **Scope**: 新增后端微服务 `services/agent-relay/`，打通 LivaChat server 与外部 Agent（Hermes / OpenClaw 等）的双向通道。

---

## 1. 功能说明

### 1.1 背景与目标

当前对话链路是单路的：ASR final → LLM（实时、浅回答）→ TTS → app。LLM 受实时性约束（首 token 延迟预算 < 1s），无法调用工具、检索外部数据或做深度推理。

引入 **Agent Relay Service**（以下简称 relay），在不影响实时链路的前提下，把会话事件分发给外部 agent，并把 agent 的产出回投给 app：

1. **Reactive（应答式）**：ASR transcription(final) 实时分发给 agent；agent 通过 tools（搜索、日历、家居控制等）生成更丰富的回答，以额外的 `ai_text` 推给 app。一个用户问题，app 可收到多条回答：
   - LLM 实时回答（现有链路，毫秒~秒级）
   - Agent 增强回答（relay 链路，秒~分钟级，非实时）
2. **Proactive（主动式）**：agent 订阅录制内容（recording）事件，离线分析提炼（如会议纪要、待办提取、学习报告），主动向 app push `ai_text`。

### 1.2 明确不做（Phase 1）

- Agent 回答**不走 TTS**（仅文本；语音播报待 Phase 3 评估打断/排队策略后再定）
- Agent 不可见实时音频流（只能拿到 ASR 文本与离线录制文件）
- 离线推送（APNs/FCM）——device 不在线时丢弃 + metric，不接厂商推送通道
- 多 agent 复杂编排（路由规则 Phase 1 仅支持按配置广播到所有启用 adapter）
- 富媒体卡片 / action 协议（Phase 1 纯文本）
- 离线消息持久化（Phase 1 device 不在线直接丢弃；按 §7.3 处理）

---

## 2. 总体架构

### 2.1 关键决策

第三方 Agent（Hermes、OpenClaw 等）提供的是 **public API**（HTTP/SSE/gRPC），不会实现我们定义的私有 WS 协议。因此 Relay **内置 Adapter**，由 Relay 主动连接外部 Agent，而非等待 Agent 连入。

| 决策点 | 选择 | 理由 |
|--------|------|------|
| relay 部署模式 | **独立微服务** `services/agent-relay/` | agent 连接的生命周期/鉴权/扩缩容与 app 连接完全不同；故障隔离（agent 异常不影响实时对话） |
| 内部传输（orchestrator→relay, relay→gateway） | **Redis Pub/Sub** | 复用现有 Redis 基础设施；天然解耦多实例路由；与 recording sink 同为 fire-and-forget 模式 |
| agent 接入方式 | **Relay 内置 Adapter，主动调用外部 Agent API** | 三方 agent 提供 public API（HTTP/SSE），不实现私有协议；Adapter 封装差异 |
| 投递给 app 的载体 | **复用 `ai_text` 消息**（schema 加可选字段） | 客户端渲染逻辑零改动可跑通；新字段 optional 向后兼容 |

phase1: agent-relay service嵌入到LivaChat Server. 单实例部署.  phase2 or phase3再做独立实例部署. 

### 2.2 架构图

```
                 ┌────────────────────────────────────────────────────┐
                 │                 LivaChat Server                     │
                 │                                                     │
  app ──WS──▶ gateway ──▶ session orchestrator ──▶ ASR/LLM/TTS        │
   ▲            ▲  │                    │                             │
   │            │  │                    │ ASR final / recording.ready │
   │            │  │                    │ (fire-and-forget)           │
   │            │  │                    ▼                             │
   │            │  │            ┌── Redis Pub/Sub ──────────────────┐ │
   │            │  │            │ ch: livachat:agent:events         │ │
   │            │  │            │ ch: livachat:push:{instance_id}   │ │
   │            │  │            └──▲───────────────────────▲────────┘ │
   │            │  │               │ subscribe             │ publish │
   │            │  └───────────────┘                       │         │
   │            │  (gateway 订阅本实例 push channel)         │         │
   │            └───────────────────────────────────────────│─────────│
   │              agent ai_text 推入 outbound queue          │         │
   └────────────────────────────────────────────────────────│─────────┘
                                                            │
                 ┌──────────────────────────────────────────│─────────┐
                 │          agent-relay service             │         │
                 │                                          │         │
                 │  ┌────────────────┐   ┌────────────────┐ │         │
                 │  │  event router  │──▶│ session manager │ │         │
                 │  │  (subscribe    │   │ (1:1 w/ chat    │ │         │
                 │  │   events ch)   │   │  sessions)      │ │         │
                 │  └────────────────┘   └───────┬─────────┘ │         │
                 │                               │           │         │
                 │  ┌────────────────┐   ┌───────┴─────────┐ │         │
                 │  │  push          │   │    adapters      │ │         │
                 │  │  publisher     │   │  ├─ HermesAdapter │ │         │
                 │  │  → push ch     │   │  ├─ OpenClaw     │ │         │
                 │  └────────────────┘   │  └─ Custom       │ │         │
                 │                       └───────┬─────────┘ │         │
                 └───────────────────────────────│───────────┘         │
                                                 │                     │
                                    HTTP/SSE/gRPC                      │
                                                 │                     │
                          ┌──────────────────────┴───────────────────┐ │
                          ▼                                          ▼ │
                    ┌───────────┐                            ┌───────────┐
                    │  Hermes   │                            │ OpenClaw  │
                    │  (public  │                            │ (public   │
                    │   HTTP    │                            │  API)     │
                    │   API)    │                            │           │
                    └───────────┘                            └───────────┘
```

---

## 3. 消息流

### 3.1 Reactive：ASR final → agent → ai_text

```
1. orchestrator: ASR final (orchestrator.py ASR final 分支)
2.   ├─(现有, 实时)──▶ _start_thinking() ─▶ LLM ─▶ ai_text (source=llm) ─▶ app
3.   └─(新增, fire-and-forget)──▶ AgentEventPublisher.publish("user.utterance")
                                      │
4.   relay: 订阅 events channel ◀───────┘
5.   relay: 路由到对应 session 的 SessionManager
6.   relay: SessionManager 调用 adapter.ask(text, context)
7.   adapter: HTTP/SSE 调用外部 Agent API（任意耗时）
8.   adapter: 流式/批量接收 Agent 响应
9.   relay: 查 conn:{device_id} → instance_id
10.  relay: publish ─▶ livachat:push:{instance_id}
11.  gateway(该实例): 订阅收到 ─▶ ConnectionHandler.enqueue_outbound(ai_text frame)
12.  app: 收到第二条 ai_text (source=agent, reply_to=transcription_id)
```

时序特性：步骤 3 与 LLM pipeline **并行**，publish 失败仅记日志+metric，绝不阻塞/失败主链路（同 recording sink 约定）。

### 3.2 Proactive：recording 分析 → 主动 push

```
1. recording service: 录音段落落盘完成
2.   └─▶ publish("recording.ready", {recording_id, device_id, session_id, manifest_path, duration_ms})
3. relay: 订阅收到 ─▶ SessionManager.on_recording_ready(data)
4. relay: SessionManager 调用 adapter.analyze_recording(manifest_path, context)
5. adapter: 拉取录音转写文本，调用外部 Agent 分析（分钟~小时级均可）
6. adapter: 返回分析结果
7. relay ─▶ push channel ─▶ gateway ─▶ ai_text (source=agent, proactive=true) ─▶ app
   （device 离线时按 §7.3 处理）
```

### 3.3 示例：一问两答

```
User: "今天北京天气怎么样？"
  │
  ├─[实时路径] LLM → "今天北京天气晴朗，22°C..."
  │   └─ ai_text {message_id: "msg_001", is_final: true, source: "llm"}
  │
  └─[Agent 路径] ASR final → Redis pub → Relay → HermesAdapter
      HermesAdapter: POST /api/chat (SSE) → Hermes 调用 weather API, web search → 聚合
      → Redis pub → Gateway → ai_text {
          message_id: "msg_agent_002",
          source: "agent",
          agent_name: "hermes",
          reply_to: "tr-xxx",
          is_final: true
        }
      
      "北京当前 22°C，晴，湿度 45%，PM2.5 28 优。
       未来三天持续升温。出行建议：适合户外活动。"
```

App 收到两条独立 ai_text 流，按 `message_id` 分别在 UI 中展示，agent 消息打标签展示来源。

### 3.4 示例：Agent 主动推送

```
Recording 段落落盘 → publish recording.ready → Relay → HermesAdapter
  HermesAdapter: 拉取转写文本 → POST /api/analyze → Hermes 分析
  → 发现用户提到行程计划
  → push → Gateway → ai_text {
      message_id: "msg_agent_003",
      source: "agent",
      agent_name: "hermes",
      proactive: true,
      is_final: true
    }
  
  "我注意到你提到了下周去东京的行程。
   需要我帮你查航班、酒店或者做行程规划吗？"
```

---

## 4. 协议设计

### 4.1 App 侧：扩展 `s2c/ai-text.json`（向后兼容）

新增 **optional** 字段：

```jsonc
"source": {
  "type": "string",
  "enum": ["llm", "agent"],
  "description": "Origin of this message. Absent = \"llm\" (backward compatible)."
},
"reply_to": {
  "type": "string",
  "description": "For agent replies: transcription_id of the user utterance this answers."
},
"agent_name": {
  "type": "string",
  "description": "When source=\"agent\": identifies which agent produced this message (e.g. \"hermes\")."
},
"proactive": {
  "type": "boolean",
  "description": "Whether this is a proactive push (not in response to a user utterance)."
}
```

- 现有 LLM 链路**不设** `source`（或显式 `"llm"`），旧客户端完全无感。
- Agent 每条回复使用独立 `message_id`（约定前缀 `msg_agent_` 便于日志排查），`index`/`is_final` 语义不变。
- DTO 由 `packages/api-schema/` 生成，禁止手改。
- `agent_name` 用于 App 端展示来源标签；`reply_to` 用于锚定到原始问题下方。

### 4.2 Adapter 接口（relay 内部）

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class AgentContext:
    """传递给 adapter 的上下文信息。"""
    session_id: str
    device_id: str
    language: str


@dataclass
class AgentResponseChunk:
    """Adapter 返回的响应片段。"""
    text: str
    is_final: bool = False
    # 可选：agent 侧的结构化元数据
    metadata: dict | None = None


@dataclass
class RecordingInfo:
    """传递给 adapter 的录制信息。"""
    recording_id: str
    session_id: str
    device_id: str
    manifest_path: str
    duration_ms: int
    # relay 提供的只读 transcript endpoint
    transcript_endpoint: str


class AgentAdapter(ABC):
    """外部 Agent 的抽象适配器。

    每个 adapter 封装一种外部 Agent 的通信协议（HTTP/SSE/gRPC/...）。
    Relay 通过统一的接口调用，不关心底层协议差异。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter 标识名，对应配置中的 adapter key 和 ai_text.agent_name。"""
        ...

    async def connect(self) -> None:
        """建立到外部 Agent 的长连接（可选，如 keep-alive HTTP session）。"""
        pass

    async def close(self) -> None:
        """关闭连接，释放资源。"""
        pass

    @abstractmethod
    async def ask(
        self,
        text: str,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """发送用户问题，返回 agent 响应流。

        Reactive 场景：每次 ASR final 触发一次调用。
        返回 AsyncIterator 支持流式（SSE）和批量两种响应模式。
        """
        ...

    async def analyze_recording(
        self,
        recording: RecordingInfo,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """分析录制内容，返回主动推送。

        Proactive 场景：recording.ready 事件触发。
        默认实现为空操作，子类按需覆盖。
        """
        # 默认不实现，子类按需覆盖
        return
        yield  # type: ignore[unreachable]
```

### 4.3 内部消息（Redis Pub/Sub payload）

```jsonc
// livachat:agent:events — orchestrator/recording → relay
{
  "kind": "user.utterance" | "recording.ready" | "session.started" | "session.ended",
  "device_id": "...",
  "session_id": "...",
  "ts_ms": 1721894400000,
  // kind=user.utterance 时:
  "transcription_id": "...",
  "text": "...",
  "language": "...",
  // kind=recording.ready 时:
  "recording_id": "...",
  "manifest_path": "...",
  "duration_ms": 3720000
}

// livachat:push:{instance_id} — relay → gateway
{
  "kind": "ai_text",
  "device_id": "...",
  "session_id": "...",
  "message_id": "msg_agent_01J...",
  "text": "...",
  "index": 0,
  "is_final": true,
  "source": "agent",
  "agent_name": "hermes",
  "reply_to": "tr-xxx",
  "proactive": false
}
```

---

## 5. 组件设计

### 5.1 `services/agent-relay/`（新增）

结构对齐现有服务（`gateway/`、`session/` 风格）：

```
services/agent-relay/
├── config.py              # pydantic-settings
├── application.py         # FastAPI lifespan：Redis 连接、event router、session manager
├── router.py              # events channel 订阅 + 事件路由 → SessionManager
├── publisher.py           # relay → gateway push channel 发布（含 instance_id 解析）
├── session_manager.py     # AgentSessionManager：1:1 映射到 LivaChat session
├── adapters/
│   ├── __init__.py
│   ├── base.py            # AgentAdapter ABC + AgentContext/AgentResponseChunk/RecordingInfo
│   ├── hermes.py          # HermesAdapter (HTTP/SSE)
│   ├── openclaw.py        # OpenClawAdapter (HTTP/gRPC)
│   └── loader.py          # adapter factory: name → Adapter class
├── models.py              # 内部消息模型（pydantic）
└── errors.py              # 错误类型定义
```

### 5.2 Session 生命周期管理

```python
class AgentSessionManager:
    """管理 Agent 侧会话生命周期，1:1 映射到 LivaChat chat session。"""

    def __init__(self, adapters: dict[str, AgentAdapter], publisher: PushPublisher):
        self._adapters = adapters
        self._publisher = publisher
        self._sessions: dict[str, _AgentSession] = {}

    async def on_session_started(self, event: SessionStarted) -> None:
        """创建 agent 侧会话，初始化各 adapter 的上下文。"""
        ...

    async def on_user_utterance(self, event: UserUtterance) -> None:
        """并行调用所有启用的 adapter.ask()，汇总响应推送到 gateway。"""
        ...

    async def on_recording_ready(self, event: RecordingReady) -> None:
        """并行调用各 adapter.analyze_recording()，汇总响应推送。"""
        ...

    async def on_session_ended(self, event: SessionEnded) -> None:
        """清理 agent 侧会话资源。"""
        ...

    async def shutdown(self) -> None:
        """关闭所有 adapter 连接。"""
        for adapter in self._adapters.values():
            await adapter.close()
```

**关键行为**：

- 同一 session 的多个 adapter 并行调用，各自独立返回响应
- 任一 adapter 失败不影响其他 adapter
- Adapter 超时由配置控制，超时后取消并记 metric
- 同一个 `utterance_id` 的重复事件（Redis at-most-once 下不太可能但仍做防御）幂等处理：已处理过的 id 直接丢弃

### 5.3 Adapter 实现

**HermesAdapter** — 通过 HTTP + SSE 对接 Hermes：

```python
class HermesAdapter(AgentAdapter):
    name = "hermes"

    async def ask(self, text: str, context: AgentContext) -> AsyncIterator[AgentResponseChunk]:
        # POST {base_url}/api/chat
        # Body: { "text": ..., "session_id": ..., "history": ..., "language": ... }
        # Response: SSE stream (text/event-stream)
        #   每个 SSE event 映射为一个 AgentResponseChunk
        ...

    async def analyze_recording(self, recording: RecordingInfo, context: AgentContext):
        # GET {transcript_endpoint} 获取转写文本
        # POST {base_url}/api/analyze
        # Body: { "transcript": ..., "session_id": ..., "duration_ms": ... }
        # Response: SSE stream → AgentResponseChunk
        ...
```

**OpenClawAdapter** — 对接 OpenClaw agent framework：

```python
class OpenClawAdapter(AgentAdapter):
    name = "openclaw"

    async def ask(self, text: str, context: AgentContext) -> AsyncIterator[AgentResponseChunk]:
        # 通过 OpenClaw HTTP API 创建 agent run
        # 轮询 run steps 或订阅 webhook
        ...

    # Phase 1 不实现 analyze_recording（默认空操作）
```

### 5.4 orchestrator 接入点（最小侵入）

新增 `services/session/agent_publisher.py`：`AgentEventPublisher`，封装 Redis publish，接口：

```python
class AgentEventPublisher:
    def __init__(self, redis_client, events_channel: str):
        ...

    async def publish_utterance(
        self, session_id: str, device_id: str,
        text: str, transcription_id: str, language: str,
    ) -> None:
        """Fire-and-forget; 失败仅记日志+metric，绝不抛异常。"""
        ...

    async def publish_session_started(self, ...) -> None: ...
    async def publish_session_ended(self, ...) -> None: ...
```

在 `orchestrator.py` ASR final 分支（现 `meaningful or had_asr_final` 处）追加：

```python
# 新增：发布到 Agent Relay Bus（fire-and-forget）
if self._agent_publisher is not None and (meaningful or had_asr_final):
    asyncio.create_task(
        self._agent_publisher.publish_utterance(
            session_id=self._ctx.session_id,
            device_id=self._handler.principal.device_id,
            text=text,
            transcription_id=self._current_transcription_id,
            language=detected_lang,
        )
    )
```

- 通过配置注入；未配置 relay 时 `self._agent_publisher = None`，零开销零行为变化。
- publish 失败**绝不**影响主链路：内部 try/except + logger.warning + metric counter。
- `LIVACHAT_PIPELINE_MODE=asr_only` 下同样发布（该模式是分阶段联调 agent 链路的最佳环境）。
- **Interrupt 语义**：用户 barge-in 取消的是 LLM/TTS pipeline；已发出的 utterance 事件不撤回（agent 回复晚到时 app 按 `reply_to` 自行决定是否仍展示——见 §9 Q1）。

### 5.5 gateway 接入点

- `application.py` lifespan 中新增一个订阅 task：`PushSubscriber` 订阅 `livachat:push:{instance_id}`（`instance_id` 复用现有值）。
- 收到 push 消息后：`ConnectionManager` 查 `device_id` → `ConnectionHandler.enqueue_outbound(frame)`；frame 由 gateway 侧 `WireCodec` 按 ai-text schema 构造，seq 由 writer 统一分配（复用现有 seq 单调性保证）。
- device 不在本实例/已下线：丢弃 + metric（离线策略见 §7.3）。

**实例路由依赖**：`conn:{device_id}→instance_id` 映射由 gateway 的 `ConnectionManager` 维护（现有机制）。Relay 侧 `PushPublisher` 通过该映射确定目标 push channel。映射不存在 = device 不在线，行为见 §7.3。

### 5.6 recording 接入点

- recording service 在录音段落落盘完成处 publish `recording.ready` 到 events channel（与 orchestrator 共用同一个 channel）。改动量小，直接复用 `AgentEventPublisher` 模式。
- relay 的适配器如需拉取转写文本，通过 recording service 的只读 REST API 获取（`GET /v1/recordings/{id}/transcript`），不直接访问文件系统。

---

## 6. 鉴权与安全

| 通道 | 机制 |
|------|------|
| orchestrator/recording → Redis events | 内网 Redis，不对外暴露；复用现有 Redis 凭据 |
| relay → 外部 Agent API | 每个 adapter 独立配置 `api_key`（env var 注入，如 `${HERMES_AUTH_TOKEN}`） |
| relay → Redis push channel | 复用现有 Redis 凭据；push 消息由 gateway 做 schema 校验 + 字段白名单 |
| 限流 | 复用 `RedisStore.check_message_rate` 模式：per-adapter 回复频率上限、per-device push 频率上限、单实例 push channel 全局吞吐上限 |

---

## 7. 可靠性与边界

### 7.1 失败语义

- Pub/Sub 是 at-most-once：relay 宕机期间的事件**丢失不补**（Phase 1 接受；Phase 2 如需可靠可换 Redis Stream，接口在 `publisher.py` 内封装，外部无感）。
- 外部 Agent 不可用（超时/5xx）：adapter 捕获异常，记 metric + 日志，不影响其他 adapter 和主链路。
- relay 重启：events channel 订阅随 lifespan 恢复；adapter 连接按需重建。

### 7.2 幂等

- `transcription_id` 为 Reactive 场景幂等键；`recording_id` 为 Proactive 场景幂等键。
- relay 侧 `AgentSessionManager` 维护已处理 id 集合（LRU，容量 10k），重复事件直接丢弃并记 `duplicate` metric。

### 7.3 离线投递策略（Phase 1）

- device 不在线（Redis `conn:` 无记录 或 push 到达时目标实例无连接）：**直接丢弃 + metric**。
- **不做离线持久化**（Phase 1 不落 `Message` 表，降低复杂度）。Agent 回复的时效性很强（天气查询在 30 分钟后毫无意义），离线存储的价值需要 Phase 2 用数据验证后再设计。

### 7.4 顺序与渲染

- 同一问题的两条 `ai_text`（llm / agent）`message_id` 不同、到达时间任意，协议层无顺序假设。
- App 按 `message_id` 分气泡渲染，agent 气泡用 `reply_to` 锚定到原问题下方。UX 细节由客户端设计补充。

### 7.5 多 Adapter 并发

- 同一 utterance 触发所有启用的 adapter 并行调用。
- 多个 adapter 各自返回的 `ai_text` 全部透传给 app（不做服务端仲裁或去重），app 按 `agent_name` 分气泡展示。
- 任一 adapter 超时/失败不影响其他 adapter。

---

## 8. 配置与可观测

### 8.1 配置

```yaml
# configs/dev/server.yaml
agent_relay:
  enabled: false                      # 总开关，默认关
  events_channel: "livachat:agent:events"
  push_channel_prefix: "livachat:push"
  max_reply_bytes: 16384
  default_adapters: ["hermes"]        # 默认启用的 adapter 列表

  adapters:
    hermes:
      base_url: "http://hermes.internal:8080"
      api_key: "${HERMES_AUTH_TOKEN}"
      timeout_s: 30
      max_retries: 2

    openclaw:
      api_url: "http://openclaw.internal:9000"
      api_key: "${OPENCLAW_API_KEY}"
      timeout_s: 60
      max_tool_calls_per_turn: 5

  proactive:
    enabled: true
    min_segment_duration_s: 10        # 最少 10s 录音才触发分析
    debounce_s: 30                    # 同一 session 两次分析的最小间隔
```

env：`LIVACHAT_AGENT_RELAY__ENABLED`、`LIVACHAT_AGENT_RELAY__ADAPTERS__HERMES__BASE_URL` 等（pydantic-settings 自动映射）。

### 8.2 Metrics

复用 `MetricsRegistry._get_or_create` 模式新增：

- `livachat_agent_events_total{kind, result}` — orchestrator/recording 发布计数
- `livachat_agent_replies_total{adapter, result}` — delivered / dropped_offline / duplicate / timeout / error
- `livachat_agent_reply_latency_seconds{adapter}` — utterance → reply 端到端时延
- `livachat_agent_adapter_errors_total{adapter, error_type}` — adapter 调用失败分类

### 8.3 日志

- `livachat.agent_relay.*` logger
- utterance/reply 全量记录（文本截断 200 字符）
- adapter 调用超时/失败记录完整 request/response metadata

---

## 9. 开放问题（Review 重点）

1. **Q1 — Interrupt 与迟到回复**：agent 回复到达时该 turn 已被 barge-in/会话已切走，app 是仍展示（文本无打断成本）还是丢弃？倾向「仍展示」，需要产品确认。
2. **Q2 — 双回答的 UX 契约**：app 是否需要把 agent 回复与 llm 回复合并展示（如"补充卡片"）？这决定 `reply_to` 是否够用、是否需要 llm 回答的 `message_id` 也透传给 agent 事件。
3. **Q3 — utterance 上下文窗口**（已移除，2026-10-18）：`recent_turns` 窗口已删除；`session_history_turns` 配置停止使用（字段保留为 deprecated no-op，避免存量 yaml 校验失败），历史上下文由各 adapter 自行维护。
4. **Q4 — 多 adapter 冲突**：Hermes 和 OpenClaw 同时回复同一 utterance 时全部透传，app 分气泡展示。是否需要服务端仲裁留待讨论。**（2026-10-18）Q4 保持开放；当前不新增服务端仲裁，monitor/任务书不注入同侪信息（Q4=B），LivaChat 认知声明按 `docs/agent_relay_prompts.md` §8 生成。**
5. **Q5 — Adapter 超时策略**：agent 调用超时后是否通知 app（如 "agent timeout" 系统消息）？还是不通知、仅记 metric？Phase 1 不通知，后续按需加。
6. **Q6 — Agent 回答的 TTS**：如果未来 agent 回答也需要语音播报，需要解决 agent 回答与 LLM TTS 的排队/打断策略。Phase 3 评估。

---

## 10. 分阶段计划

| 阶段 | 内容 | 验收 |
|------|------|------|
| **Phase 1** | relay 骨架 + event router + session manager；HermesAdapter HTTP/SSE 实现；orchestrator utterance 发布；`user.utterance`→adapter→ai_text 全链路；ai-text schema 加 `source`/`reply_to`/`agent_name`/`proactive`；单 adapter、在线投递 | mock agent HTTP server + `asr_only` 联调：app 提一个问题收到两条 ai_text |
| **Phase 2** | `recording.ready` 事件发布；adapter.analyze_recording() 实现；`agent.push` 主动推送；多 adapter 并行；限流完善 | agent 对录制内容生成摘要并主动 push 到在线 app |
| **Phase 3** | agent.reply 流式分帧；富媒体/action 字段；离线历史同步；按需 TTS；Pub/Sub→Stream 可靠性升级 | 视 Phase 1/2 反馈定 |

---

## 11. 对现有代码的改动清单（Phase 1）

| 位置 | 改动 | 性质 |
|------|------|------|
| `packages/api-schema/s2c/ai-text.json` | +`source`, +`reply_to`, +`agent_name`, +`proactive`（optional） | 新增字段，向后兼容 |
| `packages/sdk-*` generated DTO | `make generate-*` 重新生成 | 生成物 |
| `services/agent-relay/` | 新服务（见 §5.1 文件列表） | 新增 |
| `services/session/agent_publisher.py` | `AgentEventPublisher`（封装 Redis publish） | 新增 |
| `services/session/orchestrator.py` | `__init__` 注入 `agent_publisher`；ASR final 分支 +1 个 fire-and-forget 调用 | 最小侵入 |
| `services/gateway/` + `services/app/application.py` | `PushSubscriber` 订阅 task | 新增，不改现有路径 |
| `services/recording/` | 录音段落落盘处 publish `recording.ready` | 新增 fire-and-forget |
| `services/shared/observability/metrics.py` | +agent 指标 | 新增方法 |
| `configs/dev/server.yaml` | +`agent_relay:` 段 | 新增配置 |
