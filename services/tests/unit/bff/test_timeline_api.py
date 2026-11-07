from __future__ import annotations

import pytest

from tests.unit.bff.conftest import (
    DEVICE_A,
    FRAME_ID,
    SESS_A,
    SESS_B,
    SESS_D,
    TS_A1,
    TS_B1,
    TS_C1,
    add_timezones_table,
)


def _insert_event(lvdata_root, *, ts_ms: int, etype: str, seq: int = 0,
                  session: str = SESS_A, payload: dict | None = None) -> None:
    """插入任意类型事件（测试内新增，不动夹具既有行）。"""
    import json as _json
    import sqlite3

    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
            (f"{session}:{etype}:{ts_ms}:{seq}", DEVICE_A, session, etype, ts_ms,
             "files", "", _json.dumps(payload or {})))


def test_overview_days_from_day_summary(store):
    """§7.3 E6：days 列表 = day_summary 行（date 降序）+ day_stats 透出（单表读取）。"""
    from bff.timeline.service import build_overview

    resp = build_overview(store, None, None)
    days = {d.date: d for d in resp.days}
    assert list(days) == ["2026-08-21", "2026-08-20", "2026-08-19",
                          "2026-08-18", "2026-08-17"]
    assert days["2026-08-18"].counts == {
        "chat": 3, "audio": 2, "video": 1, "window": 1, "agent_job": 1}
    assert days["2026-08-17"].counts == {"chat": 1, "audio": 1}
    assert days["2026-08-19"].counts == {}          # 非 ok 行也有 counts（预计算）


def test_overview_hot_slots_and_images_passthrough(store):
    """hot_slots/images 取自 day_stats；media_url 由 BFF 组装（E5 单 helper）。"""
    from bff.timeline.service import build_overview

    days = {d.date: d for d in build_overview(store, None, None).days}
    d18 = days["2026-08-18"]
    assert [(s.start_ms, s.end_ms, s.count) for s in d18.hot_slots] == [
        (TS_A1, TS_B1, 4)]
    assert len(d18.images) == 1
    img = d18.images[0]
    assert (img.frame_id, img.ts_ms, img.window_id) == (FRAME_ID, TS_A1 + 2000, FRAME_ID)
    assert img.media_url ==         f"/api/mylife/media/{DEVICE_A}/{SESS_A}/video/{FRAME_ID}.jpg"
    assert days["2026-08-17"].hot_slots == [] and days["2026-08-17"].images == []


def test_overview_topics_from_day_summary(store):
    """topics 透出 day_summary JSON topics 段（§13 新格式 str / legacy {topic,
    detail} 归一化）；status=ok 才透出（E11）。"""
    from bff.timeline.service import build_overview

    days = {d.date: d for d in build_overview(store, None, None).days}

    # 08-18 = 字典形态：主题名照常透出；§13 起 detail 统一置 null
    # （LLM 不再产出 detail，Android chip 只显示主题名，无感知）；
    # §10 search_words 透出，缺省为 null（客户端回退用 topic 搜索）
    d18 = days["2026-08-18"]
    assert [(t.topic, t.detail, t.search_words) for t in d18.topics] == [
        ("旅游与天气", None, None),
        ("股票与投资", None, "股票走势"),
    ]

    d17 = days["2026-08-17"]
    assert [(t.topic, t.search_words) for t in d17.topics] == [
        ("早间新闻", None)]  # §10：str 形态 → 搜索词 null

    # 非 ok 行（08-19 pending / 08-21 failed 残留 doc）→ 空列表（E11 门控）
    assert days["2026-08-19"].topics == []
    assert days["2026-08-21"].topics == []


def test_overview_summary_gating(store):
    """summary 仅 status=ok 透出；failed 行不清旧 doc（day.py:590-598）→ 必须门控。"""
    from bff.timeline.service import build_overview

    days = {d.date: d for d in build_overview(store, None, None).days}
    assert days["2026-08-18"].summary == "用户询问了三亚天气与股票信息。"
    assert days["2026-08-17"].summary == "用户听了早间新闻播报。"  # §14 纯总述
    assert days["2026-08-19"].summary is None                    # pending 占位
    assert days["2026-08-21"].summary is None                    # failed + 陈旧 doc


def test_overview_has_pending_branches(store):
    """E1（二轮修订）：pending → True；failed 且 fail_count<3 → True；
    failed 且 fail_count≥3（已放弃重试）→ False；ok → False。"""
    from bff.timeline.service import build_overview

    days = {d.date: d for d in build_overview(store, None, None).days}
    assert days["2026-08-19"].has_pending is True    # pending
    assert days["2026-08-21"].has_pending is True    # failed, fail_count=1
    assert days["2026-08-20"].has_pending is False   # failed, fail_count=5
    assert days["2026-08-18"].has_pending is False   # ok
    assert days["2026-08-17"].has_pending is False   # ok


def test_overview_device_id_ignored(store):
    """E2：device_id 参数保留但对 overview 失效（day_stats 为全设备合并口径）。"""
    from bff.timeline.service import build_overview

    base = build_overview(store, None, None)
    other = build_overview(store, "unknown-device", None)
    assert [d.date for d in other.days] == [d.date for d in base.days]


def test_overview_single_query(store, monkeypatch):
    """§7 + §8.5-1：全量模式仍只有一次 day_summary 查询（未触防呆不复用 COUNT）；
    分页模式 = 页查询 1 次（LIMIT limit+1）+ total COUNT 1 次。"""
    from bff.common.store import LvdataStore
    from bff.timeline.service import FULL_CAP_DAYS, build_overview

    rows_calls, count_calls = [], []
    orig_rows, orig_count = LvdataStore.day_summaries, LvdataStore.count_days

    def spy_rows(self, tz, date_from, date_to, *, before=None, limit=None):
        rows_calls.append((tz, date_from, date_to, before, limit))
        return orig_rows(self, tz, date_from, date_to, before=before, limit=limit)

    def spy_count(self, tz):
        count_calls.append(tz)
        return orig_count(self, tz)

    monkeypatch.setattr(LvdataStore, "day_summaries", spy_rows)
    monkeypatch.setattr(LvdataStore, "count_days", spy_count)

    build_overview(store, None, None)
    assert rows_calls == [("Asia/Shanghai", None, None, None, FULL_CAP_DAYS + 1)]
    assert count_calls == []                       # 未触防呆：total = len(days)

    rows_calls.clear()
    build_overview(store, None, None, limit=2)
    assert rows_calls == [("Asia/Shanghai", None, None, None, 3)]
    assert count_calls == ["Asia/Shanghai"]


def test_overview_day_stats_missing_column(lvdata_root, store):
    """交叉版本（§7.2.6）：库未 ALTER（无 day_stats 列）→ 三字段空、summary 照常。"""
    import sqlite3

    from bff.timeline.service import build_overview

    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute("ALTER TABLE day_summary DROP COLUMN day_stats")
    days = {d.date: d for d in build_overview(store, None, None).days}
    d18 = days["2026-08-18"]
    assert (d18.counts, d18.hot_slots, d18.images) == ({}, [], [])
    assert d18.summary == "用户询问了三亚天气与股票信息。"


@pytest.mark.parametrize("bad,date", [("not json", "2026-08-17"),
                                      ('"[1]"', "2026-08-18")])
def test_overview_bad_day_stats_json(lvdata_root, store, bad, date):
    """坏 JSON / 非 dict → 空字段（不 500）。"""
    import sqlite3

    from bff.timeline.service import build_overview

    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute("UPDATE day_summary SET day_stats = ? WHERE local_date = ?",
                     (bad, date))
    days = {d.date: d for d in build_overview(store, None, None).days}
    assert (days[date].counts, days[date].hot_slots, days[date].images) == ({}, [], [])


def test_overview_malformed_day_stats_entries(lvdata_root, store):
    """条目级畸形（hot_slot 非 dict / image.ts_ms 类型不符）→ 丢该条目，不 500。"""
    import json
    import sqlite3

    from bff.timeline.service import build_overview

    bad = {
        "schema_version": 1, "counts": {"chat": 1},
        "hot_slots": [{"start_ms": 1, "end_ms": 2, "count": 3}, "garbage"],
        "images": [
            {"frame_id": "bad", "ts_ms": "not-int", "event_ref": "e",
             "device_uuid": "d", "rsession_id": "s"},          # ts_ms 非 int → 丢
            {"frame_id": "good", "ts_ms": 5, "event_ref": "e2",
             "device_uuid": DEVICE_A, "rsession_id": SESS_A, "window_id": None},
        ],
    }
    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute("UPDATE day_summary SET day_stats = ? WHERE local_date = ?",
                     (json.dumps(bad), "2026-08-18"))
    days = {d.date: d for d in build_overview(store, None, None).days}
    d18 = days["2026-08-18"]
    assert [(s.start_ms, s.end_ms, s.count) for s in d18.hot_slots] == [(1, 2, 3)]
    assert [i.frame_id for i in d18.images] == ["good"]
    assert d18.counts == {"chat": 1}


# -- overview 分页（§8）------------------------------------------------------


def test_overview_pagination_pages_cover_all(store):
    """§8 E1：limit 逐页翻完 = 全集，不重不漏；next_cursor = 截断后最后一天（非多取行）。"""
    from bff.timeline.service import build_overview

    seen: list[str] = []
    cursor = None
    for _ in range(5):                                # 夹具 5 天 / limit=2 → 3 页
        resp = build_overview(store, None, None, limit=2, cursor=cursor)
        dates = [d.date for d in resp.days]
        assert dates == sorted(dates, reverse=True)   # 页内 date 降序
        assert resp.total == 5                        # 各页 total 恒等 = 全集天数
        assert resp.next_cursor == (dates[-1] if resp.next_cursor else None)
        seen.extend(dates)
        cursor = resp.next_cursor
        if cursor is None:
            break
    assert seen == ["2026-08-21", "2026-08-20", "2026-08-19", "2026-08-18", "2026-08-17"]
    assert cursor is None


def test_overview_pagination_cursor_is_strictly_older(store):
    """§8 E1：cursor 语义 = 严格早于（cursor 当天不重发）。"""
    from bff.timeline.service import build_overview

    resp = build_overview(store, None, None, limit=10, cursor="2026-08-19")
    assert [d.date for d in resp.days] == ["2026-08-18", "2026-08-17"]
    assert resp.next_cursor is None                   # 无更早 → None
    assert resp.total == 5


def test_overview_pagination_empty_page_and_table_missing(lvdata_root, store):
    """§8：空页边界（最早一天作 cursor）与 day_summary 表缺失（容错不 500）。"""
    import sqlite3

    from bff.timeline.service import build_overview

    resp = build_overview(store, None, None, limit=2, cursor="2026-08-17")
    assert resp.days == [] and resp.next_cursor is None and resp.total == 5

    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute("DROP TABLE day_summary")
    resp = build_overview(store, None, None, limit=2)
    assert resp.days == [] and resp.next_cursor is None and resp.total == 0


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-40", "2026-08-12|200",
                                 "20260812", ""])
def test_overview_pagination_invalid_cursor(client, bad):
    """§8 E5：非法 cursor → 400（含 /timeline 形态 `date|offset` 与 ISO 非严格形态）。"""
    resp = client.get("/api/mylife/overview", params={"limit": 2, "cursor": bad})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"
    assert "invalid cursor" in resp.json()["error"]["message"]


def test_overview_pagination_cursor_requires_limit(client):
    """§8 E3：只传 cursor 不传 limit → 400（不回落全量）。"""
    resp = client.get("/api/mylife/overview", params={"cursor": "2026-08-19"})
    assert resp.status_code == 400
    assert "cursor requires limit" in resp.json()["error"]["message"]


@pytest.mark.parametrize("limit", [0, 401])
def test_overview_pagination_limit_bounds(client, limit):
    """§8 E7：limit ∉ [1, 400] → 422（Query 约束，对齐 /timeline 风格）。"""
    resp = client.get("/api/mylife/overview", params={"limit": limit})
    assert resp.status_code == 422


def test_overview_pagination_envelope(client):
    """§8：API 层 limit/cursor 生效，响应带 next_cursor/total。"""
    resp = client.get("/api/mylife/overview",
                      params={"limit": "2", "cursor": "2026-08-20"})
    assert resp.status_code == 200
    body = resp.json()
    assert [d["date"] for d in body["days"]] == ["2026-08-19", "2026-08-18"]
    assert body["next_cursor"] == "2026-08-18"
    assert body["total"] == 5


def test_overview_full_mode_unchanged_and_capped(lvdata_root, store):
    """§8 E2/E11：不传 limit = 全量零变化；超 FULL_CAP 截断 + 游标可续 + total 走 COUNT。"""
    import sqlite3
    from datetime import date, timedelta

    from bff.timeline.service import FULL_CAP_DAYS, build_overview

    resp = build_overview(store, None, None)
    assert [d.date for d in resp.days] == ["2026-08-21", "2026-08-20", "2026-08-19",
                                           "2026-08-18", "2026-08-17"]
    assert resp.next_cursor is None and resp.total == 5        # 未触防呆

    base = date(2026, 8, 16) - timedelta(days=FULL_CAP_DAYS - 4)   # 更早的连续日，止于 08-15
    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.executemany(
            "INSERT INTO day_summary (timezone, local_date, utc_offset_min, day_start_ms, "
            "day_end_ms, day_summary, status, event_count, fail_count, day_stats) "
            "VALUES ('Asia/Shanghai', ?, 480, 0, 0, '{}', 'pending', 0, 0, '{}')",
            [((base + timedelta(days=i)).isoformat(),) for i in range(FULL_CAP_DAYS - 4)])

    resp = build_overview(store, None, None)
    assert len(resp.days) == FULL_CAP_DAYS
    assert resp.total == FULL_CAP_DAYS + 1
    assert resp.next_cursor == resp.days[-1].date              # 截断后最后一天（非第 1001 天）
    nxt = build_overview(store, None, None, limit=10, cursor=resp.next_cursor)
    assert nxt.days and all(d.date < resp.next_cursor for d in nxt.days)


def test_overview_pagination_under_tz_redirect(lvdata_root, store):
    """§8 E3-D″ × 分页：非最新时区被重定向后，页窗口在**生效时区**的行集上推进。"""
    import sqlite3

    from bff.timeline.service import build_overview

    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.executemany(
            "INSERT INTO day_summary (timezone, local_date, utc_offset_min, day_start_ms, "
            "day_end_ms, day_summary, status, event_count, fail_count, day_stats) "
            "VALUES ('UTC', ?, 0, 0, 0, '{}', 'pending', 0, 0, '{}')",
            [(d,) for d in ("2026-08-18", "2026-08-19", "2026-08-20")])
    add_timezones_table(lvdata_root, ["Asia/Shanghai", "UTC"])
    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute("UPDATE timezones SET updated_at = '2026-09-24T00:00:00.000Z' "
                     "WHERE timezone = 'Asia/Shanghai'")
        conn.execute("UPDATE timezones SET updated_at = '2026-09-25T00:00:00.000Z' "
                     "WHERE timezone = 'UTC'")

    page1 = build_overview(store, None, "Asia/Shanghai", limit=2)   # 请求值非最新 → 重定向
    assert page1.timezone == "UTC"
    assert [d.date for d in page1.days] == ["2026-08-20", "2026-08-19"]
    assert page1.total == 3
    page2 = build_overview(store, None, "Asia/Shanghai", limit=2, cursor=page1.next_cursor)
    assert [d.date for d in page2.days] == ["2026-08-18"]
    assert page2.next_cursor is None


def test_timeline_merge_and_sort(store):
    from bff.timeline.service import build_timeline

    resp = build_timeline(store, None, None, None, None, None, 200, None, None)
    # 本地日倒序（河流从最新开始），日内事件正序
    assert [d.date for d in resp.days] == ["2026-08-18", "2026-08-17"]
    for day in resp.days:
        assert [e.ts_ms for e in day.events] == sorted(e.ts_ms for e in day.events)
    events = [e for day in resp.days for e in day.events]
    assert resp.total == 10  # A:6 + B:2 + C:1 + F:1

    chat0 = events[0]
    assert chat0.type == "chat"
    assert chat0.device_uuid == "11111111-1111-4111-8111-111111111111"
    # §9.1.1：session 维度不暴露（rsession_id 列与 session_id 别名均不透出）
    d = chat0.model_dump()
    assert "rsession_id" not in d
    assert "session_id" not in d

    audio = next(e for e in events if e.type == "audio" and e.device_uuid == DEVICE_A)
    assert audio.media_url == (
        f"/api/mylife/media/11111111-1111-4111-8111-111111111111/{SESS_A}"
        "/audio/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.ogg"
    )
    assert audio.transcript == "三亚天气如何"
    assert audio.emotions == [{"emotion": "neutral", "confidence": 0.9}]

    video = next(e for e in events if e.type == "video")
    assert video.media_url.endswith("/video/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.jpg")
    assert video.caption == "浏览器页面" and video.place == "办公室"

    job = next(e for e in events if e.type == "agent_job")
    assert job.job_key == "job-1"
    assert job.status == "done"


def test_timeline_type_filter(store):
    from bff.timeline.service import build_timeline

    resp = build_timeline(store, None, None, None, None, "chat,audio", 200, None, None)
    types = {e.type for day in resp.days for e in day.events}
    assert types == {"chat", "audio"}
    assert resp.total == 7  # A:3 + B:2 + C:1 + F:1


def test_timeline_pagination_cursor(store):
    from bff.timeline.service import build_timeline

    page1 = build_timeline(store, None, None, None, None, None, 4, None, None)
    assert page1.next_cursor is not None
    assert page1.total == 10
    assert len([e for day in page1.days for e in day.events]) == 4

    page2 = build_timeline(store, None, None, None, None, None, 4, page1.next_cursor, None)
    assert page2.next_cursor is not None
    ids1 = [(e.ts_ms, e.type) for day in page1.days for e in day.events]
    ids2 = [(e.ts_ms, e.type) for day in page2.days for e in day.events]
    assert not set(ids1) & set(ids2)

    page3 = build_timeline(store, None, None, None, None, None, 4, page2.next_cursor, None)
    assert page3.next_cursor is None
    assert len([e for day in page3.days for e in day.events]) == 2


def test_timeline_date_range(store):
    from bff.timeline.service import build_timeline

    resp = build_timeline(store, None, "2026-08-17", "2026-08-17", None, None, 200, None, None)
    days = {d.date for d in resp.days}
    assert days == {"2026-08-17"}
    assert resp.total == 2  # C audio + F chat


def test_timeline_session_filter_and_missing(store):
    from bff.common.store import BffError
    from bff.timeline.service import build_timeline

    resp = build_timeline(store, None, None, None, SESS_B, None, 200, None, None)
    assert resp.total == 2
    assert {e.type for day in resp.days for e in day.events} == {"chat", "audio"}

    # SESS_D timeline.json 缺失 → 不报错、不出现
    resp = build_timeline(store, None, None, None, SESS_D, None, 200, None, None)
    assert resp.total == 0

    with pytest.raises(BffError) as ei:
        build_timeline(store, None, None, None, "no-such-session", None, 200, None, None)
    assert ei.value.code == "not_found"


def test_timeline_bad_type_filter(store):
    from bff.common.store import BffError
    from bff.timeline.service import build_timeline

    with pytest.raises(BffError) as ei:
        build_timeline(store, None, None, None, None, "bogus", 200, None, None)
    assert ei.value.code == "bad_request"


# -- T1：audio LLM 摘要暴露（docs/android_timeline_audio_gallery.md §3.2）--------


def test_timeline_audio_summary_from_payload(store):
    """audio 的 payload LLM 摘要以 audio_summary 透出；同名字段 summary 仍为列值（检索拼接串）；
    store 侧私有键 payload_dict 永不上 wire。"""
    from bff.timeline.service import build_timeline

    resp = build_timeline(store, None, None, None, None, "audio", 200, None, None)
    audio = next(e for day in resp.days for e in day.events if e.transcript == "三亚天气如何")

    d = audio.model_dump()
    assert d["summary"] == "三亚天气如何 询问三亚天气"   # 列值不变（向后兼容）
    assert d["audio_summary"] == "询问三亚天气"          # payload；列同名字段遮蔽的真值
    assert "payload_dict" not in d
    assert d["transcript"] == "三亚天气如何"             # payload 扁平化照旧


def test_timeline_audio_summary_absent_for_other_types(store):
    """audio_summary 仅 audio 事件独有；payload_dict 对所有类型都不外泄。"""
    from bff.timeline.service import build_timeline

    resp = build_timeline(store, None, None, None, None, None, 200, None, None)
    events = [e for day in resp.days for e in day.events]
    assert events  # 夹具非空
    for e in events:
        d = e.model_dump()
        assert "payload_dict" not in d
        if e.type != "audio":
            assert "audio_summary" not in d


def test_timeline_audio_energy_fields_pass_through(store, lvdata_root):
    """§10 A1′：能量字段随 payload 下发到 BFF wire（MCP 侧剔除，**BFF 保留**）。
    与 tools/lvdata-mcp/tests/test_energy_guard.py 构成前后半场契约。"""
    from bff.timeline.service import build_timeline

    _insert_event(
        lvdata_root, ts_ms=TS_A1 + 5000, etype="audio", seq=9,
        payload={"segment_id": "seg-energy", "transcript": "打开应用非常慢",
                 "summary": "用户反馈启动慢", "duration_s": 60,
                 "energy_v": 1, "frame_ms": 20, "db_min": -70.0, "db_max": 0.0,
                 "energy": "AAAA"},
    )
    resp = build_timeline(store, None, None, None, None, "audio", 200, None, None)
    d = next(
        e.model_dump() for day in resp.days for e in day.events
        if e.model_dump().get("energy_v") == 1
    )
    assert d["energy"] == "AAAA" and d["frame_ms"] == 20
    assert d["db_min"] == -70.0 and d["db_max"] == 0.0
    assert d["transcript"] == "打开应用非常慢"      # 既有字段不受影响


def test_audio_count_matches_timeline_total(store):
    """§3.3 不变量：日卡 counts["audio"] == 同日 /timeline?types=audio 的 total。"""
    from bff.timeline.service import build_overview, build_timeline

    for day in build_overview(store, None, None).days:
        resp = build_timeline(store, None, day.date, day.date, None, "audio", 200, None, None)
        assert day.counts.get("audio", 0) == resp.total


def test_api_overview_envelope(client):
    resp = client.get("/api/mylife/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == 1
    assert body["timezone"] == "Asia/Shanghai"
    # session 维度不再暴露（§9.1.1）
    assert "total_sessions" not in body
    assert all("sessions" not in d for d in body["days"])
    # images 增量字段 window_id 透出（Q3 客户端跨日轮转分组键）
    d18 = next(d for d in body["days"] if d["date"] == "2026-08-18")
    assert d18["images"][0]["window_id"] == FRAME_ID


def test_api_timeline_pending_day_not_failing(client):
    """timeline.json 缺失的 session 不整请求报错。"""
    resp = client.get("/api/mylife/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10


def test_api_bad_timezone(client):
    resp = client.get("/api/mylife/overview?timezone=Not/AZone")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


def test_api_device_param_accepted(client):
    # fixture dev_token 为空串 → 不带 token 即通过；本用例只断言 device_id 参数被接受
    resp = client.get("/api/mylife/overview?device_id=some-device")
    assert resp.status_code == 200


def test_timeline_device_filter(store):
    """§5.5：device_id / 白名单过滤后只返回可见设备的事件。"""
    from bff.common.store import LvdataStore
    from bff.timeline.service import build_timeline

    # 未知设备 → 无事件
    resp = build_timeline(store, "unknown-device", None, None, None, None,
                          200, None, None)
    assert resp.total == 0
    # 白名单外设备 → 空
    scoped = LvdataStore(str(store.data_root),
                         device_uuids=["99999999-9999-4999-8999-999999999999"])
    resp2 = build_timeline(scoped, None, None, None, None, None, 200, None, None)
    assert resp2.total == 0
    # 白名单内 → 全部事件
    scoped2 = LvdataStore(str(store.data_root), device_uuids=[DEVICE_A])
    resp3 = build_timeline(scoped2, None, None, None, None, None, 200, None, None)
    assert resp3.total == 10  # A:6 + B:2 + C:1 + F:1


@pytest.mark.parametrize("ts,date", [(TS_A1, "2026-08-18"), (TS_B1, "2026-08-18"), (TS_C1, "2026-08-17")])
def test_utc_to_local_day(ts, date):
    from bff.timeline.service import _local_date

    assert _local_date(ts, "Asia/Shanghai") == date


# ── 时区：timeline 的 G3 语义（timezone-support.md §7.1）与 overview 的 E3-D″ ──
# 两套规则**有意不同**（docs/bff_service_interfaces.md §7.3 E10）：
#   /timeline、/search → resolve_request_tz：known 判定（不看 recency），未登记回退默认；
#   /overview          → resolve_overview_tz（E3-D″）：非「最新时区」一律用最新时区。


def _resp_tz(client, path: str, raw_tz: str) -> str:
    from urllib.parse import quote

    resp = client.get(f"{path}?timezone={quote(raw_tz, safe='')}")
    assert resp.status_code == 200
    return resp.json()["timezone"]


def test_api_timezone_in_table_used(client, lvdata_root):
    """请求时区在 timezones 表 → 用之（timeline 路径）。"""
    add_timezones_table(lvdata_root, ["Asia/Shanghai", "UTC"])
    assert _resp_tz(client, "/api/mylife/timeline", "UTC") == "UTC"


def test_api_timezone_not_in_table_falls_back(client, lvdata_root):
    """合法但不在 timezones 表 → 回退默认，响应 timezone 为生效值。"""
    add_timezones_table(lvdata_root, ["Asia/Shanghai"])
    assert _resp_tz(client, "/api/mylife/timeline", "UTC") == "Asia/Shanghai"


def test_api_timezone_empty_table_passes_through(client, lvdata_root):
    """空表（全新部署）→ 放行请求时区（现状行为不退化）。"""
    add_timezones_table(lvdata_root, [])
    assert _resp_tz(client, "/api/mylife/timeline", "UTC") == "UTC"


def test_api_gmt_offset_normalized_against_table(client, lvdata_root):
    """GMT+08:00 请求归一化为 +08:00 后按表成员判定 → 命中表内规范形。"""
    add_timezones_table(lvdata_root, ["Asia/Shanghai", "+08:00"])
    assert _resp_tz(client, "/api/mylife/timeline", "GMT+08:00") == "+08:00"


def test_api_gmt_offset_not_in_table_falls_back(client, lvdata_root):
    """GMT 前缀归一化后不在表 → 回退默认（不再 400，§7.1 边界）。"""
    add_timezones_table(lvdata_root, ["Asia/Shanghai"])
    assert _resp_tz(client, "/api/mylife/timeline", "GMT+08:00") == "Asia/Shanghai"


def test_api_default_timezone_short_circuits(client, lvdata_root):
    """tz == default 短路不查表（表缺失也放行）。"""
    assert _resp_tz(client, "/api/mylife/timeline", "Asia/Shanghai") == "Asia/Shanghai"


def test_api_fixed_offset_day_grouping(client, lvdata_root):
    """固定偏移规范形分日与 Asia/Shanghai 一致（+08:00 无 DST 差异，§7.3）。"""
    add_timezones_table(lvdata_root, ["Asia/Shanghai", "+08:00"])
    resp = client.get("/api/mylife/timeline?timezone=GMT%2B08:00")
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "+08:00"
    assert [d["date"] for d in body["days"]] == ["2026-08-18", "2026-08-17"]


def test_api_empty_timezone_falls_back_to_default(client, lvdata_root):
    """空串 timezone 回退默认（validate_tz 的 `timezone or default` 语义回归）。"""
    assert _resp_tz(client, "/api/mylife/timeline", "") == "Asia/Shanghai"


def test_overview_tz_redirects_to_latest(client, lvdata_root):
    """E3-D″：非最新时区（含未登记）→ 用最新时区行；响应 timezone = 生效时区。"""
    import sqlite3

    # 两行 updated_at 相同 → tie-break timezone ASC → 最新 = America/New_York
    add_timezones_table(lvdata_root, ["Asia/Shanghai", "America/New_York"])
    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute(
            "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
            "day_start_ms, day_end_ms, day_summary, day_stats, status, "
            "event_count, fail_count, generated_at) VALUES "
            "('America/New_York','2026-08-18',-240,0,0,'{}','{}','ok',0,0,NULL)")
    resp = client.get("/api/mylife/overview?timezone=Asia/Shanghai")
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "America/New_York"
    assert [d["date"] for d in body["days"]] == ["2026-08-18"]
    # 请求恰为最新时区 → 用它
    assert _resp_tz(client, "/api/mylife/overview", "America/New_York") == \
        "America/New_York"


def test_overview_tz_table_missing_fail_open(client, lvdata_root):
    """timezones 表缺失（默认夹具）→ fail-open 放行请求值（现状语义）。"""
    assert _resp_tz(client, "/api/mylife/overview", "UTC") == "UTC"
