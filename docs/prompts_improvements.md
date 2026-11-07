# Hermes prompts 改进 #1：Agent 名字（昵称）注入 —— 分析与设计

> 来源：用户需求（2026-09-30）——"在 config file 里可以配置 hermes agent 的名字（如「小黑」），
> 在给 hermes prompts 的时候把名字加入，让 hermes 知道自己叫什么。以后用户可以直接通过这个
> 名字，显式地让 agent 做回答、执行任务等"。
> 状态：**已实施（2026-09-30）**——实施记录见 §8；方案（D1–D9 全部拍板）与逐字文案见 §3.2/§3.5。
> 评审记录：一轮 **D6 = A（点名 = 直接指令）**、**D7 = 做，升级为"双向认知"**（LivaChat 知道
> agent(nickname) 的存在与能力；agent 也知道 LivaChat 负责实时互动、自己负责长任务）、**D8 = 本期不做**；
> 二轮 **D9 = a（omni prompt 同步注入，接受 omni 无结果闭环）**。
> 范围：`services/agent_relay/`（prompt 文案 + 渲染）、`services/agent_relay/config.py`（注释）、
> `configs/dev/server.yaml`、`services/app/application.py`（D7 声明注入）、`services/session/agent_job_ingest.py`
> （声明文案常量，位置见 §3.5-(d)）、`services/tests/`；文档同步。
> Hermes 侧、lvdata-mcp、协议帧均零改动（§3.3 逐条说明）。
> 关联：[docs/agent_relay_prompts.md](./agent_relay_prompts.md)（时区注入先例：同一渲染模式）、
> [docs/hermes_adaptor_design_v2.md](./hermes_adaptor_design_v2.md) §4（mission 规范副本）/ §9 Q8（展示协调）、
> [docs/recording_data_process.md](./recording_data_process.md) §F（`agent_id` = adapter nickname 的既有裁定）。

## 1. 背景与需求

| # | 需求 | 概述 |
|---|------|------|
| R1 | **名字可配置** | config file 配置 hermes agent 名字（例："小黑"） |
| R2 | **prompts 注入** | hermes 的 prompts 里带名字，让 agent 知道自己叫什么 |
| R3 | **点名调用** | 用户用这个名字显式让 agent 回答 / 执行任务（D6-A 已拍板） |
| R4 | **双向认知**（2026-09-30 评审补充） | LivaChat（实时 LLM，名字 = LivaChat）知道 agent(nickname) 的存在与能力：长任务 / 自己不支持的事，在回复里自然地建议交给它；agent 也知道 LivaChat 负责实时互动、自己负责长任务 —— 双方分工明确，用户侧互动自然 |

R3/R4 的边界（评审明确）：**不新增投递/通知链路**——LivaChat 只是"建议"（措辞层），
实际触发仍走既有链路（用户话语落 chatlog → NUDGE → agent 按契约决定 job_request/message，§2.4）。
"LivaChat 建议 → agent 派单"靠两侧 prompt 语义对齐，不加任何新机制。

## 2. 现状核查（codebase 事实，file:line）

### 2.1 `nickname` 配置已存在，且已有一条完整链路（但用途是 provenance，不是 prompts）

| 环节 | 位置 | 事实 |
|------|------|------|
| 配置项 | `services/agent_relay/config.py:19-20`（hermes）、`:79`（openclaw） | `nickname: str = ""`；注释："Agent 昵称（agent_id）：随 job 结果透传到 chatlog 行（doc §2.5）；空 = 未配置" |
| 装配 | `services/app/application.py:196-201` | `JobResultDeliverer(..., agent_id=relay_cfg.adapters.hermes.nickname, ...)` |
| 投递 | `services/agent_relay/delivery.py:50-51`、`models.py:61-62` | `PushMessage.agent_id`（昵称）；未配置恒 `""` |
| 落盘 | `services/agent_relay/publisher.py:104-119` → `services/session/agent_job_ingest.py:58-60` → `services/recording/sink.py:66-68` → `services/recording/chatlog.py:44-73` | job 结果行（history.jsonl）带 `agent_name`(="hermes") / `agent_id`(昵称) / `job_key` |
| 读侧 | `tools/lvdata-mcp/src/lvdata_mcp/analysis/chatlog.py:78-79` | lvdata MCP 返回的 message dict 已含 `agent_name`/`agent_id`（agent 自查历史时可见昵称） |

**结论**：名字的"配置 → 落库 → 读侧"链路已就绪；`configs/dev/server.yaml:244-264` 的 hermes 块
**目前没有 `nickname` 键**（保持默认 `""`，即"未启用"态）。env 覆盖：
`LIVACHAT__AGENT_RELAY__ADAPTERS__HERMES__NICKNAME`（`config.py:103-108` 前缀 + nested delimiter）。

### 2.2 缺口：`nickname` 没有进入 hermes 的 prompts

hermes 主 agent / 后台 agent 可见的 prompt 通道共三条，均无名字（src 核查）：

| 通道 | 渲染位置 | 现状 |
|------|----------|------|
| mission（system prompt；`session.create messages` 种子 + resume 后 `mission_restate`） | `services/agent_relay/adapters/hermes.py:1535-1553`（`_render_mission`）；模板 `prompts/hermes_monitor.md` | 占位符仅 `$path_hint`/`$timezone_hint`；无名字 |
| background 任务书（`prompt.background`） | `hermes.py:1555-1565`（`_render_job_task`）；模板 `prompts/job_task.md` | 占位符 `$chat_session_id`/`$timezone_hint`/`$chatlog_path`/`$task`；无名字 |
| NUDGE（每轮增量 JSON） | `hermes.py:1500-1514`（`_build_nudge_prompt`）；调用点 `:1186-1189` | 字段 `type/chatlog_path/chat_session_id/timezone/new_lines/truncated/outstanding_jobs`；无名字 |

**关键便利**：adapter 已持有 `self._cfg`（`hermes.py:164`，类型 `HermesAdapterConfig`），名字是
**进程级静态配置**——渲染处直接读 `self._cfg.nickname` 即可，**零透传**（对比时区需要
`session.started` 事件 → `AgentContext` 的 6 文件透传链，见 `docs/agent_relay_prompts.md` §3.2）。

### 2.3 身份字段语义：`agent_name` ≠ `nickname`（显示面现状一并核查）

| 字段 | 来源 | 现状用途 |
|------|------|----------|
| `agent_name` | adapter 类属性 `hermes.py:161`（`"hermes"`）、`openclaw.py:22` | 推送帧/chatlog/事件/job_status 的 **agent 标识**；`job_status.py:63/214`、`models.py:75` 均默认 `"hermes"`（硬编码字面量，非配置） |
| `agent_id` | 配置 `nickname`（§2.1） | 仅 job 结果行 provenance；**不上推送 wire**——`publisher.py:140-146` 白名单只透传 `source/agent_name/reply_to`，且 `packages/api-schema/s2c/ai-text.json` 为 `additionalProperties:false` |

显示面事实（与 R3 的"用户可见"相关）：

- App **会话气泡不显示任何 agent 名字**：SDK 回调签名只透出 `source`，`agent_name` 被丢弃
  （`apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/LivaChatListener.kt:53`）；
- job 状态面板**不渲染** `agentName`（`AgentJobListPanel.kt:116-148` 只显示 summary/耗时/开始时间，
  标题固定 "Agent任务 (n)"）；
- MyLife 时间线 `agent_job` 行**以 `agent_name`（"hermes"）作回落文本**
  （`MylifeRepository.kt:341`）——即现状用户可能在时间线看到 "hermes" 字样，而不是昵称；
- live nudge 产出的 `message` 回推：`session_manager.py:386-393` 取 `chunk.metadata` 的
  agent_name（hermes.py 的 nudge 未设 metadata → 恒 `"hermes"`），**且不带 `agent_id`**；
  job 结果回推则带 `agent_id`（§2.1）——两条回推路径的"身份"字段并不一致。

### 2.4 R3 现状行为（"用户点名"会发生什么）

用户说「小黑，帮我查下上周的会议纪要」时，现状链路：

1. 实时 LLM（`configs/dev/prompts/companion.txt`）**不知道任何 agent 存在**，会照常回答；
   其 prompt 明写"If the question needs live, up-to-date information … say plainly that this
   isn't supported"——即现状可能答"我不支持查询"；
2. chatlog 落 user 行 → `ChatlogWatcher`（`chatlog_watcher.py:79-82`：poll 3s /
   coalesce 2s / min interval 5s；§9 Q10 记录文件 tail 最坏 ~8s）→ NUDGE 到主 agent；
3. 主 agent 按现有契约判定（`hermes_monitor.md`）——**点名与否不改判定**："你的价值是补充
   实时 LLM 做不到的事"，没有"被点名 = 直接指令"的语义；
4. 响应回推：`message` 走 live push（`session_manager.py:395-416`，`proactive=True`，
   **无 TTS**——`fd_agent_relay_service_v2.md` §1.3 非目标）；`job_request` 走 JobStore →
   `prompt.background` → 结果经 `JobResultDeliverer` 回推。

### 2.5 实时 LLM 侧现状（D7 的落点核查）

| 项 | 位置 | 事实 |
|----|------|------|
| 名字 | `configs/dev/prompts/companion.txt:1` | "You are LivaChat, a helpful and empathetic voice AI companion."——**LivaChat 名字已在**，无需新配置 |
| 能力自述 | 同上末段 | "If the question needs live, up-to-date information … say plainly that this isn't supported — never make it up."——即现状会**直说"不支持"**，与 agent 存在的能力范围冲突（§2.4 第 1 条） |
| 注入先例 | `services/app/application.py:68-69`、`:305-312`（`_with_agent_job_declaration`）；文案常量 `services/session/agent_job_ingest.py:19-24` | dialog system prompt 加载后**追加声明**的既有模式（`[Agent job result]` provenance 声明）；**只作用于 dialog 管线**（docstring `:306-307`：translate/omni 不走） |
| 装配顺序（实施踩点） | `application.py:63-98`（prompt 加载）→ `:140-147`（relay 装配，`relay_cfg` 在此才存在）→ `:247-248`（`app.state.system_prompt` / `omni_system_prompt` 赋值） | 若在 `:69` 注入，`relay_cfg` **尚未定义**（只能读原始 dict）；**推荐注入点 = `:247` 赋值前**（`relay_cfg.adapters.hermes.nickname if relay_cfg else ""`），一次覆盖 dialog（+ omni，见 D9） |
| omni prompt | `configs/dev/server.yaml:156`（`omni.system_prompt_path` = 同一份 `companion.txt`）、`application.py:89-96` 独立加载 | omni 是与 dialog 并列的用户互动模式（`fd_pipeline_omni_mode.md`），其 prompt 与 dialog **同源文件、独立加载**；既有 job 声明**不注入**它（现状纪律） |
| **omni 的"history"与 agent 结果可见性**（2026-09-30 补查，D9 关键事实） | `services/omni/base.py:26-45`（`OmniStream`/`OmniProvider` 协议）、`services/session/orchestrator.py:2198-2214`、`services/omni/providers/alibaba_omni.py:93-143`（`_reconnect`） | ① omni **不接收我们组装的历史**：`open()` 只吃 `system_prompt/voice/modalities/generation_opts`，`OmniStream` 协议**无任何文本注入方法**（只有 feed_audio/feed_image/commit/cancel/reset/close）；上下文由 provider 侧 realtime session 自持（同连接内）。② **agent 结果在 omni 模式下连本地 history 都不写**：`record_agent_job_result` 显式 `history=None if self._omni_active else self._history`（注释：realtime API 自持上下文，写 WorkingMemory 是死写且污染 turn_idx；沿用 OmniOrchestrator 语义，设计 D7）——chatlog/chat_store 仍照写（用户翻历史可见）。③ 断线重连（`_reconnect`）只重发 `session.update`（配置），**不重放历史** → provider 侧上下文清零。**结论：omni 模式下 LivaChat 看不到 agent 的输出结果，且现协议无通道可告知它**（对照：dialog 每轮送 WorkingMemory，`[Agent job result]` 行 + job 声明 `agent_job_ingest.py:18-24/76`，可见可引用）。**精确边界（审查修正 2026-09-30）**：dialog 侧也只有 **job 结果**入 history——nudge 的 `message` 回推不带 `job_key`，`publisher.py:110-119` 的入史挂钩不触发（history/chatlog 均无该行）→ **两侧 LivaChat 都看不到 agent 的即时 `message` 回推**，dialog 的"可见"仅指 job 结果 |
| translate | `services/llm/prompt.py`（`load_system_prompt`）、`translator.txt` | 独立角色（翻译器），与 LivaChat 身份无关 → 明确不注入 |
| agent 回复形态 | `fd_agent_relay_service_v2.md` §1.3（"agent 回复走 TTS"= 非目标） | agent 的 `message`/job 结果**只上屏、不朗读**；LivaChat 的回复走 TTS 被朗读——分工文案可引用该事实 |

## 3. 设计

### 3.1 总览（三层，全部复用既有结构）

```
config（复用 adapters.hermes.nickname）
  ├─ hermes 侧：mission 渲染注入「你的名字」小节 + 点名语义（R2/R3）
  │              background 任务书注入短版名字行（同一 agent 的两条 prompt 通道身份一致）
  ├─ LivaChat 侧（D7/D9，已拍板）：dialog + omni system prompt 追加"agent 认知"声明
  │                          （同 _with_agent_job_declaration 模式，注入点见 §3.5-c）
  └─ 显示侧（D8，本期不做）：agent_id 上 wire → SDK → App（三方变更，另立文档）
```

### 3.2 文案草案（逐字，供评审；标点风格对齐现有 prompt 文件）

**`prompts/hermes_monitor.md`** —— 插在 `$path_hint` 之后、「# 本设备时区」之前：

```markdown
$agent_name_hint
```

渲染值（`_agent_name_hint(verbose=True)`）：
- 有值 →
  ```markdown
  # 你的名字

  你叫「小黑」——用户会用这个名字称呼你,无需自我介绍。用户在对话里呼唤这个名字(如「小黑,帮我查下…」)
  就是在直接给你下指令:此时必须给出响应——需要工具 / 检索 / 历史记忆的用 job_request 派生任务
  (不要凭印象直接答);能秒级回答的用 message。用户只是顺口提到这个名字、并非对你说话时,按普通素材处理。
  LivaChat(实时语音助手,负责与用户的实时互动、回复会被朗读)可能在回复里建议「交给小黑」——
  这只是它的对话措辞,不改变你的判定标准,也不要因此重复派生已处理过的请求。你的回复以文字上屏(不朗读)。
  ```
- 空值 → `""`（**整段消失，含标题**；D5）

**（审查修正 2026-09-30，两处）**：① 原稿"用户可见的称呼"不实——昵称当前**不显示**在任何 App 界面
（D8 本期不做，§2.3），改为"用户会用这个名字称呼你"；② 原稿"LivaChat 建议交接 = 直接指令,必须响应"
**会造成重复派单**：LivaChat 的回复行在 chatlog 里通常晚于 2s 合并窗（assistant 行在整段回复生成完毕
才落盘，`orchestrator.py:1362-1366`），
只会随**下一次** user 行批量进入 nudge（`chatlog_watcher.py:246-254/279`）——若第一次 nudge 已派生 job，
下一批再按"直接指令"判定就会为同一请求派生第二个 job（且首个 job 若已完成，不在 `outstanding_jobs`
里，无去重依据）。故改为"不改变判定标准、不要重复派生"。

**（改）`prompts/hermes_monitor.md` 角色段首句** —— "实时 LLM 已在直接回答用户" 改为
"LivaChat(实时语音助手)已在直接回答用户(会朗读)"，其余不动（点明对方是谁，支撑 R4 的分工表述）。
现有断言 `test_render_mission_with_path`（"NUDGE 通知" 等）不受影响。

**`prompts/job_task.md`** —— 头部「对话 session」行下加一行（同 `$timezone_hint` 位置）：

```markdown
你的名字:「小黑」(面向用户的成品文案直接作答即可,无需自我介绍)。
```

渲染值（`_agent_name_hint(verbose=False)`）：有值 → 上行；空值 → `""`。

**NUDGE 载荷**：不加名字字段（D3；理由见决策表——名字是进程级静态配置，
mission 在 `session.create` 与 `mission_restate` 两条路径都已覆盖）。

### 3.3 改动清单（逐文件）

| 文件 | 改动 | 说明 |
|------|------|------|
| `services/agent_relay/prompts/hermes_monitor.md` | 新增 `$agent_name_hint` 独立行（§3.2 位置）+ 角色段首句改提"LivaChat" | 占位符**含标题**，空值才能整段消失 |
| `services/agent_relay/prompts/job_task.md` | 新增 `$agent_name_hint` 一行 | 短版 |
| `services/agent_relay/adapters/hermes.py` | 新增 `_agent_name_hint(name, *, verbose)` 静态 helper（对照 `_timezone_hint` `:1518-1533`）；`_render_mission`（`:1548-1553`）、`_render_job_task`（`:1560-1565`）的 `safe_substitute` 各加一个 kwarg | 名字源 = `self._cfg.nickname`；**两处都不需要新参数/状态**（对比时区在 `_handle_job_request` 加过 `timezone` 参数） |
| `services/agent_relay/config.py:19-20` | 注释更新：nickname 用途扩为三处——"hermes prompts 自称 + LivaChat 声明里的称呼 + chatlog `agent_id` provenance" | 不改默认值/校验 |
| `configs/dev/server.yaml` hermes 块（`:244-264`） | 增加 `nickname: "小黑"`（+注释） | 部署配置；staging/production 由部署方按需设 |
| `services/session/agent_job_ingest.py` | 新增声明文案常量（`{nickname}` 模板，§3.5-d） | 与 `AGENT_JOB_PROMPT_DECLARATION` 同址 |
| `services/app/application.py` | 新增 `_with_agent_awareness(prompt, nickname)` 并调用（§3.5-c 注入点 `:247-248` 前） | 条件 = relay enabled 且 **hermes adapter 装载成功**（`adapters` 含 "hermes"；实施加固，§8-偏差 4）且 nickname 非空；dialog + omni 均注入（D9；omni prompt 为空/未启用则跳过）；translate 不注入（独立角色）。**2026-10-18 已扩展为实际装载的 proactive adapter 集合 + nickname 非空，见 `docs/agent_relay_prompts.md` §8。** |
| `docs/hermes_adaptor_design_v2.md` §4（`:254` 起 "Mission 内容要点" 清单） | 加一条（规范副本同步，教训：三处清单曾漏改两处） | 只加行，不回填 §5.6 历史 code block |

**零改动确认**：协议帧（`ai-text.json` / `job-status.json`）与 SDK、App、lvdata-mcp、Hermes 侧
（`hermes_adaptor_design_v2.md` §6）均不动。

### 3.4 点名语义（R3 的核心；D6 两个变体）

| 变体 | 契约文案 | 评价 |
|------|----------|------|
| **A（已拍板 2026-09-30）** | 显式呼唤（如「小黑,帮我查…」）= 直接指令：**必须响应**（message 或 job_request）；仅"顺口提到这个名字、并非对你说话"时按普通素材处理 | 与用户预期一致（"点名就能用"）；误触发风险用"呼唤口气"措辞收窄 |
| B（保守） | 点名只是提高介入意愿，仍允许 noop | 双答噪音小，但"点名了却不理"违背 R3 初衷 |

配套事实（写入文档，供评审判断取舍）：

- 点名的秒级响应同样受 watcher 延迟约束（最坏 ~8s，§2.4），"秒级"是相对后台 job 而言；
- 实时 LLM 一定会同时回答（无静默通道）——双答是结构性问题，见 D7；
- 若评审担心 ASR 变体（同音/带空格/形近字），可在 A 的文案加一句"形近、同音或带空格的写法
  也算"——**建议先不加**（无实测样本，加指令会稀释契约），真机验证后再决定。

### 3.5 双向认知（D7，已拍板"做"）

**目标语义（评审原话）**：LivaChat 负责实时和用户互动；需要长时间或 LivaChat 不支持的功能，
LivaChat 在回复里建议 agent(nickname) 来处理；agent 也知道 LivaChat 的工作、自己的工作。

**(a) LivaChat 侧声明**（逐字草案；中文，对齐既有 `AGENT_JOB_PROMPT_DECLARATION` 的中英混排先例）：

```
【后台助手「小黑」】用户可能用这个名字称呼一个后台 Agent:它能做需要较长时间、或你在实时对话里
做不到的事——查用户的历史对话与长期记忆、联网查询实时信息、多步工具调用、长思考。
你自己负责与用户的实时语音互动;遇到超出你能力、或需要较长时间的要求,在回复里自然地建议交给它
(如「这个我让小黑帮你查一下」),不要生硬拒绝或声称完全无法处理,也不要替它执行、不要自称是它。
用户点名「小黑」时,那句要求是给它的:你可以简短确认(如「好,我让小黑处理」);
它的答复会以文字显示在对话里(不会朗读)。
```

要点：① 名字用模板参数（`{nickname}`），nickname 为空时**整条不注入**（零 delta）；
② 明确"建议交给它"替代旧行为（companion.txt 末段的"直说不支持"在能力范围内不再适用）；
③ 不新增动作/机制——LivaChat 只是措辞建议，实际派单仍由 agent 侧契约完成（§1 R3/R4 边界）；
④ **能力清单口径**（含 2026-09-30 二次修正）：清单 = 本地历史检索（lvdata MCP 5 工具，只读）+
   **联网查询实时信息**（部署方确认 Hermes 侧具备该能力；具体 toolset 由 Hermes 侧配置决定，
   `hermes_adaptor_design_v2.md` §6.2）。首版曾因"仓库内无据"删掉联网，收到部署方确认后加回；
   换部署若没有联网工具，按实际 toolset 删改文案常量即可。

**(b) agent 侧**：§3.2 已含（角色段点名 LivaChat + 名字小节里的"LivaChat 建议只是措辞、不改变判定标准、
不要重复派生" + "回复以文字上屏不朗读"）。

**(c) 注入点（实施踩点）**：`application.py:247-248` 赋值前（此时 `relay_cfg` 已装配，
`:140-147`）——`_with_agent_awareness(prompt, nickname)`，与 `_with_agent_job_declaration`
（`:305-312`）并列；**不要在 `:69` 注入**（`relay_cfg` 尚未定义）。条件 =
`cfg.agent_relay.enabled` 且 hermes adapter 装载成功 且 nickname 非空
（**2026-10-18 扩展**：实际装载的 proactive adapter 集合 + nickname 非空；见 `docs/agent_relay_prompts.md` §8）
（装载失败仍声明会把用户指向不存在的 agent——实施加固，§8-偏差 4）。

**(d) 文案常量位置（备选，评审可改）**：推荐放 `services/session/agent_job_ingest.py`
（与 `AGENT_JOB_PROMPT_DECLARATION:19-24` 同址——dialog prompt 声明的既有家，零新模块）；
备选 = 新模块 `services/session/prompt_declarations.py`（职责更干净，但多一个文件）。

**约束（如实记录）**：管线**没有"LLM 保持沉默"的通道**，该声明只能让实时 LLM 的抢答变得
"不矛盾、有出口"（改成"我让小黑来"），不能让出话语权；点名 + 闲聊时仍可能出现
"LivaChat 已答 + agent 也答"的双答（观察项，§7-4）。彻底方案（点名时抑制实时 LLM）属产品级改动。

**omni 的声明边界（D9-a 已接受）**：声明同样注入 omni prompt，但 omni 下**只承诺两件事**——
知道 agent 存在/能力、建议交接；**不承诺引用结果**（omni 无 history 注入、agent 结果不写
WorkingMemory，§2.5「omni 的 history 与 agent 结果可见性」行）。实现时不要为 omni 加"结果回灌"类试探（不在本期范围，闭环登记 §7-5）。

## 4. 决策

| # | 决策 | 推荐 | 理由 / 备选 |
|---|------|------|-------------|
| D1 | 配置键 | **复用各 adapter 的 `nickname`**（2026-10-18 修订） | 单一事实源：该值已是用户可见名（chatlog `agent_id`/lvdata 读侧）；新增 `display_name` 会造成两个名字漂移。LivaChat 声明按实际装载的 proactive adapter 集合生成（`docs/agent_relay_prompts.md` §8）；Hermes 单助手保留原模板，OpenClaw-only/多助手使用通用模板 |
| D2 | mission 组织 | **独立小节 `# 你的名字`**（与「# 本设备时区」「# 长期记忆」同款） | 备选：并入「# 角色」段——但点名契约有 4 行，并入会稀释角色描述 |
| D3 | NUDGE 载荷带名字？ | **不带** | 名字是进程级静态配置（改配置需重启），mission + restate 已覆盖每会话；带字段属于无收益的载荷膨胀。对比时区带 nudge 是因为它是 per-session 值且可能中途变化（`docs/agent_relay_prompts.md` D3） |
| D4 | background 任务书注入？ | **注入（短版一行）** | 同一 agent 的产出文案身份一致；空值零成本（整行消失）。若评审认为无用可删，代码（占位符+helper）复用不变 |
| D5 | 空值行为 | **整段/整行消失** | 对比时区空值走"未上报"显式文案——因为时区影响 MCP 入参正确性（不显式会错）；名字未配置只是功能未启用，让模型自称"未配置名字"有害无益 |
| D6 | 点名语义 | **A：显式呼唤 = 直接指令（必须响应）**；纯提及不适用 | **已拍板（2026-09-30）**。见 §3.4；B（允许 noop）与 R3 初衷相悖 |
| D7 | 实时 LLM 认知 | **做——双向认知**（§3.5）：LivaChat 侧声明（知道 agent 存在/能力/建议交接）+ agent 侧分工文案（知道 LivaChat 负责实时、自己负责长任务；其建议不改变判定标准、不得重复派生——审查修正） | **已拍板（2026-09-30）**，语义按评审原话（R4）。不做的话点名/长任务场景"答不支持 + 随后 agent 回推"体验割裂 |
| D8 | 名字上屏（App/SDK） | **本期不做**，另立文档 | **已拍板（2026-09-30）**。显示昵称需 `agent_id` 进 wire（schema `additionalProperties:false`）+ SDK surface + App 渲染，属三方变更（`hermes_adaptor_design_v2.md:29/195` 的既有红线）；且 §9 Q8（proactive 展示协调）尚未拍板，应一并设计 |
| D9 | omni prompt 是否同步注入 LivaChat 声明 | **a：同步注入（dialog + omni 两处），接受 omni 无结果闭环** | **已拍板（2026-09-30）**。omni 是与 dialog 并列的用户互动模式、prompt 与 dialog 同源（§2.5）；只注入 dialog 的话，omni 模式下 LivaChat 不知道 agent，R4 在半数模式下失效。**如实记录的限制**：omni 上下文由 provider 自持、无文本注入通道，agent 结果在该模式下不写 WorkingMemory（§2.5「omni 的 history 与 agent 结果可见性」行）→ omni 下 LivaChat 只能"知道 + 建议交接"，**无法引用/评述结果**（"小黑查到了吗"答不上）；闭环候选路径（探针 `conversation.item.*` 文本注入等）登记在 §7-6，不在本次范围 |

## 5. 影响面与验收

**测试**（`services/tests/unit/`）：

| 用例 | 断言 |
|------|------|
| 新增 `test_hermes_contract.py::test_render_mission_with_agent_name` | `_adapter(nickname="小黑")` → 渲染含「你的名字」「小黑」、"job_request"/"message"、"LivaChat"（交接语义）；**空 nickname → 不含「你的名字」且无 `$agent_name_hint` 残留**（防回退漂移） |
| 新增 `test_render_job_task_with_agent_name` | 有值 → 含名字行；空值 → 不含 |
| 现有 `test_render_mission_with_path` / `test_render_job_task`（`:110/:120/:132/:148`） | 不回归（断言均为包含式；角色段措辞改动后 "NUDGE 通知" 等断言仍成立） |
| 新增 `tests/unit/app/test_agent_awareness_prompt.py` | `_with_agent_awareness(prompt, "小黑")` → 追加且含「小黑」；nickname 空 → 原样返回；空 prompt → 原样（对齐 `test_agent_job_prompt.py` 粒度） |
| 改 `tests/integration/agent_relay/test_application_wiring.py` | relay enabled + nickname 置位 → `app.state.system_prompt` 与 `omni_system_prompt` **均含**声明（D9）；nickname 空 / **hermes adapter 未装载** → 均不含；translate prompt 恒不含。**注意既有精确相等断言**（`:138` `== "base prompt\n\n" + AGENT_JOB_PROMPT_DECLARATION`）只在 nickname 空时成立——新增用例改置位 nickname 需同步该断言或用包含式 |
| integration（`test_hermes_v2_integration.py`） | 全绿（断言包含式，新增段落不破坏；Redis 用例已随去 Redis 移除，当前无需 Redis） |

**验收**：

1. dev config 设 `nickname: "小黑"` → 真机（或 integration mock）核对 `session.create` 的 system
   消息含名字小节且值一致；`session_restate` 载荷同；
2. background job 派发的 `prompt.background` 文本含名字行；
3. 还原空 nickname（git HEAD 行为）→ 三处 prompt 均无名字段、无空占位残留；
4. 真机说「小黑，帮我查下上周会议纪要」→ 观察 nudge 日志 action=job_request 与结果回推
   （D6-A 生效）；说「今天天气不错」→ 仍按现状（点名语义不误触发）；
5. （D7）真机问"帮我把这周聊过的待办整理一下"（不点名）→ LivaChat 回复**建议交给小黑**
   （而非"我不支持"），随后 agent 派 job、结果文字回推；
6. （D7/D9）dialog 与 omni（`session.change(mode=omni)`）两种模式下各验收一次第 5 条；
   omni 只验**知识层 + 交接建议**（漏注入即视为 D9 未生效）——**不验**"omni 引用 agent 结果"
   （D9-a 已接受无闭环，§2.5「omni 的 history 与 agent 结果可见性」行）；
7. 观察项（不阻断验收）：点名 + 闲聊（如「小黑，你好」）的双答观感；不点名长任务时
   LivaChat 建议交接 → agent 是否只派一次 job（重复派生即文案需加强，§7-4）。

## 6. 防漏改清单

- `configs/dev/prompts/companion.txt` **不改**——声明走装配期追加（`_with_agent_awareness`）：
  改文件会绕过 nickname 参数化（名字写死）、在未配置名字 / relay 未启用时也出现，且该文件被
  dialog / omni 两条管线各加载一份，条件化逻辑会被复制两处；
- `docs/hermes_adaptor_design_v2.md` §4 的 "Mission 内容要点" 是 prompt 的规范副本，改动须同步
  （本次 = 加一条 + 角色段措辞按 §3.2 对齐）；
- `docs/agent_relay_prompts.md` 是时区专稿，本文自成一稿、不改它；两稿共享同一渲染模式
  （`$*_hint` 占位符 + helper），后续若合并 prompt 注入族再统一；
- `config.py:19-20` 注释、`configs/dev/server.yaml` 注释同步（避免"nickname 只影响 chatlog"的过时描述）；
- `docs/recording_data_process.md` §F 不需要改（`agent_id` 来源与语义未变，仅取值从 "" 变为配置值）；
- §5.6 的 config code block 是历史设计快照，**不回填**。

## 7. 开放问题

1. **多 agent / 多设备命名**：本期全局一名。若未来 per-device 或 per-session 名字（如儿童设备
   用不同称呼），配置结构需从 adapter 级下移到 device/session 级，注入点随之带上 `AgentContext`
   ——届时参照时区的透传方案。
2. **展示协调**（既有 `hermes_adaptor_design_v2.md` §9 Q8）：proactive 文本与实时 LLM 抢答的
   客户端呈现，与 D8（名字上屏）应合并设计。
3. **点名触发时延**：~8s（§2.4）。若 R3 要求"点名后快速响应"，属 §9 Q10（chatlog 事件直发）
   的收益场景，单独评估。
4. **双答 / 承诺 / 重复派生的观感**（D6-A + D7 的观察项）：① 点名 + 闲聊时 LivaChat 的
   "简短确认"与 agent 的 message 会先后出现；② LivaChat 建议"交给小黑"但 agent 判定 noop 时
   用户空等（两侧判定标准不同源，只能靠文案逼近）；③ 同一请求被派生两次（§3.2 审查修正已用
   "不要重复派生已处理过的请求"约束，真机若仍现即文案需加强）。若实机观感嘈杂，可选调方向：
   放宽 D6（闲聊点名允许 noop）、或在 LivaChat 声明里收紧"确认"话术。**先按当前方案实施、
   真机取样后再定**。
6. **omni 的结果闭环（D9-a 明确接受的缺口，后续项）**：omni 模式下 agent 结果不进 WorkingMemory、
   provider 无文本注入通道（§2.5），LivaChat 无法引用结果——**本期接受**。后续若要闭环，候选路径
   （**均未验证，需真机探针，另立项**）：① Qwen-Omni 协议族是否存在可用的 `conversation.item.create`
   （文本注入）——`alibaba_omni.py:340` 注释显示该协议族**存在** `conversation.item.*`、当前仅用了
   audio/image buffer，能否注入文本需实测；② 退出/重开 omni 流时回灌本地 history（无 API，只能靠
   system_prompt 重申，价值有限）。
7. **reactive / both 模式的覆盖**：reactive 路径的 `session.create` 不带 mission 种子（§8 已知边界）
   → 该路径下名字/时区/契约均缺失。现网均为 proactive，需要时与"reactive 会话是否也该 seed mission"
   一并评审（不在本期）。

## 8. 实施记录（2026-09-30）

| 层 | 改动 | 文件 |
|----|------|------|
| 文案 | mission 加 `$agent_name_hint` 独立行（`$path_hint` 后）+ 角色段首句改提 LivaChat；background 加 `$agent_name_hint` 行 | `services/agent_relay/prompts/hermes_monitor.md`、`prompts/job_task.md` |
| 渲染 | 新增 `_agent_name_hint(name, *, verbose)`（§3.2 逐字；空值 → `""`）；`_render_mission` / `_render_job_task` 的 `safe_substitute` 各注入 1 个 kwarg（源 = `self._cfg.nickname`，零参数透传） | `services/agent_relay/adapters/hermes.py` |
| 配置 | nickname 注释更新为三处用途；dev 配置置 `nickname: "小黑"` | `services/agent_relay/config.py`、`configs/dev/server.yaml` |
| 声明 | 新增 `AGENT_AWARENESS_DECLARATION_TEMPLATE`（§3.5-a 逐字，`$nickname` 占位）；新增 `_with_agent_awareness(prompt, nickname)` 并在 `app.state` 赋值前对 dialog + omni 注入（条件：prompt 非空 且 nickname 非空；translate 不注入） | `services/session/agent_job_ingest.py`、`services/app/application.py` |
| 声明（追加） | **2026-09-30 二次修正**：能力清单加回"联网查询实时信息"（部署方确认 Hermes 侧具备）；单测加 `test_declaration_lists_capabilities` 钉住清单两项 | 同上 |
| 测试 | contract 新增 `test_render_mission_with_agent_name` / `test_render_job_task_with_agent_name`（含空值口径 + 无占位残留）；新增 `tests/unit/app/test_agent_awareness_prompt.py`（5 例：追加/去尾空白/空 nickname/空 prompt/占位符全代入）；wiring 新增 `TestAgentAwarenessPromptWiring` 3 例（dialog+omni 均含 / nickname 空均不含 / hermes 未装载不注入，translate 恒不含） | `services/tests/unit/agent_relay/test_hermes_contract.py`、`tests/unit/app/test_agent_awareness_prompt.py`（新）、`tests/integration/agent_relay/test_application_wiring.py` |
| 文档 | §4「Mission 内容要点」加第 6 条 + 角色段措辞对齐 | `docs/hermes_adaptor_design_v2.md` |

**与计划的偏差（4 处，均为简化/加固）**

1. 占位符实现用 `$nickname` + `string.Template`（计划书写 `{nickname}`）——对齐 prompt 家族
   `$` 约定，且文案后续若出现字面 `{` 不会崩。
2. 测试基建：`_make_app` 增 `hermes_nickname` / `omni_enabled` 形参；`_install_mocks` 补
   `omni.factory.create_omni` 桩（omni 启用用例必需，原先未启用故无桩）。
3. 计划外加 `test_empty_nickname_no_awareness`（空值口径的接线断言，防回退）。
4. **注入条件加固（自查 review 后追加）**：原实现只判 relay enabled + nickname 非空——
   relay 启用但 hermes adapter 装载失败时，LivaChat 仍会被告知"有后台助手小黑"，用户被指向
   不存在的 agent。已收紧为 `adapters` 含 "hermes" 才注入，并加 `test_hermes_not_loaded_no_awareness`
   钉住（§3.3/§3.5-c 已同步措辞）。

**已知边界（本期接受，不做）**

- **reactive / both 模式的 reactive 路径不覆盖**：`ask()` 的 `session.create`（`hermes.py:1369`）
   不带 mission 种子 → 该路径下 agent 不知道名字/时区/契约（时区特性同样如此，属既有形态）。
   部署均为 proactive（dev 配置与默认值），需要时另立设计（§7-7）。
- dev 配置置 `nickname: "小黑"` 后，**job 结果行的 `agent_id` 从 "" 变为 "小黑"**（既有链路
   `delivery → chatlog → lvdata payload`，全透传、无约束），时间线回落文本仍用 `agent_name`（"hermes"），
   不受影响。
- `fd_pipeline_omni_mode.md` 对 `omni_system_prompt` 的描述仍准确（来源文件不变，本次为装配期追加声明）。
- **名字不上屏（D8 不做）**：用户侧看不到"小黑"字样——agent 的 `message`/job 结果气泡与 LivaChat
   的回复在 UI 上无名字区分，"点名可用、看不见名"是本期已接受的产品形态（§7-2 展示协调）。

**测试结果**

- `services/tests/unit` 全量：**1129 passed / 1 skipped**；
- `tests/integration/agent_relay/test_application_wiring.py`：**8 passed**（含新增 3 例）；
- `tests/integration/agent_relay` 其余用例本机大面积失败——**环境原因**（本机 6379 Redis 未启动，
  失败栈均为 `redis.asyncio` 连接被拒；与 `docs/agent_relay_prompts.md` §7 记录的既有基线一致，
  与本次改动无关）。有 Redis 的环境应整套跑一遍。

**待办（真机验收，§5 验收 1–7）**：需 dev server + Hermes + 真机——
核对 mission/restate/background 三段含名字、空值无残留；「小黑，帮我查…」→ job_request；
不点名长任务 → LivaChat 建议交接且 agent 只派一次；omni 模式验知识层+交接建议（不验结果引用）。

## 9. 追加：多 adapter 认知声明（2026-10-18）

按 `docs/agent_relay_prompts.md` §8 实施：

- `_with_agent_awareness` 改为接收 `NamedAgent` 列表；`application._named_background_agents`
  按实际装载的 proactive adapter + 非空 nickname 生成，重复名去重并告警，空名告警并排除。
- 模板：Hermes 单助手保留 `AGENT_AWARENESS_DECLARATION_TEMPLATE` 逐字不变；OpenClaw-only 使用
  `AGENT_AWARENESS_GENERIC_TEMPLATE`（能力清单含联网查询实时信息；2026-10-18 用户确认
  OpenClaw 也支持）；多助手使用 `AGENT_AWARENESS_MULTI_TEMPLATE`。同侪提示不实施
  （Q4=B；monitor/任务书不变）。
- 测试：`tests/unit/app/test_agent_awareness_prompt.py` 覆盖 0/1/N、重复名、空名告警、
  OpenClaw-only 通用模板与 Hermes 逐字基线；wiring 覆盖 OpenClaw-only / 双 adapter / 空名 /
  reactive-only / `proactive.enabled=false`（G8 现状）。
