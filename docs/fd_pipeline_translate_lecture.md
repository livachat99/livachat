# Pipeline Translate — Lecture Usecase (LiveTranslate 引擎) 设计

> 关联文档：[`fd_pipeline_translate_mode_v2.md`](fd_pipeline_translate_mode_v2.md)（动态切换基线）、
> [`fd_realtime_translation.md`](fd_realtime_translation.md)、[`fd_server_omni_model.md`](fd_server_omni_model.md)。

> **实现现状（2026-07-06 起，commit 30411b5）**：lecture 同传为 **text-only 输出**——
> `LectureTranslateEngine.open()` 以 `modalities=("text",)` 打开 provider，服务端不产出译音，
> 引擎内 24k OpusEncoder、`AUDIO_DELTA → ai_audio`、`SPEAKING` 迁移均为**保留未启用**。
> 下文涉及「译音出 / ai_audio / 24k Opus / SPEAKING 迁移」的段落即对应该休眠路径的设计意图
> （重新启用只需改回 `modalities=("text","audio")`，omni 路径当前即此配置）。

> **2026-10-10 迁移注（3.8-only，已实施）**：模型迁移至 `qwen3.8-livetranslate-flash-realtime`，
> 3.5 不再支持（见 [`qwen_livetranslate_realtime.md`](qwen_livetranslate_realtime.md)）——
> provider 的 `modalities` 入参改发 `session.output_modalities`；增量文本事件由 `.text` 改为
> `.delta`（译文直传、源文累计回全量再上抛）；`input_audio_transcription` 不再发送（3.8 ASR
> 常开、不可关、免费），`enable_source_transcription` / `source_transcription_model` 配置项已删除；
> 失败语义改为**仅报错不降级**（流终结/喂帧失败 → `TRANSLATE_UNAVAILABLE` 一次，不关引擎、
> 不回落逐轮；原 D3-b 降级已移除）；mute 收尾采用 **`vad_state="muted"` 端到端透传**——客户端
> mute 补发帧标 muted，服务端替换为 20ms raw 全零 PCM 喂引擎（lecture 分支 5 帧/条，
> omni/dialogue 1:1；`orchestrator._resolve_audio_pcm`，见 [`android_audio_capture.md`](android_audio_capture.md) §5.2）。
> （2026-10-10 过程记录：中途曾以「服务端断流守护」达成，随 muted 透传（§5.2）实施已一并移除——无标记断流不再单独兜底。）

> **2026-10-27 起（已实施）**：lecture 不再写 `WorkingMemory` / `chat_store`，只保留
> `chat/translate.jsonl`（`source="lecture"`）；详见 §8.1。

## 1. 背景与目标

当前三段式管线（ASR → LLM → TTS，即 `DialogOrchestrator`）已支持运行时通过
`session.change(mode=translate)` 切换到翻译模式，翻译由 **LLM prompt** 完成，属于
**逐轮（turn-based）** 翻译：说话人停顿 → ASR final → LLM 翻译 → TTS 播报。

新需求要求在**同一个三段式管线**内区分两类翻译场景：

| 场景 | 触发 | 翻译方案 | 特征 |
|------|------|---------|------|
| **对话翻译（默认）** | `session.change(mode=translate)` | **保持现状**：LLM prompt 逐轮翻译 | 一问一答、需停顿断句 |
| **演讲/同传翻译** | `session.change(mode=translate, usecase=lecture)` | **alibaba_livetranslate** 流式同传 | 说话人连续讲话、边说边译、低延迟 |

`alibaba_livetranslate` 当时仅在 **omni 路径**（`OmniOrchestrator` + `omni.enabled=true`）中使用；
本设计把该 provider 作为**可插拔的同传引擎**接入三段式管线，供 lecture 场景使用。
> 2026-09-14 更新：omni 路径的 livetranslate 配置已整体移除（见
> [`fd_realtime_translation.md`](fd_realtime_translation.md) 顶部注记）——本 pipeline lecture
> 路径是该 provider 的唯一使用方。

### 目标
- `session.change` 新增 `usecase` 字段；`usecase=lecture` 时在 pipeline 模式下启用 LiveTranslate 同传。
- 复用（**不修改**）现有 `LiveTranslateProvider`，仅以 import 方式消费。
- chat / translate(对话) / translate(lecture) 三态可在会话内任意动态切换。

### 非目标（Out of Scope）
- **不改动任何 omni 相关实现**：`services/omni/**`（含 `alibaba_livetranslate.py`、`factory.py`、
  `service.py`、`base.py`）与 `services/session/omni_orchestrator.py` 全部**只读复用**，零改动。
  > 2026-09-14 修订：`alibaba_livetranslate.py` 一处例外——P2 接百炼业务空间时
  > `LiveTranslateProvider` 增加可选参数 `workspace_id`（仅驱动 `X-DashScope-WorkSpace` 头；
  > 域名与头"两值配套、互不派生"，与 asr/tts 同一约定；缺省 None 行为零变化，
  > `open` 与 `_reconnect` 同源用头）。其余 omni 文件与 `omni_orchestrator.py` 仍零改动。
- 不改动对话翻译（默认 translate）的既有 LLM prompt 逻辑与行为。
- 客户端（iOS/Android/Web）改动仅限于「发送 `usecase` 字段」，属独立任务，本文仅约定协议。

---

## 2. 现状回顾

### 2.1 三段式对话翻译（保持不变）
`DialogOrchestrator._start_thinking()` 中，`_pipeline_mode == "translate"` 时用
`_build_translate_instruction()` 构造语言感知 prompt，走 LLM 流式输出 + TTS。
输入侧仍由 ASR（`_poll_asr_results` / `_handle_audio`）+ `TurnDetector` 逐轮驱动。

### 2.2 LiveTranslate Provider（复用对象）
`services/omni/providers/alibaba_livetranslate.py::LiveTranslateProvider` 实现 `OmniProvider` 协议：
- `open(*, sample_rate, voice, modalities, ...) -> OmniStream`：建立 WSS，发送 `session.update`
  （`translation.language` = 目标语言、`output_modalities` = 输出模态；3.8 ASR 常开，不再发
  `input_audio_transcription`，源语种自动识别）。
- 返回的 `_RealtimeTranslateStream` 实现 `OmniStream`：
  `feed_audio(pcm)` / `events()` / `cancel_response()`（no-op）/ `reset()` / `close()`。
- 事件流 `OmniEvent`（`omni/base.py`）：`INPUT_TRANSCRIPTION_DELTA/DONE`、`RESPONSE_STARTED`、
  `TEXT_DELTA`、`AUDIO_DELTA`、`RESPONSE_DONE`、`SPEECH_STARTED/STOPPED`、`ERROR`。
- **服务端自带 VAD / 断句**，客户端持续喂音频即可，无需 `commit_user_turn`。
- **输出固定 24kHz PCM**（配置 `output_sample_rate: 24000`）——但当前 lecture 以 text-only 打开、不消费音频输出（见文首现状注）；输入采样率由 `input_sample_rate`（16k）决定。

### 2.3 OmniOrchestrator 事件泵（作为实现参考，不复用其代码）
`OmniOrchestrator._event_pump()` 展示了如何把 `OmniEvent` 映射为
`transcription` / `ai_text` / `ai_audio` 帧与状态机迁移。本设计的 `LectureTranslateEngine`
将**沿用相同映射逻辑**（受 §1 非目标限制，无法直接调用 `OmniOrchestrator`，故独立实现）。
> 备注：`omni_orchestrator.py` 顶部 `TODO(phase2)` 已注明 send helper 是有意从
> `DialogOrchestrator` 复制的，本设计延续该「先复制、后期再抽公共层」策略。

---

## 3. 方案总览与关键决策

**核心思路**：在 `DialogOrchestrator` 内新增一个**可选的同传子引擎**
`LectureTranslateEngine`。当会话切到 `translate + lecture` 时：
关闭/暂停 ASR·LLM·TTS 逐轮链路，改由该引擎直接消费解码后的 PCM 音频，通过一条
LiveTranslate WSS 流完成「音频进 → 译文（+ 译音，当前未启用，见文首现状注）」，事件泵复用与 omni 一致的帧映射。

```
                    session.change 路由
                          │
        ┌─────────────────┼───────────────────────────┐
        ▼                 ▼                           ▼
   mode=chat        mode=translate               mode=translate
                    usecase≠lecture              usecase=lecture
        │                 │                           │
   三段式对话        三段式 + LLM 翻译 prompt      LectureTranslateEngine
  (ASR→LLM→TTS)      (现状, 逐轮)                 (LiveTranslate 同传流)
```

### 关键设计决策

| # | 决策点 | 方案 | 理由 |
|---|--------|------|------|
| D1 | 复用 vs 复制 omni 编排 | 新建 `LectureTranslateEngine`，**import 复用** `LiveTranslateProvider` / `OmniEvent`，事件泵逻辑复制自 `OmniOrchestrator` | 满足「不改 omni」约束；避免污染稳定的三段式代码 |
| D2 | provider 实例化时机 | **session.change 时按需 open**，用动态 `my_language/local_language` 覆盖目标/源语言 | 目标语言随用户偏好变化，不能在启动时固定 |
| D3 | 输出采样率 | 引擎**自带 24kHz OpusEncoder**（`frame_size=480`），不复用会话 16k 编码器〔text-only 定调后未启用，见文首现状注〕 | 与 omni 路径一致；客户端 Opus 解码自适应采样率；免重采样开销 |
| D4 | 输入音频 | 复用 `DialogOrchestrator` 已解码的 16k PCM 直接 `feed_audio`，provider `input_sample_rate=16000` 匹配 | 零额外重采样 |
| D5 | 配置来源 | 新增顶层 `translate` 配置块（与 asr/llm/tts/omni 平级，不读 omni.providers） | 语义解耦，pipeline 与 omni 配置互不依赖 |
| D6 | open 失败降级 | 回退到 §2.1 的 LLM 翻译方案（usecase 降级为 dialogue）+ 发 error 帧 | 保证「翻译不中断」，优雅降级 |
| D7 | lecture 下的 barge-in | 关闭三段式的服务端 VAD 打断（连续喂音频），保留手动 interrupt 停播 | 演讲为单向连续输入，不应被 VAD 打断 |

---

## 4. 协议变更

### 4.1 `session.change` 新增 `usecase`（C2S）
`packages/api-schema/c2s/session-change.json` — payload 新增字段：

```json
"usecase": {
  "type": "string",
  "enum": ["dialogue", "lecture"],
  "default": "dialogue",
  "description": "Translation usecase. Only meaningful when mode=translate. 'dialogue' (default) = turn-based LLM translation; 'lecture' = streaming simultaneous interpretation (LiveTranslate)."
}
```

- `usecase` 可选；缺省 = `dialogue`。
- `mode=chat` 时忽略 `usecase`。
- `mode=translate` 且 `usecase=lecture` → 启用 LiveTranslate 引擎。

### 4.2 `session.changed` 回显 `usecase`（S2C）
`packages/api-schema/s2c/session-changed.json` — payload 同样新增 `usecase`（enum 同上），
用于客户端确认当前生效的场景。

### 4.3 DTO 重新生成
Schema 为单一事实来源，改后需重新生成（禁止手改生成物）：

```bash
make generate-android-api          # Kotlin DTO
python scripts/generate_python_api.py   # Python DTO（或对应 make 目标）
make check-android-api-generated   # CI 一致性校验
```

受影响的生成物（自动更新，无需手写）：

| 文件 | 变更 |
|------|------|
| `packages/sdk-python/.../protocol_models.py` | `ClientSessionChangePayload` / `ServerSessionChangedPayload` 增 `usecase` 字段 + 新 `Usecase` StrEnum |
| `packages/sdk-python/.../client_message.py`、`server_message.py` | 随之更新 |
| `packages/sdk-kotlin/.../ClientMessage.kt`、`ServerMessage.kt` | `usecase` 字段 |

> `usecase` 为**可选带默认值**字段，旧客户端不发送时按 `dialogue` 处理 → **向后兼容**。

### 4.4 codec 构造器
`services/gateway/codec.py::WireCodec.make_session_changed()` 增加 `usecase` 形参并写入 payload：

```python
def make_session_changed(self, *, mode, my_language=None, local_language=None, usecase=None) -> str:
    return self.build_typed(
        ServerSessionChangedPayload(
            mode=mode, my_language=my_language,
            local_language=local_language, usecase=usecase,
        ),
        msg_cls=ServerSessionChanged,
    )
```

---

## 5. 配置变更

`services/shared/config/models.py` — 新增顶层 `TranslateConfig`（新增 model，**不动 omni 配置**）：

```python
class TranslateConfig(BaseModel):
    """三段式 lecture 同传引擎（alibaba_livetranslate）配置。

    顶层配置段，与 asr / llm / tts / omni 平级。
    当前仅支持 lecture 场景，未来可扩展 dialogue 同传 provider。
    """
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str | None = None
    # 源/目标语言为默认值；运行时由 session.change 的 local/my_language 覆盖
    source_language: str = "en"
    target_language: str = "en"
    corpus_phrases: dict[str, str] | None = None
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    enable_voice_clone: bool = False
    voice_clone_frequency: str | None = None


class AppConfig(BaseSettings):
    ...
    translate: TranslateConfig = TranslateConfig()
```

**设计理由**：`translate` 作为独立顶层段而非 `llm.lecture_translate`：
- 同传替代的是 LLM 翻译 + TTS **两段**，不只是 LLM 配置的子项。
- 顶层配置遵循 asr/llm/tts/omni 的既有惯例——每个可替换的能力一个顶层段。
- 未来扩展第二个同传 provider 时，直接在 `translate` 下加 `active/fallback/providers` 即可。

`configs/dev/server.yaml` — 新增顶层 `translate:` 段（与 `llm:` / `asr:` / `tts:` 平级）：

```yaml
# ---------------- Live Translate (pipeline 模式) ----------------
# session.change { mode: "translate", usecase: "lecture" } 时使用
translate:
  enabled: true
  base_url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-livetranslate-flash-realtime
  api_key: "${DASHSCOPE_API_KEY}"
  model: qwen3.8-livetranslate-flash-realtime
  voice: Tina
  input_sample_rate: 16000
  output_sample_rate: 24000
  corpus_phrases:
    人工智能: Artificial Intelligence
  enable_voice_clone: false
```

启动装配（`services/app/application.py::lifespan`）：在 pipeline 分支（`else` / omni 未启用）中，
若 `cfg.translate.enabled`，把该配置存入 `app.state.translate_cfg`，供 endpoint 注入。omni 分支完全不动。

---

## 6. 详细设计

### 6.1 语言方向映射（lecture）
LiveTranslate 只有单一 `translation.language`（目标语言）+ 源语言 ASR。lecture 语义是
「把演讲者的语言译成我能听懂的语言」，故：

- **目标语言（译文）** = `my_language`（听众母语），缺省 `zh`。
- **源语言（ASR 识别）** = `local_language`（演讲现场语言），缺省 `en`；亦可留空由 provider 自动检测。

构造 `LiveTranslateProvider(source_language=local_language, target_language=my_language, ...)`。

### 6.2 新增模块 `services/session/lecture_translate.py`

```python
class LectureTranslateEngine:
    """三段式管线内的 LiveTranslate 同传子引擎。

    生命周期由 DialogOrchestrator 驱动：切入 lecture 时 open()，切出/结束时 close()。
    仅 import 复用 omni 的 provider 与事件类型，不改动 omni 代码。

    引擎自行持有 handler + codec 构造协议帧，不依赖 orchestrator 的内部方法。
    """

    def __init__(
        self,
        *,
        handler: Any,               # ConnectionHandler（发帧 + 队列操作）
        codec: Any,                 # WireCodec（构造协议消息）
        ctx: SessionContext,
        interrupt: InterruptController,
        cfg: TranslateConfig,
        recording_sink: Any = None,
    ):
        self._handler = handler
        self._codec = codec
        self._ctx = ctx
        self._interrupt = interrupt
        self._cfg = cfg
        self._recording_sink = recording_sink

        # 自带 24k OpusEncoder + AudioFrameBuffer
        self._opus_enc = create_opus_encoder(sample_rate=cfg.output_sample_rate)  # 24000 Hz
        self._audio_out_buf = AudioFrameBuffer(self._opus_enc.frame_size)

        # 运行时状态
        self._stream: OmniStream | None = None
        self._event_pump_task: asyncio.Task[None] | None = None
        self._response_epoch: int = 0

        # Per-response 状态
        self._message_id: str = ""
        self._transcription_id: str = ""
        self._full_text: str = ""
        self._text_index: int = 0
        self._audio_index: int = 0

    async def open(self, *, my_language: str, local_language: str) -> None:
        # 1. 用 cfg + 动态语言构造 LiveTranslateProvider
        # 2. provider.open(sample_rate=cfg.input_sample_rate, modalities=("text",))
        #    —— text-only（30411b5 定调）；音频输出路径保留未启用，见文首现状注
        # 3. 启动 _event_pump 后台任务
        # 4. ctx.asr_provider = ctx.llm_provider = ctx.tts_provider = "alibaba_livetranslate"

    async def feed_pcm(self, pcm: bytes) -> None:
        # 直接喂 16k PCM；异常 → close + 抛 ProviderError（由 orchestrator 降级）

    async def interrupt(self, reason: str) -> None:
        # cancel_response()(no-op) + 重置 audio buffer + 发 status(listening, interrupted)

    async def close(self) -> None:
        # 取消事件泵 + provider.close()

    # ── 帧构造 helper（自给自足，不依赖 orchestrator）──

    async def _send_transcription(self, text: str, *, is_final: bool) -> None: ...
    async def _send_ai_text(self, text: str, *, is_final: bool) -> None: ...
    async def _send_ai_audio(self, opus_frame: bytes) -> None: ...
    async def _send_status(self, status: str, reason: str) -> None: ...

    async def _event_pump(self) -> None:
        # 复制自 OmniOrchestrator._event_pump 的 OmniEvent → 帧映射（见 §6.4）
```

**设计理由**：引擎自行持有 `handler` + `codec` 并实现 send helper，而非接收 orchestrator 的方法引用：
- **显式接口**：`handler` 和 `codec` 是项目中已稳定的公共类型，不是 orchestrator 的私有方法签名。
- **解耦**：orchestrator 的 `_send_transcription` 等方法签名可能因自身需求演变（如增加 `transcription_id` 参数），
  引擎不应依赖这些隐式契约。
- **可测试**：单元测试直接 mock `handler` + `codec`，无需构造一个完整的 `DialogOrchestrator` 实例。
- **可接受重复**：send helper 与 `OmniOrchestrator` 已有的复制模式一致
  （`omni_orchestrator.py` 顶部 `TODO(phase2)` 已注明后续可抽公共层）。

### 6.3 `DialogOrchestrator` 改动点

| 位置 | 改动 |
|------|------|
| `__init__` | 新增 `self._translate_cfg`、`self._lecture_engine: LectureTranslateEngine \| None = None`、`self._lecture_active = False` |
| `_handle_session_start` | 已有「重置为 chat」逻辑基础上，追加 `self._lecture_active = False`；若曾有引擎则 close |
| `_handle_session_change` | **核心**：解析 `mode` + `usecase`，驱动切入/切出 lecture（见 §6.5） |
| `_handle_audio` | 顶部分支：`if self._lecture_active: pcm=_decode_opus(...); await self._lecture_engine.feed_pcm(pcm); return`（不走 ASR/TurnDetector/state 门控） |
| `_handle_interrupt` | `if self._lecture_active: await self._lecture_engine.interrupt(reason); return` |
| `_handle_video` | lecture 下暂不喂图（保持 ai_video_ack 逻辑）；可作为后续增强 |
| `run()` finally / `_handle_session_end` | 追加 `if self._lecture_engine: await self._lecture_engine.close()` |

依赖注入（`endpoint.py`）：在构造 `DialogOrchestrator` 时传入
`translate_cfg=getattr(ws.app.state, "translate_cfg", None)`。**endpoint 其余逻辑不变**，
omni 分支完全不动。

### 6.4 事件 → 帧映射（与 omni 一致）

| OmniEvent | 处理 |
|-----------|------|
| `INPUT_TRANSCRIPTION_DELTA` | `transcription(is_final=false)`（源语言原文，interim） |
| `INPUT_TRANSCRIPTION_DONE` | `transcription(is_final=true)` + recording `translate.jsonl`（`source="lecture"`）；**不写 history/chat_store** |
| `RESPONSE_STARTED` | `sm→THINKING`；`status(thinking, asr_final)`；重置 message/index |
| `TEXT_DELTA` | `ai_text(is_final=false)`（译文增量） |
| `AUDIO_DELTA` | 首帧 `sm→SPEAKING` + `status(speaking, tts_start)`；PCM(24k)→buffer→24k Opus→`ai_audio`〔text-only 下不会触发，见文首现状注〕 |
| `RESPONSE_DONE` | flush 尾音；`ai_text(is_final=true)`；`sm→LISTENING`；`status(listening, tts_finish)`；recording `translate.jsonl`（`source="lecture"`）；**不写 history/chat_store** |
| `SPEECH_STARTED` | lecture 下忽略（不 barge-in，见 D7） |
| `ERROR` | `error(code=1007/1008)` 帧；连续错误触发降级（§7） |

`interrupt` 语义（epoch）：引擎内部持有 `_response_epoch`，与 `InterruptController.should_yield`
配合，手动打断后丢弃过期 response 的 TEXT/AUDIO（复用 omni 的做法）。

### 6.5 切换时序（`_handle_session_change`）

```
收到 session.change:
  校验 mode ∈ {chat, translate}，否则 error(4001)
  校验 ctx 已 start，否则 error(4003)

  target_lecture = (mode == "translate" and usecase == "lecture")

  ── 切入 lecture（!_lecture_active and target_lecture）──
     await _cancel_pipeline(); await _cancel_asr_poll()
     关闭并置空 _asr_stream / _tts_stream（释放 provider 连接，切回时惰性重开）
     _lecture_engine = LectureTranslateEngine(handler, codec, ctx, interrupt, cfg=translate_cfg, recording_sink=...)
     try: await _lecture_engine.open(my_language, local_language)
          _lecture_active = True; _pipeline_mode = "translate"
     except ProviderError:  # D6 降级
          _lecture_engine = None; _lecture_active = False
          _pipeline_mode = "translate"; usecase 视为 dialogue
          发 error(1008, "lecture translate unavailable, fell back to LLM translate")

  ── 切出 lecture（_lecture_active and !target_lecture）──
     await _lecture_engine.close(); _lecture_engine = None; _lecture_active = False
     设置 _pipeline_mode = "full"(chat) 或 "translate"(dialogue)
     （ASR/TTS 于下一帧音频惰性重开——复用 _handle_audio 现有逻辑）

  ── 其余（chat↔dialogue-translate）──
     沿用 v2 既有逻辑（_pipeline_mode = full/translate；设置 my/local_language）

  持久化 mode_change 事件（含 usecase）
  发 session.changed(mode, my_language, local_language, usecase)
  发 status(state, "mode_changed")
```

### 6.6 状态机
沿用 `SessionState` 现有合法迁移（§state_machine），lecture 事件泵产生的
`LISTENING→THINKING→SPEAKING→LISTENING` 均为合法迁移，无需扩展状态机
（text-only 下 lecture 实际只走 `LISTENING→THINKING→LISTENING`，`SPEAKING` 段未启用，见文首现状注）。
切入 lecture 时若正处于 SPEAKING/THINKING，先 `_cancel_pipeline` 并复位到 LISTENING。

### 6.7 采样率处理
- 输入：会话协商采样率（16k）→ `_opus_dec` 解码 → 直接喂 provider（`input_sample_rate=16000`）。
- 输出：provider 出 24k PCM → 引擎自带 **24k OpusEncoder**（`frame_size=480`）编码 → `ai_audio(codec=opus)`。
  客户端 Opus 解码自适应 24k（与 omni 路径行为一致，已验证）。〔该路径在 text-only 下未启用，见文首现状注〕
- 备选方案（若客户端强约束单一采样率）：用 `media.resample.Resampler(24000→16000)` 降采样后复用会话 16k 编码器。

---

## 7. 异常处理与降级

| 场景 | 行为 |
|------|------|
| `session.change` mode 非法 | `error(code=4001)`，不改状态 |
| `session.change` 早于 `session.start` | `error(code=4003)` |
| `translate+lecture` 但 `translate.enabled=false` 或未配置 | 记 warning，**降级**为 dialogue LLM 翻译；`session.changed(usecase=dialogue)` |
| LiveTranslate open 失败 | 降级为 dialogue LLM 翻译 + `error(1008)`（D6） |
| 运行中 `feed_audio`/事件泵异常 | 引擎 close，发 `error(1008)`；自动降级到 dialogue（下一帧走 ASR） |
| lecture 期间收到手动 interrupt | 引擎停播 + `status(listening, interrupted)` |
| 断线重连快照 | lecture 不写 history，因此不会新增到任何基于 `_history` 的快照；重连后默认 chat（session.start 重置） |

> 建议在 `ErrorCode` 新增 `TRANSLATE_UNAVAILABLE = 1008`（可选；否则复用 `1007`）。这是对
> `shared/utils/errors.py` 的**追加**，非 omni 改动。若 `s2c/error.json` 枚举了 code，需同步。

---

## 8. 持久化与可观测性
- provider 归一命名 `alibaba_livetranslate`；`ctx.asr_provider/llm_provider/tts_provider` 均置为该名。
- **不写 `WorkingMemory` / `chat_store`**（2026-10-27 起，见 §8.1）。
- 录制：user/assistant 均写 `chat/translate.jsonl`，`source="lecture"`；这是 lecture 的服务端持久化事实源。
- 日志 logger：`livachat.orchestrator` + 复用 provider 内 `livachat.omni.livetranslate.messages`（采样打印）。
- 建议新增指标：`lecture_translate_open_total{result}`、`lecture_translate_active_sessions`（gauge）。

### 8.1 history / chat_store：不写入（2026-10-27）

术语：本文的 **history** 指 `WorkingMemory` / `orchestrator._history`，不是
`chat/history.jsonl`；**chat_store** 指 `chat_server.db` 的 `messages` 表。lecture 的录制
文件是 `chat/translate.jsonl`（`source="lecture"`），与这两者不同。

行为：

- `LectureTranslateEngine` **不接收** `history` / `chat_store` 构造参数；
- `INPUT_TRANSCRIPTION_DONE`：只发 `transcription(is_final=true)` + 录制 `translate.jsonl`；
- `RESPONSE_DONE`：只发 `ai_text(final)` / 状态帧 + 录制 `translate.jsonl`；
- 不新增 `WorkingMemory` 条目，不写 `chat_store.messages`；
- recording 关闭时没有 `translate.jsonl` 持久化，但仍不影响“不写 history/chat_store”。

理由：lecture 是翻译/字幕流，不是 chat 对话；lecture 原文/译文不应进入后续 chat 的 LLM
上下文或 chat_store restore。dialogue translate 与 transcribe 本来就不写；chat/omni 仍按
原语义写入，不在本变更范围。

模式切换与恢复边界：

- 进入 lecture **不清空**已有 history；lecture 只停止追加。lecture 之前已有的 history
  （可能来自 chat、omni 或部署前的旧版 lecture）原样保留；切回 chat 后仍是这些旧内容。
- 新版 lecture 产生的原文/译文不会出现在切回 chat 的 history/restore 中。
- resume/reconnect 只从 `chat_store` restore；新版 lecture 不新增行，因此不会恢复新版
  lecture 内容。**部署前已经写入的旧 lecture 行仍可能被 restore**，直到按天 prune。

旧数据边界：

- `messages` 表只有 `device_uuid/session_id/kind/role/content/images/ref_id/ts_ms`，没有
  `source` / `usecase`，无法可靠识别“这是 lecture 行”；
- 因此不新增 migration、不回填、不删除旧 lecture 行；旧行按现有按天 prune 自然淘汰；
- 验收必须使用部署后新建的 lecture 会话（或换日）判断“不再写入”，不能用旧行做回归结论。

非 lecture 引擎的写入：

- agent job 结果注入仍走 `record_agent_job_result`，会写 history/chat_store；它不属于
  lecture 翻译输出，本变更不处理；
- 若产品要求 lecture 期间“任何来源都不写”，需要给 `record_agent_job_result` 等入口加
  lecture 模式门控，另行 review。

---

## 9. 消息流对比

### 9.1 切入 lecture 同传
```
Client                                  Server
  |── session.change ────────────────────>|
  |   {mode:"translate",                   |
  |    usecase:"lecture",                  | cancel ASR/LLM/TTS 逐轮链
  |    my_language:"zh",                   | open LiveTranslate(src=en, tgt=zh)
  |    local_language:"en"}                | 启动事件泵
  |<── session.changed ────────────────────|  {mode:translate, usecase:lecture, ...}
  |<── status(listening, mode_changed) ────|
  |── audio(opus) ─────────────────────────>|  decode→feed_audio (连续)
  |── audio(opus) ─────────────────────────>|
  |<── transcription(interim, 源文) ────────|
  |<── status(thinking) ───────────────────|  RESPONSE_STARTED
  |<── ai_text(译文增量) ───────────────────|  TEXT_DELTA
  |<── status(speaking) ───────────────────|  首个 AUDIO_DELTA〔text-only 下无此三段〕
  |<── ai_audio(opus 24k, 译音) ────────────|
  |<── ai_text(final) / status(listening) ─|  RESPONSE_DONE
```

### 9.2 切回对话/普通翻译
```
Client                                  Server
  |── session.change {mode:"translate"} ──>|  close LiveTranslate 引擎
  |   (或 mode:"chat")                      |  _lecture_active=false
  |<── session.changed(usecase:dialogue) ──|  ASR/TTS 下一帧惰性重开
  |<── status(listening, mode_changed) ────|
```

---

## 10. 文件改动清单

| 文件 | 改动 | 触及 omni? |
|------|------|-----------|
| `packages/api-schema/c2s/session-change.json` | payload 增 `usecase` | 否 |
| `packages/api-schema/s2c/session-changed.json` | payload 增 `usecase` | 否 |
| `packages/sdk-python/.../*`、`packages/sdk-kotlin/.../*` | **重新生成** | 否 |
| `services/gateway/codec.py` | `make_session_changed` 增 `usecase` | 否 |
| `services/shared/config/models.py` | 新增 `TranslateConfig`；`AppConfig.translate` | 否 |
| `configs/dev/server.yaml` | 新增顶层 `translate:` 配置块 | 否 |
| `services/app/application.py` | 三段式分支读取并暴露 `translate_cfg` | 否 |
| `services/gateway/endpoint.py` | 向 `DialogOrchestrator` 注入 `translate_cfg`（omni 分支不动） | 否 |
| `services/session/lecture_translate.py` | **新增** `LectureTranslateEngine`（import 复用 omni provider/event） | 只读复用 |
| `services/session/orchestrator.py` | `__init__`/`_handle_session_start`/`_handle_session_change`/`_handle_audio`/`_handle_interrupt`/`run`/`_handle_session_end` 分支 | 否 |
| `services/shared/utils/errors.py` | （可选）新增 `TRANSLATE_UNAVAILABLE=1008` | 否 |

**零改动**：`services/omni/**`、`services/session/omni_orchestrator.py`。

---

## 11. 测试计划

**单元 / 集成**（新增 fake：可复用 `tests/unit/omni/fakes/fake_omni_provider.py` 思路，构造产生
`OmniEvent` 序列的假 stream 注入 `LectureTranslateEngine`）：
1. `session.change(translate, lecture)` → 收到 `session.changed(usecase=lecture)` + `status(mode_changed)`；ASR poll 被取消。
2. 喂音频 → 假 provider 依次发 `INPUT_TRANSCRIPTION_*`/`RESPONSE_STARTED`/`TEXT_DELTA`/`RESPONSE_DONE`
   → 断言收到 `transcription`/`ai_text`/状态迁移序列正确（事件泵仍保留 `AUDIO_DELTA→ai_audio` 分支，
   音频用例待重新启用音频输出时补齐，见文首现状注）。
3. lecture → `session.change(translate, dialogue)` → 引擎关闭；下一帧音频重新走 ASR/LLM/TTS。
4. lecture → `session.change(chat)` → `_pipeline_mode="full"`。
5. `session.start` 后默认 chat（即使上个会话是 lecture）。
6. 目标语言映射：`my=zh, local=en` → provider `target_language=zh, source_language=en`。
7. 降级：`translate.enabled=false` / open 抛 `ProviderError` → 回退 dialogue + `error` 帧。
8. lecture 期间手动 `interrupt` → 停播 + `status(listening, interrupted)`；`SPEECH_STARTED` 不触发 barge-in。
9. `usecase` 缺省（旧客户端不发）→ 按 dialogue 处理（兼容性）。
10. 采样率：断言 `ai_audio` 由 24k 编码器产出、帧长 20ms〔音频输出未启用，待启用时补齐〕。

**真机联调**：`services/scripts/test_bailian_translate_real.py`（provider 级真机探针：wav →
译文/源文事件统计；支持 `--base-url`/`--workspace-id` 组合矩阵），或用 e2e-backend 场景验证
lecture 全链路。

---

## 12. 兼容性、回滚与工作量

- **兼容性**：`usecase` 可选带默认；旧客户端与旧服务端行为不变。
- **回滚**：`translate.enabled=false` 即整体关闭（切 lecture 自动降级 dialogue）；
  代码层面 lecture 分支被 `_lecture_active` 门控，不影响既有 chat/translate 路径。
- **工作量拆解**：
  1. Schema + DTO 重新生成（S）
  2. 配置 model + yaml + lifespan/endpoint 注入（S）
  3. `LectureTranslateEngine` 实现（M，主要为事件泵移植）
  4. `DialogOrchestrator` 切换/分发/降级接线（M）
  5. 单测 + 集成 + 真机脚本（M）
