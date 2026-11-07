"""v2 FTS 索引测试（§6.6：数据源 = events）。"""

from __future__ import annotations

import sqlite3

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.index import (
    build_fts_query,
    cjk_bigram,
    ensure_index,
    is_stopword_query,
    lag_sessions,
    rebuild_all,
    rebuild_session,
    search,
)
from lvdata_mcp.store import DataStore

from conftest import DEVICE, SID, make_sample


def _conn(root):
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    return conn


def _store(root):
    return DataStore(LvdataMcpConfig(data={"root_dir": str(root)}, watcher={"timezone": "Asia/Shanghai"}))


def test_cjk_bigram():
    assert cjk_bigram("打开应用") == "打开 开应 应用"
    assert cjk_bigram("hello world") == "hello world"
    assert cjk_bigram("打开应用 slow") == "打开 开应 应用  slow"


def test_build_fts_query():
    assert build_fts_query("响应慢") == '"响应 应慢"'
    assert build_fts_query("hello 世界") == '"hello" "世界"'
    # 标点/关键字一律加引号（2026-09-21 修复：原样透传会触 FTS5 语法错）
    assert build_fts_query("v1.2.3") == '"v1.2.3"'
    assert build_fts_query("a AND b") == '"a" "AND" "b"'


def test_is_stopword_query():
    assert is_stopword_query("的")
    assert is_stopword_query("   ")
    assert not is_stopword_query("响应慢")


def test_rebuild_session_from_events(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    store = _store(root)
    with _conn(root) as conn:
        ensure_index(conn)
        n = rebuild_session(conn, store, SID)
        conn.commit()
    assert n == 5  # 5 条 events 全部入索引（chat×2 + audio + window + video）
    with _conn(root) as conn:
        rows = conn.execute("SELECT * FROM search_docs").fetchall()
        types = {r["type"] for r in rows}
        assert types == {"chat", "audio", "window", "video"}
        # text = events.summary 的 bigram（检索/LLM 三面一致）
        ev = conn.execute(
            "SELECT summary FROM events WHERE type = 'chat' LIMIT 1").fetchone()
        assert any(cjk_bigram(ev["summary"]) == r["text"] for r in rows
                   if r["type"] == "chat")


def test_search_from_events(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    store = _store(root)
    with _conn(root) as conn:
        ensure_index(conn)
        rebuild_session(conn, store, SID)
        conn.commit()
        hits = search(conn, build_fts_query("响应"), limit=10)
    refs = [h["event_ref"] for h in hits]
    assert f"{SID}:chat:m1" in refs
    assert f"{SID}:chat:m2" in refs
    # 类型过滤
    with _conn(root) as conn:
        hits = search(conn, build_fts_query("启动慢"), scope="audio", limit=10)
    assert all(h["type"] == "audio" for h in hits)


def test_rebuild_all(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    store = _store(root)
    with _conn(root) as conn:
        n = rebuild_all(conn, store)
    assert n == 5


def test_lag_sessions_watermark_forms(tmp_path):
    """lag 三形态（index_state 水位，retention 方案 §2.3）：仅全局 / 仅会话 /
    两者。COALESCE（自有行优先，非 MAX）：自有行旧于全局行时取自有行 → 多发
    一次重建，方向保守、不漏。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    with _conn(root) as conn:
        conn.execute("UPDATE events SET updated_at = '2026-01-01T00:00:00.000Z'")
        # 1) 空表 → 全部滞后（首轮全量重建）
        assert lag_sessions(conn) == [SID]
        # 2) 仅全局行 ≥ 事件水位 → 不滞后
        conn.execute("INSERT INTO index_state VALUES ('', '2026-02-01T00:00:00.000Z')")
        assert lag_sessions(conn) == []
        # 3) 仅会话行 ≥ 事件水位 → 不滞后
        conn.execute("DELETE FROM index_state")
        conn.execute("INSERT INTO index_state VALUES (?, '2026-02-01T00:00:00.000Z')",
                     (SID,))
        assert lag_sessions(conn) == []
        # 4) 两者都有：自有行更旧 → 取自有行 → 滞后（即使全局为 2099）
        conn.execute("INSERT INTO index_state VALUES ('', '2099-01-01T00:00:00.000Z')")
        conn.execute("UPDATE index_state SET last_reindex_at = "
                     "'2025-01-01T00:00:00.000Z' WHERE target = ?", (SID,))
        assert lag_sessions(conn) == [SID]
        # 5) 自有行 ≥ 事件水位 → 不滞后
        conn.execute("UPDATE index_state SET last_reindex_at = "
                     "'2026-02-01T00:00:00.000Z' WHERE target = ?", (SID,))
        assert lag_sessions(conn) == []


def test_rebuild_empty_events_returns_zero(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    store = _store(root)
    with _conn(root) as conn:
        ensure_index(conn)
        conn.execute("DELETE FROM events")
        conn.commit()
        n = rebuild_session(conn, store, SID)
        conn.commit()
    assert n == 0
