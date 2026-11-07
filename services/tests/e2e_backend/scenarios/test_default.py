from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestDefault:
    async def test_handshake_and_ping_pong(self, e2e_client):
        resp = await e2e_client.ping(seq=1)
        assert resp["type"] == "pong"
        assert resp["payload"]["ping_seq"] == 1

    async def test_session_start_returns_ready(self, e2e_client):
        """session.start → session.ready（当前契约：新会话 restored=false）。"""
        await e2e_client.session_start(seq=1)
        resp = await e2e_client.recv()
        assert resp["type"] == "session.ready"
        assert resp["payload"]["session_restored"] is False

    async def test_multiple_pings(self, e2e_client):
        for i in range(3):
            resp = await e2e_client.ping(seq=i + 1)
            assert resp["type"] == "pong"
            assert resp["payload"]["ping_seq"] == i + 1
