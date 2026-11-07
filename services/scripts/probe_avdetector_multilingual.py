#!/usr/bin/env python3
"""17 语种 avdetector 真实探针（docs/server_multilingual.md §6.2）。

用生产 provider/prompt/parser 跑同一组合成非语音刺激，检查：
  1. event 是否始终为 normal/danger（不随目标语言漂移）；
  2. desc 是否为目标语言；
  3. max_tokens=256 下是否截断 / JSON 是否可解析；
  4. 每个语种的延迟与 usage。

运行（仓库根）：
    cd services
    ../.venv/bin/python scripts/probe_avdetector_multilingual.py
产物：/tmp/avdetector_multilingual_probe.json
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import random
import re
import struct
import sys
import time
from pathlib import Path

import structlog

_SERVICES = Path(__file__).resolve().parents[1]
_ROOT = _SERVICES.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from avdetector.providers.alibaba_avdetector import AlibabaAvDetectorProvider  # noqa: E402
from session.av_detector import _parse_reply  # noqa: E402
from session.proactive_i18n import (  # noqa: E402
    LANGUAGE_NAMES,
    SUPPORTED_PROACTIVE_LANGUAGES,
    build_desc_language_clause,
)
from shared.config.loader import load_config  # noqa: E402

# 只保留探针结果表；provider 的逐消息 debug 日志会淹没 17 行结果
logging.getLogger("livachat.avdetector.alibaba.messages").setLevel(logging.WARNING)
logging.getLogger().setLevel(logging.WARNING)
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

SR = 16000
OUT_DEFAULT = Path("/tmp/avdetector_multilingual_probe.json")

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_ARABIC = re.compile(r"[؀-ۿ]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_THAI = re.compile(r"[฀-๿]")
_LATIN = re.compile(r"[A-Za-z]")


def _synth_cough(seconds: float = 0.55) -> bytes:
    random.seed(7)
    n = int(SR * seconds)
    samples = [0.0] * n
    for start, dur, amp in ((0.00, 0.22, 0.9), (0.26, 0.18, 0.6)):
        s0 = int(start * SR)
        for i in range(int(dur * SR)):
            if s0 + i >= n:
                break
            t = i / SR
            env = math.exp(-14 * t) if t < 0.03 else math.exp(-14 * t) * 0.7
            v = (random.uniform(-1, 1) * 0.7 + math.sin(2 * math.pi * 140 * t) * 0.3) * env * amp
            samples[s0 + i] = v
    return struct.pack(f"<{n}h", *(int(max(-1, min(1, v)) * 32767) for v in samples))


def _synth_knock(seconds: float = 0.5) -> bytes:
    random.seed(11)
    n = int(SR * seconds)
    samples = [0.0] * n
    for start in (0.0, 0.18, 0.36):
        s0 = int(start * SR)
        for i in range(int(0.06 * SR)):
            if s0 + i >= n:
                break
            t = i / SR
            env = math.exp(-45 * t)
            v = (random.uniform(-1, 1) * 0.5 + math.sin(2 * math.pi * 90 * t) * 0.5) * env * 0.9
            samples[s0 + i] = v
    return struct.pack(f"<{n}h", *(int(max(-1, min(1, v)) * 32767) for v in samples))


def _synth_whistle(seconds: float = 0.6) -> bytes:
    n = int(SR * seconds)
    out: list[float] = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        f = 2000 + 1500 * t / seconds
        phase += 2 * math.pi * f / SR
        env = min(1.0, t / 0.05, (seconds - t) / 0.05)
        out.append(math.sin(phase) * 0.5 * max(0.0, env))
    return struct.pack(f"<{n}h", *(int(v * 32767) for v in out))


_STIMULI = (
    ("cough", _synth_cough()),
    ("knock", _synth_knock()),
    ("whistle", _synth_whistle()),
)


async def _feed(stream, pcm: bytes) -> None:
    step = 1280  # 40ms @16k
    for off in range(0, len(pcm), step):
        await stream.feed_audio(pcm[off:off + step])
        await asyncio.sleep(0.012)


def _script_ok(lang: str, desc: str) -> bool:
    if not desc:
        return False
    if lang == "en":
        return bool(_LATIN.search(desc)) and not _HAN.search(desc)
    if lang in ("zh-Hans", "zh-Hant"):
        return bool(_HAN.search(desc))
    if lang == "ja":
        return bool(_HAN.search(desc) or _KANA.search(desc))
    if lang == "ko":
        return bool(_HANGUL.search(desc))
    if lang == "ru":
        return bool(_CYRILLIC.search(desc))
    if lang == "ar":
        return bool(_ARABIC.search(desc))
    if lang == "hi":
        return bool(_DEVANAGARI.search(desc))
    if lang == "th":
        return bool(_THAI.search(desc))
    # 其余拉丁字母语言（de/fr/it/es/pt/tr/vi/id）只做 script 检查；
    # 是否真是目标语言需看报告 desc 人工复核。
    return bool(_LATIN.search(desc)) and not any(
        p.search(desc) for p in (_HAN, _KANA, _HANGUL, _CYRILLIC, _ARABIC, _DEVANAGARI, _THAI)
    )


async def _probe_language(
    provider: AlibabaAvDetectorProvider,
    *,
    base_prompt: str,
    lang: str,
    gen_opts: dict,
    timeout_s: float,
    attempts: int,
) -> dict:
    clause = build_desc_language_clause(lang)
    prompt = f"{base_prompt}\n\n{clause}" if clause else base_prompt
    result: dict = {
        "language": lang,
        "language_name": LANGUAGE_NAMES.get(lang, lang),
        "prompt_has_clause": bool(clause),
        "attempts": [],
    }
    try:
        stream = await provider.open(
            sample_rate=SR, system_prompt=prompt, generation_opts=gen_opts,
        )
    except Exception as exc:  # noqa: BLE001 - probe records, does not abort
        result["attempts"].append({
            "stimulus": "open", "error": f"{type(exc).__name__}: {exc}",
        })
        result.update({"event": "ERROR", "desc": "", "script_ok": False})
        return result
    try:
        for name, pcm in _STIMULI[:max(1, attempts)]:
            await _feed(stream, pcm)
            await asyncio.sleep(0.2)
            t0 = time.monotonic()
            try:
                check = await stream.check(timeout_s=timeout_s)
            except Exception as exc:  # noqa: BLE001 - probe records, does not raise
                result["attempts"].append({
                    "stimulus": name, "error": f"{type(exc).__name__}: {exc}",
                    "latency_s": round(time.monotonic() - t0, 3),
                })
                break
            parsed = _parse_reply(check.text)
            attempt = {
                "stimulus": name,
                "latency_s": round(time.monotonic() - t0, 3),
                "raw": check.text,
                "event": parsed["event"] if parsed else None,
                "desc": parsed["desc"] if parsed else "",
                "transcripts": check.transcripts,
                "usage": check.usage,
            }
            result["attempts"].append(attempt)
            if parsed is not None:
                result.update({
                    "event": parsed["event"],
                    "desc": parsed["desc"],
                    "script_ok": _script_ok(lang, parsed["desc"]),
                })
                break
    finally:
        with contextlib.suppress(Exception):
            await stream.close()

    if "event" not in result:
        last = result["attempts"][-1] if result["attempts"] else {}
        result["event"] = "NONE" if last.get("raw", "").strip().upper() == "NONE" else "INVALID"
        result["desc"] = ""
        result["script_ok"] = False
    return result


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-env", default="dev")
    parser.add_argument("--languages", default=",".join(sorted(SUPPORTED_PROACTIVE_LANGUAGES)))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()

    cfg = load_config(args.config_env)
    av = cfg.avdetector
    provider_cfg = av.providers[av.active]
    provider = AlibabaAvDetectorProvider(
        base_url=provider_cfg.base_url,
        api_key=provider_cfg.api_key,
        model=provider_cfg.model,
        workspace_id=provider_cfg.workspace_id,
    )
    base_path = _ROOT / av.system_prompt_path
    base_prompt = base_path.read_text(encoding="utf-8").strip()
    gen_opts = av.generation.model_dump()
    languages = [x.strip() for x in args.languages.split(",") if x.strip()]
    unknown = set(languages) - set(SUPPORTED_PROACTIVE_LANGUAGES)
    if unknown:
        print(f"unknown languages: {sorted(unknown)}", file=sys.stderr)
        return 2

    report = {
        "config_env": args.config_env,
        "model": provider_cfg.model,
        "max_tokens": gen_opts.get("max_tokens"),
        "timeout_s": args.timeout,
        "languages": [],
    }
    print(f"model={provider_cfg.model} max_tokens={gen_opts.get('max_tokens')}")
    print(f"{'lang':<8} {'event':<8} {'script':<7} {'lat':>6}  desc")
    for lang in languages:
        r = await _probe_language(
            provider, base_prompt=base_prompt, lang=lang, gen_opts=gen_opts,
            timeout_s=args.timeout, attempts=args.attempts,
        )
        report["languages"].append(r)
        lat = r.get("attempts", [{}])[-1].get("latency_s", 0.0) if r.get("attempts") else 0.0
        print(f"{lang:<8} {r['event']:<8} {str(r['script_ok']):<7} {lat:>6.2f}  {r['desc']}")
        if r["event"] in ("ERROR", "INVALID"):
            print(f"  attempts: {json.dumps(r['attempts'], ensure_ascii=False)[:1000]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    parsed = sum(1 for r in report["languages"] if r["event"] in ("normal", "danger"))
    print(
        f"\nparsed={parsed}/{len(languages)} "
        f"normal={sum(1 for r in report['languages'] if r['event'] == 'normal')} "
        f"danger={sum(1 for r in report['languages'] if r['event'] == 'danger')} "
        f"none={sum(1 for r in report['languages'] if r['event'] == 'NONE')} "
        f"invalid={sum(1 for r in report['languages'] if r['event'] == 'INVALID')} "
        f"script_ok={sum(1 for r in report['languages'] if r['script_ok'])}"
    )
    print(f"report: {args.out}")
    return 0 if parsed == len(languages) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
