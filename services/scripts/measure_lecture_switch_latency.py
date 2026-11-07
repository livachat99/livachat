#!/usr/bin/env python3
"""Measure lecture mode switch latency through the real server stack.

Skips the phone: connects to a locally running livachat-server as a fake device
(WebSocket + opus frames, same messages the Android SDK sends), then measures
"session.change sent → session.changed received" for entering AND exiting
lecture mode.  This is the quantity a user perceives and the one that was
15.1s in the 2026-09-14 production log (docs/translate_lecture_improvements.md).

Usage::

    # 1) start the server with real configs/keys (cwd must be services/)
    env $(grep -v '^#' .env | grep -v '^$' | xargs) \
      .venv/bin/python -m uvicorn app.main:app --port 18899

    # 2) run the probe
    services/.venv/bin/python services/scripts/measure_lecture_switch_latency.py \
      --token dev-livachat-token --repeats 2

Expected after the fix: entry ≈1s (ASR/TTS close 并发、各限 1s 超时), exit ≈1s
(LiveTranslate close 限 1s).  Before the fix: entry ≈15.1s, exit ≈10s.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from websockets.asyncio.client import connect as ws_connect

_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
for p in (str(_SERVICES), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from livachat_protocol.generated.protocol_models import (  # noqa: E402
    ClientAudioPayload,
    ClientSessionChangePayload,
    ClientSessionStartConfig,
    ClientSessionStartPayload,
)
from gateway.auth import wire_digest  # noqa: E402
from media.opus import OpusEncoder  # noqa: E402
from shared.utils.time import now_ms  # noqa: E402

FRAME_MS = 20


class FakeDevice:
    def __init__(self, url: str) -> None:
        self._url = url
        self._ws = None
        self._seq = 0
        self.acks: list[tuple[str, float]] = []  # (type, monotonic_ts)

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
                t = msg.get("type")
                if t in ("session.ready", "session.changed", "status"):
                    self.acks.append((t if t != "status" else f"status:{msg.get('status')}", time.monotonic()))
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
            client_version="android-1.0.0",  # schema ^(ios|android)-\d+\.\d+\.\d+(\.\d+)?$
            timezone="Asia/Shanghai",
        )
        await self._send("session.start", ClientSessionStartPayload(session_id=None, config=cfg).model_dump())

    async def session_change(self, *, mode: str, my_language: str = "", local_language: str = "",
                             usecase: str = "") -> None:
        payload = ClientSessionChangePayload(
            mode=mode,
            my_language=my_language or None,
            local_language=local_language or None,
            usecase=usecase or None,
        )
        await self._send("session.change", payload.model_dump(exclude_none=True))

    async def audio_frame(self, opus: bytes, *, vad_state: str) -> None:
        payload = ClientAudioPayload(
            data=base64.b64encode(opus).decode(), codec="opus",
            duration_ms=FRAME_MS, vad_state=vad_state,
        )
        await self._send("audio", payload.model_dump())

    async def wait_ack(self, msg_type: str, *, since: float, timeout: float = 25.0) -> float | None:
        """Return latency (s) of the first *msg_type* ack arriving after *since*."""
        deadline = since + timeout
        while time.monotonic() < deadline:
            for t, at in self.acks:
                if t == msg_type and at >= since:
                    return at - since
            await asyncio.sleep(0.005)
        return None


async def run_once(url: str, sample_rate: int, feed_ms: int, lecture_hold_ms: int) -> dict:
    frame_samples = int(sample_rate * FRAME_MS / 1000)
    encoder = OpusEncoder(sample_rate=sample_rate, frame_ms=FRAME_MS)

    # 低幅底噪（非全零：全零经 Opus 往返不是静音，云端 VAD 不闭合句子）
    import array as _array
    import random as _random

    def _noise_frame() -> bytes:
        return encoder.encode(
            _array.array("h", (_random.randint(-150, 150) for _ in range(frame_samples))).tobytes()
        )

    result: dict = {"entry_ms": None, "exit_ms": None, "error": None}
    async with FakeDevice(url) as dev:
        await dev.session_start(sample_rate=sample_rate)
        ready = await dev.wait_ack("session.ready", since=time.monotonic(), timeout=5.0)
        if ready is None:
            result["error"] = "session.ready 未到达"
            return result
        await asyncio.sleep(0.3)  # 等 idle → listening 的初始 status

        # 喂一小段音频（真实感；ASR/TTS 已在 session.start 时 eager open）
        feed_frames = max(0, feed_ms) // FRAME_MS
        t_feed = time.monotonic()
        for i in range(feed_frames):
            await dev.audio_frame(_noise_frame(), vad_state="silence")
            delay = t_feed + (i + 1) * FRAME_MS / 1000 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)

        # ── 进入 lecture ──
        t0 = time.monotonic()
        await dev.session_change(mode="translate", my_language="zh", local_language="en", usecase="lecture")
        entry = await dev.wait_ack("session.changed", since=t0)
        result["entry_ms"] = None if entry is None else entry * 1000

        # 保持 lecture 一小段（同时继续喂音频，模拟真实使用）
        hold_frames = max(0, lecture_hold_ms) // FRAME_MS
        for _ in range(hold_frames):
            await dev.audio_frame(_noise_frame(), vad_state="silence")
            await asyncio.sleep(FRAME_MS / 1000)

        # ── 退出 lecture ──
        t1 = time.monotonic()
        await dev.session_change(mode="chat")
        exit_lat = await dev.wait_ack("session.changed", since=t1)
        result["exit_ms"] = None if exit_lat is None else exit_lat * 1000

    return result


async def main_async(args: argparse.Namespace) -> None:
    print(f"URL {args.url}\n喂音频 {args.feed_ms}ms, lecture 停留 {args.lecture_hold_ms}ms, "
          f"{args.repeats} 次\n期望：进入 ≈1s、退出 ≈1s（改前 15.1s / 10s）\n")
    entries: list[float] = []
    exits: list[float] = []
    for i in range(1, args.repeats + 1):
        device = uuid.uuid4()
        # --token 是明文 secret；wire token 必须是摘要（docs/server_auth.md §3.1）
        url = f"{args.url}?token={wire_digest(args.token)}&device_id={device}"
        try:
            r = await run_once(url, args.sample_rate, args.feed_ms, args.lecture_hold_ms)
        except Exception as exc:  # noqa: BLE001 — 探针：任何异常都只报告不中断
            print(f"  run {i}/{args.repeats}  ERROR: {type(exc).__name__}: {exc}")
            continue
        if r["error"]:
            print(f"  run {i}/{args.repeats}  {r['error']}")
            continue
        if r["entry_ms"] is not None:
            entries.append(r["entry_ms"])
        if r["exit_ms"] is not None:
            exits.append(r["exit_ms"])
        print(f"  run {i}/{args.repeats}  进入 lecture={r['entry_ms'] and round(r['entry_ms'])}ms  "
              f"退出={r['exit_ms'] and round(r['exit_ms'])}ms")

    def _summary(name: str, xs: list[float]) -> None:
        if xs:
            print(f"{name}: mean={sum(xs) / len(xs):.0f}ms min={min(xs):.0f} max={max(xs):.0f} (n={len(xs)})")

    print()
    _summary("进入 lecture 回执延迟", entries)
    _summary("退出 lecture 回执延迟", exits)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lecture mode switch latency probe (fake device → local server → real providers)")
    parser.add_argument("--url", default="ws://127.0.0.1:18899/v1/ws/stream")
    parser.add_argument("--token", default=os.environ.get("LIVACHAT_E2E_TOKEN", "dev-livachat-token"),
                        help="明文 secret（脚本内派生 sha1 摘要后发送）")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--feed-ms", type=int, default=1000, help="切换前喂音频时长（默认 1000ms）")
    parser.add_argument("--lecture-hold-ms", type=int, default=1500, help="lecture 停留时长（默认 1500ms）")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
