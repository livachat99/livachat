from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from gateway.auth import wire_digest
from shared.version import SERVER_VERSION

pytestmark = pytest.mark.integration


class TestWSAuth:
    async def test_invalid_token_rejected(self, server: str):
        url = f"{server}/v1/ws/stream?token=wrong&device_id=00000000-0000-0000-0000-000000000001"
        with pytest.raises(websockets.InvalidStatus) as exc_info:
            async with websockets.connect(url):
                pass
        assert exc_info.value.response.status_code == 401

    async def test_invalid_device_id_rejected(self, server: str, dev_token: str):
        url = f"{server}/v1/ws/stream?token={wire_digest(dev_token)}&device_id=not-a-uuid"
        with pytest.raises(websockets.InvalidStatus) as exc_info:
            async with websockets.connect(url):
                pass
        assert exc_info.value.response.status_code == 401

    async def test_valid_handshake(self, ws_url: str):
        async with websockets.connect(ws_url) as ws:
            # If connect() returns without raising, the handshake succeeded
            assert ws  # connection is alive


class TestWSPingPong:
    async def test_ping_gets_pong(self, ws_url: str):
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"type": "ping", "seq": 1, "timestamp": 1000, "payload": {}}))
            resp = json.loads(await ws.recv())
            assert resp["type"] == "pong"
            assert resp["payload"]["ping_seq"] == 1

    @pytest.mark.parametrize("client_version", ["android-1.0.0", "android-1.0.0.130"])
    async def test_session_start_gets_session_ready(self, ws_url: str, client_version: str):
        """session.start 是合法消息（orchestrator 装配时）：处理成功后回
        session.ready 而非 error（旧 dialog stub 的 unsupported 语义已被取代）。

        client_version 同时覆盖存量 3 段格式与带 versionCode 的 4 段格式。
        """
        async with websockets.connect(ws_url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "session.start",
                        "seq": 1,
                        "timestamp": 1000,
                        "payload": {
                            "session_id": None,
                            "config": {
                                "enable_video": True,
                                "audio_sample_rate": 24000,
                                "vad_sensitivity": "medium",
                                "client_version": client_version,
                            },
                        },
                    }
                )
            )
            while True:
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                if resp["type"] == "session.ready":
                    assert resp["payload"]["session_id"]
                    assert resp["payload"]["server_version"] == SERVER_VERSION
                    break
            # 无 error 响应（session.start 合法处理）
            assert resp["type"] == "session.ready"

    async def test_invalid_json_gets_error(self, ws_url: str):
        async with websockets.connect(ws_url) as ws:
            await ws.send("not valid json")
            resp = json.loads(await ws.recv())
            assert resp["type"] == "error"
            assert resp["payload"]["code"] == 1006
