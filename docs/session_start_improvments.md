# session.start 增加 App 语言上报（分析与设计）

> **状态**：已实施（2026-10-27）。协议、server、Android/iOS SDK/App 与自动化测试已完成；真机发布/联调未执行。详见 §6。
> **日期**：2026-10-27
> **范围**：Android App + Android SDK、iOS App + iOS SDK、server + 协议 Schema/DTO
> **协议关联**：`packages/api-schema/c2s/session-start.json`、`docs/ws-protocol-design.md` §2.2.1
> **语言关联**：`docs/android_multilingual.md`、`docs/ios_multilingual.md`

## 0. 目标与结论

### 0.1 需求

`session.start` 增加 `language` 字段，把 App **当前 UI 语言**上报给 server；server 将其保存到
**session 的变量**中，供以后使用，本期不改变任何对话/翻译/TTS 行为。

### 0.2 结论（先给实施口径）

| 项 | 结论 |
|---|---|
| 字段位置 | `payload.config.language`（不是 `payload.language`） |
| 是否必填 | 否（optional；老客户端不传，server 兼容） |
| 值格式 | App 的 canonical UI locale code：`en`、`zh-Hans`、`zh-Hant`、`ja`、`ko`、`ru`、`de`、`fr`、`it`、`es`、`pt`、`hi`、`id`、`th`、`vi`、`ar` |
| Android 取值 | `Context.resources.configuration.locales[0]` → canonical 化（§1.1） |
| iOS 取值 | `AppLocale.resourceCode`（`Bundle.main.preferredLocalizations` 解析，§2.1） |
| 取值时机 | 每次 `session.start` 由 App 传入的 provider 现取；静态值作为 SDK 兼容兜底（§1.2/§2.2） |
| server 变量 | `SessionContext.language: str = ""`（`services/session/context.py`） |
| 空值语义 | 存储值：未上报/空串 = 未知，**不默认成 `en`**；主动对话文本解析层（`docs/server_multilingual.md`）空/未知默认 `en` |
| 持久化 | 不落库；每次 `session.start`（含 resume）重新带入，`SessionContext` 每次重建 |
| 与 translate 的关系 | 完全独立；**不覆盖** `_my_language` / `_local_language`，不参与 `session.change` |
| 协议版本 | 向后兼容的 optional 字段，**不 bump** 协议版本 |

### 0.3 为什么放在 `config` 下（codebase 依据）

- `packages/api-schema/c2s/session-start.json`：
  - `payload` 是 `"additionalProperties": false`；
  - `payload.config` 是 `"additionalProperties": true`，并明确用于“Client capability and
    configuration declaration”。
- 这意味着：把 `language` 放在 `payload` 顶层会让**旧 server** 在反序列化时因未知字段拒绝整条
  `session.start`；放在 `config` 下，旧 server 的生成 DTO（Pydantic `extra="allow"`）会接受并忽略
  未知字段，新 server 显式读取。
- 同类平台/配置字段 `client_version`、`timezone` 已在 `config` 下，`language` 属于同一类信息。

### 0.4 现状事实（已核对，非猜测）

- 协议 Schema：`packages/api-schema/c2s/session-start.json`；`config` 当前 required 为
  `enable_video` / `audio_sample_rate` / `vad_sensitivity` / `client_version`，另有 optional
  `timezone`。
- Python DTO：`packages/sdk-python/src/livachat_protocol/generated/protocol_models.py`
  `ClientSessionStartConfig` 使用 `ConfigDict(extra="allow")`。
- Kotlin DTO：`packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated/ProtocolModels.kt`
  `ClientSessionStartConfig` 当前为 5 个字段（含 `timezone`）。
- Swift DTO：`packages/sdk-swift/Sources/LivaChatProtocol/Generated/ProtocolModels.swift`
  `ClientSessionStartConfig` 当前为 5 个字段（含 `timezone`）。
- Android SDK 每次 `startSession()` 都调用 `configProvider()` 重新构造 config：
  `apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatSDK.kt:134-148`；
  `SessionManager.startSession()` 组装 `ClientSessionStartPayload`：
  `apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/internal/session/SessionManager.kt:156-166`。
- iOS SDK 同样每次 start 通过 `configProvider()` 构造 config：
  `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatSDK.swift:120-128`；
  `SessionManager.startSession()` 组装 payload：
  `apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Session/SessionManager.swift:54-62`。
- server 入口：`services/session/orchestrator.py::_handle_session_start`（约 484 行）在 541 行调用
  `self._session_mgr.start(...)`；`SessionManager.start()` 在 140 行构造 `SessionContext`。
- `SessionContext`（`services/session/context.py:15-33`）已保存 `enable_video` /
  `audio_sample_rate` / `vad_sensitivity` / `client_version` / `timezone`。
- 去 Redis 后，`SessionManager.start()` 的注释（`services/session/manager.py:83-92`）明确：
  client 每次 `session.start` 携带完整 config，不从 snapshot 恢复这些字段；resume 同样走此路径。
- server 已在主动对话文本（avdetector 环境事件、cam 首帧直起轮）与 chat 普通回合
  输出语言回落（`docs/server_multilingual.md` §4.12）消费 App UI language；
  `_my_language` / `_local_language` 仍只由 `session.change` 设置，
  见 `services/session/orchestrator.py:357-367、780-833`。

---

## 1. Android 相关修改

### 1.1 上报值：从 App 资源配置取 canonical code

Android 没有 App 内 UI 语言选择器（`docs/android_multilingual.md` §1.2）：UI 语言跟随系统，
Android 13+ 可再通过系统“应用语言”页选择。Compose 与现有日期代码读取的都是
`resources.configuration.locales[0]`（例如 `TimelineViewModel.kt:447`），因此这里使用同一来源，
不读取 `Locale.getDefault()`，避免在 per-app locale 场景下把“系统语言”当成“App 语言”。

新增纯函数（建议文件：
`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/util/AppLanguage.kt`）：

```kotlin
internal val appLanguageCodes = setOf(
    "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de", "fr",
    "it", "es", "pt", "hi", "id", "th", "vi", "ar",
)

internal fun appLanguageCode(locale: Locale): String {
    val language = locale.language.lowercase(Locale.ROOT)

    if (language == "zh") {
        val script = locale.script.lowercase(Locale.ROOT)
        val country = locale.country.uppercase(Locale.ROOT)
        return when {
            script == "hant" -> "zh-Hant"
            script == "hans" -> "zh-Hans"
            country in setOf("TW", "HK", "MO") -> "zh-Hant"
            else -> "zh-Hans"
        }
    }

    val code = if (language == "in") "id" else language
    return if (code in appLanguageCodes) code else "en"
}
```

映射口径必须与现有 Android/iOS 资源一致：

| Android Locale | 上报 |
|---|---|
| `zh-CN` / `zh-SG`（无 Hant 脚本） | `zh-Hans` |
| `zh-TW` / `zh-HK` / `zh-MO` | `zh-Hant` |
| 遗留 `in` / 现代 `id` | `id` |
| `pt-BR` / `pt-PT` | `pt`（App 只有一套 pt-BR 文案） |
| `en-US` / `th-TH` / `vi-VN` 等 | 基础语言码 |
| App 未支持的 locale（例如 `fil-PH`） | `en`（与 `values/` 英文兜底一致） |

说明：

- `zh-*` 先看 `Locale.script`；没有脚本时按 region 判断。`zh-Hant` + `TW` 不冲突。
- 不能只取 `locale.language` 后判断是否在 `locales_config.xml` 中，因为 `zh-CN`/`zh-TW` 在
  `locales_config` 中是区域码，而 wire 使用脚本码；必须在 App 侧归一。
- 不支持语言回退 `en` 与 Android 资源解析“未匹配落 `values/`”一致。

`AppModule.provideLivaChatConfig()`（`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/di/AppModule.kt:36-64`）
中传入 **language provider**（不是一次性字符串）。provider 在每次 `session.start` 时执行，
因此 Android 13+ per-app language 切换、Activity 重建而进程未重启时，下一次会话仍能拿到新值：

```kotlin
languageProvider = {
    val locales = context.resources.configuration.locales
    if (locales.isEmpty()) "en" else appLanguageCode(locales[0])
},
```

同一 provider 也可用于诊断日志；但不要在 `provideLivaChatConfig` 构造时只算一次字符串。

### 1.2 Android SDK：透传字段（provider 优先）

`apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatConfig.kt`：

- 新增 optional 静态值 + optional provider（建议放在 `timezone` 之后，保持现有构造参数位置不变）：

```kotlin
/**
 * App UI language reported in session.start.config.language.
 * null = omit (old integrations / SDK users that do not set it).
 */
val language: String? = null,

/**
 * Optional dynamic provider, evaluated on every session.start.
 * Takes precedence over [language]; null result falls back to [language].
 */
val languageProvider: (() -> String?)? = null,
```

`apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatSDK.kt` 的 `configProvider`：

```kotlin
val language = config.languageProvider?.invoke() ?: config.language
ClientSessionStartConfig(
    enableVideo = config.enableVideo,
    audioSampleRate = config.audioSampleRate.hz,
    vadSensitivity = mapVadSensitivity(config.vadSensitivity),
    clientVersion = config.clientVersion,
    timezone = timezone,
    language = language,
)
```

约束：

- SDK **不**自行读取 `Locale.getDefault()` 或资源配置；语言由 App 通过 provider 决定，SDK 只执行并
  透传。provider 必须轻量、线程安全（每次 `session.start` 调用一次）。
- `language` 与 `languageProvider` 都为 null 时，按生成 DTO 的 optional 语义省略该字段；
  不发送空串。
- 现有 SDK 单测/集成测试中未设置 `language` / `languageProvider` 的构造调用保持编译与行为不变。

### 1.3 Android App 装配

- `AppModule.provideLivaChatConfig()`：加入 `languageProvider = { ... }`（§1.1）。
- 不新增 App 内语言设置入口；`LanguagePickerSheet` 仍是 translate/lecture 的“对方语言”选择器，
  与本字段无关。
- 不修改 `ConversationViewModel` 的 `deviceLanguage = Locale.getDefault().language`；该值是
  translate 的“说话者语言”，语义不同。后续若要统一 source 语言口径，另立需求。

### 1.4 Android 测试

新增 `AppLanguageTest`（JVM，纯函数表驱动）：

- `zh-CN`、`zh-SG` → `zh-Hans`
- `zh-TW`、`zh-HK`、`zh-MO`、`zh-Hant` → `zh-Hant`
- `in-ID` / `id-ID` → `id`
- `pt-BR` / `pt-PT` → `pt`
- `en-US`、`th-TH`、`vi-VN`、`ar-EG` → 基础码
- `fil-PH`、`tr-TR`、`Locale.ROOT` → `en`
- 断言 `appLanguageCodes` = 16 个 canonical code；用 `I18nResourceFilesTest` 同款方式读取
  `locales_config.xml`/`values*` 目录，防止新增语言时漏改 helper（不允许只靠人工同步）

SDK 测试：

- `SessionManagerTest`：`configProvider` 使用 provider；第一次 start 返回 `zh-Hans`、改变 provider
  值后第二次 start 返回 `ja`，断言两条 wire `session.start` 分别为对应值（证明不是构造时快照）。
- 静态 `language` 兜底：provider 为 null 时发送静态值；两者都为 null 时省略字段。
- `MessageCodecTest`：生成 DTO 的 optional 行为——设置时输出、`null` 时省略。
- `LivaChatSDKIntegrationTest`：`LivaChatConfig(language = ...)` / provider 原样保留（配置层不丢失）。

App 测试：

- 可加一个 `AppModule` 装配测试（若现有 Hilt 测试成本高，则用纯函数测试覆盖；
  `AppModule` 改动为 provider 闭包）。
- 现有 `:LivaChatApp:testDebugUnitTest` 全量回归。

### 1.5 Android 文档与版本

实施时同步：

- `docs/android_sdk_design_v3.md`：§2.2 `LivaChatConfig` 字段表（`language` + `languageProvider`）
  + §5.3 `session.start.config` 示例。
- `docs/android_app_arch.md`：`LivaChatConfig` 构造参数摘要加入 `language` / `languageProvider`。
- `docs/android_multilingual.md`：在“App 语言来源/per-app locale”节说明 `session.start` 上报值
  及 canonical 映射（`zh-CN → zh-Hans` 等）。
- 版本号按项目规则：`versionCode 138 → 139`（`versionName 1.0.0` 不变）。

---

## 2. iOS 相关修改

### 2.1 上报值：直接使用 `AppLocale.resourceCode`

`apps/ios/LivaChatApp/Core/UI/AppLocale.swift` 已经提供了与本需求完全一致的口径：

```swift
/// The bundle localization code, e.g. `zh-Hant` or `ar`.
static var resourceCode: String {
    resolvePreferred(Bundle.main.preferredLocalizations)
}
```

- 返回值来自 `Bundle.main.preferredLocalizations`，即 App 的实际资源语言，不是设备语言；
  per-app language 也能正确反映。
- `resolvePreferred` 已在 `AppLocale.supportedCodes` 中做匹配：`zh-TW → zh-Hant`、
  `zh-CN → zh-Hans`、`in → id`、`pt-BR → pt`、不支持 → `en`。
- 因此 iOS **不需要**新增语言映射函数，直接把 `AppLocale.resourceCode` 传入 SDK config。

### 2.2 iOS SDK：透传字段（provider 优先）

`apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatConfig.swift`：

- 新增 optional 静态值 + optional provider（属性声明可放在 `timezone` 附近）：

```swift
/// App UI language reported in `session.start.config.language`.
/// nil = omit the field (old integrations / SDK users that do not set it).
public var language: String?

/// Optional dynamic provider, evaluated on every session.start.
/// Takes precedence over `language`; a nil result falls back to `language`.
public var languageProvider: (@Sendable () -> String?)?
```

- 在 `init` 参数列表**末尾**（现有 `tlsPolicy` 之后）增加：

```swift
language: String? = nil,
languageProvider: (@Sendable () -> String?)? = nil
```

  默认参数保证现有调用源兼容。
- `LivaChatConfig` 仍是 `Sendable`：`String?` 与 `@Sendable` closure 均满足。
- provider 必须轻量、线程安全；每次 `session.start` 调用一次。

`apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Session/ProtocolMapping.swift`：

```swift
static func startConfig(
    enableVideo: Bool,
    audioSampleRate: Int,
    vadSensitivity: LivaChatProtocol.VadSensitivity,
    clientVersion: String,
    timezone: String?,
    language: String?
) -> ClientSessionStartConfig {
    ClientSessionStartConfig(
        enableVideo: enableVideo,
        audioSampleRate: AudioSampleRate(rawValue: audioSampleRate) ?? .hz16000,
        vadSensitivity: vadSensitivity,
        clientVersion: clientVersion,
        timezone: timezone,
        language: language
    )
}
```

`apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatSDK.swift` 的 `configProvider`：

```swift
let language = config.languageProvider?() ?? config.language
ProtocolMapping.startConfig(
    ...,
    timezone: config.timezone ?? TimeZone.current.identifier,
    language: language
)
```

与 Android 相同的约束：SDK 不读取 `Bundle` / `Locale`，由 App 通过 provider 决定；`nil` 时省略。

### 2.3 iOS App 装配

生产路径：`apps/ios/LivaChatApp/App/AppContainer.swift:429-438`

```swift
let config = LivaChatConfig(
    serverURL: url,
    token: token,
    deviceID: deviceID,
    clientVersion: AppInfo.clientVersion,
    enableVideo: true,
    logFileURL: AppLog.fileURL,
    timezone: TimeZone.current.identifier,
    tlsPolicy: tlsPolicy,
    language: AppLocale.resourceCode,
    languageProvider: { AppLocale.resourceCode }
)
```

静态 `language` 用于诊断/兜底，provider 保证每次 `session.start` 现取（iOS per-app language
切换通常重启 App；即使不重启，provider 也不会使用旧值）。

UI 测试/本地 server 路径：`apps/ios/LivaChatApp/App/AppLaunchOptions.swift:18-29`
`ServerOverride.livaChatConfig` 同样加入 `language: AppLocale.resourceCode` 与
`languageProvider: { AppLocale.resourceCode }`，保证 UI 测试走真实 server 时也符合新协议。

不修改 `ConversationModeModel` 的 `deviceLanguage`（translate 的说话者语言来源），与 §1.3 同理。

### 2.4 iOS 测试

- `SessionManagerTests`：`startConfig` 带 `language = "zh-Hans"`，断言发出的 `session.start`
  JSON `payload.config.language == "zh-Hans"`。
- provider 动态测试：第一次 start provider 返回 `zh-Hans`，改变返回值后第二次 start 断言为
  `ja`（证明不是构造时快照）。
- 静态 `language` 兜底：provider 为 nil 时发送静态值；两者都为 nil 时省略字段。
- `ProtocolMapping` 单测（或 `SessionManagerTests` 覆盖）：
  `startConfig(language: "zh-Hant")` 映射到生成 DTO 的 `language`。
- 生成 DTO 编码测试：`EncodingTests` 中设置 language 时输出；nil 时不出现
  `language` 键（Swift synthesized Codable optional 语义）。
- `ConfigTests`：`LivaChatConfig` 默认 `language == nil`、`languageProvider == nil`；显式赋值保留。
- `AppLocaleTests` 已有 zh-Hans/zh-Hant/en 回退用例，无需重复，但可在 App 配置装配测试中断言
  `AppLocale.resourceCode` 与传给 SDK 的值一致（若现有测试 harness 可取到 config）。

### 2.5 iOS 文档与版本

实施时同步：

- `docs/ios_sdk_design.md`：§5.1 `LivaChatConfig` 字段表（`language` + `languageProvider`）
  + §5.3 `session.start.config` 示例。
- `docs/ios_app_arch.md`：`LivaChatConfig` 构造摘要加入 `language` / `languageProvider`。
- `docs/ios_multilingual.md`：说明 `AppLocale.resourceCode` 是上报源，以及 `zh-Hans`/`zh-Hant`
  wire 值。
- 版本号按项目规则：`CURRENT_PROJECT_VERSION 11 → 12`（`MARKETING_VERSION 1.0.0` 不变）。

---

## 3. Server 相关修改

### 3.1 协议 Schema（SSOT）

`packages/api-schema/c2s/session-start.json` 的 `payload.config.properties` 增加 optional
`language`（放在 `timezone` 之后）：

```json
"language": {
  "type": "string",
  "description": "Client app UI language (canonical app locale code), e.g. \"en\", \"zh-Hans\", \"zh-Hant\", \"pt\", \"ar\". Use the app's resolved resource language, not the device locale. Optional; omitted by old clients. The server stores it on the session for later use."
}
```

同时更新 `examples[0].payload.config`：

```json
"language": "zh-Hans"
```

设计约束：

- **不**加入 `required`（老客户端继续合法）。
- **不**定义为 enum/pattern 白名单：新增 App 语言时不能因为 server 未同步而拒绝整个
  `session.start`；语言值由 App 保证 canonical，server 本期只保存。
- `config.additionalProperties: true` 保持不变。
- 协议版本不 bump（optional、向后兼容）；更新 `docs/ws-protocol-design.md` §2.2.1 的示例与字段表：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `payload.config.language` | `string` | 否 | App 当前 UI 语言 canonical code（`en`、`zh-Hans`、`zh-Hant` 等）；不是设备语言；老客户端可省略 |

生成物（禁止手改）：

```bash
python3 scripts/generate_python_api.py \
  --schema packages/api-schema \
  --out packages/sdk-python/src/livachat_protocol/generated
make generate-android-api
make generate-swift-api
```

预期生成 diff：

- Python `ClientSessionStartConfig.language: str | None = Field(default=None, ...)`。
- Kotlin `@SerialName("language") val language: String? = null`（optional 默认）。
- Swift `public let language: String?`，init 默认 `nil`，`CodingKeys.language = "language"`。

### 3.2 `SessionContext`：保存到 session 变量

`services/session/context.py`：

```python
# Config carried from session.start
enable_video: bool = False
audio_sample_rate: int = 16000
vad_sensitivity: str = "medium"
client_version: str = ""
# App 当前 UI 语言（canonical code；未上报/空 = ""）
language: str = ""
timezone: str = ""
```

- 选 `SessionContext.language` 作为“session 的一个变量”：它已经是 session.start config 的承载对象，
  后续 LLM/TTS/日志/Agent 需要时直接读 `ctx.language`。
- 不新增 orchestrator 级 `_app_language` 字段，避免出现两份生命周期不同的状态。
- 不持久化：`SessionManager.start()` 每次新建 `SessionContext`，resume 也重新读取客户端上报；
  这与现有 `timezone` / `client_version` 的去 Redis 语义一致。
- 保存的是完整 App locale（例如 `zh-Hans` / `zh-Hant`）。未来若下游只接受 ISO-639-1 基码，
  必须显式归一（可复用 `services/session/orchestrator.py::_normalize_lang` 的规则），不要把
  完整码直接塞给只接受 `zh` 的接口。

### 3.3 `SessionManager.start()`：接收并赋值

`services/session/manager.py`：

```python
async def start(
    self,
    *,
    device_id: str,
    user_id: str = "...",
    enable_video: bool = False,
    audio_sample_rate: int = 16000,
    vad_sensitivity: str = "medium",
    client_version: str = "",
    language: str | None = None,
    timezone: str | None = None,
    resume_id: str | None = None,
) -> tuple[SessionContext, WorkingMemory]:
```

构造：

```python
ctx = SessionContext(
    session_id=session_id,
    device_id=device_id,
    user_id=user_id,
    enable_video=enable_video,
    audio_sample_rate=audio_sample_rate,
    vad_sensitivity=vad_sensitivity,
    client_version=client_version,
    language=language or "",
    timezone=canonical_tz or "",
    ...
)
```

日志（`session started`）建议增加：

```python
language=language or "",
```

“空/缺失 → `""`”是明确契约：不把未知语言误判成英文，不改变任何现有行为。

### 3.4 `orchestrator._handle_session_start()`：传入 cfg.language

`services/session/orchestrator.py` 541 行附近：

```python
ctx, history = await self._session_mgr.start(
    device_id=self._handler.principal.device_id,
    user_id=self._handler.principal.user_id,
    enable_video=cfg.enable_video,
    audio_sample_rate=cfg.audio_sample_rate,
    vad_sensitivity=cfg.vad_sensitivity,
    client_version=cfg.client_version,
    language=cfg.language,
    timezone=cfg.timezone,
    resume_id=resume_id,
)
```

注意：

- 这是新会话/重连的唯一汇聚点（当前 `SessionManager.start()` 只有这一个 caller，已核对
  `rg '_session_mgr.start' services/session`）。
- `session.start` 当前会重置 `_my_language` / `_local_language` 等模式状态（orchestrator 498-501），
  本字段**不参与**这些重置；App UI language 与 translate 源/目标语言是不同概念。
- 不修改 `session.ready` 回包、不新增 s2c 字段、不写数据库。

### 3.5 Server 测试

新增 `services/tests/unit/session/test_session_manager_language.py`：

- `start(language="zh-Hans")` → `ctx.language == "zh-Hans"`；
- 不传 `language` → `ctx.language == ""`；
- `language=None` → `""`；
- resume：第一次 `language="en"`，第二次同 `resume_id` + `language="ja"` → 新 ctx 为 `"ja"`
  （证明每次 session.start 以客户端最新值为准，无 snapshot 残留）；
- 可选：非法/未知字符串原样保留（本期不做白名单），用于锁定“server 不做语言映射”的契约。

集成测试：

- `services/tests/integration/orchestrator/test_full_turn.py::test_session_start_sends_ready`：
  给 `ClientSessionStartConfig` 加 `language="zh-Hans"`，run 后断言
  `fake_orchestrator._ctx.language == "zh-Hans"`。
- 现有未带 `language` 的 session.start 用例继续通过（缺失 → `""`）。

协议 DTO 测试：

- `packages/sdk-python/tests/test_protocol_models.py`：`ClientSessionStartConfig(language="zh-Hans")`
  round-trip；`exclude_unset` 下未设置时 wire 不含 `language`。
- `packages/sdk-kotlin/src/test/.../MessageCodecTest.kt`：编码含 `language`；optional 省略。
- `packages/sdk-swift/Tests/LivaChatProtocolTests/EncodingTests.swift` /
  `DecodingTests.swift`：编码/解码 `language`；nil 省略；fixture `schema-examples.json` 同步。

### 3.6 Server 文档

- `docs/ws-protocol-design.md` §2.2.1：示例 + 字段表（§3.1）。
- `docs/session_start_improvments.md`：本文。
- 不新增数据库 migration，不新增配置项，不新增环境变量。

---

## 4. 兼容性、边界与非目标

### 4.1 兼容矩阵

| 客户端 | server | 结果 |
|---|---|---|
| 旧客户端（无 `language`） | 新 server | `cfg.language is None` → `ctx.language == ""`；session 正常建立 |
| 新客户端（有 `language`） | 旧 server | `config.additionalProperties: true` + Pydantic `extra="allow"` → 字段被忽略；session 正常建立 |
| 新客户端 | 新 server | 字段保存到 `SessionContext.language` |
| 新 SDK、未配置 `language` | 新 server | SDK 省略字段（nil/null）→ `ctx.language == ""` |

关键点：字段放在 `config` 而不是 `payload` 顶层，才能保证“新客户端 → 旧 server”不因
`payload.additionalProperties: false` 被拒绝。

### 4.2 非目标（本期不做）

- 不根据 `language` 生成/翻译 AI 回复、TTS、system prompt 或 MyLife 内容。
- 不把 `language` 用作 translate 的 `my_language` / `local_language`。
- 不新增 `session.ready` 回显、不新增 S2C 字段。
- 不写入 SQLite/PG、不新增 session snapshot、不做跨进程恢复。
- 不改变 App UI 语言选择机制（iOS/Android 仍只跟随系统/per-app 设置）。
- 不改变 `LanguagePickerSheet` / `LanguagePickerDialog` 的 translate 候选列表。

### 4.3 取值时机

`language` **不在 App 启动时快照**。App 通过 `LivaChatConfig.languageProvider` 传入一个轻量
闭包，SDK 在每次 `session.start` 的 `configProvider` 中执行：

- Android：provider 读取 `context.resources.configuration.locales[0]` 并 canonical 化。即使
  per-app language 切换只重建 Activity、进程级 Hilt singleton 仍在，下一次 `session.start`
  也会重新取值。
- iOS：provider 读取 `AppLocale.resourceCode`。per-app language 切换通常重启 App；即使进程
  存活，下一次 `session.start` 也不会使用构造时的旧值。
- 静态 `LivaChatConfig.language` 仅作为 provider 未配置时的兜底（SDK 使用者/测试）；iOS
  生产路径同时设置静态值（诊断/兜底）与 provider，Android 生产路径只设置 provider。静态值不参与
  动态刷新。

会话进行中切换语言不会 retroactively 改变已建立的 session 变量：新值只在**下一次**
`session.start`（含重连）生效；这与字段只在 `session.start` 上报的协议语义一致。

---

## 5. 实施顺序与验收

### 5.1 顺序

1. 修改 `packages/api-schema/c2s/session-start.json`（+ example）。
2. 重生成 Python/Kotlin/Swift DTO；确认生成器 `--check` 通过。
3. Server：`SessionContext` → `SessionManager.start` → `orchestrator` → 测试。
4. Android SDK（`LivaChatConfig` 的静态值 + provider / `LivaChatSDK`）+ App
   （`AppLanguage` / `AppModule`）+ 测试。
5. iOS SDK（`LivaChatConfig` 的静态值 + provider / `ProtocolMapping` / `LivaChatSDK`）+ App
   （`AppContainer` / `AppLaunchOptions`）+ 测试。
6. 更新协议/SDK/多语言文档；按项目规则 bump Android versionCode / iOS build。

### 5.2 验收命令（实施后执行）

```bash
# 协议生成与一致性（从仓库根目录执行）
python3 scripts/generate_python_api.py --check \
  --schema packages/api-schema \
  --out packages/sdk-python/src/livachat_protocol/generated
python3 scripts/generate_kotlin_api.py --check \
  --schema packages/api-schema \
  --out packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated \
  --package dev.livachat.protocol.generated
make check-swift-api-generated
npx ajv-cli compile -s packages/api-schema/message.json \
  -r 'packages/api-schema/common.json' \
  -r 'packages/api-schema/c2s/*.json' \
  -r 'packages/api-schema/s2c/*.json' --spec=draft2020

# Python SDK / server（子 shell，避免改变后续命令的工作目录）
(cd packages/sdk-python && pytest)
source .venv/bin/activate && make server-test

# Android（从仓库根目录执行）
apps/android/gradlew -p apps/android :LivaChatSDK:testDebugUnitTest
apps/android/gradlew -p apps/android :LivaChatApp:testDebugUnitTest
apps/android/gradlew -p apps/android :LivaChatApp:assembleDebug

# iOS（子 shell，避免连续 cd 改变工作目录）
(cd packages/sdk-swift && swift test)
(cd apps/ios/LivaChatSDK && swift test)
make ios-app-test IOS_SIMULATOR="iPhone 16"
make ios-i18n-check
```

> `make check-android-api-generated` 依赖 `git status` 判断生成物是否已提交；在工作区尚未提交时
> 会误报失败。因此上述验收优先使用 `scripts/generate_kotlin_api.py --check`；CI/提交后再跑
> `make check-android-api-generated`。

### 5.3 验收标准

- wire 上出现：
  ```json
  "config": {
    "enable_video": true,
    "audio_sample_rate": 16000,
    "vad_sensitivity": "medium",
    "client_version": "ios-1.0.0.12",
    "timezone": "Asia/Shanghai",
    "language": "zh-Hans"
  }
  ```
- server 侧 `SessionContext.language == "zh-Hans"`；缺失时为 `""`。
- resume 后语言取客户端本次上报值，不残留上一会话。
- provider 在同一进程内改变返回值后，下一次 `session.start` 携带新值（不是构造时快照）。
- `session.change` 的 `my_language` / `local_language` 行为不变；translate/lecture 回归通过。
- 旧客户端无 `language` 的接入测试通过；新客户端连旧 server 不被拒绝。
- Android/iOS 在 `zh-CN`、`zh-TW`、`en`、不支持系统语言（回退 `en`）四种场景上报值符合 §1.1/§2.1。

---

## 6. 实施结果（2026-10-27）

### 6.1 已落地

- `packages/api-schema/c2s/session-start.json`：新增 optional `config.language` + example。
- 重新生成 Python/Kotlin/Swift DTO；三个生成器 `--check` 均 `up to date`。
- Server：`SessionContext.language`、`SessionManager.start(language=...)`、`orchestrator` 透传。
- Android SDK：`LivaChatConfig.language` + `languageProvider`；`LivaChatSDK` 每次 start 现取。
- Android App：`AppLanguage` canonical 归一 + `AppModule.languageProvider`；versionCode `138 → 139`。
- iOS SDK：`LivaChatConfig.language` + `languageProvider`；`ProtocolMapping` / `LivaChatSDK` 透传。
- iOS App：`AppContainer` / `AppLaunchOptions` 使用 `AppLocale.resourceCode`；build `11 → 12`。
- 文档：`docs/ws-protocol-design.md`、Android/iOS SDK/App 文档、多语言文档同步。

### 6.2 自动化验证

| 范围 | 结果 |
|---|---|
| Python SDK protocol tests | `12 passed` |
| Swift protocol tests | `38 passed` |
| iOS SDK host tests | `69 passed` |
| iOS App tests | `519 tests / 0 failures / 1 skipped` |
| iOS i18n guard | `235 keys / 12 plural / 17 locales / 4 InfoPlist keys` |
| Android SDK unit tests | `189 tests / 0 failures` |
| Android App unit tests | `968 tests / 0 failures / 4 skipped` |
| Android `assembleDebug` | `BUILD SUCCESSFUL` |
| Server language unit tests | `5 passed` |
| Server full-turn integration | `2 passed` |
| Server session unit（排除 `test_lecture_translate.py`） | `376 passed` |
| AJV schema compile | `message.json is valid`（仅有 strict-mode 提示） |

**环境说明**：本次临时测试环境未安装完整的 native Opus 运行库，`tests/unit/session/test_lecture_translate.py`
在 `opus_encoder_ctl` 初始化处失败；该失败与本改动无关，已用排除该文件的 session 套件验证其余 376 例。
真机 per-app language、server 真实部署联调与 Android/iOS 安装包验收尚未执行。
