"""day_summary topics 归一化（§13 / §10）：对象 {topic, search_words} / legacy str 数组。

单点收敛：timeline 日卡 / search 命中 / graph 节点三处消费共用，防形态漂移。
"""

from __future__ import annotations


def topic_items(topics: list | None) -> list[tuple[str, str | None]]:
    """[{topic, search_words}] 归一化为 (主题名, 搜索词)；legacy str → 搜索词 None。"""
    out: list[tuple[str, str | None]] = []
    for t in topics or []:
        if isinstance(t, str) and t.strip():
            out.append((t.strip(), None))
        elif isinstance(t, dict):
            name = str(t.get("topic") or "").strip()
            if not name:
                continue
            sw = t.get("search_words")
            out.append((name, sw.strip() if isinstance(sw, str) and sw.strip() else None))
    return out


def topic_names(topics: list | None) -> list[str]:
    return [name for name, _ in topic_items(topics)]
