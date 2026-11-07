from __future__ import annotations

import pytest

from tests.integration.agent_relay.mock_hermes_server import MockHermesServer


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def mock_hermes():
    """In-process mock Hermes WS server (no external process needed)."""
    server = MockHermesServer()
    await server.start()
    yield server
    await server.stop()
