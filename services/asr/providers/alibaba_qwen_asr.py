from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from asr.base import AsrResult, AsrStream
from shared.utils.errors import ProviderError

logger = logging.getLogger("livachat.asr.qwen")


class _QwenAsrStream:
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[AsrResult | None] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._final_text = ""
        self._transcription_id = ""
        self._event_id = 0

    def _next_event_id(self) -> str:
        self._event_id += 1
        return f"evt_{self._event_id}"

    async def _start_receiver(self) -> None:
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = msg.get("type", "")

                if event_type == "conversation.item.input_audio_transcription.text":
                    text = msg.get("stash", "")
                    detected_lang = msg.get("language")
                    await self._queue.put(
                        AsrResult(
                            text=text,
                            is_final=False,
                            transcription_id=self._transcription_id,
                            language=detected_lang,
                        )
                    )

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    text = msg.get("transcript", "")
                    if text:
                        self._final_text = text
                    detected_lang = msg.get("language")
                    await self._queue.put(
                        AsrResult(
                            text=text,
                            is_final=True,
                            transcription_id=self._transcription_id,
                            language=detected_lang,
                        )
                    )

                elif event_type == "session.finished":
                    break

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("qwen asr receive loop error")
        finally:
            await self._queue.put(None)

    async def feed(self, pcm: bytes) -> None:
        if self._ws.protocol.state.name == "CLOSED":
            raise ProviderError("qwen asr websocket closed", provider="alibaba_qwen_asr")
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "event_id": self._next_event_id(),
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm).decode(),
                    }
                )
            )
        except Exception as exc:
            raise ProviderError(str(exc), provider="alibaba_qwen_asr") from exc

    async def results(self) -> AsyncIterator[AsrResult]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    async def final(self) -> str:
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "event_id": self._next_event_id(),
                        "type": "session.finish",
                    }
                )
            )
        except Exception:
            pass

        # Drain remaining results
        try:
            while True:
                item = await asyncio.wait_for(self._queue.get(), timeout=2.0)
                if item is None:
                    break
                if item.is_final and item.text:
                    self._final_text = item.text
        except TimeoutError:
            pass

        return self._final_text

    async def reset(self) -> None:
        self._transcription_id = ""
        self._final_text = ""
        while not self._queue.empty():
            self._queue.get_nowait()
        # Clear server-side buffer
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "event_id": self._next_event_id(),
                        "type": "input_audio_buffer.clear",
                    }
                )
            )
        except Exception:
            pass

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


class AlibabaQwenASRProvider:
    name = "alibaba_qwen_asr"

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
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            # close 握手限时 1s：会话级流关闭在派发循环内，默认 10s 会卡模式切换/收尾
            # （见 docs/translate_lecture_improvements.md）
            ws = await websockets.connect(
                self._base_url, additional_headers=headers, close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"qwen asr connect failed: {exc}", provider="alibaba_qwen_asr") from exc

        try:
            # Wait for session.created
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                msg = json.loads(raw)
                if msg.get("type") != "session.created":
                    raise ProviderError(
                        f"expected session.created, got {msg.get('type')}", provider="alibaba_qwen_asr"
                    )
            except TimeoutError:
                raise ProviderError("timeout waiting for session.created", provider="alibaba_qwen_asr")

            # Send session.update
            update: dict[str, Any] = {
                "event_id": "evt_session_update",
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "input_audio_format": "pcm",
                    "sample_rate": sample_rate,
                    "enableTurnDetection": True,
                },
            }
            if language:
                update["session"]["language"] = language

            # Merge any extra provider params
            if self._params:
                update["session"].update(self._params)

            await ws.send(json.dumps(update))

            # Wait for session.updated (or error)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                msg = json.loads(raw)
                event_type = msg.get("type", "")
                if event_type == "error":
                    err = msg.get("error", {})
                    raise ProviderError(
                        f"server error: {err.get('message', err)} (code={err.get('code', 'unknown')})",
                        provider="alibaba_qwen_asr",
                    )
                if event_type != "session.updated":
                    raise ProviderError(
                        f"expected session.updated, got {event_type}: {json.dumps(msg)}",
                        provider="alibaba_qwen_asr",
                    )
            except TimeoutError:
                raise ProviderError("timeout waiting for session.updated", provider="alibaba_qwen_asr")

        except ProviderError:
            await ws.close()
            raise
        except Exception as exc:
            await ws.close()
            raise ProviderError(f"qwen asr init failed: {exc}", provider="alibaba_qwen_asr") from exc

        stream = _QwenAsrStream(ws)
        await stream._start_receiver()
        return stream
