"""P2：音频分析（§6.3）——mock LLM 下的事件写入/静音过滤/失败状态机。"""

from __future__ import annotations

import array
import base64
import io
import json
import sqlite3
import struct
import wave
from pathlib import Path

import pytest

from lvdata_mcp.analysis.audio import AudioAnalyzer
from lvdata_mcp.config import LvdataMcpConfig
from lvdata_mcp.providers import ApiError

DEV = "b53985c7-0000-4000-8000-000000000001"
SID = "019fabe0-3b0f-7947-8000-1e567def787d"
AUDIO = "019fabe1-0000-0000-0000-000000000001.ogg"


class FakeLlm:
    """可编程 fake：按调用次数依次返回 (content, usage) 或抛异常。"""

    max_attempts = 3

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls = 0
        self.model = "fake-audio"

    async def chat(self, system, user_content, enable_thinking=None):
        self.calls += 1
        if not self.responses:
            return ('{"transcript": "x"}', None)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _seed(root: Path, *, file_path: str | None = None,
          file_size: int = 2000) -> sqlite3.Connection:
    """file_size 默认 2000（> EMPTY_AUDIO_SIZE 1024）：正常分片语义；
    空文件场景显式传小值。"""
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
    conn.execute(
        "INSERT INTO files (rsession_id, device_uuid, file_type, file_ext, "
        "file_path, file_size, uuid_ts_ms, analysis_status) "
        "VALUES (?,?,?,?,?,?,?, 'pending')",
        (SID, DEV, "audio", "ogg", file_path or f"{root}/{DEV}/{SID}/audio/{AUDIO}",
         file_size, 1784876100000))
    conn.commit()
    sess = root / DEV / SID / "audio"
    sess.mkdir(parents=True)
    (sess / AUDIO).write_bytes(b"fake ogg")
    return conn


def _analyzer(root: Path, fake: FakeLlm, sample_rate: int = 0) -> AudioAnalyzer:
    """默认 sample_rate=0（直传 ogg）——测试不依赖真实 ffmpeg。"""
    cfg = LvdataMcpConfig(
        data={"root_dir": str(root)},
        watcher={"timezone": "Asia/Shanghai", "concurrency": 2,
                 "audio_sample_rate": sample_rate})
    return AudioAnalyzer(cfg, client=fake)


def _ev(conn, ref):
    return conn.execute("SELECT * FROM events WHERE event_ref = ?", (ref,)).fetchone()


def _file(conn):
    return conn.execute("SELECT * FROM files LIMIT 1").fetchone()


def _ok_segment() -> tuple[str, dict]:
    content = json.dumps({
        "language": "zh", "transcript": "打开应用非常慢",
        "summary": "用户反馈启动慢",
        "value": "high",
        "emotions": [{"emotion": "frustrated", "confidence": 0.8}],
        "objects": [],
        "people": {"count": 1, "male_count": 1, "female_count": 0,
                   "description": "一名男性在说话"}},
        ensure_ascii=False)
    return content, {"input_tokens": 10, "output_tokens": 5}


def test_audio_ok_writes_event_and_audit(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content, usage = _ok_segment()
    analyzer = _analyzer(root, FakeLlm([(content, usage)]))
    res = asyncio_run(analyzer.process())
    assert res == {"processed": 1, "ok": 1, "silent": 0, "failed": 0}
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    ev = _ev(conn, f"{SID}:audio:{AUDIO.replace('.ogg', '')}")
    assert ev["type"] == "audio" and ev["ts_ms"] == 1784876100000
    assert "打开应用非常慢" in ev["summary"]
    p = json.loads(ev["payload"])
    assert p["transcript"] == "打开应用非常慢" and p["segment_id"] == AUDIO.replace(".ogg", "")
    assert p["duration_s"] == 60  # seed 未设 audio_segment_s → 兜底 60
    # 2026-09-16 二轮 schema：value 新字段，place 与旧三字段均缺席
    assert p["value"] == "high"
    assert p["people"]["count"] == 1
    assert "place" not in p and "background_sounds" not in p and "quality" not in p
    f = _file(conn)
    assert f["analysis_status"] == "ok" and f["fail_count"] == 0
    run = conn.execute("SELECT * FROM analysis_runs").fetchone()
    assert run["kind"] == "audio" and run["status"] == "ok"
    assert run["usage_input_tokens"] == 10
    audit = json.loads((root / "derived" / DEV / SID / "audio"
                        / f"{AUDIO.replace('.ogg', '')}.json").read_text())
    assert audit["parse_ok"] is True and audit["silent"] is False
    conn.close()


def test_audio_duration_falls_back_to_segment_s_when_ogg_unparsable(tmp_path):
    """回落路径（E7′）：ogg 解析失败（seed 写的是 `b"fake ogg"`）→
    duration_s 用 recording_sessions.audio_segment_s。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    conn = _seed(root)
    conn.execute("UPDATE recording_sessions SET audio_segment_s = 30")
    conn.commit()
    conn.close()
    content, usage = _ok_segment()
    analyzer = _analyzer(root, FakeLlm([(content, usage)]))
    asyncio_run(analyzer.process())
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    ev = _ev(conn, f"{SID}:audio:{AUDIO.replace('.ogg', '')}")
    assert json.loads(ev["payload"])["duration_s"] == 30
    conn.close()


# ── 段时长实测 granule（E7′：docs/audio_segment_rotation.md §4-9）────────────
# granule 时钟恒 48kHz（RFC 7845）：content_ms = (last_granule - pre_skip) / 48

_SERIAL = 7


def _ogg_page(payload: bytes, granule: int, *, flags: int = 0, seq: int = 0) -> bytes:
    """手写 OggS 页（lacing + 27 字节头；CRC 解析侧不校验），不依赖 recording 包。"""
    lacing = bytearray()
    remaining = len(payload)
    while remaining >= 255:
        lacing.append(255)
        remaining -= 255
    lacing.append(remaining)
    header = bytearray(27 + len(lacing))
    header[0:4] = b"OggS"
    header[5] = flags
    header[6:14] = granule.to_bytes(8, "little", signed=True)
    header[14:18] = _SERIAL.to_bytes(4, "little")
    header[18:22] = seq.to_bytes(4, "little")
    header[26] = len(lacing)
    header[27:] = bytes(lacing)
    return bytes(header) + payload


def _opus_head(pre_skip: int = 312) -> bytes:
    return struct.pack("<8sBBHIhB", b"OpusHead", 1, 1, pre_skip, 16000, 0, 0)


def _ogg_audio(content_ms: int, *, pre_skip: int = 312) -> bytes:
    """OpusHead 页（BOS）+ 一个数据页；granule 按 writer 同款算法折算。"""
    return (_ogg_page(_opus_head(pre_skip), 0, flags=0x02, seq=0)
            + _ogg_page(b"\x00" * 40, 48 * content_ms, seq=1))


def test_ogg_duration_reads_last_granule_and_subtracts_pre_skip(tmp_path):
    from lvdata_mcp.analysis.audio import ogg_content_duration_s
    with_pre_skip = tmp_path / "a.ogg"
    with_pre_skip.write_bytes(_ogg_audio(2_000))
    assert ogg_content_duration_s(str(with_pre_skip)) == pytest.approx(
        (48 * 2000 - 312) / 48000)
    no_pre_skip = tmp_path / "b.ogg"
    no_pre_skip.write_bytes(_ogg_audio(2_000, pre_skip=0))
    assert ogg_content_duration_s(str(no_pre_skip)) == pytest.approx(2.0)


def test_ogg_duration_last_page_wins_and_skips_minus_one(tmp_path):
    from lvdata_mcp.analysis.audio import ogg_content_duration_s
    p = tmp_path / "c.ogg"
    p.write_bytes(
        _ogg_page(_opus_head(0), 0, flags=0x02, seq=0)
        + _ogg_page(b"\x00" * 40, 48 * 1000, seq=1)      # 1s
        + _ogg_page(b"\x00" * 40, -1, seq=2)             # -1 = 本页无包结束 → 跳过
        + _ogg_page(b"\x00" * 40, 48 * 3000, seq=3))     # 3s ← 末页生效
    assert ogg_content_duration_s(str(p)) == pytest.approx(3.0)


def test_ogg_duration_none_without_pages_and_truncated_uses_last_complete(tmp_path):
    from lvdata_mcp.analysis.audio import ogg_content_duration_s
    not_ogg = tmp_path / "fake.ogg"
    not_ogg.write_bytes(b"fake ogg")
    assert ogg_content_duration_s(str(not_ogg)) is None
    assert ogg_content_duration_s(str(tmp_path / "missing.ogg")) is None

    data = _ogg_audio(2_000)
    page1_len = 27 + 1 + len(_opus_head())          # 头页长度（单段 lacing）
    truncated = tmp_path / "trunc.ogg"
    truncated.write_bytes(data[:page1_len + 5])     # 数据页残缺
    assert ogg_content_duration_s(str(truncated)) == 0.0   # 取最后一个完整页（头页 granule=0）


def test_analyzer_duration_measured_wins_over_config(tmp_path):
    """E7′ 集成：实测 granule 优先于会话 audio_segment_s（App 段卡片总长）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    conn = _seed(root)
    conn.execute("UPDATE recording_sessions SET audio_segment_s = 30")
    conn.commit()
    conn.close()
    (root / DEV / SID / "audio" / AUDIO).write_bytes(_ogg_audio(2_000))  # 实测 ≈2s
    content, usage = _ok_segment()
    analyzer = _analyzer(root, FakeLlm([(content, usage)]))
    asyncio_run(analyzer.process())
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    ev = _ev(conn, f"{SID}:audio:{AUDIO.replace('.ogg', '')}")
    assert json.loads(ev["payload"])["duration_s"] == 2               # 不是 30
    audit = json.loads((root / "derived" / DEV / SID / "audio"
                        / AUDIO.replace(".ogg", ".json")).read_text())
    assert audit["duration_s"] == 2
    conn.close()


def test_segment_duration_coerces_dirty_config(tmp_path):
    """回落值防御（E7′）：脏 session 配置（inf/nan/非数/0/缺失）→ 60，不得抛。

    `int(inf)` 会让整批分析炸掉（同 2026-08-29 空音频 400 的教训）。
    """
    from lvdata_mcp.analysis.audio import segment_duration_s
    missing = {"file_path": str(tmp_path / "nope.ogg")}          # 解析失败 → 走回落
    for dirty in (None, float("inf"), float("nan"), "abc", 0, -5):
        assert segment_duration_s({**missing, "s_audio_segment_s": dirty}) == 60
    assert segment_duration_s({**missing, "s_audio_segment_s": 30}) == 30


def test_audio_empty_file_duration_measured_zero(tmp_path):
    """空文件分支同样走实测（E7′）：仅 header 的 ogg → 0，非配置值。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    conn = _seed(root, file_size=100)
    (root / DEV / SID / "audio" / AUDIO).write_bytes(
        _ogg_page(_opus_head(), 0, flags=0x02, seq=0))
    analyzer = _analyzer(root, FakeLlm([]))
    res = asyncio_run(analyzer.process())
    assert res == {"processed": 1, "ok": 1, "silent": 1, "failed": 0}
    audit = json.loads((root / "derived" / DEV / SID / "audio"
                        / AUDIO.replace(".ogg", ".json")).read_text())
    assert audit["silent"] is True and audit["duration_s"] == 0
    conn.close()


def test_audio_silent_no_event(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content = json.dumps({
        "language": "", "transcript": "", "summary": "",
        "value": "low",
        "emotions": [], "objects": [],
        "people": {"count": 0, "male_count": None, "female_count": None,
                   "description": ""}})
    analyzer = _analyzer(root, FakeLlm([(content, None)]))
    res = asyncio_run(analyzer.process())
    assert res["silent"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert _file(conn)["analysis_status"] == "ok"  # 静音同样算分析完成（§6.3.5）
    audit = json.loads((root / "derived" / DEV / SID / "audio"
                        / f"{AUDIO.replace('.ogg', '')}.json").read_text())
    assert audit["silent"] is True  # 审计 JSON 照写（含判定依据）
    conn.close()


def test_audio_environment_only_is_silent(tmp_path):
    # 模型对无语音音频输出环境描述（summary 非空、objects 非空）→ 仍判静音不写
    # events（2026-09-16 二轮规则：词数>5 或 value=high 才落行；objects 不参与）
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content = json.dumps({
        "language": "", "transcript": "", "summary": "纯音乐，无语音",
        "value": "low",
        "emotions": [],
        "objects": [{"name": "钢琴", "description": "背景钢琴曲"}],
        "people": {"count": 0, "male_count": None, "female_count": None,
                   "description": ""}})
    analyzer = _analyzer(root, FakeLlm([(content, None)]))
    res = asyncio_run(analyzer.process())
    assert res["silent"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    conn.close()


# ── schema 二轮改造（2026-09-16，docs/audio_analysis.md）──────────────────


def test_normalize_value_people_defaults():
    """value/people 缺失/类型错误 → 完整默认值（value 非法 → low；防 None→[]）。"""
    from lvdata_mcp.analysis.audio import normalize_segment

    out = normalize_segment({"language": "zh", "transcript": "hi", "summary": "s"})
    assert out["value"] == "low"
    assert out["people"] == {"count": 0, "male_count": None,
                             "female_count": None, "description": ""}
    assert "place" not in out  # 二轮已删字段不复活

    out = normalize_segment({"value": "HIGH ", "people": ["a"], "objects": None,
                             "emotions": "x"})
    assert out["value"] == "high"           # 大小写/空白归一
    assert isinstance(out["people"], dict)  # 错误类型兜底为 dict（不得变 []）
    assert out["objects"] == [] and out["emotions"] == []

    assert normalize_segment({"value": "medium"})["value"] == "low"  # 非法枚举 → low


def test_word_count_cjk_aware():
    """词数口径（V2）：CJK 字符逐个 + 拉丁词；标点不计——中文不得退化为 1 词。"""
    from lvdata_mcp.analysis.audio import _word_count

    assert _word_count("") == 0
    assert _word_count("还不错。") == 3
    assert _word_count("What's your name?") == 3
    assert _word_count("哈喽，你好。") == 4
    assert _word_count("哈喽，你好。What's your name?") == 4 + 3
    assert _word_count("打开应用非常慢") == 7
    assert _word_count("……？！") == 0


def test_normalize_count_coercion():
    """count 强转：str 数字/float 取整；非法/None/bool → 0；male/female 非法 → null。"""
    from lvdata_mcp.analysis.audio import normalize_segment

    def count(v):
        return normalize_segment({"people": {"count": v}})["people"]["count"]

    assert count("2") == 2
    assert count(None) == 0
    assert count(1.5) == 1
    assert count("abc") == 0
    assert count(True) == 0
    # 毒丸回归：inf/nan 形态必须兜住（不能抛穿 stage，2026-09-16 review）
    assert count("inf") == 0
    assert count("Infinity") == 0
    assert count("1e999") == 0
    assert count(float("inf")) == 0
    assert count(float("nan")) == 0

    out = normalize_segment({"people": {"count": 2, "male_count": "x",
                                        "female_count": 2.0}})
    assert out["people"]["male_count"] is None
    assert out["people"]["female_count"] == 2


def test_is_silent_word_count_or_value():
    """二轮规则（§4）：词数>5 或 value=high 放行；其余静音。"""
    from lvdata_mcp.analysis.audio import is_silent

    base = {"transcript": "", "value": "low"}
    assert is_silent(base) is True
    assert is_silent({**base, "transcript": "还不错。"}) is True       # 3 字 ≤5
    assert is_silent({**base, "transcript": "打开应用非常慢"}) is False  # 7 字 >5
    assert is_silent({**base,
                      "transcript": "What is the weather today my friend"}) is False
    assert is_silent({**base, "value": "high"}) is False               # 空转写豁免
    # 长转写但 value=low：OR 语义放行（内容量优先）
    assert is_silent({**base, "transcript": "请查一下今天最新的科技财经新闻。",
                      "value": "low"}) is False
    # 非判定字段不参与：objects/people 有值但词数低且 low → 仍静音
    assert is_silent({**base, "objects": [{"name": "汽车"}],
                      "people": {"count": 3}}) is True


def test_audio_summary_composition(tmp_path):
    """summary 列 = [transcript, summary, 情感名]（二轮回退 location 置首）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content, usage = _ok_segment()
    analyzer = _analyzer(root, FakeLlm([(content, usage)]))
    asyncio_run(analyzer.process())
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    ev = _ev(conn, f"{SID}:audio:{AUDIO.replace('.ogg', '')}")
    assert ev["summary"].startswith("打开应用非常慢")
    assert "frustrated" in ev["summary"]
    assert "place" not in json.loads(ev["payload"])
    conn.close()


def test_audio_schema_violation_retry_then_ok(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content, usage = _ok_segment()
    # 第一次返回缺键 JSON（schema 违规）→ 重试 → 成功（§6.3.4）
    bad = '{"language": "zh"}'
    analyzer = _analyzer(root, FakeLlm([(bad, None), (content, usage)]))
    res = asyncio_run(analyzer.process())
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert _file(conn)["analysis_status"] == "ok"
    conn.close()


def test_audio_api_failure_fail_count_and_pause(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    analyzer = _analyzer(root, FakeLlm([ApiError("boom")]))
    for _ in range(3):
        res = asyncio_run(analyzer.process())
        assert res["failed"] == 1
        # 退避窗口：模拟 60/300s 已过（真实服务按 BACKOFF_DELAYS 排期重试）
        conn = sqlite3.connect(root / "lvdata_inventory.db")
        conn.execute("UPDATE files SET analyzed_at = '2000-01-01T00:00:00.000Z'")
        conn.commit()
        conn.close()
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    f = _file(conn)
    assert f["analysis_status"] == "failed" and f["fail_count"] == 3
    # fail_count >= 3 → 暂停：不再取批（§5.2）
    res = asyncio_run(analyzer.process())
    assert res["processed"] == 0
    conn.close()


def test_audio_force_resets_fail_count(tmp_path):
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    analyzer = _analyzer(root, FakeLlm([ApiError("boom")]))
    for _ in range(3):
        asyncio_run(analyzer.process())
        conn = sqlite3.connect(root / "lvdata_inventory.db")
        conn.execute("UPDATE files SET analyzed_at = '2000-01-01T00:00:00.000Z'")
        conn.commit()
        conn.close()
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    assert _file(conn)["fail_count"] == 3
    conn.close()
    content, usage = _ok_segment()
    analyzer2 = _analyzer(root, FakeLlm([(content, usage)]))
    res = asyncio_run(analyzer2.process(SID, force=True))
    assert res["ok"] == 1


def test_audio_ogg_400_degrades_to_wav(tmp_path, monkeypatch):
    """ogg 直送 400 → wav 降级重试一次（§6.3.2/§13）。

    新次序（docs/qwen_audio_analysis.md D2）：首个 400 先降思考（不换格式），
    仍 400 才降格式——因此本用例走满三次调用：ogg+思考关 → ogg+无参数 → wav 成功。
    """
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content, usage = _ok_segment()

    class DegradeLlm:
        model = "fake-audio"
        max_attempts = 3

        def __init__(self):
            self.calls = 0

        async def chat(self, system, user_content, enable_thinking=None):
            self.calls += 1
            fmt = user_content[0]["input_audio"]["format"]
            if fmt == "ogg":
                raise ApiError("bad request", status=400)
            return content, usage

    fake = DegradeLlm()
    analyzer = _analyzer(root, fake, sample_rate=0)  # 0 = 直传 ogg
    # wav 放子目录：降级路径会 rm -rf wav 父目录（生产 = mkdtemp 专用目录）
    wav_dir = root / "wavtmp"
    wav_dir.mkdir()
    wav = wav_dir / "conv.wav"
    wav.write_bytes(b"wav")
    seen_rates: list[int] = []

    def fake_convert(ogg, sr):
        seen_rates.append(sr)
        return str(wav)

    monkeypatch.setattr("lvdata_mcp.analysis.audio.convert_ogg_to_wav",
                        fake_convert)
    res = asyncio_run(analyzer.process())
    assert res["ok"] == 1
    assert fake.calls == 3  # 思考降级一次 + ogg→wav 降级一次后成功
    # sample_rate=0（直传）时降级必须用默认采样率，否则 ffmpeg -ar 0 必败
    assert seen_rates == [16000]


def test_audio_thinking_off_by_default(tmp_path):
    """3.8-omni 默认开思考：调用须显式带 enable_thinking=False（D1）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content, usage = _ok_segment()

    class Recorder(FakeLlm):
        def __init__(self, responses):
            super().__init__(responses)
            self.thinking_flags: list[bool | None] = []

        async def chat(self, system, user_content, enable_thinking=None):
            self.thinking_flags.append(enable_thinking)
            return await super().chat(system, user_content, enable_thinking)

    fake = Recorder([(content, usage)])
    res = asyncio_run(_analyzer(root, fake).process())
    assert res["ok"] == 1
    assert fake.thinking_flags == [False]


def test_audio_enable_thinking_400_degrades(tmp_path):
    """enable_thinking=False 400 → 降级一次不再传该参数（D2，同 vision 先例）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content, usage = _ok_segment()

    class Thinking400(FakeLlm):
        def __init__(self, responses):
            super().__init__(responses)
            self.thinking_flags: list[bool | None] = []

        async def chat(self, system, user_content, enable_thinking=None):
            self.thinking_flags.append(enable_thinking)
            if enable_thinking is False:
                self.calls += 1
                raise ApiError("enable_thinking rejected", status=400)
            return await super().chat(system, user_content, enable_thinking)

    fake = Thinking400([(content, usage)])
    res = asyncio_run(_analyzer(root, fake).process())
    assert res["ok"] == 1
    assert fake.thinking_flags == [False, None]


def test_audio_retry_window_blocks_failed_file(tmp_path):
    """failed 文件退避窗口内不再取批（§5.4 同款语义）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    analyzer = _analyzer(root, FakeLlm([ApiError("boom")]))
    asyncio_run(analyzer.process())  # fail_count=1, analyzed_at=now
    res = asyncio_run(analyzer.process())  # 60s 窗口内 → 不取
    assert res["processed"] == 0
    # 模拟 60s 已过
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.execute("UPDATE files SET analyzed_at = '2000-01-01T00:00:00.000Z'")
    conn.commit()
    conn.close()
    res = asyncio_run(analyzer.process())
    assert res["failed"] == 1  # 退避期满 → 重试


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ── 空分片跳过（2026-08-29：400 空音频曾崩掉整个 stage）──────────────

def test_audio_empty_file_skipped_without_llm(tmp_path):
    """file_size < EMPTY_AUDIO_SIZE → 直接判空跳过：不打 LLM、审计 JSON
    照写（silent）、标记 ok，不卡 pending。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    conn = _seed(root, file_size=100)
    fake = FakeLlm([])
    analyzer = _analyzer(root, fake)
    res = asyncio_run(analyzer.process())
    assert res == {"processed": 1, "ok": 1, "silent": 1, "failed": 0}
    assert fake.calls == 0  # 未打 LLM
    audit = json.loads(
        (root / "derived" / DEV / SID / "audio"
         / AUDIO.replace(".ogg", ".json")).read_text())
    assert audit["silent"] is True and audit["parse_ok"] is False
    assert "empty audio file" in audit["error"]
    row = conn.execute(
        "SELECT analysis_status, fail_count FROM files").fetchone()
    assert row["analysis_status"] == "ok" and row["fail_count"] == 0
    conn.close()


# ── 取批限额（P0-2：max_files_per_poll=0 = 不限）──────────────────────────

def test_select_pending_files_zero_limit_unlimited(tmp_path):
    """limit <= 0 → 不加 LIMIT（回归：原实现 LIMIT 0 恒返回空批）。"""
    from lvdata_mcp.analysis.common import select_pending_files
    root = tmp_path / "lvdata"
    root.mkdir()
    conn = _seed(root)
    for i in range(3):
        conn.execute(
            "INSERT INTO files (rsession_id, device_uuid, file_type, file_ext, "
            "file_path, file_size, uuid_ts_ms, analysis_status) "
            "VALUES (?,?,?,?,?,?,?, 'pending')",
            (SID, DEV, "audio", "ogg", f"{root}/{i}.ogg", 100,
             1784876100000 + i))
    conn.commit()
    rows = select_pending_files(conn, "audio", SID, 0)
    assert len(rows) == 4
    rows = select_pending_files(conn, "audio", SID, 2)
    assert len(rows) == 2
    conn.close()


def test_select_pending_ignores_session_status(tmp_path):
    """cs 单链路：取件不按 session 状态过滤——idle 会话文件不再滞留。"""
    from lvdata_mcp.analysis.common import select_pending_files
    root = tmp_path / "lvdata"
    root.mkdir()
    conn = _seed(root)
    conn.execute("UPDATE recording_sessions SET status = 'idle' "
                 "WHERE rsession_id = ?", (SID,))
    conn.commit()
    rows = select_pending_files(conn, "audio", None, 0)  # 全局取件
    assert len(rows) == 1
    assert rows[0]["rsession_id"] == SID
    conn.close()


def test_poll_budget_global_guardrail(tmp_path):
    """§10.2 全局护栏：共享预算跨 process 调用扣减（单轮 poll 上限）。"""
    from lvdata_mcp.pipeline import PollBudget
    b = PollBudget(3)
    assert b.take(2) == 2 and b.remaining == 1
    assert b.take(5) == 1 and b.remaining == 0
    assert b.take(1) == 0  # 耗尽
    b.reset(0)  # 0 = 不限
    assert b.remaining is None and b.take(10) == 10
    b.reset(200)
    assert b.remaining == 200


# -- 能量包络（docs/android_timeline_audio_gallery.md §10 A1′）----------------


def _wav_bytes(values: list[int], rate: int = 16000) -> bytes:
    """常量样本序列 → 单声道 16-bit WAV（每帧 dBFS 可精确预期）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array.array("h", values).tobytes())
    return buf.getvalue()


def _decode(b64: str) -> list[int]:
    return list(base64.b64decode(b64))


def test_energy_payload_known_dbfs(tmp_path):
    """常量幅值帧 → 已知 dBFS → 已知量化值（-20 dBFS → 182；0 dBFS → 255）。"""
    from lvdata_mcp.analysis.audio import compute_energy
    samples = [3277] * 320 + [32767] * 320          # 3277/32768 = -20.0 dBFS
    p = compute_energy("x.wav", _wav_bytes(samples), "wav")
    assert p is not None
    assert (p["energy_v"], p["frame_ms"], p["db_min"], p["db_max"]) == (1, 20, -70.0, 0.0)
    assert _decode(p["energy"]) == [182, 255]
    # 往返：量化值 → dBFS 可还原（客户端归一化用 db_min/db_max）
    assert abs((-70.0 + 182 / 255 * 70.0) - (-20.0)) < 0.2


def test_energy_clamp_and_frame_boundary():
    """低于 db_min 截断为 0；不足一帧的尾巴丢弃；帧数 = 样本数 // 320。"""
    from lvdata_mcp.analysis.audio import compute_energy, energy_payload
    # 1 帧满 + 319 尾巴：幅值 1 → dBFS -90.3 < db_min → 截断为 0
    p = compute_energy("x.wav", _wav_bytes([1] * 639), "wav")
    assert p is not None and _decode(p["energy"]) == [0]
    # 319 样本（不足一帧）→ 无能量
    assert compute_energy("x.wav", _wav_bytes([100] * 319), "wav") is None
    # 空样本 → None
    assert energy_payload(array.array("h"), 16000) is None


def test_energy_unsupported_wav_returns_none():
    """非 WAV 字节 / 立体声 → None（增强项失败不抛、不影响分析主流程）。"""
    from lvdata_mcp.analysis.audio import compute_energy, wav_pcm_s16le
    assert compute_energy("/nonexistent/x.ogg", b"fake ogg", "wav") is None
    stereo = io.BytesIO()
    with wave.open(stereo, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(array.array("h", [1] * 640).tobytes())
    with pytest.raises(ValueError):
        wav_pcm_s16le(stereo.getvalue())
    assert compute_energy("/nonexistent/x.ogg", stereo.getvalue(), "wav") is None


def test_audio_ok_writes_energy_into_payload(tmp_path, monkeypatch):
    """集成：非静音段的事件 payload 含能量字段（尺寸与真机实测同量级）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content, usage = _ok_segment()
    wav = _wav_bytes([3277] * 320 * 3000)           # 60s
    monkeypatch.setattr("lvdata_mcp.analysis.audio.load_audio_bytes",
                        lambda path, sr: (wav, "wav"))
    analyzer = _analyzer(root, FakeLlm([(content, usage)]), sample_rate=16000)
    res = asyncio_run(analyzer.process())
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    p = json.loads(_ev(conn, f"{SID}:audio:{AUDIO.replace('.ogg', '')}")["payload"])
    assert p["energy_v"] == 1 and p["frame_ms"] == 20
    assert len(base64.b64decode(p["energy"])) == 3000
    assert set(_decode(p["energy"])) == {182}


def test_audio_ogg_passthrough_without_ffmpeg_has_no_energy(tmp_path, monkeypatch):
    """直传 ogg（sample_rate=0）+ 假 ogg 文件：ffmpeg 解码失败 → 无能量字段（降级）。"""
    root = tmp_path / "lvdata"
    root.mkdir()
    _seed(root)
    content, usage = _ok_segment()
    analyzer = _analyzer(root, FakeLlm([(content, usage)]), sample_rate=0)
    res = asyncio_run(analyzer.process())
    assert res["ok"] == 1
    conn = sqlite3.connect(root / "lvdata_inventory.db")
    conn.row_factory = sqlite3.Row
    p = json.loads(_ev(conn, f"{SID}:audio:{AUDIO.replace('.ogg', '')}")["payload"])
    assert "energy" not in p and "energy_v" not in p
