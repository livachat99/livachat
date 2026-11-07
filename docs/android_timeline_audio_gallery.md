# Android MyLife 语音画廊（日卡入口 + AudioGallery + 能量包络）

> **状态**：已实施（真机手测与输出设备路由矩阵待验证，见 §6）
> **范围**：BFF `/timeline` 的 audio 摘要增量字段、Android 日卡音频入口、AudioGallery 屏、语音段播放器、时间标签跳转、进度条能量包络、存量能量回填
> **关联文档**：`docs/android_timeline_hotslot.md`（日卡 chips / 子屏范式）、`docs/android_timeline_image_gallery.md`（`/timeline` 取数与全屏路由先例）、`docs/android_timeline_topics.md`（eventListViewer）、`docs/data/lvdata_audio_analysis.md`（audio payload）、`docs/android_audio_output_devices.md §6`（输出设备策略）

---

## 1. 功能总览

### 1.1 日卡入口

- 仅 **DAY 粒度**日卡显示。段数来自 overview 的 `counts["audio"]`，周/月/年卡恒为 0。
- 当日有音频且注入了点击回调时，在 hotSlot chips **同一行右侧**显示入口：`GraphicEq` 图标 + 段数。
- 当日没有 hotSlot 但音频段数 > 0 时，仍渲染该行（左侧留空，入口靠右）。
- 点击进入 **AudioGallery**，路由携带 `date`（日卡 key）、`label`、`count`（首屏前段数快照）、`pending`（顶栏「分析中…」初值）。

### 1.2 AudioGallery

- 该本地日的音频段按 `ts_ms` 升序单流展示。
- 每段一个居中时间头（`HH:mm` + `››`），时间头下方是段卡片。
- 卡片结构：播放条（播放/暂停按钮 + 进度条 + 时码）→ 正文单区。
- 正文规则：`transcript` 长度 > 5 显示转写，否则显示 `audio_summary`；两者皆无则不渲染正文。
- 顶栏：返回、日期 label、`N 段`、`分析中…`（pending）。
- 分页：每页 40，cursor 翻页，按 `eventRef` 去重。

### 1.3 播放

- 全局单播：同一时刻只有一段在播，播放态由单例播放器统一持有。
- 点击某段播放按钮：同段 → 暂停/续播；异段 → 切段并从 0 播放；播完 → 停在 0 并保留该段为当前段。
- 进度条：拖动时只更新本地位置，松手 seek；点击轨道定位。
- **任意有效时长 > 0 的卡片轨道都可拖动/点击**：非当前段会先切换为该段并开始播放，再 seek；当前段只 seek，不改变播放状态。`durationMs == 0` 的段不挂手势。
- 离开 AudioGallery（backstack entry 销毁）→ `stop()` 并释放播放器。
- 打开 eventListViewer 不会销毁画廊 entry，返回后播放与滚动位置保留。
- 实时会话开始播放时，**已处于播放态**的画廊会被自动暂停（单向）；画廊在起播/缓冲阶段，或实时会话已在播时启动画廊，没有额外的启动拦截，是否叠加取决于系统音频焦点（真机待验证）。画廊在播时 AI 开口由系统音频焦点暂停，恢复预期由 media3 默认焦点策略处理（真机待验证）。
- 播放输出设备跟随实时会话当前输出设备（策略 C）。

### 1.4 时间头跳转

- 点击某个段的时间头 → `eventListViewer`，窗口 = 该段 `ts_ms ± 10 分钟`。
- 跳转携带画廊已收敛的 `pending`。
- 返回键回到画廊，列表位置由 LazyColumn key/rememberLazyListState 保持。
- eventListViewer 只展示 5 类可展示事件（chat/agent_job/video/transcribe/translate），**不包含 audio 本身**。

---

## 2. 数据面

### 2.1 取数端点

AudioGallery 直接复用 `/timeline`：

```
GET /api/mylife/timeline
    ?types=audio
    &date_from={date}
    &date_to={date}
    &limit=40
    &cursor={next_cursor}
    &timezone={设备当前时区}
```

- `date_from` / `date_to` 为同一本地日闭区间。
- 事件按 `ts_ms` 升序返回（`ORDER BY ts_ms, 优先级, event_id`）。
- 响应提供 `total` 与 `next_cursor`；客户端逐页追加，按 `event_ref` 去重。

### 2.2 事件字段与客户端映射

`/timeline` 的 audio 事件在 wire 上可用字段：

| 字段 | 含义 | 客户端映射 |
|---|---|---|
| `ts_ms` | 事件时刻 | `AudioSegment.tsMs` |
| `event_ref` | 事件唯一键（`{rsession_id}:audio:{segment_id}`，DB 唯一约束） | `AudioSegment.eventRef`；列表 key、去重键、播放器身份 |
| `media_url` | `/api/mylife/media/{device}/{session}/audio/{segment_id}.ogg` | 绝对化并附 `?token=` → `AudioSegment.mediaUrl` |
| `transcript` | payload 转写（可能为空串） | 空白 → `null` |
| `audio_summary` | payload 的 LLM 摘要（BFF 新增字段） | 空白 → `null` → `AudioSegment.summary` |
| `duration_s` | 段时长（秒，实测 ogg 时长优先） | ×1000 取 `AudioSegment.durationMs` |
| `energy` / `energy_v` / `frame_ms` / `db_min` / `db_max` | 能量包络量化数据 | 解码为 `AudioSegment.energy: EnergyEnvelope?` |

客户端出口容错：

- `mediaUrl` 或 `eventRef` 缺失/空 → 丢弃该条（`mapNotNull`）。
- `duration_s` 使用 `Double?` 接收：写入侧 `{**parsed}` 可能让 LLM 输出覆盖为浮点，用 `Int` 会整包解析失败。
- 能量字段不完整、`energy_v != 1`、base64 非法、空串、`db_max <= db_min` → `energy = null`，渲染降级为纯轨道。

### 2.3 BFF 的 `audio_summary`

`events.summary` 列是检索拼接串：`transcript + " " + LLM 摘要 + " " + 情绪名`，且是 FTS 索引源。payload 中的 `summary` 会被同名列遮蔽，不能直接作为展示摘要。

BFF 出口的处理：

- `LvdataStore._event_out` 在列优先扁平化后，把原始 payload dict 以私有键 `payload_dict` 挂到事件对象上。
- `_to_timeline_event` 构造 wire dict 时剥掉 `payload_dict`（永不上 wire）。
- audio 事件若 payload 的 `summary` 为非空字符串，输出为 `payload["audio_summary"]`；其他类型不输出该字段。
- wire 上的 `summary` 保持列拼接值，供检索/旧消费方使用。

### 2.4 日卡段数与画廊 total

- `PeriodAggregator` 只对 DAY 粒度取 `counts["audio"]`，写入 `PeriodDigest.audioCount`。
- `counts` 由 lvdata-mcp 的 `day_stats` 预计算：`derive_counts` 按 events 类型逐条计数（全设备、全类型、原样计数）。BFF overview 的 counts 直接取 `day_stats.counts`，不再逐事件现算，也不再应用 `device_id`。
- `/timeline` 仍会按 `device_id` / `bff.device_uuids` 应用设备白名单。
- overview 与 timeline 的生效时区解析规则不同：overview 在请求时区不是「最新时区」时会改用最新时区，timeline 使用请求时区（未登记时回落默认时区）。
- 因此 `audioCount` 是入口提示值，**不保证在所有部署下等于画廊 total**：多设备白名单或多时区会使服务端 `total` 与 `counts` 不一致；事件缺少 `media_url` 不会改变服务端 `total`，但客户端会丢弃该条，导致实际可播放段数少于 `total`。画廊顶栏在首屏响应后切换为响应 `total`，以服务端实际返回为准。
- 极端情况下入口存在但画廊为空：`media_url`/`event_ref` 在客户端被丢弃或页面为空时，画廊显示空态。

### 2.5 媒体端点

```
GET /api/mylife/media/{device}/{session}/kind/{filename}
```

- `kind` 只接受 `audio` / `video` / `image`；audio 返回 `audio/ogg`。
- `FileResponse` 支持 HTTP Range，播放器 seek 不需要整文件下载。
- 兼容回退：音频可加 `?format=m4a`，由 BFF 用 ffmpeg 转码为 `audio/mp4` 并缓存。
- 媒体 URL 由客户端统一绝对化并附 `?token=`。

---

## 3. 写入侧（lvdata-mcp）

### 3.1 事件写入

- 音频分析完成后按 `is_silent` 判定是否落 events：
  - `transcript` 词数 > 5（CJK 按字符、拉丁按词）或 `value == "high"` → 落事件；
  - 否则视为静音，只写审计文件、`mark_file_ok`，**不落 events 行**。
- 音频事件的 column `summary` = `transcript + " " + LLM 摘要 + " " + 情绪名`（检索用拼接串）。
- `event_ref` = `{rsession_id}:audio:{segment_id}`，`events.event_ref` 有唯一约束。
- `payload` 字段：`segment_id`、`duration_s`、`language`、`transcript`、`summary`、`value`、`emotions`、`objects`、`people`；非静音段在能量计算成功时另带能量字段。
- `duration_s`：优先扫描 ogg 最后一页 granule、减 OpusHead pre-skip，四舍五入到秒；解析失败回落会话配置段长；空段为 0。LLM schema 不含该字段，但 `{**parsed}` 展开在 `duration_s` 之后，理论上可被同名输出覆盖——客户端用 `Double?` 容忍浮点，非数值仍会导致该页解析失败。

### 3.2 能量包络

仅对非静音段计算，随 audio 事件 payload 写入：

```json
{
  "energy_v": 1,
  "frame_ms": 20,
  "db_min": -70.0,
  "db_max": 0.0,
  "energy": "base64(uint8[...])"
}
```

- 优先使用分析路径已加载的 16k 单声道 WAV 字节；直传 ogg（`audio_sample_rate = 0`）时用 ffmpeg 解为 16k 单声道裸 PCM。
- 每帧 `frame_ms = 20`（16k 下 320 样本，与 Opus 包时间对齐）；不足一帧的尾巴丢弃。
- 每帧 RMS → dBFS（`20*log10(rms/32768)`）；量化前 clamp 到 `[db_min, db_max]`，再映射为 0..255 的 uint8，base64 输出。
- 能量的计算失败不影响分析主流程：失败则 payload 不带能量，客户端降级为纯轨道。
- 数据量参考：60 秒段约 2999 帧 → base64 约 4 KB → gzip 约 2.5 KB；能量随列表一次性下发。
- 规模边界：单段显著变长（约 240 秒级以上）或单页能量增量过大时，应改为独立文件按需加载；当前实现为随 payload 内嵌。

### 3.3 存量回填

`tools/backfill_audio_energy.py`：

- 默认 dry-run，`--apply` 才写库。
- 只处理**已有 events 行的 audio 段**；静音段无事件、无卡片，不需要能量。
- 读现有 payload → 合并能量键 → `UPDATE events SET payload = ?`；不覆盖 `transcript`/`summary`/`emotions`。
- 不使用 `upsert_event_if_changed`（其 `WHERE excluded.summary != events.summary` 会在摘要未变时跳过整行更新）。
- 单事务 + `busy_timeout`；执行时避开 watcher/pipeline 在线写入窗口。
- 已带当前 `energy_v` 的段默认跳过，`--force` 可重算覆盖。

### 3.4 MCP 输出护栏

- MCP 的 `get_timeline` 使用轻量投影，不输出 payload。
- `get_event` 的 `detail` 是唯一直接透出 payload 的出口，调用 `strip_energy` 移除 `energy` / `energy_v` / `frame_ms` / `db_min` / `db_max`。
- BFF 面向 App 的 `/timeline` 保留能量字段，不受 MCP 护栏影响。

---

## 4. Android 实现

### 4.1 文件与职责

路径相对 `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app`。

| 文件 | 职责 |
|---|---|
| `data/remote/mylife/dto/MylifeDtos.kt` | `TimelineEventDto` 的 audio/能量字段；`EnergyEnvelope`；`AudioSegment` |
| `data/repository/MylifeRepository.kt` | `audioSegments(date, cursor, limit = 40)`；URL 绝对化 + token；能量解码；`AudioPage` |
| `media/AudioPlayer.kt` | `AudioPlayer` 接口、`State`、`ToggleAction`/`decideToggle` 纯函数 |
| `media/ExoAudioPlayer.kt` | 单例 ExoPlayer 实现、音频焦点、实时会话互斥、输出设备钉住、位置回写 |
| `di/MylifeModule.kt` | `AudioPlayer` → `ExoAudioPlayer` 的 Hilt 绑定 |
| `ui/screens/mylife/audiogallery/AudioGalleryScreen.kt` | 屏：顶栏、状态分支、LazyColumn、触底预取 |
| `ui/screens/mylife/audiogallery/AudioGalleryUiState.kt` | 状态模型；`segmentTimeLabels` / `segmentBodyText` / `formatTimecode` / `envelopeBars` |
| `ui/screens/mylife/audiogallery/AudioGalleryViewModel.kt` | 首屏/分页/去重/重试/播放与 scrub 转发/离屏停止 |
| `ui/screens/mylife/audiogallery/components/AudioSegmentCard.kt` | 段卡片：时间头、播放条、正文单区、错误行 |
| `ui/screens/mylife/audiogallery/components/AudioTimeHeader.kt` | 时间 + `››` 药丸，整组可点 |
| `ui/screens/mylife/audiogallery/components/SegmentProgressBar.kt` | 可拖进度条、能量镜像柱、滑块 |
| `ui/screens/mylife/timeline/PeriodAggregator.kt` | `PeriodDigest.audioCount`（仅 DAY） |
| `ui/screens/mylife/timeline/components/PeriodCard.kt` | 日卡 chips 行 + 右侧音频入口 |
| `ui/screens/mylife/timeline/components/AudioEntryButton.kt` | `GraphicEq` + 段数入口 |
| `ui/screens/mylife/timeline/TimelineViewModel.kt` | `AudioGalleryNavigation` / `audioNavigation` / `onAudioClick` |
| `ui/screens/mylife/timeline/TimelineScreen.kt` | 收集导航并透传；接 `PeriodCard.onAudioClick` |
| `ui/screens/mylife/MylifeHomeScreen.kt` | 宿主参数透传 |
| `ui/navigation/Routes.kt` / `NavGraph.kt` | `MYLIFE_AUDIO` 路由、builder、composable、eventList 跳转 |

### 4.2 日卡入口

`PeriodDigest.audioCount` 由 `buildDigest` 填充：

```kotlin
audioCount = if (granularity == Granularity.DAY) {
    days.sumOf { it.counts[EventType.AUDIO] ?: 0 }
} else 0
```

`PeriodCard`：

- 行渲染条件：`hotSlots.isNotEmpty() || (audioCount > 0 && onAudioClick != null)`。
- 布局：`Row(verticalAlignment = Alignment.Top)`；有 chips 时 `HotSlotChips(Modifier.weight(1f))`，否则 `Spacer(weight(1f))`；入口固定在右侧。
- 入口仅在 `audioCount > 0 && onAudioClick != null` 时显示。
- `AudioEntryButton`：整行可点，`Panel(0.8)` 圆形底 + 18dp Gold `GraphicEq` + Dust `labelMedium` 段数；`contentDescription` 为本地化复数文案（中文「语音记录，N 段」）。

导航：

- `TimelineViewModel.onAudioClick(digest)` 在 `audioCount > 0` 时发出 `AudioGalleryNavigation(date, label, count, pending)`。
- `TimelineScreen` 收集后调用 `onNavigateToAudio`，经 `MylifeHomeScreen` 透传到 `NavGraph`。
- 路由：`mylife/audio?date={date}&label={label}&count={count}&pending={pending}`；`mylifeAudio` 对 label 做 URI 编码。

### 4.3 AudioGallery 屏

状态：

- `Loading`：加载提示。
- `Empty`：`Info` 图标 +「这一天没有语音」+ 副标题「分析完成后会自动出现」。
- `Error`：错误文案 +「重试」按钮。
- `Data(segments, total, nextCursor, loadingMore)`。

ViewModel：

- `load(date, countSnapshot, pending)`：同日且已有 `Data` 时跳过；否则重置并拉第一页。
- `retry()`：不经过日期跳过逻辑，直接重拉第一页。
- `reloadFirstPage()`：取消进行中的加载；已有数据时不回落到 Loading；首屏失败才进入 Error。
- `loadMore()`：`nextCursor` 为空或正在加载时直接返回；失败静默保留已加载页。
- `applyPage()`：replace 清空并重建 `seenRefs`；append 按 `eventRef` 去重追加；每次更新 `headerCount = response.total`。
- overview 轮询：已加载且当前无播放/暂停中的段（`player.key == null`）时重拉第一页；pending 为 true 且 `hasPendingAnalysis()` 为 false 时单向收敛为 false。
- `onCleared()` → `player.stop()`。

顶栏：

- 返回按钮（Dust）、label（`titleMedium` / Gold，单行省略）、`N 段`（Dust / `labelMedium`）。
- `N`：首屏响应前用路由快照 `count`，响应后使用 `total`；`loadMore` 也会刷新为最新 `total`。
- pending 时显示「分析中…」（Gold 0.8）。

列表与分页：

- `LazyColumn`，`itemsIndexed(key = segment.eventRef)`。
- 时间标签由 `segmentTimeLabels(segments, locale)` 生成：每段一个 `HH:mm`，不去重；数字按 locale 本地化（阿拉伯语使用阿数）。
- 触底条件：最后一个可见 item 索引 ≥ `segments.size - 4`。
- 防重试风暴：`LaunchedEffect(atListEnd, segments.size, loadingMore)` + `lastAttemptSize` + `leftEndSinceAttempt`，每次「新鲜触发」只发一次 `loadMore()`；失败后需滚离阈值区再回来，或上一页成功追加（`segments.size` 变化）才会再次触发。
- 正在加载下一页时列表底部显示加载提示。
- 当前屏只加 `statusBarsPadding()`，未加 `navigationBarsPadding()`/底部 contentPadding（与既有子屏一致，末卡可能被系统导航栏压住）。

### 4.4 播放器

接口：

```kotlin
interface AudioPlayer {
    val state: StateFlow<State>
    fun toggle(key: String, url: String)
    fun seekTo(ms: Long)
    fun stop()

    data class State(
        val key: String? = null,
        val isPlaying: Boolean = false,
        val isBuffering: Boolean = false,
        val positionMs: Long = 0L,
        val durationMs: Long = 0L,
        val error: String? = null,
    )
}
```

`ExoAudioPlayer`（`@Singleton`）：

- 懒建单个 `ExoPlayer`；`DefaultMediaSourceFactory(OkHttpDataSource.Factory(@Named("mylife") client))`。
- `AudioAttributes`: `USAGE_MEDIA` + `AUDIO_CONTENT_TYPE_SPEECH` + `handleAudioFocus = true`；与实时会话的 `USAGE_VOICE_COMMUNICATION` 区分。
- `decideToggle`：
  - 异 key → `Start`（设置 media item，从 0 播放）；
  - 同 key 且 error → `Start`（清错误、seek 0、prepare 后重播）；
  - 同 key 且正在播 → `Pause`；
  - 其余 → `Resume`。
- `STATE_ENDED`：先 `pause()` 再 `seekTo(0)`，状态位置归零、`isPlaying=false`，`key` 保留。
- 位置/时长：播放中每 200 ms 轮询 `currentPosition`/`duration`；`onIsPlayingChanged` 启停 ticker。
- 错误：`onPlayerError` 停止 ticker，保留 `key`，`error` 为本地化「播放失败」；错误显示在对应卡片内，不弹窗。
- `stop()`：停止 ticker、`stop()` + `release()` ExoPlayer、状态复位。

与实时会话互斥：

- 观察 `AudioPlaybackController.isPlaying`；当其由 false 变为 true 且画廊 `isPlaying == true` 时暂停画廊。
- 该观察不覆盖两种时序：实时会话已在播时启动画廊、画廊处于起播/缓冲（`isPlaying == false`）时 AI 开始说话。这两种情况没有额外的启动拦截，取决于系统音频焦点。
- 不观察回落沿、不自动恢复；反向（AI 开口打断画廊）由系统音频焦点处理，AI 说完后预期由 media3 默认焦点策略恢复播放（真机待验证）。

输出设备（策略 C）：

- 自定义 `DefaultRenderersFactory` 捕获 `DefaultAudioSink`。
- 每次 `ToggleAction.Start` 在 `prepare()`/`play()` 前调用 `setPreferredDevice(playbackController.currentOutputDevice())`。
- 订阅 `playbackController.preferredOutputDeviceId`，变化时重新应用。
- `currentOutputDevice()` 解析顺序：
  - 用户已选设备且可解析 → 用该设备；
  - 已选设备失效 → 内置扬声器 → 实时 AudioTrack 的 `routedDevice` → `null`；
  - 用户选择「系统默认」（`preferredOutputDeviceId == null`）→ 实时 AudioTrack 的 `routedDevice` → `null`。
  不修改全局 `AudioManager.mode`/`isSpeakerphoneOn`。
- 若 sink 未就绪或平台拒绝设备，记录 warning 并继续播放。

生命周期与后台：

- `onCleared()` → `stop()`，释放播放器。
- 进入 eventListViewer 不销毁画廊 entry，播放继续；返回后恢复列表状态。
- App 退到后台不主动暂停播放；不注册前台服务/通知，当前段播完即停。

### 4.5 段卡片

- `Surface(RoundedCornerShape(12.dp), Panel 0.6)`，外层 `horizontal = md` / `vertical = xs`，内层 `padding(md)`。
- 顶部可选 `AudioTimeHeader`（在卡片 Surface 之外）。
- 播放条行：圆形播放/暂停按钮（Panel 0.9 + 22dp Gold 图标）→ 进度条（`weight(1f)`，高 40dp）→ 时码（`labelMedium` / Dust）。
- 时码：当前段在有效时长 > 0 时显示 `当前 / 总长`（播放器时长优先，未就绪回落到段 `duration_s`），其他段只显示总长。
- 播放按钮 `contentDescription` 随状态切换「播放」/「暂停」。
- 正文：`segmentBodyText(transcript, summary)`，`bodyMedium` + Star；单区，不单独显示摘要。
- error 非空且在当前段时，播放条下方显示红色错误文案。
- 进度条不暴露 `ProgressBarRangeInfo` 语义（仅按钮有语义）。

### 4.6 时间头

`AudioTimeHeader(label, onTimeClick)`：

- 居中 `Row`，内层圆角药丸：`Panel(0.8)` 背景，时间 Dust、`››` Gold，均为 `labelMedium`。
- 外层触控带 `heightIn(min = 32.dp)`，`clickable` 在 `padding(horizontal = xs)` 之前，padding 计入触控区。
- 可点时使用 `Role.Button` + `mergeDescendants`，`contentDescription = "查看 HH:mm 前后 10 分钟的事件"`。
- `onTimeClick == null` 时只展示，不可点、无自定义语义。
- 点击回调 → `onNavigateToEventList(segment.tsMs, uiState.pending)` → `Routes.mylifeEventList` → eventListViewer（窗口 `ts ± 10min`，锚点行高亮 2 秒）。

### 4.7 进度条与能量包络

- 高度 40dp；滑块 7dp Gold 圆点 + 1.5dp Abyss 描边。
- **有能量数据**：
  - 以中线为轴上下对称的圆角柱；柱宽为槽位的 62%，槽位约 1.5dp 一条，最多 512 条。
  - 已播部分 Gold，未播部分 Line；不画中心水平轨道线。
  - 条数按可用宽度计算：`barCount = widthPx / 1.5dp`，限制在 `1..512`。
  - 第 b 条取对应帧区间的 **max**（保起音）；映射按 `values.size` 比例，不假设 `len == durationMs/20`。
  - 归一化：`floor = max(峰值 - 40 dB, dbMin)`，柱高 `(value - floor) / (peak - floor)` 并 clamp 到 0..1。
  - 全柱为 0（平直/极安静段）→ 返回空数组，降级为纯轨道。
- **无能量数据**：保留原纯轨道形态，4dp（Line 底 + Gold 已播）+ 滑块。
- 手势：单一 `awaitEachGesture`；横向位移超过 touch slop 才 consume 并回调 `onScrub`；纵向未过 slop 时交给 `LazyColumn` 滚动；父级已消费则本轮作废、不 seek；松手/点击回调 `onScrubEnd`。
- 回调通过 `rememberUpdatedState` 取最新值，避免 `pointerInput(Unit)` 捕获首次组合的旧闭包。
- 非当前段轨道也可交互（有效时长 > 0 时）：`AudioGalleryViewModel.onScrubEnd` 先 `toggle` 切到该段，再 `seekTo`。
- 拖动中的位置只作用于被拖的那张卡（`scrubRef` + `scrubMs`），其他卡不跟随。

---

## 5. 测试覆盖

### 5.1 Android JVM

入口：`apps/android` → `./gradlew :LivaChatApp:testDebugUnitTest`

| 测试文件 | 覆盖 |
|---|---|
| `audiogallery/AudioGalleryViewModelTest` | 首屏/分页追加去重、失败保留已加载页、pending 收敛、播放中不重取、toggle/seek/scrub、`onCleared` 停止 |
| `audiogallery/SegmentTimeLabelsTest` | 每段一个标签、同分钟不去重、一一对应、跨小时、指定时区、空列表、阿拉伯语数字本地化、时码格式 |
| `audiogallery/SegmentBodyTextTest` | >5 字显示转写、≤5 字回退摘要、皆无返回 null |
| `audiogallery/EnvelopeBarsTest` | 窗口 max、峰值归一、floor 绝对下界、全零降级、帧数不整除、空包络、帧数少于条数 |
| `media/AudioPlayerToggleTest` | `decideToggle` 的切段/暂停/续播/错误重试 |
| `media/AudioPlaybackControllerTest` | `currentOutputDevice()` 优先级与回退链（选中、失效→扬声器→routedDevice→null、系统默认、释放竞态） |
| `data/repository/MylifeRepositoryTest` | 能量解码成功、版本不识别/缺字段/非法 base64/非法区间四态降级 |
| `timeline/PeriodAggregatorTest` | `audioCount` 仅 DAY 非零、无音频为 0 |
| `timeline/TimelineViewModelTest` | 日卡音频入口导航参数、无音频不导航 |

### 5.2 Android 仪器

入口：单类建议 `am instrument` 直跑（`connectedDebugAndroidTest` 跑完会卸载被测 app，清空数据）。

| 测试文件 | 覆盖 |
|---|---|
| `audiogallery/AudioGalleryScreenTest` | 段渲染与正文三态、逐段标签、空态、触底失败不重试风暴、能量包络像素断言、非播放段轨道点击起播、时间头跳转与 32dp 触控带 |
| `audiogallery/SegmentProgressBarTest` | 重组后手势回调取最新闭包 |
| `hotslot/HotSlotComponentsTest` | 日卡入口渲染/点击、0 段隐藏、无 hotSlot 日仍出行、无回调不渲染 |

### 5.3 Pipeline / MCP / BFF

| 套件 | 覆盖 |
|---|---|
| `tools/lvdata-mcp/tests/test_audio.py` | 能量 dBFS 已知值、量化 clamp、帧数边界、非 WAV/立体声降级、直传 ogg 缺 ffmpeg 时无能量降级 |
| `tools/lvdata-mcp/tests/test_energy_guard.py` | DB 行保留能量、`get_event` 剔除、`get_timeline` 轻量投影、其他类型不受影响、helper 幂等 |
| `services/tests/unit/bff/test_timeline_api.py` | `audio_summary` 取值、仅 audio 独有、`payload_dict` 不外泄、能量字段透传、`counts["audio"] == timeline total` 不变量 |

命令：

```bash
# BFF
.venv/bin/python -m pytest services/tests/unit

# lvdata-mcp
cd tools/lvdata-mcp && ../../.venv/bin/python -m pytest tests/ -q

# Android JVM
cd apps/android && ./gradlew :LivaChatApp:testDebugUnitTest
```

---

## 6. 已知边界

| 项 | 说明 |
|---|---|
| 计数口径 | `audioCount` 来自 overview 的 `counts`（全设备预计算），`/timeline` 有设备白名单；overview/timeline 时区解析规则也不同。画廊以 `total` 为准，入口数字只作提示。 |
| 入口存在但画廊为空 | 客户端会丢弃缺少 `media_url`/`event_ref` 的事件；异常数据下可能出现入口存在但空屏，画廊有空态兜底。 |
| 时区切换 | 日卡 key 来自 overview 的生效时区（可能是最新时区，而非设备时区）；进入画廊按当前设备时区重算日窗口。旅行改时区可能对应不同的一天。 |
| eventList 不含语音 | 时间头跳转的 ±10 分钟窗口只展示 5 类可展示事件，不包含 audio 本身。 |
| 重取行为 | 非播放状态下 overview 每次更新会重拉首页并替换列表，已加载的后续页会丢弃；播放/暂停中不重取（避免播放段从列表消失）。 |
| 实时会话互斥时序 | 画廊只在 `isPlaying=true` 时接收实时会话的上升沿并暂停；画廊起播/缓冲期间 AI 开口、或实时会话已在播时启动画廊，均无代码级拦截，需依赖系统音频焦点。 |
| 播放缓存 | 每次播放/seek 走 Range 请求，无本地缓存；60 秒 Opus 段通常 100–500 KB。 |
| 后台播放 | 不注册前台服务/通知；App 退到后台不主动暂停，当前段播完即停；系统音频焦点变化仍可能暂停。 |
| 导航栏留白 | AudioGallery 未加 `navigationBarsPadding`/底部 contentPadding，末卡可能被系统导航栏遮挡。 |
| TLS | 开发环境 ExoPlayer 复用 mylife 的 trust-all OkHttpClient；生产前需收敛为系统证书校验。 |
| 输出设备 | 策略 C 跟随实时会话；实时 Track 不存在且选择「系统默认」时，voice/media 可能落到不同设备。系统默认下若实时 Track 的 `routedDevice` 在播放中变化而 `preferredOutputDeviceId` 不变，画廊不会实时跟随，下次 Start 才重新读取。 |
| 能量体积 | 20 ms 分辨率下 60 秒段约 +2.5 KB gzip；单段约 240 秒级以上应改为独立文件按需加载。 |
| `duration_s` | LLM 若输出非数值同名键，kotlinx 解析会失败，整页进入 Error（可重试）；`Double?` 只解决浮点问题。 |
| 真机待验证 | 拖动 seek 的 Range/206、后台播完即停、断网「播放失败」与重试、实时会话与画廊的双向音频焦点（含 AI 说完自动续播）、输出设备路由矩阵、自签证书下 Ogg Opus 解码。 |
