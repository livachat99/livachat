"""FastMCP tool 定义（§12 v2 集合）。全部只读；错误返回结构化错误对象。

注意：不要加 `from __future__ import annotations`——mcp 1.12 的 Tool.from_function
用 issubclass() 直接检查未 eval 的注解字符串，字符串注解会抛 TypeError。
"""

import base64
import json
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import ValidationError

from .consts import EVENT_TYPES
from .index import build_fts_query, is_stopword_query, search as _search
from .schemas import (
    EventParams,
    RecentEventsParams,
    SearchParams,
    SemanticSearchParams,
    TimelineParams,
)
from .store import DataStore, StoreError
from .timeutil import date_window_ms, ms_to_local_or_none
from .vec import VecIndex, expand_context, rrf_merge

log = structlog.get_logger("lvdata_mcp.tools")


def error_result(code: str, message: str, **extra: Any) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


def text_result(payload: dict) -> CallToolResult:
    """结构化 payload → 单段 TextContent 的 CallToolResult（错误也走这里，不置 isError）。"""
    return CallToolResult(content=[TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))])


def _safe_call(fn, *args):
    try:
        return fn(*args)
    except StoreError as e:
        return error_result(e.code, e.message, **e.extra)


def register_tools(mcp: FastMCP, store: DataStore,
                   vec_index: VecIndex | None = None) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Cross-modal full-text search over chat text, audio transcripts, visual "
            "captions/OCR, and agent job results. Narrow by date window and type; hits "
            "include event_ref for get_event."
        ),
    )
    async def search(
        query: str,
        type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
        timezone: str | None = None,
    ) -> dict:
        try:
            p = SearchParams(query=query, type=type, start_date=start_date,
                             end_date=end_date, limit=limit, timezone=timezone)
        except ValidationError as e:
            return error_result("invalid_param", str(e.errors()[0]["msg"]))
        if is_stopword_query(p.query):
            return error_result(
                "invalid_param",
                "Query is empty or contains only stopwords (nothing to search)")
        fts = build_fts_query(p.query)
        if not fts:
            # 纯分隔符查询（「——」/「- -」）：MATCH '' 是 FTS5 语法错误，
            # 会落到下面的 broad except 被误报为 index_not_built（bff 同款前置守卫）
            return error_result(
                "invalid_param",
                "Query contains no valid search terms (separators/punctuation only)")
        tz, tz_out = store.effective_tz(p.timezone)
        try:
            start_ms, end_ms = date_window_ms(p.start_date, p.end_date, tz)
        except ValueError as e:
            return error_result("invalid_param", str(e))
        try:
            with store.connect() as conn:
                hits = _search(conn, fts, p.type, None, start_ms, end_ms, p.limit)
        except StoreError as e:
            return error_result(e.code, e.message, **e.extra)
        except Exception:
            return error_result(
                "index_not_built",
                "search_docs index is not ready",
                hint="Run `lvdata-mcp reindex` or wait for the watcher to build the index",
            )
        return {
            "query": p.query,
            "timezone": tz_out,
            "searched_types": list(EVENT_TYPES),
            "hits": [
                {
                    "event_ref": h["event_ref"],
                    "type": h["type"],
                    "timestamp": ms_to_local_or_none(h["ts_ms"], tz),
                    "snippet": h["snip"],  # bigram 高亮片段
                    "text": h["text"],     # 原文（Agent 可读）
                    "score": h["score"],
                }
                for h in hits
            ],
        }

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Semantic retrieval (FTS + vectors, RRF fusion). Hits include via, vec_score, "
            "fts_score; details via get_event. with_context=true adds ±2min context events. "
            "Returns embedding_disabled when not configured and embedding_failed on API "
            "failure (no silent fallback); FTS failure degrades to vector-only."
        ),
    )
    async def semantic_search(
        query: str,
        type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
        timezone: str | None = None,
        with_context: bool = False,
    ) -> dict:
        try:
            p = SemanticSearchParams(query=query, type=type, start_date=start_date,
                                     end_date=end_date, limit=limit, timezone=timezone)
        except ValidationError as e:
            return error_result("invalid_param", str(e.errors()[0]["msg"]))
        if vec_index is None:
            return error_result(
                "embedding_disabled",
                "Embedding provider is not configured (vector index disabled)",
                hint="Configure providers.embedding and run reindex --vectors, then retry",
            )
        if is_stopword_query(p.query):
            return error_result(
                "invalid_param",
                "Query is empty or contains only stopwords (nothing to search)")
        fts = build_fts_query(p.query)
        if not fts:
            # 纯分隔符查询（「——」/「- -」）：MATCH '' 是 FTS5 语法错误，本工具又无兜底
            # except → 会以未捕获异常返回（同 search() 前置守卫）
            return error_result(
                "invalid_param",
                "Query contains no valid search terms (separators/punctuation only)")
        tz, tz_out = store.effective_tz(p.timezone)
        try:
            start_ms, end_ms = date_window_ms(p.start_date, p.end_date, tz)
        except ValueError as e:
            return error_result("invalid_param", str(e))
        try:
            query_vec = await vec_index.embed_query(p.query)   # §13：进程内 LRU
        except Exception as e:
            return error_result("embedding_failed", f"Embedding request failed: {e}")
        vec_hits = vec_index.search(query_vec, None, p.type, limit * 3,
                                    ts_min=start_ms, ts_max=end_ms)
        try:
            with store.connect() as conn:
                fts_hits = _search(conn, fts, p.type, None,
                                   start_ms, end_ms, limit * 3)
        except Exception as e:  # noqa: BLE001 — FTS 路不可用 → 降级为纯向量（§11 D5）
            # 索引表缺失抛的是 sqlite3.OperationalError（非 StoreError）；只 catch
            # StoreError 会让"静默降级"名存实亡（未捕获异常直接抛给调用方）
            log.warning("semantic_search fts leg degraded", error=str(e)[:200])
            fts_hits = []
        # §15：向量路相似度下限（配置驱动，0 = 关闭）；过滤只减不增——幸存数少于 limit
        # 就少返回，不为凑满回填低相关命中。
        min_score = store.settings.providers.embedding.min_vec_score
        dropped = [h["score"] for h in vec_hits
                   if min_score and (h.get("score") or 0.0) < min_score]
        hits = []
        for h in rrf_merge(fts_hits, vec_hits, min_vec_score=min_score)[: p.limit]:
            # 邻域解析必须在剥离 session_id 之前（expand_context 依赖它，§11.3）
            ctx = expand_context(store, h, tz) if with_context else None
            hit = {
                "event_ref": h["event_ref"],
                "type": h["type"],
                "timestamp": ms_to_local_or_none(h.get("ts_ms"), tz),
                "score": h["score"],
                "via": h["via"],
                "snippet": h.get("snippet") or "",
                "text": h.get("text") or "",
            }
            if h.get("vec_score") is not None:
                hit["vec_score"] = h["vec_score"]      # 余弦相似度（§15）
            if h.get("fts_score") is not None:
                hit["fts_score"] = h["fts_score"]      # -bm25
            if with_context:
                hit["context_events"] = ctx
            hits.append(hit)
        out = {"query": p.query, "timezone": tz_out, "mode": "hybrid",
               "searched_types": list(EVENT_TYPES)}
        if dropped:      # 仅在真的丢弃过时出现：出现本身即"过滤发生了"的信号（§15）
            out["filtered"] = {"min_score": min_score, "best_dropped": max(dropped)}
        out["hits"] = hits
        return out

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Unified multimodal timeline (events view): grouped by day (ascending; "
            "ascending within a day); cursor is the [ts_ms, type, event_id] keyset, "
            "and pagination may split a day across pages."
        ),
    )
    async def get_timeline(
        start_date: str | None = None,
        end_date: str | None = None,
        types: list[str] | None = None,
        limit: int = 200,
        cursor: str | None = None,
        timezone: str | None = None,
    ) -> dict:
        try:
            p = TimelineParams(start_date=start_date, end_date=end_date, types=types,
                               limit=limit, cursor=cursor, timezone=timezone)
        except ValidationError as e:
            return error_result("invalid_param", str(e.errors()[0]["msg"]))
        return _safe_call(store.get_timeline, p.types, p.start_date, p.end_date,
                          p.timezone, p.limit, p.cursor) or {}

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Full detail for one event_ref ({session_id}:{type}:{source_id}); video "
            "events with a keyframe include image content."
        ),
    )
    async def get_event(
        event_ref: str, timezone: str | None = None
    ) -> CallToolResult:
        try:
            p = EventParams(event_ref=event_ref, timezone=timezone)
        except ValidationError as e:
            return text_result(
                error_result("invalid_param", str(e.errors()[0]["msg"])))
        try:
            out, img = store.get_event(p.event_ref, p.timezone)
        except StoreError as e:
            return text_result(error_result(e.code, e.message, **e.extra))
        content = [TextContent(
            type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]
        if img is not None:
            data, mime = img
            content.append(ImageContent(
                type="image", data=base64.b64encode(data).decode(), mimeType=mime))
        return CallToolResult(content=content)

    @mcp.tool(
        annotations={"readOnlyHint": True},
        description=(
            "Event list for the last N local days (days=1 = today, max 31): grouped by "
            "day descending, ascending within a day; truncated=true means the list was "
            "limited by page size (total is the window total)."
        ),
    )
    async def get_recent_events(
        days: int = 1, timezone: str | None = None
    ) -> dict:
        try:
            p = RecentEventsParams(days=days, timezone=timezone)
        except ValidationError as e:
            return error_result("invalid_param", str(e.errors()[0]["msg"]))
        return _safe_call(store.get_recent_events, p.days, p.timezone) or {}
