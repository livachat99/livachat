#!/usr/bin/env python3
"""chat mode 输出语言真实 LLM 探针（docs/server_multilingual.md §6.4）。

覆盖：中性短句回落 session 语言、用户提问语言优先、显式语言要求优先、
历史语言不影响当前回合。只读探针，不启动 server。

运行（services 目录）：
    ../.venv/bin/python scripts/probe_llm_output_language.py
产物：/tmp/llm_output_language_probe.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

_SERVICES = Path(__file__).resolve().parents[1]
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from agent_relay.adapters.loader import load_all  # noqa: E402
from agent_relay.config import AgentRelayConfig  # noqa: E402
from app.application import (  # noqa: E402
    _named_background_agents,
    _with_agent_awareness,
    _with_agent_job_declaration,
)
from llm.base import ChatMessage  # noqa: E402
from llm.factory import create_llm  # noqa: E402
from llm.prompt import load_system_prompt  # noqa: E402
from session.proactive_i18n import build_output_language_rule  # noqa: E402
from shared.config.loader import load_config  # noqa: E402

logging.getLogger().setLevel(logging.WARNING)

OUT_DEFAULT = Path("/tmp/llm_output_language_probe.json")

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_ARABIC = re.compile(r"[؀-ۿ]")
_THAI = re.compile(r"[฀-๿]")
_LATIN = re.compile(r"[A-Za-z]")


def _script_ok(lang: str, text: str) -> bool:
    if not text:
        return False
    if lang == "en":
        return bool(_LATIN.search(text)) and not _HAN.search(text)
    if lang in ("zh-Hans", "zh-Hant"):
        return bool(_HAN.search(text))
    if lang == "ja":
        return bool(_HAN.search(text) or _KANA.search(text))
    if lang == "ar":
        return bool(_ARABIC.search(text))
    if lang == "th":
        return bool(_THAI.search(text))
    if lang == "vi":
        return bool(_LATIN.search(text)) and not _HAN.search(text)
    return False


def _system_prompt(cfg) -> str:
    base = load_system_prompt(cfg.llm.system_prompt_path)
    base = _with_agent_job_declaration(base)
    if cfg.agent_relay and cfg.agent_relay.get("enabled"):
        relay_cfg = AgentRelayConfig.model_validate(cfg.agent_relay)
        adapters = load_all(relay_cfg)
        if adapters:
            base = _with_agent_awareness(base, _named_background_agents(adapters))
    return base


async def _ask(llm, messages: list[ChatMessage], gen_opts: dict, timeout_s: float) -> str:
    async def _collect() -> str:
        chunks: list[str] = []
        async for token in llm.stream(messages=messages, **gen_opts):
            chunks.append(token)
        return "".join(chunks)

    try:
        return (await asyncio.wait_for(_collect(), timeout=timeout_s)).strip()
    except Exception as exc:  # noqa: BLE001 - probe records, does not raise
        return f"ERROR: {type(exc).__name__}: {exc}"


def _scenarios() -> list[dict]:
    scenarios: list[dict] = []
    neutral = "ok"
    for lang in ("en", "zh-Hans", "zh-Hant", "ja", "ar", "th", "vi"):
        scenarios.append({
            "name": f"neutral_fallback_{lang}",
            "session": lang,
            "user": neutral,
            "history": [],
            "expected": lang,
        })
    scenarios += [
        {
            "name": "question_language_overrides_session_en",
            "session": "en",
            "user": "明天北京天气怎么样？",
            "history": [],
            "expected": "zh",
        },
        {
            "name": "question_language_overrides_session_zh",
            "session": "zh-Hans",
            "user": "What's the weather in Beijing tomorrow?",
            "history": [],
            "expected": "en",
        },
        {
            "name": "explicit_language_request_wins",
            "session": "zh-Hans",
            "user": "请用英文回答：今天适合出门吗？",
            "history": [],
            "expected": "en",
        },
        {
            "name": "history_language_ignored",
            "session": "en",
            "user": "今天适合出门吗？",
            "history": [
                {"role": "user", "content": "What's the weather like today?"},
                {"role": "assistant", "content": "It's sunny and warm today."},
            ],
            "expected": "zh",
        },
        {
            "name": "unknown_session_neutral_defaults_en",
            "session": "",
            "user": neutral,
            "history": [],
            "expected": "en",
        },
    ]
    return scenarios


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-env", default="dev")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()

    cfg = load_config(args.config_env)
    llm = create_llm(cfg.llm)
    gen_opts = cfg.llm.generation.model_dump()
    base_system = _system_prompt(cfg)

    report = {
        "provider": cfg.llm.active,
        "model": cfg.llm.providers[cfg.llm.active].model,
        "gen_opts": gen_opts,
        "scenarios": [],
    }
    for sc in _scenarios():
        system = base_system.rstrip() + "\n\n" + build_output_language_rule(sc["session"])
        messages = [ChatMessage(role="system", content=system)]
        messages += [ChatMessage(role=h["role"], content=h["content"]) for h in sc["history"]]
        messages.append(ChatMessage(role="user", content=sc["user"]))
        actual = await _ask(llm, messages, gen_opts, args.timeout)
        expected_script = "zh-Hans" if sc["expected"].startswith("zh") else sc["expected"]
        row = {
            **sc,
            "actual": actual,
            "script_ok": _script_ok(expected_script, actual),
        }
        report["scenarios"].append(row)
        print(f"{sc['name']:<42} expected={sc['expected']:<7} "
              f"script={row['script_ok']} text={actual[:100]!r}")

    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
