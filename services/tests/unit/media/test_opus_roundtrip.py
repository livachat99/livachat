from __future__ import annotations

import math
import struct

import pytest

from media.opus import OpusDecoder, OpusEncoder


def _make_sine(samples: int, freq: float = 440.0, sample_rate: int = 24000, amplitude: int = 16000) -> bytes:
    """Generate PCM16 mono sine wave."""
    vals = [int(amplitude * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(samples)]
    return struct.pack("<" + "h" * samples, *vals)


def _max_cross_correlation(a: list[int], b: list[int], max_lag: int = 200) -> float:
    """Find the maximum normalised cross-correlation between *a* and *b* within ±max_lag samples."""
    best = 0.0
    for lag in range(max_lag):
        # Shift b forward relative to a
        dot = sum(a[i] * b[i + lag] for i in range(min(len(a), len(b) - lag)))
        mag_a = math.sqrt(sum(x * x for x in a[: len(a) - lag]))
        mag_b = math.sqrt(sum(x * x for x in b[lag : lag + len(a) - lag]))
        if mag_a > 0 and mag_b > 0:
            corr = dot / (mag_a * mag_b)
            best = max(best, corr)
    return best


class TestOpusRoundtrip:
    @pytest.mark.parametrize("sample_rate,frame_ms", [(24000, 20), (24000, 40), (16000, 20)])
    def test_silence_roundtrip(self, sample_rate, frame_ms):
        enc = OpusEncoder(sample_rate=sample_rate, frame_ms=frame_ms)
        dec = OpusDecoder(sample_rate=sample_rate, frame_ms=frame_ms)
        samples = enc.frame_size
        pcm = _make_sine(
            samples, freq=0.0, sample_rate=sample_rate
        )  # silence via amplitude 0... wait this isn't silence

        # Actually use true silence
        pcm = b"\x00" * (samples * 2)
        opus_pkt = enc.encode(pcm)
        assert len(opus_pkt) > 0
        assert len(opus_pkt) < 200  # silence should compress very well

        pcm_out = dec.decode(opus_pkt)
        assert len(pcm_out) == len(pcm)
        enc.close()
        dec.close()

    @pytest.mark.parametrize("sample_rate,frame_ms", [(24000, 20), (24000, 40), (16000, 20)])
    def test_sine_roundtrip_shape(self, sample_rate, frame_ms):
        enc = OpusEncoder(sample_rate=sample_rate, frame_ms=frame_ms)
        dec = OpusDecoder(sample_rate=sample_rate, frame_ms=frame_ms)
        samples = enc.frame_size

        # Generate enough frames for a meaningful measurement
        total_samples = samples * 10
        pcm = _make_sine(total_samples, freq=440.0, sample_rate=sample_rate, amplitude=8000)
        in_vals = struct.unpack("<" + "h" * total_samples, pcm)

        # Encode frame-by-frame
        frame_bytes = samples * 2
        opus_packets = [enc.encode(pcm[i : i + frame_bytes]) for i in range(0, len(pcm), frame_bytes)]

        # Decode
        out_chunks = [dec.decode(pkt) for pkt in opus_packets]
        pcm_out = b"".join(out_chunks)
        out_vals = struct.unpack("<" + "h" * (len(pcm_out) // 2), pcm_out)

        # Skip first 2 frames (codec warm-up) and use the stable middle portion
        skip = samples * 2
        window = samples * 5
        in_slice = in_vals[skip : skip + window]
        out_slice = out_vals[skip : skip + window]

        corr = _max_cross_correlation(in_slice, out_slice)
        assert corr > 0.8, f"Cross-correlation too low: {corr:.3f}"

        enc.close()
        dec.close()

    def test_encoder_reset_clears_state(self):
        enc = OpusEncoder(sample_rate=24000, frame_ms=20)
        pcm = _make_sine(480, freq=440.0)
        enc.encode(pcm)
        enc.reset()
        # After reset, encoding should still work
        pcm2 = _make_sine(480, freq=880.0)
        pkt = enc.encode(pcm2)
        assert len(pkt) > 0
        enc.close()

    def test_decoder_reset_clears_state(self):
        dec = OpusDecoder(sample_rate=24000, frame_ms=20)
        enc = OpusEncoder(sample_rate=24000, frame_ms=20)
        pkt = enc.encode(_make_sine(480))
        dec.decode(pkt)
        dec.reset()
        # After reset, decoding should still work
        pcm_out = dec.decode(pkt)
        assert len(pcm_out) == 960
        enc.close()
        dec.close()

    def test_decode_missing_plc(self):
        dec = OpusDecoder(sample_rate=24000, frame_ms=20)
        pcm = dec.decode_missing()
        assert len(pcm) == 960  # 480 samples * 2 bytes
        dec.close()

    def test_opus_packet_small(self):
        """Opus packet for a 20ms frame should be < 200 bytes at 32kbps."""
        enc = OpusEncoder(sample_rate=24000, frame_ms=20)
        pcm = _make_sine(480, freq=440.0)
        pkt = enc.encode(pcm)
        assert len(pkt) < 200, f"Packet too large: {len(pkt)} bytes"
        enc.close()

    def test_rejects_multichannel(self):
        """Only mono PCM buffers are implemented; channels>1 must fail fast."""
        with pytest.raises(ValueError, match="mono only"):
            OpusEncoder(channels=2)
        with pytest.raises(ValueError, match="mono only"):
            OpusDecoder(channels=2)
