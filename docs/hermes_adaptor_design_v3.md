# Hermes Adapter 交互设计 v3 — Relay 自建 Job 会话（Job Session 方案）

> **状态**：已实施（P1–P6 完成；v3.2 清理 `prompt.background` legacy 载体，session 为唯一 job 载体；v3.3 对抗式 review 加固：dispatch/recovery 边界与 shutdown 隔离）；P0 剩余项见 §9
> **日期**：2026-10-17（v3.1 同日评审修订：D10 容量算术、D17 恢复复查、D14 close 后 resume 探针、§5.7 测试补漏；复审补充：D17 复查窗口 10s、cap 作用域源码确认 per-HOME、close 进程清理语义、§8 默认值修正、Q10 close→resume 结论；**产品确认 job 不需要常驻进程，终态 close 清理为预期行为**。v3.2：**删除 background carrier 的代码/配置/测试/文档，session 成为唯一路径**；v3.3：review 修复 WS 未就绪/缺 stored id/跨重连 recheck/存储异常/shutdown/无连接 cancel 等边界；真机确认 steer `queued` 契约与 interrupt transcript 文案；v3.4：dispatch 取消落 `dispatch_aborted`、stale job resume 后清理、error 事件存储异常 arm recheck、recovery text 分支对称校验、注明 recheck 10s 语义）
> **前置文档**：
> - [`docs/hermes_websocket_api.md`](./hermes_websocket_api.md)（协议调研；§9 为 v0.20.2 真机探针，§10 为 v0.22.2+canary 静态差异）
> - [`docs/hermes_adaptor_design_v2.md`](./hermes_adaptor_design_v2.md)（v2：`prompt.background` 方案，已由本稿取代）
> - [`docs/fd_agent_relay_service_v2.md`](./fd_agent_relay_service_v2.md)（JobStore / Deliverer / 48h 补投 / 看门狗）
> - [`docs/openclaw_adaptor_design.md`](./openclaw_adaptor_design.md)（结构参照；B 方案：relay 自建 job 会话）
> **范围**：`services/agent_relay/`（`adapters/hermes.py` 为主 + `config.py` / `job_store*.py` 小改 + `prompts/` 复用 + 测试），不改 Hermes 源码、不改 `PushMessage` wire、不改 Android/客户端。
> **一句话**：**relay 为每个 job 创建一个独立 Hermes 会话**（`session.create` + `prompt.submit`），用 `job_key ↔ (live sid, stored sid)` 关联；`message.complete` 收终稿，`session.interrupt` / `session.steer` / `session.active_list` 提供 cancel / 追加 / 状态。`prompt.background` 载体及其配置/代码/测试已删除，session 是唯一 job 载体（D12）。

---

## 0. 关键决策（待评审）

| # | 决策点 | 选择 | 依据 |
|---|--------|------|------|
| D1 | 连接模型 | **保持 per-device 一条 WS + 一个 device session**；同一 WS 上再承载 N 个 job session | v2 已实现 `_DeviceConnection`；实测同一 WS 可并行多个 session，事件按 `session_id` 分发（`docs/hermes_websocket_api.md` §9.3.1；本稿 §3.3 探针） |
| D2 | job 载体 | **独立 job 会话**（每 job 一个 Hermes session），唯一 job 载体 | 协议提供确定性 create/submit/interrupt/steer/status（§9.3）；native subagent 无 spawn RPC（§9.2） |
| D3 | 关联键 | `JobRecord.job_key` ↔ 内存 `(live_sid, stored_sid)`；`agent_jobs.hermes_task_id` 写 **`session:<stored_session_id>`** | 复用现有列/索引，**零 schema 迁移**；stored id 是 `session.resume` 的稳定键 |
| D4 | 完成检测 | job 会话的 `message.complete`（按 `params.session_id` 路由） | v0.20.2 实测 `message.complete` 携带 `status`/`text`/`usage`；job 结果全文不受 `subagent.complete` 的 500 字符截断 |
| D5 | 取消 | `session.interrupt {session_id}`；可选 `process.list` → `process.kill` 清理后台进程 | 实测 interrupt 使 turn `status:"interrupted"`、`text:null`；后台进程默认保留（§9.3.3，本稿 §3.3 探针） |
| D6 | 追加 prompt | `session.steer {session_id, text}`（不打断）；**v3 job 是单 turn**，不使用 `queued:true` 制造第二 turn | 实测 steer 在下一个工具边界注入；`queued:true` 会引入第二 turn，与“job 一个终态”冲突 |
| D7 | 状态 | **不新增状态 RPC**：JobStore + `JobStatusNotifier` 仍是 wire 状态源；`session.active_list` / `session.status` 仅用于 adapter 恢复判定 | `fd_job_status.md` 已覆盖 running 快照；`active_list` 的 `working/idle` 实测可用 |
| D8 | 恢复 | relay 启动/WS 重连时，对 active job 的 stored id 做 `session.resume`；running 继续等事件，已完成从 `resume.messages` 读回结果补投 | 实测：运行中会话 22s 离线后 resume 同 sid 并在新连接收到 complete；完成后离线 25s，resume 返回新 sid + 完整 messages（含终稿） |
| D9 | 投递 | 复用 `JobResultDeliverer` / `PushPublisher`，确定性 `message_id=msg_agent_job_{job_key}` | 现有 v2 收口；离线补投 / 48h 窗口 / per-device 锁全部不变 |
| D10 | 配额 | per-device / global in-flight 上限不变；新增 Hermes 侧 `4090`（active session cap）失败语义 | `session.create` 本身不占槽；job 的首个 `prompt.submit` 会 claim active session 槽（`_ensure_active_session_slot`）。cap 是 **per-HERMES_HOME 的文件注册表**（`~/.hermes/runtime/active_sessions.json`，跨 CLI/desktop/gateway 进程共享），不是 per-WS；容量算术见 §4.3.2 注 / §5.1，错误码 4090 已由源码确认（§9-Q2） |
| D11 | 交互兜底 | 复用 approval / clarify / sudo / secret autorespond 矩阵（事件按 job sid） | `_event_session_id` 已支持任意 sid；job 会话的审批与 device 会话同构 |
| D12 | 单一载体 | **session only**：`job_carrier` 配置、`prompt.background` 分发分支、`background.complete` 处理、`_mark_session_lost` 及相应测试已全部删除 | 产品确认 background job 方式不再需要；保留双路径会增加两套恢复/清理语义的维护与误判面 |
| D13 | 任务书 | `prompts/job_task.md`（原名 `background_task.md`），不 seed system mission | 模板自包含（任务 + chatlog + 时区 + lvdata + webroot + 输出要求）；旧 `prompt.background` 本来也不继承父会话历史 |
| D14 | 清理 | job 终态后 `session.close` 并清理内存映射；transcript 保留；**不保留 job 启动的后台进程** | 释放活跃会话槽；v0.20.2 `AIAgent.close()` 会 kill 该 session/task 的 tracked 后台进程，这是**预期清理**（产品已确认目前 job 不需要常驻 server/watcher，见 §2/§7）；实测显式 close 后仍可按 stored id `session.resume` 并返回 messages（`/tmp/hermes_probe_session.log`、`/tmp/hermes_probe_omit.log`），审计/补取声明成立 |
| D15 | JobStore 改动 | 新增 `bind_external_ref(job_key, ref)`（requested 状态下写 `hermes_task_id`，不改状态） | 在 `prompt.submit` 前持久化恢复键，消除“create 成功但 relay 崩溃”窗口；Redis/SQLite 双实现，无 DDL |
| D16 | 版本要求 | **不升级**：v0.20.2 即可；v0.22.2+ 的 `session.events.since` 作为未来增强，不依赖 | §10 的差异均为可选增益；v0.20.2 的 `session.resume` + messages 已满足恢复 |
| D17 | 恢复兜底 | resume 判定 working 后 arm 一次性 `job_resume_recheck_s`（默认 10s）复查（`active_list` + messages 重判）；仍 working 则说明 transport 已恢复，不再重查 | 缝隙只存在于 resume 快照与 transport 重绑定之间（毫秒级）；10s 一次性复查足够，避免把极小概率竞态的结果延迟到 600s/48h（§4.4.3 / §4.4.4） |

---

## 1. 背景与问题

### 1.1 v2 的 job 路径（历史，已删除）

v3 实施前 `services/agent_relay/adapters/hermes.py` 的旧路径：

```
主 agent 契约 job_request
  → JobStore.create(status=requested)
  → prompt.background {session_id: device_sid, text: 任务书}
  → task_id（bg_xxxx）→ JobStore.mark_running(hermes_task_id=task_id)
  → WS 事件 background.complete {task_id, text}
  → _handle_background_complete：mark_done → JobResultDeliverer → ai_text
```

该路径（含 `job_carrier` 开关、`_handle_background_complete`、`_mark_session_lost`、mock 的 `prompt.background` 支持与相关测试）已随 v3.2 全部删除；本节仅保留为设计背景。

### 1.2 已确认的能力缺口（v0.20.2 真机）

`docs/hermes_websocket_api.md` §9.1 已证实 `prompt.background`：

| 需求 | 现状 |
|---|---|
| cancel | `session.interrupt` 对后台线程无效；任务跑满（实测 sleep 20 在 interrupt 后仍完成） |
| 追加 prompt | 无 task 句柄；`session.steer` 只作用于 device session |
| 状态查询 | `session.status` / `session.usage` / `delegation.status` / `process.list` 均看不到后台 task |
| 结果可靠性 | 仅一次性 `background.complete`；错过即只能看门狗超时；父会话 history 无轨迹 |

### 1.3 为什么不用 native subagent

`docs/hermes_websocket_api.md` §9.2 / §10.3：原生 subagent 的控制面（`subagent.interrupt/steer`、v0.22.2 的 `subagent.list/tail`）可用，但**没有 RPC 创建入口**——创建只能由模型调用 `delegate_task`；完成结果回注父会话并触发额外 turn；`subagent.complete` 的 `text/summary` 有截断。作为 relay 的确定性 job API 不合格。

### 1.4 为什么 job 会话可行

`docs/hermes_websocket_api.md` §9.3 已逐项实测：

- `session.create` + `prompt.submit` 是确定性 RPC；
- `session.active_list` 提供 `working/idle`；
- `session.steer` 不打断注入；`prompt.submit` 运行中默认 `redirected`；
- `session.interrupt` 立即停 turn（`status:"interrupted"`）；
- 同一 WS 多 session 并行且互不影响；
- 会话 transcript 持久化，`stored_session_id` 可 `session.resume`。

本稿补充的关键耐久性探针（v0.20.2，2026-10-17）：

| 场景 | 探针 | 结果 |
|---|---|---|
| 运行中 job 会话断连 >20s | `/tmp/hermes_probe_jobdisc_live.log` | 22s 离线后 `session.resume` 返回**同一 sid**，`active_list.status="working"`，turn 继续；resume 后 `message.complete` 在新连接到达（`text=JOB_DISC_LIVE`，`status=complete`） |
| turn 在离线期间完成（>20s 孤儿窗口后） | `/tmp/hermes_probe_jobdisc_done.log` | resume 返回**新 sid**（`d23ec7e6`）+ `messages`（user → tool → assistant `JOB_DISC_DONE`）；无 `message.complete` 重放，但结果可从 transcript 恢复 |
| interrupt 与后台进程 | `/tmp/hermes_probe_bgproc.log` | `session.interrupt` 后前台 turn `interrupted`（exit 130），`terminal background=true` 启动的 `sleep 120` 仍 `running`；`process.list` 可见，`process.kill` 返回 `killed` |

> 源码解释（v0.20.2 `tui_gateway/server.py:1036-1047`）：`_ws_session_is_orphaned` 在 `session["running"]` 为真时返回 False，所以**运行中的 job 会话不会被 20s 孤儿回收**；turn 结束后若仍未 resume，才可能被回收，但 transcript 可恢复。

---

## 2. 设计目标与非目标

### 目标

1. **Job 全生命周期**：创建 → 运行 → 完成/失败 → 投递，复用 JobStore/Deliverer/补投；新增 **cancel / steer / 状态判定**。
2. **确定性关联**：relay 自己掌握 `job_key ↔ session` 映射与 `stored_session_id`，不依赖模型回报 task_id。
3. **可靠恢复**：relay 重启 / WS 重连后，running job 继续等待（由 D17 一次性复查兜底，不依赖 48h 看门狗）；离线期间完成的 job 从 `resume.messages` 读回结果补投（优于 v2 at-most-once）。
4. **隔离**：job 不污染 device session 的 history / mission / nudge context；多个 job 并行互不影响。
5. **零 wire / 零 schema / 零客户端改动**：`PushMessage`、`job_status`、`agent_jobs` 表、Android 均不变。
6. **单一路径**：background carrier 已删除，避免双载体在恢复/取消/清理语义上的分叉。

### 非目标（本期不做）

- native subagent 作为 job 载体（§1.3）；
- 多 turn job / `queued:true` 续轮（v3 job 单 turn）；
- 用户侧 cancel/steer 产品入口（adapter API 预留，产品交互另评）；
- **job 结束后保留其启动的后台 server/watcher**：产品已确认目前不需要常驻；v3 终态 close 会清理 tracked 进程，明确不提供进程留存能力；
- Hermes 升级到 v0.22.2+（不升级；`session.events.since` 作为未来增强）；
- 跨机部署、TTS、多 Hermes 实例。

---

## 3. 调研结论（codebase + 协议）

### 3.1 当前 adapter 结构

| 结构 | 位置 | v3 复用/修改 |
|---|---|---|
| `_DeviceConnection` | `hermes.py:120-158` | **扩展**：增加 job 会话映射 |
| 每 device 一条 WS、常驻 reader | `hermes.py:552-594` | 不变；reader 增加 job 事件路由 |
| device session 建/复用 | `hermes.py:1149-1206` | 不变；job session 独立 create |
| nudge / reactive turn | `hermes.py:1298-1440` / `:1444-1538` | 不变 |
| `_submit_turn` / `_register_turn` | `hermes.py:1654-1765` | 复用模式（先注册 future 再发）；job 用 sid 映射 |
| `_handle_job_message_complete` | 新增 | job sid 的终态收敛（live） |
| 启动恢复 / resume | `hermes.py:853-926` / `:744-816` | **扩展**：job session 恢复 |
| watchdog | `hermes.py:248-271` | **扩展**：`scan_stale` 收敛（failed）后对 session-carrier job 尽力 interrupt/kill（资源清理而非状态迁移；与 openclaw 顺序的差异见 §4.3.4） |
| JobStore | `job_store.py` / `job_store_sqlite.py` | **小改**：新增 `bind_external_ref` |
| Deliverer | `delivery.py` | 零改动（按 `JobRecord.agent_name` 归属） |
| JobStatus | `job_status.py` | 零改动（running 快照仍由 JobStore 驱动） |
| ChatStore 映射 | `hermes_device_sessions` | 不改（device 维度；job 用 `agent_jobs.hermes_task_id`） |

### 3.2 协议事实（引自 `hermes_websocket_api.md` §9 / §10）

| 事实 | 结论 | 来源 |
|---|---|---|
| 同一 `/api/ws` 可创建多个 session | job 会话与 device 会话共用一条 WS | §9.3.1 实测 |
| 事件按 `params.session_id` 路由 | job 完成事件可精确路由 | §9.3.2 |
| `session.create` → `prompt.submit` → `{"status":"streaming"}` | 确定性派发 | §9.3.1 |
| `message.complete` `status` ∈ `complete/interrupted/error` | 终态判定 | v0.20.2 源码 + 实测 |
| `session.resume` 返回 `session_id` / `resumed` / `messages` | 恢复键 + 结果来源 | 本稿探针 |
| `session.steer` | 运行中注入；v0.22.2 idle 返回 `rejected` | §9.3.1 / §10.5 |
| `session.interrupt` | 停 turn；不清理后台进程 | §9.3.3 + 本稿探针 |
| `process.list/kill` | session 作用域，可清理 job 后台进程 | 本稿探针 |
| `4090` 错误 | active session 上限需显式失败；首个 `prompt.submit` 返回 | §3.7 / v0.20.2 源码（`methods_prompt.py:316`） |

### 3.3 本稿新增探针（v0.20.2，2026-10-17）

| 探针 | 文件 | 说明 |
|---|---|---|
| job 会话运行中跨断连 | `/tmp/hermes_probe_jobdisc_live.log` | 22s 离线 → resume 同 sid → 新连接收 complete |
| job 会话离线完成 | `/tmp/hermes_probe_jobdisc_done.log` | 25s 离线 → resume 新 sid + 完整 messages |
| interrupt 与进程清理 | `/tmp/hermes_probe_bgproc.log` | 前台 turn 停止；后台进程保留；process.kill 成功 |
| resume 的 `omit_messages` 回退 | `/tmp/hermes_probe_omit.log` | `resume {omit_messages:true}` → `messages_omitted=true`、`messages=[]`、`running=false`、`status="idle"`；`session.history` 取回终稿 `JOB_DISC_DONE` |
| 显式 `session.close` 后 resume | `/tmp/hermes_probe_session.log`、`/tmp/hermes_probe_omit.log` | scenario C close→resume 成功（`message_count:3`、history 含 `Operation interrupted.`）；done 会话 close 后再次 resume 取回 4 条 messages。D14 的审计/补取成立 |
| steer 契约 + interrupt 文案 | `/tmp/hermes_probe_steer_contract.log`；可复现脚本 `services/tests/manual/probe_hermes_job_contract.py` | working job 会话 `session.steer` 返回精确 `{"status":"queued"}`，注入在工具边界生效（最终回复 `STEER_INJECTED`）；interrupt 后 history 最后 assistant 精确 `"Operation interrupted."`；指定不存在 model 会被回退，`"Error:"` 未触发 |
| clarify 型 waiting 恢复 | `/tmp/hermes_probe_waiting_clarify_id.log` | pending clarify → 断 WS → `session.resume` 返回 `waiting` + `pending_clarify.request_id`；带 id 的 `clarify.respond` 使状态转 working 并完成（不带 id 时保持 waiting）。确认 relay 必须回传 request_id |
| Adapter clarify autorespond E2E | `/tmp/hermes_probe_clarify_e2e.log`（`/tmp/hermes_adapter_clarify_e2e.py`） | job 任务书要求模型调用 clarify；真实 Adapter 收到事件后自动应答（带 request_id），job 正常完成并投递 `JOB_CLARIFY_OK` |
| 既有 job/会话能力 | `/tmp/hermes_probe_*.log` | `hermes_websocket_api.md` §11.3 清单 |

> 探针脚本：`/tmp/hermes_job_session_disc.py`、`/tmp/hermes_omit_probe.py`（本稿新增）；既有脚本 `/tmp/hermes_ws_probe.py`。

### 3.4 与 v2 的关键差异（设计输入）

| 维度 | v2（现状） | v3（本设计） |
|---|---|---|
| job 载体 | `prompt.background`（父会话旁路线程） | 独立 Hermes job 会话 |
| 关联键 | task_id `bg_*` | `job_key` + `session:<stored_session_id>` |
| 完成事件 | `background.complete`（一次性） | job sid 的 `message.complete` |
| 结果全文 | `background.complete.text` | `message.complete.text`（完整） |
| cancel | ✗ | `session.interrupt` + 可选 process.kill |
| 追加 | ✗ | `session.steer` |
| 状态 | ✗ | JobStore/JobStatus；`active_list` 用于恢复判定 |
| 断连恢复 | 事件丢失不可恢复 | resume + transcript 恢复 |
| 主会话影响 | 不写 history；仅事件 | 完全隔离 |
| 配额 | Hermes 无任务上限 | 每 active job 占一个 Hermes session 槽（4090） |

---

## 4. 总体设计

### 4.1 架构与数据流

```
 LivaChat Server（relay 同进程）
 ┌────────────────────────────────────────────────────────────────────┐
 │ ChatlogWatcher ─► HermesAdapter.nudge() ─► device session          │
 │     契约 job_request ─► JobStore.create(requested)                 │
 │                         └─ session.create(close_on_disconnect=false)
 │                         └─ bind_external_ref("session:<stored>")   │
 │                         └─ prompt.submit(task)                     │
 │                         └─ mark_running(requested→running)        │
 │                                                                    │
 │ HermesAdapter reader（per-device WS）                               │
 │   message.complete(sid) ─► job_sid_to_key ─► mark_done/failed      │
 │   approval/clarify/sudo/secret(sid) ─► 既有 autorespond 矩阵       │
 │                                                                    │
 │ JobResultDeliverer ─► PushPublisher ─► gateway ─► ai_text          │
 └────────────────────────────────────────────────────────────────────┘
                              │ 同一 WS（JSON-RPC 2.0）
                              ▼
 Hermes :9119
   ├─ device session（监听/nudge/ask，现有）
   └─ job session ×N（每 job 一个；独立 history；可 steer/interrupt/resume）
```

### 4.2 会话模型

| 会话 | 归属 | 创建 | 生命周期 |
|---|---|---|---|
| device session | `_DeviceConnection.hermes_session_id` | 现有 `ensure_session`（`messages=[mission]`） | 现有（keep/resume） |
| reactive session | `ask()` 临时 | `session.create(close_on_disconnect=True, source="tool")` | 现有（turn 后 close） |
| **job session** | `_JobSession`（新增） | `session.create(close_on_disconnect=False, source="livachat-relay", title=summary[:80])` | job 终态后 `session.close` |

新增数据结构（放在 `hermes.py`）：

```python
@dataclass
class _JobSession:
    job_key: str
    hermes_session_id: str      # live sid（事件路由）
    stored_session_id: str      # durable id（session.resume）
    created_at: float

@dataclass
class _DeviceConnection:
    ...
    job_sessions: dict[str, _JobSession] = field(default_factory=dict)
    job_sid_to_key: dict[str, str] = field(default_factory=dict)
```

- 一个 WS 连接承载 device + N 个 job session；`message.complete` 先查 `job_sid_to_key`，未命中再走现有 `turn_futures`（device/ask）。
- job 会话与 device 会话的 sid 不同；job 之间也互不影响。
- 设备连接回收逻辑不变：`_keep_connection` 已因 active job 保留连接（`hermes.py:423-429`）。

### 4.3 Job 生命周期

#### 4.3.1 状态机

```
requested
  │ session.create 成功 → bind_external_ref("session:<stored>")
  │ mark_running（先转 running，再 submit）
  ▼
running ──message.complete(complete)──► done ──deliver──► delivered
  │
  ├─message.complete(error)──────────► failed(run_error)
  ├─message.complete(interrupted)────► failed(interrupted)
  ├─watchdog timeout → interrupt ────► failed(timeout)
  ├─cancel_job() → interrupt ────────► failed(cancelled)
  ├─resume 新 sid 且无 transcript ───► failed(session_lost)
  └─requested 超时（未 bind / 未 mark_running）──► failed(orphaned)
```

`fail_reason` 新增：`run_error` / `interrupted` / `cancelled` / `session_cap` / `create_error` / `submit_error` / `dispatch_aborted`。`JobRecord` 的 docstring 同步更新（`job_store.py:50-53`）。

#### 4.3.2 创建与派发

```
1. 校验 spec / per-device cap / global in-flight cap（现有 hermes.py:1371-1393 不变）
2. job = job_store.create(session_id, device_id, summary, agent_name="hermes")
3. r = session.create {
       close_on_disconnect: False,
       source: "livachat-relay",
       title: summary[:80] or f"job:{job_key}",
   }
   → 必须有 session_id + stored_session_id；仅缺 stored → best-effort session.close
     已创建的 live sid，再 mark_failed("create_error")（不留不可恢复的空闲会话）
4. job_session = _JobSession(job_key, r.session_id, r.stored_session_id)
   conn.job_sessions[job_key] = job_session
   conn.job_sid_to_key[r.session_id] = job_key
5. job_store.bind_external_ref(job_key, f"session:{stored_session_id}")
   → 失败/异常（watchdog 已收敛或存储错误）→ close job session，清理映射，
     mark_failed("create_error")，返回
6. ok = job_store.mark_running(job_key, f"session:{stored_session_id}")
   → 失败（watchdog 已收敛）→ close job session，清理映射，返回
   → 成功 → inc_job_event("started")（与 v2 parity；§5.6）
   // 先转 running 再 submit：避免 dispatcher 在 submit 之前被取消时
   // job 仍停在 requested、被 60s watchdog 误杀（长任务 >60s）。
7. reply = prompt.submit {session_id: live_sid, text: task_desc}
   → 接受 status ∈ {"streaming","redirected","queued"}；
     其他/异常 → mark_failed("submit_error"|"rpc_error"|"session_cap") + close + 清理映射
     // 4090（active session limit）在首个 prompt.submit 时返回（session.create 不占槽）
     // fresh idle session 的正常 ack 是 "streaming"；收到 redirected/queued
     // 防御性接受，但 warning log + metric submit_unexpected_status（协议漂移观测，§5.6）
8. notify_job_started 时 yield summary（现有语义不变）
9. 若 create→submit 期间 WS 被替换（conn.ws 身份变化）：说明重连恢复可能因
   job_resume_inflight 跳过了本 job，且完成事件可能留在旧 transport 上丢失；
   arm 一次 D17 recheck 兜底（正常完成会 cleanup 并取消该 recheck）
10. 若 nudge task 在 bind/mark_running/submit 期间被取消（CancelledError）：
    mark_failed("dispatch_aborted") + close job session（submit 可能已被服务端受理），
    然后重新抛出；CAS 保护并发完成的 job
```

- **为什么 bind 在 submit 前**：`session.create` 成功后即拿到 `stored_session_id`；先把恢复键落库，relay 在 submit 前后崩溃都能被启动恢复识别。`bind_external_ref` 只写 `hermes_task_id`，不改状态（新增 JobStore 方法，见 §5.3）。
- **为什么 mark_running 在 submit 前**：避免 dispatcher 在 submit 前后被取消时 job 停在 requested、被 60s watchdog 误杀；fresh session 的 `prompt.submit` ack 是 `streaming`，submit 失败则立即 `mark_failed("submit_error")` 回滚。代价：running 状态可能早于实际 turn 几毫秒；可接受。
- **mark_running 后、submit 前崩溃**：job 为 running + `session:<stored>`；恢复时该会话 idle 且无 messages → `failed(orphaned)`（§4.4.3），不会等 48h 看门狗。
- **dispatcher 在 submit 后被取消**：job 已是 running，turn 的正常完成事件仍会收敛；若 submit 实际未发出，恢复/看门狗按 running-no-turn 处理（同上）。
- **完成事件不可能早于 mark_running**：mark_running 在 submit 之前，因此 reader 收到任何 job 会话的完成事件时 job 至少已是 running；reader 对 requested 的 defensive CAS 仅作为防御（§4.3.3）。
- **与重连恢复串行**：`create → bind → mark_running → submit` 整段在 `conn.start_lock` 下执行（与 device session resume / `_resume_job_sessions` 共用），防止 WS 在 create 与 submit 之间重连时，恢复逻辑把尚未提交的 job 会话判为 idle/orphaned。`conn.lock`（nudge 串行）与 `start_lock` 的获取顺序固定为 `conn.lock → start_lock`，无反向路径。
- **session.create 的 `title`**：仅用于 dashboard/运维辨识；v0.20.2 的 pending title 在首个成功 turn 后落库（`server.py`），不影响恢复。
- **不传 `messages`**：job 任务书自包含（旧 `prompt.background` 也不继承父会话历史）。
- **不传 `cwd/model`**：与当前 relay 创建 device session 的语义一致（gateway 默认）；如未来 device session 有 per-session override，另行评估（§9-Q3）。
- **容量算术（D10）**：active-session cap 是 **per-HERMES_HOME/profile** 的文件注册表（`~/.hermes/runtime/active_sessions.json`；`try_acquire_active_session` 跨进程计数，CLI/desktop/gateway 共享），不是 per-WS-connection。每个**跑过 turn 且仍存活**的 session 从首次 `prompt.submit` 起持有一个 lease，直到 session finalize/reap（`_release_active_session_slot` 在 `_finalize_session`），不是只在 turn 期间。因此 relay 侧的保守上界：
  `#live_device_sessions + #concurrent_ask_turns + #active_jobs ≤ max_concurrent_sessions`
  最坏情况按 `#devices × 2 + Σ max_concurrent_jobs_per_device` 估算；`max_concurrent_sessions` 未配置（默认 None）时不设限。否则 4090 会在峰值并发处**成片**出现（系统性容量墙，非单点随机失败）。配置对齐见 §5.1。

#### 4.3.3 执行与完成

reader 的 `message.complete` 分支增加 job 路由（在现有 straggler/turn_futures 之前）：

```python
sid = self._event_session_id(params)
job_key = conn.job_sid_to_key.get(sid)
if job_key:
    await self._handle_job_message_complete(conn, job_key, sid, payload)
    return
# 现有 device/ask turn 逻辑
```

`_handle_job_message_complete`：

```
job = job_store.get(job_key)
if job is None or job.status not in ACTIVE:
    // job is None 可能掩盖 store 故障：error log + metric complete_unknown_job（§5.6）
    清理映射，drop
if job.status == requested:
    // 防御性：正常路径下 mark_running 已在 submit 前完成，不应出现 requested。
    // 若出现（例如旧版本/异常），reader 自己完成 requested→running 的 CAS。
    ok = mark_running(job_key, job.hermes_task_id)
    if not ok:
        job = job_store.get(job_key)
        if job is None or job.status != running: 清理映射，drop
status = payload.get("status") or "complete"
text   = payload.get("text") or ""
if status == "complete":
    kind, out_text = classify_job_result(text)   // live/recovery 共用
    if kind == "deliver":
        mark_done(job_key, out_text)
        cleanup_job_maps + best_effort session.close(live_sid)   // 先释放会话槽
        await deliverer.deliver(job)        // 再投递（deliver 内部重读状态）
        metric completed
    else:                                    // noop / rpc_error / empty：消费但不投递
        mark_done(job_key); mark_delivered(job_key)
        cleanup_job_maps + best_effort session.close(live_sid)
        metric kind
elif status == "interrupted":
    mark_failed(job_key, "interrupted"); metric interrupted
    cleanup_job_maps + best_effort session.close(live_sid)
elif status == "error":
    mark_failed(job_key, "run_error"); metric run_error   // payload.error 记日志
    cleanup_job_maps + best_effort session.close(live_sid)
else:
    mark_failed(job_key, "run_error")
    cleanup_job_maps + best_effort session.close(live_sid)
```

`classify_job_result(text)`（**live 与 recovery 必须共用**）：

| 输入 | kind | 处置 |
|---|---|---|
| `strip().upper() == "NOOP"` | `noop` | done+delivered，不投递（v2 NOOP/error 约定） |
| `strip().startswith("error:")` | `rpc_error` | done+delivered，不投递（v2 NOOP/error 约定；kind 名沿 v2 metric，实为模型侧错误输出而非 RPC 错误，代码注释注明） |
| `strip() == ""` | `empty` | done+delivered，不投递（避免空 ai_text） |
| 其它 | `deliver` | `mark_done(text)` + deliver |

> 如果 recovery 路径不共用该分类，离线完成的 NOOP/error 会被当成成品推给用户——这是必须在实现期用单测锁住的行为。

- **reader 内不得 `await _rpc`**：job 完成处理运行在 `_reader_loop` → `_handle_message` 内；同一连接的 RPC 响应也由该 reader 读取，`await _rpc` 会自锁。清理里的 `session.close` 用 `_send_notification`（现有 `_close_hermes_session` 即此模式）或 `asyncio.create_task` 调度；`mark_done/deliver` 不涉及 RPC，可 inline await。
- **store/deliver 异常必须 per-event 捕获**：`job_store.get/mark_done/mark_delivered` 抛异常时不能穿透 reader（穿透会丢掉本事件并让 reader 休眠）；保留映射、log + metric，并 **arm 一次 D17 recheck**（10s 后从 transcript 重收敛；若彼时 job 已 done 则幂等补投）——不能只依赖 48h 看门狗。`deliverer.deliver` 异常只 log，job 保持 `done` 等补投（v2 同款）。`job is None` 与“get 抛异常”要区分：前者是未知/已清理，后者是存储故障，不能直接 drop。
- 结果文本用 `message.complete.text`，**不解析** `status.update` / 自由文本。
- `params.type="error"` 事件若 `sid` 属于 job：`mark_failed("run_error")` + 清理 + close；若随后还有 `message.complete`，按非 active 丢弃（避免重复收敛）。
- `NOOP` / `error:` 沿用 v2 的 NOOP/error 约定（不投递、视为已消费；注意与 v3 D14"清理"无关——v2/v3 决策编号各自独立，引用一律写全称）。
- 完成/失败后**先关闭 job 会话**（释放 active session 槽，避免 deliverer 的 `maybe_reap_conn` 抢先拆 WS），再投递/补投（见 §4.8）。v0.20.2 的 `AIAgent.close()` 会 `process_registry.kill_all(task_id=session_id)`，job 启动的 tracked server/watcher 随 close 被清理——这是**预期行为**（产品已确认目前不需要常驻；job 产物应为文案/文件/静态资源）。
- 若 `deliverer.deliver` 失败（设备离线），job 保持 `done`，现有 `sweep_device` 补投。

#### 4.3.4 取消（adapter 能力，产品入口暂不接）

```python
async def cancel_job(self, job_key: str, reason: str = "cancelled") -> bool:
    job = await job_store.get(job_key)
    if job is None or job.status not in ACTIVE:
        return False                     # 已终态/不存在：幂等 no-op
    if conn is None:                      # relay 重启后尚无本地连接
        conn = await ensure_device(job.device_id)   # best-effort 重连，才能真 interrupt
    js = locate_job_session(job_key)     # 内存；缺失时按 §4.4.3 resume 建映射
    if js is None:
        await job_store.mark_failed(job_key, reason)
        return True
    await _interrupt_job_session(js)     # interrupt + 可选 process.kill
    # reader 会收到 message.complete(interrupted) → failed(interrupted)
    # 等待 job 离开 active（asyncio.Event / 短轮询）；超 job_cancel_grace_s：
    #   mark_failed(job_key, reason)（CAS active）+ close session
    return True

async def _interrupt_job_session(js: _JobSession) -> None:
    await session.interrupt {session_id: js.live_sid}
    if cfg.job_cancel_kill_processes:
        procs = await process.list {session_id: js.live_sid}
        for p in procs:
            # 注意：process.list 的条目把进程 registry id 放在 "session_id" 字段
            # （如 "proc_27ca1ff4c1f5"）；process.kill 的参数名是 "process_id"。
            await process.kill {
                session_id: js.live_sid,
                process_id: p["session_id"],
            }
```

- 返回语义：`True` = job 原本 active，取消已发起/已收敛；`False` = 已终态或不存在。无本地连接时先 best-effort `_ensure_device` 重连并 resume/interrupt 远端 job；重连失败才退化为仅 mark_failed。
- **本地 shutdown/reap 不得误杀 job**：job recovery / recheck 在 `conn.stopped` 后不再发起 resume，也不做 failed 迁移——留给下次启动恢复。
- `_interrupt_job_session` 是 best-effort：`session.interrupt` / `process.list` / `process.kill` 的异常只记日志（不向上抛），job 仍按 `cancelled`/`timeout` 收敛；watchdog 对 stale job 的调用也按 per-job 捕获，不能因一个设备失败停掉扫描。
- 缺失内存映射时，`cancel_job` 的 resume 与 §4.4.3 共用同一套 job-resume 去重与 `conn.start_lock` 约束。
- 等待 `message.complete(interrupted)` 的收敛由 reader 完成；`cancel_job` 只等一个短宽限，超时自行 `mark_failed`，随后到达的 complete 按非 active 丢弃（现有语义）。
- watchdog timeout 路径**不能**调用 `cancel_job`（此时 job 已被 `scan_stale` 标 failed，`cancel_job` 会 no-op）；应直接调用 `_interrupt_job_session`。若内存里没有映射（relay 刚重启、恢复尚未跑），先按 ref resume 建映射；resume 失败则跳过（job 已是 failed，仅损失一次清理机会）。
- **顺序与 openclaw 不同**：openclaw 先 `_abort_run_best_effort` 再 `mark_failed`（`openclaw.py:1075`）；hermes v3 是 `scan_stale` 收敛**后**清理——interrupt 仅作资源清理而非状态迁移，先收敛再清理可避免在 interrupt 与 mark_failed 之间引入新的 CAS 竞态。实现者勿照抄 openclaw 顺序（§3.1 已同步修正）。
- `session.interrupt` 只停 turn；后台进程需 `process.list/kill`（实测，`/tmp/hermes_probe_bgproc.log`）。

#### 4.3.5 追加 prompt（adapter 能力，产品入口暂不接）

```python
async def steer_job(self, job_key: str, text: str) -> bool:
    js = locate_job_session(job_key)
    if js is None: return False
    r = await session.steer {session_id: js.live_sid, text: text}
    # 真机 v0.20.2（2026-10-17，/tmp/hermes_probe_steer_contract.log）：
    # 运行中的 job 会话返回精确 {"status":"queued", text}，注入在下一个
    # 工具边界生效（最终回复被 steer 文案覆盖）
    return r.get("status") == "queued"
```

- 只对 running job 使用；v0.22.2 的 idle `rejected` 语义已在 §10 记录（本机 v0.20.2 当前不升级）。
- **返回值契约已实测（§9-Q6 已解决）**：v0.20.2 真机 job 会话运行中返回 `{"status":"queued"}`，注入生效；非预期值仍 warning log（防御未来版本漂移）。
- 不提供 `queued:true` 续轮：job 仍是单 turn、单终态。

#### 4.3.6 状态查询

- **wire 状态**：JobStore + `JobStatusNotifier`（`job_status.py:96-237`）保持不变；`running` 快照由 `mark_running` 驱动，`job_status` 已带 `agent_name`。
- **adapter 内部状态**：`session.active_list`（`status: working/idle/waiting/starting`）用于恢复判定；`session.status` 仅作人类可读辅助。
- 不新增 `job.status` RPC；不改变 `fd_job_status.md` 协议。

### 4.4 恢复设计

> 现有 `startup_recovery_enabled` 是启动恢复总开关；job 会话恢复在它之上再由 `job_recovery_enabled`（默认 true）独立控制（启动与重连都受该开关控制）。关闭后新 job 仍可运行，但 relay 重启/断连后不主动恢复 job 会话（回到 v2 的 at-most-once 语义）。

#### 4.4.1 启动恢复（relay 重启）

现有 `recover_active_devices`（`hermes.py:853-926`）只恢复 device session，且 `targets` 仅包含有 **running** job 的 device（`hermes.py:872-875`）。v3 需要两处调整：

1. **扩大 target 集合**：`running` job，或 `requested` 且 `hermes_task_id.startswith("session:")` 的 job（bind 后、mark_running 前崩溃的 requested job 必须恢复；无 ref 的 requested 仍跳过，留给 `job_requested_timeout_s` 收敛）。
2. **恢复 job session**：

```
for each active job (requested/running) of device:
    ref = job.hermes_task_id
    if ref.startswith("session:"):
        stored = ref.removeprefix("session:")
        resume job session (§4.4.3)
    else:
        // 无 ref：requested 窗口没 bind 就崩溃 → 留给 job_requested_timeout 收敛
```

- 不同 device 的恢复可并行（`recovery_concurrency` 限并发）；同一 device 内 device/job session 的 RPC 由 `conn.start_lock` 串行化。
- `job_sessions` 映射在恢复时重建；`job_sid_to_key` 用 resume 返回的新 sid。
- job 恢复与 device session 恢复**无依赖、无需定序**：两者共享 `conn.start_lock` 串行化 RPC 即可；job resume 用独立重试循环，不复用 device 专用的 `_resume_with_retries`（§4.4.3）。

#### 4.4.2 WS 重连（relay 进程存活）

- 现有 `_reader_loop` 重连成功后 `_spawn_resume(conn)` 恢复 device session。v3 在同一处追加 `_spawn_job_resume(conn)`：
  - 对 `conn.job_sessions` 中仍 active 的 job，逐个 `session.resume {stored}`；
  - 更新 `job_sid_to_key`（同 sid 或新 sid）；
  - **应尽快在完成事件到达前完成 resume**，否则事件仍发往旧 transport（实测：resume 后 complete 才到新连接）；错过事件时仍可由 resume messages / D17 复查恢复，不是不可逆丢失。
- 若重连退避期间 job 完成：resume 返回新 sid + messages，走 §4.4.3 的结果恢复。
- 重连后的 device session resume 逻辑不变。
- `_spawn_job_resume` 必须以 `asyncio.create_task` 运行（与现有 `_spawn_resume` 同模式），**不能 inline 在 reader 里 await `_rpc`**——同一连接的响应由 reader 读取。
- job 恢复按 `job_key` 去重（in-flight set 或 per-connection `resume_tasks`），避免启动恢复与重连恢复、或多次重连同时 resume 同一 job；同一 job 的并发 resume 可能把 running 会话误判/重复 close。

#### 4.4.3 resume 与判定表

输入：`job`（JobStore）、`stored`（ref 去掉前缀）、`resume` 结果。

> **不要复用 `_resume_with_retries`**：它是 device-session 专用；job resume 用独立的重试循环，只作用于该 job。**单个 job 的 resume/active_list/history 异常必须 per-job 捕获**，不能中断 device 恢复或重连 sweep；失败 job 留给下次重连/看门狗，不得误标其他 job。

```
r = session.resume {session_id: stored, lazy: True}   // 重试 session_resume_retries / backoff
                                              // lazy：已完成会话只读 messages，不构建 agent（实测）
if r.error:
    if transient → retry；耗尽 → mark_failed("session_lost")
live = r.session_id
update job_sessions/job_sid_to_key
messages = r.messages
if r.get("messages_omitted"):
    // resume 可选 omit_messages（desktop 分页场景）；不处理会把空 messages
    // 误判为“无结果”。回退到 session.history 取完整 transcript。
    messages = (session.history {session_id: live}).messages
status = session.active_list[?id==live].status   // 未命中时回退 r.status / r.running
if status == "working":
    // turn 仍在跑（实测运行中不会被 20s 孤儿回收）
    if job.status == requested: mark_running(job_key, ref)   // 防御：submit ack 丢失
    keep waiting
    arm 一次性 job_resume_recheck_s（默认 10s）复查（D17）：
        到期先 re-read JobStore；非 active（live complete 已收敛）直接结束，避免对已关闭会话再 resume/close；
        仍 active 才重新 active_list + 读 messages，按本判定表重走 working/idle 分支；
        消除"completion 落在 resume 响应快照与 transport 重绑定之间"的缝隙
        （若仍 working，说明 transport 已恢复、后续 complete 会到新连接，不再重查；
         不把 48h 看门狗当唯一兜底）

> **为什么默认是 10s，而不是 600s（勿"修正"为周期轮询）**：该复查是**一次性**的，
> 且每次 WS 重连都会重新 arm（`_spawn_job_resume` → `_resume_job_session` 的 working 分支）。
> 它只覆盖 resume 快照与 transport 重绑定之间的毫秒级缝隙；10s 后仍 working 说明事件通道
> 已恢复，后续 `message.complete` 会正常到达，继续轮询没有意义。600s 只在把 recheck 当作
> 周期性状态轮询时才成立——当前语义不是；改大只会无谓延迟极小概率竞态的收敛。
elif status == "waiting":
    // 阻塞在 approval/clarify（事件可能丢失）。先按无人值守矩阵 best-effort：
    approval.respond {session_id: live, choice:"deny", all:true}
    clarify.respond {session_id: live, answer: 无人值守文案,
                     request_id: r.pending_clarify.request_id}  // v0.20.2 必需
    // 短重试重新 active_list；仍 waiting（可能是 sudo/secret，矩阵无法应答）：
    session.interrupt {session_id: live}
    // 后续按 message.complete(interrupted) 收敛；若宽限内无 complete → failed(interrupted)
elif status in ("idle", "starting"):
    if status == "starting" or r.get("running"):
        keep waiting（agent build / turn 结算中，短重试）
    else:
        kind, text = extract_job_result(messages)   // interrupted | run_error | text | none
        if kind == "none" and messages 尚无 assistant:
            // turn 结束后 transcript 落盘有 settle 窗口：重试 0.5s/1s/2s（总 ~3.5s）
            // 仍无 result → failed(orphaned)
        if kind == "interrupted": mark_failed("interrupted")
        elif kind == "run_error":  mark_failed("run_error")
        elif kind == "text":
            if job.status == requested: mark_running(job_key, ref)  // 防御
            result_kind, out_text = classify_job_result(text)       // live/recovery 共用
            if result_kind == "deliver":
                mark_done(job_key, out_text)
                cleanup maps + close job session
                deliverer.deliver(job)                              // 补投
            else:
                mark_done(job_key); mark_delivered(job_key)         // noop/error/empty
                cleanup maps + close job session
        else: mark_failed("orphaned" | "session_lost")
    cleanup maps; close job session（终止分支）
```

`extract_job_result(messages)`（返回 `kind, text`）：

- job 会话是专用会话，第一条 `role=user` 即 job 任务书；
- 取该 user 之后最后一条 `role=assistant` 的非空 `text`；若存在 assistant 但 text 为空 → `("text", "")`（交给 classify 归为 `empty`）；
- `"Operation interrupted."`（v0.20.2 打断后的持久化文案，实测）→ `("interrupted", "")`；
- `"Error:"` 前缀 → `("run_error", "")`；
- 否则 → `("text", text)`；无 assistant → `("none", "")`。
- **终稿文本必须再走 `classify_job_result`**，否则离线完成的 NOOP / `error:` / 空输出会被误投递。
- 恢复路径里的 `mark_running` 失败时（watchdog 已收敛）必须重新 `get`：非 running → cleanup/close 返回，不能继续 wait（与 live 路径的 defensive CAS 同一规则）。若 re-get 本身抛异常（存储故障）→ 状态未知，**保留会话/映射、不得 close**，等 live completion / 下次恢复；同理适用于 complete 路径的 defensive `requested→running` 重读失败。
- **字符串契约脆性（实现期锁定）**：`"Operation interrupted."` / `"Error:"` 是 v0.20.2 transcript 持久化文案（大写 E）；live 路径 `classify_job_result` 的 `error:`（小写）针对 `message.complete.text`——两者数据源不同、不是同一字符串，勿合并。Hermes 改文案会静默误判为 `text` → **误投递给用户**（比误判 failed 更糟）。真机已钉死 `"Operation interrupted."`（`/tmp/hermes_probe_steer_contract.log`，interrupt 后 history 精确等于该串）；`"Error:"` 的格式已由源码确认（`tui_gateway/server.py:10986-10993`：backend error 且无可见输出时写 `f"Error: {detail}"`），但指定不存在的 model 会回退默认模型，真机未能触发——保留 mock 单测，异常注入时再补真机验证。

#### 4.4.4 恢复期间的事件时序

- 若 resume 发生在 completion 之前：resume 绑定新 transport，后续 `message.complete` 正常到达（实测）。
- 若 completion 发生在 resume 之前：事件丢失；resume 的 messages 含终稿（实测）；按 §4.4.3 恢复并补投。
- 若 completion 与 resume 并发（落在响应快照与 transport 重绑定之间）：事件与 messages 均可能滞后；由 D17 的一次性复查收敛，不新增状态。
- 若 resume 返回新 sid 且 **在 `messages_omitted` 回退后** messages 仍为空：说明会话被重建但没有 transcript → `failed(session_lost)`（不占 48h 看门狗）。

#### 4.4.5 与 device session 失效解耦

**device session 与 job session 是两个独立 Hermes session，只是共用同一条 WS。** v2 曾在 device session resume 返回新 sid 时调用 `_mark_session_lost(conn)`，把该 device 所有 running job 标 `failed(session_lost)`；v3 已删除该函数及其调用——device session 的任何 resume 结果都不再触碰 job。

| 事件 | 对 job |
|---|---|
| device session resume 新 sid / resume 失败 | **不动**；由该 job 自己的 `session.resume` + transcript 判定 |
| job session resume 新 sid 且有终稿 | `done` + 补投 |
| job session resume 新 sid 且无 transcript | `failed(session_lost)` |
| WS 断开（running job） | job 会话仍 running，重连后 resume；完成过则从 messages 恢复 |

### 4.5 阻塞交互兜底

| 事件 | 现有处理（`hermes.py:1104-1145`） | job 会话适配 |
|---|---|---|
| `approval.request` | `approval.respond {session_id, choice:"deny", all:true}` | sid 即 job sid，无需改 |
| `clarify.request` | `clarify.respond {session_id, answer:..., request_id}` | 同上；**v0.20.2 必须回传事件 payload 的 `request_id`**，否则 4009 且不解除阻塞（真机确认）。恢复路径从 resume 的 `pending_clarify` 取 id |
| `sudo/secret.request` | `session.interrupt {session_id}` + turn_future 异常 | job 无 turn_future；interrupt 后由 `message.complete(interrupted)` 收敛为 failed(interrupted)；若宽限内没有 complete，按 failed(interrupted) 收敛并 close（复用 cancel 的宽限机制）。若恢复时 `active_list=waiting`，按 §4.4.3 主动 resolve/interrupt。 |

> 以上 autorespond 在 reader 内执行，只能 `_send_notification`；宽限收敛/close/mark_failed 必须调度到 reader 外的任务，禁止在 reader 内 `await _rpc` 或 sleep 等待。

### 4.6 Reactive `ask()`

不变：仍使用临时 `session.create(close_on_disconnect=True, source="tool")` + `prompt.submit` + `turn_futures`；job 路由不参与。`mode=both` 时 device/ask/job 三类 sid 在同一 reader 上按 sid 分流。

### 4.7 推送语义

- 不变：job 结果仍由 `JobResultDeliverer` 发 `PushMessage(agent_name="hermes", proactive=True, job_key=..., message_id=msg_agent_job_{job_key})`（`delivery.py:91-104`）。
- `JobStatusNotifier` 不变（`job_status.py`）；running 快照来自 `mark_running`。
- 不新增客户端字段 / 协议。

### 4.8 连接与清理

- `_keep_connection` 已有 active job 判定；job 运行期间连接不会被 idle reaper 回收。
- job 终态后：`mark_*` → 清理 `job_sessions`/`job_sid_to_key` → `session.close(live_sid)`（释放 active session 槽；v0.20.2 同时通过 `AIAgent.close()` kill 该 session/task 的 tracked 后台进程）→ `deliverer.deliver`（可能 schedule `maybe_reap_conn`）。先 close 再 deliver，避免 `maybe_reap_conn` 抢先拆 WS 使 close 失败。
  - `maybe_reap_conn` 以 task 方式延后执行；在它真正关 WS 前完成 close（顺序见 §4.3.3）。
- `close_on_idle` 配置：job 活跃时不会触发；终态关闭 job 会话后再按现有逻辑停连接。
- `close()`/`_stop_conn`：job 会话默认不主动 close（保持 `close_on_disconnect=false`，以便快速重启恢复）；重启后由 §4.4 恢复。若 `session_retention=close_on_idle`，无 active job 时随连接关闭 device session；job 会话终态后已 close。

---

## 5. Relay 侧改动

### 5.1 `config.py` — `HermesAdapterConfig` 新增

```python
job_cancel_kill_processes: bool = True      # cancel 时清理 process registry 跟踪的后台进程
job_cancel_grace_s: float = 5.0             # interrupt 后等 interrupted complete 的宽限
job_recovery_enabled: bool = True           # 启动/重连时恢复 job 会话
job_resume_recheck_s: float = 10.0          # D17：resume working 后一次性复查；仍 working 则事件通道已恢复，不再重查
# 复用：startup_recovery_enabled / recovery_concurrency / session_resume_retries /
#       session_resume_backoff_s / max_concurrent_jobs_per_device / max_in_flight_jobs
```

- 无 `job_carrier` 开关：session 是唯一载体（D12）。
- **容量对齐（D10）**：`max_concurrent_jobs_per_device` 必须与 Hermes `max_concurrent_sessions` 联调。cap 是 per-HERMES_HOME 的文件注册表（跨进程/界面共享，源码 `hermes_cli/active_sessions.py`），lease 从首次 `prompt.submit` 持有到 session finalize/reap。按最坏情况 `#devices × 2 + Σ per-device cap ≤ cap` 反推 relay 侧上限；4090 错误码已由源码确认（`methods_prompt.py:316`），本机 `max_concurrent_sessions` 未配置（不设限），容量实测待配置后补（§9-Q2）；生产保守取值（per-device ≤ 2）。
- 不新增 Hermes 侧配置项；不需要升级。

**YAML 现状（2026-10-17 清理后）**：`configs/*/server.yaml` 的 hermes 段只保留 6 键——`mode` / `base_url` / `api_key` / `nickname` / `background_timeout_s` / `max_concurrent_jobs_per_device`；其余 v2 键回落到 `HermesAdapterConfig` 默认值（行为不变）。`api_key` 改为 `${HERMES_AUTH_TOKEN}`，值写仓库根 `.env`（gitignored；本次只迁移、未改 token 值）。v3 新增的 4 个键同样可只写非默认值；env 覆盖仍走 `LIVACHAT__AGENT_RELAY__ADAPTERS__HERMES__*`。

### 5.2 `adapters/hermes.py` — 修改点

| 位置 | 修改 |
|---|---|
| `_DeviceConnection`（:120-158） | 增加 `job_sessions`、`job_sid_to_key` |
| `_handle_message`（:930-980） | `message.complete` 先查 job sid；`error` 事件同样路由到 job |
| `_handle_job_request` | 校验 cap → JobStore.create → `_dispatch_job_session`（唯一路径） |
| 新增 `_dispatch_job_session` / `_handle_job_message_complete` / `_handle_job_error` / `_extract_job_result` / `classify_job_result` / `_cleanup_job_session` / `cancel_job` / `steer_job` / `_recover_job_sessions` / `_resume_job_session` | 见 §4 |
| `_handle_background_complete` | **已删除**（含 `background.complete` 事件分支） |
| `recover_active_devices` | 追加 job session 恢复 |
| `_reader_loop` 重连路径（:552-594） | `was_reconnect` 时追加 `_spawn_job_resume(conn)` |
| `_watchdog_loop`（:248-271） | 对 `scan_stale` 返回的 job，若是 session carrier：有映射则 `_interrupt_job_session`；无映射先按 ref resume（失败则跳过）。此时 job 已被标 failed，`cancel_job` 会 no-op |
| `_mark_session_lost` | **已删除**：device session reap 不再 fail 任何 job（§4.4.5） |
| `on_session_end`（:1270-1296） | 不变（job 不随 LivaChat session 结束） |

### 5.3 `job_store.py` / `job_store_sqlite.py` — 新增 `bind_external_ref`

```python
async def bind_external_ref(self, job_key: str, external_ref: str) -> bool:
    """requested 状态下写 hermes_task_id（不改状态）；CAS 失败返回 False。"""
```

- Redis：WATCH `status == requested` → `HSET hermes_task_id` + `SET _task_key(ref)`；`_notify_record`。
- SQLite：`UPDATE agent_jobs SET hermes_task_id=?, updated_at=? WHERE job_key=? AND status='requested' AND updated_at<=?`；rowcount==1 后 `_notify_record`。
- 无 DDL；`hermes_task_id` 列/索引复用。`JobRecord.hermes_task_id` 注释更新为“外部关联键：`session:<stored_session_id>` / openclaw runId”。
- `find_by_task_id` 对 `session:` ref 也可用（`_task_key` 映射），便于运维/恢复查询；新 job 完成事件不依赖它。

### 5.4 `application.py`

- 零改动（`job_store` 已注入；deliverer/notifier/session manager 不变）。
- `JobStatusNotifier` 的 `agent_name` 参数保持 `"hermes"`；`JobRecord.agent_name` 已是真源（`job_status.py:214-216`）。

### 5.5 `prompts/`

- 使用 `prompts/job_task.md`（由 `background_task.md` 更名）作为 job 任务书；不新增模板。
- 若需要 job 会话标题/标识，由 `session.create.title` 与 JobStore 提供，不进 prompt。

### 5.6 Metrics

复用 `inc_job_event(self.name, event)`：

- 新增/补充：`session_created`、`submit_error`、`submit_unexpected_status`（fresh session 收到非 `streaming` ack，§4.3.2）、`session_cap`、`dispatch_aborted`（nudge task 在提交前被取消）、`interrupted`、`run_error`、`cancelled`、`cancel_sent`（用户取消已发出，与 OpenClaw 同标签；`interrupted/cancelled` 仍表示收敛原因）、`recovered_from_history`、`recovery_new_sid`、`session_closed`、`complete_unknown_job`（完成事件找不到 job 或状态非 active，§4.3.3）、`resume_recheck`（D17 复查触发/命中）。
- 现有 `requested/started/completed/rejected/rpc_error/noop_output/timeout/orphaned/session_lost/delivered/delivery_pending` 语义不变；`started` 在 `mark_running` 成功后发射（§4.3.2 step 6，与 v2 parity）。
- 日志：job_key/device_id/sid/stored/status/fail_reason；不打印 prompt 正文。

### 5.7 测试计划

基座：扩展 `services/tests/integration/agent_relay/mock_hermes_server.py`：

- `session.create` 支持多 session、stored id；
- `prompt.submit` 按 `session_id` 产生可编排的 `message.complete`（`status=complete/interrupted/error`、`text`、延迟）；
- `session.resume` 可返回同 sid（live）或新 sid + `messages`（已完成）；
- `session.interrupt` / `session.steer` / `process.list` / `process.kill`；
- 可模拟 WS 断连/重连（现有 mock 支持连接重建）。

| 层 | 用例 |
|---|---|
| 单测 | `_extract_job_result`（complete/interrupted/Error/无 assistant）；`classify_job_result`（NOOP/error:/empty/正常，live 与 recovery 共用）；`messages_omitted` → `session.history` 回退；external ref 前缀解析；mark_running→submit 顺序与 defensive CAS；cancel/steer 参数；4090 映射；D17 recheck 判定（working → 到期重查 → idle 收敛） |
| 集成 | ① job_request → session.create → bind → mark_running → submit → complete → done → delivered（断言 `msg_agent_job_*`）；② interrupt cancel → failed(interrupted)、无推送、process.kill 被调用；③ steer 运行中 → queued；④ WS 断连 running → 重连 resume 同 sid → complete 投递；⑤ WS 断连且完成 → resume 新 sid + messages → done + 补投；⑤b 离线完成 NOOP/error:/空输出 → done+delivered、**不投递**；⑤c resume messages_omitted → session.history 回退后正确恢复；⑥ relay 重启恢复（SqliteJobStore + Mock）；⑦ prompt.submit 返回 4090 → failed(session_cap) + close；⑧ approval/clarify/sudo/secret 在 job sid；⑨ 双 adapter（openclaw）回归；⑩ device session reap（resume 新 sid）**不影响** running job（§4.4.5），job 后续完成仍投递；⑪ live/recovery 竞态（resume 读 messages 与 `message.complete` 同到）→ 单次收敛、单次投递；⑫ 4090 容量：device + ask + N job 并发打满 `max_concurrent_sessions` 的失败快照与恢复（§4.3.2 容量算术） |
| 真机 | dev 环境：nudge → job_request → 真实 job 会话全链路；取消（interrupt + 进程清理）；steer 返回 `queued` 且注入生效；interrupt 后 transcript 精确含 `"Operation interrupted."`（`/tmp/hermes_probe_steer_contract.log`）；`"Error:"` 触发条件未构造（保留 mock 单测）（§4.4.3） |
| 边界（v3.2/v3.4 review 补充） | WS 未就绪 → failed(create_error)；create 缺 stored id → close 已建会话；dispatch 跨重连 → arm recheck；dispatch 被取消 → failed(dispatch_aborted)+close；完成/error 事件存储异常 → arm recheck；stale job 无内存映射 → resume 后 interrupt/close；`conn.stopped` 后 recovery/recheck 不误标 failed；无本地连接 cancel → 重连后 interrupt；done 未投递 → recheck 幂等补投；recovery working/text 分支 mark_running 失败 → 重新 get 校验；re-get 存储失败 → 保留会话/映射不 close；complete defensive 重读失败 → arm recheck 不 close |

### 5.8 实施阶段

| 阶段 | 内容 |
|---|---|
| P0 | 已完成：job 会话跨断连/离线完成探针（§3.3）、close 后 resume（§9-Q10）、cap 作用域（源码确认 per-HOME）、steer on job session + 返回契约（§9-Q6）、interrupt transcript 文案；待补：4090 容量实测（错误码已由源码确认；本机 cap 未配置）、approval 型 waiting（本机自动放行无法构造）、compression 轮换、transcript `"Error:"` 真机异常注入（格式已源码确认） |
| P1 | 配置/JobStore（4 键 + `bind_external_ref`） |
| P2 | session carrier 主链路（create/bind/submit/complete/deliver） |
| P3 | 恢复（启动/重连/resume/transcript/D17 复查）；可选：untracked job session 启动 janitor（§7） |
| P4 | cancel/steer/watchdog interrupt |
| P5 | 单测/集成/真机 E2E |
| P6 | **删除 legacy `prompt.background` 载体**：配置开关、分发分支、完成处理、`_mark_session_lost`、mock/测试/文档同步清理 |

- 无灰度/回退开关：session carrier 是唯一路径；旧代码已删除，不保留兼容分支。
- 若存在旧版本遗留的在途 `bg_*` job，升级后不再被收敛（无 `background.complete` 消费者）；由于该方案从未在生产启用，按无存量处理。

### 5.9 实施进展（2026-10-17）

| 阶段 | 状态 | 内容 |
|---|---|---|
| P1 配置/JobStore | ✅ | 4 个配置项；Redis/SQLite `bind_external_ref`（无 DDL） |
| P2 job 主链路 | ✅ | job 会话 create→bind→mark_running→submit；complete/error 路由；live/recovery 共用的 `classify_job_result`/`_extract_job_result`；终态 close + deliver |
| P3 恢复 | ✅ | 启动/重连 job resume；messages_omitted→history；waiting/working/idle/starting 判定；D17 一次性复查；单 job 异常隔离 |
| P4 cancel/steer/watchdog | ✅ | `cancel_job`/`steer_job`；interrupt + process.list/kill；watchdog stale job interrupt；security autorespond 宽限收敛 |
| P5 测试 | ✅ | v3 单测 + 集成；agent_relay 全量通过；真实 Hermes E2E 通过（`PROBE_V3_OK`） |
| P6 legacy 清理 | ✅ | 删除 `job_carrier`、`prompt.background` 分发/`background.complete` 处理、`_mark_session_lost`、mock background 支持与相关测试；任务书更名 `prompts/job_task.md` |

- **单一载体**：无 `job_carrier` 开关；所有 job 走独立会话。
- **Redis**：server 已不依赖 Redis；95 个真实 Redis（`requires_redis`）集成用例已全部移除。Redis 版 `JobStore` 仍由 fakeredis 单测用于 JobStatus/Delivery 链路；生产使用 `SqliteJobStore`（含 `bind_external_ref`），已全量验证。
- **真实 E2E**：`/tmp/hermes_adapter_v3_e2e.py` 使用真实 `hermes serve`（v0.20.2）完成 job 会话 create→submit→complete→delivered，结果为 `PROBE_V3_OK`，终态 job 映射已清理；`/tmp/hermes_adapter_clarify_e2e.py` 验证 job 内 clarify → relay autorespond（request_id）→ `JOB_CLARIFY_OK` 投递。
- **剩余 P0**：§9-Q2（4090 容量实测；错误码已源码确认）、approval 型 waiting、compression 轮换、transcript `"Error:"` 真机异常注入（格式已源码确认）；Q6（steer 契约）、clarify 型 waiting 恢复与 `"Operation interrupted."` 已真机确认。

---

## 6. Hermes 侧部署（不改源码）

1. 继续使用 `hermes serve` + 预置 `HERMES_DASHBOARD_SESSION_TOKEN`（与现状一致）。
2. `delegation.*` / subagent 配置不参与本方案；job 会话是普通 session。
3. 建议的审批/toolsets 收窄与 v2 相同（第一道防线）；relay 的 autorespond 是第二道。
4. **不需要升级 Hermes**；v0.20.2 已验证 §3.3 的全部关键行为。若未来升级 v0.22.2+，可用 `session.events.since` 作为恢复增强（不是前置条件）。

---

## 7. 可靠性与边界情况

| 场景 | 处理 |
|---|---|
| session.create 失败 | `mark_failed("create_error")` + metric；不重试（避免风暴）；用户/主 agent 可重新请求 |
| prompt.submit 失败（含 4090） | close job session + `mark_failed("submit_error"|"rpc_error"|"session_cap")`；4090 只在首个 prompt.submit 出现 |
| completion 早于 running（不应发生） | mark_running 在 submit 前，正常不可能；reader 仍保留 defensive `requested→running` CAS |
| relay 在 session.create 成功后、bind 前崩溃 | Hermes 侧泄漏 untracked session（relay 未持久化 stored id）：live 对象 20s 孤儿回收；`session.create` 不落 DB row（首个 prompt 才持久化），无 transcript。毫秒级窗口、单次崩溃至多一个，**接受**；可选启动 janitor：`active_list` 中 `source="livachat-relay"` 且不在 JobStore → close（§5.8 P2） |
| relay 在 create/bind 后崩溃 | requested + `session:<stored>`；启动恢复 resume；无消息 → failed(orphaned) |
| relay 在 mark_running 后、submit 前崩溃 | running + ref；resume 若 working → 继续；idle 且无 messages → failed(orphaned) |
| dispatcher（nudge task）被取消 | `failed(dispatch_aborted)` + close job session；CAS 保护并发完成 |
| relay 在 bind 后、mark_running 前崩溃 | requested + ref；resume 若 working（防御路径）→ mark_running；idle 且无 messages → failed(orphaned) |
| WS 断连（running） | job 会话不被 20s 孤儿回收（`running`）；重连后 `session.resume` 重新绑定；complete 到新连接 |
| WS 断连（完成） | resume 新 sid + messages → 提取终稿 → done + 补投 |
| resume 失败/新 sid 无 messages | `failed(session_lost)`（不占 48h） |
| 设备离线 | done 保持；`sweep_device` 补投（现有） |
| job 永不完成 / 超时 | `background_timeout_s` 是 job 时长硬上限（running 期间无事件刷新 `updated_at`）；watchdog → failed(timeout) + 尽力 interrupt/kill。注意 v3 下超时是**真杀**（v2 仅标失败、后台线程续跑）；长 job 需同步调整该值 |
| job 自行产生后台进程 | cancel 时 `process.list/kill` 清理。**正常终态 close job session 时，v0.20.2 `AIAgent.close()` 会 `process_registry.kill_all(task_id=session_id)`**，清理该 job 的 tracked 后台进程（`run_agent.py:4460-4490`）。**产品已确认目前不需要常驻 server/watcher，这是预期清理**；job 不得依赖结束后仍存活的进程。旧 `prompt.background` 载体已删除；未来若需要留存，另设系统级进程管理（不在 v3） |
| job 会话自行派生 async subagent | `session.interrupt` 不会级联 detached async delegation（§9.3.3）；cancel 需要 `delegation.status` + `subagent.interrupt` 或关闭 job 会话（`_finalize_session` → `interrupt_for_session`）。v3 job 任务书不鼓励派生 |
| 审批挂起（waiting） | 正常路径 autorespond；恢复路径主动 `approval.respond deny all` + `clarify.respond`（带 resume 的 `pending_clarify.request_id`）。真机验证：pending clarify 跨 WS 断连后 resume 仍为 waiting；带 request_id 的 respond 使状态转 working 并正常完成；Adapter E2E 中 job 主动 clarify → autorespond → 投递 `JOB_CLARIFY_OK` |
| 多 job 并发 | 每 job 独立 sid；per-device/global cap；同一 WS 多 session（实测） |
| 双 adapter | `agent_name` 归属不变；watchdog `agent_name` 过滤不变 |

---

## 8. 代码级改动清单

| 文件 | 改动 |
|---|---|
| `services/agent_relay/adapters/hermes.py` | `_JobSession`、job 映射、create/bind/submit、complete 路由、cancel/steer、恢复、watchdog 联动；删除 background 分支/`_mark_session_lost` |
| `services/agent_relay/config.py` | 4 个新配置项（§5.1）；无 `job_carrier` |
| `services/agent_relay/prompts/job_task.md` | 由 `background_task.md` 更名（任务书内容不变） |
| `services/tests/integration/agent_relay/test_hermes_v2_integration.py` | 移除 background 断言/`session_lost` fail-fast 用例，适配 session 载体 |
| `services/agent_relay/job_store.py` | `bind_external_ref`；docstring |
| `services/agent_relay/job_store_sqlite.py` | `bind_external_ref`（SQL + CAS） |
| `services/tests/integration/agent_relay/mock_hermes_server.py` | 多 session / resume messages / interrupt / steer / process |
| `services/tests/integration/agent_relay/test_hermes_v3_integration.py` | 新增集成用例 |
| `services/tests/unit/agent_relay/test_hermes_v3.py` | 纯函数/判定表单测 |
| `configs/{dev,test}/server.yaml` | 无需新增 v3 键；`job_carrier` 已删除（§5.1/D12） |
| `docs/hermes_adaptor_design_v3.md` | 本稿 |

**不改**：`base.py`、`session_manager.py`、`delivery.py`、`job_status.py`、`models.py`、`application.py`、Android/推送 wire、`agent_jobs` DDL、Hermes 源码。

---

## 9. 开放问题与暂缓验证项

1. **`session.resume` 的完整字段**：v0.20.2 实测同 sid 时无 `stored_session_id`，新 sid 时用 `resumed`；本设计保留已知 stored id，不依赖返回字段。`running` / `status` / `messages_omitted` 已用于 §4.4.3（`messages_omitted` 实测回退 `session.history` 可取回终稿）；`inflight` 具体语义仍待确认，但本设计不依赖。
2. **首个 `prompt.submit` 的 active session 上限（4090）**：`session.create` 不占槽，claim 发生在首个 `prompt.submit`（`_ensure_active_session_slot`）。源码确认：(a) 超限时 `prompt.submit` 返回 JSON-RPC `code=4090`（`tui_gateway/methods_prompt.py:316`，消息由 `active_session_limit_message` 生成）；(b) `max_concurrent_sessions` 作用域是 **per-HERMES_HOME/profile** 的文件注册表（`~/.hermes/runtime/active_sessions.json`，`try_acquire_active_session` 跨进程/界面计数），不是 per-WS；(c) lease 从首次 `prompt.submit` 持有到 session finalize/reap（`_release_active_session_slot`），不是只在 turn 运行期间。剩余：容量实测——本机 `max_concurrent_sessions` 未配置（不设限），需在测试实例配置 cap 后跑并发快照。
3. **per-session model/cwd**：job 会话继承 gateway 默认；若未来 device session 有 per-session override，是否需要透传（当前 relay 无 override，风险低）。
4. **job 会话压缩轮换**：单 turn 一般不触发；若触发，`stored_session_id` 是否变化、旧 ref 是否仍可 resume（Hermes DB lineage）——列为 P2 观察。
5. **`waiting` 恢复——clarify 已解决；approval 部分验证（v3.3 真机）**：`/tmp/hermes_probe_waiting_clarify_id.log` 显示 pending clarify 跨 WS 断连后 `session.resume` 返回 `status=waiting` + `pending_clarify.request_id`，携带该 id 的 `clarify.respond` 使状态转 working 并完成（缺失 id 时保持 waiting）；此前 relay 未带 request_id，是既有 bug，已修。approval 分支因本机 terminal 命令自动放行、无法构造 pending approval；源码确认 `approval.respond` 按 session_key + 可选 request_id 解析，`all:true` 语义成立。
6. **`session.steer` on job session——已解决（v3.3 真机复核）**：`/tmp/hermes_probe_steer_contract.log` 显示运行中的 job 会话返回精确 `{"status":"queued", "text":...}`，下一个工具边界注入并在最终回复中生效（`STEER_INJECTED` 覆盖原指令）。§4.3.5 契约成立；非预期值仍 warning 防御版本漂移。
7. **mark_running 后、submit 前 dispatcher 被取消——已解决（v3.4）**：`_dispatch_job_session` 在 bind→submit 段捕获 `asyncio.CancelledError`，执行 `mark_failed("dispatch_aborted")` + close job session 后重新抛出；`mark_failed` 的 CAS 保证并发已完成的 job 不会被覆盖。create 阶段被取消仍留给 60s requested 看门狗（此时没有可清理的 job session）。已加 mock 取消路径测试。
8. **watchdog timeout 的 cancel 时点**：`scan_stale` 先标 failed 再 interrupt；确认 `message.complete(interrupted)` 到达时按非 active 丢弃（现有语义）。
9. **`prompt.submit` RPC timeout 的不确定性**：服务端可能已受理。v3 初版按 `submit_error` 失败（与 v2 的 rpc_error 对齐）；P2 可复用 §4.4.3 的 active_list/messages 判定，把仍 running/已完成的 job 救回。`session.create` timeout 同理：按 create_error 处理，泄漏的空闲会话由 20s 孤儿回收。
10. **`session.close` 后能否 `session.resume`——已解决（v3.1 复核）**：`/tmp/hermes_probe_session.log`（scenario C 显式 `session.close` 后 `session.resume` 成功，`message_count:3`、history 含 `Operation interrupted.`）与 `/tmp/hermes_probe_omit.log`（done 会话 close 后再次 resume，取回 4 条 messages）均验证。close 结束会话行，resume 以 stored id 重建；D14 的审计/补取声明成立，无剩余 P0。

**暂缓验证项（2026-10-17 决定：仅记录，当前不实施，不阻塞上线）**：

| 项 | 当前状态 | 若未来实施 |
|---|---|---|
| 4090 容量实测 | 错误码/触发点已源码确认；本机 `max_concurrent_sessions` 未配置，未跑并发打满 | 测试实例配 cap 后跑 device+ask+N job 并发，按 §4.3.2 算术反推 relay 配额 |
| approval 型 waiting 恢复 | clarify 分支已真机验证并修复 request_id；本机 terminal 自动放行，构造不出 pending approval | 收紧 approval 策略后重复「pending → 断连 → resume → deny all」 |
| compression 轮换 | 未观察到 `stored_session_id` 轮换；代码只更新内存映射，不回写 JobStore | 若确认轮换，新增 active 状态 `update_external_ref` 并持久化 |
| transcript `"Error:"` | 格式已源码确认（`server.py:10986-10993`）；真机未能注入 backend error | 测试实例强制 provider 4xx/5xx，核对 history 最后一条 assistant |

> 已知非严重缺口（不阻塞上线）：D17 recheck 自身在 `JobStore.get` 抛异常时不会重新 arm；若在完成事件已被消费后又连续遇到存储故障，该 job 可能等到下次 WS 重连/48h 看门狗才收敛。

---

## 10. 结论

- 以“**独立 job 会话**”替代 `prompt.background` 是 v0.20.2 上唯一能同时满足 create / cancel / steer / 状态 的确定性方案（§3.2/§3.3）。
- 复用现有 JobStore/Deliverer/JobStatus/配额/补投/审批兜底，只新增一个 `bind_external_ref` 小 API 与 adapter 内部映射；**无 schema/wire/客户端改动**。
- 关键耐久性已真机验证：运行中 job 会话跨 22s 断连仍存活，resume 后新连接收到 complete；离线期间完成的 job 可从 resume messages 恢复并补投。
- 不升级 Hermes；**session 是唯一 job 载体**，`prompt.background` 代码/配置/测试已删除（D12）。
- ✅ **进程留存已定**：产品确认目前 job 不需要常驻 server/watcher；job 终态 `session.close` 清理 tracked 后台进程（v0.20.2 `AIAgent.close()`）是预期行为，v3 不提供进程留存能力。
- P1–P6 已实施、测试通过（§5.9）；无灰度开关。
- P0 待办：4090 容量实测（错误码与 cap 作用域已由源码确认）、approval 型 waiting、compression 轮换、transcript `"Error:"` 触发验证；steer 返回契约（§9-Q6）、clarify 型 waiting 恢复与 `"Operation interrupted."` 已真机确认，close 后 resume 已由探针确认（§9-Q10），恢复兜底保留 D17 一次性复查。
