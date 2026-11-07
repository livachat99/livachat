from __future__ import annotations

from typing import Any

from avdetector.service import AvDetectorService
from shared.config.models import AvDetectorConfig
from shared.utils.errors import ConfigError


def _create_provider(name: str, cfg: dict[str, Any]) -> Any:
    if name == "alibaba_avdetector":
        from avdetector.providers.alibaba_avdetector import AlibabaAvDetectorProvider

        return AlibabaAvDetectorProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            workspace_id=cfg.get("workspace_id"),
        )
    raise ConfigError(f"unknown avdetector provider: {name}")


def create_avdetector(config: AvDetectorConfig) -> AvDetectorService:
    providers = config.providers
    if config.active not in providers:
        raise ConfigError(f"unknown avdetector provider: {config.active}")
    active = _create_provider(config.active, providers[config.active].model_dump())
    fallbacks = []
    for n in config.fallback:
        if n not in providers:
            raise ConfigError(f"unknown avdetector provider: {n}")
        fallbacks.append(_create_provider(n, providers[n].model_dump()))
    return AvDetectorService(active=active, fallbacks=fallbacks)
