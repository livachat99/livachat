# iOS App / SDK 内存泄漏与内存越界风险排查

**日期**: 2026-10-25
**范围**:
- `apps/ios/LivaChatApp/`（App，SwiftUI + AVFoundation）
- `apps/ios/LivaChatSDK/`（SDK，含 `COpus` / `CVad` / `CWebSocketClose` C/ObjC 桥）
- `packages/sdk-swift/`（协议 DTO / JSON 编解码）
- `apps/ios/deps/NetworkImage/`（App 内 vendored 依赖，实际承载时间线图片加载）

**方法**: 逐文件静态审计（音频采集/播放、视频采集、WebSocket 传输、SQLite、图片解码与缓存、SwiftUI 生命周期），
重点覆盖：`installTap` / `AVAudioPlayerNode` / `URLSession`+delegate / `DispatchSourceTimer` / `NotificationCenter` /
`AsyncStream` / `NSCache` / `CGImageSource` / `sqlite3` C 指针 / `unsafeBitCast` / 指针与整数强转。

**未做**: 未运行 Instruments / ASan / TSan（沙箱无设备），所有结论均标注置信度；置信度为「中」的条目建议按第九节实测确认。

> **2026-10-25 复核更新**
>
> 本稿已按当前 HEAD（`83dce6d`）逐条复核，并完成第一批整改。复核结论：28 条中 **17 条成立、8 条需实质性修正、3 条不成立**（RACE-01/02/03）。复核同时确认了构建配置对结论的影响：Swift 6.0、`SWIFT_STRICT_CONCURRENCY=complete`、Release 使用 `-O`（`project.pbxproj:1433`），因此 `precondition` 在 Release 仍然生效。
>
> **第一批已修复**：LEAK-01、LEAK-02、GROW-01、GROW-02、GROW-03、OOB-01。各条正文以「✅ 已修复」标注实现与验证；未修复项保留原文，并以「复核修正」标注错误或定级调整。完整对照见文末「十一、复核记录」。

---

## 一、结论摘要

共发现 **28** 条风险：**P1 = 9 条**（LEAK-01/02/03/05、OOB-01、GROW-01/02/03、RACE-01），
P2 = 11 条，P3 = 8 条。无 P0。

> **复核后定级**：17 条成立、8 条需实质性修正、3 条不成立（RACE-01/02/03）。P1 收敛为
> **GROW-02、GROW-03** 两条无争议项；LEAK-02、OOB-01 保留为第一批防御性修复；其余原 P1 降级。
> 下表「级别」列为初稿定级，复核后的定级与处理结果见文末「十一、复核记录」。

| ID | 级别 | 类别 | 位置 | 结论 |
|----|------|------|------|------|
| LEAK-01 | **P1** | 泄漏 | `NetworkImage/NetworkImageLoader.swift:63-96` | 图片下载失败时 `ongoingTasks` 条目永不删除，Task + URL 随失败 URL 数量线性泄漏 |
| LEAK-02 | **P1** | 泄漏 | `Core/Mylife/MylifeImageLoader.swift:21-29` | 每次重连新建 `URLSession`+`URLCache(10MB/100MB)`+delegate，且从不 `invalidate` |
| LEAK-03 | **P1** | 资源未释放 | `Core/Audio/MylifeAudioPlayer.swift`（无 `deinit`） | `AVAudioEngine.attach` 的节点永久挂载 + `tmp/livachat-audio-*.m4a` 残留 |
| LEAK-04 | P2 | 资源未释放 | `Core/Audio/AudioCaptureController.swift`（无 `deinit`） | 未 `stop()` 即释放时 tap 仍驻留共享 engine，麦克风/回调持续存活 |
| LEAK-05 | **P1** | 无界缓冲 | `LivaChatSDK/LivaChatSDK.swift:68` | `AsyncStream(bufferingPolicy: .unbounded)`，消费者停止后事件（含图片 Data）在缓冲中无界堆积 |
| LEAK-06 | P2 | 泄漏 | `Core/History/HistoryDatabase.swift:42-48` | `close()` 忽略 `sqlite3_close` 返回值并置空句柄；`HistoryDatabase` 全生命周期无调用者 |
| LEAK-07 | P2 | 泄漏 | `Core/Video/CameraController.swift:74-77` | `deinit` 不 `stop()` 会话；`AVCaptureVideoDataOutput` 强持有 delegate（`CameraFramePipeline`） |
| LEAK-08 | P3 | 泄漏 | `Core/Mylife/MylifeStore.swift:347-371` | `pollTask` 无 `deinit` 兜底；`guard let self` 跨越 60s sleep 持强引用 |
| LEAK-09 | P3 | 磁盘无界 | `Core/Platform/AppLog.swift:31` / `SdkLogger.swift:38` | 日志 append-only，无轮转/截断 |
| OOB-01 | **P1** | 越界/陷阱 | `Core/Audio/AudioCaptureController.swift:789-790` | `ratio = 16k / buffer.format.sampleRate`；采样率为 0 时 `+inf` → `AVAudioFrameCount(inf)` **运行时陷阱崩溃** |
| OOB-02 | P2 | 数值陷阱 | `Core/Audio/MylifeAudioPlayer.swift:343,355` | `Double`/`Int64` → `AVAudioFramePosition`/`AVAudioFrameCount` 无范围校验 |
| OOB-03 | P2 | 前置条件失效 | `LivaChatSDK/Internal/Pipeline/FrameAssembler.swift:12-15`、`PCMChunker.swift:9` | `precondition` 在 Release 被编译掉，负/零 frameSamples 会直接构造越界数组 |
| OOB-04 | P2 | C 边界 | `Sources/COpus/COpus.c:27-37`、`Sources/CVad/CVad.c:22-30` | C 层不校验 `frame_samples` / `frame_length` 上界，完全依赖 Swift 调用方正确 |
| OOB-05 | P3 | 防御缺失 | `AudioCaptureController.swift:820-823`、`AudioPlaybackController.swift:339` | `AVAudioPCMBuffer.frameLength` 直接当样本数使用，未与 `frameCapacity` 取 min |
| OOB-06 | P3 | C 边界 | `HistoryDatabase.swift:424-430` | `Optional<OpaquePointer>` 传入 IUO C API（`sqlite3_errmsg(handle)`），错误信息误导 |
| GROW-01 | **P1** | 峰值内存 | `Core/Audio/JitterBuffer.swift:10,51` | `defaultMaxLatencyFrames = 15_000` 被当作**帧数**上限（≈5 分钟音频，9.6–57.6 MB） |
| GROW-02 | **P1** | 无界增长 | `Core/History/HistoryService.swift:380-410` | 载入历史会话时把该会话**所有**图片原图 Data 读进内存并挂在 `ChatMessage.image` |
| GROW-03 | **P1** | 峰值内存 | `Core/Conversation/ConversationViewModel.swift:31`、`Features/Conversation/AIImageView.swift:46-70` | 会话图片 Data 永久驻留 + `NSCache(countLimit:20)` 无 costLimit 的全尺寸解码位图 |
| GROW-04 | P2 | 峰值内存 | `Features/Timeline/Gallery/GalleryViewerOverlay.swift:44-52` | `TabView(.page)` + `ForEach` 全量实例化，N 页同时发起原图下载/解码 |
| GROW-05 | P2 | 峰值内存 | `Core/PlatformAdapters/CameraPhotoPicker.swift:41` | 主线程对原图 `UIImage.jpegData(0.9)`，12MP ≈ 48 MB 位图 + 编码缓冲 |
| GROW-06 | P2 | CPU/分配抖动 | `Core/Conversation/ConversationCoordinator.swift:486` | `currentAIText += text` 逐 token 字符串拼接（O(n²)）+ 每 token 重解析 Markdown |
| GROW-07 | P2 | 分配抖动 | `Core/Audio/PCMChunker.swift:13-23` | `removeFirst` 循环迁移 `pending`，大 chunk 时 O(n²) 内存搬运 |
| GROW-08 | P3 | 缓存无界 | `MarkdownMessageView.swift:21-25` / `AgentJobCollapse.swift:21-25` | 静态 `NSCache` 只按条数（50）限制，流式文本每次生成新 key |
| GROW-09 | P3 | 平台缓存 | `NetworkImage/NetworkImageCache.swift:21-31` | 仅 `countLimit = 100`，无 `totalCostLimit`，无降采样 |
| RACE-01 | **P1** | 数据竞争 | `ConnectionManager.swift:68-74` vs `connectInternal/cleanupSession` | `deinit` 在任意线程清理「connection 队列私有」状态（`session`/`delegate`/`webSocketTask`） |
| RACE-02 | P2 | 数据竞争 | `CameraController.swift:202,260-278` vs `384,416` | `videoOutput` 在 sessionQueue 写、Main 读；`AVCaptureConnection` 被跨线程改写 |
| RACE-03 | P3 | 竞争 | `SdkLogger.swift:42-52` / `AppLog.swift:34-38` | `deinit` 读 `fileDescriptor` 与 log 队列写入 / `close(fd)` 存在窗口 |
| RACE-04 | P3 | 生命周期 | `Core/Conversation/AgentJobStatusModel.swift` | `canceler` 与 `Task` 在 runtime 销毁后仍可能回调（由 weak self 兜住） |

> **复核后（2026-10-25）**：RACE-01/02/03 不成立；LEAK-01/05/06、OOB-02/03/04、GROW-06/08 有实质性修正；原 9 条 P1 收敛为 2 条无争议项（GROW-02/03）。逐条变化见第十一节。

> **已核查确认无风险**的项（避免误报）：见第七节。

---

## 二、级别定义

| 级别 | 定义 |
|------|------|
| **P0** | 必现崩溃 / 稳定复现的严重泄漏（本次未发现） |
| **P1** | 特定但常见的用户路径下必现的泄漏、越界或数 MB 级内存增长 |
| **P2** | 需要组合条件（异常路由、失败重试、大图、长会话）才触发 |
| **P3** | 健壮性/可维护性建议，或影响有限的资源浪费 |

---

## 三、内存泄漏

### LEAK-01 · P1 → P2 · 图片下载失败时 `ongoingTasks` 永不清理
**位置**: `apps/ios/deps/NetworkImage/Sources/NetworkImage/NetworkImageLoader.swift:24,58-99`

```swift
private var ongoingTasks: [URL: Task<CGImage, Error>] = [:]

public func image(from url: URL) async throws -> CGImage {
  if let image = self.cache.image(for: url) { return image }
  if let task = self.ongoingTasks[url] { return try await task.value }

  let task = Task<CGImage, Error> {
    let (data, response) = try await self.data(url)   // ← 抛错即直接跳出
    self.ongoingTasks.removeValue(forKey: url)        // ← 只在成功路径执行
    ...
  }
  self.ongoingTasks[url] = task
  return try await task.value
}
```

**触发**: 任意图片请求失败（超时、DNS、连接被拒、TLS 失败、离线）。
**影响**: `Task`（含捕获的 `URL`、`Error`、actor 上下文）永久驻留字典；`MylifeImageLoader` 的每个时间线图片 URL 都是唯一的，失败重试/弱网下条目数单调增长，且**同一 URL 之后不再重试**（缓存了失败结果）。
**建议**:
```swift
let task = Task<CGImage, Error> {
  defer { self.ongoingTasks.removeValue(forKey: url) }   // 所有出口都清理
  ...
}
```
同时把「失败」与「进行中」区分开（失败结果不缓存），并对 `Task.checkCancellation()` 做处理。
**置信度**: 高（代码路径确定）。

**状态**: ✅ **已修复（2026-10-25，第一批）**
- `NetworkImageLoader.swift` 的 Task 改为 `defer { self.ongoingTasks.removeValue(forKey: url) }`，所有出口（含传输错误、取消）都会清理；`shutdown()` 会取消在途任务。
- 新增 `DefaultNetworkImageLoaderTests.testTransportFailureIsRetried`（失败后第二次请求会重试）与 `testShutdownRejectsNewLoads`。

**复核修正**：
- `removeValue` 原本就在 `data(url)` 返回后、HTTP 状态/解码检查**之前**执行（`:71`），只有传输层抛错（`:68`）会跳过；HTTP 4xx/5xx、解码失败都会清理。原稿“只在成功路径执行”不准确。
- 泄漏对象是 Task+URL+URLError，量级很小，不是“数 MB”；真正的用户影响是**同一 URL 在瞬时网络失败后不再重试**。
- 定级 P1 → **P2**（功能影响为主）。

---

### LEAK-02 · P1 → P2 · `URLSession` 反复创建且从不 `invalidate`
**位置**: `apps/ios/LivaChatApp/Core/Mylife/MylifeImageLoader.swift:5-30`；
`apps/ios/deps/NetworkImage/Sources/NetworkImage/NetworkImageLoader.swift:34-46,102`

```swift
static func make(serverBase: ServerBase) -> DefaultNetworkImageLoader {
    configuration.urlCache = URLCache(memoryCapacity: 10 * 1024 * 1024,
                                      diskCapacity: 100 * 1024 * 1024)
    let session = URLSession(configuration: configuration,
                             delegate: delegate, delegateQueue: nil)   // ← 无 owner 负责 invalidate
    return DefaultNetworkImageLoader(cache: DefaultNetworkImageCache(countLimit: 100),
                                     session: session)
}
```

调用链：`AppContainer.applyServerSettingsAndReconnect()` → `openConversationIfNeeded()` → `mylifeImageLoader = MylifeImageLoader.make(...)`。
每次改服务器/重连都会新建一套 `URLSession` + 10 MB 内存缓存 + 100 MB 磁盘缓存 + `ProbeSessionDelegate`（session 强持有 delegate）。

**影响**: Apple 明确要求 `invalidateAndCancel()` / `finishTasksAndInvalidate()` 收尾，否则 session 底层的 `CFURLSession`、连接池、delegate、`URLCache` 生命周期不确定。反复重连时内存（≥10 MB/次的内存缓存）+ 磁盘缓存碎片叠加。App 内其他客户端（`ServerProbe`、`AgentJobCancelClient`、`MylifeAPIClient`）都正确实现了 `deinit { invalidate }`，唯独图片 loader 缺失。
**建议**: 在 `DefaultNetworkImageLoader` 增加 `deinit`/`shutdown()`；或让 `AppContainer` 在替换 loader 前显式关闭旧 session；`URLCache` 改为全 App 单例。
**置信度**: 高（模式缺失确定），具体泄漏量取决于 Foundation 实现，建议用 Instruments → Allocations 过滤 `NetworkImageLoader` 实测。

**状态**: ✅ **已修复（2026-10-25，第一批）**
- `DefaultNetworkImageLoader` 现在持有 `session`，新增 `shutdown()` 和 `deinit { session?.invalidateAndCancel() }`。
- `AppContainer.discardMylifeImageLoader()` 在替换/清空 loader 前调用 `shutdown()`（`applyServerSettingsAndReconnect`、启动失败、UI test 配置等路径）。
- 新增 `DefaultNetworkImageLoaderTests.testShutdownRejectsNewLoads`。

**复核修正**：
- 重建只发生在首次进入会话和应用服务器设置，不是“每次网络重连”；`MylifeImageLoader.make` 的调用点是 `AppContainer.swift:421`、`:581`。
- 10 MB 是 `URLCache` 的容量上限，不是立即分配；但未 invalidate 的 `URLSession` 确实会保留 delegate/session（已用最小程序验证）。
- 定级 P1 → **P2**（修复成本低，保留第一批）。

---

### LEAK-03 · P1 → P2 · `MylifeAudioPlayer` 无 `deinit`：引擎节点与临时文件泄漏
**位置**: `apps/ios/LivaChatApp/Core/Audio/MylifeAudioPlayer.swift:51,255-290,389-406`

```swift
try data.write(to: url, options: .atomic)        // :259  tmp/livachat-audio-<uuid>.m4a
node = AVAudioPlayerNode(); engine.attach(node)  // :266-269  engine 强持有 node
mediaNode = node; mediaNodeEngine = engine
```
释放路径只有 `releaseMediaFile()` / `detachMediaNode()`，二者仅被 `stop()`、`pause()`、ticker、`handleMediaFinished` 调用。

**触发**: 视图被销毁但没走到 `stop()`（`TimelineFlowView.onDisappear` 未触发，例如父视图被整体替换、
`fullScreenCover`/`sheet` 状态被强制重置、或后续重构遗漏）。`MylifeAudioPlayer` 本身没有 `deinit` 兜底。
**影响**:
1. `AVAudioEngine` 在 `attach` 后持有该节点（必须 `detach` 才会释放），没有 `detach` 则该节点（及其 connection）
   随共享 engine 存活到 App 结束；
2. `tmp/livachat-audio-*.m4a` 文件残留（`releaseMediaFile` 是唯一的 `removeItem` 调用点），长期使用后磁盘持续增长；
3. `AVAudioFile` 句柄未关闭（文件删除也失败，因 fd 仍打开）。

**建议**: 增加
```swift
deinit {
    // 不能触碰 @MainActor 状态，使用拆解点
    mediaNode?.stop(); mediaNodeEngine?.disconnectNodeOutput(mediaNode!)
    mediaNodeEngine?.detach(mediaNode!)
    if let mediaFileURL { try? FileManager.default.removeItem(at: mediaFileURL) }
}
```
（`deinit` 属于 nonisolated，需要用 `nonisolated(unsafe)` 包装或把拆解收敛到一个 nonisolated 的 `TearDown` 结构体里。）
**置信度**: 高（缺失确定；触发概率取决于 UI 路径）。

**复核修正（未修复）**：
- `TimelineFlowView.onDisappear` 会调用 `flow.stop()` → `TimelineFlowModel.stop()` → `audioPlayer.stop()`（`TimelineFlowView.swift:58`、`TimelineFlowModel.swift:36-39`），正常关闭路径会清理。
- `startOnEngine` 的 catch 分支也会删除临时文件（`MylifeAudioPlayer.swift:307`），原稿“releaseMediaFile 是唯一的 removeItem 调用点”不准确；`removeItem` 在 fd 仍打开时**可以成功**（空间在 close 时释放），原稿“删除也失败”不成立。
- `AVAudioEngine` 是 runtime 级（`ConversationRuntimeFactory.swift:17-18`），不是 App 级。
- 触发需要 `onDisappear` 未执行，定级 P1 → **P2**。

---

### LEAK-04 · P2 → P3 · `AudioCaptureController` 无 `deinit`：tap 残留
**位置**: `apps/ios/LivaChatApp/Core/Audio/AudioCaptureController.swift:340-356,364-379`

```swift
inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
    self?.handleTap(buffer)
}
tapInstalled = true
```
`removeTap` 只出现在 `performStartAttempt` / `teardownLocked`（由 `stop()` 驱动）。

**影响**: 若控制器被释放而未调用 `stop()`，tap block 仍挂在共享 `AVAudioEngine` 的 inputNode 上。
block 内是 `[weak self]`，不会造成控制器泄漏，但 **AURemoteIO 会继续以 1024 帧频率回调**（空转），
麦克风指示灯、功耗、以及「engine 仍在跑但无消费者」的状态都会保留；`AudioSessionController` 与
`AudioCaptureController` 共享同一个 engine，风险被放大。
**建议**: 增加 `deinit { if tapInstalled { engine.inputNode.removeTap(onBus: 0) } }`（该调用线程安全，且
`removeTap` 会同步 flush 在途回调，注意与现有 `controlLock` 的死锁注释保持一致）。
**置信度**: 中高。

**复核修正（未修复）**：controller 由 `ConversationRuntime` 持有，`AppContainer.stopRuntime` 必先调用 `runtime.stop()` 再置 nil，正常 teardown 会走 `capture.stop()`；engine 随 runtime 释放。触发路径很窄，定级 P2 → **P3**（防御性加固）。

---

### LEAK-05 · P1 → P2 · SDK 事件流使用 `.unbounded` 缓冲
**位置**: `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatSDK.swift:68-72`

```swift
let events = AsyncStream(bufferingPolicy: .unbounded) { continuation = $0 }
```

消费端只有一个：`ConversationEventAdapter.start(_:)`（`Core/Conversation/ConversationEventAdapter.swift:15-23`），
其 `Task` 在 `runtime.stop()` 时被 `cancel()`。

**触发**:
1. `runtime.stop()` 之后 SDK 仍在运行（`LivaChatSDK` 由 `ConversationRuntime` 与闭包多处强持有，见
   `AudioCaptureController` 的 `uplink`、`CameraController` 的 `pushJPEG`），此时 `connectionManager.onMessage` /
   `onStateChange` / `onPing` 仍会 `continuation.yield(...)`；
2. MainActor 长时间被占用（大图解码、SQLite 写、`jpegData`）导致消费者变慢。

**影响**: 事件在 `AsyncStream` 缓冲里无界堆积。其中 `aiText` 事件可能携带 `AiImage`（**base64 解码后的原始
JPEG Data**，见 `SessionManager.handleInbound` 的 `.aiText` 分支），单条即数百 KB～数 MB，堆积 100 条即数十 MB。
**建议**:
- 改为 `AsyncStream(bufferingPolicy: .bufferingNewest(256))` 并对 `.aiText` 的 image 做丢弃降级；
- `stop()` 后显式 `connectionManager.disconnect()`，避免「无消费者仍在产事件」；
- 或在 continuation 上做背压统计（yield 前检查 `bufferedCount`）。
**置信度**: 中（缓冲无界确定；实际堆积量取决于 stop 后的产事件速率）。

**复核修正（未修复）**：
- `ConversationRuntime.stop()` → `ConversationEventAdapter.stop()` 后，`ConversationCoordinator.stop()` 会调用 **`sdk.disconnect()`**（`ConversationCoordinator.swift:174`），连接关闭且 `shutdown=true` 不再重连；`onPing` 也不 yield 事件。原稿“stop 后 SDK 仍运行并持续 yield”不成立。
- 无界缓冲在“连接期间 MainActor 消费者变慢”时仍成立；取消消费后，已缓冲的事件会保留到 SDK deinit 的 `eventContinuation.finish()`（`LivaChatSDK.swift:275`）。
- 直接改成 `bufferingNewest` 会丢弃 `connectionTerminated`/`sessionChanged` 等状态事件，应按事件类型定策略。
- 定级 P1 → **P2**。

---

### LEAK-06 · P2 → P3 · SQLite 连接关闭路径不严谨、且从不关闭
**位置**: `apps/ios/LivaChatApp/Core/History/HistoryDatabase.swift:42-48`

```swift
func close() {
    if let handle { sqlite3_close(handle) }   // ← 忽略 SQLITE_BUSY
    handle = nil                              // ← 即便未真正关闭也置空
}
```
`HistoryService.storage`（`HistoryDatabase`）在 `AppContainer` 生命周期内常驻，`close()` **无任何调用者**；
`actor HistoryDatabase` 也没有 `deinit`。

**影响**:
1. `sqlite3_close` 返回 `SQLITE_BUSY`（仍有未 finalize 的 stmt）时连接不会被释放，但 `handle` 被置 nil →
   连接及其 page cache / WAL 索引内存泄漏，且之后再也无法关闭；
2. 应用内唯一连接不关闭 —— 影响有限，但 WAL 文件与 `-shm` 会常驻；`clearAll()` 后不做 `wal_checkpoint(TRUNCATE)`。
**建议**: 校验返回值并保留句柄；新增 `deinit { sqlite3_close_v2(handle) }`（`close_v2` 允许延迟关闭）；
`clearAll()`/`deleteAll()` 末尾 `PRAGMA wal_checkpoint(TRUNCATE)`。
**置信度**: 高（代码确定），影响量级小。

**复核修正（未修复）**：生产代码确实没有 `close()` 调用者，但测试有调用（`HistoryStorageTests.swift:63` 等）。所有语句都以 `defer { sqlite3_finalize }` 收尾（：68、:85、:106 等），正常路径不会出现未 finalize 的 stmt，`SQLITE_BUSY` 路径实际不可达；WAL/`-shm` 常驻和 checkpoint 建议仍有效。定级 P2 → **P3**。

---

### LEAK-07 · P2 → P3 · 相机 `deinit` 不停止会话；output 强持有 delegate
**位置**: `apps/ios/LivaChatApp/Core/Video/CameraController.swift:74-77,218`

```swift
deinit {
    notificationTokens.forEach(NotificationCenter.default.removeObserver)
    orientationMonitor.stop()      // ← 没有 session.stopRunning() / setSampleBufferDelegate(nil)
}

videoOutput?.setSampleBufferDelegate(pipeline, queue: videoQueue)   // :202  output 强持有 pipeline
```
`AVCaptureVideoDataOutput` 会持有其 `sampleBufferDelegate`（未公开声明为 weak；实践中必须通过
`setSampleBufferDelegate(nil, queue: nil)` 才能释放），而 `pipeline` 持有 `ciContext` 与
`pushJPEG` 闭包（闭包强持有 `LivaChatSDK`）。

**影响**: 若控制器在拍摄中被释放（`stop()` 未调用），`session` 仍持有 input/output，output 仍持有 pipeline，
pipeline 仍持有 SDK 闭包 —— 形成 `CameraController → session → output → pipeline → sdk` 的存活链，
相机指示灯与功耗持续，直到整条链被释放。
**建议**: `deinit` 内 `videoOutput?.setSampleBufferDelegate(nil, queue: nil)` + `session.stopRunning()`
（`stopRunning()` 是阻塞调用，建议在 `sessionQueue` 上异步执行或在 `deinit` 里同步执行并接受一次阻塞）。
**置信度**: 中高（`deinit` 缺失确定；delegate 强持有属平台行为，建议用 Allocations 实测确认 `CameraFramePipeline` 是否滞留在内存中）。

**复核修正（未修复）**：
- SDK 头文件并未保证 `AVCaptureVideoDataOutput` 强持有 delegate（`sampleBufferDelegate` 是只读属性，没有 retain 语义）；coordinator 在 `stop()` 时总会调用 `stop()`，原稿的 `:218` 应为 `:202`。定级 P2 → **P3**。
- 真正需要修的同类问题是：`stop()` 的清理 block 是 `sessionQueue.async { [weak self] ... }`（`:125-134`），如果 controller 在 block 执行前被释放，`stopRunning()`/`setSampleBufferDelegate(nil)` 会被跳过。修复应强捕获 `session`/`videoOutput`，而不是只加 `deinit`。

---

### LEAK-08 · P3 · `MylifeStore` 轮询任务无 `deinit` 兜底
**位置**: `apps/ios/LivaChatApp/Core/Mylife/MylifeStore.swift:347-371`

```swift
pollTask = Task { @MainActor [weak self] in
    while !Task.isCancelled {
        guard let self else { return }              // 强引用绑定贯穿本轮（含 60s sleep）
        if self.hasPendingAnalysis() {
            try? await Task.sleep(nanoseconds: 60_000_000_000)
```
`AppContainer` 只在 `applyServerSettingsAndReconnect` / `openConversationIfNeeded` 中先 `stopPolling()` 再置 nil。
**影响**: 漏掉 `stopPolling()` 的路径上，`MylifeStore` 会多存活最多 60 s，并继续触发网络刷新。
**建议**: `deinit { pollTask?.cancel() }`；轮询内使用 `weak self` + 每轮重新判断，不要跨 `await` 持有强引用。
**置信度**: 高。

**复核补充**：仅加 `deinit` 并不能解决对象多存活 60s 的问题——任务在 `guard let self` 后跨 60s `sleep` 持有强引用，deinit 根本不会在 sleep 期间发生。应改为“每轮重新取 weak self、sleep 前释放”，或把 sleep 放到不依赖 self 的结构里。

---

### LEAK-09 · P3 · 日志文件无轮转
**位置**: `apps/ios/LivaChatApp/Core/Platform/AppLog.swift:31`、`apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Util/SdkLogger.swift:38`

两处都以 `O_WRONLY | O_CREAT | O_APPEND` 打开 `livachat.log`（App 与 SDK 共用同一文件），单行上限 8 KB，但**永不截断**。
**影响**: 磁盘无界增长（音频路径 20 Hz 级别的 debug 日志在 DEBUG 下尤其多）；`flush()` 之外没有大小控制。
**建议**: 启动时按大小轮转（例如 > 5 MB 重命名为 `.1`，仅保留 1～2 个），并在 `applicationDidEnterBackground` 时
`PURGE`/checkpoint。
**置信度**: 高（代码确定；影响随使用时长线性增长）。

**复核修正**：“音频路径 20 Hz 级别的 debug 日志”不成立——音频类只有个位数日志点（`AudioCaptureController` 5 处、`AudioPlaybackController` 3 处、`MylifeAudioPlayer` 6 处），日志增长主要是事件驱动。无轮转本身成立，P3 不变。

---

## 四、内存越界与内存安全（OOB / UB）

> 说明：Swift 的数组下标越界会 trap（崩溃）而非内存破坏；真正危险的是
> **指针算术、C 互操作、无符号/浮点强转、跨线程读写，以及 Release 下失效的 `precondition`**。
> 下列条目按「是否可能读到分配区之外」与「是否可能崩溃」排序。

### OOB-01 · P1 → P2 · `AVAudioFrameCount(+inf)` 运行时陷阱（采集回调）
**位置**: `apps/ios/LivaChatApp/Core/Audio/AudioCaptureController.swift:789-790`

```swift
let ratio = targetFormat.sampleRate / buffer.format.sampleRate     // 16000 / 0 == +inf
let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
```

`AVAudioFrameCount` 是 `UInt32`，`UInt32(Double)` 对 `+inf` / `NaN` / 负数 / >`UInt32.max` 都**直接 trap**
（`Fatal error: Double value cannot be converted to UInt32 because it is either infinite or NaN`）。

**触发**: 路由切换/`AVAudioEngineConfigurationChange`/蓝牙 HFP 切换的瞬间，tap 回调可能拿到
`sampleRate == 0`（或 `frameLength == 0` 但 `sampleRate == 0`）的 buffer。`start()` 时只在
`performStartAttempt` 校验过一次 `inputFormat.sampleRate > 0`，**回调路径没有任何校验**。
**影响**: 采集线程崩溃（`EXC_BREAKPOINT`），线上表现为「切换蓝牙耳机/插拔有线耳机时偶现闪退」。
**建议**:
```swift
let inputRate = buffer.format.sampleRate
guard inputRate > 0, buffer.frameLength > 0 else { return }
let ratio = targetFormat.sampleRate / inputRate
let frames = min(Double(UInt32.max - 4096), Double(buffer.frameLength) * ratio)
let capacity = AVAudioFrameCount(frames) + 64
```
**置信度**: 高（陷阱语义确定；触发需异常格式，属「条件必现」）。

**状态**: ✅ **已修复（2026-10-25，第一批）**
- `AudioCaptureController.handleTap` 在除法前 guard `inputRate > 0, targetRate > 0, frameLength > 0`，并把 capacity 夹到 `UInt32.max - 64`。
- 新增 `AudioCaptureLevelTests.testZeroSampleRateTapBufferIsIgnored`（构造 0Hz `AVAudioFormat`/buffer，走 `ingestTapBufferForTesting`）。

**复核修正**：
- tap 的 format 是 install 时已校验 `sampleRate > 0` 的 `inputFormat`（`:317`、`:340-343`），没有代码安装 0-rate tap；“切换蓝牙耳机闪退”是推测，仓库中无证据。
- trap 语义确认：0Hz buffer 可构造，`AVAudioFrameCount(Double)` 会触发 `Fatal error: Double value cannot be converted to UInt32...`。
- 定级 P1 → **P2**（保留防御性修复；生产触发待实测）。

---

### OOB-02 · P2 → P3 · 音频帧位置/数量的整数强转
**位置**: `apps/ios/LivaChatApp/Core/Audio/MylifeAudioPlayer.swift:343,355`

```swift
let requested = AVAudioFramePosition((Double(fromMs) / 1000 * format.sampleRate).rounded())
...
frameCount: AVAudioFrameCount(remaining)          // remaining: Int64 → UInt32
```
`format.sampleRate == 0`（损坏/伪造的 m4a）→ `+inf` → `Int64(inf)` trap；`remaining > 4.29e9` 帧
（@44.1 kHz 约 27 小时音频）→ trap。当前 `durationMs` 来自服务端，`clampedMs` 只夹紧上界不校验正值。
**建议**: 校验 `format.sampleRate > 0`、`file.length > 0`；用 `UInt32(clamping:)`；分段调度超过 `UInt32.max` 的剩余帧。
**置信度**: 高（语义确定），触发概率低。

**复核修正（未修复）**：
- `:343` 是**乘法**（`Double(fromMs) / 1000 * sampleRate`），`sampleRate == 0` 的结果是 0，不是 `+inf`，不会在该行 trap。
- 真正的除零陷阱在 **`:280`**（`Double(file.length) / format.sampleRate` → `Int64`）和 **`:373-374`**（`playerTime.sampleRate` → `Int64`）；`:355` 的 `Int64 → UInt32` 溢出仍成立（> `UInt32.max` 帧，约 27 小时 @44.1 kHz）。
- `durationMs` 是本地计算的（`:280`），不是“来自服务端”；`clampedMs` 已经夹紧负值。
- 定级 P2 → **P3**。

---

### OOB-03 · P2 → P3 · Release 下 `precondition` 失效导致数组构造崩溃
**位置**: `apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Pipeline/FrameAssembler.swift:12-15`、
`apps/ios/LivaChatApp/Core/Audio/PCMChunker.swift:9`

```swift
precondition(sampleRate > 0 && frameMs > 0)
frameSamples = sampleRate * frameMs / 1000
precondition(frameSamples > 0)
pending = [Int16](repeating: 0, count: frameSamples * 2)     // ← 负值/溢出直接 trap
```
`LivaChatConfig.validate()` 目前把 `audioSampleRate` 限制为 `.hz16000`、`audioFrameMs == 20`，因此**现状安全**；
但这层保证与 `FrameAssembler` 之间没有类型约束，一旦放开（例如支持 24 kHz / 60 ms 帧，或新增 Android 端共用代码），
`frameSamples * 2` 的 Int 溢出或负 count 会在 `Array(repeating:count:)` 处崩溃。
**建议**: 改为可失败的初始化（`init?`）并把配置校验下沉到 pipeline 构造点；`frameSamples` 用
`Int32`/`UInt16` 并在乘法前 `multipliedReportingOverflow`。
**置信度**: 高（当前不触发）。

**复核修正（未修复）**：
- **前提错误**：项目 Release 使用 `SWIFT_OPTIMIZATION_LEVEL = "-O"`（`LivaChatApp.xcodeproj/project.pbxproj:1433`）；Swift 的 `precondition` 在 `-O` 下**仍然生效**，只有 `-Ounchecked` 才移除。用 `swiftc -O` 实测：`precondition` 触发 SIGTRAP（exit 133），`assert` 被移除。
- 因此当前配置下，负/零 `frameSamples` 的行为是**确定性 trap（崩溃）**，不是构造越界数组。
- 定级 P2 → **P3**（加固项）；建议保留可失败初始化的改造，但按“避免崩溃”而非“避免越界”描述。

---

### OOB-04 · P2 → P3 · C 桥缺少入参上界校验（越界读的潜在入口）
**位置**: `apps/ios/LivaChatSDK/Sources/COpus/COpus.c:27-37`、`Sources/CVad/CVad.c:22-30`

```c
int lc_opus_encode(LcOpusEncoderRef ref, const int16_t *pcm, int frame_samples,
                   unsigned char *out, int max_bytes) {
    if (encoder == NULL || pcm == NULL || out == NULL) return OPUS_BAD_ARG;
    return opus_encode(encoder, pcm, frame_samples, out, max_bytes);   /* frame_samples 未校验 */
}

int lc_vad_process(LcVadRef vad, int sample_rate_hz, const int16_t *audio_frame, size_t frame_length) {
    if (vad == NULL || audio_frame == NULL) return -1;
    return Wvad_Process((WvadInst *)vad, sample_rate_hz, audio_frame, frame_length);  /* 未校验 */
}
```

**当前调用方是正确的**：
- `OpusEncoderImpl.encode` 先 `guard pcm.count >= frameSamples`，然后传 `Int32(frameSamples)`（320）；
- `VadDetectorImpl` 先用 `lc_vad_valid_rate_and_frame_length` 校验，再传 `frameSamples`（320）。

**风险**: `pcm` 的**实际可读长度**与 `frame_samples` 是两套信息，C 层无法自证。任何未来改动
（例如为了支持 60 ms 帧传 `frameSamples * 3` 而忘记同步 `pcm.count` 校验）都会让 `opus_encode`
读到 Swift 数组缓冲区之外（**真实的越界读**，非 trap）。
**建议**: C 层加硬上界
```c
if (frame_samples <= 0 || frame_samples > 5760) return OPUS_BAD_ARG;      /* 120ms@48k */
if (max_bytes <= 0 || max_bytes > 4000) return OPUS_BAD_ARG;
if (frame_length == 0 || frame_length > 5760) return -1;
```
并在 Swift 侧把「缓冲区长度」显式一并传入（`pcm_len`）做交叉校验。
**置信度**: 高（当前安全，属加固项）。

**复核修正（未修复）**：
- VAD 侧并不“完全依赖 Swift 调用方”：`lc_vad_process` 包装的 `Wvad_Process` 自身会校验采样率/帧长，调用方还先调了 `lc_vad_valid_rate_and_frame_length`（`VadDetector.swift:29`）。
- Opus encode shim 的缺上界校验成立：libopus 的 `frame_size_select` 会拒绝非法帧长，但无法发现“`pcm` 缓冲区短于合法帧长”的越界读。
- 同类缺口也存在于 `lc_opus_decode`（`COpus.c:61-71` 的 `len`/`max_samples`）；当前调用方安全。
- 定级 P2 → **P3**（加固）。

---

### OOB-05 · P3 · `AVAudioPCMBuffer` 读取长度未与容量交叉校验
**位置**: `AudioCaptureController.swift:820-823`、`Core/Audio/AudioPlaybackController.swift:339-351`

```swift
guard output.frameLength > 0, let channelData = output.int16ChannelData?[0] else { ... }
let samples = Array(UnsafeBufferPointer(start: channelData, count: Int(output.frameLength)))
```
`UnsafeBufferPointer` 是**不受检查的**视图：一旦 `frameLength > frameCapacity`（理论上 AVAudioConverter 不会，
但 `frameLength` 是 `var`，任何后续对 `output.frameLength` 的赋值都会破坏不变量），这里会越界读。
另外 `int16ChannelData` 在**交错**格式下 `stride == channelCount`；当前 `AudioFormats.capture` 是
16 kHz/1ch/交错（`stride == 1`），因此读 `[0]` 正确 —— 这个前提应写进注释或断言。
**建议**: `let n = min(Int(output.frameLength), Int(output.frameCapacity))`；对 `channelCount != 1` 断言或
按 `stride` 抽取。播放侧同理（`frameCapacity` 已等于 `samples.count`，风险更低）。
**置信度**: 高（当前不触发）。

---

### OOB-06 · P3 · `sqlite3_errmsg` 的 Optional 传参
**位置**: `apps/ios/LivaChatApp/Core/History/HistoryDatabase.swift:424-430`

```swift
private func step(_ stmt: OpaquePointer) throws -> Int32 {
    let result = sqlite3_step(stmt)
    guard result == SQLITE_ROW || result == SQLITE_DONE else {
        throw error(message: String(cString: sqlite3_errmsg(handle)))   // handle 为 Optional
    }
```
`SQLite3` 模块把 `db` 声明为 `OpaquePointer!`，Swift 允许把 `Optional` 传进去（隐式强解包）。
SQLite 自身对 `NULL` 是安全的（返回 `"out of memory"`），**不会崩溃**，但错误信息会完全误导排障
（真正原因被替换成 OOM 文案）。同文件的 `error(message:)` 用 `handle.map` 是正确的，两处风格不一致。
**建议**: `guard let handle else { throw .statement("\(message): database is closed") }`。
**置信度**: 高（SQLite 对 NULL 安全，纯健壮性问题）。

---

## 五、内存无界增长与峰值内存

### GROW-01 · P1 → P2 · JitterBuffer 的「帧数」上限被当成「延迟」上限
**位置**: `apps/ios/LivaChatApp/Core/Audio/JitterBuffer.swift:10,51`

```swift
static let defaultMaxLatencyFrames = 15_000        // 名字暗示 15000 samples ≈ 937 ms @16k
...
guard frames.count < maxLatencyFrames else {       // 实际是「帧数」上限
    stats.droppedOverflow += 1
    return
}
frames[frame.sequenceNumber] = frame
```
`frames` 是 `[UInt64: AudioPCMFrame]`，每个元素是完整的 `[Int16]` PCM。

**容量测算**（`AudioPCMFrame.samples` 长度 = Opus 包解码结果）:

| 服务端帧长 | samples/帧 | 单帧内存 | 15000 帧合计 |
|---|---|---|---|
| 20 ms | 320 | 640 B | **≈ 9.6 MB**（+ 每帧结构体/字典 ≈ 100 B → ≈ 11 MB）|
| 60 ms | 960 | 1.92 KB | ≈ 28.8 MB |
| 120 ms（`OpusDecoderImpl.maxSamples` 允许的最大值）| 1920 | 3.84 KB | **≈ 57.6 MB** |

**触发**: 播放泵停止（`stop()` / `prepareForAudioReconfiguration()` / 后台静音）但 SDK 仍在送 TTS；
`push` 只做 epoch 与容量检查，**从不按延迟/时效丢弃**，所以缓冲区会一路涨到容量上限。
**影响**: 播放恢复后延迟巨大（5 分钟音频排队），且瞬时内存可达 10～60 MB。
**建议**:
1. 语义修正：`maxLatencyFrames` 改为按 **时长** 计算（`maxLatencyMs / frameDurationMs`，默认 **60,000 ms**；复核时发现 1000 ms 会截断快于实时的 TTS 突发，见下）；
2. `pull()` 时按 `sequenceNumber` 落后超过阈值直接丢帧（当前只有 `started` 之后的 late 丢弃）；
3. `frames.keys.min()`（:65）是 O(n) 扫描，50 Hz 拉取下对 15000 个 key 是明显的 CPU 与内存带宽浪费 ——
   改用 `nextExpected` 优先、失败再回退到 `min()`。
**置信度**: 高（数值与逻辑确定）。

**状态**: ✅ **已修复（2026-10-25，第一批）**
- `JitterBuffer` 的上限从 `maxLatencyFrames` 改为 `maxLatencyMs`（默认 **60,000 ms**）；`push` 以**解码后的 samples 数**计算帧时长（向上取整，至少 1 ms），不信任 wire 上的 `durationMs`，超过上限丢帧并计入 `droppedOverflow`。
- 空 `samples` 帧直接丢弃（避免零成本帧绕过上限）；删除“空缓冲时接受超长首帧”的特殊分支，超长帧被丢弃后正常帧仍可继续入队；迟到帧判断移到容量判断之前。
- `pull()` 先查 `nextExpected`，缺失时才回退到 `keys.min()`，避免每次 50 Hz 拉取都做 O(n) 扫描。
- 新增 `currentBufferedDurationMs()` 及 11 个测试（20/60 ms 帧、samples 覆盖错误 durationMs、超长首帧、单帧/连续空帧、非整数时长向上取整、零/亚毫秒时长、默认上限的 10s 突发、上限、pull 恢复）。

**复核修正**：
- `playback.stop()` 会 `jitterBuffer.reset()`（`AudioPlaybackController.swift:116-128`）；后台静音走 `teardownMedia()` → `stop()` → reset。原稿把 `stop()`/后台静音列为触发条件不准确。
- 只有 `prepareForAudioReconfiguration()` 保留缓冲，而它是同步重配窗口（capture retry 最长约 1.4s）。更现实的触发是“恢复/网络积压导致解码快于 20 ms 播放泵”。
- **上限不能设成 1000 ms**：服务端 `_send_audio_chunk`（`services/session/orchestrator.py:1944-1972`）按 TTS provider 的产出速度发送，没有实时节流；正常 10–60 s 的回复会以突发形式到达，1 s 上限会丢掉大部分音频。60 s 上限在 20 ms/120 ms 帧下都约 **1.9 MB**，仍远低于原来的 9.6–57.6 MB，同时能容纳完整回复。
- 若未来 lecture 模式需要 >60 s 的连续 TTS，建议改为**字节上限**（如 8 MB）或服务端节流，而不是继续放大时间上限。
- 定级 P1 → **P2**。

---

### GROW-02 · P1 · 载入历史会话时把该会话所有图片原图读入内存
**位置**: `apps/ios/LivaChatApp/Core/History/HistoryService.swift:380-410`

```swift
private func chatMessages(from records: [HistoryMessageRecord], images: HistoryImageStore) async -> [ChatMessage] {
    for record in records {
        if let filename = record.imagePath,
           let data = try? await images.read(filename: filename) {      // ← 无大小上限、无降采样
            image = AiImage(data: data, ...)                            // ← 原图 JPEG 进内存
        }
        result.append(ChatMessage(..., image: image))
    }
```
调用点：`beginSession()`（App 每次启动/重连）、`loadDetail(sessionID:)`（历史详情页）。

**影响**: 一个包含 30 张图片的会话，JPEG 按 300 KB/张 → 9 MB；用户点「昨天的会话」时，
`HistoryDetail` 也把整段会话的所有图片一次性读入。且 `AiImage.data` 会被 `ChatMessage`（`@Published` 数组）
长期持有 —— **同一份数据在 messages 数组里驻留整个会话生命周期**。
另注：`HistoryImageStore.write` 不做尺寸/大小限制（`data.write(to:options:.atomic)`），
`GalleryPhotoEncoder` 已把发送图压到 1920px，但服务端下发图（`aiText.image`）无约束。
**建议**:
1. `AiImage` 增加「按需加载」变体（存 filename + 缩略图），列表只保留 1 张缩略图；
2. `chatMessages` 限制单文件大小（`< 2 MB`）并对超限文件跳过/降级；
3. 历史详情页分页加载（`messages(sessionID:limit:cursor:)`）。
**置信度**: 高。

**状态**: ✅ **已修复（第一版，2026-10-25）**
- `HistoryImageStore.read(filename:maxBytes:)` 在 `Data(contentsOf:)` 之前检查文件大小，超限直接跳过并记录日志。
- `HistoryService.maxHistoryImageBytes = 2 * 1024 * 1024`；`chatMessages(from:images:)` 使用该上限，图片被跳过但文本消息保留。
- 同时修复了复核发现的图片-only 消息丢失：`recordAssistantMessage` 现在允许“空文本 + 有图片”的消息落库（此前会被提前 return，重启后图片消失）。
- 新增 `HistoryStorageTests.testImageStoreSkipsFilesAboveReadLimit`、`testServiceSkipsOversizedHistoryImages`、`testServicePersistsImageOnlyAssistantMessage`。

**备注**：这是“单文件大小上限”方案，不是按需加载，也**不是总量上限**：N 张图片仍会加载 N × 单文件大小（注释已改为“bounds each image, not the aggregate”）。超大图仍会占磁盘，但不再进入 `messages`；分页/按需加载与总量上限留待后续。
**复核修正**：触发点是会话启动/权限授予/设置应用，不是每次网络重连。

---

### GROW-03 · P1 · 会话图片 Data 常驻 + 全尺寸解码位图缓存无成本上限
**位置**: `apps/ios/LivaChatApp/Core/Conversation/ConversationViewModel.swift:31`、
`apps/ios/LivaChatApp/Features/Conversation/AIImageView.swift:46-70`

```swift
nonisolated(unsafe) private static let cache: NSCache<NSString, Box> = {
    let cache = NSCache<NSString, Box>()
    cache.countLimit = 20                                  // 只限条数
    return cache
}()
...
guard let source = CGImageSourceCreateWithData(image.data as CFData, nil),
      let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil) else { ... }   // 全尺寸解码
```

**影响**:
- 一张 3000×4000 的 JPEG 解码为 RGBA 位图 = **48 MB**；`countLimit = 20` 的理论上限接近 1 GB；
  `NSCache` 只在系统内存告警时被动清理，真机上是典型的 `EXC_RESOURCE`/`Jetsam` 来源；
- 气泡视图（`AIImageView`）完全可以用缩略图解，却解码了原图；
- `ChatMessage.image`（含原始 JPEG Data）在 `messages` 数组中长期驻留（无条数上限，
  `appendMessage` 只做 id 去重）。
**建议**: `CGImageSourceCreateThumbnailAtIndex` + `kCGImageSourceThumbnailMaxPixelSize`
（按气泡最大边长 × `displayScale`，通常 ≤ 900 px），并设置
`cache.totalCostLimit = 64 * 1024 * 1024`、`setObject(_:forKey:cost:)` 传 `bytesPerRow*height`。
全屏查看器（`ImageViewerOverlay`）才解码原图，并限制到 1 页。
**置信度**: 高。

**状态**: ✅ **已修复（第一批，2026-10-25）**
- `AIImageDecoder.decode` 新增 `maxPixelSize`：气泡传 `bubbleMaxPixelSize = 1024`，走 `CGImageSourceCreateThumbnailAtIndex`；全屏查看器保持 `nil`（原图）。
- 缓存拆成两个 `NSCache`：缩略图 `countLimit = 20`、全屏图 `countLimit = 3`，各自 `totalCostLimit = 64 MB` 并传 `cost = bytesPerRow * height`；查看器的大图不再驱逐气泡缩略图。`clearCache()` 同时清空两者。
- `.task` 在解码完成后检查 `Task.isCancelled`，不再写入已退出的气泡状态。
- 新增 `M2CameraGalleryTests.testAIImageDecoderDownsamplesForBubble` 与 `testAIImageDecoderDownsamplesAndRotates`。

**复核修正**：
- 原稿 `ConversationViewModel.swift:31` 是 `galleryPreviewData`，`messages` 数组在 `:9`。
- 旋转路径仍会分配第二张位图，但现在是缩略图尺寸，不再翻倍全尺寸内存。
- 全屏查看器/保存路径仍可能解码原图，见「十一、复核记录」的补充发现。

---

### GROW-04 · P2 · 画廊查看器一次性实例化所有页
**位置**: `apps/ios/LivaChatApp/Features/Timeline/Gallery/GalleryViewerOverlay.swift:44-52,170-200`

```swift
TabView(selection: $pageIndex) {
    ForEach(Array(frames.enumerated()), id: \.element.frameId) { index, frame in
        GalleryRemotePage(frame: frame, onTap: onDismiss).tag(index)   // ← 每页一个 NetworkImage
    }
}
.tabViewStyle(.page(indexDisplayMode: .never))
```
`frames` 由 `FrameGalleryViewModel.loadMore()` **不断追加**（无上限）；`TabView(.page)` 需要相邻页对象，
实践中会实例化全部子视图，每个 `GalleryRemotePage` 内的 `NetworkImage` 都会启动下载 + 解码。

**影响**: 打开查看器时 N 页（N 可达数百）同时命中 `DefaultNetworkImageCache`；
每个 1280×720 位图 ≈ 3.7 MB，`countLimit = 100` 时上限 ≈ 370 MB。
**建议**: 只渲染 `pageIndex-1 … pageIndex+1` 三页（`frames` 切片），或改用 `ScrollView + LazyHStack`
+ `.scrollTargetBehavior(.paging)`（iOS 17）；`loadMore` 增加总条数上限（如 500）。
**置信度**: 中（取决于 SwiftUI `TabView` 页实例化策略，建议用 Instruments 的 VM Tracker 实测）。

**复核修正（未修复）**：`FrameGalleryViewModel.loadMore()` 确实无总上限；`NetworkImage` 的下载在 `.task(id:)`（`NetworkImage.swift:168`）触发，是否全部离屏页都预加载取决于 SwiftUI 分页策略，中等置信度成立，建议实测后再定级。

---

### GROW-05 · P2 · 相机拍立得路径在主线程处理全尺寸原图
**位置**: `apps/ios/LivaChatApp/Core/PlatformAdapters/CameraPhotoPicker.swift:36-42`

```swift
let image = info[.originalImage] as? UIImage
onResult(image?.jpegData(compressionQuality: 0.9))     // @MainActor Coordinator，未降采样
```
**影响**: 12 MP 照片 ≈ 48 MB 位图 + JPEG 编码缓冲，全部在 MainActor 上同步完成；
`onResult` 之后 `Data`（数 MB）还会经 `ConversationViewModel.galleryPreviewData` / `pendingGalleryPreviewData`
（`ConversationViewModel.swift:31,40`）继续驻留，直到预览关闭或发送。
**建议**: 改为 `CGImageSourceCreateThumbnailAtIndex`（`maxPixelSize = 1920`，与
`GalleryPhotoEncoder.maxLongSide` 对齐）在后台队列完成；预览态只保留缩略图 Data。
**置信度**: 高。

**复核修正（未修复）**：该 picker 的数据走**头像裁剪**（`ConversationView.swift:299` → `AvatarCustomizationModel.handleCameraPhoto`），不是 `galleryPreviewData`（后者属于 PHPicker 路径）。同类问题还存在于 `AvatarCropView.croppedData()`（主线程全尺寸 crop + JPEG，`:243-255`），见「十一、复核记录」的补充发现。定级 P2 不变。

---

### GROW-06 · P2 · 流式 AI 文本的 O(n²) 拼接与逐 token 重解析
**位置**: `apps/ios/LivaChatApp/Core/Conversation/ConversationCoordinator.swift:478-494`

```swift
} else {
    currentAIText += text                                    // ← 每 token 复制整串
}
viewModel.setStreamingAI(messageID: messageID, text: currentAIText)   // ← 每 token 发布整个快照
```

下游 `MarkdownMessageView.init` 每次构造都会执行
`LivachatURLConverter.convert` + `MarkdownTableFallback.rewrite` + `hashValue`
（`Features/Conversation/MarkdownMessageView.swift:42-48,82-96`）——注意 `@State` 初值只在首次生效，
但**初始值表达式每次 body 求值都会真正执行**，结果被丢弃。

**影响**: 一条 2000 字的回复按 4 字/token 计 ≈ 500 次全串复制（≈ 500 KB 临时分配）+ 500 次 Markdown 解析；
配合 `NSCache(countLimit: 50)`，会持续挤出旧条目（缓存命中率接近 0），产生大量短命大对象 → 内存抖动、CPU 飙高。
**建议**: 用 `[String]` 累积 + `joined()`（或 `String.reserveCapacity`），发布频率做节流（如 80 ms）；
把 Markdown 解析挪出 `init`（`onAppear`/`task(id:)` 中计算并缓存）。
**置信度**: 高。

**复核修正（未修复）**：
- `currentAIText += text` 的 O(n²) 复制成立（已发布的快照让 String 缓冲区非唯一，每 token 全量复制）。
- 但流式 AI 文本渲染的是**纯 `Text`**（`ConversationView.swift:679`），不是 `MarkdownMessageView`；没有“每 token 重解析 Markdown”，Markdown 只在最终消息追加时解析一次。
- 真实成本是每次 body 求值时 `MarkdownMessageView.init` 里的 convert+rewrite+hash（缓存命中前仍执行）+ Text relayout。
- 定级 P2（CPU/分配抖动），不是内存泄漏。

**状态**: ✅ **部分修复（2026-10-25，build 1.0.0.3）**
- `MessageBubbleView` 改为 `Equatable`（只比较 message 数据、maxBubbleWidth、serverBaseURL、layoutDirection、bodyFontSize，忽略每次 body 重建的闭包），会话列表使用 `.equatable()`。流式 token / 新消息到达时，未变化的已定稿气泡不再重新执行 body，也不再重跑 Markdown 转换。
- `MarkdownMessageView` 增加 prepared 缓存（raw markdown + serverBaseURL → convert/rewrite 结果），同一消息在宽度/环境变化后重算时不再重复转换。
- 自动滚动节流到 150ms（新消息 `force` 立即滚动），避免每个 token 重启 0.2s 滚动动画。
- 仍保留：`currentAIText += text` 的 O(n²) 拼接；如需进一步优化，可改为 `[String]` 累积 + `joined()` 或发布节流。

---

### GROW-07 · P2 → P3 · `PCMChunker` 的 `removeFirst` 造成 O(n²) 搬运
**位置**: `apps/ios/LivaChatApp/Core/Audio/PCMChunker.swift:13-23`

```swift
pending.append(contentsOf: samples)
while pending.count >= frameSamples {
    frames.append(Array(pending.prefix(frameSamples)))
    pending.removeFirst(frameSamples)      // ← 每次整体前移
}
```
每次 tap 回调（1024 帧 @48k → 转换后约 341 samples）都会走 1～2 次 `removeFirst`。
**影响**: 每次搬运 `pending` 剩余元素；虽然 `pending` 很短（≤ 2 帧），但这是热路径（每秒 50 次），
叠加 `Array(prefix:)` 的分配，形成持续的小对象抖动。
**建议**: 改为环形缓冲（固定 `[Int16]` + `writeIndex`/`readIndex`），或用
`pending.removeFirst(n)` 的替代实现：`pending = Array(pending[n...])`（同样 O(n)，但少一次 memmove 语义），
最优是下标游标 + 周期性 `compact()`。
**置信度**: 高。

**复核修正（未修复）**：`pending` 每次调用后 <320 samples（tap 转换后约 341–2112 samples），每次最多 1–2 次很小的 `removeFirst`，不会跨调用增长，O(n²) 没有实际发生。定级 P2 → **P3**。

---

### GROW-08 · P3 · 静态 `NSCache` 只按条数限制
**位置**: `Features/Conversation/MarkdownMessageView.swift:21-25`、`Features/Conversation/AgentJobCollapse.swift:21-25`

`cacheKey` 形如 `"\(cacheKey):\(prepared.hashValue)"`，流式文本每变一次就是新 key（见 GROW-06），
`countLimit = 50` 意味着 50 个中间态 `MarkdownContent`（每个含完整 AST）。
**建议**: `totalCostLimit` + `cost = prepared.utf8.count`；流式阶段（`isFinal == false`）不进缓存。
**置信度**: 中（缓存命中率低是确定的，具体峰值取决于消息长度）。

**复核修正（未修复）**：流式文本不使用该缓存；`MarkdownMessageView` 的 key 是 `message.id + collapsed 状态`，对已定稿消息稳定；`AgentJobCollapse` 也只用于最终 agent job 文本。没有“50 个中间态”的 key churn。保留“无 costLimit”的问题，定级 P3。

---

### GROW-09 · P3 · 图片缓存无成本上限、无降采样
**位置**: `apps/ios/deps/NetworkImage/Sources/NetworkImage/NetworkImageCache.swift:21-31`、
`Core/Mylife/MylifeImageLoader.swift:27`

`DefaultNetworkImageCache(countLimit: 100)`，`setObject` 不传 `cost`；`CGImageSourceCreateImageAtIndex`
未使用 `kCGImageSourceThumbnailMaxPixelSize`（`NetworkImageLoader.swift:79-87`）。
**建议**: 与 GROW-03 合并治理：统一一个「按像素降采样的图片加载器」，缓存以字节为成本。
**置信度**: 高。

---

## 六、并发与生命周期导致的内存不安全

### RACE-01 · P1 → 撤销 · `ConnectionManager.deinit` 在任意线程清理队列私有状态
> **❌ 2026-10-25 复核结论：不成立（原 P1 撤销，降为 P3 风格问题）。**
> `cleanupSession` 对 `session`/`delegate`/`webSocketTask` 的访问已经在 `stateLock` 内（`:345-351`）；`WebSocketDelegate.manager` 是 `weak`（`:506`）；所有队列/回调路径都是 `[weak self]` + `guard let self`，执行期间持强引用，deinit 不可能与之并发；`connectInternal` 与 `cleanupSession` 同在串行 connection 队列。不存在数据竞争，也不会因此泄漏 URLSession。

**位置**: `apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Network/ConnectionManager.swift:68-74`

```swift
deinit {
    reconnectWorkItem?.cancel()
    cleanupSession(cancel: true)        // 触碰 session/delegate/webSocketTask/heartbeatTimer
}
```

`ConnectionManager` 头部注释明确写着 `// Connection-queue-confined state.` ——
`session`、`delegate`、`generation`、`heartbeatTimer`、`watchdogWorkItem` 只能在 `SDKQueue.connection` 上访问。
但 `deinit` 触发的线程是**最后一个强引用释放处**（`LivaChatSDK.deinit` → 可能在 MainActor、也可能在
`SDKQueue.session` 的闭包里）。

**影响**:
1. 与正在执行的 `connectInternal()` / `cleanupSession()` / `startHeartbeat()` 并发读写 `self.session`、`self.delegate`
   → 对 `URLSession`/delegate 的重复 `invalidate`/`cancel`（`-[NSURLSession invalidateAndCancel]` 幂等，但
   `self.session = nil` 与 `let session = self.session` 交错会造成 **URLSession 泄漏** 或对已释放 delegate 的访问）；
2. `deinit` 里 `reconnectWorkItem?.cancel()` 后 `cleanupSession` 又调用 `cancelWatchdog()`/`stopHeartbeat()`，
   与队列上仍在执行的 `heartbeatTick()` 竞争。
3. `LivaChatSDK.deinit` 调用 `connectionManager.disconnect()` 是 `SDKQueue.connection.async { [weak self] ... }`；
   由于 `ConnectionManager` 在 `LivaChatSDK` 之后立刻释放，该 block 内的 `weak self` 很可能已经是 `nil`，
   实际清理完全依赖 `deinit` 这条「非队列」路径 —— 设计意图与实现相反。

**建议**:
```swift
deinit {
    // 只做线程安全的收尾
    let session: URLSession?
    stateLock.lock(); session = self.session; self.session = nil; stateLock.unlock()
    session?.invalidateAndCancel()
    reconnectWorkItem?.cancel()
    watchdogWorkItem?.cancel()
    heartbeatTimer?.cancel()
}
```
即：把 `session`/`delegate`/timer 的读取放入 `stateLock`（现在 `connectInternal` 里这两个赋值没有加锁），
或用一个 `nonisolated` 的 `tearDown()` 只操作受锁保护的字段，把队列专属逻辑（`generation` 判定）留在队列上。
**置信度**: 高（约束违反确定；后果取决于竞态窗口，建议 TSan 复现）。

---

### RACE-02 · P2 → 撤销 · `CameraController.videoOutput` / `AVCaptureConnection` 跨线程访问
> **❌ 2026-10-25 复核结论：不成立。**
> `videoOutput` 的所有访问都在 `sessionQueue`：`configure`（`:108-110` 派发）、`stop`（`:125-134`）、orientation 闭包（`beginOrientationUpdate` 的 `:449-452` 派发 `apply()`）。`handleCoordinatorAngle`/`deviceOrientationChanged` 在主线程只是构造闭包，真正读 `videoOutput`/改 connection 的代码在 sessionQueue 上执行。原稿建议的“收敛到 sessionQueue”已经实现，本条为误报。

**位置**: `apps/ios/LivaChatApp/Core/Video/CameraController.swift:202,260-278` vs `:384,416`

- 写：`configure(front:pipeline:)`（sessionQueue）里 `videoOutput = output`、`connection.videoRotationAngle = ...`
- 读/写：`handleCoordinatorAngle` / `deviceOrientationChanged` 的分支在 **Main** 上
  `self.videoOutput?.connection(with: .video)` 并直接改 `connection.videoOrientation` / `videoRotationAngle`

**影响**: `AVCaptureConnection` 的旋转属性被两个线程同时改写（AVFoundation 不保证线程安全）→ 可能出现
「角度错乱」甚至 `EXC_BAD_ACCESS`（在 `configure` 里 `session.removeInput` / `commitConfiguration`
导致 `connection` 对象被重建时）。`videoOutput` 字段本身在不同线程读写，Swift 无内存序保证。
**建议**: 所有 `videoOutput`/`connection` 的访问收敛到 `sessionQueue`
（现有 `beginOrientationUpdate` 已经把 `apply()` 派发到 sessionQueue，只需把 `let connection = ...`
也移进那个闭包）。
**置信度**: 中高。

---

### RACE-03 · P3 → 撤销 · 日志写入器 `deinit` 与写队列
> **❌ 2026-10-25 复核结论：不成立。**
> 队列 block 内的 `guard let self` 持强引用，执行期间 deinit 不会发生；deinit 之后才排到的 block 看到 nil 直接返回；`SdkLogger` 的 `close` 与写入在同一 FIFO 队列，排在所有已排队写入之后。不存在 close/write 竞争，最多是“deinit 时尚未执行的日志行被丢弃”的预期行为。

**位置**: `SdkLogger.swift:42-52`、`AppLog.swift:34-38`

`SdkLogger.deinit` 把 `close(fd)` 派发到 log 队列（正确），但 `fileDescriptor` 的**读取没有同步**；
`LogFileWriter.deinit` 则在调用线程直接 `close(fileDescriptor)`，可能与队列中正在执行的
`write(self.fileDescriptor, ...)` 并发 → 写到已关闭的 fd（返回 EBADF，或 fd 号被复用后**写错文件**）。
当前 `AppLog.writer` 是 `static let`，实际不会 deinit，但模式危险。
**建议**: 统一用「队列内关闭 + 队列内所有 fd 访问」的模式（SdkLogger 的做法），`LogFileWriter` 对齐；
`fileDescriptor` 只在队列内读写。
**置信度**: 中（竞态窗口真实存在，但 `AppLog.writer` 永不 deinit，当前不触发）。

---

### RACE-04 · P3 · 取消任务（`AgentJobCancelClient`）在 runtime 销毁后回调
`AgentJobStatusModel.confirmCancel` 的 `Task` 捕获 `canceler`（`AgentJobCancelClient` 持有一条 `URLSession`）
并在 `await` 后 `self?.completeCancel(...)`。`AppContainer.applyServerSettingsAndReconnect()` 会
`agentJobStatusModel.setCanceler(nil)`，但已发出的请求仍持有旧 canceler 直到 60 s 超时。
影响有限（`weak self` 已避免回写），仅提示：`canceler` 建议改成 `weak`/`Sendable` 的可取消句柄。
**置信度**: 高（影响可忽略）。

**复核修正**：机制成立，影响有界（≤60s），`weak self` 已兜住；`AgentJobStatusModel` 是 App 级，不是 runtime 级。P3 不变。

---

## 七、已验证确认「无风险」的点（避免误报）

> **2026-10-25 复核补充**：本节结论大多成立，但有几处行号/措辞需要修正：
> - `AudioSinkBox` 实际在 `LivaChatSDK.swift:445-447`（不是 `:436-438`）。
> - `sqlite3_bind_text` 的 `transient` 在 `:19`、绑定在 `:435`；`sqlite3_column_text` 的读取在 `:451-453`（原稿 `:452`、`:457-460` 是其他 helper）。
> - `UplinkAGC` 只 clamp 了 `len`，`offset` **没有** clamp：负 `offset` 会让 `offset..<(offset+count)` 索引到数组外。当前唯一调用方使用默认 `offset: 0`，所以无 live risk，但“offset/len 已 clamp”的表述不准确。
> - `HistoryService` 的 `tail` 链会暂存 `PendingMessage`（包含 `AiImage` Data），量级受磁盘写入速度约束，但不是严格有界。
> - “`AVCaptureVideoDataOutput` 强持有 delegate”在 SDK 头文件中没有依据（只读属性、无 retain 语义），建议删除或实测确认。

| 项 | 位置 | 结论 |
|---|---|---|
| C 内存所有权（Opus / VAD 句柄） | `OpusProcessor.swift:51-55,98-102`、`VadDetector.swift:39-43` | `lc_*_create` 的 +1 引用由 ARC 在 `deinit` 正确释放；`lc_*_destroy` 均判空，无 double free |
| `AVCaptureVideoDataOutput` 强持有 delegate | `CameraController.swift:202` | 属平台行为（建议实测确认），本条按「强持有」保守评估；不影响 LEAK-07 的修复动作（`deinit` 里置空 delegate 即可） |
| `JpegEncoderImpl` 的 `mutable as Data` | `Internal/Processors/JpegEncoder.swift:13-31` | `CFDataCreateMutable` 的所有权经 `as` 转移到 Swift 侧，无泄漏（`Data` 值语义会拷贝一次，属性能问题） |
| `sqlite3_bind_text` + `unsafeBitCast(-1, …)` | `HistoryDatabase.swift:19,452` | `SQLITE_TRANSIENT` 由 SQLite 立即复制，`withCString` 的指针生命周期足够，**不是**悬垂指针 |
| `String(cString: sqlite3_column_text(...))` | `HistoryDatabase.swift:457-460` | SQLite 保证 NUL 结尾；`String(cString:)` 会修复非法 UTF-8 序列，无越界 |
| `int16ChannelData` + 交错格式 | `AudioFormats.capture`（16 kHz/1ch/interleaved） | 头文件明确说明交错格式下 `channelData[i]` 有效、`stride == channelCount`；单声道 `stride == 1`，读 `frameLength` 个样本正确 |
| `AVAudioPCMBuffer` 写入 | `AudioPlaybackController.swift:334-351` | `frameCapacity == samples.count`、`frameLength == samples.count`、`floatChannelData[0]` 写入范围不越界 |
| `UplinkAGC` / `MicLevelMeter` 下标 | `UplinkAGC.swift:32-69`、`MicLevelMeter.swift:16-38` | `offset`/`len` 已 clamp；`Int16` 平方在 64-bit 上不溢出；`Int16(clamping:)` 避免溢出陷阱 |
| `FrameAssembler` 下标 | `FrameAssembler.swift:19-42` | `copyCount = min(frameSamples - pendingLen, remaining)`，`pendingLen` 复位于回调前，索引恒在界内 |
| `CameraFramePolicy.scaledSize` | `CameraProtocols.swift:114-128` | `longSide > 0` 已校验，尺寸 `max(1, …)` 兜底 |
| `WebSocketDelegate` 循环引用 | `ConnectionManager.swift:479-560` | `manager` 为 `weak`，session→delegate→manager 环已断开；`dismantle` 清空 KVO（`WebPageView`） |
| `NetworkImage` 的 KVO / `WKWebView` 代理 | `Features/WebView/WebPageView.swift:186-226` | `dismantleUIView` 中 `invalidate()` 观察 + 置空代理 + `stopLoading`，无泄漏 |
| `AudioSinkBox` | `LivaChatSDK.swift:436-438` | 对 SDK 持 `weak`，避免 `SDK → pipeline → sink → SDK` 环；`audioSink` 也是 `weak` |
| `NotificationCenter` selector 观察者 | `MylifeAudioPlayer.swift:51`、`AudioSessionController.swift:344-372` | iOS 9+ selector 型注册对 observer 是弱引用并自动注销，**不构成泄漏**（`AudioSessionController` 还显式 remove） |
| `HistoryService` 的 `tail` Task 链 | `HistoryService.swift:172-181` | 每个 Task 只捕获前一个 Task，完成后即释放；pending 期间内存与队列长度成正比（合理），且 `[weak self]` 兜住 continuation |
| `DispatchSourceTimer` 释放 | `AudioPlaybackController.swift:272-288` | timer 创建即 `resume()`，释放已 resume 的 source 是合法的（不会触发 libdispatch 的 suspended-release 崩溃） |
| `AsyncStream` 的调用顺序 | `LivaChatSDK.swift:68-72` | `AsyncStream.init` 的 build 闭包同步执行，`continuation` 使用前已赋值，无隐式解包崩溃风险 |

---

## 八、整改优先级路线图

### 第一批（2026-10-25 已完成）
1. **LEAK-01** ✅ `defer { ongoingTasks.removeValue(forKey: url) }`；失败后允许重试。
2. **LEAK-02** ✅ `DefaultNetworkImageLoader.shutdown()` + `deinit` invalidate；`AppContainer.discardMylifeImageLoader()`。
3. **GROW-01** ✅ `JitterBuffer` 上限改为 `maxLatencyMs`（默认 **60,000 ms**，容纳服务端快于实时的 TTS 突发），按帧时长丢帧；`pull()` 优先 `nextExpected`。
4. **GROW-03** ✅ 气泡图 `CGImageSourceCreateThumbnailAtIndex`（1024 px）+ `totalCostLimit = 64 MB` + cost。
5. **OOB-01** ✅ 采集回调的采样率/帧长防御 + capacity 上界；新增 0Hz buffer 回归测试。
6. **GROW-02** ✅ 历史图片 2 MB 读取上限；超限跳过但保留文本。
7. **LEAK-05** → 复核后降为 P2，未在第一批处理（触发条件不成立；慢消费者风险保留）。
8. **RACE-01** → 复核后撤销（误报）。
9. **LEAK-03** → 复核后降为 P2，未在第一批处理（正常 `onDisappear` 会清理）。

### 第二批（修订，P2）
- 图片/历史：GROW-04（画廊查看器三页窗口化）、GROW-05 + `AvatarCropView`（后台降采样）、GROW-06（流式文本 + Markdown init 成本）
- 历史加载：GROW-02 的按需加载/分页，以及 `HistoryDetailView` 的取消（见「十一、复核记录」补充发现）
- 相机：LEAK-07 的 `stop()` 强捕获 teardown（见补充发现）
- 音频：LEAK-03、LEAK-04（deinit 兜底）、LEAK-05（按事件类型的有界缓冲）
- 实测后再决定是否处理 OOB-02 的真实除零点（`:280`/`:373-374`）

### 第三批（P3）
- LEAK-06、LEAK-08、LEAK-09、GROW-07、GROW-08、GROW-09、OOB-02/03/04/05/06、RACE-04
- RACE-01/02/03：不成立，无需处理（RACE-01 可作为注释一致性清理）

---

## 九、验证方法（建议在整改前后各跑一次）

1. **泄漏**（LEAK-02/03/05/07）
   ```bash
   # Xcode Scheme → Run → Diagnostics：勾选 Malloc Scribble + Malloc Stack Logging
   # 或用命令行
   xcrun xctrace record --template 'Leaks' --launch <bundle-id> --time-limit 120s
   ```
   关注 `URLSession`、`AVAudioPlayerNode`、`AsyncStream`、`CameraFramePipeline` 的实例增量。
2. **越界/UB**（OOB-01/03/04/05）
   Scheme → Diagnostics → **Address Sanitizer**，配合 `-O1` Release 构建；ASan 抓不到 trap（OOB-01 是 Swift
   运行时陷阱），需要专门的单测：构造 `sampleRate == 0` 的 `AVAudioPCMBuffer` 走 `ingestTapBufferForTesting`。
3. **数据竞争**（RACE-01/02/03）
   Scheme → Diagnostics → **Thread Sanitizer**，用例：连接建立中立刻 `stopRuntime()` + 重连；相机开启中快速旋转设备。
4. **峰值内存**（GROW-01/02/03/04）
   Instruments → **Allocations + VM Tracker**，用例：
   - 静音后台 3 分钟（验证 GROW-01 的 jitter buffer 增长曲线）；
   - 打开含 30 张图片的历史会话（GROW-02/03）；
   - 打开 200 帧的画廊查看器（GROW-04）。
5. **回归单测**
   - `JitterBufferTests`（`apps/ios/LivaChatApp/LivaChatAppTests/`）：新增 `testLatencyBoundUsesFrameDuration`、`testLatencyBoundScalesWithLongerFrames`、`testSampleCountOverridesUnderreportedServerDuration`、`testOversizedFirstFrameDoesNotBlockFollowingFrames`、`testEmptyFramesAreDropped`、`testContinuousEmptyFramesDoNotGrowTheBuffer`、`testNonIntegerFrameDurationRoundsUp`、`testZeroDurationFallsBackToSampleCount`、`testSubMillisecondFramesStillConsumeLatencyBudget`、`testDefaultLatencyAllowsFullTTSBurst`、`testPullRestoresBufferedDuration`，断言按时间上限丢帧、samples 覆盖错误时长、超长/连续空帧不破坏后续播放、非整数时长向上取整，且正常 TTS 突发不被截断。
   - `DefaultNetworkImageLoaderTests`（`apps/ios/deps/NetworkImage/Tests/`）：新增 `testTransportFailureIsRetried`（失败后重试）、`testShutdownRejectsNewLoads`。
   - `HistoryStorageTests`：新增 `testImageStoreSkipsFilesAboveReadLimit`、`testServiceSkipsOversizedHistoryImages`、`testServicePersistsImageOnlyAssistantMessage`。
   - `AudioCaptureLevelTests`：新增 `testZeroSampleRateTapBufferIsIgnored`（0Hz buffer 不触发 trap）。
   - `M2CameraGalleryTests`：新增 `testAIImageDecoderDownsamplesForBubble`（2000×1000 → 512×256）与 `testAIImageDecoderDownsamplesAndRotates`（缩略图 + 90° → 256×512）。

---

## 十、附：按文件索引

| 文件 | 相关条目 |
|---|---|
| `apps/ios/deps/NetworkImage/Sources/NetworkImage/NetworkImageLoader.swift` | LEAK-01, LEAK-02, GROW-09 |
| `apps/ios/deps/NetworkImage/Sources/NetworkImage/NetworkImageCache.swift` | GROW-09 |
| `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatSDK.swift` | LEAK-05、七（无风险） |
| `apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Network/ConnectionManager.swift` | RACE-01、七（无风险） |
| `apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Processors/OpusProcessor.swift` | OOB-04、七（无风险） |
| `apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Processors/VadDetector.swift` | OOB-04、七（无风险） |
| `apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Pipeline/FrameAssembler.swift` | OOB-03、七（无风险） |
| `apps/ios/LivaChatSDK/Sources/COpus/COpus.c` | OOB-04 |
| `apps/ios/LivaChatSDK/Sources/CVad/CVad.c` | OOB-04 |
| `apps/ios/LivaChatSDK/Sources/LivaChatSDK/Internal/Util/SdkLogger.swift` | LEAK-09, RACE-03 |
| `apps/ios/LivaChatApp/Core/Audio/AudioCaptureController.swift` | LEAK-04, OOB-01, OOB-05 |
| `apps/ios/LivaChatApp/Core/Audio/AudioPlaybackController.swift` | OOB-05、七（无风险） |
| `apps/ios/LivaChatApp/Core/Audio/JitterBuffer.swift` | GROW-01 |
| `apps/ios/LivaChatApp/Core/Audio/PCMChunker.swift` | GROW-07, OOB-03 |
| `apps/ios/LivaChatApp/Core/Audio/MylifeAudioPlayer.swift` | LEAK-03, OOB-02 |
| `apps/ios/LivaChatApp/Core/Video/CameraController.swift` | LEAK-07, RACE-02 |
| `apps/ios/LivaChatApp/Core/History/HistoryDatabase.swift` | LEAK-06、OOB-06、七（无风险） |
| `apps/ios/LivaChatApp/Core/History/HistoryService.swift` | GROW-02、七（无风险） |
| `apps/ios/LivaChatApp/Core/Mylife/MylifeImageLoader.swift` | LEAK-02, GROW-09 |
| `apps/ios/LivaChatApp/Core/Mylife/MylifeStore.swift` | LEAK-08 |
| `apps/ios/LivaChatApp/Core/Conversation/ConversationCoordinator.swift` | GROW-06 |
| `apps/ios/LivaChatApp/Core/Conversation/ConversationViewModel.swift` | GROW-03, GROW-05 |
| `apps/ios/LivaChatApp/Core/Platform/AppLog.swift` | LEAK-09, RACE-03 |
| `apps/ios/LivaChatApp/Features/Conversation/AIImageView.swift` | GROW-03 |
| `apps/ios/LivaChatApp/Features/Conversation/ImageViewerOverlay.swift` | GROW-03 |
| `apps/ios/LivaChatApp/Features/Conversation/MarkdownMessageView.swift` | GROW-08 |
| `apps/ios/LivaChatApp/Features/Timeline/Gallery/GalleryViewerOverlay.swift` | GROW-04 |
| `apps/ios/LivaChatApp/Core/PlatformAdapters/CameraPhotoPicker.swift` | GROW-05 |
| `apps/ios/LivaChatApp/Features/WebView/WebPageView.swift` | 七（无风险） |

---

## 十一、复核记录（2026-10-25）

### 11.1 方法与结论

- **基线**：`83dce6d`；**构建配置**：Swift 6.0、`SWIFT_STRICT_CONCURRENCY=complete`、Release `-O`（`LivaChatApp.xcodeproj/project.pbxproj:1433`）。
- **方式**：逐条读当前代码 + 队列/线程与生命周期追踪 + 两个最小实验：
  - `swiftc -O` 编译含 `precondition`/`assert` 的程序：`precondition` 触发 SIGTRAP（exit 133），`assert` 被移除；
  - 构造 0Hz `AVAudioFormat`/`AVAudioPCMBuffer`，确认 `AVAudioFrameCount(Double)` 在 inf/NaN 时触发 runtime trap。
- **结果**：**17 条成立、8 条需实质性修正、3 条不成立**；P1 从 9 条收敛为 2 条无争议项（GROW-02、GROW-03）。

### 11.2 不成立（3 条）

| ID | 复核结论 |
|---|---|
| RACE-01 | `cleanupSession` 已在 `stateLock` 内访问 session/delegate/webSocketTask；delegate 的 manager 是 weak；队列块均持强引用，deinit 不可能并发。无数据竞争。 |
| RACE-02 | `videoOutput`/connection 的所有访问都在 `sessionQueue`（configure、stop、`beginOrientationUpdate` 派发的 `apply()`）；不存在 Main 线程改写。原建议已实现。 |
| RACE-03 | 队列 block 内 `guard let self` 持强引用；deinit 后的 block 看到 nil 直接返回；SdkLogger 的 close 与写入同队列 FIFO。无 close/write 竞争。 |

### 11.3 需实质性修正（8 条）

| ID | 修正要点 |
|---|---|
| LEAK-01 | `removeValue` 在 HTTP/解码检查之前执行，只有传输错误会跳过；泄漏量小，主要影响是失败 URL 不重试。P1 → P2；已修复。 |
| LEAK-05 | `runtime.stop()` 会 `sdk.disconnect()`；触发条件不成立。无界缓冲在慢消费者下仍成立。P1 → P2。 |
| LEAK-06 | 生产代码无 `close()` 调用（测试有）；所有语句均 finalize，`SQLITE_BUSY` 不可达。P2 → P3。 |
| OOB-02 | `:343` 是乘法（0 → 0，不是 inf）；真实除零点在 `:280`/`:373-374`。P2 → P3。 |
| OOB-03 | Release `-O` 仍保留 `precondition`；当前配置下是确定性 trap，不是越界数组。P2 → P3。 |
| OOB-04 | VAD 库自身校验帧长；Opus encode shim 缺口成立，另漏了 `lc_opus_decode`。P2 → P3。 |
| GROW-06 | 流式文本是纯 `Text`，没有逐 token Markdown 解析；O(n²) 复制与 body 求值成本仍成立。P2（CPU）。 |
| GROW-08 | 流式文本不走该缓存，key 对定稿消息稳定；没有中间态 churn。P3。 |

### 11.4 第一批实现与验证

| ID | 改动 | 回归测试 |
|---|---|---|
| LEAK-01 | `apps/ios/deps/NetworkImage/Sources/NetworkImage/NetworkImageLoader.swift`：Task 内 `defer` 清理 `ongoingTasks`；`shutdown()` 取消在途任务 | `DefaultNetworkImageLoaderTests.testTransportFailureIsRetried` |
| LEAK-02 | 同上：持有 `session`，`shutdown()`/`deinit` 调用 `invalidateAndCancel()`；`AppContainer.discardMylifeImageLoader()` | `DefaultNetworkImageLoaderTests.testShutdownRejectsNewLoads` |
| GROW-01 | `JitterBuffer.swift`：`maxLatencyMs`（默认 60,000 ms）+ 按 samples 时长限流；空帧/超长首帧防护；`pull()` 优先 `nextExpected` | `JitterBufferTests` 新增 11 条 |
| GROW-02 | `HistoryImageStore.read(filename:maxBytes:)` + `HistoryService.maxHistoryImageBytes = 2 MB` + 图片-only 消息落库 | `HistoryStorageTests` 新增 3 条 |
| GROW-03 | `AIImageView.swift`：气泡 1024 px 缩略图 + 缩略图/全屏分离缓存（各 64 MB）+ cost + 解码后取消检查 | `M2CameraGalleryTests` 新增 2 条 |
| OOB-01 | `AudioCaptureController.handleTap`：`inputRate/targetRate/frameLength` guard + capacity 上界 | `AudioCaptureLevelTests.testZeroSampleRateTapBufferIsIgnored` |

**测试结果**：
- `cd apps/ios/deps/NetworkImage && swift test`：**8/8 通过**。
- `xcodebuild test ... -only-testing:LivaChatAppTests/JitterBufferTests -only-testing:LivaChatAppTests/AudioCaptureLevelTests -only-testing:LivaChatAppTests/HistoryStorageTests -only-testing:LivaChatAppTests/M2CameraGalleryTests`：**通过**。
- 全量 `LivaChatAppTests`：除 4 个依赖模拟器语言/数字格式的既有测试外全部通过。失败项为 `AgentJobFormattingTests`、`AudioGalleryMathTests`、`L10nFormattingTests`、`MylifeAggregatorTests`——模拟器当前为阿拉伯语/阿拉伯数字环境，断言的英文/拉丁数字不匹配；与本次改动无关，切回英文模拟器应恢复。

### 11.5 补充发现（已确认，未纳入第一批）

以下问题在复核中确认，但不在第一批范围，建议在第二批处理：

1. **AI 图片旋转/查看器峰值**：`AIImageView.swift` 的旋转路径仍会分配第二张位图（现在已是缩略图尺寸）；`ImageViewerOverlay` 可能同时保留 3 张全尺寸 CGImage（`:12`、`:271-292`），与 20 条缓存叠加。
2. **入站 base64 图片无大小上限**：`ProtocolJSON.swift:37` 使用 `.base64` 解码，`ProtocolModels.swift:396-398` 在进入 App 策略层前完整分配 JPEG Data；建议在 SDK 解码前加最大字节数限制。
3. **相机 teardown 可能丢失**：`CameraController.stop()` 用 `sessionQueue.async { [weak self] ... }`（`:125-134`），controller 在 block 前释放时 `stopRunning()`/`setSampleBufferDelegate(nil)` 会被跳过。应强捕获 `session`/`videoOutput`。
4. **图片下载不响应调用方取消**：`DefaultNetworkImageLoader` 的 Task 只检查自身取消（`shutdown()` 会 cancel），不传播调用方 `.task(id:)` 的取消；View 取消后下载/解码仍会完成并写缓存。
5. **头像/画廊保存路径**：`AvatarCropView.croppedData()`（`:243-255`）在主线程对全尺寸图 crop + `jpegData`；`FrameGalleryViewModel.saveFrame`（`:136-137`）为校验完整解码原图；`HotSlotViewModel.saveViewerFrame` 同类。
6. **历史详情加载不响应取消**：`HistoryDetailView` 的 `.task` 取消后，`loadDetail`/`chatMessages` 仍会读完整个会话的图片；快速开关多个大会话可能叠加峰值。
7. **历史存储无保留策略**：`HistoryDatabase.append` 使用 `INSERT OR REPLACE` 并无条件 `messageCount + 1`，重复投递同一 id 会虚增计数；若替换后的记录没有图片，旧 `imagePath` 不再被引用，`deleteSession`/`deleteAll` 也不会清理。建议加 retention/quota 与幂等计数。
8. **SettingsViewModel 生命周期**：关闭设置后 `AppContainer.settingsViewModel` 仍强持有（含明文 secret 与 probe Task），直到下次打开设置；建议在 dismiss 时取消 probe 并置 nil。

### 11.6 结论

第一批的 6 个修复已落地并通过定向回归。原稿的**覆盖面**和**证据密度**仍然值得肯定，但 RACE-01/02/03 是明确的误报，LEAK-01/05/06、OOB-02/03/04、GROW-06/08 的技术前提需要按上述修正。建议把本文件作为持续维护的基线：新增发现先标“静态/已复现/已实测”，再进入排期。
