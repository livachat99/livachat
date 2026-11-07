# LivaChat iOS App 相机横竖屏采集设计

**状态**：已实现（方案 A，预览保持竖屏）；模拟器单元测试 338 条通过（1 跳过）、相机相关 UI 回归 4 条通过；iPhone 11 真机前后摄四向验证通过，旋转后暗帧已由 skip 保护消除；连续旋转合并与 KVO/抑制修正已实现，待真机复测。
**日期**：2026-10-22（2026-10-23 review 修正）
**范围**：相机打开期间，设备竖持/横持（含倒置）时的采集帧方向。App 仍锁定竖屏，预览仍保持竖屏；不改变会话 UI、SDK、服务端协议。

**上游参考**：
- `docs/camera_enhance.md`（Android 相机链路、图库上行、方向处理）
- `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/media/CameraController.kt`
- `apps/ios/LivaChatApp/Core/Video/CameraController.swift`
- `apps/ios/LivaChatApp/Core/Conversation/ConversationRuntimeFactory.swift`
- `docs/ios_dev_rules.md`（iOS 15 / SwiftUI / SPM / UIKit 例外）

---

## 1. 目标与非目标

### 1.1 目标

1. 相机打开后，用户竖持、左横持、右横持（以及倒置）拍摄时，上行 JPEG 的**像素本身已旋正**。
2. 上行协议 `orientation` 恒为 `0`，与 Android 一致；服务端/AI 拿到的就是正立图片。
3. video message / AI 回图中的图片在 iOS、Android、Web 三端方向正确。
4. 方向变化不影响既有链路：warmup、throttle、turn 计数、前后摄切换、后台/前台、图库暂停恢复均保持现有语义。

### 1.2 非目标

- 不旋转 App UI，不实现横屏相机布局。
- 不旋转相机预览；预览继续按现有竖屏策略显示（已确认接受）。
- 不修改 SDK、服务端协议、服务端图片处理。
- 不改变 `CameraFramePolicy` 的分辨率/节流/暖机参数。
- 不支持 iPad 原生分栏/多窗口；沿用 iPhone 兼容模式。

### 1.3 硬约束（不可回退）

1. 上行 JPEG 的**像素本身**必须已按设备方向旋正；不得依赖 EXIF 方向或让下游客户端旋转。
2. 上行协议 `orientation` 恒为 `0`（`.portrait0`）。
3. 协议 `width`/`height` 必须是旋正后的像素尺寸：长边缩放到 ≤1280，比例随 session preset（16:9 约 `720×1280` / `1280×720`，4:3 约 `960×1280` / `1280×960`）。
4. 方向变化不改变 `turnId`、不切换镜头、不重置 warmup/throttle、不重建 session。
5. 不修改 SDK、服务端协议、服务端图片处理与现有相机 UI/预览。

---

## 2. 现状取证

### 2.1 iOS 现状

- App 默认竖屏锁定（`AppDelegate` 返回 `.portrait`）；仅 Agent Job 结果覆盖层临时解锁横竖屏。
- `CameraController.applyOrientation`（`Core/Video/CameraController.swift:239-249`）对 **data 与 preview 两条 connection** 固定 `.portrait` / `videoRotationAngle = 90`，运行期间不再更新。
- `CameraImageEncoder.jpegData`（同文件 `397-457`）直接编码连接旋转后的 `CVPixelBuffer`，不额外旋转、不写 EXIF。
- `ConversationRuntimeFactory.swift:26-36` 固定发送 `orientation: .portrait0`。
- 没有设备方向监听。因此设备横持时，传感器画面被错误地按 portrait 旋转，上行 JPEG 与 AI 回图方向错误。

### 2.2 Android 参考实现

Android 的产品策略与实现如下：

- `AndroidManifest.xml:50`：`android:screenOrientation="portrait"`，UI/预览锁定竖屏；`docs/camera_enhance.md:696` 明确“设备旋转不改变预览，属设计内”。
- `CameraController.kt:69-75`：`OrientationEventListener` 监听**物理设备方向**，通过 `UseCase.snapToSurfaceRotation` 更新 `ImageAnalysis.targetRotation`。
- `CameraController.kt:236-260`：`imageProxyToJpeg` 先缩放到长边 ≤1280，再按 `imageProxy.imageInfo.rotationDegrees` 旋转像素。
- `CameraController.kt:223`：上行时固定传 `Orientation.PORTRAIT_0`。
- 预览不跟随物理方向（保持竖屏），只有采集帧被旋正。

### 2.3 图片消费链路

- 服务端把上行 JPEG 原样保存/交给模型（`services/session/orchestrator.py` 的 `ChatImageFrame` / `ServerAiTextImage`），没有服务端旋转步骤。
- iOS `AIImageView` 仅在 `image.orientation != 0` 时旋转像素（`Features/Conversation/AIImageView.swift:66-99`）；Android `ChatImageView` 同样按 `orientation` 旋转。
- 结论：**必须由客户端旋正像素并发送 `orientation = 0`**；不能发送 `landscape90/270` 让下游去转，否则 AI 模型看到的是歪图。

---

## 3. 已确认决策（方案 A）

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | App 仍锁定竖屏，相机 UI/预览不旋转 | 与 Android 现有产品策略一致；已确认接受预览保持竖屏 |
| D2 | 使用**物理设备方向**（`UIDeviceOrientation`）触发采集旋转；iOS 17+ 角度由 `AVCaptureDevice.RotationCoordinator` 提供，iOS 15/16 使用 `videoOrientation` 语义 | UI 不旋转，不能依赖 interface orientation；Android 同样读取物理方向；前后摄传感器原生方向不同，不能硬编码角度 |
| D3 | 只更新 **data connection** 的方向；preview connection 继续 portrait/90 | 预览与 UI 保持竖屏一致；采集帧独立旋正 |
| D4 | 像素旋正 + 协议 `orientation = .portrait0` | 服务端/AI 直接消费 JPEG 原始像素；Android 契约 |
| D5 | 方向变化不递增 `turnId`、不重置 warmup/throttle、不重建 session | `turnId` 只表达镜头/采集轮变化，与 Android `shouldBumpTurn` 一致 |
| D6 | 支持 portrait / landscapeLeft / landscapeRight / portraitUpsideDown 四个物理方向 | 与 Android `OrientationEventListener` 对齐；UI 不支持倒置不影响采集倒置旋正 |
| D7 | 忽略系统方向锁（按物理方向旋转）；若 `UIDevice` 通知受锁定影响则改用 CoreMotion | Android `OrientationEventListener` 同样忽略系统旋转锁；iOS 以物理方向为准 |

---

## 4. 方向映射与角度来源

### 4.1 物理方向 → `AVCaptureVideoOrientation`

| 物理持机方向（`UIDeviceOrientation`） | `AVCaptureVideoOrientation` |
|---|---|
| `.portrait` | `.portrait` |
| `.portraitUpsideDown` | `.portraitUpsideDown` |
| `.landscapeLeft` | `.landscapeRight` |
| `.landscapeRight` | `.landscapeLeft` |
| `.faceUp` / `.faceDown` / `.unknown` | 忽略，保留上一次有效方向 |

这是标准映射（landscape 左右互换），用于 iOS 15/16 的 `connection.videoOrientation` 以及日志。

### 4.2 iOS 17+：使用 `RotationCoordinator`，不硬编码角度

- 在配置设备时创建：
  ```swift
  AVCaptureDevice.RotationCoordinator(device: device, previewLayer: nil)
  ```
- 应用 `rotationCoordinator.videoRotationAngleForHorizonLevelCapture` 到 data connection 的 `videoRotationAngle`。
- `RotationCoordinator` 会按**前后摄传感器原生方向**计算角度；硬编码 90°/180° 在前摄上会 180° 倒立（真机已验证）。
- 每次方向变化时重新读取该角度；不缓存在应用侧。
- `videoRotationAngleRelative(toDeviceOrientation:)` 在 iOS 27+ 才可用，本 App 需支持 iOS 15/26，故不使用。
- 仍检查 `connection.isVideoRotationAngleSupported(angle)`；不支持时回退到 `videoOrientation`。

### 4.3 iOS 15/16 回退

- 使用 `connection.videoOrientation = CameraFrameOrientation.videoOrientation(for: deviceOrientation)`；这是语义 API，由 AVFoundation 处理前后摄传感器差异。
- 不手工计算角度。

### 4.4 像素方向

- 方向以 JPEG 像素/四象限为准，不依赖 EXIF；JPEG 不写 EXIF orientation。
- data connection 继续不镜像；preview 继续按前置镜像（现有逻辑，保持不变）。

---

## 5. 详细设计

### 5.1 设备方向监听

在 `CameraController` 内部（或抽成 `CameraDeviceOrientationMonitor`）实现：

- `start` 时：
  - `UIDevice.current.beginGeneratingDeviceOrientationNotifications()`
  - 注册 `UIDevice.orientationDidChangeNotification`
  - 读取当前方向；若无效（faceUp/faceDown/unknown），默认 `.portrait`
- 方向回调：
  - 只接受四个有效方向；无效值忽略
  - 记录最近一次有效方向（锁保护）
  - 分发到 `sessionQueue` 更新 data connection
- `stop` / 失败 / `deinit` 时：
  - 移除通知
  - `endGeneratingDeviceOrientationNotifications()`
  - 重复 start/stop 必须成对，避免通知泄漏；用状态位保证 begin/end 一一对应
- 若实测系统方向锁会抑制 `UIDevice` 通知，则使用 CoreMotion fallback：
  - `CMMotionManager.deviceMotion.gravity`（或姿态）判断物理方向；
  - 按最近 90° 吸附，使用 45° 阈值/少量滞回，等价 Android `snapToSurfaceRotation`；
  - 设备平放（gravity 接近 ±Z）时保持上一次有效方向，不切到 unknown。

### 5.2 data connection 更新

新增纯函数/小工具：

```swift
enum CameraFrameOrientation {
    static func videoOrientation(for device: CameraDeviceOrientation) -> AVCaptureVideoOrientation
}
```

`CameraController` 内（`rotationCoordinator` 因 stored property 不能标 `@available`，以 `AnyObject?` 存储，使用时 cast）：

```swift
// iOS 17+: KVO 是角度与 skip 的唯一来源，不再在 UIDevice 回调里读 coordinator。
rotationObservation = coordinator.observe(
    \.videoRotationAngleForHorizonLevelCapture,
    options: [.new]
) { [weak self] coordinator, _ in
    self?.coordinatorAngleChanged(
        coordinator.videoRotationAngleForHorizonLevelCapture,
        generation: generation
    )
}

// iOS 15/16 fallback only:
connection.videoOrientation = CameraFrameOrientation.videoOrientation(for: device)
```

> 真机已验证：若用后摄硬编码角度套到前摄，前摄会 180° 倒立；`RotationCoordinator` 会按前/后摄传感器方向给出正确角度。
>
> **不能只用 UIDevice 通知触发读取 coordinator**：coordinator 的 KVO 更新在主队列投递，UIDevice 通知可能先到，sessionQueue 读到旧角度后不会再被纠正，整段会话会保持错误方向。必须观察 KVO。

要求：

- **拆分方向来源**：`configureDataConnection` 在 iOS 17+ 使用 coordinator 当前 capture angle，iOS 15/16 使用 UIDevice 语义方向；`configurePreviewConnection` **固定 portrait/90 + 前置镜像**，绝不跟随物理方向。
- **iOS 17+ 运行时**：UIDevice 通知只维护 `latestDeviceOrientation` 与日志；coordinator KVO 在主队列回调，切到 sessionQueue 后写 `videoRotationAngle`。回调携带 coordinator generation，切换镜头/停止后旧回调直接丢弃。
- **iOS 15/16 运行时**：UIDevice 通知驱动语义方向；同一套更新抑制/合并逻辑。
- **不要**在方向回调里修改 preview connection；预览保持现有 `configurePreviewConnection`（portrait/90、前置镜像）。
- data connection 的 `automaticallyAdjustsVideoMirroring = false`、`isVideoMirrored = false` 保持现状。
- 方向变化只更新 connection 属性，不 `beginConfiguration/commitConfiguration`、不重启 session；如个别设备出现异常，再评估是否需要在 sessionQueue 内包一次配置事务。
- Apple 文档说明：对 `AVCaptureVideoDataOutput` 设置 `videoRotationAngle` 会物理旋转 buffer，并可能触发 capture render pipeline 重配；初始角度必须在 `startRunning` 前设置。运行中方向变化是**每次方向切换一次**的重配成本，不是每帧成本；真机若出现明显卡顿/黑帧，回退到编码器侧 CIImage 旋转（连接固定 portrait）。
- `start` 的 `configure` 内：先应用当前方向，再 `startRunning`/`setSampleBufferDelegate`，保证首帧方向正确；`switchCamera` 重建 input 后重新应用；`pauseCameraForPhoto`/后台恢复重新 start 时自然重新应用。

### 5.3 生命周期

| 事件 | 方向监听 | data connection |
|---|---|---|
| `start(front:)` | 启动并读取当前方向 | 配置完成后应用当前方向 |
| 方向变化（iOS 17+） | UIDevice 仅更新状态/日志；coordinator KVO 驱动角度 | 更新 connection；抑制 push + 合并 skip |
| 方向变化（iOS 15/16） | UIDevice 语义方向 | 更新 connection；抑制 push + 合并 skip |
| `switchCamera(front:)` | 保持监听 | 重新配置后应用当前方向 |
| `stop()` | 停止并移除通知 | 无（session 停止） |
| `pauseCameraForPhoto()` → `stop()` | 停止 | 无 |
| `resumeCameraAfterPhoto()` → `start()` | 重新启动并读取 | 重新应用 |
| 后台/前台（coordinator stop/start） | 随停随启 | 随停随启 |
| session 失败/中断 | `stop()` 内清理 | 无 |

### 5.4 线程与一致性

- 方向通知在主线程；只维护 latest 与日志，不直接碰 session。iOS 17+ 运行时的 data connection 角度由 coordinator KVO 驱动；iOS 15/16 由 UIDevice 驱动。
- `start` 只把当前方向写入 `current`，不回调为“变化”；初始方向由 session 配置阶段直接应用，避免启动时误请求一次 skip。
- 最新方向用 `stateLock` 保护；`rotationCoordinator` / KVO token 为 `sessionQueue` 私有；`rotationGeneration` / `usesRotationCoordinator` 用 `stateLock` 保护。
- coordinator 的 KVO 在主队列投递；回调切到 `sessionQueue` 后写 connection。切换镜头/停止时 generation 递增，旧 coordinator 的延迟回调直接丢弃。`rotationCoordinator` 只在 `sessionQueue` 创建/读取/销毁，避免跨线程访问。
- 方向切换是连接属性级操作；帧的宽高与像素来自同一个 buffer，不会尺寸/元数据错配。
- 连接重配可能输出暗帧/曝光未收敛帧（真机首帧 JPEG 23.8KB vs 常规 150KB+）。更新期间与结束后 500ms settle 内抑制 push：
  - 每次角度更新（KVO 或 iOS 15/16 语义）调用 `beginOrientationUpdate`，pipeline 的 `activeOrientationUpdates` 计数 +1；`captureOutput` 在 warmup 后、throttle 前丢弃这些帧（不消费 throttle）；
  - 更新完成 `endOrientationUpdate` 计数 -1，并在最后一次更新结束后额外抑制 500ms（覆盖 setter 返回后才到达的暗帧）；
  - 不重置 warmup、不递增 turn、不影响 facing 检测。
- **连续旋转合并**（`CameraOrientationSkipCoalescer`，2s 滑动窗口）：一次连续旋转只武装一次 skip；窗口内的后续角度变化仍更新 connection 并延长抑制窗口，但不再武装新的 skip。
  - skip 在更新开始时武装；由于更新期间与 settle 期间帧被抑制，skip 不会被旧角度帧提前消费，而是在所有排队更新结束、settle 结束后才消费，覆盖最终过渡帧；
  - skip 带 2s 时效（从最后一次 settle 结束计），超时后不再丢弃健康帧；
  - 连续旋转因此只消费一个 throttle 槽位，不会因每次变化各 skip 一次而叠加空档；
  - 取舍：settle 期间会主动丢帧，最后一次旋转后约 500ms 才开始恢复；这是为了避免暗帧。
- 日志锚点：`frame skipped after orientation change`；窗口内后续变化为 `orientation skip coalesced`；coordinator 角度日志含 `coordinatorAngle=...`。KVO 可能先于 `device orientation` 日志到达，此时日志里的 `device=...` 可能是上一方向，**以 `coordinatorAngle` 为准**。

### 5.5 编码与协议（保持不变）

- `CameraImageEncoder` 继续使用连接旋转后的 `CVPixelBuffer`；像素已正立，无需额外旋转，JPEG 不写 EXIF orientation。
- 缩放仍以实际 buffer 长边为准（长边 ≤1280）：
  - 竖持：约 720×1280（16:9）或 960×1280（4:3），长边 1280
  - 横持：约 1280×720（16:9）或 1280×960（4:3），长边 1280
  - session preset 回退到 `.high` 时，缩放后尺寸随其宽高比变化，长边仍为 1280。
- **上行 `width`/`height` 必须是旋正后的尺寸**，不是传感器旋转前的尺寸；与 JPEG 像素尺寸一致。
- iOS 由 AVFoundation 在 sample buffer 层旋转，编码器再缩放；Android 是先缩放后旋转。顺序不同但长边不变、输出尺寸/方向一致；App 侧不做整图旋转副本（是否产生中间 buffer 由 AVFoundation 决定）。
- `ConversationRuntimeFactory` 继续传 `orientation: .portrait0`；补充注释说明像素已旋正、协议恒 0。
- `CameraFramePipeline`、`VideoSendPipeline`、SDK、服务端均无需改动。
- 建议日志补充：`deviceOrientation`、`rotationAngle`、编码后 `width×height`、`orientation=0`，便于真机排障。

### 5.6 预览策略（已确认）

- preview connection 继续固定 portrait/90，前置镜像保持现有行为。
- 设备横持时，预览仍是竖屏画面；上行 JPEG 是横屏正立。预览与最终图片方向不一致是**已接受**的 Android parity 取舍。
- 不新增预览方向监听、不修改 `CameraPreviewView`。

### 5.7 边界

- 平放桌面（faceUp/faceDown）或方向未知：保留上一次有效方向；首次启动若未知则按 portrait。
- 倒置持机：采集按 270° 旋正；App UI 仍不支持倒置（不影响采集）。
- 前后摄切换：新 data connection 重新应用当前方向；镜像语义不变。
- 首帧在方向未知时可能按 portrait 编码；一旦收到有效方向，后续帧更新。方向监听应在 session 启动前开启，尽量缩短该窗口。
- 旋转过程中不重启 warmup/throttle/turn；`turnId` 只在 facing 变化时递增。

---

## 6. 改动清单

| 文件 | 改动 |
|---|---|
| `apps/ios/LivaChatApp/Core/Video/CameraController.swift` | 方向监听接入、data connection 动态旋转、生命周期清理、日志 |
| `apps/ios/LivaChatApp/Core/Video/CameraOrientation.swift`（新增） | `CameraDeviceOrientation`、方向提供协议、方向/角度纯映射 |
| `apps/ios/LivaChatApp/Core/PlatformAdapters/CameraDeviceOrientationMonitor.swift`（新增） | `UIDevice` 物理方向监听、last-valid、start/stop 配对 |
| `apps/ios/LivaChatApp/Core/Conversation/ConversationRuntimeFactory.swift` | 注入 monitor；注释固定 `orientation = .portrait0` 的像素契约 |
| `apps/ios/LivaChatApp/LivaChatAppTests/CameraOrientationTests.swift`（新增） | 方向映射、角度映射、flat/unknown 忽略单测 |
| `apps/ios/LivaChatApp/LivaChatApp.xcodeproj/project.pbxproj` | 新文件/测试 target 引用 |
| `docs/ios_app_camera.md` | 本文 |

不改动：`AppOrientation.swift`、`CameraPreviewView.swift`、`CameraModeView.swift`、SDK、服务端、Info.plist、UI 布局。

---

## 7. 测试计划

### 7.1 单元测试

- `portrait` → `.portrait`
- `landscapeLeft` → `.landscapeRight`
- `landscapeRight` → `.landscapeLeft`
- `portraitUpsideDown` → `.portraitUpsideDown`
- `faceUp` / `faceDown` / `unknown` → `nil`（保留 last-valid）
- 既有 `CameraFramePolicy` / turn 规则回归
- 角度由 `AVCaptureDevice.RotationCoordinator` 提供，不做单测硬编码；前/后摄由真机四象限覆盖。

### 7.2 真机（iPhone 11，iOS 26.6）

使用四象限靶标或带明确“上”方向的标记，**按 JPEG 像素/象限判断方向，不依赖 EXIF**：

1. 竖持：JPEG 像素正立，宽高约 720×1280，协议 orientation 0。
2. 左横持：JPEG 像素正立，宽高约 1280×720，协议 orientation 0。
3. 右横持：同上。
4. 倒置：JPEG 像素正立（180°），协议 orientation 0。
5. 前摄/后摄分别覆盖 1–4；切换镜头后继续正确。
6. 相机打开时已横持：首帧方向正确。
7. 拍摄中旋转：后续帧方向切换正确；warmup/throttle 不重置；turn 不变；日志出现 `coordinatorAngle=...`、`frame skipped after orientation change`；旋转期间与 settle 内无 push，恢复后的首条上行帧不是暗帧/小字节帧。
8. 连续快速旋转（如 portrait → landscapeRight 间隔约 1.5s）：整段旋转只出现一次 `frame skipped after orientation change`，窗口内后续变化为 `orientation skip coalesced`；不会因每次变化各自消费一个 throttle 槽位而叠加空档；最后一个变化后约 500ms 恢复 push。
9. 平放启动相机后抬起至 portrait：首个有效方向通知不应产生 skip，也不应丢掉 warmup 后的首帧。
10. 旋转过程中切换前/后摄：旧 coordinator 的延迟 KVO 回调被 generation 丢弃；新镜头最终角度正确。
11. 关闭相机 → 重新打开；后台/前台；图库选择器暂停/恢复。
12. 服务端与 AI 回图：iOS、Android/Web 三端图片方向正确；服务端日志 orientation=0；协议 width/height 与 JPEG 像素尺寸一致。
13. 设备物理旋转时 App/相机预览仍保持竖屏锁定（方案 A 验收项）。
14. 开启系统方向锁后重复 1–4：采集仍按物理方向旋正（D7），UI 仍保持竖屏。
15. 若最终采用 CoreMotion fallback，重复 1–4，并验证平放桌面时保持 last-valid 方向。

### 7.3 回归

- 竖屏拍照/上行路径与现状一致。
- 相机模式 UI、滚动位置、切换摄像头、图库发送路径不受影响。
- 模拟器无摄像头：真机测试为必需项；UI 测试使用现有 fake camera 路径。

---

## 8. 风险与限制

| 风险 | 处理 |
|---|---|
| 前后摄传感器原生方向不同 | 使用 `AVCaptureDevice.RotationCoordinator`，不硬编码角度；真机已发现前摄硬编码 90° 会 180° 倒立，coordinator 修复；前后摄四象限复测 |
| 前置摄像头镜像/旋转组合 | 前后摄分别验证，保持 data 不镜像、preview 镜像 |
| `RotationCoordinator` 的 horizon-level 角度随重力 | 正常持机下画面保持水平/正立；四象限真机验证；如产品要求严格贴合设备轴向，可改用 iOS 27 的 `videoRotationAngleRelative(toDeviceOrientation:)` |
| 系统方向锁抑制 `UIDevice` 通知 | 实测；必要时切换 CoreMotion，语义仍为忽略旋转锁 |
| UIDevice 通知先于 coordinator KVO，读到旧角度后不再纠正 | iOS 17+ 以 coordinator KVO 为唯一角度来源；KVO 回调 generation 校验；不再在 UIDevice 回调里读角度 |
| 旋转瞬间可能有一帧沿用旧方向 | 更新进行中与 500ms settle 内抑制 push；角度更新之间的帧使用当前角度，不再推送重配中的旧角度帧 |
| data output 角度变更触发 render pipeline 重配并产生暗帧 | 更新期间抑制 push、结束后 500ms settle，再消费一次 skip；恢复帧不是暗帧 |
| 连续旋转多次 skip 造成视频空档 | 2s 滑动窗口合并为一次 skip；所有排队更新结束、settle 结束后才消费；真机验证日志与帧间隔 |
| data output 角度变更触发 render pipeline 重配 | 每次方向切换一次；真机验证卡顿/黑帧，必要时回退到编码器侧 CIImage 旋转（连接固定 portrait） |
| 预览与上行图片方向不一致 | 已确认接受（Android parity）；横屏取景 UI 属于后续独立需求 |
| iOS 15/16 无 `videoRotationAngle` | 使用 `videoOrientation` 回退 |

---

## 9. 备选方案（未采纳）

**方案 B：横屏相机 UI**——相机开启期间解锁 App 横竖屏，UI/预览/采集统一跟随 interface orientation，并重新设计横屏 header/toolbar/预览布局。该方案取景体验更好，但 Android 当前锁定竖屏、没有横屏布局参考，且会牵涉 History/Timeline/Settings 的方向策略；本次不实施，作为后续独立需求。

---

## 10. 验收标准

1. 设备竖持、左横、右横、倒置拍摄时，上行 JPEG 像素均正立，宽高与持机方向一致。
2. 协议 `orientation` 恒为 `0`；video message / AI 回图在各客户端方向正确。
3. 旋转不改变相机会话生命周期：不重启 warmup/throttle、不递增 turn、不切换镜头状态。
4. 预览与 App UI 保持竖屏，关闭相机行为不变。
5. 不改动 SDK、服务端与现有 UI 布局。

---

## 11. 实施记录（2026-10-22）

- 已实现：`CameraDeviceOrientationMonitor`（UIDevice 通知、flat/unknown 忽略、last-valid）、`CameraFrameOrientation` 方向映射、`CameraController` data connection 动态旋转、preview 固定 portrait、失败/停止时清理方向监听。
- 真机修正：初版角度表对前摄硬编码后摄角度，前摄 180° 倒立；已改用 `AVCaptureDevice.RotationCoordinator.videoRotationAngleForHorizonLevelCapture`（iOS 17+），不再硬编码；iOS 15/16 回退 `connection.videoOrientation`。
- 已保持：`ConversationRuntimeFactory` 上行 `orientation = .portrait0`；`CameraImageEncoder` 与 SDK/服务端未改。
- 测试：`CameraOrientationTests` 3 条新增；单元测试全量 338 条通过（1 跳过），相机相关 UI 回归 4 条通过。**2026-10-22 真机已测**：后摄四向正确；前摄曾倒立，改用 `RotationCoordinator` 后前后摄四向均正确。真机日志另发现旋转后首帧偏暗，已增加 skip 保护。
- 连续旋转合并：新增 `CameraOrientationSkipCoalescer`（2s 滑动窗口），同一段连续旋转只武装一次 skip，避免多次 skip 各消费一个 throttle 槽位。窗口内后续方向变化仍逐次更新连接角度；切换/重开相机时重置窗口。
- **Review 修正（2026-10-23）**：
  - iOS 17+ 改为观察 `RotationCoordinator.videoRotationAngleForHorizonLevelCapture` KVO；UIDevice 仅维护状态/日志。修复 UIDevice 通知先于 coordinator 更新时读到旧角度并永久卡住的问题。回调带 generation，切换镜头/停止后的旧回调丢弃。
  - pipeline 增加方向更新抑制：更新期间 `activeOrientationUpdates > 0` 丢弃帧（不消费 throttle），最后更新结束后追加 500ms settle；skip 在更新开始时武装，因抑制不会被旧角度帧提前消费，统一在所有更新+settle 结束后消费。skip 带 2s 时效，避免长时间停顿后误丢健康帧。
  - 方向监听 `start` 不再把初始方向回调为变化；控制器对首个有效通知做 `latest` 比较，修复平放启动后抬起时无角度变化却 skip 首帧；iOS 17+ coordinator 激活后语义回退路径不再写 connection。
  - 版本：iOS 17+ coordinator 创建/读取/销毁均在 `sessionQueue`；KVO 回调按主队列投递并 hop 到 `sessionQueue` 应用。
  - 方向监听 off-main `stop` 的延迟 hop 带 session token；失败后的旧 stop 不会关掉刚重启的监听。
- **待真机复测**：新的 KVO + 抑制/settle 行为、连续旋转只出现一次 skip、旋转中切换镜头、平放启动后抬起。
- **真机日志复核（15:20 会话，合并 skip 之前的版本）**：
  - 后摄：portrait `coordinatorAngle=90` / landscapeLeft `0` / landscapeRight `180`，尺寸 720×1280 / 1280×720。
  - 前摄（前一会话）：portrait `90` / landscapeLeft `180` / landscapeRight `0` / upsideDown `270`，尺寸正确。
  - 每次旋转后日志出现 `frame skipped after orientation change`，下一条 push 为正常大小帧（177KB / 226KB），不再出现 23KB 级暗帧。
  - 当前会话无 `Camera failure`/中断/处理失败；方向变化不递增 turn。
- 待验收：方向锁场景、视频消息图片在 iOS/Android/Web 三端方向、提交前回归。
- 设备日志锚点：`Camera` 的 `device orientation`、`capture rotation ... coordinatorAngle=...`、`frame facing=... WxH ...`。
