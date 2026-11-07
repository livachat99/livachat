"""day_stats stage（docs/bff_service_interfaces.md §7.2/§7.5 P2）。

等价性基线 = tests/fixtures/day_stats_golden.json（由 BFF 原实现生成、删码前固化，
§7.5 P0）——**本文件是「BFF 派生被安全移除」的唯一凭据，勿改夹具/勿重算基线**。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.ddl import ensure_schema
from lvdata_mcp.summary import day_stats as ds_mod
from lvdata_mcp.summary.day_stats import DayStats
from lvdata_mcp.timeutil import day_bounds_ms

FIX = Path(__file__).resolve().parent / "fixtures"
EVENTS = json.loads((FIX / "day_stats_events.json").read_text(encoding="utf-8"))
GOLDEN = json.loads((FIX / "day_stats_golden.json").read_text(encoding="utf-8"))

TZ = GOLDEN["timezone"]
BASE_UPDATED = "2026-09-01T00:00:00.000Z"       # 夹具事件的 updated_at（固定值）
TS_UTC = "2026-09-01T00:00:00.000Z"


def _build(root: Path, *, updated_at: str = BASE_UPDATED) -> LvdataMcpConfig:
    """夹具树：events 按夹具次序插入（event_id 自增 = 夹具序，排序等价）。"""
    root.mkdir(parents=True, exist_ok=True)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": TZ})
    conn = sqlite3.connect(cfg.data.resolved_db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn, TZ, [])
    conn.executemany(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "ts_utc, source, summary, payload, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(e["event_ref"], e["device_uuid"], e["rsession_id"], e["type"], e["ts_ms"],
          TS_UTC, "test", e["summary"], json.dumps(e["payload"], ensure_ascii=False),
          updated_at, updated_at) for e in EVENTS])
    conn.commit()
    conn.close()
    return cfg


def _insert_event(cfg: LvdataMcpConfig, *, event_ref: str, type_: str, ts_ms: int,
                  payload: dict, updated_at: str, device: str,
                  session: str) -> None:
    conn = sqlite3.connect(cfg.data.resolved_db_path)
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "ts_utc, source, summary, payload, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (event_ref, device, session, type_, ts_ms, TS_UTC, "test", "x",
         json.dumps(payload, ensure_ascii=False), updated_at, updated_at))
    conn.commit()
    conn.close()


def _rows(cfg: LvdataMcpConfig, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(cfg.data.resolved_db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _doc(cfg: LvdataMcpConfig, date: str) -> dict:
    r = _rows(cfg, "SELECT day_stats, status, utc_offset_min, day_start_ms, day_end_ms "
                   "FROM day_summary WHERE timezone = ? AND local_date = ?", (TZ, date))
    assert r, f"缺行: {date}"
    return json.loads(r[0]["day_stats"])


# ── 1. 等价性（golden）───────────────────────────────────────────────────────


def test_equivalence_with_bff_golden(tmp_path):
    """逐字段等于 BFF 原实现产出：counts / hot_slots / images（含 media URL 规则）。"""
    cfg = _build(tmp_path / "data")
    DayStats(cfg).run(force=True)
    for date, want in sorted(GOLDEN["days"].items()):
        got = _doc(cfg, date)
        assert got["counts"] == want["counts"], date
        assert got["hot_slots"] == want["hot_slots"], date
        # images：存储形态为原始字段（E5），media_url 由 BFF 按 §4.4 规则组装
        assert [i["frame_id"] for i in got["images"]] == \
               [i["frame_id"] for i in want["images"]], date
        for g, w in zip(got["images"], want["images"]):
            assert {k: g[k] for k in ("ts_ms", "event_ref", "window_id")} == \
                   {k: w[k] for k in ("ts_ms", "event_ref", "window_id")}, date
            url = (f"/api/mylife/media/{g['device_uuid']}/{g['rsession_id']}"
                   f"/video/{g['frame_id']}.jpg")
            assert url == w["media_url"], date
    # 无事件日不落行
    assert not _rows(cfg, "SELECT 1 FROM day_summary WHERE local_date = ?",
                     ("2026-08-21",))


# ── 2. 游标 / 幂等 / 快照上界 ───────────────────────────────────────────────


def test_cursor_advances_and_incremental_picks_new_events(tmp_path, monkeypatch):
    cfg = _build(tmp_path / "data")
    t1 = "2026-09-01T00:00:00.000Z"
    monkeypatch.setattr(ds_mod, "now_iso", lambda: t1)
    assert DayStats(cfg).run()["days"] == 4            # 首轮：游标 '' → 全量
    cur = _rows(cfg, "SELECT last_scan_at FROM day_stats_state WHERE timezone = ?",
                (TZ,))[0]["last_scan_at"]
    assert cur == t1                                    # 完成推进（E13）

    assert DayStats(cfg).run()["days"] == 0             # 无新事件 → 无候选

    t2 = "2026-09-01T01:00:00.000Z"
    monkeypatch.setattr(ds_mod, "now_iso", lambda: t2)
    _insert_event(cfg, event_ref="late:chat:1", type_="chat",
                  ts_ms=EVENTS[1]["ts_ms"] + 10_000, payload={},
                  updated_at=t2, device=EVENTS[1]["device_uuid"],
                  session=EVENTS[1]["rsession_id"])
    assert DayStats(cfg).run()["days"] == 1             # 只有该日被重算
    assert _doc(cfg, "2026-08-17")["counts"]["chat"] == 6   # 5 + 迟到 1


def test_snapshot_upper_bound_excludes_later_events(tmp_path, monkeypatch):
    """日窗查询带 updated_at <= T_scan：晚于 T_scan 的事件本轮不计，下轮必见。"""
    cfg = _build(tmp_path / "data")
    t1 = "2026-09-01T00:00:00.000Z"
    later = "2026-09-01T02:00:00.000Z"
    _insert_event(cfg, event_ref="future:chat:1", type_="chat",
                  ts_ms=EVENTS[1]["ts_ms"] + 1, payload={},
                  updated_at=later, device=EVENTS[1]["device_uuid"],
                  session=EVENTS[1]["rsession_id"])
    monkeypatch.setattr(ds_mod, "now_iso", lambda: t1)
    DayStats(cfg).run()
    assert _doc(cfg, "2026-08-17")["counts"]["chat"] == 5   # 未见 later 事件

    t3 = "2026-09-01T03:00:00.000Z"
    monkeypatch.setattr(ds_mod, "now_iso", lambda: t3)
    assert DayStats(cfg).run()["days"] == 1                 # 游标 t1 < later → 候选
    assert _doc(cfg, "2026-08-17")["counts"]["chat"] == 6


def test_idempotent_rows_unchanged(tmp_path, monkeypatch):
    cfg = _build(tmp_path / "data")
    t = "2026-09-01T00:00:00.000Z"
    monkeypatch.setattr(ds_mod, "now_iso", lambda: t)
    DayStats(cfg).run(force=True)
    before = {d: _doc(cfg, d) for d in GOLDEN["days"]}
    DayStats(cfg).run(force=True)                        # 同 T 再跑：结果一致
    assert {d: _doc(cfg, d) for d in GOLDEN["days"]} == before


def test_force_recomputes_and_advances_cursor(tmp_path, monkeypatch):
    cfg = _build(tmp_path / "data")
    monkeypatch.setattr(ds_mod, "now_iso", lambda: "2026-09-01T00:00:00.000Z")
    DayStats(cfg).run()
    conn = sqlite3.connect(cfg.data.resolved_db_path)
    conn.execute("UPDATE day_summary SET day_stats = '{}'")
    conn.commit()
    conn.close()
    t2 = "2026-09-01T05:00:00.000Z"
    monkeypatch.setattr(ds_mod, "now_iso", lambda: t2)
    assert DayStats(cfg).run(force=True)["days"] == 4     # 忽略游标全量重算
    assert _doc(cfg, "2026-08-17")["counts"]["chat"] == 5
    assert _rows(cfg, "SELECT last_scan_at FROM day_stats_state WHERE timezone = ?",
                 (TZ,))[0]["last_scan_at"] == t2


def test_target_is_ignored_global_run(tmp_path, monkeypatch):
    """E15：带 session_id（target）与不带等价——始终全局。"""
    cfg = _build(tmp_path / "data")
    monkeypatch.setattr(ds_mod, "now_iso", lambda: "2026-09-01T00:00:00.000Z")
    stat = DayStats(cfg).run(target=EVENTS[0]["rsession_id"], force=True)
    assert stat["days"] == 4
    assert _doc(cfg, "2026-08-20")["counts"]["video"] == 18   # 非该 session 的日也算


# ── 3. 行写入语义（E12 / INSERT 列 / 保留 LLM 行）──────────────────────────


def test_insert_fills_real_day_bounds(tmp_path, monkeypatch):
    cfg = _build(tmp_path / "data")
    monkeypatch.setattr(ds_mod, "now_iso", lambda: "2026-09-01T00:00:00.000Z")
    DayStats(cfg).run()
    r = _rows(cfg, "SELECT status, utc_offset_min, day_start_ms, day_end_ms, event_count, "
                   "fail_count, generated_at FROM day_summary WHERE local_date = ?",
              ("2026-08-17",))[0]
    assert r["status"] == "pending" and r["fail_count"] == 0
    assert r["utc_offset_min"] == 480                    # 非 0 占位
    assert r["day_start_ms"] > 0 and r["day_end_ms"] > r["day_start_ms"]
    assert r["generated_at"] is None


def test_dedup_only_day_not_inserted(tmp_path, monkeypatch):
    """E12：全部为「去重 audio」（transcript 非空）的日不落新行；已有行仍 UPDATE。"""
    cfg = _build(tmp_path / "data")
    monkeypatch.setattr(ds_mod, "now_iso", lambda: "2026-09-01T00:00:00.000Z")
    day = "2026-08-25"                      # 该本地日无其它事件
    ts = day_bounds_ms(day, TZ)[0] + 12 * 3600_000
    _insert_event(cfg, event_ref="dup:audio:1", type_="audio", ts_ms=ts,
                  payload={"segment_id": "s", "transcript": "有文字"},
                  updated_at=BASE_UPDATED, device=EVENTS[1]["device_uuid"],
                  session=EVENTS[1]["rsession_id"])
    DayStats(cfg).run(force=True)
    assert not _rows(cfg, "SELECT 1 FROM day_summary WHERE local_date = ?", (day,))

    # 已有行（如 LLM 已生成）仍 UPDATE day_stats——去重只挡 INSERT
    conn = sqlite3.connect(cfg.data.resolved_db_path)
    conn.execute("INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
                 "day_start_ms, day_end_ms, day_summary, status, event_count, "
                 "fail_count, generated_at, day_stats) VALUES (?,?,480,0,0,'{}',"
                 "'ok',1,0,'2026-08-31T00:00:00.000Z','{\"old\":1}')", (TZ, day))
    conn.commit()
    conn.close()
    DayStats(cfg).run(force=True)
    assert json.loads(_rows(cfg, "SELECT day_stats FROM day_summary WHERE local_date = ?",
                            (day,))[0]["day_stats"])["counts"] == {"audio": 1}


def test_existing_llm_row_only_day_stats_touched(tmp_path, monkeypatch):
    cfg = _build(tmp_path / "data")
    conn = sqlite3.connect(cfg.data.resolved_db_path)
    conn.execute("INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
                 "day_start_ms, day_end_ms, day_summary, status, event_count, "
                 "fail_count, generated_at, day_stats) VALUES "
                 "(?,?,480,0,0,'{\"summary\":\"keep\"}','ok',5,0,"
                 "'2026-08-31T00:00:00.000Z','{}')", (TZ, "2026-08-17"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(ds_mod, "now_iso", lambda: "2026-09-01T00:00:00.000Z")
    DayStats(cfg).run(force=True)
    r = _rows(cfg, "SELECT day_summary, status, event_count, generated_at, day_stats "
                   "FROM day_summary WHERE local_date = ?", ("2026-08-17",))[0]
    assert json.loads(r["day_summary"]) == {"summary": "keep"}   # LLM 内容不动
    assert (r["status"], r["event_count"]) == ("ok", 5)
    assert r["generated_at"] == "2026-08-31T00:00:00.000Z"
    assert json.loads(r["day_stats"])["counts"]["chat"] == 5     # 只更新 day_stats


# ── 4. 时区边界与派生函数单测 ────────────────────────────────────────────────


def test_fixed_offset_timezone_does_not_crash(tmp_path, monkeypatch):
    """固定偏移规范形（"+08:00"，canonicalize_timezone 合法输出、可入 timezones 表）
    不得让 stage 崩（`ZoneInfo("+08:00")` 会抛 ZoneInfoNotFoundError——2026-09-25 修复）。"""
    cfg = _build(tmp_path / "data")
    conn = sqlite3.connect(cfg.data.resolved_db_path)
    conn.execute("UPDATE timezones SET enabled = 0")        # 只留固定偏移为活跃
    conn.execute("INSERT INTO timezones (timezone, enabled) VALUES ('+08:00', 1)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(ds_mod, "now_iso", lambda: "2026-09-01T00:00:00.000Z")
    stat = DayStats(cfg).run(force=True)
    assert stat["timezones"] == ["+08:00"]
    assert stat["days"] == 4
    r = _rows(cfg, "SELECT utc_offset_min FROM day_summary WHERE timezone = ? "
                   "AND local_date = ?", ("+08:00", "2026-08-17"))[0]
    assert r["utc_offset_min"] == 480




@pytest.mark.parametrize("gap_ms,expect_segments", [
    (60 * 60 * 1000, 1),          # 恰好 60min → 不分段
    (60 * 60 * 1000 + 1, 2),      # 60min + 1ms → 分段
])
def test_hot_slot_gap_boundary(gap_ms, expect_segments):
    t = 1_700_000_000_000
    events = [{"type": "chat", "ts_ms": t}, {"type": "chat", "ts_ms": t + gap_ms}]
    assert len(ds_mod.derive_hot_slots(events)) == expect_segments


def test_merge_tie_picks_leftmost():
    """>3 段且存在相等最小间隔：合并最左的一对（确定性）。"""
    t = 1_700_000_000_000
    mins = [0, 30, 60, 150, 240]           # 段间隔 30/30/90/90 → 首步并列取最左
    events = [{"type": "chat", "ts_ms": t + m * 60_000} for m in mins]
    slots = ds_mod.derive_hot_slots(events)
    assert len(slots) == 3
    assert slots[0]["start_ms"] == t and slots[0]["end_ms"] == t + 60 * 60_000
    assert slots[0]["count"] == 3


def test_images_pool_cap_and_no_url():
    t = 1_700_000_000_000
    events = [{"type": "video", "ts_ms": t + i * 60_000, "event_ref": f"e{i}",
               "frame_id": f"f{i}", "window_id": "w", "device_uuid": "d",
               "rsession_id": "s"} for i in range(20)]
    imgs = ds_mod.derive_images(events)
    assert len(imgs) == 15
    assert [i["ts_ms"] for i in imgs] == sorted(i["ts_ms"] for i in imgs)
    assert "media_url" not in imgs[0]      # E5：URL 由 BFF 组装
