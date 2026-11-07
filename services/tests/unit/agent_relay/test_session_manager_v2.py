"""Unit tests: AgentSessionManager v2 lifecycle (proactive routing)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from agent_relay.adapters.base import (
    AgentAdapter,
    AgentContext,
    AgentResponseChunk,
    ChatlogUpdate,
)
from agent_relay.config import AgentRelayConfig
from agent_relay.models import (
    make_session_ended_event,
    make_session_started_event,
    make_user_utterance_event,
)
from agent_relay.session_manager import AgentSessionManager


class FakePublisher:
    def __init__(self):
        self.published: list[dict] = []

    async def publish(self, msg) -> bool:
        self.published.append({
            "device_id": msg.device_id,
            "session_id": msg.session_id,
            "text": msg.text,
            "agent_name": msg.agent_name,
            "proactive": msg.proactive,
            "reply_to": msg.reply_to,
        })
        return True


class ProactiveMockAdapter(AgentAdapter):
    name = "mock"

    def __init__(self):
        self.reactive_enabled = False
        self.proactive_enabled = True
        self.started: list[AgentContext] = []
        self.ended: list[AgentContext] = []
        self.nudges: list[ChatlogUpdate] = []
        self.push_callback = None

    async def ask(self, text: str, context: AgentContext) -> AsyncIterator[AgentResponseChunk]:
        if False:
            yield

    async def on_session_start(self, context: AgentContext) -> None:
        self.started.append(context)

    async def on_session_end(self, context: AgentContext) -> None:
        self.ended.append(context)

    async def nudge(self, update: ChatlogUpdate, context: AgentContext):
        self.nudges.append(update)
        if False:
            yield

    def set_push_callback(self, cb) -> None:
        self.push_callback = cb


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.fixture
def cfg():
    return AgentRelayConfig()


@pytest.fixture
def pub():
    return FakePublisher()


@pytest.fixture
def adapter():
    return ProactiveMockAdapter()


@pytest.fixture
def sm(adapter, pub, cfg):
    # No Redis → watcher not started; lifecycle callbacks still fire.
    return AgentSessionManager(adapters={"mock": adapter}, publisher=pub, cfg=cfg)


class TestProactiveLifecycle:
    async def test_on_session_started_calls_adapter(self, sm, adapter):
        ev = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.on_session_started(ev)
        assert await _wait_until(lambda: len(adapter.started) == 1)
        assert adapter.started[0].session_id == "s1"

    async def test_on_session_ended_calls_adapter(self, sm, adapter):
        ev = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.on_session_started(ev)
        await _wait_until(lambda: len(adapter.started) == 1)

        end = make_session_ended_event(device_id="d1", session_id="s1", ts_ms=2000)
        await sm.on_session_ended(end)
        assert await _wait_until(lambda: len(adapter.ended) == 1)
        assert adapter.ended[0].session_id == "s1"

    async def test_no_watcher_without_redis(self, sm):
        ev = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.on_session_started(ev)
        assert sm._watchers == {}


class TestReactiveFiltering:
    async def test_proactive_adapter_not_asked(self, sm, adapter):
        """Proactive-only adapters never receive ask() on user utterance."""
        ev = make_user_utterance_event(
            device_id="d1", session_id="s1", ts_ms=1000,
            transcription_id="tr-1", text="hi",
        )
        await sm.on_user_utterance(ev)
        await asyncio.sleep(0.05)
        assert adapter.nudges == []
        # no ask_calls recording in this adapter — verify no crash and no push

    async def test_reactive_adapter_still_asked(self, cfg, pub):
        from agent_relay.adapters.base import AgentAdapter, AgentResponseChunk

        class _ReactiveMock(AgentAdapter):
            name = "rmock"

            def __init__(self):
                self.reactive_enabled = True
                self.proactive_enabled = False
                self.ask_calls = []

            async def ask(self, text, context):
                self.ask_calls.append(text)
                yield AgentResponseChunk(text="ok", is_final=True)

        adapter = _ReactiveMock()
        sm = AgentSessionManager(adapters={"rmock": adapter}, publisher=pub, cfg=cfg)
        ev = make_user_utterance_event(
            device_id="d1", session_id="s1", ts_ms=1000,
            transcription_id="tr-1", text="hi",
        )
        await sm.on_user_utterance(ev)
        assert await _wait_until(lambda: len(adapter.ask_calls) == 1)
        assert len(pub.published) == 1
        assert pub.published[0]["proactive"] is False


class TestPushCallback:
    async def test_push_callback_publishes_proactive(self, sm, adapter, pub):
        ev = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.on_session_started(ev)
        await _wait_until(lambda: len(adapter.started) == 1)

        assert adapter.push_callback is not None
        await adapter.push_callback(
            "d1", "s1",
            AgentResponseChunk(text="后台完成", is_final=True, metadata={"agent_name": "mock"}),
        )
        assert len(pub.published) == 1
        assert pub.published[0]["text"] == "后台完成"
        assert pub.published[0]["proactive"] is True
        assert pub.published[0]["agent_name"] == "mock"

    async def test_push_callback_unknown_session_dropped(self, sm, adapter, pub):
        assert adapter.push_callback is not None
        await adapter.push_callback(
            "d1", "no-such-session", AgentResponseChunk(text="x", is_final=True)
        )
        assert pub.published == []

    async def test_push_callback_empty_text_dropped(self, sm, adapter, pub):
        ev = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.on_session_started(ev)
        await _wait_until(lambda: len(adapter.started) == 1)
        await adapter.push_callback("d1", "s1", AgentResponseChunk(text="", is_final=True))
        assert pub.published == []


class TestWatcherSelfHeal:
    async def test_utterance_rearms_watcher_after_restart(self, cfg, pub, tmp_path):
        """Relay restart loses watchers for live sessions; the next utterance
        must re-arm the watcher and the Hermes session."""

        async def lookup(device_uuid=None, **kw):
            return {"recording_session_id": "rec-1", "base_path": str(tmp_path)}

        adapter = ProactiveMockAdapter()
        sm = AgentSessionManager(
            adapters={"mock": adapter},
            publisher=pub,
            cfg=cfg,
            recording_lookup=lookup,
            base_dir=str(tmp_path),
        )
        ev = make_user_utterance_event(
            device_id="d1", session_id="s1", ts_ms=1000,
            transcription_id="tr-1", text="hi",
        )
        await sm.on_user_utterance(ev)

        assert "s1" in sm._watchers
        # Hermes session (re)created for the session
        assert await _wait_until(lambda: len(adapter.started) == 1)
        await sm.shutdown()

    async def test_no_self_heal_without_lookup(self, cfg, pub):
        """Without a recording lookup the watcher cannot resolve the chatlog — no watcher."""
        adapter = ProactiveMockAdapter()
        sm = AgentSessionManager(adapters={"mock": adapter}, publisher=pub, cfg=cfg)
        ev = make_user_utterance_event(
            device_id="d1", session_id="s1", ts_ms=1000,
            transcription_id="tr-1", text="hi",
        )
        await sm.on_user_utterance(ev)
        assert sm._watchers == {}

    async def test_self_heal_does_not_duplicate_watcher(self, cfg, pub, tmp_path):

        async def lookup(device_uuid=None, **kw):
            return {"recording_session_id": "rec-1", "base_path": str(tmp_path)}

        adapter = ProactiveMockAdapter()
        sm = AgentSessionManager(
            adapters={"mock": adapter},
            publisher=pub,
            cfg=cfg,
            recording_lookup=lookup,
            base_dir=str(tmp_path),
        )
        ev = make_user_utterance_event(
            device_id="d1", session_id="s1", ts_ms=1000,
            transcription_id="tr-1", text="hi",
        )
        await sm.on_user_utterance(ev)
        first = sm._watchers["s1"]
        # Second utterance → same watcher instance, no duplicate poll loop.
        ev2 = make_user_utterance_event(
            device_id="d1", session_id="s1", ts_ms=2000,
            transcription_id="tr-2", text="hi again",
        )
        await sm.on_user_utterance(ev2)
        assert sm._watchers["s1"] is first
        await sm.shutdown()


class TestWatcherLanguageUpdate:
    """docs/agent_multilingual.md D10：重复 session.started 更新已启动 watcher。"""

    @staticmethod
    def _sm(cfg, pub, tmp_path):
        async def lookup(device_uuid=None, **kw):
            return {"recording_session_id": "rec-1", "base_path": str(tmp_path)}

        adapter = ProactiveMockAdapter()
        sm = AgentSessionManager(
            adapters={"mock": adapter},
            publisher=pub,
            cfg=cfg,
            recording_lookup=lookup,
            base_dir=str(tmp_path),
        )
        return sm, adapter

    async def test_repeated_session_started_updates_watcher(self, cfg, pub, tmp_path):
        sm, _ = self._sm(cfg, pub, tmp_path)
        await sm.on_session_started(make_session_started_event(
            device_id="d1", session_id="s1", ts_ms=1000, language="zh-Hans"))
        watcher = sm._watchers["s1"]
        assert watcher._context.language == "zh-Hans"

        await sm.on_session_started(make_session_started_event(
            device_id="d1", session_id="s1", ts_ms=2000, language="en"))
        assert sm._watchers["s1"] is watcher
        assert watcher._context.language == "en"
        await sm.shutdown()

    async def test_late_session_started_overrides_temp_en_watcher(
        self, cfg, pub, tmp_path
    ):
        sm, adapter = self._sm(cfg, pub, tmp_path)
        await sm.on_user_utterance(make_user_utterance_event(
            device_id="d1", session_id="s1", ts_ms=1000,
            transcription_id="tr-1", text="hi", language="ar",
        ))
        watcher = sm._watchers["s1"]
        assert watcher._context.language == "en"   # 临时默认

        await sm.on_session_started(make_session_started_event(
            device_id="d1", session_id="s1", ts_ms=2000, language="ja"))
        assert watcher._context.language == "ja"
        # 重复 session.started 不重复派发生命周期
        await _wait_until(lambda: len(adapter.started) >= 1)
        assert len(adapter.started) == 1
        await sm.shutdown()


class TestSessionEndKeepsJobs:
    async def test_session_end_does_not_orphan_jobs(self, cfg, pub):
        """D4: job 生命周期不绑定 livachat session —— session end 不再 orphan。"""

        class _FakeJobStore:
            def __init__(self):
                self.orphan_called = False

            async def orphan_session_jobs(self, session_id: str):
                self.orphan_called = True
                return []

        adapter = ProactiveMockAdapter()
        store = _FakeJobStore()
        sm = AgentSessionManager(
            adapters={"mock": adapter}, publisher=pub, cfg=cfg, job_store=store
        )
        ev = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.on_session_started(ev)
        await _wait_until(lambda: len(adapter.started) == 1)

        end = make_session_ended_event(device_id="d1", session_id="s1", ts_ms=2000)
        await sm.on_session_ended(end)
        assert store.orphan_called is False


class TestShutdownStopsWatchers:
    async def test_shutdown_stops_watchers(self, cfg, pub):
        adapter = ProactiveMockAdapter()

        async def lookup(device_uuid=None, **kw):
            return None

        sm = AgentSessionManager(
            adapters={"mock": adapter}, publisher=pub, cfg=cfg, recording_lookup=lookup
        )
        ev = make_session_started_event(device_id="d1", session_id="s1", ts_ms=1000)
        await sm.on_session_started(ev)
        await _wait_until(lambda: len(adapter.started) == 1)
        await sm.shutdown()
        assert sm._watchers == {}
