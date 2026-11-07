# MyLife Timeline Android V2 终稿（Timeline 重构 + 全局搜索）

> **Status**: 终稿（实施契约，唯一实施依据）
> **Date**: 2026-08-21
> **来源**: 合并早期 Timeline 骨架与吸收点设计稿 + 2026-08-21 产品决策。
> 原文未修改；差异与决策记录见 §11。
> **Scope**: 仅重构 MyLife **Timeline tab**；Graph、Replay、导航骨架、DI、SSL、媒体链路不变。
> 全部结论经仓库代码逐条核对，锚点见 §12。
> **已被取代（2026-09-21 实施落地，见 docs/android_timeline_topics.md / docs/bff_search_fts.md）**：
> 卡片 topics 不再截断（日卡全量 / 周月年 ≤50 且超限按**权重（出现次数）优先** + 同权重种子随机；
> chip 可点 → 搜索该 topic）；**搜索结果点击不再进 Replay** 而是 eventListViewer
> （`mylife/events?ts&pending`，被点事件 ±10min，Replay 仅保留星图入口）；`/search` **结果集收敛为
> 五类展示事件**（chat/transcribe/translate/agent_job/video，topic/audio/window 命中移除）、检索改走
> lvdata-mcp `search_docs` FTS（bm25 相关度序）、`kind="video"`。§5.3 的搜索与 §3.3 的 topics
> 相关表述按此为准。
>
> **已被取代（2026-09-22，见 docs/android_timeline_hotslot.md §14 R1/R2/R3=C）**：`Routes.MYLIFE` 收敛为**无参** `"mylife"`
> （`tab`/`date` 参数与 §4.2 的"Graph appearance → Timeline"日期深链机制**随批删除**）；`MYLIFE_REPLAY` 与 Replay 整栈删除
> （§4.2 表内"搜索 hit → Replay"行早已改走 eventListViewer，见上）；宿主为**单视图**（无 tab、无底栏），标题「时光机」、
> 搜索框 placeholder「搜索…」——§4.1/§4.2 与 §3.1 布局 mock（「✦ MyLife」「搜索我的生活…」「HotSlot chips 可点 → Replay」）为历史形态。

---

## 1. 背景与需求

### 1.1 V1 现状（代码事实，2026-08-21 核对）

Timeline tab 当前为"概览章节 + 事件流"双源结构（`TimelineScreen.kt`）：

- 顶部类型过滤 chips（全部/对话/语音/画面/窗口/任务，`TypeFilterChips.kt`）
- HeatStrip 每日密度条（`HeatStrip.kt`，点击触发日期定位/定位模式）
- overview 章节：月大字分隔 + 日卡片（stats 徽章 🎙️/🖼️/💬/⏱ + summary + topics）
- 事件流：跨 session 合并事件，5 类 EventCard 无限滚动（游标分页 PAGE_SIZE=200）
- 数据源：`/overview`（全量天，无分页）+ `/timeline`（事件流分页）

### 1.2 V2 需求（逐条 → 设计决策）

| # | 需求 | 决策 |
|---|------|------|
| 1 | 去掉顶部 filter chips 与密度条 | 删除 `TypeFilterChips`/`HeatStrip`；`?types=` 过滤语义在 Android 端废弃 |
| 2 | 搜索框，全局搜索 | 顶部常驻搜索框；**新增 BFF 端点 `GET /api/mylife/search`**（§5.3）+ overview 扩展 `frames` 字段（§5.3B）；结果 → Replay 定位 |
| 3 | day/week/month/year 粒度，默认 day，各级显示对应 summary | 粒度选择器（segmented 4 选 1）；周/月/年摘要由客户端从 overview 全量天聚合（`PeriodAggregator` 纯逻辑，§5.1），**无新 BFF 摘要端点** |
| 4 | 卡片只显示 hot 时间段 + summary + topics；点击进 replay | 删除 stats 徽章与 counts 行；**所有粒度的 hot 时段均可直接点击进 Replay**（§3.3 点击语义表）；月/年卡本体提供下钻（月 → 只看该月日卡；年 → 只看该年月卡） |
| 5 | 搜索覆盖语音转写与画面 caption | 搜索 hit 5 类：chat / transcript / caption / summary / topic（§5.3），min 2 字符 |
| 6 | 其他必要调整 | 事件流整体下线（§2）；深链行为重定义（§4）；Repository 清理（§5.4） |

### 1.3 非目标（V2 不做）

- 不改 Graph / Replay / BFF 既有端点（search 为唯一新端点；overview 扩展 `frames` 字段，§5.3B）
- 不做 LLM 叙事级周/月/年摘要（V2 为确定性聚合拼接；叙事摘要归分析管线 rollup，§10 风险 2）
- 不做搜索的分词/相关性排序/语义搜索（子串匹配起步，§10 风险 5）
- **不做服务端 ranges 聚合**（k3 与 d4 的分歧已裁决：客户端聚合；服务端聚合仅作数据量超阈后的演进路径，§10 风险 3）
- 不动 Web 版 mylife（V2 起 Android Timeline 与 Web 交互分叉；Graph/Replay 仍对齐）

---

## 2. 信息架构：从「事件流」到「摘要层级浏览」

```
V1:  chips → HeatStrip → overview 章节(日卡片) → 事件流(5 类卡无限滚动)
V2:  搜索框 → 粒度选择器(日/周/月/年) → 摘要卡片列表(hot 时段+summary+topics)
                                            ↓ hot 点击
                                          Replay
```

- **事件流整体下线**：Timeline 不再消费 `/timeline`；chat/audio/video/window/agent_job
  事件只在 Replay 内可见（产品确认，§10 风险 6）
- **层级模型**：年 → 月 → 日（钻取链）；**周为平级视图**（不参与下钻，经粒度选择器进入）。
  粒度选择器是平级切换；卡片本体点击是**下钻**（仅月/年卡，带范围过滤）；hot 时段是**终端入口**（全粒度直进 Replay）
- **范围过滤（scope）**：下钻不是"定位滚动"而是**过滤**——月卡 → 日粒度后列表只含该月的日卡；
  年卡 → 月粒度后只含该年的月卡（§3.3.2）
- 数据基础：`/overview` 单请求返回**全部**天（`build_overview` 无分页，
  `service.py:37-76`），四种粒度全部由一份 overview 客户端聚合产出，
  **浏览路径零新 BFF 端点、零分页**

---

## 3. UI 设计

### 3.1 布局结构（上 → 下）

```
┌──────────────────────────────┐
│ ✦ MyLife            (既有头部) │
│ ┌──────────────────────────┐ │
│ │ 🔍 搜索我的生活…      ✕  │ │  SearchBar（常驻）
│ └──────────────────────────┘ │
│ [ 日 | 周 | 月 | 年 ]         │  GranularitySelector（默认"日"）
│ ──────────────────────────── │
│ 2026年 · 8月        (分组大字) │  ※ 粒度对应的分组头（§3.5）
│ ┌──────────────────────────┐ │
│ │ 8月21日 · 周四             │ │
│ │ ▸ 09:12–10:05  ▸ 14:00–15:20│ │  HotSlot chips（可点 → Replay）
│ │  summary 摘要两行…          │ │
│ │  [topic] [topic] [topic]   │ │
│ └──────────────────────────┘ │
│  …（滚动列表 + 下拉刷新）       │
└──────────────────────────────┘
```

搜索激活（query 非空）时，粒度选择器以下区域整体替换为**搜索结果列表**（§3.4）；
清空 query 即恢复粒度浏览。

### 3.2 粒度选择器（GranularitySelector）

- Material3 `SingleChoiceSegmentedButtonRow`（BOM 2024.06.00 → material3 1.2.1 可用，
  `ExperimentalMaterial3Api`，与既有 `PullToRefreshContainer` 同缀 @OptIn）
- 四项：`日 / 周 / 月 / 年`；选中 gold 高亮，未选 dust；触控目标 ≥44dp（沿用 §6.2 约束）
- 切换即就地重组列表，**无网络请求**（纯客户端重聚合）；**手动切换粒度清除 scope**
  （§3.3.2）；选择状态 `rememberSaveable` + UiState 持久，tab 切换不丢

### 3.3 时间范围卡片（四种粒度统一信息结构）

> **已被取代（2026-09-20 实施落地）**：本节 hot 时段相关约定（全粒度 chips、点击 → Replay、
> chip 带日期前缀）由 `docs/android_timeline_hotslot.md` 取代——仅日卡显示 chips、只显示开始
> 时间、点击进 hotSlotView、周卡本体点击移除。其余（标题行/summary/topics/图片区）仍有效。

每张卡片**只含三类内容**（需求 4），视觉层级自上而下：

1. **标题行**：范围标签
   - 日：`8月21日 · 周四`（+ 星期几，增强时间感）
   - 周：`8/10 – 8/16`（ISO 周，周一起始；右侧小字 `第33周`）
   - 月：`2026年8月`
   - 年：`2026年`
2. **hot 时段**：HotSlot chips 行（`▸ 09:12–10:05`），**≤3 个**，可点 → Replay
   （§5.2 推导规则：session 活跃区间合并；**有图（含视频帧）的 slot 优先选取**，
   同图状态按时序，2026-08-21 增强）
3. **summary**：
   - 日：当日各 session summary 合并展示（多 session 时最多 3 条，每条 2 行截断，
     超出 `+N 条`折叠）
   - 周/月/年：聚合摘要（§5.1 规则：取该时段内最近 N 天的代表 summary 拼接，
     确定性无 LLM）；**无任何 summary 时降级为 `"{范围标签}共 N 段会话"`**
4. **topics**：按 mentions 合并排序后的 top 3–5 gold chips（沿用现有 chip 样式）
5. **图片区**（2026-08-21 增强）：topics 下方、3 列网格缩略图（正方形，圆角 8dp）：
   - **日卡 ≤3 张（1 行）**；**周卡 ≤6 张（2 行）**；**月卡 ≤9 张（3 行）**；
     **年卡 ≤15 张（5 行）**
   - 数据源：组内 session 的视频关键帧（`overview SessionItem.frames`，§5.3B），
     保持 BFF 返回顺序（key_rank 升序，1=最重要）取前 N（聚合器按粒度截断）
   - 纯展示，无点击行为（replay 定位入口仍为 hot slot chips）

**明确不显示**（需求 4）：`audio:5 · chat:20` 计数行、🎙️/🖼️/💬/⏱ stats 徽章、
"N 个事件"计数、事件列表、agent_job 状态卡、小时直方图/高峰文本（V2 已裁决移除，
hot 信息统一由可点击的 slot chips 承载）。

**pending 处理**：当日/时段内任一 session `analysis.timeline == pending` →
卡片顶部"分析中…"脉冲点（复用 `PulsingDot`）；pending session 不产生 hot slot
（无 summary/topics 可展示），但仍计入聚合的天数。

#### 3.3.1 点击语义（产品决策 2026-08-21）

| 卡片 | hot slot chip | 卡片本体（非 chip 区域） |
|------|--------------|--------------------------|
| 日卡 | `navigate("mylife/replay?session=<sid>&t=<slotStartMs>")` | 不可点（多 session 日避免歧义；slot 是唯一 replay 入口） |
| 周卡 | 同上（`t=<slotStartMs>`） | `replay?session=<latestSlot.sid>&t=<latestSlot.startMs>`（latestSlot = 该周时序最后一段，独立于图优先展示排序/截断，§5.1；周内无 slot 则不可点） |
| 月卡 | 同上 | **下钻**：粒度切到"日" + `scope = 该月`（如 `2026-08`），列表只显示该月的日卡 |
| 年卡 | 同上 | **下钻**：粒度切到"月" + `scope = 该年`（如 `2026`），列表只显示该年的月卡 |

- chip 点击消费事件，不触达本体；本体点击区域排除 chip 行
- 周卡本体点击的语义与 hot chip 一致（进 replay），仅目标时段不同（最近 slot vs 该 chip）

#### 3.3.2 Scope 模式（下钻后的范围过滤）

下钻 = 粒度切换 + 范围过滤，**无路由变化**（VM 内状态，§7）：

- `scopeKey` 取值：月下钻 → 月 key（`2026-08`）；年下钻 → 年 key（`2026`）
- 过滤规则：DAY 粒度时仅保留 `date.startsWith(scopeKey)` 的日卡；MONTH 粒度时仅保留
  以 scopeKey 为前缀的月卡
- 列表倒序（最新在前），范围小时无需额外定位
- 退出方式：① 粒度选择器手动切换任意粒度（清 scope）；② 顶部 scope pill
  （`只看 2026年8月 ✕`，沿用 LocateBanner 样式语义）点击清除，回到该粒度全量
- 深链 date/session 进入时 scope=null（深链语义见 §4.2）

### 3.4 搜索模式

- **SearchBar**：`OutlinedTextField` 风格常驻搜索框（leading 🔍 / trailing ✕ 清除），
  占位文案"搜索我的生活…"；IME action=search 立即触发；输入防抖 350ms；
  **最小 2 字符**（CJK 单字噪声大，min=1 的代价高于收益）
- **状态机**：`Idle（空 query）→ Loading → Data(hits,total) / Empty / Error`；
  Error 带重试按钮
- **结果列表**（替代粒度列表区域）：按 date 倒序分组，日头 + hit 行：
  - hit 行 = 类型图标（💬 chat / 🎙️ transcript / 🖼️ caption / 📝 summary / 🏷 topic）
    + 时间（HH:mm）+ snippet（2 行，命中子串 gold 高亮）
  - 点击 → `mylife/replay?session=<sid>&t=<ts_ms>`（summary/topic 类 hit 无 ts 时
    传 session 首事件时刻或不带 t）
  - 顶部小字"共 N 条结果"；limit=50 分页，滚动到底 loadMore
- **退出**：点 ✕ / 清空文本 / 系统返回键（搜索模式消费一次返回，仅清 query 不退出路由）

### 3.5 分组头规则

| 粒度 | 分组头 | 说明 |
|------|--------|------|
| 日 | `2026年 · 8月` | 沿用现有 MonthHeader 样式（headlineMedium gold/70%） |
| 周 | `2026年 · 8月` | 以**周一**所在日期归属月份；跨月周归周一所在月（口径统一，§10 风险 7） |
| 月 | `2026年` | 年大字分隔 |
| 年 | 无 | 平铺 |

### 3.6 其他状态与适配

- **空态**：当前粒度+scope 无数据 → "还没有记录，开始一次对话吧"（复用 EmptyState 语义）；
  scope 空态文案带范围（如"2026年8月没有记录"）
- **下拉刷新**：保留（`PullToRefreshContainer`），刷新 overview 并重聚合；
  搜索模式下刷新 = 重跑当前 query
- **定位条**：深链定位后底部 gold pill（"已定位到 8月21日 ✕"），点击清除
  （沿用现有 LocateBanner 语义）；scope pill 与其并存（定位条用于深链、scope pill 用于下钻）
- **主题/insets/触控**：全部沿用 `MylifeTheme`、44dp 约束、statusBars/navigationBars
  避让（V1 §6.2 不变）

---

## 4. 交互模型与深链

### 4.1 路由（不变）

`Routes.MYLIFE = "mylife?tab={tab}&date={date}&session={session}"` 与
`MYLIFE_REPLAY = "mylife/replay?session={session}&t={t}"` 均不新增/不改参数
（`t=-1` 兜底不变，`Routes.kt:13-21`）。

> **2026-09-22 已移除**：以上两条路由均退役——`MYLIFE = "mylife"`（无参）、`MYLIFE_REPLAY` 删除（§14 R1/R3=C）。

### 4.2 深链行为重定义

| 来源 | 参数 | V1 行为 | V2 行为 |
|------|------|---------|---------|
| Graph appearance → Timeline | `tab=timeline&date=&session=` | 事件流定位模式（date_to 定向拉取） | 粒度强制切到"日"、scope=null，滚动到该日卡片并短暂高亮；session 参数用于定位其所属日（overview 全量在内存，**无定向拉取**，定位模式机制删除） |
| Graph event 节点 | `mylife/replay?session=` | 不变 | 不变 |
| 卡片下钻 | 无路由变化 | — | VM 内 granularity + scopeKey 状态切换（§7） |
| 搜索 hit | `mylife/replay?session=&t=` | — | 新增（Replay `?t=` 定位既有能力直接复用，`ReplayViewModel.load` → `locateCueTs`） |

`launchSingleTop` 复用 entry 的参数重触发机制（`NavGraph.kt:122-128`）保持不变，
`MylifeHomeScreen` 的 tab 同步 `LaunchedEffect` 不变。

---

## 5. 数据层设计

### 5.1 PeriodAggregator（纯 Kotlin，可单测，新文件）

`ui/screens/mylife/timeline/PeriodAggregator.kt`——把 overview 全量天聚合为四种粒度的
摘要卡片模型，**确定性、无 LLM、无网络**：

```kotlin
enum class Granularity { DAY, WEEK, MONTH, YEAR }

data class HotSlot(
    val startMs: Long, val endMs: Long,
    val sessionId: String,        // 点击 → replay(sessionId, t=startMs)
    val date: String,             // 所属本地日（yyyy-MM-dd，取自 overview 天 key）；
                                  //   周/月/年 chip 的日期前缀用（2026-08-21 增强）
)

data class PeriodImage(
    val url: String,              // 绝对 media URL（Repository 出口已附 token）
    val tsMs: Long,
    val sessionId: String,
)

data class PeriodDigest(
    val key: String,              // "2026-08-21" | "2026-W33" | "2026-08" | "2026"
    val granularity: Granularity,
    val rangeLabel: String,       // 展示标签（§3.3 标题行规则）
    val hotSlots: List<HotSlot>,        // 合并后的活跃时段（≤3；有图 slot 优先，§3.3 增强）
    val latestSlot: HotSlot?,           // 合并后时序最后一段（独立于图优先排序/截断）；
                                        //   周卡本体 replay 入口（§3.3.1）
    val summaryLines: List<String>,     // 聚合摘要行（见下）
    val summaryLimit: Int,              // 默认显示条数（DAY≤3 / WEEK≤4 / MONTH≤5 / YEAR≤6）
    val topics: List<TopicItemDto>,     // mentions 合并降序
    val images: List<PeriodImage>,      // 保持 BFF frames 顺序（key_rank 优先级）；
                                        //   数量按粒度 day3/week6/month9/year15（§3.3 增强）
    val dayCount: Int, val sessionCount: Int,
    val hasPending: Boolean,
)

fun aggregate(
    days: List<OverviewDayDto>,
    granularity: Granularity,
    scopeKey: String? = null,     // 下钻过滤（§3.3.2）；null = 全量
): List<PeriodDigest>
```

聚合规则：

- **scope 过滤**：scopeKey 非空时先过滤 days（`date.startsWith(scopeKey)`），再分组
- **分组**：DAY 每组一天；WEEK 按 ISO-8601 周（`java.time` `WeekFields.ISO`，
  周一起始——minSdk 26 平台自带 java.time，**无新依赖**）；MONTH/YEAR 按
  `date.take(7)/take(4)`
- **hotSlots（全粒度，2026-08-21 增强）**：组内所有 session 按 §5.2 推导 slot，跨 session
  合并重叠/相邻（间隔 <5min）区间；合并 slot 携带"组内任一来源 session 有视频帧"标记；
  **选取排序：有图 slot 优先**（图片时段信息密度更高），同图状态按时序升序，**取前 ≤3 段**
- **summaryLines**：组内所有非空 `session.summary`，按 session 开始时间**倒序**，
  去重后 DAY 取 ≤3 条、WEEK ≤4、MONTH ≤5、YEAR ≤6（超长截断在 UI 层 maxLines）；
  **组内无任何非空 summary 时**产出降级行 `"{范围标签}共 N 段会话"`（§3.3）
- **topics**：组内 `TopicItem` 按 topic 文本合并、mentions 求和，降序取 top 5
- **latestSlot（2026-08-21 修复）**：合并后按 startMs 取最后一段（截断/排序前独立计算），
  所有粒度均计算；周卡本体点击用它（图优先展示排序会破坏 `hotSlots.lastOrNull()` 语义）
- **images（2026-08-21 增强）**：组内 session 的 `frames`（§5.3B）拍平，**保持 BFF 返回
  顺序**（服务端已按 key_rank 升序，1=最重要；客户端不重排），数量上限按粒度
  （DAY 3 / WEEK 6 / MONTH 9 / YEAR 15）——与 §3.3 图片区行数对应
- **hasPending**：组内任一 session `analysis.timeline == PENDING`
- 排序：各粒度列表均按 key 倒序（最新在前）；DAY 粒度在 UI 层再套月分组头（§3.5）

### 5.2 HotSlot 推导（精度声明）

> **已被取代（2026-09-20 实施落地）**：本节描述 V1 session 推导已不适用；现行 hot slot
> 由 bff 从 events 五类（chat/agent_job/video/transcribe/translate）派生——60min 间隔切段、
> 超 3 段合并至 ≤3，客户端零推导（`docs/android_timeline_hotslot.md` §3.1/§3.2/§4.1）。

- 来源：`SessionItemDto.startedAt`（ISO 字符串 → epoch ms，解析失败则跳过该 slot）
  + `stats.durationMin`（分钟，`SessionStats.duration_min`，`schemas.py:33-37`）；
  `endMs = startMs + durationMin * 60_000`
- **精度**：`duration_min` 为分钟取整且 `SessionItem` 无 `ended_at` 字段
  （`schemas.py:40-48`）——slot 末端误差 ±1 分钟，展示与 replay 定位均可接受
  （replay 定位为"最接近 cue"，`locateCueTs`，误差对用户不可感知）
- duration 缺失/为 0 → 跳过该 slot；同日重叠/相邻（间隔 <5min）slot 合并，
  合并 slot 保留**最早** sessionId 用于 replay 跳转
- 单 session 日：卡片仍只显示 hot slot + summary + topics（不特殊化）

### 5.3 全局搜索（V2 唯一 BFF 新端点）

**BFF 端点提案**（服务端实现归后端任务，本文定契约）：

```
GET /api/mylife/search?q=<str>&limit=50&cursor=<opaque>
→ 200 { schema_version: 1, query, total, next_cursor, hits: [SearchHit] }
→ 400 {"error":{"code":"bad_request","message":"empty query"}}

SearchHit = {
  session_id: str, date: str,            # 本地日
  ts_ms: int | None,                     # 命中事件的原始时刻（summary/topic 命中可为空）
  kind: "chat" | "transcript" | "caption" | "summary" | "topic",
  snippet: str,                          # 含命中子串的上下文片段（服务端截取 ~120 字符）
}
```

**搜索范围（产品决策：含转写与画面 caption）**：

| kind | 数据来源 |
|------|---------|
| chat | timeline.json 中 chat 事件的 `text` |
| transcript | timeline.json 中 audio 事件的 `transcript` |
| caption | timeline.json 中 video/window 事件的 `caption` |
| summary | `session_summary` 的 `session_summary` 字段 |
| topic | `session_summary` 的 `topics[].topic` 字段 |

服务端实现要点（供后端参考，非本文约束）：`LvdataStore.list_sessions` →
每 session 扫 `session_summary`（summary/topics，`store.py:100 read_derived`）与
`timeline.json` 事件（chat text / audio transcript / video·window caption，
`store.py:175 timeline_events`），大小写不敏感子串匹配，按 ts 倒序，游标 =
`session 序号|事件序号` 复合（与 timeline 现有 `date|idx` 游标同构，
`service.py:171-185`）。同步文件 IO 移入线程池（参照 media.py 的 def 端点先例）。
**可参照 `tools/lvdata-mcp` 的 FTS 实现**（`build_fts_query` + `search_docs` 索引 +
bigram 高亮片段）评估索引复用或至少借鉴 snippet 截取/高亮方案（§10 风险 5）。

**Android 侧**：

```kotlin
// dto/MylifeDtos.kt 新增
@Serializable data class SearchHitDto(
    @SerialName("session_id") val sessionId: String,
    val date: String,
    @SerialName("ts_ms") val tsMs: Long? = null,
    val kind: String,                    // 宽松字符串，不建枚举（对齐 V1 §5.2 不建 sealed 决策）
    val snippet: String,
)
@Serializable data class SearchResponseDto(
    val query: String, val total: Int = 0,
    @SerialName("next_cursor") val nextCursor: String? = null,
    val hits: List<SearchHitDto> = emptyList(),
)

// MylifeApi 新增
suspend fun search(q: String, limit: Int = 50, cursor: String? = null): SearchResponseDto

// MylifeRepository 新增：无缓存（查询词组合无限，缓存价值低；对齐"仅内存缓存"语义下的
// 最小实现），结果分页状态由 TimelineViewModel 持有
suspend fun search(query: String, cursor: String? = null): SearchResponseDto = api.search(query, cursor = cursor)
```

### 5.3B overview 关键帧字段（2026-08-21 增强，BFF 第二处改动）

卡片图片区的数据源：`GET /api/mylife/overview` 的 `SessionItem` 增加 `frames` 字段
（**既有端点扩展字段，非新端点**；Android 端 `ignoreUnknownKeys` 兼容旧服务端）：

```
SessionItem.frames: [{
  frame_id: str,            # timeline.json video 事件的 frame_id
  ts_ms: int,               # 事件原始时刻（绝对 epoch ms）
  media_url: str,           # 相对路径 /api/mylife/media/{device}/{session}/video/{frame_id}.jpg
                            #   （Android Repository 出口统一绝对化并附 token，对齐 replay 语义）
}]
```

- **提取规则**：`build_overview` 每 session 从已读取的 `timeline_events`（`service.py` 外层
  循环已读，无额外 IO）过滤 video 事件，**按 key_rank 升序（1=最重要，分析管线语义；
  无 key_rank 排最后，并列按 ts）取前 ≤6 帧**（防 overview 膨胀；真实数据每 session
  实测 ≤4 帧）。timeline.json 的 video 事件均出自分析管线 `is_key_frame` 筛选
  （`lvdata_summary._load_video_events`），frames 恒为关键帧；**返回顺序即客户端
  展示优先级**
- **聚合消费**：客户端 `PeriodAggregator` 组内 frames 拍平 → ts 升序 → 按粒度截断
  （day 3 / week 6 / month 9 / year 15，§5.1）
- **URL 绝对化**：`MylifeRepository.refreshOverview` 出口统一转换（复用 `absolutize`），
  UI 零感知

### 5.4 Repository / Api 清理（事件流下线）

`MylifeRepository` 删除（唯一消费者是 V1 TimelineViewModel，已核对）：
`TimelinePageState` / `TimelineCursor` / `resetTimeline` / `loadMoreTimeline` /
`mergeDays` / `timeline: StateFlow`（`MylifeRepository.kt:70-136,199-204`）。

`MylifeApi.timeline()` 与 `TimelineDto/TimelineDayDto/TimelineEventDto` 在 Android
端无消费者 → 一并删除；**BFF `/timeline` 端点不动**（Web 版仍消费）。

保留不变：overview StateFlow + 60s pending 轮询、graph LRU、replay、媒体 URL 绝对化。

### 5.5 overview 分页窗口（2026-09-25，BFF 契约见 docs/bff_service_interfaces.md §8）

`/overview` 支持天数 keyset 分页后，Android 侧 `OverviewDto.days` 语义从「全量」变为
**已加载窗口**，规则如下（实现：`MylifeRepository` + `TimelineViewModel`）：

| 规则 | 内容 |
|---|---|
| 页大小随粒度 | DAY/WEEK 30 天、MONTH 90 天、YEAR 366 天（`pageSizeFor`）；切粒度时若窗口比该粒度页浅 → 从最新补取一页（`ensureWindow`，与已加载集合合并，保深度） |
| 触底加载 | `TimelineScreen` 距底部 3 个 item 触发 `onLoadMore`（对齐 SearchResultList）；`loadingMore` 同步置位互斥；失败落 `loadMoreError` 由 footer「加载失败，点击重试」消费。**自动触发必须带来 item 增长**（`lastAutoLoadCount`，离开底部重新武装）——否则 `loadingMore` 的 true→false 会反复重新武装 effect，在列表不增长时一轮一页拉完整个历史。**drilled scope 内不翻页**（VM no-op + 不出 footer）：范围数据在进入下钻时已全量加载，翻页只会取回被 scope 过滤掉的更早日卡 |
| footer 三态 | 加载中 / 已到最早（`nextCursor == null`）/ 失败重试；**最旧的未闭合 group 不上屏**（`renderablePeriods`：`hasMore && 窗口最旧日 > group 首日` 的 calendar 比较——无事件日不落行，「首日是否有行」判定恒不成立），由 footer 占位防半截月/年卡 |
| 合并语义 | 按 `date` 去重（新页优先）、追加即保持 date 降序；`nextCursor` 取更深（日期较小）的一方，任一侧 `null`（已达最早）则整体 `null`。合并走 `MutableStateFlow.update{}`（CAS）——轮询在 `Dispatchers.Default`、VM 取数在 Main，读后写会丢合并 |
| 下拉刷新 | 全局：重置回第一页（`pageSizeFor(granularity)`）；**drilled scope 内**：`cursor = <scope 末日次日>` + `limit = scope 天数` 定位重取（否则 scope 过滤后 periods 为空 → 假空态）；scope 形状非法 → 清 scope 回全局 |
| 60s 轮询 | 重取**已加载窗口**（`limit = min(已加载天数, 400)`，不带 cursor）而非只刷第一页——只刷首页会让窗口内更早的 pending 日永不更新（徽章常亮 + 轮询不停止）；`hasPendingAnalysis()` 只统计窗口内（深尾 >400 天不参与轮询） |
| tz 变化 | 响应 `timezone` 与上次不同 → 丢弃旧窗口（BFF §8 E6：日界整体变了）；若本次取的是更早页/定位页（`cursor != null`）则**自动回第一页**（否则窗口只剩旧日卡、最新日卡消失），VM 同时清 `scopeKey` |

`PeriodAggregator` **零改动**（输入仍是 `List<OverviewDayDto>`；边界 group 过滤在 VM 的
`renderablePeriods` 里，聚合前后各不相干）。

---

## 6. UI 层结构变更清单

```
ui/screens/mylife/timeline/
├── TimelineScreen.kt          # 重写：SearchBar + GranularitySelector + 卡片/搜索列表 + 下拉刷新 + 定位条 + scope pill
├── TimelineViewModel.kt       # 重写：overview/粒度/scope/搜索状态机/下钻定位（§7）
├── TimelineUiState.kt         # 重写（§7）
├── PeriodAggregator.kt        # 新增：纯逻辑聚合（§5.1，可单测）
└── components/
    ├── SearchBar.kt           # 新增
    ├── GranularitySelector.kt # 新增（segmented 4 选 1）
    ├── PeriodCard.kt          # 新增：统一卡片（标题行 + HotSlot chips + summary + topics）
    │                          #   按 granularity 渲染标题；本体点击回调由调用方决定（§3.3.1）
    ├── HotSlotChips.kt        # 新增：hot 时段 chip 行（≤3，点击 → replay）
    ├── ScopePill.kt           # 新增：scope 指示（"只看 2026年8月 ✕"）
    ├── SearchResultList.kt    # 新增：命中分组列表 + snippet 高亮
    └── OverviewSection.kt     # 保留 MonthHeader/groupByMonth（分组头复用）；
                               #   删除 DayCard/SessionBlock/Badge（stats 徽章随需求 4 废弃）
删除：TypeFilterChips.kt / HeatStrip.kt / EventCard.kt
```

`MylifeHomeScreen`：仅 Timeline tab 的参数透传微调（date/session 语义见 §4.2），
结构不变。

---

## 7. ViewModel / UiState 设计

```kotlin
data class TimelineUiState(
    val granularity: Granularity = Granularity.DAY,   // 默认日（需求 3）
    val scopeKey: String? = null,                     // 下钻过滤（§3.3.2）；null = 全量
    // overview（浏览数据源）
    val overview: OverviewDto? = null,
    val overviewLoading: Boolean = true,
    val overviewError: String? = null,
    // 聚合产物（combine(overview, granularity, scopeKey) → PeriodAggregator.aggregate）
    // 由 VM 以 StateFlow 暴露 periods，保证可单测；Screen 不再自行 groupBy
    // 搜索
    val query: String = "",
    val search: SearchState = SearchState.Idle,
    // 定位（深链 date/session）：目标 key（date）+ 一次性消费
    val locateKey: String? = null,
    val isRefreshing: Boolean = false,
)

sealed interface SearchState {
    data object Idle : SearchState                     // query 为空 → 显示粒度列表
    data object Loading : SearchState
    data class Data(val hits: List<SearchHitDto>, val total: Int,
                    val nextCursor: String?, val loadingMore: Boolean) : SearchState
    data object Empty : SearchState
    data class Error(val message: String) : SearchState
}
```

`TimelineViewModel` 职责：

- `periods: StateFlow<List<PeriodDigest>>`：
  `combine(repository.overview, granularity, scopeKey).map { aggregate(days, g, scope) }`
  （`aggregate` 为纯函数，万级天数据 O(n) 可忽略）
- **下钻** `onPeriodCardBodyClick(digest)`：
  - DAY / WEEK：不响应（日卡本体不可点；周卡本体 → replay，见下）
  - MONTH：`granularity = DAY; scopeKey = digest.key`（月 key `2026-08`）
  - YEAR：`granularity = MONTH; scopeKey = digest.key`（年 key `2026`）
- **hot 点击** `onHotSlotClick(slot)` → 导航 `mylife/replay?session=&t=`（全粒度统一）
- **周卡本体点击** → 取 `digest.hotSlots.lastOrNull()`（最近 slot）导航 replay；
  无 slot 则不响应
- `onGranularityChange(g)`：手动切换 → `scopeKey = null`（含切回当前粒度也清 scope）；
  深链 `onInitialParams(date, session)` → `granularity = DAY; scopeKey = null` + locateKey
- `onClearScope()`：`scopeKey = null`（粒度不变，恢复该粒度全量）
- 搜索：`query` MutableStateFlow → `debounce(350).distinctUntilChanged()` →
  空串回 Idle；`trim().length < 2` 回 Idle（§3.4）；非空 → Loading → repository.search；
  `onSearchLoadMore()` 游标累页；新 query 到达即废弃上一页结果（`flatMapLatest` 语义，
  用 Job 取消实现）
- `onInitialParams(date, session)`（深链）：date → granularity=DAY + locateKey=date；
  session → 在 overview 中反查所属日后同上；overview 未到位时挂起等待首次数据
- 定位消费：Screen 滚动完成后回调 `onLocateConsumed()` 清除 locateKey
  （一次性事件，避免重组重复滚动）
- `refresh()`：force 刷 overview；搜索模式下改为重跑当前 query
- pending 60s 轮询：无改动（Repository 层共享）；pending 消化后 overview 更新 →
  periods 自动重聚合，UI 无额外代码

---

## 8. 实施计划（分阶段，各自独立可交付）

| 阶段 | 内容 | 依赖 | 验收 |
|------|------|------|------|
| **P1 粒度浏览重构** | 删除 chips/HeatStrip/EventCard/事件流与 Repository 分页状态机；PeriodAggregator + 统一 PeriodCard + 选择器 + 下钻/scope + 周卡本体 replay；深链新行为；UiState/VM 重写 | 无 BFF 依赖，纯客户端 | 四粒度切换正确；卡片仅含 hot 时段+summary+topics；hot slot 点击进 Replay 且 t 定位正确；月/年卡本体下钻且 scope 过滤正确、scope pill 清除正确；周卡本体进最近 slot replay；pending 脉冲与轮询消化正常；单测全绿 |
| **P2 全局搜索** | BFF `GET /search`（后端任务）+ Android DTO/Api/Repository/SearchBar/结果列表/snippet 高亮/分页 | BFF 端点上线（可 mockwebserver 契约先行开发） | 契约用例过（含 5 类 kind 与 transcript/caption 数据）；防抖/最小字符/清空/返回键语义正确；hit → Replay t 定位；空/错/加载态完整 |
| **P3 打磨** | 下钻/定位滚动动画、卡片进入动画、空态插图、summary 折叠展开交互、周/月/年摘要文案调优 | P1 | 手动冒烟三粒度 + 搜索 + 深链三向 |

---

## 9. 测试计划

- **`PeriodAggregatorTest`**（新增，重点）：
  - scope 过滤：月 key 过滤日卡、年 key 过滤月卡、scope 空数据返回空列表
  - ISO 周分组边界：跨月周（周一归属）、跨年周（2026-01-01 属 2025-W53 类用例）
  - HotSlot：started_at 解析失败跳过、duration 缺失跳过、重叠/相邻（<5min）合并、
    合并保留最早 sessionId、周/月/年卡 ≤3 段截断
  - topics 合并排序、summary 条数上限与降级行（无 summary → "{label}共 N 段会话"）、
    pending 标记传播
- **`TimelineViewModelTest`**（重写）：粒度切换清 scope、下钻状态切换（月→日+scope、
  年→月+scope）、scope pill 清除、周卡本体点击 → 最近 slot 导航、hot chip 导航参数、
  搜索防抖（350ms 内连续输入仅一次请求）、min 2 字符、query 清空回 Idle、加载更多累页、
  深链 date/session → locateKey、locateKey 一次性消费
- **`MylifeApiTest`**（追加）：search URL 拼装（q/limit/cursor）、400 bad_request
  信封、`ts_ms` 可空容错
- **增强测试（2026-08-21）**：`PeriodAggregatorTest` 图片聚合（ts 升序、按粒度截断
  day3/week6/month9/year15、无帧不产出）与 hot slot 有图优先（截断取 3 时排除最早无图段）；
  BFF `test_timeline_api` 断言 overview `SessionItem.frames`（frame_id/ts_ms/media_url，
  无 video 事件为空）
- **UI 冒烟（androidTest）**：粒度切换 happy path；月卡下钻 → 仅该月日卡；搜索输入 →
  结果点击导航断言
- **手工验收**：真实 `/opt/lvdata` 数据四粒度浏览；深链（Graph appearance → 日卡定位）；
  搜索中文/英文/emoji query（≥2 字符）；返回键行为

---

## 10. 风险与开放问题

1. **搜索打破 V1「零 BFF 改动」约束**：`GET /api/mylife/search` 是 V2 唯一服务端
   新增，需后端排期；P1 不依赖它，可与 P2 分开发布。契约先行 + mockwebserver
   可解耦 Android 开发
2. **周/月/年摘要为确定性聚合拼接，非叙事级 LLM 摘要**：若产品验收需要"本周我…"
   式叙事摘要，需分析管线新增 period rollup 产物（周/月 summary.json）+ BFF 端点
   ——属后续阶段，V2 明确不做（§1.3）
3. **overview 全量无分页（含服务端聚合裁决）**：四种粒度浏览与搜索反查都构建在
   "overview 单请求全量"之上（`build_overview` 每 session 一次 `read_derived`，
   `service.py:54-57`）。**k3（客户端聚合）与 d4（服务端 ranges 聚合）已裁决采用客户端**；
   数据量增长（数千 session）后响应变大、首屏变慢——演进路径为 BFF 增量聚合端点
   （ranges 结构可按 d4 §4.1 预留），V2 记录不解决
4. **HotSlot 精度**：`duration_min` 分钟取整、无 `ended_at`（§5.2），slot 末端
   ±1min；replay 定位到 slot 起点，误差对用户不可感知
5. **搜索为子串匹配 + 性能**：无分词/相关性排序；min 2 字符降低 CJK 单字噪声。
   线性扫 timeline.json 在 session 数千级可接受；**项目已有更完整的搜索基础设施**
   （`tools/lvdata-mcp`：FTS `search_docs` 索引 + 向量 `vec.py` + RRF 混合检索），
   若 BFF 与 MCP 数据通路可共享，应评估复用索引而非从零线性扫描——归后端评估
6. **事件流下线的产品确认**：Timeline 不再展示逐条 chat/audio/video/window/
   agent_job 事件（仅 Replay 内可见）；`?types=` 过滤语义在 Android 端废弃。
   若后续需要"某天的事件明细"，入口应是 Replay 而非恢复事件流
7. **跨月/跨年的周归属口径**：统一"周卡片归周一所在月"（§3.5）；跨年周 key 用
   ISO week-year（如 2025-W53），避免 1 月初的周被切到两个年份展示
8. **`build_overview` 无 started_at session 归入 1970 天**（`service.py:67`
   `_parse_started(...) or 0`）：低概率但会导致 UI 出现 1970 年日卡；V2 不做，
   建议后端顺手改为跳过或归入 session_date

---

## 11. 决策记录（与 k3 / d4 的差异）

| # | 议题 | k3 原案 | d4 原案 | **终稿** | 依据 |
|---|------|---------|---------|----------|------|
| 1 | 周/月/年聚合位置 | 客户端（PeriodAggregator） | 服务端（overview 加 granularity） | **客户端**；d4 ranges 结构留作演进路径（风险 3） | 零 BFF 依赖 P1 可独立发布；纯函数可单测；深链反查必中；d4 分页只解决传输不解决计算 |
| 2 | hot 时段点击 | 仅日卡 slot 可点；周/月/年直方图不可点 | 周/月/年 top3 hot chips 可点 | **全粒度 hot slot chips（≤3）可点 → replay** | 产品决策：日/周卡直接点击可 replay；月/年卡 hot 直接 replay |
| 3 | 月/年卡本体 | 钻取：月→周、年→月（定位式） | 无钻取 | **月→日、年→月（scope 过滤式，只看该范围）** | 产品决策：month → 该月 day card；year → 该年月 card；周不参与下钻 |
| 4 | 搜索范围 | 5 类（含 transcript/caption） | 3 类（chat/summary/topic，caption 扩展项） | **5 类** | 产品决策：含 transcription/caption |
| 5 | 搜索最小字符 | 1 | 2 | **2** | CJK 单字噪声（k3 风险 5 自认） |
| 6 | 搜索缓存 | 无 | LRU 8 项/60s | **无** | query 组合无限，命中率低 |
| 7 | `timeline()` 保留 | 删除 | 保留（契约测试复用） | **删除** | 契约测试应测 BFF；Android 死代码不保留 |
| 8 | 聚合 summary 降级 | 无 | `"{label}共 N 段会话"` | **采用 d4** | 无 summary 时卡片不空白 |
| 9 | 周/月/年 hot 呈现 | 24 桶直方图 + 高峰文本 | top3 小时桶 chips | **slot chips（session 粒度）**；移除直方图 | hot 统一可点交互；直方图无交互价值且信息与 chips 重叠 |

## 12. 事实基线锚点（2026-08-21 核对）

| 事实 | 锚点 |
|------|------|
| V1 Timeline 结构（chips/HeatStrip/双源/事件流/定位模式） | `ui/screens/mylife/timeline/TimelineScreen.kt`、`TimelineViewModel.kt`、`TimelineUiState.kt` |
| 待删组件 | `timeline/components/TypeFilterChips.kt`、`HeatStrip.kt`、`EventCard.kt` |
| 现有日卡片 stats 徽章/counts（需求 4 删除对象） | `timeline/components/OverviewSection.kt:64-163`（DayCard/SessionBlock/Badge） |
| Repository 事件流分页状态机（唯一消费者为 TimelineViewModel） | `data/repository/MylifeRepository.kt:70-136,199-204`；`TimelineViewModel.kt:27-39,63,81,98,110,116,131` |
| overview 全量无分页、session 逐条 read_derived | `services/bff/timeline/service.py:37-76 build_overview` |
| SessionItem 字段（started_at/duration_min/summary/topics/analysis；无 ended_at） | `services/bff/common/schemas.py:28-48` |
| BFF 无搜索端点；store 检索能力（list_sessions/read_derived/timeline_events） | `services/bff/common/store.py:54,100,175`（grep 全 bff 无 search） |
| timeline 游标同构参考（`date\|idx`） | `services/bff/timeline/service.py:171-185` |
| 线程池化同步 IO 先例（def 端点） | `services/bff/common/media.py:33` |
| 深链路由与 launchSingleTop 复用机制 | `ui/navigation/Routes.kt:13-21`、`NavGraph.kt:105-149` |
| pending 轮询在 Repository 共享 | `data/repository/MylifeRepository.kt:55-68` |
| Replay `?t=` 定位既有能力（搜索 hit / hot slot 复用） | `ReplayViewModel.load(sessionId, initialT)` → `ReplayTimeline.locateCueTs`；`Routes.mylifeReplay`（`t=-1` 兜底） |
| segmented 控件可用性（material3 1.2.1，Experimental） | Compose BOM 2024.06.00（`LivaChatApp/build.gradle.kts`） |
| java.time 平台自带（minSdk 26，ISO 周无新依赖） | `apps/android/LivaChatApp/build.gradle.kts` minSdk 26；`WeekFields.ISO` |
| MCP 搜索基础设施（FTS search_docs + bigram 高亮 + 向量 + RRF，演进参考） | `tools/lvdata-mcp/src/lvdata_mcp/tools.py:79`（search 工具）、`store.py`（build_fts_query/search_docs）、`vec.py:223`（向量）、`rrf_merge` |
| `build_overview` 无 started_at 归 1970 天（风险 8） | `services/bff/timeline/service.py:67` |

---

*Design version: 2.0-merged+img | Created: 2026-08-21 | 合并 k3+d4+产品决策；仅 Timeline tab 重构；Graph/Replay/基础设施沿用 V1 契约；2026-08-21 增强：卡片图片区（overview frames）+ hot slot 有图优先*
