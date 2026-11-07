from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from asr.base import AsrResult, AsrStream
from shared.utils.errors import ProviderError
from shared.utils.ids import generate_transcription_id

logger = logging.getLogger("livachat.asr.deepgram")

# ---------------------------------------------------------------------------
# AsrStream implementation
# ---------------------------------------------------------------------------


class _DeepgramStream:
    def __init__(self, ws: ClientConnection, sample_rate: int) -> None:
        self._ws = ws
        self._sample_rate = sample_rate
        self._queue: asyncio.Queue[AsrResult | None] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._final_text = ""
        self._transcription_id = generate_transcription_id()

    async def _start_receiver(self) -> None:
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") != "Results":
                    continue

                channel = msg.get("channel", {})
                alternatives = channel.get("alternatives", [])
                text = alternatives[0].get("transcript", "") if alternatives else ""
                is_final = msg.get("is_final", False)
                confidence = alternatives[0].get("confidence") if alternatives else None
                detected_lang = channel.get("detected_language")

                result = AsrResult(
                    text=text, is_final=is_final, confidence=confidence,
                    transcription_id=self._transcription_id,
                    language=detected_lang,
                )
                await self._queue.put(result)

                if is_final and text:
                    self._final_text = text
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("deepgram receive loop error")
        finally:
            await self._queue.put(None)  # sentinel

    async def feed(self, pcm: bytes) -> None:
        if self._ws.protocol.state.name == "CLOSED":
            raise ProviderError("deepgram websocket closed", provider="deepgram")
        try:
            await self._ws.send(pcm)
        except Exception as exc:
            raise ProviderError(str(exc), provider="deepgram") from exc

    async def results(self) -> AsyncIterator[AsrResult]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    async def final(self) -> str:
        # Send a zero-length message to signal end of audio (Deepgram close trick)
        try:
            await self._ws.send(b"")
        except Exception:
            pass
        # Drain the queue for final results instead of reading the WebSocket
        # directly, avoiding concurrent reads with the background receiver task.
        try:
            async with asyncio.timeout(2.0):
                while True:
                    item = await self._queue.get()
                    if item is None:  # sentinel
                        break
                    if item.is_final and item.text:
                        self._final_text = item.text
        except TimeoutError:
            pass
        except Exception:
            pass
        return self._final_text

    async def reset(self) -> None:
        self._transcription_id = generate_transcription_id()
        self._final_text = ""
        while not self._queue.empty():
            self._queue.get_nowait()

    @property
    def final_text(self) -> str:
        return self._final_text

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
            self._recv_task = None
        if self._ws.protocol.state.name != "CLOSED":
            try:
                await self._ws.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# AsrProvider implementation
# ---------------------------------------------------------------------------


class DeepgramProvider:
    name = "deepgram"

    def __init__(self, base_url: str, api_key: str, model: str, params: dict[str, Any] | None = None) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._params = params or {}

    async def open(
        self,
        *,
        sample_rate: int,
        language: str | None = None,
        interim_results: bool = True,
    ) -> AsrStream:
        query_parts = [
            f"model={self._model}",
            "encoding=linear16",
            f"sample_rate={sample_rate}",
            f"interim_results={'true' if interim_results else 'false'}",
        ]
        if language:
            query_parts.append(f"language={language}")

        # Merge additional params from config
        for key, val in self._params.items():
            if key in ("model", "encoding", "sample_rate", "interim_results", "language"):
                continue  # already set
            query_parts.append(f"{key}={val}")

        url = f"{self._base_url}?{'&'.join(query_parts)}"
        extra_headers = {"Authorization": f"Token {self._api_key}"}

        try:
            # close 握手限时 1s：会话级流关闭在派发循环内，默认 10s 会卡模式切换/收尾
            # （见 docs/translate_lecture_improvements.md）
            ws = await websockets.connect(
                url, additional_headers=extra_headers, close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"deepgram connect failed: {exc}", provider="deepgram") from exc

        stream = _DeepgramStream(ws, sample_rate)
        await stream._start_receiver()
        return stream
