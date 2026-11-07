"""视频/图像分析（§6.4）：进程内替代 lvdata_analyze_video.py。

两阶段（v1 默认 --pipeline two-step）：
- filter：uuid_ts_ms 排序，video_window_s 窗口分组（window_id = 首帧 stem，
  确定性/可续跑）；单次多图调用，输出逐帧判定（quality/scene_id/key_frame）+
  window_caption；**关键帧 = 模型判定的 key_frame**——同一场景最多一帧（取该
  场景画质最高帧），过渡/黑屏场景可为零；归一含 D6 守卫（quality=low 降级）与
  §11.2 场景级抑制（同窗同 scene_id 只留帧索引最小 key，触发写 warning log）
  （docs/video_analysis.md §10/§11）；
- extract：关键帧按 video_extract_chunk_size 分 chunk 提取。

窗口原子性：整窗（filter + 全部关键帧 extract）完成才标记文件 ok；
任一失败 → 整窗文件 failed + fail_count+1（下轮整窗重跑，幂等 upsert，
window_id 不漂移）。events：window 事件（ts=window_start_ms，**关键帧集
为空则不产**）+ 每关键帧 video 事件。审计 JSON：windows/<window_id>.json +
frames/<frame_id>.json（仅人工核查）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import random
import sqlite3
from pathlib import Path
from typing import Any

import structlog

from ..config import LvdataMcpConfig
from ..language import output_language_rule
from ..pipeline import PollBudget
from ..providers import ApiError, LlmClient, parse_json
from ..timeutil import now_iso
from .common import (
    atomic_write,
    derived_dir,
    mark_file_failed,
    mark_file_ok,
    record_run,
    select_pending_files,
    upsert_event,
)

log = structlog.get_logger("lvdata_mcp.vision")

WINDOW_KEYS = ["window_caption", "frames"]
FRAME_KEYS = ["frame_id", "quality", "scene_id", "key_frame", "reason"]
EXTRACT_KEYS = ["caption", "place", "objects", "ocr", "people"]

FILTER_SYSTEM_PROMPT = (
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

EXTRACT_SYSTEM_PROMPT = (
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


def _filter_system_prompt(language: str) -> str:
    target = output_language_rule(language)
    return FILTER_SYSTEM_PROMPT + (
        f"\n\n输出语言要求：window_caption、reason 使用 {target} 书写；"
        "frame_id/quality/scene_id/key_frame 与 JSON 字段名保持不变。"
    )


def _extract_system_prompt(language: str) -> str:
    target = output_language_rule(language)
    return EXTRACT_SYSTEM_PROMPT + (
        "\n\n输出语言要求：caption、place、objects[].name、objects[].description、"
        f"people[].description、ocr[].location 使用 {target} 书写；"
        "ocr[].text 保留画面原文；JSON 字段名与结构值保持不变。"
    )


def load_image_b64(path: str, max_size: int) -> str:
    """JPEG → base64 data-URL；长边 > max_size 时 Pillow LANCZOS 缩放。"""
    if max_size and max_size > 0:
        try:
            from PIL import Image, ImageFile
            # 录制中半截写入的 jpg 允许尽力解码（§5.2 不误判 failed）；
            # 完全损坏的文件仍抛错 → 整窗 failed + fail_count（§6.4.5）
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            img = Image.open(path).convert("RGB")
            w, h = img.size
            longest = max(w, h)
            if longest > max_size:
                scale = max_size / longest
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            pass
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def group_windows(frames: list[dict], window_s: int,
                  max_frames: int) -> list[dict]:
    """按 ts 分组窗口；window_id = 首帧 stem（确定性/可续跑，§6.4.1）。"""
    windows: list[dict] = []
    cur: list[dict] = []
    win_start: int | None = None
    for f in sorted(frames, key=lambda x: x["uuid_ts_ms"]):
        if (win_start is None
                or f["uuid_ts_ms"] - win_start > window_s * 1000
                or len(cur) >= max_frames):
            if cur:
                windows.append(_make_window(cur))
            cur, win_start = [f], f["uuid_ts_ms"]
        else:
            cur.append(f)
    if cur:
        windows.append(_make_window(cur))
    return windows


def group_windows_by_device(frames: list[dict], window_s: int,
                            max_frames: int) -> list[dict]:
    """device 分组 + 组内时间窗（裁定 H；session 不参与分组）。

    多设备并发录制时纯时间戳会把两台设备的屏幕混进同一次 filter 调用
    （duplicate/novelty 判定失真）；跨 session 连续帧合并成同一窗口是预期
    行为（§2 处理原则），window/derived 产物归属首帧所属 session。
    """
    by_device: dict[str, list[dict]] = {}
    for f in frames:
        by_device.setdefault(f["s_device_uuid"], []).append(f)
    windows: list[dict] = []
    for group in by_device.values():
        windows.extend(group_windows(group, window_s, max_frames))
    return windows


def _make_window(frames: list[dict]) -> dict:
    return {
        "window_id": Path(frames[0]["file_path"]).stem,
        "start_ms": frames[0]["uuid_ts_ms"],
        "end_ms": frames[-1]["uuid_ts_ms"],
        "frames": frames,
    }


def _norm_frame_id(raw: Any) -> str:
    """frame_id 归一：容忍模型回显图片 marker 原文（"frame 3" → "3"）。

    2026-09-18 dev 实跑发现：extract 阶段模型稳定回显 "frame N" 原文（与 prompt
    旧/新首行无关），旧解析裸 str() 比对 → 缺索引 → 整窗重试耗尽失败。归一后
    "0"/0/"frame 0" 三种形态均接受；纯数字串再经 int 规整（"00" → "0"）。
    """
    s = str(raw).strip()
    if s.lower().startswith("frame"):
        s = s[len("frame"):].strip()
    if s.isascii() and s.isdigit():  # 仅 ASCII（'²' 等 isdigit 为真但 int() 抛错）
        s = str(int(s))
    return s


def _norm_bool(v: Any, default: bool) -> bool:
    """bool 归一（docs/video_analysis.md §10 D4，保留供 key_frame）：原生 bool；
    int/float 0|1；"true"/"false"（strip/lower）；其余/缺失 → default。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return default


def _norm_scene_id(v: Any) -> int | None:
    """scene_id 归一（docs/video_analysis.md §11 V1/V2）：int / 整数字符串 /
    整值 float → int；bool / 负值 / 其余 → None（语义 = 本帧独立场景）。"""
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


def normalize_frame_entry(e: dict) -> dict:
    """帧条目归一化（docs/video_analysis.md §10/§11）。

    quality：∉{low,high} → "low"（降噪优先，不变）；
    scene_id：int/整数字符串/整值 float → int；非法/负值 → None（= 本帧独立
    场景，§11 V1）；
    key_frame：bool 归一（D4）+ **守卫（D6=B）**：key_frame=True 且 quality
    （归一后）=="low" → False——存储中不存在 key∧quality=low 组合。
    """
    out = dict(e)
    q = out.get("quality")
    q = q.strip().lower() if isinstance(q, str) else ""
    out["quality"] = q if q in ("low", "high") else "low"
    out["scene_id"] = _norm_scene_id(out.get("scene_id"))
    out["key_frame"] = (_norm_bool(out.get("key_frame"), False)
                        and out["quality"] != "low")
    return out


def suppress_scene_duplicate_keys(
        entries: dict[str, dict]) -> list[tuple[str, str, list[str]]]:
    """§11.2 场景级 key 抑制：同窗同一有效 scene_id 至多保留 1 个 key frame
    （帧索引最小者），其余强制降级。在逐帧归一（含 D6 守卫）之后、`key_ids`
    计算之前调用。

    返回 ``[(scene_id, 保留帧, 被抑制帧列表), ...]`` 供调用方写 warning log；
    ``scene_id=None``（非法值，V1）= 本帧独立场景，不参与分组。
    """
    by_scene: dict[int, list[int]] = {}
    for fid, e in entries.items():
        if e.get("key_frame") is True and e.get("scene_id") is not None:
            by_scene.setdefault(e["scene_id"], []).append(int(fid))
    out: list[tuple[str, str, list[str]]] = []
    for scene, idxs in by_scene.items():
        if len(idxs) <= 1:
            continue
        idxs.sort()  # 帧索引序，不依赖模型输出顺序
        kept, rest = str(idxs[0]), [str(i) for i in idxs[1:]]
        for i in rest:
            entries[i]["key_frame"] = False
        out.append((str(scene), kept, rest))
    return out


def normalize_extract_entry(e: dict) -> dict:
    """extract 条目归一（§9.2）：caption 非字符串→""；place 空串/字面量
    null/none（不区分大小写）→ None（沿用 2026-09-18 nullish 防御先例）；
    objects/ocr/people 非 list→[]。"""
    out = dict(e)
    for k in EXTRACT_KEYS:
        if k == "caption":
            if not isinstance(out.get(k), str):
                out[k] = ""
        elif k == "place":
            v = out.get(k)
            v = v.strip() if isinstance(v, str) else None
            if not v or v.lower() in ("null", "none"):
                v = None
            out[k] = v
        elif not isinstance(out.get(k), list):
            out[k] = []
    return out


def parse_filter_output(content: str, n_frames: int) -> dict:
    """解析 + 校验 filter 输出；schema 违规抛 ValueError（调用方重试）。"""
    parsed = parse_json(content)
    missing = [k for k in WINDOW_KEYS if k not in parsed]
    if missing:
        raise ValueError(f"window schema missing: {missing}")
    frames = parsed.get("frames") if isinstance(parsed.get("frames"), list) else []
    if not frames:
        raise ValueError("empty frames array")
    entries: dict[str, dict] = {}
    for f in frames:
        if not isinstance(f, dict):
            raise ValueError("frame entry not an object")
        fid = _norm_frame_id(f.get("frame_id"))
        if fid in entries:
            continue
        bad = [k for k in FRAME_KEYS if k not in f]
        if bad:
            raise ValueError(f"frame {fid} schema missing: {bad}")
        entries[fid] = normalize_frame_entry(f)
    expected = {str(i) for i in range(n_frames)}
    # 编造索引必须与缺索引同样走 retry：否则下游 rows[int(fid)] 越界崩溃
    # 整个 video stage（2026-09-18 review 修复）
    extra_idx = sorted((k for k in entries if k not in expected),
                       key=lambda k: (len(k), k))
    if extra_idx:
        raise ValueError(f"unexpected frame indices: {extra_idx}")
    missing_idx = [str(i) for i in range(n_frames) if str(i) not in entries]
    if missing_idx:
        raise ValueError(f"missing frame indices: {missing_idx}")
    return {"window_caption": parsed.get("window_caption", ""),
            "entries": entries}


def parse_extract_output(content: str, n_frames: int) -> dict[str, dict]:
    parsed = parse_json(content)
    frames = parsed.get("frames") if isinstance(parsed.get("frames"), list) else []
    if not frames:
        raise ValueError("extract: empty frames array")
    entries: dict[str, dict] = {}
    for f in frames:
        if not isinstance(f, dict):
            raise ValueError("extract: frame entry not an object")
        fid = _norm_frame_id(f.get("frame_id"))
        if fid not in entries:
            entries[fid] = normalize_extract_entry(f)
    missing_idx = [str(i) for i in range(n_frames) if str(i) not in entries]
    if missing_idx:
        raise ValueError(f"extract missing frame indices: {missing_idx}")
    return entries


class VisionAnalyzer:
    def __init__(self, cfg: LvdataMcpConfig, client: LlmClient | None = None,
                 budget: PollBudget | None = None):
        self.cfg = cfg
        self.root = cfg.data.root_dir
        self.db_path = cfg.data.resolved_db_path
        self.client = client or LlmClient(cfg.providers.vision.resolved("vision"),
                                          cfg.watcher.llm_timeout_s)
        self.window_s = cfg.watcher.video_window_s
        self.max_frames_per_call = cfg.watcher.video_max_frames_per_call
        self.extract_chunk_size = cfg.watcher.video_extract_chunk_size
        self.max_image_size = cfg.watcher.max_image_size
        self.budget = budget if budget is not None else PollBudget()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    async def process(self, session_id: str | None = None,
                      force: bool = False) -> dict:
        """分析一批 pending 视频帧（§6.4.1）。

        force：重置 fail_count/状态后重跑（全局；带 session_id 时限定会话）。
        分组（裁定 H）：按 device_uuid 分组后组内时间窗——多设备并发帧不混流；
        session 不参与分组（存储语义），跨 session 连续帧合并成同一窗口，
        window/derived 产物归属首帧所属 session。
        """
        with self._conn() as conn:
            if force:
                sql = ("UPDATE files SET fail_count = 0, "
                       "analysis_status = 'pending', error = NULL "
                       "WHERE file_type = 'video'")
                params: list = []
                if session_id:
                    sql += " AND rsession_id = ?"
                    params.append(session_id)
                conn.execute(sql, params)
                conn.commit()
            if self.budget.remaining is not None and self.budget.remaining <= 0:
                return {"processed": 0, "budget_exhausted": True}
            # 批尾留 max_frames_per_call-1 余量：窗口不被批界劈开
            # （window_id 确定性，§6.4.1）；预算按整窗消费（窗口原子性）
            fetch = None if self.budget.remaining is None \
                else self.budget.remaining + self.max_frames_per_call - 1
            rows = select_pending_files(conn, "video", session_id, fetch)
            files = [dict(r) for r in rows]
            windows = group_windows_by_device(files, self.window_s,
                                              self.max_frames_per_call)
            if self.budget.remaining is not None:
                kept, consumed = [], 0
                for w in windows:
                    n = len(w["frames"])
                    if consumed + n > self.budget.remaining:
                        break
                    kept.append(w)
                    consumed += n
                windows = kept
                self.budget.take(consumed)
        if not windows:
            log.debug("video batch empty", session_id=session_id, force=force,
                      budget_exhausted=len(files) > 0)
            return {"processed": 0, "budget_exhausted": len(files) > 0}
        sem = asyncio.Semaphore(self.cfg.watcher.concurrency)

        async def one(window: dict) -> dict:
            async with sem:
                return await self._process_window(window)

        results = await asyncio.gather(*[one(w) for w in windows])
        log.info("video batch done", session_id=session_id, force=force,
                 windows=len(windows),
                 processed=sum(len(w["frames"]) for w in windows),
                 ok=sum(1 for r in results if r["status"] == "ok"),
                 failed=sum(1 for r in results if r["status"] == "failed"))
        return {"processed": sum(len(w["frames"]) for w in windows),
                "windows": len(windows),
                "ok": sum(1 for r in results if r["status"] == "ok"),
                "failed": sum(1 for r in results if r["status"] == "failed")}

    async def _process_window(self, window: dict) -> dict:
        rows = window["frames"]
        row0 = rows[0]
        dev, rsid = row0["s_device_uuid"], row0["rsession_id"]
        window_id = window["window_id"]
        wdir = derived_dir(self.root, dev, rsid) / "video" / "windows"
        fdir = derived_dir(self.root, dev, rsid) / "video" / "frames"
        started = now_iso()
        t0 = asyncio.get_event_loop().time()

        # ── filter ──
        try:
            # 图片解码/缩放在线程中执行（Pillow C 实现释放 GIL，可并行），
            # 避免阻塞 asyncio 事件循环（§3.2.5 外部调用即 IO）
            images = await asyncio.gather(*(asyncio.to_thread(
                load_image_b64, r["file_path"], self.max_image_size)
                for r in rows))
        except Exception as e:  # noqa: BLE001
            return self._window_failed(rows, window_id, started, t0, f"load: {e}")

        caption, entries, usage, error = await self._filter(images, len(rows))
        if error or entries is None:
            return self._window_failed(rows, window_id, started, t0, error)

        # §11.2 场景级抑制：同窗同 scene_id 只留帧索引最小 key；触发写 log
        for scene, kept, rest in suppress_scene_duplicate_keys(entries):
            log.warning("scene duplicate key suppressed", window_id=window_id,
                        scene_id=scene, kept_frame=kept, suppressed_frames=rest)

        latency = int((asyncio.get_event_loop().time() - t0) * 1000)
        key_ids = [str(i) for i, e in entries.items() if e.get("key_frame")]
        window_doc = {
            "schema_version": 1,
            "window_id": window_id,
            "session_id": rsid,
            "device_uuid": dev,
            "window_start_ms": window["start_ms"],
            "window_end_ms": window["end_ms"],
            "frame_count": len(rows),
            "model": self.client.model,
            "analyzed_at": now_iso(),
            "window_caption": caption,
            # entry 先展开、权威字段后覆写：模型回显的 frame_id（"frame 0" 等）
            # 不得覆盖窗口内局部索引（2026-09-18 review 修复）
            "frames": [{**entries[str(i)], "frame_id": str(i),
                        "ts_ms": r["uuid_ts_ms"]} for i, r in enumerate(rows)],
        }
        atomic_write(wdir / f"{window_id}.json",
                     json.dumps(window_doc, ensure_ascii=False, indent=2))
        with self._conn() as conn:
            if key_ids:
                # 无关键帧的窗口不产任何事件（docs/video_analysis.md §0.3/§10，
                # 2026-09-18 拍板）；审计 JSON 与 record_run 照旧
                upsert_event(conn, f"{rsid}:window:{window_id}", dev, rsid,
                             "window", window["start_ms"], "files", caption, {
                                 "window_id": window_id,
                                 "window_start_ms": window["start_ms"],
                                 "window_end_ms": window["end_ms"],
                                 "frame_count": len(rows),
                                 "window_caption": caption,
                                 "key_frame_count": len(key_ids),
                             }, ts_utc=row0["uuid_ts_utc"])
            for r in rows:
                record_run(conn, r["file_id"], "video", self.client.model,
                           "ok", usage, latency, None, started)
            conn.commit()

        # ── extract（关键帧 chunk=4）──
        if not key_ids:
            with self._conn() as conn:
                for r in rows:
                    mark_file_ok(conn, r["file_id"])
                conn.commit()
            return {"status": "ok", "window_id": window_id,
                    "frames": len(rows), "key_frames": 0}
        key_rows = [rows[int(i)] for i in key_ids]
        ext_results = await self._extract_all(key_rows, window_id, fdir)
        if all(r["status"] == "ok" for r in ext_results):
            with self._conn() as conn:
                for r in rows:
                    mark_file_ok(conn, r["file_id"])
                conn.commit()
            return {"status": "ok", "window_id": window_id,
                    "frames": len(rows),
                    "key_frames": len(ext_results)}
        # 任一 extract 失败 → 整窗 failed（下轮整窗重跑，window_id 不漂移）
        err = next((r["error"] for r in ext_results if r["status"] != "ok"), "extract")
        return self._window_failed(rows, window_id, started, t0, err)

    async def _filter(self, images: list[str], n: int
                      ) -> tuple[str, dict[str, dict] | None, dict | None, str | None]:
        error: str | None = None
        usage: dict | None = None
        thinking_off = True
        for attempt in range(self.client.max_attempts):
            try:
                content, usage = await self.client.chat(
                    _filter_system_prompt(self.cfg.language), self._image_parts(images),
                    enable_thinking=False if thinking_off else None)
                parsed = parse_filter_output(content, n)
                return parsed["window_caption"], parsed["entries"], usage, None
            except ApiError as e:
                # enable_thinking=False 400 → 降级重试一次（§6.4.2/§13）
                if thinking_off and e.status == 400 and attempt < self.client.max_attempts - 1:
                    thinking_off = False
                    continue
                return "", None, usage, f"api: {e}"
            except (ValueError, json.JSONDecodeError) as e:
                if attempt >= self.client.max_attempts - 1:
                    return "", None, usage, f"{type(e).__name__}: {e}"
                # schema 违规/坏 JSON：重试（瞬态错误重试归属 LlmClient.chat）
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
        return "", None, usage, error

    async def _extract_all(self, rows: list[dict], window_id: str,
                           fdir: Path) -> list[dict]:
        results: list[dict] = []
        for i in range(0, len(rows), self.extract_chunk_size):
            chunk = rows[i:i + self.extract_chunk_size]
            results.extend(await self._extract_chunk(chunk, window_id, fdir))
        return results

    async def _extract_chunk(self, rows: list[dict], window_id: str,
                             fdir: Path) -> list[dict]:
        started = now_iso()
        t0 = asyncio.get_event_loop().time()
        try:
            images = await asyncio.gather(*(asyncio.to_thread(
                load_image_b64, r["file_path"], self.max_image_size)
                for r in rows))
        except Exception as e:  # noqa: BLE001
            return self._extract_failed(rows, window_id, fdir, started, t0,
                                        f"load: {e}")
        entries: dict[str, dict] | None = None
        usage: dict | None = None
        error: str | None = None
        thinking_off = True
        for attempt in range(self.client.max_attempts):
            try:
                content, usage = await self.client.chat(
                    _extract_system_prompt(self.cfg.language), self._image_parts(images),
                    enable_thinking=False if thinking_off else None)
                entries = parse_extract_output(content, len(rows))
                break
            except ApiError as e:
                if thinking_off and e.status == 400 and attempt < self.client.max_attempts - 1:
                    thinking_off = False
                    continue
                error = f"api: {e}"
                break
            except (ValueError, json.JSONDecodeError) as e:
                if attempt >= self.client.max_attempts - 1:
                    error = f"{type(e).__name__}: {e}"
                    break
                # schema 违规/坏 JSON：重试（瞬态错误重试归属 LlmClient.chat）
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
        if error or entries is None:
            return self._extract_failed(rows, window_id, fdir, started, t0, error)

        latency = int((asyncio.get_event_loop().time() - t0) * 1000)
        results = []
        with self._conn() as conn:
            for i, r in enumerate(rows):
                entry = entries[str(i)]
                frame_id = Path(r["file_path"]).stem
                doc = {
                    # entry 先展开、权威字段后覆写：模型回显的 frame_id
                    # （chunk 局部索引 "0"/"frame 0"）不得覆盖真帧 UUID
                    # （2026-09-18 review 修复；dev 审计文件曾 12/12 全错）
                    **entry,
                    "schema_version": 1,
                    "frame_id": frame_id,
                    "window_id": window_id,
                    "session_id": r["rsession_id"],
                    "device_uuid": r["s_device_uuid"],
                    "ts_ms": r["uuid_ts_ms"],
                    "ts_utc": r["uuid_ts_utc"],
                    "model": self.client.model,
                    "analyzed_at": now_iso(),
                }
                atomic_write(fdir / f"{frame_id}.json",
                             json.dumps(doc, ensure_ascii=False, indent=2))
                upsert_event(
                    conn, f"{r['rsession_id']}:video:{frame_id}",
                    r["s_device_uuid"], r["rsession_id"], "video",
                    r["uuid_ts_ms"] or 0, "files",
                    self._video_summary_text(entry),
                    {**entry, "frame_id": frame_id, "window_id": window_id},
                    ts_utc=r["uuid_ts_utc"])
                record_run(conn, r["file_id"], "video", self.client.model,
                           "ok", usage, latency, None, started)
                results.append({"frame_id": frame_id, "status": "ok"})
            conn.commit()
        return results

    def _extract_failed(self, rows: list[dict], window_id: str, fdir: Path,
                        started: str, t0: float, error: str | None) -> list[dict]:
        latency = int((asyncio.get_event_loop().time() - t0) * 1000)
        out = []
        with self._conn() as conn:
            for r in rows:
                frame_id = Path(r["file_path"]).stem
                atomic_write(fdir / f"{frame_id}.json", json.dumps({
                    "schema_version": 1, "frame_id": frame_id,
                    "window_id": window_id, "session_id": r["rsession_id"],
                    "device_uuid": r["s_device_uuid"],
                    "ts_ms": r["uuid_ts_ms"], "model": self.client.model,
                    "analyzed_at": now_iso(), "parse_ok": False, "error": error,
                }, ensure_ascii=False, indent=2))
                record_run(conn, r["file_id"], "video", self.client.model,
                           "failed", None, latency, error, started)
                out.append({"frame_id": frame_id, "status": "failed",
                            "error": error})
            conn.commit()
        return out

    def _window_failed(self, rows: list[dict], window_id: str, started: str,
                       t0: float, error: str | None) -> dict:
        latency = int((asyncio.get_event_loop().time() - t0) * 1000)
        dev, rsid = rows[0]["s_device_uuid"], rows[0]["rsession_id"]
        wdir = derived_dir(self.root, dev, rsid) / "video" / "windows"
        atomic_write(wdir / f"{window_id}.json", json.dumps({
            "schema_version": 1, "window_id": window_id,
            "session_id": rsid, "device_uuid": dev,
            "window_start_ms": rows[0]["uuid_ts_ms"],
            "window_end_ms": rows[-1]["uuid_ts_ms"],
            "frame_count": len(rows), "model": self.client.model,
            "analyzed_at": now_iso(), "parse_ok": False, "error": error,
        }, ensure_ascii=False, indent=2))
        with self._conn() as conn:
            for r in rows:
                mark_file_failed(conn, r["file_id"], error or "unknown")
                record_run(conn, r["file_id"], "video", self.client.model,
                           "failed", None, latency, error, started)
            conn.commit()
        log.warning("window failed", window_id=window_id, error=error)
        return {"status": "failed", "window_id": window_id,
                "frames": len(rows), "key_frames": 0, "error": error}

    @staticmethod
    def _image_parts(images: list[str]) -> list:
        parts: list[dict] = []
        for i, b64 in enumerate(images):
            parts.append({"type": "text", "text": f"frame {i}"})
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        parts.append({"type": "text",
                      "text": "请按系统提示的 JSON Schema 分析以上截图。"})
        return parts

    @staticmethod
    def _video_summary_text(entry: dict) -> str:
        """video 检索文本（§9.3）：caption + place（非空）+ ocr 文本。"""
        parts = [entry.get("caption"), entry.get("place")]
        parts.extend(o.get("text") for o in entry.get("ocr") or []
                     if isinstance(o, dict))
        return " ".join(str(x) for x in parts if x)
