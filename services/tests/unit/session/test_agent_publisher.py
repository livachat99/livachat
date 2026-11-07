from __future__ import annotations

import pytest

from agent_relay.event_bus import InProcessEventBus
from session.agent_publisher import AgentEventPublisher


@pytest.fixture
def publisher():
    return AgentEventPublisher(InProcessEventBus())


class TestPublishUtterance:
    @pytest.mark.asyncio
    async def test_publishes_with_correct_payload(self, publisher):
        got = []
        publisher._bus.subscribe(lambda ev: got.append(ev) or _noop())
        await publisher._bus.start()
        await publisher.publish_utterance(
            session_id="s1",
            device_id="d1",
            text="hello",
            transcription_id="tr-1",
            language="en",
        )
        await _drain(publisher)
        await publisher._bus.stop()
        assert len(got) == 1
        data = got[0]
        assert data.kind == "user.utterance"
        assert data.session_id == "s1"
        assert data.device_id == "d1"
        assert data.text == "hello"
        assert data.transcription_id == "tr-1"
        assert data.language == "en"
        assert data.ts_ms > 0

    @pytest.mark.asyncio
    async def test_queue_full_does_not_raise(self, publisher):
        # 队列满（无 consumer 消费）→ 丢弃不抛（fire-and-forget）
        bus = InProcessEventBus(max_queue=1)
        pub = AgentEventPublisher(bus)
        await bus.start()
        for i in range(5):
            await pub.publish_utterance(
                session_id="s1", device_id="d1", text=f"t{i}",
                transcription_id=f"tr-{i}",
            )
        await bus.stop()

    @pytest.mark.asyncio
    async def test_language_defaults_to_empty(self, publisher):
        got = []
        publisher._bus.subscribe(lambda ev: got.append(ev) or _noop())
        await publisher._bus.start()
        await publisher.publish_utterance(
            session_id="s1",
            device_id="d1",
            text="hello",
            transcription_id="tr-1",
        )
        await _drain(publisher)
        await publisher._bus.stop()
        assert got[0].language == ""


class TestPublishSessionStarted:
    @pytest.mark.asyncio
    async def test_publishes_correctly(self, publisher):
        got = []
        publisher._bus.subscribe(lambda ev: got.append(ev) or _noop())
        await publisher._bus.start()
        await publisher.publish_session_started(session_id="s1", device_id="d1")
        await _drain(publisher)
        await publisher._bus.stop()
        assert got[0].kind == "session.started"
        assert got[0].session_id == "s1"
        assert got[0].device_id == "d1"
        assert got[0].timezone is None          # 未传 → None（渲染走"未上报"文案）

    @pytest.mark.asyncio
    async def test_publishes_timezone(self, publisher):
        """docs/agent_relay_prompts.md §3.2：设备时区随 session.started 透传到事件。"""
        got = []
        publisher._bus.subscribe(lambda ev: got.append(ev) or _noop())
        await publisher._bus.start()
        await publisher.publish_session_started(session_id="s1", device_id="d1",
                                                timezone="Asia/Shanghai")
        await _drain(publisher)
        await publisher._bus.stop()
        assert got[0].timezone == "Asia/Shanghai"

    @pytest.mark.asyncio
    async def test_publishes_language(self, publisher):
        """docs/agent_multilingual.md：App UI 语言随 session.started 透传到事件。"""
        got = []
        publisher._bus.subscribe(lambda ev: got.append(ev) or _noop())
        await publisher._bus.start()
        await publisher.publish_session_started(session_id="s1", device_id="d1",
                                                language="zh-Hant")
        await _drain(publisher)
        await publisher._bus.stop()
        assert got[0].language == "zh-Hant"


class TestPublishSessionEnded:
    @pytest.mark.asyncio
    async def test_publishes_correctly(self, publisher):
        got = []
        publisher._bus.subscribe(lambda ev: got.append(ev) or _noop())
        await publisher._bus.start()
        await publisher.publish_session_ended(session_id="s1", device_id="d1")
        await _drain(publisher)
        await publisher._bus.stop()
        assert got[0].kind == "session.ended"


class TestPublishRecordingReady:
    @pytest.mark.asyncio
    async def test_publishes_correctly(self, publisher):
        got = []
        publisher._bus.subscribe(lambda ev: got.append(ev) or _noop())
        await publisher._bus.start()
        await publisher.publish_recording_ready(
            session_id="s1",
            device_id="d1",
            recording_id="rec-1",
            manifest_path="/tmp/m.json",
            duration_ms=60000,
        )
        await _drain(publisher)
        await publisher._bus.stop()
        assert got[0].kind == "recording.ready"
        assert got[0].recording_id == "rec-1"
        assert got[0].manifest_path == "/tmp/m.json"
        assert got[0].duration_ms == 60000


async def _drain(publisher, timeout: float = 0.5) -> None:
    import asyncio
    deadline = asyncio.get_running_loop().time() + timeout
    while publisher._bus._queue.qsize() > 0:
        if asyncio.get_running_loop().time() > deadline:
            break
        await asyncio.sleep(0.01)


def _noop() -> None:
    return None
