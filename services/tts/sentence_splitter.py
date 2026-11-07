from __future__ import annotations

import re

# Punctuation at which we consider a sentence boundary reached.
_SENTENCE_END = re.compile(r"[、。！？；\.,!?;:\n]")
_STRIP_WS = re.compile(r"^\s+")


class SentenceSplitter:
    """Incrementally buffers text and yields complete sentences.

    Feed partial text via :meth:`feed`; when a sentence-ending punctuation
    is seen the accumulated sentence is returned.  Call :meth:`flush` for any
    remaining (incomplete) text.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        """Feed a text delta and return 0..N complete sentences."""
        self._buf += text
        self._buf = _STRIP_WS.sub("", self._buf)

        results: list[str] = []
        while True:
            match = _SENTENCE_END.search(self._buf)
            if not match:
                break
            idx = match.end()
            sentence = self._buf[:idx].strip()
            if sentence:
                results.append(sentence)
            self._buf = self._buf[idx:].lstrip()
        return results

    def flush(self) -> str | None:
        """Return any remaining buffered text, or None if empty."""
        remainder = self._buf.strip()
        self._buf = ""
        return remainder or None
