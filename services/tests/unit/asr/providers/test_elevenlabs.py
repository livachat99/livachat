from __future__ import annotations

import json

import websockets

from asr.providers.elevenlabs import ElevenLabsProvider


async def _elevenlabs_mock(ws):
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                await ws.send(json.dumps({"text": "interim eleven", "is_final": False, "confidence": 0.7}))
                await ws.send(json.dumps({"text": "final eleven", "is_final": True, "confidence": 0.92}))
                break
    except websockets.ConnectionClosed:
        pass


class TestElevenLabsProvider:
    async def test_open_and_results(self):
        async with websockets.serve(_elevenlabs_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = ElevenLabsProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="test-key",
                model="scribe_v1",
            )
            stream = await provider.open(sample_rate=24000)

            await stream.feed(b"\x00" * 960)
            results = []
            async for r in stream.results():
                results.append(r)

            assert len(results) >= 2
            assert results[0].text == "interim eleven"
            assert results[-1].text == "final eleven"
            assert results[-1].is_final is True

            await stream.close()

    async def test_close_cleans_up(self):
        async with websockets.serve(_elevenlabs_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = ElevenLabsProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="test-key",
                model="scribe_v1",
            )
            stream = await provider.open(sample_rate=16000)
            await stream.close()
            await stream.close()

    async def test_reset_new_transcription_id(self):
        async with websockets.serve(_elevenlabs_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = ElevenLabsProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="test-key",
                model="scribe_v1",
            )
            stream = await provider.open(sample_rate=24000)
            await stream.feed(b"\x00" * 960)
            async for _ in stream.results():
                pass
            await stream.reset()
            final = await stream.final()
            assert final == ""
            await stream.close()
