# LivaChat iOS Agent Job Status 分析与设计

**状态**：已实现并完成 iPhone/iPad 模拟器、真实 iPad 与真实 server Agent Job 取消验证（2026-10-21）；仅真机 VoiceOver/Reduce Motion 手测待补（见 §14）。
**日期**：2026-10-21
**目标**：在 iOS 上实现与 Android 当前实现一致的 Agent Job 运行状态：头像旁 badge、浮动任务面板、运行时长、行内取消确认与 BFF 取消链路。
**以 codebase 为准**：
- `docs/android_agent_job_status.md`（Android 当前实现：badge/面板/取消/行布局/面板高度）
- `docs/fd_job_status.md`（server 侧快照协议）
- Android：`AgentJobStatusBadge.kt`、`AgentJobListPanel.kt`、`AgentJobCancelConfirm.kt`、`ConversationScreen.kt`、`ConversationViewModel.kt`、`ConversationListenerAdapter.kt`、`MylifeApi.kt`、`AgentJobRepository.kt`
- iOS：`ConversationView.swift`、`ConversationCoordinator.swift`、`AppContainer.swift`、`ServerProbe.swift`、`ServerAddress.swift`（`ServerBase`）、`docs/ios_agent_job_view.md`（M3 结果查看，另一个功能）

> 本文覆盖 **运行中的 Agent Job 状态**（`job_status` 快照）与取消；M3 的 **Agent Job 结果气泡/全屏查看** 是另一个功能，复用消息模型。本文不涉及服务端/WS 协议改动。

---

## 1. 目标与范围

### 1.1 本期做

- SDK `job_status` 全量快照 → App 状态 → 头像右侧 badge（99+ 钳制）；
- 点击 badge 打开浮动任务面板：summary（2 行）、Started at HH:mm:ss、running time（1s 本地续算）、按 `startedAtMs` 升序；
- 面板行内 × → 行内确认卡片（问题独占一行、失败行、否/是、取消中 spinner）→ 调用 BFF 取消；
- 取消状态机：单请求、防双击、陈旧响应保护、404/409 静默、其他失败保留可重试；
- 面板关闭/打开、断连/会话结束时自清、取消中的状态保留；
- 面板内容驱动高度、上限按底部工具栏实测高度计算并保留 24pt 间距；
- 单测、UI 测试、iPad 真机 + 真实 server Agent Job 验证。

### 1.2 不做

- job 详情、重跑、steer、删除记录（服务端无 remove）；
- 历史 job / done/failed 展示（server 只推 running）；
- 通知/系统级展示；
- 修改 WS 协议、SDK 事件、`api-schema`、server/BFF（BFF 取消端点已存在）；
- Agent 结果消息的渲染/全屏（M3 已实现）；
- History 中展示 job status（job status 是 device 级瞬态，不落库，D8）；
- 横屏/极小窗口专项优化（iOS target 竖屏，iPad 兼容）。

---

## 2. Android 基线（源码级事实）

### 2.1 数据链路与展示

```
server s2c job_status（全量快照）
  → SDK AgentJob(jobId, agentName, summary, startedAtMs, runningTimeMs)
  → ConversationListenerAdapter._agentJobs（整体替换；非 CONNECTED / clearSession 清空）
  → ConversationViewModel → ConversationUiState.agentJobs
  → AgentJobStatusBadge(count) → 点击 → AgentJobListPanel(Popup)
```

- `job_status` 全量替换；空列表 = 清除；server 只推 running。
- `startedAtMs` 已由 SDK 做本地时钟换算；App 用 `now - startedAtMs` 每秒本地续算，负值回退 `runningTimeMs`。
- 当前代码事实（以此为准）：
  - **非 CONNECTED 清空**：`ConversationListenerAdapter.onConnectionStateChanged` 中 `_agentJobs.value = emptyList()`；
  - **clearSession 清空**：`replaceWithNewSession` 走 `clearSession`，job 列表同时清空；
  - **session.ended 清空**：`onSessionEnded` 清空；
  - 断连后到达的 job_status 帧丢弃；
  - **不落库、不进消息流、不受 suppressMessages 门控（D8）**。
- 列表清空时自动关闭面板（D5）；空列表不展开面板。
- badge 只在 count > 0 显示；count `99+`。

### 2.2 badge / 面板的当前 UI

**badge**（`AgentJobStatusBadge.kt`）：

- 28dp 高胶囊，`BrandIndigo.copy(alpha=.7)` 底 + 1.5dp `Surface` 描边 + 14dp SmartToy 图标 + count；触控区 48dp（iOS 用 `AgentRobot` template asset 镜像同一机器人头，见 §4）；
- 语音模式：固定在 avatar 右侧（`offset x = AvatarSize + Spacing.sm`，不动 avatar 布局）；
- **视频/头像隐藏模式**：当前代码在顶栏隐藏态头像簇中也显示 badge（`ConversationScreen.kt` 的 `avatarVisible == false` 分支）。
- 点击 `ToggleJobStatusPanel`。

**面板**（`AgentJobListPanel.kt`，Popup，`AgentJobPanelTopOffset = 180.dp`）：

- 宽 85%，圆角 16，Surface 不透明，1dp `Gold (#FFD700)` 边框 + shadow；顶部固定在 avatar 下方（180dp）；
- 内容驱动高度，`heightIn(max = maxHeight)`，超出后 LazyColumn 滚动；
- 面板级单 1s ticker（所有行共享 `nowMs`）；行按 `startedAtMs` 升序（防御性排序）。
- 行：
  - 第一行 summary（bodyLarge，最多 2 行，Ellipsis）；
  - 第二行 `Started at HH:mm:ss`（bodySmall/TextSecondary）+ elapsed（labelLarge/Success）左对齐相邻，右侧 48dp ×（16dp Close，TextSecondary）；
  - elapsed 格式 `Xs / Xm Ys / Xh Ym`，数字按 locale 本地化；started 为本地 `HH:mm:ss`；
  - 行无分隔线，`vertical padding = Spacing.sm`。
- 点击 × → **该行内覆盖确认 subview**（不是 AlertDialog）：
  - 遮罩 `Background.copy(alpha=.82)` 覆盖该行、吞点击、隐藏原行语义；
  - 卡片 Surface + Gold 1dp 边框 + radius 12；问题独占一行居中（最多 2 行）；失败行（`Cancel failed. Try again`，error 色）；操作行居中「否」「是」；
  - inFlight：操作行替换为 14dp spinner + `Cancelling…`；按钮不可重复点击；
  - 卡片可随行撑高；滚出视口状态保留；不锁定列表滚动。
- 中文/英文文案、plural 见 §2.4。

### 2.3 取消链路（`docs/android_agent_job_status.md` §7）

```
行 × → 行内确认 → 是
  → POST /api/mylife/agent-jobs/cancel?token=…&device_id=…
     body: {"job_id": "…"}
  → BFF 按 JobRecord.agent_name 路由 adapter.cancel_job
  → JobStore 变化 → change listener 立即评估并推新 job_status 快照
     （`job_status_enabled` 时；设备离线则不推，上线首报；`services/agent_relay/job_status.py`）
  → App 列表移除该行；无成功 toast，无本地乐观删除
```

- 请求超时：独立 60s（adapter abort 30s + 5s 宽限/重连余量）。
- 成功（200）：清确认态；**不自行删行**，等快照。
- 错误语义（status + code 双条件）：
  - `404/not_found`、`409/conflict`：任务已结束/不存在 → 静默清确认态，交给快照；
  - 其他（含 `503 cancel_incomplete`、`503 agent_relay_unavailable`、旧 server 路由级 `404/internal`）：保留确认态，显示 `Cancel failed. Try again`，可重试；
  - IO/超时：同上；请求可能仍在服务端进行，最终由快照收敛。
- 同一时刻仅一个确认态/一个在飞请求：
  - 未 inFlight 时点另一行 × 切换目标；inFlight 时其他 × 置灰、VM 忽略；
  - 目标 job 从快照消失（取消成功/自然完成/断连/session.ended）→ 清确认态（含 inFlight）；
  - 面板关闭（外点/返回）：未 inFlight 清确认态；inFlight 保留，重开面板继续显示进度；
  - 陈旧响应按 jobId 守卫，不覆盖新 target。
- 取消与自然完成竞态：结果仍可能送达；UI 不承诺「取消后无结果」。
- 成功/取消后 job-started 的 summary 消息仍留在 chat；不新增「已取消」消息（O13）。

### 2.4 文案与视觉

| 位置 | en | zh-Hans |
|---|---|---|
| badge accessibility | `%d agent task(s) running` plural | `Agent任务，%d 个运行中` plural |
| 面板标题 | `Agent task (%d)` / `Agent tasks (%d)` | `Agent任务 (%d)` |
| started | `Started at %@` | `开始于 %@` |
| cancel action (×) | `Cancel Agent task` | `取消 Agent任务` |
| cancel question | `Cancel Agent task?` | `取消 Agent任务？` |
| yes / no | `Yes` / `No` | `是` / `否` |
| in progress | `Cancelling…` | `正在取消…` |
| failed | `Cancel failed. Try again` | `取消失败，请重试` |

- 颜色：badge BrandIndigo 0.7 + Surface 描边；面板 Surface + Gold 边框；elapsed Success；× TextSecondary；错误 error。iOS 需新增 `Gold = #FFD700`（Android `Color.kt`）。
- 面板高度：`rootHeight - 180 - (toolbarHeight + 24)`；工具栏未测量时用 140 保守 reserve（约工具栏 104 + 24 + 字体余量）；不小于 0。

---

## 3. iOS 现状与差距

| 能力 | iOS 当前事实 |
|---|---|
| SDK | `AgentJob`（jobID/agentName/summary/startedAtMs/runningTimeMs）与 `LivaChatEvent.jobStatus([AgentJob])` 已存在（`Values.swift:31`、`LivaChatEvent.swift:23`） |
| 消费 | `ConversationCoordinator.handle` 把 `.jobStatus` 与 `.videoAck/.sessionChanged` 一起忽略（`ConversationCoordinator.swift:453`） |
| UI | 无 badge/面板；ConversationView 有 header / 96pt avatar / messageArea / toolbar |
| API | 无 BFF cancel 客户端；已有 `ServerBase`（wss→https/ws→http）与 TLS-aware `URLSessionServerProbe`/`ProbeSessionDelegate`，可抽取改造复用（现有 delegate 未限制自签 host，见 D7） |
| 会话身份 | `AppContainer.openConversationIfNeeded` 解析出 WS URL/token/deviceID/tlsPolicy；取消请求需要同源 token/deviceID |
| 生命周期 | runtime `start/stop` 由 AppContainer 控制；后台不结束会话（M1），因此 job 状态不应在切后台时清空 |
| History | 已实现；job status 是瞬态、**不落库**；历史页不展示 job status；历史页打开时 job 状态照常更新 |
| 已有相近 UI | M3 Agent 结果气泡/全屏覆盖层（`MessageBubbleView`/`AgentJobResultOverlay`）；不是运行状态面板 |

---

## 4. 设计决策

### D1 独立 `AgentJobStatusModel`（已定）

- 新增 `@MainActor final class AgentJobStatusModel: ObservableObject`，持有：
  - `@Published private(set) var jobs: [AgentJob]`、`@Published private(set) var isPanelPresented`、`@Published private(set) var cancelState: AgentJobCancelState?`（三者在 View 层都要触发刷新）；
  - `AgentJobCanceling?`（取消客户端；nil 时确认「是」直接按失败处理，不崩溃）。
- 由 AppContainer 持有（`let agentJobStatusModel`）并注入：
  - `AppRootView` 把 model 传给 `ConversationView`（`@ObservedObject`）；
  - `ConversationRuntimeFactory.make(..., jobStatus: AgentJobStatusModel?)` 再传给 Coordinator。
- Coordinator 的 model 参数用可选（默认 nil），既有单测不必全部改签名；生产路径始终由 AppContainer 传入，Job 状态测试显式注入。Coordinator 只负责 `.jobStatus → replaceJobs`、生命周期清空。
- 不塞进 ConversationViewModel（该 VM 已负责消息/权限/相机等；Android 放 VM 是平台差异，不放这里）。
- 这样 UI 测试可直接注入 fixture/fake canceler；AppContainer 的 DEBUG preview 路径负责 `configureForUITest(jobs:canceler:)`。fake canceler 成功后由 fixture 再触发一次 `replaceJobs` 模拟 server 快照（快照语义不写进 cancel client）。

### D2 job_status 消费与生命周期

- `.jobStatus(jobs)`：仅当 `isStarted` 且 `sdk.state.connectionState == .connected` 时 `model.replaceJobs(jobs)`（全量替换）；否则丢弃在途帧。
- `.connectionStateChanged(state != .connected)`：`model.clear()`（jobs/panel/cancel 全清）。
- `.sessionEnded`：`model.clear()`。
- `.connectionTerminated`：清（连接状态也会到非 connected）。
- **History `sessionSwitched`（删除当前会话 / 清空全部）**：`model.clear()`（对齐 Android `clearSession()` 清 `_agentJobs`）。该 publisher 只在 `processDelete(wasCurrent)` 与 `processClear` 触发（`HistoryService.swift:303/327`）；打开历史详情/列表**不会**触发，不影响 job 状态。
- **设置重建/stopRuntime**：`model.clear()`；settings apply/新 runtime 后由新快照重建。
- 后台：**不做显式清空**（iOS `onBackground` 只停媒体，不结束会话，`ConversationCoordinator.onBackground()`）。但若 WS 在后台掉线，`.connectionStateChanged(!= .connected)` 仍会清空——这是连接规则，不是额外的后台清空；测试与验收按此口径。
- job status 不进入 ConversationViewModel.messages、不写 HistoryStore；不受 `suppressMessages` 门控（同 Android D8），历史清空/history suppress 均不影响 job 帧消费。

### D3 badge 布局

- **语音模式**（avatar 可见）：
  - avatar 保持 96pt 居中；badge 在右侧；用「左槽 64pt + avatar + badge/空槽 64pt」的 HStack，两侧槽等宽，保证 avatar 仍居中；badge 与 avatar 间距 8pt（对齐 Android offset 思路）。
  - badge 视觉：28pt 胶囊，BrandIndigo 0.7、1.5pt Surface 描边、14pt AgentRobot 模板图标（SmartToy 机器人头）、count；触控高度 ≥44pt（iOS HIG；Android 48dp 的等价）。`99+` 钳制。实现用 `.frame(minHeight: 28)`，Dynamic Type 大字号下允许胶囊增高，避免计数被裁剪。
  - 出现/消失：fade + scale 200/150ms；Reduce Motion 下无动画。
- **视频/相机模式**（avatar 不显示）：badge 放在 header 的菜单按钮左侧（复用同一 badge 组件，独立按钮，不改变菜单按钮），点击同样的面板；对齐 Android 当前代码的隐藏态顶栏 badge。
- badge 是 `Button`：图标 `AgentRobot` `.accessibilityHidden(true)`；按钮 `.accessibilityLabel` 用 plural 文案（见 D8），trait `.isButton`；count=0 时整个按钮不渲染。
- 面板打开时 badge 被全屏 scrim 覆盖，不响应。
- UI 测试 ID：`agent_job_badge`。

### D4 浮动面板

- iOS 无 Popup；在 ConversationView 根 ZStack 加覆盖层：
  - 透明全屏 scrim（`Color.clear.contentShape(Rectangle())` + tap → `dismissPanel()`；不调暗；`.ignoresSafeArea()`；`.accessibilityHidden(true)`，避免 VoiceOver 停在无标签元素上）；
  - 面板卡片：宽 85% 内容宽（AppRootView max 600），顶部偏移 **180pt**，内容驱动高度，最大高度 = `rootHeight - 180 - bottomReserve`；
  - **坐标定义**：`rootHeight` = ConversationView 在安全区内可用于对话内容的完整高度（header 顶到 toolbar 底，不含系统状态栏/Home indicator）；180pt 与 toolbar 测量都相对同一坐标；`bottomReserve = 实测 toolbar 高度 + 24pt`，未测量时 140pt（同 Android）。
  - **高度测量（关键）**：列表内容用**非 lazy `VStack`**，不用 `LazyVStack`——后者在 ScrollView 中只实现视口附近的行，Preference 测到的高度会随滚动变化，`min(content,max)` 会出现首帧 0 高死锁或面板高度跳变。server 默认 `max_concurrent_jobs_per_device = 10`（`services/agent_relay/config.py:36`；可配置），非 lazy 成本可忽略。
    - 面板 header 用 `GeometryReader` 测高；每个 `AgentJobRow` 同样上报自身高度到 `RowHeightKey`（按 jobID 存字典，行消失时清理）；
    - 面板总高度 = `min(headerHeight + headerGap(8) + ΣrowHeights + innerVerticalPadding(16×2), maxHeight)`；测量完成前先用 `maxHeight`（避免 0 高），测量完成后收敛到内容高度；行内确认卡片撑高、Dynamic Type 变化都会触发重算；
    - 列表 `ScrollView` 的可视高度 = `面板总高度 - headerHeight - headerGap(8) - innerVerticalPadding(16×2)`（下限 0），**不是**面板总高度；内容超过可视高度时滚动，最后一行可达。卡片的 Gold 边框/圆角只包住面板，水平内边距 16pt（对齐 Android Spacing.md）。
  - 圆角 16，Surface，1pt Gold 边框，shadow；顶部 AgentRobot 18pt BrandIndigo + 标题 plural；下方 8pt 间距；
  - jobs 防御性按 `startedAtMs` 升序；
  - 1s ticker 使用 `TimelineView(.periodic(from: .now, by: 1))`，只在面板可见时存在；行共享 `nowMs`；
  - 面板 `zIndex`：高于消息/工具栏/普通 banner；Agent 结果覆盖层必须更高——现有 `AgentJobResultOverlay` 已是 `.zIndex(1)`，面板用 `.zIndex(0.5)` 或把结果覆盖层提到 2（实际路径上 scrim 已阻止打开结果覆盖层，但仍要保证层级）；Settings/History/Safari/WebPage 是系统 fullScreenCover/sheet，天然在上层；打开 Settings/History 前仍显式 dismiss（D5）。
  - 点击 scrim 关闭；**无系统返回手势**，因此把 VoiceOver 关闭动作挂在 panel header（combined accessibility element）上：`.accessibilityAction(named: Text(L10n.t("common_close"))) { dismissPanel() }`；不依赖容器级动作是否被暴露。
  - 面板呈现时对底层对话内容（含 header/badge/banner/消息/工具栏）`accessibilityHidden(true)`（scrim 只挡触摸，不挡 VoiceOver），面板作为模态区域；关闭后恢复。确认卡片同理只暴露卡片语义（D6）。
  - 实现补充：面板额外 `.accessibilityAddTraits(.isModal)`。实测 SwiftUI 的 `accessibilityHidden` 对容器子元素在 XCUITest 树中仍可能可见（触摸被 scrim 阻断，VoiceOver 由 isModal 隔离）；动态类型与 VoiceOver 最终以真机手测为准。
  - 打开面板时用 `@AccessibilityFocusState` 把 VoiceOver 焦点移到 panel header；关闭后回到 badge（或触发按钮），避免焦点停在已隐藏的底层元素上。
  - 动画 fade/scale 150ms，Reduce Motion 无。
- UI 测试 ID：`agent_job_panel`、`agent_job_list`、`agent_job_row_<jobID>`、`agent_job_scrim`。

### D5 面板/确认状态

- `togglePanel()`：jobs 非空才打开；关闭时未 inFlight 清 cancelState，inFlight 保留。
- `dismissPanel()`：同关闭语义。
- 面板因 jobs 清空自动关闭（D5 Android）；目标 job 消失清 cancelState（含 inFlight）。
- 打开 History/Settings 前调用 `dismissPanel()`（保留 inFlight 取消）；避免覆盖层下面残留面板。落点：`AppContainer.openHistory()` / `openSettings()` 先调 `agentJobStatusModel.dismissPanel()`——这两个入口由 AppRootView/ConversationView 的菜单、banner 共用，集中在这里才不会漏路径；Camera/相册/图片查看器是系统 cover，面板打开时 scrim 已阻止其入口。
- 请求不因面板关闭而取消；重开面板若目标仍在，继续显示「正在取消…」。

### D6 行 UI

- summary：16pt body，最多 2 行，尾部省略；
- meta 行：`Started at HH:mm:ss`（12pt caption，最多 1 行，空间不足时优先截断 started） + elapsed（14pt label，Success，最多 1 行）左对齐相邻；右侧 44pt × 触控（16pt xmark，textSecondary）；inFlight 时 × 禁用（所有行）；
- started：DateFormatter `HH:mm:ss`，本地时区/locale；elapsed: `Xs/Xm Ys/Xh Ym`（单位保持 h/m/s）；两者数字都按 locale 本地化（同 Android `localizeDigits`，用 NumberFormatter/locale digits，不能只靠 DateFormatter）；负值回退 `runningTimeMs`；
- 行无 Divider；垂直 padding 8pt；行用 `.accessibilityElement(children: .contain)`，文本块单独 `.combine`（label = summary + started + elapsed），× 保持独立 Button（label = `conv_agent_job_cancel_action`）；**不要对整行 `.combine`**，否则 × 会被合并进一个元素、VoiceOver 无法单独触发取消；
- 确认态：行内容与确认层放在 **`ZStack`（参与布局）而不是 `.overlay`**：
  - 底层行保留布局与绘制，`.accessibilityHidden(true)`；确认 scrim（`AppColor.background.opacity(0.82)`，`.accessibilityHidden(true)`）+ 卡片（Surface、Gold 1pt、radius 12）作为 ZStack 子层，行高 = max(原行高, 卡片高)；这样卡片比行高时长高、相邻行被推开，且每行高度测量能包含卡片。**不要给底层行设 opacity 0**（Android 是 0.82 遮罩，仍可见底纹）。
  - **不要用 `.overlay`**：overlay 不参与父布局尺寸，卡片会溢出/压到相邻行，或滚动时被裁。
  - 问题 16pt 居中最多 2 行；**不展示目标 summary**（与 chat 内 Agent 气泡重复，Android 2026-10-18 反馈）；错误 12pt error 最多 2 行；No/Yes 居中；inFlight 14pt spinner + `Cancelling…`（该区域 label = `conv_agent_job_cancel_in_progress`）；scrim 吞点击；滚动不锁定；
- 取消请求 200 成功后**行不立即消失**：确认态关闭，等待下一帧 `job_status`；只有新快照不含该 job 时才移除行（与 Android 一致，见 D7）。
- UI 测试 ID：`agent_job_cancel_action_<jobID>`、`agent_job_cancel_confirm`、`agent_job_cancel_no`、`agent_job_cancel_yes`、`agent_job_cancel_spinner`。

### D7 取消 API

- 协议与错误类型：
  - `protocol AgentJobCanceling: Sendable { func cancel(jobID: String) async throws }`；调用方（`@MainActor` model）await 结果，client 内部用 URLSession async（Swift 6 下 client 自身声明 `@unchecked Sendable` 或 actor，不要求协议是 @MainActor）。
  - `AgentJobCancelError: Error, Equatable, Sendable { statusCode: Int; code: String; message: String }`，`code` 缺失时用 `"internal"`。
- `AgentJobCancelClient`：
  - URL 用 `URLComponents`（scheme/host/port 来自 `ServerBase`）+ path `/api/mylife/agent-jobs/cancel`；query `token`/`device_id` 由 `URLComponents` 百分号编码。
  - `POST`，`Content-Type: application/json; charset=utf-8`，body `{"job_id":"..."}`。
  - `URLSessionConfiguration.ephemeral`，`waitsForConnectivity = false`，`timeoutIntervalForRequest = 60` **且** `timeoutIntervalForResource = 60`（对齐 Android `callTimeout(60s)` 的总时长；只设 request 是空闲超时，不代表总时长），`httpCookieStorage = nil`、`urlCache = nil`。
  - 2xx：解码 `{schema_version, job_id, status}`；`cancelled`/`completed`/`failed` 都按调用成功处理（Android 同样忽略 status）；空体/解码失败 → 通用失败。
  - 非 2xx：解析 `{error:{code,message}}`；无信封/非法 JSON/无 code → `code = "internal"`（对齐 Android `MylifeApi.parseError`；旧 server 路由级 404 走此路径，**必须按失败提示，不能静默**）。
  - 判定用 **status + code 双条件**，不能只看 status。
  - TLS：从 `ProbeSessionDelegate` 抽取“自签放行 + 拒绝重定向”，但**自签放行必须限定到当前 server host（及 port）**：现有 `ProbeSessionDelegate.handle` 对任意 host 的 serverTrust 都 `.useCredential`，原样复用会违背风险表“只对当前 server host 放行”的边界。实现为带 `expectedHost/expectedPort` 的共享 delegate：challenge 的 `protectionSpace` 不匹配 → `.performDefaultHandling`；`willPerformHTTPRedirection` 一律 `completionHandler(nil)`，token/body 不跟随重定向。`allowSelfSigned` 按当前 launch `TLSPolicy`。
    - **探针兼容**：`URLSessionServerProbe` 目前在 `init(tlsPolicy:)` 就创建 session/delegate，而 address 在 `probe(address:)` 才传入；加 expectedHost/Port 时须把 session/delegate 改为每次 probe 创建（或按 `task.originalRequest` 的 host/port 校验），否则 ServerProbe 会对所有地址都用错误的 expectedHost、探测失效。若每次创建，probe 结束须 `finishTasksAndInvalidate()`/`invalidateAndCancel()`，避免设置页反复探测泄漏 URLSession。
  - token/deviceID 来自 `openConversationIfNeeded` 解析出的 launch config（与 WS 同源）。AppContainer 在解析成功时保留该 config（或 token/deviceID/serverBase），据此创建/更新 client；无 config 或无 token 时不创建 client，model 保持空；若 model 有 fixture 且 canceler 为 nil，确认「是」直接进入通用失败态。
  - 日志只记录 path/status/code；不打印完整 URL、query、token、body。
- 状态机（`AgentJobStatusModel.confirmCancel`）：
  - 取当前 cancelState；inFlight 忽略；
  - 置 inFlight、清 error；调用 cancel；
  - 成功（含 200 status=completed/failed）：只有当前 target jobID 仍一致时清 cancelState；不删 jobs（等快照）；无成功 toast；
  - `404/not_found`、`409/conflict`：清 cancelState；不显示错误；
  - 其他错误（含 503 `cancel_incomplete`/`agent_relay_unavailable`、旧 server 404/internal、非信封错误、2xx 解码失败、超时/IO）：若 target 仍一致，置 inFlight=false + 通用失败文案（`conv_agent_job_cancel_failed`）；保留 target；
  - 所有回写校验 jobID；目标消失/断连/模型 clear 后陈旧响应不覆盖；
  - Swift `CancellationError`/Task cancel 原样退出（不吞、不显示失败）。
- AppContainer 接线：
  - config 解析成功 → 创建/更新 model 的 canceler（新 ServerBase + token/deviceID + TLSPolicy）；
  - settings apply：`stopRuntime()` → `agentJobStatusModel.clear()` → 新 config → 新 client；旧 client 不与新 runtime 混用，替换时 `invalidateAndCancel()` 旧 URLSession；
  - UI 测试注入 fake canceler（`-uiTestJobStatus`，见 §7.2）。

### D8 时间/本地化

- `AgentJob.startedAtMs` 已是本地时钟；`now - startedAtMs`；负值回退 `runningTimeMs`。
- 新增字符串 en/zh：`conv_agent_jobs_title`（plural）、`conv_agent_jobs_running`（plural）、`conv_agent_job_started`、`conv_agent_job_cancel_action/question/yes/no/in_progress/failed`。
- **plural 必须走 Format，不能用 `L10n.t(key)` 直接显示**：`L10n.t` 只做 `NSLocalizedString`，不会替 String Catalog 的 plural 变体选分支，直接渲染会显示格式串/占位符。新增 helper（与 `HistoryDateFormatting.subtitle` 现有写法一致，`HistoryDateFormatting.swift:23-29`）：
  ```swift
  static func plural(_ key: String, _ count: Int) -> String {
      String.localizedStringWithFormat(t(key), count)
  }
  ```
  `Localizable.xcstrings` 中 `conv_agent_jobs_title`/`conv_agent_jobs_running` 配 `variations.plural`（en: one/other；zh-Hans: other），值用 `%lld`；`conv_agent_job_started` 用 `%@`。例如 en title `Agent task (%lld)` / `Agent tasks (%lld)`，zh `Agent任务 (%lld)`；badge label en `%lld agent task running` / `%lld agent tasks running`，zh `Agent任务，%lld 个运行中`。
- 单测覆盖 en one/other 与 zh-Hans 的 plural 输出、无 `%lld` 残留；UI 测试断言 badge/标题不出现占位符。
- 新增 `AppColor.gold = #FFD700`；不新增其他颜色。
- 图标使用自定义 template asset `AgentRobot`（按 Android Material `SmartToy` 机器人头绘制；SF Symbols 无等价物）。
- 可访问性：badge label plural + count；× label；面板标题；确认卡片读屏不重复底层行；44pt 触控；Dynamic Type 由 appFont。

---

## 5. 状态机

```
                  job_status(non-empty)              job_status([]) / disconnect / sessionEnded / sessionSwitched / stop
AgentJobStatusModel jobs ────────────────▶ present ───────────────────────────────▶ empty
   │  isPanelPresented (toggle/dismiss; empty -> false)
   │  cancelState: nil | (job, inFlight, error)
   │
   └─ confirm(job):
        inFlight=true ── success ──▶ nil (wait snapshot)
                      ├─ 404/not_found / 409/conflict ──▶ nil
                      ├─ other ──▶ (job, false, error)
                      └─ stale (target changed/gone) ──▶ ignore
```

- 只有 jobs 非空时 badge 可见、面板可开。
- 面板关闭：未 inFlight 清 cancelState；inFlight 保留。
- 目标 job 从新快照消失：清 cancelState（含 inFlight）。
- 断连/会话结束/clearSession（history switch）/stop：jobs 清空、面板关、cancelState 清。

---

## 6. UI 布局

```
ConversationView
  header [status | ... | badge(camera) | menu]
  avatar HStack [64 slot | avatar 96 | badge/64 slot]
  messageArea
  toolbar (height measured via PreferenceKey)
  overlay:
    if panel:
      scrim (tap dismiss)
      panel card (top 180, width 85%, height min(content, max), Gold border)
        header AgentRobot + Agent tasks (N)
        TimelineView 1s → rows
          summary (2 lines)
          Started at HH:mm:ss  1m 23s [×]
          (if confirming) scrim + inline confirm card (question/error/No/Yes or spinner)
```

- `rootHeight` = ConversationView 安全区内高度（header 顶到 toolbar 底）；面板 maxHeight = `rootHeight - 180 - (toolbarHeight > 0 ? toolbarHeight + 24 : 140)`，下限 0。
- 内容高度测量：非 lazy `VStack` + 每行 `GeometryReader`/PreferenceKey 上报高度；面板总高 = `min(header + 8 + Σrows + 16×2, maxHeight)`，列表 ScrollView 可视高 = `面板总高 - header - 8 - 16×2`；未测量完成前用 maxHeight。**不要**在 ScrollView 外层直接测“内容总高度”（greedy/首帧 0 高/ LazyVStack 虚拟化都会失真）；也不要依赖 `.fixedSize` 绕过 maxHeight。
- 面板呈现时底层对话内容 `.accessibilityHidden(true)`；面板/确认卡片是唯一可访问区域。
- 面板宽度 0.85 * conversation content width（iPhone 375/393 宽约 319/334pt；iPad 600 宽下 510pt）。
- 面板不覆盖底部工具栏；scrim 全屏。
- badge 动画/面板动画 respect Reduce Motion。
- iPad：内容与面板受 AppRootView maxWidth 600 约束。

---

## 7. 测试计划

### 7.1 单元/集成

- `AgentJobStatusModel`（fake canceler）：
  - replace 非空/空、自动关面板、目标消失清 cancel；
  - toggle/dismiss（未 inFlight 清、inFlight 保留）；
  - request/cancel/no/yes、inFlight 防双击、切换目标；
  - **200 成功但无新快照 → cancelState 清、行仍在**；下一帧不含该 job → 行移除（证明无乐观删除）；
  - canceler 为 nil → 确认后进入失败态；
  - 404/not_found、409/conflict 静默；503 cancel_incomplete/agent_relay_unavailable、旧 server 404/internal、2xx 解码失败、超时 → 失败保留；
  - 陈旧响应（目标变化/清空）不覆盖；断连/clear 清 jobs/cancel；后台不做显式清空（连接掉线仍清）；
  - 历史 session switch 清 jobs（只覆盖 delete current/clear all，打开历史不触发）。
- 格式化/本地化：elapsed `Xs/Xm Ys/Xh Ym`、负值回退、started `HH:mm:ss`、locale 数字；99+；plural helper 在测试进程 locale=en 下验证 one/other、无 `%lld` 残留。**zh-Hans 不要靠单测运行时切 bundle locale**（`NSLocalizedString`/bundle 本地化缓存不可靠），放 UI 测试用 `-AppleLanguages (zh-Hans)` 验证。
- `AgentJobCancelClient`（URLProtocol/fake session）：
  - URL/path/query 百分号编码/body/Content-Type、POST；request+resource 均 60s、waitsForConnectivity=false；200 解析；
  - 带信封的 404/409/503 解析出对应 code（not_found/conflict/cancel_incomplete/…）；旧 server 404 无信封 → `code="internal"`；非 JSON；空体/2xx 解码失败；超时/IO；
  - 重定向被拒绝（不跟随、不发送 token/body 到新 host）；自签只在 expectedHost/Port 生效，错误 host 走系统校验；
  - 日志不含 token/完整 URL。
- Coordinator：jobStatus replace、非 connected 帧丢弃、断连/sessionEnded/stop/sessionSwitched 清；jobs 不进消息/History；history suppress 不影响 jobStatus。
- 服务端 BFF 已由 Android 覆盖；iOS 只测 client 契约。

### 7.2 UI 测试（静态注入）

- `-uiTestJobStatusCount <n>` + `-uiTestJobCancelResult <mode>`（`-uiTestJobStatus` 为 2 个 fixture 的简写；与 M3 既有 `-uiTestAgentJob` 区分）：
  - badge 出现/99+；点击开面板；outside tap 关闭；空 jobs 关闭；
  - 行 summary/started/elapsed；排序；1s ticker（可选检查文本变化）；badge/标题无 `%lld` 等占位符；
  - 本地化：默认 en（1/2 个 job 的 singular/plural）；`-AppleLanguages (zh-Hans)` 启动验证中文 badge/标题/确认卡文案，无占位符；
  - × 开行内确认；No 收起；Yes inFlight → spinner；
  - **fake 返回 200 后不注入新快照 → 行仍在、确认态关闭**；再注入不含该 job 的快照 → 行移除；
  - fake 失败 → 错误可重试；404/409 静默（行仍由快照决定）；
  - 取消中关闭面板再打开 → spinner 仍在、请求未中断；目标 job 从快照消失 → 确认态清空；
  - 另一行 × inFlight 禁用/切换；
  - 高度/滚动（1 行 vs 10 行；最后一行可达；上限不超过 maxHeight；XCUITest frame 断言 panel.maxY ≤ toolbar.minY − 24）；iPad；
  - 相机模式 header badge；
  - Reduce Motion/Dynamic Type 冒烟；面板/确认卡片模态 a11y（底层对话不可访问、可执行关闭动作）。

### 7.3 真机

- 真实 server 触发 Agent Job（agent task）→ badge count/面板；
- 取消运行中 job → inFlight → 快照收敛后行消失（不依赖响应立即删行）；result 是否送达的竞态可接受；
- 自然完成 → 行消失、结果消息进入会话；
- 断连/session end/设置重建/history 删除当前会话 → jobs 清空；
- 大字体/中文/旁白；M1/M2/M3/history 回归。

---

## 8. 实施顺序

1. `AgentJobStatusModel` + 状态机/格式化/plural helper 单测；
2. `AgentJobCancelClient`/协议 + 60s request/resource + host-scoped 自签 delegate + 拒重定向 + client 单测；
3. Coordinator jobStatus/生命周期 + RuntimeFactory/AppRootView/ConversationView 注入 + AppContainer config/client 生命周期 + history session switch 清空 + 单测；
4. Badge/面板/行内确认 UI + 模态 a11y + 字符串/Gold；
5. UI 测试 fixtures/fake canceler（含“200 无快照不删行”）+ 模拟器全量；
6. iPad 真机 + 真实 server；文档实施记录。

---

## 9. 验收标准

- [ ] `job_status` 非空 → badge 出现（count 99+），空 → 消失；面板空自动关；
- [ ] 面板列表按 started 升序、summary 2 行、started HH:mm:ss、running 1s 续算（负值回退）；
- [ ] 面板宽 85%、顶 180、内容驱动高度（1 行不撑满、10 行到上限）、超限滚动、底边距工具栏 24pt、不遮挡工具栏；
- [ ] × → 行内确认；No/Yes；inFlight spinner 不可重复；失败文案可重试；404/409 静默；
- [ ] 取消 200 后行不立即消失，下一帧快照移除；无乐观删除/成功 toast；
- [ ] badge/标题 plural 正确（en one/other、zh-Hans），无占位符；面板模态时底层对话不可被 VoiceOver 访问，可执行关闭；
- [ ] 断连/session.ended/设置重建/history 删除清空 jobs；后台不额外清空（平台差异记录）；
- [ ] job status 不写 History/消息；历史/M3 结果功能不回归；
- [ ] 单测、UI 测试、iPad 真机、真实 server 通过；无 server/SDK/协议改动。

> 实施后的实际验证结果与待补项见 §14.3/§14.4（模拟器项已完成，真机签名与真实 Agent Job 成功/取消待补）。

---

## 10. 风险与差异

| 风险/差异 | 说明 | 缓解 |
|---|---|---|
| iOS 无 Popup/系统返回 | Android 外点/返回关闭；iOS 用 scrim + 无系统返回 | scrim 点击关闭；打开 History/Settings 前 dismiss；状态机同 Android |
| 视频模式 | 旧 D9 说不展示；当前 Android 顶栏显示 badge | 以当前代码为准：相机模式 header badge；面板可用 |
| 后台 | Android 后台结束 session 清 jobs；iOS 不结束 session | iOS 后台不做显式清空；若 WS 掉线仍按连接状态清；连接/session 终止才清；记录差异 |
| 取消与自然完成竞态 | 结果仍可能送达 | 不承诺无结果；文案不承诺；快照为唯一事实源 |
| 取消超时 | 服务端可能仍在取消 | 失败可重试；最终由 job_status 快照收敛；不乐观删行 |
| ScrollView 内容高度 | SwiftUI ScrollView 默认 greedy；LazyVStack 虚拟化使总高度测量失真；直接测首帧可能 0 高 | 非 lazy VStack + 每行高度汇总；未测量前用 maxHeight；1/10 行、Dynamic Type、确认卡片撑高真机验证 |
| 工具栏测量 | iOS 15 无 onGeometryChange | PreferenceKey；未测时 140 reserve；真机截图验证 24pt |
| 视频/相机模式 | 面板锚点 180 与 header badge | 真机确认不遮挡相机/工具栏 |
| iPad | 目标竖屏、AppRootView 600 宽 | 面板 85% 宽；大字体/长文案溢出测试 |
| Cancel API 自签 | BFF 与 WS 同 host:port，自签；现有 `ProbeSessionDelegate` 对任意 host 放行自签，不能原样复用 | 抽取带 expectedHost/Port 的共享 delegate；仅当前 server 放行；拒绝重定向；单测错误 host 不自签放行 |
| 旧 server | 无 agent-jobs/cancel 路由 | 404/internal → 显示失败，不静默；对齐 Android O3 |
| 历史联动 | history delete/clear current 会清 jobs（Android clearSession），但 job 服务端仍可能运行 | 等下一帧快照重建（变化帧或 ≤15s 周期帧，`job_status_interval_s`）；文档记录 |

---

## 11. 已拍板决策（2026-10-21）

全部采纳推荐项，无偏离：

| # | 决策 | 结论 |
|---|---|---|
| 1 | 状态归属 | **独立 `AgentJobStatusModel`**（D1），不塞入 ConversationViewModel |
| 2 | 面板实现 | **SwiftUI overlay + 全屏透明 scrim**（D4），不引入 UIKit Popup |
| 3 | 相机模式 badge | **放 header 菜单按钮左侧**（D3），对齐 Android 当前隐藏态顶栏；旧 D9“视频不展示”不采用 |
| 4 | 取消客户端 | **抽取 `ProbeSessionDelegate` 并新增 expectedHost/Port 约束**（D7），拒绝重定向 |
| 5 | 触控尺寸 | **44pt**（iOS HIG；视觉 28pt 胶囊，Android 48dp 为平台等价） |

实施按 §8 顺序进行；实现后在本文件追加实施记录（与 `docs/ios_chat_history.md` §15 同格式）。

---

## 12. 文件索引

| 主题 | 文件 |
|---|---|
| 本文 | `docs/ios_agent_job_status.md` |
| Android 状态/取消 | `docs/android_agent_job_status.md` |
| Android 协议基线 | `docs/fd_job_status.md` |
| Android UI | `AgentJobStatusBadge.kt`、`AgentJobListPanel.kt`、`AgentJobCancelConfirm.kt`、`ConversationScreen.kt` |
| Android 状态/API | `ConversationViewModel.kt`、`ConversationListenerAdapter.kt`、`MylifeApi.kt`、`AgentJobRepository.kt` |
| iOS SDK | `LivaChatEvent.swift`、`Values.swift` |
| iOS 会话/UI | `ConversationView.swift`、`ConversationCoordinator.swift`、`ConversationRuntimeFactory.swift`、`AppContainer.swift`、`AppRootView.swift` |
| iOS TLS/基址 | `ServerProbe.swift`、`ServerAddress.swift`（`ServerBase`） |
| iOS M3 结果查看（区分） | `docs/ios_agent_job_view.md`、`AgentJobResultOverlay.swift` |
| History（区分） | `docs/ios_chat_history.md` |

---

## 13. 对抗式自审修订记录（2026-10-21）

本轮以当前代码为准逐条核对 Android 基线、iOS 现状与 server 契约；以下是发现的**严重问题**及已落入正文的修订（未改代码）：

| # | 严重问题 | 修订落点 |
|---|---|---|
| S1 | 原方案用 `LazyVStack` + 测量“内容总高度”再 `min(content,maxHeight)`；SwiftUI 虚拟化/首帧 0 高会使面板高度失真或死锁 | D4、§6：非 lazy VStack + 每行高度汇总；未测量前用 maxHeight |
| S2 | 直接用 `L10n.t` 渲染 plural key 不会选择 String Catalog 的 plural 分支，badge/标题会出现格式占位符 | D8：新增 `L10n.plural`（`localizedStringWithFormat`），列出 en one/other 与 zh-Hans 条目；单测/UI 测试占位符 |
| S3 | 原方案称“只对当前 server host 放行自签”，但现有 `ProbeSessionDelegate` 对任意 host 的 serverTrust 都放行；原样复用不成立 | D7：带 expectedHost/Port 的共享 delegate；错误 host 走系统校验；拒绝重定向；client 测试 |
| S4 | 只写“60s 超时”但未区分 request 空闲超时与 resource 总时长，URLSession 默认 resource 7 天，与 Android `callTimeout(60s)` 不等价 | D7：request/resource 均 60s，waitsForConnectivity=false |
| S5 | “旧 server 404/internal”未定义 code 来源；若 iOS 把无信封 404 当 `not_found` 静默，会让用户误以为已取消 | D7：非 2xx 无信封/无 code → `"internal"`；status+code 双条件；2xx 解码失败也为失败 |
| S6 | UI 测试计划写“fake cancel 成功 → job 移除”，与“无乐观删除、等快照”的设计矛盾，会测出假行为 | §7.2/§9：200 成功仅清确认态；必须注入新快照才移除 |
| S7 | 面板是事实上的模态覆盖层，但只挡触摸；VoiceOver 仍可访问底层对话，且无系统返回可退出；整行 `.combine` 还会吞掉 × 按钮 | D4/D6/§6：底层 accessibilityHidden、面板提供关闭动作；行用 `.contain` + 文本块 `.combine`，× 保持独立按钮；测试覆盖 |
| S8 | AppContainer 只被描述为“持有 model”，未定义 config/凭据保留、client 更新时序、既有测试兼容与 UI 测试注入 | D1/D5/D7/§8：config 保留、settings apply 顺序、Coordinator 可选参数、`-uiTestJobStatus` |
| S9 | “后台不清空”表述会让测试误以为后台一定保留；若 WS 掉线仍应清 | D2/§10：后台不做显式清空，但连接状态规则仍生效 |
| S10 | “JobStore 变化 → 立即推快照”未注明 `job_status_enabled`/离线条件 | §2.3：补条件与代码依据 |
| S11 | 原方案说确认卡片“行内覆盖”，若用 SwiftUI `.overlay` 不参与布局，卡片会溢出/压行，Android 的 Box 语义要求行随卡片撑高 | D6：确认态用 `ZStack`（参与布局），行高 = max(原行, 卡片)，测量纳入卡片 |
| S12 | 原方案把“面板总高”和“ScrollView 可视高”混为一谈，且未计入 header/list 间距；ScrollView 取 maxHeight 会把 header 撑出卡片或末尾行被截 | D4/§6：面板总高 = min(header+8+Σrows+16×2, maxHeight)；ScrollView 可视高 = 总高 − header − 8 − 16×2 |
| S13 | 给 `ProbeSessionDelegate` 加 expectedHost/Port 与现有 `URLSessionServerProbe`（init 创建 delegate、probe 时才给 address）时序冲突，直接改会使探测全部失效 | D7：probe 每次创建 session/delegate，或按 `task.originalRequest` 校验；补兼容说明 |
| S14 | 单测内切换 bundle locale 验证 zh-Hans plural 不可靠（本地化缓存/测试宿主 bundle），测试计划不可执行 | §7.1/§7.2：en plural 单测 + `-AppleLanguages (zh-Hans)` UI 测试验证中文 |

同样核对并确认**无需修改**的关键事实：

- Android 全量替换 + 非 CONNECTED/`clearSession`/`session.ended` 清空；`onJobStatus` 不受 `suppressMessages` 门控（`ConversationListenerAdapter.kt:290/303/308-316`）。
- Android badge 28dp/48dp、面板 85%/180dp/1s ticker/Gold、行内取消卡片与「不展示 summary」（`AgentJobStatusBadge.kt`、`AgentJobListPanel.kt`、`AgentJobCancelConfirm.kt`）。
- Android 取消成功不删行；404/not_found 与 409/conflict 静默、其他失败保留；请求 60s callTimeout（`ConversationViewModel.kt:767-824`、`MylifeApi.kt:57-62/136-152`）。
- BFF 路径 `/api/mylife/agent-jobs/cancel`、device 归属校验、`cancel_incomplete`/`agent_relay_unavailable`/`conflict` 语义（`services/bff/agent_jobs/{router,service}.py`、`services/tests/unit/bff/test_agent_jobs_cancel_api.py`）。
- iOS SDK `AgentJob` 字段与 `.jobStatus` 事件、Coordinator 忽略现状、`ServerBase` scheme/port 推导、History `sessionSwitched` 仅删除当前/清空所有触发、现有 `history_item_meta` plural 写法。

结论：严重问题已修订；§11 的 5 项已按推荐拍板（2026-10-21）。下一步按 §8 实施，并在本文件追加实施记录。

---

## 14. 实施记录（2026-10-21）

**状态**：已实现。iPhone/iPad 模拟器全量测试通过；真实 iPad 签名/安装与真实 server Agent Job 成功/取消链路待补（原因见 §14.4）。

### 14.1 变更文件

新增：

| 文件 | 内容 |
|---|---|
| `Core/Conversation/AgentJobStatusModel.swift` | 状态机、单飞取消、jobID 陈旧守卫、404/409 静默、panel 状态 |
| `Core/Conversation/AgentJobFormatting.swift` | started `HH:mm:ss`、elapsed、负值回退、99+、locale 数字 |
| `Core/Conversation/AgentJobCancelClient.swift` | BFF 取消 POST、60s request/resource、错误信封、host-scoped TLS、拒重定向 |
| `Features/Conversation/AgentJobStatusBadge.swift` | badge（minHeight 28、44pt 触控、plural a11y） |
| `Features/Conversation/AgentJobListPanel.swift` | 浮层面板、行/行内确认、内容高度测量、模态 a11y |
| `LivaChatAppTests/AgentJobStatusModelTests.swift` | 11 个状态机/格式化测试 |
| `LivaChatAppTests/AgentJobCancelClientTests.swift` | 8 个 client 契约/TLS host 测试 |

修改：`ConversationCoordinator`（`.jobStatus`、生命周期清空）、`ConversationRuntimeFactory`、`AppRootView`、`AppContainer`（model/client/UI fixture/dismiss）、`AppLaunchOptions`、`ConversationView`（badge/浮层/工具栏测量/a11y/focus）、`ServerProbe`（expectedHost/Port）、`L10n`（plural）、`AppTheme`（gold）、`Localizable.xcstrings`、`project.pbxproj`、Coordinator/AppContainer/UI 测试。

### 14.2 实现要点与设计偏差

- **确认卡片**：不用 `.overlay`（不参与布局），改为单一可访问卡片 + 使用隐藏 spacer 按卡片测量高度撑高行；避免此前“布局副本 + 可见副本”导致 XCUITest/读屏出现两个 `No/Yes`。
- **面板高度**：非 lazy VStack + 逐行 preference 汇总；面板总高与 ScrollView 可视高分算；iPhone/iPad 的 1 行/10 行、滚动、工具栏 24pt 间距均验证。
- **a11y**：面板加 `.accessibilityAddTraits(.isModal)`；`@AccessibilityFocusState` 打开聚焦 header、关闭回 badge。实测 SwiftUI `accessibilityHidden` 对容器子元素在 XCUITest 树中不保证移除（触摸由 scrim 阻断），故以 isModal 作为 VoiceOver 隔离机制；真机旁白手测待补。
- **Dynamic Type**：badge 改用 `minHeight: 28`（大字号可增高，计数不裁剪）；面板标题最多 2 行；AX XXXL 下打开确认卡片无崩溃/无截断关键操作。
- **iPad**：应用以兼容窗口居中运行；scrim 覆盖 ConversationView 内容区域（含面板上方 header/avatar 区），兼容窗口四周黑边不属于应用内容。UI 测试用面板上方内容区（`panel.minY - 20`）点击关闭，iPhone/iPad 均验证。
- **TLS**：`ProbeSessionDelegate` 增加可选 `expectedHost/Port`；取消 client 传入当前 ServerBase host/port，探针保持默认 nil 行为。
- **UI fixture**：`-uiTestJobStatusCount <n>` + `-uiTestJobCancelResult <success|success-no-snapshot|failure|not_found|conflict|incomplete>`；`-uiTestJobStatus` 等价 2 个。

### 14.3 验证结果

- **构建**：Debug 与 Release 模拟器构建均通过。
- **单元测试**（iPhone 15 Pro 模拟器，签名运行）：`213 tests / 1 skipped / 0 failures`。
- **全量 UI 测试**（产品最终版本）：`36 tests / 2 skipped / 0 failures`；跳过项为既有 `M2DeviceE2ETests` 与新增 `testRealServerAgentJobCancel` 两个手动真机 E2E（默认跳过）。之后仅调整外部点击测试坐标（改为面板上方）并重跑 Agent Job 11 例，iPhone/iPad 均 11/11。
- **Agent Job UI**：新增 11 例在 iPhone 15 Pro 与 iPad Pro 11-inch 模拟器均 **11/11** 通过：面板/取消成功、无快照保留行、失败重试、404 静默、取消中关面板重开、10 行高度/滚动、99+、相机 header badge、zh-Hans、Dynamic Type、最后一个 job 自动关面板。
- **截图**：面板、确认卡片、10 行封顶、99+、zh-Hans、AX XXXL 均导出检查；确认无卡片重复/主布局溢出。
- **真实 server 契约探针**：对 `https://localhost:8080/api/mylife/agent-jobs/cancel`（真实 `.env` 派生 wire token + 随机 device + 不存在 job）返回 `404 {"error":{"code":"not_found"}}`，与 client 静默语义一致；探针未打印 token。
- **真实 iPad**（iPadOS 17.7.5，GUI Terminal 签名）：Agent Job 新增 11 例 **11/11** 通过（含面板/取消、99+、相机、zh-Hans、Dynamic Type）。自动化终端本身无钥匙串交互权限（`errSecInternalComponent`），GUI Terminal 可正常签名。
- **真实 server Agent Job 取消**：正常模式 App 连接 `https://localhost:8080`，外放语音触发真实 Hermes job（`job_key=92a6c89e…`，running）；真机 UI 打开面板 → 取消 → 确认，DB 变为 `failed/interrupted`，iPad App 日志 `AgentJobCancel cancel status=200`，行从面板消失，`testRealServerAgentJobCancel` 通过。
  - 同一轮更早的真实天气 job 自然完成并 `delivered`，覆盖自然完成路径。
  - 真实取消可能快到 1s ticker 来不及渲染 spinner；真机 E2E 以“目标行消失”为成功断言，spinner/失败态由 fixture UI 测试覆盖。

### 14.4 待补验证

1. **真机 VoiceOver/Reduce Motion**：panel `.isModal`/焦点转移行为与动画关闭需真机旁白手测（模拟器 XCUITest 不能替代）。
2. **真实 server E2E 复跑**：`testRealServerAgentJobCancel` 默认跳过；在 Xcode scheme 的 Test Action 设置 `LIVA_REAL_JOB_E2E=1`（或临时放开 guard）后，用真实 Hermes/OpenClaw job 运行。本轮验证后已恢复 guard。

未改动 server/SDK/WS 协议。
