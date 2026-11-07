from __future__ import annotations

from typing import Any

from asr.base import AsrProvider
from asr.providers.alibaba_bailian import AlibabaBailianProvider
from asr.providers.alibaba_qwen_asr import AlibabaQwenASRProvider
from asr.providers.deepgram import DeepgramProvider
from asr.providers.elevenlabs import ElevenLabsProvider
from asr.service import AsrService
from shared.config.models import AsrConfig
from shared.utils.errors import ConfigError

_PROVIDER_CLASSES: dict[str, type[AsrProvider]] = {
    "deepgram": DeepgramProvider,
    "elevenlabs": ElevenLabsProvider,
    "alibaba_bailian": AlibabaBailianProvider,
    "alibaba_qwen_asr": AlibabaQwenASRProvider,
}


def _create_provider(name: str, cfg: dict[str, Any]) -> AsrProvider:
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ConfigError(f"unknown asr provider: {name}")

    if name == "deepgram":
        return DeepgramProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            params=cfg.get("params"),
        )
    elif name == "elevenlabs":
        return ElevenLabsProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
        )
    elif name == "alibaba_bailian":
        return AlibabaBailianProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            params=cfg.get("params"),
            workspace_id=cfg.get("workspace_id"),
        )
    elif name == "alibaba_qwen_asr":
        return AlibabaQwenASRProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            params=cfg.get("params"),
        )
    raise ConfigError(f"unknown asr provider: {name}")


def create_asr(config: AsrConfig) -> AsrService:
    """Build an :class:`AsrService` from configuration.

    Returns a failover-ready service with ``active`` and ``fallback`` providers.
    """
    providers = config.providers
    if config.active not in providers:
        raise ConfigError(f"unknown asr provider: {config.active}")
    active = _create_provider(config.active, providers[config.active].model_dump())
    fallbacks = [_create_provider(name, providers[name].model_dump()) for name in config.fallback]

    return AsrService(active=active, fallbacks=fallbacks)
