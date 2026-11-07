#!/usr/bin/env python3
"""Real voice test for Alibaba Bailian (DashScope) ASR provider.

Feeds a local audio file to the DashScope real-time ASR WebSocket API and
prints recognition results.

Usage::

    python scripts/test_bailian_asr_real.py <audio_file> [options]

The audio file is auto-detected:
    - ``.wav`` files are decoded to PCM16 mono (any sample rate, resampled
      on the fly with soxr).
    - ``.pcm`` / ``.raw`` files are read as raw PCM16 mono at the given
      sample rate.

Requires:
    - DASHSCOPE_API_KEY environment variable (Aliyun DashScope API key).
    - ``soxr`` (already a project dependency, used for wav resampling).

Examples::

    export DASHSCOPE_API_KEY=sk-xxxxxxxx
    python scripts/test_bailian_asr_real.py hello.wav
    python scripts/test_bailian_asr_real.py audio.pcm --sample-rate 8000
    python scripts/test_bailian_asr_real.py speech.wav --model paraformer-realtime-v2 --language zh

    # qwen-audio 等业务空间模型：--base-url 必须指向 workspace 专属域名，workspace-id 只补请求头
    python scripts/test_bailian_asr_real.py hello.wav \
        --base-url wss://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference \
        --workspace-id llm-1tmsqifo51cwfadr
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
from pathlib import Path

# Ensure the services/ package root is on sys.path so we can import from asr/
_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from asr.providers.alibaba_bailian import AlibabaBailianProvider
from media.resample import Resampler

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DASHSCOPE_ASR_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_MODEL = "qwen-audio-3.1-asr-flash-streaming"
DEFAULT_SAMPLE_RATE = 16000
FEED_CHUNK_MS = 100  # audio chunk size in milliseconds for each feed() call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_wav(path: str, target_rate: int) -> tuple[bytes, int]:
    """Decode a WAV file to PCM16 mono bytes, resampling if needed.

    Returns ``(pcm_bytes, target_rate)``.
    """
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
            samples = tuple((s - 128) << 8 for s in samples)  # type: ignore[assignment]
        elif sampwidth == 4:
            samples = tuple(s >> 16 for s in samples)  # type: ignore[assignment]
        raw = struct.pack(f"<{len(samples)}h", *samples)

    if nchannels == 2:
        # Drop second channel
        mono = bytearray(len(raw) // 2)
        for i in range(0, len(raw), 4):
            mono[i // 2 : i // 2 + 2] = raw[i : i + 2]
        raw = bytes(mono)
    elif nchannels > 2:
        # Keep first channel only
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
    workspace_id: str | None = None,
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
    provider = AlibabaBailianProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        workspace_id=workspace_id,
    )

    ws_tag = f"  (X-DashScope-WorkSpace: {workspace_id})" if workspace_id else ""
    print(f"Connecting to {base_url}  (model={model}){ws_tag} ...")
    t0 = time.monotonic()

    stream = await provider.open(
        sample_rate=actual_rate,
        language=language,
        interim_results=True,
    )
    print(f"Connected  ({time.monotonic() - t0:.2f}s)")

    # -- interim 消费者（--all-results）：背景任务实时打印 interim --
    # final() 是唯一的 finish-task 发送者且自带 drain，两个消费者会争抢队列哨兵
    # （一方取到 None 另一方挂到超时），故收尾前必须先取消本任务（见 Finalise）。
    consume_task: asyncio.Task[None] | None = None
    if show_interim:

        async def _consume() -> None:
            async for r in stream.results():
                if not r.is_final:
                    print(f"  [interim] {r.text!r}")

        consume_task = asyncio.create_task(_consume())

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

    # ---- 尾随静音 2s ---------------------------------------------------------
    # 生产里静音连续不断，final 由服务端 VAD 断句自然产出（不依赖 finish-task）；
    # 补尾静音才能观察到完整 interim 序列与自发的 final（max-speed 下尤其必要）。
    for _ in range(20):
        await stream.feed(b"\x00" * chunk_bytes)
        await asyncio.sleep(0.05)

    # ---- finalise -----------------------------------------------------------
    if consume_task is not None:
        # 等 final 落地（有界）：final_text 由接收循环维护，与谁消费队列无关，可安全
        # 轮询；随后必须停消费者——final() 的 drain 与其争抢队列哨兵（一方取到 None
        # 另一方挂超时）。
        deadline = time.monotonic() + 8.0
        while not stream.final_text and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        consume_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await consume_task

    # final() sends the finish-task message and drains remaining results.
    print("Results:")

    final_text = await stream.final()

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
        description="Real voice test for Alibaba Bailian ASR provider",
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
        help=f"DashScope ASR model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--base-url", default=DASHSCOPE_ASR_URL,
        help="DashScope WebSocket endpoint",
    )
    parser.add_argument(
        "--workspace-id", default=None,
        help="DashScope workspace id: 仅附加 X-DashScope-WorkSpace 请求头。官方推荐与 "
             "workspace 专属域名(wss://{id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/"
             "inference)配套；实测（2026-09-13）本账号缺头亦可连，两种组合均可用",
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

    if args.workspace_id and "maas.aliyuncs.com" not in args.base_url:
        # 实测（2026-09-13）：头与专属域名非硬性配套（专属域名±头、旧域名+头均可连，
        # 见 docs/asr_bailian_improvements.md §6.3-#1）——不再阻断，仅提示推荐组合。
        print(
            "WARNING: --workspace-id 通常与 workspace 专属域名配套使用"
            "（wss://{id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference）；"
            "实测旧域名+头亦可连，继续执行。",
            file=sys.stderr,
        )

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
            workspace_id=args.workspace_id,
        )
    )


if __name__ == "__main__":
    main()
