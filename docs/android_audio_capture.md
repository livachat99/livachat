# Android 音频采集流程

> **状态**：已实施
> **基线**：2026-10-24，`versionCode 137`
> **范围**：Android 正式 App（`apps/android/LivaChatApp`）与 SDK（`apps/android/LivaChatSDK`）的实时音频上行链路：麦克风采集 → SDK 上行处理 → 服务端。
> **关联**：下行 `ai_audio` 接收与播放见 [`android_audio_playback.md`](android_audio_playback.md)。
> **不含**：服务端 ASR/TTS 模型细节、`LivaChatAppMock`；播放侧细节不在此文重复。

---

## 1. 链路总览

```text
麦克风
  │
  ▼
AudioRecord（16 kHz / mono / 16-bit / 20 ms 读帧）
  │  AudioCaptureController 读帧协程（Dispatchers.IO）
  ├─ MicLevelMeter → micLevel StateFlow（0..4）→ BottomToolbar / MicVolumeGlyph
  ├─ SilenceFlushGate：真实帧 / 1s 静音补发 / 丢弃
  └─ PcmDump（默认关闭，调试落盘）
  │
  ▼
LivaChatSDK.pushAudioPcm(pcm, offset, len, muted)
  │  audioSend dispatcher
  ▼
FrameAssembler（任意长度块 → 20 ms 帧）
  ▼
VAD（本地，VadState.SPEECH/SILENCE/UNKNOWN）
  ▼
UplinkAgc（App 显式开启；VAD 之后、编码之前；AI 说话时 unity）
  ▼
Opus encode
  ▼
ClientAudioPayload { data, codec="opus", duration_ms=20, vad_state }
  ▼
WebSocket `audio`
  ▼
服务端：dialogue / lecture / omni 分支
```

上行链路止于服务端。服务端回流的 `ai_audio`、JitterBuffer、AudioTrack 与输出设备见 [`android_audio_playback.md`](android_audio_playback.md)。

并行路径：

- **本地 VAD 打断**：`sdk.localVadState == SPEECH` 且会话状态 `SPEAKING` 时，`InterruptCoordinator` 限频 500 ms 发送 `interrupt(USER_VOICE)` 并触发轻触觉反馈（受设置 `vadInterruptEnabled` 控制）。
- **前后台**：`ProcessLifecycleOwner` → `AppForegroundState` → `ConversationCoordinator` → `AudioCaptureController.onAppBackgrounded/onAppForegrounded`；仅「录音中且静音」时后台释放麦克风。
- **采集保护**：采集成功后补启 `MicCaptureService`（microphone FGS）；FGS 是后台 while-in-use 豁免，不是采集功能的前置条件。

---

## 2. 组件与职责

| 层 | 组件 | 职责 |
|---|---|---|
| App | `AudioCaptureController` | AudioRecord 生命周期、读帧、重试/代次、源/输入设备切换、mute/后台挂起、电平、PCM dump、Mic FGS |
| App | `SilenceFlushGate` | mute 沿后 50 帧（1 s）静音补发窗口；窗口后丢弃 |
| App | `MicLevelMeter` | 20 ms 帧 RMS → dBFS → 4 段量化 → 快攻慢放平滑 |
| App | `PcmDump` | 调试 PCM 落盘（默认关闭），非阻塞、按采集代分文件 |
| App | `MicCaptureService` | `microphone` 类型 FGS 薄壳，提供 while-in-use 豁免 |
| App | `AppForegroundState` | 进程前后台事实（`ProcessLifecycleOwner`） |
| App | `AudioDeviceManager` | 输入/输出设备枚举、白名单、设备变化回调；本文只用输入侧，输出侧见播放文档 |
| App | `InterruptCoordinator` | 本地 VAD speech + SPEAKING → 限频打断 |
| App | `ConversationCoordinator` | 会话启动/停止、采集/FGS 生命周期、前后台转发、前台自愈；播放启停仅做顺序编排，细节见播放文档 |
| App | `ConversationViewModel` | mute/source/输入设备事件、micLevel 透传、采集状态合流；播放事件见播放文档 |
| SDK | `AudioSendPipeline` / `FrameAssembler` | 20 ms 帧组装、VAD、AGC、Opus、muted 标记 |
| SDK | `UplinkAgc` | makeup-only AGC（只提升不衰减） |
| SDK | `LivaChatSDK` / `SessionManager` | 上行 PCM 入口、协议封装、发送闸口；下行分发见播放文档 |
| Server | `services/session/orchestrator.py` | 音频分支、VAD 轮次、muted 零 PCM 替换 |
| Protocol | `packages/api-schema/c2s/audio.json`、`common.json` | 上行音频字段、VAD 状态、采样/时长约束 |

---

## 3. 上行采集（App）

### 3.1 启动、停止、FGS 与权限

- `ConversationCoordinator.start()` 在创建会话并连接后调用：
  1. `audioCapture.start()`（默认 16 kHz）；
  2. `interruptCoordinator.start()`。
- 播放启动由同一会话流程编排，顺序与细节见 [`android_audio_playback.md`](android_audio_playback.md) §11.1。
- `AudioCaptureController.start()`：
  - `_isCapturing` 已 true → 幂等返回；
  - 无 `RECORD_AUDIO` → 仅 `logWarn("start skipped: mic permission missing")` 后返回，不抛 UI 错误；
  - 设置采样率、清重试计数、`startInternal()`；
  - 采集成功后才 `startForegroundService(MicCaptureService)`（先采集、后补 FGS，与录屏相反）。
- `stop()`：
  - 清 `suspendedForBackground`；
  - `teardownCapture()`：`stopping=true`、`generation++`、`_isCapturing=false`、清 flushGate/电平、取消读协程、`AudioRecord.stop/release`、关 PCM dump、`stopService(MicCaptureService)`。
- `MicCaptureService`：
  - `onStartCommand`：若 `controller.isCapturing == false` → `stopSelf()`；否则 `startForeground(type=microphone)`；`SecurityException`（API 34 后台 while-in-use 失败）→ `stopSelf()` 降级；
  - `START_NOT_STICKY`；
  - channel `microphone`、notification id `1002`、标题/正文取资源；
  - `onDestroy` → `controller.onMicServiceDestroyed()`；若仍在采集，按 1 s 冷却重启 FGS（后台重启失败静默）。
- FGS 是**保护/豁免**而非采集必需：`startForegroundService` 同步失败时仅日志，采集继续；正常停止/死态/后台挂起都会停 FGS。

### 3.2 AudioRecord 与读循环

`AudioRecordFactory.Real`：

- `AudioRecord.getMinBufferSize(16k, CHANNEL_IN_MONO, ENCODING_PCM_16BIT)`；
- buffer = `maxOf(minBuf * 2, frameSamples * 2)`（`minBuf` 可能为负，地板上限防构建失败）；
- `AudioRecord.Builder`：`source`、mono、16-bit、采样率、buffer；
- `state != STATE_INITIALIZED` → 释放并抛内部 `AudioCaptureFailed`。

`startInternal()`：

1. `frameSamples = sampleRate * 20 / 1000`（16 kHz → 320 samples）；
2. 创建 AudioRecord，校验 state；
3. 应用 `preferredInputDeviceId`：解析不到/平台拒绝/异常 → 静默回落默认并清选择（不视为重建失败）；
4. `startRecording()`，记录 `routedDevice` 日志；
5. `_isCapturing=true`；
6. 创建/轮换 `PcmDump`（若启用）；
7. 启动 `Dispatchers.IO` 读循环。

读循环：

```text
flushGate.resetBaseline(_isMuted.value)
while (isActive && _isCapturing):
    read = audioRecord.read(frame, 0, frameSamples)   // 阻塞
    if read < 0: handleReadError(...); return
    if active && capturing: micLevel = levelMeter.onPcmFrame(frame, read, isMuted)
    when flushGate.decide(isMuted):
      SEND_REAL    -> dump?.write(frame, read); sdk.pushAudioPcm(frame, 0, read)
      SEND_SILENCE -> sdk.pushAudioPcm(zeroFrame, 0, frameSamples, muted=true)
      DROP         -> nothing
退出：micLevel=0; levelMeter.reset()
```

- `read` 可以短读；SDK `FrameAssembler` 负责把任意长度块拼成 20 ms 帧。
- 读错误：`handleReadError` 先按捕获代次校验；匹配则释放 AudioRecord、`retryCount++`；≤3 次且未停止 → 同步 `startInternal()` 重建；超过 → 死态：`_isCapturing=false`、电平归零、取消读协程、关 dump、停 FGS，仅日志。
- 死态恢复：`ConversationCoordinator` 在前台且 `sessionActive` 时，若 `!isCapturing` → `audioCapture.start()`（前台自愈）；另：连接 `DISCONNECTED` 时 `sdk.connect()`。
- `generation` 用于作废旧读循环的迟到错误；`stopping` 防止 stop 后读错误重试复活采集。
- 采样率固定：当前会话默认 16 kHz；无运行中改采样率入口。

### 3.3 采集源（原声 / 语音）

`TranscribeAudioSource`：

| 值 | AudioSource | 说明 |
|---|---|---|
| `VOICE`（默认） | `VOICE_COMMUNICATION` | 语音通信链，带平台回声消除/降噪 |
| `RAW` | `MIC` | 原声（仅转录模式可选） |

- `ConversationViewModel` 观察 `(isTranscribeMode, transcribeAudioSource)`：
  - `transcribe && source == RAW` → `MIC`；
  - 其他情况 → `VOICE_COMMUNICATION`。
- 因此离开转录模式、切到翻译/讲座、清会话等都会归位 `VOICE_COMMUNICATION`。
- `AudioCaptureController.switchAudioSource(source)`：
  - 相同源 → 幂等；
  - 未采集 → 只记录，下次 `start()` 生效；
  - 采集中 → `restartCaptureLocked`：冻结旧链路（generation++、取消读协程、release）→ 用新源 `startInternal()`；
  - 新源失败 → 回退 previous 再试一次；回退成功补启 FGS；两者皆败 → 死态（FGS 停），仅日志。
  - 不停止 FGS（成功路径）、不重置 mute/电平；`flushGate` 跨重建保留，读循环起点 `resetBaseline` 处理。
- `TranscribeSourcePicker` 只写 `UiState.transcribeAudioSource`；真正下发由上面的派生 collector 完成。

### 3.4 输入设备选择

- `AudioDeviceManager` 白名单输入类型：
  `TYPE_BUILTIN_MIC`、`TYPE_WIRED_HEADSET`、`TYPE_BLUETOOTH_SCO`、`TYPE_BLE_HEADSET`、`TYPE_USB_DEVICE`、`TYPE_USB_HEADSET`。
- 进程级 `AudioDeviceCallback`：任意设备增减 → 同时刷新输入/输出列表（主线程 Handler）；初始同步快照一次。
- `AudioCaptureController.setPreferredInputDevice(id)`：
  - 幂等；未采集 → 只记录；
  - 采集中 → `restartCaptureLocked` 重建，应用 `AudioRecord.setPreferredDevice`；
  - 设备解析不到/平台拒绝/调用异常 → 静默回落默认并清选择；
  - 重建失败 → 恢复 previous 再试；两者皆败 → 死态。
- 源切换与设备切换共用 `restartCaptureLocked`，切换源时保留输入设备偏好。
- `preferredInputDeviceId` 是 controller 事实源，UI 反显；不跨进程持久化。

### 3.5 静音与静音补发

`AudioCaptureController.setMuted(muted)` 只更新 `_isMuted` StateFlow；mute 沿由读循环内的 `SilenceFlushGate` 检测，避免跨线程状态机竞态。

`SilenceFlushGate`（默认 `flushFrames = 50`，50 × 20 ms = **1 s**）：

- `decide(false)` → `SEND_REAL`，清窗口；
- 首次 `decide(true)`（false→true 沿）→ 设 `remaining=50`，开始补发；
- 窗口内 → `SEND_SILENCE`，每帧递减；窗口耗尽 → `DROP`；
- `resetBaseline(mutedNow)`：读循环起点调用；若启动时已静音且无剩余窗口，不产生新 mute 沿，直接进入 DROP；
- `reset()`：完整 stop/会话 teardown 调用，清残留窗口；
- 读错误重建走 `resetBaseline`；若发生在窗口内，续发剩余帧，不重置窗口。

静音语义：

- 补发帧内容为全零 `ShortArray`，`sdk.pushAudioPcm(..., muted=true)`；
- 真实帧 `muted=false`；
- 电平表在 muted 时恒 0（即使读到的仍是真实拾音）；PCM dump 不写 muted 帧；
- 目的：dialogue 路径让服务端 VAD 收到足够连续静音（默认 medium 阈值 600 ms；low 900 ms/high 400 ms），使当前 ASR 句正常 finalize，而不是直接断流；lecture 路径则由服务端 5× 零帧放大供云端端点收尾（§5.2）。
- 后台挂起前 `topUpMuteFlushWindow()` 按 `flushGate.remainingFrames` 补满本次 1 s 窗口（只多不少），再拆除；会话 stop 不需要补。

### 3.6 前后台释放/恢复

`AppForegroundState`：

- `ProcessLifecycleOwner` onStart/onStop 发布 `isForeground`；初始 true；
- onStop 有约 700 ms debounce（ProcessLifecycleOwner 行为）。

`ConversationCoordinator`：

- `start()`：先取消旧 collector 再注册 `appForegroundState.isForeground.collect`；
- 回前台：`audioCapture.onAppForegrounded()`；若 `sessionActive`：
  - `!audioCapture.isCapturing` → `audioCapture.start()`（前台自愈）；
  - `sdk.connectionState == DISCONNECTED` → `sdk.connect()`；
- 退后台：`audioCapture.onAppBackgrounded()`；
- `stop()`：先取消 collector，再停采集/FGS，`sessionActive=false`，之后回前台不复活；播放停止顺序见播放文档。

`AudioCaptureController`：

| 状态 | 行为 |
|---|---|
| 前台，任意 mute | 保持采集 |
| 后台，unmuted | 保持采集 + mic FGS（后台语音/屏幕交互）；FGS 缺失时平台可能喂全零 |
| 后台，muted 且采集 | `suspendedForBackground=true` → `topUpMuteFlushWindow()` → `teardownCapture()`（释放 AudioRecord、停 FGS） |
| 前台，挂起标志存在 | 清标志 → `start(currentSampleRate)`（权限校验、重建、补启 FGS） |
| 会话 stop | 清挂起标志，不因回前台复活 |

- 挂起期间 `switchAudioSource` 只记录，恢复后生效。
- 挂起期间会话终结（`stop()`/VM onCleared）清标志，不复活。
- 挂起态无 FGS；回前台补启 FGS（前台窗口合法）。
- 后台 unmuted 采集继续；FGS 被系统回收由 `onMicServiceDestroyed` 自愈（1 s 冷却，后台失败静默）。

### 3.7 电平表与 PCM dump

`MicLevelMeter`：

- 参数：4 段、floor `-45 dBFS`、ceil `-8 dBFS`、release `0.85`/帧；
- 每 20 ms 帧计算 RMS → dBFS → 归一到 [0,1] → 攻快放慢（`norm >= smoothed` 立即取，否则乘 release）；
- `muted` 或空帧 → 0 并清平滑态；
- `AudioCaptureController.micLevel: StateFlow<Int>`（0..4）；停止/死态/挂起归 0；
- UI：`ConversationViewModel.micLevel` 独立透传（不并入 `ConversationUiState`，避免 50 Hz 整屏重组）；`BottomToolbar` 在 mic 开启时用 `MicVolumeGlyph` 自绘 4 段话筒头；TalkBack 不播报电平（仅 testTag/LevelKey）。

`PcmDump`：

- `PcmDump.ENABLED = false`（默认关闭，仅调试）；
- 每次 `startInternal` 换新实例/文件：`<externalFiles>/audiodump/pcm_s<source>_r<rate>_<timestamp>.raw`；
- 独立写线程 + 64 帧队列；非阻塞 `offer`，队列满丢帧计数；单文件上限 100 MB；
- 写**增益/编码前**的真实拾音（s16le mono）；muted 帧不写；
- `close()` 记录字节数/丢弃数/是否 capped；stop 与死态都关闭；
- 用途：离线分析电平/频段/采集源 A/B，不进入正常业务流程。

### 3.8 失败与重试语义

| 场景 | 当前行为 |
|---|---|
| 无 `RECORD_AUDIO` | `start()` 日志后返回；不采集、不崩；Splash 权限门禁负责首启 |
| AudioRecord 构建/启动/state 失败 | `startInternal` catch → `handleReadError`；≤3 次重建 |
| 读错误 | 按代次校验；≤3 次重建；耗尽进入死态（停 FGS、电平 0、日志） |
| 源切换失败 | 回退 previous 再试；皆败进入死态 |
| 输入设备不可用 | 静默回落默认并清选择；不视为失败 |
| FGS 启动被拒 | 采集继续；仅失去后台豁免（后台采集可能被平台喂全零）；日志 |
| `MicCaptureService` startForeground SecurityException | `stopSelf()` 降级；采集继续但无 FGS（后台可能被喂全零） |
| 后台挂起 | 非失败；muted+后台的主动让麦 |
| 回前台 | 若挂起 → 重建；若采集死态且 sessionActive → 前台自愈 `start()` |

**当前没有麦克风采集失败的 UI 通道**：`AudioCaptureFailed` 只在 controller 内部收敛；`ConversationError.AudioError` 虽在 `ConversationScreen` 有渲染分支，但生产代码没有置位方。用户可见的间接信号是 `micLevel` 不跳动/无转写；恢复依赖前台自愈或重开会话。

---

## 4. SDK 上行处理

### 4.1 帧组装

`LivaChatSDK.pushAudioPcm(pcm, offset, len, muted)`：

- 防御性拷贝（调用方读循环复用 buffer）；
- 投递到 `SdkDispatchers.audioSend`；
- `AudioSendPipeline.pushPcm` → `FrameAssembler`；
- `FrameAssembler` 把任意长度块拼成 `sampleRate * 20 / 1000` 样本的帧（16 kHz → 320 samples）；
- 一次 push 可触发 0..N 次 `onFrame`；完整帧先复位 `pendingLen` 再回调，回调异常不会导致重复帧。

### 4.2 VAD

- `VadDetectorImpl`（WebRTC VAD，`vadSensitivity` 默认 `MEDIUM`）逐帧检测；
- 输出 SDK `VadState.SPEECH/SILENCE/UNKNOWN`；
- `onVad` → `LivaChatSDK.localVadState`（供 `InterruptCoordinator`）；
- 上行 payload：`SPEECH→speech`、`SILENCE→silence`、`UNKNOWN→silence`；muted 帧覆盖为 `muted`；
- 服务端用 `vad_state` 驱动 dialogue turn 结束（默认 medium 600 ms 连续静音）与打断门。

### 4.3 上行 AGC

- SDK 默认 `uplinkAgc=false`；App 在 `AppModule` 显式 `uplinkAgc=true`。
- `AudioSendPipeline` 在 **VAD 之后、Opus 编码之前**调用 `UplinkAgc.process`：
  - VAD/`onVad` 看到原始信号；只有编码器输入被提升；
  - `isAiSpeaking()`（`SessionState.SPEAKING`）为 true 时保持 unity，避免放大 AEC 残留回声造成假打断；
  - muted 全零帧 peak=0，直接跳过逐样本增益。
- 参数：
  - target `-20 dBFS`；
  - max gain `+18 dB`；
  - gate `-55 dBFS`（低于视为静音/底噪，不提升）；
  - ceiling `-3 dBFS`（按峰值限制本帧增益，防削顶）；
  - attack `20 ms`、release `400 ms`；
  - 只提升、不衰减（makeup-only）。
- `UplinkAgc.reset()` 回到 unity；当前生产路径未调用 `AudioSendPipeline.reset()`，AGC 增益状态跨采集 stop/start 保留（见 §8）。

### 4.4 Opus 与协议

- Opus 编码（16 kHz、20 ms 帧、mono；协议 `codec="opus"`）。
- `ClientAudioPayload`：
  - `data`：Base64 Opus；
  - `codec`：`opus`；
  - `duration_ms`：20；
  - `vad_state`：`speech` / `silence` / `muted`。
- `muted` 标记由 `AudioSendPipeline.pushPcm(..., muted)` 穿透；`FrameAssembler` 跨 push 拼帧时，本帧取“完成该帧的最后一次 push”的 muted 值（mute 沿至多一帧混合，取静音侧；unmute 沿同理取最后一次真实 push）。
- 服务端收到 `vad_state="muted"` 不解码 Opus，直接替换为零 PCM（见 §5.2）。
- `LivaChatSDK` 对 push 异常做首次日志节流（同一失败段只记首条），不让协程裸异常崩进程。

### 4.5 本地 VAD 打断

`InterruptCoordinator`：

- 输入：`sdk.sessionState`、`sdk.localVadState`、`settings.vadInterruptEnabled`；
- 条件：`enabled && sessionState == SPEAKING && vadState == SPEECH`；
- 限频 500 ms：`sdk.sendInterrupt(InterruptReason.USER_VOICE)` + `haptic.lightTap()`；
- `start(scope)` 先取消旧 job 再注册；会话 stop 时停止。
- 与服务端打断相互独立：本地 VAD 只负责“用户开口”的快速打断，服务端仍按自身门控处理。

---

## 5. 协议与服务端

### 5.1 C2S `audio`

`packages/api-schema/c2s/audio.json`：

- 必填：`data`（Base64 Opus）、`codec`、`duration_ms`、`vad_state`；
- 帧长 20 ms；采样率 16/24 kHz；CBR 32–64 kbps（schema 描述）；
- `vad_state` 枚举：`silence` / `speech` / `muted`（`common.json` `$defs.VadState`）；
- 服务端用连续静音帧（≥600 ms，medium）判断用户 turn 结束；
- `muted` = 客户端 mic 静音补发帧，服务端不解码，替换为零 PCM。

### 5.2 服务端 muted 替换与 lecture 失败语义

`services/session/orchestrator.py::_resolve_audio_pcm`：

- `vad_state == "muted"`：
  - 不解码 Opus；
  - 生成全零 PCM 帧：`sample_rate * 20 / 1000 * 2` 字节（16 kHz → 640 B/20 ms）；
  - lecture 分支活跃且引擎存在时每条 muted 帧放大为 **5 帧**零 PCM（100 ms/条，5× 压缩投递），omni/dialogue 维持 **1:1**；
  - 逐帧 debug 日志：收到 muted / 送出零 PCM（计数、字节数、采样率）。
- 非 muted：原样 `_decode_opus`；畸形帧按既有守卫丢弃。
- 每个 muted 帧仍会计入会话 `muted_n`，session.start 重置。
- 客户端 1 s 静音窗口（50 帧）在 dialogue 下覆盖默认 600 ms / low 900 ms 阈值；lecture 5× 放大用于满足云端端点约 3.4 s 静音样本的特征。

lecture 路径另有独立失败语义：`lecture_translate.py` 的 `feed_pcm` 失败或流终结时只向客户端报 `TRANSLATE_UNAVAILABLE` 一次，不关闭引擎、不回落逐轮；此后 `feed_pcm` 静默丢弃（`_mark_failed`）。muted 补发不改变这条失败路径。

---

## 6. 会话接线

### 6.1 ConversationCoordinator

`start()`：

- 注册 `listenerAdapter`（播放 listener 的注册见播放文档）；
- 取消旧前后台 collector 后注册 `appForegroundState`；
- 创建/获取当天会话，设置 `sessionActive=true`；
- `sdk.connect()`；
- `audioCapture.start()`；
- `interruptCoordinator.start(scope)`；
- 播放启动顺序见 [`android_audio_playback.md`](android_audio_playback.md) §11.1；
- 启动连接代际观察：连接成功且 `sessionActive` → `sdk.startSession(serverSessionId)`（续会话）。

`stop()`：

- 取消前后台 collector，`sessionActive=false`；
- `interruptCoordinator.stop()`；
- `audioCapture.stop()`；
- 播放停止顺序见 [`android_audio_playback.md`](android_audio_playback.md) §11.1；
- `cameraController.unbind()`；
- `sdk.disconnect()`；
- 结束本地会话记录、清 session、注销 listener。

前台自愈：

- `sessionActive` 且回前台：
  - `!audioCapture.isCapturing` → `audioCapture.start()`；
  - `sdk.connectionState == DISCONNECTED` → `sdk.connect()`。
- 两个调用均幂等；`sessionActive` 门控防 stop 后复活。

### 6.2 ConversationViewModel

- `micLevel: StateFlow<Int>` 直接来自 `audioCapture.micLevel`，不并入 `ConversationUiState`；
- `ToggleMic` → `audioCapture.setMuted(!isMicMuted)`；
- `SelectMicInputDevice` → `audioCapture.setPreferredInputDevice(id)`；
- 采集源 collector：`(isTranscribeMode, transcribeAudioSource)` → `switchAudioSource(RAW→MIC / 其他→VOICE_COMMUNICATION)`；
- `ConversationUiState` 合流采集相关字段：`audioCapture.isMuted`、输入设备列表/选中 id；`micLevel` 走独立 StateFlow，不并入 UiState；
- `onCleared()` → `screenCaptureController.stop()` + `coordinator.stop()`（采集/FGS 随会话停止）。

### 6.3 UI

- `BottomToolbar`：
  - mic 开启时 `MicVolumeGlyph` 自绘 4 段话筒头，collect `micLevel`；关闭时普通 MicOff；
  - 多输入设备时麦克风标题前显示 `^`，打开 `MicDevicePicker`；
- `ConversationScreen` 将 `viewModel.micLevel` 传给 `BottomToolbar`；
- `TranscribeSourcePicker`：原声 / 语音 二选一，写 `UiState.transcribeAudioSource`；
- 错误横幅可渲染 `ConversationError.AudioError`，但当前没有采集侧生产方（§3.8）。

---

## 7. 测试

### 7.1 App / SDK 单测

| 文件 | 覆盖 |
|---|---|
| `AudioCaptureControllerSourceTest` | 源切换（采集中/未采集/同源）、读错误代次、构建失败重试、未初始化 state 死态、连续切换收敛、源回退、设备选择/回退、源切换保留设备、设备异常回落 |
| `AudioCaptureControllerBackgroundTest` | 后台 unmuted 保持、后台前 no-op、muted 后台挂起+停 FGS、前台恢复+补 FGS+mute 保留、静音窗口 top-up、稳态无多余 top-up、stop 清挂起、双挂起幂等、权限撤销恢复、挂起期间源切换 |
| `SilenceFlushGateTest` | unmuted 真实帧、首次 muted 起窗、启动即静音、unmute/再 mute、快速 mute 抖动、读循环重建续窗、窗口耗尽、stop 清窗、重建后清理 |
| `MicLevelMeterTest` | 静音/满幅/安静/正常语音、muted 归零、快攻、慢放、空帧、reset |
| `PcmDumpTest` | 落盘/队列/上限/capped/关闭/非阻塞/文件命名 |
| `AudioDeviceManagerTest` | 输入/输出初始快照、白名单、增删回调、双向刷新（输入/输出共用） |
| `AudioSendPipelineTest` | 单帧/多帧/部分帧组装、reset、VAD 传播、AGC 落点（VAD 原帧/编码器提升）、AI speaking unity、AGC 关闭、muted 穿透 |
| `UplinkAgcTest` / `UplinkAgcPerfTest` | 静音/安静语音收敛/响度不衰减/gate/峰值保护/攻放/reset；热路径耗时护栏 |
| `InterruptCoordinatorTest` | SPEAKING+SPEECH 触发、LISTENING 不触发、设置关闭不触发、500 ms 限频 |
| `ConversationCoordinatorTest` | 前后台转发、重复 start 不重复 collector、前台自愈采集/连接、stop 后不复活 |
| `ConversationViewModelAudioSourceTest` | 初始 VOICE、RAW→MIC、VOICE 保持、退出 RAW/翻译/清会话归位、重连恢复 RAW |
| `ConversationViewModelMicDeviceTest` | 输入设备列表/选择/默认/null/选中 id 反显 |
| `AudioInputDeviceLabelTest` | 输入设备标签、同 type 去重后缀、长名取产品段 |

### 7.2 仪器/真机

- `androidTest/.../AudioCaptureControllerTest`：当前 `@Ignore`（mockk 与 ART 不兼容，需替代 SDK stub）；采集生命周期主要靠 JVM fake + 真机手工验证。
- 真机走查建议：权限撤销/恢复、后台 muted 释放、回前台重建、输入设备切换、mute 后服务端 turn 收尾、采集死态前台自愈。

### 7.3 门禁

- `:LivaChatApp:testDebugUnitTest`
- `:LivaChatSDK:testDebugUnitTest`（SDK 单元测试）
- `:LivaChatApp:assembleDebug`
- `androidTest` 编译（真机 UI 走查另行执行）
- 当前无 Android CI；音频门禁依赖本地 JVM/仪器测试（Android 工作流目前仅 iOS CI）。

---

## 8. 已知限制

1. **无麦克风失败 UI**：`AudioCaptureFailed` 只在 controller 内部收敛；`ConversationError.AudioError` 有渲染分支但无生产方。用户只能通过电平/转写缺失间接感知；恢复靠前台自愈或重开会话。
2. **FGS 是保护而非采集必需**：`startForegroundService` 失败时采集继续但失去后台豁免；`MicCaptureService` startForeground 失败会 `stopSelf` 降级。
3. **后台释放仅限 muted**：unmuted 后台保持采集（FGS 正常时才有 while-in-use 豁免；产品语义：后台语音/屏幕交互）；muted 后台才让麦。
4. **静音窗口 1 s**：`SilenceFlushGate` 默认 50 × 20 ms；后台挂起 top-up 只多不少；启动时已静音不会新开窗口。
5. **AGC 只提升不衰减**：安静语音补偿，响度不压低；AI `SPEAKING` 时 unity；`AudioSendPipeline.reset()` 当前生产未调用，因此 AGC 增益状态跨采集 stop/start 保留；同一原因，`FrameAssembler` 的未满帧样本也可能保留（正常 20 ms 整帧读取时通常为空）。
6. **输入设备选择不持久化**：controller 内存 StateFlow；设备不可用静默回落默认并清选择。
7. **PCM dump 默认关闭**：调试工具；写增益/编码前真实拾音，muted 不写；队列满丢帧、100 MB 上限。
8. **音量表仅 UI**：4 段、20 ms 更新、muted/停止恒 0；不进入协议，也不代表服务器 VAD 结果。
9. **无 Android CI**：音频门禁依赖本地 JVM/仪器测试；当前 Android 工作流仅 iOS CI。

---

## 9. 文件索引

### 9.1 App

| 文件 | 职责 |
|---|---|
| `media/AudioCaptureController.kt` | AudioRecord 生命周期、读循环、源/设备切换、mute/后台、电平、PCM dump、FGS |
| `media/SilenceFlushGate.kt` | mute 静音补发窗口 |
| `media/MicLevelMeter.kt` | 4 段电平量化/平滑 |
| `media/PcmDump.kt` | 调试 PCM 落盘 |
| `media/MicCaptureService.kt` | microphone FGS + 通知 |
| `media/AppForegroundState.kt` | 进程前后台事实 |
| `media/AudioDeviceManager.kt` | 输入/输出设备枚举/白名单/回调（输入侧） |
| `media/AudioInputDevice.kt` | 输入设备模型与标签规格 |
| `media/InterruptCoordinator.kt` | 本地 VAD 打断限频 |
| `domain/ConversationCoordinator.kt` | 会话/采集/FGS 生命周期、前后台转发、自愈 |
| `ui/screens/conversation/ConversationViewModel.kt` | mute/source/输入设备事件、micLevel 透传、采集状态合流 |
| `ui/screens/conversation/ConversationUiState.kt` | 采集源枚举/状态 |
| `ui/screens/conversation/TranscribeSourcePicker.kt` | 原声/语音选择 |
| `ui/components/BottomToolbar.kt` / `MicVolumeGlyph.kt` | 电平表 UI |
| `ui/screens/conversation/MicDevicePicker.kt` | 输入设备选择 |

### 9.2 SDK / 协议 / 服务端

| 层 | 文件 | 职责 |
|---|---|---|
| SDK | `internal/pipeline/AudioSendPipeline.kt` | 帧组装、VAD、AGC、Opus、muted |
| SDK | `internal/pipeline/UplinkAgc.kt` | makeup-only AGC |
| SDK | `LivaChatSDK.kt` / `LivaChatListener.kt` | 上行 PCM 入口、本地 VAD；下行回调见播放文档 |
| SDK | `internal/session/SessionManager.kt` | 上行协议发送/发送闸口；下行分发见播放文档 |
| SDK | `LivaChatConfig.kt` | 采样率/frame/VAD/AGC 配置 |
| Protocol | `packages/api-schema/c2s/audio.json` | C2S 音频帧 |
| Protocol | `packages/api-schema/common.json` | `VadState`（含 `muted`） |
| Server | `services/session/orchestrator.py` | 音频分支、muted 零 PCM 替换 |
| Server | `services/session/lecture_translate.py` | lecture 分支零帧投递与失败语义 |
| Server | `services/session/turn_detector.py` | VAD 静音阈值（low 900/medium 600/high 400 ms） |

### 9.3 测试

`AudioCaptureControllerSourceTest`、`AudioCaptureControllerBackgroundTest`、`SilenceFlushGateTest`、`MicLevelMeterTest`、`PcmDumpTest`、`AudioDeviceManagerTest`、`AudioSendPipelineTest`、`UplinkAgcTest`、`UplinkAgcPerfTest`、`InterruptCoordinatorTest`、`ConversationCoordinatorTest`、`ConversationViewModelAudioSourceTest`、`ConversationViewModelMicDeviceTest`、`AudioInputDeviceLabelTest`。
