from __future__ import annotations

import json

import websockets

from tts.providers.elevenlabs import ElevenLabsTTSProvider


async def _elevenlabs_mock(ws):
    # First message is BOS
    init = json.loads(await ws.recv())
    assert init["text"] == " "

    # Receive text and send audio
    await ws.recv()  # text message
    await ws.send(json.dumps({"audio": "AAAA"}))  # base64-encoded audio stub

    # Flush
    await ws.recv()  # empty text
    await ws.recv()  # flush


class TestElevenLabsProvider:
    async def test_open_and_feed_text(self):
        async with websockets.serve(_elevenlabs_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = ElevenLabsTTSProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="eleven_turbo_v2_5",
                voice="v",
            )
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.feed_text("Hello world")
            await stream.flush()
            await stream.close()

    async def test_close_cleans_up(self):
        async with websockets.serve(_elevenlabs_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = ElevenLabsTTSProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="m",
                voice="v",
            )
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.close()
            await stream.close()
