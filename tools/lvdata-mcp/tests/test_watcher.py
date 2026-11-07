"""Watcher（cs 单链路）：round 固定序列、force 分派、day-summary 流程、状态口径。"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.pipeline import Pipeline
from lvdata_mcp.recording_watch import RecordingWatch
from lvdata_mcp.store import DataStore
from lvdata_mcp.summary.day import DaySummary
from lvdata_mcp.timeutil import ms_to_local_date
from lvdata_mcp.watcher import FLUSH_STAGES, ROUND_STAGES, Watcher

DEV = "b53985c7-0000-4000-8000-000000000001"
SID = "019fabe0-3b0f-7947-8000-1e567def787d"


class _StubLlm:
    """门控测试不触达 LLM；端到端生成用例返回合法 JSON。"""
    model = "stub"
    max_attempts = 1

    async def chat(self, system, user_content, enable_thinking=None):
        return ('{"summary": "stub", "events": ["e1"], "topics": ["t1"]}', {})


def _setup(root: Path) -> None:
    # 顶层 conftest 导入：pytest 会把本目录插入 sys.path；用 tests.conftest
    # 会撞名仓库内 namespace package（services/tests，editable install 在 sys.path）
    from conftest import make_sample
    make_sample(root)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("UPDATE files SET analysis_status = 'pending', fail_count = 0")
    conn.commit()
    conn.close()


def _watcher(root: Path) -> Watcher:
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": "Asia/Shanghai"})
    store = DataStore(cfg)
    pipeline = Pipeline(cfg, store, RecordingWatch(cfg),
                        day_summary=DaySummary(cfg, client=_StubLlm()))
    return Watcher(cfg, store, pipeline)


def _db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    return conn


async def drive(w: Watcher, rounds: int = 1) -> None:
    worker = asyncio.create_task(w._work_loop())
    for _ in range(rounds):
        await w.poll_once()
        await w.queue.join()
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


def test_poll_enqueues_round_marker_and_records_inventory(tmp_path):
    _setup(tmp_path)
    w = _watcher(tmp_path)
    asyncio.run(w.poll_once())
    assert w._round_queued is True
    assert w.queue.qsize() == 1
    # round 标记无数据且合并：重复 poll 不重复入队
    asyncio.run(w.poll_once())
    assert w.queue.qsize() == 1
    with _db(tmp_path) as conn:
        rows = conn.execute(
            "SELECT stage, target, status FROM mcp_pipeline_runs "
            "ORDER BY run_id").fetchall()
        assert rows[0]["stage"] == "inventory"
        assert rows[0]["target"] is None  # 全局 stage
        assert rows[0]["status"] == "ok"


def test_round_runs_fixed_sequence(tmp_path):
    """消费 round → chatlog → audio → video → reindex → embed → day_summary。"""
    _setup(tmp_path)
    w = _watcher(tmp_path)
    calls: list[tuple[str, object, bool]] = []

    async def spy(stage, target=None, force=False):
        calls.append((stage, target, force))
        return {"status": "ok"}

    w.pipeline.run_stage = spy
    asyncio.run(drive(w))
    # 样本事件 ts 陈旧 → flush 门控按时限放行 → 完整序列（含 flush 组）
    assert [c[0] for c in calls] == ["inventory", *ROUND_STAGES, *FLUSH_STAGES]
    assert w._round_queued is False  # 已消化
    seq = calls[1:]
    assert all(target is None and force is False for _, target, force in seq)


def test_trigger_force_scoped_to_stage_and_session(tmp_path):
    _setup(tmp_path)
    w = _watcher(tmp_path)
    calls: list[tuple[str, object, bool]] = []

    async def spy(stage, target=None, force=False):
        calls.append((stage, target, force))
        return {"status": "ok"}

    w.pipeline.run_stage = spy

    async def main():
        await w.trigger(["audio"], force=True, session_id=SID)
        await drive(w)

    asyncio.run(main())
    by_stage = {c[0]: c for c in calls}
    assert by_stage["audio"][2] is True           # force 命中指定 stage
    assert by_stage["audio"][1] == SID            # session 作用域
    assert by_stage["chatlog"][2] is False        # 其余 stage 无 force
    # force 快照已消费（下一轮恢复默认）
    assert w._stage_force == {} and w._force_target is None


def test_trigger_without_force_ignores_session(tmp_path):
    _setup(tmp_path)
    w = _watcher(tmp_path)
    calls: list[tuple[str, object, bool]] = []

    async def spy(stage, target=None, force=False):
        calls.append((stage, target, force))
        return {"status": "ok"}

    w.pipeline.run_stage = spy

    async def main():
        await w.trigger(["chatlog"], force=False, session_id=SID)
        await drive(w)

    asyncio.run(main())
    by_stage = {c[0]: c for c in calls}
    assert by_stage["chatlog"] == ("chatlog", None, False)  # 无 force 不带 target


# ── flush 门控（§5.C）────────────────────────────────────────────────────

_FUTURE = "2099-01-01T00:00:00.000Z"
# 事件已入库（updated_at ≤ 水印 → n=0，门控只可能由 day_summary 待办放行），
# 且 ≤ 生成快照 t0（可进入 LLM 输入）——与真实场景一致
_EV_UPDATED = "2026-06-01T00:00:00.000Z"


def _set_watermark(root: Path, ts: str) -> None:
    with _db(root) as conn:
        conn.execute(
            "INSERT INTO flush_state (id, last_flush_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_flush_at = excluded.last_flush_at",
            (ts,))
        conn.commit()


def _add_event(root: Path, ref: str, *, ts_ms: int, updated_at: str) -> None:
    with _db(root) as conn:
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary, payload, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (ref, DEV, SID, "chat", ts_ms, "test", "s", "{}", updated_at))
        conn.commit()


def test_flush_watermark_advances_then_gate_skips(tmp_path):
    """首轮 flush（样本事件 ts 陈旧）→ 水印推进；次轮干净 → flush 组跳过。"""
    _setup(tmp_path)
    calls: list[str] = []

    async def spy(stage, target=None, force=False):
        calls.append(stage)
        return {"status": "ok"}

    async def main():
        w = _watcher(tmp_path)          # 单事件循环内两轮（watcher 原语绑定 loop）
        w.pipeline.run_stage = spy
        await drive(w)
        assert calls == ["inventory", *ROUND_STAGES, *FLUSH_STAGES]
        with _db(tmp_path) as conn:
            row = conn.execute(
                "SELECT last_flush_at FROM flush_state WHERE id = 1").fetchone()
        assert row is not None and row[0]  # 水印已推进

        calls.clear()
        await drive(w)
        assert calls == ["inventory", *ROUND_STAGES]  # 干净 → 无 flush 组

    asyncio.run(main())


def test_flush_gate_count_threshold(tmp_path):
    """待处理 events 达阈值（默认 10）即放行（ts 为当下，时限不参与）。"""
    import time as _time
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)  # 现有事件视为已处理
    now_ms = int(_time.time() * 1000)
    for i in range(10):
        _add_event(tmp_path, f"r{i}:chat:e{i}", ts_ms=now_ms,
                   updated_at="2099-06-01T00:00:00.000Z")
    w = _watcher(tmp_path)
    assert w._flush_due() is True


def test_flush_gate_age_floor(tmp_path):
    """单条陈旧事件（低于数量阈值）由时限兜底放行。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    _add_event(tmp_path, "old:chat:e1", ts_ms=1000,  # 1970s → 远超时限
               updated_at="2099-06-01T00:00:00.000Z")
    w = _watcher(tmp_path)
    assert w._flush_due() is True


def _seed_day(root: Path, tz: str, d: str, *, status: str = "pending",
              generated_at: str | None = None) -> None:
    with _db(root) as conn:
        conn.execute(
            "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
            "day_start_ms, day_end_ms, day_summary, status, generated_at) "
            "VALUES (?, ?, 480, 0, 0, '{}', ?, ?)", (tz, d, status, generated_at))
        conn.commit()


def _defer_day(root: Path, tz: str, d: str) -> None:
    with _db(root) as conn:
        conn.execute(
            "INSERT INTO day_deferred (timezone, local_date) VALUES (?, ?)",
            (tz, d))
        conn.commit()


def test_flush_gate_day_summary_retry(tmp_path):
    """无待处理事件但 day_summary 非 ok 行已可生成 → 放行（失败重试不被吞）。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    ev_ms = int(time.time() * 1000) - 3600_000
    d = ms_to_local_date(ev_ms, "Asia/Shanghai")
    _add_event(tmp_path, "r1:chat:e1", ts_ms=ev_ms, updated_at=_EV_UPDATED)
    _seed_day(tmp_path, "Asia/Shanghai", d)
    w = _watcher(tmp_path)
    assert w._flush_due() is True


def test_flush_gate_cleanup_day_row_without_events(tmp_path):
    """无事件日的残留非 ok 行 → 放行（discover 直接清理，否则永不消失）。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    _seed_day(tmp_path, "Asia/Shanghai", "2026-07-29")
    w = _watcher(tmp_path)
    assert w._flush_due() is True


def test_flush_gate_unready_deferred_day_closed(tmp_path):
    """deferred 待生成日事件仍新鲜（debounce 未过）→ 不放行——否则活跃录制
    期间门控恒放行，节流失效（回归）。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    ev_ms = int(time.time() * 1000) - 60_000
    d = ms_to_local_date(ev_ms, "Asia/Shanghai")
    _add_event(tmp_path, "r1:chat:e1", ts_ms=ev_ms, updated_at=_EV_UPDATED)
    _seed_day(tmp_path, "Asia/Shanghai", d)
    _defer_day(tmp_path, "Asia/Shanghai", d)
    assert _watcher(tmp_path)._flush_due() is False


def test_flush_gate_ready_deferred_day_row(tmp_path):
    """deferred 待生成日事件已静默 → 放行（回归：事件停流后 n=0 且占位行在
    deferred 中被排除，discover 永不运行 → 永久 starvation，09-20 案例）。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    ev_ms = int(time.time() * 1000) - 3600_000
    d = ms_to_local_date(ev_ms, "Asia/Shanghai")
    _add_event(tmp_path, "r1:chat:e1", ts_ms=ev_ms, updated_at=_EV_UPDATED)
    _seed_day(tmp_path, "Asia/Shanghai", d)
    _defer_day(tmp_path, "Asia/Shanghai", d)
    assert _watcher(tmp_path)._flush_due() is True


def test_flush_gate_ignores_inactive_timezone_rows(tmp_path):
    """非活跃时区（非最近使用、非默认）的非 ok 行不放行：discover 只遍历
    活跃时区，放行只会每轮空转 flush（travel 后无限期）；再激活即恢复放行。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    with _db(tmp_path) as conn:
        for tz, updated in (("America/New_York", "2026-01-01T00:00:00.000Z"),
                            ("Asia/Shanghai", "2026-06-01T00:00:00.000Z")):
            conn.execute(
                "INSERT OR REPLACE INTO timezones "
                "(timezone, enabled, created_at, updated_at) VALUES (?, 1, ?, ?)",
                (tz, updated, updated))
        conn.commit()
    ev_ms = int(time.time() * 1000) - 3600_000
    _add_event(tmp_path, "r1:chat:e1", ts_ms=ev_ms, updated_at=_EV_UPDATED)
    _seed_day(tmp_path, "America/New_York",
              ms_to_local_date(ev_ms, "America/New_York"))
    assert _watcher(tmp_path)._flush_due() is False
    with _db(tmp_path) as conn:            # 再激活（最近使用）→ 恢复放行
        conn.execute("UPDATE timezones SET updated_at = '2026-12-01T00:00:00.000Z' "
                     "WHERE timezone = 'America/New_York'")
        conn.commit()
    assert _watcher(tmp_path)._flush_due() is True


def test_flush_gate_ready_deferred_ok_day_row(tmp_path):
    """ok 日 + 新事件（已静默）的 deferred 行 → 放行。旧条件只数非 ok 行，
    ok 日 deferred 行在事件停流后无任何条件可放行（同 starvation）。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    ev_ms = int(time.time() * 1000) - 3600_000
    d = ms_to_local_date(ev_ms, "Asia/Shanghai")
    _add_event(tmp_path, "r1:chat:e1", ts_ms=ev_ms, updated_at=_EV_UPDATED)
    _seed_day(tmp_path, "Asia/Shanghai", d, status="ok",
              generated_at="2026-01-01T00:00:00.000Z")
    _defer_day(tmp_path, "Asia/Shanghai", d)
    assert _watcher(tmp_path)._flush_due() is True


def _stub_heavy_stages(w: Watcher) -> None:
    """只跑真实 day_summary stage，其余 stage spy 短路（不触外部调用）。"""
    real = w.pipeline.run_stage

    async def spy(stage, target=None, force=False):
        if stage == "day_summary":
            return await real(stage, target, force)
        return {"status": "ok"}

    w.pipeline.run_stage = spy


def test_round_generates_ready_deferred_day_without_new_events(tmp_path):
    """09-20 回归：待生成日静默后**无新事件** → 门控放行 → 本轮生成 ok
    （旧行为：卡到下一次有新事件到达才生成）。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    ev_ms = int(time.time() * 1000) - 3600_000
    d = ms_to_local_date(ev_ms, "Asia/Shanghai")
    _add_event(tmp_path, "r1:chat:e1", ts_ms=ev_ms, updated_at=_EV_UPDATED)
    _seed_day(tmp_path, "Asia/Shanghai", d)
    _defer_day(tmp_path, "Asia/Shanghai", d)
    w = _watcher(tmp_path)
    _stub_heavy_stages(w)
    asyncio.run(drive(w))
    with _db(tmp_path) as conn:
        row = conn.execute(
            "SELECT status, day_summary FROM day_summary "
            "WHERE timezone = 'Asia/Shanghai' AND local_date = ?",
            (d,)).fetchone()
    assert row["status"] == "ok" and row["day_summary"] != "{}"


def test_round_skips_unready_deferred_day(tmp_path):
    """待生成日事件仍新鲜（debounce 未过）→ 本轮不生成、占位行保留（节流）。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    ev_ms = int(time.time() * 1000) - 60_000
    d = ms_to_local_date(ev_ms, "Asia/Shanghai")
    _add_event(tmp_path, "r1:chat:e1", ts_ms=ev_ms, updated_at=_EV_UPDATED)
    _seed_day(tmp_path, "Asia/Shanghai", d)
    _defer_day(tmp_path, "Asia/Shanghai", d)
    w = _watcher(tmp_path)
    _stub_heavy_stages(w)
    asyncio.run(drive(w))
    with _db(tmp_path) as conn:
        row = conn.execute(
            "SELECT status FROM day_summary "
            "WHERE timezone = 'Asia/Shanghai' AND local_date = ?",
            (d,)).fetchone()
    assert row["status"] == "pending"


def test_flush_gate_force_bypasses(tmp_path):
    """显式 force（trigger）绕过门控；flush 组携带 force 标记。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    w = _watcher(tmp_path)
    calls: list[tuple[str, bool]] = []

    async def spy(stage, target=None, force=False):
        calls.append((stage, force))
        return {"status": "ok"}

    w.pipeline.run_stage = spy

    async def main():
        await w.trigger(["embed"], force=True)
        await drive(w)

    asyncio.run(main())
    by_stage = dict(calls)
    assert by_stage.get("embed") is True    # 绕过门控，force 命中
    # flush 组为整体：同组 day_summary 随之执行（force=False，水印需两者皆 ok）
    assert by_stage.get("day_summary") is False


def test_target_scoped_flush_does_not_advance_watermark(tmp_path):
    """force+session 限定的 flush 不推全局水印（否则其它会话待处理事件永久漏嵌）。"""
    _setup(tmp_path)
    _set_watermark(tmp_path, _FUTURE)
    w = _watcher(tmp_path)
    calls: list[tuple[str, object, bool]] = []

    async def spy(stage, target=None, force=False):
        calls.append((stage, target, force))
        return {"status": "ok"}

    w.pipeline.run_stage = spy

    async def main():
        await w.trigger(["embed"], force=True, session_id=SID)
        await drive(w)

    asyncio.run(main())
    by = {s: (t, f) for s, t, f in calls}
    assert by["embed"] == (SID, True)      # 目标限定 + force 命中
    with _db(tmp_path) as conn:
        row = conn.execute(
            "SELECT last_flush_at FROM flush_state WHERE id = 1").fetchone()
    assert row is not None and row[0] == _FUTURE  # 水印保持旧值（未推进）


def test_flush_watermark_not_advanced_on_failure(tmp_path):
    """flush 组失败不推水印 → 下轮重触发（崩溃/失败安全）。"""
    _setup(tmp_path)
    w = _watcher(tmp_path)

    async def spy(stage, target=None, force=False):
        if stage == "embed":
            return {"status": "failed", "error": "boom"}
        return {"status": "ok"}

    w.pipeline.run_stage = spy
    asyncio.run(drive(w))
    with _db(tmp_path) as conn:
        row = conn.execute(
            "SELECT last_flush_at FROM flush_state WHERE id = 1").fetchone()
    assert row is None            # 水印未推进
    assert w._flush_due() is True  # 下轮重触发


def test_trigger_during_round_rearms(tmp_path):
    """round 进行中到达的 trigger 不被吞：重新入队 + force 标记保留待下轮。"""
    _setup(tmp_path)
    w = _watcher(tmp_path)
    in_chatlog = asyncio.Event()

    async def slow_spy(stage, target=None, force=False):
        if stage == "chatlog":
            in_chatlog.set()
            await asyncio.sleep(0.2)
        return {"status": "ok"}

    w.pipeline.run_stage = slow_spy

    async def main():
        worker = asyncio.create_task(w._work_loop())
        await w.enqueue_round()
        await in_chatlog.wait()                    # round 进行中
        await w.trigger(["audio"], force=True)
        assert w._round_queued is True             # 已重新入队
        assert w._stage_force.get("audio") is True  # 标记待下轮消费
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    asyncio.run(main())


def test_embed_force_rebuilds_all_and_inventory_in_round(tmp_path):
    """embed force=全量；trigger(inventory) 在 round 序列内有执行载体。"""
    _setup(tmp_path)

    class FakeVec:
        def __init__(self):
            self.calls: list = []

        async def sync_changed(self):
            self.calls.append("sync")
            return 0

        async def rebuild_all(self):
            self.calls.append("all")
            return 0

        async def rebuild_session(self, sid):
            self.calls.append(("session", sid))
            return 0

    async def main():
        cfg = LvdataMcpConfig(data={"root_dir": str(tmp_path)},
                              watcher={"timezone": "Asia/Shanghai"})
        store = DataStore(cfg)
        fake = FakeVec()
        pipeline = Pipeline(cfg, store, RecordingWatch(cfg), fake)
        await pipeline.run_stage("embed", None, False)
        await pipeline.run_stage("embed", None, True)   # force → 全量
        await pipeline.run_stage("embed", SID, False)   # target → 单会话
        assert fake.calls == ["sync", "all", ("session", SID)]
        assert "inventory" in ROUND_STAGES             # trigger 载体存在
    asyncio.run(main())


def test_day_summary_discover_and_generate_flow(tmp_path):
    """poll 发现（discover）→ round 生成（generate，force=False）。"""
    _setup(tmp_path)

    class FakeDay:
        def __init__(self):
            self.generated = 0
            self.forces: list[bool] = []

        def discover(self) -> bool:
            return True

        async def generate(self, target=None, force=False) -> dict:
            self.generated += 1
            self.forces.append(force)
            return {"days": 1, "ok": 1, "failed": 0, "skipped": 0}

    async def main() -> None:
        fake = FakeDay()
        cfg = LvdataMcpConfig(data={"root_dir": str(tmp_path)},
                              watcher={"timezone": "Asia/Shanghai"})
        store = DataStore(cfg)
        pipeline = Pipeline(cfg, store, RecordingWatch(cfg))
        pipeline.day_summary = fake
        w = Watcher(cfg, store, pipeline)
        await drive(w)
        assert fake.generated == 1
        assert fake.forces == [False]

    asyncio.run(main())


def test_day_summary_force_via_trigger(tmp_path):
    """trigger(day_summary, force) → 该轮 generate 携带 force。"""
    _setup(tmp_path)

    class FakeDay:
        def __init__(self):
            self.forces: list[bool] = []

        def discover(self) -> bool:
            return False

        async def generate(self, target=None, force=False) -> dict:
            self.forces.append(force)
            return {"days": 0, "ok": 0, "failed": 0, "skipped": 0}

    async def main() -> None:
        fake = FakeDay()
        cfg = LvdataMcpConfig(data={"root_dir": str(tmp_path)},
                              watcher={"timezone": "Asia/Shanghai"})
        store = DataStore(cfg)
        pipeline = Pipeline(cfg, store, RecordingWatch(cfg))
        pipeline.day_summary = fake
        w = Watcher(cfg, store, pipeline)
        await w.trigger(["day_summary"], force=True)
        worker = asyncio.create_task(w._work_loop())
        await w.queue.join()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        assert fake.forces == [True]

    asyncio.run(main())


def test_status_reports_sync_health(tmp_path):
    _setup(tmp_path)
    w = _watcher(tmp_path)
    asyncio.run(drive(w))
    st = w.status()
    assert st["started"] is False
    assert st["last_poll_at"]
    assert st["pending_files"] >= 0
    assert "sync_lag_s" in st["sync"] and "chat_backlog" in st["sync"]
