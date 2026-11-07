#!/usr/bin/env python3
"""TODO-1 验证探针：omni「点按即回一句」——image + text 能否让模型正确回复。

对应 `docs/fd_pipeline_omni_mode.md` §10 TODO-1：抓屏后不等用户开口，直接让模型看图回一句。
本脚本只做**协议层验证**（不改产品代码），把三条候选路径跑成可对比的结果：

  V1  semantic_vad + 音频 + 图 + conversation.item.create(text) + response.create
      不改 VAD 语义的路线；关注点：VAD 模式下图片是否随该轮生效（官方语义为图随
      input_audio_buffer.commit 提交，VAD 由服务端在判停时自动提交）。
  V2  manual(turn_detection=null) + 音频 + 图 + input_audio_buffer.commit
      + conversation.item.create(text) + response.create
      已在 docs/qwen_omni_realtime.md §4.5 验证过一次的基线路径（红圆→「圆形，红色」）。
  V3  manual + 仅图 + item.create(text) + response.create（不发音频）
      验证官方约束「发送 input_image_buffer.append 前至少已发过一次音频」。

判定：回复文本是否**描述正确**（默认图为红圆 → 期望含「圆」与「红」），以及是否收到 error 帧。

用法：
    cd services && set -a && . ./.env && set +a
    ./.venv/bin/python scripts/probe_omni_image_text_turn.py \
        --base-url wss://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime \
        --workspace-id <WorkspaceId> [--model qwen3.8-omni-flash-realtime]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import time
import wave

import websockets

DEFAULT_IMAGE = "/tmp/probe_omni_img.jpg"
PROMPT = "请描述这张图片的内容，只回答你看到的东西。"


def _make_image(path: str) -> bytes:
    """默认生成「白底红圆」图（便于自动判定回复是否正确）。"""
    if os.path.exists(path):
        return open(path, "rb").read()
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (640, 360), "white")
    ImageDraw.Draw(im).ellipse([120, 40, 520, 320], fill="red")
    im.save(path, quality=85)
    return open(path, "rb").read()


def _read_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as wf:
        return wf.readframes(wf.getnframes())


async def _run_variant(args, name: str, *, vad: str | None, send_image: bool = True,
                       send_audio: bool = True, commit: bool = False) -> dict:
    url = args.base_url
    if "model=" not in url:
        url += ("&" if "?" in url else "?") + "model=" + args.model
    headers = {"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"}
    if args.workspace_id:
        headers["X-DashScope-WorkSpace"] = args.workspace_id

    session = {
        "modalities": ["text", "audio"],
        "voice": "Tina",
        "instructions": "你是一个友好的语音助手。回答要简短。",
        "turn_detection": ({"type": vad} if vad else None),
        "audio": {
            "input": {"format": {"type": "pcm", "sample_rate": 16000}},
            "output": {"format": {"type": "pcm", "sample_rate": 16000}},
        },
    }
    result: dict = {"variant": name, "error": "", "reply": "", "seconds": None}
    text_out: list[str] = []
    t0 = time.monotonic()

    async with websockets.connect(url, additional_headers=headers, close_timeout=1) as ws:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if msg.get("type") != "session.created":
            result["error"] = f"expected session.created, got {msg.get('type')}"
            return result
        await ws.send(json.dumps({"event_id": "u1", "type": "session.update", "session": session}))
        msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if msg.get("type") != "session.updated":
            result["error"] = f"session.update rejected: {json.dumps(msg, ensure_ascii=False)[:200]}"
            return result
        result["vad"] = msg["session"].get("turn_detection")

        if send_audio:
            pcm = _read_pcm(args.wav)
            step = 1280  # 40ms @16k
            for i, off in enumerate(range(0, min(len(pcm), 16000 * 2), step)):  # 只发 1s，满足"先发过音频"
                await ws.send(json.dumps({
                    "event_id": f"a{i}", "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm[off:off + step]).decode(),
                }))
                await asyncio.sleep(0.01)
        if send_image:
            await ws.send(json.dumps({
                "event_id": "i1", "type": "input_image_buffer.append",
                "image": base64.b64encode(args.image_bytes).decode(),
            }))
        if commit:
            await ws.send(json.dumps({"event_id": "c1", "type": "input_audio_buffer.commit"}))
        await ws.send(json.dumps({
            "event_id": "it1", "type": "conversation.item.create",
            "item": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": PROMPT}]},
        }))
        await ws.send(json.dumps({"event_id": "rc1", "type": "response.create"}))

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.monotonic())))
            except TimeoutError:
                break
            t = m.get("type", "")
            if t in ("response.text.delta", "response.audio_transcript.delta"):
                text_out.append(m.get("delta", ""))
            elif t == "response.done":
                result["seconds"] = round(time.monotonic() - t0, 2)
                break
            elif t == "error":
                result["error"] = json.dumps(m.get("error", {}), ensure_ascii=False)[:220]
                break
        result["reply"] = "".join(text_out)
        if result["seconds"] is None:
            result["seconds"] = round(time.monotonic() - t0, 2)

        with contextlib.suppress(Exception):  # 收尾失败不影响判定
            await ws.send(json.dumps({"event_id": "f1", "type": "session.finish"}))
            await asyncio.sleep(0.2)
    return result


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.8-omni-flash-realtime")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--workspace-id", default=None)
    ap.add_argument("--wav", default=os.path.expanduser("~/projects/asr_test1.wav"))
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()
    args.image_bytes = _make_image(args.image)
    print(f"image: {args.image} ({len(args.image_bytes)} bytes) | prompt: {PROMPT!r}\n")

    matrix = [
        ("V1 semantic_vad + audio + image + text", {"vad": "semantic_vad"}),
        ("V2 manual + audio + image + commit + text", {"vad": None, "commit": True}),
        ("V3 manual + image only (无音频) + text", {"vad": None, "send_audio": False}),
    ]
    results = []
    for name, kwargs in matrix:
        r = await _run_variant(args, name, **kwargs)
        results.append(r)
        print(f"[{name}]")
        print(f"  turn_detection = {r.get('vad')}")
        print(f"  error   = {r['error'] or '-'}")
        print(f"  reply   = {r['reply']!r}")
        print(f"  用时    = {r['seconds']}s\n")

    print("=== 汇总 ===")
    for r in results:
        ok = ("圆" in r["reply"] and "红" in r["reply"])
        print(f"{r['variant']:44s} reply={'✔ 描述正确' if ok else ('✘ ' + (r['error'] or '未描述图片'))} "
              f"({r['seconds']}s)")


if __name__ == "__main__":
    asyncio.run(main())
