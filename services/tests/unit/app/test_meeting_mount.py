"""/meeting 静态托管（docs/livachat_meeting.md §6.8）。"""

from __future__ import annotations

from pathlib import Path

import pytest


def _client(tmp_path: Path, **meeting):
    from fastapi.testclient import TestClient

    from app.application import create_app
    from shared.config.models import AppConfig

    app = create_app(config=AppConfig(meeting=meeting))
    return TestClient(app), app


def _make_dist(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<h1>meeting</h1>", encoding="utf-8")
    (root / "config.json").write_text('{"tokenMode":"server"}', encoding="utf-8")


def test_enabled_serves_index_and_config(tmp_path):
    root = tmp_path / "dist"
    _make_dist(root)
    client, _ = _client(tmp_path, enabled=True, web_dir=str(root))
    assert client.get("/meeting/").status_code == 200
    assert "meeting" in client.get("/meeting/").text
    assert client.get("/meeting/config.json").json()["tokenMode"] == "server"
    assert client.get("/meeting/nope.js").status_code == 404

    redirect = client.get("/meeting", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"].endswith("/meeting/")


def test_spa_fallback_for_meeting_paths(tmp_path):
    root = tmp_path / "dist"
    _make_dist(root)
    client, _ = _client(tmp_path, enabled=True, web_dir=str(root))
    headers = {"Accept": "text/html,application/xhtml+xml"}

    for path in ("/meeting/34479999", "/meeting/abcdefgh",
                 "/meeting/34479999/", "/meeting/34479999/extra"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, path
        assert "meeting" in resp.text

    # Navigation fallback must not swallow real files or asset-like paths.
    assert client.get("/meeting/config.json", headers=headers).json()["tokenMode"] == "server"
    assert client.get("/meeting/assets/missing.js", headers=headers).status_code == 404
    assert client.get("/meeting/vendor/missing", headers=headers).status_code == 404
    # Non-navigation requests keep the normal 404.
    assert client.get("/meeting/34479999",
                      headers={"Accept": "application/json"}).status_code == 404
    assert client.head("/meeting/34479999", headers=headers).status_code == 200


def test_disabled_by_default(tmp_path):
    client, app = _client(tmp_path)
    assert client.get("/meeting/").status_code == 404
    assert not any(getattr(r, "path", None) == "/meeting" for r in app.routes)


def test_empty_web_dir_fails_fast(tmp_path):
    from app.application import create_app
    from shared.config.models import AppConfig

    with pytest.raises(RuntimeError, match="web_dir"):
        create_app(config=AppConfig(meeting={"enabled": True, "web_dir": ""}))


def test_missing_index_fails_fast(tmp_path):
    from app.application import create_app
    from shared.config.models import AppConfig

    root = tmp_path / "dist"
    root.mkdir()
    with pytest.raises(RuntimeError, match="index.html"):
        create_app(config=AppConfig(meeting={"enabled": True, "web_dir": str(root)}))
