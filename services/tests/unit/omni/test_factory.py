from __future__ import annotations

import pytest

from omni.factory import create_omni
from omni.providers.openai_realtime import OpenAIRealtimeProvider
from shared.config.models import OmniConfig, OmniProviderConfig
from shared.utils.errors import ConfigError


class TestCreateOmni:
    def test_active_unknown_raises_config_error(self) -> None:
        cfg = OmniConfig(active="nonexistent", providers={})
        with pytest.raises(ConfigError, match="unknown omni provider"):
            create_omni(cfg)

    def test_active_resolved(self) -> None:
        cfg = OmniConfig(
            active="alibaba_omni",
            providers={
                "alibaba_omni": OmniProviderConfig(
                    base_url="wss://x", api_key="k", model="m", voice="v"
                ),
            },
        )
        svc = create_omni(cfg)
        assert svc is not None

    def test_fallback_list_parsed(self) -> None:
        cfg = OmniConfig(
            active="alibaba_omni",
            fallback=["openai_realtime"],
            providers={
                "alibaba_omni": OmniProviderConfig(
                    base_url="wss://x", api_key="k", model="m", voice="v"
                ),
                "openai_realtime": OmniProviderConfig(
                    base_url="wss://y", api_key="k2", model="m2", voice="v2"
                ),
            },
        )
        svc = create_omni(cfg)
        assert svc is not None

    def test_openai_realtime_instantiable(self) -> None:
        """openai_realtime can be instantiated with correct properties."""
        p = OpenAIRealtimeProvider(
            base_url="wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview",
            api_key="sk-test",
            model="gpt-4o-realtime-preview",
            voice="alloy",
        )
        assert p.name == "openai_realtime"
        assert p.supports_vision is False

    def test_unknown_fallback_raises_config_error(self) -> None:
        cfg = OmniConfig(
            active="alibaba_omni",
            fallback=["unknown_provider"],
            providers={
                "alibaba_omni": OmniProviderConfig(
                    base_url="wss://x", api_key="k", model="m"
                ),
            },
        )
        with pytest.raises(ConfigError, match="unknown omni provider"):
            create_omni(cfg)
