"""AgentEventPublisher — fire-and-forget in-process publish for agent relay events.

Wraps the InProcessEventBus so the orchestrator and recording service can
push events to the agent relay bus without blocking the main pipeline
（chatserver_no_redis.md §3.2.1：替代原 Redis pub/sub 实现，接口不变）。

Usage in orchestrator::

    if self._agent_publisher is not None:
        asyncio.create_task(
            self._agent_publisher.publish_utterance(
                session_id=...,
                device_id=...,
                text=...,
                transcription_id=...,
                language=...,
            )
        )
"""

from __future__ import annotations

import time

from agent_relay.event_bus import InProcessEventBus
from agent_relay.models import (
    make_recording_ready_event,
    make_session_ended_event,
    make_session_started_event,
    make_user_utterance_event,
)


class AgentEventPublisher:
    """Publishes session events to the in-process agent event bus.

    All publish methods are fire-and-forget: bus enqueue never blocks or
    raises to the caller（队列满 → 丢弃 + metric）。
    """

    def __init__(self, bus: InProcessEventBus) -> None:
        self._bus = bus

    # -- public API -----------------------------------------------------------

    async def publish_utterance(
        self,
        *,
        session_id: str,
        device_id: str,
        text: str,
        transcription_id: str,
        language: str = "",
    ) -> None:
        event = make_user_utterance_event(
            device_id=device_id,
            session_id=session_id,
            ts_ms=_now_ms(),
            transcription_id=transcription_id,
            text=text,
            language=language,
        )
        self._publish(event)

    async def publish_session_started(
        self,
        *,
        session_id: str,
        device_id: str,
        timezone: str | None = None,
        language: str = "",
    ) -> None:
        event = make_session_started_event(
            device_id=device_id,
            session_id=session_id,
            ts_ms=_now_ms(),
            timezone=timezone,
            language=language,
        )
        self._publish(event)

    async def publish_session_ended(
        self,
        *,
        session_id: str,
        device_id: str,
    ) -> None:
        event = make_session_ended_event(
            device_id=device_id,
            session_id=session_id,
            ts_ms=_now_ms(),
        )
        self._publish(event)

    async def publish_recording_ready(
        self,
        *,
        session_id: str,
        device_id: str,
        recording_id: str,
        manifest_path: str,
        duration_ms: int,
    ) -> None:
        event = make_recording_ready_event(
            device_id=device_id,
            session_id=session_id,
            ts_ms=_now_ms(),
            recording_id=recording_id,
            manifest_path=manifest_path,
            duration_ms=duration_ms,
        )
        self._publish(event)

    # -- internal -------------------------------------------------------------

    def _publish(self, event) -> None:
        self._bus.publish_nowait(event)


def _now_ms() -> int:
    return int(time.time() * 1000)
