from __future__ import annotations

import pytest

from agent_relay.metrics import (
    inc_adapter_error,
    inc_agent_event,
    inc_agent_reply,
    inc_job_event,
    observe_reply_latency,
    set_device_connections,
)
from shared.observability.metrics import get_registry


@pytest.fixture(autouse=True)
def _clear_metrics():
    """Reset the registry between tests to avoid duplicate metric errors."""
    import shared.observability.metrics as m
    m._registry = None


class TestAgentEventMetrics:
    def test_inc_agent_event(self):
        inc_agent_event("user.utterance", "ok")
        reg = get_registry()

        # Collect metrics text and verify counter exists
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_events_total' in text

    def test_inc_agent_event_error(self):
        inc_agent_event("user.utterance", "error")
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_events_total' in text


class TestAgentReplyMetrics:
    def test_inc_agent_reply(self):
        inc_agent_reply("hermes", "delivered")
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_replies_total' in text

    def test_inc_agent_reply_dropped(self):
        inc_agent_reply("hermes", "dropped_offline")
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_replies_total' in text

    def test_inc_agent_reply_timeout(self):
        inc_agent_reply("hermes", "timeout")
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_replies_total' in text


class TestAdapterErrorMetrics:
    def test_inc_adapter_error(self):
        inc_adapter_error("hermes", "connection")
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_adapter_errors_total' in text

    def test_multiple_error_types(self):
        inc_adapter_error("hermes", "timeout")
        inc_adapter_error("hermes", "http_error")
        inc_adapter_error("openclaw", "timeout")
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_adapter_errors_total' in text


class TestReplyLatencyMetrics:
    def test_observe_latency(self):
        observe_reply_latency("hermes", 2.5)
        observe_reply_latency("hermes", 5.0)
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_reply_latency_seconds' in text


class TestJobEventMetrics:
    def test_v2_job_event_values(self):
        """§7 定稿 event 取值：delivered / delivery_pending / session_lost / deferred。"""
        for event in ("delivered", "delivery_pending", "session_lost", "deferred",
                      "cancel_sent"):
            inc_job_event("hermes", event)
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_job_events_total' in text


class TestDeviceConnectionsGauge:
    def test_gauge_registered(self):
        set_device_connections(5)
        reg = get_registry()
        from prometheus_client import generate_latest
        text = generate_latest(reg.registry).decode()
        assert 'livachat_agent_device_connections' in text


class TestRegistryReuse:
    def test_idempotent_creation(self):
        """Verify that repeated metric creation is idempotent (existing pattern)."""
        reg = get_registry()
        c1 = reg.agent_events_total()
        c2 = reg.agent_events_total()
        assert c1 is c2

    def test_thread_safe_get_or_create(self):
        reg = get_registry()
        # Just verify the lock mechanism doesn't deadlock
        c = reg.agent_replies_total()
        assert c is not None
