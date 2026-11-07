"""GZipMiddleware 装配与行为（services/app/application.py）。

docs/android_timeline_image_gallery.md Q8/E6：overview 池放大后加 gzip（minimum_size=1024）
压 JSON；starlette 默认排除 content-type 为 image/*、audio/*、video/* 的响应，
且 206 分片响应整体跳过 → media/Range 路径零影响。

用真实 create_app 装配（而非裸 FastAPI + register），保证中间件接线本身被钉住。
"""

from __future__ import annotations

from tests.unit.bff.conftest import AUDIO_SEG, DEVICE_A, FRAME_ID, SESS_A


def _app_client(lvdata_root):
    from fastapi.testclient import TestClient

    from app.application import create_app
    from bff.common.store import LvdataStore
    from gateway.auth import Authenticator
    from shared.config.models import AppConfig, AuthConfig

    app = create_app(config=AppConfig())
    app.state.lvdata_store = LvdataStore(str(lvdata_root))
    # TestClient 不带 with → lifespan 不跑；手动镜像 application.py:41-42 的装配
    app.state.authenticator = Authenticator(AuthConfig(mode="dev", dev_token=""))
    return TestClient(app)


def test_json_response_is_gzipped(lvdata_root):
    """overview（夹具 ~1.3KB > minimum_size）→ content-encoding: gzip，可解压。"""
    resp = _app_client(lvdata_root).get(
        "/api/mylife/overview", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.json()["days"]  # httpx 透明解压后仍可解析


def test_media_jpeg_not_gzipped(lvdata_root):
    """image/jpeg 在排除表内 → 不压缩。"""
    resp = _app_client(lvdata_root).get(
        f"/api/mylife/media/{DEVICE_A}/{SESS_A}/video/{FRAME_ID}.jpg",
        headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") is None


def test_media_range_not_gzipped(lvdata_root):
    """206 分片响应整体跳过（Range 语义零影响）。"""
    resp = _app_client(lvdata_root).get(
        f"/api/mylife/media/{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg",
        headers={"Range": "bytes=0-4", "Accept-Encoding": "gzip"})
    assert resp.status_code == 206
    assert resp.headers.get("content-encoding") is None
