# Timeline Topic 增强（Android）当前实现

> 范围：day_summary topics 的生成与透出、Android 时间轴卡片展示、topic 点击搜索、搜索命中进入 eventListViewer。
> 本文只记录当前代码的最终行为与契约，不含版本沿革；相关主题分散在下列文档：
> `docs/bff_search_fts.md`（FTS 检索）、`docs/android_timeline_homepage.md`（首页与搜索 UI）、
> `docs/android_timeline_hotslot.md`（hotSlotView / 行组件）、`docs/day_summary.md`（topics 生成）、
> `docs/bff_service_interfaces.md` §8（overview 分页）。

---

## 1. 术语与链路

### 1.1 术语

- **topic**：day_summary 对一天内容生成的日级主题，wire 形状为对象 `{topic, search_words}`。
- **topic**（展示名）：卡片 chip 上显示的概括名。
- **search_words**（搜索词）：从事件摘要中直接提取的原文连续片段，用于保证「点 topic → 搜到重点事件」。
  缺省/空白时客户端回退用 topic 名搜索。

### 1.2 端到端链路

```
day_summary LLM
  → day_summary.doc.topics [{topic, search_words}]
  → bff /overview（仅 status=ok 透出）→ OverviewDay.topics [DayTopicItem]
  → Android OverviewDayDto.topics [DayTopicDto]
  → PeriodAggregator（去重/上限/权重筛选）→ PeriodCard → TopicChipRow
  → 点击 chip → /search（FTS）→ SearchHit
  → EventListViewer（命中时刻前后各 10 分钟）
```

### 1.3 责任边界

- **lvdata-mcp**：生成 day_summary topics；维护 `search_docs` FTS 索引。
- **bff**：归一化并透出 topics（`/overview`）；提供 FTS 检索（`/search`）。
- **Android**：卡片聚合与展示、topic 点击、搜索 UI、EventListViewer 定位/高亮。

### 1.4 非目标

- 不提供 topic 详情页或主题聚合页；topic 只作为搜索入口。
- 不把 `audio` / `window` 纳入搜索结果或 EventListViewer 行模型（当前展示口径为五类）。
- 不做客户端搜索缓存或离线搜索；每次查询直通 `/search`。

### 1.5 与搜索相关的边界事实

- **可搜 ⊆ 可展示**：搜索结果只含五类可展示事件 `chat / transcribe / translate / agent_job / video`；
  `topic`、`audio`（transcript）、`window` 内容不会成为搜索命中。
- 检索走 FTS 索引（`events.summary` 单列，bm25 相关度序），不存在全表子串扫描。
- topic 是日级产物，没有事件锚；点 topic 等价于**用 search_words 搜索事件**，不会定位日卡。
- 命中的事件若本身在五类展示口径内，进入 EventListViewer 后可见；不在口径内的事件不会被搜索产出。

---

## 2. 数据源概览

| 数据 | 产生 | 透出 / 检索 | 客户端消费 |
|---|---|---|---|
| topics | day_summary LLM（`tools/lvdata-mcp/src/lvdata_mcp/summary/day.py`） | bff `/overview`（`services/bff/common/topics.py` 归一化） | `DayTopicDto` → `PeriodAggregator` → `PeriodCard` |
| 事件检索 | `events.summary` → `search_docs` FTS5 索引（`tools/lvdata-mcp/src/lvdata_mcp/index.py`） | bff `/search`（`services/bff/search/service.py`） | `MylifeRepository.search` → `SearchResultList` |

topics 的生成/归一化/透出契约见 §10；搜索契约见 §3.2.2。

---

## 3. 卡片、topic 搜索与 EventListViewer

### 3.1 卡片 topics 展示

#### 聚合规则

`PeriodAggregator` 输入为 overview 当前已加载窗口内的日卡
（`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/mylife/timeline/PeriodAggregator.kt`）：

```kotlin
// 组内日倒序；按 topic 名首见去重（保留数据序，detail 随同）
val merged = mergeTopics(daysByTime)

topics = if (granularity == DAY || merged.size <= MAX_TOPICS) merged
         else merged.shuffled(Random(key.hashCode().toLong()))
                  .sortedByDescending { weights[it.topic] ?: 0 }  // 权重 = 期内出现次数
                  .take(MAX_TOPICS)
```

- `MAX_TOPICS = 50`，日卡不设上限。
- 日卡：day_summary 给出的 topic **全量渲染**（prompt 约定 ≤5，代码不做卡片截断）。
- 周/月/年卡：
  - 去重后 ≤50：保持「日倒序首见」原序，不重排；
  - 去重后 >50：按**权重（期内出现次数，每次 +1）稳定降序**取前 50；
  - 同权重内以卡片 key 为种子随机（同一窗口下跨重组/轮询刷新集合与顺序稳定）；
  - 新加载更早日卡会改变权重与集合，卡片 topics 会随窗口变化重新计算。
- 去重键是 **topic 展示名**：同名主题跨天只保留首见条目的 `search_words`。
- 卡片 topics 只覆盖**当前已加载窗口**；overview 尚未加载的更早日不参与聚合。

#### 卡片与 chip UI

- 卡片内容顺序：pending 提示 → 标题 → hot slot chips（日卡）+ 音频入口 → summary → 图片区 → topics
  （`.../timeline/components/PeriodCard.kt:70-161`）。
- topics 与上一区块间距为 `Spacing.sm`（8dp）（`PeriodCard.kt:156-161`）。
- chip 组为 `TopicChipRow`（`.../timeline/components/TopicChipRow.kt:30-60`）：
  - `FlowRow` 自动换行；水平/垂直间距均为 `Spacing.xs`（4dp）；
  - 视觉：透明底 + `Line` 1dp 描边 + `Dust` 字，圆角 50%；内容 padding 水平 8dp、垂直 2dp；
  - 可点与纯展示同款视觉；可点时在同一视觉上叠加 `Modifier.clickable`（手拼 `clip + clickable`，
    不使用 M3 `Surface`，避免 48dp 最小触控撑高行距）。
- `PeriodCard.onTopicClick` 默认 `null`（纯展示）；时间轴传入 `viewModel::onTopicSearch`
  （`TimelineScreen.kt:256-264`）。
- 月/年卡本体可点击下钻；topic chip 的 clickable 先消费点击，不会触发卡片本体下钻
  （`PeriodCard.kt:164-172`）。

### 3.2 topic 点击 → 搜索

#### 3.2.1 客户端行为

- `onTopicSearch(topic)`：`query = searchWords?.takeIf { it.isNotBlank() } ?: topic`
  （`.../timeline/TimelineViewModel.kt:274-279`），随即进入搜索模式。
- 搜索流（`TimelineViewModel.kt:169-176,283-352`）：
  - `query` 非空 → 350ms 防抖；空串立即发射；
  - `distinctUntilChanged` + `flatMapLatest`，新 query 自动取消上一轮请求；
  - `MIN_QUERY_LENGTH = 2`；不足 2 字符落 `Idle`，不发请求；
  - 键盘搜索键 / 搜索错误重试走 `searchImmediate()`，跳过防抖；
  - 分页由 `onSearchLoadMore()` 用 `next_cursor` 追加。
- 搜索模式（`TimelineScreen.kt:153-156,180-197,335-362`）：
  - `query` 非空即用搜索结果整体替换浏览列表（粒度选择器隐藏）；
  - 返回键只消费一次并清空 query，不退出时光机路由；
  - Idle 显示「至少输入 2 个字」，Loading/Empty/Error 各有独立状态；
  - 搜索结果按 `date` **首现顺序聚合**（同日命中不再相邻也只有一个日头），组内保持服务端顺序；
  - 行 = kind 图标 + `HH:mm` + snippet（2 行截断）；snippet 中整串 query 命中时金色高亮，
    未命中则原样展示（放宽/多词命中的客户端兜底）。

#### 3.2.2 bff `/search` 契约

- `GET /api/mylife/search?q=&limit=&cursor=&timezone=`；`limit` 1..200，默认 50；cursor 为 `0|offset`
  （`services/bff/search/router.py:11-32`）。
- 检索源 = lvdata-mcp 的 `search_docs` FTS5 派生索引；索引文本为 **`events.summary` 单列**
  （`tools/lvdata-mcp/src/lvdata_mcp/index.py:98-113`）。
- **结果集 = 展示口径五类**：`chat` / `transcribe` / `translate` / `agent_job` / `video`
  （`services/bff/search/service.py:28`；与 `tools/lvdata-mcp/src/lvdata_mcp/consts.py:41` 的
  `HOT_SLOT_TYPES` 一致，契约测试守护）。
  - `topic` / `audio`（transcript）/ `window` 内容**不进入搜索结果**（可搜 ⊆ 可展示）。
- 查询：
  - CJK 按重叠 bigram 预切分，查询词一律加引号（`index.py:27-70`），无 FTS5 语法歧义；
  - 停用词 / 纯标点切词后无 token → 空结果，不落 FTS（`search/service.py:94-102`）；
  - 索引表缺失 → 空结果 + warning，无回退；表存在但为空 → 空结果（`search/service.py:104-107`）；
  - 设备白名单与 timeline 语义一致（`search/service.py:109-111`）。
- 排序与分页：bm25 相关度升序（同分按 ts 倒序 + event_ref 稳定）；`total` + `0|offset` cursor
  （`search/service.py:113-130`；`store.search_fts` 的 SQL 排序）。
- 放宽回退：精确查询 0 命中时，改用 bigram OR 重查一次（`search/service.py:114-122`）。
- snippet：原文（`orig_text`）中整串 query 优先、否则首个命中词定位，前后约 120 字符
  （`search/service.py:35-59`）。
- `SearchHit{date, ts_ms, kind, snippet}`；FTS 产出的命中均带 `ts_ms`，客户端仍对 null 做 no-op 防御
  （`services/bff/common/schemas.py:109-115`；`MylifeDtos.kt:175-181`）。

#### 3.2.3 结果渲染

- `kind` 图标：`chat` 💬、`transcribe` 📄、`translate` 🌐、`agent_job` 🤖、`video` 🖼️，
  未知 kind 兜底 📍（`SearchResultList.kt:168-175`）。
- 高亮由客户端按整串 query 在 snippet 中查找；放宽命中（bigram OR）或多词非相邻命中时
  snippet 可能不含整串 query，此时不高亮（不报错）。
- `HH:mm` 由客户端按设备本地时区格式化，数字按 UI locale 本地化；日头直接使用服务端
  `SearchHit.date`（Android 请求未传 `timezone`，取 bff 默认/配置时区）。
- 搜索无缓存；新 query 到达即取消上一轮结果（`flatMapLatest`）。

### 3.3 搜索命中 → EventListViewer

#### 3.3.1 点击分派与 pending

- `TimelineViewModel.onSearchHitClick(hit)`：`tsMs == null` → no-op；否则发射
  `EventListNavigation(tsMs, pending)`（`TimelineViewModel.kt:263-272`）。
  - 不按 kind 分支：所有带 ts 的搜索命中（五类事件）统一进入 EventListViewer。
- `pending = overview 当前窗口内该本地日（localDateOf(tsMs)）的 hasPending`；无该日 → false。
  - `localDateOf` 是 hotSlot 取数与搜索命中归属的共享口径（`MylifeRepository.kt:417`），
    不得改用 UTC 日。
  - 由于 overview 分页，命中日不在已加载窗口时 pending 为 false（当前行为）。

#### 3.3.2 路由

- `mylife/events?ts={ts}&pending={pending}`；`ts` 为 Long，`pending` 为可空 String
  （`Routes.kt:21-23,47-49`）。
- NavGraph 注册 `LongType + nullable StringType`，Screen 回调 `onOpenLink` 跳 webview
  （`NavGraph.kt:195-211`）。
- Screen 文件：`ui/screens/mylife/hotslot/EventListViewerScreen.kt`（与 VM/Rows/RowItems/FrameViewer 同包）。

#### 3.3.3 EventListViewer

**窗口与取数**

- `EVENT_WINDOW_MS = 10 * 60_000`；`startMs = ts - 10min`，`endMs = ts + 10min`
  （`EventListViewerScreen.kt:63-64,90-92`）。
- 复用 `HotSlotViewModel.load(startMs, endMs, pending)`，与 hotSlotView 完全同源
  （`HotSlotViewModel.kt:75-87`）。
- `MylifeRepository.hotSlotEvents`（`MylifeRepository.kt:341-362`）：
  - `/timeline` + `types = chat,agent_job,video,transcribe,translate` + `limit = 500`；
  - 取数日由 ms 现推（设备当前时区），窗口过滤**含端点**；
  - 空文本行、无帧 video 剔除；页内 ts 升序；
  - `reachedWindowEnd` = 本页出现越过窗口上界的事件（用于停止翻页）。
- VM 跨页按 `event_ref` 去重；overview 实例变化（pending 轮询、刷新等）会重取首页替换列表
  （`HotSlotViewModel.kt:57-69,96-119`）。

**行模型**

`buildHotSlotRows`（`.../hotslot/HotSlotRows.kt:55-118`）把五类事件映射为行：

- 连续 `video` 每 3 张一行（`chunked(3)`），文本行打断分组；
- 文本行（气泡）：
  - `chat`：role user/assistant 白名单映射左右气泡；
  - `transcribe`：正文加 `(转录)` 后缀；
  - `translate`：按 role 加 `(原文)` / `(译文)` 后缀；
  - `agent_job`：`AGENT` 气泡，正文走 Markdown/链接渲染（不受 role 白名单限制）；
  - `chat` / `transcribe` / `translate` 的 role 非 user/assistant → 整行丢弃；
- 行时间标签 = 分钟桶首行的 `HH:mm`，同分钟其余行 `timeLabel = null`；
- `HotSlotRow.Text` 携带原始 `tsMs`（锚点定位用）；帧行通过 `frames` 逐帧判断；
- 行 key：文本 `t-{eventRef ?: tsMs}`，帧 `f-{首帧 eventRef ?: tsMs}`。

行组件：`FrameRowItem`、`HotSlotRowItem`（分派）、私有 `BubbleRowItem`
（`.../hotslot/components/HotSlotRowItems.kt:59,103,141`）。
覆盖层：帧点击 → `HotSlotFrameViewer`（全屏 + 保存到相册）；agent_job 行点击 →
`AgentJobResultHost`（全屏结果，含未命中守卫）。二者互斥，打开时列表禁滚。

**锚点定位与追击**

- 锚点行解析 `anchorRowIndex(rows, anchorTs)`（`HotSlotRows.kt:128-147`）：
  1. 精确命中优先：`Text.tsMs == anchorTs`，或帧行任意帧 ts == anchorTs（整行高亮）；
  2. 否则退化：首个「行尾 ts ≥ anchorTs」的行（Text 为其 ts，Frames 为末帧 ts）；
  3. 仍无 → -1，不滚动（追击未覆盖或窗口内无更晚行）。
- 定位只做一次：`scrollToItem(index)` **瞬时跳转**（重度日锚点可能深至百余行），
  置 `located = true` 后不再重复滚动（`EventListViewerScreen.kt:134-145`）。
- 高亮：行容器金色描边 2dp + 圆角 8dp，2s 后自动清除；清除 effect 独立于 rows 变化，
  防止续页/重取重启定位 effect 吞掉 delay（`EventListViewerScreen.kt:146-152,240-258`）。
- **锚点追击**（`HotSlotViewModel.kt:167-206`）：
  - 终止判据在事件层：`events.any { it.tsMs >= anchorTs } || exhausted`；
  - Screen 以 `LaunchedEffect(uiState.state, tsMs)` 响应式触发，内部 `chasing/loadingMore/exhausted`
    幂等 guard；轮询重取清空列表后 effect 自动重入；
  - 硬上限 `MAX_CHASE_PAGES = 5`，按**尝试**计数（请求前 +1，失败也消耗预算）；
  - 预算只计「窗口内已有内容之后」的追击页；`fetchPages` 首段跳过窗口前整页不计；
  - `reloadFirstPage` 清零追击预算与 `chasing`；
  - 追击期间阻塞触底 `loadMore()`（二者竞争同一 `nextCursor`）；
  - 失败静默保留已加载页，定位退化由 Screen 兜底。

**标题、状态与返回**

- 标题：`formatHotSlotTitle(tsMs, locale)` → 「M月d日 HH:mm」（设备本地时区，数字按 locale 本地化）
  （`HotSlotRows.kt:149-163`）。
- 状态：Loading / Error（可重试）/ Empty（pending 时显示「分析中…」空态）/ Data；
  顶栏 pending 显示「分析中…」。
- 触底续页使用「新鲜触发」判定：`lastAttemptSize` + `leftEndSinceAttempt`，
  失败后不会自动重试风暴；滚离底部再回来或上一页成功追加后重新武装
  （`EventListViewerScreen.kt:110-127`；同款实现见 HotSlotScreen / FrameGrid / AudioGalleryScreen）。
- 覆盖层生命周期：overview 轮询重取保持覆盖层打开；`load()`（进入窗口/参数变化）与
  `retry()`（手动重试）会先清空覆盖层（`HotSlotViewModel.kt:75-93,285-304`）。
- 返回：无覆盖层 → 退出路由；覆盖层打开 → 先由覆盖层自己的 BackHandler 关闭。

---

## 4. 代码索引

| 层 | 文件 | 职责 |
|---|---|---|
| 生成 | `tools/lvdata-mcp/src/lvdata_mcp/summary/day.py` | DAY_SYSTEM_PROMPT、_normalize |
| bff 归一化 | `services/bff/common/topics.py` | topic_items / topic_names |
| bff 透出 | `services/bff/timeline/service.py` | overview 分页、DayTopicItem 构造 |
| bff 检索 | `services/bff/search/service.py` | FTS 查询、五类过滤、放宽回退、snippet |
| FTS 索引 | `tools/lvdata-mcp/src/lvdata_mcp/index.py` | search_docs 建表/重建/查询 |
| Android DTO | `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/data/remote/mylife/dto/MylifeDtos.kt` | DayTopicDto / OverviewDto / SearchHitDto |
| Android 数据 | `.../data/repository/MylifeRepository.kt` | overview 窗口、hotSlot 取数、localDateOf |
| 聚合 | `.../ui/screens/mylife/timeline/PeriodAggregator.kt` | mergeTopics / selectTopics / 权重 |
| 卡片 | `.../ui/screens/mylife/timeline/components/PeriodCard.kt` | 卡片内容序、topics 区 |
| chip | `.../ui/screens/mylife/timeline/components/TopicChipRow.kt` | 描边 chip、可点模式 |
| 时间轴 VM | `.../ui/screens/mylife/timeline/TimelineViewModel.kt` | onTopicSearch、搜索流、命中导航、pending |
| 时间轴 Screen | `.../ui/screens/mylife/timeline/TimelineScreen.kt` | 接线、搜索模式、Idle 文案 |
| 搜索结果 | `.../ui/screens/mylife/timeline/components/SearchResultList.kt` | 日聚合、kind 图标、高亮 |
| EventList | `.../ui/screens/mylife/hotslot/EventListViewerScreen.kt` | ±10min 窗口、锚点定位/高亮、覆盖层 |
| EventList VM | `.../ui/screens/mylife/hotslot/HotSlotViewModel.kt` | 取数、翻页、追击、轮询重取 |
| 行模型 | `.../ui/screens/mylife/hotslot/HotSlotRows.kt` | buildHotSlotRows、anchorRowIndex、标题 |
| 路由 | `.../ui/navigation/Routes.kt`、`.../ui/navigation/NavGraph.kt` | `mylife/events` |

---

## 5. 边界与当前行为

| # | 场景 | 当前行为 |
|---|---|---|
| 1 | 日卡 topics | 全量渲染（prompt 约定 ≤5，卡片不截断） |
| 2 | 周/月/年卡 topics | 日倒序首见去重；≤50 原序，>50 权重优先取 50 |
| 3 | 点 topic chip | query = `search_words`（空白/缺省回退 topic），进入搜索模式 |
| 4 | query < 2 字符 | `Idle`，显示「至少输入 2 个字」，不发请求 |
| 5 | 点带 ts 的搜索命中 | 进入 EventListViewer（ts ±10min）；pending 取当前窗口该日 hasPending |
| 6 | 命中 ts 为 null | no-op（FTS 命中恒有 ts；防御性保留） |
| 7 | 锚点事件不在行模型内（role 白名单外/空文本/无帧 video） | 无精确锚点 → 首个行尾 ts ≥ anchor 的行；仍无 → 不滚动 |
| 8 | 窗口内无可展示事件 | Empty；pending 时显示分析中空态 |
| 9 | 窗口跨午夜/跨日 | 取数日由 ms 现推，最多覆盖两天 |
| 10 | 重度日：锚点在后续页 | 追击最多 5 页（尝试计数）后静默退化 |
| 11 | 轮询重取 / 手动重试 | 轮询：首页替换、预算清零、响应式重入、覆盖层保持；`load`/`retry`：另清空覆盖层 |
| 12 | 触底续页失败 | 保留已加载页，不自动重试；离开底部再回来或列表增长后重新武装 |
| 13 | 查询 audio/window/topic 内容 | 不出现在搜索结果（仅五类可展示事件可搜） |
| 14 | 同窗口内 >50 的周/月/年卡刷新 | chip 集合与顺序稳定（权重 + 卡片 key 种子） |
| 15 | 命中日不在已加载 overview 窗口 | pending = false（当前窗口口径） |
| 16 | 点月/年卡上的 topic chip | chip 的内层 clickable 先消费，不触发卡片本体下钻 |

---

## 6. 测试覆盖

**Android JVM**

- `PeriodAggregatorTest`：日卡 topics 全量（超 5 不截断）、周卡 ≤50 原序去重、
  年卡 >50 权重优先且同 key 稳定、恰 50 不重排。
- `TimelineViewModelTest`：`onTopicSearch` 置 query / search_words 优先与空白回退、
  搜索防抖 350ms、<2 字符 Idle、分页追加、命中导航 EventList 携带 ts 与当日 pending、
  命中日无 overview 条目时 false、ts 为空不导航。
- `HotSlotRowsTest`：文本行携带 tsMs、锚点解析（精确/帧行内/被剔除退化）、
  分钟桶与 role 白名单。
- `HotSlotViewModelTest`：锚点在后续页、锚点已覆盖不再追击、窗口终止不发请求、
  5 页上限、失败消耗预算、追击与 loadMore 互斥、轮询重取后预算清零、
  首页整页落在窗口前时继续追击、overview 重取替换列表。
- `SearchResultListGroupingTest`：同日命中不相邻时聚合为单一日头。

**Android 仪器**

- `EventListViewerScreenTest`：锚点在后续页追击并高亮、帧查看器开合、空窗口空态、
  后续页失败不自动重试、返回回调、agent_job 结果覆盖层开合。
- `HotSlotComponentsTest`：topic chips 全量渲染并按 chip 回调 topic；
  hot slot chip 只展示开始时间且点击携带 pending。

**bff pytest**

- `test_search_api.py`：五类命中与 kind、topic/audio/window 排除、bm25 相关度优先、
  同分 ts 倒序、多词隐式 AND、ASCII 整词、停用词/纯标点/索引缺失空结果、
  放宽回退、分页不重叠、设备过滤、时区、`SEARCH_TYPES == HOT_SLOT_TYPES`。
- `test_timeline_api.py::test_overview_topics_from_day_summary`：三形态 topics
  （对象 search_words / legacy 字符串 / legacy detail）透出与 detail 恒 null。

---

## 7. 本地化与时间显示

- 标题「M月d日 HH:mm」与行时间 `HH:mm` 均用设备本地时区；月份/日期文案按 UI locale 生成
  （`HotSlotRows.kt:149-163`；`MylifeDateLabels.kt`）。
- 阿拉伯语下 `localizeDigits` 将标题、行时间、搜索时间、周范围等数字转为阿拉伯-印度数字。
- 搜索占位、搜索状态、结果总数、语音段数走资源/复数规则；周/月/年卡空 summary 的
  「共 N 天」在 `PeriodAggregator` 内按 locale 分支生成（含 ru/ar 等复数形，`docs/android_multilingual.md`）。
- topic chip 文案直接使用服务端返回的 topic 展示名，不做本地化。

---

## 8. 已知行为与限制

- **overview 分页口径**：卡片 topics 与 pending 只基于已加载窗口；更早日卡加载后会重算。
- **搜索索引依赖 watcher**：`search_docs` 由 lvdata-mcp 的 `reindex` stage 维护；watcher 未运行时
  新事件不会进入索引，`/search` 可能是空结果或陈旧结果。
- **>50 卡片仍可能很长**：周/月/年卡最多 50 个 chip，未提供折叠；TalkBack 节点较多。
- **topic 去重键为展示名**：同名主题跨天合并，沿用首见条目的 `search_words`；不同搜索词不会分别保留。
- **legacy topics**：字符串或无 `search_words` 的存量行，点击用 topic 名搜索，召回可能低于新数据。
- **搜索不覆盖 audio/window/topic**：这些内容查询返回空；属于设计边界，不是故障。
- **高亮可能缺省**：放宽回退/多词非相邻命中时 snippet 不含整串 query，客户端不高亮。
- **agent_job 搜索字段与展示字段**：FTS 只索引 `events.summary`；查看器按
  `text → summary → agent_name` 取首个非空。若 payload.text 非空且与 summary 不同，
  snippet 可能来自 summary 而气泡展示 text（正常写入方两者一致）。
- **锚点追击有界**：超过 5 个追击页后静默退化到「首个行尾 ≥ anchor」或「不滚动」。
- **触底续页失败不自动重试**：需滚离阈值区再回来，或上一页成功追加后才重新武装
  （“新鲜触发”判定，见 `EventListViewerScreen.kt:110-127`）。
- **pending 窗口限制**：命中日不在已加载 overview 窗口时按 false 处理。
- **搜索日头与本地日口径不同**：日头来自 bff 的 `SearchHit.date`（服务端时区），
  而 pending 与 EventList 取数日按设备本地时区；设备时区与 bff 时区不一致时，
  同一条命中的日头与 pending 归属可能落在不同日界。

---

## 9. 验证入口

### 9.1 Android

```bash
./gradlew :LivaChatApp:testDebugUnitTest

# 仪器（跑完会卸载被测 app，数据清空）
./gradlew :LivaChatApp:connectedDebugAndroidTest
```

仪器执行前需保持设备解锁（`adb shell svc power stayon true`），否则可能全类
"No compose hierarchies found"（详见 `docs/android_timeline_hotslot.md` §8.2）。

### 9.2 bff / lvdata-mcp

```bash
# 在仓库根激活项目虚拟环境；不要用裸 python
# （版本漂移会造成假失败：如 test_gzip.py 依赖 starlette 1.6.0）
source .venv/bin/activate

(cd services && pytest tests/unit/bff)
(cd tools/lvdata-mcp && pytest tests)
```

---

## 10. topics 数据契约

### 10.1 day_summary 生成

- `DAY_SYSTEM_PROMPT` 要求输出三键 `{summary, events, topics}`，其中 topics 为对象数组
  （`tools/lvdata-mcp/src/lvdata_mcp/summary/day.py:119-141`）。
- topics 规则：
  - `topic` 是主题名，可概括；
  - `search_words` 必须是从事件摘要里直接提取的原文连续片段，不做修改/拼接；
  - 不输出事件类型标签（如 chat、translate、agent_job、video frame），不照抄整句；
  - **最多 5 个，可以为空**。
- **最多 5 个是 prompt 约定，代码不强制截断**。`_normalize` 只做形态归一化与空/畸形条目过滤
  （`day.py:552-577`）：
  - 对象 `{topic, search_words}` → 保留；空串/空白 `search_words` → `None`；
  - legacy 字符串 → `{topic: str, search_words: None}`；
  - 缺 topic / 空串 / 非容器条目丢弃。
- LLM 调用关闭思考（`enable_thinking=False`，`day.py:534-535`），避免 JSON 输出被截断。

### 10.2 bff 归一化与 overview 透出

- `topic_items()` 是唯一的读侧归一化入口（`services/bff/common/topics.py:9-23`）：
  - 对象 `{topic, search_words}` → `(topic, search_words)`；
  - legacy 字符串 → `(topic, None)`；
  - legacy `{topic, detail}` → 只取 topic，detail 丢弃；
  - 空 topic 条目丢弃。
- `topic_names()` 是 `topic_items()` 的投影（`topics.py:25-26`）。
- `/overview` 逐日构造 `DayTopicItem(topic, search_words)`（`services/bff/timeline/service.py:117-127`）：
  - **仅 `status == "ok"` 的 day_summary 透出**（failed/pending 行不透出陈旧内容）；
  - 服务端不做数量截断；
  - `DayTopicItem.detail` 字段保留但读侧不再取值，响应恒 `null`（`services/bff/common/schemas.py:23-33`）。
- overview 为 keyset 分页（`timeline/service.py:78-112`）：响应 `days` 是**本页**，
  `next_cursor` 非空表示还有更早的日，`total` 为该时区天卡总数（`schemas.py:73-80`）；
  Android 客户端把多页合并为**已加载窗口**（`MylifeRepository.kt:102-134`）。

### 10.3 Android 数据层

- `OverviewDayDto.topics: List<DayTopicDto>`，`DayTopicDto{topic, detail?, searchWords?}`
  （`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/data/remote/mylife/dto/MylifeDtos.kt:35-47`）。
- `MylifeApi.search(q, limit = 50, cursor = null)` 直通 `/search`，无缓存
  （`.../data/remote/mylife/MylifeApi.kt:92-93`；`.../data/repository/MylifeRepository.kt:191`）。
- overview 同样走分页窗口（`MylifeRepository.kt:82-118`）；窗口变化会触发时间轴重聚合。
