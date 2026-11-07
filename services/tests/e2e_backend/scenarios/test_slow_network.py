from __future__ import annotations

import json

import pytest
import websockets

pytestmark = pytest.mark.e2e


class TestSlowNetwork:
    async def test_partial_frame_handling(self, e2e_url: str):
        """Server handles a rapid burst of messages without dropping any."""
        async with websockets.connect(e2e_url) as ws:
            for i in range(5):
                await ws.send(json.dumps({"type": "ping", "seq": i + 1, "timestamp": 1000, "payload": {}}))
            # Receive all pongs
            responses = []
            for _ in range(5):
                raw = await ws.recv()
                responses.append(json.loads(raw))
            pongs = [r for r in responses if r["type"] == "pong"]
            assert len(pongs) == 5

    async def test_rapid_reconnect(self, e2e_url: str):
        """Quick disconnect + reconnect should succeed."""
        async with websockets.connect(e2e_url) as ws1:
            await ws1.send(json.dumps({"type": "ping", "seq": 1, "timestamp": 1000, "payload": {}}))
            await ws1.recv()
        # Reconnect
        async with websockets.connect(e2e_url) as ws2:
            await ws2.send(json.dumps({"type": "ping", "seq": 1, "timestamp": 1000, "payload": {}}))
            resp = json.loads(await ws2.recv())
            assert resp["type"] == "pong"
