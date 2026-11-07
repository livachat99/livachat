# Android 音频输出设备

> **状态**：已实施
> **基线**：2026-10-24，`versionCode 137`
> **范围**：Android 正式 App（`apps/android/LivaChatApp`）的音频输出设备枚举、标签、选择、AudioTrack 应用、SCO、路由/焦点，以及 Audio Gallery 跟随实时会话输出设备的现状。
> **关联**：输入设备见 [`android_audio_input_devices.md`](android_audio_input_devices.md)，实时播放循环见 [`android_audio_playback.md`](android_audio_playback.md)。
> **不含**：输入设备选择细节；SDK/协议/服务端；编码/AEC/混音；Gallery 播放器 UI 与播放列表逻辑。

---

## 1. 总览

### 1.1 实时输出设备选择

```text
AudioDeviceCallback（设备集合变化）
  │  AudioDeviceManager（@Singleton，进程级）
  ▼
outputDevices: StateFlow<List<AudioOutputDevice>>
  │  ConversationViewModel collect
  ▼
ConversationUiState.outputDevices / selectedOutputDeviceId
  │  BottomToolbar「^ 扬声器」 / OutputDevicePicker
  ▼
ConversationEvent.SelectOutputDevice(id?)
  ▼
AudioPlaybackController.setPreferredOutputDevice(id?)
  │  未播放：只记录；播放中：playbackScope 重建 AudioTrack
  ▼
AudioTrack.setPreferredDevice(dev) + AudioRouteManager mode/focus + AudioPlaybackController SCO
  ▼
routedDevice 日志（平台实际采纳的路由）
```

`null` 表示“系统默认”（不指定偏好），不是固定设备。

### 1.2 Audio Gallery 跟随

```text
AudioPlaybackController.preferredOutputDeviceId / currentOutputDevice()
  │  ExoAudioPlayer 订阅 preferredOutputDeviceId
  ▼
GalleryRenderersFactory 捕获 DefaultAudioSink
  ▼
DefaultAudioSink.setPreferredDevice(currentOutputDevice())
  ▼
Gallery 的媒体 AudioTrack（USAGE_MEDIA）
```

Gallery 跟随实时会话当前输出设备；输出选择本身由本文的 picker 管理。

---

## 2. 设备模型与白名单

### 2.1 `AudioOutputDevice`

`media/AudioOutputDevice.kt`：

```kotlin
data class AudioOutputDevice(
    val id: Int,
    val type: Int,
    val productName: String?,
    val address: String?,
)
```

- `id`：`AudioDeviceInfo.getId()`，仅用于设备连接期间的匹配；不承诺跨重连稳定。
- `type`：`AudioDeviceInfo.getType()`。
- `productName`：平台可读名称；内置设备可能等于机型名，展示时需过滤。
- `address`：API 28+ 才有值；同 type 多条设备用作区分后缀。

模型是纯数据；展示名依赖列表上下文，由纯函数 `labeledOutputDevices()` 计算。

### 2.2 输出白名单

`AudioDeviceManager.ALLOWED_OUTPUT_TYPES`：

| 纳入 | 说明 |
|---|---|
| `TYPE_BUILTIN_SPEAKER` | 内置扬声器 |
| `TYPE_BUILTIN_EARPIECE` | 内置听筒 |
| `TYPE_WIRED_HEADSET` | 有线耳机 |
| `TYPE_WIRED_HEADPHONES` | 有线耳机（无麦克风） |
| `TYPE_BLUETOOTH_SCO` | 蓝牙 SCO |
| `TYPE_BLUETOOTH_A2DP` | 蓝牙 A2DP |
| `TYPE_BLE_HEADSET` | BLE 耳机 |
| `TYPE_BLE_SPEAKER` | BLE 扬声器 |
| `TYPE_USB_DEVICE` | USB 音频设备 |
| `TYPE_USB_HEADSET` | USB 耳机 |
| `TYPE_HEARING_AID` | 助听器 |

白名单外不展示，例如 `TYPE_DOCK`、`TYPE_TELEPHONY`、`TYPE_HDMI`。`TYPE_BLUETOOTH_A2DP` 已实测可作为播放出口，保留；与 SCO 同名双行通过类型后缀区分。

### 2.3 展示标签规则

`labeledOutputDevices(devices, deviceModel = Build.MODEL)`：

1. `productName` 非空且不等于机型名时，作为 base 名；
   - 若包含 `" - "`，取第一个分隔符之后的产品段；分隔后为空则保留原名。
   - 例：`USB-Audio - HUAWEI USB-C HEADSET` → `HUAWEI USB-C HEADSET`。
2. 否则使用 type 对应资源名：
   - 扬声器 / 听筒 / 有线耳机 / 有线耳机（无麦克风）/ 蓝牙语音 / 蓝牙音乐 / BLE 耳机 / BLE 扬声器 / USB 音频设备 / USB 耳机 / 助听器；
   - 未知 type 使用通用输出设备兜底名。
3. 同 type 多条时：
   - 先按 `id` 排序，避免刷新时顺序漂移；
   - `address` 非空时作为后缀，否则使用排序后的 1-based 序号。
4. 跨 type 同名冲突（典型：同一蓝牙耳机的 SCO + A2DP 两条同名）：
   - 追加类型后缀（`蓝牙语音` / `蓝牙音乐`），避免列表出现两个完全相同的名称。
5. 输出 `DeviceLabelSpec`；具体文案由 UI 层 `deviceLabelText()` 组合，支持多语言。

当前 `" - "` 取产品段规则来自 USB 麦实测；若未来出现“品牌在前”式名称，可能只显示后半段，属于已知标签风险（§9）。

### 2.4 平台语义

- `AudioManager.getDevices(GET_DEVICES_OUTPUTS)` 与 `AudioDeviceCallback` 不需要新增运行时权限。
- `AudioTrack.setPreferredDevice()` 是**偏好**而非强制；平台可能因通信模式、其他 App 抢占或 OEM 策略覆盖。
- 实际路由用 `AudioTrack.getRoutedDevice()` 查询；该值仅在播放中有效。
- `AudioManager.setCommunicationDevice()` 只接受输出 sink，会由平台自动配对输入；本功能不用它，避免输入/输出互相踩踏。
- `address` 仅 API 28+；`TYPE_BLE_HEADSET`/`TYPE_BLE_SPEAKER`/`TYPE_HEARING_AID` 等需对应平台版本。
- 设备 id 不承诺跨插拔/重连稳定，因此不做持久化。

---

## 3. 枚举与状态：`AudioDeviceManager`

### 3.1 初始化与回调

`media/AudioDeviceManager.kt`：

- `@Singleton`，通过 `@ApplicationContext` 获取 `AudioManager`；
- `init` 中注册 `AudioDeviceCallback`，handler 为主线程 `Looper`；
- 任意设备增加/移除回调都会 `refreshInputs()` **和** `refreshOutputs()`；回调不区分方向，整体重算规模很小；
- 注册后立即同步执行一次 `refreshInputs()` / `refreshOutputs()`，保证首帧不依赖异步回调；
- 进程级注册，不注销；由 Hilt 作为 `@Singleton` 持有，首次按需注入后随进程存活。

### 3.2 StateFlow

- `outputDevices`：白名单过滤后的输出设备列表；
- `inputDevices`：输入设备列表（见输入设备文档）；
- 两者都是不可变列表快照；设备变化时整体替换，不做增量 diff。

### 3.3 线程与读取

- 回调在主线程执行 `getDevices`（binder）并写 StateFlow；
- StateFlow 可在任意线程读取；
- 列表规模通常为个位数，整表重算可接受。

### 3.4 消费方

`ConversationViewModel` 在 `init` 中收集：

- `deviceManager.outputDevices` → `ConversationUiState.outputDevices`；
- `audioPlayback.preferredOutputDeviceId` → `ConversationUiState.selectedOutputDeviceId`。

---

## 4. 输出设备选择

### 4.1 控制器状态与 API

`AudioPlaybackController`：

```kotlin
private val _preferredOutputDeviceId =
    MutableStateFlow(resolveColdStartSelection())
val preferredOutputDeviceId: StateFlow<Int?>
```

- 初值由冷启动规则计算（§4.2）；`null` = 系统默认；
- `setPreferredOutputDevice(id: Int?)`：
  - 与当前值相同 → 幂等返回；
  - 未播放 → 只记录，下次 `start()` 生效；
  - 播放中 → 在 `playbackScope` 投递重建协程；
  - `null` = 取消指定，交还平台默认；
  - 不持久化，进程重启重新计算。

### 4.2 冷启动选择

`resolveColdStartSelection()` 使用 `deviceManager.outputDevices.value`（白名单过滤后的列表），进程启动计算一次：

1. 列表为空 → `null`；
2. 外设为空（仅内置听筒/扬声器）→ 内置扬声器 id；无扬声器 → `null`；
3. 外设**全部为经典蓝牙**（SCO/A2DP；BLE 不算）→ SCO 优先（多台取 id 最小）；无 SCO 取 A2DP（多台取 id 最小）；
4. 其他（含 USB/有线/BLE 等）→ `null`（系统默认）。

BLE 设备按“非经典蓝牙外设”处理，因此冷启动不会自动优先 BLE。

### 4.3 `start()` 应用与失败回落

`AudioPlaybackController.start()`：

1. `switchRoute(SPEAKER)` + `requestAudioFocus()`；
2. 创建 AudioTrack；
3. 应用 `preferredOutputDeviceId`；
4. 设备不可用（解析不到/平台拒绝/异常）→ 回落内置扬声器（无扬声器 → 系统默认/null）；
5. 应用 SCO（仅当应用了显式偏好且最终设备为 `TYPE_BLUETOOTH_SCO`）；
6. `audioTrack = track`，重放 `_isMuted`；
7. `_isPlaying=true`，清计数，`jitterBuffer.reset()`，启动播放协程。

设备应用由 `applyPreferredDevice(track, id)` 完成：

```kotlin
private fun applyPreferredDevice(track: AudioTrack, id: Int): AudioDeviceInfo? =
    runCatching {
        resolveOutputDevice(id)?.takeIf { dev -> track.setPreferredDevice(dev) }
    }.getOrNull()
```

`resolveOutputDevice()` 与 `setPreferredDevice()` 同在 `runCatching` 内；异常/拒绝/解析不到统一返回 null，由调用方回落。

### 4.4 运行中切换与重建

播放中切换设备：

- `setPreferredOutputDevice` 更新 StateFlow 后，在 `playbackScope` 启动 `rebuildTrack(previous)`；
- `rebuildTrack` 读取最新目标值，调用 `rebuildAttempt`；
- `rebuildAttempt`：
  1. 取消并 join 旧播放 job；
  2. 新建 AudioTrack；
  3. 应用目标设备；不可用 → 扬声器优先回落；
  4. 同锁字段变更段：`ensureActive`、赋值、重放静音、同步 SCO、启动 loop；
  5. 返回 `OK` / `FAILED` / `ABANDONED`（stop 已接管）。
- 首次重建失败 → 若选择未被并发改选覆盖，恢复 previous id 再重建一次；
- 回退成功 → 记录 warning；再次失败 → `_isPlaying=false`、释放 SCO、等待下一次 `start()`；
- 重建不 reset JitterBuffer；跨重建保留已缓冲帧。

### 4.5 所选设备消失

`AudioPlaybackController` 在 `deviceWatchScope` 观察 `deviceManager.outputDevices`：

- 当前选择为 `null`、设备表为空、或所选 id 仍在列表 → no-op；
- 所选设备消失 → 回落内置扬声器 id（无扬声器 → `null`）；
- 更新选择；若正在播放 → 走 §4.4 重建；
- 非所选设备的增减不改变选择；
- 设备表为空按“无信息”处理，不判定所选设备消失。

### 4.6 SCO

`syncBluetoothScoLocked(device)`：

- `want = device?.type == TYPE_BLUETOOTH_SCO`；
- `want` 变化时：
  - `true` → `startBluetoothSco()`；
  - `false` → `stopBluetoothSco()`；
- 调用点：`start()`、重建字段变更段、`stop()`、双重建失败终态；
- 只有**应用了显式偏好且最终设备为 SCO** 才拉起；`null`/系统默认、A2DP、BLE、其他设备都会释放 SCO；
- SCO 生命周期与输出选择同生共死，避免驻留钉住耳机通话模式/压制 A2DP。

### 4.7 `currentOutputDevice()`（供 Gallery）

```text
selectedId != null:
    选中 id 可解析 → 选中设备
    否则 → 内置扬声器
    否则 → 实时 AudioTrack.routedDevice
    否则 → null

selectedId == null:
    实时 AudioTrack.routedDevice
    否则 → null
```

实现细节：

- 锁内只快照 `selectedId`、`speakerId`、`audioTrack` 引用；
- `getDevices()`、`routedDevice` 等 binder 调用在锁外执行；
- `routedDevice` 读取用 `runCatching` 兜底 playback loop 异步 release 的窗口；
- `resolveSelectedLossFallback()` 只读 StateFlow，留在锁内。

### 4.8 系统默认与持久化

- 选择 `null` 只是撤销我们的偏好；平台默认可能变化，不是固定设备。
- 冷启动选择每次进程启动重算；不持久化。
- 设备 id 不保证跨插拔/重连稳定，重连后不自动恢复旧选择。
- 所选设备断开时回落扬声器/系统默认；设备重新出现不会自动回认。

---

## 5. UI

### 5.1 状态、事件与 ViewModel

`ConversationUiState` 包含输出设备三件套：

```kotlin
val outputDevices: List<AudioOutputDevice> = emptyList()
val showOutputDevicePicker: Boolean = false
val selectedOutputDeviceId: Int? = null   // null = 系统默认
```

事件：

- `ShowOutputDevicePicker`
- `DismissOutputDevicePicker`
- `SelectOutputDevice(id: Int?)`

`ConversationViewModel`：

- 收集 `deviceManager.outputDevices` → `outputDevices`；
- 收集 `audioPlayback.preferredOutputDeviceId` → `selectedOutputDeviceId`（控制器是事实源）；
- `SelectOutputDevice`：先关闭弹窗，再调用 `audioPlayback.setPreferredOutputDevice(id)`；
- 选择 `null` 即回到系统默认。

### 5.2 BottomToolbar

- `showOutputDeviceSelector = uiState.outputDevices.size > 1`。
- 多设备时，在「扬声器」文字前显示 `KeyboardArrowUp`（16dp，`TextSecondary`）。
- caption 行整体可点，打开 `OutputDevicePicker`；`^` 带 `contentDescription="选择扬声器设备"`。
- 扬声器按钮本身仍负责静音开关（`ToggleSpeaker`），与设备选择分离。
- caption 文本取资源文案的第一个空格前部分（通常为「扬声器」）；完整文案仍在 `contentDescription`。
- 三列 caption 使用统一底部 padding 与 1dp 下移；`^` 左侧悬挂、文字中心对齐上方图标。
- 触达范围：横向至少 48dp，纵向约 24dp（caption 行）。

### 5.3 OutputDevicePicker

- 首项固定为「系统默认」，`null` 表示取消指定。
- 设备列表直接使用 `outputDevices`，弹窗打开期间设备变化会实时刷新。
- 选中项：
  - `selectedDeviceId` 在当前列表中 → 对应行勾选；
  - 不在当前列表（设备被拔、控制器尚未重建清选择）→ 与「系统默认」同显。
- 行内容使用 `labeledOutputDevices` + `deviceLabelText`；
- 点任意行立即生效并关窗；外点/返回 = 取消，无底部确认按钮。
- 行高 48dp；名称 `weight(1f)` + 单行 `Ellipsis`；行尾 22dp 单选圆点；长名称不遮挡圆点。

---

## 6. Audio Gallery 输出设备跟随

### 6.1 `currentOutputDevice()`

`AudioPlaybackController.currentOutputDevice()` 的选择链见 §4.7，供 Gallery 读取实时会话当前输出设备。

### 6.2 `ExoAudioPlayer` 与 `GalleryRenderersFactory`

Gallery 使用 Media3 `ExoPlayer`，播放 Ogg Opus 语音段：

- `GalleryRenderersFactory : DefaultRenderersFactory` 覆写 `buildAudioSink()`，捕获 `DefaultAudioSink` 到 `audioSink`；
- `ensurePlayer()` 通过 `.setRenderersFactory(renderersFactory)` 使用该工厂；
- `applyPreferredDevice()`：

```kotlin
private fun applyPreferredDevice() {
    val sink = renderersFactory.audioSink
    if (sink == null) {
        Log.w(TAG, "gallery audio sink not ready; skip preferred device")
        return
    }
    runCatching { sink.setPreferredDevice(playbackController.currentOutputDevice()) }
        .onFailure { Log.w(TAG, "setPreferredDevice failed", it) }
}
```

- 应用时机：
  1. `ToggleAction.Start` 分支，在 `prepare()` / `play()` 之前；
  2. 订阅 `playbackController.preferredOutputDeviceId`，值变化时重新应用。
- `sink == null`（防御分支）只 warning，不影响播放；
- `setPreferredDevice` 异常只 warning，不影响播放；
- 不修改 `AudioManager.mode` / `isSpeakerphoneOn`，不干扰实时会话；
- Gallery 的音频属性为 `USAGE_MEDIA` + `CONTENT_TYPE_SPEECH`，`handleAudioFocus=true`（ExoPlayer 自管焦点）。

### 6.3 行为矩阵

| 实时会话状态 | Gallery 输出 |
|---|---|
| 显式选中设备且可解析 | 钉该设备 |
| 显式选中设备已失效 | 内置扬声器 → 实时 Track `routedDevice` → null |
| 选择“系统默认”且实时 Track 在播 | 实时 Track `routedDevice` |
| 选择“系统默认”且实时 Track 不存在 | null（媒体系统默认） |
| 实时会话切换输出设备 | `preferredOutputDeviceId` 变化 → Gallery 重新应用 |
| 实时 Track 重建 | 选择值不变时不重置 Gallery；选择值变化时由 StateFlow 触发 |
| 实时会话结束/停止 | Track 释放；Gallery 已应用的 device 保留，直到下次 Start 重新读取 |

### 6.4 当前限制

- **不实时跟随 routedDevice 变化**：如果实时 Track 的 `routedDevice` 在 Gallery 播放中变化，但 `preferredOutputDeviceId` 没变（例如系统默认下平台切换路由），Gallery 不会自动跟随；下次 Start 才重新读取。
- **voice/media 系统默认可能不同**：实时 Track 用 `USAGE_VOICE_COMMUNICATION`，Gallery 用 `USAGE_MEDIA`；两者都走系统默认且实时 Track 不存在时，可能落到不同设备。
- **平台可忽略偏好**：听筒/部分 SCO 路由可能不接受 `USAGE_MEDIA` 的 `setPreferredDevice`；失败只 warning。
- **不保证“绝不听筒”**：Gallery 跟随实时会话；实时会话若使用听筒，Gallery 也使用听筒。
- **Media3 unstable API**：`DefaultAudioSink.setPreferredDevice` 与 `GalleryRenderersFactory` 相关 API 属 `@UnstableApi`；升级 Media3 时需重新验证。
- **Track 释放竞态**：`currentOutputDevice()` 对 `routedDevice` 读取做了 `runCatching`；播放循环 finally 的异步 release 不会导致崩溃。

---

## 7. 路由与焦点

`AudioRouteManager`：

- `switchRoute(SPEAKER)` → 不改 speakerphone；
- `switchRoute(EARPIECE)` → `isSpeakerphoneOn=false`；
- `switchRoute(BLUETOOTH)` → 有蓝牙时 `isSpeakerphoneOn=false` 并返回 true，无蓝牙返回 false（不改 mode）；
- 成功切换 → `audioManager.mode = MODE_IN_COMMUNICATION`；
- `requestAudioFocus`：API 26+ `AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE` + `USAGE_VOICE_COMMUNICATION`/`CONTENT_TYPE_SPEECH`；失败返回 false；
- `abandonAudioFocus` → `MODE_NORMAL`。

当前 UI 没有独立的 SPEAKER/EARPIECE/BLUETOOTH 路由切换入口；`AudioPlaybackController.start()` 固定 `switchRoute(SPEAKER)`，实际出声口由输出设备 picker + `AudioTrack.setPreferredDevice` 决定。

---

## 8. 测试

### 8.1 JVM

| 文件 | 覆盖 |
|---|---|
| `AudioDeviceManagerTest` | 输出初始快照、输出白名单、设备增删回调、输入/输出双向刷新 |
| `AudioOutputDeviceLabelTest` | 机型名过滤、产品名优先、`" - "` 取产品段、同 type 地址/序号后缀、BT SCO/A2DP 同名类型后缀、未知 type 兜底 |
| `AudioPlaybackControllerTest` | 冷启动分类（仅内置/经典 BT/A2DP/多台/BLE/非 BT）、所选设备消失回落、设备新增不改选择、系统默认、重建/回退/双败终态、SCO 选择/断开回落、静音重放、jitter 跨重建、stop×rebuild 竞态、`currentOutputDevice` 全链路、routedDevice 日志 |
| `ConversationViewModelOutputDeviceTest` | 输出设备列表镜像、选中 id 反显、Show/Dismiss/Select 事件、Select 关闭弹窗并调用控制器 |
| `AudioPlayerToggleTest` | Gallery 播放/toggle 状态与实时会话互斥 |

### 8.2 仪器/真机

- `BottomToolbarTest`：扬声器 `^` 单/多设备显隐、位置/对齐、点 `^` 只触发选择器、不误触静音。
- `OutputDevicePickerTest`：系统默认/设备勾选、长名称不遮挡圆点、行距 48dp。
- `androidTest/.../AudioPlaybackControllerTest`：真机 `start/stop`、`flush` 幂等、`setMuted`、PCM 写入。
- 真机走查：扬声器/听筒/有线/USB/BT SCO/A2DP/BLE 路由、SCO 建链/释放、所选设备断开回落、Gallery 跟随矩阵、平台拒绝偏好。

### 8.3 门禁

- `:LivaChatApp:testDebugUnitTest`
- `:LivaChatApp:assembleDebug`
- `androidTest` 编译（真机 UI 走查另行执行）
- 当前无 Android CI；输出设备测试依赖本地 JVM/真机。

---

## 9. 已知限制

1. **选择不持久化**：冷启动每次进程重算；`AudioDeviceInfo.id` 不承诺跨重连稳定；设备重连不自动恢复旧选择。
2. **冷启动蓝牙优先仅限经典蓝牙**：BLE 视为非经典蓝牙外设；外设混合时走系统默认。
3. **偏好非强制**：平台可在通信模式、其他 App 抢占、OEM 策略下覆盖 `setPreferredDevice`；只承诺常规场景。
4. **设备表为空不判消失**：空表按“无信息”处理，所选设备旧 id 可暂时保留。
5. **设备新增不自动改选择**：只有所选设备消失触发回落；新插入设备不会改变当前选择。
6. **双重建失败进入终态**：`_isPlaying=false`、释放 SCO，等待下一次 `start()`；不会自动无限重试。
7. **SCO 需显式输出选择**：只有应用了显式偏好且最终设备是 SCO 才 `startBluetoothSco()`；null/系统默认、A2DP/BLE/其他会释放 SCO。
8. **SCO 可能无声**：平台/耳机链路可能未建链或拒绝媒体路由；应用只记录 `bluetooth sco requested/released`，实际建链需真机验证。
9. **无独立路由 UI**：没有 SPEAKER/EARPIECE/BLUETOOTH 三态切换；`start()` 固定 `switchRoute(SPEAKER)`，实际出声口靠输出设备 picker + `setPreferredDevice`。
10. **Gallery 不实时跟随 routedDevice 变化**：只有 `preferredOutputDeviceId` 变化或下次 Start 才重新应用。
11. **voice/media 系统默认可能不同**：实时 Track 与 Gallery 的 usage 不同；实时 Track 不存在时无法完全对齐。
12. **Gallery 可能使用听筒**：跟随实时会话的语义决定，不保证“绝不听筒”。
13. **Media3 unstable API**：`DefaultAudioSink.setPreferredDevice` 等升级时需重新验证。
14. **标签规则存在单样本启发式**：`" - "` 取产品段来自 USB 麦实测；未来“品牌在前”式名称可能只显示后半段。
15. **无 Android CI**：输出设备测试依赖本地 JVM 与真机。

---

## 10. 文件索引

### 10.1 App

| 文件 | 职责 |
|---|---|
| `media/AudioOutputDevice.kt` | 输出设备模型、输出标签规则/跨 type 后缀 |
| `media/AudioDeviceManager.kt` | 输入/输出设备枚举、白名单、进程级回调、StateFlow |
| `media/DeviceLabel.kt` | `DeviceLabelSpec` 与 UI 文案组合 |
| `media/AudioPlaybackController.kt` | `preferredOutputDeviceId`、冷启动选择、`setPreferredOutputDevice`、重建/回落、SCO、`currentOutputDevice()` |
| `media/AudioRouteManager.kt` | 路由开关、`MODE_IN_COMMUNICATION`、音频焦点 |
| `media/ExoAudioPlayer.kt` | Gallery `GalleryRenderersFactory`、`applyPreferredDevice` |
| `ui/components/BottomToolbar.kt` | 扬声器按钮、`^` 设备选择入口 |
| `ui/screens/conversation/OutputDevicePicker.kt` | 输出设备选择弹窗 |
| `ui/screens/conversation/ConversationUiState.kt` | 输出设备列表/弹窗/选中 id |
| `ui/screens/conversation/ConversationEvent.kt` | Show/Dismiss/Select 输出设备事件 |
| `ui/screens/conversation/ConversationViewModel.kt` | 输出设备列表/选中 id 收集、事件处理 |
| `ui/screens/conversation/ConversationScreen.kt` | 工具栏选择器与弹窗接线 |

### 10.2 测试

`AudioDeviceManagerTest`、`AudioOutputDeviceLabelTest`、`AudioPlaybackControllerTest`、`ConversationViewModelOutputDeviceTest`、`AudioPlayerToggleTest`、`BottomToolbarTest`、`OutputDevicePickerTest`。
