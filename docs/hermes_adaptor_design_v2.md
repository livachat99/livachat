# Hermes Adapter 交互设计 v2 — Proactive 监听模式（合并终稿）

> 状态：**已被 [`hermes_adaptor_design_v3.md`](hermes_adaptor_design_v3.md) 取代**——`prompt.background` job 载体已从代码中删除，本文仅保留为历史设计记录（device session / watcher / autorespond 等部分仍有效）。
>
> 原状态：Implementable（P1 主链路已实施，P0 实测结论已回填 §3.2/§9）
> 前置文档：[`hermes_adaptor_design.md`](hermes_adaptor_design.md)（v1，WS JSON-RPC 对接）、
> [`hermes_websocket_api.md`](hermes_websocket_api.md)（Hermes 协议调研，v0.20.0 实测复核）、
> [`fd_agent_relay_service.md`](fd_agent_relay_service.md)（agent-relay 总体设计）、
> [`data/lvdata_mcp_service.md`](data/lvdata_mcp_service.md)（lvdata MCP 服务）
> 合并来源：`hermes_adaptor_design_v2-k3.md`（主干）、`hermes_adaptor_design_v2-d4.md`（组件并入）
> 日期：2026-08-09

---

## 0. 合并说明（决策记录）

本稿由 k3 / d4 两份草案评审合并而成。以 **k3 稿为架构主干**，并入 d4 稿的 6 个组件，
否决 d4 稿的 5 个方案。决策依据见各章节，此处仅留结论：

| 决策点 | 采纳 | 否决 | 理由（详见） |
|--------|------|------|--------------|
| WS 连接模型 | 单条长连 + 常驻 reader 按 session 分发（k3） | d4 每会话一条连接 | 协议文档 §8.2 明确推荐单连多路复用；per-session 锁已解决 v1 全局锁问题（§4.1） |
| job 派生机制 | relay 两阶段派生：主 agent 决策 → **relay** 调 `prompt.background`（k3 修正版） | d4 模型自行委派子代理 | 模型无法调 RPC；relay 派生使 task_id 直达 relay，消除 id 映射难题（§4.3） |
| 完成检测 | `background.complete` 事件为主 + JobStore 看门狗超时收敛（k3 为主） | d4 `[STATUS]` 轮询为主 | `prompt.submit` 在 turn 进行中会排队并默认打断（协议 §3.4），轮询有打断/4009 风险且每轮烧一个 LLM turn（§4.4） |
| 状态持久化 | **JobStore-lite（d4，裁剪版）** | k3 纯内存 outstanding_jobs | relay 重启对账、看门狗收敛需要持久状态（§4.4 / §5.4） |
| mission 注入 | **`session.create(messages=...)` 种子**（d4） | k3 首个 prompt.submit | 省一个 turn，语义干净；以 P0 实测为前提，有 fallback（§4.3） |
| 阻塞交互兜底 | approval + **clarify/sudo/secret 全覆盖**（d4） | k3 仅 approval（v1 parity） | 协议 §3.6 四类均阻塞 turn，无人值守必须全兜底（§4.5） |
| job 标识 | **relay 幂等键 + hermes task_id 双写**（d4 简化版） | d4 要求模型传播 job_id | 两阶段派生后模型无需感知 id（§4.4） |
| 资源治理 | 硬上限：per-session / 全局并发 + job 超时（d4） | k3 仅靠模型看 outstanding_jobs 自律 | 不能靠 prompt 约束防失控（§4.4） |
| base.py 接口 | k3 通用 `nudge()` 语义 | d4 `dispatch/query_status/cancel` | 后者把 Hermes job 模型泄漏进通用接口，openclaw 无法适配（§5.1） |
| push 协议 | **零改动**（k3） | d4 `PushMessage` 加 `job_id/turn_ref` | gateway `push_subscriber.py` 白名单只透传 `source/agent_name/reply_to/proactive`，加字段是 relay+gateway+客户端三方变更（§4.7） |
| 记忆通道 | 仅 Hermes 侧注册 lvdata MCP（k3） | d4 通道 B（relay 内置 lvdata client 检索注入） | 热路径加延迟、复制 Hermes 原生能力，YAGNI（§3.3） |
| 触发粒度 | k3 增量行 nudge + 攒批（增补触发规则） | d4 ref_id 轮次配对 + 30s debounce | 配对逻辑无谓复杂，30s 对主动帮助太慢（§5.3） |

---

## 1. 背景与问题

### 1.1 v1 交互模式回顾

现有 `HermesAdapter`（`services/agent_relay/adapters/hermes.py`）只实现了 **Reactive** 路径：

```
每条 ASR final → user.utterance 事件 → AgentSessionManager.on_user_utterance
  → adapter.ask(text, ctx)：
      session.create → prompt.submit(当前 utterance) → 等 message.complete → session.close
  → 聚合全文 → PushPublisher → ai_text (source=agent)
```

Proactive 路径（`analyze_recording()`）在 v1 是 **no-op**（继承 base 空实现）。

### 1.2 问题分析

Hermes 是通用 Agent 平台，一个 turn 通常伴随工具调用（shell / web search / 文件读写），
耗时 **数十秒到分钟级**。v1 模式在真实环境暴露的问题：

| # | 问题 | 根因 / 后果 |
|---|------|-------------|
| 1 | 大量 turn 被丢弃 | `AgentRelayConfig.adapter_call_timeout_s` 默认 120s，Hermes turn 经常超时 → `inc_agent_reply(name, "timeout")`，用户什么都收不到 |
| 2 | 实时问答 UX 差 | 即使不超时，用户在语音对话里等几分钟才收到"第二答"，已失去对话上下文 |
| 3 | 全局串行 | `ask()` 持单连接全局 `asyncio.Lock` 同步等待整个 turn，多会话互相阻塞 |
| 4 | Hermes 侧无上下文 | v1 取舍：`session.create` 不 seed history，每轮 create/close，无状态一问一答 |
| 5 | 阻塞类交互悬挂 | v1 只处理 `approval.request` 自动 deny；`clarify.request` / `sudo.request` / `secret.request`（协议 §3.6，均阻塞等应答）未应答 → turn 永久阻塞 |
| 6 | 成本高 | 每条 ASR final 都起一个独立 Hermes session + turn，utterance 越碎调用越频 |
| 7 | Proactive 缺失 | 真正适合慢 Agent 的场景（听后分析、主动建议、长任务）没有落地 |
| 8 | 无长期记忆 | Hermes 对用户的了解仅来自当轮 prompt |

### 1.3 结论

**Reactive 模式保留但降级为可选；重点建设 Proactive 模式。**

核心思路转变：不再把 Hermes 当"第二个实时 LLM"逐句喂 ASR 文本，而是把 Hermes 当作
**旁听 LivaChat 对话的慢速 Agent**——session 创建后交给它一个持续监听的上下文（chatlog 文件），
由它自己决定何时介入；**长任务的派生动作由 relay 执行**（两阶段，§4.3），耗时任务一律后台化。

---

## 2. 设计目标与非目标

### 目标

1. **Proactive 优先**：session 创建后不再逐条发送 ASR 给 Hermes；改为告知 Hermes 实时监控
   该 session 的 chatlog 文件，基于"用户 ↔ 实时 LLM"的问答内容，自行决定给出答案或行动。
2. **长期记忆**：需要用户长期记忆时，通过 **lvdata MCP server** 查询历史会话数据，
   给出更贴合用户的结果。
3. **Hermes 主 Agent 轻量契约**：主 agent 收到 prompt 后只做两件事——
   - (a) 判断是否需要生成新的 job/task，需要则**请求 relay 派生** background 任务；
   - (b) 判断是否是 job/task **status 查询**，是则返回相关后台任务的最新状态。
   - **任何长时间 job/task 都不在主 agent 内执行**，保证 adapter 与主 agent 的每轮交互都能在秒级完成。
4. **job 生命周期 relay 可见**：JobStore 持久化 job 状态，支持重启对账与超时收敛。
5. **Reactive 保留**：v1 的 `ask()` 能力保留（配置开关），不作为默认路径。

### 非目标（本期不做）

- Hermes 回复的 TTS 播报（仍走 `ai_text` 文本帧；proactive 帧与进行中播报的客户端协调见 §9 Q8）；
- 多 Hermes 实例负载分担；
- Hermes → LivaChat 的主动发起会话（agent 发起新对话）；
- 音频分段分析（`recording.ready` / `analyze_recording` 真实现）留待 v2.1 单独设计。

---

## 3. 调研结论（可行性支撑）

### 3.1 Chatlog 已存在且可持续监听

Recording 服务（`services/recording/`）已经落地了对话文本持久化：

- **路径**：`{recording.base_dir}/{device_uuid}/{recording_session_id}/chat/history.jsonl`
  （`base_dir` 默认 `/opt/lvdata`，见 `services/shared/config/models.py`）
- **格式**（`ChatLogWriter`，`services/recording/chatlog.py`）：append-only JSONL，每行：
  ```json
  {"ts": 1754..., "utc": "2026-08-08T02:03:04.123Z", "role": "user|assistant",
   "text": "...", "ref_id": "tr-xxx", "chat_session_id": "cs-xxx"}
  ```
  `role=user` 来自 ASR final，`role=assistant` 来自 LLM 一轮聚合全文（被打断的带 `interrupted:true`）。
  `chat_session_id` 可能为空字符串（`chatlog.py` 默认值），监听方按不等即跳过处理（§5.3）。
- **flush 策略**：每 20 行或 5s flush 一次——文件对旁听者是"准实时"的（最坏延迟 ~5s）。
- **recording session 粒度**：per-device per-UTC-day；一个 chat session（一通对话）是多行
  `chat_session_id` 相同的记录；同一天多通对话共享同一文件，**靠 `chat_session_id` 字段区分**。
- **路径解析**：`RecordingService` 在 Redis 维护
  `livachat:recording:device:{device_uuid}:current` → `{session_id, session_date}`（TTL 2 天，
  见 `services/recording/service.py` `_redis_key`）。relay 凭 `device_id` 即可确定当天 chatlog 路径，
  **无需改动主 pipeline**。

> 注意：需求描述中的 "chatlog.json" 实际产物是 `chat/history.jsonl`（JSONL 流式追加，
> 天然适合 tail 监听），本设计直接复用，不新增文件。

### 3.2 Hermes 协议能力满足"主 agent 轻量 + 后台执行"

基于 `docs/hermes_websocket_api.md`（v0.20.0 实测复核）：

| 能力 | 协议支撑 | 对本设计的意义 |
|------|---------|---------------|
| 后台 turn | `prompt.background` RPC：后台跑一个独立 turn，完成时推 **`background.complete {task_id, text}`** 事件 | relay 派生长任务的官方通道；完成事件被常驻 reader 异步接收并推给用户 |
| 会话持久化 | `session.create(close_on_disconnect=false, messages=[...])` + `stored_session_id` + `session.resume`；断连有 ~20s 孤儿回收宽限 | relay↔Hermes 长会话抗 WS 重连；`messages` 种子用于注入 mission 契约（§4.3） |
| 长连接事件分发 | 事件按 `session_id` 投递到该 session 最近活跃的 WS 连接 | relay 单条 `/api/ws` 长连即可收全部事件（含 background.complete） |
| MCP 客户端 | REST `GET /api/mcp/servers`；Hermes 支持配置外部 MCP server | **lvdata-mcp 可作为 Hermes 的 MCP 工具源注入**（§3.3） |
| 审批/交互响应 | `approval.respond` / `clarify.respond` / `sudo.respond` / `secret.respond` | 无人值守兜底矩阵（§4.5） |
| 任务管理（可选） | `kanban list/show/create/...`、`delegation.*`、`spawn_tree.*` | 本期不依赖；留作 job 可视化/人工干预的运维入口 |

**两条必须尊重的协议行为**（直接影响设计，协议文档 §3.4/§3.7 实测）：

1. `prompt.submit` 在 turn 进行中重复提交会**排队（并默认打断当前 turn）**，不拒绝。
   → relay 侧必须 per-session 串行化提交（per-session lock + nudge 攒批），
   这也是否决"[STATUS] 轮询为主路径"的原因——轮询会打断进行中的决策 turn。
2. 错误码 `4009 session busy / subagent still running`、`4090 活跃会话数达上限`。
   → background 运行中主 session 是否 busy 未实测（§9 Q2），设计不依赖在此期间提交 prompt。

已知限制：`prompt.background` 的参数细节（任务描述字段、是否继承会话上下文、background
turn 内审批事件的投递通道、`background.complete.text` 是否即最终全文）协议文档未展开。
**P0 实测结论（2026-08-09，`tests/manual/p0_hermes_probe.py` + `tui_gateway` 源码，
全部已验证）**：

| 项目 | 实测/源码结论 |
|------|--------------|
| `prompt.background` 参数 | `{session_id, text}`；`text` 必填（缺省 4012）；响应 `{task_id: "bg_<hex>"}` |
| 上下文继承 | **不继承**父会话历史：background 是全新 `AIAgent` 实例，仅复制父 agent 的模型/工具链/reasoning 配置 → 任务说明必须内联全部所需上下文（relay 模板已按此设计） |
| `background.complete` 事件 | `params.type=background.complete, params.session_id=父会话, payload={task_id, text}`；`text` 即 `final_response` **最终全文**；任务抛异常时 `text` 以 `error: ` 开头（relay 已识别丢弃不回推） |
| 事后可查性（Q1e） | **不可查询**：任务跑在 `threading.Thread`（内存），无 kanban/DB 记录 → 看门狗"先查后判"不可行，维持"先标 failed，完成事件到达时丢弃" |
| turn 进行中 submit（Q2） | **不报 4009**：`session["running"]` 时排队并默认打断当前 turn，响应 `{"status": "redirected"}`；`4009 subagent still running` 仅出现在 lazy 子代理场景 → per-session 串行化（lock + 攒批）必须 |
| 被 redirect 的 turn | **仍会补发 `message.complete`**（P0 实测 Q2 首个 turn 被打断后 complete 到达）→ 每个 turn 恰好一个 complete 是 straggler 防御的前提；relay 对本地超时的 turn 打 straggler 标记，丢弃紧随的下一个 complete（§7） |
| `session.create messages` 种子（Q3） | **实测生效**：`system` role 种子被模型严格遵循（无需 fallback）；`stored_session_id` 正常返回；strict OpenAI 兼容 provider 要求 system 消息在消息列表开头（种子是首条，满足） |
| 阻塞交互通道（Q4） | `approval.respond` / `clarify.respond` 方法存在；无 pending 请求时 `clarify.respond` 返回 4009（正常语义）；**background turn 内交互事件不投递到主 WS 连接**（无 transport 绑定）→ relay §4.5 兜底只覆盖主 turn，Hermes 侧 toolsets 收窄是 background 的第一道防线 |

### 3.3 lvdata-mcp 提供长期记忆查询

`tools/lvdata-mcp/` 已是常驻 MCP 服务（Streamable HTTP，默认 `127.0.0.1:8090/mcp`），
其 ingestion 平面 60s 轮询的 **`/opt/lvdata` 与 recording 的 `base_dir` 是同一数据根**——
LivaChat 写盘的历史会话会被自动分析、索引。

对 Hermes 可用的 **5 个只读 tools**（2026-09-27 工具集收敛，见
[docs/data/lvdata_mcp_tools.md](./data/lvdata_mcp_tools.md)；运维件 status/trigger 已迁 REST、
不再走 MCP；`get_key_image` 已并入 `get_event`——video 事件详情自带关键帧图片）：
`search`(FTS5) / `semantic_search`(向量 RRF 融合) / `get_timeline` / `get_event` /
`get_recent_events`(近 N 个本地日清单，days=1 今天)。

lvdata-mcp README 给出标准 MCP 客户端接入模板（`mcpServers.lvdata` type=http + Bearer token）。
即本设计不需要 relay 写任何查询代码——**在 Hermes 侧注册 MCP server，由 background agent
按需调用**，典型路径：

```
background job: search/semantic_search(用户历史偏好) → get_recent_events/get_timeline(细节)
  → 生成个性化结论 → background.complete → relay → 用户
```

> 否决项记录（d4 通道 B）：relay 内置 lvdata client 做检索注入，会在 nudge 热路径上增加
> 检索延迟与缓存复杂度，且复制 Hermes 原生 MCP 能力。本期不做；若 Hermes 侧 MCP 配置
> 长期无法落地，再单独评审引入。

### 3.4 Relay 侧已有基础设施可直接复用

- 事件总线（`livachat:agent:events`）已有 `session.started / session.ended / user.utterance /
  recording.ready` 四类事件（orchestrator 已埋点，`services/session/agent_publisher.py`）。
- 推送链路 `PushPublisher → livachat:push:{instance_id} → PushSubscriber → ai_text` 已支持
  `proactive=true`、`agent_name`、`reply_to` 字段（`services/agent_relay/models.py`），
  **push 协议零改动**（gateway `push_subscriber.py` 白名单透传上述四字段，新增字段需
  relay+gateway+客户端三方变更，本期明确不做）。
- `AgentSessionManager` 已维护 per-session 状态（device_id、language、timezone），
  只需增加 adapter 生命周期回调与 chatlog 监听触发。

---

## 4. 总体设计

### 4.1 架构与数据流

```
                        LivaChat Server 进程
 ┌────────────────────────────────────────────────────────────────┐
 │ orchestrator ──► AgentEventPublisher ──► Redis agent:events ─┐ │
 │ recording ──► chat/history.jsonl (/opt/lvdata/<dev>/<rec>/)  │ │
 │                                                              ▼ │
 │              AgentSessionManager（+ 生命周期回调 + ChatlogWatcher）│
 │                        │ on_session_start / nudge / on_end     │ │
 │                        ▼                                       │ │
 │                  HermesAdapter (v2) ──────► JobStore (Redis)   │ │
 └────────────────────────┼───────────────────────────────────────┘
                          │ 单条 WS /api/ws（JSON-RPC 2.0，常驻 reader）
                          ▼
              Hermes (hermes serve / dashboard)
                ├─ 主 Agent session（每 LivaChat session 一个，轻量）
                │     · mission 契约种子（session.create messages）
                │     · 收 NUDGE prompt，只回契约 JSON：noop / job_request / message
                ├─ background jobs（relay 调 prompt.background 派生，分钟级）
                │     · 读 chatlog 全文 / 调 lvdata MCP / web / 工具
                │     · 完成 → background.complete 事件
                └─ MCP client ──► lvdata-mcp (127.0.0.1:8090/mcp)
                                      └─ /opt/lvdata 长期记忆索引

 回推：background.complete / 主 agent message → adapter → PushPublisher
       → livachat:push:{instance} → gateway → ai_text(source=agent, proactive=true)
```

### 4.2 会话生命周期映射

| LivaChat 侧 | Hermes 侧 | 说明 |
|---|---|---|
| `session.started` | `session.create(close_on_disconnect=false, source="livachat-relay", title=<sid>, messages=[mission 种子])` | 建立 1:1 映射；mission 含 chatlog 路径、chat_session_id、契约说明（§4.3） |
| 对话进行中（chatlog 出现新 user 行） | 提交 **NUDGE prompt**（合并去抖，含增量行） | 主 agent 秒级决策：noop / job_request / message |
| 主 agent 回 `job_request` | **relay 调 `prompt.background`** 派生 | task_id 直达 relay，写 JobStore（§4.4） |
| `session.ended` | `session.close`（可配置先提交 WRAP_UP 再关） | 释放 Hermes 侧资源；running job 标记 orphaned 不再推送（§4.4） |

- relay 内存维护 `livachat_session_id → {hermes_session_id, stored_session_id}`，
  并写 Redis（`livachat:agent:hermes:session:{sid}`，TTL 2 天）供重启后 `session.resume` 恢复。
- WS 断连：`_ensure_connected()` 重连后对进行中的 Hermes session 做 `session.resume`
  （`close_on_disconnect=false` + 20s 孤儿宽限内可恢复）。

### 4.3 Hermes 主 Agent 契约（Prompt Contract）

**注入方式**：mission 契约作为 `session.create` 的 `messages` 种子下发
（`[{role:"system", content:<mission 模板渲染>}]`，模板文件
`services/agent_relay/prompts/hermes_monitor.md`，新增）。system role 种子已由 P0 实测
**确认生效**（§9 Q3，无需 fallback）；fallback（首个 `prompt.submit` 下发 MONITOR
并等 noop 应答）仅作为配置兜底保留。

Mission 内容要点：

0. **本设备时区**（2026-09-28 增，`docs/agent_relay_prompts.md`）：mission 内新增
   `# 本设备时区` 小节——渲染值 = 设备上报时区（`session.start` 的 canonical 值，经
   `session.started` 事件 → `AgentContext.timezone` 透传）；未上报则显式写"未上报时区…按服务端
   配置时区处理"。后台任务书（`background_task.md`）与 nudge 载荷同样带该值。
1. **角色**：你是 LivaChat 语音对话的旁听 Agent。对话文本实时追加在
   `{chatlog_path}`（JSONL，每行 `{ts, role: user|assistant, text, chat_session_id}`），
   你只关心 `chat_session_id == "{sid}"` 的行。LivaChat(实时语音助手)已在直接回答用户(会朗读)，
   **你的价值是补充它做不到的**：需要工具/检索/长思考/主动行动的事。
2. **只允许三种响应**（收到任何 prompt 后，输出**唯一 JSON 对象**，无额外文本）：
   - 无需介入 → `{"action":"noop"}`（绝大多数 nudge 的应答，占绝对多数）；
   - 需要超过 ~10s 的工作（查资料、调 lvdata MCP、多步工具）→
     `{"action":"job_request","summary":"给用户的一句话","spec":{"task":"任务说明，含目标/输入/输出要求"}}`。
     **你只做决策，派生动作由 relay 执行**（relay 收到 job_request 后调
     `prompt.background`）；不要试图自己创建后台任务；
   - 有**秒级能给出**的补充 → `{"action":"message","text":"..."}`（应稀少，主 agent 不做重活）。
   - （可选，v2 默认关闭）prompt 是状态查询 → `{"action":"status","jobs":[...],"text":"..."}`。
3. **禁止**：在主 turn 内执行长任务、执行高危工具、向用户输出契约 JSON 以外的寒暄、
   编造 task_id / job_id。
4. **长期记忆**：需要用户历史偏好/过往会话事实时，写进 `spec.task` 让 background job
   调用 `lvdata` MCP tools（`search` / `semantic_search` / `get_recent_events` /
   `get_timeline` / `get_event`）；不要在主 turn 内调用（索引查询虽快，但会拉长主 turn）。
   （**2026-09-28**：`get_session_summary` 为 v1 残留、早已删除；本行与两份实际 prompt 同步更新。）
5. **上下文自服务**：NUDGE 只带增量行；需要更早上下文时自己读 chatlog 文件
   （路径已知，按 `chat_session_id` 过滤）。
6. **Agent 名字与点名契约**（2026-09-30 增，`docs/prompts_improvements.md`）：mission 内新增
   `# 你的名字` 小节（渲染值 = adapter 配置 `nickname`，空则整段不出现）——用户用该名字呼唤
   即**直接指令**（必须响应：工具/检索/历史记忆 → `job_request`，秒级 → `message`；不得凭印象
   直接答）；同时点明 LivaChat 的分工与"它建议「交给 X」只是措辞、不改变判定标准、不得重复
   派生"。后台任务书（`background_task.md`）带同款短版名字行。LivaChat 侧另有对称声明
   （`docs/prompts_improvements.md` §3.5，装配期追加 dialog/omni system prompt）。
   **2026-10-18**：多 adapter 时 LivaChat 声明按实际装载的 proactive 集合生成
   （`docs/agent_relay_prompts.md` §8）；monitor/任务书不注入同侪信息（Q4=B）。
7. **Web 页面能力**（2026-10-01 增，`docs/webroot_feature.md`）：mission 内新增
   `# Web 页面(复杂 / 可交互内容)` 小节（渲染值 = webroot 静态目录，未启用则整段不出现）——
   用户明确要求"网页方式呈现"或需求超出文字/图片承载（图表/看板/可交互工具）时，在
   `spec.task` 里明确要求"产出一个 Web 页面"。后台任务书（`background_task.md`）带完整执行
   规范（独立 uuidv7 目录 / `index.html` + 相对路径 / 自包含静态资源 / `playwright-dev` MCP
   （`--ignore-https-errors`）经 server 访问验收门禁 / 结果链接 `livachat_url://webroot/<uuid>/` 放开头前几行）。

**NUDGE prompt**（relay 拼装，`prompt.submit` 下发）：

```json
{
  "type": "chatlog_update",
  "chatlog_path": "/opt/lvdata/dev-xxx/rec-yyy/chat/history.jsonl",
  "chat_session_id": "cs-zzz",
  "new_lines": [
    {"role": "user", "text": "下周去东京出差，帮我看看机票"},
    {"role": "assistant", "text": "好的，几号出发？..."}
  ],
  "truncated": false,
  "outstanding_jobs": [{"job_key":"jk-1","task_id":"task-abc","summary":"在查东京机票"}]
}
```

- `truncated=true` 表示增量超出 `nudge_max_lines/nudge_max_bytes`，提示主 agent 自行读文件；
- `outstanding_jobs` 来自 JobStore（§5.4），让主 agent 感知在途负载、避免重复派生。

**契约解析规则**（relay 侧，防泄漏兜底）：

1. 对 `message.complete` 的 text：trim → 若以 ```` ``` ```` 开头则去 code fence →
   从首个 `{` 起做括号配平，提取**首个完整 JSON 对象**；
2. 解析失败、无 `action` 字段、或 `action` 未知 → 一律按 noop 处理 +
   `contract_parse_error` metric + debug 日志（**契约文本/模型废话绝不回推给用户**）；
3. `message` 缺 text 或空 text → 按 noop 处理。

### 4.4 后台 Job 生命周期与回推

**两阶段派生（关键修正：模型只决策，relay 执行）**：

```
主 agent 回 {"action":"job_request", spec, summary}
  → relay 校验：spec 非空；per-session 在途 < max_concurrent_jobs_per_session；
    全局在途 < max_in_flight_jobs（超限按 noop 处理 + job_rejected metric）
  → JobStore 写入 {job_key(relay 生成 uuid), status: requested, summary}
  → relay 调 prompt.background(session_id, 任务说明 = spec 渲染 + chatlog 路径
    + lvdata MCP 使用指引 + webroot 提示（启用时）+ 输出要求)
  → RPC 成功 → JobStore {status: running, hermes_task_id}
  → Hermes 后台执行（分钟级，可读文件全文/调 MCP/用工具）
  → WS 事件 background.complete {task_id, text}
  → 常驻 reader 收到 → 按 task_id 查 JobStore → 标记 done
  → PushCallback → AgentSessionManager
  → PushMessage(device_id, session_id, text, proactive=true, agent_name="hermes")
  → PushPublisher → gateway → ai_text
```

- **输出要求**（写进 background 任务说明）：job 的最终 text 就是发给用户的成品文案
  （口语、简洁、无需解释过程）；若结论是不必打扰用户，输出字面量 `NOOP`，adapter 丢弃
  （`job_noop_output` metric）。
- **无 relay 侧调用超时**：后台 job 是事件驱动的，`adapter_call_timeout_s` 不适用；
  只给主 agent 的轻量 turn 设 `nudge_timeout_s`（默认 60s）。
- **看门狗（替代 d4 的 STATUS 轮询）**：每 `watchdog_interval_s`（默认 60s）扫描 JobStore：
  - `running` 超过 `background_timeout_s`（默认 1800s）→ 标 `failed(timeout)` + metric；
  - `requested` 超过 `job_requested_timeout_s`（默认 60s）未转 running
    （relay 在 RPC 前后崩溃的残骸）→ 标 `failed(orphaned)`。
  - 不主动向主 agent 发 STATUS（两阶段派生后主 agent 不感知 task；且轮询有
    打断/4009 风险，§3.2）。P0 实测（§9 Q1e）确认 background task **不可**经
    REST/kanban 事后查询（threading 内存任务）→ 看门狗不加"先查后判"，
    维持超时收敛；被误杀的 job 完成事件到达时由 adapter 按非 running 状态丢弃。
- **session 结束**：该 session 的 running job 标 `orphaned`，完成事件到达时丢弃不推送
  （session.close 后 Hermes 侧任务可能仍在跑，v2 不做 cancel；§9 Q9）。
- **离线丢弃**：device 不在线时 `PushPublisher.publish` 返回 False，v2 记 metric
  （`dropped_offline`），离线暂存重投列为开放问题（§9 Q7）。
- **relay 重启对账**：JobStore 持久化于 Redis。重启后 running job 继续等事件
  （停机期间的 `background.complete` 已丢失——事件只投递到连接，无滞留），
  最终由看门狗超时收敛；不主动重投。

**JobStore 状态机**：

```
requested ──(prompt.background 成功)──► running ──(background.complete)──► done
    │                                     │
    └─(60s 未转 running)─► failed         └─(超 background_timeout_s)─► failed
        (orphaned)                            (timeout)      └─(session 结束)─► orphaned
```

### 4.5 阻塞类交互兜底矩阵（无人值守）

协议 §3.6 中四类事件均**阻塞 turn 等应答**，v1 只处理了 approval（且漏传
`session_id` 参数，`hermes.py` v1 `_event_loop`，v2 一并修正）：

| 事件 | relay 应答 | 说明 |
|------|-----------|------|
| `approval.request` | `approval.respond {session_id, choice:"deny", all:true}` | v1 语义保留，补 `session_id` |
| `clarify.request` | `clarify.respond {session_id, answer:"(无人值守) 无需澄清，按你的最佳判断继续，不要再询问"}` | 防悬挂；answer 文案进 mission 模板统一管理 |
| `sudo.request` | 不持有凭据：记 security 告警 metric + `session.interrupt` 打断当前 turn | 协议无 deny 语义，interrupt 是唯一的防悬挂手段 |
| `secret.request` | 同 `sudo.request` | 同上 |

background turn 内这些事件的投递通道未知（§9 Q1c）；Hermes 侧必须另做 toolsets 收窄
与审批降级（§6.2），relay 兜底是第二道防线。

### 4.6 Reactive 模式保留

- `HermesAdapterConfig.mode: "reactive" | "proactive" | "both"`，默认 **`proactive`**。
- `reactive` 时行为与 v1 完全一致（`ask()` 逐 utterance 调 Hermes），仅享受 v2 的
  基础设施红利（常驻 reader、per-session 锁、兜底矩阵）。
- `AgentSessionManager.on_user_utterance` 按 adapter 的 `reactive_enabled` 过滤 fan-out：
  hermes(proactive) 不再收到逐条 ASR——满足"不再发送 ASR 给 hermes"。
- （可选，P3）reactive turn 超过 `reactive_max_wait_s`（默认 30s）未完成时转为
  job_request 语义后台化——复用同一 job 通道，避免同步等死。来自 d4 稿，本期可选。

### 4.7 推送语义与客户端约定

- 复用现有 `PushMessage` 全部字段，**wire 协议零改动**：`proactive=true` 标识主动推送，
  `agent_name="hermes"`，`reply_to` 不填（不归属特定 utterance）。
- 不新增 `job_id/turn_ref` 等字段：gateway `push_subscriber.py` 按白名单构造 ai_text
  帧（仅透传 `source/agent_name/reply_to/proactive`），新字段会被静默丢弃，且客户端
  wire 协议需同步扩展——收益不抵成本。客户端如需关联任务，v2.1 再议。
- relay 保证单 session 的 proactive 帧按完成顺序到达；**到达时机与进行中主 LLM
  播报/TTS 的协调（打断/排队/仅文本展示）是客户端产品决策**，见 §9 Q8。

---

## 5. Relay 侧改动

### 5.1 `adapters/base.py` — 接口扩展（向后兼容）

```python
@dataclass
class ChatlogLine:  role: str; text: str; ts_ms: int
@dataclass
class ChatlogUpdate:
    path: str; chat_session_id: str
    new_lines: list[ChatlogLine]; truncated: bool
    outstanding_jobs: list[dict]   # 来自 JobStore：{job_key, task_id, summary, agent_name}

class AgentAdapter(ABC):
    reactive_enabled: bool = True      # 实例属性，来自 config.mode
    proactive_enabled: bool = False

    async def on_session_start(self, context: AgentContext) -> None: ...      # 默认 no-op
    async def on_session_end(self, context: AgentContext) -> None: ...        # 默认 no-op
    async def nudge(self, update: ChatlogUpdate, context: AgentContext
                    ) -> AsyncIterator[AgentResponseChunk]:                   # 默认 no-op（空生成器）
        return; yield
    def set_push_callback(self, cb: Callable[[str, AgentResponseChunk], Awaitable[None]]
                          ) -> None: ...   # 供 background.complete 等带外事件回推
```

`ask()` / `analyze_recording()` 签名不变；openclaw 等现有 adapter 零改动。
接口保持"chatlog 增量通知"通用语义，不出现 dispatch/job 等 Hermes 专有概念。

### 5.2 `session_manager.py` — 路由调整

- `on_session_started`：对 `proactive_enabled` 的 adapter `create_task(adapter.on_session_start(ctx))`；
  并为该 session 启动 **ChatlogWatcher**（§5.3）。
- `on_user_utterance`：仅对 `reactive_enabled` 的 adapter 调 `ask()`（其余逻辑不变）。
- `on_session_ended`：停 watcher，`create_task(adapter.on_session_end(ctx))`，
  JobStore 该 session 的 running job 标 `orphaned`，清理 Redis 会话映射。
- `__init__`：对每个 adapter 调 `set_push_callback(self._on_adapter_push)`；
  `_on_adapter_push(session_id, chunk)` 查 session → 组 `PushMessage(proactive=True)` →
  `PushPublisher.publish`（复用现有 `_call_adapter` 尾部推送逻辑，抽公共方法）。

### 5.3 新增 `chatlog_watcher.py` — ChatlogWatcher

职责：监听活跃 session 的 chatlog 增长，按触发规则合并去抖后调用 `adapter.nudge()`。

- **路径解析**：`device_id` → Redis `livachat:recording:device:{device_id}:current`
  → `{base_dir}/{device_id}/{recording_session_id}/chat/history.jsonl`
  （`base_dir` 由 `application.py` 从 `cfg.recording.base_dir` 注入 wiring）。解析失败
  （recording 未启用 / key 缺失）→ 该 session 不启用 proactive，记 warning +
  `chatlog_resolve_failed` metric。
- **offset 策略（从简）**：watcher 启动时定位到 **EOF**——proactive 语义是"从现在起监听"，
  不回放历史（主 agent 需要历史可自行读文件，路径已知）。offset 只存内存，不落 Redis；
  relay 重启后 watcher 重建，同样从 EOF 开始。
- **轮询**：每 `chatlog_poll_interval_s`（默认 3s）`stat` 文件；size 变化则从 offset
  读新增字节，按行解析（末尾不完整行留到下次），按 `chat_session_id` 过滤——
  **不等（含空值）一律跳过**。
- **触发规则（成本治理，关键）**：
  - 新增行中含 ≥1 条 `role=user` → 触发（或进入攒批）；
  - 新增行**仅** `role=assistant` → **不触发**；若已有 pending 攒批则并入
    （assistant 行是主 agent 判断"实时 LLM 是否已解决"的关键上下文，攒批天然等到它）；
  - 距上次 nudge 不足 `nudge_min_interval_s`（默认 5s）→ 攒批；
  - 上一 nudge turn 未完成（in-flight）→ 攒批，turn 完成后合并补发；
  - 攒批窗口内的合并去抖 `nudge_coalesce_s`（默认 2s）。
- **截断**：增量截断到 `nudge_max_lines`（默认 20）/ `nudge_max_bytes`（默认 4KB），
  超出置 `truncated=true`，提示主 agent 自行读文件。
- **跨天 rollover**：文件不存在或 recording_session_id 变化时重新解析路径；
  路径变化时先发一条带新路径的 nudge（主 agent 更新监听目标）。
- 只读文件，不加锁不阻塞 writer（JSONL append + 5s flush，允许读到半行——按行解析即可）。

### 5.4 新增 `job_store.py` — JobStore（Redis）

```python
@dataclass
class JobRecord:
    job_key: str            # relay 生成 uuid（幂等键，主键）
    hermes_task_id: str     # prompt.background 返回（事件关联键）
    session_id: str; device_id: str
    status: str             # requested|running|done|failed|orphaned
    summary: str            # 主 agent 给的一句话
    fail_reason: str = ""   # timeout|orphaned|rpc_error
    created_at: float; updated_at: float
```

- Keys：`livachat:agent:hermes:job:{job_key}`（hash，TTL 2 天）；
  per-session 索引 `livachat:agent:hermes:session:{sid}:jobs`（set）；
  全局在途计数 `livachat:agent:hermes:inflight`（用于 `max_in_flight_jobs` 硬上限）。
- 职责：状态机写入（§4.4）、`outstanding_jobs(session_id)` 查询（供 NUDGE 拼装）、
  按 `hermes_task_id` 反查（background.complete 路由）、看门狗扫描。
- 刻意保持 "lite"：不做结果存储（结果即推即弃）、不做重投、不做 STATUS 轮询调度。

### 5.5 `adapters/hermes.py` — v2 重构

最大变化：从"ask() 内联收事件 + 全局锁"改为 **常驻 reader + 按 session 分发**：

```
HermesAdapterV2:
  connect(): 建 WS → gateway.ready → 启动 _reader_task
  _reader_task(): 循环 recv：
      - RPC response(id 匹配)          → 唤醒对应 _pending[req_id] future
      - message/thinking/reasoning.delta → 忽略（方案B 不变）
      - message.complete(session_id)   → 完成该 session 当前 turn 的 future
      - approval/clarify/sudo/secret.* → §4.5 兜底矩阵
      - background.complete            → JobStore 标 done → _push_callback(session_id, chunk(text))
      - error                          → 完成 future with exception
  ask(): reactive 模式，逻辑不变（改为向 reader 注册 turn future 等待完成）
  on_session_start(): session.create(close_on_disconnect=false, messages=[mission 种子])
                       → 存映射+Redis（§4.2）
  nudge(): prompt.submit(NUDGE JSON) → 等 turn future（nudge_timeout_s）
           → §4.3 契约解析：
               noop / 解析失败 → 不回推
               message        → yield chunk(text)
               job_request    → §4.4 两阶段派生（默认不回推；notify_job_started 可配
                                时 yield chunk("已开始：<summary>")）
  on_session_end(): session.close → 清映射
```

- 每 LivaChat session 一个 Hermes session；同一 Hermes session 的 prompt 串行
  （**per-session lock** 替代 v1 全局锁，不同 session 可并行）——尊重协议
  "turn 进行中重复提交会排队并默认打断"的行为（§3.2）。
- MONITOR/NUDGE/background 任务说明模板放 `services/agent_relay/prompts/`（新增目录），
  配置 `monitor_prompt_path` 可覆盖。

### 5.6 `config.py` — 配置新增

```python
class HermesAdapterConfig(BaseModel):
    base_url: str = "http://localhost:8080"     # 实测环境 http://127.0.0.1:9119
    api_key: str = ""
    timeout_s: int = Field(default=30, gt=0)
    max_retries: int = 2
    # -- v2 --
    mode: Literal["reactive", "proactive", "both"] = "proactive"
    nudge_timeout_s: int = 60                   # 主 agent 轻量 turn 超时
    nudge_coalesce_s: float = 2.0               # 攒批合并窗口
    nudge_min_interval_s: float = 5.0           # per-session nudge 最小间隔
    nudge_max_lines: int = 20
    nudge_max_bytes: int = 4096
    chatlog_poll_interval_s: float = 3.0
    max_concurrent_jobs_per_session: int = 2    # per-session 在途硬上限
    max_in_flight_jobs: int = 20                # 全局在途硬上限
    background_timeout_s: int = 1800            # 看门狗：running 超时收敛
    watchdog_interval_s: int = 60               # 看门狗扫描间隔
    job_requested_timeout_s: int = 60           # requested→running 对账超时
    notify_job_started: bool = False            # job 派生成功是否即时告知用户
    monitor_prompt_path: str = ""               # 空 = 内置默认模板
    reactive_max_wait_s: int = 30               # 可选（P3）：reactive 超时转后台
    # -- 2026-10-06 §5.8 连接保活 --
    liveness_probe_interval_s: float = Field(default=30.0, ge=0)  # 空闲连接应用层探测周期；0 = 关闭
    liveness_probe_timeout_s: float = Field(default=5.0, gt=0)    # 单次探测超时（tools.list）
    liveness_probe_failures: int = Field(default=2, ge=1)  # 连续超时阈值 → 拆连重连
```

### 5.7 Metrics（`metrics.py` 增补同模式 helper）

`nudge_sent / nudge_noop / nudge_coalesced / job_requested / job_started / job_completed /
job_rejected / job_timeout / job_noop_output / contract_parse_error / chatlog_resolve_failed /
session_resumed / interaction_autorespond{kind=clarify|sudo|secret}`；
推送侧复用现有 `inc_agent_reply` 结果口径（delivered/dropped_offline/timeout/error）。

---

### 5.8 WS 保活：应用层活性探测（2026-10-06 新增，review 修订）

#### 5.8.1 背景（2026-10-06 线上"半死连接"事故，以下均为实测/源码取证）

**事故**：relay 为等待一个运行中的 background job 而保留的 hermes 连接（loopback）
进入"半死"状态约 2 小时——**TCP 与 WS 协议层完全正常，两个方向的应用层消息都不通**：
hermes 的 `background.complete` 未被处理（job 卡 running，直到 48h 看门狗超时上限），
relay 的 nudge 注入未被 hermes 执行（hermes 侧零日志）。设备重连（拆旧连接换新会话）
后自愈。

**为什么现有机制覆盖不了**（逐项核实）：

| 层 | 现状 | 为何失败 |
|---|---|---|
| hermes 协议层 ping | `/api/ws` 无协议内 ping；uvicorn 协议 ping 在 **loopback 上被上游主动禁用**（`hermes_cli/web_server.py:18831` `ws_ping_interval=None if _is_loopback else 20.0`，上游注释 "Half-open detection for public binds only"——为避免事件循环被长任务卡顿误杀而有意为之，**仅公网绑定保留 20/20**） | 无 ping 可依赖 |
| relay 客户端协议 ping | 建连已带 `ping_interval=30 / ping_timeout=10`（`adapters/hermes.py:470`） | 事故中**未触发**任何断连——协议/传输层活 ≠ 应用层可派发；半死不产生 FIN/RST，协议 ping/pong 一路健康 |
| 断连检测 | `_reader_loop` 的 `ConnectionClosed/OSError` 路径（`hermes.py:549-558`：warn → ready.clear → teardown → backoff 重连） | 半死连接永不关闭，该路径不触发 |

**探测方案选型实测**（2026-10-06，对线上 hermes 只读探测 45 次）：

- `tools.list` 是 `/api/ws` 一等 JSON-RPC 方法（源码注册 `tui_gateway/methods_tools.py:1479`
  `@method("tools.list")`；实测同一 ws 连接发送，median **17.3ms** / max 29.4ms）；
- 语义：**任意 JSON-RPC 响应（含 error 响应）即证明应用层分派活着**，仅"无响应超时"判死
  ——探测走完整链路（relay 写 → hermes app 分派 → relay 读回），任意一层卡死都超时；
- **无 session 依赖**：`tools.list` 无会话门禁（缺 `session_id` 回落全局 toolset 配置，
  `methods_tools.py:1484-1489`），无 session 的全新连接实测直接成功；`_rpc` 空 params
  省略 `params` 键的线格式亦实测正常（19.6ms）——探测不触碰任何会话、不产生新会话
  （`session.active_list` 无探测条目）；与 §3.2/§9 Q1e 的结论不冲突（hermes **无**
  background task 查询方法，本机制不做 job 对账，见 §9 Q12）。

#### 5.8.2 设计

**（1）连接级任务**（与 reader 任务生命周期完全对齐）：
- `_DeviceConnection`（`hermes.py:114`）新增字段 `liveness_task: asyncio.Task[None] | None`；
- `_start_conn`（`hermes.py:318`，创建 `reader_task` 处）并列创建；
- `_stop_conn`（`hermes.py:322`，取消 `reader_task` 处）并列取消。

**（2）探测循环 `_liveness_loop(conn)`**：

```
周期 sleep(liveness_probe_interval_s)（= 0 关闭探测）
跳过条件（任一命中 → 记 debug 跳过本轮，不发探测）：
  - conn.ws is None / conn.stopped.is_set() / not conn.ready.is_set()
  - not await self._keep_connection(conn.device_id)
    （全空闲连接不探测：reader 对每条入站消息刷新 last_active_at
    （hermes.py:545），探测响应会让全空闲连接始终落在 idle reaper 的
    120s 活动阈值内（:383-389）而永不回收——只保护"保留连接"，全空闲
    连接交给 D8 reaper 正常回收；Redis 抖动期 _device_has_active_jobs
    fail-closed 返 True，退化为多探测几次，无害）
  - conn.pending 非空（有在飞 RPC）
  - conn.turn_futures 非空（有在飞 turn——事件流本身就是活性证据；
    且规避上游已知的"长 turn 卡事件循环数分钟"窗口，防误杀）
await self._wait_ready(conn)          # 在计数 try 之外：其异常按跳过本轮处理
发 await self._rpc(conn, "tools.list", timeout=liveness_probe_timeout_s)：
  任意响应 → 失败计数清零（含 JSON-RPC error 响应，见下）
  AdapterTimeoutError → 失败计数 +1
  计数 ≥ liveness_probe_failures →
    logger.warning("hermes ws liveness probe failed, forcing reconnect", ...)
    await self._teardown_ws(conn)      # 仅关 ws
循环体兜底 except Exception → log + continue（单次意外异常不得杀死探测
循环，否则保活静默停摆）
```

**计数规则**（对齐 `_resolve_rpc` 既有行为，`hermes.py:845-860`）：仅
`AdapterTimeoutError`（**无响应**）计失败——且仅限**探测实际发出后**的响应超时。
`_rpc` 入口 `_wait_ready` 在重连风暴窗口也会抛 `AdapterTimeoutError`（`:524`，
等 ready 超 `timeout_s`）：该来源按"跳过本轮"处理、不计失败，否则可能误拆 reader
刚重建的健康 ws 并刷出误导的 "forcing reconnect" 日志。注意 error 响应的既有形态：
hermes 应答 JSON-RPC error 时 `_resolve_rpc` 以
`AdapterConnectionError(status_code=<err.code>)` set_exception（`:850-858`）——
**这类带 `status_code` 的异常同样证明分派层活着，按"存活"清零**；其余连接层异常
（`status_code is None`，如拆连/重连窗口的 "WebSocket is not connected"）不计失败、
不刷日志（reader 负责）。判死判据唯一：**探测发出后的超时**。计数按连接划线：
`conn.ws` 换代（重连）时失败计数重新起算——"连续"以同一连接为界，重连后的第一条
超时不会搭上前一条连接的旧计数（防误拆刚重建的 ws，也防污染 `force_reconnect`
误杀率信号）。

**（3）拆连语义**：只 `_teardown_ws(conn)`（关 ws；**不** pop `_devices`、**不**关 hermes
会话、**不**置 stopped）——reader 循环随即按既有 `except (ConnectionClosed, OSError)`
路径重连（`:549-558`，含 backoff+jitter；`was_reconnect` → `_spawn_resume`）。
**零新增重连分支**，与现网断连行为逐位一致。

**（4）`_rpc` 扩展**：加 keyword-only 参数 `*, timeout: float | None = None`（缺省
None → 现行为 `self._cfg.timeout_s` 不变，`hermes.py:1853`）；探测传短超时。
keyword-only 避免与既有 positional `params` 调用混淆；单行改动、向后兼容。

**（5）配置**（`config.py` `HermesAdapterConfig`，归入 "v2 device connection & recovery" 组）：

```python
liveness_probe_interval_s: float = Field(default=30.0, ge=0)  # 空闲连接探测周期；0 = 关闭探测
liveness_probe_timeout_s: float = Field(default=5.0, gt=0)    # 单次探测超时（实测 RTT ~17–30ms，余量 >100×）
liveness_probe_failures: int = Field(default=2, ge=1)  # 连续超时次数阈值 → 拆连
```

取值依据：30s 把半死检测上限从"数小时"压到 **~70s（2×(interval+timeout)）**；若恰好
撞上卡死的在飞业务 RPC，再 +`timeout_s`（30s，该 RPC 自愈弹出后探测恢复）≈ 100s。
5s 超出实测 RTT 两个数量级，且探测仅在**空闲**时进行（业务活跃期不探测），进一步规避
上游事件循环卡顿窗口（该卡顿是上游禁 loopback ping 的原因，我们不重蹈）。

**background job 期间探测是安全的，不要为此加 skip**：上游 `prompt.background` 的
agent 跑在独立 `threading.Thread`（`tui_gateway/methods_prompt.py:1258`
`threading.Thread(target=run, daemon=True).start()`，非事件循环任务），
不阻塞 ws 分派——job 运行期间探测 RTT 不受影响，2×5s 超时判死仍然可信。这恰是本机制
的主要受益场景（保留连接等 job），若日后有人想加 `job_store` 类 skip，会把事故窗口
重新暴露出来。

#### 5.8.3 测试计划（基于现有基座）

`mock_hermes_server.py` 对未知方法 `_handle_rpc` 落 `return {}, []`（`:165`）
→ **`tools.list` 天然有响应，mock 无需改动即可支持探测**；超时模拟仿照既有
`submit_no_response`（`:104`，"swallow the request entirely (RPC timeout simulation)"）
加同款 `swallow_methods` 开关。

- 集成（`tests/integration/agent_relay/`，mock + 短周期参数）：
  - `test_liveness_probe_pings_idle_connection`：≥1 周期后 `mock.requests` 含 `tools.list`，连接未断；
  - `test_liveness_probe_timeout_forces_reconnect`：swallow `tools.list` → 连续 2 次超时 →
    断言 warning + 重连发生（mock 收到新连接）；
  - `test_liveness_probe_skipped_when_turn_or_rpc_in_flight`：占住 `turn_futures` / `pending`
    → 无 `tools.list` 请求；
  - `test_liveness_probe_disabled_by_zero_interval`：interval=0 → 完全无探测；
  - `test_liveness_probe_error_response_counts_as_alive`：mock 对 `tools.list` 回
    JSON-RPC error → 不计失败、不拆连（守"带 `status_code` 异常按存活清零"这条最易
    实现错的规则）；
  - `test_liveness_probe_failure_count_resets_on_success`：swallow 1 次后恢复正常响应
    → 计数复位、不拆连；
  - `test_liveness_probe_failure_count_resets_on_reconnect`：旧连接超时 1 次 + 强制拆连
    重连 → 新连接第一条超时不拆（计数按连接划线），第二条才拆（对齐"连续"语义，
    由实现 review 补入）；
  - `test_liveness_probe_does_not_starve_idle_reaper`（D8 回归）：无 job 无 live
    session 的全空闲连接不探测，且仍被 idle reaper 正常回收；
  - `test_liveness_probe_skipped_during_reconnect`：ready 未 set（重连窗口）→ 跳过，
    `_wait_ready` 超时不计失败。
- 单测（`tests/unit/agent_relay/`）：跳过判定/计数抽纯函数直测（对齐仓库纯函数测试惯例）。
- 验收：保留连接半死 ≤ 2×(interval+timeout) 内出现 warning 并重连；全量 hermes 用例 +
  真机连续会话无任何探测引发的断开（半死场景无法人工构造，依赖 swallow 模拟 + 真机
  日志 / `liveness_force_reconnect` metric 观察）。

#### 5.8.4 影响面

- 代码：`adapters/hermes.py`（`_DeviceConnection` / `_start_conn` / `_stop_conn` /
  `_liveness_loop` / `_rpc` 可选超时参）、`config.py`（3 字段，含 ge/gt 校验）、
  `metrics.py`（3 个同模式 counter，见下）、`app/routes.py`（/metrics 路由改为合并
  默认 + 自定义 registry 输出——实现时发现 app 指标从未在该端点暴露的存量缺口，
  一并修复：docs/server_design.md §metrics 契约）——**无协议 / 依赖 / 数据库变化**；
- Metrics：§5.7 增补 `liveness_probe_sent / liveness_probe_timeout /
  liveness_force_reconnect`——`force_reconnect` 的低频非零即真机误杀率信号，
  验收标准"真机无误杀"靠它长期观测，而非仅靠日志；
- 运行面：保留连接每 30s 一次 ~17ms 只读 RPC + 一次 `_keep_connection` Redis 查询，
  复用既有连接（无新连接、无新会话）；判死仅触发一次既有重连路径；全空闲连接不探测
  （§5.8.2(2) skip 条件），D8 idle reaper 行为不变。

#### 5.8.5 已知边界

- 拆连瞬间的在飞业务（如正在等响应的 nudge turn）按**既有断连语义**收尾（回退/重试走
  现网路径），不新增语义；
- 只探测"保留连接"（有 active job / live session 而空闲）——正是事故窗口；全空闲连接
  不探测、由 D8 idle reaper 正常回收（否则探测响应刷新 `last_active_at` 会使其永不
  回收，见 §5.8.2(2)）；
- **已丢的 `background.complete` 不回溯**（at-most-once 语义；对账补偿依赖上游能力，见 §9 Q12）。

---

## 6. Hermes 侧改动（配置，非本仓库代码）

1. **MCP 注册**：Hermes 配置新增 lvdata server（详见 lvdata-mcp README "Agent 客户端配置"）：
   `mcpServers.lvdata = {type:"http", url:"http://127.0.0.1:8090/mcp",
   headers:{Authorization:"Bearer $LVDATA_MCP_TOKEN"}}`（loopback 默认无鉴权可省 token）。
2. **审批策略**：relay 场景无人值守，background job 可能调用工具——通过 Hermes toolsets
   配置收窄可用工具集/降低审批级别，避免 job 卡在 approval（relay 的 §4.5 兜底只作用于
   主 WS 连接上的事件；background turn 的审批行为需实测，见 §9 Q1c）。
3. **主 agent 人格**：mission 契约经 `session.create messages` 种子注入（§4.3）；
   如需长期固化，可落为 Hermes skill，种子中仅引用。
4. **部署形态**：`hermes serve`（headless）+ 显式预置 `HERMES_DASHBOARD_SESSION_TOKEN`
   （relay config 的 `api_key`），与 LivaChat server、lvdata-mcp **同机部署**
   （chatlog 路径为本地文件路径；跨机部署见 §9 Q6）。

---

## 7. 可靠性与边界情况

| 场景 | 处理 |
|------|------|
| WS 断连 | `_ensure_connected` 重连 → 对存活 session `session.resume`（20s 孤儿宽限内）；恢复失败则重建 session 并重发 mission 种子 |
| WS 半死（TCP/协议层活、应用层消息不通；2026-10-06 事故实证） | §5.8 应用层保活探测：保留连接空闲时周期发 `tools.list`、短超时；连续超时达阈值拆连 → 走上方既有重连路径 |
| relay 进程重启 | `restore_sessions()` 从 Redis（`livachat:agent:hermes:session:{sid}`，含 hermes/stored id + device_id）重建内存映射 → `session.resume`（20s 宽限内复用 live 会话，宽限后经 stored id DB 重建）；restore 走 per-session start lock 防与 on_session_start 交错，失败仅移除自己恢复的条目；恢复失败的 session 由下一个 nudge 惰性重建；watcher 由首个 utterance 自愈重建（从 EOF 不回放）；JobStore 按 §4.4 对账，不主动重投 |
| Hermes 重启 | token 失效/连接失败 → 按现有 retry + backoff；`hermes serve` 预置 token 避免重取 |
| turn 进行中提交 | 协议语义为排队+默认打断（§3.2）：per-session lock 保证同 session 串行；nudge in-flight 时攒批，等 turn 完成后合并补发；不丢弃 chatlog（watcher offset 持续跟踪） |
| nudge 本地超时后旧 turn 残余 complete | 被 redirect 的 turn 仍会补发 `message.complete`（P0 实测，§3.2）；relay 对本地超时的 turn 打 **straggler 标记**，丢弃紧随的下一个同 session complete，防旧 turn 残余被误解析为新契约（fail-safe：宁可少推不可错推）；断连时标记清除 |
| background 运行中提交 nudge | 未实测（§9 Q2，可能 4009）；设计上 nudge 不依赖此期间成功——4009 视为 in-flight，攒批重试 |
| 主 agent 输出非契约 JSON | §4.3 解析规则兜底：按 noop + `contract_parse_error` metric + debug 日志（契约文本/废话绝不回推） |
| 主 agent 违规重复派生 | 硬上限拦截（per-session 2 / 全局 20），超限按 noop + `job_rejected` |
| background job 永不完成 | 看门狗 `background_timeout_s` 标 failed + metric；不停主 agent 的 turn |
| relay 停机期间 job 完成 | 事件丢失不可恢复：看门狗超时收敛；P0 追加实测 task 事后可查性（§9 Q1e） |
| 主 agent 请求 clarify/sudo/secret | §4.5 矩阵：clarify 固定应答，sudo/secret 告警 + interrupt |
| 同一天多通对话 | 共享 history.jsonl，按 `chat_session_id` 过滤（空值跳过）；watcher per chat session 独立 offset |
| UTC 跨天 | recording session 换新 → watcher 重解析路径 → 新路径 nudge 同步给主 agent |
| 设备离线 | 回推 dropped_offline 记 metric（v2 不暂存，见 §9 Q7） |

---

## 8. 分阶段实施

| Phase | 内容 | 验收 |
|-------|------|------|
| **P0 实测验证** | 脚本实测 §9 Q1–Q4（`prompt.background` 全行为、busy 语义、messages 种子、阻塞交互触发条件） | ✅ 完成（2026-08-09，`tests/manual/p0_hermes_probe.py`）；结论回填 §3.2/§9；Q1e 结论：task 不可事后查询，看门狗**不加**"先查后判"，维持超时收敛 |
| **P1 主链路** | base.py 扩展、session_manager 路由、ChatlogWatcher、JobStore、hermes.py v2 重构、mission/NUDGE 契约、config、§4.5 兜底矩阵 | 单测（mock WS server 扩展 `prompt.background`/`background.complete`/clarify 等）+ 集成测试；dev 环境一通对话可收到 hermes 主动 ai_text |
| **P2 记忆** | Hermes 注册 lvdata MCP；background 任务模板加入 MCP 使用指引 | 人为构造"依赖历史偏好"的对话，验证 job 调用了 lvdata tools |
| **P3 打磨** | 看门狗完善、reactive_max_wait 转换、离线暂存评估、metrics/日志完善、mock_agent_server 对齐 | 压测（多会话并发 job 的连接数/内存开销）+ soak |
| **P4 连接保活**（2026-10-06 新增） | §5.8 应用层活性探测（`tools.list`）；mock `swallow_methods` 开关 + 集成/单测 | 保留连接半死 ≤ 2×(interval+timeout)（~70s）内拆连重连；真机长会话无探测误杀 |

---

## 9. 开放问题

**P0 必答（阻塞 P1 开工）**——✅ **已实测**（2026-08-09，`tests/manual/p0_hermes_probe.py`，
结论回填 §3.2）：

1. **Q1 `prompt.background` 协议细节**：
   a. ✅ 参数 `{session_id, text}`（text 必填），响应 `{task_id: "bg_<hex>"}`；
   b. ✅ **不继承**父会话历史（全新 AIAgent，仅继承模型/工具链配置）→ 任务说明内联全部上下文；
   c. ✅ background turn 内交互事件不投递主连接（无 transport）→ relay 兜底仅主 turn，
      Hermes 侧 toolsets 收窄为第一道防线（§6.2）；
   d. ✅ `background.complete.payload.text` 即最终全文；异常时 `text` 以 `error: ` 开头（relay 丢弃）；
   e. ✅ **不可事后查询**（threading 内存任务，无 kanban/DB 记录）→ 看门狗"先查后判"不可行，
      维持"先标 failed + 完成事件到达时丢弃"。
2. **Q2 busy 语义**：✅ turn 进行中 `prompt.submit` → **排队 + 默认打断**（响应
   `{"status": "redirected"}`，非 4009）；`4009 subagent still running` 仅 lazy 子代理场景。
   → nudge in-flight 时攒批、turn 完成后合并补发（§5.3 已实现）。
3. **Q3 mission 种子**：✅ **生效**（system role 种子被模型严格遵循）→ 无需 fallback，
   保持 `session.create(messages=[...])` 注入（§4.3 主路径）。
4. **Q4 阻塞交互触发条件**：✅ `approval.respond` / `clarify.respond` 方法确认存在
   （无 pending 时返回 4009 属正常语义）；clarify/sudo/secret 的实际触发场景难以在
   无人值守环境自然触发，§4.5 矩阵按协议语义覆盖，维持现状。

**后续版本评估**：

5. **Q5 多设备并发**：单 Hermes 实例承载多 device 的监听 session 是否有并发上限
   （4090 活跃会话数上限）；达到上限时的排队/降级策略。
6. **Q6 跨机部署**：chatlog 以本地路径交给 Hermes 的前提是三者同机；跨机时需要
   relay 提供 chatlog HTTP 拉取端点或改为 nudge 内嵌全文（带宽换部署自由度）。暂按同机。
7. **Q7 离线暂存**：proactive 结果（尤其长 job 产出）在设备离线时丢弃可惜；可在
   PushPublisher 前加 Redis 暂存队列，设备重连后补投（需定义过期与去重）。
8. **Q8 客户端展示协调**：proactive `ai_text` 到达时若主 LLM 播报/TTS 进行中，
   客户端是打断、排队还是仅文本展示——产品决策，需在客户端协议评审时明确。
9. **Q9 用户打断语义**：job 运行中用户发起新话题，是否用 `session.steer`/`session.redirect`
   注入新上下文重定向，还是放任跑完；v2 先放任跑完（orphaned 不推送）。
10. **Q10 触发延迟优化（v2.1）**：文件 tail 最坏延迟 ~8s（5s flush + 3s 轮询）；
    可改由 `RecordingSession.on_chat_text` 同步直发 `chat.turn` 事件（文件仍作回放源），
    需评估对主 pipeline 的侵入。
11. **Q11 与 recording.ready 的关系**：v2 用 chatlog 监听替代了 `analyze_recording()`
    的 hermes 实现；`recording.ready` 事件保留给 openclaw 等 adapter，两者并存不冲突；
    音频分段分析的 hermes 真实现留待 v2.1。
12. **Q12 半死连接与 completion 对账（2026-10-06 新增）**：
    a. 连接"半死"（TCP/协议层活、应用层消息不通——2026-10-06 事故实证，§5.8.1）
       由 §5.8 应用层保活探测覆盖（检测 + 拆连重连）；
    b. `background.complete` **at-most-once**：事故中完成事件丢失无法回溯（Q1e 复核：
       hermes 无 background 查询方法）。补偿对账依赖上游能力（任务查询接口或
       complete 重发/重放）；在此之前维持 §7"事件丢失不可恢复 + 看门狗超时收敛"。
       本次事故的该 job 已由人工从 hermes 日志提取结果文本 `mark_done` 补投（一次性处理，
       非通用路径）。
