from __future__ import annotations

import json

import websockets

from tts.providers.deepgram import DeepgramTTSProvider


async def _deepgram_mock(ws):
    # Receive Speak and send audio
    msg = json.loads(await ws.recv())
    assert msg["type"] == "Speak"
    await ws.send(json.dumps({"audio": "AAAA"}))


class TestDeepgramTTSProvider:
    async def test_open_and_speak(self):
        async with websockets.serve(_deepgram_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = DeepgramTTSProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="aura-asteria-en",
            )
            stream = await provider.open(voice="en-US", sample_rate=24000)
            await stream.feed_text("Hello")
            await stream.close()

    async def test_close_cleans_up(self):
        async with websockets.serve(_deepgram_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = DeepgramTTSProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="aura-asteria-en",
            )
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.close()
            await stream.close()
