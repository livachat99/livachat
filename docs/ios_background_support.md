# iOS 后台支持（Background Audio）分析与设计

> **状态**：已实施（R3，2026-10-24；单元测试、模拟器 UI 测试、iPhone 11 真机验证）
> **日期**：2026-10-24
> **范围**：iOS App（`apps/ios/LivaChatApp`）会话期间的进程前后台处理、后台麦克风采集、TTS 播放、音频会话、回前台恢复与中断恢复。
> **参考**：Android 行为见 [`android_background_support.md`](android_background_support.md)；当前 iOS 音频实现见 [`ios_app_audio.md`](ios_app_audio.md)、前后台接线见 [`ios_app_shell.md`](ios_app_shell.md)。
> **非目标**：后台摄像头、ReplayKit/屏幕采集、CallKit/VoIP、后台音频画廊、服务端/SDK 协议改造、进程被杀后的自动恢复。
> **R2 修订**：落实首轮 review；产品已确认“麦克风静音只关闭输入，不得因此中止 AI 声音”，因此不再推荐静音即停止全部媒体的方案 A，改为采集/播放独立生命周期。
> **R3 实施修订**：真机验证发现共享 `AVAudioEngine` 在 `playAndRecord`/voice-processing 使用后无法在 `.playback` 下重启。最终实现采用**双 engine**（capture engine + playback engine）；后台播放只保留独立 playback engine。具体实现以 `docs/ios_app_audio.md` 为准。本文 §2 保留实施前分析作为背景，§5/§8/§9 已按最终实现更新。
> **R4 决策修订（2026-10-24，实现已变更）**：真机发现双 engine 导致 TTS 音量明显下降且 AEC 失效——播放不在 VPIO 的 AEC 参考路径中，并被 `.voiceChat` 当作 "other audio" ducking。现回归**单 `AVAudioEngine`**（采集与播放共用）。后台策略简化为：mic 未静音保持 full；mic 静音则 teardown、停媒体并 `deactivate()` 释放麦克风，后台不再播放 TTS；回前台恢复 full。本文 R2/R3 的 `.playbackOnly`、双 engine 与后台 TTS watchdog 分析已被取代，§2–§9 相关内容仅作历史记录。当前实现以 `docs/ios_app_audio.md` §3.3 为准。

---

## 1. 结论摘要（R4）

**最终策略：单 engine；后台按 mic 静音状态二选一。**

1. `Resources/Info.plist` 保留 `UIBackgroundModes = [audio]`。
2. **前台**：full；采集与 TTS 播放共用同一个 `AVAudioEngine`，VPIO/AEC 正常。
3. **后台 + mic 未静音**：保持 full；采集与播放继续（同通话类 app）。
4. **后台 + mic 静音**：先补满静音窗口，再 `teardownMedia()` 停 playback/capture/engine 并 `deactivate()`；麦克风真正释放，后台不再播放 TTS。回前台重新激活并恢复 full。
5. VPIO 是全双工 I/O unit：单 engine 在保留播放时无法释放麦克风；这是本策略的物理依据，也是后台静音时停媒体的原因。
6. 不使用 `beginBackgroundTask` 做长期保活，不播放静音音频，不使用 `voip` 后台模式。
7. 后台静音导致 App 可能被挂起、服务端会话可能超时；回前台由现有 reconnect/session 逻辑恢复。
8. `teardownMedia()` 必须幂等；后台音频重启不得消费 `cameraResumeIntent`。
9. 历史方案（R2/R3）的 `.playbackOnly`、双 engine、后台 TTS 活动/watchdog 机制已移除。

---

## 2. 当前实现（代码事实）

### 2.1 App 前后台接线

`LivaChatApp` 在根视图监听 `scenePhase`：

```swift
.onChange(of: scenePhase) { phase in
    container.handleScenePhase(phase)
}
```

`AppContainer.handleScenePhase(_:)`：

- `.active`：
  - `isForeground = true`；
  - 刷新麦克风/相机权限；
  - 仍在 Permission 页时按已授权状态进入 Conversation；
  - Conversation 页存在 runtime 时调用 `coordinator.onForeground()`；
  - 麦克风权限丢失时调用 `onMicPermissionLost()` 并显示 `.micUnavailable`。
- `.background`：`isForeground = false`，调用 `coordinator.onBackground()`。
- `.inactive`：不做任何事，避免 Control Center/系统弹窗等短暂非活跃状态停音频。

`AppDelegate` 只负责屏幕方向，不实现 `applicationDidEnterBackground` / `applicationWillEnterForeground`。`AppContainer` 把 `isForeground` 闭包传给 `ConversationCoordinator`。

### 2.2 音频会话与媒体启动

`AudioSessionController.activate()` 当前配置：

```swift
try session.setCategory(
    .playAndRecord,
    mode: captureSource == .voice ? .voiceChat : .measurement,
    options: [.defaultToSpeaker, .allowBluetooth, .allowBluetoothA2DP]
)
try session.setActive(true)
try engine.inputNode.setVoiceProcessingEnabled(captureSource == .voice)
```

- 采集与播放共用一个 `AVAudioEngine`：
  - `AudioCaptureController` 在 input node 安装 tap；
  - `AudioPlaybackController` 使用同一 engine 的 `AVAudioPlayerNode` 和 20 ms 播放定时器。
- R4 最终只有 `.playAndRecord` 配置（`.voice`/`.original`）；后台 mic 静音时不再切 `.playback`，而是完整 teardown 并 `deactivate()`，以系统麦克风指示器熄灭为准。
- 当前 `startFullMediaFresh()` 顺序：
  1. `audioSession.activate(captureSource:)`；
  2. `capture.setMuted(viewModel.isMicMuted)`；
  3. `capture.start()`（安装 tap 并启动 engine）；
  4. `audioSession.startEngine()`（幂等确认）；
  5. `playback.start()`；
  6. `playback.setMuted(viewModel.isSpeakerMuted)`。
- `stopMedia()` 顺序：`capture.stop()` → `playback.stop()` → `stopEngine()` → `deactivate()`，最后 `mediaStarted = false`。

当前有两个必须在实施前修正的资源语义：

1. `mediaStarted` 只在启动最后一步置 true，但 `stopMedia()` 在其为 false 时直接返回。若启动中途失败，已经激活的 session 或部分组件可能没有回滚。
2. `mediaStarted` 只表示启动意图，不证明 capture、tap、player 或 engine 健康，也不能作为是否存在待清理资源的完整依据。

`AudioCaptureController.start()` 遇到可重试错误时会启动内部重试并正常返回。因此 `startMedia()` 返回后可能处于“engine 已运行、capture recovering、尚未产生 PCM”的状态；后台策略不能把这段时间误判为永久失败。

### 2.3 当前播放活动信息不足

`AudioPlaybackController` 当前具有 `running`、`primed`、jitter buffer 和 20 ms pump，但：

- `running` 只是播放生命周期，不表示有可听音频；
- `playerNode.isPlaying` 也不能证明仍有未播放 buffer；
- `start()` 会 reset jitter buffer，不能用于要求保留 TTS 的配置切换；
- `scheduleBuffer()` 没有跟踪完成回调，无法精确判断已调度音频是否排空；
- pump 初次 priming 要求 jitter buffer 至少达到 `targetFrames = 3`；如果没有 end-of-input 语义，不足 3 帧的短 utterance/尾帧可能永不调度；
- `AudioPlaying` 没有 `hasBufferedOrScheduledAudio`、activity 回调或保留缓冲的 resume 接口；
- `livaChat(didReceiveAudio:)` 在 `running=false` 时仍会把帧推入 jitter buffer（`testSinkPushIsNonBlockingWithoutStart` 已验证），因此“已停止/已挂起”时新 TTS 可能已经到达；从 stopped 启动 playback-only 不得调用会 reset buffer 的 `start()`；
- `AudioPCMFrame.turnID` 当前为空，帧本身没有 end-of-utterance 标志。可结合 SDK `sessionState == .speaking`、连接/session ready 和播放队列状态判断，不需要改协议。

R2 所需的播放空闲检测必须至少覆盖“jitter buffer 非空”与“已调度但尚未播放完成的 buffer”，不能只看 `running` / `isPlaying`。同时还必须把“后续不再有输入”传给 playback，使不足 priming 阈值的尾帧也能播放并排空，否则非空 buffer 会让 playback-only 永久无法进入 idle。

### 2.4 当前后台行为

`ConversationCoordinator.onBackground()` 当前实现：

```swift
guard isStarted else { return }
if viewModel.isMicMuted {
    capture.topUpSilenceWindow()
}
if viewModel.isCameraOn {
    cameraResumeIntent = true
    camera?.stop()
}
stopMedia()
```

即：

- 无论麦克风是否静音，进入后台都会停止采集、播放和音频引擎，并释放 `AVAudioSession`。
- 静音时先补满静音窗口，再停止媒体。
- 相机在退后台时停止，但保留 `cameraResumeIntent`，回前台可恢复。
- SDK 连接不主动断开；媒体停止后 App 可能被系统挂起。

该行为不满足已确认的产品语义，因为 mic mute 退后台会同时中止 TTS。

### 2.5 当前回前台、中断恢复与相机顺序

`ConversationCoordinator.onForeground()` 当前在媒体未启动时重建媒体，并在连接为 `.disconnected` 时重连；相机按 `cameraResumeIntent` 恢复。

`restartMediaIfPossible()` 当前要求 `isForeground()`，由以下回调触发：

- `onInterruptionEnded`（仅 `.shouldResume`）；
- `onRouteChanged`（300 ms 去抖）；
- `onMediaServicesReset`。

`onInterruptionBegan` 会停止相机并 `stopMedia()`。放宽到后台前必须注意两个现有细节：

1. `resumeCameraIfPossible()` **先清除 `cameraResumeIntent`，再检查 `isForeground()`**。如果后台直接调用，会吞掉恢复意图并把相机状态置为关闭；后台路径必须不调用它，或先把 foreground guard 前移。
2. 后台启动失败后若回前台自愈成功，应清除本次对应的 `.audioUnavailable` / `.captureFailed` 状态，避免功能已恢复但错误 banner 仍悬挂；不得误清其他 banner。

即使 R2 计划增加 capture 三态（或 `isActive` 便捷查询），也只能表达控制器生命周期，不能证明 tap 持续产出 PCM。真正的 PCM liveness 需要 `lastBufferTime` 或 tap 序号等信息，不应混为一谈。

### 2.6 API 配置与现有测试

- `Resources/Info.plist` 没有 `UIBackgroundModes`。
- 工程没有 `beginBackgroundTask`、`BGTaskScheduler`、ReplayKit、CallKit、PushKit/VoIP。
- 部署目标为 iOS 15.0；本文涉及的音频能力可用，具体后台行为必须真机验证。
- `ConversationCoordinatorTests` 中 `testBackgroundStopsMediaAndForegroundRestartsAndReconnects` 需要按 full/playback-only/stopped 策略拆分。
- `testBackgroundStopsCameraAndForegroundResumesWithoutTurnBump` 的相机行为应保留，并增加“后台音频重启不消费恢复意图”的组合测试。
- `testForegroundAfterSessionEndedDoesNotRestartMedia`、`testAuthFailureBlocksForegroundAutoReconnect`、`testForegroundWithoutPermissionDoesNotStartMedia` 的“不自动复活”语义应保留。
- `docs/ios_app_shell.md` 对 `sessionEnded` 的描述与代码/测试不一致；实施时同步修正。

### 2.7 与 Android 的对照

| 维度 | Android 当前 | iOS 当前 | iOS R2 建议 |
|---|---|---|---|
| 后台执行机制 | `MicCaptureService`（microphone FGS） | 无；退后台停媒体 | `UIBackgroundModes: audio` + 与实际业务匹配的活跃音频配置 |
| 用户可见提示 | 常驻通知 | 无 | 系统麦克风指示器；不伪造常驻通知 |
| 后台未静音 | 保持采集 + FGS | 停止全部媒体 | 保持 full 采集 + 播放 |
| 后台静音 | 释放采集/FGS；播放不因退后台单独停止 | 补静音后停止全部媒体 | 释放采集/麦克风；TTS 独立继续，空闲后释放播放 |
| 回前台恢复 | Coordinator 自愈采集/连接 | `onForeground()` 重建媒体/连接 | full/playback-only 状态感知恢复，保留播放缓冲 |
| 中断/路由变化 | 由音频焦点/系统处理 | 前台才重启媒体 | 按目标 pipeline 恢复；后台不触碰相机恢复意图 |
| 屏幕采集 | `ScreenCaptureService` | 无 ReplayKit | 不在本设计范围 |
| 进程死亡 | FGS 不粘性、无 boot receiver | 无自动恢复 | 不承诺自动恢复 |
| 前后台 debounce | `ProcessLifecycleOwner` 约 700 ms | `scenePhase` | 不以 Android 的 700 ms 为前提 |

---

## 3. 设计目标与约束

### 3.1 目标

1. **后台语音对话可用**：会话在前台启动后，退后台/锁屏且未静音时，麦克风继续采集、TTS 继续播放。
2. **输入与输出解耦**：麦克风静音只关闭输入，不得直接停止、flush 或清空 AI 播放。
3. **后台静音可继续听 AI**：已有或正在到达的 TTS 在 playback-only 模式继续，播放真正空闲后释放资源。
4. **保留会话语义**：`sessionEnded`、认证失败、麦克风权限丢失后不自动复活；`stop()` 后不复活。
5. **保留相机行为**：退后台停止相机，回前台按意图恢复；后台音频事件不得消费相机恢复意图。
6. **隐私可控**：后台 mic mute 后释放采集和麦克风硬件；不播放静音音频维持进程。
7. **平台合规**：只在实际采集或 TTS 业务期间使用 audio background mode；无音频业务时允许挂起。
8. **失败可回滚**：任何启动/切换失败都完整清理部分资源，不在后台形成无限重启循环。
9. **不改协议**：优先复用现有 SDK session 状态和 PCM 帧，不改 SDK、服务端或编解码协议。

### 3.2 非目标

- 后台摄像头、后台屏幕采集/ReplayKit；
- 后台音频画廊，其 `scenePhase == .background` 暂停行为保持现状；
- 进程被用户强杀/系统终止后的自动拉起；
- 没有采集或 TTS 活动时的长期后台连接保活；
- 播放空闲并挂起后，由新 TTS 主动唤醒 App；
- Android FGS/通知的等价物；
- `session.ended` 后自动开新会话。

---

## 4. 设计选项

### 4.1 方案 A（不采用）：静音退后台即停止全部媒体

优点仍是实现简单，但它会在 mic mute 时停止 playback，违反已确认的产品语义，也会造成 iOS 与 Android 播放行为不一致。

**决策：否决，不进入实施。**

### 4.2 方案 B（推荐）：采集与播放独立生命周期

- 后台未静音：full（采集 + 播放）。
- 后台静音：补静音窗口、停止采集；有 TTS 活动时转 playback-only，不停止播放。
- playback-only 使用无麦克风输入的音频配置，不能只把 tap 移除就宣称隐私释放完成。
- TTS 活动结束且播放排空后停止 engine/session；App 可挂起。
- 回前台恢复 full，配置切换不得把 playback 当作新会话重新 start 而清空缓冲。

优点：

- 满足“mute 输入不影响对方声音”；
- 与 Android“后台只直接调整采集，不单独暂停播放”的语义一致；
- 没有真实采集/TTS 活动时可以释放资源并挂起；
- 不需要 CallKit、VoIP 或静音保活。

代价：

- 需要独立的 pipeline 状态、播放活动检测和保留缓冲的重启路径；
- full ↔ playback-only 的切换可能需要重配 engine/session，必须保证麦克风确实释放且不清空 TTS；
- 测试面大于方案 A。

### 4.3 已排除方案

- **继续前台-only**：不满足后台语音目标。
- **永久保持 playback-only，即使长期无 TTS**：把后台音频变成连接保活，耗电与审核边界不清；本设计只在 TTS 活动/有界等待期间保持。
- **`beginBackgroundTask` 长期保活**：系统只给有限时间，不能替代后台音频。
- **播放静音/空音频保活**：不符合后台音频用途，且有审核与电池风险。
- **`voip` / CallKit**：当前没有电话呼叫或来电唤醒需求，不引入。
- **后台摄像头/ReplayKit**：另立设计。

---

## 5. 推荐设计（方案 B）

### 5.1 Info.plist

在 `apps/ios/LivaChatApp/Resources/Info.plist` 增加：

```xml
<key>UIBackgroundModes</key>
<array>
    <string>audio</string>
</array>
```

- 只添加 `audio`，不添加 `voip`、`location`、`fetch` 等。
- 该能力不需要新增 entitlement；Xcode 的 Background Modes 开关最终也是写这个 key。
- 现有 `NSMicrophoneUsageDescription` 已存在；不需要新的运行时权限。
- App Review 说明必须同时覆盖后台未静音采集和后台静音时的真实 TTS 播放，不得描述成连接保活。

### 5.2 统一状态模型

在 `ConversationCoordinator` 内显式维护 pipeline，而不是继续用一个 `mediaStarted` 推断所有状态：

```swift
enum MediaPipelineState {
    case stopped
    case full                 // playAndRecord：capture + playback
    case playbackOnly         // 无输入配置：playback only
}

enum CaptureLifecycleState {
    case stopped
    case recovering           // 初始启动或重试中，允许有界恢复
    case running
}
```

要求：

- `pipelineState` 表示目标/已应用的配置，不单独承担资源所有权；任何路径失败都可调用无条件 teardown。
- `AudioCapturing` 暴露至少三态生命周期。`recovering` 与 `stopped` 必须区分，不能只有瞬时 `isActive`。
- `AudioPlaying` 暴露播放活动，不把 `running` 当作有声音：

```swift
var hasBufferedOrScheduledAudio: Bool { get }
var onActivityChange: (() -> Void)? { get set }
func setTTSInputExpected(_ expected: Bool)
```

`hasBufferedOrScheduledAudio` 至少包括：

1. jitter buffer 中仍有帧；
2. 已 schedule 给 player、但 completion 尚未确认播放完成的 buffer。

Coordinator 根据 `connected + sessionReady + speaking` 设置 `setTTSInputExpected`。当后续输入结束（`expected=false`）时，pump 必须允许不足 `targetFrames` 的剩余尾帧绕过初次 priming 阈值并播放排空；这不是 flush，也不得丢弃这些帧。新 utterance 到达时可重新进入正常 buffering/priming 策略。

建议使用 `scheduleBuffer` 的 `.dataPlayedBack` completion 跟踪未播放数量，并用 generation 使 stop/flush 前的迟到 completion 失效。activity 回调切到 MainActor 后只做策略 reconcile，不在音频队列直接操作 coordinator。

### 5.3 后台策略

公共资格保留现有“不自动复活”语义：

```swift
private var isSessionMediaEligible: Bool {
    isStarted
        && !sessionEnded
        && !awaitingAuthFix
        && viewModel.micPermission == .granted
}
```

TTS 活动由实际缓冲/调度状态和当前流状态共同判断：

```swift
private var hasTTSActivity: Bool {
    if backgroundTTSWaitExpired { return false }
    if playback.hasBufferedOrScheduledAudio { return true }
    return sessionReady
        && sdk.state.connectionState == .connected
        && sdk.state.sessionState == .speaking
}
```

后台目标 pipeline：

```swift
private func desiredBackgroundPipeline() -> MediaPipelineState {
    guard isSessionMediaEligible else { return .stopped }
    if !viewModel.isMicMuted { return .full }
    guard !viewModel.isSpeakerMuted, hasTTSActivity else { return .stopped }
    return .playbackOnly
}
```

含义：

- mic 未静音时，即使 speaker muted，也保持 full 采集；playback volume 仍为 0。
- mic muted 不会直接停止 playback；只有 speaker muted 或没有 TTS 活动时，才没有 playback-only 的后台理由。
- `sessionReady` 在断连时清除，避免旧 `.speaking` 状态在重连期间无限保活。
- `.speaking` 只表示仍可能有 TTS 到达，不能单独形成无上限等待，见 §5.6。
- `setTTSInputExpected` 使用相同的连接/session/utterance 状态驱动，但不包含本地 buffer 条件；watchdog 到期或 teardown 时必须置 false。新 PCM 可清除 wait-expired 并恢复播放资格，但只有 `connected + sessionReady + speaking` 时才重新等待后续输入。

### 5.4 音频配置与麦克风释放

> **R4 已废弃本节的双 engine / `.playbackOnly` 机制。** 当前只有一个 engine；后台 mic 静音时直接 teardown 并 `deactivate()`，不再切换 `.playback` category，也不再保留后台 TTS。以下内容保留为 R3 历史设计。

`AudioSessionController` 增加明确的配置语义，可命名为：

```swift
enum AudioPipelineConfiguration {
    case full(captureSource: TranscribeAudioSource)
    case playbackOnly
}
```

- `.full`：capture engine 使用 `.playAndRecord`、当前 mode/options，并按 capture source 配置 voice processing；playback engine 独立运行。
- `.playbackOnly`：使用无录音输入的配置 `.playback` + `.spokenAudio`，禁用 capture engine 的 voice processing；capture tap/engine 必须已经停止/移除，独立 playback engine 保持运行。

从 full 进入 muted background（目标为 playback-only 或 stopped）时，先通过统一的 `suspendCaptureForMutedBackground()` 暂停采集：

1. 若 capture 尚未停止，调用一次 `capture.topUpSilenceWindow()`；已经 playback-only/stopped 时不重复 topUp；
2. `capture.stop()`，取消采集重试、移除 tap、停止上行。

随后由 reconcile 按目标继续：playback-only 切换为无输入配置并保留 jitter buffer/未播放音频；stopped 走完整 teardown。playback-only 必须真机确认系统麦克风指示消失且没有输入采集。

`onBackground()`、后台 mute 事件和 reconcile 不得各自重复 topUp；统一 helper 只负责 topUp + capture.stop，并保证每次 full → muted background 转换最多一次。

**R3 真机结论**：共享 engine 在 `playAndRecord`/voice-processing 使用后无法在 `.playback` 下重启（`2003329396`），因此最终采用双 engine。playback engine 只做输出，不启用 voice processing；capture engine 可独立停止/重建。真机 `AUDIO_TWO` 验证了 capture pause/resume、playback-only category 切换、回 full 后 VP 恢复。不得通过 `playback.start()` 丢弃 jitter 来伪装切换，也不得把 mic mute 退化成 `playback.stop()`。

若 playback engine 的 category 切换失败/被系统停止，使用 `resumePreservingBuffer()` 保留 jitter 并重启；失败才隐私优先 teardown。

### 5.5 启动、恢复与无条件 teardown

将“全新启动”和“保留内容的恢复”分开：

- 初次进入会话可使用 fresh `playback.start()`，沿用清理旧代缓冲的语义。
- full ↔ playback-only、route change、engine 自愈等场景使用保留当前 epoch 缓冲的 resume/reconfigure；不得重复 fresh start。
- **stopped → playback-only/full**（后台收到新 PCM、回前台恢复、watchdog 后的新 utterance）也必须保留已经推入 jitter buffer 的帧；只有会话首次启动或明确 epoch flush/teardown 丢弃旧内容时才允许 fresh start。
- 播放活动回调必须在 `running=false` 时收到新帧也触发 reconcile；否则 stopped 状态下的新 TTS 可能永远不会启动 playback-only。
- 所有启动和配置切换都必须可回滚。

统一无条件清理：

```swift
private func teardownMedia() {
    invalidatePendingMediaCallbacksAndIdleTimers()
    playback.setTTSInputExpected(false)
    capture.stop()                 // 各组件必须幂等
    playback.stop()
    audioSession.stopCaptureEngine()
    audioSession.stopPlaybackEngine()
    audioSession.deactivate()
    pipelineState = .stopped
}
```

具体实现可调整调用顺序，但要求：

- 不再以 `pipelineState == .stopped` 或 `mediaStarted == false` 为理由跳过资源清理；
- `startFullMedia()` / `startPlaybackOnlyMedia()` / reconfigure 任一步失败，都在 catch 中执行 teardown 或文档明确的安全回滚后返回错误，不得留下部分资源；
- stop、session end、auth failure、权限丢失和启动失败共用同一完整 teardown，避免不同路径泄漏不同资源；
- teardown 使迟到的 capture retry、播放 completion 和 idle timer 失效，不得再次触发启动；
- **`teardownMedia()` 是破坏性清理**：`playback.stop()` 会丢弃 jitter buffer 和已调度播放。需要保留 TTS 的路径（full ↔ playback-only、playback-only → foreground、stopped 新 PCM 恢复）不得把“先 teardown 再恢复”当作无损操作；必须使用 non-destructive reconfigure/resume。只有失败或明确结束会话时才接受播放内容丢失，并需在错误路径中说明。

### 5.6 TTS 空闲与有界等待

后台 playback-only 不能只看 `playerNode.isPlaying`，也不能永久依赖 `.speaking`：

1. 有 buffered/scheduled audio 时保持播放。
2. 没有本地音频但 `connected + sessionReady + speaking` 时，允许等待后续帧。
3. 等待期间启动有界 **progress watchdog**，建议初始 **10 秒**；收到真实 PCM、buffer drain/schedule、scheduled completion、离开 playback-only、回前台或 teardown 时重置/取消。10 秒是防止 stuck-speaking/缓冲卡死的兜底，不是正常 underrun 目标值，可结合真机与服务端指标调整。
4. `hasBufferedOrScheduledAudio == true` **不是无限保活的理由**：如果 buffer 长期不足 `targetFrames` 无法调度、scheduled completion 丢失或引擎停止，必须由 progress watchdog 到期 teardown；不能让一个卡住的布尔值绕过 watchdog。
5. watchdog 到期后设置 `backgroundTTSWaitExpired = true` 并 teardown；同一个陈旧 `.speaking` 状态不得反复触发重启。只有新 PCM、明确的新 utterance 或回前台才重新获得等待资格；新 PCM/新 utterance 的 activity 回调必须先清除 `backgroundTTSWaitExpired` 再执行 reconcile，否则新 buffer 会被 `hasTTSActivity == false` 抑制。
6. session 离开 `.speaking` 时调用 `setTTSInputExpected(false)`，让不足 priming 阈值的尾帧也能调度；待本地音频排空后停止 playback/engine/session。对“最后一帧”和状态事件的不同到达顺序增加短去抖，并覆盖两种顺序的测试，避免过早切断尾音或因残留 buffer 永不释放。
7. 停止后若进程仍在运行，新的 PCM/新 utterance 可触发一次事件驱动的 reconcile：按 `desiredBackgroundPipeline()` 选择 full（mic 未静音）或 playback-only（mic 静音且有 TTS 活动）；必须保留已到达的 PCM，失败不循环。进程已挂起时不保证收到或播放新 TTS。
8. 任何 `player.stop()`、engine stop、route/media reset 或完整 teardown 都必须使未完成的 scheduled 计数/completion generation 失效；引擎停止后丢失的 completion 不能永久占据 `hasBufferedOrScheduledAudio`。
9. `setTTSInputExpected(true)` 进入新 utterance 时，如果上一段已经进入 tail/drain，必须重置该段的 priming/generation，让新 utterance 重新按 `targetFrames` 缓冲；不能让上一段的 `primed` 状态把新段直接放行。

不使用后台轮询做长期保活；上述 timer 只用于有界降级和状态去抖。

### 5.7 `onBackground()`

目标结构：

```swift
func onBackground() {
    guard isStarted else { return }
    stopCameraForBackgroundPreservingResumeIntent()
    reconcileMediaForBackgroundEntry()
}
```

`reconcileMediaForBackgroundEntry()`：

- 若 mic muted 且当前为 full，先按 §5.4 调用统一的 `suspendCaptureForMutedBackground()`；topUp 只在这里执行一次。
- 目标 `.full`：
  - 当前 full、engine running、playback running 且 capture 为 `.running` 或 `.recovering`：保持，不调用 stop/start；
  - capture recovering 不得被误判为失败并取消重试；
  - 若 capture 已 stopped（非 recovering），不得继续声称 full：有 TTS 活动时切 playback-only 并保留 buffer，否则 stopped/teardown；回前台恢复 full；
  - 当前 full 但 engine/playback 不健康：有 TTS 活动时先尝试 non-destructive resume 并保留 buffer，失败才 teardown；无 TTS 活动直接 teardown。本次 background entry 不无限尝试重建，回前台再恢复；
  - 当前为 playbackOnly/stopped 时，**background entry 不凭空启动新的 full**：playbackOnly 有实际 TTS 活动则保留至回前台；stopped 若已有 buffered/scheduled TTS，不主动 teardown，交给 activity-driven reconcile 或回前台；其他情况保持 stopped。回前台恢复 full。显式后台 unmute 事件按 §5.10 可尝试提升为 full，失败不循环。
- 目标 `.playbackOnly`：
  - 当前 full：按 §5.4 suspendCaptureForMutedBackground 后切到无输入配置，playback 不 stop/flush；若 playback 未运行但仍有 TTS 活动，先尝试 non-destructive resume，失败则 teardown；
  - 当前 playbackOnly 且健康：保持；
  - 当前 playbackOnly 但 engine/playback 不健康：先尝试 non-destructive resume 并保留 buffer；失败才完整 teardown；
  - 当前 stopped 且有 TTS 活动：启动 playback-only 并保留已到达的 PCM；失败完整 teardown。
- 目标 `.stopped`：完整 teardown；App 可挂起。
- 相机始终停止并保留恢复意图，且与音频 pipeline 决策无共享状态。

### 5.8 `onForeground()`

回前台目标为 full（权限和会话资格仍满足时），但必须识别当前状态：

- `.full` 且 engine running、playback running、capture `.running/.recovering`：不重复启动；recovering 不被无意义地取消重来。
- `.full` 但 capture 明确 stopped、engine 未运行或 playback 未运行：先尝试非破坏性 resume/reconfigure（保留 buffer）；无法恢复时完整 teardown 后重建 full，播放内容丢失作为失败降级并上报。
- `.playbackOnly` 且 engine 正常：确保 playback 生命周期为 running（若未运行但有缓冲，用保留 buffer 的 resume，不得 reset），保留播放进度，切换到 full 并启动 capture；不得因为 capture 是“有意停止”而误判成媒体损坏。若切 full 失败，先尝试回滚到 playback-only 以保留 TTS，并上报音频错误；回滚也失败才 teardown。
- `.playbackOnly` 但 engine 未运行：先尝试启动 engine/playback 并保留 buffer；失败才完整 teardown 后重建 full，播放内容丢失作为失败降级并上报。
- `.stopped`：事务式启动 full，并保留已经到达 jitter buffer 但尚未播放的 TTS（不得用会 reset buffer 的 fresh start 清掉）；失败先完整 teardown，再展示音频错误。
- `.disconnected` 时 `sdk.connect()`；connecting/reconnecting 不重复连接。
- 自动恢复成功后清除本次对应的音频错误状态，再按意图恢复相机。

`sessionEnded`、认证失败、权限缺失仍直接 guard，不启动媒体或自动重连。

生命周期 sanity check 与 PCM liveness 分开：首期可用 capture 三态 + `engine.isRunning` 做恢复判断；full 状态还应校验 `playback.isRunning`（`AudioPlaying` 需暴露该生命周期布尔值；它不证明 audible，只证明播放器生命周期），缺失时按 full 损坏处理并保留 PCM 重建。若要求检测“capture running 但 tap 无数据”，另加 `lastBufferTime`/tap 序号验收，不宣称布尔状态已经解决该问题。

### 5.9 中断、路由变化与媒体服务重置

不要继续使用“前台一律 full restart、后台一律不处理”的单一逻辑。音频事件应先计算当前目标 pipeline：

- 前台：恢复 full。
- 后台未静音：允许恢复 full。
- 后台静音且有 TTS 活动：只允许恢复 playback-only，不启动 capture。
- 后台静音且无 TTS 活动：不重启，等待新音频事件或回前台。
- `sessionEnded` / auth / 权限丢失 / `stop()`：不重启。

要求：

- route change 的重建尽量保留播放缓冲；中断开始已经完整停止的播放不承诺续播旧内容。
- 中断结束但无 `.shouldResume`：按当前实现不立即重启；回前台自愈。
- 后台 `setActive(true)` / `startEngine()` 失败：完整 teardown、记录错误，不做 coordinator 无限重试；回前台再试。
- `AudioCaptureController` 现有的最多 3 次、200/400/800 ms 内部重试是独立的有界机制，不与“禁止后台无限重启”冲突；测试必须区分两者。
- **只有前台才调用相机恢复**。更稳妥的实现是把 `resumeCameraIfPossible()` 的 foreground guard 放到清除 `cameraResumeIntent` 之前。

### 5.10 静音、speaker、权限与会话事件

| 事件 | 行为 |
|---|---|
| 前台 toggle mic | 沿用 `capture.setMuted` 和上行静音窗口；不停止/flush playback |
| 后台切到 mic muted | topUp → 停 capture → 按 TTS 活动切 playback-only 或 stopped；不直接停 playback |
| 后台切到 mic unmuted | 进程仍在运行且资格满足时可恢复 full；失败不循环 |
| 后台 toggle speaker | 只改变输出策略；mic 未静音时采集继续，mic muted 且无 audible playback 理由时 teardown |
| 会话结束 / `.serverClosed` | 完整 teardown；不后台重启 |
| 认证失败 | 完整 teardown；`awaitingAuthFix=true`；不后台重启 |
| 麦克风权限丢失 | 沿用 fatal 权限语义，停相机并完整 teardown；不自动复活 |
| `stop()` / runtime 销毁 | 完整 teardown、断连、取消回调/timer；不复活 |
| 权限恢复 | 复用同一上下文策略；不得在后台静音且无 TTS 时启动 full |

`toggleMic()`、`toggleSpeaker()`、playback activity、SDK session state 和音频回调都应进入同一 reconcile 入口，避免只在 `onBackground()` 判断一次导致状态陈旧。前台已有行为不因统一 reconcile 而额外重启音频。

### 5.11 网络、静音 flush 与后台任务

- 后台 full 或实际 playback-only 执行期间，SDK WebSocket、心跳和有界重连通常可继续，但 audio background mode 不是网络可用性或进程永久存活保证。
- TTS 空闲并 teardown 后 App 可挂起；回前台恢复 full，并在 `.disconnected` 时重连。
- 本地 `topUpSilenceWindow()` 调用和媒体停止是同步的，但 `sdk.pushAudioPCM` 的实际编码/发送是异步入队，**不能宣称静音窗口已经同步 flush 完成**。
- 验收必须确认服务端收到完整静音窗口并正确结束当前 ASR turn。若不能保证，再增加明确 drain/ack 与短时、有界 background task；不得演变成无限保活。
- 不使用长期 `beginBackgroundTask`，不新增 SDK/服务端协议来假装保证后台网络。

### 5.12 相机、屏幕与音频画廊

- 相机：退后台停止、保留 `cameraResumeIntent`；只有前台恢复。后台 route/interruption/media reset 不得改变相机状态或消费意图。
- 屏幕采集：不在本设计范围。
- 音频画廊：`AudioGalleryView` 现有的后台暂停逻辑保持不变。

### 5.13 状态矩阵

| 上下文 | mic | 目标行为 |
|---|---|---|
| `.active`，会话合格 | 任意 | 确保 full；断连则连接；恢复相机 |
| `.inactive` | 任意 | 不因 scenePhase 本身停音频；音频事件按当前上下文处理 |
| `.background` | 未静音 | 保持 full（采集+播放）；停止相机 |
| `.background` | 静音 | topUp 后完整 teardown（停 playback/capture/engine、`deactivate()`）；麦克风释放，后台停媒体；可挂起 |
| `.background` 内静音 → 未静音 | - | 事件驱动恢复 full |
| `.background` 内未静音 → 静音 | - | 完整 teardown |
| 后台 stopped | 未静音 | 新 PCM/utterance 事件驱动启动 full；失败不循环 |
| `sessionEnded` / auth / 权限丢失 | 任意 | 完整 teardown；不重启 |
| 中断 `.began` | 任意 | 停止相机（保留意图）+ 完整 teardown |
| 中断 `.ended(.shouldResume)` | 未静音 | 前台或后台可恢复 full；后台不恢复相机 |
| 中断 `.ended(.shouldResume)` | 静音 | 保持 stopped（后台不播放） |
| route change / media reset | 任意 | 按当前目标 pipeline 恢复；无后台业务则不启动 |
| 进程被杀/强退 | 任意 | 不自动恢复 |

---

## 6. 错误处理与边界

1. **任一启动/切换失败**：必须完整清理部分资源，不能因状态标记为 stopped 而跳过清理。若失败发生在“mic mute → playback-only”切换，隐私优先 teardown 会中断 TTS；这是失败降级，必须在日志/UI 中可见，并按 §5.4 的设计阻断处理，不能冒充正常 mute 语义。若失败发生在 playback-only → full，先尝试回滚到 playback-only 保留 TTS；回滚失败才 teardown。
2. **capture recovering**：不是失败；后台 full 可保持，内部重试仍受原有限次约束。重试耗尽后的 `onFailure` 走完整 teardown。
3. **后台 playback-only 启动失败**：记录/展示对应音频错误，不循环；进程若仍运行，仅明确的新音频事件可再尝试，回前台统一自愈。
4. **恢复成功后的 UI**：清除本次对应的音频错误 banner，不影响认证、服务端或其他错误。
5. **中断结束但无 `.shouldResume`**：不立即重启；回前台处理。
6. **路由变化连续触发**：保留现有 300 ms 去抖与 1.5 s/0.5 s 抑制，避免 stop/start 循环。
7. **媒体服务重置**：按当前目标 pipeline 重建；失败完整 teardown。
8. **WebSocket 重连耗尽**：沿用现有 stop + retry 语义；回前台可重建并 connect（非 auth/sessionEnded）。
9. **后台相机事件**：不得清除 `cameraResumeIntent`；只有前台恢复路径可以消费。
10. **静音窗口 flush**：本地入队不等于服务端收到；以服务端日志和 ASR turn 结果验收。
11. **App Store 审核**：审核说明覆盖真实采集/TTS；无音频活动时不得靠空音频或永久 timer 保活。
12. **强制退出/系统终止**：无自动恢复。

---

## 7. 测试与验收

### 7.1 单元测试（Fake SDK/Capture/Playback/AudioSession）

必须新增/调整：

| 场景 | 断言 |
|---|---|
| 后台未静音，capture running | 不 stop capture/playback，不 stopEngine/deactivate |
| 后台未静音，capture recovering | 不取消 retry、不 teardown；恢复后仍只启动一次 |
| 后台未静音，媒体明确损坏 | 完整 teardown；无无限重启 |
| 后台未静音，capture 已 stopped | 不保留 full；有 TTS 切 playback-only，否则 stopped；回前台 full |
| 后台未静音，当前 playbackOnly/stopped | 不凭空启动 full；回前台再恢复 |
| 后台静音 + TTS 活动 | topUp；capture.stop；playback 不 stop/flush；切 playback-only；缓冲保留 |
| 后台静音 + 无 TTS 活动 | topUp；完整 teardown |
| stopped + 新 TTS 到达 | 事件驱动 reconcile：mic 未静音启动 full，mic 静音且有 TTS 活动启动 playback-only；已入 jitter buffer 的帧保留，不被 start() reset |
| 后台静音 + speaker muted | topUp；完整 teardown，无后台 audio 占用 |
| 后台 playback-only + TTS 排空且离开 speaking | 停 engine/session；迟到 completion 不复活 |
| 短 utterance/尾帧少于 targetFrames | `setTTSInputExpected(false)` 后仍能调度并排空；不因 buffer 非空永久保持后台 |
| speaking 但无本地音频 | 有界等待；新 PCM 重置等待；watchdog 到期 teardown 且不循环 |
| buffer 不足 targetFrames / scheduled completion 丢失 | progress watchdog 到期 teardown；不因 hasBuffered 为 true 永久保活 |
| engine/player stop 后迟到 completion | generation 失效；不恢复旧 scheduled 计数、不触发重启 |
| full ↔ playback-only | 不调用会 reset 缓冲的 fresh start；当前 epoch 音频保留 |
| playback-only → 前台 | 确保 playback running、切回 full、启动 capture；TTS 不清空、不重复播放；full 切换失败回滚 playback-only |
| full 但 playback 生命周期未运行 | 先尝试非破坏性 resume；失败才 teardown/重建（TTS 丢失作为失败降级） |
| 后台静音 + 中断/route | 仅按 TTS 活动恢复 playback-only，不启动 capture |
| 中断无 shouldResume | 不立即重启；前台可恢复 |
| 启动每个失败点 | 无论状态标记如何，capture/playback/engine/session 都完整清理 |
| capture.onFailure 重试耗尽 | 完整 teardown；旧 retry/completion 不复活；后续仅新上下文事件按策略恢复 |
| 相机 + 后台音频重启 | 相机停止、恢复意图保留；前台恢复且不增加 turn bump |
| mic mute / speaker mute | mic mute 不影响 playback；speaker mute 不影响未静音采集 |
| 会话结束/auth/权限丢失 | 完整 teardown，回前台不自动复活 |
| 自动恢复成功 | 对应音频错误被清除，其他 banner 保留 |
| stop/flush 后迟到 playback completion | generation 失效，不改变新状态、不触发重启 |

原 `testBackgroundStopsMediaAndForegroundRestartsAndReconnects` 应拆成 full、playback-only 和 stopped 三组；另为 `AudioPlaybackController` 增加 buffered/scheduled activity、completion 和 stale generation 测试。

### 7.2 真机验收

1. **后台未静音**：Home/锁屏 5 分钟以上，服务端帧序号持续增长且可解码；用受控音源验证上行包含真实声音，不要求环境安静时也必须“非零 PCM”。AI TTS 可播放。
2. **后台静音且 AI 正在说话**：mute 后 Home/锁屏，TTS 不因 mute 被截断；capture 停止，在指示器实际可见的系统界面确认麦克风不再使用。
3. **静音 flush**：服务端收到完整静音窗口，当前 ASR turn 正确结束；覆盖退后台前网络正常/弱网两种情况。
4. **playback-only 排空**：AI 说完且 buffer 排空后 engine/session 释放；覆盖少于 `targetFrames` 的短 utterance/尾帧，既不得卡 priming，也不得因残留 buffer 永久保活。之后的新 TTS 在 App 已挂起时不作为必达验收。
5. **双静音**：mic 与 speaker 都 muted 时退后台，无采集/播放资源保留；回前台按当前 mute 状态恢复 full。
6. **状态切换连续性**：full → playback-only → 前台 full，TTS 不重复、不清空、无明显长中断，麦克风权限/采集状态正确。
7. **来电/Siri/路由**：中断结束后按 mic mute 与 TTS 活动恢复 full 或 playback-only；蓝牙耳机/有线耳机插拔无长时间无声。
8. **相机组合路径**：相机开启 → 后台 → 来电或 route change → 前台，相机恢复且恢复意图未被后台消费。
9. **无网/弱网**：重连行为符合 SDK；重连耗尽后回前台可重试；旧 speaking 不造成无限后台等待。
10. **会话结束/认证失败/权限丢失**：后台媒体停止，回前台不自动复活。
11. **进程强退/系统终止**：无自动恢复。
12. **能耗**：按 PRD §3.1“持续语音对话电量消耗 <5%/小时”制定可复现测量；记录设备、iOS、网络、路由和初始电量。15–30 分钟可作初筛，小时级结论使用至少 1 小时数据或明确的外推方法。

### 7.3 文档同步

实现后需同步更新：

- `docs/ios_app_audio.md`：后台模式、pipeline 状态、播放活动和静音语义；
- `docs/ios_app_shell.md`：`.background` / `.active` 行为及 `sessionEnded` 描述；
- `docs/ios_app_conversation.md`：前后台表与 mute 行为；
- `docs/ios_app_settings.md`：`UIBackgroundModes` 配置；
- `docs/ios_app_arch.md` / `docs/ios_sdk_design.md`：后台音频决策及 SDK 不保证后台唤醒的边界。

---

## 8. 文件影响与实施顺序

### 8.1 预计改动

| 文件 | 改动 |
|---|---|
| `Resources/Info.plist` | 增加 `UIBackgroundModes = [audio]` |
| `Core/Conversation/ConversationCoordinator.swift` | pipeline 状态、统一 reconcile、前后台/中断/ mute 策略、无条件 teardown、相机恢复修复 |
| `Core/Audio/AudioProtocols.swift` | capture 三态、`playback.isRunning`、播放 activity、end-of-input、保留缓冲的 resume/reconfigure 接口 |
| `Core/Audio/AudioCaptureController.swift` | 暴露 running/recovering/stopped；保持现有有界重试 |
| `Core/Audio/AudioPlaybackController.swift` | buffered/scheduled activity、尾帧 priming 释放、completion generation、保留缓冲恢复 |
| `Core/Audio/AudioSessionController.swift` | 单 engine、`activate(captureSource:)`、VP engine-stopped 前置、category fallback/rollback、deactivate |
| `Core/Conversation/ConversationRuntimeFactory.swift` | capture 与 playback 绑定同一个 engine/node |
| `App/AppContainer.swift` | `timelineMediaEngine` 使用会话 engine |
| `ConversationViewModel` | 定向清除音频错误，避免误清其他 banner |
| `LivaChatAppTests/ConversationCoordinatorTests.swift` | 前后台策略矩阵、静音 teardown、失败恢复、相机组合测试 |
| `LivaChatAppTests/AudioPlaybackControllerTests.swift` | 播放活动、排空、completion/stale generation、engine-running guard、session config 测试 |
| iOS 文档 | 见 §7.3 |

不需要改：`LivaChatSDK` 协议、服务端、编解码。App 侧需要改单 engine、播放/采集状态、音频配置和 Coordinator 策略，不能声称“只改 Info.plist 和 onBackground 即可”。

### 8.2 实施顺序

1. ✅ 技术 spike：Info.plist + 后台 full；R3 曾改为双 engine，R4 因 AEC/音量问题回归单 engine。
2. ✅ capture 三态、播放 activity、无条件 teardown 与底层单测。
3. ✅ 启动失败回滚、`resumeCameraIfPossible()` intent 顺序和回归测试。
4. ✅ `MediaPipelineState` + 统一 `reconcileMedia()`；onBackground/onForeground 接入。
5. ✅ mute/speaker、playback activity、session state、中断/route/engine config/media reset 接入。
6. ✅ 策略矩阵、播放 completion、失败注入和相机组合测试。
7. ◐ 真机 harness 已验证单 engine 启动/停止、AEC/响度与后台策略；后台锁屏、静音释放麦克风、ASR turn、能耗仍需按 §7.2 人工/服务端验收。
8. ◐ 文档已同步；App Store 审核说明待产品定稿。

---

## 9. 风险与待验证问题

1. **单 engine 的取舍**：后台 mic 静音时停媒体以释放麦克风；后台不再播放 TTS，App 可能被挂起。此为用户确认的产品决策。
2. **配置切换连续性**：`.voice`/`.original` 切换需要停 engine；`resumePreservingBuffer()` 从 jitter 恢复，已 schedule 的 1–2 帧可能丢失（约 20–40ms）。
3. **后台激活限制**：真机 harness 已验证前台 full 与后台 full/teardown；后台 `setActive(true)`/engine start 失败按完整 teardown 处理，不无限重试。
4. **播放活动判定**：状态事件与最后音频帧可能乱序；需 completion、去抖和 generation 共同防止切尾音或迟到复活。
5. **真实 PCM liveness**：capture running 与 engine running 不等于 tap 持续产出；若需要检测此类假死，另加 lastBufferTime/序号，不能用布尔状态冒充。
6. **静音窗口 flush**：异步入队不代表服务端收到，必须验收完整静音和 ASR turn 结束。
7. **网络保活**：音频后台模式不等于网络保证；TTS 空闲挂起后，新音频不保证唤醒。
8. **耗电/发热**：后台采集（mic 未静音 full）的耗电与发热需按 PRD 量化；teardown 后不得依赖后台等待/轮询保活。
9. **用户感知**：iOS 没有 Android 式常驻通知；系统指示器在熄屏/锁屏并非始终可见，文案和验收不得依赖其全程展示。
10. **App Store 审核**：真实 TTS 是明确音频用途；风险主要来自无音频活动时仍长期保持后台执行。审核通过不是技术验收的替代品。
11. **进程终止**：强退/系统回收后不自动恢复；不为此引入 VoIP/CallKit。

---

## 10. 设计决策摘要（供再次 review）

> 注：本表保留 R2 决策记录；R4 已用单 engine + `full`/`teardown` 取代 `.playbackOnly` 相关决策（D2/D4/D5/D6），当前实现以 §9 与 `docs/ios_app_audio.md` §3.3 为准。

| # | 决策 | R2 建议 |
|---|---|---|
| D1 | iOS 后台机制 | `UIBackgroundModes: audio` + 与实际业务匹配的音频配置，无 FGS |
| D2 | 后台 pipeline | `.full` / `.playbackOnly` / `.stopped`，采集与播放独立门控 |
| D3 | 后台未静音 | 保持 full；capture recovering 也保持，不误停有界重试 |
| D4 | 麦克风静音 | 只关闭输入；topUp、释放采集/麦克风，不直接 stop/flush TTS |
| D5 | 静音后台 TTS | 有 TTS 活动时 playback-only；排空后释放，等待必须有界 |
| D6 | 回前台 | 状态感知恢复 full；playback-only 切回时保留播放内容 |
| D7 | 中断/路由/媒体重置 | 按目标 pipeline 恢复；后台不消费相机恢复意图 |
| D8 | 启动失败 | 事务式回滚 + 无条件幂等 teardown；状态标记不作为清理前提 |
| D9 | 后台保活 | 不用长期 background task、不播静音、不用 voip；无活动时允许挂起 |
| D10 | 相机/屏幕 | 相机后台停止且仅前台恢复；屏幕采集不在范围 |
| D11 | 网络与进程死亡 | 不保证挂起后新 TTS 唤醒；进程死亡不自动恢复 |
| D12 | 上线前置 | 麦克风释放/TTS 连续性 spike、策略测试、真机与审核说明全部通过 |
