from __future__ import annotations

from agent_relay.errors import (
    AdapterConnectionError,
    AdapterTimeoutError,
    AgentRelayError,
    DeviceOfflineError,
    DuplicateEventError,
)


class TestAgentRelayError:
    def test_base_exception_catchable(self):
        try:
            raise AgentRelayError("base error")
        except AgentRelayError as e:
            assert str(e) == "base error"

    def test_is_exception(self):
        assert issubclass(AgentRelayError, Exception)


class TestAdapterTimeoutError:
    def test_inherits_base(self):
        assert issubclass(AdapterTimeoutError, AgentRelayError)

    def test_stores_adapter_name(self):
        err = AdapterTimeoutError("timed out", adapter_name="hermes")
        assert err.adapter_name == "hermes"
        assert "timed out" in str(err)


class TestAdapterConnectionError:
    def test_inherits_base(self):
        assert issubclass(AdapterConnectionError, AgentRelayError)

    def test_stores_fields(self):
        err = AdapterConnectionError("connection refused", adapter_name="hermes", status_code=503)
        assert err.adapter_name == "hermes"
        assert err.status_code == 503

    def test_status_code_optional(self):
        err = AdapterConnectionError("dns error", adapter_name="hermes")
        assert err.status_code is None


class TestDuplicateEventError:
    def test_inherits_base(self):
        assert issubclass(DuplicateEventError, AgentRelayError)

    def test_stores_event_id(self):
        err = DuplicateEventError("duplicate", event_id="tr-1")
        assert err.event_id == "tr-1"


class TestDeviceOfflineError:
    def test_inherits_base(self):
        assert issubclass(DeviceOfflineError, AgentRelayError)

    def test_stores_device_id(self):
        err = DeviceOfflineError("offline", device_id="dev-1")
        assert err.device_id == "dev-1"
