from __future__ import annotations

import pytest

from asr.factory import create_asr
from asr.service import AsrService
from shared.config.models import AsrConfig, AsrProviderConfig
from shared.utils.errors import ConfigError


def _make_asr_config(active: str, providers: dict) -> AsrConfig:
    return AsrConfig(active=active, fallback=[], providers=providers)


class TestFactory:
    def test_create_deepgram(self):
        cfg = _make_asr_config(
            "deepgram",
            {"deepgram": AsrProviderConfig(enabled=True, base_url="wss://test/v1/listen", api_key="k", model="nova-2")},
        )
        svc = create_asr(cfg)
        assert isinstance(svc, AsrService)

    def test_bailian_workspace_id_passed_through(self):
        """workspace_id 必须从 config 透传到 provider——漏传即静默丢头
        （provider 层测试全绿而功能失效，与 pydantic extra=ignore 同款静默模式）。"""
        cfg = _make_asr_config(
            "alibaba_bailian",
            {
                "alibaba_bailian": AsrProviderConfig(
                    enabled=True,
                    base_url="wss://test/api-ws/v1/inference",
                    api_key="k",
                    model="qwen-audio-3.0-asr-flash-streaming",
                    workspace_id="ws-123",
                )
            },
        )
        svc = create_asr(cfg)
        provider = svc._providers[0]
        assert provider._workspace_id == "ws-123"
        assert provider._headers()["X-DashScope-WorkSpace"] == "ws-123"

    def test_unknown_provider_raises(self):
        cfg = _make_asr_config("foo", {})
        with pytest.raises(ConfigError, match="unknown asr provider"):
            create_asr(cfg)
