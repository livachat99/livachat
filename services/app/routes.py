import asyncio
import logging
import sqlite3
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.lifespan import get_shutdown_handler
from gateway.endpoint import router as gateway_router
from shared.utils.errors import AuthError

router = APIRouter()
router.include_router(gateway_router)

logger = logging.getLogger("livachat.routes")


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict:
    """Readiness probe with real dependency checks.

    Returns 503 if shutting down or any dependency is unhealthy.
    去 Redis 后 readiness 仅剩的信号面是 chat store 存活检查（§6.2）——
    checks 不得为空恒真。
    """
    handler = get_shutdown_handler()
    if handler.is_shutting_down:
        response.status_code = 503
        return {"ok": False, "reason": "shutting_down"}

    checks: dict[str, str] = {}

    # Chat store — 同步直连一次轻量查询（to_thread）
    chat_store = getattr(request.app.state, "chat_store", None)
    if chat_store is not None:
        db_path = chat_store._db_path
        try:

            def _ping() -> None:
                with sqlite3.connect(db_path, timeout=2) as conn:
                    conn.execute("SELECT 1").fetchone()

            await asyncio.to_thread(_ping)
            checks["chat_store"] = "ok"
        except Exception as exc:
            checks["chat_store"] = f"error: {exc}"

    errors = [k for k, v in checks.items() if v != "ok"]
    if errors:
        response.status_code = 503
        return {"ok": False, "checks": checks}

    return {"ok": True, "checks": checks}


@router.get("/healthz_token")
async def healthz_token(
    request: Request,
    token: str = Query(...),
    device_id: str = Query(...),
) -> dict:
    """握手预检:token/device_id 校验语义与 /v1/ws/stream 握手一致。"""
    authenticator = request.app.state.authenticator
    try:
        authenticator.verify(token, device_id)
    except AuthError:
        raise HTTPException(status_code=401, detail="authentication failed")
    try:
        uuid.UUID(device_id, version=4)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="invalid device_id format")
    return {"ok": True}


# route 已摘除(不对公网暴露);实现保留供内部复用
async def metrics() -> Response:
    """Prometheus metrics endpoint.

    合并两个 registry：prometheus 默认（python_gc/process 等）+ 应用自定义
    `MetricsRegistry`（全部 `livachat_*` 指标——此前只导出前者，app 指标
    从未在此端点可见，docs/server_design.md §metrics 契约因此未兑现）。
    """
    try:
        from prometheus_client import REGISTRY, generate_latest

        from shared.observability.metrics import get_registry

        body = generate_latest(REGISTRY) + generate_latest(get_registry().registry)
        return Response(content=body, media_type="text/plain; charset=utf-8")
    except Exception:
        return Response(content="", media_type="text/plain; charset=utf-8")
