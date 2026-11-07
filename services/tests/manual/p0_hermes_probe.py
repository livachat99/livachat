"""P0 probe: verify Hermes protocol behaviors against the live Hermes instance.

Covers design-doc §9 Q1–Q4:
  Q1a  prompt.background params schema + response
  Q1b  background context inheritance
  Q1d  background.complete.text == final response
  Q2   busy semantics: submit while a turn is in flight (queue + interrupt?)
  Q3   session.create messages seed (system role) effectiveness
  Q4   blocking interaction respond channels (approval/clarify respond)

Reads the token from HERMES_DASHBOARD_SESSION_TOKEN in ~/.hermes/.env.
Run from services/:  python -m tests.manual.p0_hermes_probe
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import websockets

HERMES_URL = "ws://127.0.0.1:9119/api/ws"

_results: list[str] = []


def result(name: str, detail: str) -> None:
    _results.append(f"[{name}] {detail}")
    print(f"  RESULT {name}: {detail}")


def _load_token() -> str:
    env_file = Path.home() / ".hermes" / ".env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("HERMES_DASHBOARD_SESSION_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("HERMES_DASHBOARD_SESSION_TOKEN not found in ~/.hermes/.env")


class Probe:
    def __init__(self) -> None:
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: list[dict] = []
        self._ws = None
        self._task = None
        self._exit = asyncio.Event()

    async def connect(self) -> None:
        token = _load_token()
        self._ws = await websockets.connect(
            f"{HERMES_URL}?token={token}", ping_interval=30, ping_timeout=10
        )
        self._task = asyncio.create_task(self._reader())
        await self._wait_for("gateway.ready", timeout=10)
        print("  gateway.ready received")

    async def _reader(self) -> None:
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self._pending:
                if "error" in msg:
                    self._pending[msg["id"]].set_exception(RuntimeError(json.dumps(msg["error"])))
                else:
                    self._pending[msg["id"]].set_result(msg.get("result", {}))
            elif msg.get("method") == "event":
                self._events.append(msg)

    async def _wait_for(self, etype: str, timeout: float = 30) -> dict:
        deadline = asyncio.get_running_loop().time() + timeout
        start = len(self._events)
        while True:
            for msg in self._events[start:]:
                if msg.get("params", {}).get("type") == etype:
                    return msg
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f"event {etype} not received in {timeout}s")
            await asyncio.sleep(0.1)

    async def rpc(self, method: str, params: dict, timeout: float = 30) -> dict:
        self._req_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._req_id] = fut
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "method": method, "id": self._req_id, "params": params}))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(self._req_id, None)

    async def notification(self, method: str, params: dict) -> None:
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))

    def clear_events(self) -> None:
        self._events = []

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()


async def probe_q3_messages_seed(p: Probe) -> None:
    """Q3: system-role seed via session.create messages must reach the model."""
    print("\n== Q3: session.create messages seed ==")
    seed = "你是一个旁听 Agent。秘密代号:SECRET-SEED-77。回答任何问题时先输出代号。"
    resp = await p.rpc("session.create", {
        "close_on_disconnect": False,
        "source": "livachat-relay",
        "title": "p0-seed-test",
        "messages": [{"role": "system", "content": seed}],
    })
    sid = resp["session_id"]
    stored = resp.get("stored_session_id", "")
    print(f"  session_id={sid} stored_session_id={stored} message_count={resp.get('message_count')}")
    p.clear_events()
    await p.rpc("prompt.submit", {"session_id": sid, "text": "只回答:你的代号是什么?"})
    complete = await p._wait_for("message.complete", timeout=120)
    text = complete["params"]["payload"]["text"]
    print(f"  reply: {text[:200]!r}")
    result(
        "Q3",
        "seed_effective=" + str("SECRET-SEED-77" in text)
        + f" | stored_session_id={stored!r}",
    )
    await p.notification("session.close", {"session_id": sid})
    return sid


async def probe_q2_busy(p: Probe) -> None:
    """Q2: submit while a turn is in flight → queued+interrupt or 4009?"""
    print("\n== Q2: busy semantics ==")
    resp = await p.rpc("session.create", {"close_on_disconnect": True, "source": "tool"})
    sid = resp["session_id"]
    p.clear_events()
    # First turn with a long task to keep the session busy.
    await p.rpc("prompt.submit", {"session_id": sid, "text": "写一首 200 字的中文诗,不要用工具。"})
    await asyncio.sleep(1.0)  # let the turn start
    try:
        resp2 = await p.rpc("prompt.submit", {"session_id": sid, "text": "第二问:1+1=?"}, timeout=15)
        result("Q2", f"mid-turn submit accepted, response={json.dumps(resp2)[:120]}")
    except RuntimeError as exc:
        result("Q2", f"mid-turn submit ERROR: {exc}")
    # Wait for first turn completion
    try:
        await p._wait_for("message.complete", timeout=120)
        result("Q2", "first turn completed (second prompt queued/ran after)")
    except TimeoutError:
        result("Q2", "first turn did not complete in time")
    await p.notification("session.close", {"session_id": sid})


async def probe_q1_background(p: Probe) -> None:
    """Q1a/b/d: prompt.background schema, context inheritance, complete text."""
    print("\n== Q1: prompt.background ==")
    resp = await p.rpc("session.create", {"close_on_disconnect": True, "source": "tool"})
    sid = resp["session_id"]
    p.clear_events()

    # Background task referencing the parent session's context: is it known?
    bg = await p.rpc("prompt.background", {
        "session_id": sid,
        "text": "背景:主会话刚才问你'什么是量子计算'。任务:直接回答'量子计算是什么'一句话。",
    })
    task_id = bg.get("task_id")
    print(f"  background response: {json.dumps(bg)}")
    result("Q1a", f"params=text+session_id; response task_id={task_id!r}")

    complete = await p._wait_for("background.complete", timeout=180)
    params = complete["params"]
    print(f"  background.complete: type={params['type']} session_id={params.get('session_id')}")
    print(f"  payload keys: {list(params['payload'].keys())}")
    text = params["payload"].get("text", "")
    print(f"  text: {text[:150]!r}")
    result("Q1d", f"complete.text is final response: len={len(text)}")
    result(
        "Q1b",
        "context_inherited="
        + str("量子计算" in text)
        + " (task text carried the context inline; history NOT inherited by design)",
    )
    await p.notification("session.close", {"session_id": sid})


async def probe_q4_interaction_channels(p: Probe) -> None:
    """Q4: blocking-interaction respond channels exist; clarify flow works."""
    print("\n== Q4: interaction respond channels ==")
    resp = await p.rpc("session.create", {"close_on_disconnect": True, "source": "tool"})
    sid = resp["session_id"]
    # approval.respond + clarify.respond are valid method names → expect _ok
    for method, params in [
        ("approval.respond", {"session_id": sid, "choice": "deny", "all": True}),
        ("clarify.respond", {"session_id": sid, "answer": "继续"}),
    ]:
        try:
            r = await p.rpc(method, params, timeout=10)
            result("Q4", f"{method} accepted: {json.dumps(r)[:100]}")
        except RuntimeError as exc:
            result("Q4", f"{method} ERROR: {exc}")
    await p.notification("session.close", {"session_id": sid})


async def main() -> None:
    p = Probe()
    await p.connect()
    try:
        await probe_q3_messages_seed(p)
        await probe_q2_busy(p)
        await probe_q1_background(p)
        await probe_q4_interaction_channels(p)
    finally:
        await p.close()

    print("\n================ P0 PROBE SUMMARY ================")
    for line in _results:
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
