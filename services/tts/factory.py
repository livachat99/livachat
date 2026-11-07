from __future__ import annotations

from typing import Any

from shared.config.models import TtsConfig
from shared.utils.errors import ConfigError
from tts.providers.alibaba_bailian import AlibabaBailianTTSProvider
from tts.providers.alibaba_qwen_tts import AlibabaQwenTTSProvider
from tts.providers.cartesia import CartesiaProvider
from tts.providers.deepgram import DeepgramTTSProvider
from tts.providers.elevenlabs import ElevenLabsTTSProvider
from tts.service import TtsService


def _create_provider(name: str, cfg: dict[str, Any]) -> Any:
    if name == "cartesia":
        return CartesiaProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            voice=cfg.get("voice", ""),
            output_format=cfg.get("output_format"),
            version=cfg.get("version", "2024-06-10"),
        )
    elif name == "elevenlabs":
        return ElevenLabsTTSProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            voice=cfg.get("voice", ""),
            output_format=cfg.get("output_format", "pcm_16000"),
        )
    elif name == "alibaba_bailian":
        return AlibabaBailianTTSProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            voice=cfg.get("voice", ""),
            workspace_id=cfg.get("workspace_id"),
        )
    elif name == "alibaba_qwen_tts":
        return AlibabaQwenTTSProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            voice=cfg.get("voice", ""),
        )
    elif name == "deepgram":
        return DeepgramTTSProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
        )
    raise ConfigError(f"unknown tts provider: {name}")


def create_tts(config: TtsConfig) -> TtsService:
    providers = config.providers
    if config.active not in providers:
        raise ConfigError(f"unknown tts provider: {config.active}")
    active = _create_provider(config.active, providers[config.active].model_dump())
    fallbacks = [_create_provider(name, providers[name].model_dump()) for name in config.fallback]
    return TtsService(active=active, fallbacks=fallbacks)
