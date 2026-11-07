from __future__ import annotations

from session.manager import SessionManager


class TestSessionManager:
    async def test_start_new_session(self):
        mgr = SessionManager()
        ctx, history = await mgr.start(device_id="d1")
        assert ctx.session_id.startswith("sess_")
        assert ctx.device_id == "d1"
        assert ctx.restored is False
        assert str(ctx.state) == "listening"
        assert history.is_empty

    async def test_end_session(self):
        mgr = SessionManager()
        ctx, _ = await mgr.start(device_id="d1")
        await mgr.end(ctx, "user_quit")
        assert str(ctx.state) == "idle"
