"""/webroot 静态托管装配与行为（docs/webroot_feature.md §3.1/§6）。"""

from __future__ import annotations

from pathlib import Path

import pytest

# 需求示例目录名（uuidv7），仅作路径字符串使用
UUID = "01a0f54b-4fe4-7c4f-80a2-88e711556d69"


def _client(tmp_path: Path, **webroot):
    from fastapi.testclient import TestClient

    from app.application import create_app
    from shared.config.models import AppConfig

    app = create_app(config=AppConfig(webroot=webroot))
    return TestClient(app), app


def _make_page(root: Path) -> None:
    page = root / UUID
    page.mkdir(parents=True, exist_ok=True)
    (page / "index.html").write_text("<h1>agent page</h1>", encoding="utf-8")


def test_enabled_serves_index_and_dir_request(tmp_path):
    root = tmp_path / "webroot"
    _make_page(root)
    client, _ = _client(tmp_path, enabled=True, root=str(root))

    resp = client.get(f"/webroot/{UUID}/index.html")
    assert resp.status_code == 200
    assert "agent page" in resp.text

    # html=True：目录请求（带尾斜杠）直接服务 index.html
    assert client.get(f"/webroot/{UUID}/").status_code == 200


def test_dir_without_trailing_slash_redirects(tmp_path):
    """Starlette html 模式：无尾斜杠且 index.html 存在 → 307 到带尾斜杠（WebView 跟随）。"""
    root = tmp_path / "webroot"
    _make_page(root)
    client, _ = _client(tmp_path, enabled=True, root=str(root))

    resp = client.get(f"/webroot/{UUID}", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].endswith(f"/webroot/{UUID}/")


def test_missing_path_404(tmp_path):
    root = tmp_path / "webroot"
    _make_page(root)
    client, _ = _client(tmp_path, enabled=True, root=str(root))
    assert client.get(f"/webroot/{UUID}/nope.js").status_code == 404


def test_missing_dir_created_on_startup(tmp_path):
    root = tmp_path / "webroot"
    assert not root.exists()
    _client(tmp_path, enabled=True, root=str(root))
    assert root.is_dir()


def test_root_empty_falls_back_to_recording_base_dir(tmp_path):
    from app.application import resolve_webroot_dir
    from shared.config.models import AppConfig

    cfg = AppConfig(recording={"base_dir": str(tmp_path)}, webroot={"enabled": True})
    assert resolve_webroot_dir(cfg) == str(tmp_path / "webroot")
    # root 显式配置优先
    cfg2 = AppConfig(webroot={"enabled": True, "root": str(tmp_path / "custom")})
    assert resolve_webroot_dir(cfg2) == str(tmp_path / "custom")


def test_disabled_default_no_mount_no_fs_side_effect(tmp_path, monkeypatch):
    """默认配置（AppConfig()）不挂载；测试装配不得对真实数据根产生 FS 副作用。"""
    from app.application import create_app
    from shared.config.models import AppConfig

    touched: list[str] = []
    orig_mkdir = Path.mkdir

    def spy(self, *args, **kwargs):
        touched.append(str(self))
        return orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", spy)
    app = create_app(config=AppConfig())

    assert not any("webroot" in p for p in touched)
    assert not any(getattr(r, "path", None) == "/webroot" for r in app.routes)

    from fastapi.testclient import TestClient

    assert TestClient(app).get(f"/webroot/{UUID}/").status_code == 404


def test_missing_parent_fails_fast(tmp_path):
    """父目录缺失 → 启动 fail-fast（§4 D2，install 契约），错误信息带目录上下文。"""
    from app.application import create_app
    from shared.config.models import AppConfig

    root = tmp_path / "missing-parent" / "webroot"
    with pytest.raises(RuntimeError, match="webroot dir not creatable"):
        create_app(config=AppConfig(webroot={"enabled": True, "root": str(root)}))


def test_traversal_blocked(tmp_path):
    """路径穿越：%2e%2e 编码直达服务端（裸 `..` 会被 httpx 客户端规范化）。"""
    root = tmp_path / "webroot"
    _make_page(root)
    (tmp_path / "secret.txt").write_text("top-secret", encoding="utf-8")
    client, _ = _client(tmp_path, enabled=True, root=str(root))

    resp = client.get("/webroot/%2e%2e/secret.txt")
    assert resp.status_code == 404
    assert "top-secret" not in resp.text
