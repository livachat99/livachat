# Agent Relay → Hermes prompts 增强：注入设备时区（调研 + 修改方案）

> 来源：用户需求（2026-09-28）——"mcp tools 需要 timezone 参数，所以在给 agent（Hermes）的
> prompts 里加入当前 device 会话的时区提示；device 连接后，发送的 session.create 消息里带 timezone"。
> 状态：**已实施（2026-09-28，D1–D6 全按推荐）**——实施记录见 §7。
> 范围：`services/agent_relay/`（prompts 文案 + 渲染 + 事件/上下文透传）、`services/session/`
> （事件发布处透传）、文档。**不动 lvdata-mcp**（其 `timezone` 入参与回落规则 §8.7 已就绪）。
> 关联：[docs/timezone-support.md](./timezone-support.md)（客户端时区链的权威设计）、
> [docs/hermes_adaptor_design_v2.md](./hermes_adaptor_design_v2.md) §4（prompt 规范副本）。

## 1. 背景（问题）

lvdata-mcp 的 4 个只读工具都带 `timezone` 入参（`search`/`semantic_search`/`get_timeline`/
`get_event`/`get_recent_events`），语义是"**当前查询所在的时区**"：不传或无效 → 服务端
`watcher.timezone`（默认 `Asia/Shanghai`）。而 Hermes 侧 agent：
- **不知道设备本机时区** → 无法传 `timezone`，也无法正确解释"今天/昨天/最近三天"；
- 后台 job（`job_task.md`）同样不传递时区，跨时区用户的结果会按服务端默认时区切日。

好消息：**客户端时区链已实现**（见 §2），设备时区在 `session.start` 就已上到服务端并落库——
缺的只是"最后一跳"：把它交给 agent。

## 2. 现状事实（codebase 核查，file:line）

### 2.1 已实现的客户端时区链（`docs/timezone-support.md`，已落地）

| 环节 | 位置 | 事实 |
|------|------|------|
| 协议字段 | `packages/sdk-python/src/livachat_protocol/generated/protocol_models.py:225` | `ClientSessionStartConfig.timezone`（"Client local timezone ID… server validates and canonicalizes it, and ignores invalid values. Optional."） |
| 归一化 | `services/shared/timeutil.py::canonicalize_timezone`（经 `services/session/manager.py:85` 调用） | 无效 → 忽略 + info log；**有效 → 规范形**（IANA / `UTC` / `+HH:MM`） |
| 落库① | `services/session/orchestrator.py:415-422` → `ChatStore.on_device(timezone=ctx.timezone)` | **chat_server.db `devices.timezone`**（schema：`services/shared/persistence/chat_store.py:41-49`） |
| 落库② | `services/session/manager.py:24-45` `_upsert_timezone` | **lvdata `timezones` 表**（day_summary 生成维度；`timezone-support.md` §5.2/§6） |
| 读侧 | `services/bff/common/deps.py::resolve_request_tz`（`timezone-support.md` §7.1） | BFF 用时区：未传 → 默认；非法 → 400；合法但不在表 → 回退默认 |
| 会话上下文 | `services/session/manager.py:129-138` `SessionContext(timezone=canonical_tz)` | 内存里也有（但只活在 livachat 会话层） |

### 2.2 缺口（2026-09-28 实施前快照）：relay/Hermes 侧完全没有时区

> 本节保留实施前的缺口快照；当前实现见 §7。2026-10-18 仅按代码更新了 `AgentContext` /
> `_AgentSession` 两行（移除 `recent_turns`），其余行仍为历史状态（如"prompts 无时区字样"
> 已由 §7 实施，阅读时以 §7 为准）。

| 项 | 位置 | 现状 |
|----|------|------|
| Agent 上下文 | `services/agent_relay/adapters/base.py`（`AgentContext`） | `AgentContext{session_id, device_id, language, timezone}` |
| 事件载荷 | `services/agent_relay/models.py:17-33`（`AgentEvent`）、`:133-144`（`make_session_started_event`） | `session.started` 只有 `{kind, device_id, session_id, ts_ms}`；`language` 字段存在，但只在 `publish_utterance` 上被填（`agent_publisher.py:48-64`），`publish_session_started` 不填 |
| 发布处 | `services/session/orchestrator.py:451-454` | `publish_session_started(session_id, device_id)`——**同一个函数里 `ctx.timezone` 就在手边**（`:424 self._ctx = ctx`） |
| relay 会话态 | `services/agent_relay/session_manager.py`（`_AgentSession`） | 存 `language` / `timezone` / `last_proactive_at`；`AgentContext(...)` 构造共 **5 处**，改动须全覆盖 |
| prompts | `prompts/hermes_monitor.md` / `prompts/job_task.md` | 无时区字样 |
| 渲染 | `adapters/hermes.py:1509-1526`（`_render_mission`）、`:1528-1537`（`_render_job_task`）、`:1494-1501`（nudge 载荷） | 占位符分别 `$chatlog_path/$chat_session_id/$path_hint`、`$task/$chat_session_id/$chatlog_path`；nudge = `{type, chatlog_path, chat_session_id, new_lines, truncated, outstanding_jobs}` |
| 规范副本 | `docs/hermes_adaptor_design_v2.md` §4（:256 起 "Mission 内容要点"） | monitor prompt 的规范摘要——**改动须同步**（I-2 的教训：三处清单漏改两处） |
| 测试 | `services/tests/unit/agent_relay/test_hermes_contract.py:97/109/119`、`services/tests/integration/agent_relay/test_hermes_v2_integration.py` | mission/background 渲染与 nudge 载荷断言 |

## 3. 方案

### 3.1 时区来源（决策点 D1）

| 方案 | 做法 | 评价 |
|------|------|------|
| **A（推荐）** | `session.started` 事件加 `timezone` 字段：orchestrator 发布处传 `ctx.timezone`（**已是 canonical 值**，见 §2.1）→ relay 存入 `_AgentSession` / `AgentContext` | 复用既有链路、无新依赖方向（relay 不读别人的表）；**随每次连接刷新**（用户改手机时区 → 下次 session.start 生效）；改动面 = 6 个代码文件的透传 + 2 份文案（§3.2） |
| B | relay 读 `chat_server.db devices.timezone`（relay 已用该库：`hermes_device_sessions`） | 免改事件层，但引入"读 livachat 写的表"的反向耦合 + 需处理缓存/刷新时机（什么时候重读？）→ 不推荐 |
| C | 只在 prompt 里写死服务端配置时区 | 等于没做（跨时区用户仍错），否决 |

**A 的关键前提**：`ctx.timezone` 只在用户**上报过**时非空（老客户端/未上报 → `""`），
relay 必须容忍空值（§3.4）。

### 3.2 代码改动（A 方案，逐文件）

| 文件 | 改动 |
|------|------|
| `services/agent_relay/models.py` | `AgentEvent` 加 `timezone: str \| None = None`（与 `language` 并列，不新增 kind）；`make_session_started_event(*, device_id, session_id, ts_ms, timezone=None)` |
| `services/session/agent_publisher.py` | `publish_session_started(..., timezone: str \| None = None)` 透传 |
| `services/session/orchestrator.py:451` | 调用处补 `timezone=ctx.timezone or None`（`ctx` 已在作用域内） |
| `services/agent_relay/adapters/base.py` | `AgentContext` 加 `timezone: str = ""` |
| `services/agent_relay/session_manager.py` | `_AgentSession` 加 `timezone: str = ""`；`session.started` 处理时 `sess.timezone = event.timezone or sess.timezone`；**5 处** `AgentContext(...)` 全部补 `timezone=`（re-arm 路径 `:219` 从 `sess` 取） |
| `services/agent_relay/adapters/hermes.py` | ① `_render_mission` 新增 `timezone_hint` 占位符（§3.3，源 = `context.timezone`——两处调用点 `:1043/:1082` 都已持有 ctx）；② `_render_job_task` 加 `timezone` 参数 + `$timezone_hint`；**该派发点（`:1226-1262`）作用域内没有 ctx**（locals 只有 `contract`/`conn`/`session_id`/`chatlog_path`）→ **实施时核实**：`_handle_job_request`（`:1221`）的调用点就在 `nudge()`（`:1203-1205`）内、`context` 在作用域 → **不需要 adapter 缓存**：给 `_handle_job_request` 加 `timezone` 参数、调用点传 `context.timezone` 即可（比计划少一处状态与生命周期耦合）；③（D3）`_build_nudge_prompt` 载荷加 `"timezone": ...` |
| `services/agent_relay/prompts/*.md` | 两份文案加时区小节（§3.3 逐字草案） |
| `docs/hermes_adaptor_design_v2.md` §4 | "Mission 内容要点"加一条（prompt 规范副本，改动须同步） |

**不做**：relay 侧不做时区校验/归一化——上游 `ctx.timezone` 已 canonical（§2.1），MCP 侧
`try_resolve_timezone` 接受 IANA / `UTC` / `+HH:MM` 全部规范形；重复校验只会引入两套规则。

### 3.3 文案草案（逐字，供评审）

**`prompts/hermes_monitor.md`** —— 在"# 角色"之后新增独立小节（`$path_hint` 同款占位符模式）：

```markdown
# 本设备时区

$timezone_hint
```

渲染值（`_render_mission`）：
- 有值 → ``本设备时区:`Asia/Shanghai`。判断"今天/昨天"、换算本地日界、以及调用 `lvdata` MCP 工具时,把 `timezone` 参数传这个值(不传会按服务端配置时区解释)。``
- 空值 → ``本设备未上报时区;调用 `lvdata` MCP 时省略 `timezone` 参数,由服务端按其配置时区处理。``

**`prompts/job_task.md`** —— 加一行（同样用 `$timezone_hint`）：

```markdown
对话 session:`$chat_session_id`
$timezone_hint
对话文本文件:`$chatlog_path`(...)
```

渲染值（`_render_job_task`）：
- 有值 → ``本设备时区:`Asia/Shanghai`(调 `lvdata` MCP 工具时把 `timezone` 传该值)。``
- 空值 → ``本设备未上报时区(调 MCP 时省略 `timezone`,按服务端配置时区)。``

**nudge 载荷（若 D3 采纳）** —— `chatlog_update` 加一个字段：

```json
{"type": "chatlog_update", "chatlog_path": "…", "chat_session_id": "…",
 "timezone": "Asia/Shanghai", "new_lines": [...], "truncated": false, "outstanding_jobs": [...]}
```

### 3.4 空值/异常语义

| 场景 | 行为 |
|------|------|
| 客户端未上报（老版本 / 用户拒绝） | `event.timezone=None` → `AgentContext.timezone=""` → 渲染走"未上报"文案（agent 明确知道"按服务端默认"，不会瞎猜） |
| 客户端上报但非法 | 上游已忽略（`canonicalize_timezone` → None），same as 未上报 |
| 会话中途用户改系统时区 | 下次 `session.start` 刷新（A 方案天然支持）；若 D3 采纳，nudge 每轮也带最新值 |
| 时区为固定偏移（`+08:00`，`GMT+08:00` 归一化而来） | 原样透传（MCP 侧接受，`timezone-support.md` §7.1 边界已覆盖） |

## 4. 决策（2026-09-28：D1–D6 全按推荐拍板）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 时区来源 | **A：`session.started` 事件加 `timezone`**（orchestrator 发布处透传已有 canonical 值）——不引入"relay 读 livachat 表"的反向耦合；随每次连接刷新 |
| D2 | mission prompt 组织 | **独立小节 `# 本设备时区`**（与"# 长期记忆"同款） |
| D3 | nudge 载荷 | **带 `timezone`**（每轮刷新，跨时区旅行/改系统时区时纠正 session 值） |
| D4 | `job_task.md` | **注入**（后台 job 是 lvdata MCP 的主要调用方） |
| D5 | 未上报文案 | **显式"未上报 + 按服务端配置时区"**（不让 agent 误以为自己的时区对） |
| D6 | `timezone-support.md` 指向 | **加**（已就地加 §17 指向本文，见该文档） |

**否决/搁置**：B（relay 直读 `chat_server.db devices.timezone`）——免改事件层但要处理缓存/刷新
时机与反向依赖，A 方案同效且更干净；C（只写服务端默认时区）——跨时区用户仍错；relay 侧重复做
时区校验/归一化——上游已 canonical、MCP 侧接受全部规范形，两套规则只会引入分歧。

## 5. 影响面与验收（已拍板，待执行）

**测试**（`services/tests/`）：

| 用例 | 断言 |
|------|------|
| 新增 `test_hermes_contract.py::test_render_mission_with_timezone` | `AgentContext(timezone="Asia/Shanghai")` → 渲染文本含 `Asia/Shanghai` 与"timezone 参数传这个值"；空值 → 走"未上报"文案（两条都测，防 fallback 漂移） |
| 改 `test_render_job_task` | 加 `timezone` 参数后渲染含时区行；既有断言不回归 |
| 新增（若 D3）nudge 载荷断言 | `_build_nudge_prompt` 输出含 `timezone` |
| 新增/改 orchestrator 发布侧 | `publish_session_started(timezone=...)` 透传到 `AgentEvent.timezone`（可单测 publisher，不必起会话） |
| 现有 integration（`test_hermes_v2_integration.py`） | 全绿（事件新增可选字段不应破坏既有契约） |

**验收**：

1. 真机（或 integration mock）：设备 `session.start(timezone="Asia/Shanghai")` → relay 的
   `session.create` system 消息（即 mission）**含时区小节**且值等于上报值；
2. 未上报客户端 → mission 走"未上报"文案（不出现空值/乱码）；
3. 后台 job 派发的 `prompt.background` 文本含时区行（D4 通过时）；
4. Hermes 侧按提示传 `timezone` 调 `get_recent_events` —— 用真机 MCP 验证"今天"的日界与该时区一致；
5. `services/tests` 全量 pytest 全绿；`docs/hermes_adaptor_design_v2.md` §4 规范副本已同步。

## 6. 与既有设计的关系（防漏改清单）

- **`docs/timezone-support.md`**（本链权威）：其 §5/§6/§7/§8 覆盖"客户端→服务端→落库→BFF/lvdata"；
  本方案是同一链的**最后一跳（→Agent/LLM）**，实施时在该文档 §9（开放问题）或新增小节加指向。
- **`docs/hermes_adaptor_design_v2.md`** §4：monitor prompt 的规范摘要——本次改动须同步
  （教训：I-2 的三处工具清单曾只改一处）。
- **`tools/lvdata-mcp/README.md` / `docs/data/lvdata_mcp_tools.md`**：MCP 侧 `timezone` 语义
  §8.7/§14 已描述，本方案不改 MCP；若文案里出现"服务端配置时区"，与 MCP 文档口径一致即可。

## 7. 实施记录（2026-09-28）

| 层 | 改动 |
|----|------|
| 事件 | `agent_relay/models.py`：`AgentEvent.timezone: str \| None = None`（注释标 `session.started` 用途）；`make_session_started_event(..., timezone=None)` |
| 发布 | `session/agent_publisher.py`：`publish_session_started(..., timezone: str \| None = None)` 透传；`session/orchestrator.py:451` 调用处传 `timezone=ctx.timezone or None`（上游已 canonicalize） |
| 上下文 | `agent_relay/adapters/base.py`：`AgentContext.timezone: str = ""`；`session_manager.py`：`_AgentSession.timezone` + `session.started` 记录 + **5 处** `AgentContext(...)` 全覆盖（utterance 兜底的 `_AgentSession` 有意留空并注释——utterance 事件不带 tz） |
| 渲染 | `adapters/hermes.py`：新增 `_timezone_hint(tz, verbose)`（文案见 §3.3，一处收口四种形态）；`_render_mission` 注入 `$timezone_hint`；`_render_job_task(..., timezone="")` 注入；`_build_nudge_prompt(update, timezone="")` 载荷加 `timezone` 字段 |
| 文案 | `prompts/hermes_monitor.md` 新增 `# 本设备时区` 小节；`prompts/job_task.md` 头部加一行（均用 `$timezone_hint` 占位符，同 `$path_hint` 模式） |

**与计划的唯一偏差（简化）**：原计划为 `_render_job_task` 引入"adapter 侧 `session_id → timezone` 小字典 + 生命周期维护"——实施时核实 `_handle_job_request` 的调用点就在 `nudge()` 内（`context` 在作用域），改为**参数透传**，少一处状态与清理耦合（§3.2 已就地更正）。

**测试**：`test_hermes_contract.py`——nudge 载荷断言加 `timezone`；新增 `test_render_mission_with_timezone`（有值注入 + 空值走"未上报"文案 + 占位符不残留）与 `test_render_job_task_with_timezone`；`tests/unit/session/test_agent_publisher.py`——新增 `test_publishes_timezone`（原用例补 `timezone is None` 断言）。
**验证**：`services/tests/unit` 全量 **1118 passed / 0 failed**；真机渲染核对（mission/background/nudge 三段文案与 §3.3 逐字一致，含未上报分支）。
**integration 说明**：`services/tests/integration` 在本机大面积失败（158 条 Redis 连接被拒 + 级联），**已用 `git stash` 在 HEAD 基线复现同样失败**（代表用例与两个相关测试类计数完全一致 7F/2P/9E）→ 环境缺 Redis，与本次改动无关；有 Redis 的环境应整套跑一遍。已核对集成断言均为**包含式**（`"chat_session_id" in content` 等），新增字段/小节不会触及其它断言。

**追加（同日，review）**：补上"事件 → 上下文"这一跳的单测
`test_session_manager.py::test_session_started_carries_timezone_to_context`（`session.started.timezone`
→ `_AgentSession.timezone` → 派发到 adapter 的 `AgentContext.timezone`；未上报 → `""`）——此前只覆盖了
渲染与发布两侧，中间的 relay 落库/派发无断言。实施踩点：生命周期钩子走 `create_task`（需让出一次
事件循环再断言）；`MockAdapter` 增 `session_contexts` 记录、测试内显式开 `proactive_enabled`（默认 False，
生命周期只在 proactive 适配器上派发）。验证：`services/tests/unit` **1119 passed / 0 failed**、
`tools/lvdata-mcp/tests` **286 passed / 0 failed**。

---

## 8. 多 adapter（`default_adapters`）与 prompts 一致性：分析与设计（已实施，2026-10-18）

> 来源：用户需求（2026-10-18）——"`default_adapters` 中的每个 agent adaptor 都会启用，所以 prompts
> 里面要体现出来"。
> 状态：**已实施（2026-10-18：Q4=B，其余按推荐）**。实现记录见 §8.10。
> 范围：启动期注入 LivaChat 的认知声明、两个 adapter 的 monitor/任务书 prompts、nudge 载荷与推送归因。
> 不改 job 路由、投递语义与客户端展示（D8 仍是既有搁置项）。

### 8.1 结论

`default_adapters` 是**完整启用清单**，不是"默认值/回退选择"；清单中的 adapter 会全部实例化并接入事件扇出。
但当前 prompts 体系建立在"只有 Hermes 一个后台 agent"的假设上：

- LivaChat 的 dialog/omni 认知声明只读 `adapters.hermes.nickname`：OpenClaw-only 部署下完全不注入；
  两者同时启用时只会尝试声明 Hermes——**若 Hermes nickname 为空，则连 Hermes 也不声明**（即使
  OpenClaw 配置了名字）。
- 两个 adapter 的 monitor prompt 都只描述自己，不知道同侪存在；同一个 chatlog 更新会被依次发给所有
  proactive adapter，服务端不做跨 adapter 去重/仲裁。**Q4=B 已拍板：不注入同侪提示，接受重复
  message/重复 job 的风险。**
- nudge 的 `outstanding_jobs` 是设备维度、混合所有 adapter，但载荷不带归属；多个 monitor 能看到彼此的在途
  任务却无法区分 owner。
- 推送归因存在隐式 `"hermes"` 默认值；live proactive 推送不带 `agent_id`，只有 job 结果带 nickname 溯源。

设计目标：让 **LivaChat 的认知声明**基于实际装载的 adapter 集合生成；adapter 自身的 monitor/任务书
保持单 agent 语义（Q4=B：不注入同侪信息）；**Hermes 单 adapter 保持与现状逐字一致，OpenClaw-only
使用通用单昵称模板**（能力清单含联网查询实时信息）；空 nickname / 非 proactive / 重复名字等
边界可解释；不做服务端仲裁。

### 8.2 代码事实

> 本节记录实施前的代码快照；实施后的变化见 §8.10：G1/G2/声明装配 → §8.3 已实施，G4 → Q7 补
> `agent_name`，G5 → D-E 绑定 adapter 名。当前行为以代码与 §8.10 为准。

#### 8.2.1 `default_adapters` 的"启用"语义

| 环节 | 位置 | 事实 |
|------|------|------|
| 配置 | `services/agent_relay/config.py`（`AgentRelayConfig.default_adapters`） | 默认 `["hermes"]`；dev 配置为 `["hermes"]` |
| 实例化 | `services/agent_relay/adapters/loader.py:29-36` | 按列表顺序逐个查 `_REGISTRY`；未知名字 `except KeyError: pass` **静默跳过**；重复名字被 dict key 合并 |
| 总开关 | `services/app/application.py:162` | 仅 `agent_relay.enabled: true` 时进入装配 |
| 连接 | `services/app/application.py:211-223` | 逐个 `await adapter.connect()`；失败只告警，**不从 `adapters` 移除** |
| 分发 | `services/app/application.py:255-264` | 完整 `adapters` dict 交给 `AgentSessionManager` |
| 顺序 | Python dict 插入序 | 声明顺序 = `default_adapters` 中首次出现的顺序 |

因此 prompt 侧应使用**实际实例化后的 `adapters` dict**（而非直接读配置列表）：未知名字已被 loader 过滤，
重复名字已被合并。

**现有注释与实际口径**：`application.py:279` 注释所说的"装载成功"实际等于 `load_all()` 实例化成功
（dict membership），不包含 `connect()` 成功。因此连接失败的 adapter 仍会被 LivaChat 声明；这是既有行为，
不是本次设计引入。Q8 已拍板**不收紧**：当前 prompt 是启动期一次性产物，若按连接成功过滤，一次瞬时
失败会让该 agent 在本进程内永久缺席，直到重启；保持"已实例化"口径，实施时修正注释措辞。

#### 8.2.2 事件扇出（多 adapter 的实际行为）

| 事件/路径 | 位置 | 扇出规则 |
|-----------|------|----------|
| `session.started` / `session.ended` 生命周期 | `session_manager.py:119,150,307` | 仅 `proactive_enabled` 的 adapter |
| `user.utterance` | `session_manager.py:236-243` | 仅 `reactive_enabled` 的 adapter（并行 `create_task`） |
| `recording.ready` | `session_manager.py:283-288` | **所有 adapter** 都会调用 `analyze_recording`（受 `proactive.enabled` 约束，见下方注）；OpenClaw 继承 base 的 no-op，Hermes 有实现 |
| chatlog watcher | `chatlog_watcher.py:286-294` | 每个 session 一个 watcher；同一 update **按顺序**发给所有 proactive adapter，无去重/仲裁 |
| job 派发配额 | `hermes.py:2030`、`openclaw.py:849` → `job_store.outstanding_jobs_for_device`；`in_flight_count()` | per-device 与 global 两条 cap 都是**全 adapter 混合计数**（两处查询均不过滤 agent_name）；每个 adapter 用自己的配置阈值与混合计数比较，不是独立配额 |
| nudge 在途任务 | `chatlog_watcher.py:328-333` → `job_store_sqlite.py:548-570` | 设备维度、混合所有 adapter；实施前每条只含 `job_key/task_id/summary`；**Q7 后增加 `agent_name`**（payload-only） |

> 两个独立前提（易混淆，必须分开记）：
> ① `on_recording_ready` 在 `not self._cfg.proactive.enabled` 时直接返回（`session_manager.py:246`）——
>    `proactive.enabled: false` 只关闭 **recording.ready 分析**；
> ② watcher **不受** `proactive.enabled` 控制：只要存在 `adapter.proactive_enabled` 的 adapter 且
>    `recording_lookup is not None`，`on_session_started`/utterance self-heal 就会启动 watcher
>    （`session_manager.py` 的 `_start_watcher` 调用点）。因此关闭 `proactive.enabled` 后 nudge 链仍会运行。

#### 8.2.3 prompt / 身份面清单

| 面 | 渲染/注入点 | 当前取值 | 多 adapter 现状 |
|----|-------------|----------|-----------------|
| LivaChat dialog system prompt | `application.py:277-284` → `agent_job_ingest.py:29-37` | 仅 `adapters.hermes.nickname`（单昵称模板） | OpenClaw-only 不注入；双启用只尝试声明 Hermes（Hermes 名为空时完全不注入） |
| LivaChat omni system prompt | 同上 | 同上 | 同上（translate 不注入） |
| Hermes monitor | `hermes.py:2573-2593` + `prompts/hermes_monitor.md` | `hermes.nickname` | 只描述自己 |
| OpenClaw monitor | `openclaw.py:1460-1479` + `prompts/openclaw_monitor.md` | `openclaw.nickname` | 只描述自己 |
| Hermes 任务书 | `hermes.py:2595-2610` + `prompts/job_task.md` | `hermes.nickname`（短版） | job 自包含，无同侪信息 |
| OpenClaw 任务书 | `openclaw.py:1481-1492` + `prompts/openclaw_background_task.md` | `openclaw.nickname`（短版） | 同上 |
| live 推送归因 | `session_manager.py:357-405` | `agent_name` 取 chunk metadata；实施前缺失回退 `"hermes"`，**D-E 后回退注册时的 adapter 名**；`agent_id` 不设置 | Hermes 的 push 路径无 metadata；OpenClaw 的 out-of-band push 三处均带 `metadata={"agent_name":"openclaw"}`；reactive ask 的 `openclaw.py:1150` yield 不带 metadata，但走 `_call_adapter` 的循环 name，不受影响 |
| job 结果归因 | `application.py:225-236` → `delivery.py:61-67` | `agent_name=JobRecord.agent_name`；`agent_id=nickname` 映射 | 已支持多 adapter |
| job_status 快照 | `job_status.py`（`JobStatusItem`） | 每条 job 的 `agent_name` 取 `JobRecord.agent_name` | 已支持多 adapter |
| chatlog 溯源 | `recording/sink.py`（`on_chat_text` 注释）→ `recording/chatlog.py`、`session/agent_job_ingest.py` | `agent_name/agent_id/job_key` 仅 agent job 结果行携带 | live proactive 推送不带 `agent_id` |

#### 8.2.4 缺口清单

| # | 缺口 | 证据 | 影响 |
|---|------|------|------|
| G1 | LivaChat 认知声明硬编码 Hermes | `application.py:281` 判 `"hermes" in adapters`，只取 `adapters.hermes.nickname` | OpenClaw-only 部署时 LivaChat 不知道后台 agent；双启用时只尝试声明 Hermes，Hermes 名为空则两者都不声明 |
| G2 | 认知声明不区分 `mode` 与连接健康 | 仅判 relay enabled + dict membership；`reactive_enabled/proactive_enabled` 未参与 | reactive-only adapter 被描述成能接长任务；connect 失败的 adapter 仍被宣告 |
| G3 | monitor prompt 无同侪意识 | 两份 monitor 文案均为单 agent 叙事；watcher 把同一 update 依次发给所有 proactive adapter | 两个 monitor 可能对同一意图各发一条 message、各派一个 job；服务端当前无仲裁。**Q4=B：接受该风险，不提供 prompt 级缓解；Q10 不新增服务端仲裁** |
| G4 | `outstanding_jobs` 无归属 | `job_store_sqlite.py:559-570` 只 SELECT `job_key/hermes_task_id/summary` | monitor 看得到同侪的在途任务但无法判断 owner。**Q4=B 后不新增 prompt 指引；Q7 仍补 payload 归属信息（字段随 nudge JSON 下发）** |
| G5 | 推送默认 `agent_name="hermes"` | `session_manager.py:376` | 未来新增 adapter 忘记设置 metadata 时会被错误归属；Hermes 目前正依赖该默认 |
| G6 | live proactive 推送无 `agent_id` | `session_manager.py:395-405` 不传 `agent_id`；chatlog 溯源注释说明仅 job 结果行携带 | 多 adapter 下 live 输出只能靠 `agent_name` 区分；nickname 不进入 live 消息溯源 |
| G7 | 既有设计前提过期 | `docs/prompts_improvements.md:244` D1 以"openclaw 是遗留适配器（`default_adapters=["hermes"]`）"为由只复用 Hermes nickname | 一旦 OpenClaw 被启用，该前提不成立 |
| G8 | `proactive.enabled` 不是总开关 | `session_manager.py:246` 只在 `on_recording_ready` 检查；watcher 启动路径不检查 | 关闭 `proactive.enabled` 后 nudge/主动推送仍然运行，只有 `recording.ready` 分析停止；prompt 设计不能把它当作 proactive 总开关 |

> 实施后状态（2026-10-18）：G1/G2 由 §8.3 的声明装配覆盖；G4 由 Q7 补 `agent_name`；G5 由 D-E
> 加固；G3/G6/G8 按 Q4=B/Q9/Q11 接受现状；G7 文档已同步。

### 8.3 设计

#### 8.3.1 启动期计算 enabled agents（D-A）

在 `application.py` 完成 `load_all()` 后、注入 prompt 前计算：

```python
named_agents = [
    (name, adapter, adapter.nickname.strip())
    for name, adapter in adapters.items()
    if adapter.proactive_enabled and adapter.nickname.strip()
]
```

- 只保留 `adapter.proactive_enabled`（来自 adapter `mode`）且有 nickname 的 adapter：声明承诺的是
  "可交接的长任务"，而 `job_request` 只出现在 proactive monitor 的 nudge 中；reactive-only adapter
  不应被描述成后台助手。
- 空 nickname 告警但不阻断启动（Q2 已拍板）；它不进入声明，但 proactive 输出仍可能出现，属于已知限制。
- 注意过滤的是 `adapter.proactive_enabled`，**不是** `agent_relay.proactive.enabled` 总开关：后者当前只关
  recording.ready 分析，watcher 仍运行（G8）；Q11 已拍板本期不改。
- 不按连接健康过滤：LivaChat 认知声明是启动期一次性注入；连接失败可由 adapter 重连恢复，按健康过滤会让
  一次抖动永久影响本进程的 prompt，直到重启（Q8 已拍板）。
- 为 adapter 增加公共 `nickname` property（base 默认返回 `""`，Hermes/OpenClaw 返回各自 `_cfg.nickname`），
  避免 application 层触碰私有 `_cfg`，也为未来 adapter 留统一入口。
- 顺序 = `adapters` dict 顺序（即 `default_adapters` 首次出现顺序），保证渲染稳定。
- **Q4=B 已拍板：不计算、不注入同侪集合**；不新增 `proactive_agents`、`set_peer_agents`、
  `peer_agents_hint` 或 `$peer_agents_hint`。

#### 8.3.2 LivaChat 认知声明（D-B）

`_with_agent_awareness(prompt, agents)` 按**去重后的名字数**分支：

- 0 个 → 不注入（现状）；
- 1 个且为 Hermes → **保持现有单昵称模板逐字不变**（回归零 delta；该模板含已由部署方确认的
  "联网查询实时信息"能力）；
- 1 个且非 Hermes（当前即 OpenClaw）→ 使用通用单昵称模板（§8.4-b）：能力清单与 Hermes 模板一致
  （2026-10-18 用户确认 OpenClaw 也支持联网查询实时信息），仅措辞略简；`AgentAdapter` 没有
  per-adapter 能力字段，当前按两个 adapter 共有的通用能力描述；
- N 个 → 新的多助手模板（§8.4-a），按 enabled 顺序列举；重复 nickname 去重并告警（声明无法消歧，
  但不应阻断启动）。
- 去重后若唯一名字对应多个 adapter（重复 nickname），使用通用模板而非 Hermes 模板，避免把 Hermes
  专属能力承诺给其他后端；同时告警。

注入点不变：dialog + omni 两处；translate 不注入（`application.py:283-284`）。声明仍在启动期生成，
配置变更需重启生效（现有行为）。

未配置 nickname 的 proactive adapter 不进入声明（无法被点名），但它的 proactive 输出仍可能出现；
LivaChat 对这类输出没有身份认知，属于已知限制（Q2 已拍板：不声明 + 告警）。

#### 8.3.3 monitor 同侪提示：已否决（Q4=B）

- 决策（2026-10-18）：不向 `hermes_monitor.md` / `openclaw_monitor.md` 或任务书注入任何同侪信息。
- 影响：两个 proactive adapter 仍会对同一 chatlog update 各自决策，可能重复 message / 重复派 job；
  服务端无仲裁（G3/Q10）。这是**接受的风险**，本期不提供 prompt 级缓解。
- 原备选（不实施）：注入"其他后台助手"段，并用 `outstanding_jobs` 做去重；需要 `peer_agents_hint` +
  `set_peer_agents` + 两份 monitor 模板新增占位符。该方案已否决。

#### 8.3.4 `outstanding_jobs` 归属（D-D，已采纳）

`job_store_sqlite.py` 的设备级查询增加 `agent_name` 字段：

```python
SELECT job_key, hermes_task_id, summary, agent_name
FROM agent_jobs WHERE device_uuid = ? AND <active>
```

返回 dict 增加 `"agent_name": r["agent_name"]`。nudge JSON 是自由结构，增加字段向后兼容。
**Q4=B 后不新增 prompt 指引**；Q7 已实施，作为 payload/运维归属信息——字段会随 nudge JSON
下发给模型，但 prompt 不要求模型据它去重。

注意：该查询同时被 per-device cap 使用（§8.2.2），字段增加不改变计数语义。当前应用装配的是
`SqliteJobStore`（`application.py:187`）；遗留 Redis `JobStore` 也有同名方法（`job_store.py:375`），
若仍被测试/部署使用，需同步增加 `agent_name`。Redis 版的键前缀仍硬编码 `agent:hermes`（遗留口径），
不是本次多 adapter 设计的运行路径。

#### 8.3.5 推送归因加固（D-E，已实施最小版）

- 把 `_on_adapter_push` 的默认值 `"hermes"` 改为**注册时绑定的 adapter 名**：`SessionManager` 初始化时用
  `adapter.set_push_callback(partial(self._on_adapter_push, adapter.name))`（或 `set_push_callback(cb, name=...)`），
  回调内以绑定名兜底。Hermes 无需改 yield；OpenClaw 的 metadata 仍优先。
- `ChatlogWatcher.set_push_callback` 同步改为 `(adapter_name, device_id, session_id, chunk)`；nudge 路径
  传 `adapter.name`，与 out-of-band 路径统一（否则 4 参方法会被 watcher 以 3 参调用）。
- live 推送的 `agent_id` 本期**不扩**：保持"仅 job 结果携带 nickname"的既有语义（`models.py` 注释 +
  `recording/sink.py`），避免改变 chatlog/lvdata 的溯源口径（Q9 已拍板；后续如需全量归因另立评估）。

#### 8.3.6 文档同步（D-F）

- `docs/prompts_improvements.md` D1：删除/改写"openclaw 是遗留适配器"的前提，改为"LivaChat 声明的
  enabled agents 来自 `default_adapters` 实际装载集合"；D8（昵称上屏）仍搁置。
- `docs/hermes_adaptor_design_v2.md` §4/§6 与 `docs/openclaw_adaptor_design.md` 的 prompt 段：
  monitor/任务书本轮**不变**（Q4=B），无需同步同侪段；仅在 Q/A 中登记"多 adapter 声明由 LivaChat
  认知声明覆盖"。
- `docs/fd_agent_relay_service.md` Q4（多 adapter 冲突）增加指向本节：Q4 保持开放，本设计不新增服务端仲裁，
  且 Q4=B 不注入同侪提示，仅用 prompt 约束。
- `config.py` 两处 nickname 注释补充"多 proactive adapter 时进入 LivaChat 声明"（不涉及同侪提示）。

### 8.4 文案草案（逐字，已采纳）

**a) 多助手认知声明**（Hermes 单助手继续使用现有 `AGENT_AWARENESS_DECLARATION_TEMPLATE`；非 Hermes 单助手见 b）：

```text
【后台助手:$nickname_list】用户可能用这些名字分别称呼后台 Agent:
它们能做需要较长时间、或你在实时对话里做不到的事(查用户的历史对话与长期记忆、联网查询实时信息、多步工具调用、长思考)。
你自己负责与用户的实时语音互动;遇到超出你能力、或需要较长时间的要求,在回复里自然地建议交给合适的后台助手
(如「这个我让$first_nickname帮你查一下」),不要生硬拒绝或声称完全无法处理,也不要替它们执行、不要自称是它们。
用户点名某个后台助手时,那句要求是给它的:你可以简短确认(如「好,我让它处理」);
若用户没有点名或无法判断指的是谁,用「后台助手」泛称,不要臆测具体名字。
它们的答复会以文字显示在对话里(不会朗读)。
```

- `$nickname_list` 渲染为 `「小黑」「小龙虾」`（按 enabled 顺序、去重）。
- `$first_nickname` = 列表首项。
- 多助手场景**不逐项承诺差异化能力**：能力清单由各后端 toolset 决定，代码中没有可枚举的 per-adapter 能力声明
  （`AgentAdapter` 无该字段）；统一使用当前 adapter 共有的通用能力描述（含联网查询实时信息）。

**b) 通用单助手认知声明**（非 Hermes 单 adapter，如 OpenClaw-only）：

```text
【后台助手「$nickname」】用户可能用这个名字称呼一个后台 Agent:它能做需要较长时间、
或你在实时对话里做不到的事(查用户的历史对话与长期记忆、联网查询实时信息、多步工具调用、长思考)。
你自己负责与用户的实时语音互动;遇到超出你能力、或需要较长时间的要求,在回复里自然地建议交给它
(如「这个我让$nickname帮你查一下」),不要生硬拒绝或声称完全无法处理,也不要替它执行、不要自称是它。
用户点名「$nickname」时,那句要求是给它的:你可以简短确认(如「好,我让它处理」);
它的答复会以文字显示在对话里(不会朗读)。
```

- 与现有 Hermes 模板的差异：仅措辞/换行；能力清单相同（含联网查询实时信息，2026-10-18 用户确认
  OpenClaw 也支持）。
- Hermes 单助手继续使用现有 `AGENT_AWARENESS_DECLARATION_TEMPLATE`，逐字不变。

> 同侪提示方案（曾拟为方案 b；本节的 b 已改为通用单助手模板）已按 Q4=B 否决，本期不提供文案、
> 不修改 monitor/任务书。

### 8.5 边界矩阵

| `default_adapters` / 状态 | LivaChat 声明 | 备注 |
|---|---|---|
| `["hermes"]`，hermes proactive + nickname | 现有单昵称模板，逐字不变 | 回归基线 |
| `["openclaw"]`，openclaw proactive + nickname | 通用单昵称声明（§8.4-b；含联网查询实时信息） | 修复 G1 的 OpenClaw-only 缺失 |
| `["hermes","openclaw"]`，均 proactive + nickname | 多助手模板 | monitor 之间不感知彼此（Q4=B）；重复 message/job 风险保留 |
| 双启用，Hermes 有名、OpenClaw 名为空 | 单昵称声明（Hermes 现有模板） | OpenClaw 仍会 proactive 输出但 LivaChat 无认知；启动告警（OpenClaw nickname 默认空） |
| 双启用，Hermes 名为空、OpenClaw 有名 | 声明 OpenClaw（现状会完全不声明） | 无名 adapter 不进声明，但仍会 proactive 输出 |
| 双启用，两名皆空 | 不声明 | 两个 monitor 仍运行，输出无身份认知 |
| `["hermes"]` 但 mode=reactive | 不声明（非 proactive） | reactive 回复仍以 `agent_name="hermes"` 上屏 |
| `["openclaw"]` 但 mode=reactive | 不声明 | 无 watcher/monitor |
| 双启用且 nickname 重复 | 名字去重后声明 + 告警；唯一名字对应多后端时用通用模板 | 用户点名无法消歧，建议配置唯一名；不复用 Hermes 专属能力 |
| adapter connect 失败 | 仍声明（静态产物） | 重连可恢复（Q8） |
| `default_adapters` 含未知名字 | loader 跳过，不声明 | 现状行为 |
| `default_adapters` 重复名字 | dict 合并为一个 | 顺序取首次出现 |
| `default_adapters: []` / relay 未启用 | 不声明 | relay core 不装配 |
| `proactive.enabled: false` | 声明不变（只过滤 adapter mode） | watcher/nudge **仍运行**；仅 recording.ready 分析停止（G8，现状） |
| 运行中改配置 | prompt 不变 | 需重启（现状） |

除首行外，"LivaChat 声明"列描述**设计后**行为；monitor 同侪提示按 Q4=B 不实施，重复派生风险接受（G3）。

### 8.6 测试计划（实施时）

| 用例 | 断言 |
|------|------|
| `tests/unit/app/test_agent_awareness_prompt.py` | 0/1/N 三种渲染；Hermes-only 逐字基线；OpenClaw-only 使用 §8.4-b 通用模板且包含"联网查询实时信息"；Hermes 有名 + OpenClaw 空名 → 仅声明 Hermes；Hermes 空名 + OpenClaw 有名 → 声明 OpenClaw；两名皆空 → 不声明；reactive-only 不注入；空/重复 nickname（去重+告警；重复名对应多后端用通用模板）；dialog+omni 均生效；translate 不注入 |
| `test_hermes_contract.py` / `test_openclaw.py` | monitor/任务书**不含**同侪段或 `$peer_*` 占位残留（Q4=B 负向钉住）；单 adapter 输出与基线一致 |
| `tests/integration/agent_relay/test_application_wiring.py` | 双 adapter 装配 → dialog/omni 含两名字；OpenClaw-only → 含其名；hermes reactive-only → 不含；`default_adapters: []` → 不含；`proactive.enabled: false` → 声明**不变**（现状钉住，见 G8） |
| `test_chatlog_watcher.py` | 同一 update 依次发给两个 proactive adapter（现状行为钉住）；`proactive.enabled: false` 时 watcher 仍运行、recording.ready 停止（现状钉住，见 G8） |
| job store 单测（Q7） | `outstanding_jobs_for_device` 返回 `agent_name`；计数语义不变；SQLite 与遗留 Redis 实现同步；**不新增 prompt 指引（字段仍随 nudge JSON 下发）** |
| `test_session_manager.py`（D-E） | 未带 metadata 的 push 按注册 adapter 名归因，不再回退 hermes |
| 全量 | `services/tests/unit` + `tests/integration/agent_relay` 全绿 |

### 8.7 决策（2026-10-18 已拍板）

| # | 决策 | 结论 |
|---|------|------|
| Q1 | 声明是否只含 proactive adapter | **A：是**（reactive-only 不能接 job_request） |
| Q2 | 空 nickname 的 proactive adapter 如何处理 | **A：不写入 LivaChat 声明 + 启动告警，不阻断**；无同侪提示，因此无 peer 计数 |
| Q3 | 重复 nickname 如何处理 | **A：声明显示名去重 + 告警，不阻断**；唯一名字对应多后端时用通用模板；无 peer 集合 |
| Q4 | monitor prompt 是否注入同侪信息 | **B：不加**（2026-10-18 拍板）；接受重复 message/job 风险 |
| Q5 | 同侪提示是否注入任务书 | **A：不注入**（Q4=B 已决定任何 prompt 都不注入同侪信息） |
| Q6 | 多助手声明是否逐项列能力 | **A：统一通用描述**（`AgentAdapter` 无能力字段）；Hermes 单助手保留现有模板；非 Hermes 单助手/多助手使用通用模板；能力清单含联网查询实时信息（Hermes/OpenClaw 均支持） |
| Q7 | `outstanding_jobs` 是否增加 `agent_name` | **A：增加**；Q4=B 后仅作 payload/运维归属，**不新增 prompt 指引**（字段随 nudge JSON 下发） |
| Q8 | connect 失败是否从声明剔除 | **A：不剔除**（已实例化即计入；静态 prompt + 重连恢复） |
| Q9 | live 推送的 `agent_id` 是否补 nickname | **A：本期不补**（保持 job 结果溯源口径） |
| Q10 | 是否加服务端跨 adapter 去重/仲裁 | **A：不做**（`fd_agent_relay_service.md` Q4 保持开放） |
| Q11 | 是否让 `proactive.enabled` 同时关闭 watcher/nudge | **A：本期不改**（G8 是既有语义问题，需单独评估 watcher 生命周期） |
| Q12 | 是否采用 §8.4 文案 | **A：采用 §8.4-a 多助手声明 + §8.4-b 非 Hermes 单助手模板；同侪提示方案不采用（Q4=B）** |

### 8.8 非目标

- 不做服务端跨 adapter 的重复 job/消息抑制或仲裁（`fd_agent_relay_service.md` Q4 仍开放）。
- 不向 monitor/任务书注入同侪信息（Q4=B），接受重复派生风险。
- 不修复遗留 Redis `JobStore` 的 hermes 硬编码键前缀（当前应用未装配该实现）。
- 不做运行中 prompt 热更新（配置变更需重启）。
- 不改变 job 的创建、归属、投递与配额语义。
- 不实现昵称上屏（`prompts_improvements.md` D8 仍搁置）。
- 不为 reactive 路径补 mission seed（既有边界，见 `prompts_improvements.md` §7-7）。

### 8.9 本轮 review 修正与决策记录（2026-10-18）

- **决策**：Q4=B（不加同侪提示），其余 Q 按推荐；文档状态改为已拍板、待实施。
- 修正"双启用只声明 Hermes"的边界：Hermes nickname 为空时现状连 Hermes 也不声明；设计后由
  `named_agents` 决定，OpenClaw 有名则声明 OpenClaw。
- 修正非 Hermes 单助手声明：OpenClaw-only 使用 §8.4-b 通用单昵称模板（措辞略简，能力清单含
  联网查询实时信息）；Hermes-only 逐字不变。**2026-10-18 用户确认 OpenClaw 也支持该能力，
  此前"未确认"的保守假设已撤销。**
- Q4=B 后移除 `proactive_agents` / `set_peer_agents` / `peer_agents_hint` / `$peer_agents_hint` 设计；
  `named_agents` 只服务 LivaChat 声明。重复 message/job 作为接受风险记录在 G3。
- 修正 `proactive.enabled` 的作用范围：它只关 `recording.ready` 分析，不关 watcher/nudge，登记为 G8/Q11；
  `recording_lookup` 才是 watcher 启动前提。
- 服务端仲裁（Q10）保持不做，`fd_agent_relay_service.md` Q4 仍开放。
- `outstanding_jobs.agent_name`（Q7）保留为 payload/运维归属信息；字段随 nudge JSON 下发，不新增 prompt 指引。
- 矩阵补充 `default_adapters: []`、`proactive.enabled: false`、双空昵称、Hermes 空/OpenClaw 有名等行；
  移除同侪提示列。

### 8.10 实施记录（2026-10-18）

| 层 | 改动 | 文件 |
|----|------|------|
| Adapter | `AgentAdapter.nickname` 默认 `""`；Hermes/OpenClaw 返回各自 `_cfg.nickname` | `services/agent_relay/adapters/{base,hermes,openclaw}.py` |
| 模板 | 新增 `NamedAgent`、`AGENT_AWARENESS_GENERIC_TEMPLATE`、`AGENT_AWARENESS_MULTI_TEMPLATE`、`render_agent_awareness()`；Hermes 单助手模板逐字保留 | `services/session/agent_job_ingest.py` |
| 装配 | `_named_background_agents()`：实际装载的 proactive adapter + 非空 nickname；空名告警排除、重复名去重告警；`_with_agent_awareness(prompt, agents)` 按 0/1/N 渲染；dialog + omni 注入，translate 不注入 | `services/app/application.py` |
| Q7 | `outstanding_jobs` / `outstanding_jobs_for_device` 增加 `agent_name`（SQLite 与遗留 Redis 实现同步）；session 级查询一并补充 | `services/agent_relay/job_store_sqlite.py`、`job_store.py` |
| D-E | push callback 注册时绑定 `adapter.name`；`_on_adapter_push` 缺 metadata 时回退绑定名，不再硬编码 `"hermes"`；metadata 仍优先；`ChatlogWatcher.set_push_callback` 同步改为 4 参（adapter_name 在前） | `services/agent_relay/session_manager.py`、`chatlog_watcher.py` |
| 不做 | Q4=B：monitor/任务书不注入同侪信息；Q9：live 推送不补 `agent_id`；Q10：不新增服务端仲裁；Q11：`proactive.enabled` 语义不变 | — |
| 配置注释 | Hermes/OpenClaw nickname 注释补充"多 proactive adapter 时进入 LivaChat 声明" | `services/agent_relay/config.py` |
| 文档 | D1/注入条件修订 + §9 追加；fd Q4 指向本节；hermes v2/openclaw 设计文档登记"声明由 LivaChat 认知声明覆盖" | `docs/prompts_improvements.md`、`docs/fd_agent_relay_service.md`、`docs/hermes_adaptor_design_v2.md`、`docs/openclaw_adaptor_design.md` |
| 测试 | 新增/更新 awareness 单测（0/1/N、Hermes 逐字基线、OpenClaw-only 通用模板、重复名、空名告警）、wiring（OpenClaw-only、双 adapter、Hermes 有名/OpenClaw 空名、双空、reactive-only、G8）、adapter nickname、job store `agent_name`、push 归因回退、watcher 4 参回调（adapter_name 透传） | `services/tests/unit/app/test_agent_awareness_prompt.py`、`services/tests/integration/agent_relay/test_application_wiring.py`、`services/tests/unit/agent_relay/...` |

**验证**

- `services/tests/unit`：**1480 passed**；
- `services/tests/integration/agent_relay`：**106 passed**；
- `ruff check`（改动涉及的代码/测试）：All checks passed。

**与方案的差异**

- Q7 额外覆盖了 `outstanding_jobs(session_id)`（方案只点名设备级查询）；字段格式一致、向后兼容，
  便于 session 维度消费方同样获得归属信息。
- **2026-10-18 追加修正**：用户确认 OpenClaw 也支持联网查询实时信息；§8.4-b 通用单助手模板与
  §8.4-a 多助手模板的能力清单已包含该项（此前"未确认"的保守假设撤销）。
- 其余按 §8.3 设计实施，无行为偏差。

## 9. Agent 输出语言（已实施，2026-10-28）

- 目标语言来源：`session.start.config.language` → `SessionContext.language` →
  `publish_session_started(language=...)` → `AgentEvent.language` →
  `_AgentSession.language` / `AgentContext.language`。utterance/ASR language
  只作 ASR 诊断元数据，不作为目标语言。
- 空/未知/非法 → `en`；App 17 个 canonical code 原样透传，不做语言枚举白名单；
  `language` 是客户端可控字段，只接受安全 token（`[A-Za-z0-9][A-Za-z0-9_-]{0,23}`），
  防止改写 prompt 结构。`zh-Hans` / `zh-Hant` 按 code 区分。
- 优先级：**用户显式语言要求 > `session.start.language` > `en`**。用户消息本身
  使用的语言不算显式要求；只有“用中文回答”“reply in English”这类明确要求才覆盖
  基线。显式要求由 agent 在 prompt 规则下执行，服务端不解析、不落库、不跨请求持久化。
- 实现方式：不翻译 prompt 模板；`agent_relay.prompt_hints.language_hint()`
  追加到 Hermes mission / job task / reactive ask，以及 OpenClaw nudge contract /
  background task / reactive ask 末尾。Hermes 每轮 nudge 在数据 JSON 前重复一条
  compact hint（仅靠 mission 时，中文用户消息会让 agent 忽略目标语言）。
- Hermes 复用会话时比较 `AgentContext.language` 与 `conn.mission_ctx.language`，
  不一致则 `_restate_mission`；OpenClaw 每轮 nudge 自带 hint，无需 restate。
- 重复 `session.started`（同 session_id）更新已有 `_AgentSession` 和已启动
  `ChatlogWatcher` 的 `AgentContext`。
- 面向用户字段：`message.text`、`job_request.summary`、`spec.task`、
  job_status summary、后台任务最终 `text`、reactive ask 回复。JSON 字段名和
  action 值保持英文。
- 不新增 language 配置、不改协议/DB schema；job task/summary 在创建时固化语言，
  恢复/补投沿用原文本，不重新翻译。OpenClaw 崩溃恢复重发时原 job 语言未知，
  background task 不注入语言 hint。
- 真实探针（2026-10-28，en/zh-Hant/ja/ar/th/vi）：Hermes/OpenClaw 的 monitor
  summary/spec.task、reactive ask、后台任务最终 text 均使用目标语言；
  脚本 `services/scripts/probe_agent_multilingual.py`。
