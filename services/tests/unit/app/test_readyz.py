"""/readyz chat store 存活检查（chatserver_no_redis.md §6.2：去 Redis 后
readiness 仅剩的信号面，checks 不得为空恒真）。"""

from __future__ import annotations

from types import SimpleNamespace

from app.routes import readyz
from shared.persistence.chat_store import ChatStore


def _request(chat_store=None, *, shutting_down: bool = False) -> tuple:
    import app.lifespan as lifespan_mod

    handler = lifespan_mod.get_shutdown_handler()
    original = handler._shutting_down
    handler._shutting_down = shutting_down
    app = SimpleNamespace(state=SimpleNamespace(chat_store=chat_store))
    req = SimpleNamespace(app=app)
    return req, handler, original


class TestReadyz:
    async def test_chat_store_ok(self, tmp_path):
        store = ChatStore(str(tmp_path / "chat_server.db"))
        await store.start()
        try:
            req, handler, original = _request(store)
            try:
                resp = SimpleNamespace(status_code=200)
                out = await readyz(req, resp)
            finally:
                handler._shutting_down = original
            assert out["ok"] is True
            assert out["checks"]["chat_store"] == "ok"
        finally:
            await store.stop()

    async def test_chat_store_error_returns_503(self, tmp_path):
        # 指向不存在目录的 db 路径 → 连接失败 → 503
        store = ChatStore(str(tmp_path / "missing" / "chat_server.db"))
        req, handler, original = _request(store)
        try:
            resp = SimpleNamespace(status_code=200)
            out = await readyz(req, resp)
        finally:
            handler._shutting_down = original
        assert resp.status_code == 503
        assert out["ok"] is False
        assert out["checks"]["chat_store"].startswith("error:")

    async def test_shutting_down_returns_503(self):
        req, handler, original = _request(None, shutting_down=True)
        try:
            resp = SimpleNamespace(status_code=200)
            out = await readyz(req, resp)
        finally:
            handler._shutting_down = original
        assert resp.status_code == 503
        assert out["reason"] == "shutting_down"
