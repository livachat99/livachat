"""BFF /events/remove 接口（docs/bff_service_remove.md §6.2）。

口径：请求级唯一硬错误 = 格式非法（400）；ref 级失败逐条 skip（部分成功，D7）；
全 skip 不调执行面；BFF 不写任何 DB/文件（删除执行全在 lvdata-mcp）。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import httpx
import pytest

from bff.common import lvdata_api
from bff.common.store import BffError
from tests.unit.bff.conftest import DEVICE_A, FRAME_ID, SESS_A, TS_A1

VIDEO_REF = f"{SESS_A}:video:{TS_A1 + 2000}:0"
WINDOW_REF = f"{SESS_A}:window:{TS_A1 + 3000}:0"
CHAT_REF = f"{SESS_A}:chat:{TS_A1}:0"
GHOST_REF = "ghost:video:no-such-frame"


# ── 1. 请求格式：唯一整批拒绝条件（400） ────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"event_refs": []},                 # 空
    {"event_refs": ["x"] * 51},         # 超 50
    {"event_refs": [123]},              # 元素非字符串（strict）
    {"event_refs": ["  "]},             # 元素为空白
    {},                                 # 缺字段
])
def test_request_format_400(client, body):
    resp = client.post("/api/mylife/events/remove", json=body)
    assert resp.status_code == 400, body
    assert resp.json()["error"]["code"] == "bad_request"


def test_request_format_non_object_400(client):
    assert client.post("/api/mylife/events/remove", json=[]).status_code == 400
    resp = client.post("/api/mylife/events/remove", content=b"not-json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 400


# ── 2. 可见性：不存在/不可见合并 not_found（不泄漏） ────────────────────────

def test_invisible_device_skipped_not_found(make_client):
    c = make_client(device_uuids=["99999999-9999-4999-8999-999999999999"])
    resp = c.post("/api/mylife/events/remove", json={"event_refs": [VIDEO_REF]})
    assert resp.status_code == 200
    assert resp.json()["skipped"] == [{"event_ref": VIDEO_REF,
                                       "reason": "not_found"}]
    assert resp.json()["removed"] == []


def test_device_id_mismatch_skipped_not_found(client):
    resp = client.post(
        "/api/mylife/events/remove?device_id=99999999-9999-4999-8999-999999999999",
        json={"event_refs": [VIDEO_REF]})
    assert resp.status_code == 200
    assert resp.json()["skipped"] == [{"event_ref": VIDEO_REF,
                                       "reason": "not_found"}]


def test_missing_ref_skipped_not_found(client):
    resp = client.post("/api/mylife/events/remove", json={"event_refs": [GHOST_REF]})
    assert resp.json()["skipped"] == [{"event_ref": GHOST_REF,
                                       "reason": "not_found"}]


# ── 3. 类型：非 video 逐条 skip，不整批拒绝 ─────────────────────────────────

def test_non_video_skipped(client):
    resp = client.post("/api/mylife/events/remove", json={"event_refs": [CHAT_REF]})
    assert resp.status_code == 200
    assert resp.json()["skipped"] == [{"event_ref": CHAT_REF,
                                       "reason": "not_video"}]
    assert resp.json()["removed"] == []


# ── 4. 部分成功：只转发可见 video 子集 + 合并双方 skipped ───────────────────

def test_partial_forward_merge_and_passthrough(make_client, monkeypatch):
    calls: dict = {}

    async def fake(base_url, token, timeout_s, refs, **kw):
        calls.update(base_url=base_url, refs=refs)
        return {"removed": [VIDEO_REF], "window_events_removed": [WINDOW_REF],
                "images_removed": [FRAME_ID], "images_pending": [],
                "days_recomputed": [{"timezone": "Asia/Shanghai",
                                     "local_date": "2026-08-18"}],
                "skipped": [{"event_ref": "race:video:x", "reason": "not_found"}]}

    monkeypatch.setattr("bff.common.lvdata_api.remove_events", fake)
    c = make_client(lvdata_api_url="http://upstream")
    resp = c.post("/api/mylife/events/remove",
                  json={"event_refs": [VIDEO_REF, GHOST_REF, CHAT_REF]})

    assert resp.status_code == 200
    assert calls["base_url"] == "http://upstream"
    assert calls["refs"] == [VIDEO_REF]          # 只转发「可见 video」子集
    body = resp.json()
    assert body["schema_version"] == 1
    assert body["removed"] == [VIDEO_REF]
    assert body["window_events_removed"] == [WINDOW_REF]
    assert body["skipped"] == [
        {"event_ref": GHOST_REF, "reason": "not_found"},      # 本地（请求序）
        {"event_ref": CHAT_REF, "reason": "not_video"},
        {"event_ref": "race:video:x", "reason": "not_found"},  # 上游（竞态）
    ]


def test_duplicate_refs_deduped(make_client, monkeypatch):
    """重复 ref 保序去重：只转发一次，响应无重复条目。"""
    calls: dict = {}

    async def fake(base_url, token, timeout_s, refs, **kw):
        calls["refs"] = refs
        return {"removed": [VIDEO_REF], "window_events_removed": [],
                "images_removed": [], "images_pending": [],
                "days_recomputed": [], "skipped": []}

    monkeypatch.setattr("bff.common.lvdata_api.remove_events", fake)
    c = make_client(lvdata_api_url="http://upstream")
    resp = c.post("/api/mylife/events/remove",
                  json={"event_refs": [VIDEO_REF, VIDEO_REF]})
    assert calls["refs"] == [VIDEO_REF]
    assert resp.json()["removed"] == [VIDEO_REF]
    assert resp.json()["skipped"] == []


def test_all_skipped_no_upstream_call(make_client, monkeypatch):
    called: list = []

    async def spy(*a, **kw):
        called.append(1)
        return {}

    monkeypatch.setattr("bff.common.lvdata_api.remove_events", spy)
    c = make_client(lvdata_api_url="http://upstream")
    resp = c.post("/api/mylife/events/remove", json={"event_refs": [GHOST_REF]})
    assert resp.status_code == 200
    assert resp.json()["removed"] == [] and resp.json()["skipped"]
    assert called == []          # 全 skip：不调执行面（§2.1-3）


# ── 5. lvdata_api 客户端：wire 格式与错误映射 ───────────────────────────────

def test_lvdata_api_wire_format():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"removed": ["r1"]})

    result = asyncio.run(lvdata_api.remove_events(
        "http://up:8090/", "tok", 5.0, ["r1"],
        transport=httpx.MockTransport(handler)))
    assert seen["url"] == "http://up:8090/api/events/remove"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == {"event_refs": ["r1"]}
    assert result == {"removed": ["r1"]}


def test_lvdata_api_error_mapping():
    def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(BffError) as ei:
        asyncio.run(lvdata_api.remove_events(
            "http://up", "t", 5.0, ["r"],
            transport=httpx.MockTransport(connect_error)))
    assert ei.value.code == "upstream_unavailable"

    with pytest.raises(BffError) as ei:
        asyncio.run(lvdata_api.remove_events(
            "http://up", "t", 5.0, ["r"],
            transport=httpx.MockTransport(lambda req: httpx.Response(500))))
    assert ei.value.code == "upstream_unavailable"

    with pytest.raises(BffError) as ei:
        asyncio.run(lvdata_api.remove_events(
            "http://up", "t", 5.0, ["r"],
            transport=httpx.MockTransport(lambda req: httpx.Response(
                400, json={"error": {"code": "invalid_param",
                                     "message": "boom"}}))))
    assert ei.value.code == "bad_request" and "boom" in ei.value.message

    with pytest.raises(BffError) as ei:      # url 未配置：发请求前即拒
        asyncio.run(lvdata_api.remove_events("", "t", 5.0, ["r"]))
    assert ei.value.code == "removal_disabled"


# ── 6. 路由层状态码映射 ─────────────────────────────────────────────────────

def test_disabled_503_without_url(client):
    resp = client.post("/api/mylife/events/remove", json={"event_refs": [VIDEO_REF]})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "removal_disabled"


def test_upstream_unavailable_502(make_client, monkeypatch):
    async def boom(*a, **kw):
        raise BffError("upstream_unavailable", "lvdata-mcp 请求失败")

    monkeypatch.setattr("bff.common.lvdata_api.remove_events", boom)
    c = make_client(lvdata_api_url="http://upstream")
    resp = c.post("/api/mylife/events/remove", json={"event_refs": [VIDEO_REF]})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_unavailable"


# ── 7. BFF 不写 DB/文件（只读约束守护） ─────────────────────────────────────

def test_no_db_or_file_mutation(client, lvdata_root, monkeypatch):
    async def fake(*a, **kw):
        return {"removed": [VIDEO_REF], "window_events_removed": [],
                "images_removed": [FRAME_ID], "images_pending": [],
                "days_recomputed": [], "skipped": []}

    monkeypatch.setattr("bff.common.lvdata_api.remove_events", fake)
    db = lvdata_root / "lvdata_inventory.db"

    def counts() -> list[int]:
        with sqlite3.connect(db) as conn:
            return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("events", "files", "day_summary", "search_docs")]

    before = counts()
    resp = client.post("/api/mylife/events/remove", json={"event_refs": [VIDEO_REF]})
    assert resp.status_code == 200 and resp.json()["removed"] == [VIDEO_REF]
    assert counts() == before
    assert (lvdata_root / DEVICE_A / SESS_A / "video" / f"{FRAME_ID}.jpg").exists()
