"""InProcessEventBus — 进程内 agent 事件总线（chatserver_no_redis.md §3.2.1）。

替代 `livachat:agent:events` Redis pub/sub 通道。语义对齐：
- FIFO：有界 asyncio.Queue + 单 consumer task（单订阅者，与 Redis 单
  channel 单 consumer 等价；未来多订阅者再演进为每订阅者队列）；
- fire-and-forget：队列满 → 丢弃 + dropped metric + 日志（按 kind 分级），
  永不阻塞、永不抛给生产端；
- consumer 异常不扩散：单 handler 失败只 log + 继续。

线程约束：全部生产/消费必须在事件循环线程内调用 publish_nowait
（asyncio.Queue 非线程安全；跨线程需经 loop.call_soon_threadsafe 桥接——
现有全部生产端均在事件循环线程，无桥接需要，§3.2.1 审计结论）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from agent_relay.metrics import inc_agent_event
from agent_relay.models import AgentEvent
from shared.observability.metrics import get_registry

logger = logging.getLogger("livachat.agent_relay.event_bus")


class InProcessEventBus:
    """有界队列 + 单 consumer task 的进程内事件总线。"""

    def __init__(self, *, max_queue: int = 1024) -> None:
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=max_queue)
        self._handlers: list[Callable[[AgentEvent], Awaitable[None]]] = []
        self._task: asyncio.Task[None] | None = None

    # -- API -----------------------------------------------------------------

    def subscribe(self, handler: Callable[[AgentEvent], Awaitable[None]]) -> None:
        """注册订阅 handler（async callable；application.py 接线一次）。"""
        self._handlers.append(handler)

    def publish_nowait(self, event: AgentEvent) -> None:
        """O(1)，永不抛。队列满 → 丢弃 + metric。"""
        try:
            self._queue.put_nowait(event)
            inc_agent_event(event.kind, "ok")
        except asyncio.QueueFull:
            get_registry().agent_event_bus_dropped_total().labels(kind=event.kind).inc()
            logger.warning(
                "agent event bus queue full, event dropped",
                extra={"kind": event.kind, "pending": self._queue.qsize()},
            )
        self._refresh_depth()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="agent-event-bus")
            self._refresh_depth()

    async def stop(self, *, drain_timeout_s: float = 3.0) -> None:
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=drain_timeout_s)
        except TimeoutError:
            logger.warning(
                "agent event bus drain timeout", extra={"pending": self._queue.qsize()}
            )
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    # -- consumer ------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                for handler in self._handlers:
                    try:
                        await handler(event)
                    except Exception:
                        logger.warning(
                            "agent event handler failed",
                            extra={"kind": event.kind},
                            exc_info=True,
                        )
            finally:
                self._queue.task_done()
                self._refresh_depth()

    def _refresh_depth(self) -> None:
        get_registry().agent_event_bus_depth().set(self._queue.qsize())
