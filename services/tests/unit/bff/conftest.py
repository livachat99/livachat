"""v2 bff 测试夹具：events + day_summary + files + recording_sessions（§9.1 数据面）。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DEVICE_A = "11111111-1111-4111-8111-111111111111"
SESS_A = "22222222-2222-4222-8222-222222222222"  # 5 类事件齐, UTC 08-17 晚 (Shanghai 08-18)
SESS_B = "33333333-3333-4333-8333-333333333333"  # chat+audio, UTC 08-18 (Shanghai 08-18) → 与 A 合流
SESS_C = "44444444-4444-4444-8444-444444444444"  # 仅 audio
SESS_D = "55555555-5555-4555-8555-555555555555"  # 无事件 → pending
SESS_E = "66666666-6666-4666-8666-666666666666"  # 无事件 → pending
SESS_F = "77777777-7777-4777-8777-777777777777"  # 与 C 同 ts 事件（精确匹配平局用），started 更早

AUDIO_SEG = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
FRAME_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


TS_A1 = _ms("2026-08-17T16:00:00Z")   # Shanghai 08-18 00:00
TS_A2 = _ms("2026-08-17T16:30:00Z")   # Shanghai 08-18 00:30
TS_B1 = _ms("2026-08-18T01:00:00Z")   # Shanghai 08-18 09:00
TS_C1 = _ms("2026-08-17T08:00:00Z")   # Shanghai 08-17 16:00


def _event(ts_ms: int, etype: str, device: str, session: str,
           summary: str, payload: dict, seq: int = 0) -> tuple:
    return (f"{session}:{etype}:{ts_ms}:{seq}", device, session, etype, ts_ms,
            None, "files", summary, json.dumps(payload, ensure_ascii=False))


def _build(root: Path) -> None:
    root.mkdir(parents=True)
    media = root / DEVICE_A / SESS_A
    (media / "audio").mkdir(parents=True)
    (media / "video").mkdir(parents=True)
    (media / "audio" / f"{AUDIO_SEG}.ogg").write_bytes(b"OggS fake audio")
    (media / "video" / f"{FRAME_ID}.jpg").write_bytes(b"fake jpeg")

    db = root / "lvdata_inventory.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE recording_sessions (
            rsession_id TEXT PRIMARY KEY, device_uuid TEXT NOT NULL,
            session_date TEXT NOT NULL, status TEXT NOT NULL,
            started_at TEXT, duration_ms INTEGER, root_path TEXT,
            analysis_status TEXT, cnt_chat_messages INTEGER,
            cnt_audio_segments INTEGER, cnt_video_frames INTEGER,
            finalized_at TEXT, last_active_at TEXT, timezone TEXT DEFAULT 'UTC'
        );
        CREATE TABLE files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rsession_id TEXT NOT NULL, device_uuid TEXT,
            file_type TEXT NOT NULL, file_ext TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE, file_size INTEGER NOT NULL,
            uuid_ts_ms INTEGER, uuid_ts_utc TEXT,
            analysis_status TEXT NOT NULL DEFAULT 'pending',
            fail_count INTEGER NOT NULL DEFAULT 0,
            scanned_at TEXT
        );
        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_ref TEXT NOT NULL UNIQUE,
            device_uuid TEXT NOT NULL,
            rsession_id TEXT NOT NULL,
            type TEXT NOT NULL,
            ts_ms INTEGER NOT NULL,
            ts_utc TEXT,
            source TEXT NOT NULL,
            summary TEXT,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        -- 与生产 schema 对齐（lvdata-mcp ddl.py）：bff 的翻页查询带窗时用
        -- `INDEXED BY idx_events_ts`（docs/get_timeline_optimize.md §8），索引缺失会 fail loud
        CREATE INDEX idx_events_ts ON events(ts_ms);
        CREATE INDEX idx_events_type ON events(type);
        CREATE INDEX idx_events_rsession_ts ON events(rsession_id, ts_ms);
        CREATE TABLE day_summary (
            timezone TEXT NOT NULL, local_date TEXT NOT NULL,
            utc_offset_min INTEGER NOT NULL, day_start_ms INTEGER NOT NULL,
            day_end_ms INTEGER NOT NULL,
            day_summary TEXT NOT NULL DEFAULT '{}',
            model TEXT, status TEXT NOT NULL DEFAULT 'pending',
            event_count INTEGER NOT NULL DEFAULT 0,
            fail_count INTEGER NOT NULL DEFAULT 0,
            error TEXT, generated_at TEXT,
            day_stats TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (timezone, local_date)
        );
        """)
        sessions = [
            (SESS_A, DEVICE_A, "2026-08-17", "finalized", "2026-08-17T15:50:00Z",
             7_200_000, "ra", "completed", 2, 1, 1,
             "2026-08-17T16:40:00Z", "2026-08-17T16:40:00Z", "Asia/Shanghai"),
            (SESS_B, DEVICE_A, "2026-08-18", "finalized", "2026-08-18T00:50:00Z",
             3_600_000, "rb", "completed", 1, 1, 0,
             "2026-08-18T01:30:00Z", "2026-08-18T01:30:00Z", "Asia/Shanghai"),
            (SESS_C, DEVICE_A, "2026-08-17", "finalized", "2026-08-17T07:50:00Z",
             3_600_000, "rc", "completed", 0, 1, 0,
             "2026-08-17T08:30:00Z", "2026-08-17T08:30:00Z", "Asia/Shanghai"),
            (SESS_D, DEVICE_A, "2026-08-19", "finalized", "2026-08-19T00:00:00Z",
             3_600_000, "rd", "pending", 0, 0, 0,
             None, None, "Asia/Shanghai"),
            (SESS_E, DEVICE_A, "2026-08-20", "finalized", "2026-08-20T00:00:00Z",
             3_600_000, "re", "pending", 0, 0, 0,
             None, None, "Asia/Shanghai"),
            (SESS_F, DEVICE_A, "2026-08-17", "finalized", "2026-08-17T07:40:00Z",
             3_600_000, "rf", "completed", 1, 0, 0,
             "2026-08-17T08:10:00Z", "2026-08-17T08:10:00Z", "Asia/Shanghai"),
        ]
        conn.executemany(
            "INSERT INTO recording_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            sessions)

        # events（§5.1 payload schema）
        events = [
            # SESS_A：5 类事件齐
            _event(TS_A1, "chat", DEVICE_A, SESS_A, "你好",
                   {"role": "user", "text": "你好", "chat_session_id": "c1"}, seq=0),
            _event(TS_A1, "chat", DEVICE_A, SESS_A, "Hello",
                   {"role": "assistant", "text": "Hello", "chat_session_id": "c1"}, seq=1),
            _event(TS_A1 + 1000, "audio", DEVICE_A, SESS_A,
                   "三亚天气如何 询问三亚天气",
                   {"segment_id": AUDIO_SEG, "transcript": "三亚天气如何",
                    "summary": "询问三亚天气", "duration_s": 60,
                    "emotions": [{"emotion": "neutral", "confidence": 0.9}],
                    "objects": [{"name": "杯子", "description": "薄荷绿杯子"}],
                    "people": [{"role": "user", "gender": "male"},
                               {"role": "other", "gender": "female"},
                               {"role": "other", "gender": "male"},
                               {"role": "other", "gender": None}]}),
            _event(TS_A1 + 2000, "video", DEVICE_A, SESS_A,
                   "浏览器页面 办公室",
                   {"frame_id": FRAME_ID, "window_id": FRAME_ID,
                    "caption": "浏览器页面", "place": "办公室",
                    "objects": [{"name": "马克杯", "description": "浅蓝色"}],
                    "ocr": [], "people": []}),
            _event(TS_A1 + 3000, "window", DEVICE_A, SESS_A, "窗口",
                   {"window_id": FRAME_ID, "window_caption": "窗口"}),
            _event(TS_A2, "agent_job", DEVICE_A, SESS_A, "完成了一次查询 done",
                   {"job_key": "job-1", "status": "done", "summary": "完成了一次查询",
                    "has_result": True}),
            # SESS_B：chat + audio
            _event(TS_B1, "chat", DEVICE_A, SESS_B, "股票怎么看",
                   {"role": "user", "text": "股票怎么看", "chat_session_id": "c2"}),
            _event(TS_B1 + 1000, "audio", DEVICE_A, SESS_B,
                   "聊聊股票 讨论股票",
                   {"segment_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    "transcript": "聊聊股票", "summary": "讨论股票", "duration_s": 60,
                    "emotions": [{"emotion": "sad", "confidence": 0.5}],
                    "people": [{"role": "user", "gender": "male"}]}),
            # SESS_C：仅 audio
            _event(TS_C1, "audio", DEVICE_A, SESS_C,
                   "早间播报 早间新闻",
                   {"segment_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    "transcript": "早间播报", "summary": "早间新闻", "duration_s": 60,
                    "emotions": [{"emotion": "happy", "confidence": 0.8}]}),
            # SESS_F：与 C 同 ts 的 chat（replay 精确匹配平局用例；文本不与现有查询词碰撞）
            _event(TS_C1, "chat", DEVICE_A, SESS_F,
                   "平局锚点测试",
                   {"role": "user", "text": "平局锚点测试", "chat_session_id": "c3"}),
        ]
        conn.executemany(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, ts_utc, source, summary, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            events)

        # search_docs（FTS 派生索引，bff /search 的检索源；docs/bff_search_fts.md §6）：
        # 规则与 lvdata-mcp/index.py rebuild_session 同构——text = summary 列 bigram 预切分、
        # 原文另存 orig_text；这里用 bff 自身实现，保证"索引/查询同函数"
        #（bff 副本与 lvdata-mcp 的漂移由 test_search_api 的契约测试守护）
        from bff.search.fts import cjk_bigram

        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_docs USING fts5("
            "text, orig_text UNINDEXED, event_ref UNINDEXED, session_id UNINDEXED, "
            "device_uuid UNINDEXED, type UNINDEXED, ts_ms UNINDEXED)")
        docs = []
        for ev in events:  # (event_ref, device_uuid, rsession_id, type, ts_ms, …, summary, …)
            raw = ev[7] or ""
            text = cjk_bigram(raw)
            if text:
                docs.append((text, raw, ev[0], ev[2], ev[1], ev[3], ev[4]))
        conn.executemany(
            "INSERT INTO search_docs (text, orig_text, event_ref, session_id, "
            "device_uuid, type, ts_ms) VALUES (?,?,?,?,?,?,?)",
            docs)

        # files（§9.1.1 数据面；overview 自 §7 起不再读它，保留以维持夹具真实形态）
        files = [
            (SESS_A, "audio", f"{root}/{DEVICE_A}/{SESS_A}/audio/{AUDIO_SEG}.ogg", TS_A1 + 1000, "ok"),
            (SESS_A, "video", f"{root}/{DEVICE_A}/{SESS_A}/video/{FRAME_ID}.jpg", TS_A1 + 2000, "ok"),
            (SESS_B, "audio", f"{root}/{DEVICE_A}/{SESS_B}/audio/x.ogg", TS_B1 + 1000, "ok"),
            (SESS_C, "audio", f"{root}/{DEVICE_A}/{SESS_C}/audio/y.ogg", TS_C1, "ok"),
            (SESS_D, "audio", f"{root}/{DEVICE_A}/{SESS_D}/audio/z.ogg",
             _ms("2026-08-19T00:30:00Z"), "pending"),
            (SESS_E, "audio", f"{root}/{DEVICE_A}/{SESS_E}/audio/w.ogg",
             _ms("2026-08-20T00:30:00Z"), "failed"),
        ]
        for sid, ftype, fpath, ts, status in files:
            conn.execute(
                "INSERT INTO files (rsession_id, file_type, file_ext, file_path, "
                "file_size, uuid_ts_ms, analysis_status, fail_count) "
                "VALUES (?, ?, 'ogg', ?, 100, ?, ?, 0)",
                (sid, ftype, fpath, ts, status))

        # day_summary（§5.4：日卡 summary + topics 数据源；§7：day_stats 预计算列）
        # —— 08-17 = §13/§14 新格式样例（summary 纯总述 + events 独立字段 + str
        # topics、无 highlights/mood）；08-18 = 字典样例（一个 legacy {topic, detail}
        # + 一个 §10 {topic, search_words}），两形态持续覆盖 topic_items 归一化。
        # 08-19 ~ 08-21 = 非 ok 行，覆盖 has_pending 四分支（§7.3 E1）与「无 summary
        # 行 → null/summary 透出门控」（§7.3 E11）。day_stats 为预计算列（lvdata-mcp
        # day_stats stage 产物），此处手工固化——counts 与 events 真实分布一致。
        day_stats_17 = {
            "schema_version": 1, "computed_at": "2026-08-20T00:00:00.000Z",
            "counts": {"chat": 1, "audio": 1}, "hot_slots": [], "images": []}
        day_stats_18 = {
            "schema_version": 1, "computed_at": "2026-08-20T00:00:00.000Z",
            "counts": {"chat": 3, "audio": 2, "video": 1, "window": 1,
                       "agent_job": 1},
            "hot_slots": [{"start_ms": TS_A1, "end_ms": TS_B1, "count": 4}],
            "images": [{"frame_id": FRAME_ID, "ts_ms": TS_A1 + 2000,
                        "event_ref": f"{SESS_A}:video:{FRAME_ID}",
                        "window_id": FRAME_ID, "device_uuid": DEVICE_A,
                        "rsession_id": SESS_A}]}
        # 无事件日（08-19~08-21 夹具无 events）→ counts 为空，与 timeline total 同口径
        day_stats_empty = {
            "schema_version": 1, "computed_at": "2026-08-20T00:00:00.000Z",
            "counts": {}, "hot_slots": [], "images": []}
        day_rows = [
            ("2026-08-17", "ok", 0, 3,
             {"schema_version": 1, "timezone": "Asia/Shanghai",
              "local_date": "2026-08-17",
              "summary": "用户听了早间新闻播报。",
              "moments": ["收听早间新闻播报"],  # §15：独立落 doc，当前无消费者
              "topics": ["早间新闻"],
              "stats": {"events": 3}}, day_stats_17),
            ("2026-08-18", "ok", 0, 6,
             {"schema_version": 1, "timezone": "Asia/Shanghai",
              "local_date": "2026-08-18",
              "summary": "用户询问了三亚天气与股票信息。",
              "highlights": [],
              "topics": [{"topic": "旅游与天气", "detail": "询问三亚天气"},
                         {"topic": "股票与投资", "search_words": "股票走势"}],
              "stats": {"events": 6}}, day_stats_18),
            # E1 分支：pending → has_pending=True
            ("2026-08-19", "pending", 0, 0, {}, day_stats_empty),
            # E1 分支：failed 且 fail_count ≥ MAX_FAILURES(3) → False（已放弃重试）
            ("2026-08-20", "failed", 5, 0, {}, day_stats_empty),
            # E1 分支：failed 且 fail_count < 3 → True；E11：failed 行不清旧 doc，
            # 但 summary/topics 必须被门控吞掉（不透出陈旧内容）
            ("2026-08-21", "failed", 1, 2,
             {"schema_version": 1, "local_date": "2026-08-21",
              "summary": "陈旧 summary（重生成前残留）",
              "topics": [{"topic": "陈旧主题", "search_words": "旧词"}]},
             day_stats_empty),
        ]
        for d, status, fail_count, count, doc, stats in day_rows:
            conn.execute(
                "INSERT INTO day_summary (timezone, local_date, utc_offset_min, "
                "day_start_ms, day_end_ms, day_summary, model, status, "
                "event_count, fail_count, generated_at, day_stats) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("Asia/Shanghai", d, 480, 0, 0,
                 json.dumps(doc, ensure_ascii=False), "m",
                 status, count, fail_count, "2026-08-20T00:00:00.000Z",
                 json.dumps(stats, ensure_ascii=False)))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def add_timezones_table(root: Path, tzs: list[str]) -> None:
    """创建 timezones 表并插入行（timezone-support.md §7.1 用例专用）。

    默认夹具无此表 = 「表缺失放行」语义；本 helper 供 G3 新用例显式建表，
    幂等（同一测试多次调用不冲突）。
    """
    db = root / "lvdata_inventory.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS timezones (
            timezone   TEXT PRIMARY KEY,
            enabled    INTEGER NOT NULL DEFAULT 1,
            priority   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        """)
        conn.executemany(
            "INSERT OR IGNORE INTO timezones (timezone) VALUES (?)",
            [(t,) for t in tzs])
        conn.commit()


@pytest.fixture
def lvdata_root(tmp_path: Path) -> Path:
    root = tmp_path / "lvdata"
    _build(root)
    return root


@pytest.fixture
def store(lvdata_root: Path):
    from bff.common.store import LvdataStore

    return LvdataStore(str(lvdata_root))


@pytest.fixture
def make_client(lvdata_root: Path):
    from fastapi import FastAPI

    from bff.common.store import LvdataStore
    from bff.router import register
    from gateway.auth import Authenticator
    from shared.config.models import AuthConfig

    def _factory(*, device_uuids: list[str] | None = None,
                 lvdata_api_url: str = "",
                 dev_token: str = "") -> TestClient:
        app = FastAPI()
        app.state.lvdata_store = LvdataStore(str(lvdata_root),
                                             device_uuids=device_uuids)
        config = _fake_config(lvdata_root)
        config.bff.lvdata_api_url = lvdata_api_url
        app.state.config = config
        register(app)
        # TestClient 不带 with → lifespan 不跑；手动镜像 application.py:41-42 的装配。
        # dev_token 空 = 存量用例不带 token 也放行（夹具语义：仅空 token 通过；
        # 非空 secret 时请求须带 wire_digest(secret)，docs/server_auth.md §3.2）。
        app.state.authenticator = Authenticator(AuthConfig(mode="dev", dev_token=dev_token))
        return TestClient(app)

    return _factory


@pytest.fixture
def client(make_client):
    return make_client()


def _fake_config(root: Path):
    class _Bff:
        def __init__(self):
            self.data_root = str(root)
            self.lvdb = None
            self.device_uuids: list[str] = []
            self.timezone = "Asia/Shanghai"
            # remove 执行面（docs/bff_service_remove.md §2.1）；测试经
            # make_client(lvdata_api_url=...) 覆写
            self.lvdata_api_url = ""
            self.lvdata_api_token = "test-token"
            self.lvdata_api_timeout_s = 30.0

    class _Recording:
        def __init__(self):
            self.base_dir = str(root)

    class _Cfg:
        bff = _Bff()
        recording = _Recording()

    return _Cfg()
