from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from asr.base import AsrProvider, AsrResult, AsrStream
from shared.utils.errors import ErrorCode, ProviderError

logger = logging.getLogger("livachat.asr.service")


class _FallbackStream:
    """Delegates to the active stream, surfacing provider errors upward."""

    def __init__(self, stream: AsrStream, provider_name: str) -> None:
        self._stream = stream
        self._provider_name = provider_name

    async def feed(self, pcm: bytes) -> None:
        try:
            await self._stream.feed(pcm)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), provider=self._provider_name, code=ErrorCode.ASR_UNAVAILABLE) from exc

    def results(self) -> AsyncIterator[AsrResult]:
        return self._stream.results()

    async def final(self) -> str:
        try:
            return await self._stream.final()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), provider=self._provider_name, code=ErrorCode.ASR_UNAVAILABLE) from exc

    @property
    def final_text(self) -> str:
        return self._stream.final_text

    async def reset(self) -> None:
        await self._stream.reset()

    async def close(self) -> None:
        await self._stream.close()


class AsrService:
    """Failover-aware ASR service.

    On :meth:`open`, tries providers in ``[active, *fallback]`` order.
    The first successful ``open()`` wins.  If all fail, raises :class:`ProviderError`.
    """

    def __init__(self, active: AsrProvider, fallbacks: list[AsrProvider]) -> None:
        self._providers = [active] + fallbacks

    async def open(
        self,
        *,
        sample_rate: int,
        language: str | None = None,
        interim_results: bool = True,
    ) -> AsrStream:
        last_err: Exception | None = None
        for provider in self._providers:
            try:
                stream = await provider.open(
                    sample_rate=sample_rate,
                    language=language,
                    interim_results=interim_results,
                )
                logger.info("asr provider opened", extra={"provider": provider.name})
                return _FallbackStream(stream, provider.name)
            except ProviderError as exc:
                last_err = exc
                logger.warning(
                    "asr provider open failed, trying next",
                    extra={"provider": provider.name, "error": str(exc)},
                )
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "asr provider open failed, trying next", extra={"provider": provider.name, "error": str(exc)}
                )

        raise ProviderError(
            f"all asr providers failed to open: {last_err}",
            provider="asr",
            code=ErrorCode.ASR_UNAVAILABLE,
        ) from last_err
