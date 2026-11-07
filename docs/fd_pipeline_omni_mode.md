# Pipeline Omni Mode（全模态模式）— 问题分析与设计

> **状态**：**已实施（2026-09-27）**（同日二次、三次 review 增补已并入，见 §8.1-5…10）。
> 服务端：C1–C9 落地（schema+DTO 重生成、`omni_mode.py` 引擎、orchestrator 第四模式、endpoint/application 装配、
> `omni_orchestrator.py` 删除、旧测试迁移）；客户端：C10/C11 落地（App 入口/弹窗/徽标/抓屏停发 text + SDK 映射）。
> 回归：服务端 unit **1115 passed**、omni 模式新增 14 例 + 装配 1 例全绿；Android `:LivaChatApp:testDebugUnitTest` **628 passed**、`:LivaChatSDK` 全绿。
> - D1 取 **A**：`session.change(mode=omni)` 会话内切换 + 新引擎（连接级路由废弃，`omni_orchestrator.py` 删除）
> - UI 取 **A**：同声翻译组内第 4 行（与其它三行同形）；弹窗取**确认式**（取消 / 开启）
> - `omni.enabled` 语义变更为「允许该模式」（并删除 `application.py:249` 配置级互斥）——**同意**
> - **头像徽标必做**（不再是可选项）
> - 其余按本文推荐执行（D2 新引擎、D3 4001 回错、D5 采样率同源、D6-D8 路由与落库对齐 chat）
> - **二次 review 增补**：D4 补 enter-failure 语义（open 失败回 4001、成功后才置位）与三条 lecture parity
>   （语言字段 / TurnDetector / ASR 冷却复位）；C5 补 omni voice/generation_opts 装配参数；
>   omni 模式客户端停发 `payload.text`（服务端忽略兜底）；§7 补 enter-failure / omni↔lecture 直切 /
>   重发幂等 / omni→transcribe poll 重建四条用例
> **Date**: 2026-09-27
> **增补（2026-10-02，待 review）**：omni 独立 prompt 文件 + 主动互动（不等开口，听到咳嗽/喷嚏、
> 看到有意思的图片可主动开启聊天）——codebase 分析 + 真机探针 + 设计草案见 **§11**。
> **编者注（2026-10-03）**：`session.change` 拒绝错误码已统一为 `error(1009)`（原 4001/4003，
> 即 D3 备选中的"新增专用 error code"方案已落地）；`permanentModeRejectCodes`（4001/4003/1006）
> 已从客户端删除，`onError` 现仅对 `1009` 触发回落并清恢复请求。变更方案与版本错位说明见
> [`session_mode_change.md`](session_mode_change.md)。本文 D3/D4 两节已按新语义订正，
> 其余行文中的 4001/4003 与"任意错误回落"表述均以该文为准。
> **〔2026-10-09 修订〕** omni 附帧超限防护（**已实施**，服务端）——真机 + 探针确认 DashScope
> 网关对 WS 单帧 >262144B 直接 close 1009（证据：`docs/fd_pipeline_avdetector.md` §10 条目 28），
> 而 omni 模式**每帧都附图**、暴露高于按节拍检查的 avdetector（§4 D6 原"base64 ≤256KB 已兼容"
> 表述不成立，已勘误）。`alibaba_omni.py::feed_image` 前置两档重压（max_edge 960→640，复用
> `media.jpeg.image_resize`）；失败/两档仍超 → warning 日志 + **跳过本帧**（不抛错——引擎对
> `feed_image` 异常会 `_drop_stream` 关掉整个 omni 会话流）。测试：`TestFeedImageResize`
> 6 例（先失败后通过）；回归：服务端全量 **1532 passed / 0 failed / 1 skipped**。
> **Scope**：`packages/api-schema/c2s|s2c`（mode 枚举）· `services/session/orchestrator.py`（模式切换 + 引擎挂载）·
> 新增 `services/session/omni_mode.py`（omni 引擎）· `services/gateway/endpoint.py`、`services/app/application.py`（装配）·
> `configs/dev/server.yaml`（`omni.enabled` 语义）· Android App/SDK（模式入口 + 弹窗 + 映射）
> **上游文档**：`docs/fd_pipeline_transcribe_mode.md`（**同款先例**：`session.change` 加一条模式的端到端改造清单）·
> `docs/fd_pipeline_translate_mode_v2.md`（`session.change` 动态切换基线）·
> `docs/translate_lecture_improvements.md`（模式切换阻塞治理：`session.changed` ≤1s）·
> `docs/qwen_omni_realtime.md`（omni provider 现状：3.8 + nested 16 kHz + semantic_vad，2026-09-27 已实施）·
> `docs/fd_server_omni_model.md`（omni 抽象层：`OmniService`/`OmniProvider`/`OmniEvent`）·
> `docs/android_screen_recording.md`（抓屏图片链路）
>
> **结论先行**：
> 1. 需求可实现，且**绝大部分是"接线"而非新能力**——omni provider 抽象（`OmniService`/`OmniStream`）与事件语义
>    已就绪（`docs/qwen_omni_realtime.md` 刚落地 3.8 迁移 + 采样率/转写修复），缺的只是"把 omni 从
>    **连接级路由**改成**会话内模式**"：schema mode 枚举 + `DialogOrchestrator` 的第四模式 + 一个 omni 引擎
>    （结构照抄 `LectureTranslateEngine`，事件映射照抄 `OmniOrchestrator._event_pump`）。
> 2. 按需求，**连接级路由（`endpoint.py:100` 二选一）随之废弃**，`omni_orchestrator.py` 删除（§4 D2，二次 review 改判）。
>    若接受"继续用现有 OmniOrchestrator"，还有一条代码量小得多的路（§4 D1-B），需 review 拍板。
> 3. 三处必须同步的既有约束：`application.py:249` 的配置级互斥（`omni.enabled=true` 会让 translate
>    **配置也消失**）必须改；omni 引擎开流必须用**编解码器采样率**（否则 1.5 倍慢放回归，§4 D5）；
>    客户端模式切换回滚机制（`ConversationListenerAdapter.onError`；现行以 `error(1009)` 为唯一失败信号，
>    见 `session_mode_change.md`）可直接复用于"服务端未启用 omni"。
> 4. **必须改一处易漏的既有门控**：`orchestrator.py:1491` 的 v3 抓屏文本回合门控未排除 `omni`——
>    不改则全模态下每张抓屏都会触发一轮三段式 LLM+TTS（双声叠放 + history 被指令污染），详见 §4 D6b。
> 5. **另有一处同类"独占式"写法要拆**：`application.py:52-93` 的服务创建是 `if omni.enabled … else …`——
>    现状开启 omni 时**不建 ASR/LLM/TTS**（三段式三模式全失效），模式化后必须"三段式无条件 + omni 额外 +
>    两个 system prompt 分离"，详见 §4 D3 第三条。

## 1. 背景与目标

现状：omni 是一条**连接级**路径——`configs/dev/server.yaml` 的 `omni.enabled=true` 时，
`services/gateway/endpoint.py:100` 为**每个**连接装配 `OmniOrchestrator`，三段式 pipeline 完全不参与；
`omni.enabled=false`（当前 dev 默认）时全模态能力对客户端不可见。

目标：把全模态变成**用户可在会话内选择的模式**，与既有的对话/听众/转录三模式并列、互斥。

| | Chat | Translate（对话/听众） | Transcribe | **Omni（新增）** |
|---|---|---|---|---|
| 进入方式 | `session.start` 默认 | `session.change(mode=translate[, usecase])` | `session.change(mode=transcribe)` | **`session.change(mode=omni)`** |
| 引擎 | ASR + LLM + TTS | 同左（lecture 走 `LectureTranslateEngine`） | ASR only | **`OmniModeEngine`（单流：audio+image → text+audio）** |
| ASR | 服务端 ASR 服务 | 同左 | 同左 | **模型内建（`input_audio_transcription.*`）** |
| TTS | 服务端 TTS 服务 | 同左 | 不调用 | **模型内建（`response.audio.delta`）** |
| 音色 | 三段式 TTS 音色 | 同左 | — | **provider 音色（Tina）** |
| 落库/录制 | 照常 | 照常 | `translate.jsonl`/`transcribe.jsonl` 变体 | **照常（与 chat 同：`history.jsonl` + chat_store）** |

### 1.1 目标

- 协议：`session.change` / `session.changed` 的 `mode` 枚举新增 `omni`（§3）。
- 服务端：`DialogOrchestrator` 支持第四模式——进入时替换 ASR/TTS 为 omni 引擎，退出时懒重开（§4）。
- 客户端：`AvatarCustomizationSheet`「同声翻译」组内新增**全模态**入口（描述「使用全模态模型」），
  与既有三项互斥；选中后弹窗显示「使用全模态AI模型，可以降低交互延迟」，确认后发 `session.change(mode="omni")`（§6）。
- 客户端：头像**全模态徽标**（与 translate/transcribe 徽标同款，§6.4）。
- 连接级路由废弃：`endpoint.py` 不再按 `omni.enabled` 二选一 orchestrator；`omni_orchestrator.py` 删除
  （事件泵逻辑由新引擎承接，git history 存档，§4 D2）。

### 1.2 非目标（Out of Scope）

- **不改 omni provider 层**（`services/omni/**`）：3.8 迁移、nested 16 kHz、semantic_vad、转写双读均已完成
  （`docs/qwen_omni_realtime.md` §10），本设计只消费其接口。
- **不改抓屏/图片协议**：`video.payload.text`（v3/v4）仍只作用于三段式路径；全模态模式下图片走
  `feed_image` 作为模型视觉上下文（现状语义，§4 D6）。
- **不做模式能力广播**：不在 `session.ready` 增加"服务端支持哪些模式"字段；客户端一律展示入口，
  服务端不支持时按"切换被拒回落"处理（§6.4 的 `onError` 路径，现行以 `error(1009)` 为信号；备选方案「session.ready 广播能力」见 §8 风险 6）。
- iOS / Web 客户端（`apps/ios` 仍为占位）。

## 2. 现状关键代码锚点（实现基线，逐条核实）

### 服务端

| 位置 | 内容 |
|---|---|
| `packages/api-schema/c2s/session-change.json` | `payload.mode.enum = ["chat","translate","transcribe"]`；注释已注明 transcribe 为 "pipeline only, ignored by omni" |
| `packages/api-schema/s2c/session-changed.json` | `payload.mode.enum` 同上；`my_language`/`local_language`/`usecase` 仅 translate 携带 |
| `services/session/orchestrator.py:329-342` | `_dispatch`：`session.start/end/change/audio/interrupt/video` 六类（**无 session.change 的 omni 分支**） |
| `services/session/orchestrator.py:348-372` | `_handle_session_start`：**无条件**复位 `_pipeline_mode="full"` 与语言字段；并关闭上一会话遗留的 lecture 引擎（:366-372） |
| `services/session/orchestrator.py:540-646` | `_handle_session_change`：mode 白名单校验（:551-556，非法回 `error(1009)`）→ lecture 切出（:571-575）→ chat/transcribe/translate 分支（:577-628）→ `session.changed` 回声（:638-644）+ `status(state.value,"mode_changed")`（:646） |
| `services/session/orchestrator.py:650-737` | `_enter_lecture_mode`：重入先关旧引擎 → `_cancel_pipeline()` + `_cancel_asr_poll()` → SM 复位 → 并发关 ASR/TTS → `LectureTranslateEngine(...)` + `open()` |
| `services/session/orchestrator.py:739-756` | `_close_lecture_engine`：静默 close + 置空 + `_lecture_active=False`（ASR/TTS 懒重开） |
| `services/session/orchestrator.py:757-776` | `_handle_audio`：**lecture 分流在最前**（:762-776，直接 `feed_pcm` 后 return）——omni 引擎照此插入 |
| `services/session/orchestrator.py:1344-1354` | `_handle_interrupt`：lecture 分流（:1349-1354）——omni 引擎照此插入 |
| `services/session/orchestrator.py:1431+` | `_handle_video`：仅入 buffer + `ai_video_ack`（**无引擎分流**，omni 需新增 `feed_image`） |
| `services/session/orchestrator.py:1491` | **v3 文本回合门控** `_pipeline_mode not in ("translate","transcribe","asr_only")`——omni 必须加入排除（D6b）；同类门控 `:849`（ASR final 起轮）、`:881`（ASR final 落库）、`:1786`（AF 快照）均为 **ASR 派生**，omni 下靠"ASR 已关"不可达 |
| `services/session/orchestrator.py:1049-1055`、`:1905-1917` | TTS / ASR 的**懒重开**路径（退出 omni 后按需重建：首轮/首帧） |
| `services/session/lecture_translate.py:27-82` | `LectureTranslateEngine` **引擎形态基线**：own `handler/codec/ctx/history/interrupt/cfg`，自建 Opus 编码器 + `AudioFrameBuffer`，自持事件泵与 per-response 状态 |
| `services/session/lecture_translate.py:83-190` | 引擎公开面：`open()/feed_pcm()/interrupt()/close()` |
| `services/session/lecture_translate.py:239/263/319` | 引擎直接操作 `ctx.sm.transition_or_reject(...)`（SM 由引擎推进，先例成立） |
| `services/session/omni_orchestrator.py:507-675` | `_event_pump`：`INPUT_TRANSCRIPTION_DELTA/DONE`、`SPEECH_STARTED`(→SPEAKING 时 `user_voice` 打断)、`RESPONSE_STARTED`(SM→THINKING + 图像快照)、`TEXT_DELTA`、`AUDIO_DELTA`、`RESPONSE_DONE`(flush 尾帧 + history/chat_store + 录制 tap + SM→LISTENING)、`ERROR` → **omni 引擎的事件映射蓝本**（D2 实施后该文件删除，锚点为删除前状态） |
| `services/session/omni_orchestrator.py:660-675` | 流终结兜底：泵退出 → 置空流 + 冷却，下一帧音频懒重开（omni 3.8 实测空闲 150s 不触发 `session.finished`，见 `docs/qwen_omni_realtime.md` §4.2，兜底仍保留） |
| `services/session/omni_orchestrator.py:430-501`（馈图 `:471-488`） | `_handle_video` 内 best-effort 馈图：`feed_image` 失败（`ProviderError`）→ 关流 + 冷却 + 下帧懒重开 |
| `services/omni/providers/alibaba_omni.py:268-269` | provider 侧：API 回 `Invalid video/image` 时置 `_image_disabled`（`:327` 起跳过后续馈图）——**属 provider，本次不改** |
| `services/gateway/endpoint.py:83-99` | 编解码器采样率 = `cfg.media.audio.default_sample_rate`（16000）；**provider 级采样率同源先例**：`OmniOrchestrator(..., pipeline_sample_rate=output_sample_rate)`（:118） |
| `services/gateway/endpoint.py:100-127` | 连接级二选一：`omni_enabled and omni_service is not None` → `OmniOrchestrator`，否则 `DialogOrchestrator`（**本设计要拆掉的分支**） |
| `services/app/application.py:52-60` | `omni_service` 仅在 `cfg.omni.enabled` 时创建（`create_omni(cfg.omni)`），失败只告警 |
| `services/app/application.py:240-249` | `app.state.omni_service/omni_enabled` 暴露给 endpoint；**`translate_cfg = cfg.translate if not cfg.omni.enabled else None`**（配置级互斥，与"模式互斥"冲突，必须改） |
| `services/omni/service.py:11-30` | `OmniService.open(**kwargs) -> (OmniStream, provider_name)`，active + fallback 顺序试连，全失败抛 `ProviderError(OMNI_UNAVAILABLE)` |
| `services/omni/base.py:9-51` | `OmniEventType`（9 类事件）·`OmniStream`（`feed_audio/feed_image/commit_user_turn/events/cancel_response/reset/close`）·`OmniProvider.supports_vision` |

### 客户端（Android）

| 位置 | 内容 |
|---|---|
| `.../ui/screens/conversation/AvatarCustomizationSheet.kt:69-93` | 「同声翻译」组：三个 `SwitchRow`（对话模式/听众模式/转录模式），互斥靠 `enabled = !其它两个` |
| `.../ConversationScreen.kt:646-655` | sheet 形参接线：`xxxEnabled = uiState.isXxxMode \|\| uiState.showXxxPicker`（picker 打开期间开关保持 on）+ `onToggleXxx` |
| `.../ConversationScreen.kt:704-740` | `LanguagePickerSheet` ×2 + `TranscribeSourcePicker` 对话框先例——全模态说明弹窗照此 |
| `.../ConversationViewModel.kt:344-459` | 三个模式的 `Toggle*` / `Confirm*` 分支：开启→弹 picker；确认→三 flag 复位 + `listenerAdapter.setXxxMode` + `rememberModeRequest(ModeChangeRequest(...))` + `markModeChangePending(mode)` + `sdk.sendSessionChange(...)`；**关闭→全复位 + `rememberModeRequest(null)` + `sendSessionChange("chat")`** |
| `.../ConversationListenerAdapter.kt:53-142` | 模式 StateFlow（`_translateMode/_lectureMode/_transcribeMode`）、`setXxxMode`、`rememberModeRequest`、`markModeChangePending`、`clearSession` |
| `.../ConversationListenerAdapter.kt:245-262` | `session.ready`/重连：按 `lastModeRequest` 乐观置位并请求 ViewModel 补发 `session.change`（**"omni" 需要新分支**） |
| `.../ConversationListenerAdapter.kt:299-306` | `onSessionChanged`：以服务端回声为准回写三个 flag |
| `.../ConversationListenerAdapter.kt:308-330` | `onError`：`code == 1009`（`SESSION_CHANGE_FAILED`）且 `pendingModeChange` 非空 → 按模式回落为关 + 清 `lastModeRequest`；其余错误不回落（**"服务端未启用 omni" 直接复用此路径**） |
| `.../ui/screens/conversation/ModeChangeRequest.kt` | 重连恢复用的 `ModeChangeRequest(mode, myLanguage, localLanguage, usecase)` |
| `.../ConversationScreen.kt:862-894` | 头像右上角模式徽标：translate（`Icons.Filled.Translate`）/ transcribe（`Icons.Filled.Subtitles`），26dp 圆 + BrandIndigo 0.7 alpha |
| `apps/android/LivaChatSDK/.../internal/session/SessionManager.kt:197-206` | `sendSessionChange(mode, ...)`：字符串→`ClientSessionChangeMode` 枚举的 `when` 映射，未知值告警丢弃（**需加 `"omni"`**） |
| `scripts/generate_python_api.py` / `scripts/generate_kotlin_api.py`；`make generate-android-api`、`make check-android-api-generated` | schema → DTO 生成与 CI 一致性校验（`docs/fd_pipeline_transcribe_mode.md` §3.4 同款流程） |

## 3. 协议变更

### 3.1 C2S `session-change.json`

`payload.mode.enum` 增加 `"omni"`；描述同步更新：

```json
"mode": {
  "type": "string",
  "enum": ["chat", "translate", "transcribe", "omni"],
  "description": "Target pipeline mode. 'transcribe' = ASR-only live transcription (no LLM, no TTS; pipeline only). 'omni' = full-modality realtime model (single stream: audio+image in, text+audio out; requires server omni provider)."
}
```

- `omni` 不需要 `my_language`/`local_language`/`usecase`（与 chat/transcribe 一致，发送方不携带、服务端忽略）。

### 3.2 S2C `session-changed.json`

`payload.mode.enum` 同样增加 `"omni"`；全模态回执只带 `mode`（既有 `effective_usecase` 的 `else: None` 分支自动覆盖，零改动）。

### 3.3 不变化的 schema / 消息

- `s2c/transcription.json`、`ai-text.json`、`ai-audio.json`、`status.json` 全部不变——全模态复用 chat 同款下行
  （transcription interim/final、ai_text 分片、ai_audio Opus、status thinking/speaking/listening）。
- 无新 C2S/S2C 消息类型、无新字段、无新 schema 枚举（未启用场景的拒绝错误码 2026-10-03 起为 `error(1009)`，原 4001，见 §4 D3）。

### 3.4 DTO 重新生成（schema 为单一事实来源）

```bash
python3 scripts/generate_python_api.py --schema packages/api-schema \
  --out packages/sdk-python/src/livachat_protocol/generated
make generate-android-api && make check-android-api-generated
```

生成产物新增 `ClientSessionChangeMode.OMNI` / `ServerSessionChangedMode.OMNI`（Python、Kotlin 各一套）。

### 3.5 兼容性

- 旧客户端不发 `omni`，不受影响。
- 旧服务端收到 `omni` 按既有白名单返回 `error(4001)`（现行统一为 `error(1009)`）。同版本组合：
  客户端据 1009 回落并清恢复意图，不会每次重连都重试；新旧错位组合的过渡行为见
  [`session_mode_change.md`](session_mode_change.md) §5。

## 4. 服务端设计

### D1 模式如何进入 omni —— 三方案（**需 review 拍板，推荐 A**）

| | **A（需求原文，推荐）** | B（低成本替代） | C（不做/维持现状） |
|---|---|---|---|
| 形态 | `session.change(mode=omni)`，**会话内可切** | 连接建立前协商（`session.start` 配置位或连接参数），endpoint 选 orchestrator | 保持 `omni.enabled` 连接级路由 |
| 服务端改动 | schema + `DialogOrchestrator` 第四模式 + **新引擎**（§4 D2） | 仅 schema/握手加字段，**复用现有 `OmniOrchestrator`**（零引擎新增） | 零 |
| 客户端改动 | 模式入口 + 弹窗 + 状态机 + SDK 映射 | 设置项（非会话内模式），改动更小 | 零（但 App 无入口） |
| 代价 | 事件泵逻辑与 `OmniOrchestrator` 重复一份（D2） | 会话内不可切；与既有三模式"会话内互斥切换"的交互不一致 | 与需求不符 |

**取 A**（需求明确要求 app 发 `session.change(mode=omni)`）。B 仅作为"若不愿重复代码"的备选记录在案（§8.1 已拍板取 A）。

### D2 引擎形态：新类 `OmniModeEngine`（`services/session/omni_mode.py`）

- 结构**照抄 `LectureTranslateEngine`**（`services/session/lecture_translate.py:27-190`）：own
  `handler/codec/ctx/history/interrupt/omni_service`，公开 `open()/feed_pcm()/feed_image()/interrupt()/close()`，
  内部 `_event_pump()` + `_send_ai_text/_send_ai_audio/_send_transcription/_send_status`。
- 事件映射**照抄 `OmniOrchestrator._event_pump`**（`omni_orchestrator.py:507-675`，含流终结兜底 `:660-675`）
  + `_handle_audio`（`:285-356`）与 `_handle_video`/馈图（`:430-501`，best-effort 见 `:471-488`）的 feed 语义，
  含：RESPONSE_STARTED 图像快照、SPEAKING 中 SPEECH_STARTED → `user_voice` 打断、RESPONSE_DONE flush 尾帧
  + 落库 + 录制 tap、流终结懒重开。
- `open()` 参数与 `OmniOrchestrator` 开流完全同构（`omni_orchestrator.py:210-216`）：`sample_rate`
  （D5，**必须用编解码器采样率**）+ `system_prompt` + `voice`（配置音色 Tina）+ `modalities=("text","audio")`
  + `generation_opts`——后四项来自 `cfg.omni`（装配见 D3/C5，**不可用** `cfg.llm.generation`）。
- open 成功后写 `ctx.llm_provider = provider_name`（用量/会话归因；先例 `omni_orchestrator.py:217/:301`、
  `lecture_translate.py:111-113`）。切出后不恢复（与 lecture 同款 latest-wins，记录在案）。
- `omni_orchestrator.py` **删除**（二次 review 改判，推翻初版"保留死代码"）：死代码 + 其集成测试持续
  亮绿灯会给"该路径仍活着"的虚假信心；回滚手段（`omni.enabled=false` + revert 部署）本就不需要它——
  endpoint 二选一删除后死代码在回滚中也不会被重新装配。事件泵逻辑由新引擎承接，git history 即存档；
  `tests/integration/omni/` 的 fakes 迁移复用、仅测新引擎。
  *（备选 D2-B：把事件泵抽成共享件——旧文件删除后该议题自然消解，不推荐另搞抽象。）*

### D3 配置语义与装配

- `omni.enabled` 新语义：**"允许客户端切到全模态模式"**（创建 `OmniService`），不再影响默认路径。
- `services/app/application.py`：删除 `:249` 的 `translate_cfg = ... if not cfg.omni.enabled else None`
  配置级互斥——模式互斥由运行时（§4 D4）保证，lecture 与 omni 必须**同时可用**。
- **`application.py` 的服务创建是 `if cfg.omni.enabled: … else: …` 整体二选一（`:52-93`），必须改为并存
  ——二次 review 发现的严重遗漏**：现状 `omni.enabled=true` 时**只建 omni**（`omni_service` + 从
  `cfg.omni.system_prompt_path` 载入的 `system_prompt`），**ASR/LLM/TTS/`translate_prompt` 全为 None**
  （过去 omni 独占整条连接，这样写没问题）。模式化后必须：
  ① 三段式一套（`create_asr` / `create_llm` + `system_prompt = load_system_prompt(cfg.llm.system_prompt_path)`
  + `_with_agent_job_declaration(...)` / `translate_prompt = load_system_prompt(cfg.llm.translate_prompt_path)`
  / `create_tts`）**无条件创建**；② `cfg.omni.enabled` 时**额外**创建 `omni_service` 与
  **独立的 `omni_system_prompt`**（`cfg.omni.system_prompt_path`）；③ 两个 prompt 分别下发：
  `app.state.system_prompt`（对话/翻译用，保持 agent job 声明）与 `app.state.omni_system_prompt`
  （引擎开流用），否则两条路径必然互相污染（改错则 chat 用 omni prompt，或 omni 用 LLM prompt）。
- `services/gateway/endpoint.py`：删除 `:100` 的 orchestrator 二选一；**恒建 `DialogOrchestrator`**，
  新 kwarg 传入：`omni_service`（可 None）、`pipeline_sample_rate`（缺省 None → ctx 值）、
  **`omni_voice` 与 `omni_generation_opts`**（取值迁移自现 omni 分支 `:111-114`：
  `cfg.omni.providers[cfg.omni.active].voice` 与 `cfg.omni.generation.model_dump()`；
  `system_prompt` 复用既有 kwarg。缺这两项引擎开流拿不到 omni 音色与生成参数——二次 review 补全）。
- 未启用/创建失败时（`omni_service is None`）收到 `session.change(mode=omni)` →
  `error(1009, "omni mode unavailable", fatal=false)`，**不发 `session.changed`**：
  客户端 `onError` 会把开关回落为关并清掉重连恢复意图（按 1009 处理），不会死循环。
  *（2026-10-03 落地：曾列为备选的"新增专用 error code"已实施——`SESSION_CHANGE_FAILED = 1009`，
  见 [`session_mode_change.md`](session_mode_change.md)。）*

### D4 进入 / 退出 / 会话边界

- **模式取值**：进入时置 `self._pipeline_mode = "omni"`（新增枚举值；`:350` 的新会话重置自然覆盖）。
- **进入**（`_enter_omni_mode()`，镜像 `_enter_lecture_mode`）：先关 lecture 引擎（若在）→ `_cancel_pipeline()`
  + `_cancel_asr_poll()` → 并发静默关闭 ASR/TTS 流（`_close_quietly`，沿用 lecture 治理后的并发语义）→
  SM 复位 `LISTENING` → `TurnDetector.reset()`（`:672-673` 先例）→ `_asr_open_fail_time = 0.0`
  （`:687` 先例——否则进入前残留的 ASR 冷却会让退出后首帧音频被 `:1905-1908` 门控静默丢弃最长 5s）→
  `OmniModeEngine.open(...)` → **open 成功后才置** `_pipeline_mode="omni"` + `_omni_active=True`
  （置位顺序镜像 `:707-708`）。
- **进入时 open 失败（enter-failure，二次 review 增补）**：`OmniService.open` 全 provider 失败抛
  `ProviderError(OMNI_UNAVAILABLE)`（`omni/service.py:28-32`），`_enter_omni_mode` **必须 catch**——
  `run()` 的 dispatch 循环没有 per-message try/except（`:299-304`），不 catch 则 provider 抖动 = 连接
  主循环死亡（lecture 因此在 `:717` 兜底）。失败处理与 D3 同一路径：`error(1009, "omni mode
  unavailable", fatal=false)`、**不发 `session.changed`**、`_pipeline_mode` 保持原值（ASR/TTS 已置
  None，懒重开自然兜底，用户停留在原模式）。**不可用 1007**——1007 非 1009，客户端不会回落
  开关、也不清恢复意图（开关停留"开"且每次重连乐观重试再失败，§3.5 要避免的循环）。
  会话中途的流失败仍沿用 1007 + 懒重开（现状 omni 语义，此时无 pending 切换、不影响恢复意图）。
- **语言字段复位**：进入 omni 时与 chat 分支同款复位 `_my_language/_local_language/_loc_auto/
  _effective_local_language`（`:578-582`）——否则从 translate 切 omni 后 `session.changed` 回声
  （`:639-642` 读实例字段）会带出旧的 translate 语言对，污染客户端 `onSessionChanged` 回写。
- **退出**（`mode ∈ {chat, translate, transcribe}` 且当前 omni）：`_close_omni_engine()` → ASR/TTS 懒重开
  （与 lecture 退出同款：不预建，按需重建——ASR 懒重开在 `orchestrator.py:1905-1917`，**含 poll 任务
  重建**（`:1896` docstring 承诺"确保 poll 任务"，omni→transcribe 依赖此点），TTS 在 `:1049-1055`）。
- **重发同模式**（客户端断网恢复会重发 `session.change(omni)`）：按幂等处理——若已 `_omni_active`，
  仅回 `session.changed` + status，不重建引擎（避免打断在途回复）。dispatch 串行处理，
  open 进行中无并发重入窗口。
- **切出规则**：`_handle_session_change` 顶部把"切出"判定从 `lecture` 扩展为 `lecture | omni`——两者互斥，
  且**录音引擎与 omni 引擎不同时存活**（omni ↔ lecture 直切：先关旧引擎再进新引擎）。
- **会话边界**：`_handle_session_start`（:366-372）与 `_handle_session_end`（:481-500）、`run()` 收尾
  （:314-323）三处的引擎清理清单加入 omni 引擎；新会话无条件回到 chat（既有行为，客户端须知）。

### D5 采样率同源（防 1.5 倍慢放回归）

`DialogOrchestrator` 目前把 `ctx.audio_sample_rate`（客户端声明值）交给 ASR/TTS；而 OmniStream 的输出
**必须与 Opus 编码器同源**，否则复现刚修掉的 1.5 倍慢放（`docs/qwen_omni_realtime.md` §4.3）。

- 做法：`endpoint` 把 `output_sample_rate`（`= cfg.media.audio.default_sample_rate`）作为新 kwarg
  `pipeline_sample_rate` 传给 `DialogOrchestrator`；omni 引擎开流时用它（缺省回退 `ctx.audio_sample_rate`）。
  与 `OmniOrchestrator` 已落地的 C3 完全同构（`endpoint.py:118`）。
- 引擎下行音频复用编排器持有的 `_opus_enc`（会话率 16 kHz，与 omni nested `audio.output.format` 一致），
  **不另建编码器**（lecture 另建 24 kHz 编码器是因其输出率不同，omni 不需要）。

### D6 音频 / 图片 / 中断路由

- `_handle_audio`：在 lecture 分流之前插入 omni 分流——解码 Opus → `engine.feed_pcm(pcm)` → return
  （**不再进 TurnDetector/ASR**；VAD 由 provider 的 `semantic_vad` 承担）。
- `_handle_video`：omni 模式除入 buffer + `ai_video_ack` 外，best-effort `engine.feed_image(jpeg, w, h)`
  （照抄 `omni_orchestrator.py:471-488`：`ProviderError` → 关流并冷却、下帧懒重开）。官方图片约束
  （须先发过音频、≥480p、base64 ≤256KB、≤1080p）与抓屏 1920 长边已兼容（`docs/qwen_omni_realtime.md` §4.5）。
  〔2026-10-09 修订：**"base64 ≤256KB 已兼容"不成立**——该约束由网关按 WS 单帧 262144B 硬限执行
  （超限直接 close 1009；真机复现 + 探针实测见 `docs/fd_pipeline_avdetector.md` §10 条目 28），而相机/抓屏
  帧可超限。已实施防护：`feed_image` 前置两档重压（960→640，复用 `media.jpeg.image_resize`），
  失败/仍超 → warning 日志 + **跳过本帧**（不抛错——`_drop_stream` 会关掉整个 omni 会话流）。
  执行位置与代价：重压在 `_handle_video` 的 `await engine.feed_image(...)` 内经
  `asyncio.to_thread` 完成——**事件循环不冻结、其他任务（ASR/TTS/收发）照常**，仅该超限帧的
  dispatch 处理延长（单帧 ~0.1s 级，且只发生在超限帧；普通帧零成本）。与 avdetector 的差异：
  avdetector 的重压在 monitor 后台管道内执行；omni 引擎的 feed 为 dispatch 内直 await
  （§4 D2 引擎形态）——本次不新建后台 feed 管道，为当前形态下的接受项。〕
- `_handle_interrupt`：omni 分流 → 先 `self._interrupt.interrupt(reason)`（SM 推进，与 lecture 同款）
  再 `engine.interrupt(reason)` → `cancel_response()` + 清音频缓冲/`_tid` + `status(listening,"interrupted")`；
  引擎内 SPEECH_STARTED@SPEAKING → `user_voice` 自打断（提供方 VAD 触发；`should_yield(epoch)` 守卫沿用）。

### D6b **`video.payload.text` 门控必须排除 omni —— 本次 review 发现的严重项**

现状：`_handle_video` 的 v3 文本回合门控是
`self._pipeline_mode not in ("translate", "transcribe", "asr_only")`（`orchestrator.py:1491`，
同类门控见 `:849`/`:881`/`:1786`）。**omni 是新增模式值，不在此排除列表内**，而客户端在**每次抓屏**
都会带发图指令（已使用本地化 `conv_image_action_text`，17 locale；见 `docs/android_multilingual.md` §2.3.9/§21）——若不加排除，全模态模式下
每张抓屏都会：

1. 走 `_snapshot_turn_images()` 并把**指令文本**作为 user 消息写入 history / chat_store / recording（三件一体，`:1502-1521`）；
2. 条件中断（`:1525-1528`）；
3. `_start_thinking(text, image_frames=frames)` 起一轮**三段式 LLM + TTS**——`orchestrator.py:1049-1055`
   会懒重开 TTS 并让三段式音色说话（`:1191`），与全模态回复**双声叠放**、history 被指令污染。

**设计**：v3 门控的排除列表加入 `"omni"`（即全模态下 `payload.text` 忽略，图片仍 `feed_image` 作为视觉上下文）。
理由与 v3 §1.2「omni 路径本期不接线」一致；未来若要做"全模态抓屏直起一轮"，应在 omni 引擎内用
`conversation.item.create(input_text)` 实现（provider 目前未暴露文本入口）——**未实施，见 §10 TODO-1**。

**客户端配套（二次 review 拍板，§8.1-7）**：omni 模式下抓屏**不再发送 `payload.text`**（省带宽 +
语义干净；服务端忽略作为旧客户端/版本错配的兜底）。落点：`ScreenCaptureController.kt:624-627` 的
`sdk.pushVideoJpeg(..., text = context.getString(R.string.conv_image_action_text))`（2026-10-27 已实施）——omni 模式下改传 `text = null`；
控制器需能从既有 `CompositeLivaChatListener`/监听器取到"当前是否 omni"（注入一个只读查询，勿直连 UiState）。
注意交互模型变化：全模态下"免 ASR 直起一轮"降级为**抓屏作视觉上下文 + 用户开口提问才触发回复**
（`semantic_vad` 只认语音）——验收剧本见 §7.5-3。

**同族门控的处理**（避免后续误改）：ASR 派生门控 `:849/:881/:925/:943/:1786` 在全模态下**天然不可达**
（进入 omni 时已关 ASR 流并取消 poll 任务，见 D4）——本轮**不加** omni 排除，只在代码注释里说明
"omni 下不可达依赖 D4 的 ASR 关闭"；`:1491` 是唯一非 ASR 派生、必须改的门控。

### D7 落库 / 录制 / agent relay（与 chat 对齐）

- `INPUT_TRANSCRIPTION_DONE` → `history.add_message("user")` + `chat_store.on_message(kind="asr")` +
  `agent_publisher.publish_utterance`（沿用 `omni_orchestrator.py:521-547` 语义）。
- `RESPONSE_DONE` → `history.add_message("assistant")` + `chat_store.on_message(kind="llm")` +
  `recording_sink.on_chat_text(role="assistant")`。
- 全模态是**完整对话模式**，不适用 transcribe/translate 的落库排除规则。

### D8 状态机与 status 序列（与现状 omni 编排器一致）

`RESPONSE_STARTED` → `THINKING` + `status(thinking,"asr_final")`；首个 `AUDIO_DELTA` → `SPEAKING` +
`status(speaking,"tts_start")`；`RESPONSE_DONE` → `LISTENING` + `status(listening,"tts_finish")`；
模式切换回执后 `status(state.value,"mode_changed")`（既有）。中断 → `status(listening,"interrupted")`。

## 5. 消息流

### 5.1 切到全模态

```
C→S session.change{mode:"omni"}
S:   _cancel_pipeline + _cancel_asr_poll → 并发关 ASR/TTS → SM→LISTENING（+ TurnDetector/ASR 冷却复位）
S:   OmniService.open(sample_rate=16000, voice=Tina, ...) → OmniStream（含 nested audio 16k + semantic_vad）
     ├─ 成功 → 置 _pipeline_mode="omni" + _omni_active（D4：成功后才置位）
     └─ ProviderError → error(1009, fatal=false)，无 session.changed，停留原模式（D4 enter-failure）
S→C session.changed{mode:"omni"}
S→C status{state:"listening", reason:"mode_changed"}
```

### 5.2 全模态一轮

```
C→S audio(Opus) ── 解码 ──► engine.feed_pcm
C→S video(JPEG) ─────────► engine.feed_image（best-effort，需已发过音频）
C→S video(payload.text) ─► omni 模式客户端不再发送；旧客户端发来则忽略（D6b：不得触发三段式文本回合）
S:   input_audio_buffer.speech_started/stopped（semantic_vad）
S→C transcription(interim × N, 取 text+stash) → transcription(final)
S→C status{thinking, asr_final}
S→C ai_text(delta × N)
S→C status{speaking, tts_start} + ai_audio(Opus × N, 16 kHz)
S→C ai_text(final, 可带 image 快照) + status{listening, tts_finish}
```

### 5.3 切回对话/翻译/转录

```
C→S session.change{mode:"chat"|"translate"|"transcribe"}
S:   _close_omni_engine（close() 发 session.finish 后关 WS；音/图缓冲丢弃）
S:   （translate+lecture 目标则 _enter_lecture_mode；否则仅置 _pipeline_mode）
S→C session.changed{mode:...} + status{state, mode_changed}
S:   ASR/TTS 懒重开（首帧/首轮）
```

## 6. 客户端设计（Android App + SDK）

### 6.1 入口 UI（`AvatarCustomizationSheet.kt`）

**布局已定（取 A）**：在「同声翻译」组内、**转录模式行下方**新增第 4 行 ——
`SwitchRow(title="全模态", subtitle="使用全模态模型")`，与其它三行**同形**（title + subtitle），
互斥在同一视觉组内表达，改动最小。

```
┌─ 同声翻译 ────────────────────────────────┐
│ 对话模式   实时翻译对话内容        [switch] │
│ 听众模式   实时翻译演讲内容        [switch] │
│ 转录模式   实时转录现场声音        [switch] │
│ 全模态     使用全模态模型          [switch] │  ← 新增（本次取 A）
├───────────────────────────────────────────┤
│ ── 头像选择 ──                             │
│ 从相册选择 / 拍照 / 恢复默认头像            │
└───────────────────────────────────────────┘
```

若日后想改成独立分组（B：在行前插一个 `SectionHeader("全模态")`），仅 1 行改动、不影响互斥逻辑；
本次不做（避免 section 标题与行标题重复）。

### 6.2 互斥与开关态

- 新增形参 `omniModeEnabled: Boolean` / `onToggleOmniMode: (Boolean) -> Unit`；
  四行互斥：`enabled = !其余三个 flag`（既有三行各追加 `&& !omniModeEnabled`）。
- 开关"保持 on"沿用既有模式：`omniModeEnabled = uiState.isOmniMode || uiState.showOmniIntroDialog`。

### 6.2b 全模态下禁用屏幕交互（2026-09-27 追加需求，已实施）

- **点击录制/抓屏按钮**：`ConversationViewModel.toggleScreenRecording()` 首行判 `isOmniMode` → 不改动任何录制状态，
  以 `ConversationError.Unknown("全模态模式下不支持屏幕交互")` 提示（ErrorBanner，复用既有"不可用原因"通道）。
- **进入全模态时**：若正在录制（`screenCaptureController.isRecording`）→ 先 `stop()` 停掉屏幕采集，并关闭
  可能打开的"停止确认"弹窗（`showStopRecordingConfirm = false`）。
- 服务端侧无需改动：D6b 已保证 `payload.text` 被忽略、图片仅作视觉上下文。

### 6.3 弹窗（说明 + 确认，已定取确认式）

- 组件：`AlertDialog`（照 `ConversationScreen.kt:704-740` 的 picker 先例）；
- 文案（用户原文）：正文「**使用全模态AI模型，可以降低交互延迟**」；按钮「取消 / 开启」；
- 交互：开关打开 → 弹窗；「开启」→ 走 6.4 确认分支；「取消」/点外部 → 关闭弹窗、开关回落
  （与 picker 取消一致，**不**发送任何消息）。
- 说明：正文第二句为**产品承诺**，验收含真机延迟对比（§7.5-4）；若不达标，改文案而非改架构。

### 6.4 状态机与 SDK

| 层 | 改动 |
|---|---|
| `ConversationEvent` | 新增 `ToggleOmniMode` / `ConfirmOmniMode` / `DismissOmniDialog` |
| `ConversationViewModel` | 开：`showOmniIntroDialog=true`；确认：三 flag 复位 + `setOmniMode(true)` + `rememberModeRequest(ModeChangeRequest("omni"))` + `markModeChangePending("omni")` + `sdk.sendSessionChange("omni")`；关：四 flag 复位 + `rememberModeRequest(null)` + `sendSessionChange("chat")` |
| `ConversationListenerAdapter` | `_omniMode` StateFlow + `setOmniMode`；`clearSession()` 复位；`onSessionChanged` → `mode == "omni"`；**两处 `when` 各自新增 `"omni"` 分支**：① `session.ready` 重连恢复（`request.mode=="omni"` → 乐观置位 + 补发）② `onError` 回落（`"omni" -> _omniMode.value = false`）——**现行回落仅认 1009**（2026-10-03：`SESSION_CHANGE_FAILED`；`permanentModeRejectCodes` 已删除，见 `session_mode_change.md`），但漏写 ② 会让开关停在"开"（服务端实际未切），是本次最容易踩的实现坑 |
| `LivaChatSDK.SessionManager` | `sendSessionChange` 的 `when` 增加 `"omni" -> ClientSessionChangeMode.OMNI`（未知值告警丢弃的兜底保留） |
| 徽标（**必做**） | `ConversationScreen.kt:862-894` 徽标链（translate → transcribe）末尾追加 `else if (uiState.isOmniMode)`：26dp 圆 + BrandIndigo 0.7 alpha + `Surface` 1.5dp 描边（与既有同款）+ `Icons.Filled.AutoAwesome` + contentDescription「全模态模式」（`material-icons-extended` 已在依赖内，`LivaChatApp/build.gradle.kts:73`）。备选图标 `Icons.Filled.BlurOn` |

## 7. 测试计划

### 7.1 服务端单测（`services/tests/unit/session/`）

- `session.change(mode=omni)`：正常路径（ASR/TTS 关闭、引擎建/开、`session.changed` 回声、`status(mode_changed)`）。
- `omni_service is None`：回 `error(1009)`、**不发** `session.changed`、编排器状态不变。
- **enter-failure（§4 D4）**：`OmniService.open` 抛 `ProviderError` → 回 `error(1009)`、不发
  `session.changed`、`_pipeline_mode`/`_omni_active` 未变、**dispatch 主循环存活**（后续消息照常处理）、
  ASR/TTS 后续可懒重开。
- 非法 mode 仍回 error 1009（既有用例不回归）。
- 切出：omni → chat/translate/transcribe 各自关闭引擎并懒重开 ASR/TTS；omni ↔ lecture 互斥（**直切**：
  先关旧引擎再进新引擎，旧引擎 WS 收到 finish，SM 复位）。
- **重发幂等**：`_omni_active=True` 时重收 `session.change(omni)` → 仅回声 + status、引擎不重建
  （断言 `open` 调用次数不增）。
- **omni → transcribe**：ASR 懒重开**含 poll 任务重建**（`_feed_asr_with_recovery` 的 `:1896` "确保 poll
  任务"是唯一隐含依赖点，断言转录 interim 照常下发）。
- 会话边界：`session.start` 重置回 chat 并清理引擎；`session.end`/`run()` 收尾关引擎。
- 路由：omni 模式下 `_handle_audio` 不走 TurnDetector/ASR、`_handle_video` 触发 `feed_image`、`_handle_interrupt` 走 `cancel_response`。
- **D6b 专项**：全模态下带 `payload.text` 的 video 帧**不得**触发三段式轮——断言无 `ai_text`/`ai_audio`、
  TTS 流未被重开、history/chat_store 无该指令文本，且帧已 feed 给引擎（图片仍作视觉上下文）。
- D5：引擎开流用 `pipeline_sample_rate` 而非 `ctx.audio_sample_rate`（缺省回退用例各一）。
- 引擎事件映射（用 `FakeOmniProvider`）：RESPONSE_STARTED→TEXT_DELTA→AUDIO_DELTA→RESPONSE_DONE 的 s2c 序列与 chat 对齐；
  流终结后下一帧懒重开（镜像 `tests/integration/omni/test_omni_stream_end.py`）。

### 7.2 集成（`services/tests/integration/orchestrator/`）

- 新增 `test_omni_mode_switch.py`：切换延迟（`session.changed` ≤0.5s，镜像 `test_lecture_switch.py` 的慢 close 场景）。
- 复用 `tests/integration/omni/` 的 fakes；`test_omni_sample_rate.py` 的断言语义扩展到新引擎。
- **装配断言（C6 专项，落 `tests/integration/agent_relay/test_application_wiring.py` 同域，该文件已驱动真实
  lifespan + mock 外部依赖）**：`omni.enabled=true` 时 `asr/llm/tts/omni_service` **同时非 None**、
  `translate_prompt` 非空、`app.state.system_prompt` 来自 `cfg.llm.*`（含 agent job 声明）而
  `app.state.omni_system_prompt` 来自 `cfg.omni.system_prompt_path`（两者不同源，防互换）。
- 切换延迟度量复用 `scripts/measure_lecture_switch_latency.py` 的 harness 覆盖 omni 切换
  （`docs/translate_lecture_improvements.md` 的 ≤1s 治理成果保持同等观测）。

### 7.3 协议 / 生成物

- `make generate-android-api` 后 `make check-android-api-generated` 通过（CI 已有守卫）；
  Python DTO 侧 `ClientSessionChangeMode.OMNI` 可导入。

### 7.4 客户端

- `ConversationViewModelTest`：打开→弹窗→确认→发 `session.change("omni")` + 乐观置位；取消→不发消息、开关回落；
  四模式两两互斥（开 A 时其它三行 `enabled=false`）。
- `ConversationListenerAdapter` 单测：回声回写、1009 回落 + 清恢复意图、重连按 `lastModeRequest="omni"` 乐观恢复。
- Compose UI：sheet 新增行的文案/互斥/弹窗出现（沿用既有 Compose 测试基建）。
- 抓屏发送链：omni 模式下 `video.payload.text` 不再随帧发送；切回其它模式恢复（§8.1-7）。

### 7.5 真机验收（服务端 `omni.enabled=true`）

1. 会话内切「全模态」：`session.changed` 到达 ≤1s；切回 chat 后 ASR/TTS 恢复正常。
2. 全模态对话：音色/语速正常（= 刚修复的 16 kHz 路径）、interim 转写逐字出现、打断可用。
3. 抓屏（D6b 验收，**交互模型已变**，§4 D6b 客户端配套）：全模态下客户端不再发送 `payload.text`，
   抓屏只作视觉上下文——验收剧本：**抓屏后开口提问**（如"屏幕上是什么"），回复须正确引用最新抓屏帧，
   **且不得出现三段式抢答**（无三段式音色的 `ai_audio`、history 无指令文本）。旧客户端若仍发
   `payload.text`，服务端忽略（`docs/android_screen_recording.md` §5.3「omni 路径」）；
   v4 的等待态判据（下一轮 index==0 的 `ai_text`）在语音触发的那一轮同样命中。
4. **延迟对比（支撑弹窗文案）**：同设备同网络，chat 与 omni 各 5 轮，记录"用户说完→首个 ai_text/ai_audio"的
   p50；若 omni 未显著更低，需回改弹窗文案（§8 风险 2）。
5. 断网重连：重连后自动恢复全模态（`lastModeRequest`）并回声确认；`omni.enabled=false` 时选全模态 → 弹错并回落。

## 8. 风险与回滚（决策项已全部拍板，见文首）

| # | 风险 | 等级 | 说明与对策 |
|---|---|---|---|
| 1 | 事件泵实现迁移（`OmniModeEngine` 承接原 `OmniOrchestrator`） | 低 | 旧文件删除（D2，§8.1-6）后单一实现，无双份维护；`tests/integration/omni/` fakes 迁移复用、仅测新引擎 |
| 2 | 弹窗承诺"降低交互延迟" | 中 | 需 §7.5-4 真机量化；不达标改文案（不改架构） |
| 3 | `omni.enabled` 语义变更的文档面 | 低 | 实施时同步 `docs/fd_server_omni_model(.dev).md` 与 `configs/dev/server.yaml` 注释（C8/C12） |
| 4 | 四模式互斥与 lecture 引擎并存 | 低 | 切出判定扩展为 `lecture \| omni`；两引擎不同时存活（D4） |
| 5 | 全模态音色与三段式不一致（Tina vs 三段式音色） | 低 | 用户可感知；弹窗文案已提示"全模态模型" |
| 6 | 客户端无法预知服务端是否启用 omni | 低 | 采用"先试后回落"（4001 路径，客户端已有永久拒绝清除逻辑）；备选 `session.ready` 广播能力（本轮不做） |
| 7 | 回滚 | — | 服务端：`omni.enabled=false` 即入口失效（客户端 4001 回落弹错）；整体回退 = revert 部署（`omni_orchestrator.py` 从 git history 恢复）；客户端：入口按 build 灰度（如需） |

### 8.1 已拍板决策（2026-09-27）

1. **D1 = A**：会话内 `session.change(mode=omni)` + 新引擎；连接级路由废弃。
2. **UI = A**：同组第 4 行；**弹窗 = 确认式**（取消 / 开启）。
3. **`omni.enabled` 语义 = "允许该模式"**，并删除配置级互斥（`application.py:249`）。
4. **头像徽标 = 必做**（§6.4）。

**二次 review 增补（2026-09-27 同日，锚点经代码逐条核实）**：

5. **enter-failure = 4001 路径**：进入时 `OmniService.open` 抛 `ProviderError` 必须 catch（dispatch 主循环
   无 per-message try/except，`orchestrator.py:299-304`）→ `error(4001)` + 不发 `session.changed` +
   **open 成功后才置** `_pipeline_mode`/`_omni_active`；不可用 1007（不在 `permanentModeRejectCodes` 内，
   会造成重连重试循环，§4 D4）。
6. **`omni_orchestrator.py` = 删除**（推翻初版"保留死代码"：死代码在回滚中不会被重新装配，其测试亮绿灯
   只会给虚假信心；fakes 迁移复用，git history 存档）。
7. **omni 模式客户端停发 `payload.text`**（服务端 D6b 忽略作兜底）；交互模型 = 抓屏作视觉上下文 +
   开口提问触发回复。
8. **C5 装配参数补全**：`omni_voice` + `omni_generation_opts` 随 `omni_service`/`pipeline_sample_rate`
   一并传入 `DialogOrchestrator`（取值迁移自 `endpoint.py:111-114`，不可用 `cfg.llm.generation`）。

**三次 review 增补（2026-09-27 同日）**：

9. **应用装配改"并存"**：`application.py:52-93` 的 `if cfg.omni.enabled … else …` 拆开——三段式
   （asr/llm/tts/translate_prompt）**无条件创建**，omni 在启用时**额外**创建；**两个 system prompt 分离**：
   `app.state.system_prompt`（LLM，含 agent job 声明）与新增 `app.state.omni_system_prompt`
   （`cfg.omni.system_prompt_path`），分别下发给两条路径（详见 §4 D3 第三条、C5/C6）。
10. **客户端 `when` 分支补全**：`ConversationListenerAdapter` 的重连恢复与 `onError` 回落**各加一个
   `"omni"` 分支**（拒错码集合零新增，但漏写回落分支会让开关停在"开"，§6.4）。

## 9. 改动清单（预估，实施时按 §4 D1 拍板结果细化）

| # | 文件 | 改动 |
|---|---|---|
| C1 | `packages/api-schema/c2s/session-change.json`、`s2c/session-changed.json` | mode enum + 描述加 `omni` |
| C2 | `packages/sdk-python/.../generated/*`、`packages/sdk-kotlin/.../generated/*` | 由脚本重生成（禁止手改） |
| C3 | `services/session/omni_mode.py`（新） | `OmniModeEngine`：open/feed_pcm/feed_image/interrupt/close + 事件泵（蓝本 `omni_orchestrator.py:507-675`，含流终结块） |
| C4 | `services/session/orchestrator.py` | `_handle_session_change`（白名单 + omni 分支 + 切出判定 + 重发幂等）、`_enter_omni_mode`/`_close_omni_engine`（含 enter-failure catch + 语言字段/TurnDetector/ASR 冷却三复位，§4 D4）、`_handle_audio/_handle_video/_handle_interrupt` 分流、**`:1491` 门控排除 `omni`（D6b）**、三处引擎清理清单、新 kwarg `omni_service`/`omni_system_prompt`/`pipeline_sample_rate`/`omni_voice`/`omni_generation_opts` |
| C5 | `services/gateway/endpoint.py` | 删二选一（:100-127）→ 恒建 `DialogOrchestrator` 并传 `omni_service` + `pipeline_sample_rate` + `omni_voice` + `omni_generation_opts` + **`omni_system_prompt`**（前二取值迁移自现 omni 分支 `:111-114`；prompt 见 C6③） |
| C6 | `services/app/application.py` | ①服务创建 `:52-93` 由 if/else 改并存（三段式无条件 + omni 额外，含独立 `omni_system_prompt`）→ `app.state.omni_system_prompt`；②删 `:249` 配置级互斥；③`omni_enabled` 语义注释更新 |
| C7 | `services/session/omni_orchestrator.py` | **删除**（§8.1-6）；`tests/integration/omni/` fakes 迁移复用、仅测新引擎 |
| C8 | `configs/dev/server.yaml` | `omni.enabled` 注释改语义（允许模式）；真机验收期置 true |
| C9 | `services/tests/unit/session/`、`services/tests/integration/orchestrator/` | 新增用例（§7.1/7.2） |
| C10 | `apps/android/.../AvatarCustomizationSheet.kt`、`ConversationScreen.kt`、`ConversationViewModel.kt`、`ConversationEvent.kt`、`ConversationListenerAdapter.kt`、`ConversationUiState.kt`、`ConversationScreen` 徽标、抓屏发送链 | 入口 + 弹窗 + 状态机 + 徽标（§6）；omni 模式抓屏停发 `payload.text`（§4 D6b，§8.1-7） |
| C11 | `apps/android/LivaChatSDK/.../internal/session/SessionManager.kt` | `sendSessionChange` 增加 `"omni"` |
| C12 | `docs/fd_pipeline_transcribe_mode.md`（§"Omni 路径不支持"注记）、`docs/fd_server_omni_model(.dev).md` | 更新"omni 忽略 session.change"的旧结论 |

## 10. 后续增强（TODO，未实施）

> 本节只登记方向与已知约束，**不属于本次实施范围**；每一项开工前需单独出方案并 review。

### TODO-1 「点按即回一句」——给 `OmniStream` 加文本入口（omni 抓屏免说话直起一轮）

**目标**：omni 模式下点按抓屏后，不等用户开口就起一轮——模型看图 + 收到本地化发图指令（已实施 `conv_image_action_text`，16 locale）
后直接回复（`ai_text` + `ai_audio`），恢复三段式下 v3/v4 的抓屏体验（等待态由本次抓取的回复终结）。

**现状（为什么现在做不到）**：
- `OmniStream` 协议（`services/omni/base.py:31-38`）只有
  `feed_audio / feed_image / commit_user_turn / events / cancel_response / reset / close`——**没有文本输入**，
  provider 也未发送过 `conversation.item.create(input_text)`。
- omni 模式走 `semantic_vad`：回合由**语音**触发，图片只是"下一次提交"的视觉上下文（§4 D6b）。

**探针实测（2026-09-27，`services/scripts/probe_omni_image_text_turn.py`，可复跑）**：

| 变体 | 序列 | 结果 |
|---|---|---|
| V1 | `semantic_vad` + 音频 + 图 + `item.create(text)` + `response.create` | **✘ 30s 无任何回复**（无 error 帧）——保留 VAD 语义的路线**不可行** |
| V2 | `manual`（`turn_detection: null`）+ 音频 + 图 + `commit` + `item.create(text)` + `response.create` | **✔ 1.61s** 回「我看到一个红色的椭圆形，背景是白色的。」——**唯一可行路径** |
| V3 | `manual` + 仅图（无音频）+ text | 精确报错 `Error append image before append audio.`（"先发音频"约束被证实） |

**结论（推翻本文档初稿的路线排序）**：可用的只有 **manual 语义**那条；而 omni 模式当前跑
`semantic_vad`（官方约束：VAD 配置须在会话 IDLE 阶段、首段音频前完成，**运行中不可切**）——因此本 TODO
真正要 review 的是**架构选择**，不是接口细节：
- (a) omni 模式整体改 `manual` + 服务端自管回合（VAD 用本地 `TurnDetector` 复刻）：点按即回一句成为可能，
  但要重做「语音回合」的判停/打断链路，风险大；
- (b) 维持 `semantic_vad`，**放弃**"点按即回一句"（保持现状：图作下一轮上下文，用户开口才回）；
- (c) 混合：omni 会话仍 manual，但仅在"抓到图 + 有 text"时手动起一轮、其余仍由服务端 VAD 判停——
  需先验证 `turn_detection: null` 下服务端是否仍发 `speech_started/stopped`（本次未测，探针可扩展）。

**实现方向（供将来评审，两条路）**：
1. **协议扩展**：`OmniStream` 增 `send_user_text(text)`（或 `request_response(text=...)`）→ provider 发
   `conversation.item.create(input_text)` + `response.create` 直起一轮；引擎在抓屏路径调用它。
   客户端恢复发送 `payload.text`（当前 omni 下已停发），服务端 omni 分支消费该文本（不再按 D6b 忽略）。
2. **复用现有事件**：引擎用 `feed_image` + `commit_user_turn()` + 手动 `response.create`——但
   `commit_user_turn()` 现为 no-op（VAD 模式自动提交），需要先解决提交语义。

**已知约束（本次探针实测，务必带入方案）**：
- **manual 模式已验证可用**：`turn_detection: null` + `音频 → 图片 → input_audio_buffer.commit →
  conversation.item.create(input_text) → response.create` 顺序实测通过（640×360 红色圆形 → 模型答
  「圆形，红色」，见 `docs/qwen_omni_realtime.md` §4.5）。
- **VAD 模式下显式 commit 会报错**：`semantic_vad` 下自行发 `input_audio_buffer.commit` 曾收
  `no accepted speech` / `buffer too small, or have no audio`——图片随音频自动提交的语义不能与手动提交混用。
  （配合上表 V1/V2：VAD 路线在本序列下既不能 commit、`response.create` 也不产出回复。）
- **图片限制**：发送 `input_image_buffer.append` 前须已发过至少一次音频（现状满足：会话持续上行麦克风）；
  图片 ≤1080p、base64 ≤256KB。

**验收草案**：点按后不开口 → 收到该轮的 `ai_text`(index 从 0) 与 `ai_audio`（≤3s 起首包）；等待态正常终结；
不说话时不得刷出多轮；切出 omni 后三段式行为不变。

> **2026-10-02 增补（见 §11）**：探针进一步证明 `response.create` **单独**即可起一轮（无需
> `conversation.item.create(text)` 入口），且 manual 下语音回合实测可用——TODO-1 的"实现方向 1"
> 相应简化（无需新增 text 协议入口）；建议 TODO-1 与 §11 的 L2 合并为一个专项立项评估。

## 11. 增补设计（2026-10-02）：omni 独立 Prompt 与主动互动（**待 review**）

> 需求：① omni 的 system prompt 改用**独立文件**（不再与 pipeline/三段式共用 `companion.txt`）；
> ② 新 prompt 加入**主动互动性**——不等用户提问：听到咳嗽声、喷嚏声，或看到有意思的图片，都可
> 主动开启聊天。

**结论先行**（探针证据见 §11.2）：

1. **① prompt 拆分 = 纯配置变更，零代码**：装配层早已支持两 prompt 分离（`application.py:88-95`，
   omni 走 `cfg.omni.system_prompt_path`）；当前 dev 两边同指 `companion.txt`（`server.yaml:81` vs
   `:156`）。改法 = 新增 omni 专用文件 + 改一行路径。
2. **② 的完整形态（无用户开口即开口）在现行 VAD 架构下不成立**——`semantic_vad` 对非语音声
   **不起回合**（探针 A：合成咳嗽静候 25s 零事件零回复）；prompt 再主动，没有回合也无处表达。
3. **但机制已被探针打通**：`manual(turn_detection:null)` 下，**单独一条 `response.create` 即可
   起一轮**，模型会基于最近"听到/看到"的上下文**自发点评**——图片（探针 C/C2：无任何 text，
   1.4s 点评机器人图）与声音（探针 D：咳嗽声 →「听到了，是两下清脆的敲击声」）均成立；manual
   下**语音回合同样可用**（探针 E 正确应答 9.7s 真实语音）。
4. 因此设计**分两期**：**L1**（prompt 拆分 + 主动条款文案，本次可做、零架构风险，但只在"有回合
   时"生效）；**L2**（真正"无开口主动" = omni 转 manual 架构 + 触发源设计，与 §10 TODO-1 合并
   评估，**需单独拍板**）。
5. 声音事件（咳嗽/喷嚏）检测**全仓无既有组件**；图片触发可复用既有抓屏帧链路。

### 11.1 现状锚点（codebase 事实，逐条核实）

| # | 事实 | 锚点 |
|---|------|------|
| 1 | 两 prompt 已分离装配：`app.state.system_prompt`（三段式，含 agent job 声明）vs `app.state.omni_system_prompt`（`cfg.omni.system_prompt_path`），分别下发 | `application.py:88-95, 261`；本文档 §4 D3 第三条 |
| 2 | 当前 dev 两者同指 `companion.txt` | `server.yaml:81`（llm）/ `:156`（omni） |
| 3 | omni 回合 = 服务端 semantic_vad 判停 + `create_response:true` 自动应答；`commit_user_turn()` 为 **no-op**（"手动 turn 模式钩子留待后续"） | `alibaba_omni.py:353-356`；session.update `:485` |
| 4 | 常规链路从不发送 `conversation.item.create` / `response.create`（仅探针用过） | `alibaba_omni.py` send 事件面（append/clear/cancel/finish） |
| 5 | 本地回合判停组件已存在：`TurnDetector`（连续静音帧判停，low/medium/high = 900/600/400ms），三段式在用 | `services/session/turn_detector.py:9-30` |
| 6 | 声音事件（咳嗽/喷嚏）检测组件：**无**（services/apps 全仓 grep 零命中） | — |
| 7 | 抓屏帧链路既有：客户端持续上行帧 → 编排器 best-effort `feed_image`；图作"下一次提交"的视觉上下文 | `omni_mode.py:117-125`；本文档 §4 D6b |
| 8 | 先例：§10 TODO-1 已探「点按即回一句」（V1 ✘ VAD 路线 / V2 ✔ manual 路线） | §10 TODO-1 |

### 11.2 真机探针（2026-10-02，`/tmp/lvomni_probe/probe.py`，可复跑；qwen3.8-omni-flash-realtime）

方法：raw websockets 直连（与 provider 同协议）；`instructions` 含主动条款；合成咳嗽 = 双噪声
爆发（**非真人咳嗽**，见限制）；图片 = 机器人渲染图 640×640 JPEG。

| 变体 | 会话配置 | 序列 | 结果 |
|------|---------|------|------|
| A | semantic_vad | 合成咳嗽声（无语音、无 response.create），静候 25s | **零事件、零回复**（连 speech_started 都没有）——VAD 对非语音声不起回合 |
| C | manual | 低幅环境噪声 → 图片 → commit → response.create（**无 text**） | ✔ 1.42s「我看到画面里有一个可爱的白色机器人，有着蓝色的眼睛和天线。」 |
| C2 | manual | 同 C，**不发 commit** | ✔ 1.58s 同样成功（commit 非必需） |
| D | manual | 合成咳嗽声 → commit → response.create（无 text） | ✔ 0.97s「听到了，是两下清脆的敲击声。」 |
| E | manual | 真实语音 9.7s（`~/projects/asr_test1.wav`）→ commit → response.create | ✔ 1.02s 正确应答语音内容；**manual 下服务端不发 speech_started/stopped** |

**衍生事实**：

1. `response.create` **单独**即可起一轮——不需要 `conversation.item.create(text)`（比 TODO-1
   V2 路径更简：主动点评无需构造文本指令）。
2. manual 下对**无语音缓冲** commit 不报错（VAD 模式会报 no accepted speech——TODO-1 已录）。
3. manual 下模型对"最近的听觉/视觉上下文"自发表达；D 的限制：**合成咳嗽被识别为"敲击声"**——
   机制成立，真实咳嗽的识别质量未直接验证（需真人样本）。
4. manual 模式语音回合可用（E：模型正确理解并应答真实语音）——为 L2 选项 O1 补上关键拼图。
5. 事件序列（manual）：`conversation.item.created → …transcription… → input_audio_buffer.committed
   → transcription.completed → response.created → …`；**无 speech_started/stopped**。

产物：`/tmp/lvomni_probe/`（`probe.py`、`cough.wav`、`probe_image.jpg`、`report.json`）。

### 11.3 设计

**D9（L1，本次建议实施）：omni 独立 prompt 文件 + 主动条款**

- 新增 `configs/dev/prompts/omni_companion.txt`（名待拍板）：基座 = 现 `companion.txt` 全部内容
  （对话纪律不变），追加「主动互动」段；`server.yaml:156` 指向新文件；`llm` 段与 `companion.txt`
  **零改动**（三段式不受影响）。
- 主动条款草案（供 review 逐句改）：

```
【主动互动】
你不必总是等用户先开口——你的耳朵和眼睛一直开着：
- 听到明显的咳嗽、喷嚏、哈欠等生活声音，或画面里出现新变化、有意思的东西时，可以自然地主动
  关心或开启话题（如「是不是嗓子不舒服？」「这台机器人好可爱，是你做的吗？」）。
- 主动开口要简短、一次只接一件事；刚主动过一次后，除非出现新的明显事件，保持安静。
- 只在真正听到/看到的时候开口；没有把握的内容不要编造，可以轻轻问一句确认。
- 用户开口后，先完整回应用户，再补充你注意到的画面或声音。
- 画面涉及隐私或敏感内容时，不主动点评。
```

- **效果边界（诚实声明）**：现行 VAD 架构下，以上条款只在**有回合发生时**影响表达倾向（如用户
  开口后的补充、点评）；**不会**产生"无用户开口的主动开口"（探针 A 为证）——完整主动需 L2。

**D10（L2，需拍板）：完整主动互动 = manual 架构 + 触发源（与 TODO-1 合并评估）**

- 机制两面（探针已证）：**触发** = 在"决定性时刻"由服务端发 `response.create`；**能力** = 模型
  基于最近上下文自发点评/回应（C/C2/D/E 全绿）。
- 架构选项（TODO-1 的 (a)(b)(c) 收拢为两个真选项）：
  - **O1 manual 全迁**：语音回合改由**本地 `TurnDetector`** 判停（组件既有，三段式在用）→ 判停时
    `commit + response.create`；主动事件（图片/声音）到达时直接 `response.create`。全部能力可达；
    代价 = 语音回合/打断/转写链路需重做并专项验收（抢答、静默、连续对话）。
  - **O2 维持 VAD（现状）**：放弃"无开口主动"，仅落实 D9 文案。
  - （原 (c) 混合不可行：VAD 配置会话内不可切、VAD 下 response.create 无效——探针 A 与 TODO-1 V1
    双重否定；manual 下又无服务端 speech 事件，所谓"混合"只剩 O1 一种实现。）
- 触发源设计：
  - **图片**：既有抓屏帧链路即候选触发点；"有意思"判定无组件 → v1 粗策略（帧变化 + 冷却窗口，
    如 ≥N 秒间隔且画面变化超阈值）或点按（= TODO-1）；频率上限必须（见 R10）。
  - **声音**：咳嗽/喷嚏检测**无既有组件**——选项：(i) 客户端轻量声音事件分类（新组件，Android
    侧）；(ii) 服务端引入检测模型（新依赖）；(iii) 暂缓，先做图片主动。建议 (iii) 起步。
- 与 TODO-1 的关系：同一机制（manual + response.create）；本次探针把 TODO-1 的实现方向 1
  **简化**（无需 text 协议入口即可起一轮），建议 L2 与 TODO-1 合并为一个专项立项。

### 11.4 改动清单（L1；L2 待拍板后另立清单）

| # | 文件 | 改动 |
|---|------|------|
| C13 | `configs/dev/prompts/omni_companion.txt` | 新增（基座 + 主动条款，§11.3-D9） |
| C14 | `configs/dev/server.yaml:156` | `omni.system_prompt_path` → `configs/dev/prompts/omni_companion.txt`；注释同步 |
| C15 | 本文档 §11、文首状态行 | 增补记录 |

测试：现有 `test_application_wiring`（omni prompt 用临时文件）零影响；零费用验收 = 启动后
`app.state.omni_system_prompt` 来自新文件（配置级检查即可）。回滚 = 路径改回一行。

### 11.5 风险

| # | 风险 | 等级 | 对策 |
|---|------|------|------|
| R8 | 主动条款"语气过头"（频繁搭话/冒犯） | 中 | 文案内置频控条款（一次一事 / 刚开过口保持安静 / 隐私不点评）；上线后收集体感再调文案 |
| R9 | L2 manual 迁移影响现有 omni 验收（抢答/打断/转写） | 高 | 单独立项 + 专项验收；`TurnDetector` 灵敏度（low/medium/high）复用现有配置面 |
| R10 | 主动触发频率失控（图片帧持续到达） | 高（L2） | 冷却窗口 + 每窗口上限 + 与打断/播放态互斥（正播报时不主动） |
| R11 | 合成咳嗽与真人咳嗽识别差异 | 低 | D 已证机制；真人样本验收归入 L2 |

### 11.6 review 问题（拍板项）

1. **L1 是否先实施**（prompt 拆分 + 主动条款，零架构风险）；文件名 `omni_companion.txt` 是否合适；
   条款文案逐句。
2. **L2 方向**：O1（manual 全迁，与 TODO-1 合并专项）还是 O2（维持 VAD，仅文案）？
3. **声音检测源**：客户端 / 服务端 / 暂缓（建议暂缓，先图片）。
4. L2 触发策略：图片"变化 + 冷却"粗触发 vs 仅点按（TODO-1）。

## 附录 A：与既有文档的口径对齐

- `docs/fd_pipeline_transcribe_mode.md:1.2` 曾写「Omni 路径不支持：`OmniOrchestrator._dispatch` 没有
  `session.change` 分支——维持现状，零改动」：本设计**推翻该结论**（omni 由连接级路径改为会话内模式），
  实施时同步该文档注记（C12）。
- `docs/qwen_omni_realtime.md` §5 D3 的 `semantic_vad`、§4.3 的 16 kHz 直配、§4.4 的转写双读，
  在模式化后**同样生效**（引擎复用同一 provider 与 payload）。
