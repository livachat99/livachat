from __future__ import annotations

from typing import Any

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
