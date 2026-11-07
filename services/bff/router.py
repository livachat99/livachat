from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from bff.agent_jobs.router import router as agent_jobs_router
from bff.common.media import router as media_router
from bff.common.store import BffError
from bff.graph.router import router as graph_router
from bff.remove.router import router as remove_router
from bff.search.router import router as search_router
from bff.timeline.router import router as timeline_router

router = APIRouter(prefix="/api/mylife")
router.include_router(timeline_router)
router.include_router(graph_router)
router.include_router(search_router)
router.include_router(media_router)
router.include_router(remove_router)
router.include_router(agent_jobs_router)

_STATUS_BY_CODE = {"bad_request": 400, "unauthorized": 401, "not_found": 404,
                   "conflict": 409, "range_invalid": 416,
                   "upstream_unavailable": 502,
                   "removal_disabled": 503,
                   "agent_relay_unavailable": 503,
                   "cancel_incomplete": 503}


def register(app: FastAPI) -> None:
    """Mount the BFF query layer + its error envelope handler on the app."""

    @app.exception_handler(BffError)
    async def _bff_error_handler(_: Request, exc: BffError) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": {"code": exc.code, "message": exc.message}})

    app.include_router(router)
