# Server 多语言（主动对话历史消息 + chat mode LLM 输出语言规则）

> **状态**：已实施（主动对话 2026-10-27；chat 输出语言规则 2026-10-28）。
> 17 语种检测器/回复语言探针通过；chat 输出语言规则真实 LLM 探针通过；omni 本期不改。
> **范围**：server 端——① chat 模式**主动发起**链路（avdetector 环境事件、cam 首帧直起轮）；
> ② pipeline chat 普通回合的 LLM 输出语言规则（§4.12，已实施）；omni 本期不改。
> **目标**：主动链路的合成 user 行与 assistant 回复按 `session.start` 语言本地化；
> chat 普通回合回复优先跟随用户当前提问/显式语言要求，无法判定时回落到
> `session.start.language`。
> **关联文档**：`docs/session_start_improvments.md`、`docs/fd_pipeline_avdetector.md`、
> `docs/fd_pipeline_video_process.md`、`docs/chatserver_sqlite_store.md`、
> `docs/recording_data_process.md`、`docs/mylife/mylife-design.md`。

---

## 0. 结论先行

| 项 | 结论 |
|---|---|
| 目标语言 | `session.start.payload.config.language` → `SessionContext.language`（已存储；主动链路用于文本与回复语言） |
| 支持集合 | server 17 code 表（含已下线的 `tr`；App 当前上报 16 个，见 §2.1） |
| 空值/未上报/未知值 | **默认 `en`**：按英文目标语言处理（英文 label + 英文 desc + 语言子句）；App 自身也默认英文 |
| `zh-Hans` | 源语言，检测器输出简体中文 |
| `zh-Hant` / 其余 server 表语言 | 检测器 `desc` 直接输出目标语言（App 当前 14 种，不含 `tr`） |
| 检测器 `event` | 固定枚举：`normal` / `danger`（无事件仍输出 `NONE`） |
| 用户可见 label | 统一 ASCII `[]`；label 文本按 server 17 语言表本地化（App 当前 16，不含 `tr`） |
| `desc` | **检测器直出目标语言，不做服务器翻译** |
| 发给 LLM 的 user message | 合成 user 行 = 目标语言 label + 目标语言 `desc`；请求以该 user 行结尾 |
| assistant 回复 | user message 用目标语言，并在主动回合注记末尾追加目标语言回复指令（非 `zh-Hans`） |
| LLM 输出语言（chat，§4.12） | **用户明确要求 > 用户当前提问语言 > `session.start.language` > `en`**；只做 system prompt 规则注入，无服务器识别/翻译 |
| 主动回合 vs 普通回合 | 主动回合没有“用户提问”，使用 session 语言；普通回合按上述优先级 |
| omni | 本期不改 system prompt；输出语言维持现状 |
| 写入面 | `WorkingMemory` / `chat_store` / `chat/history.jsonl` 三处统一写本地化文本 |
| 实时会话用户气泡 | **不补发**（已定）：实时会话/App 本地历史只显示 AI 回复；合成行仅 chatlog/MyLife 与内部 history |
| 协议 / DB schema | **零变更** |
| 关键风险 | ① 检测器不遵守目标语言要求时没有服务器兜底；② `event` 是危险判定控制字段，不能翻译/自由文本化；③ 检测器在串行监控管道内被 await，不能在回调中加网络调用；④ chat 的输出语言规则只是 prompt 约束，无服务器兜底（§4.12） |

---

## 1. 需求与范围

### 1.1 需求

> 历史末尾落一条 user 行：环境事件；历史以 user 行 `[相机画面]`（带图）结尾。
> 历史消息要记录到 chat log，显示给最终用户；里面的文字要支持不同的语言翻译。
> 语言支持与 App 一致（17 种），以 `session.start()` 传递的 `language` 为翻译目标。
> 目前只考虑这种主动 server 发起的对话模式。

（需求提出时 App 为 17 语言；当前 App 16、server 表 17，见 §2.1。）

已确认的文本口径：

- `event` 直接定义成 `normal` / `danger` 两种，不再使用自由文本标签；
- `desc` 由检测器直接生成目标语言，**不做服务器翻译**；
- 用户可见 label 使用通用 `[]` 中括号；
- 合成 user message 使用 `SessionContext.language` 对应的语言；server 端空值/未知值默认英文；
  `zh-Hans` 保持中文。

### 1.2 本期范围

- `_on_av_event`（avdetector 环境事件主动回合）的合成 user 行与 assistant 回复；
- `_handle_video` 的 `turn_id` 首帧直起轮（cam 首帧）的合成 user 行与 assistant 回复；
- 仅 chat 模式（两条链路本就只在 chat 生效）；
- server 端文本本地化、写入、检测器 prompt 语言约束、回复语言验证；
- 相关单测/集成测试与文档同步。

**2026-10-28 追加需求（§4.12，已实施）**：

- 仅 pipeline chat mode（`_pipeline_mode == "full"`，ASR→LLM→TTS）普通回合；
- **omni 不改 system prompt**，输出语言维持现状；
- 输出语言优先级：用户当前消息的明确语言要求 > 用户当前提问语言 >
  `session.start.language`（空/未知 → `en`）。

### 1.3 非目标

- translate / transcribe / asr_only / lecture 模式（pipeline chat 普通回合由 §4.12
  追加覆盖）；
- **omni mode 的 system prompt / 输出语言规则**（本期不改）；
- 服务端语言识别（不消费 ASR `result.language`）、服务端翻译/后处理；
- agent job 结果的服务器翻译——agent relay 已按自身语言规则生成目标语言文本
  （`docs/agent_multilingual.md`），主 chat 不再二次翻译；
- 客户端 UI 文案、协议 schema、DB schema、聊天记录历史回填；
- 客户端 App 语言运行中切换（`session.start.language` 以每次 `session.start` 为准；
  用户仍可通过当前提问语言或显式语言要求改变 LLM 回复语言，§4.12）；
- 多语言同时存储（一条历史只按写入时的目标语言保存）；
- MyLife day_summary / 音频分析的输出语言。

---

## 2. 改造前基线（以 codebase 为准）

### 2.1 语言契约（server 表与 App 当前集合）

**server 侧** `proactive_i18n.SUPPORTED_PROACTIVE_LANGUAGES`（17 项）：

```text
en, zh-Hans, zh-Hant, ja, ko, ru, de, fr, it, es, pt, tr, hi, id, th, vi, ar
```

**App 当前上报**（两端 16 项）：

```text
en, zh-Hans, zh-Hant, ja, ko, ru, de, fr, it, es, pt, hi, id, th, vi, ar
```

依据：

- Android：`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/util/AppLanguage.kt:7-10`
  （`appLanguageCodes`）；不支持的系统 locale 回落 `en`。
- iOS：`apps/ios/LivaChatApp/Core/UI/AppLocale.swift:7-10`（`AppLocale.supportedCodes`）；
  不支持回落 `en`。
- App 已移除 `tr`（commit `22ba7b28`）；server 表仍保留 `tr` 作为历史兼容，
  App 不会再上报。是否清理 server 的 `tr` 条目另行处理，不影响本规则。

两端都会把**不支持的系统 locale 回落 `en`**，因此正常客户端每次上报 16 个 code 之一；
server 收到空值/未知值时**也默认 `en`**，与 App 默认一致。

### 2.2 `session.start` 的 `language`

- 协议字段：`packages/api-schema/c2s/session-start.json` → `payload.config.language`，
  optional string，语义为“App 当前 UI 语言 canonical code，不是设备语言”。
- 协议文档：`docs/ws-protocol-design.md` §2.2.1；当前描述为“服务端保存到会话变量，
  并用于主动对话历史文本的目标语言”（agent relay 与 §4.12 chat 消费口径在实施后补充）。
- server 存储：
  - `_handle_session_start` 把它传给 `SessionManager.start(...)`（`services/session/orchestrator.py:548`）；
  - `SessionContext.language: str = ""`（`services/session/context.py:26`）；
  - `SessionManager.start` 每次 `session.start` 重建 `SessionContext`，不落库、不从快照恢复
    （`services/session/manager.py:79,149,165`）。resume 时客户端会重新带上完整 config。
- 改造前 server 无任何代码消费该字段；主动对话链路已消费（§4.1/§4.6）；
  §4.12 计划将同一字段用于 pipeline chat 普通回合的输出语言回落（omni 本期不改）。
- translate 模式的 `_my_language` / `_local_language` 由 `session.change` 设置，
  与本字段完全独立。

### 2.3 两条主动链路

#### 2.3.1 avdetector 环境事件主动回合

入口：`DialogOrchestrator._on_av_event`（`services/session/orchestrator.py:1046`）。
门控顺序（现状）：

1. `mode == "full"`（chat）硬门；
2. pipeline 在飞：危险且非危险回合 → 抢占（`_do_interrupt("av_danger")` + `_cancel_pipeline`）；
   否则丢弃；
3. 不在飞：`LISTENING` → 竞态复检（`_asr_final_sent` / 像说话的 interim）→ 视觉抑制窗
   （普通画面）→ 冷却（声音；危险/画面豁免）；
4. 置 `_last_av_proactive`，构造 user 行并起轮。

合成 user 行的三件一体写入点：

- `content = f"【环境事件】{desc}"`（`orchestrator.py:1145`）；
- `history.add_message("user", content, images=urls)`（`:1147`）；
- `chat_store.on_message(kind="asr", role="user", content=content, ref_id="avd-…", images=urls)`
  （`:1151`）；
- `recording_sink.on_chat_text(role="user", text=content, ref_id="avd-…", frames=…)`（`:1165`）；
- `_turn_user_record["content"] = content`（4xx 回滚句柄，`:1158`）；
- system 注记 `_build_av_event_note(desc, label)`（`:1030`）把同一段 `desc` 再写进 system；
- `_start_thinking("", image_frames=..., turn_token=ref_id, event_note=note)`（`:1189`）。

当前 `desc` 与 `label` 来自检测器解析（`services/session/av_detector.py:85` `_parse_reply`）：

- `desc` 是模型生成的自由文本，当前 prompt 为中文，通常输出中文；
- `label` 是 `event` 字段，当前是自由文本；危险判定依赖 `is_danger_label`
  （`av_detector.py:75-82`，匹配 `"危险"` / `"danger"`）。**它不是用户显示文本。**

#### 2.3.2 cam 首帧直起轮

入口：`_handle_video` 的 turn 首帧分支（`services/session/orchestrator.py:2209-2266`）。
触发条件：`turn_id` 非空非 0 且变化、非 text 帧、模式不在 translate/transcribe/asr_only/omni、
非“危险在飞”、快照非空。

合成 user 行：

- `content = "[相机画面]"`（`orchestrator.py:2228` 附近三处写入 + `_turn_user_record`）；
- 三件一体写入同 avdetector（history / chat_store / recording）；
- system 注记 `_VIDEO_TURN_NOTE`（`:150`）；
- `_start_thinking("", image_frames=frames, turn_token=ref_id,
  event_note=_VIDEO_TURN_NOTE, visual_turn=True)`（`:2265`）。

该分支有“入 buffer → 快照”的同步段纪律（中间不得插 await）；本地化标签必须是**纯同步查表**。

### 2.4 最终用户实际看到哪些面

| 面 | 当前写入/来源 | 用户可见？ | 现状文本 | 本地化后的期望 |
|---|---|---|---|---|
| `WorkingMemory` history | `history.add_message("user", content, images)` | 否（LLM 上下文；resume 时恢复） | 中文 | 目标语言 |
| `chat_store` messages | `chat_store.on_message(kind="asr")` | 间接（`load_day` restore 到 history；不直接上屏） | 中文 | 目标语言 |
| `chat/history.jsonl` → lvdata `chat` event → BFF timeline/search → MyLife | `recording_sink.on_chat_text` | **是**（MyLife 聊天气泡/搜索） | 中文 | 目标语言 |
| Hermes / OpenClaw 监控 | chatlog watcher 读 `history.jsonl` 并 NUDGE | 否（agent 上下文） | 中文 | 目标语言（agent LLM 可读） |
| 实时会话 | S2C `transcription` / `ai_text` | **是** | **合成 user 行不存在**；只有 assistant 回复 | **不补发**（已定）：仍只有 AI 回复 |
| App 本地“对话历史” | Android/iOS 只把 final `transcription` 写成 USER 消息 | **是** | 合成 user 行不存在 | **不补发**（已定）：合成行不进该面 |

依据：

- MyLife chat 气泡来自 chatlog 事件：`docs/mylife/mylife-design.md:629`、
  `docs/mylife/mylife-frontend-android.md:508`；lvdata chatlog 映射见
  `tools/lvdata-mcp/src/lvdata_mcp/analysis/chatlog.py:57-90`（`summary` / `payload.text` = 行 `text`）。
- App 本地历史只消费 final transcription：Android
  `ConversationListenerAdapter.kt:155-190`；iOS `ConversationCoordinator.swift:455-480`；
  `docs/ios_chat_history.md` §2.4。
- 两条主动链路目前都**没有**调用 `_send_transcription`；只有 `ai_text` 发给客户端。
  本期确定维持这一行为（§4.7）。

### 2.5 检测器调用约束

- avdetector 的 `on_event` 在监控串行管道的 `_run_check` 内被 **await**
  （`services/session/av_detector.py:456-463`）；管道同时处理音频 append/commit/检查，
  `asyncio.Queue(maxsize=256)`（`:123,143`）。
- 因此本期**不在回调里增加任何网络翻译调用**；`desc` 的语言必须由检测器本身在
  `response.create` 那一轮直接生成。这也是“检测器直出目标语言”的核心工程依据。
- cam 首帧分支的“入 buffer → 快照”同步段不能插入 await（`orchestrator.py:2209-2225` 注释）；
  固定 label 用纯同步静态表。

### 2.6 当前检测器 prompt 与危险判定

- prompt 文件：`configs/dev/prompts/avdetector.txt`；
- 输出：一行 JSON `{"event":"<事件类型>","desc":"<不超过40字的客观描述>"}` 或 `NONE`；
- 配置：`temperature: 0.2, max_tokens: 128`（`configs/dev/server.yaml`）；
- 危险判定：`is_danger_label` 依赖 `"危险"` / `"danger"` 子串（`av_detector.py:75-82`）；
- 危险行为影响三条安全路径：冷却豁免、在飞抢占、紧迫语气注记
  （`orchestrator.py:1072-1094`、`_build_av_event_note`）。

---

## 3. 问题清单

| # | 问题 |
|---|---|
| P1 | `【环境事件】` / `[相机画面]` 是固定中文；括号风格不统一 |
| P2 | `desc` 由检测器按中文 prompt 生成，非中文用户看到中文描述 |
| P3 | `event` 是自由文本，危险判定依赖中英文子串；无法作为稳定的控制字段 |
| P4 | 合成 user 行只进 chatlog/MyLife 与内部 history，**不进实时会话/App 本地历史**（§2.4） |
| P5 | `event` 是危险判定控制字段，不能被目标语言本地化 |
| P6 | 检测器回调在串行监控管道内被 await，不能新增网络翻译 |
| P7 | 空/未知 `language` 默认英文（与 App 默认一致）；`zh-Hans` 保持中文；label 括号统一 `[]` 是有意的可见变化 |
| P8 | 检测器不遵守目标语言要求时，没有服务器兜底；必须用探针把关 |
| P9 | pipeline chat 普通回合没有输出语言规则：`SessionContext.language` 不进入 LLM 请求，回复语言只由用户发言/历史隐式决定（§4.12） |
| P9 | 下游消费者（MyLife 搜索、Hermes/OpenClaw、chat_store restore、day_summary 输入）会读到本地化文本，需说明影响 |

---

## 4. 设计

### 4.1 目标语言解析

新增 server 侧常量与解析函数（建议 `services/session/proactive_i18n.py`）：

```python
SUPPORTED_PROACTIVE_LANGUAGES = frozenset({
    "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de", "fr",
    "it", "es", "pt", "tr", "hi", "id", "th", "vi", "ar",
})
SOURCE_LANGUAGE = "zh-Hans"
DEFAULT_LANGUAGE = "en"


def resolve_proactive_language(code: str | None) -> str:
    """空/未知 → 默认英文；已知 → 原样返回。"""
    code = (code or "").strip()
    return code if code in SUPPORTED_PROACTIVE_LANGUAGES else DEFAULT_LANGUAGE
```

规则：

- 精确匹配 server 语言表（17 项；含已下线的 `tr`，§2.1）；**空值/未知值默认 `en`**
  （`DEFAULT_LANGUAGE`），与 App 端不支持 locale 时回落 `en` 的口径一致（§2.1）。
- 该默认只作用于主动对话文本解析，**不改变 `SessionContext.language` 的存储值**（仍为 `""`）；
  空值/未知值只代表老客户端或其他客户端，它们会看到英文主动消息。
- `zh-Hans` = 源语言，不附加语言要求（中文）；`zh-Hant` 是目标语言。
- 未知值（如旧客户端传 `zh`、`en-US`）→ `en`。
- 每次 `session.start` 重建 `SessionContext`；resume 重新读取，不做跨会话持久化。
- 该集合是 server 侧副本；测试固定断言 17 项，并在注释中指向 Android/iOS 的两处事实源。
- §4.12 的 chat 输出语言规则复用同一 `resolve_proactive_language()` 与
  `LANGUAGE_NAMES` 作为会话语言回落，不新增第二套语言表。

### 4.2 `event` 固定枚举：`normal` / `danger`

#### 4.2.1 输出契约

检测器只允许两种事件值：

```json
{"event":"normal","desc":"<目标语言描述>"}
{"event":"danger","desc":"<目标语言描述>"}
```

无值得关注内容仍输出 `NONE`。`event` 是控制字段，**不随语言变化，不做任何本地化**。

#### 4.2.2 prompt 改动

`configs/dev/prompts/avdetector.txt` 的输出段改为：

```text
输出格式：一行 JSON：{"event":"normal","desc":"<不超过40个字符的客观描述>"}；
危险行为（有人摔倒、小孩子的行为有潜在危险等）→ "event":"danger"；
其余值得关注的事件 → "event":"normal"。都没有值得关注的内容 → 只输出：NONE。
event 只能是 normal 或 danger，不得输出其它值，也不得翻译。
```

`desc` 的语言约束由 §4.3 的 per-session 子句追加。

#### 4.2.3 解析与危险判定

`_parse_reply`（`av_detector.py:85`）改为：

- `NONE` / 空 → `None`；
- `event = str(obj.get("event") or "").strip().lower()`；只接受 `"normal"` / `"danger"`，
  其它值 → `None`；
- `desc = str(obj.get("desc") or "").strip()`；
- `event == "normal"` 且 `desc` 为空 → `None`（丢弃 + 告警）；
- `event == "danger"` 且 `desc` 为空 → **不丢弃**：返回 `{"event":"danger","desc":""}`，
  走 label-only 安全兜底（content = 本地化 env label），告警保留原始回复；
- 非法 JSON / 未知/缺失 `event` → `None`，由 `_run_check` 告警；日志在 `_run_check` 打印
  （`_parse_reply` 保持纯函数），必须带原始回复截断（如 `text[:200]`，其中含 event/desc
  原文）和 `session_id`，方便 debug 与调 prompt；
- `_run_check` 用原始文本是否为 `NONE` 区分“无事件”（debug）与“非法回复”（warning），
  避免合法空事件刷 warning；
- 返回 `{"event": event, "desc": desc}`。

危险判定改为精确匹配：

```python
is_danger = (event == "danger")
```

现有 `is_danger_label` / `_DANGER_CORE_WORDS`（`av_detector.py:75-82`）删除或替换为
`is_danger_event(event)`；不再依赖中文/英文子串。回调 `_on_av_event` 的 `label` 参数承载
`normal` / `danger`。

危险三条路径保持不变：

- 冷却豁免（`is_danger`）；
- pipeline 在飞抢占（`is_danger and not self._pipeline_danger`）；
- `_build_av_event_note` 的紧迫语气（`event == "danger"`）。

### 4.3 `desc` 由检测器直出目标语言

#### 4.3.1 prompt 组合

`_ensure_av_monitor`（`orchestrator.py:966-979`）在创建 monitor 时，把基础 prompt 与
per-session 语言子句组合后传给 `AvDetectorMonitor(system_prompt=...)`：

```python
base = self._avdetector_system_prompt          # configs/dev/prompts/avdetector.txt
lang = resolve_proactive_language(self._ctx.language)
clause = build_desc_language_clause(lang)      # lang == SOURCE_LANGUAGE 时返回 ""
prompt = f"{base}\n\n{clause}" if clause else base
```

语言子句（`lang != zh-Hans` 时；空/未知已解析为 `en`）：

```text
语言要求：desc 字段必须使用 {language_name} ({code}) 书写，
不超过40个字符；event 仍只能是 normal 或 danger，不要翻译 event。
输出格式不变，仍是一行 JSON。
```

- `lang == "zh-Hans"`（源语言）：不加子句，desc 输出中文；
- 空/未知已在 §4.1 解析为 `en`，因此会加英文子句，desc 输出英文；
- `zh-Hant`：`language_name = "Traditional Chinese"`，desc 输出繁体中文；
- 其余 15 种（含 `en`）：输出对应语言。
- `event` 的 `normal`/`danger` 是 ASCII 控制值，始终不变。
- 语言名表见附录 B；使用英文语言名 + canonical code，避免仅用短 code（如 `it` 被误读为代词）。

#### 4.3.2 不做服务器翻译

- 不在 `_on_av_event`、monitor 回调或任何写路径调用 `LlmService` 做翻译；
- 不新增翻译 provider/prompt/task；
- 检测器 `check()` 的网络调用本来就在监控管道内，语言要求随 system prompt 下发，不增加轮次；
- `desc` 非空但非目标语言（检测器未遵守）→ **没有服务器翻译兜底**：按实际输出写入，
  由探针/验收保证质量（§6.2）；
- `normal` + 空 `desc` → 丢弃；`danger` + 空 `desc` → label-only 安全兜底（§4.2.3），
  不因缺描述丢失危险事件。

#### 4.3.3 `max_tokens` 调整为 256

本期确定把 `avdetector.generation.max_tokens` 从 **128 提高到 256**，为 17 种语言下
40 字符描述 + JSON 包装预留生成空间。17 语种探针仍需验证 256 下无截断、JSON 格式合规；
若仍截断，再单独评估更高值（不默认继续加）。该调整只影响检测器生成，不影响协议。

### 4.4 用户可见 label：统一 `[]` + 17 语言静态表

两条链路的 label 统一使用 ASCII `[]`，文本按目标语言本地化：

| 链路 | 源语言（zh-Hans） | 目标语言示例 |
|---|---|---|
| avdetector | `[环境事件]` | `[Environment event] ` / `[環境事件]` / `[環境イベント]` |
| cam 首帧 | `[相机画面]` | `[Camera view]` / `[相機畫面]` / `[カメラ映像]` |

- 非 CJK 语言的 env 前缀末尾带一个空格，保证 `prefix + desc` 可读；cam label 无 desc，不加空格。
- 静态表放 server（建议 `proactive_i18n.py`）；查不到目标语言 → 默认英文（防御分支；
  正常只会传入 server 表内的 code，App 当前 16 个）。
- 该查表是纯同步操作，满足 cam 首帧分支的同步段纪律。
- label 文案采用 **AI 翻译稿**（附录 A），静态写入 server 表，不在运行时调用 AI；
  如后续产品统一术语，再批量更新。
- 这是一次**有意的可见变化**：中文源 label 从 `【环境事件】` 改为 `[环境事件]`；空/未知语言
  不再保持中文，而是与 App 默认一致走英文；`zh-Hans` 仍为中文。
- system 注入注记也统一为 ASCII `[]`：`[环境事件·系统注入]` / `[实时画面·系统注入]`。
  它们不是用户可见 label，但风格与 history label 保持一致。

### 4.5 写入链路改动

#### 4.5.1 avdetector

在 `_on_av_event` 的最终门控之后：

```python
lang = resolve_proactive_language(self._ctx.language)
content = f"{ENV_PREFIX[lang]}{desc}"   # desc 已是检测器直出的目标语言
```

- `history.add_message("user", content, images=urls)`；
- `chat_store.on_message(kind="asr", role="user", content=content, ref_id="avd-…", images=urls)`；
- `recording_sink.on_chat_text(role="user", text=content, ref_id="avd-…", frames=…)`；
- `_turn_user_record["content"] = content`；
- `_build_av_event_note(event, lang)` **不重复 desc**：desc 只保留在 user 行（环境内容不可信，
  不注入 system，降低提示注入面）；`event == "danger"` 时走紧迫语气；`desc` 为空（仅 danger
  兜底）时 content 只用 `ENV_PREFIX[lang]`，并记 warning；
- `_start_thinking("", image_frames=..., turn_token=ref_id, event_note=note)`。

不变：`role="user"`、`kind="asr"`、`ref_id` 前缀、`ts_ms`、`images`、三件一体顺序、
危险门控顺序、`_last_av_proactive` 冷却语义。

#### 4.5.2 cam 首帧

- `content = CAMERA_LABEL[lang]`（同步查表）；
- 三件一体 + `_turn_user_record` 使用同一 `content`；
- `_VIDEO_TURN_NOTE` 保持现有中文注记，并追加目标语言回复指令（§4.6）；
- 不新增网络调用、不改变现有同步段纪律。

### 4.6 给 LLM 的 user message 与回复语言

- 合成 user 行就是 `WorkingMemory` 的最后一条 user message；
  `_start_thinking` 非 translate 分支只组装 system + history，不额外追加 `user_text`
  （`orchestrator.py:1607-1611`）。因此本地化后的合成行就是 LLM 请求结尾的 user message。
- 目标语言由 `SessionContext.language` 决定（空/未知默认 `en`）；该 user message 的 label 与
  `desc` 均为目标语言（仅 `zh-Hans` 为中文）。
- **回复语言必须显式约束**：主动回合的 system 注记仍是中文，仅靠目标语言 user message
  不稳定（真实探针中 ja/ar/th 会回英文、zh-Hant 会回简体）。因此两条链路都默认在注记末尾
  追加：

  ```text
  请使用 {language_name} ({code}) 回复。
  ```

  该指令对 `zh-Hans` 不追加（源语言），对 `en`/`zh-Hant`/其余 14 种追加；不改变 user
  message 的本地化方案。
- 真实探针已确认全部 17 语种在追加指令后回复目标语言。
- TTS 语言随 LLM 输出文本；需在探针/真机验证目标语言发音与音色。

### 4.7 实时会话与 App 本地历史（已定：不补发）

**决策**：两条主动链路**不调用** `_send_transcription`，不向客户端补发合成 user 行。

后果：

- MyLife 的 chatlog 气泡**会**显示合成 user 行（chatlog → lvdata → MyLife）；
- 实时会话页与 App 本地对话历史**不会**显示该 user 行，只显示 assistant 回复；
- 内部 `WorkingMemory` / `chat_store` 仍保留合成 user 行，供 LLM 上下文与 restore；
- 不新增协议字段、S2C 消息类型；未来若要在实时会话展示触发源，另立需求（可复用
  `transcription`，但本期不做）。

### 4.8 fallback 与质量边界

| `SessionContext.language` | label | `desc` | user message | 说明 |
|---|---|---|---|---|
| `""`（老客户端） | 英文 `[]` | 检测器英文 | 英文 | 默认 `en`（与 App 默认一致）；括号变化是本次有意变更 |
| 未知值 | 英文 `[]` | 检测器英文 | 英文 | 同上 |
| `zh-Hans` | 源语言 `[]` | 检测器中文 | 中文 | 不附加语言要求 |
| `zh-Hant` | zh-Hant 表 | 检测器繁体中文 | 繁体中文 | 语言子句 |
| 其余 15 种 | 目标表 | 检测器目标语言 | 目标语言 | 语言子句 |
| 检测器未遵守语言要求 | 目标表 | 检测器实际输出（可能仍是中文） | 混合 | **无服务器兜底**；探针/验收把关（§6.2） |
| `event` 非法/缺失 | — | — | — | 丢弃回复并告警，日志带原始回复，不猜测 |
| `danger` + 空 `desc` | 目标/源表 | 空 | 仅 label | **不丢弃**：label-only 安全兜底 + 告警，保留危险语义 |
| 危险事件 | 目标/源表 | 目标/源语言 | 目标/源语言 | `event == "danger"` 仍触发冷却豁免/抢占/紧迫语气 |

### 4.9 下游影响

- **MyLife / 搜索**：`history.jsonl` 的 `text` 本地化后，lvdata chat 事件 `summary`/`payload.text`
  与 FTS 搜索均使用目标语言；用户切换语言后旧行不会重译（写入时语言固定）。
- **Hermes / OpenClaw**：chatlog watcher 读到本地化文本；其 LLM 可读多语言，无代码依赖。
- **chat_store restore**：`load_day` 恢复的是已本地化内容；resume 后 LLM 上下文与旧行语言可能
  不同（若本次 App 语言变了），可接受。
- **day_summary / audio 分析**：输入包含本地化 chat 文本；摘要语言不由本设计控制。
- **App 本地历史**：本期不补发 `transcription`，因此不会出现合成 user 行；只显示 AI 回复。
- **chatlog schema / lvdata 映射**：不改字段；`text` 只是内容变为目标语言。

### 4.10 安全与危险语义

- `event` 固定 `normal` / `danger`，是语言无关的控制字段；不再依赖 `"危险"`/`"danger"` 子串。
- 解析层严格校验：未知/缺失 `event` 直接丢弃，不把自由文本猜测成危险；大小写/空白先归一。
- `danger` 事件即使 `desc` 为空也不丢弃，走 label-only 安全兜底；`normal` 空描述才丢弃。
- 环境 `desc` 只保留在 user 行，不重复注入 system 注记：环境内容来自不可信音视频，
  避免把不可信文本提升到 system 权限（提示注入面）。
- 危险三条路径（冷却豁免、抢占、紧迫语气）只依赖 `event == "danger"`，不受目标语言影响。
- 检测器 prompt 的 `event` 约束与 `desc` 语言约束必须分开写，禁止把 `event` 翻译成目标语言。

### 4.11 可观测性

- 日志保留 `event`（normal/danger）、`desc[:80]`、目标语言、session_id；
- 检测器 prompt 组合时记录 target language（debug/info）；
- `NONE` 走 debug；非法 event / `normal` 空 desc 走 warning；`danger` 空 desc 走
  warning 但继续投递。warning 必须带原始回复截断（含 event/desc 原文）和 session_id，
  便于调试；
- 不新增 metric 为硬要求；实现时可按需要加。

---

### 4.12 Pipeline chat 的 LLM 输出语言规则（已实施，2026-10-28）

> 本期只改 chat mode；omni mode 的 system prompt / 输出语言不改。

#### 4.12.1 需求

1. 优先使用**用户当前提问的语言**，或用户**明确要求用于回答的语言**，作为 LLM 回答语言；
2. 无法从用户当前消息判定时，使用 `session.start.language`（空/未知 → `en`）；
3. 覆盖 pipeline chat mode 的**普通回合**（`_pipeline_mode == "full"`，ASR→LLM→TTS）；
   主动回合继续使用 §4.6；**omni 本期不改**；
4. 不引入服务器语言检测/翻译，不依赖 ASR `result.language`。

#### 4.12.2 现状（codebase 事实）

- **pipeline chat 普通回合**：`_start_thinking` 非 translate 分支组装
  `self._system_prompt` + 可选 `event_note` + WorkingMemory 历史
  （`orchestrator.py:1622-1644`）；`SessionContext.language`（`context.py:26`）
  进入输出语言规则。回复语言按 §4.12 规则选择。
- **主动回合**：av/cam 的 system 注记末尾已追加
  `请使用 {language_name} ({code}) 回复。`（`orchestrator.py:1047-1062`、
  `2251-2255`，`proactive_i18n.py:107-117`），语言来自 `SessionContext.language`，
  空/未知 → `en`（§4.6）。
- **omni**：本期不改 system prompt / 输出语言规则；其 system prompt 仍由
  `_enter_omni_mode` 直接使用 `self._omni_system_prompt`（`orchestrator.py:920-940`）。
- **translate**：有独立的 my/local language 指令（`_build_translate_instruction` /
  auto 模式），不属于本节范围。
- ASR `result.language` 在当前 active provider 下通常为空，且**不作为**目标语言
  （§2.2；`docs/agent_multilingual.md` D1）。

#### 4.12.3 规则（优先级从高到低）

1. **用户当前消息明确要求用于回答**的语言（如“用英文回答”“reply in Japanese”）；
2. **用户当前消息使用的语言**；
3. `session.start.language` —— **当前消息语言无法判定时唯一回落值**（现有 server
   语言表 canonical code；空/未知/非法 → `en`；App 当前上报 16 个 code，表内 `tr`
   为已下线残留，见 §2.1）。

补充口径：

- 只看**当前消息**，不因历史消息或 system prompt 的语言改变。
- 当前消息过短/语言中性（如“ok”“2”“...”）或混合语言无法判定 → 第 3 条。
- “明确要求”由 LLM 理解；服务端不解析、不落库；要求仅在当前消息/当前请求生效，
  之前消息中的要求不延续（与 agent relay 口径一致）；要求可以是现有语言表之外的语言。
- 注入 prompt 的只有 `resolve_proactive_language` 从固定表解析出的 name/code；
  原始客户端 `language` 字符串不进入 prompt（未知/非法 → `en`），不形成新的
  prompt 注入面。
- 服务端不做语言识别（不使用 ASR `result.language`），不做翻译/后处理。
- **主动回合没有“用户提问”，不注入本通用规则**：继续走 §4.6 的
  `请使用 session 语言回复` 指令（等价于第 3 条），不改变主动对话本地化方案。
  这样可避免把不可信的检测器 `desc`（环境文本）当成“用户当前消息”的语言依据或
  语言要求。

#### 4.12.4 prompt 文案

使用英文规则（system prompt 主体为英文；避免规则文本自身把输出带向中文）：

```text
Reply language rules (highest priority first):
1. If the user's current message explicitly asks you to reply in a specific language (for example "reply in English"), reply in that language.
2. Otherwise, reply in the language used by the user's current message.
3. If the current message is language-neutral, too short, or its language cannot be determined, reply in the default language configured for this session: {language_name} ({code}).
Do not choose the reply language from earlier conversation history or from the language of this system prompt.
```

第 3 条的 `{language_name} ({code})` 就是 `session.start.language` 解析后的值
（`resolve_proactive_language`；空/未知/非法 → `en`），不是模型自行选择或默认英文；
prompt 里用 “default language configured for this session” 表述，避免与对话历史语言混淆。
`{language_name}` 复用 `proactive_i18n.LANGUAGE_NAMES`（§附录 B）；所有支持的 code
统一按同一规则处理。

#### 4.12.5 注入点

| 模式 | 注入方式 | 生命周期 |
|---|---|---|
| pipeline chat 普通回合 | `_start_thinking` 非 translate 且 `event_note` 为空：`system_content = base + "\n\n" + rule` | 每轮请求组装；规则随 `self._ctx.language` 读取 |
| pipeline chat 主动回合（`event_note` 非空） | **不注入通用规则**，沿用 §4.6 的回复语言指令 | 每轮注记 |
| omni | **不改**（system prompt 维持 `self._omni_system_prompt`） | — |
| translate / transcribe / asr_only / lecture | 不改 | — |

- 不修改 `self._system_prompt` 共享字符串；按使用处拼接，避免跨会话污染。
- pipeline chat 普通回合每轮都带规则（system prompt 字符串稳定，利于前缀缓存）。
- 非 translate 且无 `event_note` 的分支也覆盖内部 `asr_llm` 调试模式；对生产 chat 无
  行为影响。
- 主动回合的事件注记保留现有回复语言指令（最后一条，优先级最高）；它是 §4.6 的
  更具体注记，与 §4.12 通用规则相互独立，本设计不改变其行为。

#### 4.12.6 边界

| 场景 | 行为 |
|---|---|
| chat：用户中文提问，session=`en` | 中文回复（规则 2 > 3） |
| chat：用户英文提问，session=`zh-Hans` | 英文回复（规则 2） |
| chat：用户说“用英文回答”，session=`zh-Hans` | 英文回复（规则 1） |
| chat：用户“ok/2”等中性短句，session=`ja` | 日文回复（规则 3） |
| chat：历史英文、当前中文，session=`en` | 中文回复（只看当前消息） |
| chat：主动 av/cam 回合（无用户提问） | session 语言（现有指令） |
| omni | 本期不改，维持现状 |
| session 空/未知 code | `en` |
| 用户要求语言表之外的语言 | 遵守用户要求（规则 1）；语言表不扩展 |
| 模型不遵守规则 | 无服务器兜底；真实探针/真机验收把关（§6.4） |

#### 4.12.7 决策（已定）

| # | 决策 | 建议 |
|---|---|---|
| D-O1 | 优先级 | 用户明确要求 > 当前提问语言 > `session.start.language` > `en` |
| D-O2 | 语言识别方式 | 只靠 LLM 读当前消息；服务端不做 NLP，不使用 ASR language |
| D-O3 | prompt 语言 | 英文规则 + 语言名/code；避免规则文本自身语言偏置 |
| D-O4 | pipeline 注入时机 | 每轮 system 组装时拼接（不修改共享 prompt） |
| D-O5 | omni | 本期不改 system prompt / 输出语言规则 |
| D-O6 | 主动回合 | 保留现有 session 语言回复指令（无“用户提问”） |
| D-O7 | 未知 session code | `en`（沿用主动链路口径；原始字符串不进入 prompt） |
| D-O8 | 服务器翻译/后处理 | 不做 |
| D-O9 | 显式语言要求的有效期 | 仅当前消息/当前请求；不跨请求持久化 |

---

## 5. 变更清单（已实施）

| 文件 | 改动 |
|---|---|
| `services/session/proactive_i18n.py`（新） | 17 语言集合、固定 label 表、语言名表、`resolve_proactive_language()`、`build_desc_language_clause()`、`build_reply_language_directive()` / `append_reply_language_directive()` |
| `configs/dev/prompts/avdetector.txt`、`configs/test/prompts/avdetector.txt` | `event` 改为 `normal`/`danger`；desc 语言由 per-session 子句追加；更新输出格式 |
| `services/session/av_detector.py` | `_parse_reply` 严格校验 `normal`/`danger`（大小写归一）；danger 空 desc 不丢弃；`_run_check` 区分 NONE/非法回复并打 raw log；删除/替换 `is_danger_label`；日志字段改为 event |
| `services/session/orchestrator.py` | `_ensure_av_monitor` 组合语言子句；`_build_av_event_note` 用 `event == "danger"`、`[环境事件·系统注入]`、不重复 desc（desc 只在 user 行）并追加回复语言指令；av 行 content 用本地化 label + desc；cam 行 content 用本地化 `[相机画面]`，`_VIDEO_TURN_NOTE` 改 `[实时画面·系统注入]` 并追加回复语言指令；不补发 `_send_transcription`；危险判定改精确匹配 |
| `services/session/context.py` / `manager.py` | **不改**（`language` 已存储；空值只在主动文本解析层默认 `en`） |
| `configs/dev/server.yaml`、`configs/test/server.yaml` | `avdetector.generation.max_tokens: 128 → 256` |
| `services/tests/unit/session/test_proactive_i18n.py`（新） | 17 语言表/解析/prompt 子句/回复语言指令 |
| `services/scripts/probe_avdetector_multilingual.py`（新） | 17 语种真实检测器探针；输出 `/tmp/avdetector_multilingual_probe.json` |
| `services/tests/unit/session/test_orchestrator_avdetector.py` | event normal/danger、本地化行、系统注记 `[]`、危险语义、danger 空 desc 兜底、空/未知默认英文（含语言子句） |
| `services/tests/unit/session/test_av_monitor.py` | 删除 `is_danger_label` 旧用例；`normal`/`danger` 归一与非法 event 丢弃；`_run_check` 告警日志（raw 含 event/desc、session_id） |
| `services/tests/unit/avdetector/providers/test_alibaba_avdetector.py` | 新输出格式/prompt 透传 |
| `services/tests/integration/orchestrator/test_video_frame_processing.py` | cam 首帧 `[相机画面]` 本地化、三件一体、无 `_send_transcription` |
| `docs/ws-protocol-design.md` | `language` 字段描述更新为主动对话历史文本、chat 普通回合输出语言回落与 agent relay 用户可见输出语言 |
| `docs/session_start_improvments.md` | 更新 server 消费口径（主动对话 + chat 输出语言回落） |
| `docs/fd_pipeline_avdetector.md` / `docs/fd_pipeline_video_process.md` | 合成行文本/event/注记的语言行为 |
| 本文 | 已更新为实施状态；§7 保留验收门 |

**§4.12 chat LLM 输出语言规则（已实施；omni 不改）**

| 文件 | 改动 |
|---|---|
| `services/session/proactive_i18n.py` | 新增 `build_output_language_rule()`：先 `resolve_proactive_language`，只注入表内 `language_name (code)`，输出 §4.12.4 英文规则；空/未知/非法 → `en` |
| `services/session/orchestrator.py` | `_start_thinking` 非 translate 且无 `event_note` 的普通回合在 system 末尾拼接规则；主动回合不注入；omni 不改 |
| `services/tests/unit/session/test_proactive_i18n.py` | 规则文案/优先级/空值/未知 code；所有支持 code 统一处理 |
| `services/tests/unit/session/test_translate_mode.py` | chat 普通回合含规则、translate 不含（回归） |
| `services/tests/integration/orchestrator/conftest.py` / `test_chat_language_rule.py` | Fake LLM 记录 messages；断言 chat system 含规则、主动回合/translate 不含 |
| `services/scripts/probe_llm_output_language.py`（新） | 真实 LLM chat 探针：提问语言/显式要求/中性短句/session fallback |
| `docs/fd_pipeline_llm_v2.md` | 已同步 chat 输出语言规则（§3.3） |

不新增协议字段、DB 列、S2C 消息类型；本期不补发 `transcription`（§4.7）。

---

## 6. 测试计划

### 6.1 单测

- `resolve_proactive_language`：17 个 code 原样返回；`""`/`zh`/`en-US`/None → `en`。
- `build_desc_language_clause`：`zh-Hans` 返回空；`zh-Hant` / en / ja / ar / th / vi 返回含
  language_name + code 的子句；子句明确要求 `event` 不翻译。
- 固定 label 表：17 个 code 全覆盖；未知 → 默认英文；全部使用 ASCII `[]`；cam/env 非空。
- `_parse_reply`：
  - `normal` / `danger` 通过；大小写/空白归一后（如 `DANGER` / `Normal`）也通过；
  - `NONE` / 空 → None；
  - 旧式自由文本 event（如 `咳嗽` / `Dangerous behavior`）→ None；
  - 缺 event / 非法 JSON → None；
  - `normal` + 空 desc → None；
  - `danger` + 空 desc → `{"event":"danger","desc":""}`（不丢弃）。
- `_run_check`（monitor）：
  - `NONE` → debug，无事件；
  - 非 `NONE` 解析失败 → warning，含原始回复截断（含 event/desc 原文）和 session_id（caplog）；
  - `danger` + 空 desc → warning 但继续投递事件（label-only 兜底）。
- `event` 危险语义：`danger` 触发冷却豁免/抢占/紧迫注记；`normal` 不触发；不再依赖中文/英文子串。
- `_on_av_event`（假 monitor/检测器）：
  - `language="en"` → history/chat_store/recording 三处 content 均为英文 label + 英文 desc；
  - `language=""` / 未知值 → 默认英文 label + 英文 desc；
  - `language="zh-Hans"` → 中文 label + 中文 desc（除 `[]` 括号变化）；
  - `zh-Hant` → 繁体 label + 繁体 desc；
  - 不新增任何翻译 LLM 调用；
  - `danger` + 空 desc → content 仅 `ENV_PREFIX[lang]`，三件一体仍写；注记不含 desc（desc 只在 user 行）；
  - `_build_av_event_note` 前缀为 `[环境事件·系统注入]`，非 `zh-Hans` 时末尾带目标语言回复指令；`desc` 不进入 system 注记（只在 user 行）。
- cam 首帧：
  - `language="en"` → content 为 `[Camera view]`；三件一体一致；
  - `language=""` / 未知值 → `[Camera view]`（默认英文）；
  - `language="zh-Hans"` → `[相机画面]`；不触发检测器语言子句/翻译调用；
  - `_VIDEO_TURN_NOTE` 前缀为 `[实时画面·系统注入]`，非 `zh-Hans` 时末尾带目标语言回复指令。
- 不补发 S2C：两条主动链路均无 `_send_transcription` 调用；实时会话只收到 `ai_text`。

### 6.2 探针（实施前/验收）

- 脚本：`services/scripts/probe_avdetector_multilingual.py`；产物
  `/tmp/avdetector_multilingual_probe.json`。已实测 17/17 `event=normal`、`desc` 目标语言、
  `max_tokens=256` 无截断（output_tokens 13–22）。
- 17 语言全量（重点覆盖 en / zh-Hant / ja / ar / th / vi）不同 `desc`
  （咳嗽、喷嚏、危险行为、画面变化）：
  - `event` 始终是 `normal` / `danger`；
  - `desc` 是目标语言；
  - 危险事件 `event == "danger"`；
  - 验证 `max_tokens=256` 下无截断、JSON 格式合规；记录耗时。
- **发布门槛**：若 17 语种探针出现 `desc` 非目标语言、非法 `event` 或截断，不发布；
  先调 prompt/模型（或重新评估方案），不静默接受混合语言输出。
- 回复语言：默认已追加目标语言指令；17 语种真实探针全部回复目标语言。
- TTS：目标语言发音/音色可用。
- 老客户端回归：不传 `language` → 默认英文（英文 `[]` label + 英文 desc + 语言子句）；
  `zh-Hans` → 中文且无语言子句。
- MyLife：本地化文本出现在 chat 事件与搜索；Hermes/OpenClaw 能正常读取。

### 6.3 手工验收

- Android/iOS 分别把 App 语言切到 en、ja、ar、th、vi，触发 avdetector 声音事件与 cam 首帧，
  检查 MyLife chat 气泡/搜索文本、AI 回复语言/TTS；实时会话应只有 AI 回复（不出现合成 user 行）。
- `zh-Hans` 中文回归；空/未知默认英文（与 App 默认一致）；括号风格变化为有意变更。

### 6.4 chat LLM 输出语言规则（§4.12，已实施；omni 不改）

**单测**

- `build_output_language_rule`：
  - 所有支持的 code 均含对应 `language_name (code)`，统一处理、无源语言例外；
  - 空/未知/非法 code → `English (en)`；
  - 规则文本包含“显式要求 > 当前消息语言 > 会话语言”的优先级与“历史不决定语言”。
- pipeline chat：
  - 普通回合（无 `event_note`）system 含规则；translate 分支不含；
  - 主动回合（`event_note` 非空）不注入通用规则，只保留 §4.6 回复语言指令；
  - 即使主动回合的 `desc` 含外语/语言要求文本，也不改变 session 语言的回复指令；
  - 规则不写入 history / chat_store / chatlog。
- omni：本期不改，`OmniModeEngine` 的 system prompt 保持现状（回归断言）。

**真实 LLM 探针**

脚本：`services/scripts/probe_llm_output_language.py`（新）。矩阵：

| 场景 | 期望 |
|---|---|
| session=`en`，用户中文提问 | 中文 |
| session=`zh-Hans`，用户英文提问 | 英文 |
| session=`zh-Hans`，用户“用英文回答” | 英文 |
| session=`ja`，用户“ok/2”等中性短句 | 日文 |
| session=`en`，历史英文、当前中文 | 中文 |
| session 空/未知 + 用户中性短句 | `en` |

- 覆盖 en / zh-Hans / zh-Hant / ja / ar / th / vi；
- 记录原始回复，人工复核语言（脚本级语言检查只作辅助）；
- **发布门槛**：若显式要求或提问语言被稳定忽略，先调 prompt（或重新评估方案），
  不静默接受混合语言。

**实施结果**：2026-10-28 已运行 `scripts/probe_llm_output_language.py`，12 个场景
（7 个中性回落 + 提问语言覆盖 + 显式要求 + 历史忽略 + 空 session fallback）脚本检查
全部通过；`zh-Hant` 人工确认为繁体，`vi` 等拉丁语言按原文复核。报告：
`/tmp/llm_output_language_probe.json`。

**真机验收**

- Android/iOS 切 App 语言后，分别用不同语言提问/显式要求语言，检查 chat 的文本/TTS 语言；
- chat TTS 语音/音色随目标语言可用；
- omni 仅做现状回归（system prompt 不变）。

---

## 7. 外部依赖与验收门

设计决策已收敛；以下是落地前需要完成的外部确认或验证，不是新的方案分叉：

| 项 | 内容 | 处理 |
|---|---|---|
| 回复语言探针 | 默认目标语言指令在 17 语种下是否生效 | 已默认追加；17 语种实测全部通过；TTS 语言待真机验收 |
| 检测器语言/格式探针 | 17 语种 `desc` 语言、`event` 合法、`max_tokens=256` 无截断 | 不达标不发布；先调 prompt/模型，不静默接受混合输出（§6.2） |
| 17 语言集合防漂移 | server 副本与 App 的两处事实源 | 固定 17 项测试 + 注释指向 Android/iOS 源文件；跨端自动校验另议 |
| LLM 输出语言规则 | chat 在“提问语言≠session 语言”“显式要求”“中性短句”下是否遵守 | 已实施；真实 LLM 探针通过（§6.4） |

---

## 8. 非目标复述

- 不改 ASR/翻译/TTS 主链路；不改 lecture；**omni 本期不改**（system prompt /
  输出语言维持现状）；
- 不做服务端语言识别（不消费 ASR `result.language`）、不做服务器翻译/后处理；
- 不改协议 schema、DB schema、客户端本地化资源；
- 不补发 S2C `transcription`：实时会话/App 本地历史不展示合成 user 行（§4.7）；
- 不服务器翻译 agent job 结果（`[Agent job result]`）；agent relay 已按自身语言规则
  生成目标语言文本（`docs/agent_multilingual.md`）；
- 不做历史回填/多语言并存/运行中语言切换；
- 不改变危险事件的安全语义与门控顺序。

---

## 附录 A：固定 label 文案（AI 翻译稿）

> 以下为 AI 翻译稿，直接作为 server 静态表实现文案，不在运行时调用 AI；
> 如后续产品统一术语，再批量更新。
> 所有 label 使用 ASCII `[]`；非 CJK 的 env 前缀末尾带一个空格，表中 `␠` 表示半角空格；
> cam label 无 desc，不加尾随空格。`zh-Hans` 为源语言。

| code | cam label | env label |
|---|---|---|
| `en` | `[Camera view]` | `[Environment event]␠` |
| `zh-Hans` | `[相机画面]` | `[环境事件]` |
| `zh-Hant` | `[相機畫面]` | `[環境事件]` |
| `ja` | `[カメラ映像]` | `[環境イベント]` |
| `ko` | `[카메라 화면]` | `[환경 이벤트]` |
| `ru` | `[Изображение с камеры]` | `[Событие окружения]␠` |
| `de` | `[Kamerabild]` | `[Umgebungsereignis]␠` |
| `fr` | `[Vue caméra]` | `[Événement ambiant]␠` |
| `it` | `[Vista fotocamera]` | `[Evento ambientale]␠` |
| `es` | `[Vista de la cámara]` | `[Evento del entorno]␠` |
| `pt` | `[Imagem da câmera]` | `[Evento do ambiente]␠` |
| `tr` | `[Kamera görüntüsü]` | `[Ortam olayı]␠` |
| `hi` | `[कैमरा दृश्य]` | `[पर्यावरण घटना]␠` |
| `id` | `[Tampilan kamera]` | `[Peristiwa lingkungan]␠` |
| `th` | `[ภาพจากกล้อง]` | `[เหตุการณ์สภาพแวดล้อม]␠` |
| `vi` | `[Hình ảnh camera]` | `[Sự kiện môi trường]␠` |
| `ar` | `[عرض الكاميرا]` | `[حدث بيئي]␠` |

## 附录 B：语言名与检测器语言子句

| code | language_name |
|---|---|
| `en` | English |
| `zh-Hans` | Simplified Chinese |
| `zh-Hant` | Traditional Chinese |
| `ja` | Japanese |
| `ko` | Korean |
| `ru` | Russian |
| `de` | German |
| `fr` | French |
| `it` | Italian |
| `es` | Spanish |
| `pt` | Portuguese |
| `tr` | Turkish |
| `hi` | Hindi |
| `id` | Indonesian |
| `th` | Thai |
| `vi` | Vietnamese |
| `ar` | Arabic |

检测器语言子句（`lang != zh-Hans` 时追加；空/未知已解析为 `en`）：

```text
语言要求：desc 字段必须使用 {language_name} ({code}) 书写，
不超过40个字符；event 仍只能是 normal 或 danger，不要翻译 event。
输出格式不变，仍是一行 JSON。
```

检测器基础输出契约（`configs/dev/prompts/avdetector.txt`）：

```text
输出格式：一行 JSON：{"event":"normal","desc":"<不超过40个字符的客观描述>"}；
危险行为（有人摔倒、小孩子的行为有潜在危险等）→ "event":"danger"；
其余值得关注的事件 → "event":"normal"。都没有值得关注的内容 → 只输出：NONE。
event 只能是 normal 或 danger，不得输出其它值，也不得翻译。
```
