"""Scenario-specific tests: each test starts a fresh server with the target scenario."""

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


def _start_server(scenario: Scenario, **overrides) -> int:
    port = _free_port()
    config = ServerConfig(port=port, scenario=scenario)
    for k, v in overrides.items():
        setattr(config, k, v)
    app = create_app(config)

    def _run():
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.3)
    return port


def _device_id() -> str:
    return str(uuid.uuid4())


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _recv_json(ws) -> dict:
    raw = await ws.recv()
    ServerMessageAdapter.validate_json(raw)
    return json.loads(raw)


async def _send_json(ws, data: dict) -> None:
    await ws.send(json.dumps(data))


async def _connect(port: int) -> websockets.WebSocketClientProtocol:
    return await websockets.connect(
        f"ws://127.0.0.1:{port}/v1/ws/stream?token=dev-token&device_id={_device_id()}",
        close_timeout=1,
    )


async def _session_start(ws):
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


async def _trigger_response(ws, base_seq: int):
    """Send speech + silence frames to trigger the full response flow."""
    for i in range(3):
        await _send_json(ws, {
            "type": "audio",
            "seq": base_seq + i,
            "timestamp": _now_ms(),
            "payload": {
                "data": "AAAA",
                "codec": "opus",
                "duration_ms": 20,
                "vad_state": "speech",
            },
        })
    for i in range(35):
        await _send_json(ws, {
            "type": "audio",
            "seq": base_seq + 3 + i,
            "timestamp": _now_ms(),
            "payload": {
                "data": "AAAA",
                "codec": "opus",
                "duration_ms": 20,
                "vad_state": "silence",
            },
        })


# ---------------------------------------------------------------------------
# default scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_scenario_has_audio():
    port = _start_server(Scenario.DEFAULT)
    async with await _connect(port) as ws:
        await _session_start(ws)
        for _ in range(3):
            await _recv_json(ws)

        await _trigger_response(ws, 2)

        types_seen: set[str] = set()
        for _ in range(25):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=3.0)
                types_seen.add(msg["type"])
            except asyncio.TimeoutError:
                break

        assert "ai_text" in types_seen
        assert "ai_audio" in types_seen
        assert "transcription" in types_seen


# ---------------------------------------------------------------------------
# text_only scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_text_only_no_audio():
    port = _start_server(Scenario.TEXT_ONLY)
    async with await _connect(port) as ws:
        await _session_start(ws)
        for _ in range(3):
            await _recv_json(ws)

        await _trigger_response(ws, 2)

        types_seen: set[str] = set()
        for _ in range(20):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=3.0)
                types_seen.add(msg["type"])
            except asyncio.TimeoutError:
                break

        assert "ai_text" in types_seen
        assert "ai_audio" not in types_seen


# ---------------------------------------------------------------------------
# text_on_start scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_text_on_start_flows_without_audio():
    port = _start_server(Scenario.TEXT_ON_START)
    async with await _connect(port) as ws:
        await _session_start(ws)

        types_seen: set[str] = set()
        listening_after_start = False
        for _ in range(20):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=3.0)
            except asyncio.TimeoutError:
                break
            types_seen.add(msg["type"])
            if (
                msg["type"] == "status"
                and msg["payload"]["session_status"] == "listening"
                and len(types_seen) > 3
            ):
                listening_after_start = True
                break

        assert "transcription" in types_seen
        assert "ai_text" in types_seen
        assert "ai_audio" not in types_seen
        assert listening_after_start


# ---------------------------------------------------------------------------
# with_audio scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_with_audio_has_audio():
    port = _start_server(Scenario.WITH_AUDIO)
    async with await _connect(port) as ws:
        await _session_start(ws)
        for _ in range(3):
            await _recv_json(ws)

        await _trigger_response(ws, 2)

        audio_count = 0
        for _ in range(25):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=3.0)
                if msg["type"] == "ai_audio":
                    audio_count += 1
            except asyncio.TimeoutError:
                break

        assert audio_count > 0


# ---------------------------------------------------------------------------
# slow_network scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slow_network_connects():
    port = _start_server(Scenario.SLOW_NETWORK)
    async with await _connect(port) as ws:
        await _session_start(ws)
        for _ in range(3):
            await _recv_json(ws)

        await _trigger_response(ws, 2)

        # just verify we can receive messages (delays don't break the flow)
        found = False
        for _ in range(25):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=5.0)
                if msg["type"] == "ai_text":
                    found = True
            except asyncio.TimeoutError:
                break

        assert found


# ---------------------------------------------------------------------------
# server_error scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_server_error_sends_error_after_connect():
    port = _start_server(Scenario.SERVER_ERROR)
    async with await _connect(port) as ws:
        await _session_start(ws)

        # session.ready comes first
        ready = await _recv_json(ws)
        assert ready["type"] == "session.ready"

        # error should arrive after session.ready
        found_error = False
        for _ in range(5):
            msg = await _recv_json(ws)
            if msg["type"] == "error":
                assert msg["payload"]["code"] == 1001
                found_error = True
                break

        assert found_error


# ---------------------------------------------------------------------------
# auth_failed scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_failed_rejects():
    port = _start_server(Scenario.AUTH_FAILED)
    with pytest.raises(websockets.exceptions.InvalidStatus):
        await websockets.connect(
            f"ws://127.0.0.1:{port}/v1/ws/stream?token=dev-token&device_id={_device_id()}",
            close_timeout=1,
        )


# ---------------------------------------------------------------------------
# idle_timeout scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idle_timeout_closes():
    port = _start_server(Scenario.IDLE_TIMEOUT, idle_timeout_s=3)
    async with await _connect(port) as ws:
        await _session_start(ws)
        for _ in range(3):
            await _recv_json(ws)

        # don't send anything → server closes after idle_timeout_s (3s)
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=8.0)

        assert exc_info.value.code == 4003


# ---------------------------------------------------------------------------
# ping_timeout scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ping_timeout_no_pong():
    port = _start_server(Scenario.PING_TIMEOUT, ping_timeout_s=3)
    async with await _connect(port) as ws:
        await _session_start(ws)
        for _ in range(3):
            await _recv_json(ws)

        await _send_json(ws, {
            "type": "ping",
            "seq": 2,
            "timestamp": _now_ms(),
            "payload": {},
        })

        # no pong, server closes after ping_timeout_s (3s)
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=8.0)

        assert exc_info.value.code in (4002, 4003)


# ---------------------------------------------------------------------------
# resume_success scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_success_restores_session():
    port = _start_server(Scenario.RESUME_SUCCESS)

    # first connection: create session
    async with await _connect(port) as ws:
        await _session_start(ws)
        ready = await _recv_json(ws)
        session_id = ready["payload"]["session_id"]
        assert ready["payload"]["session_restored"] is False
        # drain remaining status messages
        for _ in range(2):
            await _recv_json(ws)

    # second connection: resume
    async with await _connect(port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": session_id,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        ready2 = await _recv_json(ws)
        assert ready2["payload"]["session_restored"] is True
        assert ready2["payload"]["session_id"] == session_id


@pytest.mark.asyncio
async def test_resume_drop_reconnects_and_streams():
    port = _start_server(Scenario.RESUME_DROP)

    async with await _connect(port) as ws:
        await _session_start(ws)
        ready = await _recv_json(ws)
        session_id = ready["payload"]["session_id"]
        assert ready["payload"]["session_restored"] is False
        for _ in range(2):
            await _recv_json(ws)

        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=3.0)
        assert exc_info.value.code == 1012

    async with await _connect(port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": session_id,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        ready2 = await _recv_json(ws)
        assert ready2["payload"]["session_restored"] is True
        assert ready2["payload"]["session_id"] == session_id

        types_seen: set[str] = set()
        for _ in range(20):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=3.0)
            except asyncio.TimeoutError:
                break
            types_seen.add(msg["type"])
            if "ai_text" in types_seen:
                break
        assert "transcription" in types_seen
        assert "ai_text" in types_seen


@pytest.mark.asyncio
async def test_session_end_removes_resumable_session():
    port = _start_server(Scenario.RESUME_SUCCESS)

    async with await _connect(port) as ws:
        await _session_start(ws)
        ready = await _recv_json(ws)
        session_id = ready["payload"]["session_id"]
        for _ in range(2):
            await _recv_json(ws)

        await _send_json(ws, {
            "type": "session.end",
            "seq": 2,
            "timestamp": _now_ms(),
            "payload": {"reason": "user_quit"},
        })
        ended = await _recv_json(ws)
        assert ended["type"] == "session.ended"

    async with await _connect(port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": session_id,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        ready2 = await _recv_json(ws)
        assert ready2["payload"]["session_restored"] is False
        assert ready2["payload"]["session_id"] != session_id


# ---------------------------------------------------------------------------
# resume_expired scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_expired_new_session():
    port = _start_server(Scenario.RESUME_EXPIRED)
    async with await _connect(port) as ws:
        await _send_json(ws, {
            "type": "session.start",
            "seq": 1,
            "timestamp": _now_ms(),
            "payload": {
                "session_id": "sess_000000000000000000",
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "ios-1.0.0",
                },
            },
        })

        ready = await _recv_json(ws)
        assert ready["payload"]["session_restored"] is False
        assert ready["payload"]["session_id"] != "sess_000000000000000000"


# ---------------------------------------------------------------------------
# interrupt scenario (already in SPEAKING pre-seeded)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interrupt_scenario():
    port = _start_server(Scenario.INTERRUPT)
    async with await _connect(port) as ws:
        await _session_start(ws)
        for _ in range(3):
            await _recv_json(ws)

        # trigger response to reach SPEAKING
        await _trigger_response(ws, 2)

        # wait for SPEAKING status
        speaking = False
        for _ in range(15):
            msg = await asyncio.wait_for(_recv_json(ws), timeout=3.0)
            if msg["type"] == "status" and msg["payload"]["session_status"] == "speaking":
                speaking = True
                break

        assert speaking

        # interrupt
        await _send_json(ws, {
            "type": "interrupt",
            "seq": 42,
            "timestamp": _now_ms(),
            "payload": {"reason": "user_voice"},
        })

        interrupted = False
        for _ in range(10):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=2.0)
                if (
                    msg["type"] == "status"
                    and msg["payload"]["session_status"] == "listening"
                    and msg["payload"]["reason"] == "interrupted"
                ):
                    interrupted = True
                    break
            except asyncio.TimeoutError:
                break

        assert interrupted


# ---------------------------------------------------------------------------
# invalid token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_token_rejected(default_port=None):
    port = _start_server(Scenario.DEFAULT)
    with pytest.raises(websockets.exceptions.InvalidStatus):
        await websockets.connect(
            f"ws://127.0.0.1:{port}/v1/ws/stream?token=wrong-token&device_id={_device_id()}",
            close_timeout=1,
        )


# ---------------------------------------------------------------------------
# Contract test: every S2C message emitted is valid per ServerMessageAdapter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contract_all_s2c_messages_valid():
    """Full conversation flow — every S2C JSON must pass ServerMessageAdapter."""
    port = _start_server(Scenario.WITH_AUDIO)
    async with await _connect(port) as ws:
        await _session_start(ws)

        # collect ALL S2C messages
        messages: list[dict] = []
        for _ in range(3):
            msg = await _recv_json(ws)
            messages.append(msg)

        # ping
        await _send_json(ws, {
            "type": "ping", "seq": 2, "timestamp": _now_ms(), "payload": {},
        })
        messages.append(await _recv_json(ws))

        # video
        await _send_json(ws, {
            "type": "video",
            "seq": 3,
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
        messages.append(await _recv_json(ws))

        # trigger full audio → response flow
        await _trigger_response(ws, 4)
        for _ in range(25):
            try:
                msg = await asyncio.wait_for(_recv_json(ws), timeout=3.0)
                messages.append(msg)
            except asyncio.TimeoutError:
                break

    # Validate every message
    for msg in messages:
        raw = json.dumps(msg)
        try:
            ServerMessageAdapter.validate_json(raw)
        except Exception as e:
            pytest.fail(f"ServerMessageAdapter rejected: {raw}\nError: {e}")

    # Type coverage check
    types_seen = {m["type"] for m in messages}
    assert "session.ready" in types_seen
    assert "status" in types_seen
    assert "pong" in types_seen
    assert "ai_video_ack" in types_seen
    assert "transcription" in types_seen
    assert "ai_text" in types_seen
    assert "ai_audio" in types_seen


# ---------------------------------------------------------------------------
# video_gallery scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_video_gallery_returns_ai_text_with_image():
    port = _start_server(Scenario.VIDEO_GALLERY)
    async with await _connect(port) as ws:
        await _session_start(ws)
        for _ in range(3):
            await _recv_json(ws)

        await _send_json(ws, {
            "type": "video",
            "seq": 2,
            "timestamp": _now_ms(),
            "payload": {
                "data": "AAAA",
                "frame_id": 1,
                "width": 64,
                "height": 64,
                "orientation": 0,
                "camera_facing": "gallery",
                "quality": 70,
                "text": "描述图片内容",
            },
        })

        messages = []
        for _ in range(4):
            messages.append(await asyncio.wait_for(_recv_json(ws), timeout=2.0))

        types_seen = [m["type"] for m in messages]
        assert types_seen[0] == "ai_video_ack"
        assert "status" in types_seen
        assert "ai_text" in types_seen

        ai_text = next(m for m in messages if m["type"] == "ai_text")
        image = ai_text["payload"]["image"]
        assert image["mime"] == "image/jpeg"
        assert image["camera_facing"] == "gallery"
        assert image["data"]
