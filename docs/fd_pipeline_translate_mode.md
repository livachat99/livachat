# Pipeline Translate Mode — Design Document

## 1. Overview

在现有 `DialogOrchestrator` 的 chat mode 基础上，增加 **translate mode**（实时翻译模式），使服务端可以在对话/翻译两种模式之间切换。

### 1.1 两种模式对比

| | Chat Mode | Translate Mode |
|---|---|---|
| ASR 输出 | 识别文本 → LLM | 识别文本 + **语言标签** → 翻译方向判断 → LLM |
| LLM 系统提示 | companion prompt | translator prompt + **翻译方向指令** |
| LLM 任务 | 自由对话 | **严格翻译**：LLM 自行判断语言并翻译 |
| 语言检测 | 无 | **ASR provider 返回的 detected_language**，作为 hint 追加到 prompt；无则不追加 |
| TTS 输入 | LLM 完整回复 | LLM 翻译结果 |
| ai_text 内容 | LLM 完整回复 | 翻译后的文本 |
| transcription | ASR 原始识别文本 | ASR 原始识别文本（不变） |

### 1.2 分阶段实施

| 阶段 | 模式切换方式 | 协议变更 |
|------|------------|---------|
| Phase 1 | 配置文件 (`server.yaml`) | **无** — C2S/S2C 协议不变 |
| Phase 2 | `session.change` 消息动态切换 | 新增 C2S `session.change` |

---

## 2. Phase 1 — 配置驱动

### 2.1 配置变更

在 `server.yaml` 中增加 `pipeline` 节：

```yaml
# services/app/endpoint.py 当前通过环境变量 LIVACHAT_PIPELINE_MODE 控制
# asr_only / asr_llm / full，不新增配置节，沿用现有机制扩展。

# 方案：新增 LIVACHAT_PIPELINE_MODE=translate 取值
# 同时新增 translate 专用 system prompt 路径配置（已有 translator.txt）
```

**具体变更**：

1. `configs/dev/server.yaml` — 在 `llm` 节增加 `translate_prompt_path`：

```yaml
llm:
  active: alibaba_bailian
  fallback: []
  system_prompt_path: ../configs/dev/prompts/companion.txt
  translate_prompt_path: ../configs/dev/prompts/translator.txt   # 新增
  generation: { temperature: 0.8, max_tokens: 1024, top_p: 0.9 }
```

2. `services/shared/config/models.py` — `LlmConfig` 增加字段：

```python
class LlmConfig(BaseModel):
    # ... existing fields ...
    translate_prompt_path: str = "configs/dev/prompts/translator.txt"  # 新增
```

### 2.2 启动时加载 translate prompt

`services/app/application.py` — 在 lifepan 中加载 translate system prompt：

```python
# 现有代码加载 companion prompt:
system_prompt = load_system_prompt(cfg.llm.system_prompt_path)

# 新增：始终加载 translate prompt（供运行时切换使用）
translate_prompt = load_system_prompt(cfg.llm.translate_prompt_path)
app.state.translate_prompt = translate_prompt
```

`services/app/endpoint.py` — 传递 translate prompt 给 orchestrator：

```python
orchestrator = DialogOrchestrator(
    # ... existing params ...
    system_prompt=getattr(ws.app.state, "system_prompt", ""),
    translate_prompt=getattr(ws.app.state, "translate_prompt", ""),  # 新增
    pipeline_mode=os.environ.get("LIVACHAT_PIPELINE_MODE", "full"),
)
```

### 2.3 Orchestrator 变更

`services/session/orchestrator.py` — `DialogOrchestrator.__init__` 增加参数：

```python
class DialogOrchestrator:
    def __init__(
        self,
        *,
        # ... existing params ...
        system_prompt: str = "",
        translate_prompt: str = "",       # 新增
        generation_opts: dict[str, Any] | None = None,
        pipeline_mode: str = "full",
    ) -> None:
        # ...
        self._system_prompt = system_prompt
        self._translate_prompt = translate_prompt  # 新增
        self._pipeline_mode = pipeline_mode
```

### 2.4 语言检测 — ASR 提供 hint，LLM 自行判断

**不硬编码翻译方向**。LLM 接收统一指令自行判断源语言并翻译。ASR 检测到的语言仅作为可选 hint 追加到 prompt 末尾。

#### 2.4.1 扩展 AsrResult

`services/asr/base.py` — 在 `AsrResult` dataclass 中增加可选 `language` 字段：

```python
@dataclass
class AsrResult:
    text: str
    is_final: bool
    confidence: float | None = None
    transcription_id: str = ""
    language: str | None = None           # ASR 检测到的语言代码，作为 hint 使用
```

#### 2.4.2 各 Provider 提取语言

| Provider | 语言来源 | 提取位置 |
|----------|---------|---------|
| **Deepgram** | `channel.detected_language` | `_receive_loop` → `msg["channel"]["detected_language"]` |
| **ElevenLabs** | `msg["detected_language"]` | `_receive_loop` → `msg.get("detected_language")` |
| **Alibaba Bailian** | 无（API 不返回） | 不设置，无 hint |
| **Alibaba Qwen ASR** | `msg["language"]` | `_receive_loop` → `msg.get("language")`（OpenAI Realtime 格式） |

#### 2.4.3 Translate Prompt 结构

LLM 收到的是**统一指令**，不做服务端方向分支：

```
{translate_prompt}

Translate the following text: if it's Chinese, translate to English;
if it's any other language, translate to Chinese.
Output only the translation, no explanations.

[Detected language: zh-CN]    ← 仅当 ASR 有检测结果时追加
```

LLM 自行判断 `user_text` 的语言并执行对应方向翻译。ASR 的 `detected_language` 仅作为弱 hint，不强制服务端逻辑。

**不再需要** `_resolve_translate_direction()` 方法和 `asr_default_language` 配置。

### 2.5 Translate Mode 核心流程

在 `_start_thinking` 中增加 translate mode 分支。关键差异：

```
Chat Mode:     ASR text → LLM(companion prompt + history) → TTS(text) + ai_text(text)
Translate Mode: ASR text → LLM(translator prompt + translation instructions) → TTS(text) + ai_text(text)
```

**Translate mode 的 `_start_thinking` 逻辑**：

1. ASR 输出 `user_text`（原始 transcription）
2. 发送 transcription 给客户端（同 chat mode）
3. 构建 LLM 消息：
   - `system`: translate prompt + 统一翻译指令 + 可选 language hint
   - **不传 conversation history**（每次翻译独立）
   - `user`: ASR 识别出的原始文本
4. LLM stream → TTS feed + ai_text（同 chat mode）
5. 翻译结果**不**写入 history

### 2.6 代码变更汇总

| 文件 | 变更 |
|------|------|
| `services/asr/base.py` | `AsrResult` 增加可选 `language` 字段 |
| `services/asr/providers/deepgram.py` | `_receive_loop` 提取 `channel.detected_language` |
| `services/asr/providers/elevenlabs.py` | `_receive_loop` 提取 `detected_language` |
| `services/asr/providers/alibaba_bailian.py` | 无需变更（API 不返回语言） |
| `services/asr/providers/alibaba_qwen_asr.py` | `_receive_loop` 提取 `msg["language"]` |
| `configs/dev/server.yaml` | `llm` 节加 `translate_prompt_path` |
| `services/shared/config/models.py` | `LlmConfig` 加 `translate_prompt_path` |
| `services/app/application.py` | lifepan 加载 translate prompt → `app.state.translate_prompt` |
| `services/session/orchestrator.py` | 构造函数 + `_start_thinking` 增加 translate 分支 + `_last_detected_language` hint 注入 |
| `services/app/endpoint.py` | 传递 `translate_prompt` 给 `DialogOrchestrator` |
| `services/app/application.py` | lifepan 加载 translate prompt → `app.state.translate_prompt` |

### 2.7 客户端行为（无变更）

Translate mode 下客户端收到的消息序列与 chat mode 完全一致：

```
session.ready → status(idle) → status(listening)
  → transcription (ASR 原始文本)
  → status(thinking) → ai_text (翻译结果流) + ai_audio (翻译结果语音)
  → status(speaking) → status(listening)
```

客户端无需感知服务端处于 chat 还是 translate 模式。

---

## 3. Phase 2 — 动态切换（设计概要）

Phase 2 引入新的 C2S 消息 `session.change`，允许客户端在会话中动态切换模式。

### 3.1 新增 Schema

`packages/api-schema/c2s/session-change.json`：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://livachat.dev/schemas/c2s/session-change.json",
  "title": "Session Change",
  "type": "object",
  "properties": {
    "type": { "const": "session.change" },
    "seq": { "$ref": "../common.json#/$defs/SeqNumber" },
    "timestamp": { "$ref": "../common.json#/$defs/Timestamp" },
    "payload": {
      "type": "object",
      "properties": {
        "mode": {
          "type": "string",
          "enum": ["chat", "translate"]
        },
        "language": {
          "type": "string",
          "description": "Optional: user's preferred source language hint"
        }
      },
      "required": ["mode"]
    }
  },
  "required": ["type", "seq", "timestamp", "payload"]
}
```

### 3.2 Orchestrator 变更

- `_dispatch` 增加 `session.change` 分支
- 运行时替换 `self._pipeline_mode`、`self._system_prompt` / `self._translate_prompt`
- 发送 status 通知客户端模式已切换

---

## 4. 风险与约束

1. **语言检测精度**：由 ASR provider 返回的 `detected_language` 驱动。Deepgram 和 ElevenLabs 返回可靠的语言标签；Alibaba Bailian 不返回语言，此时回退到 config `asr.language`（如 `zh-CN`）。对于同时使用中英文的双语用户，回退到 config 的固定语言可能导致翻译方向错误。
2. **翻译质量**：依赖 LLM provider 的翻译能力，不是专用翻译模型。对专有名词、俚语可能翻译不准确。
3. **translate prompt**：现有的 `translator.txt` 是通用翻译提示词，需要在 prompt 中显式注入翻译方向（`→ Chinese` 或 `→ English`）。
4. **Translate mode 与 Omni 模式的关系**：Phase 1 仅覆盖 `DialogOrchestrator`（三段式 pipeline）。`OmniOrchestrator` 的 translate 场景优先使用 Alibaba LiveTranslate provider（已在 config 中配置），不需要额外改造。

---

## 5. 测试要点

1. 中文语音 → ASR 识别中文 → LLM 翻译为英文 → TTS 英文语音 + ai_text 英文
2. 英文语音 → ASR 识别英文 → LLM 翻译为中文 → TTS 中文语音 + ai_text 中文
3. Alibaba Bailian provider 无 detected_language 时正确回退到 config `asr.language`
4. `pipeline_mode=translate` 配置加载正确性
5. Translate mode 下 interrupt 行为正常
6. Translate mode 下 history 不累积翻译内容
