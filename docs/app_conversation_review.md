# LivaChat ConversationScreen 设计与实现 Review

**日期**: 2026-06-20
**范围**: `apps/android/LivaChatApp/` 中 ConversationScreen 及其全部关联组件

---

## 一、架构设计

### 1.1 ViewModel 承担过多职责

`ConversationViewModel`（233 行）同时处理：SDK 生命周期、音频/摄像头控制、打断协调、Room 消息订阅、UI 状态组合、相机绑定、音频路由和触觉反馈。按设计文档的分层架构，这些应该通过 UseCase 和 App Media Layer 承担。

**具体问题：**

- `onScreenStart` 直接调用 `audioCapture.start()`, `audioPlayback.start()`, `interruptCoordinator.start()` — 应该封装在 `SessionUseCase.startSession()` 里
- 摄像头绑定逻辑 (`bindCamera`, `toggleCamera`, `switchCamera`) 直接管理 `LifecycleOwner` 和 `PreviewView` 引用

**建议：** 引入 `ConversationOrchestrator` 或扩展 `SessionUseCase` 来编排 Media 生命周期，让 ViewModel 只负责 UI state 映射和事件分发。

### 1.2 UiState 单一巨石导致重组浪费

`ConversationUiState` 包含 14 个字段，任何单个字段更新都会触发整个 `ConversationScreen` 重组树：

```kotlin
_uiState.value = _uiState.value.copy(
    currentTranscription = transcription,  // 高频变化，每 ~200ms
    isTranscriptionFinal = transFinal,
)
```

**建议：** 拆分为独立 StateFlow — `connectionUiState`, `mediaUiState`, `chatUiState`, `transcriptionState` — 或在 `collectAsState` 时使用 `derivedStateOf` 防止不必要的子树重组。

### 1.3 设计文档与实现差异

| 设计文档要求 | 实际实现 | 影响 |
|-------------|---------|------|
| LazyColumn `reverseLayout = true` | 普通正向排列，手动滚动到底部 | 新消息出现位置不一致 |
| 最大可见 10 条消息 | 无限制 | 长对话可能卡顿 |
| Camera2 SurfaceView | CameraX PreviewView + COMPATIBLE 模式 | 额外纹理拷贝 |
| 视频模式消息叠加（4 条、半透明）| voice-only 与 video 互斥切换 | 视频模式无文字对话 |
| 双指缩放 (1.0x-3.0x) | `setZoom()` 只有空注释 | 不可缩放 |
| 音频焦点管理 | `AudioRouteManager` 存在但无调用者 | 来电话时不会自动暂停 |
| 打断状态机 150ms 快速动画 | 无对应实现 | 打断反馈延迟 |

---

## 二、性能问题

### 2.1 CameraController 视频管线双重 JPEG 编码

```kotlin
private fun processFrame(imageProxy: ImageProxy) {
    val bitmap = imageProxyToBitmap(imageProxy)  // YUV → NV21 → JPEG → Bitmap
    sdk.pushVideoBitmap(bitmap, orientation)      // Bitmap → JPEG (again!)
    imageProxy.close()
}
```

**实际流程：** `CameraX ImageProxy (YUV_420_888)` → 手动拼接 NV21 → `YuvImage.compressToJpeg()` → `ByteArray` → `BitmapFactory.decodeByteArray()` → `Bitmap` → `sdk.pushVideoBitmap()` → SDK 内部再次 `Bitmap.compress(JPEG)` → Base64。

JPEG 被编码了 **两次**，第一轮完全是浪费。此外 `imageProxyToBitmap` 可以简化为 `ImageProxy.toBitmap()` (CameraX 内置方法)。

**建议：** 增加 SDK API `pushVideoJpeg(jpegBytes: ByteArray, ...)` 绕过 SDK 的 `Bitmap.compress`，或者直接在 App 层把 `ImageProxy` 转成 JPEG bytes 交付 SDK。

### 2.2 AudioCaptureController 不必要的 copyOf

```kotlin
val data = if (_isMuted.value) zeroFrame else frame
sdk.pushAudioPcm(data.copyOf(read), 0, read)  // 每 20ms 分配一次 ShortArray
```

每 20ms 在 IO 线程分配一块新堆内存再提交协程，GC 压力极大。SDK 已在 `AudioSendPipeline.pushPcm` 内部做了复制。

**建议：** `sdk.pushAudioPcm(frame, 0, read)`。

### 2.3 onAiText 全量 DB 查询

```kotlin
override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean) {
    scope.launch {
        val existing = chatRepository.getMessages(sessionId) // 每个 chunk 全量查询
        val existingMsg = existing.find { it.id == "ai-$messageId" }
        // ...
        chatRepository.appendMessage(message)
    }
}
```

流式 AI 文本每秒到达 10-20 个 chunk。每个 chunk 走一次全量 `getMessages(sessionId)`。对话越长越重。

**建议：** 在 `ConversationListenerAdapter` 内部维护 `ConcurrentHashMap<String, ChatMessage>` 作为写缓存，仅 `isFinal` 时写入 Room。或用 DAO 的 `getById()` 替代全量查询。

### 2.4 LazyColumn 自动滚动

```kotlin
LaunchedEffect(uiState.messages.size) {
    if (uiState.messages.isNotEmpty()) {
        listState.animateScrollToItem(uiState.messages.size)  // 越界 + 高开销动画
    }
}
```

两个问题：

1. **索引越界** — `messages.size` 是 count 不是 index，应为 `messages.size - 1`
2. **高频动画** — 流式 AI 文本每几十 ms 一个 chunk，`animateScrollToItem` 产生大量动画帧

**建议：** 使用 `snapToItem` 代替 `animateScrollToItem`，或仅当用户已在底部时自动滚动。

### 2.5 线程安全问题

**CameraController.lastFrameTimeMs（非同步访问）：**

```kotlin
private var lastFrameTimeMs = 0L  // CameraX analyzer thread 写入，无同步
```

**AudioCaptureController 状态字段：**

```kotlin
private var retryCount = 0              // IO 线程写入
private var currentSampleRate: Int = 16_000  // 主线程读取，IO 线程读取
```

**建议：** `lastFrameTimeMs` 加 `@Volatile`（单 analyzer 线程时安全），或使用 `AtomicLong`。`retryCount` 等应在单一调度器中管理。

---

## 三、功能实现

### 3.1 AudioTrack 阻塞写入与打断的竞态

```kotlin
// AudioPlaybackController.onAiAudioPcm (SDK 音频回调线程)
track.write(pcm, 0, pcm.size, AudioTrack.WRITE_BLOCKING)

// AudioPlaybackController.flush (主线程，InterruptCoordinator 触发)
track.pause()
track.flush()
track.play()
```

`WRITE_BLOCKING` 可能在缓冲区满时阻塞几百毫秒。在此期间 `flush()` 被调用时，打断延迟可达 ~200ms，违背设计文档"SPEAKING→LISTENING 快速动画 150ms"的要求。

**建议：** 使用 `WRITE_NON_BLOCKING` + 自管理写循环，或使用 callback-based 写入。

### 3.2 视频模式与语音模式应共存

当前实现中视频和语音内容是互斥的：

```kotlin
AnimatedContent(targetState = uiState.videoVisibility) { visibility ->
    when (visibility) {
        VideoVisibility.VISIBLE -> VideoPreview(...)
        else -> ConversationVoiceContent(...)
    }
}
```

设计文档明确要求视频模式下消息气泡应叠加在视频上方（半透明，最多 4 条可见）。当前实现在视频模式下完全看不到对话内容。

**建议：** 改为叠加布局 — 视频全屏作为背景层，消息气泡和转录气泡叠加在上方。

### 3.3 会话恢复和断线重连的状态一致性

```kotlin
sdk.connect()          // 异步
audioCapture.start()   // 同步
audioPlayback.start()  // 同步
```

若 `sdk.connect()` 成功后断开又重连，ViewModel 的 `sessionActive = true` 和 `sdk.connectionState == DISCONNECTED` 可能进入不一致状态。

**建议：** 在 `connectionState` 的回调中统一管理 Media 层的启停，而非在 `onScreenStart` 中一次性的 fire-and-forget。

### 3.4 ErrorBanner 反复重建

```kotlin
// ErrorBanner — AnimatedVisibility 内部的 LaunchedEffect
LaunchedEffect(msg.id) {
    delay(5000)
    onDismiss()
}
```

如果错误是可恢复的网络错误，重试仍然失败时将快速触发新的 `BannerMessage`（每次 `UUID.randomUUID()`），LaunchedEffect 不断被取消和重建，5 秒计时器永远无法触发。

**建议：** 加防抖 — 连续相同的可恢复错误，延迟退避直到不再触发新的错误。

### 3.5 缺失的功能入口

- 音频路由底部弹出面板的 `onSelectRoute` 回调为空操作，未接入 `AudioRouteManager`
- `AudioRouteManager` 存在但无任何调用者，缺少音频焦点生命周期管理
- 连接中状态缺少骨架屏显示（`LoadingSkeleton` 已定义但未使用）
- 缺少"打断"的 UI 反馈 — 当前 AI 被打断后无视觉提示

---

## 四、测试覆盖

### 4.1 当前状态

**ConversationViewModelTest** — 仅 3 个测试：
- 验证 UI 默认值
- 验证 Event sealed class 数量
- 验证 VideoVisibility enum 条目数

无 mock 依赖的 ViewModel 初始化测试、无状态转换测试、无协程调度验证。

**ConversationListenerAdapterTest** — 6 个测试：
- 1 个被 `@Ignore` 标记（依赖 `Dispatchers.Main` 未设置）
- 其余为 smoke test，验证方法不崩溃

**缺失：**
- 无 Composable UI 测试（Screenshot test / Compose testing）
- 无 Media 层与 SDK 的真实集成测试
- 无打断逻辑的状态机测试
- 无断线重连场景测试

### 4.2 建议

- 使用 `kotlinx-coroutines-test` 注入 `TestDispatcher` 来测试 ViewModel 和 ListenerAdapter
- 使用 Compose Testing library (`createComposeRule`) 测试 ConversationScreen 渲染
- `InterruptCoordinator` 应至少有一个状态组合验证测试

---

## 五、优先级建议

### 高优先级（影响正确性和用户体验）

1. ~~LazyColumn 滚动索引越界 — `messages.size` → `messages.size - 1`~~
2. ~~`onAiText` 全量 DB 查询改为内存缓存 + 仅最终写入~~
3. 视频模式与语音模式共存（消息叠加层），而非互斥
4. ~~`AudioCaptureController.copyOf()` 移除，减少 GC 压力~~
5. ~~CameraController 视频管线去重（移除双重 JPEG 编码）~~

### 中优先级（性能优化和合规）

6. UiState 拆分，减少重组范围
7. `animateScrollToItem` 改为条件性 `snapToItem`
8. `lastFrameTimeMs` 加 `@Volatile` / `AtomicLong`
9. AudioTrack 用 `WRITE_NON_BLOCKING` 降低打断延迟
10. 接入 `AudioRouteManager` 的音频焦点管理

### 低优先级（完善性）

11. 视频模式下半透明消息叠加 + 最大可见数量限制
12. CameraX pinch-to-zoom 实现
13. 连接中断线/加载骨架屏
14. 补充 ViewModel 和 Composables 的单元/UI 测试
