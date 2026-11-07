"""Starlette 装配：/mcp + /api（运维 REST）+ /healthz + /readyz + bearer auth + 每 IP 限流（§4.1/§4.2）。"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import structlog
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import VERSION
from .config import LvdataMcpConfig
from .remove import EventRemover
from .schemas import RemoveParams, TriggerParams
from .store import DataStore, StoreError
from .tools import error_result, register_tools
from .vec import VecIndex, build_provider
from .watcher import Watcher

log = structlog.get_logger("lvdata_mcp.server")

# MCP initialize 的 instructions：agent 连上后一次性进入其上下文，**常驻**——措辞即契约
# （改动须评审），逐字留档于 docs/data/lvdata_mcp_tools.md §14.2（§16 修订版），
# 测试 test_tools.py::test_initialize_metadata 按同字面量锁定。
# §16：工具描述里逐字重复的共同约定（日期窗/时间口径/轻量投影/详情链/错误约定）收拢到这里，
# 描述只留各工具特有行为。
INSTRUCTIONS = (
    "LivaChat's recorded multimodal lifelog data. Read-only.\n"
    "Locate with get_recent_events / get_timeline; search with search (fast, preferred) / "
    "semantic_search (alternative wording); fetch details with get_event by event_ref.\n"
    "Conventions: start_date/end_date are YYYY-MM-DD inclusive; timezone is the timezone of "
    "the current query; list/search results are lightweight projections; fetch details and "
    "keyframe images with get_event by event_ref; failures return error{code,message} "
    "(do not rely only on isError).")


def create_app(settings: LvdataMcpConfig, watcher: Watcher | None = None,
               start_watcher: bool = True, vec_index: VecIndex | None = None,
               remover: EventRemover | None = None) -> Starlette:
    store = DataStore(settings)
    started_at = time.monotonic()
    if vec_index is None:
        # embedding 整块为空 → build_provider 返回 None（离线模式，仅 FTS）
        provider = build_provider(settings)
        if provider is not None:
            vec_index = VecIndex(settings, store, provider)
    if remover is None:
        # 删除执行器（docs/bff_service_remove.md §2.2）；离线模式 vec_index=None 亦可
        remover = EventRemover(settings, vec_index)
    mcp = FastMCP("lvdata", instructions=INSTRUCTIONS, stateless_http=True,
                  json_response=True)
    # FastMCP（mcp 1.29.1）没有 version 参数，serverInfo.version 会回落成 SDK 版本
    # （"1.29.1"）；低层 Server 支持 version，这里补上（§14 D1）。测试断言 initialize 的
    # serverInfo.version == VERSION——SDK 将来若改了这层，会以测试失败暴露而非静默回退。
    mcp._mcp_server.version = VERSION
    register_tools(mcp, store, vec_index)
    mcp_app = mcp.streamable_http_app()

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def readyz(request: Request) -> JSONResponse:
        if not store.db_ok():
            return JSONResponse({"status": "unavailable", "reason": "database_not_initialized"},
                                status_code=503)
        if watcher is not None and not watcher.first_poll_done:
            return JSONResponse({"status": "unavailable",
                                 "reason": "watcher_first_poll_pending"},
                                status_code=503)
        return JSONResponse({"status": "ok"})

    async def api_status(request: Request) -> JSONResponse:
        """运维面：服务与数据新鲜度（原 get_status MCP tool，输出同构）。

        未初始化 → **503**（与 `/readyz` 同口径："未就绪"而非服务端故障，I-5）。
        注：`store.get_status` 对缺库**不抛错**（返回 `data.error`），故此处显式判定；
        其余 StoreError（如判定后库被删的竞态）→ 503/500 按错误码映射。
        """
        if not store.db_ok():
            return JSONResponse(
                error_result("database_not_initialized",
                             f"Database not initialized: {store.db_path}",
                             hint="Run lvdata_inventory.py or wait for the watcher to create tables"),
                status_code=503)
        try:
            out = store.get_status(time.monotonic() - started_at)
        except StoreError as e:
            code = 503 if e.code == "database_not_initialized" else 500
            return JSONResponse(error_result(e.code, e.message, **e.extra),
                                status_code=code)
        if watcher is not None and "watcher" in out:
            out["watcher"] = watcher.status()
        if vec_index is not None:      # 未配 embedding → 不出现 cache 字段（§13 D2）
            out["cache"] = {"query_embeddings": vec_index.query_cache_stats()}
        return JSONResponse(out)

    async def api_trigger(request: Request) -> JSONResponse:
        """运维面：手动触发 pipeline（原 trigger_analysis MCP tool，入队语义不变）。"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — 空体/非法 JSON 一律 invalid_param
            body = None
        if not isinstance(body, dict):
            return JSONResponse(
                error_result("invalid_param", "Request body must be a JSON object"),
                status_code=400)
        if watcher is None:
            return JSONResponse(
                error_result("watcher_not_running", "Watcher is not running"),
                status_code=503)
        try:
            p = TriggerParams(**body)
        except ValidationError as e:
            return JSONResponse(
                error_result("invalid_param", str(e.errors()[0]["msg"])),
                status_code=400)
        if p.session_id is not None:
            try:
                exists = store.session_exists(p.session_id)
            except StoreError as e:
                return JSONResponse(error_result(e.code, e.message, **e.extra),
                                    status_code=500)
            if not exists:
                return JSONResponse(error_result(
                    "not_found", f"Session not found: {p.session_id}",
                    hint="Wait for the watcher to finish inventory on its next poll"),
                    status_code=404)
        await watcher.trigger(p.stages, force=p.force, session_id=p.session_id)
        return JSONResponse({"accepted": True, "stages": p.stages or "all",
                             "reason": "enqueued"})

    async def api_remove(request: Request) -> JSONResponse:
        """运维面：删除 video 事件（同步执行，docs/bff_service_remove.md §2.2）。

        `EventRemover.remove` 是同步阻塞实现 → 必须 `asyncio.to_thread` 卸载
        （§3 执行载体）：直接内联会阻塞事件循环（healthz/status/MCP 全停）。
        注意勿仿 `api_trigger` 的入队内联写法（trigger 非阻塞）。
        部分成功语义（D7）：ref 级失败进响应 `skipped`，请求格式合法恒 200。
        """
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — 空体/非法 JSON 一律 invalid_param
            body = None
        if not isinstance(body, dict):
            return JSONResponse(
                error_result("invalid_param", "Request body must be a JSON object"),
                status_code=400)
        try:
            p = RemoveParams(**body)
        except ValidationError as e:
            return JSONResponse(
                error_result("invalid_param", str(e.errors()[0]["msg"])),
                status_code=400)
        try:
            result = await asyncio.to_thread(remover.remove, p.event_refs)
        except Exception as e:  # noqa: BLE001 — T1 异常（同事务已回滚）→ 500 信封
            log.exception("events remove failed")
            return JSONResponse(
                error_result("internal", str(e)[:300]), status_code=500)
        return JSONResponse(result)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        # mcp 的 session_manager.run() 挂在 mcp_app 的 lifespan 上，必须透传，
        # 否则请求时 task group 未初始化（500）
        async with mcp_app.router.lifespan_context(app):
            if watcher is not None and start_watcher:
                await watcher.start()
            # 启动完成一行 → stdout（用户指令 2026-10-13；有意不走 logging——serve 运行态
            # 只写日志文件，docs/server_logging.md §8；flush 保证管道/重定向下即时可见）
            try:
                print(f"server started: listening address: {settings.server.host}:{settings.server.port}", flush=True)
            except OSError:  # 装饰性输出不得阻塞启动（stdout 管道已关闭等；如 `serve | head`）
                pass
            try:
                yield
            finally:
                if watcher is not None and start_watcher:
                    await watcher.stop()

    # access log 必须最外层（list 首位）：Starlette 按 reversed() 包裹，
    # 列表第一个最先被请求经过——才能看到内层 auth/限流返回的 401/429
    middleware = [Middleware(access_log_middleware())]
    if settings.server.token:
        middleware.append(Middleware(bearer_auth_middleware(settings.server.token)))
    middleware.append(Middleware(rate_limit_middleware(settings.server.max_ip_conn)))

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/readyz", readyz),
            Route("/api/status", api_status),
            Route("/api/analysis/trigger", api_trigger, methods=["POST"]),
            Route("/api/events/remove", api_remove, methods=["POST"]),
            *mcp_app.routes,
        ],
        middleware=middleware,
        lifespan=lifespan,
    )


def bearer_auth_middleware(token: str):
    """仅作用于 /mcp 与 /api；/healthz /readyz 不鉴权（供探活）。

    401 体与其余错误同构（`{"error": {code, message}}`，I-5）——运维脚本按同一形状解析。
    """
    expected = f"Bearer {token}"

    class _Auth(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path.startswith(("/mcp", "/api")):
                if request.headers.get("authorization") != expected:
                    return JSONResponse(
                        error_result("unauthorized", "Missing or invalid Bearer token"),
                        status_code=401)
            return await call_next(request)

    return _Auth


def access_log_middleware():
    """/mcp 与 /api 请求访问日志：tool/ip/status/latency；慢请求与 4xx/5xx 升 warning。

    放最外层才能看到内层 auth/限流直接返回的 401/429；healthz/readyz 不记。
    """

    class _AccessLog(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if not request.url.path.startswith(("/mcp", "/api")):
                return await call_next(request)
            t0 = time.perf_counter()
            tool = None
            try:
                body = await request.body()  # Starlette 缓存 body，下游可再读
                if body:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        tool = data.get("params", {}).get("name")
            except Exception:  # noqa: BLE001 — 取不到 tool 名不阻塞请求
                pass
            response = await call_next(request)
            meta = {
                "path": request.url.path,
                "method": request.method,
                "status": response.status_code,
                "tool": tool,
                "ip": request.client.host if request.client else "unknown",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
            if response.status_code >= 400 or meta["latency_ms"] > 1000:
                log.warning("mcp request slow_or_error", **meta)
            else:
                log.info("mcp request", **meta)
            return response

    return _AccessLog


def rate_limit_middleware(max_conn: int):
    """每 IP 并发请求数限制，防止 agent 失控打爆后端。"""
    active: dict[str, int] = defaultdict(int)
    lock = asyncio.Lock()

    class _RateLimit(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if not request.url.path.startswith(("/mcp", "/api")):
                return await call_next(request)
            ip = request.client.host if request.client else "unknown"
            async with lock:
                if active[ip] >= max_conn:
                    # 体与其余错误同构（I-5）
                    return JSONResponse(
                        error_result("too_many_requests",
                                     f"Too many concurrent requests per IP "
                                     f"(limit {max_conn}); retry later"),
                        status_code=429)
                active[ip] += 1
            try:
                return await call_next(request)
            finally:
                async with lock:
                    active[ip] -= 1
                    if active[ip] <= 0:
                        active.pop(ip, None)

    return _RateLimit
