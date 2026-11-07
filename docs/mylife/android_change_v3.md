# Android mylife v3 改造（LvData MCP v2 数据面适配）— 执行契约（终版）

> **Status**: 定稿（执行契约 · 终版）。基于 `android_change_v3-A.md`（定稿），合入 2026-08-26 评审修复 P1–P6（裁决见 §0.1）；A/B 两稿历史分歧裁决保留于 §0。
> **决策（2026-08-26）**: **Web app 不再使用**——Web 兼容性约束全部解除，`session_id` 字段保留理由消失，bff 无 Phase A/B 拆分，一刀切删除、无兼容期（与上游 §9 一致）。本文档 §0/§8.5 即为该决策及其发布影响的记录载体（P6-f）。
> **上游文档**: [`docs/data/lvdata_mcp_service_v2.md`](../data/lvdata_mcp_service_v2.md)（v2 定稿；§9.1 bff 查询面迁移、§9.1.2 日卡确定性字段服务端化、§9.2 Android 行、§8.2 period_summary 预留）。
> **Scope**: bff 最小配套（含 session 字段全清 + graph topic weight 修复）+ `apps/android/LivaChatApp` 的 mylife module（Timeline / Replay / Graph）。
> 全部结论经仓库代码逐条核对（2026-08-26，含评审复核），锚点见 §11。

---

## 0. 评审裁决（d4/k3 分歧 + 评审复核 + Web 决策的合并结论）

| # | 分歧点 | d4 | k3 | **定稿裁决** | 理由 |
|---|---|---|---|---|---|
| R1 | 发布模型 | 无兼容期一刀切 | bff 不在范围 / Phase A+B | **一刀切单阶段（A 模型；Web 决策后 B 的 Phase B 前置条件消失）** | 上游 §9.2 约束「Android 不留旧消费路径」；Web 不再使用 → bff 字段无保留理由，同批删除。在途版本影响与决策见 §8.5（P3） |
| R2 | Replay 锚 | event_ref 优先 + t_ms 兜底 | 仅 ts_ms | **ts_ms 主锚 + event_ref 第二锚（仅图片入口）；bff 解析加「事件精确匹配」前置（评审加固）** | 全部入口的 ts_ms 均为真实事件时刻（§4.3 入口不变量，P5），精确匹配使解析与 event_ref 同样确定；HotSlot/SearchHit/GraphNode 零 schema 增量；`locateCueTs` 纯 ts 锚零改动（`ReplayTimeline.kt:42`） |
| R3 | overview topics | bff 补透出（mentions） | 移除 topic 行 | **bff 补透出，契约 `DayTopicDto{topic, detail}`（B 方案）** | day_summary JSON topics 结构 = `{topic, detail}`，**无 mentions**（`summary/day.py:50`、上游 :349）；A 的 `TopicItem{mentions}` 必填字段直接映射会 500 或恒 0；`PeriodCard.kt:88` 只渲染 topic 文本，换型零回归 |
| R4 | Graph 范围 | 一并重锚 | 不在本期 | **Android 最小配套 + bff graph `session_id` 清理并入本期（A 范围；Web 决策解除保留理由）** | Android 侧用 `node.firstSeenMs` 进 replay（已存在于 `GraphNodeDto:128`，event 节点非空保证见 §3.4，P6-b），不依赖 bff 本期加字段；bff 侧仅删字段 + appearance 按 `session_date` 去重 + topic weight 修复（P4） |
| R5 | Search | `SearchHit` 加 `event_ref` | 契约不变，`ts_ms` 导航 | **契约不变，仅删 `session_id`（k3/A + Web 决策）；Android 分派判据用 `kind` 而非 `tsMs`（评审修复 P1）** | 事件命中 `ts_ms` 即事件精确时刻，精确匹配解析足够；**topic 命中 `ts_ms` 非 null（§3.3 事实更正），tsMs 判据会把 topic 命中错误导航进 Replay**；Web 不再读 `session_id`，删除 |
| R6 | 解析语义 | forward-only 最近事件 | containment → 最近事件 → 404 | **k3 规则 + 精确匹配前置 + containment 零事件过滤（评审加固 P2）** | containment 窗口语义更稳健；精确匹配使全部当前入口确定性；零事件 session 过滤使 `not_analyzed` 在新路径下真正不可达（§3.2） |
| R7 | HotSlot.date | 未提（疑漏） | 保留 | **保留（k3）** | 周/月/年 chip 日期前缀依赖（HotSlotChips 零改动前提） |
| R8 | dayCount/sessionCount | 删 sessionCount，留 dayCount | 两者皆删 | **皆删（k3）** | 均未渲染（`PeriodCard.kt` 无消费）；降级行「共 N 天」由聚合器内联计算 |
| R9 | ReplaySessionInfo | 精简为 `session_date` | 原样保留 | **精简为 `session_date`（d4），本期直接实施** | UI 仅消费标题 `sessionDate`（`ReplayScreen.kt:148`）；Web 不再读，无保留理由 |
| R10 | SearchResultList item key | 不变 | 改 eventRef ?: date | **改 `kind-date-tsMs-snippet` 复合键（评审发现 + P6-a 加固）** | `SearchResultList.kt:93` key 引用 `hit.sessionId`，删字段即编译失败；去 sessionId 后同日同 session 多 topic 命中区分全靠 hashCode，碰撞即 crash，补 `kind` 提高区分度 |

### 0.1 评审修复裁决（2026-08-26 评审，P1–P6）

| # | 问题 | 严重度 | 修复裁决 | 落点 |
|---|---|---|---|---|
| P1 | **topic 命中 `ts_ms` 非 null**：`_hit` 直通 session `started_at`（`search/service.py:64-66,100-113`），A 稿 `tsMs != null` 分派判据会把 topic 命中错误导航进 Replay；且按其写的单测 fixture（tsMs=null）全绿、真机走错分支 | 高 | Android 分派判据改 **`hit.kind != "topic"`**（现有字段，零 bff 改动）；§3.3 事实更正；bff 测试钉死 topic 命中 ts_ms 现状语义防漂移 | §3.3/§4.3/§5.7/§8 |
| P2 | **`not_analyzed` 未消亡**：containment 可命中零事件 session（有录制行、无 events）→ `build_replay` 仍抛 `not_analyzed`（`replay/service.py:16-17`） | 中 | 阶梯 ② containment 候选**过滤零事件 session**（`EXISTS events`），全部零事件则落 ③；①②③ 结果均保证 ≥1 事件 → `not_analyzed` 新路径不可达，错误文案表删除成立 | §3.2/§5.3/§5.7 |
| P3 | **在途旧版影响无决策记录**：A0 部署后旧版搜索/实体详情 JSON 解析失败（`sessionId` 非空无默认字段）、replay 404 | 中 | 新增 §8.5：影响矩阵 + 决策（接受一刀切，不保留窗口期）+ 回滚声明 + 发版注记事项 | §8.5 |
| P4 | **graph topic 节点 weight 恒 0**：`mentions` 数据源消亡 → `min_weight=1` 下星图无 topic 节点（现存静默退化） | 中 | 本期顺手修：`graph/service.py:164` 改 `weight += 1`（每 session 出现计 1，与其他实体同语义）；补测试断言 | §5.6/§5.7 |
| P5 | **入口不变量无防回归**：精确匹配依赖「所有入口 ts_ms 皆真实事件时刻」，未来入口传近似时刻会静默降级落错 session | 中 | bff 解析阶梯命中步骤记 `logging`（bff 现无日志设施，引入标准库，uvicorn 接管）；§4.3 声明入口不变量；§12 深链注明近似语义 | §3.2/§4.3/§5.4/§9/§12 |
| P6 | 次要项 6 条 | 低 | a) SearchResultList key 加 `kind`；b) 契约写明「event 节点 first_seen_ms 非空」+ Android `?.let` 守卫；c) 「三步阶梯」→「四步阶梯」措辞；d) 白名单校验「保留」→「新增」（现状 replay 无设备校验）；e) 周聚合 hot slot 合并加「同 local date」约束；f) Web 决策记录载体 = 本文档 | §3.2/§3.4/§4.2/§4.5/§5.4/§6/头部 |

---

## 1. 背景

### 1.1 v2 数据面变化（对 Android 的全部影响）

v2 后 lvdata 的查询面只有两类产物：**events 表**（SSOT）与 **day_summary 表**（按本地日 + LLM 生成）。session 摘要（session_summary/summary/video_summary）与 timeline.json 全部废弃；recording session 退化为存储层内部概念，不对上层暴露（上游 §3.2.7）。

bff 已完成迁移（`services/bff`，当前工作树），overview 响应的新形态：

| 字段 | 来源 | 说明 |
|---|---|---|
| `days[].summary` | `day_summary` 表（status=ok 时取其 JSON 的 `summary` 段） | LLM 整段日总结；pending/无行 → null（`timeline/service.py:_day_summary_text`） |
| `days[].hot_slots[]` | 服务端派生：当日 events 间隔 >5min 切段，≤3 + latest（`MERGE_GAP_MS`） | 无 session_id；replay 锚 = 段首事件时刻（start_ms，已核 `service.py:108`） |
| `days[].images[]` | 当日 video 事件按 `key_rank` 升序取前 3 | 含 `frame_id/ts_ms/event_ref/media_url`（`schemas.DayImage`） |
| `days[].has_pending` | 当日 `files.analysis_status ∈ (pending,failed)` 计数 > 0，实时派生 | 日粒度布尔 |
| `days[].topics` | **本期 bff 补透出**：day_summary JSON 的 `topics` 段（`{topic, detail}`） | 上游 §9.1.2 指定供数源 |
| `days[].sessions[]` | **残留**：`SessionItem` 仍在响应中，`summary=None`、`topics=[]` 已恒空 | 本期删除（§5.1） |
| `days[].counts` | 当日 events 按 type 计数 | 保留；Android 不消费，v3 继续不消费 |

Android 当前代码对照该响应的**断点清单**（代码事实，2026-08-26 复核）：

| # | 断点 | 位置 | 后果 |
|---|---|---|---|
| C1 | `OverviewDayDto` 只有 `date/counts/sessions`；`ignoreUnknownKeys=true` 把新字段全部吞掉 | `dto/MylifeDtos.kt:82-89` | 日卡拿不到 summary/hotSlots/images/hasPending/topics |
| C2 | `PeriodAggregator` 的输入是 `days[].sessions[]`；bff 侧 `SessionItem.summary/topics` 已恒空 | `timeline/PeriodAggregator.kt:93-116` | **所有卡片 summary 恒为降级行、topics 恒空** —— 线上即坏 |
| C3 | Android `HotSlot` 以 `sessionId + startMs` 为 replay 导航键；replay 端点即将不再接受 session_id | `PeriodAggregator.kt:18-24`、`TimelineViewModel.kt:120-126`、`ui/navigation/Routes.kt:21` | 导航模型失锚，必须重定义 |
| C4 | `hasPending` 与 60s 轮询条件仍按 `sessions[].analysis.timeline == PENDING` 判定 | `PeriodAggregator.kt:114`、`MylifeRepository.kt:66-83` | 与 bff 新语义（日粒度、含 failed）脱节 |
| C5 | Replay 端点仍是 `GET /replay/{session_id}`（bff 未实现锚定解析） | `services/bff/replay/router.py:11`、`MylifeApi.kt:69` | bff 前置依赖（§5.4） |
| C6 | 深链 `?session=` 反查依赖 overview.sessions[] | `TimelineViewModel.kt:41-72,133-147` | sessions[] 消亡后无反查数据源 |
| C7 | Graph 事件节点以 `node.sessionId` 为 replay 锚；appearance 点击带 sessionId 深链 | `GraphViewModel.kt:67`、`GraphScreen.kt:154-164`、`EntityDetailSheet.kt:130` | replay 路由改单参后编译失败（§6） |
| C8 | `SearchResultList` item key 引用 `hit.sessionId` | `SearchResultList.kt:93` | 删字段即编译失败（§4.5） |

### 1.2 目标

1. 日卡 summary 改读 `day_summary`（经 bff `days[].summary`），topics 改读 `days[].topics`（`{topic, detail}`），不再从 sessions 聚合。
2. hotSlots/images/hasPending 改读 bff 派生字段；`PeriodAggregator` 输入模型从「sessions[]」重构为「日字段」。
3. Replay 入口从 sessionId 锚改为 **ts_ms 主锚 + event_ref 第二锚**（bff 与 Android 同步修改，无兼容期）。
4. bff 查询面 session 字段全清（overview/search/graph/timeline/replay），符合上游 §9.1.1「session 维度不再暴露」。
5. 周/月/年卡本期**客户端聚合日卡**（上游 §8.2：period_summary 生成逻辑下一阶段；组装合并是兜底路径，UI 不变）。

### 1.3 非目标（本期不做）

- period_summary（周/月/年 LLM/组装摘要）消费 —— 下一阶段，仅预留切换点（§4.2）。
- Replay 播放内核（cue 编排、压缩时间轴、`ReplayTimeline.locateCueTs`、音频/画面渲染、SDK 互斥）—— 仅改「入屏参数」。
- **apps/web/mylife**：不再使用，代码保留、不维护、不兼容；删除属单独任务（§8.5/§9）。bff 不再为其保留任何字段。
- lvdata-mcp 本体、Agent Relay、reindex/eval CLI（上游文档自身范围）。

---

## 2. 信息架构变化

```
v2（现状，已断）:  overview(days[].sessions[]) ──PeriodAggregator──> 卡片(summary=降级行, topics=空)
v3:               overview(days[].summary/topics/hot_slots/images/has_pending)
                        │  day 卡：直读日字段
                        │  week/month/year 卡：PeriodAggregator 客户端聚合日卡（本期）
                        │  （下一阶段：period_summary 直接供数，UI 不变）
                        ↓ hot slot / 图片 / 搜索命中 / 图节点
                  Replay（ts_ms 主锚 / event_ref 第二锚；bff 内部解析 session，不暴露给客户端）
```

客户端的核心心智变化：**「日」成为最小内容单元**；session 从 UI 模型中彻底退出（卡片、导航、深链、轮询条件全部不再出现 sessionId），bff 查询面同步清零。

---

## 3. 数据契约

### 3.1 overview（`GET /api/mylife/overview`，bff 已派生大部分，本期补 topics/删 sessions）

```kotlin
@Serializable
data class OverviewDayDto(
    @SerialName("date") val date: String,
    @SerialName("counts") val counts: Map<String, Int> = emptyMap(),  // 保留透传，UI 不消费
    @SerialName("summary") val summary: String? = null,               // day_summary 整段日总结
    @SerialName("hot_slots") val hotSlots: List<HotSlotDto> = emptyList(),
    @SerialName("images") val images: List<DayImageDto> = emptyList(),
    @SerialName("has_pending") val hasPending: Boolean = false,
    @SerialName("topics") val topics: List<DayTopicDto> = emptyList(),  // 本期 bff 补透出（§5.2）
)

@Serializable
data class HotSlotDto(
    @SerialName("start_ms") val startMs: Long,
    @SerialName("end_ms") val endMs: Long,
    @SerialName("count") val count: Int,
)  // 无锚字段：replay 主锚 = startMs（= 段内首事件时刻，§3.2 精确匹配命中；已核 service.py:108）

@Serializable
data class DayImageDto(
    @SerialName("frame_id") val frameId: String,
    @SerialName("ts_ms") val tsMs: Long,
    @SerialName("event_ref") val eventRef: String,   // 图片入口 replay 第二锚（§4.3）
    @SerialName("media_url") val mediaUrl: String,   // 相对路径，Repository 出口绝对化（沿用）
)

@Serializable
data class DayTopicDto(
    @SerialName("topic") val topic: String,
    @SerialName("detail") val detail: String? = null,  // day_summary JSON 原始结构；本期透传备用，UI 不渲染（§9-O1）
)
```

**删除的 DTO**（无兼容期，随 sessions[] 消费路径一并移除）：`SessionItemDto`、`SessionStatsDto`、`AnalysisStatusDto`、`FrameItemDto`、`TopicItemDto`（被 `DayTopicDto` 取代）、`AnalysisState` 常量对象。

**保留的 DTO**：`OverviewDto`（壳）、`SearchResponseDto`、`GraphDto/EntityDto`（字段变化见 §3.4）、`ReplayCueDto/EmotionDto`。

### 3.2 replay（契约重定义 —— bff 前置依赖 §5.4，Android 侧按此实现）

```
GET /api/mylife/replay?ts_ms=<epoch ms>[&event_ref=<ref>][&speed=8.0]
```

**锚定解析（bff 内部，不暴露 session 概念）**：

1. `event_ref` 提供时优先：events 表 `WHERE event_ref=?`（唯一索引）→ rsession_id；无此事件 → 404 `not_found`。
2. 否则按 `ts_ms`，白名单设备内**四步阶梯**（P6-c）：
   - **① 事件精确匹配**：`WHERE ts_ms=?` 命中事件所在的 session（多命中取 `started_at` 最早的 session，平局按 rsession_id 字典序）—— 当前全部入口（hot slot 段首、图片、搜索事件命中、图节点 firstSeenMs）的 ts_ms 均为真实事件时刻（§4.3 入口不变量），此步使解析确定；
   - **② containment**：`started_at ≤ ts_ms ≤ finalized_at∨last_active_at∨∞`，最早开始者胜（与上游 §6.2.1 enrich 时间窗同规则）；**候选 session 须含 ≥1 事件，零事件 session 跳过（P2）**——零事件 session（有 recording_sessions 行、无 events）命中后 `build_replay` 会抛 `not_analyzed`，过滤后①②③结果均保证 ≥1 事件，`not_analyzed` 在新路径下不可达；
   - **③ 最近事件**：`|ts − ts_ms|` 最小的事件所在 session；
   - **④ 均无 → 404 `not_found`**。
3. **白名单校验（新增，P6-d）**：解析出的 rsession 必须属于白名单设备，否则 404 `not_found`（不泄露存在性）。注：现状 `build_replay` 直接 `get_session(session_id)`、**无设备校验**（知 session_id 即可拉取），本条款为新增安全强化，非「保留」。
4. 双参皆空 → 400 `bad_request`。
5. **解析可观测性（P5）**：每步命中记 `logging.getLogger(__name__).info("replay_resolve step=… anchor=…")`（bff 现无日志设施，本期引入标准库 logging，uvicorn 接管输出；为后续 metric 留点）。①②③ 命中分布漂移 = §4.3 入口不变量被新调用方破坏的早期信号。

**响应体**：`ReplayResponse.session` 精简为单字段 `session_date`（屏内标题用）；`cues` 编排与 `duration_ms` 不变；`session_id/device_uuid/started_at/ended_at/time_span_ms` 全部删除。

Android 对 `?session=` 路径参数与 `ReplayNavigation.sessionId` 全部下线（§4.3）。

### 3.3 search（契约仅删 session_id；**topic 命中 ts_ms 语义事实更正，P1**）

bff search 契约：事件级命中（chat/transcript/caption，`ts_ms` = 事件时刻）+ topic 命中（day_summary.topics）。**删除 `SearchHit.session_id`**（Web 不再使用）；其余字段不变。

> **事实更正（P1）**：topic 命中的 `ts_ms` **不是 null**，而是所属 session 的 `started_at`（`_parse_started` 结果；仅 started_at 缺失时为 null）——`_hit` 对 `ts_ms` 直通透出（`search/service.py:64-66` 调用、`:100-113` 直通）。**因此 Android 不得以 `tsMs != null` 作为导航分派判据**：topic 命中几乎全部携带非空 tsMs（session 起始时刻，非事件时刻），tsMs 判据会把 topic 命中错误导航进 Replay。

Android 侧导航分派（判据 = `kind`，现有字段、零 bff 改动）：

- `hit.kind != "topic"`（chat/transcript/caption 事件命中）→ `ReplayNavigation(hit.tsMs!!)`——bff 事件分支恒传 `e["ts_ms"]`（`search/service.py:69-77`），非空断言安全（契约保证，§5.7 钉死）；
- `hit.kind == "topic"` → **日卡定位** `locateTo(hit.date)`（复用 §4.6 的 date 深链机制）。

bff 行为本期不变（topic 命中 ts_ms 维持现状透出）；§5.7 加钉死断言防无意漂移（若未来改为 null，须同步复核 Android 判据——kind 判据在两种语义下均正确）。

### 3.4 graph（bff 删 session_id + appearance 去重 + topic weight 修复；Android 最小配套）

- bff：`GraphNode.session_id` 删除；`EntityAppearance.session_id` 删除；**`build_entity` appearances 按 `session_date` 去重**（同日多 session 合并一条，保最新）—— Android date-only 键唯一性的服务端保证。`GraphStats.sessions`（计数）保留。不新增 `event_ref`（ts_ms 锚足够，§0-R2）。
- **event 节点 `first_seen_ms` 非空保证（契约条款，P6-b）**：event 节点由 `_accumulate` 以 session 首事件 `first_ts` 建点（`_touch` 恒赋非空值），bff 对 event 节点恒输出非空 `first_seen_ms`；Android DTO 层类型为 `Long?`（`MylifeDtos.kt:128`），消费处用 `?.let` 守卫。
- **topic 节点 weight 修复（P4）**：`_accumulate` 中 `node.weight += int(t.get("mentions") or 0)` 的 mentions 数据源已随 session_summary 消亡 → topic 节点 weight 恒 0 → 被 `min_weight=1` 过滤（`graph/service.py:116`），**星图当前实际不出 topic 节点**（现存静默退化）。本期修复为 `weight += 1`（每 session 出现计 1，与 emotion/person/scene/app/object 同语义，§5.6）。
- Android：`GraphNodeDto` 删 `sessionId`；`EntityAppearanceDto` 删 `sessionId`；event 节点 Play → `ReplayNavigation(node.firstSeenMs)`；appearance 点击 date-only（§6）。

### 3.5 timeline（bff 删 session_id，附注）

`TimelineEvent.session_id` 删除（上游 §9.1.1 全清）。Android 未消费 timeline 端点（`MylifeApi.kt:42-73` 仅 overview/graph/entity/replay/search），无客户端改动。

---

## 4. Android 详细设计

### 4.1 领域模型重构（`timeline/PeriodAggregator.kt` 同文件）

```kotlin
/** 活跃时段（v3：无 sessionId；replay 导航键 = startMs，§4.3）。 */
data class HotSlot(
    val startMs: Long,
    val endMs: Long,
    val count: Int,
    /** 所属本地日（yyyy-MM-dd）；周/月/年 chip 日期前缀用；客户端聚合时继承自日 key。保留，勿删。 */
    val date: String,
)

/** 卡片缩略图（v3：无 sessionId；tsMs 定位 + eventRef 精确锚，§4.3）。 */
data class PeriodImage(
    val url: String,      // 绝对 media URL（Repository 出口已附 token）
    val tsMs: Long,
    val eventRef: String, // bff DayImage.event_ref 非空（events.event_ref NOT NULL UNIQUE）
)

data class PeriodDigest(
    val key: String,
    val granularity: Granularity,
    val rangeLabel: String,
    val hotSlots: List<HotSlot>,
    val latestSlot: HotSlot?,      // 时序最后一段（合并后）；周卡本体 replay 入口
    val summaryLines: List<String>,
    val summaryLimit: Int,         // DAY≤3 / WEEK≤4 / MONTH≤5 / YEAR≤6（沿用）
    val topics: List<DayTopicDto>, // day_summary 供数；chip 只显示 topic 文本（PeriodCard.kt:88）
    val images: List<PeriodImage>,
    val hasPending: Boolean,
    // 移除：dayCount、sessionCount（均未渲染；降级行「共 N 天」由聚合器内联计算）
)
```

### 4.2 PeriodAggregator v3：日直读 + 周以上聚合日卡

仍为纯函数、无网络、可单测；输入从 `List<OverviewDayDto>` 的 sessions[] 改为日字段。**「保持 BFF 顺序」原则延伸到 `day.images`**（bff 已按 key_rank 升序）与 `day.hotSlots`（bff 已 ≤3+latest）。

| PeriodDigest 字段 | DAY（直读） | WEEK / MONTH / YEAR（客户端聚合日卡） |
|---|---|---|
| `summaryLines` | `listOfNotNull(day.summary)`；空 → **不渲染 summary 块**（pending 脉冲点已兜底） | 各日 `summary` 非空去重、按日期**倒序**；limit W4/M5/Y6 折叠（沿用）；全空 → 降级行「{rangeLabel}共 N 天」 |
| `hotSlots` | `day.hotSlots` 直接映射（`date` = 日 key） | 各日 slots 按 startMs 升序、**MERGE_GAP_MS=5min 客户端再合并，且仅同 local date 的相邻段参与合并（P6-e）**——跨午夜段（如 23:58/00:02）不合并，chip 日期前缀恒正确；`date` 继承合并段首段来源日；≤3 + latest（合并后时序最后一段为 latestSlot） |
| `images` | `day.images`（bff 已截 3） | flatMap 各日 images（保持日倒序 + 日内 key_rank 序），截 W6/M9/Y15 |
| `topics` | `day.topics` | 各日 topics 按日倒序 flatMap、按 `topic` 首见去重（detail 随同），≤5（MAX_TOPICS 沿用） |
| `hasPending` | `day.hasPending` | `days.any { it.hasPending }` |
| `latestSlot` | 时序最后一段 | 同左（合并后） |

设计要点：

- **hot slot 跳段语义简化**：v1 的「有图 slot 优先」选取依赖 session.frames，新模型下 bff 日级 slots 已是终态（≤3+latest），客户端周以上合并不再做图优先排序（统一按 startMs 升序截断 + latest 保留）。点击可进性由「pending 跳过」改为**不过滤**——replay 以 ts_ms 解析，无事件则 bff 404，UI 按既有 error 路径提示（pending 日的 slot 通常为空，自然不可点）。
- **slot 语义声明**：段由当日跨 session 的 events 流按 5min gap 切出，可跨 session 边界；replay 回放 = **锚定事件所在 session 的 cues**，槽位时间范围与回放覆盖可能不完全一致（§9-R7，产品接受）。
- **粒度 key**（ISO 周 `yyyy-Www`、月 `yyyy-MM`、年 `yyyy`）、`rangeLabel`、下钻 scope 过滤（`aggregate(days, granularity, scopeKey)`）全部不变。
- **§12 切换点**：`PeriodAggregator.aggregate` 的签名与输出不变——period_summary 阶段只需把 `summaryLines`（及可选 topics）的数据源换成新端点/新字段，UI 与聚合骨架零改动。

### 4.3 Replay 重锚：ts_ms 主锚 + event_ref 第二锚

**导航模型**（`TimelineViewModel.kt`）：

```kotlin
data class ReplayNavigation(
    val tsMs: Long,               // 必选：bff 解析主锚 + locateCueTs 定位基准（所有入口都有确定时刻）
    val eventRef: String? = null, // 可选：图片入口的第二锚（bff 优先按此解析）
)
```

> **入口不变量（P5，契约条款）**：所有 `ReplayNavigation(tsMs)` 的 `tsMs` **必须是真实事件时刻**（events 表某行的 ts_ms）——bff 精确匹配步骤（§3.2-①）的确定性依赖此前提。当前四入口已核验成立（hot slot 段首 = `service.py:108`、图片 ts_ms、搜索事件命中 ts_ms、图节点 firstSeenMs = `_accumulate` 首事件 first_ts）。**未来新入口**：若导航源不携带真实事件时刻，须改用 event_ref 或显式接受 ②③ 近似语义；各构造处注释本不变量。
>
> **⚠ 2026-09-20 更新（Q6 supersede）**：**卡片图片入口已废除**——图片点击改为进入 Gallery 帧网格
> （`docs/android_timeline_image_gallery.md` §3.2 R2/Q6），不再发 `ReplayNavigation`；本表「卡片图片」行与
> 上文「四入口」枚举随之过期（现为三入口，bff replay 解析日志的入口分布基线同步变化）。

**入口映射**：

| 入口 | 现状 | v3 |
|---|---|---|
| hot slot chip（全粒度） | `ReplayNavigation(slot.sessionId, slot.startMs)` | `ReplayNavigation(slot.startMs)` |
| 周卡本体 | `latestSlot.sessionId/startMs` | `ReplayNavigation(latestSlot.startMs)` |
| 卡片图片（**新可点击**） | 不可点 | ~~`ReplayNavigation(img.tsMs, img.eventRef)` —— 图片区由此成为正式 replay 入口~~ **2026-09-20 废除**：点击改进入 Gallery（`docs/android_timeline_image_gallery.md` Q6），不再走 Replay |
| 搜索命中（**P1 修复：kind 判据**） | `(hit.sessionId, hit.tsMs)` | `hit.kind != "topic"` → `ReplayNavigation(hit.tsMs!!)`；`hit.kind == "topic"` → 日卡定位 `locateTo(hit.date)`（**不得以 `tsMs != null` 分派**，§3.3 事实更正） |
| 图节点（§6） | `(node.sessionId, …)` | `node.firstSeenMs?.let { ReplayNavigation(it) }`（event 节点；first_seen_ms 非空保证见 §3.4，DTO `Long?` 用 `?.let` 守卫，P6-b） |

**路由**（`ui/navigation/Routes.kt`、`NavGraph.kt`）：

> **2026-09-22 已移除（见 docs/android_timeline_hotslot.md §14 R3=C）**：下述 `MYLIFE_REPLAY` / `mylifeReplay` 路由与
> Replay 屏栈、`MylifeApi/Repository.replay`、`ReplayDto` 系 DTO 已整栈删除；本节为历史实施记录（恢复见 git 历史）。

```kotlin
const val MYLIFE_REPLAY = "mylife/replay?ts_ms={tsMs}"
fun mylifeReplay(tsMs: Long, eventRef: String? = null) =
    "mylife/replay?ts_ms=$tsMs" + (eventRef?.let { "&event_ref=${Uri.encode(it)}" } ?: "")
```

- 删除 `session`/`t` 双参数路由与 `Uri.encode(session)`；`ts_ms` 为 Long 必选，`-1` 兜底哨兵与 NavGraph 兼容分支随之删除（`NavGraph.kt:139-145`）。
- `NavGraph` 该路由参数解析：`ts_ms`（long 型）+ `event_ref`（string 型，defaultValue null）。

**Replay 屏**：

- `ReplayViewModel.load(sessionId, initialT)` → `load(tsMs, eventRef)`：`repository.replay(tsMs, eventRef)` 取数；初始定位沿用 `ReplayTimeline.locateCueTs(cues, tsMs)`（按 cue.tsUtc 找最近 cue，**零改动**）；定位后自动播放语义不变。
- `ReplayScreen` 路由参数读取从 `session`/`t` 改 `ts_ms`/`event_ref`（`ReplayScreen.kt:39,58`）；`ReplayUiState` 不变。
- 标题 fallback：`uiState.session?.sessionDate ?: sessionId` → `uiState.session?.sessionDate ?: "回放"`（`ReplayScreen.kt:148`）。
- 错误映射：`not_found` → 「该时刻没有录制内容」；`not_analyzed` 语义消失——**前提由 §3.2-② 零事件过滤补齐（P2）**：①②③ 解析结果均保证 session 含 ≥1 事件，`build_replay` 的 `not_analyzed` 分支在新路径下不可达——错误文案表更新。

### 4.4 Repository（`MylifeRepository.kt`）

- `refreshOverview`：端点不变；`absolutizeFrames()` 改为遍历 `days[].images[].mediaUrl`（原 sessions[].frames 路径删除）。
- 60s 轮询条件：`hasPendingAnalysis()` 改判 `days.any { it.hasPending }`（一处判定，init 块与公开方法同源，`MylifeRepository.kt:66-83`）；轮询节奏不变。
- `replay(sessionId)` → `replay(tsMs: Long, eventRef: String? = null)`：`api.replay(tsMs, eventRef)` + cues 媒体 URL 绝对化（不变）。
- `MylifeApi.replay`：`execute("replay", mapOf("ts_ms" to …, "event_ref" to …, "speed" to …), …)`（路径参数改查询参数，`MylifeApi.kt:68-69`）。
- Graph LRU、search 直通、错误信封解析不变。

### 4.5 UI 组件

| 组件 | 改动 |
|---|---|
| `PeriodCard.kt` | 图片格 `AsyncImage` 加 `clickable { onImageClick(img) }`（新回调参数）；topics chip 渲染**零改动**（换 DTO 类型，`PeriodCard.kt:88` 只显示 topic 文本）；summary 块/hot chips/标题/脉冲点**不动** |
| `HotSlotChips.kt` | 零改动（`HotSlot.date` 保留，粒度前缀逻辑不变） |
| `TimelineScreen.kt` | 新增 `onImageClick` 透传；`onNavigateToReplay` 通道签名改 `(tsMs: Long, eventRef: String?)`（`TimelineScreen.kt:101,132`）；搜索命中点击按 §3.3 的 **kind 判据**分派（`:190`，P1） |
| `SearchResultList.kt` | **item key 改复合键（含 kind，P6-a）**：`"hit-${hit.kind}-${hit.date}-${hit.tsMs}-${hit.snippet.hashCode()}"`（`:93` 原引用 `hit.sessionId`，删字段即编译失败，§0-R10；去 sessionId 后同日同 session 多 topic 命中 `date+tsMs` 全同，区分全靠 hashCode，碰撞即 crash，补 `kind` 提高区分度） |
| `GranularitySelector/ScopePill/SearchBar` | 不变 |
| `MylifeHomeScreen.kt` | 不变（Graph 通道改动见 §6） |

### 4.6 深链（`TimelineViewModel.onInitialParams`）

- `?date=` 保留不变（切日粒度 + locateKey 滚动定位）。
- **`?session=` 废弃**：sessions[] 消亡后无反查数据源；删除 `pendingSessionLocate` 挂起机制与 overview collect 内的反查块（`TimelineViewModel.kt:41-72,133-147`）。**上线前任务**：grep 通知/其他屏/埋点等外部 `?session=` 与 sessionId 调用方，统一改为 `?date=`（§8.5-O3）。
- 不新增 `?ts_ms=` 直进 replay 深链（本期无需求方；路由已支持，未来加一个 navigate 调用即可——注意其走 ②③ 近似语义，见 §12）。

---

## 5. bff 实施清单（单阶段，无兼容期）

### 5.1 schemas（`common/schemas.py`）

- `OverviewDay`：加 `topics: list[DayTopicItem]`（新增 `DayTopicItem{topic: str, detail: str | None}`，对齐 day_summary JSON §5.4 结构）；删 `sessions`。
- 删除随 sessions 一并废弃的 schema：`SessionItem`、`SessionStats`、`AnalysisState`、`FrameItem`、`TopicItem`（已核对：引用仅 `timeline/service.py` 与 schemas 自身，§11）。
- `SearchHit`：删 `session_id`。
- `GraphNode`：删 `session_id`；`EntityAppearance`：删 `session_id`。
- `ReplaySessionInfo` 精简为 `session_date: str | None` 单字段。
- `TimelineEvent`：删 `session_id`。
- `OverviewResponse`：删 `total_sessions`（无消费者；连带 `timeline/service.py:86` 与 3 处测试断言）。

### 5.2 timeline service（`timeline/service.py`）

- 新增 `_day_topics()`：读 day_summary doc.topics（status=ok 才透出，否则空；复用 `graph/service.py:235 _session_topics` 的写法），供 overview `days[].topics`。
- 删除 sessions 构建路径（`_to_session_item`/`_extract_frames`/`SessionItem` 相关调用）；day 发现保留「遍历 recording_sessions 归日 + events 时间修正」现有循环（§9.1.1 语义），仅不再产出 `SessionItem`。
- `_derive_hot_slots`/`_derive_images`/`_day_summary_text` 不变（hot slot 锚 = 段首事件时刻，已核 `service.py:108`，客户端 startMs 即可，schema 零增量）。

### 5.3 store helpers（`common/store.py`）

新增只读 helper（events 表）：

- `event_by_ref(ref) -> dict | None`：`SELECT … FROM events WHERE event_ref=?`（唯一索引，`event_ref TEXT NOT NULL UNIQUE`，§11 DDL）。
- `resolve_session_at(t_ms, devices) -> str | None`：白名单设备内按 §3.2 阶梯解析——① 精确匹配 `WHERE ts_ms=?`（多命中取 `started_at` 最早的 session，平局 rsession_id 字典序）→ ② containment（`started_at ≤ t_ms ≤ finalized_at∨last_active_at∨∞`，最早开始者胜，**候选过滤零事件 session：`EXISTS (SELECT 1 FROM events WHERE rsession_id=…)`，全部零事件则继续 ③（P2）**）→ ③ `|ts − t_ms|` 最近事件；返回 rsession_id 或 None（索引：`idx_events_ts` 支撑；containment 走 `list_sessions` + `timeline_events` 头尾检查的现有路径）。

### 5.4 replay（`replay/router.py` + `replay/service.py`）

- 路由 `GET /replay/{session_id}` → `GET /replay`，query 参数 `ts_ms: int | None`、`event_ref: str | None`、`speed: float = 8.0`；两者皆空 → 400 `bad_request`。
- `build_replay` 入口改为锚定解析（§3.2）：event_ref 优先 → ts_ms 阶梯 → 404；**白名单校验为新增（P6-d）**——现状无设备校验（知 session_id 即可拉取），解析出的 rsession 不在白名单设备 → 404（不泄露存在性）；cue 生成逻辑与媒体路径解析不变。
- **解析日志（P5）**：每步命中记 `logging.getLogger(__name__).info(...)`（bff 现无日志设施，本期引入标准库 logging，uvicorn 接管输出）；字段含 step(①/②/③)、anchor 类型、是否命中。
- `ReplayResponse.session` 只回 `session_date`。

### 5.5 search（`search/service.py`）

`_hit` 删除 `session_id` 输出；其余不变（topic 命中已读 day_summary.topics，`search/service.py:38-97`）。**topic 命中 `ts_ms` 透出行为本期不变**（= session started_at，§3.3 事实更正），测试钉死（§5.7）。

### 5.6 graph（`graph/service.py`）

- `_to_node` 不再输出 `session_id`（`_Node.session_id` 内部字段可随删）。
- `build_entity`：appearances 删 `session_id`；**按 `session_date` 去重**（同日多 session 合并一条，保最新——配合 Android date-only 深链与列表 key 唯一性，§6）。
- **topic 节点 weight 修复（P4）**：`_accumulate` 中 topic 分支 `node.weight += int(t.get("mentions") or 0)` → `node.weight += 1`（每 session 出现计 1；mentions 数据源已随 session_summary 消亡，恒 0 导致 topic 节点被 `min_weight=1` 过滤——现存静默退化，本期顺手修复）。
- 不新增 `event_ref` 字段（§0-R2）。

### 5.7 bff 测试

- `test_timeline_api.py`：sessions/total_sessions 断言 → topics 断言（含 pending/无行 → 空列表）。
- `test_replay_api.py`：`event_ref` 锚（命中/404）、`ts_ms` 锚（精确匹配/containment/nearest/404）、**containment 跳过零事件 session → 落 ③ 最近事件（P2）**、白名单拒绝（**含「现状无校验 → 新增校验」的安全用例**，P6-d）、双参皆空 400、精确匹配平局规则。
- `test_search_api.py`：`session_id` 断言删除；**钉死 topic 命中 `ts_ms` = session started_at（P1 防漂移）与事件命中 `ts_ms` 非空**。
- `test_graph_api.py`：`session_id` 断言删除；appearances 同日去重（多 session 同日 → 单条）；**topic 节点 weight ≥ 1 且不被 `min_weight=1` 裁掉（P4）**；event 节点 `first_seen_ms` 非空（§3.4 契约条款，P6-b）。
- `conftest.py` 如构造旧 overview 数据则同步。

---

## 6. Graph 配套

bff graph/entity 端点本期删 `session_id` 字段 + appearance 去重 + topic weight 修复（§5.6）；Android 仅改导航消费：

| 位置 | 现状 | v3 |
|---|---|---|
| `GraphViewModel.kt:67` | `entitySessionId = node.sessionId` | `entityReplayTsMs = node.firstSeenMs`（`Long?` 原样存放；event 节点非空保证见 §3.4，消费处 `?.let` 守卫，P6-b；实体节点不设，`GraphScreen.kt:155` 的 `kind == "event"` 分支语义不变） |
| `GraphScreen.kt:65,162-164` | `onNavigateToReplay(sessionId, t)`、`onNavigateToTimeline(date, sessionId)` | `onNavigateToReplay(tsMs: Long)`、`onNavigateToTimeline(date: String)`（date-only）；Play 分支 `entityReplayTsMs?.let { ts -> onNavigateToReplay(ts) }` |
| `EntityDetailSheet.kt:42,128-130` | `onAppearanceClick(date, sessionId)`，key 含 sessionId | `onAppearanceClick(date: String)`；LazyColumn key 改 `appearance.sessionDate`（唯一性由 bff 同日去重保证，§5.6） |
| `dto/MylifeDtos.kt:123-131,156-160` | `GraphNodeDto.sessionId`、`EntityAppearanceDto.sessionId` | 删除字段（bff 同步删除，无 ignoreUnknownKeys 依赖） |

---

## 7. 实施计划

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **A0 — bff** | §5 全部（schemas/timeline/store/replay/search/graph + bff 测试全绿） | — |
| **A1 — DTO/Repository** | §3.1/§3.3/§3.4/§3.5 DTO + §4.4（absolutize/轮询/replay(tsMs,eventRef)）+ SearchHitDto/GraphNodeDto/EntityAppearanceDto 字段删除 | A0 契约定稿（可先行） |
| **A2 — 聚合层** | §4.1/§4.2 PeriodAggregator v3 + `PeriodAggregatorTest` 重写 | A1 |
| **A3 — 导航/Replay** | §4.3（Routes/NavGraph/TimelineViewModel 入口映射**含 kind 判据（P1）**/ReplayViewModel.load(tsMs,eventRef)/ReplayScreen 标题与错误文案） | A0（联调硬依赖） |
| **A4 — UI/深链** | §4.5（PeriodCard 图片可点/SearchResultList key/TimelineScreen 接线）+ §4.6 深链清理 | A2 |
| **A5 — Graph** | §6 | A3 |
| **A6 — 回归** | §8 单测 + 真机冒烟 + §8.5 发版注记 | A0-A5 |

---

## 8. 测试计划

**bff 单测**：`pytest services/tests/unit/bff/`（timeline/replay/search/graph 四文件全绿，§5.7）。

**Android 单测**（`./gradlew :LivaChatApp:testDebugUnitTest`）：

- `PeriodAggregatorTest` 重写：
  - 日卡直读（summary 透传/空 summary 降级/hasPending 直读/images 透传/topics 透传）；
  - 周聚合：ISO 跨年多日 → 合并 slot（5min gap 边界 + **跨午夜段不合并（P6-e）**）/ ≤3+latest / images 截 6 / summary 去重倒序截 4 / topics 首见去重截 5；
  - 月/年截断配额（9/15）与降级行「共 N 天」；
  - 既有 scope 过滤、ISO 周 key、rangeLabel 用例保留（输入 DTO 换形）。
- `ReplayTimelineTest`：locateCueTs 既有用例不变（函数零改动）。
- ViewModel 测试（如有既有 harness）：hot slot → `ReplayNavigation(tsMs)`；图片 → `(tsMs, eventRef)`；**搜索分派用真实语义 fixture（P1）：`kind="transcript"` 且 `tsMs` 非空 → `ReplayNavigation(tsMs)`；`kind="topic"` 且 `tsMs` 非空（= session started_at，与 bff 钉死断言一致）→ `locateTo(date)`**；graph Play → `(firstSeenMs)`。

**真机/模拟器冒烟**（对齐 bff 已迁移环境）：

1. 日卡显示 day_summary 文案 + topics 行；pending 日显示「分析中…」且 summary 缺席；
2. hot slot chip → Replay 落在对应时刻自动播放；
3. 图片点击 → Replay 定位该帧附近（event_ref 解析）；
4. 搜索 transcript 命中 → Replay；**topic 命中（tsMs 非空的真实数据）→ 日卡定位（P1 冒烟覆盖点）**；
5. 星图 event 节点 Play → Replay；appearance 点击 → 日卡定位（同日多段 appearance 合并为单条）；**星图出现 topic 节点（P4 修复验证）**；
6. 60s 轮询：分析完成后日卡自动从 pending 转为显示摘要。

---

## 8.5 发布、回滚与在途版本影响（P3）

### 8.5.1 在途旧版影响矩阵（A0 部署瞬间生效）

| 功能 | 旧版现状 | A0 后旧版 | 机制 |
|---|---|---|---|
| 日卡 summary/topics | **已坏**（恒降级行/空，C2） | 仍坏 | sessions[] 删除——已坏功能，无增量损失 |
| 搜索 | 可用 | **报错（internal）** | `SearchHitDto.sessionId` 非空 String 无默认值（`MylifeDtos.kt:101`），字段删除 → `MissingFieldException` → `MylifeApiException("internal")` |
| 实体详情（appearance 列表） | 可用 | **报错（internal）** | `EntityAppearanceDto.sessionId` 非空无默认（`MylifeDtos.kt:157`），同上 |
| Graph 图本体 | 可用 | 可用 | `GraphNodeDto.sessionId` 可空有默认（`:130`），字段缺失被吞掉 |
| Replay | 可用 | **404 → internal** | 路由删除 + `ReplaySessionInfoDto.sessionId/deviceUuid` 非空无默认（`:176-177`） |

### 8.5.2 决策（2026-08-26，与 Web 决策同批记录）

**接受一刀切，不保留旧路径窗口期。** 理由：① mylife 主界面（日卡）对旧版已坏，用户无滞留旧版的可用场景，bff 上线即推动升级；② 保留双轨 = B 稿 Phase A/B，其唯一前置（Web 联动）已被 Web 决策消除；③ 全部删除项为查询面派生数据，无 DB 迁移。

### 8.5.3 回滚

所有删除字段均由 events/day_summary 派生、无数据迁移：**bff 回滚 = 重部署旧镜像**（纯查询面变更，回滚友好）；Android 回滚 = 旧版 APK（其体验 = 上表「A0 后旧版」列，与当前线上坏态一致）。

### 8.5.4 发版注记与上线前任务（O3 扩展）

- 发版注记声明：旧版 mylife（搜索/实体详情/Replay）随服务端上线停用，引导升级；
- 历史推送/外部入口的 `?session=` 深链静默失效（落 Timeline 默认态，不崩溃）——发版注记声明废弃；
- **上线前 grep**：`?session=` 外部调用方（通知/其他屏）统一改 `?date=`；**并扩展盘点埋点（analytics）是否携带 sessionId 维度**（`GraphViewModel` 已有 analytics 调用先例）——服务端字段删除后，埋点若引用已删 DTO 字段将编译失败（随删随修），下游看板若依赖 sessionId 维度需同步声明断流。

---

## 9. 风险与开放问题

1. **A0 未落地即开发 A3 的风险**：replay 锚定是硬依赖；A1/A2 可先行，A3 起联调。若 bff 延期，临时方案是 HotSlot/图片不可点（不发版）。
2. **日卡 summary 文案长度**：day_summary 是整段 2-3 句（上游 §5.4.1），日卡全显不折叠（沿用 v2 决策）；若实际超长，UI 侧加 `maxLines` + 展开，不进本期。
3. **周以上卡 summary 重复感**：多日 summary 拼接存在语义重叠（上游 §14.3 同款降本问题在客户端的投影）；period_summary 阶段解决，本期接受。
4. **event_ref 仅图片入口**：其他入口（hot slot/搜索/图节点）如需帧级精度，bff 解析已支持 event_ref，扩展仅限导航参数，成本低。
5. **无事件日的 hot slot**：debounce 窗口内当日 slot 已在 events 落地（chat 事件先于分析），slot 可点且 replay 可用（chat 部分），与「分析中…」状态共存——语义自洽，不特殊处理。
6. **跨 session 合并 slot（评审新增，已接受）**：5min gap 合并可跨 session 边界，replay 只回锚定 session 的 cues；槽位时间范围与回放覆盖可能不一致。契约语义 = 「slot 回放锚定段首事件所在 session」。
7. **ts_ms 精确匹配平局**：同一 ts_ms 多事件（罕见）取最早 started_at 的 session，平局按 rsession_id 字典序——规则已在 §3.2 定义，测试覆盖。
8. **Web 遗留**：`apps/web/mylife` 保留不维护（其代码将随 bff 删除字段而整体不可用——不再使用，可接受）；删除属单独任务，不入本期。
9. **`EntityDetailSheet` 同日多段 appearance**：键唯一性由 bff 同日去重保证（§5.6）；若去重回归，列表 key 冲突仅表现为显示压缩，无崩溃。
10. **入口不变量被未来调用方破坏（P5 残留风险）**：`ReplayNavigation(tsMs)` 的确定性依赖「tsMs = 真实事件时刻」（§4.3 契约条款）。防回归机制 = bff 解析日志（§5.4，①②③ 命中分布漂移可观测）+ 构造处注释；接受残余风险。
11. **O1（待产品确认）`DayTopicDto.detail` 是否入 UI**：本期 chip 只显示 topic 文本（与现状渲染一致，无回归）；detail 已契约透传，入 UI 则 chip 副行/展开，不动聚合骨架。

---

## 10. 附 A：文件级改动清单

**bff**：

| 文件 | 动作 | 要点 |
|---|---|---|
| `services/bff/common/schemas.py` | 改 | OverviewDay 加 topics 删 sessions；DayTopicItem 新增；删 Session 系/TopicItem schema；SearchHit/GraphNode/EntityAppearance/TimelineEvent 删 session_id；ReplaySessionInfo 精简；OverviewResponse 删 total_sessions |
| `services/bff/timeline/service.py` | 改 | `_day_topics`；删 SessionItem 构建路径；删 total_sessions |
| `services/bff/common/store.py` | 改 | `event_by_ref`/`resolve_session_at`（② 含零事件过滤，P2） |
| `services/bff/replay/router.py` | 改 | query 参数锚定 |
| `services/bff/replay/service.py` | 改 | 锚定解析 + 白名单（新增，P6-d）+ 解析日志（P5）+ session 精简 |
| `services/bff/search/service.py` | 改 | `_hit` 删 session_id（topic ts_ms 透出不变，P1 钉死） |
| `services/bff/graph/service.py` | 改 | `_to_node` 删 session_id；`build_entity` 删 session_id + 按 session_date 去重；**topic weight += 1（P4）** |
| `services/tests/unit/bff/test_timeline_api.py` | 改 | sessions/total_sessions → topics 断言 |
| `services/tests/unit/bff/test_replay_api.py` | 改 | 锚定/404/白名单/400/**零事件 containment（P2）**/平局 |
| `services/tests/unit/bff/test_search_api.py` | 改 | session_id 断言删除 + **topic ts_ms 钉死（P1）** |
| `services/tests/unit/bff/test_graph_api.py` | 改 | session_id 删除 + appearance 去重 + **topic weight（P4）+ event 节点 first_seen_ms 非空（P6-b）** |
| `services/tests/unit/bff/conftest.py` | 改 | 如构造旧 overview 数据则同步 |

**Android**：

| 文件 | 动作 | 要点 |
|---|---|---|
| `dto/MylifeDtos.kt` | 改 | overview 新 DTO（含 DayTopicDto）；删 Session/Analysis/Frame/TopicItem 系 DTO；删 SearchHitDto.sessionId、GraphNodeDto.sessionId、EntityAppearanceDto.sessionId；ReplaySessionInfoDto 精简 sessionDate |
| `data/remote/mylife/MylifeApi.kt` | 改 | `replay(tsMs, eventRef)` 查询参数化 |
| `data/repository/MylifeRepository.kt` | 改 | absolutize 改 day.images；轮询改 hasPending；replay(tsMs, eventRef) |
| `ui/screens/mylife/timeline/PeriodAggregator.kt` | 重构 | 模型 + 日直读/周以上聚合（§4.1/§4.2，含同 local date 合并约束 P6-e） |
| `ui/screens/mylife/timeline/TimelineViewModel.kt` | 改 | ReplayNavigation(tsMs, eventRef?)；入口映射（**搜索 kind 判据 P1**）；删 pendingSessionLocate；构造处注入口不变量（P5） |
| `ui/screens/mylife/timeline/TimelineUiState.kt` | 微调 | 无结构性变化（periods 模型变） |
| `ui/screens/mylife/timeline/TimelineScreen.kt` | 改 | onImageClick 透传；onNavigateToReplay(tsMs, eventRef?)；搜索命中 kind 分派 |
| `ui/screens/mylife/timeline/components/PeriodCard.kt` | 改 | 图片可点；topics chip 换 DTO 类型（渲染不变） |
| `ui/screens/mylife/timeline/components/HotSlotChips.kt` | 不变 | — |
| `ui/screens/mylife/timeline/components/SearchResultList.kt` | 改 | item key 改 **kind**-date-tsMs-snippet 复合键（§0-R10 + P6-a） |
| `ui/navigation/Routes.kt` / `NavGraph.kt` | 改 | ts_ms 单参 + event_ref 可选；删 -1 兜底分支 |
| `ui/screens/mylife/replay/ReplayViewModel.kt` | 改 | load(tsMs, eventRef)；错误文案表（not_analyzed 删除，前提 §3.2-② P2） |
| `ui/screens/mylife/replay/ReplayScreen.kt` | 改 | 路由参数读取；标题 fallback「回放」 |
| `ui/screens/mylife/graph/GraphViewModel.kt` | 改 | entitySessionId → entityReplayTsMs(node.firstSeenMs，Long? 存放，P6-b) |
| `ui/screens/mylife/graph/GraphScreen.kt` | 改 | onNavigateToReplay(tsMs)/onNavigateToTimeline(date) 签名；Play 分支 `?.let` 守卫 |
| `ui/screens/mylife/graph/EntityDetailSheet.kt` | 改 | appearance 点击 date-only；key 改 sessionDate |
| `src/test/**/PeriodAggregatorTest.kt` | 重写 | §8 |

---

## 11. 附 B：代码锚点（调查依据，2026-08-26 评审复核）

- 上游契约：`docs/data/lvdata_mcp_service_v2.md` §9.1.1（session 维度不暴露）、§9.1.2（summaryLines/topics 由 day_summary 供数 + 服务端派生 + replay 锚定）、§9.2（Android 无兼容期）、§8.2（period_summary 预留）、§5.4（topics `{topic, detail}`，:349）
- day_summary 数据：`tools/lvdata-mcp/src/lvdata_mcp/summary/day.py:41`（REQUIRED_KEYS 含 topics）、`:50`（topics 元素结构 `{topic, detail}`，无 mentions）
- events DDL：`tools/lvdata-mcp/src/lvdata_mcp/ddl.py:23`（`event_ref TEXT NOT NULL UNIQUE`）
- bff overview 派生：`services/bff/timeline/service.py:46-144`（build_overview/_derive_hot_slots **:108 start_ms = 段首事件 ts_ms**/_derive_images/_day_summary_text）；`_to_session_item` 恒空 summary/topics：`:147-166`；schema：`services/bff/common/schemas.py:29-31`（TopicItem 无默认值）、`:64-97`（OverviewDay 无 topics，sessions 残留）
- bff replay 现状：`services/bff/replay/router.py:11`（`/replay/{session_id}`）、`replay/service.py:10-46`（**`:11` get_session 无设备校验——P6-d 新增白名单的依据；`:16-17` not_analyzed 现状——P2 的前提**）
- bff search 现状：`services/bff/search/service.py:38-97`（topic 命中已读 day_summary.topics；**`:64-66` topic 命中传 session_ts——P1 事实更正依据**）、**`:100-113` `_hit` 对 ts_ms 直通透出**；`SearchHit`（schemas.py:127-133，含 session_id）
- bff graph 现状：`services/bff/graph/service.py:298-307`（`_to_node` 输出 session_id）、`:317-357`（`build_entity` appearances 含 session_id）、`:235-243`（`_session_topics`，topics 读取先例）；**`:164` mentions 恒 0 + `:48-58` `_Node.weight=0` 初始 + `:116` min_weight 过滤——P4 依据**；`:170-228` 其他实体 weight 均 `+= 1`（P4 修复语义对齐依据）；`_accumulate` event 节点以 first_ts 建点（P6-b 非空保证依据）
- bff store：`services/bff/common/store.py`（list_sessions/get_session 为内部实现）；`day_has_pending`（:246-270，pending+failed 日粒度）；**bff 全库无 logging/structlog 设施（P5 引入标准库 logging 依据）**
- Android 聚合：`app/**/timeline/PeriodAggregator.kt`（HotSlot.sessionId/date、summarize/mergeTopics/collectImages/deriveHotSlots、MAX_HOT_SLOTS/MAX_TOPICS/MERGE_GAP_MS）
- Android 导航：`app/**/ui/navigation/Routes.kt:21`（session+t 双参路由）、`NavGraph.kt:119-145`（-1 兜底分支）
- Android replay 定位：`app/**/replay/ReplayTimeline.kt:42`（locateCueTs 按 tsUtc 最近 cue——ts 锚天然兼容）；标题 fallback `ReplayScreen.kt:148`（仅消费 sessionDate）
- Android 数据层：`app/**/data/remote/mylife/dto/MylifeDtos.kt:82-89,100-106,123-131,156-160,175-182`（**SearchHitDto.sessionId :101 非空无默认、EntityAppearanceDto.sessionId :157 非空无默认、ReplaySessionInfoDto :176-177 非空字段、GraphNodeDto.sessionId :130 可空有默认——P3 在途影响矩阵依据**；GraphNodeDto.firstSeenMs :128 为 `Long?`——P6-b 依据）、`data/repository/MylifeRepository.kt:66-83`（60s 轮询）、`data/remote/mylife/MylifeApi.kt:42-73`（仅 overview/graph/entity/replay/search，无 timeline 端点；replay :68-69 路径参数）
- Android Graph：`app/**/graph/GraphViewModel.kt:67`（entitySessionId）、`GraphScreen.kt:154-164`（Play 分支 kind=="event" + entitySessionId?.let）、`EntityDetailSheet.kt:42,128-130`
- Android Search 列表：`app/**/timeline/components/SearchResultList.kt:93`（item key 引用 hit.sessionId，§0-R10 + P6-a）
- Web（决策背景，不再兼容）：`apps/web/mylife/src/views/TimelineView.tsx:39,211`、`GraphView.tsx:42-43,156-157`、`ReplayView.tsx:80-81`（session 路径导航）、`api/types.ts`（session 系类型）

---

## 12. 下一阶段（不在本期契约内，仅记录）

- **period_summary**（上游 §8.2）上线后：周/月/年卡 `summaryLines`（及可选 topics）改读 period_summary，`PeriodAggregator` 骨架与 UI 不变（§4.2 切换点）。
- **`?ts_ms=` 直进 replay 深链**：路由已支持；如通知/外部屏需要，加一个 navigate 调用即可。**注意：外部任意时刻非真实事件时刻，走 ②③ 近似语义（① 精确匹配不命中属预期，§4.3 入口不变量不适用于外部深链，P5）**。
- **apps/web/mylife 删除**：Web 不再使用，代码删除决策与执行另立任务。
- **`DayTopicDto.detail` 入 UI**（§9-O1 待产品确认）：chip 副行/展开，UI 小改，聚合骨架不变。
- **bff 解析日志 → metric**：replay_resolve 日志（§5.4）后续可接指标管道，①②③ 命中分布看板化（P5 延伸）。
