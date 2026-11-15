"""join_meeting 业务：lvdata 查询 → 窗口校验 → LiveKit JWT 签发。"""

from __future__ import annotations

import secrets
import time
from datetime import timedelta
from typing import Any

from fastapi import Request

from bff.common.lvdata_api import get_meeting
from bff.common.store import BffError
from bff.meeting.ratelimit import PerIpRateLimiter
from bff.meeting.schemas import JoinMeetingRequest


def _limiter(request: Request, limit: int) -> PerIpRateLimiter:
    limiter = getattr(request.app.state, "meeting_rate_limiter", None)
    if limiter is None:
        limiter = PerIpRateLimiter(limit)
        request.app.state.meeting_rate_limiter = limiter
    return limiter


def enforce_rate_limit(request: Request) -> None:
    """Count every join attempt (including malformed bodies) before body parsing."""
    cfg = request.app.state.config
    ip = request.client.host if request.client else "unknown"
    if not _limiter(request, cfg.meeting.join_rate_limit_per_min).allow(ip):
        raise BffError("rate_limited", "too many join attempts; retry later")


def _meeting_times(meeting: dict) -> tuple[int, int]:
    try:
        start_ms = int(meeting["start_at_ms"])
        end_ms = int(meeting["end_at_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BffError("upstream_unavailable", "lvdata returned invalid meeting times") from exc
    return start_ms, end_ms


def _mint_token(cfg, *, meeting_number: str, participant_name: str,
                now_ms: int, end_ms: int) -> str:
    try:
        from livekit import api
    except ImportError as exc:  # 未安装依赖时不能让应用启动/模块导入失败
        raise BffError("meeting_disabled", "livekit-api is not installed") from exc

    remaining_s = max(1, (end_ms + cfg.meeting.expire_after_end_s * 1000 - now_ms) // 1000)
    ttl_s = min(cfg.meeting.token_ttl_s, max(60, remaining_s + 30))
    grants = api.VideoGrants(
        room_join=True,
        room=meeting_number,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
        can_publish_sources=["camera", "microphone", "screen_share", "screen_share_audio"],
    )
    return (
        api.AccessToken(cfg.meeting.livekit_key, cfg.meeting.livekit_secret)
        .with_identity(f"guest_{secrets.token_hex(8)}")
        .with_name(participant_name)
        .with_ttl(timedelta(seconds=ttl_s))
        .with_grants(grants)
        .to_jwt()
    )


async def build_join(request: Request, body: JoinMeetingRequest) -> dict[str, Any]:
    cfg = request.app.state.config
    if not cfg.meeting.enabled:
        raise BffError("meeting_disabled", "meeting feature is disabled")

    meeting = await get_meeting(
        cfg.bff.lvdata_api_url,
        cfg.bff.lvdata_api_token,
        cfg.bff.lvdata_api_timeout_s,
        body.meeting_number,
    )
    upstream_number = str(meeting.get("meeting_number", ""))
    if upstream_number != body.meeting_number:
        raise BffError("upstream_unavailable", "lvdata returned a different meeting number")

    start_ms, end_ms = _meeting_times(meeting)
    now_ms = int(time.time() * 1000)
    opens_at_ms = start_ms - cfg.meeting.join_early_s * 1000
    expires_at_ms = end_ms + cfg.meeting.expire_after_end_s * 1000
    if now_ms < opens_at_ms:
        raise BffError("meeting_not_started", "meeting is not open yet",
                       opens_at_ms=opens_at_ms)
    if now_ms > expires_at_ms:
        raise BffError("meeting_expired", "meeting has ended",
                       expires_at_ms=expires_at_ms)

    token = _mint_token(
        cfg,
        meeting_number=body.meeting_number,
        participant_name=body.participant_name,
        now_ms=now_ms,
        end_ms=end_ms,
    )
    return {
        "serverUrl": cfg.meeting.livekit_server,
        "roomName": body.meeting_number,
        "participantName": body.participant_name,
        "participantToken": token,
    }
