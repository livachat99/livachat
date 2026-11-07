"""bff /search：FTS 检索（检索源 = lvdata-mcp 的 `search_docs` 派生索引）。

口径：**可搜 ⊆ 可展示**——结果只含五类可展示事件（chat/transcribe/translate/agent_job/video），
topic/audio/window 不入结果；排序 = bm25 相关度序；索引缺失 → 空结果（无回退）；
精确 0 命中时按 bigram OR 放宽重查一次（§10.1）。
详见 docs/bff_search_fts.md。
"""

from __future__ import annotations

import logging
from datetime import datetime

from bff.common.deps import resolve_request_tz
from bff.common.schemas import SearchHit, SearchResponse
from bff.common.store import BffError, LvdataStore
from bff.search.fts import build_fts_query, build_relaxed_query, is_stopword_query, query_terms, relaxed_terms
from shared.timeutil import resolve_tzinfo

log = logging.getLogger(__name__)

# 命中上下文窗口（~120 字符），命中位置偏前 1/3，前后补省略号
SNIPPET_WIDTH = 120

# 可搜事件类型 = 展示口径五类（展示口径自 docs/bff_service_interfaces.md §7 起由
# lvdata-mcp `consts.HOT_SLOT_TYPES` 定义，bff 侧不再持有——**改动需两侧同步**；
# docs/bff_search_fts.md §2）
SEARCH_TYPES: tuple[str, ...] = ("chat", "transcribe", "translate", "agent_job", "video")


def _local_date(ts_ms: int, tz: str) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, resolve_tzinfo(tz)).strftime("%Y-%m-%d")


def _snippet(text: str, query: str, terms: list[str] | None = None) -> str:
    """命中片段（含命中词，±SNIPPET_WIDTH 字符）。整串 `query` 不出现时退化为首个命中词定位
    ——Android 按整串高亮，找不到则不亮（客户端既有兜底）。

    `terms`：放宽命中（§10.1）传入 bigram 词集——CJK 拼接词（「A股行情」）用分隔符切不开，
    必须用 bigram 词定位，否则会退化成"取文首、看不到命中处"。
    """
    lower = text.lower()
    idx = lower.find(query.lower())
    if idx >= 0:
        hit_len = len(query)
    else:
        idx, hit_len = -1, 0
        for term in (query_terms(query) if terms is None else terms):
            j = lower.find(term.lower())
            if j >= 0:
                idx, hit_len = j, len(term)
                break
    if idx < 0:
        return text[:SNIPPET_WIDTH]
    start = max(0, idx - SNIPPET_WIDTH // 3)
    end = min(len(text), idx + hit_len + SNIPPET_WIDTH * 2 // 3)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def _hit(ts_ms: int, kind: str, text: str, query: str, tz: str,
         terms: list[str] | None = None) -> SearchHit:
    return SearchHit(date=_local_date(ts_ms, tz), ts_ms=ts_ms, kind=kind,
                     snippet=_snippet(text, query, terms))


def _parse_cursor(cursor: str | None) -> int:
    """cursor 形如 `0|offset`（与 timeline 同格式）。"""
    if not cursor:
        return 0
    try:
        _, idx = cursor.rsplit("|", 1)
        return int(idx)
    except (ValueError, AttributeError):
        raise BffError("bad_request", "invalid cursor") from None


def build_search(
    store: LvdataStore,
    device_id: str | None,
    query: str,
    limit: int,
    cursor: str | None,
    timezone: str | None,
    default_tz: str = "Asia/Shanghai",
) -> SearchResponse:
    q = (query or "").strip()
    if not q:
        raise BffError("bad_request", "empty query")
    tz = resolve_request_tz(store, timezone, default_tz)
    start = _parse_cursor(cursor)

    # 停用词/无有效 token：空结果——不落 FTS（「的」这类查询会命中大量无关文档）（Q4 定案）
    if is_stopword_query(q):
        return SearchResponse(query=q, total=0, next_cursor=None, hits=[])

    # 查询串切词后无 token（如纯标点「——」；停用词清洗集不含 `-–—·`，会漏到这一步）：
    # 空匹配串不可落 FTS（`MATCH ''` 是语法错误）→ 同样按空结果处理
    match = build_fts_query(q)
    if not match:
        return SearchResponse(query=q, total=0, next_cursor=None, hits=[])

    # 索引缺失（未跑 reindex）：空结果 + warning（无回退，Q6 定案）
    if not store.has_search_docs():
        log.warning("search_docs index missing; returning empty result")
        return SearchResponse(query=q, total=0, next_cursor=None, hits=[])

    # 设备白名单（对齐 timeline 语义）：可见设备集合；空集合由 store 层返回空（安全关键）
    devices = list(dict.fromkeys(
        s["device_uuid"] for s in store.list_sessions(device_id)))

    total = store.count_fts(match, SEARCH_TYPES, devices)
    relaxed: list[str] | None = None
    if total == 0:
        # 精确 0 命中 → 放宽回退（docs/bff_search_fts.md §10.1）：拼接词/轻微改写类 topic
        # （如「A股行情」）的组成部分在文中、连起来不在；排序/分页仍全在 SQL 层（纯 bm25）
        terms = relaxed_terms(q)
        candidate = build_relaxed_query(terms)
        if candidate:
            match, relaxed = candidate, terms
            total = store.count_fts(match, SEARCH_TYPES, devices)
    start = max(0, min(start, total))  # 负/越界 cursor 钳制（SQLite 负 OFFSET 会当 0，但 next_cursor 会错位）
    rows = store.search_fts(match, SEARCH_TYPES, devices, limit, start)
    hits = [_hit(r["ts_ms"], r["type"], r["orig_text"] or "", q, tz, relaxed) for r in rows]

    next_cursor = None
    if start + limit < total:
        next_cursor = f"0|{start + limit}"
    return SearchResponse(query=q, total=total, next_cursor=next_cursor, hits=hits)
