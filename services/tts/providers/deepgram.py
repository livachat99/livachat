from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

from shared.utils.errors import ProviderError
from tts.base import TtsStream

logger = logging.getLogger("livachat.tts.deepgram")


class _DeepgramStream:
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
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
                else:
                    try:
                        msg = json.loads(raw)
                        audio_b64 = msg.get("audio")
                        if audio_b64:
                            import base64

                            await self._queue.put(base64.b64decode(audio_b64))
                    except (json.JSONDecodeError, KeyError):
                        pass
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("deepgram tts receive loop error")

    async def reset(self) -> None:
        """Reset for a new utterance on the existing WebSocket connection."""
        await self._cancel_receiver()
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue = asyncio.Queue()
        await self._start_receiver()

    async def feed_text(self, text: str) -> None:
        if self._ws.protocol.state.name == "CLOSED":
            raise ProviderError("deepgram tts websocket closed", provider="deepgram")
        try:
            await self._ws.send(json.dumps({"type": "Speak", "text": text}))
        except Exception as exc:
            raise ProviderError(str(exc), provider="deepgram") from exc

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
            await self._ws.send(json.dumps({"type": "Flush"}))
        except Exception:
            pass

    async def close(self) -> None:
        await self._cancel_receiver()
        if self._ws.protocol.state.name != "CLOSED":
            try:
                await self._ws.close()
            except Exception:
                pass


class DeepgramTTSProvider:
    name = "deepgram"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model

    async def open(self, *, voice: str, sample_rate: int, audio_format: str = "pcm") -> TtsStream:
        url = f"{self._base_url}?model={self._model}&encoding=linear16&sample_rate={sample_rate}"
        headers = {"Authorization": f"Token {self._api_key}"}

        try:
            # close 握手限时 1s：会话级流关闭在派发循环内，默认 10s 会卡模式切换/收尾
            # （见 docs/translate_lecture_improvements.md）
            ws = await websockets.connect(
                url, additional_headers=headers, close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"deepgram tts connect failed: {exc}", provider="deepgram") from exc

        stream = _DeepgramStream(ws)
        await stream._start_receiver()
        return stream
