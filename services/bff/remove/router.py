from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from bff.common.deps import get_principal, get_store
from bff.common.schemas import RemoveRequest
from bff.common.store import BffError

router = APIRouter()


@router.post("/events/remove")
async def remove_events(
    request: Request,
    principal=Depends(get_principal),
    store=Depends(get_store),
) -> dict:
    """删除 video 事件（部分成功，docs/bff_service_remove.md §2.1）。

    请求体手动解析：格式非法一律 400 `bad_request`（契约口径——不用 FastAPI
    参数校验的 422，与执行面同语义）。token 经 get_principal 校验（dev-token，
    失败 401 `unauthorized`）；device_id 与白名单取交定可见性。
    """
    from bff.remove.service import build_remove

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — 空体/非法 JSON 一律 bad_request
        payload = None
    if not isinstance(payload, dict):
        raise BffError("bad_request", "请求体应为 JSON 对象")
    try:
        body = RemoveRequest(**payload)
    except ValidationError as exc:
        raise BffError("bad_request", str(exc.errors()[0]["msg"])) from exc

    return await build_remove(store, principal, request.app.state.config.bff,
                              body.event_refs)
