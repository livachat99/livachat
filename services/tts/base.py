from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class TtsStream(Protocol):
    """Session-scoped TTS stream reused across utterances.

    Call ``feed_text()`` with incremental text, iterate ``audio()`` for PCM16
    chunks, and call ``flush()`` to signal end-of-text.  Call ``reset()``
    between utterances to prepare the stream for the next turn without
    closing the underlying connection.
    """

    async def feed_text(self, text: str) -> None:
        """Push incremental text delta into the stream."""
        ...

    async def audio(self) -> AsyncIterator[bytes]:
        """Async generator yielding PCM16 mono audio chunks."""
        ...

    async def flush(self) -> None:
        """Signal end of text input and wait for remaining audio."""
        ...

    async def poll_audio(self) -> AsyncIterator[bytes]:
        """Yield immediately-available audio chunks, then return. Non-blocking."""
        ...

    async def reset(self) -> None:
        """Reset stream state for a new utterance.  Does NOT close the connection."""
        ...

    async def close(self) -> None:
        """Close the underlying connection and free resources."""
        ...


class TtsProvider(Protocol):
    """Factory that opens a TTS stream."""

    name: str

    async def open(
        self,
        *,
        voice: str,
        sample_rate: int,
        audio_format: str = "pcm",
    ) -> TtsStream: ...
