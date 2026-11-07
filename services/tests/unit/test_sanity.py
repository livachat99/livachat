"""Sanity test to verify the test harness works."""


def test_sanity():
    assert True


async def test_healthz_client():
    from httpx import ASGITransport, AsyncClient

    from app.application import create_app
    from shared.config.models import AppConfig

    cfg = AppConfig()
    cfg.auth.mode = "dev"
    cfg.auth.dev_token = "test-token"
    app = create_app(config=cfg)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
