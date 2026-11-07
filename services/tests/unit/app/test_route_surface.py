"""对外路由面回归：/mylife 挂载与自动文档端点摘除后不得复活；核心端点保持可达。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _app():
    from app.application import create_app
    from shared.config.models import AppConfig

    return create_app(config=AppConfig())


def test_metrics_route_removed():
    assert TestClient(_app()).get("/metrics").status_code == 404


def test_mylife_mount_removed():
    # 工作区 apps/web/mylife/dist 存在——旧装配下 /mylife/ 与 index.html 会 200
    # （/mylife 会 307 跳转）；本测试防挂载回归。
    client = TestClient(_app())
    for path in ("/mylife", "/mylife/", "/mylife/index.html"):
        assert client.get(path).status_code == 404


def test_docs_endpoints_disabled():
    client = TestClient(_app())
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_core_routes_intact():
    from app.lifespan import get_shutdown_handler

    app = _app()
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    # 集成用例可能已触发优雅停机（模块级 shutdown 单例残留）→ 显式复位再断言（同 test_readyz 手法）
    handler = get_shutdown_handler()
    original = handler._shutting_down
    handler._shutting_down = False
    try:
        assert client.get("/readyz").status_code == 200
    finally:
        handler._shutting_down = original
    # starlette 1.6 的 include_router 不再摊平 app.routes（嵌套 _IncludedRouter 无 .path）；
    # 用 in-process app.openapi()（不受 openapi_url=None 影响）断言 BFF 未波及。
    assert "/api/mylife/overview" in app.openapi()["paths"]
