from __future__ import annotations

from typing import Any

from omni.service import OmniService
from shared.config.models import OmniConfig
from shared.utils.errors import ConfigError


def _create_provider(name: str, cfg: dict[str, Any]) -> Any:
    if name == "alibaba_omni":
        from omni.providers.alibaba_omni import AlibabaOmniProvider

        return AlibabaOmniProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            voice=cfg.get("voice") or "",
            workspace_id=cfg.get("workspace_id"),
        )
    elif name == "openai_realtime":
        from omni.providers.openai_realtime import OpenAIRealtimeProvider

        return OpenAIRealtimeProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            voice=cfg.get("voice") or "",
        )
    raise ConfigError(f"unknown omni provider: {name}")


def create_omni(config: OmniConfig) -> OmniService:
    providers = config.providers
    if config.active not in providers:
        raise ConfigError(f"unknown omni provider: {config.active}")
    active = _create_provider(config.active, providers[config.active].model_dump())
    fallbacks = []
    for n in config.fallback:
        if n not in providers:
            raise ConfigError(f"unknown omni provider: {n}")
        fallbacks.append(_create_provider(n, providers[n].model_dump()))
    return OmniService(active=active, fallbacks=fallbacks)
