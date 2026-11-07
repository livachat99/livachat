from __future__ import annotations

import pytest

from shared.config.models import TtsConfig, TtsProviderConfig
from shared.utils.errors import ConfigError, ProviderError
from tts.factory import create_tts
from tts.service import TtsService


def _make_tts_config(active: str, providers: dict) -> TtsConfig:
    return TtsConfig(active=active, fallback=[], providers=providers)


class TestFactory:
    def test_create_cartesia(self):
        cfg = _make_tts_config(
            "cartesia",
            {
                "cartesia": TtsProviderConfig(
                    enabled=True, base_url="wss://test", api_key="k", model="sonic-2", voice="v"
                )
            },
        )
        svc = create_tts(cfg)
        assert isinstance(svc, TtsService)

    def test_unknown_provider_raises(self):
        cfg = _make_tts_config("foo", {})
        with pytest.raises(ConfigError):
            create_tts(cfg)


class TestFailover:
    async def test_active_succeeds(self):

        class _FakeStream:
            async def feed_text(self, text):
                pass

            async def audio(self):
                yield b"\x00"

            async def flush(self):
                pass

            async def close(self):
                pass

        class _FakeProvider:
            name = "fake"

            async def open(self, *, voice, sample_rate, audio_format="pcm"):
                return _FakeStream()

        svc = TtsService(active=_FakeProvider(), fallbacks=[])
        stream = await svc.open(voice="v", sample_rate=24000)
        buf = b""
        async for chunk in stream.audio():
            buf += chunk
        assert len(buf) > 0

    async def test_all_fail_raises(self):
        class _FailingProvider:
            name = "fail"

            async def open(self, *, voice, sample_rate, audio_format="pcm"):
                raise RuntimeError("down")

        svc = TtsService(active=_FailingProvider(), fallbacks=[_FailingProvider()])
        with pytest.raises(ProviderError):
            await svc.open(voice="v", sample_rate=24000)
