# Hermes Dashboard WebSocket API 调研文档

> **调研对象**：Hermes Agent（NousResearch/hermes-agent）本地 Dashboard 服务
> **实测环境**：`http://127.0.0.1:9119`，Hermes **v0.20.2**（release 2026.8.16），`hermes serve` headless + 预置 `HERMES_DASHBOARD_SESSION_TOKEN`，`auth_required=false`（loopback 模式）
> **用途**：LivaChat `agent-relay` 服务通过该协议对接 Hermes Agent（对话交互 + 运维管理 + 后台 job）
> **协议事实来源**：本机安装源码 `~/.hermes/hermes-agent/`（`hermes_cli/web_server.py`、`tui_gateway/ws.py`、`tui_gateway/server.py`、`tools/delegate_tool.py`、`tools/async_delegation.py`），所有关键行为均已对运行中的实例实测验证。
> **调研日期**：2026-07-27（初版，v0.18.2）；2026-08-07 升级 v0.20.0 后复核，协议核心未变；**2026-10-17 v0.20.2 复核并新增 §9（background / agent / subagent 与 job 生命周期，真机探针）**
> **版本注记**：§1-§9 的实测基于本机仍在运行的 v0.20.2；同日对 `~/projects/github/hermes-agent` 最新 main（v0.22.2+canary）做了**静态代码审查**，差异见 §10。**本机未升级**，§10 未做真机验证。

---

## 1. 总览

Hermes Dashboard 由 `hermes dashboard` 启动（FastAPI + uvicorn），在同一个端口上同时提供：

| 类型 | 端点 | 用途 |
|------|------|------|
| **WebSocket** | `/api/ws` | **JSON-RPC 2.0 主协议**：会话管理、对话交互（流式）、审批响应。**对接核心** |
| **WebSocket** | `/api/console` | **管理控制台**：结构化 JSON 帧执行 `hermes` CLI 管理命令（config/cron/kanban/skills…） |
| **WebSocket** | `/api/pty` | PTY 终端桥：spawn `hermes --tui`，ANSI 字节流（dashboard Chat 页 xterm.js 用） |
| **WebSocket** | `/api/events` | 事件订阅（按 channel 广播，dashboard 侧边栏工具调用流） |
| **WebSocket** | `/api/pub` | 事件发布（PTY 内 TUI 子进程 → dashboard，内部用） |
| **WebSocket** | `/api/audio/speak-stream` | **流式 TTS**（v0.20.0 新增）：文本帧进、raw int16 PCM 帧出，按句切割流式合成（desktop 语音用） |
| HTTP REST | `/api/*` | 管理面 REST（sessions、cron、skills、config、gateway 控制等，见 §7） |

> 另有独立的 **OpenAI 兼容 HTTP API**（`gateway/platforms/api_server.py`，默认 :8642，`/v1/chat/completions`、`/api/sessions/{id}/chat/stream` SSE），属于 gateway 平台进程，**不在 dashboard 端口上**，本文不展开；需要时作为备选对接通道评估。

### 对接选型建议（先说结论）

| 需求 | 推荐通道 |
|------|---------|
| 与 Hermes Agent 进行**对话交互**（发 prompt、收流式回复、处理审批） | **`/api/ws` JSON-RPC** |
| **运维管理**（改配置、管 cron/skills/kanban、备份、doctor） | **`/api/console`**（或 §7 的 REST） |
| 订阅 dashboard 已建立会话的工具调用事件 | `/api/events` |
| **后台 agent job（create / cancel / 追加 prompt / 状态查询）** | **每 job 一个独立 Hermes 会话（`session.create` + `prompt.submit`）**，见 §9.3 |
| 完整终端体验（slash 命令 TUI） | `/api/pty`（一般不需要） |
| 流式语音合成（TTS） | `/api/audio/speak-stream`（v0.20.0 新增） |

---

## 2. 认证与连接安全

### 2.1 凭证类型

dashboard 有两种认证模式，由启动方式决定（`/api/status` 的 `auth_required` 字段可查）：

| 模式 | 触发条件 | WS 凭证 |
|------|---------|---------|
| **loopback / insecure**（当前部署） | 绑定 127.0.0.1 或 `--insecure` | `?token=<SESSION_TOKEN>` |
| **gated** | 公网绑定且未加 `--insecure`（`auth_required=true`） | `?ticket=<一次性票据>`（30s TTL，由 `POST /api/auth/ws-ticket` 用 cookie 换取）；`?token=` 在 gated 模式下**一律拒绝** |

**SESSION_TOKEN 的来源**（loopback 模式）：

- 进程启动时生成：`HERMES_DASHBOARD_SESSION_TOKEN` 环境变量优先，否则 `secrets.token_urlsafe(32)` 随机生成，**每次重启变化**。
- 注入在 dashboard 首页 HTML 的 `<script>` 中：
  ```html
  <script>window.__HERMES_SESSION_TOKEN__="42v6m4AbsWopenlcWY9BGB--75boIrpG_IB2cvmeMTU"; ...</script>
  ```
- 程序化获取方式：`GET http://127.0.0.1:9119/` 解析该变量（当前实测值即上例，重启后失效）。

**HTTP REST 认证**（§7 使用）：以下两种头均可（实测验证）：

```
X-Hermes-Session-Token: <SESSION_TOKEN>
Authorization: Bearer <SESSION_TOKEN>
```

公开（免认证）REST 路径仅有：`/api/status`、`/api/config/defaults`、`/api/config/schema`、`/api/model/info`、`/api/dashboard/themes`、`/api/dashboard/plugins`、`/api/cron/fire`。

### 2.2 连接期安全门（WS 握手阶段）

每个 WS 端点在 `accept()` 之前按顺序检查（失败即在握手阶段拒绝，客户端表现为 **HTTP 403**，握手后被踢则收到对应 close code）：

| 检查 | 说明 | close code |
|------|------|-----------|
| embedded chat 开关 | 常量开启，正常不会触发 | 4403/4404 |
| 认证（`_ws_auth_reason`） | token 恒定时间比较；拒绝原因：`no_credential` / `token_mismatch` / `ticket_invalid` | **4401** |
| Host/Origin（`_ws_host_origin_reason`） | Host 必须匹配绑定地址；Origin 为 http(s) 时其 netloc 必须匹配绑定地址。**非浏览器客户端不带 Origin 头可直接通过** | **4403** |
| 对端 IP（`_ws_client_reason`） | loopback 绑定 + 免认证时仅允许 loopback 对端 | **4408** |
| channel 参数合法性（仅 `/api/events`、`/api/pub`、`/api/pty`） | 须匹配 `^[A-Za-z0-9._-]{1,128}$` | **4400** |

> **对 LivaChat 的意义**：agent-relay 是 Python 后端（非浏览器），不带 `Origin` 头，只要携带正确 `?token=` 且从 loopback 连接即可通过全部检查。

### 2.3 `hermes serve`（headless 后端）的 token 获取

`hermes serve` 是与 `hermes dashboard` 并行的 **headless 模式**：同一套 JSON-RPC/WS/REST API，但**不构建、不挂载 SPA**（`HERMES_SERVE_HEADLESS=1`），因此**没有 index.html 注入 token 的通道**。启动时仅打印绑定信息，**不打日志、不写 ready 文件暴露 token**：

```
HERMES_BACKEND_READY port=19199          ← stdout 哨兵（仅端口，供进程管理器解析）
  Hermes backend listening on 127.0.0.1:19199
```

`GET /` 只返回 404 JSON（实测）：

```json
{"error": "Headless backend (hermes serve): web UI disabled — use `hermes dashboard` for the browser UI."}
```

**正确方式：调用方自己铸造 token，通过环境变量注入后再启动**（源码 `web_server.py:279`，Hermes Desktop App 即此模式 —— 见 `apps/desktop/electron/main.ts` spawn 子进程时传入 `HERMES_DASHBOARD_SESSION_TOKEN`）：

```bash
# 方式一：临时环境变量（每次启动可不同）
HERMES_DASHBOARD_SESSION_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  hermes serve --host 127.0.0.1 --port 9119 --no-open

# 方式二：写入 ~/.hermes/.env 持久化（启动时经 python-dotenv 加载进 os.environ）
echo 'HERMES_DASHBOARD_SESSION_TOKEN=<your-token>' >> ~/.hermes/.env
hermes serve --host 127.0.0.1 --port 9119 --no-open
```

已实测验证（`hermes serve` + 预置 `HERMES_DASHBOARD_SESSION_TOKEN=test-token-abc123`）：

| 检查 | 结果 |
|------|------|
| `GET /api/status` 无凭证 | 200（公开） |
| `GET /` | 404 JSON，无 token 泄漏 |
| `GET /api/config` 带头 `X-Hermes-Session-Token: test-token-abc123` | 200 |
| `GET /api/config` 无凭证 | 401 |
| `WS /api/ws?token=test-token-abc123` | `gateway.ready` + `session.create` 成功 |
| `WS /api/ws?token=wrong` | 握手 HTTP 403 |

配套机制：

- **端口发现**：stdout 哨兵 `HERMES_BACKEND_READY port=<port>`（`--port 0` 时 OS 分配临时端口）；或设 `HERMES_DESKTOP_READY_FILE=<path>`，启动后原子写入 JSON `{"port": <port>}`。
- **shell 环境变量优先于 `.env`**（python-dotenv `override=False`），可用 shell 临时覆盖。
- **token 随进程失效**：与 dashboard 模式一样；未预置 env 时随机生成且无任何导出通道，实际等于"仅本机同进程可信"——headless 部署务必显式预置。
- gated 模式（非 loopback 绑定，`auth_required=true`）下 `?token=` 对 WS **一律拒绝**，需走 OAuth/password provider + `POST /api/auth/ws-ticket` 换 `?ticket=`；`serve` 典型用法是 loopback，不展开。

---

## 3. `/api/ws` — JSON-RPC 2.0 主协议（对接核心）

实现：`tui_gateway/ws.py`（传输）+ `tui_gateway/server.py`（方法分派）。与 Hermes 桌面 App、TUI（Ink over stdio）**共用同一套 dispatch**，即 TUI 的全部能力都可通过 WS 使用。

### 3.1 传输规则

- 每个 **WS 文本帧** 承载一个 JSON 对象（newline-delimited JSON-RPC 风格，无额外分帧）。
- 服务端对每个出站流式 token 帧做 ~30fps 合批（`message.delta`/`reasoning.delta`/`thinking.delta`），非流式帧（响应、审批、工具事件）会冲刷缓冲、保证顺序。
- 出站已禁 Nagle（TCP_NODELAY）。
- 断连行为：断开后服务端按 `close_on_disconnect` 决定回收或保留会话（保留时有 ~20s 孤儿回收宽限，`HERMES_TUI_WS_ORPHAN_REAP_GRACE_S` 可调），快速重连可 `session.resume` 恢复。

### 3.2 帧格式

**请求（客户端 → 服务端）**：

```json
{"jsonrpc": "2.0", "id": "w1", "method": "session.create", "params": {"close_on_disconnect": true}}
```

- `id` 客户端自定，字符串/数字均可；dashboard 前端用 `w<n>` 前缀。

**响应（服务端 → 客户端）**：

```json
{"jsonrpc": "2.0", "id": "w1", "result": { ... }}
{"jsonrpc": "2.0", "id": "w1", "error": {"code": 4009, "message": "session busy"}}
```

**事件（服务端 → 客户端，无 id）**：

```json
{"jsonrpc": "2.0", "method": "event", "params": {"type": "message.delta", "session_id": "889ba3a4", "payload": {"text": "PON"}}}
```

- 连接建立后服务端**立即**推送 `gateway.ready`：
  ```json
  {"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready", "payload": {"skin": {...}}}}
  ```

### 3.3 典型对话生命周期（已实测）

```
client → session.create          ⇒  {session_id, stored_session_id, info{model, cwd, branch, ...}}
client ← event session.info      (agent 异步构建完成后推送)
client → prompt.submit           ⇒  {"status": "streaming"}        (立即返回)
client ← event message.start
client ← event thinking.delta*   (可选)
client ← event reasoning.delta*  (可选)
client ← event tool.start / tool.complete*  (可选)
client ← event message.delta*    (正文 token 流)
client ← event message.complete  {text, usage, status}
client → session.close           ⇒  {"closed": true}
```

实测记录（2026-07-27，prompt="Reply with exactly one word: PONG"）：

```
session.create → {"session_id": "889ba3a4", "stored_session_id": "20260727_164513_3442a3", ...}
prompt.submit  → {"status": "streaming"}
事件序列: session.info ×1, message.start ×1, thinking.delta ×3,
         reasoning.delta ×36, message.delta ×2, reasoning.available ×1, message.complete ×1
message.complete.payload.text = "PONG"
```

### 3.4 关键 RPC 方法（会话/对话）

#### `session.create`

| 参数 | 类型 | 说明 |
|------|------|------|
| `close_on_disconnect` | bool | true 时 WS 断开即回收会话；false 时保留可 resume |
| `source` | string | 会话来源标记，如 `"tool"`、`"tui"` |
| `title` | string | 预置标题 |
| `messages` | array | 种子历史（`[{role, content}]`） |
| `parent_session_id` | string | 从既有会话分支 |
| `cwd` | string | 工作目录（须真实存在才记为显式 workspace） |
| `profile` | string | 目标 profile（多 profile 场景） |
| `model` / `provider` | string | 会话级模型覆盖（不写全局配置） |
| `reasoning_effort` | string | 推理强度覆盖 |
| `fast` | bool | true → service_tier=priority |
| `cols` | int | 渲染宽度，默认 80 |

返回：

```json
{
  "session_id": "889ba3a4",
  "stored_session_id": "20260727_164513_3442a3",
  "message_count": 0,
  "messages": [],
  "info": {"model": "deepseek-v4-pro", "tools": {}, "skills": {},
           "cwd": "...", "branch": "...", "lazy": true,
           "desktop_contract": ..., "profile_name": "default"}
}
```

> `session_id`（8 位 hex）是 WS 侧句柄；`stored_session_id`（`yyyymmdd_HHMMSS_xxxxxx`）是持久化 ID，用于 REST/DB/`session.resume`。Agent 在首个 prompt 时惰性构建。

#### `prompt.submit`

| 参数 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 必填 |
| `text` | string | 用户消息 |
| `truncate_before_user_ordinal` | int? | 可选：截断到第 N 条用户消息之前（编辑重发） |

返回 `{"status": "streaming"}`；turn 进行中重复提交会**排队**（并默认打断当前 turn），不拒绝。

#### 其余会话方法

| 方法 | 说明 |
|------|------|
| `session.interrupt` | 打断当前 turn（cooperative），参数 `{session_id}` |
| `session.steer` | 不打断 turn，向下一批 tool result 注入用户消息 `{session_id, text}` |
| `session.redirect` | **（v0.20.0 新增）** 重定向当前进行中的模型 turn，保留有效工作/上下文 `{session_id, text}` |
| `session.workspace.move` | **（v0.20.0 新增）** 按 `session_key` 将已持久化会话的 workspace 迁移到另一文件夹/project（无需 live agent，可修复错目录创建的会话） |
| `session.close` | 关闭会话 `{session_id}` → `{"closed": bool}` |
| `session.resume` | 恢复持久化会话 `{session_id: <stored_id 或 title>, lazy?, cols?, profile?}` |
| `session.list` | 列出持久化会话（实测返回 id/title/preview/started_at/message_count/source） |
| `session.history` | 会话消息历史 |
| `session.title` | 改标题 |
| `session.branch` | 分支当前会话 |
| `session.save` / `session.delete` / `session.undo` / `session.compress` | 保存/删除/撤销/压缩上下文 |
| `session.usage` / `session.status` / `session.context_breakdown` | token 用量/状态/上下文构成 |
| `session.most_recent` / `session.active_list` / `session.activate` / `session.cwd.set` | 其他会话操作 |
| `prompt.background` | 后台跑一个独立 turn，完成时推 `background.complete` |
| `config.get` / `config.set` / `config.show` | 配置读写（`config.set` 支持会话级 model 切换） |
| `model.options` / `model.disconnect` / `model.save_key` | 模型目录/连接/凭据 |
| `tools.list` / `tools.show` / `tools.configure` / `toolsets.list` | 工具与工具集 |
| `skills.manage` / `skills.reload` | 技能管理 |
| `slash.exec` / `command.dispatch` / `commands.catalog` | 执行 slash 命令 |
| `shell.exec` / `cli.exec` | 执行 shell / `hermes` CLI（`cli.exec` 参数 `{argv: [...], timeout?}`，返回 `{code, output}`） |
| `image.attach` / `image.attach_bytes` / `pdf.attach` / `file.attach` | 附件上传（多模态 prompt） |
| `llm.oneshot` | 单次 LLM 调用 |
| `approval.respond` / `clarify.respond` / `sudo.respond` / `secret.respond` | 审批/交互响应（见 §3.6） |
| `complete.path` / `complete.slash` | 自动补全 |
| `process.list` / `process.kill` / `process.stop` | 子进程管理 |
| `delegation.status` / `delegation.pause` / `subagent.interrupt` / `subagent.steer` | 原生 subagent **控制面**（状态/暂停/打断/追加），但**没有创建入口**，见 §9.2 |
| `agents.list` | **不是 agent 列表**：返回 process registry 的后台进程（`methods_tools.py:1652`） |
| `spawn_tree.save/list/load` | TUI spawn-tree 快照的保存/列出/读取（展示用，非创建；`methods_session.py:3261`） |
| `message.react` | **（v0.20.0 新增）** 设置/清除持久化消息的 emoji reaction（iOS Tapback 语义：每作者每消息一个，重复发送同一 emoji 即撤回） |
| `voice.toggle` / `voice.record` / `voice.tts` | **（v0.20.0 新增）** 语音开关/录音/本地 TTS |
| `wake.start` / `wake.stop` / `wake.pause` / `wake.resume` / `wake.status` / `wake.feed` | **（v0.20.0 新增）** 唤醒词检测控制与喂入 |
| `cron.manage` / `plugins.manage` / `browser.manage` | 管理类 |

完整方法表（v0.20.2 源码 `@method(...)` 注册共 **154** 个；v0.20.0 实测为 134 个；初版 v0.18.2 约 120 个）见 `tui_gateway/server.py` 及 `methods_*.py`。现有对接依赖的方法（`session.*`、`prompt.*`、`approval.respond` 等）在升级后全部保留，协议核心未变。

> **重要的边界**：`delegate_task`（原生 subagent）是**模型工具**，不是 JSON-RPC 方法——方法表中**没有任何 `*.spawn` / `agent.spawn` / `subagent.spawn`**。`delegation.*` / `subagent.*` 只是控制面。`prompt.background` 与原生 subagent 是两套互不相通的后台机制，详见 §9。

### 3.5 事件目录（`params.type`）

对话流核心事件：

| 事件 | payload | 说明 |
|------|---------|------|
| `gateway.ready` | `{skin}` | 连接建立即推，无 session_id |
| `session.info` | `{model, tools, skills, cwd, branch, ...}` | agent 构建完成/信息变更 |
| `session.title` | `{title}` | 自动标题生成 |
| `message.start` | （无） | 一条 assistant 回复开始 |
| `message.delta` | `{text, rendered?}` | 正文 token（合批 ~30fps） |
| `thinking.delta` | `{text}` | thinking 块 token |
| `reasoning.delta` | `{text, verbose?}` | 推理内容 token |
| `reasoning.available` | — | 推理内容可取 |
| `message.complete` | `{text, usage, status, reasoning?, warning?, rendered?}` | 回复完成；`status` 为 `complete`/`interrupted` 等 |
| `tool.start` | `{tool_id, name, args}` | 工具调用开始 |
| `tool.complete` | `{tool_id, name, args, result, duration_s?}` | 工具调用完成 |
| `tool.generating` | `{name}` | 工具调用参数生成中 |
| `tool.output_risk` | — | 工具输出风险提示 |
| `status.update` | `{kind, text}` | 状态行（kind: status/compacting/...） |
| `approval.request` | `{command?, choices, smart_denied?, allow_permanent?}` | **需客户端应答**（见 §3.6） |
| `clarify.request` | `{question, choices, request_id}` | agent 提问澄清，**阻塞等应答**；`request_id` 是应答的必需关联键 |
| `sudo.request` / `secret.request` | — | 凭据请求，**阻塞等应答** |
| `terminal.read.request` | `{start?, count?}` | read_terminal 工具（GUI 专用） |
| `background.complete` | `{task_id, text}` | `prompt.background` 完成（该机制无其它事件，见 §9.1） |
| `subagent.start` | `{goal, task_count, task_index, subagent_id, child_session_id, depth, model, tool_count, toolsets, text}` | 原生子代理启动（§9.2） |
| `subagent.thinking` / `subagent.text` / `subagent.tool` / `subagent.progress` | 在 start/complete 的公共字段上追加 `text` / `tool_name` / `tool_preview` | 子代理流式活动；`subagent.text` 不发给父会话，只镜像给 child watch session |
| `subagent.complete` | 公共字段 + `{status, summary, text, duration_seconds, input_tokens, output_tokens, reasoning_tokens, api_calls, files_read, files_written, output_tail}` | 子代理终态；`status` + `summary`/`text`（有截断，见 §9.2.2） |
| `status.update`（`kind="process"`） | `{kind, text}` | 异步委派完成后注入父会话前的通知（text 为 `[ASYNC DELEGATION BATCH COMPLETE — deleg_…]` 结构化文本，见 §9.2） |
| `notification.show` / `notification.clear` | `{text, level, kind, ttl_ms, key, id}` / `{key}` | 通知条 |
| `error` | `{message}` | 错误（如 agent init 失败） |
| `reaction` | `{kind}` | 彩蛋（hearts） |
| `moa.aggregating` / `moa.reference` / `review.summary` / `browser.progress` / `voice.*` / `pet.*` 等 | — | 专项功能 |

> 事件路由注意：事件按 `session_id` 投递到**该 session 当前绑定的 transport**（最后一次为该 session 发请求的 WS 连接）。多连接并发操作同一会话时需注意事件只会去其中一个连接。

### 3.6 审批与交互响应

agent 执行高危工具前会推 `approval.request` 并**阻塞**等待：

```json
// server → client
{"method": "event", "params": {"type": "approval.request", "session_id": "...",
  "payload": {"command": "rm -rf ...", "choices": ["once", "session", "always", "deny"]}}}

// client → server
{"jsonrpc": "2.0", "id": "w9", "method": "approval.respond",
 "params": {"session_id": "...", "choice": "once", "all": false}}
```

| 响应方法 | 参数 | 说明 |
|---------|------|------|
| `approval.respond` | `{session_id, choice: "once"\|"session"\|"always"\|"deny", all?}` | `all=true` 解决该 session 全部待审批 |
| `clarify.respond` | `{session_id, answer, request_id}` | 回答澄清问题。**v0.20.2 必须带 `clarify.request` 的 `request_id`**：缺失时返回 4009/no pending，阻塞不会解除（源码 `_respond`，2026-10-17 真机验证）；恢复场景从 `session.resume` 的 `pending_clarify.request_id` 取回 |
| `sudo.respond` | `{session_id, password}` | 提供 sudo 密码 |
| `secret.respond` | `{session_id, value}` | 提供密钥值 |

> **无人值守对接建议**：relay 场景没有人类在场，应在 Hermes 侧配置 toolsets/审批策略（或选用 `source` 标记配合配置），并对 `approval.request` 实现自动 deny 兜底，否则 turn 会一直阻塞。

### 3.7 错误码

JSON-RPC 标准层：`-32700` parse error、`-32600` invalid request、`-32601` unknown method、`-32603` internal error、`-32000` handler error。

业务层（节选，4xxx 参数/状态类，5xxx 服务端类）：

| code | 含义 |
|------|------|
| 4002 | 缺参数（如 model value required） |
| 4006 | session_id required |
| 4009 | session busy / subagent still running |
| 4018 | truncate ordinal 越界 |
| 4090 | 活跃会话数达上限（`prompt.submit` 时返回；`session.create` 不占槽，源码 `methods_prompt.py:316`） |
| 5004 | approval 处理失败 |
| 5032 | agent 初始化超时/失败 |

---

## 4. `/api/console` — 管理控制台协议

实现：`hermes_cli/console_engine.py`。在 dashboard 进程内执行**策展过的** `hermes` CLI 管理命令（不 spawn shell），是**未来管理能力**的主要 WS 通道。

### 4.1 帧格式

连接即推 ready：

```json
{"type": "ready", "context": "local", "profile": "current", "prompt": "hermes> "}
```

客户端帧：

| 帧 | 说明 |
|----|------|
| `{"type": "input", "line": "cron list"}` | 执行一行命令（`"command"` + `"command"` 字段亦可） |
| `{"type": "ping"}` | 心跳 → `pong` |
| `{"type": "cancel"}` | 取消运行中命令 / 撤销待确认 |
| `{"type": "confirm", "command": "<原命令>"}` | 确认执行 mutating 命令（command 必须与待确认命令一致） |

服务端帧：

| 帧 | 说明 |
|----|------|
| `{"type": "output", "id": n, "stream": "stdout", "data": "...", "command": "..."}` | 命令输出 |
| `{"type": "complete", "id": n, "status": "ok"\|"error"\|"timeout"\|"cancelled"\|"idle"\|"confirm_required", "command": "...", "prompt": "hermes> "}` | 命令终态 |
| `{"type": "error", "message": "...", "prompt": ...}` | 错误（协议级/执行级） |
| `{"type": "confirm_required", "id": n, "command": "...", "message": "Run `hermes ...`?"}` | 该命令需确认 |
| `{"type": "pong"}` | 心跳应答 |

规则：单命令超时 60s；输出上限 50000 字符；同时只允许一条命令在跑；`exit` 状态会以 1000 关闭连接。

### 4.2 mutating 命令确认流（已实测）

```
client → {"type": "input", "line": "bundles reload"}
server ← {"type": "confirm_required", "id": 1, "command": "bundles reload", "message": "Run `hermes bundles reload`?"}
server ← {"type": "complete", "id": 1, "status": "confirm_required", ...}
client → {"type": "confirm", "command": "bundles reload"}   // 或 {"type": "cancel"}
```

> relay 自动执行 mutating 命令时：发送 input → 若收到 `confirm_required` → 回 `confirm`（带原命令串）。

### 4.3 命令家族（实测 `help` 输出，节选）

help 中带 `*` 前缀的为 mutating 命令（需 confirm）：

| 家族 | 举例 |
|------|------|
| auth | `auth status/list/add/remove/logout/reset` |
| config | `config show/check/set/migrate/path/env-path` |
| cron | `cron list/create/edit/pause/resume/remove/run/status/tick` |
| skills | `skills list/...`（含 hub install/scan/search） |
| kanban | `kanban list/show/create/assign/complete/archive/boards.*/...` |
| curator | `curator status/run/pause/resume/pin/prune/rollback` |
| checkpoints | `checkpoints status/list/prune/clear` |
| bundles | `bundles list/show/create/delete/reload` |
| hooks | `hooks list/doctor/test/revoke` |
| project | `project list/show/create/use/...` |
| portal | `portal ...` |
| 运维 | `doctor`、`dump`、`backup`、`import`、`insights`、`debug share/delete` |

完整列表：连上后发 `help` 获取（约 14KB，随版本变化）。

---

## 5. `/api/pty` — 终端桥（参考）

spawn `hermes --tui` 于 POSIX PTY，dashboard Chat 页用 xterm.js 渲染。**对 relay 对接一般不需要**，仅记录：

- Query 参数：`channel`（事件广播通道）、`resume`（stored_session_id）、`fresh=1`（强制新会话）、`attach=<token>`（keep-alive，断开 30min 内可重挂，最多 16 个）、`profile`。
- 下行：二进制帧（ANSI 字节流）；上行：文本/二进制输入；窗口调整用转义序列 `\x1b[RESIZE:<cols>;<rows>]`。
- 子进程 EOF 或 WS 断开（无 attach）即回收。

## 6. `/api/events` + `/api/pub` — 事件广播（参考）

- `/api/events?channel=<id>`：**订阅**。订阅方不发言，连接保持即收 `{method: "event", params: {type, payload}}` 帧（与 `/api/ws` 事件同构，如 `session.info`、`dashboard.new_session_requested`、工具调用事件）。
- `/api/pub?channel=<id>`：**发布**。dashboard 内部使用（PTY 内 TUI 子进程经 `HERMES_TUI_SIDECAR_URL` 注入连入），写入的每一帧文本广播给同 channel 全部订阅者。
- channel 正则：`^[A-Za-z0-9._-]{1,128}$`。

> 用途：当用户同时在 dashboard Chat 页操作同一会话时，relay 可用它观察该会话的工具事件流。

---

## 7. REST API 补充（管理面）

均已在实测环境验证（认证头见 §2.1）。管理类端点节选：

| 端点 | 说明 |
|------|------|
| `GET /api/status` | 版本、gateway 状态、活跃会话数、profiles（**公开**） |
| `GET /api/config` / `GET /api/config/raw` / `POST /api/config/...` | 配置读写 |
| `GET /api/sessions` (`?limit=&offset=&profile=&sort=`) | 会话列表（含 stored id、end_reason 等） |
| `GET/POST /api/sessions/...`（`search/stats/prune/bulk-delete/empty`） | 会话管理 |
| `GET /api/cron/jobs`，`POST /api/cron/jobs`，`POST /api/cron/jobs/{id}/pause|resume|trigger` | 定时任务 |
| `GET /api/skills`，`POST /api/skills/toggle`，`/api/skills/hub/*` | 技能 |
| `GET /api/tools/toolsets` | 工具集及启用状态 |
| `GET /api/system/stats` | 主机/Python/Hermes 版本信息 |
| `GET /api/model/info` / `GET /api/model/options` / `POST /api/model/set` | 模型 |
| `POST /api/gateway/start|stop|restart`，`GET /api/gateway` | gateway 进程控制 |
| `GET /api/logs` | 日志 |
| `GET/POST /api/ops/doctor|dump|backup|checkpoints|...` | 运维 |
| `GET /api/memory`，`GET /api/mcp/servers`，`GET /api/profiles`，`GET /api/webhooks` | 记忆/MCP/profile/webhook |
| `POST /api/hermes/update`，`GET /api/hermes/update/check` | 自更新 |

> 前端 bundle 中出现的完整路径清单：`api/analytics/*, api/credentials/pool/*, api/curator/*, api/dashboard/*, api/env/*, api/events, api/files/*, api/messaging/*, api/pairing/*, api/providers/oauth/* 等`。REST 无统一 Schema 文档，以前端调用与源码为准。

---

## 8. 对接建议（LivaChat agent-relay）

### 8.1 通道选择

现有 `HermesAdapter`（`services/agent_relay/adapters/hermes.py`）按 HTTP/SSE（`POST /api/chat`）设计——该端点在 dashboard 上**并不存在**（mock server 模拟的是假定 API）。基于本次调研，真实可用的对话通道有三个候选：

| 候选 | 位置 | 协议 | 评价 |
|------|------|------|------|
| **`/api/ws`（本文）** | dashboard :9119 | JSON-RPC over WS | **推荐**：desktop/TUI 同款协议，流式事件丰富（delta/tool/approval），会话管理完整，单连接多路复用 |
| OpenAI API server | gateway :8642 | HTTP/SSE（OpenAI 兼容） | 需启动 gateway 平台（当前 stopped），协议简单但能力面窄 |
| `/api/console` | dashboard :9119 | JSON 帧 | 管理命令专用，不适合对话 |

### 8.2 `HermesWsAdapter` 设计要点

1. **连接管理**：单条 `/api/ws` 长连承载全部会话（request id 多路复用）；token 从 `GET /` 的 `__HERMES_SESSION_TOKEN__` 解析并缓存，401/403 时刷新。
2. **会话映射**：LivaChat session ↔ Hermes `session_id`；用 `close_on_disconnect=false` + `session.resume` 抗断连；relay 重启后用 `stored_session_id` 恢复。
3. **utterance → prompt**：`prompt.submit` 后消费事件流，`message.delta` 增量转 `ai_text`（或等 `message.complete` 整段下发）；`usage` 记 metric。
4. **工具可见性**：`tool.start/tool.complete` 可映射为 LivaChat 的工具事件或直接忽略。
5. **审批兜底**：无人在场，必须实现 `approval.request → approval.respond(deny)` 自动兜底（或预先在 Hermes 配置收窄 toolsets/审批策略），否则 turn 永久阻塞。
6. **超时与打断**：turn 超时用 `session.interrupt`；`prompt.submit` 在 turn 中默认返回 `redirected`（重定向当前 turn，`display.busy_input_mode` 可配 `queue`/`steer`），不是无条件排队；要排队需显式 `queued:true`（§9.3.1）。
7. **管理面**：cron/skills/配置等用 `/api/console`（或 REST）实现，与对话链路解耦。
8. **job 载体（2026-10-17 补充）**：现有 `prompt.background` 不支持 cancel / 追加 prompt / 状态查询（§9.1 真机实测）。原生 subagent 有完整控制面但**没有 RPC 创建入口**（§9.2）。要同时满足创建/追加/取消/状态，推荐 **每 job 一个独立 Hermes 会话**（§9.3/§9.4），而不是继续扩展 `prompt.background`。

### 8.3 最小可用 Python 客户端（实测可运行）

```python
import asyncio, json, re, urllib.request, websockets

BASE = "http://127.0.0.1:9119"
WS = "ws://127.0.0.1:9119"

def get_token() -> str:
    html = urllib.request.urlopen(f"{BASE}/").read().decode()
    return re.search(r'__HERMES_SESSION_TOKEN__="([^"]+)"', html).group(1)

async def chat(text: str) -> str:
    async with websockets.connect(f"{WS}/api/ws?token={get_token()}") as ws:
        await ws.recv()  # gateway.ready
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": "c1", "method": "session.create",
                                  "params": {"close_on_disconnect": True, "source": "tool"}}))
        sid = None
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("id") == "c1":
                sid = msg["result"]["session_id"]
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": "p1", "method": "prompt.submit",
                                          "params": {"session_id": sid, "text": text}}))
            elif msg.get("method") == "event":
                p = msg["params"]
                if p["type"] == "approval.request":          # 无人值守兜底
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": "a1", "method": "approval.respond",
                                              "params": {"session_id": sid, "choice": "deny", "all": True}}))
                elif p["type"] == "message.complete":
                    return p["payload"]["text"]

print(asyncio.run(chat("用一句话介绍你自己")))
```

---

## 9. Background / agent / subagent 与 job 生命周期（2026-10-17 补充调研）

> **背景**：relay v2 曾用 `prompt.background` 承载 agent job（两阶段派生 → `background.complete` → JobStore 收敛），但业务需要 **cancel / 追加 prompt / 状态查询**。本节回答：Hermes 的 background、原生 subagent、以及“独立会话”三种载体各自能做到什么。所有结论都有源码定位 + 本机 v0.20.2 真机探针证据；探针脚本 `/tmp/hermes_ws_probe.py`，原始输出 `/tmp/hermes_probe_*.log`（§11.3）。
>
> **落地状态（2026-10-17）**：relay 已改用独立 job 会话，`prompt.background` 载体及其配置/代码/测试已删除；§9.1 保留为协议调研记录。
>
> **结论速览**：
> - `prompt.background`：❌ 无法支持 cancel / 追加 / 状态（源码无注册表；实测 `session.interrupt` 无效）。relay 已不再使用。
> - 原生 subagent：⚠️ 控制面（status / steer / interrupt）可用，但**没有 RPC 创建入口**，只能由模型调用 `delegate_task`；完成结果会回注父会话，且事件里的结果有截断。
> - 独立 job 会话：✅ create / append / cancel / status / 完成 / 恢复全部有确定性 RPC，已逐项实测；推荐作为 relay 的 job 载体。

### 9.1 `prompt.background`：无注册表、无控制面（现状即上限）

源码 `tui_gateway/methods_prompt.py:1200-1260`：

- 每次调用生成 `task_id = bg_<uuid6>`，`threading.Thread(daemon=True).start()` 执行一个**全新的 AIAgent**（`_background_agent_kwargs`，`server.py:6484`），与父 session 的 `session["agent"]` 无关联；
- 线程句柄、task 记录都不保存；唯一出口是完成后向父 session `_emit("background.complete", parent, {task_id, text})`；
- 不写父会话 history（实测 `session.history` count=0）。

实测（prompt=`run \`sleep 20\` then reply BG_DONE`，submit 后约 4s 查询，6.08s 调 `session.interrupt`；日志 `/tmp/hermes_probe_bg.log`）：

| 查询/操作 | 结果 |
|---|---|
| `prompt.background` | `{"task_id":"bg_e93bc4"}`（立即返回） |
| `session.status` | `Agent Running: No`（后台任务不可见） |
| `session.usage` | `active_subagents: 0` |
| `delegation.status` | `{"active": []}` |
| `process.list`（父会话作用域） | `[]` |
| `session.history` | `count: 0` |
| `subagent.interrupt {subagent_id:"bg_e93bc4"}` | `{"found": false}` |
| `session.interrupt`（6.08s） | `{"status":"interrupted"}` —— **对后台任务无效** |
| `background.complete` | **+24.79s 才到达**（submit→complete 22.8s），`text: "BG_DONE"`（sleep 20 正常跑完） |

结论：

- **cancel 不可实现**：`session.interrupt` 只中断父 session 的 live agent；后台线程不在 `_sessions` / delegation registry 中（`subagent.interrupt` 返回 `found:false`），也没有 task 级进程/状态 RPC；
- **追加 prompt 不可实现**：没有 task 句柄；`session.steer` 只作用于父 agent；
- **状态查询不可实现**：没有任何 RPC 能看到 task_id 的运行状态；
- 结果可靠性依赖“连接活着收到 `background.complete`”，错过即只能靠 relay 看门狗超时收敛；父会话 history 不保留任务轨迹。

> 因此 relay 的“cancel / 追加 / 状态”需求**不能**在 `prompt.background` 协议面上补齐；要换承载方式（§9.2/§9.3），要么接受现状。

### 9.2 原生 subagent（`delegate_task`）：控制面完整，但没有 RPC 创建入口

#### 9.2.1 创建路径：只有模型工具

- 创建入口是模型可调用的 `delegate_task` 工具（`tools/delegate_tool.py`），不是 RPC；v0.20.2 的 154 个 `@method` 中**没有 spawn 类方法**。
- `/agents` 只是 CLI/TUI 展示命令（`hermes_cli/cli_commands_mixin.py:495`）；`agents.list` RPC 返回 process registry（`methods_tools.py:1652`）；`spawn_tree.*` 是展示快照（`methods_session.py:3261`）。
- 因此 relay 若要创建 subagent，只能先对某个会话 `prompt.submit`，由**模型**决定调用 `delegate_task(background=true, ...)`；这不是确定性 API（模型可能不调、参数可能被改写）。spawn 的确定性回执来自 `tool.complete` 事件的工具结果（见 §9.2.2），而不是 JSON-RPC 响应。

#### 9.2.2 生命周期与事件（实测）

```
prompt.submit（要求模型调用 delegate_task background=true）
→ tool.generating / tool.start        name=delegate_task
→ tool.complete                       result={status:"dispatched", mode:"background", count, delegation_id:"deleg_…",
                                              goals, subagent_ids:["sa-0-…"], live_transcripts:[…], control_hint}
→ subagent.start                      {subagent_id, child_session_id, goal, depth, model, toolsets, ...}
→ subagent.thinking / subagent.tool / subagent.progress*
                                      （subagent.text 只镜像给 child watch session，父会话收不到）
→ subagent.complete                   {status:"completed"|"interrupted"|"failed"|"error"|"timeout", summary, text, duration_seconds, tokens, output_tail}
→ status.update (kind=process)        "[ASYNC DELEGATION BATCH COMPLETE — deleg_…] ..."（注入父会话前的通知）
→ 父会话被触发一个新的 turn            message.start / message.complete（父 agent 对结果的二次总结；非子代理原文）
```

实测（goal=`sleep 12` 后回复 BG_SUB_DONE；dispatch 后约 3s 调控制面）：

- `delegation.status` → `active: [{subagent_id:"sa-0-8b0ca3c6", parent_id:null, depth:0, goal, model, started_at, status:"running", tool_count:1, last_tool:"terminal"}]`；完成后 `active: []`（**只含 live 记录，无完成历史、无 delegation_id**）。
- `session.usage` → `active_subagents: 1`（进程级异步委派计数，不是单 job 状态）。
- `subagent.steer {session_id:<父会话>, subagent_id, text}` → `{"status":"queued"}`；子代理在下一个工具边界读到追加文本，最终回复包含 `BG_SUB_STEERED`（实测生效）。
- `subagent.interrupt {subagent_id}` → `{"found": true}`；实测调用后同一秒内 `subagent.complete` `status:"interrupted"`（子代理总时长 6.24s），`output_tail` 显示 `[Command interrupted]`、`exit_code:130`；父会话随后收到 completion 通知并总结。**取消有效，且是 cooperative hard interrupt。**
- 快速任务实测 `subagent.complete` 携带 `text:"QUICK_OK", summary:"QUICK_OK", status:"completed", duration_seconds:1.26`——relay 应以此事件作为 job 结果，而不是解析 `status.update` 的自由文本。
- **截断注意**：事件里的 `text` 是 `summary[:160]`、`summary` 是 `summary[:500]`（源码 `delegate_tool.py:3040+`），长结果不完整；完整子代理 transcript 持久化在 `child_session_id`，实测可用 `session.resume {session_id: child_session_id}` 打开并 `session.history` 读取（注意 `session.history` 默认 `include_ancestors=True`，会带出父会话链路，需按 `row_id`/时间过滤）。

#### 9.2.3 控制面 RPC（v0.20.2 源码 + 实测）

| RPC | 参数 | 作用 | 限制 |
|---|---|---|---|
| `delegation.status` | `{}` | 返回进程内所有 live subagent 记录 | 进程级、非会话级；完成即消失；记录里没有 `delegation_id` / `child_session_id` |
| `delegation.pause` | `{paused?: bool}` | 全局暂停新 spawn（源码；为避免影响运行中的实例，未做真机切换实测） | 不影响已运行子代理 |
| `subagent.interrupt` | `{subagent_id}` | 中断指定子代理 | **无 owner 校验**（任何 WS 客户端知道 id 即可调用） |
| `subagent.steer` | `{session_id, subagent_id, text}` | 向运行中的子代理追加指令 | 要求 `session_id` 是**创建它的 live 会话**，且必须来自同一 transport（源码 `delegate_tool.py:237-285`）；子代理已收尾/非 owner 时返回 `status:"rejected"`（源码，未单独构造该失败路径的实测）。**断连重连后 `session.resume` 会产生新的 live sid，旧 subagent 的 owner 不匹配，steer 不可用；interrupt 不受影响** |

#### 9.2.4 作为 relay job 载体的可行性判断

**可用，但只适合“agent 自治编排”，不适合作为 relay 的确定性 job API**：

- 创建不确定：必须由模型调用工具；relay 只能“请求模型去 spawn”，无法直接创建。
- 回执虽可从 `tool.complete.result` 解析出 `delegation_id` / `subagent_ids`，但这依赖父 turn 的模型行为。
- 完成结果会**回注父会话**并触发父 agent 的一次新 turn（额外 token；会干扰该会话上的实时对话）。若坚持使用，必须用**独立的 dispatcher 会话**，不能复用设备的 live chat 会话。
- 并发上限：`delegation.max_concurrent_children`（本机 3）统一约束前台并发与后台异步委派；池满时 `delegate_task` **退化为同步执行**，会阻塞父 turn（源码 `delegate_tool.py:4150-4165`）。`delegation.max_spawn_depth`（本机 1）限制嵌套。
- 会话关闭会中断该会话 commission 的异步委派（`server.py:856` → `interrupt_for_session`），所以 dispatcher 会话必须保持存活。
- 审批：本机 `delegation.subagent_auto_approve=false`；无人值守仍需 relay 对 `approval.request` 自动 deny/allow。

### 9.3 独立 job 会话（`session.create` + `prompt.submit`）：确定性 API，推荐

不借助 `prompt.background` / subagent：relay 为每个 job 创建一个**独立 Hermes 会话**（`close_on_disconnect=false`），在自己的 session 上跑 job turn。这样主会话（device live chat）完全不受影响，且全部控制都是 RPC。

#### 9.3.1 能力映射（全部实测）

| relay 需求 | RPC / 事件 | 实测结果 |
|---|---|---|
| 创建 job | `session.create` → `prompt.submit` | 返回 `session_id` / `stored_session_id`；`prompt.submit` → `{"status":"streaming"}` |
| 状态查询 | `session.active_list`（按 id 过滤） | 行含 `status: "working"|"idle"|"waiting"|"starting"`、`message_count`、`preview`、`session_key`；运行中为 `working` |
| 状态查询（人类可读） | `session.status` | `Agent Running: Yes/No`、tokens、title |
| 追加 prompt（不打断） | `session.steer {session_id, text}` | `{"status":"queued"}`；实测最终回复同时包含原任务与追加 token（`JOB_DONE JOB_STEERED`） |
| 追加 prompt（作为下一轮） | `prompt.submit {..., "queued": true}` | `{"status":"queued"}`；当前 turn 结束后按序执行（实测第二个 `message.complete` 为 `SECOND_TURN`） |
| 追加 prompt（默认行为） | `prompt.submit`（运行中） | `{"status":"redirected"}`：重定向当前 live turn（`display.busy_input_mode` 默认 `interrupt`；可配 `queue`/`steer`） |
| 取消 | `session.interrupt {session_id}` | `{"status":"interrupted"}`；`tool.complete` 显示 `[Command interrupted]`/`exit_code:130`；`message.complete` `status:"interrupted"`、`text:null` |
| 完成结果 | 该 session 的 `message.complete` | `{text, usage, status}`；**没有额外的父会话二次总结 turn** |
| 结果持久化 | DB transcript + `session.history` | `close` 后用 `stored_session_id` `session.resume` 成功：`message_count:3`、历史含 `Operation interrupted.` |
| 多 job 复用连接 | 同一 WS 上多个 session | 实测两个 session 并行；`message.complete` / `tool.*` 均带正确 `session_id`；interrupt A 不影响 B |

#### 9.3.2 事件路由与恢复

- 所有事件按 `params.session_id` 分发；relay 现有 `turn_futures: dict[sid, Future]` 与 `_event_session_id()` 已经是按 session 关联的（`services/agent_relay/adapters/hermes.py:930-980`），扩展为“每 job 一个 sid”与现有结构同构。
- `session.create` 的 `close_on_disconnect=false` 使 job 会话在断连后保留；relay 重启后按 `stored_session_id` `session.resume`。若 resume 返回**新的 session_id**，说明旧会话已被回收，running job 按现有 D10 规则 fail-fast（与现状一致）。
- 审批事件同样带 job session 的 `session_id`，relay 现有 `_autorespond_approval` 直接可用。

#### 9.3.3 约束与注意

- **活跃会话上限**：`max_concurrent_sessions`（本机未配置 = 不限；配置后由 `hermes_cli/active_sessions.py` 执行，跨 CLI/desktop/gateway 共享）。relay 的 `max_in_flight_jobs` 应小于该上限。
- **上下文成本**：每个 job 会话独立构建 agent，首个 turn 的 prompt 约 2.4 万 tokens（含 system prompt 与工具 schema，实测 `context_used≈24.4k`），与 `prompt.background` 的新 AIAgent 成本同量级；短任务大量派发时需评估。
- **job prompt 必须自包含**：不要依赖 device 主会话的 mission/system 种子；直接把 relay 渲染的 `job_task.md` 文本作为首个 `prompt.submit`。
- **`session.active_list` 是进程级列表**，relay 需按 `id` / `session_key` 过滤自己的 job 会话。
- **`session.status` 的输出是给人看的文本**；程序化判断优先用 `session.active_list.status`。
- **取消语义**：实测是在工具执行中打断（hard interrupt）；中断后 `message.complete.text` 为 `null`，job 结果应标记为 cancel/interrupted，而不是当成空结果投递。
- **取消不清理后台进程**：实测（v0.20.2，`/tmp/hermes_probe_bgproc.log`）：turn 先用 `terminal background=true` 启动 `sleep 120`，再在前台跑 `sleep 30`；`session.interrupt` 后前台 turn 变 `interrupted`（exit 130），但后台进程 `proc_27ca1ff4c1f5` 仍为 `running`。`process.list` 能看到它，`process.kill {session_id, process_id}` 返回 `killed`（两者实现见 `methods_tools.py:49-80`）。源码注释也明确“只停 turn，后台进程有意保留”（`session.interrupt`）。若 job 的取消必须连进程一起清理，relay 需显式执行这一步。
- **若 job 自己派生了 async subagent**：`session.interrupt` 只停父 turn；async delegation 在 spawn 时已从父 agent 的 `_active_children` detach，不会被父 turn 的 interrupt 级联。要终止它们需 `delegation.status` + `subagent.interrupt`（v0.20.2 无 owner 校验），或关闭 job 会话（`_finalize_session` 会调用 `interrupt_for_session`）。
- **断连耐久性**：`close_on_disconnect=false` 只保留**有限宽限**；WS 断开超过 `HERMES_TUI_WS_ORPHAN_REAP_GRACE_S`（默认 20s）且未 resume，会话会被孤儿回收（`server.py:1125-1163`）。因此 job 会话仍要求 relay 快速重连；超过窗口的 running job 沿用 `session_lost` fail-fast。对比：原生 subagent 的异步委派只要还活着，`_session_has_active_delegations` 会推迟回收（reaper reschedule），这是 subagent 路径在断连耐久性上的差异。

### 9.4 三条 job 载体对比

| 维度 | `prompt.background`（现状） | 原生 subagent（`delegate_task`） | 独立 job 会话（推荐） |
|---|---|---|---|
| 创建 | `prompt.background` → `task_id`，**确定性** | 只能 prompt 模型去调工具；回执在 `tool.complete.result`，**非确定性** | `session.create` + `prompt.submit`，**确定性** |
| 取消 | ✗ | ✓ `subagent.interrupt` | ✓ `session.interrupt` |
| 追加 prompt | ✗ | ✓ `subagent.steer` | ✓ `session.steer` / `prompt.submit`（redirect/queue） |
| 状态查询 | ✗ | ✓ `delegation.status`（仅 live，进程级） | ✓ `session.active_list` / `session.status` |
| 完成结果 | 仅 `background.complete` 事件（错过即丢） | `subagent.complete`（`text`≤160 / `summary`≤500 截断）+ 父会话二次总结 turn；完整正文需 resume `child_session_id` | 该 session 的 `message.complete`（全文）；transcript 持久化 |
| 持久化/重启恢复 | 父会话无轨迹；任务结果仅在事件里 | 子会话 transcript / async_delegations sqlite；父会话通知 | DB transcript；`stored_session_id` resume |
| 对主会话影响 | 主会话 history 不写；仅 completion 事件 | completion 回注父会话（触发额外 turn） | 完全隔离 |
| 额外 LLM 开销 | 无（后台 agent 本身除外） | 每次 job ≈ 3 次父会话模型调用（dispatch 2 次 + completion 总结 1 次，实测 usage.calls） | 无额外 |
| 并发上限 | 无（Hermes 不限制） | `max_concurrent_children`（本机 3），池满退化同步 | 受 `max_concurrent_sessions`（默认不限） |
| 适合 | fire-and-forget | agent 自治编排/树状委派 | relay-facing job（create/cancel/append/status） |

**建议（已落地）**：relay 的 agent job 使用“每 job 一个独立 Hermes 会话”；`prompt.background` 不再由 relay 使用（协议仍可供其它 fire-and-forget 场景使用）；原生 subagent 不作为 relay job API（除非产品明确要 agent 自编排，此时用独立 dispatcher 会话隔离）。

### 9.5 relay 落地要点（与现有代码对齐）

1. `_DeviceConnection` 增加 `job_sessions: dict[job_key, {session_id, stored_session_id}]` 与 `job_sid_to_key`；`session.create` 时 `close_on_disconnect=false`、`source="livachat-relay"`、`title=summary`（可按需 `cwd`）；`agent_jobs.hermes_task_id = "session:<stored_session_id>"`。
2. 派发：`session.create` → `bind_external_ref("session:<stored>")` → `mark_running` → `prompt.submit`（首条即 `services/agent_relay/prompts/job_task.md` 的渲染文本）；`stored_session_id` 供重启恢复。
3. 取消：`session.interrupt` → 等该 sid 的 `message.complete(status="interrupted")` → `mark_failed(job_key, "cancelled")`；如需连 job 启动的后台进程一起清，追加该会话的 `process.list` → `process.kill`（§9.3.3）。
4. 追加：优先 `session.steer`（不打断）；需要新 turn 时 `prompt.submit {queued:true}`。
5. 状态：`session.active_list` 按 sid 查 `status` / `message_count`；`session.status` 仅作辅助。
6. 完成：`message.complete`（按 sid 关联）→ `mark_done(text)` → 现有 deliverer/补投管线；输出 `NOOP` / `error:` / 空文本由 relay `classify_job_result`（live 与 recovery 共用）消费、不投递。
7. 恢复：`recover_active_devices` 对每个 running job 的 `stored_session_id` 做 `session.resume`；返回新 sid 且有 transcript ⇒ 从 messages 恢复终稿补投，无 transcript ⇒ `failed(session_lost)`。device session 的 reap 不再影响 job（`_mark_session_lost` 已删除）。
8. 清理：job 完成/失败后 `session.close`；不要在 job 会话上复用 device live chat。

---

## 10. 最新 main（v0.22.2+canary）相对 v0.20.2 的差异（静态审查，2026-10-17）

> **范围**：本节是对 `~/projects/github/hermes-agent`（main，HEAD `868b9f244f`，`v0.22.2+canary.20261017T065512Z-39-g868b9f244f`）的**静态代码审查**，**没有运行最新版**；本机部署仍是 v0.20.2，§1-§9 的真机实测结论以 v0.20.2 为准。稳定版 v0.22.2 发布于 2026-10-15；自 v0.20.2 release commit `df4b65147d` 起，本文关注的文件有 436 个提交（含 merge）。

### 10.1 版本与代码结构

- 稳定标签：v0.22.2（2026-10-15）、v0.22.1（2026-10-14）；main 另有 39 个 canary 提交。
- `tui_gateway` 做了模块拆分：`methods_subagents.py`、`methods_session_interrupt.py`、`prompt_turn.py`、`event_replay.py`、`session_lifecycle.py` 等；`prompt.background` / `subagent.*` / `session.*` 的实现位置与 v0.20.2 不同。
- 方法注册不再只用 `@method`：`session.*` 走 `_session_method`，部分工具方法走 `_rpc`。按 `@method` 计数从 154（v0.20.2）变为 142（v0.22.2）不能直接对比（注册方式变化 + 功能增删并存）。
- 新增方法族（与 job 无关但属于协议面变化）：`bot_relay.*`、`connectors.*`、`vault.*`、`display.*`、`onboarding.*`、`free_tier.*`、`shared_metrics.*`、`i18n.*`、`session.foreign.*`、`session.events.*`、`subagent.list/tail`、`prompt.btw` 等。

### 10.2 `prompt.background` / side agent：控制面没有变化

- 实现重构为 `_side_agent_args` + `_spawn_side_agent` + `_start_session_work`（`methods_prompt.py:1095/1347/1383`、`session_lifecycle.py:24`）；`_start_session_work` 只做 retirement admission 和线程启动，**不是任务注册表**。
- 资源生命周期改进：side agent 结束调用 `AIAgent.close()`（#50197）；使用独立的 session DB 引用（`_side_agent_session_db`）；线程纳入 retirement 管理。
- **job 语义变化（v0.22.2，源码）**：`AIAgent.close()` 会 kill 该 agent 拥有的、被 process registry 跟踪且未标记 `persist_on_release` 的 running 进程（`run_agent.py:938-957` → `agent/client_lifecycle.py:104-122`）。因此 v0.22.2 的 `prompt.background` **job 结束后，其启动且被跟踪的 server/watcher 不再存活**；v0.20.2 不调用 `close()`，后台进程会继续跑（本项未在 v0.22.2 运行验证）。
- **仍无 cancel / status / steer / list RPC**；唯一完成信号仍是父会话的 `background.complete`（契约 `contracts/events.py:543`，payload `{task_id, text}`）。
- 同族新增 `prompt.btw`（side question，完成事件 `btw.complete`）与 `preview.restart`（`preview.restart.complete/progress`），机制相同、同样不可控。
- ⇒ §9.1 的结论在最新代码上**不变**。

### 10.3 原生 subagent：控制面增强，但仍无 spawn

- **新增 `subagent.list`**（`methods_subagents.py:53`，契约 `profiles_vault_complete_foreign_subagents.py:666`）：
  - `subagents`: 当前 session 的 live children，字段含 `subagent_id`、**`delegation_id`**、`status`、`goal`、`model`、`started_at`、`tool_count`、`last_tool`、`accepting_steer`；
  - `delegations`: 从 durable store 读取的最近失败 async delegation（`delegation_id`、`task_index`、`status`、`goal`、`error`、`dispatched_at`、`completed_at`），由 `async_delegation.failed_delegations_for_session` 提供（`async_delegation.py:664`）。
  - 可见性带 lineage fallback（`methods_subagents.py:29-50`）：断连/resume/压缩轮换后仍能列出子代理。
- **新增 `subagent.tail`**（`methods_subagents.py:105`）：返回 owned child 的 live transcript 最后 16 KiB（`_SUBAGENT_TAIL_BYTES=16384`），可用于运行进度观察。
- **`subagent.interrupt` 改为 owner-scoped**（`methods_subagents.py:81`）：必须带 `session_id`，并通过 `_current_session_steer_authority` 校验 transport + live session generation；不再允许任意客户端凭 id 停止（修复 v0.20.2 的无校验问题）。
- `subagent.steer` 语义不变（`methods_session.py:2228`）：仍要求创建它的 live session + 同一 transport；收尾后返回 `rejected`。
- **控制仍受 generation 限制**：list 有 lineage fallback，但 `steer` / `interrupt` / `tail` 仍要求精确的 live session generation；WS 断连后 `session.resume` 会产生新 sid，旧子代理无法再控制。
- **仍无 spawn RPC**：全部注册方法中没有 `*.spawn`；创建仍只能由模型调用 `delegate_task`（§9.2.1 结论不变）。
- **main canary 新增可观测事件 `subagent.spawn_requested`**（relay 转发 commit `c6ac2564ba`，2026-10-16，**v0.22.2 稳定版没有**）：在子代理被接受、真正运行前发出；v0.20.2 的 child callback 与 v0.22.2 的 `_LIFECYCLE_EVENTS` 都没有转发它，父会话收不到。relay 在 canary 上可用它提前建立 job ↔ subagent_id 关联。注意它只是事件，**不是 spawn RPC**。
- `/stop` 现在会中断 async delegations（`cli_commands_session_tools.py:286-307`），仍不覆盖 `prompt.background` 的 side agent。
- async delegation 可靠性增强：v0.22.2 稳定版已含 durable completion restore（`34c2dc5cb1`）、orphan re-offer（`021530a69e`）、失败委派查询（`a30bd337e5`）、`/stop` 返回 partial interrupted completions（`b71359066c`）；main canary 另含未查看 completion 转 note（`d04e580956`，after v0.22.2）。

### 10.4 事件回放：新增的可靠性能力

- 所有带 `session_id` 的 event 帧新增 per-session 单调 `seq`；`gateway.ready` 新增 `replay_epoch`（`ws.py:455-460`）。
- 新增 `session.events.since` / `session.events.stats`（`methods_session.py:2327/2344`）：
  - 客户端带 `last_seen` 取回之后的事件；`truncated=true` 时回查 history；
  - 环形缓冲：每 session 512 事件 / 4 MiB，最多 64 sessions，进程 64 MiB；按 FIFO 淘汰并记录 truncation watermark；
  - `epoch` 变化表示后端重启，客户端应重置水位。
- 对 relay 的意义：断连重连后可补回错过的 `background.complete`、`subagent.complete`、`message.complete` 等（必要时按 `truncated` 回查 `session.history`）。这是 v0.20.2 没有的能力（v0.22.2 稳定版已包含）。
- **截断时的边界**：`background.complete` 不写入父会话 history；若 replay ring 已截断，回查 history 也拿不回该结果（仍需 relay JobStore / 看门狗兜底）。独立 job 会话的 `message.complete` 可从 transcript 恢复，这是 job 会话方案在可靠性上的又一优势。

### 10.5 会话控制行为变化

- `session.steer`：session 空闲时现在返回 `{"status":"rejected"}`，应作为下一条 prompt 排队；仅 turn 运行中返回 `queued`（`methods_session_interrupt.py:164`）。
- `session.redirect`：turn build 窗口内 / 压缩进行中返回 `{"status":"queued"}`，不再直接 4010。
- `session.interrupt`：语义不变，新增 hosted-room 参数 `expected_hosted_task_id`（不匹配时返回 `{"status":"not_interrupted","interrupted":false}`）；正常路径仍返回 `{"status":"interrupted"}`（`methods_session_interrupt.py:70`）。
- `session.create` / `resume` / `close` / `status` / `history` / `usage` / `active_list` 仍在（部分改为 `_session_method` 注册）。
- ⇒ §9.3 的独立 job 会话方案仍成立，且可用 §10.4 的事件回放增强断连恢复。

### 10.6 升级/对接注意（若未来升级到 v0.22.2+）

1. `subagent.interrupt` 必须带 `session_id`，且只能由 owner transport 调用；旧调用方式会失败。
2. `session.steer` 空闲时返回 `rejected`，relay 需区分 `queued` / `rejected` 并自行排队。
3. 事件帧新增 `seq`；重连应使用 `session.events.since` + `replay_epoch`，`truncated` 时回查 history。
4. 方法模块已拆分，按 `@method` 数量或旧文件路径做工具/反射会失效；以方法名和 contracts 为准。
5. 以上为**静态审查**；升级前建议在测试实例上重跑 §11.3 的探针（尤其 background / subagent 控制面与事件回放）。

---

## 11. 附录

### 11.1 实测握手样例（`/api/ws`）

```
→ GET /api/ws?token=42v6...  (无效 token ⇒ HTTP 403)
← {"jsonrpc":"2.0","method":"event","params":{"type":"gateway.ready","payload":{"skin":{...}}}}
→ {"jsonrpc":"2.0","id":"t1","method":"session.create","params":{"close_on_disconnect":true,"source":"tool"}}
← {"jsonrpc":"2.0","id":"t1","result":{"session_id":"6d6f1a14","stored_session_id":"20260727_164513_3442a3",...}}
→ {"jsonrpc":"2.0","id":"t3","method":"session.close","params":{"session_id":"6d6f1a14"}}
← {"jsonrpc":"2.0","id":"t3","result":{"closed":true}}
```

### 11.2 参考源码位置（本机 `~/.hermes/hermes-agent/`）

| 文件 | 内容 |
|------|------|
| `hermes_cli/web_server.py` | 全部 WS/REST 端点、认证与安全门（约 17.5k 行） |
| `tui_gateway/ws.py` | `/api/ws` 传输层（合批、写超时、断连回收） |
| `tui_gateway/server.py` | JSON-RPC dispatch 与全部 `@method` 实现（约 14.5k 行）；`prompt.background` 完成事件、子代理事件转发、async completion poller 均在此 |
| `tui_gateway/methods_prompt.py` | `prompt.submit` / `prompt.background`（`:1200`） |
| `tui_gateway/methods_session.py` | `session.status`（`:2484`）、`session.interrupt`（`:3102`）、`session.steer`（`:3379`）、`delegation.*`（`:3176`）、`subagent.*`（`:3204`）、`spawn_tree.*`（`:3261`） |
| `tui_gateway/methods_tools.py` | `agents.list`（`:1652`，实为 process registry） |
| `tools/delegate_tool.py` | `delegate_task` 工具、subagent registry（`:139-330`）、async dispatch（`:3920-4165`）、完整结果 payload（`:3040+`） |
| `tools/async_delegation.py` | 异步委派 registry / 持久化 / 完成队列 / `interrupt_all` / `interrupt_for_session` |
| `hermes_cli/console_engine.py` | console 命令注册与执行 |
| `hermes_cli/dashboard_auth/ws_tickets.py` | gated 模式 ticket 机制 |
| `gateway/platforms/api_server.py` | OpenAI 兼容 HTTP/SSE API（备选通道） |

### 11.3 探针脚本与原始日志（2026-10-17，v0.20.2）

- 脚本：`/tmp/hermes_ws_probe.py`（场景：`bg` / `sub` / `subcancel` / `session` / `multi` / `steer` / `quick` / `bgproc`）、`/tmp/hermes_steer_contract_probe.py`；仓库内可复现版本 `services/tests/manual/probe_hermes_job_contract.py`（job 会话 steer 契约 + interrupt transcript）
- 原始输出：
  - `/tmp/hermes_probe_bg.log` — `prompt.background` 不可取消/不可见（§9.1）
  - `/tmp/hermes_probe_sub.log` — 原生 subagent spawn + steer + 完成回注
  - `/tmp/hermes_probe_subcancel.log` — `subagent.interrupt` 取消
  - `/tmp/hermes_probe_session.log` — 独立 job 会话：status/steer/redirect/interrupt/resume
  - `/tmp/hermes_probe_multi.log` — 单 WS 双 session 并行与隔离
  - `/tmp/hermes_probe_steer.log` — `session.steer` 实际注入 + `queued:true` 下一轮
  - `/tmp/hermes_probe_steer_contract.log` — 运行中的独立 job 会话 `session.steer` 返回精确 `{"status":"queued"}` 且注入生效（最终回复被覆盖为 `STEER_INJECTED`）；`session.interrupt` 后 `session.history` 最后一条 assistant 精确为 `"Operation interrupted."`；指定不存在 model 会被回退默认模型，未能触发 `"Error:"` transcript
  - `/tmp/hermes_probe_quick.log` — `delegate_task` ack 与 `subagent.complete` 完整 payload
  - `/tmp/hermes_probe_bgproc.log` — `session.interrupt` 后后台进程仍存活 + `process.kill` 清理
- 探针会话均使用 `source="livachat-probe"`、`cwd=/tmp`，结束后 `session.close`；未修改任何 Hermes 配置。

### 11.4 注意事项

- SESSION_TOKEN 随 dashboard 重启变化，客户端需实现重取逻辑。
- 协议无独立版本号，以源码为准。已复核 **v0.20.2**（release 2026.8.16）：握手、事件、`session.*` / `prompt.*` / 审批方法全部兼容。源码 `@method` 注册数从 v0.20.0 文档记载的 134 增至 **154**（未做逐版本方法 diff；§3.4 的方法表以 v0.20.2 源码为准）；§9 的 background/subagent 行为已在本版本逐项实测。
- 事件 `message.delta` 等流式帧有 33ms 合批，对延迟敏感的逐 token 转发场景需评估。
- `/api/ws` 事件按 session 投递到最近活跃连接，避免多连接交叉操作同一会话。
- 原生 subagent 的 `subagent.text` **不会**发到父会话（只镜像给 child watch session）；relay 若只监听父 session，收不到子代理的逐 token 正文，但 `subagent.tool` / `subagent.complete` 仍可见。
- `prompt.background` 的 `background.complete` 与 `subagent.complete` 是两套事件；前者无 `session_id` 语义上的 job 句柄（只有 `task_id`），后者带完整子代理标识，不要混用。
