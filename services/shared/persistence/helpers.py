from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger("livachat.persistence")


# Bounded set of in-flight persist tasks so they don't accumulate unseen on fast
# reconnect.  A done callback removes each task on completion; _MAX_PERSIST_TASKS
# prevents unbounded growth if completion lags behind creation.
_persist_tasks: set[asyncio.Task[None]] = set()
_MAX_PERSIST_TASKS = 200


def _persist_done_callback(task: asyncio.Task[None]) -> None:
    _persist_tasks.discard(task)
    # Suppress unhandled exceptions — the _runner already logs errors.
    try:
        task.result()
    except Exception:
        pass


def persist_async(coro: Coroutine[Any, Any, Any]) -> None:
    """Fire-and-forget a persistence coroutine.

    Errors are logged and counted as a metric (``livachat_errors_total{code="persist"}``)
    but are **never** re-raised to the caller, ensuring the real-time path is not blocked.

    Usage::

        persist_async(session_repo.create(sess))
    """
    global _persist_tasks

    async def _runner() -> None:
        try:
            await coro
        except Exception:
            logger.exception("persist_async failed")
            try:
                from shared.observability.metrics import get_registry

                get_registry().errors_total().labels(code="persist").inc()
            except Exception:
                pass

    # Prune completed tasks if we're at capacity so we don't grow unbounded.
    if len(_persist_tasks) >= _MAX_PERSIST_TASKS:
        _persist_tasks = {t for t in _persist_tasks if not t.done()}

    task = asyncio.create_task(_runner())
    _persist_tasks.add(task)
    task.add_done_callback(_persist_done_callback)
