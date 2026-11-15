from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from shared.config.models import (
    AppConfig,
    AsrConfig,
    LlmConfig,
    TtsConfig,
)
from shared.utils.errors import ConfigError

_VALID_SAMPLE_RATES = {16000, 24000}
_VALID_OPUS_FRAME_MS = {20, 40}


def validate_config(cfg: AppConfig) -> None:
    """Run fail-fast semantic checks on a populated AppConfig.

    Raises :class:`ConfigError` on the first violation.
    """
    _validate_auth(cfg)
    _validate_media(cfg)
    _validate_ssl(cfg)
    _validate_asr(cfg.asr)
    _validate_llm(cfg.llm)
    _validate_tts(cfg.tts)
    _validate_meeting(cfg)


def _validate_auth(cfg: AppConfig) -> None:
    if cfg.auth.mode == "dev":
        if not cfg.auth.dev_token:
            raise ConfigError("auth.dev_token must not be empty when auth.mode='dev'")
    elif cfg.auth.mode == "jwt":
        if not cfg.auth.jwt_secret:
            raise ConfigError("auth.jwt_secret must not be empty when auth.mode='jwt'")
    else:
        raise ConfigError(f"auth.mode must be 'dev' or 'jwt', got '{cfg.auth.mode}'")


def _validate_media(cfg: AppConfig) -> None:
    sr = cfg.media.audio.default_sample_rate
    if sr not in _VALID_SAMPLE_RATES:
        raise ConfigError(f"media.audio.default_sample_rate must be one of {_VALID_SAMPLE_RATES}, got {sr}")
    frame_ms = cfg.media.audio.opus_frame_ms
    if frame_ms not in _VALID_OPUS_FRAME_MS:
        raise ConfigError(f"media.audio.opus_frame_ms must be one of {_VALID_OPUS_FRAME_MS}, got {frame_ms}")
    # The Opus wrapper's PCM buffers are mono; rejecting multi-channel here
    # turns a heap OOB (encoder read / decoder write) into a startup error.
    channels = cfg.media.audio.channels
    if channels != 1:
        raise ConfigError(f"media.audio.channels must be 1 (mono), got {channels}")


def _validate_provider_active(
    active: str,
    fallback: list[str],
    providers: dict[str, Any],
    section: str,
) -> None:
    if active not in providers:
        raise ConfigError(f"{section}.active='{active}' references a provider not defined in {section}.providers")
    if not providers[active].enabled:
        raise ConfigError(f"{section}.active='{active}' references a disabled provider")
    for name in fallback:
        if name not in providers:
            raise ConfigError(f"{section}.fallback contains '{name}' which is not defined in {section}.providers")
        if not providers[name].enabled:
            raise ConfigError(f"{section}.fallback contains '{name}' which is disabled")


def _validate_asr(cfg: AsrConfig) -> None:
    _validate_provider_active(cfg.active, cfg.fallback, cfg.providers, "asr")


def _validate_llm(cfg: LlmConfig) -> None:
    _validate_provider_active(cfg.active, cfg.fallback, cfg.providers, "llm")


def _validate_tts(cfg: TtsConfig) -> None:
    _validate_provider_active(cfg.active, cfg.fallback, cfg.providers, "tts")


def _validate_ssl(cfg: AppConfig) -> None:
    ssl_cfg = cfg.server.ssl
    if not ssl_cfg.enabled:
        return

    if not ssl_cfg.certfile:
        raise ConfigError("server.ssl.certfile must not be empty when server.ssl.enabled=true")
    if not ssl_cfg.keyfile:
        raise ConfigError("server.ssl.keyfile must not be empty when server.ssl.enabled=true")

    if ssl_cfg.cert_reqs in ("optional", "required") and not ssl_cfg.cafile:
        raise ConfigError(
            "server.ssl.cafile must be set when server.ssl.cert_reqs is 'optional' or 'required'"
        )


def _validate_meeting(cfg: AppConfig) -> None:
    """会议配置 fail-fast（docs/livachat_meeting.md §6.1）。"""
    meeting = cfg.meeting
    if not meeting.enabled:
        return

    if not cfg.bff.enabled:
        raise ConfigError("meeting.enabled requires bff.enabled=true")

    addr = meeting.external_server_addr.strip()
    if not addr:
        raise ConfigError("meeting.external_server_addr must not be empty")
    if "\\" in addr or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7f for ch in addr):
        raise ConfigError("meeting.external_server_addr contains invalid characters")
    try:
        parsed = urlsplit(addr)
        host = parsed.hostname
        parsed.port  # 非法端口在 .port 上抛 ValueError
    except ValueError as exc:
        raise ConfigError("meeting.external_server_addr is not a valid URL") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("meeting.external_server_addr must not contain userinfo")
    if parsed.scheme not in ("http", "https") or not host:
        raise ConfigError("meeting.external_server_addr must be http(s)://host[:port]")
    if parsed.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        raise ConfigError(
            "meeting.external_server_addr must use https:// except localhost")
    if parsed.path not in ("",) or parsed.query or parsed.fragment:
        raise ConfigError("meeting.external_server_addr must not contain a path/query/fragment")

    if not meeting.livekit_key.strip():
        raise ConfigError("meeting.livekit_key must not be empty")
    if not meeting.livekit_secret.strip():
        raise ConfigError("meeting.livekit_secret must not be empty")

    # wss:// is mandatory even for localhost/dev: WebRTC does not work over ws://.
    lk_raw = meeting.livekit_server
    if not lk_raw.startswith("wss://"):
        raise ConfigError("meeting.livekit_server must start with wss://")
    if "\\" in lk_raw or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7f for ch in lk_raw):
        raise ConfigError("meeting.livekit_server contains invalid characters")
    try:
        lk = urlsplit(lk_raw)
        lk_host = lk.hostname
        lk.port  # 非法端口在 .port 上抛 ValueError
    except ValueError as exc:
        raise ConfigError("meeting.livekit_server is not a valid URL") from exc
    if lk.username is not None or lk.password is not None:
        raise ConfigError("meeting.livekit_server must not contain userinfo")
    if lk.scheme != "wss" or not lk_host:
        raise ConfigError("meeting.livekit_server must be wss://host[:port]")

    if not meeting.web_dir.strip():
        raise ConfigError("meeting.web_dir must not be empty")
    # 函数内 import：loader 在模块顶层 import validation，顶层反向 import 会成环。
    from shared.config.loader import resolve_path

    root = resolve_path(meeting.web_dir.strip())
    if not (root / "index.html").is_file():
        raise ConfigError(
            f"meeting.web_dir has no index.html: {root}; build apps/web/meeting and point web_dir at dist"
        )
