#!/usr/bin/env python3
"""Measure ASR wrap-up latency ("speech ends → final text") on real DashScope.

Feeds a speech audio file at real-time pace (20 ms frames @ 50 Hz) followed by
trailing silence, then reports how long after the *last speech frame was sent*
the provider emits its final result.  With real-time feeding the audio timeline
equals the wall clock, so this number is exactly the delay a user perceives
between "I stopped talking" and "the final transcript appeared".

The trailing silence (<--trailing-silence-ms>) must be long enough to cover the
provider's server-side VAD window (``max_sentence_silence``, default 1300 ms);
if it is too short the final is only produced by finish-task and the number is
meaningless — the script warns when that happens.

Usage::

    export DASHSCOPE_API_KEY=sk-xxxxxxxx
    services/.venv/bin/python services/scripts/measure_asr_final_latency.py speech.wav \
      --base-url wss://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference \
      --workspace-id <WorkspaceId> --matrix

    # single config, custom params
    services/.venv/bin/python services/scripts/measure_asr_final_latency.py speech.wav \
      --model paraformer-realtime-v2 --param max_sentence_silence=800 --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import struct
import sys
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path

# Ensure the services/ package root is on sys.path so we can import from asr/
_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from asr.base import AsrResult, AsrStream
from asr.providers.alibaba_bailian import AlibabaBailianProvider
from media.resample import Resampler

DASHSCOPE_ASR_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_MODEL = "qwen-audio-3.1-asr-flash-streaming"
DEFAULT_SAMPLE_RATE = 16000
FRAME_MS = 20

# 与 configs/dev/server.yaml 的 asr params 对齐（heartbeat/language_hints），
# 保证矩阵测量的是生产同款 run-task 参数
BASE_PARAMS: dict[str, object] = {"heartbeat": True, "language_hints": ["zh", "en"]}

# name → params-override；None 表示不传 max_sentence_silence（吃服务端默认 800）
MATRIX: list[tuple[str, dict[str, object] | None]] = [
    ("qwen-audio-3.0-asr-flash-streaming", None),
    ("qwen-audio-3.0-asr-flash-streaming", {"max_sentence_silence": 600}),
    ("qwen-audio-3.0-asr-flash-streaming", {"max_sentence_silence": 400}),
    ("paraformer-realtime-v2", None),
]


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------
def load_wav(path: str, target_rate: int) -> bytes:
    """Decode a WAV file to PCM16 mono bytes at *target_rate* (mirrors test_bailian_asr_real)."""
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
    """Drop trailing frames whose RMS is below *threshold* (int16 scale).

    Defines "the user stopped speaking" for the measurement; without it the
    script's t_last_speech would land at the end of the file's silence tail
    instead of at the last spoken word.
    """
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


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
@dataclass
class RunResult:
    model: str
    params: dict[str, object]
    latency_ms: float | None = None
    finals: list[tuple[float, str]] = field(default_factory=list)  # (ms relative to speech end, text)
    interims: int = 0
    last_interim: str = ""
    speech_ms: int = 0
    silence_fed_ms: int = 0
    error: str | None = None


async def _consume(stream: AsrStream, events: list[tuple[float, AsrResult]]) -> None:
    async for r in stream.results():
        events.append((time.monotonic(), r))


async def _pace(t_base: float, frame_index: int) -> None:
    target = t_base + frame_index * FRAME_MS / 1000
    delay = target - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)


async def run_once(
    *,
    provider: AlibabaBailianProvider,
    model: str,
    params: dict[str, object],
    pcm_speech: bytes,
    sample_rate: int,
    trailing_silence_ms: int,
    grace_ms: int,
    language: str | None,
) -> RunResult:
    frame_bytes = int(sample_rate * FRAME_MS / 1000) * 2
    speech_frames = split_frames(pcm_speech, frame_bytes)
    silence = b"\x00" * frame_bytes

    result = RunResult(model=model, params=params)
    result.speech_ms = len(speech_frames) * FRAME_MS
    events: list[tuple[float, AsrResult]] = []

    stream = await provider.open(sample_rate=sample_rate, language=language, interim_results=True)
    consume_task = asyncio.create_task(_consume(stream, events))
    try:
        # -- feed speech at real-time pace --
        t0 = time.monotonic()
        index = 0
        for fr in speech_frames:
            await stream.feed(fr)
            index += 1
            await _pace(t0, index)
        t_last_speech = time.monotonic()

        # -- feed trailing silence until a post-speech final arrives (+grace) --
        # 只认 t_last_speech 之后到达的 final：句中停顿被切断时，前一段的 final
        # 会在说话过程中就到达，若把它当作收尾信号会提前停止喂静音，后一段永远
        # 等不到 final（收尾时延就测不出来了）。
        def _post_speech_finals() -> list[float]:
            return [t for t, r in events if r.is_final and t >= t_last_speech]

        final_at: float | None = None
        for _ in range(trailing_silence_ms // FRAME_MS):
            await stream.feed(silence)
            index += 1
            result.silence_fed_ms += FRAME_MS
            await _pace(t0, index)
            arrivals = _post_speech_finals()
            if arrivals:
                final_at = min(arrivals)
                if time.monotonic() - final_at >= grace_ms / 1000:
                    break

        # 静音喂完仍无 final：等一小段（不再喂帧，任务级判死是 23s，秒级安全）
        if final_at is None:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                arrivals = _post_speech_finals()
                if arrivals:
                    final_at = min(arrivals)
                    break
                await asyncio.sleep(0.02)
    finally:
        consume_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await consume_task
        with contextlib.suppress(Exception):
            await stream.close()

    result.interims = sum(1 for _, r in events if not r.is_final)
    interim_texts = [r.text for _, r in events if not r.is_final and r.text]
    result.last_interim = interim_texts[-1] if interim_texts else ""

    for t, r in events:
        if r.is_final:
            result.finals.append((round((t - t_last_speech) * 1000, 1), r.text))
    # 收尾 final = 语音结束后到达的第一个 final（主指标）
    after = [ms for ms, _ in result.finals if ms >= 0]
    if after:
        result.latency_ms = after[0]
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _config_label(model: str, params: dict[str, object]) -> str:
    mss = params.get("max_sentence_silence")
    tag = f"max_sentence_silence={mss}" if mss is not None else "默认(1300)"
    return f"{model} [{tag}]"


def _print_run(result: RunResult, idx: int, total: int) -> None:
    if result.error:
        print(f"  run {idx}/{total}  ERROR: {result.error}")
        return
    latency = f"{result.latency_ms:.0f}ms" if result.latency_ms is not None else "未收到 final"
    finals_desc = " | ".join(
        f"@{ms:.0f}ms{' (句中切断)' if ms < 0 else ''} {text!r}" for ms, text in result.finals
    ) or "<无>"
    print(
        f"  run {idx}/{total}  收尾={latency}  finals={len(result.finals)}  "
        f"interims={result.interims}  语音={result.speech_ms}ms  尾静音={result.silence_fed_ms}ms"
    )
    print(f"          finals: {finals_desc}")
    if result.last_interim:
        print(f"          最后 interim: {result.last_interim!r}")


def _summarize(label: str, results: list[RunResult]) -> dict[str, object]:
    ok = [r for r in results if r.latency_ms is not None]
    latencies = [r.latency_ms for r in ok]
    finals_per_run = [len(r.finals) for r in results]
    texts = {text for r in ok for _, text in r.finals}
    return {
        "label": label,
        "n": len(results),
        "ok": len(ok),
        "mean": sum(latencies) / len(latencies) if latencies else None,
        "min": min(latencies) if latencies else None,
        "max": max(latencies) if latencies else None,
        "finals_per_run": sum(finals_per_run) / len(finals_per_run) if finals_per_run else 0,
        "stable_text": len(texts) <= 1,
        "texts": texts,
    }


def _print_matrix(summaries: list[dict[str, object]]) -> None:
    print(f"\n{'=' * 96}")
    print("汇总（收尾时延 = 语音结束 → final 到达，实时节奏喂帧；单位 ms）")
    print(f"{'配置':<58}{'mean':>8}{'min':>8}{'max':>8}{'finals/次':>10}")
    for s in summaries:
        mean = f"{s['mean']:.0f}" if s["mean"] is not None else "-"
        mn = f"{s['min']:.0f}" if s["min"] is not None else "-"
        mx = f"{s['max']:.0f}" if s["max"] is not None else "-"
        print(f"{s['label']:<58}{mean:>8}{mn:>8}{mx:>8}{s['finals_per_run']:>10.1f}")
    for s in summaries:
        if s["finals_per_run"] > 1.5:
            print(f"\n[碎句风险] {s['label']} 平均每次出 {s['finals_per_run']:.1f} 个 final：")
            for text in sorted(t for t in s["texts"] if t):
                print(f"    - {text!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main_async(args: argparse.Namespace, api_key: str) -> None:
    pcm = load_wav(args.audio, args.sample_rate)
    full_ms = len(pcm) / (args.sample_rate * 2) * 1000
    pcm = trim_trailing_silence(pcm, int(args.sample_rate * FRAME_MS / 1000) * 2, args.speech_threshold)
    speech_ms = len(pcm) / (args.sample_rate * 2) * 1000
    print(f"Loaded {args.audio}: {full_ms:.0f}ms → 语音 {speech_ms:.0f}ms（已裁尾部静音）")
    print(f"尾静音 {args.trailing_silence_ms}ms, 每配置 {args.repeats} 次\n")

    if args.matrix:
        cases: list[tuple[str, dict[str, object]]] = []
        for model, override in MATRIX:
            params = dict(BASE_PARAMS)
            if override:
                params.update(override)
            cases.append((model, params))
    else:
        params = dict(BASE_PARAMS)
        params.update(args.param)
        cases = [(args.model, params)]

    summaries: list[dict[str, object]] = []
    for model, params in cases:
        provider = AlibabaBailianProvider(
            base_url=args.base_url,
            api_key=api_key,
            model=model,
            params=params,
            workspace_id=args.workspace_id,
        )
        label = _config_label(model, params)
        print(f"--- {label} ---")
        results: list[RunResult] = []
        for i in range(1, args.repeats + 1):
            try:
                result = await run_once(
                    provider=provider,
                    model=model,
                    params=params,
                    pcm_speech=pcm,
                    sample_rate=args.sample_rate,
                    trailing_silence_ms=args.trailing_silence_ms,
                    grace_ms=args.grace_ms,
                    language=args.language,
                )
            except Exception as exc:  # 单次失败不中断矩阵
                result = RunResult(model=model, params=params, error=f"{type(exc).__name__}: {exc}")
            results.append(result)
            _print_run(result, i, args.repeats)
        summaries.append(_summarize(label, results))

    _print_matrix(summaries)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure ASR final-result latency (speech end → final) at real-time pace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("audio", help="Speech audio file (.wav, any rate/channels → 16k mono)")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DASHSCOPE_ASR_URL)
    parser.add_argument("--workspace-id", default=None, help="仅附加 X-DashScope-WorkSpace 头")
    parser.add_argument("--language", default=None, help="映射为 language_hints（仅单配置模式）")
    parser.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="追加/覆盖 run-task 参数（可重复），如 --param max_sentence_silence=800",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--trailing-silence-ms", type=int, default=5000,
                        help="语音结束后补喂的静音上限（须 > 服务端 VAD 阈值，默认 5000）")
    parser.add_argument("--grace-ms", type=int, default=400,
                        help="final 到达后继续喂帧的时长，防收尾过早断帧")
    parser.add_argument("--speech-threshold", type=int, default=300,
                        help="尾部静音裁剪的 RMS 阈值（int16 尺度，默认 300）")
    parser.add_argument("--matrix", action="store_true",
                        help="跑内置对照矩阵：qwen/paraformer × 默认1300/800")
    args = parser.parse_args()

    # --param KEY=VALUE 解析（值按 YAML 风格转换 bool/int，其余留字符串）
    parsed: dict[str, object] = {}
    for item in args.param:
        if "=" not in item:
            parser.error(f"--param 需要 KEY=VALUE 形式: {item!r}")
        key, _, value = item.partition("=")
        if value.lower() in ("true", "false"):
            parsed[key] = value.lower() == "true"
        else:
            try:
                parsed[key] = int(value)
            except ValueError:
                parsed[key] = value
    args.param = parsed

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("ERROR: DASHSCOPE_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main_async(args, api_key))


if __name__ == "__main__":
    main()
