#!/usr/bin/env python3
"""Agent relay 多语言真实探针（docs/agent_multilingual.md §5.2）。

用生产 adapter（Hermes/OpenClaw）+ 真实 gateway 验证 language_hint 是否让
agent 面向用户的输出使用目标语言：

  - monitor：nudge 契约里的 message.text / job_request.summary / spec.task
  - reactive ask：mode=both 的实时回复
  - job task：后台任务书最终 text
  - 显式用户语言要求优先（基线 zh-Hans + 让用户说英文）
  - 语言变更（同一 device 会话 zh-Hans → en，Hermes 触发 mission restate）

运行（services 目录）：
    ../.venv/bin/python scripts/probe_agent_multilingual.py

产物：/tmp/agent_multilingual_probe.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import sys
import time
from pathlib import Path

_SERVICES = Path(__file__).resolve().parents[1]
_ROOT = _SERVICES.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from agent_relay.adapters.base import AgentContext, ChatlogLine, ChatlogUpdate  # noqa: E402
from agent_relay.adapters.loader import load_adapter  # noqa: E402
from agent_relay.contract import parse_contract  # noqa: E402
from agent_relay.prompt_hints import language_hint  # noqa: E402
from shared.config.loader import load_config  # noqa: E402

logging.getLogger().setLevel(logging.WARNING)

OUT_DEFAULT = Path("/tmp/agent_multilingual_probe.json")
SUPPORTED = ("en", "zh-Hant", "ja", "ar", "th", "vi")

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_ARABIC = re.compile(r"[؀-ۿ]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_THAI = re.compile(r"[฀-๿]")
_LATIN = re.compile(r"[A-Za-z]")


def script_ok(lang: str, text: str) -> bool:
    """脚本级检查（vi 等拉丁语言需人工复核；zh-Hans/Hant 无法由脚本区分）。"""
    if not text:
        return False
    if lang == "en":
        return bool(_LATIN.search(text)) and not _HAN.search(text)
    if lang in ("zh-Hans", "zh-Hant"):
        return bool(_HAN.search(text))
    if lang == "ja":
        return bool(_HAN.search(text) or _KANA.search(text))
    if lang == "ko":
        return bool(_HANGUL.search(text))
    if lang == "ru":
        return bool(_CYRILLIC.search(text))
    if lang == "ar":
        return bool(_ARABIC.search(text))
    if lang == "hi":
        return bool(_DEVANAGARI.search(text))
    if lang == "th":
        return bool(_THAI.search(text))
    return bool(_LATIN.search(text)) and not any(
        p.search(text) for p in (_HAN, _KANA, _HANGUL, _CYRILLIC, _ARABIC, _DEVANAGARI, _THAI)
    )


def _ctx(device_id: str, language: str) -> AgentContext:
    return AgentContext(
        session_id=f"probe-sess-{int(time.time() * 1000)}",
        device_id=device_id,
        language=language,
        timezone="Asia/Shanghai",
    )


def _update(ctx: AgentContext, text: str) -> ChatlogUpdate:
    return ChatlogUpdate(
        path="/tmp/probe_chatlog.jsonl",
        chat_session_id=ctx.session_id,
        new_lines=[ChatlogLine(role="user", text=text, ts_ms=int(time.time() * 1000))],
    )


async def _hermes_monitor(adapter, ctx: AgentContext, user_text: str, timeout_s: float) -> dict:
    """跑一轮 monitor 契约（不派发 job，避免真实后台任务）。"""
    await adapter.on_session_start(ctx)
    conn = adapter._devices[ctx.device_id]
    # 与 HermesAdapter.nudge() 同构：每轮 compact hint + 数据 JSON
    prompt = language_hint(ctx.language, verbose=False) + "\n\n" + json.dumps(
        adapter._build_nudge_prompt(_update(ctx, user_text), ctx.timezone),
        ensure_ascii=False,
    )
    reply = await adapter._submit_turn(conn, prompt, timeout_s=timeout_s)
    return _contract_result(reply)


def _contract_result(reply: str | None) -> dict:
    if not reply:
        return {"action": "TIMEOUT", "raw": ""}
    contract = parse_contract(reply)
    if not isinstance(contract, dict):
        return {"action": "PARSE_ERROR", "raw": reply[:500]}
    result = {"action": contract.get("action"), "raw": reply[:2000]}
    if contract.get("action") == "message":
        result["text"] = (contract.get("text") or "").strip()
    elif contract.get("action") == "job_request":
        result["summary"] = (contract.get("summary") or "").strip()
        spec = contract.get("spec") or {}
        result["task"] = str(spec.get("task") or "").strip()
    return result


async def _openclaw_monitor(adapter, ctx: AgentContext, user_text: str, timeout_s: float) -> dict:
    """跑一轮 nudge 契约（不经 nudge() 派发，避免创建真实 job）。"""
    from agent_relay.adapters.openclaw import _LISTENING_PREFIX

    conn = await adapter._ensure_ready()
    session = await adapter._ensure_session(
        conn, ("dev", ctx.device_id), adapter._session_key(_LISTENING_PREFIX, ctx.device_id)
    )
    prompt = adapter._render_nudge(
        _update(ctx, user_text), ctx.timezone, language=ctx.language,
    )
    run_id = f"livachat:probe:{ctx.device_id}:{int(time.time() * 1000)}"
    future = await adapter._send_turn(conn, session, prompt, run_id=run_id)
    reply = await adapter._wait_run(conn, future, timeout_s=timeout_s)
    return _contract_result(reply)


async def _monitor(adapter, ctx: AgentContext, user_text: str, timeout_s: float) -> dict:
    if adapter.name == "hermes":
        return await _hermes_monitor(adapter, ctx, user_text, timeout_s)
    return await _openclaw_monitor(adapter, ctx, user_text, timeout_s)


async def _ask(adapter, ctx: AgentContext, text: str, timeout_s: float) -> dict:
    chunks = [c.text async for c in adapter.ask(text, ctx)]
    answer = "".join(chunks).strip()
    return {"action": "answer", "text": answer}


async def _job_task_direct(adapter, ctx: AgentContext, task: str, timeout_s: float) -> dict:
    """用真实后台任务书模板 + reactive ask 验证最终 text 语言。"""
    rendered = adapter._render_job_task(
        task, ctx.session_id, "", timezone=ctx.timezone, language=ctx.language,
    ) if adapter.name == "hermes" else adapter._render_background_task(
        task, ctx.session_id, "", timezone=ctx.timezone, language=ctx.language,
    )
    chunks = [c.text async for c in adapter.ask(rendered, ctx)]
    return {"action": "job_final", "text": "".join(chunks).strip()}


def _checks(lang: str, result: dict) -> dict:
    checks = {}
    for field in ("text", "summary", "task"):
        if field in result:
            checks[field] = script_ok(lang, result[field])
    return checks


async def _probe_language(adapter, lang: str, timeout_s: float) -> dict:
    device = f"probe-{adapter.name}-{lang}-{int(time.time() * 1000)}"
    row: dict = {"language": lang, "results": {}}

    monitor_ctx = _ctx(device, lang)
    monitor = await _monitor(
        adapter, monitor_ctx, "帮我查一下明天北京的天气，给个简短建议。", timeout_s,
    )
    monitor["checks"] = _checks(lang, monitor)
    row["results"]["monitor"] = monitor

    ask_ctx = _ctx(device, lang)
    ask = await _ask(adapter, ask_ctx, "你好，用一句话介绍你自己。", timeout_s)
    ask["checks"] = _checks(lang, ask)
    row["results"]["ask"] = ask

    if lang in ("en", "ja", "ar"):
        task_ctx = _ctx(device, lang)
        task = await _job_task_direct(
            adapter, task_ctx, "告诉用户今天的日期。只需一句话，不要调用任何工具。", timeout_s,
        )
        task["checks"] = _checks(lang, task)
        row["results"]["job_task"] = task

    return row


async def _probe_override(adapter, timeout_s: float) -> dict:
    """基线 zh-Hans + 用户明确要求英文 → 用户可见字段用英文。"""
    device = f"probe-{adapter.name}-override-{int(time.time() * 1000)}"
    ctx = _ctx(device, "zh-Hans")
    result = await _monitor(
        adapter, ctx,
        "帮我查一下明天北京的天气，并且请用英文汇报结果。",
        timeout_s,
    )
    result["checks"] = _checks("en", result)
    return result


async def _probe_language_change(adapter, timeout_s: float) -> dict:
    """同一 device：zh-Hans monitor → en session.started → 后续 nudge 用英文。"""
    device = f"probe-{adapter.name}-change-{int(time.time() * 1000)}"
    first_ctx = _ctx(device, "zh-Hans")
    first = await _monitor(
        adapter, first_ctx, "帮我查一下明天北京的天气，给个简短建议。", timeout_s,
    )
    second_ctx = _ctx(device, "en")
    second_ctx.session_id = first_ctx.session_id   # 同一会话内 App 语言变化
    second = await _monitor(
        adapter, second_ctx, "再帮我查一下后天上海的天气，给个简短建议。", timeout_s,
    )
    return {
        "zh-Hans": {**first, "checks": _checks("zh-Hans", first)},
        "en_after_change": {**second, "checks": _checks("en", second)},
    }


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-env", default="dev")
    parser.add_argument("--adapter", default="hermes", choices=("hermes", "openclaw"))
    parser.add_argument("--languages", default=",".join(SUPPORTED))
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--skip-extras", action="store_true",
                        help="跳过 override / language_change 探针")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()

    languages = [x.strip() for x in args.languages.split(",") if x.strip()]
    unknown = set(languages) - set(SUPPORTED)
    if unknown:
        print(f"unknown languages: {sorted(unknown)}", file=sys.stderr)
        return 2

    cfg = load_config(args.config_env)
    from agent_relay.config import AgentRelayConfig

    relay_cfg = AgentRelayConfig.model_validate(cfg.agent_relay or {})
    adapter = load_adapter(args.adapter, relay_cfg)
    # 探针要覆盖 reactive ask，即使生产配置是 mode=proactive
    adapter.reactive_enabled = True
    if cfg.webroot.enabled:
        from app.application import resolve_webroot_dir

        adapter.webroot_dir = resolve_webroot_dir(cfg)
    await adapter.connect()

    report = {
        "adapter": args.adapter,
        "timeout_s": args.timeout,
        "languages": [],
        "override": None,
        "language_change": None,
    }
    try:
        for lang in languages:
            row = await _probe_language(adapter, lang, args.timeout)
            report["languages"].append(row)
            print(
                f"{lang:<8} monitor={row['results']['monitor'].get('action')} "
                f"checks={row['results']['monitor'].get('checks')} "
                f"ask_checks={row['results']['ask'].get('checks')}"
            )
        if not args.skip_extras:
            report["override"] = await _probe_override(adapter, args.timeout)
            print(f"override checks={report['override'].get('checks')}")
            report["language_change"] = await _probe_language_change(adapter, args.timeout)
            print(
                f"language_change checks: "
                f"zh-Hans={report['language_change']['zh-Hans'].get('checks')} "
                f"en={report['language_change']['en_after_change'].get('checks')}"
            )
    finally:
        with contextlib.suppress(Exception):
            await adapter.close()

    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
