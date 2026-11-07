from __future__ import annotations

import math
import struct

from media.resample import Resampler


def _make_sine(samples: int, freq: float = 440.0, sample_rate: int = 24000) -> bytes:
    vals = [int(8000 * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(samples)]
    return struct.pack("<" + "h" * samples, *vals)


class TestResampler:
    def test_24k_to_16k_downsample(self):
        resampler = Resampler(input_rate=24000, output_rate=16000)
        # Feed enough data for the resampler to produce output (needs internal buffer fill)
        pcm = _make_sine(4800, freq=440.0, sample_rate=24000)  # 200ms of audio
        out = resampler.process(pcm)
        tail = resampler.flush()
        total = out + tail
        # 24k → 16k: ratio = 16/24, output ≈ 4800 * 16/24 = 3200 samples
        expected_samples = int(4800 * 16 / 24)
        actual_samples = len(total) // 2
        assert abs(actual_samples - expected_samples) <= 5, f"Expected ~{expected_samples}, got {actual_samples}"

    def test_16k_to_24k_upsample(self):
        resampler = Resampler(input_rate=16000, output_rate=24000)
        pcm = _make_sine(3200, freq=440.0, sample_rate=16000)  # 200ms
        out = resampler.process(pcm)
        tail = resampler.flush()
        total = out + tail
        # 16k → 24k: ratio = 24/16, output ≈ 3200 * 24/16 = 4800 samples
        expected_samples = int(3200 * 24 / 16)
        actual_samples = len(total) // 2
        assert abs(actual_samples - expected_samples) <= 5

    def test_statefulness_across_chunks(self):
        resampler = Resampler(input_rate=24000, output_rate=16000)
        pcm = _make_sine(4800, freq=440.0, sample_rate=24000)  # 200ms
        half = len(pcm) // 2
        out1 = resampler.process(pcm[:half])
        out2 = resampler.process(pcm[half:])
        out_combined = out1 + out2
        assert len(out_combined) > 0

    def test_flush_returns_tail(self):
        resampler = Resampler(input_rate=24000, output_rate=16000)
        pcm = _make_sine(4800, freq=440.0, sample_rate=24000)
        resampler.process(pcm)
        tail = resampler.flush()
        assert isinstance(tail, bytes)

    def test_empty_input_returns_empty(self):
        resampler = Resampler(input_rate=24000, output_rate=16000)
        out = resampler.process(b"")
        assert out == b""
