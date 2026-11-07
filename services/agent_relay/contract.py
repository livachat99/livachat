"""Shared nudge-contract parser for proactive adapters (hermes / openclaw).

The contract is relay-defined (see docs/agent_relay_prompts.md and the
mission/nudge templates): the monitor agent must answer with a single JSON
object — {"action": "noop"} | {"action": "message", ...} |
{"action": "job_request", ...}. Both adapters parse the same wire contract,
so the extraction + repair logic lives here (moved out of HermesAdapter,
which keeps thin delegating wrappers for its existing tests).
"""

from __future__ import annotations

import json
from typing import Any

# 合法闭合引号的后续字符（修复值内裸引号时用于区分闭合与裸引号）
_STRING_END_OK = frozenset(',}] \t\r\n:')


def repair_unescaped_quotes(s: str) -> str:
    """修复字符串值内未转义的英文双引号。

    状态机扫描：字符串内未被 \\ 转义的 " 若后随 , } ] : 空白或串尾，
    视为合法闭合引号；否则为值内裸引号，补反斜杠转义。JSON 字符串值
    内未转义的 " 必然非法，修复是安全的。
    """
    out: list[str] = []
    in_str = False
    escaped = False
    for i, c in enumerate(s):
        if in_str:
            if escaped:
                out.append(c)
                escaped = False
                continue
            if c == "\\":
                out.append(c)
                escaped = True
                continue
            if c == '"':
                nxt = s[i + 1] if i + 1 < len(s) else ""
                if nxt in _STRING_END_OK:
                    out.append(c)
                    in_str = False
                    continue
                out.append("\\")
            out.append(c)
            continue
        if c == '"':
            in_str = True
        out.append(c)
    return "".join(out)


def parse_contract(text: str) -> dict[str, Any] | None:
    """Extract the first complete JSON object from a model reply.

    Trims, strips a markdown code fence, then brace-matches from the
    first '{'. Returns None on any parse failure.
    """
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = t[start : i + 1]
                try:
                    obj = json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    # 模型偶发契约违例：字符串值内未转义英文双引号 → 修复后
                    # 重试一次（2026-08-28 事故；prompt 加固后仍偶发，解析层兜底）
                    repaired = repair_unescaped_quotes(candidate)
                    if repaired == candidate:
                        return None
                    try:
                        obj = json.loads(repaired)
                    except (json.JSONDecodeError, ValueError):
                        return None
                return obj if isinstance(obj, dict) else None
    return None
