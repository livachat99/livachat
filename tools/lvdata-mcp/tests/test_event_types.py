"""事件类型常量与排序（recording_data_process.md §2.5）：transcribe/translate 扩展。

覆盖：优先级表形状（有序、新增类型位置）、排序 SQL 单一出处（PRIORITY_SQL 由
EVENT_PRIORITY 生成、无手写副本）、同 ts 全类型 timeline 排序。
"""

from __future__ import annotations

import sqlite3

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.consts import EVENT_PRIORITY, EVENT_TYPES, PRIORITY_SQL
from lvdata_mcp.ddl import ensure_schema
from lvdata_mcp.store import DataStore

DEV = "b53985c7-0000-4000-8000-000000000001"
RS = "019fabe0-3b0f-7947-8000-1e567def787d"
TS = 1784876900000


def test_priority_table_shape_and_single_source():
    assert EVENT_TYPES == ("chat", "transcribe", "translate", "window",
                           "video", "audio", "agent_job")
    values = [EVENT_PRIORITY[t] for t in EVENT_TYPES]
    assert values == sorted(values)  # 有序且互异（游标依赖）
    assert len(set(values)) == len(values)
    # 文本类靠前：transcribe/translate 在 window 之前
    assert EVENT_PRIORITY["transcribe"] < EVENT_PRIORITY["window"]
    # 排序 SQL 单一出处：每个类型都必须出现在生成的 CASE 里（防手写副本漏改）
    for t in EVENT_TYPES:
        assert f"WHEN '{t}'" in PRIORITY_SQL
    assert "ELSE" in PRIORITY_SQL


def test_timeline_orders_all_types_by_priority(tmp_path):
    db = tmp_path / "lvdata_inventory.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.execute(
        "INSERT INTO recording_sessions (rsession_id, device_uuid, session_date, "
        "status, root_path) VALUES (?,?,?,?,?)",
        (RS, DEV, "2026-09-15", "finalized", str(tmp_path)))
    # 同一 ts、乱序插入 → 输出必须是 EVENT_PRIORITY 顺序
    for t in ("agent_job", "audio", "video", "window", "translate",
              "transcribe", "chat"):
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary) VALUES (?,?,?,?,?,?,?)",
            (f"{RS}:{t}:x", DEV, RS, t, TS, "test", f"summary-{t}"))
    conn.commit()
    conn.close()

    store = DataStore(LvdataMcpConfig(data={"root_dir": str(tmp_path)}))
    out = store.get_timeline(limit=20)
    assert [e["type"] for d in out["days"] for e in d["events"]] == list(EVENT_TYPES)
