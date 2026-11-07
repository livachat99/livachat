from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

_SID = "sess_" + "x" * 18  # valid 18-char session_id


class TestResumeExpired:
    async def test_session_start_with_nonexistent_resume_id(self, e2e_client):
        """resume 失败自动新会话：session.ready 且 restored=false（非 error）。"""
        await e2e_client.session_start(seq=1, session_id=_SID)
        resp = await e2e_client.recv()
        assert resp["type"] == "session.ready"
        assert resp["payload"]["session_restored"] is False

    async def test_new_session_after_failed_resume(self, e2e_client):
        await e2e_client.session_start(seq=1, session_id=_SID)
        assert (await e2e_client.recv())["type"] == "session.ready"
        for _ in range(2):
            await e2e_client.recv()  # 初始 status（idle → listening）
        pong = await e2e_client.ping(seq=2)
        assert pong["type"] == "pong"
