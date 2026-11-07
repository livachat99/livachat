"""RAG 向量检索（设计文档 §9 R2）：事件级 chunk + sqlite-vec + FTS/向量混合检索。

- 向量库独立于 inventory DB（`lvdata_index.db`，可整体重建，不锁主库）
- 索引单元 = 事件（event_ref 为稳定主键），命中可直接 `get_event` 追溯
- embedding provider 抽象：DashScope（text-embedding-v4）/ 测试用 fake；
  `--offline`（embedding_model 为空）时向量索引关闭，仅 FTS
- 混合检索：FTS bm25 + 向量 cosine → RRF(k=60) 融合
"""

from __future__ import annotations

import json
import sqlite3
from array import array
from collections import OrderedDict
from pathlib import Path
from typing import Any, Protocol

from .config import LvdataMcpConfig
from .providers import dashscope_headers
from .store import DataStore, StoreError

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# 百炼 embedding 单次请求上限：text-embedding-v4 实测拒绝 >10（2026-08-18 真实
# API 400）；qwen3.7-text-embedding 曾拒绝 >20——取更严的 10 兼容两者
BATCH_SIZE = 10


class EmbeddingProvider(Protocol):
    model: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class DashScopeEmbeddingProvider:
    """百炼 OpenAI 兼容端点（text-embedding-v4 / qwen3.7-text-embedding，1024 维）。

    vendor/workspace_id 与 LlmClient 同一约定：workspace_id 只驱动
    X-DashScope-WorkSpace 头（业务空间专属域名配套），非 dashscope 不带头。
    """

    def __init__(self, model: str = "text-embedding-v4", base_url: str | None = None,
                 dim: int = 1024, api_key: str | None = None,
                 vendor: str = "dashscope", workspace_id: str | None = None):
        import os

        from openai import AsyncOpenAI

        self.model = model
        self.dim = dim
        key = api_key or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            # openai 客户端构造期即校验凭据 → 显式 fail-fast（build_provider 阶段）
            raise ValueError("DASHSCOPE_API_KEY (or OPENAI_API_KEY) is required")
        self._client = AsyncOpenAI(
            base_url=base_url or DEFAULT_BASE_URL,
            api_key=key,
            default_headers=dashscope_headers(vendor, workspace_id),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            resp = await self._client.embeddings.create(
                model=self.model, input=batch,
                dimensions=self.dim if self.model == "text-embedding-v4" else None)
            out.extend(d.embedding for d in resp.data)
        return out


def build_provider(settings: LvdataMcpConfig) -> EmbeddingProvider | None:
    """§4.2/§4.3：embedding 整块为空（model/api_key）→ 关闭向量检索。

    与 ProvidersConfig.embedding_enabled() 同语义；api_key 用 yaml 配置
    （load_config 已 fail-fast 校验），不再依赖 env 兜底。
    """
    if not settings.providers.embedding_enabled():
        return None
    p = settings.providers.embedding
    return DashScopeEmbeddingProvider(
        model=p.model or "text-embedding-v4",
        base_url=p.base_url or None,
        dim=p.dim or 1024,
        api_key=p.api_key or None,
        vendor=p.vendor,
        workspace_id=p.workspace_id)


# ── 向量库 ─────────────────────────────────────────────────────────────────

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    import sqlite_vec

    sqlite_vec.load(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection, dim: int) -> None:
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
        f"embedding float[{dim}] distance_metric=cosine,"
        f"chunk_id TEXT, event_ref TEXT, session_id TEXT, device_uuid TEXT,"
        f"type TEXT, ts_ms INTEGER, text TEXT)")
    # per-event 增量状态（recording_data_process.md §2.3/§2.3-4）：记录每个
    # event_ref 已嵌入的 events.updated_at——内容变化（updated_at 不一致）或缺失
    # = 待重嵌；崩溃后据此恢复（不依赖内存）。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vec_state ("
        "event_ref TEXT PRIMARY KEY, updated_at TEXT NOT NULL)")


_UPSERT_STATE_SQL = (
    "INSERT INTO vec_state (event_ref, updated_at) VALUES (?, ?) "
    "ON CONFLICT(event_ref) DO UPDATE SET updated_at = excluded.updated_at"
)


def _iter_session_chunks(conn: sqlite3.Connection, store: DataStore,
                         session_id: str):
    """产出 (chunk_id, event_ref, type, ts_ms, text)；数据源 = events（§5.9）。

    chunk 粒度 = event，chunk_id = event_ref；text = events.summary
    （检索/LLM 三面一致）。
    """
    rows = conn.execute(
        "SELECT event_ref, type, ts_ms, summary FROM events "
        "WHERE rsession_id = ? ORDER BY ts_ms", (session_id,)).fetchall()
    for r in rows:
        if not (r["summary"] or "").strip():
            continue
        yield (r["event_ref"], r["event_ref"], r["type"], r["ts_ms"],
               r["summary"])


class VecIndex:
    def __init__(self, settings: LvdataMcpConfig, store: DataStore,
                 provider: EmbeddingProvider, query_cache_size: int = 500):
        self.settings = settings
        self.store = store
        self.provider = provider
        self.db_path = str(settings.data.resolved_rag_db_path)
        # 查询向量 LRU（§13）：只缓存 query 侧；文档嵌入路径（sync_changed/rebuild_*）不碰它。
        # 存 array('d')（float64）——float32 往返不逐位还原，会让命中/未命中喂给 KNN 的向量
        # 略有差异（近并列排序可能翻转），不用它换内存。
        self._query_cache_size = query_cache_size
        self._query_cache: OrderedDict[str, array] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0

    def conn(self) -> sqlite3.Connection:
        conn = _connect(self.db_path)
        _ensure_schema(conn, self.provider.dim)
        return conn

    # ── 查询向量缓存（§13）──────────────────────────────────────────────────

    @staticmethod
    def _norm_query(text: str) -> str:
        """缓存键 = embedding 输入：strip + 内部空白折叠（不折大小写）。"""
        return " ".join(text.split())

    async def embed_query(self, text: str) -> list[float]:
        """query string → 向量（进程内 LRU；命中免 API 往返，实测 ~93 ms）。

        同一个 query 换 `limit`/日期窗/`type` 等都命中同一条（embedding 只依赖文本）。
        失败不写缓存（无负缓存）；`query_cache_size=0` 等价关闭。返回 `list[float]`，
        与 `provider.embed` 的形状一致（`vec.search` 需要 JSON 可序列化）。
        """
        key = self._norm_query(text)
        cached = self._query_cache.get(key)
        if cached is not None:
            self._cache_hits += 1
            self._query_cache.move_to_end(key)
            return list(cached)
        self._cache_misses += 1
        vec = (await self.provider.embed([key]))[0]
        if self._query_cache_size > 0:
            self._query_cache[key] = array("d", vec)
            while len(self._query_cache) > self._query_cache_size:
                self._query_cache.popitem(last=False)   # 逐出最旧（LRU）
        return vec

    def query_cache_stats(self) -> dict:
        """缓存观测（/api/status 的 cache.query_embeddings）：累计命中/未命中 + 当前条数。"""
        return {"size": len(self._query_cache), "hits": self._cache_hits,
                "misses": self._cache_misses}

    def _chunks_for_session(self, session_id: str) -> list[tuple[str, str, str, str, int | None, str]]:
        out: list[tuple[str, str, str, str, int | None, str]] = []
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT device_uuid FROM recording_sessions WHERE rsession_id = ?",
                (session_id,)).fetchone()
            if row is None:
                return out
            for chunk_id, _src, etype, ts_ms, text in _iter_session_chunks(
                    conn, self.store, session_id):
                if text:
                    out.append((chunk_id, chunk_id, session_id,
                                row["device_uuid"], etype, ts_ms, text))
        return out

    def _session_state(self, session_id: str) -> list[tuple[str, str]]:
        """该会话全部事件的 (event_ref, updated_at)——vec_state 同步用。"""
        with self.store.connect() as conn:
            return [(r["event_ref"], r["updated_at"]) for r in conn.execute(
                "SELECT event_ref, updated_at FROM events WHERE rsession_id = ?",
                (session_id,)).fetchall()]

    async def rebuild_session(self, session_id: str) -> int:
        chunks = self._chunks_for_session(session_id)
        state = self._session_state(session_id)
        vecs = await self.provider.embed([c[6] for c in chunks]) if chunks else []
        with self.conn() as conn:
            conn.execute(
                "DELETE FROM vec_chunks WHERE session_id = ?", (session_id,))
            # vec0 INTEGER 元数据列不接受 NULL：无时间戳的 chunk（summary）记 0
            rows = [
                (json.dumps(v), chunk_id, event_ref, sid, device, etype,
                 0 if ts_ms is None else ts_ms, text)
                for v, (chunk_id, event_ref, sid, device, etype, ts_ms, text)
                in zip(vecs, chunks)
            ]
            if rows:
                conn.executemany(
                    "INSERT INTO vec_chunks (embedding, chunk_id, event_ref, "
                    "session_id, device_uuid, type, ts_ms, text) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    rows)
            if state:
                conn.executemany(_UPSERT_STATE_SQL, state)
            conn.commit()
        return len(chunks)

    async def sync_changed(self, batch_size: int = 64) -> int:
        """per-event 增量同步（§2.3 index/vector 步）：内容变化或缺失的事件重嵌，
        事件已删的孤儿清 chunk + state。返回重嵌事件数。

        状态在 DB（vec_state），崩溃/重启后下轮自然续；空文本事件也标 state
        （不产 chunk，不重复考虑）。embed 先于删除执行——瞬时失败不丢旧 chunk。
        """
        with self.store.connect() as conn:
            events = {r["event_ref"]: (r["updated_at"], r["type"], r["ts_ms"],
                                       r["summary"], r["rsession_id"],
                                       r["device_uuid"])
                      for r in conn.execute(
                          "SELECT event_ref, updated_at, type, ts_ms, summary, "
                          "rsession_id, device_uuid FROM events").fetchall()}
        with self.conn() as conn:
            state = {r["event_ref"]: r["updated_at"]
                     for r in conn.execute(
                         "SELECT event_ref, updated_at FROM vec_state").fetchall()}
            stale = [ref for ref, v in events.items() if state.get(ref) != v[0]]
            orphans = [ref for ref in state if ref not in events]
            if orphans:
                ph = ",".join("?" * len(orphans))
                conn.execute(f"DELETE FROM vec_chunks WHERE event_ref IN ({ph})",
                             orphans)
                conn.execute(f"DELETE FROM vec_state WHERE event_ref IN ({ph})",
                             orphans)
            embedded = 0
            for i in range(0, len(stale), batch_size):
                batch = stale[i:i + batch_size]
                items = [(ref, events[ref]) for ref in batch
                         if (events[ref][3] or "").strip()]
                vecs = await self.provider.embed([it[1][3] for it in items]) \
                    if items else []
                ph = ",".join("?" * len(batch))
                conn.execute(f"DELETE FROM vec_chunks WHERE event_ref IN ({ph})",
                             batch)
                for (ref, (_upd, etype, ts_ms, text, sid, device)), v in \
                        zip(items, vecs):
                    conn.execute(
                        "INSERT INTO vec_chunks (embedding, chunk_id, event_ref, "
                        "session_id, device_uuid, type, ts_ms, text) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (json.dumps(v), ref, ref, sid, device, etype,
                         0 if ts_ms is None else ts_ms, text))
                embedded += len(items)
                conn.executemany(_UPSERT_STATE_SQL,
                                 [(ref, events[ref][0]) for ref in batch])
            conn.commit()
        return embedded

    def remove_refs(self, refs: list[str]) -> int:
        """按 event_ref 删除向量行（chunks + state），返回删除的 chunk 数。

        删除接口专用（docs/bff_service_remove.md §3.2）：语句与孤儿清扫同款
        （`sync_changed`/`sweep_orphans`）；向量库不存在 → 0（不建库）。
        调用方（`remove.EventRemover`）负责吞异常语义（D10）。
        """
        if not refs or not Path(self.db_path).exists():
            return 0
        ph = ",".join("?" * len(refs))
        with self.conn() as conn:
            n = conn.execute(
                f"SELECT COUNT(*) FROM vec_chunks WHERE event_ref IN ({ph})",
                refs).fetchone()[0]
            conn.execute(f"DELETE FROM vec_chunks WHERE event_ref IN ({ph})", refs)
            conn.execute(f"DELETE FROM vec_state WHERE event_ref IN ({ph})", refs)
            conn.commit()
        return int(n)

    async def rebuild_all(self) -> int:
        total = 0
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT rsession_id FROM recording_sessions").fetchall()
        for r in rows:
            total += await self.rebuild_session(r["rsession_id"])
        self.sweep_orphans()
        return total

    def sweep_orphans(self) -> int:
        """清理孤儿 chunk：events 已不存在的 event_ref，返回清扫数。

        增量重建只覆盖有事件的会话；事件被清理（如静音判定收紧）后该会话
        不再进重建集合（无 dirty 信号），残留 chunk 永不失效——全量重建
        路径（embed rebuild_all / reindex --vectors）顺带清扫（§5.9）。
        """
        with self.store.connect() as conn:
            valid = {r["event_ref"]
                     for r in conn.execute("SELECT event_ref FROM events")}
        with self.conn() as conn:
            rows = conn.execute("SELECT event_ref FROM vec_chunks").fetchall()
            stale = [r["event_ref"] for r in rows if r["event_ref"] not in valid]
            if stale:
                placeholders = ",".join("?" * len(stale))
                conn.execute(
                    f"DELETE FROM vec_chunks WHERE event_ref IN ({placeholders})",
                    stale)
            state_rows = conn.execute("SELECT event_ref FROM vec_state").fetchall()
            stale_state = [r["event_ref"] for r in state_rows
                           if r["event_ref"] not in valid]
            if stale_state:
                placeholders = ",".join("?" * len(stale_state))
                conn.execute(
                    f"DELETE FROM vec_state WHERE event_ref IN ({placeholders})",
                    stale_state)
            conn.commit()
        return len(stale)

    def search(self, query_vec: list[float], session_id: str | None = None,
               etype: str | None = None, limit: int = 10,
               ts_min: int | None = None, ts_max: int | None = None) -> list[dict]:
        """vec0 KNN；`ts_min`/`ts_max` 为 epoch ms 闭区间（§11：日期窗下推）。

        vec0 的元数据约束在 KNN 内是**先筛后取 k**（0.1.9 实测），窗口不会丢召回。
        """
        where: list[str] = []
        params: list[Any] = []
        if session_id:
            where.append("session_id = ?"); params.append(session_id)
        if etype:
            where.append("type = ?"); params.append(etype)
        if ts_min is not None:
            where.append("ts_ms >= ?"); params.append(ts_min)
        if ts_max is not None:
            where.append("ts_ms <= ?"); params.append(ts_max)
        sql = ("SELECT event_ref, session_id, type, ts_ms, text, distance "
               "FROM vec_chunks")
        if where:
            sql += " WHERE " + " AND ".join(where) + " AND embedding MATCH ? AND k = ?"
        else:
            sql += " WHERE embedding MATCH ? AND k = ?"
        params += [json.dumps(query_vec), limit]
        with self.conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {"event_ref": r["event_ref"], "session_id": r["session_id"],
             "type": r["type"], "ts_ms": r["ts_ms"], "text": r["text"],
             "score": round(1.0 - r["distance"], 4)}  # cosine distance → 相似度
            for r in rows
        ]


# ── 混合检索（RRF）─────────────────────────────────────────────────────────

def rrf_merge(fts_hits: list[dict], vec_hits: list[dict], k: int = 60,
              min_vec_score: float | None = None) -> list[dict]:
    """FTS/向量两路 RRF 融合（k=60）；命中字段语义与 search 对齐（§11 D2）：

    `snippet` = FTS bigram 高亮片段（纯向量命中为空串）、`text` = 可读原文 ≤200 字；
    `score` = RRF 融合分（仅排序用），腿级原生分另暴露（§15）：`fts_score` = `-bm25`、
    `vec_score` = 余弦相似度（仅该路命中时出现）。

    `min_vec_score`（§15）：**只过滤向量路**——相似度低于它的向量命中不入选（FTS 命中不受
    影响，bm25 无界不适合作阈值）；过滤**保留腿内原始名次**（`1/(k+rank+1)` 仍用原始 rank，
    不为幸存者重排）；`None`/0 = 不过滤。
    """
    scores: dict[str, dict] = {}
    for rank, h in enumerate(fts_hits):
        ref = h["event_ref"]
        s = scores.setdefault(ref, {"event_ref": ref, "session_id": h.get("session_id"),
                                    "type": h.get("type"), "ts_ms": h.get("ts_ms"),
                                    "score": 0.0, "via": [], "snippet": "", "text": ""})
        s["score"] += 1.0 / (k + rank + 1)
        s["via"].append("fts")
        s["snippet"] = h.get("snip") or ""
        s["text"] = (h.get("text") or "")[:200]
        s["fts_score"] = h.get("score")
    for rank, h in enumerate(vec_hits):
        if min_vec_score and (h.get("score") or 0.0) < min_vec_score:
            continue                      # 过滤但仍用原始 rank（下面的 rank 未被重置）
        ref = h["event_ref"]
        s = scores.setdefault(ref, {"event_ref": ref, "session_id": h.get("session_id"),
                                    "type": h.get("type"), "ts_ms": h.get("ts_ms"),
                                    "score": 0.0, "via": [], "snippet": "", "text": ""})
        s["score"] += 1.0 / (k + rank + 1)
        if "vec" not in s["via"]:
            s["via"].append("vec")
        if not s["text"]:
            s["text"] = (h.get("text") or "")[:200]
        s["vec_score"] = h.get("score")
    return sorted(scores.values(), key=lambda x: x["score"], reverse=True)


def expand_context(store: DataStore, hit: dict, tz: str,
                   window_ms: int = 120_000, limit: int = 10) -> list[dict]:
    """命中 → 邻域事件（±window_ms，rsession_id 范围），把 RAG 片段还原为
    多模态时间上下文（§11.3；v2 数据源 = events 表）。

    输出为**轻量投影**（`store._event_brief`，与 `get_timeline` 的 `days[].events[]`
    逐字同形，§12）：`{event_ref, type, timestamp, source, summary≤256}`；`tz` 为生效
    时区串（渲染用原始生效串，§8.7）。细节（audio `transcript`、video `ocr`/关键帧）
    按 `event_ref` 走 `get_event`。
    """
    ts = hit.get("ts_ms")
    if not ts or not hit.get("session_id"):
        return []
    try:
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE rsession_id = ? "
                "AND ts_ms BETWEEN ? AND ? ORDER BY ts_ms, event_id LIMIT ?",
                (hit["session_id"], ts - window_ms, ts + window_ms, limit)).fetchall()
    except StoreError:
        return []
    return [store._event_brief(r, tz) for r in rows]
