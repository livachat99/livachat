from __future__ import annotations

import asyncio
import base64
import json

import websockets

from tts.providers.alibaba_qwen_tts import AlibabaQwenTTSProvider


async def _qwen_mock(ws):
    # 1. session.created
    await ws.send(json.dumps({"type": "session.created", "session": {"id": "sess_1"}}))

    # 2. session.update
    update = json.loads(await ws.recv())
    assert update["type"] == "session.update"
    assert update["session"]["voice"] == "Cherry"
    await ws.send(json.dumps({"type": "session.updated"}))

    # 3. input_text_buffer.append + commit
    append = json.loads(await ws.recv())
    assert append["type"] == "input_text_buffer.append"
    assert append["text"] == "Hello"
    commit = json.loads(await ws.recv())
    assert commit["type"] == "input_text_buffer.commit"

    # 4. response.created (carries response.id for turn boundary detection)
    resp_id = "resp_1"
    await ws.send(json.dumps({"type": "response.created", "response": {"id": resp_id}}))

    # 5. Audio with response_id
    test_audio = b"\x00\x01\x02\x03"
    await ws.send(json.dumps({
        "type": "response.audio.delta",
        "response_id": resp_id,
        "delta": base64.b64encode(test_audio).decode(),
    }))
    await ws.send(json.dumps({"type": "response.audio.done", "response_id": resp_id}))

    # 6. response.done (carries response.id, needed for SENTINEL)
    await ws.send(json.dumps({"type": "response.done", "response": {"id": resp_id}}))

    # 7. session.finish
    finish = json.loads(await ws.recv())
    assert finish["type"] == "session.finish"
    await ws.send(json.dumps({"type": "session.finished"}))


class TestQwenTTSProvider:
    async def test_open_and_feed_text(self):
        async with websockets.serve(_qwen_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaQwenTTSProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="qwen3-tts-flash-realtime",
                voice="Cherry",
            )
            stream = await provider.open(voice="Cherry", sample_rate=24000)
            await stream.feed_text("Hello")
            await stream.flush()

            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\x00\x01\x02\x03"]

            await stream.close()

    async def test_close_cleans_up(self):
        async with websockets.serve(_qwen_mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaQwenTTSProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="qwen3-tts-flash-realtime",
                voice="Cherry",
            )
            stream = await provider.open(voice="Cherry", sample_rate=24000)
            await stream.close()
            await stream.close()


class TestQwenTTSReconnect:
    async def test_reconnect_after_receive_loop_crash(self):
        """Dead connection (receive loop crash) must be proactively
        reconnected by reset()/feed_text() instead of writing to the broken
        socket (provider closes idle WS with 1007; the old code restarted the
        receiver on the dead connection, and close() could hang on the send).
        """
        crash_done = False

        async def mock(ws):
            nonlocal crash_done
            await ws.send(json.dumps({"type": "session.created", "session": {"id": "s"}}))
            update = json.loads(await ws.recv())
            assert update["type"] == "session.update"
            await ws.send(json.dumps({"type": "session.updated"}))
            if not crash_done:
                # Simulate the provider closing an idle connection (1007)
                crash_done = True
                await ws.close(1007, "request timeout after 300 seconds")
                return
            # Second connection (after reconnect): serve one synthesis
            append = json.loads(await ws.recv())
            assert append["type"] == "input_text_buffer.append"
            commit = json.loads(await ws.recv())
            assert commit["type"] == "input_text_buffer.commit"
            await ws.send(json.dumps({"type": "response.created", "response": {"id": "r2"}}))
            await ws.send(json.dumps({
                "type": "response.audio.delta",
                "response_id": "r2",
                "delta": base64.b64encode(b"\xaa\xbb").decode(),
            }))
            await ws.send(json.dumps({"type": "response.done", "response": {"id": "r2"}}))
            await asyncio.sleep(0.1)

        async with websockets.serve(mock, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaQwenTTSProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="qwen3-tts-flash-realtime",
                voice="Cherry",
            )
            stream = await provider.open(voice="Cherry", sample_rate=24000)

            # Let the receive loop observe the crash (marks _ws = None)
            await asyncio.sleep(0.2)
            assert stream._ws is None

            # reset() must reconnect, not restart the receiver on the dead socket
            await stream.reset()
            assert stream._ws is not None

            # feed_text on the fresh connection succeeds
            await stream.feed_text("Hello")
            await stream.flush()
            chunks = [c async for c in stream.audio()]
            assert chunks == [b"\xaa\xbb"]

            # close() on a healthy connection is still clean
            await stream.close()
            assert stream._ws.protocol.state.name in ("CLOSED", "CLOSING")
