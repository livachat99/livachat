from __future__ import annotations

import asyncio
import json

import pytest
import websockets

pytestmark = pytest.mark.integration


def _session_start(seq: int) -> str:
    return json.dumps(
        {
            "type": "session.start",
            "seq": seq,
            "timestamp": 1000,
            "payload": {
                "session_id": None,
                "config": {
                    "enable_video": True,
                    "audio_sample_rate": 24000,
                    "vad_sensitivity": "medium",
                    "client_version": "android-1.0.0",
                },
            },
        }
    )


def _audio(seq: int) -> str:
    return json.dumps(
        {
            "type": "audio",
            "seq": seq,
            "timestamp": 1000,
            "payload": {"data": "x", "codec": "opus", "duration_ms": 20, "vad_state": "speech"},
        }
    )


class TestSeqGuard:
    async def test_seq_regression_silently_dropped(self, ws_url: str):
        """A regressed seq causes the frame to be silently dropped: no error
        response, and the connection stays healthy for subsequent frames.

        (The per-frame regression decision itself is unit-tested in
        tests/unit/gateway/test_seq.py; this verifies the end-to-end wire
        behavior: drop without error, no connection disruption.)
        """
        async with websockets.connect(ws_url) as ws:
            # session.start — 合法业务帧，建立会话
            await ws.send(_session_start(10))
            got_ready = False
            while True:
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                if resp["type"] == "session.ready":
                    got_ready = True
                    break
            assert got_ready

            # 正常业务帧（audio, seq 20）
            await ws.send(_audio(20))
            # 回归 seq 5 → 静默丢弃：不应产生任何响应
            await ws.send(_audio(5))
            await asyncio.sleep(0.4)

            # ping 不经过 seq guard：验证连接仍健康，且无 error 残留
            await ws.send(json.dumps({"type": "ping", "seq": 6, "timestamp": 1000, "payload": {}}))
            got_pong = False
            errors: list[dict] = []
            while True:
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                if resp["type"] == "error":
                    errors.append(resp)
                elif resp["type"] == "pong":
                    got_pong = True
                    break
            assert got_pong
            assert errors == []  # 回归消息被静默丢弃（无 error）
