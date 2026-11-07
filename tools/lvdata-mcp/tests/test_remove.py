"""删除 video 事件接口（docs/bff_service_remove.md §6.1）。

- EventRemover.remove：部分成功（D7）、window 归零连带（跨 session 计数）、
  day_stats 重算（部分/删空/非活跃时区/游标不变）、文件面与护栏、
  T2 异常（D10）、重试幂等；
- api_remove handler：格式 400 / 200 语义 / asyncio.to_thread 卸载（§3 执行载体）。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from conftest import DEVICE, FRAME, SID, TS, FakeEmbeddingProvider, make_sample  # noqa: E402
from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.remove import EventRemover
from lvdata_mcp.store import DataStore
from lvdata_mcp.summary.day_stats import DayStats, _utc_offset_min
from lvdata_mcp.timeutil import day_bounds_ms, ms_to_local_date
from lvdata_mcp.vec import VecIndex

TZ = "Asia/Shanghai"
VREF = f"{SID}:video:{FRAME}"      # 样例帧（window w1，样例窗内唯一 video）
WREF = f"{SID}:window:w1"
DAY = 86400_000


# ── 夹具 ─────────────────────────────────────────────────────────────────────

def _root(tmp_path: Path) -> Path:
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    return root


def _cfg(root: Path) -> LvdataMcpConfig:
    return LvdataMcpConfig(data={"root_dir": str(root)},
                           watcher={"timezone": TZ})


def _conn(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    return conn


def _add_video(root: Path, *, rsid: str, frame: str, window: str, ts: int,
               write_file: bool = True) -> str:
    """插一条 video 事件（session 行 + files 行 + 磁盘 jpg），返回 event_ref。"""
    p = root / DEVICE / rsid / "video" / f"{frame}.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    if write_file:
        p.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")
    ref = f"{rsid}:video:{frame}"
    with _conn(root) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recording_sessions (rsession_id, device_uuid, "
            "session_date, status, root_path) VALUES (?,?,?,?,?)",
            (rsid, DEVICE, "2026-07-24", "finalized", str(root)))
        conn.execute(
            "INSERT INTO files (rsession_id, device_uuid, file_type, file_ext, "
            "file_path, file_size, uuid_ts_ms, analysis_status) "
            "VALUES (?,?,'video','jpg',?,?,?, 'ok')",
            (rsid, DEVICE, str(p), 123, ts))
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
            "ts_utc, source, summary, payload) VALUES (?,?,?,'video',?,'x','files',?,?)",
            (ref, DEVICE, rsid, ts, f"caption {frame}",
             json.dumps({"frame_id": frame, "window_id": window,
                         "caption": f"caption {frame}"})))
        conn.commit()
    return ref


def _add_window(root: Path, *, rsid: str, window: str, ts: int) -> str:
    ref = f"{rsid}:window:{window}"
    with _conn(root) as conn:
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
            "ts_utc, source, summary, payload) VALUES (?,?,?,'window',?,'x','files',?,?)",
            (ref, DEVICE, rsid, ts, f"win {window}",
             json.dumps({"window_id": window, "window_start_ms": ts,
                         "window_caption": f"win {window}", "frame_count": 1,
                         "key_frame_count": 1})))
        conn.commit()
    return ref


def _build_fts(root: Path) -> None:
    from lvdata_mcp import index as index_mod
    with _conn(root) as conn:
        index_mod.rebuild_all(conn, DataStore(_cfg(root)))


def _build_vec(root: Path) -> VecIndex:
    vec = VecIndex(_cfg(root), DataStore(_cfg(root)), FakeEmbeddingProvider())
    asyncio.run(vec.sync_changed())
    return vec


def _fts_refs(root: Path) -> set[str]:
    with _conn(root) as conn:
        return {r[0] for r in conn.execute("SELECT event_ref FROM search_docs")}


def _vec_refs(root: Path) -> set[str]:
    p = root / "lvdata_index.db"
    if not p.exists():
        return set()
    conn = sqlite3.connect(p)
    try:
        return {r[0] for r in conn.execute("SELECT event_ref FROM vec_state")}
    finally:
        conn.close()


def _event_refs(root: Path) -> set[str]:
    with _conn(root) as conn:
        return {r[0] for r in conn.execute("SELECT event_ref FROM events")}


def _files_rows(root: Path) -> list[sqlite3.Row]:
    with _conn(root) as conn:
        return conn.execute("SELECT file_id, file_path FROM files "
                            "WHERE file_type='video'").fetchall()


def _frame_path(root: Path, rsid: str, frame: str) -> Path:
    return root / DEVICE / rsid / "video" / f"{frame}.jpg"


def _day_stats_doc(root: Path, tz: str, d: str) -> dict | None:
    with _conn(root) as conn:
        r = conn.execute("SELECT day_stats FROM day_summary "
                         "WHERE timezone = ? AND local_date = ?", (tz, d)).fetchone()
    return json.loads(r["day_stats"]) if r else None


def _seed_day_row(root: Path, tz: str, d: str, *, status: str = "ok") -> None:
    """手工落一行 day_summary（供非活跃时区/自定义日场景）。"""
    start, end = day_bounds_ms(d, tz)
    with _conn(root) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO day_summary (timezone, local_date, utc_offset_min, "
            "day_start_ms, day_end_ms, day_summary, status, event_count, fail_count, "
            "day_stats) VALUES (?,?,?,?,?,'{}',?,0,0,'{}')",
            (tz, d, _utc_offset_min(tz, d), start, end, status))
        conn.commit()


# ── 1-2. 主路径 / 空窗连带 ──────────────────────────────────────────────────

def test_remove_video_keeps_window_when_siblings_remain(tmp_path):
    """窗内还有同窗 video → window 行保留；目标帧的事件/jpg/files/FTS/vec 全消失。"""
    root = _root(tmp_path)
    f2 = "019fabe5-b00e-7b8d-8000-fffffffff001"
    ref2 = _add_video(root, rsid=SID, frame=f2, window="w1", ts=TS + 11000)
    _build_fts(root)
    vec = _build_vec(root)

    r = EventRemover(_cfg(root), vec).remove([VREF])

    assert r["removed"] == [VREF]
    assert r["window_events_removed"] == []
    assert r["skipped"] == [] and r["images_removed"] == [FRAME]
    refs = _event_refs(root)
    assert VREF not in refs and WREF in refs and ref2 in refs
    assert not _frame_path(root, SID, FRAME).exists()
    assert all(row["file_path"] != str(_frame_path(root, SID, FRAME))
               for row in _files_rows(root))
    assert VREF not in _fts_refs(root) and WREF in _fts_refs(root)
    assert VREF not in _vec_refs(root) and WREF in _vec_refs(root)


def test_remove_last_video_deletes_window(tmp_path):
    """样例窗只有一条 video → 删掉后 window 行连带删除（事件/FTS/vec 同清）。"""
    root = _root(tmp_path)
    _build_fts(root)
    vec = _build_vec(root)

    r = EventRemover(_cfg(root), vec).remove([VREF])

    assert r["removed"] == [VREF]
    assert r["window_events_removed"] == [WREF]
    refs = _event_refs(root)
    assert VREF not in refs and WREF not in refs
    assert VREF not in _fts_refs(root) and WREF not in _fts_refs(root)
    assert VREF not in _vec_refs(root) and WREF not in _vec_refs(root)


# ── 3. 跨 session 计数 ──────────────────────────────────────────────────────

def test_window_count_across_sessions(tmp_path):
    """同窗 video 跨 session：删一条窗保留，删到归零才删 window 行。"""
    root = _root(tmp_path)
    sid2 = "019fabe0-3b0f-7947-8000-000000000002"
    f2 = "019fabe5-b00e-7b8d-8000-fffffffff002"
    ref2 = _add_video(root, rsid=sid2, frame=f2, window="w1", ts=TS + 11000)

    r1 = EventRemover(_cfg(root), None).remove([VREF])
    assert r1["window_events_removed"] == []
    assert WREF in _event_refs(root)

    r2 = EventRemover(_cfg(root), None).remove([ref2])
    assert r2["window_events_removed"] == [WREF]
    assert WREF not in _event_refs(root)


# ── 4. day_stats 重算 ───────────────────────────────────────────────────────

def test_day_stats_partial_and_empty_ok_zeroing(tmp_path):
    """部分删除按剩余事件重算；删空 ok 行清零（D3）；游标不变；非活跃时区同算。"""
    root = _root(tmp_path)
    cfg = _cfg(root)
    DayStats(cfg).run(force=True)                      # 建行（pending 占位）
    d = ms_to_local_date(TS, TZ)
    with _conn(root) as conn:
        conn.execute("UPDATE day_summary SET status='ok'")
        conn.commit()
    # 非活跃时区（不在 active 集合、仅手工有行）也要被重算
    d_ny = ms_to_local_date(TS, "America/New_York")
    _seed_day_row(root, "America/New_York", d_ny, status="ok")

    before = _day_stats_doc(root, TZ, d)
    assert before["counts"].get("video") == 1
    with _conn(root) as conn:
        cursor_before = [tuple(r) for r in conn.execute(
            "SELECT timezone, last_scan_at FROM day_stats_state")]

    r = EventRemover(cfg, None).remove([VREF])

    after = _day_stats_doc(root, TZ, d)
    assert after["counts"].get("video") is None        # 唯一 video 已删
    assert after["counts"].get("window") is None       # 窗归零连带删
    assert all(i["frame_id"] != FRAME for i in after["images"])
    ny = _day_stats_doc(root, "America/New_York", d_ny)
    assert ny is not None and ny["counts"] == after["counts"]   # 非活跃时区同算
    assert ny["counts"].get("video") is None
    pairs = {(x["timezone"], x["local_date"]) for x in r["days_recomputed"]}
    assert (TZ, d) in pairs and ("America/New_York", d_ny) in pairs
    with _conn(root) as conn:
        cursor_after = [tuple(r) for r in conn.execute(
            "SELECT timezone, last_scan_at FROM day_stats_state")]
    assert cursor_after == cursor_before               # 游标不变


def test_day_stats_empty_non_ok_row_deleted(tmp_path):
    """删空且非 ok 行 → 删行（镜像 stage 竞态兜底）；video-only 日全删后行消失。"""
    root = _root(tmp_path)
    cfg = _cfg(root)
    ts2 = TS + DAY
    f2 = "019fabe5-b00e-7b8d-8000-fffffffff010"
    ref2 = _add_video(root, rsid=SID, frame=f2, window="w2", ts=ts2)
    w2 = _add_window(root, rsid=SID, window="w2", ts=ts2)
    DayStats(cfg).run(force=True)
    d2 = ms_to_local_date(ts2, TZ)
    assert _day_stats_doc(root, TZ, d2) is not None    # 该日只有 f2/w2

    r = EventRemover(cfg, None).remove([ref2])

    assert r["window_events_removed"] == [w2]
    assert _day_stats_doc(root, TZ, d2) is None        # pending 行被删
    assert {"timezone": TZ, "local_date": d2} in r["days_recomputed"]


def test_window_ts_cross_day_recomputed(tmp_path):
    """window 行 ts 与帧不同日 → days_recomputed 覆盖两日。"""
    root = _root(tmp_path)
    cfg = _cfg(root)
    ts3 = TS + 3 * DAY
    f3 = "019fabe5-b00e-7b8d-8000-fffffffff020"
    ref3 = _add_video(root, rsid=SID, frame=f3, window="w3", ts=ts3)
    w3 = _add_window(root, rsid=SID, window="w3", ts=ts3 - DAY)   # 前一日
    _seed_day_row(root, TZ, ms_to_local_date(ts3, TZ), status="pending")
    _seed_day_row(root, TZ, ms_to_local_date(ts3 - DAY, TZ), status="pending")

    r = EventRemover(cfg, None).remove([ref3])

    assert r["window_events_removed"] == [w3]
    days = {x["local_date"] for x in r["days_recomputed"] if x["timezone"] == TZ}
    assert days == {ms_to_local_date(ts3, TZ), ms_to_local_date(ts3 - DAY, TZ)}


# ── 6-7. 部分成功 / 重试幂等 ────────────────────────────────────────────────

def test_partial_success_mixed_batch(tmp_path):
    """混合批次：有效 video 删除 + 缺失 not_found + 非 video not_video，互不阻塞。"""
    root = _root(tmp_path)
    missing = f"{SID}:video:no-such-frame"
    r = EventRemover(_cfg(root), None).remove([VREF, missing, f"{SID}:chat:m1"])

    assert r["removed"] == [VREF]
    assert r["skipped"] == [{"event_ref": missing, "reason": "not_found"},
                            {"event_ref": f"{SID}:chat:m1", "reason": "not_video"}]
    assert not _frame_path(root, SID, FRAME).exists()   # 有效那条级联生效
    assert f"{SID}:chat:m1" in _event_refs(root)        # 非 video 未动


def test_retry_idempotent(tmp_path):
    """重发同一批：第二次全 skipped: not_found、零副作用。"""
    root = _root(tmp_path)
    EventRemover(_cfg(root), None).remove([VREF])
    r2 = EventRemover(_cfg(root), None).remove([VREF])
    assert r2["removed"] == [] and r2["window_events_removed"] == []
    assert r2["skipped"] == [{"event_ref": VREF, "reason": "not_found"}]


def test_duplicate_refs_deduped(tmp_path):
    """重复 ref 保序去重：只删一次，removed/images/windows 均无重复条目。"""
    root = _root(tmp_path)
    r = EventRemover(_cfg(root), None).remove([VREF, VREF, VREF])
    assert r["removed"] == [VREF]
    assert r["window_events_removed"] == [WREF]
    assert r["images_removed"] == [FRAME]
    assert r["skipped"] == []


def test_two_windows_emptied_in_one_batch(tmp_path):
    """同批把两个窗口各自删空 → 两个 window 行都删除（防游标迭代漏删回归）。"""
    root = _root(tmp_path)
    f2 = "019fabe5-b00e-7b8d-8000-fffffffff030"
    ref2 = _add_video(root, rsid=SID, frame=f2, window="w2", ts=TS + DAY)
    w2 = _add_window(root, rsid=SID, window="w2", ts=TS + DAY)

    r = EventRemover(_cfg(root), None).remove([VREF, ref2])

    assert sorted(r["window_events_removed"]) == sorted([WREF, w2])
    refs = _event_refs(root)
    assert WREF not in refs and w2 not in refs


# ── 8. 无 provider / 无 vec 库 ──────────────────────────────────────────────

def test_no_vec_index_and_missing_vec_db(tmp_path):
    """vec_index=None 与向量库不存在：删除照常、无 vectors_pending。"""
    root = _root(tmp_path)
    r1 = EventRemover(_cfg(root), None).remove([VREF])
    assert "vectors_pending" not in r1

    root2 = tmp_path / "lvdata2"
    root2.mkdir()
    make_sample(root2)
    vec = VecIndex(_cfg(root2), DataStore(_cfg(root2)), FakeEmbeddingProvider())
    assert not (root2 / "lvdata_index.db").exists()     # 未建库
    r2 = EventRemover(_cfg(root2), vec).remove([VREF])
    assert "vectors_pending" not in r2 and r2["removed"] == [VREF]


# ── 9-10. 文件面 / 路径护栏 ─────────────────────────────────────────────────

def test_file_missing_still_deletes_row(tmp_path):
    """jpg 已缺失（missing_ok）→ files 行仍删、计入 images_removed。"""
    root = _root(tmp_path)
    _frame_path(root, SID, FRAME).unlink()
    r = EventRemover(_cfg(root), None).remove([VREF])
    assert r["images_removed"] == [FRAME] and r["images_pending"] == []
    assert all(row["file_path"] != str(_frame_path(root, SID, FRAME))
               for row in _files_rows(root))


def test_unlink_failure_keeps_row_and_reports_pending(tmp_path, monkeypatch):
    """unlink 抛 OSError → files 行保留 + images_pending（prune 兜底自愈）。"""
    root = _root(tmp_path)
    real_unlink = Path.unlink

    def fake_unlink(self, missing_ok=False):
        if self.name == f"{FRAME}.jpg":
            raise OSError("boom")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    r = EventRemover(_cfg(root), None).remove([VREF])

    assert r["images_pending"] == [FRAME] and r["images_removed"] == []
    assert _frame_path(root, SID, FRAME).exists()
    assert any(row["file_path"] == str(_frame_path(root, SID, FRAME))
               for row in _files_rows(root))


def test_file_face_db_error_aborted_to_pending(tmp_path):
    """files 行 DELETE 失败（触发器模拟）→ 不 500：removed 空、全部进 pending，
    交 prune 自愈（T1 已提交，D9/D10 同哲学）。"""
    root = _root(tmp_path)
    with _conn(root) as conn:
        conn.execute("CREATE TRIGGER files_boom BEFORE DELETE ON files "
                     "BEGIN SELECT RAISE(ABORT, 'boom'); END")
        conn.commit()

    r = EventRemover(_cfg(root), None).remove([VREF])

    assert r["removed"] == [VREF]          # T1 级联照常
    assert r["window_events_removed"] == [WREF]
    assert r["images_removed"] == []
    assert r["images_pending"] == [FRAME]  # 未完成 → prune 兜底
    assert any(row["file_path"] == str(_frame_path(root, SID, FRAME))
               for row in _files_rows(root))   # files 行随回滚保留


def test_path_guard_outside_root(tmp_path):
    """files.file_path 越 root → 跳过（外部文件与行均保留）。"""
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    op = outside / f"{FRAME}.jpg"
    op.write_bytes(b"x")
    with _conn(root) as conn:
        conn.execute("UPDATE files SET file_path = ? WHERE file_type='video'",
                     (str(op),))
        conn.commit()

    r = EventRemover(_cfg(root), None).remove([VREF])

    assert op.exists()
    assert r["images_removed"] == [] and r["images_pending"] == []
    assert any(row["file_path"] == str(op) for row in _files_rows(root))


# ── 11. search_docs 缺失 ───────────────────────────────────────────────────

def test_missing_fts_index_skipped(tmp_path):
    """未建 search_docs（未 reindex）→ 跳过不报错。"""
    root = _root(tmp_path)
    r = EventRemover(_cfg(root), None).remove([VREF])
    assert r["removed"] == [VREF]
    with _conn(root) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='search_docs'").fetchone() is None


# ── 12. T2 异常（D10） ─────────────────────────────────────────────────────

def test_t2_exception_swallowed_and_selfheals(tmp_path, monkeypatch):
    """remove_refs 抛异常 → 200 语义 + vectors_pending；孤儿由 sync_changed 收敛。"""
    root = _root(tmp_path)
    vec = _build_vec(root)
    assert VREF in _vec_refs(root)

    def boom(refs):
        raise RuntimeError("vec boom")

    monkeypatch.setattr(vec, "remove_refs", boom)
    r = EventRemover(_cfg(root), vec).remove([VREF])

    assert r["vectors_pending"] is True
    assert r["removed"] == [VREF]                       # T1 级联照常
    assert not _frame_path(root, SID, FRAME).exists()   # 文件面照常
    assert VREF in _vec_refs(root)                      # 孤儿残留

    asyncio.run(vec.sync_changed())                     # 孤儿清扫收敛
    assert VREF not in _vec_refs(root)


# ── 13. api_remove handler（格式 400 / 200 / to_thread 卸载） ──────────────

def _client(root: Path):
    from starlette.testclient import TestClient
    from lvdata_mcp.server import create_app
    return TestClient(create_app(_cfg(root)))


def test_api_remove_format_validation(tmp_path):
    """请求级唯一硬错误 = 格式非法（空/超 50/元素非字符串/非 JSON 对象）→ 400。"""
    root = _root(tmp_path)
    with _client(root) as c:
        assert c.post("/api/events/remove", json={"event_refs": []}).status_code == 400
        assert c.post("/api/events/remove",
                      json={"event_refs": ["x"] * 51}).status_code == 400
        assert c.post("/api/events/remove",
                      json={"event_refs": [123]}).status_code == 400
        assert c.post("/api/events/remove", json={}).status_code == 400
        assert c.post("/api/events/remove", content=b"not-json",
                      headers={"content-type": "application/json"}).status_code == 400
        assert _event_refs(root) >= {VREF}              # 全部零副作用


def test_api_remove_partial_success_200(tmp_path):
    """混合批次 → 200 + removed/skipped 逐条（部分成功即成功）。"""
    root = _root(tmp_path)
    with _client(root) as c:
        resp = c.post("/api/events/remove",
                      json={"event_refs": [VREF, f"{SID}:video:ghost"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["removed"] == [VREF]
        assert body["skipped"] == [{"event_ref": f"{SID}:video:ghost",
                                    "reason": "not_found"}]
        assert body["window_events_removed"] == [WREF]


def test_api_remove_runs_via_to_thread(tmp_path, monkeypatch):
    """执行载体（§3）：api_remove 必须经 asyncio.to_thread 卸载，不得内联阻塞。"""
    import lvdata_mcp.server as server_mod

    calls: list = []
    real = asyncio.to_thread

    def spy(func, *a, **kw):
        calls.append(func)
        return real(func, *a, **kw)

    monkeypatch.setattr(server_mod.asyncio, "to_thread", spy)
    root = _root(tmp_path)
    with _client(root) as c:
        assert c.post("/api/events/remove",
                      json={"event_refs": [VREF]}).status_code == 200

    picked = [f for f in calls if getattr(f, "__name__", "") == "remove"]
    assert len(picked) == 1
    assert picked[0].__self__.__class__.__name__ == "EventRemover"
