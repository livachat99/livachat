from __future__ import annotations

import pytest

from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.adapters.loader import load_adapter, load_all
from agent_relay.adapters.openclaw import OpenClawAdapter
from agent_relay.config import AgentRelayConfig


class TestLoadAdapter:
    def test_load_hermes(self):
        cfg = AgentRelayConfig()
        adapter = load_adapter("hermes", cfg)
        assert isinstance(adapter, HermesAdapter)
        assert adapter.name == "hermes"

    def test_load_openclaw(self):
        cfg = AgentRelayConfig()
        adapter = load_adapter("openclaw", cfg)
        assert isinstance(adapter, OpenClawAdapter)
        assert adapter.name == "openclaw"

    def test_unknown_adapter(self):
        cfg = AgentRelayConfig()
        with pytest.raises(KeyError, match="nonexistent"):
            load_adapter("nonexistent", cfg)


class TestLoadAll:
    def test_loads_default_adapters(self):
        cfg = AgentRelayConfig()
        cfg.default_adapters = ["hermes"]
        adapters = load_all(cfg)
        assert "hermes" in adapters
        assert isinstance(adapters["hermes"], HermesAdapter)

    def test_skips_unknown(self):
        cfg = AgentRelayConfig()
        cfg.default_adapters = ["hermes", "nonexistent"]
        adapters = load_all(cfg)
        assert len(adapters) == 1
        assert "hermes" in adapters

    def test_empty_default_adapters(self):
        cfg = AgentRelayConfig()
        cfg.default_adapters = []
        adapters = load_all(cfg)
        assert adapters == {}

    def test_config_passed_to_adapter(self, monkeypatch):
        monkeypatch.setenv("LIVACHAT__AGENT_RELAY__ADAPTERS__HERMES__TIMEOUT_S", "42")
        cfg = AgentRelayConfig()
        adapter = load_adapter("hermes", cfg)
        assert isinstance(adapter, HermesAdapter)
        assert adapter._cfg.timeout_s == 42
