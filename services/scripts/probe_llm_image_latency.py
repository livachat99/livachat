#!/usr/bin/env python3
"""真机探针：LLM 请求携带 2 张 vs 10 张（vs 0 张基线）历史图时的处理耗时对比。

背景：docs/fd_pipeline_llm_v2.md §9.4（请求组装图片裁剪至最新 2 张，待实施）——
本探针实测图片数量对 LLM 处理时间的影响，为裁剪方案提供数据。

方法（与生产同路径）：
- 配置经 shared.config.loader.load_config()（自动注入仓库根 .env）→ llm.factory.create_llm；
- 流式调用 LlmService.stream（openai_compat 渲染 image parts、timeout、extra_body 同生产），
  gen opts 取 cfg.llm.generation；
- 消息形态复刻 _start_thinking：system + 10 轮 user(文本[+图])/assistant(短文本) + 末轮提问；
  · 10 张：每轮 user 带 1 图（10 图分布在 10 条消息，生产 K=1 形态）；
  · 2 张：同一 20 条 history、仅最后 2 轮 user 带图（= 裁剪后请求形态）；
  · 0 张：基线；
- 每条件 warmup 1 次（丢弃）+ N 次测量；每轮按 0/2/10 轮转，抗缓存与时间漂移；
  末轮提问带 rep nonce（同一 rep 内三条件同 nonce）；
- 指标：TTFT（首内容 token）、总耗时、输出字符数；另以非流式同请求取 usage（辅助）。

产物：<out-dir>（默认 /tmp/llm_image_latency/）：results.json + images/frame_*.jpg。

用法（从 services/ 目录；需 .env 中 DASHSCOPE_* 已配置）：
    .venv/bin/python scripts/probe_llm_image_latency.py --runs 5
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICES = _HERE.parent
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from PIL import Image, ImageChops, ImageDraw, ImageFilter  # noqa: E402

SYSTEM_PROMPT = "你是实时语音助手，回答必须极简。"
FINAL_QUESTION = "简单总结此刻画面。只回复两个字：收到。"
CONDITIONS = (0, 2, 10)


def make_image(idx: int, *, w: int = 1280, h: int = 720,
               quality: int = 86, noise: int = 32) -> bytes:
    """合成"相机帧"JPEG（渐变 + 色块 + 传感器噪点），目标 ~100–200KB/张。"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            r = int(120 + 60 * math.sin((x / 90.0) + idx))
            g = int(110 + 50 * math.sin((y / 70.0) + idx * 1.3))
            b = int(100 + 40 * math.sin((x + y) / 150.0))
            for dy in range(4):
                for dx in range(4):
                    if x + dx < w and y + dy < h:
                        px[x + dx, y + dy] = (
                            min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b)))
    d = ImageDraw.Draw(img)
    rnd = random.Random(idx)
    for _ in range(14):
        x0, y0 = rnd.randint(0, w - 120), rnd.randint(0, h - 120)
        d.ellipse([x0, y0, x0 + 100, y0 + 100],
                  fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    small = Image.new("L", (w // 3, h // 3))
    small.putdata([rnd.randint(0, 255) for _ in range((w // 3) * (h // 3))])
    noise_img = small.resize((w, h), Image.BILINEAR).convert("RGB")
    img = ImageChops.add(img, noise_img.point(lambda v: int(v * noise / 255)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def load_images_from_dir(d: Path, limit: int = 10) -> list[bytes]:
    """从目录读取真实 JPEG（按文件名排序取前 limit 张）——如从 chat_server.db
    messages.images_json 导出的 AF 快照帧。"""
    files = sorted(d.glob("*.jpg"))[:limit]
    if len(files) < limit:
        raise SystemExit(f"images-dir 需 ≥{limit} 张 .jpg：{d} 只有 {len(files)} 张")
    return [f.read_bytes() for f in files]


def build_messages(data_urls: list[str], *, rounds: int, keep_last: int, nonce: str):
    from llm.base import ChatMessage

    msgs = [ChatMessage(role="system", content=SYSTEM_PROMPT)]
    for i in range(rounds):
        images = [data_urls[i]] if i >= rounds - keep_last else None
        msgs.append(ChatMessage(role="user", content=f"第{i + 1}轮画面。", images=images))
        msgs.append(ChatMessage(role="assistant", content="看到了。"))
    msgs.append(ChatMessage(role="user", content=f"{FINAL_QUESTION}（{nonce}）"))
    return msgs


async def measure_stream(svc, msgs, gen_opts, timeout_s: float = 240.0) -> dict:
    t0 = time.monotonic()
    ttft: float | None = None
    n_chars = 0

    async def _consume() -> None:
        nonlocal ttft, n_chars
        async for tok in svc.stream(messages=msgs, **gen_opts):
            if ttft is None:
                ttft = time.monotonic() - t0
            n_chars += len(tok)

    await asyncio.wait_for(_consume(), timeout=timeout_s)
    return {
        "ttft_ms": round((ttft or -1) * 1000, 1),
        "total_ms": round((time.monotonic() - t0) * 1000, 1),
        "out_chars": n_chars,
    }


async def fetch_usage(svc, msgs, gen_opts) -> dict:
    """非流式同请求取 usage（辅助指标）；消息渲染复刻生产 _build_oai_messages。"""
    import httpx
    from openai import AsyncOpenAI

    from llm.providers.openai_compat import _build_oai_messages

    provider = svc._providers[0]
    oai_messages = _build_oai_messages(msgs, provider)
    model = provider._model
    if provider.supports_vision and provider._vision_model and any(
            isinstance(getattr(m, "images", None), list) and m.images for m in msgs):
        model = provider._vision_model
    client = AsyncOpenAI(base_url=provider._base_url, api_key=provider._api_key,
                         default_headers=provider._default_headers,
                         timeout=httpx.Timeout(60.0, connect=5.0))
    try:
        resp = await client.chat.completions.create(
            model=model, messages=oai_messages, stream=False,
            extra_body=provider._extra_body, **gen_opts)
    finally:
        await client.close()
    u = resp.usage
    return {
        "prompt_tokens": getattr(u, "prompt_tokens", None),
        "completion_tokens": getattr(u, "completion_tokens", None),
    }


def summarize(runs: list[dict]) -> dict:
    ttfts = [r["ttft_ms"] for r in runs if "error" not in r]
    totals = [r["total_ms"] for r in runs if "error" not in r]
    chars = [r["out_chars"] for r in runs if "error" not in r]
    if not ttfts:
        return {"n": 0, "error": "all runs failed"}

    def _stats(xs: list[float]) -> dict:
        return {"mean": round(statistics.mean(xs), 1), "median": round(statistics.median(xs), 1),
                "min": round(min(xs), 1), "max": round(max(xs), 1)}

    return {"n": len(ttfts), "ttft_ms": _stats(ttfts), "total_ms": _stats(totals),
            "out_chars_mean": round(statistics.mean(chars), 1)}


async def run(args: argparse.Namespace) -> int:
    from llm.factory import create_llm
    from shared.config.loader import load_config

    cfg = load_config()
    svc = create_llm(cfg.llm)
    gen_opts = cfg.llm.generation.model_dump()
    provider = svc._providers[0]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.images_dir:
        images = load_images_from_dir(Path(args.images_dir))
        image_source = f"real:{args.images_dir}"
    else:
        images = [make_image(i) for i in range(10)]
        image_source = "synthetic"
        img_dir = out_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(images):
            (img_dir / f"frame_{i:02d}.jpg").write_bytes(b)
    data_urls = [f"data:image/jpeg;base64,{base64.b64encode(b).decode()}" for b in images]
    base64_chars = [len(u) for u in data_urls]

    print(f"model={provider._model}  gen_opts={gen_opts}")
    print(f"images: {image_source}  {len(images)} 张, raw JPEG {[len(b) // 1024 for b in images]}KB, "
          f"base64 chars {[c // 1024 for c in base64_chars]}K")

    async def one(cond: int, nonce: str) -> dict:
        msgs = build_messages(data_urls, rounds=args.rounds, keep_last=cond, nonce=nonce)
        try:
            return await measure_stream(svc, msgs, gen_opts)
        except Exception as e:  # noqa: BLE001 — 探针：单次失败记录后继续
            return {"error": f"{type(e).__name__}: {e}"[:300]}

    print("warmup...")
    for c in CONDITIONS:
        await one(c, "warmup")

    results: dict[str, list[dict]] = {str(c): [] for c in CONDITIONS}
    for rep in range(args.runs):
        for c in CONDITIONS:
            r = await one(c, f"r{rep + 1}")
            results[str(c)].append(r)
            if "error" in r:
                print(f"[run {rep + 1}] images={c}: ERROR {r['error']}")
            else:
                print(f"[run {rep + 1}] images={c}: ttft={r['ttft_ms']}ms "
                      f"total={r['total_ms']}ms chars={r['out_chars']}")

    usages: dict[str, dict] = {}
    for c in CONDITIONS:
        msgs = build_messages(data_urls, rounds=args.rounds, keep_last=c, nonce="usage")
        try:
            usages[str(c)] = await fetch_usage(svc, msgs, gen_opts)
        except Exception as e:  # noqa: BLE001
            usages[str(c)] = {"error": f"{type(e).__name__}: {e}"[:300]}

    summaries = {str(c): summarize(results[str(c)]) for c in CONDITIONS}
    payload = {
        str(c): {"images": c,
                 "raw_jpeg_kb": round(sum(len(b) for b in images[:c]) / 1024, 1),
                 "base64_chars_k": round(sum(base64_chars[:c]) / 1024, 1)}
        for c in CONDITIONS
    }

    doc = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": provider._model,
        "base_url": provider._base_url,
        "gen_opts": gen_opts,
        "rounds": args.rounds,
        "runs_per_condition": args.runs,
        "image_source": image_source,
        "image_sizes_kb": [round(len(b) / 1024, 1) for b in images],
        "payload": payload,
        "summaries": summaries,
        "usages": usages,
        "raw_runs": results,
    }
    (out_dir / "results.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2))

    # -- 汇总输出 ---------------------------------------------------------
    print("\n## 汇总（ms）\n")
    print("| 图数量 | TTFT 均值 | TTFT 中位 | 总耗时均值 | 总耗时中位 | 输出字符 |")
    print("|---|---|---|---|---|---|")
    for c in CONDITIONS:
        s = summaries[str(c)]
        if s.get("n"):
            print(f"| {c} | {s['ttft_ms']['mean']} | {s['ttft_ms']['median']} | "
                  f"{s['total_ms']['mean']} | {s['total_ms']['median']} | {s['out_chars_mean']} |")
        else:
            print(f"| {c} | - | - | - | - | - |")
    if summaries["0"].get("n") and summaries["2"].get("n") and summaries["10"].get("n"):
        d10_2 = summaries["10"]["ttft_ms"]["mean"] - summaries["2"]["ttft_ms"]["mean"]
        d2_0 = summaries["2"]["ttft_ms"]["mean"] - summaries["0"]["ttft_ms"]["mean"]
        t10_2 = summaries["10"]["total_ms"]["mean"] - summaries["2"]["total_ms"]["mean"]
        print(f"\nΔ TTFT  2-0 = {d2_0:+.1f}ms   10-2 = {d10_2:+.1f}ms")
        print(f"Δ total 10-2 = {t10_2:+.1f}ms")
    print("\nprompt_tokens（非流式同请求，辅助）：")
    for c in CONDITIONS:
        print(f"  images={c}: {usages[str(c)]}")
    print(f"\n产物：{out_dir}/results.json")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 2 vs 10 张图的处理耗时探针")
    parser.add_argument("--runs", type=int, default=5, help="每条件测量次数（默认 5）")
    parser.add_argument("--rounds", type=int, default=10, help="history 轮数（默认 10）")
    parser.add_argument("--images-dir", default=None,
                        help="真实图目录（*.jpg ≥10 张，按名取前 10）；缺省用合成图")
    parser.add_argument("--out-dir", default="/tmp/llm_image_latency",
                        help="产物目录（默认 /tmp/llm_image_latency）")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
