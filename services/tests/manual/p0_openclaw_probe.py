"""P0 probe: verify OpenClaw protocol behaviors for the B-plan adapter design.

Covers design-doc §9 Q1–Q8 against the live local gateway (openclaw 2026.10.3,
ws://127.0.0.1:18789). Probe sessions are named `livachat-p0-probe-*` and are
left behind for inspection (created deliberately, cheap).

  Q1 sessions.create with parentSessionKey/spawnDepth; result fields; adopt
  Q2 chat.send ack (runId == idempotencyKey) + chat event stream + final
     message snapshot shape
  Q3 transcript shapes: chat.history / sessions.describe before/after terminal
     runs (completed + aborted)
  Q4 busy semantics: second chat.send (queueMode=followup) while a run is in
     flight; abort semantics
  Q5 gateway restart interruption — NOT testable safely (would disrupt the
     user's gateway service); design keeps the conservative reconcile path
  Q6 idempotent resend of the same chat.send idempotencyKey
  Q7 approvals path — local main agent runs defaultPermissionMode=full, no
     approval expected; resolve params verified from schema in the design doc
  Q8 archive semantics — code check in the design doc: archiveAfterMinutes
     only applies to the native subagent registry; plain relay-created
     sessions are unaffected

Reads gateway.auth.token from ~/.openclaw/openclaw.json.
Run from services/:  python -m tests.manual.p0_openclaw_probe
Raw frames (token masked) → /tmp/openclaw_p0_frames.jsonl
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
import uuid

import websockets
from websockets.asyncio.client import ClientConnection

GATEWAY_URL = "ws://127.0.0.1:18789"
FRAMES_OUT = pathlib.Path("/tmp/openclaw_p0_frames.jsonl")

_results: list[str] = []


def result(name: str, detail: str) -> None:
    _results.append(f"[{name}] {detail}")
    print(f"  RESULT {name}: {detail}")


def _load_token() -> str:
    cfg = json.loads(
        (pathlib.Path.home() / ".openclaw" / "openclaw.json").read_text()
    )
    return cfg["gateway"]["auth"]["token"]


class Probe:
    def __init__(self, token: str) -> None:
        self._token = token
        self._req_id = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._events: list[dict] = []
        self._run_events: dict[str, list[dict]] = {}
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task | None = None
        self._seq_last: int | None = None

    # -- plumbing -------------------------------------------------------------

    def _log(self, direction: str, frame: dict) -> None:
        masked = json.loads(json.dumps(frame).replace(self._token, "<TOKEN>"))
        with FRAMES_OUT.open("a") as f:
            f.write(json.dumps({"dir": direction, "ts": time.time(), "frame": masked}) + "\n")

    async def connect(self) -> dict:
        self._ws = await websockets.connect(
            GATEWAY_URL, ping_interval=30, ping_timeout=10, close_timeout=5,
            max_size=2**24,
        )
        self._task = asyncio.create_task(self._reader())
        challenge = await self._wait_event("connect.challenge", timeout=10)
        nonce = challenge.get("payload", {}).get("nonce", "")
        print(f"  challenge nonce={nonce[:8]}…")
        hello = await self.rpc("connect", {
            "minProtocol": 4, "maxProtocol": 4,
            "client": {"id": "gateway-client", "version": "0.1.0-p0probe",
                       "platform": "darwin", "mode": "backend"},
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
            "caps": [],
            "auth": {"token": self._token},
            "locale": "zh-CN", "userAgent": "livachat-p0-openclaw-probe/0.1",
        }, timeout=15)
        assert hello.get("ok"), f"connect failed: {hello}"
        return hello["payload"]

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws is not None:
            await self._ws.close()

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                self._log("in", msg)
                if msg.get("type") == "event":
                    seq = msg.get("seq")
                    if isinstance(seq, int):
                        if self._seq_last is not None and seq > self._seq_last + 1:
                            print(f"  !! seq gap {self._seq_last} -> {seq}")
                        self._seq_last = seq
                    self._events.append(msg)
                    run_id = (msg.get("payload") or {}).get("runId", "")
                    if run_id:
                        self._run_events.setdefault(run_id, []).append(msg)
                elif msg.get("type") == "res":
                    fut = self._pending.pop(str(msg.get("id")), None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
            return
        except Exception as exc:  # pragma: no cover
            print(f"  reader error: {exc}")

    async def rpc(self, method: str, params: dict | None, *, timeout: float = 30) -> dict:
        assert self._ws is not None
        self._req_id += 1
        rid = f"p0-{self._req_id}"
        frame: dict = {"type": "req", "id": rid, "method": method}
        if params is not None:
            frame["params"] = params
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(json.dumps(frame))
        self._log("out", frame)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    async def _wait_event(self, event: str, *, timeout: float = 10) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in self._events:
                if ev.get("event") == event:
                    return ev
            await asyncio.sleep(0.05)
        raise TimeoutError(f"event {event} not received")

    async def wait_run_terminal(self, run_id: str, *, timeout: float = 120) -> dict | None:
        """Wait for the chat final/error/aborted event of a run; returns it."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in self._run_events.get(run_id, []):
                if ev.get("event") == "chat" and (ev.get("payload") or {}).get("state") in (
                    "final", "error", "aborted",
                ):
                    return ev
            await asyncio.sleep(0.1)
        return None


def _run_states(probe: Probe, run_id: str) -> list[str]:
    return [
        (ev.get("payload") or {}).get("state", "?")
        for ev in probe._run_events.get(run_id, [])
    ]


def _msg_summary(m: dict) -> dict:
    """Defensive chat.history row summary (content blocks may be dicts or str)."""
    content = m.get("content")
    blocks: list[str] = []
    tail = ""
    if isinstance(content, list):
        for b in content:
            blocks.append(b.get("type", "?") if isinstance(b, dict) else type(b).__name__)
        if content:
            b = content[-1]
            tail = (str(b.get("text", "")) if isinstance(b, dict) else str(b))[:60]
    elif content is not None:
        blocks = [type(content).__name__]
        tail = str(content)[:60]
    return {"role": m.get("role"), "blocks": blocks, "tail": tail}


async def main() -> None:
    FRAMES_OUT.unlink(missing_ok=True)
    token = _load_token()
    p = Probe(token)
    ts = uuid.uuid4().hex[:8]

    print("== connect ==")
    hello = await p.connect()
    policy = hello.get("policy", {})
    result("Q/hello", f"protocol={hello.get('protocol')} tickIntervalMs={policy.get('tickIntervalMs')} "
                      f"maxPayload={policy.get('maxPayload')} "
                      f"server={hello.get('server', {}).get('version')}")
    result("Q/hello-scopes", json.dumps(hello.get("auth")))

    probe_key = f"livachat-p0-probe-{ts}"

    # -- Q1: sessions.create + lineage fields + adopt -------------------------
    # NOTE (first run): sessions.create WITH idempotencyKey is rejected on the
    # device-less backend path ("idempotent session creation requires an
    # authenticated principal or device identity") — retry WITHOUT it.
    print("\n== Q1 sessions.create (lineage, no idempotencyKey) ==")
    r = await p.rpc("sessions.create", {
        "key": probe_key,
        "parentSessionKey": "agent:main:main",
        "spawnDepth": 1,
    })
    result("Q1a-lineage-create", json.dumps(r, ensure_ascii=False)[:900])
    canonical = probe_key
    if r.get("ok"):
        payload = r.get("payload") or {}
        canonical = payload.get("sessionKey") or payload.get("key") or probe_key
    else:
        print("  lineage create rejected; falling back to plain create")
        r = await p.rpc("sessions.create", {"key": probe_key})
        result("Q1a-plain-create", json.dumps(r, ensure_ascii=False)[:600])
        payload = r.get("payload") or {}
        canonical = payload.get("sessionKey") or payload.get("key") or probe_key
    result("Q1a-result-fields", f"canonical={canonical} fields={sorted((r.get('payload') or {}).keys())}")

    d0 = await p.rpc("sessions.describe", {"key": canonical})
    row0 = (d0.get("payload") or {}).get("session") or {}
    result("Q1a-describe-lineage", f"spawnDepth={row0.get('spawnDepth')} "
                                   f"spawnedBy={row0.get('spawnedBy')} "
                                   f"parentSessionKey={row0.get('parentSessionKey')} "
                                   f"createdVia={row0.get('createdVia')}")

    r2 = await p.rpc("sessions.create", {"key": probe_key})
    result("Q1b-adopt-same-key", json.dumps(r2, ensure_ascii=False)[:600])

    # -- subscribe -------------------------------------------------------------
    print("\n== subscribe ==")
    sub = await p.rpc("sessions.messages.subscribe", {"key": canonical})
    result("subscribe", json.dumps(sub, ensure_ascii=False)[:300])

    # -- Q2: chat.send + event stream + final message shape -------------------
    print("\n== Q2 chat.send + events ==")
    run1 = f"p0-run-noop-{ts}"
    ack = await p.rpc("chat.send", {
        "sessionKey": canonical,
        "message": "契约测试。请仅输出一个 JSON 对象,不要任何其他文字:{\"action\":\"noop\"}",
        "idempotencyKey": run1,
    })
    result("Q2-ack", json.dumps(ack, ensure_ascii=False)[:500])
    result("Q2-ack-runId==key", str((ack.get("payload") or {}).get("runId") == run1))
    fin = await p.wait_run_terminal(run1, timeout=180)
    if fin is None:
        result("Q2-final", "TIMEOUT waiting terminal event")
    else:
        payload = fin.get("payload") or {}
        result("Q2-event-states", str(_run_states(p, run1)))
        result("Q2-final-keys", str(sorted(payload.keys())))
        msg = payload.get("message")
        result("Q2-final-message-shape",
               json.dumps(msg, ensure_ascii=False)[:1200] if msg is not None else "None")
        result("Q2-delta-sample", json.dumps(
            next((ev["payload"] for ev in p._run_events.get(run1, [])
                  if (ev.get("payload") or {}).get("state") == "delta"), {}),
            ensure_ascii=False)[:400])

    # -- Q6: idempotent resend -------------------------------------------------
    print("\n== Q6 resend same idempotencyKey ==")
    resend = await p.rpc("chat.send", {
        "sessionKey": canonical,
        "message": "契约测试。请仅输出一个 JSON 对象,不要任何其他文字:{\"action\":\"noop\"}",
        "idempotencyKey": run1,
    }, timeout=20)
    result("Q6-resend-immediate", json.dumps(resend, ensure_ascii=False)[:700])

    print("  (settle 6s, then resend again + describe)")
    await asyncio.sleep(6)
    resend2 = await p.rpc("chat.send", {
        "sessionKey": canonical,
        "message": "契约测试。请仅输出一个 JSON 对象,不要任何其他文字:{\"action\":\"noop\"}",
        "idempotencyKey": run1,
    }, timeout=20)
    result("Q6-resend-after-settle", json.dumps(resend2, ensure_ascii=False)[:700])

    # -- Q3 (part 1): describe + history after completed run -------------------
    print("\n== Q3 describe/history after completed run ==")
    d = await p.rpc("sessions.describe", {"key": canonical})
    row = (d.get("payload") or {}).get("session") or {}
    result("Q3-describe-active", f"hasActiveRun={row.get('hasActiveRun')} "
                                 f"activeRunIds={row.get('activeRunIds')} "
                                 f"status={row.get('status')} lastRunId={row.get('lastRunId')} "
                                 f"endedAt={row.get('endedAt')} lastRunError={row.get('lastRunError')}")
    h = await p.rpc("chat.history", {"sessionKey": canonical, "limit": 10})
    msgs = ((h.get("payload") or {}).get("messages")) or []
    result("Q3-history-rows", str([_msg_summary(m) for m in msgs])[:900])

    # -- Q4: busy semantics (followup while running) + abort -------------------
    print("\n== Q4 busy + abort ==")
    run2 = f"p0-run-count-a-{ts}"
    run3 = f"p0-run-count-b-{ts}"
    a2 = await p.rpc("chat.send", {
        "sessionKey": canonical,
        "message": "从 1 数到 60,每行一个数字,不要任何其他内容。",
        "idempotencyKey": run2,
    }, timeout=20)
    a3 = await p.rpc("chat.send", {
        "sessionKey": canonical,
        "message": "从 1 数到 60,每行一个数字,不要任何其他内容。",
        "idempotencyKey": run3,
        "queueMode": "followup",
    }, timeout=20)
    result("Q4-ack-first", json.dumps(a2.get("payload"), ensure_ascii=False)[:300])
    result("Q4-ack-second(followup)", json.dumps(a3.get("payload"), ensure_ascii=False)[:300])

    ab3 = await p.rpc("sessions.abort", {"runId": run3})
    result("Q4-abort-queued", json.dumps(ab3, ensure_ascii=False)[:400])
    ab2 = await p.rpc("sessions.abort", {"runId": run2})
    result("Q4-abort-running", json.dumps(ab2, ensure_ascii=False)[:400])
    await asyncio.sleep(4)

    fin2 = await p.wait_run_terminal(run2, timeout=60)
    result("Q4-run2-terminal", json.dumps(
        (fin2 or {}).get("payload", {}), ensure_ascii=False)[:500] if fin2 else "none")
    result("Q4-run2-states", str(_run_states(p, run2)))
    result("Q4-run3-states", str(_run_states(p, run3)))

    # -- Q3 (part 2): describe + history after aborted runs --------------------
    print("\n== Q3b describe/history after aborted run ==")
    d2 = await p.rpc("sessions.describe", {"key": canonical})
    row2 = (d2.get("payload") or {}).get("session") or {}
    result("Q3b-describe-active", f"hasActiveRun={row2.get('hasActiveRun')} "
                                  f"activeRunIds={row2.get('activeRunIds')}")
    h2 = await p.rpc("chat.history", {"sessionKey": canonical, "limit": 20})
    msgs2 = ((h2.get("payload") or {}).get("messages")) or []
    result("Q3b-history-rows", str([_msg_summary(m) for m in msgs2])[:1400])

    await p.close()

    print("\n================ SUMMARY ================")
    for line in _results:
        print(line)
    print(f"\nraw frames (token masked) → {FRAMES_OUT}")


if __name__ == "__main__":
    asyncio.run(main())
