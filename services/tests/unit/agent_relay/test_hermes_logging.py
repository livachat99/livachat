from __future__ import annotations

import logging
import traceback

import pytest
from websockets.exceptions import InvalidURI

from agent_relay.adapters import hermes as hermes_module
from agent_relay.adapters.hermes import HermesAdapter, _DeviceConnection
from agent_relay.config import HermesAdapterConfig
from agent_relay.errors import AdapterConnectionError, AdapterTimeoutError

_HERMES_LOGGER = "livachat.agent_relay.adapter.hermes"


async def test_connect_ws_logs_redacted_url(monkeypatch, caplog):
    cfg = HermesAdapterConfig(
        base_url="http://127.0.0.1:9119",
        api_key="hermes-secret",
        timeout_s=1,
    )
    adapter = object.__new__(HermesAdapter)
    adapter._cfg = cfg
    conn = _DeviceConnection(device_id="device-1")

    async def _timeout(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(hermes_module.websockets, "connect", _timeout)

    with caplog.at_level(logging.INFO, logger=_HERMES_LOGGER), pytest.raises(AdapterTimeoutError):
        await adapter._connect_ws(conn)

    record = next(r for r in caplog.records if r.getMessage() == "connecting to hermes ws")
    assert record.device_id == "device-1"
    assert "hermes-secret" not in record.url
    assert "redacted" in record.url


async def test_invalid_uri_exception_does_not_leak_token(monkeypatch):
    cfg = HermesAdapterConfig(
        base_url="http://127.0.0.1:9119",
        api_key="hermes-secret",
        timeout_s=1,
    )
    adapter = object.__new__(HermesAdapter)
    adapter._cfg = cfg
    conn = _DeviceConnection(device_id="device-1")

    async def _invalid_uri(uri, **kwargs):
        raise InvalidURI(uri, "bad uri")

    monkeypatch.setattr(hermes_module.websockets, "connect", _invalid_uri)

    with pytest.raises(AdapterConnectionError) as exc:
        await adapter._connect_ws(conn)

    assert "hermes-secret" not in str(exc.value)
    assert "hermes-secret" not in "".join(traceback.format_exception(exc.value))
    assert "invalid" in str(exc.value).lower()
