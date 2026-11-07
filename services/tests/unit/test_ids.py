from __future__ import annotations

import re

from shared.utils.ids import (
    _set_random_source,
    generate_message_id,
    generate_session_id,
    generate_transcription_id,
)


class _FakeRandom:
    """Deterministic random source for tests."""

    def __init__(self, char: str = "a"):
        self.char = char

    def choice(self, seq: str) -> str:
        return self.char

    def choices(self, population: str, k: int) -> list[str]:
        return [self.char] * k


class TestSessionId:
    def test_format(self):
        sid = generate_session_id()
        assert re.match(r"^sess_[a-z0-9]{18}$", sid), f"Bad session_id: {sid}"

    def test_length(self):
        sid = generate_session_id()
        assert len(sid) == 5 + 18  # "sess_" + 18 chars

    def test_deterministic_source(self):
        _set_random_source(_FakeRandom("x"))
        try:
            sid = generate_session_id()
            assert sid == "sess_" + "x" * 18
        finally:
            # Reset to system random
            from shared.utils.ids import _SystemRandom

            _set_random_source(_SystemRandom())

    def test_unique_likely(self):
        ids = {generate_session_id() for _ in range(100)}
        assert len(ids) == 100

    def test_no_ambiguous_chars(self):
        """Generated ids must not contain 0, o, 1, l, i."""
        ambiguous = set("0o1li")
        for _ in range(50):
            sid = generate_session_id()
            assert not ambiguous & set(sid), f"Ambiguous char in {sid}"


class TestMessageId:
    def test_format(self):
        mid = generate_message_id()
        assert re.match(r"^msg_[a-z0-9]{12}$", mid), f"Bad message_id: {mid}"


class TestTranscriptionId:
    def test_format(self):
        tid = generate_transcription_id()
        assert re.match(r"^trans_[a-z0-9]{12}$", tid), f"Bad transcription_id: {tid}"
