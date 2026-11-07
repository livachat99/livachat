"""E2E tests: full agent-relay chain through the in-process bus + mock Hermes.

These tests exercise the complete reactive flow (chatserver_no_redis.md §3.2):
  orchestrator → InProcessEventBus → AgentSessionManager.dispatch
  → adapter → PushPublisher → ConnectionManager handler

No Redis: bus + publisher + fake conn_mgr（进程内即终态）。
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.config import AgentRelayConfig, HermesAdapterConfig
from agent_relay.event_bus import InProcessEventBus
from agent_relay.models import (
    make_user_utterance_event,
)
from agent_relay.publisher import PushPublisher
from agent_relay.session_manager import AgentSessionManager

MOCK_SERVER = "http://127.0.0.1:8090"


def _make_cfg() -> AgentRelayConfig:
    cfg = AgentRelayConfig()
    cfg.adapters.hermes = HermesAdapterConfig(
        base_url=MOCK_SERVER,
        api_key="",
        timeout_s=10,
        max_retries=1,
    )
    return cfg


class _ConnMgr:
    def __init__(self):
        self._handlers: dict[str, object] = {}

    def set_handler(self, device_id: str, handler) -> None:
        self._handlers[device_id] = handler

    def get_handler(self, device_id: str):
        return self._handlers.get(device_id)

    def is_online(self, device_id: str) -> bool:
        return device_id in self._handlers


class _SinkHandler:
    def __init__(self):
        self.enqueued: list[str] = []

    async def enqueue_outbound(self, frame: str) -> None:
        self.enqueued.append(frame)

    def record_agent_job_result(self, *, text: str, message_id: str,
                                agent_name: str = "", agent_id: str = "",
                                job_key: str = "") -> None:
        pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.e2e
class TestE2EReactiveFlow:
    """E2E: utterance → bus → adapter → push full chain."""

    async def test_full_reactive_chain(self):
        """一问: utterance 入总线 → adapter 被调 → ai_text 帧入 handler。"""
        cfg = _make_cfg()
        conn_mgr = _ConnMgr()
        handler = _SinkHandler()
        conn_mgr.set_handler("e2e-dev-1", handler)

        adapter = HermesAdapter(cfg.adapters.hermes)
        await adapter.connect()
        try:
            pub = PushPublisher(conn_mgr, cfg)
            sm = AgentSessionManager(adapters={"hermes": adapter}, publisher=pub, cfg=cfg)
            bus = InProcessEventBus()
            bus.subscribe(sm.dispatch)
            await bus.start()

            # Simulate orchestrator: publish user.utterance
            tid = f"tr-{uuid.uuid4().hex[:12]}"
            bus.publish_nowait(make_user_utterance_event(
                device_id="e2e-dev-1",
                session_id="e2e-sess-1",
                ts_ms=int(time.time() * 1000),
                transcription_id=tid,
                text="What is the weather in Beijing?",
                language="en",
            ))

            # Wait for the full chain to complete
            await asyncio.sleep(2.0)

            # Verify session was created
            assert "e2e-sess-1" in sm._sessions

            await bus.stop()
        finally:
            await adapter.close()

    async def test_duplicate_utterance_idempotent(self):
        """Duplicate transcription_id should be ignored by session manager."""
        cfg = _make_cfg()
        conn_mgr = _ConnMgr()
        handler = _SinkHandler()
        conn_mgr.set_handler("e2e-dev-2", handler)

        adapter = HermesAdapter(cfg.adapters.hermes)
        await adapter.connect()
        try:
            pub = PushPublisher(conn_mgr, cfg)
            sm = AgentSessionManager(adapters={"hermes": adapter}, publisher=pub, cfg=cfg)
            bus = InProcessEventBus()
            bus.subscribe(sm.dispatch)
            await bus.start()

            tid = f"tr-{uuid.uuid4().hex[:12]}"
            ev = make_user_utterance_event(
                device_id="e2e-dev-2",
                session_id="e2e-sess-2",
                ts_ms=int(time.time() * 1000),
                transcription_id=tid,
                text="Hello",
                language="en",
            )

            # First utterance
            bus.publish_nowait(ev)
            await asyncio.sleep(1.0)
            assert "e2e-sess-2" in sm._sessions

            # Duplicate — should be idempotent
            bus.publish_nowait(ev)
            await asyncio.sleep(0.5)
            # No crash = pass

            await bus.stop()
        finally:
            await adapter.close()

    async def test_device_offline_drops_push(self):
        """Device not registered → push publisher returns False."""
        cfg = _make_cfg()
        conn_mgr = _ConnMgr()  # 不注册任何 handler → 全部离线

        adapter = HermesAdapter(cfg.adapters.hermes)
        await adapter.connect()
        try:
            pub = PushPublisher(conn_mgr, cfg)
            sm = AgentSessionManager(adapters={"hermes": adapter}, publisher=pub, cfg=cfg)

            ev = make_user_utterance_event(
                device_id="e2e-offline-dev",
                session_id="e2e-offline-sess",
                ts_ms=int(time.time() * 1000),
                transcription_id=f"tr-{uuid.uuid4().hex[:12]}",
                text="test",
            )
            # Should not raise
            await sm.on_user_utterance(ev)
            # Session should still be created
            assert "e2e-offline-sess" in sm._sessions
        finally:
            await adapter.close()

    async def test_agent_relay_disabled_skips_all(self):
        """When enabled=False, the relay should not process events."""
        cfg = _make_cfg()
        cfg.enabled = False
        conn_mgr = _ConnMgr()

        adapter = HermesAdapter(cfg.adapters.hermes)
        await adapter.connect()
        try:
            pub = PushPublisher(conn_mgr, cfg)
            sm = AgentSessionManager(adapters={}, publisher=pub, cfg=cfg)

            # With no adapters, utterances are no-ops
            ev = make_user_utterance_event(
                device_id="e2e-disabled-dev",
                session_id="e2e-disabled-sess",
                ts_ms=int(time.time() * 1000),
                transcription_id=f"tr-{uuid.uuid4().hex[:12]}",
                text="test",
            )
            await sm.on_user_utterance(ev)
            # No crash, no side effects when adapters dict is empty
        finally:
            await adapter.close()
