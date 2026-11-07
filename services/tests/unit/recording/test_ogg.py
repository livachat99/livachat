"""Unit tests for recording.ogg — OggS/Opus container writer."""

import struct
import tempfile
from pathlib import Path

from recording.ogg import OggOpusWriter, _make_opus_head, _make_opus_tags, _ogg_crc32


def make_silence_packet() -> bytes:
    """Minimal Opus-like packet for testing."""
    return bytes([0xFC, 0xFF, 0xFE])


def parse_ogg_pages(filepath: Path) -> list[dict]:
    """Parse all OggS pages from a file, returning page metadata."""
    pages = []
    with open(filepath, "rb") as f:
        data = f.read()

    pos = 0
    while pos < len(data):
        if data[pos:pos + 4] != b"OggS":
            break
        _version, flags, granule = struct.unpack_from("<BBq", data, pos + 4)
        _serial, seq, crc_stored, num_segs = struct.unpack_from("<IIIB", data, pos + 14)

        header_end = pos + 27 + num_segs
        seg_table = list(data[pos + 27 : header_end])

        payload = b""
        dp = header_end
        for lv in seg_table:
            payload += data[dp : dp + lv]
            dp += lv

        # Verify CRC
        page_for_crc = bytearray(data[pos : pos + 22])
        page_for_crc.extend(b"\x00\x00\x00\x00")
        page_for_crc.extend(data[pos + 26 : header_end])
        page_for_crc.extend(payload)
        crc_computed = _ogg_crc32(bytes(page_for_crc))

        pages.append({
            "flags": flags,
            "granule": granule,
            "seq": seq,
            "num_segs": num_segs,
            "seg_table": seg_table,
            "payload": payload,
            "crc_stored": crc_stored,
            "crc_ok": crc_stored == crc_computed,
        })
        pos = dp

    return pages


class TestOggOpusWriter:
    def test_single_packet(self):
        """One packet creates a valid OGG with OpusHead + OpusTags + audio + EOS."""
        tmp = Path(tempfile.mkdtemp()) / "test.ogg"
        w = OggOpusWriter(tmp, sample_rate=16000)
        w.open()
        w.write_packet(make_silence_packet(), duration_ms=20)
        w.close()

        pages = parse_ogg_pages(tmp)
        assert len(pages) == 3, f"Expected 3 pages, got {len(pages)}"

        # Page 1: OpusHead (BOS)
        assert pages[0]["flags"] & 0x02  # BOS
        assert pages[0]["payload"][:8] == b"OpusHead"
        assert pages[0]["crc_ok"]

        # Page 2: OpusTags
        assert pages[1]["payload"][:8] == b"OpusTags"
        assert pages[1]["crc_ok"]

        # Page 3: audio + EOS
        assert pages[2]["flags"] & 0x04  # EOS
        assert pages[2]["granule"] == 960  # 20ms @ 48kHz
        assert pages[2]["crc_ok"]

        assert w.packets_written == 1
        tmp.unlink()
        tmp.parent.rmdir()

    def test_packet_count(self):
        """50 packets (1s) produce correct granule position."""
        tmp = Path(tempfile.mkdtemp()) / "test.ogg"
        w = OggOpusWriter(tmp, sample_rate=16000)
        w.open()
        for _ in range(50):
            w.write_packet(make_silence_packet(), duration_ms=20)
        w.close()

        pages = parse_ogg_pages(tmp)
        # Last non-header page (before EOS) has granule = 50 * 960 = 48000
        data_pages = [p for p in pages if (p["flags"] & 0x02) == 0 and p["payload"]]
        assert len(data_pages) >= 1
        last_data = data_pages[-1]
        assert last_data["granule"] == 50 * 960

        assert w.packets_written == 50
        tmp.unlink()
        tmp.parent.rmdir()

    def test_all_crc_valid(self):
        """Every page in the output has valid CRC."""
        tmp = Path(tempfile.mkdtemp()) / "test.ogg"
        w = OggOpusWriter(tmp, sample_rate=16000)
        w.open()
        for _ in range(100):
            w.write_packet(make_silence_packet(), duration_ms=20)
        w.close()

        pages = parse_ogg_pages(tmp)
        for i, p in enumerate(pages):
            assert p["crc_ok"], f"Page {i} CRC mismatch: stored=0x{p['crc_stored']:08x}"

        tmp.unlink()
        tmp.parent.rmdir()

    def test_no_spurious_continuation_flags(self):
        """Mid-stream page flushes must never set the continuation flag (0x01).

        Packets are never split across pages (_append_packet finishes each
        packet before the flush check), so RFC 3533 continuation flag must
        always be 0 on intermediate pages.
        """
        tmp = Path(tempfile.mkdtemp()) / "test.ogg"
        w = OggOpusWriter(tmp, sample_rate=16000)
        w.open()
        # 256 packets × 3 bytes = enough to trigger a mid-stream flush
        # by segment count (255 per page max).
        for _ in range(260):
            w.write_packet(make_silence_packet(), duration_ms=20)
        w.close()

        pages = parse_ogg_pages(tmp)
        # More than 3 pages means mid-stream flushes occurred
        assert len(pages) > 3, f"Expected >3 pages (mid-stream flush), got {len(pages)}"

        for i, p in enumerate(pages):
            flags = p["flags"]
            # BOS (0x02) and EOS (0x04) are fine.
            # Continuation (0x01) must never be set.
            assert (flags & 0x01) == 0, (
                f"Page {i}: continuation flag set (flags=0x{flags:02x}). "
                f"Packets never span pages in this implementation."
            )

        tmp.unlink()
        tmp.parent.rmdir()

    def test_different_sample_rate(self):
        """24kHz sample rate is correctly written in OpusHead."""
        tmp = Path(tempfile.mkdtemp()) / "test.ogg"
        w = OggOpusWriter(tmp, sample_rate=24000)
        w.open()
        w.write_packet(make_silence_packet(), duration_ms=20)
        w.close()

        pages = parse_ogg_pages(tmp)
        opus_head = pages[0]["payload"]
        sample_rate = struct.unpack_from("<I", opus_head, 12)[0]
        assert sample_rate == 24000

        tmp.unlink()
        tmp.parent.rmdir()

    def test_opus_head_structure(self):
        """OpusHead packet has correct fields."""
        head = _make_opus_head(16000, 1)
        magic, ver, ch, pre_skip, sr, gain, mapping = struct.unpack("<8sBBHIhB", head)
        assert magic == b"OpusHead"
        assert ver == 1
        assert ch == 1
        assert pre_skip == 312
        assert sr == 16000
        assert gain == 0
        assert mapping == 0

    def test_opus_tags_structure(self):
        """OpusTags packet has vendor string."""
        tags = _make_opus_tags("TestVendor")
        magic = tags[:8]
        assert magic == b"OpusTags"
        assert b"TestVendor" in tags


class TestOggCrc32:
    def test_empty(self):
        assert _ogg_crc32(b"") == 0

    def test_known_vector_self_consistent(self):
        """CRC round-trip: write, read back, and verify."""
        tmp = Path(tempfile.mkdtemp()) / "test.ogg"
        w = OggOpusWriter(tmp, sample_rate=16000)
        w.open()
        w.write_packet(bytes([0xFC, 0xFF, 0xFE]), duration_ms=20)
        w.close()

        pages = parse_ogg_pages(tmp)
        for p in pages:
            assert p["crc_ok"], f"CRC mismatch at seq={p['seq']}"

        tmp.unlink()
        tmp.parent.rmdir()
