from __future__ import annotations

import asyncio
import json

import pytest
import websockets

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


class TestPingTimeout:
    async def test_pong_received_quickly(self, e2e_url: str):
        """Ping-pong round-trip completes in under 1 second."""
        async with websockets.connect(e2e_url) as ws:
            t0 = asyncio.get_event_loop().time()
            await ws.send(json.dumps({"type": "ping", "seq": 1, "timestamp": 1000, "payload": {}}))
            resp = json.loads(await ws.recv())
            elapsed = asyncio.get_event_loop().time() - t0
            assert resp["type"] == "pong"
            assert elapsed < 1.0, f"Ping-pong took {elapsed:.2f}s"
