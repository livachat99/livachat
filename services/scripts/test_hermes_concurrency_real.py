#!/usr/bin/env python3
"""Real Hermes gateway concurrency probe (fd_agent_relay_service_v2.md Q1).

Verifies that a single API token can hold N concurrent /api/ws connections
(one per device — the v2 architecture assumes per-device connections share
one token, D1). If the gateway caps per-token concurrent connections, the
startup recovery and multi-device steady state will fail in production.

Each connection follows the relay's real handshake: websocket connect →
gateway.ready → session.create(close_on_disconnect=False). Connections are
held for --hold seconds to confirm they stay alive, then closed cleanly.

Usage::

    # hermes base_url/api_key read from configs/dev/server.yaml, 10 conns
    python scripts/test_hermes_concurrency_real.py --connections 10

    # explicit endpoint (no config file involved)
    python scripts/test_hermes_concurrency_real.py \
        --base-url http://localhost:9119 --api-key XXX --connections 20

Exit code 0 = all connections accepted; 1 = any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
_PROJECT_ROOT = _HERE.parent.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

import websockets
import yaml
from websockets.asyncio.client import ClientConnection


def _load_config_yaml(path):
    """同步读取 server.yaml（测试脚本一次性配置加载）。"""
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class ProbeResult:
    idx: int
    ok: bool = False
    phase: str = ""            # connect | ready | session.create | hold
    error: str = ""
    code: int | None = None    # jsonrpc error code, e.g. 4090
    session_id: str = ""
    connect_ms: float = 0.0
    create_ms: float = 0.0
    ws: ClientConnection | None = field(default=None, repr=False)


async def _rpc(ws: ClientConnection, req_id: int, method: str, params: dict, timeout_s: float) -> dict:
    await ws.send(json.dumps({"jsonrpc": "2.0", "method": method, "id": req_id, "params": params}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_s))
        if msg.get("id") != req_id:
            continue
        if "error" in msg:
            err = msg["error"]
            raise RpcError(err.get("code"), err.get("message", "unknown"))
        return msg.get("result", {})


class RpcError(Exception):
    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code


def _ws_url(base_url: str, api_key: str) -> str:
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[8:]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[7:]
    else:
        ws_base = base
    token = api_key
    if token:
        return f"{ws_base}/api/ws?token={token}"
    return f"{ws_base}/api/ws"


async def probe_one(
    idx: int,
    url: str,
    *,
    timeout_s: float,
    create_session: bool,
    hold_s: float,
) -> ProbeResult:
    res = ProbeResult(idx=idx)
    t0 = time.monotonic()
    try:
        ws = await asyncio.wait_for(
            websockets.connect(url, ping_interval=30, ping_timeout=10, close_timeout=5),
            timeout=timeout_s,
        )
    except TimeoutError:
        res.phase = "connect"
        res.error = f"timed out after {timeout_s}s"
        return res
    except Exception as exc:
        res.phase = "connect"
        res.error = str(exc)[:200]
        return res
    res.connect_ms = (time.monotonic() - t0) * 1000
    res.ws = ws

    try:
        # handshake: wait for gateway.ready (real Hermes wraps pushes in method=event)
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_s))
            if msg.get("method") == "event" and (msg.get("params") or {}).get("type") == "gateway.ready":
                break
    except TimeoutError:
        res.phase = "ready"
        res.error = "no gateway.ready"
        await ws.close()
        return res
    except Exception as exc:
        res.phase = "ready"
        res.error = str(exc)[:200]
        await ws.close()
        return res

    if create_session:
        t1 = time.monotonic()
        try:
            result = await _rpc(
                ws, idx + 1, "session.create",
                {
                    "close_on_disconnect": False,
                    "source": "livachat-concurrency-probe",
                    "title": f"probe-conn-{idx}",
                    "messages": [{"role": "system", "content": "concurrency probe"}],
                },
                timeout_s,
            )
            res.session_id = result.get("session_id", "")
            res.create_ms = (time.monotonic() - t1) * 1000
        except RpcError as exc:
            res.phase = "session.create"
            res.error = exc.args[0]
            res.code = exc.code
            await ws.close()
            return res
        except Exception as exc:
            res.phase = "session.create"
            res.error = str(exc)[:200]
            await ws.close()
            return res

    res.ok = True
    res.phase = "hold"
    try:
        await asyncio.wait_for(ws.wait_closed(), timeout=hold_s)
    except TimeoutError:
        pass  # connection stayed up for the hold window — good
    except Exception as exc:
        res.ok = False
        res.error = f"connection dropped during hold: {str(exc)[:200]}"
    return res


async def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes per-token concurrent WS probe (Q1)")
    parser.add_argument("--env", default="dev", help="config env dir under configs/ (default: dev)")
    parser.add_argument("--base-url", default="", help="override hermes base_url (http(s)://host:port)")
    parser.add_argument("--api-key", default="", help="override hermes api_key (default: from server.yaml)")
    parser.add_argument("--connections", type=int, default=10, help="concurrent connections (default: 10)")
    parser.add_argument("--no-sessions", action="store_true", help="skip session.create (connect+ready only)")
    parser.add_argument("--hold", type=float, default=3.0, help="seconds to hold connections after create (default: 3)")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-step timeout seconds (default: 30)")
    args = parser.parse_args()

    base_url, api_key = args.base_url, args.api_key
    if not base_url:
        explicit = os.environ.get("LIVA_CHAT_CONFIG")
        path = Path(explicit) if explicit else (_PROJECT_ROOT / "configs" / args.env / "server.yaml")
        if not path.exists():
            print(f"ERROR: config file not found: {path}", file=sys.stderr)
            return 2
        raw = _load_config_yaml(path)
        hermes = (raw.get("agent_relay") or {}).get("adapters", {}).get("hermes", {})
        base_url = hermes.get("base_url", "")
        api_key = args.api_key or hermes.get("api_key", "")
        source = str(path)
    else:
        source = "command line"
    if not base_url:
        print("ERROR: no hermes base_url (set --base-url or --env with a config)", file=sys.stderr)
        return 2

    url = _ws_url(base_url, api_key)
    print(f"endpoint:     {base_url}")
    print(f"source:       {source}")
    print(f"api_key:      {'<set>' if api_key else '<EMPTY>'}")
    print(f"connections:  {args.connections} (concurrent, one WS per device)")
    print(f"handshake:    ws connect -> gateway.ready -> session.create"
          f"{'  [skipped]' if args.no_sessions else ''}")
    print(f"hold:         {args.hold}s after create")
    print("-" * 72)

    t0 = time.monotonic()
    results = await asyncio.gather(
        *(
            probe_one(
                i,
                url,
                timeout_s=args.timeout,
                create_session=not args.no_sessions,
                hold_s=args.hold,
            )
            for i in range(args.connections)
        )
    )
    total_ms = (time.monotonic() - t0) * 1000

    ok = sum(1 for r in results if r.ok)
    print(f"\n{'#':>3}  {'result':<8} {'phase':<16} {'connect ms':>10} {'create ms':>9}  detail")
    print("-" * 72)
    for r in sorted(results, key=lambda r: r.idx):
        detail = r.error or r.session_id or "-"
        print(
            f"{r.idx:>3}  {'OK' if r.ok else 'FAIL':<8} {r.phase:<16} "
            f"{r.connect_ms:>10.1f} {r.create_ms:>9.1f}  {detail}"
        )
    print("-" * 72)
    print(f"accepted:     {ok}/{args.connections}  (total wall {total_ms:.0f} ms)")

    if ok < args.connections:
        codes = sorted({r.code for r in results if not r.ok and r.code is not None})
        print(f"\nFAILED: {args.connections - ok} connections rejected"
              + (f"; jsonrpc error codes: {codes}" if codes else ""))
        print("This is evidence for the Q1 BLOCKER: the gateway caps per-token"
              " concurrent connections below the relay v2 target scale.")
        return 1

    print("\nALL CONNECTIONS ACCEPTED — single token held", args.connections,
          "concurrent sessions for", args.hold, "s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
