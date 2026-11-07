"""OggS/Opus container writer (RFC 3533 / RFC 7845).

All methods are synchronous — the caller (RecordingSession writer task) is
a dedicated async task where bounded sync I/O is acceptable.  File writes
for video frames use asyncio.to_thread separately.
"""

from __future__ import annotations

import struct
from pathlib import Path

# OggS page constants
_OGG_MAGIC = b"OggS"
_OGG_HEADER_SIZE = 27  # capture_pattern(4) + version(1) + flags(1) + granule(8) + serial(4) + seq(4) + crc(4) + segments(1)

_OPUS_SAMPLE_RATE = 48000  # Ogg granule clock is always 48kHz for Opus
_OPUS_PRE_SKIP = 312       # RFC 7845 recommended pre-skip
_MAX_PACKETS_PER_PAGE = 255  # RFC 3533 hard limit
_MAX_PAGE_PAYLOAD = 50000   # soft limit for flush


class OggOpusWriter:
    """Streaming OggS/Opus file writer with 60s segment rotation."""

    def __init__(self, path: Path, sample_rate: int = 16000, channels: int = 1):
        self._path = path
        self._input_sample_rate = sample_rate
        self._channels = channels
        self._f = None

        self._serial = _make_serial()
        self._page_seq: int = 0
        self._granule: int = 0

        # Current page: segment table + raw data stored separately
        self._segments: list[bytes] = []
        self._segment_data: bytearray = bytearray()
        self._segments_in_page: int = 0  # count of segment table entries

        # Stats
        self._packets_written: int = 0
        self._bytes_written: int = 0
        self._headers_written: bool = False

    # -- public API -------------------------------------------------------

    def open(self) -> None:
        self._f = open(self._path, "wb")  # noqa: SIM115  # 流式 writer 跨多次 write 持 fd
        self._write_headers()

    def write_packet(self, opus_bytes: bytes, duration_ms: int = 20) -> None:
        """Write one Opus packet. duration_ms is the packet duration (typ. 20ms)."""
        if not self._headers_written:
            self._write_headers()

        self._append_packet(opus_bytes)
        self._packets_written += 1

        # Advance granule: 48kHz * (duration_ms / 1000)
        granule_step = int(_OPUS_SAMPLE_RATE * duration_ms / 1000)
        self._granule += granule_step

        # Flush page when approaching limits
        payload_size = len(self._segment_data)
        if payload_size >= _MAX_PAGE_PAYLOAD or self._segments_in_page >= _MAX_PACKETS_PER_PAGE:
            self._flush_page(continuation=False)

    def close(self) -> None:
        """Flush remaining data, write EOS page, close file."""
        if self._f is None:
            return
        if self._segments_in_page > 0:
            self._flush_page(continuation=False, last_page=True)
        else:
            self._write_eos()
        self._f.close()
        self._f = None

    @property
    def packets_written(self) -> int:
        return self._packets_written

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    # -- internal ----------------------------------------------------------

    def _write_headers(self) -> None:
        """Write OpusHead + OpusTags pages."""
        opus_head = _make_opus_head(self._input_sample_rate, self._channels)
        self._write_ogg_page([opus_head], granule=0, flags=0x02)

        opus_tags = _make_opus_tags()
        self._write_ogg_page([opus_tags], granule=0, flags=0x00)

        self._headers_written = True

    def _append_packet(self, data: bytes) -> None:
        """Append a packet as OggS segments (lacing values ≤ 255 each)."""
        remaining = len(data)
        while remaining >= 255:
            self._segments.append(bytes([255]))  # lacing value = 255 means "continues"
            self._segment_data.extend(data[:255])
            self._segments_in_page += 1
            remaining -= 255
            data = data[255:]
        # Final segment: lacing value < 255
        self._segments.append(bytes([remaining]))
        self._segment_data.extend(data)
        self._segments_in_page += 1

    def _flush_page(self, *, continuation: bool = False, last_page: bool = False) -> None:
        """Write the current page buffer as an OggS page.

        continuation MUST be False: _append_packet always finishes the packet
        before the flush check, so no packet ever spans page boundaries.
        """
        flags = 0x00
        if continuation:
            flags |= 0x01
        if last_page:
            flags |= 0x04

        # Build segment table
        segment_table = b"".join(self._segments)
        raw_data = bytes(self._segment_data)

        self._write_ogg_page_raw(
            segment_table=segment_table,
            data=raw_data,
            granule=self._granule,
            flags=flags,
        )

        self._segments.clear()
        self._segment_data.clear()
        self._segments_in_page = 0

    def _write_eos(self) -> None:
        """Write an empty EOS page."""
        self._write_ogg_page_raw(segment_table=b"", data=b"", granule=self._granule, flags=0x04)

    def _write_ogg_page(self, packets: list[bytes], *, granule: int, flags: int) -> None:
        """Write a page containing the given packets (used for header packets)."""
        segment_table = b"".join(_ogg_lacing(p) for p in packets)
        data = b"".join(packets)
        self._write_ogg_page_raw(segment_table=segment_table, data=data, granule=granule, flags=flags)

    def _write_ogg_page_raw(self, *, segment_table: bytes, data: bytes, granule: int, flags: int) -> None:
        """Build and write a single OggS page from segment_table + raw data."""
        num_segments = len(segment_table)
        header = bytearray(_OGG_HEADER_SIZE + num_segments)
        struct.pack_into("<4sBBqIIIB", header, 0,
            _OGG_MAGIC,       # capture_pattern
            0,                 # stream_structure_version
            flags,             # header_type_flag
            granule,           # granule_position
            self._serial,      # bitstream_serial_number
            self._page_seq,    # page_sequence_number
            0,                 # CRC_checksum (placeholder)
            num_segments,      # page_segments
        )
        header[_OGG_HEADER_SIZE:] = segment_table

        # Ogg CRC-32: init=0, final_xor=0 (not the standard Ethernet CRC-32)
        crc = _ogg_crc32(bytes(header) + data)
        struct.pack_into("<I", header, 22, crc)

        self._f.write(bytes(header))
        self._f.write(data)
        self._bytes_written += len(header) + len(data)
        self._page_seq += 1


# -- CRC32 (Ogg variant: init=0, final_xor=0, polynomial 0x04C11DB7) ----

_CRC32_TABLE: list[int] = []

def _init_crc32_table() -> None:
    if _CRC32_TABLE:
        return
    for i in range(256):
        crc = i << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = (crc << 1) ^ 0x04C11DB7
            else:
                crc = (crc << 1)
            crc &= 0xFFFFFFFF
        _CRC32_TABLE.append(crc)


def _ogg_crc32(data: bytes) -> int:
    """Ogg CRC-32: init=0, final_xor=0, polynomial 0x04C11DB7."""
    _init_crc32_table()
    crc = 0
    for byte in data:
        idx = ((crc >> 24) ^ byte) & 0xFF
        crc = ((crc << 8) ^ _CRC32_TABLE[idx]) & 0xFFFFFFFF
    return crc

def _make_serial() -> int:
    import os
    return int.from_bytes(os.urandom(4), "little") & 0x7FFFFFFF


def _ogg_lacing(data: bytes) -> bytes:
    """Convert raw packet data into OggS segment table entries."""
    if not data:
        return b""  # empty packet → no segment (avoids zero-length segment ambiguity)
    parts = []
    remaining = len(data)
    offset = 0
    while remaining > 255:
        parts.append(b"\xff")
        remaining -= 255
        offset += 255
    parts.append(bytes([remaining]))
    return b"".join(parts)


def _make_opus_head(sample_rate: int, channels: int) -> bytes:
    """Build OpusHead packet (RFC 7845 §5.1)."""
    return struct.pack(
        "<8sBBHIhB",
        b"OpusHead",
        1,                    # version
        channels,
        _OPUS_PRE_SKIP,
        sample_rate,
        0,                    # output_gain
        0,                    # channel_mapping_family
    )


def _make_opus_tags(vendor: str = "LivaChat Recording") -> bytes:
    """Build OpusTags packet (RFC 7845 §5.2)."""
    vendor_bytes = vendor.encode("utf-8")
    return struct.pack("<8sI", b"OpusTags", len(vendor_bytes)) + vendor_bytes + struct.pack("<I", 0)
