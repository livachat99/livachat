# LivaChat Backend Server 设计文档 (Phase 1)

**版本**: v1.0
**状态**: reviewed
**日期**: 2026-06-13
**适用范围**: LivaChat 后端服务（单体多模块架构）
**关联文档**:
- 产品需求：[`docs/livachat_prd.md`](./livachat_prd.md)
- 交互设计：[`docs/livachat_ux.md`](./livachat_ux.md)
- WS 协议（契约标准）：[`docs/ws-protocol-design.md`](./ws-protocol-design.md)
- Android SDK：[`docs/android_sdk_design_v3.md`](./android_sdk_design_v3.md)
- 协议 SSOT：[`packages/api-schema/`](../packages/api-schema/) → 生成 [`packages/sdk-python`](../packages/sdk-python/)

> **范围声明**：本文档描述 **Phase 1 (MVP)** 的产品级后端服务。Phase 2（记忆系统）/ Phase 3（WebRTC）仅做前瞻性预留标注，不在本期实现范围内。
>
> `tools/local-dev/mock-services/livachat_mock_server` 是用于 SDK 测试的 Mock，**不是**本设计的参考实现，二者仅共享 `packages/sdk-python` 协议 DTO。

---

## 目录

1. [Server 描述](#1-server-描述)
2. [Server 架构](#2-server-架构)
3. [Server Components](#3-server-components)
4. [Server Main Logics](#4-server-main-logics)
5. [Provider 抽象与多供应商支持](#5-provider-抽象与多供应商支持)
6. [配置文件设计](#6-配置文件设计)
7. [PostgreSQL DB Schema](#7-postgresql-db-schema)
8. [Redis Key 设计](#8-redis-key-设计)
9. [鉴权（Phase 1 Dev-Token）](#9-鉴权phase-1-dev-token)
10. [非功能需求与可观测性](#10-非功能需求与可观测性)
11. [目录结构](#11-目录结构)
12. [附录](#12-附录)

---

## 1. Server 描述

### 1.1 定位

LivaChat Backend Server 是一个 **实时多模态对话编排服务**：通过单一 WebSocket 端点接入客户端，将上行的 **Opus 音频流 + JPEG 视频帧 + 控制信令** 编排到 **ASR → LLM(+Vision) → TTS** 流水线，再以流式方式把 **转写文本 / AI 文本 / TTS 音频 / 状态** 下发给客户端，目标端到端延迟 < 1s。

它对外是 [`ws-protocol-design.md`](./ws-protocol-design.md) 定义协议的 **唯一服务端实现**，对内是 ASR / LLM / TTS 三类外部 AI 供应商的 **统一编排与抽象层**。

### 1.2 Phase 1 关键约束（来自需求）

| 约束 | 说明 |
|------|------|
| **单体架构** | 单进程 FastAPI + uvicorn，内部按 module 划分（gateway / session / asr / llm / tts / media / persistence / config）。**不拆微服务**，但模块边界以接口隔离，为后续拆分预留。 |
| **鉴权简化** | JWT 采用 **hardcoded dev-token**，不实现登录、token 签发/刷新。校验逻辑可一键开关（`auth.mode: dev`）。见 §9。 |
| **多 ASR Provider** | `deepgram` / `elevenlabs` / `alibaba_bailian`，可配置切换。 |
| **多 TTS Provider** | `deepgram` / `elevenlabs` / `alibaba_bailian` / `cartesia`，可配置切换。 |
| **多 LLM Provider** | `alibaba_bailian` / `openrouter` / `deepseek`，可配置切换。 |
| **配置驱动** | 所有 provider 的 `provider / base_url / api_key / model` 等参数通过 YAML 配置文件 + 环境变量注入，运行时不改代码即可切换。见 §6。 |

### 1.3 核心职责

- **协议终结**：WebSocket 握手、鉴权、心跳、seq/timestamp 管理、消息编解码（复用 `livachat_protocol` 生成 DTO）。
- **会话管理**：会话状态机（idle/listening/thinking/speaking）、断线重连与 30s 会话恢复、空闲超时。
- **媒体处理**：上行 Opus 解码为 PCM、（必要时）重采样后喂给 ASR；下行把 TTS 音频转码为 Opus 20/40ms 帧。视频帧解码与节流。
- **对话编排（核心）**：话轮检测（turn-taking）、流式 ASR、流式 LLM（含视觉）、流式 TTS、**打断（barge-in）** 的协程级取消。
- **供应商抽象**：ASR/LLM/TTS 三个统一接口，多 provider 实现，故障可降级。
- **持久化**：会话/消息/转写/用量写入 PostgreSQL；工作记忆与会话恢复上下文写入 Redis。

### 1.4 非职责（明确不做）

- 不做客户端侧的录音/播放/相机（由 App + SDK 负责，见 `android_sdk_design_v3.md`）。
- Phase 1 不做长期记忆 / 向量检索 / 用户画像（Phase 2）。
- 不做 WebRTC / SFU（Phase 3）。
- 不做用户注册登录与正式鉴权（dev-token）。

---

## 2. Server 架构

### 2.1 架构风格：模块化单体（Modular Monolith）

单进程内部按 **分层 + 领域模块** 组织。所有跨模块调用通过 **接口（Protocol/ABC）** 与 **依赖注入（DI）** 完成，禁止跨层直接访问实现细节。每个 module 都可以在后续 Phase 抽成独立 service 而不改调用方契约。

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          LivaChat Backend (单进程)                          │
│                                                                            │
│  ┌──────────────────────────── Edge / Transport Layer ─────────────────┐  │
│  │  FastAPI ASGI App  ·  uvicorn[uvloop]  ·  WSS(TLS 由 LB 终结)          │  │
│  │  GET /v1/ws/stream   (WebSocket)                                      │  │
│  │  GET /healthz /healthz_token /readyz  (REST)                          │  │
│  │  POST /v1/auth/token  (dev: 返回 hardcoded dev-token, 见 §9)          │  │
│  └──────────────────────────────┬──────────────────────────────────────┘  │
│                                 │ 每连接一个 ConnectionHandler(asyncio task)│
│  ┌──────────────────────────────┴──────────────────────────────────────┐  │
│  │                        Gateway Module                                 │  │
│  │  Auth · ConnectionManager · MessageRouter · SeqGuard · Heartbeat      │  │
│  │  WireCodec(序列化/反序列化, 复用 livachat_protocol DTO)                  │  │
│  └──────────────────────────────┬──────────────────────────────────────┘  │
│                                 │  ServerMessage 出站队列 / ClientMessage 入站│
│  ┌──────────────────────────────┴──────────────────────────────────────┐  │
│  │            Session Module  +  Dialog Orchestrator (核心)               │  │
│  │  SessionManager · SessionContext · SessionStateMachine                │  │
│  │  TurnDetector · DialogPipeline(asr→llm→tts) · InterruptController      │  │
│  └───────┬──────────────────┬───────────────────┬──────────────────────┘  │
│          │                  │                   │                          │
│  ┌───────┴──────┐   ┌───────┴───────┐   ┌───────┴───────┐  ┌────────────┐  │
│  │  ASR Module  │   │   LLM Module  │   │  TTS Module   │  │Media Module│  │
│  │  Provider IF │   │  Provider IF  │   │  Provider IF  │  │OpusEnc/Dec │  │
│  │  ·Deepgram   │   │  ·Bailian     │   │  ·Deepgram    │  │Resampler   │  │
│  │  ·ElevenLabs │   │  ·OpenRouter  │   │  ·ElevenLabs  │  │JPEG decode │  │
│  │  ·Bailian    │   │  ·DeepSeek    │   │  ·Bailian     │  │AudioBuffer │  │
│  │              │   │  (+Vision)    │   │  ·Cartesia    │  │            │  │
│  └──────────────┘   └───────────────┘   └───────────────┘  └────────────┘  │
│          │                  │                   │                          │
│  ┌───────┴──────────────────┴───────────────────┴──────────────────────┐  │
│  │                    Persistence / Infra Layer                         │  │
│  │  PgRepository(asyncpg/SQLAlchemy)  ·  RedisStore(redis.asyncio)       │  │
│  │  ConfigProvider  ·  Logging  ·  Metrics  ·  Tracing  ·  Lifespan      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                          │                         │                        │
└──────────────────────────┼─────────────────────────┼────────────────────────┘
                           ▼                         ▼
                  ┌─────────────────┐       ┌─────────────────┐
                  │   PostgreSQL    │       │      Redis      │
                  │ (会话/消息/用量) │       │ (会话恢复/工作记忆)│
                  └─────────────────┘       └─────────────────┘
              外部供应商 (HTTPS/WSS): Deepgram · ElevenLabs · DashScope(Bailian)
                                     Cartesia · OpenRouter · DeepSeek
```

### 2.2 分层与依赖规则

| 层 | 模块 | 依赖规则 |
|----|------|---------|
| **Transport** | FastAPI 路由 / WS 端点 | 仅依赖 Gateway |
| **Gateway** | 鉴权、连接管理、编解码、心跳、路由 | 依赖 Session（注入），不直接依赖 ASR/LLM/TTS |
| **Domain** | Session + Dialog Orchestrator | 依赖 ASR/LLM/TTS/Media 的**接口**，不依赖具体 provider |
| **Provider** | ASR/LLM/TTS/Media | 仅依赖 Infra（HTTP 客户端、配置、日志），互不依赖 |
| **Infra** | Persistence/Config/Observability | 最底层，无业务依赖 |

依赖方向单向向下；同层通过接口通信；上层通过 DI 容器拿到下层接口实例。

### 2.3 并发模型

- **全异步**：`asyncio` + `uvloop`，所有 I/O（WS、Provider HTTP/WS、PG、Redis）非阻塞。
- **每连接一个会话协程树**：`ConnectionHandler` 启动后 fork 出
  - `reader_task`：从 WS 读取 ClientMessage → 入站队列
  - `writer_task`：从出站队列取 ServerMessage → WS 发送（统一 seq 分配，保证发送顺序）
  - `dialog_task`：Dialog Orchestrator 主循环
  - `heartbeat_task`：心跳与超时检测
- **CPU 密集（Opus 编解码 / JPEG 解码）**：单帧 < 5ms，直接在事件循环执行；若实测阻塞则迁移到 `ThreadPoolExecutor`（`media.codec.executor` 可配）。
- **打断 = 协程取消**：`thinking/speaking` 阶段的 LLM/TTS 任务持有可取消句柄，`interrupt` 到达时 `task.cancel()` 并切换 epoch。

### 2.4 水平扩展（前瞻）

Phase 1 单实例支撑 1000 并发（PRD 指标）。多实例时：
- WebSocket 为有状态长连接 → LB 用 **IP/connection sticky** 或基于 `device_id` 的一致性哈希；
- 会话恢复上下文存 Redis（非进程内存），断线重连命中同实例最佳，跨实例可从 Redis 恢复；
- 后续可引入 Redis Pub/Sub 做实例间信令（Phase 2+）。

---

## 3. Server Components

### 3.1 组件清单总览

| # | 组件 | 模块 | 职责 | 关键依赖 |
|---|------|------|------|---------|
| C1 | `app` / lifespan | core | ASGI 应用、DI 容器、启动/关闭钩子 | 全部 |
| C2 | `WsEndpoint` | gateway | `/v1/ws/stream` 握手、参数校验、鉴权 | Auth, ConnectionManager |
| C3 | `Authenticator` | gateway | dev-token 校验、JWT 解析 | Config |
| C4 | `ConnectionManager` | gateway | 连接注册表、生命周期、优雅关闭 | Redis |
| C5 | `WireCodec` | gateway | ClientMessage 解析 / ServerMessage 序列化 | livachat_protocol |
| C6 | `SeqGuard` / `OutboundSeq` | gateway | 入站 seq 单调校验、出站 seq 分配 | - |
| C7 | `Heartbeat` | gateway | ping/pong、空闲超时、心跳超时 | - |
| C8 | `SessionManager` | session | 会话创建/恢复/结束、状态机驱动 | Redis, Pg |
| C9 | `SessionContext` | session | 单会话运行时状态（历史、video buffer、epoch） | - |
| C10 | `DialogOrchestrator` | session | 核心编排：asr→turn→llm→tts | ASR/LLM/TTS/Media |
| C11 | `TurnDetector` | session | 话轮结束判定（vad silence + ASR endpoint） | - |
| C12 | `InterruptController` | session | barge-in：取消 LLM/TTS、epoch 切换 | - |
| C13 | `AsrService` + providers | asr | 流式 ASR 抽象与多实现 | Media(decode) |
| C14 | `LlmService` + providers | llm | 流式 LLM(+Vision) 抽象与多实现 | - |
| C15 | `TtsService` + providers | tts | 流式 TTS 抽象与多实现 | Media(encode) |
| C16 | `MediaCodec` | media | Opus 编/解码、重采样、JPEG 解码 | libopus |
| C17 | `PgRepository` | persistence | 会话/消息/转写/用量持久化 | PostgreSQL |
| C18 | `RedisStore` | persistence | 会话恢复、工作记忆、连接注册、限流 | Redis |
| C19 | `ConfigProvider` | config | YAML+env 加载、provider 配置、热校验 | - |
| C20 | `Observability` | infra | 结构化日志、Prometheus 指标、trace | - |

### 3.2 Gateway Module

#### C2 `WsEndpoint`（`/v1/ws/stream`）
- 解析 query：`token`、`device_id`（UUID v4 校验）。
- 调用 `Authenticator.verify(token)`；失败 → 握手阶段返回 HTTP 401 并以 close code `4001` 关闭（协议 §1.1）。
- 成功 → 创建 `ConnectionHandler`，注册到 `ConnectionManager`，启动协程树。

#### C4 `ConnectionManager`
- 内存表 `{device_id -> ConnectionHandler}`；同一 `device_id` 重连时主动踢掉旧连接（`server_shutdown` / replaced）。
- Redis 记录 `livachat:conn:{device_id}`（实例归属，便于多实例诊断）。
- 优雅关闭：`SIGTERM` → 向所有连接发 `session.ended(reason=server_shutdown)` → 等待 flush → 关闭。

#### C5 `WireCodec`
- 入站：`raw str → ClientMessageAdapter.validate_json()`（pydantic discriminated union by `type`）。解析失败 → `error(code=1006, UNSUPPORTED_MESSAGE_TYPE)` 或丢弃（防御）。
- 出站：业务层构造 `ServerXxxPayload` → `WireCodec.build(type, payload)` 自动填 `seq`(由 OutboundSeq)、`timestamp`(server now ms) → `model_dump_json(exclude_none=True)`。
- 单帧 ≤ 2MB；超限入站 → `error(3003 FRAME_TOO_LARGE)`。

#### C6 `SeqGuard` / `OutboundSeq`
- 入站：校验 C2S `seq` 单调递增，`seq <= prev` → 告警并丢弃（协议 §4.1）。TCP 有序，无需 reorder buffer（重排是客户端职责）。
- 出站：`OutboundSeq` 从 1 单调递增，**所有** S2C 消息（含 ping/pong）经唯一 `writer_task` 串行分配 seq，保证顺序。

#### C7 `Heartbeat`
- 每个连接维护 `last_recv_at`（任何入站消息刷新）。
- 服务端可主动每 15s 发 `ping`；收到客户端 `ping` 立即回 `pong(ping_seq)`。
- 35s 未收到任何消息 → close `4002 WS_PING_TIMEOUT`。
- 60s 无 `type` 业务交互（仅心跳）→ `session.ended(idle_timeout)` + close `4003`。

### 3.3 Session Module

#### C8 `SessionManager`
- `start(client_cfg, resume_id)`：
  - `resume_id` 非空且 Redis 命中未过期 → 恢复 `SessionContext`（最近历史、状态），回 `session.ready(session_restored=true)`。
  - 否则新建 `session_id = "sess_" + 18位[a-z0-9]`，落 PG `sessions`，回 `session.ready(session_restored=false)`。
- `end(reason)`：落 PG（`ended_at`、`end_reason`），删除 Redis 恢复键，回 `session.ended`。
- 断开（非主动 end）：将 `SessionContext` 快照写 Redis，TTL=30s（协议 §1.3 会话保留）。

#### C9 `SessionContext`（运行时，每会话一份）
```
session_id, user_id, device_id
state: SessionStatus                      # idle/listening/thinking/speaking
config: {sample_rate, enable_video, vad_sensitivity, client_version}
history: deque[Turn]                      # 工作记忆，最近 N=10 轮
current_message_id / current_transcription_id
latest_video_frames: ring buffer[(frame_id, jpeg_bytes, ts)]  # 仅保留最近 K=2 帧
epoch: int                                # 打断纪元，每次话轮+1，用于丢弃陈旧产物
clock_offset_ms                           # 与客户端时钟偏差
asr_session: 当前 ASR 流句柄(可空)
```

#### C11 `TurnDetector`
- 主依据：连续 `vad_state == silence` 帧 ≥ 600ms（20ms×30 帧）→ 话轮结束。
- 辅依据：ASR provider 的 endpoint/`speech_final` 事件（若支持）取二者更早者。
- 输出 `on_turn_end()` 给 Orchestrator 触发 `thinking`。

#### C12 `InterruptController`
- 触发源：客户端 `interrupt` 消息，或服务端基于上行 `vad_state=speech` 在 `speaking` 态的兜底判定。
- 动作（协议 §3.2、目标 < 50ms 内部取消）：
  1. `epoch += 1`（旧 epoch 的 LLM/TTS 输出一律丢弃）；
  2. `llm_task.cancel()` / `tts_task.cancel()`；
  3. 清空出站音频队列中本轮未发送的 `ai_audio`；
  4. 状态 → `listening`，下发 `status(listening, reason=interrupted)`；
  5. 重置 ASR 流为新话轮。

### 3.4 Provider Modules（详见 §5）

`ASR / LLM / TTS` 各自一个 `Service` 门面 + `Provider` 接口 + N 个实现 + `ProviderFactory`（按配置实例化）。

### 3.5 Media Module（C16 `MediaCodec`）

| 能力 | 实现 | 备注 |
|------|------|------|
| Opus 解码 | libopus（`opuslib`/`pyogg`/`av`） | 上行 base64 Opus → PCM16 mono |
| Opus 编码 | libopus | 下行 PCM → Opus 20/40ms 帧，CBR 32kbps，与 SDK 编码参数对齐 |
| 重采样 | `soxr`/`av` | 客户端 16k/24k ↔ provider 要求采样率（如 Deepgram 16k、Cartesia 输出 PCM） |
| JPEG 解码 | `Pillow`/`turbojpeg` | 视频帧解码为图像（供视觉 LLM 或转 base64 直传多模态 API） |
| AudioFrameBuffer | 内部 | 拼帧/切帧，保证下行严格 20/40ms 边界 |

> **关键设计**：客户端与服务端的 Opus 参数（采样率、帧长、声道、码率）必须与 `docs/android_sdk_design_v3.md` 的编码参数一致（16k/24k、20/40ms、CBR 32kbps、mono），否则解码错位。

---

## 4. Server Main Logics

本节是服务核心。所有时序与协议消息严格遵循 [`ws-protocol-design.md`](./ws-protocol-design.md)。

### 4.1 连接生命周期

```
WS 握手(?token&device_id)
   │ Authenticator.verify → 失败:HTTP401 + close 4001
   ▼
注册连接 → 启动 reader/writer/heartbeat 协程
   │
   ▼
收到 session.start
   │  ├─ resume(session_id) 命中 → session.ready(restored=true) + status(listening)
   │  └─ 新建 → session.ready(restored=false) + status(idle) → status(listening)
   ▼
进入对话编排循环 (DialogOrchestrator)
   │
   ▼
session.end / idle_timeout / ping_timeout / fatal error
   │  └─ session.ended(...) → 优雅 close(1000)
   ▼
断开：若非主动 end → 快照 SessionContext 到 Redis(TTL 30s)
```

### 4.2 会话状态机（服务端主导）

状态枚举与协议 `SessionStatusCode` 一致：`idle / listening / thinking / speaking`。**状态变更由服务端主导**，每次变更下发 `status` 消息（带 `reason`）。

```
 idle ──session.ready──▶ listening
                           │ TurnDetector.on_turn_end (asr_final)
                           ▼
                        thinking ──LLM首token+TTS首块──▶ speaking
                           ▲                               │
                           │ interrupt                     │ TTS 全部下发完 (tts_finish)
                           └──────────── listening ◀───────┘
 任意态 ──interrupt──▶ listening(reason=interrupted)
 任意态 ──session.end/fatal──▶ (ended)
```

| 迁移 | 触发 | 下发 |
|------|------|------|
| idle→listening | session.ready | `status(listening, connected)` |
| listening→thinking | 话轮结束 | `transcription(is_final=true)` + `status(thinking, asr_final)` |
| thinking→speaking | TTS 首块就绪 | `status(speaking, tts_start)` |
| speaking→listening | TTS 全部下发 | `status(listening, tts_finish)` |
| *→listening | interrupt | `status(listening, interrupted)` |

### 4.3 完整一轮对话编排（核心流程）

对应协议 §3.1。`DialogOrchestrator` 在 `dialog_task` 中运行：

```
LISTENING:
  for each 入站 audio 帧:
     pcm = MediaCodec.opus_decode(payload.data)              # base64 opus → PCM16
     asr_session.feed(pcm)                                   # 流式喂入 ASR provider
     TurnDetector.observe(vad_state)
     async for interim in asr_session.results():
         send transcription(text, is_final=false)
  for each 入站 video 帧:
     if state==speaking: send ai_video_ack(processed=false) # 说话态不处理视觉
     else:
         ctx.latest_video_frames.push(frame)                 # 仅留最近 K 帧
         send ai_video_ack(ack_frame_id, processed=true)

TurnDetector.on_turn_end()  ── 连续 silence≥600ms 或 ASR endpoint:
  final_text = await asr_session.final()
  send transcription(final_text, is_final=true)
  state → thinking; send status(thinking, asr_final)
  ctx.history.append(Turn(role=user, text=final_text))
  epoch_snapshot = ctx.epoch

THINKING → SPEAKING (LLM→TTS 流式管道, 可被打断):
  message_id = "msg_"+12位
  llm_stream = LlmService.stream(
       history=ctx.history,
       images=ctx.latest_video_frames if enable_video else None)   # 多模态
  tts_stream = TtsService.open(message_id)
  text_index = 0; audio_index = 0
  async for text_delta in llm_stream:
       if ctx.epoch != epoch_snapshot: break                # 被打断, 丢弃
       send ai_text(text_delta, message_id, index=text_index, is_final=...)
       text_index += 1
       tts_stream.feed(text_delta)                          # 句子/标点边界触发合成
       async for audio_chunk in tts_stream.poll():
            if ctx.epoch != epoch_snapshot: break
            if audio_index==0: state→speaking; send status(speaking, tts_start)
            opus = MediaCodec.opus_encode(audio_chunk)       # PCM/transcode → Opus帧
            send ai_audio(opus, message_id, index=audio_index, is_final=false)
            audio_index += 1
  # flush 收尾
  async for audio_chunk in tts_stream.drain():
       send ai_audio(..., is_final=(last))
  ctx.history.append(Turn(role=ai, text=full_text))
  persist Turn(s) to PG (async, 不阻塞下发)
  state → listening; send status(listening, tts_finish)
```

**延迟优化要点（满足 PRD < 1s / 首字 < 500ms）**：
- ASR、LLM、TTS 三段 **流式串联**，不等整段；TTS 以**句子/标点边界**增量合成（首句尽快出声）。
- LLM 首 token 即下发 `ai_text` 并把首句送 TTS；不等全文。
- `ai_audio` 一边生成一边发，客户端边收边播（SDK `AudioRecvPipeline`）。

### 4.4 打断（Barge-in）

对应协议 §3.2、UX §6.4、SDK §6.2。

```
触发: 收到 client interrupt(reason)  OR  speaking态下连续 audio(vad_state=speech)
  │
  ▼ InterruptController.interrupt()
  1. ctx.epoch += 1
  2. cancel(llm_task); cancel(tts_task)  (协程级 CancelledError, 内部清理 provider 流)
  3. (可选) 调用 provider 的 cancel/close 主动断开上游生成
  4. 丢弃出站队列中本轮未发送的 ai_audio
  5. state → listening; send status(listening, interrupted)
  6. asr_session.reset() 开新话轮
```
内部取消目标 < 50ms；端到端（客户端 flush + interrupt 到达 + status 回传）受 RTT 约束，目标 < 200ms（PRD 打断成功率 > 95%）。

### 4.5 断线重连与会话恢复

对应协议 §3.3、SDK §6.3。

```
WS 断开(非主动 end):
  SessionManager.snapshot → Redis SET livachat:session:{sid} {state, history(最近3条), config} EX 30
重连(新 WS) → session.start(session_id=旧ID):
  Redis GET 命中 & TTL>0 → 恢复 ctx → session.ready(restored=true) + status(listening)
  未命中/过期 → 新建 → session.ready(restored=false) + status(idle)
```
- 恢复仅含最近 3 条消息与状态机位置（协议 §3.3），不重放历史媒体。
- 同一 `device_id` 旧连接若仍在 → 踢旧迎新。

### 4.6 视频帧处理

- 频率约 1fps；服务端节流：排队 > 3 帧未处理则仅保留最新（协议 §4.2.1）。
- 仅在 **非 speaking** 态送入视觉上下文；speaking 态回 `ai_video_ack(processed=false)`。
- 视频帧通过 `timestamp` 软关联到相近音频上下文（±50ms 窗口），在 LLM 推理时作为多模态输入（base64 data URL 或解码后图像）。
- `ai_video_ack` 用滑动窗口 `ack_frame_id` 通知客户端最新已处理帧。

### 4.7 心跳、seq、超时

| 项 | 规则 |
|----|------|
| 入站 seq | 单调校验，回退则告警丢弃 |
| 出站 seq | writer_task 串行从 1 递增 |
| timestamp | 出站填服务端 UTC ms；入站记录用于单向延迟统计 |
| 心跳 | 收 client ping → 回 pong；服务端 15s 主动 ping |
| 心跳超时 | 35s 无任何入站 → close 4002 |
| 空闲超时 | 60s 无业务消息 → session.ended(idle_timeout) + close 4003 |

### 4.8 错误处理与降级

错误码完全遵循协议 §4.3。

| 场景 | 处理 | 错误码 |
|------|------|--------|
| ASR provider 不可用 | 尝试 failover 到备用 provider；仍失败发 error | 1001（fatal=false） |
| TTS provider 不可用 | 降级为仅文本（继续发 ai_text，不发 ai_audio） | 1002（fatal=false） |
| LLM provider 不可用 | failover；失败提示稍后再试 | 1003（fatal=false） |
| 内部异常 | 记录 trace，结束会话 | 1004（fatal=true） |
| 过载 | 拒绝新会话 / 退避 | 1005 |
| token 无效/过期 | dev 模式固定 token；非法直接 4001 | 2001/2002 |
| 单帧超 2MB | 丢弃该帧 | 3003 |

降级与 failover 策略由 `providers.*.fallback` 配置驱动（见 §6）。

---

## 5. Provider 抽象与多供应商支持

### 5.1 设计目标

- **统一接口**：业务层只依赖 `AsrProvider` / `LlmProvider` / `TtsProvider` 三个接口，不感知具体厂商。
- **配置切换**：通过 YAML 选择 `active` provider 及其 `base_url / api_key / model` 等参数；支持 `fallback` 列表。
- **流式优先**：三者接口均以异步迭代器（`AsyncIterator`）暴露流式结果。

### 5.2 ASR Provider 接口

```python
class AsrProvider(Protocol):
    name: str
    async def open(self, *, sample_rate: int, language: str | None,
                   interim_results: bool = True) -> "AsrStream": ...

class AsrStream(Protocol):
    async def feed(self, pcm: bytes) -> None: ...          # PCM16 mono
    def results(self) -> AsyncIterator[AsrResult]: ...      # interim + final
    async def final(self) -> str: ...                       # 当前话轮最终文本
    async def reset(self) -> None: ...                      # 新话轮
    async def close(self) -> None: ...

@dataclass
class AsrResult:
    text: str
    is_final: bool
    confidence: float | None = None
```

| Provider | 接入方式 | 输入音频 | 关键参数 |
|----------|---------|---------|---------|
| `deepgram` | WSS `wss://api.deepgram.com/v1/listen` | linear16（Media 先 Opus→PCM16） | `model`(nova-2/3), `language`, `interim_results`, `endpointing`, `vad_events`，header `Authorization: Token <key>` |
| `elevenlabs` | WSS 流式 STT (Scribe) | PCM16 | `model_id`(scribe_v1), `language`，header `xi-api-key` |
| `alibaba_bailian` | DashScope WSS 实时识别 | PCM16/Opus | `model`(paraformer-realtime-v2 / gummy), `sample_rate`，`Authorization: Bearer <key>`，`base_url` 可配 |

> 多数 provider 接受 linear16/PCM16；Media 模块统一把上行 Opus 解码为 PCM16 并按 provider 要求重采样（如 Deepgram 16k）。

### 5.3 LLM Provider 接口

```python
class LlmProvider(Protocol):
    name: str
    supports_vision: bool
    async def stream(self, *, messages: list[ChatMessage],
                     images: list[ImageRef] | None = None,
                     **gen_opts) -> AsyncIterator[str]: ...   # 文本增量

@dataclass
class ChatMessage: role: str; content: str        # system/user/assistant
@dataclass
class ImageRef: data: bytes | None; data_url: str | None; mime: str = "image/jpeg"
```

| Provider | Endpoint（OpenAI 兼容） | Vision | 模型示例 |
|----------|------------------------|--------|---------|
| `alibaba_bailian` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | ✅(qwen-vl) | `qwen-plus` / `qwen-vl-max` |
| `openrouter` | `https://openrouter.ai/api/v1` | ✅(取决于模型) | `openai/gpt-4o` / `anthropic/claude-3.5-sonnet` 等 |
| `deepseek` | `https://api.deepseek.com` | ❌(纯文本) | `deepseek-chat` |

> **统一实现**：三者均 OpenAI Chat Completions 兼容，可用同一 `AsyncOpenAI(base_url=..., api_key=...)` 客户端，仅 `base_url/api_key/model` 不同。多模态：把视频帧编码为 `image_url` data URL 注入 user message（仅 `supports_vision=true` 的 provider）。`deepseek` 无视觉时自动忽略 images 并记日志。

### 5.4 TTS Provider 接口

```python
class TtsProvider(Protocol):
    name: str
    async def open(self, *, voice: str, sample_rate: int,
                   audio_format: str) -> "TtsStream": ...

class TtsStream(Protocol):
    async def feed_text(self, text: str) -> None: ...        # 增量文本(句子/标点边界)
    def audio(self) -> AsyncIterator[bytes]: ...             # PCM/编码音频块
    async def flush(self) -> None: ...
    async def close(self) -> None: ...
```

| Provider | 接入方式 | 输出格式 | 关键参数 |
|----------|---------|---------|---------|
| `deepgram` | Aura WSS/REST 流式 | linear16/opus | `model`(aura-*), `voice` |
| `elevenlabs` | WSS `/v1/text-to-speech/{voice}/stream-input` | mp3/pcm | `voice_id`, `model_id`(eleven_turbo_v2_5), `xi-api-key` |
| `alibaba_bailian` | DashScope TTS (CosyVoice/Sambert) WSS | PCM/mp3 | `model`, `voice`, `sample_rate` |
| `cartesia` | WSS `wss://api.cartesia.ai/tts/websocket` | `pcm_s16le`/opus | `model`(sonic), `voice.id`, `X-API-Key`, `Cartesia-Version` |

> Media 模块把 provider 输出（PCM 或非 Opus 编码）统一 **转码为 Opus 20/40ms 帧** 后封 `ai_audio`。优先让 provider 直接输出 PCM16，server 端 Opus 编码，避免双重有损转码。

### 5.5 ProviderFactory 与 failover

```
ProviderFactory.create_asr(config.asr) -> AsrProvider
  active = config.asr.active                # e.g. "deepgram"
  fallbacks = config.asr.fallback           # e.g. ["alibaba_bailian"]
  返回带 failover 包装的 Service:
    open() 时按 [active, *fallbacks] 顺序尝试, 首个成功者胜出
    运行中失败 → 抛错由 Orchestrator 转 error(100x)
```

LLM/TTS 同理。`active`/`fallback`/`enabled` 均配置驱动。

---

## 6. 配置文件设计

### 6.1 加载优先级

`默认值 < configs/{env}/server.yaml < 环境变量(LIVACHAT__ 前缀) < 启动参数`

- 使用 `pydantic-settings`，嵌套用 `__` 分隔（如 `LIVACHAT__ASR__ACTIVE=deepgram`）。
- **密钥不入库 YAML**：`api_key` 字段在 YAML 中写 `${ENV_VAR}` 占位，运行时从环境变量解析；生产用 K8s Secret / Vault。

### 6.2 配置文件示例（`configs/dev/server.yaml`）

```yaml
server:
  host: 0.0.0.0
  port: 8080
  workers: 1
  max_frame_bytes: 2097152          # 2MB
  log_level: INFO

auth:
  mode: dev                         # dev | jwt
  dev_token: "dev-livachat-token"   # 见 §9，客户端 query token 必须等于此值
  jwt_algorithm: HS256
  jwt_secret: "${LIVACHAT_JWT_SECRET}"   # mode=jwt 时使用(本期不启用)

session:
  resume_ttl_s: 30
  idle_timeout_s: 60
  heartbeat_interval_s: 15
  heartbeat_timeout_s: 35
  working_memory_turns: 10
  max_video_frames: 2

media:
  audio:
    default_sample_rate: 24000      # 与客户端协商, 支持 16000/24000
    opus_frame_ms: 20
    opus_bitrate: 32000
    channels: 1
  video:
    max_queue_frames: 3             # 超过仅保留最新
    decode: pillow                  # pillow | turbojpeg

# ---------------- ASR ----------------
asr:
  active: deepgram
  fallback: [alibaba_bailian]
  language: zh-CN
  providers:
    deepgram:
      enabled: true
      base_url: wss://api.deepgram.com/v1/listen
      api_key: "${DEEPGRAM_API_KEY}"
      model: nova-2
      params: { interim_results: true, endpointing: 300, vad_events: true }
    elevenlabs:
      enabled: false
      base_url: wss://api.elevenlabs.io
      api_key: "${ELEVENLABS_API_KEY}"
      model: scribe_v1
    alibaba_bailian:
      enabled: true
      base_url: wss://dashscope.aliyuncs.com/api-ws/v1/inference
      api_key: "${DASHSCOPE_API_KEY}"
      model: paraformer-realtime-v2
      params: { sample_rate: 16000 }

# ---------------- LLM ----------------
llm:
  active: alibaba_bailian
  fallback: [openrouter, deepseek]
  system_prompt_path: configs/dev/prompts/companion.txt
  generation: { temperature: 0.8, max_tokens: 1024, top_p: 0.9 }
  providers:
    alibaba_bailian:
      enabled: true
      base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
      api_key: "${DASHSCOPE_API_KEY}"
      model: qwen-plus
      vision_model: qwen-vl-max     # 启用视频帧时使用
      supports_vision: true
    openrouter:
      enabled: true
      base_url: https://openrouter.ai/api/v1
      api_key: "${OPENROUTER_API_KEY}"
      model: openai/gpt-4o
      supports_vision: true
    deepseek:
      enabled: true
      base_url: https://api.deepseek.com
      api_key: "${DEEPSEEK_API_KEY}"
      model: deepseek-chat
      supports_vision: false

# ---------------- TTS ----------------
tts:
  active: cartesia
  fallback: [elevenlabs, alibaba_bailian]
  providers:
    cartesia:
      enabled: true
      base_url: wss://api.cartesia.ai/tts/websocket
      api_key: "${CARTESIA_API_KEY}"
      model: sonic-2
      voice: "<voice_id>"
      output_format: { container: raw, encoding: pcm_s16le, sample_rate: 24000 }
      version: "2024-06-10"
    elevenlabs:
      enabled: true
      base_url: wss://api.elevenlabs.io
      api_key: "${ELEVENLABS_API_KEY}"
      model: eleven_turbo_v2_5
      voice: "<voice_id>"
      output_format: pcm_24000
    deepgram:
      enabled: false
      base_url: wss://api.deepgram.com/v1/speak
      api_key: "${DEEPGRAM_API_KEY}"
      model: aura-asteria-en
    alibaba_bailian:
      enabled: true
      base_url: wss://dashscope.aliyuncs.com/api-ws/v1/inference
      api_key: "${DASHSCOPE_API_KEY}"
      model: cosyvoice-v1
      voice: longxiaochun

postgres:
  dsn: "${LIVACHAT_PG_DSN}"         # postgresql+asyncpg://user:pass@host:5432/livachat
  pool_min: 2
  pool_max: 20

redis:
  url: "${LIVACHAT_REDIS_URL}"      # redis://host:6379/0
  key_prefix: "livachat"

observability:
  metrics_enabled: true
  tracing_enabled: false
  json_logs: true
  log_file:                         # 轮转日志（docs/server_logging.md）
    enabled: true
    dir: ""                         # 空 = <project_root>/logs
    max_bytes: 52428800             # 50MB
    backup_count: 3
```

### 6.3 配置校验
- 启动时 `ConfigProvider` 校验：`active` provider 必须 `enabled=true` 且关键字段非空；`fallback` 项必须存在；采样率 ∈ {16000,24000}；否则 **启动失败并打印明确错误**（fail-fast）。

---

## 7. PostgreSQL DB Schema

### 7.1 设计原则
- 所有表带 `created_at/updated_at`（`timestamptz`）。
- ID：业务对外 ID 用协议格式字符串（`sess_*`/`msg_*`），主键用 `bigserial` 或 `uuid`，便于索引。
- Phase 1 即落库会话/消息/转写/用量，支撑 UX 历史记录（US-07）与运营分析。
- Phase 2 表（`memories`/`user_profiles`/embeddings + `pgvector`）已建模但标注，本期可空跑。
- 写入采用**异步、非阻塞**（不阻塞对话下发）；失败仅告警不影响实时链路。

### 7.2 DDL

```sql
-- ========== 扩展 ==========
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- Phase 2: CREATE EXTENSION IF NOT EXISTS vector;

-- ========== 用户（Phase1 为 dev 占位; Phase2 完善） ==========
CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id   text UNIQUE,                       -- JWT sub; dev 模式固定 dev-user
    display_name  text,
    locale        text DEFAULT 'zh-CN',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ========== 设备 ==========
CREATE TABLE devices (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_uuid   text UNIQUE NOT NULL,              -- 客户端 device_id (uuid v4)
    user_id       uuid REFERENCES users(id) ON DELETE SET NULL,
    platform      text,                              -- ios | android
    client_version text,
    last_seen_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_devices_user ON devices(user_id);

-- ========== 会话 ==========
CREATE TABLE sessions (
    id              bigserial PRIMARY KEY,
    session_id      text UNIQUE NOT NULL,            -- 'sess_' + 18
    user_id         uuid REFERENCES users(id) ON DELETE SET NULL,
    device_id       uuid REFERENCES devices(id) ON DELETE SET NULL,
    status          text NOT NULL DEFAULT 'active',  -- active | ended
    enable_video    boolean NOT NULL DEFAULT false,
    audio_sample_rate int NOT NULL,
    vad_sensitivity text,
    client_version  text,
    asr_provider    text,
    llm_provider    text,
    tts_provider    text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    end_reason      text,                            -- user_quit/idle_timeout/error/...
    resumed_count   int NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_sessions_user_time ON sessions(user_id, started_at DESC);
CREATE INDEX idx_sessions_device ON sessions(device_id);

-- ========== 消息（对话轮次：user / assistant） ==========
CREATE TABLE messages (
    id              bigserial PRIMARY KEY,
    session_id      text NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    message_id      text,                            -- ai: 'msg_'+12; user 可空
    role            text NOT NULL,                   -- user | assistant | system
    content         text NOT NULL DEFAULT '',
    turn_index      int NOT NULL,                    -- 会话内话轮序号
    has_vision      boolean NOT NULL DEFAULT false,  -- 该轮是否含视频帧上下文
    asr_confidence  real,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_session_turn ON messages(session_id, turn_index);

-- ========== 转写记录（ASR final, 可选细粒度） ==========
CREATE TABLE transcriptions (
    id              bigserial PRIMARY KEY,
    session_id      text NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    transcription_id text NOT NULL,                  -- 'trans_'+12
    text            text NOT NULL,
    is_final        boolean NOT NULL,
    provider        text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_trans_session ON transcriptions(session_id);

-- ========== 会话事件（状态迁移/打断/错误, 用于分析与回放） ==========
CREATE TABLE session_events (
    id            bigserial PRIMARY KEY,
    session_id    text NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    event_type    text NOT NULL,                     -- status_change|interrupt|error|reconnect
    from_status   text,
    to_status     text,
    reason        text,
    error_code    int,
    metadata      jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_session_time ON session_events(session_id, created_at);

-- ========== 供应商用量/计费 ==========
CREATE TABLE provider_usage (
    id            bigserial PRIMARY KEY,
    session_id    text REFERENCES sessions(session_id) ON DELETE SET NULL,
    kind          text NOT NULL,                     -- asr | llm | tts
    provider      text NOT NULL,
    model         text,
    request_count int  NOT NULL DEFAULT 1,
    input_units   bigint,                            -- llm: prompt tokens; asr: audio ms
    output_units  bigint,                            -- llm: completion tokens; tts: chars/audio ms
    latency_ms    int,
    success       boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_usage_session ON provider_usage(session_id);
CREATE INDEX idx_usage_kind_time ON provider_usage(kind, created_at);

-- ========== Phase 2 预留（本期不写入） ==========
CREATE TABLE user_profiles (              -- P2: 用户画像
    user_id     uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    attributes  jsonb NOT NULL DEFAULT '{}',         -- 姓名/年龄/偏好...
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE memories (                   -- P2: 短期/长期记忆 + 向量检索
    id          bigserial PRIMARY KEY,
    user_id     uuid REFERENCES users(id) ON DELETE CASCADE,
    session_id  text,
    kind        text,                                -- summary | event | preference
    content     text NOT NULL,
    -- embedding vector(1536),                        -- P2 启用 pgvector
    importance  real DEFAULT 0,
    expires_at  timestamptz,                          -- 短期记忆 30 天; 长期 null
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- CREATE INDEX idx_memories_embedding ON memories USING ivfflat (embedding vector_cosine_ops);
```

### 7.3 数据保留与隐私
- 会话/消息默认保留；提供按 `user_id` / `session_id` 的级联删除（PRD §3.2 用户可删除历史）。
- 媒体原始数据（Opus/JPEG）**不落库**，仅在内存/上游处理，符合最小化原则。

---

## 8. Redis Key 设计

统一前缀 `livachat`（可配 `redis.key_prefix`）。冒号分层命名。

| Key | 类型 | TTL | 用途 |
|-----|------|-----|------|
| `livachat:session:{session_id}` | Hash | 30s（断线后） / 否则会话期 | **会话恢复快照**：`state`,`config`,`user_id`,`device_id`,`epoch` |
| `livachat:session:{session_id}:history` | List | 同上 | **工作记忆**：最近 N=10 轮（恢复时取最近 3 条），JSON 元素 |
| `livachat:session:{session_id}:video` | List | 60s | 最近 K 个视频帧元数据（frame_id, ts），actual bytes 仅内存 |
| `livachat:conn:{device_id}` | String | 连接期 | 连接归属实例 ID（多实例诊断/踢旧连接） |
| `livachat:device:{device_id}:session` | String | 30s | device→当前 session_id 映射（重连快速定位） |
| `livachat:ratelimit:conn:{device_id}` | String(计数) | 300s | 全局重连上限（≤3 次/5 分钟，协议 §3.3） |
| `livachat:ratelimit:msg:{session_id}` | String(计数) | 1s | 入站消息频率保护（防异常突增） |
| `livachat:lock:session:{session_id}` | String(SET NX) | 10s | 同会话单实例处理互斥（防并发重连竞争） |
| `livachat:usage:{date}:{provider}` | Hash | 实时累加 | 当日各 provider 调用计数（运营快照，异步落 PG） |
| `livachat:health:providers` | Hash | 30s | 各 provider 健康状态缓存（failover 决策） |

**会话恢复快照示例**：
```
HSET livachat:session:sess_abc123def456ghijk
     state        listening
     config       {"enable_video":true,"audio_sample_rate":24000,"vad_sensitivity":"medium"}
     user_id      dev-user
     device_id    7c9e6679-...-uuid
     epoch        4
     updated_at   1712345778000
EXPIRE livachat:session:sess_abc123def456ghijk 30   # 仅断线后设置
```

**说明**：
- 活跃会话上下文主要驻留进程内存（`SessionContext`）；Redis 仅承载 **恢复所需子集** 与 **跨实例/限流** 状态。
- 断线时写快照并设 30s TTL；重连命中即恢复，超时即新建（协议 §3.3 逻辑）。

---

## 9. 鉴权（Phase 1 Dev-Token）

### 9.1 策略
- `auth.mode: dev`（默认）：WebSocket query 中的 `token` 必须等于 `sha1_hex(auth.dev_token + "LivaChat")`（固定盐摘要，明文 secret 不上网——2026-10-14 起，见 `docs/server_auth.md`），否则握手返回 HTTP 401 + close `4001`。`user_id` 固定为 `dev-user`（或从 `device_id` 派生），`device_id` 仍按 UUID v4 校验。
- REST 预检端点 `GET /healthz_token?token=…&device_id=…`：校验语义与握手逐条一致（wire token 摘要比对；device_id 同款 UUID 解析校验；失败 401 的 `detail` 与握手字符串相同），成功返回 `{"ok": true}`。
- **不实现** `/v1/auth/token` 真实签发与 `/v1/auth/refresh` 刷新；为兼容客户端流程，`POST /v1/auth/token` 在 dev 模式下直接返回固定 wire token（sha1 摘要；可选 stub，本期未实现）。
- `auth.mode: jwt`（预留，本期关闭）：校验 HS256/RS256 签名、`exp`、`sub`、`device_id`，对应协议 §1.1 JWT Payload；切换仅改配置，不改业务代码。

### 9.2 实现要点
```python
class Authenticator:
    def verify(self, token: str, device_id: str) -> Principal:
        if self.cfg.mode == "dev":
            # wire token = sha1_hex(dev_token + "LivaChat")（docs/server_auth.md §3.2）
            if not hmac.compare_digest(token.encode(), self._expected.encode()):
                raise AuthError(close_code=4001)        # WS_AUTH_FAILED
            return Principal(user_id="dev-user", device_id=device_id)
        # jwt 模式 (预留)
        claims = jwt.decode(token, self.cfg.jwt_secret, algorithms=[self.cfg.jwt_algorithm])
        return Principal(user_id=claims["sub"], device_id=claims["device_id"])
```
- `device_id` 必须匹配 `common.json` 的 UUID v4 pattern，否则 4001。
- dev 模式下也记录 `device` 行（落 PG），保证后续数据链路一致。

---

## 10. 非功能需求与可观测性

### 10.1 性能目标（对齐 PRD §3.1）

| 指标 | 目标 | 服务端保障手段 |
|------|------|---------------|
| 端到端延迟 | < 1000ms | ASR/LLM/TTS 全流式串联，句子级 TTS 触发 |
| 首字延迟 | < 500ms | LLM 首 token 立即下发 ai_text + 首句送 TTS |
| 打断响应 | < 200ms | 协程取消 + epoch 隔离 + 出站队列清空 |
| 视频帧处理 | < 500ms | 1fps 节流，仅保留最新帧 |
| 单实例并发 | 1000 | 全异步 + uvloop；连接级协程，无线程膨胀 |
| 可用性 | 99.9% | provider failover + 优雅重启 + 健康探针 |

### 10.2 可观测性
- **日志**：结构化 JSON，每条带 `session_id` / `device_id` / `seq` / `epoch`；分级（INFO 业务里程碑、WARN 降级、ERROR 异常）。落盘：进程内 `RotatingFileHandler`（默认 `<project_root>/logs/livachat.log`，50MB×3 份；`observability.log_file` 可配，docs/server_logging.md）。**容器/12-factor 依赖 stdout 采集的环境须显式 `log_file.enabled=false` 回退 stdout（`log_file.enabled` 默认 true 且运行态只写文件——否则日志只落容器内文件，见 docs/server_logging.md §8.6）。**
- **指标（Prometheus `/metrics`）**：活跃连接数、活跃会话数、各阶段延迟直方图（asr_first_partial、llm_first_token、tts_first_chunk、e2e_turn）、打断次数与耗时、provider 成功率/延迟、错误码计数。导出路由已摘除（2026-10-13，不对公网暴露）；实现保留于 `app/routes.py`，registry 照常收集。
- **Trace**（可配，OpenTelemetry）：一次话轮串联 asr→llm→tts span。
- **健康检查**：`/healthz`（存活）、`/readyz`（chat_store 可达；去 Redis 后仅此一项）、`/healthz_token`（握手预检：token+device_id 校验，200 `{"ok": true}` / 401）。

### 10.3 安全
- 传输：WSS（TLS 1.3 在 LB/Ingress 终结）。
- 密钥：provider api_key 仅来自环境变量/Secret，不入代码与日志。
- 数据：媒体不落库；PG 静态加密（PRD §3.2，由基础设施层提供）。

### 10.4 部署
- 容器化（`infra/docker`），K8s 部署（`infra/kubernetes`）。
- WS 长连接：Ingress 配置 `proxy-read-timeout` 足够大、启用 sticky session。
- 优雅停机：`SIGTERM` → 停止接受新连接 → 通知在连会话 `server_shutdown` → 等待 flush → 退出。

---

## 11. 目录结构

落位于 `services/`（mono-repo 已有占位）。Phase 1 单体，但按模块组织，与 `services/{gateway,asr,llm,tts,session,...}` 占位目录一致：单进程组合这些 package。

```
services/
├── app/                              # 单体入口 (组合各 module)
│   ├── main.py                       # uvicorn 入口
│   ├── application.py                # FastAPI app + lifespan + DI 容器装配
│   └── routes.py                     # /v1/ws/stream, /healthz, /healthz_token, /readyz;metrics 实现保留（route 已摘除）
│
├── gateway/                          # 传输/协议网关 module
│   ├── auth.py                       # Authenticator (dev-token / jwt 预留)
│   ├── connection.py                 # ConnectionHandler, ConnectionManager
│   ├── codec.py                      # WireCodec (复用 livachat_protocol DTO)
│   ├── seq.py                        # SeqGuard / OutboundSeq
│   └── heartbeat.py
│
├── session/                          # 会话 + 对话编排 (核心 domain)
│   ├── manager.py                    # SessionManager
│   ├── context.py                    # SessionContext
│   ├── state_machine.py              # SessionStateMachine
│   ├── orchestrator.py               # DialogOrchestrator (主循环)
│   ├── turn_detector.py
│   └── interrupt.py                  # InterruptController
│
├── asr/                              # ASR module
│   ├── base.py                       # AsrProvider / AsrStream 接口
│   ├── service.py                    # AsrService + failover 包装
│   ├── factory.py
│   └── providers/{deepgram.py, elevenlabs.py, alibaba_bailian.py}
│
├── llm/                              # LLM module
│   ├── base.py                       # LlmProvider 接口
│   ├── service.py
│   ├── factory.py
│   ├── prompt.py                     # system prompt 装配
│   └── providers/{alibaba_bailian.py, openrouter.py, deepseek.py}
│
├── tts/                              # TTS module
│   ├── base.py
│   ├── service.py
│   ├── factory.py
│   └── providers/{deepgram.py, elevenlabs.py, alibaba_bailian.py, cartesia.py}
│
├── media/                            # 媒体处理 module
│   ├── opus.py                       # OpusEncoder / OpusDecoder (libopus)
│   ├── resample.py
│   ├── jpeg.py
│   └── buffer.py                     # AudioFrameBuffer
│
└── shared/                           # 已有占位: 跨模块共享
    ├── config/                       # ConfigProvider (pydantic-settings + yaml)
    ├── persistence/                  # PgRepository, RedisStore
    ├── models/                       # ORM 模型 / DAO
    ├── observability/                # logging, metrics, tracing
    └── utils/                        # id 生成(sess_/msg_/trans_), 时间, 错误
```

> 协议 DTO 不在 server 内重复定义，统一 `from livachat_protocol.generated import ...`（`packages/sdk-python`，`pip install -e`）。

---

## 12. 附录

### 12.1 与协议消息的映射表

| 协议消息 | 方向 | 处理组件 | 触发逻辑 |
|---------|------|---------|---------|
| `session.start` | C2S | SessionManager | 新建/恢复会话 |
| `session.end` | C2S | SessionManager | 结束并清理 |
| `audio` | C2S | Orchestrator+Media+ASR | 解码→喂 ASR→VAD 计数 |
| `video` | C2S | Orchestrator | 节流→存最近帧→ack |
| `interrupt` | C2S | InterruptController | 取消 LLM/TTS |
| `ping`/`pong` | C2S | Heartbeat | 刷新存活/回应 |
| `session.ready` | S2C | SessionManager | start 后下发 |
| `session.ended` | S2C | SessionManager | end/超时下发 |
| `transcription` | S2C | ASR via Orchestrator | interim/final |
| `ai_text` | S2C | LLM via Orchestrator | 流式文本 |
| `ai_audio` | S2C | TTS+Media via Orchestrator | 流式 Opus |
| `ai_video_ack` | S2C | Orchestrator | 帧处理确认 |
| `status` | S2C | StateMachine | 状态迁移 |
| `error` | S2C | 全局错误处理 | 异常/降级 |
| `ping`/`pong` | S2C | Heartbeat | 主动探测/回应 |

### 12.2 Phase 演进对接点

| 演进项 | 本期预留 |
|--------|---------|
| Phase 2 记忆 | `memories`/`user_profiles` 表已建模；`SessionContext.history` 可注入长期记忆；预留 `memory` module 接口 |
| Phase 2 向量检索 | pgvector 扩展 + `memories.embedding` 注释保留 |
| Phase 3 WebRTC | 协议预留 `webrtc_*` type；Gateway 可加信令分支；媒体层可替换 |
| 正式鉴权 | `auth.mode: jwt` 配置开关，HS256/RS256 校验代码骨架就绪 |
| 微服务拆分 | 各 module 已接口隔离，可按 gateway/asr/llm/tts/session 拆独立服务 |

### 12.3 关键技术选型

| 关注点 | 选型 | 理由 |
|--------|------|------|
| Web 框架 | FastAPI + uvicorn[uvloop] | 原生 async WS，协议文档既定 |
| 协议 DTO | `livachat_protocol`(pydantic) | SSOT，端到端类型一致 |
| PG 驱动 | asyncpg (+ SQLAlchemy 2.0 async 可选) | 高性能异步 |
| Redis | redis.asyncio | 异步、会话恢复/限流 |
| Opus | libopus (opuslib/pyogg/av) | 与客户端编码参数对齐 |
| LLM 客户端 | openai AsyncClient(base_url 可变) | 三家均 OpenAI 兼容，单一实现 |
| 配置 | pydantic-settings + YAML | 分层覆盖、env 注入密钥 |

---

*本文档为 LivaChat 后端服务 Phase 1 产品级设计规格。实现须严格遵循 [`ws-protocol-design.md`](./ws-protocol-design.md) 协议契约，并复用 `packages/api-schema` / `packages/sdk-python` 作为单一事实来源。协议或接口变更需更新本文档并三方评审。*
