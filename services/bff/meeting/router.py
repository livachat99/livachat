"""公开 BFF：POST /api/mylife/join_meeting（docs/livachat_meeting.md §6.2）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from bff.common.store import BffError
from bff.meeting.schemas import JoinMeetingRequest
from bff.meeting.service import build_join, enforce_rate_limit

router = APIRouter()

MAX_JOIN_BODY_BYTES = 16 * 1024


async def _read_json_body(request: Request) -> object:
    """Read a small JSON body with a hard cap (public endpoint: no unbounded buffering)."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_JOIN_BODY_BYTES:
                raise BffError("bad_request", "request body too large")
        except ValueError as exc:
            raise BffError("bad_request", "invalid Content-Length") from exc
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_JOIN_BODY_BYTES:
                raise BffError("bad_request", "request body too large")
            chunks.append(chunk)
    except BffError:
        raise
    except Exception:  # noqa: BLE001 — 断连/流错误一律按非法请求处理
        return None
    try:
        return json.loads(b"".join(chunks))
    except (UnicodeDecodeError, ValueError):
        return None


@router.post("/join_meeting")
async def join_meeting(request: Request, response: Response) -> dict:
    """Web 会议页换 LiveKit token；公开，不要求 App token/device_id。"""
    meeting = getattr(request.app.state.config, "meeting", None)
    if meeting is None or not meeting.enabled:
        raise BffError("meeting_disabled", "meeting feature is disabled")

    # Count malformed/oversized attempts too; do it before body parsing.
    enforce_rate_limit(request)
    payload = await _read_json_body(request)
    if not isinstance(payload, dict):
        raise BffError("bad_request", "请求体应为 JSON 对象")
    try:
        body = JoinMeetingRequest(**payload)
    except ValidationError as exc:
        raise BffError("bad_request", str(exc.errors()[0]["msg"])) from exc

    result = await build_join(request, body)
    response.headers["Cache-Control"] = "no-store"
    return result
