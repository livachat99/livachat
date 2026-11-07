# Android Agent Job 状态（badge / 面板 / 取消）

> **状态**：已实施（Android）
> **文档基线**：2026-10-24，对应当前 `apps/android/` 代码
> **范围**：`apps/android/LivaChatSDK/` 的 `job_status` 消费；`apps/android/LivaChatApp/` 的状态流、Conversation UI 与 BFF 取消客户端；server/BFF 契约作为客户端依赖一并记录
> **相关文档**：
> [`ios_agent_job_status.md`](ios_agent_job_status.md)（iOS 对等实现）、
> [`fd_job_status.md`](fd_job_status.md)（server 侧快照协议）
> **说明**：Agent Job **结果**气泡与全屏查看是另一个功能，见 [`android_agent_job_view.md`](android_agent_job_view.md)；本文只覆盖**运行中状态**与**取消**。

---

## 1. 功能与范围

### 1.1 功能

| # | 能力 | 行为 |
|---|---|---|
| 1 | **job_status 消费** | SDK 解码 s2c `job_status` 全量快照，映射为 `AgentJob`，经 listener 上抛 |
| 2 | **运行中 badge** | count > 0 时显示；显示 SmartToy + count（>99 显示 `99+`）；点击开/关面板 |
| 3 | **浮动任务面板** | 展示 summary、开始时间、实时运行时长；按 `startedAtMs` 升序；内容驱动高度、超出滚动 |
| 4 | **行内取消** | 每行 meta 行最右 × → 行内确认卡片（否/是）→ BFF REST 取消；不弹 AlertDialog |
| 5 | **取消状态机** | 单请求、防双击、陈旧响应保护、404/409 静默、其他失败可重试 |
| 6 | **生命周期自清** | 断连、`session.ended`、`clearSession`、列表变空时清列表/关面板/清确认态 |

### 1.2 非目标

- 历史 job（done/failed/delivered）展示——server 只推 running；
- job 详情、重跑、steer、删除记录（服务端无 remove 能力）；
- 通知栏/系统级展示；
- 本地乐观删除：列表唯一事实源是 `job_status` 快照；
- 取消后的会话「已取消」消息（不新增消息通道）；
- 修改 WS 协议 / SDK 事件 / `packages/api-schema`（SDK 的 `AgentJob` 与 `onJobStatus` 已存在）。

### 1.3 badge 入口位置（当前代码）

| `avatarVisible` | `videoVisibility` | badge 位置 |
|---|---|---|
| `true` | 非 `VISIBLE`（voice 模式） | avatar 右侧：`align(CenterStart)` + `offset(x = 120.dp + 8.dp)`，不参与布局测量，avatar 位置不动 |
| `true` | `VISIBLE`（相机分支） | **无 badge**：相机分支渲染 `VideoPreview`，不渲染 `ConversationVoiceContent`；顶栏隐藏态头像簇也只由 `avatarVisible=false` 触发 |
| `false` | 任意 | `TopAppBar` 内的隐藏态头像簇：avatar 按钮右侧固定 48dp slot 显示 badge |

`avatarVisible` 是用户偏好（头像显隐），与相机开关独立；相机分支只影响
`ConversationVoiceContent` 是否组合。相机模式且 `avatarVisible=true` 时当前没有 job status
入口；这是代码现状。voice/相机分支的差异来自 `ConversationVoiceContent` 是否组合，
而顶栏入口只由 `avatarVisible` 决定。

---

## 2. 数据链路

```text
server s2c job_status（全量快照）
  → OkHttp WS reader 线程
  → ConnectionManager.onMessage
    → SessionManager.handleInbound
        · logInbound（"job_status" 分支）
        · dispatchMessage（DTO → AgentJob，ClockOffset 换算）
  → ListenerDispatcher.onJobStatus
    → LivaChatListener.onJobStatus（默认空实现）
    → CompositeLivaChatListener（fan-out）
  → ConversationListenerAdapter._agentJobs: StateFlow<List<AgentJob>>
        · 全量替换
        · 非 CONNECTED 丢弃在途帧
        · 非 CONNECTED / session.ended / clearSession 清空
  → ConversationViewModel collect
  → ConversationUiState.agentJobs / showJobStatusPanel / agentJobCancel
  → ConversationScreen
        ├─ AgentJobStatusBadge（avatar 右侧或顶栏隐藏簇）
        └─ AgentJobListPanel（Popup）
              └─ 行 × → AgentJobCancelConfirm（行内覆盖）
                    └─ AgentJobRepository → MylifeApi → BFF 取消
```

取消不修改本地列表：BFF 取消 → JobStore 变化 → `JobStatusNotifier` 推新快照 → 列表自然收敛。

---

## 3. 协议与语义

### 3.1 `job_status` 帧

- 载荷：`jobs[]`，每项字段 `job_id / agent_name / summary / started_at_ms / running_time_ms`
  （`packages/api-schema/s2c/job-status.json`）；
- **全量替换**：每次帧携带该 device 当前**全部** running job；客户端整体替换，不做增量合并；
- **排序**：server 按 `started_at_ms` 升序；客户端展示层再按 `startedAtMs` 防御性排序一次；
- **空列表 = 清除信号**：仅当上一快照非空时发送；client 不能依赖周期性空帧维持「已清除」；
- **只推 running**：done/failed/delivered 不进入列表；
- `running_time_ms` 是发送时 server 计算的派生值，每次帧刷新；变化判据为
  `(job_id, summary, started_at_ms)` 指纹，`running_time_ms` 不参与变化判定；
- 推送节奏：变化即时；内容未变时每 `job_status_interval_s`（默认 15s）周期刷新；
  非空→空发一次清除帧；空且无记录时静默；总开关 `job_status_enabled = True`
  （`services/agent_relay/config.py`）；
- 仅在线设备接收帧；离线不发送、不更新内部指纹；上线首报会 force 评估当前快照
  （有 running job 时无视 15s 节流立即发送；空快照仍遵循「非空→空才发清除帧」，
  空且无记录时静默）；
- per-device 并发上限默认 10（`max_concurrent_jobs_per_device`）。

### 3.2 与结果消息的关联

`job_id` 是 relay 的 `job_key`；该 job 的结果 ai_text 的 `message_id` 恒为
`msg_agent_job_{job_id}`（SDK `AgentJob` KDoc 与协议 schema 均记载）。运行状态与结果气泡通过该键对齐；本文不涉及结果渲染。

`agent_name` 由服务端按 `JobRecord.agent_name` 透传；默认部署启用 hermes
（`default_adapters=["hermes"]`），存在 OpenClaw 时可为 `openclaw`。UI 不展示该字段。

### 3.3 未知消息类型

`MessageCodec.decode` 对整个 `decodeFromString` 包了 `try/catch → null`：未知 `type` 判别值被吞掉，
`SessionManager.handleInbound` 对 null 直接返回。旧版客户端收到 `job_status` 会静默忽略，不崩溃、不断连。

---

## 4. SDK 层

### 4.1 `AgentJob` 模型

文件：`apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/model/AgentJob.kt`

```kotlin
data class AgentJob(
    val jobId: String,
    val agentName: String,
    val summary: String,
    /** 已由 SDK 经 session clock offset 换算为设备本地时钟的 epoch ms。 */
    val startedAtMs: Long,
    /** 帧发送时 server 计算的已运行毫秒；时钟不可用时的回退值。 */
    val runningTimeMs: Long,
)
```

### 4.2 Listener 与分发

- `LivaChatListener.onJobStatus(jobs: List<AgentJob>)`：默认空实现；KDoc 声明与
  `onTranscription` 相同，在 **WS reader 线程**的 inbound dispatch 路径触发，写 `StateFlow` 安全；
- `ListenerDispatcher`（internal）与 `ListenerDispatcherImpl` 各有一行 `onJobStatus` 透传；
- `CompositeLivaChatListener` 对每个订阅者扇出；
- `ConversationListenerAdapter` 实现 `LivaChatListener`，是 App 侧唯一消费点。

### 4.3 `SessionManager` 映射

`logInbound` 的 `when (msg)` 是穷尽表达式，包含：

```kotlin
is ServerMessage.JobStatus -> "job_status"
```

`dispatchMessage` 的映射：

```kotlin
is ServerMessage.JobStatus -> listenerDispatcher.onJobStatus(
    msg.payload.jobs.map { item ->
        AgentJob(
            jobId = item.jobId,
            agentName = item.agentName,
            summary = item.summary,
            startedAtMs = item.startedAtMs - clockOffset.offsetMs(),
            runningTimeMs = item.runningTimeMs,
        )
    },
)
```

- `ClockOffset.update(serverTimeMs, localTimeMs)` 在 `session.ready` 时校准，
  `offsetMs = serverTimeMs - localTimeMs`；`startedAtMs - offsetMs` 即 server → local；
- 正常时序下 `session.ready` 早于 `session.started` 触发的首帧，offset 已就绪；
- offset 未校准（值为 0）时 `startedAtMs` 保持 server 时钟，App 端「负值回退 `runningTimeMs`」兜底，
  下一帧（≤15s）自动修正；
- SDK 不二次排序，保持 server 顺序。

---

## 5. App 状态层

### 5.1 `ConversationListenerAdapter`

文件：`ui/screens/conversation/ConversationListenerAdapter.kt`

```kotlin
private val _agentJobs = MutableStateFlow<List<AgentJob>>(emptyList())
val agentJobs: StateFlow<List<AgentJob>> = _agentJobs.asStateFlow()

@Volatile
private var lastConnectionState: ConnectionState = ConnectionState.DISCONNECTED

override fun onJobStatus(jobs: List<AgentJob>) {
    // 全量替换；不落库、不进消息流、不受 suppressMessages 门控
    if (lastConnectionState != ConnectionState.CONNECTED) return
    _agentJobs.value = jobs
}
```

清空路径：

| 触发 | 行为 |
|---|---|
| `onConnectionStateChanged(state != CONNECTED)` | `_agentJobs = emptyList()`（覆盖 relay 重启/首报丢失等 server 静默角） |
| `onSessionEnded(reason, finalSessionId)` | `_agentJobs = emptyList()` |
| `clearSession()` | `_agentJobs = emptyList()`（换会话/停会话） |
| `onJobStatus` 在非 CONNECTED 时到达 | 丢弃该帧（防断连清理后迟到帧复活幻影列表） |

线程契约：`onConnectionStateChanged` 在 Main（facade observer scope）写 `lastConnectionState`；
`onJobStatus` 在 WS reader 线程读。字段为 `@Volatile`，`MutableStateFlow` 线程安全。
正常时序下 `CONNECTED` 状态先于 `session.started` 触发的首帧；若首帧先于状态观察者到达
（跨线程竞态），该帧被丢弃，由下一次变化帧/周期帧（≤15s）重建。

### 5.2 `ConversationUiState` / `ConversationEvent`

```kotlin
// ConversationUiState
val agentJobs: List<AgentJob> = emptyList(),
val showJobStatusPanel: Boolean = false,
/** 行内取消确认态（× 触发）；null = 无确认。 */
val agentJobCancel: AgentJobCancelUiState? = null,

// ConversationEvent
data object ToggleJobStatusPanel : ConversationEvent()
data object DismissJobStatusPanel : ConversationEvent()
data class RequestAgentJobCancel(val job: AgentJob) : ConversationEvent()
data object DismissAgentJobCancel : ConversationEvent()
data object ConfirmAgentJobCancel : ConversationEvent()
```

`AgentJobCancelUiState`（定义在 `ui/components/AgentJobCancelConfirm.kt`）：

```kotlin
data class AgentJobCancelUiState(
    val job: AgentJob,
    val inFlight: Boolean = false,
    val error: String? = null,   // 本地化后的失败文案
)
```

### 5.3 `ConversationViewModel`

**列表 collect**：

```kotlin
listenerAdapter.agentJobs.collect { jobs ->
    val pending = _uiState.value.agentJobCancel
    val stillThere = pending != null && jobs.any { it.jobId == pending.job.jobId }
    _uiState.value = _uiState.value.copy(
        agentJobs = jobs,
        showJobStatusPanel = if (jobs.isEmpty()) false else _uiState.value.showJobStatusPanel,
        agentJobCancel = if (pending != null && !stillThere) null else pending,
    )
}
```

- 列表变空 → 面板自动关闭；
- 目标 job 从快照消失（取消成功/自然完成/断连/会话结束）→ 清确认态（含 inFlight）。

**面板开关**：

| 事件 | 行为 |
|---|---|
| `ToggleJobStatusPanel` | 仅当当前关闭且 `agentJobs` 非空时打开；关闭时未 inFlight 的确认态清掉，inFlight 保留 |
| `DismissJobStatusPanel` | 关闭；未 inFlight 的确认态清掉，inFlight 保留 |

**取消事件**：

| 事件 | 行为 |
|---|---|
| `RequestAgentJobCancel(job)` | 未 inFlight 时设为该 job（可切换目标）；inFlight 时忽略 |
| `DismissAgentJobCancel` | 未 inFlight 时清空确认态；inFlight 时忽略 |
| `ConfirmAgentJobCancel` | 调用 `confirmCancelAgentJob()` |

**`confirmCancelAgentJob()`**：

1. 取当前 target；inFlight 时直接返回（防双击）；
2. 置 `inFlight=true`、清 `error`；
3. `agentJobRepository.cancel(jobId)`；
4. 成功：仅当当前 target 仍是同一 `jobId` 才清空确认态；**不删本地行**，等快照收敛；
5. `MylifeApiException`：
   - `404 && code == "not_found"` 或 `409 && code == "conflict"` → 静默清空确认态（任务已结束/不存在）；
   - 其它（含 `503 cancel_incomplete` / `503 agent_relay_unavailable` / 旧 server 路由级
     `404/internal`）→ 保留确认态，`inFlight=false`，`error=取消失败，请重试`；
6. 其它异常（IO/超时）同样按「取消失败，请重试」；
7. `CancellationException` 原样抛出，不吞取消；
8. 所有回写都校验 `jobId`，陈旧响应不覆盖新 target。

---

## 6. UI

### 6.1 `AgentJobStatusBadge`

文件：`ui/components/AgentJobStatusBadge.kt`

- `AnimatedVisibility(visible = count > 0)`：进入 `fadeIn(200) + scaleIn(200)`，退出 `fadeOut(150)`；
- 视觉胶囊：28dp 高、`RoundedCornerShape(14.dp)`、`BrandIndigo.copy(alpha=0.7f)` 底、
  1.5dp `Surface` 描边、14dp `SmartToy` 图标（`TextPrimary`）、count 文本 `labelMedium/TextPrimary`；
- **触控区 48dp**：外层 `Box(heightIn(min=48.dp).clickable)`，视觉胶囊保持 28dp 且位置不变；
- count `> 99` 显示 `99+`（protocol per-device 上限 10，纯防御）；
- 图标 `contentDescription = pluralStringResource(conv_agent_jobs_running, count, count)`；
- 位置见 §1.3：voice 模式在 avatar 右侧（`offset(x=120.dp+8.dp)`，不参与布局测量）；
  `avatarVisible=false` 时在顶栏隐藏态头像簇：`Row(avatar 按钮 + 48dp badge slot)` 整体
  `offset(x=(48.dp+8.dp)/2 = 28.dp)`，保证 avatar 仍居中。

### 6.2 `AgentJobListPanel`

文件：`ui/components/AgentJobListPanel.kt`

```kotlin
fun AgentJobListPanel(
    jobs: List<AgentJob>,
    onDismiss: () -> Unit,
    cancelState: AgentJobCancelUiState?,
    onRequestCancel: (AgentJob) -> Unit,
    onDismissCancel: () -> Unit,
    onConfirmCancel: () -> Unit,
    maxHeight: Dp,
)
```

- **Popup**：`alignment = TopCenter`，`offset y = AgentJobPanelTopOffset (180.dp)`，
  `properties = PopupProperties(focusable = true)` → 点外部/返回键自动 `onDismissRequest`；
- 宽度 `fillMaxWidth(0.85f)`；`heightIn(max = maxHeight)`；内容驱动高度，超出后 `LazyColumn` 滚动；
- 外观：`Surface` 不透明底、`RoundedCornerShape(16.dp)`、1dp `Gold` 边框、
  `Elevation.Level3`（8dp）阴影；内容 padding `Spacing.md`（16dp）；
- 头部：18dp `SmartToy`（`BrandIndigo`）+ `pluralStringResource(conv_agent_jobs_title, jobs.size, jobs.size)`（`titleSmall/TextPrimary`）；头部在 `LazyColumn` 之外，滚动时固定；
- 列表：`jobs.sortedBy { it.startedAtMs }`，`key = { it.jobId }`；`testTag("agent-job-list")`；
- **面板级单 ticker**：`LaunchedEffect(Unit) { while (true) { delay(1_000); nowMs = System.currentTimeMillis() } }`；
  所有行共享同一 `nowMs`，面板关闭即退出；
- `AgentJobPanelTopOffset` 与 `ConversationScreen` 的 `maxHeight` 计算共用，避免两处漂移。

### 6.3 行布局

```text
┌──────────────────────────────────────────────┐
│ summary（bodyLarge / TextPrimary，最多 2 行） │
│ 开始于 HH:mm:ss   1m 23s              [×]    │
└──────────────────────────────────────────────┘
```

- summary：`bodyLarge`、`TextPrimary`、`maxLines=2`、`TextOverflow.Ellipsis`；
- meta 行：内层 `Row(weight(1f))` 放 started + elapsed；`started` `bodySmall/TextSecondary`、
  `maxLines=1`、`Ellipsis`、`weight(1f, fill=false)`；中间 `Spacer(8dp)`；`elapsed`
  `labelLarge/Success`、`maxLines=1`；最右 `IconButton`（48dp 触控、16dp `Close`、
  `TextSecondary`、`contentDescription=conv_agent_job_cancel_action`）；
- `started`：`HH:mm:ss`，设备本地时区；`elapsed`：`Xs` / `Xm Ys` / `Xh Ym`；
  两者数字均经 `localizeDigits`（阿拉伯语环境阿拉伯-印度数字），单位保持 `h/m/s`；
- 运行时长：`nowMs - job.startedAtMs`；若为负（时钟未校准/偏差）回退 `job.runningTimeMs`；
- 行无 Divider，垂直 padding `Spacing.sm`（8dp）；`inFlight` 时所有 × `enabled=false`。

### 6.4 行内取消确认

点击某行 × → 该行内容变暗并被遮罩覆盖，中央显示确认卡片：

```text
┌─ 该行原内容（变暗、语义隐藏）───────────────┐
│           取消 Agent任务？                  │  ← bodyMedium，最多 2 行，居中
│ 取消失败，请重试                            │  ← 可选，bodySmall/error，最多 2 行，左对齐
│               [否]  [是]                    │  ← 操作行居中；inFlight 时替换为进度
└─────────────────────────────────────────────┘
```

- 判定：`cancelState?.job?.jobId == job.jobId`；
- 遮罩：`matchParentSize()`、`Background.copy(alpha=0.82f)`；
  `clickable(indication=null) {}` 吞掉点击，防穿透到被遮罩的 ×；
  `clearAndSetSemantics {}`（读屏不重复原行）；
- 原行在确认态下也 `clearAndSetSemantics {}`；
- 卡片：`Surface`、`RoundedCornerShape(12.dp)`、1dp `Gold` 边框、`Elevation.Level2`；
  调用方 `align(Center) + fillMaxWidth() + padding(horizontal=Spacing.xs)`；
- 问题：`conv_agent_job_cancel_question`，`bodyMedium/TextPrimary`、`maxLines=2`、居中；
- 失败：`state.error`，`bodySmall/colorScheme.error`、`maxLines=2`；未设置 `textAlign`，按 Column 默认左对齐；
- 操作行：居中；`inFlight` 时 14dp `CircularProgressIndicator`（`BrandIndigo`，2dp 描边）+
  `conv_agent_job_cancel_in_progress`（`labelMedium/TextSecondary`）；否则「否」「是」两个
  `TextButton`（紧凑 padding，否在左、是在右）；
- 卡片**不展示目标 summary**（与聊天内的 Agent 气泡重复）；
- 卡片随目标 LazyColumn item 撑高/滚动；滚出视口不丢状态；
- 测试 tag：`agent-job-cancel-scrim`、`agent-job-cancel-confirm`、`agent-job-cancel-action-{jobId}`。

### 6.5 面板高度与滚动

`ConversationScreen` 根部 `BoxWithConstraints` 取 `maxHeight`；底部工具栏通过
`BottomToolbar(modifier = Modifier.onSizeChanged { toolbarHeightPx = it.height })` 实测：

```kotlin
val bottomReserve = if (toolbarHeightPx == 0) 140.dp else toolbarHeightDp + Spacing.lg
val panelMaxHeight = (maxHeight - AgentJobPanelTopOffset - bottomReserve).coerceAtLeast(0.dp)
```

- 工具栏未测量（首帧/重建）时用 **140dp 保守 reserve**（工具栏≈104dp + 24dp 间距 + 字体余量），
  避免小屏/横屏压住工具栏；
- 面板底边与工具栏之间保留 `Spacing.lg`（24dp）；
- 内容高度小于上限时：面板高度=内容高度；超过 `panelMaxHeight` 后封顶并由列表滚动；
- Popup 是独立窗口，Compose `boundsInRoot` 不能跨窗口比较「面板底边 vs 工具栏顶边」；
  该间距需 UiAutomator `getBoundsInScreen` 或真机截图验证。

### 6.6 文案与视觉

| 位置 | key | 默认英文 | 中文 |
|---|---|---|---|
| badge a11y | `conv_agent_jobs_running`（plural） | one: `%d agent task running`；other: `%d agent tasks running` | `Agent任务，%d 个运行中` |
| 面板标题 | `conv_agent_jobs_title`（plural） | one: `Agent task (%d)`；other: `Agent tasks (%d)` | `Agent任务 (%d)` |
| started | `conv_agent_job_started` | `Started at %s` | `开始于 %s` |
| × a11y | `conv_agent_job_cancel_action` | `Cancel Agent task` | `取消 Agent任务` |
| 确认问题 | `conv_agent_job_cancel_question` | `Cancel Agent task?` | `取消 Agent任务？` |
| 是/否 | `conv_agent_job_cancel_yes/no` | `Yes` / `No` | `是` / `否` |
| 取消中 | `conv_agent_job_cancel_in_progress` | `Cancelling…` | `正在取消…` |
| 取消失败 | `conv_agent_job_cancel_failed` | `Cancel failed. Try again` | `取消失败，请重试` |

- 17 个 `strings.xml`（默认 + 16 locale）均包含上述 key；
- 颜色：badge `BrandIndigo` 0.7 + `Surface` 描边；面板 `Surface` + `Gold`（#FFD700）边框；
  elapsed `Success`（#10B981）；× `TextSecondary`；错误 `colorScheme.error`；
- 间距：`Spacing` xs/sm/md/lg = 4/8/16/24dp；圆角：badge 14dp、面板 16dp、确认卡片 12dp。

---

## 7. 取消链路

### 7.1 客户端请求

```http
POST /api/mylife/agent-jobs/cancel?token=<wire_token>&device_id=<device_id>
Content-Type: application/json; charset=utf-8

{"job_id": "<job_key>"}
```

- `MylifeApi.cancelAgentJob(jobId)`：`baseUrl` 已含 `/api/mylife`，`addPathSegments("agent-jobs/cancel")`；
  token/device_id 走 query（对齐 `events/remove`），取值来自 `LivaChatConfig`（与 WS 握手同源）；
- 专用 `cancelClient`：read/write/call timeout 均 **60s**（adapter abort 30s + 5s 宽限 + 重连/resume 余量）；
- 请求 DTO `CancelAgentJobRequestDto(jobId)`；响应 DTO
  `CancelAgentJobResponseDto(schemaVersion, jobId, status)`；
- `AgentJobRepository.cancel(jobId)` 是薄封装，VM 不直接依赖 remote API；
- 非 2xx 统一抛 `MylifeApiException(status, code, message)`；无错误信封/无 `code` 时
  `code="internal"`（旧 server 路由级 404 走此路径，客户端按失败提示，不静默）；
- 响应 `status`（`cancelled`/`completed`/`failed`）仅作服务端结果说明；客户端**不依赖它做 UI**，
  确认态关闭与列表收敛都以 `job_status` 快照为准。

### 7.2 BFF 语义

| 场景 | HTTP | code | 客户端行为 |
|---|---|---|---|
| 成功：取消生效 | 200 | — | `status="cancelled"`；清确认态，等快照移除行 |
| 取消与自然完成竞态：done/delivered | 200 | — | `status="completed"`；结果仍可能已投递 |
| 其它终态 | 200 | — | `status="failed"` |
| token 错误 | 401 | `unauthorized` | 失败提示 |
| 缺 `device_id` / body 非法 | 400 | `bad_request` | 失败提示 |
| job 不存在或属于其它 device | 404 | `not_found` | **静默清确认态**（不泄漏存在性） |
| 请求开始时 job 已非 active | 409 | `conflict` | **静默清确认态** |
| adapter 不支持 cancel | 503 | `agent_relay_unavailable` | 失败提示、可重试 |
| adapter 返回后 job 仍 active | 503 | `cancel_incomplete` | 失败提示、可重试 |
| JobStore 读写失败 / adapter 抛异常且仍 active | 502 | `upstream_unavailable` | 失败提示、可重试 |

BFF 实现要点（`services/bff/agent_jobs/{router,service}.py`）：

- `device_id` 必填（缺失 400）；job 归属按 `JobRecord.device_id` 比对；
- 只有 `requested/running` 可取消（`ACTIVE_STATUSES`）；
- adapter 的布尔返回值不作为成功判据：取消后**重读 JobRecord**，仍 active → `cancel_incomplete`；
- adapter 抛异常但已收敛（如 `mark_failed` 成功后清理异常）→ 仍按 200 返回终态；
- `status` 映射：done/delivered → `completed`；failed 且 `fail_reason ∈ {cancelled, interrupted, aborted}`
  → `cancelled`；其它 → `failed`；JobRecord 已不存在 → `cancelled`。

### 7.3 取消与结果投递的竞态

取消**生效**时（`failed(interrupted/cancelled/aborted)`）不投递结果；但 job 可能在 interrupt 生效前
自然 `done`，结果仍会被正常投递。UI 不承诺「取消后一定没有结果」；确认卡片也不承载该说明。

job-started 的 summary 是普通 `ai_text`，取消后仍留在会话里；本功能不新增「已取消」消息，
用户感知为「任务从面板消失」。

---

## 8. 状态机

```text
                  job_status(non-empty)                    job_status([]) / disconnect /
AgentJob jobs ─────────────────────────▶ present ──▶ sessionEnded / clearSession ──▶ empty
   │  showJobStatusPanel (toggle/dismiss; empty -> false)
   │  agentJobCancel: nil | (job, inFlight, error)
   │
   └─ confirm(job):
        inFlight=true ── 200 success ──▶ nil（等快照移除行）
                      ├─ 404/not_found / 409/conflict ──▶ nil
                      ├─ 其它失败 ──▶ (job, false, error)
                      └─ 目标已消失/已切换（陈旧响应）──▶ ignore
```

| 来源状态 | 事件 | 目标状态 |
|---|---|---|
| jobs 空 | 收到非空快照 | jobs 非空；badge 出现；面板保持关闭 |
| jobs 非空 | 收到空快照 | jobs 空；badge 消失；面板关闭；确认态清空 |
| 任意 | 非 CONNECTED / session.ended / clearSession | jobs 空、面板关、确认态清 |
| 面板关 | 点 badge（jobs 非空） | 面板开 |
| 面板开 | 点 badge / 外点 / 返回 | 面板关；未 inFlight 确认态清，inFlight 保留 |
| 无确认态 | 行 × | 该 job 进入确认态 |
| 确认态（未 inFlight） | 另一行 × | 切换目标 |
| 确认态（inFlight） | 另一行 × / 否 / 是 | 忽略（× 置灰、按钮被进度替代） |
| 确认态（未 inFlight） | 否 | 清确认态 |
| 确认态 | 是 | inFlight=true；成功后清；失败保留并可重试 |
| 任意 | 目标 job 从快照消失 | 清确认态（含 inFlight） |

---

## 9. 边界与失败语义

| 场景 | 行为 |
|---|---|
| 收到非空 `job_status` | 整体替换；badge/count/面板内容刷新（LazyColumn 按 `jobId` key 局部重组） |
| 收到空列表 | 列表清空、badge 消失、面板自动关闭、确认态清空 |
| 15s 周期帧内容未变 | 整体替换为相同内容；`startedAtMs` 不变，本地续算无跳变 |
| server 静默（无 running job 且无记录） | 无帧；本地初始为空，状态一致 |
| 断连（含网络抖动） | adapter 立即清空 → badge 消失、面板关闭；重连后首报重建（短暂闪烁可接受） |
| relay 重启 + 离线期间 job 全部结束 | server 可能静默；client 已因断连清空，无幻影残留 |
| `session.ended` | 清空（不依赖连接关闭）；若 WS 仍在线且 device 仍有 running job，后续 `job_status` 帧会重新填充（device 级状态） |
| `clearSession()`（换会话/停会话） | 清空 |
| `suppressMessages=true`（Settings 清空历史） | job status 不受门控，正常刷新 |
| `job_status` 先于 CONNECTED 状态/`session.ready` 到达 | 帧被丢弃；下一次变化帧/周期帧（≤15s）重建 |
| 旧 SDK 收到 `job_status` | `MessageCodec` 吞掉未知 type，静默忽略 |
| 时钟未校准 / `now - startedAtMs < 0` | 回退 `job.runningTimeMs` |
| 面板打开时新 job 到达 | 按 `startedAtMs` 升序插入；badge count 刷新 |
| 面板打开时列表清空/断连 | 面板自动关闭，无空态 UI |
| 面板打开时点外部/返回 | 关闭；未 inFlight 确认态清，inFlight 保留 |
| 取消期间关面板再打开 | inFlight 状态保留，目标仍在则继续显示「正在取消…」；请求不中断 |
| 取消期间任务自然完成 | 目标从快照消失 → 清确认态（含 inFlight）；结果可能已投递 |
| 取消 404/not_found、409/conflict | 静默清确认态，交给快照 |
| 取消 503 `cancel_incomplete` / `agent_relay_unavailable` / 502 | 保留确认态，显示失败，可重试 |
| 旧 server 无该路由（404 无错误信封） | `code="internal"` → 显示失败，不静默（避免伪装成功） |
| HTTP 超时（60s） | 显示失败；服务端取消可能仍在进行，最终由快照移除行，VM 的「job 消失清态」自愈 |
| 同一时刻多个 job | 只允许一个确认态/一个在飞请求；未 inFlight 可切换，inFlight 其它 × 置灰且 VM 忽略 |
| 取消最后一个 job | 快照变空 → 面板自动关闭 |
| summary 超长 | 行内 `maxLines=2` + Ellipsis，行高有界 |
| count > 99 | badge 显示 `99+`（per-device 上限 10，纯防御） |
| 相机模式 + `avatarVisible=true` | 当前无 badge 入口（voice content 不组合、顶栏隐藏簇不触发）；job 列表仍照常更新 |
| 相机模式 + `avatarVisible=false` | 顶栏隐藏态头像簇显示 badge；可打开面板 |
| 面板在隐藏头像模式 | Popup 仍固定在 `AgentJobPanelTopOffset=180dp`，不跟随顶栏 badge 位置 |
| 进程被杀/重建 | 列表为设备级瞬态、不落库，进程内状态丢失；重连首报/变化帧重建 |
| App 切后台 | 不额外清空；若 WS 掉线，`onConnectionStateChanged(!= CONNECTED)` 按连接规则清空 |
| 横屏/极小窗口 | `panelMaxHeight` 随根高度收缩，下限 0；列表滚动；不专门优化横屏布局 |

---

## 10. 测试

### 10.1 SDK

| 文件 | 用例 |
|---|---|
| `LivaChatSDK/src/test/.../SessionManagerTest.kt` | job_status 五字段映射；空列表透传；server 顺序保持；`startedAtMs` 经 ClockOffset 换算；`logInbound` 对 JobStatus 不抛异常 |

### 10.2 App 单测

| 文件 | 用例 |
|---|---|
| `ConversationListenerAdapterTest` | 全量替换；空列表清空；`suppressMessages=true` 仍更新；非 CONNECTED 丢弃；重连后恢复接收；`clearSession` 清空；`onSessionEnded` 清空；非 CONNECTED 清空；CONNECTED 保留 |
| `ConversationViewModelJobStatusTest` | agentJobs 进 UiState；面板开关/空列表不展开/清空自动关；请求确认态；空闲切换目标；否清态；成功清态并调用 repository；双击只发一次；503 保留错误；404/not_found、409/conflict 静默；路由级 404 显示错误；目标消失清态；面板关闭清空闲确认态/inFlight 保留；inFlight 时请求被忽略 |
| `MylifeApiTest` | cancel POST body/token/device_id 且解析 status；503 `cancel_incomplete` 错误信封 |
| `ConversationViewModelTest` | UiState 默认值（`agentJobCancel=null`）；sealed 事件枚举包含面板/取消事件 |

### 10.3 androidTest

`AgentJobStatusUiTest`（组件级，`createComposeRule`）：

- badge：count>0 可见、count=0 不可见、`99+` 钳制、48dp 触控高度大于 28dp 视觉胶囊；
- 面板：点 badge 打开并显示行；点外部关闭；返回键关闭；标题/行内容正确；
- 行内确认：点 × 出现确认卡片、点「否」收起；点「是」进入进度态且按钮消失；
- 失败态 inline 文案；
- 问题与操作行分行、居中；
- 遮罩吞点击（不穿透到 ×）；
- 面板高度随行数增长、到 `maxHeight` 封顶；列表可滚到最后一行。

### 10.4 手工/真机

- 面板底边与底部工具栏 **24dp** 间距：Popup 独立窗口，需 UiAutomator `getBoundsInScreen` 或真机截图；
- 横屏/极小窗口下面板可用性；
- 真实 server：job 触发 → badge/面板 → 取消 → 快照移除行；断连自清 → 重连首报重建；
- 相机 + `avatarVisible=true` 无 badge、`avatarVisible=false` 顶栏 badge 的入口矩阵目检。

---

## 11. 文件索引

### 11.1 生产代码

| 层 | 文件 | 职责 |
|---|---|---|
| SDK | `LivaChatSDK/.../model/AgentJob.kt` | 运行中 job 模型 |
| SDK | `LivaChatSDK/.../LivaChatListener.kt` | `onJobStatus` 回调（默认空实现） |
| SDK | `LivaChatSDK/.../internal/session/ListenerDispatcher.kt` | internal 分发接口 |
| SDK | `LivaChatSDK/.../internal/util/ListenerDispatcherImpl.kt` | 桥接到 public listener |
| SDK | `LivaChatSDK/.../internal/session/SessionManager.kt` | `logInbound` / `dispatchMessage` / ClockOffset 换算 |
| SDK | `LivaChatSDK/.../internal/session/ClockOffset.kt` | server ↔ local 时钟偏移 |
| App | `media/CompositeLivaChatListener.kt` | listener 扇出 |
| App | `ui/screens/conversation/ConversationListenerAdapter.kt` | `agentJobs` StateFlow、清空与丢帧规则 |
| App | `ui/screens/conversation/ConversationUiState.kt` | `agentJobs` / `showJobStatusPanel` / `agentJobCancel` |
| App | `ui/screens/conversation/ConversationEvent.kt` | 面板/取消事件 |
| App | `ui/screens/conversation/ConversationViewModel.kt` | collect、面板开关、取消状态机 |
| App | `ui/screens/conversation/ConversationScreen.kt` | badge 两处接线、面板 `maxHeight` |
| App | `ui/components/AgentJobStatusBadge.kt` | badge 视觉/触控/动画 |
| App | `ui/components/AgentJobListPanel.kt` | Popup 面板、行、ticker、时间格式化 |
| App | `ui/components/AgentJobCancelConfirm.kt` | 行内确认卡片 + `AgentJobCancelUiState` |
| App | `data/remote/mylife/MylifeApi.kt` | `cancelAgentJob` + 60s `cancelClient` |
| App | `data/remote/mylife/dto/MylifeDtos.kt` | 取消请求/响应 DTO |
| App | `data/repository/AgentJobRepository.kt` | 取消薄封装 |
| App | `res/values*/strings.xml` | 2 个 plural + `started` + 6 个取消文案（16 locale） |

### 11.2 测试

`LivaChatSDK/src/test/.../SessionManagerTest.kt`；
`LivaChatApp/src/test/.../ConversationListenerAdapterTest.kt`、
`ConversationViewModelJobStatusTest.kt`、`ConversationViewModelTest.kt`、`MylifeApiTest.kt`；
`LivaChatApp/src/androidTest/.../AgentJobStatusUiTest.kt`。

---

## 12. 关键取舍与已知限制

| # | 取舍/限制 | 说明 |
|---|---|---|
| 1 | **相机 + `avatarVisible=true` 无 badge** | 相机分支不组合 `ConversationVoiceContent`，顶栏入口只由 `avatarVisible=false` 触发；当前代码没有在 `VideoPreview` 上挂 badge。若产品需要，需在相机分支新增入口 |
| 2 | **面板锚点固定 180dp** | Popup `TopCenter + 180dp` 是为 voice avatar 区域设计的；隐藏头像模式下 badge 在顶栏，面板仍出现在 180dp，不跟随 badge |
| 3 | **取消无本地乐观删除** | 200 成功只清确认态；行由 `job_status` 快照移除。正常情况下取消触发变化帧立即到达；帧延迟/丢失时等周期帧（≤15s）或重连首报 |
| 4 | **取消成功无 toast** | 列表消失即反馈；不新增提示通道 |
| 5 | **取消与自然完成竞态** | 结果仍可能送达；UI/API 不承诺「取消后一定无结果」 |
| 6 | **单确认态/单在飞请求** | 同一时刻只允许一个；inFlight 时其它 × 置灰、VM 忽略 |
| 7 | **inFlight 期间面板可关闭** | 关闭只保留状态、不取消请求；重开面板（job 仍在）继续显示进度 |
| 8 | **失败保留确认态** | 除 404/not_found、409/conflict 外均显示「取消失败，请重试」，不静默清态（含旧 server 路由级 404） |
| 9 | **48dp × 触控区抬高行高** | meta 行从视觉 ~20dp 变为 48dp 触控下限；面板靠内容增长 + 滚动承接，小屏/大字体下可见条数减少 |
| 10 | **面板高度 140dp 首帧 reserve** | 工具栏未测量时的保守值；测量后按真实高度 + 24dp 重算；极端字体缩放下仍以滚动兜底 |
| 11 | **Popup 底边间距需窗口级验证** | Compose `boundsInRoot` 不能跨 Popup/主窗口比较；组件级只验证高度/滚动 |
| 12 | **job status 是 device 级瞬态** | 不落库、不进消息流、不受 `suppressMessages`；进程死亡丢失，重连快照重建 |
| 13 | **`99+` 是防御性钳制** | per-device 并发上限默认 10，正常不会触发 |
| 14 | **不展示 `agent_name`** | 默认部署为 hermes；多 agent（openclaw 等）也仅透传不展示，演进时再增加来源标签 |
| 15 | **不新增「已取消」会话消息** | job-started 的 summary 保留；取消生效时无结果消息（自然完成竞态除外） |
| 16 | **旧 server 无取消路由时显示失败** | 无信封 404 → `code="internal"` → 失败提示，不伪装成已取消 |
| 17 | **时间格式与本地化** | `HH:mm:ss`（含秒）匹配 job 生命周期；`Xs/Xm Ys/Xh Ym` 更易扫读；数字按 locale 本地化 |
| 18 | **不专门优化横屏/极小窗口** | `panelMaxHeight` 收缩到 0 以上并滚动；横屏布局未单独设计 |
| 19 | **`clearSession`/`session.ended` 清空是会话级动作，job 状态是 device 级** | 清空后若连接仍在线且 device 仍有 running job，下一次 `job_status` 帧（变化或 ≤15s 周期）会重新填充；已断连则等重连首报。这是已知的语义差，换来「会话结束不残留旧 UI」 |
