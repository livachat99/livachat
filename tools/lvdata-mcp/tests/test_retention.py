"""审计表保留方案（docs/audit_tables_retention_plan.md）。

- index_state 水位写路径：force/增量/target；T0 入口快照；空跑/失败不写；
  force/CLI 清会话行
- _prune_once 按龄清理：running 护栏（独立析取）、孤儿水位、0 = 关闭、幂等
- _video_prune_once：video 帧保留（docs/data_clean.md）——ok 且无事件的过期帧
  （jpg + 行）删除；pending/failed 保留；clamp ≥24h

lag 读语义见 test_index / test_tools。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import DEVICE, FRAME, SID, make_sample
from lvdata_mcp import index as index_mod
from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.index import lag_sessions
from lvdata_mcp.pipeline import Pipeline
from lvdata_mcp.recording_watch import RecordingWatch
from lvdata_mcp.store import DataStore
from lvdata_mcp.watcher import Watcher

_OLD = "2000-01-01T00:00:00.000Z"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    return root


def _cfg(root: Path, **watcher: object) -> LvdataMcpConfig:
    return LvdataMcpConfig(data={"root_dir": str(root)},
                           watcher={"timezone": "Asia/Shanghai", **watcher})


def _pipeline(root: Path) -> Pipeline:
    cfg = _cfg(root)
    return Pipeline(cfg, DataStore(cfg), RecordingWatch(cfg))


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    return conn


def _watermarks(root: Path) -> dict[str, str]:
    with _db(root) as conn:
        return {r["target"]: r["last_reindex_at"] for r in conn.execute(
            "SELECT target, last_reindex_at FROM index_state")}


def _lag(root: Path) -> list[str]:
    with _db(root) as conn:
        return lag_sessions(conn)


# ── 写路径（§2.2）────────────────────────────────────────────────────────

def test_force_writes_global_and_clears_session_rows(tmp_path):
    """force：写 '' + 同事务清会话行 → 读路径 COALESCE 落空取新 ''、不误判滞后。"""
    root = _root(tmp_path)
    with _db(root) as conn:
        conn.executemany("INSERT INTO index_state VALUES (?, ?)",
                         [(SID, _OLD), ("", _OLD)])
        # 事件钉在旧时刻：水位 = T0−1ms，同毫秒事件会（保守地）判滞后一次
        conn.execute("UPDATE events SET updated_at = '2026-06-01T00:00:00.000Z'")
        conn.commit()
    _pipeline(root)._reindex(None, force=True)
    wm = _watermarks(root)
    assert list(wm) == [""]           # 仅剩全局行（会话行已清）
    assert wm[""] > "2026-01-01"      # 新 T0（now−1ms）
    assert _lag(root) == []           # 事件水位 ≤ 水位 → 不滞后


def test_incremental_writes_per_session_and_not_global(tmp_path, monkeypatch):
    """增量：逐会话写各自行、绝不写 ''、不清既有会话行；空跑不推进。"""
    root = _root(tmp_path)
    with _db(root) as conn:
        # 预置他会话行（无事件、不参与滞后名单）：增量不得清它
        conn.execute("INSERT INTO index_state VALUES (?, ?)",
                     ("other-session", _OLD))
        conn.commit()
    pl = _pipeline(root)
    # 受控时钟：T0 落在 2099（覆盖样本事件的现实时间戳）→ 次轮必空跑。
    # 水位 = 快照−1ms（同毫秒边界闭合，见 index.set_watermark）
    ticks = iter(["2099-01-01T00:00:00.000Z", "2099-01-02T00:00:00.000Z"])
    monkeypatch.setattr("lvdata_mcp.pipeline.now_iso", lambda: next(ticks))
    pl._reindex(None, force=False)    # 首轮：全部滞后 → 重建 → 逐会话写
    assert _watermarks(root) == {"other-session": _OLD,
                                 SID: "2098-12-31T23:59:59.999Z"}
    assert _lag(root) == []
    pl._reindex(None, force=False)    # 空跑：不写（值保持 T0_1 而非 T0_2）
    assert _watermarks(root) == {"other-session": _OLD,
                                 SID: "2098-12-31T23:59:59.999Z"}


def test_target_writes_single_session_row(tmp_path):
    """target=session：只写该会话行（不写 ''）。"""
    root = _root(tmp_path)
    _pipeline(root)._reindex(SID, force=False)
    assert list(_watermarks(root)) == [SID]


def test_t0_snapshot_self_heals_mid_rebuild_updates(tmp_path, monkeypatch):
    """T0 = 入口快照：重建期间更新的 event（updated_at > T0）下轮仍判滞后 → 自愈。

    旧「完成时刻」水位语义会把该窗口的更新永久吞掉（水位 > 事件 → 不再判定）。
    """
    root = _root(tmp_path)
    pl = _pipeline(root)
    real = index_mod.rebuild_session

    def wrapper(conn, store, sid):
        n = real(conn, store, sid)
        # 模拟重建读完后、水位写前到达的事件更新（竞态窗口）
        conn.execute("UPDATE events SET updated_at = ? WHERE rsession_id = ?",
                     ("2099-06-01T00:00:00.000Z", sid))
        return n

    monkeypatch.setattr(index_mod, "rebuild_session", wrapper)
    pl._reindex(None, force=False)
    assert _lag(root) == [SID]        # 2099 > T0(now) → 未清、下轮重建


def test_watermark_same_ms_boundary_self_heals(tmp_path, monkeypatch):
    """同毫秒边界：事件更新与 T0 同毫秒（且晚于该会话读取）——水位取 T0−1ms，
    下轮仍判滞后、自愈。

    不修则 updated_at == 水位 → strict `>` 判不出 → 永久漏（确定性复现）；
    修后语义：声称覆盖 ≤ T0−1ms（必然先于快照存在、读取必见）。
    """
    root = _root(tmp_path)
    pl = _pipeline(root)
    frozen = "2099-01-01T00:00:00.000Z"
    monkeypatch.setattr("lvdata_mcp.pipeline.now_iso", lambda: frozen)
    real = index_mod.rebuild_session

    def wrapper(conn, store, sid):
        n = real(conn, store, sid)
        # 重建读完后、水位写前到达的更新（同毫秒竞态窗）
        conn.execute("UPDATE events SET updated_at = ? WHERE rsession_id = ?",
                     (frozen, sid))
        return n

    monkeypatch.setattr(index_mod, "rebuild_session", wrapper)
    pl._reindex(None, force=False)
    assert _watermarks(root) == {SID: "2098-12-31T23:59:59.999Z"}
    assert _lag(root) == [SID]        # 同毫秒更新 > 水位 → 下轮重建（自愈）


def test_failure_writes_nothing(tmp_path, monkeypatch):
    """失败不写（同事务回滚）；force 崩在两次 commit 之间水位未推进（安全方向）。"""
    root = _root(tmp_path)
    pl = _pipeline(root)

    def boom(conn, store, sid):
        raise RuntimeError("boom")

    monkeypatch.setattr(index_mod, "rebuild_session", boom)
    with pytest.raises(RuntimeError):
        pl._reindex(None, force=False)
    assert _watermarks(root) == {}

    monkeypatch.undo()                # 还原 rebuild_session，再单独 patch rebuild_all
    real_all = index_mod.rebuild_all

    def crash_after_commit(conn, store):
        real_all(conn, store)         # 内部 commit：FTS 已落
        raise RuntimeError("crash before watermark commit")

    monkeypatch.setattr(index_mod, "rebuild_all", crash_after_commit)
    with pytest.raises(RuntimeError):
        pl._reindex(None, force=True)
    assert _watermarks(root) == {}    # 水位未推进 → 下轮重来
    with _db(root) as conn:
        assert conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0] == 5


def test_cli_reindex_writes_global_and_clears(tmp_path):
    """CLI 全量重建（reindex 子命令路径）：写 '' + 清会话行（与 watcher force 同）。"""
    root = _root(tmp_path)
    with _db(root) as conn:
        conn.execute("INSERT INTO index_state VALUES (?, ?)", (SID, _OLD))
        conn.commit()
    from lvdata_mcp.main import run_reindex

    run_reindex(_cfg(root))
    wm = _watermarks(root)
    assert list(wm) == [""]
    assert wm[""] > "2026-01-01"


# ── 清理（§2.5）──────────────────────────────────────────────────────────

def _ago(seconds: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _watcher(root: Path, **watcher: object) -> Watcher:
    cfg = _cfg(root, **watcher)
    store = DataStore(cfg)
    return Watcher(cfg, store, Pipeline(cfg, store, RecordingWatch(cfg)))


def _ensure_runs_table(root: Path) -> None:
    """mcp_pipeline_runs 由 pipeline._conn 惰性建表（首轮 stage 时）；直插测试先建。"""
    with _db(root) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mcp_pipeline_runs ("
            "run_id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT, "
            "stage TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, "
            "status TEXT NOT NULL DEFAULT 'running', exit_code INTEGER, error TEXT)")
        conn.commit()


def _add_run(root: Path, *, status: str, started_at: str) -> None:
    _ensure_runs_table(root)
    with _db(root) as conn:
        conn.execute(
            "INSERT INTO mcp_pipeline_runs (target, stage, started_at, status) "
            "VALUES (NULL, 'reindex', ?, ?)", (started_at, status))
        conn.commit()


def test_prune_retention_and_zombie_running(tmp_path):
    """非 running 按 14 天保留期；running 按僵尸窗口独立裁——僵尸行须用保留期
    内的构造（>14 天的行会因保留期条件被删、测不到僵尸逻辑）。幂等：连跑两次。"""
    root = _root(tmp_path)
    w = _watcher(root)
    t_expired = _ago(20 * 86400)
    t_keep = _ago(2 * 86400)
    t_inflight = _ago(10 * 60)      # 在途（< zombie=1h）
    t_zombie = _ago(2 * 3600)       # 僵尸（> zombie，但 < 14 天）
    for status, ts in (("ok", t_expired), ("failed", t_expired),
                       ("ok", t_keep), ("running", t_inflight),
                       ("running", t_zombie)):
        _add_run(root, status=status, started_at=ts)
    w._prune_once()
    w._prune_once()
    with _db(root) as conn:
        rows = conn.execute("SELECT status, started_at FROM mcp_pipeline_runs "
                            "ORDER BY run_id").fetchall()
    assert [(r["status"], r["started_at"]) for r in rows] == [
        ("ok", t_keep), ("running", t_inflight)]


def test_prune_cutoff_boundary_strict_less(tmp_path, monkeypatch):
    """cutoff 边界：严格 `<`——正好等于 cutoff 的行保留（旧/新各删/留）。"""
    root = _root(tmp_path)
    _ensure_runs_table(root)
    monkeypatch.setattr("lvdata_mcp.watcher._iso_ago",
                        lambda seconds: "2026-01-01T00:00:00.000Z")
    w = _watcher(root)
    _add_run(root, status="ok", started_at="2025-12-31T23:59:59.999Z")  # <
    _add_run(root, status="ok", started_at="2026-01-01T00:00:00.000Z")  # ==
    _add_run(root, status="ok", started_at="2026-01-01T00:00:00.001Z")  # >
    w._prune_once()
    with _db(root) as conn:
        rows = [r[0] for r in conn.execute(
            "SELECT started_at FROM mcp_pipeline_runs ORDER BY run_id")]
    assert rows == ["2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.001Z"]


def test_prune_analysis_runs_and_null_started(tmp_path):
    """analysis_runs 按 30 天；started_at NULL 行无法判龄、不清理（防御语义）。"""
    root = _root(tmp_path)
    _ensure_runs_table(root)
    w = _watcher(root)
    with _db(root) as conn:
        conn.executemany(
            "INSERT INTO analysis_runs (file_id, kind, status, started_at) "
            "VALUES (1, 'audio', 'ok', ?)",
            [(_ago(31 * 86400),), (_ago(86400),), (None,)])
        conn.commit()
    w._prune_once()
    with _db(root) as conn:
        n = conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
        nulls = conn.execute(
            "SELECT COUNT(*) FROM analysis_runs WHERE started_at IS NULL"
        ).fetchone()[0]
    assert n == 2 and nulls == 1


def test_prune_orphan_watermarks(tmp_path):
    """孤儿会话水位被清；'' 全局行与有事件的会话行保留。"""
    root = _root(tmp_path)
    _ensure_runs_table(root)
    w = _watcher(root)
    with _db(root) as conn:
        conn.executemany("INSERT INTO index_state VALUES (?, ?)",
                         [("", _OLD), (SID, _OLD), ("ghost-session", _OLD)])
        conn.commit()
    w._prune_once()
    assert _watermarks(root) == {"": _OLD, SID: _OLD}


def test_prune_zero_disables_and_skips_orphan_cleanup(tmp_path):
    """audit_retention_days=0：runs 不删、孤儿清理同步跳过；analysis 键独立生效。"""
    root = _root(tmp_path)
    w = _watcher(root, audit_retention_days=0, analysis_runs_retention_days=30)
    _add_run(root, status="ok", started_at=_ago(20 * 86400))
    with _db(root) as conn:
        conn.execute("INSERT INTO index_state VALUES (?, ?)", ("ghost-session", _OLD))
        conn.execute(
            "INSERT INTO analysis_runs (file_id, kind, status, started_at) "
            "VALUES (1, 'audio', 'ok', ?)", (_ago(31 * 86400),))
        conn.commit()
    w._prune_once()
    with _db(root) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM mcp_pipeline_runs").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 0
    assert "ghost-session" in _watermarks(root)


def test_prune_loop_survives_failure(tmp_path, monkeypatch):
    """单次失败不杀任务：首 tick 抛错被吞、任务存活继续跑下一轮。"""
    root = _root(tmp_path)
    w = _watcher(root, audit_prune_interval_s=1)
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(w, "_prune_once", flaky)

    async def main() -> None:
        task = asyncio.create_task(w._prune_loop())
        await asyncio.sleep(1.3)   # tick1 立即（抛错被吞）→ 睡 1s → tick2
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
    assert len(calls) >= 2


# ── video 帧 prune（docs/data_clean.md §6）───────────────────────────────

def _add_video_frame(root: Path, name: str, age_h: float, *,
                     status: str = "ok", write_file: bool = True,
                     path: str | None = None) -> tuple[str, int]:
    """插入一个 video 帧（files 行 + 磁盘文件），返回 (file_path, file_id)。"""
    p = path or str(root / DEVICE / SID / "video" / f"{name}.jpg")
    ts_ms = int((time.time() - age_h * 3600) * 1000)
    if write_file:
        Path(p).write_bytes(b"\xff\xd8\xff\xe0fake jpeg")
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    try:
        cur = conn.execute(
            "INSERT INTO files (rsession_id, device_uuid, file_type, file_ext, "
            "file_path, file_size, uuid_ts_ms, analysis_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (SID, DEVICE, "video", "jpg", p, 123, ts_ms, status))
        conn.commit()
        return p, cur.lastrowid
    finally:
        conn.close()


def _row_exists(root: Path, path: str) -> bool:
    with _db(root) as conn:
        return conn.execute("SELECT 1 FROM files WHERE file_path = ?",
                            (path,)).fetchone() is not None


def test_video_prune_deletes_stale_non_key_ok(tmp_path):
    """§6.1：ok 且无事件、超期 → 文件+行删除；关键帧/未超期保留；events 不动。"""
    root = _root(tmp_path)
    with _db(root) as conn:
        # 样例帧转 ok（有事件 {SID}:video:{FRAME}）——关键帧必须保留
        conn.execute("UPDATE files SET analysis_status='ok' "
                     "WHERE file_type='video'")
        n_ev = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.commit()
    stale_p, _ = _add_video_frame(root, "stale1", 100)
    fresh_p, _ = _add_video_frame(root, "fresh1", 10)
    key_p = str(root / DEVICE / SID / "video" / f"{FRAME}.jpg")

    w = _watcher(root)
    w._video_prune_once()

    assert not Path(stale_p).exists() and not _row_exists(root, stale_p)
    assert Path(fresh_p).exists() and _row_exists(root, fresh_p)
    assert Path(key_p).exists() and _row_exists(root, key_p)
    with _db(root) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == n_ev
    assert (root / "derived" / DEVICE / SID / "video" / "windows"
            / "w1.json").is_file()


def test_video_prune_keeps_failed_and_pending(tmp_path):
    """§6.2（D1）：>72h 的 failed/pending 无事件帧 → 保留。"""
    root = _root(tmp_path)
    failed_p, _ = _add_video_frame(root, "failed1", 100, status="failed")
    pending_p, _ = _add_video_frame(root, "pending1", 100, status="pending")
    _watcher(root)._video_prune_once()
    assert Path(failed_p).exists() and _row_exists(root, failed_p)
    assert Path(pending_p).exists() and _row_exists(root, pending_p)


def test_video_prune_min_24h_clamp(tmp_path):
    """§6.3（D2）：配置 1h → 运行时按 24h——10h 帧保留、100h 帧删除。"""
    root = _root(tmp_path)
    mid_p, _ = _add_video_frame(root, "mid10h", 10)
    old_p, _ = _add_video_frame(root, "old100h", 100)
    _watcher(root, video_retention_hours=1)._video_prune_once()
    assert Path(mid_p).exists() and _row_exists(root, mid_p)
    assert not Path(old_p).exists() and not _row_exists(root, old_p)


def test_video_prune_idempotent(tmp_path):
    """§6.4：重复执行幂等（二跑无删除、无异常）。"""
    root = _root(tmp_path)
    stale_p, _ = _add_video_frame(root, "stale2", 100)
    w = _watcher(root)
    w._video_prune_once()
    w._video_prune_once()          # 幂等：无异常、状态不变
    assert not Path(stale_p).exists() and not _row_exists(root, stale_p)


def test_video_prune_absent_file_selfheal(tmp_path):
    """§6.5：行在、文件已缺失 → 删行、不抛错（崩溃自愈路径）。"""
    root = _root(tmp_path)
    p, _ = _add_video_frame(root, "absent1", 100, write_file=False)
    _watcher(root)._video_prune_once()
    assert not _row_exists(root, p)


def test_video_prune_path_guard(tmp_path):
    """§6.6：file_path 越 root → 跳过 + 行保留、外部文件不动。"""
    root = _root(tmp_path)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"x")
    p, _ = _add_video_frame(root, "evil", 100, write_file=False,
                            path=str(outside))
    _watcher(root)._video_prune_once()
    assert outside.exists() and _row_exists(root, p)
