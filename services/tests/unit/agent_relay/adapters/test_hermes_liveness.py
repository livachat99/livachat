"""Unit tests: §5.8 liveness 纯函数——跳过判定与探测结局分类。

（循环行为见 tests/integration/agent_relay/test_liveness_probe_integration.py）
"""

from __future__ import annotations

from websockets.exceptions import ConnectionClosed

from agent_relay.adapters.hermes import HermesAdapter, _DeviceConnection
from agent_relay.errors import AdapterConnectionError, AdapterTimeoutError


def _conn() -> _DeviceConnection:
    conn = _DeviceConnection(device_id="dev-1")
    conn.ws = object()  # 跳过判定只看 None 与否
    conn.ready.set()
    return conn


class TestLivenessSkip:
    def test_retained_connection_not_skipped(self):
        assert HermesAdapter._liveness_skip(_conn()) is False

    def test_skips_without_ws(self):
        conn = _conn()
        conn.ws = None
        assert HermesAdapter._liveness_skip(conn) is True

    def test_skips_stopped(self):
        conn = _conn()
        conn.stopped.set()
        assert HermesAdapter._liveness_skip(conn) is True

    def test_skips_ready_unset(self):
        conn = _conn()
        conn.ready.clear()
        assert HermesAdapter._liveness_skip(conn) is True

    def test_skips_pending_rpc(self):
        conn = _conn()
        conn.pending[1] = object()
        assert HermesAdapter._liveness_skip(conn) is True

    def test_skips_in_flight_turn(self):
        conn = _conn()
        conn.turn_futures["t-1"] = object()
        assert HermesAdapter._liveness_skip(conn) is True


class TestLivenessVerdict:
    def test_no_exception_is_alive(self):
        assert HermesAdapter._liveness_probe_verdict(None) == "alive"

    def test_timeout_counts(self):
        exc = AdapterTimeoutError("probe timed out", adapter_name="hermes")
        assert HermesAdapter._liveness_probe_verdict(exc) == "timeout"

    def test_jsonrpc_error_response_is_alive(self):
        # _resolve_rpc 对 error 响应以带 status_code 的 AdapterConnectionError
        # set_exception——分派层活着（§5.8 计数规则）
        exc = AdapterConnectionError("jsonrpc error", adapter_name="hermes", status_code=5000)
        assert HermesAdapter._liveness_probe_verdict(exc) == "alive"

    def test_connection_layer_error_ignored(self):
        exc = AdapterConnectionError("WebSocket is not connected", adapter_name="hermes")
        assert HermesAdapter._liveness_probe_verdict(exc) == "connection"

    def test_websocket_closed_ignored(self):
        assert HermesAdapter._liveness_probe_verdict(ConnectionClosed(None, None)) == "connection"

    def test_oserror_ignored(self):
        assert HermesAdapter._liveness_probe_verdict(OSError("reset")) == "connection"

    def test_unexpected_error(self):
        assert HermesAdapter._liveness_probe_verdict(ValueError("boom")) == "unexpected"
