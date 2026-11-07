#!/usr/bin/env python3
"""Real voice test for Alibaba Qwen3-TTS Flash Realtime provider.

Sends text to the Qwen TTS Realtime WebSocket API and saves the
synthesised audio to a WAV file.

Usage::

    python scripts/test_qwen_tts_real.py "你好，这是一个测试。" [options]
    python scripts/test_qwen_tts_real.py --file input.txt [options]

Requires:
    - DASHSCOPE_API_KEY environment variable (Aliyun DashScope API key).

Examples::

    export DASHSCOPE_API_KEY=sk-xxxxxxxx
    python scripts/test_qwen_tts_real.py "你好世界"
    python scripts/test_qwen_tts_real.py "Hello world" --voice Cherry --sample-rate 24000
    python scripts/test_qwen_tts_real.py --file speech.txt --model qwen3-tts-flash-realtime
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

# Ensure the services/ package root is on sys.path so we can import from tts/
_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from tts.providers.alibaba_qwen_tts import AlibabaQwenTTSProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DASHSCOPE_QWEN_TTS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime"
DEFAULT_MODEL = "qwen3-tts-flash-realtime"
DEFAULT_VOICE = "Cherry"
DEFAULT_SAMPLE_RATE = 24000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def save_wav(path: str, pcm: bytes, sample_rate: int) -> None:
    """Write PCM16 mono audio to a WAV file."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def _synthesize_one(
    stream,
    text: str,
    sample_rate: int,
    turn: int,
    total: int,
) -> tuple[bytes, float]:
    """Feed one text, collect audio, return (pcm, duration_s)."""
    await stream.feed_text(text)
    t_feed = time.monotonic()
    await stream.flush()

    AUDIO_TIMEOUT_S = 30.0
    audio_chunks: list[bytes] = []
    total_bytes = 0
    t_first_audio = None
    audio_gen = stream.audio()
    while True:
        try:
            chunk = await asyncio.wait_for(audio_gen.__anext__(), timeout=AUDIO_TIMEOUT_S)
            if t_first_audio is None:
                t_first_audio = time.monotonic()
            audio_chunks.append(chunk)
            total_bytes += len(chunk)
        except StopAsyncIteration:
            break
        except TimeoutError:
            print("  WARNING: audio receive timed out after flush")
            break

    pcm = b"".join(audio_chunks)
    duration_s = total_bytes / (sample_rate * 2) if sample_rate > 0 else 0
    first_latency = (t_first_audio - t_feed) if t_first_audio is not None else 0

    print(f"  [{turn}/{total}] \"{text[:50]}{'...' if len(text) > 50 else ''}\"")
    print(f"       {len(text)} chars | first-byte: {first_latency:.3f}s | "
          f"audio: {total_bytes}B {duration_s:.2f}s")
    return pcm, duration_s


async def run_test(
    *,
    texts: list[str],
    voice: str,
    model: str,
    sample_rate: int,
    base_url: str,
    api_key: str,
    output_dir: str,
) -> None:
    total = len(texts)
    print(f"Texts:     {total} utterances")
    print(f"Model:     {model}, voice={voice}, rate={sample_rate}")

    # ---- connect -----------------------------------------------------------
    provider = AlibabaQwenTTSProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        voice=voice,
    )

    print(f"Connecting to {base_url} ...")
    t0 = time.monotonic()
    stream = await provider.open(voice=voice, sample_rate=sample_rate)
    print(f"Connected  ({time.monotonic() - t0:.2f}s)\n")

    # ---- synthesize each text on the same connection -----------------------
    total_bytes = 0
    total_duration = 0.0
    for i, text in enumerate(texts, 1):
        t_turn = time.monotonic()
        if i > 1:
            await stream.reset()  # simulate orchestrator reset between turns
        pcm, dur = await _synthesize_one(stream, text, sample_rate, i, total)
        total_bytes += len(pcm)
        total_duration += dur
        out_path = f"{output_dir}/tts_turn_{i:02d}.wav"
        save_wav(out_path, pcm, sample_rate)
        print(f"       saved -> {out_path}  ({time.monotonic() - t_turn:.2f}s turn)\n")

    elapsed = time.monotonic() - t0
    print(f"{'='*60}")
    print(f"All {total} turns complete")
    print(f"Total audio: {total_bytes} bytes, {total_duration:.2f}s")
    print(f"Total time:  {elapsed:.2f}s")
    if elapsed > 0 and total_duration > 0:
        print(f"RT factor:   {total_duration / elapsed:.2f}x")

    await stream.close()
    print("Connection closed.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Real voice test for Alibaba Qwen3-TTS Flash Realtime provider",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "text", nargs="*", default=[],
        help="One or more texts to synthesise (separate by space, each is a turn)",
    )
    parser.add_argument(
        "--texts", "-t",
        help="Multiple texts separated by '|', each is a turn on the same connection",
    )
    parser.add_argument(
        "--file", "-f",
        help="Read texts from file (one per line), each line is a turn",
    )
    parser.add_argument(
        "--voice", default=DEFAULT_VOICE,
        help=f"Voice name (default: {DEFAULT_VOICE})",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Qwen TTS model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE,
        help=f"Output sample rate in Hz (default: {DEFAULT_SAMPLE_RATE})",
    )
    parser.add_argument(
        "--base-url", default=DASHSCOPE_QWEN_TTS_URL,
        help="Qwen TTS Realtime WebSocket endpoint",
    )
    parser.add_argument(
        "--output-dir", "-o", default="/tmp/tts_test",
        help="Output directory for WAV files (default: /tmp/tts_test)",
    )

    args = parser.parse_args()

    # Resolve texts
    texts: list[str] = []
    if args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"ERROR: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        texts = [t.strip() for t in filepath.read_text(encoding="utf-8").strip().splitlines() if t.strip()]
    elif args.texts:
        texts = [t.strip() for t in args.texts.split("|") if t.strip()]
    elif args.text:
        texts = args.text
    else:
        print("ERROR: provide text(s) as arguments, --texts, or --file", file=sys.stderr)
        sys.exit(1)

    if not texts:
        print("ERROR: no text provided", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("ERROR: DASHSCOPE_API_KEY environment variable is not set.", file=sys.stderr)
        print("  export DASHSCOPE_API_KEY=sk-xxxxxxxx", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(
        run_test(
            texts=texts,
            voice=args.voice,
            model=args.model,
            sample_rate=args.sample_rate,
            base_url=args.base_url,
            api_key=api_key,
            output_dir=str(out_dir),
        )
    )


if __name__ == "__main__":
    main()
