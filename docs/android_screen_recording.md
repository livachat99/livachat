# Android 屏幕交互（抓屏 / 浮窗 / 等待态）

> **状态**：已实施（Android）；**2026-10-27 已实施变更**：`TAP_CAPTURE_TEXT` 中文常量已改为 15 locale 资源 `conv_image_action_text`，随后扩展至 17 locale（th/vi），随 App 语言、英文兜底；见 `docs/android_multilingual.md` §2.3.9/§21。
> **文档基线**：2026-10-24，对应当前 `apps/android/` 代码
> **范围**：`apps/android/LivaChatApp/` 的屏幕交互启动/停止、悬浮窗、单帧抓取与等待态；`apps/android/LivaChatSDK/` 的 `video.payload.text` 透传；server 文本回合契约作为客户端依赖一并记录
> **相关文档**：
> [`fd_pipeline_video_process.md`](fd_pipeline_video_process.md)（video 帧与 `turn_id`）、
> [`fd_pipeline_omni_mode.md`](fd_pipeline_omni_mode.md)（omni 对抓屏帧的消费）、
> [`android_background_support.md`](android_background_support.md)（前后台/FGS 语义）、
> [`ws-protocol-design.md`](ws-protocol-design.md)（协议总览）
> **说明**：文件名保留 `screen_recording`，但当前产品语义是**点按浮窗单帧抓屏**，不是持续录屏；2s 定时上行已停用（`AUTO_TICK_ENABLED=false`，代码保留）。

---

## 1. 功能与范围

### 1.1 用户可见功能

| # | 能力 | 行为 |
|---|---|---|
| 1 | **启动屏幕交互** | 对话页顶栏录制按钮 → 说明弹窗（首次或缺悬浮窗权限时）→ 悬浮窗权限（如需）→ 系统 MediaProjection 授权 → 前台服务 + 屏幕边缘浮窗 |
| 2 | **点按抓屏** | 用户切到目标 App 后点按浮窗：隐藏浮窗 → 取当前屏幕一帧 → 携带本地化发图指令 `conv_image_action_text` 上行（2026-10-27 已实施）→ 浮窗反馈 |
| 3 | **等待 AI 回复** | 点按成功后浮窗显示绿色旋转缺口环，等待本轮 `ai_text`；15s 兜底超时 |
| 4 | **停止** | 顶栏按钮（录制中）→ 停止确认弹窗 → 确认停止；进入非 chat 模式/开相机/会话结束等路径自动停 |
| 5 | **浮窗交互** | 可拖拽；横向吸附左/右边缘；点按与拖拽以 touch-slop 区分；位置只在本次会话内记忆 |

### 1.2 当前交互模型

- **一次点按 = 一帧**：不存在持续推流；`AUTO_TICK_ENABLED=false` 关闭了 2s 定时上行（ticker/keepalive/门控代码与单测保留，置回 true 可恢复，但当前产品不使用）。
- **点按即起一轮**：帧携带 `payload.text`（`context.getString(R.string.conv_image_action_text)`，16 locale、随 App 语言、英文兜底），服务端不等待 ASR，直接以该文本 + 本帧起 LLM/TTS 回合。
- **本地不插入用户气泡**：服务端 history/chat_store 记录该 user 轮；App 实时聊天只接收 AI 的 `ai_text` 回复，浮窗反馈（「屏幕截图已发送」+ 绿环）承担用户操作反馈。
- **抓取需 App 在后台**：App 前台时点按会被拒绝（「切到其他应用后点击」），避免把聊天界面/既往回图再次采集上行。
- **仅 chat 模式可用**：`translate`（含 lecture）/`transcribe`/`omni` 模式禁用；顶栏按钮**始终显示**，非 chat 模式点击由 ViewModel 守卫弹「当前模式下不支持屏幕交互」；进入非 chat 模式会强制停止当前会话。

### 1.3 非目标

- 持续录屏 / 录屏文件（视频或音频）落盘；
- 抓取历史、相册选图（相册发送是另一个功能，复用 `video` 链路）；
- 在 App 前台抓取自身画面；
- 等待态的服务端确认/精确 reply 匹配（当前是客户端 UI 状态 + 15s 超时）；
- job 状态/结果查看（见 `android_agent_job_status.md`、`android_agent_job_view.md`）。

---

## 2. 用户流程

### 2.1 启动

```text
顶栏录制按钮（ToggleRecording）
  │
  ├─ 非 chat 模式 → ErrorBanner「当前模式下不支持屏幕交互」，中止
  ├─ 已在抓屏 → 停止确认弹窗（Confirm → stop；Dismiss → 继续）
  ├─ 相机开着 → 先 unbind 相机（互斥）
  │
  ├─ 说明已展示 && 悬浮窗权限已授予
  │    → pendingScreenCaptureLaunch = true
  │
  └─ 否则 → 说明弹窗
       ├─ 取消 → 关闭，不启动
       └─ 继续 → 记录「已展示」；
            ├─ 悬浮窗权限已授予 → pendingScreenCaptureLaunch = true
            └─ 未授予 → pendingOverlayPermissionLaunch = true
                 → 系统「显示在其他应用上层」设置页
                 → 返回复查 canDrawOverlays()
                      ├─ true → pendingScreenCaptureLaunch = true
                      └─ false → ErrorBanner「需要显示在其他应用上层权限」
  │
  ▼
MediaProjection 系统授权（Android 14+ 每次启动都弹；单次 token）
  ├─ 拒绝/取消 → 静默复位（isRecording 本就 false）
  └─ 允许 → ScreenCaptureController.start(resultCode, data)
       → 前台服务 startForeground(mediaProjection)
       → onForegroundServiceReady(代次) → 创建 Projection/VirtualDisplay/ImageReader
       → 挂载浮窗（失败 → 启动错误 + stop）
```

### 2.2 点按抓取

```text
浮窗点按（主线程）
  ▼
captureDecision:
  !isRecording            → Ignore
  waitingForReply         → IgnoreWaiting（等待期点按被吞；拖动仍可用）
  capturing               → Ignore（在飞）
  now - lastCapture < 600ms → Ignore（防连点）
  !shouldPushFrame(...)   → Reject（App 前台 / 熄屏 / 锁屏 → 对应提示）
  否则                    → StartCapture
  ▼
capture handler:
  maybeResizeForRotation()
  → 主线程：代次校验 → bubble.hideForCapture()（瞬时 INVISIBLE）
  → capture handler：drainReader() 排空可能含浮窗的旧帧
  → 150ms settle（等合成器产出无浮窗画面）
  → 二次门控复查 + 代次复查
  → acquireFrameWithRetry（≤3 次 × 100ms）
  → imageToFrame（RGBA → Bitmap → JPEG q70）
  → sdk.pushVideoJpeg(..., "screen", turnCounter.current,
                      text = context.getString(R.string.conv_image_action_text))
  → 主线程 finishCapture:
       SENT → 显示「屏幕截图已发送」胶囊（800ms）；且会话仍在时进入等待态（绿弧旋转）
       其它 → 红圈 + 对应提示（1500ms）→ 回 IDLE
```

**顺序硬约束**：必须先隐藏浮窗、再排空 ImageReader。反序时，隐藏前的旧帧会重新填满 2 个缓冲，隐藏后的新帧无空位，最终取到含浮窗的旧帧。

**其它实现硬约束**：

1. **异常兜底**：抓取序列中的 handler runnable 必须整体 try/catch，异常一律收敛到 `finishCapture(FAILED_NO_FRAME)`。capture `HandlerThread` 上的裸异常会终止 Looper（ticker/stopLocked/ready 全部静默失效）；若异常发生在「已隐藏、未收尾」窗口，还会让浮窗永久 `INVISIBLE`、`capturing` 永真，后续点按全被吞掉。
2. **代次双校验**：`captureNow()` 快照代次；(i) 主线程隐藏前校验——不匹配直接放弃，避免 stop→start 后旧序列隐藏**新会话**的浮窗；(ii) `grabAndPush` 取帧前校验——不匹配静默复位，避免旧代次帧上行。
3. **重试串行**：`acquireFrameWithRetry` 用 `SystemClock.sleep` 串行重试（≤3×100ms），段间复查 `isRecording`；`stopLocked` 的排队延迟最多约一个间隔（≤100ms）。

### 2.3 等待态

```text
SENT（且会话仍在）
  ▼
等待态：浮窗绿环 = 270° 缺口弧，20fps 旋转（周期 1200ms）
  ├─ 首个非 agent 的 index==0 ai_text → 结束
  ├─ 15s 超时 → 结束
  └─ 停录 / 重新启动 → 结束并复位
```

- 等待期点按：SENT 胶囊 800ms 内窗口为 `FLAG_NOT_TOUCHABLE`（触摸穿透）；胶囊消失后窗口恢复
  可触摸，等待期点按由 `IgnoreWaiting` 吞掉，拖拽可用；
- 等待环与 SENT 胶囊同时开始，但各自绘制，胶囊不会吞掉环；
- 系统「动画缩放=0」时环退化为静止整圈，不启动 ticker（零功耗）；
- 等待态是**纯客户端 UI 状态**，不含服务端确认。

### 2.4 停止

| 路径 | 行为 |
|---|---|
| 顶栏按钮（录制中） | 弹停止确认；「停止」→ `stop()`；「取消」/点外部 → 仅关框，继续抓屏 |
| 开相机 | `toggleCamera()` 先 `stop()`（互斥） |
| 启动抓屏 | 先 unbind 相机（互斥） |
| 进入 translate / lecture / transcribe / omni | `stopRecordingIfActive()`：停止 + 关停止确认框 |
| 会话结束（`SessionState.IDLE`） | `screenCaptureController.stop()` |
| ViewModel `onCleared()` | `stop()` |
| 系统侧停止投屏（`MediaProjection.Callback.onStop`） | `stopLocked()` 回收资源、隐藏浮窗、停 FGS |

`stop()` 清 token、置 `isRecording=false`、post `stopLocked()` 并 `stopService`；`stopLocked()` 回收 VD/ImageReader/Projection、结束等待态、注销 `ai_text` 订阅、隐藏浮窗、复位 resize 状态、再次置 `isRecording=false`，并 `stopService`。两者均幂等。

---

## 3. 权限与平台约束

| 约束 | 当前实现 |
|---|---|
| **MediaProjection 单次授权** | targetSdk 34 在 Android 14+ 每次启动都走系统授权；一次采集会话内 N 次点按复用同一个 Projection，不重复授权（旧系统是否复用 token 由平台决定） |
| **前台服务** | `ScreenCaptureService` 以 `foregroundServiceType="mediaProjection"` 调 `startForeground()`（API 29+ 带类型）；通知常驻 |
| **悬浮窗权限** | `SYSTEM_ALERT_WINDOW` 是特殊权限，不能运行时申请；只能跳 `ACTION_MANAGE_OVERLAY_PERMISSION` 设置页，返回后用 `Settings.canDrawOverlays()` 复查 |
| **Go 设备** | Android 10 Go 设备无法获得 `SYSTEM_ALERT_WINDOW`，`canDrawOverlays()` 恒 false；当前给出明确错误提示，不启动采集 |
| **悬浮窗层级** | `TYPE_APPLICATION_OVERLAY`：位于其他 App 之上、系统栏/输入法之下 |
| **浮窗会被自己采集入镜** | MediaProjection 采集 SurfaceFlinger 合成后的整屏输出，无法排除自身窗口；抓取前必须瞬时隐藏浮窗（`FLAG_SECURE` 只会变成黑色矩形） |
| **抓取门控** | 仅「App 在后台 × 屏幕交互 × 未锁屏」允许抓取；App 前台/熄屏/doze/锁屏一律拒绝并提示 |
| **敏感系统界面** | 权限对话框、投屏授权框、部分系统设置页会隐藏 overlay（防 tapjacking）；浮窗在授权完成后才挂载，系统页隐藏属平台行为 |
| **系统边缘手势** | 浮窗距边 4dp；左右边缘可能被系统返回手势优先识别，纵向拖拽为主 |
| **旋转** | App 被清单锁定竖屏；抓屏通过默认屏 `getRealMetrics()` + `DisplayListener` 感知旋转，`VirtualDisplay.resize` 适配；不依赖 Activity 生命周期 |
| **动画缩放=0** | 浮窗不启动自绘 ticker：`~` 静态白色、等待环静态整圈 |

---

## 4. 组件与实现

### 4.1 `ScreenCaptureController`

文件：`app/media/ScreenCaptureController.kt`（`@Singleton`）。

**持有资源/状态**：

- `MediaProjection`、`VirtualDisplay`、`ImageReader`（2 个缓冲）、capture `HandlerThread`；
- `_isRecording: StateFlow<Boolean>`、`_appForeground: StateFlow<Boolean>`、`startErrors: SharedFlow<String>`；
- 点按共享状态（只在主线程）：`capturing`、`lastCaptureMs`；
- 等待态（只在主线程）：`waitingForReply`、`waitStartMs`、`replyListener`、`waitTimeout`；
- 代次 `generationSeq`、`pendingToken`（consent 结果 + 代次）、`turnCounter`。

**启动序列**：

```text
start(resultCode, data):
  幂等守卫（isRecording / pendingToken）
  turnCounter.next()                     // 新采集轮
  attachForegroundObserverOnce()          // ProcessLifecycleOwner
  attachDisplayListenerOnce()             // 默认屏旋转
  generation = generationSeq.incrementAndGet()
  pendingToken = ConsentToken(generation, resultCode, data)
  startForegroundService(serviceIntent(generation))
     失败 → pendingToken=null + startErrors
```

`onForegroundServiceReady(generation)`：代次匹配才消费 token；`startCaptureLocked` 创建：

- 默认屏 `getRealMetrics()`；`scaledCaptureSize`（长边 ≤1920、取偶）；
- `MediaProjection` + `registerCallback`（API 34 必须先注册再创建 VD）；
- `ImageReader`（RGBA_8888，2 缓冲）；
- `VirtualDisplay`（`AUTO_MIRROR`）；
- `_isRecording=true`；
- `AUTO_TICK_ENABLED=false` → 不启动 2s ticker；
- 主线程挂载浮窗；失败 → `capture_overlay_failed` + `stop()`。

**停止/FGS 自愈**：

- `stop()`：清 token、`isRecording=false`、post `stopLocked()`、`stopService`；
- `stopLocked()`：先 post 主线程收尾（`endWaiting`、注销 listener、`bubble.hide()`），再释放 VD/reader/projection、清 ticker/cache/resize 状态、`isRecording=false`、`stopService`；
- `onForegroundServiceDestroyed()`：若仍在录制/等待授权，按期望代次重启 FGS（采集资源不依赖 service 实例）；
- `onForegroundServiceReady`：token 已被 stop 清掉且未录制 → 兜底 `stopService`；录制中 → 保留 service（防重启循环）。

**点按序列**：见 §2.2；`captureDecision`、`shouldPushFrame`、`scaledCaptureSize`、`imageToFrame`、`rgbaToBitmap`、`nextResizeFailureCount` 均为可直测纯函数/内部逻辑。

**等待态**：

- `startWaiting()`：`waitingForReply=true`、记录起点、`bubble.setWaiting(true)`、15s 超时；
- `replyListener.onAiText`：`endsWait(source, index)` 为 true → 主线程 `endWaiting("ai_text")`；
- `endsWait = source != "agent" && index == 0L`（首个非 agent 的首分片，含空 final）；
- `endWaiting(reason)`：幂等；清超时、`setWaiting(false)`；停录/重开也会调用；
- `replyListener` 在浮窗挂载成功后 `compositeListener.register`，`stopLocked` 时 `unregister`。

**旋转适配**：

- 常驻 `DisplayListener`（默认屏）→ `maybeResizeForRotation()`；
- 点按路径每次也先 `maybeResizeForRotation()`（ticker 已停）；
- `resizeCaptureLocked`：新建 reader → `VirtualDisplay.resize` → `setSurface` → close 旧 reader；清 `lastFrame`；
- 连续失败达 `MAX_RESIZE_FAILURES=3` → `capture_stopped` + `stop()`；失败后 1s 内不重复尝试（防旋转动画期瞬态失败烧完计数）。

### 4.2 `ScreenCaptureBubble`

文件：`app/media/ScreenCaptureBubble.kt`（`@Singleton`，自绘 `View`，非 Compose）。

**窗口**：

- `TYPE_APPLICATION_OVERLAY`，`FLAG_NOT_FOCUSABLE`（窗口外触摸落到下层），`PixelFormat.TRANSLUCENT`；
- `gravity = TOP or START`；横向只贴左/右（松手吸附最近边）；纵向 clamp 到 `[24dp, H - h - 24dp]`；
- 距边 4dp；可见标记 32dp 居中于 56dp 点按热区；其余区域透明；
- 位置只在本次会话内记忆（`side` + `y`）；`hide()` 复位为右侧、纵向居中。

**视觉**：

- 高透明黑底圆（`TranslucentBlack`）+ 白色 `~`（`AvatarGradientEnd` 紫色 ↔ 白色脉动，2s 单程、1fps tick）；
- 状态环 3dp：SENT 绿圈、拒绝/失败红圈、等待态绿色 270° 缺口弧；
- 提示胶囊：`PillBlack` 底、`TextPrimary` 13sp、高 32dp（全圆角）、贴屏幕内侧生长；文案超宽时按
  可用宽度 `TextUtils.ellipsize` 省略；
- 定位坐标以 `getWindowVisibleDisplayFrame` 实测的浮窗父框为准（不是整屏）：EMUI 等系统的父框
  可能按状态栏内缩，用整屏尺寸做 clamp 会让浮窗在屏幕底部越界；
- SENT：立即显示「屏幕截图已发送」胶囊（TTL 800ms）；会话仍在时同时进入等待态（绿弧旋转）；
  期间窗口加 `FLAG_NOT_TOUCHABLE`（触摸穿透）；TTL 结束后恢复可触摸，等待期点按由 controller
  忽略、拖拽可用；
- `REJECTED_FOREGROUND` / `REJECTED_LOCKED` / `FAILED_NO_FRAME`：胶囊 + 红圈，TTL 1500ms，保持可触摸以便立即重试；
- 等待态：`setWaiting(true)` 后环以 20fps 旋转（每 tick +15°，周期 1200ms）；系统动画缩放=0 → 静止整圈且不启动 ticker。

**手势**：

- `ACTION_DOWN` 记录起点并清提示；
- 位移超过 touch-slop → 拖拽；松手吸附最近边；
- 未超过 slop → `onBubbleTap()` → controller `captureNow()`；
- 等待态点按由 controller 忽略；拖动照常。

**生命周期**：

- `show(host)` 幂等挂载；`addView` 失败返回 false（启动失败）；
- `hide()` 幂等移除并复位等待/位置状态；
- `hideForCapture()` 瞬时 `INVISIBLE`（无动画，避免半透明残留入帧）；
- `presentFeedback(feedback)` 恢复可见、渲染反馈、设置胶囊宽度上限、启动 TTL 计时。

### 4.3 `ScreenCaptureService`

文件：`app/media/ScreenCaptureService.kt`。

- `@AndroidEntryPoint` 前台服务薄壳；注入 controller；
- `onStartCommand`：`startForegroundCompat()`（API 29+ `FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION`），然后 `controller.onForegroundServiceReady(generation)`；`START_NOT_STICKY`；
- 通知：channel `screen_capture`、ID 1001、ongoing；标题「屏幕抓取已就绪」/正文「点按屏幕边缘的浮窗即可抓取当前画面发送给 AI」；点击打开 `MainActivity`（`FLAG_ACTIVITY_NEW_TASK`）；
- `onDestroy`：`controller.onForegroundServiceDestroyed()` 自愈。

### 4.4 `OverlayPermissions`

文件：`app/media/OverlayPermissions.kt`。

- 唯一职责：`Settings.canDrawOverlays(context)`；
- 跳转设置页的 Intent 构造在 `ConversationScreen`（UI 导航职责），VM 只做判定。

### 4.5 Conversation 接线

**`ConversationUiState` 字段**：

```kotlin
val isRecording: Boolean = false,
val showScreenRecordRationale: Boolean = false,
val showStopRecordingConfirm: Boolean = false,
val pendingScreenCaptureLaunch: Boolean = false,
val pendingOverlayPermissionLaunch: Boolean = false,

// 仅 chat 模式可录屏/抓屏
val canScreenRecord: Boolean
    get() = !(isTranslateMode || isLectureMode || isTranscribeMode || isOmniMode)
```

**`ConversationEvent`**：

```kotlin
data object ToggleRecording
data object ConfirmScreenRecordRationale
data object DismissScreenRecordRationale
data object ConfirmStopScreenRecord
data object DismissStopScreenRecord
data object ConsumeScreenCaptureLaunch
data object ScreenCaptureConsentDenied
data object ConsumeOverlayPermissionLaunch
data object OverlayPermissionReturned
```

**`ConversationViewModel`**：

- `screenCaptureController.isRecording` 合入 UiState；录制停止时自动复位 `showStopRecordingConfirm`；
- `startErrors` → `ConversationError.Unknown` → ErrorBanner；
- `translate/lecture/transcribe/omni` collector 为 true → `stopRecordingIfActive()`；
- `toggleScreenRecording()`：非 chat 守卫 → 录制中弹停止确认 → 相机互斥 → 说明/权限分流；
- `startScreenCapture(resultCode, data)`：再次校验 `canScreenRecord` 后转发 controller；
- `stopRecordingIfActive()`：停止 + 关停止确认框；
- `toggleCamera()`：录制中先 `stop()`；启动抓屏前先关相机；
- `startScreenSession()`：录制中不自动开相机；
- `sessionState == IDLE` → `stop()`；
- `onCleared()` → `stop()`。

**`ConversationScreen`**：

- `screenCaptureLauncher`：`StartActivityForResult`，`RESULT_OK` 且 `data != null` → `startScreenCapture`；否则 `ScreenCaptureConsentDenied`；
- `pendingScreenCaptureLaunch` 消费 → `createScreenCaptureIntent()`；
- `overlayLauncher`：跳 `ACTION_MANAGE_OVERLAY_PERMISSION`（package URI），失败回退 `ACTION_APPLICATION_DETAILS_SETTINGS`；返回（或两个设置页都打不开）→ `OverlayPermissionReturned`；
- 说明弹窗：标题「启动屏幕交互」/正文解释/「取消」「继续」；
- 停止确认弹窗：标题「停止屏幕交互」/正文/「取消」「停止」；
- 顶栏按钮：**始终显示**；`isRecording` 时红色 `ic_screen_capture_active` + contentDescription「停止录制」，否则 `FiberManualRecord` +「开始录制」。

---

## 5. 协议与服务端

### 5.1 `video` 帧

字段（`packages/api-schema/c2s/video.json`）：`data`（base64 JPEG）、`frame_id`、`width`、`height`、`orientation`、`camera_facing`、`quality`、可选 `turn_id`、可选 `text`（≤512）。

**屏幕抓取填充**：

| 字段 | 值 |
|---|---|
| `data` | JPEG q70，长边 ≤1920（真机 1176×2400 → 940×1920） |
| `frame_id` | SDK 单调递增 |
| `width/height` | 实际捕获尺寸（等比缩放、取偶） |
| `orientation` | `PORTRAIT_0`（VirtualDisplay 输出已正立；旋转通过 resize 适配） |
| `camera_facing` | `"screen"`（枚举：front/back/screen/gallery） |
| `quality` | `config.videoQuality`（默认 70） |
| `turn_id` | `CaptureTurnCounter.current`（开始抓屏时 `next()`；0 = 未启用哨兵） |
| `text` | `context.getString(R.string.conv_image_action_text)`（16 locale，随 App 语言）；omni 模式传 null（字段不落线） |

### 5.2 服务端文本回合

`_handle_video` 中的文本回合分支（`services/session/orchestrator.py`）：

```text
processed && payload.data && text.strip()
  && _count_meaningful_chars(text) >= 2
  && pipeline_mode ∉ {translate, transcribe, asr_only, omni}
  ▼
_snapshot_turn_images()                // 唯一取图点；取走并清空 buffer
→ history.add_message("user", text, images=urls)
→ chat_store.on_message(kind="asr", role="user", content=text, images=urls, ref_id="video-{frame_id}")
→ recording_sink.on_chat_text(...)
→ 若 THINKING/SPEAKING/管线在飞 → _do_interrupt("user_text")
→ _spawn_pipeline(_start_thinking(text, image_frames=frames, turn_token=ref_id), danger=False)
```

- 帧必须实际进入 buffer（`processed=true` 且 `data` 非空），否则不起回合；
- 文本回合**先于 ack**，且「入 buffer → snapshot」之间不得插入 await；
- 文本回合不写本地用户气泡；服务端 history/chat_store 记录 user 轮，App 实时聊天只显示 AI 回复；
- 结果 `ai_text` 正常回到客户端；等待态按 §2.3 结束。

### 5.3 与相机/omni 的关系

- **相机 turn 首帧直起轮**（`turn_id` 变化且无 text）不处理带 text 的帧；屏幕抓取帧（带 text）留给文本回合，`turn_id` 照记但不触发相机回合；
- **avdetector** 只接收不带 text 的帧；屏幕抓取帧不参与 avdetector；
- **omni**：客户端 gating 禁止在 omni 模式启动抓屏；controller 仍保留 `isOmniModeProvider`，omni 下不发送 `payload.text`；server omni 分支把帧 feed 给 omni 引擎；
- 服务端若为不支持 `payload.text` 的旧版本：该帧不会触发文本回合；客户端只保证等待环 15s 兜底结束（旧服务端是否把该帧当相机首帧并回复，取决于其版本，不在当前契约内）。当前发布要求客户端与服务端同步升级。

---

## 6. 视觉、文案与本地化

### 6.1 浮窗常量

| 常量 | 值 |
|---|---|
| 点按热区 | 56dp |
| 可见标记直径 | 32dp |
| 距边/上下边距 | 4dp / 24dp / 24dp |
| 环线宽 | 3dp |
| 胶囊 | 高 32dp、水平 padding 12dp、文字 13sp、与标记间距 8dp |
| `~` 字号 | 22sp |
| 脉动 | 白 ↔ 紫，2s 单程，1fps tick |
| 等待弧 | 270°、20fps、周期 1200ms |
| SENT 胶囊 TTL | 800ms |
| 拒绝/失败胶囊 TTL | 1500ms |
| 等待超时 | 15s |

### 6.2 文案

| 位置 | key | 英文 | 中文 |
|---|---|---|---|
| 顶栏开始/停止 | `conv_menu_start_recording` / `conv_menu_stop_recording` | Start/Stop recording | 开始/停止录制 |
| 说明标题 | `conv_screen_capture_rationale_title` | Start screen interaction | 启动屏幕交互 |
| 说明正文 | `conv_screen_capture_rationale_text` | …snapshot of your screen… | 和AI进行屏幕内容交互…点按浮窗即抓取当前屏幕… |
| 继续/取消 | `conv_continue` / `common_cancel` | Continue / Cancel | 继续 / 取消 |
| 停止确认标题/正文 | `conv_screen_capture_stop_title` / `_message` | Stop screen interaction / …stop it? | 停止屏幕交互 / 屏幕AI交互已经启动，需要停下来吗？ |
| 停止 | `common_stop` | Stop | 停止 |
| SENT | `cap_bubble_sent` | Screenshot sent | 屏幕截图已发送 |
| App 前台拒绝 | `cap_bubble_foreground` | Switch to another app, then tap | 切到其他应用后点击 |
| 锁屏拒绝 | `cap_bubble_locked` | Unlock to use | 解锁后使用 |
| 抓取失败 | `cap_bubble_failed` | Capture failed, try again | 抓取失败，请重试 |
| 启动失败 | `capture_start_failed` | Failed to start screen interaction: %1$s | 屏幕交互启动失败: %1$s |
| 浮窗失败 | `capture_overlay_failed` | Floating bubble failed… | 浮窗启动失败，屏幕抓取不可用 |
| 异常停止 | `capture_stopped` | Screen interaction stopped unexpectedly | 屏幕交互异常停止 |
| 非 chat 模式 | `conv_screen_capture_unsupported` | Screen interaction isn't available in this mode | 当前模式下不支持屏幕交互 |
| 悬浮窗权限 | `conv_overlay_permission_required` | The “Display over other apps” permission is required… | 需要「显示在其他应用上层」权限才能使用浮窗抓屏 |
| 通知渠道/标题/正文 | `notif_screen_channel` / `_title` / `_text` | Screen interaction / Screen capture ready / Tap the bubble… | 屏幕交互 / 屏幕抓取已就绪 / 点按屏幕边缘的浮窗… |

### 6.3 颜色

- 浮窗黑底 `TranslucentBlack #59000000`；胶囊 `PillBlack #D9000000`；
- 标记白 `TextPrimary #F8FAFC`；脉动紫 `AvatarGradientEnd #8B5CF6`；
- 成功环 `Green #10B981`；拒绝/失败环 `Red #EF4444`；
- 顶栏录制中图标 `Red`。

---

## 7. 边界与失败语义

| 场景 | 行为 |
|---|---|
| 非 chat 模式点录制按钮 | ErrorBanner「当前模式下不支持屏幕交互」，不启动 |
| 进入非 chat 模式时正在抓屏 | `stopRecordingIfActive()` 立即停止 + 关停止确认框 |
| 录制中开相机 | 先 `stop()`，再开相机 |
| 相机开着启动抓屏 | 先 unbind 相机，再走权限流程 |
| 首次启动 / 悬浮窗权限缺失 | 先展示说明弹窗；确认后按权限分流 |
| 悬浮窗权限被拒/Go 设备 | 不启动采集；ErrorBanner 说明原因 |
| MediaProjection 授权拒绝/取消 | 静默复位，不报错 |
| FGS 启动被系统拒绝 | `capture_start_failed` → ErrorBanner；清 token |
| 浮窗 `addView` 失败 | `capture_overlay_failed` → ErrorBanner；停止会话 |
| App 前台点按 | 拒绝 + 「切到其他应用后点击」；不隐藏浮窗、不抓帧 |
| 熄屏/doze/锁屏点按 | 拒绝 + 「解锁后使用」 |
| 双击/连点（<600ms） | 忽略 |
| 抓取在飞时再点 | 忽略 |
| 旋转/尺寸变化 | `DisplayListener` + 点按路径自检；VD resize 适配；3 次连续失败 → 停止并提示 |
| 抓取时目标画面完全静止 | 隐藏→排空→settle 后可能无新帧 → `FAILED_NO_FRAME`，提示重试 |
| 抓取过程中停录 | 不再进入等待态；若停录落在取帧/推帧窗口内，已取到的帧仍可能上行，浮窗由 `stop` 隐藏（不新增等待态） |
| 系统侧停止投屏 | `MediaProjection.Callback.onStop` → `stopLocked()` 清理 |
| FGS 被系统销毁（录制/授权等待中） | 按代次自愈重启 FGS；采集资源继续 |
| stop 后迟到的 service ready | 代次/token 校验；不误启动；必要时兜底 stopService |
| 等待期再点浮窗 | SENT 胶囊 800ms 内触摸穿透；之后点按静默忽略，拖动可用 |
| 等待期收到无关 `ai_text`（非 agent、index=0） | 也会结束等待（判据是「某一轮开始」，不保证同一轮） |
| 15s 无回复 | 等待环消失，回 IDLE，无额外提示 |
| 等待期停录/重开 | 结束等待并复位 |
| 系统动画缩放=0 | 无脉动/无旋转；等待环静态整圈；不启动 ticker |
| omni 模式 | gating 禁止启动抓屏；若已在 omni，进入时停录；controller 的 omni 分支不发送 text |
| 旧 server 不支持 `payload.text` | 帧不触发文本回合；客户端等待环最多 15s 兜底（旧服务端对该帧的其他处理取决于其版本） |
| 进程被杀 | 抓屏会话/FGS 结束；下次启动重新授权 |
| 通知点击 | 打开 `MainActivity`（`NEW_TASK`） |
| 悬浮窗与系统边缘返回手势竞争 | 距边 4dp + 纵向拖拽为主；平台可能优先识别系统手势 |
| 敏感系统页隐藏 overlay | 平台防 tapjacking 行为；授权完成后才挂载浮窗 |
| 用户名/语言 | 抓屏指令按 App 语言取 `conv_image_action_text`（16 locale）；回复语言由服务端/LLM 决定 |

---

## 8. 测试

### 8.1 App 单测

| 文件 | 覆盖 |
|---|---|
| `ScreenCaptureControllerTest` | 缩放尺寸（1920 长边/偶数/小屏/横屏/非法输入）；tick 推新帧/keepalive/无帧；门控真值表；resize 判定（初始化/同尺寸/横竖互换/180°）；resize 连续失败计数与升级 |
| `ScreenCaptureControllerRaceTest` | stop→start 快速交替；stop 后 ready 兜底 stopService；service 销毁自愈；干净停止不重启；旧代次 ready 不消费新 token |
| `ScreenCaptureControllerWaitingTest` | 等待开始/15s 超时；首个非 agent `ai_text` 结束；空 final index=0 结束；agent/后续分片不结束；endWaiting 幂等；超时后 ai_text 无效 |
| `ScreenCaptureBubbleTest` | snapSide/snapX/clampY/isTap 纯函数；脉动相位（1fps 四步/禁用/负时间）；旋转相位（0/90/180/270/周期/负时间）；每个 feedback 有文案 |
| `ScreenCaptureBubbleRenderTest` | 等待环 270° 缺口 + 墙钟相位；动画缩放=0 静态整圈；IDLE 无环；等待不吞 SENT 胶囊 |
| `ConversationViewModelScreenCaptureTest` | 首次说明；已说明且权限齐备直接授权；录制中点击弹停止确认；开抓屏时相机开着先关相机；说明确认持久化；取消说明；缺悬浮窗权限重弹说明；跳设置页；返回授予/拒绝；消费一次性信号；授权转发；授权拒绝静默；停止确认/取消；外部停录复位；重复确认 no-op；录制中开相机先停录 |

- **2026-10-27 已实施**：抓屏/图库 text 断言改为 `context.getString(R.string.conv_image_action_text)`；`I18nResourceFilesTest` 覆盖新 key 的 17 目录齐平（见 `docs/android_multilingual.md` §21）。

### 8.2 SDK / 服务端

| 层 | 用例 |
|---|---|
| SDK `VideoSendPipelineTest` / `VideoTurnPayloadTest` | `payload.text` 透传、`turn_id`/`camera_facing`/`quality` 字段、text 省略语义 |
| 服务端 `test_video_text_turn.py` | 文本非空且过门控 → 起回合；空 text/无帧/模式门控不起；history/chat_store/recording 三件一体落盘；中断在飞回合 |
| 服务端 `test_video_frame_processing.py` | 相机 turn 首帧 / text 帧分流；帧缓存与 model 消费 |
| 服务端 `test_orchestrator_avdetector.py` | 带 text 帧不进入 avdetector |
| 服务端 omni 相关测试 | omni 忽略 text、feed_image |

### 8.3 真机/手工

- 首次启动：说明 → 悬浮窗设置 → 返回复查 → MediaProjection → 浮窗/通知；
- 切到目标 App 点按：隐藏/取帧/反馈/等待环；前台/锁屏/熄屏三种拒绝提示；
- 等待态：首分片结束、15s 超时、停录复位、等待期拖动；
- 旋转/横竖屏：抓取尺寸与内容方向；
- 相机互斥、非 chat 模式门控、会话结束/VM 销毁停录；
- FGS 通知点击回 App；FGS 被系统回收后的自愈；
- Go 设备/拒绝悬浮窗权限的失败提示；
- 动画缩放=0 的静态环/无脉动；
- 旧 server 无 `text` 支持时的客户端兜底表现（等待环最多 15s 退出）。

---

## 9. 文件索引

### 9.1 生产代码

| 层 | 文件 | 职责 |
|---|---|---|
| App | `media/ScreenCaptureController.kt` | MediaProjection/VD/ImageReader、点按抓取、等待态、旋转/resize、FGS 代次 |
| App | `media/ScreenCaptureBubble.kt` | 悬浮窗、拖拽/吸附、`~` 标记、状态环、胶囊、等待态 |
| App | `media/ScreenCaptureService.kt` | `mediaProjection` 前台服务 + 通知 |
| App | `media/OverlayPermissions.kt` | `canDrawOverlays()` 判定 |
| App | `media/CaptureTurnCounter.kt` | `turn_id` 进程级单调计数 |
| App | `ui/screens/conversation/ConversationViewModel.kt` | 启动/停止流程、gating、说明/停止确认、会话/相机互斥 |
| App | `ui/screens/conversation/ConversationUiState.kt` | `isRecording`、弹窗/一次性信号、`canScreenRecord` |
| App | `ui/screens/conversation/ConversationEvent.kt` | 录制/说明/停止/权限事件 |
| App | `ui/screens/conversation/ConversationScreen.kt` | 顶栏按钮、授权 launcher、说明/停止弹窗、悬浮窗设置页 |
| SDK | `LivaChatSDK.kt` / `VideoSendPipeline.kt` | `pushVideoJpeg(text=...)` 透传 |
| SDK | `LivaChatSDK/.../model/Orientation.kt` | `PORTRAIT_0` |
| Server | `services/session/orchestrator.py` | 文本回合、相机 turn、omni、avdetector 分流 |
| Server | `services/recording/sink.py` | 抓屏文本回合的录制落盘 |

### 9.2 测试

`app/src/test/.../media/ScreenCaptureControllerTest.kt`、`ScreenCaptureControllerRaceTest.kt`、
`ScreenCaptureControllerWaitingTest.kt`、`ScreenCaptureBubbleTest.kt`、`ScreenCaptureBubbleRenderTest.kt`；
`ConversationViewModelScreenCaptureTest.kt`；
SDK `VideoSendPipelineTest.kt`、`VideoTurnPayloadTest.kt`；
服务端 `test_video_text_turn.py`、`test_video_frame_processing.py`、`test_orchestrator_avdetector.py`。

---

## 10. 关键取舍与已知限制

| # | 取舍/限制 | 说明 |
|---|---|---|
| 1 | **单帧点按，不持续录屏** | `AUTO_TICK_ENABLED=false`；2s ticker/keepalive/门控代码保留，置回 true 可恢复，但当前产品语义是「一次点按 = 一帧」 |
| 2 | **抓取必须 App 在后台** | 避免采集自身/既往回图（隐私 + 回显循环）；前台点按提示切到其他应用 |
| 3 | **不插入本地用户气泡** | 服务端 history/chat_store 已记录 user 轮；App 实时聊天只有 AI 回复，浮窗反馈承担用户操作反馈 |
| 4 | **等待态是客户端近似** | 判据为「首个非 agent 的 index=0 ai_text」，不保证是本次抓取那一轮；15s 超时兜底 |
| 5 | **发图指令语言** | 已改为 `conv_image_action_text`（16 locale，随 App 语言、英文兜底）；回复语言由服务端/LLM 决定 |
| 6 | **浮窗无法从采集中排除** | 必须抓取前隐藏；目标画面完全静止时可能无新帧，按失败提示重试 |
| 7 | **App 前台不采集** | 与「不采集自身」的隐私不变量一致 |
| 8 | **Go 设备无悬浮窗** | `canDrawOverlays()` 恒 false；只能失败提示 |
| 9 | **FGS 通知常驻** | Android 平台要求；通知点击回 App |
| 10 | **顶栏按钮始终显示** | 非 chat 模式点击弹提示；进入非 chat 时强制停录（不是隐藏按钮） |
| 11 | **浮窗位置仅会话内记忆** | 停止后再启动复位为右侧/纵向居中；不写 DataStore |
| 12 | **resize 连续失败会停止会话** | 3 次失败 → `capture_stopped`；重建 VD 有单次授权风险，stop 是安全升级路径 |
| 13 | **动画缩放=0** | 无脉动/无旋转；等待环静态整圈；零自绘功耗 |
| 14 | **omni 模式不可启动抓屏** | `canScreenRecord` 排除 omni；controller 仍保留 omni 不发送 text 的防御分支 |
| 15 | **旧 server 不支持 `text`** | 帧不触发文本回合；客户端只保证 15s 兜底；旧服务端对该帧的其他处理取决于其版本；客户端与服务端需同步升级 |
| 16 | **抓屏尺寸/质量** | 长边 ≤1920、JPEG q70；静态桌面约 160KB/帧，base64 后约 212KB |
| 17 | **系统边缘手势竞争** | 浮窗距边 4dp + 纵向拖拽为主；平台可能优先识别返回手势 |
| 18 | **敏感系统页隐藏 overlay** | 权限页/投屏授权页等平台行为；浮窗在授权完成后才挂载 |
