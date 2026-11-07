from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum, auto


class HeartbeatAction(Enum):
    NONE = auto()
    SEND_PING = auto()
    CLOSE_HEARTBEAT = auto()  # 4002 — no frames at all for 45s


@dataclass
class HeartbeatTracker:
    """Tracks liveness of a single WebSocket connection.

    Tick every second; the returned :class:`HeartbeatAction` tells the caller what to do.

    只以「是否有任意帧（含 ping/pong 心跳）」判定存活：心跳正常即保持连接，
    不再因无业务帧而关闭（2026-08-28 移除 CLOSE_IDLE——后台 app 只传心跳
    时连接被 60s 一轮踢掉重连，空转重开 ASR/TTS 流）。
    """

    heartbeat_interval_s: float = 15.0
    heartbeat_timeout_s: float = 45.0

    _last_recv_at: float = field(default_factory=time.monotonic, init=False)
    _ping_sent: bool = field(default=False, init=False)

    def on_any_frame(self) -> None:
        now = time.monotonic()
        self._last_recv_at = now
        self._ping_sent = False

    def on_ping_sent(self) -> None:
        self._ping_sent = True

    def tick(self) -> HeartbeatAction:
        now = time.monotonic()
        silence = now - self._last_recv_at

        if silence >= self.heartbeat_timeout_s:
            return HeartbeatAction.CLOSE_HEARTBEAT
        if silence >= self.heartbeat_interval_s and not self._ping_sent:
            return HeartbeatAction.SEND_PING
        return HeartbeatAction.NONE

    async def run(
        self,
        send_ping_cb: Callable[[], Awaitable[None]],
        close_cb: Callable[[int, str], Awaitable[None]],
        *,
        stopped: Callable[[], bool] | None = None,
    ) -> None:
        """Coroutine that ticks every second and invokes callbacks.

        Runs until *close_cb* is called, the task is cancelled, or
        *stopped*() returns True (the connection was already torn down —
        otherwise the heartbeat would keep a dead connection alive for the
        full timeout before the caller's teardown runs).
        """
        try:
            while True:
                await asyncio.sleep(1)
                if stopped is not None and stopped():
                    return
                action = self.tick()
                if action == HeartbeatAction.SEND_PING:
                    self.on_ping_sent()
                    await send_ping_cb()
                elif action == HeartbeatAction.CLOSE_HEARTBEAT:
                    await close_cb(4002, "heartbeat timeout")
                    return
        except asyncio.CancelledError:
            pass
