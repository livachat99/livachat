"""探针：filter prompt 草案 scene_id 版——实测指定日期窗口（默认无过滤 = 全量）。

只读——不写 DB / derived / events；按生产同款窗口分组（device 分组 + 30s/24 帧）
跑 filter 草案，输出逐帧判定、每窗口 scene 结构与"两天有多少 scene"统计。

草案变更（用户 2026-10-07）：`duplicated`（bool）→ `scene_id`（整数）——窗口内
同样的场景标注相同的 id，不同的场景标注不同的 id；其余内容与原版一致。

用法（仓库根）：
    set -a && source .env && set +a
    .venv/bin/python3.12 tools/lvdata-mcp/scripts/probe_video_filter.py \
        --dates 2026-10-05,2026-10-06
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvdata_mcp.analysis.vision import group_windows_by_device, load_image_b64
from lvdata_mcp.config import load_config
from lvdata_mcp.providers import ApiError, LlmClient, parse_json
from lvdata_mcp.timeutil import resolve_timezone

# filter prompt 草案（scene_id 版，逐字）：帧键 5 个，duplicated → scene_id 整数。
FILTER_DRAFT_PROMPT = (
    "你是图片序列分析引擎。你只输出 JSON，不输出任何其他文本或解释。\n"
    "用户提供按时间顺序排列的 N 张图片（图片之间以 \"frame <index>\" 文本标记索引，从 0 开始）。\n"
    "请逐帧判定并严格按照以下 JSON Schema 输出：\n"
    "{\"window_caption\": \"该时间窗口内图片内容的一句话概括\", "
    "\"frames\": [{\"frame_id\": \"必须使用用户提供的图片索引字符串\", "
    "\"quality\": \"图片画质 low|high（模糊、黑屏、花屏等低质 → low）\", "
    "\"scene_id\": \"本帧所属场景的整数编号：窗口内同样的场景标注相同的 id，"
    "不同的场景标注不同的 id\", "
    "\"key_frame\": \"本帧是否为其场景选出的代表帧：同一场景最多一帧 true（取该场景"
    "画质最高的帧）；画面移动中的过渡场景、黑屏场景可以没有关键帧 → true，否则 false\", "
    "\"reason\": \"一句话理由\"}]}\n"
    "要求：每个输入的 frame_id 都必须有且仅有一个条目，不得遗漏、不得编造索引；"
    "只输出 JSON，不要 Markdown 代码块。"
)


def _tri(v) -> bool | None:
    """宽容解析模型 bool 输出（True/False/0/1/"true"/"false"）；无法解析 → None。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def _scene_id(v) -> int | None:
    """宽容解析 scene_id（int / 整数字符串 / 整值 float）；非法 → None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, float) and v.is_integer():
        return int(v) if v >= 0 else None
    if isinstance(v, str):
        s = v.strip()
        if s.isascii() and s.isdigit():  # 仅 ASCII（'²' 等 int() 会抛错）
            return int(s)
    return None


def _image_parts(images: list[str]) -> list[dict]:
    parts: list[dict] = []
    for i, b64 in enumerate(images):
        parts.append({"type": "text", "text": f"frame {i}"})
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    parts.append({"type": "text",
                  "text": "请按系统提示的 JSON Schema 分析以上截图。"})
    return parts


async def _filter_with_fallback(client: LlmClient,
                                parts: list[dict]) -> tuple[str, dict | None]:
    """镜像生产 `_filter`：enable_thinking=False → 400 则无参数重试一次。"""
    try:
        return await client.chat(FILTER_DRAFT_PROMPT, parts, enable_thinking=False)
    except ApiError as e:
        if e.status == 400:
            return await client.chat(FILTER_DRAFT_PROMPT, parts)
        raise


def _pick_windows(windows: list[dict], n: int | None) -> list[dict]:
    """确定性均匀取样（含首尾端点）。"""
    if not n or n >= len(windows):
        return windows
    if n == 1:
        return [windows[0]]
    return [windows[round(i * (len(windows) - 1) / (n - 1))] for i in range(n)]


async def main() -> None:
    ap = argparse.ArgumentParser(description="video filter prompt 草案实测探针")
    ap.add_argument("--config", type=Path, default=None,
                    help="lvdata_mcp.yaml 路径（缺省同生产解析）")
    ap.add_argument("--dates", default=None,
                    help="逗号分隔本地日期（Asia/Shanghai）过滤窗口首帧所在日，"
                         "如 2026-10-05,2026-10-06；缺省 = 全部")
    ap.add_argument("--max-windows", type=int, default=None,
                    help="过滤后均匀取样窗口数（缺省 = 全部）")
    ap.add_argument("--fresh-client-per-window", action="store_true",
                    help="每个窗口新建 LLM 客户端（新连接），用完即关"
                         "——对比连接复用是否影响分析结果")
    args = ap.parse_args()
    cfg = load_config(args.config)
    tz = resolve_timezone(cfg.watcher.timezone)

    conn = sqlite3.connect(f"file:{cfg.data.resolved_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT f.*, s.device_uuid AS s_device_uuid FROM files f "
        "JOIN recording_sessions s ON s.rsession_id = f.rsession_id "
        "WHERE f.file_type = 'video' ORDER BY f.uuid_ts_ms")]
    conn.close()
    all_windows = group_windows_by_device(rows, cfg.watcher.video_window_s,
                                          cfg.watcher.video_max_frames_per_call)

    def _day(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz).strftime("%Y-%m-%d")

    dates = {d.strip() for d in args.dates.split(",")} if args.dates else None
    if dates:
        windows = [w for w in all_windows if _day(w["start_ms"]) in dates]
    else:
        windows = list(all_windows)
    windows = _pick_windows(windows, args.max_windows)
    day_win = Counter(_day(w["start_ms"]) for w in windows)
    print(f"video files = {len(rows)}（只读）；全部窗口 = {len(all_windows)}；"
          f"本轮取样 = {len(windows)}（按日: {dict(sorted(day_win.items()))}；"
          f"window_s={cfg.watcher.video_window_s}, "
          f"max_frames={cfg.watcher.video_max_frames_per_call}）", flush=True)

    def _new_client() -> LlmClient:
        return LlmClient(cfg.providers.vision.resolved("vision"),
                         cfg.watcher.llm_timeout_s)

    mode = "每窗口新建连接" if args.fresh_client_per_window else "单连接复用"
    print(f"连接模式：{mode}", flush=True)
    reuse_client: LlmClient | None = None if args.fresh_client_per_window \
        else _new_client()

    def _ts(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz).strftime("%m-%d %H:%M:%S")

    # 聚合（全局 + 按日）
    day_agg = defaultdict(lambda: {"win": 0, "frames": 0, "scenes": 0, "keys": 0})
    size_dist: Counter = Counter()        # 场景规模（每场景帧数）分布
    win_scene_dist: Counter = Counter()   # 每窗 scene 数分布
    n_none = n_badkey = n_nonef = n_lack = n_extra = n_multikey = 0
    n_keylow = 0
    total_frames = total_scenes = total_key = 0

    for wi, w in enumerate(windows, 1):
        frames = w["frames"]
        images = [await asyncio.to_thread(load_image_b64, r["file_path"],
                                          cfg.watcher.max_image_size)
                  for r in frames]
        client = reuse_client if reuse_client is not None else _new_client()
        try:
            content, usage = await _filter_with_fallback(
                client, _image_parts(images))
        finally:
            if reuse_client is None:
                await client._client.close()
        parsed = parse_json(content)
        entries = {str(e.get("frame_id")): e
                   for e in parsed.get("frames") or [] if isinstance(e, dict)}

        scenes_of: dict[int, int | None] = {}
        keys_of: dict[int, bool | None] = {}
        print("═" * 76, flush=True)
        print(f"[窗口 {wi}/{len(windows)}] {w['window_id']}  {len(frames)} 帧  "
              f"{_ts(w['start_ms'])}–{_ts(w['end_ms'])}", flush=True)
        print(f"window_caption: {parsed.get('window_caption', '')}", flush=True)
        for i, r in enumerate(frames):
            e = entries.get(str(i), {})
            q = e.get("quality")
            sc = _scene_id(e.get("scene_id"))
            kf = _tri(e.get("key_frame"))
            scenes_of[i], keys_of[i] = sc, kf
            flag = ""
            if q not in ("low", "high") or kf is None:
                flag += "  ⚠枚举越界/类型异常"
                n_badkey += 1
            if sc is None:
                flag += "  ⚠scene 异常"
                n_none += 1
            if kf is True and q == "low":
                n_keylow += 1
                flag += "  ⚠key∧quality=low"
            print(f"  [{i:02d}] {_ts(r['uuid_ts_ms'])}  "
                  f"q={str(q):6} scene={str(sc):>4} key={str(kf):5}"
                  f"{flag}  {e.get('reason') or '<无 reason>'}", flush=True)
        lack = [i for i in range(len(frames)) if str(i) not in entries]
        extra = [k for k in entries
                 if k not in {str(i) for i in range(len(frames))}]
        if lack:
            n_lack += 1
            print(f"  ⚠ 缺失帧条目: {lack}", flush=True)
        if extra:
            n_extra += 1
            print(f"  ⚠ 编造帧索引: {sorted(extra)}", flush=True)

        # 每场景: 帧集合 + key 数
        sframes: dict[int | None, list[int]] = defaultdict(list)
        for i in range(len(frames)):
            sframes[scenes_of[i]].append(i)
        valid_scenes = [s for s in sframes if s is not None]
        keys = [i for i in range(len(frames)) if keys_of[i] is True]
        multi = [s for s in valid_scenes
                 if sum(1 for i in sframes[s] if keys_of[i] is True) > 1]
        n_multikey += len(multi)
        desc = " ".join(
            f"{s}:{len(sframes[s])}"
            f"{'✓' if any(keys_of[i] is True for i in sframes[s]) else '✗'}"
            for s in sorted(valid_scenes))
        print(f"  ⇒ scenes = {len(valid_scenes)}  [{desc}]"
              f"{'  ⚠ 同场景多 key: ' + str(multi) if multi else ''}；"
              f"key_frames = {keys}", flush=True)
        if usage:
            print(f"  usage = {usage}", flush=True)

        d = _day(w["start_ms"])
        day_agg[d]["win"] += 1
        day_agg[d]["frames"] += len(frames)
        day_agg[d]["scenes"] += len(valid_scenes)
        day_agg[d]["keys"] += len(keys)
        total_frames += len(frames)
        total_scenes += len(valid_scenes)
        total_key += len(keys)
        win_scene_dist[len(valid_scenes)] += 1
        for s in valid_scenes:
            size_dist[len(sframes[s])] += 1

    if reuse_client is not None:
        await reuse_client._client.close()
    print("═" * 76, flush=True)
    for d in sorted(day_agg):
        a = day_agg[d]
        print(f"{d}: 窗口 {a['win']} / 帧 {a['frames']} → scene {a['scenes']}、"
              f"key {a['keys']}", flush=True)
    print(f"合计：{len(windows)} 窗口 / {total_frames} 帧 → scene {total_scenes}"
          f"（窗口内计数之和；跨窗同场景不合并）、key {total_key}", flush=True)
    print(f"场景规模（每场景帧数）分布: {sorted(size_dist.items())}", flush=True)
    print(f"每窗 scene 数分布: {sorted(win_scene_dist.items())}", flush=True)
    print(f"异常统计：scene 无法解析 {n_none}；同场景多 key 场景数 {n_multikey}；"
          f"key∧quality=low {n_keylow}；枚举/类型异常 {n_badkey}；"
          f"缺失条目窗口 {n_lack}；编造索引窗口 {n_extra}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
