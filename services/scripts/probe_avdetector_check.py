#!/usr/bin/env python3
"""avdetector 语义探针（docs/fd_pipeline_avdetector.md 设计依据；只读，不改产品配置；
2026-10-03 按该文档 [R11] 入库，原驻留 /tmp/lvdet_probe/probe2.py）。

目标（相比 /tmp/lvomni_probe/probe.py 的增量）：
  1. text-only modalities（["text"]）是否被 qwen3.8-omni-flash-realtime 接受
     —— 检测器只要文本描述，不出音频（省成本/延迟）；
  2. 「检测器契约」：instructions 固定为监测器提示词，每次 response.create 即一次
     检测查询；无事件 → 输出 NONE，有事件 → 一行 JSON。验证正/负样本可判别性；
  3. 同一会话多轮重复查询（轮询式监测的可行性）；
  4. response.done 的 usage 字段（成本估算依据）。

变体：
  N1  新会话：环境噪声 → response.create → 期望 NONE（负样本）
  P1  新会话：环境噪声+咳嗽 → response.create → 期望咳嗽描述（正样本）
  P2  新会话：环境噪声+咳嗽 → conversation.item.create(显式查询文本) + response.create
      → 验证 item.create 文本入口与格式（备选触发方式）
  M   单会话四轮：环境噪声 / 咳嗽 / 纯灰图 / 机器人图 → 各一次 response.create
      → 多轮可重复性 + 图片正负样本
  S   新会话：真实语音（~/projects/asr_test1.wav）→ 期望 NONE（说话负样本；
      2026-10-03 实测 3/3 误报——转写否决信号的依据，见文档 §3.2）
  H   新会话：咳嗽 → **静默 commit**（不触发回复）→ 安静 → response.create
      → 验证「周期静默提交」可行性（窗口纪律，见文档 §4 D5）：commit 不报错、
      流保持可用、后续查询正常、静默提交的转写事件是否下发
  B   检测能力电池：声音 {ambient, cough, cough_quiet, knock, whistle, speech} ×
      图像 {gray, robot}，每 case 新会话 × --repeat 次；记录判定（NONE/JSON/OTHER）、
      事件标签、逐事件时间线（committed/transcription/created/first_delta/done，
      均相对 response.create 发送时刻）与 usage
  F   文件电池：--samples-dir 下每个 16k/mono/PCM16 wav（建议 lv-audiotool 生成，
      已迁至 videogo 仓库：
      `generate -p "…" --format wav --sample-rate 16000 --channels 1`）× --repeat 次，
      记录同上——用真实感非语言声音（咳嗽/喷嚏/清嗓/哈欠…）检验检测与分类

运行（仓库根）：
    cd services && set -a && . ../.env && set +a && ./.venv/bin/python scripts/probe_avdetector_check.py
产物：/tmp/lvdet_probe/report.json（含每条响应文本、事件名、usage）
样本依赖：/tmp/lv3d_robot/robot_preview.png（机器人图）、~/projects/asr_test1.wav（真人语音）
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import math
import os
import random
import struct
import time
from pathlib import Path

import websockets

OUT = Path("/tmp/lvdet_probe")
MODEL = "qwen3.8-omni-flash-realtime"
SR = 16000

DETECT_INSTRUCTIONS = (
    "你是一名环境监测器，不参与对话、不寒暄。每次收到响应请求时，检查你最近收到的音频与图像："
    "若音频中有值得关心的非语音声音事件（如咳嗽、喷嚏、哈欠、异响、物体掉落等），"
    "或图像中有清晰且值得描述的内容/明显变化，输出一行 JSON："
    '{"event":"<事件类型>","desc":"<不超过40字的描述>"}。'
    "若没有值得关注的内容（安静、只有人在正常说话、画面无变化或模糊），只输出：NONE。"
    "不要输出任何其它文字。"
)


def _synth_cough(seconds: float = 0.55) -> bytes:
    random.seed(7)
    n = int(SR * seconds)
    samples = [0.0] * n
    for start, dur, amp in ((0.00, 0.22, 0.9), (0.26, 0.18, 0.6)):
        s0 = int(start * SR)
        for i in range(int(dur * SR)):
            if s0 + i >= n:
                break
            t = i / SR
            env = math.exp(-14 * t) if t < 0.03 else math.exp(-14 * t) * 0.7
            v = (random.uniform(-1, 1) * 0.7 + math.sin(2 * math.pi * 140 * t) * 0.3) * env * amp
            samples[s0 + i] = v
    return struct.pack(f"<{n}h", *(int(max(-1, min(1, v)) * 32767) for v in samples))


def _synth_knock(seconds: float = 0.5) -> bytes:
    """三下敲门声：低频短脉冲串（合成，非真实样本）。"""
    random.seed(11)
    n = int(SR * seconds)
    samples = [0.0] * n
    for start in (0.0, 0.18, 0.36):
        s0 = int(start * SR)
        for i in range(int(0.06 * SR)):
            if s0 + i >= n:
                break
            t = i / SR
            env = math.exp(-45 * t)
            v = (random.uniform(-1, 1) * 0.5 + math.sin(2 * math.pi * 90 * t) * 0.5) * env * 0.9
            samples[s0 + i] = v
    return struct.pack(f"<{n}h", *(int(max(-1, min(1, v)) * 32767) for v in samples))


def _synth_whistle(seconds: float = 0.6) -> bytes:
    """0.6s 正弦扫频哨声 2k→3.5kHz（合成，非真实样本）。"""
    n = int(SR * seconds)
    out: list[float] = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        f = 2000 + 1500 * t / seconds
        phase += 2 * math.pi * f / SR
        env = min(1.0, t / 0.05, (seconds - t) / 0.05)
        out.append(math.sin(phase) * 0.5 * max(0.0, env))
    return struct.pack(f"<{n}h", *(int(v * 32767) for v in out))


def _scale_pcm(pcm: bytes, factor: float) -> bytes:
    import array

    a = array.array("h")
    a.frombytes(pcm)
    return array.array(
        "h", (int(max(-1.0, min(1.0, v / 32768.0 * factor)) * 32767) for v in a)
    ).tobytes()


def _ambient(seconds: float = 0.8) -> bytes:
    random.seed(3)
    n = int(SR * seconds)
    return struct.pack(f"<{n}h", *(int(random.uniform(-0.02, 0.02) * 32767) for _ in range(n)))


def _speech() -> bytes:
    import wave

    with wave.open(os.path.expanduser("~/projects/asr_test1.wav"), "rb") as wf:
        assert wf.getframerate() == SR and wf.getnchannels() == 1
        return wf.readframes(wf.getnframes())


def _gray_image() -> bytes:
    from PIL import Image

    im = Image.new("RGB", (640, 640), (128, 128, 128))
    p = OUT / "gray.jpg"
    im.save(p, quality=85)
    return p.read_bytes()


def _robot_image() -> bytes:
    from PIL import Image

    im = Image.open("/tmp/lv3d_robot/robot_preview.png").convert("RGB").resize((640, 640))
    p = OUT / "robot.jpg"
    im.save(p, quality=85)
    return p.read_bytes()


def _load_wav16(path) -> bytes:
    """读 16k/mono/PCM16 wav 为裸 PCM（lv-audiotool 可直出该格式）。"""
    import wave

    with wave.open(str(path), "rb") as wf:
        assert wf.getframerate() == SR and wf.getnchannels() == 1 and wf.getsampwidth() == 2, (
            f"need 16k mono pcm16 wav: {path} "
            f"(got {wf.getframerate()}Hz/{wf.getnchannels()}ch/{wf.getsampwidth()*8}bit)"
        )
        return wf.readframes(wf.getnframes())


class Session:
    def __init__(self, ws, name: str):
        self.ws = ws
        self.name = name
        self.report: dict = {"variant": name, "rounds": [], "errors": [], "modalities": None}

    async def send(self, obj: dict) -> None:
        await self.ws.send(json.dumps(obj))

    async def append_audio(self, pcm: bytes) -> None:
        step = 1280  # 40ms @16k
        for off in range(0, len(pcm), step):
            await self.send({
                "event_id": f"a{off}", "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm[off:off + step]).decode(),
            })
            await asyncio.sleep(0.012)

    async def append_image(self, jpeg: bytes) -> None:
        await self.send({"event_id": "i1", "type": "input_image_buffer.append",
                         "image": base64.b64encode(jpeg).decode()})


async def _connect(modalities: list[str]) -> Session:
    host = os.environ["DASHSCOPE_API_HOST"]
    url = f"wss://{host}/api-ws/v1/realtime?model={MODEL}"
    headers = {"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"}
    if os.environ.get("DASHSCOPE_WORKSPACE_ID"):
        headers["X-DashScope-WorkSpace"] = os.environ["DASHSCOPE_WORKSPACE_ID"]
    ws = await websockets.connect(url, additional_headers=headers, close_timeout=1)
    s = Session(ws, "conn")
    s.report["modalities"] = modalities
    msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
    if msg.get("type") != "session.created":
        s.report["errors"].append(f"expected session.created, got {msg.get('type')}")
        return s
    session_cfg: dict = {
        "modalities": modalities,
        "instructions": DETECT_INSTRUCTIONS,
        "turn_detection": None,
        "audio": {
            "input": {"format": {"type": "pcm", "sample_rate": SR}},
            "output": {"format": {"type": "pcm", "sample_rate": SR}},
        },
    }
    await s.send({"event_id": "u1", "type": "session.update", "session": session_cfg})
    msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
    if msg.get("type") != "session.updated":
        s.report["errors"].append(f"session.update rejected: {json.dumps(msg, ensure_ascii=False)[:300]}")
    else:
        s.report["vad_echo"] = msg["session"].get("turn_detection")
        s.report["modalities_echo"] = msg["session"].get("modalities")
    return s


async def _query(s: Session, *, prompt: str | None, wait_s: float = 25.0) -> dict:
    """发一次检测查询并收集本轮响应。prompt 非空 → 先 item.create 文本。

    timeline：各关键事件相对 response.create 发送时刻的秒偏移
    （committed / transcription / created / first_delta / done）。
    """
    r: dict = {"reply": "", "events": [], "errors": [], "seconds": None, "usage": None,
               "timeline": {}}
    if prompt:
        await s.send({
            "event_id": "ic1", "type": "conversation.item.create",
            "item": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": prompt}]},
        })
    await s.send({"event_id": "rc1", "type": "response.create"})
    t0 = time.monotonic()
    deadline = t0 + wait_s
    text_out: list[str] = []
    while time.monotonic() < deadline:
        try:
            m = json.loads(await asyncio.wait_for(
                s.ws.recv(), timeout=max(1.0, deadline - time.monotonic())))
        except TimeoutError:
            break
        t = m.get("type", "")
        dt = round(time.monotonic() - t0, 3)
        if t != "response.audio.delta":
            r["events"].append(t)
        if t == "input_audio_buffer.committed":
            r["timeline"].setdefault("committed_s", dt)
        elif t == "conversation.item.input_audio_transcription.completed":
            r.setdefault("input_transcripts", []).append(m.get("transcript", ""))
            r["timeline"].setdefault("transcription_s", dt)
        elif t == "response.created":
            r["timeline"].setdefault("created_s", dt)
        elif t in ("response.text.delta", "response.audio_transcript.delta"):
            r["timeline"].setdefault("first_delta_s", dt)
            text_out.append(m.get("delta", ""))
        elif t == "response.done":
            r["timeline"]["done_s"] = dt
            r["seconds"] = round(time.monotonic() - t0, 2)
            r["usage"] = m.get("response", {}).get("usage") or m.get("usage")
            break
        elif t == "error":
            r["errors"].append(json.dumps(m.get("error", {}), ensure_ascii=False)[:300])
    if r["seconds"] is None:
        r["seconds"] = round(time.monotonic() - t0, 2)
    r["reply"] = "".join(text_out)
    return r


async def _drain_events(s: Session, seconds: float, sink: dict) -> None:
    """静默收集 seconds 秒内的下行事件（不触发查询；H 变体观察 commit 的回应）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            m = json.loads(await asyncio.wait_for(
                s.ws.recv(), timeout=max(0.1, deadline - time.monotonic())))
        except TimeoutError:
            break
        t = m.get("type", "")
        sink.setdefault("events", []).append(t)
        if t == "conversation.item.input_audio_transcription.completed":
            sink.setdefault("transcripts", []).append(m.get("transcript", ""))
        elif t == "error":
            sink.setdefault("errors", []).append(
                json.dumps(m.get("error", {}), ensure_ascii=False)[:300])


async def run_round_session(name: str, rounds: list[tuple[str, list[str]]],
                            modalities: list[str]) -> dict:
    """rounds: [(kind, [payload])] where kind in {audio, ambient, image, robot_image, speech, housekeeping}"""
    s = await _connect(modalities)
    s.name = name
    s.report["variant"] = name
    if s.report["errors"]:
        with contextlib.suppress(Exception):
            await s.ws.close()
        return s.report
    try:
        for i, (kind, parts) in enumerate(rounds):
            if kind == "audio":
                await s.append_audio(_ambient())
                await s.append_audio(_synth_cough())
            elif kind == "ambient":
                await s.append_audio(_ambient())
            elif kind == "image":
                await s.append_audio(_ambient())
                await s.append_image(_gray_image())
            elif kind == "robot_image":
                await s.append_audio(_ambient())
                await s.append_image(_robot_image())
            elif kind == "speech":
                await s.append_audio(_speech())
                await s.append_audio(_ambient(0.8))
            elif kind == "housekeeping":
                await s.append_audio(_synth_cough())
                await s.send({"event_id": "hc1", "type": "input_audio_buffer.commit"})
                await _drain_events(s, 1.5, s.report.setdefault("housekeeping", {}))
                await s.append_audio(_ambient(0.8))
            elif kind == "item_prompt":
                pass
            r = await _query(s, prompt=parts[0] if parts else None)
            r["label"] = f"{i}:{kind}"
            s.report["rounds"].append(r)
            print(f"[{name}/{i}:{kind}] reply={r['reply']!r} ({r['seconds']}s) "
                  f"errors={r['errors'] or '-'}")
    finally:
        with contextlib.suppress(Exception):
            await s.send({"event_id": "f1", "type": "session.finish"})
            await asyncio.sleep(0.2)
        with contextlib.suppress(Exception):
            await s.ws.close()
    return s.report


BATTERY_SOUND_CASES = ("ambient", "cough", "cough_quiet", "knock", "whistle", "speech")
BATTERY_IMAGE_CASES = ("gray", "robot")


def _parse_verdict(reply: str) -> str:
    t = reply.strip()
    if t.upper() == "NONE":
        return "NONE"
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and ("desc" in obj or "event" in obj):
            return "JSON"
    except Exception:
        pass
    return "OTHER"


def _event_label(reply: str) -> str:
    try:
        obj = json.loads(reply.strip())
        return str(obj.get("event", "")) if isinstance(obj, dict) else ""
    except Exception:
        return ""


async def _battery_one(case: str, kind: str, payload: bytes, rep: int) -> dict:
    s = await _connect(["text"])
    entry: dict = {"case": case, "kind": kind, "rep": rep, "errors": []}
    try:
        if s.report["errors"]:
            entry["errors"] = list(s.report["errors"])
            return entry
        await s.append_audio(_ambient(0.6))
        if kind == "sound":
            await s.append_audio(payload)
        else:
            await s.append_image(payload)
        r = await _query(s, prompt=None)
        entry.update({
            "reply": r["reply"],
            "verdict": _parse_verdict(r["reply"]),
            "event_label": _event_label(r["reply"]),
            "timeline": r.get("timeline", {}),
            "usage": r.get("usage"),
            "transcripts": r.get("input_transcripts", []),
            "errors": r["errors"],
        })
    finally:
        with contextlib.suppress(Exception):
            await s.send({"event_id": "f1", "type": "session.finish"})
            await asyncio.sleep(0.1)
        with contextlib.suppress(Exception):
            await s.ws.close()
    return entry


async def run_battery(repeat: int) -> list[dict]:
    """检测能力电池：每 case 新会话 × repeat 次；逐次记录判定/事件标签/时间线。"""
    sound_payloads = {
        "ambient": _ambient(1.2),
        "cough": _synth_cough(),
        "cough_quiet": _scale_pcm(_synth_cough(), 0.25),
        "knock": _synth_knock(),
        "whistle": _synth_whistle(),
        "speech": _speech(),
    }
    image_payloads = {"gray": _gray_image(), "robot": _robot_image()}
    out: list[dict] = []
    for case in BATTERY_SOUND_CASES:
        for rep in range(repeat):
            e = await _battery_one(case, "sound", sound_payloads[case], rep)
            out.append(e)
            print(f"[B/sound/{case}#{rep}] {e.get('verdict')} "
                  f"{e.get('reply', '')[:70]!r} tl={e.get('timeline')}")
    for case in BATTERY_IMAGE_CASES:
        for rep in range(repeat):
            e = await _battery_one(case, "image", image_payloads[case], rep)
            out.append(e)
            print(f"[B/image/{case}#{rep}] {e.get('verdict')} "
                  f"{e.get('reply', '')[:70]!r} tl={e.get('timeline')}")
    return out


async def run_file_battery(samples_dir: Path, repeat: int) -> list[dict]:
    """F 变体：对目录下每个 16k/mono/PCM16 wav 跑检测（每文件 × repeat 次新会话）。

    样本建议用 lv-audiotool 生成（`--sample-rate 16000 --channels 1`）：
    真实感的人声非语言声音（咳嗽/喷嚏/清嗓/哈欠/笑/吸鼻）——比噪声合成样本更接近验收场景。
    """
    files = sorted(Path(samples_dir).glob("*.wav"))
    if not files:
        print(f"[F] no wav in {samples_dir}")
        return []
    out: list[dict] = []
    for f in files:
        pcm = _load_wav16(f)
        for rep in range(repeat):
            e = await _battery_one(f.stem, "sound", pcm, rep)
            e["file"] = str(f)
            out.append(e)
            print(f"[F/{f.stem}#{rep}] {e.get('verdict')} "
                  f"{e.get('reply', '')[:70]!r} tl={e.get('timeline')}")
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="N1,P1,P2,M")
    ap.add_argument("--modalities", default="text")
    ap.add_argument("--repeat", type=int, default=4,
                    help="B/F 电池每 case 重复次数")
    ap.add_argument("--samples-dir", default="/tmp/lvdet_probe/samples",
                    help="F 电池的 16k/mono/PCM16 wav 目录（lv-audiotool 直出格式；工具已迁至 videogo 仓库）")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    mods = [m.strip() for m in args.modalities.split(",") if m.strip()]

    report = []
    for name in [v.strip() for v in args.variants.split(",") if v.strip()]:
        if name == "B":
            report.extend(await run_battery(args.repeat))
            continue
        if name == "F":
            report.extend(await run_file_battery(args.samples_dir, args.repeat))
            continue
        if name == "N1":
            r = await run_round_session("N1", [("ambient", [])], mods)
        elif name == "P1":
            r = await run_round_session("P1", [("audio", [])], mods)
        elif name == "P2":
            r = await run_round_session(
                "P2",
                [("audio", ["请执行一次检测：最近音频中有值得关心的声音事件吗？按你的输出契约回答。"])],
                mods,
            )
        elif name == "S":
            r = await run_round_session("S", [("speech", [])], mods)
        elif name == "H":
            r = await run_round_session("H", [("housekeeping", [])], mods)
        elif name == "M":
            r = await run_round_session("M", [
                ("ambient", []),
                ("audio", []),
                ("image", []),
                ("robot_image", []),
            ], mods)
        else:
            continue
        report.append(r)

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport -> {OUT/'report.json'}")


if __name__ == "__main__":
    asyncio.run(main())
