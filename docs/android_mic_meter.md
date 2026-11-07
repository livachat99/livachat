# Android 麦克风音量电平（Mic Meter）

> **状态**：已实施
> **基线**：2026-10-24，`versionCode 137`
> **范围**：Android 正式 App（`apps/android/LivaChatApp`）的麦克风输入电平：PCM 帧 → 4 段量化 → `StateFlow` → 会话页工具栏图标。
> **关联**：采集生命周期、静音补发、读错误重试见 [`android_audio_capture.md`](android_audio_capture.md)；输入设备见 [`android_audio_input_devices.md`](android_audio_input_devices.md)。
> **不含**：iOS 实现、服务端 ASR/TTS、VAD、AGC、Opus 编码与上行协议。

---

## 1. 总览

### 1.1 链路

```text
AudioRecord.read(frame, 0, sampleRate * 20 / 1000)
  │  AudioCaptureController 读帧协程（Dispatchers.IO）
  ▼
MicLevelMeter.onPcmFrame(frame, read, _isMuted.value)
  │  整帧 RMS → dBFS → [-45, -8] dBFS 归一 → 快攻慢放 → 0..4 量化
  ▼
_micLevel: MutableStateFlow<Int>  ──►  micLevel: StateFlow<Int>
  │                                     │
  │                                     ├─ ConversationViewModel.micLevel（直接透传）
  │                                     └─ ConversationScreen → BottomToolbar(micLevel = ...)
  ▼
MicVolumeGlyph(flow).collectAsState()
  │  4 条横线自下而上点亮；颜色 Spring 动画
  ▼
工具栏 Mic 图标
```

电平计算是采集读循环的旁路：它读取同一帧 PCM，但不参与 `SilenceFlushGate` 的发送/补静音/丢弃决策，也不改变上行帧内容。

### 1.2 关键事实

- 固定 4 段，取值 `0..4`；`0` = 无点亮段，`4` = 四段全亮。
- 电平在 `AudioCaptureController` 内完成量化与平滑，对外只暴露整数 `StateFlow<Int>`。
- `micLevel` 不进入 `ConversationUiState`，由 `ConversationViewModel` 原样透传；UI 在叶子组件 `MicVolumeGlyph` 内 collect，避免约 50 次/秒的更新驱动整屏重组。
- UI 的 Mic 开关本质是**静音开关**：前台关闭 Mic 仍继续读取麦克风数据，只在上行侧补发/丢弃（后台静音挂起会释放采集，见 §3.4）；muted 帧的电平强制为 `0`，且工具栏不再渲染电平图标。
- 电平是纯视觉信息，TalkBack 不播报当前段数。

---

## 2. 电平算法：`MicLevelMeter`

### 2.1 API 与参数

`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/media/MicLevelMeter.kt`：

```kotlin
class MicLevelMeter(
    private val levels: Int = LEVELS,
    private val floorDb: Float = -45f,
    private val ceilDb: Float = -8f,
    private val releasePerFrame: Float = 0.85f,
) {
    companion object {
        const val LEVELS = 4
    }

    fun onPcmFrame(frame: ShortArray, length: Int, muted: Boolean): Int
    fun reset()
}
```

| 参数 | 当前值 | 含义 |
|---|---:|---|
| `levels` | `MicLevelMeter.LEVELS` = `4` | 量化段数；无参构造时与 UI 横线条数一致 |
| `floorDb` | `-45f` | 归一化窗口下限，达到或低于此值映射为 0 |
| `ceilDb` | `-8f` | 归一化窗口上限，达到或高于此值映射为 1 |
| `releasePerFrame` | `0.85f` | 慢放系数；每帧向 0 衰减 15% |

生产代码只使用无参构造 `MicLevelMeter()`，因此实际参数就是上表默认值；UI 的横线条数直接读取 `MicLevelMeter.LEVELS`，而不是构造参数。类为纯 Kotlin，不依赖 Android API。

### 2.2 单帧计算

每次读循环成功读到 PCM 后调用一次 `onPcmFrame`。传入的 `length` 是本次 `AudioRecord.read()` 的实际样本数；正常情况下读帧缓冲为 `sampleRate * 20 / 1000` 样本（16 kHz 时 320 样本，即 20 ms）。

处理顺序：

1. **静音/空帧短路**：`muted == true` 或 `length <= 0` 时，将内部平滑值清零并返回 `0`。
2. **整帧 RMS**：
   ```text
   sum = Σ(frame[i]²)          // Long 累加
   rms = sqrt(sum / length)    // Double
   ```
3. **转 dBFS**：
   ```text
   db = 20 * log10(max(rms, 1.0) / 32768.0)
   ```
   `max(rms, 1.0)` 用于避免 `log10(0)`；全零帧因此落到约 `-90 dBFS`，低于窗口下限。
4. **窗口归一**：
   ```text
   norm = clamp((db - floorDb) / (ceilDb - floorDb), 0, 1)
   ```
   `floorDb = -45`、`ceilDb = -8` 时，窗口宽度为 37 dB。
5. **快攻慢放**：
   ```text
   smoothed = if (norm >= smoothed) norm else smoothed * releasePerFrame
   ```
   输入变大时立即跟随（单帧可到顶）；输入变小时按帧衰减。
6. **量化**：
   ```text
   level = round(smoothed * levels).coerceIn(0, levels)
   ```

### 2.3 平滑与量化对应关系

`smoothed` 是 `[0, 1]` 的连续值，经 `round(smoothed * 4)` 后输出 0..4：

| 输出段数 | `smoothed` 区间（近似） |
|---:|---|
| 0 | `[0, 0.125)` |
| 1 | `[0.125, 0.375)` |
| 2 | `[0.375, 0.625)` |
| 3 | `[0.625, 0.875)` |
| 4 | `[0.875, 1.0]` |

行为特征：

- **快攻**：单帧满幅信号即可从任意值升到 4；不设置攻击时间常数。
- **慢放**：静音帧把 `smoothed` 每帧乘以 0.85。从满值 1.0 开始，约第 13 帧（260 ms）落到 0 段；单测允许 11..15 帧的浮点/量化误差。
- **muted 立即归零**：`muted` 不是按 release 衰减，而是直接把 `smoothed` 清零并返回 0；发生 muted 帧后，unmute 从 0 重新攻击。
- 输出只取整数，`AudioCaptureController` 的 `StateFlow` 因此能在稳态去重，不会以浮点噪声持续触发重组。

### 2.4 状态、线程与复位

- 唯一可变状态是 `private var smoothed: Float`，初值 `0f`。
- `reset()` **只清内部平滑值，不修改任何外部 `StateFlow`**。调用方需要同时把 `_micLevel.value` 置 0；当前 controller 的所有显式复位点都成对调用。
- `onPcmFrame` 正常由读帧协程在 `Dispatchers.IO` 调用；`reset()` 也会随 `stop()` 在调用方线程调用。实现不加锁，依赖单 Float 的原子读写与后续帧覆盖；controller 另有活跃守卫和循环退出归零来收敛竞态。
- 算法本身不读取系统音量、不读取麦克风增益、不做 AGC；它只针对 `AudioRecord` 返回的原始 `ShortArray` 计算。

---

## 3. 采集集成：`AudioCaptureController`

### 3.1 对外状态

`AudioCaptureController` 持有：

```kotlin
private val levelMeter = MicLevelMeter()
private val _micLevel = MutableStateFlow(0)
val micLevel: StateFlow<Int> = _micLevel.asStateFlow()
```

对外契约：

- 取值恒为 `0..MicLevelMeter.LEVELS`，当前即 `0..4`。
- 已由 controller 完成量化与平滑；消费者不需要再做归一化或动画曲线。
- muted 帧、停止、挂起、读错误耗尽后为 `0`。
- `StateFlow` 等值去重；只有段数变化时才发射。
- 任意线程可读；写入主要来自读帧协程，生命周期复位点也会写。

### 3.2 读帧循环插入点

`startInternal()` 每次启动/重建采集时按当前采样率创建 20 ms 读缓冲：

```kotlin
val frameSamples = currentSampleRate * 20 / 1000
```

读循环中的顺序（简化）：

```kotlin
while (isActive && _isCapturing.value) {
    val read = audioRecord?.read(frame, 0, frameSamples) ?: -1
    if (read < 0) {
        handleReadError(..., gen)
        return@launch
    }
    if (isActive && _isCapturing.value) {
        _micLevel.value = levelMeter.onPcmFrame(frame, read, _isMuted.value)
    }
    when (flushGate.decide(_isMuted.value)) {
        SEND_REAL    -> { dump?.write(frame, read); sdk.pushAudioPcm(frame, 0, read) }
        SEND_SILENCE -> sdk.pushAudioPcm(zeroFrame, 0, frameSamples, muted = true)
        DROP         -> Unit
    }
}
_micLevel.value = 0
levelMeter.reset()
```

要点：

- 电平在成功读取之后、`flushGate.decide()` 之前计算；计算只取 `_isMuted`，不看 `decide()` 的分支。未静音帧走 `SEND_REAL` 时反映真实拾音；muted 帧即使进入 `SEND_SILENCE`/`DROP` 也强制为 0。
- 传给 `onPcmFrame` 的是本次 `read` 的实际长度，而不是固定 `frameSamples`；短读时只计算已读样本。
- 活跃守卫 `isActive && _isCapturing.value` 避免 `stop()` 在阻塞式 `read()` 期间执行后，在途帧把已归零的电平重新写成残留值。
- 电平是旁路，不触碰 `flushGate`、PCM dump 或上行帧数据。

### 3.3 静音与隐私语义

`setMuted(muted)` 只更新 `_isMuted.value`，不直接写 `_micLevel`，也不停止读循环：

```kotlin
fun setMuted(muted: Boolean) {
    _isMuted.value = muted
}
```

因此：

- mute 从**下一次成功读帧**开始生效：`onPcmFrame(..., muted = true)` 返回 0 并清平滑态；`setMuted` 本身不会立即把当前值清零。
- 前台 muted 期间 `AudioRecord` 仍在读取真实麦克风数据；电平强制为 0 是隐私语义，而不是“没有采集”。
- `flushGate` 的静音补发窗口独立处理上行：mute 后先补发有限零帧，再丢弃；电平不等待该窗口，muted 帧直接为 0。
- unmute 后，若 muted 期间至少经过一次读帧，`smoothed` 已被清零，下一帧从 0 重新攻击；若 mute 与 unmute 落在同一读帧间隔内、没有 muted 帧经过 meter，则会保留原平滑值。
- Mic 按钮关闭（`isMicOn = false`）对应 `isMicMuted = true`；UI 直接换成标准 `MicOff`，不再显示电平表，因此即使内部 `StateFlow` 在下一帧前仍有旧值，也不可见。

### 3.4 归零与生命周期

| 场景 | `_micLevel` | 内部 `smoothed` | 说明 |
|---|---|---|---|
| 初始/未采集 | `0` | `0` | `MutableStateFlow(0)` 初值 |
| `stop()` / `teardownCapture()` | 立即 `0` | `reset()` | 停采集、取消读协程、释放 AudioRecord、停 Mic FGS |
| 后台挂起（采集且 muted） | 立即 `0` | `reset()` | `topUpMuteFlushWindow()` 后走同一 `teardownCapture()` |
| 读循环正常退出 | `0` | `reset()` | 循环结束后的终态归零；覆盖取消/停止的补充保证 |
| 读帧错误重试（第 1..3 次） | 不主动归零 | 不重置 | 旧循环经 `return@launch` 退出，跳过循环末尾归零；新循环复用同一 `MicLevelMeter`，电平随新帧继续收敛 |
| 读错误耗尽（进入死态） | `0` | `reset()` | 停读协程、关 dump、停 Mic FGS |
| 切采集源 / 切输入设备重建 | 重建序列本身不写 | 重建序列本身不 reset | `restartCaptureLocked` 不调用 `teardownCapture`；新循环每帧仍以当前 `_isMuted` 判定，复用同一 meter |
| mute | 下一成功读帧变 `0` | 清 `0` | `setMuted` 不立即写；UI 同时切到 `MicOff` |
| unmute | 下一成功读帧重新计算 | 经过 muted 帧则从 `0`；否则保留原值 | 不恢复 mute 前残留的前提是 muted 期间至少读过一帧 |

补充说明：

- `reset()` 不清 `_micLevel`；表中“归零”均指 controller 同时执行 `_micLevel.value = 0` 与 `levelMeter.reset()`。
- 读错误重试刻意保留平滑态：重建期间不闪断，重试成功后表从上次值继续衰减/攻击；只有重试耗尽才归零。
- 切源/切设备走 `restartCaptureLocked`，它本身不归零；旧读循环取消后的退出路径取决于在途 `read()` 的返回。若旧循环走到 `while` 末尾，仍会执行一次终态归零，因此重建后的首帧电平不承诺延续，随后以新帧为准。
- 后台仅在“采集中且 muted”时释放麦克风；后台 unmuted 继续采集，电平也继续更新（但此时通常无页面可见）。

### 3.5 与上行旁路的关系

- **`SilenceFlushGate`**：电平使用 `_isMuted.value` 判断，而不是 `decide()` 的返回分支；因此 `SEND_SILENCE` 和 `DROP` 帧不会因为上行被替换/丢弃而改变电平语义。
- **`PcmDump`**：dump 只在 `SEND_REAL` 分支写入真实拾音；muted 帧不落盘。电平与 dump 共享“muted 不展示真实拾音”的隐私边界，但电平计算本身不依赖 dump 是否开启。
- **上行 AGC/编码**：电平基于 `AudioRecord` 读出的 PCM，在 `sdk.pushAudioPcm` 之前计算；它不代表 Opus 编码或增益后的服务端信号。

---

## 4. ViewModel 与页面接线

### 4.1 `ConversationViewModel.micLevel`

`ConversationViewModel` 直接暴露 controller 的 flow：

```kotlin
val micLevel: StateFlow<Int> = audioCapture.micLevel
```

这不是 `ConversationUiState` 字段，原因：

- 电平约每 20 ms 可能变化一次；如果并入 `ConversationUiState` 并参与页面级 `combine`/`copy`，会驱动整个会话页重组。
- 独立 flow 让只有真正 collect 它的 `MicVolumeGlyph` 重组。
- controller 已量化为 0..4，`StateFlow` 等值去重进一步降低稳定期的发射频率。

### 4.2 `ConversationScreen` → `BottomToolbar`

`ConversationScreen` 在工具栏调用处传入：

```kotlin
BottomToolbar(
    // ...
    isMicOn = !uiState.isMicMuted,
    micLevel = viewModel.micLevel,
)
```

`BottomToolbar` 的签名为可选参数：

```kotlin
micLevel: StateFlow<Int>? = null
```

默认 `null` 让预览和其他调用点无需提供电平；正式会话页始终传入 `viewModel.micLevel`。

### 4.3 重组边界

- `BottomToolbar` 只传递 `StateFlow` 引用，不读取 `flow.value`；电平 tick 不会触发它重组。
- 真正的 `collectAsState()` 在 `MicVolumeGlyph` 函数体内；电平 tick 的重组范围是该 Canvas 图标自身。
- `BottomToolbar` 随传入的页面状态（如 `isMicOn`、设备列表）变化重组，但不会随每帧电平重建整排按钮。

---

## 5. UI：`BottomToolbar` 与 `MicVolumeGlyph`

### 5.1 显隐与回退

`BottomToolbar` 的 Mic 按钮根据 `isMicOn` 和 `micLevel` 选择图标：

| `isMicOn` | `micLevel` | 渲染 |
|---|---|---|
| `true` | 非 `null` | `MicVolumeGlyph(flow)`，话筒头内显示 4 段电平 |
| `true` | `null` | 标准 `Icons.Filled.Mic` |
| `false` | 任意 | 标准 `Icons.Filled.MicOff`；不创建 glyph 节点 |

当使用 glyph 时，外层 `Box` 设置 `contentDescription = micCaption`，让自绘图标仍能被无障碍服务识别为 Mic 按钮；glyph 自身不提供内容描述。Mic 关闭时使用标准 `MicOff`，图标和说明文字仍保持原有工具栏布局。

### 5.2 视觉结构

`MicVolumeGlyph` 在 24dp Canvas 上按 dp 坐标自绘，绘制前整体按屏幕密度缩放：

```text
        ╭─────────╮   ← 话筒外圈：圆角胶囊描边
        │  ▬▬▬▬▬  │   ← 4 条横线（自下而上点亮）
        │  ▬▬▬▬▬  │
        │  ▬▬▬▬▬  │
        │  ▬▬▬▬▬  │
        ╰─────────╯
             │         ← 中心竖线延伸
          ╱─────╲      ← Material mic 底座（bell path）
```

组成部分：

1. **话筒外圈**：圆角矩形描边，包住 4 条横线。
2. **4 条横线**：等宽、等高的圆角横条，自下而上点亮。
3. **话筒底座**：Material `MIC_BELL_PATH` 填充，整体下移 `BELL_DY`。
4. **中心竖线**：在底座中心竖线的末端继续向下延伸，与底座形成一条长竖线。

当前几何常量（24dp 视口）：

| 部件 | 常量与值 |
|---|---|
| 外圈 | `RING_LEFT = 7.8f`、`RING_RIGHT = 16.2f`、`RING_TOP = 0.5f`、`RING_BOTTOM = 13.6f` |
| 外圈圆角/线宽 | `RING_CORNER_RADIUS = 2.5f`、`RING_THICKNESS = 1.7f` |
| 横线 | `BAR_START_Y = 2.2f`、`BAR_HEIGHT = 1.9f`、`BAR_GAP = 0.9f` |
| 横线宽度/圆角 | `BAR_WIDTH = 5.8f`、`BAR_CORNER_RADIUS = 0.8f` |
| 底座位移 | `BELL_DY = 0.9f` |
| 中心竖线 | `STEM_TOP_Y = 21.4f`、`STEM_BOTTOM_Y = 23.1f`、`STEM_WIDTH = 1.7f` |

横线中心固定在 `x = 12f`；第 `i` 条（从上往下 `i = 0..3`）的 `top = BAR_START_Y + i * (BAR_HEIGHT + BAR_GAP)`。这些常量是 24dp 视口内的观感微调点，不参与电平算法。

### 5.3 点亮规则与动画

- `barCount = MicLevelMeter.LEVELS`，当前为 4。
- 输入电平先 `coerceIn(0, barCount)`，得到 `lit`。
- 横线索引从上到下为 `0..3`；第 `i` 条点亮条件为：
  ```text
  i >= barCount - lit
  ```
  因此 `lit = 1` 只亮最底一条；`lit = 4` 四条全亮。
- 点亮色为 `BrandIndigo`，未点亮色为 `SurfaceVariant`。
- 每条横线独立使用 `animateColorAsState`，动画为 `spring(dampingRatio = 0.5f)`；只有颜色渐变，不改变横线高度。
- 外圈、底座和中心竖线始终使用 `BrandIndigo`，不随电平变暗。因此 `level = 0` 时仍然能看到完整的麦克风轮廓，只是 4 条横线全部为 `SurfaceVariant`。

### 5.4 语义与无障碍

`Canvas` 根节点使用：

```kotlin
clearAndSetSemantics {
    testTag = "micVolumeGlyph"
    set(LevelKey, lit)
}
```

- `LevelKey = SemanticsPropertyKey<Int>("MicVolumeGlyphLevel")`，仅供测试断言当前点亮段数。
- glyph 清除子节点语义并只保留测试键；不向 TalkBack 暴露“当前音量 0..4”或逐帧变化。
- 外层 `BottomToolbar` 的 `Box` 提供 `contentDescription = micCaption`，所以无障碍用户听到的是 Mic 按钮的开关文案，而不是电平。
- 由于 glyph 语义被合并进 `IconButton`，仪器测试需要用 `useUnmergedTree = true` 定位 `micVolumeGlyph` 节点。

---

## 6. 行为速查与边界

### 6.1 状态到视觉的映射

| 状态 | `micLevel` | 工具栏 |
|---|---|---|
| 会话未启动 / 采集停止 | `0` | 若 Mic 仍为开启态，显示 glyph；外圈/底座为主色，4 条横线全暗 |
| Mic 开启、持续安静 | `0` | glyph，4 条横线全暗 |
| Mic 开启、正常说话 | 通常 `1..3` | glyph，自下而上点亮对应条数 |
| Mic 开启、大声/满幅 | `4` | glyph，4 条全亮 |
| Mic 关闭（muted） | 下一次读帧后 `0` | 标准 `MicOff`，不显示 glyph |
| 后台且 muted 挂起 | `0` | 采集已释放；无页面可见 |
| 读错误重试中 | 保留/继续变化 | 若 Mic 开启，glyph 继续显示；短暂停顿后随新帧恢复 |
| 读错误耗尽（采集死态） | `0` | Mic 开关状态未必变化；若仍开启，glyph 只剩轮廓，无点亮条 |

### 6.2 已知限制

1. **固定窗口，无用户校准**：`-45..-8 dBFS` 与 `releasePerFrame = 0.85` 是编译期默认值；不同 OEM 的麦克风增益会让同样的说话音量落到不同段数。
2. **mute 不是立即写 0**：`setMuted(true)` 只更新状态，内部 flow 要等下一次成功读帧才归零；UI 在 Mic 关闭后不再渲染 glyph，因此这段延迟通常不可见。
3. **静音不等于停止采集**：前台 Mic 关闭（未触发后台挂起）时仍在读取真实麦克风数据，只是上行补静音/丢弃、电平强制为 0。隐私边界依赖 `_isMuted` 而不是停止 `AudioRecord`。
4. **0 段有多种含义**：安静、muted、采集停止、读错误死态、后台挂起都可能显示 0；电平本身不能区分原因。
5. **采集死态没有独立 UI 通道**：读重试耗尽后会停止采集并把电平归零，但没有独立的错误提示；用户可见的间接信号是电平不跳动/无转写。采集侧错误收敛见 [`android_audio_capture.md`](android_audio_capture.md)。
6. **无 TalkBack 电平播报**：为避免高频播报，`MicVolumeGlyph` 只保留测试语义；无障碍用户只能获知 Mic 开/关。
7. **`reset()` 不负责对外归零**：它只清内部平滑值，单独调用不会让 `micLevel` 变成 0；所有调用方必须同时写 `_micLevel.value = 0`。
8. **电平是视觉近似**：没有声压级校准，也不代表服务端增益/编码后的响度；仅用于输入活动反馈。

---

## 7. 测试

### 7.1 JVM：`MicLevelMeterTest`

纯 JVM，不依赖 Android；使用 320 样本帧验证算法。

| 用例 | 覆盖 |
|---|---|
| `silenceReturnsZero` | 全零帧 → 0 |
| `fullScaleReturnsTopLevel` | 满幅交替 +32767 / -32768 → 4 |
| `quietEnvironmentReturnsZero` | 低于窗口下限 → 0 |
| `normalSpeechLevelsInMiddleRange` | 正常语音幅度 → 中间段（2..3） |
| `mutedForcesZeroAndClearsSmoothing` | muted 强制 0，且清除平滑态，unmute 从 0 起跳 |
| `fastAttackHitsTopOnSingleLoudFrame` | 单帧满幅立即到 4 |
| `slowReleaseDecaysMonotonicallyAndZeroesWithinHalfSecond` | 静音后单调衰减，约 13 帧内归零 |
| `emptyFrameReturnsZero` | `length = 0` → 0 |
| `resetClearsSmoothedState` | `reset()` 清平滑态 |

### 7.2 仪器/UI：`BottomToolbarTest`

| 用例 | 覆盖 |
|---|---|
| `micVolume_glyph_exposes_semantic_level` | flow=3 时 `micVolumeGlyph` 存在，`LevelKey = 3` |
| `micVolume_glyph_updates_are_collected` | flow 从 3 更新到 4，glyph 语义同步更新 |
| `micVolume_glyph_hidden_when_mic_off` | Mic 关闭时无 glyph，显示标准 `MicOff` |
| `micLevel_null_falls_back_to_standard_icon_and_keeps_captions_aligned` | Mic 开启但 flow=null → 标准 Mic 图标，且三列 caption 对齐 |

### 7.3 门禁

在 `apps/android` 下：

```bash
./gradlew :LivaChatApp:testDebugUnitTest
./gradlew :LivaChatApp:assembleDebug
```

`androidTest` 需在真机/模拟器编译执行；电平算法为纯 JVM，本地单测即可覆盖主要边界。

---

## 8. 文件索引

### 8.1 实现

| 文件 | 职责 |
|---|---|
| `media/MicLevelMeter.kt` | RMS → dBFS → 归一 → 快攻慢放 → 0..4 量化；`LEVELS = 4` |
| `media/AudioCaptureController.kt` | 读帧循环调用 meter、`micLevel` StateFlow、mute/停止/重试复位 |
| `ui/screens/conversation/ConversationViewModel.kt` | `micLevel` 直接透传 |
| `ui/screens/conversation/ConversationScreen.kt` | 将 `viewModel.micLevel` 传给工具栏 |
| `ui/components/BottomToolbar.kt` | `micLevel` 参数、Mic 开关显隐、标准图标回退 |
| `ui/components/MicVolumeGlyph.kt` | 24dp 话筒图标、4 段横线、颜色动画、测试语义 |

### 8.2 测试

| 文件 | 职责 |
|---|---|
| `test/.../media/MicLevelMeterTest.kt` | 纯 JVM 算法边界 |
| `androidTest/.../ui/components/BottomToolbarTest.kt` | glyph 语义、更新、Mic 关闭隐藏、空 flow 回退 |
