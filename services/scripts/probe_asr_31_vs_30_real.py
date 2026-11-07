#!/usr/bin/env python3
"""Probe: qwen-audio-3.1-asr-flash-streaming vs 3.0 — protocol, latency, billing.

Raw WebSocket (not via provider) so every server event — including the
``usage`` block (input/output tokens for 3.1, duration seconds for 3.0) — is
captured verbatim.  Feeds a wav at realtime pace (20 ms frames) followed by
trailing silence; measures "last speech frame sent → post-speech final
arrives"; dumps per-run raw events to JSONL for audit.

Usage::

    cd services
    .venv/bin/python scripts/probe_asr_31_vs_30_real.py /tmp/asr_probe/speech_16k.wav \\
      --base-url "wss://${DASHSCOPE_API_HOST}/api-ws/v1/inference" \\
      --workspace-id "$DASHSCOPE_WORKSPACE_ID" --repeats 3

    # single case
    .venv/bin/python scripts/probe_asr_31_vs_30_real.py ... --only 3.1-vad-near

Artifacts: /tmp/asr_probe/raw_<case>_<n>.jsonl + summary printed to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import struct
import sys
import time
import uuid
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path

import websockets

_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from media.resample import Resampler

FRAME_MS = 20
ARTIFACT_DIR = Path("/tmp/asr_probe")

# 生产同款 base params（configs/dev/server.yaml asr 段），保证可比性
BASE_PARAMS: dict[str, object] = {"heartbeat": True, "language_hints": ["zh", "en"]}

# case 名 → (model, params)。3.0-prod 复刻现网配置；3.1 各 case 探测参数接受度
CASES: dict[str, tuple[str, dict[str, object]]] = {
    "3.0-prod": ("qwen-audio-3.0-asr-flash-streaming",
                 {**BASE_PARAMS, "max_sentence_silence": 600}),
    "3.0-default": ("qwen-audio-3.0-asr-flash-streaming", dict(BASE_PARAMS)),
    "3.1-prod-params": ("qwen-audio-3.1-asr-flash-streaming",
                        {**BASE_PARAMS, "max_sentence_silence": 600}),
    "3.1-vad-near": ("qwen-audio-3.1-asr-flash-streaming",
                     {**BASE_PARAMS, "vad_model": "near_meeting_16k"}),
    "3.1-vad-far": ("qwen-audio-3.1-asr-flash-streaming",
                    {**BASE_PARAMS, "vad_model": "far_field_meeting_16k"}),
    "3.1-keep-dialect": ("qwen-audio-3.1-asr-flash-streaming",
                         {**BASE_PARAMS, "keep_dialect": True}),
    # 组合调优：near VAD 是否可与 max_sentence_silence 叠加
    "3.1-near-mss600": ("qwen-audio-3.1-asr-flash-streaming",
                        {**BASE_PARAMS, "vad_model": "near_meeting_16k",
                         "max_sentence_silence": 600}),
    "3.1-near-mss400": ("qwen-audio-3.1-asr-flash-streaming",
                        {**BASE_PARAMS, "vad_model": "near_meeting_16k",
                         "max_sentence_silence": 400}),
    "3.1-near-mss800": ("qwen-audio-3.1-asr-flash-streaming",
                        {**BASE_PARAMS, "vad_model": "near_meeting_16k",
                         "max_sentence_silence": 800}),
    # 粤语对照：keep_dialect 关（默认行为）→ 方言转普通话
    "3.1-dialect-off": ("qwen-audio-3.1-asr-flash-streaming", dict(BASE_PARAMS)),
}

# 计费单价（官网 2026-10-10）：北京地域
PRICE_30_CNY_PER_S = 0.00033        # 音频时长每秒
PRICE_31_CNY_PER_M_IN = 6.0         # 输入 token 每百万
PRICE_31_CNY_PER_M_OUT = 4.5        # 输出 token 每百万


def load_env() -> None:
    """repo 根 .env 注入 os.environ（setdefault；与 shared/config/loader 同语义）。"""
    env_path = _SERVICES.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        os.environ.setdefault(key.strip(), value)


def load_wav(path: str, target_rate: int) -> bytes:
    with wave.open(path, "rb") as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        src_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        fmt = {1: "b", 2: "h", 4: "i"}[sampwidth]
        samples = struct.unpack(f"<{len(raw) // sampwidth}{fmt}", raw)
        if sampwidth == 1:
            samples = tuple((s - 128) << 8 for s in samples)
        elif sampwidth == 4:
            samples = tuple(s >> 16 for s in samples)
        raw = struct.pack(f"<{len(samples)}h", *samples)

    if nchannels > 1:
        mono = bytearray(len(raw) // nchannels)
        for i in range(0, len(raw), nchannels * 2):
            mono[i // nchannels : i // nchannels + 2] = raw[i : i + 2]
        raw = bytes(mono)

    if src_rate != target_rate:
        resampler = Resampler(src_rate, target_rate)
        raw = resampler.process(raw) + resampler.flush()

    return raw


def trim_trailing_silence(pcm: bytes, frame_bytes: int, threshold: int) -> bytes:
    frames = [pcm[i : i + frame_bytes] for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes)]
    last_speech = -1
    for idx, fr in enumerate(frames):
        samples = array("h")
        samples.frombytes(fr)
        if sys.byteorder == "big":
            samples.byteswap()
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        if rms >= threshold:
            last_speech = idx
    if last_speech < 0:
        return pcm
    return pcm[: (last_speech + 1) * frame_bytes]


def split_frames(pcm: bytes, frame_bytes: int) -> list[bytes]:
    frames = [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]
    if frames and len(frames[-1]) < frame_bytes:
        frames[-1] = frames[-1] + b"\x00" * (frame_bytes - len(frames[-1]))
    return frames


@dataclass
class Run:
    case: str
    model: str
    params: dict[str, object]
    connect_s: float | None = None
    task_started_ms: float | None = None
    task_failed: str | None = None
    error_events: list[str] = field(default_factory=list)
    interims: list[tuple[float, str]] = field(default_factory=list)   # (ms rel. speech end, text)
    finals: list[tuple[float, str]] = field(default_factory=list)
    latency_ms: float | None = None
    usage_last: dict | None = None                                    # 3.1: tokens; 3.0: duration
    usage_first: dict | None = None
    speech_ms: int = 0
    silence_fed_ms: int = 0


async def run_once(
    *,
    case: str,
    model: str,
    params: dict[str, object],
    base_url: str,
    api_key: str,
    workspace_id: str | None,
    pcm_speech: bytes,
    sample_rate: int,
    trailing_silence_ms: int,
    grace_ms: int,
    run_idx: int,
) -> Run:
    frame_bytes = int(sample_rate * FRAME_MS / 1000) * 2
    speech_frames = split_frames(pcm_speech, frame_bytes)
    silence = b"\x00" * frame_bytes

    run = Run(case=case, model=model, params=params)
    run.speech_ms = len(speech_frames) * FRAME_MS

    raw_path = ARTIFACT_DIR / f"raw_{case}_{run_idx}.jsonl"
    raw_f = raw_path.open("w")

    headers = {"Authorization": f"Bearer {api_key}"}
    if workspace_id:
        headers["X-DashScope-WorkSpace"] = workspace_id

    events: list[tuple[float, dict]] = []  # (monotonic, parsed json)
    t0 = time.monotonic()

    ws = await websockets.connect(base_url, additional_headers=headers, close_timeout=1)
    run.connect_s = time.monotonic() - t0

    task_id = str(uuid.uuid4())
    start_msg = {
        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": model,
            "parameters": {"format": "pcm", "sample_rate": sample_rate, **params},
            "input": {},
        },
    }
    await ws.send(json.dumps(start_msg))

    async def _recv_loop() -> None:
        try:
            while True:
                raw = await ws.recv()
                now = time.monotonic()
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    msg = {"_raw": repr(raw)[:200]}
                raw_f.write(json.dumps({"t": round(now - t0, 4), "msg": msg}, ensure_ascii=False) + "\n")
                raw_f.flush()
                events.append((now, msg))
        except Exception:
            pass

    recv_task = asyncio.create_task(_recv_loop())

    def _task_started_at() -> float | None:
        for t, m in events:
            if m.get("header", {}).get("event") == "task-started":
                return t
        return None

    def _extract_result(m: dict) -> tuple[bool, str, dict | None] | None:
        """result-generated → (is_final, text, usage)；非结果事件返回 None。"""
        if m.get("header", {}).get("event") != "result-generated":
            return None
        output = m.get("payload", {}).get("output", {})
        sentence = output.get("sentence") or {}
        if sentence.get("heartbeat"):
            return None
        usage = output.get("usage")
        is_final = sentence.get("end_time") is not None or sentence.get("sentence_end", False)
        return is_final, sentence.get("text", ""), usage

    def _failures() -> list[str]:
        out = []
        for _, m in events:
            h = m.get("header", {})
            if h.get("event") == "task-failed":
                out.append(f"{h.get('error_code')}: {h.get('error_message')}")
        return out

    try:
        # -- wait task-started (bounded) --
        deadline = time.monotonic() + 10.0
        while _task_started_at() is None and time.monotonic() < deadline:
            fails = _failures()
            if fails:
                run.task_failed = "; ".join(fails)
                return run
            await asyncio.sleep(0.02)
        started = _task_started_at()
        if started is None:
            run.task_failed = "no task-started within 10s"
            return run
        run.task_started_ms = round((started - t0) * 1000, 1)

        # -- feed speech at realtime pace --
        index = 0
        for fr in speech_frames:
            await ws.send(fr)
            index += 1
            delay = t0 + index * FRAME_MS / 1000 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        t_last_speech = time.monotonic()

        def _post_speech_finals() -> list[tuple[float, str]]:
            out = []
            for t, m in events:
                r = _extract_result(m)
                if r and r[0] and t >= t_last_speech:
                    out.append((t, r[1]))
            return out

        # -- trailing silence until post-speech final (+grace) --
        final_at: float | None = None
        for _ in range(trailing_silence_ms // FRAME_MS):
            await ws.send(silence)
            index += 1
            run.silence_fed_ms += FRAME_MS
            delay = t0 + index * FRAME_MS / 1000 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            arrivals = _post_speech_finals()
            if arrivals:
                final_at = min(t for t, _ in arrivals)
                if time.monotonic() - final_at >= grace_ms / 1000:
                    break
            fails = _failures()
            if fails:
                run.task_failed = "; ".join(fails)
                return run
        if final_at is None:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                arrivals = _post_speech_finals()
                if arrivals:
                    final_at = min(t for t, _ in arrivals)
                    break
                await asyncio.sleep(0.02)

        # finish-task 收尾（清理服务端任务）+ 短暂等待 usage 终值
        with contextlib.suppress(Exception):
            await ws.send(json.dumps({
                "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {"input": {}},
            }))
        await asyncio.sleep(0.5)
    finally:
        recv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await recv_task
        with contextlib.suppress(Exception):
            await ws.close()
        raw_f.close()

    for t, m in events:
        r = _extract_result(m)
        if r is None:
            continue
        is_final, text, usage = r
        if usage:
            if run.usage_first is None:
                run.usage_first = usage
            run.usage_last = usage
        rel = round((t - t_last_speech) * 1000, 1)
        if is_final:
            run.finals.append((rel, text))
        elif text:
            run.interims.append((rel, text))
    after = [ms for ms, _ in run.finals if ms >= 0]
    run.latency_ms = after[0] if after else None
    run.error_events = _failures()
    return run


def summarize(runs: list[Run]) -> list[dict]:
    by_case: dict[str, list[Run]] = {}
    for r in runs:
        by_case.setdefault(r.case, []).append(r)

    summaries = []
    for case, rs in by_case.items():
        ok = [r for r in rs if r.latency_ms is not None]
        lat = [r.latency_ms for r in ok]
        finals_texts = {t for r in ok for _, t in r.finals if t}
        usage = next((r.usage_last for r in reversed(rs) if r.usage_last), None)
        entry: dict = {
            "case": case,
            "model": rs[0].model,
            "n": len(rs),
            "ok": len(ok),
            "failed": [r.task_failed for r in rs if r.task_failed],
            "latency_mean": round(sum(lat) / len(lat), 1) if lat else None,
            "latency_min": min(lat) if lat else None,
            "latency_max": max(lat) if lat else None,
            "finals_per_run": round(sum(len(r.finals) for r in rs) / len(rs), 2),
            "interim_mean": round(sum(len(r.interims) for r in rs) / len(rs), 1),
            "texts": sorted(t for t in finals_texts),
            "usage_last": usage,
            "connect_s": round(rs[0].connect_s or 0, 2),
            "task_started_ms": rs[0].task_started_ms,
            "speech_ms": rs[0].speech_ms,
        }
        if usage:
            audio_s = (rs[0].speech_ms + rs[0].silence_fed_ms) / 1000
            if "input_tokens" in usage:  # 3.1 token 计费（duration 字段仅参考）
                cost = usage["input_tokens"] * PRICE_31_CNY_PER_M_IN / 1e6 \
                    + usage.get("output_tokens", 0) * PRICE_31_CNY_PER_M_OUT / 1e6
                entry["cost_cny_per_run"] = round(cost, 6)
                entry["cost_cny_per_audio_s"] = round(cost / audio_s, 8) if audio_s else None
                entry["tokens_per_audio_s"] = round(
                    (usage["input_tokens"] + usage.get("output_tokens", 0)) / audio_s, 2
                ) if audio_s else None
            elif "duration" in usage:  # 3.0 时长计费
                cost = usage["duration"] * PRICE_30_CNY_PER_S
                entry["cost_cny_per_run"] = round(cost, 6)
                entry["cost_cny_per_audio_s"] = round(
                    cost / usage["duration"], 8
                ) if usage["duration"] else None
        summaries.append(entry)
    return summaries


async def main_async(args: argparse.Namespace) -> None:
    load_env()
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    base_url = args.base_url or f"wss://{os.environ.get('DASHSCOPE_API_HOST', 'dashscope.aliyuncs.com')}/api-ws/v1/inference"
    workspace_id = args.workspace_id or os.environ.get("DASHSCOPE_WORKSPACE_ID")
    if not api_key:
        print("ERROR: DASHSCOPE_API_KEY not set (env or repo-root .env)", file=sys.stderr)
        sys.exit(1)

    pcm = load_wav(args.audio, args.sample_rate)
    frame_bytes = int(args.sample_rate * FRAME_MS / 1000) * 2
    full_ms = len(pcm) / (args.sample_rate * 2) * 1000
    pcm = trim_trailing_silence(pcm, frame_bytes, args.speech_threshold)
    speech_ms = len(pcm) / (args.sample_rate * 2) * 1000
    print(f"Loaded {args.audio}: {full_ms:.0f}ms → 语音 {speech_ms:.0f}ms（已裁尾部静音）")
    print(f"endpoint: {base_url}  workspace: {workspace_id or '-'}")
    print(f"cases: {args.only or 'all'}  repeats={args.repeats}\n")

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    cases = {k: v for k, v in CASES.items() if not args.only or k in args.only}

    all_runs: list[Run] = []
    for case, (model, params) in cases.items():
        print(f"--- {case}  [{model}]  params={params} ---")
        for i in range(1, args.repeats + 1):
            try:
                run = await run_once(
                    case=case, model=model, params=params,
                    base_url=base_url, api_key=api_key, workspace_id=workspace_id,
                    pcm_speech=pcm, sample_rate=args.sample_rate,
                    trailing_silence_ms=args.trailing_silence_ms,
                    grace_ms=args.grace_ms, run_idx=i,
                )
            except Exception as exc:  # 单次失败不中断矩阵
                run = Run(case=case, model=model, params=params,
                          task_failed=f"{type(exc).__name__}: {exc}")
            all_runs.append(run)
            if run.task_failed:
                print(f"  run {i}/{args.repeats}  FAILED: {run.task_failed}")
            else:
                lat = f"{run.latency_ms:.0f}ms" if run.latency_ms is not None else "未收到final"
                texts = " | ".join(f"@{ms:.0f}{' ' if ms>=0 else '(句中) '}{t!r}" for ms, t in run.finals)
                print(f"  run {i}/{args.repeats}  task_started={run.task_started_ms}ms  收尾={lat}  "
                      f"interims={len(run.interims)}  尾静音={run.silence_fed_ms}ms")
                print(f"          finals: {texts or '<无>'}   usage={run.usage_last}")
        print()

    summaries = summarize(all_runs)
    out_path = ARTIFACT_DIR / "summary.json"
    out_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(f"{'=' * 100}")
    print(f"{'case':<20}{'mean':>8}{'min':>7}{'max':>7}{'finals/次':>10}{'interims':>9}  收尾时延(ms) / 文本")
    for s in summaries:
        mean = f"{s['latency_mean']:.0f}" if s["latency_mean"] is not None else "-"
        mn = f"{s['latency_min']:.0f}" if s["latency_min"] is not None else "-"
        mx = f"{s['latency_max']:.0f}" if s["latency_max"] is not None else "-"
        print(f"{s['case']:<20}{mean:>8}{mn:>7}{mx:>7}{s['finals_per_run']:>10.2f}{s['interim_mean']:>9.1f}")
        for t in s["texts"]:
            print(f"     text: {t!r}")
        if s.get("usage_last"):
            print(f"     usage: {s['usage_last']}  cost≈{s.get('cost_cny_per_run')}元/次 "
                  f"({s.get('cost_cny_per_audio_s')}元/音频秒)")
        if s["failed"]:
            print(f"     FAILED: {s['failed']}")
    print(f"\nsummary → {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio", help="Speech wav (resampled to --sample-rate mono)")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--base-url", default=None,
                        help="默认 wss://$DASHSCOPE_API_HOST/api-ws/v1/inference")
    parser.add_argument("--workspace-id", default=None,
                        help="默认 $DASHSCOPE_WORKSPACE_ID（附 X-DashScope-WorkSpace 头）")
    parser.add_argument("--only", nargs="*", choices=list(CASES), default=None,
                        help="只跑指定 case")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--trailing-silence-ms", type=int, default=5000)
    parser.add_argument("--grace-ms", type=int, default=400)
    parser.add_argument("--speech-threshold", type=int, default=300)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
