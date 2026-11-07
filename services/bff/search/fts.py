"""查询侧 FTS 工具（bff /search 检索源 = lvdata-mcp 的 `search_docs`）。

**与索引侧同源（硬约束）**：`cjk_bigram` / `build_fts_query` / `is_stopword_query` 必须与
`tools/lvdata-mcp/src/lvdata_mcp/index.py` 逐字同语义——索引文本按 bigram 预切分，
查询侧一旦不同源就查不到。一致性由契约测试守护（tests/unit/bff 内 import
`lvdata_mcp.index` 对比输出，缺包则 skip）。docs/bff_search_fts.md §3.2（Q1 定案）。
"""

from __future__ import annotations

import re

# 与 index.py 的 CJK_RE 等价（转义写法，避免同形异码字符）
CJK_RE = re.compile("[㐀-䶿一-鿿豈-﫿]")
STOPWORDS = {"的", "了", "吗", "呢", "是", "在", "啊", "吧", "哦", "嗯"}

# 查询串分隔符（与 index.build_fts_query 同一集合）；query_terms 供 snippet 定位复用
_SPLIT_RE = re.compile(r"[\s，。！？、；：\"'（）【】《》…\-–—·]+")


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


def query_terms(raw: str) -> list[str]:
    """查询串 → 词列表（分隔符同 index.build_fts_query）；供 `_snippet` 多词定位复用。"""
    return [w for w in _SPLIT_RE.split(raw) if w]


def build_fts_query(raw: str) -> str:
    """查询串：词一律双引号包裹（CJK 连续串先 bigram 预切）——零 FTS5 语法歧义。

    加引号是**安全要求**（2026-09-21 修复，docs/bff_search_fts.md §10.3）：非 CJK 词原样
    透传时，ASCII 标点与大写关键字被 FTS5 当语法解析并抛 sqlite3.OperationalError（实测
    `recording_data_process.md` / `What's your name?` / `C++` / `a:b` / `*abc`）。代价
    （已接受）：放弃 FTS5 原生通配与布尔语义（`abc*` 前缀查询、`a AND b`）——本端与 MCP
    `search()` 的查询都是自然文本，无此需求。全标点词加引号后是空短语（`"..."`）→ 0 命中，
    不再报错。
    """
    return " ".join(f'"{cjk_bigram(w)}"' for w in query_terms(raw))


def is_stopword_query(raw: str) -> bool:
    cleaned = re.sub(r"[\s，。！？、；：\"'（）【】《》…]+", "", raw)
    return not cleaned or cleaned in STOPWORDS


def relaxed_terms(raw: str) -> list[str]:
    """放宽检索词集（docs/bff_search_fts.md §10.1）：CJK 连续串 → 重叠 bigram；ASCII → 整词；去重。

    **丢弃 len<2 的 token** 是刻意的——「A股行情」拆出的 `A` 会命中一切含 A 的文本（噪声）。
    """
    out: list[str] = []
    for word in query_terms(raw):
        if CJK_RE.search(word):
            out.extend(cjk_bigram(word).split())
        else:
            out.append(word)
    return [t for t in dict.fromkeys(out) if len(t) >= 2]


def build_relaxed_query(terms: list[str]) -> str:
    """放宽查询串：词一律加引号后 OR 连接（空集 → ""）。

    加引号除规避语法歧义外，顺带免疫 `AND`/`OR`/`NOT` 等大写 FTS 关键字（按字面 token 匹配）。
    """
    return " OR ".join(f'"{t}"' for t in terms if t)
