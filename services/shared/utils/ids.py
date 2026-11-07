from __future__ import annotations

import os
import secrets
import time
import uuid as _uuid
from typing import Protocol


class _RandomSource(Protocol):
    """Injectable random source for deterministic testing."""

    def choice(self, seq: str) -> str: ...
    def choices(self, population: str, k: int) -> list[str]: ...


class _SystemRandom:
    def choice(self, seq: str) -> str:
        return secrets.choice(seq)

    def choices(self, population: str, k: int) -> list[str]:
        # secrets doesn't have .choices; build it from individual choices
        return [secrets.choice(population) for _ in range(k)]


# Characters ux-friendly: no uppercase, no ambiguous chars (0/o, 1/l/i)
_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

# Injectable random source — replace in tests for deterministic output.
_random: _RandomSource = _SystemRandom()


def _set_random_source(src: _RandomSource) -> None:
    """Override the random source (for tests only)."""
    global _random
    _random = src


def generate_session_id() -> str:
    """Return a session id like ``sess_<18 chars>``."""
    return "sess_" + "".join(_random.choices(_ID_ALPHABET, k=18))


def generate_message_id() -> str:
    """Return a message id like ``msg_<12 chars>``."""
    return "msg_" + "".join(_random.choices(_ID_ALPHABET, k=12))


def generate_transcription_id() -> str:
    """Return a transcription id like ``trans_<12 chars>``."""
    return "trans_" + "".join(_random.choices(_ID_ALPHABET, k=12))


def uuid7() -> str:
    """Generate a UUIDv7 string (time-ordered, RFC 9562).

    Format: 48-bit Unix ms timestamp | 4-bit version(7) | 12-bit rand_a
            | 2-bit variant(10) | 62-bit rand_b
    """
    timestamp_ms = int(time.time() * 1000)
    timestamp_bytes = timestamp_ms.to_bytes(6, "big")

    rand_bytes = os.urandom(10)

    # version 7
    rand_a = int.from_bytes(rand_bytes[0:2], "big")
    rand_a = (rand_a & 0x0FFF) | 0x7000

    # variant 10
    rand_b = int.from_bytes(rand_bytes[2:8], "big")
    rand_b = (rand_b & 0x3FFFFFFFFFFFFFFF) | 0x8000000000000000

    b = timestamp_bytes + rand_a.to_bytes(2, "big") + rand_b.to_bytes(8, "big")
    return str(_uuid.UUID(bytes=b))
