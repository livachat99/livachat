from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from bff.common.deps import get_principal, get_store, get_timezone
from bff.common.schemas import OverviewResponse, TimelineResponse

router = APIRouter()


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    principal=Depends(get_principal),
    store=Depends(get_store),
    tz_cfg: str = Depends(get_timezone),
    timezone: str | None = Query(default=None),
    # §8：limit=天数页大小（缺省 = 全量）；cursor 不带 pattern——非法形态须 400 而非 422
    # （校验在 build_overview 内，对齐 /timeline 的 invalid cursor 语义）
    limit: int | None = Query(default=None, ge=1, le=400),
    cursor: str | None = Query(default=None),
) -> OverviewResponse:
    from bff.timeline.service import build_overview

    return build_overview(store, principal.device_id, timezone, default_tz=tz_cfg,
                          limit=limit, cursor=cursor)


@router.get("/timeline", response_model=TimelineResponse)
async def timeline(
    principal=Depends(get_principal),
    store=Depends(get_store),
    tz_cfg: str = Depends(get_timezone),
    date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    session_id: str | None = Query(default=None),
    types: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: str | None = Query(default=None),
    timezone: str | None = Query(default=None),
) -> TimelineResponse:
    from bff.timeline.service import build_timeline

    return build_timeline(
        store,
        principal.device_id,
        date_from,
        date_to,
        session_id,
        types,
        limit,
        cursor,
        timezone,
        default_tz=tz_cfg,
    )
