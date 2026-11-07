from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestInterrupt:
    async def test_interrupt_before_session_is_ignored(self, e2e_client):
        """无 session 时 interrupt 被静默忽略（无响应，连接存活）。"""
        await e2e_client.interrupt(seq=1, reason="manual")
        pong = await e2e_client.ping(seq=2)
        assert pong["type"] == "pong"

    async def test_interrupt_after_session_start(self, e2e_client):
        await e2e_client.session_start(seq=1)
        assert (await e2e_client.recv())["type"] == "session.ready"
        for _ in range(2):
            await e2e_client.recv()  # 初始 status（idle → listening）
        await e2e_client.interrupt(seq=2, reason="user_voice")
        resp = await e2e_client.recv()
        assert resp["type"] == "status"
