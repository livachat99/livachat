# iOS 麦克风输入音量段表（Mic Meter）分析与设计

**状态**：已实施（2026-10-24；算法、接线、测试与 UI 已落地；2026-10-25 修正 unmute 组合测试与文档一致性，并修复 `removeTap` flush 与 tap 线程 `controlLock` 的死锁）
**日期**：2026-10-24

> 注：本文中 `startMedia`/`stopMedia`/`mediaStarted` 是后台音频重构前的名称；当前实现使用 `pipelineState`/`reconcileMedia()`/`teardownMedia()`。R4 起后台策略为：**mic 未静音保持 full（采集+播放），mic 静音先 topUp 再 `teardownMedia()` 并释放麦克风**。§2.2 记录实施前状态；当前实现以代码与 §4、§5 为准。
**范围**：iOS 会话页底部 toolbar 的 Mic 按钮，对齐 Android `BottomToolbar` / `MicVolumeGlyph`；包含 unmute 时 pending PCM 的隐私边界修复，不改上行协议
**上游参考**：
- `docs/android_mic_meter.md`（Android 实现）
- Android：`media/MicLevelMeter.kt`、`media/AudioCaptureController.kt`、`ui/components/MicVolumeGlyph.kt`、`ui/components/BottomToolbar.kt`
- iOS：`Core/Audio/MicLevelMeter.swift`、`Core/Audio/AudioCaptureController.swift`、`Features/Conversation/ConversationView.swift`

> 本文所有结论均来自当前代码或已有文档；不确定项单独列在 §7，不做默认假设。

---

## 1. 目标与非目标

### 1.1 目标

1. Mic 开启时，在 Mic 图标内显示 Android 同款 **4 段输入电平表**（3 段仅为真机验收失败后的备选）：
   - 4 条横线自下而上点亮；
   - 点亮色 `brandIndigo`，未点亮 `surfaceVariant`；
   - Mic 关闭时显示 `mic.slash.fill`，不渲染电平表。
2. 电平算法与 Android 对齐：RMS → dBFS → `[-45, -8] dB` 窗口归一 → 快攻慢放 → 4 段量化。
3. 高频电平更新 **只驱动图标叶子节点**，不触发 `ConversationView`/`ConversationViewModel` 整页重绘。
4. muted、显式 stop、restart、重试耗尽均归零/不残留；采集中途重试也明确归零，避免无采集期间冻结旧电平。后者是与 Android「短暂保持」的有意差异，见 §4.5。
5. 保持现有 toolbar 视觉：透明圆形、激活态无描边、图标 `brandIndigo`、caption `textSecondary`、按压 0.92。
6. unmute 时丢弃共享 chunker 中不足 320 样本的 pre-unmute pending：既避免电平混合帧，也避免这些真实拾音在 unmute 后作为 real PCM 上行。此外，unmute 增代会丢弃已开始回调的旧 tap，并用 one-shot token 丢弃首个 post-edge callback；两者共同构成 best-effort 上行隐私边界，可能连续丢弃两个 buffer，见 §4.3。不改变 mute 后 50 帧静音补发窗口语义，不改变上行协议。

### 1.2 非目标

- 不改 SDK、音频上行协议、服务端；
- 不做多色 VU（绿/黄/红）、不做独立电平条/外环；
- 不把电平值加入 VoiceOver 播报；
- 不改 Android 实现；
- 不在本设计阶段实现代码。

---

## 2. Codebase 事实

### 2.1 Android 现状（对齐基准）

`media/MicLevelMeter.kt`：

- `LEVELS = 4`；
- 每 20ms / 320 样本帧调用一次 `onPcmFrame(frame, length, muted)`；
- RMS → `20*log10(rms/32768)` → `[-45, -8] dB` 归一；
- 快攻慢放：`smoothed = norm >= smoothed ? norm : smoothed * 0.85`（每帧 20ms；是 attack/release，不是 `max`）；
- 量化：`round(smoothed * 4)`，返回 `0..4`；
- muted 或空帧：`smoothed = 0`，返回 0；
- `reset()` 清平滑态。

`media/AudioCaptureController.kt`：

- 读帧循环每 20ms 读 320 样本，`_micLevel.value = levelMeter.onPcmFrame(...)`；
- `micLevel: StateFlow<Int>`，**不进入** `ConversationUiState`/大 combine；
- muted/停止/重试耗尽强制 `_micLevel.value = 0`；
- UI 通过 `BottomToolbar(micLevel = viewModel.micLevel)` 传递，`MicVolumeGlyph` 在叶子节点 `collectAsState()`。

`ui/components/MicVolumeGlyph.kt`：

- 24dp 视口自绘；
- 话筒头：4 条横线（宽 5.8、高 1.9、间隙 0.9、起点 y=2.2），自下而上点亮；
- 话筒外圈：圆角胶囊描边，x 7.8..16.2、y 0.5..13.6、圆角 2.5、线宽 1.7；
- 话筒下半：material mic bell path（`MIC_BELL_PATH`），整体下移 0.9，主色填充；
- 中心竖线：宽 1.7，y 21.4..23.1；
- 点亮 `BrandIndigo`，未点亮 `SurfaceVariant`；
- 仅 mic 开启时渲染；关闭渲染标准 MicOff。

`ui/components/BottomToolbar.kt`：

- active 实际视觉为 **无填充、无描边、icon tint = primary**；`containerColor` 计算后未使用（文档 §4.4 明确）；
- caption 恒 `TextSecondary`，只取首个词；
- `ToolbarButton` 有 `iconContent` 槽，Mic 用它渲染 `MicVolumeGlyph`。

### 2.2 iOS 实施前现状（历史，供对照）

> 本节记录 2026-10-24 实施前的代码状态，不代表当前实现；当前实现见 §4、§5 与源码。

`Core/Audio/MicLevelMeter.swift`：

- `struct MicLevelMeter`；
- 输入 `[Int16]`，输出 **Float 0...1**；
- 曲线 `-80...0 dB` 映射；
- 平滑系数：attack 0.5 / release 0.1；
- muted/空帧归零并清平滑态；
- 当前 4 条单测只断言 Float 行为（`>0.9` 等）。

`Core/Audio/AudioCaptureController.swift`：

- `AudioFormats.capture` 为 **16kHz mono Int16**；
- tap `bufferSize: 1024`，按输入采样率转换到目标格式；
- `process(samples:)` 当前在 **chunker 之前** 对整个转换 buffer 调用 `meter.update(samples:muted:)`；
- `PCMChunker` 以 **320 样本 = 20ms** 切帧，用于上行；
- 电平 UI 上报有 **50ms 节流**（`lastLevelReport`，约 20Hz），通过 `Task { @MainActor }` 调 `onLevel`；
- `onLevel` 类型是 `@MainActor (Float) -> Void`；
- `setMuted(true)` 立即 `reportLevel(0)`；`stop()` 立即 `reportLevel(0)`；
- `performStartAttempt`/`teardownLocked` 调用 `meter.reset()`。

`Core/Conversation/ConversationViewModel.swift`：

- `micLevel: Float` 当前已是 **非 `@Published`**（上一提交 de09f0e），无 UI 消费者；
- `setMicLevel(_ level: Float)` 做 0...1 clamp 与 0.005 去抖；
- `ConversationView` 是 `@ObservedObject var viewModel`。

`Core/Conversation/ConversationCoordinator.swift`：

- `installAudioHooks` 中 `capture.onLevel = { viewModel.setMicLevel($0) }`；
- `toggleMic` mute 时额外 `viewModel.setMicLevel(0)`。

`Features/Conversation/ConversationView.swift`：

- Mic/Speaker 共用 `toolbarButton(systemImage: caption: active: identifier: action:)`；
- 当前激活态为透明圆形 + `brandIndigo` 图标，无电平表；
- Mic 关闭使用 `mic.slash.fill` + `textSecondary`；
- 48pt 圆形，caption 12pt `textSecondary`；
- 图标状态用 `.transaction { $0.animation = nil }` 即时切换。

### 2.3 差异结论

| 维度 | Android | iOS 实施前 | 设计需要 |
|---|---|---|---|
| 采样格式 | 16kHz PCM16 | 16kHz PCM16（转换后） | 可直接复用算法 |
| 电平窗口 | -45...-8 dB | -80...0 dB | 改为 Android 窗口 |
| 平滑 | 每 20ms 帧，快攻慢放 0.85 | 每 tap，attack .5/release .1 | 改为每 320 样本帧、0.85 |
| 输出 | Int 0..4 | Float 0...1 | 改为 Int 0..4 |
| UI | 4 条横线融入 mic 图标 | 无电平表 | 新增 `MicVolumeGlyph` |
| 更新通道 | StateFlow → 叶子 collect | `onLevel` → ViewModel 普通属性 | 新增叶子 `MicLevelModel` |
| 高频重绘 | 不进 uiState | 实施前无 | 确保只重绘 glyph |

> iOS 已有 `PCMChunker(frameSamples: 320)`，与 Android 的 20ms 帧完全一致；因此可以在 chunker 输出上计算电平，**不需要猜测 tap 的实际采样率/帧率**。

---

## 3. 4 段 vs 3 段

### 3.1 4 段（推荐）

理由（全部来自 codebase）：

1. Android 的事实源是 `MicLevelMeter.LEVELS = 4`；`MicLevelMeterTest` 的满幅断言、muted 残留断言都按 4 段设计。
2. Android `MicVolumeGlyph` 的 24dp 几何就是 4 条横线（高 1.9、间隙 0.9），已在真机三轮评审通过；iOS glyph 可按 24pt 同几何复刻。
3. 4 段提供更细的反馈，且不会改变 Android 现有测试/语义；3 段需要重新调几何与测试，没有 codebase 驱动。
4. iOS 现有 Mic 图标已经放在 48pt 圆内，24pt glyph 不存在额外空间约束；但实施前 SF Symbol 未显式固定 24pt frame，不能预设新旧图标的视觉尺寸完全一致，需以截图/真机验收。

### 3.2 3 段（备选）

- 优点：单段更大，24pt 下更容易看清；
- 代价：与 Android 的 `LEVELS=4`、几何常量、测试用例不一致；需要单独调 bar 高度/间隙和量化阈值；
- 触发条件：真机上 4 条横线在 24pt 下确实不可辨（当前无证据；Android 24dp 已验证可辨）。
- 结论：默认 4 段；3 段仅作为真机视觉验收失败后的 fallback，不进入默认实现。

---

## 4. 方案设计

### 4.1 MicLevelMeter（iOS，Android 直接移植）

已实现 API：

```swift
struct MicLevelMeter {
    static let levels = 4

    mutating func update(samples: [Int16], muted: Bool) -> Int   // 0...levels
    mutating func reset()
}
```

实现要点（与 Android `MicLevelMeter.kt` 一致）：

1. `muted || samples.isEmpty` → `smoothed = 0`，返回 0；
2. 整帧 RMS：
   ```swift
   sum += Double(sample) * Double(sample)
   rms = sqrt(sum / Double(count))
   ```
3. `db = 20 * log10(max(rms, 1) / 32768.0)`；
4. 窗口归一：`norm = ((db + 45) / 37).clamped(0...1)`（`-45...-8`，跨度 37）；
5. 快攻慢放：`smoothed = norm >= smoothed ? norm : smoothed * 0.85`；不能写成 `max(norm, smoothed * 0.85)`——当 `norm` 介于 `smoothed*0.85` 与 `smoothed` 之间时，两者结果不同（Android 走 release，`max` 会跟随 `norm`）；
6. 量化：`Int((smoothed * Float(levels)).rounded()).clamped(0...levels)`；
7. `reset()` 清 `smoothed`。

> 输入必须是 **320 样本帧**（`PCMChunker` 的 20ms 帧），而不是整个转换 buffer；这样 release 0.85/帧的语义与 Android 完全一致，不依赖 iOS tap 的实际帧率。

### 4.2 AudioCaptureController 接入

接入实现（已落地）：

1. `onLevel` 类型已由 `(@MainActor (Float) -> Void)?` 改为 `(@MainActor (Int) -> Void)?`（`AudioProtocols.swift` 同步）。
2. `process(samples:tapGeneration:)`：
   - `handleTap` 在初始 `lock` 临界区内捕获 `levelGeneration`，转换完成后传给 `process`；`process` 在锁内若发现 `tapGeneration != levelGeneration`（mute/unmute/stop/restart/recovery 都会增代）则整 buffer 丢弃，不进入 chunker/meter/上行。转换在锁外进行；若不校验，mute 期间开始转换、unmute 后才进入 `process` 的 buffer 会被当作 post-unmute real PCM 上行。
   - 在 `lock` 内先读取当前 `muted` 和 `levelGeneration`，再 `let chunked = chunker.append(samples)`；
   - 对每个 20ms chunk 调用 `meter.update(samples: muted:)`；
   - 保存最后一个 chunk 的结果到 `lastMeterLevel`（无完整 chunk 时保持上一个值）；
   - 现有 `lastLevelReport >= 0.05` 节流保留（约 20Hz），上报 `lastMeterLevel` + 捕获的 `levelGeneration`；
   - **不要**对整个转换 buffer 调 meter（旧实现会因 tap 帧率不同导致平滑节奏与 Android 不一致）。
   - **uplink 阶段同样必须校验 generation**：`process` 在 `lock` 内生成 `outgoing` 并捕获 generation，解锁后（可选测试 hook）在专用 `uplinkLock` 内检查 `generation == uplinkGeneration` 才逐帧调用 `uplink`。`resetLevelLocked` 在递增 `levelGeneration` 的同一临界区内获取 `uplinkLock` 并更新 `uplinkGeneration`；因此 mute/stop/restart 要么等待在途 uplink 完成后才返回（旧帧在此之前已投递），要么让尚未开始的旧帧在 generation 校验处被丢弃。只在 uplink 前再查一次 generation 只能缩小窗口，不能关闭“生成 outgoing → 实际投递”的竞态。
   - 测试缝：`beforeUplink`（生产默认 no-op）在解锁后、`uplinkLock` 之前调用，用于暂停 tap 线程并插入 mute/stop/restart，见 §6.3。
   - `uplink` 闭包不得回调 capture controller（否则 `uplinkLock` 与 `lock` 可能死锁）；当前实现只调用 SDK/播放层，满足该约束。
   - **转换错误的 recovery/terminal 迁移必须原子校验来源代次**：`handleTap` 的错误路径先做一次 generation 预过滤，但真正的 `requestConversionRecovery` / `reportPersistentConversionFailure` 必须在 `controlLock → lock` 的同一临界区内复核 `capturing`/`recovering` 与 `originatingGeneration == levelGeneration`，再递增 `startGeneration`、设置 `recovering`、取消旧 retry；否则旧 tap 的错误可以在 stop/start 后进入 recovery，读取并递增新采集的 `startGeneration`，导致新采集被重建或 fatal teardown。
   - **转换错误的 recovery 不得在 tap 线程同步取 `controlLock`**：`AVAudioEngine.removeTap` 会同步 flush 在途 callback；teardown 持有 `controlLock` 时，如果 callback 也同步取 `controlLock`，会与 `removeTap` 形成死锁。`handleTap` 只在 `lock` 下记录 stats、递增 `conversionFailureToken` 并把恢复决策投递到 `controlQueue`；恢复函数在 `controlLock + lock` 内再次校验来源 generation 与 token。成功转换或更新的失败会递增 token，使排队中的旧决策自动失效。因此第三次失败后、排队终止决策执行前若出现成功转换，会取消终止并清零 streak（成功证明转换器已恢复），这是异步投递的明确语义。
   - 成功转换的统计/`conversionFailureStreak` 清零、空转换统计也必须在写入前校验 generation；旧代成功不得擦掉新代已累计的失败。buffer 分配失败的 `onFailure` 携带来源 level generation，迟到的 fatal 不得拆掉 replacement capture。
   - **retry task 的创建与 `recovering` 发布必须原子**：`beginRetries` 必须在持有 `controlLock` 的同一临界区内创建并保存 `retryTask`、设置 `recovering = true`；task 内所有清除 `recovering`/`retryTask` 的路径也必须先取 `controlLock`。否则立即返回的 `retrySleep` 可以让 task 在注册前完成恢复并清除状态，创建线程随后又保存已完成的 task 并重设 `recovering`，在没有任务可再清除的情况下永久停留在 `.recovering`。回归测试用无挂起的 `retrySleep` 连续 start/stop 覆盖该发布竞态。
3. `setMuted(_:)`：
   - 在 `lock` 内更新 `currentMuted`；仅在 muted 状态实际变化时，通过下述统一 reset helper 递增 `levelGeneration` 并建立 0 基线，释放 `lock` 后立即上报**当前代** 0。重复的 `setMuted(true)` 仍可上报当前代 0，但不重复增代或 reset chunker。
   - **仅在 muted true→false 边沿 `chunker.reset()`**：muted 期间可能持有 <320 个 pre-unmute 真实拾音样本。若不 reset，这些样本不仅会混入首个电平帧，还可能在 `gate.decide(muted: false)` 后作为 real PCM 上行。reset 同时修复电平与上行隐私边界，不只是视觉优化。
   - 重复 `setMuted(false)`（每次 full 启动都会调用 `capture.setMuted(viewModel.isMicMuted)`）不得重复 reset chunker，否则会无故丢弃 pending 样本；只有实际 mute 边沿才触发 reset。
   - 仅 `chunker.reset()` 不足以覆盖 unmute 时正在采集的硬件 buffer：`handleTap` 可能在 unmute 之后才开始，但 buffer 内含 mute 期间的音频。unmute 边沿在 `lock` 内设置一次性 `discardNextTapBuffer`；`handleTap` 在转换前、初始临界区内消费该 token 并整 buffer 丢弃，下一次 callback 恢复正常。mute、实际重启与 teardown 清除该 token。
   - **丢音边界是明确的保守策略**：① 若 unmute 时没有 in-flight tap，只丢弃 unmute 后第一个 callback；② 若旧 tap 已捕获旧 generation 并正在转换，旧 tap 会被 generation 校验在进入 real uplink 前丢弃（转换后、`process` 前，或 `process` 内的 uplink generation 校验处），但 token 仍保留，紧随其后的首个 post-unmute callback 又会被 token 丢弃，即最常见的交错会**连续丢弃两个 buffer**（旧 in-flight + 首 post-unmute callback）。generation 过滤会丢弃**所有**在 unmute 前已开始且尚未完成的 callback，因此丢弃的 callback 数是「unmute 前已开始且未完成的 callback 数 + 1」；两个是最常见的交错，不是代码强制上限。生产 tap 请求 1024 frame，单个 callback 在 48kHz 输入下约 21ms、16kHz 下约 64ms；实际 `frameLength` 与可听损失还取决于平台和 in-flight buffer 跨越 unmute 的比例。该策略以隐私优先换取短时丢音，必须由组合测试锁定，不得在未论证的情况下删除 token。
   - **该 token 是 best-effort，不是“任意 pre-unmute 样本绝不泄漏”的强保证**：generation 校验覆盖所有在 unmute 前已开始回调的 tap；one-shot token 只覆盖 unmute 后第一个开始执行的 callback。即使该 callback 没有产生可上行帧（空转换或转换失败），token 也不会顺延到下一个 callback；平台排队多个 callback 时也只覆盖第一个。平台未公开 tap callback 队列深度，无法证明队列中不存在第二个仍含 pre-unmute 音频的 buffer。文档、注释与提交说明不得使用绝对保证措辞；验收以“已开始回调的旧 tap 与首个 post-unmute callback 均不上行”为准。
   - 该 reset 会丢弃不足一个 20ms 帧的 pending muted 数据，属于明确的上行样本边界行为变化；不得改变 `SilenceFlushGate` 的 50 帧静音补发窗口。Android 每 20ms 整帧读取，没有 iOS 这种跨 tap pending 余量；iOS 通过 tap 起点 generation 快照与 unmute 首 callback 丢弃 token 处理跨 mute 沿的硬件 buffer，并明确接受上述 best-effort 边界。
4. 电平重置统一为一个 `lock` 内 helper（名称可实现时确定）：递增一次 `levelGeneration`、执行 `meter.reset()`、设置 `lastMeterLevel = 0`，并在同一临界区内**预留**该 0 基线的 `levelReportSequence`，返回 `(generation, sequence)`；释放 `lock` 后调度新代 0。
   - 实际 mute/unmute、`start` 的实际新启动流程、`stop`/`teardown`、`performStartAttempt` 的每次实际采集重建（含中途重试）都必须经过该 helper；重试耗尽时采集已停止且 meter 必为 0，只需上报当前代 0，不新增 `levelGeneration`，也不递增 `startGeneration`。同一状态迁移或同一 `stop`/`teardown` 路径不得重复增代。
   - **stop 的 no-op 0 也必须在持锁时快照**：当 stop 前既未 capturing 也未 recovering 时，不递增 `levelGeneration`（`startGeneration` 仍递增），但仍需在 `controlLock` + `lock` 内快照 `levelGeneration` 并预留 `levelReportSequence`，解锁后按快照上报。禁止在解锁后重新读取“当前代”再上报，否则旧 stop 可能把 0 标成新 start 的代次并以更高 sequence 覆盖新电平。
   - **不变量：每个新 `levelGeneration` 都必须有当前代 0 基线上报，或由 MainActor 同步 reset `MicLevelModel`。** 不能只增代后丢弃旧 0，否则 UI 可能在下一次有效上报前保留旧非零值。controller 的内部重试不经过 coordinator 的媒体启动/teardown 路径，因此必须自己提供当前代 0。
   - **顺序不变量：0 基线的 sequence 必须与 generation 递增在同一 `lock` 临界区内预留。** 否则 tap 线程可能在 unlock 后、`reportLevel` 分配 sequence 前读取新 generation 并上报非零电平，使该新电平拿到更小的 sequence 而被基线覆盖或拒绝（unmute 后 glyph 空约一个节流周期）。预留后即使基线任务被 generation 过滤，也只会留下无害的 sequence 空洞，不得重置 sequence 或依赖 `Task` FIFO。
   - 采集中途重试采用「立即归零，恢复后从新帧起表」，不保留旧平滑态；这是与 Android 短暂保持电平的有意差异。
5. `reportLevel(_ level: Int, generation: UInt64, sequence: UInt64? = nil)`：
   - 仍用 `Task { @MainActor ... }`，不阻塞音频线程；为确定性测试保留手动调度接缝，见 §6.3。
   - `levelGeneration` 的**所有读写**（包括 MainActor 上的比较）都必须持有同一把 `lock`，或改用明确的原子机制；不得依赖 `@unchecked Sendable` 下的裸 `UInt64` 读写。
   - 每次调度上报时，在同一 `lock` 内递增全局单调的 `levelReportSequence`（0 基线可传入 reset helper 预留的 sequence），任务携带 `generation + sequence + level`。MainActor 在锁内同时检查：generation 必须匹配，且 sequence 必须大于 `lastAppliedLevelReportSequence`；通过后先更新 last-applied，再释放锁调用 `onLevel`。不得在锁内执行 ViewModel/UI 代码。
   - generation 过滤旧代，sequence 保证同代乱序时只应用较新值：当前代 0 基线若晚于同代新电平执行，也必须被丢弃，不得依赖 `Task` FIFO。sequence 不随 generation 重置。
   - 0 基线负责建立新代事实源，与上述过滤缺一不可。`ConversationViewModel.updateMicLevel` 的 muted 检查是额外隐私兜底，不替代该协议。
6. 首版**不增加** controller 侧 `lastReportedLevel` 去重。异步任务可能被 generation 丢弃时，调度前去重会额外引入「已记录但未送达」状态；20Hz 下先保留简单投递，由 `MicLevelModel` 做等值去重和 UI 隔离。

说明：

- `lastLevelReport` 50ms 节流意味着 UI 约 20Hz 更新；Android 最坏 50Hz。4 段离散值下 20Hz 足够；若真机观感偏慢，可将间隔降到 20ms（50Hz），因为叶子模型已保证不会整页重绘。显式 mute/unmute/stop/reset 的 0 基线不受该节流延迟。
- `PCMChunker` 的 pending 余量在 stop/start 已 reset；unmute reset 的上行与隐私语义见上，实施和 review 不得把它当作无副作用的 meter 内部操作。

### 4.3 状态归属与「只重绘叶子」

新增：

```swift
@MainActor
final class MicLevelModel: ObservableObject {
    static let levels = MicLevelMeter.levels
    @Published private(set) var level: Int = 0

    func set(_ value: Int) {
        let clamped = min(max(value, 0), Self.levels)
        guard clamped != level else { return }   // 去重
        level = clamped
    }

    func reset() { set(0) }
}
```

归属与接线：

- `ConversationViewModel` 持有 `let micMeter = MicLevelModel()`（**不是 `@Published`**）。
- `ConversationCoordinator.installAudioHooks`：
  ```swift
  capture.onLevel = { [weak self] level in
      self?.viewModel.updateMicLevel(level)
  }
  ```
- `ConversationViewModel` 新增：
  ```swift
  func updateMicLevel(_ level: Int) {
      // 双保险：controller 的 generation/sequence 已过滤旧上报；这里再按当前 mute
      // 状态兜底，muted 时任何非零电平都归零。
      micMeter.set(isMicMuted ? 0 : level)
  }
  ```
  - `setMicMuted(true)` 时调用 `micMeter.reset()`，这是 mute 的唯一同步归零点；`toggleMic` 不再重复调用 `updateMicLevel(0)`。unmute 由 controller 的当前代 0 基线与既有 mute 重置保证从 0 开始。
  - 媒体生命周期：`ConversationCoordinator.startCaptureAndConfirmEngine()` 在 `capture.start()`/engine 确认后统一调用 `viewModel.resetMicLevel()`（`micMeter.reset()`），覆盖 `startFullMediaFresh()`、`reconfigureFullMedia()` 以及 `transitionToFull()` 中**实际重启 capture engine** 的分支；`teardownMedia()` 在 `capture.stop()` 后单独 reset。不依赖 controller 的异步 0 上报——generation 可能把旧 0 上报丢弃，直接同步 reset 才能保证 stop/restart 后不残留。
  - `transitionToFull()` 的 recovering 早退分支与“engine 已在运行”的健康检查不要 reset：它们不是实际重启，reset 会让每次 foreground/route reconcile 都闪一下 0；由 controller 的当前代 0 基线覆盖。
  - 顺序：先完成 `capture.stop()`/`capture.start()`（产生新一代）再在 MainActor 同步 reset 模型；否则同代在途非零上报可能在 reset 之后落地。`teardownMedia()` 应在 `capture.stop()` 之后 reset。内部采集重试不经过这些方法，由 §4.2 的当前代 0 基线覆盖。
- 删除（或明确弃用）旧的 `micLevel: Float` / `setMicLevel(_:)`，避免两份事实源；相关测试改为断言 `micMeter.level`。
- `ConversationView` 只把 `viewModel.micMeter` **引用**传给 Mic 按钮；不读取 `micMeter.level`。
- `MicVolumeGlyph` 内部 `@ObservedObject var model: MicLevelModel`；只有 glyph 订阅电平变化。
- 这样即使电平以 20Hz/50Hz 更新，`ConversationView`（`@ObservedObject viewModel`）也不会因电平重绘；与 Android「不进 uiState、叶子 collect」等价。

### 4.4 UI：MicVolumeGlyph

新增 `Features/Conversation/MicVolumeGlyph.swift`（仅 Mic 激活时使用）：

- 尺寸：24×24pt，置于现有 48pt 圆形按钮内；不改变按钮/ caption 布局。
- 几何（直接复用 Android 24dp 常量）：
  - 外圈：圆角矩形描边 `x=7.8, y=0.5, w=8.4, h=13.1, corner=2.5, lineWidth=1.7`；
  - 4 条横线：`w=5.8, h=1.9, gap=0.9, x=9.1, y=2.2 + i*2.8, corner=0.8`；
  - 自下而上点亮：bar index `i`（0=顶部）满足 `i >= levels - level` 时点亮；
  - 话筒下半：`MIC_BELL_PATH` + `BELL_DY=0.9`；
  - 中心竖线：`w=1.7, y=21.4..23.1, corner=0.85`。
- 颜色：
  - 点亮：`AppColor.brandIndigo`；
  - 未点亮：`AppColor.surfaceVariant`；
  - 外圈/话筒下半/竖线：`AppColor.brandIndigo`；
  - 背景透明；不使用 `mic.fill` 的实心话筒头。
- 绘制方式：
  - bell path 可用 SwiftUI `Path` 按 Android `MIC_BELL_PATH` 的 24 视口坐标平移；外圈/竖线/横线用 `RoundedRectangle` 或 `Path`。外圈使用中心描边语义的 `.stroke(lineWidth:)`，不要用内缩描边的 `.strokeBorder`，否则与 Android 几何不一致；
  - 横线使用独立 `RoundedRectangle` 视图并加 `.animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: level)`，实现段间颜色过渡。Android 当前是 `spring(dampingRatio = 0.5)`，这里的 120ms ease-out 是有意的 iOS 动画选择，不声称动画参数完全一致；若真机要求进一步对齐，再单独评审 spring 参数；
  - 固定 24pt frame，避免 Dynamic Type 改变图标尺寸；与旧 SF Symbol 的视觉大小差异通过截图验收，不预设完全一致。
- Mic 关闭：继续使用 `Image(systemName: "mic.slash.fill")` + `textSecondary`；不渲染 glyph。
- 布局与现有 toolbar 一致：48pt 圆、`VStack(spacing: AppSpacing.xs)`、caption 12pt `textSecondary`；无背景、无描边（激活态）。
- **动画边界（重要）**：现有 `toolbarButton` 的图标容器有 `.transaction { $0.animation = nil }`，用于让 Mic/Speaker/Camera 的 on/off 状态即时切换。**不能把 `MicVolumeGlyph` 包在这个 transaction 内**，否则 4 段颜色动画会被抑制。实现要求：
  - 为 Mic 单独写 `micToolbarButton`，不把 glyph 塞进通用 `toolbarButton` 的 transaction 容器；
  - 圆形描边 + 关闭态 `mic.slash.fill`：`transaction { $0.animation = nil }`，on/off 即时；
  - 开启态 `MicVolumeGlyph`：位于该 transaction 之外，bar 内部使用
    `.animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: model.level)`；
  - 不依赖父级 transaction 的 nil，也不在 glyph 根设置全局 nil transaction。

可访问性：

- glyph `.accessibilityHidden(true)`；电平是装饰信息，不进入 VoiceOver。
- Button 继续使用 `caption`（`conv_toolbar_mic_on/off`）作为 `accessibilityLabel`。
- iOS 没有 Compose 的「独立 testTag + 并入父语义」机制；因此 segment 数值建议用单元测试覆盖，UI 测试只验证 Mic on/off 与按钮存在。若必须做 UI 数值断言，可加仅在 UI test 构建启用的隐藏 `accessibilityValue`，但不能污染正常 VoiceOver（见 §7）。

### 4.5 生命周期与隐私

| 场景 | 电平模型 | 图标 |
|---|---|---|
| Mic 开、说话 | 1..4 段随音量变化 | 4 段逐步点亮 |
| Mic 开、静音环境 | 0；4 段未点亮 | 外圈/下半仍主色 |
| Mic 关（muted） | `setMuted(true)` 递增 generation、立即发送当前代 0、meter.reset；在途旧上报被丢弃 | `mic.slash.fill`，灰色描边按钮 |
| unmute | 仅在 true→false 边沿递增 generation、发送当前代 0 并 `chunker.reset()`；旧 in-flight tap 按 generation 丢弃，首个 post-edge callback 由 one-shot token 丢弃，最常见的交错会连续丢两个 buffer（更多旧 in-flight callback 时更多）；pre-unmute pending 不上行，但 token 是 best-effort（见 §4.3） | 从 0 重新显示 glyph，首个 callback 被丢时最多约一个 buffer 不驱动电平 |
| 显式停止 | capturing/recovering 时递增 `levelGeneration`、reset、发送当前代 0；no-op stop 不递增 `levelGeneration`（`startGeneration` 仍递增），持锁快照当前 `levelGeneration`/sequence 后发送 0；`teardownMedia()` 在 `capture.stop()` 之后同步 reset 模型 | glyph 消失/0 |
| 重试耗尽 | 采集已停止且 meter 为 0；terminal path 只在同一临界区捕获当前 `levelGeneration` 后上报 0，**不递增 `levelGeneration`、不 reset**（`startGeneration` 也不变） | 0 |
| 媒体 restart | `startFullMediaFresh()` / `reconfigureFullMedia()` / `transitionToFull()` 中实际重启采集的分支 reset 模型；controller 每个新启动/重建代都提供 0 基线 | 从 0 开始 |
| 采集中途重试重建 | **立即归零**，每次实际重建提供当前代 0；恢复后由新帧重新起表，不保留旧平滑态 | 重试期间 0，恢复后正常；与 Android 短暂保持是有意差异 |
| 后台 mic 未静音 | 保持 full：采集与约 20Hz 电平上报继续，模型保持最新（glyph 不可见）；回前台不重启、不强制归零 | 后台不可见；回前台直接显示当前电平 |
| 后台 mic 静音 | `reconcileMedia` 先 topUp 再 `teardownMedia()`：采集停止、`deactivate()`，模型归零 | 后台不可见；回前台从 0 重新起表 |

muted 的视觉隐私语义同 Android：PCM 仍是真实拾音，必须强制 0，UI 同时切到 mic.slash。unmute 的 chunker reset、tap 起点 generation 校验与 unmute 首个 post-edge callback 的丢 token 共同承担实际上行隐私边界：已开始回调的旧 tap 必须被 generation 过滤；首个 post-edge callback 必须被 token 整 buffer 丢弃；两者同时发生时可能连续丢弃两个 buffer（见 §4.3 的明确丢音边界）。这是 best-effort 策略：平台 tap 队列深度不可观测，不能承诺“任意队列深度下绝无 pre-unmute 样本”。controller 以 `levelGeneration` 过滤旧代、以 report sequence 拒绝同代旧任务，并以当前代 0 建立新基线；`ConversationViewModel.updateMicLevel` 再按 `isMicMuted` 兜底。

### 4.6 数据流

```text
AVAudioEngine tap (1024 frames @ input rate)
  → AVAudioConverter → 16kHz mono Int16
  → PCMChunker (320 samples = 20ms)
  → MicLevelMeter.update (RMS→dBFS→[-45,-8]→attack/release→0..4)
  → AudioCaptureController (50ms throttle；reset 立即给当前代 0，generation + sequence 过滤旧任务)
  → Task @MainActor → ConversationViewModel.updateMicLevel(Int)
  → MicLevelModel (dedupe @Published)
  → MicVolumeGlyph (@ObservedObject, leaf only)
```

---

## 5. 改动清单

| 文件 | 改动 |
|---|---|
| `apps/ios/LivaChatApp/Core/Audio/MicLevelMeter.swift` | 算法/输出改为 Android 对齐：320 样本帧、`-45..-8`、0.85、Int 0..4；`levels=4` |
| `apps/ios/LivaChatApp/Core/Audio/AudioProtocols.swift` | `onLevel` 类型 Float → Int |
| `apps/ios/LivaChatApp/Core/Audio/AudioCaptureController.swift` | meter 按 chunk 更新；`lastMeterLevel`；generation 全路径同步访问并过滤旧代，report sequence 拒绝同代旧任务；每个新代提供 0 基线；mute/unmute reset；unmute 清共享 chunker pending、按 tap 起点 generation 丢弃跨代 buffer、并在转换前消费 one-shot token 丢弃 unmute 后首个 callback（旧 in-flight 与首个 post-edge callback 可能连续丢弃；对平台队列深度是 best-effort，不声称绝对无泄漏）；新增 `installTapConverterForTesting` + `ingestTapBufferForTesting` 组合测试缝，走完整 `handleTap`（token、generation、AVAudioConverter、process、uplink）；**uplinkLock**：reset 在同临界区更新 `uplinkGeneration`，`process` 投递前校验，关闭“生成 outgoing → 实际 uplink”窗口；中途重试明确归零；重试耗尽在同一临界区捕获 level generation 后上报 0；`beginRetries` 在同一 `controlLock` 临界区内创建/保存 `retryTask` 并发布 `recovering`，task 的清除路径同样持锁，防止立即完成的重试把 `.recovering` 永久锁存；保留样本输入、时钟、上报调度与 `beforeUplink` 暂停接缝；`onLevel` Int |
| `apps/ios/LivaChatApp/Core/Audio/MicLevelModel.swift` | **新增**叶子 ObservableObject（clamp + dedupe） |
| `apps/ios/LivaChatApp/Core/Conversation/ConversationViewModel.swift` | 持有 `let micMeter`；`updateMicLevel`（muted 强制 0）；`setMicMuted(true)` reset；`resetMicLevel`；移除 Float `micLevel`/`setMicLevel` |
| `apps/ios/LivaChatApp/Core/Conversation/ConversationCoordinator.swift` | onLevel -> `updateMicLevel`；mute 由 `setMicMuted(true)` reset，不重复归零；`startCaptureAndConfirmEngine` 统一 reset（覆盖 `startFullMediaFresh` / `reconfigureFullMedia` / 实际重启采集的健康分支），`teardownMedia` 在 `capture.stop()` 后单独 reset（不在 no-op early return 中 reset） |
| `apps/ios/LivaChatApp/Features/Conversation/MicVolumeGlyph.swift` | **新增** 4 段 glyph |
| `apps/ios/LivaChatApp/Features/Conversation/ConversationView.swift` | 新增/拆出 Mic 专用按钮：激活时使用 glyph（不在 `transaction nil` 内）；传入 `viewModel.micMeter`；关闭保持 mic.slash 且 on/off 即时 |
| `apps/ios/LivaChatApp/Features/AudioSpike/AudioSpikeView.swift` | 调试页适配 Int 电平（进度条按 `/4` 归一） |
| `apps/ios/LivaChatApp/LivaChatApp.xcodeproj/project.pbxproj` | 新增 `MicLevelModel.swift`、`MicVolumeGlyph.swift`、`MicLevelModelTests.swift`、`AudioCaptureLevelTests.swift` 的 file ref/build file |
| `apps/ios/LivaChatApp/LivaChatAppTests/MicLevelMeterTests.swift` | 替换为 Android 同款 320 样本用例 |
| `apps/ios/LivaChatApp/LivaChatAppTests/MicLevelModelTests.swift` | **新增** clamp/dedupe/reset |
| `apps/ios/LivaChatApp/LivaChatAppTests/AudioCaptureLevelTests.swift` | **新增** 320 分帧接线、多 chunk、50ms throttle、generation/sequence/0 基线、同代乱序与迟到的 unmute 0 基线、unmute pending/在途 tap buffer 上行边界与首 buffer discard token、**经 `handleTap` 全路径（token + generation + AVAudioConverter + process + uplink）验证 pre-unmute pending 不泄漏、首个 post-edge callback 被丢弃、第二个正常 callback 上行，以及旧 in-flight tap + 首个 post-edge callback 连续丢弃的完整序列**、**生成 outgoing→投递之间的 mute/stop/restart 交错**、**旧 no-op stop 与新 start 的 0 归属交错**、静音窗口回归；使用手动上报调度与 `beforeUplink`/`beforeStopBaseline` 暂停接缝 |
| `apps/ios/LivaChatApp/LivaChatAppTests/AudioCaptureRetryTests.swift` | 追加启动重试/重试耗尽/中途转换重建/持久转换失败的当前代 0 基线与模型不残留验证；旧代 transient/persistent conversion error 在 stop→start 后不得恢复或 teardown 新采集；无挂起 `retrySleep` 的连续 start/stop 回归测试验证 task 发布与 `recovering` 清除不可交错；阻塞 `controlQueue` 验证 tap 转换失败只投递、不同步迁移 lifecycle、成功转换的 failure token 使已排队恢复失效、三次排队失败仍进入终止，以及成功先于排队 persistent 决策时取消终止 |
| `apps/ios/LivaChatApp/LivaChatAppTests/ConversationViewModelTests.swift` | 删除/替换 Float 电平测试；muted 时 `updateMicLevel(3)` 仍为 0 |
| `apps/ios/LivaChatApp/LivaChatAppTests/ConversationCoordinatorTests.swift` | fake `onLevel` 类型改 Int；`onLevel?(3)`，断言 `micMeter.level`；mute 后旧非零上报不覆盖 0；播种非零后 stop/restart 归零 |
| `apps/ios/LivaChatApp/LivaChatAppTests/ConversationRuntimeIntegrationTests.swift` | fake `onLevel` 类型改 Int |
| `apps/ios/LivaChatApp/LivaChatAppUITests/AppShellUITests.swift` | 可选：Mic on/off 图标存在性；segment 数值不做 UI 断言 |
| `docs/ios_app_ui.md` | Mic 激活态补充 4 段 glyph 说明 |

> 非目标：不改 `MicVolumeGlyph.kt`、Android 测试、SDK、服务端。

---

## 6. 测试计划

### 6.1 MicLevelMeter 单测（直接对齐 Android）

使用 320 样本帧：

| 用例 | 输入 | 期望 |
|---|---|---|
| 静音 | 全 0 | 0 |
| 满幅 | 交替 ±32767/±32768 | `levels`（4） |
| 安静 | 振幅 30（≈-60.7dB） | 0 |
| 正常语音 | 振幅 3277（≈-20dB） | 2..3 |
| muted 清平滑 | 满幅→muted×5→振幅 1920 | 连续 muted 返回 0；unmute 返回 2 |
| 快攻 | 静音×10→满幅单帧 | 当帧 4 |
| 释放不跟随中间值 | 满幅→振幅 8519（norm≈0.9）单帧 | 3（release 0.85；`max` 写法会得 4） |
| 慢放 | 满幅→静音 40 帧 | 单调不增；首次 0 在 11..15 帧（≈260ms） |
| 空帧 | length 0 | 0 |
| reset | 满幅→reset→静音 | 0 |

### 6.2 MicLevelModel 单测

- `set(-1)` / `set(5)` clamp 到 0/4；
- 相同值不重复发布（用 `objectWillChange` 计数或自定义 sink 断言）；
- `reset()` 回 0。

### 6.3 接线/异步时序/上行回归单测

测试接缝：为 `process` 提供 internal 样本输入入口，并将电平上报时钟与 MainActor 调度做成 internal 可注入接缝；生产默认仍是 `systemUptime` + `Task { @MainActor ... }`，测试使用可控时钟和可在 MainActor 上手动 drain 的队列。测试必须驱动生产使用的同一分帧/上报逻辑，不能复制一份算法，也不能只测试 `generation1 != generation2` 这样的纯比较。

`AudioCaptureLevelTests`（新增）至少覆盖：

- **跨 tap 不足一帧**：160 个满幅样本不产生 meter 更新，再追加 160 个后只按一个 320 帧计算并上报 4；验证 controller 没有退回整 buffer 更新。
- **单 tap 多 chunk**：一帧满幅 + 一帧静音共 640 样本，meter 依次执行两次；最后电平按第二帧 release 后为 3，而非把整个 640 buffer 算成 4。保存最后一个 chunk 的结果。
- **节流与立即重置**：可控时钟验证常规上报约 20Hz；mute/unmute/stop/reset 的当前代 0 不等待 50ms 节流。
- **mute 后迟到非零**：旧代非零任务在 mute 后执行必须被丢弃，模型保持 0。
- **unmute/restart 后迟到旧 0**：先建立新一代非零电平，再执行旧代 0，必须丢弃且不能覆盖新电平。
- **同代任务乱序**：手动反转当前代 0 基线与同代新非零的执行顺序，sequence 必须丢弃较旧任务；普通电平任务乱序时也只应用较新值，不依赖 `Task` FIFO。
- **新代 0 基线**：stop 的 0 尚未执行时 start/rebuild 再次增代；即使旧 0 被丢弃，当前代 0 或 MainActor 同步 reset 仍保证模型不残留。内部重试不经过 coordinator，也必须有该基线。
- **unmute 上行隐私边界（完整生产路径）**：① 构造 <320 个可识别的 pre-unmute 非零 pending；unmute 后第一个 320 样本 tap 必须被 one-shot token 整 buffer 丢弃（不上行、不驱动 meter）；第二个正常 tap 必须作为 real PCM 上行，且不含 pre-unmute pending 或第一个 post-edge tap 的样本。使用 `installTapConverterForTesting` + `ingestTapBufferForTesting` 走完整 `handleTap`，不得用直接调用 `process` 的接缝代替组合验证。② 通过 `tapGeneration` 接缝模拟在 mute 期间开始转换、unmute 后才进入 `process` 的整 buffer，必须被丢弃且不驱动 meter（process 级 generation 单元测试）。③ 旧 tap 已捕获旧 generation、转换后暂停在 uplink 前；unmute 增代并 arm token；释放旧 tap 后其 outgoing 被 generation 丢弃，token 仍保留；随后第一个 post-edge tap 被 token 丢弃，第二个正常 tap 上行。断言 uplink 中只有最后一个 buffer，验证“旧 in-flight + 首个 post-edge”连续两个 buffer 丢弃的完整交错，并断言 generation 只增一次。④ unmute 后一次性 discard token 只能被下一个 tap 消费一次，重复 unmute 不重新 arm。
- **生成 outgoing → 投递之间 mute/stop/restart**：用 `beforeUplink` 暂停 tap 线程在 `lock.unlock()` 之后、`uplinkLock` 之前，分别执行 mute 与 stop→restart；恢复后旧 real frame 不得进入 `uplink`。`resetLevelLocked` 对 `uplinkLock` 的互斥保证旧帧要么在 reset 返回前投递完、要么在 generation 校验处被丢弃。
- **跨代转换错误**：旧 generation 的 transient/persistent conversion error 在 stop→start 后调用，必须被 `controlLock + lock` 的原子校验拒绝；新采集保持 running，`startGeneration` 不被旧错误递增，也不触发 `onFailure`。成功/空转换/streak 更新同样不得跨代写入。
- **tap 线程恢复投递**：阻塞 `controlQueue`，从当前 generation 注入 tap 转换失败；在释放队列前 lifecycle 必须保持 `.running`（不产生同步 `.recovering`），释放后进入 `.recovering`、退避重试成功并回到 `.running`、上报当前代 0。随后注入失败但先记录一次成功（token++），释放后必须保持 `.running` 且不触发 `onFailure`。三次排队失败后释放队列必须进入 `.stopped` 并触发“failed repeatedly” `onFailure`；若第三次失败后、释放队列前记录一次成功，则必须取消终止并保持 `.running`。
- **旧 no-op stop 与新 start 交错**：暂停旧 stop 在释放 `controlLock` 后、上报 0 前；完成新 start 并产生非零电平；旧 stop 继续后其 0 必须因旧 generation/较小 sequence 被丢弃，新电平保留。
- **静音窗口回归**：持续 muted 时仍在 mute 沿后准确发送 50 个完整零帧再 drop；unmute reset 不改变既有 `SilenceFlushGate` 状态机契约。

其他接线与回归：

- `AudioCaptureRetryTests`：中途每次实际重建及重试耗尽都有当前代 0；恢复后从新帧起表，旧平滑态不保留；无挂起的 `retrySleep` 连续执行 100 次 start→retry→stop，每次都必须在有限时间内回到 `.running`，验证 task 在注册完成前不会清除 `recovering`；阻塞 controlQueue 验证 tap 转换失败异步投递及 failure token 失效。
- `ConversationCoordinatorTests`：`capture.onLevel?(3)` → `viewModel.micMeter.level == 3`；onFailure 仍正常。
- `ConversationViewModelTests`：移除 Float clamp 用例，改为模型 clamp/dedupe；`isMicMuted == true` 时 `updateMicLevel(3)` 仍为 0，unmute 后 `updateMicLevel(3)` 为 3。
- `ConversationCoordinatorTests`：先 mute，再模拟迟到的 `capture.onLevel?(3)`，模型仍为 0；先播种非零电平，再执行媒体 stop 后模型为 0，随后再次播种并 restart，`startCaptureAndConfirmEngine()` 的同步 reset 使模型在基线到达前即为 0。
- `AudioSpike` 是 DEBUG 诊断页：确认 Int 电平显示不崩溃，进度条按 `/4` 归一正确。
- 全量 iOS 单测回归；测试总数以当前测试发现为准，不在文档中硬编码。

### 6.4 UI / 真机

- Mic 关：`mic.slash.fill`；无 glyph。
- Mic 开：glyph 出现，4 段自下而上点亮；说话时 1→4。停止说话后，算法从首个静音 20ms 帧起约 260ms 首次归 0；50ms 节流自身最多再增加约 50ms 上报等待，120ms 颜色过渡单独验收，另计 tap/process 调度与帧对齐时间，因此不把「严格 0.3s 内视觉全暗」作为验收承诺。
- muted：立即切斜杠图标；对着 mic 说话不亮；说话后立刻 mute，确认没有迟到的非零电平/残留；unmute 从 0 开始，既不混入 mute 前电平，也不在可观察范围内上行 pre-unmute real PCM；旧 in-flight tap + 首个 post-edge callback 的 best-effort 边界与可能连丢两个 buffer 的行为见 §4.3。
- 显式 stop、重试耗尽、后台 mic 静音 teardown：电平归零；后台 mic 未静音时采集继续（glyph 不可见，回前台直接显示当前值）；采集中途重试期间同样为 0，恢复后从新帧起表；前台从 stopped 重启时从 0 恢复。
- 流式 transcription 高频更新时 glyph 不卡顿，`ConversationView` 不因电平整页重绘（可用 Instruments/SwiftUI body 日志辅助）。
- 真机 24pt 下 4 段可辨，并用截图比较新旧 Mic 图标视觉尺寸、外圈描边和 caption 位置；若 4 段不可辨，再评估 §3.2 的 3 段 fallback。
- 至少覆盖内置 Mic 与免提路由；有蓝牙设备时补测蓝牙输入。若不同路由增益导致窗口不适配，只记录证据并另行评审参数，不在本次默认实现中引入平台动态调参。

> iOS 没有 Compose 的 unmerged semantics 机制，glyph 对 VoiceOver 隐藏，因此 segment 数值以单测为主；UI 测试不强行读取电平值。

---

## 7. 风险、权衡与待确认项

1. **4 段 vs 3 段**：默认 4（Android 事实源）；3 段仅作为 24pt 视觉不可辨时的 fallback，需重新调几何与测试。
2. **50ms 节流 vs Android 50Hz**：iOS 当前是 20Hz UI 更新；4 段离散值下通常足够。叶子模型已保证 50Hz 也不会整页重绘，如真机觉得滞后可把 `lastLevelReport` 调到 20ms。
3. **meter 更新时机**：必须改为按 `PCMChunker` 的 320 样本帧更新；若继续按整个转换 buffer，release 节奏会随 tap 帧率变化，不能算 Android 对齐。
4. **旧 Float 电平 API 的迁移**：`AudioSpikeView`、两个测试 fake、`ConversationViewModelTests`/`ConversationCoordinatorTests` 都会受影响；本设计推荐一次性切换 Int，不保留双通道。
5. **UI 数值可测性 vs a11y**：glyph 对 VoiceOver 隐藏后，XCUITest 无法读到 segment；推荐单测 + 真机视觉，不为了测试把电平暴露给 VoiceOver。若必须做 UI 数值断言，再加仅 UI-test 构建的隐藏 value，另行评审。
6. **自定义 glyph 的视觉保真**：`MIC_BELL_PATH` 需要按 24 视口坐标翻译；外圈/横线/竖线常量来自 Android，但 dp→pt、SF Symbol→自定义 glyph 以及 spring→120ms ease-out 都不能预设视觉完全一致。外圈使用 `.stroke`，以新旧截图和真机验收为准。
7. **布局稳定性**：glyph 固定 24pt，Mic/Speaker/Camera 三列和 caption 位置不变；不要在 glyph 内加外环/独立 meter 槽位。
8. **性能**：20Hz 主线程模型更新 + 叶子重绘；`@Published` 去重；不要把 `MicLevelModel.level` 读进 `ConversationView` body。后台 mic 未静音时 full 继续，电平上报仍约 20Hz（glyph 不可见）；若要进一步降低后台唤醒，需在 controller/coordinator 增加基于 `isForeground` 的门控，属于独立优化。
9. **跨线程 generation、同代乱序与新代基线**：`process` 的旧任务可能晚于 mute/unmute/stop/restart 执行，同代 0 基线也可能晚于同代新电平执行。`levelGeneration`、report sequence 与 last-applied 的访问必须遵循同一同步契约，MainActor 只在锁内比较/更新，回调在锁外执行；不得假设 `Task` FIFO。每个新代还必须有当前代 0 或 MainActor 同步 reset；只丢弃旧 0 不足以清空 UI。手动乱序调度测试是必做项，不以「逻辑简单」为由只测纯比较或依赖真机。
10. **unmute pending、in-flight tap 与 discard token 的上行边界**：iOS `PCMChunker` 的 pending 可能残留 <320 个 pre-unmute 真实样本。保留 `setMuted(false)` 时共享 `chunker.reset()` 的方案，但**只在实际 muted→unmuted 边沿执行**；明确接受丢弃这段不足 20ms 的 muted pending。仅 reset 不能覆盖正在转换的旧 tap 或平台已排队的 callback：generation 丢弃所有在 unmute 前已开始且未完成的 callback，one-shot token 再丢弃首个 post-edge callback；最常见的交错会连续丢弃两个 buffer，代码不强制上限（生产 tap 请求 1024 frame，单 callback 约 21ms@48kHz / 64ms@16kHz）。one-shot token 是 best-effort：平台未公开 tap 队列深度，无法证明不存在第二个仍含 pre-unmute 音频的 callback；文档与验收不得使用“绝不泄漏”的绝对措辞。必须用 `handleTap` 全路径组合测试验证 pending / 旧 in-flight / 首个 post-edge / 第二个正常 callback 的完整序列，不能只分别测试 pending reset 与 token；独立 meter chunker 只能隔离电平计算，不能修复上行边界。
11. **on/off transaction 与电平动画冲突（已处理）**：实施前 Mic/Speaker 共用按钮容器的 `.transaction { $0.animation = nil }` 会让所有子动画失效；若直接把 glyph 放进去，4 段颜色更新会瞬变或完全不响应 SwiftUI 动画。实现已拆出 Mic 专用按钮：on/off transaction 只作用于描边/关闭态图标，`MicVolumeGlyph` 在 transaction 外并拥有自己的 bar animation；回归时重点检查这一层。
12. **内部重试的平台差异**：Android 在短暂重建时保留电平，本文选择 iOS 立即归零，避免无采集期间冻结非零电平；当前 iOS 的每次 `performStartAttempt` 以及 stop/recovery 路径的统一 reset helper 都会 `meter.reset()`（纯 `teardownLocked()` 本身不 reset），与该选择一致。该差异必须保留在 §4.5 和真机验收中，后续若要改为保持，需重新设计内部 teardown 与当前代基线，不能只改表格文字。
13. **uplink 串行化与主线程等待**：`uplinkLock` 关闭“生成 outgoing → 实际 uplink”的竞态；`resetLevelLocked` 在 generation 递增的同一临界区更新 `uplinkGeneration`。代价是 mute/stop/restart（通常在 MainActor）可能短暂等待一个在途 `uplink` 调用；`uplink` 闭包不得回调 capture controller，否则会与 `lock` 形成锁环。当前实现只调用 SDK/播放层。
14. **转换错误与恢复的跨代竞态**：错误路径的预过滤与 recovery 状态迁移不在同一锁域时会跨代；恢复函数必须在 `controlLock` + `lock` 内原子复核 `capturing`/`recovering` 与来源 generation 后再迁移。成功/空转换/统计/streak 与分配失败上报同样要带 generation；否则旧错误会拆掉新采集，或旧成功会清掉新代已累计的 streak。
15. **旧 stop 的 0 归属**：no-op stop 的 0 必须在持锁时快照 generation 和 sequence；解锁后不得重新读取“当前代”。否则旧 stop 会把 0 标成新 start 的代次，并以较新 sequence 覆盖新采集的第一帧电平。
16. **retry task 发布竞态**：task 在注册前即可运行；若 `retrySleep` 立即返回，task 可能先恢复并清除 `recovering`，创建线程随后又保存已完成的 task 并重设 `recovering`，导致 capture 实际 running 但 `lifecycleState` 永久为 `.recovering`。修复不依赖调度顺序：task 创建、`retryTask` 保存与 `recovering = true` 在同一 `controlLock` 临界区完成，且 task 的清除路径必须先取得同一把锁。
17. **`removeTap` flush 与 tap 线程 `controlLock` 死锁**：`AVAudioEngine.removeTap` 会同步 flush 在途 tap callback。teardown 持有 `controlLock` 调用 `removeTap` 时，如果 callback 的转换错误路径同步获取 `controlLock`，双方互等。当前实现把恢复校验/迁移投递到 `controlQueue`，tap 线程只持有短时 `lock`；`conversionFailureToken` 防止成功转换或更新失败后旧恢复仍执行。回归测试：`testTapConversionFailureDispatchesOffTapThreadAndRecovers`、`testTapConversionSuccessInvalidatesQueuedFailure`。

---

## 8. 验收标准

1. Mic 开启时，图标内显示 4 条横线，自下而上点亮；颜色与 Android 一致。
2. Mic 关闭时显示 `mic.slash.fill`，无 glyph；muted 立即归零，mute 后迟到的旧非零不覆盖 0；unmute/restart 后迟到的旧代 0 不覆盖新一代电平，同代乱序任务也由 sequence 保证只应用较新值。
3. 算法单测与 Android 用例对齐（静音/满幅/正常语音/muted/快攻/慢放/空帧/reset），controller 接线测试证明实际按 320 样本帧更新。
4. 电平更新只重绘 glyph；`ConversationView` 不因电平整页重绘。
5. 每个新 generation 都有当前代 0 或 MainActor 同步 reset；显式 stop、重试耗尽、后台 mic 静音 teardown 不残留；后台 mic 未静音时采集继续、模型保持最新；中途重试明确归零并在恢复后从新帧起表。
6. unmute 从 0 开始；pre-unmute pending 既不参与新电平，也不作为 post-unmute real PCM 上行；旧 in-flight tap 与首个 post-edge callback 均不上行，且必须用完整 `handleTap` 组合测试锁定该序列；明确接受保守丢弃策略（最常见为连续两个 buffer；若 unmute 前有更多未完成 callback，generation 会继续丢弃），并承认 one-shot token 对未公开的 tap 队列深度只是 best-effort；mute 后 50 帧静音补发窗口保持原语义。
7. 全量 iOS 单测与相关 UI 回归通过；真机 24pt 4 段可辨，新旧截图确认图标尺寸/描边/caption 位置，算法归零、上报延迟和颜色动画分别验收。
8. 不改 SDK、服务端、Android 或上行协议；明确的上行样本边界变化是本文所述 unmute pending 丢弃，以及旧 in-flight tap 与首个 post-edge callback 的保守丢弃（最常见连续两个 buffer；若 edge 前有更多未完成 callback，generation 会继续丢弃）。

---

## 9. 实施顺序（建议）

1. 重写 `MicLevelMeter` + 单测（先锁定算法）。
2. `AudioCaptureController` 按 chunk 接入 + `onLevel` 改 Int；实现同步 generation、report sequence、每代 0 基线和 unmute 上行边界，用手动调度跑异步/上行/重试单测。
3. 新增 `MicLevelModel`，接线 `ConversationViewModel`/`ConversationCoordinator`；更新 fakes/测试。
4. 实现 `MicVolumeGlyph`，接入 `ConversationView` Mic 按钮；更新 AudioSpike。
5. 全量单测、UI 回归、真机视觉/生命周期验证。
6. 更新 `docs/ios_app_ui.md` 与本文状态。
