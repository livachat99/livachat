from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from gateway.auth import Principal
from gateway.codec import WireCodec
from gateway.seq import OutboundSeqAllocator
from session.manager import SessionManager
from session.orchestrator import DialogOrchestrator


class FakeChatStore:
    """同步钩子记录调用（不落盘）；orchestrator 直构集成测试用。"""

    def __init__(self):
        self.devices = []
        self.messages = []
        self.deleted_messages = []

    def on_device(self, **kwargs) -> None:
        self.devices.append(kwargs)

    def on_message(self, **kwargs) -> None:
        self.messages.append(kwargs)

    def delete_message(self, *, device_uuid: str, session_id: str, kind: str,
                       role: str, ts_ms: int) -> None:
        self.deleted_messages.append({
            "device_uuid": device_uuid, "session_id": session_id,
            "kind": kind, "role": role, "ts_ms": ts_ms,
        })
        self.messages = [
            m for m in self.messages
            if not (m.get("device_uuid") == device_uuid
                    and m.get("session_id") == session_id
                    and m.get("kind") == kind
                    and m.get("role") == role
                    and m.get("ts_ms") == ts_ms)
        ]


class FakeRecordingSink:
    """recording tap 钩子记录（video 文本回合三件一体断言用）。"""

    def __init__(self):
        self.chat_texts: list[dict] = []

    def on_chat_text(self, **kwargs) -> None:
        self.chat_texts.append(kwargs)


class _FakeHandler:
    """Minimal ConnectionHandler stub that captures outbound frames."""

    def __init__(self, device_id: str = "device-1"):
        alloc = OutboundSeqAllocator()
        self._codec = WireCodec(alloc)
        self._stopped = False
        self._inbound: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.principal = Principal(user_id="u", device_id=device_id)
        self.outbound: list[str] = []

    async def enqueue_outbound(self, frame: str) -> None:
        self.outbound.append(frame)

    def stop(self) -> None:
        self._stopped = True


class FakeLlmService:
    """LLM that yields canned text tokens (strings, matching the real LlmProvider protocol)."""

    def __init__(self, tokens: list[str] | None = None):
        self._tokens = tokens or ["Hello", " world!"]
        self.calls: list[dict] = []

    async def stream(self, *, messages, images=None, **opts) -> AsyncIterator[str]:
        self.calls.append({"messages": messages, "images": images, **opts})
        for t in self._tokens:
            yield t


class FakeAsrService:
    async def open(self, *, sample_rate, language=None, interim_results=True):
        return _FakeAsrStream()


class _FakeAsrStream:
    def __init__(self):
        self._final_text = "user text"

    async def feed(self, pcm):
        pass

    async def results(self):
        yield None

    async def final(self):
        return self._final_text

    @property
    def final_text(self) -> str:
        return self._final_text

    async def reset(self):
        self._final_text = ""

    async def close(self):
        pass


class FakeTtsService:
    async def open(self, *, voice, sample_rate, audio_format="pcm"):
        return _FakeTtsStream()


class _FakeTtsStream:
    async def feed_text(self, text):
        pass

    def audio(self):
        # Return an empty async generator — no audio chunks
        async def _empty():
            if False:
                yield
        return _empty()

    async def flush(self):
        pass

    async def reset(self):
        pass

    async def close(self):
        pass


@pytest.fixture
def fake_handler():
    return _FakeHandler()


@pytest.fixture
def fake_orchestrator(fake_handler):
    return DialogOrchestrator(
        handler=fake_handler,
        codec=fake_handler._codec,
        session_mgr=SessionManager(),
        asr_service=FakeAsrService(),
        llm_service=FakeLlmService(["Hello", " world!"]),
        tts_service=FakeTtsService(),
        system_prompt="You are helpful.",
        chat_store=FakeChatStore(),
    )


@pytest.fixture
def fake_recording_sink():
    return FakeRecordingSink()


@pytest.fixture
def fake_orchestrator_with_sink(fake_handler, fake_recording_sink):
    """带 recording tap 的 orchestrator（video 文本回合三件一体断言用）。"""
    return DialogOrchestrator(
        handler=fake_handler,
        codec=fake_handler._codec,
        session_mgr=SessionManager(),
        asr_service=FakeAsrService(),
        llm_service=FakeLlmService(["Hello", " world!"]),
        tts_service=FakeTtsService(),
        system_prompt="You are helpful.",
        chat_store=FakeChatStore(),
        recording_sink=fake_recording_sink,
    )
