from __future__ import annotations

from typing import Any

from llm.providers.alibaba_bailian import AlibabaBailianLLMProvider
from llm.providers.deepseek import DeepSeekProvider
from llm.providers.openai_compat import OpenAICompatProvider
from llm.providers.openrouter import OpenRouterProvider
from llm.service import LlmService
from shared.config.models import LlmConfig
from shared.utils.errors import ConfigError


def _create_provider(name: str, cfg: dict[str, Any]) -> OpenAICompatProvider:
    extra_body = cfg.get("extra_body")
    if name == "alibaba_bailian":
        return AlibabaBailianLLMProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            vision_model=cfg.get("vision_model"),
            extra_body=extra_body,
            workspace_id=cfg.get("workspace_id"),
        )
    elif name == "openrouter":
        return OpenRouterProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            extra_body=extra_body,
        )
    elif name == "deepseek":
        return DeepSeekProvider(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=cfg["model"],
            extra_body=extra_body,
        )
    raise ConfigError(f"unknown llm provider: {name}")


def create_llm(config: LlmConfig) -> LlmService:
    providers = config.providers
    if config.active not in providers:
        raise ConfigError(f"unknown llm provider: {config.active}")
    active = _create_provider(config.active, providers[config.active].model_dump())
    fallbacks = [_create_provider(name, providers[name].model_dump()) for name in config.fallback]
    return LlmService(active=active, fallbacks=fallbacks)  # type: ignore[arg-type]
