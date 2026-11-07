#!/usr/bin/env python3
"""Real-device smoke test for Alibaba Qwen-Omni Realtime provider.

Usage:
    DASHSCOPE_API_KEY=... python services/scripts/test_omni_real.py [wav_file] [voice] [output_wav]

Default WAV file: ~/projects/asr_test1.wav
Default voice: Tina
Default output: /tmp/test_omni_real_<voice>.wav

Workspace 组合矩阵（与 asr/tts/translate 探针同款；realtime 族文档中
X-DashScope-WorkSpace 为可选项）：

    # 业务空间专属域名 + workspace 头（生产配置同款组合）
    python services/scripts/test_omni_real.py --base-url \\
        wss://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime \\
        --workspace-id llm-1tmsqifo51cwfadr

    # 省略 --workspace-id 即不带头；--base-url 省略则用公共域名（基线）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import wave
from pathlib import Path

DASHSCOPE_OMNI_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen3.8-omni-flash-realtime"


def build_url(base_url: str, model: str) -> str:
    """Append ?model= when the caller passed a bare endpoint."""
    if "model=" in base_url:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}model={model}"


def read_pcm(wav_path: str) -> tuple[bytes, int]:
    """Read a WAV file and return (pcm_bytes, sample_rate)."""
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        if nch != 1:
            print(f"Warning: {nch} channels, using only channel 0")
        if sw != 2:
            print(f"Warning: {sw * 8}-bit samples, expected 16-bit")
        pcm = wf.readframes(wf.getnframes())
        # If multi-channel, extract channel 0
        if nch > 1:
            frame_size = nch * sw
            pcm = bytes(
                pcm[i : i + sw] for i in range(0, len(pcm), frame_size)
            )
    return pcm, sr


async def main() -> None:
    parser = argparse.ArgumentParser(description="Real-device smoke test for Alibaba Qwen-Omni Realtime")
    parser.add_argument("wav_file", nargs="?", default=os.path.expanduser("~/projects/asr_test1.wav"))
    parser.add_argument("voice", nargs="?", default="Tina")
    parser.add_argument("output_wav", nargs="?", default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--base-url", default=DASHSCOPE_OMNI_URL,
                        help="WebSocket endpoint（裸地址或含 ?model=；默认公共域名）")
    parser.add_argument("--workspace-id", default=None,
                        help="DashScope workspace id：仅附加 X-DashScope-WorkSpace 头；"
                             "官方推荐与 workspace 专属域名配套（wss://{id}.cn-beijing.maas.aliyuncs.com"
                             "/api-ws/v1/realtime），头为可选项")
    args = parser.parse_args()

    if args.workspace_id and "maas.aliyuncs.com" not in args.base_url:
        print("WARNING: --workspace-id 通常与 workspace 专属域名配套使用（realtime 族该头为可选项），"
              "继续执行。", file=sys.stderr)

    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("Set DASHSCOPE_API_KEY", file=sys.stderr)
        sys.exit(1)

    wav_path = args.wav_file
    voice = args.voice
    out_wav = args.output_wav or f"/tmp/test_omni_real_{voice}.wav"
    if not Path(wav_path).exists():
        print(f"WAV file not found: {wav_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading audio: {wav_path}")
    pcm, sample_rate = read_pcm(wav_path)
    duration_s = len(pcm) / (sample_rate * 2)  # 16-bit = 2 bytes per sample
    print(f"  Sample rate: {sample_rate} Hz, duration: {duration_s:.1f}s, size: {len(pcm)} bytes")

    from omni.providers.alibaba_omni import AlibabaOmniProvider

    url = build_url(args.base_url, args.model)
    ws_tag = f"  (X-DashScope-WorkSpace: {args.workspace_id})" if args.workspace_id else ""
    print(f"Connecting to {url}{ws_tag}")
    provider = AlibabaOmniProvider(
        base_url=url,
        api_key=api_key,
        model=args.model,
        voice=voice,
        workspace_id=args.workspace_id,
    )

    print(f"Opening omni stream (voice={voice})...")
    stream = await provider.open(
        sample_rate=sample_rate,
        system_prompt="你是一个友好的语音助手。请用简短的中文回答问题。",
        voice=voice,
        modalities=("text", "audio"),
    )
    print("  connected.")

    # Feed audio in 40ms chunks (640 bytes at 16kHz mono 16-bit)
    chunk_ms = 40
    chunk_size = int(sample_rate * 2 * chunk_ms / 1000)  # bytes per chunk
    offset = 0
    while offset < len(pcm):
        chunk = pcm[offset : offset + chunk_size]
        offset += len(chunk)
        await stream.feed_audio(chunk)
        await asyncio.sleep(chunk_ms / 1000 * 0.5)  # feed at 2x realtime

    print(f"All audio fed ({offset} bytes). Waiting for omni events (timeout 30s)...")

    all_audio = bytearray()
    all_text: list[str] = []
    transcriptions: list[str] = []
    event_count = 0

    try:
        async for ev in stream.events():
            event_count += 1
            label = ev.type.value

            if label == "input_transcription_delta":
                print(f"  [{label}] delta={ev.text!r}")
                transcriptions.append(ev.text)
            elif label == "input_transcription_done":
                print(f"  [{label}] final={ev.text!r}")
                transcriptions.append(f"[FINAL] {ev.text}")
            elif label == "speech_started" or label == "speech_stopped":
                print(f"  [{label}]")
            elif label == "response_started":
                print(f"  [{label}] response_id={ev.response_id}")
            elif label == "text_delta":
                print(f"  [{label}] {ev.text!r}" if len(ev.text) < 100 else f"  [{label}] len={len(ev.text)}")
                all_text.append(ev.text)
            elif label == "audio_delta":
                all_audio.extend(ev.audio)
                if len(all_audio) % (sample_rate * 2) == 0:
                    print(f"  [{label}] audio accumulated: {len(all_audio) / (sample_rate * 2):.1f}s")
            elif label == "response_done":
                print(f"  [{label}] → full text: {''.join(all_text)!r}")
                print("  → response complete")
                break
            elif label == "error":
                print(f"  [{label}] ERROR: {ev.error_message}")
                break

        await asyncio.sleep(0.5)
    except TimeoutError:
        print("Timed out waiting for events")
    finally:
        await stream.close()

    print("\n--- Results ---")
    print(f"Total events: {event_count}")
    print(f"Transcriptions: {transcriptions}")
    print(f"Full response text: {''.join(all_text)!r}")

    if all_audio:
        out_path = out_wav
        with wave.open(out_path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(all_audio))
        print(f"Output audio: {out_path} ({len(all_audio) / (sample_rate * 2):.1f}s)")
    else:
        print("No audio received")


if __name__ == "__main__":
    asyncio.run(main())
