# Pipeline Transcribe Mode — 设计文档

> 关联文档：[`fd_pipeline_translate_mode_v2.md`](fd_pipeline_translate_mode_v2.md)（`session.change` 动态切换基线）、
> [`fd_pipeline_translate_lecture.md`](fd_pipeline_translate_lecture.md)（lecture 同传，usecase 先例）、
> [`android_audio_capture.md`](android_audio_capture.md)（**§3.3 采集源切换；本文 §7 Phase 2 已落地**，语义以该文为准——
> 本文 §7.1「运行中不可换源、新会话生效」与 §7.2「切回不改变采集链路」两处预留语义已被修订）。
> 本文合并 `fd_pipeline_transcribe_mode-A.md` / `fd_pipeline_transcribe_mode-B.md` 两稿评审结论
> （评审要点：行格式采用 `source="transcribe"` 第三方案；agent relay 屏蔽仅排除 transcribe；
> 远场风险升为 gating 验收项；补充中段切换/SM 复位失败/客户端失败路径三处盲区）。
> 本文所有代码锚点均以当前 codebase 核实（server: `services/session/orchestrator.py`、
> `services/recording/**`；client: `apps/android/**`）。
>
> **编者注（2026-10-03）**：`session.change` 拒绝错误码已统一为 `error(1009)`
> （原 4001/4003；客户端回落仅认 1009）。变更方案与新旧组合的过渡说明见
> [`session_mode_change.md`](session_mode_change.md)。本文相关行文已同步订正。

## 1. 背景与目标

在 Conversation Screen 增加**转录模式（transcribe mode）**：实时转录现场声音
（会议、课堂、演讲等），只出不进——服务端只做 ASR，不做 LLM、不做 TTS，
把识别文本实时回传客户端展示，并将 final 文本**单独落盘保存**。

| | Chat Mode | Translate Mode | **Transcribe Mode（新增）** |
|---|---|---|---|
| 切换方式 | session.start 默认 | `session.change(mode=translate)` | `session.change(mode=transcribe)` |
| ASR → transcription | 照常发送 | 照常发送 | 照常发送 |
| LLM | 完整对话 | 逐轮翻译 | **不调用** |
| TTS / ai_audio | 播报回复 | 播报译文 | **不调用** |
| ai_text | LLM 完整回复 | 翻译结果 | **不产生** |
| WorkingMemory / chat_store | 写入 | 不写入 | **不写入**（§4.3） |
| recording 文本落盘 | `chat/history.jsonl` | **`chat/translate.jsonl`**（source="translate"） | **`chat/transcribe.jsonl`**（§4.4） |
| 轮末状态 | thinking→speaking→listening | 同左 | 停留 LISTENING，`status(listening, asr_final)` |
| agent relay utterance | 发布 | 发布 | **不发布**（§4.4，D7） |

### 1.1 目标

- `session.change` / `session.changed` 的 `mode` 枚举新增 `transcribe`；`DialogOrchestrator` 支持 ASR-only 转录。
- 客户端「同声翻译」section 增加「转录模式」开关，与对话/听众模式三向互斥。
- 开启转录时弹出「原声 / 语音」录音源选择对话框（**Phase 1**：选择仅记录到本地状态，
  采集链路统一沿用现状 `VOICE_COMMUNICATION`；Phase 2 才接入真实采集，见 §7）。
- 客户端处于 transcribe mode 时，final transcription 持久化为带 `(转录)` 后缀的本地消息
  （与 translate mode 的 `(原文)` 同款"存储时注入"策略）。
- 服务端把 transcribe 模式下的 final 转录文本落盘到
  `{base_dir}/{device_uuid}/{recording_session_id}/chat/transcribe.jsonl`——
  与 chat 记录 `history.jsonl` 同目录、同 JSONL 格式，**本期只保存，无任何消费方**（§4.5）。

### 1.2 非目标（Out of Scope）

- **Omni 路径不支持**：`OmniOrchestrator._dispatch` 当前没有 `session.change` 分支，
  该消息在 omni 下被静默忽略——维持现状，零改动。客户端不在 omni 场景展示该开关。
  **（2026-09-27 更新）**：omni 已改为会话内模式（`session.change(mode=omni)`），
  `OmniOrchestrator` 删除、由 `DialogOrchestrator` 第四模式 + `OmniModeEngine` 承接——本节结论作废，
  现状见 `docs/fd_pipeline_omni_mode.md`。
- **transcribe.jsonl 的消费**：不做 watcher、不进 lvdata-mcp 扫描、不做 restore、
  不提供客户端/协议出口（§4.5.4）。
- **录音源选择的实际生效**（Phase 2，见 §7）。
- iOS / Web（`apps/ios` 当前仅占位）。

---

## 2. 现状关键代码锚点（实现基线）

### 服务端

| 位置 | 内容 |
|------|------|
| `services/session/orchestrator.py:242-246` | `_handle_session_start`：每个新 session 无条件重置 `_pipeline_mode = "full"`（:244） |
| `services/session/orchestrator.py:411-492` | `_handle_session_change`：mode 白名单（:422-427，非法回 error 1009）→ lecture 切出（:442-446）→ chat/translate 分支（:448-474）→ `session.changed` 回声（:484-490）+ `status(self._ctx.state.value, "mode_changed")`（:492，**携带当前 state**） |
| `services/session/orchestrator.py:434-439` | `target_lecture` 判定：要求 `new_mode == "translate"` 且 `usecase == "lecture"`——`mode=transcribe + usecase=lecture` 组合不会进入 lecture |
| `services/session/orchestrator.py:476-482` | `effective_usecase`：非 translate 目标落 `else: None`——transcribe 回声不携带 usecase，无需改动 |
| `services/session/orchestrator.py:496-504` | `_enter_lecture_mode`：进入 lecture 先 `_cancel_pipeline()` + `_cancel_asr_poll()` + SM 复位 LISTENING（取消在途 pipeline 的既有先例；transcribe **不照搬 ASR poll 关闭**，见 §4.2） |
| `services/session/orchestrator.py:712-806` | `_handle_audio` 轮末：final transcription / history 写入 / asr_only 分支（:782-787，只发 `status(listening,"asr_final")` 不启 thinking）/ agent relay `publish_utterance`（:797-806，**现状无条件发布**，chat/translate/asr_only 均发） |
| `services/session/orchestrator.py:743-753, 1425-1434` | WorkingMemory + chat_store 写入点两处，guard 均为 `pipeline_mode != "translate"` |
| `services/session/orchestrator.py:1313-1339` | `_send_transcription`：发送 + recording `on_chat_text(role="user")` tap（:1328-1339，final 才落盘，任何模式无条件） |
| `services/session/orchestrator.py:1473-1488` | `_cancel_pipeline`：取消在途 LLM/TTS pipeline task |
| `services/recording/sink.py:8-55, 58-83` | `RecordingSink` Protocol（orchestrator 唯一接触面）+ `NoOpRecordingSink`（recording 关闭时兜底）；`on_chat_text` 已带 `source: str \| None` 参数 |
| `services/recording/session.py:33-44` | `_RecordingItem`：`kind: Literal["audio","video","asr_final","llm_final","close"]`，已有 `source: str = ""` 字段（:43） |
| `services/recording/session.py:104-105, 129-131` | `start()/attach()`：打开 `ChatLogWriter(storage_path / "chat" / "history.jsonl")` |
| `services/recording/session.py:209-228, 244-245, 303-322` | `on_chat_text` 入队 → `_run_writer` 分发（:244）→ `_handle_text` 写 `history.jsonl`（:309-317 透传 `source=item.source or None`）+ `chat_messages += 1`（:318，无条件计数） |
| `services/recording/session.py:361-368` | `_flush_all`：writer task 收尾关闭 chat writer |
| `services/recording/chatlog.py:29-61` | `ChatLogWriter.write`：`role: str` 必填（:32）；`source: str \| None = None`（:38），非 None 才写入 `"source"` 字段（:54-55）。**零改动即可支持 transcribe 行格式（D6）** |
| `services/recording/service.py:49-75, 101-141` | `RecordingService`：per-device per-UTC-day session 注册表，UTC 日切换 finalize 旧 session → 目录与文件自然按日切分 |
| `services/agent_relay/chatlog_watcher.py:56` | chatlog 路径规则 `{base_dir}/{device_id}/{recording_session_id}/chat/history.jsonl`；agent relay 只 tail `history.jsonl` |
| `packages/api-schema/c2s/session-change.json` | `mode` enum `["chat","translate"]`（:24）；payload 仅 `mode` required |
| `packages/api-schema/s2c/session-changed.json` | `mode` enum `["chat","translate"]`（:24） |
| `packages/sdk-python/.../generated/protocol_models.py:65-68, 102-105` | 生成枚举 `ClientSessionChangeMode` / `ServerSessionChangedMode` |
| `scripts/generate_python_api.py` / `scripts/generate_kotlin_api.py` | schema → Python/Kotlin DTO 生成脚本；`make generate-android-api`、`make check-android-api-generated` |

### 客户端（Android）

| 位置 | 内容 |
|------|------|
| `apps/android/.../ui/screens/conversation/AvatarCustomizationSheet.kt:66-85` | 「同声翻译」Group：`对话模式` / `听众模式` 两个互斥 `SwitchRow` |
| `apps/android/.../ui/screens/conversation/ConversationScreen.kt:570-596` | sheet 调用处形参接线；:591 `conversationSceneEnabled = (isTranslateMode && !isLectureMode) \|\| showLanguagePicker`（picker 打开期间开关保持 on 的既有模式） |
| `apps/android/.../ui/screens/conversation/ConversationScreen.kt:641-661` | `LanguagePickerSheet` 对话框先例（转录源对话框复用此模式） |
| `apps/android/.../ui/screens/conversation/ConversationScreen.kt:747-766` | avatar 右上角 translate 徽标（26dp 圆形 BrandIndigo） |
| `apps/android/.../ui/screens/conversation/ConversationViewModel.kt:272-321` | ToggleTranslateMode / ConfirmTranslateLanguage / lecture 对称逻辑（**关闭任一模式全复位 + `sendSessionChange("chat")`**） |
| `apps/android/.../ui/screens/conversation/ConversationListenerAdapter.kt:92-122` | `onTranscription`：translate 模式 final 持久化加 `" (原文)"` 后缀（:104）→ USER 消息 |
| `apps/android/.../ui/screens/conversation/ConversationListenerAdapter.kt:209-212` | `onSessionChanged`：服务端回声回写 `_translateMode` / `_lectureMode` |
| `apps/android/.../ui/screens/conversation/ConversationListenerAdapter.kt:214-220` | `onError(code, message, fatal, recoverable)` → `_errors` SharedFlow（`ConversationError.ServerError` / `NetworkError`）——模式切换失败回落路径复用此通道（§6.9） |
| `apps/android/.../ui/screens/conversation/ConversationListenerAdapter.kt:79-90` | `clearSession()`：复位 mode StateFlow |
| `apps/android/LivaChatSDK/.../internal/session/SessionManager.kt:179-206` | SDK `sendSessionChange`：mode 字符串→生成枚举映射（:181-188），未知值丢弃并告警 |
| `apps/android/.../media/AudioCaptureController.kt:105-106` | `AudioRecord.Builder().setAudioSource(VOICE_COMMUNICATION)`（16k 单声道 PCM16），采集由 App 持有、向 SDK 推 PCM |
| `apps/android/.../media/ConversationCoordinator.kt:74` | 会话起播处 `audioCapture.start()` |

---

## 3. 协议变更

### 3.1 C2S `session-change.json` — mode 枚举扩展

`payload.mode.enum` 增加 `"transcribe"`：

```json
"mode": {
  "type": "string",
  "enum": ["chat", "translate", "transcribe"],
  "description": "Target pipeline mode. 'transcribe' = ASR-only live transcription (no LLM, no TTS; pipeline only, ignored by omni)."
}
```

- payload 本就只 require `mode`：transcribe 不需要语言对，发送方**不携带**
  `my_language` / `local_language` / `usecase`；服务端收到也一律忽略。

### 3.2 S2C `session-changed.json` — mode 枚举扩展

`payload.mode.enum` 同样增加 `"transcribe"`。transcribe 确认帧只携带 `mode`，
不携带语言与 usecase（与 chat 回声一致，既有 `effective_usecase` 的 `else: None` 分支自动覆盖）。

### 3.3 不变化的 schema / 消息

- **`s2c/transcription.json` 及 transcription 消息流完全不变**：interim/final 照常推送，
  客户端无需区分模式即可接收。（其描述文案 "final … trigger THINKING state" 在
  transcribe 下不成立——纯文档措辞，不改 schema。）
- `s2c/status.json` / `common.json`：复用既有 `reason="mode_changed"` 与
  `reason="asr_final"`，无新枚举。
- 无新 C2S/S2C 消息类型、无新字段。

### 3.4 DTO 重新生成（schema 为单一事实来源，禁止手改生成物）

```bash
# Python DTO（后端消费；sdk-python 为 editable 安装，生成后无需重装）
python3 scripts/generate_python_api.py --schema packages/api-schema \
  --out packages/sdk-python/src/livachat_protocol/generated
# Kotlin DTO（Android SDK 消费）+ CI 一致性校验
make generate-android-api
make check-android-api-generated
```

生成产物自动新增枚举值：Python `ClientSessionChangeMode.TRANSCRIBE` /
`ServerSessionChangedMode.TRANSCRIBE`；Kotlin `ClientSessionChangeMode.TRANSCRIBE`。

### 3.5 兼容性

`mode` 为必填枚举：旧客户端不发 `transcribe` 不受影响；服务端收到未知 mode 按白名单
拒绝——错误码自 2026-10-03 起统一为 `error(1009)`（pre-1009 服务端回 `error(4001)`；
版本错位组合的过渡行为见 [`session_mode_change.md`](session_mode_change.md) §5）。

---

## 4. 服务端设计

### 4.1 设计决策

| # | 决策点 | 方案 | 理由 |
|---|--------|------|------|
| D1 | `_pipeline_mode` 取值 | 新增显式值 `"transcribe"`，不复用 `"asr_only"` 字符串 | 协议 mode 与内部 mode 一一对应（同 `translate` 先例），日志/调试语义清晰；为转录专属行为留出分叉点 |
| D2 | LLM/TTS 关闭方式 | 轮末门控扩为 `in ("asr_only", "transcribe")`，不调度 `_start_thinking` | 复用已被验证的 asr_only 行为路径（含静音轮、barge-in 边角），改动最小 |
| D3 | 进入 transcribe 时在途 pipeline | **取消**（`_cancel_pipeline()`）+ SM 复位 LISTENING | transcribe 语义是"静默旁听"：不应有 AI 语音残留播完盖过现场，且客户端在 transcribe 态收到的裸 ai_text 无法归类（无 `(原文)/(译文)` 语境）。沿用 `_enter_lecture_mode`（:498-504）先例。**注意**：`:492` 的 status 回声携带 `self._ctx.state.value`（当前 state，非字面 "listening"）——"回声确定携带 listening"依赖 SM 复位成功；复位被 reject 的边角见 §4.6.7 |
| D4 | WorkingMemory / chat_store | **不写入**（guard 扩为 `not in ("translate","transcribe")`） | 转录内容是现场环境语音（多为第三方发言），写入会以 `role="user"` 进入 LLM 上下文并挤占 10 轮窗口、经 `load_day` 跨会话恢复持续污染对话；与 translate 不记录的先例一致。持久化需求由 §4.5 的 transcribe.jsonl 承担。中段切换的例外见 D10 |
| D5 | 转录文本持久化 | recording 层新增 **`chat/transcribe.jsonl`**，与 `history.jsonl` 同目录同格式，只写不读 | 需求：转录历史需要保存，但不混入对话记录。独立文件同时避免 agent relay 的 ChatlogWatcher（只 tail `history.jsonl`）被现场语音触发 |
| D6 | transcribe.jsonl 行格式 | 与 `history.jsonl` **完全同 schema**：`role="user"` + **`source="transcribe"`** | 评审第三方案（取代 A 稿"role 可选化省略字段"与 B 稿"裸 role=\"user\""）：`ChatLogWriter.write` 的 `source` 参数（chatlog.py:38, :54-55）与 `_RecordingItem.source`（session.py:43）已存在并贯通链路，**`ChatLogWriter` 零改动**；schema 完整保证未来消费工具复用 history.jsonl 解析路径；`source` 字段提供语义区分，文件名本身亦表明内容性质 |
| D7 | agent relay `publish_utterance` | transcribe 下**不发布**；**asr_only 行为不变** | 隐私语义一致性：D5 已堵住 chatlog watcher 通道（转录不进 history.jsonl），若 utterance 通道照常发布，同一批第三方现场语音一处堵一处放，语义不自洽。"转录模式"的用户心智是"只记录"，不是"让 agent 听会"；与会者语音流入 agent 总线缺乏授权基础。实现为 `if self._pipeline_mode != "transcribe"`——**只排除 transcribe，勿误伤 asr_only**（asr_only 现状即发布，:797-806，保持现状） |
| D8 | omni | 不支持、不报错、静默忽略（现状） | `OmniOrchestrator._dispatch` 无 `session.change` 分支，天然满足；客户端不在 omni 场景展示该开关 |
| D9 | lecture ↔ transcribe | 复用既有 lecture 切出逻辑（:442-446） | 任何非 lecture 目标（`target_lecture` 为 False）都会先关引擎 + SM 复位，零新增代码；ASR 于下一帧按既有惰性重开逻辑恢复 |
| D10 | 轮末模式判定时序 | **读"当时值"，无 turn 级模式快照**（:782 轮末读取当时的 `_pipeline_mode`）；切换处不做 turn detector reset，半截语音自然收尾 | 切换即时生效的有意语义。**已知的代价（显式接受）**：transcribe→chat 语音中段切换时，该 turn 的现场语音按新模式（chat）收尾——写入 history/chat_store 并可能进 LLM 上下文，与 D4 的防污染理由存在张力。接受理由：切换是低频用户主动操作，污染限于单个 turn 且由用户自己触发，下一轮即恢复；turn 级快照引入的状态复杂度不值当。若产品反馈此行为突兀，再评估快照方案。单测覆盖该时序（§10 用例 4） |

### 4.2 `_handle_session_change` — 增加 transcribe 分支

`orchestrator.py:422-427` 的 mode 白名单加入 `"transcribe"`；新增分支：

```python
if new_mode not in ("chat", "translate", "transcribe"):
    # ... 既有 1009 拒绝不变

# lecture 切出（:442-446）对 transcribe 目标自动生效，无需新增代码（D9）

if new_mode == "chat":
    # ... 既有分支不变（离开 transcribe 回 chat 即恢复 "full"；
    #     transcribe 期间没有在途 pipeline，无遗留任务需取消）
elif new_mode == "transcribe":
    # 立即静音：取消在途 LLM/TTS（D3）；ASR 流与 poll 保持——
    # 不换引擎，转录要继续（与 _enter_lecture_mode :499-500 不同，
    # 不调用 _cancel_asr_poll()）
    await self._cancel_pipeline()
    if self._ctx and self._ctx.state != SessionState.LISTENING:
        self._ctx.sm.transition_or_reject(SessionState.LISTENING)
    self._pipeline_mode = "transcribe"
    self._my_language = None
    self._local_language = None
elif new_mode == "translate":
    # ... 既有分支不变
```

回声 `session.changed(mode="transcribe")` + `status(<当前state>, "mode_changed")`
复用 :484-492 现有收尾逻辑（SM 已复位 → 正常路径携带 `listening`，见 D3/§4.6.7）。

### 4.3 轮末分支与 history / chat_store 排除

**轮末门控**（`orchestrator.py:782-787`）：

```python
if self._pipeline_mode in ("asr_only", "transcribe"):
    if meaningful or had_asr_final:
        await self._send_status("listening", "asr_final")
```

即：ASR final →（interim/final transcription 已由 `_poll_asr_results` /
轮末兜底照常发送）→ 直接回 `listening`，**永远不进入 `_start_thinking`**
（无 LLM → 无 ai_text / ai_audio / TTS）。轮末只在 `meaningful or had_asr_final`
时发 status——静音轮次自然静默，与现状一致。轮末通用 `_cancel_pipeline()`（:772-773）
对 transcribe 空跑无害。

**history / chat_store guard**（D4），两处由 `!= "translate"` 改为
`not in ("translate", "transcribe")`：

- `orchestrator.py:743-753`（`_handle_audio` 轮末 user 写入，代码起 :745）
- `orchestrator.py:1425-1434`（`_poll_asr_results` final user 写入）

assistant 写入点在 `_start_thinking` 内，transcribe 永不触达，无需改动。

### 4.4 agent relay 屏蔽（D7）

轮末 `publish_utterance`（:797-806）在 transcribe 下**不发布**——转录内容是现场语音，
不是"用户发言"，不应流向 agent 事件总线：

```python
if self._agent_publisher is not None and (meaningful or had_asr_final):
    # ↓ 追加条件：只排除 transcribe；asr_only 现状即发布，保持不动
    if self._pipeline_mode != "transcribe":
        asyncio.create_task(self._agent_publisher.publish_utterance(...))
```

配合 D5（转录不进 history.jsonl → 不触发 ChatlogWatcher），transcribe 内容
流向 agent 体系的两条通道（utterance 事件 + chatlog tail）全部闭合。

### 4.5 transcribe.jsonl 落盘（recording 层）

#### 4.5.1 路径与生命周期

```
{base_dir}/{device_uuid}/{recording_session_id}/chat/transcribe.jsonl
```

与 `history.jsonl` 同目录（`chatlog_watcher.py:56` 的路径规则）。
生命周期完全挂靠既有 `RecordingSession`：per-device per-UTC-day，
UTC 日切换 rollover 后新 recording session 目录自然切到新文件；
`start()` / `attach()` 打开、`_flush_all()` 关闭——与 `_chat_writer` 同进退。

#### 4.5.2 行格式（D6）

复用 `ChatLogWriter`（**零改动**），行 schema 与 `history.jsonl` 完全一致，
`role` 固定 `"user"`，并以既有 `source` 字段标记来源：

```json
{"ts": 1757404800123, "utc": "2026-09-09T08:00:00.123Z", "role": "user",
 "text": "……", "ref_id": "trans_xxxxxxxxxxxx", "chat_session_id": "……",
 "source": "transcribe"}
```

- 同 schema：未来消费工具可复用 `history.jsonl` 的解析路径；
- `source="transcribe"`：消费方可按来源过滤，不会被 `role="user"` 误导为对话发言；
- `source` 参数为 `ChatLogWriter.write`（chatlog.py:38）与 `_RecordingItem`（session.py:43）
  既有能力，仅非 None 时落字段（:54-55），chat/translate 路径行为不变。

#### 4.5.3 写入链路（3 处改动）

1. **`RecordingSink` Protocol + `NoOpRecordingSink`**（`sink.py:39-51, 75-80` 旁）：
   新增 `on_transcribe_text(*, text: str, ts_ms: int, ref_id: str = "",
   chat_session_id: str = "") -> None`（非阻塞、NoOp 空实现，纪律与
   `on_chat_text` 相同：调用方不 await、不 try/except，sink 吞掉所有错误）。
2. **`RecordingSession`**（`session.py`）：
   - `_RecordingItem.kind` 的 Literal 增加 `"transcribe_final"`；
   - 新增 `_transcribe_writer: ChatLogWriter | None = None`，在 `start()`（:104-105 旁）
     与 `attach()`（:129-131 旁）随 `_chat_writer` 对称地
     `ChatLogWriter(self.storage_path / "chat" / "transcribe.jsonl")` 并 `open()`
     （`chat/` 目录已由 start() :102 创建），`_flush_all()`（:361-368）一并关闭；
   - 新增 `on_transcribe_text()`：与 `on_chat_text` 同风格 `put_nowait` 入队
     `kind="transcribe_final"`（队列满丢弃并告警）；
   - `_run_writer` 分发（:244-245）把 `transcribe_final` 并入文本处理；
     `_handle_text`（:303-322）按 kind 路由：`kind == "transcribe_final"` →
     写 `_transcribe_writer`（`role="user"`，**`source="transcribe"`**），
     **不写 `_chat_writer`、不计 `chat_messages`**（:318 计数仅覆盖对话文本轨）；
     `kind in ("asr_final","llm_final")` 路径不变。
3. **orchestrator recording tap 按模式路由**（`orchestrator.py:1328-1339`）：

```python
if is_final and self._recording_sink is not None and self._ctx is not None:
    try:
        if self._pipeline_mode == "transcribe":
            self._recording_sink.on_transcribe_text(
                text=text, ts_ms=now_ms(),
                ref_id=tid, chat_session_id=self._ctx.session_id,
            )
        else:
            self._recording_sink.on_chat_text(
                role="user", text=text, ts_ms=now_ms(),
                ref_id=tid, chat_session_id=self._ctx.session_id,
            )
    except Exception:
        # 既有告警不变
```

**行为变化（显式记录）**：transcribe 模式下 final 转录文本**不再写入
`chat/history.jsonl`**，改为写入 `chat/transcribe.jsonl`；translate 的文本轨
（user 原话 + assistant 译文）独立落 `chat/translate.jsonl`——dialogue translate 行
`source="translate"`，lecture 同传行（`lecture_translate.py` 两处 tap）`source="lecture"`，
均经 `on_chat_text(source=...)` + recording 层按 source 路由，**不写 history.jsonl**，
行级 source 可区分 dialogue / lecture。
chat 模式落盘行为不变。agent relay：translate/lecture 行移出 history.jsonl 后，
`ChatlogWatcher` 不再收到这两类会话的 user 行（nudge 通道随文件迁移关闭）；
utterance 发布未改动（dialogue translate 仍发布；lecture 引擎本就无 utterance 发布，
内容自此完全不进 agent 体系）。录音音频（连接级 PCM→OGG tap）与模式
无关，**不变**。interim 不进任何文本存档（现状亦如此）。

#### 4.5.4 只写不读（本期）

- 无 watcher（agent relay 的 `ChatlogWatcher` 只 tail `history.jsonl`，
  转录内容**不会**触发 proactive nudge——旁听语音不流入 agent 体系）；
- 不进 lvdata-mcp 扫描 / inventory；
- 不做 restore、不提供任何客户端或协议出口；
- recording 关闭（`NoOpRecordingSink`）时与 `history.jsonl` 一致：不产生文件。

### 4.6 行为不变量与边界

1. **轮末模式判定为"当时值"**（D10）：`_handle_audio` 轮末读取**当时的** `_pipeline_mode`
   （:782），无 turn 级模式快照。用户在语音中段切换模式，该 turn 收尾按新模式处理
   （如切到 transcribe 的半截语音收尾只发 `asr_final`、不进 LLM）。切换处不做
   turn detector reset，半截语音自然收尾。单测覆盖该时序（§10 用例 4）。
2. **中段切换的 history 污染（D10 显式接受）**：transcribe→chat 中段切换时，该 turn
   现场语音按新模式写入 history/chat_store 并可能进 LLM 上下文——与 D4 理由存在张力，
   作为有意语义接受（理由见 D10）。
3. **切换即在途取消**（D3）：SPEAKING/THINKING 中切到 transcribe → 立即取消，
   收到"该轮 ai_text/ai_audio 中断缺失"与既有 interrupt 语义一致，客户端无残留状态。
4. **会话重置**：服务端 `session.start` 无条件重置 `"full"`（:244）。客户端在新会话
   `session.ready` 后如需转录模式须重发 `session.change`（与 translate 现状一致；
   SDK 不自动重放；回声后 UI 开关回落为关）。
5. **断线重连**：恢复后默认 chat（同 translate）。
6. **transcribe ↔ lecture**（D9）：切出 lecture 的既有路径先行（关引擎 + SM 复位）；
   ASR 于下一帧按既有惰性重开逻辑恢复。
7. **SM 复位失败路径**：`transition_or_reject` 可能 reject（如 SM 处于不允许迁移的
   状态），此时 `:492` 的 status 回声携带**当前 state** 而非 `listening`（D3）。
   lecture 既有路径同写法、风险非新增，transcribe 新增一个触发点——测试断言覆盖
   该行为（记录现状语义，不在本期修复）。
8. **非法 mode** 仍回 error 1009；`session.change` 早于 `session.start` 仍回 error 1009。
   `session.change(mode=transcribe, usecase=lecture)` **不进入** lecture
   （`target_lecture` 要求 `new_mode == "translate"`，:434-439），usecase 被忽略。
9. **omni** 收到 `mode=transcribe` 静默忽略（现状）；客户端不在 omni 场景展示该开关。
10. **language hint**：`AsrResult.language` 照常累积（`_last_detected_language`），
    transcribe 不进 LLM、不发布 utterance（D7），均不被消费；无副作用。

### 4.7 不变的部分（明确列出）

- **ASR 链路 / `AsrResult.language` / transcription 发送**：与模式无关。
- **interrupt**：transcribe 稳态无在途 pipeline；从其他模式切入时在途任务已在
  §4.2 取消，既有 `_handle_interrupt` 逻辑不变。
- **recording 音频/视频 tap、连接层音频旁路**：与模式无关。
- **`session.start` 重置**（:244）：新会话仍回 `"full"`（§4.6.4）。
- **OmniOrchestrator / codec / config / env / chat_store schema / `ChatLogWriter`**：
  全部无改动。

---

## 5. 消息流

### 5.1 切到 Transcribe

```
Client                              Server
  |── session.change ─────────────────>|
  |   {mode: "transcribe"}             |
  |                                    |  （若在 lecture：先关引擎）
  |                                    |  取消在途 pipeline（若有）
  |                                    |  SM → LISTENING
  |                                    |  _pipeline_mode = "transcribe"
  |<── session.changed ────────────────|
  |   {mode: "transcribe"}             |
  |<── status ─────────────────────────|
  |   {session_status: "listening",    |
  |    reason: "mode_changed"}         |
```

### 5.2 Transcribe 模式下的一轮语音

```
Client                              Server
  |── audio ... ──────────────────────>|
  |<── transcription ──────────────────|  interim（多次）
  |<── transcription ──────────────────|  final
  |                                    |  recording tap → chat/transcribe.jsonl
  |                                    |  （不写 history.jsonl / WorkingMemory /
  |                                    |    chat_store；不发布 utterance）
  |                                    |  轮末（VAD 收尾）
  |<── status ─────────────────────────|
  |   {session_status: "listening",    |
  |    reason: "asr_final"}            |   ← 无 thinking / ai_text / ai_audio / TTS
```

### 5.3 切回 Chat（或 Translate）

```
Client                              Server
  |── session.change ─────────────────>|
  |   {mode: "chat"}                   |
  |                                    |  _pipeline_mode = "full"
  |<── session.changed ────────────────|
  |<── status(listening, mode_changed)─|  下一轮恢复 LLM/TTS
```

### 5.4 新会话自动重置（既有行为，客户端须知）

服务端 `session.start` 无条件把模式重置回 `"full"`（orchestrator.py:244）。
客户端进入新会话后若需转录模式，须在 `session.ready` 后重发
`session.change(mode="transcribe")`。SDK 侧不自动重放，保持现状（同 translate）。

---

## 6. 客户端设计（Android App + SDK）

### 6.1 入口 UI —「同声翻译」Group 增加「转录模式」

`AvatarCustomizationSheet.kt:66-85` 增加第三个 `SwitchRow`，三开关互斥：

```kotlin
SwitchRow(
    title = "转录模式",
    subtitle = "实时转录现场声音",
    checked = transcribeModeEnabled,
    onCheckedChange = onToggleTranscribeMode,
    enabled = !conversationSceneEnabled && !lectureModeEnabled,
)
```

- 新增参数：`transcribeModeEnabled: Boolean = false`、`onToggleTranscribeMode: (Boolean) -> Unit = {}`；
- 既有两行的 `enabled` 各追加 `&& !transcribeModeEnabled`；
- sheet 调用处（`ConversationScreen.kt:570-596`）接线：
  `transcribeModeEnabled = uiState.isTranscribeMode || uiState.showTranscribeSourcePicker`
  （对齐 :591 `|| showLanguagePicker` 的既有模式——picker 打开期间开关保持 on 态）。

UI 文案沿用项目惯例硬编码于组件（`strings.xml` 仅 `app_name`）。

### 6.2 状态 / 事件

| 文件 | 新增 |
|------|------|
| `ConversationUiState.kt` | `isTranscribeMode: Boolean = false`、`showTranscribeSourcePicker: Boolean = false`、`transcribeAudioSource: TranscribeAudioSource = TranscribeAudioSource.VOICE`（Phase 2 消费） |
| `ConversationEvent.kt` | `ToggleTranscribeMode`、`ConfirmTranscribeSource(source)`、`DismissTranscribeSourcePicker`；枚举 `TranscribeAudioSource { RAW, VOICE }`（原声 / 语音） |
| `ConversationViewModel.kt`（collector 区，~:156-166 旁） | 订阅 adapter 新 flow `transcribeMode` → 回写 `isTranscribeMode` |
| `ConversationListenerAdapter.kt` | `_transcribeMode` StateFlow + `setTranscribeMode()`；`clearSession()`（:79-90）复位 `_transcribeMode = false` |

### 6.3 ViewModel 切换逻辑（Phase 1：弹窗 → 确认开启）

```kotlin
ConversationEvent.ToggleTranscribeMode -> {
    if (_uiState.value.isTranscribeMode) {
        // 关闭：与 translate/lecture 关闭同法（ConversationViewModel.kt:272-321）——
        // 全部模式 flag 复位 + 发 chat（三向互斥纪律，见下）
        listenerAdapter.setTranslateMode(false)
        listenerAdapter.setLectureMode(false)
        listenerAdapter.setTranscribeMode(false)
        _uiState.value = _uiState.value.copy(
            isTranslateMode = false, isLectureMode = false, isTranscribeMode = false,
        )
        sdk.sendSessionChange("chat")
    } else {
        // 开启 → 先弹「选择现场录音模式」对话框（§6.4）
        _uiState.value = _uiState.value.copy(showTranscribeSourcePicker = true)
    }
}
is ConversationEvent.ConfirmTranscribeSource -> {
    // Phase 1：选择只记入 UiState，不下发采集链路（仍 VOICE_COMMUNICATION）；
    // Phase 2（已落地，见 android_audio_capture.md §3.3）：采集源由 UiState 派生观测
    // collector 按 (isTranscribeMode, transcribeAudioSource) 纯函数下发。
    listenerAdapter.setTranslateMode(false)
    listenerAdapter.setLectureMode(false)
    listenerAdapter.setTranscribeMode(true)
    _uiState.value = _uiState.value.copy(
        isTranslateMode = false, isLectureMode = false, isTranscribeMode = true,
        transcribeAudioSource = event.source,
        showTranscribeSourcePicker = false,
        showAvatarSheet = false,
    )
    sdk.sendSessionChange("transcribe")
}
ConversationEvent.DismissTranscribeSourcePicker ->
    _uiState.value = _uiState.value.copy(showTranscribeSourcePicker = false)
```

互斥纪律（代码与本文一致，均为三 flag 全复位）：关闭任一模式都
`setTranslateMode(false)` / `setLectureMode(false)` / `setTranscribeMode(false)`
再 `sendSessionChange("chat")`；translate / lecture 的既有开启分支
（`ConfirmTranslateLanguage` / `ConfirmLectureLanguage`）对称追加
`setTranscribeMode(false)` 与 `isTranscribeMode = false`——三者互斥，
防乐观更新窗口内同真。

### 6.4 录音源选择对话框（新增组件 `TranscribeSourcePicker`）

复用 `LanguagePickerSheet` 的对话框模式（`ConversationScreen.kt:641-661` 先例）：

```
┌─────────────────────────────┐
│      选择现场录音模式         │
│─────────────────────────────│
│  ○ 原声   保留现场原始声音   │   ← 原声 = AudioSource.MIC（Phase 2 生效）
│  ● 语音   近讲人声增强       │   ← 语音 = VOICE_COMMUNICATION（默认，Phase 1 实际行为）
│─────────────────────────────│
│        [取消]   [确定]       │
└─────────────────────────────┘
```

- 默认选中「语音」；「确定」→ `ConfirmTranscribeSource(所选)`；
  「取消」/ 点外部 → `DismissTranscribeSourcePicker`，不开启转录、不发消息。
- 选择结果是纯客户端采集参数，不进 `session.change`（协议无字段）。
- **Phase 1 两个选项行为相同**：选择结果仅存入 UiState 不接入采集
  （接入真实切换属 Phase 2，见 §7）。注意远场风险（§9.1）。

### 6.5 SDK — mode 字符串映射

DTO 重新生成后，`SessionManager.kt:181-188` 的 `when (mode)` 追加：

```kotlin
"transcribe" -> ClientSessionChangeMode.TRANSCRIBE
```

`LivaChatSDK.sendSessionChange(mode: String)` 公开签名不变（doc 注释补 `"transcribe"`）。

### 6.6 服务端回声回写

`ConversationListenerAdapter.onSessionChanged`（:209-212）扩展，服务端为模式真源：

```kotlin
_translateMode.value = mode == "translate"
_lectureMode.value = mode == "translate" && usecase == "lecture"
_transcribeMode.value = mode == "transcribe"
```

### 6.7 消息处理 — final text 加「(转录)」后缀

镜像 translate 的 `onTranscription` final 持久化（:92-122）：

```kotlin
val stored = when {
    inTranscribeMode -> "$text (转录)"   // 新增
    inTranslateMode  -> "$text (原文)"
    else             -> text
}
// ChatMessage(id = "user-$transcriptionId", role = USER, ...) 持久化不变
```

- 实时悬浮 `TranscriptionBubble`（`ConversationScreen.kt:787-799`）：interim 照常显示
  原文、不加后缀（同 translate）。
- 持久化消息沿用 USER 角色 + 文本后缀的既有模式；`MessageBubble` 纯文本渲染，无需改动。
- transcribe 下 server 不产生 ai_text，`onAiText` 无需改动。

### 6.8 模式徽标（可选、建议做）

avatar 右上角徽标逻辑（`ConversationScreen.kt:747-766`）同构扩展：
`isTranscribeMode` 时显示 `Icons.Filled.Subtitles` + `contentDescription = "转录模式"`，
样式沿用 26dp 圆形 BrandIndigo 徽标。

### 6.9 失败路径 — 乐观更新回落

开启转录是"弹窗确认 → 乐观置位 → 等回声"流程，需覆盖失败回落：

- **服务端拒绝**：`session.change` 被拒时服务端回 `error(1009)`（专属失败码，
  无 `session.changed` 回声）→ `ConversationListenerAdapter.onError` 对 1009 按
  `pendingModeChange` 回滚对应模式开关（transcribe → `_transcribeMode=false`）并清
  `lastModeRequest`（不再自动恢复）；错误信息同时经 `_errors` flow 出 banner。
  其余错误码与本次切换无关，不触发回落（详见 `session_mode_change.md`）。
- **回声为准**：正常路径由 `onSessionChanged`（§6.6）回写，服务端为模式真源；
  新会话 / 断线重连后 `clearSession()`（:79-90）复位，用户需手动重开（§4.6.4）。

---

## 7. Phase 2 — 录音源选择实际生效

> **已实现（2026-09-09）**：落地设计定稿见 [`android_audio_capture.md`](android_audio_capture.md) §3.3，
> 实施以该文为准。与本节预留语义的两处修订：① 换源时机——经采集代数守卫实现
> **运行中即时重建**（不再要求"新会话采集启动前生效"）；② 离开转录模式（关闭/三向互斥
> 切走/会话复位）**会**将采集链路归位 `VOICE_COMMUNICATION`（本节要点 2「切回不改变
> 采集链路」作废）。下文保留作设计沿革记录。

Phase 1 的「原声 / 语音」对话框只记录选择、不改变采集。Phase 2 把选择接入真实采集链路：

| 选项 | AudioSource | 适用 |
|------|-------------|------|
| 语音（默认，Phase 1 唯一实际行为） | `VOICE_COMMUNICATION`（现状，`AudioCaptureController.kt:106`） | 近讲语音，AEC/NS 开启 |
| 原声 | `MIC`（或 `UNPROCESSED`，按机型能力探测） | 远场/环境声（会议、演讲现场），避免 AEC/NS 抑制非近讲声音 |

设计要点（预留）：

1. `AudioCaptureController.start()` 增加可选 `audioSource` 参数（默认
   `VOICE_COMMUNICATION` 保持现状），`startInternal`（:105-106）透传 `setAudioSource(...)`。
   Android `AudioRecord` 不支持运行中更换 source——选择在新会话采集启动前生效
   （`ConversationCoordinator.start()` :74 传入当次会话的选择结果），并注意采集服务
   重启冷却 `MIC_SERVICE_RESTART_COOLDOWN_MS`；会话中途切换需 stop/start 重建。
2. `ConfirmTranscribeSource` 不再仅记录 `event.source`，下发到采集层；选择结果仅当
   `isTranscribeMode` 时有意义，切回 chat/translate 不改变采集链路。
3. 协议无变化：来源选择是纯客户端采集配置，不进 `session.change`。
4. 可选：最近一次选择持久化到 Settings，作为下次打开对话框的默认选中项。
5. 若 Phase 1 远场验收不达标（§9.1），本 Phase 需提前，届时同步确认采集源切换与
   屏幕录制音轨是否共享 AudioRecord 链路。

---

## 8. 文件改动清单

### 服务端

| 文件 | 变更 |
|------|------|
| `packages/api-schema/c2s/session-change.json` | `mode.enum` 增加 `"transcribe"`（:24） |
| `packages/api-schema/s2c/session-changed.json` | `mode.enum` 增加 `"transcribe"`（:24） |
| `packages/sdk-python/.../generated/*` | **重新生成**——`ClientSessionChangeMode` / `ServerSessionChangedMode` 新枚举值 |
| `packages/sdk-kotlin/.../generated/*.kt` | **重新生成**（`make generate-android-api` + `make check-android-api-generated`） |
| `services/session/orchestrator.py` | `_handle_session_change`（:411-492）白名单 + transcribe 分支（取消在途 pipeline、SM 复位、置 mode、清语言对）；轮末门控（:782-787）改为 `in ("asr_only","transcribe")`；history guard（:745、:1425）改为 `not in ("translate","transcribe")`；agent relay（:797）追加 `!= "transcribe"` 条件；recording tap（:1328-1339）按模式路由 `on_transcribe_text` / `on_chat_text` |
| `services/recording/sink.py` | `RecordingSink` Protocol + `NoOpRecordingSink` 新增 `on_transcribe_text` |
| `services/recording/session.py` | `_RecordingItem.kind` Literal += `"transcribe_final"`；`_transcribe_writer`（`chat/transcribe.jsonl`）生命周期挂靠 `_chat_writer`；`on_transcribe_text()` 入队；`_run_writer` 分发 + `_handle_text` 路由（`role="user"` + `source="transcribe"`，不计 `chat_messages`）；`_flush_all` 关闭 |

### 客户端（Android）

| 文件 | 变更 |
|------|------|
| `apps/android/LivaChatSDK/.../internal/session/SessionManager.kt` | `sendSessionChange` mode 映射（:181-188）增加 `"transcribe"` |
| `apps/android/.../ui/screens/conversation/AvatarCustomizationSheet.kt` | 「同声翻译」Group 增加「转录模式」`SwitchRow`（三向互斥） |
| `apps/android/.../ui/screens/conversation/ConversationEvent.kt` | `ToggleTranscribeMode` / `ConfirmTranscribeSource` / `DismissTranscribeSourcePicker` + `TranscribeAudioSource` 枚举 |
| `apps/android/.../ui/screens/conversation/ConversationUiState.kt` | `isTranscribeMode` + `showTranscribeSourcePicker` + `transcribeAudioSource` |
| `apps/android/.../ui/screens/conversation/ConversationViewModel.kt` | Toggle/Confirm/Dismiss 处理（§6.3）+ collector 订阅 adapter flow + 失败回落（§6.9）+ 既有 translate/lecture 分支补 transcribe 复位 |
| `apps/android/.../ui/screens/conversation/ConversationListenerAdapter.kt` | `_transcribeMode` flow + `setTranscribeMode` + `onSessionChanged` 回写（:209-212）+ `onTranscription` `"(转录)"` 后缀（:104 旁）+ `clearSession` 复位 |
| `apps/android/.../ui/screens/conversation/ConversationScreen.kt` | sheet 接线（:570-596）+ 挂载 `TranscribeSourcePicker`（+ 可选徽标 :747-766 旁） |
| `apps/android/.../ui/screens/conversation/TranscribeSourcePicker.kt`（新组件） | 「原声/语音」选择对话框（§6.4） |

### Phase 2（本期不做，仅记录）

| 文件 | 变更 |
|------|------|
| `apps/android/.../media/AudioCaptureController.kt` | `start()/startInternal()` 增加 `audioSource` 参数，`setAudioSource` 参数化（:105-106） |
| `apps/android/.../media/ConversationCoordinator.kt` | `audioCapture.start()`（:74）传入会话录音来源选择 |

### 明确不改

`s2c/transcription.json`、`s2c/status.json`、`common.json`、`codec.py`、config/env、
`OmniOrchestrator`、`services/omni/**`、chat_store schema、**`ChatLogWriter`**（D6）、
agent relay 的 ChatlogWatcher、iOS/Web。

---

## 9. 风险与约束

1. **`VOICE_COMMUNICATION` 远场风险【gating 验收项】**：该链路是近讲优化
   （AEC/NS 可能抑制远处讲话人/外放声音），而转写的核心场景是"听别人说"。
   Phase 1 两个弹窗选项都跑 VOICE_COMMUNICATION，"原声"选项名不副实。
   **Phase 1 真机验收必须包含外放/远场声源转写质量检查（§10 用例 19）**；
   若不达标，"原声"（MIC）的真实接入（Phase 2，§7）需提前，届时同步确认
   采集源切换与屏幕录制音轨是否共享 AudioRecord 链路。
2. **识别质量直接上屏**：transcribe 无 LLM 环节，ASR 误识别/无标点原文直接以
   `(转录)` 消息落屏。属该模式固有属性；后缀标记用于弱化"AI/用户说的话"误导。
3. **气泡语义**：持久化沿用 USER 角色 + `(转录)` 后缀（与 translate 的 `(原文)` 路径
   一致）。若产品上认为易与用户发言混淆，可迭代为独立样式，不在本期范围。
4. **切换中断回复**：从 chat/translate 切到 transcribe 会取消在途 LLM/TTS（D3）。
   客户端将收不到该轮 final ai_text——与既有 interrupt / lecture 进入语义一致，
   无残留状态。
5. **假选择风险**：Phase 1 的「原声/语音」对话框选择不生效，用户可能感知为无效
   交互——产品已确认本期仅 UI 定稿；选择结果存入 UiState，Phase 2 直接消费。
   若对外发布早于 Phase 2，评估是否在发布说明中标注。
6. **中段切换 history 污染**：D10 已显式记录为有意接受的行为（单 turn、用户主动
   触发）；若产品反馈突兀，评估 turn 级模式快照。
7. **agent relay 不发布转录**（D7）：会议/课堂内容不流向 agent 总线（隐私语义），
   utterance 与 chatlog watcher 双通道均闭合；如未来需要"自动总结转录"类能力，
   需显式放开并评估授权边界。
8. **会话重置语义**：每新会话服务端重置回 chat（§4.6.4），客户端需在会话内重开
   转录模式；与 translate 现状一致，非新问题。
9. **长会话累积**：长时间转录（如整场会议）会持续追加本地 USER 消息与
   transcribe.jsonl 行；本地消息列表性能与文件体积不设上限（与 history.jsonl
   同日 rollover 语义一致），如需上限/清理策略另行迭代。
10. **协议描述文案**：transcription schema 中 "final … trigger THINKING" 在
    transcribe 下不成立（文档措辞问题，不改 schema）。

---

## 10. 测试计划

**服务端单测**（新增 `services/tests/unit/session/test_transcribe_mode.py`，
对齐 `test_translate_mode.py` 风格）：

1. `session.change(mode=transcribe)` → `session.changed(mode=transcribe)` 回声
   （无语言/usecase）+ `status(listening, mode_changed)`；`_pipeline_mode == "transcribe"`。
2. transcribe 下喂音频一轮 → transcription interim/final 照发 → 轮末仅
   `status(listening, asr_final)`；**断言无** THINKING 迁移、无 ai_text / ai_audio；
   静音轮次不发 status。
3. transcribe 下 user 文本**不写** WorkingMemory / chat_store。
4. **语音中段切换**（D10）：turn 收尾时刻模式已变 → 按新模式收尾（切到 transcribe
   不启 thinking；transcribe→chat 中段切换按 chat 收尾、该 turn 写入 history——
   断言 §4.6.2 记录的行为）。
5. **translate（speaking/thinking 中）→ transcribe**：在途 pipeline 被取消、
   SM 回 LISTENING、无残留 ai_text/ai_audio、`status(mode_changed)` 携带 `listening`；
   **SM 复位被 reject 的注入用例**：status 携带当前 state（§4.6.7 行为记录）。
6. transcribe → chat：下一轮恢复 `_start_thinking`（history 写入 gate 恢复）；
   transcribe → translate：`_pipeline_mode == "translate"`，语言偏好生效。
7. lecture → transcribe：先走 lecture 退出路径（关引擎）再进入 transcribe，
   下一帧 ASR 惰性重开。
8. 边角：`session.change(mode=transcribe, usecase=lecture)` **不进入** lecture
   （`target_lecture` 要求 `new_mode == "translate"`）；非法 mode 仍回 error 1009；
   `session.change` 未 start 仍回 error 1009。
9. **agent relay（D7）**：transcribe 下 `publish_utterance` **不触发**；
   **asr_only 下仍触发**（防误伤回归）；chat/translate 下行为不变。
10. **recording tap 分流**：transcribe 下 final 调 `on_transcribe_text`、
    **不调** `on_chat_text`；非 transcribe 模式行为不变。
11. 新 `session.start` 后模式重置为 full（上个会话为 transcribe 亦然）。

**recording 单测**（扩展 `services/tests/unit/recording/test_recording_session.py`）：

12. `start()` 后 `chat/transcribe.jsonl` 存在；`on_transcribe_text` 条目写入该文件，
    `history.jsonl` 无对应行；**`chat_messages` 计数不增**。
13. 条目格式：`ts` / `utc` / `role="user"` / `text` / `ref_id` / `chat_session_id` /
    **`source="transcribe"`**；chat/translate 模式仍写 `history.jsonl` 且不带
    source（既有行为不变）。
14. `attach()` 重连后 writer 重开、续写不丢；`finalize()` / `_flush_all()` 关闭。

**客户端单测**：

15. `onSessionChanged(mode="transcribe")` → `transcribeMode=true`；`(转录)` 后缀
    拼接正确，translate 的 `(原文)` 不受影响。
16. sheet 三开关互斥逻辑（enabled 表达式与 toggle 复位）；
    **Toggle 关闭分支三 flag 全复位 + `sendSessionChange("chat")`**（§6.3）。
17. SDK `sendSessionChange("transcribe")` 映射不再被丢弃。
18. 开启转录 → 弹 `TranscribeSourcePicker`；「确定」（任选原声/语音）→ 发
    `session.change("transcribe")` 且采集参数不变（仍为 VOICE_COMMUNICATION）；
    「取消」→ 不发消息、不进入转录；服务端拒绝 `error(1009)` → 开关回落为关（§6.9）。

**真机联调**：

19. **【gating】远场/外放验收**（§9.1）：会议外放场景（扬声器播放人声、
    距设备 1-3m 讲话）连续转录，评估 ASR 召回与可读性；不达标 → Phase 2 提前。
20. 会话页 avatar → sheet 出现「转录模式」，与「对话模式/听众模式」互斥；
    开启后说话 → 悬浮气泡实时原文 → final 落为 `(转录)` 后缀气泡；
    无 AI 回复/语音播报；徽标显示与 contentDescription。
21. 转录中检查 `{recording_session}/chat/transcribe.jsonl` 逐行追加（含
    `source="transcribe"`）、`history.jsonl` 不含转录行。
22. 断线重连/新会话后模式复位为 chat（需手动重开）。

---

## 11. 兼容性与回滚

- **协议**：mode 为必填枚举，新旧组合行为明确（§3.5）。
- **数据**：transcribe.jsonl / translate.jsonl 为纯新增旁路文件，无消费方；删除不影响任何现有链路。
  `history.jsonl` 在 transcribe / translate 模式下各少一类行（均为本期/近期的模式分轨，无存量影响）。
- **回滚**：服务端改动集中在 `_handle_session_change` 白名单/分支、一处轮末门控、
  两处 history guard、一处 relay 条件、recording tap 路由与 recording 层 writer 挂靠，
  不触及 chat/translate 路径；`ChatLogWriter` 零改动（D6）；客户端开关隐藏即可下线。
- **omni**：天然不支持，无兼容性问题。
