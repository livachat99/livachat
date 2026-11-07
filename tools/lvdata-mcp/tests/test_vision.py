"""P2：视频/图像分析（§6.4）——mock LLM 下的窗口分组/事件/原子性。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from lvdata_mcp.analysis.vision import (VisionAnalyzer, group_windows,
                                        group_windows_by_device)
from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.providers import ApiError

DEV = "b53985c7-0000-4000-8000-000000000001"
SID = "019fabe0-3b0f-7947-8000-1e567def787d"

FRAMES = [
    "019fabe1-0000-0000-0000-00000000000a",
    "019fabe1-0000-0000-0000-00000000000b",
    "019fabe1-0000-0000-0000-00000000000c",
    "019fabe1-0000-0000-0000-00000000000d",
]


def _frame_ts(i: int) -> int:
    from lvdata_mcp.timeutil import uuid7_to_ms
    return uuid7_to_ms(FRAMES[i])


def _filter_json(n: int, key_frames: list[int]) -> str:
    """filter 输出 fixture（§11 新 5 键）；scene_id 由模型判定——每个 key 帧起
    一个新场景（1 起）、非 key 帧归当前场景（无同场景多 key，贴近实测形态）。

    frame_id 以 marker 原文 "frame N" 回显（dev 实测模型行为），顺带覆盖
    解析归一 + 审计文件权威字段覆写（review 修复回归）。"""
    frames = []
    scene = 0
    for i in range(n):
        is_key = i in key_frames
        if is_key:
            scene += 1
        frames.append({
            "frame_id": f"frame {i}", "quality": "high",
            "scene_id": scene or 1,
            "key_frame": is_key,
            "reason": "画面变化" if is_key else "重复"})
    return json.dumps({"window_caption": "browser - 编写代码", "frames": frames},
                      ensure_ascii=False)


def _extract_json(n: int) -> str:
    frames = [{
        "frame_id": f"frame {i}", "caption": "IDE 报错页面", "place": "办公室",
        "objects": [], "ocr": [{"text": "Traceback"}],
        "people": []} for i in range(n)]
    return json.dumps({"frames": frames}, ensure_ascii=False)


class FakeVision:
    """按 prompt 分发 filter/extract；可配置失败。"""

    def __init__(self, *, fail_filter: bool = False, fail_extract: bool = False,
                 fail_forever: bool = False, key_frames: list[int] | None = None):
        self.filter_calls = 0
        self.extract_calls = 0
        self.fail_filter = fail_filter
        self.fail_extract = fail_extract
        self.fail_forever = fail_forever
        self.model = "fake-vision"
        self.max_attempts = 3
        self.n_key = 2
        self.key_frames = [0, 1] if key_frames is None else key_frames

    async def chat(self, system, user_content, enable_thinking=None):
        if "逐帧判定" in system:  # FILTER_SYSTEM_PROMPT
            self.filter_calls += 1
            if self.fail_filter:
                raise ApiError("filter boom")
            return _filter_json(len([p for p in user_content
                                     if p.get("type") == "image_url"]),
                                self.key_frames), {"input_tokens": 10,
                                                   "output_tokens": 5}
        self.extract_calls += 1
        if self.fail_extract:
            raise ApiError("extract boom")
        return _extract_json(len([p for p in user_content
                                  if p.get("type") == "image_url"])), \
            {"input_tokens": 8, "output_tokens": 4}


def _seed(root: Path) -> None:
    from lvdata_mcp.ddl import ensure_schema
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn, "Asia/Shanghai", [])
    conn.commit()
    conn.execute(
        "INSERT INTO recording_sessions (rsession_id, device_uuid, session_date, "
        "status, root_path, started_at, finalized_at) VALUES (?,?,?,?,?,?,?)",
        (SID, DEV, "2026-08-01", "finalized", str(root),
         "2026-08-01T00:00:00.000Z", "2026-08-01T01:00:00.000Z"))
    for i, fid in enumerate(FRAMES):
        conn.execute(
            "INSERT INTO files (rsession_id, device_uuid, file_type, file_ext, "
            "file_path, file_size, uuid_ts_ms, analysis_status) "
            "VALUES (?,?,?,?,?,?,?, 'pending')",
            (SID, DEV, "video", "jpg",
             f"{root}/{DEV}/{SID}/video/{fid}.jpg", 300, _frame_ts(i)))
    conn.commit()
    sess = root / DEV / SID / "video"
    sess.mkdir(parents=True)
    for fid in FRAMES:
        (sess / f"{fid}.jpg").write_bytes(_jpeg_bytes())
    conn.close()


def _jpeg_bytes() -> bytes:
    """真实可解码 JPEG（300x200 白底）；Pillow 严格模式拒绝伪 JPEG。"""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _analyzer(root: Path, fake: FakeVision) -> VisionAnalyzer:
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "Asia/Shanghai", "concurrency": 2})
    return VisionAnalyzer(cfg, client=fake)


def _ev(conn, ref):
    return conn.execute("SELECT * FROM events WHERE event_ref = ?", (ref,)).fetchone()


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ── 窗口分组 ─────────────────────────────────────────────────────────────

def test_group_windows_deterministic():
    frames = [{"file_path": f"/x/{f}.jpg", "uuid_ts_ms": _frame_ts(i)}
              for i, f in enumerate(FRAMES)]
    w = group_windows(frames, window_s=30, max_frames=24)
    assert len(w) == 1  # 4 帧都在 30s 窗口内
    assert w[0]["window_id"] == FRAMES[0]
    # 超过 30s → 新窗口；window_id = 新窗口首帧 stem（确定性）
    frames2 = [{"file_path": f"/x/{FRAMES[0]}.jpg", "uuid_ts_ms": 0},
               {"file_path": f"/x/{FRAMES[1]}.jpg", "uuid_ts_ms": 60_000}]
    w2 = group_windows(frames2, window_s=30, max_frames=24)
    assert len(w2) == 2 and w2[1]["window_id"] == FRAMES[1]


def test_group_windows_max_frames():
    frames = [{"file_path": f"/x/f{i}.jpg", "uuid_ts_ms": i * 1000}
              for i in range(50)]
    w = group_windows(frames, window_s=3600, max_frames=24)
    assert [len(x["frames"]) for x in w] == [24, 24, 2]


def _dev_frame(dev: str, ts: int, sid: str = "s1") -> dict:
    return {"file_path": f"/{dev}/{sid}/video/{ts}.jpg", "uuid_ts_ms": ts,
            "s_device_uuid": dev, "rsession_id": sid, "file_id": ts}


def test_group_windows_by_device_isolates_devices():
    """裁定 H：多设备帧按时间交错 → 各自成窗，不混进同一次 filter 调用。"""
    frames = [_dev_frame("devA", 1000), _dev_frame("devB", 1001),
              _dev_frame("devA", 2000), _dev_frame("devB", 2001)]
    windows = group_windows_by_device(frames, window_s=30, max_frames=24)
    assert sum(len(w["frames"]) for w in windows) == 4
    for w in windows:
        assert len({f["s_device_uuid"] for f in w["frames"]}) == 1


def test_group_windows_by_device_merges_cross_session():
    """session 不参与分组：同设备跨 session 的连续帧合并成同一窗口（§2 原则）。"""
    frames = [_dev_frame("devA", 1000, sid="s1"),
              _dev_frame("devA", 1005, sid="s2")]
    windows = group_windows_by_device(frames, window_s=30, max_frames=24)
    assert len(windows) == 1
    assert len(windows[0]["frames"]) == 2


# ── 两阶段分析 ──────────────────────────────────────────────────────────

def test_truncated_jpeg_analyzable(tmp_path):
    """录制中半截写入的 jpg 可分析（LOAD_TRUNCATED_IMAGES，不误判 failed）。

    完全损坏的文件仍走 failed + fail_count（§6.4.5/§5.2）。
    """
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    sess = root / DEV / SID / "video"
    full = (sess / f"{FRAMES[0]}.jpg").read_bytes()
    (sess / f"{FRAMES[0]}.jpg").write_bytes(full[: int(len(full) * 0.6)])
    analyzer = _analyzer(root, FakeVision())
    res = asyncio_run(analyzer.process())
    assert res["ok"] == 1 and res["failed"] == 0


def test_budget_consumes_whole_windows(tmp_path):
    """§6.4.1 预算按整窗消费：30s 窗口不被批界劈开（window_id 确定性）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    analyzer = _analyzer(root, FakeVision())
    # 预算 3 帧 < 窗口 4 帧 → 整窗跳过（不劈开，下轮预算足再处理）
    analyzer.budget.reset(3)
    res = asyncio_run(analyzer.process())
    assert res["processed"] == 0 and res["budget_exhausted"] is True
    # 预算足 → 整窗处理
    analyzer.budget.reset(4)
    res = asyncio_run(analyzer.process())
    assert res["processed"] == 4 and res["windows"] == 1 and res["ok"] == 1


def test_vision_two_step_events_and_files(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    analyzer = _analyzer(root, FakeVision())
    res = asyncio_run(analyzer.process())
    assert res["processed"] == 4 and res["windows"] == 1 and res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    # window 事件（§5.1 payload.window）
    ev = _ev(conn, f"{SID}:window:{FRAMES[0]}")
    assert ev["type"] == "window" and ev["ts_ms"] == _frame_ts(0)
    p = json.loads(ev["payload"])
    assert p["frame_count"] == 4 and p["key_frame_count"] == 2
    assert "static" not in p and p["window_caption"] == "browser - 编写代码"
    # 每关键帧 video 事件
    for fid in FRAMES[:2]:
        vev = _ev(conn, f"{SID}:video:{fid}")
        assert vev["type"] == "video"
        vp = json.loads(vev["payload"])
        assert "key_rank" not in vp  # 二轮删除（quality/value 在窗口审计，不入事件 payload）
        assert "scene" not in vp and "apps" not in vp  # §9 extract schema 精简
        assert vp["place"] == "办公室" and vp["frame_id"] == fid
        # §9.3 video 拼装：summary = caption + place + ocr 文本
        assert vev["summary"] == "IDE 报错页面 办公室 Traceback"
    # 非关键帧无 video 事件
    assert _ev(conn, f"{SID}:video:{FRAMES[2]}") is None
    # 文件全部 ok（窗口原子性）
    rows = conn.execute("SELECT analysis_status, fail_count FROM files").fetchall()
    assert all(r["analysis_status"] == "ok" and r["fail_count"] == 0 for r in rows)
    # 审计 JSON：窗口级 frames 条目携带 filter 判定（含 key_frame）
    wdir = root / "derived" / DEV / SID / "video"
    assert (wdir / "windows" / f"{FRAMES[0]}.json").is_file()
    assert (wdir / "frames" / f"{FRAMES[0]}.json").is_file()
    wdoc = json.loads((wdir / "windows" / f"{FRAMES[0]}.json").read_text())
    f0, f2 = wdoc["frames"][0], wdoc["frames"][2]
    assert f0["quality"] == "high" and f0["key_frame"] is True
    assert f0["scene_id"] == 1
    assert f2["key_frame"] is False and f2["scene_id"] == 2
    assert "duplicated" not in f0 and "duplicate_of" not in f0  # 旧键不复存在
    # review 修复回归：模型回显 "frame N" 不得覆盖审计文件权威 frame_id
    assert f0["frame_id"] == "0"
    fdoc = json.loads((wdir / "frames" / f"{FRAMES[0]}.json").read_text())
    assert fdoc["frame_id"] == FRAMES[0]  # 真帧 UUID（非 chunk 局部索引）
    # analysis_runs
    runs = conn.execute("SELECT kind, status FROM analysis_runs").fetchall()
    assert len(runs) == 6  # filter 4 + extract 2
    conn.close()


def test_parse_tolerates_frame_prefix_ids():
    """回归（2026-09-18 dev 实跑）：模型回显 marker 原文 "frame N" → 解析归一接受
    （extract 阶段稳定复现；filter 可能性同样覆盖）。"""
    from lvdata_mcp.analysis.vision import (parse_extract_output,
                                            parse_filter_output)
    filt = json.dumps({"window_caption": "w", "frames": [
        {"frame_id": "frame 0", "quality": "high", "scene_id": 1,
         "key_frame": True, "reason": "r"},
        {"frame_id": "frame 1", "quality": "low", "scene_id": 1,
         "key_frame": False, "reason": "r"}]}, ensure_ascii=False)
    parsed = parse_filter_output(filt, 2)
    assert set(parsed["entries"]) == {"0", "1"}
    assert parsed["entries"]["0"]["key_frame"] is True

    ext = json.dumps({"frames": [
        {"frame_id": "frame 0", "caption": "c", "place": None,
         "objects": [], "ocr": [], "people": []}]}, ensure_ascii=False)
    assert set(parse_extract_output(ext, 1)) == {"0"}


def test_parse_rejects_fabricated_indices():
    """回归（2026-09-18 review）：模型编造额外索引 → ValueError（走 retry），
    否则下游 `rows[int(fid)]` 越界 IndexError 崩掉整个 video stage。"""
    from lvdata_mcp.analysis.vision import parse_filter_output

    def entry(i, key):
        return {"frame_id": str(i), "quality": "high", "scene_id": i + 1,
                "key_frame": key, "reason": "r"}

    content = json.dumps({"window_caption": "w", "frames": [
        entry(0, False), entry(1, True), entry(2, True)]},
        ensure_ascii=False)
    with pytest.raises(ValueError, match="unexpected frame indices"):
        parse_filter_output(content, 2)  # 窗口只有 2 帧却出现索引 "2"


def test_normalize_scene_id_and_guard():
    """§11：scene_id 归一（int/整数字符串/整值 float→int；bool/负值/其余→None）
    + key_frame bool 归一 + 守卫（D6=B：key∧quality=low → False，含枚举越界连坐）。"""
    from lvdata_mcp.analysis.vision import normalize_frame_entry
    e = normalize_frame_entry({"frame_id": "0", "quality": "high",
                               "scene_id": 1, "key_frame": True, "reason": "r"})
    assert e["key_frame"] is True and e["scene_id"] == 1
    # scene_id 宽容解析
    assert normalize_frame_entry({"scene_id": "2"})["scene_id"] == 2
    assert normalize_frame_entry({"scene_id": 3.0})["scene_id"] == 3
    assert normalize_frame_entry({"scene_id": " 4 "})["scene_id"] == 4
    assert normalize_frame_entry({"scene_id": 0})["scene_id"] == 0
    # 非法 → None（= 本帧独立场景，V1）；含非 ASCII 数字字符（'²'/'１２３'，review 修复）
    for bad in (True, -1, -2.0, "abc", None, 1.5, [], "²", "１２３"):
        assert normalize_frame_entry({"scene_id": bad})["scene_id"] is None, bad
    # 守卫：quality=low 的 key 降级；枚举越界 → low 连坐
    e = normalize_frame_entry({"frame_id": "1", "quality": "low",
                               "scene_id": 1, "key_frame": True, "reason": "r"})
    assert e["key_frame"] is False
    e = normalize_frame_entry({"frame_id": "2", "quality": "medium",
                               "scene_id": 0, "key_frame": 1, "reason": "r"})
    assert e["quality"] == "low" and e["key_frame"] is False  # 越界连坐
    # 字符串 bool + 大小写/空白归一
    e = normalize_frame_entry({"frame_id": "3", "quality": "HIGH ",
                               "scene_id": 5, "key_frame": "false",
                               "reason": "r"})
    assert e["quality"] == "high" and e["key_frame"] is False
    # 缺失 → 默认（key_frame→false；scene_id→None）
    e = normalize_frame_entry({"frame_id": "5"})
    assert e["quality"] == "low" and e["key_frame"] is False
    assert e["scene_id"] is None


def test_norm_frame_id_non_ascii_digit_no_crash():
    """review 修复（2026-10-07）：非 ASCII 数字字符（'²'）不得触发 int() 崩溃——
    isascii 守卫后原样保留，交由索引完整性校验（缺/编造 → retry）。"""
    from lvdata_mcp.analysis.vision import _norm_frame_id
    assert _norm_frame_id("²") == "²"
    assert _norm_frame_id("frame ²") == "²"
    assert _norm_frame_id("１２３") == "１２３"
    assert _norm_frame_id("frame 00") == "0"  # ASCII 路径不变


def test_suppress_scene_duplicate_keys_unit():
    """§11.2：同窗同 scene_id 只留帧索引最小 key；None（非法值）不参与分组。"""
    from lvdata_mcp.analysis.vision import (normalize_frame_entry,
                                            suppress_scene_duplicate_keys)
    entries = {str(i): normalize_frame_entry(f) for i, f in enumerate([
        {"frame_id": "0", "quality": "high", "scene_id": 1, "key_frame": True},
        {"frame_id": "1", "quality": "high", "scene_id": 1, "key_frame": True},
        {"frame_id": "2", "quality": "high", "scene_id": "2", "key_frame": True},
        {"frame_id": "3", "quality": "high", "scene_id": None, "key_frame": True},
        {"frame_id": "4", "quality": "high", "scene_id": None, "key_frame": True},
    ])}
    assert suppress_scene_duplicate_keys(entries) == [("1", "0", ["1"])]
    assert [i for i, e in entries.items() if e["key_frame"]] == ["0", "2", "3", "4"]
    assert suppress_scene_duplicate_keys(entries) == []  # 幂等


def test_scene_duplicate_key_suppressed_e2e(tmp_path, monkeypatch):
    """§11.2 实弹：窗口全标同场景多 key → 仅帧索引最小者产事件；其余降级、
    写 warning log（此处以 monkeypatch 捕获 log 调用断言）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)

    class SameScene(FakeVision):
        async def chat(self, system, user_content, enable_thinking=None):
            if "逐帧判定" in system:
                self.filter_calls += 1
                n = len([p for p in user_content if p.get("type") == "image_url"])
                frames = [{"frame_id": f"frame {i}", "quality": "high",
                           "scene_id": 1, "key_frame": True, "reason": "r"}
                          for i in range(n)]
                return json.dumps({"window_caption": "w", "frames": frames},
                                  ensure_ascii=False), \
                    {"input_tokens": 1, "output_tokens": 1}
            return await super().chat(system, user_content, enable_thinking)

    from unittest.mock import MagicMock
    fake_log = MagicMock()
    monkeypatch.setattr("lvdata_mcp.analysis.vision.log", fake_log)
    res = asyncio_run(_analyzer(root, SameScene()).process())
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    evs = [r["event_ref"] for r in conn.execute(
        "SELECT event_ref FROM events WHERE type='video'")]
    assert evs == [f"{SID}:video:{FRAMES[0]}"]  # 4 帧同场景全 key → 只留帧 0
    w = _ev(conn, f"{SID}:window:{FRAMES[0]}")
    assert json.loads(w["payload"])["key_frame_count"] == 1
    conn.close()
    wdoc = json.loads((root / "derived" / DEV / SID / "video" / "windows"
                       / f"{FRAMES[0]}.json").read_text())
    assert [f["key_frame"] for f in wdoc["frames"]] == [True, False, False, False]
    fake_log.warning.assert_called_once_with(
        "scene duplicate key suppressed", window_id=FRAMES[0], scene_id="1",
        kept_frame="0", suppressed_frames=["1", "2", "3"])


def test_normalize_extract_place_nullish():
    """§9.2：place 非字符串/空串/字面量 null/none（不区分大小写）→ None；
    正常值 strip 保留。"""
    from lvdata_mcp.analysis.vision import normalize_extract_entry
    base = {"frame_id": "0", "caption": "c", "objects": [], "ocr": [],
            "people": []}
    for raw in (None, "", "  ", "null", "NULL", "none", 0, [], {}):
        assert normalize_extract_entry({**base, "place": raw})["place"] is None, raw
    e = normalize_extract_entry({**base, "place": "  办公桌  "})
    assert e["place"] == "办公桌"
    # 缺失字段默认 None（宽松归一，§9.2）
    assert normalize_extract_entry(base)["place"] is None


def test_video_summary_text_composition():
    """§9.3：summary = caption + place（非空）+ ocr 文本；place=null 不占位，
    已删字段（scene/apps/actions）不再影响拼装。"""
    from lvdata_mcp.analysis.vision import VisionAnalyzer
    f = VisionAnalyzer._video_summary_text
    assert f({"caption": "画面", "place": "卧室",
              "ocr": [{"text": "A"}, {"text": "B"}]}) == "画面 卧室 A B"
    assert f({"caption": "画面", "place": None,
              "ocr": [{"text": "A"}]}) == "画面 A"
    assert f({"caption": "画面", "place": "卧室", "ocr": []}) == "画面 卧室"
    # 前版字段不再参与（若 payload 残留也不应拼入）
    assert f({"caption": "画面", "place": None, "scene": "browser",
              "apps": [{"name": "VSCode"}], "actions": ["调试"],
              "ocr": []}) == "画面"


def test_static_window_produces_no_events(tmp_path):
    """§10 防御路径：模型输出全 key_frame=false → 不产 window/video 事件；
    文件 ok、审计 JSON 照写（§0.3 静窗零事件语义保留为防御）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    analyzer = _analyzer(root, FakeVision(key_frames=[]))
    res = asyncio_run(analyzer.process())
    assert res["processed"] == 4 and res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    rows = conn.execute("SELECT analysis_status FROM files").fetchall()
    assert all(r["analysis_status"] == "ok" for r in rows)
    conn.close()
    # 审计 JSON 照写（窗口级），extract 未调用
    wdir = root / "derived" / DEV / SID / "video"
    assert (wdir / "windows" / f"{FRAMES[0]}.json").is_file()
    assert not (wdir / "frames" / f"{FRAMES[0]}.json").exists()


def test_vision_filter_failure_window_failed(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    analyzer = _analyzer(root, FakeVision(fail_filter=True))
    res = asyncio_run(analyzer.process())
    assert res["failed"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT analysis_status, fail_count FROM files").fetchall()
    assert all(r["analysis_status"] == "failed" and r["fail_count"] == 1
               for r in rows)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    conn.close()


def test_fabricated_index_window_failed_not_crash(tmp_path, monkeypatch):
    """回归（2026-09-18 review）：filter 持续编造索引 → 整窗 failed 走退避重试，
    不抛 IndexError（修复前会崩掉整个 video stage）。"""
    async def no_sleep(_):
        return None
    monkeypatch.setattr("asyncio.sleep", no_sleep)
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)

    class Fabricated(FakeVision):
        async def chat(self, system, user_content, enable_thinking=None):
            if "逐帧判定" in system:
                self.filter_calls += 1
                n = len([p for p in user_content
                         if p.get("type") == "image_url"])
                frames = [{"frame_id": str(i), "quality": "high",
                           "scene_id": 1, "key_frame": False,
                           "reason": "r"} for i in range(n)]
                frames.append({"frame_id": str(n), "quality": "high",
                               "scene_id": 2, "key_frame": True,
                               "reason": "编造"})
                return json.dumps({"window_caption": "w", "frames": frames},
                                  ensure_ascii=False), \
                    {"input_tokens": 1, "output_tokens": 1}
            return await super().chat(system, user_content, enable_thinking)

    fake = Fabricated()
    res = asyncio_run(_analyzer(root, fake).process())
    assert fake.filter_calls >= 3          # 重试耗尽（非一次即崩）
    assert res["ok"] == 0 and res["failed"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT analysis_status, fail_count FROM files").fetchall()
    assert all(r["analysis_status"] == "failed" and r["fail_count"] == 1
               for r in rows)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    conn.close()


def test_vision_extract_failure_whole_window_retry(tmp_path):
    """extract 失败 → 整窗 failed；重试后整窗成功（窗口原子性，§6.4.6）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    fake = FakeVision(fail_extract=True)
    analyzer = _analyzer(root, fake)
    res = asyncio_run(analyzer.process())
    assert res["failed"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT analysis_status, fail_count FROM files").fetchall()
    assert all(r["analysis_status"] == "failed" and r["fail_count"] == 1
               for r in rows)
    conn.close()
    # 退避窗口后重试 → 成功（同一 window_id，幂等 upsert）
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("UPDATE files SET analyzed_at = '2000-01-01T00:00:00.000Z'")
    conn.commit()
    conn.close()
    fake2 = FakeVision()
    analyzer2 = _analyzer(root, fake2)
    res = asyncio_run(analyzer2.process())
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert _ev(conn, f"{SID}:window:{FRAMES[0]}") is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'window'").fetchone()[0] == 1  # 无重复
    conn.close()


def test_vision_force_resets_fail_count(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    fake = FakeVision(fail_filter=True)
    analyzer = _analyzer(root, fake)
    asyncio_run(analyzer.process())
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    conn.execute("UPDATE files SET analyzed_at = '2000-01-01T00:00:00.000Z'")
    conn.commit()
    conn.close()
    analyzer2 = _analyzer(root, FakeVision())
    res = asyncio_run(analyzer2.process(SID, force=True))
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert [r["fail_count"] for r in conn.execute(
        "SELECT fail_count FROM files").fetchall()] == [0, 0, 0, 0]
    conn.close()


def test_vision_enable_thinking_400_degrades(tmp_path):
    """enable_thinking=False 400 → 降级重试一次（§6.4.2/§13）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)

    class Thinking400(FakeVision):
        def __init__(self):
            super().__init__()
            self.thinking_off = True

        async def chat(self, system, user_content, enable_thinking=None):
            if self.thinking_off and enable_thinking is False:
                self.thinking_off = False
                raise ApiError("enable_thinking rejected", status=400)
            return await super().chat(system, user_content, enable_thinking)

    fake = Thinking400()
    analyzer = _analyzer(root, fake)
    res = asyncio_run(analyzer.process())
    assert res["ok"] == 1
