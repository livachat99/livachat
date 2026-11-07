# Agent Relay Service v2 — Device 级会话与 Job 结果可靠投递（合并定稿）
> **已更新**：Redis 已去接线，job 状态迁移 chat_server.db（SqliteJobStore），见 [chatserver_no_redis.md](chatserver_no_redis.md)。
> **Job 载体已升级**：`prompt.background` 已由独立 job 会话取代（session 为唯一载体），见 [hermes_adaptor_design_v3.md](hermes_adaptor_design_v3.md)；本文中 background/task_id 相关描述仅作历史记录，JobStore/Deliverer/补投/看门狗部分仍有效。

> **状态**：评审定稿（待实施）
> **日期**：2026-08-14
> **合并自**：[`fd_agent_relay_service_v2-d4.md`](fd_agent_relay_service_v2-d4.md)、
> [`fd_agent_relay_service_v2-k3.md`](fd_agent_relay_service_v2-k3.md)。
> 评审结论：以 k3 为基线（per-device 连接为既定架构决策），并入 d4 的 Hermes 源码调研与
> 停机语义；本文档取代上述两份草案。补充决策：旧映射 key 不迁移、直接废弃（D15）；
> 当前目标规模 ~10 并发 device，4090 仅做告警不做背压（§8.4）。
> **前置文档**：[`fd_agent_relay_service.md`](fd_agent_relay_service.md)（总体设计）、
> [`hermes_adaptor_design_v2.md`](hermes_adaptor_design_v2.md)（Hermes v2 Proactive 交互终稿）、
> [`hermes_websocket_api.md`](hermes_websocket_api.md)（Hermes 协议调研）
> **范围**：`services/agent_relay/`（重点 `adapters/hermes.py`、`job_store.py`、
> `session_manager.py`，新增 `delivery.py`）+ `services/app/application.py` 接线

---

## 1. 背景与问题

### 1.1 现状行为

当前 relay 的 Hermes 会话与后台 job 生命周期**绑定在 LivaChat chat session 上**：

```
client WS 连接 → session.started → relay 建 hermes session（按 livachat session_id keyed）
client 断连   → session.ended   → relay orphan 所有 active jobs + 关闭 hermes session
```

由此产生 **job 结果必丢** 的三层丢弃链（代码事实，§2.2 已逐条验证）：

1. `AgentSessionManager.on_session_ended` → `job_store.orphan_session_jobs(sid)`
   （session_manager.py:251）：设备一离线，active job 立即被置为 `orphaned`；
2. `HermesAdapter.on_session_end` → hermes `session.close` + 删除 Redis 映射
   （hermes.py:585）：job 在 hermes 侧跑完发 `background.complete` 时，relay 查 job
   状态非 `running` → **丢弃**（hermes.py:468）；
3. 即使前两步未发生，`_on_adapter_push` 按 livachat session_id 查内存 session（已删）→
   **丢弃**（session_manager.py:304）；再即使到了 `PushPublisher`，`conn:{device_id}`
   已注销（设备离线）→ **丢弃**（publisher.py:39-45）。

### 1.2 需求

| # | 需求 | 概述 |
|---|------|------|
| R1 | **relay 启动恢复** | relay 启动后扫 Redis JobStore：有 job 在 running → 建立 hermes 连接 + session，等待 job result。**每个 device UUID 一条 WS 连接、一个 hermes session**（架构决策，见 §4.1-D1）。无任何 job running → 不建连，等 device 上线再建 |
| R2 | **48h 结果补投** | JobStore job 超时定为 48h。48h 内同一 device 重新连接上来，把 done 状态的 job 结果通过 `ai_text` 发给 device |
| R3 | **跨 chat session 复用 hermes session** | 同一 device UUID 多次连接，livachat session id 不同，但只要该 device 有 hermes job 在 running，就必须使用同一个 hermes session，否则收不到 job result |

### 1.3 非目标（本期不做）

- 离线推送（APNs/FCM）：device 超过 48h 不上线，结果随 TTL 过期丢弃；
- `requested` 状态 job 的断点重派（spec 未持久化，见 §8.6）；
- 多 relay 实例部署（仍为单实例嵌入 LivaChat Server）；
- agent 回复走 TTS；
- reactive `ask()` 的独立连接策略（reactive 模式默认关闭，见 §5.2）。

---

## 2. 调研结论（源码级，已逐条验证）

### 2.1 Hermes 侧事件投递与回收机制（`~/.hermes/hermes-agent/tui_gateway/server.py`）

**结论一：`background.complete` 的投递以「父 session 存活 + 有 live transport」为前提，
事件投递键是 session，不是连接。**

- `write_json`（server.py:1517-1535）：带 `session_id` 的事件帧 → 路由到
  `_sessions[sid]["transport"]`；无路由命中时落到 `current_transport()`（后台 daemon
  线程无绑定）→ 兜底 `_stdio_transport`，`hermes serve` 无头模式下即丢失。
- `session.close` / 回收后：session 从 `_sessions` 移除 → 后续 `background.complete` 必丢。
- WS 断连（`_close_sessions_for_transport`，server.py:1074-1108）：
  `close_on_disconnect=false` 的 session transport 置为 `_detached_ws_transport`
  （**drop sentinel**，写入即丢），并启动 `_schedule_ws_orphan_reap`——
  **20s 孤儿宽限**（`_WS_ORPHAN_REAP_GRACE_S = 20.0`，server.py:179）后 reap。
- **孤儿回收不保护有 background job 的 session**：reap 前检查
  `_session_has_active_delegations`（server.py:1060），但 `prompt.background`
  （methods_prompt.py:730-773）创建的 detached AIAgent
  （`_background_agent_kwargs`，server.py:6002：`session_id=task_id`、`platform="tui"`、
  无 `parent_session_id`）**不注册**到 `async_delegation._records`
  → `has_live_for_session` 返回 False → **断连 20s 后 session 必被 reap，job 在跑也一样**。
- `session.resume(stored_session_id)`（`_session_resume_lock` 保护）重绑 live transport：
  **20s 宽限内复用 live 会话**（可取消 reap）；宽限后从 DB 重建（返回**新** session id）。

**推论**：

1. 要收到 job 结果，**Hermes session 不能 close、且须在断连后 20s 内完成重连 + resume**——
   这是 R1/R3 的硬约束，也是 §5.4 启动恢复设计的直接输入；
2. 断连超过 20s：旧 sid 已 reap，resume 走 DB 重建得到**新 sid**；而 `background.complete`
   发往 dispatch 时捕获的**旧** parent sid → 结果必丢（at-most-once，本期接受，§8.2）。
   **判别信号**：resume 返回的 session_id 与映射中不同 ⇒ 窗口已过 ⇒ 该 device 的 running
   job 立即置 `failed("session_lost")`，不必等 48h 看门狗；
3. session 复用是硬约束：新 session 收不到旧 job 的 `background.complete`（事件按
   session 路由）——即 R3。

### 2.2 relay 现状盘点

| 组件 | 现状（代码事实） | 与需求冲突点 |
|------|------------------|--------------|
| 连接拓扑（hermes.py:104-141） | 全 device 共享**一条** WS + 单一 reader loop；RPC 按 id 多路复用（`_pending`），turn 按 hermes sid 分发（`_turn_futures`）；`application.py` 启动时**无条件 eager 建连** | R1：改为 per-device 连接 + 按需懒建 |
| 重连（hermes.py:301） | reader loop 固定 `sleep(2.0)` 重连 + `_resume_sessions()`，**无退避无抖动** | per-device 下 N 条连接同步重连会形成风暴（§8.3） |
| session 映射（hermes.py:108, 1005） | 内存 `_sessions: dict[livachat_sid]`；Redis key `agent:hermes:session:{livachat_sid}`，TTL 已 48h | R3：改 device 维度；旧 key 不迁移（D15） |
| `on_session_end`（hermes.py:585） | `session.close` + 删映射 | R3：不再随 chat session 结束而关闭 |
| `_handle_background_complete`（hermes.py:452-496） | 仅 `status==running` 时经 `job.session_id` 推送；device 离线即弃；**不存结果**；inline await 保证 per-session 完成顺序 | R2：`mark_done(result_text)` + 走 deliverer；per-device reader 天然保留 per-device 顺序 |
| JobStore（job_store.py） | `JOB_TTL_S` 已 = 48h；**无结果存储**（注释明确 "no result storage, no redelivery"）；**无 per-device 索引**；`_task_key` 是全局映射，反查不受 session 维度影响 | R2：加 result/delivered 字段 + device 索引 |
| 看门狗（hermes.py:187-209, config.py:28-30） | `running` 超 `background_timeout_s`（默认 **1800s**）→ failed(timeout)；`requested` 超 60s → failed(orphaned)；全局硬上限 `max_in_flight_jobs=20`、per-session `max_concurrent_jobs_per_session=10` | R2：running 超时改 48h；上限与 48h 的联动见 §8.4；per-session 配额语义改 per-device（§5.8） |
| 投递路径（publisher.py:33, push_subscriber.py:101-121） | `conn:{device_id}` 解析实例，离线返回 False；ai_text 帧只透传 `text/message_id/index/is_final/source/agent_name/reply_to/proactive`，**session_id 不下发** | 投递判定只需 `conn:`，不依赖存活 livachat session——补投无协议障碍 |
| 设备上线信号 | 两个 orchestrator 均在 WS 建立后发布 `session.started`（orchestrator.py:277, omni_orchestrator.py:210） | R2：即「device 重连」信号，挂补投钩子，无需新增事件 |
| NUDGE 上下文（hermes.py:690, chatlog_watcher.py:339） | `outstanding_jobs(livachat_session_id)` | R3：改 device 维度，新 chat session 必须看到旧 session 派的 job |
| mission 模板（prompts/hermes_monitor.md:5） | 写死 `chat_session_id == "$chat_session_id"` | session 跨 chat 存活后会过期，改动态语义（§5.7） |

### 2.3 可复用资产

- `session.resume` 已实现（hermes.py:328-361）：返回的 session_id 可能与映射不同
  （宽限复用或 DB 重建），可直接用于 §5.4 的窗口判别；
- JobStore `_task_key`（task_id→job_key）全局映射，`background.complete` 反查不受
  session/连接维度影响；
- `livachat:recording:device:{device_id}:current` 给出当天 chatlog 路径，device 维度天然成立；
- `PushPublisher` 已做 `max_reply_bytes` 截断（publisher.py:49）与离线 False 语义。

---

## 3. 需求 → 差距分析

| 需求 | 现状 | 差距 |
|------|------|------|
| R1 启动扫 JobStore 建连 | 启动 eager 建一条全局连接；job 不随重启恢复 | ① per-device 连接管理重构；② 懒建连；③ 启动扫 `list_active()` 按 device 分组、有界并发恢复；④ 前提是 session 结束不再 orphan job |
| R1 每 device 一条 WS + 一个 hermes session | 全局一条 WS；hermes session 按 livachat session 一对多 | HermesAdapter 重构为 per-device `_DeviceConnection`（§5.2） |
| R2 TTL 48h | `JOB_TTL_S` 已 48h ✓ | 仅需对齐看门狗 `background_timeout_s`（1800s → 48h） |
| R2 done 结果 48h 内补投 | 不存结果、无 device 索引、无补投触发点、离线即丢 | ① JobRecord 增加 `result_text` + 投递状态；② per-device job 索引；③ `session.started` 触发补投 sweep；④ 投递成功才置 `delivered` |
| R3 同 device 复用 hermes session | 映射按 livachat session keyed，session 结束即 close | ① 映射键改 device_id；② session.ended 不再 close/orphan；③ `outstanding_jobs` 改 device 维度；④ mission 模板动态化 |

---

## 4. 总体设计

### 4.1 核心决策（定稿）

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| D1 | 连接粒度 | **每 device 一条 WS + 一个 hermes session**（架构决策） | R1 的直接要求。事件按 session 路由（§2.1），连接不增加投递保障——per-device 的价值在于故障隔离与按 device 的鉴权/流控扩展性（§5.8 将其做实）。成本侧（连接规模、恢复窗口、重连风暴）在 §8.3/§8.4 以机制对冲 |
| D2 | 会话归属 | **device 级**：`device_id → {连接, hermes_session_id, stored_session_id}`；JobRecord 保留创建时 session_id 仅作追溯 | job 结果的关联键必须与易变的 livachat session 解耦 |
| D3 | 无 active job 时的 session 处置 | **关连接、留 session**：WS 关闭（LRU/idle 回收），hermes session 因 `close_on_disconnect=false` 保留服务端 + 映射留 Redis（`session_retention: keep`）；下次连接 `session.resume` 复用 | per-device 的架构意图是 device 级长期代理关系，每通 chat session 重建 session 等于放弃跨对话上下文。保留量由三层有界：连接侧 LRU（D8）、映射 TTL 48h、Hermes 侧 idle session TTL reaper（hours-scale，server.py:1126-1129）。兜底开关 `session_retention: close_on_idle` 可回退到「无 job 即 close」 |
| D4 | job 终态语义 | session 结束**不再** orphan job；job 只随 48h TTL / 看门狗 / `session_lost` 收敛 | job 存活期 > livachat session 存活期是常态（R2） |
| D5 | 结果存储 | JobRecord 增加 `result_text`（**截断至 `max_stored_result_bytes`，默认 16384**，对齐 `max_reply_bytes`） | 复用既有 48h TTL，零额外存储组件；截断防止无界写入 |
| D6 | 投递幂等键 | **确定性 `message_id = msg_agent_job_{job_key}`** | relay 在 publish 成功与 `mark_delivered` 之间崩溃会产生重投，客户端按 message_id 去重（客户端任务见 §11-Q2） |
| D7 | 补投触发点 | `session.started` 事件，**异步 sweep**（`asyncio.create_task`，不阻塞事件主链路） | app 建连即建 session，是「device 上线」的可靠代理；到达时 `conn:` 已注册，PushPublisher 可解析实例 |
| D8 | 连接建立/回收 | **懒建立**：① 启动时有 running job 的 device；② device 上线；③ reactive `ask()` 首次到达。**回收**：无 active job 且无存活 chat session → 关 WS（即时检查 + 60s reaper 兜底）；`max_device_connections` 超限 → idle LRU 驱逐（仅驱逐无 active job 的连接；驱逐 = 关 WS，不 close session） | R1「无 job 不建连」；连接规模有界（§8.4） |
| D9 | 启动恢复顺序 | **有界并发 + 优先级**：`recovery_concurrency`（默认 10）信号量并发；**running job 的 device 优先**；**requested-only 的 device 不恢复连接** | 20s reap 窗口（§2.1）下串行恢复在 N 大时系统性超时（§8.2）；requested job 无 task_id 不可救回，建连是纯浪费 |
| D10 | resume 失败语义 | 重试 `session_resume_retries`（默认 3，退避 ~10s）；**resume 返回新 sid ⇒ 20s 窗口已过 ⇒ 该 device running job 置 `failed("session_lost")`**；resume 最终失败同处置 | fail-fast：确认收不到结果的 job 不让用户等 48h（§2.1 推论 2） |
| D11 | 重连策略 | 每连接独立**指数退避 + 抖动**（1s→2s→4s→…上限 30s，jitter ±25%），**不平移**现有固定 2s | per-device N 条连接同步重连会形成风暴（§8.3） |
| D12 | 看门狗 | **全局唯一**，`connect()` 时无条件启动（只依赖 JobStore，不依赖任何 WS）；`background_timeout_s` 默认 48h | 懒建连不能连带推迟 stale job 收敛与 inflight 清理 |
| D13 | 投递串行 | 所有 job 结果投递（live + 补投）收口到 `JobResultDeliverer`，**per-device 投递锁 + 锁内重读状态** | 消除「补投 sweep 与 live complete 同时看到 !delivered 双发」的 check-then-act 竞态 |
| D14 | NOOP/error 文本 | 不入 `result_text`；`mark_done` 后直接 `mark_delivered`（视为已消费），不投递 | 沿用 v2 语义（NOOP / `error:` 前缀不推用户） |
| D15 | 旧映射 key | **不迁移，直接使用新 key**：新写入一律 device key；旧 `session:{sid}` 映射不做双读，随 48h TTL 自然过期 | 当前规模（~10 device）下升级窗口可控，不维护迁移逻辑；代价：升级时在途 running job 的结果丢失（一次性，接受，§8.6） |

### 4.2 目标架构

```
                 ┌─────────────────────── LivaChat Server ───────────────────────┐
                 │                                                                │
 app ──WS──▶ gateway ──▶ orchestrator ──▶ AgentEventPublisher                    │
   ▲          ▲                                    │ session.started/ended,      │
   │          │                                    │ user.utterance (f&f)        │
   │          │                                    ▼                             │
   │          │                    Redis: livachat:agent:events                  │
   │          │                                    │ subscribe                   │
   │          │                            ┌───────┴────────┐                    │
   │          │                            │  EventRouter   │                    │
   │          │                            └───────┬────────┘                    │
   │          │                                    ▼                             │
   │          │                     AgentSessionManager (chat session 1:1)       │
   │          │                       │  ├─ on_session_started:                  │
   │          │                       │  │   补投 sweep(device, 异步) + 建连       │
   │          │                       │  └─ on_session_ended: 不再 orphan/close  │
   │          │                       ▼                                         │
   │          │              ┌────────────────────────────────┐                 │
   │          │              │  HermesAdapter (重构)           │                 │
   │          │              │  device_id → _DeviceConnection  │──┐              │
   │          │              │   {ws, reader(退避+jitter),     │  │ 每 device    │
   │          │              │    rpc, turn, hermes_session_id,│  │ 一条 WS      │
   │          │              │    locks, last_active_at}       │  │ (懒建立)     │
   │          │              └───────┬──────────────┬─────────┘  │              │
   │          │                      │              │            │              │
   │          │              JobStore (v2)    JobResultDeliverer  │              │
   │          │              + result_text    + 上线补投 sweep     │              │
   │          │              + device 索引    + per-device 投递锁  │              │
   │          │              + delivered 状态                     │              │
   │          │                      └──────┬───────┘            │              │
   │          │                             ▼                   │              │
   │          └─────────── livachat:push:{instance_id} ─────────┼──────────────┘
   │                          (ai_text, proactive, msg_agent_job_{job_key})
   └─────────────────────────────────────────────────────────────┘
                                    │ 每 device 一条 WS（懒建立，退避+jitter）
                                    ▼
                              Hermes /api/ws
                    （每 device 一个 session，close_on_disconnect=false）
```

### 4.3 关键消息流

**R1 — relay 启动恢复（有 running job）**：

```
1. application lifespan: adapter.connect()（只启动 watchdog + recover 任务，不建任何 WS）
2. recover_active_devices():
3.   job_store.list_active_by_device() → 过滤出含 running job 的 device（requested-only 跳过）
4.   信号量（recovery_concurrency=10）有界并发，逐 device:
5.     _ensure_device(device_id)        —— 懒建 WS（退避+jitter）+ gateway.ready
6.     读 device 映射 → session.resume(stored_session_id)
7.     返回同 sid → live 复用成功，background.complete 可正常到达
8.     返回新 sid / 重试后失败 → 该 device running job 置 failed("session_lost")
9. 无 active job → 全程不建任何 WS
```

**R2 — job 完成与补投**：

```
a) 设备在线时完成：
   background.complete → find_by_task_id → job running
     → mark_done(job_key, result_text)            （截断后落 JobStore）
     → deliverer.deliver(job)：per-device 锁内重读状态
         PushPublisher.publish(ai_text, message_id=msg_agent_job_{job_key}, proactive=true)
         成功 → mark_delivered；失败（设备恰好掉线）→ 保持 done，等补投

b) 设备离线时完成：同上，publish 失败 → 保持 done（TTL 48h 内有效）

c) 设备 48h 内上线：
   session.started → SM.on_session_started
     → asyncio.create_task(deliverer.sweep_device(device_id, session_id=sid))
         list_pending_delivery(device_id)（done 未投递，按 completed_at 升序）
         → 逐条 deliver → 成功 mark_delivered；中途失败即停（保序 + fail-fast）
```

**R3 — 跨 chat session 复用**：

```
device 第一次连接: session.started(sid=A)
  → adapter 为该 device 建连 + session.create（或 resume）→ hermes session H
  → nudge 触发 job_request → job J 挂在 H 上
client 断连: session.ended(A)
  → 不 orphan J；有 active job → 保留连接与 H；无 active job → 关连接、留 H（D3）
device 再次连接: session.started(sid=B, 同 device)
  → adapter 查 device 映射命中 → resume 复用 H（不重种 mission，发一次 mission 更新 NUDGE，
    chat_session_id/chatlog 路径以最新为准，§5.7）
  → B 的 watcher/nudge 全部落在 H 上；outstanding_jobs 按 device 查询（J 可见）
  → J 完成 → 结果投递到 device
```

### 4.4 JobStore 状态机（v3）

```
requested ──(prompt.background 成功)──► running ──(background.complete)──► done ──(投递成功)──► delivered
    │                                      │
    └─(60s 未转 running)─► failed(orphaned) ├─(超 background_timeout_s=48h)─► failed(timeout)
                                            └─(resume 返回新 sid / 恢复失败)─► failed(session_lost)

※ background.complete 文本为 NOOP 或以 error: 开头：沿用 v2 语义——不入 result_text，
  mark_done 后直接 mark_delivered（视为已消费），不投递、不进补投队列。
```

- `orphaned` **不再是独立状态**，仅是 `requested → failed` 的 fail_reason（崩溃残骸收敛），
  不再由 session 结束触发；`orphan_session_jobs()` 方法保留供运维清理，从
  `on_session_ended` 路径移除（openclaw adapter 不使用 JobStore，无连带影响——已核实）。
- `delivered` 为终态 + 补投幂等键：同一 job 只投递一次（崩溃窗口由 D6 + 客户端去重兜底）。

---

## 5. 详细设计

### 5.1 JobStore v2

**JobRecord 新增字段**（hash 存储，旧记录缺字段按默认值兼容读）：

```python
@dataclass
class JobRecord:
    job_key: str
    hermes_task_id: str
    session_id: str          # 创建时的 livachat session（仅追溯用）
    device_id: str           # 投递与索引主键
    status: str              # requested|running|done|delivered|failed
    summary: str
    fail_reason: str = ""      # timeout|orphaned|rpc_error|session_lost
    result_text: str = ""      # 新增：job 成品文案（截断 max_stored_result_bytes）
    completed_at: float = 0.0  # 新增：完成时间（补投排序键）
    delivered_at: float = 0.0  # 新增：投递成功时间（0 = 未投递）
    created_at: float = 0.0
    updated_at: float = 0.0
```

**索引变化**（全部带 48h TTL）：

| key | 类型 | 变化 |
|-----|------|------|
| `agent:hermes:job:{job_key}` | hash | 不变（字段增加） |
| `agent:hermes:device:{device_id}:jobs` | set | **新增**：per-device 索引，create 时写入 |
| `agent:hermes:session:{sid}:jobs` | set | 保留（追溯/兼容），不再作为主索引 |
| `agent:hermes:task:{task_id}` → job_key | string | 不变 |
| `agent:hermes:inflight` | set | 不变 |

**新增/变更方法**：

```python
async def mark_done(self, job_key: str, result_text: str = "") -> JobRecord | None
    # running → done，写入截断后的 result_text 与 completed_at，从 inflight 移除

async def mark_delivered(self, job_key: str) -> JobRecord | None
    # done → delivered，写 delivered_at（幂等）

async def list_pending_delivery(self, device_id: str) -> list[JobRecord]
    # device 索引中 status==done 的记录，按 completed_at 升序（补投顺序保证）

async def list_active_by_device(self) -> dict[str, list[JobRecord]]
    # 启动恢复用：list_active() 按 device_id 分组

async def outstanding_jobs_for_device(self, device_id: str) -> list[dict]
    # nudge 上下文改 device 维度（R3：新 chat session 必须看到旧 session 派的 job）

# 变更：watchdog scan_stale 的 running_timeout_s 由配置传入 48h（§6）
# 移除：orphan_session_jobs() 从 on_session_ended 的调用（方法保留供运维清理）
```

**TTL 语义**：`JOB_TTL_S = 48 * 3600` 值不变，注释统一表述为「job 超时与结果可投递窗口」。
done 未投递记录随 TTL 过期 = 结果丢弃（符合需求：超 48h 不上线即放弃）。device 索引 set
中指向已过期 job hash 的成员由查询方跳过（`get()` 返回 None）。

### 5.2 HermesAdapter 重构：per-device `_DeviceConnection`

将现有「单连接 + reader loop + RPC/turn 复用」机制抽取为 `_DeviceConnection`，adapter
变为按 device 管理的容器：

```python
@dataclass
class _DeviceConnection:
    device_id: str
    ws: ClientConnection | None = None
    hermes_session_id: str = ""
    stored_session_id: str = ""
    ready: asyncio.Event
    reader_task: asyncio.Task | None = None
    pending: dict[int, Future]           # RPC 复用（原 _pending）
    turn_futures: dict[str, Future]      # 原 _turn_futures
    turn_stragglers: set[str]            # 原 _turn_stragglers
    lock: asyncio.Lock                   # turn 串行化（原 _SessionInfo.lock）
    start_lock: asyncio.Lock             # 建连/session 建立/关闭 串行化
    last_active_at: float
    reconnect_attempts: int = 0          # 退避状态（新增）

class HermesAdapter(AgentAdapter):
    self._devices: dict[str, _DeviceConnection]   # 替代原 _ws/_sessions
```

- 方法（`_connect_ws` / `_reader_loop` / `_rpc` / `_send_notification` /
  `_submit_turn` / `_resume`）基本是现有代码的平移，作用域从 adapter 全局收窄到
  单 device；**两处不平移**：
  1. **重连策略**（D11）：`_reader_loop` 的固定 `sleep(2.0)` 改为每连接独立指数退避
     （`reconnect_backoff_initial_s`→`reconnect_backoff_max_s`）+ jitter
     （`reconnect_jitter`），成功连上后重置 `reconnect_attempts`；
  2. **排序语义**：per-device reader 上 inline await 投递天然保留 per-device 完成顺序
     （原单 reader 的 inline await 注释语义随作用域收窄继续成立）。
- **懒建立**（D8）：`connect()` 不再建任何 WS，只启动全局 watchdog（D12）与
  `recover_active_devices()`（§5.4）；`_ensure_device(device_id)` 在
  on_session_start / nudge / ask 路径上按需建连（`start_lock` 串行化，幂等）。
- **session 建立/复用**（R3 + D3）：`ensure_session(device_id, ctx)`：
  1. 内存 `_devices` 命中且有 `hermes_session_id` → 直接复用；
  2. 否则读 Redis device 映射（§5.3）→ 有则 `session.resume`；
  3. 无则 `session.create(close_on_disconnect=False, source="livachat-relay",
     title=device_id, messages=[mission])`；
  4. 复用（非新建）时发一次 **mission 更新 NUDGE**（chat_session_id/chatlog 路径以
     最新为准，§5.7）。
- **连接回收**（D8/D3）：device 无 active job（`requested/running`）且无存活 chat
  session → 关闭 WS；hermes session 保留服务端 + 映射留 Redis
  （`session_retention: keep`）。判定在 `on_session_ended` / `mark_delivered` 后即时
  检查 + `device_conn_idle_reaper_s`（默认 60s）周期 reaper 兜底，均在 `start_lock`
  下做 check-then-act（防「回收判定与 job 派发完成」竞态）。`session.close` 仅在
  `session_retention: close_on_idle` 时触发。
- **连接上限**：`_devices` 超 `max_device_connections` → idle LRU 驱逐（按
  `last_active_at`，仅驱逐无 active job 的连接；驱逐 = 关 WS，不 close session）。
- **reactive `ask()`**：在 device 连接上执行（`close_on_disconnect=True` 的临时 session
  语义不变）；首次 ask 触发懒建连。reactive 模式默认关闭，本期接受；未来开启时需评估
  「一次性 ask 的 device 也占常驻连接」的问题，预留独立 ephemeral 连接策略（§11-Q6）。
- 既有行为平移保留：straggler 防护、interaction 自动应答（approval/clarify/sudo/
  secret）、断线重连 + resume（退避策略除外）、nudge 契约解析、two-stage job 派发。

### 5.3 会话映射持久化（D15）

- 新 key：`{prefix}:agent:hermes:device:{device_id}:session` →
  JSON `{hermes_session_id, stored_session_id}`，TTL 48h；
- **旧 key 不迁移、不做双读**：`restore_sessions()` 由 `recover_active_devices`
  （§5.4）取代，只读 device key；旧 `session:{livachat_sid}` 映射随 48h TTL 自然过期；
- 升级一次性代价：部署时在途 running job 的旧 session 绑定丢失，其结果按 §8.2 语义
  丢弃、由看门狗收敛——当前 ~10 device 规模下接受（§8.6）；
- 注意与 JobStore 的 `agent:hermes:device:{device_id}:jobs` 同前缀不同后缀，涉及
  scan 时模式须精确（`*:session`），避免 WRONGTYPE。

### 5.4 启动恢复（R1，D9/D10）

`HermesAdapter.recover_active_devices()`（`connect()` 时拉起，proactive 模式）：

```python
async def recover_active_devices(self) -> None:
    groups = await self.job_store.list_active_by_device()   # requested/running 按 device 分组
    targets = {d: jobs for d, jobs in groups.items()
               if any(j.status == STATUS_RUNNING for j in jobs)}
    # requested-only 的 device 不恢复：job 无 task_id 不可救回，等 60s 看门狗收敛
    sem = asyncio.Semaphore(self._cfg.recovery_concurrency)  # 默认 10，20s 窗口内完成关键恢复
    await asyncio.gather(
        *(self._recover_one(d, jobs, sem) for d, jobs in targets.items()),
        return_exceptions=True,   # 单 device 失败不影响其他；保持 running 等看门狗兜底
    )

async def _recover_one(self, device_id, jobs, sem) -> None:
    async with sem:
        conn = await self._ensure_device(device_id)       # 懒建 WS（退避+jitter）
        for attempt in range(self._cfg.session_resume_retries):  # 默认 3，退避 ~10s
            new_sid = await self._resume_device_session(conn)    # session.resume(stored_id)
            if new_sid and new_sid == conn.mapped_session_id:
                return  # 20s 窗口内 live 复用成功，running job 结果可投递
            if new_sid:
                break     # 返回新 sid ⇒ 旧 sid 已 reap ⇒ running job 必丢，fail-fast
        else:
            pass          # 重试耗尽，同样按 session_lost 处置
        for job in jobs:
            if job.status == STATUS_RUNNING:
                await self.job_store.mark_failed(job.job_key, "session_lost")
        inc_job_event(self.name, "session_lost")
```

- 无 active job → 整个恢复过程不产生任何 WS 连接（满足 R1「无 job 不建连」）；
- 恢复期间 device 恰好上线：`_ensure_device` 经 `start_lock` 串行化，幂等；
- **窗口纪律**：恢复以「断开时刻 + 20s」为硬约束（§2.1），因此并发度、重试次数与
  退避预算之和须控制在秒级；重试只对抗瞬时 RPC 失败，不指望跨窗口救命。

### 5.5 结果投递（R2）：JobResultDeliverer（D6/D13）

新组件 `agent_relay/delivery.py`：

```python
class JobResultDeliverer:
    def __init__(self, job_store, publisher: PushPublisher, cfg: AgentRelayConfig):
        self._device_locks: dict[str, asyncio.Lock] = {}   # 投递串行化（D13）

    async def deliver(self, job: JobRecord, *, session_id: str = "") -> bool:
        """投递单条 done job。成功 → mark_delivered；失败 → 保持 done。"""
        async with self._device_locks.setdefault(job.device_id, asyncio.Lock()):
            job = await self._job_store.get(job.job_key)   # 锁内重读，防与对侧路径双发
            if job is None or job.status != STATUS_DONE:
                return False
            msg = PushMessage(
                device_id=job.device_id,
                session_id=session_id,          # 不下发（push_subscriber 不透传）
                message_id=f"msg_agent_job_{job.job_key}",   # 确定性幂等键（D6）
                text=job.result_text,
                is_final=True,
                agent_name="hermes",
                proactive=True,
            )
            if await self._publisher.publish(msg):
                await self._job_store.mark_delivered(job.job_key)
                inc_job_event("hermes", "delivered")
                return True
            inc_job_event("hermes", "delivery_pending")
            return False

    async def sweep_device(self, device_id: str, *, session_id: str = "") -> int:
        """设备上线补投：按完成顺序投递所有 done 未投递 job。"""
        delivered = 0
        for job in await self._job_store.list_pending_delivery(device_id):
            if await self.deliver(job, session_id=session_id):
                delivered += 1
            else:
                break      # 设备又掉线：保序，剩余等下次上线
        return delivered
```

**两个调用点**：

1. `HermesAdapter._handle_background_complete`：`mark_done(job_key, text)` 后调
   `deliver(job)`（替代现存的 push_callback 直连路径；NOOP/`error:` 前缀按 D14
   处理——不入 `result_text`，`mark_done` 后直接 `mark_delivered`）。投递判定只依赖
   `conn:{device_id}`（PushPublisher 内置），**不要求存活 livachat session**
   （session_id 不下发，§2.2 已验证）；
2. `AgentSessionManager.on_session_started`：`asyncio.create_task(
   deliverer.sweep_device(device_id, session_id=event.session_id))`（D7，异步不阻塞
   事件主链路），再走既有逻辑。

**时序安全性**：`session.started` 到达时 app 的 WS 已注册 `conn:`（先连 WS 后发
session.start），PushPublisher 能解析到实例。

### 5.6 AgentSessionManager / 接口变更

| 位置 | 变更 |
|------|------|
| `on_session_started` | + `asyncio.create_task(deliverer.sweep_device())`（R2）；adapter `on_session_start` 语义不变（内部改为 ensure device session） |
| `on_session_ended` | **删除** `orphan_session_jobs()` 调用；adapter `on_session_end` 改为：停止 watcher、清 livachat 状态；device 有 active job → 全部保留；无 active job → 按 D8 回收连接（关 WS 留 session） |
| `_on_adapter_push` | 签名由 `(session_id, chunk)` 改为 `(device_id, session_id, chunk)`；**仅服务 live nudge 推送**（仍要求内存 session 存在），job 结果走 deliverer 不再经过此路径 |
| `AgentAdapter.set_push_callback` | 回调签名同步加 `device_id` |
| ChatlogWatcher | `_outstanding_jobs()` 改调 `outstanding_jobs_for_device(device_id)`（R3）；hermes.py:690 的 job 派发前检查同步改 device 计数（§5.8） |
| `shutdown` | adapter close 前，若存在 `done && !delivered` job，记 `deferred` metric 并日志（结果留在 Redis，下次启动恢复/补投） |

### 5.7 Prompt 模板调整

`prompts/hermes_monitor.md`（D3 下为**必需**而非优化）：mission 中写死的
`chat_session_id == "$chat_session_id"` 改为动态语义——「监听本 device 的对话；当前
chat_session_id 与 chatlog 路径以每次 NUDGE 通知为准」（`$path_hint` 已有类似措辞，
可推广；NUDGE payload 已携带这两个字段，模板措辞收敛即可）。复用 session 时由
mission 更新 NUDGE 完成切换（§5.2）。`background_task.md` 不变（任务派发时渲染的是
当时值，job 执行期不变）。

### 5.8 per-device 配额与背压（D1 价值落地）

- `max_concurrent_jobs_per_session`（默认 10）**语义改为 per-device**，配置键改名
  `max_concurrent_jobs_per_device`（旧键保留一个版本的 deprecated alias）；检查点
  （hermes.py:691）改用 `outstanding_jobs_for_device` 计数；
- 全局 `max_in_flight_jobs`（默认 20）不变；与 48h running 超时的联动评估见 §8.4；
- 单 device nudge 在 device `lock` 下串行（现状平移），天然构成 per-device 背压；
- 以上即 per-device 隔离性的最小落地：任一 device 的连接故障、重连、job 风暴不影响
  其他 device 的投递链路。

---

## 6. 配置变更

```yaml
# configs/*/server.yaml — agent_relay.adapters.hermes（2026-10-17 清理后）
hermes:
  mode: proactive                     # proactive: 仅 chatlog watcher；both: 加实时问答
  base_url: "http://localhost:9119"
  # Hermes SESSION_TOKEN；值在仓库根 .env 的 HERMES_AUTH_TOKEN（gitignored，勿提交）
  api_key: "${HERMES_AUTH_TOKEN}"
  nickname: "小黑"
  # 以下两项与 HermesAdapterConfig 默认值一致，保留以便运维可见
  background_timeout_s: 172800        # running 超时 48h，与 JobStore TTL 对齐（R2）
  max_concurrent_jobs_per_device: 10  # per-device 配额
```

- **清理原则**：本节 v2 引入的连接/恢复/结果/留存键（`startup_recovery_enabled`、`recovery_concurrency`、`session_resume_*`、`reconnect_*`、`max_stored_result_bytes`、`deliver_pending_on_reconnect`、`session_retention`、`device_conn_idle_reaper_s`、`max_device_connections` 等）全部与 `HermesAdapterConfig` 默认值一致，已从 YAML 移除；行为不变，代码默认值即配置。
- **env 覆盖**：`services/shared/config/loader.py` 的 `_apply_env_overrides` 会把 `LIVACHAT__AGENT_RELAY__ADAPTERS__HERMES__*` 合并进 YAML dict（先于 `AgentRelayConfig.model_validate`），因此被移除的键仍可用 env 临时覆盖。
- **token 迁移**：`api_key` 改为 `${HERMES_AUTH_TOKEN}`；值写入仓库根 `.env`（gitignored），`.env.example` 已加占位符。本次只迁移、未修改 token 值。
- `JOB_TTL_S` 维持 `2 * 24 * 3600`，注释统一为 48h；`configs/test/server.yaml`（本地未跟踪）已同步同样清理。
- 旧键 `max_concurrent_jobs_per_session` 的 deprecated alias 仍保留在 `HermesAdapterConfig` 一个版本；YAML 不再使用。

## 7. Metrics

复用 `livachat_agent_job_events_total{adapter, event}`，event 取值定稿：

| event | 含义 |
|-------|------|
| `completed` / `noop_output` / `rpc_error` / `timeout` / `orphaned` | 既有，语义不变 |
| `delivered` | job 结果投递成功（live 与补投合并计数；区分经日志字段 `delivery_path=live|sweep`，**不再单设 `swept`**） |
| `delivery_pending` | 投递时设备离线，留待补投 |
| `session_lost` | resume 返回新 sid 或恢复重试耗尽，**确有 running job 被置 `failed(session_lost)`** 时计数；无 running job 的空闲重建（冷唤醒常规路径）不计数（仅日志） |
| `deferred` | relay shutdown 时仍 done 未投递（结果留 Redis 待下次补投） |

新增 gauge（**必选**，非可选）：`livachat_agent_device_connections` — 当前 per-device
WS 连接数。**指标纪律**：严禁以 `device_id` 为 label（无界基数）；per-device 细节走
日志，指标只保留聚合计数。

## 8. 边界与失败语义

### 8.1 投递可靠性

- 投递是 at-most-once（Pub/Sub）+ 应用层重试（补投 sweep）：publish 成功但 app 未渲染
  （恰好在途断连）→ 该条标记 delivered 不再补投。接受（与主链路 ai_text 语义一致）；
- relay 在 publish 成功与 `mark_delivered` 之间崩溃 → 下次上线重投，客户端按
  `msg_agent_job_{job_key}` 去重（D6；客户端任务见 §11-Q2）；
- live 推送与补投 sweep 的双发竞态由 per-device 投递锁 + 锁内重读消除（D13）；
- 补投顺序：`completed_at` 升序，逐条投递，中途失败即停（保序 + fail-fast）。

### 8.2 Hermes session 失效与 20s 窗口

- 运行期单 device WS 断连：该连接的 reader 按退避+jitter 重连 + `session.resume`；
  20s 窗口内 resume → live 复用，在途 job 不受影响；窗口后 resume 返回新 sid →
  该 device running job 的结果必丢（complete 发往旧 sid）→ 置
  `failed("session_lost")`，**fail-fast 不等 48h 看门狗**（D10）；
- relay 整体重启：所有连接同时断开，20s 窗口自断开时刻起算——恢复必须
  **有界并发 + running 优先**（D9），requested-only device 不恢复；
- 无 active job 的 idle session 错过窗口可接受：stored_session_id 走 DB 重建（历史
  保留，换新 sid），下次 nudge 正常继续；
- 根除路径：Hermes 上游若让 `prompt.background` 注册 async_delegation，有 running
  job 的 session 即可豁免 20s reap（§11-Q3）。本期不依赖上游改动。

### 8.3 重连风暴防护

- 现有固定 `sleep(2.0)` 重连在单连接下无害；per-device 下 N 个 reader 会在同一 2s
  刻度同步重连 + resume，对 Hermes 网关形成周期性冲击；
- 对策（D11）：每连接独立指数退避（1s→30s）+ ±25% jitter，连接成功后重置；
- 启动恢复侧由 `recovery_concurrency` 信号量封顶（D9）。

### 8.4 连接规模与 4090 上限

- **当前目标规模：~10 并发 device**，距 Hermes 4090 活跃会话上限
  （hermes_websocket_api.md §错误码表）与 `max_device_connections`（默认 1000，
  仅作安全阀）都有两个数量级余量，**4090 不做背压测试**；
- 4090 超限处理保持轻量：`session.create` 收到 4090 错误码 → 记 metric + 告警
  （接入现有适配器错误路径），不做自动回收重试；规模增长后再评估背压策略（§11-Q4）；
- **inflight 联动**：`background_timeout_s` 提至 48h 后，Hermes 侧静默死掉的 job
  （complete 永不到达）占 inflight 坑位最长 48h；全局硬上限 20 个被僵尸 job 占满即
  全局封堵新派发。对策见 §11-Q4（容量评估 + 必要时 per-device 配额收紧）；
- **Hermes 侧 per-token 并发连接限制**：上线前置确认项（§11-Q1，blocker）。

### 8.5 并发与一致性

- 同一 device 的建连/session 建立/resume/回收经 `start_lock` 串行；turn 经 `lock`
  串行（现状平移）；
- 同一 device 同时刻两个 livachat session（异常场景）：共享 device 连接与 hermes
  session，watcher 各自独立，nudge 在 device 锁下串行；
- 投递经 per-device 投递锁串行（D13）；`mark_delivered` 幂等；
- SM 的 `_sessions`（livachat 维度）与 adapter 的 `_devices`（device 维度）无强一致
  要求：连接回收以 JobStore + SM 状态查询为准，reaper 兜底；
- 同一天多通对话（同 device）：共享 hermes session，NUDGE 按最新 chat_session_id
  过滤（§5.7）；复用 session 上的旧对话 nudge 残留沿用 v2 straggler 防御，不变。

### 8.6 已知限制（本期接受）

- `requested` 状态 job 无法断点重派（`spec.task` 未持久化）→ 看门狗 60s 收敛；后续
  如需可在 JobRecord 增加 `spec` 字段；
- 升级窗口一次性丢失：旧 key 不迁移（D15），部署时在途 running job 的结果按 §8.2
  语义丢弃、由看门狗收敛；~10 device 规模下接受，部署避开 job 高峰即可；
- relay 停机 > 20s 期间完成/在途的 job 结果丢失（at-most-once）；Phase 2 可选：
  ① 上游 delegation 修复（Q3）；② 恢复时按 `task_id`（即 detached agent 的
  session_id）向 Hermes 对账；③ Redis Stream + 事件补拉；
- hermes 侧 session 保留依赖 `close_on_disconnect=false` 与 stored_session_id 重建
  能力（既有 §7 已依赖，非新增假设）；
- 单 device 极长期累积的 chat 上下文由 hermes 侧管理，relay 不做截断；
- `failed` 结果（含 timeout/session_lost）不投递——用户侧无感知；如需失败通知属
  产品决策，另立需求。

## 9. 改动清单

| 文件 | 改动 | 性质 |
|------|------|------|
| `agent_relay/job_store.py` | JobRecord 加字段；device 索引；`mark_done(text)`/`mark_delivered`/`list_pending_delivery`/`list_active_by_device`/`outstanding_jobs_for_device`；watchdog timeout 语义；状态枚举清理 | 修改 |
| `agent_relay/adapters/hermes.py` | 重构为 per-device `_DeviceConnection`；懒建连；**重连退避+jitter**；`recover_active_devices`（有界并发+优先级）；`ensure_session(device)`；background.complete 走 deliverer；连接回收 + LRU | **重大重构** |
| `agent_relay/delivery.py` | `JobResultDeliverer`（deliver + sweep_device + per-device 投递锁） | 新增 |
| `agent_relay/session_manager.py` | on_session_started 加异步 sweep；on_session_ended 删 orphan；push 回调签名加 device_id；shutdown 记 `deferred` | 修改 |
| `agent_relay/adapters/base.py` | push callback 签名 `(device_id, session_id, chunk)` | 修改 |
| `agent_relay/chatlog_watcher.py` | outstanding_jobs 改 device 查询 | 小改 |
| `agent_relay/config.py` | `background_timeout_s` 默认 172800；新增 §6 配置项；配额键改名 + alias | 修改 |
| `agent_relay/prompts/hermes_monitor.md` | chat_session_id 改动态语义 | 修改 |
| `agent_relay/metrics.py` / `shared/observability/metrics.py` | 新增 event 取值 / device_connections gauge | 小改 |
| `app/application.py` | 装配 `JobResultDeliverer`；connect 改为条件路径（watchdog 恒启动，WS 懒建） | 小改 |
| `configs/*/server.yaml` | hermes 段新增配置项 | 修改 |
| `tests/unit/agent_relay/` + `tests/integration/agent_relay/` | 新增 deliverer / 启动恢复 / 跨 session 复用 / 风暴防护用例；调整 session 结束行为用例 | 新增+修改 |

不改：`packages/api-schema`（ai_text 字段够用）、gateway/push_subscriber、
orchestrator 事件发布点、Redis channel 拓扑、openclaw adapter（不使用 JobStore）。

## 10. 测试计划

| 层 | 内容 |
|----|------|
| unit | `test_job_store.py`（已随去 Redis 移除；现由 fakeredis 单测与 `test_job_store_sqlite.py` 覆盖）：新字段兼容读、device 索引、`list_pending_delivery` 排序、TTL、`mark_delivered` 幂等 |
| unit | `test_delivery.py`（新）：deliver/sweep 语义、per-device 锁防双发、确定性 message_id、失败保序 break |
| unit | `test_hermes.py`：device 维度映射、复用/新建判定、懒建连、**重连退避+jitter 单测**、LRU 驱逐、resume 新 sid → session_lost |
| integration | `mock_hermes_server.py` 扩展：`session.resume` 语义（同 sid 复用 / 新 sid 重建）、断连后孤儿收敛模拟 |
| integration | **恢复并发度与优先级**：100 device（含 running job）在 20s 窗口内完成关键恢复；requested-only device 不建连 |
| integration | **风暴曲线**：模拟 Hermes 重启，N=50 device 连接的重连时间分布无同步尖峰 |
| e2e | job 运行中 device 离线 → 完成 → 重连 → 收到补投 ai_text（幂等）；同 device 两段 livachat session 复用同一 hermes session；`max_device_connections` 驱逐 |
| e2e | asr_only 模式回归 + 手动 hermes 实测 |

## 11. 开放问题与上线前置项

1. **Q1 — Hermes per-token 并发连接限制（BLOCKER，上线前置）**：per-device 一条 WS
   意味着同一 API token 在 hermes 网关上有 N 条并发连接，接入限制与配额/计费模型
   须在实现前拿到 Hermes 团队书面确认；拿不到确认则本方案无法上线。
2. **Q2 — 客户端 message_id 去重（客户端任务立项）**：iOS/Android 按 `message_id`
   去重是 D6 幂等故事的最后一环；未落地前，崩溃窗口重投会出重复气泡（概率低）。
3. **Q3 — Hermes 上游 issue：`prompt.background` 注册 async_delegation**：可根除
   20s reap 对 running job 的影响（§8.2），成本远低于 Redis Stream 方案，建议本期
   即提交 issue 跟踪。
4. **Q4 — 容量评估**（§8.4）：① inflight 上限 × 48h 超时——评估僵尸 job 占坑风险，
   必要时下调 running 超时（与 48h 结果保留窗口解耦）或上调全局上限 + 收紧 per-device
   配额；② 连接规模增长超出 ~10 device 量级后，再评估 4090 背压与连接容量测试。
5. **Q5 — 补投消息的客户端呈现**：proactive 气泡即可，还是需 `reply_to` 锚定原对话
   （当前 result 不携带 transcription 锚点）？产品决策。
6. **Q6 — reactive `ask()` 的连接策略**：未来开启 reactive 时评估 ephemeral 连接
   方案（§5.2）。
7. **Q7 — 结果 > 48h 才重连**：TTL 过期即弃，不做更久暂存（延续 v2 结论）。

## 12. 分阶段实施

| 阶段 | 内容 | 验收 |
|------|------|------|
| **S1** | JobStore v2（字段、device 索引、状态机、截断） | fakeredis 单测全绿 |
| **S2** | HermesAdapter per-device 重构（行为平移 + 退避/jitter，先不改语义） | 既有 hermes 集成测试全绿；重连风暴曲线无尖峰 |
| **S3** | 懒建连 + 启动恢复（有界并发/优先级/session_lost）+ device 映射（R1/R3） | 集成：kill relay → 重启 → running job 的 complete 正常投递；20s 窗口并发恢复测试 |
| **S4** | JobResultDeliverer + 补投（R2）+ SM/watcher 接线 + prompt 模板 | 集成：离线完成 job → 重连 → 收到 ai_text（幂等、保序、不双发） |
| **S5** | 配额改名/连接上限/metrics，e2e 联调 | asr_only e2e + LRU 驱逐测试 + soak 报告 |
