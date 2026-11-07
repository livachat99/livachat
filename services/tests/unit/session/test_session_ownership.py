"""会话归属：被接管的旧连接不得发布 session.ended（重连 resume 复用同一 session_id）。

背景：旧连接延迟拆除（reader 阻塞在死链 / 心跳超时）晚于新连接 session.start，
照常发布 ended 会把 agent relay 侧**活跃**会话的 watcher/session 摘掉
（agent_relay/session_manager.py::on_session_ended 按 sid pop）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from session import ownership
from session.orchestrator import DialogOrchestrator


class _FakePublisher:
    def __init__(self):
        self.ended: list[dict] = []

    async def publish_session_ended(self, *, session_id: str, device_id: str) -> None:
        self.ended.append({"session_id": session_id, "device_id": device_id})


def _make_orch(*, session_id: str, publisher) -> DialogOrchestrator:
    orch = DialogOrchestrator(handler=MagicMock(), codec=MagicMock(), agent_publisher=publisher)
    orch._handler.principal.device_id = "dev-1"
    orch._ctx = MagicMock()
    orch._ctx.session_id = session_id
    return orch


class TestOwnershipRegistry:
    def test_take_owns_release(self):
        owner = object()
        ownership.take("sess_unit_a", owner)
        assert ownership.owns("sess_unit_a", owner)
        assert not ownership.owns("sess_unit_a", object())

        ownership.release("sess_unit_a", object())  # 非 owner：no-op
        assert ownership.owns("sess_unit_a", owner)

        ownership.release("sess_unit_a", owner)
        assert not ownership.owns("sess_unit_a", owner)

    def test_takeover_replaces_owner(self):
        old, new = object(), object()
        ownership.take("sess_unit_b", old)
        ownership.take("sess_unit_b", new)
        assert ownership.owns("sess_unit_b", new)
        assert not ownership.owns("sess_unit_b", old)
        ownership.release("sess_unit_b", new)

    def test_empty_session_id_is_ignored(self):
        ownership.take("", object())
        assert not ownership.owns("", object())


class TestPublishSessionEndedGuard:
    @pytest.mark.asyncio
    async def test_owner_still_publishes(self):
        pub = _FakePublisher()
        orch = _make_orch(session_id="sess_unit_c", publisher=pub)
        ownership.take("sess_unit_c", orch)

        await orch._publish_session_ended("connection_closed")
        await asyncio.sleep(0)  # create_task 落地

        assert pub.ended == [{"session_id": "sess_unit_c", "device_id": "dev-1"}]

    @pytest.mark.asyncio
    async def test_superseded_connection_skips_publish(self):
        pub = _FakePublisher()
        old = _make_orch(session_id="sess_unit_d", publisher=pub)
        ownership.take("sess_unit_d", old)
        # 新连接 resume 同一 session_id → 接管归属
        ownership.take("sess_unit_d", object())

        await old._publish_session_ended("connection_closed")
        await asyncio.sleep(0)

        assert pub.ended == []

    @pytest.mark.asyncio
    async def test_unknown_owner_skips_publish(self):
        """未登记的 session（如测试直构、非 session.start 路径）不发布。"""
        pub = _FakePublisher()
        orch = _make_orch(session_id="sess_unit_e", publisher=pub)

        await orch._publish_session_ended("connection_closed")
        await asyncio.sleep(0)

        assert pub.ended == []
