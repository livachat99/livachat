from __future__ import annotations

import pytest

from agent_relay.adapters.base import (
    AgentAdapter,
    AgentContext,
    AgentResponseChunk,
    RecordingInfo,
)


class TestAgentContext:
    def test_defaults(self):
        ctx = AgentContext(session_id="s1", device_id="d1", language="en")
        assert ctx.session_id == "s1"
        assert ctx.device_id == "d1"
        assert ctx.language == "en"


class TestAgentResponseChunk:
    def test_defaults(self):
        chunk = AgentResponseChunk(text="hello")
        assert chunk.is_final is False
        assert chunk.metadata is None

    def test_is_final(self):
        chunk = AgentResponseChunk(text="done", is_final=True)
        assert chunk.is_final is True

    def test_with_metadata(self):
        chunk = AgentResponseChunk(text="x", metadata={"source": "web"})
        assert chunk.metadata == {"source": "web"}


class TestRecordingInfo:
    def test_all_fields(self):
        info = RecordingInfo(
            recording_id="rec-1",
            session_id="sess-1",
            device_id="dev-1",
            manifest_path="/tmp/manifest.json",
            duration_ms=60000,
            transcript_endpoint="http://localhost/v1/recordings/rec-1/transcript",
        )
        assert info.recording_id == "rec-1"
        assert info.session_id == "sess-1"
        assert info.device_id == "dev-1"
        assert info.manifest_path == "/tmp/manifest.json"
        assert info.duration_ms == 60000
        assert info.transcript_endpoint == "http://localhost/v1/recordings/rec-1/transcript"


class TestAgentAdapterABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            AgentAdapter()  # type: ignore[abstract]

    def test_subclass_must_implement_ask(self):
        class Incomplete(AgentAdapter):
            @property
            def name(self) -> str:
                return "incomplete"

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_must_implement_name(self):
        class NoName(AgentAdapter):
            async def ask(self, text, context):
                if False:
                    yield

        with pytest.raises(TypeError):
            NoName()  # type: ignore[abstract]

    def test_connect_close_default_noop(self):
        class Minimal(AgentAdapter):
            name = "minimal"

            async def ask(self, text, context):
                if False:
                    yield

        adapter = Minimal()
        # Should not raise
        import asyncio

        async def run():
            await adapter.connect()
            await adapter.close()

        asyncio.run(run())

    def test_nickname_defaults_empty(self):
        class Minimal(AgentAdapter):
            name = "minimal"

            async def ask(self, text, context):
                if False:
                    yield

        assert Minimal().nickname == ""

    def test_analyze_recording_default_noop(self):
        class Minimal(AgentAdapter):
            name = "minimal"

            async def ask(self, text, context):
                if False:
                    yield

        adapter = Minimal()
        import asyncio

        async def run():
            ctx = AgentContext(session_id="s", device_id="d", language="en")
            info = RecordingInfo(
                recording_id="r",
                session_id="s",
                device_id="d",
                manifest_path="/tmp/m.json",
                duration_ms=10000,
                transcript_endpoint="http://localhost/t",
            )
            chunks = [c async for c in adapter.analyze_recording(info, ctx)]
            assert chunks == []

        asyncio.run(run())
