from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from bff.common.deps import AgentRelayControl, Principal, get_agent_relay, get_principal
from bff.common.schemas import CancelAgentJobRequest
from bff.common.store import BffError

router = APIRouter()


@router.post("/agent-jobs/cancel")
async def cancel_agent_job(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    relay: Annotated[AgentRelayControl, Depends(get_agent_relay)],
) -> dict:
    """取消一个 running agent job（docs/android_agent_job_status.md §7.2）。

    请求体手动解析：格式非法一律 400 `bad_request`（与 events/remove 同口径，
    不使用 FastAPI 参数校验的 422）。取消的是整个 job（adapter 语义），
    成功响应以取消后的 JobRecord 终态为准。
    """
    from bff.agent_jobs.service import build_cancel

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — 空体/非法 JSON 一律 bad_request
        payload = None
    if not isinstance(payload, dict):
        raise BffError("bad_request", "请求体应为 JSON 对象")
    try:
        body = CancelAgentJobRequest(**payload)
    except ValidationError as exc:
        raise BffError("bad_request", str(exc.errors()[0]["msg"])) from exc

    return await build_cancel(relay, principal, body.job_id)
