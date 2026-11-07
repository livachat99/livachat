# iOS 时光机语音画廊（日卡入口 + AudioGallery + 能量包络）设计

**状态**：已实现（2026-10-22）
**UI/UX 基线**：`docs/android_timeline_audio_gallery.md` 与 Android 现行代码（`AudioGalleryScreen.kt`、`AudioGalleryViewModel.kt`、`AudioGalleryUiState.kt`、`AudioSegmentCard.kt`、`AudioTimeHeader.kt`、`SegmentProgressBar.kt`、`ExoAudioPlayer.kt`）。
**共享基础**：`docs/ios_timeline_arch.md`。
**范围**：日卡语音入口、AudioGallery 屏、语音段播放器、时间头跳转、进度条能量包络、BFF `audio_summary` 与 `format=m4a` 回退。
**不在范围**：音频写入/分析管线、静音判定、能量回填脚本、实时会话 TTS 播放（`Core/Audio/`）。

---

## 1. 功能总览

### 1.1 日卡入口

- 仅 **DAY 粒度**日卡显示；段数来自 overview `counts["audio"]`，周/月/年卡恒为 0。
- 当日有音频且存在点击回调时，在 hotSlot chips **同一行右侧**显示入口：波形图标 + 段数。
- 当日没有 hotSlot 但音频段数 > 0 时，仍渲染该行（左侧留空，入口靠右）。
- 点击进入 **AudioGallery**，路由携带：
  - `date`：日卡 key（overview 生效时区的 `YYYY-MM-DD`）；
  - `label`：日卡范围文案；
  - `count`：首屏前的段数快照；
  - `pending`：顶栏「分析中…」初值。

### 1.2 AudioGallery

- 该本地日的音频段按 `ts_ms` 升序单流展示。
- 每段一个居中时间头（`HH:mm` + `››`），下方是段卡片。
- 卡片结构：播放条（播放/暂停 + 进度条 + 时码）→ 正文单区。
- 正文规则：`transcript` 长度 > 5 显示转写，否则显示 `audio_summary`；两者皆无则不渲染正文。
- 顶栏：返回、日期 label、`N 段`、`分析中…`（pending）。
- 分页：每页 40，cursor 翻页，按 `eventRef` 去重。

### 1.3 播放

- 全局单播：同一时刻只有一段在播；播放态由单个 `MylifeAudioPlayer` 持有。
- 点击某段播放按钮：
  - 同段 → 暂停/续播；
  - 异段 → 切段并从 0 播放；
  - 播完 → 停在 0，保留该段为当前段。
- 进度条：拖动时只更新本地位置，松手 seek；点击轨道定位。
- 任意有效时长 > 0 的卡片轨道都可拖动/点击：非当前段先切换并开始播放，再 seek；当前段只 seek，不改变播放状态。
- 离开 AudioGallery（弹栈/关闭时光机）→ `stop()` 并释放播放器。
- 打开 eventListViewer 不销毁画廊栈项，返回后播放与滚动位置保留。
- 实时会话开始播放时，已处于播放态的画廊自动暂停（单向）。
- 播放输出跟随系统共享音频会话路由（对应 Android「跟随实时会话输出设备」）。

### 1.4 时间头跳转

- 点击某个段的时间头 → eventListViewer，窗口 = 该段 `ts_ms ± 10 分钟`。
- 跳转携带画廊已收敛的 `pending`。
- 返回回到画廊，列表位置保持。
- eventListViewer 只展示五类可展示事件，不包含 audio 本身。

---

## 2. 数据面

### 2.1 取数端点

AudioGallery 复用 `/timeline`：

```
GET /api/mylife/timeline
  ?types=audio
  &date_from=<date>&date_to=<date>
  &limit=40&cursor=<opaque>&timezone=<device>&token=<wire>
```

- `date_from` / `date_to` 为同一本地日闭区间。
- 事件按 `ts_ms` 升序返回。
- 响应提供 `total` 与 `next_cursor`；客户端逐页追加。

### 2.2 事件字段与客户端映射

| wire 字段 | 含义 | iOS 映射 |
|---|---|---|
| `ts_ms` | 事件时刻 | `AudioSegment.tsMs` |
| `event_ref` | `{rsession_id}:audio:{segment_id}`，唯一 | `eventRef`；列表 key、去重键、播放器身份 |
| `media_url` | `/api/mylife/media/{device}/{session}/audio/{segment_id}.ogg` | 绝对化 + token + `format=m4a`；Store 出口即可直接播放 |
| `transcript` | payload 转写（可能空串） | 空白 → nil |
| `audio_summary` | payload 的 LLM 摘要（BFF 新增字段） | 空白 → nil → `summary` |
| `duration_s` | 段时长（秒） | `Double?` × 1000 → `durationMs` |
| `energy` / `energy_v` / `frame_ms` / `db_min` / `db_max` | 能量包络量化数据 | 解码为 `EnergyEnvelope?` |

容错：

- `mediaUrl` 或 `eventRef` 缺失/空 → 丢弃该条。
- `duration_s` 必须用 `Double?` 解析：写入侧 `{**parsed}` 可能让 LLM 输出覆盖为浮点，用 `Int` 会整页解析失败。
- 能量字段不完整、`energy_v != 1`、base64 非法、空串、`db_max <= db_min` → `energy = nil`，降级为纯轨道。
- 页内按 `tsMs` 稳定升序。

### 2.3 BFF 的 `audio_summary`

- `events.summary` 列是检索拼接串：`transcript + " " + LLM 摘要 + " " + 情绪名`，同时是 FTS 索引源。
- BFF 在 audio 事件上从原始 payload 取 `summary`，输出为 `audio_summary`；其他类型不输出该字段。
- wire 上的 `summary` 保持列拼接值，供检索/旧消费方使用；iOS 的段正文只使用 `transcript` / `audio_summary`，不直接把 `summary` 当展示摘要。

### 2.4 媒体端点与 m4a 回退

```
GET /api/mylife/media/{device}/{session}/audio/{filename}.ogg
  ?token=<wire>
  &format=m4a
```

- 默认返回 `audio/ogg`；`format=m4a` 时 BFF 用 ffmpeg 转为 AAC/M4A（`audio/mp4`）并缓存到 `<data_root>/_cache/m4a/`。
- 响应带 `Cache-Control: public, max-age=31536000, immutable`；支持 Range（m4a 文件）。
- **iOS 必须使用 `format=m4a`**：AVFoundation 不支持 Ogg 容器；直接播 `.ogg` 会失败。
- 首次转码可能带来额外延迟；播放器显示缓冲态。

### 2.5 计数口径

- `audioCount` 来自 overview 的 `counts["audio"]`（day_stats 预计算，全设备、全类型原始计数）。
- `/timeline` 仍按设备白名单过滤，且 overview/timeline 的时区解析规则不同。
- 因此入口数字是提示值，不保证等于画廊 `total`；画廊顶栏在首屏响应后切换为响应 `total`。
- 事件缺少 `media_url`/`event_ref` 会被客户端丢弃，实际可播放段数可能少于 `total`。
- 极端情况下入口存在但画廊为空：显示空态。

---

## 3. 日卡入口

### 3.1 聚合

```swift
audioCount = granularity == .day
    ? days.reduce(0) { $0 + ($1.counts["audio"] ?? 0) }
    : 0
```

### 3.2 布局

- 行渲染条件：`hotSlots.isNotEmpty() || (audioCount > 0 && onAudioClick != nil)`。
- `HStack(alignment: .top)`：有 chips 时 `HotSlotChips` 占满剩余宽度；否则 `Spacer`；入口固定右侧。
- 入口：
  - 圆形 Panel 80% 底 + 18pt Gold 波形图标（Android `GraphicEq`，iOS 用 SF Symbol `waveform`）；
  - 段数 Dust `labelMedium`，图标右侧 4pt；
  - 整行可点；无障碍合并为单个元素，label 用 `mylife_audio_entry_desc` 复数（「语音记录，N 段」）。
- 点击 → `TimelineRoute.audio(date: digest.key, label: digest.rangeLabel, count: digest.audioCount, pending: digest.hasPending)`。

---

## 4. AudioGallery 屏

### 4.1 状态

```swift
enum AudioGalleryState: Equatable {
    case loading
    case empty
    case error(message: String)
    case data(segments: [AudioSegment], total: Int, nextCursor: String?, loadingMore: Bool)
}

struct AudioGalleryUiState {
    var state: AudioGalleryState = .loading
    var headerCount: Int = 0        // 首屏前 = 路由 count；成功后 = 响应 total
    var pending: Bool                // 单向收敛
    var player: MylifeAudioPlayer.State
    var scrubMs: Int64?              // 正在拖动的位置
    var scrubRef: String?            // 正在拖动的段（只有该卡跟随）
}
```

- 顶栏：
  - 返回（Dust，44）；
  - label（Gold，16/24 medium，单行省略）；
  - pending 时「分析中…」（Gold 80%，12/16）；
  - 段数（Dust，12/16）：首屏成功前用路由快照，成功后用响应 `total`，loadMore 也刷新 total。
- 主体：
  - loading：居中「加载中…」；
  - empty：图标 +「这一天没有语音」+「分析完成后会自动出现」；
  - error：错误文案 + 重试；
  - data：`LazyColumn`（SwiftUI `ScrollView + LazyVStack`）按 `eventRef` 作为 item id。

### 4.2 ViewModel

- `load(date, countSnapshot, pending)`：
  - 同日期已成功加载且 Data → 跳过（不重置 pending 收敛）；
  - 否则记录日期、设置 pending/headerCount、重拉第一页。
- `retry()`：不经过日期跳过逻辑，直接重拉第一页。
- `reloadFirstPage()`：
  - 取消在飞加载；
  - 已有 Data 时不回落 Loading；
  - 首屏失败才进入 Error；
  - replace 语义：清空列表并重建去重集合。
- `loadMore()`：`nextCursor` 为空或正在加载 → no-op；失败静默保留已加载页。
- `applyPage`：
  - replace 清空并重建 `seenRefs`；
  - append 按 `eventRef` 去重追加；
  - 每次更新 `headerCount = response.total`。
- overview 轮询：
  - 已加载且 `player.key == nil`（从未播放，或已 stop）→ 重拉第一页；
  - **只要 `key != nil`（播放、暂停、或播完保留的当前段）就不重取**：重取会替换为首页，若当前段在后续页，卡片会从列表消失；
  - pending 为 true 且 `store.hasPendingAnalysis() == false` → 单向收敛 false。
- 弹栈/关闭时光机：`player.stop()`。

### 4.3 列表与分页

- 每段一个时间头 + 卡片；item id = `eventRef`。
- 时间标签：**每段一个 `HH:mm`**，不去重；由设备时区与 UI locale 生成。
- 触底条件：最后一个可见 item 索引 ≥ `segments.count - 4`。
- 防重试风暴：每个新触发只发一次；失败后需滚离阈值区再回来，或上一页成功追加后才重新触发。
- 加载中底部显示整行加载提示。
- 列表滚动与播放状态在进入 EventList 后保留（栈项不销毁）。
- 底部安全区：给列表加底部 content inset，避免末卡被 Home Indicator 遮挡（Android 当前缺此留白，iOS 应补）。

### 4.4 段卡片

- 外层 Panel 60%，圆角 12；外边距水平 16 / 垂直 4；内边距 16。
- 播放条行：
  - 圆形播放/暂停按钮：Panel 90% 底，22pt Gold 图标；当前段 `isBuffering` 时图标换 18pt 转圈（下载/首次 m4a 转码的必要反馈；Android 由 ExoPlayer 内部缓冲，卡片没有此态）；
  - 进度条：占满剩余宽度，高 40；
  - 时码：`labelMedium` Dust；
    - 当前段且有效时长 > 0：`当前 / 总长`（播放器时长优先，未就绪回落 `duration_s`）；
    - 其他：只显示总长。
  - 时码格式 `M:SS`；负值/未知按 0。
- 正文单区：`bodyMedium`（14/20）Star；`transcript` 长度 > 5 用转写，否则 `audio_summary`，皆无则不渲染。
- 当前段 `error != nil` 时，播放条下方显示 Red `bodySmall`「播放失败」。
- 播放按钮无障碍 label 随状态「播放」/「暂停」。
- 进度条不暴露滑块语义（仅按钮有语义）。

### 4.5 时间头

- 居中 HStack；内层圆角药丸：Panel 80%，时间 Dust、`››` Gold，均 `labelMedium`。
- 外层触控带 ≥ 32pt 高，水平 padding 4 计入触控区；视觉药丸尺寸不被撑大。
- 可点时 `.isButton`，`accessibilityLabel = mylife_audio_time_desc(label)`（「查看 HH:mm 前后 10 分钟的事件」）。
- 点击 → `TimelineRoute.eventList(tsMs: segment.tsMs, pending: uiState.pending)`。
- 返回后画廊栈项保留，播放/滚动不变。

---

## 5. 播放器设计（iOS）

### 5.1 为什么不能直接播 Ogg

- AVFoundation/AVPlayer/AVAudioPlayer 不支持 Ogg 容器；`media_url` 原始为 `.ogg`。
- 必须请求 `format=m4a`，由 BFF 转码为 AAC/M4A。
- 自签 TLS 下 AVFoundation 不走 `URLSession` 的自签信任回调，因此不能把远程 URL 直接交给 AVPlayer；先下载到本地再播。

### 5.2 `MylifeAudioPlayer`

```swift
@MainActor
final class MylifeAudioPlayer: NSObject, ObservableObject {
    struct State: Equatable {
        var key: String?          // eventRef
        var isPlaying: Bool
        var isBuffering: Bool
        var positionMs: Int64
        var durationMs: Int64
        var error: String?        // 本地化「播放失败」
    }

    func toggle(key: String, url: URL, startAtMs: Int64? = nil)
    func seek(to ms: Int64)
    func stop()
}
```

- 状态机（与 Android `decideToggle` 一致）：
  - `state.key != key` → 从头播放（新段）；`startAtMs` 非空时先定位到该位置再播；
  - 同 key 且 `error != nil` → 清错误、从 `startAtMs ?? 0` 重试；
  - 同 key 且正在播 → 暂停；
  - 其余 → 续播；`startAtMs` 非空时先 seek 再播；
  - 缓冲中的同段再次点击不取消下载（等价 `play()` no-op）。
- 播放实现：
  1. 切段时先停止当前播放（引擎节点或 `AVAudioPlayer`）并取消上一段的在飞下载；
  2. `isBuffering = true`，`state.key = key`；
  3. `MylifeStore.downloadMedia(url)` 下载 m4a 为内存 `Data`；
  4. 播放后端按实时会话是否在运行二选一（见 5.4）：
     - **会话引擎可用**：m4a 写临时文件 → `AVAudioFile` → 独立 `AVAudioPlayerNode` 调度到会话 `AVAudioEngine.mainMixerNode`，与 TTS 共用 voice-processing 输出；
     - **无实时会话**：`AVAudioPlayer(data:)` + `.playback` 会话；
  5. 应用定位（优先 `startAtMs`，否则 `pendingSeekMs`）→ `play()`；
  6. 回填 `durationMs`，`isBuffering = false`；引擎路径位置由 `playerTime(forNodeTime:)` 换算。
- 缓冲中的同段 `seek` 没有可操作的 player：记录 `pendingSeekMs`，下载完成、player 就绪后先定位再播放；切段时清空。
- 下载失败/转码失败/解码失败 → `error = L10n.t("player_play_failed")`，保留 key，停止计时。
- 播放中每 200ms 更新 `positionMs` 与 `durationMs`。
- 播完：`pause()` + `currentTime = 0`，`isPlaying = false`，`positionMs = 0`，保留 key。
- `seek(to:)`：夹取 `0...duration`；暂停态 seek 不自动播放（引擎路径重新调度剩余段并保持暂停）。定位到末尾（≥ 实际解码时长）时直接进入播完态，不会留下「播放中但无声」。
- 引擎路径恢复（暂停→播放、中断恢复）按暂停位置重新调度，不依赖引擎 stop/start 后节点是否保留旧调度。
- 引擎运行中丢失（后台/路由变化/会话结束导致 engine 停止）：200ms ticker 检测后置为暂停并保留最后位置；下次播放重新走引擎或 AVAudioPlayer 回退。
- 回退 `AVAudioPlayer` 播放期间实时引擎变为可用：ticker 在一次 tick 内切回引擎，避免 voice-processing 会话把独立播放器静音。
- `stop()`：取消下载、停止两种后端、删除临时文件、把画廊节点从引擎 detach、清空状态；关闭时光机时释放内存。
- 下载超时 60 秒；失败不自动重试（用户再点播放即重试）。

### 5.3 实时会话互斥

- AppContainer 暴露 `realtimeSpeaking: AnyPublisher<Bool, Never>`（`sessionState == .speaking`，去重）。
- 播放器订阅：
  - 上升沿为 true 且自己 `isPlaying == true` → `pause()`；
  - 不观察回落、不自动恢复；
  - 实时会话已在播时启动画廊 → 不拦截（与 Android 一致）；
  - 画廊在缓冲/起播阶段 AI 开口 → 不做额外拦截（Android 同样依赖系统音频焦点）。
- iOS 没有 Android 的 per-player 音频焦点；反向（AI 开口打断画廊后自动续播）不实现自动恢复。

### 5.4 音频会话、路由与中断

- **实时会话在运行时不修改** `AVAudioSession` 的 category/mode/options。`.playAndRecord` + `.voiceChat` + voice-processing I/O 下，独立 `AVAudioPlayer` 实测完全静音；重复 `setCategory` 会触发 route change，实时协调器随即重启采集/播放（实测播放约 1 秒后卡死），因此不可行。
- 画廊因此把音频调度到实时会话的 `AVAudioEngine`：独立 `AVAudioPlayerNode` → `mainMixerNode`，与 TTS 同一条输出链，音量与可听性一致。节点在 `TimelineFlowModel` 首次播放时 attach/connect，离开画廊时 detach。
- 无实时会话（引擎不可用）→ `AVAudioPlayer` + `.playback`；播放前 `setActive(true)`，不触发静音开关。
- 输出路由由共享会话决定（扬声器/耳机/蓝牙）；不做 per-player 设备钉住。
- 中断通知：
  - `.began` → 暂停（两种后端）；
  - `.ended` 且 `shouldResume` 且中断前在播 → 恢复；
  - 其他 → 保持暂停。
- 媒体服务重置/引擎重建：播放器每次起播通过 provider 解析当前实时引擎；引擎实例变化时先从旧引擎 detach，再在新引擎上 attach/connect 并重新调度。
- provider 是惰性解析的：时光机在 runtime 尚未起完时打开，也会在首次播放时拿到此时运行的引擎，不会永久退回静音路径。
- App 进入后台：`TimelineFlowView` 监听 `scenePhase`，`background` 时暂停画廊（与 Android「退后台播完即停」为平台差异；iOS 未启用后台音频）。

### 5.5 生命周期

- 播放器由 `TimelineFlowModel` 持有，整个时光机会话唯一。
- 关闭时光机 → `stop()`，释放下载数据与播放器。
- AudioGallery 弹栈 → `stop()`（对齐 Android `onCleared`）。
- 进入 EventList 不弹栈 → 播放继续。
- 轮询重取在 `player.key != nil`（播放/暂停/播完保留的当前段）时跳过，避免列表替换导致当前段消失。

---

## 6. 进度条与能量包络

### 6.1 能量解码

`TimelineEvent` → `EnergyEnvelope`：

- 要求 `energy_v == 1`、`energy` 非空、`frame_ms` / `db_min` / `db_max` 存在；
- base64 解码成功且非空；`db_max > db_min`；
- 任一不满足 → `nil`（纯轨道）。

### 6.2 柱状包络

- 高度 40；柱宽槽约 1.5pt；条数 = `clamp(Int(width / 1.5), 1...512)`。
- 第 b 条取对应帧区间的 **max**（保起音）；索引按 `values.count` 比例映射，不假设 `values.count == durationMs / 20`。
- 归一化：
  - `peak = 各条峰值 max`；
  - `floor = max(peak - 40, dbMin)`；
  - 高度 = `(value - floor) / (peak - floor)`，clamp 0...1。
- 全部为 0（平直/极安静段）→ 返回空数组，降级纯轨道。
- 绘制：
  - 以中线为轴上下对称的圆角柱；柱宽 = 槽位 × 62%；已播 Gold，未播 Line；
  - 不画中心水平轨道线；
  - 滑块：7pt Gold 圆点 + 1.5pt Abyss 描边。
- 无能量数据：4pt 纯轨道（Line 底 + Gold 已播）+ 滑块。

### 6.3 手势

- 高 40 的触控带；横向位移超过 touch slop 才接管并回调 scrub；纵向未过 slop 时交给列表滚动。
- 父级（列表）已消费 → 本轮作废，不 seek。
- 松手/点击（无位移）→ `onScrubEnd`。
- 拖动中只更新被拖卡片的 `scrubMs`，不显示播放器位置回写。
- 非当前段轨道也可交互（有效时长 > 0）：
  - `onScrubEnd`：若 `player.key != segment.eventRef`，调用 `toggle(key:url:startAtMs: ms)`——由于 iOS 是“下载后本地播放”，**必须把目标位置带入启动流程**，在下载完成、`AVAudioPlayer` 就绪后先 `currentTime = ms/1000` 再 `play()`；不能先 `toggle` 再立刻 `seek`（下载未完成时 seek 会丢失）；
  - 当前段只 seek，不改变播放状态。
- `durationMs == 0` 的段不挂手势。
- iOS 实现建议：单个 `DragGesture(minimumDistance: 0)` + 方向判定；用 `simultaneousGesture` 与列表滚动共存；回调通过 `@State`/最新闭包避免陈旧捕获。

---

## 7. 边界与已知限制

| 项 | 说明 |
|---|---|
| Ogg 不支持 | iOS 必须 `format=m4a`；首次转码有延迟；BFF ffmpeg 不可用则播放失败 |
| 计数口径 | `audioCount` 来自 overview counts；`/timeline` 有设备白名单；画廊以 `total` 为准 |
| 入口存在但画廊为空 | 缺少 `media_url`/`event_ref` 的事件被丢弃；空态兜底 |
| 时区切换 | 日卡 key 来自 overview 生效时区；画廊按设备时区重算日窗口，旅行改时区可能对应不同一天 |
| EventList 不含语音 | 时间头跳转的 ±10 分钟窗口只展示五类可展示事件 |
| 重取行为 | `player.key == nil` 时 overview 更新会重拉首页并替换列表；存在当前段（播放/暂停/播完）时不重取 |
| 实时互斥时序 | 只在画廊 `isPlaying` 时接收实时会话上升沿；起播/缓冲期间无代码级拦截 |
| 播放缓存 | 每次开始播放下载一次 m4a 为内存 Data；同段在会话内复用当前 player；关闭时光机释放 |
| 后台播放 | 不注册后台音频；退后台暂停 |
| 输出设备 | 跟随系统路由；不钉住实时会话 AudioTrack |
| 能量体积 | 60 秒段约 +2.5KB gzip；超长段应改为独立文件按需加载（服务端现状为内嵌） |
| `duration_s` | 非数值同名键会导致整页解析失败（可重试）；`Double?` 只解决浮点 |
| 正文阈值 | Android 用 UTF-16 `length > 5`；iOS 用 Swift `count > 5`，emoji 等边缘有差异 |
| 音频会话 | 与实时会话共用 `.playAndRecord/.voiceChat`；音质/路由受会话配置影响，需真机验证 |
| 真机待验证 | m4a 首次转码延迟、拖动 seek、断网错误、实时会话双向音频焦点、蓝牙/耳机路由、自签 TLS |

---

## 8. 本地化与可访问性

- key：
  - `mylife_loading`、`mylife_load_failed`、`mylife_analyzing`、`mylife_analysis_pending`、
  - `mylife_audio_empty_title`、`mylife_audio_time_desc`、`mylife_segments_count`（复数）、
  - `mylife_audio_entry_desc`（复数）、
  - `common_play`、`common_pause`、`player_play_failed`、`common_retry`。
- 时间标签 `HH:mm`、时码 `M:SS`、顶栏段数均按 UI locale 输出；设备时区。
- 无障碍：
  - 播放按钮 label 随状态「播放」/「暂停」；
  - 时间头整组可读，label 为「查看 HH:mm 前后 10 分钟的事件」；
  - 段卡片正文独立可读；
  - 进度条不暴露滑块 role（仅播放按钮有语义）；
  - 顶栏段数可读。

---

## 9. 测试计划

### 9.1 单元

- 时间标签：每段一个、同分钟不去重、一一对应、跨小时、空列表。
- 正文：转写 >5 用转写、≤5 回退摘要、皆无 nil。
- 能量：解码成功；版本/字段/非法 base64/空/区间非法四态降级。
- bars：窗口 max、峰值归一、floor 绝对下界、全零降级、帧数不整除、空包络、帧数少于条数。
- Store：audioSegments 映射（跳空、升序、绝对化、`format=m4a` URL）、duration 浮点、去重。
- ViewModel：首屏/分页追加去重、失败保留、pending 收敛、存在当前段时不重取、toggle/seek/scrub、弹栈 stop。
- Player：decideToggle 四态、播完归零、错误重试、切段停止旧播放并取消下载、下载失败、seek 夹取；切段带 `startAtMs` 时下载完成即从目标位置起播（不是 0）；缓冲中的同段 seek 走 `pendingSeekMs` 在就绪后应用。
- 实时互斥：上升沿暂停、不恢复；实时已在播时起播不拦截。

### 9.2 UI

- 日卡入口渲染/点击/0 段隐藏/无 hotSlot 日仍出行。
- AudioGallery：段渲染、正文三态、逐段标签、空态、触底失败不重试风暴。
- 播放：点按切换、进度条拖动/点击、非当前段起播、时码、错误行。
- 时间头跳转与 32pt 触控带。
- 能量包络渲染与无能量降级。

### 9.3 真机

- 自签 TLS 下 m4a 下载/播放/seek。
- 实时会话说话时画廊暂停。
- 耳机/蓝牙路由、中断（来电）、后台暂停。
- 长段（60 秒以上）与弱网缓冲。

---

## 10. 代码锚点

### Android

| 文件 | 职责 |
|---|---|
| `.../audiogallery/AudioGalleryScreen.kt` | 屏、顶栏、状态分支、触底 |
| `AudioGalleryViewModel.kt` | 首屏/分页/去重/重试/播放转发/离屏停止 |
| `AudioGalleryUiState.kt` | 状态模型、时间标签、正文、时码、能量 bars |
| `components/AudioSegmentCard.kt` | 段卡片 |
| `components/AudioTimeHeader.kt` | 时间头 |
| `components/SegmentProgressBar.kt` | 进度条/能量柱/手势 |
| `media/AudioPlayer.kt`、`media/ExoAudioPlayer.kt` | 播放器接口/实现 |
| `.../timeline/PeriodAggregator.kt` | `audioCount` |
| `.../timeline/components/AudioEntryButton.kt`、`PeriodCard.kt` | 日卡入口 |
| `.../timeline/TimelineViewModel.kt`、`TimelineScreen.kt` | 导航 |
| `services/bff/common/media.py` | `format=m4a` 转码 |
| `services/bff/timeline/service.py` | `audio_summary` 透出 |

### iOS（拟）

- `Core/Mylife/MylifeStore.swift`（audioSegments、downloadMedia、m4a URL）
- `Core/Audio/MylifeAudioPlayer.swift`
- `Features/Timeline/Audio/AudioGalleryView.swift`、`AudioGalleryViewModel.swift`、`AudioSegmentCard.swift`、`SegmentProgressBar.swift`
- `Features/Timeline/Components/AudioEntryButton.swift`
- `App/AppContainer.swift`（realtimeSpeaking publisher、Store 装配）
- `Resources/Localizable.xcstrings`
