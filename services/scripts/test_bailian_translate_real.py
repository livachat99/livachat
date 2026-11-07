#!/usr/bin/env python3
"""Real-device test for Alibaba LiveTranslate provider (qwen3.8-livetranslate-flash-realtime).

Feeds a local audio file to the DashScope realtime translate WebSocket API through
the production code path (omni.providers.alibaba_livetranslate.LiveTranslateProvider)
and prints source transcription + translated text.

``--raw-dump`` 走裸 WebSocket（不经 provider），原样打印服务端事件的字段名与统计——
用于确认 3.5→3.8 的事件改名（见 docs/qwen_livetranslate_realtime.md §4）。

Usage::

    python scripts/test_bailian_translate_real.py <audio_file> [options]

    # 裸抓包：旧字段（已弃用，回显不生效）vs 新字段 output_modalities
    python scripts/test_bailian_translate_real.py speech.wav --raw-dump \
        --session-field output_modalities --output-modalities text
    python scripts/test_bailian_translate_real.py speech.wav --raw-dump \
        --session-field output_modalities --output-modalities text,audio
    # D4 验证：不带 input_audio_transcription，3.8 仍应返回源文识别（ASR 常开）
    python scripts/test_bailian_translate_real.py speech.wav --raw-dump \
        --session-field output_modalities --output-modalities text --no-input-transcription

The audio file is auto-detected:
    - ``.wav`` files are decoded to PCM16 mono (any sample rate, resampled
      on the fly with soxr).
    - ``.pcm`` / ``.raw`` files are read as raw PCM16 mono at 16 kHz.

Requires:
    - DASHSCOPE_API_KEY environment variable (Aliyun DashScope API key).

Examples::

    export DASHSCOPE_API_KEY=sk-xxxxxxxx
    python scripts/test_bailian_translate_real.py speech.wav --target-language en

    # 业务空间专属域名 + workspace 头（生产配置同款组合）
    python scripts/test_bailian_translate_real.py speech.wav \
        --base-url wss://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime \
        --workspace-id llm-1tmsqifo51cwfadr

    # 组合矩阵：专属域名±头、旧域名±头（省略 --workspace-id 即不带头）
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import os
import struct
import sys
import time
import wave
from pathlib import Path

# Ensure the services/ package root is on sys.path so we can import from omni/
_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from media.resample import Resampler
from omni.base import OmniEventType
from omni.providers.alibaba_livetranslate import LiveTranslateProvider

DASHSCOPE_TRANSLATE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen3.8-livetranslate-flash-realtime"
INPUT_SAMPLE_RATE = 16000
FEED_CHUNK_MS = 100


def build_url(base_url: str, model: str) -> str:
    """Append ?model= when the caller passed a bare endpoint."""
    if "model=" in base_url:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}model={model}"


def load_wav(path: str, target_rate: int) -> bytes:
    """Decode a WAV file to PCM16 mono bytes at *target_rate*."""
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
        frame_sz = nchannels * 2
        mono = bytearray(len(raw) // nchannels)
        for i in range(0, len(raw), frame_sz):
            mono[i // nchannels : i // nchannels + 2] = raw[i : i + 2]
        raw = bytes(mono)

    if src_rate != target_rate:
        resampler = Resampler(src_rate, target_rate)
        raw = resampler.process(raw) + resampler.flush()

    return raw


def iter_chunks(data: bytes, chunk_bytes: int) -> list[bytes]:
    return [data[i : i + chunk_bytes] for i in range(0, len(data), chunk_bytes)]


async def run_test(
    *,
    audio_path: str,
    base_url: str,
    api_key: str,
    model: str,
    workspace_id: str | None,
    source_language: str,
    target_language: str,
    realtime: bool,
    wait_seconds: float,
    trail_silence_ms: int = 2000,
    voice: str = "Tina",
) -> None:
    ext = Path(audio_path).suffix.lower()
    if ext == ".wav":
        pcm = load_wav(audio_path, INPUT_SAMPLE_RATE)
    else:
        pcm = Path(audio_path).read_bytes()

    duration_s = len(pcm) / (INPUT_SAMPLE_RATE * 2)
    print(f"Loaded  {audio_path}")
    print(f"        {len(pcm)} bytes, {INPUT_SAMPLE_RATE} Hz, {duration_s:.2f}s PCM16 mono")

    url = build_url(base_url, model)
    ws_tag = f"  (X-DashScope-WorkSpace: {workspace_id})" if workspace_id else ""
    print(f"Connecting to {url}{ws_tag} ...")
    t0 = time.monotonic()

    provider = LiveTranslateProvider(
        base_url=url,
        api_key=api_key,
        model=model,
        voice=voice,
        source_language=source_language,
        target_language=target_language,
        input_sample_rate=INPUT_SAMPLE_RATE,
        workspace_id=workspace_id,
    )
    # 生产路径同款：lecture engine 以 text-only 打开（audio 输出不消费）
    stream = await provider.open(sample_rate=INPUT_SAMPLE_RATE, modalities=("text",))
    print(f"Connected  ({time.monotonic() - t0:.2f}s)")

    stats = {
        "src_deltas": 0, "src_done": 0, "responses": 0,
        "text_deltas": 0, "audio_bytes": 0, "errors": 0,
    }
    last_activity = [time.monotonic()]

    async def consume() -> None:
        try:
            async for ev in stream.events():
                last_activity[0] = time.monotonic()
                if ev.type == OmniEventType.INPUT_TRANSCRIPTION_DELTA:
                    stats["src_deltas"] += 1
                    print(f"  [src] {ev.text}")
                elif ev.type == OmniEventType.INPUT_TRANSCRIPTION_DONE:
                    stats["src_done"] += 1
                    print(f"  [src done] {ev.text}")
                elif ev.type == OmniEventType.RESPONSE_STARTED:
                    stats["responses"] += 1
                    print(f"  [translate #{stats['responses']} started]")
                elif ev.type == OmniEventType.TEXT_DELTA:
                    stats["text_deltas"] += 1
                    print(f"  [dst] {ev.text}")
                elif ev.type == OmniEventType.AUDIO_DELTA:
                    stats["audio_bytes"] += len(ev.audio or b"")
                elif ev.type == OmniEventType.RESPONSE_DONE:
                    print(f"  [translate #{stats['responses']} done]")
                elif ev.type == OmniEventType.ERROR:
                    stats["errors"] += 1
                    print(f"  [ERROR] {ev.error_message}")
        except asyncio.CancelledError:
            pass

    consume_task = asyncio.create_task(consume())

    chunk_bytes = INPUT_SAMPLE_RATE * 2 * FEED_CHUNK_MS // 1000
    chunks = iter_chunks(pcm, chunk_bytes)
    print(f"\nFeeding {len(chunks)} chunks ({FEED_CHUNK_MS}ms each"
          f"{', realtime pacing' if realtime else ', max speed'}) ...")
    for chunk in chunks:
        await stream.feed_audio(chunk)
        if realtime:
            await asyncio.sleep(FEED_CHUNK_MS / 1000)
    print(f"Audio fed  ({time.monotonic() - t0:.2f}s)")

    # 尾随静音（默认 2s）：服务端 VAD 借此闭合尾段（同 asr 真机脚本约定）
    for _ in range(max(1, trail_silence_ms // FEED_CHUNK_MS)):
        await stream.feed_audio(b"\x00" * chunk_bytes)
        await asyncio.sleep(0.05)

    # 有界等待：直到连续 wait_seconds 无新事件（翻译响应可能晚于喂帧 2.8s+ 到达）
    print(f"Waiting up to {wait_seconds}s for trailing events ...")
    while time.monotonic() - last_activity[0] < wait_seconds:
        await asyncio.sleep(0.2)

    consume_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await consume_task

    elapsed = time.monotonic() - t0
    print(f"\n{'=' * 60}")
    print(f"Connect+feed+wait: {elapsed:.2f}s   audio: {duration_s:.2f}s")
    print(f"src deltas/done:   {stats['src_deltas']}/{stats['src_done']}")
    print(f"responses:         {stats['responses']}   text deltas: {stats['text_deltas']}")
    print(f"audio bytes:       {stats['audio_bytes']}   errors: {stats['errors']}")

    await stream.close()


async def run_raw_dump(
    *,
    audio_path: str,
    url: str,
    api_key: str,
    workspace_id: str | None,
    source_language: str,
    target_language: str,
    session_field: str,
    output_modalities: list[str],
    no_input_transcription: bool,
    voice: str,
    realtime: bool,
    wait_seconds: float,
    trail_silence_ms: int,
    show_json: bool,
) -> None:
    """裸 WebSocket 抓包模式：不经 provider，原样打印服务端事件与其字段名。

    用途（docs/qwen_livetranslate_realtime.md §4）：确认 3.8 的增量文本事件字段名、
    `output_modalities` / 旧 `modalities` 的回执形态。provider 的 receive loop 会按已知
    事件名解析，未经它过滤才能看到线上真实事件。
    """
    import collections
    import json

    import websockets

    ext = Path(audio_path).suffix.lower()
    pcm = load_wav(audio_path, INPUT_SAMPLE_RATE) if ext == ".wav" else Path(audio_path).read_bytes()
    duration_s = len(pcm) / (INPUT_SAMPLE_RATE * 2)
    print(f"Loaded  {audio_path}: {len(pcm)} bytes, {duration_s:.2f}s PCM16 mono")

    headers = {"Authorization": f"Bearer {api_key}"}
    if workspace_id:
        headers["X-DashScope-WorkSpace"] = workspace_id
    print(f"Connecting to {url} ...")
    t0 = time.monotonic()
    ws = await websockets.connect(
        url, additional_headers=headers, ping_interval=20, ping_timeout=20, close_timeout=1,
    )

    first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    print(f"  recv {first.get('type')}  ({time.monotonic() - t0:.2f}s)")

    session: dict = {
        "voice": voice,
        "input_audio_format": "pcm",
        "output_audio_format": "pcm",
        "sample_rate": INPUT_SAMPLE_RATE,
        "translation": {"language": target_language},
    }
    if not no_input_transcription:
        session["input_audio_transcription"] = {
            "model": "qwen3-asr-flash-realtime",
            "language": source_language,
        }
    session[session_field] = output_modalities
    update = {"event_id": "evt_probe_update", "type": "session.update", "session": session}
    print(f"  send session.update  {session_field}={output_modalities} voice={voice!r} "
          f"{source_language}->{target_language}")
    await ws.send(json.dumps(update))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    print(f"  recv {ack.get('type')}: {json.dumps({k: v for k, v in ack.items() if k != 'session'}, ensure_ascii=False)[:200]}")
    if ack.get("type") == "error":
        print("  ⚠ session.update 被拒:", json.dumps(ack.get("error"), ensure_ascii=False)[:300])
    elif ack.get("type") == "session.updated":
        sess = ack.get("session", {})
        echo_keys = [k for k in ("modalities", "output_modalities", "voice") if k in sess]
        print(f"  session.updated 回执含: {json.dumps({k: sess[k] for k in echo_keys}, ensure_ascii=False)}")

    counts = collections.Counter()
    samples: dict[str, tuple[list[str], dict]] = {}
    audio_bytes = 0
    text_like = (".delta", ".text", ".done", ".completed")

    async def receiver() -> None:
        nonlocal audio_bytes
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type", "?")
                counts[t] += 1
                if t not in samples:
                    keys = sorted(k for k in msg.keys() if k != "event_id")
                    sample = {
                        k: (f"<{len(v)} chars>" if isinstance(v, str) and len(v) > 120 else v)
                        for k, v in msg.items() if k not in ("event_id",)
                    }
                    samples[t] = (keys, sample)
                if t == "response.audio.delta":
                    audio_bytes += len(msg.get("delta", ""))
                if show_json:
                    print(f"    << {json.dumps({k: (f'<{len(v)} chars>' if isinstance(v, str) and len(v) > 120 else v) for k, v in msg.items()}, ensure_ascii=False)[:400]}")
                elif t.endswith(text_like):
                    body = msg.get("delta") or msg.get("text") or msg.get("transcript") or msg.get("stash") or ""
                    if body:
                        print(f"    << {t}: {body[:80]!r}")
                if t == "session.finished":
                    return
        except asyncio.CancelledError:
            pass
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            print(f"  receiver ended: {type(exc).__name__} {exc}")

    recv_task = asyncio.create_task(receiver())

    chunk_bytes = INPUT_SAMPLE_RATE * 2 * FEED_CHUNK_MS // 1000
    chunks = iter_chunks(pcm, chunk_bytes)
    print(f"Feeding {len(chunks)} chunks{' (realtime)' if realtime else ''} ...")
    for n, chunk in enumerate(chunks, 1):
        await ws.send(json.dumps({
            "event_id": f"evt_a{n}",
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode(),
        }))
        if realtime:
            await asyncio.sleep(FEED_CHUNK_MS / 1000)
    for _ in range(max(1, trail_silence_ms // FEED_CHUNK_MS)):  # 尾随静音（默认 2s），闭合尾段
        await ws.send(json.dumps({
            "event_id": "evt_silence", "type": "input_audio_buffer.append",
            "audio": base64.b64encode(b"\x00" * chunk_bytes).decode(),
        }))
        await asyncio.sleep(0.05)

    print(f"Audio fed ({time.monotonic() - t0:.2f}s); waiting up to {wait_seconds}s ...")
    deadline = time.monotonic() + duration_s + wait_seconds + 15
    while time.monotonic() < deadline and not recv_task.done():
        await asyncio.sleep(0.2)

    recv_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await recv_task

    print(f"\n{'=' * 72}\n事件类型统计（裸 WS，{time.monotonic() - t0:.1f}s 窗口）")
    for t, n in counts.most_common():
        keys, sample = samples.get(t, ([], {}))
        print(f"  {n:4}  {t}")
        print(f"        keys={keys}")
        print(f"        first={json.dumps(sample, ensure_ascii=False)[:220]}")
    print(f"audio delta base64 累计: {audio_bytes} chars")

    with contextlib.suppress(Exception):
        await ws.send(json.dumps({"event_id": "evt_finish", "type": "session.finish"}))
        await asyncio.wait_for(ws.recv(), timeout=5)
    with contextlib.suppress(Exception):
        await ws.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Real-device test for Alibaba LiveTranslate provider",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("audio_file", help="Path to .wav or .pcm audio file")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Model name (default: {DEFAULT_MODEL}); appended as ?model= when absent",
    )
    parser.add_argument(
        "--base-url", default=DASHSCOPE_TRANSLATE_URL,
        help="WebSocket endpoint (bare URL or with ?model=)",
    )
    parser.add_argument(
        "--workspace-id", default=None,
        help="DashScope workspace id: 仅附加 X-DashScope-WorkSpace 请求头。官方 realtime "
             "族地址形态为 wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime，"
             "头为可选项——组合矩阵见模块 docstring",
    )
    parser.add_argument("--source-language", default="zh", help="ASR source language (default: zh)")
    parser.add_argument("--target-language", default="en", help="Translation target language (default: en)")
    parser.add_argument(
        "--realtime", action="store_true",
        help="Feed audio at realtime speed instead of as fast as possible",
    )
    parser.add_argument(
        "--wait-seconds", type=float, default=8.0,
        help="Bounded wait for trailing events after feeding stops (default: 8)",
    )
    parser.add_argument(
        "--trail-silence-ms", type=int, default=2000,
        help="尾随静音时长（毫秒，默认 2000）。用于观察服务端 VAD 闭合阈值："
             "客户端 mute 后补发 1s 全零静音（SilenceFlushGate），对照实验用 1000 vs 2000",
    )
    parser.add_argument(
        "--raw-dump", action="store_true",
        help="裸 WS 抓包模式：不经 provider，原样打印服务端事件与字段名"
             "（用于确认 qwen3.8 的 .delta 事件字段、output_modalities 回执）",
    )
    parser.add_argument(
        "--session-field", default="output_modalities", choices=["modalities", "output_modalities"],
        help="输出模态字段名（output_modalities=3.8 新字段；modalities=旧字段对照）",
    )
    parser.add_argument(
        "--output-modalities", default="text",
        help="输出模态取值：text 或 text,audio（默认 text）",
    )
    parser.add_argument(
        "--no-input-transcription",
        action="store_true",
        help="省略 input_audio_transcription（D4 验证：3.8 ASR 常开，不带该字段仍应返回识别事件）",
    )
    parser.add_argument(
        "--voice", default="Tina",
        help="输出音色（生产配置为 Tina；默认 Tina）",
    )
    parser.add_argument(
        "--show-json", action="store_true",
        help="--raw-dump 下打印每条原始事件的完整 JSON（长 base64 截断）",
    )

    args = parser.parse_args()

    if args.workspace_id and "maas.aliyuncs.com" not in args.base_url:
        print(
            "WARNING: --workspace-id 通常与 workspace 专属域名配套使用"
            "（wss://{id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime）；"
            "realtime 族文档中该头为可选项，继续执行。",
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

    url = build_url(args.base_url, args.model)
    if args.raw_dump:
        asyncio.run(
            run_raw_dump(
                audio_path=args.audio_file,
                url=url,
                api_key=api_key,
                workspace_id=args.workspace_id,
                source_language=args.source_language,
                target_language=args.target_language,
                session_field=args.session_field,
                output_modalities=[m.strip() for m in args.output_modalities.split(",")],
                no_input_transcription=args.no_input_transcription,
                voice=args.voice,
                realtime=args.realtime,
                wait_seconds=args.wait_seconds,
                trail_silence_ms=args.trail_silence_ms,
                show_json=args.show_json,
            )
        )
        return

    asyncio.run(
        run_test(
            audio_path=args.audio_file,
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            workspace_id=args.workspace_id,
            source_language=args.source_language,
            target_language=args.target_language,
            realtime=args.realtime,
            wait_seconds=args.wait_seconds,
            trail_silence_ms=args.trail_silence_ms,
            voice=args.voice,
        )
    )


if __name__ == "__main__":
    main()
