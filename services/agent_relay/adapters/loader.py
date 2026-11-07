"""Adapter factory: maps adapter names to AgentAdapter instances."""

from __future__ import annotations

from agent_relay.adapters.base import AgentAdapter
from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.adapters.openclaw import OpenClawAdapter
from agent_relay.config import AgentRelayConfig

_REGISTRY: dict[str, type[AgentAdapter]] = {
    "hermes": HermesAdapter,
    "openclaw": OpenClawAdapter,
}


def load_adapter(name: str, config: AgentRelayConfig) -> AgentAdapter:
    """Instantiate a single adapter by name with its config section."""
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"unknown adapter: {name!r}")

    if name == "hermes":
        return cls(config.adapters.hermes)
    elif name == "openclaw":
        return cls(config.adapters.openclaw)
    raise KeyError(f"no config mapping for adapter: {name!r}")


def load_all(config: AgentRelayConfig) -> dict[str, AgentAdapter]:
    """Instantiate all adapters enabled in default_adapters."""
    adapters: dict[str, AgentAdapter] = {}
    for name in config.default_adapters:
        try:
            adapters[name] = load_adapter(name, config)
        except KeyError:
            pass
    return adapters
