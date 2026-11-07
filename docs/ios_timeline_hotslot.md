# iOS 时光机活跃时段（Hot Slot）与 EventList 设计

**状态**：已实现（2026-10-22）
**UI/UX 基线**：`docs/android_timeline_hotslot.md` 与 Android 现行代码（`HotSlotScreen.kt`、`EventListViewerScreen.kt`、`HotSlotViewModel.kt`、`HotSlotRows.kt`、`HotSlotRowItems.kt`、`HotSlotFrameViewer.kt`）。
**共享基础**：`docs/ios_timeline_arch.md`。
**范围**：日卡活跃时段 chip、hotSlotView、eventListViewer、行模型、分钟分隔、气泡、Agent 结果覆盖层、帧全屏查看器、锚点定位/追击/高亮。
**不在范围**：hot slot 的服务端派生实现（`tools/lvdata-mcp`）、搜索入口（`ios_timeline_topics.md`）、图片 Gallery（`ios_timeline_image_gallery.md`）。

---

## 1. 功能概览

### 1.1 入口

- 每天最多 3 个活跃时段，只显示在**日卡**，周/月/年卡不显示。
- 每个 slot 只展示开始时间，chip 形如 `▸ 09:12`。
- 点击 chip 进入全屏 **hotSlotView**，展示该 slot 时间窗口内的五类可回顾事件：
  - `chat`、`agent_job`、`video`（关键帧）、`transcribe`、`translate`。
- 文本事件按角色显示为左右气泡；视频帧以 3 列网格插入时间流。
- 帧图可点开单图全屏查看器，支持保存到相册。
- Agent 结果行可点开全屏结果查看器；长正文在行内折叠。

### 1.2 路由

| 路由 | 参数 | 说明 |
|---|---|---|
| `hotSlot` | `startMs`、`endMs`、`pending` | `start/end` 为 epoch 毫秒，既取数窗口也决定标题；`pending` 为顶栏/空态初值 |
| `eventList` | `tsMs`、`pending` | 搜索/语音时间头进入；取数窗口固定 `ts ± 10min` |

- hotSlotView 没有日期参数；取数日由 slot 毫秒在当前设备时区现推。
- 两者共用同一套取数、行模型、行组件；EventList 额外做锚点定位与高亮。

### 1.3 组件关系

```
日卡 hot_slots
  → HotSlotChips（仅 DAY）
  → TimelineRoute.hotSlot(startMs,endMs,pending)
      → HotSlotViewModel
          ├─ MylifeStore.hotSlotEvents(start,end,cursor)
          ├─ HotSlotRows.build
          ├─ FrameViewerOverlay（覆盖层）
          └─ AgentJobResultOverlay（覆盖层）
  → TimelineRoute.eventList(tsMs,pending)
      → 同一 ViewModel/行模型 + 锚点追击/定位/高亮
```

---

## 2. 数据契约

### 2.1 Hot slot 口径（服务端）

派生只使用五类事件：

```
chat, agent_job, video, transcribe, translate
```

`audio` / `window` 不参与分段。单日算法：

1. 事件按 `ts_ms` 升序（同 ts 保持上游稳定顺序）。
2. 相邻事件间隔 **严格大于 60 分钟**时切新段。
3. 段数 > 3 时，反复合并「相邻间隔最小」的一对，直到 3 段；间隔并列取最左；合并后 `start_ms` = 前段起点，`end_ms` = 后段终点，`count` = 两段之和。
4. 输出按时间升序，最多 3 段。

常量（`tools/lvdata-mcp/src/lvdata_mcp/consts.py`）：

```python
SPLIT_GAP_MS = 60 * 60 * 1000
MAX_HOT_SLOTS = 3
HOT_SLOT_TYPES = {"chat", "agent_job", "video", "transcribe", "translate"}
```

- 由 lvdata-mcp `day_stats` stage 预计算，写入 `day_summary.day_stats`；BFF overview 只透传，不实时派生。
- 新鲜度跟随 day_stats round（默认 60 秒一轮），不是每次 overview 请求重算。

### 2.2 Overview 响应

```json
{
  "date": "2026-08-21",
  "hot_slots": [
    { "start_ms": 1758348000000, "end_ms": 1758350400000, "count": 26 }
  ]
}
```

- BFF 只透传 `day_stats.hot_slots`；畸形条目逐条丢弃，不整体 500。
- 升序、≤3；`count` 客户端不消费。
- overview 分页/时区语义见 `ios_timeline_arch.md` §4.5。

### 2.3 Timeline 响应（hotSlotView/EventList 取数）

```
GET /api/mylife/timeline
  ?date_from=<YYYY-MM-DD>&date_to=<YYYY-MM-DD>
  &types=chat,agent_job,video,transcribe,translate
  &limit=500&cursor=<date|offset>&timezone=<IANA>&token=<wire>
```

- `limit` 上限 500；客户端固定 500。
- `types` 未知 → 400。
- 事件排序：`ts_ms, PRIORITY, event_id`；响应按本地日分组（日期倒序），`days[].events` 内时间升序。
- 设备白名单过滤生效。
- 常用字段：`type`、`role`、`text`、`summary`、`agent_name`、`job_key`、`frame_id`、`window_id`、`caption`、`media_url`、`event_ref`、`ts_ms`。
- `media_url` 由 BFF 返回相对路径；iOS 出口绝对化并附 token。

### 2.4 iOS 取数映射

`MylifeStore.hotSlotEvents(startMs:endMs:cursor:)`：

1. 用**设备当前时区**由 `startMs`/`endMs` 推导 `dateFrom`/`dateTo`（含端点；跨午夜最多两天）。
2. 请求参数：`types = "chat,agent_job,video,transcribe,translate"`、`limit = 500`、`timezone = TimeZone.current.identifier`、`cursor`。
3. 展平 `days[].events` 后：
   - 保留 `tsMs in startMs...endMs`（含端点）；
   - `video`：`frameId` 与 `mediaUrl` 均非空，否则丢弃；
   - 非 video：正文按类型回落——
     - `agent_job`：`text → summary → agent_name` 首个非空白；
     - 其他：`text` 非空白；
   - `mediaUrl` 绝对化 + token；
   - 按 `tsMs` 稳定升序（同 ts 保持 BFF 顺序）。
4. 返回：

```swift
struct HotSlotPage {
    let events: [HotSlotEvent]
    let nextCursor: String?
    let reachedWindowEnd: Bool   // 任一本页原始事件 tsMs > endMs
}
```

- `reachedWindowEnd` 必须看**本页任一条越过窗口上界**，不能只看展平流的最后一条（响应顶层按日倒序，展平流非全局有序）。
- 翻页停止：`reachedWindowEnd == true` 或 `nextCursor == nil`。

---

## 3. 首页 chip

### 3.1 聚合

- 仅 DAY 粒度：直接映射 `OverviewDay.hotSlots` 的 `startMs/endMs`；`count` 丢弃。
- WEEK/MONTH/YEAR：恒为空。

### 3.2 视觉

- Flow 自动换行；水平/垂直间距 4。
- chip：Panel 80% 胶囊；Gold `labelMedium`；文本 `▸ HH:mm`；内容 padding 水平 8 / 垂直 2。
- 时间使用设备时区、24 小时制。
- 日卡同一行右侧可能有语音入口（`audioCount > 0`）；没有 hot slot 但有语音时该行仍出现（左侧留空）。
- 点击回调携带 `(startMs, endMs, pending = digest.hasPending)`；chip 先消费点击，不触发月/年卡下钻。

### 3.3 可访问性

- 建议新增 `mylife_hot_slot_desc`：en `Active at %@` / zh `活跃时段 %@`；VoiceOver 读「活跃时段 09:12」，`.isButton`。

---

## 4. hotSlotView / EventListViewer 取数与状态

### 4.1 状态

```swift
enum HotSlotState: Equatable {
    case loading
    case empty
    case error(message: String)
    case data(events: [HotSlotEvent], loadingMore: Bool, exhausted: Bool)
}

@MainActor final class HotSlotViewModel: ObservableObject {
    @Published private(set) var state: HotSlotState = .loading
    @Published private(set) var pending: Bool
    @Published private(set) var viewerFrame: HotSlotEvent?
    @Published private(set) var viewerAgentJobKey: String?
    @Published private(set) var saving = false
    @Published private(set) var imageSaveTip: String?
}
```

- 没有 `idle` 状态。
- `pending` 初值来自路由；随 Store overview 更新**单向收敛**为 false。

### 4.2 load / retry / reload

- `load(startMs, endMs, pending)`：
  - `startMs <= 0` 或 `endMs < startMs` 直接返回；
  - 同窗口已成功加载且处于 Data → 跳过（不重置 pending 收敛结果）；
  - 否则记录窗口、设置 pending、清空覆盖层、`reloadFirstPage()`。
- `retry()`：窗口有效时清空覆盖层并重拉第一页（错误/分析中空态的刷新入口）。
- `reloadFirstPage()`：
  - 取消在飞首屏/追击/加载更多；
  - 清空 `seenRefs`、`nextCursor`、`exhausted`、追击预算、`chasing`；
  - 已有 Data 时不回落 Loading（轮询重取不闪屏、不丢滚动位置）；
  - 首屏失败：有 Data 保留，无 Data 进入 Error。
- `fetchPages(firstPage: Bool)`：
  - 从当前 cursor 连续翻页；
  - **首页可能整页都落在窗口之前**（重度日事件 > 500 条且窗口靠后），此时继续翻页，不落 Empty；
  - 直到已见窗口内内容或翻页终止；
  - `firstPage == true` 时首页以**替换**语义应用，避免旧列表重复追加；追击期间保持 Loading；
  - 结束（含取消/失败）后 `loadingMore` 必须回落 false。
- `loadMore()`：
  - 仅 Data 可触发；追击中、正在加载、已终止、无 cursor → no-op；
  - 失败静默保留已有页；用户需滚离底部再回来或上一页成功追加后才重试。
- 跨页去重：`event_ref` 的 `Set`；无 `event_ref` 的条目保留（BFF 恒透出，防御）。
- `event_ref` 同时是行 key 的稳定来源。

### 4.3 pending 与 overview

- ViewModel 订阅 `MylifeStore.$overviewWindow`：
  - 已加载过列表（`loaded == true`）→ 实例变化即 `reloadFirstPage()`；
  - 收到非空窗口且当前 pending 为 true 且 `store.hasPendingAnalysis() == false` → pending 单向收敛 false（不因后续翻回 true）。
- 轮询重取**不清空覆盖层**（不把用户正在看的帧/Agent 结果关掉）；只有公开的 `load`/`retry` 清空。
- `pending` 为 true：
  - 顶栏显示「分析中…」；
  - 空态显示「内容分析中…」+ 刷新按钮；
  - 非 pending 空态显示「该时段没有可展示的内容」。

### 4.4 触底加载

- 可见最后一项进入末尾 4 行内触发；
- 每个「新触发」只发一次：滚离阈值区再回来，或上一页成功追加（行数变化）后才重新武装；
- 失败后 `loadingMore` 回落不得立即重发，避免断网重试风暴。

---

## 5. 行模型

### 5.1 定义

```swift
enum BubbleSide { case user, ai, agent }

enum HotSlotRow: Identifiable {
    case text(key: String, timeLabel: String?, tsMs: Int64, side: BubbleSide, body: String)
    case frames(key: String, timeLabel: String?, frames: [HotSlotEvent])

    var id: String { key }
    var timeLabel: String? { ... }
}
```

- 文本行 key：`t-<eventRef 或 tsMs>`
- 帧行 key：`f-<首帧 eventRef 或 tsMs>`
- 输入事件已按 `tsMs` 升序。

### 5.2 事件 → 行（`buildHotSlotRows`）

- 连续 `video` 事件（中间没有文本事件）成组；组内按 ts 升序 `chunked(3)`，每行最多 3 张。
- 非帧事件即使因 role 白名单被丢弃，也打断连续帧分组。
- 文本行映射：

| 类型 | role | side | 正文 |
|---|---|---|---|
| `chat` | `user` | user | `text` |
| `chat` | `assistant` | ai | `text` |
| `transcribe` | `user` | user | `text` + ` ` + `(转录)` |
| `transcribe` | `assistant` | ai | `text` + ` ` + `(转录)` |
| `translate` | `user` | user | `text` + ` ` + `(原文)` |
| `translate` | `assistant` | ai | `text` + ` ` + `(译文)` |
| `agent_job` | 不检查 role | agent | `text`（Store 已完成回落） |
| 其他 role | — | — | 整行丢弃 |

- 后缀来自已有 `conv_msg_suffix_transcript/original/translation`，随 App 语言本地化。
- 正文为空白（trim 后为空）的非 video 事件已在 Store 丢弃；`video` 无帧/无 URL 也已丢弃。

### 5.3 分钟分隔

- 分钟桶：`tsMs / 60_000`（IANA 偏移均为整分钟）。
- 同一分钟只有**第一条实际产出的行**带 `timeLabel`；同分钟后续行 `timeLabel = nil`。
- 被 role 白名单丢弃的行不产出行，也不消耗标签。
- 帧组按 chunk 处理：每个 chunk 的首帧参与分钟桶判断；同一 chunk 内跨分钟不额外出现标签。
- 标签格式 `HH:mm`，设备时区，居中 `labelMedium` Dust；上间距 8、下间距 4。

---

## 6. 行渲染

### 6.1 帧行

- 每行最多 3 张，固定 1/3 宽，1:1，`scaledToFill`，圆角 8，间距 4。
- 每行垂直 padding 4；末行不足 3 张以等宽 Spacer 补齐，保持左对齐。
- 占位/失败 Panel。
- 单击 → 单图全屏查看器。

### 6.2 文本气泡

按 Android 现行气泡规格实现时间线气泡（与会话气泡同族；现有 iOS `MessageBubbleView` 的垂直内边距为 10，时间线用 8，不改动会话页）：

| 项 | 用户侧 | AI / Agent 侧 |
|---|---|---|
| 对齐 | 右 | 左 |
| 底色 | `AppColor.brandIndigo` 实底 | `AppColor.surface` |
| 描边 | 无 | 1pt `AppColor.surfaceVariant` |
| 文字色 | `AppColor.textPrimary` | `AppColor.textPrimary` |
| 圆角 | (16,16,16,8) | (16,16,8,16) |
| 最大宽度 | 内容列宽 × 75%（含内边距；`AppLayout.messageBubbleMaxWidth`）；AI 多行撑满、单行自适应、用户贴右 | 同左 |
| 内边距 | 水平 16 / 垂直 8 | 同左 |
| 行间距 | 2 | 2 |
| 正文 | 16/24 | 16/24 |

- Agent 行额外有头行：
  - `AgentRobot` template asset（Android SmartToy 机器人头）16pt，`textSecondary`；
  - 「Agent任务」（`mylife_agent_task`）12/16 `textSecondary`；
  - 若该行可点开全屏结果，右侧 14pt 展开图标（`arrow.up.left.and.arrow.down.right`）。
- Agent 正文用 `MarkdownMessageView` 渲染（MarkdownUI），链接走 `AppContainer.openWebLink`；`cacheKey` 使用行 key（`<rowKey>:full|prefix`），避免不同时段同文本串缓存。
- Agent 正文含 GFM 表格时，正文块占满气泡可用宽度（气泡整体仍受 75% 上限约束，含内边距），对齐 Android `AgentJobResultText` 的表格整宽处理，避免 MarkdownUI 表格被压窄。
- Agent 长正文折叠：
  - 复用现有 `AgentJobCollapse`（iOS 既有字符/段落边界算法）；
  - 折叠后显示「查看全文 · 还有 N 字」入口（`conv_job_remaining_chars`）+ 展开图标，BrandIndigo；
  - 折叠入口与整行点击都打开全屏结果。
  - 与 Android 的 800 字/24 行阈值差异记录在 `ios_timeline_arch.md` §11；视觉与交互结果一致。
- 链接与行点击仲裁：复用 `MessageBubbleView` 的「链接先消费 + 120ms 延迟打开行结果」策略，避免点链接同时触发全屏。
- 文本行正文按下不记录字符偏移；iOS 全屏 Agent 结果打开在顶部（见 §7.2）。

### 6.3 行容器

- 行水平 padding 由列表统一给 16；行本身垂直 2（气泡）/4（帧）。
- 分钟分隔在气泡/帧行之外，居中，占满宽度。
- 行使用稳定 key；轮询重取替换列表时保持可滚动位置。

---

## 7. 覆盖层

### 7.1 帧全屏查看器

- 覆盖层非路由；黑色全屏，绘制在列表之上。
- 图片 `scaledToFit`；未加载/失败仍可单击退出。
- 手势：
  - 单击退出（fit 与放大态）；
  - 双指捏合 1x–5x，以手指质心为锚；
  - 放大态单指拖动平移，越界夹取；
  - fit 态单指拖动无效果（本屏无分页）。
- 底部单一 bar：
  - 黑 60%，圆角 12；无 caption 时退化为圆形保存按钮；
  - caption 单行省略；被截断时尾部显示金色 `⌄`，点击展开/收起（`⌃`）；展开为本次全屏会话级；
  - bar 区域点击不退出查看器；
  - 右侧保存按钮（≥44pt），保存中转圈 18pt，不置灰（避免 tap 穿透误退出）。
- 保存提示在 bar 上方，黑 75% 胶囊，2 秒 TTL。
- 返回：第一次关闭查看器，第二次退出路由。
- 技术复用：改编 `ImageViewerOverlay` 的手势与 `ImageViewerMath`；远程图片用 `NetworkImage`，页面持事件对象而不是列表索引，轮询/续页不会失效。

### 7.2 Agent 结果全屏

- 覆盖层非路由；复用现有 `AgentJobResultOverlay`（入参已从 `ChatMessage` 解耦为 `text + timestamp`）；HotSlot 只通过 `.agentJobResultWindow(owner: .timeline, payload:)` 发布结果，由 `AppRootView` 时间线 fullScreenCover 根部的窗口级宿主渲染，宽度不受 600pt 内容列限制。
- 结构：
  - 竖屏顶栏：`AgentRobot` 图标 + 到达时间 `HH:mm` + 关闭；横屏去顶栏、关闭钮浮动右上角（共享 `AgentJobResultOverlay` 的 M3 布局，见 `docs/ios_agent_job_view.md` §6.6）；
  - 正文 Markdown 整宽、可滚动；
  - 链接走 `onOpenLink`；
  - 背景使用 Android 同款 `AppColor.surface`；通过参数化背景颜色实现，会话/History/Timeline 共用同一阅读器。
- 身份 = 行 key（`HotSlotRow.text.key`），不是行快照；从当前 rows 按 key 解析正文。
- 分页窗口被轮询重取截回首页时，未命中不立即关窗，使用最近一次载荷兜底（Android `keepLastPayloadOnMiss` 同语义）。
- 与帧查看器互斥：打开一个会关闭另一个。
- 打开任一覆盖层时列表禁滚，避免手势漏到列表。
- iOS 差异：不按点击字符偏移落位，打开在顶部（`ios_timeline_arch.md` §11）。

### 7.3 保存帧

- `saveViewerFrame()` 以当前 `viewerFrame.mediaUrl` 为准；空 URL no-op；`saving` 时忽略重复点击。
- 下载失败 / 保存失败 → 「保存失败」；成功 → 「已保存到本地相册」；提示 2 秒。
- 关闭查看器取消在飞保存并清提示；`finally` 复位 `saving`。

---

## 8. eventListViewer（锚点定位）

### 8.1 取数

- 窗口 `ts ± 10 分钟`（含端点）。
- 复用 `HotSlotViewModel`（独立实例）与同一行模型/组件。
- 标题：`ts` 格式化（`8月21日 09:14` / `Aug 21 09:14`），只显示开始时间。
- pending 初值来自路由，行为与 hotSlotView 相同。

### 8.2 锚点追击

`ensureLoadedUntil(anchorTs)`：

- 仅 Data 态；`anchorTs <= 0` 返回；
- 已有事件满足 `tsMs >= anchorTs`、或已 exhausted、或 `nextCursor == nil`、或预算用尽 → 不再取；
- 最多追击 5 页；**按尝试计数**：每次请求前 +1，失败也消耗预算；
- 预算只计「窗口内已有内容之后」的追击页；`fetchPages` 首段跳过窗口前整页不计；
- `reloadFirstPage()` 清零预算与 `chasing`；
- 追击期间阻塞 `loadMore()`；
- 失败静默保留已加载页，由界面退化兜底。
- 触发方式：View 监听 `state + anchorTs`，状态变化即重入（轮询重取后自动恢复追击）；内部 `chasing/loadingMore/exhausted` 幂等守卫。

### 8.3 锚点解析

`anchorRowIndex(rows, anchorTs)`：

1. 精确命中优先：
   - 文本行 `tsMs == anchorTs`；
   - 帧行任意帧 `tsMs == anchorTs`（整行高亮）。
2. 否则退化：首个「行尾 ts ≥ anchorTs」的行：
   - 文本行为 `tsMs`；
   - 帧行为末帧 `tsMs`。
3. 仍无 → `-1`，不滚动。

### 8.4 定位与高亮

- 只定位一次：解析出锚点后瞬时滚动到该行（重度日锚点可能深至百余行，不使用动画）；
- 置 `located = true` 后不再重复滚动，即使轮询重取/续页改变 rows；
- 高亮：行容器 Gold 2pt 边框 + 圆角 8；2 秒后自动清除；
- 高亮计时独立于 rows 变化，避免续页/重取重启计时导致高亮残留；
- 追击未覆盖或窗口内无更晚行 → 不滚动。

### 8.5 与 hotSlotView 的差异

| 项 | hotSlotView | eventListViewer |
|---|---|---|
| 窗口 | slot 的 `[start,end]` | `ts ± 10min` |
| 入场位置 | 列表顶部 | 锚点行（一次） |
| 锚点追击 | 无 | 最多 5 页 |
| 高亮 | 无 | 2 秒 Gold 边框 |
| 其余（行模型/覆盖层/分页/pending） | 相同 | 相同 |

---

## 9. 本地化与视觉

- 文案 key：
  - `mylife_loading`、`mylife_analyzing`、`mylife_analyzing_content`、`mylife_refresh`、`mylife_hotslot_empty`、`mylife_agent_task`、`mylife_hot_slot_desc`、`common_retry`、`common_save_image`、`common_saved_to_gallery`、`common_save_failed`、`common_view_full_result`、`conv_job_remaining_chars`。
- 后缀：`conv_msg_suffix_transcript/original/translation`。
- 时间/标题：设备时区，24 小时制；日期按 en/zh 输出。
- 视觉 token：
  - 屏底色 Abyss；顶栏标题 Gold、返回 Dust；
  - 分钟分隔 Dust；用户气泡 BrandIndigo；AI/Agent 气泡 surface + surfaceVariant；
  - Agent 头行 textSecondary；帧占位 Panel；锚点高亮 Gold；保存提示黑 75% + Star。
- 子页不复制星点背景，仅 Abyss。

---

## 10. 边界与已知限制

| # | 场景 | 行为 |
|---|---|---|
| 1 | 数据新鲜度 | hot slot 由 day_stats 预计算；新事件到 chip 更新有 stage 延迟 |
| 2 | overview 时区 | 日卡 date 可能来自「最新时区」；hotSlotView 用 slot 毫秒现推设备时区规避 |
| 3 | pending 收敛窗口 | 只看已加载窗口最新 400 天 |
| 4 | 游标位移 | `/timeline` 为 offset 游标，录制中插入会重复/漏；重复靠 event_ref 去重，漏靠 pending 轮询重取首屏兜底 |
| 5 | 首页整页在窗口前 | 继续翻页，不落 Empty |
| 6 | 追击上限 | 最多 5 页，超限静默退化 |
| 7 | 锚点事件不在行模型 | 无精确锚点 → 首个行尾 ≥ anchor 的行；仍无 → 不滚动 |
| 8 | 窗口无可展示事件 | Empty；pending 时显示分析中空态 |
| 9 | 跨午夜 | 取数日由 ms 现推，最多覆盖两天 |
| 10 | 触底失败 | 不自动重试；滚离底部再回来或列表增长后重新武装 |
| 11 | 轮询重取 | 首页替换、预算清零、响应式重入、覆盖层保持 |
| 12 | load/retry | 清空覆盖层 |
| 13 | 链接 | 只允许 http(s) 且带 host；webroot 走 cover 本地 WKWebView，其余由 `SafariLinkPresenter` 直接 present Safari（不 dismiss 时间线 cover） |
| 14 | Agent 结果未命中 | 分页窗口重取导致未命中时，保留最近载荷，不关窗 |
| 15 | 行 key 冲突 | 依赖 event_ref；同毫秒多事件且无 event_ref 时可能冲突（BFF 恒透出） |

---

## 11. 测试计划

### 11.1 单元

- 行模型：分钟桶只首行带标签；role 白名单；后缀；连续帧 chunk；跨分钟 chunk；丢弃行不消耗标签。
- 锚点解析：文本精确、帧内精确、退化行尾、无锚点。
- Store 取数映射：窗口含端点、video 必需字段、agent 文本回落、`reachedWindowEnd` 看任一原始事件、稳定排序。
- ViewModel：首屏整页在窗口前继续翻页；追击 5 页/失败耗预算/与 loadMore 互斥/轮询重取清预算；pending 单向收敛；覆盖层互斥与保持。
- 保存：成功/失败/重入/关闭取消。

### 11.2 UI

- 日卡 chip 数量/开始时间/点击携带 pending。
- hotSlotView：气泡方向、分钟分隔、帧行、链接、Agent 折叠/全屏、帧查看器开合/保存提示。
- EventList：锚点在后续页追击并高亮 2 秒；帧查看器；空窗口；返回。
- 轮询重取时覆盖层不被关闭。

### 11.3 真机

- 自签 TLS 下帧图加载与保存。
- 重度日（>500 事件）的首屏/追击/触底。
- 长 Agent Markdown（表格/代码块/链接）。

---

## 12. 代码锚点

### Android

| 能力 | 位置 |
|---|---|
| hot slot 派生 | `tools/lvdata-mcp/src/lvdata_mcp/summary/day_stats.py`、`consts.py` |
| overview 透传 | `services/bff/timeline/service.py` |
| 日卡 chips | `.../timeline/components/HotSlotChips.kt`、`PeriodCard.kt` |
| hotSlotView | `.../hotslot/HotSlotScreen.kt`、`HotSlotViewModel.kt`、`HotSlotUiState.kt` |
| EventList | `.../hotslot/EventListViewerScreen.kt` |
| 行模型 | `.../hotslot/HotSlotRows.kt` |
| 行组件 | `.../hotslot/components/HotSlotRowItems.kt` |
| 帧查看器 | `.../hotslot/HotSlotFrameViewer.kt` |
| Agent 正文/覆盖层 | `.../ui/components/AgentJobResultText.kt`、`.../conversation/AgentJobResultHost.kt`、`AgentJobResultOverlay.kt` |
| 链接 | `.../ui/components/MarkdownContent.kt` |

### iOS（复用/拟）

- `Core/Mylife/MylifeStore.swift`（hotSlotEvents/frames）
- `Features/Timeline/HotSlot/HotSlotViewModel.swift`、`HotSlotRows.swift`、`HotSlotView.swift`、`EventListView.swift`、`FrameViewerOverlay.swift`
- `Features/Conversation/AgentJobResultOverlay.swift`（解耦为 text/timestamp）
- `Features/Conversation/AgentJobCollapse.swift`、`MarkdownMessageView.swift`、`ImageViewerOverlay.swift`（`ImageViewerMath`）
- `Resources/Localizable.xcstrings`
