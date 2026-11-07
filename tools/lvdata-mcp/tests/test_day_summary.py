"""P3：day_summary 生成与调度（§6.5）。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.providers import ApiError
from lvdata_mcp.summary.day import DaySummary
from lvdata_mcp.timeutil import day_bounds_ms, now_iso

DEV = "b53985c7-0000-4000-8000-000000000001"
SID = "019fabe0-3b0f-7947-8000-1e567def787d"

# 2026-08-01 (Asia/Shanghai)
DAY = "2026-08-01"
DAY_START_MS = day_bounds_ms(DAY, "Asia/Shanghai")[0]


class FakeSummary:
    max_attempts = 3

    def __init__(self, responses: list | None = None, fail_all: bool = False):
        self.responses = list(responses or [])
        self.calls = 0
        self.blocks: list[str] = []
        self.fail_all = fail_all
        self.model = "fake-summary"

    async def chat(self, system, user_content, enable_thinking=None):
        self.calls += 1
        block = user_content[0]["text"]
        self.blocks.append(block)
        if self.fail_all:
            raise ApiError("summary boom")
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return _ok_json(), {"input_tokens": 5, "output_tokens": 3}


OK_SUMMARY = "这一天用户反馈了性能问题。"
OK_EVENTS = ["用户抱怨响应慢"]


def _ok_json() -> str:
    return json.dumps({
        "summary": OK_SUMMARY,
        "events": OK_EVENTS,
        "topics": ["性能"]}, ensure_ascii=False)


def _seed(root: Path, *, n_days: int = 1, tz: str = "Asia/Shanghai",
          start_ms: int | None = None) -> None:
    from lvdata_mcp.ddl import ensure_schema
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn, tz, [])
    conn.commit()
    conn.execute("INSERT INTO recording_sessions (rsession_id, device_uuid, "
                 "session_date, status, root_path) VALUES (?,?,?,?,?)",
                 (SID, DEV, "2026-08-01", "finalized", str(root)))
    base = start_ms if start_ms is not None else DAY_START_MS
    for i in range(n_days):
        base = base + i * 86400_000
        conn.executemany(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
            [
                (f"{SID}:chat:m{i}", DEV, SID, "chat", base + 1000,
                 "chat_messages", "为什么响应这么慢", "{}"),
                (f"{SID}:audio:s{i}", DEV, SID, "audio", base + 5000,
                 "files", "打开应用非常慢", "{}"),
            ])
    conn.commit()
    conn.close()


def _ds(root: Path, fake: FakeSummary | None = None, *,
        quiet_s: int = 600) -> DaySummary:
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "Asia/Shanghai", "summarize_quiet_s": quiet_s,
                 "day_summary_max_per_poll": 10})
    return DaySummary(cfg, client=fake)


def _row(conn, tz, d):
    return conn.execute("SELECT * FROM day_summary WHERE timezone = ? AND local_date = ?",
                        (tz, d)).fetchone()


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ── dirty 发现 ──────────────────────────────────────────────────────────

def test_discover_finds_dirty_day_and_advances_scan(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is True
    assert ds._pending == [("Asia/Shanghai", DAY)]
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    st = conn.execute("SELECT last_scan_at FROM day_scan_state "
                      "WHERE timezone = 'Asia/Shanghai'").fetchone()
    assert st["last_scan_at"]  # 游标已推进
    # 占位行落盘（崩溃恢复载体，原则 8）
    row = conn.execute("SELECT status FROM day_summary "
                       "WHERE timezone = 'Asia/Shanghai' AND local_date = ?",
                       (DAY,)).fetchone()
    assert row["status"] == "pending"
    conn.close()
    # 未生成前二次发现：占位行保持 dirty（崩溃恢复语义）
    assert ds.discover() is True
    assert ds._pending == [("Asia/Shanghai", DAY)]
    # 生成成功 → 二次发现无 dirty
    assert asyncio_run(ds.generate())["ok"] == 1
    assert ds.discover() is False


def test_discover_crash_recovery_placeholder(tmp_path):
    """崩溃恢复：discover 后未生成即崩溃 → 占位行让下轮重新发现（§5.4/原则 8）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is True  # 写入占位行后"崩溃"（_pending 丢弃）
    ds2 = _ds(root, FakeSummary(), quiet_s=0)  # 新实例 = 重启
    assert ds2.discover() is True  # 占位行重新触发
    assert ds2._pending == [("Asia/Shanghai", DAY)]
    assert asyncio_run(ds2.generate())["ok"] == 1


def test_discover_debounce_blocks_recent_day(tmp_path):
    """当天最新事件年龄 < summarize_quiet_s → 跳过（§6.5 debounce）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    _seed(root, start_ms=now_ms)
    ds = _ds(root, FakeSummary(), quiet_s=3600)  # 1h 静默窗口
    assert ds.discover() is False
    assert ds._pending == []


def test_discover_debounce_rediscovered_after_quiet(tmp_path):
    """debounce 跳过不丢 day：占位行落盘 + 游标恒推进，静默期满后仍被
    重新发现（§6.5：占位行由 _check_pending_retry 兜底，不依赖候选重扫）。"""
    from lvdata_mcp.timeutil import ms_to_local_date
    root = tmp_path / "lvdata"
    root.mkdir()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    _seed(root, start_ms=now_ms)
    today = ms_to_local_date(now_ms, "Asia/Shanghai")
    ds = _ds(root, FakeSummary(), quiet_s=3600)  # 1h 静默窗口
    assert ds.discover() is False
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    # 占位行已落（重查载体）；游标已推进（恒推进，避免每轮全量重扫）
    row = conn.execute("SELECT status FROM day_summary "
                       "WHERE timezone = 'Asia/Shanghai' AND local_date = ?",
                       (today,)).fetchone()
    assert row is not None and row["status"] == "pending"
    st = conn.execute("SELECT last_scan_at FROM day_scan_state "
                      "WHERE timezone = 'Asia/Shanghai'").fetchone()
    assert st is not None and st["last_scan_at"]
    # 事件变旧（静默期满）→ 无需新事件，下一轮发现即入队（占位行兜底）
    conn.execute("UPDATE events SET ts_ms = ts_ms - 2 * 3600 * 1000")
    conn.commit()
    conn.close()
    assert ds.discover() is True
    assert ("Asia/Shanghai", today) in ds._pending


def test_discover_deferred_ok_day_with_new_events(tmp_path):
    """已 ok 日 + debounce 中新事件：游标恒推进后靠 day_deferred 表再发现（§6.5）。

    回归：原实现 pinned 不推进游标（N+1 重扫）；若简单恒推进则 ok 行场景
    无占位行兜底（INSERT OR IGNORE 不覆盖 ok 行），新事件永久丢失。
    """
    from lvdata_mcp.timeutil import ms_to_local_date
    root = tmp_path / "lvdata"
    root.mkdir()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    _seed(root, start_ms=now_ms - 60_000)  # 首轮事件 1 分钟前（避免未来 ts）
    today = ms_to_local_date(now_ms, "Asia/Shanghai")
    # 首轮：quiet_s=0 直接生成 ok
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is True
    assert asyncio_run(ds.generate())["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    # 新事件落在今天（updated_at 刷新），debounce 1h 未过 → 不入队、deferred 落表
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
        "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        (f"{SID}:chat:late", DEV, SID, "chat", now_ms - 30_000,
         "chat_messages", "迟到的消息", "{}"))
    conn.commit()
    ds2 = _ds(root, FakeSummary(), quiet_s=3600)
    assert ds2.discover() is False
    row = conn.execute("SELECT 1 FROM day_deferred "
                       "WHERE timezone = 'Asia/Shanghai' AND local_date = ?",
                       (today,)).fetchone()
    assert row is not None  # defer 落表（持久化载体）
    # 第二轮（游标已推进，无新候选）：deferred 表兜底再发现，debounce 仍不过
    assert ds2.discover() is False
    # 事件变旧（静默期满）→ deferred 入队并清除
    conn.execute("UPDATE events SET ts_ms = ts_ms - 2 * 3600 * 1000")
    conn.commit()
    assert ds2.discover() is True
    assert ("Asia/Shanghai", today) in ds2._pending
    row = conn.execute("SELECT 1 FROM day_deferred "
                       "WHERE timezone = 'Asia/Shanghai' AND local_date = ?",
                       (today,)).fetchone()
    assert row is None  # 入队即清除
    conn.close()


def test_deferred_survives_restart(tmp_path):
    """崩溃安全（原则 8）：ok 日 + debounce 中更新的 defer 落表，重启后
    新实例仍能再发现——纯内存 defer 会在崩溃后永久丢失该日更新（游标已
    越过事件、ok 行不在 _check_pending_retry 范围）。"""
    from lvdata_mcp.timeutil import ms_to_local_date
    root = tmp_path / "lvdata"
    root.mkdir()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    _seed(root, start_ms=now_ms - 60_000)
    today = ms_to_local_date(now_ms, "Asia/Shanghai")
    # 首轮生成 ok
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is True
    assert asyncio_run(ds.generate())["ok"] == 1
    # 迟到事件 + debounce → defer 落表
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
        "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        (f"{SID}:chat:late", DEV, SID, "chat", now_ms - 30_000,
         "chat_messages", "迟到的消息", "{}"))
    conn.commit()
    ds2 = _ds(root, FakeSummary(), quiet_s=3600)
    assert ds2.discover() is False
    # 「崩溃」：丢弃 ds2（内存态清零），新实例 = 重启
    conn.close()
    # 静默期满后，重启实例靠 day_deferred 表再发现该日
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    conn.execute("UPDATE events SET ts_ms = ts_ms - 2 * 3600 * 1000")
    conn.commit()
    conn.close()
    ds3 = _ds(root, FakeSummary(), quiet_s=3600)
    assert ds3.discover() is True
    assert ("Asia/Shanghai", today) in ds3._pending


def test_discover_limit_overflow_batches_via_placeholder(tmp_path):
    """批限溢出不丢 day：超出 day_summary_max_per_poll 的 dirty day 落占位
    行，第一批生成后经 _check_pending_retry 重新发现（§8.1 新时区首扫分批
    消化；回归：原实现溢出 day 不落行，游标已推进 → 永久丢失）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, n_days=2)
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "Asia/Shanghai", "summarize_quiet_s": 0,
                 "day_summary_max_per_poll": 1})
    ds = DaySummary(cfg, client=FakeSummary())
    assert ds.discover() is True
    assert len(ds._pending) == 1
    first = ds._pending[0]
    assert asyncio_run(ds.generate())["ok"] == 1  # 第一批生成 → ok
    assert ds.discover() is True  # 溢出 day 经占位行重新发现
    assert len(ds._pending) == 1
    assert ds._pending[0] != first


def test_discover_failed_retry_after_backoff(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    # 失败行：fail_count=1、generated_at=尝试时刻（退避 60s）
    conn.execute(
        "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
        "day_start_ms, day_end_ms, status, fail_count, generated_at) "
        "VALUES ('Asia/Shanghai', ?, 0, 0, 0, 'failed', 1, ?)",
        (DAY, now_iso()))
    conn.commit()
    conn.close()
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is False  # 退避窗口内 → 不重试
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("UPDATE day_summary SET generated_at = '2000-01-01T00:00:00.000Z'")
    conn.commit()
    conn.close()
    assert ds.discover() is True  # 退避期满 → 重试
    assert ("Asia/Shanghai", DAY) in ds._pending


def test_discover_failed_paused_after_3(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    from lvdata_mcp.ddl import ensure_schema
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()  # 无事件：唯一 dirty 候选 = 失败行本身
    conn.execute(
        "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
        "day_start_ms, day_end_ms, status, fail_count, generated_at) "
        "VALUES ('Asia/Shanghai', ?, 0, 0, 0, 'failed', 3, '2000-01-01T00:00:00.000Z')",
        ("2026-08-02",))  # 无事件日：仅验证失败重试暂停路径
    conn.commit()
    conn.close()
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is False  # fail_count >= 3 → 暂停（§5.4）


def test_discover_cleans_orphan_pending_without_events(tmp_path):
    """回归（08-24 案例）：事件被清理后残留的 pending 行每轮判 dirty →
    debounce 查无事件 → 落 day_deferred 永久忙循环。discover 应直接清理。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    ds = _ds(root, FakeSummary(), quiet_s=0)
    ds.discover()
    assert asyncio_run(ds.generate())["ok"] == 1  # 08-01 生成 ok
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("DELETE FROM events")  # 模拟事件被清理删除
    conn.execute("UPDATE day_summary SET status = 'pending', generated_at = NULL")
    conn.commit()
    conn.close()
    assert ds.discover() is False  # 无事件 → 清理而非入队
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    assert _row(conn, "Asia/Shanghai", DAY) is None  # 残留行被删
    assert conn.execute(
        "SELECT COUNT(*) FROM day_deferred").fetchone()[0] == 0
    conn.close()


def test_discover_multi_timezone(tmp_path):
    """活跃集 = 最近使用 + 配置默认（§8.2）：两时区都在活跃集 → 都被遍历。

    updated_at 显式指定，避免与 seed 毫秒竞态（旧断言随机性来源）。
    """
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, tz="Asia/Shanghai")
    _add_tz(root / "lvdata_inventory.db", "UTC", "2099-01-01T00:00:00.000Z")
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is True
    assert {tz for tz, _ in ds._pending} == {"Asia/Shanghai", "UTC"}


# ── 活跃时区集（§8.2/§8.3）────────────────────────────────────────────

def _add_tz(db, tz: str, updated_at: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO timezones (timezone, updated_at) VALUES (?, ?)", (tz, updated_at))
    conn.commit()
    conn.close()


def _set_updated_at(db, tz: str, updated_at: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute("UPDATE timezones SET updated_at = ? WHERE timezone = ?",
                 (updated_at, tz))
    conn.commit()
    conn.close()


def _set_enabled(db, tz: str, enabled: int) -> None:
    conn = sqlite3.connect(db)
    conn.execute("UPDATE timezones SET enabled = ? WHERE timezone = ?", (enabled, tz))
    conn.commit()
    conn.close()


def test_active_timezones_recent_plus_default(tmp_path):
    """活跃集 = 最近使用（ORDER BY updated_at DESC, timezone）+ 配置默认，有序去重。

    updated_at 显式设置（seed 的 DEFAULT 是 UTC now，避免时序依赖）。
    """
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, tz="Asia/Shanghai")
    _set_updated_at(root / "lvdata_inventory.db", "Asia/Shanghai", "2026-08-28T10:00:00.000Z")
    _add_tz(root / "lvdata_inventory.db", "UTC", "2026-08-29T10:00:00.000Z")
    _add_tz(root / "lvdata_inventory.db", "America/New_York", "2026-08-27T10:00:00.000Z")
    ds = _ds(root, FakeSummary(), quiet_s=0)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    assert ds._active_timezones(conn) == ["UTC", "Asia/Shanghai"]
    conn.close()


def test_active_timezones_recent_is_default_dedup(tmp_path):
    """最近使用 == 配置默认 → 仅 1 个。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, tz="Asia/Shanghai")
    ds = _ds(root, FakeSummary(), quiet_s=0)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    assert ds._active_timezones(conn) == ["Asia/Shanghai"]
    conn.close()


def test_active_timezones_default_missing(tmp_path):
    """配置默认不在表 → 活跃集仅最近使用。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, tz="Asia/Shanghai")
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("DELETE FROM timezones WHERE timezone = 'Asia/Shanghai'")
    conn.commit()
    conn.close()
    _add_tz(root / "lvdata_inventory.db", "UTC", "2026-08-29T10:00:00.000Z")
    ds = _ds(root, FakeSummary(), quiet_s=0)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    assert ds._active_timezones(conn) == ["UTC"]
    conn.close()


def test_active_timezones_default_disabled(tmp_path):
    """配置默认被 disabled → 不占槽位（enabled = 0 仍是总开关）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, tz="Asia/Shanghai")
    _set_enabled(root / "lvdata_inventory.db", "Asia/Shanghai", 0)
    _add_tz(root / "lvdata_inventory.db", "UTC", "2026-08-29T10:00:00.000Z")
    ds = _ds(root, FakeSummary(), quiet_s=0)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    assert ds._active_timezones(conn) == ["UTC"]
    conn.close()


def test_active_timezones_recent_disabled_takes_next_enabled(tmp_path):
    """最近使用时区被 disabled → 取次近 enabled。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, tz="Asia/Shanghai")
    _set_updated_at(root / "lvdata_inventory.db", "Asia/Shanghai", "2026-08-27T10:00:00.000Z")
    _add_tz(root / "lvdata_inventory.db", "UTC", "2026-08-28T10:00:00.000Z")
    _add_tz(root / "lvdata_inventory.db", "America/New_York", "2026-08-29T10:00:00.000Z")
    _set_enabled(root / "lvdata_inventory.db", "America/New_York", 0)
    ds = _ds(root, FakeSummary(), quiet_s=0)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    assert ds._active_timezones(conn) == ["UTC", "Asia/Shanghai"]
    conn.close()


def test_discover_inactive_timezone_preserved(tmp_path):
    """非活跃时区不被遍历：pending/游标行原样保留（§8.3）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, tz="Asia/Shanghai")
    _set_updated_at(root / "lvdata_inventory.db", "Asia/Shanghai", "2026-08-28T10:00:00.000Z")
    _add_tz(root / "lvdata_inventory.db", "UTC", "2026-08-29T10:00:00.000Z")
    # 非活跃时区：旧 updated_at，且带未消费的 pending/游标行
    _add_tz(root / "lvdata_inventory.db", "America/New_York", "2026-08-01T00:00:00.000Z")
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO day_scan_state (timezone, last_scan_at) "
                 "VALUES ('America/New_York', '2026-08-01T00:00:00.000Z')")
    conn.execute(
        "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
        "day_start_ms, day_end_ms, status, fail_count, generated_at) "
        "VALUES ('America/New_York', '2026-08-02', 0, 0, 0, 'pending', 0, NULL)")
    conn.commit()
    conn.close()
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is True  # Asia/Shanghai 有 dirty
    assert all(tz != "America/New_York" for tz, _ in ds._pending)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT last_scan_at FROM day_scan_state "
                        "WHERE timezone = 'America/New_York'").fetchone()[
                            "last_scan_at"] == "2026-08-01T00:00:00.000Z"  # 游标停滞
    assert conn.execute("SELECT status FROM day_summary WHERE timezone = 'America/New_York'"
                        ).fetchone()["status"] == "pending"  # 占位行不消费
    conn.close()


def test_discover_reactivation_resumes_from_stalled_cursor(tmp_path):
    """再激活（再次成为最近使用）→ 从停滞游标续跑，积压日入队（§8.3）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, tz="Asia/Shanghai")
    _set_updated_at(root / "lvdata_inventory.db", "Asia/Shanghai", "2026-08-28T10:00:00.000Z")
    _add_tz(root / "lvdata_inventory.db", "UTC", "2026-08-29T10:00:00.000Z")
    _add_tz(root / "lvdata_inventory.db", "America/New_York", "2026-08-01T00:00:00.000Z")
    # 非活跃期间：给 America/New_York 累积事件（在 Asia/Shanghai 本地日之外，
    # 避免污染其它时区候选）——UTC 时刻 2026-08-01T00:00:00Z → 纽约本地 07-31 20:00
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
        "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        (f"{SID}:chat:ny", DEV, SID, "chat",
         int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000),
         "chat_messages", "纽约消息", "{}"))
    conn.commit()
    conn.close()
    ds = _ds(root, FakeSummary(), quiet_s=0)
    ds.discover()  # 非活跃：不遍历 America/New_York
    assert all(tz != "America/New_York" for tz, _ in ds._pending)
    # 返程后再次 touch（UPDATE 而非 INSERT：行已存在）→ 最近使用 → 下轮即入活跃集
    _set_updated_at(root / "lvdata_inventory.db", "America/New_York", "2099-01-01T00:00:00.000Z")
    assert ds.discover() is True
    assert ("America/New_York", "2026-07-31") in ds._pending


def test_discover_dirty_days_desc_order(tmp_path):
    """时区内 dirty 日按日期 DESC（最近日优先，§8.2）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, n_days=2)  # 08-01 与 08-02 都有事件
    ds = _ds(root, FakeSummary(), quiet_s=0)
    assert ds.discover() is True
    assert ds._pending == [("Asia/Shanghai", "2026-08-02"),
                           ("Asia/Shanghai", "2026-08-01")]


# ── 生成 ────────────────────────────────────────────────────────────────

def test_generate_writes_day_summary(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    fake = FakeSummary()
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()
    res = asyncio_run(ds.generate())
    assert res["ok"] == 1 and res["failed"] == 0
    assert "[00:00] chat: 为什么响应这么慢" in fake.blocks[0]
    assert "[00:00] audio: 打开应用非常慢" in fake.blocks[0]
    # 输入注入：日期 + 时区（prompt 改版 §3.3）
    assert fake.blocks[0].startswith(
        f"local_date: {DAY}\ntimezone: Asia/Shanghai\n")
    # 单设备不变式（§12）：不出现设备段头
    assert "设备 " not in fake.blocks[0]
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    row = _row(conn, "Asia/Shanghai", DAY)
    assert row["status"] == "ok" and row["event_count"] == 2
    assert row["model"] == "fake-summary"
    # utc_offset_min = +480（Asia/Shanghai 日首）
    assert row["utc_offset_min"] == 480
    # day_start/end 与 day_bounds_ms 一致（闭区间）
    assert row["day_start_ms"] == DAY_START_MS
    doc = json.loads(row["day_summary"])
    assert doc["schema_version"] == 1
    assert doc["timezone"] == "Asia/Shanghai"
    assert doc["local_date"] == DAY
    assert doc["generated_at"] == row["generated_at"]
    assert doc["stats"]["events"] == 2
    assert doc["stats"]["chat_messages"] == 1  # TYPE_MAP：events.type → stats 键名
    assert doc["stats"]["audio_segments"] == 1
    assert doc["summary"] == OK_SUMMARY   # §14：仅 LLM 总述，不拼接
    assert doc["moments"] == OK_EVENTS    # §15：LLM events 落 doc 为 moments
    assert doc["topics"] == [{"topic": "性能", "search_words": None}]
    assert "highlights" not in doc and "mood" not in doc
    assert doc["usage"] == {"input_tokens": 5, "output_tokens": 3}
    conn.close()


@pytest.mark.parametrize("bad", [
    {"summary": "只有总结", "events": ["一条"]},   # 缺 topics
    {"summary": "只有总结", "topics": ["性能"]},   # 缺 events
])
def test_generate_required_keys_new_schema(tmp_path, bad):
    """新 schema 必需键 = (summary, events, topics)：末次仍缺 → 该日 failed。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    usage = {"input_tokens": 5, "output_tokens": 3}
    payload = json.dumps(bad, ensure_ascii=False)
    ds = _ds(root, FakeSummary(responses=[(payload, usage)] * 3), quiet_s=0)
    ds.discover()
    res = asyncio_run(ds.generate())
    assert res["failed"] == 1 and res["ok"] == 0
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    row = _row(conn, "Asia/Shanghai", DAY)
    assert row["status"] == "failed" and row["fail_count"] == 1
    assert "schema missing" in row["error"]
    conn.close()


def test_generate_multiline_summary_and_bad_escape_retry(tmp_path):
    """多行 summary 原样落库；字符串内真实换行（坏 JSON）→ 重试后成功。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    bad = '{"summary": "第一行\n第二行", "topics": []}'  # 未转义换行 = 坏 JSON
    usage = {"input_tokens": 5, "output_tokens": 3}
    fake = FakeSummary(responses=[(bad, usage)])
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()
    res = asyncio_run(ds.generate())
    assert res["ok"] == 1
    assert fake.calls == 2  # 1 次坏 JSON（重试）+ 1 次成功
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    doc = json.loads(_row(conn, "Asia/Shanghai", DAY)["day_summary"])
    assert doc["summary"] == OK_SUMMARY  # 总述经 JSON 往返原样保留
    conn.close()


@pytest.mark.parametrize("resp, expect_events", [
    ({"summary": "总述。", "events": ["事件一", "事件二"],
      "topics": ["性能", "协作"]},
     ["事件一", "事件二"]),
    ({"summary": "平淡的一天。", "events": [], "topics": []}, []),
    ({"summary": "总述。", "events": [f"e{i}" for i in range(1, 12)],
      "topics": []},
     [f"e{i}" for i in range(1, 11)]),  # 超发截断 10 条
])
def test_generate_moments_stored_and_summary_passthrough(tmp_path, resp,
                                                         expect_events):
    """§14/§15：doc.summary 仅 LLM 总述（不拼接）；LLM events 以 doc 字段
    `moments` 独立落库（截断 10 条）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    usage = {"input_tokens": 5, "output_tokens": 3}
    ds = _ds(root, FakeSummary(
        responses=[(json.dumps(resp, ensure_ascii=False), usage)]), quiet_s=0)
    ds.discover()
    assert asyncio_run(ds.generate())["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    doc = json.loads(_row(conn, "Asia/Shanghai", DAY)["day_summary"])
    assert doc["summary"] == resp["summary"]
    assert doc["moments"] == expect_events
    assert [t["topic"] for t in doc["topics"]] == [t for t in resp["topics"]]
    conn.close()


def test_generate_topics_object_schema_and_tolerance(tmp_path):
    """§10 T2：topics 对象化落库；缺/空白 search_words → None；畸形条目丢弃。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    resp = {"summary": "总述。", "events": ["事件一"], "topics": [
        {"topic": "性能", "search_words": "启动耗时"},
        {"topic": " 协作 ", "search_words": "   "},   # 空白 search_words → None
        {"search_words": "没有主题名"},               # 缺 topic → 丢弃
        "", 42,                                      # 空串 / 非容器 → 丢弃
        {"topic": ""},                               # 空 topic → 丢弃
    ]}
    usage = {"input_tokens": 5, "output_tokens": 3}
    ds = _ds(root, FakeSummary(
        responses=[(json.dumps(resp, ensure_ascii=False), usage)]), quiet_s=0)
    ds.discover()
    assert asyncio_run(ds.generate())["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    doc = json.loads(_row(conn, "Asia/Shanghai", DAY)["day_summary"])
    assert doc["topics"] == [{"topic": "性能", "search_words": "启动耗时"},
                             {"topic": "协作", "search_words": None}]
    conn.close()


def test_generate_llm_input_labels(tmp_path):
    """输入行标签（§11）：chat(user)/chat(assistant)/video window/video frame；
    缺 role 回退裸 chat（_seed 的 chat 事件 payload 为 "{}"）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    conn.executemany(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        [
            (f"{SID}:chat:u1", DEV, SID, "chat", DAY_START_MS + 60_000,
             "chat_messages", "早上好，今天要开会",
             json.dumps({"role": "user"}, ensure_ascii=False)),
            (f"{SID}:chat:a1", DEV, SID, "chat", DAY_START_MS + 120_000,
             "chat_messages", "好的，我记下了",
             json.dumps({"role": "assistant"}, ensure_ascii=False)),
            (f"{SID}:window:w1", DEV, SID, "window", DAY_START_MS + 180_000,
             "files", "屏幕为文档编辑器", "{}"),
            (f"{SID}:video:v1", DEV, SID, "video", DAY_START_MS + 240_000,
             "files", "画面出现白板", "{}"),
        ])
    conn.commit()
    conn.close()
    fake = FakeSummary()
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()
    assert asyncio_run(ds.generate())["ok"] == 1
    block = fake.blocks[0]
    assert "[00:00] chat: 为什么响应这么慢" in block  # 缺 role 回退
    assert "[00:01] chat(user): 早上好，今天要开会" in block
    assert "[00:02] chat(assistant): 好的，我记下了" in block
    assert "[00:03] video window: 屏幕为文档编辑器" in block
    assert "[00:04] video frame: 画面出现白板" in block


def test_generate_multi_device_segments(tmp_path):
    """多设备（§12）：按首现序分段、段头 `设备 <uuid8>：`、段间空行；
    段内不含他设备事件；同 ms 跨设备不再相邻。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)  # DEV 的 chat(00:00) + audio(00:00)，先入库 → 首现在前
    dev2 = "cccc3333-0000-4000-8000-000000000002"
    sess2 = "019fabe0-3b0f-7947-8000-1e567def787e"
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    conn.executemany(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        [
            # 与 dev1 的 chat 同 ms：验证跨设备不再相邻
            (f"{sess2}:chat:x1", dev2, sess2, "chat", DAY_START_MS + 1000,
             "chat_messages", "另一台设备的话",
             json.dumps({"role": "user"}, ensure_ascii=False)),
            (f"{sess2}:window:x2", dev2, sess2, "window",
             DAY_START_MS + 120_000, "files", "第二设备的屏幕", "{}"),
        ])
    conn.commit()
    conn.close()
    fake = FakeSummary()
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()
    assert asyncio_run(ds.generate())["ok"] == 1
    block = fake.blocks[0]
    h1, h2 = f"设备 {DEV[:8]}：", f"设备 {dev2[:8]}："
    assert h1 in block and h2 in block
    assert block.index(h1) < block.index(h2)      # 段序 = 首现序
    assert f"\n\n{h2}" in block                   # 段间空行
    seg1, seg2 = block.split(h1)[1].split(h2)[0], block.split(h2)[1]
    assert "为什么响应这么慢" in seg1 and "打开应用非常慢" in seg1
    assert "另一台设备的话" not in seg1           # 段内不含他设备事件
    assert "另一台设备的话" in seg2 and "第二设备的屏幕" in seg2
    assert "为什么响应这么慢" not in seg2
    assert seg1.index("为什么响应这么慢") < seg1.index("打开应用非常慢")


def test_generate_snapshot_semantics(tmp_path):
    """generated_at = 输入快照时刻 T0：生成期间落入的事件（updated_at > T0）
    下轮 dirty 必中（§5.4 快照语义，无 TOCTOU 丢失窗）。"""
    import asyncio
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)

    class SlowSummary(FakeSummary):
        async def chat(self, system, user_content, enable_thinking=None):
            import asyncio
            await asyncio.sleep(0.05)  # 模拟生成耗时
            return await super().chat(system, user_content, enable_thinking)

    fake = SlowSummary()
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()

    async def run():
        # 生成期间并发落入事件（updated_at > T0）
        res = await ds.generate()
        return res

    async def main():
        import asyncio
        task = asyncio.create_task(ds.generate())
        await asyncio.sleep(0.01)
        conn = sqlite3.connect(root / "lvdata_inventory.db")
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
            (f"{SID}:chat:late", DEV, SID, "chat", DAY_START_MS + 9000,
             "chat_messages", "迟到的消息", "{}"))
        conn.commit()
        conn.close()
        return await task

    res = asyncio.run(main())
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    row = _row(conn, "Asia/Shanghai", DAY)
    doc = json.loads(row["day_summary"])
    assert doc["stats"]["events"] == 2  # 快照输入（迟到事件不在内）
    conn.close()
    # 下轮发现：迟到事件 updated_at > generated_at → dirty → 重算
    ds2 = _ds(root, fake, quiet_s=0)
    assert ds2.discover() is True


def test_generate_failure_and_retry_watermark(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    fake = FakeSummary(fail_all=True)
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()
    res = asyncio_run(ds.generate())
    assert res["failed"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    row = _row(conn, "Asia/Shanghai", DAY)
    assert row["status"] == "failed" and row["fail_count"] == 1
    assert row["generated_at"]  # 尝试时刻 = 重试水位
    conn.close()


def test_generate_no_events_skips_row(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    ds = _ds(root, FakeSummary(), quiet_s=0)
    ds._pending = [("Asia/Shanghai", "2026-08-02")]  # 无事件日
    res = asyncio_run(ds.generate())
    assert res["skipped"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert _row(conn, "Asia/Shanghai", "2026-08-02") is None  # 0 事件不落行（§5.4）
    conn.close()


def test_generate_no_events_leaves_no_busy_loop(tmp_path):
    """0 事件日不落行（§5.4）；discover 不再重新入队（无 day_force 后无忙循环源）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    ds = _ds(root, FakeSummary(), quiet_s=0)
    ds.discover()
    asyncio_run(ds.generate())  # 08-01 完成，游标推进
    ds._pending = [("Asia/Shanghai", "2026-08-02")]  # 无事件日
    assert asyncio_run(ds.generate())["skipped"] == 1
    assert ds.discover() is False  # 08-01 ok、08-02 无事件 → 不再重新入队


def test_generate_skips_when_no_new_events(tmp_path):
    """幂等护栏：ok 行且无 updated_at > generated_at 的事件 → 不重复调 LLM
    （discover 与 generate 竞态下的重复入队不再浪费调用）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    fake = FakeSummary()
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()
    assert asyncio_run(ds.generate())["ok"] == 1
    calls = fake.calls
    ds._pending = [("Asia/Shanghai", DAY)]  # 模拟重复入队
    res = asyncio_run(ds.generate())
    assert res["skipped"] == 1 and res["ok"] == 0
    assert fake.calls == calls  # 未产生新 LLM 调用


def test_generate_force_resets_and_runs_all(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root, n_days=2)
    ds = _ds(root, FakeSummary(), quiet_s=0)
    ds.discover()
    asyncio_run(ds.generate())  # 两日 ok
    # 模拟后续重分析弄脏 08-02：force 重置重跑
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
        "day_start_ms, day_end_ms, status, fail_count, generated_at) "
        "VALUES ('Asia/Shanghai', '2026-08-02', 0, 0, 0, 'failed', 2, "
        "'2000-01-01T00:00:00.000Z') "
        "ON CONFLICT(timezone, local_date) DO UPDATE SET status = 'failed', "
        "fail_count = 2, generated_at = '2000-01-01T00:00:00.000Z'")
    conn.commit()
    conn.close()
    res = asyncio_run(ds.generate(force=True))
    # force 覆盖所有 day_summary 行（ok + 非 ok），语言迁移需要
    assert res["days"] == 2 and res["ok"] == 2
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    row = _row(conn, "Asia/Shanghai", "2026-08-02")
    assert row["status"] == "ok" and row["fail_count"] == 0
    conn.close()


# ── DST 边界（§6.5）──────────────────────────────────────────────────────

def _event_ms(d: str, hhmm: str, tz: str) -> int:
    from zoneinfo import ZoneInfo
    dt = datetime.strptime(f"{d} {hhmm}", "%Y-%m-%d %H:%M")
    return int(dt.replace(tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def test_generate_dst_spring_forward(tmp_path):
    """America/New_York 3 月切换日（23h 日）：日界与偏移按日期正确（§6.5 DST）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    from lvdata_mcp.ddl import ensure_schema
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn, "America/New_York", [])
    conn.commit()
    d = "2026-03-08"  # 春令时：02:00 跳到 03:00
    conn.executemany(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
        "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("e1", DEV, SID, "chat", _event_ms(d, "00:30", "America/New_York"),
             "chat_messages", "切换前", "{}"),
            ("e2", DEV, SID, "chat", _event_ms(d, "03:30", "America/New_York"),
             "chat_messages", "切换后", "{}"),
        ])
    conn.commit()
    conn.close()
    fake = FakeSummary()
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "America/New_York", "summarize_quiet_s": 0})
    ds = DaySummary(cfg, client=fake)
    ds._pending = [("America/New_York", d)]
    res = asyncio_run(ds.generate())
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    row = _row(conn, "America/New_York", d)
    assert row["event_count"] == 2  # 跨偏移的同一本地日事件都计入
    assert row["utc_offset_min"] == -300  # 日首 EST（-5h）
    assert row["day_start_ms"] == _event_ms(d, "00:00", "America/New_York")
    assert row["day_end_ms"] == _event_ms("2026-03-09", "00:00", "America/New_York") - 1
    doc = json.loads(row["day_summary"])
    assert doc["stats"]["events"] == 2
    conn.close()


def test_generate_dst_fall_back(tmp_path):
    """America/New_York 11 月切换日（25h 日）：重复小时不丢事件。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    from lvdata_mcp.ddl import ensure_schema
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn, "America/New_York", [])
    conn.commit()
    d = "2026-11-01"  # 冬令时：01:59 重复一次
    conn.executemany(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
        "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("e1", DEV, SID, "chat", _event_ms(d, "00:30", "America/New_York"),
             "chat_messages", "第一次", "{}"),
            ("e2", DEV, SID, "chat", _event_ms(d, "01:30", "America/New_York"),
             "chat_messages", "第二次", "{}"),
            ("e3", DEV, SID, "chat", _event_ms(d, "23:30", "America/New_York"),
             "chat_messages", "深夜", "{}"),
        ])
    conn.commit()
    conn.close()
    fake = FakeSummary()
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "America/New_York", "summarize_quiet_s": 0})
    ds = DaySummary(cfg, client=fake)
    ds._pending = [("America/New_York", d)]
    res = asyncio_run(ds.generate())
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    row = _row(conn, "America/New_York", d)
    assert row["event_count"] == 3
    assert row["utc_offset_min"] == -240  # 日首 EDT（-4h）
    conn.close()


def test_generate_llm_input_truncates_events(tmp_path):
    """每事件 summary 截断到 max_chars_per_event（§6.5.3）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute(
        "UPDATE events SET summary = ? WHERE type = 'chat'",
        ("这是一个超长的事件摘要文本，需要被截断处理",))
    conn.commit()
    conn.close()
    fake = FakeSummary()
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "Asia/Shanghai", "summarize_quiet_s": 0,
                 "max_chars_per_event": 10})
    ds = DaySummary(cfg, client=fake)
    ds._pending = [("Asia/Shanghai", DAY)]
    asyncio_run(ds.generate())
    block = fake.blocks[0]
    line = next(l for l in block.splitlines() if "chat:" in l)
    assert len(line.split(": ", 1)[1]) == 10
    assert "超长的事件摘要" not in line


# ── 输入去重（§10）：audio-with-text 不进 day_summary ──────────────────────


def _audio_payload(transcript: str) -> str:
    return json.dumps({"transcript": transcript, "value": "high"},
                      ensure_ascii=False)


def test_generate_excludes_audio_with_transcript(tmp_path):
    """§10：audio 且 transcript 非空 → 排除出 LLM 输入/event_count；
    transcript 空（环境声）→ 保留进入日总结。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("UPDATE events SET payload = ? WHERE type = 'audio'",
                 (_audio_payload("打开应用非常慢，这到底是为啥呢"),))
    conn.execute(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
        (f"{SID}:audio:sound2", DEV, SID, "audio", DAY_START_MS + 9000,
         "files", "一段环境音：雨声与雷声", _audio_payload("")))
    conn.commit()
    conn.close()
    fake = FakeSummary()
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()
    assert asyncio_run(ds.generate())["ok"] == 1
    block = fake.blocks[0]
    assert "为什么响应这么慢" in block        # chat 行照常
    assert "雨声与雷声" in block              # 无文字 audio 进入
    # 带文字 audio 排除——须断言 summary 列原文（渲染行只含 summary 列；
    # 曾误断言 payload 转写串，过滤器短路时该断言仍会通过=空断言）
    assert "打开应用非常慢" not in block
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert _row(conn, "Asia/Shanghai", DAY)["event_count"] == 2  # chat + 无文字 audio
    doc = json.loads(_row(conn, "Asia/Shanghai", DAY)["day_summary"])
    assert doc["stats"]["audio_segments"] == 1        # 进入日总结的非语音段数
    assert doc["stats"]["audio_segments_total"] == 2  # 当日 audio 事件真实总数
    conn.close()


def test_audio_only_update_does_not_regenerate(tmp_path):
    """§10 护栏：仅 audio-with-text 的 updated_at 变新（如 force 重分析）
    → up_to_date skip，不产生新 LLM 调用。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("UPDATE events SET payload = ? WHERE type = 'audio'",
                 (_audio_payload("打开应用非常慢"),))
    conn.commit()
    conn.close()
    fake = FakeSummary()
    ds = _ds(root, fake, quiet_s=0)
    ds.discover()
    assert asyncio_run(ds.generate())["ok"] == 1
    calls = fake.calls
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("UPDATE events SET updated_at = ? WHERE type = 'audio'",
                 (now_iso(),))
    conn.commit()
    conn.close()
    ds._pending = [("Asia/Shanghai", DAY)]  # 模拟 dirty 再入队
    res = asyncio_run(ds.generate())
    assert res["skipped"] == 1 and res["ok"] == 0
    assert fake.calls == calls  # 未产生新 LLM 调用


def test_generate_audio_only_day_skips_no_events(tmp_path):
    """§10：过滤后空集（当日仅剩 audio-with-text）→ no_events 语义（删占位、skip）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("DELETE FROM events WHERE type = 'chat'")
    conn.execute("UPDATE events SET payload = ? WHERE type = 'audio'",
                 (_audio_payload("有文字的段落"),))
    conn.commit()
    conn.close()
    ds = _ds(root, FakeSummary(), quiet_s=0)
    ds.discover()
    res = asyncio_run(ds.generate())
    assert res["skipped"] == 1 and res["ok"] == 0
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert _row(conn, "Asia/Shanghai", DAY) is None  # 占位行被删（no_events 语义）
    conn.close()
