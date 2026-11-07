# LivaChat iOS App 架构设计

**状态**：已实施（后台音频；R4 回归单 engine）
**日期**：2026-10-24
**依据**：
- `docs/ios_sdk_design.md`（SDK 公开契约、宿主 App 集成 §7、典型流程 §8、决策 §11）
- `docs/ios_dev_rules.md`（iOS 15 / Swift + SwiftUI / SPM）
- `apps/ios/LivaChatSDK/Sources/LivaChatSDK/`（已实现的 SDK 公开接口）
- `docs/ios_protocol_dto.md`（协议 DTO 生成与约束）

**范围**：`apps/ios/LivaChatApp` 的 App 架构、模块边界与主要交互。
**不展开**：各模块的内部实现。音频、视频、采集、播放、持久化、设置等模块的详细设计分别单独成文；本文只定义它们之间的职责、依赖与契约。

> 本文中“SDK 已实现”指 `apps/ios/LivaChatSDK` 当前代码；“App 设计”指 `apps/ios/LivaChatApp`（M0 骨架已落地，产品功能待实现）。凡未在 SDK 文档或开发规范中出现的能力，本文不假定其存在。
> `docs/ios_sdk_design.md` 的状态行已更新为“SDK 已实现；App M0 骨架已落地”；涉及 SDK 行为时仍以实际代码与公开接口为准。

---

## 1. 总体架构

iOS App 采用 **SwiftUI + MVVM + 单向数据流 + 手动依赖注入**。SDK 负责实时协议与音视频数据处理，App 负责平台媒体 I/O、状态编排、持久化和 UI。

```
┌──────────────────────────────────────────────────────────────────────┐
│ apps/ios/LivaChatApp (SwiftUI, iOS 15+)                              │
│                                                                      │
│  AppShell                                                            │
│    LivaChatApp · AppContainer · AppRouter · AppLifecycleController   │
│                         │                                            │
│  Features (SwiftUI)                                                  │
│    Onboarding/Permission · Conversation · Settings · History         │
│                         │ @Published / user intents                   │
│  ViewModels (@MainActor ObservableObject)                            │
│                         │                                            │
│  Domain                                                              │
│    ConversationCoordinator · ConversationEventAdapter                │
│    Session/Mode/Recovery policies · UseCases                         │
│                         │                                            │
│  Media (App-owned)                                                   │
│    AudioSessionController · AudioCaptureController                   │
│    AudioPlaybackController · InterruptCoordinator                    │
│    CameraController · ScreenCaptureController · GalleryPhotoSender   │
│                         │ PCM / JPEG                                 │
│  Data                                                                │
│    ConversationStore · SettingsStore · CredentialStore · ServerProbe │
│                         │                                            │
│  Platform Adapters (UIKit 例外，隔离)                                  │
│    CameraPreviewView · PHPickerView                                  │
└──────────┬───────────────────────────────────────────────────────────┘
           │ LivaChatSDK（本地 SPM package）
           ▼
┌──────────────────────────────────────────────────────────────────────┐
│ LivaChatSDK (implemented)                                            │
│  LivaChatSDK Facade                                                  │
│    events: AsyncStream<LivaChatEvent>                                │
│    audioSink: LivaChatAudioSink                                      │
│    state: LivaChatState                                              │
│    connect/startSession/pushAudioPCM/pushVideoJPEG/sendInterrupt...  │
│  ConnectionManager · SessionManager · Audio/Video pipelines          │
│  Opus / WebRTC VAD / JPEG                                             │
└──────────┬───────────────────────────────────────────────────────────┘
           │ WSS + JSON protocol
           ▼
        services/gateway
```

### 1.1 分层原则

| 原则 | 约定 |
|---|---|
| App/SDK 边界 | SDK 只做协议、会话、VAD/Opus/JPEG 处理；麦克风、播放器、摄像头、录屏、音频会话、权限全部由 App 持有 |
| 单向数据流 | ViewModel 接收用户意图 → Coordinator/Media 执行 → SDK 事件 → EventAdapter → 状态 → View |
| 单一事件消费者 | `sdk.events` 是单消费者流，只允许 `ConversationEventAdapter` 消费；其余模块订阅 App 层 fan-out |
| 音频旁路 | `LivaChatAudioSink` 单独回调，不经过 `AsyncStream`，不进入 MainActor |
| 队列封闭 | 实时音视频路径使用各自串行队列；UI/状态统一在 MainActor |
| 配置不可变 | `LivaChatConfig` 在 SDK 实例生命周期内不可变；服务器配置变化通过受控重建 runtime 生效 |
| 平台隔离 | UIKit 仅存在于 `PlatformAdapters`；Domain/Media 业务对象不得依赖 UIKit |
| 独立模块文档 | 本文只定义模块职责、依赖、对外契约；模块内部设计见各自文档 |

### 1.2 与 Android 架构的对应关系

| Android | iOS |
|---|---|
| Hilt DI | `AppContainer` 组合根 + 构造注入（不引入第三方 DI） |
| Navigation Compose | `AppRouter` + iOS 15 的 `NavigationView`/自定义路由 |
| ViewModel + StateFlow | `@MainActor ObservableObject` + `@Published` 状态快照 |
| `LivaChatListener` + `CompositeLivaChatListener` | `AsyncStream<LivaChatEvent>` 单消费者 + EventAdapter fan-out |
| AudioRecord / AudioTrack | `AVAudioEngine` 采集/播放（App-owned） |
| CameraX | `AVCaptureSession` / `AVCaptureVideoDataOutput` |
| MediaProjection | ReplayKit（范围待 App 阶段决策） |
| 前台服务保录音 | 无 Android FGS 等价物；启用 `UIBackgroundModes: audio`，由 Coordinator 策略维持后台 full（mic 未静音时） |
| Room / DataStore | `ConversationStore` / `SettingsStore`；具体持久化选型在模块文档中定稿 |
| MyLife BFF | 本设计不覆盖；时光机另立 `docs/ios_timeline_arch.md` 及各子页文档 |

---

## 2. 模块划分

> 下表中的“独立文档”列表示该模块的详细设计单独成文；本文不展开其内部实现。

| 模块 | 架构职责 | 依赖 | 独立文档 |
|---|---|---|---|
| AppShell | App 入口、`AppContainer` 组合根、路由、前后台/中断等平台生命周期聚合 | 所有 App 模块 | 本文 §3 + 独立文档 |
| Onboarding / Permission | 首启、麦克风/摄像头/本地网络权限、引导进入会话 | 系统权限 API、SettingsStore | 独立文档 |
| Conversation Feature | 会话页 SwiftUI View、ViewModel、用户意图转换、状态映射 | Domain、Media、Data | 独立文档 |
| Settings Feature | 服务器地址/密钥、偏好设置页面 | Data | 独立文档 |
| History Feature | 本地会话列表/详情（产品范围见 §10） | Data | 独立文档 |
| Conversation Domain | `ConversationCoordinator`、`ConversationEventAdapter`、会话/模式/恢复策略 | SDK、Media、Data | 独立文档 |
| Audio | `AVAudioSession` 配置、采集、播放、抖动缓冲、打断、音量/路由 | SDK、AVFoundation、PlatformAdapters（触觉协议） | 独立文档 |
| Video | 摄像头、屏幕采集、图库照片、采集轮 `turnID` | SDK、AVFoundation、ReplayKit、PlatformAdapters（PHPicker） | 独立文档 |
| Data / Persistence | 本地会话与消息、设置、凭证、服务预检 | 系统持久化框架、Keychain、URLSession | 独立文档 |
| Platform Adapters | UIKit 桥接：相机预览层、PHPicker、触觉反馈 | UIKit、AVFoundation | 独立文档（含例外审批） |
| Diagnostics | SDK/App 日志、诊断事件、脱敏 | SDK、os.Logger | 独立文档 |

### 2.1 依赖方向

```
Features ──► ViewModels ──► Domain ──► Media ──► LivaChatSDK
    │                         │          │
    │                         └──────────┴──► Data
    │
    └──► PlatformAdapters（UIKit 唯一落点；上层经协议依赖）
```

约束：

- `Domain` 不依赖 SwiftUI/UIKit；只依赖 SDK、Media 的协议接口与 Data 协议。
- `Media` 不依赖 View/ViewModel；只依赖 SDK、AVFoundation，以及 PlatformAdapters 暴露的协议（如触觉）。
- `Data` 不依赖 UI/Media；只依赖系统框架与 SDK 的模型值类型。
- View 不得直接调用 `LivaChatSDK`、`AVAudioEngine`、`AVCaptureSession`。
- PlatformAdapters 是 UIKit 的唯一落点；View/Media 通过协议接口使用（如相机预览宿主、PHPicker 呈现、触觉）。
- 只有 `ConversationEventAdapter` 消费 `sdk.events`。

---

## 3. 启动、装配与导航

### 3.1 组合根与 SDK 装配

App 启动时由 `@main` 的 `LivaChatApp` 创建唯一的 `AppContainer`：

```
AppContainer
 ├─ AppRouter / AppLifecycleController
 ├─ SettingsStore + CredentialStore（Keychain）+ ServerProbe
 ├─ ConversationStore
 ├─ PermissionService
 ├─ AudioSessionController
 ├─ AudioCaptureController
 ├─ AudioPlaybackController（实现 LivaChatAudioSink）
 ├─ CameraController / ScreenCaptureController / GalleryPhotoSender
 ├─ HapticController（UIKit 适配）
 ├─ InterruptCoordinator
 └─ ConversationRuntime
      ├─ LivaChatSDK（进程内同一时刻唯一实例）
      ├─ ConversationEventAdapter（唯一消费 sdk.events）
      └─ ConversationCoordinator
```

装配规则（来自 `docs/ios_sdk_design.md` §7.1 与 SDK 公开接口）：

- `serverURL`：`wss://<host:port>/v1/ws/stream`；不带查询参数，查询参数由 SDK 拼接。
- `token`：`sha1_hex(secret + "LivaChat")` 的小写 hex；明文 secret 只存 Keychain，不写日志。
- `deviceID`：UUID v4，持久化到 Keychain；卸载重装保留。
- `clientVersion`：`ios-<CFBundleShortVersionString>.<CFBundleVersion>`。
- `language` / `languageProvider`：App 传 `AppLocale.resourceCode` 与同一个动态 provider，SDK 每次 `session.start` 现取并写入 `config.language`。
- `audioSink`：SDK 只持弱引用，且仅在初始化时注入（运行期不可更换）；App 必须强持有 `AudioPlaybackController`，并在 SDK 初始化前构建。重建 runtime 时重新注入同一个 sink。
- `logFileURL`：App Support/Logs 下的文件；SDK 负责写入。
- `tlsPolicy`：默认 `.system`；自签名/内网场景显式 `.allowSelfSigned`。
- `audioSampleRate = .hz16000`、`audioFrameMs = 20`；SDK 在初始化时拒绝其他值。

**配置变更策略（App 设计决策）**：`LivaChatConfig` 不可变，因此服务器地址/密钥变更时不修改现有 SDK 实例，而是：
1. 结束当前会话与媒体，并停止 `InterruptCoordinator`；
2. 停止事件消费；
3. 清空 `serverSessionID`、模式标志与临时 UI；
4. 重置播放控制器的 JitterBuffer/acceptedEpoch（或重建播放控制器）——新 SDK 的 `playbackEpoch` 从 1 重新开始；
5. 销毁并重建 `ConversationRuntime`（SDK + EventAdapter + Coordinator）；
6. 用新配置重新连接。

该策略不要求杀进程；具体设置页交互由设置模块文档定义。

### 3.2 启动流程

```
App 启动
  → LivaChatApp 创建 AppContainer
  → AppRouter 判断首启 / 权限状态
      麦克风未授权 或 首次使用 → Onboarding/Permission
      否则                     → Conversation
  → Permission 完成
      → 写入首启标记
      → 进入 Conversation
```

- 麦克风为必需权限；摄像头为可选权限；图库使用 `PHPickerViewController` 时不要求相册全量权限。
- 局域网服务器需要 `NSLocalNetworkUsageDescription`；首次连接会触发系统本地网络授权。
- 权限被拒绝或永久拒绝时，入口应提供“前往系统设置”的引导；不得直接启动采集。

### 3.3 导航

iOS 15 不支持 `NavigationStack`（iOS 16+），因此：

- 使用 `NavigationView` + `AppRouter`（`@MainActor ObservableObject`）作为导航事实源；
- 若后续改用 `NavigationStack`，必须通过 `if #available(iOS 16, *)` 提供 iOS 15 降级路径；
- 路由表建议：

```
root
 ├─ onboarding/permission
 ├─ conversation          （主页面）
 ├─ settings
 └─ history               （本地会话历史；产品范围见 §10）
```

- 路由只持有值类型参数（会话 id、语言、模式），不跨路由传递 SDK/媒体对象。
- 会话页是唯一启动 SDK 连接与媒体采集的页面。

### 3.4 工程目录（目标状态）

```
apps/ios/
├── LivaChatSDK/                         # 已实现（本地 SPM package）
└── LivaChatApp/
    ├── LivaChatApp.xcodeproj
    ├── App/
    │   ├── LivaChatApp.swift             # @main
    │   ├── AppContainer.swift            # 组合根 / 依赖装配
    │   ├── AppRouter.swift               # iOS 15 路由
    │   └── AppLifecycleController.swift  # scenePhase / 媒体生命周期聚合
    ├── Features/
    │   ├── Onboarding/
    │   ├── Conversation/
    │   ├── Settings/
    │   └── History/
    ├── Domain/
    │   ├── ConversationCoordinator.swift
    │   ├── ConversationEventAdapter.swift
    │   └── UseCases/
    ├── Media/
    │   ├── Audio/
    │   ├── Video/
    │   └── Interrupt/
    ├── Data/
    │   ├── ConversationStore/
    │   ├── Settings/
    │   └── Networking/
    ├── PlatformAdapters/
    │   ├── CameraPreview/
    │   ├── PHPicker/
    │   └── Haptics/
    ├── DesignSystem/
    └── Resources/
```

- 目录只表达模块边界；各目录内部的类型、协议与实现由对应模块文档定义。
- `LivaChatApp` 只通过本地 SPM 依赖 `LivaChatSDK`，不直接链接 Opus/VAD XCFramework。

---

## 4. 状态与数据流

### 4.1 状态事实源

| 状态 | 事实源 |
|---|---|
| 连接/会话/VAD/播放代际 | `sdk.state` 快照 + `AsyncStream<LivaChatEvent>` |
| 服务端 sessionID | `ConversationEventAdapter.serverSessionID` |
| 会话是否活跃、本地会话 id | `ConversationCoordinator` |
| 实时转写、流式 AI 文本、模式镜像、agent jobs、终态错误 | `ConversationEventAdapter` → App 状态 |
| 消息/会话历史 | `ConversationStore`（最终消息落库；流式文本不落库） |
| 采集/播放/相机/录屏/音频路由 | 各 Media 控制器 |
| 用户偏好、服务器地址、凭证 | `SettingsStore` / `CredentialStore` |
| 页面弹窗、查看器、选中设备等 | 各 ViewModel 的本地状态 |
| 导航栈 | `AppRouter` |

### 4.2 事件 fan-out

`AsyncStream<LivaChatEvent>` 是单消费者流。App 的 fan-out 结构固定为：

```
LivaChatSDK.events
        │ 唯一 Task
        ▼
ConversationEventAdapter
        ├─► ConversationState（@MainActor，UI 状态）
        ├─► ConversationStore（最终文本落库，异步）
        ├─► ConversationCoordinator（epoch/ready/ended/terminated 生命周期信号）
        └─► InterruptCoordinator（sessionState + localVadState）
```

规则：

- 任何其他模块不得直接 `for await sdk.events`。
- ViewModel 不直接订阅 SDK；只订阅 EventAdapter 暴露的状态/事件。
- EventAdapter 在 Main 上处理事件，但把持久化、下载、图片处理等重活投递到后台。
- `connectionStateChanged` / `sessionStateChanged` / `localVadStateChanged` 也走事件流；需要同步读取时使用 `sdk.state` 原子快照。
- SDK 事件在同一来源内有序，跨来源不保证全局顺序；App 不依赖 `.sessionReady` 与 `.sessionStateChanged` 的先后，必要时以 `sdk.state` 快照门控。
- EventAdapter 以最近一次 `.connectionStateChanged` 快照门控 `.transcription` / `.aiText` / `.jobStatus`；断连后迟到的非 final 帧与任务快照必须丢弃，不能复活已清空的临时 UI。
- `ConversationCoordinator` 与 EventAdapter 通过回调/`AsyncStream` 解耦（EventAdapter 不持有 Coordinator），避免构造引用环。

EventAdapter 的事件映射：

| SDK 事件 | App 层处理 |
|---|---|
| `.connectionStateChanged(state)` | 更新连接状态；非 `.connected` 时清空临时 UI（流式文本、转写、job 列表） |
| `.connectionEpochAdvanced(epoch)` | `sessionActive && connected` → Coordinator 调用 `sdk.startSession(resumeSessionID:)` |
| `.connectionTerminated(reason)` | 按 `authFailed` / `reconnectExhausted` / `serverClosed` 分流；用户主动断连不触发 |
| `.sessionStateChanged(state)` | 更新会话状态与 UI 动效 |
| `.localVadStateChanged(state)` | 更新 VAD 状态；供 InterruptCoordinator 与 UI 使用 |
| `.sessionReady(id, restored)` | 保存 `serverSessionID`；`restored == false` 时清空本地消息缓存；复位模式并恢复用户模式意图 |
| `.sessionEnded(reason, id)` | 清空 `serverSessionID`；停止本地转写/任务展示 |
| `.transcription(text, isFinal, id)` | 中间态进内存；final 写 `ConversationStore` |
| `.jobStatus([AgentJob])` | 全量替换 job 列表 |
| `.aiText(...)` | 按 `messageID` 累积；final 写 `ConversationStore`（含可选图片） |
| `.playbackShouldFlush(reason, epoch)` | 交给播放模块按 `playbackEpoch` 过滤 |
| `.sessionChanged(...)` | 以服务端回声重置模式标志 |
| `.error(code, message, fatal, recoverable)` | 映射为 App 错误状态；fatal 时进入不可恢复 UI |
| `.videoAck(frameID, processed)` | 诊断/丢帧统计，不进入业务状态 |

### 4.3 本地数据边界

- `ConversationStore` 负责本地会话与消息；最终用户消息、最终 AI 消息由 EventAdapter 写入。
- 同一会话的最终消息写入必须串行化（store actor/串行队列），保持消息顺序。
- 流式 `aiText`、`transcription` 中间态只存在于内存，不落库。
- 本地会话 id 与服务端 `sess_*` id 分离；服务端 id 只用于重连 resume，不写入本地外键。
- 本地会话的创建/复用策略（例如是否按日切分）由持久化模块文档定义；本文只要求以本地会话 id 隔离消息。
- `ConversationStore` 的持久化技术选型（Core Data / SQLite / 文件）由持久化模块文档决定；不引入第三方依赖前需按 `ios_dev_rules.md` §3 评估。

---

## 5. 主要交互

### 5.1 进入会话与启动会话

```
ConversationView.onAppear
  → ConversationCoordinator.start()
      1. 取/建本地会话（ConversationStore）
      2. 启动 ConversationEventAdapter 的事件消费 Task
      3. sdk.connect()
      4. audioCapture.start()
      5. audioPlayback.start()
      6. interruptCoordinator.start()
  → SDK 建连成功，发出 .connectionEpochAdvanced
  → EventAdapter 转交 Coordinator：
        if sessionActive && sdk.state.connectionState == .connected
            sdk.startSession(resumeSessionID: serverSessionID)
  → 收到 .sessionReady(sessionID, restored)
      EventAdapter.serverSessionID = sessionID
      if !restored { 清空本地消息缓存 }
      复位本地模式标志；如用户此前有模式意图，重新发送 session.change
  → 收到 .sessionStateChanged(.listening)
```

说明：

- 采集在 `connect()` 后立即启动；`session.start` 之前的音频会被 SDK 的上行闸门丢弃（SDK 设计约定）。
- 首次连接 `serverSessionID == nil`，服务端新建会话；重连时回传旧 `sess_*` id。
- 本地会话 id 是 App 内部标识，不可作为协议 `session_id` 回传。

### 5.2 一轮对话

```
音频上行
  AVAudioEngine input tap
    → AVAudioConverter → 16k mono Int16
    → audioCapture → sdk.pushAudioPCM
    → SDK: FrameAssembler → VAD → 可选 AGC → Opus
    → WebSocket

下行事件
  .transcription(text, isFinal, id)
    → EventAdapter 更新实时转写；final 时写 ConversationStore

  .aiText(text, messageID, index, isFinal, image, source)
    → EventAdapter 按 messageID 累积
    → final 时写 ConversationStore（带 image 时一并保存）

  audioSink.livaChat(_:didReceiveAudio:)  [audio recv 队列]
    → AudioPlaybackController 的 JitterBuffer
    → 播放模块输出（AVAudioPlayerNode 或 AVAudioSourceNode，选型见 Audio 模块文档）
```

关键契约：

- `audioSink` 回调必须快速返回；禁止在 audio recv 队列做 UI、磁盘或网络操作。
- 播放模块按 `AudioPCMFrame.playbackEpoch` 过滤过期帧；`sequenceNumber` 是 SDK 生命周期内单调的帧序号，不用于 flush/重连后的新旧判定。
- `.playbackShouldFlush(reason, epoch)` 到来时，播放模块丢弃 `< epoch` 的帧，保留 `>= epoch` 的帧；不能整队清空。
- `JitterBuffer` 同时被 audio recv 队列（写入）与 Main 的 flush/断连事件（过滤）访问，必须内部加锁或串行化；用 `acceptedEpoch = max(acceptedEpoch, epoch)` 单调更新，避免旧事件回退。

### 5.3 本地 VAD 打断

```
SDK 每帧 VAD → .localVadStateChanged
InterruptCoordinator:
  sessionState == .speaking && localVadState == .speech
  → 500ms 节流
  → sdk.sendInterrupt(.userVoice)
  → 轻触觉反馈

服务端进入新回合：
  → SDK 发出 .sessionStateChanged(.thinking)
  → SDK 发出 .playbackShouldFlush(.interruptAck, epoch:)
  → 播放模块按 epoch 丢弃旧 TTS 帧
```

- 与 Android 一致：本地 VAD 触发时**不直接清空播放队列**，播放清空由 SDK 的 flush 事件驱动。
- 连接进入 `reconnecting` / `disconnected` 时同样按新的 `playbackEpoch` 过滤。

### 5.4 重连与会话恢复

```
ConnectionManager 检测断开
  → .connectionStateChanged(.reconnecting)
  → 退避重连（参数由 LivaChatConfig 决定）
  → 建连成功 → .connectionEpochAdvanced
  → Coordinator: sdk.startSession(resumeSessionID: serverSessionID)
  → .sessionReady(id, restored)
      restored == false → 清空本地消息缓存
  → 继续推送音频/视频
```

连接终态：

| 事件 | App 处理 |
|---|---|
| `.connectionTerminated(.authFailed)` | 停止自动连接，引导重新配置凭证 |
| `.connectionTerminated(.reconnectExhausted)` | 显示网络失败与重试入口；等待用户或前台自愈触发 `connect()` |
| `.connectionTerminated(.serverClosed)` | 结束当前会话，清空 `serverSessionID` |
| `.sessionEnded(reason, id)` | 清空 `serverSessionID`，停止本地转写/任务展示 |

- 连接非 `.connected` 时，EventAdapter 清空临时 UI 状态（流式 `aiText`、`transcription`、`jobStatus`），不依赖服务端补发。
- 重连期间 App 可继续采集；SDK 在非 CONNECTED 或未 startSession 时丢弃音视频帧。

### 5.5 前后台与音频中断

```
scenePhase 变化
  → AppContainer 更新 isForeground
  → 前台：coordinator.onForeground()
      guard session 资格/权限 → reconcileMedia(.foreground) 确保 full
      disconnected → sdk.connect()
      成功恢复音频 banner 后恢复相机
  → 后台：coordinator.onBackground()
      停止相机并保留恢复意图
      mic 未静音且 capture running/recovering → 保持 full
      mic 静音 → 补静音窗口后完整 teardown（释放麦克风，后台停媒体），App 可挂起
```

`AudioSessionController` 负责：

- 唯一的 `AVAudioSession` owner；单一 `AVAudioEngine`（采集与 TTS 播放共用）；
- `activate(captureSource:)` 的 category/mode/options/VP 切换与回滚；
- `interruptionNotification` → Coordinator teardown/恢复；
- `routeChangeNotification` + `AVAudioEngineConfigurationChange` → 300ms 去抖后 `onRouteChanged`；
- 媒体服务重置：停 engine、清配置状态，Coordinator 重新 activate/start；失败则 teardown；
- 音频会话配置（`.playAndRecord`/`.voiceChat` 或 `.measurement`，以及 voice processing）。

后台策略由 `ConversationCoordinator.reconcileMedia()` 统一持有；音频模块不渗透到 Domain。

### 5.6 摄像头 / 图库 / 屏幕上行

| 来源 | App 实现 | `cameraFacing` | `turnID` | `text` |
|---|---|---|---|---|
| 摄像头 | `AVCaptureSession` + `AVCaptureVideoDataOutput`；按视频模块定义的质量/尺寸输出 JPEG | `front` / `back` | 开相机、切换镜头时由 `CaptureTurnCounter` 推进 | `nil` |
| 图库 | `PHPickerViewController` + ImageIO 归一化 | `gallery` | 当前采集轮 | chat 下 `conv_gallery_action_text`（16 locale）；omni 下 `nil` |
| 屏幕 | ReplayKit（in-app capture 或广播扩展，App 阶段决策） | `screen` | 开始录屏时推进 | 持续录屏 `nil`；点按抓取为客户端指令（设计占位；omni 下 `nil`） |

- `turnID` 由 App 进程级 `CaptureTurnCounter` 维护，协议语义为“采集轮变化”；SDK 直通落线，不解释该值。
- 相机预览层与 PHPicker 是 UIKit 组件，必须封装在 `PlatformAdapters`，并按 `ios_dev_rules.md` §4 取得例外批准。
- 摄像头、屏幕与图库的并发/切换策略、帧率、质量、暖机等具体策略由视频模块文档定义；App 架构只约定统一走 `pushVideoJPEG`。

### 5.7 会话模式切换

支持模式：`chat` / `translate` / `transcribe` / `omni`；`translate` 下可带 `lecture` / `dialogue` usecase。

```
用户选择模式
  → ViewModel 乐观更新 UI
  → sdk.sendSessionChange(mode, myLanguage, localLanguage, usecase)
  → 收到 .sessionChanged(...)
      EventAdapter 以服务端回声为准重置全部模式标志
  → 重连后 .sessionReady
      EventAdapter 先复位模式标志（服务端会话重置为 chat）
      若用户此前有模式意图 → 重新发送 session.change，仍以回声为准
```

- UI 不得自行推断服务端最终模式；乐观状态只用于交互过渡。
- 模式切换对采集/录屏的影响由 Conversation/Video 模块文档定义；架构上由 Coordinator/ViewModel 仲裁。

### 5.8 Agent job

```
SDK → .jobStatus([AgentJob])
  → EventAdapter 全量替换当前列表（空数组 = 清空）
  → ConversationView 展示任务列表/徽标
连接非 CONNECTED 或 .sessionEnded → 清空列表
```

`AgentJob` 的 `startedAtMs` 已由 SDK 用服务端时钟偏移换算为设备本地时间，App 不重复换算。

### 5.9 断开与退出

```
ConversationCoordinator.stop()
  → interruptCoordinator.stop()
  → audioCapture.stop()
  → audioPlayback.stop()
  → camera/screen 解绑
  → sdk.disconnect()
  → 本地会话收尾（ConversationStore）
  → 清空 EventAdapter 临时状态
```

- 与 `docs/ios_sdk_design.md` §7.8 一致：不调用 `sdk.endSession(...)`；服务端通过断连/超时清理。
- `sdk.disconnect()` 停止心跳与重连、关闭 WebSocket；不重置 SessionManager、不释放 native 处理器。
- 用户主动 `disconnect()` 不产生 `.connectionTerminated`。

### 5.10 设置与服务器切换

```
用户保存服务器地址/密钥
  → 校验地址/密钥（App 层规则）
  → 写入 Keychain（secret / wire token / deviceID）
  → 可选：GET /healthz_token?token=<wire>&device_id=<id> 预检
  → 结束当前会话与媒体
  → 清空 serverSessionID / 模式 / 临时 UI
  → 重置播放器 JitterBuffer/acceptedEpoch
  → 重建 ConversationRuntime（新 LivaChatConfig）
  → 重新进入会话
```

- SDK 配置不可变，因此不允许原地修改 `serverURL`/`token`。
- 切换服务器前必须清空 `serverSessionID`、模式标志与临时 UI；否则新服务器可能收到旧服务器的 resume id。本地历史是否清空由产品决定。
- `ServerProbe` 使用 `URLSession`，与服务端 WS 握手的 token/device_id 语义一致；端点与校验语义参考 `docs/setting_server_address.md`。
- `ServerProbe` 的 TLS 策略必须与 SDK 的 `tlsPolicy` 一致；`.allowSelfSigned` 场景下 URLSession 也需要接受自签名证书（或安装并信任 CA），否则预检结果与 WS 握手会分裂。
- 明文 secret、wire token 不得进入日志或 UI 崩溃上报。

### 5.11 本地会话与历史

```
EventAdapter 收到最终用户/AI 消息
  → ConversationStore 落库（会话 id、角色、文本、序号、时间、可选图片）
HistoryView
  → 读取 ConversationStore 的会话列表/详情
  → 只读展示；删除/清空由历史模块文档定义
```

- 流式中间态不落库；最终消息才落库。
- 本地历史与服务器会话上下文是两套数据：重连 resume 只使用 `serverSessionID`，历史展示只使用本地存储。
- History 是否作为首期产品功能、是否支持搜索/删除，由产品与历史模块文档确认（本文只定义模块边界）。

---

## 6. 线程与并发模型

| 执行上下文 | 负责内容 |
|---|---|
| MainActor | SwiftUI、ViewModel、AppRouter、ConversationCoordinator、EventAdapter 的状态更新 |
| SDK 内部串行队列 | 连接、会话、audio send、audio recv、video send（由 SDK 管理，App 不阻塞） |
| `AVAudioEngine` tap 线程 | 采集回调、格式转换、`pushAudioPCM` |
| 播放器队列（AVAudioPlayerNode 或 AVAudioSourceNode） | 抖动缓冲消费与播放；不得回调 Main |
| `AVCaptureVideoDataOutput` 队列 | 相机帧处理、JPEG 编码、`pushVideoJPEG` |
| ReplayKit 回调 | 屏幕帧处理与上行 |
| `ConversationStore` 串行队列/actor | 消息落库、查询 |
| `URLSession` async/await | 服务预检 |
| `os.Logger` / 日志队列 | 诊断写入 |

并发约束：

- `ConversationEventAdapter` 是唯一消费 `sdk.events` 的 Task；其他消费者通过 App 层状态/流订阅。
- `LivaChatAudioSink` 实现（播放控制器）是 `Sendable`/队列封闭对象，回调必须快速返回。
- UI 状态只在 MainActor 写入；Media 回调不得直接修改 ViewModel。
- UIKit 类型只在 MainActor 使用；不得跨队列持有/传递 `UIView`、`UIViewController`。
- 需要跨线程共享的模型使用值类型（`Sendable`）；媒体控制器明确声明所属队列。
- SDK 以 Swift 6 严格并发与队列封闭实现；App 侧同样以 `Sendable` 值类型 + MainActor/串行队列为默认，避免跨线程共享引用类型。

---

## 7. 平台集成与约束

### 7.1 iOS 15 约束

- 所有 target 部署目标 15.0；SPM `platforms: [.iOS(.v15)]`。
- 不使用 `NavigationStack`（iOS 16+）、`SwiftData`（iOS 17+）、`Observation`（iOS 17+）作为必需路径；若使用必须提供 iOS 15 降级。
- Swift Concurrency（`async/await`、`AsyncStream`、`Task`）可用于 iOS 15。
- 依赖仅通过 SPM；预编译库使用 XCFramework；禁止 CocoaPods。

iOS 15 / 16 的适配边界（业务/Domain 不感知版本；availability 分支集中在 AppShell/PlatformAdapters）：

| 能力 | iOS 15 基线 | iOS 16+ 可选 |
|---|---|---|
| 导航 | `NavigationView` + `AppRouter` | `NavigationStack` / `navigationDestination` |
| 弹层 | 自定义 sheet / `fullScreenCover` | `presentationDetents` |
| 分享（若产品引入） | `UIActivityViewController`（UIKit 适配） | `ShareLink` |
| 布局 | `HStack`/`VStack`/`LazyVGrid` | `Grid`/`Table`/`ViewThatFits` |
| 屏幕/旋转 | `UIScreen`/`UIDevice` | `UIWindowScene.effectiveGeometry` / `requestGeometryUpdate`；避免 `UIScreen.main` |

- 若完全使用 iOS 15 基线 API，则无需为 iOS 16 维护业务分支；iOS 16+ 仅在选择新交互时增加 availability 分支。
- UIKit 类型（`UIScreen`/`UIDevice`/`UIActivityViewController` 等）只在 `PlatformAdapters`/`AppShell` 使用；Domain/Media 通过协议或值类型获取屏幕/方向信息。
- iOS 17-only API（`Observation`/`SwiftData`、`onChange` 两参数签名、`AVCaptureConnection.videoRotationAngle` 等）不得作为必需路径。
- Swift 6 语言模式不改变最低系统版本；SDK 的 `Package.swift` 已使用 `swift-tools-version: 6.0` + `.iOS(.v15)`。
- 验收：iOS 15 与 iOS 16 真机分别冒烟音频/路由/旋转/相机/录屏。

### 7.2 权限与 Info.plist

| 能力 | 必需配置 |
|---|---|
| 麦克风 | `NSMicrophoneUsageDescription` |
| 摄像头 | `NSCameraUsageDescription` |
| 相册选择 | `PHPickerViewController` 不需要全量相册权限；若改用 `PHAsset` 才需要 `NSPhotoLibraryUsageDescription` |
| 局域网服务器 | `NSLocalNetworkUsageDescription` |
| 后台音频 | `UIBackgroundModes = [audio]`（无 entitlement） |

### 7.3 音频会话

- 单 engine：采集与 TTS 播放共用 `.playAndRecord` + `.voiceChat/.measurement`；播放必须经过同一 engine 才能进入 VPIO 的 AEC 参考路径。
- `.voice` 开启 `setVoiceProcessingEnabled(true)`，避免外放 TTS 被本地 VAD 识别为用户说话；只能在该 engine 停止时切换。
- 后台 mic 未静音保持 full；mic 静音时 teardown 并 `deactivate()` 释放麦克风，后台不播放 TTS。
- 处理中断、路由变化、engine configuration change 和媒体服务重置；由 Audio 模块统一维护，不让各 View 自行配置。

### 7.4 网络与安全

- 默认 `wss://`；`ws://` 需要 ATS 例外，不推荐。
- 自签名/内网服务器可显式 `.allowSelfSigned`；该模式是 trust-all，不提供 MITM 防护，需在受控网络使用。
- TLS 策略必须同时覆盖 SDK WebSocket 与 App 的 `URLSession`（ServerProbe）；自签名场景下 URLSession 需要对应的信任配置或安装 CA，不能只让 WS 生效。
- secret/wire token/deviceID 存 Keychain；日志与错误上报需脱敏。
- SDK 内部日志与 App 日志分离；不得把明文凭证写入日志文件。

### 7.5 UIKit 例外

以下为 iOS 15 上 SwiftUI 无等价能力、必须桥接 UIKit 的部分：

- `PHPickerViewController`（图库选择）；
- `AVCaptureVideoPreviewLayer` 的预览容器；
- 触觉反馈（`UIImpactFeedbackGenerator`；iOS 15 无 SwiftUI `sensoryFeedback` 等价能力）；
- 若产品引入系统分享：`UIActivityViewController`（iOS 15 无 `ShareLink`）。

要求：

- 仅存在于 `PlatformAdapters`；
- Domain/Media 业务对象不得 import UIKit；
- 按 `docs/ios_dev_rules.md` §4 在方案/PR 中说明替代方案与影响，取得批准后再实现。

---

## 8. 架构约束与不变量

以下约束是 App 模块间协作的硬边界，详细模块设计不得破坏：

1. **SDK 与 App 分工**：SDK 不持有麦克风/播放器/摄像头/录屏；App 不自行实现 Opus/VAD/协议编解码。
2. **事件单消费者**：`sdk.events` 只由 `ConversationEventAdapter` 消费；多路订阅一律经过 App 层 fan-out。
3. **音频 sink 独立**：PCM 不走事件流；播放控制器强持有、SDK 弱引用、仅初始化注入；回调快速返回。
4. **播放代际**：flush/断连时按 `playbackEpoch` 过滤，不能整队清空；`JitterBuffer` 的写入与过滤必须同步，`acceptedEpoch` 取单调最大值；`sequenceNumber` 是 SDK 生命周期单调序号，不用于 flush/重连后的新旧判定。
5. **会话 id 分离**：本地会话 id 与服务端 `session_id` 分离；`.sessionEnded` 必须清空 `serverSessionID`。
6. **首包约束**：只在 `.connectionEpochAdvanced` 且 `connectionState == .connected` 后调用 `startSession`；SDK 闸门之前的音视频帧会被丢弃。
7. **配置不可变**：不修改现有 SDK 配置；服务器变更通过重建 runtime 生效。
8. **主线程边界**：ViewModel/Coordinator/UI 状态在 MainActor；实时音频/视频回调不在 Main。
9. **UIKit 隔离**：UIKit 只允许出现在 `PlatformAdapters`；Domain/Media 不依赖 UIKit。
10. **模式权威**：UI 可乐观更新，但最终以 `.sessionChanged` 为准；`.sessionReady` 后必须复位模式并按用户意图恢复。
11. **断开语义**：默认不调用 `sdk.endSession`；由 `disconnect()` + 服务端超时/断连清理。
12. **产品范围**：MyLife/账号/云同步不在本架构内；若引入，必须新增模块文档并重新审视导航与数据层。
13. **事件顺序**：同一 SDK 来源内有序，跨来源不保证全局顺序；状态门控以 `sdk.state` 快照为准。
14. **断连门控**：断连后迟到的非 final 转写/AI 文本/job 快照不得复活临时 UI。
15. **runtime 重建**：新 SDK 的 `playbackEpoch` 从 1 重新开始，重建前必须重置播放器的 acceptedEpoch/JitterBuffer。
16. **TLS 一致性**：SDK WebSocket 与 App 的 `URLSession` 预检必须使用同一 TLS 策略；自签名场景不能只让 WS 生效。

---

## 9. 架构分析

### 9.1 设计优点

- **SDK/App 边界清晰**：SDK 已有完整测试与公开契约，App 只承担平台媒体与产品编排，模块可独立测试。
- **单一事件源**：一个 EventAdapter 消费 SDK 事件，避免多消费者竞争无界 `AsyncStream`，同时方便统一处理断连清理、持久化与模式恢复。
- **音频路径独立**：PCM 走 sink、UI 走事件/状态，实时性不被 SwiftUI 渲染或异步事件消费拖慢。
- **重连策略明确**：`connectionEpochAdvanced` 作为唯一 resume 触发点；本地/服务端 session id 分离，避免错误 resume。
- **iOS 15 现实约束明确**：导航、持久化、观察机制均按 iOS 15 可用能力设计，不把 iOS 16/17 API 当成默认。

### 9.2 风险与待决策

| 风险/决策 | 影响 | 处理 |
|---|---|---|
| 产品功能尚未实现（M0 骨架已落地） | 真机音频/相机/后台特性未验证 | 按模块文档分阶段实现；先跑通前台语音链路，再补视频/后台 |
| iOS 15 真机验证 | 本机 iOS 15 runtime 不可用；App 的 SwiftUI/音频行为未在 iOS 15 验证 | 发布前用 iOS 15 真机补冒烟；模块文档定义验收项 |
| 后台持续会话 | iOS 无 Android FGS 等价物；依赖 `audio` 后台模式与活跃音频会话 | 产品决策；架构隔离在 AppLifecycle/Audio |
| 屏幕采集范围 | ReplayKit in-app capture 与广播扩展能力/审核不同 | App 阶段决策，Video 模块文档定义 |
| 播放实现 | `AVAudioPlayerNode` vs `AVAudioSourceNode` + ring buffer | Audio 模块文档定稿；本文只约定 sink/epoch 契约 |
| 持久化选型 | Core Data / SQLite / 文件各有取舍 | 持久化模块文档决策；不引入第三方前按规则评估 |
| 本地历史范围 | SDK 文档未定义历史产品范围 | 若纳入首期，需产品确认；架构只提供 `ConversationStore` 边界 |
| UIKit 例外审批 | PHPicker/预览层必须桥接 UIKit | 按 `ios_dev_rules.md` §4 先审批后实现 |
| 服务器切换 | 配置不可变，需要重建 runtime；新 SDK 的 `playbackEpoch` 从 1 重新开始 | 设置模块文档定义交互；架构保证不原地改配置；重建时重置播放 epoch/JitterBuffer，并清空 `serverSessionID` |
| `session.ended` 后行为 | SDK 不自动断开；App 是否立即 `disconnect()` 未定 | 由会话 Domain 模块文档决策；本文只要求清空 `serverSessionID` 与临时 UI |
| 事件流无界 | App 停止消费会累积内存 | EventAdapter 必须常驻消费；生命周期结束时正确取消 Task |
| TLS `allowSelfSigned` / 策略分裂 | trust-all 无 MITM 防护；SDK 与 URLSession 预检的信任配置可能不一致 | 默认 `.system`；显式 opt-in；ServerProbe 与 SDK 共用同一 TLS 策略 |
| 24kHz / 非 20ms | SDK 初始化拒绝 | App 固定 16kHz/20ms；不把 SDK 限制下放到 UI |
| 无 `bandwidthHint` | 与 Android 公开面不同 | App 不依赖该能力；如未来需要再扩展 SDK |

### 9.3 建议的模块文档拆分

| 文档主题 | 内容边界 |
|---|---|
| AppShell/导航/生命周期 | `AppContainer`、Router、scenePhase、依赖装配、重建 runtime |
| 音频 | AVAudioSession、采集、播放、抖动缓冲、epoch、打断、路由/中断 |
| 视频 | 摄像头、屏幕、图库、JPEG、turnID、预览适配 |
| 会话 Domain | Coordinator、EventAdapter、模式/恢复策略、agent job、错误处理 |
| 持久化与历史 | 本地会话/消息 schema、迁移、搜索、删除 |
| 设置与凭证 | 服务器地址/密钥、Keychain、deviceID、服务预检、配置切换 |
| Platform Adapters | UIKit 例外审批、Representable 封装、线程约束 |
| 时光机（MyLife） | 入口/首页/搜索/热点时段/图片与语音画廊；`docs/ios_timeline_arch.md` 及子页文档 |

---

## 10. 范围边界与后续

- **包含**：实时语音/视频会话、权限与首启、音频路由/中断、摄像头/图库/屏幕上行、打断、重连恢复、模式切换、agent job、设置/服务器切换、本地会话存储边界（History UI 是否首期由产品确认）。
- **不包含（本设计无依据）**：
  - MyLife/时光机的内部实现（已另立 `docs/ios_timeline_*` 设计；本文只保留 AppShell/导航边界）；
  - 账号体系、云同步、跨设备历史；
  - 推送通知、社交/分享；
  - Android 的录屏浮窗、自定义头像等产品特性。

若产品要求上述能力，应新增对应模块文档，并在本文更新模块表与依赖方向；不得直接沿用 Android 的模块设计而不核对 iOS 平台能力。

---

## 11. 依据与索引

| 主题 | 文件 |
|---|---|
| SDK 公开契约、宿主集成、流程、决策 | `docs/ios_sdk_design.md` |
| iOS 开发规范 | `docs/ios_dev_rules.md` |
| 协议 DTO | `docs/ios_protocol_dto.md`、`packages/sdk-swift/` |
| SDK 门面 | `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatSDK.swift` |
| SDK 事件 | `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatEvent.swift` |
| 音频 sink | `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatAudioSink.swift` |
| SDK 配置 | `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatConfig.swift` |
| SDK 包定义 | `apps/ios/LivaChatSDK/Package.swift` |
| Android 架构参照 | `docs/android_app_arch.md` |
| 服务器配置与预检语义 | `docs/setting_server_address.md` |
