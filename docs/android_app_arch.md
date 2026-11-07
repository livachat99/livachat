# LivaChat Android App 架构说明

**基线**：当前工作树 `dsv4fash/dev`（HEAD `48bfe24`，2026-10-20 核对）
**范围**：`apps/android/`（`LivaChatApp` / `LivaChatSDK` / `LivaChatAppMock`）与 `packages/sdk-kotlin/`
**说明**：本文只描述当前代码中存在的结构和交互，不描述规划中的能力。文中路径均相对仓库根目录。

---

## 1. 总体架构

LivaChat Android 端由四个 Gradle 模块组成：

| 模块 | 路径 | 角色 |
|---|---|---|
| `:LivaChatApp` | `apps/android/LivaChatApp` | 产品 App：Compose UI、Hilt、Room/DataStore、音视频采集与播放、摄像头、录屏、MyLife 时间线 |
| `:LivaChatSDK` | `apps/android/LivaChatSDK` | 实时会话 SDK：WebSocket 连接、协议编解码、Opus/VAD、会话状态机、重连、视频帧上行 |
| `:sdk-kotlin` | `packages/sdk-kotlin` | 协议 DTO（kotlinx.serialization 生成的 `ClientMessage` / `ServerMessage`） |
| `:LivaChatAppMock` | `apps/android/LivaChatAppMock` | 独立的最小验证 App，自带一套简化的媒体控制器与 UI，不参与产品代码 |

依赖方向：

```
LivaChatApp ──► LivaChatSDK ──api──► sdk-kotlin
     └──────────────(传递依赖)────────────┘
LivaChatAppMock ──► LivaChatSDK
```

`LivaChatSDK` 通过 `api(project(":sdk-kotlin"))` 把协议 DTO 传递给 App；SDK 本身不持有 `AudioRecord` / `AudioTrack` / Camera（见 `LivaChatSDK/src/main/AndroidManifest.xml`）。

### 1.1 运行时分层

```
┌──────────────────────────────────────────────────────────────────────┐
│ LivaChatApp                                                          │
│                                                                      │
│  UI Layer (Compose)                                                  │
│    Conversation / History / Settings / Splash / Permission / MyLife  │
│                         │ StateFlow / events                         │
│  ViewModel Layer (Hilt ViewModel)                                    │
│                         │                                            │
│  Domain Layer                                                        │
│    ConversationCoordinator · UseCases · ConversationListenerAdapter  │
│                         │                                            │
│  Media Layer                                                         │
│    AudioCapture · AudioPlayback · Camera · ScreenCapture ·           │
│    Interrupt · AudioDevice/Routing · GalleryPhotoSender · Haptics    │
│                         │                                            │
│  Data Layer                                                          │
│    LivaChatSDK  │  Room  │  DataStore  │  MyLife HTTP (BFF)          │
└──────────┬───────────────────────────────────────────┬───────────────┘
           │ WSS + JSON protocol                        │ HTTPS
           ▼                                            ▼
   LivaChatSDK internals                        services/bff /api/mylife
   ConnectionManager → SessionManager
   → Audio/Video pipelines → native Opus/VAD
```

关键分层事实：

- **UI 不直接触碰网络/协议**。会话页通过 `ConversationViewModel` 发事件；实时状态由 SDK 的 `StateFlow` 与 `ConversationListenerAdapter` 的流驱动。
- **SDK 不持有 Android 采集/播放设备**。App 把 PCM / Bitmap / JPEG 推给 SDK，SDK 回调解码后的 PCM 给 App 播放。
- **Room 是会话消息的本地事实源**。最终消息由 `ConversationListenerAdapter` 写入，UI 通过 `ChatRepository.observeMessages()` 订阅。
- **MyLife 是独立的 HTTP 数据面**，不复用 SDK 的 WebSocket 链路。

---

## 2. 启动、装配与导航

### 2.1 进程启动

1. `LivaChatApplication`（`LivaChatApplication.kt`）是 Hilt 入口；注册 `ProcessLifecycleOwner` 观察者，把进程前后台写入单例 `AppForegroundState`。
2. `AppModule.provideLivaChatConfig()`（`di/AppModule.kt`）在进程启动时同步读取 DataStore 中的服务器配置（`runBlocking { preferences.serverConfig.first() }`），构造唯一的 `LivaChatConfig`：
   - `serverUrl = wss://<address>/v1/ws/stream`（地址为 `ip:port` / `域名:port`）；
   - `token = wireToken(secret)`（`sha1(secret + "LivaChat")` 的小写 hex，明文 secret 不上网）；
   - `deviceId` 为持久化 UUID v4（SharedPreferences `livachat_device`）；
   - `clientVersion = android-<versionName>.<versionCode>`；
   - `logFilePath = files/logs/livachat.log`；
   - `uplinkAgc = true`；
   - `languageProvider` 在每次 `session.start` 读取 `resources.configuration.locales[0]` 并归一为 App UI 语言码。
3. 地址未配置/非法时使用占位地址 `wss://0.0.0.0:1/...`，SDK 不会真正连上；连接启动由会话页的"配置齐备"门控拦截。
4. `AppModule` 同时提供进程级单例 `LivaChatSDK`，监听器为 `CompositeLivaChatListener`。
5. **服务器配置只在进程装配时读取一次**；设置页保存配置后弹"必须退出"弹窗，`exitProcess(0)`，下次启动才生效（`SettingsViewModel.saveServerConfig/quitToApply`）。

### 2.2 Activity 与导航

`MainActivity` 主要负责三件事：注入 `serverBaseUrl`、提供 `LocalServerBaseUrl`（Markdown 链接转换）、建立 `NavHost`（`MainActivity.kt`）。

```
NavHost(startDestination = "splash")
  splash          → SplashScreen
  permission      → PermissionScreen
  conversation    → ConversationScreen（产品主页面）
  history         → HistoryScreen
  history/{id}    → HistoryDetailScreen
  settings        → SettingsScreen
  webview/{url}   → WebViewScreen
  mylife          → MylifeHomeScreen（时光机宿主）
  mylife/gallery  → FrameGalleryScreen
  mylife/hotslot  → HotSlotScreen
  mylife/events   → EventListViewerScreen
  mylife/audio    → AudioGalleryScreen
```

导航事实：

- `Splash` 至少停留 1 秒，同时判断"是否首次启动 / 麦克风是否已授权"；任一为真进入 `Permission`，否则进入 `Conversation`（`SplashViewModel`）。
- `Permission` 页面独立申请麦克风（必需）与摄像头（可选），完成首启标记后进入会话页（`PermissionViewModel`）。
- 会话页是唯一启动 SDK 连接与音视频采集的页面；MyLife 页面只走 HTTP。
- `MainActivity` 声明了 `configChanges=orientation|screenSize|...`，避免 agent job 结果全屏查看时旋转重建 Activity（`AndroidManifest.xml`）。

---

## 3. 主要模块与职责

### 3.1 UI 层

所有屏幕位于 `LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/`，共享组件位于 `ui/components/`，主题位于 `ui/theme/`。

| 屏幕 | ViewModel / 状态 | 主要职责 |
|---|---|---|
| Splash | `SplashViewModel` | 首启/麦克风权限分流 |
| Permission | `PermissionViewModel` | 麦克风（必需）/摄像头（可选）授权 |
| Conversation | `ConversationViewModel` + `ConversationUiState` | 实时语音/视频会话、模式切换、设备选择、头像、图片查看器、agent job 面板、录屏入口 |
| History | `HistoryViewModel` + `HistoryUiState` | 本地会话列表、搜索、删除、清空 |
| HistoryDetail | `HistoryDetailViewModel` | 只读消息列表、图片查看器、agent job 结果查看 |
| Settings | `SettingsViewModel` + `SettingsUiState` | 服务器配置（地址/密钥/检测/保存重启）、视频默认值、触觉、关于 |
| WebView | 无 | 展示会话/历史/MyLife 中 Markdown 链接打开的网页；设置页隐私政策/用户协议行当前为占位，未接跳转 |
| MyLife Timeline | `TimelineViewModel` + `TimelineUiState` | 日/周/月/年时间线、总览、范围钻取、搜索、热点入口 |
| FrameGallery | `FrameGalleryViewModel` | 关键帧网格、分页、帧查看、批量删除 |
| HotSlot | `HotSlotViewModel` | 热点时段事件列表；`EventListViewerScreen` 复用同一 ViewModel 类（各自以路由 backstack entry 为 owner） |
| AudioGallery | `AudioGalleryViewModel` | 某日语音段列表、播放/暂停/seek |

**会话页组成**（`ConversationScreen.kt`）：

- 顶部 `TopAppBar`：`StatusIndicator`（断线时点击可重试）、录屏开关、更多菜单（时光机/对话历史/设置）；头像隐藏时还承载头像按钮与 `AgentJobStatusBadge`。
- 主区域在 `VideoPreview`（相机模式）与语音模式内容（AI 头像、状态、消息列表、实时转写、欢迎引导）之间切换。
- 底部 `BottomToolbar`：麦克风、扬声器、摄像头、图库、麦克风电平、输入/输出设备选择入口。
- 覆盖层/弹窗：`ErrorBanner`、`AgentJobListPanel`、设备选择器、头像定制/裁剪、图库预览、图片查看器、agent job 结果全屏查看、录屏说明/停止确认、全模态说明、语言选择、转录源选择、服务器配置引导。

`ConversationScreen` 把用户操作收敛为 `ConversationEvent`（`ConversationEvent.kt`）交给 ViewModel；业务状态决策（是否启动、是否互斥、是否提示）在 ViewModel/控制器中完成，权限申请与系统 Intent（相机权限、MediaProjection 授权、悬浮窗设置页）由 Screen 发起。

### 3.2 Domain 层

| 组件 | 职责 |
|---|---|
| `ConversationCoordinator` | 会话生命周期中枢：创建当日会话、连接 SDK、启动/停止采集与播放、注册监听器、断线重连后的会话恢复 |
| `ConversationListenerAdapter` | SDK 事件 → App 领域状态：转写、AI 流式文本、模式回声、agent job 快照、错误；并直接写 Room |
| `ChatUseCase` | 观察/追加/读取消息 |
| `SessionUseCase` | 按本地日期创建/复用会话、生成 seq、结束会话并生成本地摘要 |
| `HistoryUseCase` | 会话列表/搜索/详情/删除/清空 |
| `SettingsUseCase` | 用户偏好与服务器配置读写 |

`ConversationCoordinator.start()` 的执行顺序（`domain/ConversationCoordinator.kt`）：

```
register(listenerAdapter) + register(audioPlayback)
  → 订阅 AppForegroundState（后台静音释放 / 前台自愈）
  → createOrGetTodaySession()（Room daily_sessions）
  → setSessionId(localSessionId, startSeq)
  → sdk.connect()
  → audioCapture.start() + audioPlayback.start() + interruptCoordinator.start()
  → 订阅 sdk.connectionEpoch
      每次成功建连（含重连）→ sdk.startSession(resume = serverSessionId)
```

`stop()` 的顺序相反：停止中断协调、采集、播放、解绑相机、断开 SDK、结束本地会话（写结束时间与摘要）、清空 adapter、注销监听器。

`replaceWithNewSession()` 用于"删除当前会话/清空全部记录"场景：先建新的当日会话，再切换 adapter 的 sessionId，返回旧 id 供调用方删除。

### 3.3 Media 层

位于 `LivaChatApp/src/main/kotlin/dev/livachat/app/media/`。核心控制器为 Hilt 单例（`@Singleton`）；`JitterBuffer`、`MicLevelMeter`、`SilenceFlushGate`、`PcmDump` 等为控制器内部持有的辅助对象。

| 组件 | 职责与关键行为 |
|---|---|
| `AudioCaptureController` | `AudioRecord` 16 kHz 单声道 PCM，每 20 ms 一帧推给 `sdk.pushAudioPcm`；静音时补发 1 s 全零帧再停发；读错误重试 ≤3 次、按"采集代次"作废旧循环；麦克风电平旁路；输入设备选择与源切换会重建 `AudioRecord`；运行中启动 `MicCaptureService` 前台服务 |
| `AudioPlaybackController` | `AudioTrack` 流式播放 SDK 解码后的 PCM；`JitterBuffer` 预缓冲 3 帧、10 ms 节拍消费；静音用 track volume；输出设备选择、设备丢失回落扬声器、蓝牙 SCO 链路联动；实现 `onAiAudioPcm` / `onPlaybackShouldFlush` |
| `InterruptCoordinator` | 合并 `sdk.sessionState`、`sdk.localVadState` 与用户"语音打断"偏好；AI `SPEAKING` 且本地 VAD `SPEECH` 时发 `sendInterrupt(USER_VOICE)` 并触发轻触觉；500 ms 节流 |
| `CameraController` | CameraX `Preview` + `ImageAnalysis`（目标 1280×960）；开流前 1 s 暖机丢帧、约 900 ms 节流；JPEG q70、长边 ≤1280；逐帧携带 `CaptureTurnCounter` 的 turnId；权限缺失在绑定前拦截并回调失败 |
| `GalleryPhotoSender` | 图库照片解码/EXIF 旋正/缩放（长边 ≤1920、q70）后作为 video 帧上行；chat 模式附带发图指令（`conv_image_action_text`，17 locale，2026-10-27 已实施），omni 模式不带 |
| `ScreenCaptureController` | `MediaProjection` + `VirtualDisplay` + `ImageReader`；当前为**浮窗点按单次抓屏**（2 s 自动定时上行已被 `AUTO_TICK_ENABLED=false` 关闭）；点按隐藏浮窗→取一帧→`pushVideoJpeg`（chat 模式带发图指令，`conv_image_action_text`，2026-10-27 已实施）→等待本轮 `ai_text`；仅"进程后台 + 屏幕交互 + 未锁屏"放行上行，App 前台/熄屏/锁屏一律丢弃；旋转时 resize 虚拟屏 |
| `ScreenCaptureBubble` | `TYPE_APPLICATION_OVERLAY` 浮窗：拖拽贴边、点按抓取、发送/拒绝反馈；只在主线程操作 |
| `AudioDeviceManager` | 进程级 `AudioDeviceCallback`，枚举并广播输入/输出设备（按类型白名单过滤） |
| `AudioRouteManager` | 音频焦点与通信模式；`switchRoute` 目前仅在播放启动时使用 `SPEAKER` |
| `HapticController` | 轻触觉反馈（打断、AI 开始说话等） |
| `AppForegroundState` | 进程前后台事实流；驱动"后台且静音时释放麦克风"与"回前台自愈" |
| `CaptureTurnCounter` | 采集轮标识；由调用方在开相机/切镜头/开录屏时推进，逐帧透传给服务端 |
| `MicCaptureService` / `ScreenCaptureService` | 两个前台服务薄壳，分别提供麦克风与 `mediaProjection` 的 FGS 豁免；均为 `START_NOT_STICKY` |
| `ImageGallerySaver` / `ImageExif` / `MicLevelMeter` / `JitterBuffer` / `PcmDump` | 相册保存、EXIF、电平、抖动缓冲、PCM 调试落盘等辅助组件 |

### 3.4 Data 层

| 数据面 | 实现 | 内容 |
|---|---|---|
| 会话/消息 | Room `LivaChatDatabase` v9 | `sessions`、`messages`、`daily_sessions` 三张表；`sessions` 含摘要/首条用户消息；`messages` 含 seq、isFinal、图片列、source、mode |
| 偏好 | DataStore `livachat_preferences` | 语音打断、默认前置摄像头、自动开相机、触觉、头像可见、首启标记、录屏说明已展示、服务器地址、认证密钥 |
| 聊天图片 | `files/chat_images/` | 消息图片按消息 id 落盘，读取时转 Base64 给领域模型 |
| 头像 | `files/avatar/`、`cache/avatar_photos/` | 自定义头像原图与相机拍照临时源图 |
| 会话摘要 | `SessionUseCase.endSession()` | 本地生成：取前 3 条用户消息、截断 100 字符；历史页不依赖服务端 |
| MyLife | `MylifeApi` + `MylifeRepository` | `GET /api/mylife/overview`、`/search`、`/timeline`；`POST /events/remove`、`/agent-jobs/cancel` |
| 服务预检 | `ServiceProbe` | `GET /healthz_token?token=<wire>&device_id=<id>`，对应设置页"检测服务" |

MyLife 数据流要点：

- `MylifeRepository` 以 `StateFlow<OverviewDto?>` 持有内存窗口，overview 支持 keyset 分页、按日期合并；
- 窗口内存在 `has_pending` 时每 60 秒强制刷新一次；无 pending 时挂起等待；
- 媒体 URL 在 Repository 出口统一转为绝对地址并追加 `token` 查询参数，UI 不感知鉴权；
- 帧图用 Coil，语音段用 Media3 ExoPlayer，二者复用同一个支持自签证书的 OkHttp client；
- 所有 MyLife 数据只存在于内存，不落 Room（本地持久化仅用于会话历史）。

### 3.5 SDK 层（`:LivaChatSDK`）

对外门面 `LivaChatSDK`（`LivaChatSDK.kt`）：

- 状态流：`connectionState`、`sessionState`、`localVadState`、`bandwidthHint`、`connectionEpoch`。
- 动作：`connect/disconnect`、`startSession(resumeSessionId)`、`endSession`、`pushAudioPcm`、`pushVideoBitmap`、`pushVideoJpeg`、`sendInterrupt`、`sendSessionChange`。
- 构造参数 `LivaChatConfig`：serverUrl、wire token、deviceId、clientVersion、采样率、VAD 灵敏度、上行 AGC、视频质量/长边、重连策略、心跳参数、日志路径、时区、App UI language（静态值 + 动态 provider）。

内部结构（`LivaChatSDK/src/main/kotlin/dev/livachat/sdk/internal/`）：

```
ConnectionManager    OkHttp WebSocket、心跳 15s/35s 超时、指数退避重连（base 1s、上限 30s、±500ms 抖动，默认 5 次）、
                     socket 代次守卫、connectionEpoch
SessionManager       会话状态机、C2S seq 闸口、S2C 重排序、时钟偏移、消息分发、
                     强制 flush 信号
AudioSendPipeline    20ms 组帧 → VAD → 上行 AGC（AI 说话时 unity）→ Opus 编码
AudioRecvPipeline    下行 Opus → PCM 解码
VideoSendPipeline    Bitmap→JPEG，或预编码 JPEG 直通
Processors           Opus（native）、WebRTC VAD（native）、JPEG
MessageCodec         JSON 编解码（type 判别字段）
ListenerDispatcher   内部事件 → 公开 LivaChatListener
```

SDK 的线程契约（`LivaChatListener.kt` 注释与 `ListenerDispatcherImpl`）：

- `onConnectionStateChanged` / `onSessionStateChanged`：在 SDK 的 Main observer 上回调；
- `onTranscription` / `onAiText` / `onJobStatus` / `onVideoAck` / `onError` / `onPlaybackShouldFlush`：在 WebSocket reader 线程同步回调；
- `onAiAudioPcm`：在 SDK 内部 audio-recv dispatcher 回调，App 直接写入播放缓冲；
- `CompositeLivaChatListener` 用 `CopyOnWriteArraySet` 同步扇出，订阅者不得抛异常或阻塞。

---

## 4. 主要交互

### 4.1 冷启动与首次授权

```
Launch
  → LivaChatApplication.onCreate（Hilt、ProcessLifecycleOwner）
  → MainActivity → NavHost(splash)
  → SplashViewModel.bootstrap()
      读取 isFirstLaunch、检查 RECORD_AUDIO
      ≥1s 后：
        首次启动 或 麦克风未授权 → Permission
        否则                     → Conversation
  → PermissionScreen
      麦克风（必需）+ 摄像头（可选）→ 标记首启完成 → Conversation
```

### 4.2 进入会话与建连

```
ConversationScreen 首次组合
  → LaunchedEffect → ConversationEvent.OnScreenStart
  → ConversationViewModel 先等服务器配置首次读取完成
      配置不齐备 → 不启动；本次运行首次检测时提示「去设置」，之后仅连接按钮再提示
      配置齐备   → startScreenSession()
  → ConversationCoordinator.start()
      创建/复用当日 Room 会话 → sdk.connect()
      → AudioCapture.start / AudioPlayback.start / Interrupt.start
  → SDK onOpen（连接状态 CONNECTED，connectionEpoch +1）
  → Coordinator 观察到 epoch → sdk.startSession(resume = serverSessionId)
  → 收到 session.ready → adapter 记录服务端 sessionId
  → 收到 status(listening) → sessionState = LISTENING
```

说明：

- App 的 Room 会话 id 是 UUID；服务端下发的 `session_id`（`sess_*`）只用于重连 resume，二者不混用。
- 会话按**本地日**复用：`daily_sessions(date → sessionId)` 决定当天沿用哪个会话；跨天上新会话。
- 当前实际的停止路径是 ViewModel `onCleared()` 触发 `coordinator.stop()`；`OnScreenStop` 事件在代码中已定义且 VM 有处理分支，但会话页未发出。

### 4.3 一轮对话（上行 / 下行）

```
AudioRecord 16kHz/20ms
  → AudioCaptureController（静音窗口 / 电平旁路）
  → sdk.pushAudioPcm（audio-send dispatcher）
  → FrameAssembler → VAD → AGC → Opus → SessionManager 取 seq → JSON → WS

收到下行 transcription(interim/final)
  → adapter 更新实时转写流；final 时写入 Room（id=user-<transcriptionId>）

收到下行 status(THINKING)，随后收到 ai_text 流式增量
  → adapter 累积到 messageCache，final 时写 Room（id=ai-<messageId>）
  → UI 的 MessageBubble 流式渲染

收到下行 ai_audio
  → SDK audio-recv dispatcher → Opus 解码 → listener.onAiAudioPcm
  → AudioPlaybackController.jitterBuffer → AudioTrack.write（10ms 节拍）
```

### 4.4 用户语音打断

```
sessionState = SPEAKING 且 localVadState = SPEECH
  → InterruptCoordinator（偏好开启、500ms 节流）
  → sdk.sendInterrupt(USER_VOICE) + 轻触觉
  → SDK 入站处理：status(THINKING) / session ended / fatal error 等场景发出 onPlaybackShouldFlush
  → AudioPlaybackController 清空 jitter buffer
  → adapter 不因打断清理转写/AI 流式文本；未收尾内容的清理点是转写 final 后的延迟清理、连接非 CONNECTED、clearSession（停止/换会话）
```

注意：当前本地打断路径**不发本地 playback.flush()**；播放清空由 SDK 在入站 `status(THINKING)` / `session ended` / `fatal error` 时发出的 `onPlaybackShouldFlush` 驱动（没有独立的 flush 报文）。`AudioPlaybackController.flush()` 仍保留。

### 4.5 断线重连

```
ConnectionManager 检测断开 → connectionState = RECONNECTING
  → 指数退避重连（base 1s、上限 30s、±500ms 抖动，默认 5 次）
  → 重连成功 → connectionEpoch +1
  → ConversationCoordinator 观察到 epoch → sdk.startSession(resume = serverSessionId)
  → 收到 session.ready(restored=true) → 会话延续
  → adapter 按上次用户模式请求补发 session.change
重试耗尽 → DISCONNECTED（UI 显示重试入口）
HTTP 401 / 服务端 4001 → 终态，不重试，直接 DISCONNECTED
```

`connectionEpoch` 是单调自增计数，避免 `StateFlow` 对"CONNECTED → RECONNECTING → CONNECTED"值回归去重而吞掉重连事件。

### 4.6 前后台与麦克风

```
ProcessLifecycleOwner ON_START/ON_STOP
  → AppForegroundState
  → ConversationCoordinator 订阅：
       前台：曾挂起则重新获取麦克风；采集已死（isCapturing=false）则 start() 自愈；连接已死（DISCONNECTED）则 sdk.connect()
       后台：AudioCaptureController.onAppBackgrounded()
             ——仅"录音中且静音"时补满静音窗口、拆除采集并停 MicCaptureService
```

摄像头在离开会话页组合时 `unbind()`；录屏由前台服务维持，进程被杀后不重启（两个 FGS 均 `START_NOT_STICKY`）。

### 4.7 视觉上行（相机 / 图库 / 录屏）

| 来源 | 采集 | 上行 |
|---|---|---|
| 相机 | CameraX `Preview` + `ImageAnalysis`，开流暖机 1s、约 900ms 节流，JPEG q70、长边 ≤1280 | `sdk.pushVideoJpeg(frame, ..., facing, turnId)` |
| 图库 | 解码 + EXIF 旋正 + 长边 ≤1920、q70 | 同上，`cameraFacing="gallery"`；chat 模式带发图指令（`conv_image_action_text`，2026-10-27 已实施） |
| 录屏 | MediaProjection，浮窗点按单帧抓取 | `pushVideoJpeg(..., cameraFacing="screen", text=context.getString(R.string.conv_image_action_text))`（2026-10-27 已实施）；omni 模式不带 text |

- 相机与录屏互斥：开录屏会先关相机，开相机会先停录屏；translate/lecture/transcribe/omni 模式禁止录屏。
- `CaptureTurnCounter`：开相机（含进入页面自动开相机）与开始录屏由 ViewModel 推进；切换前后摄由 `CameraController` 在检测到首帧 facing 变化时推进。turnId 逐帧透传给服务端，SDK 不解释该值。
- 相机帧由 `CameraController` 直接编码 JPEG，走 `pushVideoJpeg` 直通路径（不做 SDK 侧二次压缩）。

### 4.8 会话模式切换

App 侧维护四个互斥标志：`isTranslateMode`、`isLectureMode`、`isTranscribeMode`、`isOmniMode`。

```
用户选择模式 → ViewModel 乐观置位 UI/adapter 标志 → sdk.sendSessionChange(mode, ...)
  → 收到 session.changed → adapter 以回声为准重置四标志
  → 若回声前收到错误码 1009（切换被拒）→ adapter 按 pending 模式回滚
  → 重连后收到 session.ready 时 adapter 先复位四标志；再依据"最近一次用户请求"补发
```

`transcribe` 还带有采集源选择（原声 `MIC` / 语音 `VOICE_COMMUNICATION`），由 ViewModel 的派生 collector 下发 `AudioCaptureController.switchAudioSource()`。

### 4.9 Agent job

- 收到 `job_status` **全量快照**（替换语义），adapter 更新 `agentJobs`。
- UI：头像旁的 `AgentJobStatusBadge`（头像隐藏时位于顶栏对应位置）→ 浮层 `AgentJobListPanel`；行内 × 进入确认态。
- 取消：`POST /api/mylife/agent-jobs/cancel`，404/409 视为已结束/不存在；结果文本作为 `ai-msg_agent_job_*` 消息进入对话并可全屏查看。
- 断连、`session.ended`、连接状态非 CONNECTED 时清空 job 列表，防止幽灵任务。

### 4.10 MyLife（时光机）

```
Conversation 更多菜单 → mylife
  TimelineViewModel 拉 overview（30/90/366 天视粒度）
  → 日/周/月/年 PeriodCard
      ├─ 图片 → Gallery（/timeline?types=video，分页 40）
      ├─ hot slot → HotSlotScreen（/timeline，窗口 [start,end]，分页 500）
      ├─ 搜索命中 → EventListViewer（window ts±10min）
      └─ 语音入口 → AudioGallery（/timeline?types=audio，分页 40）
```

- `MylifeRepository` 在内存中维护 overview 窗口并共享 60 秒 pending 轮询；`has_pending` 为真期间轮询，pending 消失后停止。Timeline 订阅 overview 并重聚合 periods；HotSlot/AudioGallery 在 overview 变化时重取首页（AudioGallery 播放中除外）；FrameGallery 不订阅 overview，进入或删除后自行重载。
- 详情屏均使用独立路由 push 在宿主之上，返回各自 `popBackStack`。
- 图片/音频媒体 URL 在 Repository 出口绝对化并附 token。

### 4.11 历史

- `HistoryScreen` 的数据只来自 Room：列表 + LIKE 搜索 + 删除/清空（写回 Room）。
- 若删除的是当前活动会话：`ConversationCoordinator.replaceWithNewSession()` 先切换新会话，再删除旧记录，避免活会话把消息写回已删行。
- `HistoryDetailScreen` 只读展示消息、支持图片查看器与 agent job 结果查看器。
- `HistoryUseCase` 只读写 Room，不调用远端接口；卸载 App 后本地历史即丢失。

### 4.12 设置

- "服务"：服务器地址/认证密钥输入、预设选择、`/healthz_token` 检测、保存后强制退出重启。
- "视频"：默认前置摄像头、进入对话自动开相机。
- "通用"：触觉反馈。
- "关于"：版本号；隐私政策/用户协议行存在，但当前点击回调为空（代码注释为 `CustomTabs URL`），未接 `WebView`。

---

## 5. 状态与线程模型

### 5.1 状态事实源

| 状态 | 事实源 |
|---|---|
| 连接/会话/VAD/带宽/连接代际 | `LivaChatSDK` 的 `StateFlow` |
| 实时转写、AI 流式文本、模式回声、agent job 快照 | `ConversationListenerAdapter` 的 `StateFlow` |
| 当前 Room 会话 id | `ConversationCoordinator.currentSessionId` |
| 采集/播放/静音/相机/录屏/设备选择 | 各 Media 控制器 |
| 会话列表/消息 | Room（经 Repository 转换为 Flow） |
| 用户偏好/服务器配置 | DataStore |
| MyLife 总览窗口 | `MylifeRepository.overview` |
| 页面级弹窗/覆盖层/查看器 | 各 ViewModel 的 UiState |

`ConversationViewModel` 负责状态合并、事件分发与页面级业务编排（启动门控、模式切换、录屏/相机互斥、查看器）；高频麦克风电平走独立的 `micLevel` StateFlow，避免 50Hz 更新驱动整屏重组。

### 5.2 线程

| 线程/调度器 | 用途 |
|---|---|
| Main | Compose、ViewModel、`ConversationCoordinator`、SDK 状态观察回调 |
| WebSocket reader（OkHttp） | SDK 入站消息、转写/AI 文本/job_status/错误回调、`ConversationListenerAdapter` 写流 |
| SDK audio-send dispatcher | `pushAudioPcm` 的 VAD/AGC/Opus/发送 |
| SDK audio-recv dispatcher | Opus 解码、`onAiAudioPcm` |
| `AudioCaptureController` IO 协程 | 阻塞式 `AudioRecord.read` 循环 |
| `AudioPlayback` 单线程（URGENT_AUDIO） | JitterBuffer 消费与 `AudioTrack.write` |
| `screen-capture` HandlerThread | MediaProjection/VirtualDisplay/ImageReader/ticker |
| `MylifeRepository` Default scope | overview 轮询、退屏刷新等后台请求 |
| 主线程 Handler | 浮窗操作与抓屏状态收尾 |

跨线程契约：

- adapter 的会话 id 用 `@Volatile`，供 WS reader 线程与主线程共读；
- `CompositeLivaChatListener` 在事件来源线程同步扇出，订阅者必须快速、不可抛；
- 采集/播放控制器内部通过锁与代次守卫处理 stop/rebuild 竞争。

### 5.3 Hilt 图

| Module | Scope | 提供 |
|---|---|---|
| `AppModule` | SingletonComponent | `LivaChatConfig`、`deviceId`、`LivaChatSDK`、`serverBaseUrl` |
| `DatabaseModule` | SingletonComponent | Room DB、DAO、DataStore |
| `MylifeModule` | SingletonComponent | MyLife API 基址、共享 CoroutineScope、trust-all OkHttp、Coil ImageLoader、AudioPlayer |
| `ServiceProbeModule`（`ServiceProbe.kt`） | SingletonComponent | `ServiceProbe` → `OkHttpServiceProbe` |
| `ConversationModule` | ViewModelComponent | 空模块（占位） |
| `MediaModule` | ActivityRetainedComponent | 空模块（占位） |
| `RepositoryModule` | SingletonComponent | 空模块（占位） |

核心控制器与 Repository 以 `@Singleton` 直接构造注入；接口型依赖（`AudioPlayer`、`ServiceProbe`）经 Module 绑定；ViewModels 使用 `@HiltViewModel`。

---

## 6. 架构约束与不变量

以下约束可从代码直接验证，改动时需要保持：

1. **App/SDK 边界**：SDK 不申请危险权限、不持有音频/相机设备；所有采集与播放生命周期由 App 控制。
2. **单 SDK 实例**：`provideLivaChatSDK` 为进程单例；会话的启停通过 `connect/startSession/disconnect` 表达。
3. **本地会话 id 与服务端 session id 分离**：Room 外键使用 UUID；重连 resume 使用服务端 `sess_*`。
4. **一天一会话**：`daily_sessions` 是"本地日 → 会话"的唯一映射；跨天自动新会话。
5. **消息事实源在 Room**：只有 `ConversationListenerAdapter` 写入最终消息；UI 不直接改消息列表（除输入中气泡/流式展示）。
6. **服务器配置是进程级装配**：DataStore 改动不热生效，必须重启进程。
7. **模式互斥**：translate / lecture / transcribe / omni 与 chat 互斥；非 chat 模式禁止录屏；相机与录屏互斥。
8. **前台服务成对**：录音运行时有 `MicCaptureService`，录屏运行时有 `ScreenCaptureService`；两者均不粘性重启。
9. **断连清理**：连接非 CONNECTED 时，agent job 列表、未完成的转写/流式文本会被清理；迟到的非 final 帧不得复活气泡。
10. **UI 事件单向**：Screen 的业务操作统一收敛为 `ConversationEvent`；业务状态判定（启动门控、录屏互斥、模式切换等）在 VM/控制器。权限申请与系统 Intent（相机权限、MediaProjection、悬浮窗设置页）由 Screen 直接发起。

---

## 7. 架构分析

### 7.1 现有设计的优点

- **职责边界清晰**：实时链路的协议/传输/编解码全部收敛在 SDK；Android 媒体与 UI 留在 App，两个模块可以独立测试。
- **状态流单向**：SDK/控制器/Repository 均以 `StateFlow` 暴露事实，ViewModel 合并后驱动 Compose，避免双向回调散落。
- **生命周期集中**：`ConversationCoordinator` 统一管理"建会话 → 连接 → 采集/播放 → 断开 → 本地收尾"，避免多个页面各自启停 SDK。
- **可靠性投入明确**：连接代次、采集代次、socket 代次、播放重建 CAS、消息 seq 闸口等机制在代码中都有明确实现，用于处理重连/重建/并发竞态。
- **本地优先**：历史、偏好、头像都能离线工作；实时链路的临时状态（agent job 快照、未完成转写/流式文本）不与历史持久化混淆。

### 7.2 耦合与风险（基于代码现状）

| 观察 | 影响 |
|---|---|
| `ConversationCoordinator`（domain）直接依赖 `ConversationListenerAdapter`（ui.screens.conversation）；`HistoryViewModel` 也依赖该 adapter | domain 与 UI 包形成反向依赖，复用/测试边界变弱 |
| `ConversationListenerAdapter` 同时承担事件适配、模式状态、Room 写入、断连清理 | 单类职责偏多，后续协议事件增加时容易继续膨胀 |
| 三个 DI 模块为空占位 | 模块结构表达了分层意图，但实际绑定仍集中在 `@Singleton` 自注入 |
| `CameraController.toggleCamera()` / `setZoom()` 为空实现 | 切换镜头实际依赖 UI 重组触发 rebind；缩放尚未接入 |
| `OnScreenStop` 事件已定义但会话页未发出 | 会话停止目前依赖 `ViewModel.onCleared()`，需要理解 Compose 导航/组合生命周期 |
| `AudioRouteManager` 仍保留 SPEAKER/EARPIECE/BLUETOOTH 路由；UI 已改为细粒度设备选择 | 两套音频出口模型并存，实际生效的是设备选择 + `AudioRouteManager` 的通信模式/焦点 |
| SDK `ConnectionManager` 与 MyLife client 均使用 trust-all TLS，Manifest 允许 cleartext | 当前为开发/自签阶段配置；发布前需收敛为系统证书校验（代码注释已标注） |
| `LivaChatAppMock` 复制了一套媒体控制器 | 仅用于 SDK 验证，不随产品演进；修改 SDK 接口时需同步维护 |
| 历史摘要为本地启发式（前 3 条用户消息截断 100 字） | 摘要质量有限，但保证离线可用 |
| MyLife 数据仅内存缓存 | 进入页面需重新拉取；无离线浏览 |

### 7.3 建议保持的演进原则

1. 新增协议事件优先落在 `ConversationListenerAdapter` 的流上，由 ViewModel 消费，避免 UI 直接订阅 SDK。
2. 新增媒体能力沿用"App 采集/播放 + SDK 编解码/传输"的边界，不把 `AudioRecord`/Camera 下沉进 SDK。
3. 新增本地持久化先判断是否属于"会话历史"；非历史数据（如 MyLife）继续走内存/HTTP，避免 Room 膨胀。
4. 涉及会话启停、设备重建、重连恢复的改动，应复用现有的代次/epoch 守卫，不引入第二套状态机。
5. 服务器地址/密钥仍按"进程装配"处理；若未来要热切换，应显式重建 SDK、MyLife client 与所有依赖它的单例。

---

## 8. 关键文件索引

| 关注点 | 文件 |
|---|---|
| 进程装配 / SDK 单例 / 服务器配置 | `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/di/AppModule.kt` |
| Room / DataStore | `.../app/di/DatabaseModule.kt`、`.../app/data/local/LivaChatDatabase.kt`、`.../data/local/PreferencesDataSource.kt` |
| 导航 | `.../app/ui/navigation/NavGraph.kt`、`Routes.kt` |
| 会话编排 | `.../app/domain/ConversationCoordinator.kt` |
| SDK 事件适配与落库 | `.../app/ui/screens/conversation/ConversationListenerAdapter.kt` |
| 会话 UI / 事件 / 状态 | `.../ui/screens/conversation/ConversationScreen.kt`、`ConversationEvent.kt`、`ConversationUiState.kt`、`ConversationViewModel.kt` |
| 音频采集/播放/打断 | `.../app/media/AudioCaptureController.kt`、`AudioPlaybackController.kt`、`JitterBuffer.kt`、`InterruptCoordinator.kt` |
| 相机 / 图库 / 录屏 | `.../app/media/CameraController.kt`、`GalleryPhotoSender.kt`、`ScreenCaptureController.kt`、`ScreenCaptureBubble.kt` |
| 设备 / 前台服务 | `.../app/media/AudioDeviceManager.kt`、`MicCaptureService.kt`、`ScreenCaptureService.kt`、`AppForegroundState.kt` |
| 本地历史 | `.../app/data/repository/ChatRepository.kt`、`HistoryRepository.kt`、`.../domain/usecase/SessionUseCase.kt` |
| MyLife | `.../app/data/remote/mylife/MylifeApi.kt`、`.../data/repository/MylifeRepository.kt`、`.../ui/screens/mylife/` |
| 设置与服务器预检 | `.../ui/screens/settings/SettingsScreen.kt`、`SettingsViewModel.kt`、`.../data/remote/ServiceProbe.kt` |
| SDK 门面 / 配置 / 监听器 | `apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatSDK.kt`、`LivaChatConfig.kt`、`LivaChatListener.kt` |
| SDK 网络 / 会话 / 管道 | `.../sdk/internal/network/ConnectionManager.kt`、`.../internal/session/SessionManager.kt`、`.../internal/pipeline/` |
| 协议 DTO | `packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated/` |
