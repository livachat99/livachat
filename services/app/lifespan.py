"""Graceful shutdown protocol for LivaChat Server.

Design §4.7 / §11 — shutdown runs through uvicorn's own signal handling:
uvicorn catches SIGTERM/SIGINT, then the FastAPI lifespan teardown (see
application.py) invokes GracefulShutdown.shutdown():
1. /readyz → 503 (traffic drain)
2. Stop accepting new WS connections
3. Send session.ended(server_shutdown) to all connections
4. Wait for writer_task flush (max 10s), then force-close
5. chat store 排空在 lifespan teardown 中、连接关闭之后

Custom signal handlers are deliberately NOT installed — overriding
uvicorn's handlers (as a plain signal.signal did before) leaves the
reloader waiting for a worker that never exits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("livachat.lifespan")

_SHUTDOWN_TIMEOUT = 10.0  # seconds


class GracefulShutdown:
    """Manages the graceful shutdown sequence."""

    def __init__(self) -> None:
        self._shutting_down = False
        self._conn_mgr: Any = None

    def attach(self, *, conn_mgr: Any = None) -> None:
        self._conn_mgr = conn_mgr

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    async def shutdown(self) -> None:
        self._shutting_down = True
        logger.info("graceful shutdown initiated")

        # 1. /readyz → 503 (handled by lifespan context)
        # 2. ConnectionManager stops accepting new connections
        # 3. Broadcast session.ended to all active connections
        if self._conn_mgr:
            try:
                await asyncio.wait_for(self._conn_mgr.shutdown(), timeout=_SHUTDOWN_TIMEOUT)
            except TimeoutError:
                logger.warning("connection shutdown timed out after %ss", _SHUTDOWN_TIMEOUT)

        logger.info("graceful shutdown complete")


# Singleton
_shutdown_handler = GracefulShutdown()


def get_shutdown_handler() -> GracefulShutdown:
    return _shutdown_handler
