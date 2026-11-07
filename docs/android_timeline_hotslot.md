# Hot Slot 现行实现（Android Timeline）

> 本文只描述**当前代码的最终行为**，不记录设计演进、评审过程或历史决策。
> 范围：日卡活跃时段（hot slot）的数据生产、BFF 契约、Android 首页入口、hotSlotView、eventListViewer，以及与帧查看器 / agent_job 结果查看器的交互。
>
> 相关文档：
> - `docs/bff_service_interfaces.md` §7（overview 单表化与 day_stats）、§8（overview 分页）
> - `docs/android_agent_job_view.md`（agent_job 结果折叠与全屏查看）
> - `docs/android_timeline_image_gallery.md`（帧 Gallery / 查看器先例）
> - `docs/android_timeline_audio_gallery.md`（日卡音频入口）
> - `docs/android_timeline_topics.md`（eventListViewer 锚点机制）

---

## 1. 概览

### 1.1 功能

- 每天最多 3 个活跃时段（hot slot），只显示在**日卡**上，周/月/年卡不显示。
- 每个 slot 只展示开始时间，chip 形如 `▸ 09:12`。
- 点击 chip 进入独立全屏页 **hotSlotView**，展示该 slot 时间窗口内的五类可回顾事件：
  - `chat`
  - `agent_job`
  - `video`（关键帧）
  - `transcribe`
  - `translate`
- 文本事件按角色显示为左右对齐气泡；视频帧以 3 列网格插入时间流。
- 帧图可点开单图全屏查看器，支持保存到相册。
- agent_job 结果行可点开全屏结果查看器；长正文在行内折叠。

### 1.2 路由

```kotlin
MYLIFE_HOT_SLOT = "mylife/hotslot?start={start}&end={end}&pending={pending}"
MYLIFE_EVENT_LIST = "mylife/events?ts={ts}&pending={pending}"
```

- `start` / `end`：epoch 毫秒，既是取数窗口也是标题时间来源。
- `pending`：`"true"` / `"false"`，作为顶栏「分析中…」和 pending 空态的初值。
- hotSlotView 没有日期参数；取数日期由 slot 的毫秒值在**当前设备时区**现推。
- eventListViewer 由搜索命中进入，窗口固定为被点事件 `ts ± 10min`。

### 1.3 组件关系

```
events(五类可展示事件)
  └─ lvdata-mcp day_stats stage（按 updated_at 增量重算活跃时区的日）
       └─ day_summary.day_stats JSON
            └─ BFF GET /api/mylife/overview（读预计算值，不再派生）
                 └─ Android MylifeRepository.overview（分页窗口 StateFlow）
                      └─ PeriodAggregator（仅 DAY 映射 hot_slots）
                           └─ PeriodCard → HotSlotChips
                                └─ hotSlotView（HotSlotScreen / HotSlotViewModel）
                                     ├─ HotSlotRowItem（气泡 / 帧行）
                                     ├─ HotSlotFrameViewer（单图查看器）
                                     └─ AgentJobResultHost（agent 结果查看器）
```

eventListViewer 与 hotSlotView 复用 `HotSlotViewModel` 的取数/状态逻辑（各自路由持有实例）以及同一套行模型、行组件，只额外增加锚点定位与高亮。

---

## 2. 数据契约

### 2.1 hot slot 口径

hot slot 的派生只使用五类事件：

```
chat, agent_job, video, transcribe, translate
```

`audio` / `window` 不参与分段。

单日算法：

1. 事件按 `ts_ms` 升序（同 ts 保持上游稳定顺序）。
2. 相邻事件间隔 **大于 60 分钟**（严格 `>`）时切新段。
3. 段数超过 3 时，反复合并“相邻间隔最小”的一对，直到只剩 3 段。
   - 间隔并列时取最左的一对。
   - 合并后 `start_ms` 取前段起点，`end_ms` 取后段终点，`count` 为两段之和。
4. 输出始终按时间升序，最多 3 段。

相关常量：

```python
SPLIT_GAP_MS = 60 * 60 * 1000
MAX_HOT_SLOTS = 3
HOT_SLOT_TYPES = {"chat", "agent_job", "video", "transcribe", "translate"}
```

定义位置：`tools/lvdata-mcp/src/lvdata_mcp/consts.py`。

### 2.2 预计算与存储

hot slot **不再由 BFF 在请求时派生**，而是由 lvdata-mcp 的 `day_stats` stage 预计算：

- 派生函数：`tools/lvdata-mcp/src/lvdata_mcp/summary/day_stats.py` → `derive_hot_slots()`。
- 组装函数：同文件 `derive_day_stats()`，产物还包含 `counts` 与 `images`。
- 写入位置：`day_summary.day_stats` JSON 列。
- 运行时机：`day_stats` 属于常跑组，位于 `video` 之后、`reindex` 之前；每个 round 执行（poll 默认 60 秒一轮）。
- 增量发现：候选日 = `events.updated_at > day_stats_state.last_scan_at`，按活跃时区映射到本地日。
- 日窗读取：某日全部事件（`ts_ms` 在日窗内、`updated_at <= T_scan`），按 `ts_ms, PRIORITY, event_id` 排序。
- 已有 `day_summary` 行时只更新 `day_stats` 列，不触碰 `status` / `day_summary` / `model` / `generated_at` / `fail_count`。
- 删除事件接口会对受影响日调用 `recompute_days()`，与 stage 游标解耦。

因此 hot slot 的数据新鲜度跟随 day_stats stage 的 round 周期，不是每次 `/overview` 请求实时重算。

### 2.3 Overview API

```http
GET /api/mylife/overview?timezone=<IANA>&limit=<days>&cursor=<YYYY-MM-DD>
```

响应中的 `days[].hot_slots`：

```json
[
  { "start_ms": 1758348000000, "end_ms": 1758350400000, "count": 26 }
]
```

- BFF 只做 `day_stats.hot_slots` 的透传；畸形条目丢弃，不使整个请求 500。
- 结构仍为 `{start_ms, end_ms, count}`，升序、≤3。
- `count` 保留在 wire 上仅为兼容旧客户端；当前 Android 不消费它。
- `hot_slots` 之外，overview 已经支持分页：
  - `limit`（1..400）存在时分页，`days` 是页窗口；
  - `cursor` 为上一页最后一天，语义是严格早于；
  - 响应包含 `next_cursor` 与 `total`；
  - 不传 `limit` 为全量模式（有 1000 天防呆上限）。
- overview 的时区由 `resolve_overview_tz()` 决定：请求时区等于“最新时区”时使用请求值，否则强制使用最新时区（`timezones` 表中最近更新的启用行）；表缺失/异常时放行请求值。
- overview 不再做设备过滤：`build_overview` 的 `device_id` 入参被忽略，`day_stats` 为全设备合并口径。
- `has_pending` 的当前口径：
  - `status == "pending"` → true；
  - `status == "failed"` 且 `fail_count < 3` → true（仍会重试）；
  - 其他 → false。

### 2.4 Timeline API

hotSlotView 取数复用 `/timeline`，BFF 没有 hot slot 专用端点。

```http
GET /api/mylife/timeline
  ?date_from=<YYYY-MM-DD>&date_to=<YYYY-MM-DD>
  &types=chat,agent_job,video,transcribe,translate
  &limit=500&cursor=<date|offset>&timezone=<IANA>
```

- `limit` 默认 200，上限 500。
- `cursor` 形态为 `<本地日期>|<offset>`，由 BFF 生成；客户端只透传。
- 未知 `types` 返回 `bad_request`。
- 设备白名单过滤仍生效（由 principal 的 `device_id` 与 `store.device_uuids` 决定）。
- 事件排序：`ts_ms, PRIORITY, event_id`。
- 响应按本地日分组，`days` 为日期倒序；每个 `days[].events` 内为时间升序。
- 顶层包含 `timezone`、`total`、`next_cursor`。
- 事件 payload 扁平化；`event_ref` 透出，`event_id` 不透出。
- `media_url` 由 BFF 返回相对路径，Android 出口统一转为绝对 URL 并附 `token`。
- 常用字段：`type`、`role`、`text`、`summary`、`agent_name`、`job_key`、`frame_id`、`window_id`、`caption`、`media_url`、`event_ref`、`ts_ms`。

### 2.5 兼容性

- 新 BFF × 旧 App：旧 App 继续读取 `hot_slots` 的 `start_ms/end_ms/count`，点击仍可用。
- 旧 BFF × 新 App：前提是旧 BFF 的 `EVENT_TYPES` 已包含 transcribe/translate；满足时新 App 忽略 `count`，分段边界/数量可能按旧口径不同但不会解析失败。若旧 BFF 不认识五类中的任一类型，`/timeline` 会返回 `bad_request`，hotSlotView 落错误态。
- `HotSlotDto.count` 在 Kotlin 中仍为非可空字段，删除会破坏旧客户端解析。

---

## 3. Android 聚合与入口

### 3.1 Overview 数据层

`MylifeRepository` 是单例，持有：

```kotlin
val overview: StateFlow<OverviewDto?>
```

- `refreshOverview()` / `loadMoreOverview()` 维护 keyset 分页窗口；多页按 `date` 去重合并。
- 页面进入时强制刷新首屏；时间线粒度切换可能按粒度页大小补取。
- pending 轮询：已加载窗口的最新 **400 天**内存在 `hasPending` 时，每 60 秒强制刷新该窗口。
- `hasPendingAnalysis()` 只检查上述窗口，不再代表全量历史。

### 3.2 PeriodAggregator

`PeriodDigest.hotSlots` 的规则：

- **仅 DAY 粒度非空**。
- 直接映射 `OverviewDayDto.hotSlots` 为：

```kotlin
data class HotSlot(
    val startMs: Long,
    val endMs: Long,
)
```

- `date` / `count` / `latestSlot` 均不存在于当前模型。
- WEEK / MONTH / YEAR 的 `hotSlots` 恒为 `emptyList()`。

### 3.3 日卡 chips

`PeriodCard` 在日卡上渲染 hot slot chips：

- 标签由 `HotSlotChips` 生成：`▸ HH:mm`。
- 设备本地时区；数字经 `localizeDigits()` 处理（例如阿语数字）。
- 多个 chip 使用 `FlowRow` 自动换行。
- chip 点击回调携带 `(slot, pending)`，其中 `pending = digest.hasPending`。
- 日卡同一行右侧可能有音频入口（`audioCount > 0` 时）；没有 hot slot 但有音频时该行仍会出现。
- 周卡本体不可点；月/年卡本体仍下钻。日卡本体也不可点。

### 3.4 导航链路

```
PeriodCard(onHotSlotClick)
  → TimelineViewModel.onHotSlotClick(slot, pending)
      _hotSlotNavigation.tryEmit(HotSlotNavigation(startMs, endMs, pending))
  → TimelineScreen 收集 hotSlotNavigation
  → MylifeHomeScreen(onNavigateToHotSlot)
  → NavGraph.navigate(Routes.mylifeHotSlot(...))
  → HotSlotScreen
```

`TimelineViewModel` 同时保留 Gallery、EventList、AudioGallery 三条 SharedFlow 导航通道。

---

## 4. hotSlotView 取数

### 4.1 Repository

`MylifeRepository.hotSlotEvents(startMs, endMs, cursor)`：

1. 用**当前系统时区**由 `startMs` / `endMs` 推导 `date_from` / `date_to`：
   - `from = localDate(startMs)`
   - `to = localDate(endMs)`
   - 不使用 overview 的 `date` 键（overview 时区可能已被重定向到“最新时区”，且跨时区时该键可能不覆盖 slot）。
2. 调用 `/timeline`，参数：
   - `types = "chat,agent_job,video,transcribe,translate"`
   - `limit = 500`
   - `timezone = 系统时区`
   - `cursor = 上一页 cursor`
3. 处理响应：
   - 扁平化所有 `days[].events`；
   - 过滤 `ts_ms in startMs..endMs`（含端点）；
   - video 事件必须同时有 `frame_id` 与 `media_url`，否则丢弃；
   - 非 video 事件必须有非空正文；agent_job 的正文按 `text → summary → agent_name` 回落，其他类型取 `text`；
   - `media_url` 绝对化并附 token；
   - 按 `ts_ms` 稳定升序排序（同 ts 保持 BFF 的 prio 顺序）。
4. 返回 `HotSlotPage`：
   - `events`
   - `nextCursor`
   - `reachedWindowEnd = 本页任一事件 ts_ms > endMs`

翻页停止条件：

- `reachedWindowEnd == true`，或
- `nextCursor == null`。

`reachedWindowEnd` 使用“本页任一事件越界”，不能只看扁平流末尾事件，因为响应顶层按日倒序、扁平流并非全局有序。

### 4.2 ViewModel 状态

`HotSlotUiState`：

```kotlin
data class HotSlotUiState(
    val state: HotSlotState = HotSlotState.Loading,
    val pending: Boolean = false,
    val viewerFrame: HotSlotEvent? = null,
    val viewerAgentJobKey: String? = null,
    val agentJobInitialCharOffset: Int? = null,
    val saving: Boolean = false,
    val imageSaveTip: String? = null,
)
```

`HotSlotState`：

- `Loading`
- `Empty`
- `Error(message)`
- `Data(events, loadingMore, exhausted)`

当前没有 `Idle` 状态。

### 4.3 首屏、分页与追击

`HotSlotViewModel` 行为：

- `load(startMs, endMs, pending)`：
  - 窗口非法（`startMs <= 0` 或 `endMs < startMs`）直接返回；
  - 同窗口且已有 Data 时跳过重复加载；
  - 否则重置分页并加载首页；
  - 首页加载会清空两个覆盖层。
- `reloadFirstPage()`：
  - 取消在飞的首屏/追击/加载更多；
  - 清空 `seenRefs`、`nextCursor`、`exhausted`；
  - 已有 Data 时不回落到 Loading（避免轮询刷新闪屏、丢滚动位置）；
  - 失败时保留已有列表，仅首屏无数据时进入 Error。
- `fetchPages(firstPage)` 主循环：
  - 从当前 cursor 连续翻页；
  - **首页可能整页都落在窗口之前**（重度日事件超过 500 条且窗口靠后），此时继续翻页，不落 Empty；
  - 直到已见窗口内内容或翻页终止；
  - 重取首屏按替换语义应用，避免旧列表与新页重复追加。
- `loadMore()`：
  - 仅 Data 态可触发；
  - 追击中、已在加载、已终止、无 cursor 时 no-op；
  - 加载失败静默保留已有页；用户需滚离触底阈值区再回来才会重试（同一次触底不会自动重发）。
- 触底重试不是自动风暴：
  - Screen 侧用“新鲜触发”判定：滚离阈值区再回来，或上一页成功追加（行数变化）才再次请求；
  - 同一触底状态不会因 `loadingMore` 回落而立即重发。
- `ensureLoadedUntil(anchorTs)`（eventListViewer 用）：
  - 事件层已有 `ts >= anchorTs` 时不再取；
  - 最多追击 5 页（只计首段落地后的追击页）；
  - 失败/超限静默退化，定位由 Screen 兜底。

### 4.4 跨页去重

- `/timeline` 的 cursor 是 `date|offset`，录制中持续插入事件会使 OFFSET 位移。
- Repository 每页返回后，ViewModel 用 `event_ref` 的 `HashSet` 做跨页去重：
  - 有 `event_ref`：首次出现才保留；
  - 无 `event_ref`：保留（当前 BFF 恒透出，实际不可达）。
- 去重只解决“重复”，不解决 OFFSET 位移导致的“漏”；漏由 pending 期间的 overview 轮询触发的首屏重取兜底（只追到首个窗口内页，更深的漏页不会被这次重取恢复）。

### 4.5 pending

- `pending` 初值来自路由参数（日卡 `hasPending` 快照）。
- ViewModel 持续 collect `repository.overview`：
  - 已加载过列表时，overview 实例变化会重取首页并替换列表；
  - 收到非空 overview 后，当 `pending == true` 且 `repository.hasPendingAnalysis() == false` 时，`pending` **单向收敛为 false**（不会因后续 overview 又变 true 而回翻）。
- pending 时：
  - 顶栏显示「分析中…」；
  - 空态显示「内容分析中…」并提供刷新入口；
  - 非 pending 空态显示「该时段没有可展示的内容」。
- 注意：`hasPendingAnalysis()` 是已加载窗口的最新 400 天口径，不是全量历史。

### 4.6 覆盖层状态

- 帧查看器与 agent_job 结果查看器互斥：
  - 打开帧查看器会清空 agent_job 查看器状态；
  - 打开 agent_job 查看器会清空帧查看器状态。
- `load()` / `retry()` 会清空覆盖层；
- overview 轮询触发的 `reloadFirstPage()` **不清空**覆盖层，避免后台轮询把用户正在看的内容关掉。
- 保存帧：
  - `saveViewerFrame()` 以 `uiState.viewerFrame.mediaUrl` 为准；
  - URL 为空直接 no-op；
  - `saving` 为真时忽略重复点击；
  - `repository.downloadFrame()` 失败 → 「保存失败」；
  - `ImageGallerySaver.saveJpegBytes()` 失败 → 「保存失败」；
  - 成功 → 「已保存到本地相册」；
  - 提示 2 秒后消失；`saving` 在 `finally` 复位；
  - 关闭查看器会取消在飞保存并清提示。

---

## 5. 行模型与渲染

### 5.1 行模型

```kotlin
enum class BubbleSide { USER, AI, AGENT }

sealed interface HotSlotRow {
    val key: String
    val timeLabel: String?

    data class Text(
        override val key: String,
        override val timeLabel: String?,
        val tsMs: Long,
        val side: BubbleSide,
        val body: String,
    ) : HotSlotRow

    data class Frames(
        override val key: String,
        override val timeLabel: String?,
        val frames: List<HotSlotEvent>,
    ) : HotSlotRow
}
```

行 key：

- 文本行：`t-<event_ref 或 ts_ms>`
- 帧行：`f-<首帧 event_ref 或 ts_ms>`

`buildHotSlotRows(events, locale, transcriptSuffix, originalSuffix, translationSuffix)` 负责事件 → 行转换。

### 5.2 帧行

- 连续 video 事件（中间没有其他事件）成组。
- 组内按 `ts_ms` 升序，`chunked(3)`，每行最多 3 张。
- 每张 1/3 宽，1:1，`ContentScale.Crop`，8dp 圆角。
- 占位/错误色：`MylifeColors.Panel`。
- 末行不足 3 张时用等宽 `Spacer` 补齐，保持左对齐、每张 1/3 宽。
- 每张可点 → `HotSlotFrameViewer`。

### 5.3 分钟分隔标签

- 分钟桶：`tsMs / 60_000`（前提：IANA 时区偏移均为整分钟，UTC 桶与本地分钟边界重合）。
- 同一分钟只有**第一条实际产出的行**带 `timeLabel = "HH:mm"`；同分钟后续行 `timeLabel = null`。
- 被角色白名单丢弃的行不产出行，也不消耗标签。
- 连续帧按 chunk 处理：只有每个 chunk 的首帧参与分钟桶判断；同一 chunk 内跨分钟不额外出标签。
- 标签由 `HotSlotRowItem` 统一渲染为居中分隔行：
  - `labelMedium`
  - `MylifeColors.Dust`
  - 位于气泡/帧行上方。

### 5.4 文本行与角色映射

角色白名单：只有 `user` / `assistant` 可渲染；其他 role 整行丢弃。

| 类型 | role | side | 正文 |
|---|---|---|---|
| `chat` | `user` | `USER` | `text` |
| `chat` | `assistant` | `AI` | `text` |
| `transcribe` | `user` | `USER` | `text` + 「(转录)」后缀（写入侧 role 恒为 user；若出现 assistant，会按 AI 处理并同样带该后缀） |
| `translate` | `user` | `USER` | `text` + 「(原文)」后缀 |
| `translate` | `assistant` | `AI` | `text` + 「(译文)」后缀 |
| `agent_job` | 不检查 role | `AGENT` | `text`（Repository 已完成 `text → summary → agent_name` 回落） |

后缀都来自 string resources，随 App 语言本地化。

### 5.5 气泡

`HotSlotRowItem(row, onFrameClick, onLinkClick, onAgentJobClick)` 是唯一渲染入口：

1. 可选分钟分隔行；
2. `Text` → `BubbleRowItem`；
3. `Frames` → `FrameRowItem`。

气泡规格：

| 项 | 用户侧 (`USER`) | AI / Agent 侧 |
|---|---|---|
| 对齐 | 右 | 左 |
| 底色 | `BrandIndigo` 实底 | `MaterialTheme.colorScheme.surface` |
| 描边 | 无 | 1dp `surfaceVariant` |
| 文字色 | `TextPrimary` | `onSurface` |
| 圆角 | (16,16,16,8) | (16,16,8,16) |
| 最大宽度 | 屏宽 × 75% | 屏宽 × 75% |
| 内边距 | 横 16dp / 纵 8dp | 同左 |
| 行间距 | 2dp | 2dp |
| 正文 | `bodyLarge` | `bodyLarge` |

Agent 侧额外有头行：

- `Icons.Filled.SmartToy` 16dp，`TextSecondary`；
- 「Agent任务」`labelMedium` / `TextSecondary`；
- 当该行可点开全屏结果时，头行右侧显示 `OpenInFull` 图标。

### 5.6 agent_job 正文

仅 `side == AGENT` 且 `onLinkClick != null` 时走 Markdown；其他行保持纯 `Text`。

Agent 正文组件为共享的 `AgentJobResultText`：

- Markdown 由 `MarkdownContent` 渲染（Markwon）。
- **折叠**：
  - 仅当 `collapsible == true` 且正文长度 ≥ 800 字符时启用；
  - 按第 24 行边界裁剪；
  - 裁剪后显示「查看全文 · 还有 N 字」入口行；
  - 未超过行数或测量失败时不折叠（fail-open）。
- **表格宽度**：
  - 正文命中 GFM 表格分隔行时，`MarkdownContent` 获得 `Modifier.fillMaxWidth()`；
  - 避免 Markwon `TablePlugin` 在 0 宽首测下列宽坍缩为 0；
  - 表格判定是逐字符分隔行启发式，可能存在少数“误判为表格 → 多占宽度”的长尾，不影响正文内容。
- **点击**：
  - 仅当调用方传入 `onAgentJobClick` 时，Agent 行整体可点（hotSlotView / eventListViewer 均传入）；
  - 正文按下处通过 `onContentTap` 上报字符偏移；
  - 打开全屏查看器时按该偏移落位；
  - 点击链接不会触发行点击（链接先消费）。

全屏 agent 结果查看器由 `AgentJobResultHost` 承载：

- 身份 = 行 key（`HotSlotRow.Text.key`），不是行快照；
- 从当前 `rows` 按 key 解析正文；
- 分页窗口被轮询重取截回首页时，未命中不立即关窗，使用最近一次载荷兜底（`keepLastPayloadOnMiss = true`）；
- 覆盖层由 `AgentJobResultOverlay` 绘制：
  - 底色 `Surface`；
  - 竖屏顶栏：Agent 图标 + 到达时间 `HH:mm` + 关闭按钮；
  - 横屏去掉顶栏，关闭按钮浮在右上角；
  - 打开期间 Activity 使用 `FULL_SENSOR` 方向；
  - 正文整宽 Markdown，可滚动；
  - 打开时按点击字符偏移滚动到视口约 1/3 处；
  - 链接交给 `onOpenLink` → WebView 路由。

### 5.7 链接

`MarkdownContent` 的链接行为：

- 自动成链只启用 `Linkify.WEB_URLS`，不启用电话/邮箱匹配（避免数字、日期被误染成链接）。
- 只有 `http` / `https` 且带 host 的 URL 会回调 `onLinkClick`。
- `javascript:` / `file:` / `data:` / `intent:` 等不会外泄。
- `livachat_url://webroot/...` 在渲染前转换为实际服务端 URL。
- hotSlotView / eventListViewer 的 `onOpenLink` 都导航到 `Routes.WEBVIEW`。

### 5.8 锚点定位（eventListViewer）

`anchorRowIndex(rows, anchorTs)`：

1. 优先精确命中：
   - 文本行 `tsMs == anchorTs`；
   - 帧行任意帧 `tsMs == anchorTs`。
2. 否则取第一条“行尾 ts ≥ anchorTs”的行：
   - 文本行：`tsMs >= anchorTs`；
   - 帧行：末帧 `tsMs >= anchorTs`。
3. 仍无 → -1（不滚动）。

定位只执行一次；轮询重取后行列表变化不会重复滚动。高亮独立 2 秒后清除。

---

## 6. 屏幕

### 6.1 hotSlotView（HotSlotScreen）

- 自带 `MylifeTheme` + `Abyss` 背景 + `statusBarsPadding`，不复制首页星空背景。
- 顶栏：
  - 返回按钮；
  - 标题「M月d日 HH:mm」（只显示开始时间，按设备时区与 UI locale 本地化）；
  - pending 时标题右侧「分析中…」。
- 内容区：
  - `Loading` → 加载提示；
  - `Empty` + pending → 「内容分析中…」+ 刷新；
  - `Empty` + 非 pending → 「该时段没有可展示的内容」；
  - `Error` → 错误文案 + 重试；
  - `Data` → `LazyColumn`（ts 升序单流），行使用 `HotSlotRowItem`。
- 列表打开任一覆盖层时禁用用户滚动，避免覆盖层手势漏到列表。
- 覆盖层挂载在外层 `Box` 的直接子节点、`Column` 之后：
  - `AgentJobResultHost`
  - `HotSlotFrameViewer`

### 6.2 eventListViewer（EventListViewerScreen）

- 路由参数 `ts`；取数窗口 `ts ± 10min`。
- 复用 `HotSlotViewModel`、`buildHotSlotRows`、`HotSlotRowItem`、帧查看器与 agent 结果查看器。
- 额外行为：
  - 进入后 `ensureLoadedUntil(ts)` 追击锚点事件；
  - 行解析出锚点后瞬时 `scrollToItem`；
  - 锚点行外层 `Box` 绘制 2dp `MylifeColors.Gold` 边框，2 秒后消退；
  - 追击最多 5 页，超限静默退化。
- 顶栏标题使用 `ts` 生成，同样只显示开始时间；pending 行为与 hotSlotView 相同。

### 6.3 帧查看器（HotSlotFrameViewer）

- 屏内覆盖层，非新路由；`BackHandler` 先关查看器，再退路由。
- 黑底全屏 + `ContentScale.Fit`。
- 手势：
  - 单击（fit 态与放大态）退出；
  - 双指捏合 1x–5x；
  - 放大态单指拖动平移；
  - fit 态单指拖动无动作；
  - 未加载/加载失败仍可单击退出。
- 底部 bar：
  - 黑色 60% 圆角底；
  - caption 单行省略，溢出时金色 `⌄` 展开/收起；
  - caption 为 null 或空白时，bar 退化为圆形保存按钮；
  - bar 区域内的点击不会退出查看器；
  - 右侧 48dp 保存按钮，保存中显示 18dp 转圈，不置灰。
- 保存提示在 bar 上方显示，2 秒 TTL。
- API ≤ 28 需要 `WRITE_EXTERNAL_STORAGE`：
  - 懒请求；
  - 有在飞守卫；
  - 使用 `rememberSaveable` 保存 pending；
  - 拒绝后提示「保存失败：需要存储权限」，可再次重试。
- 保存流程：
  - `repository.downloadFrame(url)` 取字节；
  - `ImageGallerySaver.saveJpegBytes()` 写入相册；
  - 失败/成功分别显示提示。
- 查看器保持事件对象（`HotSlotEvent`），不是列表索引；轮询重取/触底追加不会使其失效。

---

## 7. 本地化与视觉

### 7.1 本地化

- 主要用户可见文案走 string resources：
  - 标题、空态/错误、加载提示、保存提示；
  - `(转录)` / `(原文)` / `(译文)`；
  - 「Agent任务」；
  - 首页标题「时光机」（简体中文值；默认资源为英文 `Time Machine`）。
- 仍有少量硬编码字面量：chip 的 `▸`、caption 展开/收起符号 `⌄`/`⌃`，以及 `HotSlotRowItems` 中 SmartToy 图标的 `contentDescription = "Agent"`。
- 时间/日期：
  - `formatRowTime` / `formatHotSlotTitle` / `formatStart` 均按设备时区；
  - 月日使用 `monthDayText(locale)`；
  - 数字经 `localizeDigits()`（例如阿拉伯语数字）。
- 默认资源语言为英文；简体中文在 `values-b+zh+Hans/`，繁体中文在 `values-b+zh+Hant/`，另有 de/es/fr/hi/id/it/ja/ko/pt/ru/tr/ar。
- `buildHotSlotRows` 接收 locale 与三个后缀字符串，不内嵌中文。

### 7.2 视觉 token

| 用途 | 颜色/样式 |
|---|---|
| 屏底色 | `MylifeColors.Abyss` |
| 顶栏标题/返回 | `MylifeColors.Gold` / `MylifeColors.Dust` |
| chip | `Panel` 底 + `Gold` 字 |
| 分钟分隔 | `MylifeColors.Dust` |
| 用户气泡 | `BrandIndigo` 实底 + `TextPrimary` |
| AI/Agent 气泡 | `colorScheme.surface` + `surfaceVariant` 描边 + `onSurface` |
| Agent 头行 | `TextSecondary` |
| 帧占位/错误 | `MylifeColors.Panel` |
| 锚点高亮 | `MylifeColors.Gold` 2dp 边框 |
| 保存提示 | 黑 75% 底 + `MylifeColors.Star` 字 |

正文样式统一使用 `bodyLarge`；分钟标签 `labelMedium`；Agent 头行 `labelMedium`。

---

## 8. 边界与已知限制

- **数据新鲜度**：hot slot 由 day_stats 预计算，overview 读缓存；新增事件到日卡 hot slot 更新之间存在 stage/round 延迟。
- **overview 时区**：请求时区可能被重定向到“最新时区”，因此日卡 `date` 与客户端请求时区不保证一致；hotSlotView 已通过 slot 毫秒现推取数日规避。
- **pending 收敛**：`hasPendingAnalysis()` 只看已加载窗口的最新 400 天，深历史日卡可能不参与轮询/收敛。
- **分页游标**：`/timeline` 使用 `date|offset`，录制中并发插入可能造成跨页重复或漏事件；客户端按 `event_ref` 去重，漏由 pending 轮询触发的首屏重取兜底（只追到首个窗口内页）。未改为 keyset cursor。
- **contract 测试缺口**：`/timeline` “跨页 ts 非递减”目前没有专项契约测试；客户端停止翻页依赖该隐式性质。
- **帧查看器保存取消**：取消对下载严格生效；若取消恰好落在 `saveJpegBytes` 写盘期间，文件可能已写入，覆盖层已关闭，用户不可见。
- **表格判定**：`AgentJobResultText` 的表格检测是测量前启发式；围栏代码块内出现分隔行形态、冒号位置非法等可能被误判为表格，表现为多占宽度，不影响正文。
- **无障碍**：气泡去掉了「用户/AI助理」文案，用户/AI 气泡的屏幕阅读器只能读到正文；Agent 气泡还会读到 SmartToy 图标的硬编码 `Agent` 描述。气泡本身仍没有区分说话人的 `contentDescription`。
- **行 key**：依赖 `event_ref`（或 ts 回退）；若未来 BFF 不透出 `event_ref` 且同一毫秒多事件，LazyColumn key 可能冲突。
- **agent 结果折叠**：长正文行内只显示前 24 行，剩余内容通过「查看全文」进入全屏查看器。

---

## 9. 代码锚点

### BFF / lvdata

| 能力 | 位置 |
|---|---|
| hot slot 派生 | `tools/lvdata-mcp/src/lvdata_mcp/summary/day_stats.py` → `derive_hot_slots` |
| day_stats 组装 | 同文件 → `derive_day_stats` |
| 常量 | `tools/lvdata-mcp/src/lvdata_mcp/consts.py` |
| day_stats stage | `tools/lvdata-mcp/src/lvdata_mcp/summary/day_stats.py` → `DayStats.run` |
| round 顺序 | `tools/lvdata-mcp/src/lvdata_mcp/watcher.py` → `ROUND_STAGES` |
| overview 读取/分页 | `services/bff/timeline/service.py` → `build_overview` / `_to_hot_slots` |
| overview 时区 | `services/bff/timeline/service.py` → `resolve_overview_tz` |
| timeline 读取/分页 | `services/bff/timeline/service.py` → `build_timeline` |
| API 参数 | `services/bff/timeline/router.py` |
| 响应 schema | `services/bff/common/schemas.py` |

### Android

> 路径相对 `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/`；测试路径相对 `apps/android/LivaChatApp/`。

| 能力 | 位置 |
|---|---|
| 日卡聚合 | `ui/screens/mylife/timeline/PeriodAggregator.kt` |
| chip | `ui/screens/mylife/timeline/components/HotSlotChips.kt` |
| 日卡 | `ui/screens/mylife/timeline/components/PeriodCard.kt` |
| 导航模型 | `ui/screens/mylife/timeline/TimelineViewModel.kt` |
| 首页入口 | `ui/screens/mylife/MylifeHomeScreen.kt` |
| 路由 | `ui/navigation/Routes.kt`、`ui/navigation/NavGraph.kt` |
| 取数 | `data/repository/MylifeRepository.kt` → `hotSlotEvents` |
| DTO | `data/remote/mylife/dto/MylifeDtos.kt` |
| hotSlotView | `ui/screens/mylife/hotslot/HotSlotScreen.kt` |
| eventListViewer | `ui/screens/mylife/hotslot/EventListViewerScreen.kt` |
| 状态/VM | `ui/screens/mylife/hotslot/HotSlotUiState.kt`、`HotSlotViewModel.kt` |
| 行模型 | `ui/screens/mylife/hotslot/HotSlotRows.kt` |
| 行组件 | `ui/screens/mylife/hotslot/components/HotSlotRowItems.kt` |
| 帧查看器 | `ui/screens/mylife/hotslot/HotSlotFrameViewer.kt` |
| agent 正文/覆盖层 | `ui/components/AgentJobResultText.kt`、`ui/screens/conversation/AgentJobResultHost.kt`、`AgentJobResultOverlay.kt` |
| Markdown/链接 | `ui/components/MarkdownContent.kt` |

### 关键测试

- 派生口径：`tools/lvdata-mcp/tests/test_day_stats.py`（gap 边界、合并、golden）。
- BFF 透传/分页：`services/tests/unit/bff/test_timeline_api.py`。
- Android JVM：
  - `src/test/.../mylife/hotslot/HotSlotRowsTest.kt`
  - `src/test/.../mylife/hotslot/HotSlotViewModelTest.kt`
  - `src/test/.../mylife/timeline/PeriodAggregatorTest.kt`
  - `src/test/.../mylife/timeline/HotSlotChipLabelTest.kt`
  - `src/test/.../data/repository/MylifeRepositoryTest.kt`
- Android 仪器：
  - `src/androidTest/.../mylife/hotslot/HotSlotComponentsTest.kt`
  - `src/androidTest/.../mylife/hotslot/EventListViewerScreenTest.kt`
