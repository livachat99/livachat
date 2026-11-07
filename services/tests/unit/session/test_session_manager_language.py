from __future__ import annotations

from session.manager import SessionManager


class TestSessionManagerLanguage:
    async def test_language_stored_on_new_session(self):
        mgr = SessionManager()

        ctx, _ = await mgr.start(device_id="dev-1", language="zh-Hans")

        assert ctx.language == "zh-Hans"

    async def test_missing_language_is_empty(self):
        mgr = SessionManager()

        ctx, _ = await mgr.start(device_id="dev-1")

        assert ctx.language == ""

    async def test_none_language_is_empty(self):
        mgr = SessionManager()

        ctx, _ = await mgr.start(device_id="dev-1", language=None)

        assert ctx.language == ""

    async def test_resume_uses_latest_client_language(self):
        mgr = SessionManager()

        first, _ = await mgr.start(
            device_id="dev-1", language="en", resume_id="sess_resume",
        )
        second, _ = await mgr.start(
            device_id="dev-1", language="ja", resume_id="sess_resume",
        )

        assert first.language == "en"
        assert second.language == "ja"
        assert second.session_id == first.session_id

    async def test_unknown_language_is_preserved(self):
        """Server stores what the client reports; language mapping is client-side."""
        mgr = SessionManager()

        ctx, _ = await mgr.start(device_id="dev-1", language="x-test")

        assert ctx.language == "x-test"
