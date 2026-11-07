from __future__ import annotations

import pytest

from avdetector.service import AvDetectorService
from shared.utils.errors import ErrorCode, ProviderError
from tests.unit.avdetector.fakes.fake_avdetector_provider import FakeAvDetectorProvider


class TestAvDetectorServiceFailover:
    async def test_active_succeeds(self) -> None:
        active = FakeAvDetectorProvider("active")
        fallback = FakeAvDetectorProvider("fallback")
        svc = AvDetectorService(active=active, fallbacks=[fallback])

        stream, name = await svc.open(sample_rate=16000)
        assert name == "active"
        assert stream is active.stream

    async def test_first_fails_second_succeeds(self) -> None:
        active = FakeAvDetectorProvider(
            "active", raise_on_open=ProviderError("fail", provider="active")
        )
        fallback = FakeAvDetectorProvider("fallback")
        svc = AvDetectorService(active=active, fallbacks=[fallback])

        stream, name = await svc.open(sample_rate=16000)
        assert name == "fallback"
        assert stream is fallback.stream

    async def test_all_fail(self) -> None:
        active = FakeAvDetectorProvider("a", raise_on_open=RuntimeError("dead"))
        fallback = FakeAvDetectorProvider("b", raise_on_open=RuntimeError("also dead"))
        svc = AvDetectorService(active=active, fallbacks=[fallback])

        with pytest.raises(ProviderError) as exc_info:
            await svc.open(sample_rate=16000)
        assert exc_info.value.code == ErrorCode.AVDETECTOR_UNAVAILABLE
        assert exc_info.value.provider == "avdetector"

    async def test_open_kwargs_forwarded(self) -> None:
        active = FakeAvDetectorProvider("a")
        svc = AvDetectorService(active=active, fallbacks=[])

        stream, name = await svc.open(
            sample_rate=24000,
            system_prompt="sys",
            generation_opts={"temperature": 0.2},
        )
        assert name == "a"
        assert stream is not None
        assert active.last_open_kwargs == {
            "sample_rate": 24000,
            "system_prompt": "sys",
            "generation_opts": {"temperature": 0.2},
        }
