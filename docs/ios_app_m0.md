# LivaChat iOS App M0 分析与设计：工程骨架

**状态**：已实施（模拟器与 iPad 真机通过；iPhone 11 待 Xcode 26.x）
**日期**：2026-10-20
**上游文档**：`docs/ios_app_arch.md`、`docs/ios_sdk_design.md`、`docs/ios_dev_rules.md`
**范围**：`apps/ios/LivaChatApp` 工程骨架与 SDK 链接验证；不实现任何产品功能

---

## 1. M0 目标与边界

### 1.1 目标

创建 `apps/ios/LivaChatApp` Xcode 工程，使 App 能够：

1. 在 iOS 模拟器上构建、安装、启动；
2. 在真机上构建、安装、启动；
3. 链接并初始化 `apps/ios/LivaChatSDK`（含 native Opus/VAD）；
4. 在启动页展示 SDK 初始化结果。

### 1.2 “跑起来”的判定

M0 的“跑起来”定义为：

- App 进程正常启动，无崩溃；
- `LivaChatSDK` 成功构造；
- 页面显示 SDK 初始化成功（或明确错误）；
- **不包含**连接服务器、创建会话、音频采集/播放、权限申请、摄像头、设置、持久化。

### 1.3 非目标

M0 明确不做：

- 不调用 `sdk.connect()` / `startSession()`；
- 不申请麦克风、摄像头、本地网络权限；
- 不实现 Onboarding、Conversation、Settings、History；
- 不实现 AppRouter、AppLifecycleController、媒体控制器、数据层；
- 不做后台音频、ReplayKit、PHPicker、Keychain；
- 不做视觉设计、正式图标、App Store 配置；
- 不修改 `LivaChatSDK` 或 `packages/sdk-swift`；
- 不接 CI（构建命令可在 M0 文档中给出，CI 接入另议）。

---

## 2. 现状分析

### 2.1 仓库现状

| 项 | 状态 |
|---|---|
| `apps/ios/LivaChatSDK` | 已存在，Swift package，含 SDK 源码、C shim、native XCFramework 依赖与测试 |
| `packages/sdk-swift` | 已存在，`LivaChatProtocol` 生成 DTO |
| `3rdparty/dist-ios/Opus.xcframework` | 已存在，约 2.2MB |
| `3rdparty/dist-ios/WebRtcVadLite.xcframework` | 已存在，约 148KB |
| `apps/ios/LivaChatApp` | **不存在**（`apps/ios` 目前只有 SDK 与 `.gitkeep`） |
| `scripts/build-ios.sh` | 支持 `sdk` / `app` / `all`；`sdk` 为 DTO 校验与 SDK 测试，`app` 为签名 App 构建（需 `IOS_DEVICE_UDID` / `IOS_DEVELOPMENT_TEAM`） |
| App Xcode 工程 / scheme | 不存在 |

### 2.2 工具链与环境（本机实测）

| 项 | 结果 |
|---|---|
| Xcode | 16.4（Build 16F6），iPhoneOS SDK 18.5 |
| Swift | Swift 6 工具链（SDK package 为 `swift-tools-version: 6.0`） |
| 可用模拟器 runtime | iOS 17.0、iOS 18.6 |
| 不可用 runtime | 本机无 iOS 15/16 runtime |
| 签名 | 已安装 Apple Development 开发证书 |
| 真机（M0 验收） | iPad Pro 10.5 英寸（iPad7,4），iPadOS **17.7.5**（21H420）；已配对、Developer Mode enabled、DDI available；App 以 iPhone 兼容模式运行 |
| 真机（iPhone 11） | 当前未连接；此前为 iOS **26.6**，与 Xcode 16.4（iOS 18.5 SDK）不兼容，未用于 M0 验收 |
| Provisioning profile | 使用 Team `<TEAM_ID>` 的 wildcard iOS Team Provisioning Profile（自动签名） |

### 2.3 结论

M0 是 **App 侧的 greenfield 工程搭建 + SDK 链接验证**。SDK 与协议层已具备，不需要在 M0 改动 SDK 代码；主要风险集中在 Xcode 工程配置、本地 SPM 路径、native XCFramework 链接、签名与真机可用性。

当前结论：

- 模拟器（iOS 17.0 / 18.6）构建、启动、SDK 初始化均通过；
- iPad Pro 10.5（iPadOS 17.7.5）已完成配对、Developer Mode、DDI、签名、安装、启动与设备端单测；
- iPhone 11（iOS 26.6）与 Xcode 16.4 不兼容，未用于 M0；需要 Xcode 26.x 才能验证；
- 签名构建必须在 GUI Terminal 或 Xcode 会话执行；Claude Code shell 无法访问钥匙串（`errSecInternalComponent`）。

---

## 3. 关键设计决策

| # | 决策 | 结论 |
|---|---|---|
| D1 | 工程形式 | 标准 Xcode `.xcodeproj`（Xcode 16.4 iOS App 模板）；不引入 XcodeGen/Tuist |
| D2 | 依赖方式 | App 通过本地 SPM 依赖 `apps/ios/LivaChatSDK`，只链接 `LivaChatSDK` product；不直接链接 Opus/VAD XCFramework |
| D3 | 最低版本 / 语言 | `IPHONEOS_DEPLOYMENT_TARGET = 15.0`；`SWIFT_VERSION = 6.0` |
| D4 | 设备家族 | M0 target 为 iPhone only（`TARGETED_DEVICE_FAMILY = 1`）、竖屏；真机验收设备 iPad 以 iPhone 兼容模式运行通过。正式 iPad 支持另立需求 |
| D5 | Bundle ID | 默认 `dev.livachat.app`（与 Android applicationId 对齐）；若 App ID 冲突则用 `dev.livachat.app.ios`，并在实现记录中注明 |
| D6 | SDK 验证方式 | 构造一个带占位配置的 `LivaChatSDK`，**不 connect**；无网络、无权限、无音频会话 |
| D7 | App 结构 | 只建 App 入口、`AppContainer`、M0 View/ViewModel、SDK Probe；不建空壳 Router/Lifecycle/Feature 模块 |
| D8 | 测试 | 新增最小 XCTest 单元测试（SDK 初始化）+ 最小 XCUITest（启动、状态断言、截图附件）；不做完整 UI 流程自动化 |
| D9 | 签名 | Automatic signing；开发团队在本机 Xcode 中选择，不把个人证书/私钥入库 |
| D10 | Info.plist | 使用 Xcode 生成的 Info.plist；M0 不添加任何权限、后台模式、ATS 例外 |
| D11 | SDK 代码 | M0 零改动；若链接失败，修 Xcode/构建设置或 SDK 包路径，不顺手改 SDK |
| D12 | 构建入口 | 统一使用 `scripts/build-ios.sh [sdk|app|all]`；默认 `sdk`，`app` 执行签名构建（需 `IOS_DEVICE_UDID` / `IOS_DEVELOPMENT_TEAM`） |

---

## 4. 工程结构

M0 只创建最小文件集；`Features/` 下的正式模块、`Domain/`、`Media/`、`Data/` 等在 M1 以后按模块文档逐步创建。

```
apps/ios/LivaChatApp/
├── LivaChatApp.xcodeproj/
│   └── xcshareddata/xcschemes/LivaChatApp.xcscheme   # 共享 scheme，供 xcodebuild 使用
├── App/
│   ├── LivaChatApp.swift          # @main，App 入口
│   ├── AppContainer.swift         # M0 组合根（仅持有 M0 ViewModel）
│   └── M0SDKProbe.swift           # SDK 链接/初始化探针（M1 可删除）
├── Features/
│   └── M0/
│       ├── M0View.swift           # 启动页/状态页
│       └── M0ViewModel.swift      # @MainActor，驱动探针
├── Resources/
│   └── Assets.xcassets/           # 模板占位 AppIcon，无视觉设计
├── LivaChatAppTests/
│   └── M0SDKLinkageTests.swift    # SDK 初始化 smoke test
├── LivaChatAppUITests/
│   └── M0ScreenshotUITests.swift  # 启动 App、断言状态并保存截图
└── .gitignore                     # Xcode 本地文件
```

### 4.1 文件职责

| 文件 | 职责 |
|---|---|
| `LivaChatApp.swift` | `@main` SwiftUI App；创建 `AppContainer` 并渲染 `M0View` |
| `AppContainer.swift` | 组合根；当前只持有 `M0ViewModel`；M1 替换为正式 runtime |
| `M0SDKProbe.swift` | 构造占位 `LivaChatConfig` + `LivaChatSDK`；持有 SDK 与 no-op sink；返回状态 |
| `M0ViewModel.swift` | `@MainActor ObservableObject`，启动一次探针并发布状态 |
| `M0View.swift` | 展示 App 名称、版本、SDK 初始化状态；无业务交互 |
| `M0SDKLinkageTests.swift` | 在测试宿主中构造 SDK，验证链接与 native 初始化 |
| `M0ScreenshotUITests.swift` | 启动 App、等待 `m0_sdk_status`、断言 “SDK 初始化成功”，保存 `XCUIScreen` 截图附件 |

---

## 5. Xcode 工程与构建配置

### 5.1 创建方式

使用 Xcode 16.4 创建标准 `.xcodeproj`（GUI 模板或等价的标准工程结构；不引入 XcodeGen/Tuist）。推荐的模板步骤：

1. File → New → Project → iOS → App；
2. Product Name：`LivaChatApp`；
3. Team：本机 Apple Development 团队；
4. Organization Identifier：`dev.livachat`；
5. Interface：SwiftUI；Language：Swift；
6. 勾选 Include Tests 创建单元测试 target；另新增 UI test target `LivaChatAppUITests`（M0 截图验证用）；
7. 存储到 `apps/ios/LivaChatApp`；
8. 删除模板 `ContentView.swift`，替换为 M0 文件；
9. 将 scheme 设为 Shared（`xcshareddata/xcschemes`）。

不引入 XcodeGen、Tuist、CocoaPods。

### 5.2 关键 build settings

| Setting | 值 |
|---|---|
| `PRODUCT_NAME` | `LivaChatApp` |
| `PRODUCT_BUNDLE_IDENTIFIER` | `dev.livachat.app`（冲突时 `dev.livachat.app.ios`） |
| `IPHONEOS_DEPLOYMENT_TARGET` | `15.0` |
| `SWIFT_VERSION` | `6.0` |
| `TARGETED_DEVICE_FAMILY` | `1`（iPhone） |
| `SUPPORTED_PLATFORMS` | `iphoneos iphonesimulator` |
| `SUPPORTS_MACCATALYST` | `NO` |
| `CODE_SIGN_STYLE` | `Automatic` |
| `DEVELOPMENT_TEAM` | 本机 Xcode 选择（不入库证书/私钥） |
| `GENERATE_INFOPLIST_FILE` | `YES` |
| `INFOPLIST_KEY_CFBundleDisplayName` | `LivaChat` |
| `INFOPLIST_KEY_UILaunchScreen_Generation` | `YES` |
| `INFOPLIST_KEY_UIApplicationSceneManifest_Generation` | `YES` |
| `INFOPLIST_KEY_UISupportedInterfaceOrientations` | `UIInterfaceOrientationPortrait` |
| `MARKETING_VERSION` | `1.0.0` |
| `CURRENT_PROJECT_VERSION` | `1` |
| `ASSETCATALOG_COMPILER_APPICON_NAME` | `AppIcon` |
| `ENABLE_PREVIEWS` | `YES` |

M0 不配置：

- `NSMicrophoneUsageDescription` / `NSCameraUsageDescription`；
- `NSLocalNetworkUsageDescription`；
- `UIBackgroundModes`；
- `NSAppTransportSecurity`；
- Entitlements / Keychain / Push。

### 5.3 本地 SPM 集成

1. Xcode → File → Add Package Dependencies → Add Local；
2. 选择 `apps/ios/LivaChatSDK`；
3. 将 `LivaChatSDK` product 加入 App target；
4. 不手动添加 `Opus.xcframework` / `WebRtcVadLite.xcframework`；
5. 执行 `xcodebuild -resolvePackageDependencies` 验证路径。

注意：

- `LivaChatSDK` 包内的 `../../../packages/sdk-swift` 与 `../../../3rdparty/dist-ios` 依赖相对仓库根解析；工程必须保持在 monorepo 原位。
- `3rdparty/dist-ios` 两个 XCFramework 已入库；CI 检出必须包含它们。
- `Package.resolved` 若由 Xcode 生成，提交到仓库以保证解析状态可复现。

### 5.4 Bundle ID 与签名

- 默认 `dev.livachat.app`；首次构建时 Xcode 自动签名会创建 App ID 与 provisioning profile。
- 若该 Bundle ID 已被占用（自动签名报错），改用 `dev.livachat.app.ios`，并在实现记录中注明原因；不要同时保留两个有效配置。
- 真机（iPad Pro 10.5 / iPadOS 17.7.5）需要：设备连接、解锁、在 Xcode → Devices and Simulators 完成配对/信任、开启 Developer Mode；Xcode 16.4 支持该 OS。
- 签名构建必须在 GUI Terminal 或 Xcode 会话执行；Claude Code shell 无法访问钥匙串。
- iPhone 11（iOS 26.6）需要 Xcode 26.x，当前 Xcode 16.4 不支持；未用于 M0 验收。
- 模拟器不需要签名；真机验收是 M0 的硬性条件之一。

### 5.5 `.gitignore`

App 目录至少忽略：

```
xcuserdata/
*.xcuserstate
DerivedData/
build/
.build/
```

`.xcodeproj`、共享 scheme、`Package.resolved`（如生成）、`Assets.xcassets` 入库。

---

## 6. SDK 启动探针设计

### 6.1 目的

用最小代码验证四件事：

1. App target 能编译并链接 `LivaChatSDK`；
2. 本地 SPM 的传递依赖（`LivaChatProtocol`）可解析；
3. native Opus/VAD XCFramework 在模拟器和真机架构上可加载；
4. `LivaChatSDK` 公开初始化接口与 `LivaChatAudioSink` 协议在 App 侧可用。

**不发起连接**，因此不会产生网络请求、权限申请或音频会话。

### 6.2 占位配置

```swift
let config = LivaChatConfig(
    serverURL: URL(string: "wss://127.0.0.1:1/v1/ws/stream")!,
    token: "m0-placeholder-token",
    deviceID: UUID().uuidString,
    clientVersion: clientVersion()
)
```

- `serverURL` 只用于通过构造期校验；M0 不调用 `connect()`。
- `token` / `deviceID` 只满足非空校验；不持久化、不写入日志、不具备真实鉴权意义。
- `clientVersion` 按 SDK 约定生成：`ios-<CFBundleShortVersionString>.<CFBundleVersion>`。
- 其他参数使用 SDK 默认值（16kHz、20ms、q70、TLS `.system` 等）。

### 6.3 初始化与状态

```swift
@MainActor
final class M0SDKProbe {
    enum Status: Equatable {
        case initializing
        case ready
        case failed(String)
    }

    private let sink = NoopAudioSink()
    private var sdk: LivaChatSDK?

    func run() -> Status {
        do {
            let sdk = try LivaChatSDK(config: makePlaceholderConfig(), audioSink: sink)
            self.sdk = sdk
            return .ready
        } catch {
            return .failed(String(describing: error))
        }
    }
}

final class NoopAudioSink: LivaChatAudioSink, @unchecked Sendable {
    func livaChat(_ sdk: LivaChatSDK, didReceiveAudio frame: AudioPCMFrame) {}
}
```

- `M0SDKProbe` 强持有 SDK 与 sink；SDK 对 sink 是弱引用，不能只传局部变量。
- `makePlaceholderConfig()` 封装 §6.2 的配置；`clientVersion()` 返回 `ios-<CFBundleShortVersionString>.<CFBundleVersion>`。
- SDK 构造成功后，`sdk.state.connectionState` 应为 `.disconnected`；M0 不读取/展示其他状态。
- 若构造失败，`M0View` 展示错误文本，不崩溃。

### 6.4 启动流程

```
@main LivaChatApp
  → AppContainer
      → M0ViewModel
          → M0View.task
              → M0SDKProbe.run()
                  → LivaChatSDK(config:audioSink:)
                  → status = .ready / .failed
              → M0View 更新
```

- `M0ViewModel` 使用 `@MainActor`；探针只运行一次（幂等）。
- SDK 构造是同步调用；若在低端真机上出现明显主线程阻塞，M0 可把 `run()` 投递到后台队列再回主线程发布状态，但先不引入额外并发结构。
- M0 不注册 `scenePhase`、不启动音频会话、不创建后台任务。

---

## 7. 构建与运行

### 7.1 模拟器

可用 runtime：iOS 17.0、iOS 18.6。M0 使用 iPhone 16 Pro（iOS 18.6）作为主要模拟器验收设备。

```bash
xcodebuild -resolvePackageDependencies \
  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
  -scheme LivaChatApp

xcodebuild build \
  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
  -scheme LivaChatApp \
  -configuration Debug \
  -derivedDataPath apps/ios/LivaChatApp/build \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro,OS=18.6'

xcrun simctl boot "iPhone 16 Pro"
xcrun simctl install booted apps/ios/LivaChatApp/build/Build/Products/Debug-iphonesimulator/LivaChatApp.app
xcrun simctl launch booted dev.livachat.app
```

验收：模拟器启动后展示 SDK 初始化成功；无权限弹窗、无网络请求。

### 7.2 真机（iPad Pro 10.5 / iPadOS 17.7.5）

设备信息：

- 型号：iPad Pro 10.5 英寸（iPad7,4）
- OS：iPadOS 17.7.5（21H420）
- xcodebuild UDID：`<IPAD_UDID>`
- devicectl identifier：`<DEVICE_ID>`
- 已配对、Developer Mode enabled、DDI available

构建（**必须在 GUI Terminal 或 Xcode 中执行**；签名需要钥匙串交互）：

```bash
IOS_DEVICE_UDID=YOUR_DEVICE_UDID \
IOS_DEVELOPMENT_TEAM=YOUR_TEAM_ID \
./scripts/build-ios.sh app
```

将 `YOUR_DEVICE_UDID`、`YOUR_TEAM_ID`、`YOUR_DEVICE_ID` 替换为本机设备与签名 team（`xcrun devicectl list devices` 可查设备 ID）。

等价命令：

```bash
xcodebuild build \
  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
  -scheme LivaChatApp \
  -configuration Debug \
  -derivedDataPath apps/ios/LivaChatApp/build/device-signed \
  -destination 'platform=iOS,id=YOUR_DEVICE_UDID' \
  DEVELOPMENT_TEAM=YOUR_TEAM_ID \
  -allowProvisioningUpdates
```

安装/启动：

```bash
xcrun devicectl device install app \
  --device YOUR_DEVICE_ID \
  apps/ios/LivaChatApp/build/device-signed/Build/Products/Debug-iphoneos/LivaChatApp.app

xcrun devicectl device process launch \
  --device YOUR_DEVICE_ID \
  dev.livachat.app
```

**当前状态**：已签名、安装、启动成功；iPad 上 `M0SDKLinkageTests` 与 `M0ScreenshotUITests` 均 1/1 通过。设备截图由 XCUITest 的 `XCUIScreen` 附件导出：`/tmp/livachat_m0_ipad.png`。

iPhone 11（iOS 26.6）需要 Xcode 26.x，当前 Xcode 16.4 不支持，未用于 M0。

### 7.3 构建配置核验

```bash
xcodebuild -showBuildSettings \
  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
  -scheme LivaChatApp | grep -E \
  'IPHONEOS_DEPLOYMENT_TARGET|SWIFT_VERSION|PRODUCT_BUNDLE_IDENTIFIER|TARGETED_DEVICE_FAMILY'
```

期望：`15.0`、`6.0`、`dev.livachat.app`、`1`。

---

## 8. 测试与验收

### 8.1 单元测试

最小测试目标 `LivaChatAppTests`。使用模板默认的 App-hosted 单元测试 target（测试宿主为 `LivaChatApp`），确保 `LivaChatSDK` 已随 App target 链接；测试不直接 `import LivaChatSDK`。

```swift
import XCTest
@testable import LivaChatApp

@MainActor
final class M0SDKLinkageTests: XCTestCase {
    func testSDKInitializesWithPlaceholderConfig() {
        XCTAssertEqual(M0SDKProbe().run(), .ready)
    }
}
```

目的：在测试宿主中验证 SDK 链接、native 初始化与公开 API 可用。

运行：

```bash
xcodebuild test \
  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
  -scheme LivaChatApp \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro,OS=18.6'
```

### 8.1.1 UI 测试（截图）

`LivaChatAppUITests/M0ScreenshotUITests.swift` 启动 App，等待 `m0_sdk_status`，断言状态文案为 “SDK 初始化成功”，并将 `XCUIScreen.main.screenshot()` 作为测试附件保存：

```swift
@MainActor
final class M0ScreenshotUITests: XCTestCase {
    func testM0SDKReadyAndScreenshot() {
        let app = XCUIApplication()
        app.launch()

        let status = app.staticTexts["m0_sdk_status"]
        XCTAssertTrue(status.waitForExistence(timeout: 10))
        XCTAssertEqual(status.label, "SDK 初始化成功")

        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "M0 SDK ready"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
```

运行：

```bash
xcodebuild test \
  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
  -scheme LivaChatApp \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro,OS=18.6' \
  -only-testing:LivaChatAppUITests
```

导出截图附件：

```bash
xcrun xcresulttool export attachments \
  --path <Test-*.xcresult> \
  --output-path /tmp/livachat_ui_attachments
```

### 8.2 验收矩阵

| 项 | 通过条件 | 结果（2026-10-20） |
|---|---|---|
| Xcode 工程 | 可在 Xcode 16.4 打开、解析本地包、无红色错误 | 通过（`xcodebuild -list` 解析成功） |
| 模拟器构建 | iPhone 16 Pro / iOS 18.6 build 通过 | 通过（BUILD SUCCEEDED） |
| 模拟器启动 | App 启动，显示 SDK 初始化成功 | 通过（XCUITest 断言 + 截图） |
| 真机构建 | iPad Pro 10.5 / iPadOS 17.7.5 完成配对与 Developer Mode 后，signed build + install 通过 | 通过（Team `<TEAM_ID>`；安装成功） |
| 真机启动 | App 启动，SDK 初始化成功 | 通过（devicectl launch + XCUITest 断言 + 截图） |
| 真机测试 | `M0SDKLinkageTests` 在 iPad 上通过 | 通过（1/1，TEST SUCCEEDED） |
| 真机 UI 测试 | `M0ScreenshotUITests` 在 iPad 上通过并生成截图附件 | 通过（1/1，截图已导出） |
| 单元测试 | `M0SDKLinkageTests` 通过 | 通过（模拟器 + iPad 均 1/1） |
| 配置核验 | 部署目标 15.0、Swift 6、iPhone only、Bundle ID 正确 | 通过 |
| 范围核验 | 无网络、无权限、无音频/摄像头/持久化代码 | 通过 |
| 依赖核验 | 无 CocoaPods；App 不直接引用 Opus/VAD XCFramework | 通过（仅经 `LivaChatSDK` 传递链接） |

### 8.3 证据

已保存/可复现的证据：

- 模拟器启动截图：`/tmp/livachat_m0_simulator.png`（iPhone 16 Pro / iOS 18.6）与 `/tmp/livachat_m0_simulator_ios17.png`（iPhone 15 Pro / iOS 17.0），页面均显示 “SDK 初始化成功”；
- 模拟器构建日志：`/tmp/livachat_m0_build.log`；
- 单元测试日志：`/tmp/livachat_m0_test.log`（1 test / 0 failures）；
- 设备架构编译日志：`/tmp/livachat_m0_device_build.log`（arm64，`CODE_SIGNING_ALLOWED=NO`，BUILD SUCCEEDED）；
- iPad 构建/测试日志：`/tmp/livachat_ipad_test.log`（signed build + device test 1/1，TEST SUCCEEDED）；
- iPad 安装/启动：`devicectl` 安装 `dev.livachat.app` 成功；启动成功，并由 XCUITest 再次启动并断言；
- 签名：Apple Development（Team `<TEAM_ID>`），wildcard profile；`codesign -dv` 校验通过；
- `-showBuildSettings`：`IPHONEOS_DEPLOYMENT_TARGET=15.0`、`SWIFT_VERSION=6.0`、`PRODUCT_BUNDLE_IDENTIFIER=dev.livachat.app`、`TARGETED_DEVICE_FAMILY=1`；
- UI 测试日志（模拟器）：`/tmp/livachat_m0_uitest_sim2.log`（1/1，TEST SUCCEEDED）；
- UI 测试日志（iPad）：`/tmp/livachat_ipad_uitest.log`（1/1，TEST SUCCEEDED）；
- 模拟器 UI 测试截图：`/tmp/livachat_m0_simulator_uitest.png`；
- iPad UI 测试截图：`/tmp/livachat_m0_ipad.png`（iPhone 兼容模式，页面显示 “SDK 初始化成功”）；
- 如后续 SDK 初始化失败，保留错误文本与修复记录。

---

## 9. 风险与依赖

| 风险/依赖 | 影响 | 处理 |
|---|---|---|
| 真机设备差异 | iPhone 11（iOS 26.6）与 Xcode 16.4（iOS 18.5 SDK）不兼容 | M0 改用 iPad Pro 10.5 / iPadOS 17.7.5 完成真机验收；iPhone 11 待 Xcode 26.x |
| 签名钥匙串会话 | Claude Code shell 无钥匙串交互权限，`codesign` 返回 `errSecInternalComponent` | 在 GUI Terminal 或 Xcode 中设置 `IOS_DEVICE_UDID` / `IOS_DEVELOPMENT_TEAM` 后执行 `./scripts/build-ios.sh app`；不要在 Claude shell 内做签名构建 |
| 无 iOS 15/16 模拟器 runtime | 无法在本机做 iOS 15/16 运行时验证 | M0 只验证 iOS 15 部署目标可编译；运行时用 iOS 17/18 模拟器；iOS 15/16 真机冒烟按 SDK 文档 D15 后续补 |
| Bundle ID 被占用 | 自动签名失败 | 改用 `dev.livachat.app.ios` 并记录 |
| 开发者账号能力不足 | 免费/受限团队可能无法自动创建 App ID 或进行真机调试 | 使用已注册 App ID/付费团队；必要时手动创建 App ID 与 profile |
| 本地 SPM 相对路径解析失败 | App 无法链接 SDK | 保持 monorepo 原位；检查 `LivaChatSDK/Package.swift` 相对路径；检查 Xcode 本地包引用 |
| XCFramework 架构缺失 | 模拟器/真机初始化失败 | 检查 `3rdparty/dist-ios` 两个 XCFramework 的切片；SDK 测试已覆盖 native 初始化，若 App 失败优先查链接配置 |
| Swift 6 严格并发 | 简单代码也可能出现 concurrency 编译错误 | M0 所有 UI/状态类标 `@MainActor`；保持最小代码 |
| Xcode 工程文件冲突 | 多人同时改 `.pbxproj` 易冲突 | M0 单次创建；后续修改尽量按文件/配置拆分 |
| SDK 构造阻塞主线程 | 启动页短暂停顿 | 先接受；若真机明显卡顿，改为后台构造后回主线程发布状态 |
| 签名团队/证书差异 | 其他开发者无法直接真机运行 | 各自在 Xcode 选择团队；证书/私钥不入库 |

---

## 10. 实施步骤

1. 用 Xcode 16.4 创建 `apps/ios/LivaChatApp`（iOS App / SwiftUI / Swift / Include Tests）。
2. 设置 Deployment Target 15.0、Swift 6、iPhone only、竖屏、Bundle ID。
3. 删除模板 `ContentView`，加入 M0 文件。
4. 添加本地 SPM 依赖 `../LivaChatSDK`，链接 `LivaChatSDK` product。
5. 设置本机签名团队与 Automatic signing。
6. 创建最小 `Assets.xcassets`（AppIcon 占位即可）。
7. 添加 `.gitignore`，共享 scheme。
8. 解析包依赖并在模拟器构建。
9. 在模拟器安装/启动，确认 SDK 初始化成功。
10. 运行 `M0SDKLinkageTests`。
11. 在 GUI Terminal 或 Xcode 中配对 iPad（iPadOS 17.7.5），设置 `IOS_DEVICE_UDID` / `IOS_DEVELOPMENT_TEAM` 后执行 `./scripts/build-ios.sh app` 签名构建，使用 `devicectl` 安装/启动，并运行设备端 M0 单测与 UI 测试；iPhone 11（iOS 26.6）待 Xcode 26.x。
12. 运行 `M0ScreenshotUITests`（模拟器 + iPad），用 `xcresulttool export attachments` 导出截图；保存构建/测试日志与 build settings 输出。
13. `make build-ios` 仍默认执行 `scripts/build-ios.sh sdk`；App 构建使用 `scripts/build-ios.sh app`（需设置 `IOS_DEVICE_UDID` / `IOS_DEVELOPMENT_TEAM`）。
14. 提交 M0 工程与文档；记录 Bundle ID/团队/真机验证结果。

---

## 11. M0 完成标准

- [x] `apps/ios/LivaChatApp/LivaChatApp.xcodeproj` 存在且可打开；
- [x] 本地 SPM 依赖 `LivaChatSDK` 解析成功；
- [x] 模拟器构建、启动成功，页面显示 SDK 初始化成功；
- [x] 真机构建、安装、启动成功，页面显示 SDK 初始化成功（iPad Pro 10.5 / iPadOS 17.7.5；设备单测 + UI 测试断言 + 截图）；
- [x] `M0SDKLinkageTests` 通过（模拟器 + iPad 均 1/1）；
- [x] 部署目标 15.0、Swift 6、iPhone only；
- [x] 无网络请求、无权限弹窗、无音频/摄像头/持久化代码；
- [x] App 未直接引用 Opus/VAD XCFramework（仅经 `LivaChatSDK` 传递链接）；无 CocoaPods；
- [x] 截图、测试日志与 build settings 证据已保存（模拟器 + iPad UI 测试截图均已导出）；
- [x] 未修改 SDK、协议 DTO 或服务端。

> M0 真机验收使用 **iPad Pro 10.5 / iPadOS 17.7.5**（已配对、Developer Mode、DDI、签名、安装、启动、设备单测、UI 测试与截图均通过）。iPhone 11（iOS 26.6）需要 Xcode 26.x，未用于 M0；签名构建需在 GUI Terminal/Xcode 会话执行。

---

## 12. 与 M1 的衔接

M0 完成后，M1 在现有骨架上替换 `M0SDKProbe` 为正式的 `ConversationRuntime`（SDK + EventAdapter + Coordinator）。完整目标、UI/UX 与测试计划见 `docs/ios_app_m1.md`。M1 开始时：

1. Onboarding/权限；
2. 设置与 Keychain（服务器地址/密钥/deviceID）；
3. 音频采集/播放与 `playbackEpoch`；
4. `connectionEpochAdvanced` → `startSession`；
5. Mock Server 上的前台语音闭环。

M0 的 `AppContainer`、目录边界和 SDK 链接方式应保持，使 M1 不需要重做工程配置。

---

## 13. 实施记录（2026-10-20）

### 13.1 已落地

- `apps/ios/LivaChatApp/LivaChatApp.xcodeproj`：标准 Xcode 工程（手写 `project.pbxproj`，未使用 XcodeGen/Tuist）；Xcode 16.4 可解析。
- 本地 SPM 引用 `../LivaChatSDK`；App target 链接 `LivaChatSDK` product，未直接引用 Opus/VAD XCFramework。
- App 入口、`AppContainer`、`M0SDKProbe`、`M0View`、`M0ViewModel`、no-op `LivaChatAudioSink`。
- 最小 `Assets.xcassets`（1024×1024 占位 AppIcon）与 `.gitignore`。
- 共享 scheme `LivaChatApp`；`LivaChatAppTests` 为 App-hosted 单元测试 target。
- `LivaChatAppUITests`：`M0ScreenshotUITests` 启动 App、断言 `m0_sdk_status`，保存截图附件。
- 构建配置：iOS 15.0、Swift 6、iPhone only、`dev.livachat.app`、`SWIFT_STRICT_CONCURRENCY=complete`。
- `scripts/build-ios.sh`：新增 `app` 模式（GUI Terminal 签名构建）；`app` 模式要求显式传入 `IOS_DEVELOPMENT_TEAM` 与 `IOS_DEVICE_UDID`，另可用 `IOS_DERIVED_DATA_PATH` 覆盖；`sdk` 模式保留原 SDK 测试流程。
- 根目录旧 `mybuild.sh` 已合并进该脚本并移除（临时备份 `/tmp/mybuild.sh.m0.bak`）。

### 13.2 验证结果

| 项 | 结果 |
|---|---|
| 工程解析 | `xcodebuild -list` 成功，本地包 `LivaChatSDK` / `LivaChatProtocol` 解析成功 |
| `scripts/build-ios.sh sdk` | `RUN_IOS_TESTS=0`：DTO 校验、协议测试、SDK host 测试 66/66 通过 |
| `scripts/build-ios.sh app` | GUI Terminal：签名构建 `BUILD SUCCEEDED`，exit 0 |
| 模拟器构建 | iPhone 16 Pro / iOS 18.6：`BUILD SUCCEEDED` |
| 模拟器启动（iOS 18.6） | 安装/启动成功，页面显示 “SDK 初始化成功” |
| 模拟器启动（iOS 17.0） | iPhone 15 Pro / iOS 17.0：安装/启动成功，页面显示 “SDK 初始化成功” |
| iPad 签名构建 | Apple Development（Team `<TEAM_ID>`）签名成功，`codesign -dv` 校验通过 |
| iPad 安装/启动 | `devicectl` 安装/启动成功；XCUITest 再次启动并断言 |
| iPad 设备测试 | `M0SDKLinkageTests` 1/1 通过，`TEST SUCCEEDED` |
| 模拟器 UI 测试 | `M0ScreenshotUITests` 1/1 通过，截图附件已导出 |
| iPad UI 测试 | `M0ScreenshotUITests` 1/1 通过，设备截图附件已导出 |
| 单元测试 | 模拟器 + iPad 均 1 test / 0 failures |
| 设备架构编译 | `generic/platform=iOS` arm64，`CODE_SIGNING_ALLOWED=NO`：`BUILD SUCCEEDED` |
| 关键 build settings | `IPHONEOS_DEPLOYMENT_TARGET=15.0`、`SWIFT_VERSION=6.0`、`PRODUCT_BUNDLE_IDENTIFIER=dev.livachat.app`、`TARGETED_DEVICE_FAMILY=1` |
| 范围 | 未调用 `connect()`；无网络请求、无权限、无音频会话/摄像头/持久化代码 |
| 改动范围 | 仅新增 App 工程、扩展 `scripts/build-ios.sh` 与文档；未修改 SDK、协议 DTO、服务端 |

### 13.3 真机状态

- 当前真机：iPad Pro 10.5 英寸（iPad7,4），iPadOS 17.7.5（21H420）；已配对、Developer Mode enabled、DDI available。
- 签名：Apple Development（Team `<TEAM_ID>`），wildcard provisioning profile；签名构建在 GUI Terminal 中执行（Claude Code shell 无钥匙串交互权限）。
- 安装/启动：`devicectl` 安装 `dev.livachat.app` 成功；`process launch` 成功；XCUITest 再次启动并断言通过。
- 设备测试：iPad 上 `M0SDKLinkageTests` 1/1 通过，证明设备端 SDK 初始化成功。
- 设备 UI 测试：iPad 上 `M0ScreenshotUITests` 1/1 通过；截图由 `XCUIScreen` 附件导出到 `/tmp/livachat_m0_ipad.png`（iPhone 兼容模式）。
- iPhone 11（iOS 26.6）：与 Xcode 16.4 不兼容，未用于 M0；待 Xcode 26.x 验证。

### 13.4 证据路径

- `/tmp/livachat_m0_build.log`
- `/tmp/livachat_m0_test.log`
- `/tmp/livachat_m0_device_build.log`
- `/tmp/livachat_ipad_test.log`（iPad 签名构建 + 设备测试）
- `/tmp/livachat_build_sdk_mode.log`（`scripts/build-ios.sh sdk`，RUN_IOS_TESTS=0）
- `/tmp/livachat_build_app_mode.log`（`scripts/build-ios.sh app`，BUILD SUCCEEDED）
- `/tmp/livachat_m0_uitest_sim2.log`（模拟器 UI 测试）
- `/tmp/livachat_ipad_uitest.log`（iPad UI 测试）
- `/tmp/livachat_terminal_build.log`（早期 GUI Terminal 签名构建 exit 0）
- `/tmp/livachat_m0_simulator.png`
- `/tmp/livachat_m0_simulator_ios17.png`
- `/tmp/livachat_m0_simulator_uitest.png`（XCUITest 截图）
- `/tmp/livachat_m0_ipad.png`（iPad XCUITest 截图）

### 13.5 M1 后续说明

2026-10-20：M1 实施已删除 `M0SDKProbe`、`M0View`、`M0ViewModel`、`M0SDKLinkageTests`、`M0ScreenshotUITests`；本文保留为工程骨架阶段的历史记录。当前 App 入口、构建方式与测试请以 `docs/ios_app_m1.md` §14 为准。
