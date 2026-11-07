# LivaChat iOS App Shell / 生命周期设计

**状态**：已实施（M1.3/M1.4 + 后台音频；AppContainer 单测与 UI 测试通过）
**日期**：2026-10-24
**上游文档**：
- `docs/ios_app_m1.md` §3、§4.5、§8（Runtime、重建、前后台）
- `docs/ios_app_conversation.md`、`docs/ios_app_settings.md`、`docs/ios_app_ui.md`

本文定义 App 入口、组合根、路由、前后台、权限门禁和 runtime 重建。M1 只有 Splash / Permission / Conversation / Settings 四个页面。

---

## 1. 入口与组合根

```swift
@main
struct LivaChatApp: App {
    @StateObject private var container = AppContainer()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            AppRootView(container: container)
                .onChange(of: scenePhase) { container.handleScenePhase($0) }
                .preferredColorScheme(.dark)
        }
    }
}
```

- `AppContainer` 是唯一组合根（`@MainActor`, `ObservableObject`）。
- 不引入 DI 框架；依赖在 `AppContainer` 内构造并通过 init 注入。
- iOS 15 使用 `@StateObject`/`@ObservedObject`/`@Published`，不使用 iOS 17 `@Observable`。
- App 强制 dark 模式；不支持浅色主题。

### 1.1 所有权

```
AppContainer
 ├─ AppRouter (@Published phase/showSettings)
 ├─ SettingsStore (UserDefaults)
 ├─ CredentialStore (Keychain)
 ├─ ServerProbe (URLSession)
 ├─ HapticController
 ├─ AudioSessionController?          // 每个 runtime 一份
 ├─ ConversationViewModel?          // 当前会话
 └─ ConversationRuntime?            // 当前 runtime；与 ViewModel 成对
```

- `AppContainer` 强持有 playback controller（SDK 只弱引用）；runtime 释放时同步释放。
- `AppRouter` 只负责页面切换；不持有业务对象。
- `ConversationViewModel` 不持有 `AppContainer`，避免环。

---

## 2. AppRouter

```swift
@MainActor
final class AppRouter: ObservableObject {
    enum Phase: Equatable { case splash, permission, conversation }
    @Published var phase: Phase = .splash
    @Published var showSettings = false
}
```

- `AppRootView`：

```swift
switch router.phase {
case .splash:       SplashView()
case .permission:   PermissionView(...)
case .conversation: ConversationView(viewModel: container.conversationViewModel)
}
```

- Settings：在 Conversation 上 `.fullScreenCover(isPresented: $router.showSettings)`；关闭时保持 runtime 运行。
- 不用 `NavigationStack`（iOS 16+）；不引入导航库。
- 转场：0.25s opacity；reduce motion 时无动画。

### 2.1 启动路由

```
AppContainer.init
  → router.phase = .splash
  → AppRootView.task { container.bootstrap() }

bootstrap():
  记录 splashStartedAt
  await 最短 1s
  读取 onboardingCompleted + micPermission
  if !onboardingCompleted || micPermission != .granted:
      router.phase = .permission
  else:
      router.phase = .conversation
      prepareConversationIfNeeded()
```

- Splash 最短 1s；最多等待 2s（权限查询是同步的，不阻塞）。
- 首启完成但权限被系统回收：仍进 Permission，显示“去系统设置”。
- 不从 Splash 直接连接；只有 Conversation 出现后才创建 runtime。

### 2.2 首次启动与权限

- `onboardingCompleted` 只在用户完成 Permission 页“开始聊天/授权”后置 true。
- 权限查询：`AVAudioSession.sharedInstance().recordPermission`（iOS 17 下 deprecated 但可用；若后续迁移到 `AVAudioApplication` 需 `#available`）。
- 权限请求：`requestRecordPermission`，回调切回 MainActor。
- 用户拒绝：
  - 非永久拒绝：再次点“开始聊天”仍可触发系统弹窗（系统只弹一次）；
  - 永久拒绝：显示 `perm_mic_required_hint` + `perm_go_to_settings`。
- 授权成功后调用 `router.phase = .conversation` 并 `prepareConversationIfNeeded()`。
- 从系统设置返回：`scenePhase == .active` 时重新读取权限；若已授权且当前在 Permission，进入 Conversation。

---

## 3. Runtime 构建与重建

### 3.1 首次进入 Conversation

```swift
func prepareConversationIfNeeded() {
    guard conversationViewModel == nil else { return }
    let vm = ConversationViewModel(permissionProvider: ...)
    var launchConfig: AppLaunchConfig?
    do {
        launchConfig = try settings.loadSavedConfig(credentials: credentials)
            ?? DebugLaunchConfig.override()
    } catch {
        vm.showError(...)
    }
    conversationViewModel = vm
    guard let config = launchConfig else {
        vm.markServerConfigRequired()
        return
    }
    let runtime = ConversationRuntimeFactory.make(config: config, viewModel: vm, ...)
    vm.attach(runtime: runtime)
    runtime.start()
}
```

- 配置缺失：仍创建 ViewModel，Conversation 显示阻断卡片“去设置”，不创建 SDK/音频。
- Debug 覆盖（`docs/ios_app_settings.md` §7）在保存配置之前生效，便于 Mock Server 联调。
- 创建顺序：`AudioSessionController` → `AudioPlaybackController` → `LivaChatSDK` → `AudioCaptureController` → `InterruptCoordinator` → `ConversationCoordinator` → `ConversationEventAdapter` → `ConversationRuntime`。
- 任何构造失败：释放半成品、显示 error banner，不崩溃。

### 3.2 保存设置后的重建

由 `SettingsViewModel` 发出 `AppContainer.applyServerSettings(address:secret:)`：

```
guard settings differ
stopRuntime()                 // adapter/媒体/连接/音频 session
persist address + secret + wire token
conversationViewModel = nil / 新建
prepareConversationIfNeeded() // 新 runtime，新 playbackEpoch
router.showSettings = false
```

- 重建前清空：`serverSessionID`、消息、转写、流式文本、banner、`sessionEnded`。
- 新 `AudioPlaybackController` 与 JitterBuffer 从 `acceptedEpoch = 1` 开始。
- 重建失败：保留新配置但显示错误；用户可再次保存或回设置。
- 触觉开关不触发重建。
- 取消保存：不调用以上任何步骤。

### 3.3 退出

- M1 不主动 `endSession`/退出 App；进入后台按 §4 处理。
- 无显式“结束会话”按钮；`sessionEnded` 由服务端驱动。

---

## 4. 前后台与生命周期

`AppContainer.handleScenePhase(_:)`：

| ScenePhase | 动作 |
|---|---|
| `.active` | 重新读权限；若在 Permission 且已授权 → Conversation；若 runtime 存在 → `coordinator.onForeground()` |
| `.inactive` | no-op（系统弹窗/来电过渡；不停止音频，避免系统权限弹窗时爆音） |
| `.background` | `isForeground = false`；`coordinator.onBackground()`：停相机并保留恢复意图；mic 未静音保持 full，mic 静音先 topUp 再完整 teardown |

- `onForeground()`：guard `isStarted && !sessionEnded && !awaitingAuthFix && micPermission == .granted`；不满足时只恢复 UI，不启动媒体、不自动开新会话。满足时 `reconcileMedia` 确保 full（从 stopped 恢复会重建采集/播放并丢弃停机期间缓冲的 TTS；full 内 engine/route 恢复保留 jitter），`connectionState == .disconnected` 时 `sdk.connect()`，成功后恢复相机。
- `startRuntime()` 可能在后台完成；Coordinator `start()` 用 `isForeground()` 决定是否启动媒体/相机，后台只建立连接，回前台再启动 full。
- 中断、route change、`AVAudioEngine` configuration change 和 media services reset 都由 `AudioSessionController` 回调 Coordinator；Coordinator 统一走 `reconcileMedia()`，不再使用前台 stop/start 的单一逻辑。
- `UIBackgroundModes = [audio]` 已启用：后台未静音保持 full（采集+播放）；mic 静音时 topUp 后完整 teardown 并 `deactivate()` 释放麦克风，后台不再播放 TTS，App 可挂起。后台音频模式不保证 WebSocket/进程永久存活。
- 从后台回前台若服务端 session 已失效，走 `sessionReady(restored=false)` 清空内存消息。

---

## 5. 权限状态

```swift
enum MicPermissionState: Equatable {
    case unknown, undetermined, granted, denied
}
```

- 每次 `.active` 重新读取，处理“设置中授予/回收”。
- 进入 Conversation 时若权限变为 denied：Conversation 显示权限提示 + 去系统设置，不启动采集。
- 权限丢失不销毁 runtime；用户授权返回后 `.active` 会重新读取权限并调用 `coordinator.onForeground()`，由统一 reconcile 重启媒体（`onMicPermissionRestored()` 保留为测试/兼容入口）。
- 系统权限弹窗本身由 Permission 页触发；Conversation 不在无权限时反复弹窗。

---

## 6. 文件结构

```
apps/ios/LivaChatApp/
  App/
    LivaChatApp.swift
    AppContainer.swift
    AppRouter.swift
    AppRootView.swift
    AppLaunchOptions.swift          // DEBUG UI/网络覆盖参数
  Core/
    Audio/
      AudioSessionController.swift
      AudioCaptureController.swift
      AudioPlaybackController.swift
      JitterBuffer.swift
      InterruptCoordinator.swift
      HapticController.swift
      PCMChunker.swift
      SilenceFlushGate.swift
      MicLevelMeter.swift
    Conversation/
      ConversationProtocols.swift
      ConversationRuntime.swift
      ConversationRuntimeFactory.swift
      ConversationEventAdapter.swift
      ConversationCoordinator.swift
      ConversationViewModel.swift
      ConversationModels.swift
    Settings/
      CredentialStore.swift
      SettingsStore.swift
      ServerAddress.swift
      ServerProbe.swift
      SettingsViewModel.swift
    Platform/
      AppInfo.swift
      AppLog.swift
      SystemSettingsOpener.swift
  Features/
    Splash/SplashView.swift
    Permission/PermissionView.swift
    Conversation/ConversationView.swift
    Conversation/components/*.swift
    Settings/SettingsView.swift
    AudioSpike/AudioSpikeView.swift   // #if DEBUG
  Resources/
    Assets.xcassets
    Info.plist
    Localizable.xcstrings
    en.lproj/InfoPlist.strings
    zh-Hans.lproj/InfoPlist.strings
```

- M0 的 `M0SDKProbe.swift`、`Features/M0/*`、`M0SDKLinkageTests.swift`、`M0ScreenshotUITests.swift` 已删除，由 M1 runtime/UI 测试覆盖。
- 新增文件需加入 `LivaChatApp` target 的 Sources；测试文件加入 `LivaChatAppTests`；UI 测试加入 `LivaChatAppUITests`。
- 业务层不 import UIKit/SwiftUI（ViewModel/Coordinator/Settings 只依赖 Foundation/SDK）；UIKit 仅限 Haptic、Permission 打开系统设置、字体缩放适配。

---

## 7. UI 测试启动参数

`AppLaunchOptions` 仅在 `#if DEBUG` 读取：

| 参数 | 作用 |
|---|---|
| `-uiTestScreen splash|permission|conversation|settings` | 直接进入指定页面 |
| `-uiTestConnection idle|listening|thinking|speaking|disconnected` | Conversation 预置状态 |
| `-uiTestPermission granted|denied|undetermined` | Permission 预置 |
| `-uiTestMessages empty|sample` | 消息内容 |
| `-uiTestBanner warning|error|none` | 错误 banner |
| `-uiTestSkipSplash` | 跳过 1s |

- UI 测试模式不创建真实 runtime、不请求权限、不播放音频。
- 生产 Release 构建忽略所有 `-uiTest*` 参数。
- 网络联调环境变量见 `docs/ios_app_settings.md` §7。

---

## 8. 测试

| 测试 | 覆盖 |
|---|---|
| `AppRouterTests` | splash→permission/conversation 路由；settings 开关 |
| `AppContainerTests` | 配置缺失不建 runtime；保存重建清空状态；停止顺序 |
| `AppLifecycleTests` / `ConversationCoordinatorTests` | background/active 调用 Coordinator；后台 full/stopped 策略；sessionEnded/auth 回前台不自动复活 |
| `PermissionFlowTests` | 首启/已授权/拒绝路由；返回前台重读权限 |
| UI tests | 四页渲染与标识；Conversation 五状态；Settings 服务区块；截图 |
| 构建回归 | `./scripts/build-ios.sh sdk`、`app`；iPad 兼容模式启动 |

---

## 9. 待决策/风险

| 项 | 处理 |
|---|---|
| iOS 15 无 NavigationStack | 用 AppRouter + fullScreenCover；不引入第三方路由 |
| scenePhase `.inactive` 时机 | M1 不在 inactive 停音频，避免系统弹窗打断 |
| Keychain 首次写入失败 | 降级内存 deviceID + 错误提示；设置保存失败不重建 |
| 权限永久拒绝 | 引导系统设置；不循环请求 |
| 重建 runtime 时旧任务迟到事件 | adapter.stop 后旧实例释放；新 runtime 使用新实例，不共享状态 |
| iPad 兼容模式 | target family 1；内容居中；M2 再评估 Universal |

---

## 10. 统一日志文件

App 与 SDK 共用一个 append-only 文件（无设置页导出入口）：

```
Library/Logs/livachat.log
```

- 每行一个事件，首字段为 **ISO 8601 UTC 微秒**时间，例如 `2026-10-20T07:30:00.123456Z`；
- 第二字段为 `<level>/<component>`，例如 `I/App/AudioSession`、`W/SDK/Session`；
- 其余为消息；`\n`/`\r` 转义为 `\\n`/`\\r`，保证一行一条；
- 每次 App 启动写一行 `I/App/System session start app=<version> os=<os> log=<path>`；
- App 与 SDK 都使用 `O_APPEND` 单次 `write`，文件内按时间戳排序；同时保留 os_log（`dev.livachat.app` / `dev.livachat.sdk`）；
- 单文件不轮转，文件在 App 数据容器内；需要清理时重装 App（会清空数据）。
- 当前无大小上限（2026-10-20 实测设备上约 3 KB，8 秒运行约 1.3 KB）；后续观察体积，异常重连/持续错误导致明显增长时再评估“启动时保留最新一半”或轮转。

使用 debug 工具导出（无需 GUI）：

```bash
xcrun devicectl device copy from \
  --device <DEVICE_UDID> \
  --domain-type appDataContainer \
  --domain-identifier dev.livachat.app \
  --source Library/Logs/livachat.log \
  --destination /tmp/livachat.log
```

`DEVICE_UDID` 可用 `xcrun devicectl list devices` 获取。首次调试自签名服务器的连接问题时，日志中 `TLS certificate validation is disabled (allowSelfSigned)` 与 `TLS`/`-1202` 行可直接定位证书问题。
