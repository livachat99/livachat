"""BFF → lvdata REST get_meeting 客户端（docs/livachat_meeting.md §6.7）。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from bff.common.lvdata_api import get_meeting
from bff.common.store import BffError


def _run(transport, *, base="http://lvdata", number="12345678"):
    return asyncio.run(get_meeting(base, "secret-token", 5.0, number,
                                   transport=transport))


def test_wire_format_and_success():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"meeting": {
            "meeting_number": "12345678",
            "topic": "产品评审",
            "start_at_ms": 1,
            "end_at_ms": 2,
            "timezone": "Asia/Shanghai",
        }})

    meeting = _run(httpx.MockTransport(handler))
    assert seen["url"].endswith("/api/meetings/12345678")
    assert seen["auth"] == "Bearer secret-token"
    assert meeting["meeting_number"] == "12345678"


@pytest.mark.parametrize("status,code", [(404, "meeting_not_found"),
                                          (400, "bad_request"),
                                          (500, "upstream_unavailable")])
def test_error_mapping(status, code):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(BffError) as exc:
        _run(transport)
    assert exc.value.code == code


def test_connect_error():
    def handler(_request):
        raise httpx.ConnectError("boom", request=None)

    with pytest.raises(BffError) as exc:
        _run(httpx.MockTransport(handler))
    assert exc.value.code == "upstream_unavailable"


def test_bad_json_and_missing_meeting():
    for response in (httpx.Response(200, text="not-json"),
                     httpx.Response(200, json={"ok": True})):
        with pytest.raises(BffError) as exc:
            _run(httpx.MockTransport(lambda _, r=response: r))
        assert exc.value.code == "upstream_unavailable"


def test_empty_base_url_is_disabled():
    with pytest.raises(BffError) as exc:
        _run(None, base="")
    assert exc.value.code == "meeting_disabled"
