# Feature Design: Server 全模态大模型 (Omni Model) 支持

> **Status**: reviewed 
> **Date**: 2026-07-01
> **Scope**: 后端服务 (`services/`) —— 在**不改动现有 WebSocket 协议**的前提下，支持以单一全模态大模型 (omni model) 替代 ASR + LLM + TTS 三段式 pipeline。

---

## 1. 功能说明 (Feature Overview)

### 1.1 背景

当前后端采用三段式 pipeline（见 `docs/server_design.md` §4.3、`services/session/orchestrator.py`）：

```
LISTENING ──ASR──> THINKING ──LLM──> SPEAKING ──TTS──> LISTENING
```

音频经 ASR 转文字 → 文字送 LLM 生成回复 → 回复经 TTS 合成音频。三段各自独立连接不同 provider，串行累加延迟（ASR endpointing + LLM 首 token + TTS 首帧），且丢失了语音中的副语言信息（语气、情感、停顿）。

**全模态大模型**（如 Qwen-Omni Realtime、OpenAI GPT-4o Realtime）在单一 realtime 连接内原生完成"听（含 VAD/turn detection）→ 理解 → 说（文字 + 音频）"，具备更低的端到端延迟与更自然的语音交互（原生 barge-in、情感语音）。

### 1.2 目标

- 新增可插拔的 **Omni 能力层** (`services/omni/`)，与现有 `asr/`、`llm/`、`tts/` 平级。
- 通过配置开关 `omni.enabled`，在会话建立时选择 **Omni 编排路径** 或 **传统三段式路径**，二者互斥。
- **omni model 支持多家 provider**：`alibaba_omni`（DashScope Qwen-Omni Realtime）、`openai_realtime`（OpenAI Realtime API），复用现有 provider/factory/service 的失败切换 (failover) 模式。
- 将 omni 的 realtime 事件流**映射回现有 s2c 协议消息**（`transcription` / `ai_text` / `ai_audio` / `status`），客户端与 WS 协议**零改动**。
- 保留现有会话语义：状态机 (idle/listening/thinking/speaking)、中断 (interrupt)、断线重连快照、持久化 (PG/Redis)、观测日志。

### 1.3 范围与非目标 (Scope & Non-Goals)

**本期范围（基本设计）**

- 音频输入（Opus → PCM16）→ omni → 文字转写 + 文字回复 + 音频回复（PCM16 → Opus）的完整闭环。
- 客户端主动中断 (`interrupt` 消息) 与服务端 VAD barge-in 的映射。
- 图像/视频帧作为**附加输入 item** 送入 omni（best-effort，沿用 `fd_video_chat_v1` 的"最近 K 帧"语义）。
- provider 失败切换（连接建立阶段）。

**非目标（后续迭代）**

- WS 协议扩展（如新增 omni 专用消息、二进制音频通道）——本期显式排除。
- 混合模式（omni 做 ASR+LLM、外部 TTS 兜底）——仅作为架构预留，不实现。
- 函数调用 / tool use、多说话人分离、音色克隆。
- 客户端侧改动。

---

## 2. 架构设计 (Architecture)

### 2.1 总体定位

Omni 是一个与 ASR/LLM/TTS **平级的能力层**，而非替换某一段。它在 orchestration 层被整体选用：

```
                         ┌─────────────────────────────────────────┐
   WS (协议不变)          │              services/                   │
  client ──────────────► │  gateway/ (endpoint, connection, codec)  │
   audio/video/interrupt │       │                                  │
                         │       ▼ 根据 config 选择编排器            │
                         │  ┌──────────────┐   ┌──────────────────┐ │
                         │  │DialogOrchestr│   │ OmniOrchestrator │ │
                         │  │ (三段式)      │   │  (全模态)         │ │
                         │  └──────┬───────┘   └────────┬─────────┘ │
                         │   asr/  llm/  tts/            │ omni/     │
                         │    │     │     │              │          │
                         └────┼─────┼─────┼──────────────┼──────────┘
                              ▼     ▼     ▼              ▼
                          Deepgram Qwen  Qwen      Qwen-Omni / OpenAI
                          ...     ...    ...       Realtime (单一双工连接)
```

**选择点**：`gateway/endpoint.py:ws_stream` 在构建 orchestrator 时读取 `cfg.omni.enabled`：
- `true` → 构建 `OmniOrchestrator`（注入 `omni_service`）；
- `false` → 构建现有 `DialogOrchestrator`（注入 `asr/llm/tts`）。

两个 orchestrator 对 `ConnectionHandler` 暴露**相同契约**：一个 `async def run(self)`，从 `handler._inbound` 队列消费 `ClientMsg`，通过 `handler.enqueue_outbound(codec.build_*(...))` 回发。因此 `gateway/connection.py`（reader/writer/heartbeat/seq/snapshot）**完全复用，无需改动**。

> **设计取舍**：为何用兄弟 orchestrator 而非在 `DialogOrchestrator` 内加分支？
> - `DialogOrchestrator` 已 1166 行、状态与三段式 provider 深度耦合（eager open ASR/TTS、turn detector、audio buffer、epoch）。
> - Omni 的驱动模型是**事件驱动**（provider 主动推送 turn 边界/中断），与三段式的**服务端 turn detector 驱动**范式不同。
> - 分离后各自内聚，共享逻辑（Opus 编解码、持久化、状态机、send helpers、视频帧管理）抽到 `session/pipeline_common.py` 复用。第一阶段可以不用抽象session/pipeline_common.py!
> - 备选方案（`pipeline_mode="omni"` 内部分支）在文末 §9 记录，不推荐。

### 2.2 模块结构

```
services/omni/
  __init__.py
  base.py           # OmniProvider / OmniStream Protocol + OmniEvent 数据类
  service.py        # OmniService —— 失败切换封装 (对齐 LlmService)
  factory.py        # create_omni(OmniConfig) -> OmniService
  providers/
    __init__.py
    alibaba_omni.py     # DashScope Qwen-Omni Realtime
    openai_realtime.py  # OpenAI Realtime API

services/session/
  omni_orchestrator.py   # 新增：OmniOrchestrator
  pipeline_common.py     # 新增：抽取 DialogOrchestrator 与 OmniOrchestrator 共享逻辑
```

复用的既有组件（**不改或极小改**）：`gateway/*`、`session/context.py`、`session/state_machine.py`、`session/manager.py`、`session/history.py`、`media/opus.py`、`media/buffer.py`、`shared/persistence/*`、`gateway/codec.py`。

---

## 3. 组件设计 (Component Design)

### 3.1 Omni 抽象接口 (`omni/base.py`)

Omni 的本质是一条**双工流**：输入 = 音频/视频/文本；输出 = 交织的转写/文本/音频事件。因此统一为单个 `OmniStream`，而非三个独立接口。输出用**带类型的事件** `OmniEvent` 承载，供 orchestrator 直接映射到 s2c 消息。

```python
# omni/base.py
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from typing import AsyncIterator, Protocol


class OmniEventType(StrEnum):
    INPUT_TRANSCRIPTION_DELTA = "input_transcription_delta"   # 用户语音的增量转写
    INPUT_TRANSCRIPTION_DONE  = "input_transcription_done"    # 用户语音转写 final
    SPEECH_STARTED            = "speech_started"              # 服务端 VAD：检测到用户说话 (barge-in 触发点)
    SPEECH_STOPPED            = "speech_stopped"              # 服务端 VAD：用户停止说话 (turn 结束)
    RESPONSE_STARTED          = "response_started"            # 模型开始生成回复 (→ thinking)
    TEXT_DELTA                = "text_delta"                  # 回复文本增量 (→ ai_text)
    AUDIO_DELTA               = "audio_delta"                 # 回复音频增量 PCM16 (→ ai_audio, 首帧→ speaking)
    RESPONSE_DONE             = "response_done"               # 本轮回复结束 (→ listening)
    ERROR                     = "error"


@dataclass
class OmniEvent:
    type: OmniEventType
    text: str = ""              # 转写 / 回复文本
    audio: bytes = b""          # PCM16 mono (AUDIO_DELTA)
    is_final: bool = False
    response_id: str = ""       # 用于跨 turn 边界丢弃陈旧音频 (对齐 qwen_tts)
    error_message: str = ""


class OmniStream(Protocol):
    """会话级双工 omni 流，跨 turn 复用（对齐 TtsStream 的 session-scoped 语义）。"""

    async def feed_audio(self, pcm: bytes) -> None:
        """推入 PCM16 mono 音频帧（对齐 AsrStream.feed）。"""
        ...

    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None:
        """推入一帧图像作为附加视觉输入（best-effort，provider 不支持则忽略）。"""
        ...

    async def commit_user_turn(self) -> None:
        """当依赖服务端 VAD 时通常为 no-op；手动 turn 模式下提交用户输入缓冲并请求回复。"""
        ...

    def events(self) -> AsyncIterator[OmniEvent]:
        """统一事件流（转写 / 文本 / 音频 / VAD / 生命周期）。"""
        ...

    async def cancel_response(self) -> None:
        """中断在途回复（response.cancel + 清空输入缓冲），用于 interrupt / barge-in。"""
        ...

    async def reset(self) -> None:
        """turn 间复位（丢弃陈旧音频，重置 response_id），不关连接。"""
        ...

    async def close(self) -> None:
        ...


class OmniProvider(Protocol):
    name: str
    supports_vision: bool

    async def open(
        self,
        *,
        sample_rate: int,
        system_prompt: str = "",
        voice: str = "",
        modalities: tuple[str, ...] = ("text", "audio"),
        generation_opts: dict | None = None,
    ) -> OmniStream:
        ...
```

> 该接口刻意贴近现有 provider 契约：`open()` 对齐 `TtsProvider.open`，`feed_audio` 对齐 `AsrStream.feed`，`events()` 对齐 `AsrStream.results()`。realtime 内部实现（session.created / session.update 握手、后台 receive loop + `asyncio.Queue`、response_id turn 边界处理、断线重连）可**直接复用** `alibaba_qwen_asr.py` / `alibaba_qwen_tts.py` 的成熟模式。

### 3.2 OmniService —— 失败切换 (`omni/service.py`)

对齐 `llm/service.py:LlmService`：持有 `[active] + fallbacks`，在 `open()` 阶段（连接握手失败）切换到下一个 provider；连接建立后不做中途切换（realtime 有状态，无法透明迁移）。

```python
class OmniService:
    def __init__(self, active: OmniProvider, fallbacks: list[OmniProvider]) -> None:
        self._providers = [active] + fallbacks

    async def open(self, **kwargs) -> tuple[OmniStream, str]:
        """返回 (stream, provider_name)。全部失败抛 ProviderError(OMNI_UNAVAILABLE)。"""
        last_err = None
        for idx, p in enumerate(self._providers):
            try:
                return await p.open(**kwargs), p.name
            except Exception as exc:
                last_err = exc
                logger.warning("omni provider open failed, trying next",
                               extra={"provider": p.name, "error": str(exc)})
        raise ProviderError(f"all omni providers failed: {last_err}",
                            provider="omni", code=ErrorCode.OMNI_UNAVAILABLE)
```

> 需在 `shared/utils/errors.py:ErrorCode` 增加 `OMNI_UNAVAILABLE`（复用现有错误码体系与 `make_error` 上报路径）。

### 3.3 Provider 实现

两家 provider 都基于 realtime WebSocket，事件语义高度相似（OpenAI Realtime 协议是事实标准，Qwen 系列亦兼容其形态）。共性逻辑（连接、握手、后台 recv loop、队列、response_id 处理）抽到内部基类 `_RealtimeOmniStream`，两家仅差异化：URL/鉴权头、`session.update` 载荷、事件类型名映射。

**alibaba_omni** (`providers/alibaba_omni.py`)
- URL：业务空间专属域名 `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-omni-flash-realtime`
  （3.8 起必须绑定业务空间，公共域名不可用；见 `docs/qwen_omni_realtime.md` §2.1）
- 握手：等 `session.created` → 发 `session.update`（`modalities: ["text","audio"]`、`voice`、nested
  `audio.input/output.format`（16 kHz，**生效字段**）、flat `input_audio_format`/`output_audio_format`/`sample_rate`
  （兼容回显，实测不生效）、`turn_detection: {type:"semantic_vad"}`、`instructions: <system_prompt>`）→ 等 `session.updated`。
- 事件映射：
  | Qwen Realtime event | OmniEvent |
  |---|---|
  | `input_audio_buffer.speech_started` | `SPEECH_STARTED` |
  | `input_audio_buffer.speech_stopped` | `SPEECH_STOPPED` |
  | `conversation.item.input_audio_transcription.text/.completed` | `INPUT_TRANSCRIPTION_DELTA/DONE` |
  | `response.created` | `RESPONSE_STARTED` |
  | `response.text.delta` (或 `.audio_transcript.delta`) | `TEXT_DELTA` |
  | `response.audio.delta` | `AUDIO_DELTA` (base64→PCM16) |
  | `response.done` | `RESPONSE_DONE` |
  | `error` | `ERROR` |
- 输入：音频 `input_audio_buffer.append`；图像 `conversation.item.create`（image_url data URL）。
- 中断：`response.cancel` + `input_audio_buffer.clear`。

**openai_realtime** (`providers/openai_realtime.py`)
- URL：`wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview`，头 `Authorization: Bearer` + `OpenAI-Beta: realtime=v1`。
- 事件名与上表基本一致（Qwen 对齐 OpenAI）；差异：音频输出格式默认 `pcm16` 24kHz（需按 §4.4 重采样到会话 sample_rate），`response.audio_transcript.delta` 承载文本。
- 视觉：GPT-4o Realtime 当前不吃实时视频帧 → `supports_vision=False`，`feed_image` 直接忽略并记 debug 日志（不报错，保持协议兼容）。

### 3.4 配置模型 (`shared/config/models.py`)

新增 `OmniProviderConfig` / `OmniConfig`，`AppConfig` 增加 `omni` 字段。**`omni.enabled` 是总开关**——为 true 时走 omni 路径。

```python
class OmniProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str | None = None
    supports_vision: bool = False
    modalities: list[str] = ["text", "audio"]
    extra_body: dict[str, object] | None = None

class OmniConfig(BaseModel):
    enabled: bool = False          # 总开关：true → omni 路径；false → 三段式
    active: str = ""
    fallback: list[str] = []
    system_prompt_path: str = "configs/dev/prompts/companion.txt"
    generation: LlmGenerationConfig = LlmGenerationConfig()
    providers: dict[str, OmniProviderConfig] = {}

# AppConfig 增加：
    omni: OmniConfig = OmniConfig()
```

环境变量沿用现有机制：`LIVACHAT__OMNI__ENABLED=true`、`LIVACHAT__OMNI__ACTIVE=alibaba_omni` 可覆盖。

### 3.5 工厂 (`omni/factory.py`)

对齐 `llm/factory.py`：

```python
_PROVIDER_CLASSES = {
    "alibaba_omni": AlibabaOmniProvider,
    "openai_realtime": OpenAIRealtimeProvider,
}

def create_omni(config: OmniConfig) -> OmniService:
    if config.active not in config.providers:
        raise ConfigError(f"unknown omni provider: {config.active}")
    active = _create_provider(config.active, config.providers[config.active].model_dump())
    fallbacks = [_create_provider(n, config.providers[n].model_dump()) for n in config.fallback]
    return OmniService(active=active, fallbacks=fallbacks)
```

### 3.6 生命周期装配 (`app/application.py:lifespan`)

在现有 ASR/LLM/TTS 创建块之后增加分支。**互斥**：omni 启用时跳过三段式服务创建，节省连接资源。

```python
omni_service = None
if cfg.omni.enabled:
    try:
        from omni.factory import create_omni
        omni_service = create_omni(cfg.omni)
        system_prompt = load_system_prompt(cfg.omni.system_prompt_path)
        logger.info("Omni service created", extra={"active": cfg.omni.active})
    except Exception:
        logger.warning("Omni service not available", exc_info=True)
else:
    # 现有 asr/llm/tts 创建逻辑（保持不变）
    ...

app.state.omni_service = omni_service
app.state.omni_enabled = cfg.omni.enabled
```

### 3.7 端点选择编排器 (`gateway/endpoint.py`)

```python
if getattr(ws.app.state, "omni_enabled", False) and ws.app.state.omni_service is not None:
    from session.omni_orchestrator import OmniOrchestrator
    orchestrator = OmniOrchestrator(
        codec=codec,
        session_mgr=ws.app.state.session_mgr,
        omni_service=ws.app.state.omni_service,
        opus_encoder=opus_enc,
        opus_decoder=opus_dec,
        system_prompt=ws.app.state.system_prompt,
        generation_opts=cfg.omni.generation.model_dump(),
        voice=cfg.omni.providers[cfg.omni.active].voice or "",
        pg_session_factory=ws.app.state.pg_session_factory,
    )
else:
    orchestrator = DialogOrchestrator(...)  # 现有逻辑不变
```

`ConnectionHandler` 与其后续 `handler.run()` 无差别。

### 3.8 OmniOrchestrator (`session/omni_orchestrator.py`)

核心区别：**事件驱动**而非 turn-detector 驱动。两个协程配合：

1. **dispatch loop**（`run()`）：消费 `handler._inbound`，把 `audio`/`video`/`interrupt`/`session.*` 转成对 `OmniStream` 的调用（对齐 `DialogOrchestrator._dispatch`）。
2. **event pump**（后台 task）：迭代 `omni_stream.events()`，将每个 `OmniEvent` 映射为 s2c 消息 + 状态机迁移。

```python
async def run(self):
    q = self._handler._inbound
    while not self._handler._stopped:
        try:
            msg = await asyncio.wait_for(q.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        await self._dispatch(msg)      # session.start 时 eager open + 启动 event pump
    # finally: cancel event pump, close omni stream

async def _dispatch(self, msg):
    t = msg.type
    if t == "session.start":  await self._handle_session_start(msg)
    elif t == "audio":        await self._feed_audio(msg)      # Opus→PCM→feed_audio
    elif t == "video":        await self._feed_video(msg)      # base64 JPEG→feed_image
    elif t == "interrupt":    await self._handle_interrupt(msg) # →cancel_response + status
    elif t == "session.end":  await self._handle_session_end(msg)

async def _event_pump(self):
    epoch_snap = self._ctx.epoch
    async for ev in self._omni_stream.events():
        # 中断/turn 边界后陈旧事件用 epoch + response_id 双重丢弃
        if ev.type == OmniEventType.INPUT_TRANSCRIPTION_DELTA:
            await self._send_transcription(ev.text, is_final=False, transcription_id=self._tid)
        elif ev.type == OmniEventType.INPUT_TRANSCRIPTION_DONE:
            await self._send_transcription(ev.text, is_final=True, transcription_id=self._tid)
            self._history.add_message("user", ev.text); self._persist_transcription(ev.text)
        elif ev.type == OmniEventType.SPEECH_STARTED and self._ctx.state == SPEAKING:
            await self._do_interrupt("user_voice")          # 服务端 VAD barge-in
        elif ev.type == OmniEventType.RESPONSE_STARTED:
            self._ctx.sm.transition_or_reject(THINKING); await self._send_status("thinking", "asr_final")
        elif ev.type == OmniEventType.TEXT_DELTA:
            await self._send_ai_text(ev.text, message_id=self._mid, index=self._ti, is_final=False); self._ti += 1
        elif ev.type == OmniEventType.AUDIO_DELTA:
            if self._ctx.state != SPEAKING:
                self._ctx.sm.transition_or_reject(SPEAKING); await self._send_status("speaking", "tts_start")
            for frame in self._audio_out_buf.feed(ev.audio):        # 复用 AudioFrameBuffer
                await self._send_ai_audio(self._opus_enc.encode(frame), message_id=self._mid, index=self._ai, ...); self._ai += 1
        elif ev.type == OmniEventType.RESPONSE_DONE:
            await self._send_ai_text("", message_id=self._mid, index=self._ti, is_final=True, image=self._last_image)
            self._history.add_message("assistant", self._full_text); self._persist_messages(...)
            self._ctx.sm.transition_or_reject(LISTENING); await self._send_status("listening", "tts_finish")
            self._reset_turn()                                       # 新 message_id/tid、buf.reset()
        elif ev.type == OmniEventType.ERROR:
            await self._handler.enqueue_outbound(self._codec.make_error(code=1003, message=ev.error_message))
```

**复用要点**：`_send_transcription/_send_ai_text/_send_ai_audio/_send_status`、`_persist_*`、`AudioFrameBuffer`、`SessionContext`、状态机、Opus 编解码器全部与 `DialogOrchestrator` 相同 —— 抽到 `session/pipeline_common.py` 的 mixin/基类中，两个 orchestrator 继承。

**中断实现**（`_do_interrupt`）：`ctx.epoch += 1`（使 event pump 中的陈旧 response_id 事件失效）→ `omni_stream.cancel_response()` → `audio_out_buf.reset()` → `send_status("listening", "interrupted")`。与现有 `InterruptController` 语义一致，可复用。

**视觉/视频**：沿用 `fd_video_chat_v1` 的"最近 K 帧"。收到 `video` 消息时 `feed_image()`（provider 不支持则 no-op），并快照最后一帧放入 `RESPONSE_DONE` 的 final `ai_text.image` 字段，客户端展示逻辑不变。

---

## 4. 基本交互流程 (Interaction Flows)

### 4.1 会话建立 (session.start)

```
Client                Gateway/OmniOrchestrator            Omni Provider (realtime)
  │  session.start ───────►│
  │                        │ session_mgr.start() → ctx/history
  │  ◄── session.ready ────│
  │  ◄── status:idle ──────│ (新会话)
  │  ◄── status:listening ─│
  │                        │ omni_service.open() ──── ws connect ───►│
  │                        │   (system_prompt/voice/modalities)      │◄─ session.created
  │                        │                          session.update ►│
  │                        │                                          │◄─ session.updated
  │                        │ 启动 event_pump 后台 task                │
```
与三段式一致，session.ready + 初始 status 由 orchestrator 主动发。omni 流 **eager open**（消除首帧冷启动），失败则记录并在首个 audio 帧重试（对齐现有 ASR eager open 逻辑）。

### 4.2 一轮对话（音频输入 → 语音+文字回复）

```
Client                     OmniOrchestrator                    Omni Provider
  │ audio(Opus)×N ─────────►│ Opus→PCM → feed_audio() ─────────────►│ (server VAD 累积)
  │                         │                                       │
  │                         │◄─── SPEECH_STOPPED (turn 结束) ────────│  ← provider 自主判定
  │ ◄── transcription final │◄─── INPUT_TRANSCRIPTION_DONE ─────────│
  │ ◄── status:thinking ────│◄─── RESPONSE_STARTED ─────────────────│
  │ ◄── ai_text delta ×N ───│◄─── TEXT_DELTA ×N ────────────────────│
  │ ◄── status:speaking ────│◄─── AUDIO_DELTA (首帧) ────────────────│
  │ ◄── ai_audio(Opus) ×N ──│◄─── AUDIO_DELTA ×N (PCM→重采样→Opus) ──│
  │ ◄── ai_text final ──────│◄─── RESPONSE_DONE ────────────────────│
  │ ◄── status:listening ───│  reset turn (新 message_id, buf.reset)│
```

关键：**turn detection 由 omni provider 的 server VAD 完成**，服务端不再运行 `TurnDetector`。transcription/ai_text/ai_audio/status 消息格式与三段式**完全相同**，客户端无感知。

### 4.3 中断 (Interrupt)

**客户端主动中断**（用户点击/切后台）：
```
Client  ── interrupt ──►│ epoch+=1 → cancel_response() ─► response.cancel + buffer.clear ►│
        ◄─ status:listening (interrupted) ─│  audio_out_buf.reset()
```
**服务端 VAD barge-in**（用户在 AI 说话时插话）：event pump 收到 `SPEECH_STARTED` 且当前 `state==SPEAKING` → 触发同一 `_do_interrupt("user_voice")`。in-flight 的 `TEXT_DELTA/AUDIO_DELTA` 因 epoch 快照不匹配被丢弃（对齐现有 `InterruptController.should_yield`）。

> 与三段式不同：omni 的 barge-in 由 provider 原生检测，响应更快、更准，无需服务端 30 帧 VAD debounce。

### 4.4 音频采样率适配

- 输入：客户端 Opus @ `audio_sample_rate`（默认 16kHz）→ `OpusDecoder` → PCM16 → `feed_audio`。若 provider 要求特定输入采样率（如 OpenAI 24kHz），在 provider 内用 `media/resample.py` 升采样。
- 输出：provider 返回 PCM16（Qwen 可配 16kHz；OpenAI 固定 24kHz）→ 必要时 `resample` 到会话 `audio_sample_rate` → `AudioFrameBuffer` 分帧 → `OpusEncoder` → `ai_audio`。采样率协商在 `OmniProvider.open()` 内封装，orchestrator 只见会话采样率。

### 4.5 Provider 失败切换

`OmniService.open()` 在**连接握手阶段**失败时切到 `fallback`（如 `alibaba_omni` → `openai_realtime`）。全部失败 → orchestrator 发 `error(code=OMNI_UNAVAILABLE, fatal=false)`，状态回 listening。连接建立后的运行期错误（`ERROR` 事件/断线）：轻量重连（复用 qwen provider 的 `_reconnect`），重连失败则上报 fatal error。

### 4.6 断线重连与会话结束

- **断线快照**：`connection.py:_snapshot_session` 已通过 `orchestrator._session_mgr` / `_ctx` / `_history` 存快照 —— `OmniOrchestrator` 暴露相同的 `_session_mgr/_ctx/_history` 属性即可**零改动复用**。恢复时 omni 以 `system_prompt + 历史` 重建上下文（realtime 会话无法迁移，重开一条并注入近 N 轮历史）。
- **session.end**：cancel event pump → `omni_stream.close()` → `session_mgr.end()` → `handler.stop()`。

---

## 5. 状态机映射

复用现有 4 态状态机 (`session/state_machine.py`)，仅**触发源**变化：

| 状态迁移 | 三段式触发 | Omni 触发 |
|---|---|---|
| idle → listening | session.start 后 | 同左 |
| listening → thinking | turn detector 静音 + ASR final | `RESPONSE_STARTED` 事件 |
| thinking → speaking | TTS 首帧音频 | `AUDIO_DELTA` 首帧 |
| speaking → listening | TTS drain 完成 | `RESPONSE_DONE` 事件 |
| *→ listening (中断) | client interrupt / VAD debounce | client interrupt / `SPEECH_STARTED` |

s2c `status` 消息的 `session_status` / `reason` 取值不变，客户端状态机无需改动。

---

## 6. 配置示例 (`configs/dev/server.yaml`)

```yaml
# 启用全模态模式（与 asr/llm/tts 互斥）
omni:
  enabled: true
  active: alibaba_omni
  fallback: [openai_realtime]
  system_prompt_path: ../configs/dev/prompts/companion.txt
  generation: { temperature: 0.8, max_tokens: 1024, top_p: 0.9 }
  providers:
    alibaba_omni:
      enabled: true
      base_url: wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-omni-flash-realtime
      api_key: "${DASHSCOPE_API_KEY}"
      model: qwen3.8-omni-flash-realtime
      voice: Tina
      workspace_id: "{WorkspaceId}"
      supports_vision: true
      modalities: [text, audio]
    openai_realtime:
      enabled: true
      base_url: wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview
      api_key: "${OPENAI_API_KEY}"
      model: gpt-4o-realtime-preview
      voice: alloy
      supports_vision: false
      modalities: [text, audio]

# omni.enabled=true 时以下三段式配置被忽略（保留以便一键回退）
asr:  { active: alibaba_bailian, ... }
llm:  { active: alibaba_bailian, ... }
tts:  { active: alibaba_qwen_tts, ... }
```

一键回退：`omni.enabled: false`（或 `LIVACHAT__OMNI__ENABLED=false`）即恢复三段式，无需重构。

---

## 7. 兼容性与影响面

| 维度 | 影响 |
|---|---|
| WS 协议 / `api-schema` | **零改动**（本期硬约束） |
| 客户端 SDK | **零改动**（消息格式一致） |
| `gateway/*` | 仅 `endpoint.py` 增加 orchestrator 选择分支 |
| `session/orchestrator.py` (三段式) | 抽取共享逻辑到 `pipeline_common.py`（重构，行为不变） |
| 新增文件 | `omni/*`、`session/omni_orchestrator.py`、`session/pipeline_common.py` |
| 配置 | `AppConfig` 增加 `omni` 段；旧配置默认 `enabled=false` 向后兼容 |
| 持久化 | `ctx.asr_provider/llm_provider/tts_provider` 复用为记录 omni provider 名（或新增 `omni_provider` 字段，见 §9） |
| 观测 | 新增 `livachat.omni.messages` logger，对齐 `livachat.gateway.messages` 采样策略 |

---

## 8. 测试策略（概要）

- **单元**：`OmniService` 失败切换；provider 事件映射（mock realtime 事件流 → 断言 `OmniEvent`）；orchestrator event pump 的状态迁移与 s2c 消息序列。
- **集成**：`FakeOmniProvider`（内存模拟 realtime，脚本化 speech_stopped/text/audio/response_done）驱动 `OmniOrchestrator`，断言与三段式相同的 s2c 序列（transcription→thinking→ai_text→speaking→ai_audio→listening）。
- **真机脚本**：对齐 `services/scripts/test_qwen_*_real.py`，新增 `test_omni_real.py` 打通 DashScope/OpenAI realtime。
- **回退验证**：`omni.enabled=false` 时全部现有测试通过（保证零回归）。

---

## 9. 待决问题与备选方案 (Open Questions)

1. **provider 名持久化**：复用 `ctx.llm_provider` 记录 omni 名，还是新增 `ctx.omni_provider` + PG 列？倾向新增列（分析更清晰），需一个 migration。
2. **视频/多帧**：Qwen-Omni 对 realtime 视频帧的确切支持形态待验证；本期仅按"最近 K 帧图像 item"实现，实时视频流留后续。
3. **手动 turn vs server VAD**：默认信任 provider server VAD。若某 provider VAD 质量不足，预留 `commit_user_turn()` + 复用 `TurnDetector` 的手动兜底路径（配置项 `omni.turn_detection: server_vad | manual`）。
4. **备选架构（不推荐）**：在 `DialogOrchestrator` 内以 `pipeline_mode="omni"` 分支实现。优点是少一个类；缺点是事件驱动与 turn-detector 驱动两种范式混在 1166 行内，可维护性差、回归风险高。故采用 §2.1 的兄弟 orchestrator + 共享 mixin 方案。
5. **音频输出采样率**：OpenAI 固定 24kHz，重采样到 16kHz 有轻微质量损失；可评估让客户端协商 24kHz 播放（属协议/客户端改动，超出本期范围）。
```
