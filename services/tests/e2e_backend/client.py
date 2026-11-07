"""Lightweight async WebSocket E2E client based on livachat-protocol DTOs."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Self

import websockets
from livachat_protocol.generated.client_message import ClientPing
from livachat_protocol.generated.protocol_models import (
    ClientInterruptPayload,
    ClientPingPayload,
    ClientSessionStartConfig,
    ClientSessionStartPayload,
)

from shared.utils.time import now_ms


class E2EClient:
    """Lightweight async WS client for E2E tests.

    Messages are constructed via the livachat-protocol DTOs where possible
    and sent over a raw websockets connection.
    """

    def __init__(self, url: str):
        self._url = url
        self._ws: Any = None
        self.received: list[dict] = []

    async def __aenter__(self) -> Self:
        self._ws = await websockets.connect(self._url)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._ws:
            await self._ws.close()

    async def send_raw(self, data: str) -> None:
        await self._ws.send(data)

    async def send(self, msg: Any) -> None:
        if hasattr(msg, "model_dump_json"):
            raw = msg.model_dump_json(exclude_unset=True)
        else:
            raw = json.dumps(msg)
        await self._ws.send(raw)

    async def recv(self, timeout: float = 5.0) -> dict:
        raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        data = json.loads(raw)
        self.received.append(data)
        return data

    async def recv_all(self, count: int, timeout: float = 5.0) -> list[dict]:
        results = []
        for _ in range(count):
            results.append(await self.recv(timeout=timeout))
        return results

    # -- convenience methods using protocol DTOs --------------------------

    async def ping(self, seq: int = 1) -> dict:
        msg = ClientPing(seq=seq, timestamp=now_ms(), payload=ClientPingPayload())
        await self.send(msg)
        return await self.recv()

    async def session_start(
        self,
        *,
        seq: int = 1,
        enable_video: bool = False,
        audio_sample_rate: int = 24000,
        vad_sensitivity: str = "medium",
        client_version: str = "android-1.0.0",
        session_id: str | None = None,
        timezone: str | None = None,
    ) -> None:
        cfg = ClientSessionStartConfig(
            enable_video=enable_video,
            audio_sample_rate=audio_sample_rate,
            vad_sensitivity=vad_sensitivity,
            client_version=client_version,
            timezone=timezone,
        )
        payload = ClientSessionStartPayload(session_id=session_id, config=cfg)
        raw = json.dumps({"type": "session.start", "seq": seq, "timestamp": now_ms(), "payload": payload.model_dump()})
        await self.send_raw(raw)

    async def interrupt(self, *, seq: int = 1, reason: str = "manual") -> None:
        payload = ClientInterruptPayload(reason=reason)
        raw = json.dumps({"type": "interrupt", "seq": seq, "timestamp": now_ms(), "payload": payload.model_dump()})
        await self.send_raw(raw)

    async def audio_frame(self, *, seq: int, data: str = "AAAA", vad_state: str = "speech") -> None:
        from livachat_protocol.generated.protocol_models import ClientAudioPayload
        payload = ClientAudioPayload(data=data, codec="opus", duration_ms=20, vad_state=vad_state)
        raw = json.dumps({"type": "audio", "seq": seq, "timestamp": now_ms(), "payload": payload.model_dump()})
        await self.send_raw(raw)
