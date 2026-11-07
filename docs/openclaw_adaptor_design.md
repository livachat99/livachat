# OpenClaw Adapter 交互设计 — Relay 自建 Job 会话（B 方案）

> **状态**：设计稿（待评审）
> **日期**：2026-10-14
> **前置文档**：
> [`docs/openclaw_websocket_api.md`](./openclaw_websocket_api.md)（OpenClaw WS 协议调研 + §9 job 生命周期补充调研，本稿的协议事实全部源自该文档的 [实测]/[源码]/[官方文档] 证据）
> [`docs/hermes_adaptor_design_v2.md`](./hermes_adaptor_design_v2.md)（hermes 交互设计 v2；本稿结构参照，术语对齐）
> [`docs/fd_agent_relay_service_v2.md`](./fd_agent_relay_service_v2.md)（relay device 级会话与 job 可靠投递：JobStore/Deliverer/补投）
> **范围**：`services/agent_relay/`（`adapters/openclaw.py` 实现 + `config.py` / `session_manager.py` / `job_store*.py` / `delivery.py` / `job_status.py` / `application.py` 小改 + `prompts/`）+ OpenClaw 侧部署配置（非本仓库代码）
> **B 方案一句话**：job 不由 agent 侧 `sessions_spawn` 派生，而是 **relay 自建 job 会话**（`sessions.create`）并 `chat.send` 派发任务，以 `runId`（= 提交时的幂等键，relay 掌控）为关联键，订阅该会话 `chat` 事件收终稿，`sessions.abort {runId}` 取消。

---

## 0. 关键决策（定稿）

| # | 决策点 | 选择 | 依据 |
|---|--------|------|------|
| D1 | 对接通道 | **根路径 WS 协议**（JSON 帧 req/res/event），非 HTTP OpenAI 兼容面 | HTTP 面"等于 owner 全量权限"且无会话/事件/审批能力（协议文档 §7） |
| D2 | 认证路径 | loopback + `client.id="gateway-client"` + `mode="backend"` + role `operator` + 共享 token；scopes `[operator.read, operator.write]`（审批兜底再 +approvals/questions） | 免 device 身份/免配对/scope 保留，已实测（协议文档 §2.2）；自定义 backend id 无效 |
| D3 | 连接粒度 | **每 OpenClaw 实例一条 WS**（懒建+退避重连），device 维度只体现在 sessionKey | 订阅式事件分发；无 hermes 的 per-device 连接/20s 孤儿窗口问题（协议文档 §8.2） |
| D4 | job 载体 | **B 方案：relay 自建 job 会话**（`sessions.create` + `chat.send`），**不用** native subagent（路径 A） | A 无外部入口：公开方法面 485 条无 spawn RPC，`AgentParamsSchema=closedObject` 无 runtime 字段（§3.2） |
| D5 | 完成检测 | 订阅 job 会话 `chat` 事件，**按 `runId`（= 提交时的幂等键，源码核实）精确关联**；无 hermes 式 straggler 问题 | `chat` 事件公共字段含 `runId`（协议文档 §5.2）；`runId = idempotencyKey`（§3.1） |
| D6 | 取消（新增能力） | watchdog 超时 → `sessions.abort {runId}` 后收敛 failed(timeout)；对账确认无法收敛 → failed(interrupted) | 协议文档 §9.2；hermes 无取消能力是净缺口 |
| D7 | 契约注入 | **NUDGE 自包含**：契约块随每次 nudge 载荷下发；AGENTS.md 部署为可选增强，**不作依赖** | `sessions.create` 无 `messages` 种子参数、`chat.send` 无 `extraSystemPrompt`（源码 schema）；自包含免疫压缩/漂移/重启 |
| D8 | 会话键 | 确定性键：监听 `livachat-dev-<device_id>`、job `livachat-job-<job_key>`、reactive `livachat-req-<device_id>`；**不建映射表** | 键即恢复凭据（对比 hermes `hermes_device_sessions` 表可省） |
| D9 | 推送 | 复用 `PushMessage`/`PushPublisher`/`JobResultDeliverer`/`JobStatusNotifier`，wire 协议零改动；`agent_name="openclaw"` | 协议文档 §8.4；app 端 `AgentJob`/ai_text 已有多 agent 字段 |
| D10 | 保活 | 服务端 `tick`（30s）静默 > `2×tickIntervalMs` 判死重连 + 连接级 `seq` 空洞即重连；**不做** hermes 式 `tools.list` 探测 | tick/seq 是协议一等机制（协议文档 §3.3/§2.4），半死检测等价而更简单 |
| D11 | 多 adapter 共存 | `JobRecord` 增 `agent_name`（sqlite 列已存在）；deliverer 按 record 归属分发；notifier 单实例按 record 标注 | 现 `agent_name` 在 `_to_row` 硬编码 `"hermes"`（job_store_sqlite.py:649） |
| D12 | 重启对账（净增益） | relay 启动/WS 重连时对 running job 做 `sessions.describe` + `chat.history` 对账，已完成的可**从持久 transcript 取回结果补投** | 会话/transcript 服务端持久（协议文档 §8.2）；hermes 为 at-most-once（其 §9 Q12） |

---

## 1. 背景与问题

### 1.1 现状

- `services/agent_relay/adapters/openclaw.py` 是 Phase-1 stub（33 行）：`ask()` 抛 `NotImplementedError`，配置 `api_url: http://localhost:9000` 为占位（本机并不存在该服务）。
- `loader.py:10-13` 注册表已含 `openclaw`，但 `default_adapters` 仅启用 hermes（`configs/dev/server.yaml:282`）。
- hermes adaptor v2（`hermes_adaptor_design_v2.md`）已建成完整参照系：proactive 监听（ChatlogWatcher nudge + 契约 JSON）＋ relay 两阶段派生 job（`prompt.background` → `background.complete`）＋ JobStore 48h TTL / 补投 / 看门狗。relay 侧通用基础设施（JobStore、JobResultDeliverer、ChatlogWatcher、AgentSessionManager、PushPublisher）全部 adapter 无关或仅轻度耦合。
- OpenClaw 协议调研（`openclaw_websocket_api.md`）及其 §9 补充调研已完成：认证路径实测打通；job 载体三路径（A 原生 subagent / B relay 自建 job 会话 / B' agent+agent.wait）裁决为 **B**。

### 1.2 问题分析

给 LivaChat 增加 OpenClaw 作为 agent 后端的核心问题：**"job"在 OpenClaw 里没有 hermes 那种"由 relay 直接派生、task_id 直达 relay、完成事件自动回推"的现成通道**——

| # | 问题 | 说明 |
|---|------|------|
| 1 | 无 spawn RPC | 外部 operator 无法调 native subagent（§3.2 证据）；`sessions_spawn` 只是 agent 工具 |
| 2 | 无 system 种子 | `sessions.create` 无 `messages` 参数（hermes 的 mission 注入方式不可用） |
| 3 | 无 background.complete | 完成回执要走会话事件流（`chat` 事件按 `runId` 关联），且事件只投递给**订阅了该会话**的连接 |
| 4 | 事件不重放 | WS 断开期间的终稿事件丢失；但 transcript 持久可事后读取（对比 hermes 事件与任务都不可回溯） |
| 5 | 既有 stub 配置语义不对 | `api_url/api_key/max_tool_calls_per_turn` 既非 WS 也非真实端点 |

### 1.3 结论

**以 B 方案为核心实现 OpenClawAdapter**：把"job"落地为 **relay 自建的专用会话 + 一次 run**，用公开 RPC（`sessions.create` / `chat.send` / `sessions.messages.subscribe` / `sessions.abort`）拼出与 hermes `prompt.background` 生命周期同构、且**多出取消与可对账性**的 job 通道。proactive 监听链（watcher → nudge → 契约解析 → 派生）与 reactive `ask()` 一并实现，复用 hermes 已验证的交互骨架。

---

## 2. 设计目标与非目标

### 目标

1. **Proactive 监听（默认模式）**：per-device 监听会话 + ChatlogWatcher nudge；契约三响应 `noop / job_request / message`（与 hermes 完全同构，共享解析器）。
2. **Job 全生命周期（B 方案）**：创建 → 运行 → 完成/失败 → 投递，全部走 relay 既有 JobStore 管线（48h TTL、done 补投、看门狗）；**新增取消**（`sessions.abort`）。
3. **Reactive `ask()`（配置开启）**：独立设备会话，`chat.send` 取终稿。
4. **可靠性**：单连接 + 断线重连（tick 静默/seq 空洞判死）+ 订阅重建 + 启动/重连对账；对账可取回已完成 run 的结果（持久 transcript）。
5. **零 wire 改动、hermes 双开兼容**：`agent_name` 显式化（D11），推送/状态协议不变。

### 非目标（本期不做）

- **native subagent（A 方案）**：外部无入口，且其"结果 announce 回父会话"的语义与 relay-facing job 的确定性要求不匹配（协议文档 §9.3）；
- **B'（`agent`+`agent.wait`）**：`agent.wait` 只回终态元数据、终稿仍需读 transcript（源码 `projectAgentJobObservation` 无正文）——ask 与 B 共用会话机制保持一致性，不引入第二套；
- 用户侧"更改 job"（steer）产品入口——协议能力保留，V2 按需（§4.4.4）；
- 跨机部署（relay 与 OpenClaw 不同机 → device identity + pairing / TLS，退路已记录）；
- TTS、多 OpenClaw 实例、ACP 执行器（`runtime:"acp"`）、`analyze_recording()` 真实现（沿用 base 默认 no-op，hermes Q11 同款）。

---

## 3. 调研结论（可行性支撑）

### 3.1 协议事实（引自 openclaw_websocket_api.md，均为 [实测]/[源码] 级）

- 握手：challenge → connect → hello-ok（协议 v4）；本机 2026.10.3，`ws://127.0.0.1:18789`，token 认证；
- 单连接 + 订阅分发；事件连接级 `seq` 单调、空洞即重连；`tick` 30s；`maxPayload` 25MiB；
- `chat.send` ack `{runId, status:"started"}`；`idempotencyKey` 必填；**ack 的 `runId` 即客户端提交的 `idempotencyKey`**（[源码] `chat-send-handler-Bw_pz541.mjs:3762` `clientRunId = p.idempotencyKey`；事件广播同源 :598-664）→ **关联键完全由 relay 掌控**；网关按 `chat:{runId}` 记 dedupe（同键重发命中缓存，:601-608）；
- `chat` 事件五态（status/delta/final/aborted/error），公共字段 `runId/sessionKey/agentId?/spawnedBy?/seq`；
- `sessions.messages.subscribe {key, agentId?, includeApprovals?}` ack `{subscribed, key, agentId}`；`unsubscribe` 需 key；
- `sessions.abort {key?, runId?, clearQueued?}`：带 runId 精确取消该 run 及其子孙；key 级也请求级联；
- `sessions.create`：`key` / `idempotencyKey` / `parentSessionKey`（+`spawnDepth` 谱系）/ `message` 可选首轮；无 `messages` 种子；
- 审批：`exec.approval.*` / `question.*`（事件 + resolve），scopes `operator.approvals` / `operator.questions`。

### 3.2 Job 载体裁决：采纳 B（A 不可行的决定性证据在此）

| 路径 | 裁决 | 证据 |
|------|------|------|
| A 原生 subagent | **不可行（外部面）** | [源码] 公开方法面 485 条无任何 `*.spawn`/`*.subagent*` 方法；`AgentParamsSchema = closedObject({...})` 无 `runtime` 字段（`dist/src-l2dWvEsN.mjs`）→ 外部 operator 无法经公开 `agent` RPC 起 `runtime:"subagent"` run。[联网] 交叉核对（OpenClaw-Book / 官方文档）：`sessions_spawn` 内部走**进程内 router** 的受信通道（"Agent tools running inside a Gateway dispatch requests directly through that Gateway's router"，官方 protocol.md），非 WS 公开面 |
| B relay 自建 job 会话 | **采纳** | 全部构件为公开 RPC（`sessions.create`/`chat.send`/`subscribe`/`abort`），`operator.write`/`read` 即可；runId 确定性回执；取消能力存在 |
| B' agent+agent.wait | 记录不采纳 | [源码] `projectAgentJobObservation`（`dist/agent-job-BbqHn5HJ.mjs:380`）返回 `status/error/terminalReply?...` 等终态元数据，正常成功 run 无正文文本（`terminalReply` 仅 message-tool 边缘场景）→ 终稿仍需读 transcript |

### 3.3 codebase 复用资产（零改或小改）

| 资产 | 位置 | 复用方式 |
|------|------|---------|
| 适配器接口 | `adapters/base.py:69-163`（`ask`/`nudge`/`on_session_start/end`/`set_push_callback`） | 不变；openclaw 实现同一接口 |
| JobStore 状态机 + 48h TTL + 补投 | `job_store_sqlite.py`（589 行，含 CAS 迁移/守望扫描/device 索引） | 零改动复用；仅加 `agent_name` 字段（§5.3） |
| 投递收口 | `delivery.py:25-104`（per-device 锁 + 确定性 `message_id=msg_agent_job_{job_key}` + 补投 sweep） | 复用；`agent_name` 改为按 record 读取 |
| Chatlog 监听 | `chatlog_watcher.py`（完全 adapter 无关，构造入参含 `adapters` 列表） | 复用；参数来源调整（§5.3） |
| 契约解析 | `hermes.py:1546-1632`（`_parse_contract` 纯函数 + 引号修复） | **抽取共享模块**，两 adapter 共用（契约同构） |
| 会话管理路由 | `session_manager.py:108-249`（lifecycle/utterance/watcher 装配） | 复用；watcher 参数来源调整 |
| 推送协议 | `models.py:50-68`（`PushMessage` 含 `agent_name/agent_id/proactive/job_key`） | 零改动 |
| 看门狗 | `hermes.py:244-266`（`scan_stale` 驱动） | openclaw 自建同构 watchdog（带 agent 过滤，§5.2） |

### 3.4 与 hermes 的关键差异（设计输入）

| 维度 | hermes（现状） | OpenClaw（本设计） |
|------|---------------|-------------------|
| 连接粒度 | 每 device 一条 WS（`_DeviceConnection`） | **单条 WS**（实例级） |
| 会话生命周期 | 服务端会话随连接可能被 reap（20s 窗口） | 会话/transcript 持久，无窗口约束 |
| job 派生 | relay 调 `prompt.background`，task_id 回执 | relay 建 job 会话 + `chat.send`，runId 回执 |
| 完成检测 | WS 事件 `background.complete`（按 task_id） | 订阅 `chat` 事件（按 runId，精确） |
| 取消 | 无 | `sessions.abort {runId}`（含级联子孙） |
| 重启对账 | at-most-once，事件丢失不可恢复（其 §9 Q12） | `sessions.describe`+`chat.history` 可读回已完成结果 |
| 保活 | 应用层 `tools.list` 探测（§5.8） | 服务端 `tick` 静默判死（协议内建） |
| mission 注入 | `session.create(messages=[system])` 种子 | NUDGE 自包含（无种子通道） |

---

## 4. 总体设计

### 4.1 架构与数据流

```
 LivaChat Server 进程（relay 与 gateway 同进程）
 ┌─────────────────────────────────────────────────────────────────────┐
 │ orchestrator ─► AgentEventPublisher ─► InProcessEventBus ─► AgentSessionManager
 │ recording ─► chat/history.jsonl（/opt/lvdata/...）        │   │
 │                                                           │   └─ ChatlogWatcher（共用，device 维度）
 │                    proactive: nudge()                     ▼
 │                  ┌──────────────────── OpenClawAdapter（新增，单 WS）──────────┐
 │                  │  监听会话 livachat-dev-<dev>  ← nudge（自包含契约）          │
 │                  │  契约 job_request ─► JobStore.create(agent_name=openclaw)   │
 │                  │      └─ sessions.create(livachat-job-<jkey>) ─► subscribe  │
 │                  │      └─ chat.send ─► ack{runId} ─► mark_running              │
 │                  │  chat 事件(runId) final/error ─► mark_done/failed ─► 投递    │
 │                  │  watchdog ─► sessions.abort {runId}（超时取消）              │
 │                  │  启动/重连 ─► describe+history 对账（可回收已完成结果）       │
 │                  └───────────────┬──────────────────────────────────────────┘
 │                                  │ 单条 WS（退避+jitter 重连；tick/seq 判死）
 │  JobResultDeliverer ─► PushPublisher ─► conn handler ─► 设备（ai_text/    │
 │  JobStatusNotifier  ─► PushPublisher                  job_status）      │
 └──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                 OpenClaw Gateway :18789（root WS，JSON req/res/event）
                   ├─ 监听会话：旁听 chatlog + 契约应答（noop/job_request/message）
                   ├─ job 会话：执行任务（读文件/调 MCP/工具），产物=终稿文本
                   └─ MCP client ─► lvdata-mcp（部署注册，§6）
```

### 4.2 会话模型与键规划

会话键 = `agent:<agent_id>:<key>`（`agent_id` 来自配置，默认 `main`）。**确定性键 = 天然恢复凭据**（D8），无映射表、无 resume 逻辑：

| 会话 | 键 `<key>` | 生命周期 | 契约语境 |
|------|-----------|---------|---------|
| 监听会话（per device） | `livachat-dev-<device_id>` | 懒建（首次 nudge/ask），持久（不随 chat session 关闭） | 契约三响应（monitor 角色） |
| Job 会话（per job） | `livachat-job-<job_key>` | 创建于 job 派发，终态后自然闲置（不主动删） | 任务说明（自包含） |
| Reactive 会话（per device） | `livachat-req-<device_id>` | 懒建，持久 | 无契约（普通问答） |

- 监听/parent 关系：job 会话创建时带 `parentSessionKey=<监听会话键>`（+`spawnDepth:1`）以形成会话树谱系（导航/UI 归属；**不依赖** announce——那是 native spawn 的机制）。外部 operator 传 `spawnDepth` 的接受度未实测（协议文档 §9.4-2），列为 P0-Q1；不通过则去谱系字段，功能不受影响。
- 创建一律 `sessions.create {key, agentId?, idempotencyKey}`；同键即 adopt（幂等），崩溃重启后无孤儿会话。
- **agentId 纪律**：键规划采用裸键 + `agentId` 参数（create/subscribe/abort/chat.send 等 schema 均提供该参数），多 agent 部署下显式路由、避免落入默认 agent；服务端返回的规范全键（`agent:<id>:<key>`，subscribe ack/describe）作为后续调用的对齐基准。
- **会话累积注记**：job 会话终态后保留（不主动删）——与 hermes 的差异是**每个 job 留下一个持久会话**。v1 靠 OpenClaw 侧归档/运维 `sessions.delete`（admin）管理；长期保留策略随 P0-Q8（归档语义）一并定。
- 设备维度不复用设备名：relay 只按 `device_id`（客户端 UUID）派生键。

### 4.3 监听契约（NUDGE 自包含）

**注入方式论证**（D7）：

| 方案 | 可行性 | 取舍 |
|------|--------|------|
| a. `sessions.create` messages 种子（hermes 方式） | ✗ | schema 无 `messages` 参数（源码 `SessionsCreateParamsSchema`） |
| b. 首轮 bootstrap turn 注入契约 | △ | 多一轮成本；长会话压缩/漂移风险；重启后监听会话仍在但契约未被重申 |
| c. **NUDGE 自包含（采纳）** | ✓ | 每次 nudge 载荷内嵌契约块（~1KB）+ 数据；免疫压缩/重启/漂移，relay 侧完全自治 |
| d. AGENTS.md 部署注入（可选增强） | ✓ | 作为系统提示层强化（dedicated agent workspace）；**不依赖**部署项即可工作 |

**NUDGE 载荷**（`chat.send.message`，两段式：契约文本 + 数据 JSON）：

```
（契约块全文，~1KB，由 prompts/openclaw_monitor.md 渲染：角色/三种响应/禁止项/
  JSON 引号规则/lvdata 指引/上下文自服务/时区段/昵称段/webroot 段）

{"type":"chatlog_update","chatlog_path":"/opt/lvdata/dev-xxx/rec-yyy/chat/history.jsonl",
 "chat_session_id":"cs-zzz",
 "new_lines":[{"role":"user","text":"下周去东京出差，帮我看看机票"},
              {"role":"assistant","text":"好的，几号出发？..."}],
 "truncated":false,
 "outstanding_jobs":[{"job_key":"jk-1","task_id":"run-abc","summary":"在查东京机票"}],
 "timezone":"Asia/Shanghai"}
```

- 布局理由：契约**不嵌套进 JSON 字符串**（避免大段 markdown 的转义脆弱性），数据段保持纯 JSON 以沿用 hermes 的载荷语义；两段由 relay 拼接，不引入解析歧义。
- 契约块内容与 `hermes_monitor.md` 对齐（三种响应 + 禁止项 + JSON 引号规则[`hermes_monitor.md:27-29` 的教训直接继承] + lvdata 指引 + 上下文自服务 + 昵称段/时区段/webroot 段渲染规则）；
- `truncated=true`（超出 `nudge_max_lines/nudge_max_bytes`）提示自行读文件；
- `outstanding_jobs` 取 JobStore device 维度（`outstanding_jobs_for_device`，与 hermes 相同）；
- 契约解析：抽取共享的 `_parse_contract`（hermes.py:1546）→ `agent_relay/contract.py`，解析失败/未知 action 一律 noop + `contract_parse_error` metric（绝不回推）。
- 模板文件：`prompts/openclaw_monitor.md`（契约块与段渲染）；任务书 `prompts/openclaw_background_task.md`（§4.4.2）。

### 4.4 后台 Job 生命周期（B 方案）

#### 4.4.1 创建（两阶段派生，与 hermes 同构）

```
主 agent 回 {"action":"job_request", spec, summary}
  → relay 校验：spec 非空；per-device 在途 < max_concurrent_jobs_per_device；
    全局在途 < max_in_flight_jobs（超限按 noop + job_rejected，同 hermes.py:1377-1388）
  → JobStore.create(session_id, device_id, summary, agent_name="openclaw")
     job_key = uuid4().hex
  → sessions.create {key:"livachat-job-<job_key>", parentSessionKey:<监听会话键>,
                     spawnDepth:1}                              ← 不传 idempotencyKey
                    （P0-Q1：后端路径带 idempotencyKey 会被拒；同 key 重创建即 adopt，幂等成立）
  → sessions.messages.subscribe {key} → 等 ack（先订阅后发送，杜绝漏帧）
  → 注册 run wait future（key = 幂等键，发送前注册，hermes._register_turn 同款）
  → chat.send {sessionKey, message:<任务书渲染>,
               idempotencyKey:"livachat:job:<job_key>"}        ← 确定性幂等键 = runId
     ack {runId} → JobStore.mark_running(job_key, runId)        ← runId 落 hermes_task_id 列（§5.3）
  →（notify_job_started 时）yield chunk(summary) 即时告知用户
```

- **返回字段**（P0-Q1 实测）：create 结果 `{ok, key(规范键), sessionId, entry, runStarted, resolved}`——以 `payload.key` 为后续对齐基准；subscribe ack 亦返回规范全键。
- **runId = 幂等键**（D5/§3.1）：`runId` 在发送前已知，故 run wait future 在 `chat.send` 之前注册（发送前注册的镜像模式见 hermes.py:1926）——**ack 丢失（超时/断连）不破坏关联**，直接等事件即可。
- **幂等键全局唯一约束**：网关 dedupe 键为 `chat:{runId}`（无 session 维度，chat-send-handler:601-608）——job 键含 `job_key`、nudge/ask 键含 device+uuid，均不发生跨会话撞键。

失败语义（任一步异常 → `mark_failed`，与 hermes 同构）：

| 失败点 | 处置 |
|--------|------|
| sessions.create 报错 | failed("create_error") + unsubscribe（未订阅则跳过） |
| subscribe 报错 | `chat.send` 前中止 → failed("subscribe_error") |
| chat.send 报错 | failed("rpc_error") + unsubscribe（runId 已预注册，弹出 wait） |
| mark_running CAS 失败（看门狗已收敛） | 放弃（该 run 完成事件到达时按非 running 丢弃，同 hermes） |

**"请求前崩溃"窗口**：create 与 mark_running 之间 relay 崩溃 → job 停留 `requested`，由启动对账按 §4.4.5-3 的幂等重发处置（P0-Q6 验证 dedupe 语义）；未通过则回退 hermes 现状语义（60s 看门狗 → failed(orphaned)）。

#### 4.4.2 执行与完成

- 任务书（`openclaw_background_task.md`）与 hermes 版（`background_task.md`）同构：任务说明 + chatlog 路径 + 时区/昵称 + lvdata MCP 指引 + webroot 段 + "无人值守，勿等人工审批" + 输出要求（成品文案；不打扰输出字面量 `NOOP`）。
- 完成检测（reader 内）：`chat` 事件按 `runId` 路由——
  - `state:"final"` → 结果文本 = 事件 `message` 快照提取（优先）∨ 累积 deltas（缺省）；`mark_done(job_key, text)` → `deliverer.deliver(job)`（复用 D13 收口）；
  - `state:"error"` / `state:"aborted"` → `mark_failed("run_error"|"aborted")`（不投递、不推送，hermes parity）；
  - `NOOP` 字面量 → `mark_done()` + `mark_delivered()`（视为已消费，D14 语义沿用）；`result_text` 为空则不投递。
- 终稿文本提取细节（`message` 快照结构 / delta 合并 replace 语义）列为 P0-Q2 实测。
- 完成后 `unsubscribe` 该 job 会话（释放订阅）；订阅集有界 = 在途 job 数（≤20）+ 设备数（监听/reactive 会话）。

#### 4.4.3 取消（新增能力）

| 场景 | 动作 |
|------|------|
| 看门狗 `running` 超时（`background_timeout_s`，默认 48h 对齐 R2） | 先 `sessions.abort {runId}`（尽力，失败仅告警）→ `mark_failed("timeout")` + metric |
| relay 启动/重连对账确认无法收敛（§4.4.5） | `sessions.abort {runId}`（尽力）→ `mark_failed("interrupted")` |
| 取消已收敛 job（重复取消） | `sessions.abort` 幂等（对不存在/已终态 run 返回 no-active-run）；JobStore CAS 保证只迁一次 |

- openclaw 自建 watchdog 循环（与 `hermes.py:244-266` 同构）：`sqlite_job_store.list_active()` 过滤 `agent_name == "openclaw"` 后按超时规则收敛；**与 hermes 的全局扫描不冲突**（hermes 版扫描全量属存量行为，双开时先按 agent 过滤改造 hermes 版——列入 §5.3 小改）。
- 不新增"用户主动取消"入口（hermes 无此产品面；LivaChat 客户端无对应交互）。

#### 4.4.4 更改（steer）能力清单与暂不产品化

协议具备的更改手段（记录备用，V1 不建链路——遵守"宁删功能不加新链路"）：

| 手段 | RPC | 用途（V2 条件） |
|------|-----|----------------|
| 对运行中 job 注入修正 | `chat.send {queueMode:"steer"}` | 用户打断语义（hermes §9 Q9 的对偶问题）产品化时 |
| 排队续轮 | `chat.send {queueMode:"followup"}` | 多轮任务 |
| 对 paused 子会话续投 | `sessions.send {key, message}` | native subagent 场景才需要（本设计不产生 pause） |
| 元数据 | `sessions.patch {label}` 等 | 运维 |

#### 4.4.5 启动/重连对账（D12 净增益）

触发：`connect()` 启动时 + 每次 WS 重连成功后。输入：JobStore `agent_name=openclaw` 的 active 记录。步骤（逐 job，有界并发）：

```
1. 重订阅：sessions.messages.subscribe {key:"livachat-job-<job_key>"}（事件恢复前提）
2. 对账 running job（判定用 sessions.describe；P0-Q3 实测字段）：
   a. sessions.describe {key} → 若 hasActiveRun=true 且 settle 重试后仍 true →
      保持 running（事件流已恢复，正常收敛）
   b. hasActiveRun=false → 看 lastRunId：
      · lastRunId == job.runId 且 status=="done" → 读 chat.history 末条 assistant
        （content 块数组取 text 块）→ mark_done(文本) → deliverer.deliver()（补投）
      · lastRunId == job.runId 且 lastRunError 非空 → mark_failed("run_error")
      · lastRunId 不匹配/缺失 → 等待 settle（describe 短重试）后仍不决 →
        mark_failed("interrupted")（fail-fast，不占 48h 看门狗）
3. 对账 requested job（**runId 已知 = 该 job 的幂等键**）：**幂等重发**
   `chat.send {同 idempotencyKey}`——P0-Q6 实测：运行中/结算中 → `{status:"in_flight"}`
   不产生重复 run；已结态 → `{status:"ok"}` 缓存终态；两种情况都安全采纳
   （返回 in_flight → mark_running；返回终态 → 走步骤 2b 判定）。
```

- 该步骤正是 hermes at-most-once 缺口的补偿路径：**结果在持久 transcript 里，读回即可补投**。
- `chat.history` 尾行判定细节（assistant 行结构、error run 的 transcript 形态）→ P0-Q3 实测后固化。

#### 4.4.6 JobStore 状态机（复用，无新增状态）

```
requested ──(chat.send ack, runId)──► running ──(chat final)──► done ──(deliver)──► delivered
    │                                    │
    ├─(60s 未转 running)─► failed        ├─(chat error/aborted)─► failed(run_error|aborted)
    │   (orphaned)                       ├─(watchdog timeout → abort尝试)─► failed(timeout)
                                         └─(对账不可收敛 → abort尝试)─► failed(interrupted)
```

### 4.5 阻塞交互兜底（无人值守）

与 hermes §4.5 同构，事件源改为 OpenClaw 的审批/提问面：

| 事件 | relay 应答 | scope | 说明 |
|------|-----------|-------|------|
| `exec.approval.requested` | `exec.approval.resolve {id, decision:"deny"}` | `operator.approvals` | 无人值守默认 deny（与 hermes approval.respond deny 对齐）；参数 schema [源码] `ExecApprovalResolveParamsSchema = {id, decision, reviewer?, grantExpiresInDays?}`，`decision` 取值集待 P0-Q7 实测补全 |
| `question.requested` | `question.resolve {id, answers:{answers:{<questionId>:["…"]}}}`（或 `{id, cancel:true}`） | `operator.questions` | 参数 schema [源码] `QuestionResolveParamsSchema`（union）；文案沿用 `_CLARIFY_AUTORESPONSE`（hermes.py:94）精神："无需澄清，按最佳判断继续" |
| 其它 `*.approval.*` | 记录日志/metric，不主动 resolve | — | 清单按实测补全（P0-Q7） |

- **第一道防线在部署侧**（同 hermes §6.2）：OpenClaw agent 的 `permissionMode` / exec approvals 策略 / 工具策略收窄，让 job 尽量不产生审批；
- 订阅面选择（P0-Q7）：job 会话订阅加 `includeApprovals:true`（事件精确归属该会话）vs 依赖广播 `exec.approval.requested`（需 `operator.approvals`，且与设备无关的全局流）。设计倾向后者为主（无需 admin/配对语义不确定性——官方文档对 includeApprovals 附加了"admin 或 paired device 的 approvals"条件，backend 路径下是否成立未实测）。

### 4.6 Reactive 模式（`ask()`）

- `mode: "reactive" | "proactive" | "both"`，默认 `proactive`（与 hermes 对齐）。
- `ask(text, ctx)`：`_ensure_ready()`（连接 + reactive 会话 `livachat-req-<device>` 确保 + 订阅）→ per-device 锁 → `chat.send {message:text, idempotencyKey:uuid}` → 等该 runId 的 `chat` final（`timeout_s`）→ yield 全文 chunk（`is_final=True`）。
- 超时 → `sessions.abort {runId}` + 抛 `AdapterTimeoutError`（管理侧记 timeout metric）；连接层异常抛 `AdapterConnectionError`（`ask` 的 retry 包装与 hermes.py:1439-1490 同构）。
- 契约隔离：reactive 会话无契约语境（不与监听会话混用），避免"用户原话被 monitor 契约要求回 JSON"。
- `mode="both"` 时同一连接/同一 agent 上并行：监听 nudge 与 reactive ask 各自会话、各自锁。

### 4.7 推送语义与客户端约定

- job 结果：`PushMessage(agent_name="openclaw", agent_id=<nickname>, proactive=True, job_key=<job_key>, message_id="msg_agent_job_<job_key>")` → `PushPublisher` → `ai_text`（wire 零改动；设备离线保持 done 走补投）；
- 即时通知（`notify_job_started`）与 `message` 动作的 nudge 回复：走既有 watcher→`_on_adapter_push` 路径（需 live session，语义同 hermes）；
- `job_status` 快照：`JobStatusItem.agent_name` 按 record 标注（§5.3），app 端 `AgentJobListPanel` 无需改动；
- 与主 LLM 播报的展示协调（hermes §9 Q8）对 openclaw 同样成立，属客户端产品决策，不在本稿。

### 4.8 连接与保活（单连接）

- **懒建**：首次需要（nudge/ask/启动对账）时建连；`connect()` 本身只启动 watchdog + 对账任务（hermes.py:203-222 同款骨架）。
- **重连**：指数退避 + jitter（复用 `reconnect_backoff_initial_s/max_s/jitter` 语义）；重连成功后触发 §4.4.5 对账 + 订阅重建。
- **半死判死（D10）**：跟踪最近入站事件时刻（任何事件/tick）；静默 > `silence_timeout_s`（默认 75s = 2×tickIntervalMs(30s) + 余量；tickIntervalMs 运行时以 `hello-ok.policy` 校正）→ 主动关闭走重连；连接级 `seq` 空洞同样判死重连（协议文档 §3.3）。
- **在飞请求**：RPC pending futures 按 id 匹配（响应可乱序）；连接断开 → 全部以 `AdapterConnectionError` 失败弹出；nudge/ask 的 run wait futures 失败弹出（run 服务端继续，靠对账/下一轮 nudge 兜底）。
- 不做 per-device 连接 LRU/空闲 reaper（单连接无此面）。

---

## 5. Relay 侧改动

### 5.1 `config.py` — OpenClawAdapterConfig 重写

```python
class OpenClawAdapterConfig(BaseModel):
    ws_url: str = "ws://127.0.0.1:18789"     # 根路径 WS（协议文档 §1.1）
    token: str = ""                          # gateway.auth.token（共享密钥）
    agent_id: str = "main"                   # 会话键中的 agentId
    mode: Literal["reactive", "proactive", "both"] = "proactive"
    nickname: str = ""                       # agent_id(nickname) 透传（同 hermes）
    request_timeout_s: int = Field(default=30, gt=0)   # 单条 RPC 超时
    ask_timeout_s: int = Field(default=60, gt=0)       # reactive turn 超时
    nudge_timeout_s: int = Field(default=60, gt=0)     # 监听 turn 超时（hermes 对齐）
    connect_timeout_s: int = Field(default=10, gt=0)   # 握手（challenge→hello）超时
    silence_timeout_s: float = Field(default=75.0, gt=0)  # 入站静默判死（2×tick+余量）
    reconnect_backoff_initial_s: float = 1.0
    reconnect_backoff_max_s: float = 30.0
    reconnect_jitter: float = 0.25
    # -- job（语义与 hermes 同名键对齐）--
    max_concurrent_jobs_per_device: int = 10
    max_in_flight_jobs: int = 20
    background_timeout_s: int = 172800       # 48h，与 JobStore TTL 对齐（R2）
    watchdog_interval_s: int = 60
    job_requested_timeout_s: int = 60
    notify_job_started: bool = True
    max_stored_result_bytes: int = 16384     # 落库截断（对齐 max_reply_bytes）
    startup_recovery_enabled: bool = True    # 启动对账（§4.4.5）
    recovery_concurrency: int = 10
    job_parent_link: bool = True             # job 会话挂 parentSessionKey/spawnDepth 谱系
    monitor_prompt_path: str = ""            # 契约块模板覆盖（空 = 内置）
    task_prompt_path: str = ""               # 任务书模板覆盖
    # 审批兜底（§4.5）；空 = 不订阅/不应答
    approvals_autorespond: bool = True
    question_autorespond: bool = True
```

**watcher 参数来源调整（D11 配套）**：`session_manager._start_watcher`（session_manager.py:170-184）目前硬读 `cfg.adapters.hermes.*` 的 5 个 watcher 旋钮（poll/coalesce/min_interval/max_lines/max_bytes）。watcher 是 adapter 无关组件，双 adapter 下参数必须唯一。方案：**上移至 `ProactiveConfig`**（`proactive.chatlog_poll_interval_s` 等 5 键），hermes 侧旧键保留为 deprecated alias（`HermesAdapterConfig._apply_legacy_quota_alias` 模式先例，config.py:67-76）。

**scopes 推导**：连接时提交的 scopes 固定含 `[operator.read, operator.write]`；`approvals_autorespond` / `question_autorespond` 开启时追加 `operator.approvals` / `operator.questions`（backend 路径下请求的 scopes 原样保留，协议文档 §2.2；方法所需最小 scope 已核：`exec.approval.resolve`=operator.approvals、`question.resolve`=operator.questions）。

`configs/*/server.yaml` 示例（替换现占位段）：

```yaml
    openclaw:
      mode: proactive
      ws_url: "ws://127.0.0.1:18789"
      # ${} 占位符启动时由 loader 解析（configs 既有惯例）；值放仓库根 .env 的
      # OPENCLAW_AUTH_TOKEN（取自 gateway.auth.token；变量缺失时启动直接报错）
      token: "${OPENCLAW_AUTH_TOKEN}"
      agent_id: "main"
      nickname: ""                     # 空 = 不注入称呼段
```

### 5.2 `adapters/openclaw.py` — 新增实现（约 700-900 行，hermes.py 的骨架同构但显著更薄）

```
class _GatewayConnection:                # 单连接状态（对比 hermes 的 per-device _DeviceConnection）
    ws; ready: asyncio.Event; stop_flag
    pending: dict[str, Future]           # RPC 按 id
    run_waits: dict[str, Future]         # runId → 本地等待（nudge/ask turn）
    run_deltas: dict[str, str]           # runId → 累积 delta（有界，完成后清理）
    subscribed: set[str]                 # 已订阅会话键（断线后清空重建）
    seq_last: int | None; last_event_at: float
    handshake: challenge nonce/ts（连接期）

class OpenClawAdapter(AgentAdapter):
    name = "openclaw"
    __init__(cfg): reactive_enabled/proactive_enabled 按 mode；
        注入点（application wiring）：job_store / deliverer（getattr 模式同 hermes.py:1077）
    # 连接
    connect(): 启动 watchdog 循环 + 启动对账任务（不建连）；close(): 取消任务、关 ws
    _ensure_ready(): 懒建连 + 等 hello-ok；断线窗口等待 ready（超时抛 AdapterTimeoutError）
    _reader_loop(): connect → 收 challenge → 发 connect → hello-ok →
        循环分发：res→pending；event: chat/（others 忽略或计数）/tick 刷新 last_active；
        seq 空洞 → 关连接；静默超阈值 → 关连接；断线 → fail_all_pending → 退避重连
        → 重连成功后 create_task(对账 §4.4.5)
    # RPC 基建
    _rpc(method, params, *, timeout=None) → 任意 JSON-RPC 响应即活（error 响应抛
        AdapterConnectionError(code)，同 hermes 判活语义）
    # 会话确保（确定性键，无映射；create 不传 idempotencyKey——P0-Q1）
    _ensure_listening_session(device_id) / _ensure_reactive_session(device_id):
        sessions.create({key}) adopt 语义（返回 payload.key）→ subscribe（ack 后返回）
    _unsubscribe(key)
    # proactive
    nudge(update, ctx): 契约块渲染进载荷 → per-device 锁 → chat.send
        （queueMode:"followup"，P0-Q4 实测定夺）
        → runId → 等 run_waits（nudge_timeout_s；超时 → sessions.abort {runId}）
        → _parse_contract（共享）→ 分支：message → yield chunk
          （metadata={"agent_name":"openclaw"}，见下）；job_request → _dispatch_job
    on_session_start(ctx) / on_session_end(ctx): **no-op**（懒建/持久语义，D4 精神；
        订阅生命周期随连接管理，不随 chat session 结束回收）
    _dispatch_job(...): §4.4.1 七步；失败逐点 mark_failed
    _on_chat_event(runId, event): nudge/ask wait 唤醒；job 终态 → mark_done/failed → deliver
    # watchdog + 对账
    _watchdog_loop(): 同构 hermes.py:244；超时收敛前对 openclaw running job 尽力 abort
    _reconcile_openclaw_jobs(): §4.4.5（启动 + 重连后调用，有界并发）
    # reactive
    ask(text, ctx): §4.6
```

- **契约/任务书渲染**：`_render_nudge_contract()`（读 `prompts/openclaw_monitor.md` 渲染时区/昵称/webroot 段）+ `_render_background_task()`（`prompts/openclaw_background_task.md`，模板变量与 hermes 版对齐）。
- **推送归属（双开防错标）**：nudge 的 `message` 动作与 job-started 通知经 watcher→`_on_adapter_push` 推送，`AgentResponseChunk.metadata` 必须携带 `{"agent_name":"openclaw"}`——否则 `session_manager.py:386` 的默认回退会把推送标成 `hermes`（用户可见归属错误）。测试用例覆盖。
- **订阅补订**：`_ensure_listening_session` / `_ensure_reactive_session` 在 `subscribed` 集不含该键时先补订（ack 后再发送），覆盖 WS 重连后的首批 nudge/ask。
- **`sessions.create` 会话不存在即创建的事实**：`displayName`/`label` 可选（job 会话建议 `label=summary[:N]` 便于运维浏览，可选）。
- **queueMode（nudge/ask 发送）**：hermes 靠 per-session 锁串行 + 攒批，本身不会并发提交；OpenClaw 侧显式传 `queueMode:"followup"`（排队）避免默认 `steer` 打断自身在飞 turn。实测定夺（P0-Q4）。

### 5.3 共用组件最小改动清单

| 文件 | 改动 | 理由 |
|------|------|------|
| `job_store.py` | `JobRecord` 增字段 `agent_name: str = "hermes"`；`create(..., agent_name="hermes")` | D11；Redis 版与 sqlite 版接口保持一致（测试基座仍覆盖两实现） |
| `job_store_sqlite.py` | `_to_row` 的 `"agent_name": "hermes"`（:649）改为 `record.agent_name`；`_row_to_record` 读 `row["agent_name"]` | sqlite 列已存在（chat_store.py:79，默认 'hermes'），**零 schema 迁移** |
| （同上）`hermes_task_id` 列 | **不改名**，承载 openclaw 的 runId | 列注释更新为"外部关联键（hermes task_id / openclaw runId）"；改名需动 sqlite 存量库/PG 镜像/仓储/索引，命名收益不抵迁移成本（CREATE TABLE IF NOT EXISTS 无迁移框架） |
| `delivery.py` | `JobResultDeliverer`：投递时按 `job.agent_name` 取 `PushMessage.agent_name`、按构造注入的 `agent_ids: dict[str,str]`（name→nickname）取 `agent_id`；`agent_name/agent_id` 构造参数保留为**记录缺失时的回退**（存量测试/单 adapter 装配零改动） | 一个 deliverer 实例即可服务两 adapter（保持单实例 = per-device 投递锁覆盖跨 adapter 的同一设备，优于两实例双锁） |
| `job_status.py` | `JobStatusItem.agent_name` 取 `r.agent_name`（空回退构造默认） | 单实例扫描全量 job；两 adapter 的 job 各自带上归属（wire 不变） |
| `session_manager.py` | `_start_watcher` 的 5 个 watcher 旋钮改读 `cfg.proactive.*`（§5.1） | watcher 参数唯一化 |
| `job_store*.py` | `scan_stale` 增 `agent_name` 过滤参数；hermes watchdog 传 `agent_name=self.name` | 双开时各自只收敛自己的 job |
| `contract.py`（新增） | 从 hermes.py:1546-1632 抽出 `parse_contract()` / `repair_unescaped_quotes()` | 两 adapter 共用 |
| `prompt_hints.py`（新增） | 从 hermes.py 抽出 `timezone_hint()` / `agent_name_hint()` / `webroot_hint()` 三个提示文案函数（规范副本） | 两 adapter 的 prompt 必须同文，改动集中一处 |
| `metrics.py` | 复用现有 helper（均按 adapter label）；新增 `inc_gateway_reconnect()`、`inc_gateway_seq_gap()` 两个 counter | 单连接健康观测 |
| `application.py` | 接线扩展（§5.4） | — |

### 5.4 `application.py` 接线（application.py:155-260 扩展）

- `OpenClawAdapterConfig` 自然由 `load_all()` 注入（loader.py:24-26 现成映射）；
- job_store 注入：`openclaw.job_store = job_store`（与 hermes.py:190-199 同款 getattr 注入）；
- deliverer：构造改为 `JobResultDeliverer(job_store, publisher, relay_cfg, agent_ids={"hermes": hermes_nick, "openclaw": openclaw_nick}, on_delivered=hermes.maybe_reap_conn)`（on_delivered 仅 hermes 需要）；两 adapter 共享同一实例（`hermes.deliverer = deliverer; openclaw.deliverer = deliverer`）；
- notifier：`JobStatusNotifier(job_store, publisher, ...)`（去 agent_name 参数）；
- 其余（event_bus 订阅、watcher、push callback）无改动；
- agent 认知声明：**2026-10-18 已按 `docs/agent_relay_prompts.md` §8 更新**——LivaChat 声明按实际装载
  的 proactive adapter 集合 + nickname 生成；OpenClaw-only 使用通用单昵称模板，多助手使用多助手模板。
  monitor/任务书不注入同侪信息（决策 Q4=B），重复 message/job 风险接受。

### 5.5 `prompts/` 新增

- `openclaw_monitor.md`：契约块模板（三种响应/禁止/引号规则/lvdata 指引/上下文自服务/`$timezone_hint`/`$agent_name_hint`/`$webroot_hint` 段），内容与 `hermes_monitor.md` 对齐（同一契约语义，两个后端）。**契约文本是规范副本**——改动须同步 hermes 版（沿用 `prompts_improvements.md` 的"三处清单同步"约定）。
- `openclaw_background_task.md`：任务书模板（与 `background_task.md` 对齐：任务/执行指引/NOOP 输出规则）。

### 5.6 Metrics

复用全部现有口径（`inc_nudge`/`inc_job_event`/`inc_agent_reply`/`inc_job_store`/`inc_job_status`，均带 adapter label）；新增 `gateway_ws_reconnect_total` / `gateway_seq_gap_total`。契约违例、job 取消（abort 发出/成功）计入 `inc_job_event("openclaw", "cancel_sent")` 等新增 event 值（文档口径补录）。

### 5.7 测试计划

基座：新增 `services/tests/integration/agent_relay/mock_openclaw_server.py`（WS mock：challenge→connect→hello-ok；`sessions.create`（adopt 语义）/`chat.send`（ack + 可编排的 delta/final/error 序列）/`sessions.messages.subscribe` ack/`sessions.abort`/`sessions.describe`/`chat.history`；故障注入：吞请求、error 帧、seq 空洞、断连、慢响应）。参照 `mock_hermes_server.py` 的既有形制（`swallow_methods` 等开关复用模式）。

| 层 | 用例（对齐 hermes 测试面） |
|----|--------------------------|
| 单测 | 契约解析（共享模块，含引号修复/代码围栏/括号配平）；会话键构造；delta 累积与 replace 语义；对账决策表（describe/history 组合 → 处置）；config 校验与 alias |
| 集成 | ① nudge 全链路：watcher→nudge→契约 message→推送；② job_request→create→subscribe→send→final→done→delivered（含确定性 message_id 断言）；③ NOOP 字面量消费；④ job error/aborted → failed 不投递；⑤ 超时取消：吞 final → nudge_timeout → abort 请求发出；⑥ 每设备/全局配额拦截；⑦ 断线重连：订阅重建（mock 断言 re-subscribe）+ 在飞 run 弹出 + 对账 sweep；⑧ 启动对账两分支：运行中（保持）/已完成（history 回收 → 补投）；⑨ 请求前崩溃窗口（requested → orphaned）；⑩ 审批自动 deny/answer（mock 广播 requested 事件）；⑪ ask 超时→abort；⑫ 双 adapter 共存：job 归属（agent_name）与 hermes 用例全量回归 |
| 真机 | dev 环境：一通对话收到 openclaw 主动 ai_text；job 全链路（含 NOOP）；重启 relay 对账补投；半死模拟（kill -STOP mock/真机断开 ≥ 90s，越过 `silence_timeout_s` 75s 阈值）验证 tick 判死 |

---

## 6. OpenClaw 侧改动（部署配置，非本仓库代码）

1. **gateway token 获取**：`~/.openclaw/openclaw.json` → `gateway.auth.token`（本机已实测可用，协议文档 §9.1）；
2. **专用 agent（建议）**：为 LivaChat 建独立 `agents.entries`（如 `livachat`），避免监听/契约语境污染其他 agent 会话；配置项：模型、workspace（可选放契约副本到 `AGENTS.md` 作系统提示层增强，D7-d）、工具策略与 `permissionMode`（无人值守收窄）；
3. **lvdata MCP 注册**（长期记忆，hermes §6.1 同款目标）：`mcp.servers.lvdata = {transport:"streamable-http", url:"http://127.0.0.1:8090/mcp"}`（本机 `mcp.servers.bocha` 即此形制）；
4. **审批策略**：exec approvals / 权限模式按"无人值守"预置（relay 兜底是第二道防线）；
5. **部署形态**：与 LivaChat server、lvdata-mcp 同机（chatlog 为本地路径；跨机见非目标）。

---

## 7. 可靠性与边界情况

| 场景 | 处理 |
|------|------|
| WS 断连 | 退避+jitter 重连；pending RPC 与 run wait 全部失败弹出；重连成功后对账 sweep + 订阅重建 |
| WS 半死（TCP 活、应用层死） | tick 静默 > `silence_timeout_s`（75s）判死重连（D10）；协议层 ping/pong 由 websockets 客户端托管 |
| 事件 `seq` 空洞 | 立即重连接（协议文档 §3.3） |
| relay 重启 | 无状态恢复动作：job 对账（§4.4.5：running→describe/history；requested→幂等重发）；监听/reactive 会话由下一轮 nudge/utterance 自愈（`session_manager.py:218-234` 既有自愈路径） |
| relay 停机（运行中 job） | WS 断开，OpenClaw 侧 run 继续；重启后由 §4.4.5 对账收敛/取回结果；done 未投递结果由既有 shutdown 盘点 + 设备上线补投保障（`session_manager.py:324-338`） |
| OpenClaw 重启 | 连接断开→重连退避；会话与 transcript 持久（sqlite store）——对账按 §4.4.5；网关重启中运行被中断的 run：对账按 describe/history 收敛（**中断语义契约待 P0-Q5 固化**） |
| 停机期间 job 完成 | 重连/启动对账从 transcript 读回结果补投（对比 hermes 不可恢复） |
| job 永不完成 | 看门狗 48h 收敛 + abort 尝试；不做 48h 内主动轮询 |
| 监听 turn 超时 | abort 该 run（防坏 turn 阻塞后续 nudge）→ nudge 按失败返回 → watcher 攒批下轮重试（数据不丢，offset 持续跟踪） |
| 契约 JSON 违例 | 共享解析器兜底：按 noop + metric + debug 日志，绝不回推 |
| 主 agent 违规重复派生 | per-device/全局在途硬上限拦截（同 hermes） |
| 同一天多通对话 / UTC 跨天 | 与 hermes 相同（watcher 按 `chat_session_id` 过滤、rollover 重解析 + 新路径 nudge） |
| 设备离线 | done 结果保持待补投（Deliverer 现状）；status 快照跳过离线（现状） |
| 双 adapter 同开 | job 归属（agent_name）分流；配额按设备总量计（`outstanding_jobs_for_device` 语义保持，文档注明） |

---

## 8. 分阶段实施

| Phase | 内容 | 验收 |
|-------|------|------|
| **P0 实测验证**（阻塞 P1 开工） | `tests/manual/p0_openclaw_probe.py`：§9 Q1–Q8（会话树字段接受度、终稿 message 形状、error run transcript、queueMode 行为、agent.wait 存续、重启中断语义、审批事件路径、幂等重发） | 结论回填 §4/§9；探测在**专用探测会话/独立 agent** 上进行（会创建少量会话，不触碰用户会话） |
| **P1 主链路** | `openclaw.py` 核心（连接/reader/RPC/订阅）、`_ensure_*` 会话、nudge+契约、job 派发（B 方案）、ask、config、wiring、共享 `contract.py` | mock 集成 ①-⑥⑪ + 单测；dev 环境一通对话收到 openclaw 主动 ai_text；job 全链路（含 NOOP） |
| **P2 恢复与兜底** | 对账 sweep、watchdog+abort、审批 autorespond、共用组件 `agent_name` 改造、hermes watchdog 过滤 | 集成 ⑦-⑩⑫；重启 relay 补投验证；hermes 全量用例回归 |
| **P3 打磨** | metrics/日志、真机 soak（多设备并发 nudge/job）、文档（job_status 口径、部署手册）、`configs/*/server.yaml` 定稿 | 真机长会话无 tick 误杀；双 adapter 稳定运行；判死窗口 ≤ 90s |

**实施进展（2026-10-14）**：

- ✅ **P0**：`tests/manual/p0_openclaw_probe.py` 实测完成，Q1–Q8 结论回填 §9（含设计修正：create 不传 idempotencyKey）；
- ✅ **P1**：`adapters/openclaw.py`（单连接/订阅/runId 关联/job 派发/ask/渲染）、`config.py` 重写 + watcher 旋钮上移、`contract.py`/`prompt_hints.py` 共享模块、`prompts/openclaw_*.md`、application 接线、`mock_openclaw_server.py` + 单测 18 + 集成 19 用例；
- ✅ **P2**：启动/重连对账（describe/history + 幂等重发）、openclaw watchdog（超时 abort）、审批/提问 autorespond（限定 relay 会话归属）、hermes watchdog agent 过滤、`JobRecord.agent_name` 全链路（store/deliverer/notifier）；
- ⏳ **P3**：`configs/{dev,test}/server.yaml` openclaw 段已更新；**真机冒烟 PASS**（`tests/manual/openclaw_adapter_smoke.py`：真实 nudge → 模型回 `job_request` → job 会话全链路 → final → JobStore done/delivered → 确定性 message_id 投递）；长时 soak 与部署手册待办。

---

## 9. 开放问题

**P0 必答（阻塞 P1 开工）** —— ✅ **已实测**（2026-10-14，`services/tests/manual/p0_openclaw_probe.py` 对运行中的本机 gateway，原始帧 `/tmp/openclaw_p0_frames.jsonl`；结论逐项回填）：

1. **Q1 会话树与 create 结果**：✅ `sessions.create {key, parentSessionKey:"agent:main:main", spawnDepth:1}` 被接受（describe 佐证 `spawnDepth=1`/`parentSessionKey` 持久）；结果字段 `{ok, key(规范键), sessionId, entry, runStarted, resolved}`；同 key 重创建 = adopt（sessionId/createdAt 不变）。**⚠️ 设计修正：create 传 `idempotencyKey` 在后端路径被拒**（`INVALID_REQUEST "idempotent session creation requires an authenticated principal or device identity"`）→ **创建不传 idempotencyKey，幂等由 key adopt 保证**（§4.4.1 已改）。
2. **Q2 终稿文本**：✅ final 事件 `message = {role:"assistant", content:[{type:"text", text:"…"}], timestamp}`（内容块数组）；delta 帧 `{deltaText, message?}`（首个 delta 带内容快照，后续可省）；转录含 `thinking` 块；ack `runId == idempotencyKey` 两轮实测成立（§3.1 源码结论坐实）。
3. **Q3 对账读取**：✅ `sessions.describe` 行含 `status`（done/running）、`hasActiveRun`、`activeRunIds`、**`lastRunId`**、`lastRunError`、`endedAt`；final 后有 **~秒级 settle 窗口**（settle 前 describe 仍显 running）。判定改用 describe 字段（优于 history 尾行启发式，§4.4.5 已改）；`chat.history` 行形态：user = 纯文本 str，assistant = 块数组（thinking+text）。
4. **Q4 queueMode**：✅ 运行中再发（queueMode=followup）不报错，各自独立 runId、ack 均 `status:"started"`；`sessions.abort {runId}` 精确取消（含排队中 run）返回 `{abortedRunId, status:"aborted"}`；aborted 事件 `{state:"aborted", stopReason:"rpc"}` 且**可无 `message` 字段**（adapter 需容忍）。
5. **Q5 网关重启中断语义**：❌ **不可安全实测**（需重启用户的 gateway 服务）；维持保守设计：对账以 describe 的 `lastRunId`/`lastRunError`/settle 重试为准，判定不决时 `failed(interrupted)`。
6. **Q6 幂等重发语义**：✅ 立即重发（运行中/结算中）→ `{runId, status:"in_flight"}`，**不产生重复 run**；settle 后重发 → `{runId, status:"ok"}`（缓存终态）。重发策略安全可用（保留期未探边界，§4.4.5-3 采用并加兜底）。
7. **Q7 审批路径**：⚠️ 本机 main agent `permissionMode=full`，未触发审批事件；参数按 [源码] schema（§4.5 已写实）；广播路径的真机可达性留实现期验证，兜底策略不变。
8. **Q8 会话维护语义**：✅（代码核查）`archiveAfterMinutes` 仅作用于 native subagent registry 条目（`dist/subagent-registry-helpers-Cxh3eRfU.mjs:925` `updateSubagentArchiveAtMs`）——**relay 自建普通会话不受其自动归档**；长期保留策略仍由运维决定（§4.2 注记）。

**后续版本评估**：

9. **Q9 会话键的 agentId 归属**：多 agent 部署（本机已有 main/stock/…）下 relay 固定单 agent 还是按 device 映射；
10. **Q10 job 迁移**：`sessions.dispatch`/`sessions.move`（跨设备/云 worker）是否值得接入（本地部署无需求）；
11. **Q11 展示协调**：proactive `ai_text` 与主 LLM 播报/TTS 的客户端协调（hermes §9 Q8 同题）；
12. **Q12 跨机部署**：relay 与 OpenClaw 分机 → device identity + pairing + TLS（协议文档 §2.2 退路）；
13. **Q13 steer 产品化**：用户打断 job 的交互面（§4.4.4）。
