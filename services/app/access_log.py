"""HTTP access log middleware（structlog JSON）。

uvicorn.access 被 setup_logging 钉在 WARNING（shared/observability/logging.py
「Quiet noisy libs」）——HTTP 请求默认不可见。此处用应用日志栈补访问记录：
method/path/status/ip/latency；4xx/5xx 与慢请求升 warning。
"""

from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

log = structlog.get_logger("livachat.access")


def access_log_middleware():
    class _AccessLog(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            t0 = time.perf_counter()
            meta = {
                "method": request.method,
                "path": request.url.path,
                "ip": request.client.host if request.client else "unknown",
            }
            try:
                response = await call_next(request)
                meta["status"] = response.status_code
            except Exception:
                meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                log.exception("http request", **meta)
                raise
            meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if meta["status"] >= 400 or meta["latency_ms"] > 1000:
                log.warning("http request", **meta)
            else:
                log.info("http request", **meta)
            return response

    return _AccessLog
