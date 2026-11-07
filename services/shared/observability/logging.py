from __future__ import annotations

import logging
import logging.handlers
import sys
from contextvars import ContextVar
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog

from shared.config.loader import resolve_path
from shared.config.models import LogFileConfig

# Context variables that are automatically bound to every log line when set.
session_id_ctx: ContextVar[str | None] = ContextVar("session_id", default=None)
device_id_ctx: ContextVar[str | None] = ContextVar("device_id", default=None)
seq_ctx: ContextVar[int | None] = ContextVar("seq", default=None)
epoch_ctx: ContextVar[int | None] = ContextVar("epoch", default=None)

# Keys whose values should be redacted from log output.
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "dev_token",
        "jwt_secret",
        "password",
        "dsn",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "authorization",
    }
)
# Keys whose string values are URLs; query parameters are scrubbed.
_URL_KEYS = frozenset({"url", "urls", "uri", "endpoint", "base_url", "ws_url"})
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "password",
        "auth",
        "authorization",
        "signature",
        "sig",
    }
)


def redact_url(url: str) -> str:
    """Redact sensitive query parameter values from a URL for logging.

    Non-sensitive query parameters and the URL structure are preserved so logs
    remain useful for diagnosis.
    """
    if not isinstance(url, str) or "?" not in url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        # Malformed URL: never risk echoing a query string.
        return url.split("?", 1)[0] + "?[redacted]"
    if not parts.query:
        return url

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted = False
    safe_pairs: list[tuple[str, str]] = []
    for key, value in pairs:
        lower = key.lower()
        if lower in _SENSITIVE_QUERY_KEYS or lower.endswith(("_token", "_secret", "_password", "_key")):
            safe_pairs.append((key, "[redacted]"))
            redacted = True
        else:
            safe_pairs.append((key, value))
    if not redacted:
        return url
    return urlunsplit(parts._replace(query=urlencode(safe_pairs)))


def _redact_value(key: Any, value: Any) -> Any:
    lower = key.lower() if isinstance(key, str) else ""
    if lower in _SENSITIVE_KEYS:
        return "[redacted]"
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, str) and (lower in _URL_KEYS or lower.endswith(("_url", "_uri"))):
        return redact_url(value)
    return value


def _redact_sensitive(_: Any, __: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Replace sensitive values, including URL query strings, with '[redacted]'."""
    for key, value in list(event_dict.items()):
        event_dict[key] = _redact_value(key, value)
    return event_dict


def _inject_contextvars(_: Any, __: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Attach bound context vars (session_id, device_id, seq, epoch) to the event."""
    sid = session_id_ctx.get()
    if sid is not None:
        event_dict["session_id"] = sid
    did = device_id_ctx.get()
    if did is not None:
        event_dict["device_id"] = did
    s = seq_ctx.get()
    if s is not None:
        event_dict["seq"] = s
    ep = epoch_ctx.get()
    if ep is not None:
        event_dict["epoch"] = ep
    return event_dict


def _timestamp_first(_: Any, __: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Move timestamp to the front so time-ordered logs are easy to scan."""
    if "timestamp" in event_dict:
        ts = event_dict.pop("timestamp")
        return {"timestamp": ts, **event_dict}
    return event_dict


def setup_logging(*, json_logs: bool = True, log_level: str = "INFO", file: LogFileConfig | None = None) -> None:
    """Configure structlog for the application.

    Set ``json_logs=False`` to switch to a coloured console renderer (dev-friendly).
    ``file`` 非空且 enabled → **仅**进程内 RotatingFileHandler（运行态只写文件，docs/server_logging.md
    §8：默认 <project_root>/logs/livachat.log，50MB×3 份；目录不可用 fail-fast）；
    未激活（None / disabled）→ stdout 兜底（§8 D1）。
    """
    shared_processors: list[Any] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _inject_contextvars,
        _redact_sensitive,
        _timestamp_first,
    ]

    if json_logs:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    if file is not None and file.enabled:
        # 运行态：仅写文件（docs/server_logging.md §8——进程不再输出 stdout）
        log_dir = resolve_path(file.dir or "logs")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                log_dir / "livachat.log", maxBytes=file.max_bytes, backupCount=file.backup_count, encoding="utf-8"
            )
        except OSError as exc:
            raise RuntimeError(f"log dir not usable: {log_dir} ({exc})") from exc
        handler.setFormatter(formatter)
        handlers: list[logging.Handler] = [handler]
    else:
        # file 未激活（测试 / 显式 disabled）→ stdout 兜底（§8 D1），保证日志可见
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handlers = [handler]

    root = logging.getLogger()
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Quiet noisy libs — pin to WARNING so that setting root to DEBUG
    # does not flood logs with per-frame binary dumps.
    for noisy in ("uvicorn", "uvicorn.access", "websockets", "websockets.client"):
        lg = logging.getLogger(noisy)
        lg.handlers.clear()
        for h in handlers:
            lg.addHandler(h)
        lg.setLevel(logging.WARNING)
        lg.propagate = False


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name or "livachat")
