# Android 音频播放流程

> **状态**：已实施
> **基线**：2026-10-24，`versionCode 137`
> **范围**：Android 正式 App（`apps/android/LivaChatApp`）与 SDK（`apps/android/LivaChatSDK`）的实时音频下行链路：`ai_audio` 接收 → SDK 解码 → JitterBuffer → AudioTrack 播放；含输出设备、音频路由、扬声器静音与 MyLife 播放互斥。
> **关联**：上行麦克风采集、VAD、AGC 与 C2S `audio` 见 [`android_audio_capture.md`](android_audio_capture.md)。
> **不含**：服务端 TTS/ASR 模型细节、`LivaChatAppMock`；MyLife 语音相册的完整 UI/播放器实现仅说明与实时会话的互斥。

---

## 1. 链路总览

```text
服务端 `ai_audio`（Opus）
  │  SessionManager → ListenerDispatcher.onAiAudio(data, codec, durationMs)
  ▼
LivaChatSDK.aiAudioHandler
  │  audioSeqGen.incrementAndGet() → App 内部 sequenceNumber
  │  Base64 decode
  ▼
SdkDispatchers.audioRecv
  ▼
AudioRecvPipeline.handleAiAudio
  ▼
OpusDecoderImpl.decode
  ▼
AudioPlaybackController.onAiAudioPcm（SDK audio-recv dispatcher）
  ▼
JitterBuffer（TreeMap，按 sequenceNumber 重排）
  ▼
播放协程（单线程 URGENT_AUDIO，10 ms tick）
  ▼
AudioTrack（16 kHz / mono / 16-bit / MODE_STREAM）
```

并行路径：

- **输出设备**：`AudioDeviceManager` 枚举输出设备，`AudioPlaybackController` 通过 AudioTrack `setPreferredDevice` 应用选择；所选设备消失时回落扬声器。
- **路由与焦点**：`AudioRouteManager` 维护 `MODE_IN_COMMUNICATION` 与 `AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE`；`AudioPlaybackController.start()` 固定 `switchRoute(SPEAKER)`。
- **MyLife 互斥**：`ExoAudioPlayer` 使用媒体通道；实时会话 `isPlaying=true` 时暂停画廊，不自动恢复。

---

## 2. 组件与职责

| 层 | 组件 | 职责 |
|---|---|---|
| App | `AudioPlaybackController` | AudioTrack 创建/重建、抖动缓冲、播放循环、输出设备/焦点/SCO、扬声器静音 |
| App | `JitterBuffer` | 下行 PCM 帧按 sequenceNumber 重排、去晚到/溢出 |
| App | `AudioDeviceManager` | 输入/输出设备枚举、白名单、设备变化回调；本文只用输出侧 |
| App | `AudioOutputDevice` | 输出设备模型与标签规格 |
| App | `AudioRouteManager` | `MODE_IN_COMMUNICATION`、音频焦点、SPEAKER/EARPIECE/BLUETOOTH 路由分支 |
| App | `ExoAudioPlayer` | MyLife 语音段播放；与实时会话单向互斥 |
| App | `ConversationCoordinator` | 会话启停时按顺序启动/停止播放；播放不被前后台 collector 单独暂停 |
| App | `ConversationViewModel` | speaker/output 事件、`isSpeakerMuted`/输出设备状态合流 |
| SDK | `AudioRecvPipeline` | `ai_audio` Opus → PCM |
| SDK | `SessionManager` / `ListenerDispatcher` | 下行 `ai_audio` 分发、flush 原因 |
| SDK | `LivaChatSDK` / `LivaChatListener` | sequence 生成、Base64 解码、`onAiAudioPcm` / `onPlaybackShouldFlush` 回调 |
| Protocol | `packages/api-schema/s2c/ai-audio.json` | `ai_audio` 字段与音频块约束 |

---

## 3. 协议：S2C `ai_audio`

`packages/api-schema/s2c/ai-audio.json`：

- 必填：`data`（Base64 Opus）、`codec`、`message_id`、`index`、`duration_ms`、`is_final`；
- `message_id` 关联对应 `ai_text`；`index` 为消息内音频块序号；`is_final` 标记最后一块；
- 当前 Android SDK 只消费 `data`/`codec`/`duration_ms`，忽略 `message_id`/`index`/`is_final`，并自行生成连续 sequenceNumber（§4/§13）。

---

## 4. `ai_audio` 接收与解码

- `SessionManager` 收到 `ai_audio` → `ListenerDispatcher.onAiAudio(data, codec, durationMs)`。
- `LivaChatSDK` 的 `aiAudioHandler`：
  - `audioSeqGen.incrementAndGet()` 生成**App 内部** sequenceNumber（不是协议 `msg.seq`）；
  - Base64 解码；
  - 投递到 `SdkDispatchers.audioRecv`；
  - `AudioRecvPipeline.handleAiAudio` → `OpusDecoderImpl.decode` → `listener.onAiAudioPcm(pcm, sampleRate, 16, durationMs, seq, turnId="")`（`sampleRate` 取 SDK 配置，当前 App 默认 16 kHz）。
- 当前 SDK **只使用** `data`、`duration_ms`：
  - `codec` 形参虽传入 `aiAudioHandler`，但当前实现忽略、不校验；
  - 忽略 `message_id`、`index`、`is_final`；
  - `turnId` 固定空串；
  - sequenceNumber 连续自增，正常情况下无空洞。
- `AudioRecvPipeline.reset()` 当前为空实现；解码器状态不因 session 边界重建。
- `onAiAudioPcm` 在 SDK audio-recv dispatcher 回调，不在主线程；`AudioPlaybackController` 直接在此线程 push jitter buffer（synchronized）。

---

## 5. JitterBuffer

生产参数（`AudioPlaybackController`）：

- `targetFrames = 3` → 初始缓冲 3 帧（代码日志按 20 ms 帧估算为 **60 ms**；服务端 `duration_ms` 可能不同，实际缓冲时长按各帧 duration 累计）；
- `maxLatencyFrames = 15000`（按帧数上限；20 ms 帧时约 300 s，不按真实时长换算）。

行为：

- `push(frame)`（synchronized）：
  - 已启动且 `sequenceNumber < nextExpected` → `droppedLate++`，丢弃；
  - `buffer.size >= maxLatencyFrames` → `droppedOverflow++`，丢弃**新帧**（不淘汰旧帧）；
  - 否则插入 `TreeMap<sequenceNumber, PcmFrame>`。
- `pull()`：
  - 空 → null；
  - 首次 → `nextExpected = firstKey`；
  - `nextExpected` 不存在 → 跳到当前 firstKey（容忍空洞/丢帧）；
  - 取出并 `nextExpected = seq + 1`。
- `isReady()`：`size >= targetFrames`。
- `reset()`：清 buffer/started/nextExpected/丢弃计数。
- `PcmFrame` 保存 `pcm/sampleRate/sampleBits/durationMs/sequenceNumber/turnId`；当前播放逻辑只用 `pcm`，`turnId` 未消费。
- 跨 AudioTrack 重建不丢帧：`rebuildAttempt` 不 reset jitter buffer；`start()`/`stop()`/flush 才 reset。

---

## 6. AudioTrack 播放循环

`AudioTrackFactory.Real`：

- `getMinBufferSize(16k, CHANNEL_OUT_MONO, ENCODING_PCM_16BIT)`；
- buffer = `minBuf * 3`（`minBuf <= 0` 时回退 `sampleRate * 2`）；
- `AudioAttributes.USAGE_VOICE_COMMUNICATION` + `CONTENT_TYPE_SPEECH`；
- mono、16-bit、采样率；
- `MODE_STREAM`。

`AudioPlaybackController.start(sampleRate=16000)`：

1. `_isPlaying` 已 true → 幂等；`runBlocking` join 旧播放 job；
2. `AudioRouteManager.switchRoute(SPEAKER)` + `requestAudioFocus()`；
3. 创建 AudioTrack；
4. 应用 `preferredOutputDeviceId`；不可用 → 扬声器优先回落（无扬声器 → 系统默认/null）；
5. 应用 BT SCO（仅当应用了显式偏好且最终设备是 `TYPE_BLUETOOTH_SCO`）；
6. 重放当前 `_isMuted`（新 track 默认全音量）；
7. `_isPlaying=true`、清计数、`jitterBuffer.reset()`、启动播放协程。

播放协程（`playbackDispatcher` 单线程，`THREAD_PRIORITY_URGENT_AUDIO`）：

- **初始缓冲**：`while (active && !jitterBuffer.isReady()) delay(10)`；达到 3 帧后 `track.play()`；
- **稳态**：每 10 ms tick：
  - `frame = jitterBuffer.pull()`；
  - frame != null → `track.write(frame.pcm, 0, size, WRITE_BLOCKING)`；短写仅日志；
  - frame == null → 不写静音、不补帧，只 `nullCount++`；AudioTrack 内部 buffer 自然填补短间隙；
  - tick 超过 50 ms → `slow tick` 日志；
  - `delay(tickIntervalMs - elapsed)`。
- 退出（stop/rebuild/cancel）：`pause()`、`flush()`、`stop()`、`release()`、`audioTrack=null`。
- `stop()`：取消 rebuild/播放 job、释放 SCO、`_isPlaying=false`、`jitterBuffer.reset()`、`abandonAudioFocus()`，记录 push/pull/write/null/drop 计数。
- `isPlaying` 从 `start()` 起为 true（不等待第一帧音频）；播放协程在无帧时保持等待/空转。
- `onAiAudioPcm` 在 `sampleRate != currentSampleRate` 时只日志，仍把帧 push 进 JitterBuffer（无重采样，见 §13）。

---

## 7. Flush

`AudioPlaybackController.onPlaybackShouldFlush(reason)`：

- 只 `jitterBuffer.reset()`，记录 dropped 帧数；
- **不** pause/flush AudioTrack；已写入 AudioTrack 内部 buffer 的 PCM 可能继续播放到耗尽；
- `flush()` 方法同样只 reset jitter buffer（当前生产未直接调用）。

触发来源（`SessionManager`）：

| 原因 | 触发点 |
|---|---|
| `PlaybackFlushReason.SESSION_ENDED` | `session.ended` |
| `PlaybackFlushReason.INTERRUPT_ACK` | 会话状态变为 `THINKING`（不是 `SPEAKING→LISTENING`；该分支已注释） |
| `PlaybackFlushReason.SERVER_ABORT` | 收到 fatal error |

- `AudioPlaybackController.start()` / `stop()` 也会 reset jitter buffer。
- AudioTrack 的 `flush()` 只出现在播放协程 finally（stop/rebuild 退出）中；interrupt ack 不 flush track。

---

## 8. 输出设备、路由与焦点

输出设备的选择、picker、冷启动规则、断开回落与 SCO 细节见 [`android_audio_output_devices.md`](android_audio_output_devices.md)；本节只保留播放链路所需摘要。

`AudioDeviceManager` 输出白名单：

`TYPE_BUILTIN_SPEAKER`、`TYPE_BUILTIN_EARPIECE`、`TYPE_WIRED_HEADSET`、`TYPE_WIRED_HEADPHONES`、`TYPE_BLUETOOTH_SCO`、`TYPE_BLUETOOTH_A2DP`、`TYPE_BLE_HEADSET`、`TYPE_BLE_SPEAKER`、`TYPE_USB_DEVICE`、`TYPE_USB_HEADSET`、`TYPE_HEARING_AID`。

`AudioPlaybackController` 冷启动选择（进程启动算一次，不持久化）：

1. 仅内置（听筒/扬声器）→ 内置扬声器（无扬声器 → null）；
2. 外设全部为经典蓝牙（SCO/A2DP；BLE 不算）→ SCO 优先（多台取 id 最小），无 SCO 取 A2DP（id 最小）；
3. 其他（USB/有线/BLE 等）→ null（系统默认）。

运行时：

- 显式选择：记录 id；播放中 → `rebuildTrack` 新 track + `setPreferredDevice`；未播放 → 下次 start 生效；
- 所选设备消失 → 总是回落内置扬声器（无扬声器 → null/系统默认）；设备表为空按“无信息”处理，不触发消失回落；
- 非所选设备增减 → 不改选择；
- 设备不可用三态（解析不到/平台拒绝/异常）→ 扬声器优先回落；构建失败 → 回退 previous；双败 → `_isPlaying=false` 等下次 start；
- BT SCO 仅在应用了显式偏好且最终设备为 `TYPE_BLUETOOTH_SCO` 时 `startBluetoothSco()`；未选择（null/系统默认）或其他设备 → `stopBluetoothSco()`；生命周期与选择同生共死；
- `currentOutputDevice()`：选中可解析 → 选中；选中存在但解析不到 → 扬声器优先 → track.routedDevice → null；选中为 null → track.routedDevice → null。

`AudioRouteManager`：

- `switchRoute(SPEAKER)` → 不改 speakerphone；`EARPIECE` → `isSpeakerphoneOn=false`；`BLUETOOTH` → 有蓝牙时 `isSpeakerphoneOn=false` 并返回 true，无蓝牙返回 false（不改 `mode`）；
- 成功切换 → `audioManager.mode = MODE_IN_COMMUNICATION`；
- `requestAudioFocus`：API 26+ `AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE` + `USAGE_VOICE_COMMUNICATION`/`CONTENT_TYPE_SPEECH`；失败返回 false；
- `abandonAudioFocus` → `MODE_NORMAL`。
- 当前 UI 没有独立的 SPEAKER/EARPIECE/BLUETOOTH 路由切换入口；`AudioPlaybackController.start()` 固定 `switchRoute(SPEAKER)`，实际出声口由输出设备选择器 + `setPreferredDevice` 决定。

---

## 9. 扬声器静音

- `AudioPlaybackController.setMuted(muted)`：`_isMuted` + `audioTrack.setVolume(0f/1f)`；
- `start()` 与 `rebuildAttempt()` 都会重放 `_isMuted`，避免“UI 显示已静音但新 track 全音量”；
- `ConversationViewModel` 的 `ToggleSpeaker` → `audioPlayback.setMuted(!isSpeakerMuted)`；`isSpeakerMuted` 从 controller StateFlow 合流。

---

## 10. 与 MyLife 语音播放互斥

- `ExoAudioPlayer` 使用 `USAGE_MEDIA` + `CONTENT_TYPE_SPEECH`，ExoPlayer 自管音频焦点；
- 观察 `AudioPlaybackController.isPlaying`：升为 true 且画廊正在播放 → `player.pause()`；
- **单向互斥**：不观察回落沿、不自动恢复；反向（AI 开口打断画廊）交给系统焦点；
- 画廊输出设备跟随 `preferredOutputDeviceId`；
- 实时会话 `isPlaying` 从会话 start 起为 true（不是首帧音频才 true），因此进入实时会话即暂停画廊播放。

---

## 11. 会话接线

### 11.1 ConversationCoordinator

`start()` 中播放相关顺序：

- 注册 `audioPlayback` 到 `CompositeLivaChatListener`；
- `sdk.connect()` 后按 `audioCapture.start()` → `audioPlayback.start()` → `interruptCoordinator.start()` 启动；采集细节见 [`android_audio_capture.md`](android_audio_capture.md)。
- `audioPlayback.start()` 创建 AudioTrack、申请焦点、应用输出设备、重放静音并启动播放协程。

`stop()` 中播放相关顺序：

- `interruptCoordinator.stop()` → `audioCapture.stop()` → `audioPlayback.stop()`；
- `audioPlayback.stop()` 取消播放/重建 job、释放 SCO、清 JitterBuffer、放弃焦点；
- 注销 `audioPlayback` listener。

前后台：

- 前后台 collector 只直接影响采集；播放不因退后台单独暂停，随会话 `stop()` 结束。
- 前台自愈只重建采集/连接，不重建播放；播放重建失败不会自动重试；未播放时输出设备操作只记录选择，恢复播放需重新进入会话（再次 `start()`）。

### 11.2 ConversationViewModel

- `ToggleSpeaker` → `audioPlayback.setMuted(!isSpeakerMuted)`；
- `SelectOutputDevice` → `audioPlayback.setPreferredOutputDevice(id)`；
- `ConversationUiState` 合流 `audioPlayback.isMuted`、输出设备列表/选中 id；
- `micLevel` 与输入设备状态属于采集侧，见 [`android_audio_capture.md`](android_audio_capture.md)；
- `isPlaying` 不经过 ViewModel：`ExoAudioPlayer` 直接观察 `AudioPlaybackController.isPlaying` 做画廊互斥（§10）。

### 11.3 UI

- `BottomToolbar` 扬声器按钮：`isSpeakerOn = !uiState.isSpeakerMuted`，图标 `VolumeUp/VolumeOff`，点击发 `ToggleSpeaker`；
- 多输出设备时扬声器标题前显示 `^`，打开 `OutputDevicePicker`；
- `ConversationScreen` 将 `outputDevices`/`selectedOutputDeviceId` 传入工具栏，并在 `uiState.showOutputDevicePicker` 时显示 `OutputDevicePicker`；
- 当前没有独立的 SPEAKER/EARPIECE/BLUETOOTH 路由切换入口（§8/§13）。

---

## 12. 测试

### 12.1 App / SDK 单测

| 文件 | 覆盖 |
|---|---|
| `AudioPlaybackControllerTest` | 冷启动选择（内置/经典 BT/A2DP/多台/BLE/非 BT）、设备消失回落、重建、SCO、jitter 跨重建、mute 重放、设备不可用/构建失败/双败、stop 竞态、routed device 日志 |
| `JitterBufferTest` | 连续/乱序/晚到/ready/reset/溢出/空 pull/首帧/重开/并发 |
| `AudioRouteManagerTest` | SPEAKER/EARPIECE/BLUETOOTH（无蓝牙 false） |
| `AudioRecvPipelineTest` | 解码回调、连续 seq、reset 不抛 |
| `AudioDeviceManagerTest` | 输出初始快照、白名单、增删回调、双向刷新（输入/输出共用） |
| `ConversationViewModelOutputDeviceTest` | 输出设备列表/选择/默认/null/选中 id 反显 |
| `AudioPlayerToggleTest` | 画廊播放 state/toggle 决策 |
| `AudioOutputDeviceLabelTest` | 输出设备标签、同 type 去重后缀、长名取产品段 |

### 12.2 仪器/真机

- 真机走查建议：输出设备切换、BT SCO（含 A2DP→SCO 连坐）、扬声器静音重放、播放打断/flush、设备消失回落、画廊互斥。
- `androidTest/.../AudioPlaybackControllerTest` 覆盖真机 `start/stop`、`flush` 幂等、`setMuted`、PCM 写入；AudioTrack 实际出声与 SCO 建链仍需手工验证。

### 12.3 门禁

- `:LivaChatApp:testDebugUnitTest`
- `:LivaChatSDK:testDebugUnitTest`（SDK 单元测试）
- `:LivaChatApp:assembleDebug`
- `androidTest` 编译（真机 UI 走查另行执行）
- 当前无 Android CI；音频门禁依赖本地 JVM/仪器测试（Android 工作流目前仅 iOS CI）。

---

## 13. 已知限制

1. **播放初始缓冲 3 帧（按 20 ms 帧约 60 ms）**：`runLoop` 在 `isReady()` 前无限等待；若某轮总帧数不足 3，播放不启动，直到 stop/新帧累计。
2. **JitterBuffer 上限 15000 帧**：生产参数按帧数计数（20 ms 帧约 300 s）；溢出丢**新帧**，不淘汰旧帧。
3. **下行 SDK 丢字段**：`message_id`/`index`/`is_final` 未透传；`codec` 未校验；`turnId` 固定空串；sequenceNumber 由 App 生成，不是协议 seq；无法按消息/轮次对齐或按 `is_final` 结束。
4. **Flush 只清 jitter buffer**：interrupt ack / session end / fatal error 不 flush AudioTrack；已写入 track 内部 buffer 的 PCM 可能继续播放；track `flush()` 只在 stop/rebuild 退出时调用。
5. **无重采样**：AudioTrack 固定 16 kHz；`onAiAudioPcm` 采样率不匹配只日志，仍按原帧写入。
6. **无独立路由开关**：UI 无 SPEAKER/EARPIECE/BLUETOOTH 三态切换；`start()` 固定 `switchRoute(SPEAKER)`，实际出声口靠输出设备 picker + `setPreferredDevice`。
7. **输出设备选择不持久化**：冷启动选择每次进程重算；所选设备断开回落扬声器；设备新增不改选择。
8. **SCO 需显式启停**：只有显式选择且最终设备为 `TYPE_BLUETOOTH_SCO` 才 `startBluetoothSco()`；null/系统默认、A2DP/BLE/其他会释放 SCO。
9. **画廊互斥单向**：实时会话 `isPlaying=true` 时暂停画廊；不自动恢复，反向由系统音频焦点处理；`isPlaying` 从会话 start 起即为 true，进入实时会话即暂停画廊。
10. **`AudioRecvPipeline.reset()` 为空**：解码器状态不因 session 边界重建；无内部缓冲。
11. **空帧不补静音**：JitterBuffer 为空时播放循环不写静音、不补帧，依赖 AudioTrack 内部 buffer 与系统 underrun 行为。
12. **无 Android CI**：音频门禁依赖本地 JVM/仪器测试；当前 Android 工作流仅 iOS CI。

---

## 14. 文件索引

### 14.1 App

| 文件 | 职责 |
|---|---|
| `media/AudioPlaybackController.kt` | AudioTrack、播放循环、抖动缓冲、输出设备/焦点/SCO、扬声器静音 |
| `media/JitterBuffer.kt` | 下行 PCM 重排/丢弃 |
| `media/AudioRouteManager.kt` | MODE_IN_COMMUNICATION、音频焦点、路由分支 |
| `media/AudioDeviceManager.kt` | 输入/输出设备枚举/白名单/回调（输出侧） |
| `media/AudioOutputDevice.kt` | 输出设备模型与标签规格 |
| `media/ExoAudioPlayer.kt` | MyLife 语音段播放与实时会话互斥 |
| `domain/ConversationCoordinator.kt` | 会话/播放生命周期、停止顺序 |
| `ui/screens/conversation/ConversationViewModel.kt` | speaker/output 事件、输出设备状态合流 |
| `ui/screens/conversation/ConversationUiState.kt` | `isSpeakerMuted`、输出设备列表/选中 id |
| `ui/components/BottomToolbar.kt` | 扬声器按钮、输出设备 caption |
| `ui/screens/conversation/OutputDevicePicker.kt` | 输出设备选择 |

### 14.2 SDK / 协议

| 层 | 文件 | 职责 |
|---|---|---|
| SDK | `internal/pipeline/AudioRecvPipeline.kt` | `ai_audio` 解码 |
| SDK | `internal/session/SessionManager.kt` | 下行音频分发、flush 原因 |
| SDK | `LivaChatSDK.kt` / `LivaChatListener.kt` | `ai_audio` 入口、sequence、`onAiAudioPcm` / `onPlaybackShouldFlush` |
| Protocol | `packages/api-schema/s2c/ai-audio.json` | S2C TTS 音频块 |

### 14.3 测试

`AudioPlaybackControllerTest`、`JitterBufferTest`、`AudioRouteManagerTest`、`AudioRecvPipelineTest`、`AudioDeviceManagerTest`、`ConversationViewModelOutputDeviceTest`、`AudioPlayerToggleTest`、`AudioOutputDeviceLabelTest`。
