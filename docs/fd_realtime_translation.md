# Feature Design: 实时同声翻译 (Realtime Simultaneous Translation)

> **Status**: reviewed（**部分废弃，2026-09-14**：本文设计的「livetranslate 作为 **omni provider**」
> 配置路径已移除——`configs/dev/server.yaml` 的 `omni.providers.alibaba_livetranslate` 段、
> `omni/factory.py` 的注册分支与 `OmniProviderConfig` 的同传字段均已删除；同传改由
> **pipeline lecture 路径**（`translate:` 段 + `session.change(usecase="lecture")`）提供，
> 见 [`fd_pipeline_translate_lecture.md`](fd_pipeline_translate_lecture.md)。
> 下文 §3.1 配置、§5 yaml 示例、§6 改动清单保留为历史实现记录。）
> **2026-10-10 迁移注（3.8-only，已实施）**：模型已从 `qwen3.5-livetranslate-flash-realtime`
> 迁移至 `qwen3.8-livetranslate-flash-realtime`（3.5 不再支持）——`session.modalities` →
> `session.output_modalities`；增量文本事件 `.text` → `.delta`；`input_audio_transcription`
> 不再发送（3.8 ASR 常开、不可关、免费）。本文所记请求/事件名均为 3.5 口径，仅供历史对照；
> 现行口径与迁移细节见 [`qwen_livetranslate_realtime.md`](qwen_livetranslate_realtime.md)。
> **Date**: 2026-07-04
> **Scope**: 后端服务 (`services/`) —— 接入 DashScope `qwen3.5-livetranslate-flash-realtime` 模型实现同声翻译；源语种 / 目标语种由 **server 配置文件** 指定；**第一阶段保持现有 client/server WebSocket 通信协议不变**。
> **相关文档**: `docs/fd_server_omni_model.md`（Omni 全模态编排路径）、`docs/ws-protocol-design.md`、`docs/server_design.md`
> **上游 API**: [实时语音/音视频翻译-千问](https://help.aliyun.com/zh/model-studio/qwen3-5-livetranslate-flash-realtime) · [客户端事件](https://help.aliyun.com/zh/model-studio/live-translator-client-events) · [服务端事件](https://help.aliyun.com/zh/model-studio/live-translator-server-events)

---

## 1. 功能说明 (Feature Overview)

### 1.1 背景

`qwen3.5-livetranslate-flash-realtime` 是视觉增强型实时翻译模型，通过 **WebSocket** 接入 DashScope（OpenAI-Realtime 风格协议），特性：

- 支持 60 种语言互译（29 种支持"音频+文本"输出，31 种仅文本），约 **2.8s 同传延迟**；
- 单一双工连接内完成"听音频（服务端自动 VAD 分句）→ 翻译 → 说（译文文本 + 译文音频）"；
- 可选输出**源语言识别原文**（`input_audio_transcription.model = qwen3-asr-flash-realtime`）；
- 可选图像输入辅助消歧、热词（corpus）、声音复刻（voice clone）。

它与仓库已有的 `alibaba_omni`（`qwen3.5-omni-flash-realtime`）**共用同一套 realtime WS 协议骨架**（`session.created / session.update / input_audio_buffer.append / input_image_buffer.append / response.* / session.finish(ed)`），差异仅在 `session.update` 的字段与若干输出事件的命名/语义。

### 1.2 核心洞察：为何协议可以不变

同传场景到现有 s2c 协议的映射是**天然的**：

| LiveTranslate 输出 | 现有 s2c 消息 | 语义 |
|---|---|---|
| 源语言识别原文（ASR） | `transcription` | 用户气泡 = 说话人原文 |
| 译文文本 | `ai_text` | AI 气泡 = 译文 |
| 译文音频 | `ai_audio` | 播放 = 译文语音 |
| 状态迁移 | `status` (listening/thinking/speaking) | 每句翻译一个 turn |

因此客户端**无需任何改动**即可复用现有会话 UI：原文显示在"用户"侧，译文显示在"AI"侧并播放译文语音。第一阶段只在后端新增一个 provider + 少量配置，即可跑通闭环。

### 1.3 目标

- 新增可插拔 provider **`alibaba_livetranslate`**，实现现有 `OmniProvider` / `OmniStream` 协议，**复用 `OmniOrchestrator` 与 `OmniService`/`create_omni` 失败切换机制**。
- 源/目标语种、输出模态、是否输出源语言原文、音色、热词、声音复刻等，全部通过 **`configs/<env>/server.yaml`** 配置。
- 将 LiveTranslate 的 realtime 事件流映射回现有 `transcription`/`ai_text`/`ai_audio`/`status` 消息，**client 与 WS 协议零改动**。
- 保留既有会话语义：状态机、持久化 (PG/Redis)、断线快照、观测日志、心跳/序列号。

### 1.4 范围与非目标

**本期范围（基本设计）**

- 音频输入（Opus→PCM16）→ LiveTranslate → 源语言原文（`transcription`）+ 译文文本（`ai_text`）+ 译文音频（PCM24→Opus，`ai_audio`）完整闭环。
- 源/目标语种由 server 配置文件驱动。
- 图像帧作为附加输入（best-effort，沿用 omni 的"最近 K 帧"语义，可配置关闭）。
- provider 连接建立阶段的失败切换。

**非目标（后续迭代）**

- WS 协议扩展（如客户端运行时切换语种、per-session 覆盖语种）——本期显式排除，语种为**全局配置级**。
- 客户端 UI 文案本地化 / 双向对话式翻译（两个说话人各自母语）。
- 声音复刻的端到端产品化（仅在配置层预留字段）。
- 客户端侧改动。

> **第一阶段的已知约束**：源/目标语种是**服务实例级**配置，一个部署固定一对语向（或一组 provider）。客户端无法在会话内选择语种。运行时可切换语向留待第二阶段（需扩展协议，见 §9）。

---

## 2. 架构设计 (Architecture)

### 2.1 总体定位：复用 Omni 编排路径

LiveTranslate 与 Omni 同为"单一 realtime 双工模型"，驱动范式一致（provider 主动推送 turn 边界/事件）。因此**不新建编排器**，而是把 LiveTranslate 实现成 `omni/providers/` 下的又一个 provider，挂到既有的 `OmniOrchestrator` 上：

```
                         ┌─────────────────────────────────────────────┐
   WS (协议不变)          │                 services/                    │
  client ──────────────► │   gateway/ (endpoint, connection, codec)     │
   audio/video/interrupt │        │                                     │
                         │        ▼  cfg.omni.enabled=true → OmniOrchestrator
                         │   ┌──────────────────┐                        │
                         │   │ OmniOrchestrator │  (复用，事件→s2c 映射) │
                         │   └────────┬─────────┘                        │
                         │            │ omni/  OmniService(failover)     │
                         │            ▼                                  │
                         │   OmniProvider 实现：                          │
                         │   • alibaba_omni          (对话)              │
                         │   • openai_realtime       (对话)              │
                         │   • alibaba_livetranslate (同传) ★新增        │
                         └───────────────────────────────┬──────────────┘
                                                          ▼
                              wss://dashscope.../realtime?model=qwen3.5-livetranslate-flash-realtime
```

**选择点不变**：`gateway/endpoint.py:ws_stream` 仍按 `cfg.omni.enabled` 选 `OmniOrchestrator`；`cfg.omni.active = alibaba_livetranslate` 即启用同传。`gateway/*`、状态机、持久化、快照全部复用。

> **设计取舍：复用 Omni 路径 vs 新建 Translate 编排器 vs 复用三段式 DialogOrchestrator**
> - **复用 Omni 路径（本方案，推荐）**：LiveTranslate 的 WS 事件 90% 与 `alibaba_omni` 同构，`OmniOrchestrator`（state machine / interrupt / audio buffer / send & persist helpers / 视频帧）可直接复用。改动面最小：1 个新 provider + 配置 + 少量事件命名适配。
> - 新建 `TranslateOrchestrator`：重复 `OmniOrchestrator` 逻辑，收益低。
> - 复用三段式 `DialogOrchestrator`：范式不符（它由服务端 turn detector 驱动、串接三个独立 provider），强行塞入 realtime 双工模型代价大。

### 2.2 模块结构

```
services/omni/
  base.py                       # OmniProvider / OmniStream / OmniEvent  (不改)
  service.py                    # OmniService failover                    (不改)
  factory.py                    # create_omni: 注册 alibaba_livetranslate  ★改
  providers/
    alibaba_omni.py             # 现有 (不改)
    openai_realtime.py          # 现有 (不改)
    alibaba_livetranslate.py    # ★新增：LiveTranslateProvider + Stream

services/shared/config/
  models.py                     # OmniProviderConfig 增补 translation 字段  ★改
  validation.py                 # (可选) 校验语种合法性                     ★改

configs/<env>/server.yaml       # 新增 alibaba_livetranslate provider 配置  ★改

services/session/omni_orchestrator.py   # 复用；至多做 §4.2 的小适配        △可能微调
services/gateway/endpoint.py            # 输出 Opus 编码器采样率适配 (§4.3)  ★改
```

复用不改：`gateway/connection.py`、`gateway/codec.py`、`session/{context,state_machine,manager,history,interrupt}.py`、`media/{opus,buffer}.py`、`shared/persistence/*`、`packages/api-schema`（协议不变）。

---

## 3. 组件设计 (Component Design)

### 3.1 配置模型 (`shared/config/models.py`)

在 `OmniProviderConfig` 增补同传相关字段（保持向后兼容，均有默认值）：

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

    # ---- 新增：同声翻译 (仅 alibaba_livetranslate 使用) ----
    source_language: str = "en"          # session.input_audio_transcription.language
    target_language: str = "en"          # session.translation.language
    enable_source_transcription: bool = True   # 是否回传源语言原文(有额外 token 计费)
    source_transcription_model: str = "qwen3-asr-flash-realtime"
    corpus_phrases: dict[str, str] | None = None   # 热词 source->target
    input_sample_rate: int = 16000       # 送入模型的输入采样率 (8000|16000)
    output_sample_rate: int = 24000      # 模型输出音频固定 24kHz (下游 Opus 编码用)
    enable_voice_clone: bool = False
    voice_clone_frequency: str | None = None   # never|once|always
```

> **为何放在 provider 级**：语种/热词是 LiveTranslate provider 专有配置，放在 `providers.alibaba_livetranslate.*` 下最内聚，且天然随 provider 一起被 `create_omni` 注入，无需触碰其它 provider。

### 3.2 Factory 注册 (`omni/factory.py`)

在 `_create_provider` 增加分支：

```python
elif name == "alibaba_livetranslate":
    from omni.providers.alibaba_livetranslate import LiveTranslateProvider
    return LiveTranslateProvider(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=cfg["model"],
        voice=cfg.get("voice") or "",
        source_language=cfg["source_language"],
        target_language=cfg["target_language"],
        enable_source_transcription=cfg["enable_source_transcription"],
        source_transcription_model=cfg["source_transcription_model"],
        corpus_phrases=cfg.get("corpus_phrases"),
        input_sample_rate=cfg["input_sample_rate"],
        enable_voice_clone=cfg["enable_voice_clone"],
        voice_clone_frequency=cfg.get("voice_clone_frequency"),
    )
```

### 3.3 Provider 实现 (`omni/providers/alibaba_livetranslate.py`)

结构对齐 `alibaba_omni.py`：`LiveTranslateProvider`（实现 `OmniProvider.open`）+ 内部 `_RealtimeTranslateStream`（实现 `OmniStream`）。**大量逻辑可从 `alibaba_omni.py` 复制**（连接/接收循环/reconnect/feed_audio/feed_image/close），仅两处不同：**session.update 构造** 与 **事件解析映射**。

#### 3.3.1 连接与 session.update

- URL 由配置提供：`wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3.5-livetranslate-flash-realtime`
- 鉴权：`Authorization: Bearer <DASHSCOPE_API_KEY>`（同 omni）
- 握手：收 `session.created` → 发 `session.update` → 收 `session.updated`（同 omni）

`session.update` payload（**与 omni 关键差异**：无 `instructions`/`enable_search`/`turn_detection`；改用 `translation` + `input_audio_transcription`）：

```jsonc
{
  "event_id": "evt_session_update",
  "type": "session.update",
  "session": {
    "modalities": ["text", "audio"],          // 或 ["text"] 仅文本
    "voice": "Tina",                          // 目标语音音色
    "sample_rate": 16000,                     // 输入采样率
    "input_audio_format": "pcm",
    "output_audio_format": "pcm",             // 输出固定 24kHz PCM
    "input_audio_transcription": {            // 可选：回传源语言原文
      "model": "qwen3-asr-flash-realtime",
      "language": "zh"                        // = source_language
    },
    "translation": {
      "language": "en",                       // = target_language
      "corpus": { "phrases": { "人工智能": "Artificial Intelligence" } }  // 可选热词
    }
    // 声音复刻(可选): "enable_voice_clone": true,
    //               "voice_clone_options": {"frequency": "once"}, voice="default"
  }
}
```

> `enable_source_transcription=false` 时省略 `input_audio_transcription`，此时不产生 `transcription` 消息（客户端只显示译文），并节省源文识别的 token 计费。

#### 3.3.2 输入

- `feed_audio(pcm)` → `input_audio_buffer.append`（Base64 PCM16）。**服务端自动 VAD 分句并触发响应**，无需手动 commit。
- `feed_image(jpeg)` → `input_image_buffer.append`（Base64 JPEG，≤500KB，≤2fps；首帧图片前必须已发过至少一帧音频）。
- **无 `response.cancel` / `input_audio_buffer.clear` 客户端事件**（见 §4.4）。
- `close()` → `session.finish`，等待 `session.finished` 后关闭（同 omni 语义，务必发送，否则丢最后一句）。

#### 3.3.3 事件映射（→ `OmniEvent`）

`OmniOrchestrator._event_pump` 消费 `OmniEvent`，映射到 s2c 消息。Provider 内把 LiveTranslate 服务端事件翻译成既有 `OmniEventType`：

| LiveTranslate 服务端事件 | 字段 | → OmniEvent | → s2c 消息 |
|---|---|---|---|
| `session.created` / `session.updated` | — | (握手，内部消费) | — |
| `conversation.item.input_audio_transcription.text` | `text`(已确认)+`stash`(待确认)+`language` | `INPUT_TRANSCRIPTION_DELTA` (text=`text`+`stash`) | `transcription` (is_final=false) |
| `conversation.item.input_audio_transcription.completed` | `transcript` | `INPUT_TRANSCRIPTION_DONE` (text=`transcript`) | `transcription` (is_final=true) |
| `response.created` | `response.id` | `RESPONSE_STARTED` | `status: thinking` |
| `response.audio_transcript.text` | `text`(已确认)+`stash` | `TEXT_DELTA`（**增量转换见 §4.1**） | `ai_text` (is_final=false) |
| `response.audio_transcript.done` | `transcript` | `TEXT_DELTA`(尾部) | `ai_text` |
| `response.text.text` / `response.text.done` | `text` | `TEXT_DELTA`（仅 `["text"]` 模态时） | `ai_text` |
| `response.audio.delta` | `delta`(b64 PCM24) | `AUDIO_DELTA` | `ai_audio` (opus) |
| `response.audio.done` | — | (忽略) | — |
| `response.done` | `usage` | `RESPONSE_DONE` | `ai_text(final)` + `status: listening` |
| `input_audio_buffer.speech_started/stopped` | — | `SPEECH_STARTED/STOPPED`（**若模型发送**，best-effort） | 见 §4.4 |
| `session.finished` | — | `_SENTINEL`（结束 events 迭代） | — |
| `error` | `error.message/code` | `ERROR` | `error` (code 1007) |

> `response.output_item.*` / `response.content_part.*` 为结构性事件，本期忽略（`.text`/`.delta`/`.done` 已覆盖内容）。

### 3.4 编排器复用 (`session/omni_orchestrator.py`)

`OmniOrchestrator` 现有事件处理逻辑（`_event_pump`）**几乎可直接复用**：

- `INPUT_TRANSCRIPTION_*` → `_send_transcription`（原文）✅
- `RESPONSE_STARTED` → `THINKING` + `status` ✅
- `TEXT_DELTA` → 累加 `_full_text` + `_send_ai_text` 增量 ✅（依赖 §4.1 的 delta 转换）
- `AUDIO_DELTA` → `AudioFrameBuffer` 切帧 + Opus 编码 + `_send_ai_audio` ✅（依赖 §4.3 的 24kHz 编码器）
- `RESPONSE_DONE` → flush 音频 + `ai_text(final)` + `LISTENING` + 持久化 ✅

持久化字段沿用（`ctx.llm_provider = "alibaba_livetranslate"`，`kind` 复用 asr/llm/tts 统计）。是否需要微调见 §4。

---

## 4. 关键技术点与风险 (Key Technical Points & Risks)

### 4.1 译文文本是"修订"语义，需转成"追加"增量 ★重要

- `ai_text` 协议是 **append 语义**（"Chunks sharing message_id … display incrementally and finalize when is_final=true"）——客户端把每个 chunk 追加。
- 但 `response.audio_transcript.text` 是 **revision 语义**：每次给出"已确认 `text`" + "临时 `stash`"，`text` **单调增长**，`stash` 会被后续事件覆盖修正，最终以 `.done` 的 `transcript` 为准。
- **解决方案（在 provider 内转换）**：维护 `self._confirmed`，每次仅把新增的已确认部分 `text[len(prev):]` 作为 `OmniEvent.text`(delta) 上抛；`stash` 不参与增量流（避免客户端追加临时文本导致重复/回退）。`.done` 时把 `transcript` 与 `_confirmed` 的差值作为尾部 delta，再由 orchestrator 发 `ai_text(final)`。这样 orchestrator 的 `_full_text += ev.text` 与客户端 append 语义都成立。

```python
# provider 内，处理 response.audio_transcript.text
new_confirmed = msg.get("text", "")
if new_confirmed.startswith(self._confirmed) and len(new_confirmed) > len(self._confirmed):
    delta = new_confirmed[len(self._confirmed):]
    self._confirmed = new_confirmed
    emit(OmniEvent(TEXT_DELTA, text=delta, response_id=rid))
# 处理 response.audio_transcript.done
transcript = msg.get("transcript", "")
if len(transcript) > len(self._confirmed):
    emit(OmniEvent(TEXT_DELTA, text=transcript[len(self._confirmed):], response_id=rid))
self._confirmed = ""  # reset for next turn (on response.created)
```

> 权衡：牺牲一点"临时预测文本(stash)"的即时展示，换取与既有协议/客户端的完全兼容。第二阶段若要展示 stash，需要 `transcription` 那样的 revisable 机制（协议改动）。

### 4.2 源语言原文用 `transcription`（可 revise），译文用 `ai_text`（append）

- 源文事件 `conversation.item.input_audio_transcription.text` 也带 `text`+`stash`，但目标是 `transcription` 消息——协议允许 interim 被 revise，故这里**可直接把 `text+stash` 作为 interim** 上抛（每次覆盖），`completed` 时发 final。语义对齐，无需 delta 转换。

### 4.3 输出音频 24kHz，需专用 Opus 编码器 ★重要

- LiveTranslate **输出音频固定 `pcm24`(24000Hz)**，而输入是 16000Hz。
- 现状：`gateway/endpoint.py` 用 `cfg.media.audio.default_sample_rate`(=16000) 同时创建输入解码器与输出编码器；`OmniOrchestrator` 用该 `opus_enc` 编码 `AUDIO_DELTA`，并用 `AudioFrameBuffer(opus_enc.frame_size)` 切帧。
- 若用 16kHz 编码器编码 24kHz PCM：`frame_size=320` 会把 24kHz 的 480 样本/20ms 错切成 320 样本/帧，被当作 16kHz 20ms 编码 → **音调偏高、语速变快、时长计费漂移**。
- **解决方案**：为同传路径的**输出**单独创建 24kHz Opus 编码器（`frame_size=480`）。`libopus` 支持 24000Hz；客户端 `OpusDecoder`（16kHz 构造）会在内部（Opus 恒定 48kHz 内核）**自动重采样**到播放率，`duration_ms=20` 保持正确，故**客户端无需改动**。
- 落地方式（择一）：
  1. `endpoint.py` 在构建 `OmniOrchestrator` 时，对 livetranslate provider 使用 `output_sample_rate` 单独建输出编码器，注入 orchestrator 的 `opus_encoder`（输入解码器保持 16kHz）。
  2. `OmniProviderConfig.output_sample_rate` 驱动上述编码器采样率。
- **附带 TODO**：核对现有 `alibaba_omni`（`qwen3.5-omni`）输出采样率是否也是 24kHz；若是，说明既有 omni 路径也存在同一隐患，应一并修正（本设计的 24kHz 输出编码器方案可直接复用）。

### 4.4 无 `response.cancel` / barge-in 语义弱化

- LiveTranslate 客户端事件**仅** `session.update / input_audio_buffer.append / input_image_buffer.append / session.finish`，**不支持** `response.cancel`、`input_audio_buffer.clear`。
- 影响 `OmniOrchestrator._do_interrupt` 会调用 `stream.cancel_response()`：
  - Provider 的 `cancel_response()` 实现为 **no-op**（或记录日志），不发不支持的事件。
  - 客户端 `interrupt` 消息仍生效：本地 `interrupt.interrupt()` 提升 epoch → 丢弃当前 turn 后续 `TEXT_DELTA/AUDIO_DELTA`（`should_yield` 拦截）+ 重置 audio buffer + 回 `listening`。译文播放可被"软打断"，只是无法通知服务端停止生成（服务端会自然生成完当前句）。
- `SPEECH_STARTED`（服务端 VAD barge-in 自动打断）：LiveTranslate 交互流程未列出该事件；同传本身是"被动听译"而非对话，无需自动打断。若模型实际发送则按 omni 逻辑处理（best-effort），否则 turn 边界由 `response.created` 提供，`OmniOrchestrator` 已支持。

### 4.5 连续多轮 / 会话生命周期

- 与 omni 一致：一条连接内服务端按 VAD 逐句切分，每句一个 `response.created … response.done`，`OmniOrchestrator` 依 `response_id` 处理 turn 边界。会话在客户端 `session.end` 或断线时才 `session.finish`。
- 断线快照 `snapshot_on_disconnect` 复用；同传的"历史"意义不大，但沿用不影响正确性（可后续精简）。

### 4.6 计费 / 成本提示

- `enable_source_transcription=true` 会额外产生源文识别的输出文本 token 计费。若产品只需译文，可关闭以省成本。
- 图像输入按像素计费（32×32=0.5 token），默认建议关闭或限制 ≤2fps。

### 4.7 语种校验（可选）

`shared/config/validation.py` 可新增：当 `omni.active` 为 livetranslate 时，校验 `source_language`/`target_language` ∈ 支持语种集合，且 `["text","audio"]` 模态下 `target_language` 属于 29 种"音频+文本"语种（否则仅 `["text"]`）。失败快速抛 `ConfigError`。

---

## 5. 配置示例 (`configs/dev/server.yaml`)

```yaml
# 开启 omni 路径并选用同传 provider
omni:
  enabled: true
  active: alibaba_livetranslate
  fallback: []
  system_prompt_path: ../configs/dev/prompts/companion.txt   # 同传不使用，占位
  generation: { }
  providers:
    alibaba_livetranslate:
      enabled: true
      base_url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3.5-livetranslate-flash-realtime
      api_key: "${DASHSCOPE_API_KEY}"
      model: qwen3.5-livetranslate-flash-realtime
      voice: Tina                         # 目标语音音色
      supports_vision: true
      modalities: [text, audio]
      # ---- 同传配置 ----
      source_language: zh                 # 源语种
      target_language: en                 # 目标语种
      enable_source_transcription: true   # 回传源语言原文 -> transcription
      source_transcription_model: qwen3-asr-flash-realtime
      input_sample_rate: 16000
      output_sample_rate: 24000           # 模型输出固定 24kHz
      corpus_phrases:                     # 可选热词
        人工智能: Artificial Intelligence
      enable_voice_clone: false
      # voice_clone_frequency: once       # 启用时 voice 需设为 default
    # 仍可保留 alibaba_omni 作为对话模式 provider（切换 active 即可）
    alibaba_omni:
      enabled: true
      base_url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3.5-omni-flash-realtime
      api_key: "${DASHSCOPE_API_KEY}"
      model: qwen3.5-omni-flash-realtime
      voice: Tina
      supports_vision: true
      modalities: [text, audio]
```

切换语向 = 改 `source_language`/`target_language` 后重启服务（第一阶段为实例级）。

---

## 6. 端到端数据流 (End-to-End Flow)

```
客户端说话(源语言)
  │ Opus 20ms 帧
  ▼
gateway: 解码 Opus→PCM16(16k) ──► OmniOrchestrator._handle_audio
  │                                    │ feed_audio(pcm)
  ▼                                    ▼
                         LiveTranslateStream ──► input_audio_buffer.append
                                                        │ (服务端自动 VAD 分句)
        ┌───────────────────────────────────────────────┤
        ▼ conversation.item.input_audio_transcription.*   │
  transcription(原文, interim→final) ◄── INPUT_TRANSCRIPTION_*
        ▼ response.created                                │
  status: thinking ◄── RESPONSE_STARTED                   │
        ▼ response.audio_transcript.text/.done            │
  ai_text(译文, 增量→final) ◄── TEXT_DELTA(confirmed delta)│
        ▼ response.audio.delta (PCM24)                    │
  ai_audio(译文语音) ◄── AUDIO_DELTA ──► AudioFrameBuffer(480)+Opus@24k
        ▼ response.done                                   │
  status: listening ◄── RESPONSE_DONE ──────────────────┘
```

---

## 7. 实施步骤 (Implementation Plan)

1. **配置层**：`OmniProviderConfig` 增补同传字段（§3.1）；`configs/dev/server.yaml` 增加 `alibaba_livetranslate`（§5）；（可选）`validation.py` 语种校验（§4.7）。
2. **Provider**：新建 `omni/providers/alibaba_livetranslate.py`（复制 `alibaba_omni.py` 骨架，改 `session.update` 构造 + 事件映射 + §4.1 delta 转换 + `cancel_response` no-op）。
3. **Factory**：`omni/factory.py._create_provider` 注册 `alibaba_livetranslate`（§3.2）。
4. **输出采样率**：`gateway/endpoint.py` 为 livetranslate 路径按 `output_sample_rate` 建 24kHz 输出 Opus 编码器并注入 orchestrator（§4.3）。
5. **编排器**：验证 `OmniOrchestrator` 复用；如需，微调 `ctx.llm_provider` 记录与 interrupt 分支。
6. **测试**（§8）。
7. **文档**：更新 `docs/fd_server_omni_model.md` 交叉引用 + `REPO.md` 分阶段联调段落补充同传模式说明。

> 改动集中在 1 个新文件 + 3 个小改（config models / factory / endpoint），无协议、无客户端改动。

---

## 8. 测试策略 (Testing)

- **单元测试**（对齐 `services/tests/unit/omni/`）：
  - `test_factory`：`alibaba_livetranslate` 正确构造、未知 provider 报错。
  - Provider 事件映射：喂入伪造的 `response.audio_transcript.text/.done`、`input_audio_transcription.text/.completed`、`response.created/.done`、`error`，断言产出的 `OmniEvent` 序列（重点覆盖 §4.1 的 confirmed-delta 转换，含 `stash` 修订、`text` 回退保护）。
  - `session.update` payload：断言含 `translation.language`/`input_audio_transcription`，不含 `instructions`/`enable_search`。
  - `cancel_response` no-op 不抛异常、不发非法事件。
- **采样率**：断言 livetranslate 输出编码器 `frame_size==480`（24k×20ms），`_send_ai_audio(duration_ms=20)`。
- **集成/伪 provider**：用 `tests/unit/omni/fakes` 风格的假 WS，跑通 `OmniOrchestrator` 一整轮，断言 s2c 依次产出 `transcription`→`status(thinking)`→`ai_text`→`ai_audio`→`ai_text(final)`→`status(listening)`。
- **真机联调**：`omni.enabled=true`、`active=alibaba_livetranslate`，中文说话 → Android 端"用户"气泡显示中文原文，"AI"气泡显示英文译文并播放英文语音；核对译文音频语速/音调正常（验证 §4.3）。

---

## 9. 备选方案与第二阶段 (Alternatives & Phase 2)

- **备选：新建 `TranslateOrchestrator`** —— 逻辑与 `OmniOrchestrator` 高度重复，仅在需要与对话模式行为强分化时才考虑；本期不采纳。
- **第二阶段（需协议扩展）**：
  - 客户端运行时选择/切换源目标语种（`session.start` payload 增字段或新增控制消息）——需改 `packages/api-schema` 与客户端。
  - 展示"临时预测文本(stash)"，需要 `ai_text` 支持 revise（类似 `transcription` 的 interim 语义）。
  - 双向对话同传（两说话人各自母语互译）、声音复刻产品化、per-session 热词。
  - 抽取 `session/pipeline_common.py` 收敛 `OmniOrchestrator`/`DialogOrchestrator`/LiveTranslate 的共享 helper（承接 `omni_orchestrator.py` 顶部 TODO）。

---

## 10. 附录：与 alibaba_omni 的差异速查

| 维度 | alibaba_omni | alibaba_livetranslate |
|---|---|---|
| model | qwen3.5-omni-flash-realtime | qwen3.5-livetranslate-flash-realtime |
| session.update | instructions + enable_search + turn_detection(server_vad) | translation.language + input_audio_transcription + (无 instructions/search) |
| 源文事件 | `conversation.item.input_audio_transcription.*` | 同（需显式配 `input_audio_transcription.model`）|
| 回复文本增量 | `response.text.delta` / `response.audio_transcript.delta`（纯 delta） | `response.audio_transcript.text` / `response.text.text`（confirmed+stash 修订，需转 delta）|
| 回复文本终态 | `response.done` 内容 | `response.audio_transcript.done`.transcript / `response.text.done`.text |
| 输出音频 | (需核对) | 固定 pcm24 (24kHz) |
| 打断 | `response.cancel` + `input_audio_buffer.clear` 支持 | **不支持**（cancel no-op）|
| 客户端事件集 | update/append/image/cancel/clear/finish | update/audio.append/image.append/finish |
