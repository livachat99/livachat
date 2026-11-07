"""探针：用 docs/video_analysis.md §9.1 的 extract prompt 草案实测存量关键帧。

只读——不写 DB / derived / events；从存量 video 事件取关键帧（frame_id = 文件
stem），按生产同款 chunk 分片（video_extract_chunk_size）跑 extract 草案，输出
逐帧 caption/place/objects/ocr/people 与 place 的 null 率/多轮稳定性供 review。

用法（仓库根）：
    set -a && source .env && set +a   # 独立脚本不经 load_config，需 shell 注入根 .env
    .venv/bin/python3.12 tools/lvdata-mcp/scripts/probe_video_extract.py [--rounds 3]
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvdata_mcp.analysis.vision import _norm_frame_id, load_image_b64
from lvdata_mcp.config import load_config
from lvdata_mcp.providers import ApiError, LlmClient, parse_json

# extract prompt 草案（逐字取自 docs/video_analysis.md §9.1，第二轮精简版）：
# 删 scene/apps/events/actions；新增 place；"关键帧截图"→"关键帧图片"（D3）。
EXTRACT_DRAFT_PROMPT = (
    "你是专业的图片内容分析引擎。你只输出 JSON，不输出任何其他文本或解释。\n"
    "用户提供 1-4 张关键帧图片（图片之间以 \"frame <index>\" 文本标记索引）。\n"
    "对每张图严格按照以下 JSON Schema 输出：\n"
    "{\"frames\": [{\"frame_id\": \"必须使用用户提供的图片索引字符串\", "
    "\"caption\": \"画面的一句话客观描述\", "
    "\"place\": \"根据图片推测地点，不确定就是 null；如果是屏幕截图，也是 null\", "
    "\"objects\": [{\"name\": \"物体名\", \"description\": \"描述\"}], "
    "\"ocr\": [{\"text\": \"显著可见文字，每条不超过80字\", \"location\": \"画面位置\"}], "
    "\"people\": [{\"count\": 0, \"description\": \"人物与动作\"}]}]}\n"
    "要求：ocr 提取画面中显著可见的文字（标题、菜单、弹窗、代码、错误信息等），"
    "最多 15 条；画面文字极少时可为空数组；每个数组字段可以为空但必须存在；"
    "只输出 JSON，不要 Markdown 代码块。"
)


def _norm_place(raw) -> str | None:
    """place 归一草案（§9.2）：非字符串 → None；空串/字面量 null/none → None。"""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.lower() in ("null", "none"):
        return None
    return s


def _image_parts(images: list[str]) -> list[dict]:
    parts: list[dict] = []
    for i, b64 in enumerate(images):
        parts.append({"type": "text", "text": f"frame {i}"})
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    parts.append({"type": "text",
                  "text": "请按系统提示的 JSON Schema 分析以上截图。"})
    return parts


async def _extract_with_fallback(client: LlmClient,
                                 parts: list[dict]) -> tuple[str, dict | None]:
    try:
        return await client.chat(EXTRACT_DRAFT_PROMPT, parts,
                                 enable_thinking=False)
    except ApiError as e:
        if e.status == 400:
            return await client.chat(EXTRACT_DRAFT_PROMPT, parts)
        raise


def _key_frames(cfg) -> list[dict]:
    """存量关键帧（video 事件 → 文件路径），按 ts 升序。"""
    conn = sqlite3.connect(f"file:{cfg.data.resolved_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT event_ref, device_uuid, rsession_id, ts_ms, "
        "json_extract(payload, '$.frame_id') AS frame_id "
        "FROM events WHERE type = 'video' ORDER BY ts_ms")]
    conn.close()
    root = Path(cfg.data.root_dir)
    out = []
    for r in rows:
        fid = r["frame_id"] or r["event_ref"].rsplit(":", 1)[-1]
        p = root / r["device_uuid"] / r["rsession_id"] / "video" / f"{fid}.jpg"
        if not p.is_file():
            print(f"⚠ 缺文件，跳过: {p}")
            continue
        out.append({"frame_id": fid, "file_path": str(p), "ts_ms": r["ts_ms"]})
    return out


async def main() -> None:
    ap = argparse.ArgumentParser(description="video extract prompt 草案实测探针")
    ap.add_argument("--config", type=Path, default=None,
                    help="lvdata_mcp.yaml 路径（缺省同生产解析）")
    ap.add_argument("--rounds", type=int, default=3, help="实测轮数（默认 3）")
    args = ap.parse_args()
    cfg = load_config(args.config)

    frames = _key_frames(cfg)
    chunk_size = cfg.watcher.video_extract_chunk_size
    chunks = [frames[i:i + chunk_size]
              for i in range(0, len(frames), chunk_size)]
    print(f"存量关键帧 = {len(frames)}（只读）；chunk_size={chunk_size} "
          f"→ {len(chunks)} chunk；轮数 = {args.rounds}")

    client = LlmClient(cfg.providers.vision.resolved("vision"),
                       cfg.watcher.llm_timeout_s)
    # frame_id → [place_per_round]
    places: dict[str, list[str | None]] = {f["frame_id"]: [] for f in frames}
    for rnd in range(1, args.rounds + 1):
        print("═" * 76)
        print(f"【第 {rnd}/{args.rounds} 轮】")
        for ci, chunk in enumerate(chunks, 1):
            images = [await asyncio.to_thread(load_image_b64, f["file_path"],
                                              cfg.watcher.max_image_size)
                      for f in chunk]
            content, usage = await _extract_with_fallback(
                client, _image_parts(images))
            parsed = parse_json(content)
            entries = {_norm_frame_id(e.get("frame_id")): e
                       for e in parsed.get("frames") or []
                       if isinstance(e, dict)}
            print(f"─ chunk {ci}/{len(chunks)}（{len(chunk)} 帧）"
                  + (f"  usage={usage}" if usage else ""))
            for i, f in enumerate(chunk):
                e = entries.get(str(i))
                if e is None:
                    print(f"  [{i}] {f['frame_id'][:13]}  ⚠ 缺条目")
                    places[f["frame_id"]].append("<missing>")
                    continue
                raw_place = e.get("place")
                place = _norm_place(raw_place)
                places[f["frame_id"]].append(place)
                objs = e.get("objects") if isinstance(e.get("objects"), list) else []
                ocr = e.get("ocr") if isinstance(e.get("ocr"), list) else []
                ppl = e.get("people") if isinstance(e.get("people"), list) else []
                place_flag = "" if place is None else "  ←非null"
                print(f"  [{i}] {f['frame_id'][:13]}  "
                      f"place(原始)={raw_place!r} → {place!r}{place_flag}")
                print(f"        caption: {e.get('caption')}")
                print(f"        objects({len(objs)}): "
                      f"{[o.get('name') for o in objs if isinstance(o, dict)]}")
                print(f"        ocr({len(ocr)}): "
                      f"{(ocr[0].get('text') if ocr and isinstance(ocr[0], dict) else '')!r}")
                print(f"        people: {ppl}")

    print("═" * 76)
    print("【place 多轮稳定性 / null 率】")
    total = nullish = 0
    for f in frames:
        vals = places[f["frame_id"]]
        total += 1
        if all(v is None for v in vals):
            nullish += 1
        stable = "一致" if len(set(map(str, vals))) == 1 else "⚠波动"
        print(f"  {f['frame_id'][:13]}  {vals}  [{stable}]")
    print(f"合计 {total} 帧：全部轮次 place=null 的帧 = {nullish}/{total}；"
          f"波动帧 = {sum(1 for f in frames if len(set(map(str, places[f['frame_id']]))) > 1)}")
    try:
        await client._client.close()
    except RuntimeError:  # 探针收尾：async 生成器 teardown 噪音，不影响结果
        pass


if __name__ == "__main__":
    asyncio.run(main())
