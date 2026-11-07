from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from bff.common.deps import get_principal, get_store, get_timezone
from bff.common.schemas import EntityResponse, GraphResponse

router = APIRouter()


@router.get("/graph", response_model=GraphResponse)
async def graph(
    principal=Depends(get_principal),
    store=Depends(get_store),
    tz_cfg: str = Depends(get_timezone),
    date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    types: str | None = Query(default=None),
    focus: str | None = Query(default=None),
    depth: int = Query(default=1, ge=1, le=3),
    min_weight: int = Query(default=1, ge=0),
) -> GraphResponse:
    from bff.graph.service import build_graph

    return build_graph(
        store,
        principal.device_id,
        date_from,
        date_to,
        types,
        focus,
        depth,
        min_weight,
        default_tz=tz_cfg,
    )


@router.get("/graph/entity/{entity_id}", response_model=EntityResponse)
async def entity(
    entity_id: str,
    principal=Depends(get_principal),
    store=Depends(get_store),
    tz_cfg: str = Depends(get_timezone),
) -> EntityResponse:
    from bff.graph.service import build_entity

    return build_entity(store, entity_id, device_id=principal.device_id,
                        default_tz=tz_cfg)
