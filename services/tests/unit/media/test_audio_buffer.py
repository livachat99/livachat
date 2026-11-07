from __future__ import annotations

from media.buffer import AudioFrameBuffer


class TestAudioFrameBuffer:
    def test_exact_frame_yields_one(self):
        buf = AudioFrameBuffer(frame_samples=480)
        pcm = b"\x00" * 960  # 480 samples * 2 bytes
        frames = buf.feed(pcm)
        assert len(frames) == 1
        assert len(frames[0]) == 960

    def test_partial_frame_buffered(self):
        buf = AudioFrameBuffer(frame_samples=480)
        frames = buf.feed(b"\x00" * 500)  # 250 samples < 480
        assert len(frames) == 0
        assert buf.pending_samples == 250

    def test_accumulate_to_full_frame(self):
        buf = AudioFrameBuffer(frame_samples=480)
        frames = buf.feed(b"\x00" * 600)  # 300 samples
        assert len(frames) == 0
        frames = buf.feed(b"\x00" * 400)  # +200 = 500 samples → 1 frame (480) + 20 left
        assert len(frames) == 1
        assert len(frames[0]) == 960
        assert buf.pending_samples == 20

    def test_multiple_frames(self):
        buf = AudioFrameBuffer(frame_samples=480)
        pcm = b"\x01" * (960 * 3)  # exactly 3 frames
        frames = buf.feed(pcm)
        assert len(frames) == 3
        assert all(len(f) == 960 for f in frames)

    def test_flush_returns_residue(self):
        buf = AudioFrameBuffer(frame_samples=480)
        buf.feed(b"\x00" * 500)  # 250 samples pending
        residue = buf.flush()
        assert residue is not None
        assert len(residue) == 500
        assert buf.pending_samples == 0

    def test_flush_empty_returns_none(self):
        buf = AudioFrameBuffer(frame_samples=480)
        assert buf.flush() is None

    def test_reset_discards_pending(self):
        buf = AudioFrameBuffer(frame_samples=480)
        buf.feed(b"\x00" * 500)
        buf.reset()
        assert buf.pending_samples == 0

    def test_ordering_preserved(self):
        buf = AudioFrameBuffer(frame_samples=480)
        pcm = bytes(i & 0xFF for i in range(960))  # unique pattern
        frames = buf.feed(pcm[:400]) + buf.feed(pcm[400:])
        assert len(frames) == 1
        assert frames[0] == pcm
