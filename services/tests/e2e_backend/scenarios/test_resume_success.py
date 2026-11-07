from __future__ import annotations

import pytest

from shared.version import SERVER_VERSION

pytestmark = pytest.mark.e2e

_SID = "sess_" + "a" * 18  # valid 18-char session_id


class TestResumeSuccess:
    async def test_session_start_with_resume_id(self, e2e_client):
        """resume 走 session.start：session.ready 回执（restored 取决于 Redis 快照）。"""
        await e2e_client.session_start(seq=1, session_id=_SID)
        resp = await e2e_client.recv()
        assert resp["type"] == "session.ready"
        assert resp["payload"]["server_version"] == SERVER_VERSION

    async def test_session_restored_flag_false_when_no_redis(self, e2e_client):
        await e2e_client.session_start(seq=1)
        resp = await e2e_client.recv()
        assert resp["type"] == "session.ready"
        assert resp["payload"]["session_restored"] is False
