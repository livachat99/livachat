from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from shared.config.models import AppConfig
from shared.config.validation import validate_config
from shared.utils.errors import ConfigError

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: str) -> str:
    """Replace ``${ENV_VAR}`` placeholders with environment variable values.

    Raises ConfigError if a referenced variable is not set.
    """

    def _replacer(match: re.Match[str]) -> str:
        name = match.group(1)
        val = os.environ.get(name)
        if val is None:
            raise ConfigError(f"Environment variable '{name}' referenced in config is not set")
        return val

    return _ENV_VAR_RE.sub(_replacer, value)


def _resolve_placeholders(obj: Any) -> Any:
    """Recursively walk a dict/list tree and resolve ``${ENV_VAR}`` strings."""
    if isinstance(obj, dict):
        return {k: _resolve_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_placeholders(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    return obj


def _project_root() -> Path:
    """Find the project root by walking up from this file until we see ``configs/``."""
    start = Path(__file__).resolve().parent
    for ancestor in [start, *start.parents]:
        if (ancestor / "configs").is_dir() and (ancestor / "services").is_dir():
            return ancestor
    # Fallback: walk up from CWD
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        if (ancestor / "configs").is_dir():
            return ancestor
    return cwd


def _dotenv_path() -> Path:
    """仓库根 .env 路径（独立函数：测试可 monkeypatch 隔离本机文件）。"""
    return _project_root() / ".env"


def _load_dotenv() -> None:
    """把 .env 的值注入 ``os.environ``（setdefault：shell/CI 注入优先，.env 只补缺）。

    文件不存在 → 静默跳过。解析规则：跳过空行与 ``#`` 注释；容忍 ``export ``
    （含 Tab）前缀；按首个 ``=`` 拆分；去首尾空白；成对引号剥离；**同键后行覆盖前行**
    （与 ``set -a && source .env`` / python-dotenv 一致）。不支持行内注释与变量展开
    （docs/ali_bailian_provider.md §9）。
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


def _find_config_path(env: str | None) -> Path:
    """Locate the YAML config file for the given environment."""
    root = _project_root()

    # Try LIVA_CHAT_CONFIG env var first
    explicit = os.environ.get("LIVA_CHAT_CONFIG")
    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            return candidate
        raise ConfigError(f"Config file not found (LIVA_CHAT_CONFIG): {candidate}")

    env = env or "dev"
    candidate = root / "configs" / env / "server.yaml"
    if candidate.exists():
        return candidate
    raise ConfigError(f"Config file not found: {candidate}")


_ENV_OVERRIDE_PREFIX = "LIVACHAT__"


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply ``LIVACHAT__``-prefixed env vars as overrides on top of *data*.

    ``LIVACHAT__ASR__ACTIVE=deepgram`` → ``data["asr"]["active"] = "deepgram"``
    """
    import copy

    result = copy.deepcopy(data)

    for key, value in os.environ.items():
        if not key.startswith(_ENV_OVERRIDE_PREFIX):
            continue
        path = key[len(_ENV_OVERRIDE_PREFIX) :].lower().split("__")
        _set_nested(result, path, value)

    return result


def _set_nested(d: dict[str, Any], path: list[str], value: str) -> None:
    """Set a deeply nested key in *d*, creating intermediate dicts as needed."""
    for segment in path[:-1]:
        if segment not in d or not isinstance(d[segment], dict):
            d[segment] = {}
        d = d[segment]
    # Try to coerce the value to match expected types
    existing = d.get(path[-1])
    if isinstance(existing, bool):
        d[path[-1]] = value.lower() in ("true", "1", "yes")
    elif isinstance(existing, int):
        d[path[-1]] = int(value)
    elif isinstance(existing, float):
        d[path[-1]] = float(value)
    else:
        d[path[-1]] = value


def resolve_path(path: str) -> Path:
    """将相对于项目根的路径转为绝对路径。绝对路径原样返回。"""
    p = Path(path)
    if p.is_absolute():
        return p
    return (_project_root() / p).resolve()


def load_config(env: str | None = None) -> AppConfig:
    """Load and validate the application configuration.

    0. Load repo-root ``.env`` into the environment (setdefault; shell wins).
    1. Locate the YAML file for ``env`` (defaults to ``dev``).
    2. Resolve ``${ENV_VAR}`` placeholders from the environment.
    3. Apply ``LIVACHAT__``-prefixed env vars as overrides.
    4. Parse into :class:`AppConfig`.
    5. Run fail-fast validation.
    """
    _load_dotenv()
    config_path = _find_config_path(env)
    with open(config_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    raw = _resolve_placeholders(raw)
    raw = _apply_env_overrides(raw)

    cfg = AppConfig(**raw)
    validate_config(cfg)
    return cfg
