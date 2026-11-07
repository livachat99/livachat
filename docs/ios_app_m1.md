# LivaChat iOS App M1 分析与设计：前台语音闭环 MVP

**状态**：已实施并验收（M1.0–M1.5；2026-10-20 真机人工验收完成）

> **历史里程碑文档**：后台音频/双 engine 的最终实现以 `docs/ios_app_audio.md`、`docs/ios_background_support.md` 为准；本文中“不启用后台音频”等描述是当时 M1/M2 的历史状态，不再是当前行为。

**日期**：2026-10-20
**上游文档**：
- `docs/ios_app_arch.md`（App 架构、模块边界、线程模型）
- `docs/ios_app_m0.md`（M0 工程骨架、SDK 链接、模拟器/iPad 验收）
- `docs/ios_sdk_design.md`（SDK 公开契约、宿主集成 §7、流程 §8）
- `docs/ios_dev_rules.md`（iOS 15 / SwiftUI / SPM）

**UI/UX 基线**：Android 产品 App 的现行实现（不是旧设计稿）：
- `apps/android/LivaChatApp/.../ui/screens/conversation/ConversationScreen.kt`
- `.../conversation/ConversationViewModel.kt`、`.../domain/ConversationCoordinator.kt`
- `.../ui/screens/splash/SplashScreen.kt`、`.../permission/PermissionScreen.kt`、`.../settings/SettingsScreen.kt`
- `.../ui/components/{AiAvatar,StatusIndicator,MessageBubble,TranscriptionBubble,BottomToolbar,ErrorBanner,WelcomeGuide}.kt`
- `.../ui/theme/{Color,Type,Spacing,Shape,Theme}.kt`
- Android 音频实现：`AudioCaptureController`、`AudioPlaybackController`、`InterruptCoordinator`

> 本文中的 Android 数值/行为均引用上述源码；iOS 侧平台差异与新决策会明确标注。M1 只做前台 chat 模式，不把 Video、Translate、Lecture、Transcribe、Omni、History、Agent job UI 混入范围。

---

## 1. M1 目标与验收定义

### 1.1 目标

M1 交付一个**前台语音闭环 MVP**：

```
配置服务器 → 麦克风授权 → connect
→ collection epoch → startSession
→ 上行语音 → transcription / aiText
→ TTS 下行播放 → 用户语音打断
→ 断线重连并 resume → 主动结束/断开
```

### 1.2 “完成”的判定

- 用户可以在 Settings 配置服务器地址/密钥，并成功检测服务；
- 首次启动完成麦克风授权，进入会话页后自动连接并建立会话；
- 用户说话时能看到实时转写，最终用户消息与 AI 回复出现在消息列表；
- 能听到 AI TTS，播放不依赖 SwiftUI 主线程；
- 用户开口可打断 AI 播放（本地 VAD 触发），无需等待服务端；
- 网络断开后自动重连，并通过 `serverSessionID` 恢复；
- 设置变更能重建 runtime 并重新连接；
- 无相机、无录屏、无模式切换、无历史持久化、无后台通话。

### 1.3 非目标（M2 以后）

- 摄像头、图库、屏幕采集、屏幕抓取浮窗；
- translate / lecture / transcribe / omni 模式；
- agent job 列表/结果查看/取消；
- Room/本地历史、MyLife、跨设备同步；
- 后台音频、CallKit、Notification；
- 自定义头像、语言选择、设备选择器；
- Markdown 渲染、图片消息、全屏查看器；
- 原生 iPad 分栏布局（M1 保持 iPhone-only target + iPad 兼容模式）；
- iOS 15/16 真机冒烟（当前可用设备是 iPadOS 17.7.5；iPhone 11 需 Xcode 26.x）。

---

## 2. 当前基线

### 2.1 M0 已落地

`apps/ios/LivaChatApp` 已有：

- 标准 Xcode 工程、Swift 6、iOS 15 target、iPhone-only；
- 本地 SPM 依赖 `apps/ios/LivaChatSDK`；
- `AppContainer`、`M0View`、`M0SDKProbe`、no-op `LivaChatAudioSink`；
- `M0SDKLinkageTests` + `M0ScreenshotUITests`；
- 模拟器（iOS 17.0 / 18.6）和 iPad Pro 10.5（iPadOS 17.7.5）运行通过；
- `scripts/build-ios.sh sdk|app|all` 构建入口。

M1 已替换 `M0SDKProbe` / `M0View` / `M0ViewModel` 并删除对应文件；M0 页面级 UI 测试与 SDK linkage smoke 已移除，由 M1 runtime/UI 测试覆盖。

### 2.2 SDK 事实

来自 `apps/ios/LivaChatSDK` 与 `docs/ios_sdk_design.md`：

- `LivaChatSDK(config:audioSink:)`，配置不可变；
- `events: AsyncStream<LivaChatEvent>`，单消费者；
- `audioSink: LivaChatAudioSink` 弱引用、初始化注入；
- `state: LivaChatState` 原子快照；
- `.connectionEpochAdvanced` 是唯一 resume 触发点；
- `.sessionEnded` 必须清空 `serverSessionID`；
- `.playbackShouldFlush(reason, epoch)` 要求丢弃 `< epoch` 的帧；
- `connectionEpoch` 只在成功建连时递增；
- 音频固定 16kHz / 20ms；
- SDK 不自动 `startSession`，也不在 App teardown 时自动发送 `session.end`。

### 2.3 环境约束

- Xcode 16.4（iOS 18.5 SDK）；可用模拟器 iOS 17.0 / 18.6；
- 真机验收设备：iPad Pro 10.5（iPadOS 17.7.5，iPhone 兼容模式）；
- iPhone 11（iOS 26.6）需要 Xcode 26.x，M1 不依赖它；
- 签名构建必须在 GUI Terminal / Xcode 中执行；
- 当前没有 iOS 15/16 runtime，M1 运行时验收以 iOS 17/18 模拟器 + iPad 为准。

---

## 3. M1 架构

### 3.1 Runtime 组成

用 `ConversationRuntime` 替换 M0 的 `M0SDKProbe`：

```
AppContainer
 ├─ AppRouter / AppLifecycleController
 ├─ SettingsStore + CredentialStore + ServerProbe
 ├─ ConversationRuntime
 │    ├─ LivaChatSDK
 │    ├─ ConversationEventAdapter   // sdk.events 唯一消费者
 │    └─ ConversationCoordinator    // 会话/媒体生命周期
 ├─ AudioSessionController
 ├─ AudioCaptureController
 ├─ AudioPlaybackController        // LivaChatAudioSink
 ├─ InterruptCoordinator
 └─ HapticController
```

规则：

- `ConversationEventAdapter` 是 `sdk.events` 的唯一消费者；
- ViewModel 不直接订阅 SDK；
- `AudioPlaybackController` 在 SDK 初始化前构建，SDK 只持弱引用，App 强持有；
- `ConversationCoordinator` 与 EventAdapter 通过回调/`AsyncStream` 解耦，避免引用环；
- 服务器配置变更时整体重建 runtime，不修改已有 SDK 配置。

### 3.2 模块边界

| 模块 | M1 职责 | 详细文档 |
|---|---|---|
| AppShell | App 入口、`AppContainer`、`AppRouter`、scenePhase、runtime 重建 | `docs/ios_app_shell.md` |
| Conversation UI | 会话页、权限页、设置页、主题与组件 | `docs/ios_app_ui.md` |
| Conversation Domain | `ConversationCoordinator`、`ConversationEventAdapter`、会话/恢复策略 | `docs/ios_app_conversation.md` |
| Audio | AVAudioSession、采集、播放、JitterBuffer、playbackEpoch、打断 | `docs/ios_app_audio.md` |
| Settings/Keychain | 服务器配置、凭证、deviceID、ServerProbe、TLS 策略 | `docs/ios_app_settings.md` |
| Platform adapters | 触觉（UIImpactFeedbackGenerator）等 UIKit 例外 | `docs/ios_app_ui.md` |

本文只定义模块契约与跨模块交互；每个模块的类/协议/状态机细节由独立文档承载。

### 3.3 测试缝

为让 Domain/ViewModel 可测试，M1 在 App 侧定义窄协议（具体命名由模块文档定稿）：

- SDK 生命周期/事件源：`LivaChatSDKProviding` / `LivaChatEventStreaming`
- 音频采集：`AudioCapturing`（start/stop/mute/micLevel）
- 音频播放：`AudioPlaying`（sink 接收、flush/epoch、mute）
- 凭证与设置：`CredentialStoring` / `ServerProbing`
- 触觉：`HapticProviding`

生产实现包装真实 SDK/AVFoundation；测试注入 fake，不引入第三方 mock 框架。

---

## 4. 会话与状态设计

### 4.1 状态模型

`ConversationViewModel` 的 M1 状态（内存，不落库）：

- `connectionState`
- `sessionState`
- `messages: [ChatMessage]`（本次运行内存）
- `currentTranscription`
- `streamingAiText`
- `isMicMuted` / `isSpeakerMuted`
- `error`（banner）
- `isFirstSession`
- `serverConfigReady`
- `micPermission`

`ChatMessage` 最小字段：`id`、`role`、`text`、`isFinal`、`timestamp`。M1 不含图片、mode、source。

### 4.2 EventAdapter 事件映射

| SDK 事件 | M1 App 动作 |
|---|---|
| `.connectionStateChanged` | 更新状态；非 `.connected` 清空转写/流式文本；保留已有消息 |
| `.connectionEpochAdvanced` | `sessionActive && connected` → Coordinator 调 `startSession(resumeSessionID:)` |
| `.connectionTerminated` | `authFailed` → 去 Settings；`reconnectExhausted`/`serverClosed` → DISCONNECTED + 重试入口 |
| `.sessionStateChanged` | 驱动头像/状态指示器 |
| `.localVadStateChanged` | 供 InterruptCoordinator 与头像状态使用 |
| `.sessionReady` | 保存 `serverSessionID`；`restored=false` 清空本次内存消息/临时文本；复位会话状态 |
| `.sessionEnded` | 清空 `serverSessionID`、临时文本；停止采集/播放；显示“会话已结束”与重试；不自动开新会话 |
| `.transcription` | interim 进内存；final 追加用户消息 |
| `.aiText` | 按 `messageID` 累积；final 追加 AI 消息 |
| `.playbackShouldFlush` | 转发给 AudioPlaybackController，按 `playbackEpoch` 过滤 |
| `.error` | recoverable → 5s 自动消失的 Warning banner；fatal → 常驻 Error banner |
| `.jobStatus` / `.videoAck` | M1 忽略（不展示、不持久化） |
| `.sessionChanged` | M1 不呈现模式 UI；仍记录/忽略，保持 chat 默认 |

事件门控：

- 以最近一次连接状态门控 transcription/aiText；断连后迟到的非 final 帧不得复活临时 UI；
- 不依赖跨来源事件的全局顺序；需要门控时读 `sdk.state` 快照；
- 同一来源内有序，跨来源不保证。

### 4.3 建连与会话

M1 的启动策略：

```
App 启动 / 进入 Conversation
  → 读取服务器配置
      不完整 → 提示去 Settings，不启动 runtime
      完整   → ConversationCoordinator.start()
  → 读取/生成 Keychain deviceID
  → 创建 AudioPlaybackController（sink）→ 构造 LivaChatSDK
  → 启动 EventAdapter 消费
  → sdk.connect()
  → capture.start() + playback.start() + interrupt.start()
  → .connectionEpochAdvanced
      → sdk.startSession(resumeSessionID: serverSessionID ?? nil)
  → .sessionReady
      → serverSessionID = id
      → restored == false → 清空内存消息/临时状态
  → .sessionStateChanged(.listening)
```

- 首次连接 `serverSessionID == nil`；
- 重连复用服务端 `sess_*`，不得传本地 UUID；
- 采集在 `connect` 后立即启动，`session.start` 前的音频由 SDK 闸门丢弃；
- M1 不发送 `session.change`，保持在 chat 模式；
- `ConversationCoordinator.start()` 幂等；从 Settings 返回不重复建连；进入 Settings 页本身不停止 runtime，只有保存并应用配置时才重建。

### 4.4 重连与结束

```
网络断开 → connectionState = RECONNECTING
  → SDK 指数退避重连
  → 建连成功 → connectionEpochAdvanced
  → Coordinator: startSession(resume: serverSessionID)
  → sessionReady(restored=true) → 继续
  → restored=false → 清空内存消息/临时文本，按新会话继续
```

终态：

- `authFailed`：停止自动连接，显示错误并引导 Settings；
- `reconnectExhausted`：DISCONNECTED，状态指示器可点重试；
- `serverClosed` / `sessionEnded`：清空 serverSessionID、临时 UI，停止媒体，显示会话结束 + 重试；
- 用户主动离开/进程退出：M1 不调用 `sdk.endSession`，由系统/服务端清理；App 内不提供“结束会话”按钮，避免与 Android 现状不一致。

### 4.5 设置变更与 runtime 重建

保存服务器配置后：

1. 停止采集/播放/打断；
2. 停止 EventAdapter 消费；
3. 清空 `serverSessionID`、内存消息、临时文本；
4. 重置播放器 JitterBuffer / `acceptedEpoch`（新 SDK 的 `playbackEpoch` 从 1 开始）；
5. 销毁并重建 `ConversationRuntime`；
6. 回到 Conversation，按新配置连接。

M1 不要求杀进程（iOS 无 Android `exitProcess` 语义）。

### 4.6 线程与并发

- `ConversationViewModel` / `ConversationCoordinator` / EventAdapter 状态更新在 MainActor；
- `audioSink` 在 SDK audio recv 队列，播放控制器不得做 UI/磁盘/网络；
- JitterBuffer 被 audio recv 写入、Main 的 flush 事件读取，必须加锁或串行化；
- `acceptedEpoch = max(acceptedEpoch, epoch)`；只丢弃 `< acceptedEpoch` 的帧，保留新 epoch 帧；
- 采集回调、相机/录屏不在 M1；
- 设置写 Keychain 在后台/异步完成，避免阻塞 UI。

---

## 5. Audio 设计契约

详细实现见 `docs/ios_app_audio.md`，M1 只定义跨模块契约：

### 5.1 AVAudioSession

- Category `.playAndRecord`，Mode `.voiceChat`；
- Options 至少 `.defaultToSpeaker`、`.allowBluetooth`、`.allowBluetoothA2DP`；
- 开启 `setVoiceProcessingEnabled(true)` 获取系统 AEC；
- 处理 `interruptionNotification`、`routeChangeNotification`、媒体服务重置；
- 启动时请求/检查麦克风权限；无权限不启动采集。

### 5.2 采集

- `AVAudioEngine` input tap → `AVAudioConverter` → 16kHz mono Int16；
- 20ms 帧推 `sdk.pushAudioPCM`；
- mute：
  - mute 沿后补发 1s 全零 PCM（`muted: true`）；
  - 窗口耗尽后继续采集但不 push；
  - unmute 立即恢复真实帧；
- 麦克风电平由 App 旁路计算，mute/停止恒 0；
- 采集失败有限重试；耗尽后停止并上报 UI。

### 5.3 播放

M1 选型：**`AVAudioPlayerNode` + JitterBuffer**（先实现，后续按延迟/underrun 评估 `AVAudioSourceNode` + ring buffer）。

- `LivaChatAudioSink` 回调快速拷贝入 JitterBuffer；
- 目标预缓冲 3 帧（60ms）；最大积压按 SDK 文档；
- `playbackShouldFlush`：丢弃 `playbackEpoch < acceptedEpoch` 的帧，保留新帧；不整队清空；
- 连接断开/重连同样按 epoch 过滤；
- 静音使用 player volume；
- `AudioPlaybackController` 强持有，SDK 弱引用；runtime 重建时重置 acceptedEpoch。

### 5.4 打断

- 条件：`sessionState == .speaking && localVadState == .speech`；
- 500ms 节流；
- `sdk.sendInterrupt(.userVoice)` + `UIImpactFeedbackGenerator` 轻触觉；
- **不本地 flush 播放队列**；等 SDK 的 `.playbackShouldFlush(.interruptAck, epoch:)`。

### 5.5 路由与后台

- M1 使用系统默认输出路由；设备选择器后置；
- M1 不启用 `UIBackgroundModes: audio`，不承诺后台通话；
- App 进入后台时停止采集/播放并释放音频会话；回前台重启采集/播放，若连接 DISCONNECTED 则 `connect()` 自愈；
- 音频中断/路由变化由 AudioSessionController 统一处理。

---

## 6. Settings / Keychain 契约

详细实现见 `docs/ios_app_settings.md`。

### 6.1 存储

| 数据 | 存储 | 说明 |
|---|---|---|
| 服务器地址（`ip:port` / 域名:port） | UserDefaults | 非机密；进程启动/设置页读取 |
| 明文 secret | Keychain | 不写日志 |
| wire token（`sha1(secret + "LivaChat")`） | Keychain（可缓存） | 仅 wire token 上网 |
| deviceID（UUID v4） | Keychain | 卸载重装保留 |
| 触觉开关 | UserDefaults | M1 General 设置 |

### 6.2 服务预检

- `GET /healthz_token?token=<wire>&device_id=<id>`；
- `AVAILABLE` / `AUTH_FAILED` / `UNREACHABLE` 三态映射 UI；
- `ServerProbe` 的 TLS 信任策略必须与 SDK `tlsPolicy` 一致；自签名场景在 Settings 开启“允许自签名证书”（默认开启）后两者同时使用 `.allowSelfSigned`，或安装/信任 CA。

### 6.3 保存与生效

- 地址必须匹配 `ip:port` / `域名:port`；secret 非空且符合允许字符集；
- 保存后走 §4.5 的 runtime 重建；
- 需要重建时给用户一个明确确认（应用并重新连接 / 取消）；
- M1 不使用 Android 的内置开发服务器预设；地址手工输入，避免把开发 IP 固化到 iOS 产品。

### 6.4 平台配置

| 配置 | M1 |
|---|---|
| `NSMicrophoneUsageDescription` | 必需（麦克风权限弹窗的系统文案） |
| `NSLocalNetworkUsageDescription` | 连接局域网服务器 / Mac 上的 Mock Server 时需要 |
| `NSCameraUsageDescription` | M1 不添加（无相机） |
| `UIBackgroundModes: audio` | M1 不启用（不做后台通话） |
| ATS / TLS | 默认 `wss://`；`ws://` 仅受控开发环境加 ATS 例外；自签名通过 Settings 的“允许自签名证书”开关（SDK/Probe 同策略）或安装 CA 处理 |
| Keychain | secret / wire token / deviceID；服务器地址在 UserDefaults |

---

## 7. UI/UX 设计（Android 现行实现为基线）

### 7.1 设计原则

- Dark-only；色值、字号、间距、圆角与 Android 现行实现一致；
- iPhone 竖屏为基准；M1 保持 iPhone-only target，iPad 走兼容模式；
- 状态用“颜色 + 文案 + 动画”共同表达，不只依赖颜色；
- 最小操作路径：打开即连接，说话即可用，打断无额外操作；
- 动态字体、VoiceOver、44pt 最小触控目标；
- 本批只实现 chat 语音所需组件；未实现的 Android 功能不显示死控件。

### 7.2 设计 Token（来自 Android `ui/theme`）

**颜色**

| Token | 值 | 用途 |
|---|---|---|
| `BrandIndigo` | `#6366F1` | 主品牌色 / 用户气泡 / 按钮 |
| `BrandViolet` | `#8B5CF6` | 渐变终点 / 辅助强调 |
| `Success` | `#10B981` | IDLE / SPEAKING 状态、可用 |
| `Warning` | `#F59E0B` | CONNECTING / THINKING、可恢复错误 |
| `Error` | `#EF4444` | 断线 / 致命错误 |
| `Info` | `#3B82F6` | LISTENING |
| `Background` | `#0F172A` | 页面背景 |
| `Surface` | `#1E293B` | 卡片 / AI 气泡 |
| `SurfaceVariant` | `#334155` | 分割线 / 次要面 / 输入框未聚焦边框 |
| `TextPrimary` | `#F8FAFC` | 主文字 |
| `TextSecondary` | `#94A3B8` | 次要文字 |
| `TextTertiary` | `#64748B` | 占位 / 禁用 |

**字体**（iOS 使用系统字体，按相同字号/字重映射）

| 层级 | 字号/行高 | 字重 | 用途 |
|---|---|---|---|
| headline | 28 / 36 | Bold | Splash 标题 |
| title | 22 / 28 | Semibold | 页面标题 |
| body | 16 / 24 | Regular | 正文/消息 |
| label | 14 / 20 | Medium | 状态/按钮 |
| caption | 12 / 16 | Regular | 按钮 caption / 辅助信息 |

**间距**：`4 / 8 / 16 / 24 / 32 / 48` pt。
**圆角**：`4 / 8 / 12 / 16 / 24`；消息气泡 16，尾角 8；按钮/卡片 16；工具栏圆形按钮 50%。

### 7.3 导航流（M1）

```
Splash ──首次/无麦克风权限──▶ Permission ──授权──▶ Conversation
   └──────────已有权限──────────────────────────────▶ Conversation
Conversation ──右上菜单/配置缺失──▶ Settings
Settings ──返回──▶ Conversation
```

M1 无 History/WebView/MyLife 路由。

### 7.4 Splash

对齐 Android `SplashScreen.kt`：

- 深色背景；
- 80pt 渐变圆 + 文本 "~"；
- 标题 `LivaChat` 28 Bold；
- tagline 14 Secondary（Android 文案）；
- 底部“正在唤醒你的伙伴...” 14 Secondary；
- 渐变圆脉冲 1.0→1.05，600ms 循环；
- 至少 1 秒；同时检查首启/麦克风权限并路由。

### 7.5 Permission（M1 仅麦克风）

参考 Android `PermissionScreen.kt`，但删除摄像头卡片（M1 无相机）：

```
┌─────────────────────────────────────┐
│        （标题：开始之前，需要许可）  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ 🎤 麦克风权限          [授权] │  │  16pt 圆角 SurfaceVariant 卡片
│  │    让我听见你的声音…          │  │  已授权时显示 ✓
│  └───────────────────────────────┘  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │         开始聊天              │  │  56pt 高，BrandIndigo，16pt 圆角
│  └───────────────────────────────┘  │
│         前往系统设置（文字按钮）     │
└─────────────────────────────────────┘
```

- 首次启动展示说明后再请求系统权限；
- 拒绝/永久拒绝：显示“请先授权麦克风权限才能开始聊天”，提供前往系统设置；
- 授权后进入 Conversation；
- 摄像头权限不申请；M2 相机批次再恢复可选摄像头卡片。

### 7.6 Conversation

对齐 Android 语音模式的布局与状态，但删除本批未实现功能。

```
┌─────────────────────────────────────┐
│  ● 随时可以说             ⋯         │  状态指示器 + 更多菜单（Settings）
├─────────────────────────────────────┤
│              （AI 头像）             │  120pt；渐变 96pt；状态光环
│                                     │
│  ┌───────────────────────────────┐  │
│  │ 用户气泡                       │  │  BrandIndigo，右侧
│  │ AI 气泡                       │  │  Surface + 1pt 描边，左侧
│  │ ...                           │  │  LazyVStack，自动滚底
│  └───────────────────────────────┘  │
│                                     │
│  ┌─ 实时转写浮层（非 final）─────┐   │  底部覆盖，不挤占消息列表
│  └───────────────────────────────┘   │
│                                     │
│  [🎤 麦克风]        [🔊 扬声器]     │  48pt 圆形按钮 + 12pt caption
└─────────────────────────────────────┘
```

**顶部状态**

| 条件 | 点颜色 | 文案 | 头像 |
|---|---|---|---|
| DISCONNECTED | Error | “连接断开” | IDLE |
| CONNECTING / RECONNECTING | Warning | “正在连接...” | IDLE |
| CONNECTED + IDLE | Success | “随时可以说” | IDLE |
| CONNECTED + LISTENING | Info | “我在听...” | LISTENING |
| CONNECTED + THINKING | Warning | “让我想想...” | THINKING |
| CONNECTED + SPEAKING | Success | 无文案 | SPEAKING |

- 断线时状态指示器可点，触发重连；
- 更多菜单 M1 只保留 Settings（History/时光机后置）。

**头像**

- 120pt 外框，中心 96pt 渐变圆（`BrandIndigo` → `BrandViolet`），默认显示 "~"；
- 状态光环颜色与上表一致；IDLE 不显示光环，非 IDLE 才显示（对齐 Android `AiAvatar`）；
- M1 实现状态色 + 基础呼吸/脉冲；Android 的 listening 三涟漪、thinking 粒子、speaking 音波作为 P1 视觉打磨，不阻塞 M1 验收；
- 无自定义头像/无 job 徽标/无模式徽标。

**消息气泡**（对齐 `MessageBubble.kt`）

- 用户：`BrandIndigo` 背景，`TextPrimary`，右下角 8pt、其余 16pt；
- AI：`Surface` + 1pt `SurfaceVariant` 描边，左下角 8pt、其余 16pt；
- 最大宽度约屏宽 75%；
- 正文 16/24；
- AI final 流式结束后显示；`isFinal=false` 时显示闪烁光标；
- 与 Android 现行气泡一致，M1 不显示逐条时间戳（需要时后续批次统一补）；
- M1 仅纯文本；Markdown、图片、agent job 后置。

消息列表滚动：默认自动滚到底部；用户主动上滑后暂停自动滚动，滚回底部后恢复（对齐 Android `ConversationVoiceContent` 的逻辑）。

**实时转写**（对齐 `TranscriptionBubble.kt`）

- 固定在消息区底部、不挤占列表；
- `BrandIndigo` 88% 背景、12pt 圆角；
- 16/24 白字；非 final 时三点跳动动画；
- final 后转为用户消息并消失（Android 的延迟清理语义）。

**底部工具栏**（对齐 `BottomToolbar.kt` 的语音子集）

- M1 只保留麦克风、扬声器两项，`SpaceEvenly`；
- 48pt 圆形按钮；激活透明背景、无描边、图标 `BrandIndigo`，关闭 1pt `SurfaceVariant` 描边 + 图标 `TextSecondary`（Android `BottomToolbar` parity）；
- 图标 + 12pt caption；按压缩放 0.92；
- 麦克风开启时图标保持 1.0 不透明（不再随 `micLevel` 变暗）；
- 摄像头/图库/设备选择器不显示（M2）。

**错误 banner**（对齐 `ErrorBanner.kt`）

- 顶部滑入；recoverable 用 Warning、fatal 用 Error；
- 14/20 白字；recoverable 提供重试 + 关闭，5s 自动消失；fatal 常驻，可关闭/去设置；
- 断线/认证失败/重连耗尽文案区分。

**欢迎/空态**（对齐 `WelcomeGuide.kt` 的视觉，但收敛内容）

- 首次会话且无消息时展示卡片：标题、副标题（Android 现行文案）；
- M1 不显示示例 chips（无文本输入/本地文本上行路径）；
- 空态提示直接复用 Android `conv_status_idle`（“随时可以说”/“Say something anytime”），不新造文案。

**状态覆盖**

| 状态 | 视觉 |
|---|---|
| 无配置 | 阻断提示“去设置”，不启动连接 |
| 连接中 | 黄色状态 + 头像 IDLE + 欢迎/骨架 |
| 空会话 | 欢迎卡片 |
| LISTENING | 蓝色状态 + 头像状态 |
| THINKING | 黄色状态 + 流式文本 |
| SPEAKING | 绿色状态 + TTS |
| 断线 | 红色状态 + ErrorBanner + 重试 |
| 会话结束 | 红色/警告 banner + 重试 |
| 麦克风权限丢失 | 权限提示 + 去系统设置 |

### 7.7 Settings

对齐 Android `SettingsScreen.kt` 的“服务 / 通用 / 关于”，删除 Video（M1 无相机）：

```
┌─────────────────────────────────────┐
│ ←           设置                    │
│                                     │
│ 服务                                 │
│  服务器                              │
│  [ 192.168.1.10:8080        ▾ ]     │  44pt 输入框，聚焦 BrandIndigo 边框
│  认证密钥                            │
│  [ ••••••••••••             👁 ]    │  掩码 + 显示切换
│  检测服务              [可用/认证失败]│  16sp 文案 + 状态色
│  [            保存            ]      │  仅 dirty 时出现
│                                     │
│ 通用                                 │
│  触觉反馈                    [ON]    │
│                                     │
│ 关于                                 │
│  LivaChat               1.0.0.1     │
└─────────────────────────────────────┘
```

- 地址字段占位“ip:port 或 域名:port”；密钥占位“查看 server 配置文件获取”；
- 检测服务三态：可用（Success）/认证失败（Error）/不可用（Error）；
- 保存前校验；保存后确认“应用并重新连接”，执行 runtime 重建；
- M1 不显示 Video 区块；隐私政策/用户协议在 URL 明确后再接；
- 服务端预设不复制 Android 的开发 IP；M1 手工输入。

### 7.8 文案与本地化

- 文案基线直接复用 Android `values/strings.xml`（英文默认）与 `values-b+zh+Hans/strings.xml`（中文）；
- M1 先提供 **English + 简体中文** 两套；其他语言后置；
- 关键文案：

| Key | English | 简体中文 |
|---|---|---|
| splash_tagline | Your AI companion | 你的AI伙伴 |
| splash_waking | Waking up your companion... | 正在唤醒你的伙伴... |
| perm_dialog_title | Before we start, we need your permission | 开始之前，需要你的许可 |
| perm_mic_title | Microphone permission | 麦克风权限 |
| perm_mic_desc | So I can hear your voice and be here for you | 让我听见你的声音，更好地陪伴和倾听 |
| perm_start_chat | Start chatting | 开始聊天 |
| perm_mic_required_hint | Microphone permission is required to start chatting | 请先授权麦克风权限才能开始聊天 |
| perm_go_to_settings | Open system settings | 前往系统设置 |
| conv_status_idle | Say something anytime | 随时可以说 |
| conv_status_connecting | Connecting... | 正在连接... |
| conv_status_listening | I'm listening... | 我在听... |
| conv_status_thinking | Let me think... | 让我想想... |
| conv_status_disconnected | Disconnected | 连接断开 |
| conv_toolbar_mic_on/off | Mic on/off | 麦克风 已开启/已关闭 |
| conv_toolbar_speaker_on/off | Speaker on/off | 扬声器 已开启/已关闭 |
| settings_section_service | Service | 服务 |
| settings_section_general | General | 通用 |
| settings_section_about | About | 关于 |
| settings_server_label | Server | 服务器 |
| settings_auth_token | Auth key | 认证密钥 |
| settings_check_service | Check service | 检测服务 |
| settings_status_available | Available | 可用 |
| settings_status_auth_failed | Auth failed | 认证失败 |
| settings_status_unavailable | Unavailable | 不可用 |

### 7.9 无障碍

- 状态指示器合并读屏标签（颜色 + 文案）；
- 工具栏按钮、关闭/重试、保存等设置明确的 accessibilityLabel；
- 最小触控 44×44pt；
- 支持 Dynamic Type 换行；头像/工具栏固定尺寸；
- 触觉可在 Settings 关闭；
- 错误/状态不只靠颜色表达（同时有文案/图标）。

### 7.10 iPad 兼容

- M1 target 继续 `TARGETED_DEVICE_FAMILY = 1`、竖屏；
- iPad 以 iPhone 兼容模式运行，内容居中；不实现 iPad 原生分栏、横屏或 Split View；
- 使用自适应宽度/居中布局，避免未来切 Universal 时重写业务层；
- 原生 iPad 布局作为 M2 产品决策。

---

## 8. 主要交互

### 8.1 冷启动

```
Splash（≥1s）
  → 读取 isFirstLaunch、麦克风权限
      首次 或 无权限 → Permission
      否则           → Conversation
Permission 授权完成 → 标记首启完成 → Conversation
```

### 8.2 进入会话

```
Conversation 出现
  → 配置缺省 → 阻断提示 / 去 Settings
  → 配置完整 → Coordinator.start()
      获取 Keychain deviceID
      构造 AudioPlaybackController（sink）→ LivaChatSDK
      EventAdapter 开始消费
      sdk.connect()
      capture.start + playback.start + interrupt.start
  → epoch → startSession(resume)
  → sessionReady → 状态 LISTENING
```

### 8.3 一轮对话

```
麦克风 PCM → sdk.pushAudioPCM
  → SDK VAD/AGC/Opus → WS
  → transcription interim（转写浮层）
  → transcription final（用户消息）
  → status THINKING → aiText 流式（AI 气泡）
  → aiText final（定型）
  → status SPEAKING → ai_audio → audioSink
  → JitterBuffer → AVAudioPlayerNode（epoch 过滤）
  → status LISTENING
```

### 8.4 静音/扬声器

- 麦克风：`capture.setMuted`；1s 静音补发后停推；UI 图标/caption 更新；
- 扬声器：播放器 volume 0/1，不停止 jitter buffer/连接；
- 设备选择器不在 M1。

### 8.5 语音打断

```
sessionState == SPEAKING 且 localVad == SPEECH
  → InterruptCoordinator（500ms 节流）
  → sdk.sendInterrupt(.userVoice) + 轻触觉
  → 服务端新回合
  → SDK playbackShouldFlush(.interruptAck, epoch)
  → 播放器丢弃旧 epoch 帧
```

### 8.6 断线重连

- 连接 RECONNECTING → UI 黄色“正在连接...”；
- epoch → startSession(serverSessionID)；
- 成功 → 继续；restored=false → 清空内存消息与临时文本；
- 重试耗尽/认证失败 → 状态/错误 banner；认证失败引导 Settings。

### 8.7 设置变更

- 进入 Settings 页本身不停止 runtime；只有保存并应用配置时才重建；
- 保存 → 校验/检测 → 确认应用；
- 停媒体/事件消费 → 清 sessionID/内存消息/临时文本 → 重置播放 epoch → 重建 runtime → 重连；
- 取消 → 保持原配置与 runtime。

### 8.8 前后台

- M1 只保证前台；不启用后台音频；
- 后台：停止采集/播放并释放音频会话；连接由系统处理；
- 回前台：重启采集/播放；若 DISCONNECTED → `connect()` 自愈；
- 返回后若服务端会话已失效，按 `sessionReady(restored=false)` 处理。

### 8.9 会话结束/断开

- `sessionEnded`/`serverClosed`：清 serverSessionID/临时状态，停止媒体，显示结束 + 重试；
- `authFailed`：停止自动重试，去 Settings；
- `reconnectExhausted`：DISCONNECTED + 状态指示器重试；
- M1 不提供显式“结束会话”按钮；App 退出由系统清理。

---

## 9. 测试计划

### 9.1 单元测试（fake 驱动）

| 范围 | 用例 |
|---|---|
| EventAdapter | epoch → startSession；sessionReady 保存/清空；sessionEnded 清 serverSessionID；断连迟到帧门控；transcription/aiText final 消息 |
| Coordinator | start/stop 顺序；重连 resume；settings rebuild 清 ID/重置 epoch；sessionActive 门控 |
| Playback | `acceptedEpoch` 单调；flush 丢旧留新；JitterBuffer 并发；静音；sink 快速返回 |
| Capture | mute 1s 补发；unmute；采集代次/重试（可在真实/模拟器边界测试） |
| Settings | wire token；Keychain fake；地址校验；probe 三态；TLS 策略一致 |
| ViewModel | 状态映射（连接/会话/头像/文案）；错误 banner 生命周期；配置缺失门控 |

### 9.2 Mock Server 集成

- 使用 `make mock-server`（`tools/local-dev/mock-services/livachat_mock_server`）；
- 模拟器使用 `127.0.0.1`；iPad 真机使用 Mac 局域网 IP；
- 覆盖：connect → session.ready → status listening → 转写/AI 文本 → TTS → 打断 → 断连 → 重连/restore；
- 音频可用合成 PCM 注入（fake capture）跑通上行；真实 TTS/麦克风在真机验证；
- 注意 ATS / 自签名 TLS / 本地网络权限；`ServerProbe` 与 SDK 使用同一 TLS 策略。

### 9.3 UI 测试

- M0 的 `M0ScreenshotUITests` 依赖 `M0View`/`m0_sdk_status`，在 M0 页面移除后同步替换；不把 M0 页面作为 M1 回归目标；
- SDK 链接 smoke 从 `M0SDKLinkageTests` 迁移到 M1 runtime 初始化测试；
- M1 新增静态状态 UI 测试（可用 debug launch argument 注入 fake runtime/state，不联网）：
  - Permission 页麦克风卡片/开始按钮；
  - Settings 服务区块（地址/密钥/检测/保存）；
  - Conversation 的 IDLE/LISTENING/THINKING/SPEAKING/DISCONNECTED 状态与错误 banner；
- 真机 UI 截图继续用 `XCUIScreen` 附件导出（XCUITest target 已存在）。

### 9.4 真机验收（iPad Pro 10.5 / iPadOS 17.7.5）

- 配置服务器 → 检测可用；
- 首次麦克风授权；
- 说话 → 实时转写 → AI 文本 → TTS 可听；
- 语音打断；
- 静音/取消静音、扬声器静音；
- 断网/停 Mock Server → 重连恢复；
- 设置切换服务器 → 重建 runtime → 重连；
- 无相机/录屏/模式/历史/后台音频；
- 截图、日志、崩溃/内存检查。

### 9.5 构建与回归

- `./scripts/build-ios.sh sdk`（或 `make build-ios`）不回归；
- `./scripts/build-ios.sh app` 签名构建通过；
- `xcodebuild test`（单元 + UI）通过；
- SDK 链接/native 初始化由 M1 runtime 测试覆盖；M0 页面被替换，不要求 M0 UI 用例原样保留；
- iPad 构建/运行不回归。

---

## 10. 实施顺序

### M1.0 设计与决策（先于代码）

1. 写 `docs/ios_app_audio.md`、`docs/ios_app_conversation.md`、`docs/ios_app_settings.md`、`docs/ios_app_ui.md`、`docs/ios_app_shell.md`；
2. 锁定：`AVAudioPlayerNode` 播放、sessionEnded 行为、后台策略、文案 en+zh-Hans、无 Android 服务器预设、M1 无 Markdown；
3. 建立测试缝协议与 fake 边界。

### M1.1 Audio Spike（最高风险先做）

先完成 §6.4 的 `NSMicrophoneUsageDescription` 配置；spike 可只做本地采集/播放，不接业务 UI。

4. `AudioSessionController`（voiceChat/AEC/路由/中断）；
5. `AudioCaptureController`（tap/converter/16k/mute/micLevel）；
6. `AudioPlaybackController`（sink/JitterBuffer/epoch/player）；
7. `InterruptCoordinator` + 触觉；
8. 在 M0 App 内用临时 debug harness 验证采集/播放/回声/打断；不接完整业务 UI。

### M1.2 Runtime（无真实音频）

9. `ConversationEventAdapter` + `ConversationCoordinator` + `ConversationRuntime`；
10. 用 debug 配置/环境变量提供 Mock Server 地址；用 fake capture/playback 跑 connect → session → 转写/AI 文本 → 断开；
11. 单测：epoch/resume、sessionEnded、迟到帧门控、错误映射。

### M1.3 Settings / 权限 / AppShell

12. `CredentialStore`（Keychain）+ `SettingsStore`（UserDefaults）+ `ServerProbe`；
13. 服务器配置页、检测服务、保存/重建 runtime；
14. Splash/Permission/AppRouter/AppLifecycle；麦克风权限申请与设置引导。

### M1.4 UI/UX

15. 深色主题与 Android token 对齐；
16. Conversation 语音页（状态、头像、消息、转写、工具栏、错误 banner、欢迎）；
17. Settings/Permission/Splash 视觉实现；
18. 不做未实现功能的死控件。

### M1.5 集成与验收

19. Mock Server E2E（模拟器 + iPad）；
20. 断线重连/restore/设置重建；
21. UI 测试与构建/iPad 运行回归；
22. 真机录屏/截图/日志；更新 `docs/ios_app_m1.md` 实施记录。

---

## 11. M1 验收标准

- [x] 服务器配置可保存/检测，配置不全时不启动连接（单测；真实服务器检测待 E2E）；
- [x] 首次启动麦克风授权流程完整，永久拒绝可去系统设置（UI 测试）；
- [x] 进入会话自动 connect + startSession；重连按 `serverSessionID` resume（单测 + Mock Server 文本流）；
- [x] 能完成一轮“用户说话 → 转写 → AI 文本 → TTS 播放”（Mock 文本流 + iPad 真机人工验收）；
- [x] 语音打断生效，且不依赖本地 flush（逻辑与单测 + iPad 真机人工验收）；
- [x] 静音/扬声器开关有效，麦克风 1s 静音补发（单测）；
- [x] 断网/停服后重连恢复；restored=false 时清空临时/内存消息（`resume_drop` mock E2E + 单测）；
- [x] 设置切换服务器后 runtime 重建、播放 epoch 重置、重新连接（真实 wss server 人工验收）；
- [x] 无相机/录屏/模式/agent job/历史/后台音频代码；
- [x] iPhone-only target 仍为 iOS 15；模拟器与 iPad Pro 10.5 兼容模式验收通过；
- [x] 单元测试、Mock Server E2E、UI 测试、真机冒烟全部通过（模拟器 104 unit + 6 UI、iPad 88 unit + 6 UI；1 个 Mock E2E 默认跳过；真实 TTS 听感已人工确认）；
- [x] SDK 链接/native 初始化由 M1 runtime 测试覆盖；M0 页面与 M0 UI 用例已删除；
- [x] iPhone 11 仍未用于 M1（Xcode 26.x 待补）。

---

## 12. 风险与待决策

| 风险/决策 | 影响 | 处理 |
|---|---|---|
| AVAudioEngine 采集/AEC/回声 | 打断误触发、TTS 被当成用户语音 | 先做音频 spike；开启 voice processing；真机验证 |
| JitterBuffer 并发/epoch | 断连/打断后旧帧播放、卡顿 | 加锁/串行；acceptedEpoch max；flush 只丢旧帧 |
| 后台策略 | 无 `UIBackgroundModes: audio`，后台不可通话 | M1 明确前台验收；后台只释放/自愈 |
| 自签名 TLS 与本地网络 | Settings 检测或 WS 连接失败（iOS 默认拒绝自签证书，Android SDK 为 trust-all） | Settings 的“允许自签名证书”开关让 ServerProbe 与 SDK 同用 `.allowSelfSigned`；配置 ATS/本地网络 |
| Mock Server 地址 | iPad 通过局域网 IP 连接；模拟器 127.0.0.1 | 文档化；避免写死 IP |
| 运行时重建 | 旧 epoch/旧 sessionID 泄漏 | 重建前清 serverSessionID、消息、临时文本、播放 epoch |
| 文案与本地化 | 与 Android 不一致 | en + zh-Hans 先复用 Android 字符串；其他后置 |
| iPad 兼容模式 | 原生布局不是 M1 目标 | target family 1；M2 再决策 Universal |
| 范围膨胀 | 相机/模式/历史混入 M1 | 本文非目标清单强制执行 |
| 设置保存即重建 | 通话中被中断 | 保存前确认；明确会重新连接 |

---

## 13. 模块文档与文件索引

| 主题 | 文件（待创建/更新） |
|---|---|
| M1 总设计 | `docs/ios_app_m1.md`（本文） |
| App 架构 | `docs/ios_app_arch.md` |
| M0 记录 | `docs/ios_app_m0.md` |
| SDK 契约 | `docs/ios_sdk_design.md` |
| 会话 Domain 设计 | `docs/ios_app_conversation.md` |
| 音频设计 | `docs/ios_app_audio.md` |
| 设置/Keychain 设计 | `docs/ios_app_settings.md` |
| UI/UX 设计 | `docs/ios_app_ui.md` |
| AppShell/生命周期 | `docs/ios_app_shell.md` |
| Android UI/UX 基线 | `apps/android/LivaChatApp/.../ui/`（上述源码路径） |
| iOS SDK | `apps/ios/LivaChatSDK/Sources/LivaChatSDK/` |
| iOS M0 App | `apps/ios/LivaChatApp/` |

---

## 14. 实施记录（2026-10-20）

### 14.1 已落地

| 阶段 | 内容 | 结果 |
|---|---|---|
| M1.0 | `docs/ios_app_audio.md`、`conversation.md`、`settings.md`、`ui.md`、`shell.md` | 5 份模块文档完成 |
| M1.1 | `AudioSessionController`、`AudioCaptureController`、`AudioPlaybackController`、`JitterBuffer`、`InterruptCoordinator`、`HapticController`、纯逻辑 `PCMChunker`/`SilenceFlushGate`/`MicLevelMeter`；DEBUG `AudioSpikeView` | 模拟器音频引擎/AEC/50 帧测试音通过；iPad 真机待跑 |
| M1.2 | `ConversationEventAdapter`、`ConversationCoordinator`、`ConversationRuntime`、`ConversationRuntimeFactory`、`ConversationViewModel` | 22 个 coordinator 单测 + event adapter/viewmodel 单测；Mock Server `text_on_start` 真实 WebSocket 流程通过 |
| M1.3 | `ServerAddress`/`WireToken`、`KeychainCredentialStore`、`SettingsStore`、`URLSessionServerProbe`、`SettingsViewModel`、`AppRouter`、`AppLaunchOptions`、`AppContainer`、显式 `Info.plist`（本地网络 ATS） | 设置/Keychain/Probe/AppShell 20 个单测通过 |
| M1.4 | 深色 token、Splash/Permission/Conversation/Settings、消息气泡/转写浮层/工具栏/ErrorBanner/欢迎态、en+zh-Hans String Catalog、UI 测试注入 | 5 个 AppShell UI 测试通过；M0 页面与 M0 UI 用例已删除 |

### 14.2 测试结果

```bash
# 模拟器（iPhone 16 Pro, iOS 18.6）
xcodebuild test -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj -scheme LivaChatApp \
  -destination 'platform=iOS Simulator,id=AD9096F3-27D8-4FDC-8571-D8D370D54651'

# 结果：104 unit tests / 1 skipped / 0 failures；6 UI tests / 0 failures
```

- Mock Server 集成测试默认跳过；设置 `/tmp/livachat_mock_server_url` 与 `/tmp/livachat_mock_token`，并以 `MOCK_SCENARIO=text_on_start` 启动 mock 后运行 `ConversationRuntimeIntegrationTests`，已通过。
- Mock Server 新增 `/healthz_token`、`text_on_start`、`resume_drop` 场景；mock 全量 pytest 28/28 通过。
- App Shell 真实 E2E：模拟器以 DEBUG 环境变量连接 `text_on_start` mock，完成 connect → sessionReady → 用户转写 → AI 文本 → listening，UI 显示两段中文消息（截图 `/tmp/livachat_m1_5_app_e2e.png`）。
- 重连/恢复 E2E：`resume_drop` mock 首连发送 ready 后关闭，SDK 自动重连并带 `serverSessionID` 重新 startSession；服务端返回 `restored=true` 并推送文本流，App 恢复 listening 且显示消息（截图 `/tmp/livachat_m1_5_resume_e2e.png`）。
- iPad 真机：88 unit tests（1 skipped）/ 6 UI tests 全部通过；`AudioSpikeUITests` 断言 tap/uplink > 0、AEC enabled、mute 窗口后停止发送、unmute 恢复、打断模拟计数。
- iPad 音频实测：`taps=61 samples=97582 uplink=304 scheduled=50`，AEC enabled；采集/播放链路可用。
- SDK 回归：`RUN_IOS_TESTS=0 ./scripts/build-ios.sh sdk`，66/66 通过；Release 模拟器构建 `BUILD SUCCEEDED`。
- 签名构建：`./scripts/build-ios.sh app`（GUI Terminal），`BUILD SUCCEEDED`，exit 0。
- 真实 LAN server 连接：开启自签名开关后，设备日志出现 `connection state=connected`、`session ready restored=false id=sess_...`、`session state=listening/thinking`。
- 统一日志导出验证：`devicectl device copy from --domain-type appDataContainer --domain-identifier dev.livachat.app --source Library/Logs/livachat.log` 成功，文件内 App/SDK 行混排且首字段为 ISO 8601 UTC 微秒。
- 截图证据：`/tmp/livachat_m1_3_permission2.png`、`/tmp/livachat_m1_3_settings.png`、`/tmp/livachat_m1_3_conversation.png`、`/tmp/livachat_m1_5_app_e2e.png`、`/tmp/livachat_m1_5_resume_e2e.png`；设备截图见 `build/device-m1/Logs/Test/*.xcresult` 附件。

### 14.3 真机人工验收（2026-10-20 完成）

| 项 | 结果 |
|---|---|
| 真实服务器连接/设置检测 E2E | 通过（真实 LAN server + 自签名 TLS，SDK/Probe 同策略） |
| 设置切换服务器后重建 E2E | 通过（真实 wss server，runtime 重建、epoch 重置、重连正常） |
| 真实用户说话 → TTS 可听 | 通过（真实麦克风/TTS，听感与 loopback 回声人工确认） |
| 后台/中断真机回归 | 通过（后台切换与来电中断人工回归） |

### 14.4 实现备注

- 音频播放连接格式为 **Float32 mono 16 kHz**（`AVAudioPlayerNode` 混合器总线不接受 Int16）；采集侧仍为 Int16 16 kHz。
- 引擎启动顺序：`activate()` → `capture.start()`（安装 tap 并启动引擎）→ `playback.start()` → `startEngine()`（幂等）。
- App 使用显式 `Resources/Info.plist`：`NSMicrophoneUsageDescription`、`NSLocalNetworkUsageDescription`、`NSAppTransportSecurity.NSAllowsLocalNetworking`；不启用 `UIBackgroundModes: audio`。两段权限文案另有 en/zh-Hans `InfoPlist.strings`。
- `AVAudioEngine.configurationChangeNotification` 不单独观察：安装/移除 tap 自身会触发该通知，直接重启会形成循环；M1 由 route change 覆盖硬件路由变化，并在 `activate/startEngine` 后 1.5s 抑制自身 setCategory/engine 引起的 route change，避免 stop/start 循环。
- 自签名服务器：Android SDK 默认 trust-all；iOS 通过 Settings → Service → Allow self-signed certificate（默认开启）显式开启 `.allowSelfSigned`，SDK 与 ServerProbe 同策略。
- 统一日志文件：App + SDK 写入 `Library/Logs/livachat.log`；每行首字段为 ISO 8601 UTC 微秒时间，例如 `2026-10-20T07:28:56.598000Z I/App/Conversation ...`；不使用设置页导出，调试时用 `xcrun devicectl device copy from` 拉取（见 `docs/ios_app_shell.md` §10）。
- `connectionEpochAdvanced` 的自动 startSession 门控包含 `!sessionEnded`：服务端结束会话后 SDK 静默重连不会重开 session（单测覆盖）。
- 采集启动失败：初始尝试 + 最多 3 次重试（200/400/800ms）；engine.start 失败先移除 tap；转换错误触发管线重建，连续 3 次转换失败仍未恢复则停止并 onFailure；单测覆盖重试次数/耗尽上报。
- ServerProbe 禁止 HTTP 重定向（delegate 返回 nil），并补 TLS/重定向单测；避免代理 302→200 被误判可用。
- 消息列表自动滚底仅在用户位于底部 80pt 内生效；上滑暂停，回到底部恢复（DragGesture + 底部位置 PreferenceKey）。
- 设置页产品输入仍只接受 `host:port`（默认 wss）；Mock Server 联调走 DEBUG 环境变量 `LIVACHAT_SERVER_URL`/`LIVACHAT_SERVER_TOKEN`。
- M1.4 的 UI 测试通过 Debug 启动参数注入静态状态；UI 测试不联网、不开麦克风；`AudioSpikeUITests` 例外，只测本地音频引擎。
