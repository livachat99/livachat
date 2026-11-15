"""Agent 会议：MeetingStore + MCP tools + REST（docs/livachat_meeting.md）。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import make_sample
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.meetings import (
    MeetingError,
    MeetingStore,
    generate_meeting_number,
    normalize_meeting_number,
)
from lvdata_mcp.timeutil import day_bounds_ms, try_resolve_timezone

TZ = "Asia/Shanghai"
FUTURE_START = "2030-01-01T10:00:00+08:00"
FUTURE_END = "2030-01-01T11:00:00+08:00"


def _cfg(root: Path) -> LvdataMcpConfig:
    return LvdataMcpConfig(data={"root_dir": str(root)}, watcher={"timezone": TZ})


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    return root


async def _mcp_call(base: str, tool: str, args: dict):
    async with (
        streamablehttp_client(base + "/mcp") as (read, write, _),
        ClientSession(read, write) as session,
    ):
        return await session.call_tool(tool, args)


def call(base: str, tool: str, args: dict):
    return asyncio.run(_mcp_call(base, tool, args))


def content(result) -> dict:
    for c in result.content:
        if c.type == "text":
            return json.loads(c.text)
    return {}


# ── MeetingStore ────────────────────────────────────────────────────────────

def test_normalize_and_generate_number():
    assert normalize_meeting_number("1234-5678") == "12345678"
    assert normalize_meeting_number(" 1234 5678 ") == "12345678"
    assert normalize_meeting_number("１２３４５６７８") == "12345678"
    assert len(generate_meeting_number()) == 8
    # Arabic-Indic digits are Unicode decimal digits but not valid meeting numbers.
    for bad in ("1234567", "123456789", "abcdefgh", "1234 5678 9", "١٢٣٤٥٦٧٨"):
        with pytest.raises(MeetingError) as exc:
            normalize_meeting_number(bad)
        assert exc.value.code == "invalid_param"


def test_create_get_list_delete(tmp_path):
    store = MeetingStore(_cfg(_root(tmp_path)))
    meeting = store.create(topic="产品评审", start_time=FUTURE_START,
                           end_time=FUTURE_END, timezone=TZ)
    assert len(meeting.meeting_number) == 8
    assert meeting.topic == "产品评审"
    assert meeting.timezone == TZ
    assert store.get(meeting.meeting_number) == meeting

    rows, total = store.list(start_ms=None, end_ms=None,
                             include_past=False, limit=50)
    assert total == 1 and rows == [meeting]

    start_ms, end_ms = day_bounds_ms("2030-01-01", TZ)
    rows, total = store.list(start_ms=start_ms, end_ms=end_ms,
                             include_past=False, limit=50)
    assert total == 1 and rows[0].meeting_number == meeting.meeting_number

    deleted = store.delete(meeting.meeting_number)
    assert deleted == meeting
    assert store.get(meeting.meeting_number) is None
    assert store.delete(meeting.meeting_number) is None


def test_include_past_and_default_upcoming(tmp_path, monkeypatch):
    import lvdata_mcp.meetings as meetings_mod

    store = MeetingStore(_cfg(_root(tmp_path)))
    meeting = store.create(topic="历史会议", start_time=FUTURE_START,
                           timezone=TZ)
    # 让“现在”越过会议结束时间：默认不再返回，include_past 才返回
    monkeypatch.setattr(meetings_mod, "now_ms", lambda: meeting.end_at_ms + 1)
    rows, total = store.list(start_ms=None, end_ms=None,
                             include_past=False, limit=50)
    assert total == 0 and rows == []
    rows, total = store.list(start_ms=None, end_ms=None,
                             include_past=True, limit=50)
    assert total == 1 and rows[0].meeting_number == meeting.meeting_number


def test_default_end_and_naive_timezone(tmp_path):
    store = MeetingStore(_cfg(_root(tmp_path)))
    meeting = store.create(topic="默认时长", start_time="2030-01-01T10:00:00",
                           timezone=TZ)
    assert meeting.end_at_ms - meeting.start_at_ms == 60 * 60 * 1000
    assert meeting.start_at_ms > 0


def test_list_ordering(tmp_path):
    store = MeetingStore(_cfg(_root(tmp_path)))
    first = store.create(topic="first", start_time="2030-01-01T10:00:00+08:00",
                         timezone=TZ)
    second = store.create(topic="second", start_time="2030-01-02T10:00:00+08:00",
                          timezone=TZ)
    rows, total = store.list(start_ms=None, end_ms=None,
                             include_past=False, limit=50)
    assert total == 2
    assert [m.meeting_number for m in rows] == [first.meeting_number, second.meeting_number]
    rows, _ = store.list(start_ms=None, end_ms=None,
                         include_past=True, limit=50)
    assert [m.meeting_number for m in rows] == [second.meeting_number, first.meeting_number]


def test_create_validation(tmp_path):
    store = MeetingStore(_cfg(_root(tmp_path)))
    cases = [
        # naive 且没有 timezone
        {"topic": "x", "start_time": "2030-01-01T10:00:00"},
        # end <= start
        {"topic": "x", "start_time": FUTURE_START, "end_time": FUTURE_START},
        # > 24h
        {"topic": "x", "start_time": FUTURE_START,
         "end_time": "2030-01-03T10:00:00+08:00"},
        # 已结束
        {"topic": "x", "start_time": "2020-01-01T10:00:00+08:00",
         "end_time": "2020-01-01T11:00:00+08:00"},
        # 控制字符
        {"topic": "bad\x00topic", "start_time": FUTURE_START, "timezone": TZ},
    ]
    for kwargs in cases:
        with pytest.raises(MeetingError) as exc:
            store.create(**kwargs)
        assert exc.value.code == "invalid_param", kwargs


def test_collision_retry(tmp_path, monkeypatch):
    import lvdata_mcp.meetings as meetings_mod

    store = MeetingStore(_cfg(_root(tmp_path)))
    first = store.create(topic="first", start_time=FUTURE_START, timezone=TZ)
    numbers = iter([first.meeting_number, "87654321"])
    monkeypatch.setattr(meetings_mod, "generate_meeting_number", lambda: next(numbers))
    second = store.create(topic="second", start_time=FUTURE_START, timezone=TZ)
    assert second.meeting_number == "87654321"


def test_database_not_initialized(tmp_path):
    store = MeetingStore(_cfg(tmp_path / "missing"))
    for op in (lambda: store.get("12345678"),
               lambda: store.create(topic="x", start_time=FUTURE_START, timezone=TZ)):
        with pytest.raises(MeetingError) as exc:
            op()
        assert exc.value.code == "database_not_initialized"


def test_existing_db_without_meetings_table(tmp_path):
    root = _root(tmp_path)
    with sqlite3.connect(root / "lvdata_inventory.db") as conn:
        conn.execute("DROP TABLE meetings")
        conn.commit()
    store = MeetingStore(_cfg(root))
    for op in (lambda: store.get("12345678"),
               lambda: store.list(start_ms=None, end_ms=None,
                                  include_past=False, limit=50),
               lambda: store.create(topic="x", start_time=FUTURE_START, timezone=TZ),
               lambda: store.delete("12345678")):
        with pytest.raises(MeetingError) as exc:
            op()
        assert exc.value.code == "database_not_initialized"


def test_overflow_timezone_and_surrogate_topic(tmp_path):
    assert try_resolve_timezone("+1e309") is None
    store = MeetingStore(_cfg(_root(tmp_path)))
    with pytest.raises(MeetingError) as exc:
        store.create(topic="x", start_time=FUTURE_START, timezone="+1e309")
    assert exc.value.code == "invalid_param"
    with pytest.raises(MeetingError) as exc:
        store.create(topic="bad\ud800", start_time=FUTURE_START, timezone=TZ)
    assert exc.value.code == "invalid_param"


# ── MCP tools ───────────────────────────────────────────────────────────────

def test_mcp_create_get_remove(env):
    base, _ = env
    created = content(call(base, "create_meeting", {
        "topic": "产品评审",
        "start_time": FUTURE_START,
        "end_time": FUTURE_END,
        "timezone": TZ,
    }))
    meeting = created["meeting"]
    number = meeting["meeting_number"]
    assert len(number) == 8
    assert meeting["start_time"] == "2030-01-01T10:00:00+08:00"
    assert meeting["end_time"] == "2030-01-01T11:00:00+08:00"
    assert "join_url" not in meeting

    listed = content(call(base, "get_meetings", {
        "start_date": "2030-01-01", "end_date": "2030-01-01", "timezone": TZ,
    }))
    assert listed["total"] == 1
    assert listed["meetings"][0]["meeting_number"] == number
    assert "join_url" not in listed["meetings"][0]

    removed = content(call(base, "remove_meeting", {"meeting_number": number}))
    assert removed["removed"] is True
    assert removed["meeting"]["meeting_number"] == number

    missing = content(call(base, "remove_meeting", {"meeting_number": number}))
    assert missing["error"]["code"] == "not_found"

    listed_after = content(call(base, "get_meetings", {
        "start_date": "2030-01-01", "end_date": "2030-01-01", "timezone": TZ,
    }))
    assert listed_after["total"] == 0


def test_mcp_invalid_timezone_falls_back(env):
    base, _ = env
    result = content(call(base, "get_meetings", {
        "start_date": "2040-01-01", "end_date": "2040-01-01",
        "timezone": "+999999999999999999:00",
    }))
    assert "error" not in result
    assert result["total"] == 0


# ── REST ────────────────────────────────────────────────────────────────────

def _create_for_rest(root: Path, cfg: LvdataMcpConfig) -> str:
    return MeetingStore(cfg).create(
        topic="REST 会议", start_time=FUTURE_START, timezone=TZ
    ).meeting_number


def test_rest_get_meeting(env):
    base, _ = env
    number = content(call(base, "create_meeting", {
        "topic": "REST 会议", "start_time": FUTURE_START, "timezone": TZ,
    }))["meeting"]["meeting_number"]
    try:
        with urllib.request.urlopen(f"{base}/api/meetings/{number}") as resp:
            assert resp.status == 200
            assert resp.headers["Cache-Control"] == "no-store"
            body = json.loads(resp.read())
        assert body["meeting"]["meeting_number"] == number
        assert body["meeting"]["start_at_ms"] > 0

        for bad, code in (("abc", 400), ("99999999", 404)):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"{base}/api/meetings/{bad}")
            assert exc.value.code == code
    finally:
        content(call(base, "remove_meeting", {"meeting_number": number}))


def test_rest_bearer_and_to_thread(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    import lvdata_mcp.server as server_mod
    from lvdata_mcp.server import create_app

    root = _root(tmp_path)
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": TZ},
        server={"host": "127.0.0.1", "port": 0, "token": "tok"},
    )
    number = _create_for_rest(root, cfg)
    app = create_app(cfg)

    with TestClient(app) as client:
        assert client.get(f"/api/meetings/{number}").status_code == 401
        ok = client.get(f"/api/meetings/{number}",
                        headers={"Authorization": "Bearer tok"})
        assert ok.status_code == 200
        assert ok.headers["cache-control"] == "no-store"

    calls: list = []
    real = asyncio.to_thread

    def spy(func, *args, **kwargs):
        calls.append(func)
        return real(func, *args, **kwargs)

    monkeypatch.setattr(server_mod.asyncio, "to_thread", spy)
    # StreamableHTTPSessionManager 只能 run 一次；spy 阶段用新的 app 实例。
    app2 = create_app(cfg)
    with TestClient(app2) as client:
        assert client.get(f"/api/meetings/{number}",
                          headers={"Authorization": "Bearer tok"}).status_code == 200
    picked = [f for f in calls if getattr(f, "__name__", "") == "get"]
    assert picked
    assert picked[0].__self__.__class__.__name__ == "MeetingStore"
