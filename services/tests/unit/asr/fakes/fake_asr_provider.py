from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from asr.base import AsrResult, AsrStream


class FakeAsrStream:
    """Stream that replays a predefined sequence of results."""

    def __init__(self, results: list[AsrResult], *, raise_on_feed: Exception | None = None) -> None:
        self._results = results
        self._raise_on_feed = raise_on_feed
        self._fed: list[bytes] = []
        self._final_text = ""

    async def feed(self, pcm: bytes) -> None:
        if self._raise_on_feed:
            raise self._raise_on_feed
        self._fed.append(pcm)

    async def results(self) -> AsyncIterator[AsrResult]:
        for r in self._results:
            if r.is_final and r.text:
                self._final_text = r.text
            yield r
            await asyncio.sleep(0)

    @property
    def final_text(self) -> str:
        return self._final_text

    async def final(self) -> str:
        for r in reversed(self._results):
            if r.is_final:
                return r.text
        return ""

    async def reset(self) -> None:
        self._fed.clear()

    async def close(self) -> None:
        pass


class FakeAsrProvider:
    """Provider that returns :class:`FakeAsrStream` instances.

    Set ``raise_on_open`` to simulate a provider that fails to connect.
    """

    name = "fake"

    def __init__(self, results: list[AsrResult] | None = None, *, raise_on_open: Exception | None = None) -> None:
        self._results = results or []
        self._raise_on_open = raise_on_open

    async def open(
        self,
        *,
        sample_rate: int,
        language: str | None = None,
        interim_results: bool = True,
    ) -> AsrStream:
        if self._raise_on_open:
            raise self._raise_on_open
        return FakeAsrStream(list(self._results))
