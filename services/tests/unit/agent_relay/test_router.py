from __future__ import annotations

import asyncio

import pytest

from agent_relay.config import AgentRelayConfig
from agent_relay.event_bus import InProcessEventBus
from agent_relay.models import (
    make_recording_ready_event,
    make_session_ended_event,
    make_session_started_event,
    make_user_utterance_event,
)
from agent_relay.publisher import PushPublisher
from agent_relay.session_manager import AgentSessionManager


@pytest.fixture
def cfg():
    return AgentRelayConfig()


@pytest.fixture
def sm(cfg):
    from unittest.mock import MagicMock

    adapter = MagicMock()
    adapter.name = "mock"
    adapter.proactive_enabled = False
    adapter.reactive_enabled = False
    pub = PushPublisher(MagicMock(), cfg)
    return AgentSessionManager(adapters={"mock": adapter}, publisher=pub, cfg=cfg)


class TestAgentSessionManagerDispatch:
    """AgentSessionManager.dispatch（原 EventRouter._dispatch 语义，§3.2.1）。"""

    @pytest.mark.asyncio
    async def test_dispatch_session_started(self, sm):
        ev = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.dispatch(ev)
        assert "s1" in sm._sessions

    @pytest.mark.asyncio
    async def test_dispatch_user_utterance(self, sm):
        ev = make_user_utterance_event(
            device_id="d1", session_id="s1", ts_ms=1000,
            transcription_id="tr-1", text="hello",
        )
        await sm.dispatch(ev)
        assert "s1" in sm._sessions

    @pytest.mark.asyncio
    async def test_dispatch_recording_ready(self, sm):
        # Need session first
        se = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.dispatch(se)

        ev = make_recording_ready_event(
            device_id="d1", session_id="s1", ts_ms=2000,
            recording_id="rec-1", manifest_path="/tmp/m.json", duration_ms=60000,
        )
        await sm.dispatch(ev)
        # No error = pass

    @pytest.mark.asyncio
    async def test_dispatch_session_ended(self, sm):
        se = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.dispatch(se)
        assert "s1" in sm._sessions

        ev = make_session_ended_event(device_id="d1", session_id="s1", ts_ms=2000)
        await sm.dispatch(ev)
        assert "s1" not in sm._sessions


class TestInProcessEventBus:
    """事件总线语义：FIFO 投递、队列满丢弃按 kind 计数、handler 异常不扩散。"""

    @pytest.mark.asyncio
    async def test_fifo_delivery(self):
        bus = InProcessEventBus(max_queue=8)
        got: list[str] = []

        async def handler(event):
            got.append(event.kind)

        bus.subscribe(handler)
        await bus.start()
        for kind in ("session.started", "user.utterance", "session.ended"):
            from agent_relay.models import AgentEvent
            bus.publish_nowait(AgentEvent(kind=kind, device_id="d1", session_id="s1", ts_ms=1))
        await asyncio.sleep(0.05)
        await bus.stop()
        assert got == ["session.started", "user.utterance", "session.ended"]

    @pytest.mark.asyncio
    async def test_queue_full_drops_counted(self):
        from agent_relay.models import AgentEvent
        bus = InProcessEventBus(max_queue=1)
        got: list[str] = []

        async def handler(event):
            got.append(event.kind)
            await asyncio.sleep(0.01)

        bus.subscribe(handler)
        await bus.start()
        ev = AgentEvent(kind="user.utterance", device_id="d1", session_id="s1", ts_ms=1)
        bus.publish_nowait(ev)
        bus.publish_nowait(ev)
        bus.publish_nowait(ev)
        await asyncio.sleep(0.1)
        await bus.stop()
        import shared.observability.metrics as m
        reg = m.get_registry()
        dropped = int(reg.agent_event_bus_dropped_total().labels(kind="user.utterance")._value.get())
        assert dropped >= 1

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_spread(self):
        from agent_relay.models import AgentEvent
        bus = InProcessEventBus(max_queue=8)
        calls = {"n": 0}

        async def boom(event):
            calls["n"] += 1
            raise RuntimeError("handler crash")

        async def fine(event):
            calls["n"] += 1

        bus.subscribe(boom)
        bus.subscribe(fine)
        await bus.start()
        bus.publish_nowait(AgentEvent(kind="session.started", device_id="d1", session_id="s1", ts_ms=1))
        await asyncio.sleep(0.05)
        await bus.stop()
        assert calls["n"] == 2  # 两个 handler 都被调用，异常不扩散
