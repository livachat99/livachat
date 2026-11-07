# Server 日志文件轮转（方案 B：进程内 RotatingFileHandler）分析与设计

> 状态（2026-10-13）：**已实施**。拍板：enabled 默认 true；lvdata-mcp 后置（本期不做）；
> boot 文件方案；configs/test 与 dev 同目录。
> 需求（用户原话）：可在 server config file 里配置 log 目录、单个 file size、file count；
> 默认：log 目录 = `project_root/logs/`，file size 50MB，file count 4。
> **（2026-10-13 修订：file count → 3，全局统一——模型默认、两个 yaml、文档同步）**
> 全文结论以当前代码与实测为基础（附 file:line）；不含猜测。
> 阅读约定：§1–§2 为**实施前**现状快照（行号为分析时点版本）；§3–§8 为已实施形态。

---

## 1. 问题背景

chat server 与 lvdata-mcp 的日志**目前均无任何轮转**（全链路核实：Python 无文件 handler、
启动脚本无 rotate 逻辑、macOS newsyslog 无相关条目）。日志文件无限增长；live 实例的
`~/temp/livachat.log.prev/.prev2`（2026-10-03 21:15/21:16）是**一次性人工重命名**痕迹。

## 2. 现状分析（代码事实 + 实测）

### 2.1 chat server 日志写链

| 环节 | 事实 | 位置 |
|---|---|---|
| 唯一装配点 | `setup_logging(json_logs, log_level)`：`root.handlers.clear()` 后只挂一个 `logging.StreamHandler()`（**无参默认 = stderr**，实测确认）；uvicorn/websockets 等噪声库被指向同一 handler 并钉 WARNING | `services/shared/observability/logging.py:49-105` |
| 调用点 | lifespan 启动段：`setup_logging(json_logs=cfg.observability.json_logs, log_level=cfg.server.log_level)` | `services/app/application.py:24-26` |
| uvicorn 启动 | `uvicorn.run(..., reload=reload)`，**reload 默认开**（`LIVACHAT_SERVER_RELOAD` 默认 `"1"`，main.py:16）→ 运行期是 **reloader 父进程 + worker 子进程**；父进程经入口导入 `app.main`（模块级 `create_app()`，main.py:1-3），但不跑 lifespan | `services/app/main.py:15-28` |
| 落盘方式 | 全靠 launcher 输出重定向：livachat.sh `nohup livachat-server >> "$SRV_LOG" 2>&1 &`；手动实例为 `\| tee ~/temp/livachat.log`（实测：`tee` 进程持有 fd，`lsof ~/temp/livachat.log` → `tee 34707`） | `livachat.sh:361`、`livachat.sh:386`（mcp） |
| 现有目录约定 | livachat.sh `LOG_DIR="$ROOT/logs"`、`SRV_LOG=$LOG_DIR/livachat.log`，`start` 时 `mkdir -p`；`/logs/` 已在 `.gitignore:8` | `livachat.sh:19-23,337` |
| 配置现状 | `ObservabilityConfig`：`metrics_enabled/tracing_enabled/json_logs`（无文件日志字段） | `services/shared/config/models.py:212-215` |

**可行性关键**：所有服务日志（structlog + uvicorn + 噪声库）都被收敛到 setup_logging 的
单一 handler 装配——这正是方案 B 可以在**一个函数**里挂上旋转文件 handler 的原因。

### 2.2 与 launcher / reload 的既有交互（设计必须处理的三个坑）

1. **重定向与轮转互斥**：进程以 `>>`/tee 持有的 fd 在 `mv`（轮转）后仍指向旧 inode，
   写入会"丢失"。轮转生效的前提是**由持有 fd 的同一进程自己 rotate**（RotatingFileHandler
   在 emit 内 rename+重开，内部有锁）——因此重定向**不能继续指向被轮转的文件**。
2. **reload 父子**：setup_logging 在 lifespan 里、lifespan 只在 worker 跑 → 旋转文件由 worker
   独占写入（单写者 ✓）。注意父进程**会**导入 `app.main`（console script 入口即 main.py:1-3
   的模块级 `create_app()`），但不会运行 lifespan——其启动几行与 worker 进 lifespan 前的
   `Started server process`/`Waiting for application startup` 一样，**不经过我们的 handler**，
   只能落在 launcher 重定向里，那里必须给它们一个安身之处。
3. **同 worktree 多实例**：dev 手起实例与 livachat.sh test 实例同属 wt_d4flash →
   默认目录相同。RotatingFileHandler **不支持多进程同文件**（两个进程各自 rename 会互相
   踩踏）。需在配置层给 test 环境覆盖 dir，或文档化"同目录勿跑多实例"。

### 2.3 lvdata-mcp 现状（与 chat server 非同构）

- `_setup_logging(level)`（`tools/lvdata-mcp/src/lvdata_mcp/main.py:59-74`）：
  `logging.basicConfig`（stderr）+ **structlog 原生路径**（未走 stdlib
  ProcessorFormatter，默认 PrintLogger → stdout）+ `make_filtering_bound_logger`。
- 有多个 CLI 子命令（serve/reindex/eval/stage，`main(argv)` :267 起统一调 `_setup_logging`）。
- **结论**：其改造不是"同一函数加 handler"（structlog 不走 stdlib，需要换 logger_factory
  或改走 ProcessorFormatter——属于更大的重构面）；且 CLI 一次性命令不应建/转日志文件。
  本篇详细设计只覆盖 chat server；lvdata-mcp 单列 §6 待拍板。

## 3. 设计方案（chat server）

### 3.1 配置模型

`ObservabilityConfig`（models.py:221）扩展一个子模型（`LogFileConfig` 实体在 :212-218；与需求逐字对应）：

```yaml
observability:
  metrics_enabled: true
  tracing_enabled: false
  json_logs: true
  log_file:                # 新增
    enabled: true          # 默认开（替代现有 launcher 重定向的落盘角色）
    dir: ""                # 空 = <project_root>/logs（resolve_path 语义，见下）
    max_bytes: 52428800    # 50MB；沿用 bytes int 风格（对齐 max_frame_bytes）
    backup_count: 3        # 保留 .1~.3 共 3 份
```

```python
class LogFileConfig(BaseModel):
    enabled: bool = True
    dir: str = ""                                   # 空 = <project_root>/logs
    max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    backup_count: int = Field(default=3, ge=1)
```

- **dir 解析**：`resolve_path(dir or "logs")`——相对路径→**项目根**、绝对路径原样
  （`services/shared/config/loader.py:151-156`，唯一解析点，与 reload_dirs 同款）。
  默认值即 `project_root/logs`，与 livachat.sh 现有 `$ROOT/logs` 一致。
- 文件名固定 `livachat.log`（不新增 knob；对齐现有命名）。
- env 覆盖：`LIVACHAT__OBSERVABILITY__LOG_FILE__DIR` 等（loader 双层下划线机制，loader.py:112-130）。
- configs/dev yaml 显式写出（值=默认值，起文档作用）。**修订（2026-10-13 用户指令）**：test 实例
  （livachat.sh 管）日志目录固定为 `logs/test/`——start 以 env 覆盖
  `LIVACHAT__OBSERVABILITY__LOG_FILE__DIR` / `LVDATA_MCP__SERVER__LOG_FILE__DIR="logs/test"`（不依赖
  configs/test 既有内容），新生成的 configs/test 亦写入该值（install 的 sed 规则）；与手动 dev
  实例的 `logs/` 隔离，**取代原「同目录」拍板**（§2.2-③ 的多实例竞争随之消解）。boot 文件同入
  `logs/test/`（`LOG_DIR="$ROOT/logs/test"`）。

### 3.2 装配（setup_logging）

```python
def setup_logging(*, json_logs: bool = True, log_level: str = "INFO", file: LogFileConfig | None = None) -> None:
    ...  # processors/renderer/formatter 不变
    if file is not None and file.enabled:
        log_dir = resolve_path(file.dir or "logs")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                log_dir / "livachat.log", maxBytes=file.max_bytes, backupCount=file.backup_count, encoding="utf-8"
            )
        except OSError as exc:
            raise RuntimeError(f"log dir not usable: {log_dir} ({exc})") from exc   # fail-fast
        handler.setFormatter(formatter)
        handlers: list[logging.Handler] = [handler]         # 运行态：仅文件（§8）
    else:
        handler = logging.StreamHandler(sys.stdout)         # 兜底（测试 / disabled，§8 D1）
        handler.setFormatter(formatter)
        handlers = [handler]
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)
    # 噪声库循环：挂 handlers 全量（改造前只挂单个 handler）
```

- **`file=None` → stdout 兜底（§8 D1）**：`tests/unit/test_logging.py` 既有用例不经 lifespan、
  按默认参数直接调 setup_logging——不受影响。
- fail-fast 口径：目录不可建/不可写 → 启动报错（对齐 webroot/sqlite 的 fail-fast 风格；
  静默无日志是不可接受的降级）。
- 磁盘上界 = `(backup_count + 1) × max_bytes` = **200MB**（50MB×4）。
- 旋转语义（stdlib 实测，Python 3.12）：下一条记录会**达到/超过** `max_bytes` 时先滚动
  再写（`>=` 边界，空文件不触发）；`livachat.log` → `.1`，既有 `.1`→`.2`…，`backup_count`
  份封顶（更旧删除）；handler 内部锁串行 rename（单进程内 asyncio+线程混写安全）。
- lifespan 调用点改为：
  `setup_logging(json_logs=..., log_level=..., file=cfg.observability.log_file)`
  （`application.py:26-27`）。

### 3.3 启动链配合（livachat.sh）

被轮转文件的 fd 所有权交给进程内 handler，**shell 重定向改道**：

- 新增 `SRV_BOOT="$LOG_DIR/livachat.boot.log"`（lvdata-mcp **后置**：其重定向保持原样，
  无 `MCP_BOOT`；livachat.sh:24），start 时 `>/dev/null 2>"$SRV_BOOT"`（每次 start **截断**）。
- **勘误（2026-10-13 实施后 review）**：初版为 `>"$SRV_BOOT" 2>&1` + console 落 stderr——
  于是运行期全部记录被镜像进 boot（不轮转），与「boot 有界」的设计意图相悖。改为
  **console handler 走 stdout**（`logging.StreamHandler(sys.stdout)`）+ launcher
  `stdout→/dev/null`、`stderr→boot`：boot 只含 reloader 父进程、lifespan 前几行、
  warnings 与原生崩溃输出，真正有界；全量记录在轮转文件中；手动实例 `|& tee` 行为不变。
  （2026-10-13 后续变更：console handler 已整体移除，见 §8；本段其余结论（boot 有界等）仍成立。）
- 失败提示分叉（实施前全部指向 $SRV_LOG；现 livachat.sh:374/376/380）：
  - "子进程已退出 / 启动后即退出"（多为配置/依赖加载期崩溃，日志尚未进轮转文件）→ 提示 `tail $SRV_BOOT`（并保留 $SRV_LOG 参照位）；
  - "30s 未通过健康检查"（进程活着，日志已在轮转文件）→ 维持 `tail $SRV_LOG`。
  - 注意：reload 模式下 boot 里的历史行会被每次 start 截断——排障语义从"全历史"变为
    "本次启动的早段"，属预期收缩（全量历史在轮转文件里）。
- 手动 dev 实例（`~/temp` + tee）：改动后进程**只写** `<project_root>/logs/livachat.log`（§8 后
  stdout 已无日志输出）——tee 管道不再承接日志，建议去掉 tee；该实例非脚本管理，仅文档注明。
- `status` 与 usage 文案（livachat.sh:48 "日志: ..."）同步更新。

## 4. 测试与验收

**单测**（扩展 `services/tests/unit/test_logging.py`，tmp_path + 小阈值）：

1. `enabled=false` / `file=None`（默认）→ 不创建任何文件（现状兼容）；
2. 启用 → 目录自动 mkdir、`livachat.log` 生成、JSON 行可解析；
3. 写超 `max_bytes` → `.1` 生成、当前文件重置；份数封顶生效（测试以 512B×2 断言
   `.1`/`.2` 在、`.3` 不在；总量 ≤ (count+1)×size）；
4. ~~`dir` 相对路径落项目根~~ **未单测（已剔除）**：`_project_root()` 是文件系统向上查找、
   **无 env 钩子**（loader.py:44-55）——相对 dir 会写真实 `<project_root>/logs`，违反测试零 FS
   副作用原则；相对语义复用 `resolve_path` 唯一解析点。env 覆盖机制已单独功能核对
   （`LIVACHAT__OBSERVABILITY__LOG_FILE__ENABLED=false` → enabled=False）；
5. 目录不可建（父路径为只读/文件占位）→ RuntimeError（fail-fast）。

**手工验收**（实施后）：

- `./livachat.sh start` → `<project_root>/logs/livachat.log` 出现，`livachat.boot.log` 只有
  早段几行；`tail` 两个文件内容衔接正确；
- 把 `max_bytes` 临时改小（如 4096）+ 反复触发请求 → 观察 `.1/.2` 滚动与计数上限；
- reload 触发（改一行代码）→ 轮转文件不中断、boot 文件被截断重写（预期行为）。

## 5. 变更文件清单（已完成）

| 文件 | 动作 |
|---|---|
| `services/shared/config/models.py` | 新增 `LogFileConfig`；`ObservabilityConfig.log_file` |
| `services/shared/observability/logging.py` | `setup_logging` 增 `file` 参数 + RotatingFileHandler 装配 + 噪声库循环挂全量 handler |
| `services/app/application.py` | lifespan 调用点传 `cfg.observability.log_file` |
| `configs/dev/server.yaml` | 显式写出 `log_file` 段（configs/test 由 livachat.sh install 复制生成） |
| `services/tests/{integration/gateway,e2e_backend}/conftest.py`、`integration/bff/test_bff_smoke.py` | 测试 spawn 的 server 经 `LIVACHAT__OBSERVABILITY__LOG_FILE__ENABLED=false` 关文件日志（防与真实实例共写 logs/，遵守测试零 FS 副作用原则） |
| `livachat.sh` | boot 重定向、失败提示分叉、文案 |
| `services/tests/unit/test_logging.py` | 新增 §4 单测 |
| `docs/server_design.md` §10.2 | 日志节补轮转语义描述 |

## 6. 拍板记录（2026-10-13）

1. `enabled` 默认 = **true**（默认落 project_root/logs）。
2. lvdata-mcp：**后置**，本期不做（其 structlog 原生路径收敛为另立任务；过渡期其日志
   保持现状——launcher 重定向、无轮转）。
3. **boot 文件**方案采纳（`livachat.boot.log`，每次 start 截断）。
4. configs/test 与 dev **同目录**（project_root/logs）；多实例同目录的轮转竞争为已接受风险。
   **（2026-10-13 修订：test 实例改用 `logs/test/` 子目录隔离，见 §3.1。）**

---

## 7. lvdata-mcp serve 同构设计（2026-10-13，已实施）

承接 §6-2 后置项：lvdata-mcp server 的日志需求与 chat server 一致（目录/单文件大小/份数可配，
默认 `<project_root>/logs`、50MB、3 份；进程内轮转）。**仅有文件日志的适用范围不同：只覆盖
`serve` 子命令；CLI（reindex/eval/stage）不建文件、控制台输出形状不变**（用户指示）。

### 7.1 现状分析（代码事实，承接 §2.3 并补关键点）

| 环节 | 事实 | 位置 |
|---|---|---|
| 装配点 | `_setup_logging(level)`：`logging.basicConfig(level)`（stdlib → stderr，默认平文格式）+ structlog 原生配置 | `tools/lvdata-mcp/src/lvdata_mcp/main.py:59-74` |
| **关键差异** | structlog **完全绕过 stdlib**：未设 logger_factory → 默认 `PrintLoggerFactory` → `PrintLogger`（stdout），`JSONRenderer` 直出。实测样例 `{"event","level","timestamp"}`（无 logger 名）——**现装任何 stdlib root/FileHandler 都捕获不到 structlog 记录** | main.py:61-74；实测 `~/temp/lvdata_mcp.log` |
| 调用点 | `main()` 对所有子命令统一调用一次（serve/reindex/eval/stage 共用入口） | main.py:270 |
| serve 路径 | `run_serve`：`uvicorn.run(app, host, port, log_level=cfg.server.log_level.lower())`；uvicorn 默认 logging 在 `uvicorn.run` 启动时经 dictConfig 应用——**时序与 chat server 相反**，见 §7.2 勘误 | main.py:141、:171-172 |
| 配置 | `ServerConfig`（`extra="forbid"`）：host/port/token/log_level/max_ip_conn——无文件日志字段；env 覆盖为手写 `_set_nested`（与 chat server 同构，`LVDATA_MCP__` 前缀）；`_project_root()` 同构，**无 resolve_path 等价物** | `tools/lvdata-mcp/src/lvdata_mcp/config.py:110-117、:220-245、:248-258` |
| 落盘 | livachat.sh：`nohup lvdata-mcp serve >> logs/lvdata_mcp.log 2>&1`（纯 append，无轮转） | `livachat.sh:391` |
| contextvars | 处理器链含 `merge_contextvars`，但 src 中无 `bind_contextvars` 调用（实际近 no-op；保持即可） | 全仓 grep |

**结论**：满足「一切记录进轮转文件」的前提是**先收敛 structlog 到 stdlib**（§2.3 已预判）。
lvdata-mcp 是独立发行包（不依赖 services/shared），故采用**同构实现**（同一模式、各自代码），
而非 import 复用 chat server 的 `setup_logging`。

### 7.2 设计方案

**收敛装配**（镜像 `services/shared/observability/logging.py` 的处理器链与 root/噪声库接线）：

```python
def _setup_logging(level: str, file: LogFileConfig | None = None) -> None:
    shared_processors = [merge_contextvars, add_log_level, TimeStamper(fmt="iso"), format_exc_info]
    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = ProcessorFormatter(foreign_pre_chain=shared_processors,
                                   processors=[ProcessorFormatter.remove_processors_meta, JSONRenderer()])
    if file is not None and file.enabled:            # 仅 serve 传入
        log_dir = _resolve_path(file.dir or "logs")  # 相对→项目根/绝对原样；空=project_root/logs
        ... mkdir + RotatingFileHandler(log_dir / "lvdata_mcp.log",
                                        maxBytes=file.max_bytes, backupCount=file.backup_count,
                                        encoding="utf-8")；失败 → RuntimeError（fail-fast）
        handlers = [file_handler]                    # 运行态：仅文件（§8）
    else:
        handlers = [StreamHandler(sys.stdout)]       # 兜底（CLI / 测试，§8 D1）
    root.handlers.clear() → 挂全量；root.setLevel(level)
    # 不碰 uvicorn 族 logger（时序原因，见下方勘误）：改为 run_serve 给 uvicorn.run 传
    # 显式 log_config，使 uvicorn 族 logger propagate=True、无自有 handler → 记录经 root
    # 统一入列（level 由 root 控制，INFO 可见；拍板②，§7.4）
```

- **勘误（2026-10-13 review，实施前修正）**：初稿写「噪声库 handlers 挂到 uvicorn logger
  （propagate=False），chat server 同款」——**在 mcp 的时序下不成立**：`_setup_logging` 在
  `main()`（main.py:270）跑，早于 `uvicorn.run`（:171-172）；uvicorn 启动时应用默认
  `log_config` 的 `dictConfig`，会**清空**预先挂在 uvicorn 族 logger 上的 handler（实测
  uvicorn 0.52.4：dictConfig 后 `handlers=[]`）。chat server 恰好成立只因它的 setup 在
  lifespan（`application.py:26`，dictConfig 之后）跑。若不修：uvicorn/access 记录永不进轮转
  文件；launcher 下 access 行经 stdout→/dev/null **整体丢失**、uvicorn 行 stderr→MCP_BOOT
  **全程追加（无界）**——正是 §3.3 勘误禁止的形态。
  **修正设计**：`run_serve` 改传显式 `log_config`（`disable_existing_loggers=False`；
  `"uvicorn"`/`"uvicorn.access"` 仅声明 `propagate: True`、不带 handlers）→ uvicorn 族记录经
  root（我们的 handlers）双达 console/文件，无 stderr 副本。注：**`log_config=None` 不可行**
  ——`uvicorn/main.py:456` 把 None 映射回默认 `LOGGING_CONFIG`（实测 0.52.4）；`log_level`
  参数照传（语义不变）。

- **CLI 零变更**：`main()` 改为 `_setup_logging(cfg.server.log_level,
  file=cfg.server.log_file if args.command == "serve" else None)`——reindex/eval/stage 维持
  现状（无文件、无目录副作用）。细则：第三方/uvicorn 族本就走 stdlib，收敛后其行由
  stderr 平文并入 stdout JSON——随收敛的必然变化，无 stdout/stderr 分离消费方（实测核查）。
- 配置模型：`config.py` 新增 `LogFileConfig`（与 chat server 同名同字段：`enabled=True /
  dir="" / max_bytes=52428800(gt=0) / backup_count=3(ge=1)`），`ServerConfig.log_file` 接入；
  env 覆盖 `LVDATA_MCP__SERVER__LOG_FILE__DIR` 等经既有 `_set_nested`（bool/int 同款强转）。
  新增 `_resolve_path()` 小函数（语义对齐 livachat `loader.py:151-156`）。
- yaml：`configs/dev/lvdata_mcp.yaml` 的 `server:` 段显式写出 `log_file`（值=默认）。
- 旋转语义/磁盘上界（200MB）/空文件不滚/fail-fast 口径：与 §3.2 完全一致（同一 stdlib 语义，
  不重复展开）。
- 输出形状：CLI stdout 字段集合保持（`event/level/timestamp` + 业务 kwargs；**不加
  add_logger_name**，单测钉字段集合——JSON 键序无语义，可能变化）；第三方行（uvicorn 等）
  由平文变 JSON——随收敛的必然变化（拍板③，§7.4）。

### 7.3 launcher / 测试 / 验收

- **livachat.sh**（mcp 段，以 server 段为模板，§3.3 同款）：新增 `MCP_BOOT="$LOG_DIR/lvdata_mcp.boot.log"`；
  重定向 `>>"$MCP_LOG" 2>&1` → `>/dev/null 2>"$MCP_BOOT"`（截断）；失败提示分叉
  （启动即退 → MCP_BOOT；健康超时 → MCP_LOG）；header/usage/status 文案同步。
- **测试**（`tools/lvdata-mcp/tests/`，新建 `test_logging.py`，镜像 services 五用例）：
  1. 默认（CLI 路径）→ 不建文件、目录零副作用；2. serve 参数启用 → mkdir + JSON 落盘可解析；
  3. 512B×2 轮转 → `.1/.2` 在、`.3` 不在；4. `enabled=false` → 不建；5. 目录不可用 → RuntimeError。
  `test_config.py` 补 `log_file` 默认值与校验（gt/ge）。
- **手工验收**（实施后）：clone §4 手工验收，对象换 mcp（`./livachat.sh start` 后 8190 实例）+
  `MCP_BOOT` 只含早段；CLI 冒烟（`lvdata-mcp reindex` 等）不产生文件。
- **实施验收（2026-10-13，已完成）**：`./livachat.sh start`（8180/8190）→ `logs/lvdata_mcp.log`
  实时写入 JSON，**含 uvicorn access 记录**（`{"event": "127.0.0.1:… - \"GET /healthz HTTP/1.1\" 200"}`，
  勘误修正设计的核心验收点）；`logs/lvdata_mcp.boot.log` **0 行**（mcp 的 uvicorn 自始走管线，
  stderr 无残料）；server 侧 boot 对照不变；status/stop 正常。mcp 测试套件全量
  （327 collected）exit 0：新增 `test_logging.py` 6 例 + `test_config.py` log_file 3 例。
- 变更文件清单（已完成）：`tools/lvdata-mcp/src/lvdata_mcp/{config.py,main.py}`、
  `configs/dev/lvdata_mcp.yaml`、`livachat.sh`、`tools/lvdata-mcp/tests/{test_logging.py,test_config.py}`、
  本文件 §7 状态回填。

### 7.4 拍板记录（2026-10-13）

1. **收敛方案**：采纳——收敛到 stdlib/ProcessorFormatter（与 chat server 同一模式；放弃
   「双路自定义 sink」备选）。
2. **uvicorn access 可见性**：**保持 INFO**（不 pin WARNING；mcp 是 HTTP 面，与 chat server
   有意不同）。
3. **第三方日志 JSON 化**：接受（`INFO: GET /healthz` 平文 → JSON，随收敛的必然变化）。
4. `enabled` 默认 `true`（serve 即写文件）；文件名沿用 `lvdata_mcp.log` / `lvdata_mcp.boot.log`。

---

## 8. 移除 stdout、仅写文件（2026-10-13，已实施）

用户指令：运行态日志**只写文件**，不再与 stdout 双写。

### 8.1 现状与动机

- 运行态（file 激活）两处装配均为双 handler（`StreamHandler(sys.stdout)` + `RotatingFileHandler`）
  双写；launcher 下 stdout→`/dev/null`（丢弃），tee 手起实例则**双份落盘**（tee 副本冗余）。
  （单测与 mcp CLI 的 `file=None` 路径本就只有 stdout 单 handler。）
- 测试面核查（已完成）：无任何 harness 依赖 spawned server 的 stdout——网关集成 conftest 本就
  `stdout=subprocess.DEVNULL`（`services/tests/integration/gateway/conftest.py:70`），BFF smoke 与
  e2e 均走 HTTP 就绪轮询 → 移除 stdout 记录不影响任何测试工具。

### 8.2 拍板（2026-10-13）

- **D1（保留兜底）**：`file` 未激活（`file=None` / `enabled=false`）时**保留 stdout 兜底**——
  services 测试的 capsys 断言与 mcp CLI（reindex/eval/stage）的终端输出都依赖该路径；
  `file` 激活时仅挂 file handler。
- **D2（接受）**：前台直跑不再有终端输出——排障统一 `tail -f logs/*.log`（移除 stdout 的必然结果）。

### 8.3 设计

- **两处对称**（`services/shared/observability/logging.py`、`tools/lvdata-mcp/src/lvdata_mcp/main.py`）：
  `handlers = 仅 [RotatingFileHandler]`（file 激活）`/ [StreamHandler(sys.stdout)]`（未激活）。
  formatter 共享、fail-fast、旋转语义、磁盘上界、噪声库循环（挂"当时的 handlers 全量"）、
  mcp CLI 的 `file=None` 传参逻辑——**全部不变**（CLI 行为零变更）。
- **launcher（livachat.sh，仅注释 2 处）**：`>/dev/null 2>"$BOOT"` 保持不变——stdout 已无日志，
  redirect 保留**仅为防 `nohup` 生成 `nohup.out`**；注释同步改。boot 机制不变（stderr 早段/
  warnings/原生崩溃）。
- **运行间差异（分服务）**：chat server——uvicorn 早段（lifespan 前）仍走 stderr→boot；lifespan
  之后应用与 `uvicorn.error` 记录只进文件（uvicorn/uvicorn.access/websockets 仍按 §3 现状 pin
  WARNING + propagate=False，其 INFO 本就不落任何地方——本变更不改该量级）。mcp——uvicorn 自始
  经 `_UVICORN_LOG_CONFIG` 进管线（无早段 stderr，boot 实测 0 行，见 §7.3），全部记录（含 access）
  只进文件。
- **stdout 例外（2026-10-13 用户指令）**：mcp `serve` 启动完成（lifespan 内、watcher 起完后）
  向 stdout 打一行 `server started: listening address: <host>:<port>`（`server.py` lifespan，
  `print(..., flush=True)`，**有意不走 logging**）——launcher 下随 stdout→/dev/null 不可见；
  前台/管道运行可见（供确认监听地址）。不影响文件日志与本节其余结论。

### 8.4 测试与验收（已实施）

- services `tests/unit/test_logging.py` 新增 2 例：① file 激活 → root handlers **仅含
  RotatingFileHandler** 且 capsys 无输出；② `file=None` → 仍含 StreamHandler（兜底回归）。
- mcp `tests/test_logging.py` 新增 1 例：serve 模式（file 激活）→ 无 StreamHandler、capsys 空；
  既有 `test_console_json_shape_preserved`（file=None）不动 = 兜底路径回归。
- 回归：services 全量 unit + mcp 全量——**既有用例全绿**（collected 总数 +2（services）/ +1（mcp））；
  launcher `start`→两 `logs/*.log` 正常轮转写入、boot 行为不变→`stop`。

**实施验收（2026-10-13，已完成）**：services **全树 1561 passed**（另 1 skip / 97 e2e deselect；
unit 子集 1395）；mcp **全量 327 collected 全过 exit 0**。launcher e2e（8180/8190）核心断言：
两进程 `fd 1 → /dev/null`、两 `logs/*.log` 实时写入（chat 侧 app 记录、mcp 侧 access JSON 入文件）、
server boot 5 行不变、mcp boot 0 行、status/stop/端口回收正常。
- 实施时同步调整 §3.2/§3.3/§7.2/§7.3 的相关表述（含"console 记录走 stdout"框架与 §3.3 勘误块
  的先行说明），并回填实施记录。

### 8.5 变更文件清单（已完成）

| 文件 | 动作 |
|---|---|
| `services/shared/observability/logging.py` | handlers：file 激活时仅 file；注释 |
| `tools/lvdata-mcp/src/lvdata_mcp/main.py` | 同构 |
| `services/tests/unit/test_logging.py`、`tools/lvdata-mcp/tests/test_logging.py` | 新增用例（2+1） |
| `livachat.sh` | 2 处注释 |
| 本文件 | §3/§7 表述调整 + 实施记录 |

### 8.6 运维面提示：容器/12-factor 采集与「默认反转」（2026-10-13 分析登记）

- **风险陈述**：`log_file.enabled` 默认 `true`（§6-4 拍板）+ §8「运行态只写文件」叠加 →
  **未显式配置的容器/k8s 环境日志只落容器内文件**，依赖 stdout 的采集链路（12-factor）
  表现为"日志静默消失"（采集端为空，文件躺在容器文件系统里）。
- **Escape hatch（回退 stdout，走 §8 D1 兜底路径）**：
  `LIVACHAT__OBSERVABILITY__LOG_FILE__ENABLED=false`（chat server）/
  `LVDATA_MCP__SERVER__LOG_FILE__ENABLED=false`（lvdata-mcp）；或配置 yaml 显式
  `log_file.enabled: false`。
- **部署要求**：生产/容器环境必须**显式二选一**——(a) 依赖 stdout 采集 → 设上述 `ENABLED=false`；
  (b) 保留文件落盘 → 确保容器内 `<root>/logs/` 被挂卷或由采集器读取。**不得依赖代码默认值**
  （默认=只写文件；现 `infra/kubernetes/configmap.yaml` 未登记该项）。
- **登记位置**：`docs/runbooks/deploy.md` Pre-Flight Checklist；`infra/kubernetes/configmap.yaml`
  observability 段注释（环境清单）。
