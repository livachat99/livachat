#!/usr/bin/env python3
"""Real voice test for Alibaba Qwen3-ASR Flash Realtime provider.

Feeds a local audio file to the Qwen ASR Realtime WebSocket API and
prints recognition results.

Usage::

    python scripts/test_qwen_asr_real.py <audio_file> [options]

The audio file is auto-detected:
    - ``.wav`` files are decoded to PCM16 mono (any sample rate, resampled
      on the fly with soxr).
    - ``.pcm`` / ``.raw`` files are read as raw PCM16 mono at the given
      sample rate.

Requires:
    - DASHSCOPE_API_KEY environment variable (Aliyun DashScope API key).

Examples::

    export DASHSCOPE_API_KEY=sk-xxxxxxxx
    python scripts/test_qwen_asr_real.py hello.wav
    python scripts/test_qwen_asr_real.py audio.pcm --sample-rate 16000
    python scripts/test_qwen_asr_real.py speech.wav --model qwen3-asr-flash-realtime --language zh
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

# Ensure the services/ package root is on sys.path so we can import from asr/
_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from asr.providers.alibaba_qwen_asr import AlibabaQwenASRProvider
from media.resample import Resampler

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DASHSCOPE_QWEN_ASR_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime"
DEFAULT_MODEL = "qwen3-asr-flash-realtime"
DEFAULT_SAMPLE_RATE = 16000
FEED_CHUNK_MS = 100  # audio chunk size in milliseconds for each feed() call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_wav(path: str, target_rate: int) -> tuple[bytes, int]:
    """Decode a WAV file to PCM16 mono bytes, resampling if needed."""
    with wave.open(path, "rb") as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        src_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    # Convert to 16-bit mono if needed
    if sampwidth != 2:
        fmt = {1: "b", 2: "h", 4: "i"}[sampwidth]
        samples = struct.unpack(f"<{len(raw) // sampwidth}{fmt}", raw)
        if sampwidth == 1:
            samples = tuple((s - 128) << 8 for s in samples)
        elif sampwidth == 4:
            samples = tuple(s >> 16 for s in samples)
        raw = struct.pack(f"<{len(samples)}h", *samples)

    if nchannels == 2:
        mono = bytearray(len(raw) // 2)
        for i in range(0, len(raw), 4):
            mono[i // 2 : i // 2 + 2] = raw[i : i + 2]
        raw = bytes(mono)
    elif nchannels > 2:
        frame_sz = nchannels * 2
        mono = bytearray(len(raw) // nchannels)
        for i in range(0, len(raw), frame_sz):
            mono[i // nchannels : i // nchannels + 2] = raw[i : i + 2]
        raw = bytes(mono)

    if src_rate != target_rate:
        resampler = Resampler(src_rate, target_rate)
        raw = resampler.process(raw) + resampler.flush()

    return raw, target_rate


def load_raw(path: str, sample_rate: int) -> bytes:
    """Read a raw PCM16 mono file."""
    return Path(path).read_bytes()


def iter_chunks(data: bytes, chunk_bytes: int) -> list[bytes]:
    """Split *data* into fixed-size chunks."""
    return [data[i : i + chunk_bytes] for i in range(0, len(data), chunk_bytes)]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run_test(
    *,
    audio_path: str,
    sample_rate: int,
    model: str,
    base_url: str,
    api_key: str,
    language: str | None,
    show_interim: bool,
    realtime: bool,
) -> None:
    # ---- load audio --------------------------------------------------------
    ext = Path(audio_path).suffix.lower()
    if ext == ".wav":
        pcm, actual_rate = load_wav(audio_path, sample_rate)
    else:
        pcm = load_raw(audio_path, sample_rate)
        actual_rate = sample_rate

    duration_s = len(pcm) / (actual_rate * 2)
    print(f"Loaded  {audio_path}")
    print(f"        {len(pcm)} bytes, {actual_rate} Hz, {duration_s:.2f}s PCM16 mono")

    # ---- connect -----------------------------------------------------------
    provider = AlibabaQwenASRProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
    )

    print(f"Connecting to {base_url}  (model={model}) ...")
    t0 = time.monotonic()

    stream = await provider.open(
        sample_rate=actual_rate,
        language=language,
        interim_results=True,
    )
    print(f"Connected  ({time.monotonic() - t0:.2f}s)")

    # ---- feed audio ---------------------------------------------------------
    chunk_bytes = actual_rate * 2 * FEED_CHUNK_MS // 1000
    chunks = iter_chunks(pcm, chunk_bytes)

    print(f"Feeding {len(chunks)} chunks ({FEED_CHUNK_MS}ms each) ...")

    for i, chunk in enumerate(chunks):
        await stream.feed(chunk)
        if realtime:
            await asyncio.sleep(FEED_CHUNK_MS / 1000)

    feed_elapsed = time.monotonic() - t0
    print(f"Audio fed  ({feed_elapsed:.2f}s)")

    # ---- finalise -----------------------------------------------------------
    # final() sends session.finish and drains remaining results.
    final_text = await stream.final()

    if show_interim and final_text:
        print(f"  [FINAL] {final_text}")

    elapsed = time.monotonic() - t0
    print(f"\n{'='*60}")
    print(f"Final transcript:  {final_text or '<no result>'}")
    print(f"Total time:        {elapsed:.2f}s")
    print(f"Audio duration:    {duration_s:.2f}s")

    await stream.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Real voice test for Alibaba Qwen3-ASR Flash Realtime provider",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("audio_file", help="Path to .wav or .pcm audio file")
    parser.add_argument(
        "--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE,
        help=f"Target sample rate in Hz (default: {DEFAULT_SAMPLE_RATE})",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Qwen ASR model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--base-url", default=DASHSCOPE_QWEN_ASR_URL,
        help="Qwen ASR Realtime WebSocket endpoint",
    )
    parser.add_argument("--language", help="Language hint (e.g. zh, en)")
    parser.add_argument(
        "--all-results", action="store_true",
        help="Print interim results in addition to final",
    )
    parser.add_argument(
        "--realtime", action="store_true",
        help="Feed audio at realtime speed instead of as fast as possible",
    )

    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("ERROR: DASHSCOPE_API_KEY environment variable is not set.", file=sys.stderr)
        print("  export DASHSCOPE_API_KEY=sk-xxxxxxxx", file=sys.stderr)
        sys.exit(1)

    if not Path(args.audio_file).exists():
        print(f"ERROR: file not found: {args.audio_file}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(
        run_test(
            audio_path=args.audio_file,
            sample_rate=args.sample_rate,
            model=args.model,
            base_url=args.base_url,
            api_key=api_key,
            language=args.language,
            show_interim=args.all_results,
            realtime=args.realtime,
        )
    )


if __name__ == "__main__":
    main()
