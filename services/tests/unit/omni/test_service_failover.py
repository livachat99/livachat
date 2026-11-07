from __future__ import annotations

import pytest

from omni.base import OmniEvent, OmniEventType
from omni.service import OmniService
from shared.utils.errors import ErrorCode, ProviderError
from tests.unit.omni.fakes.fake_omni_provider import FakeOmniProvider


class TestOmniServiceFailover:
    async def test_active_succeeds(self) -> None:
        """Active provider open() succeeds → returns stream + name, no fallback touched."""
        active = FakeOmniProvider("active", events=[OmniEvent(type=OmniEventType.SPEECH_STOPPED)])
        fallback = FakeOmniProvider("fallback", events=[OmniEvent(type=OmniEventType.SPEECH_STOPPED)])
        svc = OmniService(active=active, fallbacks=[fallback])

        stream, name = await svc.open(sample_rate=16000)
        assert name == "active"
        assert stream is not None

    async def test_first_fails_second_succeeds(self) -> None:
        """Active fails → tries fallback → returns fallback stream + name."""
        active = FakeOmniProvider("active", raise_on_open=ProviderError("fail", provider="active"))
        fallback = FakeOmniProvider("fallback", events=[OmniEvent(type=OmniEventType.SPEECH_STOPPED)])
        svc = OmniService(active=active, fallbacks=[fallback])

        stream, name = await svc.open(sample_rate=16000)
        assert name == "fallback"
        assert stream is not None

    async def test_all_fail(self) -> None:
        """All providers fail → raises ProviderError with OMNI_UNAVAILABLE."""
        active = FakeOmniProvider("a", raise_on_open=RuntimeError("dead"))
        fallback = FakeOmniProvider("b", raise_on_open=RuntimeError("also dead"))
        svc = OmniService(active=active, fallbacks=[fallback])

        with pytest.raises(ProviderError) as exc_info:
            await svc.open(sample_rate=16000)
        assert exc_info.value.code == ErrorCode.OMNI_UNAVAILABLE
        assert exc_info.value.provider == "omni"

    async def test_open_kwargs_forwarded(self) -> None:
        """Keyword arguments are forwarded to provider.open()."""
        active = FakeOmniProvider("a", events=[OmniEvent(type=OmniEventType.SPEECH_STOPPED)])
        svc = OmniService(active=active, fallbacks=[])

        stream, name = await svc.open(
            sample_rate=24000,
            system_prompt="hello",
            voice="Cherry",
            modalities=("text",),
            generation_opts={"temperature": 0.5},
        )
        assert name == "a"
        assert stream is not None
        assert active.last_open_kwargs is not None
        assert active.last_open_kwargs["sample_rate"] == 24000
        assert active.last_open_kwargs["system_prompt"] == "hello"
        assert active.last_open_kwargs["voice"] == "Cherry"
        assert active.last_open_kwargs["modalities"] == ("text",)
        assert active.last_open_kwargs["generation_opts"] == {"temperature": 0.5}
