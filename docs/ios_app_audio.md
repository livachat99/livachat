# LivaChat iOS App 音频模块设计

**状态**：已实施（M1.1/M1.2 + iOS 后台音频 R2；模拟器与 iPhone 11 真机验证）
**日期**：2026-10-24
**上游文档**：
- `docs/ios_background_support.md`（后台音频 R2）
- `docs/ios_app_m1.md`（M1 范围、跨模块契约、验收）
- `docs/ios_sdk_design.md` §7.2–§7.4（采集/播放/打断）
- `docs/ios_mic_meter.md`（4 段电平、`MicLevelModel`、unmute 上行边界）
- `docs/ios_dev_rules.md`（iOS 15、SwiftUI、UIKit 例外）

本文定义音频模块的类、线程、数据流和测试缝。当前实现为单 `AVAudioEngine`：前台 full（采集+播放）；后台在 mic 未静音时保持 full，mic 静音时 teardown 释放麦克风与停止媒体。设备选择器、录屏、AEC 调参 UI 不在本文范围。

---

## 1. 组成

```
AudioSessionController          // 唯一的 AVAudioSession owner + 单 engine
 ├─ AVAudioEngine (engine)          // input tap + voice processing + TTS 播放
 ├─ AVAudioPlayerNode (playbackNode) // attach 到同一个 engine
 └─ notification observers           // interruption / route / engine config / media reset

AudioCaptureController          // input tap → AVAudioConverter → 16k mono Int16 → 20ms 帧
AudioPlaybackController         // LivaChatAudioSink + JitterBuffer + player pump
JitterBuffer                    // 线程安全；sequence 排序 + playbackEpoch 过滤
InterruptCoordinator            // SPEAKING + VAD speech → sendInterrupt + 触觉
HapticController                // UIImpactFeedbackGenerator 适配层（UIKit 例外）
```

所有权：

- `AudioSessionController` 由 runtime builder 创建并强持有；capture/playback controller 共享它提供的 engine / playback node。
- `AudioPlaybackController` 必须先于 `LivaChatSDK` 构造，SDK 只持弱引用；
- capture/playback/session 都由 `ConversationRuntime` 强持有；
- `InterruptCoordinator` 不持有 SDK 具体类型，只持有 `sendInterrupt` 闭包和 `HapticProviding`。

---

## 2. 线程模型

| 工作 | 线程/队列 | 规则 |
|---|---|---|
| session/capture/playback 控制 | MainActor | start/stop/setMuted/flush 都由 MainActor 调用 |
| input tap 回调 | AVFoundation 实时音频线程 | 只做转换、分帧、电平计算、`uplink` 调用；不做 UI/IO/锁长持有 |
| `audioSink` 回调 | SDK audio recv 串行队列 | 只 `jitterBuffer.push`，立即返回 |
| playback pump | 专用串行 `DispatchQueue`（`qos: .userInteractive`） | 拉帧、构造 `AVAudioPCMBuffer`、`playerNode.scheduleBuffer` |
| 事件处理 | MainActor（`ConversationEventAdapter`） | 调 `playback.flush`、UI 状态更新 |

- `JitterBuffer` 内部 `NSLock`；写者（SDK audio recv）与读者（playback queue、Main flush）互斥。
- `AudioCaptureController` 的 mute/电平状态用 `NSLock` 保护；tap 回调内不分配大对象（每帧复用零帧常量）。
- `AVAudioPlayerNode.stop()/play()` 与 `scheduleBuffer` 都在 playback queue 上串行执行；Main 只投递命令。
- 音频模块不得直接改 `ObservableObject`；电平/失败通过闭包回调到 MainActor。

---

## 3. AudioSessionController

### 3.1 配置

`activate(captureSource:)` 是唯一配置入口：

| source | category | mode | options | voice processing |
|---|---|---|---|---|
| `.voice` | `.playAndRecord` | `.voiceChat` | `[.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP]` | 开启 |
| `.original` | `.playAndRecord` | `.measurement` | 同上 | 关闭 |

- `activate(captureSource:)` 做 descriptor no-op 检测、in-place 切换、失败时 deactivate/reactivate fallback、事务回滚，并在每次切换前后抑制 1.5s route change。
- `AVAudioInputNode.setVoiceProcessingEnabled` 只能在 engine 停止时切换；`activate` 在需要改变 VP 且 engine 仍在运行时抛出 `engineMustBeStoppedForVoiceProcessing`。`.voice` 开启 VP 失败保持非致命；`.original` 关闭 VP 失败按隐私失败抛出。
- `startEngine()`/`stopEngine()` 控制唯一的 engine；`deactivate()` 调 `setActive(false, options: .notifyOthersOnDeactivation)` 并清空 `appliedCaptureSource`。
- `Info.plist` 已启用 `UIBackgroundModes = [audio]`。后台策略见 §3.3。

### 3.2 通知

| 通知 | 处理 |
|---|---|
| `interruptionNotification` | `.began` → 回调 `onInterruptionBegan`；`.ended` 且 `shouldResume` → 回调 `onInterruptionEnded` |
| `routeChangeNotification` | engine 处于活动状态时，300ms 去抖后回调 `onRouteChanged` |
| `AVAudioEngine.configurationChangeNotification` | 观察唯一的 engine；与 route change 共用 300ms 去抖，映射为 `onRouteChanged` |
| `mediaServicesWereResetNotification` | 停止 engine、清空 applied/session/VP 状态，再回调 `onMediaServicesReset` |

- `activate`/`startEngine` 后 1.5s、`stopEngine` 后 0.5s 内抑制 route/config 回调，避免自己触发的 configuration change 形成 stop/start 循环。
- 回调由 `ConversationCoordinator.reconcileMedia()` 消费：前台恢复 full；后台在 mic 未静音时保持 full，静音时 teardown 释放麦克风；不重建 SDK。

### 3.3 单引擎与后台策略

`AudioSessionController` 暴露：

```swift
var engine: AVAudioEngine { get }
var playbackNode: AVAudioPlayerNode { get }
var appliedCaptureSource: TranscribeAudioSource? { get }
var isEngineRunning: Bool { get }
func activate(captureSource:) throws
func startEngine() throws
func stopEngine()
func deactivate()
```

- **采集与播放必须共用同一个 engine**：VPIO 的 AEC 参考信号只包含同一 engine 的输出。播放若走另一个 engine，AEC 无法消除 TTS 回声，且 `.voiceChat` + VPIO 会把另一 engine 的输出当作 "other audio" 做 ducking（实测音量明显变小）。
- `playbackNode` 在 init 时 attach，并以 **Float32 mono 16 kHz** 连接到 `engine.mainMixerNode`；`AVAudioPlayerNode` 的混合器总线不接受 Int16（实测错误 `-10868`）。
- 新会话启动顺序：`activate(captureSource:)` → `capture.start()`（安装 tap 并启动 engine）→ `startEngine()`（幂等确认）→ `playback.start()`。**engine 必须先运行再调用 `play()`**；`AudioPlaybackController` 还会在 engine 未运行时跳过 `play()`，避免 AVFAudio 的 Obj-C 异常。
- 若 `capture.start()` 因 engine 启动失败进入 `.recovering`，随后的 `startEngine()` 再次失败只记录日志并保留 full，由采集的有界重试负责重启 engine；playback pump 在观察到 engine 恢复后重试 `play()`。该瞬时失败不得升级为 teardown。
- **后台 + mic 未静音**：保持 full（采集 + 播放），依赖 `UIBackgroundModes=audio`。
- **后台 + mic 静音**：teardown（停 playback/capture/engine 并 `deactivate()`），麦克风真正释放，后台不再播放 TTS；回前台重新激活并恢复。VPIO 是全双工 I/O unit，单 engine 无法在保留播放的同时释放麦克风。
- 从 `.stopped` 恢复 full 时使用 `playback.start()` 丢弃后台期间累积的 TTS，避免回前台后补播旧音频；仅在 full 内的 engine/route 恢复与 source 切换使用 `resumePreservingBuffer()`。
- 切换 `.voice`/`.original` 会 toggle VP：必须先 `playback.prepareForAudioReconfiguration()` → 停 capture/engine → `activate` → 重启 capture/engine → `resumePreservingBuffer()`。

---

## 4. AudioCaptureController

### 4.1 格式与分帧

- 硬件输入格式：`engine.inputNode.outputFormat(forBus: 0)`。
- 目标格式：`AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true)`。
- `AVAudioConverter` 在输入格式变化时重建；tap 的 `bufferSize` 取 1024 帧（硬件采样率）。
- 转换后样本进入待发队列，按 **320 samples（20ms）** 切帧；不足 320 的尾样本保留到下一回调。
- 每帧调用 `uplink(samples, muted)`；`uplink` 在 runtime 中绑定 `sdk.pushAudioPCM(_:muted:)`。
- 采样率/声道与目标不一致时由 converter 处理；converter 创建失败或转换错误计入失败。

### 4.2 静音

对齐 Android `SilenceFlushGate`：

- `setMuted(true)` 在下一采集帧的“静音沿”启动 50 帧（1s）全零窗口；
- 窗口内每帧 `uplink(zeros(320), muted: true)`；
- 窗口耗尽后继续读取/转换，但不再 `uplink`；
- `setMuted(false)` 丢弃共享 chunker 中不足 320 的 pre-unmute pending，并 arm one-shot discard token；unmute 后首个 tap callback 整 buffer 丢弃（旧 in-flight tap 另由 generation 过滤），之后恢复真实帧，不补发。token 对未公开的平台 callback 队列深度是 best-effort，丢音边界见 `docs/ios_mic_meter.md` §4.3。
- `stop()` 清空窗口；
- full → muted background 时由 `ConversationCoordinator.reconcileMedia(.background)` 在 teardown 前调用一次 `topUpSilenceWindow()`，随后停 capture；不允许 onBackground、mute 事件和 reconcile 各自重复 topUp。
- 后台 mic 静音时由 Coordinator 执行完整 teardown（停 playback/capture/engine、`deactivate()`），麦克风释放以系统指示器熄灭为准；`AVAudioSession.isInputAvailable` 不是可靠判据。

静音期间仍做旁路电平计算，但上报值强制为 0。

### 4.3 电平

- 按 `PCMChunker` 的 320 samples（20ms）逐帧计算；一个 tap buffer 可包含多个 chunk，保存最后一个 chunk 的结果（不是对整个转换 buffer 计算）。
- RMS → dBFS，映射到 Android 窗口 `[-45, -8] dB`，窗口外 clamp 到 0/1。
- 快攻慢放：`smoothed = norm >= smoothed ? norm : smoothed * 0.85`（不是 `max(norm, smoothed * 0.85)`）；muted/空帧归零并清平滑态。
- 量化：`Int((smoothed * 4).rounded()).clamped(0...4)`，输出 Int 0...4（`MicLevelMeter.levels = 4`）。
- 约 50ms 节流后通过 `(@MainActor (Int) -> Void)?` 的 `onLevel` 回调 Main；mute/unmute/stop/restart/重试重建的 0 基线不受节流延迟。
- `onLevel` 只更新叶子 `MicLevelModel`（`@Published private(set) var level: Int`，clamp + 等值去重），`MicVolumeGlyph` 独立观察，避免 `ConversationView` 整页重绘；电平不参与 SDK 上行。
- controller 用 `levelGeneration` + 单调 `levelReportSequence` 过滤旧代/同代乱序上报；每个新 generation 必须有当前代 0 基线或 MainActor 同步 reset，详见 `docs/ios_mic_meter.md`。

### 4.4 失败与重试

- `start()` 前检查 `AVAudioSession.recordPermission == .granted`；未授权抛 `.permissionDenied`，不安装 tap、不重试。
- 启动失败（格式/converter/engine.start）：初始尝试后最多 3 次重试，退避 200ms/400ms/800ms；重试在后台 Task 执行，不阻塞主线程。
- tap 安装后若 `engine.start()` 失败，先移除 tap、清 `capturing/converter`，再进入重试，避免重复 tap。
- 重试期间 `start()` 返回成功（媒体管线可先启动）；耗尽后通过 `onFailure` 上报，Coordinator 停止媒体并显示错误 banner；只对权限错误立即抛出。
- 转换错误（`AVAudioConverter` 返回 `.error`）计入失败并触发采集管线重建：在控制线程移除 tap/清状态，然后走同一 3 次退避重试；成功转换会清零连续失败计数。连续 3 次转换错误仍未恢复时停止并 `onFailure`，不会在第一个坏 buffer 上直接判死，也不会无限恢复。
- 转换错误的恢复决策不得在 tap 线程同步获取 `controlLock`：`removeTap` 会同步 flush 在途 callback，teardown 持有该锁时可能与 callback 互等。`handleTap` 只记录失败并投递到 controlQueue，恢复函数再次校验 generation 与失败 token；成功转换或更新的失败会使旧投递失效。因此第三次失败后、排队终止决策执行前若出现成功转换，会取消终止并清零 streak（成功证明转换器已恢复），这是明确的异步投递语义。
- `lifecycleState` 暴露 `stopped` / `recovering` / `running`：初始启动和重试期间为 `.recovering`，重试耗尽后 `.stopped`，成功安装 tap/启动 engine 后 `.running`。`start()` 在重试期间正常返回；Coordinator 依据三态决定保持 full 或 teardown，不能把 `recovering` 误判为失败。
- conversion recovery 的 teardown 在同一控制临界区内完成，避免短暂对外报告 `.stopped`。
- 重试 Task 的创建、`retryTask` 保存与 `recovering = true` 在同一 `controlLock` 临界区完成；Task 内清除 `recovering`/`retryTask` 的路径也必须先取得该锁。修复前，立即返回的 `retrySleep` 可能在注册前完成恢复并清状态，创建线程随后又把 `recovering` 置回 true，使 `lifecycleState` 永久停在 `.recovering`；当前实现不允许该交错。`AudioCaptureRetryTests` 用无挂起的 `retrySleep` 做连续 start/stop 回归。
- `stop()` 幂等并取消进行中的重试 Task/递增 generation；移除 tap、清空转换缓冲、窗口清零。capturing/recovering 时 reset 电平并上报当前代 0；no-op stop 不递增 `levelGeneration`（`startGeneration` 仍递增），持锁快照当前 `levelGeneration`/sequence 后上报 0，避免旧 stop 覆盖新 start 的首帧。
- 采集失败重试耗尽后，Coordinator 执行完整 teardown 并显示错误 banner；不会无限自动重试。

### 4.5 测试缝

```swift
protocol AudioCapturing: AnyObject {
    var lifecycleState: CaptureLifecycleState { get }
    var onLevel: (@MainActor (Int) -> Void)? { get set }
    var onFailure: (@MainActor (String) -> Void)? { get set }
    func start() throws
    func stop()
    func setMuted(_ muted: Bool)
    func topUpSilenceWindow()
}
```

`AudioCaptureController` 的格式化/静音逻辑拆成纯 Swift `PCMChunker` + `SilenceFlushGate`，便于 host 侧单测；AVFoundation 接入只做薄封装。

电平/生命周期测试使用 DEBUG-only 接缝：`ingestSamplesForTesting`（直接驱动 `process`）、`installTapConverterForTesting` + `ingestTapBufferForTesting`（完整 `handleTap`：discard token、generation、`AVAudioConverter`、process、uplink），以及可注入的 `uptime`、`levelDelivery`、`beforeUplink`、`beforeStopBaseline`；组合行为与边界见 `docs/ios_mic_meter.md` §6.3。

---

## 5. JitterBuffer

### 5.1 数据

```swift
struct BufferedAudioFrame {
    let samples: [Int16]
    let sampleRate: Int
    let durationMs: Int
    let sequenceNumber: UInt64
    let playbackEpoch: UInt64
}
```

`push` 接受 `AudioPCMFrame` 并去掉无关字段（`turnID` 不参与 M1）。

### 5.2 规则

- `targetFrames = 3`（60ms 预缓冲）；`maxLatencyMs = 60_000`（按**时长**上限，不再使用 15,000 帧数上限）。服务端按 TTS provider 的产出速度突发推送，1s 级别上限会截断正常回复；60s 在 20ms/120ms 帧下都约 1.9MB。
- 帧时长以**解码后的 samples 数**为准（`samples.count / sampleRate`，向上取整），不信任 wire 上的 `durationMs`；空 samples 帧直接丢弃。
- 按 `sequenceNumber` 升序；`started` 后**先**判断并丢弃 `< nextExpected` 的迟到帧，再判断容量；缺失序号时跳到当前最小序号，不阻塞。
- `acceptedEpoch` 初值取 SDK `state.playbackEpoch`（通常为 1）：
  - `frame.playbackEpoch < acceptedEpoch` → 丢弃；
  - `frame.playbackEpoch > acceptedEpoch` → `acceptedEpoch = frame.playbackEpoch`，丢弃队列中 `< acceptedEpoch` 的旧帧，保留新帧；
  - 相等则正常入队。
- `flush(epoch:)`：`acceptedEpoch = max(acceptedEpoch, epoch)`；删除 `< acceptedEpoch` 的帧；**不整队清空**。
- 溢出/迟到计数暴露给测试与日志；播放器静默等待新帧，不阻塞。
- `reset()`：清空队列、`started=false`、`nextExpected=0`；只在 runtime 重建/音频 stop 时调用。

### 5.3 并发

所有方法加锁；`push` 只做字典插入/过滤，快速返回。`pull/isReady/size/flush/reset` 同一把锁。

---

## 6. AudioPlaybackController

### 6.1 生命周期与活动

- `start()`：fresh、destructive；重置 jitter/epoch、失效旧 completion、`playerNode.stop()/play()`、启动 20ms pump。
- `stop()`：无条件清理（即使当前未 running）；取消 timer、失效 completion、清 pending、`playerNode.stop()`、`jitterBuffer.reset()`。
- `prepareForAudioReconfiguration()`：停止 pump/player、失效 scheduled completion 与 pending，但保留 jitter/epoch；用于 engine 必须重启的路径。
- `resumePreservingBuffer()`：不清 jitter、不 reset epoch；只恢复 running/timer/player，供 full 内的前台恢复、engine 重启与 source 切换使用。从 `.stopped` 恢复 full 使用 destructive `start()`（停机期间的 TTS 按设计丢弃）。
- `setMuted(_:)`：`playbackNode.volume = muted ? 0 : 1`；不停止 buffer 或 engine。
- `flush(reason:epoch:)`：失效旧 completion → `jitterBuffer.flush(epoch:)`；running 时重启 player 并重新预缓冲。
- `isRunning` 表示播放 pump 生命周期，不证明 audible；`hasBufferedOrScheduledAudio = jitterBuffer.size > 0 || pendingScheduledBuffers > 0`。
- `onActivityChange` 在 MainActor 回调；新 PCM、schedule、completion、drain、stop/flush 都会通知（约 100ms 合并），供 Coordinator 驱动 stopped→full 与播放活动判断。
- SDK 只弱引用本对象；App 必须强持有。

### 6.2 Sink 回调

```swift
func livaChat(_ sdk: LivaChatSDK, didReceiveAudio frame: AudioPCMFrame) {
    jitterBuffer.push(frame)   // 只做入队，禁止 UI/IO
}
```

- 采样率不是 16k 时记录并丢弃（SDK 固定 16k）。
- 回调线程 = SDK audio recv；不得调用 MainActor 同步方法。

### 6.3 Pump

- 专用串行队列，20ms `DispatchSourceTimer`。
- 未预缓冲：`ttsInputExpected == true` 时要求 `jitterBuffer.size >= 3`；`setTTSInputExpected(false)`（离开 speaking/尾音）后允许 ≥1 帧绕过 priming，避免短 utterance 卡死。
- 预缓冲后每次 tick 最多取 2 帧（约 40ms）schedule，让 player node 内部队列保持小延迟。
- 每帧构造 `AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: frame.samples.count)`（Float32 mono 16 kHz），把 Int16 样本除以 32768 写入 `floatChannelData`，再 `scheduleBuffer(..., completionCallbackType: .dataPlayedBack)`。
- 调度前递增 `pendingScheduledBuffers` 并记录 generation；completion 回到 playback queue 后仅在 generation 匹配时递减。stop/flush/prepare/start 递增 generation，迟到 completion 不得复活旧状态。
- 队列空时不再 schedule；不 stop player，避免重启爆音。`primed=false` 时再次等待 3 帧。
- pump 每次 tick 先检查共享 engine：engine 停止时不 schedule、不调用 `play()`；观察到 engine 恢复时失效停机期间可能丢失的 completion 并重试 `play()`（即使 jitter 已空）。
- 记录 push/pull/underrun/溢出计数；不写日志到音频线程。

### 6.4 断连与 epoch

`.connectionStateChanged` 为非 `.connected` 时，Coordinator 立即：

```swift
if let epoch = sdkState?.playbackEpoch {
    playback.flush(reason: .sessionEnded, epoch: epoch)  // 仅 epoch 过滤，不是“用户结束”
}
```

- 使用 `sdk.state.playbackEpoch`（SDK 已在状态转换中递增）。
- `playbackShouldFlush(reason, epoch)` 同样转发；两个来源都用 `max` 更新 `acceptedEpoch`。
- 不在本地 VAD 打断时 flush；只等服务端 `interruptAck`。

### 6.5 测试缝

```swift
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
```

`JitterBuffer` 与 pump 的调度策略分离；单测通过 `PlaybackNode`/`pumpTimerEnabled=false`/`pumpNowForTesting()` 做确定性 completion、generation、tail 和 resume 覆盖；真机验证 engine 行为。

---

## 7. 触觉反馈（与 Android 对齐）

- 生产会话不使用本地 VAD 打断（对齐 Android `vadInterruptEnabled=false`）。
- 触发时机：会话状态进入 `SPEAKING` 时调用 `haptics.lightTap()`；重复的 `SPEAKING` 状态不重复触发。
- 开关：受 `hapticEnabled` 控制（设置变更实时生效）。
- `InterruptCoordinator` 仅保留给 AudioSpike 调试页，不再接入生产 runtime。

---

## 8. HapticController

- `protocol HapticProviding { func lightTap() }`。
- 生产实现使用 `UIImpactFeedbackGenerator(style: .light)`；在 MainActor 调用 `prepare()`/`impactOccurred()`。
- `hapticEnabled == false` 时由 `ConversationCoordinator` 跳过调用。
- UIKit 仅出现在此适配层，符合 `docs/ios_dev_rules.md` 例外。

---

## 9. Audio Spike Harness

目标：在接业务 UI 前验证采集、播放、回声、打断和后台 pipeline。仅 `#if DEBUG`，由启动参数 `-audioDebugHarness` 打开：

```
┌────────────────────────────────────────────┐
│ Audio Spike                                │
│ Mic level: [====      ]                    │
│ State: capturing / stopped                 │
│ pipeline/apply/category/engine/player/VP   │
│ [Start] [Stop] [Mute]                      │
│ [Play 1s / 10s tone] [40ms blip]           │
│ [Loopback] [Simulate speaking+VAD] [Stats] │
│ [Toggle capture source]                    │
└────────────────────────────────────────────┘
```

- `Play 1s/10s tone`：合成 PCM 推入 playback sink，验证单 engine 输出、路由、AEC 与响度。
- `40ms blip`：2 帧尾音 + `setTTSInputExpected(false)`，验证小于 3 帧的尾音可排空。
- `Toggle capture source`：在 `.voice`/`.original` 间切换，验证 VP 关闭/恢复与采集重启。
- 验收：模拟器 UI/单测；iPhone 11 真机对比修复前后的 tone 响度与 mic level 抬升（AEC）。

---

## 10. 测试矩阵

| 测试 | 类型 | 覆盖 |
|---|---|---|
| `PCMChunkerTests` | host 单测 | 16k 输入分帧为 320；跨回调残留；奇数/空输入 |
| `SilenceFlushGateTests` | host 单测 | mute 沿 50 帧；窗口后丢弃；unmute 恢复；top-up |
| `MicLevelMeterTests` | host 单测 | 320 样本、`[-45,-8]`、attack/release（含 release 不跟随中间值）、muted/空帧/reset |
| `JitterBufferTests` | host 单测 | sequence 顺序/迟到/缺口；epoch max；flush 丢旧留新；并发 push/pull |
| `AudioCaptureRetryTests` | host 单测 | 三态、重试耗尽、stop while recovering、conversion recovery、generation 失效 |
| `AudioPlaybackControllerTests` | host 单测 | pending/completion、generation、activity、tail bypass、resume、无条件 stop |
| `AudioSessionControllerConfigurationTests` | host 单测 | descriptor、VP engine-stopped 前置、category fallback/rollback、activate/deactivate |
| `ConversationCoordinatorTests` | host 单测 | 前台/后台 full 策略、后台静音 teardown、mute topUp、engine 恢复/teardown、相机意图 |
| `ConversationViewModelTests` | host 单测 | 定向清理音频 banner，保留 auth/session banner |
| Audio spike UI | 模拟器 + iPhone 11 | 单 engine 启动/停止、tone、source 切换 |

---

## 11. 风险

| 风险 | 处理 |
|---|---|
| 单 engine 下采集与播放耦合 | engine 重启会短暂中断 TTS；仅在必要时 prepare/resume，保留 jitter（已 schedule 的 1–2 帧可能丢失，约 20–40ms） |
| `AVAudioEngine` 在 category/route 变化后停止 | 观察 engine 的 configuration change，与 route change 共用去抖；Coordinator 用 resume/重启恢复 |
| `AVAudioConverter` 在 tap 回调中的开销 | 每帧复用缓冲；spike 观察 underrun/CPU |
| `playerNode.play()` 在 engine 未运行时崩溃 | `AudioPlaybackController` 在 `play()` 前检查共享 engine 的 `isRunning`；pump 在观察到 engine 恢复时重试 `play()` 并失效停机期间可能丢失的 completion |
| scheduled completion 丢失 | generation 失效 + pending 清零 |
| `isInputAvailable` 不反映实际输入 | 真机以系统麦克风指示器/听感验收，不以该属性作为隐私证明 |
| 后台 mic 静音后 app 可能被挂起 | 预期行为；回前台由现有 reconnect/session 逻辑恢复 |
