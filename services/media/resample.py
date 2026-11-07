from __future__ import annotations

import numpy as np
import soxr  # type: ignore[import-untyped]


class Resampler:
    """Stateful PCM16 resampler for continuous streaming.

    Uses soxr (high-quality, constant-rate).  Create **one** instance per
    audio stream and call :meth:`process` repeatedly.
    """

    def __init__(self, input_rate: int, output_rate: int, quality: str = "HQ") -> None:
        self._input_rate = input_rate
        self._output_rate = output_rate
        self._resampler = soxr.ResampleStream(
            in_rate=float(input_rate),
            out_rate=float(output_rate),
            num_channels=1,
            dtype="int16",
            quality=quality,
        )

    def process(self, pcm: bytes) -> bytes:
        """Resample *pcm* (PCM16 mono) and return the resampled PCM16 bytes.

        May return an empty ``b""`` if the resampler has not yet accumulated
        enough input to produce output samples.
        """
        if not pcm:
            return b""
        arr = np.frombuffer(pcm, dtype=np.int16)
        out = self._resampler.resample_chunk(arr, last=False)
        if out.size == 0:
            return b""
        return out.tobytes()  # type: ignore[no-any-return]

    def flush(self) -> bytes:
        """Return any buffered (tail) samples and reset internal state."""
        arr = np.array([], dtype=np.int16)
        out = self._resampler.resample_chunk(arr, last=True)
        if out.size == 0:
            return b""
        return out.tobytes()  # type: ignore[no-any-return]

    @property
    def input_rate(self) -> int:
        return self._input_rate

    @property
    def output_rate(self) -> int:
        return self._output_rate
