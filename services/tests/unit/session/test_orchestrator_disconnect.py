"""Unit tests: orchestrator connection-closed hook → agent session.ended.

An abnormal disconnect (no session.end frame) must still notify the agent
relay so it reaps chatlog watchers / Hermes sessions / orphaned jobs, while
the Redis snapshot is kept for resume. The event must be published at most
once per session (session.end vs. connection-closed race).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from session import ownership
from session.orchestrator import DialogOrchestrator


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish_session_ended(self, *, session_id: str, device_id: str) -> None:
        self.published.append((session_id, device_id))


def _fake_handler(device_id: str = "dev-1") -> SimpleNamespace:
    return SimpleNamespace(
        principal=SimpleNamespace(device_id=device_id),
        stop=lambda: None,
    )


def _make_dialog(publisher: FakePublisher | None) -> DialogOrchestrator:
    orch = DialogOrchestrator(
        handler=_fake_handler(),
        session_mgr=SimpleNamespace(end=AsyncStop()),
        agent_publisher=publisher,
    )
    orch._ctx = SimpleNamespace(session_id="sess-1")
    # 夹具保真：生产路径上 _handle_session_start 会登记会话归属（owned 才发布 ended）
    ownership.take("sess-1", orch)
    return orch


class AsyncStop:
    async def __call__(self, *args, **kwargs) -> None:
        pass


class TestDialogConnectionClosed:
    async def test_publishes_session_ended(self) -> None:
        pub = FakePublisher()
        orch = _make_dialog(pub)
        await orch.on_connection_closed()
        await asyncio.sleep(0)  # publication is fire-and-forget
        assert pub.published == [("sess-1", "dev-1")]
        assert orch._session_ended_published is True

    async def test_no_duplicate_on_repeat_calls(self) -> None:
        pub = FakePublisher()
        orch = _make_dialog(pub)
        await orch.on_connection_closed()
        await orch.on_connection_closed()
        await asyncio.sleep(0)
        assert len(pub.published) == 1

    async def test_session_end_then_disconnect_publishes_once(self) -> None:
        pub = FakePublisher()
        orch = _make_dialog(pub)
        await orch._handle_session_end(
            SimpleNamespace(payload=SimpleNamespace(reason="user"))
        )
        await orch.on_connection_closed()
        await asyncio.sleep(0)
        assert len(pub.published) == 1
        assert pub.published[0][0] == "sess-1"

    async def test_no_ctx_no_publish(self) -> None:
        pub = FakePublisher()
        orch = _make_dialog(pub)
        orch._ctx = None
        await orch.on_connection_closed()
        await asyncio.sleep(0)
        assert pub.published == []

    async def test_no_publisher_no_crash(self) -> None:
        orch = _make_dialog(None)
        await orch.on_connection_closed()  # must not raise
