# Android 后台支持与会话保活

> **状态**：已实施
> **基线**：2026-10-24，`versionCode 137`
> **范围**：Android 正式 App（`apps/android/LivaChatApp`）的进程前后台感知、麦克风后台释放/恢复、回前台自愈、`MicCaptureService` / `ScreenCaptureService` 两个前台服务，以及平台限制下的降级行为。
> **关联**：采集读帧与静音补发见 [`android_audio_capture.md`](android_audio_capture.md)；屏幕抓取与浮窗见 [`android_screen_recording.md`](android_screen_recording.md)；播放见 [`android_audio_playback.md`](android_audio_playback.md)。
> **不含**：iOS、服务端、SDK 内部重连算法；Doze 下的后台网络保活（当前未实现）。

---

## 1. 总览

### 1.1 当前模型

Android 侧的后台支持由四部分组成：

1. **进程前后台事实**：`ProcessLifecycleOwner` → `LivaChatApplication` → `AppForegroundState`，向会话层发布进程级 `isForeground`。
2. **麦克风后台策略**：`ConversationCoordinator` 把前后台转发给 `AudioCaptureController`；仅“采集中且 muted”时后台释放麦克风。
3. **回前台自愈**：回前台时先尝试恢复挂起的采集，再由 `ConversationCoordinator` 检查采集/连接死态并按需重建。
4. **前台服务保护**：采集运行时启动 `MicCaptureService`（microphone FGS）；屏幕抓取运行时启动 `ScreenCaptureService`（mediaProjection FGS）。两者都可与前台 UI 无直接关系地维持系统豁免/采集能力。

### 1.2 链路

```text
ProcessLifecycleOwner
  ├─ LivaChatApplication
  │    └─ AppForegroundState.isForeground
  │         └─ ConversationCoordinator
  │              ├─ 前台：
  │              │    audioCapture.onAppForegrounded()
  │              │    if (sessionActive):
  │              │      if (!audioCapture.isCapturing) audioCapture.start()
  │              │      if (connectionState == DISCONNECTED) sdk.connect()
  │              └─ 后台：
  │                   audioCapture.onAppBackgrounded()
  │                     if (isCapturing && isMuted):
  │                       topUpMuteFlushWindow()
  │                       teardownCapture()
  │                       stopService(MicCaptureService)
  │
  └─ ScreenCaptureController
       └─ _appForeground（独立 observer）
            └─ 屏幕帧门控：仅“后台 + 交互 + 未锁屏”放行

采集成功
  └─ AudioCaptureController.startMicForegroundService()
       └─ MicCaptureService（microphone，通知 1002）

开始屏幕抓取
  └─ ScreenCaptureController.start()
       └─ ScreenCaptureService（mediaProjection，通知 1001）
```

### 1.3 关键不变量

- `AppForegroundState.isForeground` 初值为 `true`。进程只可能由前台组件拉起，两个 FGS 都是 `START_NOT_STICKY`，且没有 boot receiver，因此不存在“进程诞生于后台”的路径。
- **麦克风 FGS 是保护/豁免，不是音频采集的前置条件**：FGS 启动失败时音频采集仍继续，只是失去后台 while-in-use 保护；屏幕抓取的 mediaProjection FGS 则是消费授权的前提（§5.2 / §5.6）。
- 麦克风后台释放的唯一条件是 `isCapturing && isMuted`；后台未静音时保持采集和 mic FGS。
- 回前台自愈只在 `sessionActive == true` 时执行；`ConversationCoordinator.stop()` 之后不复活采集或连接。
- 屏幕抓取的前后台门控与麦克风共享 `ProcessLifecycleOwner` 源头，但使用独立的 observer/StateFlow，二者不合并。
- 进程被杀后两个 FGS 都不重启；重新打开 App 才会重新建立会话/抓屏。

---

## 2. 进程前后台感知

### 2.1 `AppForegroundState`

`media/AppForegroundState.kt`：

```kotlin
@Singleton
class AppForegroundState @Inject constructor() {
    private val _isForeground = MutableStateFlow(true)
    val isForeground: StateFlow<Boolean> = _isForeground.asStateFlow()

    fun onEnterForeground() { _isForeground.value = true }
    fun onEnterBackground() { _isForeground.value = false }
}
```

- 进程级单例，不按 Activity/页面区分。
- 初值 `true`，依据是：进程只能由前台组件触发启动；`MicCaptureService` 与 `ScreenCaptureService` 都是 `START_NOT_STICKY`，manifest 也没有 boot receiver。
- StateFlow 在 collector 启动时会立即发射当前值；消费者不能假设第一次回调一定对应一次真实的前后台切换。
- 依赖：`androidx.lifecycle:lifecycle-process:2.8.2`（`LivaChatApp/build.gradle.kts`），与既有 lifecycle 版本对齐。

### 2.2 `LivaChatApplication`

`LivaChatApplication.onCreate()` 注册 `ProcessLifecycleOwner` observer：

```kotlin
ProcessLifecycleOwner.get().lifecycle.addObserver(object : DefaultLifecycleObserver {
    override fun onStart(owner: LifecycleOwner) {
        Log.i(TAG, "app entered foreground")
        appForegroundState.onEnterForeground()
    }

    override fun onStop(owner: LifecycleOwner) {
        Log.i(TAG, "app entered background")
        appForegroundState.onEnterBackground()
    }
})
```

- `TAG = "LivaChatForeground"`，可用 `adb logcat -s LivaChatForeground` 观察。
- `MainActivity` 生命周期上只有 `onCreate`（内部 `setContent`/`NavGraph`），不覆写 `onStart/onStop/onPause/onResume`；前后台判断集中在 Application 层。
- 工程没有 `registerActivityLifecycleCallbacks`、`onTrimMemory`、WakeLock、电池优化白名单或 boot receiver；这些能力当前都不存在。

### 2.3 `ConversationCoordinator` 的订阅

`ConversationCoordinator.start()` 中：

```kotlin
lifecycleObserveJob?.cancel()
lifecycleObserveJob = scope.launch {
    appForegroundState.isForeground.collect { fg ->
        if (fg) {
            audioCapture.onAppForegrounded()
            if (sessionActive) {
                if (!audioCapture.isCapturing.value) audioCapture.start()
                if (sdk.connectionState.value == ConnectionState.DISCONNECTED) sdk.connect()
            }
        } else {
            audioCapture.onAppBackgrounded()
        }
    }
}
```

- collector 运行在 `Dispatchers.Main`。
- `start()` 先取消旧 collector 再注册，避免重复 `start()` 产生多个订阅。
- StateFlow 立即发射当前值 `true`，因此订阅建立时会调用一次 `audioCapture.onAppForegrounded()`；此时 `sessionActive` 通常尚未置位，自愈分支不会触发。
- `stop()` 先取消 collector、置 `sessionActive=false`，再执行采集/播放/连接停止；此后的前后台变化不会再回调采集层。

### 2.4 屏幕抓取的独立前后台观察

`ScreenCaptureController` 不使用 `AppForegroundState`，而是在第一次 `start()` 时注册自己的 `ProcessLifecycleOwner` observer：

```kotlin
private val _appForeground = MutableStateFlow(true)
val appForeground: StateFlow<Boolean>

ProcessLifecycleOwner.get().lifecycle.addObserver(
    LifecycleEventObserver { _, event ->
        when (event) {
            Lifecycle.Event.ON_START -> _appForeground.value = true
            Lifecycle.Event.ON_STOP -> _appForeground.value = false
            else -> Unit
        }
    },
)
```

用途是屏幕抓取门控：

```text
shouldPushFrame = !appForeground && isInteractive && !isKeyguardLocked
```

即只有 App 在后台、屏幕处于交互态、未锁屏时，屏幕帧才允许上行；App 前台时即便 FGS 和 MediaProjection 仍在运行，也不采集自身画面。完整抓屏逻辑见 [`android_screen_recording.md`](android_screen_recording.md)。

当前有两个独立的进程前后台 observer：

- `LivaChatApplication` → `AppForegroundState`（麦克风/会话自愈）；
- `ScreenCaptureController`（屏幕帧门控）。

两者都来自 `ProcessLifecycleOwner`，没有共享同一个 `StateFlow`；修改前后台语义时需要同时检查两处。

### 2.5 生命周期语义

- `ProcessLifecycleOwner` 是**进程可见性**，不是屏幕亮灭或锁屏状态；`AppForegroundState` 也没有 `isInteractive` / keyguard 信息。
- `ON_START` 通常即时；`ON_STOP` 有约 **700 ms** 的库内 debounce（避免 Activity 间切换误报）。因此 Home 后后台策略、muted 挂起、屏幕帧门控都可能延迟到约 0.7s 才生效。
- 后台→前台时 `ON_START` 先发生，随后才执行自愈；这让“恢复挂起采集”先于“检查死态/重连”。
- `AppForegroundState` 的 `true` 初值只表示“进程启动时位于前台”，不代表当前屏幕一定解锁。

---

## 3. 麦克风后台策略

### 3.1 门控条件

`AudioCaptureController.onAppBackgrounded()`：

```kotlin
if (!_isCapturing.value || !_isMuted.value) return
suspendedForBackground = true
topUpMuteFlushWindow()
teardownCapture()
```

| 状态 | 行为 |
|---|---|
| 后台，未静音 | 保持 `AudioRecord` + `MicCaptureService`；用于后台语音/屏幕交互 |
| 后台，静音且采集中 | 补满静音窗口后完整拆除采集，停止 mic FGS，让出麦克风 |
| 后台，未采集 | no-op |
| 前台，任意静音 | 保持采集 |

只有“采集中且 muted”才后台释放；单纯 muted 但 App 仍在前台时，读循环继续运行，只是上行补静音/丢弃（见 [`android_audio_capture.md`](android_audio_capture.md)）。

### 3.2 后台释放流程

顺序固定：

1. 置 `suspendedForBackground = true`；
2. `topUpMuteFlushWindow()` 补满本次 mute 的 1 s 静音窗口；
3. `teardownCapture()`：
   - `stopping = true`，`generation++`；
   - `_isCapturing = false`；
   - 清 `flushGate`、电平归零；
   - 取消读协程；
   - `AudioRecord.stop/release`；
   - 清空 `audioRecord`、`retryCount`、关闭 PCM dump；
   - `stopService(MicCaptureService)`。

`topUpMuteFlushWindow()` 读取 `SilenceFlushGate.remainingFrames`，按剩余帧数补发 `muted=true` 的全零 PCM（总窗口 50 帧 × 20 ms = 1 s）。静音窗口已经耗尽时不额外补帧。补发发生在拆除前，目的是让服务端 VAD 收到足够连续静音并正常收尾当前 ASR 句；会话 `stop()` 不需要补窗口。

### 3.3 前台恢复流程

`AudioCaptureController.onAppForegrounded()`：

```kotlin
if (!suspendedForBackground) return
suspendedForBackground = false
start(currentSampleRate)
```

`start()` 走完整重获取路径：

1. 检查 `RECORD_AUDIO` 权限；缺失则记录日志并返回，不重建、不崩溃、不启动 FGS。
2. 重置 `stopping=false`、`retryCount=0`，以当前采样率重建 `AudioRecord`。
3. 重新应用 `preferredInputDeviceId` 和当前 `AudioSource`。
4. 成功后置 `_isCapturing=true`，启动读循环。
5. 若 `_isCapturing` 为 true，启动 `MicCaptureService`。

恢复时 **mute 状态保持不变**：如果挂起前是 muted，恢复后仍为 muted，读循环先补静音/丢弃；如果挂起前未静音，恢复真实上行。

### 3.4 边界与幂等性

- 重复后台回调：第一次已拆除后 `_isCapturing=false`，后续 `onAppBackgrounded()` 直接 no-op。
- 连续“挂起→恢复”循环：每次恢复都会重建 `AudioRecord` 并重新启动 FGS；测试覆盖重复挂起与多次恢复。
- 挂起期间切换采集源/输入设备：`switchAudioSource()` / `setPreferredInputDevice()` 在 `_isCapturing=false` 时只记录参数，恢复时应用到新 `AudioRecord`。
- 挂起期间权限被撤销：恢复时权限检查早退，保持未采集；不会抛异常或启动 FGS。
- `stop()`：先清 `suspendedForBackground`，再执行 `teardownCapture()`；之后回前台不会复活采集。
- 后台释放只发生在会话存活且采集运行时；如果后台事件到达时本来就没有采集，不会设置挂起标志，回前台也不会因此重建。若挂起标志已设置（后台静音拆除后的状态），回前台会按 §3.3 重建。
- `onAppBackgrounded()` 自身 `@Synchronized`；挂起/恢复与 `stop()`、读错误重试在同一对象锁下串行。

### 3.5 与采集链路的关系

- `AudioRecord` 的读帧、静音补发、读错误重试、电平表、PCM dump 细节见 [`android_audio_capture.md`](android_audio_capture.md)；本文只描述前后台门控和 FGS。
- 后台未静音时，`MicCaptureService` 提供 microphone while-in-use 豁免，系统才允许后台继续获得真实麦克风数据。
- 后台静音时，采集被完整释放，没有 FGS；这既避免继续占用麦克风，也避免 FGS 通知在无采集时残留。
- FGS 启动失败时，采集仍可运行，但后台未静音场景可能被系统喂全零，见 §5.3。

---

## 4. 回前台自愈

### 4.1 触发与顺序

前台事件处理顺序固定为：

1. `audioCapture.onAppForegrounded()`：若存在挂起标志，重建采集并启动 FGS。
2. `if (sessionActive)`：
   - `!audioCapture.isCapturing` → `audioCapture.start()`；
   - `connectionState == ConnectionState.DISCONNECTED` → `sdk.connect()`。

自愈日志：

```text
ConversationCoordinator  foreground self-heal: capture restart
ConversationCoordinator  foreground self-heal: connect
```

采集死态的观测日志由 controller 在读错误重试耗尽时打印：

```text
AudioCaptureController    capture dead: read retries exhausted (FGS stopped)
```

该路径没有用户可见的错误提示，前台自愈是主要恢复路径。

### 4.2 自愈条件

| 条件 | 动作 |
|---|---|
| 挂起标志存在 | `onAppForegrounded()` 内 `start()`，恢复采集/FGS |
| `sessionActive == false` | 不做任何自愈 |
| `!isCapturing`（采集死态） | `audioCapture.start()`，成功后重建 + FGS |
| `connectionState == DISCONNECTED` | `sdk.connect()` |
| 连接为 `CONNECTING` / `RECONNECTING` | 不主动重复 connect，交给 SDK 重连机制 |
| 采集与连接都正常 | no-op |

`audioCapture.start()` 自带 `_isCapturing` 幂等守卫；`sdk.connect()` 只在 `DISCONNECTED` 时调用，避免在重连过程中重复发起连接。

### 4.3 自愈边界

- **只在前后台切换时触发**：停留在前台期间采集/连接再死亡，没有周期性 watchdog；必须再次经过后台→前台或重开会话。
- **只检测 `isCapturing == false`**：如果阻塞式 `AudioRecord.read()` 假死而 `isCapturing` 仍为 true，自愈不会触发。
- **不检查 FGS 实例本身**：自愈只判断采集状态；FGS 丢失依赖 `MicCaptureService.onDestroy` 路径（§5.4），如果 FGS 启动失败但没有 service 实例，自愈循环不会专门补启 FGS。
- **后台不做 App 层自愈**：后台期间连接断开或采集死亡时，`ConversationCoordinator` 不会额外发起恢复；SDK 自身的重连调度仍可能运行，但 Doze/网络限制下不保证成功，回到前台后才由自愈补连。
- **进程死亡不恢复**：两个 FGS 都是 `START_NOT_STICKY`，没有 boot receiver；进程被杀后需要用户重新打开 App。
- **`stop()` 后不复活**：collector 已取消且 `sessionActive=false`，回前台不会重新启动采集或连接。

---

## 5. 前台服务

### 5.1 `MicCaptureService`

`media/MicCaptureService.kt`：

- `@AndroidEntryPoint`、`Service` 薄壳；注入同进程单例 `AudioCaptureController`。
- 唯一职责是满足 targetSdk 34 的 microphone while-in-use 要求：以 `foregroundServiceType="microphone"` 调用 `startForeground()`。
- `onStartCommand`：
  1. 入口守卫：`controller.isCapturing.value == false` → `stopSelf()`，返回 `START_NOT_STICKY`；防止 start→stop 竞态下迟到的 service 回调产生孤儿 FGS/通知。
  2. `startForegroundCompat()`：API 29+ 使用带 `FOREGROUND_SERVICE_TYPE_MICROPHONE` 的三参版本，低于 29 使用两参版本。
  3. `startForeground` 抛异常 → `stopSelf()` 降级；录音仍由 controller 维持。
  4. 返回 `START_NOT_STICKY`。
- `onDestroy` → `controller.onMicServiceDestroyed()`；正常 `stop()` 会先清 `isCapturing`，因此正常停止不会触发重启。

### 5.2 启动/停止接线

`AudioCaptureController` 的 FGS 生命周期：

| 事件 | 采集 | MicCaptureService |
|---|---|---|
| `start()` 成功且 `_isCapturing=true` | 运行 | `startForegroundService()` |
| 后台 unmuted | 保持 | 保持 |
| 后台 muted | `topUpMuteFlushWindow()` → `teardownCapture()` | `stopService()` |
| 前台恢复挂起 | `start()` 重建 | 重新启动 |
| 读错误重试（1..3 次） | 旧循环重建 | 不停，保持原 service |
| 读错误耗尽（死态） | 停止 | `stopService()` |
| 切采集源/输入设备重建 | 重建 | 不停；回退成功时按需补启 |
| `stop()` | 停止 | `stopService()` |
| 系统销毁 service 且仍在录 | 保持 | `onMicServiceDestroyed()` 尝试重启（1 s 冷却） |

启动顺序是**先采集、后 FGS**：`startInternal()` 成功后才调用 `startMicForegroundService()`。microphone FGS 不是 `AudioRecord` 的启动前置条件；这样 FGS 启动失败时不会阻断采集。与屏幕抓取相反，后者必须先进入前台服务再消费 MediaProjection 授权。

`restartCaptureLocked()`（切源/切设备）不主动停 FGS；如果首次重建失败进入死态、回退重建成功，则调用 `startMicForegroundService()` 补启。

### 5.3 启动失败降级

两处失败点分开处理：

1. **`context.startForegroundService()` 同步抛异常**（API 31+ 后台启动限制等）：`startMicForegroundService()` 捕获所有异常并静默返回，采集继续但本次没有 FGS。
2. **service 侧 `startForeground()` 抛异常**（API 34 while-in-use 等）：`MicCaptureService` 捕获后 `stopSelf()`，采集继续但失去本次豁免。

两处都**不会**停止或回滚 `AudioRecord`；FGS 失败不是采集失败。用户没有专门的“后台保护失败”提示，后台未静音时可能表现为系统喂全零/上行无声音。

### 5.4 service 销毁自愈

`MicCaptureService.onDestroy()` 调用：

```kotlin
controller.onMicServiceDestroyed()
```

`AudioCaptureController.onMicServiceDestroyed()`：

- `!isCapturing` → 直接返回（正常 stop 已先清标志）；
- 距上次尝试不足 `MIC_SERVICE_RESTART_COOLDOWN_MS = 1_000L` → 返回，防止“startForeground 失败 → stopSelf → onDestroy → 再启动”热循环；
- 否则尝试 `startForegroundService()`。

这是**防误停兜底**，不是后台自愈承诺：App 在后台时，重启 FGS 可能被平台启动限制拒绝，controller 侧 catch 静默；若系统已回收/进程已死，也不会恢复。

### 5.5 通知与权限

两个 FGS 都使用低重要性、常驻通知，点击回到 `MainActivity`：

| FGS | channel ID | 通知 ID | 图标 | 类型 |
|---|---|---:|---|---|
| `MicCaptureService` | `microphone` | 1002 | `ic_notification_mic` | `microphone` |
| `ScreenCaptureService` | `screen_capture` | 1001 | `ic_notification_record` | `mediaProjection` |

共同点：

- `NotificationChannel` 在 service 启动时创建，`IMPORTANCE_LOW`；
- `setOngoing(true)`；
- `PendingIntent.getActivity` 打开 `MainActivity`，flags 为 `NEW_TASK | SINGLE_TOP | CLEAR_TOP`，`FLAG_IMMUTABLE | FLAG_UPDATE_CURRENT`；
- 标题/正文使用本地化字符串（`notif_mic_*` / `notif_screen_*`）；
- 两个通知 ID 不同，录屏和麦克风 FGS 可同时存在，通知不互相覆盖。

`AndroidManifest.xml` 已声明 `FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_MICROPHONE`、`FOREGROUND_SERVICE_MEDIA_PROJECTION`、`POST_NOTIFICATIONS`；两个 service 均 `exported=false`，并分别声明 `foregroundServiceType`。

当前代码**没有**在运行时请求 `POST_NOTIFICATIONS`。用户未授权时，FGS 通知不在通知抽屉显示，但 FGS 仍可运行；这与后台保护是否生效无关，影响的是通知可见性。

### 5.6 `ScreenCaptureService` 与并存

`ScreenCaptureService` 是 `mediaProjection` 类型的 FGS 薄壳：

- 通知 ID 1001、channel `screen_capture`；
- `onStartCommand` 先 `startForegroundCompat()`，再 `controller.onForegroundServiceReady(generation)`；
- service 启动 Intent 携带 generation，controller 用它校验 ready 回调归属；
- `onDestroy` → `controller.onForegroundServiceDestroyed()`，录制中/等待授权时按期望代次尝试重启；
- `START_NOT_STICKY`。

屏幕抓取的真实采集由 `ScreenCaptureController` 持有（MediaProjection/VirtualDisplay/ImageReader），service 只提供前台服务语义。屏幕帧门控与独立的 `_appForeground` observer 见 §2.4；完整生命周期和失败处理见 [`android_screen_recording.md`](android_screen_recording.md)。

两个 FGS 可以并存：

- 类型不同：microphone / mediaProjection；
- channel 不同：`microphone` / `screen_capture`；
- 通知 ID 不同：1002 / 1001；
- 系统允许同进程不同 foregroundServiceType 的服务同时运行。

### 5.7 FGS 状态速查

| 场景 | `AudioRecord` | `MicCaptureService` | `ScreenCaptureService` |
|---|---|---|---|
| 会话前台，任意 mute | 运行 | 运行（启动成功后） | 未录屏则无 |
| 会话后台，未静音 | 运行 | 运行 | 未录屏则无 |
| 会话后台，静音 | 拆除 | 停止 | 未录屏则无 |
| 前台恢复挂起 | 重建 | 重启 | 未录屏则无 |
| 采集死态 | 停止 | 停止 | 未录屏则无 |
| 屏幕抓取中，App 后台 | 视麦克风状态 | 视麦克风状态 | 运行 |
| 屏幕抓取中，App 前台 | 视麦克风状态 | 视麦克风状态 | 运行，但屏幕帧被门控丢弃 |
| 进程被杀 | 停止 | 不重启 | 不重启 |

> 表中 mic FGS “运行”均指启动成功；启动失败降级见 §5.3。

---

## 6. 平台约束与已知限制

当前 Android 配置：`minSdk 26`、`targetSdk 34`、`compileSdk 34`。FGS 类型/权限声明见 §5.5。

1. **microphone while-in-use**：targetSdk 34 下，后台麦克风需要 microphone FGS；没有 FGS 时平台可能向 `AudioRecord` 喂全零。`MicCaptureService` 是这一层保护。
2. **后台启动 FGS 限制**：API 31+ 从后台调用 `startForegroundService()` 可能抛 `ForegroundServiceStartNotAllowedException`；Android 14 的 while-in-use 失败可能在 `startForeground()` 抛 `SecurityException`。两处都按 §5.3 降级，不阻断当前采集。
3. **Doze/网络**：microphone FGS 不豁免 Doze 网络限制。SDK 心跳为入站静默 15s 探测、35s 超时（`apps/android/LivaChatSDK/.../HeartbeatScheduler.kt`）；长时间后台后连接可能已断，App 层不会在后台额外补连，回前台时若状态为 `DISCONNECTED` 才由自愈补连。
4. **进程死亡**：两个 FGS 均 `START_NOT_STICKY`，manifest 无 boot receiver；进程被系统杀死或用户强停后，不会自动恢复会话/抓屏，必须重新打开 App。
5. **OEM 冻结**：厂商电池管理仍可能杀死 FGS/进程；当前没有电池优化白名单引导、WakeLock 或前台服务之外的保活手段。
6. **无持续 watchdog**：自愈只在前后台切换时检查；停留前台期间采集/连接死亡不会自动恢复。阻塞式 `read()` 假死时 `isCapturing` 仍为 true，也不会被检测。
7. **FGS 通知权限**：`POST_NOTIFICATIONS` 只在 manifest 声明，未做运行时请求；未授权时通知不显示在抽屉，但不影响 FGS 本身。
8. **两个前后台 observer 未合并**：`AppForegroundState` 与 `ScreenCaptureController._appForeground` 各自订阅 `ProcessLifecycleOwner`，语义一致但状态对象独立；新增前后台消费者时不要假设二者共享同一实例。
9. **屏幕帧门控**：屏幕抓取 FGS 只维持 MediaProjection/服务语义；App 前台、熄屏或锁屏时不会上行屏幕帧。详见 [`android_screen_recording.md`](android_screen_recording.md)。
10. **后台音频的前提**：后台未静音时采集继续；后台静音时会释放麦克风。用户静音后退后台不应期待继续采集/转写。

---

## 7. 测试与验证

### 7.1 JVM

| 文件 | 覆盖 |
|---|---|
| `AudioCaptureControllerBackgroundTest` | 后台未静音保持采集；未启动时后台 no-op；muted 后台拆除并停 FGS；前台恢复重建+FGS 且 mute 保持；静音窗口 top-up；稳态无额外补帧；stop 清挂起标志不复活；重复挂起幂等/多次循环；恢复时权限被撤早退；挂起中切源/恢复应用 |
| `ConversationCoordinatorTest` | 前后台转发；重复 `start()` 不产生重复 collector；回前台自愈重启死采集并重连；采集/连接正常时 no-op；`stop()` 后不复活 |
| `AudioCaptureControllerSourceTest` | 切源/切设备重建；死态与回退；后台挂起后恢复重新应用 preferred input device |
| `ScreenCaptureControllerRaceTest` | service 销毁/迟到的 stopService/ready 代次与 FGS 重启竞态 |

### 7.2 真机走查

1. `adb logcat -s LivaChatForeground`：Home/回 App 应出现 `app entered background/foreground`；注意 background 约 700ms debounce。
2. 会话中 Home 保持未静音：`adb shell dumpsys activity services com.livachat.livachatapp` 应见 `MicCaptureService isForeground=true`，通知仍在；对手机说话，回前台应有转写/服务端收到非零 PCM。
3. 会话中静音后 Home：麦克风采集应释放、`MicCaptureService` 消失；回前台应重建采集并补启 FGS，mute 状态保持。
4. 会话中直接 stop：`MicCaptureService` 消失；再 Home/回前台不应复活采集。
5. 模拟采集死态（读错误重试耗尽）后 Home→回前台：应见 `foreground self-heal: capture restart` 与 `capture dead` 相关日志，采集重建。
6. 断网进入 `DISCONNECTED` 后回前台：应见 `foreground self-heal: connect`。
7. 录屏与麦克风同时运行：`dumpsys activity services` 应同时看到 `ScreenCaptureService` 与 `MicCaptureService`，两条通知 ID 1001/1002 不互相覆盖。
8. 进程强停后重开：不应期待旧会话/旧抓屏自动恢复；两个 FGS 均不粘性。

### 7.3 门禁

在 `apps/android` 下：

```bash
./gradlew :LivaChatApp:testDebugUnitTest
./gradlew :LivaChatApp:assembleDebug
```

`androidTest` 需在真机/模拟器执行；FGS 的后台启动限制、通知可见性和 OEM 保活行为必须在真机验证。

---

## 8. 文件索引

### 8.1 实现

| 文件 | 职责 |
|---|---|
| `LivaChatApplication.kt` | 注册 `ProcessLifecycleOwner` observer，发布前后台事实 |
| `media/AppForegroundState.kt` | 进程级 `isForeground: StateFlow<Boolean>` |
| `domain/ConversationCoordinator.kt` | 订阅前后台；后台转发；回前台恢复/自愈；会话结束取消订阅 |
| `media/AudioCaptureController.kt` | `onAppBackgrounded/onAppForegrounded`、静音窗口 top-up、teardown、FGS 启停、`onMicServiceDestroyed` |
| `media/MicCaptureService.kt` | microphone FGS 薄壳；入口守卫；通知；`START_NOT_STICKY` |
| `media/ScreenCaptureController.kt` | 独立前后台 observer、屏幕帧门控、MediaProjection FGS 控制 |
| `media/ScreenCaptureService.kt` | mediaProjection FGS 薄壳；通知；`START_NOT_STICKY` |
| `AndroidManifest.xml` | FGS 权限、`MicCaptureService` / `ScreenCaptureService` 声明 |
| `LivaChatApp/build.gradle.kts` | `lifecycle-process:2.8.2` |
| `res/drawable/ic_notification_mic.xml` / `ic_notification_record.xml` | FGS 通知小图标 |
| `res/values*/strings.xml` | 两个 FGS 通知渠道/标题/正文 |

### 8.2 测试

| 文件 | 职责 |
|---|---|
| `test/.../media/AudioCaptureControllerBackgroundTest.kt` | 麦克风后台释放/恢复/FGS/静音窗口 |
| `test/.../domain/ConversationCoordinatorTest.kt` | 前后台转发、自愈、stop 不复活 |
| `test/.../media/AudioCaptureControllerSourceTest.kt` | 重建、死态、preferred device 恢复 |
| `test/.../media/ScreenCaptureControllerRaceTest.kt` | 屏幕 FGS 代次/销毁/重启竞态 |
