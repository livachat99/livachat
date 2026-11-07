# LivaChat iOS App 设置与凭证模块设计（M1）

**状态**：已实施（M1.3；单测通过）
**日期**：2026-10-20
**上游文档**：
- `docs/ios_app_m1.md` §6（存储、预检、保存与生效、平台配置）
- `docs/ios_sdk_design.md`（wire token、TLS 策略）
- Android 基线：`SettingsScreen.kt`、`SettingsViewModel.kt`、`ServiceProbe.kt`、`ServerOption.kt`

M1 设置只有“服务 / 通用 / 关于”三块；不含 Video、语言、设备选择器、隐私政策 URL。

---

## 1. 数据模型

```swift
struct ServerAddress: Equatable {
    let host: String
    let port: Int
    var webSocketURL: URL   // wss://host:port/v1/ws/stream
    var healthURL: URL      // https://host:port/healthz_token
}
```

- 输入格式：`ip:port` 或 `域名:port`（不含 scheme/path/query）。
- 使用 `URLComponents` 解析；要求 host 非空、port 在 `1...65535`；IPv6 字面量需带方括号。
- 默认 scheme 为 `wss`/`https`；M1 产品设置页不接受 `ws://`（开发联调走 §7 的 DEBUG 覆盖）。
- 输入 `host` 不含端口时视为无效（不猜默认端口）。

```swift
struct SavedServerConfig: Equatable {
    let address: String        // 原样保存用户输入（规范化前后？M1 保存 trim 后原文）
    let secret: String         // 明文 secret，仅 Keychain
    let wireToken: String      // sha1_hex(secret + "LivaChat")
}

struct AppLaunchConfig {
    let serverAddress: String
    let secret: String
    let wireToken: String
    let deviceID: String
    let tlsPolicy: TLSPolicy
}
```

- SDK `LivaChatConfig.serverURL` 使用 `ServerAddress.webSocketURL`；
- token 只传 `wireToken`，SDK 不派生、不持久化；
- `deviceID` 为 UUID v4 字符串，首次需要时生成并存 Keychain；
- `tlsPolicy` 默认 `.system`；Settings 提供“允许自签名证书”开关（**默认开启**），SDK 与 ServerProbe 同用 `.allowSelfSigned`；DEBUG 仍可用 §7 的环境变量覆盖。

---

## 2. 存储

| 数据 | 存储 | Key/Account |
|---|---|---|
| 服务器地址 | UserDefaults | `settings.server_address` |
| 明文 secret | Keychain | service `dev.livachat.app.credentials`, account `server_secret` |
| wire token（缓存） | Keychain | 同 service, account `wire_token` |
| deviceID | Keychain | 同 service, account `device_id` |
| 触觉开关 | UserDefaults | `settings.haptic_enabled`（默认 true） |
| 首启完成 | UserDefaults | `settings.onboarding_completed` |
| 允许自签名证书 | UserDefaults | `settings.tls_allow_self_signed`（默认 true） |

Keychain 属性：`kSecClassGenericPassword`，`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`；不开启 iCloud 同步。

### 2.1 CredentialStore

```swift
protocol CredentialStoring {
    func read(_ account: CredentialAccount) throws -> String?
    func write(_ value: String, for account: CredentialAccount) throws
    func delete(_ account: CredentialAccount) throws
}

enum CredentialAccount: String {
    case serverSecret = "server_secret"
    case wireToken = "wire_token"
    case deviceID = "device_id"
}
```

- 写回统一 `SecItemUpdate`，不存在则 `SecItemAdd`；读用 `SecItemCopyMatching`。
- 错误转成 `CredentialError`（OSStatus + 操作名）；secret/wire token **不得写日志**。
- `deviceID` 读取失败/缺失时生成 UUID v4 并写回；生成失败（Keychain 不可用）时降级为内存 UUID，并记录一次 warning（连接仍可工作，但重启后变化）。
- Keychain 写入在保存动作中同步完成（数据量小）；保存成功后再重建 runtime，避免半保存状态。

### 2.2 派生 wire token

```swift
func wireToken(secret: String) -> String {
    Insecure.SHA1.hash(data: Data((secret + "LivaChat").utf8))
        .map { String(format: "%02x", $0) }.joined()
}
```

已知向量（来自 Android `ServerOptionTest`）：

```
wireToken("dev-livachat-token") == "1161f736057f8e087b6351a66254f32510d0121c"
```

- 使用 CryptoKit `Insecure.SHA1`（协议要求 SHA1，不是安全哈希选择）。
- secret 允许任意非空 UTF-8 字符串；保存前只 trim 首尾空白，不允许只含空白。
- 设置页默认掩码显示；显示/隐藏只影响 UI，不写日志。

---

## 3. SettingsStore

```swift
@MainActor
final class SettingsStore: ObservableObject {
    @Published private(set) var savedAddress: String
    @Published var hapticEnabled: Bool { didSet { defaults.set(...) } }
    @Published private(set) var onboardingCompleted: Bool
    @Published var tlsAllowSelfSigned: Bool { didSet { defaults.set(...) } }
    var tlsPolicy: TLSPolicy { tlsAllowSelfSigned ? .allowSelfSigned : .system }

    func save(address: String) throws
    func markOnboardingCompleted()
    func loadSavedConfig(credentials: CredentialStoring) throws -> SavedServerConfig?
}
```

- `loadSavedConfig`：地址存在且 secret 存在时返回；wire token 缓存缺失则重新派生并写回。
- 地址/secret 任一缺失 → 返回 nil；不自动填 Android 开发 IP。
- Keychain 会跨 App 卸载保留，而 UserDefaults 不会。`SettingsViewModel` 初始化时若 saved address 为空，不得读取或显示 `server_secret`，并清理无主的 `server_secret`/`wire_token`；用户清空地址时输入框中的 secret 也必须立即清空。
- `hapticEnabled` 变更立即持久化，不需要重建 runtime；会话进入 `SPEAKING` 时由 `ConversationCoordinator` 读取最新值。
- `tlsAllowSelfSigned` 属于运行时配置：设置页只在“保存并应用”时落盘，并触发 runtime 重建；默认开启。
- 用户保存地址/secret 成功后，先写 UserDefaults/Keychain，再由 `AppContainer` 重建 runtime。

---

## 4. 校验

`ServerConfigValidator`：

| 字段 | 规则 | 错误文案 key |
|---|---|---|
| 地址 | 非空；`host:port`；port 1...65535；无 path/query | `settings_error_address` |
| secret | trim 后非空 | `settings_error_secret` |
| 地址+secret+TLS 是否变化 | 与已保存值比较；未变化不显示保存按钮 | — |

- 地址解析失败不发起 probe、不保存。
- secret 输入只做长度/空白校验，不猜测服务端规则。
- 保存前若正在通话：弹出确认“应用并重新连接 / 取消”；取消保持原配置和 runtime。

---

## 5. ServerProbe

```swift
enum ProbeResult: Equatable { case available, authFailed, unreachable }

protocol ServerProbing {
    func probe(address: ServerAddress, wireToken: String, deviceID: String) async -> ProbeResult
}
```

请求：

```
GET https://<host>:<port>/healthz_token?token=<wire_token>&device_id=<uuid>
```

- `URLComponents` 构造并百分号编码 query；
- 5s 请求超时；**不跟随重定向**：production URLSession delegate 的 `willPerformHTTPRedirection` 返回 nil（302/登录页即使落地 200 也不能误判可用），单测覆盖重定向拒绝与 TLS 错误；
- `200` → `.available`；`401` → `.authFailed`；其他状态/网络错误/TLS 错误 → `.unreachable`；
- 只读、不重试；取消（用户改输入/离开页面）走 Swift Concurrency cancellation；
- **TLS 策略必须与 SDK 一致**：默认 system；设置页勾选“允许自签名证书”时使用 `allowSelfSigned`，`URLSessionServerProbe` 接受服务器信任；该策略同样用于 WS 连接。自签/主机名不匹配的局域网 server 必须开启此项（Android SDK 默认 trust-all，iOS 默认不信任）。

UI 三态：可用（Success）/认证失败（Error）/不可用（Error）；探测中显示 spinner 并禁用按钮。

---

## 6. 平台配置（Info.plist）

当前配置：

| Key | 值 | 说明 |
|---|---|---|
| `NSMicrophoneUsageDescription` | 本地化文案（en/zh-Hans） | 系统麦克风权限弹窗 |
| `NSLocalNetworkUsageDescription` | 本地化文案 | 连接局域网服务器/Mac Mock Server |
| `NSAppTransportSecurity` → `NSAllowsLocalNetworking` | `true` | 仅允许本地网络明文；不全局关闭 ATS |
| `UIBackgroundModes` | `[audio]` | 后台 TTS / 未静音后台采集；没有 entitlement |
| `NSCameraUsageDescription` | 本地化文案 | 视频对话/相机 |
| `NSPhotoLibraryAddUsageDescription` | 本地化文案 | 保存 AI 图片 |

- 产品默认连接 `wss://`；ATS 只放行本地网络，公网 `ws://` 仍被拒绝。
- 自签名服务器：Settings → Service → Allow self-signed certificate 开启后使用 `.allowSelfSigned`；也可在设备安装并信任 CA。DEBUG 可用 `LIVACHAT_TLS_ALLOW_SELF_SIGNED=1` 覆盖。
- 工程使用显式 `Resources/Info.plist`（`GENERATE_INFOPLIST_FILE = NO`）；新增后台模式只需改该文件，不需要 pbxproj 或 entitlement 改动。修改后应检查构建产物：`plutil -extract UIBackgroundModes json -o - LivaChatApp.app/Info.plist`。

---

## 7. 开发联调配置（DEBUG）

为 Mock Server（`ws://` + `dev-token`）和设备联调提供**仅 DEBUG** 的环境变量，不进入产品设置页：

| 环境变量 | 作用 |
|---|---|
| `LIVACHAT_SERVER_URL` | 完整 WebSocket URL，如 `ws://127.0.0.1:18080/v1/ws/stream`；覆盖设置页地址 |
| `LIVACHAT_SERVER_SECRET` | 覆盖 secret；wire token 仍按 `sha1(secret+"LivaChat")` 派生 |
| `LIVACHAT_SERVER_TOKEN` | 直接覆盖 wire token（Mock Server 用 `dev-token` 时） |
| `LIVACHAT_DEVICE_ID` | 覆盖 deviceID（便于服务端日志比对） |
| `LIVACHAT_TLS_ALLOW_SELF_SIGNED` | `1` → `.allowSelfSigned` |

- 仅 `#if DEBUG` 读取；Release 忽略。
- Mock Server 默认 token 为 `dev-token`，本地启动时把 `MOCK_DEV_TOKEN` 设为测试 secret 的 wire token，或使用 `LIVACHAT_SERVER_TOKEN=dev-token`。
- 环境变量由 Xcode scheme / `simctl spawn` / `devicectl` 注入；不写入 UserDefaults。
- 覆盖存在时，App 视为“配置完整”，不强制去 Settings；Settings 仍显示已保存值。
- **真实 server 使用 `wss://`；Mock Server 使用明文 `ws://`（固定端口 18080）**。产品 Settings 只接受 `host:port` 并固定生成 `https/wss`，Mock Server 的 `ws/http` 不能通过 Settings 检测或连接，必须使用本节 DEBUG 环境变量。

---

## 8. 保存与生效流程

```
用户编辑 address/secret
  → 校验
  → [检测服务]（可选）GET /healthz_token
  → [保存] 仅在 dirty 时出现
  → 确认弹窗：应用并重新连接 / 取消
  → 写 UserDefaults + Keychain（secret/wire token）+ TLS 开关
  → AppContainer.stopRuntime()
  → 清空 serverSessionID / messages / 临时文本 / 播放 epoch
  → 重建 ConversationRuntime（新 SDK、新 playbackEpoch=1）
  → 返回 Conversation，自动 connect
```

- 取消确认：不写存储、不重建、不停止当前 runtime。
- 保存失败（Keychain/IO）：保留原配置与 runtime，显示 error banner。
- 首次配置缺失时，Conversation 只显示“去设置”阻断提示，不构造 SDK。
- 地址/secret 未变化时保存按钮禁用；仅触觉开关变化不触发重建。

---

## 9. 测试

| 测试 | 覆盖 |
|---|---|
| `ServerAddressTests` | IPv4/域名/IPv6；非法 port/path/scheme；URL 拼接 |
| `WireTokenTests` | 已知向量；空串/Unicode |
| `CredentialStoreTests` | 内存 fake；缺省/覆盖/删除；deviceID 生成与持久化 |
| `SettingsStoreTests` | 保存/读取；缺失 secret 返回 nil；首启标记；触觉持久化 |
| `ServerConfigValidatorTests` | 空值、无效地址、未变化判断 |
| `ServerProbeTests` | URLProtocol stub：200/401/500/超时/TLS 错误；query 编码 |
| `SettingsViewModelTests` | dirty 状态、probe 三态、保存确认、取消不重建 |
| Keychain 真机/模拟器冒烟 | 写入/读取/重启后保留（M1.5） |

---

## 10. 不做

- 不复制 Android 开发服务器预设；
- 不提供设备选择器、语言选择；
- TLS 仅提供“允许自签名证书”开关（默认开启），不做证书固定/证书选择；
- 不保存聊天历史；
- 不在设置页显示隐私政策/用户协议（URL 未定）；
- 不支持后台刷新配置。
