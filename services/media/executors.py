from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# Injectable executor for offloading CPU-bound media work.
#
# Default is *None* (run in the calling thread / event loop).  If profiling
# shows Opus or JPEG operations taking > 5 ms on the event loop, replace
# this with a ``ThreadPoolExecutor`` instance and the media functions will
# use ``loop.run_in_executor(_get_executor(), ...)``.
# ---------------------------------------------------------------------------

_executor: ThreadPoolExecutor | None = None


def get_executor() -> ThreadPoolExecutor | None:
    return _executor


def set_executor(ex: ThreadPoolExecutor | None) -> None:
    global _executor
    _executor = ex
