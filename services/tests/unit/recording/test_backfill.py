"""Unit tests: recording.backfill — 存量文件扫描与离线回填（doc §2.1/§2.6）。"""

from __future__ import annotations

import json
import sqlite3

from recording.backfill import main, run_backfill, scan_data_root
from shared.utils.ids import uuid7


def _mk_tree(base, device="dev-1", sess="rs-1"):
    """构造最小数据根：session.json + audio/video/chat + 干扰文件。"""
    root = base / device / sess
    (root / "audio").mkdir(parents=True)
    (root / "video").mkdir()
    (root / "chat").mkdir()
    (root / "image").mkdir()
    (root / "session.json").write_text(json.dumps({"schema_version": 1}))
    audio_name = f"{uuid7()}.ogg"
    (root / "audio" / audio_name).write_bytes(b"OggS" + b"\x00" * 100)
    (root / "audio" / f"{uuid7()}.ogg.part").write_bytes(b"partial")  # 中间态跳过
    (root / "video" / f"{uuid7()}.jpg").write_bytes(b"\xff\xd8jpeg")
    (root / "chat" / "history.jsonl").write_text('{"ts":1}\n')
    (root / "chat" / "transcribe.jsonl").write_text('{"ts":2}\n')
    (root / "chat" / "translate.jsonl").write_text('{"ts":3}\n')
    (root / "image" / f"{uuid7()}.jpg").write_bytes(b"\xff\xd8img")  # 不登记
    (root / "stray.bin").write_bytes(b"x")  # 不登记
    # lvdata 派生产物命名空间：不登记
    derived = base / "derived" / device / sess
    (derived / "audio").mkdir(parents=True)
    (derived / "audio" / "seg.json").write_text("{}")
    return root, audio_name


def _rows(db: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM recording_files ORDER BY kind, rel_path").fetchall()
    conn.close()
    return rows


def test_scan_data_root_kinds_and_skips(tmp_path):
    root, audio_name = _mk_tree(tmp_path)
    rows = scan_data_root(tmp_path)
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["audio", "chat_history", "chat_transcribe",
                     "chat_translate", "session", "video"]
    audio = next(r for r in rows if r.kind == "audio")
    assert audio.rel_path == f"dev-1/rs-1/audio/{audio_name}"
    assert audio.ts_ms is not None  # uuid7 解析
    assert audio.device_uuid == "dev-1" and audio.recording_session_id == "rs-1"
    session = next(r for r in rows if r.kind == "session")
    assert session.rel_path == "dev-1/rs-1/session.json"


def test_backfill_inserts_and_is_idempotent(tmp_path):
    db = str(tmp_path / "chat_server.db")
    _mk_tree(tmp_path)
    rows = scan_data_root(tmp_path)

    inserted_first = run_backfill(db, rows)
    assert inserted_first == len(rows) == 6

    inserted_second = run_backfill(db, scan_data_root(tmp_path))
    assert inserted_second == 0  # INSERT OR IGNORE：重复运行不新增
    assert len(_rows(db)) == 6


def test_backfill_creates_schema_when_db_missing(tmp_path):
    db = str(tmp_path / "chat_server.db")
    _mk_tree(tmp_path)
    run_backfill(db, scan_data_root(tmp_path))
    # 建表幂等：表存在且行数正确
    assert len(_rows(db)) == 6


def test_main_dry_run_does_not_touch_db(tmp_path, capsys):
    db = tmp_path / "chat_server.db"
    _mk_tree(tmp_path)
    rc = main(["--db", str(db), "--base-dir", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert not db.exists()  # dry-run 不建库
    out = capsys.readouterr().out
    assert "dry-run" in out and "audio=1" in out


def test_main_backfill_and_rerun(tmp_path, capsys):
    db = tmp_path / "chat_server.db"
    _mk_tree(tmp_path)
    rc = main(["--db", str(db), "--base-dir", str(tmp_path)])
    assert rc == 0
    assert "inserted=6" in capsys.readouterr().out
    rc = main(["--db", str(db), "--base-dir", str(tmp_path)])
    assert rc == 0
    assert "inserted=0" in capsys.readouterr().out
    assert len(_rows(db)) == 6
