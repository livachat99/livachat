from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from asr.base import AsrResult, AsrStream
from shared.utils.errors import ProviderError

logger = logging.getLogger("livachat.asr.bailian")


class _BailianStream:
    def __init__(self, ws: ClientConnection, task_id: str) -> None:
        self._ws = ws
        self._task_id = task_id
        self._queue: asyncio.Queue[AsrResult | None] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None
        self._final_text = ""
        self._transcription_id = ""

    async def _start_receiver(self) -> None:
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        try:
            while True:
                try:
                    raw = await self._ws.recv()
                except Exception as exc:
                    logger.debug("bailian recv closed", extra={"error": str(exc)})
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                header = msg.get("header", {})
                event = header.get("event", "")

                if event == "task-started":
                    logger.info("bailian asr task started", extra={"task_id": self._task_id})
                    continue

                if event == "task-finished":
                    logger.info("bailian asr task finished", extra={"task_id": self._task_id})
                    break

                if event == "task-failed":
                    error_code = header.get("error_code", "unknown")
                    error_msg = header.get("error_message", "unknown error")
                    # extra= 被服务端 structlog 渲染静默丢弃（2026-09-13 真机日志实证：
                    # 只剩 event 名、错误码全丢）——诊断值必须写进消息体
                    logger.error("bailian asr task failed code=%s message=%s", error_code, error_msg)
                    # 服务端判死（如长静默 29min 超时）：不产出文本结果——会作为
                    # 真实转写直推设备并持久化。关闭 ws 让 feed 检测 CLOSED 抛
                    # ProviderError，orchestrator 走现有流重建路径
                    # （_handle_audio L631-688）。
                    if self._ws.protocol.state.name != "CLOSED":
                        try:
                            await self._ws.close()
                        except Exception:
                            pass
                    break

                # result-generated: uses either "sentence" or "transcription" sub-object
                if event == "result-generated":
                    output = msg.get("payload", {}).get("output", {})
                    sentence = output.get("sentence", output.get("transcription", {}))
                    # 保活包（heartbeat=true, sentence_id=0）不是识别结果：
                    # 跳过且不触碰 _final_text（qwen-audio-3.0 / fun-asr 系列协议）
                    text = sentence.get("text", "")
                    # 逐包一行会淹没 DEBUG（真机实测噪声期每秒 10+ 个空包）：
                    # 只留"心跳/句尾/有文本"三类关键包，其余静默跳过
                    if sentence.get("heartbeat") or sentence.get("sentence_end") or text:
                        logger.debug(
                            "bailian result heartbeat=%s sentence_id=%s sentence_end=%s end_time=%s text_len=%d",
                            sentence.get("heartbeat"), sentence.get("sentence_id"),
                            sentence.get("sentence_end"), sentence.get("end_time"), len(text),
                        )
                    if sentence.get("heartbeat"):
                        continue
                    # is_final: "sentence" format uses end_time != null; "transcription" format uses sentence_end
                    is_final = sentence.get("end_time") is not None or sentence.get("sentence_end", False)

                    if not self._transcription_id and text:
                        self._transcription_id = header.get("task_id", self._task_id)

                    result = AsrResult(
                        text=text,
                        is_final=is_final,
                        transcription_id=self._transcription_id,
                    )
                    await self._queue.put(result)

                    if is_final and text:
                        self._final_text = text

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("bailian receive loop error")
        finally:
            await self._queue.put(None)

    async def feed(self, pcm: bytes) -> None:
        if self._ws.protocol.state.name == "CLOSED":
            raise ProviderError("bailian websocket closed", provider="alibaba_bailian")
        try:
            await self._ws.send(pcm)
        except Exception as exc:
            raise ProviderError(str(exc), provider="alibaba_bailian") from exc

    async def results(self) -> AsyncIterator[AsrResult]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    async def final(self) -> str:
        finish_msg = {
            "header": {
                "action": "finish-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }
        try:
            await self._ws.send(json.dumps(finish_msg))
        except Exception:
            pass
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
        self._transcription_id = ""
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


class AlibabaBailianProvider:
    name = "alibaba_bailian"

    def __init__(self, base_url: str, api_key: str, model: str,
                 params: dict[str, Any] | None = None,
                 workspace_id: str | None = None) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._params = params or {}
        self._workspace_id = workspace_id

    def _headers(self) -> dict[str, str]:
        """workspace_id 存在时附加 X-DashScope-WorkSpace 头（与 TTS 侧同一约定；
        实测本账号 ASR 缺头亦可连，头按官方推荐路径保留）。"""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._workspace_id:
            headers["X-DashScope-WorkSpace"] = self._workspace_id
        return headers

    async def open(
        self,
        *,
        sample_rate: int,
        language: str | None = None,
        interim_results: bool = True,
    ) -> AsrStream:
        try:
            # 限时 close 握手：该端点不回 close 帧，默认 close_timeout=10 会让每次
            # 关闭阻塞 10s（模式切换/收尾路径曾因此卡 15s，
            # 见 docs/translate_lecture_improvements.md）；到点由库关 socket，无泄漏
            ws = await websockets.connect(
                self._base_url,
                additional_headers=self._headers(),
                close_timeout=1,
            )
        except Exception as exc:
            raise ProviderError(f"bailian connect failed: {exc}", provider="alibaba_bailian") from exc

        # stdlib logging 的 extra= 会被服务端 structlog 渲染丢弃，关键信息写进消息体
        logger.info("bailian asr open model=%s workspace=%s",
                    self._model, self._workspace_id or "-")

        task_id = str(uuid.uuid4())

        start_msg: dict[str, Any] = {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._model,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": sample_rate,
                },
                "input": {},
            },
        }

        if language:
            start_msg["payload"]["parameters"]["language_hints"] = [language]

        # Merge any extra provider params
        if self._params:
            start_msg["payload"]["parameters"].update(self._params)

        try:
            await ws.send(json.dumps(start_msg))
        except Exception as exc:
            await ws.close()
            raise ProviderError(f"bailian start failed: {exc}", provider="alibaba_bailian") from exc

        stream = _BailianStream(ws, task_id)
        await stream._start_receiver()
        return stream
