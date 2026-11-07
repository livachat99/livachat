# Android 音频输入设备

> **状态**：已实施
> **基线**：2026-10-24，`versionCode 137`
> **范围**：Android 正式 App（`apps/android/LivaChatApp`）的音频输入设备枚举、标签、选择与采集链路应用。
> **关联**：采集生命周期见 [`android_audio_capture.md`](android_audio_capture.md)；输出设备与 Gallery 输出跟随见 [`android_audio_output_devices.md`](android_audio_output_devices.md)。
> **不含**：SDK/协议/服务端；实时采集的读帧、重试、静音补发等细节；输出设备与 Gallery 输出跟随（见关联文档）。

---

## 1. 总览

### 1.1 输入设备选择链路

```text
AudioDeviceCallback（设备集合变化）
  │  AudioDeviceManager（@Singleton，进程级）
  ▼
inputDevices: StateFlow<List<AudioInputDevice>>
  │  ConversationViewModel collect
  ▼
ConversationUiState.micInputDevices / selectedMicInputDeviceId
  │  BottomToolbar「^ 麦克风」 / MicDevicePicker
  ▼
ConversationEvent.SelectMicInputDevice(id?)
  ▼
AudioCaptureController.setPreferredInputDevice(id?)
  │  未采集：只记录；采集中：restartCaptureLocked 重建
  ▼
AudioRecord.setPreferredDevice(dev)
  ▼
AudioRecord.startRecording()
  ▼
routedDevice 日志（平台实际采纳的路由）
```

`null` 表示“系统默认”（不指定偏好），不是固定设备。


---

## 2. 设备模型与白名单

### 2.1 `AudioInputDevice`

`media/AudioInputDevice.kt`：

```kotlin
data class AudioInputDevice(
    val id: Int,
    val type: Int,
    val productName: String?,
    val address: String?,
)
```

- `id`：`AudioDeviceInfo.getId()`，仅用于设备连接期间的匹配；不承诺跨重连稳定。
- `type`：`AudioDeviceInfo.getType()`。
- `productName`：平台可读名称；内置麦可能等于机型名，展示时需过滤。
- `address`：API 28+ 才有值；同 type 多条设备用作区分后缀。

模型是纯数据；展示名依赖列表上下文，由纯函数 `labeledInputDevices()` 计算。

### 2.2 输入白名单

`AudioDeviceManager.ALLOWED_INPUT_TYPES`：

| 纳入 | 说明 |
|---|---|
| `TYPE_BUILTIN_MIC` | 内置麦克风 |
| `TYPE_WIRED_HEADSET` | 有线耳机麦克风 |
| `TYPE_BLUETOOTH_SCO` | 蓝牙 SCO 麦克风 |
| `TYPE_BLE_HEADSET` | BLE 耳机麦克风（API 31+ 才可能出现） |
| `TYPE_USB_DEVICE` | USB 音频设备 |
| `TYPE_USB_HEADSET` | USB 耳机 |

白名单外一律不展示，例如 `TYPE_TELEPHONY`、`TYPE_REMOTE_SUBMIX`、`TYPE_HDMI`、`TYPE_LINE_ANALOG`、`TYPE_FM_TUNER`。这样可避免把非麦克风输入源误当成可选设备。

### 2.3 展示标签规则

`labeledInputDevices(devices, deviceModel = Build.MODEL)`：

1. `productName` 非空且不等于机型名时，作为 base 名；
   - 若包含 `" - "`，取第一个分隔符之后的产品段；分隔后为空则保留原名。
   - 例：`USB-Audio - HUAWEI USB-C HEADSET` → `HUAWEI USB-C HEADSET`。
2. 否则使用 type 对应资源名：
   - 内置麦克风 / 有线耳机 / 蓝牙耳机 / 蓝牙 LE 耳机 / USB 音频设备 / USB 耳机；
   - 未知 type 使用通用输入设备兜底名。
3. 同 type 多条时：
   - 先按 `id` 排序，避免刷新时顺序漂移；
   - `address` 非空时作为后缀，否则使用排序后的 1-based 序号。
4. 输出 `DeviceLabelSpec`；具体文案由 UI 层 `deviceLabelText()` 组合，支持多语言。

当前 `productName` 取产品段的规则来自 USB 麦实测；对不含 `" - "` 的名称无影响。若未来出现“品牌在前”式名称，可能只显示后半段，属于已知标签风险（§8）。


### 2.4 平台语义

- `AudioManager.getDevices(GET_DEVICES_INPUTS)` 与 `AudioDeviceCallback` 不需要新增运行时权限；录音本身仍需要 `RECORD_AUDIO`。
- `AudioRecord.setPreferredDevice()` 是**偏好**而非强制；平台可能因通信模式、其他 App 抢占或 OEM 策略覆盖。
- 实际路由用 `AudioRecord.getRoutedDevice()` 查询；该值仅在 recording 中有效。
- `AudioManager.setCommunicationDevice()` 只接受输出 sink，不能用于选择输入，本功能不使用。
- `address` 仅 API 28+；`TYPE_BLE_HEADSET` 仅 API 31+ 平台可能返回。
- 设备 id 不承诺跨插拔/重连稳定，因此不做持久化。

---

## 3. 枚举与状态：`AudioDeviceManager`

### 3.1 初始化与回调

`media/AudioDeviceManager.kt`：

- `@Singleton`，通过 `@ApplicationContext` 获取 `AudioManager`；
- `init` 中注册 `AudioDeviceCallback`，handler 为主线程 `Looper`；
- 任意设备增加/移除回调都会刷新输入列表；同一回调也会刷新输出侧（输出设备见 [`android_audio_output_devices.md`](android_audio_output_devices.md)）；
- 注册后立即同步执行一次 `refreshInputs()`，保证首帧不依赖异步回调；
- 进程级注册，不注销；由 Hilt 作为 `@Singleton` 持有，首次按需注入后随进程存活。

### 3.2 StateFlow

- `inputDevices`：白名单过滤后的输入设备列表；
- 列表为不可变快照；设备变化时整体替换，不做增量 diff；
- 输出侧由同一 manager 维护，见 [`android_audio_output_devices.md`](android_audio_output_devices.md) §3。

### 3.3 线程与读取

- 回调在主线程执行 `getDevices`（binder）并写 StateFlow；
- StateFlow 可在任意线程读取；
- 列表规模通常为个位数，整表重算可接受。

### 3.4 消费方

`ConversationViewModel` 在 `init` 中收集：

- `deviceManager.inputDevices` → `ConversationUiState.micInputDevices`；
- `audioCapture.preferredInputDeviceId` → `ConversationUiState.selectedMicInputDeviceId`。

---

## 4. 输入设备选择

### 4.1 控制器状态与 API

`AudioCaptureController`：

```kotlin
private val _preferredInputDeviceId = MutableStateFlow<Int?>(null)
val preferredInputDeviceId: StateFlow<Int?>
```

- 初值 `null`：冷启动为系统默认，无“蓝牙优先/内置优先”等默认规则；
- `setPreferredInputDevice(id: Int?)`：
  - 与当前值相同 → 幂等返回；
  - 未采集 → 只记录，下次 `start()` 生效；挂起态也属于未采集；
  - 采集中 → `restartCaptureLocked` 重建 AudioRecord；
  - `null` = 取消指定，交还平台默认；
  - 不持久化，进程重启回到 `null`。

### 4.2 `startInternal()` 应用设备

插入点在 `audioRecord = rec` 之后、`startRecording()` 之前：

```kotlin
_preferredInputDeviceId.value?.let { id ->
    val applied = runCatching {
        resolveInputDevice(id)?.let { dev -> rec.setPreferredDevice(dev) } ?: false
    }.getOrDefault(false)
    if (!applied) {
        sdk.logWarn("AudioCaptureController", "preferred device $id unavailable; using default")
        _preferredInputDeviceId.value = null
    }
}
audioRecord?.startRecording()
```

失败语义：

- 设备解析不到 / `setPreferredDevice` 返回 false / 调用抛异常 → 清空选择、记录日志、继续用默认路由采集；
- 设备不可用**不算**重建失败，不会进入读错误重试或死态；
- `resolveInputDevice()` 与 `setPreferredDevice()` 同置 `runCatching`，防止坏 id 把采集打死；
- 控制器不依赖 `AudioDeviceManager`；`resolveInputDevice()` 直接用 `AudioManager.getDevices(GET_DEVICES_INPUTS)` 按 id 解析；
- 任何重建路径（源切换、读错误重试、挂起恢复、下次 `start()`）都会重新应用该偏好。

`startRecording()` 成功后记录：

```text
routed device: id=... type=... addr=...
```

用于核对偏好是否被平台实际采纳。`getRoutedDevice()` 仅 recording 中有效。

### 4.3 运行中重建

`restartCaptureLocked` 与 `switchAudioSource` 共用：

1. `generation++`，`stopping=true`；
2. `_isCapturing=false`，取消读协程；
3. `AudioRecord.stop/release`；
4. `retryCount=0`，`stopping=false`；
5. `startInternal()`；
6. 新链路成功 → 返回 `OK`；
7. 首次重建因构建异常失败 → 恢复 previous id/source 再重建一次；
8. 回退成功 → 补启 FGS，返回 `FELL_BACK`；
9. 两次都失败 → 死态（采集停止、FGS 停止），仅日志。

设备切换与采集源切换正交：

- 切设备不改 `AudioSource`；
- 切源不改 `preferredInputDeviceId`；
- 成功重建不停 FGS；不重置 mute/电平/flushGate，读循环起点重新建立基线。

### 4.4 系统默认、设备断开与持久化

- **系统默认**：`preferredInputDeviceId=null`；平台默认设备不是固定值，选择“系统默认”只是撤销我们的偏好，实际可能落在任意平台默认设备。
- **设备断开（当前实现）**：输入侧没有实时设备观察；采集中拔掉所选设备时：
  - 控制器仍保留旧 id；
  - 运行中的 AudioRecord 由平台决定是否回落默认路由；
  - `MicDevicePicker` 会把“不在当前列表的选中 id”显示为“系统默认”（`effectiveSelected`）；
  - 直到下一次 `startInternal()` 解析失败，才清空偏好。

  这是惰性清理，不是实时清理。
- **未采集/挂起时**：选择只记录；恢复或下次 `start()` 应用。
- **持久化**：不做；进程重启后回到系统默认。`AudioDeviceInfo.id` 不保证跨重连稳定。

### 4.5 平台覆盖与 SCO

- `setPreferredDevice` 是 best-effort；实际 `routedDevice` 可能与选择不同。
- 输入侧不显式调用 `startBluetoothSco()`；BT SCO 输入是否可用取决于平台/通信模式与设备连接状态。
- 输入选择不修改 `AudioManager.mode`、`isSpeakerphoneOn` 或输出选择；输出侧路由/SCO 见 [`android_audio_output_devices.md`](android_audio_output_devices.md)。
- 选择输入设备不需要新增运行时权限；录音权限仍由 `AudioCaptureController.start()` 检查。

---

## 5. UI

### 5.1 状态、事件与 ViewModel

`ConversationUiState` 包含输入设备三件套：

```kotlin
val micInputDevices: List<AudioInputDevice> = emptyList()
val showMicDevicePicker: Boolean = false
val selectedMicInputDeviceId: Int? = null   // null = 系统默认
```

事件：

- `ShowMicDevicePicker`
- `DismissMicDevicePicker`
- `SelectMicInputDevice(id: Int?)`

`ConversationViewModel`：

- 收集 `deviceManager.inputDevices` → `micInputDevices`；
- 收集 `audioCapture.preferredInputDeviceId` → `selectedMicInputDeviceId`（控制器是事实源）；
- `SelectMicInputDevice`：先关闭弹窗，再调用 `audioCapture.setPreferredInputDevice(id)`；
- 选择 `null` 即回到系统默认。

### 5.2 BottomToolbar

- `showMicDeviceSelector = uiState.micInputDevices.size > 1`，与静音/模式/视频状态无关。
- 多设备时，在「麦克风」文字前显示 `KeyboardArrowUp`（16dp，`TextSecondary`）。
- caption 行整体可点，打开 `MicDevicePicker`；`^` 带 `contentDescription="选择麦克风设备"`。
- caption 文本取资源文案的第一个空格前部分（通常为「麦克风」）；完整文案仍在 `contentDescription`。
- 三列 caption 使用统一底部 padding 与 1dp 下移，保持三列文字对齐；`^` 左侧悬挂、文字中心对齐上方图标。
- 触达范围：横向至少 48dp，纵向约 24dp（caption 行）；没有把 48dp 垂直触达强加到 caption 行，避免破坏三列对齐或侵占上方静音按钮。
- 两个内置麦克风的手机会使白名单后 `size > 1`，因此 `^` 默认显示是正常现象。

### 5.3 MicDevicePicker

- 首项固定为「系统默认」，`null` 表示取消指定。
- 设备列表直接使用 `micInputDevices`，弹窗打开期间设备变化会实时刷新。
- 选中项：
  - `selectedDeviceId` 在当前列表中 → 对应行勾选；
  - 不在当前列表（设备被拔、控制器尚未重建清选择）→ 与「系统默认」同显。
- 行内容使用 `labeledInputDevices` + `deviceLabelText`；
- 点任意行立即生效并关窗；外点/返回 = 取消，无底部确认按钮。
- 行高 48dp；名称 `weight(1f)` + 单行 `Ellipsis`；行尾 22dp 单选圆点；长名称不遮挡圆点。

---

## 6. 输出设备

输出设备模型、白名单、标签、选择与 Audio Gallery 跟随不在本文展开，见 [`android_audio_output_devices.md`](android_audio_output_devices.md)。

---

## 7. 测试

### 7.1 JVM

| 文件 | 覆盖 |
|---|---|
| `AudioDeviceManagerTest` | 输入初始快照、输入白名单、设备增删回调 |
| `AudioInputDeviceLabelTest` | 机型名过滤、产品名优先、`" - "` 取产品段、同 type 地址/序号后缀、无分隔符不变 |
| `AudioCaptureControllerSourceTest` | 未采集仅记录、采集中重建、设备解析失败清选择、平台拒绝/异常回落、源切换保留设备、后台恢复重新应用、幂等 |
| `ConversationViewModelMicDeviceTest` | 输入设备列表镜像、选中 id 反显、Show/Dismiss/Select 事件、Select 关闭弹窗并调用控制器 |

### 7.2 仪器/真机

- `BottomToolbarTest`：`^` 单设备隐藏、多设备显示、位置/对齐、点 `^` 只触发选择器、不误触静音、caption 触达范围。
- `MicDevicePickerTest`：长名称不遮挡圆点、行距 48dp、系统默认/设备勾选。
- 真机走查建议：内置麦 bottom/back 互选、有线/USB/BT 插拔、系统默认、拔掉所选设备后的惰性清理、BT SCO 输入路由。
- 输出设备/Gallery 走查见 [`android_audio_output_devices.md`](android_audio_output_devices.md)。
- 真机 `routedDevice` 日志是“偏好是否生效”的唯一运行时证据；`getRoutedDevice()` 仅在 recording 时有效。

### 7.3 门禁

- `:LivaChatApp:testDebugUnitTest`
- `:LivaChatApp:assembleDebug`
- `androidTest` 编译（真机 UI 走查另行执行）
- 当前无 Android CI；音频门禁依赖本地 JVM/仪器测试。

---

## 8. 已知限制

1. **输入设备断开是惰性清理**：没有实时设备观察；采集中拔掉所选设备时，旧 id 保留到下一次 `startInternal()` 解析失败才清空；期间 UI 可以把失效 id 显示为「系统默认」。
2. **系统默认不是固定设备**：选择 `null` 只是撤销偏好，平台默认可能变化；要固定设备必须显式选择。
3. **选择不持久化**：进程重启回到系统默认；`AudioDeviceInfo.id` 不承诺跨重连稳定。
4. **偏好非强制**：平台可在通信模式、其他 App 抢占、OEM 策略下覆盖 `setPreferredDevice`；只承诺常规场景。
5. **BT SCO 输入不显式拉起 SCO**：输入侧不调用 `startBluetoothSco()`；实际可用性取决于平台/通信模式与设备状态；输出侧 SCO 见 [`android_audio_output_devices.md`](android_audio_output_devices.md)。
6. **标签规则存在单样本启发式**：`" - "` 取产品段来自 USB 麦实测；若未来出现“品牌在前”式名称，可能只显示后半段。
7. **BottomToolbar caption 触达纵向约 24dp**：为保持三列对齐与不侵占静音按钮，未强制 48dp 垂直触达；横向 ≥48dp。
8. **无 Android CI**：UI/设备测试依赖本地 JVM 与真机。

---

## 9. 文件索引

### 9.1 App

| 文件 | 职责 |
|---|---|
| `media/AudioInputDevice.kt` | 输入设备模型、输入标签规则 |
| `media/AudioDeviceManager.kt` | 输入/输出设备枚举、白名单、进程级回调、StateFlow |
| `media/AudioCaptureController.kt` | `preferredInputDeviceId`、`setPreferredInputDevice`、`startInternal` 应用、重建、routedDevice 日志 |
| `media/DeviceLabel.kt` | `DeviceLabelSpec` 与 UI 文案组合 |
| `ui/components/BottomToolbar.kt` | `^` 前缀、caption 触达、三列布局 |
| `ui/screens/conversation/MicDevicePicker.kt` | 输入设备选择弹窗 |
| `ui/screens/conversation/ConversationUiState.kt` | 输入设备列表/弹窗/选中 id |
| `ui/screens/conversation/ConversationEvent.kt` | Show/Dismiss/Select 输入设备事件 |
| `ui/screens/conversation/ConversationViewModel.kt` | 设备列表/选中 id 收集、事件处理 |
| `ui/screens/conversation/ConversationScreen.kt` | 工具栏选择器与弹窗接线 |

### 9.2 测试

`AudioDeviceManagerTest`、`AudioInputDeviceLabelTest`、`AudioCaptureControllerSourceTest`、`ConversationViewModelMicDeviceTest`、`BottomToolbarTest`、`MicDevicePickerTest`。
