"""v2 端到端集成测试：真实 uvicorn + streamable HTTP + MCP ClientSession（§12）。"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sqlite3
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from lvdata_mcp import VERSION
from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.index import build_fts_query, is_stopword_query, rebuild_session, search
from lvdata_mcp.server import create_app
from lvdata_mcp.timeutil import day_bounds_ms, ms_to_local, ms_to_local_date

from conftest import (DEVICE, FRAME, SID, TS, CountingEmbeddingProvider,
                      FakeEmbeddingProvider, make_sample, start_server)

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def _call(base: str, tool: str, args: dict):
    async with streamablehttp_client(base + "/mcp") as (read, write, _):
        async with ClientSession(read, write) as s:
            return await s.call_tool(tool, args)


def call(url, tool, args):
    return asyncio.run(_call(url, tool, args))


def _content(result) -> dict:
    """首个 text content → JSON。"""
    for c in result.content:
        if c.type == "text":
            return json.loads(c.text)
    return {}


def _assert_tools(base: str) -> set[str]:
    result = asyncio.run(_tool_list(base))
    return {t.name for t in result.tools}


async def _tool_list(base: str):
    async with streamablehttp_client(base + "/mcp") as (read, write, _):
        async with ClientSession(read, write) as s:
            return await s.list_tools()


def _seed_index(root: Path) -> None:
    """重建 FTS（数据源 = events）。"""
    from lvdata_mcp.config import LvdataMcpConfig
    from lvdata_mcp.index import ensure_index
    from lvdata_mcp.store import DataStore
    import sqlite3
    cfg = LvdataMcpConfig(data={"root_dir": str(root)}, watcher={"timezone": "Asia/Shanghai"})
    store = DataStore(cfg)
    with sqlite3.connect(store.db_path) as conn:
        ensure_index(conn)
        rebuild_session(conn, store, SID)
        conn.commit()


# ── 工具集合（§12：session 维度工具删除）────────────────────────────────

def test_tool_set(env):
    """对外工具集恰为 5 个（运维件已迁 REST、list_jobs 已删除、get_key_image 并入
    get_event，见 docs/data/lvdata_mcp_tools.md）。"""
    url, _ = env
    names = _assert_tools(url)
    assert names == {"search", "semantic_search", "get_timeline", "get_event",
                     "get_recent_events"}


def test_get_timeline(env):
    """按日分组（日升序、日内升序）+ 轻量投影（无 payload/epoch ms）。"""
    url, _ = env
    out = _content(call(url, "get_timeline", {"limit": 50}))
    assert out["timezone"] == "Asia/Shanghai"
    assert out["total"] == 5 and out["next_cursor"] is None
    assert out["date_from"] is None and out["date_to"] is None   # 缺省不限
    assert [d["date"] for d in out["days"]] == [ms_to_local_date(TS, "Asia/Shanghai")]
    events = out["days"][0]["events"]
    # 日内排序 (ts_ms, EVENT_PRIORITY, event_id)：按 ts 升序
    assert [e["type"] for e in events] == ["chat", "audio", "window", "video", "chat"]
    assert events[0]["event_ref"].startswith(f"{SID}:chat:")
    assert set(events[0]) == {"event_ref", "type", "timestamp",
                              "source", "summary"}   # 轻量投影：无 payload、无 ts_ms/ts_utc
    assert events[0]["timestamp"] == ms_to_local(TS, "Asia/Shanghai")


def test_get_timeline_date_window_and_filters(env):
    """日期窗含端点；窗口外的日子不出现。"""
    url, _ = env
    day = ms_to_local_date(TS, "Asia/Shanghai")
    one = _content(call(url, "get_timeline", {"start_date": day, "end_date": day}))
    assert one["total"] == 5 and one["date_from"] == day and one["date_to"] == day
    other = _content(call(url, "get_timeline",
                          {"start_date": "2099-01-01", "end_date": "2099-01-01"}))
    assert other["total"] == 0 and other["days"] == []


def test_get_timeline_pagination(env):
    """游标翻页：total 恒为窗口总数（不随翻页变）；分页可把同一天切成两页。"""
    url, _ = env
    day = ms_to_local_date(TS, "Asia/Shanghai")
    win = {"types": ["audio", "video"], "limit": 1, "start_date": day, "end_date": day}
    p1 = _content(call(url, "get_timeline", win))
    assert p1["total"] == 2 and len(p1["days"][0]["events"]) == 1
    assert p1["next_cursor"]
    p2 = _content(call(url, "get_timeline", {**win, "cursor": p1["next_cursor"]}))
    assert p2["total"] == 2                                    # 翻页不变
    assert p2["days"][0]["events"][0]["event_ref"] != p1["days"][0]["events"][0]["event_ref"]
    assert p2["next_cursor"] is None


def test_get_timeline_bad_dates(env):
    """格式错 / 逆序 → invalid_param。"""
    url, _ = env
    bad = _content(call(url, "get_timeline", {"start_date": "2026/07/24"}))
    assert bad["error"]["code"] == "invalid_param"
    rev = _content(call(url, "get_timeline",
                        {"start_date": "2026-07-25", "end_date": "2026-07-24"}))
    assert rev["error"]["code"] == "invalid_param"


def test_get_timeline_timezone(env):
    """timezone 影响窗口与渲染，输出为真实生效标识；无效 → 回落配置。"""
    url, _ = env
    cst_day = ms_to_local_date(TS, "Asia/Shanghai")
    # 火奴鲁鲁（UTC-10）：该事件落在前一天 → 用 CST 的日期查不到
    hnl = _content(call(url, "get_timeline",
                        {"start_date": cst_day, "end_date": cst_day,
                         "timezone": "Pacific/Honolulu"}))
    assert hnl["timezone"] == "Pacific/Honolulu" and hnl["total"] == 0
    bad = _content(call(url, "get_timeline",
                        {"start_date": cst_day, "end_date": cst_day,
                         "timezone": "bogus/zone"}))
    assert bad["timezone"] == "Asia/Shanghai" and bad["total"] == 5


def test_timeline_page_sql_uses_ts_index_when_windowed():
    """§A 优化：带日期窗的翻页 SQL 强制 `INDEXED BY idx_events_ts`，无窗不加（保护稀有类型）。"""
    from lvdata_mcp.store import _timeline_page_sql

    win = _timeline_page_sql("type IN (?) AND ts_ms >= ?", True)
    assert "INDEXED BY idx_events_ts" in win
    no_win = _timeline_page_sql("type IN (?)", False)
    assert "INDEXED BY" not in no_win
    # 排序键与 LIMIT 形状不变（语义不变量）
    for sql in (win, no_win):
        assert sql.endswith(", event_id LIMIT ?")


def test_timeline_page_plan_has_no_full_sort(tmp_path):
    """§A 优化护栏：带窗（含**游标页**形状）的计划不得出现整段 TEMP B-TREE 排序。

    只允许 `TEMP B-TREE FOR LAST N TERMS`（同 ms 小段）——整段排序意味着回到了
    "对命中类型全部行排序、LIMIT 失效"的老问题。不做计时断言（CI 机器性能波动会 flaky）。
    """
    from lvdata_mcp.consts import PRIORITY_SQL
    from lvdata_mcp.ddl import ensure_schema
    from lvdata_mcp.store import _timeline_page_sql

    db = tmp_path / "plan.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn, default_timezone="Asia/Shanghai", timezones=[])
    conn.execute("INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
                 "source, summary, payload) VALUES ('r:d:1','d','s','chat',1,'files','x','{}')")
    conn.commit()
    cur = (f"(ts_ms > ? OR (ts_ms = ? AND {PRIORITY_SQL} > ?) "
           f"OR (ts_ms = ? AND {PRIORITY_SQL} = ? AND event_id > ?))")
    cases = [  # (where, windowed, params)
        ("type IN (?) AND ts_ms >= ?", True, ("chat", 0, 2)),
        (f"type IN (?) AND ts_ms >= ? AND {cur}", True,
         ("chat", 0, 1, 1, 0, 1, 0, 0, 2)),
    ]
    for where, windowed, params in cases:
        sql = _timeline_page_sql(where, windowed)
        plan = " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
        assert "idx_events_ts" in plan, plan
        assert "TEMP B-TREE FOR ORDER BY" not in plan, plan     # 只允许 LAST N TERMS
    conn.close()


def test_get_timeline_same_ts_pagination(tmp_path):
    """三元组游标 [ts_ms, type, event_id]：同毫秒同类型事件不跳行/不重复（§12）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    from lvdata_mcp.ddl import ensure_schema
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    for i in range(5):
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
            (f"{SID}:chat:same{i}", DEVICE, SID, "chat", TS, "chat_messages",
             f"同刻消息 {i}", "{}"))
    conn.commit()
    conn.close()
    cfg = LvdataMcpConfig(data={"root_dir": str(root)}, watcher={"timezone": "Asia/Shanghai"})
    from lvdata_mcp.store import DataStore
    store = DataStore(cfg)
    all_events = []
    cursor = None
    for _ in range(10):
        page = store.get_timeline(types=["chat"], limit=3, cursor=cursor)
        all_events.extend(e["event_ref"] for d in page["days"] for e in d["events"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert cursor is None
    refs = [e for e in all_events if "same" in e]
    assert len(refs) == 5 and len(set(refs)) == 5  # 不重复不遗漏


def test_get_event_chat(env):
    url, _ = env
    result = call(url, "get_event", {"event_ref": f"{SID}:chat:m1"})
    assert [c.type for c in result.content] == ["text"]   # 非 video → 无 image 段
    out = _content(result)
    assert out["type"] == "chat"
    assert out["detail"]["text"] == "为什么响应这么慢"
    assert "ts_ms" not in out and "session_id" not in out
    assert out["timezone"] == "Asia/Shanghai"
    assert out["timestamp"] == ms_to_local(TS, "Asia/Shanghai")


def test_get_event_timezone_and_fallback(env):
    """timezone 显式生效；无效 → 回落服务配置时区，输出为真实生效标识（§8.7）。"""
    url, _ = env
    ref = f"{SID}:chat:m1"
    utc = _content(call(url, "get_event", {"event_ref": ref, "timezone": "UTC"}))
    assert utc["timezone"] == "UTC"
    assert utc["timestamp"] == ms_to_local(TS, "UTC")
    bad = _content(call(url, "get_event", {"event_ref": ref, "timezone": "bogus/zone"}))
    assert bad["timezone"] == "Asia/Shanghai"            # 回落配置值
    assert bad["timestamp"] == ms_to_local(TS, "Asia/Shanghai")


def test_get_event_video_with_image(env):
    """video + 有图 → [text(JSON), image/jpeg] 两段 content。"""
    url, _ = env
    result = call(url, "get_event", {"event_ref": f"{SID}:video:{FRAME}"})
    assert result.isError is False
    assert [c.type for c in result.content] == ["text", "image"]
    assert result.content[1].mimeType == "image/jpeg"
    out = _content(result)
    assert out["type"] == "video"
    assert out["detail"]["caption"] == "IDE 报错页面"
    assert out["related"] == {"window_id": "w1", "image_available": True}
    assert "ts_ms" not in out and "session_id" not in out


def test_get_event_video_without_image(tmp_path):
    """jpg 缺失 → 仅 text 段 + related.image_available=false（"没有图"非错误）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    (root / DEVICE / SID / "video" / f"{FRAME}.jpg").unlink()
    settings = LvdataMcpConfig(data={"root_dir": str(root)},
                               server={"host": "127.0.0.1", "port": 0},
                               watcher={"timezone": "Asia/Shanghai"})
    server, thread, port = start_server(create_app(settings))
    try:
        result = call(f"http://127.0.0.1:{port}", "get_event",
                      {"event_ref": f"{SID}:video:{FRAME}"})
        assert [c.type for c in result.content] == ["text"]
        assert _content(result)["related"]["image_available"] is False
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_get_event_image_available_single_source(tmp_path):
    """`related.image_available` 与实际附图恒一致（回归：旧实现两条推导路径可互相矛盾）。

    构造老行（2026-09-18 前）：`payload.frame_id` 是窗口内局部索引 "0"，真帧文件按
    ref 后缀存在——旧实现 JSON 报 image_available=false 却仍按 ref 找到图并附上；
    现在图片有无 = 单一出处（ref 后缀定位 + 同一次 stat/read）。
    """
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    frame_id = "019fabe5-b00e-7b8d-8000-8fc29ccdabff"
    ref = f"{SID}:video:{frame_id}"
    (root / DEVICE / SID / "video" / f"{frame_id}.jpg").write_bytes(b"\xff\xd8\xff\xe0jpg")
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        (ref, DEVICE, SID, "video", TS, "files", "帧",
         json.dumps({"frame_id": "0", "window_id": "w1"})))
    conn.commit()
    conn.close()
    from lvdata_mcp.store import DataStore

    store = DataStore(LvdataMcpConfig(data={"root_dir": str(root)},
                                      watcher={"timezone": "Asia/Shanghai"}))
    out, img = store.get_event(ref)
    assert img is not None and img[1] == "image/jpeg"
    assert out["related"]["image_available"] is True      # 与 img 同源，不可能矛盾
    # 帧文件缺失 → 两者同为"无图"
    (root / DEVICE / SID / "video" / f"{frame_id}.jpg").unlink()
    out2, img2 = store.get_event(ref)
    assert img2 is None and out2["related"]["image_available"] is False


def test_get_event_not_found(env):
    url, _ = env
    out = _content(call(url, "get_event", {"event_ref": f"{SID}:audio:nope"}))
    assert out["error"]["code"] == "not_found"


def _seed_recent_events(root: Path) -> tuple[list[str], str]:
    """播种「今天 3 条（旧→新）+ 昨天 1 条」，返回 (今日 refs, 昨日 ref)。

    锚定正午而非 now：避免测试恰逢本地午夜时窗口跨日（确定性）。今天最新一条的
    summary 故意超长（300 字），用于断言轻量投影的截断。
    """
    today = ms_to_local_date(int(time.time() * 1000), "Asia/Shanghai")
    noon_ms = day_bounds_ms(today, "Asia/Shanghai")[0] + 12 * 3600 * 1000
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    refs = []
    for i, etype in enumerate(("chat", "audio", "video")):
        ref = f"{SID}:{etype}:seeded{i}"
        summary = f"今日事件 {i}" if i < 2 else "长" * 300
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
            (ref, DEVICE, SID, etype, noon_ms + i * 1000, "files",
             summary, "{}"))
        refs.append(ref)
    yref = f"{SID}:chat:yesterday"
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
        "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        (yref, DEVICE, SID, "chat", noon_ms - 24 * 3600 * 1000, "chat_messages",
         "昨天的事件", "{}"))
    conn.commit()
    conn.close()
    return refs, yref


@pytest.fixture(scope="module")
def recent_env():
    """独立 app + 今日事件（不动共享 env 的库，避免影响 get_timeline 计数断言）。"""
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="lvdata-mcp-recent-"))
    make_sample(root)
    refs, yref = _seed_recent_events(root)
    settings = LvdataMcpConfig(data={"root_dir": str(root)},
                               server={"host": "127.0.0.1", "port": 0},
                               watcher={"timezone": "Asia/Shanghai"})
    server, thread, port = start_server(create_app(settings))
    yield f"http://127.0.0.1:{port}", root, refs, yref
    server.should_exit = True
    thread.join(timeout=10)


def test_get_recent_events_today(recent_env):
    url, root, refs, yref = recent_env
    out = _content(call(url, "get_recent_events", {"days": 1}))
    assert out["timezone"] == "Asia/Shanghai"
    assert out["date_from"] == out["date_to"]        # days=1 → 今天单日
    assert out["total"] == 3 and out["truncated"] is False
    assert len(out["days"]) == 1                     # 昨天不在窗口内
    day = out["days"][0]
    assert set(day) == {"date", "events"}
    assert day["date"] == out["date_to"]
    assert [e["event_ref"] for e in day["events"]] == refs   # 日内正序（早的在先）
    last = day["events"][-1]
    assert set(last) == {"event_ref", "type", "timestamp",
                         "source", "summary"}       # 轻量投影：无 payload、无 epoch ms
    assert len(last["summary"]) == 256               # summary 截断
    assert last["timestamp"] == f"{out['date_to']}T12:00:02+08:00"   # 今日最新一条（video）


def test_get_recent_events_grouped_by_day(recent_env):
    """日倒序 + 日内正序（days=2 时昨天成组）。"""
    url, root, refs, yref = recent_env
    out = _content(call(url, "get_recent_events", {"days": 2}))
    assert out["total"] == 4
    assert [d["date"] for d in out["days"]] == [out["date_to"], out["date_from"]]
    assert out["days"][1]["events"][0]["event_ref"] == yref


def test_get_recent_events_truncated(recent_env):
    url, root, refs, yref = recent_env
    from lvdata_mcp.store import DataStore

    store = DataStore(LvdataMcpConfig(
        data={"root_dir": str(root)}, watcher={"timezone": "Asia/Shanghai"}))
    out = store.get_recent_events(days=2, limit=2)
    assert out["total"] == 4 and out["truncated"] is True
    # 截断保留最新：只出现今天这一组（2 条都来自今天）
    assert [d["date"] for d in out["days"]] == [out["date_to"]]
    assert sum(len(d["events"]) for d in out["days"]) == 2


def test_ts_ms_zero_renders_null(tmp_path):
    """§11 D6（收口 I-8）：`ts_ms=0` 的无时间戳事件一律 `timestamp: null`。

    vision 对无 uuid 时间的帧写 ts_ms=0；渲染成 1970 会把哨兵值伪装成数据。
    覆盖 search / get_timeline 两条出口（get_event 本就为 None）。
    """
    from lvdata_mcp.store import DataStore

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    ref = f"{SID}:chat:no_ts"
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        (ref, DEVICE, SID, "chat", 0, "chat_messages", "没有时间戳的消息", "{}"))
    conn.commit()
    conn.close()
    store = DataStore(LvdataMcpConfig(data={"root_dir": str(root)},
                                      watcher={"timezone": "Asia/Shanghai"}))
    _seed_index(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          server={"host": "127.0.0.1", "port": 0},
                          watcher={"timezone": "Asia/Shanghai"})
    server, thread, port = start_server(create_app(cfg))
    try:
        url = f"http://127.0.0.1:{port}"
        hit = next(h for h in _content(call(url, "search",
                                            {"query": "时间戳"}))["hits"]
                   if h["event_ref"] == ref)
        assert hit["timestamp"] is None
        page = store.get_timeline(types=["chat"], limit=100)
        brief = next(e for d in page["days"] for e in d["events"]
                     if e["event_ref"] == ref)
        assert brief["timestamp"] is None
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# initialize 的 instructions 文案（与 docs/data/lvdata_mcp_tools.md §14.2 的 §16 修订版逐字一致）。
# 措辞即契约——改文案必须同时改这里与文档（agent 常驻上下文，行为随之改变）。
INSTRUCTIONS = (
    "LivaChat's recorded multimodal lifelog data. Read-only.\n"
    "Locate with get_recent_events / get_timeline; search with search (fast, preferred) / "
    "semantic_search (alternative wording); fetch details with get_event by event_ref.\n"
    "Conventions: start_date/end_date are YYYY-MM-DD inclusive; timezone is the timezone of "
    "the current query; list/search results are lightweight projections; fetch details and "
    "keyframe images with get_event by event_ref; failures return error{code,message} "
    "(do not rely only on isError).")


def _initialize(base: str) -> dict:
    """urllib 直发 MCP initialize（agent 连上第一条请求），返回 result。"""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "probe", "version": "0"}}}).encode()
    req = urllib.request.Request(
        base + "/mcp", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["result"]


def test_initialize_metadata(env):
    """§14：agent 连上即读到服务身份——真实版本号（非 mcp SDK 版本）+ instructions 文案。"""
    url, _ = env
    out = _initialize(url)
    assert out["serverInfo"]["name"] == "lvdata"
    assert out["serverInfo"]["version"] == VERSION      # 不是 SDK 版本（曾为 "1.29.1"）
    assert out["instructions"] == INSTRUCTIONS


ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def test_timestamp_iso_format(env):
    """§17：所有 `timestamp` 为**带偏移的 ISO 8601**（防格式回漂的正则护栏）。"""
    url, root = env
    _seed_index(root)
    day = ms_to_local_date(TS, "Asia/Shanghai")
    hits = _content(call(url, "search", {"query": "响应", "limit": 2}))["hits"]
    assert hits and all(ISO_TS.match(h["timestamp"]) for h in hits)
    page = _content(call(url, "get_timeline",
                         {"start_date": day, "end_date": day, "limit": 10}))
    evs = [e for d in page["days"] for e in d["events"]]
    assert evs and all(ISO_TS.match(e["timestamp"]) for e in evs)
    # 与生效时区自洽：Asia/Shanghai → +08:00
    assert hits[0]["timestamp"].endswith("+08:00")
    utc = _content(call(url, "search", {"query": "响应", "limit": 1, "timezone": "UTC"}))
    assert utc["hits"][0]["timestamp"].endswith("+00:00")


def test_dst_offsets_follow_zone_rules():
    """§17.2-b：偏移按**各时刻**的 zone 规则算——DST 时区内跨换季出现两种偏移。"""
    from datetime import datetime, timezone as _tz

    from lvdata_mcp.timeutil import ms_to_local

    def ms(y, mo, d, h, mi):
        return int(datetime(y, mo, d, h, mi, tzinfo=_tz.utc).timestamp() * 1000)

    # 美东 2026 换季点 = 03-08 07:00Z（当地 02:00 EST → 03:00 EDT）
    assert ms_to_local(ms(2026, 3, 8, 6, 10), "America/New_York").endswith("-05:00")
    assert ms_to_local(ms(2026, 3, 8, 7, 10), "America/New_York").endswith("-04:00")
    # 无 DST 的时区始终同一偏移
    assert ms_to_local(ms(2026, 3, 8, 6, 10), "Asia/Shanghai").endswith("+08:00")


def test_tool_descriptions_delegate_conventions(env):
    """§16 D5（防重复护栏）：共同约定只写 instructions，工具描述不得再抄回去。

    抽象走的字样：日期窗「inclusive」、时区回落「service-configured timezone」、
    投影「lightweight projection」。另断言关键指针句仍在：instructions 承载约定；
    `search`/`semantic_search` 保留 "get_event" 指针（MCP 的 instructions 是可选字段，
    客户端可能不注入）。
    """
    url, _ = env
    tools = {t.name: (t.description or "") for t in asyncio.run(_tool_list(url)).tools}
    for name, desc in tools.items():
        for moved in ("inclusive", "service-configured timezone", "lightweight projection"):
            assert moved not in desc, f"{name} description still repeats convention: {moved}"
    assert "get_event" in tools["search"] and "get_event" in tools["semantic_search"]
    assert "get_recent_events" not in tools["search"]      # 描述只讲本工具
    for conv in ("inclusive", "lightweight projection", "get_event", "isError", "timezone"):
        assert conv in INSTRUCTIONS, conv
    assert not _CJK.search(INSTRUCTIONS)
    assert not any(_CJK.search(d) for d in tools.values())
    # 描述体量护栏：英文比中文长；上限防再次塞入共同约定或长文。
    assert sum(len(d) for d in tools.values()) < 1100


_CJK = re.compile(r"[㐀-䶿一-鿿]")


def test_tool_errors_are_english(env):
    """D9：MCP tool 的 error/hint 只使用英文。"""
    url, _ = env
    cases = [
        ("get_timeline", {"start_date": "2026/07/24"}),
        ("get_timeline", {"types": []}),
        ("search", {"query": ""}),
        ("get_event", {"event_ref": "bad"}),
    ]
    for tool, args in cases:
        err = _content(call(url, tool, args))["error"]
        assert err["code"]
        assert not _CJK.search(err["message"]), (tool, err["message"])
        if "hint" in err:
            assert not _CJK.search(err["hint"]), (tool, err["hint"])


def test_version_single_source(env):
    """§14 D1/D3：/api/status、pyproject、运行时 VERSION 与安装元数据同源。

    VERSION 现在取自安装元数据，若只比较 VERSION 与元数据会退化为恒真；
    必须额外对照 pyproject 的声明版本，才能发现“改了版本但忘了重装 editable”。
    """
    url, _ = env
    with urllib.request.urlopen(url + "/api/status") as resp:
        assert json.loads(resp.read())["service"]["version"] == VERSION

    from importlib.metadata import version as pkg_version

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert VERSION == declared == pkg_version("lvdata-mcp")


def test_rest_error_bodies_are_structured(tmp_path):
    """I-5：中间件 401/429 与 `/api/status` 的错误体与其余错误同构。

    401/429 曾是字符串 `{"error": "unauthorized"}`（运维脚本按 `error.code` 解析会失败）；
    `database_not_initialized` 曾给 500，现按 `/readyz` 同口径给 **503**。
    """
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          server={"host": "127.0.0.1", "port": 0, "token": "tok"},
                          watcher={"timezone": "Asia/Shanghai"})
    server, thread, port = start_server(create_app(cfg))
    base = f"http://127.0.0.1:{port}"
    try:
        code, body = _http_error(urllib.request.urlopen, base + "/api/status")
        assert code == 401
        assert body["error"]["code"] == "unauthorized" and body["error"]["message"]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    # DB 未初始化 → 503（不是 500）
    empty = tmp_path / "empty"
    empty.mkdir()
    cfg2 = LvdataMcpConfig(data={"root_dir": str(empty)},
                           server={"host": "127.0.0.1", "port": 0},
                           watcher={"timezone": "Asia/Shanghai"})
    server2, thread2, port2 = start_server(create_app(cfg2))
    try:
        code, body = _http_error(urllib.request.urlopen,
                                 f"http://127.0.0.1:{port2}/api/status")
        assert code == 503 and body["error"]["code"] == "database_not_initialized"
    finally:
        server2.should_exit = True
        thread2.join(timeout=10)


def test_get_timeline_normalizes_echoed_dates(env):
    """I-6：`date_from`/`date_to` 回显归一为补零规范串（与 `days[].date` 同形）。"""
    url, _ = env
    out = _content(call(url, "get_timeline",
                        {"start_date": "2026-7-24", "end_date": "2026-7-24"}))
    assert out["date_from"] == "2026-07-24" and out["date_to"] == "2026-07-24"
    assert out["days"][0]["date"] == out["date_from"]      # 与分组日同形
    assert out["total"] == 5                               # 窗口语义不变（该日全部事件）


def test_get_timeline_empty_types_is_invalid(env):
    """I-7：显式 `types=[]` → invalid_param（不再静默当"全类型"）。"""
    url, _ = env
    out = _content(call(url, "get_timeline", {"types": []}))
    assert out["error"]["code"] == "invalid_param"
    assert "at least 1 item" in out["error"]["message"]      # pydantic min_length 的措辞
    # 不传 types 仍是全部类型（语义未变）
    assert _content(call(url, "get_timeline", {"limit": 50}))["total"] == 5


def test_api_status(env):
    """运维面：GET /api/status（原 get_status MCP tool）。"""
    url, _ = env
    with urllib.request.urlopen(url + "/api/status") as resp:
        assert resp.status == 200
        out = json.loads(resp.read())
    assert "data" in out and "watcher" in out
    assert out["data"]["sessions_total"] == 1


def test_get_status_lag_semantics(tmp_path):
    """v2 新鲜度：events.updated_at > reindex 水位（index_state）→ lag。

    空表 = 无重建记录 → 全部滞后（首轮全量重建）；表缺失（老库只读打开）→
    不上报滞后（无判定依据，避免失真）。
    """
    from lvdata_mcp.store import DataStore

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    store = DataStore(LvdataMcpConfig(data={"root_dir": str(root)}))
    st = store.get_status(0)
    assert st["data"]["index"]["lag_sessions"] == [SID]     # 空表 → 全部滞后
    assert st["data"]["index"]["last_rebuild_at"] is None

    conn = sqlite3.connect(root / "lvdata_inventory.db")
    # reindex 水位晚于事件（未来时间戳）→ 不滞后
    conn.execute(
        "INSERT INTO index_state (target, last_reindex_at) "
        "VALUES (?, '2099-01-01T00:00:01.000Z')", (SID,))
    conn.commit()
    st = store.get_status(0)
    assert st["data"]["index"]["lag_sessions"] == []
    assert st["data"]["index"]["last_rebuild_at"] == "2099-01-01T00:00:01.000Z"
    # 事件更新晚于重建水位 → 滞后
    conn.execute("UPDATE events SET updated_at = '2099-02-01T00:00:00.000Z'")
    conn.commit()
    assert store.get_status(0)["data"]["index"]["lag_sessions"] == [SID]
    # 表缺失（老库只读打开，未跑 ensure_schema）→ 不上报滞后
    conn.execute("DROP TABLE index_state")
    conn.commit()
    assert store.get_status(0)["data"]["index"]["lag_sessions"] == []


def test_search_end_to_end(env):
    url, root = env
    _seed_index(root)
    out = _content(call(url, "search", {"query": "响应", "limit": 5}))
    assert out["timezone"] == "Asia/Shanghai"
    assert len(out["hits"]) >= 2  # 两条 chat 均含 "响应"
    assert all("event_ref" in h for h in out["hits"])
    # §10：命中为可读时间（无 ts_ms/session_id，session 由 event_ref 前缀给出）
    assert set(out["hits"][0]) == {"event_ref", "type", "timestamp",
                                   "snippet", "text", "score"}
    day = ms_to_local_date(TS, "Asia/Shanghai")
    assert out["hits"][0]["timestamp"].startswith(day)
    # 类型过滤（audio 转写 = events.summary 同源）
    audio = _content(call(url, "search", {"query": "启动慢", "type": "audio"}))
    assert len(audio["hits"]) >= 1 and all(h["type"] == "audio" for h in audio["hits"])


def test_search_date_window(env):
    """日期窗含端点：命中日有结果、其他日为空（空结果非错误）。"""
    url, root = env
    _seed_index(root)
    day = ms_to_local_date(TS, "Asia/Shanghai")
    hit = _content(call(url, "search", {"query": "响应", "limit": 5,
                                        "start_date": day, "end_date": day}))
    assert len(hit["hits"]) >= 2
    assert all(h["timestamp"].startswith(day) for h in hit["hits"])
    other = _content(call(url, "search", {"query": "响应",
                                          "start_date": "2099-01-01",
                                          "end_date": "2099-01-01"}))
    assert other["hits"] == []


def test_search_bad_dates(env):
    """格式错 / 逆序 → invalid_param（与 get_timeline 同规则）。"""
    url, _ = env
    bad = _content(call(url, "search",
                        {"query": "响应", "start_date": "2026/07/24"}))
    assert bad["error"]["code"] == "invalid_param"
    rev = _content(call(url, "search", {"query": "响应", "start_date": "2026-07-25",
                                        "end_date": "2026-07-24"}))
    assert rev["error"]["code"] == "invalid_param"


def test_search_timezone(env):
    """timezone 影响窗口与渲染，输出为真实生效标识；无效 → 回落配置。"""
    url, root = env
    _seed_index(root)
    day = ms_to_local_date(TS, "Asia/Shanghai")
    # 火奴鲁鲁（UTC-10）：该事件落在前一天 → 用 CST 的日期查不到
    hnl = _content(call(url, "search", {"query": "响应", "start_date": day,
                                        "end_date": day,
                                        "timezone": "Pacific/Honolulu"}))
    assert hnl["timezone"] == "Pacific/Honolulu" and hnl["hits"] == []
    bad = _content(call(url, "search", {"query": "响应", "limit": 5,
                                        "timezone": "bogus/zone"}))
    assert bad["timezone"] == "Asia/Shanghai" and len(bad["hits"]) >= 2


def test_search_stopword(env):
    url, _ = env
    result = call(url, "search", {"query": "的"})
    assert result.isError is False
    assert _content(result)["error"]["code"] == "invalid_param"


def test_search_separator_only_query(env):
    """纯分隔符查询（「——」/「- -」）→ invalid_param 早退。

    修复前：空 MATCH 串触 FTS5 语法错 → 被 broad except 误报为 index_not_built。
    """
    url, _ = env
    for q in ["——", "- -", "· ·"]:
        out = _content(call(url, "search", {"query": q}))
        assert out["error"]["code"] == "invalid_param", q


def test_get_recent_events_bad_days(env):
    """days 越界（1..31）→ 结构化错误而非静默空结果。"""
    url, _ = env
    for days in (0, 32):
        out = _content(call(url, "get_recent_events", {"days": days}))
        assert out["error"]["code"] == "invalid_param", days


def test_get_recent_events_timezone_fallback(env):
    """无效 timezone → 回落服务配置，输出为真实生效标识（§8.7）。"""
    url, _ = env
    out = _content(call(url, "get_recent_events",
                        {"days": 1, "timezone": "bogus/zone"}))
    assert out["timezone"] == "Asia/Shanghai"


@pytest.fixture(scope="module")
def semantic_env():
    """独立 app + 注入 FakeEmbeddingProvider 的向量索引（§11：semantic_search 查询路径）。

    向量库建在临时 root（不碰共享 env 的数据；其 events 计数断言不受影响）。
    """
    import tempfile

    from lvdata_mcp.store import DataStore
    from lvdata_mcp.vec import VecIndex

    root = Path(tempfile.mkdtemp(prefix="lvdata-mcp-sem-"))
    make_sample(root)
    _seed_index(root)                       # FTS 路
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          server={"host": "127.0.0.1", "port": 0},
                          watcher={"timezone": "Asia/Shanghai"},
                          providers={"embedding": {"model": "fake", "dim": 128}})
    store = DataStore(cfg)
    vec = VecIndex(cfg, store, FakeEmbeddingProvider())
    asyncio.run(vec.rebuild_session(SID))   # 向量路
    server, thread, port = start_server(create_app(cfg, vec_index=vec))
    yield f"http://127.0.0.1:{port}", root
    server.should_exit = True
    thread.join(timeout=10)


def test_semantic_search_aligned_output(semantic_env):
    """§11：入参/输出与 search 对齐——可读 timestamp、无 session_id、顶层 timezone/mode。"""
    url, _ = semantic_env
    out = _content(call(url, "semantic_search", {"query": "响应", "limit": 3}))
    assert out["timezone"] == "Asia/Shanghai" and out["mode"] == "hybrid"
    assert out["searched_types"] == ["chat", "transcribe", "translate", "window",
                                     "video", "audio", "agent_job"]
    assert out["hits"]
    hit = out["hits"][0]
    base = {"event_ref", "type", "timestamp", "score", "via", "snippet", "text"}
    assert set(hit) - {"vec_score", "fts_score"} == base   # §15 只加腿级分数（可选出现）
    assert "session_id" not in hit and "ts_ms" not in hit
    assert hit["timestamp"] == ms_to_local(TS, "Asia/Shanghai")
    assert "<b>响应</b>" in hit["snippet"]            # FTS 高亮片段（与 search 同义）
    assert hit["text"] == "为什么响应这么慢"           # 原文（≤200 字）


def test_semantic_search_date_window(semantic_env):
    """日期窗同时作用于两路（含端点）；窗外为空、格式错/逆序 → invalid_param。"""
    url, _ = semantic_env
    day = ms_to_local_date(TS, "Asia/Shanghai")
    in_win = _content(call(url, "semantic_search",
                           {"query": "响应", "start_date": day, "end_date": day}))
    assert in_win["hits"] and all(h["timestamp"].startswith(day) for h in in_win["hits"])
    out_win = _content(call(url, "semantic_search",
                            {"query": "响应", "start_date": "2099-01-01",
                             "end_date": "2099-01-01"}))
    assert out_win["hits"] == []                      # 两路都被窗口挡住
    bad = _content(call(url, "semantic_search",
                        {"query": "响应", "start_date": "2026/07/24"}))
    assert bad["error"]["code"] == "invalid_param"
    rev = _content(call(url, "semantic_search",
                        {"query": "响应", "start_date": "2026-07-25",
                         "end_date": "2026-07-24"}))
    assert rev["error"]["code"] == "invalid_param"


def test_semantic_search_with_context(semantic_env):
    """with_context=true 附 ±2min 邻域（服务端在剥离 session_id 前解析）。

    §12：邻域为轻量投影——与 get_timeline 的 days[].events[] 同形。
    """
    url, _ = semantic_env
    with_ctx = _content(call(url, "semantic_search",
                             {"query": "响应", "limit": 2, "with_context": True}))
    assert all(h["context_events"] for h in with_ctx["hits"])
    ctx = with_ctx["hits"][0]["context_events"]
    assert set(ctx[0]) == {"event_ref", "type", "timestamp", "source", "summary"}
    day = ms_to_local_date(TS, "Asia/Shanghai")
    assert all(e["timestamp"].startswith(day) for e in ctx)   # 可读时间（按生效时区）
    assert [e["timestamp"] for e in ctx] == sorted(e["timestamp"] for e in ctx)
    plain = _content(call(url, "semantic_search", {"query": "响应", "limit": 2}))
    assert all("context_events" not in h for h in plain["hits"])


def test_semantic_search_timezone(semantic_env):
    """timezone 生效与回落（§8.7）：UTC 渲染 + 真实生效标识。"""
    url, _ = semantic_env
    utc = _content(call(url, "semantic_search", {"query": "响应", "limit": 1,
                                                 "timezone": "UTC"}))
    assert utc["timezone"] == "UTC"
    assert utc["hits"][0]["timestamp"] == ms_to_local(TS, "UTC")
    bad = _content(call(url, "semantic_search", {"query": "响应", "limit": 1,
                                                 "timezone": "bogus/zone"}))
    assert bad["timezone"] == "Asia/Shanghai"


def test_semantic_search_fts_degraded(tmp_path):
    """FTS 索引缺失（表未建）→ 静默降级为纯向量，不抛错（§11 D5）。

    回归：旧实现的 `except StoreError` 抓不到 sqlite3.OperationalError，
    未捕获异常会直接抛给调用方，"降级"名存实亡。
    §15 起：低于阈值的向量命中会被丢弃，故用与某条 summary 完全相同的 query
    （fake provider 下余弦 1.0）确保向量路有幸存者，"降级仍可用"才可断言。
    """
    from lvdata_mcp.store import DataStore
    from lvdata_mcp.vec import VecIndex

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)                                  # 注意：不建 FTS 索引
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          server={"host": "127.0.0.1", "port": 0},
                          watcher={"timezone": "Asia/Shanghai"},
                          providers={"embedding": {"model": "fake", "dim": 128}})
    vec = VecIndex(cfg, DataStore(cfg), FakeEmbeddingProvider())
    asyncio.run(vec.rebuild_session(SID))
    server, thread, port = start_server(create_app(cfg, vec_index=vec))
    try:
        out = _content(call(f"http://127.0.0.1:{port}", "semantic_search",
                            {"query": "为什么响应这么慢", "limit": 3}))
        assert out["hits"] and all(h["via"] == ["vec"] for h in out["hits"])
        assert all(h["snippet"] == "" for h in out["hits"])   # 无 FTS 高亮
        assert all(h["vec_score"] >= 0.50 for h in out["hits"])   # §15 阈值生效
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def cache_env():
    """独立 app + 计数版 Fake provider（§13：断言"同 query 只嵌一次"与 /api/status 指标）。"""
    import tempfile

    from lvdata_mcp.store import DataStore
    from lvdata_mcp.vec import VecIndex

    root = Path(tempfile.mkdtemp(prefix="lvdata-mcp-cache-"))
    make_sample(root)
    _seed_index(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          server={"host": "127.0.0.1", "port": 0},
                          watcher={"timezone": "Asia/Shanghai"},
                          providers={"embedding": {"model": "fake", "dim": 128}})
    provider = CountingEmbeddingProvider()
    vec = VecIndex(cfg, DataStore(cfg), provider)
    asyncio.run(vec.rebuild_session(SID))
    provider.calls = 0          # 只统计查询侧嵌入（建索引那次批量嵌入不算）
    provider.seen.clear()
    server, thread, port = start_server(create_app(cfg, vec_index=vec))
    yield f"http://127.0.0.1:{port}", provider
    server.should_exit = True
    thread.join(timeout=10)


def test_semantic_search_query_cache_across_params(cache_env):
    """§13：embedding 只依赖 query 文本——换 limit/日期窗/type/with_context 不重复嵌入。"""
    url, provider = cache_env
    day = ms_to_local_date(TS, "Asia/Shanghai")
    for args in ({"query": "响应", "limit": 1},
                 {"query": "响应", "limit": 3},
                 {"query": "响应", "start_date": day, "end_date": day},
                 {"query": "响应", "type": "chat"},
                 {"query": "响应", "with_context": True}):
        out = _content(call(url, "semantic_search", args))
        assert "error" not in out, out
    assert provider.calls == 1 and provider.seen == ["响应"]


def test_api_status_cache_field(cache_env):
    """§13 D2：/api/status 暴露 cache.query_embeddings（misses ⇔ 真实嵌入批次数）。"""
    url, provider = cache_env
    _content(call(url, "semantic_search", {"query": "响应"}))       # 命中已有缓存
    with urllib.request.urlopen(url + "/api/status") as resp:
        out = json.loads(resp.read())
    stats = out["cache"]["query_embeddings"]
    assert stats["size"] >= 1 and stats["hits"] >= 1
    assert stats["misses"] == provider.calls     # 每次未命中 ⇔ 一次 provider 调用


def test_api_status_no_cache_without_embedding(env):
    """未配 embedding（env fixture）→ 不出现 cache 字段（与 watcher 缺失时同款处理）。"""
    url, _ = env
    with urllib.request.urlopen(url + "/api/status") as resp:
        out = json.loads(resp.read())
    assert "cache" not in out


def test_semantic_search_embedding_disabled(env):
    """未配 embedding（env fixture 无 provider）→ embedding_disabled。"""
    url, _ = env
    out = _content(call(url, "semantic_search", {"query": "响应"}))
    assert out["error"]["code"] == "embedding_disabled"


def _min_vec_app(tmp_path, min_vec_score: float, keep_only: str | None = None):
    """构造 semantic_search 用的单工具 app：fake provider + 可选只留一个事件。

    返回 (base_url, server)。`keep_only=None` 保留 make_sample 的全部事件。
    """
    from lvdata_mcp.index import ensure_index
    from lvdata_mcp.store import DataStore
    from lvdata_mcp.vec import VecIndex

    root = tmp_path / "lvdata"
    root.mkdir()
    db = make_sample(root)
    if keep_only:
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM events WHERE event_ref != ?", (keep_only,))
        conn.commit()
        conn.close()
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          server={"host": "127.0.0.1", "port": 0},
                          watcher={"timezone": "Asia/Shanghai"},
                          providers={"embedding": {"model": "fake", "dim": 128,
                                                   "min_vec_score": min_vec_score}})
    store = DataStore(cfg)
    with sqlite3.connect(db) as c:
        ensure_index(c)
        rebuild_session(c, store, SID)
        c.commit()
    vec = VecIndex(cfg, store, FakeEmbeddingProvider())
    asyncio.run(vec.rebuild_session(SID))
    server, thread, port = start_server(create_app(cfg, vec_index=vec))
    return f"http://127.0.0.1:{port}", server, thread


def test_semantic_search_filters_low_similarity(tmp_path):
    """§15：默认阈值（0.50）下，无对应查询返回 `hits: []` + `filtered` 恰两键、不回填。

    fake provider 的相似度=共享字符比例——"股票" 与样本语料无字符交集 → 余弦 0.3162（< τ）。
    """
    url, server, thread = _min_vec_app(tmp_path, 0.50)
    try:
        out = _content(call(url, "semantic_search", {"query": "股票", "limit": 10}))
        assert out["hits"] == []                      # 不再"永远 limit 条"
        assert set(out["filtered"]) == {"min_score", "best_dropped"}
        assert out["filtered"]["min_score"] == 0.50
        assert out["filtered"]["best_dropped"] < 0.50
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_semantic_search_min_vec_score_zero_is_regression_guard(tmp_path):
    """§15 回归护栏：yaml `min_vec_score: 0` → 命中集合与现状一致（过滤关闭、无 filtered）。"""
    url, server, thread = _min_vec_app(tmp_path, 0)
    try:
        out = _content(call(url, "semantic_search", {"query": "股票", "limit": 3}))
        assert len(out["hits"]) == 3                  # 未过滤：vec 路照给 top-k
        assert "filtered" not in out
        assert all(h["via"] == ["vec"] for h in out["hits"])
        assert all(h["vec_score"] < 0.50 for h in out["hits"])   # 正是被默认阈值挡掉的那些
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_semantic_search_fts_hit_unaffected_by_threshold(tmp_path):
    """§15：阈值只作用于向量路——FTS 命中照常返回（即使向量路全被丢弃）。"""
    url, server, thread = _min_vec_app(tmp_path, 0.99)   # 阈值拉到 0.99：向量路几乎全灭
    try:
        out = _content(call(url, "semantic_search", {"query": "响应", "limit": 10}))
        assert out["hits"] and all(h["via"] == ["fts"] for h in out["hits"])
        assert all("vec_score" not in h for h in out["hits"])
        assert all("fts_score" in h for h in out["hits"])
        assert out["filtered"]["best_dropped"] < 0.99
        assert len(out["hits"]) == 2                  # 只减不增：不为凑满 limit=10 回填
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_semantic_search_no_filtered_when_nothing_dropped(tmp_path):
    """§15：未被丢弃时不出现 `filtered`；双路命中的命中同时带 vec_score/fts_score。"""
    ref = f"{SID}:chat:m1"
    url, server, thread = _min_vec_app(tmp_path, 0.50, keep_only=ref)   # 库里只剩这一条
    try:
        out = _content(call(url, "semantic_search",
                            {"query": "为什么响应这么慢"}))     # == 该事件 summary
        assert "filtered" not in out
        hit = out["hits"][0]
        assert hit["event_ref"] == ref and hit["via"] == ["fts", "vec"]
        assert hit["vec_score"] == 1.0 and "fts_score" in hit
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_trigger_params_validation():
    """§12：session_id 须为 UUID；stages 不可为空列表。"""
    from lvdata_mcp.schemas import TriggerParams
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TriggerParams(session_id="not-a-uuid")
    with pytest.raises(ValidationError):
        TriggerParams(stages=[])
    assert TriggerParams(session_id=SID, stages=["reindex"]).session_id == SID


def test_readyz_waits_for_watcher_first_poll(tmp_path):
    import urllib.request
    from lvdata_mcp.pipeline import Pipeline
    from lvdata_mcp.recording_watch import RecordingWatch
    from lvdata_mcp.store import DataStore
    from lvdata_mcp.watcher import Watcher

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)}, watcher={"timezone": "Asia/Shanghai"})
    store = DataStore(cfg)
    pipeline = Pipeline(cfg, store, RecordingWatch(cfg))
    watcher = Watcher(cfg, store, pipeline)
    app = create_app(cfg, watcher=watcher)
    server, thread, port = start_server(app)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz") as resp:
            pass
    except urllib.error.HTTPError as e:
        assert e.code == 503  # watcher 首轮 poll 未完成
    asyncio.run(watcher.poll_once())
    import urllib.error
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz") as resp:
        assert json.loads(resp.read())["status"] == "ok"
    server.should_exit = True
    thread.join(timeout=10)


def _post_json(url: str, path: str, payload: dict):
    req = urllib.request.Request(
        url + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req)


def _http_error(fn, *args) -> tuple[int, dict]:
    try:
        fn(*args)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    raise AssertionError("应返回 HTTP 错误")


def test_api_trigger_watcher_not_running(env):
    """env fixture 无 watcher → 503（watcher_not_running 先于 session 校验）。"""
    url, _ = env
    code, body = _http_error(_post_json, url, "/api/analysis/trigger",
                             {"session_id": SID, "stages": ["reindex"]})
    assert code == 503 and body["error"]["code"] == "watcher_not_running"


def test_api_trigger_bad_body(env):
    url, _ = env
    req = urllib.request.Request(
        url + "/api/analysis/trigger", data=b"not-json",
        headers={"Content-Type": "application/json"}, method="POST")
    code, body = _http_error(urllib.request.urlopen, req)
    assert code == 400 and body["error"]["code"] == "invalid_param"


def test_api_trigger_ok_and_not_found(tmp_path):
    """自建带 watcher 的 app（start_watcher=False → 不跑轮询，仅验证入队语义）。

    最小 Pipeline 分析器缺省 None：即便 round 被消费，分析类 stage 也会跳过，
    不产生 LLM 调用（pipeline.py 的 None 守卫）。
    """
    from lvdata_mcp.pipeline import Pipeline
    from lvdata_mcp.recording_watch import RecordingWatch
    from lvdata_mcp.store import DataStore
    from lvdata_mcp.watcher import Watcher

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": "Asia/Shanghai"})
    store = DataStore(cfg)
    watcher = Watcher(cfg, store, Pipeline(cfg, store, RecordingWatch(cfg)))
    server, thread, port = start_server(create_app(cfg, watcher=watcher,
                                                   start_watcher=False))
    base = f"http://127.0.0.1:{port}"
    try:
        code, body = _http_error(
            _post_json, base, "/api/analysis/trigger",
            {"session_id": "00000000-0000-4000-8000-000000000000"})
        assert code == 404 and body["error"]["code"] == "not_found"
        with _post_json(base, "/api/analysis/trigger",
                        {"session_id": SID, "stages": ["reindex"]}) as resp:
            assert resp.status == 200
            assert json.loads(resp.read()) == {
                "accepted": True, "stages": ["reindex"], "reason": "enqueued"}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_api_and_mcp_require_token(tmp_path):
    """配 token 时 /mcp 与 /api 需 bearer；/healthz /readyz 免鉴权。

    env fixture 未配 token（bearer 中间件不安装，server.py 的 `if token`），
    故本用例自建 app。
    """
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          server={"host": "127.0.0.1", "port": 0, "token": "tok"},
                          watcher={"timezone": "Asia/Shanghai"})
    server, thread, port = start_server(create_app(cfg))
    base = f"http://127.0.0.1:{port}"
    try:
        for path in ("/api/status", "/mcp"):
            code, _ = _http_error(urllib.request.urlopen, base + path)
            assert code == 401, path
        for path in ("/healthz", "/readyz"):
            with urllib.request.urlopen(base + path) as resp:
                assert resp.status == 200, path
        req = urllib.request.Request(base + "/api/status",
                                     headers={"Authorization": "Bearer tok"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_trigger_params_stages_enum():
    """§12：stages 枚举与 pipeline 一致（chatlog 已加入，agent_jobs 已移除）。"""
    from pydantic import ValidationError
    from lvdata_mcp.schemas import TriggerParams

    assert TriggerParams(stages=["chatlog"]).stages == ["chatlog"]
    with pytest.raises(ValidationError):
        TriggerParams(stages=["agent_jobs"])
