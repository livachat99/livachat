from __future__ import annotations

import asyncio
import sqlite3
import time

from session.manager import SessionManager, _upsert_timezone

# 与 tools/lvdata-mcp/src/lvdata_mcp/ddl.py:80-88 一致（测试内联，避免跨包依赖）
_TIMEZONES_SQL = """
CREATE TABLE timezones (
    timezone   TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 1,
    priority   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


def _make_db(tmp_path, name: str = "lvdata_inventory.db"):
    db = tmp_path / name
    conn = sqlite3.connect(db)
    conn.execute(_TIMEZONES_SQL)
    conn.commit()
    conn.close()
    return str(db)


def _rows(db: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT timezone, enabled, created_at, updated_at FROM timezones").fetchall()
    conn.close()
    return rows


class TestUpsertTimezone:
    def test_inserts_valid_iana(self, tmp_path):
        db = _make_db(tmp_path)
        _upsert_timezone(db, "Asia/Shanghai")
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0]["timezone"] == "Asia/Shanghai"
        assert rows[0]["enabled"] == 1
        assert rows[0]["updated_at"] != ""

    def test_refreshes_updated_at_on_resend(self, tmp_path):
        db = _make_db(tmp_path)
        _upsert_timezone(db, "Asia/Shanghai")
        first_updated = _rows(db)[0]["updated_at"]
        time.sleep(0.02)  # strftime 毫秒精度，确保可区分
        _upsert_timezone(db, "Asia/Shanghai")
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0]["updated_at"] >= first_updated
        assert rows[0]["created_at"] <= rows[0]["updated_at"]

    def test_missing_db_skips_without_creating(self, tmp_path):
        db = str(tmp_path / "nope.db")
        _upsert_timezone(db, "Asia/Shanghai")
        assert not (tmp_path / "nope.db").exists()

    def test_db_errors_are_swallowed(self, tmp_path):
        bad_db = tmp_path / "not_a_db"
        bad_db.write_text("this is not sqlite")
        # 不应抛出任何异常（§5.3：任何 DB 异常只 log 不抛出）
        _upsert_timezone(str(bad_db), "Asia/Shanghai")


class TestSessionManagerStart:
    async def test_valid_timezone_recorded_and_upserted(self, tmp_path):
        db = _make_db(tmp_path)
        mgr = SessionManager(lvdata_db_path=db)
        ctx, _ = await mgr.start(
            device_id="dev-1", timezone="GMT+08:00", resume_id=None
        )
        assert ctx.timezone == "+08:00"
        await asyncio.gather(*mgr._bg_tasks)
        assert len(mgr._bg_tasks) == 0  # done callback 已移除
        rows = _rows(db)
        assert [r["timezone"] for r in rows] == ["+08:00"]

    async def test_invalid_timezone_ignored(self, tmp_path):
        db = _make_db(tmp_path)
        mgr = SessionManager(lvdata_db_path=db)
        ctx, _ = await mgr.start(device_id="dev-1", timezone="garbage")
        assert ctx.timezone == ""
        await asyncio.gather(*mgr._bg_tasks)
        assert _rows(db) == []

    async def test_missing_timezone(self, tmp_path):
        db = _make_db(tmp_path)
        mgr = SessionManager(lvdata_db_path=db)
        ctx, _ = await mgr.start(device_id="dev-1", timezone=None)
        assert ctx.timezone == ""
        await asyncio.gather(*mgr._bg_tasks)
        assert _rows(db) == []

    async def test_no_db_path_no_upsert(self, tmp_path):
        mgr = SessionManager()  # lvdata_db_path=None
        ctx, _ = await mgr.start(device_id="dev-1", timezone="Asia/Shanghai")
        assert ctx.timezone == "Asia/Shanghai"
        assert mgr._bg_tasks == set()

    async def test_invalid_timezone_skips_task(self, tmp_path):
        db = _make_db(tmp_path)
        mgr = SessionManager(lvdata_db_path=db)
        await mgr.start(device_id="dev-1", timezone="+15:00")
        assert mgr._bg_tasks == set()
