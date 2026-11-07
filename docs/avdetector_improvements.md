# AVDetector 会话级开关（App 设置 + 协议增强）— 分析与设计

> **状态**：**已实施（2026-11-06）**
> **日期**：2026-11-06
> **需求来源**：用户提出的三条增强（App 设置 / `session.change` 配置模式 / `session.start` 配置字段）
> **代码基线**：当前分支 `dsv4fash/dev`（2026-11-06）
> **修订（2026-11-06，用户拍板）**：`session.changed` 对 config 请求也回显 `mode="config"`（与请求保持一致）；
> 当前为研发阶段、未上线，客户端 SDK 同步拦截该控制回声，不送入模式状态机。
>
> **复审修复（2026-11-06）**：
> ① `_ensure_av_monitor` 必须增加 `_pipeline_mode == "full"` 门——否则在 translate/omni 下收到
> config enable 会误挂载 monitor；
> ② SDK 必须处理“`session.start` 已发、`session.ready` 未到”窗口（`pendingAvdetectorChange`，
> ready 后补发），且连接断开时复位 `sessionReady`（Android 需桥接 `connectionState`）；
> ③ `_on_av_event` 在 await 后同时复检 `_pipeline_mode` 与开关，防 await 窗口内切模式/关闭；
> ④ `make_session_changed` 的 `avdetector` 必须条件加入 kwargs（显式 `None` 会序列化成 `null`）；
> ⑤ 增加 monitor generation 校验，防止 disable→enable 后旧 monitor 的迟到事件复活；
> ⑥ **config 失败改用独立错误码 `SESSION_CONFIG_FAILED = 1011`**，绝不复用 1009——否则客户端会把
> config 失败当成一次真实模式切换被拒，误回滚 `pendingModeChange`；旧服务端返回的 1006 由 SDK 在
> “config 单飞”上下文中识别为不支持并静默降级；
> ⑦ **设置项文案已确认**：“允许 AI 主动搭话”/“Enable Proactive AI Care”（原“允许AI主动发起会话”
> 易误解为自动建立新会话）；
> ⑧ `_on_av_event` 的所有丢弃分支必须在**第一个持久化写之前** return——丢弃是零副作用
> （history/chat_store/recording 聊天文本均无新增），不只是不 `_spawn_pipeline`。
>
> **复审修复（第二轮）**：
> ① `session.start.config.avdetector` 只允许“字段缺省 = enable”，显式 null 非法；Schema 不写 null，
> 服务端用 `model_fields_set` 区分并拒绝显式 null（1006）；
> ② 修正 Android `startSession()` 代码锚点：它只构造 payload，`SessionManager.sessionId` 在
> `handleSessionReady` 才赋值；
> ③ 修正 Python DTO `extra` 描述：`ClientSessionStartConfig` 是 allow，
> `ClientSessionChangePayload`/`ServerSessionChangedPayload` 才是 forbid；
> ④ 测试计划按层拆分错误码：DTO/集成非法枚举 1006；直接 orchestrator 的 config 防御性测试 1011；
> 非 config 非法 mode 防御性测试 1009；
> ⑤ `common.json` 与 `packages/api-schema/README.md` 文件头版本说明同步 v1.2 → v1.3。
>
> **最终实现复审（2026-11-06）**：
> ① `make_session_changed` 只序列化非 `None` 的可选字段——修复 config 回声/普通模式回声输出
> schema 不允许的 `null`（`my_language`/`local_language`/`usecase`）；
> ② Android/iOS 在 `endSession` 与 `session.ended` 时立即复位 AV config 生命周期，防止“已挂断、
> 连接尚未断开”窗口仍发送无归属的 `session.change(config)`；
> ③ 英文设置项文案统一为已确认的 **“Enable Proactive AI Care”**。
>
> **实施记录（2026-11-06）**：Schema/DTO、服务端、Android SDK/App、iOS SDK/App 均已实现。
> 服务端 `pytest tests/unit` 1652 passed；`tests/integration/orchestrator` 71 passed / 1 skipped；
> Android `LivaChatSDK`/`LivaChatApp` unit tests 通过；iOS `LivaChatSDK` 74 tests、`LivaChatAppTests`、
> `ios-i18n-check` 通过。网关集成测试因本机缺少 `.env`（`APP_AUTH_TOKEN`）未能启动 uvicorn，
> 属于环境缺失，非代码失败。
> **关联文档**：
> - `docs/fd_pipeline_avdetector.md`（AVDetector v1 设计；本文修订其 §4 D7“协议与客户端影响：零变更”的结论）
> - `docs/ws-protocol-design.md`（v1.3；本文对应其 v1.3 变更）
> - `packages/api-schema/README.md`（Schema 与协议文档同步约定）

---

## 结论先行

1. **开关是“会话级”的，不是部署级开关的替代**：最终是否运行 AVDetector =
   `服务端全局 cfg.avdetector.enabled` **且** `当前会话 avdetector_enabled` **且** `当前处于 chat 模式`。
   全局关闭时，客户端会话级 `enable` 不会凭空创建服务（沿用现有装配语义）。
2. **`session.start.payload.config` 新增可选 `avdetector: "enable" | "disable"`**；**仅字段缺省/
   未携带**视为 `"enable"`，**显式 `null` 非法**（Schema 不允许；DTO 若接受 null，服务端按 1006
   malformed 拒绝）。这样**老客户端不携带该字段时行为与现状完全一致**。
3. **`session.change` 新增 `mode: "config"` 控制模式**，payload 携带 `avdetector`；它只修改当前会话的
   AVDetector 开关，**不切换 pipeline mode**，不触发 `status(mode_changed)`。
4. **`session.changed` 回声与请求保持一致**：`session.change(mode="config")` 的回复也是
   `session.changed { mode: "config", avdetector: ... }`。该回声只表示“配置已应用”，**不表示
   pipeline mode 变为 config**；服务端不发送 `status(mode_changed)`。客户端 SDK 必须把
   `mode="config"` 识别为控制回声（只记录，不送进现有模式状态机），普通模式切换的回声不受影响。
   当前为研发阶段、未上线，客户端可同步升级；旧客户端不会发送 `config`，因此不会收到该回声。
5. **App 设置**：Android / iOS 的“通用设置”新增“允许 AI 主动搭话”开关（产品已确认；原需求文案
   “允许AI主动发起会话”易被误解为自动建立新会话），默认开启并本地持久化。
   修改后：
   - 先落盘；
   - 若当前有活跃会话，SDK 发送 `session.change { mode: "config", avdetector: ... }` 立即对当前会话生效；
   - 下一次 `session.start` 从持久化值携带该字段（重连/新会话不会丢设置）。
6. **SDK 公共 API 不暴露 `mode: "config"`**：继续使用 `SessionMode`（chat/translate/transcribe/omni）表达
   用户模式；新增 `setAvdetectorEnabled(Bool)` 内部完成“更新下一次 session.start 的值 + 有活跃会话时发配置
   变更”两件事。协议层的 `ClientSessionChangeMode` / `ServerSessionChangedMode` 增加 `CONFIG`，
   仅 SDK 内部使用（入站 config 回声中拦截，不映射到 App 的 `SessionMode`）。
7. **错误码与兼容性**：新服务端对 config 失败使用独立错误码 **`SESSION_CONFIG_FAILED = 1011`**，
   **1009 只属于真实模式切换被拒**。新客户端 + 旧服务端时，`session.start` 的新字段会被旧服务端的
   `additionalProperties: true` 接受但忽略；`session.change(mode="config")` 会被旧服务端以 1006
   拒绝。SDK 在“config 单飞、等待回声”的上下文中把该 1006 识别为“旧服务端不支持 config”并静默降级：
   **不向 App 抛通用错误、不回滚模式状态、不再重发 config**；本地设置保留，下一次 `session.start`
   仍会携带。这样既不会触发 Android/iOS 的 1009 模式回滚逻辑，也不会因重复 config 帧累积
   `decode_guard` 计数。

---

## 1. 需求拆解

| # | 需求 | 本文设计落点 |
|---|---|---|
| 1 | App 设置“通用设置”里加一项“允许 AI 主动搭话”（已确认；原需求文案“允许AI主动发起会话”），可按会话 enable/disable AVDetector | Android/iOS 设置项 + 本地持久化 + SDK `setAvdetectorEnabled` |
| 2 | 增强 `session.change`：`mode` 新增 `"config"`，携带 `avdetector: "enable" | "disable"`，服务端设置当前会话状态 | `session.change` schema + orchestrator `config` 分支 |
| 3 | 增强 `session.start`：`config` 新增 `avdetector: "enable" | "disable"`，默认 enable；App 保存设置，新会话携带 | `session.start` schema + `SessionContext` 字段 + SDK 每次 `session.start` 读取当前值 |
| 4 | 以 codebase 为基础，不猜测；产出分析与设计文档 | 本文所有结论均对应现有文件/行为锚点 |

**命名说明**：需求文字中出现过 `avdector` / `avdectector`，代码与现有配置统一使用 **`avdetector`**
（`cfg.avdetector`、`services/avdetector/`、`docs/fd_pipeline_avdetector.md`）。本文及实现统一使用
`avdetector`，避免出现第二套拼写。

---

## 2. 现状核实（代码事实）

### 2.1 服务端：AVDetector 当前是“全局开关 + 会话自动挂载”

| 位置 | 现状 |
|---|---|
| `services/shared/config/models.py:189-210` | `AvDetectorConfig.enabled` 是**进程级**总开关；`configs/dev/server.yaml:195-216` 当前部署值为 `true` |
| `services/app/application.py:108-117` | `cfg.avdetector.enabled` 为真才创建 `avdetector_service`；否则 `app.state.avdetector_service = None` |
| `services/gateway/endpoint.py:131-134` | 把 `avdetector_service` / `avdetector_system_prompt` / `avdetector_cfg` 透传给 `DialogOrchestrator` |
| `services/session/context.py:20-28` | `SessionContext` 只携带 `session.start` 的 `enable_video` / `audio_sample_rate` / `vad_sensitivity` / `client_version` / `language` / `timezone`，**没有 AVDetector 会话字段** |
| `services/session/manager.py:70-90` | `SessionManager.start(...)` 接收会话配置并构造 `SessionContext`（`:141-153`） |
| `services/session/orchestrator.py:492-585` | `_handle_session_start`：先关旧 monitor、取消在飞 pipeline，复位模式与 per-turn 状态，然后 `session_mgr.start(...)`，最后 `_ensure_av_monitor()` |
| `services/session/orchestrator.py:977-998` | `_ensure_av_monitor()` 只看 `_av_monitor is None`、`_avdetector_service is None`、`_ctx is None`；**没有会话级开关判断** |
| `services/session/orchestrator.py:733-869` | `_handle_session_change`：允许 `chat/translate/transcribe/omni`；非 chat 先 `_close_av_monitor()`（`:760-762`）；切回 chat 时 `_ensure_av_monitor()`（`:805`） |
| `services/session/orchestrator.py:1420-1422` | 音频热路径：`if self._av_monitor is not None: self._av_monitor.feed_audio(pcm)` |
| `services/session/orchestrator.py:1551-1560` | turn-end 空识别分支：`monitor.ready()` 且状态/声音活动满足时 `request_audio_check()` |
| `services/session/orchestrator.py:2328-2347` | 非 text 视频帧：`feed_frame(...)` + 节流/Gate 通过时 `request_video_check()` |
| `services/session/orchestrator.py:1080-1087` | `_on_av_event` 只检查 `_ctx is None` / `_pipeline_mode != "full"`，**没有检查会话级开关** |
| `services/session/orchestrator.py:1000-1010` | `_close_av_monitor()` 置空并 `await monitor.close()`；monitor 的热路径在置空后天然短路 |
| `services/gateway/codec.py:125-137` | `make_session_changed(...)` 只构造 `mode/my_language/local_language/usecase` 四个字段 |

**结论**：当前“关闭 AVDetector”只有部署级配置一种方式，且只能重启/改配置；会话内没有任何开关状态。
本需求需要新增**会话级状态**，并把现有所有“monitor 是否存在”的判断扩展为“monitor 存在且会话开关为开”。

### 2.2 协议与 Schema 现状

| 文件 | 现状 |
|---|---|
| `packages/api-schema/c2s/session-start.json:29-62` | `config` 为对象，`additionalProperties: true`；已有 `enable_video/audio_sample_rate/vad_sensitivity/client_version/timezone/language`，**无 `avdetector`** |
| `packages/api-schema/c2s/session-change.json:18-42` | `payload` 为 `additionalProperties: false`；`mode` 枚举为 `chat/translate/transcribe/omni`，**无 `config`**；无 `avdetector` 字段 |
| `packages/api-schema/s2c/session-changed.json:18-42` | `payload` 为 `additionalProperties: false`；`mode` 枚举同样四值；无 `avdetector` 回声字段 |
| `packages/api-schema/common.json:123-140` | 已有 `SessionStatusCode` / `StatusReasonCode`；无 AVDetector 相关枚举 |
| `services/shared/utils/errors.py:35-40` | `SESSION_CHANGE_FAILED=1009` 已定义为模式切换被拒专属；`AVDETECTOR_UNAVAILABLE=1010` 仅内部使用、从不下发；**1011 空闲** |
| `docs/ws-protocol-design.md:1-12` | 协议版本 v1.2；文档声明与 `packages/api-schema/` 同步更新 |
| `docs/ws-protocol-design.md:131-166` | `session.start` 字段表；`config` 兼容未定义字段 |
| `docs/ws-protocol-design.md:187-215` | `session.change` 字段表与行为；非法 mode 在协议层被 1006 拦截，业务层 1009 防御（config 改走 1011，见 D8） |
| `docs/ws-protocol-design.md:403-425` | 现状：`session.changed.mode` 定义为“切换后生效的模式”，且其后跟 `status(mode_changed)`；本次为 config 回声增加例外（D4） |

**结论**：新增字段/枚举必须同时修改三份 Schema，并重新生成 Python/Kotlin/Swift DTO；否则：
- 服务端 `ClientSessionStartConfig` 虽为 **`extra="allow"`**（未知字段可保留），但
  `ClientSessionChangePayload` / `ServerSessionChangedPayload` 是 **`extra="forbid"`**，
  缺字段/未知字段会解析失败；
- 客户端 `session.change` 无法构造 `mode=config`；
- 服务端 `session.changed` 无法携带 `avdetector` 回声。

### 2.3 Android 现状

| 位置 | 现状 |
|---|---|
| `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/domain/model/UserPreferences.kt:3-14` | 用户偏好数据类；无 AVDetector 字段 |
| `.../data/local/PreferencesDataSource.kt:20-31, 46-60, 97-109` | DataStore 映射与 setter；新增偏好需加 key、映射、setter |
| `.../data/repository/SettingsRepository.kt:14-33` | 偏好读写透传层 |
| `.../domain/usecase/SettingsUseCase.kt:14-31` | 设置用例门面 |
| `.../ui/screens/settings/SettingsUiState.kt:13-30` | 设置页状态；无 AVDetector 字段 |
| `.../ui/screens/settings/SettingsViewModel.kt:44-62, 84-94` | 收集偏好、提供 setter |
| `.../ui/screens/settings/SettingsScreen.kt:194-204` | “General”分区当前只有“Haptic feedback”一项 |
| `.../ui/screens/conversation/ConversationListenerAdapter.kt:88-97, 334-353` | `pendingModeChange` + `SESSION_CHANGE_FAILED=1009`：任何 1009 都会清 pending、回滚 translate/transcribe/omni、清 `lastModeRequest`，并向 UI 发通用错误 |
| `.../di/AppModule.kt:55-74` | `LivaChatConfig` 在 Hilt Singleton 构造时固定；`languageProvider` 是唯一每次 `session.start` 求值的动态值 |
| `apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatConfig.kt:1-94` | SDK 配置数据类；除 `languageProvider` 外均为实例生命周期固定值 |
| `.../internal/session/SessionManager.kt:156-167` | `startSession` 使用 `configProvider()` 构造 `ClientSessionStartPayload` |
| `.../internal/session/SessionManager.kt:202-228` | `sendSessionChange(mode: String, ...)` 把字符串映射到生成枚举；无 `config` 分支 |
| `LivaChatSDK.kt:132-153` | `configProvider` 组装 `ClientSessionStartConfig`；`:231` `startSession`；`:332` `sendSessionChange` |

**结论**：Android 设置页与 SDK 的链路清晰；需要新增一个 DataStore 偏好、一个 Settings UI 项，并让
SDK 能在实例生命周期内动态更新 `avdetector` 值（不能只依赖构造时固定的 `LivaChatConfig`）。

### 2.4 iOS 现状

| 位置 | 现状 |
|---|---|
| `apps/ios/LivaChatApp/Core/Settings/SettingsStore.swift:42-82` | UserDefaults key 集合与 `@Published` 偏好；无 AVDetector |
| `.../Core/Settings/SettingsViewModel.swift:28-41` | `hapticEnabled` 等偏好通过 `didSet` 写 Store 并回调；无 AVDetector |
| `.../Features/Settings/SettingsView.swift:266-288` | “General”分区当前只有 Haptic 一项 |
| `.../App/AppContainer.swift:435-446` | 构造 `LivaChatConfig`，在会话启动时创建 runtime |
| `.../App/AppContainer.swift:318-340` | `openSettings()` 构造 `SettingsViewModel`，已有 `onHapticChanged` 回调先例 |
| `apps/ios/LivaChatApp/Core/Conversation/ConversationModeModel.swift:313-330` | `handleRejection` 只在 `code == 1009` 且存在 `pendingRequest` 时回滚模式并清 `lastRequest` |
| `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatConfig.swift:5-92` | SDK 配置结构体；`languageProvider` 每次 `session.start` 求值 |
| `.../Internal/Session/SessionManager.swift:60-76` | `startSession` 使用 `configProvider()` |
| `.../Internal/Session/SessionManager.swift:101-120` | `sendSessionChange` 映射 `SessionMode -> ClientSessionChangeMode` |
| `.../Internal/Session/ProtocolMapping.swift:20-28` | `SessionMode` 四值映射；无 `config` |
| `.../LivaChatSDK.swift:121-130` | `configProvider` 组装 `ClientSessionStartConfig` |
| `.../LivaChatSDK.swift:374-396` | 公共 `sendSessionChange` |
| `apps/ios/LivaChatApp/Core/Conversation/ConversationProtocols.swift:5-18` | App 侧 `LivaChatSDKProviding` 协议；新增 SDK 方法会要求测试 Fake 同步实现 |

**结论**：iOS 的 `openSettings` 可以覆盖在活跃会话之上，`hapticEnabled` 的 `onHapticChanged` 是现成的
“设置即时生效”先例；AVDetector 可以沿用同款回调，但需要新增 SDK 方法并同步 App 侧协议/Fake。

---

## 3. 关键设计决策

### D1 开关分层与生效条件

引入两个概念：

- **部署级开关**：`cfg.avdetector.enabled`（现有）。为 `false` 时 `avdetector_service is None`，任何会话级
  设置都不能启用服务。
- **会话级开关**：`SessionContext.avdetector_enabled`（新增），默认 `True`，由 `session.start` 或
  `session.change(mode="config")` 设置。

有效运行条件：

```text
service_exists (部署级)
  AND ctx.avdetector_enabled (会话级)
  AND _pipeline_mode == "full" (chat 模式)
  AND monitor 已创建且未熔断
```

- 会话级 `enable` 只表示“允许在该会话运行 AVDetector”，不承诺服务可用。
- 非 chat 模式（translate/transcribe/omni）即使会话级为 `enable` 也不挂 monitor；切回 chat 时
  `_ensure_av_monitor()` 自动补挂。
- 会话级 `disable` 会立即 `_close_av_monitor()`，停止音频/视频喂入与后续检测；已开始的主动回合按普通
  回合语义处理（可被用户打断），不会因关闭开关而强行取消已说出口的回复。**关闭后到达、被
  `_on_av_event` 门控丢弃的事件零副作用**：不写 history/chat_store/recording、不设回滚句柄、
  不起 pipeline（见 5.4）。

### D2 `session.start` 字段

```json
{
  "type": "session.start",
  "seq": 1,
  "timestamp": 1712345678000,
  "payload": {
    "session_id": null,
    "config": {
      "enable_video": true,
      "audio_sample_rate": 24000,
      "vad_sensitivity": "medium",
      "client_version": "android-1.0.0.130",
      "timezone": "Asia/Shanghai",
      "language": "zh-Hans",
      "avdetector": "enable"
    }
  }
}
```

- 可选字段（不加入 `required`），保证老客户端兼容。
- 取值 `"enable"` / `"disable"`；**只有“字段缺省/未携带”按 `"enable"` 处理**。
- **显式 `null` 非法**：Schema 只允许字符串枚举，不写 `oneOf: [enum, null]`。JSON Schema 的
  `"default": "enable"` 只是注解，不代表服务器会自动补值；服务端必须自己实现缺省逻辑。
- 生成器可能把“非 required 属性”生成为 nullable DTO（`T | None = None`），因此 DTO 层可能接受
  `null`；服务端不得把显式 `null` 当成缺省：用 Pydantic `model_fields_set` 判断，
  `"avdetector" in cfg.model_fields_set and cfg.avdetector is None` → 按 malformed 处理
  （`error(1006, "invalid avdetector: null")`，不建立会话）；只有字段不在 `model_fields_set` 时才走
  `enable` 缺省。`session.change(config)` 的显式 null 则由业务校验返回 1011（见 D8）。
- 每次 `session.start`（包括同一连接重发、断线重连携带 resume_id）都以本次值为准；服务端**不跨连接
  持久化**会话级开关，App 的持久化设置负责在每次 `session.start` 重新携带。
- 字段名使用 `avdetector`，与配置段/服务目录一致。

### D3 `session.change(mode="config")` 语义

请求：

```json
{
  "type": "session.change",
  "seq": 42,
  "timestamp": 1712345680000,
  "payload": {
    "mode": "config",
    "avdetector": "disable"
  }
}
```

- `avdetector` 直接放在 `payload` 顶层（与 `my_language` / `local_language` 同级），不嵌套 `config`
  对象——与现有 `session.change` 字段风格一致，且 `additionalProperties: false` 下只需增加一个属性。
- `config` 是**控制伪模式**，不是 pipeline mode：
  - 不改变 `_pipeline_mode`；
  - 不关闭/重建 ASR/TTS/lecture/omni 引擎；
  - 不发送 `status(mode_changed)`；
  - 只修改 `ctx.avdetector_enabled`，并按需 `_ensure_av_monitor()` / `_close_av_monitor()`。
- `mode="config"` 时 `avdetector` 必填；缺失或非业务可接受值：
  - DTO 接受（optional nullable）→ orchestrator 返回
    **`error(SESSION_CONFIG_FAILED=1011, "invalid avdetector: ...")`**；
  - DTO/网关直接拒绝非法枚举/类型 → **1006**（malformed）；
  - `mode="config"` 在会话未建立时返回 **1011**（不是 1009）；以上均不发送 `session.changed` 回声。
  **1009 `SESSION_CHANGE_FAILED` 仅保留给真实 pipeline mode 切换被拒**，config 路径绝不能使用 1009。
- `mode="config"` 在其他模式（translate/omni/transcribe）下也接受：只更新会话状态，不影响当前引擎；
  之后切回 chat 时按新状态决定是否挂 monitor。
- 幂等：重复 enable/disable 不报错；已处于目标状态时 monitor 创建/关闭为空操作。

### D4 `session.changed` 回声

配置请求的回声与请求保持一致：

```json
{
  "type": "session.changed",
  "seq": 43,
  "timestamp": 1712345680100,
  "payload": {
    "mode": "config",
    "avdetector": "disable"
  }
}
```

- `mode="config"` 回声只表示“配置已应用”，**不表示 pipeline mode 变为 config**；服务端不改变
  `_pipeline_mode`，也不发送 `status(mode_changed)`。
- `avdetector` 回声为**本次生效的会话级值**（`"enable"` / `"disable"`），与请求值一致。
- **客户端 SDK 必须拦截该回声**，不能把 `mode="config"` 交给现有模式状态机：
  - Android `SessionManager.handleSessionChanged`：`payload.mode == CONFIG` → 记录日志
    （可选专用回调）后 `return`，不调用 `listenerDispatcher.onSessionChanged(...)`；
  - iOS `SessionManager`：`payload.mode == .config` → log 后跳过 `.sessionChanged` 事件；
    `ProtocolMapping.sessionMode` 改为返回 `SessionMode?`（`.config → nil`）或在调用前显式分支，
    避免把 config 映射成任何 pipeline mode；
  - App 的 `ConversationListenerAdapter` / `ConversationCoordinator` 模式状态机因此完全不受影响。
    额外原因：Android `onSessionChanged` 还会清空 `pendingModeChange`（`ConversationListenerAdapter`），
    config 回声不应触发该逻辑，否则可能打断一次真实的模式切换恢复。
- 普通模式切换的 `session.changed`（`chat` / `translate` / `transcribe` / `omni`）行为不变。
- **兼容性**：旧客户端不会发送 `session.change(mode="config")`，因此不会收到 `mode="config"` 回声；
  新客户端按上述逻辑处理。当前研发阶段无存量客户端，可直接采用该协议，不需要为旧解码器做额外兼容。
- 普通模式切换的 `session.changed` 不携带 `avdetector`；仅 config 回声携带，减少无意义字段。

### D5 SDK 公共 API 形态

不把 `"config"` 暴露到 App 的模式枚举：

- App 继续使用 `SessionMode { chat, translate, transcribe, omni }`（iOS）/
  `sendSessionChange(mode: String, ...)`（Android）。
- 新增：

```kotlin
// Android
LivaChatSDK.setAvdetectorEnabled(enabled: Boolean)
```

```swift
// iOS
LivaChatSDK.setAvdetectorEnabled(_ enabled: Bool)
```

SDK 内部行为：

1. 更新共享的线程安全状态盒（`AtomicBoolean` / 带锁 Bool；构造时取 `LivaChatConfig` 的初始值）。
   该状态盒必须在 `SessionManager` 构造前创建并同时传给 `SessionManager` 与 `configProvider`——否则
   Swift 无法在 `SessionManager` 自己的初始化表达式里捕获自身，Kotlin 也存在属性初始化顺序问题。
2. 每次 `session.start` 的 `configProvider()` 读取该状态盒，写入 `config.avdetector`。
3. 按会话阶段决定何时发送 `session.change { mode: "config", avdetector: ... }`：
   - **已收到 `session.ready`**（`sessionReady=true`）：立即发送；
   - **`session.start` 已发出但尚未收到 `session.ready`**：记录 `pendingAvdetectorChange`，
     在 `handleSessionReady` 时补发一次（否则本次 `session.start` 携带的是旧值，开关在本次会话
     不会生效）；
   - **尚未发送 `session.start`**：只更新状态盒；下一次 `session.start` 会读取最新值。
4. `handleSessionReady`：置 `sessionReady=true`；若 `pendingAvdetectorChange != null`，发送 config
   变更并清空 pending。`session.ended` / 连接断开 / `reset` 时置 `sessionReady=false`、清空 pending
   （最新值已在状态盒中，下一次 `session.start` 会携带）。

> **Android 关键点**：
> - 现有 `SessionManager.startSession()`（`SessionManager.kt:157-167`）只构造并发送
>   `ClientSessionStartPayload`，**不会写 `SessionManager.sessionId`**；`sessionId` 实际在
>   `handleSessionReady`（`:274-277`）才赋值为服务端下发的 id。因此 `sessionId != null` 只能近似表示
>   “已 ready”，无法表达“start 已发、ready 未到”的 pending 窗口，且与业务 id 耦合。仍应新增
>   `sessionReady` 标志（`handleSessionReady` 置 true）和 `sessionStartSent` 标志
>   （`startSession` 实际发出后置 true；连接断开 / `session.ended` / `reset` 置 false）。
>   `pendingAvdetectorChange` 只在 `sessionStartSent=true && sessionReady=false` 时记录。
> - Android 的 `SessionManager.reset()` 目前**不是连接断开时自动调用的**；`LivaChatSDK` 也没有把
>   `connectionState` 转给 SessionManager。必须新增桥接：在 `LivaChatSDK` 的
>   `connectionManager.connectionState` 观察中，当 state != CONNECTED 时调用
>   `sessionManager.onConnectionLost()`（置 `sessionReady=false`、`sessionStartSent=false`、清 pending）。
>   否则旧连接的 `sessionReady=true` 会残留，重连后可能在 `session.start` 之前发出 config 消息。
> - iOS 的 `sessionStartedOnConnection` 已由 `resetSessionGate(connectionEpoch:)` 在
>   `connectionManager.onStateChange`（state != connected）与 `onOpen` 中复位（`LivaChatSDK.swift:206-231`），
>   可直接复用；另需新增 `sessionReady`（在 `.sessionReady` 分支置 true）用于 pending 补发。

5. 入站处理 `session.changed(mode="config")`：**只记录日志/可选专用回调，不向 App 抛
   `onSessionChanged` / `.sessionChanged`**。App 的模式状态机只消费 chat/translate/transcribe/omni
   四种 pipeline mode；config 回声是控制确认，不属于模式事件。

这样 App 不需要关心“当前是否存在会话”，SDK 自己保证：
- 无会话 → 静默更新，下一次 start 生效；
- 有会话 → 立即通知服务端；
- 服务端回声 `mode="config"` → 不污染 App 的模式状态。

### D6 App 设置与持久化

- 标题（**产品已确认**）：**“允许 AI 主动搭话”**；
  英文：**“Enable Proactive AI Care”**。
  - 原需求文案“允许AI主动发起会话”容易被理解为“自动建立新会话”，而实际行为是**在当前会话中**
    根据声音/画面主动关心或搭话；已确认改用“允许 AI 主动搭话”。
  - 该变更是纯字符串变更，协议/字段/默认值不受影响。
  - 副标题建议：“AI 可以根据声音或画面主动关心你” / “Let the AI proactively respond to sounds or
    changes in view”。
- 默认：**开启**（与当前部署 `avdetector.enabled=true` 的行为一致）。
- 位置：设置页“通用设置 / General”分区，与“触觉反馈”并列。
- 改动的处理顺序：
  1. 写本地存储（Android DataStore / iOS UserDefaults）；
  2. 调用 SDK `setAvdetectorEnabled(...)`；
  3. 重启进程/下次建连时，`LivaChatConfig` 初始值从持久化值读取，`session.start` 自然携带。
- 不新增服务端用户级持久化；服务端只在当前会话内保存状态。

### D7 兼容性矩阵

| 客户端 | 服务端 | `session.start.config.avdetector` | `session.change(mode="config")` | 最终行为 |
|---|---|---|---|---|
| 旧 | 旧 | 不发送 | 不发送 | 与现状完全一致 |
| 旧 | 新 | 缺省 → `enable` | 不发送 | 与现状一致（服务端默认开启） |
| 新 | 旧 | 旧服务端 `config` 允许额外字段 → 接受但忽略 | 旧服务端枚举无 `config` → 解析期 **1006**（不是 1009） | SDK 在 config 单飞上下文中把 1006 识别为“不支持 config”：静默降级、不回滚模式、不再重发；本地设置保留 |
| 新 | 新 | 正常 | 正常；config 业务失败返回 **1011** | 完整功能 |

> 旧服务端拒绝 `config` 的具体位置：`services/gateway/codec.py` 的 `ClientMessageAdapter.validate_json`
> 对 `ClientSessionChangeMode` 做严格枚举校验；旧模型无 `CONFIG`，因此请求在协议层失败（1006）。
> **1006 是通用协议错误，不能直接当成“config 不支持”**；SDK 需要用“config 单飞、等待回声”的上下文
> 判定，详见 D8。
>
> 新客户端在 new/new 下会收到 `session.changed(mode="config")`；按 D4，SDK 拦截并只记录，不向 App
> 抛模式事件。旧客户端不会发送 config，因此不会收到该回声。

### D8 config 失败的错误码与客户端处理（与 1009 隔离）

**问题**：现有 Android/iOS 客户端把 `1009 SESSION_CHANGE_FAILED` 当成“真实模式切换被拒”的专属信号：

- Android `ConversationListenerAdapter.onError`（`ConversationListenerAdapter.kt:334-353`）：
  `1009` → 清 `pendingModeChange`、回滚 `translate/transcribe/omni`、清 `lastModeRequest`，
  并继续向 UI 发通用错误；
- iOS `ConversationModeModel.handleRejection`（`ConversationModeModel.swift:313-330`）：
  `1009` 且存在 `pendingRequest` → 回滚模式并清 `lastRequest`；
- iOS/Android 的 `onError` 通用路径还会弹网络/服务错误横幅。

若 config 失败复用 1009，一次 AVDetector 配置失败会在“另一次真实模式切换正在等待回声”时误回滚该切换。

| Code | 名称 | 唯一职责 | 客户端行为 |
|---|---|---|---|
| **1009** | `SESSION_CHANGE_FAILED` | 真实 pipeline mode 切换被拒 | 现有逻辑：清 `pendingModeChange`、回滚模式、清 `lastModeRequest` |
| **1011** | `SESSION_CONFIG_FAILED`（新增） | `session.change(mode="config")` 业务失败 | SDK 记录并吞掉；不回滚模式、不弹通用错误；本地设置保留 |

因此：

1. **服务端**：新增 `ErrorCode.SESSION_CONFIG_FAILED = 1011`（1010 已被内部
   `AVDETECTOR_UNAVAILABLE` 占用，且该值从不下发）。所有“由 config 请求产生、且不是协议解析错误”
   的失败都用 1011：
   - `mode="config"` 时缺少 `avdetector`；
   - `mode="config"` 时 `avdetector` 值不可接受；
   - `mode="config"` 但会话未建立；
   - 应用配置时的内部失败。
   `1009` 只允许用于 chat/translate/transcribe/omni 的真实模式切换被拒。
   协议解析层错误（例如 enum 值非法）仍是 1006，属于 malformed frame，不是业务拒绝。
   同理，`session.start.config.avdetector` 的显式 null 走 1006（建立会话前，无 config 业务通道）；
   `session.change(mode="config")` 的 null 走 1011（已建立会话后的配置失败）。

2. **客户端 SDK 必须消费 1011，不交给 App 的 1009 逻辑**：
   - `SessionManager` 维护 `configInFlight`（一次只允许一个 config 请求在途）与
     `queuedConfigValue`（最新一次用户意图，单飞期间合并）；**先置 `configInFlight=true` 再发送**，
     避免响应早于状态置位的理论竞态；
   - 收到 `session.changed(mode="config")`：清 `configInFlight`；若 `queuedConfigValue` 与刚确认值
     不同，再发一次；
   - 收到 `error(1011)` 且 `fatal=false`：清 `configInFlight` / queued，记录 warning，
     **不向 App 抛 `onError`**；本地设置已持久化，下一次 `session.start` 继续携带。不要回滚模式状态；
   - 收到 `error(1006)` 且 `configInFlight=true` 且 `fatal=false`：判定为“旧服务端不支持 config”，
     置本连接的 `sessionConfigUnsupported=true`，清 in-flight/queued，记录 warning 并**不向 App 抛
     `onError`**；本连接后续 `setAvdetectorEnabled` 只更新下一次 `session.start`，不再发 config。
     `fatal=true` 的任何错误仍按现有路径透传（连接将被终止）。
     旧服务端的 `decode_guard` 会因该帧计数 +1，但下一个合法帧会 `on_ok` 清零，且 SDK 不再重发，
     不会累积到 10 帧断连；
   - 连接断开 / `session.ended` / 新连接：清 `configInFlight`/queued；`sessionConfigUnsupported`
     按连接重置（旧服务端下一次连接仍只产生一次 1006）。

3. **App 侧不需要新增 1011 分支**：SDK 已吞掉 config 错误，`ConversationListenerAdapter` /
   `ConversationModeModel` 的 1009 逻辑保持原样，绝不被 config 失败触发。若后续产品需要提示
   “服务器不支持实时切换”，再新增专用 SDK 事件/回调（本期不做）。

4. **1006 归因的边界**：这是基于上下文的 best-effort 判定——SDK 发出的帧都经过编码器，正常不会产生
   1006；若恰好有另一条独立 malformed 帧在 config 在途时到达，可能被误判为“不支持 config”。研发期
   可接受；若未来要求零歧义，可另加 `session.ready` 能力位（例如 `capabilities:["session_config"]`），
   本期不扩协议。

> 关键不变量：**任何 config 请求产生的业务错误都不得使用 1009**；客户端看到 1009 时永远可以按
> “真实模式切换被拒”处理。

---

## 4. 协议 / Schema 设计

### 4.1 `common.json` 新增共享枚举

```json
"AvDetectorSwitch": {
  "type": "string",
  "enum": ["enable", "disable"],
  "description": "Per-session AVDetector switch. 'enable' = allow proactive audio/video detection; 'disable' = do not attach the detector for this session."
}
```

放在 `$defs` 中供三处 `$ref` 复用，避免生成多份同名枚举。

### 4.2 `c2s/session-start.json`

在 `payload.config.properties` 增加：

```json
"avdetector": {
  "$ref": "../common.json#/$defs/AvDetectorSwitch",
  "default": "enable",
  "description": "Optional per-session switch for AVDetector. Omitted = enable (backward compatible: old clients keep current behavior). Explicit null is invalid; the default keyword is annotation only and the server implements the omitted-field fallback."
}
```

不加入 `required`，也不写 `oneOf: [{"$ref": ...}, {"type": "null"}]`——显式 null 非法。生成器可能因
“optional” 生成 nullable DTO，服务端需按 D2 用 `model_fields_set` 区分“未携带”与“显式 null”。

### 4.3 `c2s/session-change.json`

- `mode` 枚举增加 `"config"`，并更新 description：

```json
"enum": ["chat", "translate", "transcribe", "omni", "config"]
```

描述明确：`"config"` 是控制请求，仅修改会话配置，不切换 pipeline mode；此时 `avdetector` 必填。

- `payload.properties` 增加：

```json
"avdetector": {
  "$ref": "../common.json#/$defs/AvDetectorSwitch",
  "description": "Required when mode=config. Per-session AVDetector switch to apply; ignored for other modes. Explicit null is invalid; the server rejects it as SESSION_CONFIG_FAILED (1011)."
}
```

- 不写 `if/then` 条件 Schema：仓库生成器对复杂条件支持有限，且现有 `omni`/`translate` 的条件必填也是
  运行时校验（见 `services/session/orchestrator.py:785-851` 的兜底默认值）。保持生成器可处理、服务端运行时校验。
- 显式 `null` 在 Schema 层非法；生成的 optional DTO 若接受 null，`_handle_session_change` 的
  `value is None` 分支返回 **1011**（不是 1009）；若生成器把该字段生成为非 nullable 导致网关直接
  解析失败，则为 **1006**。两种结果都不得是 1009。

### 4.4 `s2c/session-changed.json`

- `mode` 枚举增加 `"config"`（与请求保持一致）：

```json
"enum": ["chat", "translate", "transcribe", "omni", "config"]
```

  描述明确：普通取值是“切换后生效的 pipeline mode”；`"config"` 是**配置确认回声**，
  不表示 pipeline mode 变化，客户端不得据此更新模式状态机。

- `payload.properties` 增加可选：

```json
"avdetector": {
  "$ref": "../common.json#/$defs/AvDetectorSwitch",
  "description": "Echo of the per-session AVDetector switch. Present only in responses to session.change(mode=config)."
}
```

### 4.5 生成与文档

修改 Schema 后按仓库约定重新生成：

```bash
python3 scripts/generate_python_api.py \
  --schema packages/api-schema \
  --out packages/sdk-python/src/livachat_protocol/generated

make generate-android-api
make generate-swift-api
```

同时更新：
- `docs/ws-protocol-design.md`：版本 v1.2 → v1.3；在 `session.start` / `session.change` / `session.changed`
  三节增加 `avdetector` 字段；`session.change` 与 `session.changed` 的 `mode` 枚举增加 `"config"`，
  并说明 config 回声不代表 pipeline mode 变化；§4.3 错误码表增加
  **1011 `SESSION_CONFIG_FAILED`**（与 1009 的职责边界）；变更摘要注明“AVDetector 会话级开关”。
- `packages/api-schema/README.md`：文件头 “Synchronized with `docs/ws-protocol-design.md` v1.2”
  同步改为 **v1.3**；同步结构说明中的新旧字段。
- `packages/api-schema/common.json`：文件头 `description` 的 “Synchronized with ws-protocol-design.md
  v1.2” 同步改为 **v1.3**（该行是版本声明，不是 `$defs` 的一部分，容易漏改）。
- `docs/fd_pipeline_avdetector.md`：在 §4 D7 增加“v1 协议零变更；会话级开关增强见本文”的注记，避免旧结论
  与新实现冲突。

---

## 5. 服务端设计

### 5.1 会话状态

`services/session/context.py` 的 `SessionContext` 新增：

```python
# AVDetector 会话级开关（session.start.config.avdetector；缺省 enable）
avdetector_enabled: bool = True
```

`services/session/manager.py:70-90` 的 `SessionManager.start(...)` 新增关键字参数：

```python
avdetector_enabled: bool = True,
```

并在 `ctx = SessionContext(...)`（`:141-153`）传入。默认值保证现有调用/测试不破坏。

`services/session/orchestrator.py:546-559` 解析（区分“未携带”与“显式 null”）：

```python
if "avdetector" in cfg.model_fields_set:
    raw = cfg.avdetector
    if raw is None:
        # Schema 不允许显式 null；DTO 因 optional 可能接受，这里按 malformed 拒绝
        frame = self._codec.make_error(
            code=ErrorCode.MALFORMED_JSON,
            message="invalid avdetector: null",
            fatal=False,
        )
        await self._handler.enqueue_outbound(frame)
        return
    avdetector_enabled = str(raw) != "disable"
else:
    avdetector_enabled = True   # 只有字段未携带才走 enable 缺省

ctx, history = await self._session_mgr.start(
    ...,
    avdetector_enabled=avdetector_enabled,
)
```

> `cfg.model_fields_set` 是 Pydantic v2 用于区分“字段未提供”与“显式赋值（含 null）”的集合；
> 不能只依赖 `getattr(cfg, "avdetector", None)`，否则显式 null 会被误当成缺省 enable。
> JSON Schema 的 `"default": "enable"` 只是文档注解，不会自动填充。
>
> **实现顺序要求**：把上述 config 校验移到 `_handle_session_start` 开头的 `_close_av_monitor()` /
> `_cancel_pipeline()` 之前（或在 malformed 路径提前返回、不拆旧会话）。否则一个显式 null 的
> `session.start` 会先拆掉旧会话、再报 1006，客户端既丢旧会话又建不起新会话。

> 生成后的 Python 枚举是 `StrEnum`，`str(...)` 返回其 wire value；实现时也可用
> `raw == AvDetectorSwitch.DISABLE`。**只有字段不在 `model_fields_set`（真正未携带）才走 `enable`**；
> 显式 null 不是缺省，按 malformed 处理。

复位语义：
- `_handle_session_start` 每次都会用本帧的值覆盖；
- `session.end` / 连接断开后 `SessionContext` 生命周期结束，无需额外清理；
- 同一连接重发 `session.start` 时按新帧重新设置（缺省即 enable），不继承上一会话。

### 5.2 `_ensure_av_monitor` 增加会话门

`services/session/orchestrator.py:977-998`：

```python
def _ensure_av_monitor(self) -> None:
    if (self._av_monitor is not None
            or self._avdetector_service is None
            or self._ctx is None
            or self._pipeline_mode != "full"          # 新增：仅 chat
            or not self._ctx.avdetector_enabled):     # 新增：仅会话级 enable
        return
    ...
```

这样：
- 新会话 `disable` → 不创建 monitor；
- 新 `session.change(config, enable)` 在 translate/omni/transcribe 下调用 `_ensure_av_monitor()` 也不会误挂载；
- 之后切回 chat → 只有会话级 enable 才创建。

> **这是关键修复**：配置分支会直接调用 `_ensure_av_monitor()`；若该 helper 不检查 `_pipeline_mode == "full"`，
> 在 translate/omni 下 enable 会创建 monitor 并开始喂音频/视频，与“仅 chat 模式支持 AVDetector”的
> 既有约束冲突。加此门后，配置分支的调用可保持简单、幂等。

### 5.3 `_handle_session_change` 的 `config` 分支

在 `services/session/orchestrator.py:733-869` 中：

0. **调整开头的“会话未建立”分支**：现有代码在读取 `new_mode` 之前就固定返回 1009
   （`:735-740`）。必须先取 `new_mode = msg.payload.mode`，再按请求类型选码：

```python
new_mode = msg.payload.mode
if not self._ctx:
    code = (ErrorCode.SESSION_CONFIG_FAILED
            if str(new_mode) == "config"
            else ErrorCode.SESSION_CHANGE_FAILED)
    frame = self._codec.make_error(
        code=code, message="session not started", fatal=False,
    )
    await self._handler.enqueue_outbound(frame)
    return
```

1. 合法模式白名单增加 `"config"`：

```python
if new_mode not in ("chat", "translate", "transcribe", "omni", "config"):
    ...
```

2. 在 `had_monitor` / “非 chat 关闭 monitor”之前插入 `config` 分支并 `return`，
   避免配置请求误触发模式切换的 cut-out/enter 逻辑：

```python
if new_mode == "config":
    value = getattr(msg.payload, "avdetector", None)
    if value is None or str(value) not in ("enable", "disable"):
        frame = self._codec.make_error(
            code=ErrorCode.SESSION_CONFIG_FAILED,   # 1011，绝不用 1009
            message=f"invalid avdetector: {value}",
            fatal=False,
        )
        await self._handler.enqueue_outbound(frame)
        return

    previous = self._ctx.avdetector_enabled
    try:
        self._ctx.avdetector_enabled = (str(value) == "enable")
        if self._ctx.avdetector_enabled:
            self._ensure_av_monitor()   # 内部有 _pipeline_mode=="full" 门；非 chat 下 no-op
        else:
            await self._close_av_monitor()
    except Exception:
        # 清理可能已半创建的 monitor，避免 ctx 已回滚为 disable 但 monitor 仍活着
        await self._close_av_monitor()
        self._ctx.avdetector_enabled = previous   # 回滚会话状态，避免半应用
        logger.warning("apply avdetector config failed", exc_info=True,
                       extra={"session_id": self._ctx.session_id})
        frame = self._codec.make_error(
            code=ErrorCode.SESSION_CONFIG_FAILED,   # 1011
            message="apply avdetector config failed",
            fatal=False,
        )
        await self._handler.enqueue_outbound(frame)
        return

    # 回声与请求保持一致：mode="config"
    frame = self._codec.make_session_changed(
        mode="config",
        avdetector=str(value),
    )
    await self._handler.enqueue_outbound(frame)
    return
```

注意：
- **不变量**：config 分支的所有业务失败（含“会话未建立”）都回 1011，**任何路径都不得回 1009**；
  1009 只属于真实模式切换被拒。否则 Android/iOS 会把 config 失败当成模式切换拒绝并误回滚。
- `config` 分支必须在 `had_monitor = ...` 与 `if new_mode != "chat": await self._close_av_monitor()`
  之前，否则 disable 以外的 config 请求也会误关 monitor。
- `mode="config"` 回声不携带语义化的 `my_language` / `local_language` / `usecase`（本次变更与
  translate 语义无关）。实现中 `make_session_changed` 只在可选字段非 `None` 时加入 kwargs，
  避免 `exclude_unset=True` 把显式 `None` 序列化成 schema 不允许的 `null`；因此 config 回声
  实际只包含 `mode` + `avdetector`。
- 不调用 `_send_status(...)`。
- `mode="config"` 不进入 `_enter_omni_mode` / `_enter_lecture_mode`。
- 若全局 `avdetector_service is None`，`enable` 仍记录会话状态并回声 `enable`，但 `_ensure_av_monitor`
  因 service 为空 no-op；不返回错误（部署级关闭是运维决策，不应让客户端设置失败）。

### 5.4 `_on_av_event` 增加开关兜底

`services/session/orchestrator.py:1080`：

```python
if (self._ctx is None
        or self._pipeline_mode != "full"
        or not self._ctx.avdetector_enabled):
    ... drop + log ...
    return
```

并在 `_do_interrupt` / `_cancel_pipeline` 等 await 之后、**“事件落三件一体”之前**再检查一次
（**两个条件都要查**）：

```python
if (self._ctx is None
        or self._pipeline_mode != "full"
        or not self._ctx.avdetector_enabled):
    logger.info("avdetector event dropped (mode/session switch changed) ...")
    return
```

**检查点必须在 history / chat_store / recording 写入之前，而不是只在 `_spawn_pipeline` 之前**：
`_on_av_event` 当前顺序是先门控 → 写 `history.add_message` / `chat_store.on_message` /
`recording_sink.on_chat_text` / `_turn_user_record`（`orchestrator.py:1162-1199`）→ 最后
`_spawn_pipeline`（`:1207`）。若复检放在持久化之后、spawn 之前，事件虽然不起回合，但仍会留下
user 行/聊天记录/录制文本——这不符合“被丢弃事件零副作用”。

因此：**所有丢弃分支（入口门控、竞态复检、视觉抑制窗、冷却、config disable/模式切换后的复检）都必须
在第一个持久化写之前 return**。复检之后到 `_spawn_pipeline` 之间没有 await——单线程事件循环下
config 变更无法插入，所以“通过复检 → 落三件一体 → spawn”是原子的，不会出现“已落库但没起轮”或
“起了轮但没落库”的中间态。
被丢弃 = 以下全部不发生：
- 不写 `history.add_message("user", ...)`；
- 不写 `chat_store.on_message(kind="asr", role="user", ...)`；
- 不写 `recording_sink.on_chat_text(...)`（录制侧无该事件的聊天文本记录；既有音视频媒体录制链路不受影响）；
- 不设置 `_turn_user_record`（无 4xx 回滚句柄）；
- 不 `_spawn_pipeline`（无 LLM/TTS 主动回合）。

> 例外：danger 事件在 await `_do_interrupt` 期间已执行的“抢占/中断用户回合”不会因随后的复检丢弃而
> 回滚——这里说的零副作用仅指**本 avdetector 事件**不再写 history/chat_store/recording 或起轮，
> 不包含已经发生的抢占动作。

目的：`session.change(config)` 的 `_close_av_monitor()`、或 `session.change(omni/translate)` 的
模式切换，可能在 `_on_av_event` 的 await 窗口内由 dispatch 循环完成；只查开关会漏掉“切到非 chat 模式”
的竞态。重新检查可以保证“关闭/切模式后不再起新的 chat 主动回合”，**且不留下任何落库/录制残影**。
关闭前已经在播报的回合不强制取消。

**monitor generation（防 disable→enable 后的旧事件复活）**：仅靠 `ctx.avdetector_enabled` 无法区分
“旧 monitor 的迟到事件”和“新 monitor 的事件”——disable 后立即 enable 时 flag 又为 true，旧事件可能
穿过复检。建议 `_ensure_av_monitor` 每次创建时分配自增 `monitor_generation`，回调包装器在调用
`_on_av_event` 前校验 generation 仍等于当前值；`_close_av_monitor` **先置空 `_av_monitor` 并递增
generation，再 await `monitor.close()`**（与现有先置空顺序一致）。
**generation 必须随事件传入 `_on_av_event`（新增参数或闭包捕获），并在每个 await 之后再次校验**：
只在入口校验无法覆盖 `_do_interrupt` / `_cancel_pipeline` 期间发生的 disable→enable；否则旧事件在
await 返回后仍可能起轮。这样旧流的所有迟到结果都会被丢弃，且与 5.6 的关闭顺序纪律兼容。

### 5.5 喂入点

现有喂入点已经以 `self._av_monitor is not None` 为门：

- `_handle_audio`（`:1420-1422`）
- `_handle_video`（`:2328-2347`）
- `_arm_av_visual_suppression`（`:1027-1035`）

`disable` 后 `_av_monitor` 为 `None`，这些路径全部短路，无需逐点再加会话开关判断。**只改
`_ensure_av_monitor` 与 `_on_av_event` 两处**即可覆盖“不新建 monitor / 不起新主动回合”。

### 5.6 生命周期与模式切换

| 事件 | 行为 |
|---|---|
| `session.start`（新会话/重连/同连接重发） | 按本帧 `config.avdetector` 设置 `ctx.avdetector_enabled`；随后 `_ensure_av_monitor()`（chat 模式） |
| `session.change(chat)` | 已有逻辑 `_ensure_av_monitor()`；新增会话开关判断 |
| `session.change(translate/transcribe/omni)` | 已有逻辑 `_close_av_monitor()`；开关状态保留在 ctx，供以后切回 chat |
| `session.change(config, disable)` | 立即 `_close_av_monitor()`，状态保留 `False` |
| `session.change(config, enable)` | chat 下 `_ensure_av_monitor()`；非 chat 下只记录状态，不挂载 |
| `session.end` / `run()` 收尾 | 已有 `_close_av_monitor()`；状态随 ctx 丢弃 |
| 全局配置关闭 | `avdetector_service is None`；所有 enable 仅记录状态、无 monitor；无错误 |

### 5.7 `make_session_changed` 与生成模型

`services/gateway/codec.py:125-137` 增加可选参数：

```python
def make_session_changed(
    self, *, mode: str, my_language: str | None = None,
    local_language: str | None = None, usecase: str | None = None,
    avdetector: str | None = None,
) -> str:
    kwargs: dict[str, object] = {"mode": mode}
    # 可选字段无值时省略：schema 定义为 string，显式 None 会序列化成 null。
    if my_language is not None:
        kwargs["my_language"] = my_language
    if local_language is not None:
        kwargs["local_language"] = local_language
    if usecase is not None:
        kwargs["usecase"] = usecase
    # 仅在 config 回声携带；普通模式切换不出现该字段
    if avdetector is not None:
        kwargs["avdetector"] = avdetector
    return self.build_typed(
        ServerSessionChangedPayload(**kwargs),
        msg_cls=ServerSessionChanged,
    )
```

只有 `config` 分支传 `avdetector`；普通模式切换走 `None` 分支，不把 `avdetector` 放进 kwargs——
`build_typed` 使用 `exclude_unset=True`，显式传 `None` 仍会序列化成 `null`，因此不能简单写成
`avdetector=avdetector`。
需先重新生成 Python DTO，否则 `ServerSessionChangedPayload` 没有该字段。

### 5.8 错误与日志

- `config` 缺少/非法 `avdetector` → **`error(1011 SESSION_CONFIG_FAILED, "invalid avdetector: ...")`**，
  不回声、不改状态。
- `mode="config"` 但会话未建立 → **`error(1011, "session not started")`**（不是 1009）。
- config 应用内部异常 → **`error(1011, "apply avdetector config failed")`**。
- **1009 `SESSION_CHANGE_FAILED` 只用于真实模式切换被拒**；config 路径不得使用。
- 会话级 enable 但部署级关闭 → 正常回声 `enable` + INFO 日志说明 service 未创建。
- 每次 config 变更记录 INFO：`session_id`、`enabled`、当前 `pipeline_mode`、monitor 是否存在。
- `ErrorCode` 新增 `SESSION_CONFIG_FAILED = 1011`（1010 已被内部 `AVDETECTOR_UNAVAILABLE` 占用；
  1010 不下发）。同步更新 `docs/ws-protocol-design.md` 的错误码表；`packages/api-schema/s2c/error.json`
  的 `code` 是 UInt16，无需枚举改动。

---

## 6. Android 设计

### 6.1 数据层

`UserPreferences` 增加：

```kotlin
/** 允许 AI 主动搭话（AVDetector 会话级开关；对齐 iOS settings.avdetector_enabled）。 */
val avdetectorEnabled: Boolean = true,
```

`PreferencesDataSource`：
- key：`booleanPreferencesKey("avdetector_enabled")`
- `preferences` 映射：`prefs[KEY_AVDETECTOR_ENABLED] ?: true`
- setter：`suspend fun updateAvdetector(enabled: Boolean)`（与 `updateHaptic` 同款）

`SettingsRepository` / `SettingsUseCase` 增加透传 setter。
另外给 `PreferencesDataSource` 增加一个只读 Flow（或复用 `preferences`）：

```kotlin
val avdetectorEnabled: Flow<Boolean> = dataStore.data.map { it[KEY_AVDETECTOR_ENABLED] ?: true }
```

供 `AppModule` 启动时同步读取（与 `tlsAllowSelfSigned` 同款）。

### 6.2 设置页

`SettingsUiState` 增加 `avdetectorEnabled: Boolean = true`。

`SettingsViewModel`：
- `observePreferences()` 收集时写入 `avdetectorEnabled`；
- 新增：

```kotlin
fun setAvdetectorEnabled(enabled: Boolean) {
    viewModelScope.launch {
        settingsUseCase.setAvdetectorEnabled(enabled)
        conversationCoordinator.setAvdetectorEnabled(enabled)
    }
}
```

> 依赖选择：`ConversationCoordinator` 已是 `@Singleton`；设置页可以在会话进行中打开（Android 的
> `NavGraph` 只是 push 设置路由，`ConversationViewModel` 的 `OnScreenStop` 当前无发送点，会话不会因进入
> 设置页而停止）。让 ViewModel 直接调用 coordinator 是最短链路。若不想让 settings 层依赖会话层，也可在
> `ConversationCoordinator` 内订阅 `SettingsUseCase.observePreferences()` 的 `avdetectorEnabled`
> （`distinctUntilChanged().drop(1)`，只在 `sessionActive` 时发送）——两种方案二选一，建议前者以对齐 iOS
> 的 `onHapticChanged` 回调先例。
>
> 持久化纪律：用户切完开关可能立即返回；`viewModelScope` 被取消会中断 DataStore 写入。建议仿照
> `persistServerConfig` 用 `withContext(NonCancellable)` 包住“写盘 + 调 SDK”，或把写盘放到应用级 scope。

`SettingsScreen` 在 `settings_section_general` 的 `SwitchSetting` 列表中加入：

```kotlin
SwitchSetting(
    title = stringResource(R.string.settings_ai_proactive),
    subtitle = stringResource(R.string.settings_ai_proactive_desc),
    checked = uiState.avdetectorEnabled,
    onCheckedChange = viewModel::setAvdetectorEnabled,
    testTag = "settings_ai_proactive",
)
```

文案（所有 locale 按现有 i18n 流程补全）：
- zh-Hans：`允许 AI 主动搭话`
- en：`Enable Proactive AI Care`
- desc：`AI 可以根据声音或画面主动关心你` / `Let the AI proactively respond to sounds or changes in view`
- 协议/字段/默认值不受文案影响。

### 6.3 SDK 配置与状态

`LivaChatConfig` 增加：

```kotlin
/** 初始 AVDetector 会话级开关；App 从持久化设置读取。默认 true（与服务器默认一致）。 */
val avdetectorEnabled: Boolean = true,
```

`LivaChatSDK`：
- 在 `sessionManager` 构造前创建 `private val avdetectorEnabled = AtomicBoolean(config.avdetectorEnabled)`，
  同时传给 `SessionManager` 和 `configProvider`；
- 现有 `connectionState.collect`（`LivaChatSDK.kt:214-218`）增加：`state != CONNECTED` 时调用
  `sessionManager.onConnectionLost()`，保证旧连接的 `sessionReady=true` 不会残留；
- `configProvider`（`:138-152`）在每次 `session.start` 读取该 `AtomicBoolean`，写入生成模型：

```kotlin
ClientSessionStartConfig(
    ...,
    avdetector = if (enabled) AvDetectorSwitch.ENABLE else AvDetectorSwitch.DISABLE,
)
```

- 新增公共方法：

```kotlin
fun setAvdetectorEnabled(enabled: Boolean) {
    sessionManager.setAvdetectorEnabled(enabled)
}
```

`SessionManager`：
- 新增共享状态（构造参数）：`avdetectorEnabled`、`sessionStartSent=false`、`sessionReady=false`、
  `pendingAvdetectorChange: Boolean? = null`、`configInFlight=false`、
  `queuedConfigValue: Boolean? = null`、`sessionConfigUnsupported=false`；
- `startSession` 成功发出 `session.start` 后置 `sessionStartSent=true`；
- `handleSessionReady`：置 `sessionReady=true`；若 `pendingAvdetectorChange != null`，发送一次
  config change（置 `configInFlight=true`）并清空 pending；
- `setAvdetectorEnabled(enabled)`（按顺序短路，**每个分支处理完必须 return**）：
  - 更新共享状态；
  - 若 `sessionConfigUnsupported == true`：只更新状态，**return**（不发 config；旧服务端，见 D8）；
  - 若 `configInFlight == true`：`queuedConfigValue=enabled`（单飞合并，最新值覆盖），**return**；
  - 若 `sessionReady == true`（由 `handleSessionReady` 置位；**建议用显式标志，不用
    `sessionId != null`**——`sessionId` 虽在 ready 时才赋值，但它与业务 id 耦合且不能表达
    start-sent/pre-ready），构造
    `ClientSessionChangePayload(mode = ClientSessionChangeMode.CONFIG, avdetector = ...)` 并走既有
    `encodeAndSend` 发送，置 `configInFlight=true`、清 `queuedConfigValue`，**return**；
  - 否则若 `sessionStartSent == true`（start 已发出、ready 未到）：记录
    `pendingAvdetectorChange=enabled`，等 `handleSessionReady` 补发，**return**；
  - 否则（尚未 start）：只更新共享状态，下一次 start 读取；**return**。
- `handleSessionChanged` 的 config 回声：清 `configInFlight`；若 `queuedConfigValue != null` 且与刚确认值
  不同，再发一次（置 `configInFlight=true`、清 queued）；否则清空 queued；
- `handleError`（`SessionManager.kt` 现有 error 分支）新增拦截：
  - `code == SESSION_CONFIG_FAILED(1011) && !fatal`：清 `configInFlight`/queued，记录 warning，
    **不调用 `listenerDispatcher.onError`**（config 失败不弹通用网络错误；本地设置已保存）；
  - `code == 1006 && configInFlight && !fatal`：判定旧服务端不支持 config，置
    `sessionConfigUnsupported=true`，清 in-flight/queued，记录 warning，
    **不调用 `listenerDispatcher.onError`**；
  - 其余错误保持现有行为；
- `handleSessionEnded` / `onConnectionLost`（新增，由 `LivaChatSDK` 的 connectionState 观察调用）/
  `reset`：`sessionReady=false`、`sessionStartSent=false`、`pendingAvdetectorChange=null`、
  `configInFlight=false`、`queuedConfigValue=null`、`sessionConfigUnsupported=false`。
- 并发：`sessionStartSent` / `sessionReady` / `pendingAvdetectorChange` 会被 WS reader 线程
  （`handleSessionReady` / `handleSessionEnded`）与 App 主线程（`setAvdetectorEnabled`）同时访问。
  **必须在同一把锁（可复用 `sendLock`）内完成“读 ready/startSent → 决定发 config 或写 pending”**
  以及 `handleSessionReady` 里的“置 ready → 取 pending”；仅用 `@Volatile` 仍可能出现
  “toggle 读到 ready=false 后、ready 处理完毕才写入 pending”的丢更新窗口。
- `sendSessionChange` 的 `when` 不需要增加用户可见的 `"config"` 字符串分支；配置变更走独立方法，
  避免 App 误用。
- `handleSessionChanged`（`SessionManager.kt:327-334`）增加 early return：
  `msg.payload.mode == ServerSessionChangedMode.CONFIG` → 记录 `avdetector` 后 return，
  **不调用** `listenerDispatcher.onSessionChanged(...)`；普通模式仍原样分发。
- 日志：记录 `session.start config | avdetector=...`、config change 发送、config 回声接收、
  1011/1006 降级。
- **App 侧零改动**：`ConversationListenerAdapter.onError` 的 1009 分支保持原样；config 错误在 SDK
  层已被拦截，不会触发 `pendingModeChange` 清理或 translate/transcribe/omni 回滚。

### 6.4 设置即时生效

- `AppModule.provideLivaChatConfig`：通过 `runBlocking { preferences.avdetectorEnabled.first() }`
  读取初始值（与 `serverConfig` / `allowSelfSigned` 同款进程级读取）。
- `SettingsViewModel.setAvdetectorEnabled` 在落盘后调用 `conversationCoordinator.setAvdetectorEnabled`；
  coordinator 转发到 `sdk.setAvdetectorEnabled`（无论当前是否 active，SDK 自己决定是否发 config 消息）。
- 下次 `LivaChatConfig` 重建（进程重启）时读取最新持久化值。

### 6.5 测试

- `PreferencesDataSourceTest` / `SettingsRepositoryTest` / `SettingsUseCaseTest`：默认 true、写入/读取往返。
- `SettingsViewModelTest`：默认值、`setAvdetectorEnabled` 调用 use case + coordinator。
- `SettingsE2ETest`：设置页存在 `settings_ai_proactive`，切换后持久化。
- SDK 测试：`LivaChatConfig` 默认值；`SessionManager` 未 ready 时只更新、ready 后发 `session.change`
  且 payload 为 `mode=config`；`configProvider` 在每次 start 读取最新值；收到
  `session.changed(mode=config)` 不触发 `onSessionChanged`；收到 `error(1011)` 不触发 `onError`、
  不清 pending、不回滚模式；config 在途收到 `error(1006)` → `sessionConfigUnsupported=true`、
  不重发、不触发 `onError`。

---

## 7. iOS 设计

### 7.1 设置存储与 ViewModel

`SettingsStore`：
- Keys 增加 `static let avdetectorEnabled = "settings.avdetector_enabled"`；
- `@Published var avdetectorEnabled: Bool { didSet { defaults.set(...) } }`；
- init：`defaults.object(forKey:) as? Bool ?? true`。

`SettingsViewModel`：
- `@Published var avdetectorEnabled: Bool { didSet { store.avdetectorEnabled = avdetectorEnabled; onAvdetectorChanged(avdetectorEnabled) } }`；
- init 增加 `onAvdetectorChanged: (Bool) -> Void = { _ in }`，初始化时读取 `store.avdetectorEnabled`。

`SettingsView` 的 `generalSection`（`Features/Settings/SettingsView.swift:266-288`）加入与 Haptic 同款的
`HStack` + `Toggle`，`accessibilityIdentifier("settings_ai_proactive")`；文案 key：
`settings_ai_proactive` / `settings_ai_proactive_desc`，写入 `Resources/Localizable.xcstrings` 的所有 locale。

### 7.2 SDK

`LivaChatConfig`：
```swift
/// Initial per-session AVDetector switch; App reads it from persisted settings.
public var avdetectorEnabled: Bool = true
```
初始化参数增加默认值 `avdetectorEnabled: Bool = true`。

`LivaChatSDK`：
- 在 `sessionManager` 构造前创建共享状态盒（例如 `final class AvdetectorState: @unchecked Sendable`
  + `NSLock` 包一个 `Bool`，初始值 `config.avdetectorEnabled`），同时传给 `SessionManager` 与
  `configProvider`；`configProvider`（`LivaChatSDK.swift:121-130`）调用
  `ProtocolMapping.startConfig(... avdetector: ...)` 时读取该状态盒；
- 新增：
```swift
public func setAvdetectorEnabled(_ enabled: Bool) {
    SDKQueue.session.async { [sessionManager] in
        sessionManager.setAvdetectorEnabled(enabled)
    }
}
```

`SessionManager`：
- 持有共享状态盒（构造参数传入；读写走 `SDKQueue.session`，与现有 SessionManager 串行队列纪律一致）；
- 新增 `sessionReady=false`、`pendingAvdetectorChange: Bool? = nil`、`configInFlight=false`、
  `queuedConfigValue: Bool? = nil`、`sessionConfigUnsupported=false`；
- `setAvdetectorEnabled`（按顺序短路，**每个分支处理完必须 return**）：
  - 更新状态盒；
  - `sessionConfigUnsupported == true`：只更新状态，**return**（不发 config；旧服务端，见 D8）；
  - `configInFlight == true`：`queuedConfigValue=enabled`（单飞合并），**return**；
  - `sessionReady == true`：构造
    `ClientSessionChangePayload(mode: .config, avdetector: enabled ? .enable : .disable)` 并发送，
    置 `configInFlight=true`、清 queued，**return**；
  - 否则若 `sessionStartedOnConnection == true`（start 已发出、ready 未到）：记录
    `pendingAvdetectorChange=enabled`，等 `.sessionReady` 补发，**return**；
  - 否则只更新状态盒；**return**。
- `startSession` 的 `configProvider` 读取该值；
- `.sessionReady` 分支：置 `sessionReady=true`；若 `pendingAvdetectorChange != nil`，发送 config
  （置 `configInFlight=true`）并清空；
- `.sessionChanged` 的 config 分支：清 `configInFlight`；若 `queuedConfigValue != nil` 且与刚确认值
  不同，再发一次（置 `configInFlight=true`、清 queued）；否则清空 queued；
- `.error` 分支新增拦截：
  - `code == 1011 && !fatal`：清 in-flight/queued，记录 warning，**不 emit `.error`**（不弹通用网络错误）；
  - `code == 1006 && configInFlight && !fatal`：置 `sessionConfigUnsupported=true`，清 in-flight/queued，
    记录 warning，**不 emit `.error`**；
  - 其余错误保持现有 emit/致命处理；
- `resetSessionGate`（现有，连接断开/新连接时调用）与 `.sessionEnded`：置
  `sessionReady=false`、清空 pending/in-flight/queued、`sessionConfigUnsupported=false`
  （最新值仍在状态盒，下一次 start 携带）；
- `handleInbound` 的 `.sessionChanged` 分支增加 config 拦截：`payload.mode == .config` →
  记录 `avdetector` 后 `break`，**不 emit** `.sessionChanged`；普通模式仍走 `ProtocolMapping.sessionMode`。

`ProtocolMapping`：
- `startConfig` 增加 `avdetector: AvDetectorSwitch?` 参数；
- 新增 `static func avdetectorSwitch(_ enabled: Bool) -> AvDetectorSwitch`；
- `mode(_ value: SessionMode)` 不增加 config（App 层 `SessionMode` 保持四值）；
- `sessionMode(_ value: ServerSessionChangedMode)` 改为返回 `SessionMode?`（`.config → nil`），
  调用方仅在非 nil 时 emit；或在 `SessionManager` 中先判 `.config` 再调用原映射。

`ConversationProtocols.swift` 的 `LivaChatSDKProviding` 增加 `func setAvdetectorEnabled(_ enabled: Bool)`；
`ConversationRuntime`/`ConversationCoordinator` 转发；测试中的 `FakeSDK` 记录调用。

**App 侧零改动**：`ConversationModeModel.handleRejection` 的 1009 逻辑保持原样；config 的 1011/1006
在 SDK 层已被拦截，不会进入 `.error` 事件，也不会回滚 translate/transcribe/omni 或清 `lastRequest`。

### 7.3 设置即时生效

`AppContainer.openSettings()`（`:318-340`）构造 `SettingsViewModel` 时增加：

```swift
onAvdetectorChanged: { [weak self] enabled in
    self?.runtime?.sdk.setAvdetectorEnabled(enabled)
}
```

`AppContainer` 构造 `LivaChatConfig`（`:435-446`）时传入
`avdetectorEnabled: settingsStore.avdetectorEnabled`。
`AppLaunchOptions.ServerOverride.livaChatConfig`（UI 测试覆盖）同样传入默认值或可配置值。

> 线程注意：`SettingsStore` 是 `@MainActor`；`LivaChatConfig` 的 provider 若直接捕获 store 会触发
> Swift 并发检查。采用“构造时传入初始值 + 运行时通过 `setAvdetectorEnabled` 更新 SDK 内部状态”的方式，
> 避免在非 MainActor 的 `session.start` 路径读取 `@MainActor` 属性。

### 7.4 测试

- `SettingsStoreTests`：默认 true；写入后重新构造 `SettingsStore` 仍为 false/true。
- `SettingsViewModelTests`：初始化读取 store；setter 写 store 并触发 `onAvdetectorChanged`。
- `AppContainerTests` / `ConversationCoordinatorTests`：FakeSDK 记录 `setAvdetectorEnabled` 调用。
- SDK 测试：`configProvider` 携带正确 wire 值；`setAvdetectorEnabled` 在 `sessionStartedOnConnection`
  为 false/true 时的行为；`session.changed(mode=config)` 不 emit `.sessionChanged`；`ProtocolMapping` 映射
  （`.config → nil`）；收到 `error(1011)` 不 emit `.error`、不回滚模式；config 在途收到 `error(1006)`
  → `sessionConfigUnsupported=true`、不重发、不 emit `.error`。

---

## 8. 典型时序

### 8.1 新会话携带 disable

```text
App 设置：avdetectorEnabled=false（已持久化）
App → SDK: setAvdetectorEnabled(false)        （无活跃会话 → 仅更新内存值）
App → SDK: startSession()
SDK → Server: session.start{config.avdetector="disable"}
Server: ctx.avdetector_enabled = False; _ensure_av_monitor() no-op
Server → App: session.ready + status
（本会话无 AVDetector 音频/视频喂入，无主动回合）
```

### 8.2 会话中 disable

```text
App 设置页：用户关闭开关
App: 先写 DataStore/UserDefaults
App → SDK: setAvdetectorEnabled(false)
SDK → Server: session.change{mode="config", avdetector="disable"}   （已 ready）
Server: ctx.avdetector_enabled=False; await _close_av_monitor()
Server → SDK: session.changed{mode="config", avdetector="disable"}
SDK: 识别 mode="config" → 只记录，不更新模式状态机
（检测流关闭；后续 feed 因 _av_monitor=None 短路；已开始的主动回合不强制取消）
```

### 8.3 会话中 enable（chat）

```text
App → SDK: setAvdetectorEnabled(true)
SDK → Server: session.change{mode="config", avdetector="enable"}
Server: ctx.avdetector_enabled=True; _ensure_av_monitor()（chat 模式）
Server → SDK: session.changed{mode="config", avdetector="enable"}
SDK: 识别 mode="config" → 只记录，不更新模式状态机
（monitor 懒开；首个音频/视频帧触发建流）
```

### 8.4 重连

```text
SDK startSession(resumeSessionID: 旧 id)
configProvider 读取当前 avdetectorEnabled
Server: session.start 按本帧值重置 ctx.avdetector_enabled
（服务端不依赖上一连接的会话级状态）
```

---

## 9. 边界与异常

| 场景 | 设计行为 |
|---|---|
| 旧客户端不携带 `avdetector` | 字段不在 `cfg.model_fields_set` → 服务端按 `enable`，与现状一致 |
| `session.start` 显式 `avdetector: null` | Schema 非法；DTO 可能接受，服务端按 malformed 回 1006，不建立会话（不得当缺省 enable） |
| 旧客户端收到 `session.changed(mode="config")` | 不会发生：旧客户端不会发送 `config` 请求，因此不会触发该回声 |
| 新客户端收到 `session.changed(mode="config")` | SDK 识别为控制回声，只记录日志（可选专用回调），不更新模式状态机；App 不显示模式切换 |
| 新客户端 + 旧服务端 | `session.start` 字段被忽略；`session.change(config)` 被旧服务端 1006 拒绝；SDK 在 config 单飞上下文识别为不支持并静默降级，不重发、不回滚模式 |
| `mode="config"` 缺少 `avdetector` / 显式 `null` | Schema 不允许 null；DTO 若接受则 orchestrator 返回 `1011`，DTO 若拒绝则网关返回 `1006`；均不是 1009，不修改状态、不回声 |
| `mode="config"` 非法值（如 `"ENABLE"`） | DTO/网关枚举校验失败 → **1006**（malformed）；直接调用 orchestrator 绕过网关的防御性测试 → **1011**；均不修改状态 |
| `mode` 非法（非 config） | DTO/网关枚举校验失败 → **1006**；直接调用 orchestrator 的防御性测试 → **1009** |
| `mode="config"` 但会话未建立 | `error(1011, "session not started")`，不回 1009 |
| config 业务失败与真实模式切换并发 | 1011 与 1009 分离；1009 逻辑不被触发，`pendingModeChange` 不被清空 |
| 非 `config` 模式携带 `avdetector` | 服务端忽略该字段（Schema 允许但语义只对 `config` 生效）；按原模式行为处理 |
| `session.change(config)` 在非 chat 模式 | 接受并记录状态；不挂 monitor；切回 chat 时生效 |
| 全局 `avdetector.enabled=false` | 会话级 enable 正常回声但不创建服务；无错误 |
| `_on_av_event` 被任意门控丢弃 | **零副作用**：不写 history / chat_store / recording 聊天文本，不设 `_turn_user_record`，不 spawn pipeline；复检点必须在第一个持久化写之前 |
| disable 时检测查询在飞 | `_close_av_monitor()` 取消管线；`_on_av_event` 开关兜底丢弃迟到结果（零落库/零录制） |
| disable 时主动回合已开始 | 不强制取消；按普通回合/用户 barge-in 语义处理 |
| disable 后立即 enable | 新建 monitor（新检测流）；视觉基线丢失，首次画面检查可能退化为“直接描述”（与周期重建已知行为一致） |
| 设置写盘失败 | Android：本次仍调用 SDK setter，但重启后回退到旧值；iOS UserDefaults 写失败极少见。文档不承诺事务性 |
| 设置页在无会话时打开 | SDK 只更新下一次 `session.start` 的值，不发 `session.change` |
| 用户在 `session.start` 已发出、`session.ready` 未到时切换开关 | SDK 记录 `pendingAvdetectorChange`，收到 `session.ready` 后补发 config；否则本次 start 携带的是旧值 |
| 连接断开 / 重连 | SDK 置 `sessionReady=false`、清 pending；最新值保留在状态盒，下一次 start 携带。Android 必须由 `connectionState` 观察桥接（现有 `reset()` 不会自动调用） |
| 非 chat 模式收到 config enable | 服务端 `_ensure_av_monitor` 的 `_pipeline_mode == "full"` 门阻止挂载；状态保留，切回 chat 时生效 |
| 会话中切模式（translate/omni）后设置页仍可改 | 状态保留；切回 chat 时按最新值决定是否挂 monitor |

---

## 10. 测试计划

### 10.1 协议 / 生成

- JSON Schema 编译（仓库既有 CI 命令）：
  `npx ajv compile -s packages/api-schema/message.json -r 'packages/api-schema/**/*.json' --spec=draft2020`
- 生成器漂移检查：
  - `python3 scripts/generate_python_api.py --check --out packages/sdk-python/src/livachat_protocol/generated`
  - `make check-android-api-generated`
  - `make check-swift-api-generated`
- DTO 单测：`session.start.config.avdetector` 序列化/反序列化；**AJV Schema 对显式 null 报错**，
  而生成的 Pydantic DTO 可能接受 null（用于验证 D2 的服务端 `model_fields_set` 守卫）；
  `session.change(mode=config)` 构造；`session.changed(mode="config", avdetector=...)` 编解码；
  普通模式 `session.changed` 不含 `avdetector`。

### 10.2 服务端

`services/tests/unit/session/test_orchestrator_avdetector.py` 新增：

1. `session.start` **未携带** `avdetector` → `ctx.avdetector_enabled=True` 且创建 monitor；
   **显式 null** → 1006，不建立会话（DTO 层 `model_fields_set` 分支）。
2. `session.start` `disable` → 不创建 monitor；音频/视频 feed 无检测调用。
3. `session.change(config, disable)` → monitor 关闭；后续 `feed_audio`/`feed_frame` 无动作。
4. `session.change(config, enable)` → chat 下重新挂载；translate/omni/transcribe 下**不创建 monitor**
   （`_ensure_av_monitor` 的 full 门），切回 chat 后挂载。
5. 分层断言：
   - DTO/网关集成层：`avdetector` 枚举非法值（如 `"ENABLE"`）→ **1006**（Pydantic 解析失败）；
     `mode="config"` 缺少 `avdetector` → 业务层 **1011**；
   - 直接调用 orchestrator（绕过 DTO）的防御性单测：`config` 的 `avdetector` 非法/缺失 → **1011**；
     非法非 config `mode` → **1009**；两种路径都不得把 config 失败报成 1009。
6. `session.change(config)` 不改变 `_pipeline_mode`、不发 `status(mode_changed)`。
7. `session.changed` 回声 `mode="config"`、`avdetector` 为本次值；translate 字段为 `null`/省略、
   不发 status。
8. disable 后 `_on_av_event` 丢弃迟到事件（含危险事件）；disable→enable 后旧 monitor 的迟到事件
   被 generation 校验丢弃。所有丢弃路径断言 **history/chat_store/recording 聊天文本无写入、
   `_turn_user_record` 未设置、pipeline 未创建**（即零副作用，而不只是没有 `_spawn_pipeline`；
   媒体录制链路不在断言范围）。
9. 全局 service=None 时 enable 不创建 monitor、不报错。

`services/tests/unit/session/test_session_manager.py`：`start(avdetector_enabled=...)` 默认/显式值。
`services/tests/unit/gateway/test_codec.py`：`make_session_changed(avdetector=...)` 输出字段。
集成测试：`session.start` + `session.change(config)` 的 s2c 序列（无 `status`）。

### 10.3 Android

- `PreferencesDataSourceTest` / `SettingsRepositoryTest` / `SettingsUseCaseTest`：默认 true、往返。
- `SettingsViewModelTest`：偏好发射映射到 UI state；setter 调用 use case + coordinator。
- `SettingsE2ETest`：`settings_ai_proactive` 可见、切换后重进仍保持。
- SDK：
  - `LivaChatConfig` 默认 true；
  - `SessionManager` 未 ready 时 `setAvdetectorEnabled` 只更新；
  - ready 后发送 `ClientSessionChangePayload(mode=CONFIG, avdetector=...)`；
  - `configProvider` 每次 start 读取最新值；
  - 收到 `session.changed(mode=CONFIG)` 不触发 `onSessionChanged`、不清 `pendingModeChange`；
  - start 已发、ready 未到时切换 → `pendingAvdetectorChange`，ready 后补发一次；
  - 连接断开 → `sessionReady=false`、pending 清空；重连后的 start 携带最新值；
  - `error(1011)` 不触发 `onError`、不清 `pendingModeChange`；config 在途 `error(1006)` →
    `sessionConfigUnsupported=true`、不重发、不触发 `onError`；
  - 快速连续 toggle：config 单飞、只发一帧，最新值在成功回声后补发。
- 编译验证：`./gradlew :LivaChatSDK:test :LivaChatApp:testDebugUnitTest`（按仓库实际任务名）。

### 10.4 iOS

- `SettingsStoreTests`：默认 true + 持久化。
- `SettingsViewModelTests`：初始化读取、setter 回调。
- `ConversationCoordinatorTests` / `AppContainerTests`：FakeSDK 记录 `setAvdetectorEnabled`。
- SDK 测试：`configProvider` 携带 `avdetector`；`SessionManager` 在会话前/后行为；
  收到 `mode=.config` 不 emit `.sessionChanged`；start 已发/ready 未到时切换 → ready 后补发；
  连接断开后 `sessionStartedOnConnection`/`sessionReady` 复位；`error(1011)` 不 emit `.error`、
  不回滚模式；config 在途 `error(1006)` → 不支持标记 + 不重发 + 不 emit `.error`；
  快速连续 toggle → config 单飞、最新值在成功回声后补发。
- `make swift-protocol-test` / `make swift-sdk-test` / `make ios-app-test`。
- `make ios-i18n-check`：确认新 key 在所有 locale 存在。

---

## 11. 实施清单（文件级）

### 11.1 协议与生成

- [ ] `packages/api-schema/common.json`：新增 `$defs.AvDetectorSwitch`；文件头版本说明 v1.2 → v1.3
- [ ] `packages/api-schema/c2s/session-start.json`：`config.avdetector`
- [ ] `packages/api-schema/c2s/session-change.json`：`mode` 增加 `config`；`avdetector`
- [ ] `packages/api-schema/s2c/session-changed.json`：`mode` 增加 `config`；可选 `avdetector`
- [ ] `packages/sdk-python/src/livachat_protocol/generated/*`（生成）
- [ ] `packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated/*`（生成）
- [ ] `packages/sdk-swift/Sources/LivaChatProtocol/Generated/*`（生成）
- [ ] `docs/ws-protocol-design.md` v1.3（含错误码表新增 1011 `SESSION_CONFIG_FAILED`）
- [ ] `packages/api-schema/README.md`（文件头 v1.2 → v1.3）
- [ ] `docs/fd_pipeline_avdetector.md` §4 D7 注记

### 11.2 服务端

- [ ] `services/shared/utils/errors.py`：新增 `SESSION_CONFIG_FAILED = 1011`
- [ ] `services/session/context.py`：`avdetector_enabled`
- [ ] `services/session/manager.py`：`start(..., avdetector_enabled=True)` + ctx
- [ ] `services/session/orchestrator.py`：
  - `_handle_session_start` 用 `model_fields_set` 区分“未携带”与“显式 null”（null → 1006），
    再解析并传参
  - `_ensure_av_monitor` 会话门（`_pipeline_mode=="full"` + `ctx.avdetector_enabled`）
  - `_handle_session_change` 先取 `new_mode`；no-ctx 的 config 回 1011
  - `_handle_session_change` `config` 分支（回声 `mode="config"` + `avdetector`；失败回 1011）
  - `_on_av_event` 初始 + await 后复检（mode + 开关）；复检点必须在
    history/chat_store/recording 写入之前（丢弃零副作用）
  - monitor generation（`_ensure` 分配、`_close` 递增、回调包装器校验）
- [ ] `services/gateway/codec.py`：`make_session_changed(avdetector=...)`
- [ ] 服务端测试（含“config 任何路径不回 1009”断言）

### 11.3 Android

- [ ] `UserPreferences.kt` / `PreferencesDataSource.kt` / `SettingsRepository.kt` / `SettingsUseCase.kt`
- [ ] `SettingsUiState.kt` / `SettingsViewModel.kt` / `SettingsScreen.kt`
- [ ] `AppModule.kt`（初始值）
- [ ] `ConversationCoordinator.kt`（转发 setter）
- [ ] 所有 `res/values*/strings.xml` 新文案
- [ ] SDK：`LivaChatConfig.kt` / `LivaChatSDK.kt` / `SessionManager.kt`
  - `setAvdetectorEnabled` + `sessionStartSent` / `sessionReady` / `pendingAvdetectorChange`
  - `configInFlight` / `queuedConfigValue` / `sessionConfigUnsupported`（单飞 + 合并）
  - `LivaChatSDK` 的 connectionState 观察 → `sessionManager.onConnectionLost()`
  - `handleSessionReady` 补发 pending；每次 start 读取最新值
  - `handleSessionChanged` 拦截 `mode=config`：记录后 return、清 in-flight、处理 queued
  - `handleError` 拦截 1011 与（config 在途的）1006：记录后 return，不调用 `onError`
- [ ] 不修改 `ConversationListenerAdapter` 的 1009 逻辑（config 错误到不了 App）
- [ ] 测试

### 11.4 iOS

- [ ] `SettingsStore.swift` / `SettingsViewModel.swift` / `SettingsView.swift`
- [ ] `AppContainer.swift`（config 初始值 + `onAvdetectorChanged`）
- [ ] `AppLaunchOptions.swift`（UI 测试 config）
- [ ] `ConversationProtocols.swift` / `ConversationCoordinator.swift` / FakeSDK
- [ ] `Resources/Localizable.xcstrings` 新 key（全部 locale）
- [ ] SDK：`LivaChatConfig.swift` / `LivaChatSDK.swift` / `SessionManager.swift` / `ProtocolMapping.swift`
  - `setAvdetectorEnabled` + 共享状态盒 + 每次 start 读取当前值
  - `sessionReady` / `pendingAvdetectorChange` / `configInFlight` / `queuedConfigValue` /
    `sessionConfigUnsupported`；`.sessionReady` 补发；`resetSessionGate` 清 pending/in-flight
  - `SessionManager` 拦截 `mode=.config`：记录后跳过 `.sessionChanged`；`ProtocolMapping.sessionMode` 返回 optional
  - `.error` 分支拦截 1011 与（config 在途的）1006：记录后不 emit `.error`
- [ ] 不修改 `ConversationModeModel.handleRejection` 的 1009 逻辑（config 错误到不了 App）
- [ ] 测试

---

## 12. 非目标 / 后续

- **不做服务端跨连接持久化**：会话级开关只存在于当前 `SessionContext`；App 的本地设置为唯一持久化来源。
- **不做危险事件单独开关**：关闭“允许 AI 主动搭话”后，AVDetector 的危险事件也不再生效。若产品需要
  “保留危险提醒、只关普通主动发言”，需另立需求并新增更细粒度配置（当前提示词/事件模型没有该分层）。
- **不做协议能力协商**：不通过 `session.ready.server_version` 判断服务端是否支持 `config` 模式；新客户端
  按需求直接发送，旧服务端拒绝时本地设置仍保留。后续如需严格降级，可加能力位。
- **不改 AVDetector 的检测/门控算法**：本文只加开关，不改提示词、节流、冷却、危险抢占等既有逻辑。
- **不改 Web/Mylife**：仅 iOS/Android App 与 SDK。

---

## 13. 决策记录

| ID | 决策 | 依据 |
|---|---|---|
| A1 | 字段名统一 `avdetector`（非 `avdector`） | 代码/配置现有拼写；避免双拼写 |
| A2 | `session.start.config.avdetector` 可选，缺省 enable | 旧客户端兼容；需求“默认 enable” |
| A3 | `session.change` 使用 `mode="config"` 控制伪模式 | 用户指定；复用既有 change 消息/错误/回声机制 |
| A4 | `session.changed.mode` 对 config 请求回显 `"config"`（与请求一致） | 用户拍板；研发阶段未上线，客户端同步升级 |
| A5 | config 变更不发 `status(mode_changed)` | 不是模式切换；避免客户端状态指示误动 |
| A6 | `avdetector` 回声只在 config 响应携带 | 最小化无意义字段 |
| A7 | SDK 不向 App 暴露 `SessionMode.config`；新增 `setAvdetectorEnabled` | 避免污染 App 模式状态机；App 只表达用户意图 |
| A8 | disable 立即关闭 monitor，不强制取消已开始的主动回合 | 最小行为变更；沿用现有回合/打断语义 |
| A9 | 会话级 enable 不覆盖部署级关闭 | 现有 `avdetector_service is None` 装配语义；运维开关优先 |
| A10 | SDK 拦截 `session.changed(mode=config)`，不向 App 抛模式事件 | `mode` 是控制回声不是 pipeline mode；保持客户端模式状态机不变 |
| A11 | `_ensure_av_monitor` 增加 `_pipeline_mode == "full"` 门 | config enable 可在非 chat 模式下到达；防止误挂载 |
| A12 | SDK 用 `sessionStartSent` / `sessionReady` / `pendingAvdetectorChange` 处理 start→ready 窗口 | 避免切换开关在本次会话丢失；Android 需桥接 connectionState |
| A13 | `_on_av_event` 在 await 后同时复检 mode 与开关 | 防止 await 窗口内切模式/关闭后仍起 chat 主动回合 |
| A14 | monitor generation 校验旧 monitor 的迟到事件 | disable→enable 后开关又为 true，仅靠 flag 无法区分新旧事件流 |
| A15 | config 业务失败使用独立错误码 `SESSION_CONFIG_FAILED = 1011`，禁止复用 1009 | 1009 已被 Android/iOS 用作模式切换回滚专属信号 |
| A16 | 旧服务端 1006 由 SDK 在 config 单飞上下文中识别为“不支持 config”，静默降级不重发 | 避免通用错误进入 App、避免 decode_guard 累积；不扩协议能力位 |
| A17 | 设置项文案确认采用“允许 AI 主动搭话”/“Enable Proactive AI Care” | 原文案“发起会话”易误解为自动建立新会话；实际是当前会话内主动搭话。仅字符串变更，不影响协议 |
| A18 | `_on_av_event` 的所有丢弃分支在第一个持久化写之前 return | 丢弃必须零副作用：history/chat_store/recording 均无新增，不只是不 spawn pipeline |
