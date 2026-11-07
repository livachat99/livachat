# OpenClaw Gateway WebSocket API 调研文档

> **调研对象**：OpenClaw Gateway（npm 包 `openclaw`，"Multi-channel AI gateway"，<https://github.com/openclaw/openclaw>）
> **实测环境**：本机 `ws://127.0.0.1:18789`，OpenClaw **2026.10.3**（构建 `59cb447`），`gateway.mode=local` + `bind=loopback` + `auth.mode=token`，launchd 服务 `ai.openclaw.gateway`
> **用途**：为 LivaChat `agent-relay` 的 **OpenClawAdapter**（`services/agent_relay/adapters/openclaw.py`，当前为 stub）打基础——认证、连接、会话/消息、事件、长任务与审批的协议事实
> **事实来源**（三类，正文逐条标注）：
> - **[官方文档]** 随包发布的官方协议手册 `~/.openclaw` 无关，位于 npm 包内 `docs/gateway/protocol/*.md`（v2026.10.3 随包版本，等同于官网手册的对应版本）
> - **[源码]** 随包发布的实现代码 `dist/*.mjs`（未混淆、含注释，行号可查）
> - **[实测]** 对本机运行中的 gateway 的只读探针（`/tmp/openclaw_ws_probe.py` 等，原始帧落 `/tmp/openclaw_probe_frames.jsonl`）；**未触发任何 chat run**
> **调研日期**：2026-10-14（同日补充 §9：agent/subagent 模式与 job 生命周期）

---

## 1. 总览

### 1.1 形态

OpenClaw 是一个多通道 AI agent 网关（Node.js，`openclaw gateway --port 18789`）。
同一个 HTTP 端口上多路复用多种接入面，**WebSocket 协议是唯一控制面**（operator/node/worker 三类角色共用）：

| 类型 | 路径 | 用途 |
|------|------|------|
| **WebSocket** | `/`（根路径，即 `ws://127.0.0.1:18789`） | **主协议**：operator/node 客户端。会话、消息、事件、审批、自动化。**对接核心** [实测] |
| WebSocket | `/__openclaw__/worker` | Worker 专用**封闭协议**（云执行主机专用，哈希凭证准入）——不适用外部对接 [官方文档] |
| HTTP | `/v1/chat/completions` 等 | OpenAI 兼容 API，**默认关闭**，见 §7 [官方文档] |
| HTTP | 其余 REST（控制 UI、附件下载等） | 浏览器/原生客户端内部使用，本文不展开 |

本机部署（`~/.openclaw/openclaw.json`）：

```json5
gateway: {
  port: 18789,
  mode: "local",
  bind: "loopback",
  auth: { mode: "token", token: "<40-hex-secret>" }   // 共享密钥
}
```

### 1.2 对接选型建议（先说结论）

| 需求 | 推荐通道 |
|------|---------|
| 与 OpenClaw agent 进行**对话交互**（发消息、收流式回复、订阅工具/审批事件） | **根路径 WS 协议**（`chat.send` + `sessions.messages.subscribe`，见 §4/§5） |
| **一次性问答**（发一条、等终稿） | WS `agent` + `agent.wait`（§4.5） |
| **长任务/自动化** | WS 常规 run 即异步长活（runId + 事件流，§8.5）；另有 `wake` / `cron.*` |
| 审批/交互兜底（无人值守） | WS `exec.approval.*` / `question.*`（§4.6） |
| 不想实现 WS，只要 OpenAI 语义 | HTTP `/v1/chat/completions`（默认关，且**权限=owner 全量 operator**，见 §7）——不推荐 |
| 官方 SDK | 只有 **JS/TS**（`@openclaw/gateway-client` / `@openclaw/gateway-protocol`）；**无 Python SDK**，relay 需按本文自研轻客户端 [官方文档] |

**关键结论（与 Hermes 最大的架构差异）**：OpenClaw 的 WS 是**单条长连 + 订阅分发**模型，sessions 是服务端持久化的一等实体——不存在 Hermes 那种"每 device 一条连接、session 随连接回收、20s 拿不回来就丢结果"的约束。relay 侧应设计为**每个 OpenClaw 实例一条连接**（§8.2）。

---

## 2. 认证与连接安全

### 2.1 auth 模式

`gateway.auth.mode` 取值（[官方文档] `protocol/auth.md` + [源码] `message-handler`）：

| mode | 语义 | relay 可用性 |
|------|------|-------------|
| `token`（本机现用） | 共享密钥；WS 上放在 `connect.params.auth.token` | ✅ |
| `password` | 同上，字段为 `connect.params.auth.password`；mode 决定校验哪个字段，不限制客户端带哪个 | ✅ |
| `none` | 私网部署时跳过共享密钥——**不可暴露公网** | 仅受信私网 |
| `trusted-proxy` | 身份由代理头携带 | 不适用 |
| `tailscale` | Tailscale Serve 身份 | 不适用 |

此外还有**设备身份（device identity）+ 配对（pairing）**机制：Ed25519 密钥对 → 连接时对服务端 `connect.challenge` 签名 → 配对批准后签发 per-device `deviceToken`（后续连接凭证）。对远程/公网接入是主路径，但**对 relay 不是必需的**——见下节。

### 2.2 LivaChat relay 采用的认证路径：loopback 后端客户端免配对

[源码] `dist/role-policy-DjGeFe84.mjs` 与 `dist/node-connect-reconcile-CV2jR04R.mjs`：

```js
function roleCanSkipDeviceIdentity(role, sharedAuthOk) {
  return role === "operator" && sharedAuthOk;             // operator + 共享密钥校验通过 → 允许无 device
}
function shouldSkipLocalBackendSelfPairing(params) {
  // client.id === "gateway-client" && client.mode === "backend"
  // 且 locality ∈ { direct_local, shared_secret_loopback_local }，无浏览器 Origin，共享密钥 OK
  // → 跳过 pairing 检查，且【scope 不被清空】
}
```

即：**从 loopback 连接、`client.id="gateway-client"`、`client.mode="backend"`、role=`operator`、携带共享 token，则免 device 身份、免配对、请求的 scopes 原样保留**。已实测验证（§10.1）：`auth.scopes` 回显 `["operator.read","operator.write"]`，read/write 方法可直接调用。[官方文档] `protocol/auth.md` 把这个路径描述为"reserved internal helper path"（保留给内部 control-plane），并注明**自定义 backend client id 不享受该例外**——relay 必须使用字面量 `gateway-client`。
> 风险备注：该路径属官方文档中的内部保留路径，长期稳定性弱于"device identity + pairing"正路。若未来被收紧，退路是实现 Ed25519 设备签名 + 配对（[官方文档] `protocol/auth.md` 有完整流程：challenge→签名 v3 payload→`PAIRING_REQUIRED`→主机侧 `openclaw devices approve`→持久化 deviceToken）。

### 2.3 认证失败错误目录

[官方文档] `protocol/auth.md` 的 `error.details` 体系；[实测] 已确认两条：

| 场景 | error.code | details.code | 备注 |
|------|-----------|--------------|------|
| token 不对 | `INVALID_REQUEST` | `AUTH_TOKEN_MISMATCH` | + `canRetryWithDeviceToken:false`、`recommendedNextStep:"update_auth_credentials"`；随后服务端 close 1008 [实测] |
| 完全不带 auth | `NOT_PAIRED` | `DEVICE_IDENTITY_REQUIRED` | 消息 "device identity required"——**注意不是 "unauthorized"**，容易误判 [实测] |
| 设备签名类（8 种） | `INVALID_REQUEST` | `DEVICE_AUTH_*` | `DEVICE_AUTH_NONCE_REQUIRED/MISMATCH/SIGNATURE_INVALID/SIGNATURE_EXPIRED/DEVICE_ID_MISMATCH/PUBLIC_KEY_INVALID` 等 [官方文档] |
| scope 不匹配 | `FORBIDDEN` | `MISSING_SCOPE` | payload 带 `missingScope`/`requiredScopes` [实测] |
| 已配对但 scope 被缩窄 | — | `AUTH_SCOPE_MISMATCH` | 不是坏 token，提示重新配对 [官方文档] |
| 身份通过但 operator 策略拒绝 | — | `OPERATOR_ACCESS_DENIED` | 保持带退避重连 [官方文档] |

`recommendedNextStep` 枚举：`retry_with_device_token` / `update_auth_configuration` / `update_auth_credentials` / `wait_then_retry` / `review_auth_configuration`。

### 2.4 连接限额与保活

[官方文档] `protocol/transport.md` + [源码] `ws-connection` + [实测]：

| 项 | 值 | 来源 |
|----|----|------|
| 未认证连接数上限 | 每 IP **128** 条（认证成功即释放名额） | 官方文档 |
| 握手前帧上限 | **64 KiB**（`MAX_PREAUTH_PAYLOAD_BYTES`） | 官方文档 |
| 握手超时 | 参考客户端 15 s（`OPENCLAW_HANDSHAKE_TIMEOUT_MS`，服务端另有握手计时） | 官方文档（versioning） |
| 握手后帧上限 | `hello-ok.policy.maxPayload`，实测 **25 MiB**（26214400） | 实测 |
| 发送缓冲上限 | `policy.maxBufferedBytes`，实测 **50 MiB**；超限以 1008 "slow consumer" 关闭 | 实测/源码 |
| 保活 | WS ping/pong（pong 窗口 25 s）+ 服务端每 30 s 发 `tick` 事件 | 官方文档/实测 |
| 客户端静默判死 | 参考客户端在静默 > `tickIntervalMs × 2` 时以 close code **4000** 断开并重连（不重放已拒请求） | 官方文档 |
| 压缩协商 | **不支持 `permessage-deflate`**（有意为之） | 官方文档 |
| 在途请求排队 | 有聚合+每连接等待上限，超限返回可重试 `UNAVAILABLE`；**响应可乱序返回**（按 id 匹配） | 官方文档 |

---

## 3. 协议帧与握手流程

### 3.1 帧格式（三种）

[官方文档] `protocol/transport.md`，[源码] `dist/ws-connection-DDJOEJN-.mjs`：

```
Request : { "type": "req",   "id": "...", "method": "...", "params": {...}, "traceparent"?, "expectedProfileId"? }
Response: { "type": "res",   "id": "...", "ok": true, "payload": {...} }         // 失败: ok=false, "error": {code,message,details?,retryable?,retryAfterMs?}
Event   : { "type": "event", "event": "...", "payload": {...}, "seq": N, "stateVersion"?, "recipientProfileId"? }
```

- 文本帧、JSON；`id` 由客户端生成，响应按 `id` 匹配（**可乱序**）。
- 有副作用的方法要求 `idempotencyKey`（schema 强约束，如 `chat.send` 必填 [源码]）。
- `traceparent`（W3C）可选，仅用于追踪。

### 3.2 握手时序（[实测] 完整样例见 §10.1）

```
客户端 ──WS 连接──▶ gateway:18789
         ◀── event "connect.challenge"  { payload: { nonce, ts, capabilities: ["model-catalog-snapshot"] } }   （连接建立后立即下发；[源码] ws-connection:307-316）
客户端 ── req "connect" ──▶           （首帧必须是 connect；参考客户端握手预算 15s）
         ◀── res "connect" ok=true  payload = hello-ok
之后：自由 req/res + event 流
```

`connect.params` 关键字段 [官方文档 handshake.md + 实测]：

```json
{
  "minProtocol": 4, "maxProtocol": 4,          // operator 必须含协议 v4（N-1=v3 仅限 node）
  "client": { "id": "gateway-client", "mode": "backend", "version": "…", "platform": "darwin" },
  "role": "operator",
  "scopes": ["operator.read", "operator.write"],
  "caps": [],
  "auth": { "token": "…" },
  "locale": "zh-CN", "userAgent": "…"
  // device: { id, publicKey, signature, signedAt, nonce }   ← 仅设备身份路径需要；relay 路径省略
}
```

`hello-ok`（实测本机完整形态，节选）：

```jsonc
{
  "type": "hello-ok",
  "protocol": 4,
  "server": { "version": "2026.10.3", "buildId": "...", "bootId": "...", "connId": "..." },
  "features": {
    "methods": [ ...496 个方法名... ],
    "events":  [ ...68 个事件名... ],
    "capabilities": [ "profile-binding-v1", "session-scoped-model-catalog", ... ]
  },
  "snapshot": { "presence": [...], "health": {...}, "stateVersion": {...}, "uptimeMs": ..., "sessionDefaults": {...} },
  "auth":  { "method": "token", "role": "operator", "scopes": ["operator.read","operator.write"], "recoveryScope": "<opaque>" },
  "policy": {
    "maxPayload": 26214400, "maxBufferedBytes": 52428800,
    "tickIntervalMs": 30000,
    "attachments": { "maxBytes": 19464192, "maxImageBytes": 6291456 },
    "allowedSessionVisibilities": ["shared","read-only","suggest","draft"]
  }
}
```

- `auth.scopes` 是**本连接实际生效的权限**（与 RPC 派发一致）；[实测] loopback 后端路径下请求什么就是什么，不被清空。
- `features.methods` 是**发现列表而非全量**（[官方文档] rpc-methods 明说部分方法如 `push.test` 真实可调但不在列表）。
- `recoveryScope` 是 operator 的恢复身份哈希（sha256，[源码] message-handler:2339-2348），relay 可忽略。

### 3.3 事件序列号与重连

- 每条连接有独立的**单调递增 `seq`**（连接级，非全局）。[实测] 一次连接内观察到 `seq` 1→2→3→4 连续。
- **seq 出现空洞 = 可能丢帧**：参考客户端的行为是立即以 4000 关闭并重连（特例：若空洞帧本身是 `chat` 的 final/aborted/error，先交付该终态再断）。
- **重连不重放**：订阅随连接失效；重连后需重新 `sessions.messages.subscribe`（或以 `sessions.subscribe` 重建 roster），服务端会**带上完整快照补基线**（首帧含 `message` 全量对象）。
- 文本类事件是"初始快照 + 增量 delta"模型；`deltaText` 是追加内容，`replace=true` 表示整段替换（含空串清除）[官方文档]。

---

## 4. RPC 方法目录（对接相关）

### 4.1 方法面规模

[源码] `dist/core-method-policy-CcBevufV.mjs`：核心方法规格 **485** 条（每条含 family 与最小 scope）；[实测] 本机 `hello-ok.features.methods` 列出 **496** 个。方法族约 130 个（`sessions-*` `chat` `agent` `nodes` `users` `talk` `cron` `skills` `config` …）。`config.*` / `exec.approvals.*` / `wizard.*` / `update.*` 等保留前缀强制 `operator.admin`。

对 adapter 最相关的方法（scope 来自 [源码] 目录表）：

| 方法 | scope | 用途 |
|------|-------|------|
| `sessions.list` / `sessions.describe` / `sessions.resolve` | read | 会话枚举/解析（resolve 支持 label、短 ID、`reference`） |
| `sessions.create` | **dynamic**（写） | 创建会话（可选带首条消息） |
| `chat.send` | write | **向会话发消息（核心）** |
| `chat.history` / `sessions.get` / `chat.message.get` | read | 读历史（显示归一化 / 原始 store） |
| `sessions.messages.subscribe` / `.unsubscribe` | read | **订阅单会话消息与工具事件（核心）** |
| `sessions.subscribe` / `sessions.list` | read | 订阅会话索引变化（roster） |
| `chat.abort` / `sessions.abort` | write | 中断运行 |
| `sessions.patch` / `sessions.reset` / `sessions.delete` / `sessions.compact` | 写（部分 admin） | 会话维护 |
| `agent` | **dynamic**（按会话共享策略判定） | 主 agent-run 请求（一次性，见 §4.4） |
| `agent.wait` | write | 等待某个 runId 的终态 |
| `wake` | write | 唤醒注入（now / next-heartbeat，§4.7） |
| `tools.invoke` | write | 直接调用单个工具（gateway 策略管线内） |
| `exec.approval.resolve` / `question.resolve` / `approval.resolve` | approvals / questions | 审批与交互作答（§4.6） |
| `models.list` / `tools.catalog` / `agents.list` | read | 目录类查询 |
| `sessions.usage*` / `status` / `health` / `logs.tail` | read | 运维观测 |

### 4.2 `chat.send`（消息发送核心）

[源码] `dist/sessions-DJqMAtE3.mjs`（`ChatSendParamsSchema`），参数要点：

```jsonc
{
  "sessionKey": "agent:<agentId>:<key>",   // 必填；组合键定位（见 §8.3）
  "message": "…",                          // 必填
  "agentId": "…",                          // 可选，多 agent 路由校验
  "queueMode": "steer|followup|collect|interrupt",  // 默认会话配置（本机默认 steer）
  "idempotencyKey": "…",                   // 【必填】幂等键
  "timeoutMs": 0,                          // 可选
  "deliver": false,                        // 可选：请求出站投递（渠道类）
  "attachments": [...], "thinking": "...", "fastMode": "...", "expectedLeafEntryId": ...
}
```

响应（ack，[源码] `chat-send-handler` + [官方文档]）：

```json
{ "runId": "…", "status": "started", "messageSeq": 42, "interruptedActiveRun": false, "serverTiming": {...} }
```

- ack 表示**已受理**，不代表已落 transcript（`messageSeq` 缺席时按提交身份对账，别按位置猜）。
- 运行中的会话重复 send：`queueMode` 决定行为（`steer` 注入当前 turn / `interrupt` 打断重开 / `followup` 排队 / `collect` 收集）——[官方文档] 明说**不报错**，这是与 Hermes `4009 busy` 相反的设计。
- 随后在已订阅的连接上收 `chat` 事件流（§5.2），以 ack 的 `runId` 关联。

### 4.3 订阅（两条粒度）

- **roster 级**：`sessions.subscribe`（参数同 `sessions.list`，如 `{limit:60, ownerFirst:true}`）→ 响应 `{subscribed:true, list:[...]}` 一次性拿列表并持续收 `sessions.changed`。
- **单会话级**：`sessions.messages.subscribe {key, agentId?, includeApprovals?}` → 实测 ack：

  ```json
  { "subscribed": true, "key": "agent:main:cron:8068412b-…", "agentId": "main" }
  ```

  之后该连接收到此会话的 `session.message` / `session.operation` / `session.tool` 等事件；`chat` 流事件本就需要 `operator.read`（广播门）。取消订阅 `sessions.messages.unsubscribe {key}`（**key 必填**，实测 `{}` 会得到参数校验错误，§10.2）。
  可选的 `mode:"narration"` 订阅只收摘要快照（≤16384 字符的可见文本尾），适合"只要一句话状态"的消费方 [官方文档]。

### 4.4 `agent` + `agent.wait`（一次性问答路径）

[源码] `dist/src-l2dWvEsN.mjs`（真实 schema）：

```jsonc
// method: "agent"  —— 主 agent-run 请求（scope: dynamic，按会话共享策略动态判定；relay 配 operator.write 覆盖常规调用）
{ "message": "…", "agentId"?, "sessionKey"?, "sessionId"?, "deliver"?, "bestEffortDeliver"?, "timeout"?, "promptMode": "full|minimal|none", ... }
// 响应（受理）：{ "runId": "…", "status": "started" | "in_flight" }

// method: "agent.wait" —— 等待某个已提交 run 的终态（scope: operator.write）
{ "runId": "…", "timeoutMs": 1500 }
// 实测（对不存在的 runId）：约 1.5s 后 → { "runId": "...", "status": "timeout" }
```

- `agent` 与 `chat.send` 的区别：`agent` 是"投递/渠道"语义的一次性 run（[官方文档] operator-methods §Agent delivery fallback：`deliver=true` 请求出站投递，`bestEffortDeliver=true` 允许降级为仅会话内执行），可带 `deliver` 到外部渠道；`chat.send` 是会话内交互，适合订阅式收流。
- 对 relay：**reactive 短问答用 `agent`+`agent.wait` 最省事**（不必维护订阅）；**持续会话用 `chat.send`+订阅**。

### 4.5 会话维护

- `sessions.create`：可选 `key`（客户端自定义组合键，建议 device 派生，见 §8.3）、`idempotencyKey`、`label`（唯一）、`displayName`、`model`/`thinkingLevel`/`permissionMode`、`visibility`、`parentSessionKey`（带 `fork`，或配 `spawnDepth` 建会话树谱系，见 §9.2）等；也支持 `message`/`attachments` 直接带首轮。[源码] `SessionsCreateParamsSchema`。
- `sessions.send {key, message, idempotencyKey?}`：向既有会话投递消息（无 queueMode，见 §9.2）；对 **paused** 子会话有"续投并保留原任务与完成受众"语义。
- `sessions.abort {key?, runId?, clearQueued?}` / `chat.abort {sessionKey, runId?}`：带 `runId` 时精确取消该 run **及其子孙**；key 级会话取消也请求级联子树；`clearQueued:true` 清 queued follow-up（详见 §9.2）。
- `sessions.patch`：改标题/模型/思考级别等（写字段 `operator.write`，特权覆盖需 admin；archive 需带 `expectedSessionId` CAS）。
- `sessions.reset`/`delete`/`compact`：重置/删除/压缩（删/重置多为 admin）。
- `sessions.resolve`：把 label、短 ID、`reference{key,slug}` 解析为规范 key（歧义时返回候选集，成功形态）。

### 4.6 审批与交互（无人值守兜底）

[官方文档] `protocol/rpc-devices-nodes-and-approvals.md`：

| 家族 | 方法 | scope |
|------|------|-------|
| 统一审批 | `approval.get` / `approval.resolve` / `approval.history`（持久，30 天） | `operator.approvals` |
| exec 审批 | `exec.approval.request/get/list/resolve/waitDecision`；策略 `exec.approvals.get/set` | `operator.approvals`（策略为 admin） |
| plugin 审批 | `plugin.approval.request/list/waitDecision/resolve` | `operator.approvals` |
| 交互提问 | 事件 `question.requested/resolved`；方法 `question.request/waitAnswer/resolve/get/list` | `operator.questions` |

事件流：`exec.approval.requested/resolved`、`question.requested/resolved` 等（§5.4）。relay 兜底策略与 Hermes 类似：要么 relay 侧按策略自动 resolve/deny，要么留给操作者。

### 4.7 唤醒与自动化

- `wake {mode: "now"|"next-heartbeat", text, sessionKey?, agentId?}`：向会话注入唤醒文本（外部调度器/设备向 agent 会话投喂事件的标准入口）。[源码] `WakeParamsSchema`。
- `cron.*`：`cron.add/update/remove/run/runs/status`，`cron.run` 支持 `waitTimeoutMs` 就地等结果。
- 心跳：agent 级 `heartbeat`（本机 main agent 30min 周期，`status` 可读）。

### 4.8 工具与目录

- `tools.catalog`（read）：工具目录（grouped，`source: core|plugin`）。
- `tools.effective {sessionKey}`（read）：某会话的**预期工具预览**（服务端派生，非运行中真实清单）。
- `tools.invoke {name, args?, sessionKey?, agentId?, confirm?}`（write）：直接调一个工具，走 gateway 工具策略管线；审批/策略拒绝以 `ok:false` 返回 payload 而非绕过。
- `models.list`（read）：模型目录，6 行（本机实测）。

---

## 5. 事件目录

### 5.1 两类事件与作用域门

[官方文档] `protocol/presence.md` §Broadcast event scoping：

- **广播类**（认证后即可收，按 scope 过滤）：`presence`、`tick`、`heartbeat`、`shutdown`、`health`、`cron`、`plugins.changed`、`config.changed`、`skills.changed`、`node.*`、`device.pair.*`、`exec.approval.*`、`question.*` 等。
- **会话类**（需 `sessions.messages.subscribe` 或 roster 订阅）：`chat`、`agent`、`session.message`、`session.operation`、`session.tool`、`session.approval`（需 `includeApprovals`）、`session.narration`、`session.observer`、`sessions.changed`。
- chat/agent/tool-result 帧要求至少 `operator.read`；未知事件族**默认 fail-closed**。
- 本机 `hello-ok.features.events` 实测列出 68 个事件名（完整清单见 §10.1）。

### 5.2 `chat` 事件（assistant 输出主通道）

[源码] `dist/sessions-DJqMAtE3.mjs`（`ChatEventSchema`，五态 union）：

| state | 关键字段 | 语义 |
|-------|---------|------|
| `status` | `phase`（waiting_for_state / preparing_workspace / starting_model …） | 运行启动期状态；retry 信息含 rate_limit |
| `delta` | `deltaText`（追加）、`replace`（整段替换）、`message`（**首次/换基线时给完整快照**） | 流式文本 |
| `final` | `message`、`usage`、`stopReason`、`yielded` | 成功终态（权威全文） |
| `aborted` | `message`、`errorMessage`、`stopReason` | 取消终态 |
| `error` | `message`、`errorKind`（refusal/timeout/rate_limit/context_length/state_contention/unknown）、`errorDetail`（sanitized provider 信息） | 失败终态 |

公共字段：`runId`（关联 ack）、`sessionKey`、`agentId?`、`spawnedBy?`、`seq`（run 内序号，可能跳号——被节奏化合并，非连接级 seq）。

> relay 接线注意：**别把 `chat` 流与 `agent` 流累加到同一个缓冲**（[官方文档]："consuming both streams into one accumulator duplicates output"）。Android/原生客户端可选择 advertize `caps:["chat-only-assistant-text"]`，服务端就不再发文本类 `agent` 事件。

### 5.3 `session.*` 家族（订阅者）

[官方文档] `protocol/rpc-bootstrap-and-events.md`：

- `session.message`：transcript 级事件（含完整消息快照，重连补基线靠它）。
- `session.operation` / `session.tool`：进行中的操作/工具事件流（结构化工具生命周期需连接 advertize `caps:["tool-events"]`，否则**收不到工具事件且不报错**）。
- `session.approval`：sanitized 审批事件（需订阅时显式 `includeApprovals: true` + 审批权限）。
- `session.narration`：摘要快照（narration 模式）。
- `session.observer`：模型生成的会话状态摘要（headline + health，含 `runId`/`activeRunIds` 关联）。
- `sessions.changed`：roster 变化（reason=patch/send/chat.title/activity-summary…）。

### 5.4 系统事件（[实测] 40s 捕获）

`health`（每次 health 快照变更广播，payload 含 plugins/channels/agents/sessions/eventLoop 等）、`chat.metadata.changed`、`tick {ts}`（30s 周期）。实测原帧见 §10.2。

---

## 6. 错误码与失败语义

错误帧形态（§3.1）；[实测] 已确认的行为：

| 请求 | 结果 |
|------|------|
| `sessions.abort {key:"__probe_nonexistent__"}`（read-only scope） | `FORBIDDEN` / `MISSING_SCOPE`：`missingScope:"operator.write"`, `requiredScopes:["operator.write"]` |
| 同一请求（write scope） | `ok:true, {ok:true, abortedRunId:null, status:"no-active-run"}`（幂等语义） |
| `no.such.method`（write scope） | `FORBIDDEN` / `MISSING_SCOPE`（**要求 operator.admin**）——未知方法走的是 admin 兜底策略，不是 METHOD_NOT_FOUND |
| `sessions.describe {key:"__probe_nonexistent__"}` | `ok:true, {session:null}`（读不存在=null，非错误） |
| `sessions.messages.unsubscribe {}` | `INVALID_REQUEST`：`"invalid sessions.messages.unsubscribe params: must have required property 'key'"`（**参数校验错误文本带 schema 细节**） |
| 连接时 token 错误 | `INVALID_REQUEST` / `AUTH_TOKEN_MISMATCH`（细节见 §2.3），随即 close 1008 |
| 连接时无 auth | `NOT_PAIRED` / `DEVICE_IDENTITY_REQUIRED` |

通用语义：客户端应 `code`+`details.code` 分支判断；`message` 是人类可读文本可能变化。可重试错误带 `retryable:true` / `retryAfterMs`；排队饱和时是 `UNAVAILABLE`。close code 约定：1008 认证/策略拒绝；1012 服务重启（"service restart"/"gateway-shutdown"）；4000 客户端静默超时（客户端侧发起）。

---

## 7. 备选通道（对照）

- **HTTP OpenAI 兼容**（`/v1/chat/completions` 等，默认关闭）：[官方文档] 明确"**treat as full operator access**"——带共享 token 的请求会**恢复完整 operator 权限集**（含 admin），且忽略 scope 缩窄头。仅 loopback/私网。适合"不想实现 WS、只要 OpenAI 语义"的场景，语义损失大（无会话订阅/事件/审批流）。relay 不建议。
- **HTTP tools invoke**（`/gateway/tools-invoke-http-api`）：单工具调用 REST。
- **`openclaw.*` RPC 族**（本机 features 列出 `openclaw.chat` / `openclaw.chat.history` 等）：面向 OpenClaw 自家 wizard/setup 流程，不适合外部 adapter。
- **官方 SDK**：`@openclaw/gateway-client`（Node ≥22.19，含重连/设备身份/存储回调）与 `@openclaw/gateway-protocol`（schema/常量/错误读取器，tarball 内含 `protocol.schema.json` 机器可读契约）。**无 Python 版**；relay 用 Python 需按本文自研（核心面很小：握手 + req/res + 事件分发，§10.3 给了最小探针作雏形）。

---

## 8. 对接建议（LivaChat agent-relay OpenClawAdapter）

### 8.1 通道选择

结论：**根路径 WS 协议**，`client.id="gateway-client"`、`mode="backend"`、loopback + 共享 token、role operator、scopes `["operator.read","operator.write"]`（需要审批兜底再加 `operator.approvals`/`operator.questions`）。与 Hermes 适配器的分层一致（`adapters/base.py` 接口不变，`loader.py` 装配）。

### 8.2 连接模型：单连接 + 订阅（与 hermes 相反）

| 维度 | Hermes Adapter（现状） | OpenClawAdapter（建议） |
|------|----------------------|------------------------|
| 连接粒度 | **每 device 一条 WS**（`_DeviceConnection`）；session 崩溃随连接 | **每个 OpenClaw 实例一条 WS**，device 只体现在 sessionKey 上 |
| 会话归属 | device 级 hermes session（映射持久化 + `session.resume`） | 服务端持久 session（sqlite store），任意连接凭 key 读写；**无 resume/20s 窗口概念** |
| 断连后果 | 20s 内拿不回 → `session_lost` 快速失败 | 会话与 run 不受影响；重连后**重新订阅**（roster + 需要的 sessions）+ 用 `chat.history`/首帧快照补齐 |
| 事件分发 | 按 hermes session 路由（exactly-one-connection 语义） | 按**订阅**分发（同一 session 可多连接观察）；seq 空洞→重连重建 |
| 长任务 | `prompt.background` 两阶段派生 + `background.complete` | 常规 run 天然异步（runId）；结果=终态事件；无需派生 RPC |

因此 relay 侧：启动时懒建/常驻一条连接即可；连接故障的恢复成本远低于 hermes（重订阅即可），**不需要** hermes 那套 per-device LRU/退避风暴防护/启动恢复扫描（JobStore 的 device 维度补投逻辑仍需保留——那是 LivaChat 对自身设备的投递保证，与 OpenClaw 无关）。

### 8.3 会话映射

- OpenClaw 会话键：`agent:<agentId>:<key>`（实测本机有 `agent:main:main`、`agent:main:cron:<uuid>`）。建议 LivaChat device → `sessions.create {key: "<device_uuid>", agentId: <配置>}`，即全键 `agent:<agentId>:<device_uuid>`；再连同一 device 直接用同 key（`sessions.create` 同 key 为 adopt 语义，或先 `sessions.resolve`）。
- **不需要** hermes 的 device 映射持久化（映射本身就是服务端的 session 行）。
- LivaChat 多通对话复用同一 OpenClaw 会话 = 天然跨对话上下文（OpenClaw session 持久），与 relay v2"device 级长期代理关系"目标同构。
- 隔离性如果要求"每通 LivaChat session 独立"，则 key 换成 `device + livachat_session_id`，二选一在实现期定。

### 8.4 消息路径映射

| relay 能力 | OpenClaw 路径 |
|-----------|--------------|
| reactive `ask()`（一问一答） | `chat.send {sessionKey, message, idempotencyKey}` → 等 `chat` final（runId 关联）；或直接 `agent`+`agent.wait`（不建订阅，低配） |
| proactive `nudge()`（旁听介入） | chatlog 增量由 relay 自己 tail（与 hermes 相同，OpenClaw 不感知 LivaChat 的 chatlog）；触发时 `chat.send`（会话已订阅 `chat` 事件） |
| `analyze_recording()` | 同 nudge：relay 组装上下文经 `chat.send`/`agent` 投给 OpenClaw |
| 主动推送结果 | `chat` final 事件 → `PushPublisher` → `ai_text`（proactive=true），幂等键建议 `msg_agent_job_{job_key}` 同款 |

注意：`chat.send` 的 `idempotencyKey` 必填——建议用 LivaChat 侧的确定性键（如 `livachat:{job_key}`）防止 relay 重试造成双发。

### 8.5 长任务与无人值守

- OpenClaw 的 run 天然长（几十秒~分钟级），事件流即可全程观察；**没有** hermes 的"主 agent 轻量 + relay 派生 background"强制分层。
- 后台化仍然值得做（与 relay 现有 JobStore 语义对齐）：把 OpenClaw 的一次长 run 视为 relay 的一个 job（running → done/failed 从 `chat` 终态收敛），沿用 48h TTL 与补投管线。
- 无人值守兜底：订阅 `session.approval`（`includeApprovals`）或广播的 `exec.approval.requested`/`question.requested`，按策略 `exec.approval.resolve` / `question.resolve` 应答或 deny。策略上可先允许清单化（同 hermes 的 approval 白名单思路）。
- job 载体的选型（原生 subagent vs relay 自建 job 会话）与"创建/更改/取消"逐项映射见 **§9**。

### 8.6 待定问题（实现前需拍板）

1. **会话键粒度**：device 级长期会话 vs 每通 chat session（§8.3）；
2. **agentId 选取**：多 agent（实测本机有 main/stock/claude/…，default=main）——relay 配置固定 agent 还是按 device 映射；
3. **连接归属**：relay 进程内单连接与 OpenClaw 同机（loopback）；若未来 relay 与 OpenClaw 分机，需上 device identity + pairing 或 TLS/trusted-proxy（§2.2 退路）；
4. **`chat` 流消费面**：直接消费 `chat` 事件（推荐）并把 `session.*` 仅用于工具展示；
5. **审批兜底策略**：自动 deny 清单 vs 透传给 App 端（LivaChat 协议当前无审批 UI，倾向 deny + 日志）；
6. **job 载体**：relay 自建 job 会话（§9.3 路径 B，推荐）vs 原生 subagent（路径 A）；自建时是否挂 `parentSessionKey`/`spawnDepth` 谱系（可导航/UI 可见，代价是会话树语义）。

---

## 9. agent/subagent 模式与 job 生命周期（补充调研）

> 背景：hermes adaptor 用 `prompt.background` 承载 job（relay 两阶段派生 → `background.complete` → JobStore 收敛）。本节回答：OpenClaw 的 agent/subagent 模式能否承载 relay-facing job 的**创建 / 更改 / 取消**。

### 9.1 OpenClaw 的 subagent 体系

[官方文档] `docs/tools/subagents.md` 及 `docs/tools/subagents/{tool-reference,operations,nesting,announce,tool-policy,slash-command,thread-bound-sessions}.md`、`docs/gateway/config-tools/sessions-and-subagents.md`：

| 维度 | 事实 |
|------|------|
| 本质 | subagent = 从已有 agent run 派生的后台 run，**各自独立会话**（`agent:<agentId>:subagent:<uuid>`）；默认完成后把结果 **announce 回 requester**（父会话收到一个新 turn）；run 结束 ≠ session 结束 |
| 入口 | **模型工具** `sessions_spawn`（`task` 必填；`visible` / `context: isolated\|fork` / `model` / `runTimeoutSeconds` / `worktree` / `cleanup` / `expectsCompletionMessage` / `completionTarget:"parent"` 等）；配套工具 `subagents`（列表/等待/取消）、`sessions_yield`（让出 turn 等子结果）、`sessions_send`（steer/followup/notify 续投） |
| 等待/回传 | `sessions_yield` 暂停 requester → 子完成后 registry 唤醒父 turn；announce 链逐级上报；`expectsCompletionMessage:false` 为 fire-and-forget |
| 并发/配额 | 每 spawning session：执行并发 `maxConcurrent`=8、在册子数 `maxChildrenPerAgent`=5；嵌套深度上限 5；`runTimeoutSeconds` 默认 0（无超时） |
| 取消 | `subagents` 工具 `action:"cancel"`（runId，级联子孙）；网关侧等价见 §9.2 |
| 恢复 | 子 run 记录与完成交接持久化在共享 SQLite；Gateway 重启后被中断的子 agent 按正常完成路径收敛（结果注明 interrupted，**不自动重放**），由父 agent 决定续做什么 |
| 变体 | `runtime:"acp"` 把外部 harness（claude/gemini/opencode 等）作为子执行器 [官方文档 acp-agents]；swarm 收集器 `collect:true` 走独立并发车道（`tools.swarm.maxConcurrent`=32） |
| 本机实证 | [实测] `tools.effective {sessionKey:"agent:main:main"}`：`sessions_spawn`（"Spawn hidden subagent (ephemeral) or visible work session (durable)"）/ `subagents` / `sessions_yield` / `sessions_send` / `agents_wait` **都在 main agent 有效工具清单中**（`tools.catalog` 同证，defaultProfiles `["coding","messaging"]`）——本机 subagent 模式已启用 |

### 9.2 网关 RPC 面对照（relay 视角）

**结论：operator 面没有 spawn/subagent 专用 RPC**。[源码] 485 条核心方法全表核对：无任何 `*.spawn` / `*.subagent*` 方法；`worker.sessions.spawn`（`WorkerSessionsSpawnParamsSchema`）属云 worker 封闭协议，operator 不可用。

job 生命周期的 RPC 三件套（均为 `operator.write` 级，[源码] schema + [官方文档]）：

| 操作 | RPC | 要点 |
|------|-----|------|
| **创建** | `sessions.create` | `key` 自定义 + `idempotencyKey`；`parentSessionKey`+`spawnDepth` 建会话树谱系（schema 描述 "spawn-owned creations (visible subagent sessions)"，requires parentSessionKey，省略即 root depth 0）；亦可带 `message` 起首轮 |
| **发任务** | `chat.send` | ack `{runId, status:"started"}` = 确定性回执（等同 hermes `task_id`）；`idempotencyKey` 必填 |
| 简式 | `agent` + `agent.wait` | 见 §4.4；短 job 可不建订阅 |
| **更改** | `chat.send queueMode` / `sessions.send` / `sessions.patch` | `steer`=对运行中 run 注入修正、`followup`=排队续轮、`interrupt`=打断重开（§4.2）；`sessions.send {key, message}` 含"对 **paused** 子会话续投并保留原任务与完成受众"语义；`sessions.patch` 改元数据 |
| **取消** | `sessions.abort`；`chat.abort` | `{runId}` 精确取消该 run **及其子孙**（含未完成的完成交接）；`{key, clearQueued:true}` 会话级取消也请求级联子树并清 queued follow-up；`chat.abort` 不带 runId 不级联 |
| 发现/观察 | `sessions.list` / `sessions.messages.subscribe` / `chat` 事件 | 行含 `childSessions` / `hasActiveSubagentRun` / `spawnedBy`；任意子会话可订阅（read），`includeApprovals` 覆盖其子孙；`chat` 事件公共字段含 `spawnedBy` |

补充 schema（[源码] `sessions-DJqMAtE3.mjs`）：

```jsonc
// sessions.abort（key 与 runId 至少其一）
{ "key"?, "runId"?, "agentId"?, "clearQueued"? }
// sessions.send（投递进既有会话；无 queueMode）
{ "key", "agentId"?, "message", "thinking"?, "attachments"?, "timeoutMs"?, "idempotencyKey"? }
```

### 9.3 job 载体三条路径

**路径 A：原生 subagent 作 job 载体** —— 不适合 relay-facing job：

- 创建是间接的：relay 只能 prompt 主 agent 让其派生；回执（childSessionKey/runId）落在 agent 的 turn 里，relay 需解析 transcript 或轮询 `childSessions` 发现——**无确定性回执**（对比 hermes：relay 调 `prompt.background` 直接拿 task_id）。
- 完成是 announce 给父会话：relay 拿到的是父 agent 消化后的汇报（可能汇总多个子），非逐 job 终稿事件。
- 控制依赖发现：steer/取消都需要先拿到 child key/runId，发现不可靠则控制不可靠。
- 适用场景：让 OpenClaw **自治编排**（它自己管并发/嵌套/恢复），relay 只订阅主会话旁观。

**路径 B：relay 自建 job 会话（推荐，与 JobStore 同构）**：

```
创建: sessions.create {key:"relay-job-<job_key>", parentSessionKey:"agent:main:main", spawnDepth:1, idempotencyKey}
      → chat.send {sessionKey, message, idempotencyKey:"livachat:{job_key}"} → ack.runId 落 JobStore(running)
更改: chat.send queueMode=steer / followup；sessions.patch
取消: sessions.abort {runId}（级联该 job 自派的子孙）或 {key, clearQueued:true}
完成: 订阅该 job 会话的 chat 事件 → runId 关联 final/error → mark_done → 既有 48h TTL / 补投管线
```

与 hermes 的 relay-两阶段派生逐项对应（relay 派生、task 直达 relay、完成事件键控 job），且三处净增益：

| 维度 | hermes（现状） | OpenClaw 路径 B |
|------|---------------|----------------|
| 创建回执 | `prompt.background` → task_id | `chat.send` → runId（同等确定性） |
| 更改 | 不支持 | steer / followup / patch |
| 取消 | 不支持（仅看门狗超时收敛） | `sessions.abort {runId}` 精确级联 |
| 结果可靠性 | 依赖 session 存活 20s 窗口，错过即 failed | 会话与 transcript 服务端持久，relay 重启可 `chat.history` 幂等对账 |
| 并发控制 | hermes 侧上限 + relay | 普通会话不占 subagent 配额（无 OpenClaw 侧背压），需 relay 自限 |

**路径 B'（最简）**：短 job 直接 `agent` + `agent.wait`（或 `agent` + 订阅），取消 `sessions.abort {runId}`——代码最少，无会话谱系。

### 9.4 风险与待验证项

1. **Gateway 重启中断语义**：native subagent 有明确契约（收敛为 interrupted 结果、不重放）；普通会话 run 的同类契约未逐字确认，relay JobStore 看门狗保留为兜底。
2. **`spawnDepth`/`parentSessionKey` 对外部 operator 的接受度**：code check 允许（create 处理器透传，校验层未见拒绝，`sessions-create-BUKGSAQI.mjs:597` → `session-create-service-oAv-K6AM.mjs:959`），**未实测**（创建会话超出只读探针约定）；实现期首验。
3. **并发/配额**：relay 自建 job 不走 native subagent registry 配额（8 并发/会话、5 子在册、深度 5 均为 spawn 路径的），relay 需自设 job 并发上限。
4. **自动归档**：native 子会话默认 `archiveAfterMinutes`=60 自动归档；relay 自建会话不受此默认影响，但 `sessions.list` 查询需注意归档过滤。

---

## 10. 附录

### 10.1 实测握手样例（2026-10-14，本机 127.0.0.1:18789）

S1 服务端 challenge（连接后立即下发）：

```json
{"type":"event","event":"connect.challenge","payload":{"nonce":"e9ba7d82-…","ts":1791966300237,"capabilities":["model-catalog-snapshot"]}}
```

S2 connect 请求（relay 路径，**无 device 字段**）：

```json
{"type":"req","id":"connect-1","method":"connect","params":{
  "minProtocol":4,"maxProtocol":4,
  "client":{"id":"gateway-client","version":"0.1.0-probe","platform":"darwin","mode":"backend"},
  "role":"operator","scopes":["operator.read"],"caps":[],
  "auth":{"token":"<TOKEN>"},"locale":"en-US","userAgent":"livachat-probe/0.1"}}
```

S3 hello-ok（节选实际响应）：

```json
{"type":"res","id":"connect-1","ok":true,"payload":{
  "type":"hello-ok","protocol":4,
  "server":{"version":"2026.10.3","buildId":"2026.10.3-release-59cb44796b0f-2026-10-13T07-33-17.552Z","bootId":"4417ef32-…","connId":"e9201575-…","controlUiBuildSource":"bundled"},
  "features":{"methods":["health","status", … ,"sessions.messages.subscribe", …],
              "events":["connect.challenge","agent","chat", … ,"tick", …],
              "capabilities":["model-catalog-snapshot","profile-binding-v1","session-scoped-model-catalog", …]},
  "snapshot":{"suspension":{…},"presence":[…],"health":{…},"stateVersion":{…},"uptimeMs":5771138,
              "appliedConfigHash":"hmac-sha256:v1:TxKfIrA5…","sessionDefaults":{…},"updateSchedule":{…}},
  "auth":{"method":"token","role":"operator","scopes":["operator.read"],"recoveryScope":"RnxBpqA5…"},
  "policy":{"maxPayload":26214400,"maxBufferedBytes":52428800,"tickIntervalMs":30000,
            "attachments":{"maxBytes":19464192,"maxImageBytes":6291456},
            "allowedSessionVisibilities":["shared","read-only","suggest","draft"],
            "hasMultipleSessionSharingIdentities":false}}}
```

（`features.methods` 496 项、`features.events` 68 项，完整清单落 `/tmp/openclaw_probe_frames.jsonl`。）

### 10.2 实测 RPC / 事件 / 错误样例

```jsonc
// sessions.messages.subscribe ack
{"type":"res","id":"p2-…","ok":true,"payload":{"subscribed":true,"key":"agent:main:cron:8068412b-…","agentId":"main"}}

// chat.history（节选；content 为块数组，含 thinking 块）
{"ok":true,"payload":{"sessionKey":"agent:main:cron:8068412b-…","sessionId":"86cf8f3f-…","messages":[{"role":"assistant","content":[{"type":"thinking","thinking":"…"}]}]}}

// sessions.describe（节选行字段）
{"ok":true,"payload":{"session":{"key":"agent:main:cron:…","kind":"direct","classification":"cron","agentId":"main","displayName":"Automation: …","sessionId":"86cf8f3f-…","model":"deepseek-flash","thinkingDefault":"high","totalTokens":12107, …}}}

// tick 事件（30s 周期，连接级 seq）
{"type":"event","event":"tick","payload":{"ts":1791966380896},"seq":3}

// scope 拒绝（read-only 连接调 write 方法）
{"ok":false,"error":{"code":"FORBIDDEN","message":"missing scope: operator.write","details":{"code":"MISSING_SCOPE","missingScope":"operator.write","requiredScopes":["operator.write"]}}}

// token 错误
{"ok":false,"error":{"code":"INVALID_REQUEST","message":"unauthorized: gateway token mismatch (provide gateway auth token)","details":{"code":"AUTH_TOKEN_MISMATCH","authReason":"token_mismatch","canRetryWithDeviceToken":false,"recommendedNextStep":"update_auth_credentials"}}}
（随后 close 1008）

// 无 auth
{"ok":false,"error":{"code":"NOT_PAIRED","message":"device identity required","details":{"code":"DEVICE_IDENTITY_REQUIRED"}}}

// agent.wait 对不存在 run（write scope）
{"ok":true,"payload":{"runId":"probe-nonexistent-runid","status":"timeout"}}   // ~1.5s（timeoutMs=1500）后返回

// tools.effective（agent:main:main 节选）——subagent 工具面本机在效（§9.1）
{"id":"sessions_spawn","source":"core","label":"Sessions (sessions_spawn)","description":"Spawn subagent session.","rawDescription":"Spawn hidden subagent (ephemeral) or visible work session (durable). …"}
{"id":"subagents","source":"core","label":"Subagents","description":"List native subagents, wait for selected runIds, or cancel …"}
{"id":"sessions_yield", …} {"id":"sessions_send", …} {"id":"agents_wait", …}
```

### 10.3 探针脚本与参考源码位置

- 探针（只读，产物在 /tmp）：
  - `/tmp/openclaw_ws_probe.py` — 握手三态（token/坏 token/无 auth）+ read RPC + 4s 事件
  - `/tmp/openclaw_ws_probe2.py` — 40s 长连（tick/health 捕获）+ subscribe + chat.history + models.list
  - `/tmp/openclaw_ws_probe3.py` / `probe4.py` — 校验与拒绝路径（scope/未知方法/agent.wait 超时）
  - `/tmp/openclaw_probe5_tools.py` ~ `probe5d.py` — subagent 工具面实证（`tools.catalog` / `tools.effective`）
  - 原始帧：`/tmp/openclaw_probe_frames.jsonl`、`/tmp/openclaw_probe2_frames.jsonl`
- 官方协议手册（npm 包内，v2026.10.3 随包）：`/opt/node-v24.21.0-darwin-x64/lib/node_modules/openclaw/docs/gateway/protocol/{transport,handshake,auth,presence,rpc-methods,rpc-session-control,rpc-bootstrap-and-events,rpc-devices-nodes-and-approvals,rpc-system-and-channels,rpc-talk-config-and-agents,operator-methods,ledgers,versioning}.md`；客户端指南 `docs/gateway/clients.md`
- subagent 相关手册（同包内）：`docs/tools/subagents.md` 及 `docs/tools/subagents/{tool-reference,operations,nesting,announce,tool-policy,slash-command,thread-bound-sessions}.md`、`docs/gateway/config-tools/sessions-and-subagents.md`、`docs/concepts/subagent-yield-handoff.md`、`docs/tools/swarm.md`、`docs/tools/agent-send.md`
- 关键实现（dist，未混淆）：
  - 握手/挑战/帧发送：`dist/ws-connection-DDJOEJN-.mjs`（challenge 下发 :307-316）
  - connect 校验/免配对判定：`dist/message-handler-j5CWaBC4.mjs`（`evaluateMissingDeviceIdentity` :999，`verifyGatewayConnectDeviceProof` :909）；`dist/node-connect-reconcile-CV2jR04R.mjs`（`shouldSkipLocalBackendSelfPairing` / `shouldPreserveLocalCliSharedAuthScopes`）
  - 角色策略：`dist/role-policy-DjGeFe84.mjs`（`roleCanSkipDeviceIdentity`）
  - 方法目录与 scope：`dist/core-method-policy-CcBevufV.mjs`（485 条）、`dist/server-methods-list-BXiPO6FF.mjs`（事件名全表）
  - 参考客户端：`dist/client-BeP2LrQW.mjs`（`DEFAULT_GATEWAY_CLIENT_URL = "ws://127.0.0.1:18789"`，默认 client `gateway-client/backend/operator`）
  - schema：`dist/src-l2dWvEsN.mjs`（connect/agent/agent.wait/wake 等）、`dist/sessions-DJqMAtE3.mjs`（`ChatSendParamsSchema`、`ChatEventSchema`、`SessionsAbortParamsSchema`、`SessionsSendParamsSchema`）、`dist/chat-send-handler-Bw_pz541.mjs`（ack）
  - subagent / job 相关：`dist/sessions-spawn-tool-8P0qAJzx.mjs`（`sessions_spawn` 工具实现）、`dist/session-create-service-oAv-K6AM.mjs`（`spawnDepth`/`creation.via` 处理）、`dist/sessions-create-BUKGSAQI.mjs`（create RPC 处理器）、`dist/session-row-prepared-read-C1oDgGG5.mjs`（`hasActiveSubagentRun` 投影）

### 10.4 注意事项

1. **共享 token 等同 owner 级凭证**（HTTP OpenAI 面尤甚）；文档与代码中一律不落 token 明文（本文以 `<TOKEN>` 代替）。
2. 协议版本：当前 **v4**（operator 必须含 4）；N-1（v3）仅 node 角色窗口。升级 OpenClaw 时重测握手。
3. 不要复用一条连接做"每 device 一个逻辑信道"的状态机——事件按订阅分发、响应按 id 匹配且可乱序；连接级 `seq` 空洞必须按"重连+重订阅"处理。
4. `chat.send` 幂等键必填；重试前先对账（`chat.history`/`sessions.get`），别按 messageSeq 预测。
5. `tool-events` capability 不发就**静默收不到工具事件**；`includeApprovals` 不传就收不到审批事件。
6. 本机 gateway 为 launchd 常驻（`ai.openclaw.gateway`），重启由服务管理；relay 的 `1012 service restart` close 需当作正常重连触发（退避重连即可，无需清 session 状态）。
