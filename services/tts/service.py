from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from shared.utils.errors import ErrorCode, ProviderError
from tts.base import TtsProvider, TtsStream

logger = logging.getLogger("livachat.tts.service")


class _FallbackStream:
    def __init__(self, stream: TtsStream, provider_name: str) -> None:
        self._stream = stream
        self._provider_name = provider_name

    async def feed_text(self, text: str) -> None:
        try:
            await self._stream.feed_text(text)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), provider=self._provider_name, code=ErrorCode.TTS_UNAVAILABLE) from exc

    async def audio(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream.audio():
            yield chunk

    async def poll_audio(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream.poll_audio():
            yield chunk

    async def flush(self) -> None:
        await self._stream.flush()

    async def reset(self) -> None:
        try:
            await self._stream.reset()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), provider=self._provider_name, code=ErrorCode.TTS_UNAVAILABLE) from exc

    async def close(self) -> None:
        await self._stream.close()


class TtsService:
    """Failover-aware TTS service.

    On :meth:`open`, tries providers in ``[active, *fallback]`` order.
    The first successful ``open()`` wins.
    """

    def __init__(self, active: TtsProvider, fallbacks: list[TtsProvider]) -> None:
        self._providers = [active] + fallbacks

    async def open(self, *, voice: str, sample_rate: int, audio_format: str = "pcm") -> TtsStream:
        last_err: Exception | None = None
        for provider in self._providers:
            try:
                stream = await provider.open(voice=voice, sample_rate=sample_rate, audio_format=audio_format)
                logger.info("tts provider opened", extra={"provider": provider.name})
                return _FallbackStream(stream, provider.name)
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "tts provider open failed, trying next", extra={"provider": provider.name, "error": str(exc)}
                )

        raise ProviderError(
            f"all tts providers failed to open: {last_err}",
            provider="tts",
            code=ErrorCode.TTS_UNAVAILABLE,
        ) from last_err
