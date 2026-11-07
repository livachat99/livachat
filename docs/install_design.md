# 统一安装与启动设计：livachat-server + lvdata-mcp（终稿）

> 本文为由 `install_design-A.md` / `install_design-B.md` 评审合并而成的终稿：
> 主体采用 B 稿（实证充分、行号精确、覆盖 `.env.example` 修正），
> `reload_dirs` 实现采用 A 稿方案（复用现有公开 API `resolve_path`，不改动 loader.py）。
> 所有关键结论均经本机实证（§4）或以代码事实为准（§1 行号引用，2026-09-02 复核一致）。

> 目标体验（仓库根目录）：
>
> 1. `python3.12 -m venv .venv` + 一条 pip 命令装好 `livachat-server` 与 `lvdata-mcp`（全部 editable）
> 2. 必需环境变量用户手工 export（或 `set -a && source services/.env`）
> 3. `.venv/bin/livachat-server` 与 `.venv/bin/lvdata-mcp serve` 直接启动，任意 CWD 可用

---

## 1. 现状（代码事实）

### 1.1 安装现状

| 项 | 现状 | 位置 |
|----|------|------|
| livachat-server | `pip install -e packages/sdk-python` + `pip install -e "services[test,dev]"`（两条命令） | `Makefile:35-37` |
| lvdata-mcp | 独立 venv：`tools/lvdata-mcp/.venv` + `pip install -e .` | `tools/lvdata-mcp/README.md:23-26` |
| livachat-protocol | services 的依赖 `livachat-protocol`（裸名，无版本约束），由 sdk-python 提供 | `services/pyproject.toml:32`、`packages/sdk-python/pyproject.toml:6` |

三个 distribution 均为 setuptools editable 安装，无包名冲突：

- `livachat-protocol`：src layout，仅导出 `livachat_protocol`。
- `livachat-server`：flat layout（`[tool.setuptools.packages.find] where=["."]`，
  `services/pyproject.toml:53-54`），导出 `app / asr / bff / gateway / llm / media /
  migrations / omni / recording / session / shared / tts / agent_relay` 等顶层包
  （`memory/ user-profile/ scripts/ tests/` 无 `__init__.py`，不参与安装——前两者仅含
  `.gitkeep`）。
- `lvdata-mcp`：src layout（`where=["src"]`），仅导出 `lvdata_mcp`。

### 1.2 启动现状

- livachat-server：`cd services && python app/main.py`（`Makefile:39-40`）。
  `services/app/main.py` 模块级执行 `app = create_app()`，`__main__` 下
  `uvicorn.run("app.main:app", reload=True, **ssl_kwargs)`。
  **无 console script**（`services/pyproject.toml` 无 `[project.scripts]`）。
- lvdata-mcp：已有 console script `lvdata-mcp = "lvdata_mcp.main:main"`
  （`tools/lvdata-mcp/pyproject.toml:26-27`），`serve` 为默认子命令
  （`tools/lvdata-mcp/src/lvdata_mcp/main.py:52-53`）。**零改动**。

### 1.3 配置加载与路径解析（决定"任意 CWD 可启动"的关键）

两个服务的配置发现机制同构：

| 机制 | livachat-server | lvdata-mcp |
|------|-----------------|------------|
| 项目根定位 | `_project_root()` 从 `__file__` 向上找同时含 `configs/`+`services/` 的祖先；失败后 fallback 到 CWD 向上找 `configs/`（`services/shared/config/loader.py:44-55`） | 同构（`tools/lvdata-mcp/src/lvdata_mcp/config.py:226-236`） |
| 配置文件 | `LIVA_CHAT_CONFIG` env → `configs/<env>/server.yaml`（默认 dev） | `--config` → `LVDATA_MCP_CONFIG` env → `configs/<env>/lvdata_mcp.yaml` → CWD |
| `${ENV_VAR}` 占位符 | **无条件全量递归解析**（含 disabled provider），未设置即 fail-fast（`loader.py:17-30`；判定为 `os.environ.get(name) is None`，`:25-27`） | 同（`config.py:176-195`） |
| env 覆盖 | `LIVACHAT__` 前缀双层下划线（`loader.py:77-113`） | `LVDATA_MCP__` 前缀（`config.py:215-223`） |
| 相对路径资源 | SSL 证书、prompt 文件经 `_project_root()` 绝对化（`ssl.py:16-21`、`llm/prompt.py:19`）；web_dist 的 `parents[2]` 推导已随 2026-10-13 /mylife 托管摘除删除 | 数据路径锚定 `data.root_dir`（绝对路径 `/opt/lvdata`） |

**结论：只要以 editable 方式安装（`__file__` 留在 repo 源码树内），两个服务从任意
CWD 启动都能自动找到 `configs/dev/*.yaml`。非 editable 安装会破坏该约定——
`_project_root()` 仅在 venv 位于仓库内时碰巧命中。本设计强制 `-e`。**
（原文此处另举 `application.py` `parents[2]` 推导在非 editable 下错位 →
`/mylife` SPA 静默不挂载为印证——该代码已随 2026-10-13 /mylife 托管摘除删除。）

### 1.4 环境变量真实需求（以 yaml 占位符为准，非 `.env.example`）

占位符解析判定为 `os.environ.get(name) is None → ConfigError`
（`loader.py:25-27`）：**变量必须存在于环境，允许为空字符串**
（2026-09-30 起：变量来源新增"仓库根 `.env` 自动加载"——两个服务启动时经
`_load_dotenv()` 以 setdefault 注入，shell/CI 注入优先；见
`docs/ali_bailian_provider.md` §9）。

`configs/dev/server.yaml` 实际引用的占位符（共 4 个）：

| 变量 | 要求 | 用途 |
|------|------|------|
| `DASHSCOPE_API_KEY` | **真实值** | ASR/LLM/TTS active provider + translate + omni 全部用它 |
| `DASHSCOPE_API_HOST` | **真实值**（纯域名 = 控制台「API Host」列原值，不含 scheme/路径；公网路径填 `dashscope.aliyuncs.com`） | 各段 `base_url` 的域名部分（2026-09-30 外置，见 `docs/ali_bailian_provider.md`） |
| `DASHSCOPE_WORKSPACE_ID` | 真实值（业务空间 ID；空串亦可 = 不发 `X-DashScope-WorkSpace` 头） | 各段 `workspace_id` |
| `APP_AUTH_TOKEN`（2026-10-14 自 APP_AUTH_PASS 更名） | 非空 | auth.mode=dev 校验（`validation.py:30-33`）；即 wire token 固定盐摘要的明文 secret 源（2026-10-14 起，明文不上网——docs/server_auth.md） |

> 2026-10-12：6 个未被 active/fallback 引用的 provider（deepgram / elevenlabs /
> openrouter / deepseek / cartesia / omni.openai_realtime）的 `api_key` 已由
> `${裸名占位符}` 改为字面空串（方案 C：保留配置块模板，消除"变量必须存在"的启动
> 依赖）——原 6 个裸名变量不再需要 export。启用时在 yaml 块内填 key，或走 override：
> `__API_KEY` + `__ENABLED=true`（5 个 `enabled: false` 的块需一并覆盖），并用
> `LIVACHAT__<段>__ACTIVE`（或 fallback）指向它。

`configs/dev/lvdata_mcp.yaml`：

| 变量 | 要求 | 用途 |
|------|------|------|
| `DASHSCOPE_API_KEY` | **真实值** | audio/vision/embedding/summary 全部 provider |
| `DASHSCOPE_API_HOST` | **真实值** | 四段 provider `base_url` 的域名部分 |
| `DASHSCOPE_WORKSPACE_ID` | 真实值 | 四段 provider `workspace_id` |

`configs/dev/cli_tool.yaml`（lv-imagetool / lv-videotool / lv-audiotool / lv-3dtool，
2026-10-01/10-02 新增）与其设计文档（`docs/lv-*.md`）已于 **2026-10-04 随 CLI 工具集
迁至 videogo 仓库**——本仓库不再包含该配置与安装项，本文相关命令同步移除。

> **命名体系陷阱**：占位符（裸名 `DEEPGRAM_API_KEY`）与覆盖变量
> （`LIVACHAT__ASR__PROVIDERS__DEEPGRAM__API_KEY`）是两套机制——override 在占位符
> 解析**之后**生效，不能替代裸名；`services/.env.example` 目前只列了后者，照抄照样
> fail-fast。以本节表格（yaml 实证）为准，`.env.example` 在 §3.5 修正。
>
> 另：`tools/lvdata-mcp/README.md:30` 的 `export LVDATA_MCP_TOKEN=dev` 已过时——
> 当前 dev yaml 的 `server.token` 是字面量 `lvdata_dev_token`，无占位符；
> `LVDATA_MCP_TOKEN` 仅是 MCP 客户端侧的调用约定与非回环绑定时的服务端要求
> （`config.py:170`）。

### 1.5 `.env.example` 的额外缺陷（终审发现，两稿均未覆盖）

`services/.env.example` 现有的 provider key override 行**路径错误，当前完全无效**：
yaml 结构为 `asr.providers.<name>.api_key`，而 `:15-17`（ASR）、`:23-25`（LLM）、
`:28-31`（TTS）写的是 `LIVACHAT__ASR__DEEPGRAM__API_KEY` 形式（缺 `PROVIDERS` 段），
override 后生成 `asr.deepgram` 垃圾键，被 pydantic 默认 `extra='ignore'` 静默丢弃——
不报错也不生效。同文件 `:20` 的 `LIVACHAT__ASR__PROVIDERS__ALIBABA_BAILIAN__MODEL`
反而是正确写法。§3.5 一并修正。

### 1.6 其他前置

- `/opt/lvdata` 目录必须存在且当前用户可写（SQLite WAL 需要）：chat server 的
  `sqlite.chat_server_db_path` 父目录缺失 fail-fast；lvdata-mcp `data.root_dir` 同理。
- `/opt/lvdata/webroot`（agent 页面静态托管，`docs/webroot_feature.md`）：`webroot.enabled=true`
  时启动自动创建（父目录 `/opt/lvdata` 必须已存在，否则 fail-fast）并挂载 `/webroot`；
  **默认关闭——生产部署需在部署配置显式 `webroot: {enabled: true}`**。Hermes 侧需注册
  `playwright-dev` MCP（`--headless --ignore-https-errors`，自签证书必需）并冒烟（N6 验收门禁依赖：
  无浏览器自动化则 agent 不产出页面链接）。
- SSL 证书 `configs/dev/cert.pem|key.pem` 已入库，dev 配置 `ssl.enabled: true`
  （`configs/dev/server.yaml:15-18`）。

---

## 2. 目标流程

```bash
# ① 一次性：创建 venv + 一条命令安装（repo 顶层；需 pip ≥ 21.3，PEP 660 editable 联合解析）
python3.12 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e packages/sdk-python -e "services[test,dev]" -e "tools/lvdata-mcp[image,vec,dev]"

# ② 环境变量（每次开 shell；§1.4 全表）
export DASHSCOPE_API_KEY=sk-xxx
export DASHSCOPE_API_HOST=llm-xxxx.cn-beijing.maas.aliyuncs.com
export DASHSCOPE_WORKSPACE_ID=llm-xxxx
export APP_AUTH_TOKEN=dev-livachat-token
# 等价替代（推荐）：cp .env.example .env（仓库根）填好后直接启动——两个服务启动时
#   自动加载根 .env（setdefault：shell 注入优先；docs/ali_bailian_provider.md §9）；
#   上面的 export 写法仍有效且优先。如走 shell 注入：
#   set -a && source .env && set +a

# ③ 启动（任意 CWD；另需 /opt/lvdata 已存在且可写）
.venv/bin/livachat-server     # https://localhost:8080（dev ssl.enabled=true；热重载默认开）
.venv/bin/lvdata-mcp serve    # http://127.0.0.1:8090（/mcp /healthz /readyz）
```

extras 依据：

- `services[test,dev]`：对齐现 `Makefile:37`（test 供回归、dev 供 ruff/mypy）。
- `lvdata-mcp[vec]`：**必装**——dev yaml 配置了 embedding provider，而 `vec.py:85`
  的 `import sqlite_vec` 是 `_connect()` 内的硬导入（无 try/except）：启动时不崩，
  但首次向量操作（hybrid 检索 / `reindex --vectors`）即 `ImportError`。
- `lvdata-mcp[image]`：建议装——dev yaml `max_image_size: 1280`（≠0）走缩放路径；
  `analysis/vision.py:96` 的 PIL 导入有 ImportError 兜底（退化为直传原图），但
  `store.py:341` 的 `get_key_image` 缩放在缺 Pillow 时直接抛 `StoreError`。
  （**2026-09-27 注**：`get_key_image` 与缩放缓已删除——图片并入 `get_event`、无 `max_width`；
  Pillow 仍为 vision 入库缩放的必装项。）
- `lvdata-mcp[dev]`：pytest。

非开发场景（部署机只跑服务）可用最小变体：

```bash
.venv/bin/pip install -e packages/sdk-python -e services -e "tools/lvdata-mcp[image,vec]"
```

---

## 3. 设计

### 3.1 安装命令（核心变更点：一条 pip 命令）

```bash
.venv/bin/pip install -e packages/sdk-python -e "services[test,dev]" -e "tools/lvdata-mcp[image,vec,dev]"
```

- 单命令联合解析即可让 `livachat-server` 的裸名依赖 `livachat-protocol` 命中本地
  editable 包（§4.1 实证，未回退 PyPI）；现 Makefile 分两条命令只是历史写法。
- 零新增配置文件；语义直白；与现有 `make server-install` 同思路的合并。
- 全部 editable 是硬性要求（§1.3：非 editable 破坏配置发现与 SPA 托管）。
- 备选方案（根 `pyproject.toml` 元包 / 根 `requirements.txt`）均被拒绝：前者无法在
  dependencies 中表达 editable 本地路径，直接引用（`@ file:`）不可移植且改源码后需
  重装；后者与单命令等价却多一层间接。

### 3.2 livachat-server console script（唯一的代码改动）

**`services/pyproject.toml`** 增加（脚本名与包名同为 `livachat-server`，无冲突）：

```toml
[project.scripts]
livachat-server = "app.main:main"
```

**`services/app/main.py`** 重构为函数入口（保持模块级 `app` 供 uvicorn import string
与 gateway/session 测试；保持 `python app/main.py` 与 `make server-dev` 可用）：

```python
from app.application import create_app

app = create_app()


def main() -> None:
    import os
    from typing import Any

    import uvicorn

    from shared.config.loader import load_config, resolve_path
    from shared.config.ssl import build_ssl_kwargs

    cfg = load_config()
    reload = os.environ.get("LIVACHAT_SERVER_RELOAD", "1").lower() not in ("0", "false", "no")
    kwargs: dict[str, Any] = {}
    if reload:
        # uvicorn reload 默认监听 CWD，任意目录启动不可靠（§4.3）→ 显式锚定 services/
        kwargs["reload_dirs"] = [str(resolve_path("services"))]
    uvicorn.run(
        "app.main:app" if reload else app,  # reload 模式必须传 import string
        host=cfg.server.host,
        port=cfg.server.port,
        reload=reload,
        **kwargs,
        **build_ssl_kwargs(cfg.server.ssl),
    )


if __name__ == "__main__":
    main()
```

要点：

- `reload_dirs` **必须显式指定**：uvicorn `reload=True` 默认监听 **CWD**，从 repo 外
  启动时改源码不触发重启（§4.3 实证）。锚定经**现有公开 API** `resolve_path`
  （`loader.py:116-121`，内部即 `_project_root() / p`，任意 CWD 下均解析到 repo 内
  `services/`）——**不改动 loader.py**，不新增 `project_root()` 公开名/兼容别名。
- reload 默认开，与现 `make server-dev`（`reload=True` 硬编码）行为对齐。
  **可选增强**：`LIVACHAT_SERVER_RELOAD=0` 关闭热重载（单进程、传 app 对象），供
  演示/准生产使用；该开关与主流程解耦，可拆为后续 PR（拆出则退回
  `reload=True, reload_dirs=[...]` 的极简形态，其余不变）。
- 监听范围与现状一致：仅 `services/`；`configs/`、`packages/sdk-python/` 改动不
  触发重载。
- console script 启动时 `import app.main` 即执行模块级 `create_app()` →
  `load_config()`，缺 env 立即 fail-fast——与现状行为一致。
- 不加 CLI 参数：环境选择沿用 `LIVA_CHAT_CONFIG` env（loader 已支持）。

### 3.3 lvdata-mcp

零改动。`lvdata-mcp = "lvdata_mcp.main:main"` 已存在，`serve` 为默认子命令；配置
发现链（`--config` → `LVDATA_MCP_CONFIG` → `configs/dev/lvdata_mcp.yaml` → CWD）
已支持任意 CWD。

### 3.4 环境变量

以 §1.4 两表为准：`DASHSCOPE_API_KEY`、`DASHSCOPE_API_HOST`、`DASHSCOPE_WORKSPACE_ID`
+ `APP_AUTH_TOKEN` 需要真实值（两服务共用同一份环境变量）；原 6 个 disabled provider
裸名 export 已不需要（2026-10-12，见 §1.4）。可选覆盖：`LIVACHAT__` / `LVDATA_MCP__` 前缀
双层下划线（如 `LIVACHAT__SERVER__LOG_LEVEL=DEBUG`；注意 provider key 的 override
路径必须含 `PROVIDERS` 段，见 §1.5）。

### 3.5 配套修改

| 文件 | 修改 |
|------|------|
| `Makefile` | `server-install` 合并为单命令并改用 `.venv/bin/pip`（`.venv` 缺失时 fail-fast，优于误装全局；与 §2 流程完全一致）：`.venv/bin/pip install -e packages/sdk-python -e "services[test,dev]" -e "tools/lvdata-mcp[image,vec,dev]"`。`server-dev`（`cd services && python app/main.py`）、`server-test` 保留（沿用裸 `python`/`pytest`，需先激活 venv，与现状一致） |
| `.gitignore` | 增加 `.venv/`（当前未覆盖任何 `.venv`，仅忽略 `services/.env` 等）。无前导斜杠，同时覆盖 `tools/lvdata-mcp/.venv` |
| `REPO.md` | 「常用命令」更新为 §2 三步流程（venv + 单命令安装 + env 清单 + 两个启动命令）；注明 make 目标与 `.venv/bin/` 的关系（`server-install` 无需激活 venv；`server-dev`/`server-test` 需先 `source .venv/bin/activate`） |
| `tools/lvdata-mcp/README.md` | 快速开始改为复用根 `.venv`；删除过时的 `LVDATA_MCP_TOKEN=dev` 占位符说法（`:30`、`:111`；dev yaml 现为字面量 token） |
| `services/.env.example` | ① 增补**裸名占位符**段（当时 server.yaml 共 8 个占位符：`DASHSCOPE_API_KEY`、`APP_AUTH_PASS` 及 6 个 disabled provider 裸名；2026-09-30 起为 10 个，新增 `DASHSCOPE_API_HOST` / `DASHSCOPE_WORKSPACE_ID`，见 §1.4），与 `LIVACHAT__*` override 分区并注释区别；② **修正 §1.5 的错误 override 路径**（`:15-17`、`:23-25`、`:28-31` 补 `PROVIDERS` 段）；③ 注释注明：值为空的 `LIVACHAT__*` override 会**覆盖** yaml 已解析的值（`_apply_env_overrides` 不跳过空串，`loader.py:98-113`），不需要的 override 行应保持注释或删除；④ 2026-09-30：模板 `git mv` 至仓库根 `.env.example`，两个服务启动时自动加载根 `.env`（setdefault，shell 优先）——见 `docs/ali_bailian_provider.md` §9 |

### 3.6 端口与验证

| 服务 | 监听 | 验证 |
|------|------|------|
| livachat-server | `0.0.0.0:8080`，SSL on（`configs/dev/server.yaml:8-18`） | `curl -k https://localhost:8080/healthz`（自签证书必须 `-k`；端点见 `services/app/routes.py:16-17`）；`/readyz` 查 chat store 存活（`routes.py:21-22`） |
| lvdata-mcp | `127.0.0.1:8090`（`configs/dev/lvdata_mcp.yaml:5-10`） | `curl -s http://127.0.0.1:8090/healthz` → `{"status":"ok"}`；`/readyz` 首轮 poll 后 200（`tools/lvdata-mcp/src/lvdata_mcp/server.py:41-44, 76-77`） |

---

## 4. 实证验证（2026-09-01 本机执行；2026-09-02 终审复核）

在临时 venv（非 repo 内）完整执行目标命令，未改动 repo：

1. ✅ 单命令三 editable 联合解析成功：`Successfully installed livachat-protocol-0.1.0
   livachat-server-0.1.0 lvdata-mcp-0.1.0`（pip 将 `livachat-protocol` 解析为 editable
   本地包，未回退 PyPI）。
2. ✅ console script + `uvicorn.run(..., reload=True)` 在 macOS 正常（toy 包实验：
   进程启动、WatchFiles reloader spawn、请求 200）。pip 生成的 wrapper 自带
   `if __name__ == "__main__"` 保护，无 multiprocessing spawn 递归问题。
3. ⚠️ uvicorn `reload=True` **默认监听 CWD**——从 `/tmp` 启动时监听 `/private/tmp`，
   改源码不触发重启。⇒ 设计必须显式传 `reload_dirs`（§3.2）。
4. ✅ 从 `/tmp` 导入 `app.application / asr.factory / session.manager /
   agent_relay.event_bus / bff.router / recording.service` 等全部成功；两个
   `_project_root()` 均解析到仓库根；两个配置文件均被自动发现。
5. ✅ 从 `/tmp` 以 §1.4 的 env 全量 `load_config()` 成功（livachat: host=0.0.0.0
   port=8080 ssl=True asr=alibaba_bailian；lvdata-mcp: 127.0.0.1:8090
   root=/opt/lvdata）；缺 env 时 fail-fast 报错清晰
   （`ConfigError: Environment variable 'APP_AUTH_PASS' ... is not set`）。
6. ✅ `lvdata-mcp` console script 已可用（`serve/reindex/eval/stage`）。
7. 环境备注：本机 `cryptography` 无预编译 wheel 走了 sdist 构建（Rust 可用，构建
   成功但慢），属机器差异，非设计问题。
8. 终审复核（2026-09-02）：§1 全部行号引用与代码逐一核对一致；`/healthz` `/readyz`
   端点出处确认（`services/app/routes.py:16-22`、
   `tools/lvdata-mcp/src/lvdata_mcp/server.py:41-44, 76-77`）；新发现
   `.env.example` override 路径缺 `PROVIDERS` 段问题（§1.5，已纳入 §3.5）；
   `resolve_path` 公开 API 确认存在（`loader.py:116-121`），A 稿方案据此替代
   B 稿的 loader.py 重构。

---

## 5. 风险与边界

1. **editable 包名裸露**：livachat-server 以 flat layout 导出 `app / session /
   shared / media` 等通用顶层名，同一 venv 中若未来出现同名第三方包将冲突（当前
   三方依赖树实证无冲突）。长期可考虑 services 改 src layout + 统一 namespace，
   超出本设计范围。
2. **新增顶层 package 需重装**：setuptools editable finder 的映射在安装时生成，
   services 下新增顶层包后需重跑安装命令（现状如此，README 已有说明）。
3. **禁止去掉 `-e`**：非 editable 安装破坏配置发现（`_project_root()` 依赖
   `__file__` 在 repo 内）。（原举例 `parents[2]`→`/mylife` 静默不挂载已随
   2026-10-13 /mylife 托管摘除删除，结论不变。）
4. **`/opt/lvdata` 必须存在且可写**：两个服务均 fail-fast（chat_server.db 父目录 /
   data.root_dir / WAL）。
5. **占位符与 override 两套 env 命名**易混淆（§1.4）：裸名用于占位符解析（必须
   存在，可为空）；`LIVACHAT__*` 用于覆盖配置值（可选；**空串 override 会清空
   已解析值**）。经 §3.5 的 `.env.example` 修正与 README 说明缓解。
6. **reload 监听范围**：仅 `services/`；改 `configs/` 或 `packages/sdk-python/`
   需手动重启（与现状 `cd services` 启动的监听范围一致）。
7. **alembic / pytest 工作流不变**：迁移仍在 `services/` 下执行；`make server-test`、
   `tools/lvdata-mcp` 的 `reindex/eval/stage` 子命令不受影响。
8. **Python 钉 3.12**：`pyogg`/`opuslib` 等二进制依赖在 3.13 的 wheel 可用性未
   验证，文档与 venv 创建统一用 `python3.12`。
9. **命令按 POSIX 书写**：`.venv/bin/` 路径适用于 macOS/Linux；Windows 下可执行
   文件在 `.venv\Scripts\`，且 services 依赖已排除 Windows 的 `pyogg`
   （`services/pyproject.toml:28`），Windows 支持未验证，暂不在本设计范围。

---

## 6. 实施清单

- [ ] `services/pyproject.toml`：+`[project.scripts] livachat-server = "app.main:main"`
- [ ] `services/app/main.py`：提取 `main()`，`reload_dirs=[str(resolve_path("services"))]` +
      `LIVACHAT_SERVER_RELOAD` 开关（开关部分可拆后续 PR）
- [ ] `Makefile`：`server-install` 合并为单命令并改用 `.venv/bin/pip`
- [ ] `.gitignore`：+`.venv/`
- [ ] `REPO.md`：常用命令更新为 §2 流程 + make 目标与 venv 激活关系说明
- [ ] `tools/lvdata-mcp/README.md`：快速开始对齐根 venv，修正 `LVDATA_MCP_TOKEN` 过时描述
- [ ] `services/.env.example`：增补 8 个裸名占位符 + 修正缺 `PROVIDERS` 段的 override
      路径 + 空 override 风险提示注释

（注：B 稿实施清单中的 `loader.py` 新增 `project_root()` 公开 API 一项已删除——
`resolve_path` 即为现成公开 API，无需改动 loader.py。）

## 7. 验收步骤

```bash
cd <repo>
python3.12 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e packages/sdk-python -e "services[test,dev]" -e "tools/lvdata-mcp[image,vec,dev]"
ls .venv/bin/livachat-server .venv/bin/lvdata-mcp   # 两个入口均存在

# env（§1.4 全表）+ /opt/lvdata 就绪后：
cd /tmp && <repo>/.venv/bin/livachat-server          # 任意 CWD 可起，监听 8080
cd /tmp && <repo>/.venv/bin/lvdata-mcp serve         # 任意 CWD 可起，监听 8090
curl -k https://localhost:8080/healthz
curl -s http://127.0.0.1:8090/healthz

# reload 开关（可选增强，若已随主流程落地）
LIVACHAT_SERVER_RELOAD=0 <repo>/.venv/bin/livachat-server   # 单进程无热重载

# 回归：旧路径仍可用，测试不回归
source .venv/bin/activate
make server-install                                  # = §3.1 单命令（.venv/bin/pip）
make server-dev                                      # = cd services && python app/main.py
make server-test
.venv/bin/python -m pytest tools/lvdata-mcp/tests/ -q
```
