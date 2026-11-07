# Ali Bailian（DashScope）Provider 配置外置：DASHSCOPE_API_KEY / DASHSCOPE_API_HOST / DASHSCOPE_WORKSPACE_ID

> 状态：**已实施并验证（2026-09-30）**，见 §8。
> 范围：`configs/dev/server.yaml` + `configs/dev/lvdata_mcp.yaml`（§7-D2）；k8s configmap 不在本次范围（§3.5）。

## 1. 目标

1. 引入三个环境变量：`DASHSCOPE_API_KEY`（已有）、`DASHSCOPE_API_HOST`、`DASHSCOPE_WORKSPACE_ID`。
2. server 配置文件中 alibaba（百炼）相关段一律经 `${...}` 占位符读取，不再 hardcode 域名与 workspace id。
3. 不改 provider / factory 代码：取值路径不变，只是值来源从字面量变为 env。

**非目标**：`model` / `voice` / `params` / `?model=` query 保持逐段字面量（各段取值不同，本次只要求三值外置）。

## 2. 现状（代码事实）

### 2.1 硬编码分布（当前 worktree 行号，含未提交的两处 provider 条目删除）

| 文件 | 位置 | 内容 |
|---|---|---|
| `configs/dev/server.yaml:62,65` | asr.providers.alibaba_bailian | 专属域名 + workspace_id |
| `configs/dev/server.yaml:90,94` | llm.providers.alibaba_bailian | 专属域名（compatible-mode/v1）+ workspace_id |
| `configs/dev/server.yaml:140,144` | tts.providers.alibaba_bailian | 专属域名 + workspace_id |
| `configs/dev/server.yaml:166,170` | omni.providers.alibaba_omni | 专属域名（realtime?model=…）+ workspace_id |
| `configs/dev/server.yaml:189,193` | translate（顶层段） | 专属域名（realtime?model=…）+ workspace_id |
| `configs/dev/lvdata_mcp.yaml:50,52 / 59,61 / 65,67 / 73,75` | providers.{audio,vision,summary,embedding} | 专属域名（compatible-mode/v1）+ workspace_id |
| `infra/kubernetes/configmap.yaml:49,62,102,106` | 内嵌 server.yaml：asr/llm 用公网域名；tts 用专属域名+workspace_id | 独立部署快照 |
| `services/scripts/` 探针 5 个带真实字面量：`test_{llm,omni,bailian_asr,bailian_translate,bailian_tts}_real.py` | 默认 base_url / workspace_id 字面量 | 均带 `--base-url/--workspace-id` 覆盖 |
| `services/scripts/{measure_asr_final_latency,probe_omni_image_text_turn}.py` | docstring 示例 URL 用 `<WorkspaceId>` 占位（无真实值） | 无需处理 |
| `services/tests/unit/{llm/test_factory_failover, asr/providers/test_bailian, tts/providers/test_bailian}.py` | 夹具字面量 | 不读配置 |

### 2.2 loader 占位符机制（决定设计形态）

- `services/shared/config/loader.py:14-30`：`_ENV_VAR_RE = \$\{([^}]+)\}`，对字符串**任意位置**做子串替换。
  实测（2026-09-30 本机探针）：
  - `wss://${DS_HOST}/api-ws/v1/inference` → 正确拼接；带 `?model=…` 的 query 同样正常；
  - 变量存在但为空串 → 替换为空串（合法）；
  - 变量不存在 → `ConfigError`（启动 fail-fast），判定为 `os.environ.get(name) is None`。
- 占位符解析发生在 `LIVACHAT__` override 之前（loader.py:137-138），且对**全部字符串无条件解析**（含 disabled provider）。
- lvdata-mcp 侧同构：`tools/lvdata-mcp/src/lvdata_mcp/config.py:195-213`（`_resolve_env_vars` / `_resolve_placeholders`；缺失 → ValueError 启动 fail-fast）；`validate_required`（config.py:99-104）另要求 audio/vision/summary 的 `api_key` 非空。

### 2.3 官方依据（2026-09-30 联网核对）

- 业务空间专属域名 = 百炼控制台「业务空间管理」页 **API Host 列**的值，形如
  `{WorkspaceId}.cn-beijing.maas.aliyuncs.com`（新加坡为 `{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`）；
  官方迁移指引为"**替换请求地址中的域名部分**即可，无需修改业务逻辑代码"。
- 端点路径（官方）：OpenAI 兼容 `https://{host}/compatible-mode/v1`；实时族
  `wss://{host}/api-ws/v1/realtime?model=<model>`；`wss://{host}/api-ws/v1/inference`。
- `X-DashScope-WorkSpace` 官方请求头表标注为**可选**——与仓库既有多轮实测一致
  （ASR/LLM realtime 缺头均可连，见 `docs/asr_bailian_improvements.md:398-400`）。
- `DASHSCOPE_API_KEY` 是 DashScope SDK 官方环境变量名；API Key 按地域独立、不跨地域混用
  → host 天然携带地域信息。
- 官方链接：
  - 地域及接入域名：https://help.aliyun.com/zh/model-studio/beijing-access-information
  - Base URL overview：https://help.aliyun.com/zh/model-studio/base-url
  - WebSocket API（Qwen-ASR 实时）：https://help.aliyun.com/zh/model-studio/qwen-asr-realtime-interaction-process
  - DashScope SDK（env 约定）：https://pypi.org/project/dashscope/

## 3. 设计

### 3.1 三个 env 的语义

| env | 值形态 | 示例 | 备注 |
|---|---|---|---|
| `DASHSCOPE_API_KEY` | API Key | `sk-…` | 已有，不变 |
| `DASHSCOPE_API_HOST` | **纯域名**：不含 scheme、不含路径、不含 query | `llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com` | = 控制台 API Host 列原值；命名对齐官方术语。走公网路径时填 `dashscope.aliyuncs.com` |
| `DASHSCOPE_WORKSPACE_ID` | 业务空间 ID | `llm-1tmsqifo51cwfadr` | 空串 → 不发 `X-DashScope-WorkSpace` 头（provider 侧 `if workspace_id:` 语义：asr:190-194 / tts:403-406 / llm providers/alibaba_bailian.py:20 / omni:31-38 / livetranslate:31-38） |

**D1（已定）**：host 只含域名，scheme 与路径留在 yaml。
理由：同一 host 在不同段用不同 scheme（`https` 兼容模式 / `wss` 实时族）与不同路径；只外置"域名部分"
正是官方迁移口径。若 env 含 scheme，则要么拆两个变量（https/wss），要么在 yaml 里写死 scheme，均更差。

### 3.2 yaml 改写（configs/dev/server.yaml 5 段）

| 段 | before | after |
|---|---|---|
| asr.alibaba_bailian | `base_url: wss://llm-…maas.aliyuncs.com/api-ws/v1/inference` | `base_url: "wss://${DASHSCOPE_API_HOST}/api-ws/v1/inference"` |
| llm.alibaba_bailian | `base_url: https://llm-…maas.aliyuncs.com/compatible-mode/v1` | `base_url: "https://${DASHSCOPE_API_HOST}/compatible-mode/v1"` |
| tts.alibaba_bailian | 同 asr | 同 asr |
| omni.alibaba_omni | `wss://llm-…maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-omni-flash-realtime` | `"wss://${DASHSCOPE_API_HOST}/api-ws/v1/realtime?model=…"`（model 保持字面量） |
| translate | `wss://llm-…maas.aliyuncs.com/api-ws/v1/realtime?model=qwen3.5-livetranslate-flash-realtime` | 同上型 |
| 各段 workspace_id | `workspace_id: llm-1tmsqifo51cwfadr` | `workspace_id: "${DASHSCOPE_WORKSPACE_ID}"` |

写法建议：统一带双引号（与既有 `api_key: "${DASHSCOPE_API_KEY}"` 一致）。

### 3.3 取值语义矩阵

| DASHSCOPE_API_HOST | DASHSCOPE_WORKSPACE_ID | 结果 |
|---|---|---|
| 专属域名 | `llm-xxx` | 现行生产路径（推荐） |
| 专属域名 | 空串 | 域名可达但缺头；官方标注可选，ASR/LLM 实测可连；TTS 有"缺头 401/411"未复证实测的既有经验，不推荐 |
| `dashscope.aliyuncs.com` | 空串 | 公网路径（无业务空间），LLM 兼容模式官方支持 |
| 未设置 | —— | 启动 fail-fast（ConfigError） |
| 空串 | —— | base_url 形如 `wss:///api-ws/v1/…`，**启动期不报错**，首次连接才炸（ASR/TTS/omni 懒连接） |
| 误带 scheme（`wss://host`） | —— | base_url 变成 `wss://wss://host/…`，同样只在连接期暴露 → `D1` 明确"纯域名"并写入 .env.example 注释 |

### 3.4 保留的设计不变式

- **两值互不派生**：workspace_id 与 host 左标签在官方语义上同源，但仓库既有约定（多处 yaml 注释与
  provider 注释）为"两值配套、互不派生"。本次继续用两个独立 env 保持该语义：公网路径可只留 host；
  且避免"改 host 忘改 ws"的隐式耦合。
- **不改 provider 代码**：值源变化对 `cfg.get("workspace_id")`、`if self._workspace_id:` 路径完全透明。

### 3.5 设计约束（重要）

一个部署内 `DASHSCOPE_API_HOST` 只能取一个值 → **同一部署不能混用"公网 dashscope 域名"与
"业务空间专属域名"的 alibaba provider**。

- dev 现状：5 段全部走专属域名 → 无影响。
- `infra/kubernetes/configmap.yaml` 现状：asr/llm 走公网域名、tts 走专属域名 → 该快照无法用单变量表达
  （见 §7-D2：本次不动 k8s，列为后续独立项）。

## 4. 改动清单（定稿；实施待执行）

| # | 文件 | 动作 |
|---|---|---|
| 1 | `configs/dev/server.yaml` | 5 段 base_url / workspace_id 换占位符（10 处） |
| 2 | `services/.env.example` | 裸名区新增 `DASHSCOPE_API_HOST=`、`DASHSCOPE_WORKSPACE_ID=` + 注释（值形态说明） |
| 3 | `services/.env` | 同两行，填当前 dev 值（本地文件，gitignored） |
| 4 | `services/tests/unit/test_config_loader.py` | 两处 env 注入补 2 个变量（:90、:113） |
| 5 | `services/tests/e2e_backend/conftest.py` | `_server_env()` 补 2 个变量（:35 区块） |
| 6 | `services/tests/integration/gateway/conftest.py` | `_env_for_server()` 补 2 个变量（:22 区块） |
| 7 | `docs/install_design.md` | §1.4 两张表（server 8 → 10 变量；lvdata-mcp 表补 2 变量）、§3.5 说明同步 |
| 8 | `REPO.md:57` | 环境变量说明同步 |
| 9 | `configs/dev/lvdata_mcp.yaml` | 4 段 8 处换占位符（D2 定稿纳入） |
| 10 | `tools/lvdata-mcp/README.md` | 环境变量说明同步（:21、:35、:135） |

已核对**无需改动**（避免评审时重复排查）：

- `services/tests/integration/bff/test_bff_smoke.py:50`（第 4 个子进程入口）：env 走 `os.environ` + `services/.env` 继承，无字面量注入清单 → 只要 .env 含新变量即可，代码不用改。
- `services/tests/unit/{test_sanity.py, bff/test_gzip.py}`：`create_app(config=AppConfig())` 显式传配置，不经 yaml / 占位符解析。
- 历史设计文档中的示例 yaml（如 `docs/server_design.md:605-664`）保持历史记录，不逐一同步。
- `.github/workflows/*.yml` 均为 0 字节占位文件，无 CI env 注入需要同步。

## 5. 测试计划

1. 单元：`.venv/bin/pytest -c services/pyproject.toml services/tests/unit/test_config_loader.py --junitxml=/tmp/junit.xml`（解析 XML 计数，不用摘要行）。
2. 值断言（一次性脚本）：注入 env 后 `load_config("dev")`：
   - `cfg.asr.providers["alibaba_bailian"].base_url == f"wss://{host}/api-ws/v1/inference"`
   - `cfg.translate.base_url == f"wss://{host}/api-ws/v1/realtime?model=qwen3.8-livetranslate-flash-realtime"`（3.8-only 迁移后）
   - `cfg.llm / cfg.tts / cfg.omni` 同型断言。
3. 缺失 fail-fast：不带 `DASHSCOPE_API_HOST` 加载 → 期望 ConfigError（确认新变量进入必填集）。
4. 集成 smoke：`services/tests/integration/gateway`（`-m integration`；子进程 uvicorn 加载真实配置，无需 PG/Redis）随 conftest 更新后跑通。
5. lvdata-mcp 侧（D2 定稿纳入）：`.venv/bin/lvdata-mcp stage inventory`（零 LLM 用量的 CLI 入口，会走占位符解析）或 `pytest tools/lvdata-mcp/tests`。

## 6. 风险与回滚

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 外置后"配错值"比"改 yaml"更隐蔽：host 空串/误带 scheme 均**不在启动期暴露**（懒连接），首次会话才炸 | .env.example 注释写明值形态；本文 §3.3 矩阵（D3 已定不加启动期校验，误配靠注释与文档拦截） |
| R2 | 同部署公网/专属域名混用不可表达（§3.5） | dev 无此问题；k8s 本次不动，单列后续项 |
| R3 | 其他开发者 pull 后未补 .env 两行 → 启动 fail-fast（设计意图，非缺陷） | README / install_design 同步说明 |
| R4 | 值泄漏面：host / workspace_id 由 env 提供，是否会新增日志暴露？—— 无新增：provider 只打 model；lvdata 侧明确"base_url/api_key/workspace_id 一律不打"（config.py:300 注释） | — |
| 回滚 | `git revert`（yaml + 测试 + 文档）；.env 多出的两行留用无害 | — |

## 7. 决策（已定，2026-09-30，按推荐）

- **D1 host 形态 = 纯域名**（不含 scheme/路径）。理由：同 host 在不同段混用 scheme 与路径，
  只外置域名正是官方迁移口径；含 scheme 则需拆变量或在 yaml 写死 scheme。
- **D2 范围 = server + lvdata-mcp 同步**（`lvdata_mcp.yaml` 4 段 8 处 + 其 README）；
  **k8s configmap 本次不动**，列为后续独立项。理由：同账号同 host、同一份 .env 注入，
  留一半等于没消除重复；k8s 快照公网/专属域名混用，单变量表达不了（§3.5）。
- **D3 不加启动期校验**。理由：保持 loader 通用；host 空串/误带 scheme 的行为差异写入
  §3.3 矩阵与 §6-R1，靠 .env.example 注释防误配。
- **D4 探针脚本与测试夹具的字面量 host 不改**。理由：探针自带 `--base-url/--workspace-id`
  覆盖参数，改为 env 优先无收益。

## 8. 实施与验证记录（2026-09-30）

**改动**（§4 清单 10 项全部落地；含下方 QA 追加修正，最终 `git diff --stat`：11 文件，+64/−42）：

| 文件 | 内容 |
|---|---|
| `configs/dev/server.yaml` | 5 段 base_url/workspace_id → 占位符（asr/llm/tts/omni/translate） |
| `configs/dev/lvdata_mcp.yaml` | 4 段（audio/vision/summary/embedding）→ 占位符 |
| `services/.env.example` | 裸名区新增 `DASHSCOPE_API_HOST` / `DASHSCOPE_WORKSPACE_ID` + 值形态注释；头注释补 lvdata_mcp.yaml |
| `services/.env`（本地，gitignored） | 追加同两行并填当前 dev 值 |
| `services/tests/unit/test_config_loader.py` | 2 处注入点补 2 变量 |
| `services/tests/{e2e_backend,integration/gateway}/conftest.py` | 各补 2 变量（值 "x"） |
| `docs/install_design.md` | §1.4 两表（8→10 / lvdata-mcp +2）、§2 流程 export、§3.4、§3.5 计数注记 |
| `REPO.md` / `tools/lvdata-mcp/README.md` | 环境变量说明同步（含 Docker `-e` 示例） |

**验证证据**（junit XML 计数，非摘要行）：

| # | 项 | 结果 |
|---|---|---|
| 1 | `pytest services/tests/unit/test_config_loader.py` | 23 / 0 fail / 0 err / 0 skip |
| 2 | 值断言（source services/.env；5 段 base_url + 5 处 workspace_id == env 值） | 通过 |
| 3 | 缺失 `DASHSCOPE_API_HOST` → ConfigError（含变量名） | 通过 |
| 4 | `pytest services/tests/integration/gateway -m integration`（子进程加载真实配置） | 9 / 0 / 0 / 0 |
| 5 | lvdata-mcp：配置解析断言（4 段 base_url/workspace_id == env）+ `pytest tools/lvdata-mcp/tests` | 通过；286 / 0 / 0 / 0 |

**残留硬编码核对**：`llm-1tmsqifo51cwfadr` 剩余出现位置全部为文档（历史记录）、`infra/kubernetes/configmap.yaml`（本次范围外，§3.5）、5 个探针脚本与 3 个测试夹具（D4 不改）。

**QA 复核追加修正（同日）**

| 发现 | 处置 |
|---|---|
| `tools/lvdata-mcp/deploy/lvdata-mcp.service` 仅注入 `DASHSCOPE_API_KEY` → 按本方案部署会启动 fail-fast（缺 host/workspace） | 补 2 行 `Environment=`（host 用 `llm-xxxx` 形态值） |
| `tools/lvdata-mcp/deploy/Dockerfile` 用法示例与注释未列新变量 | 补 2 个 `-e` 示例 + 注释 |
| `services/.env.example` 新变量误填真实内部端点/workspace 值（与模板"填真实值"约定、README `llm-xxxx` 示例不一致） | 改为形态占位 `llm-xxxx.cn-beijing.maas.aliyuncs.com` / `llm-xxxx` |

**复核发现（存量、非本次引入，未改）**：`infra/docker/docker-compose.dev.yml` 的 `server` 服务无 `env_file`、仅列 3 个环境变量——缺 `DASHSCOPE_API_KEY` 等全部裸名占位符，**本次改动前即会启动 fail-fast**；并仍引用已废弃的 `LIVACHAT__REDIS__URL`。如要启用该 compose 需整体更新（另案处理）。

**QA 后复跑**（junit XML 计数）：test_config_loader 23/0/0/0；lvdata-mcp 286/0/0/0；gateway 集成（`-m integration`）9/0/0/0。

---

## 9. .env 自动加载（`project_root/.env`，两服务共用）——已实施并验证（2026-09-30），见 §9.8

> 本节为独立变更（不依赖 §1–§8 的取值外置，但复用同一份 env）。§4 第 2/3 项中的
> `services/.env(.example)` 位置由本节取代。

### 9.1 现状（代码事实）

- 两个 loader 均为**占位符 fail-fast 解析**（`services/shared/config/loader.py:17-30`；
  `tools/lvdata-mcp/src/lvdata_mcp/config.py:195-204`），但**都不加载 `.env`**——
  `docs/install_design.md` §2 明确"服务不加载 dotenv"，靠用户
  `set -a && source services/.env && set +a`。
- 现有 .env 位置：`services/.env`（`.gitignore:3` 忽略，无前导斜杠）；模板 `services/.env.example`。
  **（2026-09-30 用户已手工把本机 `services/.env` 移至根 `.env`，见 §9.4-12，现 `services/.env` 不存在。）**
- 仓库内已有两份**手写** .env 解析：`services/tests/e2e_backend/conftest.py:25-31`、
  `services/tests/integration/bff/test_bff_smoke.py:34-40`（读 `services/.env`，为子进程补 env）。
- 依赖面：两个 distribution 均**未声明** `python-dotenv`（lvdata-mcp 的 uv.lock 中
  `python-dotenv` 仅作为 `pydantic-settings` 的传递依赖出现）
  → 不新增依赖，各内置 ~15 行解析器（与 conftest 现有解析同风格）。
- 运行入口：livachat-server 全部经 `load_config()`（`services/app/main.py` 模块级
  `create_app` → `load_config`；`services/app/application.py:323-325`）；
  lvdata-mcp CLI（serve/reindex/eval/stage）全部经 `load_config()`（`main.py:269`）
  → **两处单点注入即可覆盖两个服务进程的全部配置读取路径（含下列旁路点）**。
- 旁路点（不经占位符、直接读 env，均在 `load_config()` 之后调用，故自动被 .env 覆盖）：
  `llm/prompt.py:15`（`LIVACHAT__LLM__SYSTEM_PROMPT_PATH`）、`gateway/endpoint.py:119`
  （`LIVACHAT_PIPELINE_MODE`）、`app/main.py:16`（`LIVACHAT_SERVER_RELOAD`）、
  `session/agent_job_ingest.py:26-30`（`_env_int` 系列）、lvdata-mcp `vec.py:51`
  （embedding key 的 env 兜底）。
  **边界**：独立进程/脚本（`services/scripts/*` 探针、`tools/data-analysis/*` 等）不经
  `load_config()` → 不受 .env 自动加载影响，仍需 shell 注入（D5 保留脚本 docstring 的原因之一）。

### 9.2 设计

1. **位置** = `_project_root() / ".env"`（仓库根，与 `configs/` 同级）。
   根定位函数两边都已存在（`loader.py:44-55`；`config.py:245-256`，均从 `__file__` 上溯，
   回退 CWD）→ 不新增定位逻辑。
2. **加载语义** = `os.environ.setdefault`：**shell / CI / 部署注入优先，.env 只补缺**
   （与仓库既有约定一致：`docs/hermes_websocket_api.md:123` "shell 环境变量优先于 .env"）。
   文件不存在 → 静默跳过；每次 `load_config()` 调用时读取（不缓存，避免测试间串味）。
3. **解析规则**（内置，两处各一份）：跳过空行与 `#` 注释；容忍 `export `（含 Tab）前缀；
   按**第一个** `=` 拆分；去首尾空白；成对引号（`'…'`/`"…"`）剥离；
   **同键后行覆盖前行**（对齐 `set -a && source .env` 语义，见 §9.8 QA 修正）。
   **不支持**行内注释、变量展开、多行值（写入文档）。
4. **注入点**：`services` → `load_config()` 首行（`loader.py:124-133`）；
   `lvdata-mcp` → `load_config()` 首行（`config.py:279-281`）。
   先于 `_resolve_placeholders` 与 `_apply_env_overrides` → `.env` 也可提供
   `LIVACHAT__*` / `LVDATA_MCP__*` / `LIVA_CHAT_CONFIG` / `LVDATA_MCP_CONFIG`。
5. **测试隔离**：实现 `_dotenv_path()`（返回根/.env，独立函数便于 monkeypatch）；
   两个测试文件加 autouse fixture 把该路径指到 tmp 下不存在文件 → 单测**不读开发者本机 .env**
   （防"本地有 .env 掩盖 CI 缺变量"）。
6. **迁移**：`mv services/.env .env`（本地真实值文件，gitignored，内容不动、不打印）；
   `git mv services/.env.example .env.example`，模板头注释改为
   "cp .env.example .env（同目录）；两服务启动自动加载；shell 变量优先"；
   `.gitignore` 增 `/.env`（**保留** `services/.env` 行防遗留误提交）。
7. **conftest 简化**（D4 定稿：删除）：两处手写解析删除——子进程（uvicorn）自加载根 .env；
   测试专用覆盖仍走 `env.update(...)`（真实 env > .env）。

### 9.3 边界矩阵

| 场景 | 行为 |
|---|---|
| shell 已设该变量 | `.env` 不覆盖（setdefault） |
| `.env` 不存在 | 静默跳过；占位符缺失仍 fail-fast（CI/生产路径不变） |
| `.env` 提供 `LIVACHAT__*` / `LVDATA_MCP__*` | 生效（override 阶段读的是 `os.environ`） |
| 值含引号 / 前后空白 | 去成对引号 / 去首尾空白；值内空白保留 |
| `.env` 中 `KEY=`（空值） | 视为已设置（空串）——server 允许空串、lvdata-mcp 的 `api_key` 校验会 fail-fast（与现状同语义） |
| 独立脚本/探针进程 | 不经 `load_config()` → 不读 .env，仍需 shell 注入（§9.1 边界） |
| 行内注释（`KEY=v # c`） | **不支持**：`# c` 会成为值的一部分（文档写明） |
| 非 editable 安装 | `_project_root()` 回退 CWD 链 → 需仓库内 CWD 才能找到 .env（与 configs 发现同一约束） |
| 单测进程 | autouse fixture 隔离 → 行为与"无 .env"时一致 |

### 9.4 改动清单（文件级）

| # | 文件 | 动作 |
|---|---|---|
| 1 | `services/shared/config/loader.py` | `_dotenv_path()` + `_load_dotenv()`；`load_config()` 首行调用 |
| 2 | `tools/lvdata-mcp/src/lvdata_mcp/config.py` | 同构实现（两 distribution 无相互依赖，刻意为两份） |
| 3 | `.env.example`（`git mv` 自 `services/`） | 头注释更新（同目录 cp、自动加载、shell 优先） |
| 4 | `.gitignore` | 增 `/.env`（保留 `services/.env`） |
| 5 | `services/tests/unit/test_config_loader.py` | autouse 隔离 fixture + 新用例（.env 注入 / shell 优先 / 缺失不报错 / 引号与 export 形态） |
| 6 | `tools/lvdata-mcp/tests/test_config.py` | 同构 fixture + 用例 |
| 7 | `services/tests/e2e_backend/conftest.py`、`services/tests/integration/bff/test_bff_smoke.py` | 删手写 .env 解析（D4） |
| 8 | `REPO.md`（:56-62、:91） | cp 路径与示例改根 `.env`；写明自动加载 + shell 优先 |
| 9 | `docs/install_design.md`（§1.4 前言、§2 流程、§3.5） | 同步；历史流程文字加"2026-09-30 起服务自动加载根 .env"注记 |
| 10 | `tools/lvdata-mcp/README.md`（快速开始 #2） | 改为"根 .env 自动加载；export 仍可用（shell 优先）" |
| 11 | `tools/lvdata-mcp/scripts/probe_video_{extract,filter}.py`、`tools/data-analysis/compare_omni_audio.py` | docstring 三条可运行命令改根 `.env` |
| 12 | 本地（不在 git） | `mv services/.env .env` —— **已由用户手工完成（2026-09-30）**，实施时跳过 |

历史文档（`docs/mylife/`、`docs/qwen_*.md`、`docs/day_summary.md`、`docs/android_*.md`、
`docs/bff_replay_removal.md`、`docs/server_dev.md`、`docs/server_ssl_support.md`）
中的 `services/.env` 命令为历史记录 → 不逐一改（D5）。

### 9.5 测试计划

1. 新单测（两处）：tmp 项目根（configs/ + .env）→ ① 占位符由 .env 解析；② 已设 shell 变量优先；
   ③ .env 缺失不报错；④ `export KEY=` 与引号形态。
2. 回归：test_config_loader（23）、lvdata-mcp 全量（286）、gateway 集成（9）——junitxml 计数。
3. 真机（两服务各一次）：`env -i PATH=$PATH .venv/bin/python -c "…load_config(…)"` 干净环境仅靠
   根 .env 成功解析；临时移走 .env → 恢复 fail-fast（回归确认）。
4. （可选）起服务 smoke：干净 env + 根 .env → `livachat-server` → `curl -k /healthz`。

### 9.6 决策（已定，2026-09-30，按推荐）

- **D1 加载语义 = `os.environ.setdefault`**（shell/CI/部署注入优先）。理由：与 hermes 侧
  python-dotenv `override=False` 既有约定一致；也保证测试与生产注入路径行为不变。
- **D2 模板位置 = `git mv` 到仓库根**（`.env.example`）。理由：与新位置同目录，
  `cp .env.example .env` 一步到位；双份模板必然漂移。
- **D3 自定义 env 路径 = 不做**。理由：保持单一"根/.env"定位规则；确实需要时用
  shell 注入（原有路径）即可，不引入新配置面。
- **D4 conftest 手写 .env 解析 = 删除**。理由：服务端已自加载，解析逻辑去重；
  测试专用覆盖仍经 `env.update(...)`（真实 env > .env），语义不变。
- **D5 文档范围 = 核心三份（README / install_design / lvdata-mcp README）+ 3 个可运行
  脚本 docstring**；历史文档不动。理由：可运行指令必须能照抄执行；历史记录保留原貌。

### 9.7 风险与回滚

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 真实密钥文件从 `services/` 迁到根：旧习惯命令（`source services/.env`）与历史文档指令失效 | README 更新；历史文档标注不改；迁移为 `mv` 不复制（避免双源） |
| R2 | 新增隐式来源：本机 .env 可能掩盖 CI 缺变量 | 单测 autouse 隔离（§9.2-5）；README 写明 shell 优先与 CI 注入路径 |
| R3 | 根 .env 误入库 | `.gitignore` 增 `/.env`；保留 `services/.env` 行 |
| 回滚 | revert 代码 + `mv .env services/.env` | — |

### 9.8 实施与验证记录（2026-09-30）

**落地**（§9.4 清单 1–11 项；第 12 项为用户手工完成）：

| # | 文件 | 内容 |
|---|---|---|
| 1 | `services/shared/config/loader.py` | `_dotenv_path()` + `_load_dotenv()`；`load_config()` 首行调用（docstring 编号 0） |
| 2 | `tools/lvdata-mcp/src/lvdata_mcp/config.py` | 同构（放在 `_project_root()` 之后） |
| 3 | `.env.example`（`git mv` 自 services/） | 头注释改写：同目录 cp、两服务自动加载、shell 优先 |
| 4 | `.gitignore` | 增 `/.env`（保留 `services/.env`） |
| 5/6 | 两测试文件 | autouse `_isolate_dotenv` fixture + 新用例（.env 供给 / shell 优先 / 缺失仍 fail-fast；含 `export ` 与引号形态） |
| 7 | e2e_backend/conftest.py、bff_smoke | 手写 .env 解析删除（D4） |
| 8-10 | README / install_design（§1.4、§2、§3.5）/ lvdata-mcp README | 同步根 .env 用法与自动加载说明 |
| 11 | probe_video_{extract,filter}.py、compare_omni_audio.py | docstring 命令改根 `.env`（并注明独立脚本需 shell 注入） |

**实施中发现的实现注意点**：`_load_dotenv` 直接写 `os.environ`（monkeypatch 不追踪）
→ 单测需逐例 `delenv` 清理，否则 .env 注入的值跨用例泄漏（已在新用例中处理，
首个失败用例即由此暴露）。

**验证证据**（junit XML 计数 / 干净环境真机断言）：

| 项 | 结果 |
|---|---|
| `pytest services/tests/unit/test_config_loader.py` | 26 / 0 / 0 / 0（23 存量 + 3 新） |
| `pytest tools/lvdata-mcp/tests` | 288 / 0 / 0 / 0（286 存量 + 2 新） |
| `pytest services/tests/integration/gateway -m integration` | 9 / 0 / 0 / 0 |
| `env -i PATH HOME`（干净环境，仅根 .env）+ livachat `load_config("dev")` | 通过（host/workspace 断言） |
| 同上 + lvdata-mcp `load_config()` | 通过（summary/embedding 断言） |
| 缺失 .env 场景（单测隔离 fixture 路径） | 仍 ConfigError / ValueError（fail-fast 未弱化） |
| `ruff check`（本次改动涉及的 services 4 文件） | clean；lvdata-mcp 4 条为**存量**（HEAD 同数，未引入） |

**QA 复核追加修正（同日，深审自身实现）**

| 发现 | 处置 |
|---|---|
| .env 内**重复键**原为"首行生效"（逐行 setdefault），与既有 `set -a && source .env`（后行覆盖）不一致；用户若按"末尾追加"习惯补 key，会静默取到模板里的空值 | 改为先收集后统一 setdefault → **同键后行覆盖前行**；两处各补 1 用例（dup key + `export\t`） |
| `export` + **Tab** 前缀不被识别 → 生成 `export\tKEY` 垃圾环境变量、真键未设置（静默） | 前缀判定改 `line[6:7] in (" ", "\t")` |
| `_load_dotenv(path=None)` 参数无调用方（测试走 monkeypatch `_dotenv_path`） | 删除参数（死代码） |

**QA 后复跑**：test_config_loader 27/0/0/0；lvdata-mcp 289/0/0/0；干净环境（`env -i`）真机断言
两服务仍通过；全服务端 smoke（独立端口 18899 + 临时 chat_server.db，clean env 仅靠根 .env）
`/healthz` 200；ruff services 侧 clean，lvdata-mcp 4 条与 HEAD 同数（存量）。

**已知遗留（D5 范围外，历史文档中的可运行命令仍指 `services/.env`）**：
`docs/server_dev.md:1149`（docker `--env-file`）、`docs/day_summary.md:206`、
`docs/mylife/mylife-design.md:653`、
`docs/qwen_audio_analysis.md:343`、
`docs/bff_replay_removal.md`（多处引用）。均为历史记录；如需清理可另起一次小改。
（`docs/qwen_livetranslate_realtime.md` 相应命令已于 2026-10-10 随 3.8 迁移改为根 `.env`。）
