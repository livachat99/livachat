# Pipeline Translate Mode — Phase 2 Design (Dynamic Switch)

## 1. Overview

Phase 1 实现了配置驱动的 translate mode（`LIVACHAT_PIPELINE_MODE=translate`），翻译方向硬编码为"中译英/其他译中"。Phase 2 在此基础上增加：

- **运行时动态模式切换**：客户端通过 `session.change` 消息在 chat/translate 之间切换
- **用户语言感知翻译**：根据用户的母语和当地语言动态构建翻译指令
- **session.start 自动重置**：新会话始终从 chat mode 开始

---

## 2. Protocol Changes

### 2.1 New C2S: `session.change`

`packages/api-schema/c2s/session-change.json` (NEW):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://livachat.dev/schemas/c2s/session-change.json",
  "title": "session.change — Request Mode Switch",
  "description": "Client requests a pipeline mode change (chat or translate). Server replies with session.changed to confirm.",
  "type": "object",
  "required": ["type", "seq", "timestamp", "payload"],
  "properties": {
    "type": {
      "const": "session.change"
    },
    "seq": {
      "$ref": "../common.json#/$defs/SeqNumber"
    },
    "timestamp": {
      "$ref": "../common.json#/$defs/Timestamp"
    },
    "payload": {
      "type": "object",
      "required": ["mode"],
      "properties": {
        "mode": {
          "type": "string",
          "enum": ["chat", "translate"],
          "description": "Target pipeline mode."
        },
        "my_language": {
          "type": "string",
          "description": "User's native language code (e.g. 'zh', 'en', 'ja'). Required when mode=translate."
        },
        "local_language": {
          "type": "string",
          "description": "Local environment language code (e.g. 'ja', 'fr'). Required when mode=translate."
        }
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false,
  "examples": [
    {
      "type": "session.change",
      "seq": 42,
      "timestamp": 1712345680000,
      "payload": {
        "mode": "translate",
        "my_language": "zh",
        "local_language": "ja"
      }
    }
  ]
}
```

**Field semantics:**

| Field | Required | Description |
|-------|----------|-------------|
| `mode` | always | `"chat"` or `"translate"` |
| `my_language` | when `mode=translate` | User's native/preferred language (ISO 639-1 code) |
| `local_language` | when `mode=translate` | Language of the environment the user is in |

When `mode=chat`, `my_language` and `local_language` are ignored. Server retains the last-known language pair for the session duration.

### 2.2 New S2C: `session.changed`

`packages/api-schema/s2c/session-changed.json` (NEW):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://livachat.dev/schemas/s2c/session-changed.json",
  "title": "session.changed — Mode Switch Confirmed",
  "description": "Server confirms the pipeline mode has been changed. Sent in response to session.change.",
  "type": "object",
  "required": ["type", "seq", "timestamp", "payload"],
  "properties": {
    "type": {
      "const": "session.changed"
    },
    "seq": {
      "$ref": "../common.json#/$defs/SeqNumber"
    },
    "timestamp": {
      "$ref": "../common.json#/$defs/Timestamp"
    },
    "payload": {
      "type": "object",
      "required": ["mode"],
      "properties": {
        "mode": {
          "type": "string",
          "enum": ["chat", "translate"],
          "description": "The now-active pipeline mode."
        },
        "my_language": {
          "type": "string",
          "description": "Echo of the user's language (only present in translate mode)."
        },
        "local_language": {
          "type": "string",
          "description": "Echo of the local language (only present in translate mode)."
        }
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false,
  "examples": [
    {
      "type": "session.changed",
      "seq": 43,
      "timestamp": 1712345680100,
      "payload": {
        "mode": "translate",
        "my_language": "zh",
        "local_language": "ja"
      }
    }
  ]
}
```

### 2.3 Updated Schema Indexes

**`packages/api-schema/c2s/message.json`** — add `session-change.json` to `anyOf`:

```json
{
  "anyOf": [
    { "$ref": "./session-start.json" },
    { "$ref": "./session-end.json" },
    { "$ref": "./session-change.json" },
    { "$ref": "./audio.json" },
    { "$ref": "./video.json" },
    { "$ref": "./interrupt.json" },
    { "$ref": "./ping.json" },
    { "$ref": "./pong.json" }
  ]
}
```

**`packages/api-schema/s2c/message.json`** — add `session-changed.json` to `anyOf`:

```json
{
  "anyOf": [
    { "$ref": "./session-ready.json" },
    { "$ref": "./session-ended.json" },
    { "$ref": "./session-changed.json" },
    { "$ref": "./transcription.json" },
    { "$ref": "./ai-text.json" },
    { "$ref": "./ai-audio.json" },
    { "$ref": "./ai-video-ack.json" },
    { "$ref": "./status.json" },
    { "$ref": "./error.json" },
    { "$ref": "./ping.json" },
    { "$ref": "./pong.json" }
  ]
}
```

### 2.4 New Status Reason Code

**`packages/api-schema/common.json`** — add `"mode_changed"` to `StatusReasonCode`:

```json
"StatusReasonCode": {
  "type": "string",
  "enum": [
    "connected",
    "asr_final",
    "tts_start",
    "tts_finish",
    "interrupted",
    "idle_timeout",
    "mode_changed"
  ]
}
```

---

## 3. Translate Mode: Language-Aware Translation Logic

### 3.1 Translation Direction Rules

When `pipeline_mode == "translate"`, the orchestrator constructs translation instructions based on `my_language` / `local_language`:

```
Let src = ASR detected language (from AsrResult.language), or LLM auto-detect
Let my  = my_language
Let loc = local_language

if src ≠ my:
    → translate to my
elif my ≠ loc:
    → translate to loc
else:
    → translate to English
```

The resulting LLM system instruction:

```
{prompt}

Translate the following text:
- If the source language is NOT "{my_language}", translate the text to "{my_language}".
- If the source language IS "{my_language}" and "{my_language}" differs from "{local_language}", translate the text to "{local_language}".
- If "{my_language}" and "{local_language}" are the same, translate to English.
Output only the translation, no explanations.

[Detected language: {detected_language}]
```

语言码统一加双引号，避免 `it` 这类既是 ISO 639-1 码又是英文单词的代码被模型当作代词；`_build_auto_translate_instruction` 同样使用加引号的码。

客户端候选（2026-10-27）：`auto, zh, en, ja, ko, fr, it, de, es, pt, ru, tr, hi, id, th, vi, ar`。Android 已实施 `th`/`vi`（`docs/android_multilingual.md` §21）；iOS 计划同列表（`docs/ios_multilingual.md` §2.9，待实现）。服务端 `_normalize_lang` 接受任意 2–3 位 ISO 码，无需新增白名单，但需验证 LLM 翻译与 TTS。

### 3.2 Rationale

| Scenario | my_language | local_language | ASR hears | Translate to |
|----------|-------------|----------------|-----------|--------------|
| Chinese tourist in Japan | `zh` | `ja` | Japanese | `zh` (understand local) |
| Chinese tourist in Japan | `zh` | `ja` | Chinese | `ja` (help local understand) |
| American in China | `en` | `zh` | Chinese | `en` (understand local) |
| American in China | `en` | `zh` | English | `zh` (help local understand) |
| Same-language region | `zh` | `zh` | Chinese | `en` (default fallback) |

---

## 4. Server-Side Code Changes

### 4.1 Protocol DTOs (Generated — Regenerate After Schema Changes)

Run after schema changes:
```bash
make generate-android-api   # Regenerate Kotlin DTOs
# Python generation is done via scripts/generate_python_api.py
```

**Generated Python files that will be updated:**

| File | New Symbol |
|------|-----------|
| `packages/sdk-python/src/livachat_protocol/generated/client_message.py` | `ClientSessionChange` class |
| `packages/sdk-python/src/livachat_protocol/generated/protocol_models.py` | `ClientSessionChangePayload` class |
| `packages/sdk-python/src/livachat_protocol/generated/server_message.py` | `ServerSessionChanged` class |
| `packages/sdk-python/src/livachat_protocol/generated/protocol_models.py` | `ServerSessionChangedPayload` class |

**Generated Kotlin files that will be updated:**

| File | New Symbol |
|------|-----------|
| `packages/sdk-kotlin/.../ClientMessage.kt` | `SessionChange` data class |
| `packages/sdk-kotlin/.../ServerMessage.kt` | `SessionChanged` data class |

### 4.2 `services/gateway/codec.py` — New Convenience Builder

Add `make_session_changed()` to `WireCodec`:

```python
def make_session_changed(
    self, *, mode: str, my_language: str | None = None, local_language: str | None = None
) -> str:
    payload: dict[str, Any] = {"mode": mode}
    if my_language:
        payload["my_language"] = my_language
    if local_language:
        payload["local_language"] = local_language
    return self.build_typed(
        ServerSessionChangedPayload(**payload),
        msg_cls=ServerSessionChanged,
    )
```

Import `ServerSessionChanged` and `ServerSessionChangedPayload` from `livachat_protocol.generated.server_message`.

### 4.3 `services/session/orchestrator.py` — Core Changes

#### 4.3.1 `__init__` — New State Fields

```python
# Translate mode — dynamic language preferences (set by session.change)
self._my_language: str | None = None
self._local_language: str | None = None
```

#### 4.3.2 `_dispatch` — Add `session.change` Branch

```python
async def _dispatch(self, msg: Any) -> None:
    t = msg.type
    if t == "session.start":
        await self._handle_session_start(msg)
    elif t == "session.end":
        await self._handle_session_end(msg)
    elif t == "session.change":
        await self._handle_session_change(msg)
    elif t == "audio":
        await self._handle_audio(msg)
    elif t == "interrupt":
        await self._handle_interrupt(msg)
    elif t == "video":
        await self._handle_video(msg)
```

#### 4.3.3 `_handle_session_start` — Reset Mode to Chat

At the beginning of `_handle_session_start`, reset mode and language prefs:

```python
async def _handle_session_start(self, msg: Any) -> None:
    # Reset to default chat mode on every new session
    self._pipeline_mode = "full"  # or os.environ default
    self._my_language = None
    self._local_language = None
    # ... rest of existing logic unchanged ...
```

#### 4.3.4 `_handle_session_change` — New Method

```python
async def _handle_session_change(self, msg: Any) -> None:
    """Handle session.change — switch between chat and translate modes."""
    if not self._ctx:
        return

    new_mode = msg.payload.mode

    if new_mode == "chat":
        self._pipeline_mode = "full"
        self._my_language = None
        self._local_language = None
    elif new_mode == "translate":
        self._pipeline_mode = "translate"
        self._my_language = getattr(msg.payload, "my_language", None)
        self._local_language = getattr(msg.payload, "local_language", None)

    # Persist event
    self._persist_event(
        "mode_change",
        metadata_={
            "from": self._ctx._previous_mode,  # tracked via ctx
            "to": new_mode,
            "my_language": self._my_language,
            "local_language": self._local_language,
        },
    )

    # Send confirmation
    frame = self._codec.make_session_changed(
        mode=new_mode,
        my_language=self._my_language,
        local_language=self._local_language,
    )
    await self._handler.enqueue_outbound(frame)

    # Notify state change
    await self._send_status(
        self._ctx.state.value,
        "mode_changed",
    )
```

#### 4.3.5 `_start_thinking` — Dynamic Translation Instruction

Replace the hardcoded translation instruction with language-aware logic:

```python
if is_translate:
    prompt = self._translate_prompt or (
        "You are a real-time voice translator. "
        "Preserve the speaker's tone, intent, and nuance. "
        "Output only the translated text — no explanations, no commentary."
    )
    instruction = self._build_translate_instruction()
    translate_system = f"{prompt}\n\n{instruction}"
    if self._last_detected_language:
        translate_system += f"\n\nDetected language: {self._last_detected_language}"
    messages.append(ChatMessage(role="system", content=translate_system))
    messages.append(ChatMessage(role="user", content=user_text))
    self._last_detected_language = None
```

#### 4.3.6 `_build_translate_instruction` — New Method

```python
def _build_translate_instruction(self) -> str:
    """Build language-aware translation instruction from user prefs."""
    my = self._my_language or "zh"
    loc = self._local_language or "en"
    # Quote ISO codes so short codes that are also English words (e.g. "it")
    # cannot be read as pronouns by the model.
    my_code = f'"{my}"'
    loc_code = f'"{loc}"'

    return (
        f"Translate the following text:\n"
        f"- If the source language is NOT {my_code}, translate the text to {my_code}.\n"
        f"- If the source language IS {my_code} and {my_code} differs from {loc_code}, "
        f"translate the text to {loc_code}.\n"
        f"- If {my_code} and {loc_code} are the same, translate to English.\n"
        f"Output only the translation, no explanations."
    )
```

---

## 5. Message Flow

### 5.1 Mode Switch to Translate

```
Client                              Server
  |                                    |
  |── session.change ─────────────────>|
  |   {mode: "translate",              |
  |    my_language: "zh",              |
  |    local_language: "ja"}           |
  |                                    |  _pipeline_mode = "translate"
  |                                    |  _my_language = "zh"
  |                                    |  _local_language = "ja"
  |<── session.changed ────────────────|
  |   {mode: "translate",              |
  |    my_language: "zh",              |
  |    local_language: "ja"}           |
  |<── status ─────────────────────────|
  |   {session_status: "listening",    |
  |    reason: "mode_changed"}         |
```

### 5.2 Mode Switch to Chat

```
Client                              Server
  |                                    |
  |── session.change ─────────────────>|
  |   {mode: "chat"}                   |
  |                                    |  _pipeline_mode = "full"
  |                                    |  _my_language = None
  |                                    |  _local_language = None
  |<── session.changed ────────────────|
  |   {mode: "chat"}                   |
  |<── status ─────────────────────────|
  |   {session_status: "listening",    |
  |    reason: "mode_changed"}         |
```

### 5.3 New Session Start — Automatic Reset

```
Client                              Server
  |                                    |
  |── session.start ──────────────────>|
  |                                    |  _pipeline_mode = "full"   (reset)
  |                                    |  _my_language = None       (reset)
  |                                    |  _local_language = None    (reset)
  |<── session.ready ──────────────────|
  |<── status(idle) ───────────────────|
  |<── status(listening) ──────────────|
```

---

## 6. File Change Summary

| File | Change |
|------|--------|
| `packages/api-schema/c2s/session-change.json` | **NEW** — C2S schema |
| `packages/api-schema/c2s/message.json` | Add `session-change.json` to `anyOf` |
| `packages/api-schema/s2c/session-changed.json` | **NEW** — S2C schema |
| `packages/api-schema/s2c/message.json` | Add `session-changed.json` to `anyOf` |
| `packages/api-schema/common.json` | Add `"mode_changed"` to `StatusReasonCode` enum |
| `packages/sdk-python/.../client_message.py` | **Regenerated** — new `ClientSessionChange` |
| `packages/sdk-python/.../protocol_models.py` | **Regenerated** — new payload models |
| `packages/sdk-python/.../server_message.py` | **Regenerated** — new `ServerSessionChanged` |
| `packages/sdk-kotlin/.../ClientMessage.kt` | **Regenerated** — new `SessionChange` |
| `packages/sdk-kotlin/.../ServerMessage.kt` | **Regenerated** — new `SessionChanged` |
| `services/gateway/codec.py` | Add `make_session_changed()` builder |
| `services/session/orchestrator.py` | `__init__`: new fields; `_dispatch`: new branch; `_handle_session_start`: reset mode; `_handle_session_change`: new method; `_start_thinking`: dynamic instruction; `_build_translate_instruction`: new helper |

### Not in Scope (Phase 2)

- Client SDK changes (iOS/Android/Web) — client must send `session.change` and handle `session.changed`, but that's a separate task
- `OmniOrchestrator` — translate via Alibaba LiveTranslate provider already configured, no dynamic switch needed
- Database schema changes — mode changes are persisted via `session_events` (existing `metadata_` JSONB column)
- `_persist_event` extension — the existing method already accepts `metadata_` kwargs

---

## 7. Error Handling

| Scenario | Server Behavior |
|----------|----------------|
| `session.change` with `mode=translate` but no `my_language`/`local_language` | Accept the message; use defaults (my=`zh`, loc=`en`) with a warning log |
| `session.change` received during THINKING/SPEAKING | Apply mode change immediately; in-flight pipeline continues with old mode; next turn uses new mode |
| Invalid `mode` value (not `chat`/`translate`) | Reject with `ServerError(code=4001, message="invalid mode")` |
| `session.change` before `session.start` | Reject with `ServerError(code=4003, message="session not started")` |

---

## 8. Testing

1. Send `session.change(mode=translate, my_language="zh", local_language="ja")` → verify `session.changed` echoed back → verify `status(reason=mode_changed)`
2. Send `session.change(mode=chat)` → verify mode reset → verify no language prefs
3. Start new session → mode defaults to `chat` even after previous translate mode
4. Translate with Chinese input, my=zh, loc=ja → LLM translates to Japanese
5. Translate with Japanese input, my=zh, loc=ja → LLM translates to Chinese
6. Translate with Chinese input, my=zh, loc=zh → LLM translates to English (fallback)
7. Send `session.change` with invalid mode → verify error response
8. `session.change` received during speaking → mode applies to next turn
