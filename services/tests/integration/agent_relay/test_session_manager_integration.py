"""Integration tests: SessionManager with the in-process event bus."""

from __future__ import annotations

import asyncio
import time

from agent_relay.config import AgentRelayConfig, HermesAdapterConfig
from agent_relay.models import (
    make_session_started_event,
)
from agent_relay.publisher import PushPublisher
from agent_relay.session_manager import AgentSessionManager


class _ConnMgr:
    def __init__(self):
        self._handlers: dict[str, object] = {}

    def set_handler(self, device_id: str, handler) -> None:
        self._handlers[device_id] = handler

    def get_handler(self, device_id: str):
        return self._handlers.get(device_id)

    def is_online(self, device_id: str) -> bool:
        return device_id in self._handlers


_CONN_MGR = _ConnMgr()


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    """The session manager dispatches adapter calls via asyncio.create_task;
    tests must wait for those tasks instead of asserting synchronously."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def _make_cfg(mock_hermes) -> AgentRelayConfig:
    cfg = AgentRelayConfig()
    cfg.events_channel = "livachat_test:agent:events"
    cfg.push_channel_prefix = "livachat_test:push"
    cfg.adapters.hermes = HermesAdapterConfig(
        base_url=mock_hermes.url,
        api_key="",
        timeout_s=10,
        max_retries=1,
        mode="reactive",
    )
    return cfg



class TestInProcessBusIntegration:
    """InProcessEventBus → AgentSessionManager.dispatch（§3.2.1）。"""

    async def test_publish_and_dispatch(self, mock_hermes):
        """publish_nowait 一个 session.started 事件 → dispatch 路由。"""
        from agent_relay.adapters.base import AgentAdapter
        from agent_relay.event_bus import InProcessEventBus

        class _StubAdapter(AgentAdapter):
            name = "mock"
            async def ask(self, text, context):
                if False:
                    yield

        cfg = _make_cfg(mock_hermes)
        pub = PushPublisher(_CONN_MGR, cfg)
        sm = AgentSessionManager(adapters={"mock": _StubAdapter()}, publisher=pub, cfg=cfg)
        bus = InProcessEventBus()
        bus.subscribe(sm.dispatch)
        await bus.start()

        bus.publish_nowait(make_session_started_event(
            device_id="pub-dev-1", session_id="pub-sess-1", ts_ms=int(time.time() * 1000)))
        assert await _wait_until(lambda: "pub-sess-1" in sm._sessions)

        await bus.stop()

    async def test_bus_restart_keeps_delivery(self, mock_hermes):
        """stop 后重启 bus：事件仍可投递（进程内无订阅丢失窗口）。"""
        from agent_relay.adapters.base import AgentAdapter
        from agent_relay.event_bus import InProcessEventBus

        class _StubAdapter(AgentAdapter):
            name = "mock"
            async def ask(self, text, context):
                if False:
                    yield

        cfg = _make_cfg(mock_hermes)
        pub = PushPublisher(_CONN_MGR, cfg)
        sm = AgentSessionManager(adapters={"mock": _StubAdapter()}, publisher=pub, cfg=cfg)
        bus = InProcessEventBus()
        bus.subscribe(sm.dispatch)
        await bus.start()

        bus.publish_nowait(make_session_started_event(
            device_id="reconn-dev", session_id="reconn-sess", ts_ms=int(time.time() * 1000)))
        assert await _wait_until(lambda: "reconn-sess" in sm._sessions)

        await bus.stop()
        await bus.start()
        bus.publish_nowait(make_session_started_event(
            device_id="reconn-dev-2", session_id="reconn-sess-2", ts_ms=int(time.time() * 1000)))
        assert await _wait_until(lambda: "reconn-sess-2" in sm._sessions)

        await bus.stop()
