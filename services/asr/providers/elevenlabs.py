from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

from asr.base import AsrResult, AsrStream
from shared.utils.errors import ProviderError
from shared.utils.ids import generate_transcription_id

logger = logging.getLogger("livachat.asr.elevenlabs")


class _ElevenLabsStream:
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
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

                is_final = msg.get("is_final", False)
                text = msg.get("text", "")
                confidence = msg.get("confidence", None)

                detected_lang = msg.get("detected_language")

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
            logger.exception("elevenlabs receive loop error")
        finally:
            await self._queue.put(None)

    async def feed(self, pcm: bytes) -> None:
        if self._ws.protocol.state.name == "CLOSED":
            raise ProviderError("elevenlabs websocket closed", provider="elevenlabs")
        try:
            await self._ws.send(pcm)
        except Exception as exc:
            raise ProviderError(str(exc), provider="elevenlabs") from exc

    async def results(self) -> AsyncIterator[AsrResult]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    async def final(self) -> str:
        # Send EOS message
        try:
            await self._ws.send(json.dumps({"type": "eos"}))
        except Exception:
            pass
        # Drain remaining results
        try:
            while True:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if item is None:
                    break
                if item.is_final:
                    self._final_text = item.text
        except TimeoutError:
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


class ElevenLabsProvider:
    name = "elevenlabs"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model

    async def open(
        self,
        *,
        sample_rate: int,
        language: str | None = None,
        interim_results: bool = True,
    ) -> AsrStream:
        url = f"{self._base_url}/v1/speech-to-text?model_id={self._model}&output_format=pcm_{sample_rate}"
        extra_headers = {"xi-api-key": self._api_key}

        try:
            # close 握手限时 1s：会话级流关闭在派发循环内，默认 10s 会卡模式切换/收尾
            # （见 docs/translate_lecture_improvements.md）
            ws = await websockets.connect(
                url, additional_headers=extra_headers, close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"elevenlabs connect failed: {exc}", provider="elevenlabs") from exc

        stream = _ElevenLabsStream(ws)
        await stream._start_receiver()
        return stream
