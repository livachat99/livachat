"""In-process mock Hermes WS JSON-RPC server for tests.

Speaks the Hermes protocol surface used by HermesAdapter:
gateway.ready, session.create (records messages seed), prompt.submit
(programmable reply via submit_handler; terminal message.complete is
emitted unless held), session resume/active_list/history/interrupt/steer,
process list/kill, and the blocking-interaction events.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection

# submit_handler: (params) -> reply text, or a dict {text, status, hold}.
# hold=True keeps the turn running; tests emit the completion explicitly.
SubmitHandler = Callable[[dict[str, Any]], str | dict[str, Any] | None]


class _MockRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MockHermesServer:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.submit_handler: SubmitHandler | None = None
        # v3 job-session support.
        self.resume_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self.session_status: dict[str, str] = {}
        self.session_history: dict[str, list[dict[str, Any]]] = {}
        self.steer_requests: list[dict[str, Any]] = []
        self.steer_status: str = "queued"
        self.interrupt_requests: list[dict[str, Any]] = []
        self.interrupt_emit_complete: bool = True
        self.processes: dict[str, list[dict[str, Any]]] = {}
        self.killed_processes: list[str] = []
        # When set, session.resume replies with an error instead of succeeding.
        self.resume_error: dict[str, Any] | None = None
        # When set, session.resume returns the SAME session id as requested
        # (20s grace-window live reuse). Default: rebuild → new session id.
        self.resume_same_sid: bool = False
        # Delay (s) before prompt.submit replies with message.complete —
        # simulates a slow turn for straggler tests.
        self.submit_delay_s: float = 0.0
        # When set, prompt.submit is swallowed entirely (no response, no
        # events) — simulates an RPC timeout.
        self.submit_no_response: bool = False
        # Methods (by name) to swallow entirely — no response, no events
        # (RPC timeout simulation; §5.8 liveness probe tests).
        self.swallow_methods: set[str] = set()
        # Methods to answer with a JSON-RPC error: {method: (code, message)}
        # (§5.8: an error response still proves app-layer dispatch is alive).
        self.rpc_error_methods: dict[str, tuple[int, str]] = {}
        # Delay (s) before session.create replies — simulates a slow create.
        self.create_delay_s: float = 0.0
        # When set, session.create returns only session_id (no stored id).
        self.create_omit_stored: bool = False
        self._conns: list[ServerConnection] = []
        self._ws_server: websockets.asyncio.server.Server | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._ws_server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self._ws_server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None

    # -- server side ----------------------------------------------------------

    async def _handle(self, ws: ServerConnection) -> None:
        self._conns.append(ws)
        await ws.send(json.dumps(self._event("gateway.ready", {})))
        try:
            async for raw in ws:
                msg = json.loads(raw)
                await self._dispatch(ws, msg)
        except (websockets.exceptions.ConnectionClosed, ConnectionResetError):
            pass
        except Exception:
            pass

    async def _dispatch(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if "id" in msg:
            self.requests.append({"method": method, "params": params})
            if method == "session.create" and self.create_delay_s > 0:
                await asyncio.sleep(self.create_delay_s)
            try:
                result, events = self._handle_rpc(method, params)
            except _MockRpcError as exc:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": exc.code, "message": exc.message},
                }))
                return
            if method in self.swallow_methods or (
                method == "prompt.submit" and self.submit_no_response
            ):
                return  # swallow the request entirely (RPC timeout simulation)
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}))
            # Like real Hermes: the RPC response arrives first, streaming
            # events (message.complete) follow — possibly delayed.
            if method == "prompt.submit" and self.submit_delay_s > 0:
                await asyncio.sleep(self.submit_delay_s)
            for event in events:
                await ws.send(json.dumps(event))
        else:
            self.notifications.append(msg)

    def _handle_rpc(
        self, method: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if method in self.rpc_error_methods:
            code, message = self.rpc_error_methods[method]
            raise _MockRpcError(code, message)

        if method == "session.create":
            sid = f"sess-{len(self.sessions) + 1}"
            stored = (
                f"stored-{sid}"
                if params.get("close_on_disconnect") is False
                and not self.create_omit_stored
                else ""
            )
            self.sessions[sid] = {
                "params": params,
                "messages": params.get("messages", []),
                "stored_session_id": stored,
            }
            self.session_status[sid] = "idle"
            self.session_history.setdefault(sid, list(params.get("messages", [])))
            result: dict[str, Any] = {"session_id": sid}
            if stored:
                result["stored_session_id"] = stored
            return result, []

        if method == "prompt.submit":
            sid = params.get("session_id", "")
            raw = self.submit_handler(params) if self.submit_handler else "mock reply"
            hold = False
            status = "complete"
            if isinstance(raw, dict):
                text = str(raw.get("text") or "")
                status = str(raw.get("status") or "complete")
                hold = bool(raw.get("hold"))
            else:
                text = str(raw or "")
            if sid:
                self.session_status[sid] = "working"
                history = self.session_history.setdefault(sid, [])
                history.append({"role": "user", "text": params.get("text", "")})
            events: list[dict[str, Any]] = []
            if not hold:
                if sid:
                    history = self.session_history.setdefault(sid, [])
                    history.append({"role": "assistant", "text": text})
                    self.session_status[sid] = "idle"
                events.append(self._message_complete_event(sid, text, status))
            return {"status": "streaming"}, events

        if method == "session.resume":
            if self.resume_error is not None:
                raise _MockRpcError(self.resume_error.get("code", 4007),
                                    self.resume_error.get("message", "session not found"))
            if self.resume_handler is not None:
                result = dict(self.resume_handler(params))
                sid = str(result.get("session_id") or "")
                if sid:
                    self.sessions.setdefault(
                        sid, {"params": params, "messages": []}
                    )
                    if not result.get("stored_session_id"):
                        result["stored_session_id"] = (
                            self.sessions[sid].get("stored_session_id") or ""
                        )
                    if "messages" not in result:
                        result["messages"] = list(
                            self.session_history.get(sid, [])
                        )
                    self.session_status[sid] = str(
                        result.get("status")
                        or ("working" if result.get("running") else "idle")
                    )
                return result, []
            resume_id = params.get("session_id", "")
            if self.resume_same_sid:
                # 20s 宽限内 live 复用：返回与 stored id 关联的同一 hermes session id
                target = (
                    resume_id.removeprefix("stored-")
                )
                self.sessions.setdefault(
                    target, {"params": params, "messages": []}
                )
                status = self.session_status.get(target, "idle")
                return {
                    "session_id": target,
                    "stored_session_id": resume_id,
                    "messages": list(self.session_history.get(target, [])),
                    "running": status == "working",
                    "status": status,
                }, []
            # 宽限后 DB 重建：返回新 session id
            new_sid = f"sess-resumed-{len(self.sessions) + 1}"
            self.sessions[new_sid] = {
                "params": params,
                "resumed_from": resume_id,
                "messages": [],
                "stored_session_id": f"stored-{new_sid}",
            }
            self.session_status[new_sid] = "idle"
            return {
                "session_id": new_sid,
                "stored_session_id": f"stored-{new_sid}",
                "messages": list(self.session_history.get(new_sid, [])),
                "running": False,
                "status": "idle",
            }, []

        if method == "session.active_list":
            sessions = [
                {
                    "id": sid,
                    "session_key": info.get("stored_session_id") or sid,
                    "status": self.session_status.get(sid, "idle"),
                }
                for sid, info in self.sessions.items()
            ]
            return {"sessions": sessions}, []

        if method == "session.history":
            sid = params.get("session_id", "")
            messages = list(self.session_history.get(sid, []))
            return {"count": len(messages), "messages": messages}, []

        if method == "session.interrupt":
            sid = params.get("session_id", "")
            self.interrupt_requests.append(dict(params))
            if sid:
                self.session_status[sid] = "idle"
            events = (
                [self._message_complete_event(sid, "", "interrupted")]
                if self.interrupt_emit_complete
                else []
            )
            return {"status": "interrupted"}, events

        if method == "session.steer":
            self.steer_requests.append(dict(params))
            return {
                "status": self.steer_status,
                "text": params.get("text", ""),
            }, []

        if method == "process.list":
            sid = params.get("session_id", "")
            return {"processes": list(self.processes.get(sid, []))}, []

        if method == "process.kill":
            sid = params.get("session_id", "")
            proc_id = str(params.get("process_id") or "")
            self.killed_processes.append(proc_id)
            self.processes[sid] = [
                p for p in self.processes.get(sid, [])
                if p.get("session_id") != proc_id
            ]
            return {"status": "killed"}, []

        return {}, []

    # -- test helpers ---------------------------------------------------------

    def _event(self, etype: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": etype, "payload": payload},
        }

    def _message_complete_event(
        self, sid: str, text: str, status: str = "complete"
    ) -> dict[str, Any]:
        event = self._event(
            "message.complete",
            {"text": text, "status": status},
        )
        if sid:
            # Real Hermes events carry params.session_id (P0 probe).
            event["params"]["session_id"] = sid
        return event

    def emit_message_complete(
        self, sid: str, text: str, status: str = "complete"
    ) -> None:
        self.emit_event(
            "message.complete",
            {"text": text, "status": status, "session_id": sid},
            session_id=sid,
        )

    def emit_event(self, etype: str, payload: dict[str, Any], *, session_id: str = "") -> None:
        """Queue an event to the most recent connection (turn completions,
        blocking interaction requests). Real Hermes events carry
        params.session_id (P0 probe)."""
        if not self._conns:
            raise RuntimeError("no connection to emit to")
        ws = self._conns[-1]
        params = {"type": etype, "payload": payload}
        if session_id:
            params["session_id"] = session_id
        asyncio.create_task(ws.send(json.dumps({
            "jsonrpc": "2.0", "method": "event", "params": params,
        })))
