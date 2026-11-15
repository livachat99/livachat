# LiveKit Server 安装与配置设计

> 状态：安装器已实施（2026-11-15），隔离测试通过；真实 LiveKit 服务、TLS 证书和反向代理不在安装器范围内。
> 目标命令：`./livachat.sh install --livekit`。
> 依据：当前 worktree（commit `b65a500`）代码事实；LiveKit `v1.13.11` 发布物、源码与官方文档（2026-11-15 查询）。
> 本文不包含任何真实 key/secret、域名、IP 或服务器地址，全部使用占位符。

---

## 1. 需求与范围

### 1.1 需求

1. 扩展 `livachat.sh install --livekit`，下载并安装 LiveKit Server 到 `repo_root/bin/livekit/`；LiveKit 官方 sample config 也放在同一目录。
2. 生成的 LiveKit 配置**不支持 Redis**（不做 full cluster mode）；生成新的 key/secret，并与仓库根 `.env` 保持一致。
3. 以现有 codebase 为准，不猜测；技术细节允许联网调研。
4. 本文保存分析与设计，review 通过后再实施。

### 1.2 本期范围

- 修改根目录 `livachat.sh` 的 `install` 子命令，增加 `--livekit` 及 LiveKit 相关选项；**不新增** `install.sh` 或其它独立安装脚本。
- Linux x86_64 / arm64 / armv7 的 LiveKit release 二进制安装。
- macOS 通过 Homebrew 安装（官方没有 macOS release 资产）。
- 生成单节点 `livekit.yaml`（无 Redis section）。
- 生成 key/secret，原子更新 `.env`，保证 `.env` 与配置文件一致。
- 安装目录、权限、gitignore、测试与运行验证说明。

### 1.3 非目标

- 不实现 `start/stop/status` 管理 LiveKit（本期只安装与配置；运行命令见 §9）。
- 不自动签发/配置 TLS 证书，不自动部署反向代理；只给出要求和示例。
- 不支持 Redis / 多节点 / cluster mode。
- 不支持 LiveKit Cloud、TURN、Egress、webhook 配置。
- 不支持 Windows 原生 shell（WSL 按 Linux 处理）。
- 不做 LiveKit 自动升级；Linux 通过 `--livekit-version <ver>` 指定新版本，macOS 通过更新 Homebrew 后删除本地二进制重装，或手动替换二进制。
- 不提供 `--rotate-keys` / `--reuse-env-keys` / `--force`；首次安装总是生成新 pair，之后不改 `.env`/配置；重置/重装通过删除 `livekit.yaml` 或二进制后重跑（见 §7.3、§8.5）。

---

## 2. Codebase 现状（事实）

### 2.1 安装入口现状

| 事实 | 位置 |
|------|------|
| 本仓库的一键安装入口是 `livachat.sh install`：安装 uv、Python 3.12、`.venv`、editable Python 包 | `livachat.sh` 的 `cmd_install` 与子命令分发 |
| `Makefile` 另有 `server-install`，只执行 pip editable 安装（不建 venv、不装 uv/Python） | `Makefile:94-95` |
| `livachat.sh` 同时负责 `start/stop/status` | `livachat.sh` 的 `usage()` |
| 仓库当前**没有**独立 `install.sh`；本次也不新增 | 根目录 |
| `scripts/` 下只有构建/代码生成脚本，没有依赖安装脚本 | `scripts/` |
| 仓库当前没有 `bin/` 目录 | 根目录 |

结论：本次**直接扩展 `livachat.sh` 的 `install` 子命令**：保留现有 Python 基础安装流程，在同一个函数中解析 `--livekit` 并执行 LiveKit 安装；不新增脚本入口，也不复制 Python 安装逻辑。

### 2.2 `.env` 与配置加载（决定写入方式）

`services/shared/config/loader.py`：

- `_load_dotenv()`（`:63-90`）在启动时读取仓库根 `.env`，用 `os.environ.setdefault` 注入：**shell/CI 已设置的值优先**，`.env` 只补缺。
- 解析规则：跳过空行与 `#` 注释；容忍 `export ` 前缀；按首个 `=` 拆分；去首尾空白；剥离成对引号；**同键后行覆盖前行**。
- `LIVACHAT__` 前缀环境变量在 YAML 加载后覆盖嵌套字段（`_apply_env_overrides`，`:116+`）。

因此安装器更新 `.env` 的 key/secret 时必须：

1. 删除这两个键的**所有**旧行，再在文件末尾追加唯一一对规范行（后行覆盖前行，避免歧义）。
2. 保留其它变量、注释与顺序。
3. 使用同目录临时文件 + 原子 `mv`。
4. 文件权限保持 `600`。

### 2.3 meeting 配置事实

| 事实 | 位置 |
|------|------|
| `MeetingConfig` 字段：`enabled`、`external_server_addr`、`livekit_key`、`livekit_secret`、`livekit_server`、`web_dir`、TTL/窗口/限流 | `services/shared/config/models.py:300-317` |
| `meeting.enabled=true` 要求 `bff.enabled=true`；key/secret 非空；`livekit_server` **必须** `wss://`（localhost/dev 也不例外）；`web_dir/index.html` 必须存在 | `services/shared/config/validation.py:102-163` |
| dev 配置 `meeting.enabled=false`；`external_server_addr`/`livekit_key`/`livekit_secret`/`livekit_server` 为空串，由 `.env` 的 `LIVACHAT__MEETING__*` 提供；`web_dir` 指向 `apps/web/meeting/dist` | `configs/dev/server.yaml:299-309` |
| BFF 只用 key/secret 签发 JWT，并把 `cfg.meeting.livekit_server` 原样返回给 Web | `services/bff/meeting/service.py:61,104` |
| 代码中没有 LiveKit webhook、RoomService、Egress 调用；LivaChat 的 Redis 已去接线（`RedisConfig` 仅保留 deprecated 字段） | `services/`、`tools/lvdata-mcp/` 搜索；`services/shared/config/models.py:132-133`、`docs/chatserver_no_redis.md` |
| `.env.example` 已有 meeting 变量模板 | `.env.example:66-72` |
| `.gitignore` 当前没有忽略 `/bin/` | `.gitignore` |

结论：安装器只负责 **二进制 + 单节点配置 + key/secret**；`LIVACHAT__MEETING__LIVEKIT_SERVER`（WSS URL）由用户手工编辑 `.env`，安装器不读取、不修改、不校验。不需要生成 webhook/Redis/TURN 配置，也不需要改 Python 配置模型或 `configs/dev/server.yaml`。

### 2.4 现有设计文档的约束与冲突

- `docs/livachat_meeting.md:1230`：浏览器 `getUserMedia` 要求安全上下文；`livekit_server` 必须始终是 `wss://`，dev 也不例外；不要用 `livekit-server --dev` 的 `ws://127.0.0.1:7880`。
- `docs/personal_meeting_design.md` §3.2 列出了端口/TLS 要求，但其"单节点也可配 Redis"的建议与本次"**不使用 Redis**"的要求冲突；本文以本次要求为准，**单节点、无 Redis**。
- 该文档提到的 webhook/TURN 属于早期设计，当前代码没有实现，本期不写入配置。

---

## 3. LiveKit 调研结论（2026-11-15）

### 3.1 版本与发布物

最新稳定版：`v1.13.11`（2026-10-25 发布），与 `docs/personal_meeting_design.md` §14.3 固定的版本一致。

Release 资产：

| 资产 | 大小（bytes） | SHA-256 |
|------|---------------|---------|
| `livekit_1.13.11_linux_amd64.tar.gz` | 19,464,037 | `1e9ee90ca301e1842b2887fc77cb122759756a9c8aeb4334ee91de3325ec43fb` |
| `livekit_1.13.11_linux_arm64.tar.gz` | 17,559,890 | `2af6ce091d6b3c79d1677f8ff1bbb91a8007f40b2325c059b52d38383c0830b6` |
| `livekit_1.13.11_linux_armv7.tar.gz` | 18,101,628 | `5f663a6498c3d81ef2dce55a3a1e7815aacfe78207e469abc0bd9c5b5ca5070b` |

另有 Windows zip 资产，但本设计不支持 Windows 原生 shell（WSL 按 Linux 处理）。

关键事实：

- **没有 macOS/darwin release 资产**。官方 macOS 安装方式是 Homebrew：`brew install livekit`。
- Linux tarball 内容只有 `LICENSE` 和 `livekit-server` 两个文件；sample config 不在 tarball 内。
- 官方 Linux 安装脚本 `curl -sSL https://get.livekit.io | bash` 会下载对应平台 tarball 并安装到 `/usr/local/bin`；本设计不直接 pipe 官方脚本，而是自己下载、校验、安装到仓库内。
- Homebrew formula：`livekit`，stable `1.13.11`，bottled，license Apache-2.0；安装的二进制名为 `livekit-server`。
- `livekit-server --version` 输出形如 `livekit-server version 1.13.11`。
- `livekit-server generate-keys` 输出（源码 `cmd/server/commands.go` `generateKeys`）：

  ```text
  API Key:  APIxxxxxxxxxxxx
  API Secret:  <base62-random>
  ```

  实现：key 为 `guid.New(utils.APIKeyPrefix)`（前缀 `API`）；secret 为 32 字节随机数的 base62（`utils/secret.go` `RandomSecret`）。两者都是 base62/字母数字字符，适合安全写入 YAML 与 `.env`（生成模板仍加双引号）。
- 健康检查：`GET /`，正常返回 `200 OK`（body `OK`）；节点统计超过 4 秒未更新返回 `406 Not Ready`（`pkg/service/server.go` `healthCheck`）。

### 3.2 Sample config 事实

官方 sample：

```text
https://raw.githubusercontent.com/livekit/livekit/v1.13.11/config-sample.yaml
```

约 457 行，关键内容：

- `port: 7880`。
- `redis:` 是**未注释的完整配置块**（`address: redis.host:6379`，含 sentinel/cluster/TLS 注释）。因此**不能**把 sample 直接复制成运行配置。
- `rtc.port_range_start: 50000`、`port_range_end: 60000`、`tcp_port: 7881`、`use_external_ip: true`。
- `keys:` 使用 `key1: secret1` / `key2: secret2` 占位。
- `webhook`、`turn`、`room` 等块均为注释状态。

### 3.3 端口与 TLS 事实

| 端口 | 协议 | 用途 | 部署要求 |
|------|------|------|----------|
| 7880 | TCP/HTTP+WS | RoomService / RTC signaling / WebSocket | 生产必须放在 TLS 负载均衡或反向代理后；LiveKit 自身不在该端口终止 TLS |
| 7881 | TCP | ICE/TCP fallback | **不能**放在 L7 负载均衡/TLS 代理后，必须直接暴露在节点上 |
| 50000-60000 | UDP | WebRTC media | 必须对客户端开放；可配置 |
| 5349 | TCP | 内置 TURN/TLS（可选） | 本期不启用 |

结论：`wss://` 由外部 TLS 终止层提供；安装器**不能**生成 `ws://` 或假装提供 WSS。`livekit_server` 的值必须是用户提供的 `wss://host[:port]`。

---

## 4. 方案总览

### 4.1 目录布局

```text
<repo_root>/
├── livachat.sh                    # 修改：install 子命令增加 --livekit 及 LiveKit 选项
├── bin/
│   └── livekit/
│       ├── livekit-server         # 可执行文件（755）
│       ├── livekit.yaml           # 生成的单节点运行配置（600，含 secret）
│       ├── config-sample.yaml     # 官方 sample 原样保存（644，仅参考）
│       ├── LICENSE                # Apache-2.0（644）
│       ├── VERSION                # 实际安装版本，一行（644）
│       └── checksums.txt          # 官方 release checksums（644，Linux 安装时保存）
├── .env                           # 更新 meeting key/secret（600）
└── docs/livekit_install.md        # 本文
```

`.gitignore` 新增：

```gitignore
# Local binaries and secrets (LiveKit install)
/bin/
# .env atomic-update leftovers (normally removed by trap; ignore in case of a crash)
/.env.tmp.*
# Timestamped .env backups contain secrets and must never be committed
/.env.bak.*
```

### 4.2 安装流程

1. `./livachat.sh install` 解析参数；无 `--livekit` 时保持现有行为不变。
2. 执行现有 Python 基础安装（除非 `--skip-base`）。
3. `--livekit` 时安装/检查 LiveKit 二进制：
   - Linux：下载所选版本的 release tarball → 校验 SHA-256 → 解压到临时目录 → 原子安装。
   - macOS：`brew install livekit` → 复制 `livekit-server`。
4. 判定 `bin/livekit/livekit.yaml` 是否存在：
   - **不存在（首次安装）**：下载官方 `config-sample.yaml`（和 `LICENSE`）；生成新 key pair；更新 `.env` 中的 key/secret；新建 `livekit.yaml`。`LIVACHAT__MEETING__LIVEKIT_SERVER` 不由安装器修改，需用户手工编辑。
   - **存在（已安装）**：不生成 key，不修改 `.env` 与 `livekit.yaml`；只做只读一致性检查并 warn。
5. 打印运行与验证命令、WSS/防火墙注意事项。

---

## 5. `livachat.sh install` CLI 设计

### 5.1 命令形态

```text
Usage: ./livachat.sh <command> [options]

Commands:
  install    Install Python dependencies; with --livekit, also install LiveKit Server
  start      Start both services in background and wait for health checks
  stop       Gracefully stop both services
  status     Show process and /healthz status

install options:
  --livekit                Install LiveKit Server into <repo>/bin/livekit
  --skip-base              With --livekit: skip the Python base install
  --livekit-version <ver>  LiveKit version to install (default: 1.13.11)
  --dry-run                Print planned actions; no network access, no writes
  -h, --help               Show help
```

`livachat.sh` 仍是唯一入口：`install` 保留现有 Python 安装流程，`--livekit` 追加 LiveKit 组件。实现时：

- 顶层 `case` 分发改为 `install) shift; cmd_install "$@" ;;`，把选项传入 `cmd_install`。
- `cmd_install` 先解析选项，再执行原有安装步骤；未知选项 `die`。
- 无 `--livekit` 时行为与当前完全一致（不加参数、不写 LiveKit 文件）。
- 首次安装（`bin/livekit/livekit.yaml` 不存在）：生成新 pair，更新 `.env`，新建配置。
- 已安装（`livekit.yaml` 存在）：不生成 key，不修改 `.env`/配置；只检查/安装二进制。
- 所有 LiveKit 函数集中在 `livachat.sh` 的一个清晰段落，避免打散现有 `start/stop/status` 逻辑；不新增独立脚本。
- `--skip-base` 必须与 `--livekit` 同时使用，否则 `die`（避免出现"什么都不装但返回成功"）。
- `--dry-run` 必须在 `ensure_uv`/`.venv` 操作之前短路，只打印计划，不访问网络、不写文件。

### 5.2 行为矩阵

| 命令 | 行为 |
|------|------|
| `./livachat.sh install` | 仅执行现有 Python 基础安装（与现状等价） |
| `./livachat.sh install --livekit`（首次，无 `livekit.yaml`） | 基础安装 + LiveKit 二进制；生成新 key pair，写 `.env` 的 key/secret 和新建 `livekit.yaml`；不修改 `LIVEKIT_SERVER` |
| `./livachat.sh install --livekit`（已有 `livekit.yaml`） | 只检查/安装二进制；**不生成 key，不修改 `.env` 和 `livekit.yaml`** |
| `./livachat.sh install --livekit --skip-base` | 仅 LiveKit 部分（首次/已有配置规则同上） |
| `--dry-run` | 打印将执行的步骤、目标路径、首次安装判定；不下载、不写文件 |
| 同版本重装 | 删除 `bin/livekit/livekit-server`（或 `VERSION`）后重跑 |

### 5.3 脚本约定

- 沿用现有 `#!/usr/bin/env bash`、`set -euo pipefail`、`ROOT`、`info/warn/die`、`VENV` 等定义。
- 新增函数统一使用 `livekit_` 前缀（如 `livekit_install`、`livekit_generate_keypair`、`livekit_first_install`），集中在 `cmd_install` 之后、`cmd_start` 之前，便于审查与回退。
- 所有失败返回非 0；现有 start/stop/status 路径不得因新增参数解析而改变。
- 不使用 `sudo`；所有写入都在 repo 内或用户目录。
- 不启用 `set -x`；**任何路径都不打印 secret**。
- 临时目录用 `mktemp -d`，`trap` 清理；secret 只存在于 shell 变量和最终文件中，不作为外部命令参数传递（避免 `ps` 泄漏）。

---

## 6. 平台安装设计

### 6.1 Linux

支持架构映射：

| `uname -m` | Release arch |
|------------|--------------|
| `x86_64` / `amd64` | `amd64` |
| `aarch64` / `arm64` | `arm64` |
| `armv7l` / `armv7` | `armv7` |

其它架构（i686、ppc64le、s390x、riscv64 等）→ `die`，提示手动安装或使用其它平台。

下载 URL：

```text
https://github.com/livekit/livekit/releases/download/v<VER>/livekit_<VER>_linux_<ARCH>.tar.gz
https://github.com/livekit/livekit/releases/download/v<VER>/checksums.txt
https://raw.githubusercontent.com/livekit/livekit/v<VER>/config-sample.yaml
https://raw.githubusercontent.com/livekit/livekit/v<VER>/LICENSE
```

步骤：

1. 若 `bin/livekit/livekit-server` 存在且 `VERSION` 与目标版本一致：运行一次 `livekit-server --version` 校验；通过则跳过下载，失败（损坏/架构不符）则重新下载。`VERSION` 缺失/不一致或二进制缺失时也重新下载。需要重装同版本时删除二进制或 `VERSION` 后重跑。无论是否跳过，仍执行 key/config 一致性检查。
2. `curl -fL --retry 3 --connect-timeout 10` 下载 tarball 和 `checksums.txt` 到临时目录。`curl` 自动遵循 `HTTPS_PROXY`/`ALL_PROXY`。
3. 从 `checksums.txt` 精确匹配 `livekit_<VER>_linux_<ARCH>.tar.gz` 的 SHA-256；缺失或不匹配 → `die`（fail closed）。
4. `tar -xzf` 只解出 `livekit-server` 与 `LICENSE` 到临时目录（解压到临时目录后再安装，避免直接解压到目标目录）。
5. 校验解压出的二进制：
   - 可执行；
   - `"$tmp/livekit-server" --version` 包含目标版本；否则 `die`。
6. `install -m 755` 原子替换 `bin/livekit/livekit-server`（先写 `.new` 再 `mv`）。
7. 安装 `LICENSE`（644）、`checksums.txt`（644）。
8. 写入 `VERSION`（目标版本）。
9. 首次安装还下载 `config-sample.yaml`（校验非空且包含 `port: 7880`；失败/回退规则同 §6.2 第 8 条）；已存在的 sample 保留，需要更新时删除后重跑。

### 6.2 macOS

官方没有 darwin release 资产，使用 Homebrew。

若本地 `bin/livekit/livekit-server` 已存在且 `livekit-server --version` 可执行，跳过下面 1-5 步，直接执行 6-9；需要重装同版本时删除本地二进制后重跑。macOS 升级需先 `brew upgrade livekit`（或安装目标版本），再删除本地二进制后重跑；`--livekit-version` 不会让 Homebrew 安装旧版本。

1. 定位 brew：先 `command -v brew`；失败时尝试 `/opt/homebrew/bin/brew`（Apple Silicon）和 `/usr/local/bin/brew`（Intel），找到后加入 PATH；仍不存在 → `die`，提示安装 Homebrew，或通过 `LIVEKIT_SERVER_BIN` 指定已有二进制。
2. 未安装 livekit → `HOMEBREW_NO_AUTO_UPDATE=1 brew install livekit`（不自动升级已安装的 brew 包）。
3. 已安装 → 直接使用。
4. `src="$(brew --prefix livekit)/bin/livekit-server"`；检查可执行。
5. 复制/安装到 `bin/livekit/livekit-server`（755），而不是软链接：让 `bin/livekit/` 自包含，brew 升级/卸载不会让本地目录失效。先写 `livekit-server.new` 再 `mv`，避免覆盖正在运行的二进制。
6. 读取实际版本：
   - 若与 `--livekit-version` 不一致（brew 通常跟随最新 stable）：**warn** 并继续，`VERSION` 记录实际版本。
   - 原因：Homebrew 不提供简单的旧版本安装路径；阻塞本地开发没有收益。Linux 仍保持严格版本校验。
7. `LICENSE`（仅首次安装处理）：若 brew 可用且 `$(brew --prefix livekit)/LICENSE` 存在则复制；否则从 `v<VER>` tag 的 raw URL 下载；仍失败则 warn（不阻塞）。已安装状态缺失时只 warn。
8. `config-sample.yaml`（仅首次安装处理）：本地已有 sample 时保留（需要更新时删除后重跑）；否则从 `v<VER>` tag 下载（`<VER>` 为实际安装版本）；若该 tag 不存在（例如 brew HEAD/dev build），回退到默认 `v1.13.11` 的 sample 并 warn；仍失败则 `die`（用户明确要求该文件存在）。已安装状态不下载/不修改 sample；缺失时 warn（§7.3）。
9. 不保存 `checksums.txt`（二进制来源是 brew，不是 release 资产，保存会误导）。

### 6.3 不支持的平台

`uname -s` 不是 `Linux`/`Darwin`：

```text
Error: unsupported platform: <uname -s>; use Linux/macOS (WSL is treated as Linux),
or set LIVEKIT_SERVER_BIN=/path/to/livekit-server.
```

Windows 原生 Git Bash 不保证可用；Windows 使用 WSL 或手动解压官方 zip。

### 6.4 离线 / 测试覆盖

为便于测试和离线环境，安装器识别：

| 变量 | 作用 |
|------|------|
| `LIVEKIT_SERVER_BIN=/path/to/livekit-server` | 跳过平台安装，直接复制该二进制；之后按首次安装/已安装规则处理（仅首次生成 key/配置） |
| `LIVEKIT_RELEASE_BASE=<url>` | 覆盖 release 下载基址（默认 `https://github.com/livekit/livekit/releases/download`） |
| `LIVEKIT_RAW_BASE=<url>` | 覆盖 raw 文件基址（默认 `https://raw.githubusercontent.com/livekit/livekit`） |

这些变量只用于安装器，不写入 `.env`，不影响 LivaChat 运行配置。

### 6.5 版本参数校验

- 接受 `1.13.11` 或 `v1.13.11`，内部去掉前导 `v`。
- 只接受 `^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+)*$`，拒绝 `/`、空格、`..`，防止 URL/路径注入。
- 默认 `1.13.11`。

---

## 7. LiveKit 配置生成

### 7.1 为什么不直接使用官方 sample

官方 sample 是**完整参考文档**，不是可直接运行的最小配置：

- 含未注释的 `redis:` 块（本次明确不支持）。
- `keys` 是 `key1/secret1` 占位。
- 含大量注释与可选块，容易让人误以为需要全部启用。

因此：

- `config-sample.yaml`：原样下载保存，供查阅；下载后校验非空且包含 `port: 7880`，否则按下载失败处理（防止把错误页保存成 YAML）。
- `livekit.yaml`：安装器生成的最小单节点配置，不含 Redis/webhook/TURN。

### 7.2 生成内容

```yaml
# Generated by ./livachat.sh install --livekit.
# Single-node only: Redis / cluster mode is intentionally not supported.
# Keep the key pair in sync with repo-root .env:
#   LIVACHAT__MEETING__LIVEKIT_KEY / LIVACHAT__MEETING__LIVEKIT_SECRET
port: 7880

rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  # Official sample default. ICE candidates are probed and selected by the
  # client; no per-deployment switch is provided here.
  use_external_ip: true

room:
  empty_timeout: 300
  departure_timeout: 300
  max_participants: 100

keys:
  "<LIVEKIT_API_KEY>": "<LIVEKIT_API_SECRET>"

logging:
  level: info
```

说明：

| 配置 | 选择理由 |
|------|----------|
| `port: 7880` | LiveKit 默认信令/HTTP 端口；TLS 由反向代理终止 |
| `rtc.tcp_port: 7881` | UDP 不可达时的 ICE/TCP fallback；不能走 L7 代理 |
| UDP 50000-60000 | 官方 sample 默认；需在防火墙放行 |
| `use_external_ip: true` | 官方 sample 默认；LiveKit 公布 ICE candidates，客户端探测并选择可达路径；STUN 结果异常时手工设置 `rtc.node_ip`，安装器不提供切换 flag |
| `room.empty_timeout/departure_timeout` | 房间创建后无人加入 / 最后一人离开后各保留 5 分钟，避免短暂断线立即销毁 |
| `room.max_participants: 100` | 单房间人数上限；按产品决策设为 100，可按需调整 |
| `logging.level: info` | 默认非 debug，减少日志量与敏感信息暴露 |
| 无 Redis | 单节点运行不需要；本次明确不支持 cluster mode |
| 无 webhook | 当前 LivaChat 代码没有 webhook endpoint，写了也不会被使用 |
| 无 TURN | 本期不启用；需要时另行设计证书与端口 |

`use_external_ip` 的取舍：

- 默认 `true`，与官方 sample 对齐；LiveKit 公布 ICE candidates，客户端负责探测并选择可达路径，不提供本地/云主机切换开关。
- 如果 STUN 探测结果与实际网络不符（例如 NAT/多网卡环境），再手工设置 `rtc.node_ip`；这属于特殊网络排障，不做成安装器参数。

### 7.3 文件所有权与首次安装规则

- **key/config 首次安装的唯一判定**：`bin/livekit/livekit.yaml` 是否存在。
  - **不存在 = 首次安装**：生成新 key pair，更新 `.env`，新建 `livekit.yaml`。
  - **存在 = 已安装**：不生成 key，不修改 `.env`，不修改 `livekit.yaml`；只检查/安装二进制。
- 首次安装会覆盖 `.env` 中已有 key/secret（先备份 `.env`）；`LIVACHAT__MEETING__LIVEKIT_SERVER` **不由安装器读取或修改**，安装后需用户手工编辑 `.env` 填写/确认本地 WSS URL。
- 已有 `livekit.yaml` 时做只读一致性检查：`.env` 的 key/secret 缺失或与 YAML 不一致 → warn，不修改 `.env`/`livekit.yaml`；shell 覆盖与 YAML 不一致也 warn。
- 重置：删除 `bin/livekit/livekit.yaml` 后重跑，下一次按首次安装重新生成 key 和配置。
- 配置/密钥只在首次安装生成；不提供 key 旋转/复用或强制重装参数。重置/重装分别删除 `livekit.yaml` / `livekit-server`（或 `VERSION`）后重跑；不创建 `livekit.yaml` 备份。
- 升级二进制不会改写已有 `livekit.yaml`；升级前需确认新版本兼容现有配置，必要时手工更新。

### 7.4 配置校验

安装完成后执行：

```bash
bin/livekit/livekit-server --config bin/livekit/livekit.yaml ports
```

预期输出包含 `7880 - HTTP service`、`7881 - ICE/TCP`、`50000-60000 - ICE/UDP range`。

额外静态检查（测试中执行）：

```bash
grep -Eq '^[[:space:]]*redis:' bin/livekit/livekit.yaml && exit 1 || true
```

注意：只检查**激活的 `redis:` 键**；注释中出现 "Redis" 是允许的。

---

## 8. Key/secret 与 `.env` 一致性设计

### 8.1 生成方式

使用已安装的二进制：

```bash
"$BIN_DIR/livekit-server" generate-keys
```

解析规则（兼容输出中的双空格）：

```bash
key="$(printf '%s\n' "$out" | sed -n 's/^API Key:[[:space:]]*//p' | head -n1)"
secret="$(printf '%s\n' "$out" | sed -n 's/^API Secret:[[:space:]]*//p' | head -n1)"
```

校验：

- 两者非空；
- 只含 `[A-Za-z0-9._-]`（LiveKit 生成的是字母数字；限制字符集是为了安全写入 YAML 和 `.env`）；
- key != secret；
- 失败 → `die`，不修改任何文件。

`generate-keys` 的 stdout 只进入变量，不直接打印；错误信息中也不包含 secret。

### 8.2 首次安装与已安装状态

判定：`bin/livekit/livekit.yaml` 不存在 = 首次安装；存在 = 已安装。

首次安装（无 `livekit.yaml`）：

1. 前置检查（在任何写入之前）：shell 若设置了 `LIVACHAT__MEETING__LIVEKIT_KEY` 或 `..._SECRET`（含空值）→ `die`，要求先 unset；否则新 pair 会被 shell 覆盖，`.env` 写入无效。
2. 用 §8.1 生成并校验新 pair。
3. 先备份 `.env`，再删除其中所有旧 key/secret 行并追加新 pair。**不修改 `LIVACHAT__MEETING__LIVEKIT_SERVER`。**
4. 用新 pair 新建 `livekit.yaml`（目标不存在）。
5. 打印提示：`LIVACHAT__MEETING__LIVEKIT_SERVER` 未由安装器修改；用户需手工编辑 `.env`，填写/确认指向本地 LiveKit 的 `wss://` URL。若 `.env` 原有 key/secret（完整或部分），同时提示 key 已更换、旧备份位置。

已安装（有 `livekit.yaml`）：

- 不生成 key，不修改 `.env`，不修改 `livekit.yaml`。
- 只读检查：`.env` 的 key/secret 缺失、无法解析、或与 YAML 不一致 → warn；shell 覆盖与 YAML/`.env` 不一致 → warn。**不修改 `.env`、`livekit.yaml` 或 key/secret。**
- 不修改 `LIVACHAT__MEETING__LIVEKIT_SERVER`；需要更新 URL 时手工编辑 `.env`。
- 仍可安装/刷新二进制（二进制缺失、损坏或版本不同时）。

重置：删除 `livekit.yaml` 后重跑 = 首次安装，重新生成 pair 和配置；`.env` 备份保留旧值。

`livekit.yaml` 的读取（仅已安装状态的只读检查）只接受安装器生成的形式：`keys:` 下一行、缩进的两个带引号字符串。解析失败只 warn，不修改文件。

### 8.3 `.env` 更新算法

以下仅在**首次安装**（无 `livekit.yaml`）时执行；已安装状态不调用本流程，不修改 `.env`。

1. `.env` 不存在：
   - 若 `.env.example` 存在 → `cp .env.example .env`，`chmod 600`，并提示仍需要填写必填变量。
   - 否则创建只含 meeting 注释与 key/secret 的新文件。
2. 备份：`umask 077; cp .env .env.bak.<UTC 时间戳>`（不保留原文件的宽权限），再 `chmod 600`；仅在实际修改前创建。时间戳使用 `YYYYMMDDTHHMMSSZ`（不含冒号，避免路径转义问题）。
3. 生成临时文件（与 `.env` 同目录，保证 `mv` 原子）：
   - 逐行复制 `.env`，删除键为 `LIVACHAT__MEETING__LIVEKIT_KEY` 或 `..._SECRET` 的行（容忍 `export ` 前缀、前后空白和 CRLF 行尾）；
   - 在末尾追加：

     ```bash
     # Meeting / LiveKit key pair (generated by livachat.sh install --livekit)
     LIVACHAT__MEETING__LIVEKIT_KEY=<key>
     LIVACHAT__MEETING__LIVEKIT_SECRET=<secret>
     ```

   - 确保文件以换行结尾。
4. `chmod 600` 临时文件；`mv` 覆盖 `.env`。
5. 安装器不处理 `LIVACHAT__MEETING__LIVEKIT_SERVER`；该行原样保留，由用户手工编辑。

实现注意：

- 若 `.env` 是符号链接，先解析到目标文件并在目标文件同目录做原子更新（或 `die` 提示手工处理）；不要用 `mv` 静默替换链接本身。
- 创建任何含 secret 的文件前设置 `umask 077`；`.env`/配置临时文件用 `mktemp`（默认 600）并显式 `chmod 600`；在 `EXIT/INT/TERM` trap 中删除临时文件，避免崩溃残留。配置临时文件放在 `bin/livekit/`（已被 gitignore）或其它已忽略目录，不要留在仓库根未忽略位置。
- secret 通过 shell builtin `printf`/重定向写入，不作为外部命令参数（避免 `ps` 可见）。
- 不在日志中打印 key 或 secret；只打印文件路径和"generated"状态。
- 备份文件同样包含 secret，权限必须 600；在输出中提示用户可自行删除旧备份。
- `.env` 更新与 `livekit.yaml` 写入按以下顺序执行（配置只在目标文件不存在时生成，不创建 `livekit.yaml` 备份）：
  1. 生成 `livekit.yaml` 内容到临时文件（不落目标）；
  2. 原子更新 `.env`（有备份）；
  3. `mv` 前再次确认目标 `bin/livekit/livekit.yaml` 仍不存在；不存在才 `mv`（避免并发运行覆盖人工配置）；
  4. 若目标已存在或 `mv` 失败，用 `.env` 备份恢复 `.env`、删除临时配置并 `die`；若 `.env` 原本不存在（步骤 1 才从 `.env.example` 创建），回滚时直接删除 `.env`，不要留下模板副本。
  因为目标 `livekit.yaml` 原本不存在，不存在需要恢复的旧配置；已存在的配置永远不会被本流程改写。

### 8.4 `LIVACHAT__MEETING__LIVEKIT_SERVER`（手工维护）

- 安装器**不读取、不修改、不校验**该变量，也不提供 `--livekit-server-url` 之类的参数；用户手工编辑仓库根 `.env`。
- 手工填写要求：`wss://host[:port]`（服务端 `services/shared/config/validation.py:137-152` 会 fail-fast；`ws://` 一律拒绝，localhost/dev 也不例外）。建议不带 path/query/fragment；尾部 `/` 可手工去掉。
- 不从 `LIVACHAT__MEETING__EXTERNAL_SERVER_ADDR` 推导 LiveKit 地址：两者可以是不同 host/port。
- 安装器**不修改** `LIVACHAT__MEETING__ENABLED` 与 `LIVACHAT__MEETING__EXTERNAL_SERVER_ADDR`：是否启用 meeting、`/meeting/` 的对外基址仍由部署方决定；启用前仍需构建 `apps/web/meeting/dist`。
- 安装器只更新仓库根 `.env` 的 key/secret，并且只能感知当前 shell；如果 LivaChat 由 systemd/容器/其它 env 文件启动，需确保其 `LIVACHAT__MEETING__LIVEKIT_KEY/SECRET/SERVER` 与本次配置一致（进程环境优先于 `.env`）。若部署在 YAML 中硬编码了 meeting 值，也需自行同步或迁移到 `.env`。

### 8.5 重新生成 key 后的运行影响

- 首次安装生成/覆盖 key 后，LivaChat 必须在启动时重新读取 `.env` 才能使用新 pair；安装器不自动重启服务。
- 按 §7.3 删除 `bin/livekit/livekit.yaml` 后重跑（视为首次安装）会生成新 pair 并更新 `.env`：
  - 已签发的旧 token 在 TTL 内仍可能被**尚未重启的** LiveKit 接受；重启后失效。
  - 必须重启 LiveKit（重新加载 `livekit.yaml`）与 LivaChat server（重新加载 `.env`）。
  - 已建立的会议连接不会因 key 更换立即断开；新入会使用新 key。
- 安装器不自动重启服务，只打印命令：

  ```bash
  ./livachat.sh stop && ./livachat.sh start
  # LiveKit 进程按其启动方式重启，例如：
  bin/livekit/livekit-server --config bin/livekit/livekit.yaml
  ```

---

## 9. WSS/TLS、端口与运行

### 9.1 为什么必须有 TLS 终止层

- `livekit_server` 校验只接受 `wss://`。
- LiveKit 的 7880 端口是明文 HTTP/WS，不在该端口提供 TLS。
- 浏览器从 HTTPS 页面连 `ws://` 会被混合内容策略拦截。
- 因此：**必须有反向代理/负载均衡在 7880 前终止 TLS**，并提供受客户端信任的证书。

### 9.2 反向代理要求

- 必须支持 WebSocket upgrade 和长连接（`/rtc` 是长连接 WebSocket）。
- 必须转发所有路径到 `http://127.0.0.1:7880`（至少 `/`、`/rtc`；不要只转发 `/rtc` 而漏掉健康检查）。
- 证书必须被浏览器/移动设备信任；自签证书需要设备安装并信任 CA。
- 代理只处理 7880/TCP；**不要**把 7881/TCP 或 UDP 50000-60000 放进 L7 代理。

示例（nginx）：

```nginx
server {
    listen 443 ssl;
    server_name <livekit-host>;

    ssl_certificate     <certs-dir>/cert.pem;
    ssl_certificate_key <certs-dir>/key.pem;

    location / {
        proxy_pass http://127.0.0.1:7880;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

对应的 `.env`：

```bash
LIVACHAT__MEETING__LIVEKIT_SERVER=wss://<livekit-host>
# 非 443 端口时：
# LIVACHAT__MEETING__LIVEKIT_SERVER=wss://<livekit-host>:<port>
```

### 9.3 本地开发

- 推荐复用本机已有的 mkcert 证书与 TLS 代理（证书需包含实际访问用的 host，例如 `localhost` 或局域网 IP；证书和 key 不入库）。
- 反向代理监听一个本地端口，转发到 `127.0.0.1:7880`；`.env` 写 `wss://localhost:<proxy-port>` 或 `wss://<lan-host>:<proxy-port>`。
- 不要使用 `livekit-server --dev` 的 `ws://127.0.0.1:7880`：Web app 校验和服务端校验都会拒绝，真机也不可用。
- 局域网设备访问时，证书 SAN 必须包含设备使用的 host/IP；否则浏览器会拒绝 WSS。

### 9.4 端口与防火墙

| 端口 | 方向 | 说明 |
|------|------|------|
| 7880/TCP | 客户端 → TLS 代理 | 代理再转发到 LiveKit；不要直接暴露明文 |
| 7881/TCP | 客户端 → LiveKit 主机 | ICE/TCP fallback；必须直达 |
| 50000-60000/UDP | 客户端 ↔ LiveKit 主机 | WebRTC 媒体；必须放行 |
| 5349/TCP | — | 本期不启用 TURN |

默认 `rtc.use_external_ip=true`（官方 sample）；LiveKit 公布 ICE candidates，客户端探测并选择可达路径。若 STUN/公网 IP 探测与实际网络不符，手工设置 `rtc.node_ip`；安装器不提供本地开关。

### 9.5 运行与验证

```bash
# 版本
bin/livekit/livekit-server --version

# 端口解析（不启动服务）
bin/livekit/livekit-server --config bin/livekit/livekit.yaml ports

# 启动（前台）
bin/livekit/livekit-server --config bin/livekit/livekit.yaml

# 本机健康检查（明文 7880；刚启动时 stats 尚未更新会返回 406，等待几秒重试）
curl -fsS http://127.0.0.1:7880/

# 经 TLS 代理的健康检查（应为 200 / OK；非 443 端口时加 :<port>）
curl -fsS https://<livekit-host>/
```

验证 WSS 时，最终以浏览器/App 入会为准；`curl` 只能验证 TLS 与健康端点，不能完整验证 WebRTC 媒体路径。

---

## 10. 安全与隐私

1. `bin/livekit/livekit.yaml` 含 API secret，必须 `600`；`bin/livekit/` 加入 `.gitignore`。
2. `.env` 与备份 `600`；备份中包含明文 secret，提示用户确认新配置后可自行删除。
3. 安装器不打印 key/secret；`generate-keys` 的输出只进入变量。
4. 下载校验：
   - Linux 使用官方 `checksums.txt` 的 SHA-256，缺失即失败。
   - 注意：checksums 与资产来自同一发布页，能防传输损坏/错资产，但不是签名校验；更高安全等级需要固定 checksum 或 GPG 签名（本期不做）。
5. 使用 `install` 原子替换二进制，避免半写状态。
6. 不引入 `sudo`；不修改 `/usr/local/bin`、系统服务或全局配置。应以运行 LivaChat 服务的同一用户执行，避免 `bin/livekit/` 与 `.env` 变成其它用户不可读。
7. GitHub 下载遵循标准 `HTTPS_PROXY`/`ALL_PROXY`；安装器不硬编码代理。当前开发环境访问 GitHub 需先设置本地代理：

   ```bash
   HTTPS_PROXY=http://127.0.0.1:8118 ./livachat.sh install --livekit
   ```

   该代理只用于 GitHub 相关域名，不影响其它网络请求。
8. 文档、日志、提交中不得出现真实域名/IP/key/secret；本文与脚本示例只使用占位符。

---

## 11. 测试计划

### 11.1 静态检查

```bash
bash -n livachat.sh
command -v shellcheck >/dev/null && shellcheck livachat.sh
bash tests/install/livekit_install_test.sh
```

`shellcheck` 可能对现有脚本已有警告；要求新增代码不引入新 warning（与修改前输出对比）。`tests/install/livekit_install_test.sh` 使用 fake `livekit-server` 和本地 `file://` 资产，不访问网络、不修改真实 `.env`。

### 11.2 隔离测试（不访问真实 GitHub）

使用 `LIVEKIT_SERVER_BIN` 指向 fake 脚本，fake 支持：

- `--version` → 输出 `livekit-server version <fake-version>`；
- `generate-keys` → 输出带双空格的 `API Key:` / `API Secret:` 固定值。

在临时目录复制 `livachat.sh`，构造假的 `.env`/`bin`，并把 `LIVEKIT_RELEASE_BASE`/`LIVEKIT_RAW_BASE` 指向本地目录（或本地 HTTP 服务），用 `install --livekit --skip-base` 执行。`--skip-base` 必须在基础安装前短路，不依赖 uv、`.venv` 或 `configs/`。核心用例已实现为 `tests/install/livekit_install_test.sh`（含 Darwin 上的 fake Homebrew copy path）；以下矩阵用于补充人工/平台验证。测试矩阵：

| # | 场景 | 预期 |
|---|------|------|
| 1 | 首次 `--livekit --skip-base`（无 YAML、无 `.env` pair、`LIVEKIT_SERVER` 为空） | 生成 `bin/livekit/{livekit-server,livekit.yaml,config-sample.yaml,LICENSE,VERSION}`；`.env` 含新 pair；YAML 与 `.env` 一致 |
| 2 | 已有 YAML，再次执行 | 二进制跳过；`livekit.yaml` 不变；`.env` 不变；退出 0 |
| 3 | 首次安装（无 YAML），`.env` 已有 pair + 非空 `LIVEKIT_SERVER` | **成功**（首次安装总是重新生成）；`.env` 旧 pair 被新 pair 覆盖并有备份；`LIVEKIT_SERVER` 原值不变；新建 `livekit.yaml`；打印提示 |
| 4 | 删除 `livekit.yaml` 后重跑 | 视为首次安装：生成新 pair；`.env` key/secret 更新并有备份；`LIVEKIT_SERVER` 不变；生成新 `livekit.yaml`；**不产生** `livekit.yaml.bak.*` |
| 5 | 已有 YAML，`.env` 缺失 / key-secret 不一致 / 无法解析 | 只读检查后 warn；不修改 `.env` 和 YAML；退出 0 |
| 6 | 首次安装，shell 设置了 `LIVEKIT_KEY`/`SECRET`（含空值） | 非 0 退出；不生成新 pair、不写 `.env`/YAML |
| 7 | 已有 YAML，shell 覆盖与 YAML/`.env` 不一致 | 只读检查后 warn；不修改任何文件 |
| 8 | `--dry-run` | 无文件写入、无网络访问、退出 0 |
| 9 | 不支持的 `uname -m`/`uname -s` | 非 0 退出，提示 |
| 10 | 校验和不匹配（fake checksums） | 非 0 退出，不安装 |
| 11 | 生成的 YAML | `grep -E '^[[:space:]]*redis:'` 无匹配；`ports` 可解析；`use_external_ip: true`、`max_participants: 100` |
| 12 | 权限 | `livekit.yaml`/`.env` 600；二进制 755；sample/LICENSE 644 |
| 13 | 输出扫描 | stdout/stderr 不含生成 secret 的字符串 |
| 14 | `.env` 更新（首次安装） | 其它变量/注释保留；key/secret 各只出现一次；`LIVEKIT_SERVER` 行原样保留；文件以换行结尾 |
| 15 | 已有 YAML，删除 `bin/livekit/livekit-server`（或 `VERSION`）后重跑 | 同版本重新下载/安装；不修改 key、`.env`、配置 |

`.env` 解析与更新可以用临时文件做纯 shell 断言（`grep -c`、`tail`、`cmp`）。

### 11.3 回归冒烟（真实仓库）

- `./livachat.sh install`（无 `--livekit`）：不创建/修改 `bin/livekit/` 与 meeting key；基础安装流程与现状一致。
- `./livachat.sh start/stop/status`：行为与修改前一致，参数解析改动无回归。

### 11.4 真实平台手工验证

首次安装（无 `livekit.yaml`）会重新生成 key 并覆盖 `.env` 中的旧 pair；`LIVEKIT_SERVER` 不由安装器修改，安装后需手工编辑 `.env` 指向本地 WSS。已有 `livekit.yaml` 时安装器不生成 key、不改 `.env` 和配置。

Linux（amd64 至少一种）：

```bash
./livachat.sh install --livekit --skip-base
bin/livekit/livekit-server --version
bin/livekit/livekit-server --config bin/livekit/livekit.yaml ports
sha256sum bin/livekit/livekit-server 2>/dev/null || shasum -a 256 bin/livekit/livekit-server   # 记录，仅人工核对
```

macOS：

```bash
# 需要时由安装器执行 brew install livekit；已有本地二进制时跳过 brew
./livachat.sh install --livekit --skip-base
bin/livekit/livekit-server --version     # 记录实际 brew 版本
```

### 11.5 与 LivaChat 的端到端验证

1. 配置 TLS 代理与 `.env` 的 `LIVACHAT__MEETING__LIVEKIT_SERVER=wss://...`；确认 `LIVACHAT__MEETING__ENABLED=true`、`LIVACHAT__MEETING__EXTERNAL_SERVER_ADDR` 已配置，并已构建 `apps/web/meeting/dist`。
2. `./livachat.sh start`，确认启动日志中 meeting 配置校验通过（无 wss/空 key 错误）。
3. 通过 Agent 创建会议或直接向 lvdata 写入测试会议，调用 `/api/mylife/join_meeting` 获取 token。
4. 浏览器打开 `/meeting/`，输入会议号 + 姓名入会；确认音视频、屏幕共享可用。
5. 检查 `bin/livekit/` 配置与 `.env` 的 key 一致；按 §7.3 删除 YAML 后重跑重新生成 key，重启 LiveKit 后旧 token 失效。

---

## 12. 实施步骤与文件清单

### 12.1 实施顺序

1. 修改 `livachat.sh`：`cmd_install` 参数解析、首次安装判定（`livekit.yaml` 是否存在）、平台安装、key/`.env`/配置一次性生成，以及已安装状态的只读检查。
2. 新增 `tests/install/livekit_install_test.sh`：fake binary + 本地 `file://` 资产，覆盖首次安装、只读重跑、dry-run、shell 覆盖、Linux 下载/校验和、symlink、参数错误。
3. 更新 `.gitignore`：`/bin/`、`/.env.tmp.*`、`/.env.bak.*`。
4. 可选：在 `.env.example` meeting 注释块补一句"本地 LiveKit 可用 `./livachat.sh install --livekit` 生成并写入 key/secret"。
5. 可选：在 `REPO.md` 常用命令中补充 `./livachat.sh install --livekit`。
6. 运行 `bash tests/install/livekit_install_test.sh`，并按 §11 做平台验证。
7. 更新 `docs/livachat_start.md` 或新增运行说明（如需要）。

### 12.2 变更文件

| 文件 | 变更 |
|------|------|
| `livachat.sh` | 修改：`install` 子命令增加 `--livekit` 选项及 LiveKit 安装/配置函数 |
| `tests/install/livekit_install_test.sh` | 新增：隔离安装测试（fake binary + 本地 file:// 资产） |
| `.gitignore` | 新增 `/bin/`、`/.env.tmp.*`、`/.env.bak.*` |
| `.env.example` | 可选注释补充 |
| `REPO.md` | 可选命令补充 |
| `docs/livekit_install.md` | 本文 |

不改动：

- `services/`、`configs/`、`tools/lvdata-mcp/`。
- LiveKit sample 内容（原样保存）。
- 任何已提交的密钥/配置。
- 不新增 `install.sh`、不新增独立 LiveKit 安装脚本。

---

## 13. 风险与开放问题

| # | 风险/问题 | 影响 | 建议/决策 |
|---|-----------|------|-----------|
| Q1 | LiveKit 安装逻辑直接追加到 `livachat.sh`，脚本明显变长 | 可维护性下降 | 新增函数集中在 `cmd_install` 后的一段；不新增独立入口；后续若继续膨胀再评估拆分 |
| Q2 | 首次安装是否覆盖 `.env` 中已有 pair | 可能使现有 demo server 的 key 失效 | 首次安装（无 YAML）总是生成新 pair 并覆盖 `.env`（先备份），打印醒目警告；已有 `livekit.yaml` 时不生成、不修改 |
| Q3 | macOS 无官方 release 二进制 | 版本依赖 Homebrew，可能漂移 | 用 brew；版本不一致时 warn 并记录实际版本 |
| Q4 | `use_external_ip` 默认值 | 特殊 NAT/多网卡环境可能探测错误 | 默认 `true`（官方 sample）；ICE 负责探测选择；异常时手工 `rtc.node_ip`，不提供开关 |
| Q5 | WSS/TLS 不由安装器处理 | 用户手工填写的 URL 缺失/错误时服务端 fail-fast | 文档给出 nginx/mkcert 说明；`LIVACHAT__MEETING__LIVEKIT_SERVER` 必须手工填 `wss://`，安装器不写 URL |
| Q6 | UDP 50000-60000/7881 未自动放行 | 入会可能只有信令、无媒体 | 文档列出端口；部署时手工放行/安全组配置 |
| Q7 | checksums 不是签名 | 供应链风险 | 本期接受；未来可固定 checksum 或签名校验 |
| Q8 | 无 Redis / 多节点 | 不能水平扩展 | 明确单节点；后续如需要另立设计 |
| Q9 | 重新生成 key 后旧 token 失效 | 用户需重新入会 | 文档提示；短 TTL 降低影响 |
| Q10 | `.env` 备份含 secret | 旧备份可能残留 | 600 权限；提示可删除；不自动提交 |
| Q11 | Linux armv7/Alpine 等未实测 | 兼容性未知 | 支持映射但不承诺；失败时提示手动安装 |

### 决策记录（2026-11-15 review）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 首次安装的 key pair | **A**：首次安装（无 YAML）总是生成全新 pair，覆盖 `.env` 中已有值（先备份）并新建 `livekit.yaml`；已有 YAML 时不生成、不修改 `.env`/配置 |
| D2 | `rtc.use_external_ip` 默认值 | **B：`true`**（官方 sample）；ICE 探测选择，不提供本地开关 |
| D3 | macOS Homebrew 版本漂移 | **A**：接受 brew 实际版本，记录并 warn |
| D4 | 默认 LiveKit 版本 | **A**：固定 `1.13.11`（与项目文档/demo 一致） |
| D5 | 生成的 room 默认值 | **A 变体**：`empty_timeout=300`、`departure_timeout=300`、`max_participants=100` |

> D1/D3/D4 按推荐执行；D2/D5 按本次 review 确认值执行。

### 已按推荐处理（如无异议不再单独确认）

- 首次安装（无 `livekit.yaml`）总是生成新 pair 并更新 `.env`；已有 `livekit.yaml` 时不生成、不修改 key、`.env` 或配置。
- `--skip-base` 保留为高级/测试选项，且必须与 `--livekit` 同时使用。
- `LIVACHAT__MEETING__LIVEKIT_SERVER` 由用户手工编辑 `.env`；安装器不读取、不修改、不校验，也不提供相关参数。
- 不自动修改 `LIVACHAT__MEETING__ENABLED` / `LIVACHAT__MEETING__EXTERNAL_SERVER_ADDR`；是否启用 meeting 由部署方决定。
- 本期不把 LiveKit 纳入 `livachat.sh start/stop/status`；运行命令见 §9。

---

## 14. 验收清单

- [ ] `bash tests/install/livekit_install_test.sh` 通过（fake binary + 本地 `file://` 资产，不访问网络）。
- [ ] 不新增 `install.sh`；LiveKit 逻辑全部在 `livachat.sh` 内，且无 `--livekit` 时 `install` 行为与现状一致。
- [ ] `./livachat.sh install --livekit` 在 Linux 上安装 `bin/livekit/livekit-server`，版本为 `1.13.11`（或指定版本），SHA-256 校验通过。
- [ ] macOS 通过 Homebrew 安装并复制到 `bin/livekit/livekit-server`，版本记录正确。
- [ ] `bin/livekit/config-sample.yaml` 来自与实际安装版本一致的官方 tag（默认 `v1.13.11`）。
- [ ] `bin/livekit/livekit.yaml` 无激活的 `redis:` 块；`ports` 命令可解析；端口为 7880/7881/50000-60000；`use_external_ip: true`、`max_participants: 100`。
- [ ] 首次安装后 `.env` 中 key/secret 与 `livekit.yaml` 完全一致；`livekit.yaml`/`.env` 权限 600。
- [ ] 首次安装（无 `livekit.yaml`）总是生成新 pair，覆盖 `.env`（先备份）并新建配置；已有 `livekit.yaml` 时不生成 key、不修改 `.env`/配置。
- [ ] 不提供 `--rotate-keys`/`--reuse-env-keys`/`--force`；重置/重装通过删除 `livekit.yaml` 或二进制后重跑，且不产生 `livekit.yaml.bak.*`。
- [ ] `.env` 中其它变量与注释保持不变；key/secret 键唯一；`LIVEKIT_SERVER` 行原样保留。
- [ ] 安装器不修改 `LIVACHAT__MEETING__LIVEKIT_SERVER`；首次安装只更新 `.env` 的 key/secret，URL 由用户手工编辑。
- [ ] `--dry-run` 无网络、无文件写入。
- [ ] 不支持平台/架构、校验和失败、首次安装前置检查失败均非 0 退出且不产生半成品；已有配置的 key 不一致只 warn，不修改。
- [ ] 日志与输出不含 secret。
- [ ] `/bin/`、`/.env.tmp.*`、`/.env.bak.*` 已加入 `.gitignore`。
- [ ] 文档中的示例全部使用占位符，无真实域名/IP/密钥。

---

## 15. 参考

### 15.1 LiveKit

- Release `v1.13.11`：<https://github.com/livekit/livekit/releases/tag/v1.13.11>
- Release 资产下载基址：<https://github.com/livekit/livekit/releases/download/v1.13.11>
- Sample config：<https://raw.githubusercontent.com/livekit/livekit/v1.13.11/config-sample.yaml>
- `generate-keys` 源码：<https://github.com/livekit/livekit/blob/v1.13.11/cmd/server/commands.go>
- secret 生成：<https://github.com/livekit/protocol/blob/main/utils/secret.go>
- API key 前缀：<https://github.com/livekit/protocol/blob/main/utils/guid/id.go>
- 健康检查：<https://github.com/livekit/livekit/blob/v1.13.11/pkg/service/server.go>
- 自托管/端口/TLS：<https://docs.livekit.io/home/self-hosting/deployment/>
- 本地运行：<https://docs.livekit.io/home/self-hosting/local/>
- Homebrew formula：<https://github.com/Homebrew/homebrew-core/blob/HEAD/Formula/l/livekit.rb>
- 官方安装脚本：<https://get.livekit.io>

### 15.2 Codebase

- `livachat.sh`（`cmd_install`、子命令分发）
- `services/shared/config/loader.py:63-90`（`.env` 加载）、`:116+`（`LIVACHAT__` 覆盖）
- `services/shared/config/models.py:300-317`（`MeetingConfig`）
- `services/shared/config/validation.py:102-163`（meeting 校验，wss-only）
- `services/bff/meeting/service.py:61,104`（key/secret 与 serverUrl）
- `configs/dev/server.yaml:299-309`（meeting 空值占位）
- `.env.example:66-72`（meeting 变量模板）
- `.gitignore`（当前未忽略 `/bin/`）
- `docs/livachat_meeting.md:1230`（wss 强制要求）
- `docs/personal_meeting_design.md` §3.2（端口/TLS 参考；Redis 建议已被本文取代）
