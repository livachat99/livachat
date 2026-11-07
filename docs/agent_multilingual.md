# Agent Relay / Adapter 多语言（最小化分析与设计）

> **状态**：已实施并完成真实探针（2026-10-28）。
> **范围**：server 端 `agent_relay`/adapters 及其用户可见输出；不包含客户端、协议 schema、
> omni/translate 主链路、main chat 的 awareness 文案本身。
> **关联文档**：`docs/agent_relay_prompts.md`、`docs/hermes_adaptor_design_v2.md`、
> `docs/openclaw_adaptor_design.md`、`docs/session_start_improvments.md`、
> `docs/server_multilingual.md`。

---

## 0. 结论先行

agent relay/adapters 的 4 个 prompt 模板、提示片段和部分注入主 LLM 的声明仍为中文；
面向用户的输出语言由 `session.start.config.language` 驱动的 language hint 控制，
不需要维护 17 份 prompt。

实现要点：

| 项 | 设计 |
|---|---|
| 目标语言来源 | **基线**只用 `session.start.config.language`（App UI 17 canonical code）→ `SessionContext.language` |
| 传递 | `publish_session_started(language=...)` → `AgentEvent.language` → `AgentContext.language` |
| 空/未知 | 默认 `en`（与 App/主动对话默认一致）；**不使用 utterance/ASR language 作为目标** |
| 语言优先级 | **用户明确指定语言 > `session.start.language` > `en`**；用户要求由 agent 在 prompt 规则下遵守 |
| prompt 本地化方式 | **不翻译 4 个中文 prompt 模板**；在渲染结果末尾追加一条通用语言指令 |
| agent 用户可见输出 | `message.text`、`job_request.summary`、`spec.task`、job_status summary、后台 job 最终 `text`、reactive ask 回复 |
| 内部 prompt | 保持中文（LLM 内部理解）；只约束面向用户的输出字段 |
| 服务端翻译 | **不做**；不调用翻译模型，不对 agent 结果后处理 |
| App 16/17 支持 | 接受 17 个 App UI code；不做语言枚举白名单，仅做安全 token 校验（非法 → `en`）；`zh-Hans`/`zh-Hant` 按 code 原样下发 |
| 非 agent 输出 | `AGENT_JOB_PREFIX` 已改为语言中性的 `[Agent job result]`；`AGENT_JOB_PROMPT_DECLARATION` / `AGENT_AWARENESS_*` 仍为中文内部注入文本，暂不本地化（见 §6） |

口径说明：App UI 有 17 个 canonical locale；translate 的具体候选是 16 个（`zh + 15`）。
本设计按 App UI 的 17 code 透传，不做白名单；同一套 code 链路自然覆盖 16 个 translate
候选。若产品只要求 translate 16，不需要另加枚举。

核心判断：**agent 本身是 LLM，最便宜的多语言支持不是复制 17 份 prompt，而是把目标语言
作为一条输出约束传给已有中文 prompt。** 真实探针确认需在每轮请求处重复该约束
（仅放 system prompt 时会被中文用户消息带偏）。

用户显式语言要求是唯一优先级高于 App 默认语言的因素：例如 `session.start.language="zh-Hans"`
但用户说“用英文回答”时，agent 的用户可见输出应使用英文。服务端不解析、不持久化该偏好，
由 agent 在可看到的 chatlog/task/user text 范围内遵守；下一次没有显式要求时回到
`session.start.language` 基线。

---

## 1. 现状事实（以 codebase 为准）

### 1.1 硬编码中文/语言的位置

| 位置 | 内容 | 用户可见性 |
|---|---|---|
| `services/agent_relay/prompts/hermes_monitor.md` | 全中文 monitor 角色/契约 | 间接：决定 agent 的 decision 和 `message.text` |
| `services/agent_relay/prompts/job_task.md` | 全中文任务书 + “最终 text 是发给用户的成品文案” | 决定 background job 最终 `ai_text` |
| `services/agent_relay/prompts/openclaw_monitor.md` | 全中文 NUDGE 契约 | 决定 OpenClaw monitor 输出 |
| `services/agent_relay/prompts/openclaw_background_task.md` | 全中文任务书 | 决定 OpenClaw job 最终 `ai_text` |
| `services/agent_relay/prompt_hints.py:12-95` | 时区、名字、Web 页面提示全中文 | 注入 mission/task prompt，影响 agent 语言 |
| `services/agent_relay/adapters/hermes.py:100-104` | `_CLARIFY_AUTORESPONSE` 中文 | agent 内部澄清回复 |
| `services/agent_relay/adapters/hermes.py:1281` | 恢复 job 的 fallback task 中文字符串 | background agent 输入 |
| `services/session/agent_job_ingest.py:21-76` | `[Agent job result]`（语言中性）；`AGENT_JOB_PROMPT_DECLARATION`、`AGENT_AWARENESS_*` 仍为中文 | history/chatlog 标记 + main chat/omni system prompt |
| `configs/dev/server.yaml:277-310` | `agent_relay` 无语言字段；nickname 中文 | 配置层无语言选择 |
| `JobStatusItem.summary` / `PushMessage.text` | 由 agent 生成，无 language 字段 | 用户可见；语言随 agent |

`NOOP`、JSON 的 `action/summary/spec/task` 等英文是**协议/契约键**，不是用户语言。

### 1.2 language 管道（已闭环）

- `session.start.config.language` → `SessionContext.language` →
  `publish_session_started(language=...)` → `AgentEvent.language` →
  `_AgentSession.language`（空/非法 → `en`）→ `AgentContext.language` →
  `prompt_hints.language_hint()`。
- `AgentEvent.language` 在 `user.utterance` 上是 ASR 诊断元数据，不参与目标语言；
  只有 `session.started` 的值进入目标语言链路。
- utterance 先于 session.started 到达时，临时会话默认 `en`；session.started
  到达后覆盖。
- adapters 已读取 `AgentContext.language`：Hermes mission / 每轮 nudge / job task /
  reactive ask；OpenClaw nudge contract / background task / reactive ask。
- Hermes 复用会话时比较 `mission_ctx.language`，变化则 `_restate_mission`；
  OpenClaw 每轮 nudge 自带 hint。
- `PushMessage`、`JobStatusItem`、`AgentResponseChunk` 没有 language 字段；
  结果文本语言在生成时固化。
- `AGENT_AWARENESS_*` / `AGENT_JOB_PROMPT_DECLARATION` 仍是装配期注入 main
  chat/omni system prompt 的中文声明（D4）。

### 1.3 现有配置能力

- `HermesAdapterConfig.monitor_prompt_path`、`OpenClawAdapterConfig.monitor_prompt_path` /
  `task_prompt_path` 可以全局替换 prompt 文件，但**只有一份**，不能按语言选择。
- 无 `language` / `target_language` 配置项；无 16/17 语言枚举。
- nickname 是产品配置（`"小黑"`/`"小龙虾"`），属于名字，不是语言支持。

---

## 2. 目标与非目标

### 2.1 目标

1. agent relay 的**用户可见文本**按 App UI 语言生成：
   - monitor `message.text`
   - `job_request.summary`
   - `job_request.spec.task`
   - `JobStatusItem.summary`
   - job 最终 `ai_text`
   - reactive ask 回复（`mode=both` 时）
2. 基线目标语言从 `session.start.config.language` 进入 `AgentContext`，不依赖 ASR language。
3. 空/未知语言默认 `en`；App 的 17 个 canonical code 原样支持。
4. **用户在当前对话/任务中明确指定语言时，优先遵守用户指定**；该要求由 agent 按 prompt
   规则处理，服务端不做 NLP 解析或持久化。
5. 不改 4 个中文 prompt 模板为 17 份；不新增翻译服务；不后处理 agent 结果。

### 2.2 非目标（最小化边界）

- 不把 monitor/job task prompt 翻译成 17 种语言；
- 不在 server 端翻译 agent 返回文本；
- 不改 `AGENT_JOB_PROMPT_DECLARATION` / `AGENT_AWARENESS_*` 这些内部注入文本；
  `AGENT_JOB_PREFIX` 已改为语言中性的 `[Agent job result]`（见 §6 D4）；
- 不改 job_status 的客户端 UI 文案（客户端已有本地化标签）；
- 不改主 chat/omni 的 system prompt 语言策略（chat 部分已由
  `docs/server_multilingual.md` §4.12 独立实现；omni 本期不改）；
- 不改协议 schema、DB schema、客户端；
- 不改变 agent 的工具/模型能力；
- 不在服务端解析/持久化用户语言偏好（显式语言要求只作为 prompt 规则）。

---

## 3. 设计

### 3.1 目标语言链路

最小改动：

```text
session.start.config.language (17 canonical)
  → SessionContext.language
  → AgentEventPublisher.publish_session_started(language=...)
  → AgentEvent.language
  → _AgentSession.language
  → AgentContext.language
  → prompt_hints.language_hint()
  → mission / nudge / job task / ask prompt
  → agent 生成面向用户的文本
```

规则：

- `publish_session_started` 增加 `language: str = ""`；orchestrator 只传
  `ctx.language`（来自 `session.start.config.language`），不使用 utterance/ASR language。
- `make_session_started_event` 增加 `language` 参数（`AgentEvent.language` 已存在，
  无 schema/协议变更；这是进程内模型）。
- `on_session_started` 把 language 放入 `_AgentSession` 和 `AgentContext`；
  `language` 为空/未知时落为 `en`。
- `on_user_utterance` **不更新** `_AgentSession.language`，也不做 ASR language 兜底：
  user utterance 的 `AgentEvent.language` 仍可保留作 ASR 诊断元数据，但不参与
  agent 目标语言。
- 如果 utterance 先于 session.started 到达并创建了临时 session，session.started
  到达时以 session.start language（空 → `en`）覆盖/补全目标语言。
- **重复 `session.started`（resume 同一 session_id / 同连接重发）不能直接 early-return**：
  必须用新的 `session.start.language` 更新已有 `_AgentSession`；若 `ChatlogWatcher` 已存在，
  同步更新其 `AgentContext.language`（或重建 watcher）。否则 watcher 会一直用旧 `en`/
  旧语言 nudge，Hermes 的 `_restate_mission` 也可能无法触发。
- `_start_watcher` 必须使用与 `AgentContext` 相同的归一后 language（空 → `en`），
  不能把原始空串传给 watcher；否则 watcher 的 nudge context 会与 session 目标不一致。
- Hermes mission 的 language 更新在下一次 `ensure_session` 比较
  `conn.mission_ctx.language` 后通过 `_restate_mission` 生效；OpenClaw 每轮 nudge 自带
  language hint，不依赖 mission。
- 空/未知统一 `en`，与 `docs/server_multilingual.md` 的主动文本默认一致。
- 17 个 App code 原样使用；不做语言枚举白名单，避免和 App/翻译候选漂移。仅做
  安全 token 校验（`[A-Za-z0-9][A-Za-z0-9_-]{0,23}`，覆盖全部 App code）；
  含空白/引号/换行等可改写 prompt 结构的值 → `en`。`zh-Hans` / `zh-Hant`
  分别下发；agent 按 code 输出对应字形。
- **显式用户语言要求优先**：若用户在 chatlog/task/user text 中明确指定输出语言
  （如“用英文回答”），agent 的用户可见输出使用用户指定语言，而不是
  `session.start.language`。服务端不解析、不落库；该偏好不跨请求持久化，下一次
  没有显式要求时回到基线语言。
- monitor 派生 `job_request` 时，应把用户显式语言要求保留进 `summary` / `spec.task`，
  使 background job 和 job status summary 也遵守同一优先规则。

### 3.2 语言指令（不翻译 prompt）

在 `prompt_hints.py` 新增：

```python
_SAFE_LANGUAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,23}")

def normalize_language(language: str | None) -> str:
    lang = (language or "").strip()
    return lang if _SAFE_LANGUAGE_RE.fullmatch(lang) else "en"

def language_hint(language: str, *, verbose: bool) -> str:
    lang = normalize_language(language)
    if verbose:
        return (
            f'面向用户的输出语言:`"{lang}"`。用户消息本身使用的语言不算语言要求;'
            "只有用户明确要求使用另一种语言时(如“用中文回答”“reply in English”)"
            "才改用该语言,否则一律使用上述语言。所有面向用户的输出"
            "（message.text、job_request.summary、spec.task、job_status summary、"
            "后台任务最终 text、reactive ask 回复）按上述规则使用语言;"
            "spec.task 里的产出要求也必须使用该语言,不要要求最终结果改用其他语言;"
            "JSON 字段名和 action 值保持英文不变。"
        )
    return (f'面向用户输出语言:`"{lang}"`(用户消息本身的语言不算要求;'
            "仅当用户明确要求使用其他语言时才改用该语言;spec.task 的产出要求也用该语言);"
            "JSON 结构/字段名不变。")
```

真实探针结论（2026-10-28）：仅把指令放在 Hermes mission 里不够——中文用户消息会让
agent 按用户语言回复并忽略目标语言。因此 Hermes 每轮 nudge 在数据 JSON 前重复一条
compact hint；OpenClaw 的 hint 本身就在每轮契约块里。

设计要点：

- **不翻译 4 个中文 prompt**；只追加这条语言指令。
- 指令放在模板渲染结果末尾，兼容 `monitor_prompt_path` / `task_prompt_path` 的全局
  自定义 prompt（自定义文件也会自动获得语言约束）。
- 对 `zh-Hans`/`zh-Hant` 使用 code；LLM 可从 code 判断简体/繁体。若实测 `zh-Hant`
  漂移，再补名称映射（不改变整体方案）。
- 指令中明确“JSON 字段名/action 不改”，防止 agent 把 `summary`/`task` 键翻译掉。
- `spec.task` 也要求目标语言，因为 background agent 会用该 task 生成最终文案。
- 用户显式语言要求必须写进 prompt 规则的优先级；monitor 派生 job 时把该要求保留在
  `summary` / `spec.task`，避免 background job 丢失用户意图。

### 3.3 注入点

| Adapter / 方法 | 注入 |
|---|---|
| Hermes `_render_mission` | mission 末尾追加 `language_hint(context.language, verbose=True)` |
| Hermes `_restate_mission` | 使用同一 mission 渲染（已有） |
| Hermes `nudge` | 数据 JSON 前追加 `language_hint(..., verbose=False)`（每轮强化） |
| Hermes `_render_job_task` | task prompt 末尾追加 `language_hint(..., verbose=False)` |
| Hermes `_ask_impl` | 在 `prompt.submit.text` 末尾追加 `language_hint(..., verbose=False)` |
| OpenClaw `_render_nudge` | contract 末尾追加 `language_hint(..., verbose=True)` |
| OpenClaw `_render_background_task` | task prompt 末尾追加 `language_hint(..., verbose=False)` |
| OpenClaw `ask` | 在 `chat.send` 的 text 末尾追加 `language_hint(..., verbose=False)` |

Hermes 的 monitor 是 **device 级长会话**：如果同一连接内 App 语言变了，仅靠
session.create 时的 mission 会过期。最小处理：

- `ensure_session` 复用已有 Hermes session 时，比较
  `context.language` 与 `conn.mission_ctx.language`；不一致则走 `_restate_mission`
  （已有的 noop 回合机制）；
- 不新增协议/DB 字段。

OpenClaw nudge 本身每轮自包含，语言 hint 每轮注入；无需额外 restate。

### 3.4 用户可见字段覆盖

| 输出 | 产生点 | 语言约束 |
|---|---|---|
| monitor `message.text` | Hermes/OpenClaw nudge contract | 语言 hint（mission/nudge） |
| `job_request.summary` | monitor contract | 语言 hint |
| `job_request.spec.task` | monitor contract | 语言 hint |
| `JobStatusItem.summary` | monitor summary → JobStore | monitor hint 已约束 |
| job 最终 `ai_text` | background task prompt | job task hint |
| reactive ask 回复 | `ask` prompt | ask hint |
| `notify_job_started` 文案 | `job.summary` | monitor hint 已约束 |

server 不生成需要本地化的 job status 固定字符串；客户端负责 UI 标签。

所有行的语言优先级统一为：**用户显式语言要求 > `session.start.language` > `en`**。
monitor 派生 job 时必须把用户显式语言要求保留进 `summary` / `spec.task`，否则 background
job 只能看到默认语言，无法遵守用户指定。

### 3.5 示例

App 语言 `en`：

```text
面向用户的输出语言:"en"。用户消息本身使用的语言不算语言要求;只有用户明确要求使用
另一种语言时(如“用中文回答”“reply in English”)才改用该语言,否则一律使用上述语言。
所有面向用户的输出（message.text、job_request.summary、spec.task、job_status summary、
后台任务最终 text、reactive ask 回复）按上述规则使用语言;JSON 字段名和 action 值保持英文不变。
```

monitor 仍用中文理解，但 `message.text`/`summary`/`task` 应输出英文；background agent
最终 `text` 也应为英文。`zh-Hant` 则输出繁体中文。

显式覆盖示例：基线为 `en`，用户说“用中文回答” → `message.text`、`summary`、
`spec.task`、job 最终 `text`、job_status summary 都应使用中文；下一次没有显式要求时
回到基线 `en`。

### 3.6 兼容与回退

- 空/未知/非法 `language` → `"en"`（与 App/主动对话默认一致）。
- 旧客户端不传 `language`：agent 输出英文，而不是旧行为的中文；这是已确认的有意行为
  变化（D6）。
- 旧 job 记录没有 language 字段：job task/summary 在创建时已经把目标语言固化；
  恢复/补投沿用原文本，不重新翻译。
- App 语言在会话内变化：更新 `_AgentSession` 和已存在 watcher 的 `AgentContext`；
  Hermes 通过 mission restate 更新；OpenClaw 每轮 nudge 自带 hint；后台 job 保持创建时
  语言。
- 用户显式语言要求不落库、不跨请求持久化；由 agent 在当前 chatlog/task/user text
  范围内遵守。服务端不解析自然语言语言要求。
- agent 不遵守 hint（包括显式语言要求）：没有服务器兜底；见 §6 风险。

---

## 4. 变更清单（已实施）

| 文件 | 改动 |
|---|---|
| `services/session/orchestrator.py` | `publish_session_started(..., language=...)`，只传 `ctx.language`（空值由 session_manager/AgentContext 归一为 `en`） |
| `services/session/agent_publisher.py` | `publish_session_started` 增加 language；传给 factory |
| `services/agent_relay/models.py` | `make_session_started_event` 增加 language |
| `services/agent_relay/session_manager.py` | session.started 保存 language（空 → en）；重复 session.started 更新已有 session；utterance 不更新目标语言；临时 session 由 session.started 补全 |
| `services/agent_relay/prompt_hints.py` | 新增 `language_hint()` |
| `services/agent_relay/adapters/hermes.py` | mission/job task/ask 注入 hint；复用 session 时 language 变化触发 restate；job task 保留用户显式语言要求 |
| `services/agent_relay/chatlog_watcher.py` | `update_context()`：重复 session.started 时同步已启动 watcher 的 `AgentContext` |
| `services/agent_relay/adapters/openclaw.py` | nudge/background/ask 注入 hint；job task 保留用户显式语言要求 |
| `services/agent_relay/prompts/*.md` | **不需要翻译**；可选择性补一句“遵循 language hint”，非必需 |
| 测试 | prompt hint、session language 传播、adapter 渲染、job/ask 注入 |
| `docs/agent_relay_prompts.md` / 本文 | 更新最终行为与边界 |

不需要改 `configs/dev/server.yaml`；没有新增语言配置。

---

## 5. 测试覆盖

### 5.1 单测（已实现）

- `test_prompt_hints.py`：`en`/`zh-Hans`/`zh-Hant`/`ja`/`ar`/`th`/`vi` code 原样；
  空/空白/非法 token → `en`（含引号、换行注入尝试）；指令覆盖 `message.text` /
  `job_request.summary` / `spec.task` / job_status summary / 最终 text / reactive ask；
  JSON 字段名不变；用户显式语言优先。
- `test_models.py` / `test_agent_publisher.py`：`make_session_started_event` /
  `publish_session_started` language 透传。
- `test_session_manager.py` / `test_session_manager_v2.py`：
  session.started language → `AgentContext.language`；空 → `en`；
  utterance language 不改变目标语言；utterance 先到时 session.started 覆盖临时默认；
  重复 session.started 更新已有 session；已启动 watcher 的 `AgentContext` 同步更新
  （含 watcher 先以临时 `en` 启动的场景）；重复 session.started 不重复派发生命周期。
- `test_hermes_contract.py`：mission / job task 含 language hint；
  复用 session 时 language 变化触发 `_restate_mission`，相同/未知语言不触发。
- `test_openclaw.py`：nudge / background task / reactive ask 含 language hint；
  崩溃恢复重发的 background task（语言未知）不注入 hint。
- `test_hermes.py`：reactive ask 追加 language hint。

### 5.2 真实 agent 探针（已执行）

脚本：`services/scripts/probe_agent_multilingual.py`（真实 Hermes / OpenClaw
gateway，Hermes 走与生产 `nudge()` 相同的 compact hint + 数据 JSON 文本）；
产物：`/tmp/agent_multilingual_probe_{hermes,openclaw}.json`。

覆盖 en / zh-Hant / ja / ar / th / vi：

| 探针 | Hermes | OpenClaw |
|---|---|---|
| monitor `summary` + `spec.task` | 6/6 | 6/6 |
| reactive ask | 6/6 | 6/6 |
| job task 最终 text（en/ja/ar） | 3/3 | 3/3 |
| 显式覆盖（基线 zh-Hans + 英文要求） | 通过 | 通过 |
| 语言变更（同一 session_id zh-Hans → en） | 通过 | 通过 |

- `zh-Hant` 人工核对为繁体中文；`vi` 等拉丁语言仅做脚本检查，报告已留存原文。
- 英文 task 中夹有中文来源名/括注（如 `中国天气网`、`后天`）时脚本检查会报
  false，但 `spec.task` 的产出要求是英文，按人工复核判定通过。
- 探针发现并修复：仅靠 Hermes mission 指令时中文用户消息会让 agent 忽略目标语言
  → 每轮 nudge 追加 compact hint；`spec.task` 产出要求改用其他语言 →
  hint 明确要求任务书的产出语言与目标语言一致。

---

## 6. 决策与边界（已定）

| # | 决策 | 已定口径 |
|---|---|---|
| D1 | 基线目标语言用 App UI `session.start.language` 还是 utterance/ASR language | **基线只用** `session.start.language`；空 → `en`；utterance/ASR language 不作为目标语言；用户显式语言要求见 D9 |
| D2 | 是否把 4 个中文 prompt 翻译成 17 份 | 不翻译；只追加 language hint，后续按探针结果再决定 |
| D3 | agent 不遵守语言 hint 时怎么办 | 已完成真实 Hermes/OpenClaw 探针；不达标时强化 prompt（已实施：每轮 nudge 重复 hint、`spec.task` 产出语言约束），仍不引入服务器翻译 |
| D4 | `AGENT_JOB_PROMPT_DECLARATION` / `AGENT_AWARENESS_*` 中文内部文本 | 本方案不改；`AGENT_JOB_PREFIX` 已改为语言中性 `[Agent job result]`。其余声明不是 agent 输出；若要 main chat 完全语言中性，另行设计 |
| D5 | `spec.task` 是否必须目标语言 | 必须是；否则 background agent 会按中文任务产出中文结果 |
| D6 | 空 language 默认 `en` | 采用；与 App/主动对话默认一致；旧客户端会从中文变英文 |
| D7 | Hermes 长会话 language 变化 | 复用 session 时比较 `mission_ctx.language`，变化则 restate；同时更新已有 session/watcher 的 language；不新增字段 |
| D8 | job 记录是否存 language | 不存；job task/summary 在创建时已固化目标语言。若以后要“按当前语言重译历史 job”，另立需求 |
| D9 | 用户明确指定输出语言 | **优先遵守用户指定**（高于 `session.start.language`）；写入 monitor/job/ask 的语言规则；不落库、不跨请求持久化；由 agent 在当前 chatlog/task/user text 内执行，服务端不解析自然语言 |
| D10 | 重复 session.started / watcher 已启动 | 用最新 session.start language 更新 `_AgentSession` 和 watcher 的 `AgentContext`；Hermes 下一次 ensure_session restate；不新增字段 |

---

## 7. 非目标复述

- 不把 agent prompt 翻译成 17 种语言；
- 不在 server 端翻译 agent 结果；
- 不改客户端、协议 schema、DB schema；
- 不改 main chat/omni 的 awareness 文案；
- 不改 job_status 客户端标签；
- 不改变 agent 的工具/模型/任务语义。
