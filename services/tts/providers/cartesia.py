from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from shared.utils.errors import ProviderError
from tts.base import TtsStream

logger = logging.getLogger("livachat.tts.cartesia")


class _CartesiaStream:
    def __init__(self, ws: ClientConnection, sample_rate: int) -> None:
        self._ws = ws
        self._sample_rate = sample_rate
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None

    async def _start_receiver(self) -> None:
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def _cancel_receiver(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
            self._recv_task = None

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    await self._queue.put(raw)
                # Cartesia also sends JSON metadata — we only care about audio bytes
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("cartesia receive loop error")

    async def reset(self) -> None:
        """Reset for a new utterance on the existing WebSocket connection."""
        await self._cancel_receiver()
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue = asyncio.Queue()
        await self._start_receiver()

    async def feed_text(self, text: str) -> None:
        if self._ws.protocol.state.name == "CLOSED":
            raise ProviderError("cartesia websocket closed", provider="cartesia")
        try:
            await self._ws.send(json.dumps({"type": "text", "text": text}))
        except Exception as exc:
            raise ProviderError(str(exc), provider="cartesia") from exc

    async def audio(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._queue.get()
            yield item

    async def poll_audio(self) -> AsyncIterator[bytes]:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            yield item

    async def flush(self) -> None:
        try:
            await self._ws.send(json.dumps({"type": "finish"}))
        except Exception:
            pass

    async def close(self) -> None:
        await self._cancel_receiver()
        if self._ws.protocol.state.name != "CLOSED":
            try:
                await self._ws.close()
            except Exception:
                pass


class CartesiaProvider:
    name = "cartesia"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        voice: str,
        output_format: dict[str, Any] | None = None,
        version: str = "2024-06-10",
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._output_format = output_format or {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 16000}
        self._version = version

    async def open(self, *, voice: str, sample_rate: int, audio_format: str = "pcm") -> TtsStream:
        headers: dict[str, str] = {
            "X-API-Key": self._api_key,
            "Cartesia-Version": self._version,
        }
        try:
            # close 握手限时 1s：会话级流关闭在派发循环内，默认 10s 会卡模式切换/收尾
            # （见 docs/translate_lecture_improvements.md）
            ws = await websockets.connect(
                self._base_url, additional_headers=headers, close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"cartesia connect failed: {exc}", provider="cartesia") from exc

        # Send start message
        start = {
            "model": self._model,
            "voice": {"mode": "id", "id": voice or self._voice},
            "output": self._output_format,
            "language": "zh",
        }
        try:
            await ws.send(json.dumps(start))
        except Exception as exc:
            await ws.close()
            raise ProviderError(f"cartesia start failed: {exc}", provider="cartesia") from exc

        stream = _CartesiaStream(ws, sample_rate)
        await stream._start_receiver()
        return stream
