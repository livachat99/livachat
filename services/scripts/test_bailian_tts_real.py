#!/usr/bin/env python3
"""Real voice test for Alibaba Bailian (DashScope) TTS provider.

Sends text to the DashScope real-time TTS WebSocket API and saves the
synthesised audio to a WAV file.

Usage::

    python scripts/test_bailian_tts_real.py "你好，这是一个测试。" [options]
    python scripts/test_bailian_tts_real.py --file input.txt [options]

Requires:
    - DASHSCOPE_API_KEY environment variable (Aliyun DashScope API key).

Examples::

    export DASHSCOPE_API_KEY=sk-xxxxxxxx
    python scripts/test_bailian_tts_real.py "你好世界"
    python scripts/test_bailian_tts_real.py "Hello world" --voice longanlingxi --sample-rate 16000
    # qwen-audio 等业务空间模型：--base-url 必须指向 workspace 专属域名，workspace-id 只补请求头
    python scripts/test_bailian_tts_real.py --file speech.txt \
        --base-url wss://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference \
        --workspace-id llm-1tmsqifo51cwfadr
    # 同 WS 多轮 + 轮间无任务 idle >23s（验证两个协议假设，见 v2 设计）
    python scripts/test_bailian_tts_real.py --file rounds.txt --multi-round --idle-s 30 \
        --base-url wss://... --workspace-id ...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
import wave
from pathlib import Path

# Ensure the services/ package root is on sys.path so we can import from tts/
_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from tts.providers.alibaba_bailian import AlibabaBailianTTSProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DASHSCOPE_TTS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_MODEL = "qwen-audio-3.0-tts-flash"
DEFAULT_VOICE = "longanlingxi"
DEFAULT_SAMPLE_RATE = 16000


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
def _split_rounds(text: str) -> list[str]:
    """--multi-round 的文本切分：换行 > 句子标点 > 均分，尽量得出多段短文本。"""
    parts = [p.strip() for p in text.splitlines() if p.strip()]
    if len(parts) < 2:
        parts = [p.strip() for p in re.split(r"(?<=[。！？!?；;])", text) if p.strip()]
    if len(parts) < 2:
        mid = max(len(text) // 2, 1)
        parts = [text[:mid].strip(), text[mid:].strip()]
    return [p for p in parts if p]


async def _synthesize_once(
    stream, text: str, sample_rate: int, t0: float, round_idx: int,
) -> tuple[bytes, float | None]:
    """单轮：feed → flush → 收集音频到 task-finished（30s 安全超时）。"""
    await stream.feed_text(text)
    t_feed = time.monotonic()
    await stream.flush()

    AUDIO_TIMEOUT_S = 30.0
    chunks: list[bytes] = []
    t_first_audio = None
    audio_gen = stream.audio()
    while True:
        try:
            chunk = await asyncio.wait_for(audio_gen.__anext__(), timeout=AUDIO_TIMEOUT_S)
            if t_first_audio is None:
                t_first_audio = time.monotonic()
            chunks.append(chunk)
        except StopAsyncIteration:
            break
        except TimeoutError:
            print(f"WARNING: [round {round_idx}] audio receive timed out after flush")
            break
    pcm = b"".join(chunks)
    duration_s = len(pcm) / (sample_rate * 2) if sample_rate > 0 else 0
    latency = t_first_audio - t_feed if t_first_audio else None
    print(
        f"[round {round_idx}] chars={len(text)}  latency={latency:.3f}s" if latency is not None
        else f"[round {round_idx}] chars={len(text)}  latency=N/A"
    )
    print(f"              audio={len(pcm)} bytes ({duration_s:.2f}s @ {sample_rate} Hz)")
    return pcm, latency


async def run_test(
    *,
    text: str,
    voice: str,
    model: str,
    sample_rate: int,
    base_url: str,
    api_key: str,
    output: str,
    workspace_id: str | None = None,
    multi_round: bool = False,
    idle_s: float = 0.0,
) -> None:
    print(f"Text:      {text[:80]}{'...' if len(text) > 80 else ''}")
    print(f"           {len(text)} chars")

    # ---- connect -----------------------------------------------------------
    provider = AlibabaBailianTTSProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        voice=voice,
        workspace_id=workspace_id,
    )

    ws_tag = f"  (X-DashScope-WorkSpace: {workspace_id})" if workspace_id else ""
    print(f"Connecting to {base_url}  (model={model}, voice={voice}){ws_tag} ...")
    t0 = time.monotonic()

    stream = await provider.open(voice=voice, sample_rate=sample_rate)
    print(f"Connected  ({time.monotonic() - t0:.2f}s)")

    # ---- rounds -------------------------------------------------------------
    rounds = _split_rounds(text) if multi_round else [text]
    if len(rounds) > 1:
        print(f"Multi-round: {len(rounds)} segments, idle {idle_s:.0f}s between rounds "
              f"(total {len(text)} chars)")

    all_pcm: list[bytes] = []
    latencies: list[float | None] = []
    for i, seg in enumerate(rounds):
        if i > 0:
            # 轮末清场对齐编排层：interrupt/轮间 reset 后连接处于无任务 idle——
            # idle >23s 时应能直接复用或自愈重连（v2 验证点 2）
            await stream.reset()
            if idle_s > 0:
                print(f"Idle {idle_s:.0f}s before round {i} ...")
                await asyncio.sleep(idle_s)
        pcm, latency = await _synthesize_once(stream, seg, sample_rate, t0, i)
        all_pcm.append(pcm)
        latencies.append(latency)

    elapsed = time.monotonic() - t0
    pcm = b"".join(all_pcm)
    total_bytes = len(pcm)
    duration_s = total_bytes / (sample_rate * 2) if sample_rate > 0 else 0

    print(f"\n{'='*60}")
    if len(rounds) > 1:
        print("Per-round first-byte latency: " + ", ".join(
            f"{lat:.3f}s" if lat is not None else "N/A" for lat in latencies
        ))
    ok = [lat for lat in latencies if lat is not None]
    if ok:
        print(f"First-byte latency:     {min(ok):.3f}s (best) / {sum(ok)/len(ok):.3f}s (avg)")
    print(f"Audio received:     {total_bytes} bytes, {duration_s:.2f}s @ {sample_rate} Hz PCM16 mono")
    print(f"Total time:         {elapsed:.2f}s")
    if elapsed > 0 and duration_s > 0:
        print(f"Real-time factor:   {duration_s / elapsed:.2f}x")

    # ---- save ---------------------------------------------------------------
    save_wav(output, pcm, sample_rate)
    print(f"Saved to:           {output}")

    await stream.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Real voice test for Alibaba Bailian TTS provider",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "text", nargs="?", default="",
        help="Text to synthesise (use --file for longer input)",
    )
    parser.add_argument(
        "--file", "-f",
        help="Read text from file instead of command-line argument",
    )
    parser.add_argument(
        "--voice", default=DEFAULT_VOICE,
        help=f"Voice name (default: {DEFAULT_VOICE})",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"DashScope TTS model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE,
        help=f"Output sample rate in Hz (default: {DEFAULT_SAMPLE_RATE})",
    )
    parser.add_argument(
        "--base-url", default=DASHSCOPE_TTS_URL,
        help="DashScope WebSocket endpoint",
    )
    parser.add_argument(
        "--workspace-id", default=None,
        help="DashScope workspace id: 仅附加 X-DashScope-WorkSpace 请求头。须配合 "
             "--base-url 指向 workspace 专属域名(wss://{id}.cn-beijing.maas.aliyuncs.com/"
             "api-ws/v1/inference)，否则业务空间模型 401/411",
    )
    parser.add_argument(
        "--output", "-o", default="/tmp/tts_output.wav",
        help="Output WAV file path (default: /tmp/tts_output.wav)",
    )
    parser.add_argument(
        "--multi-round", action="store_true",
        help="同一 WS 顺序合成多轮（文本按换行/句子切分），验证 task-finished 后 "
             "同连接可再 run-task（协议假设 1）",
    )
    parser.add_argument(
        "--idle-s", type=float, default=0.0,
        help="轮间无任务 idle 秒数（配合 --multi-round，建议 >23 验证服务端任务级 "
             "超时不再出现、无任务空闲连接可复用/自愈，协议假设 2）",
    )

    args = parser.parse_args()

    if args.workspace_id and "maas.aliyuncs.com" not in args.base_url:
        print(
            "ERROR: --workspace-id 只附加 X-DashScope-WorkSpace 头；业务空间模型还必须把 "
            "--base-url 指向 workspace 专属域名（wss://{id}.cn-beijing.maas.aliyuncs.com/"
            "api-ws/v1/inference）。",
            file=sys.stderr,
        )
        sys.exit(1)

    # Resolve text
    if args.file:
        filepath = Path(args.file)
        if not filepath.exists():
            print(f"ERROR: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        text = filepath.read_text(encoding="utf-8").strip()
        if not text:
            print("ERROR: input file is empty", file=sys.stderr)
            sys.exit(1)
    elif args.text:
        text = args.text
    else:
        print("ERROR: provide text as argument or use --file", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("ERROR: DASHSCOPE_API_KEY environment variable is not set.", file=sys.stderr)
        print("  export DASHSCOPE_API_KEY=sk-xxxxxxxx", file=sys.stderr)
        sys.exit(1)

    asyncio.run(
        run_test(
            text=text,
            voice=args.voice,
            model=args.model,
            sample_rate=args.sample_rate,
            base_url=args.base_url,
            api_key=api_key,
            output=args.output,
            workspace_id=args.workspace_id,
            multi_round=args.multi_round,
            idle_s=args.idle_s,
        )
    )


if __name__ == "__main__":
    main()
