"""Manual contract probe: session.steer status + interrupt transcript sentinel.

Requires a real Hermes dashboard at 127.0.0.1:9119 and HERMES_AUTH_TOKEN in the
repo-root .env. Not collected by pytest (tests/manual).

Usage:
    cd services
    python tests/manual/probe_hermes_job_contract.py

Evidence log: /tmp/hermes_probe_steer_contract.log
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import websockets

from shared.config.loader import _load_dotenv

_load_dotenv()
TOKEN = os.environ.get("HERMES_AUTH_TOKEN", "")
WS_URL = "ws://127.0.0.1:9119/api/ws"


class Client:
    def __init__(self) -> None:
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.events: list[dict] = []
        self._n = 0

    async def connect(self) -> None:
        self.ws = await websockets.connect(f"{WS_URL}?token={TOKEN}")
        await self.ws.recv()  # gateway.ready
        self.reader = asyncio.create_task(self._read())

    async def close(self) -> None:
        try:
            if self.ws is not None:
                await self.ws.close()
        finally:
            self.reader.cancel()

    async def rpc(self, method: str, params: dict, timeout: float = 30) -> dict:
        self._n += 1
        rid = f"probe-{self._n}"
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self.ws.send(json.dumps({
            "jsonrpc": "2.0", "id": rid, "method": method, "params": params,
        }))
        msg = await asyncio.wait_for(fut, timeout)
        if "error" in msg:
            raise RuntimeError(f"{method} failed: {msg['error']}")
        return msg.get("result") or {}

    async def _read(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.pending:
                self.pending.pop(msg["id"]).set_result(msg)
                continue
            if msg.get("method") != "event":
                continue
            params = msg.get("params") or {}
            if params.get("type") in ("message.delta", "thinking.delta", "reasoning.delta"):
                continue
            sid = params.get("session_id") or ""
            self.events.append({
                "type": params.get("type"),
                "session_id": sid,
                "payload": params.get("payload") or {},
            })
            if params.get("type") == "approval.request":
                command = str((params.get("payload") or {}).get("command") or "")
                await self.rpc("approval.respond", {
                    "session_id": sid,
                    "choice": "once" if "sleep" in command else "deny",
                    "all": True,
                })
            elif params.get("type") == "clarify.request":
                await self.rpc("clarify.respond", {
                    "session_id": sid,
                    "answer": "probe: proceed",
                    "request_id": (params.get("payload") or {}).get("request_id", ""),
                })

    async def wait_event(self, etype: str, sid: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events:
                if event["type"] == etype and event["session_id"] == sid:
                    return event
            await asyncio.sleep(0.2)
        return None


async def _create(client: Client, title: str) -> str:
    result = await client.rpc("session.create", {
        "close_on_disconnect": False,
        "source": "livachat-probe",
        "title": title,
        "cwd": "/tmp",
    })
    return str(result.get("session_id") or "")


async def check_steer_contract(client: Client) -> None:
    sid = await _create(client, "probe-steer-contract")
    try:
        await client.rpc("prompt.submit", {
            "session_id": sid,
            "text": (
                "Use the terminal tool to run exactly `sleep 15`. After the command "
                "finishes, reply with exactly STEER_BASE."
            ),
        })
        await asyncio.sleep(3)
        reply = await client.rpc("session.steer", {
            "session_id": sid,
            "text": "Override: reply with exactly STEER_INJECTED instead.",
        })
        assert reply.get("status") == "queued", f"unexpected steer reply: {reply}"
        event = await client.wait_event("message.complete", sid, timeout=40)
        assert event is not None, "no message.complete after steer"
        text = str((event["payload"] or {}).get("text") or "")
        assert "STEER_INJECTED" in text, f"steer not applied: {text!r}"
        print(f"PASS steer: status=queued text={text!r}")
    finally:
        await client.rpc("session.close", {"session_id": sid})


async def check_interrupt_sentinel(client: Client) -> None:
    sid = await _create(client, "probe-interrupt-sentinel")
    try:
        await client.rpc("prompt.submit", {
            "session_id": sid,
            "text": (
                "Use the terminal tool to run exactly `sleep 30`. After it finishes, "
                "reply with exactly INTERRUPT_DONE."
            ),
        })
        await asyncio.sleep(3)
        await client.rpc("session.interrupt", {"session_id": sid})
        event = await client.wait_event("message.complete", sid, timeout=20)
        assert event is not None, "no message.complete after interrupt"
        assert (event["payload"] or {}).get("status") == "interrupted", event
        await asyncio.sleep(1.0)
        history = await client.rpc("session.history", {"session_id": sid})
        assistants = [
            str(m.get("text"))
            for m in (history.get("messages") or [])
            if m.get("role") == "assistant"
        ]
        assert assistants, "no assistant message in transcript"
        assert assistants[-1].strip() == "Operation interrupted.", (
            f"unexpected transcript sentinel: {assistants[-1]!r}"
        )
        print("PASS interrupt: transcript sentinel exact")
    finally:
        await client.rpc("session.close", {"session_id": sid})


async def main() -> None:
    if not TOKEN:
        raise SystemExit("HERMES_AUTH_TOKEN not set (repo-root .env)")
    client = Client()
    await client.connect()
    try:
        await check_steer_contract(client)
        await check_interrupt_sentinel(client)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
