from __future__ import annotations


class AgentRelayError(Exception):
    """Base exception for all agent-relay errors."""


class AdapterTimeoutError(AgentRelayError):
    """Raised when an external agent adapter times out."""

    def __init__(self, message: str, *, adapter_name: str) -> None:
        super().__init__(message)
        self.adapter_name = adapter_name


class AdapterConnectionError(AgentRelayError):
    """Raised when an external agent adapter fails to connect or returns an error."""

    def __init__(self, message: str, *, adapter_name: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.adapter_name = adapter_name
        self.status_code = status_code


class DuplicateEventError(AgentRelayError):
    """Raised (and caught internally) when a duplicate event is detected."""

    def __init__(self, message: str, *, event_id: str) -> None:
        super().__init__(message)
        self.event_id = event_id


class DeviceOfflineError(AgentRelayError):
    """Raised when the target device is not connected to any gateway instance."""

    def __init__(self, message: str, *, device_id: str) -> None:
        super().__init__(message)
        self.device_id = device_id
