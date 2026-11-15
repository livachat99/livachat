"""POST /api/mylife/join_meeting（docs/livachat_meeting.md §6.2-§6.5）。"""

from __future__ import annotations

import time

import jwt
import pytest

from bff.common.store import BffError
from shared.config.models import MeetingConfig

KEY = "test-key"
SECRET = "test-secret-0123456789abcdef0123456789"
NOW_S = 1_800_000_000
NOW_MS = NOW_S * 1000
MIN = 60 * 1000
HOUR = 60 * MIN


def _client(make_client, tmp_path, *, enabled=True, rate=1000):
    client = make_client(lvdata_api_url="http://upstream")
    client.app.state.config.meeting = MeetingConfig(
        enabled=enabled,
        external_server_addr="https://chat.example.test:8080",
        livekit_key=KEY,
        livekit_secret=SECRET,
        livekit_server="wss://livekit.example.test:7880",
        web_dir=str(tmp_path),
        token_ttl_s=600,
        join_early_s=1800,
        expire_after_end_s=14400,
        join_rate_limit_per_min=rate,
    )
    return client


def _meeting(number="12345678", *, start_ms=NOW_MS + 10 * MIN, end_ms=None):
    return {
        "meeting_number": number,
        "topic": "产品评审",
        "start_at_ms": start_ms,
        "end_at_ms": end_ms if end_ms is not None else start_ms + HOUR,
        "timezone": "Asia/Shanghai",
    }


def _install(monkeypatch, meeting, *, now_s=NOW_S):
    async def fake(*args, **kwargs):
        if isinstance(meeting, Exception):
            raise meeting
        return meeting

    class _Clock:
        @staticmethod
        def time():
            return now_s

    monkeypatch.setattr("bff.meeting.service.get_meeting", fake)
    monkeypatch.setattr("bff.meeting.service.time", _Clock)


def test_public_join_returns_livekit_details(make_client, tmp_path, monkeypatch):
    _install(monkeypatch, _meeting())
    client = _client(make_client, tmp_path)
    resp = client.post("/api/mylife/join_meeting",
                       json={"meeting_number": "1234-5678",
                             "participant_name": " Alice "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["serverUrl"] == "wss://livekit.example.test:7880"
    assert body["roomName"] == "12345678"
    assert body["participantName"] == "Alice"
    assert body["participantToken"]
    assert resp.headers["cache-control"] == "no-store"
    assert "topic" not in body and SECRET not in resp.text

    claims = jwt.decode(body["participantToken"], SECRET, algorithms=["HS256"])
    assert claims["iss"] == KEY
    assert claims["sub"].startswith("guest_")
    assert claims["name"] == "Alice"
    assert claims["video"]["room"] == "12345678"
    assert claims["video"]["roomJoin"] is True
    assert claims["video"]["canPublish"] is True
    assert claims["video"]["canPublishSources"] == [
        "camera", "microphone", "screen_share", "screen_share_audio",
    ]
    assert claims["video"]["canSubscribe"] is True
    assert claims["exp"] > time.time()


def test_route_is_registered(make_client, tmp_path):
    client = _client(make_client, tmp_path)
    assert "/api/mylife/join_meeting" in client.app.openapi()["paths"]


@pytest.mark.parametrize("start_ms,now_s,status,detail", [
    (NOW_MS + 31 * MIN, NOW_S, 403, "opens_at_ms"),
    (NOW_MS + 30 * MIN, NOW_S, 200, None),
    (NOW_MS - 5 * HOUR - 1, NOW_S, 410, "expires_at_ms"),
    (NOW_MS - 5 * HOUR, NOW_S, 200, None),
])
def test_window_boundaries(make_client, tmp_path, monkeypatch,
                           start_ms, now_s, status, detail):
    end_ms = start_ms + HOUR
    _install(monkeypatch, _meeting(start_ms=start_ms, end_ms=end_ms), now_s=now_s)
    client = _client(make_client, tmp_path)
    resp = client.post("/api/mylife/join_meeting",
                       json={"meeting_number": "12345678", "participant_name": "A"})
    assert resp.status_code == status
    if detail:
        assert detail in resp.json()["error"]


@pytest.mark.parametrize("exc,status,code", [
    (BffError("meeting_not_found", "missing"), 404, "meeting_not_found"),
    (BffError("bad_request", "bad"), 400, "bad_request"),
    (BffError("upstream_unavailable", "down"), 502, "upstream_unavailable"),
])
def test_upstream_errors(make_client, tmp_path, monkeypatch, exc, status, code):
    _install(monkeypatch, exc)
    client = _client(make_client, tmp_path)
    resp = client.post("/api/mylife/join_meeting",
                       json={"meeting_number": "12345678", "participant_name": "A"})
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


def test_disabled_and_bad_requests(make_client, tmp_path, monkeypatch):
    _install(monkeypatch, _meeting())
    client = _client(make_client, tmp_path, enabled=False)
    resp = client.post("/api/mylife/join_meeting",
                       json={"meeting_number": "12345678", "participant_name": "A"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "meeting_disabled"

    enabled = _client(make_client, tmp_path)
    for body in ({}, {"meeting_number": "abc", "participant_name": "A"},
                 # Arabic-Indic digits are Unicode decimal digits but not a meeting number.
                 {"meeting_number": "١٢٣٤٥٦٧٨", "participant_name": "A"},
                 {"meeting_number": "12345678", "participant_name": ""},
                 {"meeting_number": "12345678", "participant_name": "A\nB"}):
        assert enabled.post("/api/mylife/join_meeting", json=body).status_code == 400


def test_mismatched_upstream_number_is_rejected(make_client, tmp_path, monkeypatch):
    _install(monkeypatch, _meeting(number="87654321"))
    client = _client(make_client, tmp_path)
    resp = client.post("/api/mylife/join_meeting",
                       json={"meeting_number": "12345678", "participant_name": "A"})
    assert resp.status_code == 502


def test_rate_limit(make_client, tmp_path, monkeypatch):
    _install(monkeypatch, _meeting())
    client = _client(make_client, tmp_path, rate=1)
    body = {"meeting_number": "12345678", "participant_name": "A"}
    assert client.post("/api/mylife/join_meeting", json=body).status_code == 200
    resp = client.post("/api/mylife/join_meeting", json=body)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"


def test_malformed_requests_consume_rate_limit(make_client, tmp_path, monkeypatch):
    """Malformed bodies must not bypass the limiter (public endpoint DoS guard)."""
    _install(monkeypatch, _meeting())
    client = _client(make_client, tmp_path, rate=1)
    bad = client.post("/api/mylife/join_meeting",
                      json={"meeting_number": "abc", "participant_name": "A"})
    assert bad.status_code == 400
    resp = client.post("/api/mylife/join_meeting",
                       json={"meeting_number": "12345678", "participant_name": "A"})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"


def test_oversized_body_rejected(make_client, tmp_path, monkeypatch):
    _install(monkeypatch, _meeting())
    client = _client(make_client, tmp_path, rate=100)
    resp = client.post(
        "/api/mylife/join_meeting",
        json={"meeting_number": "12345678", "participant_name": "x" * (16 * 1024)},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"
