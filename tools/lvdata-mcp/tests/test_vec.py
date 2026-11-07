"""v2 向量检索测试（§5.9：chunk 粒度 = event，chunk_id = event_ref）。"""

from __future__ import annotations

import asyncio

import pytest

from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.store import DataStore
from lvdata_mcp.vec import VecIndex, rrf_merge

from conftest import (DEVICE, SID, TS, CountingEmbeddingProvider,
                      FakeEmbeddingProvider, make_sample)


def _vec(root) -> tuple[VecIndex, DataStore]:
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "Asia/Shanghai"},
        providers={"embedding": {"model": "fake", "dim": 128}})
    store = DataStore(cfg)
    return VecIndex(cfg, store, FakeEmbeddingProvider()), store


def test_vec_rebuild_and_search(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    vec, store = _vec(root)
    n = asyncio.run(vec.rebuild_session(SID))
    assert n == 5  # 5 条 events（全部有 summary）
    qv = asyncio.run(vec.provider.embed(["为什么响应这么慢"]))[0]
    hits = vec.search(qv, limit=10)
    assert len(hits) >= 1
    assert all(h["event_ref"].startswith(f"{SID}:") for h in hits)


def test_sweep_orphans(tmp_path):
    """孤儿 chunk 清扫：events 已删的 event_ref 不再残留（§5.9）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    vec, store = _vec(root)
    assert asyncio.run(vec.rebuild_all()) == 5  # 全量路径
    # 删除 events 里的 chat:m1 → 对应 chunk 变孤儿
    with store.connect(readonly=False) as conn:
        conn.execute("DELETE FROM events WHERE event_ref = ?", (f"{SID}:chat:m1",))
        conn.commit()
    assert vec.sweep_orphans() == 1
    with vec.conn() as conn:
        refs = {r[0] for r in conn.execute("SELECT event_ref FROM vec_chunks")}
    assert f"{SID}:chat:m1" not in refs
    assert f"{SID}:chat:m2" in refs  # 其余 chunk 不受影响
    assert vec.sweep_orphans() == 0  # 幂等


def test_vec_chunk_id_is_event_ref(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    vec, _ = _vec(root)
    asyncio.run(vec.rebuild_session(SID))
    with vec.conn() as conn:
        rows = conn.execute("SELECT chunk_id, event_ref FROM vec_chunks").fetchall()
    for r in rows:
        assert r[0] == r[1]  # §5.9：chunk_id = event_ref
    refs = {r[1] for r in rows}
    assert f"{SID}:chat:m1" in refs


def test_sync_changed_embeds_only_stale(tmp_path):
    """per-event 增量：首次全嵌 → 无变化 0 工作量 → 内容变化只重嵌该事件。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    vec, store = _vec(root)
    assert asyncio.run(vec.sync_changed()) == 5    # 首次：5 事件全部有 summary
    assert asyncio.run(vec.sync_changed()) == 0    # 无变化 → 零嵌入
    with store.connect(readonly=False) as conn:
        conn.execute(
            "UPDATE events SET summary = ?, updated_at = ? WHERE event_ref = ?",
            ("改过的内容", "2099-01-01T00:00:00.000Z", f"{SID}:chat:m1"))
        conn.commit()
    assert asyncio.run(vec.sync_changed()) == 1    # 只重嵌变化事件
    with vec.conn() as conn:
        row = conn.execute("SELECT text FROM vec_chunks WHERE event_ref = ?",
                           (f"{SID}:chat:m1",)).fetchone()
    assert row["text"] == "改过的内容"


def test_sync_changed_cleans_orphans(tmp_path):
    """事件删除 → chunk + vec_state 一并清理（无孤儿，无需全量重建）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    vec, store = _vec(root)
    asyncio.run(vec.sync_changed())
    with store.connect(readonly=False) as conn:
        conn.execute("DELETE FROM events WHERE event_ref = ?",
                     (f"{SID}:chat:m1",))
        conn.commit()
    asyncio.run(vec.sync_changed())
    with vec.conn() as conn:
        chunk_refs = {r[0] for r in conn.execute(
            "SELECT event_ref FROM vec_chunks")}
        state_refs = {r[0] for r in conn.execute(
            "SELECT event_ref FROM vec_state")}
    assert f"{SID}:chat:m1" not in chunk_refs
    assert f"{SID}:chat:m1" not in state_refs
    assert f"{SID}:chat:m2" in chunk_refs  # 其余不受影响


def test_rebuild_session_marks_state(tmp_path):
    """rebuild_session 同步 vec_state——紧随其后的增量同步应为零工作量。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    vec, _ = _vec(root)
    asyncio.run(vec.rebuild_session(SID))
    assert asyncio.run(vec.sync_changed()) == 0


def test_build_provider_offline(monkeypatch):
    """embedding 整块为空 → 向量索引关闭（§4.2 离线模式）。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    cfg = LvdataMcpConfig(data={"root_dir": "/tmp/x"})
    from lvdata_mcp.vec import build_provider
    assert build_provider(cfg) is None  # 未配置 embedding → None


def test_build_provider_workspace_id_header():
    """embedding 接入 workspace_id：与 LlmClient 同约定（仅 DashScope 带头，
    未配置/非 dashscope 不带头）——自建 client 曾漏透传（2026-09-18 接入）。"""
    from lvdata_mcp.vec import build_provider

    def _cfg(emb: dict) -> LvdataMcpConfig:
        return LvdataMcpConfig(providers={"embedding": emb})

    p = build_provider(_cfg({"api_key": "k", "model": "qwen3.7-text-embedding",
                             "workspace_id": "llm-x"}))
    assert p._client.default_headers["X-DashScope-WorkSpace"] == "llm-x"
    p2 = build_provider(_cfg({"api_key": "k", "model": "m"}))
    assert "X-DashScope-WorkSpace" not in p2._client.default_headers
    p3 = build_provider(_cfg({"api_key": "k", "model": "m", "vendor": "openai",
                              "base_url": "http://x", "workspace_id": "llm-x"}))
    assert "X-DashScope-WorkSpace" not in p3._client.default_headers


def test_vec_search_time_window(tmp_path):
    """ts_min/ts_max 下推 vec0 KNN（§11）：窗外事件不入选，含"最近但在窗外"。

    0.1.9 实测为**先筛后取 k**——若退化成"先取 k 后筛"，把最近邻（chat m1，TS）
    排除在窗外后会返回空集，本用例即失败。
    """
    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    vec, _ = _vec(root)
    asyncio.run(vec.rebuild_session(SID))
    qv = asyncio.run(vec.provider.embed(["为什么响应这么慢"]))[0]
    nearest = vec.search(qv, limit=1)[0]["event_ref"]
    assert nearest == f"{SID}:chat:m1"          # 最近邻 = 该查询的源事件
    hits = vec.search(qv, limit=10, ts_min=TS + 1000, ts_max=TS + 60_000)
    refs = {h["event_ref"] for h in hits}
    assert nearest not in refs                  # 窗外的最近邻被排除
    assert refs                                  # 窗内仍有召回（非"先取 k 后筛"）
    assert all(TS + 1000 <= h["ts_ms"] <= TS + 60_000 for h in hits)
    # 无边界 → 与不传等价
    assert len(vec.search(qv, limit=10)) == len(
        vec.search(qv, limit=10, ts_min=None, ts_max=None))


def test_rrf_merge():
    fts = [{"event_ref": "a", "type": "chat", "ts_ms": 1, "score": 0.1}]
    vec = [{"event_ref": "b", "type": "audio", "ts_ms": 2, "score": 0.9}]
    merged = rrf_merge(fts, vec)
    assert [h["event_ref"] for h in merged] == ["a", "b"]
    assert merged[0]["via"] == ["fts"] and merged[1]["via"] == ["vec"]


def test_rrf_merge_snippet_and_text():
    """§11 D2：`snippet` = FTS 高亮片段（纯向量命中为空串）、`text` = 原文 ≤200 字。"""
    fts = [{"event_ref": "a", "type": "chat", "ts_ms": 1,
            "snip": "为什 什么 么响 <b>响应</b>", "text": "为什么响应这么慢" * 40}]
    vec = [{"event_ref": "b", "type": "chat", "ts_ms": 2, "text": "只在向量路的文本"}]
    merged = {h["event_ref"]: h for h in rrf_merge(fts, vec)}
    assert merged["a"]["snippet"] == "为什 什么 么响 <b>响应</b>"
    assert len(merged["a"]["text"]) == 200          # 原文截断（与 index.search 同口径）
    assert merged["b"]["snippet"] == ""             # 纯向量命中无高亮片段
    assert merged["b"]["text"] == "只在向量路的文本"
    # 两路同源：FTS 的高亮/原文优先
    both = rrf_merge([{"event_ref": "c", "snip": "s<b>n</b>", "text": "原文"}],
                     [{"event_ref": "c", "text": "向量侧文本"}])[0]
    assert both["via"] == ["fts", "vec"]
    assert both["snippet"] == "s<b>n</b>" and both["text"] == "原文"


def test_rrf_merge_exposes_leg_scores():
    """§15：腿级原生分暴露——fts_score（-bm25）/vec_score（余弦相似度），仅对应路出现。"""
    fts = [{"event_ref": "a", "snip": "s<b>n</b>", "text": "原文", "score": 7.5}]
    vec = [{"event_ref": "a", "text": "向量侧", "score": 0.83},
           {"event_ref": "b", "text": "只在向量路", "score": 0.61}]
    merged = {h["event_ref"]: h for h in rrf_merge(fts, vec)}
    a = merged["a"]
    assert a["via"] == ["fts", "vec"]
    # 两路原生分都在；FTS 的 score 是 -bm25，向量路是余弦相似度
    assert a["fts_score"] == 7.5 and a["vec_score"] == 0.83
    # RRF 融合分与原生分无关：两路 rank0 命中 = 1/61 + 1/61
    assert abs(a["score"] - 2 / 61) < 1e-9
    assert "fts_score" not in merged["b"] and merged["b"]["vec_score"] == 0.61
    assert abs(merged["b"]["score"] - 1 / (60 + 1 + 1)) < 1e-9


def test_rrf_merge_min_vec_score_keeps_original_ranks():
    """§15：阈值过滤只丢低相似度向量命中，且**保留腿内原始名次**（不为幸存者重排）。"""
    vec = [{"event_ref": "r0", "text": "", "score": 0.90},
           {"event_ref": "r1", "text": "", "score": 0.10},   # 低于阈值 → 丢弃
           {"event_ref": "r2", "text": "", "score": 0.80}]
    merged = {h["event_ref"]: h for h in rrf_merge([], vec, min_vec_score=0.5)}
    assert set(merged) == {"r0", "r2"}                        # r1 被丢
    assert abs(merged["r0"]["score"] - 1 / 61) < 1e-9
    # r2 仍按原始 rank=2 计分（若被重排成 rank1 会得 1/62）
    assert abs(merged["r2"]["score"] - 1 / (60 + 2 + 1)) < 1e-9
    # 不过滤（None/0）→ 三条都在（§15 回归口径）
    assert len(rrf_merge([], vec)) == 3
    assert len(rrf_merge([], vec, min_vec_score=0)) == 3
    # FTS 命中不受阈值影响
    only_fts = rrf_merge([{"event_ref": "f", "text": "t", "score": -3.0}], vec,
                         min_vec_score=0.99)
    assert {h["event_ref"] for h in only_fts} == {"f"}


def test_query_cache_hit_exact_and_normalized(tmp_path):
    """§13：同 query（含空白差异）只嵌一次；命中与未命中返回的向量逐字相同。"""
    import json as _json

    from lvdata_mcp.vec import VecIndex

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": "Asia/Shanghai"},
                          providers={"embedding": {"model": "fake", "dim": 128}})
    prov = CountingEmbeddingProvider()
    vec = VecIndex(cfg, DataStore(cfg), prov)

    async def both():
        return (await vec.embed_query("狐狸"),
                await vec.embed_query("  狐狸  "))     # 归一化 → 同一条

    v1, v2 = asyncio.run(both())
    assert prov.calls == 1 and prov.seen == ["狐狸"]    # 嵌入的是归一化串
    assert _json.dumps(v1) == _json.dumps(v2)          # float64 保真：逐字相同
    assert vec.query_cache_stats() == {"size": 1, "hits": 1, "misses": 1}


def test_query_cache_lru_eviction(tmp_path):
    """容量 2：第三次不同 query 逐出最旧；被逐出的 query 再问会重新嵌入。"""
    from lvdata_mcp.vec import VecIndex

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": "Asia/Shanghai"},
                          providers={"embedding": {"model": "fake", "dim": 128}})
    prov = CountingEmbeddingProvider()
    vec = VecIndex(cfg, DataStore(cfg), prov, query_cache_size=2)

    async def seq():
        await vec.embed_query("狐狸")
        await vec.embed_query("故事")
        await vec.embed_query("狐狸")     # 命中并刷新 LRU
        await vec.embed_query("蚂蚁")     # 逐出最旧 = 故事
        await vec.embed_query("故事")     # 已被逐出 → 重新嵌入
    asyncio.run(seq())
    assert prov.calls == 4 and prov.seen == ["狐狸", "故事", "蚂蚁", "故事"]
    assert vec.query_cache_stats() == {"size": 2, "hits": 1, "misses": 4}


def test_query_cache_no_negative_caching(tmp_path):
    """失败不写缓存：抛错后重试必须真的再调 API（否则一次瞬时失败会粘住该 query）。"""
    from lvdata_mcp.vec import VecIndex

    class Flaky(CountingEmbeddingProvider):
        async def embed(self, texts):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return await FakeEmbeddingProvider.embed(self, texts)

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    cfg = LvdataMcpConfig(data={"root_dir": str(root)},
                          watcher={"timezone": "Asia/Shanghai"},
                          providers={"embedding": {"model": "fake", "dim": 128}})
    prov = Flaky()
    vec = VecIndex(cfg, DataStore(cfg), prov)
    with pytest.raises(RuntimeError):
        asyncio.run(vec.embed_query("炸"))
    assert vec.query_cache_stats()["size"] == 0
    asyncio.run(vec.embed_query("炸"))
    assert prov.calls == 2 and vec.query_cache_stats()["size"] == 1


def test_expand_context_from_events(tmp_path):
    """expand_context（±2min 邻域）数据源 = events；输出 = 轻量投影（§12）。"""
    from lvdata_mcp.vec import expand_context
    from conftest import SID, TS

    root = tmp_path / "lvdata"
    root.mkdir()
    make_sample(root)
    store = DataStore(LvdataMcpConfig(data={"root_dir": str(root)}, watcher={"timezone": "Asia/Shanghai"}))
    ctx = expand_context(store, {"session_id": SID, "ts_ms": TS + 10000},
                         "Asia/Shanghai", window_ms=60000, limit=10)
    assert len(ctx) >= 3  # TS±60s 内：chat/audio/window/video
    assert set(ctx[0]) == {"event_ref", "type", "timestamp", "source", "summary"}
    assert ctx[0]["timestamp"] is not None            # 可读时间（非 epoch）
    refs = [e["event_ref"] for e in ctx]
    assert f"{SID}:chat:m1" in refs
    assert f"{SID}:window:w1" in refs
    # 无 ts / 无 session → 空
    assert expand_context(store, {"session_id": SID}, "Asia/Shanghai") == []
    assert expand_context(store, {"ts_ms": TS}, "Asia/Shanghai") == []
