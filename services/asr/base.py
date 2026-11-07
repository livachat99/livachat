from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class AsrResult:
    text: str
    is_final: bool
    confidence: float | None = None
    transcription_id: str = ""
    language: str | None = None


class AsrStream(Protocol):
    """Per-utterance ASR stream.

    Call ``feed()`` repeatedly with PCM16 mono audio, iterate ``results()``
    for streaming transcripts, and call ``final()`` to signal end-of-utterance.
    """

    async def feed(self, pcm: bytes) -> None:
        """Push raw PCM16 mono audio into the stream."""
        ...

    def results(self) -> AsyncIterator[AsrResult]:
        """Async generator yielding interim and final results."""
        ...

    async def final(self) -> str:
        """Signal end of utterance and return the final transcript text."""
        ...

    async def reset(self) -> None:
        """Reset stream state for a new utterance."""
        ...

    async def close(self) -> None:
        """Close the underlying connection and free resources."""
        ...

    @property
    def final_text(self) -> str:
        """The accumulated final transcript text for the current utterance."""
        ...


class AsrProvider(Protocol):
    """Factory that opens an ASR stream.

    Stateless aside from configuration; one :class:`AsrStream` per utterance.
    """

    name: str

    async def open(
        self,
        *,
        sample_rate: int,
        language: str | None = None,
        interim_results: bool = True,
    ) -> AsrStream: ...
