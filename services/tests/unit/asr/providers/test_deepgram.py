from __future__ import annotations

import json

import websockets

from asr.providers.deepgram import DeepgramProvider


async def _deepgram_mock(ws):
    """Simulate Deepgram: receive binary PCM, send JSON results."""
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                # Simulate interim result after first audio
                await ws.send(
                    json.dumps(
                        {
                            "type": "Results",
                            "is_final": False,
                            "channel": {"alternatives": [{"transcript": "interim", "confidence": 0.8}]},
                        }
                    )
                )
                # Simulate final result
                await ws.send(
                    json.dumps(
                        {
                            "type": "Results",
                            "is_final": True,
                            "channel": {"alternatives": [{"transcript": "hello world", "confidence": 0.95}]},
                        }
                    )
                )
                break
    except websockets.ConnectionClosed:
        pass


class TestDeepgramProvider:
    async def test_open_and_results(self):
        async with websockets.serve(_deepgram_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = DeepgramProvider(
                base_url=f"ws://127.0.0.1:{port}/v1/listen",
                api_key="test-key",
                model="nova-2",
            )
            stream = await provider.open(sample_rate=24000)

            await stream.feed(b"\x00" * 960)
            results = []
            async for r in stream.results():
                results.append(r)

            assert len(results) >= 2
            assert results[0].is_final is False
            assert results[0].text == "interim"
            assert results[-1].is_final is True
            assert results[-1].text == "hello world"

            await stream.close()

    async def test_close_cleans_up(self):
        async with websockets.serve(_deepgram_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = DeepgramProvider(
                base_url=f"ws://127.0.0.1:{port}/v1/listen",
                api_key="test-key",
                model="nova-2",
            )
            stream = await provider.open(sample_rate=16000)
            await stream.close()
            # Closing twice should not raise
            await stream.close()

    async def test_reset_clears_state(self):
        async with websockets.serve(_deepgram_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = DeepgramProvider(
                base_url=f"ws://127.0.0.1:{port}/v1/listen",
                api_key="test-key",
                model="nova-2",
            )
            stream = await provider.open(sample_rate=24000)
            await stream.feed(b"\x00" * 960)

            # Drain results
            async for _ in stream.results():
                pass

            await stream.reset()
            final = await stream.final()
            assert final == ""
            await stream.close()
