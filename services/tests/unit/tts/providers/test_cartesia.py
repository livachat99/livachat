from __future__ import annotations

import json

import websockets

from tts.providers.cartesia import CartesiaProvider


async def _cartesia_mock(ws):
    msg = json.loads(await ws.recv())  # start message
    assert msg["model"] == "sonic-2"

    # Receive text, send audio
    text_msg = json.loads(await ws.recv())
    assert text_msg["type"] == "text"
    await ws.send(b"\x00" * 100)  # mock PCM audio
    await ws.send(b"\x01" * 100)

    # Receive finish
    finish = json.loads(await ws.recv())
    assert finish["type"] == "finish"


class TestCartesiaProvider:
    async def test_open_and_feed_text(self):
        async with websockets.serve(_cartesia_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = CartesiaProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="sonic-2",
                voice="voice1",
            )
            stream = await provider.open(voice="voice1", sample_rate=24000)
            await stream.feed_text("Hello world")
            await stream.flush()
            await stream.close()

    async def test_close_cleans_up(self):
        async with websockets.serve(_cartesia_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = CartesiaProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="sonic-2",
                voice="v",
            )
            stream = await provider.open(voice="v", sample_rate=24000)
            await stream.close()
            await stream.close()
