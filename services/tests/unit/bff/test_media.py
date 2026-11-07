from __future__ import annotations

from tests.unit.bff.conftest import AUDIO_SEG, DEVICE_A, FRAME_ID, SESS_A


def _media(client, path: str, **params) -> tuple[int, dict, bytes]:
    resp = client.get(f"/api/mylife/media/{path}", params=params or None)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body, resp.content


def test_media_audio_ok(client):
    code, body, content = _media(client, f"{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg")
    assert code == 200
    assert content == b"OggS fake audio"
    assert body == {}


def test_media_audio_cache_header(client):
    resp = client.get(f"/api/mylife/media/{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg")
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert resp.headers["content-type"] == "audio/ogg"


def test_media_video_ok(client):
    code, _, content = _media(client, f"{DEVICE_A}/{SESS_A}/video/{FRAME_ID}.jpg")
    assert code == 200
    assert content == b"fake jpeg"


def test_media_range_request(client):
    resp = client.get(
        f"/api/mylife/media/{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg",
        headers={"Range": "bytes=0-4"},
    )
    assert resp.status_code == 206
    assert resp.content == b"OggS "
    assert resp.headers["content-range"] == "bytes 0-4/15"
    assert resp.headers["accept-ranges"] == "bytes"


def test_media_path_traversal(client):
    cases = [
        (f"{DEVICE_A}/{SESS_A}/audio/../{AUDIO_SEG}.ogg", 400),
        (f"{DEVICE_A}/{SESS_A}/audio/..%2F{AUDIO_SEG}.ogg", 400),
        (f"../../{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg", 400),
        (f"{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg/..", 400),
        (f"{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.txt", 400),
        (f"{DEVICE_A}/{SESS_A}/audio/not-a-uuid.ogg", 400),
        (f"not-a-uuid/{SESS_A}/audio/{AUDIO_SEG}.ogg", 400),
        (f"{DEVICE_A}/not-a-uuid/audio/{AUDIO_SEG}.ogg", 400),
        (f"{DEVICE_A}/{SESS_A}/bogus/{AUDIO_SEG}.ogg", 400),
        (f"{DEVICE_A}/{SESS_A}/audio/nonexistent.ogg", 404),
    ]
    for path, expected in cases:
        code, body, _ = _media(client, path)
        # 穿越必须被拒绝：starlette 路径规范化后可能 404，参数校验失败 400 —— 均安全
        assert code in (400, 404), f"{path} → {code} (expected 400/404)"
        if code == 400:
            assert body["error"]["code"] == "bad_request", path


def test_media_query_string_escape(client):
    code, _body, _ = _media(client, f"{DEVICE_A}/{SESS_A}/audio/%2e%2e/{AUDIO_SEG}.ogg")
    assert code in (400, 404)


def test_media_missing_file_404(client):
    code, body, _ = _media(client, f"{DEVICE_A}/{SESS_A}/video/{AUDIO_SEG}.jpg")
    assert code == 404
    assert body["error"]["code"] == "not_found"


def test_media_device_whitelist(make_client):
    """device_uuids 白名单对 media 入口生效：白名单外设备一律 404（§5.5）。"""
    client = make_client(device_uuids=[DEVICE_A])

    code, _, content = _media(client, f"{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg")
    assert code == 200
    assert content == b"OggS fake audio"

    other = "99999999-9999-4999-8999-999999999999"
    code, body, _ = _media(client, f"{other}/{SESS_A}/audio/{AUDIO_SEG}.ogg")
    assert code == 404
    assert body["error"]["code"] == "not_found"


def test_media_m4a_inline_disposition(client, tmp_path, monkeypatch):
    from bff.common import media as media_mod

    fake = tmp_path / "out.m4a"
    fake.write_bytes(b"fake m4a")
    monkeypatch.setattr(media_mod, "_transcode_m4a", lambda store, src, filename: fake)

    resp = client.get(f"/api/mylife/media/{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg", params={"format": "m4a"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mp4"
    assert resp.content == b"fake m4a"
    # 不传 filename → 无 Content-Disposition: attachment（<audio> 内嵌播放）
    assert "attachment" not in resp.headers.get("content-disposition", "")
