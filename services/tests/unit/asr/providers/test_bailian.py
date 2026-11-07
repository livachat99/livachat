from __future__ import annotations

import json

import pytest
import websockets

from asr.providers.alibaba_bailian import AlibabaBailianProvider
from shared.utils.errors import ProviderError


async def _task_failed_mock(ws):
    """收到音频后发 task-failed（模拟服务端判死，如长静默超时）。"""
    try:
        first = await ws.recv()
        start_msg = json.loads(first)
        task_id = start_msg["header"]["task_id"]
        await ws.send(
            json.dumps(
                {
                    "header": {"task_id": task_id, "event": "task-started", "attributes": {}},
                    "payload": {},
                }
            )
        )
        async for msg in ws:
            if isinstance(msg, bytes):
                await ws.send(
                    json.dumps(
                        {
                            "header": {
                                "task_id": task_id,
                                "event": "task-failed",
                                "error_code": "CLIENT_ERROR",
                                "error_message": "Did not observe any item or terminal signal",
                                "attributes": {},
                            },
                            "payload": {},
                        }
                    )
                )
                break
    except websockets.ConnectionClosed:
        pass


async def _bailian_mock(ws):
    try:
        # First message should be the run-task start message
        first = await ws.recv()
        start_msg = json.loads(first)
        assert start_msg["header"]["action"] == "run-task"
        assert start_msg["payload"]["task"] == "asr"

        task_id = start_msg["header"]["task_id"]

        # Send task-started
        await ws.send(
            json.dumps(
                {
                    "header": {"task_id": task_id, "event": "task-started", "attributes": {}},
                    "payload": {},
                }
            )
        )

        # Now receive binary audio or finish-task
        async for msg in ws:
            if isinstance(msg, bytes):
                # Send interim then final result
                await ws.send(
                    json.dumps(
                        {
                            "header": {"task_id": task_id, "event": "result-generated", "attributes": {}},
                            "payload": {
                                "output": {
                                    "transcription": {"text": "interim bailian", "sentence_end": False}
                                }
                            },
                        }
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "header": {"task_id": task_id, "event": "result-generated", "attributes": {}},
                            "payload": {
                                "output": {
                                    "transcription": {"text": "final bailian", "sentence_end": True}
                                }
                            },
                        }
                    )
                )
                # Send task-finished so the client's receive loop exits cleanly
                await ws.send(
                    json.dumps(
                        {
                            "header": {"task_id": task_id, "event": "task-finished", "attributes": {}},
                            "payload": {},
                        }
                    )
                )
                break
            else:
                msg_obj = json.loads(msg)
                if msg_obj["header"]["action"] == "finish-task":
                    await ws.send(
                        json.dumps(
                            {
                                "header": {"task_id": task_id, "event": "task-finished", "attributes": {}},
                                "payload": {},
                            }
                        )
                    )
                    break
    except websockets.ConnectionClosed:
        pass


async def _heartbeat_mock(ws):
    """result-generated 序列：interim → heartbeat 保活包 → final。

    保活包形态取自官方文档（heartbeat=true、sentence_id=0），不应产出识别结果。
    """
    try:
        first = await ws.recv()
        start_msg = json.loads(first)
        task_id = start_msg["header"]["task_id"]
        await ws.send(
            json.dumps(
                {
                    "header": {"task_id": task_id, "event": "task-started", "attributes": {}},
                    "payload": {},
                }
            )
        )
        async for msg in ws:
            if isinstance(msg, bytes):
                await ws.send(
                    json.dumps(
                        {
                            "header": {"task_id": task_id, "event": "result-generated", "attributes": {}},
                            "payload": {
                                "output": {"sentence": {"text": "interim bailian", "sentence_end": False}},
                                "usage": None,
                            },
                        }
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "header": {"task_id": task_id, "event": "result-generated", "attributes": {}},
                            "payload": {
                                "output": {
                                    "sentence": {
                                        "begin_time": 0, "end_time": None, "text": "",
                                        "heartbeat": True, "sentence_begin": False,
                                        "sentence_end": False, "sentence_id": 0,
                                    }
                                },
                                "usage": None,
                            },
                        }
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "header": {"task_id": task_id, "event": "result-generated", "attributes": {}},
                            "payload": {
                                "output": {"sentence": {"text": "final bailian", "sentence_end": True}},
                                "usage": None,
                            },
                        }
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "header": {"task_id": task_id, "event": "task-finished", "attributes": {}},
                            "payload": {},
                        }
                    )
                )
                break
            else:
                msg_obj = json.loads(msg)
                if msg_obj["header"]["action"] == "finish-task":
                    await ws.send(
                        json.dumps(
                            {
                                "header": {"task_id": task_id, "event": "task-finished", "attributes": {}},
                                "payload": {},
                            }
                        )
                    )
                    break
    except websockets.ConnectionClosed:
        pass


async def _capture_start_mock(ws, captured):
    """记录 run-task 报文后正常应答，供参数合并断言。"""
    try:
        first = await ws.recv()
        captured.append(json.loads(first))
        task_id = captured[-1]["header"]["task_id"]
        await ws.send(
            json.dumps(
                {
                    "header": {"task_id": task_id, "event": "task-started", "attributes": {}},
                    "payload": {},
                }
            )
        )
        async for msg in ws:
            if isinstance(msg, bytes):
                await ws.send(
                    json.dumps(
                        {
                            "header": {"task_id": task_id, "event": "task-finished", "attributes": {}},
                            "payload": {},
                        }
                    )
                )
                break
    except websockets.ConnectionClosed:
        pass


class TestBailianProvider:
    async def test_open_and_results(self):
        async with websockets.serve(_bailian_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaBailianProvider(
                base_url=f"ws://127.0.0.1:{port}/api-ws/v1/inference",
                api_key="test-key",
                model="paraformer-realtime-v2",
            )
            stream = await provider.open(sample_rate=16000)

            await stream.feed(b"\x00" * 640)
            results = []
            async for r in stream.results():
                results.append(r)

            assert len(results) >= 2
            assert results[0].text == "interim bailian"
            assert results[-1].text == "final bailian"
            assert results[-1].is_final is True

            await stream.close()

    async def test_start_message_sent_on_open(self):
        async with websockets.serve(_bailian_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaBailianProvider(
                base_url=f"ws://127.0.0.1:{port}/api-ws/v1/inference",
                api_key="test-key",
                model="paraformer-realtime-v2",
            )
            stream = await provider.open(sample_rate=16000)

            # If the mock received the start message, open() succeeded
            await stream.feed(b"\x00" * 640)
            async for _ in stream.results():
                pass
            await stream.close()

    async def test_task_failed_closes_stream_and_no_error_text(self):
        """task-failed 不产出错误文本（不直推设备/持久化），并关闭 ws
        让 feed 抛 ProviderError → orchestrator 走流重建路径。"""
        async with websockets.serve(_task_failed_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaBailianProvider(
                base_url=f"ws://127.0.0.1:{port}/api-ws/v1/inference",
                api_key="test-key",
                model="paraformer-realtime-v2",
            )
            stream = await provider.open(sample_rate=16000)

            await stream.feed(b"\x00" * 640)
            results = []
            async for r in stream.results():
                results.append(r)

            # 不产出 "[task-failed: ...]" 错误文本
            assert not any("[task-failed" in (r.text or "") for r in results)

            # ws 已关闭 → feed 抛 ProviderError（重建信号）
            with pytest.raises(ProviderError):
                await stream.feed(b"\x00" * 640)

            await stream.close()

    async def test_close_cleans_up(self):
        async with websockets.serve(_bailian_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaBailianProvider(
                base_url=f"ws://127.0.0.1:{port}/api-ws/v1/inference",
                api_key="test-key",
                model="paraformer-realtime-v2",
            )
            stream = await provider.open(sample_rate=16000)
            await stream.close()
            await stream.close()

    async def test_close_timeout_bounded(self):
        """close 握手限时 1s：该端点不回 close 帧，默认 10s 会让每次关闭阻塞 10s
        （模式切换曾因此卡 15s，见 docs/translate_lecture_improvements.md）。"""
        async with websockets.serve(_bailian_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaBailianProvider(
                base_url=f"ws://127.0.0.1:{port}/api-ws/v1/inference",
                api_key="test-key",
                model="qwen-audio-3.0-asr-flash-streaming",
            )
            stream = await provider.open(sample_rate=16000)

            assert stream._ws.close_timeout == 1
            await stream.close()

    def test_workspace_header(self):
        """base_url = 连接端点（不派生不改写）；workspace_id 只驱动请求头。"""
        base = "wss://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        provider = AlibabaBailianProvider(
            base_url=base,
            api_key="k",
            model="qwen-audio-3.0-asr-flash-streaming",
            workspace_id="llm-1tmsqifo51cwfadr",
        )
        assert provider._base_url == base
        assert provider._headers() == {
            "Authorization": "Bearer k",
            "X-DashScope-WorkSpace": "llm-1tmsqifo51cwfadr",
        }

    def test_workspace_header_absent_by_default(self):
        provider = AlibabaBailianProvider(
            base_url="ws://127.0.0.1:0/api-ws/v1/inference",
            api_key="k",
            model="paraformer-realtime-v2",
        )
        assert provider._headers() == {"Authorization": "Bearer k"}

    async def test_params_merged_into_run_task(self):
        """配置 params 必须落入 run-task parameters——这是长静默保活（heartbeat）
        与语种提示的唯一投递通道；合并被移除时不会有任何显式报错（静默回退到
        300s 静音判死），故用本用例锁住契约。"""
        captured: list[dict] = []

        async def mock(ws):
            await _capture_start_mock(ws, captured)

        async with websockets.serve(mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaBailianProvider(
                base_url=f"ws://127.0.0.1:{port}/api-ws/v1/inference",
                api_key="test-key",
                model="qwen-audio-3.0-asr-flash-streaming",
                params={"heartbeat": True, "language_hints": ["zh", "en"]},
            )
            stream = await provider.open(sample_rate=16000)
            await stream.feed(b"\x00" * 640)
            async for _ in stream.results():
                pass
            await stream.close()

        params = captured[0]["payload"]["parameters"]
        assert params["heartbeat"] is True
        assert params["language_hints"] == ["zh", "en"]
        assert params["format"] == "pcm"
        assert params["sample_rate"] == 16000

    async def test_heartbeat_packet_skipped(self):
        """保活包（heartbeat=true, sentence_id=0）不产出结果、不污染 final_text。"""
        async with websockets.serve(_heartbeat_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaBailianProvider(
                base_url=f"ws://127.0.0.1:{port}/api-ws/v1/inference",
                api_key="test-key",
                model="qwen-audio-3.0-asr-flash-streaming",
            )
            stream = await provider.open(sample_rate=16000)

            await stream.feed(b"\x00" * 640)
            results = []
            async for r in stream.results():
                results.append(r)

            # 心跳包被跳过：恰好 interim + final 两条（未跳过会出现第三条空文本）
            assert [r.text for r in results] == ["interim bailian", "final bailian"]
            assert stream.final_text == "final bailian"

            await stream.close()
