#!/usr/bin/env python3
"""Test: verify whether Qwen TTS server echoes event_id in responses."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import websockets

_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime"


async def main():
    api_key = os.environ.get("DASHSCOPE_API_KEY") 
    if not api_key:
        print("ERROR: no DASHSCOPE_API_KEY", file=sys.stderr)
        sys.exit(1)

    ws = await websockets.connect(URL, additional_headers={"Authorization": f"Bearer {api_key}"})

    # --- session.created ---
    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
    msg = json.loads(raw)
    print(f"RECV  {json.dumps(msg)}")

    # --- session.update ---
    update = {
        "event_id": "my_custom_event_id_update",
        "type": "session.update",
        "session": {"voice": "Cherry", "mode": "commit", "response_format": "pcm", "sample_rate": 24000},
    }
    print(f"SEND  {json.dumps(update)}")
    await ws.send(json.dumps(update))

    # --- session.updated ---
    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
    msg = json.loads(raw)
    print(f"RECV  {json.dumps(msg)}")

    # --- input_text_buffer.append ---
    append = {"event_id": "my_append_event_42", "type": "input_text_buffer.append", "text": "你好。"}
    print(f"SEND  {json.dumps(append)}")
    await ws.send(json.dumps(append))

    # --- input_text_buffer.commit ---
    commit = {"event_id": "my_commit_event_99", "type": "input_text_buffer.commit"}
    print(f"SEND  {json.dumps(commit)}")
    await ws.send(json.dumps(commit))

    # --- drain all responses ---
    print("\n--- drain ---")
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
        msg = json.loads(raw)
        print(f"RECV  {json.dumps(msg)}")
        if msg.get("type") == "response.done":
            break

    # --- session.finish ---
    finish = {"event_id": "my_finish_event_77", "type": "session.finish"}
    print(f"SEND  {json.dumps(finish)}")
    await ws.send(json.dumps(finish))

    await ws.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
