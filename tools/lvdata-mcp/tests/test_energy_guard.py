"""能量包络的 Agent 出口护栏（docs/android_timeline_audio_gallery.md §10 E3）。

能量随 audio 事件 payload 下发（给 App 画包络，BFF 保留）；MCP 面向 LLM Agent，
**唯一还透出 payload 的出口是 `get_event` 的 `detail`**，必须剔除（`strip_energy`）。
列表/邻域出口自 §9/§12 起一律轻量投影（不含 payload），能量字段自然不会出现——
若将来新增透出 payload 的出口，护栏点与本文档需同步。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from conftest import SID, TS, make_sample

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.store import DataStore, strip_energy

REF = f"{SID}:audio:seg1"
ENERGY = {"energy_v": 1, "frame_ms": 20, "db_min": -70.0, "db_max": 0.0,
          "energy": "AAAA"}


def _store_with_energy(tmp_path: Path) -> DataStore:
    """样例树 + 给 audio 事件补上能量字段（模拟 E1 产物）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    db = make_sample(root)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT payload FROM events WHERE event_ref = ?",
                       (REF,)).fetchone()
    payload = json.loads(row["payload"])
    payload.update(ENERGY)
    conn.execute("UPDATE events SET payload = ? WHERE event_ref = ?",
                 (json.dumps(payload, ensure_ascii=False), REF))
    conn.commit()
    conn.close()
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": "Asia/Shanghai"})
    return DataStore(cfg)


def test_db_row_keeps_energy(tmp_path):
    """数据源本身保留（BFF 侧读同一列 → App 仍能拿到）。"""
    _store_with_energy(tmp_path)
    conn = sqlite3.connect(tmp_path / "lvdata" / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    payload = json.loads(
        conn.execute("SELECT payload FROM events WHERE event_ref = ?",
                     (REF,)).fetchone()["payload"])
    assert payload["energy"] == "AAAA" and payload["energy_v"] == 1


def test_get_event_strips_energy(tmp_path):
    """get_event 的 detail 不含能量，其余字段不受影响。"""
    store = _store_with_energy(tmp_path)
    out, _img = store.get_event(REF)     # 返回 (详情, 关键帧字节|None)
    detail = out["detail"]
    assert "energy" not in detail
    assert "energy_v" not in detail and "db_min" not in detail
    assert detail["transcript"] == "打开应用非常慢"
    assert detail["segment_id"] == "seg1"


def test_get_timeline_strips_energy(tmp_path):
    """列表/邻域为轻量投影（无 payload）→ 能量字段自然不出现（§12 后无第二个出口）。"""
    from lvdata_mcp.vec import expand_context

    store = _store_with_energy(tmp_path)
    page = store.get_timeline(types=["audio"], limit=50)
    ev = next(e for d in page["days"] for e in d["events"] if e["event_ref"] == REF)
    assert set(ev) == {"event_ref", "type", "timestamp", "source", "summary"}
    # semantic_search 的邻域（with_context）同为轻量投影
    ctx = expand_context(store, {"session_id": SID, "ts_ms": TS},
                         "Asia/Shanghai", window_ms=60000)
    hit = next(e for e in ctx if e["event_ref"] == REF)
    assert set(hit) == {"event_ref", "type", "timestamp", "source", "summary"}
    assert hit["summary"].startswith("打开应用非常慢")   # summary ≠ payload.transcript


def test_get_timeline_other_types_untouched(tmp_path):
    """轻量投影对非 audio 类型同样只给摘要字段（无 payload），条数不变。"""
    store = _store_with_energy(tmp_path)
    page = store.get_timeline(types=["chat"], limit=50)
    evs = [e for d in page["days"] for e in d["events"]]
    assert len(evs) == 2
    assert all(e["type"] == "chat" and e["summary"] for e in evs)


def test_strip_energy_helper_idempotent():
    d = {"energy": "x", "frame_ms": 20, "db_min": -70.0, "text": "keep"}
    assert strip_energy(d) == {"text": "keep"}
    assert strip_energy({"text": "keep"}) == {"text": "keep"}
