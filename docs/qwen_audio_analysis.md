# Qwen3.8-Omni-Flash 音频分析模型迁移 —— 问题分析与改造设计

> **状态**：**已实施（2026-09-18）**——D1 锁定 `enable_thinking=false`；配置/代码/测试/默认值
> 全部落地（§7），lvdata-mcp 测试套件 **162 项全绿**。**最大优势是价格**：全量实测 60s 片段成本
> ≈ 3.5 的 **1/11**（¥0.0110 → ¥0.00105/段；3.5 按百炼模态分列价重算，§5.4）。
> **追加（2026-09-18）**：audio provider 已接入 **workspace_id / 业务空间专属域名**（§10，
> 只 audio、只 dev），开发与测试计划全部落地。
> **Date**: 2026-09-18
> **Scope**: `tools/lvdata-mcp/`（provider 配置 + audio 分析路径 + 测试）·
> `tools/data-analysis/`（探针/旧脚本）· `configs/dev/lvdata_mcp.yaml`
> **上游文档**：[Qwen-Omni 使用指南](https://docs.bailian.console.aliyun.com/zh/model-studio/qwen-omni) ·
> [qwen3.8-omni-flash 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-8-omni-flash) ·
> [千问AI平台模型页（含官方定价）](https://www.qianwenai.com/models/qwen3.8-omni-flash)
> **关联**：`docs/data/lvdata_mcp_service_v2.md`（服务设计）· `docs/data/lvdata_audio_analysis.md`（分析口径）·
> `docs/qwen_livetranslate_realtime.md`（同方法论先例：先探针后实施）
> **结论文先**：3.8-omni-flash 相比 3.5-omni-flash 只有一处**行为性差异**需要处理——
> **默认开启思考**（`reasoning_effort=xhigh`），实测延迟 ×2~4、输出 token ×2~3（管线 `concurrency: 1`
> 串行会放大）；显式关闭即可（探针验证 `enable_thinking=false` 与 `reasoning_effort=none` 均被
> 3.5/3.8 接受）。其余：`modalities=["text"]`、system role、`json_object` 全部被 3.8 接受且
> **12/12 次调用 JSON 守约**（含思考开启时）。价格（千问AI平台官方页）：**统一价 输入 ¥0.8 /
> 输出 ¥2.7 / 缓存命中 ¥0.1（每百万 tokens）**，不按模态分列；60s 片段成本 ≈ 现状的 1/8。

## 1. 背景与目标

- lvdata 音频分析的 provider 模型为 `qwen3.5-omni-flash`（`configs/dev/lvdata_mcp.yaml:43`，
  2026-03-30 发布），经 OpenAI 兼容端点流式调用，输出结构化 JSON（schema = `SEGMENT_KEYS`）。
- 目标：切换至 `qwen3.8-omni-flash`（2026-09-17/18 发布，基于 Qwen3.8-Flash-Next 架构）。
  **最大优势是价格**：统一价 ¥0.8/¥2.7（每百万 tokens），全量实测 60s 片段成本 ≈ 现状的 **1/11**
  （§5.4）；次收益：限流大幅放宽（TPM 100K→2M）、上下文 262K→1M、模型代际更新。
- 非目标：vision/summary/embedding provider 不动；对外 MCP 接口与 schema 不动；
  存量数据是否重跑另议（见 D3）。

## 2. 调用链（迁移后现状）

| 环节 | 位置 | 说明 |
|---|---|---|
| 模型配置 | `configs/dev/lvdata_mcp.yaml` audio 段 `model: qwen3.8-omni-flash` | **实际生效点**（迁移前为 `qwen3.5-omni-flash`，见 §1） |
| 默认值 | `tools/lvdata-mcp/src/lvdata_mcp/config.py:31` | VENDOR_DEFAULTS dashscope audio = `qwen3.8-omni-flash`（yaml 显式配置时仅兜底） |
| 端点/头 | 同上 yaml audio 段 | workspace 专属域名 + `workspace_id`（§10） |
| 调用路径 | `analysis/audio.py` `AudioAnalyzer._analyze_one` → `_call_audio` | input_audio（base64 data URL）+ 文本 prompt，streaming |
| 请求固定参数 | `providers.py:104-113`（`LlmClient._call`） | `stream=True` + `include_usage` + `response_format={json_object}` + **`modalities=["text"]`（dashscope 硬编码）** + 可选 `extra_body.enable_thinking` |
| 思考参数现状 | `_call_audio` **不传** | 3.5-Omni 无思考模式（dashscope 文档中 3.5-Omni 归非思考分组），现状正确 |
| 模型名消费 | 审计 JSON + `analysis_runs.model` 字段 | 切换后新记录写新模型 ID，可据此区分代际 |

**先例参考**：vision 路径（qwen3.7-flash）已有「关思考 + 400 降级」的成熟模式——
`analysis/vision.py:407-419`（filter）与 `:453-463`（extract），配套测试 `tests/test_vision.py:324`
（`test_vision_enable_thinking_400_degrades`）。audio 路径可直接复刻。

## 3. 官方 API 差异（3.5 → 3.8）

| 项 | 3.5-omni-flash（现状） | 3.8-omni-flash | 影响 |
|---|---|---|---|
| **思考模式** | 不支持 | **默认开启，`reasoning_effort` 默认 `xhigh`**；`none` 关闭；`enable_thinking` 兼容（实测） | **核心**：不处理则延迟/成本上升（§4） |
| 结构化输出 | 能力表标"不支持"（现靠 prompt 约束 + schema 缺失重试 + normalize 兜底） | 官方结构化输出清单未列本模型，但实测 `json_object` 被接受且守约（12/12） | 兜底机制保留即可 |
| 输出模态 | Text + Audio | **仅 Text** | 无影响（只取文本）；`modalities=["text"]` 实测被接受 |
| 上下文 | 262,144（最大输入 196K / 输出 65K） | 1M（最大输入 991K / 输出 131K） | 无感（片段 60s） |
| System message | 可用（现状用 system role 传 prompt） | 实测被接受（无 400） | 无影响 |
| 价格（北京） | 文本/图/视频入 ¥2.2、音频入 ¥18、文本出 ¥13.3（每百万 tokens） | **统一价：输入 ¥0.8 / 输出 ¥2.7 / 缓存命中 ¥0.1** | 成本 ≈ 1/8（§5） |
| 限流 | RPM 60 / TPM 100,000 | RPM 30K / TPM 2M | 批量吞吐大幅放宽 |
| 架构 | Qwen3.5-Omni | Qwen3.8-Flash-Next（多模态 MoE） | — |

## 4. 真机探针结果（2026-09-18）

### 4.1 方法与工具

- **脚本**：`tools/data-analysis/probe_qwen_structured_output.py` 新增 `--audio-matrix` 模式
  （模型 × 思考形态矩阵；复用生产 prompt/schema：`lvdata_mcp.analysis.audio.AUDIO_SYSTEM_PROMPT`
  + `missing_schema_keys` 校验）。
- **音频**：2 个真实 60s 录音片段（`/opt/lvdata/**/*.ogg`），ffmpeg 转 16k 单声道 wav（与生产
  `audio_sample_rate: 16000` 一致），base64 内联。
- **请求形态**：与生产同款——`stream=True` + `include_usage` + `response_format=json_object`
  + `modalities=["text"]` + system role prompt。
- 每格记录：JSON 守约性（含 schema key 校验）、思考链长度、TTFT、总时延、usage。

### 4.2 结果矩阵（两轮）

| 轮次 | 模型 | 思考形态 | JSON 守约 | 思考链 | TTFT | 总耗时 | in / out tokens |
|---|---|---|---|---|---|---|---|
| 1 | 3.5 | default | ✅ | 0 | 4.48s | 7.92s | 773 / 445 |
| 1 | 3.5 | `enable_thinking=false` | ✅ | 0 | 3.35s | 7.04s | 773 / 486 |
| 1 | 3.5 | `reasoning_effort=none` | ✅ | 0 | 4.86s | 7.85s | 773 / 443 |
| 1 | **3.8** | **default** | ✅ | **3814 字** | 4.18s | **28.78s** | 809 / **1295** |
| 1 | 3.8 | `enable_thinking=false` | ✅ | 0 | 1.23s | **5.94s** | 773 / 426 |
| 1 | 3.8 | `reasoning_effort=none` | ✅ | 0 | 4.81s | 11.93s | 773 / 403 |
| 2 | **3.8** | **default** | ✅ | **2457 字** | 2.69s | 10.94s | 767 / 806 |
| 2 | 3.8 | `enable_thinking=false` | ✅ | 0 | 1.15s | **2.54s** | 731 / 101 |
| 2 | 3.8 | `reasoning_effort=none` | ✅ | 0 | 0.97s | **2.50s** | 731 / 122 |

（轮 1 的 `effort=none` 11.93s 在轮 2 复现为 2.50s，属噪声）

### 4.3 结论

1. **3.8 默认开思考实锤**：每调用多出 2400~3800 字推理链，耗时 ×2~4、输出 token ×2~3；
   叠加管线 `concurrency: 1`（串行）会放大批量耗时。
2. **两种关闭方式都被 3.5/3.8 接受且生效**（无 400、思考链归零）。
3. **JSON 12/12 守约**（含思考开启的 2 格）——官方「思考模式下 json_object 可能失效」的警示
   本次未复现；但关思考仍应做（延迟/成本）。
4. `modalities=["text"]` 与 system role 在 3.8 下均被接受，`LlmClient` 现有请求形态可原样复用。
5. 质量侧（content 长度、transcript 内容）与 3.5 相当，未见退化。
6. 关思考时 3.8 与 3.5 耗时同档（2.5~6s），说明差异完全来自思考模式而非模型本身变慢。

### 4.4 全量语料对比（2026-09-18 追补，只读）

`tools/data-analysis/compare_omni_audio.py`：**25 段全量** × 3 组（`35` / `38off` / `38on`）
+ 存量 derived JSON 参照；75 次调用、251s；**不写库、不改配置**（原始响应存
`~/temp/lvdata_omni_compare_20260918/`）。

| 组 | json 守约 | schema 完整 | 思考链 | 平均延迟 | p95 | avg in/out | 成本/段 |
|---|---|---|---|---|---|---|---|
| 35 | 25/25 | 25/25 | 0 | 3.10s | 6.8s | 704/218 | ¥0.0110 |
| **38off** | 25/25 | 25/25 | 0 | 4.08s | 8.6s | 704/180 | **¥0.00105** |
| 38on | 25/25 | 25/25 | 3729 字 | 21.11s | 60.5s | 740/1173 | ¥0.0038 |

判定一致率（vs 3.5）：

| 对比对 | silent 一致 | value 一致 | transcript 相似度（均值/最低） |
|---|---|---|---|
| 35 vs 38off | 24/25 | 23/25 | 0.735 / 0.000 |
| 35 vs 38on | 25/25 | 21/25 | 0.759 / 0.023 |
| **35 重跑 vs 存量（同模型基线）** | 25/25 | 23/25 | **0.925 / 0.250** |
| 38off vs 38on | 24/25 | 23/25 | 0.812 / 0.000 |

- **同模型重跑本身就不稳**（0.925 相似度、2 段 value 不一致）→ 35 vs 38off 的 0.735 含相当
  采样方差，不能全归因模型差异。
- **38on 全量佐证关思考必要性**：延迟 ×7、p95 60s。
- 已知分歧段 `01a0b212-3b18`（唯一语义级）：3.5 两次都转写出「学习态度」长段 + 英文问答、
  value=high；38off 空转写判静音；38on 听到别的内容。**原音频已送人工听音复核，结论待补**
  （见 §9 风险表）。

## 5. 官方价格（2026-09-18 查证）

### 5.1 价目

**千问AI平台模型页**（千问官方模型市场，`www.qianwenai.com/models/qwen3.8-omni-flash`）：

| 计费项 | 价格（元/百万 tokens） |
|---|---|
| 输入（统一价，不按模态分列） | **0.8** |
| 输出（统一价） | **2.7** |
| 输入（缓存命中） | 0.1 |

上下文 1M；TPM 2M / RPM 30K。

### 5.2 差分验证（排除"模板假象"）

同站点 `qwen3.5-omni-flash` 页展示的是**模态分列价**（音频入 ¥18、文本/图/视频入 ¥2.2、
文本出 ¥13.3、文本+音频出 ¥72），证明该平台会如实展示模态差异；3.8-omni-flash 页无分列，
即统一价。旁证：官方博客口径「音视频输入成本降了超 93%」与统一价相对 3.5（¥18/¥53）的
降幅（95%+）吻合。

### 5.3 百炼侧状态

`help.aliyun.com/zh/model-studio/model-pricing` **暂无该模型价格行**（模型 2026-09-17/18 刚发布，
价格页未更新）。两平台同模型定价历史完全一致（3.5-omni-flash 均为 2.2/18/13.3/72），
¥0.8/¥2.7 大概率同样适用；**以百炼控制台模型广场详情页为准**（需登录）。

### 5.4 成本与限流影响（全量实测）

25 段全量对比实测（§4.4；关思考 in≈704 / out≈180）：

| | 单片段成本（实测） | 相对 |
|---|---|---|
| 3.5-omni-flash（现状） | ¥0.0110 | 1× |
| 3.8-omni-flash（关思考） | **¥0.00105** | **≈ 1/11（-90%）** |
| 3.8-omni-flash（默认开思考） | ¥0.0038 | ≈ 1/4，但延迟 ×7（p95 60s） |

限流：TPM 100K → 2M、RPM 60 → 30K。

> 成本口径：3.5 按百炼**模态分列**价（输入音频 ¥18/M，音频 tokens = 时长×7 = 420/60s；
> 输入文本 ¥2.2/M；输出文本 ¥13.3/M）；3.8 按千问AI平台统一价（输入 ¥0.8 / 输出 ¥2.7）。
> 早期版本曾把 3.5 全部输入按音频档计（高估 ~40%，得出 1/15），已修正为 1/11。

## 6. 设计决策

### D1 思考关闭方式 —— ✅ 已锁定 `enable_thinking=false`（2026-09-18）

| 方案 | 说明 | 代价 |
|---|---|---|
| A. `enable_thinking=false`（**建议**） | `LlmClient` 已支持（`extra_body`），audio 路径零改动到 provider 层；探针在 3.5/3.8 均验证通过 | 无 |
| B. `reasoning_effort="none"` | 3.8 官方推荐的关闭方式；需给 `LlmClient` 新增顶层参数透传口 | 改动 `providers.py`，且 3.5 上该参数仅为"被忽略"（实测无报错） |

### D2 是否加 400 降级 —— ✅ 已采纳（复刻 vision 先例）

- 模式：`thinking_off = True`；`ApiError` 且 `status == 400` 且 `thinking_off` → 置
  `thinking_off = False` 重试一次（之后不再关思考）。
- **注意与既有 ogg→wav 400 降级的次序**：audio 现有重试循环里 400 同时承载"ogg 不支持"降级
  （`audio.py` `_analyze_one`）。建议次序：**先降思考、再降格式**（先 `thinking_off` 翻转，
  仍 400 且 format == ogg 才转码重试），避免两类 400 混淆。
- 依据：repo §6.4.2/§13 的"降级一次"纪律与 vision 先例；探针虽显示 3.5/3.8 都接受该参数，
  保留降级可防未来模型/快照变更。

### D3 存量数据是否重跑 —— 待用户拍板

- 审计 JSON 与 `analysis_runs` 记录了 `model` 字段；切换后新旧数据模型版本并存。
- **只读对比已完成（§4.4）**；正式重跑（写库、覆盖存量）仍待拍板。
- 若重跑：`process(force=True)` 可重置 `fail_count/状态` 后重跑（全局或限定 session）；
  60s 片段成本 ≈ ¥0.00105，量级参考：1000 片段 ≈ ¥1.05。
- 若不重跑：新旧代际混存，检索质量不均（3.8 为新一代模型）。

### D4 `config.py:31` 默认值是否同步 —— ✅ 已采纳

- 当前 VENDOR_DEFAULTS dashscope audio 默认 `qwen3.5-omni-plus`（yaml 显式配置时仅兜底生效，
  本仓库 dev 配置始终显式）。建议同步升到 `qwen3.8-omni-flash` 保持默认一致性；
  `tests/test_config.py:135/:150` 的 fixture 与断言需一并更新。

## 7. 改动清单

### 7.1 配置（必须，1 行）—— ✅ 已完成

- `configs/dev/lvdata_mcp.yaml:43`：`qwen3.5-omni-flash` → `qwen3.8-omni-flash`
  （`base_url`/`api_key` 不变；无 model 查询参数）。

### 7.2 代码（核心）—— ✅ 已完成

- `tools/lvdata-mcp/src/lvdata_mcp/analysis/audio.py`：
  - `_call_audio` 调用改为 `self.client.chat(AUDIO_SYSTEM_PROMPT, user_content,
    enable_thinking=False if thinking_off else None)`；
  - `_analyze_one` 重试循环引入 `thinking_off` 标志与 400 降级（按 D2 次序）。
- `providers.py` **不改**（D1 选 A）。

### 7.3 测试 —— ✅ 已完成（套件 162 项全绿；含 2 条新用例 + ogg 降级用例适配新次序）

- `tools/lvdata-mcp/tests/test_audio.py`：
  - 新增「`_call_audio` 传 `enable_thinking=False`」断言；
  - 新增「400 降级一次」用例（镜像 `test_vision.py:324`）。
- `tests/test_config.py`：仅当采纳 D4（默认值同步）时更新。

### 7.4 已完成配套（本轮，未提交）

- ✅ `analysis/audio.py` docstring：`qwen3.5-omni-plus` → 「模型见 providers.audio.model」
  （既有文档漂移，已获批准修正）；
- ✅ `tools/data-analysis/probe_qwen_structured_output.py`：新增 `--audio-matrix`
  （模型 × 思考形态矩阵，生产 prompt/schema 校验）。

### 7.5 文档（可选）

- `docs/data/lvdata_mcp_service_v2.md:74/:193/:236`、`docs/data/lvdata_audio_analysis.md`（多处）
  的模型名引用更新；
- `tools/data-analysis/lvdata_analyze_audio.py`（独立旧脚本，已被 MCP 取代）的 `DEFAULT_MODEL`，
  可选同步。

### 7.6 其他

- 切换后首次批量分析观察 `analysis_runs` 的 latency 与失败率（尤其确认关思考生效——
  usage 的 completion_tokens 应与 3.5 同档，而非 ×2~3）。

### 7.7 workspace_id 接入（2026-09-18 追加）—— ✅ 已完成

- ✅ `config.py`：`ProviderConfig.workspace_id` + `resolved()` 透传（防"漏传即静默丢头"）；
- ✅ `providers.py`：`LlmClient` 构造 `default_headers={"X-DashScope-WorkSpace": ws}`（未配置不带头）；
- ✅ `configs/dev/lvdata_mcp.yaml`：audio 段 `base_url` → workspace 专属域名 + `workspace_id`（两值配套）；
- ✅ 测试 3 条：resolved 透传（含缺省）、头存在/缺省两态；
- ✅ 隔离 E2E：头在场、2/2 ok（§10.4）。
- 范围外：vision/summary/embedding、staging/production 均未动（§10 范围裁决）。

## 8. 验收标准

1. **单测**：✅ 已完成——`tools/lvdata-mcp` **162 项**全绿；新增 2 条用例（关思考传参 / 400 降级）。
2. **真机（同 §4.1 口径）**：✅ 已完成——§4.4 的 `38off` 组（25 次）即为生产请求形态
   （3.8 + `enable_thinking=False`），reasoning=0、JSON 25/25 守约。
3. **端到端**：⏳ 待服务重启后跑一批真实 pending 音频（`process()`），逐条核对审计 JSON：
   `parse_ok=true`、schema 完整、`model=qwen3.8-omni-flash`、单条延迟同档（数秒级）。
4. **回归**：✅ 测试套件全绿（含 vision/summary 既有用例）。

## 9. 风险与回滚

| 风险 | 说明 | 缓解 |
|---|---|---|
| 百炼价格未确认 | 官方价格页尚未更新该模型行（§5.3） | 上线前在控制台模型广场核对；本方案动机之一（降本）不成立时再评估 |
| 新模型（发布 ~1 天） | API 行为可能微调 | 探针脚本保留，可随时复跑；降级链（思考→格式）已在 |
| 思考被误开 / **降级落点** | 延迟 ×7、成本 ×3（§4.4 实测）；串行管线放大。注意：D2 的 400 降级**本身就是"退回思考开"的路径**（不传参数 = 模型默认 xhigh），且 400 无法区分"拒收思考参数"与"格式/音频错误" | 降级已加 **warning 日志**（audio.py，使退化可观测）；上线后以 `analysis_runs` 的 latency/output_tokens 监测（关思考 ≈3~6s/≤500 tokens） |
| 输出模态仅 Text | 无影响（只取文本） | — |
| 结构化输出未列入官方清单 | 实测 12/12 守约；能力表口径滞后于实测 | 保留 schema 缺失重试 + normalize 兜底（既有） |
| **单段语义级分歧**（`01a0b212-3b18`） | 3.5 两次均转写出长段中文 + 英文问答、38off 空转写判静音、38on 听到别的内容——存在"38off 漏识别"可能 | 原音频已送人工听音复核（§4.4）；若确认漏识别，评估 prompt 调整或对疑似静音段的复核策略 |
| 回滚 | 改配置回 `qwen3.5-omni-flash`；若已按 D2 加降级不影响 3.5（实测接受该参数） | 配置级回滚，无需改代码 |

## 10. workspace_id（业务空间专属域名）接入（2026-09-18，已实施）

> 范围裁决：**只 audio、只 dev**（staging/production 无 lvdata_mcp.yaml，仅 .gitkeep）。

### 10.1 调研

**(1) chat server 侧既有模式（参照物）**

| 环节 | 位置 | 做法 |
|---|---|---|
| 配置字段 | `services/shared/config/models.py:74/:91/:117/:167/:201` | asr/llm/tts/omni/translate 各有 `workspace_id: str \| None` |
| factory 透传 | `services/llm/factory.py:23` | `workspace_id=cfg.get("workspace_id")` |
| **落地（LLM，OpenAI SDK，与 lvdata 同构）** | `services/llm/providers/alibaba_bailian.py:14-28` | `{"X-DashScope-WorkSpace": ws} if ws else None` → SDK `default_headers` |
| yaml 约定 | `configs/dev/server.yaml:93-100` | 「workspace_id 只驱动头，两值**配套使用、互不派生**」 |
| 实测经验 | 同上 `:60-61/:143-148` | ASR 缺头亦可连；TTS 缺头 401/411 —— 头按 modality 情况不同 |
| 测试先例 | `asr/test_factory.py:24`、`llm/test_factory_failover.py:48/:60` | 「漏传即静默丢头」+ 头存在/缺省两态 |

**(2) 百炼官方语义（2026-09 文档）**：业务空间专属域名 `{WorkspaceId}.{region}.maas.aliyuncs.com`，
OpenAI 兼容路径 `/compatible-mode/v1`；优势 = **超时 3600s（vs 600s）**、更高并发/隔离、协议最全、
鉴权仅限本空间；**Dashscope 域名 2026-09-30 起不再支持新特性**；Key 与域名必须配套（否则 401）。

**(3) 真机探针（2026-09-18，3 次调用，qwen3.8-omni-flash + 60s 真实音频）**

| 组合 | 结果 |
|---|---|
| workspace 域名 + 头 | ✅ OK |
| workspace 域名 **不带**头 | ✅ OK |
| dashscope 域名 + 头 | ✅ OK |

→ compatible-mode HTTP 路径上**头非必需**（与 server 的 ASR 经验一致，非 TTS 的 401/411）；
workspace 域名用现有 Key 直接可用。

### 10.2 设计（与 server 同款）

- `ProviderConfig` 增 `workspace_id: str | None`；**`resolved()` 白名单构造必须显式透传**
  （漏传即静默丢头——已加测试锁死）；
- `LlmClient.__init__`：`default_headers={"X-DashScope-WorkSpace": ws} if ws else None`（未配置不带头）；
- yaml audio 段：`base_url` 切 workspace 专属域名 + `workspace_id` 两值配套；
- 头按推荐路径保留发送（实测非必需，零成本 + 与 server 约定一致）。

### 10.3 开发与测试计划（逐步实施，全部 ✅）

| 步 | 内容 | 验收 | 状态 |
|---|---|---|---|
| 1 | `config.py`：字段 + `resolved()` 透传 | 单测：透传含 `None` 缺省 | ✅ |
| 2 | `providers.py`：`default_headers` | 单测：有/无头两态 | ✅ |
| 3 | `lvdata_mcp.yaml` audio 段切域名 + 头 | 配置加载断言（base_url/workspace_id 读出） | ✅ |
| 4 | 隔离 E2E 复跑（`~/temp/lvdata_e2e`，写库隔离） | 2/2 ok + 头在场 + 延迟同档 | ✅ |
| 5 | 文档同步（本节 + §7） | — | ✅ |

### 10.4 实施结果

- 测试：**165 项全绿**（新增 3 条：resolved 透传、头存在/缺省；并修正 2 条既有鸭子类型
  provider 缺 `workspace_id` 的假阳性失败）；
- 隔离 E2E：`base_url=…maas.aliyuncs.com/compatible-mode/v1`、客户端头
  `X-DashScope-WorkSpace=llm-1tmsqifo51cwfadr` 在场、`process` 2/2 ok、延迟 3.7~4.7s、
  输出 tokens 146/206（关思考生效）；
- 未动：vision/summary/embedding（沿用 dashscope 域名，Q1 裁决）、staging/production。
- **后续（2026-09-18 第二轮，已实施）**：vision / summary / embedding 已接入 workspace
  专属域名 + 头（worker loop 全 LLM 使用点统一），见
  [lvdata_workspace_extend.md](lvdata_workspace_extend.md)。

## 附录 A：探针用法

```bash
# 模型 × 思考形态矩阵（真实片段 + 生产 prompt/schema 校验）
env $(grep -v '^#' services/.env | grep -v '^$' | xargs) \
  .venv/bin/python tools/data-analysis/probe_qwen_structured_output.py --audio-matrix \
  --audio /opt/lvdata/<device>/<session>/audio/<segment>.ogg

# 单模型复跑（默认列表：qwen3.5-omni-flash,qwen3.8-omni-flash；
# 变体：default / ethink=false / effort=none）
  ... --models qwen3.8-omni-flash --variants default,ethink=false
```

## 附录 B：关键代码位置

| 位置 | 说明 |
|---|---|
| `configs/dev/lvdata_mcp.yaml:43` | `providers.audio.model`（迁移点） |
| `tools/lvdata-mcp/src/lvdata_mcp/analysis/audio.py` `_call_audio` / `_analyze_one` | 关思考传参 + 400 降级落点 |
| `tools/lvdata-mcp/src/lvdata_mcp/providers.py:94-113` | `LlmClient._call`：modalities/json_object/extra_body（D1 选 A 则不改） |
| `tools/lvdata-mcp/src/lvdata_mcp/analysis/vision.py:407-419, 453-463` | 「关思考 + 400 降级」先例（照抄模板） |
| `tools/lvdata-mcp/tests/test_vision.py:324` | 降级用例先例（新用例模板） |
| `tools/lvdata-mcp/tests/test_audio.py:30, 361` | fake client 已带 `enable_thinking=None` 签名 |
| `tools/lvdata-mcp/src/lvdata_mcp/config.py:31` | VENDOR_DEFAULTS 默认值（D4） |
| `tools/data-analysis/probe_qwen_structured_output.py` | `--audio-matrix` 探针（§4 复现入口） |
