"""测试夹具：构建一份小样本 lvdata 数据树（v2 schema：events + recording_sessions）。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
import uvicorn


class FakeEmbeddingProvider:
    """确定性测试 embedding：字符 one-hot 求和（共享字符 → 高余弦），离线可复现。"""

    model = "fake"
    dim = 128

    def __init__(self, dim: int = 128):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        import hashlib
        import math

        vec = [0.0] * self.dim
        for ch in text:
            h = int(hashlib.md5(ch.encode()).hexdigest()[:8], 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class CountingEmbeddingProvider(FakeEmbeddingProvider):
    """Fake + 调用计数/输入记录（§13 查询缓存用例：断言"只嵌一次"）。"""

    def __init__(self, dim: int = 128):
        super().__init__(dim)
        self.calls = 0
        self.seen: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.seen += list(texts)
        return await super().embed(texts)


DEVICE = "b53985c7-0000-4000-8000-000000000001"
SID = "019fabe0-3b0f-7947-8000-1e567def787d"
FRAME = "019fabe5-b00e-7b8d-8000-8fc29ccdab9a"

TS = 1784876900000  # base ts_ms（对齐 k3 文档示例）


def make_sample(root: Path, analysis_status: str = "completed") -> Path:
    """构建 /opt/lvdata 等价数据树（v2 schema），返回 db 路径。"""
    from lvdata_mcp.ddl import ensure_schema

    db = root / "lvdata_inventory.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn, default_timezone="Asia/Shanghai", timezones=[])
    conn.execute("PRAGMA journal_mode = WAL")
    # v1 分析表（v1 watcher pending 判定 / 旧读路径仍用，P1/P4 重写后移除）
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audio_analyses (analysis_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "file_id INTEGER NOT NULL, session_id TEXT NOT NULL, segment_id TEXT NOT NULL, "
        "derived_path TEXT NOT NULL, model TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', transcript TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS video_analyses (analysis_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "file_id INTEGER NOT NULL, session_id TEXT NOT NULL, frame_id TEXT NOT NULL, "
        "window_id TEXT NOT NULL, model TEXT NOT NULL, "
        "filter_status TEXT NOT NULL DEFAULT 'pending')")
    conn.execute(
        "INSERT INTO recording_sessions (rsession_id, device_uuid, session_date, status, "
        "started_at, last_active_at, finalized_at, duration_ms, analysis_status, "
        "root_path, timezone) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (SID, DEVICE, "2026-07-29", "finalized",
         "2026-07-29T11:00:00Z", "2026-07-29T11:23:00Z", "2026-07-29T11:23:00Z",
         1380000, analysis_status, str(root), "Asia/Shanghai"))
    conn.executemany(
        "INSERT INTO files (rsession_id, file_type, file_ext, file_path, file_size, "
        "uuid_ts_ms, uuid_ts_utc, analysis_status, device_uuid) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (SID, "chat", "jsonl", f"{root}/{DEVICE}/{SID}/chat/m1.jsonl", 100, TS, "x", "na", DEVICE),
            (SID, "chat", "jsonl", f"{root}/{DEVICE}/{SID}/chat/m2.jsonl", 100, TS + 30000, "x", "na", DEVICE),
            (SID, "audio", "ogg", f"{root}/{DEVICE}/{SID}/audio/seg1.ogg", 2000, TS + 5000, "x", "pending", DEVICE),
            (SID, "video", "jpg", f"{root}/{DEVICE}/{SID}/video/{FRAME}.jpg", 300, TS + 10500, "x", "pending", DEVICE),
        ])
    # events：chat 事件 + 分析事件（SSOT）
    conn.executemany(
        "INSERT INTO events (event_ref, device_uuid, rsession_id, type, ts_ms, "
        "ts_utc, source, summary, payload) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (f"{SID}:chat:m1", DEVICE, SID, "chat", TS, "2026-07-29T11:00:00Z",
             "chat_messages", "为什么响应这么慢",
             json.dumps({"role": "user", "text": "为什么响应这么慢", "chat_session_id": "cs1"})),
            (f"{SID}:chat:m2", DEVICE, SID, "chat", TS + 30000, "2026-07-29T11:00:30Z",
             "chat_messages", "我在排查响应慢的问题",
             json.dumps({"role": "assistant", "text": "我在排查响应慢的问题", "chat_session_id": "cs1"})),
            (f"{SID}:audio:seg1", DEVICE, SID, "audio", TS + 5000, "2026-07-29T11:00:05Z",
             "files", "打开应用非常慢 用户反馈启动慢 frustrated",
             json.dumps({"segment_id": "seg1", "transcript": "打开应用非常慢",
                         "summary": "用户反馈启动慢", "duration_s": 60.0,
                         "emotions": ["frustrated"]})),
            (f"{SID}:window:w1", DEVICE, SID, "window", TS + 10000, "2026-07-29T11:00:10Z",
             "files", "browser - 编写代码",
             json.dumps({"window_id": "w1", "window_start_ms": TS + 10000,
                         "window_caption": "browser - 编写代码", "frame_count": 2,
                         "key_frame_count": 1})),
            (f"{SID}:video:{FRAME}", DEVICE, SID, "video", TS + 10500, "2026-07-29T11:00:10Z",
             "files", "IDE 报错页面 办公室 Traceback",
             json.dumps({"frame_id": FRAME, "window_id": "w1",
                         "caption": "IDE 报错页面", "place": "办公室",
                         "objects": [], "ocr": [{"text": "Traceback"}],
                         "people": []})),
        ])
    conn.commit()
    conn.close()

    sess_dir = root / DEVICE / SID
    (sess_dir / "chat").mkdir(parents=True)
    (sess_dir / "audio").mkdir()
    (sess_dir / "video").mkdir()
    (sess_dir / "session.json").write_text(json.dumps(
        {"device_uuid": DEVICE, "recording_session_id": SID, "session_id": SID,
         "config": {}, "stats": {},
         "started_at": "2026-07-29T11:00:00.000Z"}))
    (sess_dir / "chat" / "history.jsonl").write_text(
        json.dumps({"ts": TS, "role": "user", "text": "为什么响应这么慢"}) + "\n" +
        json.dumps({"ts": TS + 30000, "role": "assistant", "text": "我在排查响应慢的问题"}) + "\n")
    (sess_dir / "audio" / "seg1.ogg").write_bytes(b"fake ogg")
    (sess_dir / "video" / f"{FRAME}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

    derived = root / "derived" / DEVICE / SID
    (derived / "audio").mkdir(parents=True)
    (derived / "video" / "windows").mkdir(parents=True)
    (derived / "video" / "frames").mkdir(parents=True)
    (derived / "audio" / "seg1.json").write_text(json.dumps({
        "ts_ms": TS + 5000, "segment_id": "seg1", "transcript": "打开应用非常慢",
        "summary": "用户反馈启动慢", "duration_s": 60.0,
        "emotions": ["frustrated"]}))
    (derived / "video" / "windows" / "w1.json").write_text(json.dumps({
        "window_id": "w1", "window_start_ms": TS + 10000,
        "window_caption": "browser - 编写代码", "frame_count": 2,
        "frames": [{"frame_id": "0", "ts_ms": TS + 10500, "scene_id": 1,
                    "key_frame": True, "reason": "start"}],
    }))
    (derived / "video" / "frames" / f"{FRAME}.json").write_text(json.dumps({
        "ts_ms": TS + 10500, "caption": "IDE 报错页面", "place": "办公室",
        "objects": [], "ocr": [{"text": "Traceback"}], "people": [],
        "window_id": "w1"}))
    (derived / "session_summary.json").write_text(json.dumps({
        "schema_version": 1, "session_id": SID, "session_date": "2026-07-29",
        "session_summary": "用户反馈应用响应慢，已排查并给出建议",
        "topics": ["性能问题"], "notable_events": ["响应慢抱怨"]}))
    (derived / "timeline.json").write_text(json.dumps({
        "schema_version": 1, "session_id": SID, "device_uuid": DEVICE,
        "session_date": "2026-07-29", "built_at": "2026-07-29T12:00:00Z",
        "builder": "test fixture", "timezone": "Asia/Shanghai",
        "started_at": "2026-07-29T11:00:00Z",
        "ended_at": "2026-07-29T11:23:00Z",
        "time_span_ms": 1380000,
        "sources": {"chat_messages": 2, "audio_segments": 1},
        "events": []}))
    if analysis_status == "completed":
        conn = sqlite3.connect(db)
        f = conn.execute(
            "SELECT file_id FROM files WHERE rsession_id = ? AND file_type = 'audio'",
            (SID,)).fetchone()
        fv = conn.execute(
            "SELECT file_id FROM files WHERE rsession_id = ? AND file_type = 'video'",
            (SID,)).fetchone()
        if f:
            conn.execute(
                "INSERT INTO audio_analyses (file_id, session_id, segment_id, "
                "derived_path, model, status, transcript) VALUES (?,?,?,?,?, 'ok', ?)",
                (f[0], SID, "seg1", str(derived / "audio" / "seg1.json"), "m",
                 "打开应用非常慢"))
        if fv:
            conn.execute(
                "INSERT INTO video_analyses (file_id, session_id, frame_id, "
                "window_id, model, filter_status) VALUES (?,?,?,?,?, 'ok')",
                (fv[0], SID, FRAME, "w1", "m"))
        conn.commit()
        conn.close()
    return db


@pytest.fixture
def sample_root(tmp_path: Path) -> Path:
    make_sample(tmp_path)
    return tmp_path


@pytest.fixture
def db(sample_root: Path) -> Path:
    return sample_root / "lvdata_inventory.db"


def start_server(app, port: int = 0) -> tuple[uvicorn.Server, threading.Thread, int]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    actual = server.servers[0].sockets[0].getsockname()[1]
    return server, t, actual


@pytest.fixture(scope="module")
def env():
    """模块级：一份共享样本数据 + 常驻 server，返回 (base_url, root)。MCP 端点在 {base}/mcp。"""
    import tempfile

    from lvdata_mcp.config import LvdataMcpConfig
    from lvdata_mcp.server import create_app

    root = Path(tempfile.mkdtemp(prefix="lvdata-mcp-test-"))
    make_sample(root)
    settings = LvdataMcpConfig(
        data={"root_dir": str(root)},
        server={"host": "127.0.0.1", "port": 0},
        watcher={"timezone": "Asia/Shanghai"})
    server, thread, port = start_server(create_app(settings))
    yield f"http://127.0.0.1:{port}", root
    server.should_exit = True
    thread.join(timeout=10)
