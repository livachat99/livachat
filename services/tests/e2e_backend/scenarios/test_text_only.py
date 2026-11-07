from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestTextOnly:
    async def test_session_start_response(self, e2e_client):
        await e2e_client.session_start(seq=1)
        resp = await e2e_client.recv()
        assert resp["type"] == "session.ready"
        # 初始 status 序列（idle → listening，设计 §4.1/§4.2）
        for _ in range(2):
            assert (await e2e_client.recv())["type"] == "status"
        # Connection stays alive
        pong = await e2e_client.ping(seq=2)
        assert pong["type"] == "pong"

    async def test_consecutive_invalid_json(self, e2e_client):
        """Invalid JSON gets error(1006) but connection stays open."""
        await e2e_client.send_raw("not json")
        resp = await e2e_client.recv()
        assert resp["type"] == "error"
        assert resp["payload"]["code"] == 1006
        # Still alive
        pong = await e2e_client.ping(seq=3)
        assert pong["type"] == "pong"
