#!/usr/bin/env python3
"""Test: feed(text1) → immediate reset() → feed(text2) → drain audio.

Verify that text1's residual audio does not contaminate text2's audio stream,
and that TTS message logs show correct gen attribution.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import wave
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from tts.providers.alibaba_qwen_tts import AlibabaQwenTTSProvider

DASHSCOPE_QWEN_TTS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime"


def save_wav(path: str, pcm: bytes, sample_rate: int) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


async def drain_audio(stream, label: str, sample_rate: int, timeout_s: float = 15.0) -> bytes:
    """Drain all audio chunks from stream until sentinel."""
    chunks: list[bytes] = []
    audio_gen = stream.audio()
    while True:
        try:
            chunk = await asyncio.wait_for(audio_gen.__anext__(), timeout=timeout_s)
            chunks.append(chunk)
        except StopAsyncIteration:
            break
        except TimeoutError:
            print(f"  [{label}] WARNING: timeout after {timeout_s}s")
            break
    pcm = b"".join(chunks)
    dur = len(pcm) / (sample_rate * 2) if sample_rate else 0
    print(f"  [{label}] drained {len(chunks)} chunks, {len(pcm)} bytes, {dur:.2f}s")
    return pcm


async def main():
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        api_key = "sk-cdd68e245ba94d7ab29e48bd0428e7e4"  # from .env
    if not api_key:
        print("ERROR: no DASHSCOPE_API_KEY", file=sys.stderr)
        sys.exit(1)

    sample_rate = 24000
    text1 = "你好世界，这是第一段测试文本，用于验证音频流是否正确隔离。"
    text2 = "这是第二段完全不同的文本，用于确认没有音频污染。"

    print("=" * 60)
    print("Test: feed(text1) → immediate reset() → feed(text2) → drain")
    print("=" * 60)
    print(f"text1 = {text1}")
    print(f"text2 = {text2}")

    provider = AlibabaQwenTTSProvider(
        base_url=DASHSCOPE_QWEN_TTS_URL,
        api_key=api_key,
        model="qwen3-tts-flash-realtime",
        voice="Cherry",
    )

    print("\nConnecting...")
    t0 = time.monotonic()
    stream = await provider.open(voice="Cherry", sample_rate=sample_rate)
    print(f"Connected ({time.monotonic() - t0:.2f}s)\n")

    # -- Feed text1 --------------------------------------------------
    print(f"[{time.monotonic() - t0:.3f}s] feed_text(text1)...")
    await stream.feed_text(text1)
    t_feed1 = time.monotonic()

    # -- IMMEDIATE reset without waiting for turn1 audio --------------
    print(f"[{time.monotonic() - t0:.3f}s] reset() immediately (no wait)")
    await stream.reset()
    t_reset = time.monotonic()
    print(f"  feed→reset gap: {(t_reset - t_feed1)*1000:.1f}ms")

    # -- Feed text2 ----------------------------------------------------
    print(f"[{time.monotonic() - t0:.3f}s] feed_text(text2)...")
    await stream.feed_text(text2)
    t_feed2 = time.monotonic()
    print(f"  reset→feed2 gap: {(t_feed2 - t_reset)*1000:.1f}ms")

    # -- Now drain text2 audio (should only get turn2 audio) --------
    print(f"\n[{time.monotonic() - t0:.3f}s] draining audio (expect text2 only)...")
    pcm = await drain_audio(stream, "text2", sample_rate)
    save_wav("/tmp/tts_race_test_turn2.wav", pcm, sample_rate)
    print("  saved → /tmp/tts_race_test_turn2.wav")

    # -- Bonus: try draining again to see if text1 audio shows up -----
    print(f"\n[{time.monotonic() - t0:.3f}s] trying to drain any leftover...")
    leftover = await drain_audio(stream, "leftover", sample_rate, timeout_s=5.0)
    if leftover:
        save_wav("/tmp/tts_race_test_leftover.wav", leftover, sample_rate)
        print("  LEFT OVER audio! saved → /tmp/tts_race_test_leftover.wav")
    else:
        print("  No leftover audio (clean)")

    await stream.close()
    elapsed = time.monotonic() - t0
    print(f"\nTotal time: {elapsed:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
