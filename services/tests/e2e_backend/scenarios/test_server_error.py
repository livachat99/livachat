from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestServerError:
    async def test_large_payload_gets_error(self, e2e_client):
        """Sending a frame with unexpected fields should not crash the server."""
        await e2e_client.send_raw('{"type":"ping","seq":1,"timestamp":1000,"payload":{"extra":1}}')
        resp = await e2e_client.recv()
        # Server should respond with pong (ignoring extra fields) or error
        assert resp["type"] in ("pong", "error")

    async def test_unknown_message_type(self, e2e_client):
        """Unknown message types get error(1006)."""
        import json
        await e2e_client.send_raw(json.dumps({"type": "unknown.msg", "seq": 1, "timestamp": 1000, "payload": {}}))
        resp = await e2e_client.recv()
        assert resp["type"] == "error"
