# LLM 传输迁移 Responses API —— LivaChat 设计（v3）

> 状态：**设计待 review（未实施）**。2026-09-13。
> 范围：`services/llm/providers/`（新增一个 Responses 实现 + bailian provider 切换）+
> `configs/dev/server.yaml`（extra_body 微调）+ `services/llm/service.py` /
> `services/app/application.py`（生命周期）+ 单测 + 基准脚本。
> **不动**：chat 路径的 openrouter/deepseek、orchestrator、协议层、客户端。
> 结论先行：Responses 流式实测——**跨轮复用成立**（connects 恒 1、pool_idle=1）；
> 关思考、视觉、多轮历史、参数映射全部通过（§2）。两个必须显式处理的陷阱：
> ① 流内 `response.failed` 事件（HTTP 200、SDK 不抛）必须映射为 ProviderError，
> 否则静默空回复；② `store` 默认留存语义，须显式 `store=false`。
> 机理（SDK 源码已核）：流以 `[DONE]` 为 break 点 + `finally` 关响应——chat 流永远
> 命中 break（提前关闭、连接丢弃）；Responses 流无 `[DONE]`，迭代至 wire EOF →
> 连接归还池。

## 1. 背景与验收目标

- 现状：provider 每轮新建并关闭客户端；实测每轮付出完整 TCP+TLS（~100-140ms/轮，
  本机 dev 日志口径）。
- 根因（源码核实，openai 3.1.0）：`_streaming.py:63/173`（sync/async 两条
  `if sse.data.startswith("[DONE]"): break`）+ `:219` `finally: await response.aclose()`
  （注释原文："Ensure the response is closed even if the consumer doesn't read all data"）。
  chat completions 的流以 `data: [DONE]` 收尾 → 永远提前 break → body 未读尽 →
  httpx 丢弃连接。**Responses 流无 `[DONE]` 哨兵** → 迭代自然走到 wire EOF →
  连接归还池（§2 R1 实证）。
- 验收目标：
  1. 跨轮复用（门禁：复用矩阵 connects 恒 1、`pool_idle ≥ 1`）；
  2. 首 token 语义不变（5s 超时作用于首个内容增量；failover 行为不变）；
  3. 错误语义不退化：错误码 1003 行为不变，且流内失败可见（failed/error 事件映射）；
  4. 正常完成路径 `GeneratorExit`/逐轮握手 debug 噪声消失。

## 2. 实测依据（2026-09-13，venv 直连 workspace 专属域名）

| # | 探针 | 结果 |
|---|---|---|
| R1 | 复用矩阵：Responses 流式，3 连发 / 间隔 8s（缓存 client + `keepalive_expiry=55`） | **connects 恒 1、pool_idle=1**（对照实测：chat 路径 connects 1→2→3、pool_idle=0） |
| R2 | 关思考：`extra_body={"enable_thinking": false}` | reasoning 事件 = 0；首内容 0.95s（冷）/ **0.476s（热）** |
| R3 | 视觉：`{"type":"input_image","image_url":"data:image/jpeg;base64,..."}` | 通过（"绿色 正方形"） |
| R4 | 错误通道 | 坏 key → **HTTP 401 由 SDK 抛出**；坏模型 → **HTTP 200 + 单个 `response.failed` 事件、SDK 不抛** |
| M | 参数映射：`instructions` + 多轮 `input`（user/assistant）+ `max_output_tokens` + `temperature` + `top_p` | 全部接受；历史记忆正确（"2+2=4，再加3" → "7"） |
| S | `store=false` 接受性 | **被接受**（流正常 `response.completed` 收尾）；不传亦接受（服务端留存行为客户端不可观测 → 显式关闭） |
| F | `response.failed` 载荷结构 | `{"response":{...,"error":{"code":"server_error","message":"Model not found: ..."}}}` → 提取路径 `response.error.code/.message` |
| P | ALPN / h2 | workspace 端点 **ALPN 协商 h2**；但 `h2` 包**未安装**（引入需新增依赖并整体复测，见 §5-1 备选） |
| 文档 | 百炼《OpenAI Responses 接口兼容》 | 新路径 `/compatible-mode/v1/responses`（旧 `/api/v2/...` 停维护）；业务空间专属域名支持；示例模型 qwen3.8-max，实测 qwen3.8-flash 全项通过 |

流对象用法（实测）：`stream = await client.responses.create(..., stream=True)`；
迭代 typed events；文本取 `event.type == "response.output_text.delta"` 的 `event.delta`；
事件序列以 `response.completed` 收尾；迭代结束即 wire EOF
（`stream.response.is_closed=True` 且连接回池）。

## 3. 设计

### 3.1 总览

- **chat 路径保持不动**：openrouter/deepseek 无 Responses 端点，继续使用
  `openai_compat.OpenAICompatProvider`（行为零变化）。
- **Bailian 切换到独立实现**：新增 `services/llm/providers/bailian_responses.py`，
  承载 Responses 传输；`AlibabaBailianLLMProvider`（构造签名不变）改用它；
  factory/orchestrator 零改动。

```
AlibabaBailianLLMProvider（构造签名不变：base_url/api_key/model/vision_model/extra_body/workspace_id）
  └─ BailianResponsesLLM（新实现）
       ├─ 惰性缓存 httpx client（keepalive=55s）                ← 复用载体
       ├─ stream(): 载荷映射 → await responses.create(stream=True) → 事件映射 → yield content
       │            （首 token 5s wait_for 作用于映射后生成器；failed/error 事件 → ProviderError）
       └─ aclose()：关缓存 client（幂等）
```

### 3.2 新实现 `services/llm/providers/bailian_responses.py`（~150-190 行）

**载荷映射**（自现有 chat 消息语义改写为目标格式）：

| 现有（chat） | Responses 目标 |
|---|---|
| `role="system"` 消息 | `instructions=<str>`（取第一条 system；**存在且非空才传**，无 system 时省略参数） |
| `{"role":"user","content":"text"}` | `input=[{"role":"user","content":[{"type":"input_text","text":...}]}]` |
| user 消息带 images | content parts 追加 `{"type":"input_image","image_url": data_url}`（`detail` 实测可省） |
| `{"role":"assistant","content":"text"}` | `{"role":"assistant","content":[{"type":"output_text","text":...}]}` |
| `max_tokens`（gen_opts） | `max_output_tokens` |
| `temperature` / `top_p` | 直传 |
| `extra_body`（config） | `enable_thinking` 直传；`enable_search` 移除（chat 面参数，Responses 下不适用） |
| **model 选择**（含 `vision_model` 切换） | **原样保留**：带图且配了 `vision_model` 时切模型（与现 chat 路径同语义；现役 config 未配 vision_model，行为不变但代码路径不得丢弃） |
| —（新增） | **`store=false`**（S 实测被接受）：本管线无状态多轮（历史自携），禁止服务端留存响应状态 |

gen_opts 白名单：仅 `max_tokens→max_output_tokens`、`temperature`、`top_p` 三项映射，
其余键丢弃（现役 `generation.model_dump()` 恰为此三项，行为等价）。

**事件映射**（迭代 typed events）：

| 事件 | 处理 |
|---|---|
| `response.output_text.delta` | `yield ev.delta`（= 首 token 计数点）；**空 delta 跳过**（provider 契约 "Never yield empty strings"） |
| `response.failed` | **raise `ProviderError("LLM stream failed: <error>", LLM_UNAVAILABLE)`**；错误信息取 `ev.response.error.message`（缺则 `.code`，再缺则事件摘要）——字段路径按 F 实测 |
| `error`（顶层） | 同上映射为 ProviderError（兜底：部分兼容实现以顶层 error 事件报告失败） |
| `response.incomplete` | 正常结束（= 截断语义，等同 chat 的 `finish_reason=length`；debug 日志） |
| `response.reasoning_text.*` | 跳过（thinking 应已关；保留跳过以兼容） |
| 其余（created/in_progress/output_item.*/content_part.*/done/completed 等） | 忽略（前后向兼容，不 raise） |

**其余要点**：

- 惰性缓存 `httpx.AsyncClient`：`limits=Limits(keepalive_expiry=55.0, max_connections=100,
  max_keepalive_connections=20)`。**Timeout 无需在 client 上显式设置**：SDK 按请求注入
  自身 `self.timeout`（默认 connect 5s / read·write·pool 600s），覆盖注入 client 的
  默认值（openai 3.1.0 `_base_client.py:611` 实证；:615-616 对 legacy httpx client
  做 timeout 归一化）。keepalive **limits 是 client 级、必须设置**。
  Flavor：始终 classic `httpx`（pyproject 直接依赖）；openai 3.1.0 对 legacy httpx
  注入有显式兼容分支（`is_httpx2_sync_client`、`normalize_legacy_httpx_timeout`，
  `_base_client.py:72` 已核）——**无 httpx/httpx2 探测问题**。
- 首 token 5s：`wait_for(agen.__anext__(), 5.0)` 作用于**事件映射后的生成器**——
  即首个 `output_text.delta` 的超时；不得作用于原始 SDK 事件流（`response.created`/
  `in_progress` 先于内容快速到达，作用于原始流则超时形同虚设）。thinking 被配置关闭，
  首内容延迟与既有路径同量级（R2）；若未来开启 thinking，需改为"首个内容事件超时"
  语义再评估（写入 docstring 注记）。
- 错误映射两级：HTTP 状态错误（4xx/5xx）由 SDK 在 `create()`/首次读取时抛出 →
  现有 `except Exception → ProviderError("LLM stream creation failed: ...")` 包装保留；
  流内 `response.failed` / 顶层 `error` → 见上表（**必须有回归锁**）。
- 取消/中断：生成器被 `aclose()`/取消时，SDK 流收尾（body 未读尽 → 连接丢弃），
  与现状语义一致；正常读完 → 连接回池（R1）。

### 3.3 provider / factory 切换

- `services/llm/providers/alibaba_bailian.py`：`AlibabaBailianLLMProvider` 保留类名与
  构造签名，内部改为持有 `BailianResponsesLLM` 并暴露同一 `stream()` 接口
  （或等价的最薄适配）；`supports_vision=True` 不变。
- `services/llm/factory.py`：无需改动（按名构造）。

### 3.4 配置（`configs/dev/server.yaml`）

- `base_url` 不变（SDK 自动追加 `/responses`）。
- llm `extra_body`：`{ enable_thinking: false, enable_search: false }` →
  **`{ enable_thinking: false }`**（附注释：enable_search 为 chat 面参数，Responses 下不适用）。
- model 不变（qwen3.8-flash 现役；qwen3.8-max-0902 同样支持）。

### 3.5 生命周期

- `BailianResponsesLLM.aclose()`（幂等）→ `LlmService.aclose()`
  （`getattr` 容错，chat 路径 provider 无此方法不受影响）→
  `application.py` lifespan teardown 在 `handler.shutdown()` 之后调用（连接全关后再收池）。

### 3.6 测试设计

- **改写**：`tests/unit/llm/test_openai_compat_stream.py` 中针对 bailian 的用例 →
  改为 patch `AsyncOpenAI.responses`（或 provider 的 client factory），构造 typed-event
  假流；`test_factory_failover.py` 的 `TestWorkspaceHeader` 保持（client 构造与
  default_headers 不变）；openrouter/deepseek 相关用例零改动。
- **新增**（`tests/unit/llm/test_bailian_responses.py`）：
  1. 载荷映射：system→instructions（含"无 system 不传"）、图片→input_image、
     max_tokens→max_output_tokens、多轮 user/assistant 顺序、**`store=false` 随请求发出**、
     **vision_model 配置时带图切模型**；
  2. 事件映射：delta→token 序列；**空 delta 被过滤**；reasoning/created 等忽略；
     **`response.failed` → ProviderError（回归锁，取 response.error.message）**；
     **顶层 `error` → ProviderError**；`response.incomplete` 正常结束；
  3. 首 token 5s 超时（断言作用于首个 delta 而非 created 事件）；
  4. 空流（仅 created/completed 事件）→ 零 token 正常结束；
  5. `aclose()` 幂等 + client 只建一次（跨两次 stream）。

## 4. 语义对齐清单（现状 chat/SDK → Responses/SDK）

| 场景 | 现状 | 迁移后 |
|---|---|---|
| 半开/陈旧空闲连接 | 不存在（每轮新连） | 复用后可能命中 → SDK `max_retries=2` 透明重试（保持） |
| 建连失败 / 401 | SDK 抛 → ProviderError → failover | 不变（R4：401 同抛） |
| 首 token 超时 | 5s `wait_for` → ProviderError | 不变（作用于首个内容 delta） |
| 流中途断 | SDK 抛 → ProviderError | SDK 抛（EOF 截断）→ 同；另加 failed/error 事件通道 |
| **流内失败（坏模型等）** | chat：HTTP 4xx 抛 | **Responses：HTTP 200 + `response.failed` → 显式 raise ProviderError（新增）** |
| **服务端状态留存** | chat：无此面 | **Responses：`store=false` 显式关闭（新增）** |
| barge-in 打断 | `llm_iter.aclose()` → 连接销毁 | 同路径 → 响应半读关闭 → 连接丢弃 |
| failover 切换 | `_aclose_gen` 收尾 | 不变（各 provider 独立池） |

## 5. 风险与对策

| # | 风险 | 评估 | 对策 |
|---|---|---|---|
| 1 | **方案前提：端点 Responses 流尾不发 `[DONE]`**——厂商若日后补发（"更兼容 OpenAI"），SDK 回到提前关闭路径，**功能无损、复用静默退化** | 低-中 | 复用矩阵门禁常驻（§6-1）+ release notes 关注；退化为现状而非故障。备选兜底：引入 `h2` 依赖 + `http2=True`（ALPN 已证 h2，需整体复测） |
| 2 | `store` 默认留存（服务端行为客户端不可观测） | 中 | 显式 `store=false`（S 实测被接受） |
| 3 | `response.failed`/`error` 未识别 → 静默空回复 | **高（不做必踩）** | §3.2 显式映射（字段路径按 F 实测）+ 回归锁 + §6 故障注入 |
| 4 | Responses 兼容面限制（文档："非列表模型仅基础兼容"） | 低（qwen3.8 系列在列；本管线只用 text/图/流式） | 复用矩阵门禁覆盖全部所需能力 |
| 5 | thinking 默认开 | 中（不关则语音链路延迟/成本失控） | config 强制 `enable_thinking:false`（R2 实证）+ docstring 注记 5s 超时与 thinking 的互斥语义 |
| 6 | 事件/字段演进（新增事件类型） | 低 | 未识别事件一律忽略（仅 failed/error 白名单 raise） |
| 7 | classic httpx 注入依赖 SDK 兼容分支 | 低（3.1.0 已核：分支存在且实测可用） | 真实行为以 venv 为准；单测 mock 覆盖 |
| 8 | 回归面（openrouter/deepseek/translate/lecture） | 低 | chat 路径代码零改动；translate 走同一 `self._llm.stream` 自动受益 |

## 6. 验证计划

1. **复用矩阵门禁**（开发期脚本）：Responses 流式 3 连发/8s → connects 恒 1、
   `pool_idle ≥ 1`；
2. **首 token A/B**：`test_llm_real.py --turns/--gap`，改前/改后对比（期望接续轮
   -80~-140ms，且冷启动不劣化）；
3. **真机**：3-4 轮对话——日志逐轮无 `connect_tcp`（首轮除外）、无 1003；
   开视频一轮（图片经 `input_image`）；barge-in 打断一轮后正常；
4. **故障注入**：把 model 临时改为不存在 → 客户端应收到 1003（验证 failed 事件映射
   不再静默）；
5. **回归**：`tests/unit` + `tests/integration` 全绿；
6. **噪声**：正常完成路径 `GeneratorExit` 与逐轮握手 debug 行消失（早退路径除外）。

## 7. 改动清单（量级）

| 文件 | 内容 | 量级 |
|---|---|---|
| `services/llm/providers/bailian_responses.py` | 新增：Responses 传输实现（载荷/事件/缓存 client/aclose） | ~150-190 行 |
| `services/llm/providers/alibaba_bailian.py` | 切换内部实现（构造签名不变） | ~10 行 |
| `services/llm/service.py` | `LlmService.aclose()` | ~8 行 |
| `services/app/application.py` | lifespan 挂 `llm_service.aclose()` | ~5 行 |
| `configs/dev/server.yaml` | extra_body 去 `enable_search` + 注释 | ~3 行 |
| `services/tests/unit/llm/*` | 改写既有 + 新增 `test_bailian_responses.py` | ~150 行 |
| `services/scripts/test_llm_real.py` | `--turns/--gap` 基准 | ~30 行 |

净新增（不含测试）~180-220 行；无协议/编排层改动；回滚=单 commit 还原
（base_url 与 model 均未变，无配置回滚动作）。

## 8. 参考

- 百炼文档《OpenAI Responses 接口兼容》：
  `https://docs.bailian.console.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api`
  （新路径 `/compatible-mode/v1/responses`；业务空间专属域名支持；主站镜像 help.aliyun.com）。
- SDK 源码锚点（server venv openai 3.1.0）：
  `_streaming.py:63/173`（`[DONE] break`）、`:219`（`finally: aclose()`）；
  `_base_client.py:611`（按请求注入 timeout）、`:615-616`（legacy httpx timeout 归一化）、
  `:72`（`is_httpx2_sync_client` 兼容分支）。
- 实测记录：R1-R4/M/S/F/P（本机 venv 直连 workspace 端点，2026-09-13）。
- 相关代码：`services/llm/providers/openai_compat.py:52-117`、
  `services/llm/providers/alibaba_bailian.py`、`services/llm/service.py:34-89`、
  `services/app/application.py:287`。
