#!/usr/bin/env python3
"""Measure end-to-end ASR wrap-up latency through the real server stack.

Skips the phone: connects to a locally running livachat-server as a fake device
(WebSocket + opus frames, same messages the Android SDK sends), pushes a speech
WAV at real-time pace, and measures "last speech frame sent → transcription
is_final received".  That is the quantity a user perceives, including the
gateway + orchestrator hops and whatever the server's yaml actually configures.

Usage::

    # 1) start the server with real configs/keys; asr_only keeps LLM/TTS out
    #    (cwd must be services/)
    env $(grep -v '^#' .env | grep -v '^$' | xargs) \
      LIVACHAT_PIPELINE_MODE=asr_only \
      .venv/bin/python -m uvicorn app.main:app --port 18899

    # 2) run the probe
    services/.venv/bin/python services/scripts/measure_e2e_asr_final_latency.py \
      /tmp/asr_probe_a.wav --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from websockets.asyncio.client import connect as ws_connect

# Ensure the services/ package root and this scripts dir are importable
_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
for p in (str(_SERVICES), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from livachat_protocol.generated.protocol_models import (  # noqa: E402
    ClientAudioPayload,
    ClientSessionStartConfig,
    ClientSessionStartPayload,
)
from measure_asr_final_latency import split_frames, trim_trailing_silence  # noqa: E402
from media.opus import OpusEncoder  # noqa: E402
from media.resample import Resampler  # noqa: E402
from gateway.auth import wire_digest  # noqa: E402
from shared.utils.time import now_ms  # noqa: E402

FRAME_MS = 20


def load_wav_16k_mono(path: str, target_rate: int = 16000) -> bytes:
    """Decode WAV → PCM16 mono bytes at *target_rate* (same rules as the cloud probe)."""
    import struct
    import wave

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


class FakeDevice:
    def __init__(self, url: str) -> None:
        self._url = url
        self._ws = None
        self._seq = 0
        self.transcriptions: list[tuple[float, dict]] = []

    async def __aenter__(self) -> FakeDevice:
        self._ws = await ws_connect(self._url, max_size=None)
        self._recv_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, *args: object) -> None:
        self._recv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._recv_task
        with contextlib.suppress(Exception):
            await self._ws.close()

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "transcription":
                    self.transcriptions.append((time.monotonic(), msg.get("payload", {})))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _send(self, msg_type: str, payload: dict) -> None:
        await self._ws.send(json.dumps(
            {"type": msg_type, "seq": self._next_seq(), "timestamp": now_ms(), "payload": payload}
        ))

    async def session_start(self, *, sample_rate: int) -> None:
        cfg = ClientSessionStartConfig(
            enable_video=False,
            audio_sample_rate=sample_rate,
            vad_sensitivity="medium",
            client_version="android-1.0.0",  # schema 约束 ^(ios|android)-\d+\.\d+\.\d+(\.\d+)?$
            timezone="Asia/Shanghai",
        )
        await self._send("session.start", ClientSessionStartPayload(session_id=None, config=cfg).model_dump())

    async def audio_frame(self, opus: bytes, *, vad_state: str) -> None:
        import base64

        payload = ClientAudioPayload(
            data=base64.b64encode(opus).decode(), codec="opus",
            duration_ms=FRAME_MS, vad_state=vad_state,
        )
        await self._send("audio", payload.model_dump())


async def run_once(url: str, speech_pcm: bytes, sample_rate: int, trailing_silence_ms: int,
                   grace_ms: int, silence_amp: int) -> dict:
    frame_samples = int(sample_rate * FRAME_MS / 1000)
    frame_bytes = frame_samples * 2
    encoder = OpusEncoder(sample_rate=sample_rate, frame_ms=FRAME_MS)
    speech_frames = [encoder.encode(fr) for fr in split_frames(speech_pcm, frame_bytes)]
    # 尾随音频必须是"真实房间底噪"而非全零：全零经 Opus 编码后是单一恒定包，
    # 解码出的不是静音（实测服务端解码 RMS≈365 恒定周期波形），云端 VAD 不会
    # 判静音、句子永不闭合。生产里麦克风发的就是底噪（SilenceFlushGate 的全零
    # 只出现在 mute 的 1s 补发窗口），故用低幅随机噪声模拟。
    def _noise_frame() -> bytes:
        if silence_amp <= 0:
            return encoder.encode(b"\x00" * frame_bytes)
        import array as _array
        import random as _random
        return encoder.encode(
            _array.array("h", (_random.randint(-silence_amp, silence_amp) for _ in range(frame_samples))).tobytes()
        )

    result: dict = {"latency_ms": None, "interims": 0, "final": "", "error": None}
    async with FakeDevice(url) as dev:
        await dev.session_start(sample_rate=sample_rate)
        # 等 session.ready + 首个 status（idle → listening），否则首帧可能落在未就绪态
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(dev.transcriptions) == 0:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)

        t0 = time.monotonic()
        index = 0
        for opus in speech_frames:
            await dev.audio_frame(opus, vad_state="speech")
            index += 1
            delay = t0 + index * FRAME_MS / 1000 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        t_last_speech = time.monotonic()

        final_at: float | None = None
        for _ in range(trailing_silence_ms // FRAME_MS):
            await dev.audio_frame(_noise_frame(), vad_state="silence")
            index += 1
            delay = t0 + index * FRAME_MS / 1000 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            arrivals = [t for t, p in dev.transcriptions if p.get("is_final") and t >= t_last_speech]
            if arrivals:
                final_at = min(arrivals)
                if time.monotonic() - final_at >= grace_ms / 1000:
                    break
        if final_at is None:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                arrivals = [t for t, p in dev.transcriptions if p.get("is_final") and t >= t_last_speech]
                if arrivals:
                    final_at = min(arrivals)
                    break
                await asyncio.sleep(0.02)

    finals = [(t, p) for t, p in dev.transcriptions if p.get("is_final")]
    result["interims"] = sum(1 for _, p in dev.transcriptions if not p.get("is_final"))
    result["final"] = finals[0][1].get("text", "") if finals else ""
    result["mid_speech_finals"] = sum(1 for t, _ in finals if t < t_last_speech)
    if final_at is not None:
        result["latency_ms"] = (final_at - t_last_speech) * 1000
    return result


async def main_async(args: argparse.Namespace) -> None:
    pcm = load_wav_16k_mono(args.audio, args.sample_rate)
    pcm = trim_trailing_silence(pcm, int(args.sample_rate * FRAME_MS / 1000) * 2, args.speech_threshold)
    print(f"Loaded {args.audio} → 语音 {len(pcm) / (args.sample_rate * 2) * 1000:.0f}ms（已裁尾部静音）")
    print(f"URL {args.url}\n尾静音 {args.trailing_silence_ms}ms, {args.repeats} 次\n")

    latencies: list[float] = []
    for i in range(1, args.repeats + 1):
        speaker = uuid.uuid4()
        # --token 是明文 secret；wire token 必须是摘要（docs/server_auth.md §3.1）
        url = f"{args.url}?token={wire_digest(args.token)}&device_id={speaker}"
        try:
            r = await run_once(url, pcm, args.sample_rate, args.trailing_silence_ms, args.grace_ms,
                               args.silence_amp)
        except Exception as exc:
            print(f"  run {i}/{args.repeats}  ERROR: {type(exc).__name__}: {exc}")
            continue
        if r["latency_ms"] is not None:
            latencies.append(r["latency_ms"])
            note = f"  句中切断={r['mid_speech_finals']}" if r["mid_speech_finals"] else ""
            print(f"  run {i}/{args.repeats}  收尾={r['latency_ms']:.0f}ms  interims={r['interims']}{note}")
            print(f"          final: {r['final']!r}")
        else:
            print(f"  run {i}/{args.repeats}  未收到 final（interims={r['interims']}）")

    if latencies:
        print(f"\nE2E 收尾时延：mean={sum(latencies) / len(latencies):.0f}ms "
              f"min={min(latencies):.0f} max={max(latencies):.0f} (n={len(latencies)})")


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E ASR wrap-up latency probe (fake device → local server → cloud ASR)")
    parser.add_argument("audio")
    parser.add_argument("--url", default="ws://127.0.0.1:18899/v1/ws/stream")
    parser.add_argument("--token", default=os.environ.get("LIVACHAT_E2E_TOKEN", ""),
                        help="明文 secret（脚本内派生 sha1 摘要后发送）")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--trailing-silence-ms", type=int, default=5000)
    parser.add_argument("--grace-ms", type=int, default=400)
    parser.add_argument("--speech-threshold", type=int, default=300)
    parser.add_argument("--silence-amp", type=int, default=150,
                        help="尾随底噪幅度（int16，0=全零——注意全零经 Opus 往返后不是静音）")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
