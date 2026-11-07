"""lvdata_mcp.yaml 加载与配置 schema（设计文档 §4）。

加载规则（§4.1，对齐 livachat shared/config/loader.py 模式）：
1. 路径：--config CLI → env LVDATA_MCP_CONFIG → <configs>/<env>/lvdata_mcp.yaml
   （跟随 _project_root() 约定：先找 configs/ + services/ 祖先目录）→ CWD lvdata_mcp.yaml。
   全部缺失 = 启动 fail-fast（§13）。
2. ${ENV_VAR} 占位符递归解析，未设置即报错（fail-fast）。
3. LVDATA_MCP__ 前缀双层下划线 env override（pydantic-settings 原生）。
4. pydantic 解析 + validate_security（非回环绑定必须配 token）。
"""

from __future__ import annotations

import copy
import ipaddress
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger("lvdata_mcp.config")

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_ENV_OVERRIDE_PREFIX = "LVDATA_MCP__"

# 分析产物输出语言：App 当前 16 个 canonical code（不含已下线的 tr）。
SUPPORTED_LANGUAGES = (
    "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de", "fr",
    "it", "es", "pt", "hi", "id", "th", "vi", "ar",
)


def normalize_language(value: object) -> str:
    """缺省/空/非法/未知 → en；合法 code 原样返回。"""
    if isinstance(value, str):
        value = value.strip()
        if value in SUPPORTED_LANGUAGES:
            return value
    return "en"

# §4.3 vendor 默认值（openai-compatible 端点；显式 base_url/model 优先）
VENDOR_DEFAULTS: dict[str, dict[str, str | dict[str, str]]] = {
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": {"audio": "qwen3.8-omni-flash", "vision": "qwen3.7-flash",
                  "summary": "qwen3.7-flash", "embedding": "text-embedding-v4"},
    },
    "openai": {"base_url": "https://api.openai.com/v1", "model": {}},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": {}},
    "deepseek": {"base_url": "https://api.deepseek.com", "model": {}},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": {}},
}

PROVIDER_KEYS = ("audio", "vision", "embedding", "summary")


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: str = "dashscope"
    base_url: str = ""          # 空 = 按 vendor 取默认
    api_key: str = ""           # 支持 ${ENV_VAR} 占位符（yaml 加载时解析）
    model: str = ""
    dim: int | None = None      # 仅 embedding 使用
    # 仅 embedding 使用（§15）：查询向量的余弦相似度下限——低于它的向量路命中不返回
    # （0 = 关闭过滤）。默认值即 dev 标定值；换 embedding 模型须按
    # docs/data/lvdata_mcp_tools.md §15.2 的方法复标后改这里。
    min_vec_score: float = 0.50
    # 仅 DashScope：驱动 X-DashScope-WorkSpace 头，与业务空间专属域名两值配套、
    # 互不派生（与 chat server 的 asr/llm/tts 同一约定）
    workspace_id: str | None = None

    def resolved(self, capability: str) -> "ProviderConfig":
        """按 vendor 默认值填充 base_url/model；返回新实例。

        ⚠ 白名单构造：新增字段必须在此显式透传，否则静默丢
        （chat server 有同款教训与测试，见 tests/unit/asr/test_factory.py）。
        """
        vd = VENDOR_DEFAULTS.get(self.vendor)
        base_url = self.base_url or (vd or {}).get("base_url", "")
        model = self.model
        if not model and vd:
            model = (vd.get("model") or {}).get(capability, "")
        return ProviderConfig(vendor=self.vendor, base_url=base_url,
                              api_key=self.api_key, model=model, dim=self.dim,
                              min_vec_score=self.min_vec_score,
                              workspace_id=self.workspace_id)

    def validate_api_key(self, capability: str) -> None:
        """api_key 缺失 → 启动 fail-fast（§13；embedding 整块为空 = 关闭，例外）。"""
        if not self.api_key:
            raise ValueError(
                f"providers.{capability}.api_key is not configured "
                "(yaml or ${ENV_VAR} placeholder)")


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio: ProviderConfig = Field(default_factory=ProviderConfig)
    vision: ProviderConfig = Field(default_factory=ProviderConfig)
    embedding: ProviderConfig = Field(default_factory=ProviderConfig)
    summary: ProviderConfig = Field(default_factory=ProviderConfig)

    def resolved(self) -> "ProvidersConfig":
        return ProvidersConfig(
            audio=self.audio.resolved("audio"),
            vision=self.vision.resolved("vision"),
            embedding=self.embedding.resolved("embedding"),
            summary=self.summary.resolved("summary"),
        )

    def validate_required(self) -> None:
        """audio/vision/summary api_key 必须配；embedding 整块为空 = 关闭（§4.2 注释）。"""
        for cap in ("audio", "vision", "summary"):
            getattr(self, cap).validate_api_key(cap)
        if self.embedding.model or self.embedding.base_url or self.embedding.api_key:
            self.embedding.validate_api_key("embedding")

    def embedding_enabled(self) -> bool:
        return bool(self.embedding.model and self.embedding.api_key)


class LogFileConfig(BaseModel):
    """日志文件轮转（docs/server_logging.md §7）：仅 serve 生效（进程内 RotatingFileHandler）。"""

    enabled: bool = True
    dir: str = ""                                             # 空 = <project_root>/logs
    max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)    # 单文件上限 50MB
    backup_count: int = Field(default=3, ge=1)                # 保留 .1~.N


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = 8090
    token: str | None = None      # 非回环绑定必填
    log_level: str = "INFO"
    max_ip_conn: int = 32         # 每 IP 并发请求上限
    log_file: LogFileConfig = LogFileConfig()


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_dir: Path = Path("/opt/lvdata")
    db_path: str = ""             # 默认 <root_dir>/lvdata_inventory.db
    rag_db_path: str = ""         # 默认 <root_dir>/lvdata_index.db
    chat_server_db_path: str = ""  # chat server DB（agent_jobs 源表，§3.8.1 ATTACH 只读扫描）

    @property
    def resolved_db_path(self) -> Path:
        return Path(self.db_path) if self.db_path else self.root_dir / "lvdata_inventory.db"

    @property
    def resolved_rag_db_path(self) -> Path:
        return Path(self.rag_db_path) if self.rag_db_path else self.root_dir / "lvdata_index.db"

    @property
    def resolved_chat_server_db_path(self) -> Path:
        return Path(self.chat_server_db_path) if self.chat_server_db_path \
            else self.root_dir / "chat_server.db"


class WatcherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_s: int = 60
    summarize_quiet_s: int = 600          # day_summary debounce 静默窗口
    flush_min_events: int = 10            # flush 门控：待处理 ≥ 此数即跑 embed/day_summary
    flush_max_delay_s: int = 600          # flush 门控：最旧待处理事件 ts 超此时限即跑
    timezone: str = "Asia/Shanghai"       # 默认时区；timezones 表为空时的兜底
    timezones: list[str] = Field(default_factory=list)
    concurrency: int = 4                  # 分析类 LLM 调用有界并发
    max_files_per_poll: int = 200         # 单轮 audio/vision 处理上限（0 = 不限）
    chatlog_max_bytes_per_round: int = 1048576   # 单轮每 chat jsonl 读取上限（字节）
    day_summary_max_per_poll: int = 10    # 单轮 day_summary 生成上限
    llm_timeout_s: int = 300
    audio_sample_rate: int = 16000        # 0 = 直传 ogg
    video_window_s: int = 30
    video_max_frames_per_call: int = 24
    video_extract_chunk_size: int = 4
    max_image_size: int = 1280            # 0 = 不缩放
    max_chars_per_event: int = 300
    stage_timeout_s: int = 1800
    # 审计表保留（docs/audit_tables_retention_plan.md §2.6）；0 = 关闭该项
    audit_retention_days: int = 14        # mcp_pipeline_runs（0 同时跳过孤儿水位清理）
    analysis_runs_retention_days: int = 30
    audit_prune_interval_s: int = 86400
    # video 帧保留（docs/data_clean.md）：超期且无 video 事件的 ok 帧删除；
    # 运行时 clamp ≥24h（无关闭开关）
    video_retention_hours: int = 72


class LvdataMcpConfig(BaseModel):
    """配置模型。env override 在 load_config 中显式应用（§4.1.3，对齐 livachat loader）。"""

    model_config = ConfigDict(extra="ignore")

    # 分析产物输出语言（docs/lvdata_multilingual.md）；MCP/REST 文案固定英文。
    language: str = "en"

    server: ServerConfig = Field(default_factory=ServerConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    @field_validator("language", mode="before")
    @classmethod
    def _normalize_language(cls, value: object) -> str:
        lang = normalize_language(value)
        raw = value.strip() if isinstance(value, str) else value
        if raw != lang:
            log.warning("unsupported lvdata output language %r; falling back to en", value)
        return lang

    def validate_security(self) -> None:
        """绑定非回环地址时必须配置 token，否则拒绝启动（§4.1 启动校验）。"""
        if self.server.host in ("localhost", "127.0.0.1", "::1"):
            return
        try:
            loopback = ipaddress.ip_address(self.server.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback and not self.server.token:
            raise ValueError(
                "binding non-loopback address requires LVDATA_MCP_TOKEN")


# ── yaml 加载 ──────────────────────────────────────────────────────────────


def _resolve_env_vars(value: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        name = match.group(1)
        val = os.environ.get(name)
        if val is None:
            raise ValueError(
                f"Environment variable '{name}' is not set "
                "(lvdata_mcp.yaml placeholder)")
        return val

    return _ENV_VAR_RE.sub(replacer, value)


def _resolve_placeholders(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_placeholders(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    return obj


def _set_nested(d: dict[str, Any], path: list[str], value: str) -> None:
    """沿 path 逐层下钻设置；值按现有类型强转（bool/int/float/str）。"""
    for segment in path[:-1]:
        if segment not in d or not isinstance(d[segment], dict):
            d[segment] = {}
        d = d[segment]
    existing = d.get(path[-1])
    if isinstance(existing, bool):
        d[path[-1]] = value.lower() in ("true", "1", "yes")
    elif isinstance(existing, int):
        d[path[-1]] = int(value)
    elif isinstance(existing, float):
        d[path[-1]] = float(value)
    else:
        d[path[-1]] = value


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """LVDATA_MCP__WATCHER__POLL_INTERVAL_S=30 → data["watcher"]["poll_interval_s"]。"""
    result = copy.deepcopy(data)
    for key, value in os.environ.items():
        if not key.startswith(_ENV_OVERRIDE_PREFIX):
            continue
        path = key[len(_ENV_OVERRIDE_PREFIX):].lower().split("__")
        _set_nested(result, path, value)
    return result


def _project_root() -> Path:
    """跟随 livachat _project_root()：先找 configs/ + services/ 祖先目录。"""
    start = Path(__file__).resolve().parent
    for ancestor in [start, *start.parents]:
        if (ancestor / "configs").is_dir() and (ancestor / "services").is_dir():
            return ancestor
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        if (ancestor / "configs").is_dir():
            return ancestor
    return cwd


def _resolve_path(path: str) -> Path:
    """相对路径按项目根解析、绝对路径原样（对齐 livachat resolve_path 语义）。"""
    p = Path(path)
    if p.is_absolute():
        return p
    return (_project_root() / p).resolve()


def _dotenv_path() -> Path:
    """仓库根 .env 路径（独立函数：测试可 monkeypatch 隔离本机文件）。"""
    return _project_root() / ".env"


def _load_dotenv() -> None:
    """把 .env 的值注入 ``os.environ``（setdefault：shell/CI 注入优先，.env 只补缺）。

    文件不存在 → 静默跳过。解析规则与 livachat loader 同构（跳过空行/``#`` 注释；
    容忍 ``export ``（含 Tab）前缀；按首个 ``=`` 拆分；去首尾空白；成对引号剥离；
    **同键后行覆盖前行**，与 ``set -a && source .env`` 一致；不支持行内注释与变量
    展开）——见 docs/ali_bailian_provider.md §9。
    """
    dotenv = _dotenv_path()
    if not dotenv.is_file():
        return
    values: dict[str, str] = {}
    for raw in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export") and line[6:7] in (" ", "\t"):
            line = line[6:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def find_config_path(explicit: Path | None = None, env: str | None = None) -> Path | None:
    """§4.1 路径解析链：--config → LVDATA_MCP_CONFIG → configs/<env>/ → CWD。"""
    if explicit is not None:
        if explicit.is_file():
            return explicit
        raise ValueError(f"Config file does not exist: {explicit}")
    env_var = os.environ.get("LVDATA_MCP_CONFIG")
    if env_var:
        candidate = Path(env_var)
        if candidate.is_file():
            return candidate
        raise ValueError(
            f"Config file does not exist (LVDATA_MCP_CONFIG): {candidate}")
    candidate = _project_root() / "configs" / (env or "dev") / "lvdata_mcp.yaml"
    if candidate.is_file():
        return candidate
    cwd = Path.cwd() / "lvdata_mcp.yaml"
    if cwd.is_file():
        return cwd
    return None


def load_config(explicit: Path | None = None, env: str | None = None) -> LvdataMcpConfig:
    """加载并校验配置；缺失/校验失败/占位符未设置一律抛错（fail-fast，§13）。

    先加载仓库根 ``.env``（setdefault：shell 注入优先），见
    docs/ali_bailian_provider.md §9。
    """
    _load_dotenv()
    path = find_config_path(explicit, env)
    if path is None:
        raise ValueError(
            "lvdata_mcp.yaml not found: checked --config / LVDATA_MCP_CONFIG / "
            "configs/<env>/lvdata_mcp.yaml / CWD")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = _resolve_placeholders(raw)
    raw = _apply_env_overrides(raw)
    cfg = LvdataMcpConfig(**raw)
    cfg.validate_security()
    # §4.3/§13：provider api_key 缺失 → 启动 fail-fast（对齐 v1 build_provider
    # 行为；embedding 整块为空 = 关闭向量检索，例外）
    cfg.providers.validate_required()
    return cfg


def effective_summary(cfg: LvdataMcpConfig) -> dict:
    """启动打印用（§4.1 对齐 livachat）：**只打非敏感生效值**。

    `base_url`/`api_key`/`workspace_id` 一律不打（2026-09-28 用户决定）——base_url 与
    workspace_id 都是内部端点/账号标识（值本身互为副本），api_key 即使打码也泄前缀后缀；
    日志会被采集/转发，这些不该出现在里面。provider 段只留 `vendor`/`model`
    （embedding 另加会改变检索结果的 `dim`/`min_vec_score`）。
    """
    prov = {}
    for cap in PROVIDER_KEYS:
        p = getattr(cfg.providers, cap)
        block = {"vendor": p.vendor, "model": p.model}
        if cap == "embedding":
            # 仅 embedding 使用的字段（§15）：都是"影响检索结果"的生效值，必须打出来
            # ——否则复标后想核对生效 τ 只能翻代码默认值或 yaml（曾有该盲区）
            block["dim"] = p.dim
            block["min_vec_score"] = p.min_vec_score
        prov[cap] = block
    return {
        "language": cfg.language,
        "server": {"host": cfg.server.host, "port": cfg.server.port,
                   "log_level": cfg.server.log_level, "token_set": bool(cfg.server.token)},
        "data": {"root_dir": str(cfg.data.root_dir),
                 "db_path": str(cfg.data.resolved_db_path),
                 "rag_db_path": str(cfg.data.resolved_rag_db_path)},
        "watcher": cfg.watcher.model_dump(),
        "providers": prov,
    }
