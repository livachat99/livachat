"""Explore the real Hermes JSON-RPC 2.0 protocol by doing a full conversation turn."""

import asyncio
import json
import os

import websockets

from shared.config.loader import _load_dotenv

_load_dotenv()
TOKEN = os.environ.get("HERMES_AUTH_TOKEN", "")
WS_URL = f"ws://127.0.0.1:9119/api/ws?token={TOKEN}"


async def recv_msg(ws):
    raw = await ws.recv()
    return json.loads(raw)


async def send_msg(ws, method, params=None, req_id=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if req_id is not None:
        msg["id"] = req_id
    print(f">>> {json.dumps(msg, ensure_ascii=False)[:200]}")
    await ws.send(json.dumps(msg, ensure_ascii=False))


async def main():
    async with websockets.connect(WS_URL, ping_interval=30, ping_timeout=10) as ws:
        # 1. Receive gateway.ready
        msg = await recv_msg(ws)
        print(f"<<< method={msg.get('method')} type={msg.get('params',{}).get('type')}")

        # 2. session.create
        await send_msg(ws, "session.create", {"close_on_disconnect": True, "source": "tool"}, req_id=1)
        msg = await recv_msg(ws)
        print(f"<<< SESSION RESP: {json.dumps(msg, ensure_ascii=False)[:300]}")

        # Check if more events come
        try:
            while True:
                msg = await asyncio.wait_for(recv_msg(ws), timeout=0.5)
                print(f"<<< EXTRA: {json.dumps(msg, ensure_ascii=False)[:300]}")
        except TimeoutError:
            pass

        # 3. prompt.submit
        await send_msg(ws, "prompt.submit", {"session_id": msg.get("result", {}).get("session_id", ""), "text": "Hello! What is 2+2?"}, req_id=2)

        # 4. Consume all events
        response_text = None
        while True:
            msg = await recv_msg(ws)
            method = msg.get("method", "")
            params = msg.get("params", {})
            etype = params.get("type", "")
            print(f"<<< method={method} type={etype} id={msg.get('id')} keys={list(params.keys())}")

            if method == "event":
                if etype == "message.complete":
                    response_text = params.get("payload", {}).get("text", "")
                    print(f"<<< COMPLETE: {response_text[:200]}")
                    break
                elif etype in ("message.delta", "thinking.delta", "reasoning.delta"):
                    continue  # silent
                elif etype == "approval.request":
                    print(f"<<< APPROVAL REQUEST: {json.dumps(params, ensure_ascii=False)[:300]}")
                else:
                    print(f"<<< EVENT: {json.dumps(msg, ensure_ascii=False)[:300]}")
            elif "id" in msg:
                print(f"<<< RESPONSE: {json.dumps(msg, ensure_ascii=False)[:300]}")
            else:
                print(f"<<< UNKNOWN: {json.dumps(msg, ensure_ascii=False)[:300]}")

        print(f"\nFINAL RESPONSE: {response_text}")

        # 5. session.close
        await send_msg(ws, "session.close", {"session_id": msg.get("result", {}).get("session_id", "sess-1")}, req_id=3)
        try:
            msg = await asyncio.wait_for(recv_msg(ws), timeout=2.0)
            print(f"<<< CLOSE RESP: {json.dumps(msg, ensure_ascii=False)[:200]}")
        except TimeoutError:
            print("<<< no close response (timeout)")


asyncio.run(main())
