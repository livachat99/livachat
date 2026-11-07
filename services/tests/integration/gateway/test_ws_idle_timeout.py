from __future__ import annotations

import asyncio
import json

import pytest
import websockets

pytestmark = pytest.mark.integration


class TestIdleTimeout:
    async def test_server_sends_ping_after_idle(self, ws_url: str):
        """~15s 静默后服务器主动发 keepalive ping（heartbeat_interval_s=15）。"""
        async with websockets.connect(ws_url) as ws:
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=20.0))
            assert resp["type"] == "ping"

    async def test_valid_frame_resets_idle_timer(self, ws_url: str):
        """活跃帧保持连接：ping/pong 往返不被 idle 超时断开。"""
        async with websockets.connect(ws_url) as ws:
            for i in range(3):
                await ws.send(
                    json.dumps({"type": "ping", "seq": i + 1, "timestamp": 1000, "payload": {}})
                )
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                assert resp["type"] == "pong"
                assert resp["payload"]["ping_seq"] == i + 1
