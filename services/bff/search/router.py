from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from bff.common.deps import get_principal, get_store, get_timezone
from bff.common.schemas import SearchResponse

router = APIRouter()


@router.get("/search", response_model=SearchResponse)
def search(
    principal=Depends(get_principal),
    store=Depends(get_store),
    tz_cfg: str = Depends(get_timezone),
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    timezone: str | None = Query(default=None),
) -> SearchResponse:
    # def 端点：starlette 自动进线程池，避免同步文件 IO 阻塞事件循环（对齐 media.py 先例）
    from bff.search.service import build_search

    return build_search(
        store,
        principal.device_id,
        q,
        limit,
        cursor,
        timezone,
        default_tz=tz_cfg,
    )
