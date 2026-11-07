"""FTS5 全文索引（设计文档 §6.2/§6.3）。

search_docs 是本服务自有的派生索引，可整体重建（`lvdata-mcp reindex`）。
CJK 文本按空格分隔的 bigram 预切分——unicode61 对中文检索无效，这是低成本方案。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
STOPWORDS = {"的", "了", "吗", "呢", "是", "在", "啊", "吧", "哦", "嗯"}

INDEX_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS search_docs USING fts5(
    text,
    orig_text UNINDEXED,
    event_ref UNINDEXED, session_id UNINDEXED,
    device_uuid UNINDEXED, type UNINDEXED,
    ts_ms UNINDEXED
)
"""


def cjk_bigram(text: str) -> str:
    """CJK 连续串切为重叠 bigram token；非 CJK 词保持整词。索引与查询两侧同函数。"""
    tokens: list[str] = []
    buf: list[str] = []
    run: list[str] = []

    def flush_buf():
        if buf:
            tokens.append("".join(buf))
            buf.clear()

    def flush_run():
        if run:
            if len(run) == 1:
                tokens.append(run[0])
            else:
                tokens.extend("".join(run[i:i + 2]) for i in range(len(run) - 1))
            run.clear()

    for ch in text:
        if CJK_RE.match(ch):
            flush_buf()
            run.append(ch)
        else:
            flush_run()
            buf.append(ch)
    flush_run()
    flush_buf()
    return " ".join(tokens)


def build_fts_query(raw: str) -> str:
    """查询串：词一律双引号包裹（CJK 连续串先 bigram 预切）——零 FTS5 语法歧义。

    与 bff 查询侧 `services/bff/search/fts.py` 同语义（契约测试守护）。加引号是**安全要求**
    （2026-09-21 修复）：非 CJK 词原样透传时，ASCII 标点与大写关键字被 FTS5 当语法解析并抛
    sqlite3.OperationalError（`recording_data_process.md` / `C++` / `a:b` / `*abc` 实测）。
    代价（已接受）：放弃 FTS5 原生通配与布尔语义（`abc*` 前缀查询、`a AND b`）。
    """
    terms: list[str] = []
    for word in re.split(r"[\s，。！？、；：\"'（）【】《》…\-–—·]+", raw):
        if word:
            terms.append(f'"{cjk_bigram(word)}"')
    return " ".join(terms)


def is_stopword_query(raw: str) -> bool:
    cleaned = re.sub(r"[\s，。！？、；：\"'（）【】《》…]+", "", raw)
    return not cleaned or cleaned in STOPWORDS


def ensure_index(conn: sqlite3.Connection) -> None:
    # 老表升级：缺 orig_text 列 → 整体重建（search_docs 是派生索引，可安全重建）
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'search_docs'"
    ).fetchone()
    if existing and "orig_text" not in (existing[0] or ""):
        conn.execute("DROP TABLE search_docs")
    conn.execute(INDEX_SQL)


def rebuild_session(conn: sqlite3.Connection, store, session_id: str) -> int:
    """按 session 重建索引（DELETE + INSERT，幂等，§6.6 数据源 = events）。"""
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM recording_sessions WHERE rsession_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return 0
    conn.execute("DELETE FROM search_docs WHERE session_id = ?", (session_id,))
    events = conn.execute(
        "SELECT event_ref, type, ts_ms, summary FROM events "
        "WHERE rsession_id = ?", (session_id,)).fetchall()
    docs: list[tuple[str, str, str, str, str, str, int | None]] = []
    for ev in events:
        raw = ev["summary"] or ""
        text = cjk_bigram(raw)
        if text:
            docs.append((
                text, raw, ev["event_ref"], session_id, row["device_uuid"],
                ev["type"], ev["ts_ms"]))
    conn.executemany(
        "INSERT INTO search_docs (text, orig_text, event_ref, session_id, "
        "device_uuid, type, ts_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        docs,
    )
    return len(docs)


def _watermark_value(snapshot_at: str) -> str:
    """快照时刻 → 水位值（−1ms，见 set_watermark 语义）。"""
    dt = datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
    dt -= timedelta(milliseconds=1)
    return dt.astimezone(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def set_watermark(conn: sqlite3.Connection, target: str, snapshot_at: str) -> None:
    """index_state 水位 upsert（'' = 全局全量重建；其余 = rsession_id）。

    snapshot_at 必须是入口快照时刻（先于取件与任何 events 读取）。写入值 =
    快照 −1ms：毫秒粒度 + strict `>` 比较下，与快照**同毫秒**（读取之后）
    到达的更新（updated_at == 水位 → 不 > 水位）会被永久漏判；减 1ms 后
    同毫秒更新必 > 水位 → 下轮重判滞后、自愈。声称覆盖「updated_at ≤
    快照−1ms」——这些事件必然先于快照存在、读取必见，语义仍严格成立；代价
    是同毫秒且已索引的事件多发一次重建，方向保守（retention 方案 §2.2）。
    """
    conn.execute(
        "INSERT INTO index_state (target, last_reindex_at) VALUES (?, ?) "
        "ON CONFLICT(target) DO UPDATE SET "
        "last_reindex_at = excluded.last_reindex_at",
        (target, _watermark_value(snapshot_at)))


def clear_session_watermarks(conn: sqlite3.Connection) -> None:
    """全量重建后清会话行去冗余：读路径 COALESCE 落空自然取新的 ''（§2.2）。"""
    conn.execute("DELETE FROM index_state WHERE target != ''")


def lag_sessions(conn: sqlite3.Connection) -> list[str]:
    """FTS 滞后会话：events 水位晚于该 session（或全局 ''）的上次重建水位 T0。

    index_state 无行 → 全部有事件的会话视为滞后（首轮全量重建）；
    reindex stage（增量）据此取件，get_status 复用同一口径（单一出处）。
    COALESCE（自有行优先于 ''）有意保持、不取 MAX——全量重建后旧会话行由
    写侧清理（§2.2）。
    """
    rows = conn.execute(
        "SELECT e.rsession_id FROM "
        "(SELECT rsession_id, MAX(updated_at) AS max_upd FROM events "
        " GROUP BY rsession_id) e "
        "LEFT JOIN index_state r ON r.target = e.rsession_id "
        "LEFT JOIN index_state g ON g.target = '' "
        "WHERE COALESCE(r.last_reindex_at, g.last_reindex_at) IS NULL "
        "OR e.max_upd > COALESCE(r.last_reindex_at, g.last_reindex_at)").fetchall()
    return [r[0] for r in rows]


def rebuild_all(conn: sqlite3.Connection, store) -> int:
    ensure_index(conn)
    conn.execute("DELETE FROM search_docs")
    rows = conn.execute("SELECT rsession_id FROM recording_sessions").fetchall()
    total = 0
    for r in rows:
        total += rebuild_session(conn, store, r["rsession_id"])
    conn.commit()
    return total


def search(
    conn: sqlite3.Connection,
    fts_query: str,
    scope: str | None = None,
    session_id: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = 10,
) -> list[dict]:
    where = ["search_docs MATCH ?"]
    params: list = [fts_query]
    if scope:
        where.append("type = ?"); params.append(scope)
    if session_id:
        where.append("session_id = ?"); params.append(session_id)
    if start_ms is not None:
        where.append("ts_ms >= ?"); params.append(start_ms)
    if end_ms is not None:
        where.append("ts_ms <= ?"); params.append(end_ms)
    rows = conn.execute(
        f"""SELECT event_ref, session_id, device_uuid, type, ts_ms,
                   snippet(search_docs, 0, '<b>', '</b>', '…', 12) AS snip,
                   orig_text, bm25(search_docs) AS score
            FROM search_docs WHERE {' AND '.join(where)}
            ORDER BY score LIMIT ?""",
        params + [limit],
    ).fetchall()
    # snip 是 bigram 化文本（带高亮，机器可读）；text 是原文（Agent 可读）
    return [dict(r) | {"score": round(-r["score"], 4),
                       "text": (r["orig_text"] or "")[:200]} for r in rows]
