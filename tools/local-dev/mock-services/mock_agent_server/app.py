"""Mock Hermes Agent Server — SSE + WebSocket JSON-RPC 2.0 endpoints for testing agent-relay.

Matches the real Hermes Dashboard protocol: server→client pushes use
method="event" with the event type in params.type.

Usage:
    python tools/local-dev/mock-services/mock_agent_server/app.py
    # or: uvicorn mock_agent_server.app:app --port 8090
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_agent")

app = FastAPI(title="Mock Hermes Agent", version="0.3.0")

# -- configurable behavior (HTTP SSE) -----------------------------------------

DELAY_MS: int = 200  # artificial delay per chunk
ERROR_RATE: float = 0.0  # 0.0–1.0 probability of 500 error
INJECT_TIMEOUT: bool = False  # if True, never respond

# -- configurable behavior (WebSocket JSON-RPC) --------------------------------

WS_DELAY_MS: int = 0  # artificial delay between WS events
WS_INJECT_APPROVAL: bool = False  # send approval.request before message.complete
WS_INJECT_ERROR: bool = False  # send error event instead of message.complete
WS_RETURN_ERROR_ON_SESSION_CREATE: bool = False  # return JSON-RPC error for session.create
WS_CLOSE_AFTER_SESSION_CREATE: bool = False  # close WS after session.create response


# -- HTTP SSE endpoints -------------------------------------------------------


@app.get("/health")
async def health():
    return {"ok": True, "service": "mock-hermes-agent"}


@app.post("/api/chat")
async def chat(request: Request):
    """Simulate Hermes /api/chat SSE endpoint."""
    if INJECT_TIMEOUT:
        await asyncio.sleep(3600)
        return

    if ERROR_RATE > 0:
        import random
        if random.random() < ERROR_RATE:
            return StreamingResponse(
                _error_stream("internal server error"),
                media_type="text/event-stream",
                status_code=500,
            )

    try:
        body = await request.json()
    except Exception:
        body = {}

    text = body.get("text", "")
    session_id = body.get("session_id", "unknown")
    language = body.get("language", "en")

    logger.info("chat request", extra={"session_id": session_id, "text": text[:80], "language": language})

    return StreamingResponse(
        _chat_sse_stream(text, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/analyze")
async def analyze(request: Request):
    """Simulate Hermes /api/analyze SSE endpoint."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    transcript = body.get("transcript", "")
    session_id = body.get("session_id", "unknown")

    logger.info("analyze request", extra={"session_id": session_id, "transcript_len": len(transcript)})

    return StreamingResponse(
        _analyze_sse_stream(transcript, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# -- WebSocket JSON-RPC 2.0 endpoint (real Hermes protocol) ------------------


@app.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket):
    """Simulate Hermes /api/ws JSON-RPC 2.0 WebSocket endpoint.

    Real Hermes wraps all server→client pushes in method="event" with the
    actual event type in params.type.
    """
    await websocket.accept()
    logger.info("ws connection accepted")

    # Send gateway.ready as an event
    await _ws_event(websocket, "gateway.ready", {
        "skin": {"name": "default"},
    })
    logger.info("ws sent gateway.ready")

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            method = msg.get("method", "")
            req_id = msg.get("id")

            if method == "session.create":
                await _handle_session_create(websocket, req_id, msg)
            elif method == "prompt.submit":
                await _handle_prompt_submit(websocket, req_id, msg)
            elif method == "session.close":
                await _handle_session_close(websocket, req_id)
                break
            elif method == "approval.respond":
                logger.info("ws received approval.respond", extra={"msg": msg})
            else:
                await _ws_send(websocket, {
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })
    except WebSocketDisconnect:
        logger.info("ws client disconnected")
    except Exception:
        logger.exception("ws handler error")


async def _handle_session_create(websocket: WebSocket, req_id, msg):
    if WS_RETURN_ERROR_ON_SESSION_CREATE:
        await _ws_send(websocket, {
            "id": req_id,
            "error": {"code": -32000, "message": "Injected session.create error"},
        })
        return

    session_id = f"mock-session-{uuid.uuid4().hex[:8]}"
    await _ws_send(websocket, {
        "id": req_id,
        "result": {"session_id": session_id},
    })
    logger.info("ws session.created", extra={"session_id": session_id})

    if WS_CLOSE_AFTER_SESSION_CREATE:
        logger.info("ws closing after session.create (injected)")
        await websocket.close()


async def _handle_prompt_submit(websocket: WebSocket, req_id, msg):
    params = msg.get("params", {})
    text = params.get("text", "")
    session_id = params.get("session_id", "unknown")
    logger.info("ws prompt.submit", extra={"session_id": session_id, "text": text[:80]})

    response_text = f"Agent response to: {text}. Here is additional information from external sources."

    # Respond to prompt.submit immediately (real Hermes sends {"status": "streaming"})
    await _ws_send(websocket, {
        "id": req_id,
        "result": {"status": "streaming"},
    })

    # Send events (all as method="event", type in params.type)

    if WS_DELAY_MS:
        await asyncio.sleep(WS_DELAY_MS / 1000.0)

    await _ws_event(websocket, "thinking.delta", {"text": "Let me think about this..."})

    if WS_DELAY_MS:
        await asyncio.sleep(WS_DELAY_MS / 1000.0)

    await _ws_event(websocket, "reasoning.delta", {"text": "Reasoning step 1..."})

    if WS_DELAY_MS:
        await asyncio.sleep(WS_DELAY_MS / 1000.0)

    await _ws_event(websocket, "message.delta", {"text": "partial..."})

    # Optionally inject approval.request
    if WS_INJECT_APPROVAL:
        await _ws_event(websocket, "approval.request", {
            "approval_id": "approval-1",
            "message": "Allow tool execution?",
        })
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            logger.info("ws received approval response", extra={"raw": raw})
        except asyncio.TimeoutError:
            logger.warning("ws timeout waiting for approval.respond")

    # Optionally inject error event
    if WS_INJECT_ERROR:
        await _ws_event(websocket, "error", {
            "code": -32000,
            "message": "Injected server error",
        })
        return

    # Send message.complete with full text
    await _ws_event(websocket, "message.complete", {"text": response_text})


async def _handle_session_close(websocket: WebSocket, req_id):
    logger.info("ws session.close")
    await _ws_send(websocket, {
        "id": req_id,
        "result": {"closed": True},
    })


async def _ws_send(websocket: WebSocket, data: dict) -> None:
    """Send a JSON-RPC 2.0 response or request."""
    payload = json.dumps({**data, "jsonrpc": "2.0"}, ensure_ascii=False)
    await websocket.send_text(payload)


async def _ws_event(websocket: WebSocket, event_type: str, payload: dict | None = None) -> None:
    """Send a Hermes-style event (method="event", type in params.type)."""
    params: dict = {"type": event_type}
    if payload is not None:
        params["payload"] = payload
    msg = {"jsonrpc": "2.0", "method": "event", "params": params}
    await websocket.send_text(json.dumps(msg, ensure_ascii=False))


# -- SSE generators ----------------------------------------------------------


async def _chat_sse_stream(user_text: str, session_id: str):
    """Generate a simulated chat response as SSE events."""
    response_text = f"Agent response to: {user_text}. Here is additional information from external sources."

    words = response_text.split()
    for i, word in enumerate(words):
        is_final = (i == len(words) - 1)
        payload = json.dumps({
            "text": word + (" " if i < len(words) - 1 else ""),
            "is_final": is_final,
        }, ensure_ascii=False)
        yield f"data: {payload}\n\n"
        await asyncio.sleep(DELAY_MS / 1000.0)

    yield "[DONE]\n\n"


async def _analyze_sse_stream(transcript: str, session_id: str):
    """Generate a simulated analysis response."""
    import random
    topics = [
        "calendar event", "action item", "meeting note", "reminder",
        "task", "follow-up", "decision", "question",
    ]
    found = random.sample(topics, min(3, len(topics)))

    response_text = f"Analysis complete. Detected: {', '.join(found)}. Transcript length: {len(transcript)} chars."

    payload = json.dumps({"text": response_text, "is_final": True}, ensure_ascii=False)
    yield f"data: {payload}\n\n"
    yield "[DONE]\n\n"


async def _error_stream(message: str):
    yield f"data: {json.dumps({'error': message})}\n\n"
    yield "[DONE]\n\n"


# -- main --------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="info")
