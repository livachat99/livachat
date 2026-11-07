from __future__ import annotations

import pytest

from avdetector.factory import create_avdetector
from shared.config.models import AvDetectorConfig, AvDetectorProviderConfig
from shared.utils.errors import ConfigError


class TestCreateAvDetector:
    def test_active_unknown_raises_config_error(self) -> None:
        cfg = AvDetectorConfig(active="nonexistent", providers={})
        with pytest.raises(ConfigError, match="unknown avdetector provider"):
            create_avdetector(cfg)

    def test_active_resolved(self) -> None:
        cfg = AvDetectorConfig(
            active="alibaba_avdetector",
            providers={
                "alibaba_avdetector": AvDetectorProviderConfig(
                    base_url="wss://x", api_key="k", model="m"
                ),
            },
        )
        svc = create_avdetector(cfg)
        assert svc is not None

    def test_fallback_list_parsed(self) -> None:
        cfg = AvDetectorConfig(
            active="alibaba_avdetector",
            fallback=["alibaba_avdetector"],
            providers={
                "alibaba_avdetector": AvDetectorProviderConfig(
                    base_url="wss://x", api_key="k", model="m"
                ),
            },
        )
        svc = create_avdetector(cfg)
        assert svc is not None

    def test_unknown_fallback_raises_config_error(self) -> None:
        cfg = AvDetectorConfig(
            active="alibaba_avdetector",
            fallback=["unknown_provider"],
            providers={
                "alibaba_avdetector": AvDetectorProviderConfig(
                    base_url="wss://x", api_key="k", model="m"
                ),
            },
        )
        with pytest.raises(ConfigError, match="unknown avdetector provider"):
            create_avdetector(cfg)
