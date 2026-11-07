#!/usr/bin/env python3
"""Compare real-time performance of two ASR providers on the same audio file.

Usage::

    export DASHSCOPE_API_KEY=sk-xxxxxxxx
    python scripts/compare_asr_realtime.py <audio_file> [--language zh]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import struct
import sys
import time
import wave
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from asr.providers.alibaba_bailian import AlibabaBailianProvider
from asr.providers.alibaba_qwen_asr import AlibabaQwenASRProvider
from media.resample import Resampler


def load_wav(path: str, target_rate: int) -> tuple[bytes, int]:
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
    if nchannels >= 2:
        frame_sz = nchannels * 2
        mono = bytearray(len(raw) // nchannels)
        for i in range(0, len(raw), frame_sz):
            mono[i // nchannels : i // nchannels + 2] = raw[i : i + 2]
        raw = bytes(mono)
    if src_rate != target_rate:
        resampler = Resampler(src_rate, target_rate)
        raw = resampler.process(raw) + resampler.flush()
    return raw, target_rate


async def test_provider(
    name: str,
    provider,
    pcm: bytes,
    sample_rate: int,
    audio_duration_s: float,
    language: str | None,
    realtime: bool,
) -> dict:
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"{'='*60}")

    t0 = time.monotonic()
    stream = await provider.open(
        sample_rate=sample_rate,
        language=language,
        interim_results=True,
    )
    t_connect = time.monotonic() - t0
    print(f"  Connect: {t_connect:.3f}s")

    # Feed audio chunks
    chunk_ms = 100
    chunk_bytes = sample_rate * 2 * chunk_ms // 1000
    chunks = [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]

    t_first_feed = None
    for i, chunk in enumerate(chunks):
        if t_first_feed is None:
            t_first_feed = time.monotonic()
        await stream.feed(chunk)
        if realtime:
            await asyncio.sleep(chunk_ms / 1000)

    t_feed_done = time.monotonic()

    # final() sends end signal and returns final text
    t_before_final = time.monotonic()
    final_text = await stream.final()
    t_total = time.monotonic() - t0
    t_final = time.monotonic() - t_before_final

    await stream.close()

    print(f"  Final text:  {final_text or '<no result>'}")
    print(f"  Feed time:   {t_feed_done - t0:.3f}s")
    print(f"  Final time:  {t_final:.3f}s")
    print(f"  Total time:  {t_total:.3f}s")
    print(f"  RTF:         {audio_duration_s / t_total:.2f}x" if t_total > 0 else "")

    return {
        "name": name,
        "connect_s": round(t_connect, 3),
        "feed_s": round(t_feed_done - t0, 3),
        "final_s": round(t_final, 3),
        "total_s": round(t_total, 3),
        "final_text": final_text,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Compare ASR provider real-time performance")
    parser.add_argument("audio_file", help="Path to .wav or .pcm audio file")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--fast", action="store_true", help="Feed as fast as possible (non-realtime)")
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("ERROR: DASHSCOPE_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # Load audio once
    ext = Path(args.audio_file).suffix.lower()
    if ext == ".wav":
        pcm, actual_rate = load_wav(args.audio_file, args.sample_rate)
    else:
        pcm = Path(args.audio_file).read_bytes()
        actual_rate = args.sample_rate

    duration_s = len(pcm) / (actual_rate * 2)
    print(f"Audio: {len(pcm)} bytes, {actual_rate} Hz, {duration_s:.2f}s PCM16 mono")
    print(f"Mode:  {'fast' if args.fast else 'realtime'}")

    realtime = not args.fast

    # Test Bailian (paraformer-realtime-v2)
    bailian = AlibabaBailianProvider(
        base_url="wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        api_key=api_key,
        model="paraformer-realtime-v2",
    )
    r1 = await test_provider("Bailian (paraformer-realtime-v2)", bailian, pcm, actual_rate, duration_s, args.language, realtime)

    # Test Fun-ASR (same bailian provider, different model)
    funasr = AlibabaBailianProvider(
        base_url="wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        api_key=api_key,
        model="fun-asr-realtime",
    )
    r2 = await test_provider("Fun-ASR (fun-asr-realtime)", funasr, pcm, actual_rate, duration_s, args.language, realtime)

    # Test Qwen3-ASR
    qwen = AlibabaQwenASRProvider(
        base_url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime",
        api_key=api_key,
        model="qwen3-asr-flash-realtime",
    )
    r3 = await test_provider("Qwen3-ASR (qwen3-asr-flash-realtime)", qwen, pcm, actual_rate, duration_s, args.language, realtime)

    # Summary
    print(f"\n{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    print(f"{'Metric':<20} {'Bailian':>15} {'Fun-ASR':>15} {'Qwen3-ASR':>15}")
    print(f"{'-'*65}")
    print(f"{'Connect':<20} {r1['connect_s']:>14.3f}s {r2['connect_s']:>14.3f}s {r3['connect_s']:>14.3f}s")
    print(f"{'Audio feed':<20} {r1['feed_s']:>14.3f}s {r2['feed_s']:>14.3f}s {r3['feed_s']:>14.3f}s")
    print(f"{'Final (server)':<20} {r1['final_s']:>14.3f}s {r2['final_s']:>14.3f}s {r3['final_s']:>14.3f}s")
    print(f"{'Total':<20} {r1['total_s']:>14.3f}s {r2['total_s']:>14.3f}s {r3['total_s']:>14.3f}s")
    print(f"\n{'Transcript':<20} {r1['final_text'] or 'N/A'}")
    print(f"{'':<20} {r2['final_text'] or 'N/A'}")
    print(f"{'':<20} {r3['final_text'] or 'N/A'}")


if __name__ == "__main__":
    asyncio.run(main())
