"""CLI 用户可见文案统一英文（Phase 1 + Phase 2）。

- argparse 帮助（根命令与全部子命令）
- reset plan/summary 文本
- CLI 相关模块的非 docstring 字符串常量
- 包内所有 structlog 调用的消息字面量
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from lvdata_mcp.main import _parse_args
from lvdata_mcp.reset import format_plan, format_summary

CJK = re.compile(r"[㐀-鿿]")
PKG = Path(__file__).resolve().parents[1] / "src" / "lvdata_mcp"
LOG_METHODS = {"info", "warning", "error", "debug", "exception", "critical"}


def _non_docstring_strings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(node.body[0].value, ast.Constant) \
                    and isinstance(node.body[0].value.value, str):
                docstrings.add(id(node.body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            yield node.lineno, node.value


@pytest.mark.parametrize("argv", [
    ["--help"],
    ["serve", "--help"],
    ["reindex", "--help"],
    ["eval", "--help"],
    ["stage", "--help"],
    ["reset", "--help"],
])
def test_cli_help_is_english(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        _parse_args(argv)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip()
    assert not CJK.search(out), out


def test_reset_plan_and_summary_are_english():
    plan = {
        "date": "2026-09-28",
        "timezone": "Asia/Shanghai",
        "date_from": "2026-09-28",
        "date_to": "2026-09-28",
        "sessions": [{"rsession_id": "sid", "session_date": "2026-09-28",
                      "reasons": ["primary"]}],
        "files_in_scope": {"audio": 1, "video": 2, "chat": 3, "meta": 4},
        "out_of_range": {"files": 0, "events": 0},
        "delete_candidates": {"audio": 1},
        "delete_bytes": 123,
        "events_to_purge": {"chat": 1, "window": 0},
        "window_events": {"matched": 1, "after_zeroing": 0},
        "unknown_event_types": {},
        "fts_docs": 1,
        "vector_refs": 2,
        "day_summary": {"active_rows": 1, "inactive_rows": 0},
        "reset_files": {"audio": 1, "chat": 1},
        "missing_source_events": 0,
        "warnings": ["warning"],
    }
    result = {
        "sessions": 1, "sessions_failed": [], "files_deleted": 1,
        "bytes_deleted": 123, "files_unlinked": 1, "bytes_unlinked": 123,
        "failed_unlinks": 0, "events_deleted": 1,
        "window_events_deleted": 0, "unknown_kept": 0,
        "fts_rebuilt_docs": 1, "index_state_deleted": 1,
        "day_summary_reset": 1, "day_summary_deleted": 0,
        "day_stats_recomputed": 1, "derived_deleted": 1,
        "vectors_removed": 1, "vectors_swept": 0,
        "vectors_pending": False,
    }
    assert not CJK.search(format_plan(plan))
    assert not CJK.search(format_summary(result))


@pytest.mark.parametrize("rel", [
    "main.py", "reset.py", "pipeline.py", "recording_watch.py",
    "analysis/chatlog.py", "remove.py",
])
def test_cli_source_strings_are_english(rel):
    for line, text in _non_docstring_strings(PKG / rel):
        assert not CJK.search(text), f"{rel}:{line}: {text!r}"


def test_log_message_literals_are_english():
    """只约束日志消息（event）本身；字段值里的中文数据不在此列。"""
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in LOG_METHODS):
                continue
            message = node.args[0] if node.args else None
            if message is None:
                message = next(
                    (kw.value for kw in node.keywords
                     if kw.arg in ("event", "msg", "message")), None)
            if message is None:
                continue
            for sub in ast.walk(message):
                if isinstance(sub, ast.Constant) \
                        and isinstance(sub.value, str) \
                        and CJK.search(sub.value):
                    raise AssertionError(
                        f"{path.relative_to(PKG)}:{sub.lineno}: "
                        f"{sub.value!r}")
