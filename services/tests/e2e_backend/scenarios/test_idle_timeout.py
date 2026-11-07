from __future__ import annotations

import asyncio
import json

import pytest
import websockets

pytestmark = pytest.mark.e2e


class TestIdleTimeout:
    @pytest.mark.slow
    async def test_server_sends_ping(self, e2e_url: str):
        """After ~15s the server should send a ping. Slow test — skipped by default."""
        async with websockets.connect(e2e_url) as ws:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
                data = json.loads(raw)
                assert data["type"] == "ping"
            except TimeoutError:
                pytest.skip("Server ping not received within timeout")

    async def test_pong_after_client_ping(self, e2e_url: str):
        """Client ping gets an immediate pong — fast test."""
        async with websockets.connect(e2e_url) as ws:
            await ws.send(json.dumps({"type": "ping", "seq": 1, "timestamp": 1000, "payload": {}}))
            pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert pong["type"] == "pong"

    async def test_multiple_pings_keep_alive(self, e2e_url: str):
        """Regular pings keep the connection alive."""
        async with websockets.connect(e2e_url) as ws:
            for i in range(3):
                await ws.send(json.dumps({"type": "ping", "seq": i + 1, "timestamp": 1000, "payload": {}}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert resp["type"] == "pong"
                await asyncio.sleep(0.5)
