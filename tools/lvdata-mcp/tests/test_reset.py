"""reset CLI（docs/data/lvdata_mcp_cli_reset.md §8）。

覆盖：日期解析、作用域（fallback 仅 session_date=''）、文件分类、已知事件 purge
与未知类型保留、跨 session window 归零、FTS/向量/day_summary/day_stats/derived、
状态重置、dry-run、幂等、server 探测与旧 FTS schema 中止。
"""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import threading
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import FakeEmbeddingProvider

from lvdata_mcp import index as index_mod
from lvdata_mcp import reset as reset_mod
from lvdata_mcp.config import LvdataMcpConfig, load_config
from lvdata_mcp.ddl import ensure_schema
from lvdata_mcp.reset import (_open_db, _reset_days, parse_date_spec,
                              run_reset, select_sessions, server_status)
from lvdata_mcp.store import DataStore
from lvdata_mcp.summary.day import DaySummary
from lvdata_mcp.timeutil import day_bounds_ms, ms_to_local_date
from lvdata_mcp.vec import VecIndex

TZ = "Asia/Shanghai"
DEVICE = "b53985c7-0000-4000-8000-000000000001"
DAY = "2026-09-28"
T0 = day_bounds_ms(DAY, TZ)[0] + 12 * 3600 * 1000
DAY_MS = 86400_000
OUT_DAY = "2026-09-20"
NY = "America/New_York"

S1 = "session-1"          # primary 命中
S2 = "session-2"          # session_date 在范围外，video 事件在范围内（不得入选）
S3 = "session-3"          # session_date 在范围外，audio 事件在范围内（不得入选）
S4 = "session-4"          # session_date=''，media 回退命中
S5 = "session-5"          # session_date=''，events 回退命中


@pytest.fixture(autouse=True)
def _server_stopped(monkeypatch):
    """多数测试不启动 server；避免 probe 打到本机真实 8090。"""
    monkeypatch.setattr("lvdata_mcp.reset.server_status",
                        lambda _cfg: "stopped")


def _cfg(root: Path) -> LvdataMcpConfig:
    return LvdataMcpConfig(data={"root_dir": str(root)},
                           watcher={"timezone": TZ})


def _conn(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    return conn


def _add_session(conn: sqlite3.Connection, root: Path, sid: str,
                 date: str) -> None:
    conn.execute(
        "INSERT INTO recording_sessions (rsession_id, device_uuid, session_date, "
        "status, root_path) VALUES (?,?,?,'finalized',?)",
        (sid, DEVICE, date, str(root)))


_FILE_DIR = {"audio": "audio", "video": "video", "chat_history": "chat",
             "chat_transcribe": "chat", "chat_translate": "chat", "meta": ""}
_FILE_EXT = {"audio": "ogg", "video": "jpg", "chat_history": "jsonl",
             "chat_transcribe": "jsonl", "chat_translate": "jsonl",
             "meta": "json"}


def _add_file(conn: sqlite3.Connection, root: Path, sid: str, ftype: str,
              name: str, status: str, ts: int | None, *,
              write: bool = True) -> Path:
    ext = _FILE_EXT[ftype]
    path = root / DEVICE / sid / _FILE_DIR[ftype] / f"{name}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if write:
        path.write_bytes(b"data")
    conn.execute(
        "INSERT INTO files (rsession_id, device_uuid, file_type, file_ext, "
        "file_path, file_size, uuid_ts_ms, analysis_status, src_updated_at) "
        "VALUES (?,?,?,?,?,?,?,?, 'x')",
        (sid, DEVICE, ftype, ext, str(path), len(b"data") if write else 100,
         ts, status))
    return path


def _add_event(conn: sqlite3.Connection, sid: str, etype: str, name: str,
               ts: int, payload: dict | None = None) -> str:
    ref = f"{sid}:{etype}:{name}"
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "ts_utc, source, summary, payload) VALUES (?,?,?,?,?,'x','test',?,?)",
        (ref, DEVICE, sid, etype, ts, f"{etype} {name}",
         json.dumps(payload or {})))
    return ref


def _seed(root: Path) -> LvdataMcpConfig:
    """构造多 session 样本；返回 cfg。"""
    root.mkdir(parents=True, exist_ok=True)
    db = root / "lvdata_inventory.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn, default_timezone=TZ, timezones=[])
    for sid, date in ((S1, DAY), (S2, OUT_DAY), (S3, "2026-09-19"),
                      (S4, ""), (S5, "")):
        _add_session(conn, root, sid, date)

    # S1：媒体/chat/unknown/window
    _add_file(conn, root, S1, "audio", "a0", "ok", T0 - 2 * DAY_MS)
    _add_event(conn, S1, "audio", "a0", T0 - 2 * DAY_MS,
               {"segment_id": "a0", "transcript": "x"})
    _add_file(conn, root, S1, "audio", "a1", "ok", T0)
    _add_event(conn, S1, "audio", "a1", T0,
               {"segment_id": "a1", "transcript": "x"})
    _add_file(conn, root, S1, "audio", "a2", "ok", T0 + 1000)
    _add_file(conn, root, S1, "audio", "a3", "failed", T0 + 2000)
    _add_file(conn, root, S1, "video", "f1", "ok", T0 + 5000)
    _add_event(conn, S1, "video", "f1", T0 + 5000,
               {"frame_id": "f1", "window_id": "w1", "caption": "f1"})
    _add_file(conn, root, S1, "video", "f2", "ok", T0 + 6000)
    _add_file(conn, root, S1, "video", "f3", "pending", T0 + 7000)
    _add_file(conn, root, S1, "video", "f4", "ok", T0 + 8000)
    _add_event(conn, S1, "video", "f4", T0 + 8000,
               {"frame_id": "f4", "window_id": "w2", "caption": "f4"})
    _add_file(conn, root, S1, "chat_history", "history", "na", None)
    _add_event(conn, S1, "chat", "c1", T0,
               {"role": "user", "text": "hello", "chat_session_id": "cs"})
    _add_file(conn, root, S1, "meta", "session", "na", None)
    _add_event(conn, S1, "custom", "x1", T0 + 500,
               {"foo": "bar"})
    _add_event(conn, S1, "window", "w1", T0 + 5000, {"window_id": "w1"})
    _add_event(conn, S1, "window", "w2", T0 + 8000, {"window_id": "w2"})
    _add_event(conn, S1, "window", "wbad", T0 + 9000, {})

    # S2（范围外）：与 w1 同窗的 video —— 保证 w1 window 事件保留
    _add_file(conn, root, S2, "video", "f9", "ok", T0 + 9000)
    _add_event(conn, S2, "video", "f9", T0 + 9000,
               {"frame_id": "f9", "window_id": "w1", "caption": "f9"})

    # S3（范围外）：事件在范围内，但不得被 fallback 拖入
    _add_file(conn, root, S3, "audio", "s3a", "ok", T0)
    _add_event(conn, S3, "audio", "s3a", T0,
               {"segment_id": "s3a", "transcript": "keep"})

    # S4：media 回退命中；ok + 无事件 → 删除
    _add_file(conn, root, S4, "audio", "s4a", "ok", T0)

    # S5：events 回退命中；chat 文件不删，只清水位
    _add_file(conn, root, S5, "chat_history", "history", "na", None)
    _add_event(conn, S5, "chat", "c5", T0,
               {"role": "user", "text": "fallback", "chat_session_id": "cs"})

    # day_summary：活跃时区（TZ）与非活跃时区（NY）
    start, end = day_bounds_ms(DAY, TZ)
    conn.execute(
        "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
        "day_start_ms, day_end_ms, day_summary, status, event_count, fail_count, "
        "day_stats) VALUES (?,?,480,?,?,?, 'ok', 1, 0, '{\"counts\":{\"video\":99}}')",
        (TZ, DAY, start, end, json.dumps({"summary": "old"})))
    ny_day = ms_to_local_date(T0, NY)
    ny_start, ny_end = day_bounds_ms(ny_day, NY)
    conn.execute(
        "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
        "day_start_ms, day_end_ms, day_summary, status, event_count, fail_count, "
        "day_stats) VALUES (?,?,0,?,?,?, 'ok', 1, 0, '{}')",
        (NY, ny_day, ny_start, ny_end, json.dumps({"summary": "ny"})))
    conn.executemany(
        "INSERT INTO day_deferred (timezone, local_date) VALUES (?,?)",
        [(TZ, DAY), (NY, ny_day)])

    # index_state / analysis_runs（审计保留）
    conn.executemany("INSERT INTO index_state VALUES (?, ?)",
                     [(S1, "2026-01-01T00:00:00.000Z"),
                      (S2, "2026-01-01T00:00:00.000Z")])
    a2_id = conn.execute("SELECT file_id FROM files WHERE file_path LIKE ?",
                         ("%/a2.ogg",)).fetchone()[0]
    conn.execute(
        "INSERT INTO analysis_runs (file_id, kind, status, started_at) "
        "VALUES (?, 'audio', 'ok', '2026-01-01T00:00:00.000Z')", (a2_id,))
    conn.commit()
    conn.close()

    # derived 审计 JSON
    derived = root / "derived" / DEVICE / S1
    for rel in ("audio/a1.json", "video/windows/w1.json",
                "video/frames/f1.json"):
        p = derived / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")

    cfg = _cfg(root)
    with _conn(root) as conn:
        index_mod.rebuild_all(conn, DataStore(cfg))
    return cfg


def _refs(root: Path) -> set[str]:
    with _conn(root) as conn:
        return {r[0] for r in conn.execute("SELECT event_ref FROM events")}


def _file_by_path(root: Path, path: Path) -> sqlite3.Row | None:
    with _conn(root) as conn:
        return conn.execute("SELECT * FROM files WHERE file_path = ?",
                            (str(path),)).fetchone()


def _fts_refs(root: Path) -> set[str]:
    with _conn(root) as conn:
        return {r[0] for r in conn.execute("SELECT event_ref FROM search_docs")}


# ── 日期 ───────────────────────────────────────────────────────────────────

def test_parse_date_spec():
    assert parse_date_spec("2026") == ("2026-01-01", "2026-12-31")
    assert parse_date_spec("2026-02") == ("2026-02-01", "2026-02-28")
    assert parse_date_spec("2024-02") == ("2024-02-01", "2024-02-29")
    assert parse_date_spec("2026-09-28") == ("2026-09-28", "2026-09-28")
    for bad in ("2026-2-3", "2026-13", "2026-02-30", "all", "", "0000"):
        with pytest.raises(ValueError):
            parse_date_spec(bad)


# ── 作用域 ─────────────────────────────────────────────────────────────────

def test_scope_fallback_only_empty_session_date(tmp_path):
    root = tmp_path / "lvdata"
    _seed(root)
    start_ms, end_ms = day_bounds_ms(DAY, TZ)
    with _conn(root) as conn:
        sessions = select_sessions(conn, DAY, DAY, start_ms, end_ms)
    assert S1 in sessions and sessions[S1] == ["primary"]
    assert S3 not in sessions          # 非空且在范围外：不被事件回退拖入
    assert S2 not in sessions          # 其 video 事件在范围内，同样不得入选
    assert sessions[S4] == ["media"]
    assert sessions[S5] == ["events"]


def test_plan_counts_out_of_range_and_unknown(tmp_path, capsys):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    rc = run_reset(cfg, date_spec=DAY, dry_run=True, as_json=True)
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["out_of_range"]["files"] >= 1
    assert plan["out_of_range"]["events"] >= 1
    assert plan["unknown_event_types"] == {"custom": 1}
    # a2（S1）+ s4a（S4 由 media 回退命中）；video = f2
    assert plan["delete_candidates"] == {"audio": 2, "video": 1}
    # 未知 type 不计入 purge
    assert "custom" not in plan["events_to_purge"]


# ── 主路径 ─────────────────────────────────────────────────────────────────

def test_reset_core(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    rc = run_reset(cfg, date_spec=DAY, yes=True, allow_running_server=True)
    assert rc == 0

    refs = _refs(root)
    # 已知非 window 事件被删；未知类型保留
    for ref in (f"{S1}:audio:a0", f"{S1}:audio:a1", f"{S1}:video:f1",
                f"{S1}:video:f4", f"{S1}:chat:c1",
                f"{S5}:chat:c5"):
        assert ref not in refs
    assert f"{S1}:custom:x1" in refs
    # w1 仍有 S2 的 video → 保留；w2（唯一 video 在 S1）与畸形 window 删除
    assert f"{S1}:window:w1" in refs
    assert f"{S1}:window:w2" not in refs
    assert f"{S1}:window:wbad" not in refs
    # 范围外 session 不受影响
    assert f"{S3}:audio:s3a" in refs
    assert f"{S2}:video:f9" in refs

    # 文件：ok+无事件删除；其余 media pending；chat 清水位；meta 不动
    for name in ("a2", "f2"):
        p = root / DEVICE / S1 / ("audio" if name.startswith("a") else "video") \
            / f"{name}.{'ogg' if name.startswith('a') else 'jpg'}"
        assert not p.exists() and _file_by_path(root, p) is None
    for name, sub in (("a0", "audio"), ("a1", "audio"), ("a3", "audio"),
                      ("f1", "video"), ("f3", "video"), ("f4", "video")):
        row = _file_by_path(root, root / DEVICE / S1 / sub
                            / f"{name}.{'ogg' if sub == 'audio' else 'jpg'}")
        assert row["analysis_status"] == "pending"
        assert row["fail_count"] == 0 and row["analyzed_at"] is None
    chat = _file_by_path(root, root / DEVICE / S1 / "chat" / "history.jsonl")
    assert chat["processed_offset"] == 0
    assert chat["processed_updated_at"] is None
    assert chat["analysis_status"] == "na"
    meta = _file_by_path(root, root / DEVICE / S1 / "session.json")
    assert meta["analysis_status"] == "na"
    # S4 的 ok+无事件文件被删除；S3 原样保留
    s4 = root / DEVICE / S4 / "audio" / "s4a.ogg"
    assert not s4.exists() and _file_by_path(root, s4) is None
    assert (root / DEVICE / S3 / "audio" / "s3a.ogg").exists()

    # FTS：删除事件不复活；未知/window/S2 事件仍可检索
    fts = _fts_refs(root)
    assert f"{S1}:chat:c1" not in fts and f"{S1}:window:w2" not in fts
    assert {f"{S1}:custom:x1", f"{S1}:window:w1", f"{S2}:video:f9"} <= fts

    # index_state：目标 session 行删除，其它保留
    with _conn(root) as conn:
        targets = {r[0] for r in conn.execute(
            "SELECT target FROM index_state")}
    assert S1 not in targets and S2 in targets

    # day_summary：所有时区（含非活跃）置 pending，再激活后可重试
    with _conn(root) as conn:
        row = conn.execute(
            "SELECT status, day_summary, day_stats FROM day_summary "
            "WHERE timezone=? AND local_date=?", (TZ, DAY)).fetchone()
        ny = conn.execute(
            "SELECT status, day_summary FROM day_summary "
            "WHERE timezone=? AND local_date=?",
            (NY, ms_to_local_date(T0, NY))).fetchone()
        deferred = conn.execute("SELECT COUNT(*) FROM day_deferred").fetchone()[0]
    assert row["status"] == "pending"
    assert json.loads(row["day_summary"]) == {}
    assert json.loads(row["day_stats"])["counts"]["video"] == 1
    assert json.loads(row["day_stats"])["counts"]["window"] == 1
    assert ny is not None and ny["status"] == "pending"
    assert json.loads(ny["day_summary"]) == {}
    assert deferred == 0

    # derived 审计 JSON 删除；analysis_runs 保留
    derived = root / "derived" / DEVICE / S1
    assert not (derived / "audio" / "a1.json").exists()
    assert not (derived / "video" / "windows" / "w1.json").exists()
    assert not (derived / "video" / "frames" / "f1.json").exists()
    with _conn(root) as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 1


def test_reset_idempotent(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    before = _refs(root)
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    assert _refs(root) == before


def test_unknown_type_kept_and_counted(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    rc = run_reset(cfg, date_spec=DAY, yes=True, as_json=True,
                   allow_running_server=True)
    assert rc == 0
    refs = _refs(root)
    assert f"{S1}:custom:x1" in refs
    with _conn(root) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE type='custom'").fetchone()[0] == 1


# ── 前置 / 安全 ────────────────────────────────────────────────────────────

def test_server_running_refused_unless_overridden(tmp_path, monkeypatch, capsys):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    monkeypatch.setattr("lvdata_mcp.reset.server_status",
                        lambda _cfg: "running")
    before = _refs(root)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 1
    assert _refs(root) == before          # 未执行
    capsys.readouterr()
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    assert _refs(root) != before
    assert "WARNING: server status is running" in capsys.readouterr().err


def test_missing_yes_returns_2_and_no_writes(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    before = _refs(root)
    assert run_reset(cfg, date_spec=DAY) == 2
    assert _refs(root) == before


def test_outdated_fts_schema_aborts(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    with _conn(root) as conn:
        conn.execute("DROP TABLE search_docs")
        conn.execute("CREATE VIRTUAL TABLE search_docs USING fts5(text)")
        conn.commit()
    before = _refs(root)
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 1
    assert _refs(root) == before


# ── 向量 / FTS 缺失 ────────────────────────────────────────────────────────

class _FakeVec:
    def __init__(self):
        self.removed: list[str] = []
        self.swept = 0

    def remove_refs(self, refs: list[str], *, create_schema: bool = True) -> int:
        self.removed += list(refs)
        return len(refs)

    def sweep_orphans(self, *, create_schema: bool = True) -> int:
        self.swept += 1
        return 0


def test_vectors_removed_and_swept(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    fake = _FakeVec()
    monkeypatch.setattr("lvdata_mcp.reset._build_vec", lambda _cfg: fake)
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    assert f"{S1}:chat:c1" in fake.removed and f"{S1}:window:w2" in fake.removed
    assert f"{S1}:custom:x1" not in fake.removed
    assert fake.swept == 1


def test_sweep_runs_after_remove_failure(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    fake = _FakeVec()

    def boom(*a, **k):
        raise RuntimeError("vec boom")

    fake.remove_refs = boom
    monkeypatch.setattr("lvdata_mcp.reset._build_vec", lambda _cfg: fake)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 1
    assert fake.swept == 1          # remove 失败后仍必须 sweep


def test_empty_scope_sweeps_vectors(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    fake = _FakeVec()
    monkeypatch.setattr("lvdata_mcp.reset._build_vec", lambda _cfg: fake)
    assert run_reset(cfg, date_spec="2025-01-01", yes=True) == 0
    assert fake.swept == 1


def test_sweep_failure_marks_partial(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    fake = _FakeVec()
    calls = []

    def boom(*, create_schema=True):
        calls.append(create_schema)
        raise RuntimeError("sweep boom")

    fake.sweep_orphans = boom
    monkeypatch.setattr("lvdata_mcp.reset._build_vec", lambda _cfg: fake)
    assert run_reset(cfg, date_spec="2025-01-01", yes=True) == 1
    assert calls == [False]         # 本地 sweep 不建 schema


def test_sweep_count_is_audited(tmp_path, monkeypatch, capsys):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    fake = _FakeVec()
    fake.sweep_orphans = lambda *, create_schema=True: 7
    monkeypatch.setattr("lvdata_mcp.reset._build_vec", lambda _cfg: fake)
    assert run_reset(cfg, date_spec="2025-01-01", yes=True,
                     as_json=True) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["vectors_swept"] == 7
    assert result["vectors_pending"] is False


def test_empty_scope_does_not_create_vec_db(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    monkeypatch.setattr(reset_mod, "build_provider",
                        lambda _cfg: FakeEmbeddingProvider())
    rag = Path(cfg.data.resolved_rag_db_path)
    assert not rag.exists()
    assert run_reset(cfg, date_spec="2025-01-01", yes=True) == 0
    assert not rag.exists()


def test_deferred_unlink_counters(tmp_path, monkeypatch, capsys):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    monkeypatch.setattr("lvdata_mcp.reset._build_vec",
                        lambda _cfg: _FakeVec())
    real_unlink = Path.unlink

    def boom(self, missing_ok=False):
        if self.name == "a2.ogg":
            raise OSError("boom")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", boom)
    assert run_reset(cfg, date_spec=DAY, yes=True, as_json=True,
                     allow_running_server=True) == 1
    out = capsys.readouterr().out
    result = json.loads(out[out.index('{\n  "plan"'):])["result"]
    assert S1 in result["sessions_deferred"]
    assert result["files_unlinked"] >= 2     # f2 与本轮 S4 的 s4a
    assert result["bytes_unlinked"] >= 8
    assert result["partial"] is True


def test_unlink_partial_progress_counted(tmp_path, monkeypatch, capsys):
    """首个文件已删、第二个 resolve 失败：审计计数不得漏报（review6 issue 2）。"""
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    day = "2026-10-10"
    sid = "partial-session"
    with _conn(root) as conn:
        _add_session(conn, root, sid, day)
        first = _add_file(conn, root, sid, "audio", "u1", "ok", T0)
        second = _add_file(conn, root, sid, "audio", "u2", "ok", T0)
        conn.commit()

    real_resolve = Path.resolve

    def boom(self, *args, **kwargs):
        if self.name == "u2.ogg":
            raise OSError("resolve boom")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", boom)
    assert run_reset(cfg, date_spec=day, yes=True, as_json=True) == 1
    out = capsys.readouterr().out
    result = json.loads(out[out.index('{\n  "plan"'):])["result"]
    assert result["files_unlinked"] == 1
    assert result["bytes_unlinked"] == 4
    assert sid in result["sessions_failed"]
    assert not first.exists() and second.exists()

    monkeypatch.setattr(Path, "resolve", real_resolve)
    assert run_reset(cfg, date_spec=day, yes=True) == 0
    assert not second.exists()


def test_missing_fts_created_on_execute(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    with _conn(root) as conn:
        conn.execute("DROP TABLE search_docs")
        conn.commit()
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    fts = _fts_refs(root)
    assert {f"{S1}:custom:x1", f"{S1}:window:w1"} <= fts


# ── 跨 session window（两个目标 session） ──────────────────────────────────

def test_window_zeroing_across_target_sessions(tmp_path):
    """同一 window 的 video 分散在两个目标 session：逐个 purge 后归零删除。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    a, b = "target-a", "target-b"
    with _conn(root) as conn:
        ensure_schema(conn, default_timezone=TZ, timezones=[])
        for sid in (a, b):
            _add_session(conn, root, sid, DAY)
        _add_file(conn, root, a, "video", "fa", "ok", T0)
        _add_event(conn, a, "video", "fa", T0,
                   {"frame_id": "fa", "window_id": "shared"})
        _add_file(conn, root, b, "video", "fb", "ok", T0)
        _add_event(conn, b, "video", "fb", T0,
                   {"frame_id": "fb", "window_id": "shared"})
        _add_event(conn, a, "window", "shared", T0,
                   {"window_id": "shared"})
        conn.commit()
    cfg = _cfg(root)
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    refs = _refs(root)
    assert refs == set()                    # 两个 video + window 全部归零
    for sid, frame in ((a, "fa"), (b, "fb")):
        row = _file_by_path(root, root / DEVICE / sid / "video"
                            / f"{frame}.jpg")
        assert row["analysis_status"] == "pending"


def test_path_guard_keeps_outside_file(tmp_path):
    """files.file_path 越 root → 不删除外部文件/行；其余 reset 照常。"""
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    outside = tmp_path / "outside-a2.ogg"
    outside.write_bytes(b"x")
    with _conn(root) as conn:
        conn.execute("UPDATE files SET file_path=? WHERE file_path LIKE ?",
                     (str(outside), "%/a2.ogg"))
        conn.commit()
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    assert outside.exists()
    row = _file_by_path(root, outside)
    assert row is not None and row["analysis_status"] == "pending"


def test_unlink_failure_keeps_row_and_continues(tmp_path, monkeypatch):
    """unlink 失败 → 行保持 ok；故障解除后第二轮真正删除并返回 0。"""
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    real_unlink = Path.unlink

    def boom(self, missing_ok=False):
        if self.name == "a2.ogg":
            raise OSError("boom")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", boom)
    # unlink 失败 = 部分完成，按 review 要求返回 1
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 1
    a2 = root / DEVICE / S1 / "audio" / "a2.ogg"
    assert a2.exists()
    row = _file_by_path(root, a2)
    # 保持 ok，下一轮才会重新进入删除候选（不能 reset 成 pending）
    assert row is not None and row["analysis_status"] == "ok"
    # session 主事务被延后：事件尚未 purge，保证下一轮仍能选中该 session
    assert f"{S1}:chat:c1" in _refs(root)

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    assert not a2.exists()
    assert _file_by_path(root, a2) is None
    assert f"{S1}:chat:c1" not in _refs(root)


def test_fallback_only_unlink_retry_keeps_scope(tmp_path, monkeypatch):
    """fallback-only session + 越界媒体 unlink 失败：第二轮仍须在 scope 内。"""
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    sid = "session-fb"
    with _conn(root) as conn:
        _add_session(conn, root, sid, "")
        _add_file(conn, root, sid, "chat_history", "history", "na", None)
        _add_event(conn, sid, "chat", "inrange", T0,
                   {"role": "user", "text": "keep", "chat_session_id": "cs"})
        far = _add_file(conn, root, sid, "audio", "far", "ok",
                        T0 - 10 * DAY_MS)
        conn.commit()
    real_unlink = Path.unlink

    def boom(self, missing_ok=False):
        if self.name == "far.ogg":
            raise OSError("boom")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", boom)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 1
    # 选择依据的 event 未被 purge，越界媒体仍为 ok
    assert f"{sid}:chat:inrange" in _refs(root)
    assert _file_by_path(root, far)["analysis_status"] == "ok"

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 0
    assert f"{sid}:chat:inrange" not in _refs(root)
    assert _file_by_path(root, far) is None


def test_day_summary_deleted_counted_when_recompute_removes_row(tmp_path):
    """活跃时区置 pending 后无剩余事件 → recompute 删行，需计入 deleted。"""
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    # 清空该日事件，确保 recompute_days 删除 pending 行；移除非活跃时区行以隔离计数
    with _conn(root) as conn:
        conn.execute("DELETE FROM events WHERE ts_ms BETWEEN ? AND ?",
                     day_bounds_ms(DAY, TZ))
        conn.execute("DELETE FROM day_summary WHERE timezone != ?", (TZ,))
        conn.commit()
    conn = _open_db(cfg)
    try:
        conn.execute("BEGIN IMMEDIATE")
        reset, deleted, recomputed = _reset_days(conn, cfg, [T0])
        conn.execute("COMMIT")
    finally:
        conn.close()
    assert reset == 1 and deleted == 1 and recomputed == 1
    with _conn(root) as conn:
        assert conn.execute(
            "SELECT 1 FROM day_summary WHERE timezone=? AND local_date=?",
            (TZ, DAY)).fetchone() is None


def test_derived_cleanup_failure_does_not_block_reset(tmp_path, monkeypatch):
    """derived 清理异常只 warning，不影响事件 purge/files 重置（§D14 best-effort）。"""
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    real_glob = Path.glob

    def boom(self, pattern):
        if self.name == S1 and str(pattern).startswith("audio/"):
            raise OSError("boom")
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", boom)
    assert run_reset(cfg, date_spec=DAY, yes=True,
                     allow_running_server=True) == 0
    assert f"{S1}:chat:c1" not in _refs(root)
    row = _file_by_path(root, root / DEVICE / S1 / "chat" / "history.jsonl")
    assert row["processed_updated_at"] is None


# ── review 回归：只读 plan / 失败重跑 / 时区再激活 / FTS / 向量 / 探测 ─────

def test_dry_run_does_not_create_or_migrate(tmp_path):
    """plan/dry-run 不得建表、迁移或切换 journal mode（review issue 1）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    db = root / "lvdata_inventory.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE chat_messages (id INTEGER)")
        conn.commit()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    cfg = _cfg(root)
    assert run_reset(cfg, date_spec=DAY, dry_run=True) == 1
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "chat_messages" in tables and "events" not in tables
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_invalid_date_does_not_create_db(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    cfg = _cfg(root)
    assert run_reset(cfg, date_spec="2026-13") == 2
    assert not (root / "lvdata_inventory.db").exists()


def test_execute_missing_db_does_not_create(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    cfg = _cfg(root)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 1
    assert not (root / "lvdata_inventory.db").exists()


def test_unconfirmed_does_not_migrate(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    with _conn(root) as conn:
        conn.execute("CREATE TABLE chat_messages (id INTEGER)")
        conn.commit()
    assert run_reset(cfg, date_spec=DAY) == 2
    with _conn(root) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='chat_messages'").fetchone() is not None


def test_failure_rerun_reselects_media_only_session(tmp_path, monkeypatch):
    """媒体行删除留在主事务：首次失败后第二次仍能通过 media 选中 session。"""
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    sid = "session-6"
    with _conn(root) as conn:
        _add_session(conn, root, sid, "")
        audio = _add_file(conn, root, sid, "audio", "a6", "ok", T0)
        _add_file(conn, root, sid, "chat_history", "history", "na", None)
        _add_event(conn, sid, "chat", "c6", T0,
                   {"role": "user", "text": "keep me?", "chat_session_id": "cs"})
        conn.commit()
    ref = f"{sid}:chat:c6"
    real_purge = reset_mod._purge_events_tx

    def flaky(conn, sid_arg, state):
        if sid_arg == sid:
            raise RuntimeError("boom")
        return real_purge(conn, sid_arg, state)

    monkeypatch.setattr(reset_mod, "_purge_events_tx", flaky)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 1
    assert ref in _refs(root)
    assert _file_by_path(root, audio) is not None   # 行仍在，供 fallback 选中

    monkeypatch.setattr(reset_mod, "_purge_events_tx", real_purge)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 0
    assert ref not in _refs(root)
    assert _file_by_path(root, audio) is None


def test_inactive_timezone_pending_reactivates(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 0
    ny_day = ms_to_local_date(T0, NY)
    with _conn(root) as conn:
        row = conn.execute(
            "SELECT status FROM day_summary WHERE timezone=? AND local_date=?",
            (NY, ny_day)).fetchone()
        assert row is not None and row["status"] == "pending"
        conn.execute(
            "INSERT OR REPLACE INTO timezones (timezone, enabled, priority) "
            "VALUES (?, 1, 0)", (NY,))
        conn.commit()
    ds = DaySummary(cfg, client=object())
    ds.discover()
    assert (NY, ny_day) in ds._pending


def test_missing_session_keeps_unknown_event_fts(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    sid = "ghost-session"
    ref = f"{sid}:custom:x1"
    with _conn(root) as conn:
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, ts_utc, source, summary, payload) "
            "VALUES (?,?,?,'custom',?,'x','test','ghost','{}')",
            (ref, DEVICE, sid, T0))
        conn.execute(
            "INSERT INTO search_docs (text, orig_text, event_ref, session_id, "
            "device_uuid, type, ts_ms) VALUES (?,?,?,?,?,?,?)",
            (index_mod.cjk_bigram("ghost"), "ghost", ref, sid, DEVICE,
             "custom", T0))
        conn.commit()
    assert run_reset(cfg, date_spec=DAY, yes=True) == 0
    assert ref in _refs(root)
    assert ref in _fts_refs(root)


def test_vector_cleanup_without_embedding_key(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    vec = VecIndex(cfg, DataStore(cfg), FakeEmbeddingProvider())
    asyncio.run(vec.sync_changed())
    cfg_no_key = LvdataMcpConfig(
        data={"root_dir": str(root)}, watcher={"timezone": TZ},
        providers={"embedding": {"model": "fake"}})
    assert cfg_no_key.providers.embedding_enabled() is False

    assert run_reset(cfg_no_key, date_spec=DAY, yes=True) == 0
    vec_db = sqlite3.connect(root / "lvdata_index.db")
    try:
        refs = {r[0] for r in vec_db.execute("SELECT event_ref FROM vec_state")}
    finally:
        vec_db.close()
    assert f"{S1}:chat:c1" not in refs
    assert f"{S1}:custom:x1" in refs


def test_local_vector_cleanup_does_not_create_schema(tmp_path):
    root = tmp_path / "lvdata"
    _seed(root)
    empty = root / "lvdata_index.db"
    sqlite3.connect(empty).close()
    cfg_no_key = LvdataMcpConfig(
        data={"root_dir": str(root)}, watcher={"timezone": TZ},
        providers={"embedding": {"model": "fake"}})
    assert run_reset(cfg_no_key, date_spec=DAY, yes=True) == 0
    conn = sqlite3.connect(empty)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "vec_chunks" not in tables and "vec_state" not in tables


class _OkResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args):
        pass


def test_server_status_ignores_http_proxy(monkeypatch, tmp_path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        cfg = LvdataMcpConfig(
            data={"root_dir": str(tmp_path)},
            server={"host": "127.0.0.1", "port": port},
            watcher={"timezone": TZ})
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("all_proxy", "http://127.0.0.1:1")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        assert server_status(cfg) == "running"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class _FakeOpener:
    def __init__(self, behavior):
        self.behavior = behavior

    def open(self, *args, **kwargs):
        return self.behavior(*args, **kwargs)


def test_server_status_three_state(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)

    def use(behavior):
        monkeypatch.setattr(
            reset_mod.urllib.request, "build_opener",
            lambda *a, **k: _FakeOpener(behavior))

    use(lambda *a, **k: _OkResponse())
    assert server_status(cfg) == "running"

    def refused(*a, **k):
        raise urllib.error.URLError(ConnectionRefusedError())

    use(refused)
    assert server_status(cfg) == "stopped"

    def timeout(*a, **k):
        raise TimeoutError()

    use(timeout)
    assert server_status(cfg) == "unknown"

    def dns(*a, **k):
        raise urllib.error.URLError(socket.gaierror(-2, "dns"))

    use(dns)
    assert server_status(cfg) == "unknown"


def test_unknown_server_status_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    monkeypatch.setattr(reset_mod, "server_status", lambda _cfg: "unknown")
    before = _refs(root)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 1
    assert _refs(root) == before


def test_json_error_contract(tmp_path, monkeypatch, capsys):
    # invalid date (date validation must precede DB access)
    cfg = _cfg(tmp_path)
    assert run_reset(cfg, date_spec="2026-13", as_json=True) == 2
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "invalid_date"

    # missing DB on execution
    assert run_reset(cfg, date_spec=DAY, yes=True, as_json=True) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "database_not_found"

    # unknown server status has its own code
    root = tmp_path / "lvdata2"
    cfg2 = _seed(root)
    monkeypatch.setattr(reset_mod, "server_status", lambda _cfg: "unknown")
    assert run_reset(cfg2, date_spec=DAY, yes=True, as_json=True) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "server_status_unknown"
    assert err["server_status"] == "unknown"


def test_json_schema_error_contract(tmp_path, capsys):
    root = tmp_path / "lvdata"
    root.mkdir()
    db = root / "lvdata_inventory.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE chat_messages (id INTEGER)")
        conn.commit()
    cfg = _cfg(root)
    assert run_reset(cfg, date_spec=DAY, dry_run=True, as_json=True) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "schema_error"


def test_uri_reserved_chars_in_db_path(tmp_path):
    root = tmp_path / "lv?data#x%y"
    cfg = _seed(root)
    assert run_reset(cfg, date_spec=DAY, dry_run=True) == 0
    assert (root / "lvdata_inventory.db").is_file()
    assert not (tmp_path / "lv").exists()


def test_uri_reserved_chars_execute_and_rerun(tmp_path):
    """实际执行的 sweep 也经 DataStore.connect 打开主库（review5 issue 1）。"""
    root = tmp_path / "lv?data#x%y"
    cfg = _seed(root)
    vec = VecIndex(cfg, DataStore(cfg), FakeEmbeddingProvider())
    asyncio.run(vec.sync_changed())
    rag = Path(cfg.data.resolved_rag_db_path)
    assert rag.is_file()

    assert run_reset(cfg, date_spec=DAY, yes=True) == 0
    assert run_reset(cfg, date_spec=DAY, yes=True) == 0
    assert rag.is_file()
    assert not (tmp_path / "lv").exists()       # 未被 URI 截断创建


def test_max_date_is_handled(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    assert run_reset(cfg, date_spec="9999-12-31", dry_run=True) == 0
    assert run_reset(cfg, date_spec="9999", dry_run=True) == 0


def test_max_date_negative_offset_nonempty(tmp_path, capsys):
    root = tmp_path / "lvdata"
    _seed(root)
    sid = "max-session"
    ts = int(datetime(9999, 12, 31, 12,
                      tzinfo=ZoneInfo(NY)).timestamp() * 1000)
    with _conn(root) as conn:
        _add_session(conn, root, sid, "9999-12-31")
        _add_file(conn, root, sid, "chat_history", "history", "na", None)
        _add_event(conn, sid, "chat", "maxevt", ts,
                   {"role": "user", "text": "max", "chat_session_id": "cs"})
        conn.commit()
    cfg_ny = LvdataMcpConfig(data={"root_dir": str(root)},
                             watcher={"timezone": NY})
    assert run_reset(cfg_ny, date_spec="9999-12-31", dry_run=True,
                     as_json=True) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["sessions"]) == 1


def test_safe_local_date_boundary_windows():
    from lvdata_mcp.timeutil import LocalDateOutOfRange, safe_local_date

    max_ms = 253402300799999
    late_ny = int(datetime(9999, 12, 31, 23, 30,
                           tzinfo=ZoneInfo(NY)).timestamp() * 1000)
    assert safe_local_date(late_ny, NY) == "9999-12-31"
    # 本地日期 year 10000 / year 0 属于范围外，不得静默归组
    with pytest.raises(LocalDateOutOfRange):
        safe_local_date(max_ms + 1, "UTC")
    with pytest.raises(LocalDateOutOfRange):
        safe_local_date(max_ms, TZ)
    with pytest.raises(LocalDateOutOfRange):
        safe_local_date(-62135596800001, "UTC")
    # 正偏移时区的本地最小日合法（UTC 侧落在 year 0）；Juneau 的历史 LMT
    # 偏移超过 14h，边界余量必须覆盖。
    for tz in (TZ, "+14:00", "America/Juneau"):
        start = day_bounds_ms("0001-01-01", tz)[0]
        assert safe_local_date(start + 30 * 60 * 1000, tz) == "0001-01-01"
    with pytest.raises(ValueError):
        safe_local_date(max_ms + 14 * 3600 * 1000 + 1, "UTC")


def test_max_date_bounds_cover_both_offsets():
    late = int(datetime(9999, 12, 31, 23, 30,
                        tzinfo=ZoneInfo(NY)).timestamp() * 1000)
    start, end = day_bounds_ms("9999-12-31", NY)
    assert start <= late <= end
    assert end > 253402300799999       # 不截短查询窗口

    noon = int(datetime(9999, 12, 31, 12,
                        tzinfo=ZoneInfo(TZ)).timestamp() * 1000)
    start, end = day_bounds_ms("9999-12-31", TZ)
    assert start <= noon <= end


def _add_max_chat_session(root: Path, cfg: LvdataMcpConfig, sid: str,
                          ts: int) -> None:
    with _conn(root) as conn:
        _add_session(conn, root, sid, "")
        _add_file(conn, root, sid, "chat_history", "history", "na", None)
        _add_event(conn, sid, "chat", "maxevt", ts,
                   {"role": "user", "text": "max", "chat_session_id": "cs"})
        start, end = day_bounds_ms("9999-12-31", cfg.watcher.timezone)
        conn.execute(
            "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
            "day_start_ms, day_end_ms, day_summary, status, event_count, "
            "fail_count, day_stats) "
            "VALUES (?,?,0,?,?,?, 'ok', 1, 0, '{}')",
            (cfg.watcher.timezone, "9999-12-31", start, end,
             json.dumps({"summary": "old"})))
        conn.commit()


def test_max_date_execute_positive_offset_and_rerun(tmp_path):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    sid = "max-pos"
    ts = int(datetime(9999, 12, 31, 12,
                      tzinfo=ZoneInfo(TZ)).timestamp() * 1000)
    _add_max_chat_session(root, cfg, sid, ts)

    assert run_reset(cfg, date_spec="9999-12-31", yes=True) == 0
    assert f"{sid}:chat:maxevt" not in _refs(root)
    with _conn(root) as conn:
        assert conn.execute(
            "SELECT 1 FROM day_summary WHERE timezone=? AND local_date=?",
            (TZ, "9999-12-31")).fetchone() is None
    assert run_reset(cfg, date_spec="9999-12-31", yes=True) == 0


def test_max_date_execute_negative_offset_late_event(tmp_path):
    root = tmp_path / "lvdata"
    _seed(root)
    cfg_ny = LvdataMcpConfig(data={"root_dir": str(root)},
                             watcher={"timezone": NY})
    sid = "max-neg"
    ts = int(datetime(9999, 12, 31, 23, 30,
                      tzinfo=ZoneInfo(NY)).timestamp() * 1000)
    _add_max_chat_session(root, cfg_ny, sid, ts)

    assert run_reset(cfg_ny, date_spec="9999-12-31", yes=True) == 0
    assert f"{sid}:chat:maxevt" not in _refs(root)
    with _conn(root) as conn:
        assert conn.execute(
            "SELECT 1 FROM day_summary WHERE timezone=? AND local_date=?",
            (NY, "9999-12-31")).fetchone() is None
    assert run_reset(cfg_ny, date_spec="9999-12-31", yes=True) == 0


class _MaxSummaryClient:
    model = "fake"
    max_attempts = 3

    def __init__(self):
        self.blocks: list[str] = []

    async def chat(self, system, user_content, enable_thinking=None):
        self.blocks.append(user_content[0]["text"])
        return json.dumps({"summary": "ok", "events": ["late"],
                           "topics": []}), {"input_tokens": 1,
                                            "output_tokens": 1}


def test_max_date_remaining_event_readable_and_summary(tmp_path):
    """reset 后 scope 外最大日尾部事件仍可 timeline/get_event/日摘要（review5 issue 3）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": NY})
    with _conn(root) as conn:
        ensure_schema(conn, default_timezone=NY, timezones=[])
        _add_session(conn, root, "max-target", "")
        _add_file(conn, root, "max-target", "chat_history", "history", "na", None)
        target_ts = int(datetime(9999, 12, 31, 12,
                                 tzinfo=ZoneInfo(NY)).timestamp() * 1000)
        _add_event(conn, "max-target", "chat", "c1", target_ts,
                   {"role": "user", "text": "target", "chat_session_id": "cs"})
        _add_session(conn, root, "max-keep", "9998-12-31")
        _add_file(conn, root, "max-keep", "chat_history", "history", "na", None)
        late_ts = int(datetime(9999, 12, 31, 23, 30,
                               tzinfo=ZoneInfo(NY)).timestamp() * 1000)
        keep_ref = _add_event(conn, "max-keep", "chat", "late", late_ts,
                              {"role": "user", "text": "late",
                               "chat_session_id": "cs"})
        start, end = day_bounds_ms("9999-12-31", NY)
        conn.execute(
            "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
            "day_start_ms, day_end_ms, day_summary, status, event_count, "
            "fail_count, day_stats) "
            "VALUES (?,?,0,?,?,?, 'ok', 1, 0, '{}')",
            (NY, "9999-12-31", start, end, json.dumps({"summary": "old"})))
        conn.commit()

    assert run_reset(cfg, date_spec="9999-12-31", yes=True) == 0
    assert "max-target:chat:c1" not in _refs(root)
    assert keep_ref in _refs(root)

    store = DataStore(cfg)
    timeline = store.get_timeline(timezone=NY)
    kept = [e for day in timeline["days"] for e in day["events"]
            if e["event_ref"] == keep_ref]
    assert kept and kept[0]["timestamp"] == "9999-12-31T23:30:00-05:00"
    event, _ = store.get_event(keep_ref, timezone=NY)
    assert event["timestamp"] == "9999-12-31T23:30:00-05:00"

    fake = _MaxSummaryClient()
    result = asyncio.run(DaySummary(cfg, client=fake).generate(force=True))
    assert result["ok"] == 1
    assert any("[23:30]" in block for block in fake.blocks)


def _add_min_chat_session(root: Path, cfg: LvdataMcpConfig, sid: str,
                          ts: int) -> None:
    with _conn(root) as conn:
        _add_session(conn, root, sid, "")
        _add_file(conn, root, sid, "chat_history", "history", "na", None)
        _add_event(conn, sid, "chat", "minevt", ts,
                   {"role": "user", "text": "min", "chat_session_id": "cs"})
        start, end = day_bounds_ms("0001-01-01", cfg.watcher.timezone)
        conn.execute(
            "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
            "day_start_ms, day_end_ms, day_summary, status, event_count, "
            "fail_count, day_stats) "
            "VALUES (?,?,0,?,?,?, 'ok', 1, 0, '{}')",
            (cfg.watcher.timezone, "0001-01-01", start, end,
             json.dumps({"summary": "old"})))
        conn.commit()


@pytest.mark.parametrize("tz", [TZ, "+14:00"])
def test_min_date_dry_run_execute_and_rerun(tmp_path, tz, capsys):
    root = tmp_path / "lvdata"
    _seed(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": tz})
    sid = "min-session"
    ts = day_bounds_ms("0001-01-01", tz)[0] + 30 * 60 * 1000
    _add_min_chat_session(root, cfg, sid, ts)

    assert run_reset(cfg, date_spec="0001-01-01", dry_run=True,
                     as_json=True) == 0
    plan = json.loads(capsys.readouterr().out)
    assert any(s["rsession_id"] == sid for s in plan["sessions"])

    assert run_reset(cfg, date_spec="0001-01-01", yes=True) == 0
    assert f"{sid}:chat:minevt" not in _refs(root)
    with _conn(root) as conn:
        assert conn.execute(
            "SELECT 1 FROM day_summary WHERE timezone=? AND local_date=?",
            (tz, "0001-01-01")).fetchone() is None
    assert run_reset(cfg, date_spec="0001-01-01", yes=True) == 0


def test_negative_underflow_fails_before_unlink(tmp_path):
    root = tmp_path / "lvdata"
    _seed(root)
    cfg_utc = LvdataMcpConfig(data={"root_dir": str(root)},
                              watcher={"timezone": "UTC"})
    sid = "underflow"
    with _conn(root) as conn:
        _add_session(conn, root, sid, DAY)
        victim = _add_file(conn, root, sid, "audio", "u2", "ok", T0)
        _add_event(conn, sid, "audio", "u1", -62135596800001,
                   {"segment_id": "u1"})
        conn.commit()
    assert victim.exists()
    assert run_reset(cfg_utc, date_spec=DAY, yes=True) == 1
    assert victim.exists()          # plan 失败必须先于任何 unlink


def test_cross_window_audit_counts_only_existing(tmp_path):
    root = tmp_path / "lvdata"
    audit = (root / "derived" / DEVICE / S2 / "video" / "windows"
             / "w1.json")
    audit.parent.mkdir(parents=True)
    audit.write_text("{}")
    rows = [(DEVICE, S2, "w1"), (DEVICE, S2, "missing")]
    assert reset_mod._delete_cross_window_audits(root, rows) == 1
    assert not audit.exists()


def test_cross_window_audit_removed_before_commit(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    with _conn(root) as conn:
        _add_file(conn, root, S1, "video", "f5", "ok", T0 + 12000)
        _add_event(conn, S1, "video", "f5", T0 + 12000,
                   {"frame_id": "f5", "window_id": "w3"})
        _add_event(conn, S2, "window", "w3", T0 + 12000,
                   {"window_id": "w3"})
        conn.commit()
    audit = root / "derived" / DEVICE / S2 / "video" / "windows" / "w3.json"
    audit.parent.mkdir(parents=True)
    audit.write_text("{}")

    def boom(conn, cfg, ts_list):
        raise RuntimeError("boom")

    monkeypatch.setattr(reset_mod, "_reset_days", boom)
    assert run_reset(cfg, date_spec=DAY, yes=True) == 1
    assert not audit.exists()               # 提交前已删，崩溃重跑不残留
    assert f"{S1}:video:f5" in _refs(root)  # 主事务回滚，事件仍在


def test_reset_json_argparse_errors(capsys):
    from lvdata_mcp.main import main

    with pytest.raises(SystemExit) as exc:
        main(["reset", "--json"])
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert json.loads(out)["error"]["code"] == "invalid_args"
    assert "usage:" not in out

    with pytest.raises(SystemExit) as exc:
        main(["reset", "--date", DAY, "--bogus", "--json"])
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_args"


def test_argparse_abbrev_disabled_and_command_detection(capsys):
    from lvdata_mcp.main import _command_from_argv, _parse_args

    assert _command_from_argv(
        ["--config", "reset", "eval", "--golden", "x"]) == "eval"
    assert _command_from_argv(["--config=reset", "serve"]) == "serve"

    # 长参数缩写不再被接受；--j 不等于 --json，走普通 argparse 错误
    with pytest.raises(SystemExit) as exc:
        _parse_args(["reset", "--date", DAY, "--j"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""

    # --config 的值恰好是 "reset" 时不得启用 reset JSON 错误格式
    with pytest.raises(SystemExit) as exc:
        _parse_args(["--config", "reset", "eval", "--bogus", "--json"])
    assert exc.value.code == 2
    assert "invalid_args" not in capsys.readouterr().out


def test_empty_scope_skips_window_scans(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    cfg = _seed(root)

    def boom(*a, **k):
        raise AssertionError("window scan should be skipped for empty scope")

    real_sim = reset_mod._simulate_window_purge
    monkeypatch.setattr(reset_mod, "_simulate_window_purge", boom)
    assert run_reset(cfg, date_spec="2025-01-01", dry_run=True) == 0
    monkeypatch.setattr(reset_mod, "_simulate_window_purge", real_sim)
    monkeypatch.setattr(reset_mod, "_build_window_state", boom)
    assert run_reset(cfg, date_spec="2025-01-01", yes=True) == 0
    # 非空但只有 audio/chat 的 scope 也不构建 WindowState
    assert run_reset(cfg, date_spec="2026-09-19", yes=True) == 0


def test_override_warning_in_json(tmp_path, monkeypatch, capsys):
    root = tmp_path / "lvdata"
    cfg = _seed(root)
    monkeypatch.setattr(reset_mod, "server_status", lambda _cfg: "running")
    monkeypatch.setattr(reset_mod, "_execute",
                        lambda *a, **k: {"partial": False})
    rc = run_reset(cfg, date_spec=DAY, yes=True, as_json=True,
                   allow_running_server=True)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["server_status"] == "running" and out["warnings"]


def test_main_dry_run_does_not_touch_legacy_db(tmp_path):
    """真实 main() 入口：dry-run 不建表/不迁移。"""
    from lvdata_mcp.main import main

    root = tmp_path / "lvdata"
    root.mkdir()
    db = root / "lvdata_inventory.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE chat_messages (id INTEGER)")
        conn.commit()
    cfg = tmp_path / "lvdata_mcp.yaml"
    cfg.write_text(
        f"server:\n  host: 127.0.0.1\n  port: 65534\n"
        f"data:\n  root_dir: {root}\n"
        f"watcher:\n  timezone: {TZ}\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(cfg), "reset", "--date", DAY, "--dry-run",
              "--json"])
    assert exc.value.code == 1
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "chat_messages" in tables and "events" not in tables


# ── 配置加载 ───────────────────────────────────────────────────────────────

def test_load_config_reset_skips_provider_keys(tmp_path, monkeypatch):
    root = tmp_path / "lvdata"
    root.mkdir()
    monkeypatch.delenv("LV_RESET_MISSING_HOST", raising=False)
    yaml_path = tmp_path / "lvdata_mcp.yaml"
    yaml_path.write_text(
        f"server:\n  host: 127.0.0.1\n  port: 8090\n"
        f"data:\n  root_dir: {root}\n"
        f"watcher:\n  timezone: {TZ}\n"
        f"providers:\n"
        f"  audio:\n    vendor: dashscope\n"
        f"    base_url: \"https://${{LV_RESET_MISSING_HOST}}/v1\"\n"
        f"    api_key: \"\"\n    model: m\n",
        encoding="utf-8")
    cfg = load_config(yaml_path, validate_providers=False)
    assert cfg.providers.audio.api_key == ""
    assert cfg.providers.audio.base_url == "https:///v1"
    with pytest.raises(ValueError):
        load_config(yaml_path, validate_providers=True)


def test_main_config_error_json(tmp_path, monkeypatch, capsys):
    from lvdata_mcp.main import main

    monkeypatch.delenv("LV_RESET_MISSING_ROOT", raising=False)
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(
        "server:\n  host: 127.0.0.1\n  port: 65534\n"
        "data:\n  root_dir: \"${LV_RESET_MISSING_ROOT}\"\n"
        f"watcher:\n  timezone: {TZ}\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(cfg), "reset", "--date", DAY, "--json"])
    assert exc.value.code == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "config_error"


def test_load_config_reset_numeric_placeholders_use_defaults(tmp_path,
                                                            monkeypatch):
    root = tmp_path / "lvdata"
    root.mkdir()
    monkeypatch.delenv("LV_RESET_MISSING_DIM", raising=False)
    monkeypatch.delenv("LV_RESET_MISSING_SCORE", raising=False)
    yaml_path = tmp_path / "lvdata_mcp.yaml"
    yaml_path.write_text(
        f"server:\n  host: 127.0.0.1\n  port: 8090\n"
        f"data:\n  root_dir: {root}\n"
        f"watcher:\n  timezone: {TZ}\n"
        f"providers:\n"
        f"  embedding:\n    vendor: dashscope\n    api_key: \"\"\n"
        f"    model: fake\n    dim: \"${{LV_RESET_MISSING_DIM}}\"\n"
        f"    min_vec_score: \"${{LV_RESET_MISSING_SCORE}}\"\n",
        encoding="utf-8")
    cfg = load_config(yaml_path, validate_providers=False)
    assert cfg.providers.embedding.dim is None
    assert cfg.providers.embedding.min_vec_score == 0.5
