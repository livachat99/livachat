"""/healthz_token 握手预检端点——语义对齐 gateway/endpoint.py 握手。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.auth import wire_digest

V4 = "123e4567-e89b-42d3-a456-426614174000"
# version=4 参数只重写版本位、不校验版本 nibble——预检须与握手同语义(见 routes.py healthz_token)
V1 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
SECRET = "test-token"
TOKEN = wire_digest(SECRET)


def _client() -> TestClient:
    from app.application import create_app
    from gateway.auth import Authenticator
    from shared.config.models import AppConfig, AuthConfig

    app = create_app(config=AppConfig())
    # TestClient 不带 with → lifespan 不跑；手动镜像 application.py:41-42 的装配
    app.state.authenticator = Authenticator(AuthConfig(mode="dev", dev_token=SECRET))
    return TestClient(app)


def test_valid_token_and_device_ok():
    resp = _client().get("/healthz_token", params={"token": TOKEN, "device_id": V4})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_wrong_token_401():
    resp = _client().get("/healthz_token", params={"token": "wrong", "device_id": V4})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "authentication failed"


def test_non_uuid_device_401():
    resp = _client().get("/healthz_token", params={"token": TOKEN, "device_id": "not-a-uuid"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid device_id format"


def test_v1_uuid_accepted_parity_with_handshake():
    resp = _client().get("/healthz_token", params={"token": TOKEN, "device_id": V1})
    assert resp.status_code == 200


def test_empty_device_id_401():
    resp = _client().get("/healthz_token", params={"token": TOKEN, "device_id": ""})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid device_id format"


def test_missing_token_422():
    resp = _client().get("/healthz_token", params={"device_id": V4})
    assert resp.status_code == 422


def test_wrong_token_beats_bad_device_id():
    """校验顺序与握手一致：先 token 后 device_id。"""
    resp = _client().get("/healthz_token", params={"token": "wrong", "device_id": "nope"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "authentication failed"
