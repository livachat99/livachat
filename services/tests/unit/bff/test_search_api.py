"""bff /search 测试（FTS 检索，docs/bff_search_fts.md）。

口径：**可搜 ⊆ 可展示**——只含 chat / transcribe / translate / agent_job / video，
topic（day_summary）/ audio transcript / window 一律不出现在结果；排序 = bm25 相关度序。
"""

from __future__ import annotations

import json as _json
import sqlite3

import pytest

from tests.unit.bff.conftest import DEVICE_A, SESS_A, TS_A1, TS_A2, TS_B1, TS_C1


def _hits(client, q: str, **params):
    resp = client.get("/api/mylife/search", params={"q": q, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _insert_event(lvdata_root, *, ts_ms: int, etype: str, summary: str = "",
                  payload: dict | None = None, seq: int = 0) -> None:
    """测试内自造事件（不改夹具种子）并**同步派生索引 search_docs**——
    生产由 lvdata-mcp reindex 建索引，测试里手工保持同构（text = summary 列 bigram）。"""
    from bff.search.fts import cjk_bigram

    event_ref = f"{SESS_A}:{etype}:{ts_ms}:{seq}"
    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute(
            "INSERT INTO events (event_ref, device_uuid, rsession_id, type, "
            "ts_ms, source, summary, payload) VALUES (?,?,?,?,?,?,?,?)",
            (event_ref, DEVICE_A, SESS_A, etype, ts_ms, "files", summary,
             _json.dumps(payload or {})))
        text = cjk_bigram(summary or "")
        if text:
            conn.execute(
                "INSERT INTO search_docs (text, orig_text, event_ref, session_id, "
                "device_uuid, type, ts_ms) VALUES (?,?,?,?,?,?,?)",
                (text, summary, event_ref, SESS_A, DEVICE_A, etype, ts_ms))


# ---- 语料口径：五类过滤（audio/window/topic 排除）----


def test_search_chat_hits(client):
    """q=股票 → 仅 SESS_B 的 chat 命中（原 transcript/topic 命中已按口径排除）。"""
    data = _hits(client, "股票")
    assert [h["kind"] for h in data["hits"]] == ["chat"]
    assert data["hits"][0]["ts_ms"] == TS_B1
    assert data["hits"][0]["date"] == "2026-08-18"
    assert "股票" in data["hits"][0]["snippet"]
    assert data["total"] == 1


def test_search_video_hit_kind_video(client):
    """video 命中 kind=video；索引文本 = caption + place（q=办公室 亦命中）。"""
    v = _hits(client, "浏览器")
    assert v["hits"][0]["kind"] == "video"
    assert v["hits"][0]["ts_ms"] == TS_A1 + 2000

    place = _hits(client, "办公室")
    assert [h["kind"] for h in place["hits"]] == ["video"]


def test_search_audio_and_window_and_topic_excluded(client):
    """audio（transcript/summary）/ window caption / day_summary topic 均不出现在结果。"""
    assert _hits(client, "三亚天气")["total"] == 0   # audio transcript
    assert _hits(client, "早间")["total"] == 0        # audio summary
    assert _hits(client, "窗口")["total"] == 0        # window caption
    assert _hits(client, "旅游")["total"] == 0        # day_summary topic


def test_search_agent_job_hit(client):
    """agent_job 命中（索引文本 = summary 列 = 任务文本）；agent_name 不在索引内。"""
    data = _hits(client, "查询")
    assert [h["kind"] for h in data["hits"]] == ["agent_job"]
    assert data["hits"][0]["ts_ms"] == TS_A2


# ---- 排序 / 分页 ----


def test_search_case_insensitive(client):
    data = _hits(client, "hello")
    assert len(data["hits"]) == 1
    assert data["hits"][0]["kind"] == "chat"
    assert data["hits"][0]["ts_ms"] == TS_A1


def test_search_relevance_beats_recency(lvdata_root, client):
    """bm25 相关度优先于时间：短文档（早）应排在长文档（新）之前。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 - 10_000, etype="chat", summary="葡萄")
    _insert_event(lvdata_root, ts_ms=TS_A1 + 30_000, etype="chat",
                  summary="葡萄 苹果 香蕉 橘子 西瓜 桃子 梨 枣")
    hits = _hits(client, "葡萄")["hits"]
    assert len(hits) == 2
    assert hits[0]["snippet"] == "葡萄"          # dl 小 → bm25 更高
    assert hits[1]["ts_ms"] > hits[0]["ts_ms"]   # 新的排在后面（时间不主导）


def test_search_tie_then_ts_desc(lvdata_root, client):
    """相关度相同（同词同长）时按 ts 倒序——新 kind 与既有 kind 混合亦稳定。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 40_000, etype="transcribe",
                  summary="幻灯片")
    _insert_event(lvdata_root, ts_ms=TS_A1 + 50_000, etype="agent_job",
                  summary="幻灯片")
    hits = _hits(client, "幻灯片")["hits"]
    assert [h["kind"] for h in hits] == ["agent_job", "transcribe"]
    assert [h["ts_ms"] for h in hits] == [TS_A1 + 50_000, TS_A1 + 40_000]


def test_search_multi_term_implicit_and(lvdata_root, client):
    """多词查询 = 隐式 AND（词可不相邻）；只含其一的不命中。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 60_000, etype="chat",
                  summary="今天买了苹果和香蕉")
    _insert_event(lvdata_root, ts_ms=TS_A1 + 70_000, etype="chat",
                  summary="只买了苹果")
    data = _hits(client, "苹果 香蕉")
    assert data["total"] == 1
    assert data["hits"][0]["ts_ms"] == TS_A1 + 60_000
    assert "苹果" in data["hits"][0]["snippet"]   # 多词 snippet 按首个命中词定位


def test_search_ascii_whole_word(lvdata_root, client):
    """ASCII 词整词匹配（不再子串）：hell 不命中 Hello。"""
    assert _hits(client, "hell")["total"] == 0
    assert _hits(client, "Hello")["total"] == 1


def test_search_transcribe_translate_hits(lvdata_root, client):
    """transcribe/translate 命中（索引文本 = summary 列 = 正文；大小写不敏感）。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 5000, etype="transcribe",
                  summary="课堂讲了 Nyquist 采样定理")
    _insert_event(lvdata_root, ts_ms=TS_A1 + 6000, etype="translate",
                  summary="Nyquist 采样定理的英文原文")
    data = _hits(client, "nyquist")
    assert {h["kind"] for h in data["hits"]} == {"transcribe", "translate"}
    assert {h["ts_ms"] for h in data["hits"]} == {TS_A1 + 5000, TS_A1 + 6000}
    assert all("Nyquist" in h["snippet"] for h in data["hits"])


def test_search_timezone_affects_date(client):
    """事件 hit 的 date 为请求时区本地日：A 事件 UTC 归 08-17、Shanghai 归 08-18。"""
    utc = _hits(client, "hello", timezone="UTC")
    assert utc["hits"][0]["date"] == "2026-08-17"

    sh = _hits(client, "hello", timezone="Asia/Shanghai")
    assert sh["hits"][0]["date"] == "2026-08-18"


def test_search_pagination(lvdata_root, client):
    """limit 分页 + 不透明 cursor；跨页不重叠、total 恒定。"""
    for i in range(3):
        _insert_event(lvdata_root, ts_ms=TS_A1 + 12_000 + i, etype="transcribe",
                      summary=f"量子计算第{i}讲", seq=i)

    page1 = _hits(client, "量子计算", limit=2)
    assert page1["total"] == 3
    assert len(page1["hits"]) == 2
    assert page1["next_cursor"] is not None

    page2 = _hits(client, "量子计算", limit=2, cursor=page1["next_cursor"])
    assert page2["total"] == 3
    assert len(page2["hits"]) == 1
    assert page2["next_cursor"] is None
    keys1 = {(h["ts_ms"], h["kind"]) for h in page1["hits"]}
    keys2 = {(h["ts_ms"], h["kind"]) for h in page2["hits"]}
    assert not keys1 & keys2


# ---- 空结果 / 错误 / 索引缺失 ----


def test_search_no_result(client):
    data = _hits(client, "不存在的词xyz")
    assert data["total"] == 0
    assert data["hits"] == []
    assert data["next_cursor"] is None


def test_search_stopword_returns_empty(client):
    """停用词/无有效 token：空结果（不落 FTS，Q4 定案）。"""
    data = _hits(client, "的")
    assert data["total"] == 0
    assert data["hits"] == []


def test_search_punctuation_only_returns_empty(client, caplog):
    """纯标点查询（切词后无 token）→ 空结果；不得把空匹配串送进 FTS（`MATCH ''` 是语法错误，
    会走 store 的异常兜底并刷 warning）——本用例断言**无 warning**，即未落 FTS。"""
    import logging

    with caplog.at_level(logging.WARNING):
        for q in ("——", "。。。", "· ·"):
            data = _hits(client, q)
            assert data["total"] == 0
            assert data["hits"] == []
    assert not [r for r in caplog.records if r.name.startswith("bff.")], caplog.records


def test_search_cap_keyword_returns_empty(client):
    """大写 FTS 关键字（AND/OR/NOT 裸词）→ 语法错误 → 空结果（docs/bff_search_fts.md 矩阵 #17）。"""
    for q in ("AND", "OR", "NOT"):
        data = _hits(client, q)
        assert data["total"] == 0
        assert data["hits"] == []


def test_search_single_cjk_char_no_match(lvdata_root, client):
    """单字 CJK 查询不命中（bigram 索引语义：多字串只产生双字 token，无单字 token）；
    客户端 MIN_QUERY_LENGTH=2 已挡；docs/bff_search_fts.md 矩阵 #18。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 100_000, etype="chat", summary="今天天气不错")
    assert _hits(client, "天")["total"] == 0     # 单字 token 不在索引中
    assert _hits(client, "天气")["total"] == 1   # 双字短语命中同一文档


def test_search_negative_cursor_clamped(lvdata_root, client):
    """负 cursor 钳制为 0（SQLite 负 OFFSET 会被当 0，但 next_cursor 计算会错位）。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 90_000, etype="chat", summary="葡萄")
    data = _hits(client, "葡萄", cursor="0|-3")
    assert data["total"] == 1
    assert len(data["hits"]) == 1


def test_search_types_match_display_corpus():
    """可搜五类 == 展示口径五类——防常量漂移。

    展示口径（hotSlots 分段输入）自 docs/bff_service_interfaces.md §7 起由 lvdata-mcp
    的 `consts.HOT_SLOT_TYPES` 定义（BFF 侧已删派生，不再持有该常量）；此处按字面量
    钉住契约，**改动需与 lvdata-mcp consts.py 同步**（跨包无法 import 比对）。
    """
    from bff.search.service import SEARCH_TYPES

    assert set(SEARCH_TYPES) == {"chat", "transcribe", "translate",
                                 "agent_job", "video"}


def test_search_empty_query_400(client):
    resp = client.get("/api/mylife/search", params={"q": ""})
    assert resp.status_code == 400
    assert resp.json() == {"error": {"code": "bad_request", "message": "empty query"}}

    resp = client.get("/api/mylife/search", params={"q": "   "})
    assert resp.status_code == 400


def test_search_invalid_cursor_400(client):
    resp = client.get("/api/mylife/search", params={"q": "股票", "cursor": "garbage"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


def test_search_device_filter(client):
    data = _hits(client, "股票", device_id="unknown-device")
    assert data["total"] == 0


def test_search_limit_bounds(client):
    resp = client.get("/api/mylife/search", params={"q": "股票", "limit": 0})
    assert resp.status_code == 422
    resp = client.get("/api/mylife/search", params={"q": "股票", "limit": 201})
    assert resp.status_code == 422


def test_search_payload_only_text_not_indexed(lvdata_root, client):
    """索引只覆盖 events.summary 列：仅 payload 内有文本（summary 空）的事件不可搜
    （生产写入方对五类恒写 summary=正文，故对五类无损失；docs/bff_search_fts.md §1.2）。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 80_000, etype="transcribe",
                  summary="", payload={"role": "user", "text": "只写了 payload 的独特词"})
    assert _hits(client, "独特词")["total"] == 0


def test_search_index_missing_returns_empty(lvdata_root, client):
    """search_docs 缺失（未跑 reindex）→ 空结果（无回退，Q6 定案）。"""
    with sqlite3.connect(lvdata_root / "lvdata_inventory.db") as conn:
        conn.execute("DROP TABLE search_docs")
    data = _hits(client, "股票")
    assert data["total"] == 0
    assert data["hits"] == []


# ---- 放宽回退：精确 0 命中 → bigram OR（docs/bff_search_fts.md §10.1）----


def test_relaxed_terms_shape():
    """放宽词集：CJK → 重叠 bigram；ASCII → 整词；丢 len<2（「A股行情」的 A）并去重。"""
    from bff.search.fts import build_relaxed_query, relaxed_terms

    assert relaxed_terms("A股行情") == ["股行", "行情"]
    assert relaxed_terms("启动耗时优化") == ["启动", "动耗", "耗时", "时优", "优化"]
    assert relaxed_terms("hello 世界") == ["hello", "世界"]
    assert relaxed_terms("的") == []
    assert build_relaxed_query(["股行", "行情"]) == '"股行" OR "行情"'
    assert build_relaxed_query([]) == ""


def test_search_relaxed_fallback_for_composed_topic(lvdata_root, client):
    """拼接词（组成部分在文中、连起来不在）→ 放宽命中，且 snippet 指向命中的 bigram 词。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 200_000, etype="chat",
                  summary="今天下午和同事讨论了性能优化方案，主要卡在启动耗时")
    data = _hits(client, "启动耗时优化")
    assert data["total"] == 1
    hit = data["hits"][0]
    assert hit["kind"] == "chat"
    assert hit["ts_ms"] == TS_A1 + 200_000
    assert any(t in hit["snippet"] for t in ("启动", "耗时", "优化"))


def test_search_relaxed_drops_single_char_token(lvdata_root, client):
    """「A股行情」拆出的单字符 token `A` 不得把无关事件拉进来（只按 股行/行情 检索）。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 210_000, etype="chat",
                  summary="A B C 测试", seq=1)
    _insert_event(lvdata_root, ts_ms=TS_A1 + 220_000, etype="chat",
                  summary="今天行情不错", seq=2)
    data = _hits(client, "A股行情")
    assert data["total"] == 1
    assert data["hits"][0]["ts_ms"] == TS_A1 + 220_000


def test_search_exact_hit_skips_relaxed(lvdata_root, client):
    """有精确命中时不触发放宽（放宽的 OR 会多带出「海南…发展」类非连续命中）。"""
    _insert_event(lvdata_root, ts_ms=TS_A1 + 230_000, etype="chat",
                  summary="海南发展大跌", seq=3)
    _insert_event(lvdata_root, ts_ms=TS_A1 + 240_000, etype="chat",
                  summary="海南旅游发展不错", seq=4)
    data = _hits(client, "海南发展")
    assert data["total"] == 1
    assert data["hits"][0]["ts_ms"] == TS_A1 + 230_000


def test_search_relaxed_pagination(lvdata_root, client):
    """放宽路径分页：跨页不重叠、total 恒定（与精确路径同构、SQL 层排序）。"""
    for i in range(4):
        _insert_event(lvdata_root, ts_ms=TS_A1 + 250_000 + i, etype="chat",
                      summary=f"买了一斤苹果回家{i}", seq=10 + i)
    page1 = _hits(client, "苹果香蕉", limit=2)   # 精确短语不存 → 放宽（苹果 命中 4 条）
    assert page1["total"] == 4
    assert len(page1["hits"]) == 2
    assert page1["next_cursor"] is not None
    page2 = _hits(client, "苹果香蕉", limit=2, cursor=page1["next_cursor"])
    assert page2["total"] == 4
    assert len(page2["hits"]) == 2
    assert page2["next_cursor"] is None
    keys1 = {h["ts_ms"] for h in page1["hits"]}
    keys2 = {h["ts_ms"] for h in page2["hits"]}
    assert not keys1 & keys2


def test_search_relaxed_empty_when_no_term_matches(client):
    """放宽后仍 0 命中 → 空结果（「紫薯」不在任何事件里）。"""
    data = _hits(client, "紫薯")
    assert data["total"] == 0
    assert data["hits"] == []


# ---- 查询串安全：ASCII 标点/关键字不再触 FTS5 语法错（§10.3 修复）----

# 修复前的报错样本（原样透传 → FTS5 语法解析失败）
SYNTAX_CHAR_QUERIES = [
    "recording_data_process.md",   # `.`（真实 topic search_words 形态）
    "What's your name?",           # 半角 `?`
    "C++",                         # 尾随 `+`
    "test@example.com",            # `@` `.`
    "https://a.b/c",               # `:` `/`（列过滤语法 → no such column）
    "a:b", "a{b", "a}b",           # 列过滤语法
    "*abc",                        # 前导 `*`（unknown special query）
    "abc^",                        # 非前导 `^`
    "AND", "OR", "NOT", "OR abc",  # 大写关键字
    "v1.2.3", "0.5%", "c++",       # 数字/符号混排
    "...", "???", "*",             # 全标点 → 空短语（0 命中，不报错）
]

# 词被分隔符吃光 → 空 MATCH 串：**不由 FTS 消费**，调用侧必须前置早退
# （bff service.py:99 / MCP search()；`MATCH ''` 必抛）
SEPARATOR_ONLY_QUERIES = ['"', "'", "—", "——", "- -", "· ·", "  ", "，。！？"]


def test_build_fts_query_always_valid_fts5():
    """**非空** MATCH 串必须能被 FTS5 解析（修复的核心不变式）。

    原 docstring 写"任意输入"不成立——空输出（纯分隔符）落 MATCH 必抛，由调用侧守卫拦截；
    空输出本身是契约的一部分，见 `test_build_fts_query_empty_output_contract`。
    """
    import sqlite3

    from bff.search.fts import build_fts_query

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE d USING fts5(text)")
    for q in SYNTAX_CHAR_QUERIES + ["响应慢", "hello 世界", "记录v1.2.3"]:
        match = build_fts_query(q)
        assert match, q  # 本组样本必须产出非空串（空串样本单列）
        conn.execute("SELECT COUNT(*) FROM d WHERE d MATCH ?", (match,))  # 不抛即通过
    conn.close()


def test_build_fts_query_empty_output_contract():
    """空输出契约：纯分隔符查询 → `""`；`MATCH ''` 必抛——这正是调用侧守卫存在的理由。"""
    import sqlite3

    from bff.search.fts import build_fts_query

    for q in SEPARATOR_ONLY_QUERIES:
        assert build_fts_query(q) == "", q
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE d USING fts5(text)")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT COUNT(*) FROM d WHERE d MATCH ?", ("",))
    conn.close()


@pytest.mark.parametrize("q", SYNTAX_CHAR_QUERIES)
def test_search_query_syntax_chars_no_store_error(client, caplog, q):
    """端到端：不落 `count_fts/search_fts query failed` 告警（修复前必落，随后靠放宽兜底）。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="bff.common.store"):
        resp = client.get("/api/mylife/search", params={"q": q})
    assert resp.status_code == 200
    assert "query failed" not in caplog.text


def test_snippet_window():
    """长文本：命中子串居中，前后省略号，总长 ≈ 120 字符窗口；多词退化首个命中词。"""
    from bff.search.service import _snippet

    long_text = "前" * 80 + "目标词" + "后" * 100
    s = _snippet(long_text, "目标词")
    assert "目标词" in s
    assert s.startswith("…")
    assert s.endswith("…")
    assert len(s) <= 120 + 3 + 2  # 窗口 + 命中词 + 两个省略号

    short = _snippet("简短文本", "简短")
    assert short == "简短文本"

    # 多词（FTS 隐式 AND → 词可不相邻）：整串不在原文时按首个命中词定位
    multi = _snippet("前" * 40 + "苹果" + "中" * 40 + "香蕉" + "后" * 40, "苹果 香蕉")
    assert "苹果" in multi


# ---- 契约：bff 查询侧实现与 lvdata-mcp 索引侧同函数（漂移由本测试守护）----


def test_fts_helpers_match_lvdata_mcp():
    index_mod = pytest.importorskip("lvdata_mcp.index")
    from bff.search import fts

    samples = ["股票怎么看", "hello world", "Hello", "AI 与 协作", "的", "", "   ",
               "《封神演义》讨论", "abc-123", "旅行与天气", "量子计算第0讲",
               "Nyquist 采样定理", "天", "苹果 香蕉", "窗口", "浏览器页面 办公室",
               "，。！？、；：（）【】a-b—c·d",
               # §10.3 加引号修复涉及的形态：标点/关键字/前缀通配
               "v1.2.3", "C++", "a:b", "AND", "What's your name?", "hello*", "...",
               # 空输出契约（两侧必须同判空）：「词被分隔符吃光」的纯分隔符串
               "——", "- -", '"']
    for s in samples:
        assert fts.cjk_bigram(s) == index_mod.cjk_bigram(s), s
        assert fts.build_fts_query(s) == index_mod.build_fts_query(s), s
        assert fts.is_stopword_query(s) == index_mod.is_stopword_query(s), s
