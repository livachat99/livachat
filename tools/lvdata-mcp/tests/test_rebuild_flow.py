"""WS4 全链重建验证：fixtures 数据根 → backfill → 全链 → 核数 → 幂等/重建幂等。

跨包端到端（计划 WS4.1 的自动化版）：
1. `recording.backfill`（服务端 CLI）扫数据根生成 recording_files 行；
2. lvdata watcher 单轮（poll + round 固定序列）消费：同步 → chatlog → 音视频
   （fake LLM）→ FTS → 向量 → 日摘要；
3. 核数：events 按类型、search_docs、vec_chunks、day_summary、files 状态；
4. 幂等：重跑无新增；**重建幂等**：清空 lvdata 两库重跑 → 计数一致。
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from pathlib import Path

from conftest import FakeEmbeddingProvider
from PIL import Image
from test_vision import FakeVision

from lvdata_mcp.analysis.audio import AudioAnalyzer
from lvdata_mcp.analysis.chatlog import ChatlogProcessor
from lvdata_mcp.analysis.vision import VisionAnalyzer
from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.ddl import ensure_schema
from lvdata_mcp.pipeline import Pipeline, PollBudget
from lvdata_mcp.recording_watch import RecordingWatch
from lvdata_mcp.store import DataStore
from lvdata_mcp.summary.day import DaySummary
from lvdata_mcp.vec import VecIndex
from lvdata_mcp.watcher import Watcher

DEV = "b53985c7-0000-4000-8000-000000000001"
RS = "019fabe0-3b0f-7947-8000-1e567def787d"
TS = 1784876900000


def _stem(ts_ms: int, tail: int) -> str:
    """uuid7 文件名 stem：前 48 bit = ts_ms（与 uuid7_to_ms 解析互为逆）。"""
    head = f"{ts_ms:012x}"
    return f"{head[:8]}-{head[8:]}-7000-8000-{tail:012d}"


def _iso(ts_ms: int) -> str:
    from datetime import UTC, datetime
    return (datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


AUDIO_NAME = _stem(TS + 4000, 1) + ".ogg"
FRAME_A = _stem(TS + 5000, 10)
FRAME_B = _stem(TS + 6000, 11)


class _FakeAudio:
    max_attempts = 3
    model = "fake-audio"

    async def chat(self, system, user_content, enable_thinking=None):
        return json.dumps({
            "language": "zh", "transcript": "打开应用非常慢", "summary": "用户反馈启动慢",
            "value": "high",
            "emotions": [], "objects": [],
            "people": {"count": 1, "male_count": None, "female_count": None,
                       "description": ""},
        }, ensure_ascii=False), {"input_tokens": 5, "output_tokens": 5}


class _FakeDay:
    max_attempts = 3
    model = "fake-day"

    async def chat(self, system, user_content, enable_thinking=None):
        return json.dumps({
            "summary": "一天总结", "events": ["一条事件"], "topics": [],
        }, ensure_ascii=False), {"input_tokens": 5, "output_tokens": 5}


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (48, 32), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _make_root(tmp_path: Path) -> Path:
    from lvdata_mcp.timeutil import ms_to_local_date

    root = tmp_path / "lvdata"
    sess = root / DEV / RS
    (sess / "audio").mkdir(parents=True)
    (sess / "video").mkdir()
    (sess / "chat").mkdir()
    day = ms_to_local_date(TS, "Asia/Shanghai")
    (sess / "session.json").write_text(json.dumps({
        "schema_version": 1, "recording_session_id": RS, "device_uuid": DEV,
        "session_date": day,
        "started_at": _iso(TS),
        "config": {"audio_codec": "opus", "audio_container": "ogg",
                   "audio_segment_s": 60, "video_format": "jpeg-sequence"},
    }, ensure_ascii=False))

    def line(role, text, ts, ref_id, **extra):
        d = {"ts": ts, "utc": _iso(ts), "role": role,
             "text": text, "ref_id": ref_id, "chat_session_id": "cs1"}
        d.update(extra)
        return json.dumps(d, ensure_ascii=False) + "\n"

    (sess / "chat" / "history.jsonl").write_text(
        line("user", "为什么响应这么慢", TS, "m1")
        + line("assistant", "任务完成", TS + 1000, "msg_agent_job_jk1",
               source="agent_job", agent_name="hermes", agent_id="小七",
               job_key="jk-1"), encoding="utf-8")
    (sess / "chat" / "transcribe.jsonl").write_text(
        line("user", "转写内容", TS + 2000, "t1", source="transcribe"),
        encoding="utf-8")
    (sess / "chat" / "translate.jsonl").write_text(
        line("user", "翻译内容", TS + 3000, "tr1", source="lecture"),
        encoding="utf-8")
    (sess / "audio" / AUDIO_NAME).write_bytes(b"OggS" + b"\x00" * 2000)
    (sess / "video" / f"{FRAME_A}.jpg").write_bytes(_jpeg())
    (sess / "video" / f"{FRAME_B}.jpg").write_bytes(_jpeg())
    return root


def _ensure_lvdata(root: Path) -> None:
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()
    conn.close()


def _cfg(root: Path) -> LvdataMcpConfig:
    return LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "Asia/Shanghai", "audio_sample_rate": 0},
        providers={"embedding": {"model": "fake", "dim": 128}})


def _pipeline(cfg: LvdataMcpConfig) -> tuple[Watcher, Pipeline]:
    store = DataStore(cfg)
    vec = VecIndex(cfg, store, FakeEmbeddingProvider())
    pipeline = Pipeline(
        cfg, store, RecordingWatch(cfg), vec,
        audio=AudioAnalyzer(cfg, client=_FakeAudio(), budget=PollBudget(0)),
        vision=VisionAnalyzer(cfg, client=FakeVision(), budget=PollBudget(0)),
        day_summary=DaySummary(cfg, client=_FakeDay()),
        chatlog=ChatlogProcessor(cfg))
    return Watcher(cfg, store, pipeline), pipeline


async def _drive(w: Watcher) -> None:
    worker = asyncio.create_task(w._work_loop())
    await w.poll_once()
    await w.queue.join()
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


def _drive_once(root: Path) -> None:
    """一轮 poll+round；每次新建 watcher（asyncio 原语绑定当前事件循环；
    全部状态在 DB，进程内对象可弃）。"""
    w, _ = _pipeline(_cfg(root))
    asyncio.run(_drive(w))


def _counts(root: Path) -> dict:
    inv = root / "lvdata_inventory.db"
    conn = sqlite3.connect(f"file:{inv}?mode=ro", uri=True)
    counts = {
        "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "docs": conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0],
        "day_ok": conn.execute(
            "SELECT COUNT(*) FROM day_summary WHERE status='ok'").fetchone()[0],
        "audio_ok": conn.execute(
            "SELECT COUNT(*) FROM files WHERE file_type='audio' "
            "AND analysis_status='ok'").fetchone()[0],
        "video_ok": conn.execute(
            "SELECT COUNT(*) FROM files WHERE file_type='video' "
            "AND analysis_status='ok'").fetchone()[0],
    }
    conn.close()
    # vec0 虚拟表读取需加载 sqlite_vec 扩展（普通连接会 no such module: vec0）
    import sqlite_vec
    rag = sqlite3.connect(f"file:{root / 'lvdata_index.db'}?mode=ro", uri=True)
    rag.enable_load_extension(True)
    sqlite_vec.load(rag)
    counts["chunks"] = rag.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
    rag.close()
    return counts


def test_full_rebuild_flow_and_idempotency(tmp_path):
    root = _make_root(tmp_path)
    # 1) 服务端 backfill：扫数据根生成 recording_files（cs 单链路事实源）
    from recording.backfill import main as backfill_main
    assert backfill_main(["--db", str(root / "chat_server.db"),
                          "--base-dir", str(root)]) == 0
    _ensure_lvdata(root)            # 新建 lvdata 库（空库重建的起点）

    _drive_once(root)               # 2) 全链：poll + round 固定序列

    with sqlite3.connect(f"file:{root / 'lvdata_inventory.db'}?mode=ro",
                         uri=True) as conn:
        by_type = dict(conn.execute(
            "SELECT type, COUNT(*) FROM events GROUP BY type").fetchall())
    # 3) 核数：chat=1（普通行）+ agent_job=1（history 行）+ transcribe/translate
    #    各 1 + audio=1 + window=1 + video=2（FakeVision 全判关键帧）
    assert by_type == {"chat": 1, "agent_job": 1, "transcribe": 1,
                       "translate": 1, "audio": 1, "window": 1, "video": 2}

    c1 = _counts(root)
    assert c1["events"] == 8 and c1["docs"] == 8 and c1["chunks"] == 8
    with sqlite3.connect(f"file:{root / 'lvdata_inventory.db'}?mode=ro",
                         uri=True) as conn:
        day_rows = conn.execute(
            "SELECT timezone, local_date, status, fail_count, error "
            "FROM day_summary").fetchall()
    assert c1["day_ok"] == 1, day_rows
    assert c1["audio_ok"] == 1 and c1["video_ok"] == 2

    # 4) 幂等：重跑一轮 → 无新事件/无重新生成
    _drive_once(root)
    assert _counts(root) == c1

    # 5) 重建幂等：清空 lvdata 两库（数据可弃）→ 重跑 → 计数一致
    for name in ("lvdata_inventory.db", "lvdata_inventory.db-wal",
                 "lvdata_inventory.db-shm", "lvdata_index.db",
                 "lvdata_index.db-wal", "lvdata_index.db-shm"):
        p = root / name
        if p.exists():
            p.unlink()
    _ensure_lvdata(root)

    _drive_once(root)
    c2 = _counts(root)
    assert c2 == c1, (c1, c2)
