# Job Status 通知 — background job 运行状态推送（变化驱动 + 周期节流）
> **已更新**：推送链路改为进程内直接投递（PushPublisher → ConnectionManager），见 [chatserver_no_redis.md](chatserver_no_redis.md)。

> **状态**：评审定稿（待实施）
> **日期**：2026-08-16
> **合并说明**：本稿为 [`fd_job_status-k3.md`](fd_job_status-k3.md) 与
> [`fd_job_status-d4.md`](fd_job_status-d4.md) 的评审合并稿，取代前两稿。主体架构采 k3
> （单例 notifier + 扫描兜底 + per-device 串行化 + 独立 push 载荷）；字段命名、钩子契约、
> metrics 拆分、测试粒度采 d4；两稿共同遗漏的 `from_mapping` 转换清单问题已纳入 §4.2。
> **前置文档**：[`fd_agent_relay_service_v2.md`](fd_agent_relay_service_v2.md)（Job 生命周期与
> 投递）、[`ws-protocol-design.md`](ws-protocol-design.md)（WS 协议）、
> [`hermes_adaptor_design_v2.md`](hermes_adaptor_design_v2.md)（Hermes 交互）
> **范围**：`services/agent_relay/`（新增 `job_status.py`，改 `job_store.py` / `models.py` /
> `publisher.py` / `session_manager.py` / `config.py` / `metrics.py`）、
> `services/gateway/push_subscriber.py`、`services/app/application.py` 接线、
> `packages/api-schema/` 新增 s2c 消息
> **明确不在本期**：client SDK / App 侧的接收与展示设计（见
> `android_agent_job_status.md`；本期货架上仅要求 DTO 重新生成以通过 CI 一致性检查，见 §4.1）

---

## 1. 背景与需求

### 1.1 现状行为

agent relay v2 落地后，Hermes 后台 job 已有完整的生命周期管理（`requested → running →
done → delivered / failed`，`job_store.py:26-32`）与结果可靠投递（`JobResultDeliverer`，
`delivery.py`）。但 **job 处于 running 期间，device 端完全感知不到进度**：

- job 启动时仅有一条一次性的 `ai_text` summary（`hermes.py:1249-1255`，
  `notify_job_started` 开关控制，config.py:36 默认开），之后直到结果投递前没有任何
  状态更新；
- device 无法回答「现在有几个后台任务在跑、每个跑了多久」，无法区分「job 还在跑」
  与「job 丢了/超时了」。

### 1.2 需求

| # | 需求 | 概述 |
|---|------|------|
| R1 | **running 状态全量快照通知** | 设备有 status==running 的 job 时，server 周期性（15s）向 device 推送 `job_status` 消息；每次携带**全部** running jobs，不做增量 |
| R2 | **变化即时通知** | running jobs 数量或内容（快照任一字段）发生变化时**立即**推送（含变为 0 个的清除信号）；无变化且 running ≥ 1 时才走 15s 周期 |
| R3 | **时间字段** | 每个 job 携带开始时间（`started_at_ms`，进入 running 的时刻）与已运行时长（`running_time_ms`） |
| R4 | **在线检查 + 上线首报** | 推送前检查 device 是否 online，离线不推送；收到 `session.started` 事件时触发该设备的**首次** `job_status` 通知 |

### 1.3 非目标（本期不做）

- client SDK / App 的消息处理与 UI 展示设计（见 `android_agent_job_status.md`）；
- 增量/去重推送（保持全量快照 + 客户端 replace 语义，§4.1）；
- `requested` 状态 job 的通知（需求明确只通知 running；`requested` 是瞬时态，
  60s 内被 watchdog 收敛，见 §2.1）；
- 离线推送（APNs/FCM）与离线期间的状态缓存——快照无补投语义，重连后由
  `session.started` 首报与扫描 tick 补齐；
- 多 relay 实例部署（保持现状：relay 单实例嵌入 LivaChat Server）；
- job 进度百分比等 running 期动态字段（指纹机制已为其预留，见 §3.1-D2）。

---

## 2. 调研结论（源码级，已逐条验证）

### 2.1 Job 状态机与变更点（通知的数据源）

job 状态全部收敛在 `JobStore`（`services/agent_relay/job_store.py`）的 Redis hash 中，
状态流转**只有四个入口**，且全部是 CAS（`_cas_transition`，job_store.py:356-389）：

| 流转 | 方法 | 触发方（代码事实） | 对 running 集合的影响 |
|------|------|--------------------|-----------------------|
| `→requested` | `create()`（job_store.py:92-122） | `hermes._handle_job_request` | 无（需求不通知 requested） |
| `requested→running` | `mark_running()`（job_store.py:124-138） | `_dispatch_job_session`（bind 后、submit 前）/ job recovery | **+1** |
| `running→done` | `mark_done()`（job_store.py:157-185） | `_handle_job_message_complete` / `_resume_job_session`（transcript 恢复） | **-1** |
| `requested/running→failed` | `_finish()`（job_store.py:140-155），经 `mark_failed` / `mark_orphaned` | watchdog（`scan_stale`）、dispatch/gateway 失败、`cancel_job`、`_handle_job_error`、job recovery | running 时 **-1**，requested 时无影响 |
| `done→delivered` | `mark_delivered()`（job_store.py:187-205） | `delivery.py:101` | 无（早已离开 running 集合） |

**结论**：在 `mark_running` / `mark_done` / `_finish` 三处 CAS 成功后埋钩子，即可
100% 覆盖 running 集合的变更，无遗漏路径；watchdog 收敛（timeout/session_lost）也走
`_finish`，自动被钩子覆盖。**单点汇聚**，不需要在 hermes 的多个调用点分别触发。

**关键缺口一**：`JobRecord`（job_store.py:36-56）目前**没有记录进入 running 的时刻**。
`created_at` 是 requested 创建时间；`updated_at` 会被后续流转覆盖（且 watchdog 以它
计算 job 年龄，语义是"最后更新时间"）。R3 要求的 started time 无现成字段 → 必须新增
`started_at`（§4.2）。

**关键缺口二（两稿共同遗漏，本稿合并时补入）**：`from_mapping`（job_store.py:51-56）的
float 转换清单只列了 `created_at/updated_at/completed_at/delivered_at` 四个键。Redis
hash 读回的值全部是 str，`JobRecord` 是普通 dataclass、`cls(**data)` 不做类型强转——
新增 `started_at` 后若不加入该清单，读回的是 **str**，`int(started_at * 1000)` 与
`started_at or created_at` 回退都会静默出错。详见 §4.2。

### 2.2 查询能力（快照构建）

- `device_job_keys(device_id)` + `get(job_key)`：按设备取全量 job（job_store.py:253-255,
  235-239）。注意 device 索引 TTL 48h 且在 `mark_done` 时刷新（job_store.py:116-117,
  181），**含 done/delivered 历史 job**——per-device 全量读的成本随历史增长，这是
  扫描层做预判定的动机（§4.3）；
- `list_active_by_device()`（job_store.py:298-303）：全量 active job 按 device 分组，
  返回的是完整 `JobRecord`。全局 in-flight 上限 `max_in_flight_jobs=20`
  （config.py:31），每轮扫描 = 1 次 SMEMBERS + ≤20 次 HGETALL，成本有硬上界；
- 现有 `outstanding_jobs_for_device()`（job_store.py:270-285）返回的是
  `ACTIVE_STATUSES = {requested, running}`（job_store.py:279），**不能直接复用**——
  需求只通知 running，快照构建需自行过滤 `status == STATUS_RUNNING`。

### 2.3 推送链路与在线检查（复用资产）

推送链路 relay → gateway → device 已存在且被 job 结果投递充分验证：

```
JobStatusNotifier → PushPublisher.publish (publisher.py:33)
  → GET livachat:conn:{device_id} 解析实例 (publisher.py:77-84)
  → PUBLISH livachat:push:{instance_id}
  → PushSubscriber._handle (push_subscriber.py:79)
  → ConnectionManager.get_handler(device_id) (connection.py:426)
  → ConnectionHandler.enqueue_outbound → writer 分配 seq 发出 (connection.py:269-281)
```

- **在线检查**：`JobResultDeliverer.deliver` 已有成熟语义（delivery.py:70-89）——
  `resolve_instance` 为 None（无 conn key）→ 离线；conn key 指向**本实例**但进程内
  无 handler（`ConnectionManager.is_online`，connection.py:430-432）→ 判定为残留 key，
  离线；映射到其他实例 → 以 publish 结果为准。job_status 通知**原样复用该语义**。
  与结果投递不同的是：job_status 无状态变更（不 mark_delivered），校验失败只跳过
  当前帧并保持脏标记，无补投语义。
- **内部 push 载荷**：`PushMessage`（models.py:46-62）当前 `kind` 仅允许 `"ai_text"`；
  `PushSubscriber._handle`（push_subscriber.py:79-99）目前**完全不看 kind**，无条件
  构建 ai_text 帧（`_build_ai_text_frame`，push_subscriber.py:101-129）。两者都需
  扩展支持 `job_status` 帧（§4.4）。注意 push_subscriber 解析的是**裸 JSON dict**
  而非 `PushMessage` 模型——`kind` 缺省按 ai_text 处理的向后兼容很自然。
- **max_reply_bytes 截断的真实行为**：`publish` 只截断 `msg.text`
  （publisher.py:48-50）。job_status 载荷的 jobs 数组**不经过**该截断——载荷大小
  有硬上界：`JobStore.create` 存储层即把 summary 截断至 256 字符
  （`JOB_SUMMARY_MAX_CHARS`，按字符、多字节安全），`_fingerprint` 与载荷构建处对
  存量数据（截断上线前入库）再截断兜底。最终载荷 ≤ 10 jobs × 256 字符 + 定长字段，
  无需复用 max_reply_bytes。（d4 稿 §8 称"summary 复用 max_reply_bytes 截断"，
  与实现不符，本稿更正。）
- **帧的 seq/timestamp**：push 帧 seq 由 writer 在出队时统一分配（connection.py:269-281
  一带），job_status 帧走同一路径，无需特殊处理。

### 2.4 device 上线信号（首报触发点）

`session.started` AgentEvent 的流向（v2 已验证并用于补投）：

```
orchestrator → AgentEventPublisher.publish_session_started (agent_publisher.py:70)
  → Redis livachat:agent:events
  → EventRouter._dispatch "session.started"
  → AgentSessionManager.on_session_started (session_manager.py:84)
```

`on_session_started` 已在同一点触发 `deliverer.sweep_device`（session_manager.py:106-107），
且 v2 已确认该语义："session.started 到达时 conn: 已注册（先连 WS 后发 session.start），
PushPublisher 可解析实例"——首报的触发点与可推送性前提同时成立，**无需新增事件**。

注意：`livachat:agent:events` 是 Redis pub/sub，fire-and-forget
（agent_publisher.py:117-129），EventRouter 重连窗口内事件会丢。v2 的结果补投接受了
这个风险（有 48h sweep 兜底）；**job_status 不能只押在 session.started 上**——这是
保留 1s 扫描 tick 作为兜底路径的核心动机（§3.1-D3）。

### 2.5 协议与代码生成约束

- s2c 消息以 `packages/api-schema/` 为 SSOT：每个消息一个 schema 文件（模板见
  `s2c/ai-text.json`），公共 wrapper 为 `type/seq/timestamp/payload`
  （`common.json#/$defs/WrapperProperties`），判别字段 `type` 用 `const`；
  新增类型须登记进 `s2c/message.json` 的 `anyOf` 与根 `message.json`；
- `common.json` 可用 $defs 含 `SeqNumber`（:62）、`Timestamp`（:68）、`UInt64`（:57）、
  `MessageId`（:13，**带 pattern `^msg_[a-z0-9]{12}$`，不能用于 job id**）等；
- 协议文档同步义务：`api-schema/README.md` Versioning 节（:82-88）要求协议变更同步
  `docs/ws-protocol-design.md`；README Structure 节（:9-34）逐文件登记 schema，
  新增文件须补登；
- DTO 生成：Kotlin 经 `make generate-android-api`（或 `scripts/generate_kotlin_api.py`，
  CI 有 `check-android-api-generated` 一致性检查，**schema 变更后必须同提交重新生成**）；
  Python 经 `scripts/generate_python_api.py` 生成到 `packages/sdk-python`。
  服务端 push 路径（push_subscriber）手工构建 JSON 帧，不依赖生成的 DTO，但
  重新生成仍是仓库约定。

### 2.6 差距汇总

| 需求 | 现状 | 差距 |
|------|------|------|
| R1 全量快照 | 无任何 running 状态通知 | 新 s2c 消息 + 新通知组件 |
| R2 即时通知 | JobStore 无变更钩子 | 三处 CAS 成功点埋 `on_change` 回调 |
| R3 started/running time | `JobRecord` 无 running 起始时刻 | 新增 `started_at` 字段 + `from_mapping` 转换清单同步 |
| R4 在线检查 | deliverer 已有完整语义 | 复用；`session.started` 已有挂点 |

---

## 3. 总体设计

### 3.1 核心决策

| # | 决策 | 理由 |
|---|------|------|
| D1 | **快照集合 = 该 device 全部 status==running job**，`requested` 不通知；空列表仅在「从非空变为空」时作为清除信号发送 | 需求原文；requested→running 间隔通常 <1s（一次 RPC），通知 requested 无价值 |
| D2 | **指纹（fingerprint）驱动的发送判定**：快照按 `started_at` 升序排列，取其 `(job_id, summary, started_at_ms)` 元组作为指纹；「变化」= 指纹与上次成功发送的指纹不等。**`running_time_ms` 是发送时刻计算的派生字段，不进指纹**——否则每帧都"变化"，节流失效。未来新增动态字段（如进度）只需加入指纹元组，判定逻辑不变 | 需求「count 或内容改变立即通知」的统一判定；天然覆盖增删改 |
| D3 | **三触发路径，发送规则统一**（§3.2）：①JobStore `on_change` 钩子（立即，async 回调，JobStore 内 create_task + 吞错）；②1s 扫描 tick（兜底 + 15s 周期计时 + relay 重启/事件丢失自愈，扫描层预判定，§4.3）；③`session.started`（首报，force） | 钩子保证「立即」且与 session 生命周期解耦；tick 兜底钩子遗漏、pub/sub 事件丢失与重启状态丢失，成本有界（in-flight ≤ 20） |
| D4 | **`JobRecord` 新增 `started_at`**（`mark_running` 时写入），**并同步加入 `from_mapping` 的 float 转换清单**；存量数据缺省 0 → 通知层回退 `created_at`；`running_time_ms` 在发送时由 server 计算（`now_ms - started_at_ms`） | R3；缺口一与缺口二（§2.1）一并处理；双字段都发（需求原文），client 也可凭 `session.ready` 的时钟偏移自行续算 |
| D5 | **复用 PushPublisher 链路 + deliverer 式在线检查**；离线不发送、保持脏标记，`session.started` 首报或后续任一触发路径补齐 | 已被 v2 结果投递验证的语义，零新链路 |
| D6 | **内部 push 载荷新增独立 `JobStatusPushMessage`（kind="job_status"）**，不改动 `PushMessage`；push_subscriber 按 `kind` 分支构建帧，`kind` 缺省按 ai_text（滚动升级兼容） | 两种载荷字段完全不同；独立模型避免给 PushMessage 填充 `session_id=""`/`text=""` 等哑值；push_subscriber 本就不经 pydantic 解析 |
| D7 | **通知组件独立于 adapter**：新增 `JobStatusNotifier`（`agent_relay/job_status.py`），挂在 relay 层而非 hermes adapter 内 | 通知是 relay 横切关注点；JobStore 本就 hermes 专属，`agent_name` 以参数注入（对齐 `JobResultDeliverer(agent_name="hermes")` 的既有模式） |
| D8 | **per-device 发送串行化 + 全快照幂等**：同一 device 的评估/发送经 per-device `asyncio.Lock`（对齐 deliverer 的 `_device_locks`，delivery.py:52,65）；快照天然幂等，重复发送无害 | 钩子 task 与扫描 tick 并发触发同一 device 时避免过期帧后发（乱序窗口见 §6） |
| D9 | **wire 语义 = 全量替换（replace）**：客户端用每帧内容整体替换展示状态，不做增量合并；空列表 = 清空展示 | 帧幂等，client 无须去重逻辑 |

### 3.2 发送规则（三条触发路径共用一份判定）

记号：`cur` = 当前 running 快照指纹；`last` = 上次**成功发送**的指纹；
`last_sent_at` = 上次成功发送时刻。

| 条件 | 动作 |
|------|------|
| `cur` 非空 且 `cur != last` | **发送**（变化：新增/完成/失败/内容变），metric `sent` |
| `cur` 为空 且 `last` 非空 | **发送空列表**（清除信号，R2 的 →0 情形），metric `sent` |
| `cur` 非空 且 `cur == last` 且 `now - last_sent_at >= 15s` | **发送**（周期心跳，R1），metric `sent_throttled` |
| force（session.started 首报）且 `cur` 非空 | **发送**（无视 15s 计时），metric `sent` |
| 其余（`cur` 空且 `last` 空/无记录、周期未到、设备离线） | 不发送（离线记 `skipped_offline`，保持脏标记） |

关键不变式：

1. **只有发送成功才更新 `last` / `last_sent_at`**；设备离线导致发送跳过时状态保持脏，
   之后任一触发路径会重试（首报 force 路径保证重连后必然拿到当前快照）；
2. **`cur` 空且 `last` 空/无记录时不发空列表**（原 k3-O2 裁决，本稿定稿）：client 新
   会话本就没有待清除状态，每次 session.started 都发空帧是纯噪声；
3. **成功发送空快照后删除该 device 的状态条目**——此后评估视为「无记录」，与
   `last`=空 行为等价，`_last` 字典规模上界 = 有 running job 的设备数 + 脏设备数；
   长期失联设备（条目旧于 job TTL 48h）由 scan tick 按龄淘汰兜底——该设备
   running job 已随 TTL 过期，旧指纹无保留价值，重连后由首报按当前快照重新评估
   （在线设备的条目每 15s 帧刷新，永不受淘汰波及）；
4. 变化帧发送成功会刷新 `last_sent_at`，节流计时自然顺延，避免紧邻双帧。

### 3.3 目标架构

```
                        ┌──────────────────────── agent_relay ────────────────────────┐
 hermes adapter         │                                                             │
  mark_running/done ───▶│ JobStore ── create_task(on_change(device_id)) ──┐           │
  watchdog _finish ────▶│   (started_at 新字段, from_mapping 同步)        ▼           │
                        │                                           JobStatusNotifier │
 EventRouter            │   session.started ── force ──▶  · 1s scan tick（预判定）     │
  on_session_started ──▶│   (session_manager 挂点)       · per-device lock            │
                        │                              · 指纹判定 (§3.2)              │
                        │                              · 在线检查 (deliverer 语义)     │
                        └──────────────┬──────────────────────────────────────────────┘
                                       │ JobStatusPushMessage (kind=job_status)
                                       ▼
                          PushPublisher → Redis livachat:push:{instance}
                                       ▼
                          PushSubscriber（kind 分支 → job_status 帧）
                                       ▼
                          ConnectionHandler.enqueue_outbound → device
```

### 3.4 关键时序

**job 启动（requested→running）即时通知**：

```
hermes: prompt.background 返回 task_id
  → job_store.mark_running(job_key, task_id)        # CAS 成功，写 started_at
  → job_store._notify_change → create_task(notifier.on_job_change)
  → _evaluate(device_id)（锁内重读）
  → cur(1 job) != last(∅) → 在线检查 → publish
  → device 收到 job_status{jobs:[{...}]}             # < 100ms 量级
```

**job 完成（running→done）清除通知**：

```
hermes: background.complete → mark_done → on_change
  → _evaluate: cur(∅) != last(1 job) → 发送空 jobs 列表   # client 清除指示
  → 发送成功后删除该 device 状态条目（§3.2 不变式 3）
  → （并行）deliverer.deliver 投递结果 ai_text             # 既有行为不变
```

**设备重连首报**：

```
client WS 连接 + session.start → orchestrator 发布 session.started
  → EventRouter → AgentSessionManager.on_session_started
  → ├─ deliverer.sweep_device(device_id)     # 既有：补投结果
  → └─ notifier.on_device_online(device_id)  # 新增：force 评估
        → 有 running job → 立即发送当前快照（无视 15s 计时）
        → 无 running job 且上次非空（离线期间清零）→ 发送空列表清除
        → 无 running job 且无记录 → 静默（§3.2 不变式 2）
```

**稳态周期**：1s tick 扫 `list_active_by_device` → 内存中构建候选指纹预判定 →
仅疑似需发送的设备进入 `_evaluate`（锁内重读权威快照）→ §3.2 判定 →
无变化且 running≥1 且距上次发送 ≥15s → 发送（client 刷新 running_time_ms 显示）。

**relay 重启自愈**：内存指纹丢失 → 首 tick（≤1s）候选指纹对未知 `last` 视为变化 →
在线设备立即补发，**不依赖 session.started 事件**（pub/sub 丢失窗口由 tick 兜底）。

---

## 4. 详细设计

### 4.1 协议：新增 s2c `job_status` 消息

新增 `packages/api-schema/s2c/job-status.json`（模板对齐 `ai-text.json`）：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://livachat.dev/schemas/s2c/job-status.json",
  "title": "job_status — Background Job Status Snapshot",
  "description": "Server pushes the FULL snapshot of the device's running background jobs. Sent immediately when the running set changes (including dropping to zero, which clears client state), otherwise every 15s while at least one job is running; silent while unchanged and empty. Each frame is a full replacement: the client MUST replace its displayed job status with this payload. An empty jobs array clears the display and is only sent on a non-empty→empty transition; the client MUST NOT rely on periodic empty frames to maintain cleared state. First notification is triggered by session start. Not incremental.",
  "type": "object",
  "required": ["type", "seq", "timestamp", "payload"],
  "properties": {
    "type": { "const": "job_status" },
    "seq": { "$ref": "../common.json#/$defs/SeqNumber" },
    "timestamp": { "$ref": "../common.json#/$defs/Timestamp" },
    "payload": {
      "type": "object",
      "required": ["jobs"],
      "properties": {
        "jobs": {
          "type": "array",
          "description": "All currently running jobs, sorted by started_at_ms ascending. Empty array = no running jobs (clear signal, only sent when the previous snapshot was non-empty).",
          "items": {
            "type": "object",
            "required": ["job_id", "agent_name", "summary", "started_at_ms", "running_time_ms"],
            "properties": {
              "job_id": {
                "type": "string",
                "description": "Job identifier (relay job_key, uuid hex). Matches the dedup key suffix of job result delivery: the result ai_text carries message_id msg_agent_job_{job_id}."
              },
              "agent_name": {
                "type": "string",
                "description": "Agent that owns the job. Currently always \"hermes\"."
              },
              "summary": {
                "type": "string",
                "minLength": 0,
                "description": "One-line task summary from the main agent (same text as the job-started ai_text)."
              },
              "started_at_ms": {
                "$ref": "../common.json#/$defs/Timestamp",
                "description": "When the job entered running (epoch ms, server clock)."
              },
              "running_time_ms": {
                "$ref": "../common.json#/$defs/UInt64",
                "description": "Elapsed running time in ms, computed at send time: server_now_ms - started_at_ms. Derived field; refreshed on every frame."
              }
            },
            "additionalProperties": false
          }
        }
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false,
  "examples": [
    {
      "type": "job_status",
      "seq": 41,
      "timestamp": 1712345687000,
      "payload": {
        "jobs": [
          {
            "job_id": "9f3c2ab4d1e84f0c9f3c2ab4d1e84f0c",
            "agent_name": "hermes",
            "summary": "正在查询东京机票",
            "started_at_ms": 1712345640000,
            "running_time_ms": 47000
          }
        ]
      }
    },
    {
      "type": "job_status",
      "seq": 42,
      "timestamp": 1712345690000,
      "payload": { "jobs": [] }
    }
  ]
}
```

字段与命名说明：

- `job_id` 用裸 `string`——**不得** `$ref` `MessageId`（其 pattern `^msg_[a-z0-9]{12}$`
  与 uuid hex 不匹配，d4 稿此处有误，本稿更正）；与结果投递去重键
  `msg_agent_job_{job_key}`（delivery.py:93）的关联写入 description，便于 client 将
  状态项与结果 ai_text 对齐；
- 时间字段统一 `_ms` 后缀（`started_at_ms` / `running_time_ms`），单位自描述；
- `agent_name` 本期恒为 `"hermes"`，为多 adapter 演进预留（§8-O4）；
- wire 帧**不含** `message_id`：重复/节流帧由 replace 语义天然幂等，客户端无须也不应
  按消息 id 去重；
- **不含** `hermes_task_id`（内部关联键，无客户端价值）。

登记与同步（仓库协议变更三件套，§2.5）：

1. `packages/api-schema/s2c/message.json` 的 `anyOf` 增加 `{ "$ref": "./job-status.json" }`；
   根 `message.json` 同步；
2. `docs/ws-protocol-design.md` §2 增加 `job_status` 消息定义小节（语义、触发规则、
   字段表，内容与本文 §3.2/§4.1 一致）；
3. `packages/api-schema/README.md` Structure 节补登 `job-status.json`。

### 4.2 JobStore：新增 `started_at` + 变更钩子

**a) `started_at` 字段（含 from_mapping 修复，两稿共同盲点）**：

```python
@dataclass
class JobRecord:
    ...
    started_at: float = 0.0   # 进入 running 的时刻（epoch s）；0 = 未进入/存量数据
```

- `mark_running()` 的 CAS 字段追加 `("started_at", time.time())`；
- **`from_mapping`（job_store.py:53）的转换清单必须同步加入 `"started_at"`**：
  Redis hash 读回全为 str，dataclass 不强转，缺这一步则新数据读回是 str，
  `int(started_at * 1000)`（str repetition / TypeError）与 `started_at or created_at`
  回退（非空 str 恒 truthy）都会静默出错。存量数据该键缺省 → 默认 0.0，无迁移；
- 通知层读取时 `started_at or created_at` 回退（存量 running job 近似为创建时刻）；

**b) 变更钩子（async 回调，JobStore 内调度 + 吞错）**：

```python
class JobStore:
    def set_change_listener(
        self, cb: Callable[[str], Awaitable[None]] | None
    ) -> None:
        """Register a fire-and-forget callback invoked after transitions that
        change the running snapshot: mark_running (requested→running, +1),
        mark_done (running→done, -1), _finish (active→failed, covers watchdog
        convergence / session_lost / rpc_error; -1 when running). Invoked with
        device_id. Never blocks or raises into the transition path."""

    async def _notify_change(self, device_id: str) -> None:
        if self._change_listener is None:
            return
        try:
            asyncio.create_task(self._change_listener(device_id))
        except Exception:
            logger.warning("job status change callback failed", exc_info=True)
```

- 触发点严格限定三处（§2.1），均在 CAS 成功（`ok == 1`）之后、返回记录之前：
  `mark_running`、`mark_done`、`_finish`。`create` 与 `mark_delivered` 不影响
  running 集合，**不触发**；CAS 失败不触发；
- `_finish` 从 requested 收敛时也会触发一次无害的多余评估——指纹不变即空转，接受；
- 调度与吞错放在 JobStore 侧（而非依赖 listener 自觉），保证 hermes RPC 回包路径
  不被通知阻塞。

### 4.3 新组件：`JobStatusNotifier`（`services/agent_relay/job_status.py`）

```python
class JobStatusNotifier:
    """Job status snapshot publisher (change-driven + throttle + scan fallback).

    Trigger sources (D3):
    - JobStore change hook: on_job_change() → immediate evaluate;
    - 1s scan tick: fallback + 15s throttle timer + restart self-heal;
    - session.started: on_device_online() → force evaluate (first frame, R4).

    Change detection uses a fingerprint of (job_id, summary, started_at_ms)
    sorted by started_at ascending; running_time_ms is derived at send time
    and excluded from the fingerprint (D2). Only successful sends update
    last-sent state (§3.2). Frames are idempotent full replacements (D9).
    """

    def __init__(
        self,
        job_store: JobStore,
        publisher: PushPublisher,
        *,
        agent_name: str = "hermes",
        interval_s: float = 15.0,          # 周期（R1）
        scan_interval_s: float = 1.0,      # tick 兜底周期
        instance_id: str = "",
        online_check: Callable[[str], bool] | None = None,   # conn_mgr.is_online
    ) -> None: ...

    async def start(self) -> None: ...     # 创建 scan task
    async def stop(self) -> None: ...

    async def on_job_change(self, device_id: str) -> None:
        """JobStore 钩子（已由 JobStore create_task 调度）：立即 _evaluate。"""

    async def on_device_online(self, device_id: str) -> None:
        """session.started 首报：_evaluate(force=True)。"""

    async def _scan_loop(self) -> None:
        """每 scan_interval_s 一轮：
        1. list_active_by_device() —— 1 次 SMEMBERS + ≤20 次 HGETALL，有界；
        2. 用返回的 JobRecord 直接在内存构建各 device 候选指纹（过滤 running，
           started_at 升序），不重读 Redis；
        3. 候选指纹与 _last 比对 + 15s 计时预判定，仅「疑似需发送」的设备进入
           _evaluate —— 锁内按 device 重读权威快照。
        稳态（无变化且周期未到）下每 tick 仅一轮有界全局读，无 per-device 读
        （device 索引含 48h 历史 job，per-device 全量读随历史增长，§2.2）。
        relay 重启后内存指纹丢失：候选指纹对未知 _last 视为变化 → 首 tick 即
        补发（设备在线前提下），实现重启/事件丢失自愈。"""

    async def _evaluate(self, device_id: str, *, force: bool = False) -> None:
        """per-device lock 内（对齐 deliverer._device_locks）：
        0. 在线检查前置（重读之前，deliverer 语义：resolve_instance None →
           离线；本实例且 not is_online → 离线）——离线设备无论决策结果如何
           都不会发送，提前 return 与「判定后检查」语义等价；成本从「每 tick
           全量历史读（N 含 48h done/delivered）」降为「每 tick 1 次 GET」，
           dirty 保持（§3.2 不变式 1）；
        1. device_job_keys + get → 过滤 status==running → 快照（started_at 升序）
           —— 锁内重读（评估即读新，对齐 delivery.py:67），杜绝扫描数据与发送
           之间的 TOCTOU；
        2. fingerprint = tuple((job_id, summary, started_at_ms) for ...)
           —— started_at 取 `record.started_at or record.created_at` 回退后值；
        3. §3.2 判定表；
        4. 需要发送 → JobStatusPushMessage → publisher.publish；
        5. 仅发送成功才更新 _last[device_id] = (fingerprint, now)；
           成功发送空快照后删除该 device 条目（§3.2 不变式 3）。
        循环体 try/except：单 tick 异常记 metric(error) 后继续，永不 crash。"""
```

设计要点：

- **发送量上界**：per-device running 上限 `max_concurrent_jobs_per_device=10`，
  变化风暴最多 10 连发，无 coalesce / min_change_interval 需求（§8-O3）；
- **agent_name 参数化**：对齐 `JobResultDeliverer(agent_name="hermes")` 既有模式，
  与 JobStore 的 hermes 专属 key 前缀（`agent:hermes:job:`）现状匹配；
- **排序**：`started_at` 升序（最早启动的在前），快照输出确定性，client 可直接渲染；
- **状态清理**：`_last` 条目在空快照发送成功后删除（§3.2 不变式 3），内存规模
  有界，无需 LRU。

### 4.4 内部 push 载荷与 gateway 帧构建

`models.py` 新增（与 `PushMessage` 平级，不改动它）：

```python
class JobStatusItem(BaseModel):
    job_id: str
    agent_name: str = "hermes"
    summary: str = ""
    started_at_ms: int = 0
    running_time_ms: int = 0

class JobStatusPushMessage(BaseModel):
    kind: Literal["job_status"] = "job_status"
    device_id: str
    jobs: list[JobStatusItem] = []

    def to_json(self) -> str:
        return self.model_dump_json()
```

`PushPublisher.publish` 签名放宽为 `PushMessage | JobStatusPushMessage`；
`max_reply_bytes` 截断仅对 ai_text 的 `text` 生效（§2.3：job_status 载荷不经过截断，
由 10 jobs × 一行摘要约束大小）。

`gateway/push_subscriber.py`（`_handle` 当前完全不看 kind，本次引入分支）：

```python
async def _handle(self, raw):
    data = json.loads(...)
    kind = data.get("kind", "ai_text")       # 缺省 ai_text：滚动升级兼容
    ...
    if kind == "job_status":
        frame = self._build_job_status_frame(data)
    else:
        frame = self._build_ai_text_frame(data)
    await handler.enqueue_outbound(frame)

def _build_job_status_frame(self, data):
    # payload 逐字段白名单透传（job_id/agent_name/summary/started_at_ms/
    # running_time_ms），不透传内部字段——对齐 _build_ai_text_frame 的白名单风格
    return json.dumps({
        "seq": 1,                             # writer 出队时分配真实 seq
        "timestamp": now_ms(),
        "type": "job_status",
        "payload": {"jobs": [...whitelist...]},
    }, ensure_ascii=False, separators=(",", ":"))
```

### 4.5 AgentSessionManager / application.py 接线

`session_manager.py`：

- 构造新增可选参数 `job_status_notifier: JobStatusNotifier | None = None`；
- `on_session_started`（session_manager.py:84）内、deliverer sweep 之后追加（同受
  relay enabled 大环境约束）：

```python
if self._job_status_notifier is not None:
    asyncio.create_task(
        self._job_status_notifier.on_device_online(event.device_id),
        name=f"job-status-first-{event.device_id}",
    )
```

notifier 无 per-device task 生命周期，**不需要** `on_session_ended` / 自愈挂钩——
设备离线期间的清除与重连补齐由「离线保持脏 + 扫描 tick + 首报 force」共同保证。

`application.py` lifespan（agent_relay 块内，deliverer 创建之后）：

```python
from agent_relay.job_status import JobStatusNotifier

job_status_notifier = JobStatusNotifier(
    job_store, publisher,
    agent_name="hermes",
    interval_s=relay_cfg.job_status_interval_s,
    scan_interval_s=relay_cfg.job_status_scan_interval_s,
    instance_id=instance_id,
    online_check=conn_mgr.is_online,
)
job_store.set_change_listener(job_status_notifier.on_job_change)
await job_status_notifier.start()
# AgentSessionManager(...) 传入 job_status_notifier=job_status_notifier
```

shutdown 段对应 `await job_status_notifier.stop()`（在 `event_router.stop()` 之后、
`handler.shutdown()` 之前）。`job_status_enabled=False` 时不创建 notifier、不挂钩子。
整个接线维持 relay 一贯的「组件失败仅告警、主链路降级」风格（try/except + warning）。

### 4.6 配置变更（`agent_relay/config.py`，`AgentRelayConfig` 根级）

| 键 | 默认 | 说明 |
|----|------|------|
| `job_status_enabled` | `True` | 总开关；False 时不创建 notifier、不挂钩子（兼容旧 client 的应急开关，见 §5） |
| `job_status_interval_s` | `15.0` | 周期推送间隔（R1） |
| `job_status_scan_interval_s` | `1.0` | 扫描 tick 间隔（兜底/计时粒度） |

env 覆盖沿袭 `LIVACHAT__AGENT_RELAY__JOB_STATUS_ENABLED` 等既有规约
（config.py:99-103）；`configs/*/server.yaml` 的 `agent_relay` 段可显式声明。

### 4.7 Metrics

`shared/observability/metrics.py` 的 MetricsRegistry 增加
`agent_job_status_total{result}`（Counter，对齐 `agent_job_events_total` 模式），
`agent_relay/metrics.py` 增加薄封装 `inc_job_status(result)`：

| result | 含义 |
|--------|------|
| `sent` | 变化帧 / 首报 / 空列表清除发送成功 |
| `sent_throttled` | 15s 周期帧发送成功（区分保活频率与变化频率） |
| `skipped_offline` | 在线检查未通过（前置于重读）/ publish 返回 False，保持脏标记。语义为「离线评估数」——含本不会发送的空转评估（如 watchdog 对 requested 的收敛） |
| `error` | tick / 评估异常（任务存活，继续） |

---

## 5. 兼容性与升级

- **旧 client 收到未知 `type=job_status`**：client SDK 的容错行为属于下一阶段设计范围；
  本期提供 `job_status_enabled=false` 服务端开关作为应急回退（先发 client 后开开关，
  或确认 SDK 对未知 type 容错后直接开启）；
- **滚动升级中的 Redis push 通道**：`kind` 缺省按 ai_text 处理（§4.4），旧 gateway
  收到新 `job_status` 载荷时会按 ai_text 构建出空 text 帧——单进程部署下 relay 与
  gateway 同生共死，该窗口仅存在于理论上的分阶段重启，接受（与 v2 §1.3 单实例
  嵌入的部署前提一致）；
- **存量 JobRecord**：无 `started_at` 字段，`from_mapping` 缺省 0.0，通知层回退
  `created_at`（§4.2），无迁移；
- **协议版本**：新 s2c 消息为纯增量，wrapper 不变，无 breaking change。

---

## 6. 边界与失败语义

| 场景 | 行为 |
|------|------|
| job 开始运行（requested→running） | 钩子触发 → 立即变化帧（`running_time_ms`≈0） |
| job 完成/失败（running→done/failed） | 钩子触发 → 立即帧（该 job 消失；若为空则发 `jobs: []` 清空展示，随后删除 device 状态条目） |
| 快照无变化且 running ≥ 1 | 每 15s 节流帧（`running_time_ms` 刷新，指纹不变） |
| 快照无变化且 running == 0 | 静默（空帧只在变化瞬间出现一次） |
| device 离线 | 在线检查 / publish 失败 → 跳过当前帧，保持脏标记，无 backlog；重连后 session.started 首报或扫描 tick 补齐 |
| relay 重启 | 内存指纹丢失 → 首 tick（≤1s）候选指纹视为变化 → 在线设备补发；**不依赖** session.started |
| `session.started` 事件丢失（pub/sub 窗口） | 钩子路径与扫描 tick 均不依赖该事件，周期帧/变化帧不受影响；首报缺失由 tick 的 15s 计时兜底 |
| `conn:{device_id}` 残留 key（崩溃/teardown 窗口） | 本地 `is_online` 校验判离线，跳过（对齐 P1，delivery.py:70-89） |
| 变化帧与节流帧 / 双钩子并发 | per-device `asyncio.Lock` 串行化（D8），锁内重读，无过期帧后发窗口；变化帧刷新 `last_sent_at`，节流自然顺延 |
| 频繁重连（session.started 风暴） | force 评估幂等；锁串行化；cur 空且无记录时静默（§3.2 不变式 2） |
| JobStore 钩子异常 / 通知路径阻塞 | 钩子由 JobStore 内 `create_task` 调度 + 吞错（§4.2b），不阻塞 CAS / hermes RPC 回包路径 |
| 单 tick 异常（Redis 抖动） | 记 metric `error` 后继续，scan 任务不退出 |
| summary 超长 / 帧大小 | job_status 载荷不经过 `max_reply_bytes`（仅截 ai_text text，§2.3）；大小由 ≤10 jobs × 一行摘要约束，有界 |
| `job_status_enabled=false` | 不创建 notifier、不挂钩子，零推送零扫描；既有链路零影响 |

---

## 7. 测试计划

| 层 | 位置 | 用例 |
|----|------|------|
| 单元 | `services/tests/unit/agent_relay/test_job_status.py`（新） | §3.2 判定全表（五行）；**空帧只发一次**（非空→空→后续 tick 静默）；**变化帧刷新 last_sent_at**（interval 内无节流帧）；**指纹排除 running_time_ms**（仅时长变化不触发变化帧）；started_at 缺省回退 created_at；排序确定性；离线与 publish False 不更新 last、保持脏；force 无视计时；session.started 首报空且无记录 → 静默（不变式 2）；空快照发送成功后状态条目删除（不变式 3）；扫描预判定（指纹未变时无 per-device 读，可用 mock 计数断言）；tick 异常吞错任务存活 |
| 集成 | ~~`services/tests/integration/agent_relay/test_job_store.py`~~（已随去 Redis 移除） | 原用例覆盖 `mark_running` 写 `started_at`、钩子触发/不触发；现由 `tests/unit/agent_relay/test_job_status.py`（fakeredis）与 `test_job_store_sqlite.py` 覆盖相关状态语义 |
| 单元 | `services/tests/unit/gateway/test_push_subscriber.py`（扩） | kind=job_status 帧结构（type/payload.jobs 五字段白名单/seq 占位）；kind 缺省仍走 ai_text |
| 单元 | `services/tests/unit/agent_relay/test_models.py`（扩） | JobStatusPushMessage / JobStatusItem 序列化 round-trip |
| 集成 | ~~`services/tests/integration/agent_relay/test_job_status_integration.py`~~（已随去 Redis 移除） | 原全链路（快照/清除/首报/周期/重启补发）由 `tests/unit/agent_relay/test_job_status.py` 的判定表与 fake-clock 用例覆盖 |
| e2e | `services/tests/e2e-backend/scenarios/`（后续） | 经 mock hermes 的端到端 job_status 收帧（可随 client 阶段一起做） |
| 协议 | ajv 校验（CI 既有命令：`npx ajv compile -s packages/api-schema/message.json -r 'packages/api-schema/**/*.json' --spec=draft2020`）+ 生成器 `--check`（python + kotlin） | 新 schema compile + examples 自洽；DTO 一致性 |

---

## 8. 实施步骤（建议拆分）

1. **协议**：`s2c/job-status.json` + 两个 `message.json` 登记 + `ws-protocol-design.md`
   小节 + api-schema README Structure 节补登；重新生成 Kotlin（`make generate-android-api`）
   与 Python（`scripts/generate_python_api.py`）DTO；
2. **存储**：`JobRecord.started_at` + `from_mapping` 转换清单 + `mark_running` 写入 +
   `set_change_listener` 三钩子；
3. **通知器**：`job_status.py` + models/publisher/push_subscriber 扩展 + config + metrics；
4. **接线**：session_manager 首报挂点 + application.py 装配与 shutdown；
5. **测试**：按 §7 补齐；
6. **client SDK/App**：`android_agent_job_status.md`——job_status 消息消费、
   UI 展示、未知 type 容错确认。

改动清单汇总：

| # | 文件 | 改动 |
|---|------|------|
| 1 | `packages/api-schema/s2c/job-status.json` | 新增（§4.1） |
| 2 | `packages/api-schema/s2c/message.json`、`packages/api-schema/message.json` | union 注册 job_status |
| 3 | `docs/ws-protocol-design.md`、`packages/api-schema/README.md` | 协议小节 + Structure 补登 |
| 4 | `packages/sdk-python` / `packages/sdk-kotlin` generated | 重新生成 DTO（CI `--check` 跟随） |
| 5 | `services/agent_relay/job_store.py` | `JobRecord.started_at` + `from_mapping` 清单 + `set_change_listener` + 三处钩子调用 |
| 6 | `services/agent_relay/models.py` | `JobStatusItem` / `JobStatusPushMessage` |
| 7 | `services/agent_relay/publisher.py` | `publish` 签名放宽为 union |
| 8 | `services/agent_relay/job_status.py` | 新增 `JobStatusNotifier`（§4.3） |
| 9 | `services/gateway/push_subscriber.py` | kind 分支 + `_build_job_status_frame` |
| 10 | `services/agent_relay/session_manager.py` | 注入 notifier + `on_session_started` 挂点 |
| 11 | `services/agent_relay/config.py` | `job_status_enabled` / `job_status_interval_s` / `job_status_scan_interval_s` |
| 12 | `services/agent_relay/metrics.py` + `services/shared/observability/metrics.py` | `inc_job_status` + counter |
| 13 | `services/app/application.py` | 装配 + shutdown + enabled 开关 |
| 14 | `configs/dev/server.yaml` | 示例配置补充 |

---

## 9. 开放问题

| # | 问题 | 倾向 |
|---|------|------|
| O1 | 15s 周期推送期间，client 是否需要每帧 `running_time_ms` 还是凭 `started_at_ms` + 时钟偏移本地续算即可（即可否降频/停发周期帧）？ | 按需求原文保留 15s 双字段推送；client 阶段若确认本地续算可靠，可用 `job_status_interval_s` 调大或关闭周期 |
| O2 | 长跑 job 是否需要多级节流（运行超过 N 分钟后降低保活频率）？ | 本期固定 15s 可配；后续迭代可加多级周期 |
| O3 | 高频变化（同 device 大量 job 密集启停）是否加 `min_change_interval_s`？ | 本期不加：per-device ≤10 jobs 且 per-device 锁串行化，帧率有界；实测过高再加 |
| O4 | 未来多 adapter 各自拥有 job 源时，notifier 的 agent_name 如何从 JobRecord 携带？ | 本期 hermes 专属 JobStore + 参数注入；多源时给 JobRecord 加 `agent` 字段（如同 `started_at` 的加法演进） |
