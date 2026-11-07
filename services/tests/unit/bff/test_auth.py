"""BFF token 鉴权（Phase 2，§5.5）：get_principal 单点校验；失败 401 envelope。

device_id 不校验（仅白名单内过滤）——非 UUID 值 + 正确 token 必须非 401。
"""

from __future__ import annotations

from gateway.auth import wire_digest
from tests.unit.bff.conftest import DEVICE_A, FRAME_ID, SESS_A

SECRET = "test-secret"
TOKEN = wire_digest(SECRET)
UNAUTHORIZED = {"error": {"code": "unauthorized", "message": "authentication failed"}}


def test_valid_token_ok(make_client):
    resp = make_client(dev_token=SECRET).get(
        "/api/mylife/timeline", params={"token": TOKEN})
    assert resp.status_code == 200


def test_valid_token_media_ok(make_client):
    """media 强制条款（§5.5）：同一 token 鉴权，正确 token 正常取回。"""
    resp = make_client(dev_token=SECRET).get(
        f"/api/mylife/media/{DEVICE_A}/{SESS_A}/video/{FRAME_ID}.jpg",
        params={"token": TOKEN})
    assert resp.status_code == 200


def test_wrong_token_401_envelope(make_client):
    resp = make_client(dev_token=SECRET).get(
        "/api/mylife/timeline", params={"token": "wrong"})
    assert resp.status_code == 401
    assert resp.json() == UNAUTHORIZED


def test_plaintext_secret_rejected(make_client):
    """硬切换：明文 secret 直接当 token 发 → 401（wire token 必须是摘要）。"""
    resp = make_client(dev_token=SECRET).get(
        "/api/mylife/timeline", params={"token": SECRET})
    assert resp.status_code == 401
    assert resp.json() == UNAUTHORIZED


def test_missing_token_401(make_client):
    resp = make_client(dev_token=SECRET).get("/api/mylife/timeline")
    assert resp.status_code == 401
    assert resp.json() == UNAUTHORIZED


def test_auth_covers_all_endpoints(make_client):
    """7 端点全部经 get_principal：错 token 一律 401（media/remove 不可豁免）。"""
    client = make_client(dev_token=SECRET)
    for path in ("overview", "timeline", "search", "graph"):
        assert client.get(f"/api/mylife/{path}", params={"token": "wrong"}).status_code == 401
    assert client.get(
        f"/api/mylife/media/{DEVICE_A}/{SESS_A}/video/{FRAME_ID}.jpg",
        params={"token": "wrong"}).status_code == 401
    assert client.post(
        "/api/mylife/events/remove", params={"token": "wrong"}, json={}).status_code == 401


def test_device_id_not_validated(make_client):
    """非 UUID device_id + 正确 token → 非 401（过滤照常短路为 0 命中）。"""
    resp = make_client(dev_token=SECRET).get(
        "/api/mylife/search",
        params={"token": TOKEN, "device_id": "unknown-device", "q": "今天"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
