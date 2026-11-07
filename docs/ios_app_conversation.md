# LivaChat iOS App 会话模块设计

**状态**：已实施（M1 文本闭环 + 后续模式/任务/相机能力；后台音频按 `docs/ios_background_support.md` 实现）

**日期**：2026-10-24
**上游文档**：
- `docs/ios_app_m1.md` §3–§4（Runtime、事件映射、重连、恢复）
- `docs/ios_sdk_design.md` §7.1（宿主集成流程）
- `docs/ios_app_audio.md`（音频契约）
- `docs/ios_mic_meter.md`（mic 电平段表、`MicLevelModel` 与 unmute 上行边界）

本文定义 `ConversationRuntime` / `ConversationEventAdapter` / `ConversationCoordinator` / `ConversationViewModel` 的职责与状态机。历史持久化、模式、agent job 等模块的细节由各自文档定义；本文只描述会话编排接口。

---

## 1. 组成与所有权

```
AppContainer
 └─ ConversationViewModel (ObservableObject, @MainActor)
     └─ ConversationRuntime (@MainActor)
         ├─ LivaChatSDKProviding          // 生产包装 LivaChatSDK
         ├─ ConversationEventAdapter      // 唯一 sdk.events 消费者
         ├─ ConversationCoordinator       // 会话/媒体生命周期
         ├─ AudioCapturing
         ├─ AudioPlaying
         ├─ AudioSessionControlling
         └─ InterruptCoordinator
```

- View 只观察 `ConversationViewModel`，不接触 SDK、不订阅 `events`。
- `ConversationEventAdapter` 只做“消费 + 转发”，业务判断全部在 Coordinator。
- Coordinator 持有 SDK/音频协议，不持有 SwiftUI/UIKit 类型。
- Runtime 是不可变配置对象；设置变更时整体重建，不修改已有 SDK。
- `ConversationViewModel` 可在没有 runtime 的情况下存在（配置缺失态），显示阻断提示。

---

## 2. 测试缝协议

定义在 App 侧，生产实现包装真实对象；测试注入 fake。

```swift
@MainActor
protocol LivaChatSDKProviding: AnyObject {
    var state: LivaChatState { get }
    func connect()
    func disconnect()
    func startSession(resumeSessionID: String?)
    func sendInterrupt(reason: InterruptReason)
}

protocol AudioCapturing: AnyObject {
    var lifecycleState: CaptureLifecycleState { get }
    var onLevel: (@MainActor (Int) -> Void)? { get set }
    var onFailure: (@MainActor (String) -> Void)? { get set }
    func start() throws
    func stop()
    func setMuted(_ muted: Bool)
    func topUpSilenceWindow()
}

protocol AudioPlaying: AnyObject {
    var isRunning: Bool { get }
    var hasBufferedOrScheduledAudio: Bool { get }
    var onActivityChange: (@MainActor () -> Void)? { get set }
    func start()
    func stop()
    func setMuted(_ muted: Bool)
    func flush(reason: PlaybackFlushReason, epoch: UInt64)
    func setTTSInputExpected(_ expected: Bool)
    func resumePreservingBuffer()
    func prepareForAudioReconfiguration()
}

@MainActor
protocol AudioSessionControlling: AnyObject {
    var engine: AVAudioEngine { get }
    var playbackNode: AVAudioPlayerNode { get }
    var onInterruptionBegan: (() -> Void)? { get set }
    var onInterruptionEnded: (() -> Void)? { get set }
    var onRouteChanged: (() -> Void)? { get set }
    var onMediaServicesReset: (() -> Void)? { get set }
    var appliedCaptureSource: TranscribeAudioSource? { get }
    var isEngineRunning: Bool { get }
    func activate(captureSource source: TranscribeAudioSource) throws
    func startEngine() throws
    func stopEngine()
    func deactivate()
}

@MainActor
protocol HapticProviding: AnyObject {
    func lightTap()
}
```

`LivaChatSDK` 通过空扩展满足 `LivaChatSDKProviding`；采集上行由 runtime 以闭包

```swift
{ [sdk] pcm, muted in sdk.pushAudioPCM(pcm, muted: muted) }
```

注入 `AudioCaptureController`，避免 Coordinator 触碰音频线程。

---

## 3. ViewModel 状态

```swift
struct ChatMessage: Identifiable, Equatable {
    let id: String
    let role: ChatRole          // .user / .assistant
    var text: String
    var isFinal: Bool
    let timestamp: Date
}

struct StreamingAIText: Equatable {
    let messageID: String
    var text: String
}

final class ConversationViewModel: ObservableObject {
    @Published private(set) var connectionState: ConnectionState = .disconnected
    @Published private(set) var sessionState: SessionState = .idle
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var currentTranscription: String = ""
    @Published private(set) var streamingAIText: StreamingAIText?
    @Published private(set) var isMicMuted = false
    @Published private(set) var isSpeakerMuted = false
    let micMeter = MicLevelModel()
    @Published private(set) var banner: ConversationBanner?
    @Published private(set) var isFirstSession = true
    @Published private(set) var requiresServerConfig = false
    @Published private(set) var micPermission: MicPermissionState = .unknown
    @Published private(set) var sessionEnded = false
    @Published private(set) var isBusy = false
}
```

`ConversationBanner`：

```swift
struct ConversationBanner: Equatable {
    enum Kind { case warning, error }
    let kind: Kind
    let message: String
    let action: BannerAction?   // .retry / .openSettings
    let autoDismissAfter: TimeInterval?  // recoverable = 5s；fatal/常驻 = nil
}
```

`MicPermissionState`：`.unknown / .granted / .denied / .undetermined`，来自 `AVAudioSession.recordPermission`。

ViewModel 只暴露语义化方法（`apply(event:)`、`resetTransient()`、`clearConversation()` 等），Coordinator 调用；不把 SDK 对象放进 Published。

`micMeter` 是叶子 `MicLevelModel`（`@Published private(set) var level: Int`，0...4），只由 `MicVolumeGlyph` 观察；`updateMicLevel(_:)` 接收 controller 的 Int 电平，muted 时强制 0，`resetMicLevel()` 供 coordinator 在媒体 stop/restart 时同步归零。

---

## 4. 事件映射

`ConversationEventAdapter`：

```swift
@MainActor
final class ConversationEventAdapter {
    private let onEvent: (LivaChatEvent) -> Void
    private var task: Task<Void, Never>?

    func start(_ events: AsyncStream<LivaChatEvent>) {
        guard task == nil else { return }
        task = Task { [weak self] in
            for await event in events {
                guard !Task.isCancelled else { break }
                guard let self else { break }
                await MainActor.run { self.onEvent(event) }
            }
        }
    }

    func stop() { task?.cancel(); task = nil }
}
```

- 单消费者：runtime 生命周期内只调用一次 `start`；`stop` 后不可重启同一实例。
- 事件在 MainActor 顺序处理；audioSink 走独立队列，二者无全局顺序，门控按 §4.1。

Coordinator 的映射：

| SDK 事件 | 处理 |
|---|---|
| `.connectionStateChanged(state)` | 更新 `connectionState`；非 `.connected` 清空临时文本、`sessionReady=false`、按 `playbackEpoch` flush，并更新 TTS input-expected；随后 `reconcileMedia(.connectionState)` 按当前前后台策略保持/降级/teardown |
| `.connectionEpochAdvanced(epoch)` | `guard isStarted && sdk.state.connectionState == .connected`；`sdk.startSession(resumeSessionID: serverSessionID)` |
| `.connectionTerminated(reason)` | `authFailed` → 停止媒体、fatal banner + Open Settings；`reconnectExhausted` → 停止媒体、error banner + Retry；`serverClosed` → 清 `serverSessionID`、停媒体、会话结束 banner + Retry |
| `.sessionStateChanged(state)` | 更新 `sessionState`；驱动头像/状态文案；喂给 InterruptCoordinator |
| `.localVadStateChanged(state)` | 喂给 InterruptCoordinator；不直接改 UI（M1 头像仍由 sessionState 驱动） |
| `.sessionReady(id, restored)` | 保存 `serverSessionID = id`；`isFirstSession = false`；`restored == false` → `viewModel.clearConversation()`；`sessionEnded = false` |
| `.sessionEnded(reason, id)` | `serverSessionID = nil`；`sessionEnded = true`；清转写/流式文本；停止媒体；warning banner “会话已结束” + Retry；不自动开新会话 |
| `.transcription(text, final, id)` | 门控见 §4.1；interim → `currentTranscription = text`；final → 非空则追加 user 消息，清空 `currentTranscription` |
| `.aiText(text, messageID, index, final, image, source)` | 门控见 §4.1；`index == 0` 重置文本，否则追加；final → 追加 assistant 消息（id `ai-<messageID>`），清空流式；忽略 image/source |
| `.playbackShouldFlush(reason, epoch)` | `playback.flush(reason: reason, epoch: epoch)` |
| `.error(code, message, fatal, recoverable)` | fatal/不可恢复 → error banner 常驻；recoverable → warning banner 5s；消息转换见 §6 |
| `.jobStatus` / `.videoAck` / `.sessionChanged` | M1 忽略（不展示、不持久化、不改变 chat 默认） |

### 4.1 门控

- 维护 `lastConnectionState`（由 `.connectionStateChanged` 更新）。
- `transcription`/`aiText` 的 **非 final** 帧：只有 `lastConnectionState == .connected && sessionReady` 才接受；断连后迟到帧直接丢弃。
- final 帧：同样要求 connected + sessionReady；M1 不尝试补收断连期间的 final 消息。
- 不依赖跨来源事件顺序；需要时读 `sdk.state` 快照：
  - epoch 处理读 `connectionState`；
  - 断连 flush 读 `playbackEpoch`。
- `sessionReady` 前收到的 `transcription/aiText` 丢弃（SDK 闸门之外的第二道保护）。

---

## 5. Coordinator 生命周期

### 5.1 start

```swift
func start() {
    guard !isStarted else { return }
    guard let config = launchConfig else {
        viewModel.requiresServerConfig = true
        return
    }
    isStarted = true
    viewModel.requiresServerConfig = false
    viewModel.micPermission = permissionProvider.current()
    subscribeAudioCallbacks()

    installAudioHooks()
    resetMediaPipeline()

    if isForeground() {
        try audioSession.activate(captureSource: appliedAudioSource)
        try capture.start()          // 安装 tap 并启动 engine；失败/重接受三态管理
        try audioSession.startEngine() // 幂等确认
        playback.start()             // engine 运行后才能 play()
    }
    interrupt.start()
    adapter.start(sdk.events)
    sdk.connect()
}
```

- 幂等：重复调用无副作用；从 Settings 返回不重启。
- `start()` 只在前后台满足时启动媒体；后台完成 runtime 启动时只连接，回前台由 `onForeground()` 启动 full。
- 启动失败（音频/配置）会走完整 teardown，不留下部分资源；等待用户 Retry 或 App 回前台。

### 5.2 stop

```swift
func stop() {
    guard isStarted else { return }
    isStarted = false
    teardownMedia()             // 无条件：capture/playback/engine/session
    adapter.stop()
    interrupt.stop()
    sdk.disconnect()
    uninstallAudioHooks()
    serverSessionID = nil
}
```

- 不主动 `sdk.endSession`（进程退出/系统清理；与 Android 现状一致）。
- `stop` 用于设置重建/离开会话；不显示“已结束”，由调用方决定 UI。

### 5.3 重连

```
connectionStateChanged(.reconnecting/.connecting)
  → 状态黄色；清空临时文本
connect 成功 → connectionEpochAdvanced
  → startSession(resumeSessionID: serverSessionID)
sessionReady(restored: true)  → 继续
sessionReady(restored: false) → clearConversation()
```

- `serverSessionID` 只保存服务端下发的 `sess_*`；不传本地 UUID。
- `.sessionEnded` 清空后，下一次 `connectionEpochAdvanced` 用 `nil` 新会话。
- `authFailed` 不自动重试；`reconnectExhausted`/`serverClosed` 需用户点 Retry。

### 5.4 Retry

`retry()`：

1. 配置缺失 → `viewModel.requiresServerConfig = true`，不连接；
2. mic 权限未授予 → 只清理 banner，不启动媒体、不连接；
3. 调用 `reconcileMedia(reason: .retry)`：前台恢复 full，后台按 mic/TTS 策略恢复；失败时显示音频错误并停止后续连接；
4. `sessionEnded` 或已连接但无会话 → `sdk.startSession(resumeSessionID: nil)`；
5. `connectionState == .disconnected` → `sdk.connect()`；
6. 其他（connecting/reconnecting）→ no-op，避免打断 SDK 退避。

### 5.5 前后台

| 阶段 | 动作 |
|---|---|
| `.background` | 停相机并保留 `cameraResumeIntent`；`reconcileMedia(.background)`：mic 未静音 → 保持 full；mic 静音 → 先 topUp 静音窗口，再完整 teardown（停 playback/capture/engine、`deactivate()`），释放麦克风并停止后台媒体。 |
| `.active` | guard `!sessionEnded && !awaitingAuthFix && micPermission == .granted`；`reconcileMedia(.foreground)` 恢复 full；`disconnected` 时连接；成功恢复音频 banner 后恢复相机。 |

- `UIBackgroundModes = [audio]` 已启用；后台未静音时保持采集与播放，mic 静音时停止媒体，App 可挂起。
- `sessionEnded`、auth 失败、权限丢失回前台不自动启动媒体或新会话。
- 中断、route change、engine configuration change、media reset 与前后台共用 `reconcileMedia()`，不再各自 stop/start。

### 5.6 设置重建

由 `AppContainer` 执行，Coordinator 提供 `stop()`：

1. `runtime.stop()`（停事件消费/媒体/连接，清 `serverSessionID`）；
2. 重置 ViewModel（消息、转写、banner、`sessionEnded`）；
3. 重建 `AudioSessionController`/`AudioPlaybackController`/SDK（新 `playbackEpoch` 从 1 开始）；
4. 新 runtime `start()`。

设置页本身不停止 runtime；只有“保存并应用”触发重建。

---

## 6. 错误映射

| 来源 | UI |
|---|---|
| SDK `.error(code, message, fatal, recoverable)` | `fatal` 或 `!recoverable` → error banner（无自动消失，action 关闭）；`recoverable` → warning banner 5s |
| `.connectionTerminated(.authFailed)` | “认证失败，请检查服务器配置” + Open Settings；停止媒体 |
| `.connectionTerminated(.reconnectExhausted)` | “无法连接到服务器” + Retry；停止媒体 |
| `.connectionTerminated(.serverClosed)` | “连接已关闭” + Retry；清 `serverSessionID` |
| `.sessionEnded` | “会话已结束” + Retry；停媒体 |
| capture `onFailure` | “麦克风不可用” warning/error banner；重试由用户触发 |
| audio session 启动失败 | “音频不可用” warning banner |

- SDK message 只作为副文案；主文案用本地化 key。错误 code 进日志，不展示给用户。
- recoverable banner 5s 自动消失；用户点击关闭也清除；新 banner 覆盖旧 banner。

---

## 7. 单元测试（fake 驱动）

测试文件：`apps/ios/LivaChatApp/LivaChatAppTests/ConversationCoordinatorTests.swift`。

Fakes：

```swift
final class FakeSDK: LivaChatSDKProviding { ... }       // 可注入 state、记录调用
final class FakeCapture: AudioCapturing { ... }
final class FakePlayback: AudioPlaying { ... }
final class FakeAudioSession: AudioSessionControlling { ... }
final class FakeHaptic: HapticProviding { ... }
final class FakeClock: () -> TimeInterval { ... }
```

用例：

1. `start` 幂等：重复调用 connect 一次；
2. 配置缺失：不 connect，`requiresServerConfig=true`；
3. `connectionEpochAdvanced` + connected → `startSession(nil)`；
4. 重连：保存 `serverSessionID` 后 epoch → `startSession("sess_...")`；
5. `sessionReady(restored:false)` 清消息/临时文本；`restored:true` 保留；
6. `sessionEnded` 清 `serverSessionID`、停媒体、banner + Retry；
7. 断连迟到 interim/final 被丢弃；断连清空临时文本但保留消息；
8. `.playbackShouldFlush` 转发 epoch；
9. `.connectionStateChanged(.reconnecting)` 触发 playback flush（读状态 epoch）；
10. authFailed → 停媒体 + Open Settings banner；
11. `retry` 在 disconnected 时 connect，在已连接/结束后 startSession；
12. 后台未静音保持 full；capture recovering 不取消重试；capture stopped 重启 full；
13. 后台静音：topUp 恰好一次，完整 teardown 并释放麦克风；后台不再播放 TTS；
14. 后台内静音↔未静音切换：静音即停、取消静音即恢复 full；
15. stopped + 新 PCM：后台仅在 mic 未静音时事件驱动恢复 full，mic 静音不启动媒体；前台始终恢复 full（按设计丢弃停机期间缓冲的 TTS）；
16. 前台从 stopped 恢复 full；恢复失败 teardown + banner，重试成功后清除；
17. engine/route 恢复：capture 处于 stopped/recovering 时重启共享 engine 并 resume，不重复 fresh start；
18. capture failure / interruption / route / media reset / auth / session end 的 teardown 与恢复策略；
19. 相机后台停止保留 intent，后台音频事件不消费 intent，前台恢复不 bump turn；
20. 自动恢复成功后定向清除 audio banner，保留 auth/session/camera banner；
21. InterruptCoordinator 由 session/VAD 事件驱动（条件、节流、触觉）；
22. AI 文本 `index==0` 重置、增量拼接、final 落消息；
23. transcription final 只追加非空消息并清空 interim。

集成测试：

- `ConversationRuntimeTests`：用真实 adapter + fake SDK 事件流，验证单消费者、stop 后不再处理。
- Mock Server E2E 见 `docs/ios_app_m1.md` §9.2；通过 debug 启动配置连接，不用 fake。

---

## 8. 不做

- 不实现本地消息持久化、历史、Room/MyLife；
- 不实现 `session.change`、模式 UI；
- 不实现 agent job、video ack、图片；
- 不实现显式“结束会话”按钮；
- 不在断连时本地 flush 用户打断；
- 不把 SDK 事件流暴露给 View。

---

## 9. Review 记录

### 9.1 音频采集 controller diff 的可 review 性与后续拆分（2026-10-25）

评审对象：`Core/Audio/AudioCaptureController.swift` 的未提交变更（mic meter、PCM 隐私、uplink 串行化、conversion recovery、retry 状态机；相关测试见 `AudioCaptureLevelTests` / `AudioCaptureRetryTests` 与 `docs/ios_mic_meter.md` §6.3）。

> 这次 controller diff 已扩展到约 563 行，包含 meter、PCM 隐私、uplink 串行化、conversion recovery 和 retry 状态机。虽然多数修改有依据，但它们的风险等级明显高于普通 UI 电平需求。
>
> 从可 review 性看，建议后续将这类工作拆成：
>
> 1. Mic meter 算法、叶子模型与 glyph；
> 2. unmute/PCM 隐私边界；
> 3. conversion/retry 生命周期并发修复。
>
> 不建议继续在当前基础上叠加更多状态逻辑；优先提取共用生产 helper、补齐上述确定性测试。
