"""In-process mock OpenClaw Gateway WS server for tests.

Speaks the protocol surface used by OpenClawAdapter (docs/openclaw_websocket_api.md,
P0-verified shapes): connect.challenge → connect → hello-ok, sessions.create
(adopt semantics), sessions.messages.subscribe/unsubscribe, chat.send
(ack runId == idempotencyKey; scripted chat delta/final events; idempotent
resend returns in_flight/terminal status), sessions.abort, sessions.describe
(hasActiveRun/lastRunId/lastRunError), chat.history, and the approval/
question resolve methods.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection

# chat.send handler: (params) -> reply text for the run's final message
RunHandler = Callable[[dict[str, Any]], str]


class MockOpenClawServer:
    def __init__(self, *, agent_id: str = "main") -> None:
        self.agent_id = agent_id
        self.requests: list[dict[str, Any]] = []
        self.connect_params: dict[str, Any] | None = None
        # key → {sessionId, messages:[...], lastRunId, lastRunError, active_runs:set}
        self.sessions: dict[str, dict[str, Any]] = {}
        # runId → {sessionKey, status: running|done|error|aborted, text, handler}
        self.runs: dict[str, dict[str, Any]] = {}
        self.run_handler: RunHandler | None = None
        # 故障注入
        self.swallow_methods: set[str] = set()          # 吞请求（RPC 超时模拟）
        self.rpc_error_methods: dict[str, tuple[str, str]] = {}  # method → (code, message)
        self.create_error: tuple[str, str] | None = None
        self.subscribe_error: tuple[str, str] | None = None
        # connect 请求直接拒绝（如 ("NOT_PAIRED", "device identity required")）
        self.connect_error: tuple[str, str] | None = None
        self.resend_terminal_status = "in_flight"       # 幂等重发的返回状态
        # True: chat.send 只回 ack，不自动发终态事件（由测试显式
        # complete_run_async / fail_run_async 驱动 —— 超时/重连场景）
        self.defer_completion = False
        # 仅对匹配的 chat.send 延迟完成（如只延迟 livachat-job- 会话的 run）
        self.defer_when: Callable[[dict[str, Any]], bool] | None = None
        self._conns: list[ServerConnection] = []
        self._conn_seq = 0
        self._ws_server: Server | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._ws_server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self._ws_server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None

    def connection_count(self) -> int:
        return len(self._conns)

    def methods(self) -> list[str]:
        return [r["method"] for r in self.requests]

    def requests_for(self, method: str) -> list[dict[str, Any]]:
        return [r["params"] for r in self.requests if r["method"] == method]

    # -- server side ----------------------------------------------------------

    async def _handle(self, ws: ServerConnection) -> None:
        self._conns.append(ws)
        await ws.send(json.dumps(self._event(
            "connect.challenge", {"nonce": f"nonce-{len(self._conns)}", "ts": int(time.time() * 1000)}
        )))
        try:
            async for raw in ws:
                msg = json.loads(raw)
                await self._dispatch(ws, msg)
        except (websockets.exceptions.ConnectionClosed, ConnectionResetError):
            pass
        except Exception:
            pass

    async def _dispatch(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if msg.get("type") != "req":
            return
        method = msg.get("method", "")
        params = msg.get("params") or {}
        rid = msg.get("id")
        if method == "connect":
            self.connect_params = params
            if self.connect_error is not None:
                code, message = self.connect_error
                await ws.send(json.dumps(self._err(rid, code, message)))
                return
            await ws.send(json.dumps(self._res(rid, {
                "type": "hello-ok",
                "protocol": 4,
                "server": {"version": "mock-openclaw"},
                "features": {"methods": [], "events": []},
                "snapshot": {},
                "auth": {"method": "token", "role": "operator",
                         "scopes": params.get("scopes") or []},
                "policy": {"tickIntervalMs": 30000, "maxPayload": 26214400},
            })))
            return
        self.requests.append({"method": method, "params": params})
        if method in self.swallow_methods:
            return
        if method in self.rpc_error_methods:
            code, message = self.rpc_error_methods[method]
            await ws.send(json.dumps(self._err(rid, code, message)))
            return
        try:
            payload, events = self._handle_rpc(method, params)
        except _MockRpcError as exc:
            await ws.send(json.dumps(self._err(rid, exc.code, exc.message)))
            return
        await ws.send(json.dumps(self._res(rid, payload)))
        for event in events:
            await ws.send(json.dumps(event))

    def _handle_rpc(
        self, method: str, params: dict[str, Any]
    ) -> tuple[Any, list[dict[str, Any]]]:
        if method == "sessions.create":
            if self.create_error is not None:
                raise _MockRpcError(*self.create_error)
            key = str(params.get("key") or "")
            canonical = f"agent:{self.agent_id}:{key}"
            existing = self.sessions.get(key)
            if existing is not None:
                return {"ok": True, "key": canonical, **existing, "adopted": True}, []
            session = {
                "sessionId": f"sid-{len(self.sessions) + 1}",
                "messages": [],
                "lastRunId": "",
                "lastRunError": "",
                "active_runs": set(),
                "create_params": params,
            }
            self.sessions[key] = session
            return {"ok": True, "key": canonical, "sessionId": session["sessionId"],
                    "runStarted": False}, []

        if method == "sessions.messages.subscribe":
            if self.subscribe_error is not None:
                raise _MockRpcError(*self.subscribe_error)
            key = _strip_agent(params.get("key", ""), self.agent_id)
            return {"subscribed": True, "key": f"agent:{self.agent_id}:{key}",
                    "agentId": self.agent_id}, []

        if method == "sessions.messages.unsubscribe":
            return {}, []

        if method == "chat.send":
            run_id = str(params.get("idempotencyKey") or "")
            existing = self.runs.get(run_id)
            if existing is not None:
                # 幂等重发：不产生重复 run（P0-Q6 实测语义）
                if existing["status"] == "running":
                    return {"runId": run_id, "status": "in_flight"}, []
                return {"runId": run_id, "status": self.resend_terminal_status}, []
            session_key = _strip_agent(params.get("sessionKey", ""), self.agent_id)
            session = self.sessions.get(session_key)
            if session is None:
                session = {
                    "sessionId": f"sid-implicit-{len(self.sessions) + 1}", "messages": [],
                    "lastRunId": "", "lastRunError": "", "active_runs": set(),
                    "create_params": {},
                }
                self.sessions[session_key] = session
            text = "mock reply"
            if self.run_handler is not None:
                text = self.run_handler(params)
            self.runs[run_id] = {
                "sessionKey": session_key, "status": "running", "text": text,
                "task": params.get("message", ""),
            }
            session["active_runs"].add(run_id)
            session["messages"].append({
                "role": "user",
                "content": params.get("message", ""),
                "timestamp": int(time.time() * 1000),
            })
            defer = self.defer_completion or (
                self.defer_when is not None and self.defer_when(params)
            )
            if defer:
                return {"runId": run_id, "status": "started"}, []
            events = [
                self._chat_event(run_id, session_key, "delta", delta_text=text),
                self._chat_event(run_id, session_key, "final",
                                 message={"role": "assistant",
                                          "content": [{"type": "text", "text": text}],
                                          "timestamp": int(time.time() * 1000)},
                                 stop_reason="stop"),
            ]
            self._complete_run(session, run_id, "done", text)
            return {"runId": run_id, "status": "started"}, events

        if method == "sessions.abort":
            run_id = str(params.get("runId") or "")
            run = self.runs.get(run_id)
            if run is None or run["status"] != "running":
                return {"ok": True, "abortedRunId": None, "status": "no-active-run"}, []
            session = self.sessions.get(run["sessionKey"], {})
            self._complete_run(session, run_id, "aborted", None)
            event = self._chat_event(run_id, run["sessionKey"], "aborted",
                                     stop_reason="rpc")
            return {"ok": True, "abortedRunId": run_id, "status": "aborted"}, [event]

        if method == "sessions.describe":
            key = _strip_agent(params.get("key", ""), self.agent_id)
            session = self.sessions.get(key)
            if session is None:
                return {"session": None}, []
            return {"session": {
                "key": f"agent:{self.agent_id}:{key}",
                "sessionId": session.get("sessionId", ""),
                "hasActiveRun": bool(session.get("active_runs")),
                "activeRunIds": sorted(session.get("active_runs") or []),
                "lastRunId": session.get("lastRunId", ""),
                "lastRunError": session.get("lastRunError", ""),
                "status": "running" if session.get("active_runs") else "done",
                "spawnDepth": (session.get("create_params") or {}).get("spawnDepth"),
                "parentSessionKey": (session.get("create_params") or {}).get("parentSessionKey"),
            }}, []

        if method == "chat.history":
            key = _strip_agent(params.get("sessionKey", ""), self.agent_id)
            session = self.sessions.get(key)
            if session is None:
                return {"sessionKey": params.get("sessionKey"), "messages": []}, []
            limit = int(params.get("limit") or 20)
            return {"sessionKey": params.get("sessionKey"),
                    "messages": session.get("messages", [])[-limit:]}, []

        if method in ("exec.approval.resolve", "question.resolve"):
            return {"ok": True}, []

        return {}, []

    def _complete_run(self, session: dict[str, Any], run_id: str,
                      status: str, text: str | None) -> None:
        run = self.runs.get(run_id)
        if run is not None:
            run["status"] = status
        if session is not None:
            session.setdefault("active_runs", set()).discard(run_id)
            session["lastRunId"] = run_id
            if status == "done":
                session.setdefault("messages", []).append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": text or ""}],
                    "timestamp": int(time.time() * 1000),
                })
            elif status == "error":
                session["lastRunError"] = "mock run error"

    # -- frames ---------------------------------------------------------------

    def _next_seq(self) -> int:
        self._conn_seq += 1
        return self._conn_seq

    def _res(self, rid: Any, payload: Any) -> dict[str, Any]:
        return {"type": "res", "id": rid, "ok": True, "payload": payload}

    def _err(self, rid: Any, code: str, message: str) -> dict[str, Any]:
        return {"type": "res", "id": rid, "ok": False,
                "error": {"code": code, "message": message}}

    def _event(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"type": "event", "event": event, "payload": payload, "seq": self._next_seq()}

    def _chat_event(self, run_id: str, session_key: str, state: str, *,
                    delta_text: str | None = None,
                    message: dict[str, Any] | None = None,
                    replace: bool | None = None,
                    stop_reason: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runId": run_id,
            "sessionKey": f"agent:{self.agent_id}:{session_key}",
            "agentId": self.agent_id,
            "seq": 1,
            "state": state,
        }
        if delta_text is not None:
            payload["deltaText"] = delta_text
        if message is not None:
            payload["message"] = message
        if replace is not None:
            payload["replace"] = replace
        if stop_reason is not None:
            payload["stopReason"] = stop_reason
        return self._event("chat", payload)

    # -- test helpers ---------------------------------------------------------

    def emit_event(self, event: str, payload: dict[str, Any]) -> None:
        """Broadcast an event to the most recent connection (approval/question)."""
        if not self._conns:
            raise RuntimeError("no connection to emit to")
        ws = self._conns[-1]
        asyncio.create_task(ws.send(json.dumps(self._event(event, payload))))

    def emit_text(self, text: str) -> None:
        """发送任意原始文本帧（畸形帧鲁棒性测试）。"""
        if not self._conns:
            raise RuntimeError("no connection to emit to")
        ws = self._conns[-1]
        asyncio.create_task(ws.send(text))

    def emit_raw(self, frame: dict[str, Any]) -> None:
        """Send an arbitrary frame to the most recent connection (seq-gap tests)."""
        if not self._conns:
            raise RuntimeError("no connection to emit to")
        ws = self._conns[-1]
        asyncio.create_task(ws.send(json.dumps(frame)))

    async def complete_run_async(self, run_id: str, text: str) -> None:
        """Finish a run later (slow-turn tests): emit final + update state."""
        run = self.runs.get(run_id)
        if run is None:
            raise RuntimeError(f"unknown run {run_id}")
        session = self.sessions.get(run["sessionKey"], {})
        self._complete_run(session, run_id, "done", text)
        ws = self._conns[-1]
        await ws.send(json.dumps(self._chat_event(
            run_id, run["sessionKey"], "final",
            message={"role": "assistant",
                     "content": [{"type": "text", "text": text}],
                     "timestamp": int(time.time() * 1000)},
            stop_reason="stop",
        )))

    async def fail_run_async(self, run_id: str, text: str = "boom") -> None:
        run = self.runs.get(run_id)
        if run is None:
            raise RuntimeError(f"unknown run {run_id}")
        session = self.sessions.get(run["sessionKey"], {})
        self._complete_run(session, run_id, "error", None)
        ws = self._conns[-1]
        await ws.send(json.dumps(self._chat_event(
            run_id, run["sessionKey"], "error",
            message={"role": "assistant",
                     "content": [{"type": "text", "text": text}],
                     "timestamp": int(time.time() * 1000)},
        )))

    async def drop_connections(self) -> None:
        """Close all client connections (reconnect-path tests)."""
        for ws in list(self._conns):
            try:
                await ws.close()
            except Exception:
                pass


class _MockRpcError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _strip_agent(key: str, agent_id: str) -> str:
    prefix = f"agent:{agent_id}:"
    return key[len(prefix):] if key.startswith(prefix) else key
