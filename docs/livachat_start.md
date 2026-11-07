# LivaChat 本地服务管理脚本（livachat.sh）— 分析与设计

> 状态：设计定稿 v1.3（2026-10-14：运行地址显示改取本机 LAN IP（无则回退 127.0.0.1）——
> 仅显示层，探测仍走 127.0.0.1；§3.5/§3.7）。v1.2（2026-10-12：决议已回填 §7；含同日 review
> 修订、test 环境改造 §3.0、首次生成端口自动避让、start 预检细化（环境变量 + ffmpeg/libopus））。
> 验证进度见 §5。
> **hermes 不安装、不配置**（用户明示）；本脚本不读写 `~/.hermes/` 任何内容。

## 1. 背景与目标

用单脚本管理本机两个常驻服务（统一跑 **test 环境**——与真实数据隔离，见 §3.0），
替代"翻 README 手动起两个终端"的现状：

| 服务 | 进程 | 端点 |
|------|------|------|
| livachat-server | `.venv/bin/livachat-server`（console script `app.main:main`） | https://localhost:8080 |
| lvdata-mcp | `.venv/bin/lvdata-mcp serve` | http://127.0.0.1:8090 |

4 个子命令：

```
livachat.sh install   # uv + Python 3.12 + 根 .venv + editable 安装服务与依赖（幂等）
livachat.sh start     # 后台启动两个服务，等待 /healthz 就绪
livachat.sh stop      # 优雅停止两个服务
livachat.sh status    # 进程 + /healthz 状态
```

**非目标**（明确不做）：

- hermes（外部服务，另一套 `~/.hermes/` 配置与日志）——不装不配不启停；
- mylife 前端（npm，Makefile `mylife-*` 维持原样）、mock server、docker compose
  （REPO.md:73「已无需启动任何容器」）；
- 生产部署形态（systemd/Docker 已有各自手册，见 tools/lvdata-mcp/README.md「生产部署」）；
- 开机自启 / launchd / systemd daemon 化。

## 2. 现状分析（以 codebase 为据）

### 2.1 安装现状

- 手动三步（REPO.md:49-54）：`python3.12 -m venv .venv` → `pip install -U pip` →
  `pip install -e packages/sdk-python -e "services[test,dev]" -e "tools/lvdata-mcp[image,vec,dev]"`；
  等价命令 `make server-install`（Makefile:35-36，用 `.venv/bin/pip`，**依赖根 .venv 里有 pip**）。
- `docs/install_design.md` §2 为同一流程的定稿；§1.4 环境变量表；
  §1.6 前置：`/opt/lvdata` 必须已存在且可写。
- 本机实测（2026-10-12）：`uv 0.11.14` 已在 `~/.local/bin/uv`；uv-managed
  `CPython 3.12.13` 已安装；`ffmpeg` 已装（lvdata-mcp 音频转码前置，其 README「前置条件」）；
  `/opt/lvdata` 存在。
- 本 worktree（wt_kimi）根 `.venv` 为中断 install 的残留（uv venv --seed 完成、依赖未装，
  重跑续装，见 §5）；另有 `services/.venv`（uv 建的空 venv，无依赖）——README 约定的是
  **仓库根** `.venv`，本设计以根 `.venv` 为准。

### 2.2 启动与停止现状

- chat server：`services/app/main.py:15-28`（load_config → reload 判定 → uvicorn.run 取
  host/port/ssl）；**热重载默认开**（`LIVACHAT_SERVER_RELOAD` 默认 `"1"`，
  `reload_dirs=[services]`——改 `configs/` 不触发，REPO.md:64）；dev ssl.enabled=true
  （自签证书，configs/dev/server.yaml:17-20），故探活须 `curl -k`。
- lvdata-mcp：配置加载链 = `--config` → `LVDATA_MCP_CONFIG` → `configs/<env>/lvdata_mcp.yaml`
  → CWD（tools/lvdata-mcp/README.md:59-67），任意 CWD 可启动。
- 停止现状：无任何既有脚本（scripts/*.sh 均为 0 字节占位，实测 `wc -l`），靠终端 Ctrl-C；
  本机当前实例为**手动从 wt_d4flash worktree** 启动（ps 实测：`wt_d4flash/.venv/bin/python3.12
  .venv/bin/livachat-server`、`... lvdata-mcp`），日志经 tee 落 `~/temp/livachat.log` /
  `~/temp/lvdata_mcp.log`（现状习惯；本设计改为仓库 `logs/`，见 §3.7）。

### 2.3 关键机制事实（设计依据）

| 事实 | 证据 | 对设计的影响 |
|------|------|--------------|
| `/healthz` 免鉴权 | chat：app/routes.py:16-18；lvdata：server.py:188-189、:201「仅作用于 /mcp 与 /api；/healthz /readyz 不鉴权」 | status/就绪等待可用无凭证 curl |
| chat 的鉴权是 WS 每连接 token | gateway/endpoint.py:30（`token: str = Query(...)`） | 不阻碍 HTTP 探针 |
| lvdata 缺 chat_server.db 不报错 | recording_watch.py:121-122「缺失/失败 → False（优雅空转，非错误）」 | 启动顺序无硬约束（仍先起 chat server） |
| uvicorn reload 父进程处理 SIGTERM | uvicorn `supervisors/basereload.py:18-21`（SIGTERM 注册在 :81-82；退出时 shutdown→`process.terminate()` 子进程在 :103-108） | **kill 父进程 pid 即可全清**，pid 文件方案成立 |
| 两服务启动时自动加载**仓库根** `.env`（setdefault，shell 优先） | install_design.md §1.4、§2；tools/lvdata-mcp/README.md:35-38 | `.env` 存在即无需 export；shell 注入亦可 |
| 端口 8080/8090 独占 | configs/dev/server.yaml:12、configs/dev/lvdata_mcp.yaml:7 | 同一端口只能一个实例；start 有冲突预检；test 环境首次生成自动避让（§3.0） |
| 数据根须存在 | install_design.md §1.6；chat sqlite 父目录 fail-fast（chat_store.py:259-269）、lvdata `root_dir` 默认 `/opt/lvdata` | dev 为 `/opt/lvdata`；test 环境映射为 `<repo>/data`（§3.0，start 自动创建） |
| 配置环境可经环境变量切换（不改代码；只有文件路径变量，无环境名开关） | chat：`LIVA_CHAT_CONFIG`（loader.py:97-103）；lvdata：`LVDATA_MCP_CONFIG`（lvdata-mcp config.py:297-307） | start 注入指向 `configs/test/` 的绝对路径 |
| 环境变量透传能力 | `LIVACHAT_SERVER_RELOAD`（main.py）、`LIVACHAT__*` override（loader）、`LIVACHAT_PIPELINE_MODE`（REPO.md:94-106） | start 无需专门支持，`nohup` 继承即透传 |
| ffmpeg / libopus 均为**运行时**依赖（启动不校验） | ffmpeg：lvdata `analysis/audio.py:162`/`:341` 子进程调用；libopus：`media/opus.py:16-36` 懒加载（ctypes 自研加载器——`opuslib` pip 依赖全仓无 import、为存量冗余）、`gateway/endpoint.py:77` 函数内导入 | 缺失时服务"能启动但故障"（语音会话 / 音频分析）→ start 前置实测暴露（§3.3） |

## 3. 设计

### 3.0 运行环境与数据根（test 环境）

脚本管理的实例统一跑 **test 环境**，本地数据面与 dev 的真实数据（`/opt/lvdata`）隔离：

- 配置目录 `configs/test/`：**本地生成、gitignore**（`/configs/test/`）。`install` **按文件
  逐项补齐缺失项**（从 `configs/dev/` 复制并改写；已存在的文件一律不动），规则：

  | dev 来源 | test 生成 | 改写 |
  |----------|-----------|------|
  | `configs/dev/server.yaml` | `configs/test/server.yaml` | `/opt/lvdata` → `<repo>/data`（`sqlite.lvdata_db_path`、`sqlite.chat_server_db_path`、`recording.base_dir`、1 处注释）；`configs/dev/` → `configs/test/`（prompt 取值行 ×4 + 注释行 ×1、ssl cert/key、1 处注释）；`observability.log_file.dir` → `logs/test`（2026-10-13） |
  | `configs/dev/lvdata_mcp.yaml` | `configs/test/lvdata_mcp.yaml` | `data.root_dir`: `/opt/lvdata` → `<repo>/data`；`server.log_file.dir` → `logs/test`（2026-10-13） |
  | `configs/dev/prompts/*.txt` | `configs/test/prompts/` | 原样 |
  | `configs/dev/{cert,key}.pem` | `configs/test/` 同名 | 原样（server.yaml ssl 引用） |

- 数据根 `<repo>/data/`：**gitignore**（`/data/`），`start` 自动创建（chat sqlite 要求父目录
  存在，fail-fast，chat_store.py:259-269）。
- 服务选择该环境的方式（不改代码）：start 注入 `LIVA_CHAT_CONFIG=<abs>`（loader.py:97-103）
  与 `LVDATA_MCP_CONFIG=<abs>`（lvdata-mcp config.py:297-307）。
- **隔离边界**：只隔离本地文件数据（sqlite / 录制 / webroot）。外部依赖不隔离——LLM/ASR/
  TTS/omni/avdetector 走真实百炼 API（真实计费），`agent_relay` 连真实 hermes
  （`server.yaml` 里 `:9119`）；如需静默，自行在 `configs/test` 关闭对应段（如
  `agent_relay.enabled: false`）。
- **端口：首次生成时自动避让（2026-10-12 指示）**：生成 `configs/test` 的 yaml 时对基准
  8080/8090 做试绑定检查（`0.0.0.0` 与 `127.0.0.1` 双地址、含 SO_REUSEADDR，覆盖 wildcard
  与回环两类监听——macOS 上仅测 `0.0.0.0` 会漏检 `127.0.0.1` 监听），被占则 +100、+200…
  （step 100，最多 10 次）取首个空闲；`server.yaml` 的 `bff.lvdata_api_url` 同步为实际
  lvdata 端口（lvdata yaml 已存在时按其 `server.port` 对齐）。**已存在的 configs/test
  不追溯**（想重选端口：删除 `configs/test` 后重跑 install）。脚本端口解析（§3.3）读
  `configs/test` 自动跟随——dev 实例占着 8080/8090 时，test 环境取 8180/8190 可与之并存
  （手工改端口时注意同步 `bff.lvdata_api_url`，自动避让已处理该点）。
- 路径写法说明：`configs/test` 是每 checkout 的本地文件，`<repo>/data` 以**绝对路径**写入
  （chat sqlite / lvdata `root_dir` 均按原样取用、不做项目根解析）；ssl cert 与 prompt
  相对路径按项目根解析（ssl.py `_abs`、llm/prompt.py `resolve_path`），保持 dev 的
  相对写法即可。

### 3.1 命令面

| 命令 | 行为 | 退出码 |
|------|------|--------|
| `install` | uv（缺则装）→ `uv python install 3.12` → 建/复用 `.venv` → 生成 `configs/test`（缺失时）→ uv pip editable 安装三包 | 0 成功 |
| `start` | 前置检查 → 后台启动两服务（nohup + pid 文件）→ 等 `/healthz` ≤30s | 全部就绪 0；任一失败/超时 1 |
| `stop` | SIGTERM → 10s → SIGKILL；清理 pid 文件；杀前校验命令行（防 PID 复用误杀） | 全部停止（或被清理）0；有进程残留 1 |
| `status` | pid 存活性 + `/healthz` | 全部运行且健康 0，否则 1 |
| 无参/`help` | usage | 0 |
| 未知命令 | usage | 2 |

### 3.2 install

1. **uv**：`command -v uv` 命中即用；否则先把 `~/.local/bin` 加进 PATH（uv 官方安装位置），
   仍无则执行 uv 官方安装脚本（`curl -LsSf https://astral.sh/uv/install.sh | sh`）。
2. **Python 3.12**：`uv python install 3.12`（幂等；本机已有 managed 3.12.13）。
3. **`.venv`**：已存在则复用（版本非 3.12 只警告、不自动删除）；不存在则
   `uv venv --python 3.12 --seed .venv`——`--seed` 装 pip/setuptools/wheel，
   **保住 Makefile:35-36 `server-install` 的 `.venv/bin/pip` 路径**。
4. **test 环境配置**：`configs/test` 缺失文件逐项从 `configs/dev/` 复制生成（规则见 §3.0，
   含首次生成的端口自动避让）；已存在的文件一律不动（不覆盖本地改动），整目录齐备时为
   静默 no-op。
5. **依赖（纯运行，review 决议 2026-10-12）**：
   `uv pip install --python .venv/bin/python -e packages/sdk-python -e services -e "tools/lvdata-mcp[image,vec]"`
   - 不装 `services[test,dev]`（= pytest 系 / ruff / mypy / ipython，开发工具）；
     需要时补：`uv pip install -e "services[test,dev]"`，或 `make server-install`
     （seed 保证其 `.venv/bin/pip` 在位）。
   - lvdata-mcp 保留 `[vec]`（sqlite-vec，必装——依据 docs/install_design.md §2）与
     `[image]`（Pillow；dev yaml `max_image_size: 1280` 走缩放路径）。
   - 运行期依赖缺口已实测补齐：SQLAlchemy `ext.asyncio` 需 greenlet——`services/pyproject.toml`
     已改声明 `sqlalchemy[asyncio]`（METADATA 实测：2.0 有 x86_64 等平台 marker 兜底、2.1 起
     移除，greenlet 仅随 `[asyncio]` 等 extra 提供；见 §5）。
6. 末尾提示下一步（`.env` 填写 / `start`）。**不写 `.env`**（属用户数据）；数据根
   `<repo>/data` 由 start 自动创建。

### 3.3 start

前置检查按序：`curl` 存在 → `.venv` 两个 console script 存在（否则指向 install）→
`configs/test` 已生成且完整（缺失/不完整报错指向 install）→ 自动创建数据根 `<repo>/data` →
必需环境变量逐项检查 → 系统依赖检查（libopus / ffmpeg）→ **端口冲突预检**。

- 冲突预检：本 checkout 无 pid 记录但 `/healthz` 有响应 → `die`（其它 worktree / 手动实例，
  提示先停）。非 HTTP 服务的占位（探针失联）则由 uvicorn 自报
  "address already in use" 进日志，由就绪检测兜底。
- 端口不硬编码：用 `.venv` 的 python 读 `configs/test/*.yaml` 的 `server.port`
  （读不到再退回 REPO.md:64-66 记录的 8080/8090）。
- 环境注入：两服务分别以 `LIVA_CHAT_CONFIG` / `LVDATA_MCP_CONFIG` 指向 `configs/test/`
  绝对路径启动（§3.0）。
- 环境预检（`check_required_env`）：必需项 = `APP_AUTH_TOKEN` / `DASHSCOPE_API_KEY` /
  `DASHSCOPE_API_HOST`（非空；依据 install_design.md §1.4、chat `validation.py:30-33`、
  lvdata `validate_required`）；有效值解析与 loader 一致——shell 优先（含"已设置但为空"），
  未设置时取仓库根 `.env`（同键后行覆盖、容忍 export 前缀与成对引号）。`.env` 不存在或
  必需项为空 → `die` 并**逐项列出**缺失/为空的变量名与修复指引；`DASHSCOPE_WORKSPACE_ID`
  为空仅 `warn`（§1.4 允许空串——不发 Workspace 头，TTS 可能 401/411）。
- 系统依赖预检（`check_system_deps`）：libopus 用**服务自身加载器**实测
  （`media.opus._load_opus()`——与 chat 语音编解码同一路径、候选路径权威，media/opus.py:16-36），
  失败即 `die`（`brew install opus` / `apt install libopus0`；每会话 opus 编解码必需、
  缺失时服务可启动但会话必败）；ffmpeg 缺失仅 `warn`（lvdata-mcp 音频分析 ogg→wav 转码需要、
  查询平面不受影响；tools/lvdata-mcp/README.md 前置条件）。
- 启动顺序：chat server → 等 healthz；lvdata-mcp → 等 healthz（30s；期间进程若已退出 →
  立即判失败并提示 `tail` 日志，不等满 30s——配置/绑定错误均发生在被启动进程内，
  uvicorn 的 `bind_socket` 也在 reload 父进程执行，见 uvicorn/main.py:613-615）。
  **reload 模式另设子进程哨兵**（`has_server_child`：`ps` 过滤僵尸与常驻的
  multiprocessing `resource_tracker`——实测 uvicorn reloader 下子进程崩溃后为
  [resource_tracker 存活 + 服务器子进程僵尸]，以"是否有子进程"判活会失效；开关判定与
  main.py 一致，`LIVACHAT_SERVER_RELOAD=0` 时不启用）：启动 5s（壁钟宽限）后父进程已无
  "服务子进程"（子进程崩溃、父进程驻留）即提前判失败——掩盖窗口从 30s 缩到 ~8s
  （实测故障 app；含探活时延）。
- 日志：server 与 mcp **均为进程内轮转**——`logs/test/livachat.log`、`logs/test/lvdata_mcp.log`
  （各 50MB×3，目录不存在自动创建；test 子目录与手动 dev 实例的 `logs/` 隔离）+ `logs/test/*.boot.log`
  （stderr 早段，start 截断）；**2026-10-13 变更，见 docs/server_logging.md**；pid 写 `.run/*.pid`。
- 幂等：已在运行（pid 存活）→ 跳过。
- 透传：`nohup` 继承当前环境——`LIVACHAT_SERVER_RELOAD=0 ./livachat.sh start` 关热重载、
  `LIVACHAT__SERVER__LOG_LEVEL=DEBUG`、`LIVACHAT_PIPELINE_MODE=asr_only` 等直接可用。

### 3.4 stop

读 pid 文件 → 无/陈旧（进程不在）→ 清理并报"未运行"；存活 → **命令行校验**（`ps -o command=`
含服务名，防 PID 复用误杀，不匹配则跳过杀进程并清理 pid 文件）→ SIGTERM → 轮询 10s →
仍存活 SIGKILL → 再校验并报告（仍残留 → 退出码 1）。reload 子进程正常由父进程负责终止
（§2.3 表）；**SIGKILL 兜底路径**下子进程不会被 terminate（会 orphan 并继续占端口）——
杀父前记录直接子进程，父死后对残留子进程补 TERM→SIGKILL（实测：`kill -9` reloader 后
server child 孤儿化仍监听端口）。
只动 `.run/*.pid` 记录的进程，**不碰其它实例**。

### 3.5 status

每服务三态：

```
livachat-server  运行中（健康）  pid=12345  https://<lan-host>:8080/healthz
livachat-server  运行中（健康检查失败）  pid=12345  日志: <repo>/logs/livachat.log
livachat-server  未受本脚本管理，但 https://127.0.0.1:8080/healthz 有响应（其它 worktree / 手动启动的实例）
livachat-server  未运行
```

**显示地址取本机 LAN IP（2026-10-14 用户指示）**：`运行中（健康）`行与 start 的 `就绪:`
行显示 `https://<local_ip>:<port>/healthz`（如 `<lan-host>`）——该行即健康端点（自签
证书，需 `curl -k`）；手机端「服务」设置填 `<local_ip>:<port>`（App 输入框仅接受
`ip:port` / `域名:port`，**不接受**含 scheme / 路径的完整 URL——`parseServerAddress`
整串匹配，见 docs/setting_server_address.md）。取法：venv python UDP connect `192.0.2.1`
（不发包，仅解析默认路由并取源地址）；取不到（离线 / 无默认路由 / 无 venv python）→
回退 `127.0.0.1`。**仅显示层替换**：健康探测、冲突预检与 `未受本脚本管理` 行仍用
`127.0.0.1`（保只绑回环实例的检出能力、不受网卡/防火墙波动影响；该行实例绑定未知，显示
LAN IP 会误导）。lvdata-mcp 不适用（设计即绑 127.0.0.1——configs/dev/lvdata_mcp.yaml:6，
手机不直连）。已知边界：显示的是默认路由网卡地址——VPN 抢占默认路由时显示 VPN IP（手机
不可达）；多网卡并存（如有线优先于 Wi-Fi）时显示的未必是手机所在网段。显示前提：
server 配置 `host: 0.0.0.0`（configs/dev/server.yaml:11，test 继承）——改绑回环则显示
失真（脚本不感知）。

### 3.6 路径与状态文件

| 路径 | 用途 | 归属 |
|------|------|------|
| `<repo>/.venv` | Python 3.12 环境（README 约定） | 已 gitignore |
| `<repo>/.run/livachat.pid`、`lvdata_mcp.pid` | 本 checkout 的进程记录 | gitignore `.run/` |
| `<repo>/logs/test/livachat.log`、`logs/test/lvdata_mcp.log`（均进程内轮转 50MB×3）、`logs/test/livachat.boot.log`、`logs/test/lvdata_mcp.boot.log`（stderr 早段，start 截断） | 运行日志（目录自动创建；2026-10-13 轮转 + test 子目录隔离见 docs/server_logging.md） | gitignore `/logs/` |
| `<repo>/configs/test/` | test 环境配置（本地生成，改写规则 §3.0） | gitignore `/configs/test/` |
| `<repo>/data/` | test 数据根（chat sqlite / 录制 / webroot 全在其下） | gitignore `/data/` |
| `configs/dev/server.yaml`、`lvdata_mcp.yaml` | 生成源（只读，不动） | 仓库内 |

### 3.7 关键决策与取舍

- **pid 文件放仓库 `.run/`（每 checkout 各自为准）而非 `~/temp`**：多 worktree 互不踩；
  跨 worktree 的端口冲突由预检/status 呈现。
- **日志放仓库 `logs/`（review 决议 2026-10-12）**：与 `.run/` 同源、每 checkout 独立；
  **（2026-10-13 修订：test 实例日志移至 `logs/test/` 子目录，与手动 dev 实例的 `logs/` 隔离。）**
  不再跟随手动启动时代的 `~/temp` 习惯（那是现状，不是设计目标）。
- **test 环境隔离真实数据（2026-10-12 用户指示）**：脚本实例只写 `configs/test` +
  `<repo>/data`；不碰 `/opt/lvdata`（真实 `chat_server.db` 是设备/消息的唯一事实源，
  测试写入会污染真数据）。`configs/test` 本地生成、不提交，每 checkout 独立。
- **不自动杀未受管进程、不自动换端口**：宁可拒绝启动并说明，不越权动用户实例。
- **热重载保持 main.py 默认（开）**：与用户现有工作方式一致；需要稳定单进程时透传
  `LIVACHAT_SERVER_RELOAD=0`。
- **不做 `.env` 自动生成**：`install` 只提示 `cp .env.example .env`。
- **运行地址显示取本机 LAN IP（2026-10-14 用户指示）**：status 健康行 / start 就绪行的
  server 地址显示 LAN IP（无则 `127.0.0.1` 回退；取法与边界见 §3.5）——**仅显示层**，
  探测/预检仍 `127.0.0.1`（保只绑回环实例的检出能力）；lvdata-mcp 不适用（设计绑 127.0.0.1）。

## 4. 错误处理矩阵

| 情形 | 行为 |
|------|------|
| `.venv`/console script 缺失 | start 报「先 ./livachat.sh install」 |
| `configs/test` 缺失/不完整（本地生成、gitignore） | start 报错并指向 install（逐项补齐生成） |
| `<repo>/data` 缺失 | start 自动创建（无需手工前置） |
| `.env` 不存在或必需项为空（APP_AUTH_TOKEN / DASHSCOPE_API_KEY / DASHSCOPE_API_HOST） | start `die`：逐项列出缺失/为空的变量名 + `cp .env.example .env` 指引 |
| `DASHSCOPE_WORKSPACE_ID` 为空 | start `warn` 后继续（不发 Workspace 头，TTS 可能 401/411） |
| libopus 加载失败（chat 语音编解码必需） | start `die`：`brew install opus` / `apt install libopus0` 指引 |
| 缺 ffmpeg | start `warn` 后继续（lvdata 音频分析降级；查询平面不受影响） |
| 端口被其它实例占用且健康端点有响应 | start 拒绝并提示先停 |
| 启动阶段错误（配置/.env/端口占用——均在被启动进程内失败） | 进程即退 → 就绪等待提前判失败 + `tail -n 50` 日志指引 |
| reload 模式下子进程启动失败（lifespan/依赖导入错误——2026-10-12 首装缺 greenlet 实测即此类） | 父进程驻留、**不退出**；子进程哨兵提前判失败（壁钟 5s 宽限，实测 ~8s，见 §3.3）；查日志定位后 stop 再 start |
| 30s 未就绪但进程存活 | 警告 + 日志指引，退出码 1 |
| pid 文件陈旧（进程已死） | start 覆盖；stop/status 自动清理/按未运行报 |
| PID 被复用（pid 文件指向无关进程） | stop 杀前命令行校验，不匹配则拒绝误杀并清理 pid 文件 |
| SIGTERM 10s 不退出 | SIGKILL 兜底 + 再校验；仍残留 → stop 退出码 1 |
| uv 缺失 | 官方脚本安装；失败给出 uv 安装文档链接 |

## 5. 实测记录（2026-10-12）

| 项 | 结果 |
|----|------|
| `bash -n` 语法 | ✅ 通过 |
| `help` / 未知命令 | ✅ usage 正常；未知命令退出码 2 |
| `status`（当前本机态：两端口被 wt_d4flash 实例占用、无本 checkout pid） | ✅ 正确识别为"未受本脚本管理，但有响应"，退出码 1；端口从 configs 解析正确 |
| `stop`（无 pid 记录） | ✅ 两服务报"未运行"，退出码 0 |
| `install`（2026-10-12 续跑完成） | ✅ 复用断点 `.venv` → 三包 editable 安装完成；`configs/test` 重建并按避让逻辑生成（server=8180 / lvdata=8190 / bff=8190）。**实测发现纯运行依赖缺口**：`services/pyproject.toml` 仅声明 `sqlalchemy>=2.0`，sqlalchemy 2.1.x 起 greenlet 不再随平台 marker 兜底 → `ext.asyncio` 导入即崩（chat server 启动失败）；已修 `sqlalchemy[asyncio]`（顺带修好 canonical README 安装路径） |
| 决议同步后复验（纯运行依赖 + `logs/` 目录） | ✅ `bash -n` / `help`（日志路径已指向 `<repo>/logs/`）/ `status` 复验通过 |
| review 修订复验（stop 防护/退出码） | ✅ PID 复用防护生效（不匹配进程未被误杀、pid 文件被清理）；模拟服务名进程正常停止路径通过——均用假进程实测，未触碰真服务 |
| test 环境改造复验（2026-10-12） | ✅ `configs/test` 生成：diff 仅路径/注释改写（清单见 §3.0 规则表）；材料化的**逐项补齐**（部分缺失：只补缺失文件、其余不动）与**整目录重建**均与手工生成逐字节一致，齐备时 no-op；gitignore 生效（`/configs/test/`、`/data/`、`.run/`、`/logs/`）；`bash -n` / `status` 复验通过 |
| 端口自动避让复验（2026-10-12） | ✅ 本机 8080/8090 被 dev 实例占用时：全新生成自动选 **8180/8190** 且 `bff.lvdata_api_url` 同步 8190；lvdata yaml 已存在（含自定义端口）时 server.yaml 按其 `server.port` 对齐；齐备时 no-op；双地址试绑定判定正确（8080 wildcard 监听与 8090 回环监听均检出——单测 `0.0.0.0` 在 macOS 会漏检后者） |
| start 环境预检复验（2026-10-12） | ✅ 6 场景沙箱：无 `.env`/无注入 → 逐项列出 3 项缺失（rc 1）；shell 注入齐全 → 通过；`.env` 单项为空 → 精确列出该项；`WORKSPACE_ID` 空 → 仅警告放行；shell 优先于空 `.env` 值；export 前缀 + 成对引号解析正确 |
| start/stop/status 全路径实测（2026-10-12） | ✅ 与 dev 实例（8080/8090 运行中）并存、全程互不影响：start 两服务就绪（8180/8190，rc 0）→ status 运行中（健康）×2（rc 0）→ 幂等 start"已在运行，跳过"（rc 0）→ stop 两服务已停止（rc 0）→ status 未运行（rc 1）、端口释放。`data/` 落仓库内（chat_server.db / lvdata_inventory.db / webroot 全套）；日志结构化启动完成、lvdata watcher 正常轮询。期间真实复现 §4"reload 子进程崩溃父进程驻留"（greenlet 缺依赖时）并由 stop 正确清理 |
| review 修订 2 轮（2026-10-12）：reload 子进程哨兵 + stop orphan 清理 | 一轮 `pgrep -P` 版**真实机制下失效**——实测复现（uvicorn --reload 故障 app）：崩溃后父进程子进程 = [resource_tracker 存活 + 服务器子进程僵尸]，"是否有子进程"恒真。改为 `has_server_child`（ps 过滤两类）+ 壁钟 5s 宽限。验证：控制组（健康 reload app）rc 0 / 1s 不误报；实验组（故障 app）rc 2 / **8s** 提前判失败（原 23s 迭代计法 → 壁钟修正）；单元（普通子进程 true / resource_tracker 名 false / 僵尸 false）。另实测发现 `kill -9` reloader 后 server child 孤儿化继续监听端口 → stop 增加 SIGKILL 兜底后的残留子进程补杀（TERM 免疫假父实测：父死+子清，rc 0）；真机 start/stop 回归通过（stop 正常路径 2.1s 无额外开销） |
| 系统依赖预检复验（2026-10-12） | ✅ 三场景：本机真实态（ffmpeg 在、libopus 可加载）静默通过；PATH 无 ffmpeg → 仅警告放行（rc 0）；libopus 加载失败 → `die` + 安装指引（rc 1）。真实 start/status/stop 回归通过 |
| 运行地址显示取 LAN IP 实测（2026-10-14，含复核轮补测） | ✅ `bash -n` 通过。函数级（source 后直调，含 resolve_ports）：正常态 `local_ip`=`<lan-host>` → `srv_display_url`=`https://<lan-host>:8180/healthz`；回退实测两类：venv python 不存在（`VENV=/nonexistent`）与存在但失败（假 python exit 1）均 → 空 → 显示回退 `https://127.0.0.1:8180/healthz`（离线/无默认路由走同一"取不到 → 空"异常路径，未构造实测）。真机 stop→start→status 全循环 rc 0：start 就绪行与 status 健康行均显示 LAN IP；lvdata-mcp 两处均保持 127.0.0.1；"未受本脚本管理"分支实测（临时移走 pid 文件、测后恢复）仍显示探测地址 `127.0.0.1`。展示地址实证：`curl -k https://<lan-host>:8180/healthz` → 200、监听 socket 为 wildcard `*:8180`（§3.5"显示前提 host: 0.0.0.0"）；url/display 变量使用点全量 grep 对位（探测恒 `srv_url`、展示恒 `srv_disp`/第 5 参，无串用） |

## 6. 验收步骤（2026-10-12 已实测通过，结果见 §5）

```bash
./livachat.sh install          # 断点续装：复用 .venv，纯运行依赖；自动生成 configs/test（若缺失）
./livachat.sh status           # 预期: 两服务"未运行"（端口以 configs/test 为准；被占用则显示"有响应"）
# 本机实测结论（2026-10-12）：configs/test 已重建为 8180/8190，与 dev 实例（8080/8090）
# 并存全程无冲突；若端口与运行中实例重合，删 configs/test 重跑 install 自动避让：
./livachat.sh start            # 预期: 两服务就绪 + 退出码 0
./livachat.sh status           # 预期: 运行中（健康）×2，退出码 0
./livachat.sh start            # 预期: "已在运行，跳过"（幂等）
./livachat.sh stop             # 预期: 两服务"已停止"（退出码 0）
./livachat.sh status           # 预期: 未运行，退出码 1
# 冲突预检：保持一键实例运行，从另一 checkout 跑 start → 预期拒绝并提示
```

## 7. 实施清单与 review 决议

实施：

- [x] `livachat.sh`（仓库根，可执行；4 子命令 + usage）
- [x] `.gitignore`：+`.run/`、+`/logs/`、+`/configs/test/`、+`/data/`
- [x] test 环境改造：`configs/test` 生成（§3.0）+ 脚本 `ensure_test_config`（install）/ start
      注入 `LIVA_CHAT_CONFIG`、`LVDATA_MCP_CONFIG` / 数据根 `<repo>/data` 自动创建
- [x] 端口自动避让（首次生成试绑定检查 +100 步进；bff 同步；§3.0）
- [x] start 环境预检（`check_required_env`：`.env` 不存在或必需项为空 → 逐项列出）
- [x] install 续跑 + start/stop/status 全路径实测（§5；与 dev 实例并存、端口避让 8180/8190）
- [x] 依赖缺口修复：`services/pyproject.toml` → `sqlalchemy[asyncio]`（greenlet；实测发现）
- [x] review 修订：reload 子进程哨兵（§3.3/§4）+ greenlet 注释按 METADATA 实测校正
- [x] review 修订（二轮）：哨兵改 `ps` 过滤（resource_tracker/僵尸）+ 壁钟宽限；stop 补 orphan 子进程清理（§3.3/§3.4）
- [x] 系统依赖预检：libopus（服务自身加载器实测，缺即 die）/ ffmpeg（缺仅 warn；§3.3）
- [x] 运行地址显示取本机 LAN IP（2026-10-14 用户指示；§3.5/§3.7）：`local_ip()`（UDP connect
      取默认路由源地址，空则回退）+ 显示替换（start 就绪行 / status 健康行）；探测保持 127.0.0.1
      ——实测见 §5（2026-10-14 行）
- [ ] 提交（等拍板）

review 决议（2026-10-12，已回填 §3）：

1. **依赖 = 纯运行**：不装 `[test,dev]`（pytest 系 / ruff / mypy / ipython）；lvdata-mcp 保留
   `[image,vec]`。开发工具按需补：`uv pip install -e "services[test,dev]"` 或 `make server-install`。
2. **uv 官方安装脚本（`curl -LsSf ... | sh`）**：接受。
3. **日志目录 = `<repo>/logs/`**（替换原 `~/temp` 提案；`.gitignore` +`/logs/`）。
4. **README 不动**。
5. **test 环境与数据根（用户指示 2026-10-12，已实施）**：`configs/test/` 从 `configs/dev/`
   复制生成并 gitignore；数据根 `<repo>/data/` gitignore、start 自动创建；脚本统一注入
   `LIVA_CHAT_CONFIG` / `LVDATA_MCP_CONFIG` 指向 test 配置。
6. **端口自动避让（用户指示 2026-10-12，已实施）**：首次生成 configs/test 时对 8080/8090
   做系统试绑定检查，被占则 +100 步进（最多 10 次）取空闲；`bff.lvdata_api_url` 同步；
   已存在的 configs/test 不追溯。
7. **start 环境预检（用户指示 2026-10-12，已实施）**：`.env` 不存在或必需项为空时
   start 逐项列出缺失/为空的变量名（必需 = `APP_AUTH_TOKEN` / `DASHSCOPE_API_KEY` /
   `DASHSCOPE_API_HOST` 非空；`DASHSCOPE_WORKSPACE_ID` 为空仅警告——§1.4 允许空串）。
8. **系统依赖检查（用户问询 2026-10-12，已实施）**：start 前置实测 libopus（用服务自身
   加载器，缺即 `die`）与 ffmpeg（缺仅 `warn`）——两者均为运行时依赖，缺失时服务
   "能启动但故障"（语音会话 / 音频分析），故在 start 显式暴露。
