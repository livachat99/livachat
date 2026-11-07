from __future__ import annotations

import base64
import json

import websockets

from asr.providers.alibaba_qwen_asr import AlibabaQwenASRProvider


async def _qwen_asr_mock_results(ws):
    """Mock that sends results immediately on receiving audio (for results() tests)."""
    # 1. session.created
    await ws.send(json.dumps({"type": "session.created", "session": {"id": "sess_1"}}))

    # 2. session.update
    update = json.loads(await ws.recv())
    assert update["type"] == "session.update"
    assert update["session"]["input_audio_format"] == "pcm"
    await ws.send(json.dumps({"type": "session.updated"}))

    # 3. input_audio_buffer.append — verify base64 audio
    append = json.loads(await ws.recv())
    assert append["type"] == "input_audio_buffer.append"
    assert "audio" in append
    decoded = base64.b64decode(append["audio"])
    assert decoded == b"\x00\x01\x02\x03"

    # 4. Send interim + final results, then session.finished to end the stream
    await ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.text", "stash": "Hello"}))
    await ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Hello world"}))
    await ws.send(json.dumps({"type": "session.finished"}))


async def _qwen_asr_mock_final(ws):
    """Mock that expects session.finish in final(), sends result only then."""
    # session.created + session.updated
    await ws.send(json.dumps({"type": "session.created", "session": {"id": "sess_1"}}))
    update = json.loads(await ws.recv())
    assert update["type"] == "session.update"
    await ws.send(json.dumps({"type": "session.updated"}))

    # audio
    append = json.loads(await ws.recv())
    assert append["type"] == "input_audio_buffer.append"

    # final() → session.finish
    finish = json.loads(await ws.recv())
    assert finish["type"] == "session.finish"

    # Send result
    await ws.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Hello world"}))
    await ws.send(json.dumps({"type": "session.finished"}))


class TestQwenASRProvider:
    async def test_open_and_results(self):
        async with websockets.serve(_qwen_asr_mock_results, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaQwenASRProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="qwen3-asr-flash-realtime",
            )
            stream = await provider.open(sample_rate=16000, language="zh")
            await stream.feed(b"\x00\x01\x02\x03")

            results = []
            async for r in stream.results():
                results.append(r)

            assert len(results) == 2
            assert results[0].text == "Hello"
            assert results[0].is_final is False
            assert results[1].text == "Hello world"
            assert results[1].is_final is True

            await stream.close()

    async def test_final_returns_text(self):
        async with websockets.serve(_qwen_asr_mock_final, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaQwenASRProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="qwen3-asr-flash-realtime",
            )
            stream = await provider.open(sample_rate=16000)
            await stream.feed(b"\x00\x01\x02\x03")

            final_text = await stream.final()
            assert final_text == "Hello world"

            await stream.close()

    async def test_close_cleans_up(self):
        async with websockets.serve(_qwen_asr_mock_results, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaQwenASRProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="qwen3-asr-flash-realtime",
            )
            stream = await provider.open(sample_rate=16000)
            await stream.close()
            await stream.close()
