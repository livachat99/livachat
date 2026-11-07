from __future__ import annotations

import asyncio
import time

from gateway.heartbeat import HeartbeatAction, HeartbeatTracker


class TestHeartbeatTracker:
    def test_initial_state_none(self):
        h = HeartbeatTracker()
        assert h.tick() == HeartbeatAction.NONE

    def test_sends_ping_after_interval(self, monkeypatch):
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now)
        h = HeartbeatTracker(heartbeat_interval_s=1.0, heartbeat_timeout_s=5.0)
        assert h.tick() == HeartbeatAction.NONE

        # Advance past interval
        monkeypatch.setattr(time, "monotonic", lambda: now + 2.0)
        assert h.tick() == HeartbeatAction.SEND_PING

    def test_no_ping_after_already_sent(self, monkeypatch):
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now)
        h = HeartbeatTracker(heartbeat_interval_s=1.0)
        assert h.tick() == HeartbeatAction.NONE
        h.on_ping_sent()

        # Still past interval, but ping already sent
        monkeypatch.setattr(time, "monotonic", lambda: now + 2.0)
        assert h.tick() == HeartbeatAction.NONE

    def test_ping_reset_after_any_frame(self, monkeypatch):
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now)
        h = HeartbeatTracker(heartbeat_interval_s=1.0)
        h.on_ping_sent()
        monkeypatch.setattr(time, "monotonic", lambda: now + 2.0)

        # After receiving a frame, ping flag is cleared and timer resets
        h.on_any_frame()
        assert h.tick() == HeartbeatAction.NONE

    def test_heartbeat_timeout(self, monkeypatch):
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now)
        h = HeartbeatTracker(heartbeat_timeout_s=2.0)
        monkeypatch.setattr(time, "monotonic", lambda: now + 3.0)
        assert h.tick() == HeartbeatAction.CLOSE_HEARTBEAT

    async def test_run_exits_early_when_stopped(self):
        """连接已死（stop 置位）时 run() 应在下一个 tick 直接返回，
        而不是等满 heartbeat_timeout_s 才退出（P1: 断连后 35s 才 teardown）。"""
        closed = []

        async def close_cb(code: int, reason: str) -> None:
            closed.append((code, reason))

        h = HeartbeatTracker(heartbeat_timeout_s=60.0)

        async def run() -> None:
            await h.run(lambda: None, close_cb, stopped=lambda: True)

        await asyncio.wait_for(run(), timeout=3.0)
        assert closed == []

    def test_heartbeats_keep_alive_without_business(self, monkeypatch):
        """回归（2026-08-28）：只传心跳、无业务帧时连接永不关闭（CLOSE_IDLE 已移除）。

        长时间 tick + 定期 on_any_frame（心跳），唯一可能的动作是 SEND_PING/NONE。
        """
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now)
        h = HeartbeatTracker(heartbeat_interval_s=1.0, heartbeat_timeout_s=5.0)

        for _ in range(10):  # 10 个周期：心跳持续 > heartbeat_timeout
            now += 0.5
            h.on_any_frame()  # 心跳帧
            assert h.tick() in (HeartbeatAction.NONE, HeartbeatAction.SEND_PING)
