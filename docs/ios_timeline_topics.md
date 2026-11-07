# iOS 时光机 Topic 与搜索设计

**状态**：已实现（2026-10-22）
**UI/UX 基线**：`docs/android_timeline_topics.md` 与 Android 现行代码（`PeriodAggregator.kt`、`TopicChipRow.kt`、`TimelineViewModel.kt`、`SearchResultList.kt`）。
**共享基础**：`docs/ios_timeline_arch.md`。
**范围**：day_summary topics 的客户端聚合与展示、topic 点击搜索、搜索结果渲染、命中进入 eventListViewer 的参数与 pending。
**不在范围**：topic 的后端生成与 FTS 索引实现（`docs/day_summary.md`、`docs/bff_search_fts.md`）；eventListViewer 的完整行为（`ios_timeline_hotslot.md`）。

---

## 1. 术语与链路

### 1.1 术语

- **topic**：day_summary 对一天内容生成的日级主题，wire 形状 `{topic, search_words}`。
- **topic 展示名（topic）**：卡片 chip 上显示的概括名，服务端返回，不做本地化。
- **search_words**：从事件摘要中直接提取的原文连续片段，用于保证「点 topic → 搜到重点事件」；缺省/空白时回退用 topic 名搜索。

### 1.2 链路

```
day_summary LLM
  → day_summary.doc.topics [{topic, search_words}]
  → BFF /overview（仅 status=ok 透出）→ OverviewDay.topics[DayTopic]
  → iOS TimelineHomeViewModel（周期聚合）
  → PeriodCard → TopicChipRow
  → 点击 chip → 搜索模式（/search FTS）→ SearchHit
  → eventListViewer（命中时刻 ±10 分钟）
```

### 1.3 责任边界

- **lvdata-mcp**：生成 topics；维护 `search_docs` FTS 索引。
- **BFF**：归一化并透出 topics；提供 FTS 检索。
- **iOS**：卡片聚合与展示、topic 点击、搜索 UI、命中跳转参数。
- **eventListViewer**：按 ts ±10min 取数、锚点定位与高亮（见 hotslot 文档）。

### 1.4 非目标

- 不提供 topic 详情页或主题聚合页；topic 只作为搜索入口。
- 不把 `audio` / `window` 纳入搜索结果或 eventListViewer 行模型。
- 不做客户端搜索缓存或离线搜索。
- 不新增搜索端点/索引；完全复用 BFF `/search`。

---

## 2. 数据契约

### 2.1 day_summary topics 生成

- `DAY_SYSTEM_PROMPT` 要求输出 `{summary, events, topics}`；topics 为对象数组（`tools/lvdata-mcp/src/lvdata_mcp/summary/day.py`）。
- prompt 规则：
  - `topic` 是主题名，可概括；
  - `search_words` 必须是从事件摘要里直接提取的原文连续片段，不修改/拼接；
  - 不输出事件类型标签（chat/translate/agent_job/video frame），不照抄整句；
  - 最多 5 个，可以为空。
- **最多 5 个是 prompt 约定，代码不强制截断**。`_normalize` 只做形态归一化与空/畸形条目过滤：
  - `{topic, search_words}` → 保留；空白 `search_words` → `nil`；
  - legacy 字符串 → `{topic, search_words: nil}`；
  - 缺 topic / 空串 / 非容器条目丢弃。
- LLM 调用关闭思考，避免 JSON 截断。

### 2.2 BFF 归一化与透出

- `topic_items()` 是唯一读侧归一化入口（`services/bff/common/topics.py`）：
  - 对象 `{topic, search_words}` → `(topic, search_words)`；
  - legacy 字符串 → `(topic, nil)`；
  - legacy `{topic, detail}` → 只取 topic；
  - 空 topic 丢弃。
- `/overview` 逐日构造 `DayTopicItem(topic, search_words)`，**仅 `status == "ok"` 的 day_summary 透出**；服务端不截断数量。
- `DayTopicItem.detail` 保留字段但读侧不再取值，响应恒 `null`；iOS 不渲染 detail。

### 2.3 iOS DTO

```swift
struct DayTopic: Decodable, Equatable {
    let topic: String
    let searchWords: String?   // CodingKeys: search_words
}
```

- `detail` 可忽略（未知字段）。
- `OverviewDay.topics` 默认空数组。

---

## 3. 卡片 topic 聚合

### 3.1 输入

- 当前首页已加载窗口内的日卡（`OverviewWindow.days`，date 降序）；
- scope 过滤后的组内日卡；
- 组内日卡按 date 降序作为聚合顺序。

### 3.2 聚合规则（与 Android `PeriodAggregator` 一致）

1. **合并去重**
   - 按 topic 展示名首见去重；同名主题跨天只保留首见条目的 `search_words`。
   - 保留「日倒序首见」顺序。

2. **日卡**
   - 全量渲染，不设上限（prompt 约定 ≤5，代码不截断）。

3. **周/月/年卡**
   - 去重后 ≤50：保持首见原序，不重排。
   - 去重后 >50：
     - 权重 = 该 topic 在期内出现的次数（每出现一次 +1，首见计 1）；
     - 以卡片 key 为种子做确定性随机，再按权重稳定降序，取前 50；
     - 同权重保留随机序；同一窗口下跨重组/轮询刷新，集合与顺序必须稳定。
   - iOS 种子必须使用**稳定哈希**（如 FNV-1a / SplitMix64），不能使用 Swift `Hasher`/`hashValue`（进程级随机化，跨启动不稳定）。

4. **窗口限制**
   - 卡片 topics 只覆盖当前已加载窗口；更早日卡加载后重新计算。

### 3.3 展示字段

- chip 只显示 `topic` 文本；`search_words` 只用于点击搜索。
- 不做长度截断；超长 topic 由 chip 文本换行（胶囊高度增长）。

---

## 4. Chip UI 与点击

### 4.1 视觉

- 透明底 + 1pt `MylifePalette.line` 描边 + Dust `labelMedium`；
- 圆角 50%；内容 padding 水平 8 / 垂直 2；
- 自动换行，chip 间水平/垂直间距 4（SwiftUI 用 `FlexibleWrapLayout` 或自建 flow layout；iOS 15 无 `Layout`，可用 `AlignmentGuide`/分段计算或第三方不引入）；
- chip 组与上一区块（图片区/摘要）间距 8；
- 可点与纯展示同款视觉；可点时仅在视觉上叠加点击手势，不使用会强制 44pt 最小高度的 `Button` 样式把行距撑高（热区可外扩）。

### 4.2 点击

- `onTopicClick(topic)`：
  - `let trimmed = searchWords?.trimmingCharacters(in: .whitespacesAndNewlines)`；
  - `query = trimmed.flatMap { $0.isEmpty ? nil : $0 } ?? topic`；
  - 立即进入搜索模式（设置 query，触发防抖/立即搜索规则）。
- 月/年卡本体可点击下钻；topic chip 的点击先被消费，不触发卡片下钻。
- 搜索词可能只有 1 个字符：此时按搜索规则落 Idle（显示「至少输入 2 个字」），不发请求。这是既有边界，不做特殊放宽。

---

## 5. 搜索行为

### 5.1 输入与状态机

- query 非空即搜索模式；搜索结果整体替换浏览列表，粒度选择器隐藏。
- 最少 2 个字符才发请求；不足 2 字符 → `Idle`，显示「至少输入 2 个字」。
- 输入防抖 350ms；空串立即发射（退出搜索）。
- 键盘搜索键 / 错误重试 → 立即搜索，跳过防抖。
- 新 query 到达即取消上一轮请求（Combine `switchToLatest` 或 Task generation）。
- 搜索不做缓存；每次 query 直通 `/search`。
- 清空 query 立即回到浏览模式（保留进入搜索前的粒度、scope、滚动位置）。

### 5.2 搜索请求

```
GET /api/mylife/search?q=<query>&limit=50&cursor=<0|offset>&token=<token>
```

- 不传 `timezone`（与 Android 一致；日期头来自 BFF 生效时区）。
- `limit` 默认 50，1..200。
- 分页游标由响应 `next_cursor` 原样回传；不在客户端解析。
- 请求失败：首屏进入 `Error`（可重试）；续页失败保留已有结果，不切错误页。
- 取消（新 query/页面关闭）不得展示错误。

### 5.3 状态

```swift
enum TimelineSearchState: Equatable {
    case idle                 // query < 2 字符
    case loading
    case empty
    case error(message: String)
    case data(hits: [SearchHit], total: Int, nextCursor: String?)
}
```

- `data.loadingMore` 为独立布尔，不进入枚举，避免翻页时清空列表。

---

## 6. 搜索结果渲染

### 6.1 分组

- 按 `hit.date` **首次出现顺序**聚合：
  - BFF 已按 bm25 相关度排序，同日命中不保证相邻；
  - 不允许使用「连续同日分组」，否则会产生重复日头。
- 组内保持服务端顺序。
- 日期头：本地化 `M月d日` / `MMM d`，不含年份；`title` 16/24 Star；水平 16 / 垂直 4。

### 6.2 行

```
[类型图标]  HH:mm
            命中片段（最多两行，命中词金色）
```

- 整行可点；水平 16 / 垂直 8。
- 类型图标（emoji，与 Android 一致）：

| kind | 图标 |
|---|---|
| `chat` | 💬 |
| `transcribe` | 📄 |
| `translate` | 🌐 |
| `agent_job` | 🤖 |
| `video` | 🖼️ |
| 未知 | 📍 |

- 时间：设备本地时区，固定 24 小时制 `HH:mm`；Dust `labelMedium`。
- snippet：`bodyMedium` Star，最多 2 行，尾部省略。
- 高亮：在 snippet 中查找整串 query（不区分大小写）的首次出现，命中子串 Gold；找不到则原样展示。
  - 放宽回退（bigram OR）或多词非相邻命中时，snippet 可能不含整串 query，此时不高亮（不报错）。

### 6.3 分页

- 顶部显示结果总数：`mylife_search_total` 复数（en `%lld result(s)`，zh `共 %lld 条结果`）。
- 接近列表底部（距末尾 3 个 item）触发 `next_cursor` 续页。
- 续页失败静默保留已加载结果；滚离底部阈值区再回来后才重新触发（同一次触底不立即重发）。
- 结果列表滚动位置独立于浏览列表。

---

## 7. 搜索命中 → eventListViewer

### 7.1 点击

- `hit.ts_ms == nil` → no-op（FTS 命中恒有 ts，保留防御）。
- 否则进入 `TimelineRoute.eventList(tsMs: hit.tsMs, pending: pendingForDay(tsMs))`。
- 不按 kind 分支：五类事件统一进入 eventListViewer。

### 7.2 pending 归属

```
pendingForDay(tsMs) =
    overviewWindow?.days.first { $0.date == localDateOf(tsMs, deviceTimeZone) }?.hasPending ?? false
```

- `localDateOf` 使用**设备当前时区**，与 hotSlot 取数日同一口径；不得用 UTC。
- 命中日不在已加载 overview 窗口 → `false`。
- 日期头使用 BFF `hit.date`（服务端时区）；pending 与取数使用设备时区。两者在设备时区与 BFF 时区不一致时可能落在不同日界，这是 Android 既知行为，iOS 保持一致。

### 7.3 eventListViewer 参数

- `ts`：命中事件时刻；取数窗口 `ts ± 10 分钟`。
- `pending`：上述快照，用于顶栏「分析中…」与空态。
- 其余行为（锚点定位/追击/高亮、行模型、帧查看器、Agent 结果）见 `ios_timeline_hotslot.md`。

---

## 8. 边界与已知行为

| # | 场景 | 行为 |
|---|---|---|
| 1 | 日卡 topics | 全量渲染，不截断 |
| 2 | 周/月/年卡 topics | 首见去重；≤50 原序；>50 权重优先取 50 |
| 3 | 同名 topic 跨天 | 合并，保留首见条目的 search_words |
| 4 | 点 topic chip | query = search_words（空白/缺省回退 topic） |
| 5 | query < 2 字符 | Idle，显示「至少输入 2 个字」，不发请求 |
| 6 | 点带 ts 的命中 | 进入 eventListViewer（ts ±10min） |
| 7 | 命中 ts 为 nil | no-op |
| 8 | 命中日不在已加载窗口 | pending = false |
| 9 | 搜索日头与本地日口径不同 | 日头来自 BFF 时区；pending/取数用设备时区 |
| 10 | 查询 audio/window/topic 内容 | 不出现在搜索结果（可搜 ⊆ 可展示五类） |
| 11 | 搜索索引缺失/未跑 reindex | BFF 返回空结果；客户端按无结果展示 |
| 12 | 停用词/纯标点 | BFF 返回空结果 |
| 13 | 精确 0 命中 | BFF 自动 bigram OR 放宽一次；客户端无感 |
| 14 | 放宽命中 snippet 不含整串 query | 不高亮 |
| 15 | 主题很多（>50） | 不提供折叠；VoiceOver 节点多 |
| 16 | legacy topics（字符串/无 search_words） | 用 topic 名搜索，召回可能较低 |
| 17 | agent_job 的 FTS 字段与展示字段 | FTS 索引 `events.summary`；气泡正文按 text→summary→agent_name；两者可能不同 |

---

## 9. 本地化与可访问性

- topic 文本来自服务端，不做本地化。
- 搜索占位、状态、结果总数、日期头、类型图标均按现有 iOS 本地化规则输出。
- 搜索结果行合并为单个可访问元素：label =「类型, 时间, 片段」，`.isButton`。
- 日期头作为分组标题，VoiceOver 可导航。
- topic chip：聚焦时读出 topic 文本，`.isButton`。
- 搜索框：placeholder + 清除按钮 label；命中高亮不额外播报（片段文本已完整读出）。

---

## 10. 测试计划

### 10.1 单元

- `mergeTopics`：日倒序首见、同名合并、search_words 跟随首见。
- `selectTopics`：日卡全量；≤50 原序；>50 权重降序、同卡 key 稳定、恰 50 不重排。
- `onTopicSearch`：search_words 优先、空白回退、1 字符落 Idle。
- 搜索流：350ms 防抖、空串立即、<2 字符 Idle、新 query 取消旧请求、失败转 Error。
- 分组：同日命中不相邻仍聚合为单一日头；组间保持首见顺序。
- 高亮：整串命中、大小写不敏感、未命中不高亮。
- 命中跳转：ts 非空才导航；pending 取已加载窗口对应本地日；无该日 false。
- 分页：cursor 追加；失败保留已有结果。

### 10.2 UI

- 点 topic chip 进入搜索并显示结果。
- 搜索结果总数、日期头、类型图标、时间、两行截断、高亮。
- 不足 2 字提示、搜索中、无结果、错误重试。
- 命中点击进入 EventList 并定位/高亮。
- 从 EventList 返回后 query/结果/滚动保持。
- 大字号下 chip 换行与结果行布局。

### 10.3 服务端契约（只读回归）

- `/search` 五类结果、topic/audio/window 排除、bm25、分页、停用词、索引缺失、放宽回退（现有 BFF 测试已覆盖，iOS 不新增服务端改动）。

---

## 11. 代码锚点

### Android

| 层 | 文件 |
|---|---|
| topics 生成 | `tools/lvdata-mcp/src/lvdata_mcp/summary/day.py` |
| BFF 归一化/透出 | `services/bff/common/topics.py`、`services/bff/timeline/service.py` |
| BFF 检索 | `services/bff/search/service.py`、`services/bff/search/fts.py` |
| 聚合 | `.../timeline/PeriodAggregator.kt` |
| chip | `.../timeline/components/TopicChipRow.kt` |
| 搜索 UI | `.../timeline/components/SearchResultList.kt`、`SearchBar.kt` |
| 搜索 VM | `.../timeline/TimelineViewModel.kt` |
| 命中跳转 | `.../timeline/TimelineViewModel.kt`（`onSearchHitClick`）、`Routes.kt` |

### iOS（拟）

- `Core/Mylife/MylifeDTOs.swift`（`DayTopic`）
- `Features/Timeline/TimelineHomeViewModel.swift`（topic 聚合/搜索流/命中导航）
- `Features/Timeline/Components/TopicChipRow.swift`、`MylifeSearchBar.swift`、`SearchResultList.swift`
- `Features/Timeline/HotSlot/EventListView.swift`（命中目标）
- `Resources/Localizable.xcstrings`：见 `ios_timeline_arch.md` §8.3
