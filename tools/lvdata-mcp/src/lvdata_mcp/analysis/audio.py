"""音频分析（§6.3）：进程内替代 lvdata_analyze_audio.py。

- 取批 files（pending/failed、fail_count<3、退避窗口，单轮上限 max_files_per_poll）；
- ffmpeg 转码（audio_sample_rate；0 = 直传 ogg；400 降级转码重试一次）；
- 百炼 Omni（模型见 providers.audio.model）streaming + json_object，
  schema = SEGMENT_KEYS（2026-09-16 二轮：删 place、新增 value；
  objects/people/emotions 降级为 payload 信息，见 docs/audio_analysis.md）；
- 3.8-omni 默认开思考（reasoning_effort=xhigh）：显式 enable_thinking=False，
  模型拒收（400）降级一次（docs/qwen_audio_analysis.md D1/D2）；
- 静音过滤：transcript 词数 ≤5（CJK 感知）且 value≠high → 不写 events（审计 JSON 照写）；
- events upsert（非静音）+ 审计 JSON + files 状态机 + analysis_runs。
"""

from __future__ import annotations

import asyncio
import array
import base64
import io
import json
import math
import os
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import structlog

from ..config import LvdataMcpConfig
from ..language import output_language_rule
from ..pipeline import PollBudget
from ..providers import ApiError, LlmClient, parse_json
from ..timeutil import now_iso, uuid7_to_ms
from .common import (
    atomic_write,
    derived_dir,
    mark_file_failed,
    mark_file_ok,
    record_run,
    select_pending_files,
    upsert_event,
)

log = structlog.get_logger("lvdata_mcp.audio")

# 空分片判定阈值：残缺 ogg（如录音中断残留的 0.1KB 文件）转码后为空，
# 打 API 只会收到 400 "The audio is empty"（2026-08-29 曾因异常逃逸崩掉
# 整个 stage）。小于该值的音频直接判空跳过。
EMPTY_AUDIO_SIZE = 1024

SEGMENT_KEYS = [
    "language", "transcript", "summary", "value", "emotions", "objects",
    "people",
]

AUDIO_SYSTEM_PROMPT = (
    "你是专业的音频内容分析引擎。你只输出 JSON，不输出任何其他文本或解释。\n"
    "分析用户提供的音频，严格按照以下 JSON Schema 输出：\n"
    "{\"language\": \"主要语言代码，如 zh / en\", "
    "\"transcript\": \"完整转写文本，保留口语原样、不修正口误；无语音则空串\", "
    "\"summary\": \"一句话总结该片段内容\", "
    "\"value\": \"总体价值评估（low|high）：high=有实质信息或重要内容"
    "（有信息量的对话/陈述、有意义的分析、播报、演讲、重要事件声音）；"
    "low=无信息量（寒暄与填充词、纯底噪/环境杂音、无意义的短句、仅提示音）\", "
    "\"emotions\": [{\"emotion\": \"happy|sad|angry|surprised|frustrated|neutral|other\", "
    "\"confidence\": 0.0-1.0, \"evidence\": \"依据，如笑声、语速\"}], "
    "\"objects\": [{\"name\": \"物体名\", \"description\": \"描述\"}], "
    "\"people\": {\"count\": \"人数（整数；无人或无法判断填 0）\", "
    "\"male_count\": \"男性人数，无法辨识填 null\", "
    "\"female_count\": \"女性人数，无法辨识填 null\", "
    "\"description\": \"人物描述\"}}\n"
    "要求：transcript 必须完整转写全部语音内容；数组字段可以为空数组、"
    "对象字段必须存在；不确定的信息填 null，不要编造；value 必须给出；"
    "只输出 JSON，不要使用 Markdown 代码块包裹。"
)


def _audio_system_prompt(language: str) -> str:
    target = output_language_rule(language)
    return AUDIO_SYSTEM_PROMPT + (
        "\n\n输出语言要求：summary、emotions[].evidence、objects[].name、"
        f"objects[].description、people.description 使用 {target} 书写；"
        "transcript 保留原语言；language 字段填检测到的源语言代码；"
        "value/emotions[].emotion 等枚举与 JSON 字段名保持不变。"
    )


# -- 段时长实测（E7′：docs/audio_segment_rotation.md §4-9）----------------------
_OGG_MAGIC = b"OggS"
_OGG_HEADER_SIZE = 27
_OPUS_HEAD_MAGIC = b"OpusHead"
_OPUS_GRANULE_RATE = 48000  # Ogg granule 时钟恒 48kHz（RFC 7845）


def ogg_content_duration_s(path: str) -> float | None:
    """OGG/Opus 内容时长（秒）：顺序扫页取最后有效 granule，减 OpusHead pre-skip。

    录制段不插静音 ⇒ granule 即内容时钟（段内空洞被压缩），与 cs
    `recording_files.duration_ms` 同语义（差 pre-skip 6.5ms，wire 侧 round 到秒）。
    顺序扫描天然抗截断；**找不到任何 OggS 页**（或文件不可读）→ None，由调用方回落配置值。
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    granule: int | None = None
    pre_skip = 0
    pos = 0
    size = len(data)
    while pos + _OGG_HEADER_SIZE <= size:
        if data[pos:pos + 4] != _OGG_MAGIC:
            break
        g = int.from_bytes(data[pos + 6:pos + 14], "little", signed=True)
        nsegs = data[pos + 26]
        body_start = pos + _OGG_HEADER_SIZE + nsegs
        if body_start > size:
            break
        payload_len = sum(data[pos + _OGG_HEADER_SIZE:body_start])
        if body_start + payload_len > size:
            break
        if pre_skip == 0:
            head = data[body_start:body_start + payload_len]
            if head.startswith(_OPUS_HEAD_MAGIC) and len(head) >= 12:
                pre_skip = int.from_bytes(head[10:12], "little")
        if g >= 0:  # -1 = 本页无包结束（writer 不产生，防御第三方文件）
            granule = g
        pos = body_start + payload_len
    if granule is None:
        return None
    return max(granule - pre_skip, 0) / _OPUS_GRANULE_RATE


def _coerce_segment_s(value: Any) -> int:
    """会话配置段长（秒）→ int：非数 / 非有限 / <=0 / 缺失 → 60。

    手写或异常 session.json 可能落进 inf（JSON `1e999`）——`int(inf)` 会抛
    OverflowError 炸掉整批分析（同 2026-08-29 空音频 400 的教训：分析 stage
    不得被单个脏值带崩）。
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 60
    if not math.isfinite(seconds) or seconds <= 0:
        return 60
    return int(seconds)


def segment_duration_s(row: dict) -> int:
    """段时长（秒，int）：实测 ogg granule 优先，解析失败回落会话配置值（E7′）。

    实测值用 `is not None` 判断——0.0（空段/仅 header 的 ogg）是合法实测值，
    不应回落 config；负 pre-skip 已在上游夹紧到 0。
    """
    measured = ogg_content_duration_s(row.get("file_path") or "")
    if measured is None:
        return _coerce_segment_s(row.get("s_audio_segment_s"))
    return round(measured)


def convert_ogg_to_wav(ogg_path: str, sample_rate: int) -> str:
    """ogg → 16k 单声道 wav（临时目录）；失败抛 RuntimeError。"""
    tmp_dir = tempfile.mkdtemp(prefix="lvdata_wav_")
    wav_path = os.path.join(tmp_dir, "segment.wav")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", ogg_path, "-ar", str(sample_rate), "-ac", "1",
         "-f", "wav", wav_path],
        capture_output=True)
    if result.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(
            f"ffmpeg conversion failed: "
            f"{result.stderr.decode(errors='replace')[-200:]}")
    return wav_path


def load_audio_bytes(path: str, sample_rate: int) -> tuple[bytes, str]:
    """同步加载音频：sample_rate=0 直传 ogg；否则 ffmpeg 转 wav 后读入。

    转码 + 文件读 + 临时目录清理整体在线程中执行（调用方 asyncio.to_thread），
    避免阻塞 asyncio 事件循环（§3.2.5 外部调用即 IO）。
    """
    if sample_rate == 0:
        with open(path, "rb") as f:
            return f.read(), "ogg"
    wav_path = convert_ogg_to_wav(path, sample_rate)
    try:
        with open(wav_path, "rb") as f:
            return f.read(), "wav"
    finally:
        shutil.rmtree(os.path.dirname(wav_path), ignore_errors=True)


def _coerce_count(value: Any) -> int | None:
    """人数强转：int 原样 / float 取整 / 数字串解析；其余 → None。

    inf/nan 必须兜住（OverflowError/ValueError）——模型输出 "inf"/"Infinity"/
    1e999 时未捕获会让 normalize_segment 抛穿，整个 audio stage 失败且该文件
    不落失败态 → 每轮重试再炸（毒丸）。故 int() 两侧都按 (ValueError,
    OverflowError) 兜底。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return None
    if isinstance(value, str):
        s = value.strip()
        try:
            return int(s)
        except ValueError:
            try:
                return int(float(s))
            except (ValueError, OverflowError):
                return None
    return None


def normalize_segment(d: dict) -> dict:
    """schema 缺省值归一化（口径见 audio_analysis.md §3，2026-09-16 二轮 schema）。

    先置循环只覆盖 list 类键——people 为 dict，不得被 None→[] 兜底污染。
    缺失/类型错误一律给完整默认值（推断字段不编造：性别 → null；value 非法 → low）。
    """
    out = dict(d)
    for key in ("emotions", "objects"):
        if not isinstance(out.get(key), list):
            out[key] = []
    for key in ("language", "transcript", "summary"):
        if not isinstance(out.get(key), str):
            out[key] = ""
    value = out.get("value")
    value = value.strip().lower() if isinstance(value, str) else ""
    out["value"] = value if value in ("low", "high") else "low"
    people = out.get("people")
    if not isinstance(people, dict):
        people = {}
    count = _coerce_count(people.get("count"))
    out["people"] = {
        "count": count if count is not None else 0,
        "male_count": _coerce_count(people.get("male_count")),
        "female_count": _coerce_count(people.get("female_count")),
        "description": (people.get("description")
                        if isinstance(people.get("description"), str) else ""),
    }
    return out


def missing_schema_keys(d: dict) -> list[str]:
    return [k for k in SEGMENT_KEYS if k not in d]


# 词数口径（audio_analysis.md §2.3/V2）：CJK 字符逐字 + 拉丁词按词——中文无
# 空格，str.split() 会把整句算成 1 个"词"，必须按字符计。
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _word_count(text: str) -> int:
    """转写词数（CJK 感知）：CJK 字符逐个 + 拉丁词（[A-Za-z0-9']+）计数，标点不计。"""
    if not text:
        return 0
    return len(_CJK_RE.findall(text)) + len(_LATIN_WORD_RE.findall(text))


def is_silent(parsed: dict) -> bool:
    """§6.3.5 静音判定（2026-09-16 二轮重定义）：词数 + 总体价值两维度。

    写 events 的条件（取或）：transcript 词数 > 5（CJK 感知）或 value=high；
    否则按静音处理、不落行。objects/people/emotions 不参与（payload 信息）；
    summary/emotions 亦不参与（模型对无语音音频会把环境描述写进 summary）。
    入参须为 normalize_segment 后的结果（value ∈ {low,high}）。
    """
    words = _word_count(parsed.get("transcript") or "")
    return not (words > 5 or parsed.get("value") == "high")


# -- 能量包络（docs/android_timeline_audio_gallery.md §10，A1′：随 payload 下发）----
ENERGY_VERSION = 1
ENERGY_FRAME_MS = 20                     # 原生帧（16k 下 320 样本；与 Opus 包时间对齐）
ENERGY_DB_MIN = -70.0                    # 量化下界（实测事件段峰值 ≥ −30 dBFS ⇒ ≥40 dB 余量）
ENERGY_DB_MAX = 0.0
_FULL_SCALE = 32768.0


def frame_dbfs(samples: array.array, frame_samples: int) -> list[float]:
    """逐帧 RMS → dBFS（不足一帧的尾巴丢弃）。"""
    out: list[float] = []
    for i in range(0, len(samples) - frame_samples + 1, frame_samples):
        acc = 0.0
        for x in samples[i:i + frame_samples]:
            acc += x * x
        rms = math.sqrt(acc / frame_samples)
        out.append(20.0 * math.log10(max(rms, 1.0) / _FULL_SCALE))
    return out


def quantize_energy(dbfs: list[float]) -> str:
    """dBFS → uint8（先 clamp 到 [db_min, db_max]）→ base64。"""
    span = ENERGY_DB_MAX - ENERGY_DB_MIN
    raw = bytes(
        min(255, max(0, int((v - ENERGY_DB_MIN) / span * 255.0 + 0.5)))
        for v in dbfs
    )
    return base64.b64encode(raw).decode()


def wav_pcm_s16le(data: bytes) -> tuple[array.array, int]:
    """WAV 字节（ffmpeg 产物，含头）→ (s16le 样本, 采样率)；非 16-bit 单声道 → 抛异常。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(
                f"unsupported wav: {w.getsampwidth() * 8}bit/{w.getnchannels()}ch")
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    samples = array.array("h")
    samples.frombytes(frames)
    return samples, rate


def energy_payload(samples: array.array, rate: int) -> dict | None:
    """每 ENERGY_FRAME_MS 一帧的量化包络；帧数不足 1 → None。"""
    frame_samples = rate * ENERGY_FRAME_MS // 1000
    if frame_samples <= 0:
        return None
    dbfs = frame_dbfs(samples, frame_samples)
    if not dbfs:
        return None
    return {
        "energy_v": ENERGY_VERSION,
        "frame_ms": ENERGY_FRAME_MS,
        "db_min": ENERGY_DB_MIN,
        "db_max": ENERGY_DB_MAX,
        "energy": quantize_energy(dbfs),
    }


def _decode_pcm_s16le(path: str, rate: int = 16000) -> tuple[array.array, int]:
    """ffmpeg → 16k 单声道裸 PCM（sample_rate=0 直传 ogg 时的兜底路径）。"""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ar", str(rate), "-ac", "1",
         "-f", "s16le", "-"],
        capture_output=True, check=True, timeout=120)
    samples = array.array("h")
    samples.frombytes(proc.stdout)
    return samples, rate


def compute_energy(path: str, audio_bytes: bytes, audio_format: str) -> dict | None:
    """§10 能量字段：优先用分析路径已加载的 WAV 字节；直传 ogg 时补一次 ffmpeg 解码。

    任何不支持/解码失败 → None（客户端据此降级为纯轨道，不报错）。
    仅对**非静音段**调用（静音段不落事件、无卡片，见 §10.1）。
    """
    try:
        if audio_format == "wav":
            samples, rate = wav_pcm_s16le(audio_bytes)
        else:
            samples, rate = _decode_pcm_s16le(path)
        return energy_payload(samples, rate)
    except Exception as e:  # noqa: BLE001 — 能量是增强项，失败不影响分析主流程
        log.warning("energy skipped", segment=path, error=f"{type(e).__name__}: {e}")
        return None


class AudioAnalyzer:
    def __init__(self, cfg: LvdataMcpConfig, client: LlmClient | None = None,
                 budget: PollBudget | None = None):
        self.cfg = cfg
        self.root = cfg.data.root_dir
        self.db_path = cfg.data.resolved_db_path
        self.client = client or LlmClient(cfg.providers.audio.resolved("audio"),
                                          cfg.watcher.llm_timeout_s)
        self.sample_rate = cfg.watcher.audio_sample_rate
        self.budget = budget if budget is not None else PollBudget()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    async def process(self, session_id: str | None = None,
                      force: bool = False) -> dict:
        """分析一批 pending 音频文件（§6.3.1）；单轮上限 = 共享 poll 预算。

        force：重置 fail_count/状态后重跑（全局；带 session_id 时限定会话）。
        """
        with self._conn() as conn:
            if force:
                sql = ("UPDATE files SET fail_count = 0, "
                       "analysis_status = 'pending', error = NULL "
                       "WHERE file_type = 'audio'")
                params: list = []
                if session_id:
                    sql += " AND rsession_id = ?"
                    params.append(session_id)
                conn.execute(sql, params)
                conn.commit()
            rows = select_pending_files(conn, "audio", session_id,
                                        self.budget.remaining)
            granted = self.budget.take(len(rows))
            files = [dict(r) for r in rows[:granted]]
        if not files:
            log.debug("audio batch empty", session_id=session_id, force=force,
                      budget_exhausted=len(rows) > 0)
            return {"processed": 0, "budget_exhausted": len(rows) > 0}
        sem = asyncio.Semaphore(self.cfg.watcher.concurrency)

        async def one(row: dict) -> dict:
            async with sem:
                return await self._analyze_one(row)

        results = await asyncio.gather(*[one(f) for f in files])
        log.info("audio batch done", session_id=session_id, force=force,
                 processed=len(results),
                 ok=sum(1 for r in results if r["status"] == "ok"),
                 silent=sum(1 for r in results if r.get("silent")),
                 failed=sum(1 for r in results if r["status"] == "failed"))
        return {"processed": len(results), "ok": sum(1 for r in results
                                                     if r["status"] == "ok"),
                "silent": sum(1 for r in results if r.get("silent")),
                "failed": sum(1 for r in results if r["status"] == "failed")}

    async def _analyze_one(self, row: dict) -> dict:
        seg_id = Path(row["file_path"]).stem
        derived = derived_dir(self.root, row["s_device_uuid"], row["rsession_id"]) \
            / "audio" / f"{seg_id}.json"
        started = now_iso()
        t0 = asyncio.get_event_loop().time()

        if row["file_size"] < EMPTY_AUDIO_SIZE:
            # 空分片直接判空跳过：不打 API，审计 JSON 照写（静音语义），
            # 状态按 ok 处理，避免卡在 pending 无限重试。
            atomic_write(derived, json.dumps({
                "schema_version": 1,
                "segment_id": seg_id,
                "session_id": row["rsession_id"],
                "device_uuid": row["s_device_uuid"],
                "ts_ms": row["uuid_ts_ms"],
                "ts_utc": row["uuid_ts_utc"],
                "model": self.client.model,
                "analyzed_at": now_iso(),
                "duration_s": segment_duration_s(row),
                "parse_ok": False,
                "silent": True,
                "error": f"empty audio file ({row['file_size']} bytes)",
            }, ensure_ascii=False, indent=2))
            with self._conn() as conn:
                mark_file_ok(conn, row["file_id"])
                record_run(conn, row["file_id"], "audio", self.client.model,
                           "ok", None, 0, None, started)
                conn.commit()
            log.warning("audio segment empty, skipped",
                        segment_id=row["file_path"], size=row["file_size"])
            return {"segment_id": seg_id, "status": "ok", "silent": True,
                    "latency_ms": 0}

        audio_bytes: bytes
        audio_format = "wav"
        try:
            audio_bytes, audio_format = await asyncio.to_thread(
                load_audio_bytes, row["file_path"], self.sample_rate)
        except Exception as e:  # noqa: BLE001 — 转码失败按分析失败处理
            return self._finish_failed(row, derived, started, t0, f"convert: {e}")

        parsed: dict | None = None
        usage: dict | None = None
        error: str | None = None
        # 思考默认关（3.8-omni 默认 xhigh）；若模型/快照拒收 enable_thinking（400）→
        # 降级一次不再传该参数（docs/qwen_audio_analysis.md D2，同 vision §6.4.2 纪律）。
        # 注意：400 无法区分"拒收思考参数"与"格式/音频错误"——首个 400 被乐观地当作前者
        # （多耗一次调用）；且降级落点是"不传参数"= 模型默认开思考，故降级必须留日志。
        thinking_off = True
        for attempt in range(self.client.max_attempts):
            try:
                content, usage = await self._call_audio(
                    audio_bytes, audio_format,
                    enable_thinking=False if thinking_off else None)
                parsed = parse_json(content)
                missing = missing_schema_keys(parsed)
                if missing:
                    if attempt < self.client.max_attempts - 1:
                        continue  # schema 违规重试（§6.3.4）
                    parsed = normalize_segment(parsed)
                    error = f"schema missing after retries: {missing}"
                else:
                    parsed = normalize_segment(parsed)
                break
            except ApiError as e:
                if e.status == 400 and thinking_off \
                        and attempt < self.client.max_attempts - 1:
                    # 降级落点 = 不传思考控制 = 3.8 默认开思考（xhigh；实测延迟 ×7、
                    # 输出 tokens ×3）。打 warning 使该退化可观测（否则静默变慢变贵）。
                    thinking_off = False
                    log.warning(
                        "audio enable_thinking rejected (400), retry without "
                        "thinking control — model default enables thinking",
                        segment_id=row["file_path"], attempt=attempt + 1)
                    continue
                # ogg 直送 400 → wav 降级重试一次（§6.3.2/§13）。
                # sample_rate=0（直传）时降级必须用默认采样率，否则 -ar 0 必败
                if audio_format == "ogg" and e.status == 400 \
                        and attempt < self.client.max_attempts - 1:
                    try:
                        audio_bytes, audio_format = await asyncio.to_thread(
                            load_audio_bytes, row["file_path"],
                            self.sample_rate or 16000)
                        continue
                    except Exception:
                        pass
                error = f"api: {e}"
                break
            except json.JSONDecodeError as e:
                if attempt >= self.client.max_attempts - 1:
                    error = f"{type(e).__name__}: {e}"
                    break
                # 模型输出坏 JSON：重试（瞬态错误重试归属 LlmClient.chat）
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
        if error or parsed is None:
            return self._finish_failed(row, derived, started, t0, error)

        # 审计 JSON（§6.3.5：静音与失败同路径照写，含判定依据）
        doc = {
            "schema_version": 1,
            "segment_id": seg_id,
            "session_id": row["rsession_id"],
            "device_uuid": row["s_device_uuid"],
            "ts_ms": row["uuid_ts_ms"],
            "ts_utc": row["uuid_ts_utc"],
            "model": self.client.model,
            "analyzed_at": now_iso(),
            "duration_s": segment_duration_s(row),
            "parse_ok": error is None,
            "silent": is_silent(parsed),
            "error": error,
            **parsed,
        }
        atomic_write(derived, json.dumps(doc, ensure_ascii=False, indent=2))

        silent = doc["silent"]
        latency = int((asyncio.get_event_loop().time() - t0) * 1000)
        with self._conn() as conn:
            if not silent:
                # summary 拼装：emotions 只取情感名（str(dict) 是检索噪音）
                emotion_text = " ".join(
                    str(e.get("emotion")) for e in parsed["emotions"]
                    if isinstance(e, dict) and e.get("emotion"))
                # 能量包络（§10 A1′）：只对非静音段算（静音段不落事件、无卡片）
                energy = await asyncio.to_thread(
                    compute_energy, row["file_path"], audio_bytes, audio_format)
                payload = {"segment_id": seg_id, "duration_s": doc["duration_s"], **parsed}
                if energy:
                    payload.update(energy)
                upsert_event(
                    conn, f"{row['rsession_id']}:audio:{seg_id}",
                    row["s_device_uuid"], row["rsession_id"], "audio",
                    row["uuid_ts_ms"] or 0, "files",
                    " ".join(str(x) for x in [parsed["transcript"],
                                              parsed["summary"], emotion_text]
                             if x),
                    payload,
                    ts_utc=row["uuid_ts_utc"])
            mark_file_ok(conn, row["file_id"])
            record_run(conn, row["file_id"], "audio", self.client.model, "ok",
                       usage, latency, None, started)
            conn.commit()
        return {"segment_id": seg_id, "status": "ok", "silent": silent,
                "latency_ms": latency}

    async def _call_audio(self, audio_bytes: bytes, audio_format: str,
                          *, enable_thinking: bool | None = None) -> tuple[str, dict | None]:
        user_content = [
            {"type": "input_audio",
             "input_audio": {"format": audio_format,
                             "data": f"data:;base64,{base64.b64encode(audio_bytes).decode()}"}},
            {"type": "text", "text": "请按系统提示的 JSON Schema 分析这段音频。"},
        ]
        return await self.client.chat(_audio_system_prompt(self.cfg.language),
                                      user_content,
                                      enable_thinking=enable_thinking)

    def _finish_failed(self, row: dict, derived: Path, started: str,
                       t0: float, error: str | None) -> dict:
        latency = int((asyncio.get_event_loop().time() - t0) * 1000)
        # 失败审计 JSON 照写（§6.3.5，含失败依据）
        atomic_write(derived, json.dumps({
            "schema_version": 1,
            "segment_id": Path(row["file_path"]).stem,
            "session_id": row["rsession_id"],
            "device_uuid": row["s_device_uuid"],
            "ts_ms": row["uuid_ts_ms"],
            "model": self.client.model,
            "analyzed_at": now_iso(),
            "parse_ok": False,
            "silent": False,
            "error": error,
        }, ensure_ascii=False, indent=2))
        with self._conn() as conn:
            mark_file_failed(conn, row["file_id"], error or "unknown")
            record_run(conn, row["file_id"], "audio", self.client.model,
                       "failed", None, latency, error, started)
            conn.commit()
        log.warning("audio segment failed", segment_id=row["file_path"],
                    error=error)
        return {"segment_id": Path(row["file_path"]).stem, "status": "failed",
                "error": error, "latency_ms": latency}
