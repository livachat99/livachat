"""dev 重建 CLI（rebuild_dev）单测：dry-run 只读 / 备份清库 + backfill / 核数。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lvdata_mcp.ddl import ensure_schema
from lvdata_mcp.rebuild_dev import main, verify

DEV = "b53985c7-0000-4000-8000-000000000001"
RS = "019fabe0-3b0f-7947-8000-1e567def787d"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "lvdata"
    sess = root / DEV / RS
    (sess / "chat").mkdir(parents=True)
    (sess / "session.json").write_text(json.dumps({
        "recording_session_id": RS, "device_uuid": DEV,
        "session_date": "2026-09-15", "config": {},
    }, ensure_ascii=False))
    (sess / "chat" / "history.jsonl").write_text("")
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()
    conn.close()
    return root


def test_dry_run_is_read_only(tmp_path):
    root = _root(tmp_path)
    assert main(["--base-dir", str(root), "--dry-run"]) == 0
    assert (root / "lvdata_inventory.db").exists()          # 未被清空
    assert not list(root.glob("rebuild_backup_*"))          # 无备份产生
    assert not (root / "chat_server.db").exists()           # 不写 cs 库


def test_wipe_backup_and_backfill(tmp_path):
    root = _root(tmp_path)
    assert main(["--base-dir", str(root), "--yes", "--force"]) == 0

    # 清库 + 备份可回滚
    assert not (root / "lvdata_inventory.db").exists()
    backups = list(root.glob("rebuild_backup_*"))
    assert len(backups) == 1
    assert (backups[0] / "lvdata_inventory.db").exists()

    # backfill 完成 cs 事实源登记（session.json + history.jsonl）
    with sqlite3.connect(f"file:{root / 'chat_server.db'}?mode=ro",
                         uri=True) as conn:
        rows = conn.execute(
            "SELECT kind FROM recording_files ORDER BY kind").fetchall()
    assert [r[0] for r in rows] == ["chat_history", "session"]

    # verify：库已清空（未重建）→ 仅报告状态
    counts = verify(root)
    assert counts["inventory_exists"] is False


def test_no_yes_refuses(tmp_path):
    root = _root(tmp_path)
    assert main(["--base-dir", str(root), "--force"]) == 1  # 缺 --yes 拒绝
    assert (root / "lvdata_inventory.db").exists()          # 未被清空
