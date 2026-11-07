"""Contract tests for core protocol flow: connect, session.start, ping/pong,
audio → transcription, video → ack, interrupt, session.end, and basic error paths.

All S2C messages are validated through ServerMessageAdapter (contract test invariant).
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid

import pytest
import websockets
import uvicorn

from livachat_mock_server.app import create_app
from livachat_mock_server.config import ServerConfig, Scenario
from livachat_protocol.generated.server_message import ServerMessageAdapter


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _start_server(config: ServerConfig) -> int:
    """Start uvicorn in a daemon thread. Returns the actual port."""
    port = _free_port()
    config.port = port
    app = create_app(config)

    def _run():
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.3)
    return port


@pytest.fixture(scope="module")
def default_port():
    return _start_server(ServerConfig(scenario=Scenario.DEFAULT))


@pytest.fixture(scope="module")
def interrupt_port():
    return _start_server(ServerConfig(scenario=Scenario.INTERRUPT))


def _device_id() -> str:
    return str(uuid.uuid4())


async def _recv_json(ws) -> dict:
    raw = await ws.recv()
    # Contract: every S2C message must be parseable by ServerMessageAdapter
    ServerMessageAdapter.validate_json(raw)
    return json.loads(raw)


async def _send_json(ws, data: dict) -> None:
    await ws.send(json.dumps(data))


async def _connect(port: int, device_id: str | None = None) -> websockets.WebSocketClientProtocol:
    did = device_id or _device_id()
    return await websockets.connect(
        f"ws://127.0.0.1:{port}/v1/ws/stream?token=dev-token&device_id={did}",
        close_timeout=1,
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# healthz + auth token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_healthz(default_port):
    import httpx
    async with httpx.AsyncClient() as cli:
        r = await cli.get(f"http://127.0.0.1:{default_port}/healthz")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_auth_token(default_port):
    import httpx
    async with httpx.AsyncClient() as cli:
        r = await cli.post(f"http://127.0.0.1:{default_port}/v1/auth/token")
        assert r.status_code == 200
        data = r.json()
        assert data["token"] == "dev-token"
        assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_healthz_token(default_port):
    import httpx
    async with httpx.AsyncClient() as cli:
        ok = await cli.get(
            f"http://127.0.0.1:{default_port}/healthz_token",
            params={"token": "dev-token", "device_id": _device_id()},
        )
        assert ok.status_code == 200

        bad_token = await cli.get(
            f"http://127.0.0.1:{default_port}/healthz_token",
            params={"token": "wrong", "device_id": _device_id()},
        )
        assert bad_token.status_code == 401

        bad_device = await cli.get(
            f"http://127.0.0.1:{default_port}/healthz_token",
            params={"token": "dev-token", "device_id": "not-a-uuid"},
        )
        assert bad_device.status_code == 401


# ---------------------------------------------------------------------------
# session lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_start_and_ready(default_port):
    async with await _connect(default_port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        ready = await _recv_json(ws)
        assert ready["type"] == "session.ready"
        assert ready["payload"]["session_id"].startswith("sess_")
        assert ready["payload"]["session_restored"] is False
        assert ready["payload"]["server_version"] == "0.1.0"

        status_idle = await _recv_json(ws)
        assert status_idle["type"] == "status"
        assert status_idle["payload"]["session_status"] == "idle"

        status_listening = await _recv_json(ws)
        assert status_listening["type"] == "status"
        assert status_listening["payload"]["session_status"] == "listening"


@pytest.mark.asyncio
async def test_session_end(default_port):
    async with await _connect(default_port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        # drain session.ready + status messages
        for _ in range(3):
            await _recv_json(ws)

        await _send_json(ws, {
            "type": "session.end",
            "seq": 2,
            "timestamp": _now_ms(),
            "payload": {"reason": "user_quit"},
        })

        ended = await _recv_json(ws)
        assert ended["type"] == "session.ended"
        assert ended["payload"]["reason"] == "user_quit"


# ---------------------------------------------------------------------------
# ping / pong
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ping_pong(default_port):
    async with await _connect(default_port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        for _ in range(3):
            await _recv_json(ws)

        await _send_json(ws, {
            "type": "ping",
            "seq": 2,
            "timestamp": _now_ms(),
            "payload": {},
        })

        pong = await _recv_json(ws)
        assert pong["type"] == "pong"
        assert pong["payload"]["ping_seq"] == 2


# ---------------------------------------------------------------------------
# audio → transcription flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audio_to_transcription(default_port):
    async with await _connect(default_port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        # drain ready + idle + listening
        for _ in range(3):
            await _recv_json(ws)

        # send speech frames
        for i in range(5):
            await _send_json(ws, {
                "type": "audio",
                "seq": 2 + i,
                "timestamp": _now_ms(),
                "payload": {
                    "data": "AAAA",
                    "codec": "opus",
                    "duration_ms": 20,
                    "vad_state": "speech",
                },
            })

        # collect interim transcriptions
        interim_found = False
        for _ in range(5):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=2.0)
                if msg["type"] == "transcription" and not msg["payload"]["is_final"]:
                    interim_found = True
            except asyncio.TimeoutError:
                break

        assert interim_found, "Expected at least one interim transcription"

        # send silence frames to trigger final + response
        for i in range(35):
            await _send_json(ws, {
                "type": "audio",
                "seq": 7 + i,
                "timestamp": _now_ms(),
                "payload": {
                    "data": "AAAA",
                    "codec": "opus",
                    "duration_ms": 20,
                    "vad_state": "silence",
                },
            })

        # expect: final transcription → thinking → speaking → ai_text → status listening
        types_seen: set[str] = set()
        for _ in range(20):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=3.0)
                types_seen.add(msg["type"])
            except asyncio.TimeoutError:
                break

        assert "transcription" in types_seen
        assert "ai_text" in types_seen
        assert "status" in types_seen


# ---------------------------------------------------------------------------
# video ack
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_video_ack(default_port):
    async with await _connect(default_port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        for _ in range(3):
            await _recv_json(ws)

        await _send_json(ws, {
            "type": "video",
            "seq": 2,
            "timestamp": _now_ms(),
            "payload": {
                "data": "AAAA",
                "frame_id": 1,
                "width": 1080,
                "height": 1920,
                "orientation": 0,
                "camera_facing": "front",
                "quality": 70,
            },
        })

        ack = await asyncio.wait_for(_recv_json(ws), timeout=2.0)
        assert ack["type"] == "ai_video_ack"
        assert ack["payload"]["frame_id"] == 1
        assert ack["payload"]["ack_frame_id"] == 1
        assert ack["payload"]["processed"] is True


# ---------------------------------------------------------------------------
# interrupt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interrupt_during_speaking(interrupt_port):
    async with await _connect(interrupt_port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        for _ in range(3):
            await _recv_json(ws)

        # speech frames
        for i in range(3):
            await _send_json(ws, {
                "type": "audio",
                "seq": 2 + i,
                "timestamp": _now_ms(),
                "payload": {
                    "data": "AAAA",
                    "codec": "opus",
                    "duration_ms": 20,
                    "vad_state": "speech",
                },
            })

        # silence frames to trigger response
        for i in range(35):
            await _send_json(ws, {
                "type": "audio",
                "seq": 5 + i,
                "timestamp": _now_ms(),
                "payload": {
                    "data": "AAAA",
                    "codec": "opus",
                    "duration_ms": 20,
                    "vad_state": "silence",
                },
            })

        # wait for speaking status
        speaking_seen = False
        seq_after_speaking = 0
        for _ in range(15):
            msg = await asyncio.wait_for(_recv_json(ws), timeout=2.0)
            if msg["type"] == "status" and msg["payload"]["session_status"] == "speaking":
                speaking_seen = True
                seq_after_speaking = msg["seq"]
                break

        assert speaking_seen, "Expected status:speaking"

        # send interrupt
        await _send_json(ws, {
            "type": "interrupt",
            "seq": 42,
            "timestamp": _now_ms(),
            "payload": {"reason": "user_voice"},
        })

        # wait for status:listening with reason=interrupted
        interrupted_seen = False
        for _ in range(10):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=2.0)
                if (
                    msg["type"] == "status"
                    and msg["payload"]["session_status"] == "listening"
                    and msg["payload"]["reason"] == "interrupted"
                ):
                    interrupted_seen = True
                    break
            except asyncio.TimeoutError:
                break

        assert interrupted_seen, "Expected status:listening with reason=interrupted"


# ---------------------------------------------------------------------------
# invalid device_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_device_id_rejected(default_port):
    with pytest.raises(websockets.exceptions.InvalidStatus) as exc_info:
        await websockets.connect(
            f"ws://127.0.0.1:{default_port}/v1/ws/stream?token=dev-token&device_id=not-a-uuid",
            close_timeout=1,
        )
    # 4001 maps to close code
    assert exc_info.value.response.status_code == 403


# ---------------------------------------------------------------------------
# first message must be session.start
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_message_must_be_session_start(default_port):
    async with await _connect(default_port) as ws:
        # Send a non-session.start message first — server discards it silently
        await _send_json(ws, {
            "type": "ping",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {},
        })

        # Connection stays open; server is still waiting for session.start.
        # Now send session.start — server should accept it and proceed normally.
        await _send_json(ws, {
            "type": "session.start",
            "seq": 2,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        ready = await _recv_json(ws)
        assert ready["type"] == "session.ready"


# ---------------------------------------------------------------------------
# seq monotonic enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_seq_discarded(default_port):
    async with await _connect(default_port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        for _ in range(3):
            await _recv_json(ws)

        # first ping works
        await _send_json(ws, {
            "type": "ping", "seq": 2, "timestamp": _now_ms(), "payload": {},
        })
        pong = await _recv_json(ws)
        assert pong["type"] == "pong"

        # duplicate seq → ignored, no pong
        await _send_json(ws, {
            "type": "ping", "seq": 2, "timestamp": _now_ms(), "payload": {},
        })
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_recv_json(ws), timeout=1.0)
