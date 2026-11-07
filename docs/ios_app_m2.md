# LivaChat iOS App M2 分析与设计：摄像头与图库

**状态**：P0/P1 已实施并完成真机 E2E（2026-10-20）

> **历史里程碑文档**：后台音频/双 engine 的最终实现以 `docs/ios_app_audio.md`、`docs/ios_background_support.md` 为准；本文中“不启用后台音频”等描述是当时 M1/M2 的历史状态，不再是当前行为。

**日期**：2026-10-20
**上游文档**：
- `docs/ios_app_m1.md`（M1 语音闭环、模块契约、测试基线）
- `docs/ios_app_arch.md`（模块边界、依赖方向、线程模型）
- `docs/ios_sdk_design.md` §7.5（视频上行、turnID、PHPicker/预览层约束）
- `docs/ios_app_ui.md`、`docs/ios_app_settings.md`、`docs/ios_app_shell.md`、`docs/ios_app_audio.md`（M1 模块设计）
- `docs/ios_dev_rules.md`（iOS 15 / SwiftUI / UIKit 例外审批）

**UI/UX 基线**：Android 产品 App 现行实现（不是旧设计稿）：
- `apps/android/LivaChatApp/.../ui/screens/conversation/ConversationScreen.kt`
- `.../conversation/ConversationViewModel.kt`、`ConversationUiState.kt`、`ConversationListenerAdapter.kt`
- `.../media/CameraController.kt`、`CaptureTurnCounter.kt`、`GalleryPhotoSender.kt`、`ImageExif.kt`
- `.../ui/components/VideoPreview.kt`、`BottomToolbar.kt`、`MessageBubble.kt`、`ChatImageView.kt`
- `.../ui/screens/conversation/GalleryPhotoPreviewScreen.kt`、`ImageViewerOverlay.kt`
- `.../ui/screens/settings/SettingsScreen.kt`、`.../permission/PermissionScreen.kt`
- Android 设计记录：`docs/camera_enhance.md` v7.1（§8 首帧暖机、§9 图库上行、§10 选图预览、§11 `camera_facing=gallery`）

> 本文只覆盖 M2：摄像头、图库、视频设置、AI 图片展示。屏幕采集/ReplayKit、模式切换、历史持久化、Agent Job 仍不在范围内。

---

## 1. M2 目标与范围

### 1.1 目标

在 M1 前台语音闭环之上，补齐 Android 现有的视频能力：

```
会话页打开摄像头 → 实时预览 → 约 1fps JPEG 上行（front/back）
→ AI 看到画面并回复（文本/TTS 继续走 M1 链路）
→ 关闭摄像头回到语音视图

摄像头关闭时点「+」→ 系统图库选 1 张 → 全屏预览/缩放
→ 发送 → 1920/q70 规范化 → video 上行（camera_facing=gallery）
→ AI 文本 + 图片回复显示在消息列表

Settings → Video → 默认前置摄像头 / 进入对话自动开摄像头
```

### 1.2 范围分级

| 级别 | 内容 |
|---|---|
| **P0（M2 必做）** | 摄像头权限（可选）；底部工具栏摄像头开关；会话页内实时预览；前后摄像头切换；约 1fps JPEG 上行（16:9 720p、长边 ≤1280、q70、首帧暖机）；`turnID` 采集轮；图库「+」入口；PHPicker 单图选择；全屏选图预览（缩放/返回/取消/发送）；图库照片规范化（长边 ≤1920、q70、EXIF 旋正）与 `camera_facing=gallery` 上行；Settings Video 区（两个开关）；AI 回复中的图片缩略展示 |
| **P1（已实现）** | AI 图片全屏查看器（1x–5x 缩放、左右翻页、单击退出、保存到系统相册） |
| **不做（M2 之外）** | 屏幕采集/录屏浮窗（ReplayKit）、translate/lecture/transcribe/omni 模式、文本输入、History/本地持久化、Markdown 渲染、Agent Job UI、后台摄像头、CallKit/通知、iPad 原生分栏、自定义头像/语言/设备选择器、iOS 15/16 真机冒烟 |

### 1.3 “完成”的判定

- 摄像头开关可用；首次点击触发系统相机授权，拒绝后有明确引导（系统设置）；
- 打开摄像头后会话页显示实时预览；前后摄像头可切换；关闭后恢复语音视图；
- 摄像头帧约 1fps、16:9 720p 上行，服务端返回 `ai_video_ack`；开相机/切换镜头产生新的 `turn_id`；
- 进入对话时按设置自动开摄像头；默认前置/后置设置生效；权限未授权时按 Android 行为尝试开启并显示启动失败横幅；
- 摄像头关闭时「+」可见可点；选图后进入全屏预览，返回可重新选图、取消直接回会话、发送后照片上行为 `camera_facing=gallery`；
- 图库照片触发 chat 回合，AI 文本与回图显示在消息列表；
- Settings 出现 Video 区，两个开关持久化并在下次开相机/进入对话时生效；
- 摄像头在后台停止、回前台按原状态恢复；相机与 M1 语音/AEC 共存不回归；
- 不出现屏幕采集、模式切换、History 等未实现功能的死控件。

---

## 2. 现状分析

### 2.1 Android 现行行为（M2 对齐基线）

| 能力 | Android 事实 |
|---|---|
| 工具栏 | 麦克风、扬声器、摄像头三列 `SpaceEvenly`；摄像头关闭时 caption 行前置「+」，整行可点打开图库；摄像头开启时「+」隐藏且整行不可点（D9） |
| 相机模式 | 摄像头开启后，主内容区由头像 + 消息列表切换为 `VideoPreview`：全屏预览、右上 40dp 圆形切换前后摄按钮（`SurfaceVariant` 80%）、底部 AI 文本浮层与实时转写浮层；顶部状态栏与底部工具栏保留 |
| 相机采集 | CameraX `ImageAnalysis`，目标 1280×960，`KEEP_ONLY_LATEST`；每轮流前 1000ms 丢帧（AE 暖机）；帧间隔 ≥900ms；长边 ≤1280、JPEG q70；按 `rotationDegrees` 旋正后 `orientation=0`；`camera_facing=front/back` |
| turnID | `CaptureTurnCounter` 进程级单例，从 1 递增，0 = 未启用；开相机立即递增；切换镜头在实际推流的 facing 变化后的首帧递增；重连/重建 runtime 不重置 |
| 自动开相机 | Settings `autoCameraOnJoin` 为 true 时，进入对话启动序列后自动开相机；镜头取 `defaultFrontCamera`；开相机同样递增 turnID |
| 图库入口 | 单图 Photo Picker；不需要相册权限；选图后进入全屏预览（黑底、左返回重开 picker、右取消回主屏、底部悬浮「发送」），加载成功后才可发送 |
| 图库编码 | 采样解码 → 长边 ≤1920 → EXIF 八向旋正 → JPEG q70 → `pushVideoJpeg`；`camera_facing=gallery`；只推 1 帧；不递增 turnID |
| 图库触发 | chat 下 `text` 为按 App 语言本地化的相册指令（`conv_gallery_action_text`，英文兜底），复用屏幕抓取的文本回合；omni 下 `text=null`；translate/lecture/transcribe 由服务端模式门控排除（M2 无这些模式） |
| 图库竞态 | 编码完成后、push 前复检摄像头状态；摄像头已开则静默丢弃（REJECTED，不弹错误） |
| AI 回图 | final `ai_text` 携带 `image`；AI 气泡内图片显示在文本上方，12dp 圆角、按原始宽高比填满气泡宽度；点击进入全屏查看器 |
| 图片查看器 | 黑底；当前会话全部 AI 图片可左右翻页；1x–5x 捏合、放大态拖动、单击退出；可保存到系统相册并显示提示 |
| 设置 | Service 之后、General 之前有 Video 区：`Default front camera`、`Auto-start camera on joining a chat` 两个开关，默认均关闭；即时写入本地偏好，不重建 runtime |
| 权限 | 麦克风必需；摄像头可选。Permission 页有麦克风卡片 + 摄像头卡片；首次手动开相机时若无权限则请求；拒绝后弹「无法打开相机」引导去系统设置 |
| 错误 | 相机绑定失败（无权限/撤销/无匹配镜头）→ 相机 UI 复位 + 「相机启动失败，请检查相机权限」；图库编码失败 → 「抓取失败，请重试」；相机竞态拒绝 → 静默 |

### 2.2 iOS M1 现状与差距

| 项目 | iOS M1 事实（代码） | M2 需要的改动 |
|---|---|---|
| 视频能力声明 | `AppContainer` 构造 `LivaChatConfig` 时写死 `enableVideo: false`；`ServerOverride.livaChatConfig` 同样为 false | 改为 `enableVideo: true`（SDK 仅作为 `session.start.config` 能力声明，不阻断上行） |
| SDK 视频上行 | 已具备 `pushVideoJPEG(_:width:height:orientation:cameraFacing:turnID:text:)` 与 `pushVideoImage`；`SessionManager.sendVideo` 在 `sessionStartedOnConnection == false` 时丢弃 | **SDK 不需要改动**；App 直接使用 `pushVideoJPEG` |
| 视频事件 | `LivaChatEvent.videoAck(frameID:processed:)` 已存在；M1 Coordinator 忽略 | M2 仍可忽略（仅日志/测试观测），不阻塞验收 |
| AI 图片 | `LivaChatEvent.aiText` 已携带 `AiImage?`（raw JPEG `Data` + mime/width/height/orientation/cameraFacing）；M1 Coordinator 用 `_` 丢弃，`ChatMessage` 无图片字段 | 透传 image；`ChatMessage` 增加 `image`；AI 气泡显示缩略图 |
| 相机/图库 | 无任何 `AVCaptureSession`、PHPicker、相机权限、视频设置 | 全部新增 |
| 工具栏 | 仅麦克风、扬声器两项 | 增加摄像头列与「+」入口 |
| Permission | 仅麦克风卡片；`AppContainer` 只维护 `micPermission` | 增加可选摄像头卡片与 `cameraPermission` 状态 |
| Settings | Service / General / About；`SettingsStore` 只有 haptic、TLS、首启、地址 | 增加 Video 区与两个 UserDefaults 偏好 |
| Info.plist | 有麦克风/本地网络；无相机/相册 | 增加 `NSCameraUsageDescription`、`NSPhotoLibraryAddUsageDescription` |
| UIKit | M1 已用 Haptics 适配；无相机预览/PHPicker | 新增两个 UIKit 桥接：`AVCaptureVideoPreviewLayer` 宿主、`PHPickerViewController` |
| 生命周期 | `.background` 停音频/释放音频会话；`.active` 恢复 | 相机随音频一起停止/恢复；权限在 `.active` 重新读取 |
| 音频共存 | AVAudioEngine + `.voiceChat` + voice processing | 相机会话必须 video-only，且不得重配 AVAudioSession |

### 2.3 SDK 事实（无需改动，但必须遵守）

- `pushVideoJPEG` 的 `turnID` 类型为 `UInt32`，默认 0（未启用哨兵）；`cameraFacing` 为强类型 `CameraFacing.front/back/screen/gallery`；`orientation` 为 `Orientation.portrait0` 等。
- JPEG 直通路径不做缩放；App 必须自行保证长边上限与质量。
- `pushVideoJPEG` 内部复制 `Data` 并投递到 SDK 视频发送队列，可从相机采集队列调用。
- 视频帧在 `session.start` 完成前会被 SDK 丢弃；App 可以在 `connect` 后立即开相机（与 Android 一致），无需等待 `sessionReady`。
- `AiImage.data` 是 **已 Base64 解码的 JPEG 字节**（与 Android 的 Base64 字符串不同）；`orientation` 可能非 0，需要 App 旋正后显示。
- `enableVideo` 只写 `session.start.config`，不影响 SDK 是否接受视频帧。

---

## 3. 设计决策

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | 相机使用 `AVCaptureSession`（video-only）+ `AVCaptureVideoDataOutput`；预览用 `AVCaptureVideoPreviewLayer` | iOS 无 CameraX；SDK 设计已定；不引入音频输入避免与 `AVAudioEngine` 冲突 |
| D2 | 采集会话不包含音频 I/O，并设置 `automaticallyConfiguresApplicationAudioSession = false` | 保持 M1 `.playAndRecord` / `.voiceChat` / AEC 配置不被相机改寫 |
| D3 | 帧参数：约 1fps、16:9 720p（`.hd1280x720`，竖屏 720×1280）、开流前 1s 暖机、长边 ≤1280、q70、像素旋正且 `orientation=.portrait0` | 16:9 为 review 拍板（原生 720p preset、功耗更低）；其余对齐 Android |
| D4 | `CaptureTurnCounter` 放 `AppContainer`（进程级），开相机递增；切镜头在“新 facing 首帧”递增；后台恢复/重建 runtime 不重置 | 对齐 Android `CaptureTurnCounter`；避免旧镜头残帧被标成新 turn |
| D5 | 图库使用 `PHPickerViewController`（iOS 15 无 SwiftUI 等价物），单图；ImageIO 解码/旋正/编码；长边 ≤1920、q70；`cameraFacing = .gallery`；`text` 使用按 App 语言本地化的相册指令 `conv_gallery_action_text`（17 locale，英文兜底；Android `conv_image_action_text` 已扩至 17） | SwiftUI PhotosPicker 需要 iOS 16；PHPicker 不需要相册全量权限；与服务端已落地的 gallery 语义一致；2026-10-27 产品确认指令随 App 语言本地化（Android 已同步使用 `conv_image_action_text`（2026-10-27），见 `docs/android_multilingual.md` §2.3.9） |
| D6 | 图库只在摄像头关闭时开放；编码完成后、push 前再次检查摄像头状态 | 对齐 Android D9；避免选图期间开相机的竞态 |
| D7 | AI 图片：`ChatMessage` 增加 `image: AiImage?`；final `ai_text` 的图片显示在 AI 气泡文本上方 | 图库主链路会回图；不做则与 Android 视觉不一致 |
| D8 | Settings Video 两个开关写入 UserDefaults，不触发 runtime 重建；默认值均为 false | 与 Android 一致；只在下次开相机/进入对话时读取 |
| D9 | 摄像头为可选权限：Permission 页新增可选卡片；手动开相机时才请求系统权限；自动开相机不主动请求，权限不足时按 Android 行为尝试开启并收敛为启动失败横幅 | 避免启动即弹可选权限；麦克风仍是唯一阻塞权限；自动开启的失败反馈对齐 Android |
| D10 | UIKit 例外只用于两处：`PHPickerViewController` 封装、`AVCaptureVideoPreviewLayer` 的 `UIViewRepresentable` | 均无 SwiftUI 等价能力；按 `ios_dev_rules.md` §4 在方案中报批 |
| D11 | 相机 UI 状态单一写入者：ViewModel 表达“用户意图”，CameraController 只回报失败 | 规避 Android v2/v3 的 isCameraOn 双写闪烁问题 |
| D12 | AI 图片全屏查看器/保存到相册列为 P1；P0 只做气泡缩略图 | 先保证相机/图库主链路；查看器手势与相册权限可独立追加 |

---

## 4. 架构设计

### 4.1 Runtime 组成（M2 增量）

```
AppContainer
 ├─ SettingsStore（+ defaultFrontCamera / autoCameraOnJoin）
 ├─ CredentialStore / ServerProbe
 ├─ CaptureTurnCounter            // 进程级，跨 runtime 重建保留
 ├─ CameraPermissionProvider      // AVCaptureDevice.authorizationStatus
 ├─ ConversationRuntime
 │    ├─ LivaChatSDK               // enableVideo: true
 │    ├─ ConversationEventAdapter
 │    ├─ ConversationCoordinator
 │    │    ├─ AudioCaptureController / AudioPlaybackController
 │    │    ├─ CameraController     // AVCaptureSession + frame pipeline
 │    │    └─ GalleryPhotoSender   // ImageIO encode + pushVideoJPEG
 │    └─ ...
 └─ PlatformAdapters
      ├─ CameraPreviewView         // UIViewRepresentable → AVCaptureVideoPreviewLayer
      └─ PHPickerView               // UIViewControllerRepresentable → PHPickerViewController
```

规则：

- `CameraController`、`GalleryPhotoSender` 随 runtime 创建/销毁；它们通过闭包调用当前 `LivaChatSDK`，不在全局单例中持有 SDK。
- `CaptureTurnCounter` 由 `AppContainer` 持有并注入 runtime；设置变更重建 runtime 时计数不归零（与 Android 进程级单例一致）。
- `ConversationViewModel` 不直接接触 `AVCaptureSession` / `AVCaptureVideoPreviewLayer` / `PHPickerViewController`；只暴露值状态、用户动作，以及一个 `CameraPreviewing` 协议对象给视图。
- `CameraPreviewView` 适配层接收 `CameraPreviewing`，把 `AVCaptureVideoPreviewLayer` 附着到宿主；Feature 层不 import AVFoundation。
- `SettingsStore` 通过 `CameraPreferencesProviding` 注入 Coordinator；Camera 读取 `defaultFrontCamera` / `autoCameraOnJoin`，不直接依赖 Settings 模块 UI。
- `ConversationEventAdapter` 仍是 `sdk.events` 唯一消费者。
- 相机帧直接由 `CameraController` 调 `sdk.pushVideoJPEG`（与 Android 同层），不经过 ViewModel/Coordinator 转发，避免主线程负担。

### 4.2 模块边界

| 模块 | M2 职责 | 位置建议 |
|---|---|---|
| Camera | 权限、采集会话、前后台启停、切镜头、帧节流/暖机/编码、turnID | `Core/Video/CameraController.swift`、`CaptureTurnCounter.swift`、`CameraProtocols.swift` |
| Gallery | PHPicker 结果接收、预览状态、ImageIO 编码、单帧上行、竞态拒绝 | `Core/Video/GalleryPhotoSender.swift`、`GalleryModels.swift` |
| Platform Adapters | 相机预览层宿主、PHPicker 呈现 | `PlatformAdapters/CameraPreview/CameraPreviewView.swift`、`PlatformAdapters/PHPicker/PHPickerView.swift` |
| Conversation Feature | 工具栏摄像头/图库入口、相机模式布局、选图预览、AI 图片气泡/查看器 | `Features/Conversation/...` |
| Settings | Video 区与偏好持久化 | `Core/Settings/SettingsStore.swift`、`SettingsViewModel.swift`、`Features/Settings/SettingsView.swift` |
| Permission | 可选摄像头卡片与状态刷新 | `Features/Permission/PermissionView.swift`、`App/AppContainer.swift` |

依赖方向保持 `Features → ViewModel → Domain → Media → SDK`；UIKit 仅出现在 PlatformAdapters。

### 4.3 测试缝（窄协议）

| 协议 | 生产实现 | 测试用途 |
|---|---|---|
| `CameraCapturing` | `CameraController` | fake 记录 start/stop/switch；UI 测试不碰硬件 |
| `CameraPreviewing` | `CameraController`（通过 CALayer 附件） | UI 测试提供静态预览占位 |
| `CameraPermissionProviding` | `AVCaptureDevice` 包装 | 注入 granted/denied/undetermined |
| `CameraPreferencesProviding` | `SettingsStore` | 注入 defaultFrontCamera / autoCameraOnJoin |
| `PhotoPicking` | `PHPickerView` 回调 | UI 测试注入固定图片 Data |
| `GalleryEncoding` | ImageIO 实现 | 单测注入尺寸/失败；验证三态 |
| `PhotoLibraryAuthorizing`（P1） | Photos 框架包装 | 查看器保存测试 |

### 4.4 线程与并发

- `CameraController`：`sessionQueue`（串行）负责 `beginConfiguration/commitConfiguration/startRunning/stopRunning`；`videoQueue`（串行）接收 `AVCaptureVideoDataOutput` 样本；两者独立，避免配置阻塞帧回调。
- 帧编码在 `videoQueue` 完成；`pushVideoJPEG` 线程安全。
- MainActor：ViewModel 状态、权限、失败回调、预览层 attach/detach。
- `CameraController` 以 `final class ...: NSObject, @unchecked Sendable` + 串行队列/锁实现（与 `AudioCaptureController` 风格一致），Swift 6 严格并发下不得跨队列裸读写可变状态。
- `CaptureTurnCounter` 内部加锁；`GalleryPhotoSender` 编码在后台队列，完成后回 MainActor 更新 UI。
- 预览层只被 MainActor attach/detach；不在帧回调触碰 UI。

### 4.5 新增/修改文件（建议）

```
apps/ios/LivaChatApp/
  Core/Video/
    CameraController.swift
    CameraProtocols.swift
    CaptureTurnCounter.swift
    CameraFramePolicy.swift        // 暖机/节流/尺寸纯函数
    GalleryPhotoSender.swift
    GalleryImageEncoder.swift      // ImageIO
    CameraImageDecoder.swift       // AiImage / gallery preview 解码
  PlatformAdapters/
    CameraPreview/CameraPreviewView.swift
    PHPicker/PHPickerView.swift
  Features/Conversation/
    CameraPreviewPanel.swift
    GalleryPreviewView.swift
    AIImageBubble.swift
    ImageViewerOverlay.swift       // P1
  Resources/
    Info.plist                     // + NSCameraUsageDescription
    Localizable.xcstrings          // + M2 文案
```

修改：`AppContainer`、`AppLaunchOptions`、`ConversationView`、`ConversationViewModel`、`ConversationModels`、`ConversationCoordinator`、`ConversationRuntime`、`ConversationRuntimeFactory`、`ConversationProtocols`、`SettingsStore`、`SettingsViewModel`、`SettingsView`、`PermissionView`、`AppContainerTests` 等。

---

## 5. 摄像头设计

### 5.1 权限模型

```swift
enum CameraPermissionState: Equatable {
    case unknown
    case undetermined
    case granted
    case denied
}
```

- 生产实现读取 `AVCaptureDevice.authorizationStatus(for: .video)`；`.restricted` 归入 `denied`。
- `AppContainer` 在 `.active` 刷新，并同步给 `ConversationViewModel.cameraPermission`（与 mic 权限同一模式）；Permission 页 `onAppear` 刷新。
- Permission 页新增“摄像头权限（可选）”卡片：
  - `granted` 显示 ✓；
  - `undetermined` 点击“授权”调用 `AVCaptureDevice.requestAccess(for: .video)`；
  - `denied` 点击直接打开系统设置（避免无效请求）。
- 手动开相机时：
  - `granted` → 开；
  - `undetermined` → 请求，granted 后继续开，denied 后弹引导；
  - `denied` → 弹引导，不尝试开。
- 引导弹窗（对齐 Android 文案）：标题 `conv_camera_unavailable`，正文 `conv_camera_permission_guide`，按钮「取消」/「前往系统设置」。
- 自动开相机（`autoCameraOnJoin`）：
  - 不主动请求系统权限（与 Android 一致）；
  - 尝试执行打开序列；若权限非 `granted`，`CameraController.start` 失败 → 复位相机 UI（`isCameraOn=false`、预览隐藏）并显示 `conv_camera_start_failed` 横幅（对齐 Android `onBindFailed` 路径）；
  - 横幅动作提供「前往系统设置」，不自动重试。
- 相机使用过程中权限被回收：`.active` 刷新到非 granted 时，停止相机、复位相机 UI，并显示引导（不自动重建 runtime）。

### 5.2 采集会话

```swift
final class CameraController: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "livachat.camera.session")
    private let videoQueue = DispatchQueue(label: "livachat.camera.video")
    // preview / facing / generation / counters ...
}
```

配置：

- `session.sessionPreset = .hd1280x720`（16:9 720p；竖屏缓冲 720×1280）；设备不支持时回退 `.high`。这是 review 拍板的定稿值，与 Android 的 4:3 1280×960 是明确差异。
- 输入：仅 `AVCaptureDeviceInput`（`.builtInWideAngleCamera`，front/back），**不加音频输入**。
- 输出：`AVCaptureVideoDataOutput`
  - `alwaysDiscardsLateVideoFrames = true`
  - `videoSettings[kCVPixelBufferPixelFormatTypeKey] = kCVPixelFormatType_32BGRA`
  - delegate 队列 = `videoQueue`
  - 方向：data output 与预览层连接都设 `.portrait`。先检查 `isVideoOrientationSupported`；支持则设置（AVFoundation 会物理旋正 data output 缓冲；iOS 17+ 用 `videoRotationAngle` 并检查 `isVideoRotationAngleSupported`，iOS 15/16 用 `videoOrientation`）；不支持则在编码前按镜头 position 手动旋转（映射以 spike 实测为准）
  - data output 连接 `isVideoMirrored = false`（先检查 `isVideoMirroringSupported`；上行不镜像，对齐 Android analysis）；前置预览镜像、后置不镜像
  - 切换输入后重新设置 orientation/mirroring（commit 后连接属性可能被重置）
- 帧率：**不修改设备或连接的帧率**——AVFoundation 的帧率由相机硬件共享，限制 data output 也会拖慢预览；相机按系统帧率运行，在 delegate 内按 900ms 节流丢弃上行帧。
- `session.automaticallyConfiguresApplicationAudioSession = false`。
- `beginConfiguration/commitConfiguration` 包住输入替换与 preset 设置；`startRunning/stopRunning` 只在 `sessionQueue` 调用。
- 预览宿主：`UIView` 子类以 `layerClass = AVCaptureVideoPreviewLayer.self` 承载，`videoGravity = .resizeAspectFill`；不手动 addSublayer，避免尺寸/旋转错位。
- 通知：`AVCaptureSession.wasInterruptedNotification`（标记中断、暂停上行并记录原因）、`interruptionEndedNotification`（若相机仍应为开，尝试重启一次）、`runtimeErrorNotification`/媒体服务重置（停止并回报失败，UI 映射为 `conv_camera_start_failed` 横幅）。
- 生成号（generation）：每次 start/stop/切镜头递增；帧回调与异步配置回调只在 generation 匹配时生效，丢弃旧会话残帧（对齐 Android `bindGeneration`）。

### 5.3 帧管线

```
CVPixelBuffer
  → CIImage / CGImage
  → 长边 >1280 时等比缩小（只缩不放）
  → JPEG q70
  → sdk.pushVideoJPEG(data, width, height,
        orientation: .portrait0,
        cameraFacing: .front/.back,
        turnID: counter.current,
        text: nil)
```

规则：

- 开流后前 **1000ms** 丢弃 data output 帧（AE 收敛暖机，对齐 Android `WARMUP_MS`）；预览层不受影响；每次 start/切镜头/后台恢复重新计窗。
- 正常帧间隔 ≥ **900ms**；节流时间戳随每次 start/切镜头重置；只影响上行，不拖慢预览。
- 像素已按竖屏方向旋正；协议 `orientation` 恒 `.portrait0`。
- `CIContext` 与缩放用的 `CGContext` 复用，不在每帧创建；单帧转换/缩放/编码全部在 `videoQueue` 完成。
- 单帧编码失败：丢弃并记日志，不停止会话；连续失败可计数并在调试日志告警。
- 帧回调不得触碰 UI；只调用 SDK。
- 开关相机/切镜头期间旧帧必须丢弃：回调入口校验 generation，编码完成后、`pushVideoJPEG` 前再校验一次，避免“旧画面携带新 facing/turnID”。
- 不处理 `videoAck`（M2 仍忽略）；如需诊断，可仅在日志中记录丢帧区间。

### 5.4 turnID

```swift
final class CaptureTurnCounter: @unchecked Sendable {
    private var value: UInt32 = 0
    // next(): 从 1 开始递增；溢出时回到 1；0 永远不作为有效值
}
```

- `AppContainer` 持有一个实例，注入每次重建的 runtime。
- **开相机**：由 `ConversationCoordinator` 在打开序列中立即 `next()`；ViewModel 只表达用户意图，不直接操作计数器或相机。
- **切镜头**：CameraController 记录 `lastPushedFacing`；当实际推送帧的 facing 与上次不同时 `next()`，与“新 facing 首帧”同帧生效（对齐 Android `shouldBumpTurn`）。每次**打开**相机时重置 `lastPushedFacing`，避免“开相机已递增 + 首帧 facing 不同再递增”的双跳。
- **后台恢复 / runtime 重建 / 重连**：不递增；同一采集轮延续。
- 图库发送使用 `counter.current`，**不递增**。
- turnID 是服务端“新采集轮→立即起一轮”的唯一依据；任何遗漏都只应导致该优化不触发，不能导致错误标注。

### 5.5 开关、切换与自动开启

- ViewModel 状态（单一写入者）：
  - `isCameraOn: Bool`
  - `isFrontCamera: Bool`
  - `isCameraPreviewVisible: Bool`（与 `isCameraOn` 同写，恒等；保留字段便于布局/转场）
  - `cameraPermission: CameraPermissionState`
  - `galleryPickerPresented: Bool`、`galleryPreview: GalleryPreviewState?`
  - （P1）`imageViewerState`；错误统一走现有 banner
- 职责：ViewModel 负责权限交互与 UI 意图；`ConversationCoordinator` 负责 `counter.next()` 与 `camera.start/stop/switch`；`CameraController` 只回报失败，不回写相机开关状态。
- 打开：
  1. 权限检查/请求；
  2. `isFrontCamera = settings.defaultFrontCamera`；
  3. `isCameraOn = true`、预览可见；
  4. Coordinator `counter.next()`；
  5. Coordinator `camera.start(front:)`（异步；失败回调复位 UI + banner）。
- 关闭：`camera.stop()` → `isCameraOn = false`，清空 `cameraResumeIntent`；不重建音频/连接。
- 切换：翻转 `isFrontCamera`，调用 `camera.switchCamera(front:)`；预览层不变，只替换输入并更新镜像；不重建整个视图（避免 Android v3 的闪烁路径）。`CameraPreviewView` 必须保持稳定 identity，View 状态更新不得触发预览宿主/Session 重建。
- 自动开启：`ConversationCoordinator.start()` 完成音频启动并调用 `sdk.connect()` 后，若 `autoCameraOnJoin`，**跳过权限请求与前置拦截**，直接执行打开序列的步骤 2–5（含 `counter.next()`）；权限不足由 `camera.start` 失败，按打开失败路径复位相机 UI 并显示 `conv_camera_start_failed` 横幅（对齐 Android）。
- 相机开启时消息列表与头像隐藏；关闭后恢复，消息/图片保留在内存。
- 相机开关不触发生成新会话、不影响 `sessionActive`。

### 5.6 前后台与终止

| 场景 | 行为 |
|---|---|
| `.background` | 若相机开启：`camera.stop()`；`cameraResumeIntent = true`；音频按 M1 停止 |
| `.active` | 重新读相机权限；若 `cameraResumeIntent && granted && session 未结束`：`camera.start(front: 当前朝向)` 并清空标记；**不递增 turnID**；暖机重新生效。若权限非 granted、session 已结束或正在等待认证修复，则保持相机关闭、清空标记并复位 UI |
| 来电/音频中断 | 音频按 M1 停止；相机同时停止并置 `cameraResumeIntent = true`；中断结束且前台/session 未结束时恢复相机并清空标记，**不递增 turnID**。若系统中断相机（多任务占用），停止并复位相机 UI |
| 打开 Settings | **先停止相机并复位 `isCameraOn=false`，同时清空 `cameraResumeIntent`**，避免页面被覆盖后仍采集/上行、或返回后误恢复；音频与会话按 M1 继续（进入 Settings 不停止 runtime）；返回会话后不自动重开，需用户手动开启 |
| 麦克风权限丢失 | 按 M1 停止音频；同时停止相机、复位 `isCameraOn` 并清空 `cameraResumeIntent`，避免“无麦克风仍在采集视频”的残留状态 |
| `serverClosed` / `sessionEnded` / `reconnectExhausted` / `authFailed` | 与停止媒体一致：关闭相机、复位 `isCameraOn`、清空 `cameraResumeIntent`；保留 `defaultFrontCamera` 偏好 |
| 设置重建 runtime | 先 `camera.stop()`，清空相机状态；`CaptureTurnCounter` 不重置；新 runtime 不自动重开相机（除非 auto 开启且权限允许，按进入对话规则） |
| 退出会话/停止 runtime | `camera.stop()`、清空 `cameraResumeIntent`；无后台采集 |

### 5.7 错误与文案

- 相机无设备/无权限/绑定失败：`showBanner(.fatal(.server(titleKey: "conv_camera_start_failed", detail: ...), action: .openSettings))` 或专用 banner message；同时 `isCameraOn = false` 并清空 `cameraResumeIntent`。
- 权限拒绝：弹窗引导系统设置（§5.1）。
- 会话运行时错误：停止相机 + 错误 banner；清空恢复标记，不自动无限重启（最多一次重建尝试）。
- 所有失败必须收敛为 UI 复位，不允许“图标亮、画面黑”。

### 5.8 日志

- 相机链路沿用 `AppLog`：
  - `camera start front=true preset=hd1280x720`
  - `camera frame facing=front w=720 h=1280 bytes=... turn=3`
  - `camera switch front=false` / `camera stop reason=background`
  - `camera warmup skipped` / `camera frame dropped`
- 不记录图片内容、不记录相册路径。

---

## 6. 图库设计

### 6.1 入口与可见性

- 底部工具栏摄像头按钮的 caption 行前置「+」（SF Symbol `plus`，16pt，`textSecondary`），整行可点打开图库。
- `showGalleryEntry = !isCameraOn`：摄像头开启时「+」隐藏、caption 行不可点。
- 无障碍标签复用 `conv_avatar_from_gallery`（“从相册选择”），只在入口实际显示时暴露。
- 相机模式/图库预览期间不显示其他未实现控件。

### 6.2 状态机

```
idle ──tap +──▶ picking ──选中图片──▶ previewing
  ▲                │                      │
  │                └──取消───────────────┐│
  │                                       ││
  ├────────────cancel─────────────────────┘│
  │                                        │
  ├────────────send────────────────────────┤
  │                                        │
  └────────────back（重新打开 picker）◀────┘
```

细化（对齐 Android v6.4）：

- 工具栏点「+」→ `picking`（sheet 呈现 PHPicker）。
- picker 选中图片 → 关闭 picker，进入 `previewing`。
- picker 取消：
  - 从工具栏进入 → 回 `idle`（会话页原样）；
  - 从预览“返回”进入 → 回 `idle`（关闭预览，不停留在旧预览）。
- 预览左上「返回」→ 关闭预览并重新打开 picker；picker 再取消 → `idle`。
- 预览右上「取消」→ `idle`。
- 预览「发送」→ 立即关闭预览，进入编码/上行（`idle`），不阻塞 UI。
- 系统返回手势/按钮：iOS 15 全屏覆盖层不提供系统返回；以左上返回按钮为准，禁用下滑关闭（`.interactiveDismissDisabled()`）。
- **呈现串行化**：picker 与 preview 不并存。预览返回时先在 `fullScreenCover.onDismiss` 中关闭预览，再在该回调里打开 picker；picker 选中后再关闭 picker 并在其 `onDismiss` 里打开预览。不得同时写两个 presentation 布尔值，否则 SwiftUI 会出现 “already presenting”/丢失呈现。
- **单次事件**：发送/取消/返回为一次性动作；发送后忽略重复点击与返回，直到状态回到 `idle`。

### 6.3 Picker

- `PHPickerViewController`：
  - `PHPickerConfiguration(photoLibrary: .shared())`
  - `filter = .images`
  - `selectionLimit = 1`
  - `preferredAssetRepresentationMode = .current`
- 结果通过 `NSItemProvider.loadDataRepresentation(forTypeIdentifier: UTType.image.identifier)` 读取原始数据（保留 EXIF，供 ImageIO 旋正）；若 provider 未注册通用 `public.image`，回退到第一个 `conforms(to: .image)` 的 registered type；不在 UI 线程解码。
- 不支持 Live Photo 动画、GIF 多帧（取首帧）、视频。
- 不需要 `NSPhotoLibraryUsageDescription`（PHPicker 独立选择器）。
- iCloud 未下载照片可能加载较慢：预览显示 loading，失败显示 `conv_gallery_preview_failed`；可取消。

### 6.4 全屏预览

对齐 Android `GalleryPhotoPreviewScreen`：

- 黑底全屏。
- 顶部：左上返回（重开 picker）、右上「取消」（回会话）；无中间标题/提示。
- 内容：图片 fit 居中；加载中 `ProgressView`；失败显示 `conv_gallery_preview_failed`。
- 手势：捏合 1x–5x；放大态单指拖动平移；不双击缩放、不单击退出。
- 底部悬浮胶囊按钮「发送」：
  - 加载成功才可点；
  - 灰底白字（`surfaceVariant` + `textPrimary`），禁用态降低透明度；
  - 高 44pt，水平内边距 32pt。
- 预览期间不修改摄像头/音频状态。

### 6.5 图片规范化（ImageIO）

| 步骤 | 规则 |
|---|---|
| 读取 | `CGImageSourceCreateWithData`；失败 → FAILED |
| 方向 | 读取 `kCGImagePropertyOrientation`，用 `CGImageSourceCreateThumbnailAtIndex` 的 `kCGImageSourceCreateThumbnailWithTransform = true` 生成正立图 |
| 尺寸 | 采样/缩略到长边 ≤1920（只缩不放），保证像素正立 |
| 编码 | ImageIO `CGImageDestination`，JPEG q70 |
| 输出 | `Data` + 正立后的 `width/height`；协议 `orientation=.portrait0` |
| 失败 | 解码/编码失败、空数据、超大图 → FAILED；不崩溃、不弹系统错误 |

约束：

- 透明 PNG → JPEG 透明区域变黑（与 Android 记录一致）。
- HDR/广色域/HEIC 由系统 ImageIO 解码，统一输出 8-bit JPEG。
- EXIF/GPS 不随重编码上行（隐私正向）。
- 编码在后台队列完成；不阻塞主线程。
- 发送只推 **1 帧**，不周期推送。

### 6.6 上行与竞态

```swift
sdk.pushVideoJPEG(
    jpeg,
    width: encoded.width,
    height: encoded.height,
    orientation: .portrait0,
    cameraFacing: .gallery,
    turnID: turnCounter.current,
    text: GalleryPhotoEncoder.actionText   // L10n.t("conv_gallery_action_text")
)
```

- `text` 按 App 当前语言从 string catalog 解析，英文兜底；不再与 Android 中文常量相同（跨端差异）。M2 只有 chat，无 omni 分支。
- 编码完成后、push 前复检 `isCameraOn == false`；若已开相机 → `REJECTED`，静默丢弃并记日志，不弹错误。
- 结果三态：
  - `SENT` → 无 UI；
  - `REJECTED` → 无 UI；
  - `FAILED` → 可恢复错误 banner（`cap_bubble_failed`：“抓取失败，请重试”），5s 自动消失。
- 如果 `sessionReady == false`，SDK 会丢弃该帧；与 Android 一致，不额外禁用入口。可在日志中记录“video dropped before session start”。
- 不在本地插入用户图片消息（与 Android 一致）；服务端 final `ai_text` 的图片显示在 AI 气泡。

### 6.7 AI 图片展示

**P0：气泡缩略图**

- `ChatMessage` 增加 `image: AiImage? = nil`；`finishAssistantMessage` 支持图片参数；final `ai_text` 且 `image != nil` 时附加。
- final 消息即使 `text` 为空、只要 `image != nil` 也要保留（气泡只显示图片）；`appendMessage` 当前的“空文本丢弃”守卫需放行 image-only 消息。
- AI 气泡内图片位于文本上方：
  - 宽度填满气泡内容宽度（气泡最大宽度仍约为屏宽 75%）；
  - 按解码后的 `width/height` 保持宽高比；
  - 12pt 圆角；文本与图片间距 8pt；
  - 解码失败则不显示图片，不影响文本。
- 解码：`AiImage.data` → ImageIO → CGImage；若 `AiImage.orientation != 0`，按角度旋正后显示。解码在后台队列完成，按 `messageID` 缓存 CGImage/失败结果（`clearConversation()` 时清空），禁止在 SwiftUI `body` 内同步解码。
- 图片不参与流式更新（SDK 约定 final 才带图）；final 后一次性显示。Coordinator 的 `resetAIStreaming()` 同时清空已缓存的图片，断连/新会话不会把旧图带到下一条 final。
- **P0 缩略图不绑定点击**；P1 查看器实现后再加上 tap，避免死控件。
- 内存：图片 Data 随 `ChatMessage` 保留到 `clearConversation()`；M2 不落盘。长会话图片较多时存在内存增长风险，见 §13。

**P1：全屏查看器**

- 点击 AI 气泡图片进入黑底全屏查看器；查看当前会话全部 AI 图片，支持左右翻页。
- 1x–5x 捏合缩放；放大态拖动；fit 态左右滑动翻页；单击退出。
- 底部/右上保存按钮：首次点击请求“添加到照片”权限（`.addOnly`），保存成功显示 `common_saved_to_gallery`，失败显示 `common_save_failed`。保存使用已旋正的 CGImage 重新编码 JPEG，不直接写 `AiImage.data`（否则 orientation 非 0 的图会横躺）。
- Info.plist 增加 `NSPhotoLibraryAddUsageDescription`；不请求完整相册访问。
- 手势冲突处理：放大态禁用翻页；翻页落定后重置缩放。
- 若实现成本超预期，可先只支持单图查看（无翻页），但需在 review 中明确降级。

---

## 7. Settings / 权限 / 平台配置

### 7.1 Video 设置区

位置：Service 区之后、General 区之前（对齐 Android `SettingsScreen.kt`）：

```
视频
  默认前置摄像头                    [开关]
  开启视频时默认使用前置摄像头
  进入对话时自动开启摄像头          [开关]
  每次开始对话时自动启用视频
```

- 与 General 的 Haptic 行同构：标题 body、副标题 caption、`surface` 卡片、`surfaceVariant` 描边、16pt 圆角、右侧 `Toggle`。
- 开关即时保存，不出现保存按钮，不触发 runtime 重建（与 Haptic 一致）。
- 默认值均为 `false`（Android `SettingsUiState` 默认）。

### 7.2 存储

| 数据 | 存储 | Key | 默认 |
|---|---|---|---|
| 默认前置摄像头 | UserDefaults | `settings.camera_default_front` | false（后置） |
| 进入对话自动开摄像头 | UserDefaults | `settings.camera_auto_on_join` | false |
| AI 图片保存权限（P1） | 系统 Photos 授权 | — | 未请求 |

- `SettingsStore` 增加两个 `@Published` 属性，`didSet` 写 UserDefaults。
- `SettingsViewModel` 直接绑定（同 haptic），无需 dirty/apply。
- `defaultFrontCamera` 在每次**打开相机**时读取；`autoCameraOnJoin` 在 `Coordinator.start()`（进入对话/冷启动）读取。修改后：
  - 默认前置 → 下次开相机生效；
  - 自动开摄像头 → 下次进入对话生效（当前会话不立即开）。
- 与 Android 差异：Android 从 Settings 返回会话会重新触发进入屏幕逻辑，auto 开关可能立即开相机；iOS M1 进入 Settings 不停止 runtime，本设计只在下次进入对话（或冷启动）生效。
- 不因 Video 开关重建 runtime，不影响服务器配置保存流程。

### 7.3 Info.plist / InfoPlist.strings

| Key | M2 |
|---|---|
| `NSCameraUsageDescription` | 必需；en/zh-Hans 本地化 |
| `NSPhotoLibraryUsageDescription` | 不加（PHPicker 不需要） |
| `NSPhotoLibraryAddUsageDescription` | 已添加（查看器保存图片） |
| `NSMicrophoneUsageDescription` | 保留 |
| `NSLocalNetworkUsageDescription` | 保留 |
| `UIBackgroundModes: audio` | 不启用 |
| ATS | 不变 |

建议相机用途文案（与 Android `perm_camera_desc` 语义一致）：

- en: `So I can see your surroundings — you can turn it on later.`
- zh-Hans: `让我看见你的周围环境，需要时再打开也可以。`

### 7.4 UIKit 例外审批（按 `ios_dev_rules.md` §4）

| 场景 | 为什么 SwiftUI 不可行 | 影响范围 |
|---|---|---|
| `PHPickerViewController` | iOS 15 无 SwiftUI PhotosPicker（iOS 16+） | 单文件 `PlatformAdapters/PHPicker/PHPickerView.swift`；不外泄 UIKit 类型 |
| `AVCaptureVideoPreviewLayer` 宿主 | SwiftUI 无相机预览等价物 | 单文件 `PlatformAdapters/CameraPreview/CameraPreviewView.swift`；仅接受协议对象 |

两者均为 `UIViewControllerRepresentable` / `UIViewRepresentable` 薄封装，业务/状态层不 import UIKit/AVFoundation 预览类型。

---

## 8. UI/UX 设计

### 8.1 Permission 页（新增摄像头卡片）

```
开始之前，需要你的许可

┌──────────────────────────────┐
│ 🎤 麦克风权限          [授权] │
│    让我听见你的声音…          │
└──────────────────────────────┘
┌──────────────────────────────┐
│ 📷 摄像头权限 (可选)   [授权] │
│    让我看见你的周围环境…      │
└──────────────────────────────┘

        [       开始聊天       ]
           前往系统设置
```

- 卡片样式与 M1 麦克风卡片完全一致；已授权显示 ✓。
- 两张卡片 + 按钮在 iPhone SE / 大字号下不得截断：Permission 内容改为可滚动布局（Android 已使用 verticalScroll）。
- 摄像头卡片不阻塞“开始聊天”；麦克风仍是必需权限。
- 摄像头 `denied` 时卡片按钮直接打开系统设置。
- 不申请相册权限（PHPicker）。

### 8.2 底部工具栏

```
[ 🎤 麦克风 ]   [ 🔊 扬声器 ]   [ + 📷 摄像头 ]
```

- 三列 `SpaceEvenly`；48pt 圆形按钮 + 12pt caption；激活透明背景、无描边、图标 `brandIndigo`，关闭 1pt `surfaceVariant` 描边 + `textSecondary` 图标（Android `BottomToolbar` parity）；按压缩放 0.92。大字号下 caption 允许换行/截断，图标与触控区保持固定且不重叠。
- 摄像头图标：开 `camera.fill`、关 `camera.slash.fill`（以 SF Symbols 实际可用名为准）。
- 「+」只在摄像头关闭时出现；点击整行 caption 打开图库。
- 摄像头列必须拆成“图标按钮（开关相机）+ caption 行按钮（打开图库）”，不能把 Button 嵌套在 Button 内；plus 隐藏时 caption 行不可点。
- 无障碍：摄像头按钮 label `conv_toolbar_camera_on/off`；「+」label `conv_avatar_from_gallery`；两者触控高度 ≥44pt（caption 行用 contentShape 扩展）。

### 8.3 相机模式布局

摄像头开启后，替换 M1 的“头像 + 消息区”，其余保持不变：

```
┌─────────────────────────────────────┐
│  ● 随时可以说                   ⋯   │  顶部状态栏（保留）
├─────────────────────────────────────┤
│                                     │
│         摄像头实时预览               │  Fill 居中
│                                     │
│                    ┌────────────┐   │  右上 40pt 圆形按钮
│                    │ ⇄ 切换镜头 │   │  SurfaceVariant 80%
│                    └────────────┘   │
│                                     │
│  ┌───────────────────────────────┐  │  AI 文本浮层（最近一条 final AI）
│  │ AI 文本（最多 2/3 高度，可滚） │  │  SurfaceVariant 70%，12pt 圆角
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │  实时转写（复用 M1 浮层）
│  │ 用户实时转写…                  │  │  BrandIndigo 88%
│  └───────────────────────────────┘  │
├─────────────────────────────────────┤
│  [🎤 麦克风] [🔊 扬声器] [📷 摄像头] │
└─────────────────────────────────────┘
```

- 预览 `resizeAspectFill`，超出裁切；黑底。
- 切换镜头按钮：视觉 40pt 圆形、可点区域 ≥44×44pt，`surfaceVariant` 80% 背景，白色图标；label `conv_switch_to_front/back_camera`。
- AI 文本浮层显示规则（严格对齐 Android `ConversationScreen` 相机分支）：
  - 进入相机模式 / 切换镜头时记录当时最后一条 AI 消息 id；
  - 若进入后出现新的 final AI 消息 → 显示其文本；否则若进入前无 AI 消息 → 显示最后一条 AI 文本；否则不显示；
  - 只显示 final AI 消息，不显示流式中间态（Android 现状）；
  - 文本 16/24 白色；容器 70% `surfaceVariant`、12pt 圆角；最大高度 2/3 屏，内部可滚。
- 实时转写浮层规则：
  - 优先显示 `currentTranscription`；
  - 否则若进入后出现新的 final 用户消息 → 显示该文本；
  - 否则若进入前无用户消息 → 显示最后一条用户消息；
  - 否则不显示。
- 相机模式下隐藏欢迎卡片、消息气泡、头像；关闭相机后全部恢复。
- 切换动画：淡入淡出（200ms），`reduceMotion` 下无动画。

### 8.4 图库预览

```
┌─────────────────────────────────────┐
│ ←                             取消  │  黑底
│                                     │
│                                     │
│            图片（fit/缩放）          │
│                                     │
│                                     │
│           [   发送   ]              │  底部悬浮胶囊
└─────────────────────────────────────┘
```

- 左上返回 → 重新打开 picker；右上取消 → 回会话。
- 无中间提示文字；加载中转圈；失败显示 `conv_gallery_preview_failed`。
- 发送按钮灰底白字；未加载完成时禁用。
- 手势：捏合 1x–5x、放大态拖动；无单击退出/双击缩放。

### 8.5 AI 图片

- AI 气泡：图片在文本上方，12pt 圆角，按原始比例填满气泡宽度；下方文本仍按 M1；文本为空时只显示图片。
- P0 缩略图不可点击；P1 全屏查看器：黑底、单击退出、捏合/拖动、左右翻页、保存按钮与提示胶囊。
- 图片加载失败：只显示文本，不显示破图占位。

### 8.6 新增文案（en + zh-Hans）

| Key | English | 简体中文 |
|---|---|---|
| conv_toolbar_camera_on | Camera on | 摄像头 已开启 |
| conv_toolbar_camera_off | Camera off | 摄像头 已关闭 |
| conv_switch_to_back_camera | Switch to rear camera | 切换到后置摄像头 |
| conv_switch_to_front_camera | Switch to front camera | 切换到前置摄像头 |
| conv_avatar_from_gallery | Choose from gallery | 从相册选择 |
| conv_gallery_send | Send | 发送 |
| conv_gallery_action_text | Describe the image content | 描述图片内容 |
| conv_gallery_preview_image | Photo preview | 图片预览 |
| conv_gallery_preview_failed | Can't load photo | 无法加载图片 |
| conv_camera_unavailable | Can't open camera | 无法打开相机 |
| conv_camera_permission_guide | Camera permission is required to use the camera. Check LivaChat's camera permission in system settings and try again. | 使用相机功能需要相机权限。请检查 LivaChat 的相机权限设置后重试。 |
| conv_camera_start_failed | Failed to start camera — check camera permission | 相机启动失败，请检查相机权限 |
| cap_bubble_failed | Capture failed, try again | 抓取失败，请重试 |
| settings_section_video | Video | 视频 |
| settings_default_front_camera | Default front camera | 默认前置摄像头 |
| settings_default_front_camera_desc | Use the front camera by default when video starts | 开启视频时默认使用前置摄像头 |
| settings_auto_camera_on_join | Auto-start camera on joining a chat | 进入对话时自动开启摄像头 |
| settings_auto_camera_on_join_desc | Enable video each time a chat starts | 每次开始对话时自动启用视频 |
| perm_camera_title | Camera permission (optional) | 摄像头权限 (可选) |
| perm_camera_desc | So I can see your surroundings — you can turn it on later | 让我看见你的周围环境，需要时再打开也可以 |
| chat_ai_image | AI image | AI 图片 |
| common_saved_to_gallery | Saved to your gallery | 已保存到本地相册 |
| common_save_failed | Save failed | 保存失败 |

- 复用 M1 已有的 `common_back`、`common_cancel`、`perm_grant_action`、`perm_go_to_settings`。
- 图库上行指令 `conv_gallery_action_text` 已加入用户可见文案表，17 locale 本地化（含 `th`/`vi`，见 `docs/ios_multilingual.md` §2.9）；Android 已扩至 17 locale（`conv_image_action_text`，2026-10-27，见 `docs/android_multilingual.md` §2.3.9/§21）。

### 8.7 无障碍

- 摄像头按钮、切换镜头、图库「+」、发送、返回、取消、保存均有明确 label。
- 相机预览本身不逐帧暴露可访问内容（`accessibilityHidden` 或 label “Camera preview”），状态由顶部状态与文本浮层表达。
- 所有新按钮触控 ≥44×44pt；caption 行通过 `contentShape` 扩展。
- 图片提供 `chat_ai_image` 描述；查看器提供“关闭/保存/上一张/下一张”标签。
- 状态、开关不只依赖颜色；沿用 M1 的文案 + 颜色 + 图标。
- 尊重 `reduceMotion`：相机模式切换、查看器动画可关闭。
- Dynamic Type：相机浮层文本随字号缩放；切换按钮/工具栏固定尺寸。

### 8.8 iPad 兼容

- 保持 `TARGETED_DEVICE_FAMILY = 1`、竖屏；iPad 以 iPhone 兼容模式运行。
- 相机预览全屏填充；图库预览、查看器同样全屏。
- 不新增原生 iPad 分栏/横屏逻辑。

### 8.9 状态矩阵

| 状态 | 视觉 |
|---|---|
| 相机权限未决定 | Permission 页摄像头卡片「授权」；点开相机时请求 |
| 相机权限拒绝 | 卡片「授权」→ 系统设置；手动开相机弹引导 |
| 自动开相机但权限不足 | 相机 UI 复位；`conv_camera_start_failed` 横幅 + 去设置 |
| 相机开启 | 预览 + 切换镜头 + AI/转写浮层；工具栏相机按钮高亮 |
| 相机启动失败 | 相机 UI 复位；错误 banner + 去设置/重试 |
| 图库预览 | 黑底全屏；返回/取消/发送 |
| 图库编码失败 | 回会话 + 可恢复错误 banner |
| AI 回图 | AI 气泡图片 + 文本；点击查看器（P1） |
| 后台 | 相机停止；音频按 M1 停止 |
| 回前台 | 相机恢复（若之前开着且权限仍在） |

---

## 9. 协议 / 服务端 / Mock

### 9.1 上行视频消息（现有协议，无改动）

| 字段 | 摄像头 | 图库 |
|---|---|---|
| `jpeg` / 尺寸 | 长边 ≤1280，q70 | 长边 ≤1920，q70 |
| `orientation` | `.portrait0`（像素已旋正） | `.portrait0` |
| `camera_facing` | `front` / `back` | `gallery` |
| `turn_id` | 开相机/切镜头递增 | 当前值，不递增 |
| `text` | nil | 按 App 语言的本地化指令（`conv_gallery_action_text`） |
| 频率 | ~1fps | 单帧 |

### 9.2 服务端行为依赖（已存在）

- `camera_facing` 四值包含 `gallery`（v7.1 已部署）。
- `turn_id` 变化（非 0、非缺省）→ 新采集轮首帧 → 立即起 LLM/TTS 回合；无 `text`。
- 带 `text` 的 video 帧 → 文本回合；图库照片作为本轮 user 图。
- final `ai_text` 可回带同一帧图片；SDK 暴露 `AiImage`。
- `ai_video_ack` 仅确认帧，不要求 App 消费。

### 9.3 Mock Server 扩展

现有：收到 `video` 返回 `ai_video_ack`；已有场景不含图库回复。M2 建议新增：

| 场景 | 行为 |
|---|---|
| `video_gallery` | 收到带 `text` 的 video：返回 ack → `status(thinking)` → `ai_text` final（文本 + 小尺寸 JPEG 图片）→ `status(listening)`；收到无 `text` 的 video：只返回 ack |
| 现有 `default` | 保持只 ack；用于相机帧冒烟 |

- 图片 fixture：内置一张小 JPEG（如 64×64 纯色/渐变），Base64 放入 `ai_text.payload.image`。
- 测试：协议 pytest 覆盖 ack + 带图 ai_text；App 集成测试用 `video_gallery` 跑图库 E2E。
- 真机最终验收仍接真实 server（视觉链路）。

---

## 10. 测试计划

### 10.1 单元测试（fake 驱动）

| 范围 | 用例 |
|---|---|
| CaptureTurnCounter | 0 起始、next 从 1 递增、溢出回绕、运行期不重置 |
| CameraFramePolicy / generation | 暖机 1s 丢帧、900ms 节流、长边缩放纯函数、只缩不放；旧 generation 帧丢弃；facing 变化首帧递增 turn（纯逻辑） |
| CameraController（真机/集成） | start/stop/switch 顺序；方向/镜像；权限缺失抛错；失败回调主线程；不修改设备帧率 |
| Camera 权限 | undetermined → request 后 granted/denied；denied 不请求；自动开相机不请求、失败横幅 |
| Gallery 编码 | 尺寸/EXIF 八向、长边 1920、q70、透明/损坏数据 FAILED；输出已旋正 |
| Gallery 状态机 | + → picking → preview → back/cancel/send；picker 取消两分支；发送后立即回 idle |
| Gallery 竞态 | 编码完成时相机已开 → REJECTED，不提示；编码失败 → FAILED |
| ViewModel | isCameraOn/isFrontCamera 单一写入；开/关/切镜头状态；失败复位；AI image 附加；image-only final 不被空文本守卫丢弃 |
| Coordinator | autoCameraOnJoin 权限 granted/非 granted（失败横幅）；后台停相机/前台恢复；打开 Settings 停相机；sessionEnded/runtime stop 停相机；设置重建清状态但不重置 counter |
| SettingsStore | 两个键默认 false、持久化、读取 |
| AI 图片 | `ai_text` image 透传；final 后 `ChatMessage.image`；partial 图片缓存到 final；后台解码/缓存 |
| 图片解码 | AiImage orientation 旋正；损坏数据返回 nil |

### 10.2 Mock Server / 集成

- 新增 `video_gallery` 场景（§9.3）。
- 模拟器 E2E：
  - 相机：DEBUG fake camera 或真实模拟器无相机 → 用 fake frame 注入验证 `video` 报文含 `camera_facing/front/turn_id`；真实相机仅真机。
  - 图库：`-uiTestGallery` 注入 fixture 图片 → 发送 → mock 回 `ai_text` + image → UI 显示 AI 文本与图片。
- 真机 E2E：相机帧 → `ai_video_ack`；图库 → AI 描述 + 回图；日志含 `video` 上行与 `sessionReady`。
- 断线重连时相机继续采集由 SDK 丢弃；重连后保持同一 turnID（不额外递增）。

### 10.3 UI 测试（不联网、不碰硬件）

- Permission：麦克风卡片 + 摄像头卡片；摄像头已授权 ✓；拒绝态按钮。
- Settings：Video 区两个开关存在、可切换、持久化（重启后仍生效）。
- 工具栏：摄像头关闭时「+」可见；开关后「+」隐藏（注入 `isCameraOn`）。
- 相机模式：注入静态预览占位 + AI/转写文本，验证布局与切换按钮。
- 图库预览：注入 fixture，验证返回/取消/发送按钮与加载态。
- AI 图片气泡：注入带图消息；点击打开查看器（P1）。
- 失败态：相机启动失败 banner；图库编码失败 banner。

新增 DEBUG 启动参数建议：

```
-uiTestCamera on|off
-uiTestCameraFront 1|0
-uiTestCameraPermission granted|denied|undetermined
-uiTestGallery <fixture-name>
-uiTestAIImage 1
```

### 10.4 真机验收（iPad Pro 10.5 / iPadOS 17.7.5）

- 首次授权摄像头；拒绝后去系统设置再授权；
- 手动开/关摄像头；前后切换；预览无黑屏/卡死；
- 服务端收到约 1fps、16:9 720p 帧，`ai_video_ack`；开相机/切镜头 `turn_id` 变化；
- 说话 → 转写 → AI 文本/TTS 与相机模式共存（AEC 无回声、打断正常）；
- 图库选图 → 预览 → 返回重选 / 取消 / 发送；大图/HEIC/EXIF 方向正确；
- 图库发送后 AI 文本 + 回图显示；
- Settings 两个 Video 开关生效（默认前置、自动开摄像头）；
- 后台/回前台相机停止/恢复；来电中断后音频恢复、相机状态正确；
- 设置切换服务器重建 runtime 后相机状态复位、未崩溃；
- 无录屏/模式/历史入口；无后台相机常驻。

### 10.5 构建与回归

- `./scripts/build-ios.sh sdk` 不回归（M2 不改 SDK）。
- `./scripts/build-ios.sh app` 签名构建通过。
- `xcodebuild test` 单元 + UI 通过。
- M1 语音链路的 104 unit + 6 UI 基线不回归。
- iPad 构建/兼容模式运行不回归。

---

## 11. 实施顺序

### M2.0 设计与决策（本文）

1. Review 本文，确认 §3 决策与 §13 拍板记录；
2. 更新 `docs/ios_app_arch.md`、`docs/ios_app_ui.md`、`docs/ios_app_settings.md`、`docs/ios_app_shell.md` 的 M2 增量（实施后同步）；
3. 报批 UIKit 例外（PHPicker、相机预览层）。

### M2.1 Camera Spike（最高风险先做）

4. `CameraPermissionProvider` + Permission 页相机卡片；
5. `CameraController`（会话/预览/开关/切换/前后台）；
6. 帧管线（暖机/节流/缩放/JPEG）→ `pushVideoJPEG`；
7. DEBUG 相机 harness 或最小会话入口，真机验证画面、方向、镜像、AEC、功耗；
8. `CaptureTurnCounter` 与 turnID 日志。

### M2.2 相机 UI / 设置集成

9. SettingsStore/Video 区两个开关；
10. 工具栏摄像头列 + 自动开相机 + 默认前置；
11. 相机模式布局（预览、切换、AI/转写浮层）；
12. 失败/权限引导/后台恢复；
13. 单测与 UI 测试。

### M2.3 图库

14. PHPicker 适配层 + 选择状态机；
15. 全屏预览（缩放/返回/取消/发送）；
16. ImageIO 编码 + `GalleryPhotoSender` + 竞态/三态；
17. 工具栏「+」接线与错误 banner；
18. Mock `video_gallery` 场景与集成测试。

### M2.4 AI 图片

19. `ChatMessage.image` + Coordinator/ViewModel 透传；
20. AI 气泡图片缩略图；
21. （P1）全屏查看器 + 保存到相册。

### M2.5 集成与验收

22. 模拟器/Mock E2E；
23. iPad 真机相机/图库/设置/后台全量验收；
24. 更新 `docs/ios_app_m2.md` 实施记录与截图/日志证据。

---

## 12. 验收标准

- [ ] 摄像头权限为可选；Permission 页有摄像头卡片；拒绝后可去系统设置；
- [ ] 工具栏摄像头开关可用，首次手动开启触发系统权限；拒绝有引导；
- [ ] 相机模式显示实时预览、切换前后摄、底部工具栏保留；关闭后恢复语音视图与消息；
- [ ] 相机帧约 1fps、16:9 720p、长边 ≤1280、q70、首帧 1s 暖机；服务端返回 `ai_video_ack`；
- [ ] 开相机/切镜头 `turn_id` 递增；重连/后台恢复/重建 runtime 不重置计数；
- [ ] Settings Video 两个开关持久化；默认前置、自动开摄像头行为正确；自动开启时权限不足会复位相机 UI 并显示启动失败横幅；
- [ ] 摄像头关闭时「+」可见并可打开图库；摄像头开启时「+」隐藏；
- [ ] PHPicker 单图选择、全屏预览、返回重选、取消回会话、发送行为符合 §6.2；
- [ ] 图库照片长边 ≤1920、q70、EXIF 旋正、`camera_facing=gallery`、单帧、竞态拒绝；
- [ ] AI 回复文本与图片显示在消息列表；图片失败不影响文本；
- [x] AI 图片全屏查看器缩放/翻页/保存可用；
- [ ] 后台停止相机、回前台恢复；相机与 M1 音频/AEC/打断共存无回归；
- [ ] 无屏幕采集/模式/历史/后台摄像头代码与死控件；
- [ ] 单测、Mock E2E、UI 测试、iPad 真机冒烟通过；M1 基线不回归。

---

## 13. 风险与决策记录

| 编号 | 风险/决策 | 影响 | 建议 |
|---|---|---|---|
| R1 | `AVCaptureVideoDataOutput` 的 `videoOrientation`/镜像在真机上的实际行为与功耗 | 画面旋转/镜像错误；物理旋转作用于每个输出帧，可能增加功耗 | M2.1 spike 用日志与截图验证；不支持则按 position 手动旋转；若功耗异常，改为 data output 不旋转、只旋转节流后的帧 |
| R2 | 相机与 `.voiceChat` + voice processing 共存 | 回声、采样率变化、AEC 失效、音频中断 | 采集会话 video-only + `automaticallyConfiguresApplicationAudioSession=false`；真机回归说话/打断/TTS |
| R3 | Swift 6 严格并发 + AVFoundation delegate 回调 | 编译错误/数据竞争 | 串行队列 + 锁 + `@unchecked Sendable`；主线程回调显式 hop |
| R4 | 切换镜头时的旧帧/新 turn 错标 | 服务端把旧画面当新视野 | generation + `lastPushedFacing` 首帧递增；丢旧帧 |
| R5 | `PHPicker` iCloud/大图/HEIC/EXIF | 加载慢、方向错、内存峰值 | ImageIO 缩略解码 + 后台编码 + loading/失败态；捕获 OOM 收敛为 FAILED |
| R6 | AI 图片内存占用（Data 常驻消息） | 长会话内存增长 | M2 只存内存；后续可缩略缓存/落盘；文档记录 |
| R7 | 自动开相机与可选权限交互 | 启动弹权限/错误横幅 | **已拍板（follow Android）**：不请求系统权限，尝试开启；失败则复位相机 UI + `conv_camera_start_failed` 横幅（§5.1） |
| R8 | 相机模式只显示 final AI 文本（Android 现状） | 相机中看不到流式文本，体验略慢 | 先对齐 Android；若产品要求再改为流式 |
| R9 | P1 查看器手势（翻页 vs 缩放）复杂度 | 工期/体验 | 可先单图查看，确认后再加翻页 |
| R10 | 图库指令语言 | 非中文用户服务端历史出现中文 user 行 | **已解决（2026-10-27）**：iOS 改为按 App 语言发送 `conv_gallery_action_text`；Android 已同步（`conv_image_action_text`，2026-10-27） |
| R11 | 相机 preset/画幅 | 4:3 vs 16:9、功耗、兼容性 | **已拍板**：16:9 720p（`.hd1280x720`）；`.high` 仅作设备不支持时的回退 |
| R12 | 模拟器无相机 | 自动化无法覆盖真实采集 | UI 测试用 fake；真实链路仅真机验收 |
| R13 | 用设备/连接帧率限速会同时拖慢预览 | 预览卡顿、AE 变慢 | **已定**：不限制硬件帧率；相机按系统帧率运行，delegate 内按 900ms 节流上行（§5.2） |
| R14 | 相机在 Settings 覆盖层后继续采集 | 隐私/流量问题 | **已定**：打开 Settings 先停相机并清恢复标记（§5.6） |

**拍板记录（2026-10-20）**：

1. AI 图片：P0 只做气泡缩略图；全屏查看器/保存为 P1，不阻塞 M2 P0 验收。
2. 自动开相机权限未授权：**follow Android** —— 不请求权限，尝试开启，失败后复位相机 UI 并显示启动失败横幅。
3. 相机画幅：**16:9**（`.hd1280x720`，功耗更低）。
4. 图库指令：改为按 App 语言本地化的 `conv_gallery_action_text`（2026-10-27 更新，替代原中文常量）。

---

## 14. 模块文档与文件索引

| 主题 | 文件 |
|---|---|
| M2 总设计 | `docs/ios_app_m2.md`（本文） |
| M1 总设计/实施记录 | `docs/ios_app_m1.md` |
| App 架构 | `docs/ios_app_arch.md` |
| SDK 视频契约 | `docs/ios_sdk_design.md` §7.3–§7.6 |
| 音频/AEC | `docs/ios_app_audio.md` |
| 会话 Domain | `docs/ios_app_conversation.md` |
| Settings/Keychain | `docs/ios_app_settings.md` |
| UI/UX | `docs/ios_app_ui.md` |
| AppShell/生命周期 | `docs/ios_app_shell.md` |
| Android 相机设计 | `docs/camera_enhance.md`（§8–§11） |
| Android turnID/视频链路 | `docs/fd_pipeline_video_process.md` §8 |
| Android UI 基线 | `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/` |
| iOS SDK | `apps/ios/LivaChatSDK/Sources/LivaChatSDK/` |
| Mock Server | `tools/local-dev/mock-services/livachat_mock_server/` |

---

## 15. 自审记录（2026-10-20）

对本文做对抗式审查后修正的严重问题：

| # | 问题 | 修正 |
|---|---|---|
| 1 | 用 `activeVideoMinFrameDuration` 限制 ~1fps 会同时拖慢实时预览（AVFoundation 帧率由相机硬件共享） | 不限制设备/连接帧率；相机按系统帧率运行，delegate 内 900ms 节流，仅丢上行帧 |
| 2 | data output 的 `videoOrientation` 支持性未校验；切换输入后方向/镜像可能重置 | 增加 `isVideoOrientationSupported` / `isVideoRotationAngleSupported` 检查、手动旋转兜底、切换后重设 |
| 3 | 打开 Settings 后相机仍会继续采集/上行 | 打开 Settings 先停相机、复位 UI、清空恢复意图 |
| 4 | 音频中断/麦克风权限丢失时相机可能继续采集 | 中断与权限丢失都停相机并记录/清空恢复标记 |
| 5 | picker 与 preview 同时 dismiss/present 会造成 SwiftUI 呈现失败 | 明确单状态机与 `onDismiss` 串行呈现 |
| 6 | image-only final `ai_text` 会被现有空文本守卫丢弃 | 追加消息放行 image-only；气泡只显示图片 |
| 7 | AI 图片在 SwiftUI body 内同步解码会卡顿 | 后台 ImageIO 解码 + `messageID` 缓存，清会话时清缓存 |
| 8 | P0 阶段给缩略图加 tap 但没有查看器 → 死控件 | P0 不可点击；P1 再加查看器入口 |
| 9 | 保存查看器图片直接写 `AiImage.data` 会导致方向错误 | 保存已旋正 CGImage 的重新编码 JPEG |
| 10 | 摄像头按钮与「+」若嵌套 Button 会互相误触 | 图标按钮与 caption 行按钮拆开；plus 隐藏时 caption 不可点 |
| 11 | 开相机与切换 face 都可能递增 turn 导致双跳 | 打开时重置 `lastPushedFacing`，仅切换后的新 facing 首帧递增 |
| 12 | 切换镜头按钮视觉 40pt 低于 44pt 触控标准 | 视觉 40pt、可点区域 ≥44×44pt |
| 13 | Permission 页新增第二张卡片后小屏/大字号可能截断 | 页面改为可滚动布局 |
| 14 | 测试计划把 AVFoundation 生命周期当纯单测，不可执行 | 拆分为纯逻辑单测 + CameraController 真机/集成测试 |

未发现但需 spike 证实的点保留在 §13（主要是方向/镜像真机行为与功耗）。

---

## 16. 实施记录（2026-10-20）

### 16.1 已落地（P0/P1）

| 模块 | 内容 |
|---|---|
| 设置 | `SettingsStore.cameraDefaultFront` / `cameraAutoOnJoin`（UserDefaults）；Settings 页新增 Video 区两个开关；en/zh-Hans 文案 |
| 权限 | `CameraPermissionProviding` / `SystemCameraPermissionProvider`；Permission 页可选摄像头卡片（可滚动布局）；Info.plist + InfoPlist.strings |
| 相机核心 | `CaptureTurnCounter`、`CameraFramePolicy`、`CameraController`（AVCaptureSession video-only、720p、1s 暖机、900ms 节流、长边 1280/q70、方向/镜像、前后台/中断、每采集轮独立 pipeline/generation） |
| 相机集成 | `ConversationRuntime` / Factory / Coordinator / ViewModel / AppContainer 接线；`enableVideo=true`；自动开相机；Settings 打开停相机；音频中断/麦克风权限丢失停相机；turnID 语义 |
| 相机 UI | 工具栏三列；caption 行「+」；`CameraModeView`（预览、切换镜头、AI 文本/实时转写浮层） |
| 图库 | `PHPickerView`；`GalleryPreviewView`（黑底、加载态、1x–5x 缩放、返回/取消/发送）；`GalleryPhotoEncoder`（1920/q70/EXIF 旋正）与 `GalleryPhotoSender`（gallery/单帧/turn 当前值/本地化指令）；呈现串行化与相机竞态拒绝 |
| AI 图片 | `ChatMessage.image`；Coordinator 透传 final `AiImage`；image-only final 保留；`AIImageView` 后台解码/缓存/方向校正/气泡显示 |
| AI 图片查看器 | 全屏黑底；当前会话 AI 图片左右拖动渐显翻页（过阈值动画落位）；1x–5x 缩放、放大态平移、单击退出；页码；保存当前页到相册（add-only 权限 + 旋正 JPEG + 保存提示）；`ImageViewerMath` 纯函数单测 |
| Mock | `video_gallery` 场景与测试；README 更新 |
| 测试 | 单测、UI 测试、Mock 场景、UI 测试注入参数（`-uiTestCamera`、`-uiTestCameraFront`、`-uiTestCameraPermission`、`-uiTestGalleryPreview`、`-uiTestAIImage`） |

### 16.2 验证结果

```bash
# 模拟器 iPhone 16 Pro (iOS 18.6)
xcodebuild test -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj -scheme LivaChatApp \
  -destination 'platform=iOS Simulator,id=AD9096F3-27D8-4FDC-8571-D8D370D54651'
```

- 单元测试：**122 passed / 1 skipped / 0 failed**；
- UI 测试：**11 passed / 1 skipped / 0 failed**（含相机布局、图库预览取消、AI 图片气泡、查看器翻页；设备 E2E 默认跳过）；
- Mock Server：**29 passed / 0 failed**；
- SDK 回归：`RUN_IOS_TESTS=0 ./scripts/build-ios.sh sdk`，**67 passed / 0 failed**（M2 未改 SDK）；
- 模拟器 App 构建通过；`generic/platform=iOS` 无签名设备构建通过；
- 签名构建 `./scripts/build-ios.sh app` 在 GUI Terminal 执行成功，并安装到 iPad Pro 10.5（iPadOS 17.7.5）。

**真机 E2E（2026-10-20，iPad Pro 10.5 / Mock 18080 `video_gallery`）**

- 相机开关/前后摄切换通过；设备日志示例：
  - `Camera frame facing=back 720x1280 bytes=136251 turn=1`
  - `Camera switch front=true` → `frame facing=front ... turn=2`
  - 再切回/切前：`turn=3`、`turn=4`；关闭后重开 `turn=5`
  - App 进程重启后 turn 从 1 重新开始（进程级计数，符合设计）
- 帧率约 1fps，720×1280、q70；Mock 18080 持续收到 `ClientVideo` 并返回 `ServerAiText`。
- 图库真机发送通过：`gallery photo send 1314x1198 bytes=318052`（另有 1574×1080、900×1148 记录），Mock 收到 `ClientVideo` 并返回 AI 文本；PHPicker 选图、预览、发送均可用。
- 相机图标缺失/斜杠方向问题已修复（`camera.slash.fill` 在当前系统不可用，改为自绘斜杠）。
- 设备自动化 UI 测试 `M2DeviceE2ETests` 默认跳过（`M2_DEVICE_E2E=1` 才运行），避免权限弹窗导致 CI 假失败。

### 16.3 剩余项

| 项 | 说明 |
|---|---|
| AEC / loopback 回声 | 本次真机语音链路可用，但未做单独量化/回声听感记录；建议后续真机回归时补录 |
| 自动开相机权限失败文案 | 代码路径与单测已覆盖；未在本次真机单独复现权限拒绝场景 |
| 设备 UI 自动化 | `M2DeviceE2ETests` 保留为可选工具，默认跳过；当前真机验收以人工 + 日志证据为准 |

### 16.4 真机反馈与实现审查修复（2026-10-20）

| # | 问题 | 修复 |
|---|---|---|
| 1 | `camera.slash.fill` 在当前 iPadOS 不存在，关闭态相机图标空白 | 改为自绘相机 + 斜杠（方向按真机反馈修正），与 Mic/Speaker slash 风格一致 |
| 2 | 底部 caption 显示 on/off 文案，过长 | 只显示 Mic / Speaker / Camera（按空格取首词，与 Android 一致）；完整开关状态保留给 VoiceOver |
| 3 | 头像外层状态光圈占用高度 | 移除光环，头像外框 120→96pt、去掉顶部间距，消息区约多 32pt |
| 4 | 右上角「⋯」直接进 Settings | 改为菜单：时光机 / 对话 / 设置；前两项显示 Coming soon banner |
| 5 | Service 标题字号/亮度不一致 | Server/Auth key/Check service 标题统一 16pt 主文字色；Section 标题统一品牌紫 |
| 6 | Check service 禁用时整行变暗 | 不再 `.disabled` 整行；仅 action 文案在不可检测时用三级色，行为对齐 Android |
| 7 | 自签名开关默认关闭导致真实 server 检测失败 | 默认改为开启（`settings.tls_allow_self_signed` 默认 true） |
| 8 | DEBUG Mock 覆盖把 `.system` 策略强加给 Settings 检测 | Probe 始终使用设置页当前自签名策略；真机日志确认 `probe <lan-host>:8080 status=200` |
| 9 | 保存真实 server 后仍被 DEBUG Mock 覆盖 | Settings 保存/应用后以用户保存的配置优先，直到下次启动 |
| 10 | iOS 17 旋转角不支持时无回退 | 回退到 `videoOrientation = .portrait` |
| 11 | PHPicker 异步加载晚于 sheet 关闭时预览丢失 | 若 sheet 已关闭，数据到达后直接进入预览 |
