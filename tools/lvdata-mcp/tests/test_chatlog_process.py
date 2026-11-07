"""chatlog pass 单测（recording_data_process.md §2.3/§2.5）。

覆盖：增量续读 / 尾部半行 / 事件映射 golden / ref_id 清洗与兜底 / 内容比对幂等 /
force 重放 / fail_count 停止 / offset 越界重置 / 超长行跳过。
"""

from __future__ import annotations

import json
import sqlite3

from lvdata_mcp.analysis.chatlog import ChatlogProcessor
from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.ddl import ensure_schema

DEV = "b53985c7-0000-4000-8000-000000000001"
RS = "019fabe0-3b0f-7947-8000-1e567def787d"
TS = 1784876900000
T1 = "2026-09-15T10:00:00.000Z"

_TYPE_BY_NAME = {"history.jsonl": "chat_history",
                 "transcribe.jsonl": "chat_transcribe",
                 "translate.jsonl": "chat_translate"}


def _line(role: str, text: str, ts: int, ref_id: str, **extra) -> str:
    d = {"ts": ts, "utc": "2026-09-15T10:00:00.000Z", "role": role,
         "text": text, "ref_id": ref_id, "chat_session_id": "cs1"}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False) + "\n"


def _setup(tmp_path, contents: dict[str, str], **watcher_overrides):
    root = tmp_path / "root"
    sess = root / DEV / RS
    (sess / "chat").mkdir(parents=True)
    db = root / "lvdata_inventory.db"
    conn = sqlite3.connect(db)
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.execute(
        "INSERT INTO recording_sessions (rsession_id, device_uuid, session_date, "
        "status, root_path) VALUES (?,?,?,?,?)",
        (RS, DEV, "2026-09-15", "active", str(sess)))
    for name, content in contents.items():
        path = sess / "chat" / name
        path.write_text(content, encoding="utf-8")
        conn.execute(
            "INSERT INTO files (rsession_id, device_uuid, file_type, file_ext, "
            "file_path, file_size, scanned_at, analysis_status, src_updated_at, "
            "processed_offset) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (RS, DEV, _TYPE_BY_NAME[name], "jsonl", str(path),
             path.stat().st_size, T1, "na", T1, 0))
    conn.commit()
    conn.close()
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": "Asia/Shanghai",
                                   **watcher_overrides})
    return cfg, db


def _rows(db, sql, params=()):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def _bump(db, path, *, src: str = "2026-09-15T10:05:00.000Z"):
    """模拟 cs touch：更新 file_size 与 src_updated_at。"""
    conn = sqlite3.connect(db)
    conn.execute("UPDATE files SET file_size = ?, src_updated_at = ? "
                 "WHERE file_path = ?",
                 (path.stat().st_size, src, str(path)))
    conn.commit()
    conn.close()


def _events(db) -> dict[str, sqlite3.Row]:
    return {r["event_ref"]: r for r in _rows(
        db, "SELECT * FROM events ORDER BY event_id")}


# ---------------------------------------------------------------------------


def test_incremental_read_and_watermark(tmp_path):
    cfg, db = _setup(tmp_path, {"history.jsonl": _line("user", "你好", TS, "m1")})
    proc = ChatlogProcessor(cfg)
    assert proc.process()["events"] == 1
    assert list(_events(db)) == [f"{RS}:chat:m1"]

    path = tmp_path / "root" / DEV / RS / "chat" / "history.jsonl"
    path.write_text(path.read_text() + _line("assistant", "回复", TS + 1000, "m2"),
                    encoding="utf-8")
    _bump(db, path)
    assert proc.process()["events"] == 1  # 只处理增量行
    assert set(_events(db)) == {f"{RS}:chat:m1", f"{RS}:chat:m2"}
    # 水位已到 src → 无增量可处理
    assert proc.process()["processed"] == 0


def test_partial_tail_not_advanced_until_completed(tmp_path):
    good = _line("user", "完整行", TS, "m1")
    cfg, db = _setup(tmp_path, {"history.jsonl": good + '{"ts": 123, "role": "user"'})
    proc = ChatlogProcessor(cfg)
    res = proc.process()
    assert res["events"] == 1 and res["partial"] == 1
    offset = len(good.encode("utf-8"))
    row = _rows(db, "SELECT processed_offset, processed_updated_at FROM files")[0]
    assert row["processed_offset"] == offset          # 停在半行前
    assert row["processed_updated_at"] is None        # 未读完不推进水位

    # 补齐半行 → 下轮续读该行
    path = tmp_path / "root" / DEV / RS / "chat" / "history.jsonl"
    path.write_text(good + '{"ts": 124, "utc": "x", "role": "user", "text": "半行补齐",'
                          ' "ref_id": "m2", "chat_session_id": "cs1"}\n',
                    encoding="utf-8")
    _bump(db, path)
    assert proc.process()["events"] == 1
    assert f"{RS}:chat:m2" in _events(db)


def test_event_mapping_golden(tmp_path):
    cfg, db = _setup(tmp_path, {
        "history.jsonl": (
            _line("user", "你好", TS, "m1")
            + _line("assistant", "任务完成", TS + 1000, "msg_agent_job_jk1",
                    source="agent_job", agent_name="hermes", agent_id="小七",
                    job_key="jk-1")
        ),
        "transcribe.jsonl": _line("user", "转写内容", TS + 2000, "t1",
                                  source="transcribe"),
        "translate.jsonl": _line("user", "翻译内容", TS + 3000, "tr1",
                                 source="lecture"),
    })
    proc = ChatlogProcessor(cfg)
    assert proc.process()["events"] == 4

    ev = _events(db)
    chat = ev[f"{RS}:chat:m1"]
    assert chat["type"] == "chat" and chat["source"] == "chat_messages"
    assert json.loads(chat["payload"])["chat_session_id"] == "cs1"

    job = ev[f"{RS}:agent_job:jk-1"]           # event_ref 用 job_key
    assert job["type"] == "agent_job"
    pj = json.loads(job["payload"])
    assert pj["job_key"] == "jk-1"
    assert pj["agent_name"] == "hermes" and pj["agent_id"] == "小七"

    tr = ev[f"{RS}:transcribe:t1"]
    assert tr["type"] == "transcribe" and tr["source"] == "chat_transcribe"

    tl = ev[f"{RS}:translate:tr1"]
    assert tl["type"] == "translate" and tl["source"] == "chat_translate"
    assert json.loads(tl["payload"])["source"] == "lecture"   # 行级 source 原值


def test_ref_id_sanitize_and_empty_fallback(tmp_path):
    cfg, db = _setup(tmp_path, {"history.jsonl": (
        _line("user", "带冒号", TS, "a:b") + _line("user", "空 ref", TS + 1, "")
    )})
    assert ChatlogProcessor(cfg).process()["events"] == 2
    refs = set(_events(db))
    assert f"{RS}:chat:a_b" in refs                      # ':' → '_'
    assert f"{RS}:chat:chat_{TS + 1}_user" in refs       # 空 ref 确定性兜底


def test_content_compare_idempotent_on_replay(tmp_path):
    cfg, db = _setup(tmp_path, {"history.jsonl": _line("user", "你好", TS, "m1")})
    proc = ChatlogProcessor(cfg)
    proc.process()
    before = _events(db)[f"{RS}:chat:m1"]["updated_at"]
    proc.process(force=True)   # 重放（offset 归零重读）
    after = _events(db)[f"{RS}:chat:m1"]["updated_at"]
    assert after == before     # 内容未变 → 不刷水位（day_summary 纪律）


def test_fail_count_threshold_stops_and_missing_file_marks_failed(tmp_path):
    cfg, db = _setup(tmp_path, {"history.jsonl": _line("user", "x", TS, "m1")})
    (tmp_path / "root" / DEV / RS / "chat" / "history.jsonl").unlink()
    proc = ChatlogProcessor(cfg)
    assert proc.process()["failed"] == 1
    row = _rows(db, "SELECT analysis_status, fail_count FROM files")[0]
    assert row["analysis_status"] == "failed" and row["fail_count"] == 1
    # fail_count 达 3 → 不再取件
    conn = sqlite3.connect(db)
    conn.execute("UPDATE files SET fail_count = 3")
    conn.commit()
    conn.close()
    assert proc.process()["processed"] == 0


def test_offset_beyond_size_resets_to_zero(tmp_path):
    cfg, db = _setup(tmp_path, {"history.jsonl": _line("user", "你好", TS, "m1")})
    conn = sqlite3.connect(db)
    conn.execute("UPDATE files SET processed_offset = 999999, "
                 "processed_updated_at = NULL")
    conn.commit()
    conn.close()
    assert ChatlogProcessor(cfg).process()["events"] == 1  # 重置全量重读（幂等）


def test_line_longer_than_cap_is_read_fully(tmp_path):
    """cap 只限「正常批读」；单行超 cap 时继续读到行界（不误判坏行）。"""
    giant = json.dumps({"ts": TS, "role": "user", "text": "x" * 200,
                        "ref_id": "big", "chat_session_id": "cs1"})
    cfg, db = _setup(tmp_path, {"history.jsonl": giant + "\n" + _line(
        "user", "正常行", TS + 1, "m1")},
        chatlog_max_bytes_per_round=32)
    proc = ChatlogProcessor(cfg)
    for _ in range(3):
        proc.process()
    events = _events(db)
    assert f"{RS}:chat:big" in events       # 超出行长的行被完整读取
    assert f"{RS}:chat:m1" in events        # 后续行照常处理


def test_hard_limit_line_skipped_without_hanging(tmp_path, monkeypatch):
    """超过硬上限（_MAX_LINE_BYTES）的单行：跳过并续读，不卡死。"""
    from lvdata_mcp.analysis import chatlog as chatlog_mod

    monkeypatch.setattr(chatlog_mod, "_MAX_LINE_BYTES", 200)
    giant = "x" * 400   # 无换行超长坏行（超过硬上限；正常行 ~135B < 上限）
    cfg, db = _setup(tmp_path, {"history.jsonl": giant + "\n" + _line(
        "user", "正常行", TS + 1, "m1")},
        chatlog_max_bytes_per_round=32)
    proc = ChatlogProcessor(cfg)
    for _ in range(8):
        proc.process()
    assert f"{RS}:chat:m1" in _events(db)
