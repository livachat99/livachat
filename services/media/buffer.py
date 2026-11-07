from __future__ import annotations


class AudioFrameBuffer:
    """Accumulates arbitrary-length PCM16 input and yields exact-frame-size chunks.

    Used upstream of the Opus encoder so that every Opus frame is exactly
    ``frame_samples`` long (20 or 40 ms at the configured sample rate).
    """

    def __init__(self, frame_samples: int) -> None:
        self._frame_size = frame_samples
        self._pending = bytearray()

    def feed(self, pcm: bytes) -> list[bytes]:
        """Feed raw PCM16 bytes and return 0..N complete frames.

        Any leftover bytes are retained for the next :meth:`feed` call.
        Each frame is exactly ``frame_samples * 2`` bytes.
        """
        self._pending.extend(pcm)
        frames: list[bytes] = []
        frame_bytes = self._frame_size * 2

        while len(self._pending) >= frame_bytes:
            frames.append(bytes(self._pending[:frame_bytes]))
            self._pending = self._pending[frame_bytes:]

        return frames

    def flush(self) -> bytes | None:
        """Return any remaining buffered PCM (shorter than a full frame).

        Returns None if the buffer is already empty.
        """
        if self._pending:
            residue = bytes(self._pending)
            self._pending.clear()
            return residue
        return None

    def reset(self) -> None:
        """Discard all buffered PCM without yielding it."""
        self._pending.clear()

    @property
    def pending_samples(self) -> int:
        return len(self._pending) // 2

    @property
    def frame_size(self) -> int:
        return self._frame_size
