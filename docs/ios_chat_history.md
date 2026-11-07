# LivaChat iOS Chat History 分析与设计

**状态**：已实现并完成模拟器/真机验证（2026-10-21）
**日期**：2026-10-21
**目标**：在 iOS 上实现与 Android 一致的本地对话历史：会话列表、搜索、详情、删除/清空、图片与 Agent Job 结果查看。
**以 codebase 为准**：
- Android：`apps/android/LivaChatApp/.../data/local`、`data/repository`、`domain/usecase`、`ui/screens/history`、`ui/components/MessageBubble.kt`、`ConversationCoordinator.kt`、`ConversationListenerAdapter.kt`
- 架构说明：`docs/android_app_arch.md` §3.1/§3.2/§4.11/§7.2
- iOS：`apps/ios/LivaChatApp/...`、`docs/ios_dev_rules.md`

---

## 1. 范围

### 1.1 本期做

- 会话列表（按开始时间倒序）、LIKE 搜索（300ms 防抖）、空态、加载骨架、左滑/图标删除、清空全部及确认弹窗；
- 会话详情（只读）：复用会话页消息气泡（Markdown、Agent 头行、Job 结果折叠/全屏、AI 图片查看器/保存、链接）；
- 本地持久化：会话、消息、当日会话映射、AI 图片文件；进程重启后保留；
- 与实时会话联动：开始时创建/复用当日会话并 seed 已落库消息，结束时写结束时间与本地摘要；删除当前会话/清空时切换新会话并清空会话页（对齐 Android），避免活会话写回已删行；
- 单测、UI 测试、iPad 真机验证。

### 1.2 不做

- 服务端历史同步/云备份（Android 也没有；`HistoryUseCase` 只读 Room）；
- 历史会话“继续对话”/恢复为实时会话（Android 详情只读）；
- 分页/全文索引（Android 全量 Flow + LIKE）；
- MyLife/时光机时间线（iOS 菜单中的“时光机”仍为占位，不在本文范围）；
- Android 的 translate/transcribe/lecture/omni 模式（iOS 当前只有 chat；`mode` 字段保留但写 null）；
- 音频历史（Android 聊天历史不存音频，MyLife AudioGallery 是独立数据源）。

---

## 2. Android 基线（事实）

### 2.1 入口与导航

- 会话页右上角更多菜单：`时光机` → MyLife；`对话`（`conv_menu_chats`）→ `HistoryScreen`；`设置`。
- `NavGraph`：`HistoryScreen` 与 `HistoryDetailScreen` 是两个独立路由，详情参数 `sessionId`，返回各自 `popBackStack`。
- 打开历史页**不会停止**实时会话；删除当前活动会话时通过 `ConversationCoordinator.replaceWithNewSession()` 先换会话再删。

### 2.2 数据模型（Room，`livachat.db`，version 9）

```kotlin
@Entity("sessions")
SessionEntity(
  id: String PK, startTime: Instant, endTime: Instant?,
  messageCount: Int, summary: String?, firstUserMessage: String?
)

@Entity("messages", foreignKeys = [sessionId → sessions.id ON DELETE CASCADE],
        indices = [sessionId])
MessageEntity(
  id: String PK, sessionId: String, role: String, text: String,
  isFinal: Boolean, seq: Int, timestamp: Instant,
  imagePath: String?, imageMime: String?, imageWidth: Int?, imageHeight: Int?,
  imageOrientation: Int?, imageCameraFacing: String?,
  source: String?, mode: String?
)

@Entity("daily_sessions")
DailySessionEntity(date: String PK, sessionId: String)
```

- Instant 以 epoch millis 存整型；`role` 为 `USER`/`AI`；`isFinal` 0/1。
- 图片：JPEG 文件写在 `filesDir/chat_images/<messageId>.jpg`，DB 存绝对路径 + mime/宽高/方向/摄像头方向；文件不存在时读取为 `image=null`。
- `messages` 外键级联删除；`daily_sessions` 无外键，删除会话/清空时手动清。
- Android `android:allowBackup="false"`。
- 迁移：5→6 `firstUserMessage`；6→7 `daily_sessions` + 按本地日期回填；7→8 `source`；8→9 `mode`；`fallbackToDestructiveMigration()`。

### 2.3 会话生命周期

`SessionUseCase`：

- `createOrGetTodaySession()`：按本地日期（`LocalDate.now(systemDefault)`）查 `daily_sessions`；有映射且 session 存在 → 复用；否则新建 UUID、写映射、`startTime=now`、`messageCount=0`。
- `forceNewTodaySession()`：删除今日映射后新建会话（旧会话保留在历史）。
- `getNextSeq(sessionId)`：`MAX(seq)+1`。
- `endSession(sessionId)`：重新读消息，写 `endTime=now`、`messageCount=messages.size`、`firstUserMessage=首个 USER 消息`、`summary=前 3 条 USER 消息用空格连接，超过 100 字符截断加 "..."`（无 USER 消息则 null）。

`ConversationCoordinator`：

- `start()`：`createOrGetTodaySession()` → `setSessionId(localSessionId, startSeq)` → `sdk.connect()` → 采集/播放/中断启动。
- `stop()`：停媒体/断 SDK → `endSession(sessionId)` → `clearSession()`。
- `replaceWithNewSession()`：`forceNewTodaySession()` → 切换 adapter 的 session → 返回旧 sessionId（调用方删除）。
- 服务端 session id 与本地 UUID 分离；重新建连沿用服务端 session 恢复，不影响本地会话。

### 2.4 写入规则（`ConversationListenerAdapter`）

- 只写 **final** 消息：
  - 用户：`onTranscription(isFinal=true)` 且文本非空 → `ChatMessage(id="user-<transcriptionId>", seq=messageSeq++, timestamp=now)`。
  - AI：`onAiText(isFinal=true)` 且文本非空 → `id="ai-<messageId>"`，携带 image/source，`seq=messageSeq++`。
  - 空文本 AI 消息即使有 image 也**不落库**（代码事实；实际不存在纯图片 AI text，见 §8 已拍板）。
- 非 final 的流式文本只进 UI 缓存，不落库；断连后迟到的非 final 帧丢弃，final 帧仍可落库。
- `appendMessage` 同事务外：`messages` REPLACE + `sessions.messageCount += 1`；用户消息额外 `setFirstUserMessageIfNull(text)`。
- `messageCache` 仅用于流式累积与 UI，不是持久层。
- Agent job 结果通过普通 `aiText` final 落库（`source="agent"`，id 前缀 `ai-msg_agent_job_`）。

### 2.5 搜索/删除/清空（`HistoryRepository` / `HistoryViewModel`）

- 列表：`SELECT * FROM sessions ORDER BY startTime DESC`。
- 搜索 SQL（未 trim、未转义 `%`/`_`）：
  ```sql
  SELECT * FROM sessions
  WHERE :query = ''
     OR id IN (SELECT sessionId FROM messages WHERE text LIKE '%' || :query || '%')
     OR summary LIKE '%' || :query || '%'
     OR firstUserMessage LIKE '%' || :query || '%'
  ORDER BY startTime DESC
  ```
- 搜索输入 300ms debounce；查询取消前一个。
- 删除单个：若删除的是当前活动会话，先 `replaceWithNewSession()`；再删会话行 + 该会话图片文件 + `daily_sessions` 映射。
- 清空全部：`HistoryViewModel.onClearAll()`
  - 防重入；`listenerAdapter.suppressMessages = true`；
  - `NonCancellable` 内执行 `chatRepository.deleteAllImages()` + `historyRepository.deleteAll()` + `coordinator.replaceWithNewSession()`；
  - `finally` 恢复 suppress、重置搜索、关闭弹窗。
  - `deleteAllImages()` 整目录删除并重建 `chat_images`（旧版遗留的孤儿图片一并回收）；`HistoryRepository.deleteAll()` 只删 sessions（级联 messages）与 daily_sessions。
- 搜索中列表为空但库里有记录时，清空按钮仍可用（依据 `sessions.isNotEmpty() || searchQuery.isNotEmpty()`）。

### 2.6 UI/UX（`HistoryScreen` / `HistoryDetailScreen`）

**列表**

- 顶栏：返回箭头、标题 `Chat history`、右侧红色文字按钮 `Clear`。
- 搜索框：放大镜 + placeholder `Search chats...`；单行；圆角 12；SurfaceVariant 边框；无清除按钮。
- 列表：`LazyColumn` 卡片间距 8；卡片 `Surface` 圆角 12、内边距 16。
  - 标题：`firstUserMessage ?? summary ?? "Untitled conversation"`，单行省略。
  - 副标题：本地化长日期 + 完整星期 + `N messages`（plural）。
  - 右侧 Delete 图标；整卡点击进详情。
  - 左滑（EndToStart）显示红色背景 + Delete 图标，松手弹删除确认；删除确认前不真正删除。
- 空态：居中 Search 图标 64 + `No conversations yet` + `Start your first conversation`。
- 加载：`LoadingSkeleton(lineCount=5)`（40dp 圆 + 两条 shimmer 条，1200ms）。
- 删除确认：标题居中 `Delete conversation`，正文 `Delete this conversation?`，Cancel/Delete（红）。
- 清空确认：`Clear all conversations` / `Delete all conversations?`。

**详情**

- 顶栏：返回、标题 `Conversation details`、右侧 Delete。
- 只读 `LazyColumn`，消息复用 `MessageBubble`：`isFinal=true` 强制、`showCursor=false`、链接可点、图片可点、仅 job 结果气泡可点进全屏。
- 图片查看器复用会话页实现（含保存到相册提示）；Agent Job 结果复用 `AgentJobResultHost`。
- 删除确认文案 `Delete this entire conversation?`；确认后删除并返回列表。
- 详情一次性读取（`getSessionDetail`），不随实时写入更新；列表是 Room Flow，实时更新。
- 无时间戳（除 Agent 头行 `Agent: HH:mm`）；无“继续对话”入口。

**文案（en / zh-Hans）**

| Key | en | zh-Hans |
|---|---|---|
| history_title | Chat history | 对话历史 |
| history_clear | Clear | 清空 |
| history_search_placeholder | Search chats... | 搜索对话... |
| history_empty_title | No conversations yet | 还没有对话记录 |
| history_empty_subtitle | Start your first conversation | 开始你的第一次对话吧 |
| history_delete_title | Delete conversation | 删除对话 |
| common_delete | Delete | 删除 |
| history_delete_message | Delete this conversation? | 确定要删除这条对话记录吗？ |
| history_clear_all_title | Clear all conversations | 清空所有会话 |
| history_clear_all_message | Delete all conversations? | 确定要删除所有对话记录吗？ |
| history_untitled | Untitled conversation | 未命名对话 |
| history_detail_title | Conversation details | 对话详情 |
| history_detail_delete_message | Delete this entire conversation? | 确定要删除整个对话吗？ |
| history_item_meta (plural) | one: `%1$s, %2$s, %3$lld message` / other: `... %3$lld messages` | `%1$s，%2$s，%3$lld条消息` |

### 2.7 Android 已知事实/坑

1. **清空全部会删除图片文件**：`HistoryUseCase.clearAll()` 先调 `ChatRepository.deleteAllImages()`（整目录删除并重建 `chat_images`），再 `HistoryRepository.deleteAll()` 删 sessions/daily_sessions。
2. **空文本 AI 消息不落库**：代码只对文本非空的 final AI 文本落库；实际不存在纯图片 AI text（用户确认），无 UI 影响。
3. **清空/删除当前会话会立刻创建新的空会话**：`replaceWithNewSession()` 写入一条 `messageCount=0` 的 session；列表会出现 “Untitled conversation / 0 messages”。
4. **删除/清空当前会话会使会话页消息列表清空**：`ConversationViewModel` 观察 `coordinator.currentSessionId` 对应的 Room `observeMessages(sessionId)`；`replaceWithNewSession()` 切换 currentSessionId 后，UI 改为观察新（空）会话，旧消息消失，并关闭图片/Agent Job 覆盖层。adapter 的 `clearSession` 同时清空流式/转写缓存。
5. **实时会话本身读取 Room 当前会话消息**：`ConversationViewModel.init` 订阅 `currentSessionId` → `chatRepository.observeMessages(sessionId)`；因此 App 同日重启后，会话页会重新显示该会话已落库的消息，而不是从空白开始。
6. 搜索 LIKE 的 `%`/`_` 未转义，用户输入会被当作通配符；未 trim。
7. 摘要按 UTF-16 length 截断 100，可能截断 emoji。
8. `allowBackup=false`。

---

## 3. iOS 现状与差距

| 能力 | iOS 当前事实 |
|---|---|
| 消息持久化 | 无。`ConversationViewModel.messages` 仅内存（`Core/Conversation/ConversationViewModel.swift`） |
| 实时会话消息源 | iOS 仅 ViewModel 内存；Android `ConversationViewModel` 观察当前 `currentSessionId` 的 Room Flow（同日重启/换会话会重新加载/清空），iOS 需按 D2 seed/切换 |
| 会话概念 | 无本地 session；只有 SDK 服务端 session id（`ConversationCoordinator.serverSessionID`） |
| 历史入口 | `ConversationView` 菜单的 `Chats/对话` 调 `showComingSoon()`；`Chats` 文案已存在 |
| 导航 | `AppRouter.phase` + `showSettings`；`AppRootView` 用 `.fullScreenCover`/`.sheet` 呈现设置/Safari/WebView |
| 图片 | `ChatMessage.image: AiImage`（`Data` + mime/宽高/方向/朝向）；查看器 `ImageViewerOverlay`，保存 `PhotoLibrarySaver` |
| Agent Job | `ChatMessage.source`、`isAgentJobResult`、`AgentJobResultOverlay`；气泡折叠/全屏已实现 |
| Markdown/链接 | `MarkdownMessageView` + `serverBaseURL`；webroot → WKWebView，普通外链 → SFSafari |
| 生命周期 | `ConversationRuntime.start/stop` 由 `AppContainer` 创建/设置页重建时调用；切后台不 stop；无 App 终止收尾 |
| 存储基础 | `SettingsStore` 用 UserDefaults，凭据用 Keychain；无数据库/文件持久层 |
| 平台约束 | iOS 15、Swift 6、SwiftUI；iPad 兼容模式内容 maxWidth 600；暗色；竖屏（`docs/ios_dev_rules.md`） |
| 模式 | 无 translate/transcribe/lecture/omni；`ChatMessage` 无 mode |
| 消息时间戳 | `ChatMessage.timestamp` 已有；气泡不显示普通消息时间 |

---

## 4. 设计决策

### D1 存储：系统 SQLite3 + 镜像 Room schema（推荐）

- 使用 `import SQLite3`（系统 C 库，零新依赖），新建 `HistoryDatabase` actor，单连接串行访问；表名/列名/类型/SQL 语义对齐 Android（见 §5）。
- 理由：
  - “本地存储机制 follow Android”最直接：同一套 sessions/messages/daily_sessions 与搜索 SQL；
  - 免去 Core Data 的 model 文件/NSManagedObject 并发与表结构差异，便于逐条对照 Android；
  - GRDB 等第三方库会引入新依赖，不必要。
- 代价：手写 migration/prepared statement；用 actor 隔离 + 单元测试覆盖。
- 备选：Core Data（SwiftUI 集成更好，但 schema/SQL 不再与 Android 一一对应）；不推荐本期使用。
- 装配：`AppContainer` 启动时创建 `HistoryService`/DB（DB 路径和图片目录可注入，**所有 UI 测试模式使用临时目录**）；初始化失败记录日志并禁用历史写入，会话功能不受影响。HistoryService 必须在 ConversationCoordinator 之前可注入（RuntimeFactory 增加依赖）。
- 测试缝：定义 `HistoryRecording`/`HistorySessionObserving` 协议，生产实现为 HistoryService，单测/UI 预览可用 no-op/fake；避免 Coordinator 直接依赖 SQLite。

### D2 会话生命周期挂接 `ConversationCoordinator`

- **start**：`HistoryService.beginSession`（创建/复用当日会话、取 `maxSeq+1`）并返回该会话已落库的消息；在 `sdk.connect()` 之前把消息 seed 到 `ConversationViewModel`。
  - 必须先完成 seed 再连接，避免启动时新的 final 消息被 seed 覆盖；实现建议把 `ConversationRuntime.start()` 改为 async（或由 coordinator 以 completion 闸住 connect），AppContainer 用 Task 启动，期间保持 `isBusy`。
  - History 初始化失败时 begin/seed 为 no-op，不延迟 connect（会话照常工作）。
- **stop**：入队 `endSession`（写结束时间/摘要/首条用户消息）→ `currentSessionID = nil` → 清空会话 UI（对齐 Android：ViewModel 观察到 null 时清空消息）。
- **会话切换**（删除当前会话/清空全部）：`HistoryService` 发布 `sessionSwitched(newSessionID, messages)`；`ConversationCoordinator` 清空转写/流式、关闭图片/Agent 覆盖层、替换 ViewModel 消息为新会话内容（通常为空）；后续 final 消息仍由 ViewModel 内存追加并写入新会话。
- 实时消息仍由 `ConversationViewModel` 内存追加（保留现有架构）；DB 只作为“启动/切换时的初始快照”与会话列表来源——用户可见行为与 Android 的 Room Flow 一致（见 §8 差异 8）。
- SDK 的 `.sessionReady/.sessionEnded` **不**结束本地会话（与 Android 一致；本地会话跨服务端重连）。
- 服务端恢复/重连沿用现有 iOS 行为；本地历史独立。
- 应用被杀：不执行 endSession；下次启动同日继续该会话并重新 seed 已落库消息（与 Android 一致；endTime 可能为 null）。

### D3 写入串行化：MainActor FIFO 队列

- `HistoryService.enqueue(HistoryEvent)` 同步入队；单个消费 Task 顺序处理（begin/append/end/delete/clear）。`beginSession` 必须是**可等待结果**（enqueue 一个 begin 事件并用 continuation 返回 session+messages）；append/end/维护为 fire-and-forget 入队。FIFO 保证 begin 先于后续 append。
- 优点：seq 按事件顺序生成；session 替换与消息写入不会交错。
- 清空防重入：UI 在完成前保持弹窗且服务拒绝并发 clear。
- 清空全部时对齐 Android 的 `suppressMessages` 语义：清空入口**同步**置位 `HistoryService.isSuppressingMessages`，再 await 维护事件；`ConversationCoordinator` 在入口忽略 `transcription`/`aiText` 事件（UI 与落库同时抑制）；`defer/finally` 复位。维护事件本身仍走同一 FIFO 队列。
- 所有 UI 依赖 `HistoryService.revision`（Combine/`@Published`）刷新列表。

### D4 消息映射与“final-only”规则

- 复用 `ChatMessage` 作为 UI 模型；持久层新增 `StoredMessage`/`HistoryMessage` 携带 `sessionId`、`seq`。
- 只持久化 final 且文本非空的用户/AI 消息，与 Android 一致。用户确认**不存在纯图片 AI text**，因此不需要 image-only 分支；图片随文本消息一同落库。
- 时间戳由 Coordinator 生成一次，同时传给 ViewModel 与 History（避免 UI/历史时间不一致）；需要给 `finishUserTranscription`/`finishAssistantMessage` 增加 `timestamp` 参数。
- `messageCount` 在 append 时 +1（同一事务），会话结束重算。
- `firstUserMessage` 在首条用户消息写入时 `IF NULL` 设置；结束重算。
- `mode` 列保留，iOS 写 null；`source` 按 `AITextSource` 映射为 `agent`/`llm`（null 允许）。
- `role` 以 Android 一致的 `USER`/`AI` 存储，读取映射到 `ChatRole`。
- **断连后的 final 对齐 Android**：Android 只对非 final 帧要求 connected，final 仍可落库；iOS 当前 `canAcceptMediaEvents` 对 final 也要求 connected。实施时把 final 分支改为“`isStarted` 且本地 session 存在即可处理/落库”，非 final 仍要求 connected/sessionReady；并避免断连清理先清空 AI 累积导致 final 文本残缺。补迟到 final 的集成测试；若实测 SDK 不会在断连后投递 final，保留 guard 并在文档记录。

### D5 图片持久化

- 目录：`Application Support/LivaChat/chat_images/`。
- DB 只存**文件名**，不存绝对路径（iOS 容器路径会随重装/迁移变化）；读取时拼当前目录。文件名取 messageID 的 SHA-256 hex（而非直接拼接，避免 server 控制的 id 含 `/`、`..` 造成目录穿越）；加载时按 id 重新计算。
- 写入：从 `AiImage.data` 原样写 JPEG（与 Android 相同），存 mime/宽高/orientation raw/相机朝向 raw。
- 删除会话：先查该会话图片文件名，删 DB（级联）后删文件。
- 清空全部：删除全部会话 + 整个 `chat_images` 目录（与 Android 一致）。
- 文件缺失 → `image=nil`（与 Android 一致）；写文件失败 → 记日志、该消息 `imagePath=null` 但消息文本仍落库，不让图片 I/O 失败阻断对话/历史。
- 备份：对 `Application Support/LivaChat/` 设置 `isExcludedFromBackup = true`，对齐 Android `allowBackup=false`。

### D6 UI：历史入口、列表、详情导航

- 入口：`ConversationView` 菜单 `Chats/对话` → `AppContainer.openHistory()`（替换当前 `showComingSoon`）。
- 呈现：`AppRootView.fullScreenCover(isPresented: $router.showHistory)`；内部用 `NavigationView(.stack)`（iOS 15）+ 隐藏系统导航栏，自绘 52pt 顶栏；列表 → 详情为 push，返回/边缘侧滑返回。
  - 备选：自定义 route state + 转场（若隐藏导航栏后 iOS 15 的滑动返回不可用，则改用自定义返回按钮/转场）。
- 内容 maxWidth 600 居中（与 AppRootView 一致），暗色背景；打开期间实时会话保持运行（音频不中断）。
- 历史列表/详情是独立 ViewModel（对齐 Android 两层结构）。

### D7 复用消息气泡

当前 `ConversationView.messageBubble` 为私有实现。抽取为共享 `MessageBubbleView`：

```swift
MessageBubbleView(
  message, maxBubbleWidth,  // AppLayout.messageBubbleMaxWidth(containerWidth:)
  serverBaseURL,            // Markdown webroot 转换
  onOpenLink,               // AppContainer.openWebLink
  onImageTap,               // 打开 ImageViewerOverlay
  onJobResultTap            // 打开 AgentJobResultOverlay
)
```

- 会话页与历史详情共用；历史详情不渲染 streaming/transcription/banner/toolbar。
- 气泡最大宽度 = 容器宽度 75%（含内边距），与时间线 Hot Slot、Android 一致；表格在气泡内整宽但气泡不放宽。
- Agent header、折叠、链接仲裁、accessibility 标识保持不变；抽取后跑现有 M3 UI 测试回归。

### D8 链接

- 历史详情与会话共用 `AppContainer.openWebLink`：
  - 当前 server base 转换后的 webroot 链接 → WKWebView；
  - 其他 http(s) → SFSafari；
  - 非 http(s) 拦截。
- 历史里的 `livachat_url` 转换使用**当前** server base（Android 同样在渲染期用当前 `LocalServerBaseUrl`；切服后旧历史链接指向当前服务器）。

### D9 搜索

- 300ms debounce（Task 取消旧查询）；查询完成时校验 generation/当前 query，过期结果丢弃（DB 查询本身不响应取消）。
- SQL 复用 Android 的 LIKE 语义（未 trim、未转义通配符），保证与 Android 行为一致。
- 查询进行中 revision 变化时，重新执行当前查询。
- 清空全部后重置 query（对齐 Android `finally`）。

### D10 删除 / 清空 / 当前会话

- 删除单会话/清空全部通过 `HistoryService` 入队维护事件，UI await 完成：
  - 删除单个且 `currentSessionID` 匹配：先创建/切换新当日会话，再删除旧行（对齐 `replaceWithNewSession`）；新空会话立即创建并会出现在列表（“Untitled conversation / 0 messages”，与 Android 一致）；删除非当前会话直接删；
  - 清空全部：删除所有会话/映射/图片目录后**始终创建新的当日会话**（Android parity，即使当前无活动 runtime），列表会出现新的空会话；
  - 删除会话同时删除图片文件与 `daily_sessions` 映射；
  - 清空期间按 D3 抑制 UI/落库事件；完成后重置搜索。
- **会话页清空**：会话切换时按 D2 清空 ViewModel 消息/转写/流式并关闭图片、Agent 覆盖层，再显示新会话消息（空）；与 Android 观察新 session 的 Room Flow 后清空一致。没有活动 runtime 时无 UI 需要清空，新会话留给下次 start 使用。
- 历史列表是 live（revision 刷新），详情是一次性读取（对齐 Android）。

### D11 摘要与标题

- 列表标题：`firstUserMessage ?? summary ?? history_untitled`。
- 结束会话时生成 summary：前 3 条用户消息空格连接，>100 字符截断加 `...`（Swift 用 Character 计数，emoji 与 Android UTF-16 截断存在极边缘差异）。
- 首条用户消息在 append 时立即写入 `firstUserMessage`，使活动会话在结束前也有标题。

### D12 日期副标题

- 本地化长日期（`DateFormatter.dateStyle = .long`）+ 当前 locale 的完整星期 + 本地化 plural 消息数。
- 时区 `TimeZone.current`；日期/星期随系统 locale。
- 数字本地化交给系统格式化（Android 额外 `localizeDigits`；iOS 的 DateFormatter/NumberFormatter 已按 locale 输出）。

### D13 平台/错误处理

- DB 初始化失败：记录 AppLog，禁用历史写入；会话功能继续可用；历史页显示空态（对齐 Android 无错误 UI 的最小行为）。
- 写入失败：记录日志，不阻塞会话；后续写入继续尝试。
- iPad：历史 cover 内容 maxWidth 600；`NavigationView(.stack)` 避免分栏。
- 动态字体/Reduce Motion：骨架屏 shimmer 在 Reduce Motion 下静态；沿用 `appFont`/`AppColor`。

---

## 5. 数据库设计

### 5.1 DDL（iOS v1）

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY NOT NULL,
  startTime INTEGER NOT NULL,
  endTime INTEGER,
  messageCount INTEGER NOT NULL,
  summary TEXT,
  firstUserMessage TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY NOT NULL,
  sessionId TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  isFinal INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  timestamp INTEGER NOT NULL,
  imagePath TEXT,
  imageMime TEXT,
  imageWidth INTEGER,
  imageHeight INTEGER,
  imageOrientation INTEGER,
  imageCameraFacing TEXT,
  source TEXT,
  mode TEXT,
  FOREIGN KEY(sessionId) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS index_messages_sessionId ON messages(sessionId);

CREATE TABLE IF NOT EXISTS daily_sessions (
  date TEXT PRIMARY KEY NOT NULL,
  sessionId TEXT NOT NULL
);

PRAGMA user_version = 1;
```

- 时间统一存 `Int64` epoch millis。
- iOS 新装库直接从 v1 建全量 schema；不迁移 Android 5→9。
- 未来版本用 `user_version` 做增量 migration；不做 destructive fallback。

### 5.2 文件布局

```
Application Support/LivaChat/
  livachat.db            # SQLite（与 Android 同名）
  chat_images/<messageId>.jpg
```

- 整个 `LivaChat` 目录设置 `isExcludedFromBackup`（对齐 Android `allowBackup=false`）。
- 路径/文件名注入便于测试（临时目录、内存/临时 DB）。

### 5.3 SQL 语义

- 列表/搜索/消息排序/图片删除查询对齐 Android（§2.5）。
- `appendMessage` 事务：
  ```sql
  BEGIN IMMEDIATE;
  INSERT OR REPLACE INTO messages(...);
  UPDATE sessions SET messageCount = messageCount + 1 WHERE id = ?;
  -- 仅 USER：
  UPDATE sessions SET firstUserMessage = ? WHERE id = ? AND firstUserMessage IS NULL;
  COMMIT;
  ```
- `endSession` 事务：读 messages → 计算 firstUserMessage/summary/messageCount → upsert sessions 的 endTime/字段。
- `deleteSession`：读图片文件名 → 删 sessions（级联 messages）→ 删 daily_sessions → 删文件。
- `clearAll`：读全部图片文件名/目录 → 删 sessions/daily_sessions → 清空图片目录。

---

## 6. 代码结构（拟）

```
apps/ios/LivaChatApp/
  Core/History/
    HistoryModels.swift          // SessionSummary / HistoryMessage / HistoryEvent
    HistoryDatabase.swift        // actor + SQLite3 + schema/queries
    HistoryImageStore.swift      // JPEG 文件读写/清理
    HistoryService.swift         // MainActor：FIFO 队列、currentSession、revision、维护操作
    HistoryDateFormatting.swift  // 副标题日期/星期/plural
  Features/History/
    HistoryListViewModel.swift
    HistoryListView.swift
    HistoryDetailViewModel.swift
    HistoryDetailView.swift
    HistoryComponents.swift      // 空态、骨架、会话卡片
  Features/Conversation/
    MessageBubbleView.swift      // 从 ConversationView 抽取
  App/
    AppRouter.swift              // + showHistory
    AppContainer.swift           // HistoryService 装配、菜单入口
    AppRootView.swift            // history fullScreenCover
  Core/Conversation/
    ConversationCoordinator.swift // start/stop + final 消息入队
    ConversationViewModel.swift   // finish 方法接受 timestamp
  Resources/Localizable.xcstrings  // history 文案 + common_delete + plural
```

### 6.1 事件序列

```
SDK .transcription(final) ─┐
SDK .aiText(final) ────────┤
ConversationCoordinator    ├─→ HistoryService.enqueue(...) ─→ 串行消费
start/stop ────────────────┘        │
                                    ├─ HistoryDatabase actor (SQLite)
                                    ├─ HistoryImageStore (JPEG)
                                    ├─ revision → HistoryListViewModel reload
                                    └─ sessionSwitched → ConversationViewModel 清空/seed

start ──→ HistoryService.beginSession ──→ seed messages ──→ sdk.connect
HistoryListView ──(query/debounce)──→ HistoryService.search ─→ DB
HistoryDetailView ──load once──────→ HistoryService.detail ─→ DB + image files
```

### 6.2 会话状态

- `currentSessionID`、`nextSeq` 在 `HistoryService`（MainActor）内维护；begin/append/end/维护事件均入同一队列。
- `beginSession` 必须在启动时入队（先于任何 append）；seed 完成后再 connect；append 在其后处理。
- `endSession` 完成后 `currentSessionID = nil`（对齐 Android `stop()`）。
- 删除/清空当前会话时，在维护事件内部切换新会话（更新 `currentSessionID`、`nextSeq`）并发布 `sessionSwitched`；后续 append 自然写入新会话。
- 清空全部始终创建新的当日会话（Android parity）；删除单个会话仅在 `currentSessionID` 匹配时先换新会话。

---

## 7. UI 细节（iOS 映射）

| Android | iOS |
|---|---|
| TopAppBar 返回/标题/Clear | 52pt 自绘 header：chevron.left 44、标题 `appFont(.title)`、Clear 文本按钮（error 色，最小 44 高） |
| OutlinedTextField 搜索 | HStack：放大镜 + TextField；高 48；圆角 12；SurfaceVariant 1pt 边框；水平 padding 16 |
| 会话卡片 | Surface 背景、圆角 12、内边距 16、间距 8；标题 1 行 `.body`；副标题 `.caption` tertiary；右侧 44×44 trash |
| 左滑删除 | 自定义 `SwipeToDeleteRow`：右向左拖动时露出 red 背景 + 白色 trash 图标；松手超过阈值（约 30% 行宽/80pt）触发删除确认，卡片回弹，不真正删除；整卡仍可点击进详情 |
| 删除图标 | `trash` 44×44，`textTertiary` |
| 删除/清空确认 | SwiftUI `.alert`，Delete/Clear 使用 `.destructive`；与现有 Settings 确认一致 |
| 空态 | 居中 64 图标 `magnifyingglass` + `.title` 标题 + `.body` 副标题 secondary |
| 加载骨架 | 5 行：40 圆 + 两条圆角条；shimmer 1200ms；Reduce Motion 静态 |
| 详情消息 | 复用抽取后的 `MessageBubbleView`；ScrollView + LazyVStack，水平 padding 16，不自动滚底 |
| 图片查看器 | 复用 `ImageViewerOverlay`（含保存提示/翻页/缩放） |
| Agent Job 结果 | 复用 `AgentJobResultOverlay` 与折叠交互 |
| 详情删除 | 顶栏 trash → alert → 删除并 pop 回列表 |
| iPad | 内容 maxWidth 600 居中 |

- Clear 按钮可用条件与 Android 一致：`!isLoading && (!sessions.isEmpty || !searchQuery.isEmpty)`（`sessions` 为当前过滤结果）；禁用态降低不透明度。
- 副标题 plural 使用 `.xcstrings` 的 plural 变体，日期/星期由当前 locale 的 `DateFormatter` 生成；需验证 en/zh-Hans 单复数与布局。
- **格式符仅对 iOS 改写**：§2.6 表格文案来自 Android；iOS `.xcstrings` 中日期/星期用 `%1$@`/`%2$@`，消息数用 `%3$lld`（Android 是 `%1$s`/`%2$s`/`%3$d`）。
- 新增 `common_delete`（Delete/删除），其余复用现有 `common_back/common_cancel`。

**Accessibility identifiers（供 UI 测试）**

`history_open`（菜单项）、`history_back`、`history_clear`、`history_search`、`history_row`（每条）、`history_delete`、`history_empty`、`history_loading`、`history_confirm_delete`、`history_confirm_clear`、`history_detail_back`、`history_detail_delete`、`history_detail_message_*`。

**左滑实现约束**：`DragGesture(minimumDistance:)` 需要做方向判定——仅当水平位移大于垂直位移且为负方向时接管，避免抢占 `ScrollView` 纵向滚动；松手后无论是否触发删除都回弹（对应 Android `SwipeToDismissBox` 的 `confirmValueChange` 返回 false）；行内 trash 按钮与 VoiceOver 自定义 Delete action 作为无障碍/非手势入口。

---

## 8. 与 Android 的差异与已拍板

| # | 项 | Android 行为 | iOS 处理 |
|---|---|---|---|
| 1 | 清空全部图片文件 | 删除整个 `chat_images` 目录并重建 | 同 Android（内部处理，无 UI 差异） |
| 2 | 摘要截断 | UTF-16 100 chars | Swift Character 100（emoji 边缘差异） |
| 3 | 搜索通配符 | `%`/`_` 未转义 | 保持 parity（不转义） |
| 4 | 图片路径 | 绝对路径 | iOS 存相对文件名（沙盒路径稳定性） |
| 5 | 备份 | allowBackup=false | 排除 iCloud 备份 |
| 6 | 模式后缀 | translate/transcribe 行展示后缀 | iOS 无这些模式，mode=null；不显示后缀 |
| 7 | 数据迁移 | Room 5→9 迁移 | iOS 新装库直接从 v1 建全量 schema，不迁移 Android 数据 |
| 8 | 实时会话消息源 | 观察当前会话 Room Flow（重启/换会话自动重载/清空） | iOS 启动/切换时从 DB seed + 回合内内存追加；用户可见行为一致；DB 写失败时 iOS 仍显示内存消息 |

**已拍板（2026-10-21）**：

1. **不存在纯图片 AI text**：按 Android 文本非空规则落库，不做 image-only 分支。
2. **清空/删除当前会话**：follow Android，立即创建新的空会话；列表会出现 “Untitled conversation / 0 messages”。
3. **左滑删除**：follow Android，自定义红底 + trash 图标样式，不使用 iOS 原生 `.swipeActions`。
4. 存储采用系统 SQLite3 镜像 Room schema（D1）。

---

## 9. 测试计划

### 9.1 单元/集成

- DB：建表/版本；session upsert；append 事务（messageCount/firstUserMessage）；maxSeq；排序；LIKE 搜索（消息文本/summary/firstUserMessage/空串/通配符）；级联删除；daily_sessions 映射/强制新会话；清空；图片文件删除；重开 DB 持久化。
- 会话：同日复用、跨日新建、force-new、startSeq、endSession 摘要（前 3 条用户消息/100 字截断/无用户消息 null/firstUserMessage 重算）。
- 写入：final-only、空文本跳过、图片持久化/读取、缺失文件、source/mode、seq 顺序、重复 id 行为。
- 集成：Coordinator start→append user/AI→stop；断连后迟到的 final 仍落库（非 final 丢弃）；设置重建后同会话续写；同日重启先 seed 已落库消息再 connect（无覆盖/重复）；删除当前会话/清空后会话 UI 清空且新消息写新会话；stop 后 `currentSessionID=nil`；服务端 sessionEnded 不结束本地会话。
- 清空临界窗口：suppression 期间 transcription/aiText 不更新 UI、不落库；结束后恢复；并发 clear 被拒绝。
- 搜索：旧 query 的 DB 结果晚到不覆盖新 query；搜索中 revision 变化会重跑当前 query。
- 图片：文件名 SHA-256；写文件失败时消息文本仍落库且 image=nil；缺失文件读取为 nil。
- History 初始化失败：begin/append 为 no-op，会话启动/消息不阻塞。
- 失败：DB 打开失败不崩溃；写入失败不阻塞 UI。

### 9.2 UI 测试（静态注入）

- 从会话菜单打开历史；空态；会话列表排序/标题/副标题；搜索（300ms）/无结果；删除确认取消/确认；清空确认；详情 Markdown/图片/Agent Job/链接；详情删除后返回；iPad 布局。
- 会话页：同日重启后显示当前会话已落库消息；删除/清空当前会话后返回会话页为空列表/welcome；清空后列表出现新的空会话。
- 左滑删除：超过阈值弹确认、取消后行回弹且记录仍在、确认后才删除；纵向滚动不被横向手势抢占。
- 所有 UI 测试模式统一使用临时 DB（含 `-uiTestScreen history` / `-uiTestSeedHistory` 与既有 conversation/settings 测试），不污染真实数据。

### 9.3 真机

- 真实语音会话 → 历史列表出现当日会话、标题/条数；
- 详情 Markdown/图片/Agent Job/链接（Safari/WebView）；
- 删除当前会话后继续说，新会话独立记录；
- 清空后列表/图片目录；
- 杀 App 重开、同日/跨日；
- 搜索；
- 回归会话/相机/图库/设置。

---

## 10. 实施顺序

1. `HistoryDatabase` + models + image store + 单测；
2. `HistoryService`（FIFO、会话生命周期、维护操作）+ Coordinator/ViewModel 挂接 + 集成测试；
3. 抽取 `MessageBubbleView`，跑 M3 回归；
4. History 列表/详情 UI + 导航 + 文案/复数 + UI 测试；
5. iPad 真机验证、文档更新、签名构建。

---

## 11. 验收标准

- [ ] 菜单“对话”打开历史；返回不结束实时会话。
- [ ] 列表倒序、标题/副标题/消息数正确；搜索 300ms 防抖、LIKE 语义与 Android 一致；空态/骨架。
- [ ] 详情只读展示 Markdown、Agent 头行/Job 全屏、AI 图片查看/保存、链接；无工具栏/流式元素。
- [ ] 删除单会话（含图片/映射）、删除当前会话、清空全部（含图片）均有确认且不复活已删记录。
- [ ] 进程重启、同日/跨日、设置重连后历史行为与 Android 一致；同日重启会话页重新显示当前会话已落库消息；删除/清空当前会话后会话页清空并切到新会话。
- [ ] 清空后列表出现新的空会话（Android parity）。
- [ ] 无服务端/协议改动；无新增第三方依赖；iOS 15 可用；iPad 布局正常。
- [ ] 单测、UI 测试、iPad 真机通过。

---

## 12. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| SQLite C API + Swift 6 并发 | 数据竞争/崩溃 | 单 actor 连接、FIFO 写入、单元测试 |
| 删除/清空与实时写入竞态 | 消息写入已删会话 | 串行队列内维护事件；清空期间 suppression（D3）；删除前切换会话 |
| 启动 seed 与首批 final 消息竞态 | 消息被覆盖/重复 | seed 完成后再 connect；UI 切换由 `sessionSwitched` 单一入口处理 |
| 启动 seed 读取大量图片 | 首屏/连接延迟 | 当前会话通常较小；实测大会话后改为文本先 seed、图片异步补齐 |
| async start 与 stop 竞态 | 已停止的 runtime 仍 connect/seed | start 先置状态并保留取消标记；stop 取消 seed/start Task；connect 前再次校验 |
| 历史 cover 打开时实时写入 | 列表需 live 更新 | revision publisher 触发重新查询 |
| 大历史全量加载 | 内存/首屏慢 | 与 Android 一致（不分页），后续可引入分页 |
| LIKE 无 FTS | 大库搜索慢 | 与 Android 一致，后续可换 FTS5 |
| 图片文件残留/路径失效 | 存储泄漏/图片丢失 | 相对文件名、删除清理、备份排除 |
| 详情一次加载全部图片 | 大会话内存峰值 | 与 Android 一致；后续可懒加载/缩略图 |
| 历史无容量上限/无加密 | 磁盘增长/隐私 | 与 Android 一致；手动删除；备份排除 |
| iOS 15 NavigationView + 隐藏导航栏 | 滑动返回可能失效 | 保留自定义返回按钮；必要时改自定义转场 |
| 备份/隐私 | 聊天数据进 iCloud | 排除备份，对齐 Android allowBackup=false |

---

## 13. 文件索引

| 主题 | 文件 |
|---|---|
| 本文 | `docs/ios_chat_history.md` |
| Android 架构 | `docs/android_app_arch.md` §4.11 |
| Android 数据层 | `apps/android/LivaChatApp/.../data/local/*`、`data/repository/HistoryRepository.kt`、`ChatRepository.kt` |
| Android 会话/写入 | `.../domain/usecase/SessionUseCase.kt`、`.../domain/ConversationCoordinator.kt`、`.../ui/screens/conversation/ConversationListenerAdapter.kt` |
| Android UI | `.../ui/screens/history/*.kt`、`.../ui/components/MessageBubble.kt`、`EmptyState.kt`、`LoadingSkeleton.kt` |
| iOS 会话 | `apps/ios/LivaChatApp/Core/Conversation/*` |
| iOS 消息 UI | `apps/ios/LivaChatApp/Features/Conversation/ConversationView.swift`、`MarkdownMessageView.swift`、`AgentJobResultOverlay.swift`、`ImageViewerOverlay.swift` |
| iOS 导航/装配 | `apps/ios/LivaChatApp/App/AppRouter.swift`、`AppContainer.swift`、`AppRootView.swift` |
| iOS 规范 | `docs/ios_dev_rules.md` |

---

## 14. Review 记录（2026-10-21）

对抗式 review 发现并修正的严重问题：

| # | 问题 | 修正 |
|---|---|---|
| 1 | **事实错误**：原文称“删除当前会话后会话页旧消息不消失”。实际 Android `ConversationViewModel` 观察 `currentSessionId` 的 Room Flow，`replaceWithNewSession` 切换 ID 后会话页切到新（空）会话，旧消息消失；且同日重启会重新加载当前会话已落库消息 | §2.7 #4/#5、D2、D10、§6.2、测试/验收均已改为“启动 seed + 会话切换清空/加载” |
| 2 | 只描述了历史页，遗漏实时会话本身就是 Room 驱动（重启/换会话行为） | 新增 §3 gap、D2 seed/sessionSwitched、§6.1 数据流、测试与风险 |
| 3 | 清空 suppression 未定义置位/复位时机；并发 clear 未防重入 | D3/D10 明确同步置位、await、finally 复位；拒绝并发 clear |
| 4 | begin 必须可等待并在 connect 前 seed，否则首批 final 会被覆盖 | D2/D3/§6.2 明确 awaitable begin、seed 后 connect、异步 start 与 stop 竞态防护 |
| 5 | 图片文件名直接由 server 控制的 messageID 拼接存在路径穿越风险 | D5 改为 SHA-256 文件名；补充写失败不阻断消息 |
| 6 | 搜索旧查询结果可能晚到覆盖新结果 | D9 增加 generation/当前 query 校验 |
| 7 | iOS 格式化字符串误用 Android 的 `%s`/`%d`；缺失 `common_delete` | §7 改为 `%@`/`%lld`；文案表补 `common_delete` |
| 8 | UI 测试可能读写真实历史库 | 所有 UI 测试模式统一临时 DB |
| 9 | 断连后迟到的 final：Android 允许落库，iOS 现有 `canAcceptMediaEvents` 会丢弃 | D4 明确 final 门控对齐 Android，并增加迟到 final 测试 |
| 10 | 详情一次性加载全部图片、启动 seed 读取大量图片的内存/延迟风险 | §12 风险表补充 |

---

## 15. 实施记录（2026-10-21）

**状态**：已实现；单测、模拟器全量 UI 测试、iPad 真机 UI 测试与真实 server 启动验证通过。

### 15.1 落地文件

| 文件 | 内容 |
|---|---|
| `Core/History/HistoryModels.swift` | Session/Message 记录、HistorySeed/Detail、Recording 协议、摘要 |
| `Core/History/HistoryDatabase.swift` | SQLite3 actor、schema v1、session/message/daily_sessions、搜索/删除 |
| `Core/History/HistoryImageStore.swift` | JPEG 文件（SHA-256 文件名）、读写/清理 |
| `Core/History/HistoryService.swift` | MainActor FIFO（Task chain）、begin/seed、append/end、删除/清空、sessionSwitched、suppression、DEBUG 测试数据 |
| `Core/History/HistoryDateFormatting.swift` | 本地化长日期 + 星期 + plural 副标题 |
| `Features/History/HistoryListViewModel.swift` / `HistoryListView.swift` | 列表、300ms 搜索、删除/清空、空态/骨架 |
| `Features/History/HistoryDetailViewModel.swift` / `HistoryDetailView.swift` | 只读详情、图片查看、Agent 结果浮层、删除 |
| `Features/History/HistoryFlowView.swift` | 列表 → 详情自定义导航 |
| `Features/History/HistoryComponents.swift` | 会话卡片、自定义左滑、空态、骨架 |
| `Features/Conversation/MessageBubbleView.swift` | 从 ConversationView 抽出的共享气泡 |
| `ConversationCoordinator` / `ConversationViewModel` / `ConversationRuntimeFactory` / `AppContainer` / `AppRouter` / `AppRootView` / `ConversationView` | 会话生命周期挂接、seed/切换、菜单入口、Safari/WebView 复用 |
| `Localizable.xcstrings` | history 文案、`common_delete`、`history_item_meta` plural（en/zh-Hans） |
| `LivaChatAppTests/HistoryStorageTests.swift` | 存储/摘要/图片/服务生命周期（10 条） |
| `LivaChatAppUITests/AppShellUITests.swift` | history 7 条 UI 测试 |

### 15.2 关键实现与差异

1. **存储**：系统 SQLite3，actor 串行访问，WAL + 外键；schema v1；`Application Support/LivaChat/livachat.db`，图片同目录 `chat_images/`；整个目录排除 iCloud 备份（对齐 `allowBackup=false`）。
2. **会话生命周期**：`AppContainer` 先 `await historyService.beginSession()`，把已落库消息 seed 到 ViewModel 后再启动 runtime/连接；`ConversationCoordinator.stop()` 入队结束会话。**移除了 M1 的 `.sessionReady(restored:false)` 清空会话**——本地会话与服务端 session 分离，同日重启/新 server session 不应清掉本地历史。
3. **写入**：MainActor Task-chain FIFO；final 且文本非空的 user/AI 消息；时间戳由 Coordinator 生成并同时用于 UI/历史；图片写 SHA-256 文件名，失败不阻断消息；seq 在处理时按当前会话分配，避免删除/清空竞态。
4. **清空/删除当前会话**：先创建新当日会话再删除旧行，发布 `sessionSwitched`；协调器清空转写/流式/图片/Agent 覆盖层并 seed 新会话（空）。清空全部期间同步置位 suppression，协调器忽略 `transcription`/`aiText`，finally 复位。
5. **断连后 final**：`canAcceptMediaEvent(isFinal:)` 允许 final 在断连后处理/落库，非 final 仍要求 connected/sessionReady；断连不再清空 AI 累积缓存。
6. **UI**：列表/详情采用**自定义 route 转场**而非 `NavigationView`——iOS 15 下隐藏导航栏 + `NavigationLink` 会造成搜索框与列表行布局/hit-test 重叠（实测点击搜索框会进入详情），该 fallback 已在实现中采用；返回按钮/删除后返回均正常。
7. **左滑**：自定义 `DragGesture`（仅水平负向接管、松手回弹、超阈值弹确认）；行标题区用 `onTapGesture` + `.accessibilityAddTraits(.isButton)` + a11y action，避免 Button 吞掉滑动手势；XCTest 左滑/取消/记录保留已验证。
8. **测试数据**：所有 UI 测试使用临时 DB；`-uiTestScreen history` 自动 seed（Kyoto Markdown/表格/链接、图片、Agent Job、Dinner），`-uiTestHistoryEmpty` 跳过 seed 验证空态。
9. **文案**：iOS `.xcstrings` 使用 `%1$@`/`%2$@`/`%3$lld`；英文单复数与中文副标题已随 UI 测试渲染验证。
10. **未实现（与本文范围一致）**：服务端历史同步、会话恢复继续聊天、分页/FTS、历史音频、Android 模式后缀；`mode` 列保留写 null。

### 15.3 验证结果

- 单测：`HistoryStorageTests` 10 条 + Coordinator 新增（final 记录/迟到达 final/suppression/session switch/finish）+ 全量 `LivaChatAppTests` 通过。
- 模拟器：全量 UI 测试通过（含 7 条 history：空态、列表/搜索/详情、删除/清空、左滑、图片查看、Agent 浮层、菜单入口/返回）。
- iPad Pro 10.5 / iPadOS 17.7.5：12 条 UI 测试全部通过（history 7 条 + M3/webroot 5 条）。
- Mock `text_on_start`（端口 18080）真机/模拟器联调：真实 SDK final 用户/AI 消息写入 SQLite；同进程重启后同一会话 seed 出 4 条消息并继续追加。
- 真实 server 启动：iPad 上创建 schema v1 数据库、当日会话与消息落库；App 正常连接。
- `./scripts/build-ios.sh app` 签名构建成功并安装。

### 15.4 待人工验证

- 真实 server 的语音多轮 + Agent Job 长结果 + AI 图片在历史详情的完整链路（本次已覆盖 mock 文本与 seeding UI）。
- 跨本地日的新会话、清空/删除当前会话后持续说话的线上表现。
- VoiceOver/Dynamic Type 大字号逐档冒烟；iPad 横竖屏（App target 竖屏）。
- 中文历史副标题的 plural 实机渲染（英文已由 UI 测试覆盖）。

### 15.5 Review 修复（2026-10-21）

对抗式 review 发现并修复的严重问题：

| # | 问题 | 修正 |
|---|---|---|
| 1 | **停止时会丢失已入队的 final 消息**：`finishSession()` 同步把 `current` 置空，先入队但尚未处理的 append 在处理时找不到当前会话而被丢弃 | begin/append/finish 全部在处理时读取/清空 `current`；finish 在队列中处理完前面的 append 后再置空；新增“record 后立即 finish”单测 |
| 2 | **清空全部可能残留孤儿图片**：原实现只删 DB 引用的图片文件，图片写入成功但 DB append 失败等孤儿文件不会被清理 | 清空全部改为删除整个 `chat_images` 目录并重建；新增孤儿图片清理单测 |
| 3 | **会话切换未重置 AI 累积**：删除/清空当前会话后，下一条 AI final 可能拼上上一会话的流式文本 | `sessionSwitched` 处理器先 `resetAIStreaming()` 再替换会话；新增切换后同 messageID 的累积单测 |
| 4 | **`.sessionReady` 清空 AI 累积**：重连/新 server session 后迟到的 final 会丢前缀 | 移除 sessionReady 的 AI 累积重置（新消息 index 0 仍会重置）；新增 sessionReady 后迟到 final 单测 |
| 5 | DB append 失败时已写入的图片文件成为孤儿 | append 失败时删除刚写入的图片文件 |
