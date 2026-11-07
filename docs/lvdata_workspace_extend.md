# lvdata-mcp workspace_id 接入扩展（vision / summary / embedding）—— 调研与改造设计

> **状态**：**已实施（2026-09-18）**——代码/配置/测试/真机验证全部落地（§8），
> lvdata-mcp 测试套件 **176 绿**。
> **Date**: 2026-09-18
> **Scope**: `tools/lvdata-mcp/`（providers / vec / config + 测试）· `configs/dev/lvdata_mcp.yaml`
> **关联**：`docs/qwen_audio_analysis.md` §10（audio 轮先例与官方语义调研）·
> `docs/data/lvdata_mcp_service_v2.md`（服务设计，§4.2/§4.3 配置）·
> `docs/asr_bailian_improvements.md`（chat server 侧 workspace 约定来源）
> **结论先**：代码层 plumbing 在 audio 轮已做成**能力无关**（`ProviderConfig.workspace_id` +
> `resolved()` 透传 + `LlmClient` default_headers），vision（video stage）/ summary（day_summary
> stage）**零代码改动**、仅 yaml 切换即生效；唯一代码缺口是 **embedding**（`vec.py` 自建
> `AsyncOpenAI` 不认 workspace_id）。本轮 = 补 embedding 代码 + 三段 yaml 切端点 + 测试/探针/
> 文档。**模型不动**（保持 qwen3.7-flash / qwen3.7-text-embedding，裁决见 §1）。

## 1. 背景、目标与裁决

- 背景：audio provider 已于 2026-09-18 接入业务空间专属域名 + `X-DashScope-WorkSpace` 头
  （commit ac5f5d9，`docs/qwen_audio_analysis.md` §10），范围裁决为「只 audio、只 dev」。
  官方语境：业务空间专属域名为**推荐生产路径**（超时 3600s vs 600s、协议最全、鉴权限本空间）；
  公网 dashscope 域名 2026-09-30 起不再支持新特性——其余 LLM 使用点需要跟上。
- 目标：把 workspace 专属域名 + 头扩展到全部剩余 LLM 使用点：
  **video stage**（vision provider）、**day_summary stage**（summary provider）、
  **worker loop flush 组的 embedding**（embedding provider）。
- 本轮裁决（2026-09-18，用户确认）：
  - **Q1 范围**：纳入 embedding——它是唯一需要改代码的能力（`vec.py` 自建 client）。
  - **Q2 模型**：保持现状（vision/summary = `qwen3.7-flash`，embedding =
    `qwen3.7-text-embedding`），仅切端点 + 头；模型升级留作后续独立动作（先探针后实施）。
  - **Q3 存量数据**：**不重跑**——只切端点，模型与输出语义不变，历史派生结果
    （events / 派生 JSON / 向量）不受影响；不执行 force 全量重分析（对比 audio 轮
    「切模型 → force 全量重跑」的裁决，本轮无此必要）。
- 非目标：模型升级与选型对比；worker loop / pipeline / watcher 调度逻辑（**不涉及 provider
  构造**，见 §2）；staging/production（无 lvdata_mcp.yaml，仅 .gitkeep）；对外 MCP 接口与
  数据 schema。

## 2. 调研结论（根因证据）

| 层 | 现状 | 证据 |
|---|---|---|
| 通用 plumbing | **已就绪且能力无关**：`ProviderConfig.workspace_id` 字段、`resolved()` 白名单透传、`LlmClient` 构造 `X-DashScope-WorkSpace` 头（仅 dashscope vendor） | `config.py:53` / `config.py:68` / `providers.py:64`（`dashscope_headers` 唯一判定点，`:83` 消费） |
| vision（video stage） | client 构造 `cfg.providers.vision.resolved("vision")` → **yaml 配 workspace_id 即自动生效** | `analysis/vision.py:269` |
| summary（day_summary stage） | 同构：`cfg.providers.summary.resolved("summary")` | `summary/day.py:89` |
| embedding（flush 组） | **唯一代码缺口（实施前）**：`DashScopeEmbeddingProvider` 自建 `AsyncOpenAI`，仅传 base_url/api_key；`build_provider` 也不透传 workspace_id——已接入（§8） | `vec.py:32-56` / `vec.py:70-85` |
| worker loop / pipeline | 只调度 stage（watcher `_work_loop` → `pipeline._dispatch`），**不构造任何 provider**；三个分析器的 client 注入即全部 LLM 调用点 | `watcher.py:153-208` / `pipeline.py:100-142` |
| yaml | vision/summary/embedding 三段曾为公网 `dashscope.aliyuncs.com`、无 workspace_id（仅 audio 已切）——已统一切换（§8） | `configs/dev/lvdata_mcp.yaml:53-71` |
| 测试耦合 | 测试不读 dev yaml（tmp 手写 yaml）→ yaml 改动无测试连锁 | `tests/test_config.py` 全文件 |
| 既有护栏 | LlmClient 头三态单测（配/未配/非 dashscope vendor）；workspace 透传单测 | `tests/test_providers.py:86-118` / `tests/test_config.py:162-183` |
| 探针可复用性 | video 探针脚本经 `load_config(--config)` 读同一 yaml、走 `cfg.providers.vision.resolved()` → 指向临时 yaml 即探测新端点 | `scripts/probe_video_filter.py:85,101` / `scripts/probe_video_extract.py:112` |

**结论**：video/day_summary 两 stage 只需 yaml；embedding 需补代码（§3.1）；无其他隐藏调用点。

## 3. 设计

### 3.1 代码

**a. `providers.py`——头构造收敛为单点**

```python
def dashscope_headers(vendor: str, workspace_id: str | None) -> dict[str, str] | None:
    """X-DashScope-WorkSpace 头构造（唯一判定点）：仅 DashScope 语义。"""
    if workspace_id and vendor == "dashscope":
        return {"X-DashScope-WorkSpace": workspace_id}
    return None
```

`LlmClient.__init__` 改用该函数（行为逐字节不变；既有三态测试即重构护栏）。

**b. `vec.py`——embedding 接入**

```python
class DashScopeEmbeddingProvider:
    def __init__(self, model=..., base_url=None, dim=1024, api_key=None,
                 vendor: str = "dashscope", workspace_id: str | None = None):
        ...
        self._client = AsyncOpenAI(base_url=..., api_key=key,
                                   default_headers=dashscope_headers(vendor, workspace_id))

def build_provider(settings: LvdataMcpConfig) -> EmbeddingProvider | None:
    ...
    return DashScopeEmbeddingProvider(..., vendor=p.vendor, workspace_id=p.workspace_id)
```

设计取舍：vendor 判定经 helper 单点（"仅 dashscope 带头"规则只写一处），`build_provider`
保持纯透传；备选（gate 放 `build_provider`）会把规则复制成两处，不取。

背景注记（防"抽象一致性"追问）：`build_provider` 历史上就忽略 vendor 构造（始终
`DashScopeEmbeddingProvider`，仅借用其 base_url）——本次唯一 vendor 相关行为 =「非
dashscope 不带头」。另同步 class docstring（`vec.py:33`，口径含 workspace；项目先例：
audio 轮开发计划 S1.6 专列 docstring 同步项）。

**c. `config.py`——启动日志可观测**

`effective_summary()` 的 provider 摘要加 `workspace_id`（启动日志可见实际生效端点；4 个
provider 都可能带，值本身非敏感——base_url 已含同一串）。audio 轮未加，本轮补上。

> **2026-09-28 反转（用户决定）**：启动摘要里 **`base_url`/`api_key`/`workspace_id` 全部移除**
> ——三者一起看就是"内部端点 + 账号标识 + 凭据前后缀"，而日志会被采集/转发。摘要只留
> `vendor`/`model`（embedding 另加 `dim`/`min_vec_score`，见 `docs/data/lvdata_mcp_tools.md` §15.5）。
> 回归护栏：`tests/test_config.py::test_effective_summary_omits_secrets_and_endpoints`。

### 3.2 yaml（configs/dev/lvdata_mcp.yaml）

vision / summary / embedding 三段：`base_url` 切
`https://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` +
`workspace_id: llm-1tmsqifo51cwfadr`（两值配套、互不派生）。模型不动。

**注释收敛**：约定性注释（两值配套/互不派生、缺头行为）上提到 `providers:` 顶部一次，
四段（含 audio 段既有约定注释）各自只留 `workspace_id` 行——避免未来改约定要改 4 处；
模型专属注释（如 audio 段的 3.8 价格）留在原段。示例：

```yaml
providers:
  # 业务空间专属域名与 workspace_id 两值配套、互不派生（约定同 chat server 的
  # asr/llm/tts）；workspace_id 只驱动 X-DashScope-WorkSpace 头，未配置不带头。
  audio: ...        # 3.8 价格等模型专属注释保留在段内
  vision:
    vendor: dashscope
    base_url: https://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
    api_key: "${DASHSCOPE_API_KEY}"
    workspace_id: llm-1tmsqifo51cwfadr
    model: qwen3.7-flash
  # summary / embedding 与 vision 同构（仅 model 不同）
```

### 3.3 行为不变性

未配置 `workspace_id` 时，代码改动后行为与今天**逐字节一致**（不带头）→ 三段可独立
yaml 级回滚。

## 4. 边界矩阵

| 场景 | 行为 |
|---|---|
| 未配 workspace_id | 不带头（现状；yaml 回滚路径） |
| 非 dashscope vendor | 不带头（helper 单点判定，测试锁死） |
| embedding 整块为空 | `build_provider` → None，离线模式（仅 FTS）不变 |
| workspace 域名拒收 3.7 系模型 | 启动不校验（provider 无启动期模型探测，现状语义）；首轮调用重试 3 次后 ApiError → stage failed + `files.fail_count`（既有容错路径） |
| compatible-mode 缺头 | audio 实测可连（头非必需）；本着头按约定保留发送（零成本、与 chat server 一致） |
| `llm_timeout_s: 300`（客户端） | 不变；workspace 域名服务器侧 3600s 只多不少 |
| embedding BATCH_SIZE=10 | 不变（模型侧单请求上限，与端点无关） |
| 探针脚本 | 经 `load_config(--config)` 读同一 yaml，自动跟随新端点（无需改脚本） |

## 5. 测试计划

### 5.1 单测（每站代码 + 对应测试，交付即全绿）

| # | 文件 | 用例 |
|---|---|---|
| 1 | `tests/test_providers.py` | `dashscope_headers` 三态直接单测；既有 LlmClient 三态断言不动（重构护栏） |
| 2 | `tests/test_config.py` | workspace 透传扩展到 vision/summary/embedding；缺省 None 断言扩展到全能力；`effective_summary` 加 workspace_id 断言（既有 `test_effective_summary_masks_api_key` 非全字典断言、已验不受影响） |
| 3 | `tests/test_vec.py` | `build_provider` 三态：配 workspace_id → `provider._client.default_headers` 含头；未配 → 无头；非 dashscope vendor → 无头（形态对齐 test_providers） |
| 4 | 全量回归 | `pytest tools/lvdata-mcp/tests/ -q`（当前基线 **174**，实测 `--collect-only`；audio 轮的 165 已因后续视频两轮重构增长 → 全绿） |

### 5.2 真机探针 / 隔离 E2E（实施后、提交前）

| # | 内容 | 验收 |
|---|---|---|
| D1 | 配置加载断言（离线）：`load_config` 读切后 yaml，打印四 provider 的 base_url/workspace_id，并核对客户端 `default_headers` 含头 | 四段读出与 yaml 一致 + 头在场。**头的验证统一收在 D1**——两个 video 探针不打印配置/头，"头在场"在 D2 不可观测 |
| D2 | video 探针：`scripts/probe_video_filter.py` + `probe_video_extract.py --config <tmp yaml>`（真实帧；从 inventory DB 只读取件，**依赖库中已有 video 文件/事件**，空库会空跑） | **workspace 域名服务 qwen3.7-flash**：200 + parse_ok + 延迟同档（R1 前置拦截点；头在场见 D1） |
| D3 | summary 探针 | 无现成 v2 探针脚本 → 并入 D5 |
| D4 | embedding 冒烟：`build_provider` + `embed(["x"])`（真 key） | 返回 1024 维向量（`/embeddings` 子路径真实可达，R3） |
| D5 | 隔离 E2E（沿用 audio 轮做法：`~/temp/lvdata_e2e`，写库隔离）跑 video / day_summary / embed 三 stage | 三 stage 全 ok **且 day_summary 确发生 LLM 调用**（`days/ok` 计数非零 + 日志证据，防假阳性，见下） |

**D5 的 day_summary 假阳性防线**（全 ok 数据集下 stage 会「全 ok 但零 LLM 调用」，不能只看 stage status）：

- `generate(force=True)` 只重置重跑 **非 ok** 行（`day.py:307-313`、`_all_dirty`）——ok 日不进 force 队列；
- discover 路径被 debounce 挡：`_debounce_ok` 要求当日最新事件年龄 ≥ `summarize_quiet_s`（`day.py:292-301`，dev 默认 600s）；
- 即便入队，`_generate_one` 对「已 ok 且无新事件」的日直接 skipped（`day.py:365-372`）。

前置二选一（并把证据写入验收）：
(a) temp yaml 置 `summarize_quiet_s: 0` 且数据含待总结事件的日；或
(b) 隔离库造一个非 ok 日（`status='pending'`）后 `trigger(day_summary, force)`——force 路径
（`_all_dirty` → `_generate_one`）不经 `_debounce_ok`，任一 quiet 配置下均可跑。

## 6. 改动清单与风险

### 6.1 改动清单

| 文件 | 改动 | 规模 |
|---|---|---|
| `src/lvdata_mcp/providers.py` | 抽取 `dashscope_headers`；LlmClient 改用 | ~10 行 |
| `src/lvdata_mcp/vec.py` | provider 增 vendor/workspace_id 参数 + 头；build_provider 透传；class docstring 同步（vec.py:34） | ~8 行 |
| `src/lvdata_mcp/config.py` | `effective_summary` 加 workspace_id | ~1 行 |
| `configs/dev/lvdata_mcp.yaml` | vision / summary / embedding 三段切端点 + workspace_id；约定注释收敛到 `providers:` 顶部 | 4 段（含 audio 段注释收敛） |
| `tests/test_providers.py` / `test_config.py` / `test_vec.py` | §5.1 用例（含 effective_summary 断言） | ~45 行 |
| `docs/data/lvdata_mcp_service_v2.md` | §4.2 yaml 示例 + §4.3 schema/vendor 表同步 | — |
| `README.md`（tools/lvdata-mcp） | provider 配置行补 workspace_id 说明 | — |
| `docs/qwen_audio_analysis.md` | §10.4 "未动：vision/summary/embedding" 加后续注记指向本文档 | — |
| `docs/lvdata_workspace_extend.md`（本文档） | 实施后回填结果（§8） | — |

交付形态：单 commit，消息风格同 ac5f5d9（`feat(lvdata): …`）。

### 6.2 风险

| # | 级别 | 风险 | 缓解 |
|---|---|---|---|
| R1 | 高 | **workspace 专属域名是否服务 3.7 系模型无仓库内证据**（该域名现役证据只有 3.8 系：3.8-omni-flash / 3.8-max-0902 / livetranslate-3.8） | D2/D4 前置探针；若拒收 → yaml 回滚，或升级模型需再裁决（届时出选型对比） |
| R2 | 中 | 公网 dashscope 域名 2026-09-30 起不再支持新特性 | 本次迁移即为此铺路；拖后则新特性缺失 |
| R3 | 低 | embedding 端点切换后 `/embeddings` 子路径兼容性与维度/批次行为变化（workspace 域名对 embeddings 路径无仓库内证据） | D4 冒烟（真实调用 + 1024 维 + BATCH_SIZE=10 不变） |
| R4 | 低 | `dashscope_headers` 重构引入 LlmClient 回归 | 既有 test_providers 三态断言即护栏 |

回滚：三段 yaml 各自独立可回滚；代码改动向后兼容（§3.3）。

## 7. 实施顺序与出口条件

1. **代码 + 单测**：providers helper → vec → config；§5.1 全绿（基线 174）。
2. **yaml 切端点**：三段（不变量：模型 ID 不动）。
3. **真机验证**：D1 → D2 → D4 → D5（D2 是 R1 的拦截点，先于 D5）。
4. **文档同步**：v2 §4.2/§4.3、README、qwen_audio_analysis §10.4 注记 + 本文档回填。
5. **提交**：单 commit。

出口条件：单测全绿（基线 174）+ D2/D4 探针通过 + D5 三 stage ok **且 day_summary 有 LLM 调用证据** + 文档同步完成。

## 8. 实施结果（2026-09-18）

**代码 + 单测**：`providers.py` 新增 `dashscope_headers`（唯一判定点，`LlmClient` 改用）；
`vec.py` embedding provider 增 vendor/workspace_id → `default_headers`（`build_provider`
透传）+ class docstring 同步；`config.py` `effective_summary` 加 workspace_id。
测试套件 **176 绿**（基线 174 + 新增 2：`dashscope_headers` 三态、`build_provider`
三态；另扩展 workspace 透传全能力与 `effective_summary` 断言）。

**yaml**：vision / summary / embedding 三段切 workspace 专属域名 + `workspace_id`；
约定注释收敛到 `providers:` 顶部一次（audio 段既有约定注释一并收敛）；模型不动。

**D1（离线）**：四 provider 读出 workspace 域名 + workspace_id；`LlmClient`×3 +
embedding provider 的 `default_headers` 均带头；`effective_summary` 四段可见 workspace_id。

**D2（video 探针，R1 拦截点）**：`probe_video_filter.py` 2/2 窗口 ok（usage 2.9w/1.7w
input tokens；同类窗口调用实测 12–15s，见 D5 审计 latency_ms=15039/12150）、
`probe_video_extract.py` 4/4 关键帧 parse ok（含 place 判定）
——**workspace 域名服务 qwen3.7-flash 实证**。两探针进程退出时 stderr 有 httpcore2
连接池 async-generator 收尾噪音（`RuntimeError: generator didn't stop after athrow`）
——最小单次调用可复现、exit=0、所有结果已完整输出：客户端未显式 close 的既有现象，
与本改动/端点无关。

**D4（embedding 冒烟）**：`embed(["…"])` → 1×1024 维、0.74s、
model=qwen3.7-text-embedding——`/embeddings` 子路径在 workspace 域名可达（R3 解除）。

**D5（隔离 E2E）**：全新副本 `~/temp/lvdata_e2e_d5/root`（`cp -R /opt/lvdata`，
源库零写入——源库同一时段的新 round 行来自在跑 dev 服务，非本次脚本）+ temp 环境
`SUMMARIZE_QUIET_S=0`（防线 option a）：

| stage | 结果 | 证据 |
|---|---|---|
| video（force） | 38 帧 / 2 窗口，ok=2 failed=0（62s） | `analysis_runs` 42 行 kind=video、model=qwen3.7-flash、含 usage/latency |
| day_summary（discover，quiet=0） | `days=1 ok=1 skipped=0`（2026-09-15 重生成，输入 35 事件） | **确有 LLM 调用**（防线验收项）；generated_at 刷新 |
| embed（force） | 120 chunks | 全量重嵌经 workspace 域名 |

`mcp_pipeline_runs` 三行全 ok（run 23549–23551）。

**未动 / 待办**：

- dev 服务**已重启生效**（2026-09-18 22:38，用户指令；旧 PID 84360 优雅退出 → 新
  PID 86706，同参同日志 `/tmp/lvdata_mcp.log`）：启动日志四 provider 均为 workspace
  域名 + workspace_id，首轮 round 全 ok（files=0，零 force 开销，符合预期）；
- staging / production 无 lvdata_mcp.yaml（同 audio 轮裁决，dev only）；
- 存量数据不重跑（§1 Q3）。
