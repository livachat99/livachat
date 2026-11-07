# Agent Job 结果全屏查看（Conversation / History / 时间线）

> **状态**：已实施（Android）
> **文档基线**：2026-10-24，对应当前 `apps/android/LivaChatApp/` 代码
> **范围**：`apps/android/LivaChatApp/`——会话气泡入口、History 详情页、时间线两屏（eventListViewer + hotslotView）、全屏覆盖层、共享折叠文本块、`MainActivity` 清单配置
> **前置文档**：
> [`android_timeline_hotslot.md`](android_timeline_hotslot.md)（agent_job Markdown/表格先例）、
> [`ws-protocol-design.md`](ws-protocol-design.md)（`msg_agent_job_*` 契约）
> **说明**：iOS 端另有同族实现，不在本文范围。

---

## 1. 背景与需求

### 1.1 问题背景

agent job 的结果以一条 `ai_text` 落到客户端：`source="agent"`，
`message_id = msg_agent_job_{job_key}`（确定性幂等键，
`services/agent_relay/delivery.py:95`；协议见 `docs/ws-protocol-design.md:486,702`、
`packages/api-schema/s2c/job-status.json:31`）。适配器以 `id = "ai-$messageId"` 写入本地
（`ConversationListenerAdapter.kt`）。

结果文本有三个固有特征；若按普通气泡平铺，会带来阅读问题：

- **长**：单条上限 16KB（server `max_reply_bytes` / `max_stored_result_bytes = 16384`，
  `services/agent_relay/config.py`），一条结果可能占据多屏；
- **常含 GFM 表格**：气泡宽度上限 75% 屏，列宽被挤压后阅读困难；
- **缺少可发现性**：头行只有 `Agent: HH:mm`，用户不知道内容可以放大看。

本功能用「长结果折叠 + 头行展开图标 + 全屏查看 + 点击处定位」解决这三点。
同样的内容会出现在四个界面：

| # | 界面 | 数据形态 |
|---|---|---|
| ① | Conversation（仅 voice 分支的气泡列表） | `ChatMessage`（Room，实时帧写入） |
| ② | History 详情页 | `ChatMessage`（Room 历史） |
| ③ | eventListViewer（时间线搜索命中窗口） | `HotSlotRow.Text`（agent 行） |
| ④ | hotslotView（主时间线屏） | 同 ③，共用行组件与 ViewModel |

### 1.2 功能需求

| # | 需求 | 行为 |
|---|---|---|
| R1 | **长结果折叠** | 列表内超过阈值的结果按行边界折叠，显示「查看全文 · 还有 N 字 ↗」 |
| R2 | **点击进全屏** | 单击 job 结果气泡/行的显示区域 → 全屏展示完整结果 |
| R3 | **点击处定位** | 全屏打开后停在被点击的位置（屏幕 1/3 处），而非回到顶部 |
| R4 | **横竖屏自动切换** | 全屏期间支持旋转；横屏为宽表格提供更多宽度 |
| R5 | **可关闭** | 全屏内有 close button，系统返回键同样可退出 |
| R6 | **四屏一致** | ①②③④ 使用同一套折叠、点击、覆盖层与落位语义 |
| R7 | **导航返回保持位置** | 全屏内点链接去 WebView 再返回，滚动位置不变 |

### 1.3 非目标

- 不改 server / 协议 / SDK：判定与展示完全基于既有字段；
- 不做 job 详情内的操作（重跑/取消/复制/分享/导出）——纯只读查看；
- 不做气泡内联展开：折叠气泡的唯一出口是全屏；
- 非 job 消息不折叠（它们没有全屏出口，折叠会导致看不全）；
- 不隐藏系统状态栏（不做沉浸式）；
- 不改图片查看器、job 状态 badge/面板；
- 时间线的覆盖层沿用帧查看器先例（`statusBarsPadding` 之内），不是系统级真全屏。

---

## 2. 判定与触摸分发

### 2.1 如何判定「这就是 agent job 结果」

| 事实 | 出处 |
|---|---|
| 结果 ai_text 的 `message_id` 恒为 `msg_agent_job_{job_key}` | `services/agent_relay/delivery.py:95`；`packages/api-schema/s2c/job-status.json:31` |
| 协议文档明确该键可用于对齐状态项与结果气泡 | `docs/ws-protocol-design.md:486,702` |
| SDK 公开模型同样记载该契约 | `LivaChatSDK/.../model/AgentJob.kt` |
| app 侧消息 id = `"ai-" + messageId` | `ConversationListenerAdapter.kt`（`val id = "ai-$messageId"`） |

**判定式**：

```kotlin
// domain/model/ChatMessage.kt
internal const val AGENT_JOB_RESULT_ID_PREFIX = "ai-msg_agent_job_"

fun ChatMessage.isAgentJobResult(): Boolean = id.startsWith(AGENT_JOB_RESULT_ID_PREFIX)
```

为什么不能只判 `source == "agent"`：

- **job 启动提示（summary）**（"正在查询东京机票"）的 `message_id` 是
  `msg_agent_{uuid hex16}`（`agent_relay/session_manager.py`），与结果**共享 `msg_agent_` 前缀**，
  `source`、`proactive`、`index`、`is_final`、`reply_to` 对两者都相同——wire 上唯一可分辨的字段就是
  `message_id`；`job_key` 是服务端内部字段，不下发；
- **LLM 回复 / 翻译文本**的 `source` 为 `null`/`translate`。

因此判定必须包含 `job_` 段，且不得使用 `source` 单独判定。前缀中的 `"ai-"` 是适配器拼法，
改 id 生成规则时须同步该常量。

### 2.2 气泡可点与 TextView 触摸消费（D13）

`MarkdownContent` 内部是 `AndroidView(TextView)` + movement method。**若使用平台默认的
`LinkMovementMethod`，TextView 会吞掉所有触摸**，祖先 Compose `Modifier.clickable` 收不到手势。
消费链有三层：

1. `TextView.setMovementMethod()` → `fixFocusableAndClickableSettings()` 把 TextView 置
   `clickable=true / longClickable=true`（AOSP `TextView.java`）；
2. clickable View 的 `View.onTouchEvent` 对所有 action 末尾统一 `return true` → DOWN 被消费；
3. 即使 ①② 被拆掉，movement method 本体仍会消费 DOWN：
   `ScrollingMovementMethod` 委托 `Touch.onTouchEvent`，其 `ACTION_DOWN` 分支无条件 `return true`
   （登记 `DragState` 供拖动自动滚动，AOSP `Touch.java`）。

**修法（两处缺一不可）**：

- (a) `MarkdownContent.kt` 的私有 `ClickThroughLinkMovementMethod`：
  - **DOWN 未命中 `ClickableSpan`** 时返回 `false`（不消费 → 冒泡给 Compose）；落点在文本内时
    顺带上报字符偏移；
  - **MOVE 一律返回 `false`**，不交回基类——锁死「正文 TextView 从不自滚」不变量，避免折叠气泡
    内部错位；
  - 其余事件（UP/CANCEL、命中的 DOWN/UP）交回 `LinkMovementMethod` 基类：链接选择高亮、
    `onClick`、`DragState` 清理照旧；
- (b) factory 在设置 movement method **之后**追加：
  `isClickable = false; isLongClickable = false`
  （`setMovementMethod` 会先把它置 true，顺序不能反；Markwon 的 `afterSetText` 不会重置，
  仅在 `getMovementMethod() == null` 时 set）。

完成后的行为：

- 点正文非链接区 → 冒泡到气泡/行的 `clickable` → 进全屏；
- 点链接 → 由 movement method 消费 → `onLinkClick`（WebView），**不触发整卡回调**；
- `isClickable=false` 不触发 `performClick`，天然杜绝链接与整卡双触发；
- **偏移必须在 DOWN 记录，不能在 UP**：DOWN 不消费后，View 层不再把该手势的 UP/MOVE 下发到该
  TextView（ViewGroup 只向消费了 DOWN 的子 View 派发后续事件），UP 钩子不可达。

`MarkdownContent` 对外提供两个可选参数：

```kotlin
fun MarkdownContent(
    markdown: String,
    onLinkClick: (String) -> Unit,
    modifier: Modifier = Modifier,
    /** 非链接区按下（DOWN）时的字符偏移——全屏落位用。 */
    onContentTap: ((charOffset: Int) -> Unit)? = null,
    /** 内部 TextView 创建/每次更新时回调——供调用方读 Layout 做偏移→y 映射或测量。 */
    onTextViewReady: ((TextView) -> Unit)? = null,
)
```

两个回调都经 `rememberUpdatedState` 转发：回调身份变化不触发 Markwon 重建，点击时总能拿到最新
lambda。

影响面：`MarkdownContent` 的调用方只有 `AgentJobResultText` 与 `AgentJobResultOverlay`。
非 job 气泡与未传 `onAgentJobClick` 的时间线 agent 行外层均无 clickable，非链接区由
「被 TextView 吞掉」变为「冒泡后无人消费」，行为零变化。

### 2.3 覆盖层模式与视觉资产

沿用 `ImageViewerOverlay` 已确立的「进程内覆盖层」模式：

| 要素 | 做法 |
|---|---|
| 渲染位置 | 页面根 `Box` 的覆盖层子项，盖住顶栏/工具栏/列表（会话页在图片查看器之后；History 在其之前） |
| 状态 | ViewModel 存**身份**（消息 id / 行 key），不复制正文 |
| 打开/关闭 | 事件或函数式状态；关闭按钮 + `BackHandler` |
| 未命中守卫 | 身份解析不到 → 初始打开时自动关窗；时间线分页窗口已有载荷时以最近载荷兜底（§12.9.1） |
| 底层禁滚 | 覆盖层打开时 `userScrollEnabled = false` |
| 时间线先例 | `HotSlotFrameViewer` 渲染在 `statusBarsPadding` 之内（不盖状态栏） |

视觉资产全部取自现有主题：

- 顶栏：`TopAppBar`，容器色与覆盖层底色一致；
- 关闭按钮：`IconButton { Icon(Icons.Default.Close, contentDescription = common_close) }`；
- 横屏浮动关闭钮：`Color.Black.copy(alpha = 0.35f)` 圆底 + `CircleShape`（对齐图片查看器）；
- 面板：`Surface` / `RoundedCornerShape(16.dp)` / `BorderStroke(1.dp, SurfaceVariant)`（用于既有卡片）；
- 主题 token：`Surface #1E293B`、`Background #0F172A`、`SurfaceVariant #334155`、
  `TextPrimary #F8FAFC`、`TextSecondary #94A3B8`、`BrandIndigo #6366F1`；
  `Spacing` xs/sm/md/lg = 4/8/16/24dp。

### 2.4 横竖屏约束

| 约束/事实 | 设计结果 |
|---|---|
| `MainActivity` 清单锁定 `android:screenOrientation="portrait"` | 其余页面保持竖屏；覆盖层在组合期间运行时解锁 |
| 除覆盖层外，生产代码无运行时 orientation 调用 | 解锁/恢复只发生在覆盖层生命周期内 |
| 旋转若重建 Activity，会重放 `OnScreenStart` | `autoCameraOnJoin=true` 的用户机可能被重新打开相机，并切走 voice 分支 |
| 会话/媒体为进程级单例 | 旋转不丢业务状态，但重建仍会带来相机解绑/重绑与组合抖动 |
| Compose 支持 `AndroidComposeView.onConfigurationChanged` | 声明 `configChanges` 后 `LocalConfiguration` 更新并触发重组 |

**最终实现同时做两件事**：

1. **运行时解锁**：覆盖层组合期间 `activity.requestedOrientation = FULL_SENSOR`；
   离场恢复进入前的原值（正常为清单锁 `PORTRAIT`）。运行时值覆盖清单锁，其余页面零变化；
2. **清单声明 `configChanges`**：旋转不重建 Activity，规避 `OnScreenStart` 重放、相机解绑/重绑
   与组合重建抖动（当前清单见 §4.4）。

### 2.5 结果正文的语义边界

- **单条上限 16KB**：全屏一次渲染，不分页/不虚拟化；
- **结果帧不携带图片**：relay 的 ai_text 帧是白名单构建（`services/agent_relay/publisher.py`，
  仅 text/message_id/index/is_final + source/agent_name/reply_to/proactive），无 `image` 字段；
- **正文不含 `[Agent job result]` 前缀**：该前缀只加在服务端 WorkingMemory / chat store 行上
  （`services/session/agent_job_ingest.py`，给 LLM 历史用）；app 的 Room 只由实时帧写入，
  全屏展示的就是下发原文；
- **头行时间是「消息到达时间」**（适配器 `timestamp = Instant.now()`），与气泡头行同源；
  **不是 job 的开始时间**（后者只出现在 job 面板的 `startedAtMs`）。

### 2.6 视觉 token 与排版

- 正文字体：`bodyLarge`（16sp），`MarkdownContent` 行高与 Compose 对齐；
- 头行：`labelMedium` / `bodySmall`，`TextSecondary`；
- 展开图标：14dp `OpenInFull`，`TextSecondary`（气泡/行）或 `BrandIndigo`（折叠入口行）；
- 全屏底色：`Surface`（= 气泡底色），不搬气泡的 1dp 描边（铺满全屏时无视觉意义）；
- 横屏正文顶部留 `Spacing.xs`，关闭钮浮在右上角。

### 2.7 触摸分发的源码依据

AOSP 核对（本机 SDK `sources/android-34`）：

- `TextView.setMovementMethod` → `fixFocusableAndClickableSettings()` 置
  `clickable/longClickable = true`；
- `View.onTouchEvent` 对 clickable View 在 switch 之后统一 `return true`（所有 action）；
- `ScrollingMovementMethod.onTouchEvent` 委托 `Touch.onTouchEvent`，后者 `ACTION_DOWN`
  分支无条件 `return true`。

真机探针结论（instrumented，装置为「`Modifier.clickable` 祖先 Box 包住 Markdown」）：

| 装置 | 结果 | 结论 |
|---|---|---|
| 默认 `LinkMovementMethod`（D13 前） | 祖先 clickable **0 次** | 非链接触摸确被吞 |
| 仅加 `isClickable/isLongClickable = false` | 祖先 clickable **0 次** | 只拆 ①② 不够 |
| 只换 movement method、保留 `clickable=true` | 祖先 clickable **0 次** | 只做 (a) 不够 |
| 新 movement method **+** 两 flag=false | 祖先 clickable **1 次** | 两处缺一不可 |
| 同装置点链接 | `onLinkClick` 1 次、祖先 clickable **0 次** | 链接照旧命中，无双触发 |
| 同装置拖动起手于正文 | `LazyColumn` 正常滚动 | 滚动路径不受影响 |

---

## 3. 总体设计

### 3.1 核心决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 进程内覆盖层（非 Dialog / 非导航路由 / 非新 Activity） | 与图片查看器同骨架；不打断会话组合与相机绑定；VM 状态跨配置变更存活 |
| D2 | 判定用 id 前缀 `ai-msg_agent_job_`，不用 `source=="agent"` | 协议契约；`source` 会误伤 job 启动提示与 nudge 消息 |
| D3 | 覆盖层组合期间运行时解锁 `FULL_SENSOR`，离场恢复原值 | 运行时值覆盖清单锁；清单保持 portrait，其余页面零变化 |
| D4 | `MainActivity` 声明 `configChanges` | 旋转不重建，规避 `OnScreenStart` 重放与组合抖动 |
| D5 | 全屏内容 = 头行（Agent 图标 + `HH:mm`）+ 整宽可滚 Markdown | 复用现有视觉词汇；表格获得全宽 |
| D6 | 关闭方式：close button + 系统返回键 | 不做点击外部关闭/下拉手势 |
| D7 | 气泡/行提供可选 `onJobResultClick`，仅调用方对命中消息传入 | 与既有 `onImageClick` 同构；非 job 气泡零变化；涟漪受圆角约束 |
| D8 | Conversation 仅 voice 模式有气泡入口 | 气泡只在 voice 分支渲染；相机分支无气泡列表 |
| D9 | 会话切换/断连清态；身份未命中自动关窗（时间线分页窗口用最近载荷兜底，§12.9.1） | 避免状态悬空；分页窗口避免误关 |
| D10 | 覆盖层打开时底层列表 `userScrollEnabled = false` | 覆盖层根容器不消费手势：头行/留白处拖拽会漏到下层列表 |
| D11 | 不隐藏系统状态栏 | 与图片查看器一致 |
| D12 | 不加复制/分享动作 | 纯只读；头行 `actions` 是后续扩展位 |
| D13 | `MarkdownContent` 不消费非链接触摸（movement method + 两个 flag） | §2.2；两处缺一不可，否则 R2 失效 |
| D14 | 全屏底色 = 气泡底色 `Surface`，不搬描边 | 「气泡被放大铺满」 |
| D15 | 点击定位走「字符偏移」精确映射 | 气泡 75% 宽与全屏整宽换行不同，比例方案会跑偏 |
| D16 | 横屏不渲染 TopAppBar，✕ 浮动右上角；竖屏保留 TopAppBar | 横屏内容最大化；竖屏保留时间可读 |
| D17 | 落位「已执行」标记用 `rememberSaveable` | 导航往返/旋转后不重放落位，滚动位置保持 |

### 3.2 架构

```text
ConversationScreen / HistoryDetailScreen / EventListViewerScreen / HotSlotScreen
  └─ MessageBubble / HotSlotRowItem
       │  仅命中 agent job 结果时传入 onJobResultClick
       │  点击 → VM 记录「身份 + 点击处字符偏移」
       ▼
  UiState: <viewId> + <initialCharOffset>
       ▼
  AgentJobResultHost            ← 身份解析 + 未命中守卫（四屏共用）
       │  resolve(身份) → AgentJobPayload(text, timestamp)
       ▼
  AgentJobResultOverlay         ← 全屏视图
       ├─ 竖屏 TopAppBar（Agent 图标 + HH:mm + ✕）
       ├─ 横屏：无标题，✕ 浮动右上角
       ├─ Column(verticalScroll) { MarkdownContent(fillMaxWidth) }
       ├─ BackHandler → onDismiss
       ├─ FullSensorOrientationWhileComposed
       └─ 一次性落位（initialCharOffset → scrollTo）
```

### 3.3 四屏交互一览

| 页面 | 数据源 | 入口组件 | 折叠条件 | 覆盖层身份 | 分页兜底 |
|---|---|---|---|---|---|
| Conversation | Room 消息 | `MessageBubble`（voice 分支） | 有全屏出口 && ≥800 字 | 消息 id | 无（会话全量） |
| History 详情 | Room 历史 | `MessageBubble` | 同上 | 消息 id | 无（会话全量） |
| eventListViewer | `HotSlotRow.Text` | `HotSlotRowItem` → `BubbleRowItem` | agent 行 && 有回调 && ≥800 字 | 行 key | `keepLastPayloadOnMiss=true` |
| hotslotView | 同 eventListViewer | 同上 | 同上 | 行 key | 同上 |

### 3.4 视觉方案

全屏采用「沉浸全宽」：`Surface` 底 + 对话页同款 `TopAppBar`（竖屏）+ 正文整宽滚动。

- 竖屏：头行只保留 Agent 图标 + 到达时间 + 关闭钮（不显示「Agent任务」文字）；
- 横屏：不渲染 TopAppBar，正文从顶部开始，✕ 以 0.35 半透明黑圆底浮在内容右上角；
- 气泡入口提示：头行 `Agent: HH:mm` 之后追加 14dp `OpenInFull` 图标（`TextSecondary`），
  **不用 `Modifier.weight(1f)` 撑行尾**——weight 会把头行撑到约束上限，短结果气泡也会被拉成满宽。

---

## 4. 详细设计

### 4.1 判定与入口

**判定**：`ChatMessage.isAgentJobResult()`（§2.1）。

**`MessageBubble` 参数**（`ui/components/MessageBubble.kt`）：

```kotlin
fun MessageBubble(
    message: ChatMessage,
    showCursor: Boolean = false,
    modifier: Modifier = Modifier,
    onLinkClick: ((String) -> Unit)? = null,
    onImageClick: (() -> Unit)? = null,
    /**
     * 非 null ⇒ 整卡可点（仅 agent job 结果气泡由调用方传入）。
     * 参数为最近一次正文按压记录的字符偏移；首次按压即点头行/内边距时为 null
     * （此前已有正文按压记录时会沿用旧值，见 §10.2 与 §8-14）。
     */
    onJobResultClick: ((charOffset: Int?) -> Unit)? = null,
)
```

气泡 `Box` 修饰符链（clickable 位于背景/描边之后、padding 之前 → 涟漪受圆角约束并覆盖整卡）：

```kotlin
Box(
    modifier = Modifier
        .widthIn(max = maxWidth)
        .clip(bubbleShape)
        .then(if (isUser) Modifier.background(BrandIndigo) else Modifier
            .background(MaterialTheme.colorScheme.surface)
            .border(1.dp, SurfaceVariant, bubbleShape))
        .then(
            if (onJobResultClick != null) Modifier.clickable { onJobResultClick(tappedCharOffset) }
            else Modifier,
        )
        .padding(horizontal = Spacing.md, vertical = Spacing.sm),
)
```

`tappedCharOffset` 由 `onContentTap` 写入（DOWN 时记录），clickable 的 `onClick` 读取。
仅 job 结果气泡传入 `onContentTap`，其余气泡零开销。

**正文渲染条件**（不变）：`isUser || !message.isFinal || onLinkClick == null` → 纯文本；
否则 → `AgentJobResultText`（§11/§12.3.1）。

**调用点**：

```kotlin
// ConversationScreen.kt
onJobResultClick = if (message.isAgentJobResult()) {
    { charOffset -> viewModel.onEvent(ConversationEvent.OpenAgentJobResult(message.id, charOffset)) }
} else {
    null
}

// HistoryDetailScreen.kt
onJobResultClick = if (message.isAgentJobResult()) {
    { charOffset -> viewModel.openAgentJobResult(message.id, charOffset) }
} else {
    null
}
```

**头行展开图标**（仅 `onJobResultClick != null` 时渲染）：

```kotlin
if (onJobResultClick != null) {
    Spacer(modifier = Modifier.width(Spacing.sm))
    Icon(
        imageVector = Icons.Filled.OpenInFull,
        contentDescription = stringResource(R.string.common_view_full_result),
        tint = TextSecondary,
        modifier = Modifier.size(14.dp),
    )
}
```

### 4.2 状态与事件（Conversation）

```kotlin
// ConversationUiState
/** 非 null = 展示中，值为入口消息 id（"ai-msg_agent_job_*"）。 */
val agentJobResultViewId: String? = null,
/** 打开时点击处的正文字符偏移；null = 落顶部。 */
val agentJobResultInitialCharOffset: Int? = null,

// ConversationEvent
data class OpenAgentJobResult(val messageId: String, val charOffset: Int? = null) : ConversationEvent()
data object CloseAgentJobResult : ConversationEvent()
```

`ConversationViewModel`：

- `Open` → 同时写 id 与 offset；`Close` → 同时清空；
- 会话切换/结束：`coordinator.currentSessionId.collect` 内、if/else 分支之外统一执行
  `copy(imageViewerMessageId = null, agentJobResultViewId = null, agentJobResultInitialCharOffset = null)`；
- 事件覆盖测试（`ConversationViewModelTest`）包含这两个事件，当前断言 55 个 sealed 事件。

**页面接线**（`ConversationScreen.kt`）：

```kotlin
// 根 Box 最末子项，图片查看器之后
AgentJobResultHost(
    viewId = uiState.agentJobResultViewId,
    initialCharOffset = uiState.agentJobResultInitialCharOffset,
    resolve = { id ->
        uiState.messages.firstOrNull { it.id == id }
            ?.let { AgentJobPayload(it.text, it.timestamp) }
    },
    onDismiss = { viewModel.onEvent(ConversationEvent.CloseAgentJobResult) },
    onLinkClick = onOpenLink,
)
```

底层列表禁滚：

```kotlin
userScrollEnabled = uiState.imageViewerMessageId == null &&
    uiState.agentJobResultViewId == null
```

### 4.3 覆盖层组件

文件：`ui/screens/conversation/AgentJobResultOverlay.kt`。

```kotlin
@Composable
fun AgentJobResultOverlay(
    text: String,                       // 结果正文（Markdown 原文）
    timestamp: Instant,                 // 消息到达时间（头行 HH:mm）
    onDismiss: () -> Unit,
    onLinkClick: (String) -> Unit,
    initialCharOffset: Int? = null,     // 点击处偏移；null = 落顶部
    scrollState: ScrollState = rememberScrollState(),  // 测试可注入
)
```

结构：

```text
Box(fillMaxSize, background = Surface)
  Column(fillMaxSize)
    if (!landscape) TopAppBar(
        title = Row { SmartToy(18dp, BrandIndigo); Spacer(sm); Text(HH:mm, bodySmall, TextSecondary) },
        actions = { IconButton(Close) },
        containerColor = Surface)
    Column(weight(1f).fillMaxWidth()
             .onSizeChanged { viewportHeight = it.height }
             .verticalScroll(scrollState)
             .padding(horizontal = md)
             .padding(top = if (landscape) xs else 0)
             .padding(bottom = md)) {
        MarkdownContent(text, onLinkClick, onTextViewReady = { bodyTextView = it },
                        modifier = Modifier.fillMaxWidth())
    }
  if (landscape) IconButton(Close, align = TopEnd, padding = sm,
                            background = Black 0.35f, shape = CircleShape)
```

要点：

- `BackHandler(enabled = true) { onDismiss() }`；
- `FullSensorOrientationWhileComposed()`（§4.4）；
- 竖屏头行时间经 `localizeDigits`（阿拉伯语环境使用阿拉伯-印度数字）；
- 横屏不渲染标题，正文顶部留 `Spacing.xs`；关闭钮是滚动容器的兄弟节点，不随内容滚走；
- `scrollState` 在横竖分支之外持有 → 切换分支不丢滚动位置；
- `MarkdownContent` 显式 `fillMaxWidth()`，表格获得全宽；正文不折叠，始终渲染全文；
- `jobResultTimeFormatter` 为文件内 private `HH:mm` formatter（与气泡头行同源重复，接受）。

### 4.4 旋转接线与清单

**取 Activity**：

```kotlin
private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}
```

（不使用 `androidx.activity.compose.LocalActivity`：activity-compose 1.9.0 尚无可用的
`LocalActivity`。）

**进入/退出全屏**：

```kotlin
@Composable
private fun FullSensorOrientationWhileComposed() {
    val activity = LocalContext.current.findActivity()
    DisposableEffect(activity) {
        val previous = activity?.requestedOrientation
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR
        onDispose {
            activity?.requestedOrientation = previous ?: ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        }
    }
}
```

- `FULL_SENSOR` = 四向随传感器，忽略系统「自动旋转」锁；
- 恢复用**进入前的原值**，而不是硬编码常量；
- 覆盖层因任何原因离场（关闭/未命中/会话切换/导航去 WebView）都会走 `onDispose`，不存在漏解锁路径。

**清单**（`src/main/AndroidManifest.xml`，MainActivity）：

```xml
<activity
    android:name=".MainActivity"
    android:exported="true"
    android:screenOrientation="portrait"
    android:configChanges="orientation|screenSize|screenLayout|smallestScreenSize|keyboardHidden"
    android:theme="@style/Theme.LivaChat">
```

副作用（均为期望行为）：

- `LocalConfiguration` 更新 → 主题与布局按新尺寸重组；
- `rememberScrollState` / `rememberLazyListState` 不重建 → 全屏正文与底层列表位置保持；
- 相机/会话/录制无 `onDispose` / `OnScreenStart` 重放，零扰动；
- `configChanges` 是 Activity 级永久行为变更：未来新增解锁旋转的页面时，须核查其是否隐式依赖
  重建来重置 `remember` 状态；
- `keyboardHidden` 为搭车声明：键盘显隐不再重建 Activity。

### 4.5 文案、本地化与无障碍

| 位置 | 资源 | 中文示例 |
|---|---|---|
| 关闭按钮 | `common_close` | 关闭 |
| 气泡/行展开图标 | `common_view_full_result` | 查看完整结果 |
| 折叠入口行 | `conv_job_remaining_chars`（plurals） | 查看全文 · 还有 %1$s 字 |
| 全屏头行 | 无文字，仅 Agent 图标 + `HH:mm` | — |

- 所有文案均存在于项目支持的每种语言的 `strings.xml`；
- 时间数字在气泡头行与全屏头行都经 `localizeDigits`（ar 阿数）；
- 气泡为整卡 clickable 后由 Compose 语义合并；展开图标/关闭按钮均有 `contentDescription`；
- 已知取舍：D13 后正文子节点没有独立 click action，TalkBack 需聚焦气泡/行节点才能触发全屏。

---

## 5. 边界与失败语义

| 场景 | 行为 |
|---|---|
| 点击非 agent job 消息 | `onJobResultClick == null` → 无 clickable、无涟漪、无响应；外观零变化 |
| 点击气泡内 http/https 链接 | 命中 span → `onLinkClick` → WebView；不进全屏；无双触发 |
| 点击气泡内非链接区 | 冒泡至气泡/行 clickable → 进全屏（偏移为按下处字符位置） |
| 打开时 id/身份未命中 | 不渲染覆盖层；`LaunchedEffect` 自动关窗清态 |
| 会话切换（含断连后新 session） | VM 清空 id 与 offset → 覆盖层关闭 → 方向恢复竖屏 |
| 覆盖层打开期间新 job 结果到达 | 底层列表实时增长；覆盖层按身份解析，不会被其它消息/事件顶替 |
| 同一身份的内容更新（Room `upsert` / rows 刷新） | 覆盖层显示该身份的最新正文 |
| 覆盖层打开期间点正文链接 | 导航 WebView → 组合销毁 → 方向先恢复竖屏（WebView 页保持竖屏）；返回后覆盖层仍在（VM 状态），滚动位置保持（§13） |
| 旋转（竖 ↔ 横） | Activity 不重建；覆盖层保持打开；正文重新排版；滚动位置按像素偏移保持（非比例，横屏内容变矮时可能接近末尾，属常规行为）。折叠气泡的度量不随宽度重测（见 §8-13） |
| 用户开启系统「自动旋转」锁 | `FULL_SENSOR` 仍可旋转 |
| 相机模式（VideoPreview 分支） | 无气泡入口，不适用；job 结果在 `VideoPreview.lastAiText` 中照常展示 |
| 超长结果（至 16KB） | 单 TextView 全量渲染 + 滚动；不截断、不分页 |
| 拖动起手于正文非链接区 | TextView 全手势不消费 → 外层 `LazyColumn` / `verticalScroll` 接管滚动 |
| 拖动起手于链接上（越过 touchSlop） | **死手势**：链接 DOWN 交回基类被 interop TextView 消费 → Compose 侧收不到 DOWN，无法中途接管滚动；MOVE 又不交回基类 → 既不内滚也不外滚；UP 落点仍为同一链接时照常触发链接 |
| 系统杀进程后重建 | VM 状态丢失 → 覆盖层关闭、方向随清单回竖屏（无悬空） |
| 未列入 `configChanges` 的配置变更（uiMode/locale） | Activity 重建（Compose VM 存活）→ 覆盖层按 VM 状态重开并重新解锁旋转；重建瞬间可能有一次竖屏闪烁 |
| 屏幕录制中旋转 | 采集门控要求「进程后台 × 屏幕交互」→ 前台全屏查看期间无采集，旋转对录制零影响 |
| 时间线分页窗口重取 | 见 §12.9：`keepLastPayloadOnMiss=true` 兜底；真正的退出是 `load()`/`retry()` 清空或用户关闭 |
| History 页 `loadSession` 重载 | 清空覆盖层状态（防悬空） |
| 帧查看器与 agent 全屏（时间线） | VM 互斥：打开一个清另一个 |

---

## 6. 测试

### 6.1 JVM 单测

| 文件 | 覆盖 |
|---|---|
| `ChatMessageTest` | `isAgentJobResult` 命中 `ai-msg_agent_job_*`；不命中 LLM 回复、启动提示、用户消息、近似前缀 |
| `ConversationViewModelAgentJobViewTest` | 默认 null；Open 写 id/offset；无 offset 留 null；Close 清空；会话切换/结束清空；与图片查看器同时清；打开态在显式关闭前不被新消息影响 |
| `ConversationViewModelTest` | sealed 事件覆盖列表包含 `Open/CloseAgentJobResult`（断言 55） |
| `JobResultScrollTargetTest` | `lineTop < viewport/3` → 0；超过 max → clamp；正常行 → `lineTop - viewport/3` |
| `JobResultCollapseTest` | `jobResultRemainingChars` 边界（截断/未截断/恰好行数上限/越界 clamp）；千分位格式 |
| `MarkdownTableSniffTest` | 前导/无前导/对齐/单列/最小短横/缩进 命中；正文、表头、水平线、代码块、空串 不命中；病态输入不抛错/不爆栈 |
| `HistoryDetailViewModelTest` | agent 查看器默认 null、open/close、`loadSession` 清空 |
| `HotSlotViewModelTest` | `onAgentJobClick/Dismiss`、与 `viewerFrame` 互斥、`load()`/`retry()` 清两覆盖层、Q11 轮询不清、分页兜底前提 |

### 6.2 androidTest

| 文件 | 覆盖 |
|---|---|
| `MessageBubbleJobResultClickTest` | 非链接正文点按触发整卡回调一次（D13 守护主用例）；长文本点击偏移落在后段；未传回调时无 click action；点链接只触 `onLinkClick` |
| `MessageBubbleCollapseTest` | 长结果按行边界裁剪且出现入口行；短结果不裁剪；字数超阈值但行数未超不裁剪；链接上起手拖动不内部自滚；无全屏出口不折叠 |
| `MarkdownContentTest` | TextView `isClickable/isLongClickable == false`；movement method 在位且**不是**平台单例 `LinkMovementMethod`（D13 实现）；既有渲染/链接用例回归 |
| `AgentJobResultOverlayTest` | 头行不显示「Agent任务」标题、关闭钮在位；正文（长文本 + 表格）可见；close 按钮 → `onDismiss`；返回键 → `onDismiss` |
| `AgentJobResultOverlayScrollTest` | 给定 offset 滚动到目标行；靠近文首落到 0；null offset 保持顶部；`SaveableStateHolder` 模拟导航往返后位置保持且不重放落位；关闭重开后重新落位 |
| `AgentJobResultHostTest` | 命中渲染；未命中自动关窗；null 空转；命中后转未命中——`keepLastPayloadOnMiss=true` 不关窗、默认关窗；换身份不复用上一条正文 |
| `AgentJobResultOrientationTest` | 覆盖层组合期间 `requestedOrientation == FULL_SENSOR`；退出恢复进入前的值 |
| `MainActivityManifestTest` | `configChanges` 含 ORIENTATION / SCREEN_SIZE / SCREEN_LAYOUT / SMALLEST_SCREEN_SIZE / KEYBOARD_HIDDEN |
| `HotSlotComponentsTest` | agent 长行折叠高度 == `getLineTop(24)` + 入口行 + 点击回调带 key/offset；短行不折；无回调不折 |
| `EventListViewerScreenTest` | 点 agent 行 → 覆盖层开→关；窗口移出锚点 → `load()` 入口清空覆盖层 |
| 回归 | `MessageBubbleImageClickTest`、`MessageBubbleRenderingTest`、`MarkdownContentTest`、`AgentJobStatusUiTest`、`HotSlotScreen` 相关用例、E2E |

> `MarkdownContentTest` 的测试宿主在顶部加 96dp padding：内容贴顶时正文中心可能落在挖孔/状态栏区
> （本机 insets≈99px），Espresso 注入会被系统拒绝（`INJECT_EVENTS`）。这是测试宿主级处理，
> 不影响产品布局。

### 6.3 手工验证

- 竖/横屏目检：表格列宽、长段落换行、旋转动画；
- 真机确认「旋转不重建」（configChanges 真实语义）；
- 气泡正文区点按进全屏 / 起手拖动只滚动不误开；
- OEM 系统上 `FULL_SENSOR` 覆盖清单锁；
- History 详情页的页面级观感（该屏 `hiltViewModel()` + final 依赖，androidTest 无法构造，
  由 VM 单测 + 宿主用例 + 气泡组件用例覆盖接线）；
- 全屏滚到底 → 点链接 → WebView → 返回 → 停在原处（D17）。

---

## 7. 文件清单

### 7.1 生产代码

| # | 文件 | 职责 |
|---|---|---|
| 1 | `domain/model/ChatMessage.kt` | 前缀常量 + `isAgentJobResult()` |
| 2 | `ui/components/MarkdownContent.kt` | 触摸直通 movement method、`onContentTap`、`onTextViewReady` |
| 3 | `ui/components/AgentJobResultText.kt` | 共享折叠文本块、表格嗅探、剩余字数 |
| 4 | `ui/components/MessageBubble.kt` | 气泡入口、偏移记录、折叠接线 |
| 5 | `ui/screens/conversation/AgentJobResultOverlay.kt` | 全屏覆盖层、落位、方向解锁 |
| 6 | `ui/screens/conversation/AgentJobResultHost.kt` | 身份解析 + 未命中守卫 + 覆盖层渲染 |
| 7 | `ui/screens/conversation/ConversationUiState.kt` | 查看器状态字段 |
| 8 | `ui/screens/conversation/ConversationEvent.kt` | Open/Close 事件 |
| 9 | `ui/screens/conversation/ConversationViewModel.kt` | 事件处理 + 会话切换清理 |
| 10 | `ui/screens/conversation/ConversationScreen.kt` | 气泡接线、宿主、禁滚守卫 |
| 11 | `ui/screens/history/HistoryDetailViewModel.kt` | 查看器 StateFlow + 开关 + `loadSession` 清空 |
| 12 | `ui/screens/history/HistoryDetailScreen.kt` | 气泡接线、宿主、禁滚守卫 |
| 13 | `ui/screens/mylife/hotslot/HotSlotUiState.kt` | `viewerAgentJobKey` + offset |
| 14 | `ui/screens/mylife/hotslot/HotSlotViewModel.kt` | 开合、互斥、`load()/retry()` 清空 |
| 15 | `ui/screens/mylife/hotslot/components/HotSlotRowItems.kt` | agent 行整行可点 + 折叠 |
| 16 | `ui/screens/mylife/hotslot/EventListViewerScreen.kt` | 行接线、宿主、禁滚守卫 |
| 17 | `ui/screens/mylife/hotslot/HotSlotScreen.kt` | 行接线、宿主、禁滚守卫 |
| 18 | `src/main/AndroidManifest.xml` | `configChanges` |
| 19 | `res/values*/strings.xml` | 关闭/查看全文/剩余字数的本地化 |

### 7.2 测试

`src/test/...`：`ChatMessageTest`、`ConversationViewModelAgentJobViewTest`、
`ConversationViewModelTest`、`JobResultScrollTargetTest`、`JobResultCollapseTest`、
`MarkdownTableSniffTest`、`HistoryDetailViewModelTest`、`HotSlotViewModelTest`。

`src/androidTest/...`：`MessageBubbleJobResultClickTest`、`MessageBubbleCollapseTest`、
`MarkdownContentTest`、`AgentJobResultOverlayTest`、`AgentJobResultOverlayScrollTest`、
`AgentJobResultHostTest`、`AgentJobResultOrientationTest`、`MainActivityManifestTest`、
`HotSlotComponentsTest`、`EventListViewerScreenTest`。

---

## 8. 关键取舍与已知限制

| # | 取舍 | 说明 |
|---|---|---|
| 1 | `FULL_SENSOR` 忽略系统自动旋转锁 | 保证 R4 在任何系统设置下成立；若要尊重用户锁，改 `FULL_USER` 即可 |
| 2 | 不做沉浸式 | 与图片查看器一致；横竖屏系统栏变化交给系统处理 |
| 3 | 不做复制/分享 | 纯只读；头行 `actions` 留作扩展位 |
| 4 | 折叠只对「有全屏出口」的消息生效 | 非 job 长消息若折叠会看不全；要做需先给它们全屏/展开能力 |
| 5 | 不用 `TextView.maxLines` | 带 movement method 的 TextView 不采纳该上限（真机三配置对照实测），改 Compose 侧行边界裁剪 |
| 6 | 测量失败 fail-open | 宁可长，不静默截断；见 §11.10 |
| 7 | 表格嗅探是启发式 | 围栏代码块内出现分隔行形态会误判为表格 → 只多占宽度，不影响内容与列宽 |
| 8 | 链接上起手拖动是死手势 | 既不内滚也不外滚；链接点击不失效；换取「正文 TextView 从不自滚」不变量 |
| 9 | TalkBack 需聚焦气泡节点 | D13 后正文子节点无独立 click action；整卡单机制/涟漪设计的已知取舍 |
| 10 | 时间线覆盖层不盖状态栏 | 沿用帧查看器先例；系统级真全屏未做 |
| 11 | History 页面级自动化缺失 | `hiltViewModel()` + final 依赖，androidTest 无法构造；接线由 VM 单测 + 宿主用例 + 组件用例覆盖 |
| 12 | 旋转后滚动位置按像素保持 | 横屏内容总高度通常变矮，原偏移可能接近末尾，属常规行为 |
| 13 | 折叠度量不随「仅宽度变化」重测 | `AgentJobResultText` 的测量 effect 以 `bodyTextView/markdown/collapsibleByLength` 为键；旋转/多窗口缩放不改变这些键，剩余字数可能沿用旧宽度下的值。裁剪高度仍是 24 行行高 |
| 14 | 点击偏移可能沿用上一次正文按压值 | 会话气泡与时间线行的 `tappedCharOffset` 只在正文 DOWN 时更新，头行/内边距点击不会清空；先点正文再点头行会落到旧位置。首次按压即点头行时为 null |

---

## 9. 性能与运行约束

- **单条上限 16KB**，全量 Markdown 一次渲染；不做分页/虚拟化；单 TextView。
- `MarkdownContent` 的 Markwon 实例按主题色 remember；`setMarkdown` 仅在
  `renderKey`（Markwon 身份 + 转换后的 markdown）变化时执行，避免重复解析。
- 折叠测量在组合后最多等待 4 帧拿 `TextView.layout`；首帧用估算高度防止「先铺满再收起」。
- 覆盖层状态在 ViewModel（或 rememberSaveable 的滚动标记），配置变更存活；进程死亡后丢失。
- 时间线内容来自既有 rows/Repository，不额外请求；Q11 轮询重取不触碰覆盖层状态
  （`reloadFirstPage` 不清，见 §12.9）。

---

## 10. 全屏交互细化

### 10.1 底色

覆盖层底与竖屏 TopAppBar 容器色均为 `Surface`（#1E293B），与气泡底色一致，视觉上「气泡被放大
铺满」。正文色不变（`MarkdownContent` 使用 `onSurface` = `TextPrimary`）。气泡的 1dp
`SurfaceVariant` 描边不搬——铺满全屏时描边落在屏幕边缘无意义。

### 10.2 点击处定位

需求：长文本气泡里点某处 → 全屏打开时停在该处，而非回到顶部。

**用「字符偏移」做桥**：

1. **气泡侧记录点击处偏移**：`MarkdownContent` 的 `onContentTap` 在**非链接 DOWN** 时复用
   `hitsLink` 同一套 `Layout` 计算求出 offset 并回调，随后返回 `false`（D13 不变）。
   DOWN 记录而非 UP 的原因见 §2.2。
2. **气泡把偏移带进事件**：`MessageBubble` 用 `remember { mutableStateOf<Int?>(null) }` 承接，
   clickable 的 `onClick` 读取当前值 → `OpenAgentJobResult(messageId, charOffset)`。
3. **状态**：`ConversationUiState.agentJobResultInitialCharOffset`，与 view id 同生共死
   （Open 写、Close/会话切换清）。
4. **覆盖层落位**：
   - `MarkdownContent` 的 `onTextViewReady` 把内部 `TextView` 交给覆盖层；
   - 等首测完成：`snapshotFlow { viewportHeight to scrollState.maxValue }.first { height > 0 }`，
     视口高度由 `Modifier.onSizeChanged` 实测（非屏幕高，竖/横屏都不等）；
   - `line = layout.getLineForOffset(offset)` → `y = layout.getLineTop(line)` →
     `scrollTo(jobResultScrollTarget(y, viewportHeight, scrollState.maxValue))`；
   - 落点 = 屏幕 **1/3** 处：
     `(lineTop - viewportHeight / 3).coerceIn(0, maxScroll)`（纯函数 `jobResultScrollTarget`，
     便于 JVM 单测）；
   - 靠近文首的行天然被 clamp 到 0。
5. **只随「打开」执行一次**：`placed by rememberSaveable { mutableStateOf(false) }` 作为卫兵，
   见 §13。横竖屏切换、同 id upsert 都不重放；关闭覆盖层即注销，再次打开照常落位。
6. **兜底与边界**：首次按压即点头行/内边距 → offset 为 null，落顶部；表格行内 offset 同样可
   映射（表格是 TextView 内的行结构）。**注意**：`tappedCharOffset` 只在正文 DOWN 时更新，
   点头行/内边距不会清空它——先点正文再点头行会沿用上一次的偏移（已知限制，见 §8-14）。
7. **未采纳的比例方案**：气泡 `tapY/textHeight` → `maxValue × f`。气泡 75% 宽与全屏整宽换行
   不同、表格高度占比差异大，落点会明显跑偏。

### 10.3 横屏布局

- **竖屏**：保留 TopAppBar（Agent 图标 + `HH:mm` + ✕）——时间可读；不统一为浮动 ✕。
- **横屏**：不渲染 TopAppBar，正文从顶部开始（顶部仅留 `Spacing.xs`），内容最大化；✕ 浮动右上角，
  配方对齐既有浮动钮（`Color.Black.copy(alpha = 0.35f)` 圆底 + `CircleShape` + `TextPrimary` 图标）。
  关闭钮是滚动容器的兄弟节点，不随内容滚走。
- 判定：`LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE`。
- 横竖切换（configChanges 不重建）：`scrollState` 在分支之外持有 → 切换分支不丢滚动位置，
  落位也不受影响。
- 取舍：✕ 直接浮在内容之上（不留 48dp 让位带），内容最大化；首行右端可能被钮压住，靠 0.35
  半透明底保证可读。

### 10.4 交互取舍汇总

- 全屏底色 = 气泡底色（D14）；
- 落位用字符偏移精确映射（D15）；
- 横屏去标题、✕ 浮在内容上（D16）；竖屏保留 TopAppBar；
- 竖屏不统一浮动 ✕（时间可读）；
- `onTextViewReady` 仅供覆盖层/折叠组件使用，KDoc 已标注用途。

---

## 11. 长结果折叠

### 11.1 触发条件与视觉

- 消息是 agent job 结果；
- **存在全屏出口**（`onJobResultClick != null`）——没有出口就不折叠，避免长结果看不全；
- 原文长度 ≥ `JOB_RESULT_COLLAPSE_MIN_CHARS`（800 字）。

视觉：

- 正文按**行边界**裁剪到 `JOB_RESULT_COLLAPSE_LINES`（24）行；
- 底部入口行：「查看全文 · 还有 N 字」+ 14dp `OpenInFull` 图标（`labelLarge` + `BrandIndigo`）；
- **无省略号、无渐变遮罩**：裁剪切在整行，不切在行中；
- 不在气泡内展开——点击整卡/整行即进全屏。

### 11.2 折叠判定

```kotlin
val collapsibleByLength = collapsible && markdown.length >= JOB_RESULT_COLLAPSE_MIN_CHARS
```

`collapsible` 由调用方传入，当前等于「有全屏出口」：

- Conversation / History：`onJobResultClick != null`；
- 时间线 agent 行：`clickableAsAgentJob`（`side == AGENT && onAgentJobClick != null`）；
- 其它调用点（预览、无回调）：false → 整段平铺。

### 11.3 渲染实现

**不用 `TextView.maxLines`**：真机三配置对照实测（纯 TextView / +movement method /
+Markwon Spanned）——纯 TextView 能截到 8 行并带省略号；带 movement method 后 Layout 仍是全量
30 行、无省略号。改用 **Compose 侧行边界裁剪**：

```kotlin
Box(
    modifier = if (clipActive) {
        Modifier.heightIn(max = collapseHeight).clipToBounds()
    } else {
        Modifier
    },
) {
    MarkdownContent(...)
}
```

三态：

| 状态 | 行为 |
|---|---|
| 未测量（`collapseMeasured=false`） | 用估算高度裁剪：`bodyLarge.lineHeight × 24`，经 `Density` 的 `TextUnit.toDp()`（随系统字号缩放）→ 防止「先铺满再收起」 |
| 已测量且 `collapse == null`（未超行数） | 不裁剪、无入口行 |
| 已测量且 `collapse != null`（确实超行数） | 裁剪高度 = `layout.getLineTop(24)`（px→dp），显示入口行 |

测量：

```kotlin
LaunchedEffect(bodyTextView, markdown, collapsibleByLength) {
    collapse = null
    collapseMeasured = false
    val textView = bodyTextView ?: return@LaunchedEffect   // 未测：保持估算，回调到达后自愈
    if (!collapsibleByLength) return@LaunchedEffect
    var layout = textView.layout
    var waitedFrames = 0
    while (layout == null && waitedFrames < 4) {
        withFrameNanos { }
        layout = textView.layout
        waitedFrames++
    }
    collapseMeasured = true                               // 无条件置位：fail-open
    collapse = layout?.let { collapseMetrics(it, textView.text.length, JOB_RESULT_COLLAPSE_LINES) }
}
```

`collapseMetrics`：先判 `layout.lineCount <= maxLines → null`；否则裁剪高度取第 `maxLines` 行的
上沿（行边界），剩余字数取 `textLength - layout.getLineEnd(maxLines - 1)`（clamp ≥ 0）。

### 11.4 剩余字数与入口行

- 计数口径是**渲染后文本**（表格管道符、分隔线不计），与用户视觉一致；
- 仅当 `layout.lineCount > 24` 时显示入口行；
- 文案经 `pluralStringResource(R.plurals.conv_job_remaining_chars, remaining, formatRemainingChars(remaining))`；
- `formatRemainingChars` 使用千分位（1240 → "1,240"）；
- 入口行本身不单独可点——点击事件由外层整卡/整行 clickable 承接。

### 11.5 边界

| 场景 | 行为 |
|---|---|
| 短结果（< 800 字 / 渲染 ≤ 24 行） | 不折叠、无入口行 |
| 无全屏出口 | 不折叠（`collapsible=false`） |
| 表格/列表超长 | 按行截断，不切在行中 |
| 折叠态点击 | 整卡点击 → 全屏；偏移在可见前缀内 → 落位照常准确 |
| 同 id 重投/内容更新 | `markdown` 变化 → 重新布局并重新测量，入口行数字刷新 |
| 横竖屏切换/多窗口仅宽度变化 | **不触发重新测量**：裁剪高度与「还有 N 字」沿用上次测量值（`scrollState` 不丢）。已知限制，见 §8 |
| 系统字号放大 | 首帧估算随 fontScale；首测后由 `getLineTop(24)` 精确值纠正 |
| 测量失败（4 帧内无 layout） | fail-open：不裁剪、无入口行（整卡仍可点进全屏） |
| 时间线非 agent 行 | 不折叠（组件未启用 collapsible） |

### 11.6 文案与常量

```kotlin
internal const val JOB_RESULT_COLLAPSE_LINES = 24
internal const val JOB_RESULT_COLLAPSE_MIN_CHARS = 800
```

阈值/行数收在共享组件内，调用方只传 `collapsible` 开关——「四屏一致」由结构保证，不靠自觉。

### 11.7 测试

- JVM：`JobResultCollapseTest`（剩余字数边界、千分位格式）；
- androidTest：`MessageBubbleCollapseTest`（行边界裁剪高度 == `getLineTop(24)`、入口行、短结果
  不折、字数超但行数未超不折、无出口不折、链接上起手拖动不内部自滚）；
- 时间线：`HotSlotComponentsTest`（agent 长行折叠 + 点击带 key/offset；短行/无回调不折）。

### 11.8 实现约束

1. **`maxLines` 不可用**：带 movement method 的 TextView 在本机不采纳上限（实测 Layout 仍全量、
   无省略号），因此走 Compose 行边界裁剪，确定性更强。
2. **首帧估算**：未测量时按 `lineHeight × 24` 裁剪，估算必须经 Density 的 `TextUnit.toDp()`
   随 fontScale，不能用 `.value` 直乘。
3. **测量失败 fail-open**：4 帧内拿不到 `layout` 时置 `collapseMeasured=true` 且
   `collapse=null` → 不裁剪、不静默截断。

### 11.9 常量取值说明

1. `JOB_RESULT_COLLAPSE_LINES = 24`：保留足够多的内容可见；
2. `JOB_RESULT_COLLAPSE_MIN_CHARS = 800`：24 行 CJK 约容纳 ~430 字；门槛若过低会折出
   「还有几十字」的小尾巴，800 字（≈44 行 CJK）只对确实超长的结果折叠；
3. 裁剪落在行边界（`getLineTop(N)`），不使用高度硬切；
4. 入口行只在确实超行数时出现，数字为渲染后剩余字符数。

### 11.10 实现不变量

1. **`collapseMeasured` 在测量后无条件置位**：提前 return 而不置位会让 `clipActive` 因
   `!collapseMeasured` 恒真——气泡被永久按估算高度截断，且因 `collapse == null` 永远没有入口行。
2. **`textView == null` 时保持「未测」**：`MarkdownContent` 的 factory/update 都会回调
   `onTextViewReady`，`bodyTextView` 变化会重跑 effect，自愈。
3. **MOVE 不交回基类**：正文 TextView 从不自滚，滚动一律由 Compose 容器负责；避免折叠后
   `Touch` 的 `widget.scrollTo` 造成内容内部错位。
4. **阈值/行数收在组件内**：调用方只传 `collapsible`，两处阈值天然一致。
5. **偏移 state 住在调用方**：共享文本块只经 `onContentTap` 上报偏移，不建 clickable、不建 state。

---

## 12. 多屏接入

### 12.1 范围

同一套「折叠 + 点击 + 全屏 + 落位」接入：

1. **History 详情页**——本地会话历史的只读详情；
2. **eventListViewer**——时间线搜索命中事件 ±10min 的事件流；
3. **hotslotView**——主时间线屏，与 eventListViewer 共用行组件与 ViewModel。

Conversation 是会话侧入口，四屏统一使用 `AgentJobResultText` 与 `AgentJobResultHost`。

### 12.2 数据来源与身份

| 页面 | 数据 | 身份 | 正文解析 |
|---|---|---|---|
| Conversation | Room 消息（实时帧写入） | 消息 id `ai-msg_agent_job_*` | `messages.firstOrNull { it.id == viewId }` |
| History | Room 历史（`HistoryUseCase.getSessionDetail`） | 消息 id | 同上 |
| eventListViewer / hotslotView | `HotSlotRow.Text`（由 events 构建） | 行 key `t-{event_ref}`（缺失时回落 `tsMs`） | `rows.filterIsInstance<HotSlotRow.Text>().firstOrNull { it.key == viewId }` |

时间线 agent 行只来自 `EventType.AGENT_JOB`，`side == BubbleSide.AGENT` 即判据，无需新字段。
agent 行的 body 已在 Repository 侧按 `text → summary → agent_name` 取第一个非空白值；三者全空白
则该行丢弃（`MylifeRepository`）。时间线无 `ChatMessage`，载荷是 `(body, tsMs)`。

### 12.3 设计

#### 12.3.1 共享折叠文本块

文件：`ui/components/AgentJobResultText.kt`（internal）。

```kotlin
@Composable
internal fun AgentJobResultText(
    markdown: String,
    onLinkClick: (String) -> Unit,
    /** 是否允许折叠（false = 整段平铺）。允许时由组件内部自判长度阈值。 */
    collapsible: Boolean,
    /** 正文非链接区按下 → 字符偏移（全屏落位用） */
    onContentTap: ((Int) -> Unit)? = null,
    modifier: Modifier = Modifier,
)
```

职责：

- 把 `MessageBubble` 与时间线 agent 行共用的折叠三态、TextView 引用、裁剪 Box、入口行整体收在一处；
- `JOB_RESULT_COLLAPSE_*` / `collapseMetrics` / `jobResultRemainingChars` /
  `formatRemainingChars` 一并定义在这里；
- **整卡/整行 clickable 与偏移 state 由调用方持有**（本组件不知道外层回调形态）；
- **含表格的正文自动给整宽**（`markdownHasTable` → `Modifier.fillMaxWidth()`）；不含表格时保持
  wrap-content——会话气泡是 wrap-content，无条件整宽会把短气泡拉满。

调用方：

- `MessageBubble`：`collapsible = onJobResultClick != null`，`onContentTap` 仅在 job 结果气泡传入；
- `BubbleRowItem`（时间线）：`collapsible = clickableAsAgentJob`，仅 agent 行传 `onContentTap`。

#### 12.3.2 History 详情页

- `HistoryDetailViewModel` 持有两个 StateFlow：
  `agentJobResultViewId`、`agentJobResultInitialCharOffset`；`openAgentJobResult(id, offset)` /
  `closeAgentJobResult()`；`loadSession` 在加载前清空两者（防悬空）。
- `HistoryDetailScreen`：
  - `MessageBubble` 调用点对 `isAgentJobResult()` 传 `onJobResultClick`；
  - 主列表之后、图片查看器之前渲染 `AgentJobResultHost`，`resolve` 从当前 `messages` 现取；
  - 底层 `LazyColumn` 的 `userScrollEnabled = imageViewerMessageId == null && agentJobResultViewId == null`。
- 附带效果：历史页长结果**同时获得折叠**（入口存在才折叠）；短结果与其它消息零变化；
  头行 ↗ 图标自动获得。

#### 12.3.3 时间线两屏与覆盖层宿主

**覆盖层载荷**：`AgentJobPayload(text: String, timestamp: Instant)`。不合成假
`ChatMessage`（避免脏数据），会话/History 调用点也经宿主传 `(text, timestamp)`。

**宿主**（`ui/screens/conversation/AgentJobResultHost.kt`）：

```kotlin
@Composable
internal fun AgentJobResultHost(
    viewId: String?,
    initialCharOffset: Int?,
    resolve: (String) -> AgentJobPayload?,
    onDismiss: () -> Unit,
    onLinkClick: (String) -> Unit,
    keepLastPayloadOnMiss: Boolean = false,
)
```

- `viewId == null` → 不渲染；
- `resolve` 按身份解析**当前**载荷（未命中 → null）；
- `lastPayload` 以 `viewId` 为键 `remember`——换身份即丢弃，绝不把上一条结果留给下一条；
- `resolved != null` 时更新兜底；渲染优先用 `resolved`；
- `resolved == null && keepLastPayloadOnMiss` → 用 `lastPayload` 兜底继续展示；
- 仍为 null → `LaunchedEffect` 调一次 `onDismiss()` 自动关窗；
- 渲染 `AgentJobResultOverlay(text, timestamp, initialCharOffset, …)`。

**ViewModel**（`HotSlotViewModel`）：

- `HotSlotUiState` 包含 `viewerAgentJobKey: String?`、`agentJobInitialCharOffset: Int?`；
- `onAgentJobClick(key, offset)`：写身份与偏移，**清 `viewerFrame`**（与帧查看器互斥）；
- `onFrameClick(frame)`：写帧对象，**清 agent 身份与偏移**；
- `onAgentJobViewerDismiss()`：清身份与偏移；
- `load()` / `retry()`：**实际执行重取时**调用 `clearOverlays()`，同时清帧查看器与 agent
  覆盖层（重取后事件可能被替换/移除；`load()` 命中同窗口已加载的早退分支时不清）；
- **红线**：`clearOverlays()` 只允许在 `load()`/`retry()` 公开入口体内调用，**不得**加进
  `reloadFirstPage()`——Q11 轮询（60s overview 驱动，init 里直接调 `reloadFirstPage`）若清空，
  会把用户正在看的覆盖层静默关掉。

**行组件**（`HotSlotRowItems.kt`）：

- `HotSlotRowItem(..., onAgentJobClick: ((rowKey: String, charOffset: Int?) -> Unit)? = null)`
  透传给 `BubbleRowItem`；
- 仅 `side == AGENT && onAgentJobClick != null` 时：
  - 整行 `clickable`（涟漪受气泡圆角约束）；
  - 头行「Agent任务」后加 14dp `OpenInFull` 图标；
  - 正文走 `AgentJobResultText(collapsible = true)`；
- 其余行（chat/transcribe/translate/帧）零变化。

**屏幕接线**：

- `EventListViewerScreen` / `HotSlotScreen` 都传
  `onAgentJobClick = { key, offset -> viewModel.onAgentJobClick(key, offset) }`；
- 两屏在 `statusBarsPadding` 内、帧查看器旁渲染宿主；
  `resolve` 从当前 `rows` 现取 `(body, tsMs)`；
- `keepLastPayloadOnMiss = true`（分页窗口，见 §12.9）；
- 底层 `LazyColumn` 的 `userScrollEnabled = viewerFrame == null && viewerAgentJobKey == null`。

### 12.4 边界

| 场景 | 行为 |
|---|---|
| History 点折叠气泡 | 进全屏；旋转解锁同会话页（覆盖层按 Activity 实例解锁，`configChanges` 已全局声明） |
| History 返回/离开 | 覆盖层随组合销毁 → 方向恢复 |
| History `loadSession` 重载 | 清空覆盖层状态 |
| 时间线 agent 行、调用方未传回调 | 不折叠、无图标、无 clickable → 行为不变 |
| 时间线长行折叠后点击 | 进全屏 + 落位被点处（偏移在可见前缀内） |
| 帧查看器与 agent 全屏同时触发 | VM 互斥（打开一个清另一个） |
| 覆盖层打开时纵向拖动底下列表 | 禁滚：`userScrollEnabled = viewerFrame == null && viewerAgentJobKey == null`；程序化滚动（锚点/追击）不受影响 |
| 重取后原事件消失 | 会话/History：未命中 → 宿主自动关窗；时间线：`keepLastPayloadOnMiss=true` → 继续展示最近载荷。真正的退出是 `load()`/`retry()` 或用户关闭（§12.9.1） |
| 分析重写使同一事件正文变长/变短 | 覆盖层显示**当前** rows 里的正文（身份解析，非快照） |
| 时间线 agent 行折叠后行高变化 | 锚点定位/高亮按**行索引**驱动（`scrollToItem` / `highlightKey`），与行高无关 |
| hotslotView | 同一行组件 → 同样获得折叠/点击 |

### 12.5 测试

| 层 | 用例 |
|---|---|
| JVM | `HistoryDetailViewModelTest`：open/close/offset、`loadSession` 清空 |
| JVM | `HotSlotViewModelTest`：`onAgentJobClick/Dismiss`、与 `viewerFrame` 互斥、`load()`/`retry()` 清空两覆盖层（含帧查看器）、Q11 轮询路径不清（红线反向断言）、分页截断前提 |
| androidTest | `AgentJobResultHostTest`：命中渲染 / 未命中自关窗 / null 空转 / 命中后转未命中两套口径 / 换身份不复用 |
| androidTest | `HotSlotComponentsTest`：agent 长行折叠 + 入口行 + 点击带 key/offset；短行/无回调不折 |
| androidTest | `EventListViewerScreenTest`：点 agent 行 → 覆盖层开→关；`load()` 入口清空覆盖层 |
| androidTest | `MessageBubbleCollapseTest` / `MessageBubbleJobResultClickTest` / `MessageBubbleRenderingTest` / `MarkdownContentTest` 回归 |
| 手工 | History 页面级目检（需真实会话数据）；两屏渲染目检 |

History 屏级自动化不可行的原因：`HistoryDetailScreen` 走 `hiltViewModel()`，ViewModel 依赖 final 类
`ConversationCoordinator`（含 SDK 依赖），androidTest 无法构造（禁 mockk、无 Hilt 测试基础设施），
UI 全链路旅程过重。接线由 VM 单测 + 宿主级用例 + 气泡/覆盖层组件用例覆盖。

### 12.6 文件清单

| # | 文件 | 实现内容 |
|---|---|---|
| 1 | `ui/components/AgentJobResultText.kt` | 共享折叠文本块 + 常量 + 表格嗅探 |
| 2 | `ui/screens/conversation/AgentJobResultHost.kt` | 身份解析 + 未命中守卫 + 覆盖层渲染 |
| 3 | `ui/components/MessageBubble.kt` | 使用共享组件（行为不变） |
| 4 | `ui/screens/conversation/AgentJobResultOverlay.kt` + `ConversationScreen.kt` | 载荷 `(text, timestamp)`；会话侧接线 |
| 5 | `ui/screens/history/HistoryDetailViewModel.kt` | 覆盖层状态 + 开关 + `loadSession` 清空 |
| 6 | `ui/screens/history/HistoryDetailScreen.kt` | 气泡接线 + 宿主 + 禁滚守卫 |
| 7 | `ui/screens/mylife/hotslot/HotSlotUiState.kt`、`HotSlotViewModel.kt` | 身份/偏移 + 开关 + 互斥 + 重置清空 |
| 8 | `ui/screens/mylife/hotslot/components/HotSlotRowItems.kt` | agent 行 clickable + ↗ + 折叠 |
| 9 | `ui/screens/mylife/hotslot/EventListViewerScreen.kt`、`HotSlotScreen.kt` | 行接线 + 宿主 + 禁滚守卫 |
| 10 | 测试 | §12.5 |

### 12.7 设计取舍

| # | 取舍 | 说明 |
|---|---|---|
| 1 | 宿主放 `ui/screens/conversation/` 而非 `ui/components/` | 与 `AgentJobResultOverlay` 同包；避免 `components → screens.conversation` 反向依赖；跨屏引用覆盖层已有先例 |
| 2 | 表格整宽由共享组件按内容条件处理 | 保留「无表格时短气泡 wrap-content」原意；会话与时间线天然一致，同时解决会话气泡表格被 75% 宽挤压 |
| 3 | `EventListViewerScreenTest` 用 `createAndroidComposeRule<ComponentActivity>()` | Espresso 真触摸链路需要 `activity.hasWindowFocus()` 等待，消实机 `INJECT_EVENTS` 偶发 |
| 4 | 时间线用「行身份」而非快照 | 重取/分析重写后正文自动跟新；未命中时按宿主策略处理（时间线兜底，会话/History 关窗） |
| 5 | 帧查看器与 agent 覆盖层互斥 | 同一时刻只展示一个覆盖层 |

### 12.8 验证基线

- 真机渲染级验证：eventListViewer 与 hotslotView 的折叠首屏 / 滚到行尾 / 点开全屏三态；
  折叠边界落在整行（第 24 行完整可见，无半行截断）；入口行紧随其后；行头「Agent任务 + ↗」
  渲染正确；含表格正文整宽且列宽正常；覆盖层底色 = 气泡底色、头行 = Agent 图标 + `HH:mm` + ✕、
  正文整篇不折叠、表格整宽；两屏逐像素一致（同组件、同主题）。
- History 详情页的页面级目检未自动化（原因见 §12.5），需真机手工过一遍。

### 12.9 分页窗口与健壮性

#### 12.9.1 分页窗口未命中兜底

时间线的身份 = 行 key（`t-{event_ref}`），正文由 Screen 按 key 现取。Q11 轮询在 overview 实例
变化时调用 `reloadFirstPage()` → `fetchPages(firstPage=true)` → `accept(replace=true)`：**列表被
首页整页替换**，已追加的第 2+ 页事件从 rows 消失。

若不做兜底，后果是：用户打开的 agent 结果若来自第 2+ 页（`loadMore` 或锚点追击追加），下一次
轮询后 key 不在 rows → 宿主判「未命中」→ 自动关窗，把正在阅读的用户踢出。

对照：帧查看器不受影响（持事件**对象**快照，与 rows 无关）；会话/History 的列表是**会话全量**，
未命中即真消失，守卫口径正确。故这是时间线特有的「分页窗口 ≠ 身份消失」误判。

**兜底实现**（收在宿主）：

- `AgentJobResultHost` 的 `keepLastPayloadOnMiss: Boolean = false`：置 true 时以「最近一次解析到的
  载荷」兜底继续展示；state 以 viewId 为键 `remember(id)`——换身份即丢弃，绝不把上一条结果留给
  下一条。未命中不关窗、身份仍在时仍优先现取；
- 时间线两屏传 `true`；正身消失的**真**出口仍是 `load()`/`retry()` 的 `clearOverlays()`
  （换窗口/重试）与用户 ✕；
- 默认 false = 会话/History 口径不变（未命中 → 关窗）。

#### 12.9.2 表格嗅探与病态输入

表格是否整宽的判据取 **GFM 分隔行**（表格唯一可靠标记），而不是「以 `|` 开头的行」：

```kotlin
internal fun markdownHasTable(markdown: String): Boolean =
    markdown.lineSequence().any(::isTableDelimiterRow)

private fun isTableDelimiterRow(line: String): Boolean {
    var hasPipe = false; var hasDash = false
    for (c in line) when (c) {
        '|' -> hasPipe = true
        '-' -> hasDash = true
        ':', ' ', '\t' -> {}
        else -> return false
    }
    return hasPipe && hasDash
}
```

- 为什么不用正则：分隔行正则在「长 `|-` 交替」行上会回溯到栈溢出（16k 字符复现
  `StackOverflowError`）。正文来自模型输出，长度/形态不可控；`markdownHasTable` 在**组合期**
  执行，抛的是 `Error` 不是 `Exception`，Compose 不捕获 → 进程崩溃。逐字符分类器与行长线性、
  常量栈、零回溯；
- 已知残留（全部是「误判 → 多占宽度」，不影响内容与列宽）：
  ① 围栏代码块内出现分隔行形态的行（如 `--- | ---`）；
  ② 冒号未校验位置（`| - : |` 这类非法单元格也命中）；
  ③ 表头行与分隔行未配对校验；
- 不为这些长尾引入 Markwon 解析器——此处只需要**测量前**的启发式。

#### 12.9.3 底层列表禁滚守卫

会话页与 History 页均以 `userScrollEnabled = ... == null` 守卫；时间线两屏采用同一守卫，
**同时覆盖帧查看器与 agent 覆盖层**：

```kotlin
userScrollEnabled = uiState.viewerFrame == null && uiState.viewerAgentJobKey == null
```

原因：覆盖层根容器不消费移动事件，头行/留白处拖拽会漏到下层列表（位置偏移；若把列表拖到底还会
经 `atListEnd` 触发 `loadMore()` 预取，属意外副作用）。程序化滚动（锚点 `scrollToItem`/追击）
不受 `userScrollEnabled` 影响，行为零回归。

---

## 13. 全屏滚动位置跨导航保持（D17）

### 13.1 现象与根因

现象：全屏查看中滚动到某处 → 点击正文里的链接 → 进入应用内 WebView → 返回 → Markdown 内容
跳回顶部。

根因由以下事实叠加：

1. **滚动值本身会跨导航恢复**：`rememberScrollState()` 的工厂内部即
   `rememberSaveable(saver = ScrollState.Saver)`；navigation-compose 对离开的目的地由
   SaveableStateHolder 保存并恢复 `rememberSaveable` 状态。
2. **落位 effect 在返回重组时重跑**：导航去 WebView 时覆盖层所在目的地离开组合，返回是**全新
   组合**——`LaunchedEffect(initialCharOffset, bodyTextView)` 作为新实例必然再次执行，把刚恢复的
   滚动位置再次 `scrollTo` 回 `initialCharOffset` 对应处。
3. **视觉表现为「顶部」的原因**：`initialCharOffset` 来自折叠气泡（≤24 行）内的点击落点，在全文
   里恒处文档前部；落位目标 `lineTop - viewport/3` 常被 `coerceIn(0, ·)` 收敛到 0，其余情形也
   仍落在文档首屏内。

### 13.2 方案

`placed by rememberSaveable { mutableStateOf(false) }` 作为一次性落位标记：

```kotlin
var placed by rememberSaveable { mutableStateOf(false) }
LaunchedEffect(initialCharOffset, bodyTextView) {
    if (placed) return@LaunchedEffect   // 返回/旋转重组：恢复的位置不被顶掉
    val offset = initialCharOffset ?: return@LaunchedEffect
    val textView = bodyTextView ?: return@LaunchedEffect
    // …等 viewport → getLineForOffset → scrollTo(jobResultScrollTarget)…
    placed = true
}
```

该机制只涉及 `AgentJobResultOverlay.kt`；Host / VM / NavGraph / MarkdownContent 无需感知。

### 13.3 语义

| 场景 | 行为 |
|---|---|
| 首次打开（气泡点击） | `placed=false` → 落位到点击处 |
| 点链接 → WebView → 返回 | `placed` / `scrollState` 随目的地状态恢复 → **不重放、停在原处** |
| 旋转（`configChanges` 不重建） | 组合不销毁，`placed` 不变 → 不重放 |
| 未列入 `configChanges` 的配置变更导致 Activity 重建 | `placed` / `scrollState` 随 saved state 恢复 → 不重放 |
| 关闭全屏 → 再次打开 | 覆盖层离开组合即注销 saveable → `placed=false`、滚动归 0 → 落位照常 |
| `offset == null`（点 padding 打开） | effect 早退、`placed` 不置位；返回时仍早退 → 恢复位置不受扰 |

### 13.4 测试

`AgentJobResultOverlayScrollTest`：

- `rememberSaveableStateHolder().SaveableStateProvider` 托管覆盖层模拟目的地销毁/重建——
  滚到中段 → 移除 provider（= 导航离开）→ 重新放入（= 返回）→ 断言 `scrollState` 值保持、
  **未再发生落位 `scrollTo`**；
- 关闭覆盖层后重开（新偏移）→ 必须重新落位（钉住 `placed` 随关闭注销的语义）；
- 既有落位用例（给定 offset / 文首 / null offset）回归。
