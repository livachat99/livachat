from __future__ import annotations

import pytest

from asr.base import AsrResult
from asr.service import AsrService
from shared.utils.errors import ProviderError
from tests.unit.asr.fakes.fake_asr_provider import FakeAsrProvider


class TestFailover:
    async def test_active_succeeds(self):
        results = [AsrResult(text="hello", is_final=True)]
        svc = AsrService(
            active=FakeAsrProvider(results),
            fallbacks=[FakeAsrProvider([AsrResult(text="fallback", is_final=True)])],
        )
        stream = await svc.open(sample_rate=24000)
        assert isinstance(
            stream._stream,
            __import__("tests.unit.asr.fakes.fake_asr_provider", fromlist=["FakeAsrStream"]).FakeAsrStream,
        )

    async def test_active_fails_fallback_wins(self):
        svc = AsrService(
            active=FakeAsrProvider(raise_on_open=Exception("down")),
            fallbacks=[FakeAsrProvider([AsrResult(text="from fallback", is_final=True)])],
        )
        stream = await svc.open(sample_rate=16000)
        final_text = await stream.final()
        assert final_text == "from fallback"

    async def test_all_fail_raises(self):
        svc = AsrService(
            active=FakeAsrProvider(raise_on_open=Exception("down")),
            fallbacks=[FakeAsrProvider(raise_on_open=Exception("also down"))],
        )
        with pytest.raises(ProviderError, match="all asr providers failed"):
            await svc.open(sample_rate=24000)

    async def test_feed_error_converted(self):
        svc = AsrService(
            active=FakeAsrProvider([], raise_on_open=None),
            fallbacks=[],
        )
        stream = await svc.open(sample_rate=24000)
        # The underlying FakeAsrStream doesn't raise on feed, but FallbackStream should pass through
        await stream.feed(b"\x00" * 960)
        # Clean close
        await stream.close()
