# iOS 时光机（MyLife Timeline）总体架构与共享基础设施

**状态**：已实现（2026-10-22）
**范围**：iOS App 内新增「时光机」子系统的总体架构、网络与数据层、导航、视觉基础、媒体管线、本地化与测试策略。
**UI/UX 基线**：Android 现行实现（`docs/android_timeline_homepage.md`、`android_timeline_topics.md`、`android_timeline_hotslot.md`、`android_timeline_image_gallery.md`、`android_timeline_audio_gallery.md`）。
**以 codebase 为准**：Android 行为以 `apps/android/LivaChatApp/...` 与 `services/bff/...` 当前代码为准；iOS 设计只使用 `apps/ios/LivaChatApp/...` 已存在的能力或明确列出的新增文件。未在代码或契约中出现的行为不做假设。

> 本文件是所有 `ios_timeline_*` 文档的共享基础。各子页文档只描述该页的差异与细节，不再重复网络/主题/导航定义。

---

## 1. 文档集

| 文档 | 内容 |
|---|---|
| `ios_timeline_arch.md`（本文） | 总体架构、数据层、导航、主题、媒体、本地化、测试、Android 差异总表 |
| `ios_timeline_homepage.md` | 入口、首页骨架、粒度浏览、卡片、下钻、分页、搜索模式 UI、页面状态 |
| `ios_timeline_topics.md` | topic 聚合与 chip、topic 点击搜索、搜索结果、命中进入 EventList |
| `ios_timeline_hotslot.md` | 活跃时段 chip、hotSlotView、行模型、帧查看器、Agent 结果、eventListViewer |
| `ios_timeline_image_gallery.md` | 卡片缩略图、Gallery、focus、全屏查看器、保存、编辑删除 |
| `ios_timeline_audio_gallery.md` | 日卡音频入口、AudioGallery、播放器、能量包络、时间头跳转 |

不在本组文档范围：后端数据生产（day_summary / day_stats / FTS 索引）、Web 版 MyLife、Android 实现变更。

---

## 2. iOS 现状与平台约束

### 2.1 现状（代码事实）

| 能力 | iOS 当前事实 | 代码锚点 |
|---|---|---|
| 时光机入口 | 会话页更多菜单已有「时光机」高亮项，点击走 `showComingSoon()` | `Features/Conversation/ConversationView.swift:349-363,465-467` |
| MyLife 数据层 | 不存在 | — |
| 导航 | `AppRouter` 枚举 + 多个 `fullScreenCover`（Settings/History）；iOS 15 无 `NavigationStack` | `App/AppRouter.swift`、`App/AppRootView.swift` |
| 自定义页面栈 | History 使用自定义 route state + 转场；实现记录明确 `NavigationView` 隐藏导航栏在 iOS 15 存在布局/hit-test 问题 | `Features/History/HistoryFlowView.swift`、`docs/ios_chat_history.md` §15.2-6 |
| HTTP | `URLSession` + `ProbeSessionDelegate`（自签 TLS、禁跟随重定向）+ `{error:{code,message}}` 信封 | `Core/Conversation/AgentJobCancelClient.swift`、`Core/Settings/ServerProbe.swift` |
| 凭证/服务器地址 | `SavedServerConfig{wireToken, deviceID, serverAddress}`；`ServerBase` 由 `wss` 推导 `https` | `Core/Settings/SettingsStore.swift`、`Core/Settings/ServerAddress.swift` |
| 远程图片 | 工程内无网络图片组件；`deps/NetworkImage` 已 vendored（MIT、iOS 14+、支持注入 `URLSession` 的 loader），但尚未链接 | `apps/ios/deps/NetworkImage/` |
| Markdown | MarkdownUI 本地 SPM；`MarkdownMessageView` 处理 webroot 转换、链接白名单、表格降级 | `Features/Conversation/MarkdownMessageView.swift` |
| Agent 结果 | `AgentJobResultOverlay`（图标 + 到达时间 + 关闭 + Markdown）；`AgentJobCollapse` 折叠 | `Features/Conversation/AgentJobResultOverlay.swift`、`AgentJobCollapse.swift` |
| 图片查看器 | `ImageViewerOverlay` 提供 1x–5x、平移、翻页、保存；`ImageViewerMath` 为同模块共享数学 | `Features/Conversation/ImageViewerOverlay.swift` |
| 保存相册 | `PhotoLibrarySaving` + `PhotoLibrarySaver`（`.addOnly` 权限，JPEG `Data`） | `Core/PlatformAdapters/PhotoLibrarySaver.swift` |
| 实时音频 | AVAudioEngine + `AudioPlaybackController`（TTS）；`AudioSessionController` 配置 `.playAndRecord` + `.voiceChat` | `Core/Audio/` |
| 本地化 | `Localizable.xcstrings`，当前 16 locale（含 `th`/`vi`，不含 `tr`）；`L10n.t/plural` | `Resources/Localizable.xcstrings`、`Core/UI/L10n.swift` |
| 视觉 | 暗色；`AppColor/AppSpacing/AppRadius/AppFont`；`maxWidth 600`；竖屏 | `Core/UI/AppTheme.swift`、`App/AppRootView.swift`、`Resources/Info.plist` |

### 2.2 平台约束

- iOS 15 起；Swift + SwiftUI；新增依赖走 SPM；UIKit 仅允许在 `PlatformAdapters`（`docs/ios_dev_rules.md`）。
- App target 默认竖屏；仅 Agent Job 结果全屏查看期间临时解锁横竖屏，关闭后恢复竖屏。iPad 以 iPhone 兼容模式运行，内容 `maxWidth 600`（Agent Job 覆盖层由窗口级宿主呈现，会话/History/Timeline 三个入口横屏均可越过该上限）。
- 仅中英文；Android 文档里的 15 语言规则不逐条移植，日期/周/月标签按 `en` / `zh-Hans` 输出与 Android 对应语言一致。
- 音频文件服务端为 Ogg Opus；AVFoundation 不支持 Ogg 容器，iOS 必须使用 BFF 的 `?format=m4a` 转码回退（`services/bff/common/media.py:38-45`）。
- 实时会话的 `.playAndRecord` + `.voiceChat` + voice-processing I/O 会让独立 `AVAudioPlayer` 静音；时光机画廊必须把音频调度到同一 `AVAudioEngine` 才可听（`docs/ios_timeline_audio_gallery.md` §5.4）。
- 自签 TLS 必须同时覆盖 JSON、图片、音频下载与 WebView；统一使用 `ProbeSessionDelegate`（WebView 已有自己的信任实现）。

---

## 3. 总体结构

```
App/
  AppContainer
    ├─ 解析 SavedServerConfig（wireToken / deviceID / ServerBase）
    ├─ 创建 MylifeStore（每个服务器配置一个）
    ├─ 提供 realtimeSpeech 状态（sessionState == .speaking）
    └─ openTimeline() / openSettings() / openWebLink()
  AppRouter (+ showTimeline)
  AppRootView
    └─ .fullScreenCover(isPresented: showTimeline) { TimelineFlowView }

Core/Mylife/
  MylifeDTOs.swift          # Codable DTO（显式 CodingKeys）
  MylifeModels.swift        # OverviewWindow / PeriodDigest / HotSlotEvent / FrameItem / AudioSegment / EnergyEnvelope
  MylifeAPIClient.swift     # URLSession、token、TLS、错误信封、媒体 URL
  MylifeStore.swift         # overview 窗口、分页合并、60s 轮询、媒体下载、删除
  MylifeServicing.swift     # Store 协议（UI 测试注入 fake；与 Store 可同文件）
  MylifeImageLoader.swift   # NetworkImage 自定义 loader（TLS + URLCache）
  MylifeDateLabels.swift    # 日期/周/时间标签（en / zh-Hans）
  MylifeErrors.swift

Core/Audio/
  MylifeAudioPlayer.swift   # m4a 下载 + 会话引擎/AVAudioPlayer 双后端 + 单播状态机（Media 层）

Features/Timeline/
  TimelineFlowView.swift    # 全屏宿主 + 自定义页面栈
  TimelineFlowModel.swift   # path / 路由模型 / 播放器生命周期
  TimelineRoute.swift       # 路由参数（值类型）
  Components/
    MylifeTheme.swift       # 色板、字体、星点背景、Loading/Error/Empty
    PeriodCard.swift        # 首页周期卡片
    HotSlotChips.swift
    TopicChipRow.swift
    AudioEntryButton.swift
    ScopePill.swift
    ...
  Home/TimelineHomeView.swift + TimelineHomeViewModel.swift
  HotSlot/HotSlotView.swift + HotSlotViewModel.swift + HotSlotRows.swift
  HotSlot/EventListView.swift
  HotSlot/FrameViewerOverlay.swift
  Gallery/FrameGalleryView.swift + FrameGalleryViewModel.swift + FrameGrid.swift + GalleryViewerOverlay.swift
  Audio/AudioGalleryView.swift + AudioGalleryViewModel.swift + AudioSegmentCard.swift + SegmentProgressBar.swift
```

依赖方向：View → ViewModel → `MylifeStore` → `MylifeAPIClient`。ViewModel/Store 不依赖 SwiftUI View；`MylifeAudioPlayer` 依赖 AVFoundation，属于 Media 层，不进入 View。

---

## 4. 数据层

### 4.1 服务器与鉴权

- base URL：`ServerBase.baseURLString`（`https://host:port`），业务路径前缀 `/api/mylife`。
- token：`SavedServerConfig.wireToken`（`sha1_hex(secret + "LivaChat")`），作为 query 参数 `token`。
- **不发送 `device_id`**：与 Android `MylifeApi` 一致（`MylifeApi.execute` 只附 `token`）。BFF 侧 `principal.device_id == nil`，timeline/search 仍按 `bff.device_uuids` 白名单过滤；overview 本身不做设备过滤。
- TLS：`ProbeSessionDelegate(allowSelfSigned:expectedHost:expectedPort:)`；重定向一律拒绝（与 `ServerProbe` 同语义）。
- 明文 secret 不进入此层，只使用 wire token。

### 4.2 `MylifeAPIClient`

```swift
final class MylifeAPIClient: @unchecked Sendable {
    init(serverBase: ServerBase, token: String)

    func overview(timezone: String, limit: Int?, cursor: String?) async throws -> OverviewResponse
    func timeline(dateFrom: String?, dateTo: String?, types: String?, limit: Int,
                  cursor: String?, timezone: String?) async throws -> TimelineResponse
    func search(query: String, limit: Int, cursor: String?) async throws -> SearchResponse
    func removeEvents(eventRefs: [String]) async throws -> RemoveResponse
    func download(url: URL) async throws -> Data
    func mediaURL(relativePath: String, format: AudioFormat?) -> URL?
}
```

- JSON session：`URLSessionConfiguration.ephemeral`，`timeoutIntervalForRequest/Resource = 10s`，无 cookie/缓存；remove 使用 30s 超时；媒体下载 60s。
- `Accept: application/json`；POST `Content-Type: application/json; charset=utf-8`。
- 错误：非 2xx 解析 `{error:{code,message}}` → `MylifeAPIError(status:code:message:)`；无法解析时 `code = "internal"`、`message = "HTTP <status>"`。
- 展示：`MylifeAPIError.message` 非空时展示服务端文案；URLSession/解码等本地错误展示通用文案（`mylife_load_failed` / `mylife_search_failed` / `player_play_failed`），不直接把 `localizedDescription` 暴露给用户。
- 取消：`Task` 取消必须向上传播，ViewModel 不得把 `CancellationError` 当成错误态。
- URL 构造：
  - `mediaURL` 先把相对路径相对 `baseURLString` 绝对化；
  - 附 `token`；
  - audio 额外附 `format=m4a`；
  - 图片不附加 format。
- 不使用 `device_id`；不缓存 JSON（搜索/列表每次直通，与 Android 一致）。

### 4.3 DTO（与 BFF 字段逐项对齐）

必须定义（字段名用显式 `CodingKeys`，缺失字段给默认值，未知字段忽略）：

| DTO | 字段 |
|---|---|
| `OverviewResponse` | `schema_version`、`timezone`、`days[OverviewDay]`、`next_cursor?`、`total` |
| `OverviewDay` | `date`、`counts: [String:Int]`、`summary?`、`hot_slots[HotSlot]`、`images[DayImage]`、`has_pending`、`topics[DayTopic]` |
| `HotSlot` | `start_ms`、`end_ms`、`count`（客户端只消费 start/end） |
| `DayImage` | `frame_id`、`ts_ms`、`event_ref`、`media_url`、`window_id?` |
| `DayTopic` | `topic`、`detail?`（恒 null，不渲染）、`search_words?` |
| `TimelineResponse` | `schema_version`、`timezone`、`days[TimelineDay]`、`next_cursor?`、`total` |
| `TimelineDay` | `date`、`events[TimelineEvent]` |
| `TimelineEvent` | `ts_ms`、`type`、`role?`、`text?`、`summary?`、`agent_name?`、`job_key?`、`media_url?`、`frame_id?`、`window_id?`、`caption?`、`event_ref?`、`transcript?`、`audio_summary?`、`duration_s: Double?`、`energy?`、`energy_v?`、`frame_ms?`、`db_min: Double?`、`db_max: Double?` |
| `SearchResponse` | `schema_version`、`query`、`total`、`next_cursor?`、`hits[SearchHit]` |
| `SearchHit` | `date`、`ts_ms?`、`kind`、`snippet` |
| `RemoveResponse` | `removed[String]`、`skipped[RemoveSkipped]`（`event_ref`、`reason`）等；其余字段可忽略 |
| `EnergyEnvelope` | `frame_ms`、`db_min`、`db_max`、`values: [UInt8]`（解码后的领域模型） |

注意：
- `duration_s` 必须用 `Double?`：写入侧可能输出浮点，用 `Int` 会整页解析失败（Android 同因）。
- `type`/`kind` 用 `String`，未知值不整包失败。
- 所有 `*_ms` 为 epoch 毫秒（`Int64`）。
- Android 侧多处依赖**稳定排序**（同 `ts_ms` 保持 BFF 的 priority 顺序）。Swift 的 `sorted` **不保证稳定**：timeline 事件、frame 列表、audio 段、图片轮转候选的排序必须带原始下标做 tie-breaker；否则同毫秒事件的相对顺序可能变化，影响行分组、分钟标签、锚点与轮转结果。

### 4.4 `MylifeStore`

`@MainActor final class MylifeStore: ObservableObject`，每个服务器配置一个实例，持有：

```swift
@Published private(set) var overviewWindow: OverviewWindow?
struct OverviewWindow {
    let timezone: String
    var days: [OverviewDay]        // date 降序
    var nextCursor: String?
    var total: Int
}
```

方法：

| 方法 | 行为 |
|---|---|
| `refreshOverview(force:limit:cursor:)` | 取一页并与窗口合并；`cursor == nil` 从最新开始；`force == false` 且有窗口时直接返回 |
| `loadMoreOverview(limit:)` | 用 `nextCursor` 取更早一页；无游标不发请求 |
| `hasPendingAnalysis()` | 已加载窗口最新 **400 天**内任一 `has_pending` |
| `search`、`hotSlotEvents`、`frames`、`audioSegments` | 直通 API + 出口映射（见各自文档） |
| `removeEvents` | 直通删除 |
| `downloadMedia` | 下载图片/音频字节（`async throws`；取消向上传播） |
| `markRemovalRefreshPending` / `flushRemovalRefreshIfNeeded` | 图片删除后的延迟窗刷新 |
| `startPolling()` / `stopPolling()` | 时光机打开/关闭时控制 60s 轮询 |

窗口合并规则（与 Android `MylifeRepository` 一致）：

1. 按 `date` 去重：新页覆盖旧页同一天；其余旧日保留。
2. `days` 始终按 `date` 降序。
3. `nextCursor` 取更深的一方：任一为 `nil` 则 `nil`（已到最早），否则取字典序较小者。
4. 响应 `timezone` 与当前窗口不同 → 丢弃旧窗口，以新页重建；若请求的是更早页/定位页（`cursor != nil`），改回从第一页重取，避免最新日卡消失。
5. 请求时区：`TimeZone.current.identifier`；BFF 可能把生效时区改为「最新时区」，客户端以响应 `timezone` 为准。

轮询：

- `startPolling` 后启动单个 `Task`；循环：等待窗口存在且 `hasPendingAnalysis()` → 每 60 秒 `refreshOverview(force:true, limit: 当前已加载天数 clamp 1...400)`；无 pending 时挂起等待，窗口更新（首屏/加载更早/子页刷新）后重新评估。
- 失败静默（记 AppLog），下个周期重试；取消时不得错误上报。
- 轮询窗口是「已加载的最新 N 天」，深尾不参与。
- iOS 在 **TimelineFlowView 打开期间**启动轮询，关闭即停止；窗口缓存保留在 Store 内。这样用户可见行为与 Android 相同（分析完成后首页自动更新），但不让后台无端轮询。

生命周期：

- AppContainer 在 `openConversationIfNeeded()` 解析出 `token` / `ServerBase` 后创建 `MylifeStore`（与创建 `AgentJobCancelClient` 同一处）；`applyServerSettingsAndReconnect()` 时销毁并重建，同时把 `router.showTimeline` 置 false。
- `configurePreview()`（UI 测试）不构建真实 Store；测试通过 `MylifeServicing` 注入 fake。
- 无 Store（未配置服务器）时，点击「时光机」进入设置页（复用 `openSettings()`），不打开空时间线。
- 实时会话「正在说话」状态：AppContainer 增加 `@Published private(set) var isRealtimeSpeaking`，在每次创建/替换 `ConversationViewModel` 时订阅 `$sessionState` 并更新（旧订阅取消）；AudioGallery 播放器使用 `$isRealtimeSpeaking.removeDuplicates()`。

### 4.5 BFF 契约（iOS 必须按此调用）

| 端点 | 参数 | 响应要点 |
|---|---|---|
| `GET /api/mylife/overview` | `timezone`、`limit`(1..400，可空)、`cursor`(YYYY-MM-DD，可空)、`token` | `days[]` 为**本页**、`next_cursor`、`total`；`summary/topics` 仅 `status=ok` 透出；`hot_slots`/`images`/`counts` 来自 day_stats |
| `GET /api/mylife/timeline` | `date_from`、`date_to`、`types`、`limit`(1..500，默认 200)、`cursor`(`<date>\|offset`)、`timezone`、`token` | `days` 日期倒序、`days[].events` 时间升序；`next_cursor`、`total`、`timezone`；未知 type → 400 |
| `GET /api/mylife/search` | `q`、`limit`(1..200，默认 50)、`cursor`(`0\|offset`)、`timezone`、`token` | `hits[]` 五类，bm25 相关度序；`total`、`next_cursor`；空 q → 400 |
| `POST /api/mylife/events/remove` | `token`；body `{"event_refs":[...]}`（1..50） | 部分成功：`removed` + `skipped`（`not_found`/`not_video`）；30s 超时 |
| `GET /api/mylife/media/{device}/{session}/{kind}/{filename}` | `token`；audio 可加 `format=m4a` | 图片/OGG 原文件；audio `m4a` 由 BFF ffmpeg 转码并缓存；支持 Range；`Cache-Control: immutable` |

- `overview` 时区解析：请求时区等于 `timezones` 表最新时区才使用请求值，否则强制使用最新时区；非法时区 → 400。
- `has_pending`：`status == "pending"` 恒 true；`status == "failed"` 且 `fail_count < 3`（仍会重试）也 true；其余 false。
- `timeline`/`search` 使用请求时区；search 在 Android 未传 timezone，iOS 保持不传（日期头来自 BFF 时区）。
- `agent-jobs/cancel` 与时光机无关：`hotSlot` 展示的是历史结果，不提供取消。

---

## 5. 导航与页面生命周期

### 5.1 入口

```
会话页「⋯」菜单
  → 「时光机」
      ├─ MylifeStore 存在 → AppRouter.showTimeline = true
      └─ 无服务器配置   → AppContainer.openSettings()
```

- 菜单动作从 `showComingSoon()` 改为 `onOpenTimeline`；文案继续使用已有 `common_time_machine`。
- `AppContainer.openTimeline()` 先像 `openHistory()` 一样关闭 Agent job 面板/头像/模式浮层，再置 `showTimeline`。
- 打开时光机**不停止**实时会话（与 History 行为一致）；麦克风采集、TTS、相机保持原状态。
- 关闭时光机后回到会话页。

### 5.2 呈现

- `AppRootView.fullScreenCover(isPresented: $router.showTimeline)`，内容 `maxWidth 600` 居中，暗色；Agent Job 结果宿主（`AgentJobResultWindowHost(owner: .timeline)`）位于同一 cover 的 ZStack 根部，覆盖在 600pt 内容列之上。
- 时光机根视图铺满全屏：Abyss 底色 + 星点背景；内容列 600pt。
- 无底部 tab、无侧栏、无系统导航栏；自绘 52pt 顶栏。

### 5.3 页内页面栈

iOS 15 不使用 `NavigationView`（History 实测布局/hit-test 问题），采用与 `HistoryFlowView` 同族的自定义栈：

```swift
enum TimelineRoute: Equatable {
    case hotSlot(startMs: Int64, endMs: Int64, pending: Bool)
    case eventList(tsMs: Int64, pending: Bool)
    case gallery(from: String, to: String, label: String, focusFrameId: String?, pending: Bool)
    case audio(date: String, label: String, count: Int, pending: Bool)
}

@MainActor final class TimelineFlowModel: ObservableObject {
    let home: TimelineHomeViewModel          // 根，常驻
    @Published private(set) var stack: [TimelineStackEntry]   // 子页
    let audioPlayer: MylifeAudioPlayer       // 整个时光机会话唯一
}
```

- 根视图 `TimelineHomeView` 始终挂载；压入子页时仍保留在栈底（不可见、不可点击、`accessibilityHidden(true)`），因此返回后首页的粒度、下钻、query、浏览/搜索滚动位置、子页状态都保留。
- 子页各自持有 ViewModel（`HotSlotViewModel` / `FrameGalleryViewModel` / `AudioGalleryViewModel`），在压栈时创建、弹栈时销毁；压栈前清空搜索焦点并收起键盘。
- 从 AudioGallery 进入 EventList 时，AudioGallery 的 ViewModel 与播放器仍在栈中，播放继续；返回后列表位置与播放状态保留。
- 关闭时光机（根返回）销毁整个 flow：停止音频播放、清理临时音频文件、取消轮询、清空搜索与覆盖层。再次进入重新拉取第一页。
- 转场：从右滑入/滑出 0.2s；`accessibilityReduceMotion` 时不做动画。
- 子页高度以安全区顶部为准；不复制星点背景，仅 Abyss 纯色（对齐 Android 子页）。

### 5.4 返回语义

| 场景 | 行为 |
|---|---|
| 首页顶栏返回 | 关闭时光机（回到会话页） |
| 首页搜索模式 + 顶栏返回 | 第一次清空 query 并留在时光机；query 已空再按才关闭 |
| 子页顶栏返回 | 弹栈；若该页有全屏覆盖层/编辑模式，先由页面消费（见各自文档） |
| 图片详情删除中 | 顶栏返回与页面返回手势均禁用，直到请求结束 |
| Agent/图片覆盖层打开 | 第一次返回只关闭覆盖层 |
| 语音播放中返回 | 先停止播放并弹栈 |
| 系统侧滑返回 | 自定义栈不启用系统边缘手势；统一使用顶栏返回按钮（与现有 History 一致） |

> iOS 只有顶栏返回一个返回入口，因此把 Android「系统返回键」的语义映射到顶栏返回：搜索 query 非空先清 query；子页覆盖层打开先关覆盖层；Gallery 编辑模式先退编辑；删除中吞掉；最后才弹栈/关闭。Android 顶栏返回在编辑模式下会直接退 Gallery，iOS 不保留这一分支差异——统一为“先退编辑”。

### 5.5 导航参数表

| 触发 | 目标 | 参数 |
|---|---|---|
| 月卡空白区域 | 页内：日粒度 + 该月 scope | 月 key（`YYYY-MM`） |
| 年卡空白区域 | 页内：月粒度 + 该年 scope | 年 key（`YYYY`） |
| 卡片缩略图/数量角标 | Gallery | from/to、label、focusFrameId、pending |
| 日卡活跃时段 chip | hotSlotView | startMs、endMs、pending |
| 日卡音频入口 | AudioGallery | date、label、count、pending |
| 主题 chip | 页内搜索模式 | search_words（空白回退 topic） |
| 搜索命中行 | eventListViewer | tsMs、pending |
| 语音段时间头 | eventListViewer | tsMs、pending |

---

## 6. 视觉系统

### 6.1 色板（MyLife 子主题）

新增 `MylifePalette`（不复用全局语义色；与 Android `MylifeColors` 逐值一致）：

| Token | Hex | 用途 |
|---|---|---|
| `abyss` | `#0A0E1A` | 页面底色 |
| `panel` | `#141A2E` | 卡片、面板、输入框底 |
| `line` | `#232C47` | 描边、未播轨道、分隔 |
| `star` | `#E8ECFF` | 主文字 |
| `dust` | `#8B93B8` | 次要文字、时间、图标 |
| `gold` | `#F5C86E` | 标题、选中、发音入口、高亮 |
| `red` | `#F56E6E` | 错误、删除（MyLife 独立红，不引用 `AppColor.error`） |

- 用户气泡继续使用全局 `AppColor.brandIndigo`（`#6366F1`）；AI/Agent 气泡使用全局 `AppColor.surface` + `surfaceVariant` 描边 + `textPrimary`，与 Android 的「MyLife 页面内沿用全局气泡色」一致。

### 6.2 字体

Android MyLife 使用 Material3 Typography；iOS 在 `MylifeTheme` 内新增与之一一对应的语义样式（全部 `@ScaledMetric`，随 Dynamic Type）：

| 语义 | Android | iOS 基线 |
|---|---|---|
| 页面标题 | `titleLarge` 22 / 28 SemiBold | 22 / 28 semibold |
| 分组头（月/年） | `headlineMedium` 28 / 36 Regular | 28 / 36 regular |
| 子页标题 | `titleMedium` 16 / 24 Medium | 16 / 24 medium |
| 卡片标题 | `titleMedium` 16 / 24 Medium | 16 / 24 medium |
| 卡片正文/摘要 | `bodyMedium` 14 / 20 Regular | 14 / 20 regular |
| 气泡正文 | `bodyLarge` 16 / 24 Regular | 16 / 24 regular |
| 标签/时间/chip | `labelMedium` 12 / 16 Medium | 12 / 16 medium |
| 小徽标 | `labelSmall` 11 / 16 Medium | 11 / 16 medium |
| 提示条/按钮 | `labelLarge` 14 / 20 Medium | 14 / 20 medium |
| 查看器 caption | `bodySmall` 12 / 16 Regular | 12 / 16 regular |

行距 = 行高 − 字号。所有文本默认可换行；数字/时间使用固定格式（见 §8）。

### 6.3 间距与圆角

- 间距沿用 `AppSpacing`：4 / 8 / 16 / 24 / 32 / 48。
- 卡片圆角 12；缩略图/帧图 8；气泡 16（尾角 8）；chip/药丸 50%；查看器 bar 12。
- 页面水平边距 16；列表卡片外边距水平 16、垂直 4。
- 触控目标最小 44×44pt；小 chip 的视觉高度不因最小触控被撑高（用内容 padding + 外扩热区实现）。

### 6.4 星点背景

仅首页宿主使用；子页纯 Abyss。

```
starCount = clamp(Int(width * height / 9000), 24, 90)
固定种子 42 的确定性伪随机：
  x = rand * width
  y = rand * height
  r = 0.6 + rand * 1.4
  alpha = 0.10 * r
  白色圆，半径 r * 1.6
```

- SwiftUI `Canvas`（iOS 15）+ `GeometryReader`；尺寸变化时按同一种子重建。
- 静态、无动画；Reduce Motion 无影响。

### 6.5 通用状态组件

- `MylifeLoadingHint`：居中 `labelMedium` / Dust。
- `MylifeErrorHint`：居中 `bodyMedium` / Red + 描边重试按钮（Gold 文字）。
- `MylifeEmptyState`：SF Symbol + 标题（title 22）+ 可选副标题（body 16 / textSecondary）。
- 分析中脉冲点：8pt Gold 圆，透明度 0.3 ↔ 1.0，600ms 反向循环；Reduce Motion 时静态 1.0。

---

## 7. 媒体管线

### 7.1 远程图片

- 链接 vendored `apps/ios/deps/NetworkImage`（本地 SPM package，MIT，iOS 14+）。
- `MylifeImageLoader`：

```swift
let session = URLSession(
    configuration: imageConfiguration,   // URLCache 内存 10MB / 磁盘 100MB；requestCachePolicy = .returnCacheDataElseLoad
    delegate: ProbeSessionDelegate(
        allowSelfSigned: serverBase.allowsSelfSigned,
        expectedHost: serverBase.host,
        expectedPort: serverBase.port
    ),
    delegateQueue: nil
)
let loader = DefaultNetworkImageLoader(
    cache: DefaultNetworkImageCache(countLimit: 100),
    session: session
)
```

- 在 `TimelineFlowView` 根上 `.networkImageLoader(loader)`；所有子页图片共用。
- 占位/失败统一 `MylifePalette.panel`（对齐 Android `ColorPainter(Panel)`）。
- 缩略图与网格都加载完整 JPEG（BFF 无缩略图端点）；服务端 `Cache-Control: immutable`，URL 含 token。
- 不使用 SwiftUI 自带 `AsyncImage`：它无法注入自签 TLS 与统一缓存。

### 7.2 语音播放

- 服务端原始音频为 Ogg Opus；iOS 播放 URL 必须附 `format=m4a`（BFF 转成 AAC/M4A 并缓存）。
- `MylifeAudioPlayer`（`@MainActor`，`ObservableObject`）：
  - 单播状态：`key`（eventRef）、`isPlaying`、`isBuffering`、`positionMs`、`durationMs`、`error`。
  - `toggle(key:url:startAtMs:)`：异 key → 从 `startAtMs ?? 0` 播；同 key + error → 从 `startAtMs ?? 0` 重试；同 key 且正在播 → 暂停；否则续播（有 `startAtMs` 先 seek 再播）。
  - 播放方式：先用媒体下载 session（60s 超时，同一 TLS delegate）把 m4a 下载为内存 `Data`，再用 `AVAudioPlayer(data:)` 播放。**不直接给 AVPlayer 远程 URL**：自签 TLS 下 AVFoundation 不走 `URLSession` 的信任回调；也不写临时文件，会话结束由播放器释放内存。
  - 下载/缓冲期间 `isBuffering = true`；失败 → `error = L10n.t("player_play_failed")`，保留 key。
  - 播放中每 200ms 回写 position/duration；播完 pause + seek 0，保留 key、位置归零。
  - `seek(to:)` 夹取 0...duration；`stop()` 取消下载、停止播放器、释放。
  - 同一段的 `AVAudioPlayer` 在本次时光机会话内复用；关闭时光机时 `stop()` 释放。
  - 不修改 `AVAudioSession` 的 category/mode/options，只在播放前 `setActive(true)`；输出路由由系统共享会话决定（对应 Android「跟随实时会话输出设备」）。
  - 实时会话 `sessionState == .speaking` 的**上升沿**：若画廊正在播，暂停；不观察回落、不自动恢复。实时会话已在播时启动画廊不拦截（对齐 Android）。
  - 音频中断：`.began` 暂停；`.ended` 且 `shouldResume` 且中断前在播 → 恢复；其他情况不恢复。
  - App 进入后台：暂停（iOS target 未启用后台音频；与 Android「退后台继续播完」为平台差异）。
- `AudioGalleryViewModel` 持有 player 并转发 toggle/seek；`onDisappear`/弹栈/关闭时光机调用 `stop()`。

### 7.3 保存到相册

- 复用 `PhotoLibrarySaving` / `PhotoLibrarySaver`（`.addOnly`）；`NSPhotoLibraryAddUsageDescription` 已在 `Resources/Info.plist`，时光机不新增权限声明。
- 流程：`MylifeStore.downloadMedia(url)` → 校验可解码图片（`CGImageSourceCreateWithData` 成功）→ `saver.save(jpeg:)`。
- 提示文案：成功「已保存到本地相册」、失败「保存失败」；2 秒 TTL；保存中按钮转圈且忽略重复点击；关闭查看器取消在飞任务并清提示。
- iOS 无 Android 的 legacy 存储权限分支；`.addOnly` 被拒时直接失败（复用现有 iOS 语义）。

### 7.4 链接

- Agent Markdown 中的链接与普通外链统一交给 `AppContainer.presentWebLink(_:using:)`（Timeline cover 内）：
  - 当前 ServerBase 的 `/webroot/...` → cover 本地 `InAppLinkPresenter` + fullScreenCover 的 `WebPageView`（WKWebView，自签信任）；
  - 其他 http(s) → `SafariLinkPresenter` 从最顶层 UIViewController 直接 present `SFSafariViewController`；
  - 非 http(s) → 丢弃。
- 不能在 Timeline 的外层 fullScreenCover 之上再用根级 SwiftUI sheet 呈现 Safari：iOS 会在 Safari 关闭时把 cover 一起 dismiss（`docs/ios_agent_job_view.md` §7.2）。
- `MarkdownMessageView(serverBaseURL:)` 负责 `livachat_url://webroot/...` 的转换；时间线各页传入 `AppContainer.currentServerBaseURL`。

---

## 8. 本地化、日期与数字

### 8.1 语言

- 只维护 `en` 与 `zh-Hans`（当前工程事实）。
- 文案 key 与 Android 对齐；新增 key 全部写入 `Resources/Localizable.xcstrings`。
- 复数使用 xcstrings 的 plural variation；格式符用 `%lld`（Int64/Int）与 `%@`（String），不使用 Android 的 `%1$s/%1$d`。

### 8.2 日期/时间口径

- 所有 `HH:mm`、日期、周标签使用**设备当前时区**。
- 日期/星期/月份文案使用与 UI 文案一致的**有效语言**（`Bundle.main.preferredLocalizations.first`，当前 16 locale），不用原始设备 locale：设备语言不在支持列表时，文案回退英文，日期标签也必须一起回退，避免“英文界面 + 法文月份”。
- 时间固定 24 小时制 `HH:mm`（不随系统 12/24 设置改变，对齐 Android）。
- 日期标签按语言输出：
  - 日卡：zh `8月21日 · 周四`；en `Aug 21 · Thu`
  - 周卡范围：zh/en 均为 `8/10 – 8/16`（Android else 分支）
  - 周序号：zh `33周`；en `W33`
  - 月卡：zh `2026年8月`；en `Aug 2026`
  - 年卡：zh `2026年`；en `2026`
  - 月分组头：zh `2026年 · 8月`；en `Aug 2026`
  - 搜索日头：zh `8月21日`；en `Aug 21`（不含年份）
  - hotSlot/EventList 标题：zh `8月21日 09:12`；en `Aug 21 09:12`
- 首页日/周/月/年分组使用响应 `days[].date`（BFF 生效时区）；hotSlot/Gallery/Audio 的取数日、pending 归属、搜索命中本地时间使用设备时区。两套口径不同是 Android 既知行为，iOS 不修正。
- ISO 周：`Calendar(identifier: .iso8601)`，周一为一周首日；周 key `YYYY-Www`；跨年周使用 week-based year。
- `YYYY-MM-DD` 日期键是「无时区的本地日」：解析与周计算固定使用 UTC 正午或 `DateComponents`，避免在夏令时切换日出现 ±1 天偏移；不要用设备本地 `Calendar` 直接解析日期串。

### 8.3 新增文案 key

| Key | en | zh-Hans |
|---|---|---|
| `common_clear` | Clear | 清除 |
| `common_play` | Play | 播放 |
| `common_pause` | Pause | 暂停 |
| `player_play_failed` | Playback failed | 播放失败 |
| `mylife_loading` | Loading... | 加载中… |
| `mylife_analyzing` | Analyzing... | 分析中… |
| `mylife_analysis_pending` | It will appear once the analysis is done | 分析完成后会自动出现 |
| `mylife_load_failed` | Failed to load | 加载失败 |
| `mylife_no_records_in_scope` | No records in %@ | %@没有记录 |
| `mylife_no_records_yet` | No records yet — start a conversation | 还没有记录，开始一次对话吧 |
| `mylife_load_failed_retry` | Failed to load — tap to retry | 加载失败，点击重试 |
| `mylife_loading_earlier` | Loading earlier records... | 加载更早的记录… |
| `mylife_load_earlier` | Load earlier | 加载更早 |
| `mylife_reached_start` | You've reached the beginning | 已到最早 |
| `mylife_search_placeholder` | Search... | 搜索… |
| `mylife_search_min_chars` | Type at least 2 characters | 至少输入 2 个字 |
| `mylife_searching` | Searching... | 搜索中… |
| `mylife_search_no_results` | No results found | 没有找到相关结果 |
| `mylife_search_failed` | Search failed | 搜索失败 |
| `mylife_search_total` (plural) | %lld result / %lld results | 共 %lld 条结果 |
| `mylife_granularity_day` | Day | 日 |
| `mylife_granularity_week` | Week | 周 |
| `mylife_granularity_month` | Month | 月 |
| `mylife_granularity_year` | Year | 年 |
| `mylife_week_label` | W%lld | %lld周 |
| `mylife_overview_month` | %2$@ %1$lld | %1$lld年 · %2$@ |
| `mylife_scope_filter` | %@ only ✕ | 只看 %@ ✕ |
| `mylife_more_items` | +%lld | +%lld 条 |
| `mylife_agent_task` | Agent task | Agent任务 |
| `mylife_hot_slot_desc` | Active at %@ | 活跃时段 %@ |
| `mylife_refresh` | Refresh | 刷新 |
| `mylife_analyzing_content` | Analyzing content... | 内容分析中… |
| `mylife_hotslot_empty` | Nothing to show in this period | 该时段没有可展示的内容 |
| `mylife_audio_empty_title` | No audio this day | 这一天没有语音 |
| `mylife_audio_time_desc` | View events within 10 minutes of %@ | 查看 %@ 前后 10 分钟的事件 |
| `mylife_gallery_empty_title` | No photos in this period | 这段时间还没有画面 |
| `mylife_edit` | Edit | 编辑 |
| `mylife_edit_exit` | Done | 退出 |
| `mylife_max_selection` | You can select up to 50 | 最多选择 50 张 |
| `mylife_delete_timeout` | Delete timed out, please try again | 删除超时，请稍后重试 |
| `mylife_delete_failed` | Delete failed | 删除失败 |
| `mylife_deleting` | Deleting... | 删除中… |
| `mylife_delete_photos` | Delete photos | 删除照片 |
| `mylife_select_photo` | Select photo | 选择照片 |
| `mylife_delete_count` | Delete (%lld) | 删除 (%lld) |
| `mylife_delete_confirm` (plural) | Delete the %lld selected photo? / Delete the %lld selected photos? | 确定删除选中的 %lld 张照片？ |
| `mylife_photos_deleted` (plural) | Deleted %lld photo / Deleted %lld photos | 已删除 %lld 张 |
| `mylife_photos_deleted_partial` | %lld deleted, %lld failed | %lld 张已删除，%lld 张失败 |
| `mylife_frames_count` (plural) | %lld photo / %lld photos | %lld 张 |
| `mylife_analyzing_frames` (plural) | Analyzing... · %lld photo / Analyzing... · %lld photos | 分析中… · %lld 张 |
| `mylife_segments_count` (plural) | %lld segment / %lld segments | %lld 段 |
| `mylife_audio_entry_desc` (plural) | Voice recording, %lld segment / Voice recordings, %lld segments | 语音记录，%lld 段 |

复用已有：`common_time_machine`、`common_back`、`common_retry`、`common_close`、`common_cancel`、`common_delete`、`common_save_image`、`common_saved_to_gallery`、`common_save_failed`、`common_view_full_result`、`conv_msg_suffix_transcript/original/translation`、`conv_job_remaining_chars`。

---

## 9. 可访问性

- 所有图标按钮 44×44pt，带本地化 label。
- 顶栏返回：默认 `common_back`；首页搜索模式且 query 非空时语义为「清除搜索」，label 用 `common_clear`（视觉仍是返回箭头，行为见 §5.4）。
- 粒度选择器：每个分段 `.isSelected` trait 或 accessibilityValue「已选中」。
- 活跃时段 chip：`mylife_hot_slot_desc`（建议新增 key：en `Active at %@` / zh `活跃时段 %@`），点击进入热点时段。
- 语音入口：合并为单个元素，label 用 `mylife_audio_entry_desc` 复数。
- 主题 chip：label 为 topic 文本，`.isButton`。
- 缩略图/帧：label「N 张照片中的第 M 张」；数量角标合并到图片的 label 或单独「共 N 张照片」。
- 搜索命中行：合并为「类型, 时间, 片段」，`.isButton`。
- 列表 footer：文本本身可读，`.isButton`（可点时）。
- 分析中：脉冲点 `accessibilityHidden`，文案可读。
- Reduce Motion：关闭脉冲、转场、骨架动画。

### 9.1 Accessibility identifiers（供 UI 测试）

| 页面 | identifier |
|---|---|
| 首页 | `timeline_root`、`timeline_back`、`timeline_title`、`timeline_search`、`timeline_search_clear`、`timeline_granularity_day/week/month/year`、`timeline_list`、`timeline_footer`、`timeline_scope`、`timeline_card_<key>`、`timeline_hot_slot_<index>`、`timeline_audio_entry`、`timeline_image_<index>`、`timeline_image_badge`、`timeline_topic_<index>`、`timeline_search_total`、`timeline_search_hit_<index>` |
| hotSlot / EventList | `timeline_hotslot_back/title/list`、`timeline_hotslot_frame_<index>`、`timeline_eventlist_back/title/list`、`timeline_anchor_highlight`；Agent 覆盖层复用 `agent_job_result_overlay` / `agent_job_result_close` |
| Gallery | `timeline_gallery_back/title/count/edit/grid`、`timeline_gallery_photo_<frameId>`、`timeline_gallery_select_<frameId>`、`timeline_gallery_delete`、`timeline_gallery_confirm_delete`、`timeline_gallery_viewer`、`timeline_gallery_page`、`timeline_gallery_save`、`timeline_gallery_save_tip`、`timeline_gallery_caption`、`timeline_gallery_delete_tip` |
| Audio | `timeline_audio_back/title/count/list`、`timeline_audio_segment_<eventRef>`、`timeline_audio_play_<eventRef>`、`timeline_audio_progress_<eventRef>`、`timeline_audio_time_<eventRef>` |

---

## 10. 测试策略

### 10.1 单元测试（LivaChatAppTests）

- DTO 解码：缺字段、未知字段、浮点 `duration_s`、`ts_ms` 为空。
- URL 构造：token、`format=m4a`、相对路径、自签 host/port 校验。
- 窗口合并：去重、排序、游标更深、时区切换重建、定位页回第一页。
- 轮询窗口：400 天上限、无 pending 挂起、失败重试。
- 日期标签：en/zh 的日/周/月/年/分组头/标题/搜索日头；ISO 跨年周。
- 周期聚合：粒度、scope、summary 折叠上限、图片轮转、topics 权重、audioCount、未闭合组过滤。
- 搜索：350ms 防抖、<2 字符 Idle、清空退出、新 query 取消旧请求、分页、高亮。
- HotSlot 行模型：分钟桶、role 白名单、后缀、帧 chunk、锚点解析。
- HotSlot VM：追击 5 页、失败耗预算、轮询重取、覆盖层保持。
- Gallery VM：分页去重、focus、保存、编辑/删除、延迟刷新。
- Audio VM/解码：能量校验、bars 归一、正文回退、时间标签、播放状态机。
- `MylifeAudioPlayer`：用注入的 downloader + player 工厂测 toggle/ended/error/取消。

### 10.2 UI 测试（LivaChatAppUITests）

- 新增 `UITestScreen.timeline` 与 `-uiTestTimelineFixture`（home/hotSlot/gallery/audio/search）。
- 通过 `MylifeServicing` 协议注入静态 fixture；UI 测试不联网、不播放音频。
- 覆盖：入口、粒度切换、下钻/清除、footer 四态、搜索/无结果/不足 2 字、topic 点击、命中跳转、hotSlot 行/帧/Agent 覆盖层、Gallery focus/查看器/保存提示/编辑删除、Audio 播放切换/时间头跳转。
- 长按/滑动等自定义手势用 XCUITest 坐标验证。

### 10.3 真机/联调

- 本地 mock server 固定 `18080`（`docs`/现有 mock 约定）。
- 验证自签 TLS 下的 JSON、图片、m4a 音频、WebView 链接。
- 验证实时会话与画廊音频的单向暂停、音频中断、后台暂停。
- iPad 兼容模式 600pt 布局。
- 构建：`./scripts/build-ios.sh app`；测试：`xcodebuild test -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj -scheme LivaChatApp ...`。

---

## 11. 与 Android 的差异总表

| # | 项 | Android | iOS 设计 |
|---|---|---|---|
| 1 | 页面呈现 | Compose Navigation 路由压栈，系统返回键 | `fullScreenCover` + 自定义栈；顶栏返回；搜索 query 非空时返回先清 query |
| 2 | 语言 | 17 个 locale（含 th/vi）、阿拉伯数字本地化 | 17 个 locale（含 `th`/`vi`，见 `docs/ios_multilingual.md` §2.9）、阿拉伯数字本地化 |
| 3 | 图片加载 | Coil + 共享 trust-all OkHttp | vendored NetworkImage + 注入 URLSession（自签 + URLCache） |
| 4 | 语音格式 | ExoPlayer 直接播 Ogg Opus | 请求 `format=m4a`，下载本地后 AVAudioPlayer 播放；下载期间播放按钮显示缓冲转圈 |
| 5 | 语音输出设备 | 跟随实时会话并钉住 AudioTrack | 共享系统音频会话/路由，不做 per-player 钉住 |
| 6 | 后台语音 | 退后台不主动暂停，播完即停 | 退后台暂停（未启用后台音频） |
| 7 | 相册权限 | API ≤ 28 需存储权限，三种提示 | `.addOnly` 相册权限，两种提示（成功/失败） |
| 8 | Agent 结果落位 | 按点击字符偏移滚到视口 1/3 | 复用现有 iOS 覆盖层，打开在顶部；其余（折叠/全屏/链接）一致 |
| 9 | 长结果折叠 | 800 字 / 24 行边界 | 复用 iOS 现有 `AgentJobCollapse`（字符/段落边界）；视觉与交互一致 |
| 10 | 系统返回 | 搜索模式消费一次清 query | 顶栏返回在搜索模式先清 query，再关闭 |
| 11 | 旋转 | Agent 结果覆盖层临时全向 | 已实现：覆盖层组合期间解锁横竖屏，关闭恢复竖屏；其余页面保持竖屏 |
| 12 | 下拉刷新 | 无手势，错误态重试 | 与 Android 一致，不加 `.refreshable` |

> 以上差异均为平台能力或现有 iOS 行为约束，不改变用户可完成的任务与页面视觉结构；其余 UI/UX（布局、层级、文案、状态、分页、下钻、交互结果）follow Android。

---

## 12. 实施顺序与风险

### 12.1 顺序

1. `MylifeDTOs` + `MylifeAPIClient` + `MylifeStore`（窗口/轮询）+ 单测。
2. `MylifeTheme`、星点背景、通用状态组件、`TimelineFlowView`/路由栈。
3. 首页浏览（粒度/卡片/下钻/footer）+ 搜索模式。
4. topics 聚合 + 搜索结果 + EventList。
5. hotSlotView + 行渲染 + 帧查看器 + Agent 结果。
6. 图片 Gallery + 查看器 + 删除。
7. 语音 Gallery + m4a 播放器 + 能量包络。
8. UI 测试、mock/真机联调、文档回填。

### 12.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| iOS 15 无 `NavigationStack`，自定义栈状态保留 | 返回状态丢失/转场异常 | 根视图常驻；子页 ViewModel 由 flow 持有；UI 测试覆盖 |
| 自签 TLS 下 AVFoundation 不走 URLSession 信任 | 音频/图片加载失败 | 音频下载后本地播；图片改用注入 loader |
| Ogg 不支持 + ffmpeg 首次转码 | 首播延迟/失败 | 显示缓冲态；失败提示；服务端缓存 |
| 实时会话与画廊共用音频会话 | 声音叠加/路由异常 | 单向暂停；不改 category；真机矩阵验证 |
| 自定义滚动位置保持 | 返回后位置漂移 | 根视图常驻；搜索/浏览双列表独立挂载 |
| 大图完整 JPEG | 流量/内存 | 沿用服务端 immutable 缓存；后续可加缩略图端点 |
| `left|right offset` 游标在分析中位移 | 重复/漏项 | eventRef/frameId 去重；pending 轮询重取首屏 |
| overview 时区可能被服务端重定向 | 日卡与本地日不一致 | 以响应 timezone 为准；切时区重建窗口并清 scope |
| 17 locale（含 th/vi） | Android 已实施 17（含 th/vi） | 见 `docs/ios_multilingual.md` §2.9、`docs/android_multilingual.md` §21 |

---

## 13. 代码锚点

### Android（基线）

| 主题 | 位置 |
|---|---|
| 宿主/入口 | `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/mylife/MylifeHomeScreen.kt`、`ui/navigation/NavGraph.kt` |
| 首页/状态 | `.../mylife/timeline/TimelineScreen.kt`、`TimelineViewModel.kt`、`TimelineUiState.kt` |
| 聚合/日期 | `.../timeline/PeriodAggregator.kt`、`MylifeDateLabels.kt`、`components/OverviewSection.kt` |
| 卡片 | `.../timeline/components/PeriodCard.kt`、`HotSlotChips.kt`、`TopicChipRow.kt`、`AudioEntryButton.kt`、`ScopePill.kt` |
| 数据 | `.../data/repository/MylifeRepository.kt`、`data/remote/mylife/MylifeApi.kt`、`dto/MylifeDtos.kt` |
| 主题 | `.../ui/screens/mylife/MylifeTheme.kt` |
| 热点/EventList | `.../hotslot/HotSlotScreen.kt`、`EventListViewerScreen.kt`、`HotSlotViewModel.kt`、`HotSlotRows.kt`、`components/HotSlotRowItems.kt`、`HotSlotFrameViewer.kt` |
| Gallery | `.../gallery/FrameGalleryScreen.kt`、`FrameGalleryViewModel.kt`、`components/FrameGrid.kt`、`FrameViewerOverlay.kt` |
| Audio | `.../audiogallery/AudioGalleryScreen.kt`、`AudioGalleryViewModel.kt`、`AudioGalleryUiState.kt`、`components/*`、`media/AudioPlayer.kt`、`media/ExoAudioPlayer.kt` |

### BFF

| 主题 | 位置 |
|---|---|
| overview/timeline | `services/bff/timeline/router.py`、`services/bff/timeline/service.py` |
| search | `services/bff/search/router.py`、`service.py` |
| remove | `services/bff/remove/router.py` |
| media | `services/bff/common/media.py` |
| schemas | `services/bff/common/schemas.py` |
| 鉴权 | `services/bff/common/deps.py` |

### iOS（复用/改造）

| 主题 | 位置 |
|---|---|
| 组合根/配置 | `apps/ios/LivaChatApp/App/AppContainer.swift`、`Core/Settings/SettingsStore.swift` |
| 路由 | `App/AppRouter.swift`、`App/AppRootView.swift` |
| HTTP/TLS | `Core/Conversation/AgentJobCancelClient.swift`、`Core/Settings/ServerProbe.swift` |
| Markdown/链接 | `Features/Conversation/MarkdownMessageView.swift`、`MarkdownTheme.swift`、`Core/PlatformAdapters/WebLinkRouter.swift` |
| Agent 结果/折叠 | `Features/Conversation/AgentJobResultOverlay.swift`、`AgentJobCollapse.swift` |
| 查看器数学 | `Features/Conversation/ImageViewerOverlay.swift`（`ImageViewerMath`） |
| 保存相册 | `Core/PlatformAdapters/PhotoLibrarySaver.swift` |
| 主题/本地化 | `Core/UI/AppTheme.swift`、`Resources/Localizable.xcstrings` |
| 网络图片包 | `apps/ios/deps/NetworkImage/`（尚未链接） |
