from __future__ import annotations

import time


def now_ms() -> int:
    """Return current Unix epoch in **milliseconds** as an integer."""
    return int(time.time() * 1000)


class MonotonicClock:
    """Thin wrapper around ``time.monotonic()`` for measuring intervals.

    Not affected by system clock adjustments.
    """

    @staticmethod
    def now() -> float:
        return time.monotonic()

    @staticmethod
    def elapsed(start: float) -> float:
        return time.monotonic() - start
