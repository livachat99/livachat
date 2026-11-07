from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections import deque
from typing import Any

from llm.base import ChatMessage
from llm.service import LlmService
from media.buffer import AudioFrameBuffer
from recording.sink import ChatImageFrame
from session import ownership
from session.agent_job_ingest import ingest_agent_job_result
from session.av_detector import is_danger_event, is_speech_like_text
from session.context import SessionContext
from session.history import WorkingMemory
from session.interrupt import InterruptController
from session.manager import SessionManager
from session.proactive_i18n import (
    append_reply_language_directive,
    build_desc_language_clause,
    build_output_language_rule,
    camera_label,
    env_prefix,
    resolve_proactive_language,
)
from session.state_machine import SessionState
from session.turn_detector import TurnDetector
from shared.observability.logging import get_logger
from shared.utils.errors import ErrorCode, ProviderError
from shared.utils.ids import generate_message_id, generate_transcription_id
from shared.utils.time import now_ms

logger = logging.getLogger("livachat.orchestrator")

# structlog 版：stdlib 的 extra={} 会被 ProcessorFormatter 丢弃，只有 kwargs 才落 JSON
# （muted 帧 debug 用；docs/android_audio_capture.md §5.2）
kv_logger = get_logger("livachat.orchestrator")

def _count_meaningful_chars(text: str) -> int:
    """Return character count after stripping trailing commas and periods."""
    return len(text.rstrip(",.，。"))


# 请求组装携带的历史图上限（fd_pipeline_llm_v2.md §9.4）：仅最新 N 张进 LLM 请求，
# 其余条目的 images 不组装；history/chat_store/chatlog/恢复仍全量保留
_HISTORY_IMAGE_LIMIT = 2


def _to_data_urls(frames: list[ChatImageFrame]) -> list[str]:
    """AF 快照帧 → data_url（一次编码，history/SQLite 复用；LLM 请求经 history 天然携带）。"""
    return [
        f"data:image/jpeg;base64,{base64.b64encode(f.data).decode()}"
        for f in frames
    ]


def _select_recent_images(entries: list[dict[str, Any]],
                          limit: int = _HISTORY_IMAGE_LIMIT) -> list[list[str] | None]:
    """请求组装用的历史图裁剪（fd_pipeline_llm_v2.md §9.4）：从末尾向前按图计数保留
    最新 limit 张（条目内取尾部帧），返回与 entries 等长的 per-entry 图片列表——未选中的
    条目为 None（只影响请求，不改动 history 条目）。非 list / 空脏值不当作图片（与
    add_message 同规则）。"""
    out: list[list[str] | None] = [None] * len(entries)
    remaining = limit
    for i in range(len(entries) - 1, -1, -1):
        imgs = entries[i].get("images")
        if remaining <= 0 or not isinstance(imgs, list) or not imgs:
            continue
        kept = imgs[-remaining:]
        out[i] = kept
        remaining -= len(kept)
    return out


# translate auto（local_language="auto"）语言码归一化
# 分类对照只与 my_language 比较，必须容忍任意合法 ISO 码（设备语言可能不在枚举内，
# 如 id/vi/fil）：凡 2-3 位小写字母即视为"疑似语言码"；4 位+/含数字/空 → None
_ISO_BASE_RE = re.compile(r"^[a-z]{2,3}$")


def _normalize_lang(code: str) -> str | None:
    """语言码归一化：lower + 去区域后缀（zh-CN→zh），疑似 ISO 校验，非法返回 None。"""
    if not code:
        return None
    base = code.split("-")[0].split("_")[0].strip().lower()
    return base if _ISO_BASE_RE.fullmatch(base) else None


def _parse_auto_translate_json(raw: str) -> dict | None:
    """宽松解析 auto 模式 LLM 的严格 JSON（容忍 ```json 围栏与前后空白）。

    要求 dict 且含字符串 source_language / translation；任何不符返回 None（调用方按
    "LLM 未守约"处理：截取重试 → 仍失败按嫌疑处置）。
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].rstrip().endswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    src = obj.get("source_language")
    tr = obj.get("translation")
    if not isinstance(src, str) or not isinstance(tr, str):
        return None
    return {"source_language": src, "translation": tr}


def _extract_json_object(text: str) -> str | None:
    """轻量截取首个 '{' 至末个 '}' 的子串；无完整花括号对返回 None。

    覆盖两种常见未守约形态：LLM 在 JSON 前后加 prose；ProviderError/中断导致的
    残缺 JSON（有 '{' 无 '}'）。调用方截取后重试解析一次（保持不重试原则）。
    """
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start:end + 1]


# CJK 字与 word 的统一切分/计数。Han 区间一次性覆盖全平面：
# BMP 主区（4E00–9FFF）+ 扩展 A（3400–4DBF）+ 兼容表意字（F900–FAFF）
# + 扩展 B–I 整段（20000–323AF，IRG 预留区，后续 Unicode 大版本汉字基本只落
# 在这里——若未来超出 323AF 再补终点）。stdlib re 无 \p{Script} 语义类；
# 一旦需要把假名/谚文等非 Han 文字也计入 unit，手写区间将难以维护，此时切换
# 第三方 regex 库写法 \p{Hiragana}|\p{Katakana}|\p{Hangul}（需新增依赖）。
_CJK_CHARS = r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\U00020000-\U000323AF]"
_WORD_PAT = r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*"
_CJK_RE = re.compile(_CJK_CHARS)
_WORD_RE = re.compile(_WORD_PAT)
_TOKEN_RE = re.compile(
    # 单 token = 一个 CJK 字 / 一个 word / 其余单字符（标点、空白）
    _CJK_CHARS + r"|" + _WORD_PAT + r"|.",
    re.DOTALL,
)

_TTS_MAX_TEXT_UNITS = 100
# 失败透出 ai_text 的 error message 上限（需求 2：1k 字符，fd_pipeline_llm_v2.md）
_ERROR_TEXT_MAX_CHARS = 1000

# video turn 首帧直起轮的系统注记（fd_pipeline_video_process.md §8.2.3 定稿文案）：
# 开相机/切镜头的"实时画面"回合经 event_note 并入 system——对齐 avdetector [P5]
# 强制回应口径（不复述、不延续历史；请求以 [相机画面] user 行结尾）。
_VIDEO_TURN_NOTE = (
    "[实时画面·系统注入]用户刚开启了摄像头或切换了镜头，最新画面见上一条用户消息。"
    "请立即用一句自然、简短的话回应画面内容，或主动关心/提示；"
    "不要复述、不要延续之前的对话内容。"
)
# 4xx 回滚排除集：408/429 属瞬态可恢复（SDK 已重试过），回滚只针对其余 4xx（D5）
_UNRECOVERABLE_4XX_EXCLUDED = frozenset({408, 429})
# 句末级标点：播报截在此处听起来是完整句收尾
_TTS_FINAL_BOUNDARIES = frozenset("。！？；….!?;\n")
# 句中停顿标点：仅当预算内无句末标点时才作为退路，避免无谓牺牲内容长度
_TTS_MID_BOUNDARIES = frozenset("，、,:")


def _text_units(text: str) -> int:
    """中英统一时长口径：每个 CJK 字符 = 1 unit，每个英文/数字 word = 1 unit，
    标点与空白 = 0 unit。"""
    return len(_CJK_RE.findall(text)) + len(_WORD_RE.findall(text))


def _clip_for_tts(text: str, limit: int = _TTS_MAX_TEXT_UNITS) -> str:
    """超长回答只播报 ≤limit units 的前缀，控制 TTS 播报时长；客户端
    ai_text/history 仍是全文，不受影响。截点优先级（均在预算内）：
    1) 最后一个句末标点（。！？；…!?;\n）——播报以完整句收尾；
    2) 无句末标点则退回最后一个句中停顿标点（，、,:）；
    3) 预算内无任何标点则按 word 边界硬截（不拆词，去空白残留）。"""
    if _text_units(text) <= limit:
        return text
    final_cut: int | None = None
    mid_cut: int | None = None
    units = 0
    for m in _TOKEN_RE.finditer(text):
        tok = m.group()
        if _WORD_RE.fullmatch(tok) or _CJK_RE.fullmatch(tok):
            if units + 1 > limit:
                cut = final_cut if final_cut is not None else mid_cut
                if cut is not None:
                    return text[:cut]
                return text[: m.start()].rstrip()
            units += 1
        elif tok in _TTS_FINAL_BOUNDARIES:
            final_cut = m.end()
        elif tok in _TTS_MID_BOUNDARIES:
            mid_cut = m.end()
    return text  # 不可达：text_units(text) > limit 时必在循环内溢出返回


async def _close_quietly(stream: Any) -> None:
    """null 安全地关闭流；异常语义与既有内联 try/except 一致（吞掉）。

    关闭一律经 asyncio.gather 并发执行：百炼端点不回 close 帧，单条 close 要等满
    close_timeout（provider 侧已限 1s），串行 await 会在派发循环里累计成 10s+ 卡顿
    （见 docs/translate_lecture_improvements.md）。
    """
    if stream is None:
        return
    try:
        await stream.close()
    except Exception:
        pass


class DialogOrchestrator:
    """Core conversation orchestrator (design §4.3)."""

    def __init__(
        self,
        *,
        handler: Any = None,
        codec: Any = None,
        session_mgr: SessionManager | None = None,
        asr_service: Any = None,
        llm_service: LlmService | None = None,
        tts_service: Any = None,
        opus_encoder: Any = None,
        opus_decoder: Any = None,
        system_prompt: str = "",
        translate_prompt: str = "",
        generation_opts: dict[str, Any] | None = None,
        chat_store: Any = None,
        pipeline_mode: str = "full",
        translate_cfg: Any = None,
        recording_sink: Any = None,
        agent_publisher: Any = None,
        omni_service: Any = None,
        omni_system_prompt: str = "",
        omni_voice: str = "",
        omni_generation_opts: dict[str, Any] | None = None,
        pipeline_sample_rate: int | None = None,
        avdetector_service: Any = None,
        avdetector_system_prompt: str = "",
        avdetector_cfg: Any = None,
    ) -> None:
        self._handler = handler
        self._codec = codec
        self._session_mgr = session_mgr
        self._asr = asr_service
        self._llm = llm_service
        self._tts = tts_service
        self._opus_enc = opus_encoder
        self._opus_dec = opus_decoder
        self._system_prompt = system_prompt
        self._translate_prompt = translate_prompt
        self._gen_opts = generation_opts or {}
        self._chat_store = chat_store
        self._pipeline_mode = pipeline_mode
        self._translate_cfg = translate_cfg
        self._recording_sink = recording_sink
        self._agent_publisher = agent_publisher
        # Omni（全模态）模式：服务与开流参数由 endpoint 装配传入
        # （docs/fd_pipeline_omni_mode.md §4 D3/D5）
        self._omni_service = omni_service
        self._omni_system_prompt = omni_system_prompt
        self._omni_voice = omni_voice
        self._omni_gen_opts = omni_generation_opts or {}
        self._pipeline_sample_rate = pipeline_sample_rate
        # avdetector 旁路监测（chat 模式；docs/fd_pipeline_avdetector.md §4 D2）
        self._avdetector_service = avdetector_service
        self._avdetector_system_prompt = avdetector_system_prompt
        self._avdetector_cfg = avdetector_cfg

        self._ctx: SessionContext | None = None
        self._history: WorkingMemory | None = None
        self._turn_detector: TurnDetector | None = None
        self._interrupt: InterruptController | None = None
        self._asr_stream: Any = None
        self._tts_stream: Any = None

        # Task handles for interrupt cancellation
        self._pipeline_task: asyncio.Task[None] | None = None
        self._asr_poll_task: asyncio.Task[None] | None = None

        # avdetector monitor（chat 模式存在，其余模式/未启用为 None）
        self._av_monitor: Any = None
        # monitor 代际：关闭/重建时递增，旧 monitor 的迟到事件据此丢弃
        self._av_monitor_generation: int = 0
        self._last_av_proactive: float = 0.0
        # [D8] cam 首帧回合收尾后的普通画面事件抑制窗（危险豁免）
        self._av_visual_suppress_until: float = 0.0
        # [P6] 在飞 LLM 回合是否危险回合（危险事件据此决定抢占/丢弃）
        self._pipeline_danger: bool = False
        # 竞态复检基准与素材（§4 D2 三重门③；[P6.3] 去帧化——"在说话"只认文字证据）：
        # 触发受理时的 interim 挂起态快照 + 最近一条 interim 文本（"像话"判定用）
        self._av_trigger_interim_pending: bool = False
        self._last_asr_interim_text: str = ""

        # Per-turn transcription id (shared by interim + final)
        self._current_transcription_id: str = ""

        # Guards duplicate session.ended publication (session.end vs. the
        # connection-closed hook in on_connection_closed).
        self._session_ended_published: bool = False

        # Track whether a barge-in turn has started during SPEAKING.
        # Once speech is detected, silence frames must also flow through
        # TurnDetector + ASR so that turn-end can be detected.
        self._barge_in_turn_started: bool = False

        # Minimum character count for a user utterance to trigger the LLM.
        # Single-character filler words (啊, 嗯, 了, etc.) are treated as noise.
        self._min_meaningful_chars: int = 2

        # muted 帧替换 debug 计数（每会话重置；docs/android_audio_capture.md §5.2）
        self._muted_frame_count: int = 0

        # ASR open cooldown to prevent reconnect spam on auth/provider errors
        self._asr_open_fail_time: float = 0.0
        self._asr_retry_cooldown_s: float = 5.0
        # 内联恢复：feed/open 单次失败不立即给客户端报错——先在触发帧上有界重试
        # 重建（每次独立超时 + 短退避），全部失败才进冷却并下发 1001。失败场景
        # 多为上游 task 空闲判死（如百炼 29min 长静默），重建常在一次尝试内成功。
        self._asr_open_attempts: int = 3
        self._asr_open_attempt_timeout_s: float = 2.0
        self._asr_open_retry_backoff_s: float = 0.25
        # 1001 提示节流：故障周期内首败即提示，之后按该间隔周期提醒（ASR 是
        # 关键服务，持续故障需反复可见，但避免每轮 5s 冷却周期刷屏）。复位点
        # 只有两处：新会话开始（session.start）与 feed 成功（流确证可用）。
        # open 成功不算可用——"能连上但立即断连"的抖动会在每轮冷却把节流清零。
        self._asr_last_error_sent_at: float = 0.0
        self._asr_error_reminder_interval_s: float = 30.0

        # Video frame idle timeout tracking
        self._last_video_frame_time: float = 0.0

        # Guard against duplicate final transcription when ASR endpointing and
        # turn-detector silence both fire within the same turn (review #fix-asr-final).
        self._asr_final_sent: bool = False

        # 有 interim 已下发、尚未被任何 final 结算：云端存在"先给有文本 interim、
        # 再以空文本收尾"的撤回行为（真机实测短音句约占 25%）——_poll_asr_results
        # 据此补发一条空 final 让客户端清除残影（否则该 interim 常驻不消失）。
        self._asr_interim_pending: bool = False

        # 最后一帧客户端 VAD=speech 的到达时刻：final 到达时算 "since_speech"，
        # 即服务端视角的收尾时延（语音结束→final）。可区分三种成因：
        # 用户仍在说（值小）、云端慢/静音窗口（值≈mss+固化）、状态门丢帧
        # （THINKING/SPEAKING 期间不喂帧，值会远超预期）。
        self._last_speech_mono: float = 0.0

        # AF 快照帧暂存（fd_pipeline_llm_v2）：路径 B 在 poll 任务快照、路径 A 在
        # 下一音频帧消费（跨任务通道）；多 final 覆盖式写入（latest wins）。
        # 重置块取走并重新绑定新 list——任务私有，barge-in 不污染旧任务。
        self._turn_image_frames: list[ChatImageFrame] = []

        # video turn 直起轮跟踪（fd_pipeline_video_process.md §8.2.3）：上次"服务端见到"的
        # 采集轮；变化 = 新轮首帧 → 立即起 LLM/TTS 回合。会话起点由设备级记忆播种
        # （SessionManager），跨连接/跨会话延续；None = 无记录（按"变化"触发）。
        self._last_video_turn_id: int | None = None

        # Per-utterance detected language from ASR (populated by _poll_asr_results)
        self._last_detected_language: str | None = None

        # Translate mode — dynamic language preferences (set by session.change)
        self._my_language: str | None = None
        self._local_language: str | None = None
        # Translate auto（local_language="auto"）：_loc_auto 标记 auto 模式；
        # _effective_local_language 记录最近一次"对方发言"检测语言（None ≈ en 初值），
        # 每轮翻译完成按 LLM 返回 source_language 覆盖（latest-wins）
        self._loc_auto: bool = False
        self._effective_local_language: str | None = None

        # Lecture translate engine (LiveTranslate streaming interpretation)
        self._lecture_engine: Any = None
        self._lecture_active: bool = False

        # Omni（全模态）子引擎（第四模式；与 lecture 互斥，见设计 D4）
        self._omni_engine: Any = None
        self._omni_active: bool = False

        # Stateful PCM frame buffer for cross-chunk assembly (N4)
        frame_samples = opus_encoder.frame_size if opus_encoder else 480
        self._audio_out_buf = AudioFrameBuffer(frame_samples)

        # 4xx 失败回滚（fd_pipeline_llm_v2.md）：
        # _turn_user_record —— 本轮 user 记录写入句柄
        #   {content, ts_ms, token, device_uuid, session_id}（五值全为写入时值）；
        # _rolled_back_turn_tokens —— 已回滚轮次令牌有界集合（拦路径 B 迟到写）。
        self._turn_user_record: dict[str, Any] | None = None
        self._rolled_back_turn_tokens: deque[str] = deque(maxlen=8)
        self._rolled_back_turn_token_set: set[str] = set()

    def _mark_turn_rolled_back(self, token: str) -> None:
        """登记已回滚轮次令牌（有界 deque+set 对，淘汰最旧；token 天然唯一）。"""
        if len(self._rolled_back_turn_tokens) >= self._rolled_back_turn_tokens.maxlen:
            self._rolled_back_turn_token_set.discard(self._rolled_back_turn_tokens.popleft())
        self._rolled_back_turn_tokens.append(token)
        self._rolled_back_turn_token_set.add(token)

    def _is_turn_rolled_back(self, token: str) -> bool:
        return bool(token) and token in self._rolled_back_turn_token_set

    def _rollback_unrecoverable_turn(self, err: ProviderError, turn_token: str) -> None:
        """4xx 不可恢复失败：回滚本轮 user 记录（WorkingMemory + messages 行）。

        同步完成（无 await）；两步守卫（D5c）：① 登记令牌（先于记录匹配、无条件——
        拦路径 B 迟到写）→ ② 仅当 record.token == turn_token（本轮确实写入过）才删除；
        不匹配（translate 轮不写 / 路径 B 尚未送达 / stale holder）只拦不删，
        防误删历史同文条目。非 4xx（含 408/429 与 http_status=None）不回滚、不加令牌。
        """
        status = err.http_status
        if status is None or not (400 <= status <= 499) or status in _UNRECOVERABLE_4XX_EXCLUDED:
            return
        if turn_token:
            self._mark_turn_rolled_back(turn_token)
        record = self._turn_user_record
        self._turn_user_record = None  # read-and-clear：防同轮重复回滚
        if record is None or record.get("token") != turn_token:
            return
        if self._history is not None:
            self._history.remove_recent_user(record["content"])
        if self._chat_store is not None:
            self._chat_store.delete_message(
                device_uuid=record["device_uuid"],
                session_id=record["session_id"],
                kind="asr",
                role="user",
                ts_ms=record["ts_ms"],
            )

    async def run(self) -> None:
        """Main orchestrator loop — reads inbound queue, dispatches messages."""
        try:
            q = self._handler._inbound
            while not self._handler._stopped:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=0.5)
                except TimeoutError:
                    continue
                await self._dispatch(msg)
                # Fallback: clear video buffer if no frame received for 5 s
                if (self._ctx and self._ctx.enable_video and self._ctx.latest_video_frames
                        and time.monotonic() - self._last_video_frame_time > 5.0):
                    n = len(self._ctx.latest_video_frames)
                    self._ctx.latest_video_frames.clear()
                    logger.debug(
                        "video buffer cleared after idle timeout",
                        extra={"session_id": self._ctx.session_id, "frames_dropped": n},
                    )
        finally:
            # 先关事件源（[P6.5]）：teardown 的长 await 期间 monitor 若存活，
            # 事件回调会起一条无人取消的新 pipeline（_cancel_pipeline 已跑过）
            await self._close_av_monitor()
            # Cancel any in-flight pipeline on stop
            await self._cancel_pipeline()
            await self._cancel_asr_poll()
            # 三路关闭并发（见 _close_quietly）：串行 await 会累计 close 超时
            await asyncio.gather(
                _close_quietly(self._asr_stream),
                _close_quietly(self._tts_stream),
                _close_quietly(self._lecture_engine),
                _close_quietly(self._omni_engine),
            )

    # ------------------------------------------------------------------
    # message dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, msg: Any) -> None:
        t = msg.type
        if t == "session.start":
            await self._handle_session_start(msg)
        elif t == "session.end":
            await self._handle_session_end(msg)
        elif t == "session.change":
            await self._handle_session_change(msg)
        elif t == "audio":
            await self._handle_audio(msg)
        elif t == "interrupt":
            await self._handle_interrupt(msg)
        elif t == "video":
            await self._handle_video(msg)

    # ------------------------------------------------------------------
    # session start
    # ------------------------------------------------------------------

    async def _handle_session_start(self, msg: Any) -> None:
        # 先校验新会话 config，再拆旧会话：显式 null 属于 malformed，不能先把旧
        # monitor/pipeline 拆掉再报错（否则旧会话丢失、新会话又建不起来）。
        cfg = msg.payload.config
        avdetector_enabled = True
        fields_set = getattr(cfg, "model_fields_set", None)
        raw_avdetector = getattr(cfg, "avdetector", None)
        if fields_set is not None and "avdetector" in fields_set:
            if raw_avdetector is None:
                frame = self._codec.make_error(
                    code=ErrorCode.MALFORMED_JSON,
                    message="invalid avdetector: null",
                    fatal=False,
                )
                await self._handler.enqueue_outbound(frame)
                return
            avdetector_enabled = str(raw_avdetector) != "disable"
        elif fields_set is None:
            # 测试替身/旧 DTO 无 model_fields_set：仅能按非 None 值处理
            avdetector_enabled = raw_avdetector is None or str(raw_avdetector) != "disable"

        # 先关上一会话残留的事件源（[P6.5]）：下面 session_mgr.start 等长 await
        # 期间旧 monitor 若存活，_pipeline_mode 已复位 "full" + 状态门全开 →
        # 事件会在新会话边界上起一条 stale 主动回合
        await self._close_av_monitor()
        # [P6.12] 同连接重发 session.start：在飞 pipeline 一并取消——旧任务读
        # `self._ctx/_history` 是**实时**引用，边界后其残余（TTS 尾音/final 落库/
        # status）会直接泄进新会话；epoch 兜底不完全（新旧 ctx 的 epoch 常同为 0，
        # should_yield 判不出换代）。"新会话不得继承上一会话状态"（下述状态复位
        # 注释同款纪律）须连任务一起清
        await self._cancel_pipeline()

        # Reset to default chat mode on every new session (design §3)
        self._pipeline_mode = "full"
        self._my_language = None
        self._local_language = None
        self._loc_auto = False
        self._effective_local_language = None
        # per-turn AF 状态重置：gateway 允许同一连接再次 session.start（不重连，
        # connection.py "Always accept session.start"），新会话不得继承上一会话的
        # 状态——否则 `_asr_final_sent` 吞掉新会话首句的转录/落行，
        # `_turn_image_frames` 把上一会话的帧挂到新会话回复上
        self._asr_final_sent = False
        self._asr_interim_pending = False
        self._turn_image_frames = []
        self._current_transcription_id = ""
        self._barge_in_turn_started = False
        # video turn 直起轮跟踪（§8.6.3）：基准 = 设备级记忆（跨连接/跨会话延续）——
        # turn 变化即触发，不区分 fresh/resume；无记录 → None → 首帧按"变化"触发
        self._last_video_turn_id = self._session_mgr.last_video_turn(
            self._handler.principal.device_id
        )
        # 回滚句柄清于会话边界（D5c）；令牌集合**不清**——有界自然淘汰，
        # 防跨会话迟到写复活（v1 全局 bool 的复活窗口）
        self._turn_user_record = None

        # Close lecture engine from previous session if still alive
        if self._lecture_engine is not None:
            try:
                await self._lecture_engine.close()
            except Exception:
                pass
            self._lecture_engine = None
        self._lecture_active = False

        # Close omni engine from previous session if still alive（同款纪律）
        if self._omni_engine is not None:
            try:
                await self._omni_engine.close()
            except Exception:
                pass
            self._omni_engine = None
        self._omni_active = False

        resume_id = msg.payload.session_id

        ctx, history = await self._session_mgr.start(
            device_id=self._handler.principal.device_id,
            user_id=self._handler.principal.user_id,
            enable_video=cfg.enable_video,
            audio_sample_rate=cfg.audio_sample_rate,
            vad_sensitivity=cfg.vad_sensitivity,
            client_version=cfg.client_version,
            language=cfg.language,
            timezone=cfg.timezone,
            resume_id=resume_id,
            avdetector_enabled=avdetector_enabled,
        )

        # devices upsert（chat store，fire-and-forget 队列；user_id 存 principal
        # 原文，不再做 UUID 改写——chatserver_sqlite_store.md §3.2）
        if self._chat_store is not None:
            self._chat_store.on_device(
                device_uuid=ctx.device_id,
                user_id=ctx.user_id or None,
                client_version=ctx.client_version,
                timezone=ctx.timezone,
            )

        self._ctx = ctx
        self._history = history
        # 会话归属登记：resume（同一 session_id 重连）时接管，旧连接的 ended 发布被跳过
        ownership.take(ctx.session_id, self)
        self._turn_detector = TurnDetector(sensitivity=ctx.vad_sensitivity)
        self._interrupt = InterruptController(ctx)

        # avdetector monitor 重建于每个新会话（chat 模式才挂；gateway 允许同连接重发 session.start）
        self._last_av_proactive = 0.0
        self._av_visual_suppress_until = 0.0
        self._av_trigger_interim_pending = False
        self._last_asr_interim_text = ""
        self._pipeline_danger = False
        self._muted_frame_count = 0
        self._ensure_av_monitor()

        frame = self._codec.make_session_ready(
            session_id=ctx.session_id,
            restored=ctx.restored,
            server_time_ms=now_ms(),
            keepalive_interval_s=15,
        )
        await self._handler.enqueue_outbound(frame)

        # Initial status — design §4.1/§4.2: client SDK state machine
        # depends on an authoritative server status after session.ready.
        # New session: idle → listening; restored: listening directly.
        if ctx.restored:
            await self._send_status("listening", "connected")
        else:
            await self._send_status("idle", "connected")
            await self._send_status("listening", "connected")

        # Agent Relay: notify agent bus of session start
        # timezone=ctx.timezone：设备本机时区（已 canonicalize，未上报 = ""）——relay 用它渲染
        # mission/background prompt 的时区提示（docs/agent_relay_prompts.md §3.2）
        # language=ctx.language：App UI 语言（session.start.config.language）——relay 的
        # agent 用户可见输出基线语言（docs/agent_multilingual.md）；空值由 relay 归一为 "en"
        if self._agent_publisher is not None:
            asyncio.create_task(
                self._agent_publisher.publish_session_started(
                    session_id=ctx.session_id,
                    device_id=self._handler.principal.device_id,
                    timezone=ctx.timezone or None,
                    language=ctx.language or "",
                )
            )

        # 新会话开始：1001 提醒节流清零（与 eager open 成败无关——复位条件是
        # "新会话开始"，不是"建流成功"）。本会话首次故障立即提示。
        self._asr_last_error_sent_at = 0.0

        # Eagerly open ASR stream — eliminates ~100-300ms cold-start latency
        # on the first audio frame. The stream stays alive for the entire session;
        # only reset between turns to clear the ASR-side buffer.
        if self._asr and self._asr_stream is None:
            try:
                self._asr_stream = await self._asr.open(
                    sample_rate=self._ctx.audio_sample_rate,
                )
                self._asr_poll_task = asyncio.create_task(
                    self._poll_asr_results(),
                    name=f"asr-poll-{self._ctx.session_id}",
                )
                logger.info(
                    "ASR stream opened eagerly",
                    extra={
                        "session_id": self._ctx.session_id,
                        "provider": getattr(self._asr, "name", "?"),
                    },
                )
            except Exception:
                self._asr_open_fail_time = time.monotonic()
                logger.warning(
                    "ASR eager open failed, will retry on first audio",
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )

        # Eagerly open TTS stream — eliminates ~100-300ms cold-start latency
        # on the first LLM response. The stream stays alive for the entire session;
        # only reset between turns.
        if self._tts and self._tts_stream is None:
            try:
                self._tts_stream = await self._tts.open(
                    voice="", sample_rate=self._ctx.audio_sample_rate,
                )
                logger.info(
                    "TTS stream opened eagerly",
                    extra={"session_id": self._ctx.session_id},
                )
            except Exception:
                logger.warning(
                    "TTS eager open failed, will retry on first thinking turn",
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )

    async def _handle_session_end(self, msg: Any) -> None:
        # 先关事件源（[P6.5]）：teardown 长 await 期间 monitor 若存活，事件会起新回合
        await self._close_av_monitor()
        await self._cancel_pipeline()
        await self._cancel_asr_poll()
        # 三路关闭并发（见 _close_quietly）：串行 await 会累计 close 超时
        asr_stream, tts_stream, lecture_engine, omni_engine = (
            self._asr_stream, self._tts_stream, self._lecture_engine, self._omni_engine,
        )
        self._asr_stream = None
        self._tts_stream = None
        self._lecture_engine = None
        self._omni_engine = None
        await asyncio.gather(
            _close_quietly(asr_stream),
            _close_quietly(tts_stream),
            _close_quietly(lecture_engine),
            _close_quietly(omni_engine),
        )
        self._lecture_active = False
        self._omni_active = False
        if self._ctx:
            await self._publish_session_ended(str(msg.payload.reason))
            await self._session_mgr.end(self._ctx, str(msg.payload.reason))
        self._handler.stop()

    async def on_connection_closed(self) -> None:
        """Connection dropped without session.end: notify the agent relay so it
        reaps the chatlog watcher / Hermes session / orphaned jobs. The Redis
        snapshot is intentionally kept so a reconnect within the TTL resumes
        the session (session.started is re-published, re-creating the watcher).
        """
        if self._ctx is None:
            return
        await self._publish_session_ended("connection_closed")
        logger.info(
            "session ended (connection closed)",
            extra={"session_id": self._ctx.session_id},
        )

    async def _publish_session_ended(self, reason: str) -> None:
        if self._session_ended_published:
            return
        self._session_ended_published = True
        # 归属校验：重连 resume 复用同一 session_id 时，被接管的旧连接（延迟拆除）
        # 不得发布 ended——否则 agent relay 会把活跃会话的 watcher/session 摘掉
        # （session/ownership.py）
        if not ownership.owns(self._ctx.session_id, self):
            logger.debug(
                "agent session.ended skipped (session taken over)",
                extra={"reason": reason, "session_id": self._ctx.session_id},
            )
            return
        ownership.release(self._ctx.session_id, self)
        if self._agent_publisher is None:
            return
        asyncio.create_task(
            self._agent_publisher.publish_session_ended(
                session_id=self._ctx.session_id,
                device_id=self._handler.principal.device_id,
            )
        )
        logger.debug("agent session.ended published", extra={"reason": reason})

    async def _handle_session_change(self, msg: Any) -> None:
        """Handle session.change — switch between chat, translate, lecture, and omni modes."""
        new_mode = msg.payload.mode

        if not self._ctx:
            # config 失败必须与真实模式切换拒绝（1009）隔离，避免客户端误回滚 pending mode。
            code = (
                ErrorCode.SESSION_CONFIG_FAILED
                if str(new_mode) == "config"
                else ErrorCode.SESSION_CHANGE_FAILED
            )
            frame = self._codec.make_error(
                code=code, message="session not started", fatal=False
            )
            await self._handler.enqueue_outbound(frame)
            return

        if new_mode not in ("chat", "translate", "transcribe", "omni", "config"):
            frame = self._codec.make_error(
                code=ErrorCode.SESSION_CHANGE_FAILED, message=f"invalid mode: {new_mode}", fatal=False
            )
            await self._handler.enqueue_outbound(frame)
            return

        usecase_raw = getattr(msg.payload, "usecase", None)
        usecase = usecase_raw if isinstance(usecase_raw, str) else None
        if usecase is not None and usecase not in ("dialogue", "lecture"):
            usecase = "dialogue"

        # ── config 控制伪模式：只改会话级 AVDetector 开关，不切换 pipeline ──
        if new_mode == "config":
            value = getattr(msg.payload, "avdetector", None)
            if value is None or str(value) not in ("enable", "disable"):
                frame = self._codec.make_error(
                    code=ErrorCode.SESSION_CONFIG_FAILED,
                    message=f"invalid avdetector: {value}",
                    fatal=False,
                )
                await self._handler.enqueue_outbound(frame)
                return

            previous = self._ctx.avdetector_enabled
            try:
                self._ctx.avdetector_enabled = str(value) == "enable"
                if self._ctx.avdetector_enabled:
                    self._ensure_av_monitor()
                else:
                    await self._close_av_monitor()
            except Exception:
                # 清理可能已半创建的 monitor，再回滚开关，避免状态与 monitor 不一致
                await self._close_av_monitor()
                self._ctx.avdetector_enabled = previous
                kv_logger.warning(
                    "apply avdetector config failed",
                    session_id=self._ctx.session_id,
                    exc_info=True,
                )
                frame = self._codec.make_error(
                    code=ErrorCode.SESSION_CONFIG_FAILED,
                    message="apply avdetector config failed",
                    fatal=False,
                )
                await self._handler.enqueue_outbound(frame)
                return

            frame = self._codec.make_session_changed(
                mode="config",
                avdetector=str(value),
            )
            await self._handler.enqueue_outbound(frame)
            # stdlib extra={} 会被 ProcessorFormatter 丢弃；结构化字段必须用 kv_logger
            # 的 kwargs 才会落进 JSON 日志。
            kv_logger.info(
                "avdetector session config applied",
                session_id=self._ctx.session_id,
                enabled=self._ctx.avdetector_enabled,
                pipeline_mode=self._pipeline_mode,
                monitor=self._av_monitor is not None,
            )
            return

        # avdetector：仅 chat 模式挂载（§4 D4 模式门控）。[P6.5] 先关事件源再进
        # cut-out/enter 长 await——`_enter_omni_mode` 开流秒级期间 `_pipeline_mode`
        # 仍 "full" 且状态被压回 LISTENING，存活 monitor 的事件会在切换窗口内
        # 起一条 chat 主动回合（与 omni 引擎并存 = 竞态）
        had_monitor = self._av_monitor is not None
        if new_mode != "chat":
            await self._close_av_monitor()

        target_lecture = (
            new_mode == "translate"
            and usecase == "lecture"
            and self._translate_cfg is not None
            and self._translate_cfg.enabled
        )

        # ── cut out of lecture ──
        if self._lecture_active and not target_lecture:
            await self._close_lecture_engine()
            # Reset SM — lecture may have left us in SPEAKING/THINKING
            if self._ctx and self._ctx.state != SessionState.LISTENING:
                self._ctx.sm.transition_or_reject(SessionState.LISTENING)

        # ── cut out of omni（与 lecture 互斥；omni↔lecture 直切先关旧引擎）──
        if self._omni_active and new_mode != "omni":
            await self._close_omni_engine()
            if self._ctx and self._ctx.state != SessionState.LISTENING:
                self._ctx.sm.transition_or_reject(SessionState.LISTENING)

        # ── enter omni ──（幂等：已 active 时仅回声；open 失败回 1009 且不发回声，见设计 D4）
        if new_mode == "omni":
            if not self._omni_active:
                entered = await self._enter_omni_mode()
                if not entered:
                    # 1009 回滚：App 留在 chat——恢复先关的事件源，否则 chat 下检测静默死亡
                    if had_monitor:
                        self._ensure_av_monitor()
                    return
            # 与 chat 分支同款复位语言字段：否则回声会带出 translate 残留的语言对
            self._my_language = None
            self._local_language = None
            self._loc_auto = False
            self._effective_local_language = None

        if new_mode == "chat":
            self._pipeline_mode = "full"
            self._my_language = None
            self._local_language = None
            self._loc_auto = False
            self._effective_local_language = None
            self._ensure_av_monitor()
        elif new_mode == "transcribe":
            # 立即静音：取消在途 LLM/TTS（D3）；ASR 流与 poll 保持——
            # 不换引擎，转录要继续（与 _enter_lecture_mode 不同，不调用 _cancel_asr_poll）
            await self._cancel_pipeline()
            if self._ctx and self._ctx.state != SessionState.LISTENING:
                self._ctx.sm.transition_or_reject(SessionState.LISTENING)
            self._pipeline_mode = "transcribe"
            self._my_language = None
            self._local_language = None
            self._loc_auto = False
            self._effective_local_language = None
        elif new_mode == "translate":
            my_lang = getattr(msg.payload, "my_language", None) or None
            loc_lang = getattr(msg.payload, "local_language", None) or None
            if not my_lang:
                my_lang = "zh"
                logger.warning(
                    "session.change translate without my_language, defaulting to zh",
                    extra={"session_id": self._ctx.session_id},
                )
            # local_language="auto"（对话译自动识别）为保留字：loc-auto 模式，
            # effective 初值 None（≈en）；lecture 引擎源语言需固定码——客户端已把
            # lecture 的 auto 归一为 en，此处对直连兜底同样归一
            is_auto_loc = loc_lang == "auto"
            if is_auto_loc and target_lecture:
                is_auto_loc = False
                loc_lang = "en"
            elif is_auto_loc:
                pass
            elif not loc_lang:
                loc_lang = "en"
                logger.warning(
                    "session.change translate without local_language, defaulting to en",
                    extra={"session_id": self._ctx.session_id},
                )
            self._my_language = my_lang
            self._local_language = loc_lang
            self._loc_auto = is_auto_loc
            if not is_auto_loc:
                self._effective_local_language = None

            # ── enter lecture mode ──
            if target_lecture:
                await self._enter_lecture_mode(my_lang, loc_lang)
            else:
                self._pipeline_mode = "translate"

        effective_usecase: str | None
        if self._lecture_active:
            effective_usecase = "lecture"
        elif new_mode == "translate":
            effective_usecase = usecase
        else:
            effective_usecase = None

        frame = self._codec.make_session_changed(
            mode=new_mode,
            my_language=self._my_language,
            local_language=self._local_language,
            usecase=effective_usecase,
        )
        await self._handler.enqueue_outbound(frame)

        await self._send_status(self._ctx.state.value, "mode_changed")

    # ── omni mode helpers ─────────────────────────────────────────────

    async def _send_mode_unavailable(self) -> None:
        frame = self._codec.make_error(
            code=ErrorCode.SESSION_CHANGE_FAILED, message="omni mode unavailable", fatal=False
        )
        await self._handler.enqueue_outbound(frame)

    async def _enter_omni_mode(self) -> bool:
        """进入全模态模式：关 ASR/TTS，起 OmniModeEngine（镜像 `_enter_lecture_mode`）。

        返回 False 表示未进入（服务端未启用 omni 或开流失败）——调用方不得发
        `session.changed`，客户端按 error 1009 回滚本次切换（设计 D4）。
        """
        if self._omni_service is None:
            await self._send_mode_unavailable()
            logger.info(
                "session.change(omni) rejected: omni service not available",
                extra={"session_id": self._ctx.session_id if self._ctx else "none"},
            )
            return False

        # 重入保护：先关旧引擎（正常路径由切出分支关闭；此处兜底防泄漏）
        if self._omni_engine is not None:
            await self._close_omni_engine()

        # Cancel in-flight pipeline and ASR poll（poll 在 omni 下无用；退出时懒重开重建）
        await self._cancel_pipeline()
        await self._cancel_asr_poll()

        if self._ctx and self._ctx.state != SessionState.LISTENING:
            self._ctx.sm.transition_or_reject(SessionState.LISTENING)

        # Reset turn detector for clean state on re-exit to ASR mode
        if self._turn_detector:
            self._turn_detector.reset()

        # Release ASR/TTS provider connections (lazy reopen on exit)
        asr_stream, tts_stream = self._asr_stream, self._tts_stream
        self._asr_stream = None
        self._tts_stream = None
        await asyncio.gather(
            _close_quietly(asr_stream),
            _close_quietly(tts_stream),
        )

        # Reset ASR open cooldown，否则退出后首帧音频会被冷却门控静默丢弃最长 5s
        self._asr_open_fail_time = 0.0

        from session.omni_mode import OmniModeEngine

        sample_rate = self._pipeline_sample_rate or self._ctx.audio_sample_rate
        engine = OmniModeEngine(
            handler=self._handler,
            codec=self._codec,
            ctx=self._ctx,
            history=self._history,
            interrupt=self._interrupt,
            omni_service=self._omni_service,
            system_prompt=self._omni_system_prompt,
            voice=self._omni_voice,
            generation_opts=self._omni_gen_opts,
            sample_rate=sample_rate,
            opus_encoder=self._opus_enc,
            chat_store=self._chat_store,
            recording_sink=self._recording_sink,
            agent_publisher=self._agent_publisher,
        )
        try:
            await engine.open()
        except Exception:
            # dispatch 主循环没有 per-message try/except：不在此吞错则 provider 抖动
            # 会杀死连接主循环。失败即回 1009 并停留原模式（ASR/TTS 已置 None，懒重开兜底）。
            logger.warning(
                "omni mode open failed, staying in current mode",
                extra={"session_id": self._ctx.session_id if self._ctx else "none"},
                exc_info=True,
            )
            await self._send_mode_unavailable()
            return False

        self._omni_engine = engine
        self._omni_active = True
        self._pipeline_mode = "omni"
        logger.info(
            "entered omni mode",
            extra={"session_id": self._ctx.session_id, "sample_rate": sample_rate},
        )
        return True

    async def _close_omni_engine(self) -> None:
        """关闭全模态引擎并复位标志（ASR/TTS 懒重开——与 lecture 退出同款）。"""
        engine, self._omni_engine = self._omni_engine, None
        self._omni_active = False
        if engine is not None:
            try:
                await engine.close()
            except Exception:
                logger.warning(
                    "omni engine close failed",
                    extra={"session_id": self._ctx.session_id if self._ctx else "none"},
                    exc_info=True,
                )

    # ── avdetector helpers（docs/fd_pipeline_avdetector.md §4 D2/D3）────────────

    def _ensure_av_monitor(self) -> None:
        """chat 模式下挂载监控器（新会话 / 切回 chat 时；服务未启用则 no-op）。"""
        if (self._av_monitor is not None or self._avdetector_service is None
                or self._ctx is None
                or self._pipeline_mode != "full"
                or not self._ctx.avdetector_enabled):
            return
        from session.av_detector import AvDetectorMonitor

        lang = resolve_proactive_language(self._ctx.language)
        clause = build_desc_language_clause(lang)
        system_prompt = (
            f"{self._avdetector_system_prompt}\n\n{clause}"
            if clause else self._avdetector_system_prompt
        )
        self._av_monitor_generation += 1
        generation = self._av_monitor_generation

        async def _dispatch(kind: str, desc: str, label: str = "",
                            frame: tuple | None = None) -> None:
            await self._on_av_event(kind, desc, label, frame, generation=generation)

        monitor = AvDetectorMonitor(
            service=self._avdetector_service,
            system_prompt=system_prompt,
            cfg=self._avdetector_cfg,
            sample_rate=self._pipeline_sample_rate or self._ctx.audio_sample_rate,
            on_event=_dispatch,
            session_id=self._ctx.session_id,
        )
        # 先 start 成功再挂到 self：start 抛错时不留半初始化 monitor
        monitor.start()
        self._av_monitor = monitor

    async def _close_av_monitor(self) -> None:
        # 先置空并递增代际，再 await close：await 窗口内旧 monitor 的迟到事件立即失效
        monitor, self._av_monitor = self._av_monitor, None
        self._av_monitor_generation += 1
        if monitor is not None:
            try:
                await monitor.close()
            except Exception:
                logger.warning(
                    "avdetector monitor close failed",
                    extra={"session_id": self._ctx.session_id if self._ctx else "none"},
                    exc_info=True,
                )

    def _av_proactive_cooldown_ok(self) -> bool:
        cooldown = float(getattr(self._avdetector_cfg, "proactive_cooldown_s", 30.0))
        return (time.monotonic() - self._last_av_proactive) >= cooldown

    def _arm_av_visual_suppression(self) -> None:
        """[D8] cam 首帧回合收尾后开视觉抑制窗，并强制补一次带图检查建立基线。

        5s 窗短于 10s 视频节流，不能依赖窗口内帧触发检查；强制检查保证当前画面进入
        检测器上下文，避免窗口结束后同一画面再次被判为"新画面"。
        """
        suppress_s = float(getattr(self._avdetector_cfg, "visual_turn_suppress_s", 5.0))
        if suppress_s <= 0:
            return
        self._av_visual_suppress_until = time.monotonic() + suppress_s
        sid = self._ctx.session_id if self._ctx else "none"
        monitor = self._av_monitor
        if monitor is None:
            logger.debug(
                "av visual suppression armed, priming check skipped (monitor missing)",
                extra={"session_id": sid},
            )
            return
        try:
            requested = monitor.request_video_check(force=True)
        except Exception:
            logger.warning(
                "av visual priming check failed", extra={"session_id": sid}, exc_info=True
            )
            return
        logger.info(
            "av visual suppression armed (%.1fs) priming_check=%s",
            suppress_s, requested, extra={"session_id": sid},
        )

    @staticmethod
    def _build_av_event_note(event: str, language: str) -> str:
        """事件注记（§4 D3 模板；[P5] 收紧为强制回应口径——防模型续写历史尾条）。

        desc 只保留在 user 行：环境内容来自不可信音视频，不重复注入 system，
        避免把环境文本提升到 system 权限（提示注入面）；来源由 user 行自证。
        """
        if is_danger_event(event):
            ask = ("请立即用一句简短、紧迫的话回应上一条事件，提醒用户注意安全"
                   "（如“小心，别摔着了”），优先于一切")
        else:
            ask = "请立即用一句自然、简短的话回应上一条事件（主动关心或自然点评）"
        note = (
            "[环境事件·系统注入]上一条用户消息来自环境监测（非用户本人发言）。"
            f"{ask}；不要复述、不要延续之前的对话内容；不要提及“检测/系统/传感器”等字样。"
        )
        return append_reply_language_directive(note, language)

    async def _on_av_event(self, kind: str, desc: str, label: str = "",
                           frame: tuple | None = None,
                           generation: int | None = None) -> None:
        """avdetector 事件回调（monitor 管线任务调用）：最终门控 + 主动回合（D2/D3）。

        [P6] 门控顺序（§4 D2）：`mode==chat` 为唯一前置硬门 → **先按"LLM task 在飞与否"分流**：
        - 在飞（state 必为 THINKING/SPEAKING，不受 SPEAKING/THINKING 与竞态门限制）：
          危险且非危险回合 → **抢占**（`_do_interrupt("av_danger")`）；否则（危险在飞 /
          普通画面 / 声音）丢弃 + log；
        - 无在飞：LISTENING 门 → 竞态复检（丢弃 + log）→ 冷却（危险 [R15] 与画面 [P6]
          豁免、声音保留）→ 起轮。

        事件以 **user 行**落三件一体（history/chat_store/录制；kind="asr" +
        ref_id 前缀 `avd-`，沿用 v3 文本回合的 provenance 约定）——LLM 请求因此
        以 **user 消息结尾**，不再以 assistant 结尾被模型"续写/复读"（2026-10-03
        真机"咳嗽复读历史"事故的根治，[P5]）。先门控后落库（被丢弃事件零落库）。
        """
        if self._ctx is None:
            return
        if (self._pipeline_mode != "full"
                or not self._ctx.avdetector_enabled
                or (generation is not None and generation != self._av_monitor_generation)):
            kv_logger.info(
                "avdetector event dropped (gate)",
                session_id=self._ctx.session_id,
                mode=self._pipeline_mode,
                enabled=self._ctx.avdetector_enabled,
                generation=generation,
                monitor_generation=self._av_monitor_generation,
                kind=kind,
                label=label,
                desc=desc[:80],
            )
            return
        is_danger = is_danger_event(label)
        is_image = frame is not None
        busy = self._pipeline_task is not None and not self._pipeline_task.done()
        if busy:
            if is_danger and not self._pipeline_danger:
                # [P6] 抢占：中断在飞回合（含用户标准回合——安全优先）
                logger.info(
                    "avdetector danger event preempts in-flight turn label=%s desc=%r",
                    label, desc[:80], extra={"session_id": self._ctx.session_id},
                )
                await self._do_interrupt("av_danger")
                # [P6.6] 抢占窗口补刀：`_do_interrupt` 内含多个 await（cancel、ASR/TTS
                # reset、status 帧），dispatch 循环可能在此期间受理用户 turn-end / v3
                # 文本回合并建了新 pipeline——不取消它，下面的引用覆写会让其成为无人
                # 回收的孤儿（与危险回合并发双 LLM/TTS 交错）。幂等：无在飞时零成本。
                await self._cancel_pipeline()
            else:
                logger.info(
                    "avdetector event dropped (pipeline busy danger_inflight=%s) "
                    "kind=%s label=%s desc=%r",
                    self._pipeline_danger, kind, label, desc[:80],
                    extra={"session_id": self._ctx.session_id},
                )
                return
        else:
            if self._ctx.state != SessionState.LISTENING:
                logger.info(
                    "avdetector event dropped (state=%s) kind=%s label=%s desc=%r",
                    self._ctx.state.value, kind, label, desc[:80],
                    extra={"session_id": self._ctx.session_id},
                )
                return
            new_interim_speech = (
                self._asr_interim_pending
                and not self._av_trigger_interim_pending
                and is_speech_like_text(self._last_asr_interim_text)
            )
            if self._asr_final_sent or new_interim_speech:
                # 竞态复检（§4 D2 三重门③；[P6.3] 去帧化）："在说话"只认**文字证据**——
                # ① 迟到 meaningful final（触发门已保证其触发时为假）；
                # ② 触发后**新出现且文本像话**的 interim（≥5 种字符，[P6.13] 与转写否决同一判定）。
                # 语音帧快照不再单独拦：连续咳嗽的后续 VAD 帧与拟声 interim（'咳'）不算
                # "在说话"（否则咳嗽自杀式拦截）；用户真开口的兜底 = 既有 barge-in 链
                # （SPEAKING 中 user_voice 打断）+ ASR 回合送达即 cancel。命中 → 主动丢弃
                logger.info(
                    "avdetector event dropped by race recheck (final_sent=%s interim_speech=%s) "
                    "kind=%s label=%s desc=%r",
                    self._asr_final_sent, new_interim_speech, kind, label, desc[:80],
                    extra={"session_id": self._ctx.session_id},
                )
                return
            if (kind == "画面" and not is_danger
                    and time.monotonic() < self._av_visual_suppress_until):
                # [D8] cam 首帧回合收尾后的抑制窗：普通画面事件丢弃；危险/声音不受影响
                logger.info(
                    "avdetector event dropped by visual-turn suppression label=%s desc=%r",
                    label, desc[:80], extra={"session_id": self._ctx.session_id},
                )
                return
            if not (is_danger or is_image) and not self._av_proactive_cooldown_ok():
                # 冷却豁免：危险（[R15]）与画面（[P6]）；声音保留（防打扰）
                logger.info(
                    "avdetector event dropped by proactive cooldown label=%s desc=%r",
                    label, desc[:80], extra={"session_id": self._ctx.session_id},
                )
                return

        # await 窗口（danger 抢占的 _do_interrupt/_cancel_pipeline）之后、任何持久化写之前
        # 再复检：mode / 会话开关 / monitor 代际任一变化都丢弃，且不留下 history /
        # chat_store / recording 残影（丢弃 = 零副作用）。
        if (self._pipeline_mode != "full"
                or not self._ctx.avdetector_enabled
                or (generation is not None and generation != self._av_monitor_generation)):
            kv_logger.info(
                "avdetector event dropped (post-await)",
                session_id=self._ctx.session_id,
                mode=self._pipeline_mode,
                enabled=self._ctx.avdetector_enabled,
                generation=generation,
                monitor_generation=self._av_monitor_generation,
                kind=kind,
                label=label,
                desc=desc[:80],
            )
            return

        self._last_av_proactive = time.monotonic()
        image_frames: list[ChatImageFrame] | None = None
        if frame is not None:
            jpeg, width, height, orientation, camera_facing = frame
            image_frames = [ChatImageFrame(
                data=jpeg, width=width, height=height,
                orientation=orientation, camera_facing=camera_facing,
            )]
        # ── 事件落三件一体（user 行；先落盘后起轮，与 AF/v3 路径同顺序）────────
        lang = resolve_proactive_language(self._ctx.language)
        content = f"{env_prefix(lang)}{desc}"
        urls = _to_data_urls(image_frames) if image_frames else []
        ref_id = f"avd-{generate_message_id()}"
        ts_ms = now_ms()
        if self._history is not None:
            self._history.add_message("user", content, images=urls)
        if self._chat_store is not None:
            self._chat_store.on_message(
                device_uuid=self._ctx.device_id,
                session_id=self._ctx.session_id,
                kind="asr", role="user",
                content=content,
                ts_ms=ts_ms,
                images=urls,
                ref_id=ref_id,
            )
        # 4xx 回滚句柄（D5c）：token=ref_id，与 v3 文本回合同语义
        self._turn_user_record = {
            "content": content,
            "ts_ms": ts_ms,
            "token": ref_id,
            "device_uuid": self._ctx.device_id,
            "session_id": self._ctx.session_id,
        }
        if self._recording_sink is not None:
            try:
                self._recording_sink.on_chat_text(
                    role="user", text=content, ts_ms=now_ms(),
                    ref_id=ref_id, chat_session_id=self._ctx.session_id,
                    frames=image_frames,
                )
            except Exception:
                logger.warning(
                    "avdetector recording tap failed",
                    extra={"session_id": self._ctx.session_id}, exc_info=True,
                )
        note = self._build_av_event_note(label, lang)
        kv_logger.info(
            "avdetector proactive turn",
            session_id=self._ctx.session_id,
            kind=kind,
            label=label,
            target=lang,
            desc=desc[:80],
            ref_id=ref_id,
        )
        # [P6] 在飞类型；[P6.8] 换装原语：覆写前同步取消插队者（防孤儿）
        self._spawn_pipeline(
            self._start_thinking("", image_frames=image_frames, turn_token=ref_id,
                                 event_note=note),
            danger=is_danger,
        )

    # ── lecture mode helpers ──────────────────────────────────────────

    async def _enter_lecture_mode(self, my_language: str, local_language: str) -> None:
        """Close ASR/TTS streams and open the LiveTranslate engine."""
        # 重入 lecture（客户端切目标语言会重发 session.change(translate, lecture)，
        # 此时 _handle_session_change 的"cut out"关闭分支被跳过）必须先关旧引擎：
        # 否则下一行的赋值直接覆盖旧引擎 → 旧 WS/事件泵永久泄漏（泵任务持有 engine
        # 引用，无引用可回收），且旧 WS 收不到 session.finish。
        # 重入 lecture（客户端切目标语言会重发 session.change(translate, lecture)，
        # 此时 _handle_session_change 的"cut out"关闭分支被跳过）必须先关旧引擎：
        # 否则下一行的赋值直接覆盖旧引擎 → 旧 WS/事件泵永久泄漏（泵任务持有 engine
        # 引用，无引用可回收），且旧 WS 收不到 session.finish。
        if self._lecture_engine is not None:
            await self._close_lecture_engine()

        # Cancel in-flight pipeline and ASR poll
        await self._cancel_pipeline()
        await self._cancel_asr_poll()

        # Reset SM to LISTENING in case we were in SPEAKING/THINKING (design §6.6)
        if self._ctx and self._ctx.state != SessionState.LISTENING:
            self._ctx.sm.transition_or_reject(SessionState.LISTENING)

        # Reset turn detector for clean state on re-exit to ASR mode
        if self._turn_detector:
            self._turn_detector.reset()

        # Release ASR/TTS provider connections (lazy reopen on exit)
        # 并发关闭（见 _close_quietly）：串行 await 曾让此处累计阻塞 15s——
        # session.changed 延迟 + 网关背压 + 积压音频回放
        asr_stream, tts_stream = self._asr_stream, self._tts_stream
        self._asr_stream = None
        self._tts_stream = None
        await asyncio.gather(
            _close_quietly(asr_stream),
            _close_quietly(tts_stream),
        )

        # Reset ASR open cooldown so re-entry on traceback works
        self._asr_open_fail_time = 0.0

        from session.lecture_translate import LectureTranslateEngine

        self._lecture_engine = LectureTranslateEngine(
            handler=self._handler,
            codec=self._codec,
            ctx=self._ctx,
            interrupt=self._interrupt,
            cfg=self._translate_cfg,
            recording_sink=self._recording_sink,
        )

        try:
            await self._lecture_engine.open(
                my_language=my_language,
                local_language=local_language,
            )
            self._lecture_active = True
            self._pipeline_mode = "translate"
            logger.info(
                "entered lecture translate mode",
                extra={
                    "session_id": self._ctx.session_id,
                    "my_language": my_language,
                    "local_language": local_language,
                },
            )
        except (ProviderError, Exception):
            # D6: fallback to LLM translate
            logger.warning(
                "lecture translate open failed, falling back to LLM translate",
                extra={"session_id": self._ctx.session_id},
                exc_info=True,
            )
            if self._lecture_engine is not None:
                try:
                    await self._lecture_engine.close()
                except Exception:
                    pass
            self._lecture_engine = None
            self._lecture_active = False
            self._pipeline_mode = "translate"
            frame = self._codec.make_error(
                code=ErrorCode.TRANSLATE_UNAVAILABLE,
                message="lecture translate unavailable, fell back to LLM translate",
                fatal=False,
            )
            await self._handler.enqueue_outbound(frame)

    async def _close_lecture_engine(self) -> None:
        """Close the lecture engine and release its resources."""
        if self._lecture_engine is not None:
            try:
                await self._lecture_engine.close()
            except Exception:
                pass
            self._lecture_engine = None
        self._lecture_active = False
        logger.info(
            "exited lecture translate mode",
            extra={"session_id": self._ctx.session_id if self._ctx else "none"},
        )

    # ------------------------------------------------------------------
    # audio (listening phase) — design §4.3 LISTENING
    def _resolve_audio_pcm(self, msg: Any) -> bytes:
        """入站 audio 帧 → PCM。

        muted 帧（客户端 mic 静音补发，docs/android_audio_capture.md §5.2）：不做 Opus 解码，
        替换为全零 PCM（采样率取会话值 ctx.audio_sample_rate）；其余帧原样解码
        （b""=畸形，调用方按既有守卫丢弃）。

        lecture 分支放大为 **5 帧零 PCM/条**（100ms/条，5× 压缩投递）：云端端点阈值实测
        ≈3.4s 静音样本，客户端 1s 窗口（50 条 muted）经此放大 = 云端 5s 样本 ⇒ mute 后
        约 1–2s 收尾；omni/dialogue 维持 1 帧 1:1 不变
        （见 docs/android_audio_capture.md §5.2）。muted 路径逐帧各记一条
        debug（收到 / 送出零帧），供真机比对（日志里 grep "muted"）。
        """
        if msg.payload.vad_state.value == "muted":
            self._muted_frame_count += 1
            kv_logger.debug(
                "recv muted audio frame",
                session_id=self._ctx.session_id,
                seq=getattr(msg, "seq", None),
                muted_n=self._muted_frame_count,
                data_bytes=len(msg.payload.data),
            )
            frame = b"\x00" * (self._ctx.audio_sample_rate * 20 // 1000 * 2)
            frames = 5 if (self._lecture_active and self._lecture_engine is not None) else 1
            zero = frame * frames
            kv_logger.debug(
                "send zero pcm frame (muted substitution)",
                session_id=self._ctx.session_id,
                muted_n=self._muted_frame_count,
                frames=frames,
                pcm_bytes=len(zero),
                sample_rate=self._ctx.audio_sample_rate,
            )
            return zero
        return _decode_opus(msg.payload.data, self._opus_dec)

    async def _handle_audio(self, msg: Any) -> None:
        if not self._ctx:
            return

        # ── Omni mode: feed PCM directly to the full-modality engine ──
        # VAD/回合边界由 provider（semantic_vad）承担，不经过 TurnDetector/ASR。
        if self._omni_active and self._omni_engine is not None:
            pcm = self._resolve_audio_pcm(msg)
            if not pcm:
                return  # 畸形音频帧：丢弃，不喂引擎
            await self._omni_engine.feed_pcm(pcm)
            return

        # ── Lecture mode: feed PCM directly to LiveTranslate engine ──
        if self._lecture_active and self._lecture_engine is not None:
            # Feed continuously — the LiveTranslate API manages its own turn
            # boundaries (design D7).  VAD barge-in is suppressed in the event
            # pump, so there is no need to gate on session state here.
            pcm = self._resolve_audio_pcm(msg)
            if not pcm:
                return  # 畸形音频帧（见 _decode_opus）：丢弃，不喂引擎
            # 失败不降级（docs/android_audio_capture.md §5.2）：feed_pcm 内部消化失败并只报错一次，
            # 此处不关引擎、不回落逐轮；流终结后 feed_pcm 为静默丢弃。
            await self._lecture_engine.feed_pcm(pcm)
            return

        if not self._turn_detector:
            return

        # During SPEAKING, only process frames after user speech is first
        # detected (VAD=speech). Once a barge-in turn starts, both speech
        # and silence must flow through TurnDetector + ASR — otherwise the
        # turn detector can never observe trailing silence and turn end is
        # never triggered. Barge-in is confirmed by ASR producing meaningful
        # text, not by raw VAD, to avoid false interrupts from ambient noise.
        if self._ctx.state == SessionState.SPEAKING:
            if msg.payload.vad_state.value == "speech":
                if not self._barge_in_turn_started:
                    self._barge_in_turn_started = True
                    if self._turn_detector:
                        self._turn_detector.reset()
            elif not self._barge_in_turn_started:
                return

        if self._ctx.state not in (SessionState.LISTENING, SessionState.SPEAKING):
            return

        pcm = self._resolve_audio_pcm(msg)
        if not pcm:
            return  # 畸形音频帧（见 _decode_opus）：丢弃，不进 TurnDetector/ASR

        is_speech = msg.payload.vad_state.value == "speech"
        if is_speech:
            self._last_speech_mono = time.monotonic()
        turn_ended = self._turn_detector.on_frame(is_speech)

        # Generate a fresh transcription_id when speech starts in a new turn
        if is_speech and not self._current_transcription_id:
            self._current_transcription_id = generate_transcription_id()
            self._last_detected_language = None

        # Feed ASR — stream opened eagerly at session start；单帧失败先在原帧上
        # 有界重试（_feed_asr_with_recovery），全部失败才进冷却并提示客户端。
        if self._asr:
            await self._feed_asr_with_recovery(pcm)

        # avdetector 旁路 feed（同步投递零等待；仅 chat 模式存在 monitor——§4 D2 喂入点）
        if self._av_monitor is not None:
            self._av_monitor.feed_audio(pcm)

        if turn_ended and self._asr_stream:
            # Read the final text captured by the receive loop (design §5.2:
            # per-turn reset reuses the WSS connection; close() only at
            # session end, saving ~100–300ms reconnect handshake per turn).
            #
            # When _asr_final_sent is True, _poll_asr_results() already sent
            # the final transcription, updated history, and recorded usage —
            # we skip those steps here but still use _final_text for the
            # thinking pipeline (review #fix-asr-final).
            final_text = self._asr_stream.final_text
            try:
                await self._asr_stream.reset()
            except Exception:
                logger.warning(
                    "ASR stream reset failed",
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )

            meaningful = bool(final_text) and _count_meaningful_chars(final_text) >= self._min_meaningful_chars

            # AF 快照（fd_pipeline_llm_v2 门控：meaningful + 模式适用；本分支
            # 另因 not _asr_final_sent 天然排除路径 B 已处理的同一 final）
            frames: list[ChatImageFrame] = []
            urls: list[str] = []
            if not self._asr_final_sent:
                if meaningful and self._pipeline_mode not in ("translate", "transcribe", "asr_only"):
                    t0 = time.monotonic()
                    frames = self._snapshot_turn_images()
                    urls = _to_data_urls(frames)
                    self._turn_image_frames = frames
                    logger.debug(
                        "turn images snapshot",
                        extra={
                            "session_id": self._ctx.session_id,
                            "path": "A",
                            "n_frames": len(frames),
                            "encode_ms": round((time.monotonic() - t0) * 1000, 3),
                            "transcription_id": self._current_transcription_id,
                        },
                    )

                # Always send transcription so the user sees what ASR heard,
                # even for short fillers like "嗯。" that won't trigger LLM.
                if final_text:
                    # 有文本 final 送达 = 客户端残影已被该 final 结算（先置位再 await，
                    # 与 _asr_final_sent 同款时序约定；不得放进下面的轮次 reset 块——
                    # turn end 比云端空收尾先到 ~440ms，放进 reset 会把"待结算 interim"
                    # 提前抹掉，空收尾就再也不会补发清除帧，残影 bug 原样复现）
                    self._asr_interim_pending = False
                    await self._send_transcription(
                        final_text,
                        is_final=True,
                        transcription_id=self._current_transcription_id,
                        images=frames,
                    )

                # Only add to conversation history (and chat store) for meaningful text
                if meaningful and self._pipeline_mode not in ("translate", "transcribe") \
                        and self._history is not None:
                    self._history.add_message("user", final_text, images=urls)
                    ts_ms = now_ms()
                    self._chat_store.on_message(
                        device_uuid=self._ctx.device_id,
                        session_id=self._ctx.session_id,
                        kind="asr", role="user",
                        content=final_text,
                        ts_ms=ts_ms,
                        images=urls,
                    )
                    # 回滚句柄（D5c）：五元组全部写入时值，回滚原样使用
                    self._turn_user_record = {
                        "content": final_text,
                        "ts_ms": ts_ms,
                        "token": self._current_transcription_id,
                        "device_uuid": self._ctx.device_id,
                        "session_id": self._ctx.session_id,
                    }

            # Capture transcription_id before reset for agent relay publish
            current_tid = self._current_transcription_id

            # Reset per-turn state（暂存取走 + 重新绑定空 list：任务私有，见 §4.3）
            turn_frames = self._turn_image_frames
            self._turn_image_frames = []
            self._current_transcription_id = ""
            self._barge_in_turn_started = False
            had_asr_final = self._asr_final_sent
            self._asr_final_sent = False
            # 注意：_asr_interim_pending 不在这里复位——轮次结束≠客户端残影已被结算
            # （无文本收尾时 final 还没来，复位会让后续清除帧失效）。它只在两处复位：
            # 有文本 final 实际送达处（本分支上方 / poll 任务），以及 session.start。

            # Capture whether this turn was a barge-in (SPEAKING → interrupt)
            # before any state mutation.
            was_barge_in = self._ctx.state == SessionState.SPEAKING

            # Cancel the current pipeline only when this is NOT a silent
            # false barge-in.  Silence-only turn ends during SPEAKING leave
            # the running pipeline intact — they are noise / TTS echo, not
            # real user speech.
            if not was_barge_in or meaningful or had_asr_final:
                await self._cancel_pipeline()

            # Barge-in during SPEAKING: execute full interrupt to cancel
            # in-flight LLM/TTS, reset TTS stream, and notify client.
            # Only when ASR produced meaningful text — silence-only turn
            # ends during SPEAKING are noise and should be ignored.
            if was_barge_in and (meaningful or had_asr_final):
                await self._do_interrupt("user_voice")

            if self._pipeline_mode in ("asr_only", "transcribe"):
                # Only send status when the turn carried actual speech;
                # silence-only turn ends (empty final_text, no ASR endpoint)
                # are a natural idle state and need no client notification.
                if meaningful or had_asr_final:
                    await self._send_status("listening", "asr_final")
            elif meaningful or had_asr_final:
                # Schedule thinking as a background task so the dispatch loop
                # remains responsive to interrupt/video messages.
                # image_frames 仅用于 final ai_text 回图（请求图由 history 条目天然携带）
                # [P6] 标准回合非危险；[P6.8] 换装原语防"interrupt 窗口插队"孤儿
                self._spawn_pipeline(
                    self._start_thinking(final_text, image_frames=turn_frames,
                                         turn_token=current_tid),
                    danger=False,
                )
            elif (self._av_monitor is not None and self._av_monitor.ready()
                    and self._asr_stream is not None  # [R4] ASR 健康（非冷却期）
                    and self._ctx.state == SessionState.LISTENING
                    and (time.monotonic() - self._last_speech_mono) < 2.0
                    # avdetector 音频补充通道（[R13]）："ASR 没识别 + 窗口有声音活动"
                    # → 后台检测查询；受理成功即记录竞态复检基准（§4 D2 三重门③）。
                    # 主动发言冷却只在投递侧（_on_av_event）生效——检测照跑，
                    # 冷却窗内的危险行为才有 [R15] 豁免的机会
                    and self._av_monitor.request_audio_check()):
                self._av_trigger_interim_pending = self._asr_interim_pending

            # -- Agent Relay: fire-and-forget publish to agent events bus ----
            # transcribe 内容是现场语音而非用户发言，不发布（D7）；asr_only 保持发布
            if self._agent_publisher is not None and (meaningful or had_asr_final) \
                    and self._pipeline_mode != "transcribe":
                asyncio.create_task(
                    self._agent_publisher.publish_utterance(
                        session_id=self._ctx.session_id,
                        device_id=self._handler.principal.device_id,
                        text=final_text,
                        transcription_id=current_tid,
                        language=getattr(self, "_last_detected_language", "") or "",
                    )
                )

    # ------------------------------------------------------------------
    # thinking → speaking — design §4.3 THINKING → SPEAKING
    # (runs as a background asyncio.Task, cancellable via _handle_interrupt)
    # ------------------------------------------------------------------

    async def _start_thinking(self, user_text: str,
                              image_frames: list[ChatImageFrame] | None = None,
                              turn_token: str = "",
                              event_note: str = "",
                              visual_turn: bool = False) -> None:
        """turn_token：本轮唯一令牌（ASR 轮=transcription_id，v3 视频轮=ref_id），
        D5c 回滚守卫与路径 B 迟到写拦截用；默认空串 = 不参与回滚（守卫安全跳过）。
        visual_turn：cam 首帧直起轮为 True——未中断收尾回到 LISTENING 时开视觉抑制窗（D8）。"""
        if not self._ctx or not self._llm or not self._interrupt:
            return

        self._ctx.sm.transition_or_reject(SessionState.THINKING)
        await self._send_status("thinking", "asr_final")

        is_translate = self._pipeline_mode == "translate"
        # loc-auto（对话译自动识别）：LLM 输出严格 JSON，token 不上屏，整句到齐后
        # 解析 → 更新 effective 对方语言 → 以纯译文重放（上屏/TTS/tap 均消费纯文本）
        loc_auto = is_translate and self._loc_auto

        messages: list[ChatMessage] = []
        if is_translate:
            if loc_auto:
                # auto 模式：JSON 契约指令显式覆盖既有 "output only text" 表述；
                # effective 随上一轮翻译结果即时生效（_build_auto_translate_instruction 现读）
                prompt = self._translate_prompt or (
                    "You are a real-time voice translator with automatic language detection. "
                )
                instruction = self._build_auto_translate_instruction()
            else:
                prompt = self._translate_prompt or (
                    "You are a real-time voice translator. "
                    "Preserve the speaker's tone, intent, and nuance. "
                    "Output only the translated text — no explanations, no commentary."
                )
                instruction = self._build_translate_instruction()
            translate_system = f"{prompt}\n\n{instruction}"
            if self._last_detected_language:
                translate_system += f"\n\nDetected language: {self._last_detected_language}"
            messages.append(ChatMessage(role="system", content=translate_system))
            messages.append(ChatMessage(role="user", content=user_text))
            self._last_detected_language = None
        else:
            system_content = self._system_prompt
            if not event_note:
                # chat 普通回合：按用户当前消息/显式要求选择回复语言，无法判定时
                # 回落 session.start.language（docs/server_multilingual.md §4.12）。
                # 主动回合（event_note 非空）不注入，避免环境 desc 影响语言判定。
                rule = build_output_language_rule(self._ctx.language)
                system_content = (
                    f"{system_content}\n\n{rule}" if system_content else rule
                )
            if event_note:
                # avdetector 事件注记并入 system（单条，不依赖多 system message 支持）
                system_content = (
                    f"{system_content}\n\n{event_note}" if system_content else event_note
                )
            if system_content:
                messages.append(ChatMessage(role="system", content=system_content))
            if self._history:
                # 请求组装仅携带最新 _HISTORY_IMAGE_LIMIT 张历史图（fd_pipeline_llm_v2.md
                # §9.4）：其余条目即使有 images 也不组装（文本/角色不变）；history 条目
                # 本身保持全量（三写/恢复语义不变）
                entries = self._history.get_recent()
                for m, imgs in zip(entries, _select_recent_images(entries)):
                    messages.append(
                        ChatMessage(role=m["role"], content=m["content"], images=imgs)
                    )

        epoch_snap = self._ctx.epoch
        message_id = generate_message_id()

        # final ai_text 回图载荷：来自 AF 快照帧（路径 B 经暂存传入）；无帧 → None。
        # 请求所需的图已由 history 条目天然携带（组装裁剪至最新 _HISTORY_IMAGE_LIMIT 张，
        # §9.4）。translate 的 final 回图语义现状即"不带图"（门控已排除其快照，此处保底）
        latest_image_payload = None
        if not is_translate and image_frames:
            from livachat_protocol.generated.protocol_models import ServerAiTextImage
            last = image_frames[-1]
            latest_image_payload = ServerAiTextImage(
                data=base64.b64encode(last.data).decode(),
                mime="image/jpeg",
                width=last.width,
                height=last.height,
                orientation=last.orientation,
                camera_facing=last.camera_facing,
            )

        # -- LLM 开流 + 流式（统一受保护区，fd_pipeline_llm_v2.md）----------------
        # llm_iter 预初始化：开流同步抛错时 finally 的 aclose 守卫不得因未绑定而
        # NameError 掩盖原异常（任务异常 + THINKING 悬挂）。计数器在 try 外初始化，
        # 失败时收尾段读取安全。TTS 流早开（原 1210 段）随开流块一并移入受保护区。
        llm_error: ProviderError | None = None
        llm_iter: Any = None
        tts_stream = None
        full_text = ""
        text_index = 0
        audio_index = 0
        first_audio_sent = False

        # try/finally ensures provider connections are always released (N3)
        try:
            try:
                llm_iter = self._llm.stream(messages=messages, **self._gen_opts)
                if not hasattr(llm_iter, "__anext__"):
                    llm_iter = await llm_iter

                # -- ensure TTS stream is open (session-level reuse) -----------
                if self._tts and self._pipeline_mode != "asr_llm":
                    if self._tts_stream is not None:
                        tts_stream = self._tts_stream
                    else:
                        # Lazy open on first use (eager open may have failed)
                        try:
                            self._tts_stream = await self._tts.open(
                                voice="", sample_rate=self._ctx.audio_sample_rate
                            )
                            tts_stream = self._tts_stream
                        except Exception:
                            logger.warning(
                                "TTS stream open failed, falling back to text-only",
                                extra={"session_id": self._ctx.session_id},
                                exc_info=True,
                            )

                async for token in llm_iter:
                    if not self._interrupt.should_yield(epoch_snap):
                        logger.info(
                            "LLM streaming abandoned due to interrupt",
                            extra={
                                "session_id": self._ctx.session_id,
                                "tokens_received": text_index,
                                "text_so_far_len": len(full_text),
                            },
                        )
                        break

                    if not token:
                        continue

                    full_text += token
                    if not loc_auto:
                        # loc-auto 不上屏裸 JSON：整句到齐后以纯译文重放
                        await self._send_ai_text(
                            token, message_id=message_id, index=text_index, is_final=False
                        )
                        text_index += 1
            except ProviderError as exc:
                llm_error = exc
                logger.warning(
                    "LLM stream failed (error surfaced to client)",
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )
            except Exception as exc:
                # ⑦ 收编兜底（fd_pipeline_llm_v2.md）：循环体内非 ProviderError 意外
                # 异常同走失败路径（http_status=None → 透出、不回滚），关闭"任务异常
                # + THINKING 悬挂无人复位"既有故障类。CancelledError 是 BaseException，
                # 天然不在此列，中断取消仍可穿透。
                llm_error = ProviderError(
                    f"LLM pipeline error: {exc}",
                    provider="llm",
                    code=ErrorCode.LLM_UNAVAILABLE,
                )
                logger.warning(
                    "LLM pipeline failed with unexpected error",
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )

            # 4xx 不可恢复：回滚本轮 user 记录（同步、先于一切 await——防被下一轮
            # turn-end 的 _cancel_pipeline 打断；fd_pipeline_llm_v2.md）
            if llm_error is not None:
                self._rollback_unrecoverable_turn(llm_error, turn_token)

            # If interrupted, bail out (finally will close tts_stream)
            if not self._interrupt.should_yield(epoch_snap):
                logger.info(
                    "pipeline abandoned before TTS due to interrupt",
                    extra={
                        "session_id": self._ctx.session_id,
                        "full_text_len": len(full_text),
                    },
                )
                return

            # loc-auto 收尾：解析严格 JSON → 更新 effective（source≠my 即对方发言，
            # latest-wins）→ 以纯译文重放上屏。未守约处置：先 '{'..'}' 轻量截取重试
            # 一次（容忍 prose 前缀 / ProviderError 残缺）；仍失败且文本含 '{'（JSON
            # 残片嫌疑）→ 仅上屏原样供排查，清空 full_text —— 不喂 TTS、不进 recording
            # tap（残片不该被播报/存档）；纯 prose（无 '{'）视为可读内容原样走 TTS。
            final_text_index = text_index
            suppress_text_sink = False
            if llm_error is None and loc_auto and full_text:
                parsed = _parse_auto_translate_json(full_text)
                if parsed is None:
                    clipped = _extract_json_object(full_text)
                    if clipped is not None:
                        parsed = _parse_auto_translate_json(clipped)
                    if parsed is not None:
                        logger.info(
                            "auto translate: recovered JSON via extraction",
                            extra={"session_id": self._ctx.session_id},
                        )
                if parsed is not None:
                    src = _normalize_lang(parsed["source_language"])
                    my_norm = _normalize_lang(self._my_language or "zh") or "zh"
                    if src and src != my_norm:
                        self._effective_local_language = src
                        logger.info(
                            "auto translate: effective local language updated",
                            extra={
                                "session_id": self._ctx.session_id,
                                "source": src,
                                "my": my_norm,
                            },
                        )
                    full_text = parsed["translation"]
                else:
                    suppress_text_sink = "{" in full_text  # JSON 残片嫌疑
                    logger.warning(
                        "auto translate: LLM reply not strict JSON"
                        + (" (JSON fragment, TTS/tap suppressed)" if suppress_text_sink else ""),
                        extra={
                            "session_id": self._ctx.session_id,
                            "reply_len": len(full_text),
                            "reply_preview": full_text[:200],
                        },
                    )
                if full_text:
                    await self._send_ai_text(
                        full_text, message_id=message_id, index=0, is_final=False,
                    )
                    final_text_index = 1
                if suppress_text_sink:
                    # 已上屏；阻止 TTS/tap/history 消费残片
                    full_text = ""

            # -- final ai_text ---------------------------------------------------
            # 失败路径统一门控（D4）：错误文本不是 assistant 发言，不进 history/chat_store/录制
            if llm_error is None and full_text and not is_translate:
                if self._history is not None:
                    self._history.add_message("assistant", full_text)
                self._chat_store.on_message(
                    device_uuid=self._ctx.device_id,
                    session_id=self._ctx.session_id,
                    kind="llm", role="assistant",
                    content=full_text,
                    ref_id=message_id,
                    ts_ms=now_ms(),
                )

            final_text = ""
            if llm_error is not None:
                # 需求 2：出错时 final 透出部分 error message（1k 截断）；已有非 final
                # 上屏时 "\n" 分隔；带图（与成功路径同源）帮助定位是哪帧图导致的错误
                err_text = str(llm_error)[:_ERROR_TEXT_MAX_CHARS]
                final_text = ("\n" + err_text) if text_index > 0 else err_text
            await self._send_ai_text(final_text, message_id=message_id, index=final_text_index, is_final=True,
                                     image=latest_image_payload)

            # THINKING→SPEAKING 提前迁移：客户端看到 final 文本即可语音打断，
            # 不等首个 TTS audio 帧（docs/session_states.md §3）。
            # a) 仅当本轮真的会播报（TTS 可用且文本非空）——text-only/无 TTS
            #    路径不迁移，避免无音频的伪 SPEAKING；
            # b) epoch guard：interrupt 落在 final text 之后时不得再迁移/发 status；
            # c) first_audio_sent 置位后，_send_audio_chunk 首帧分支自然跳过
            #    重复迁移与重复 status。
            will_speak = llm_error is None and tts_stream is not None and bool(full_text)
            if will_speak and self._pipeline_mode != "asr_llm" \
                    and self._interrupt.should_yield(epoch_snap):
                self._ctx.sm.transition_or_reject(SessionState.SPEAKING)
                await self._send_status("speaking", "tts_start")
                first_audio_sent = True

            # -- recording tap (non-blocking) ----------------------------------
            if llm_error is None and full_text and self._recording_sink is not None and self._ctx is not None:
                try:
                    self._recording_sink.on_chat_text(
                        role="assistant", text=full_text, ts_ms=now_ms(),
                        ref_id=message_id, chat_session_id=self._ctx.session_id,
                        # translate 模式译文落 chat/translate.jsonl（与 user 原话同轨）
                        source="translate" if is_translate else None,
                    )
                except Exception:
                    logger.warning(
                        f"recording on_chat_text(assistant) failed session={self._ctx.session_id} "
                        f"message_id={message_id} text_len={len(full_text)}",
                        exc_info=True,
                    )

            # -- feed complete text to TTS, then flush & drain audio --------------
            tts_feed_ok = False
            if llm_error is None and tts_stream and full_text:
                try:
                    # translate 模式完整译文不限制；chat 模式超长只播报截断后的核心内容
                    tts_text = full_text if is_translate else _clip_for_tts(full_text)
                    if len(tts_text) != len(full_text):
                        logger.info(
                            "TTS text clipped",
                            extra={
                                "session_id": self._ctx.session_id,
                                "full_len": len(full_text),
                                "tts_len": len(tts_text),
                            },
                        )
                    await tts_stream.feed_text(tts_text)
                    tts_feed_ok = True
                except Exception:
                    logger.warning(
                        "TTS feed_text failed",
                        extra={"session_id": self._ctx.session_id},
                        exc_info=True,
                    )

            # -- flush remaining TTS audio ----------------------------------------
            if tts_stream and tts_feed_ok:
                try:
                    await tts_stream.flush()
                except Exception:
                    logger.warning(
                        "TTS flush failed",
                        extra={"session_id": self._ctx.session_id},
                        exc_info=True,
                    )

                try:
                    _AUDIO_TIMEOUT_S = 30.0
                    audio_gen = tts_stream.audio()
                    while True:
                        try:
                            audio_chunk = await asyncio.wait_for(
                                audio_gen.__anext__(), timeout=_AUDIO_TIMEOUT_S
                            )
                        except TimeoutError:
                            logger.warning(
                                "TTS audio receive timed out",
                                extra={"session_id": self._ctx.session_id},
                            )
                            break
                        except StopAsyncIteration:
                            break  # sentinel received, done
                        if not self._interrupt.should_yield(epoch_snap):
                            logger.info(
                                "TTS audio drain abandoned due to interrupt",
                                extra={
                                    "session_id": self._ctx.session_id,
                                    "audio_frames_sent": audio_index,
                                },
                            )
                            break
                        audio_index = await self._send_audio_chunk(
                            audio_chunk, message_id, audio_index, first_audio_sent
                        )
                        if not first_audio_sent:
                            first_audio_sent = True
                except Exception:
                    logger.warning(
                        "TTS audio drain failed",
                        extra={"session_id": self._ctx.session_id},
                        exc_info=True,
                    )

            if self._interrupt.should_yield(epoch_snap):
                self._ctx.sm.transition_or_reject(SessionState.LISTENING)
                if visual_turn:
                    self._arm_av_visual_suppression()
                # Reset output buffer for the next turn (M1)
                self._audio_out_buf.reset()
                await self._send_status("listening", "tts_finish")

        finally:
            # Reset TTS stream for the next turn (keeps connection alive)
            if tts_stream:
                try:
                    await tts_stream.reset()
                except Exception:
                    logger.warning(
                        "TTS stream reset failed",
                        extra={"session_id": self._ctx.session_id},
                        exc_info=True,
                    )
            # Close LLM stream to release upstream connection (M3)
            # llm_iter 判空守卫：开流同步抛错时不得 NameError 掩盖原异常（D4）
            if llm_iter is not None and hasattr(llm_iter, 'aclose'):
                try:
                    await llm_iter.aclose()
                except Exception:
                    pass

    async def _send_audio_chunk(
        self, audio_chunk: bytes, message_id: str, audio_index: int, first_already_sent: bool
    ) -> int:
        """Send a single TTS audio chunk as Opus-encoded ai_audio.

        Returns the updated audio_index after all frames are sent (N2).
        """
        if not first_already_sent:
            # Transition SM to SPEAKING and send status on very first audio chunk
            if self._ctx:
                self._ctx.sm.transition_or_reject(SessionState.SPEAKING)
            await self._send_status("speaking", "tts_start")

        # Use stateful AudioFrameBuffer to avoid cross-chunk residual loss (N4)
        pcm_frames = self._audio_out_buf.feed(audio_chunk)
        if not pcm_frames:
            return audio_index

        for pcm_frame in pcm_frames:
            opus_frame = self._opus_enc.encode(pcm_frame) if self._opus_enc else pcm_frame
            await self._send_ai_audio(
                opus_frame,
                message_id=message_id,
                index=audio_index,
                is_final=False,
                duration_ms=20,
            )
            audio_index += 1

        return audio_index

    # ------------------------------------------------------------------
    # interrupt — design §4.4
    # ------------------------------------------------------------------

    async def _handle_interrupt(self, msg: Any) -> None:
        if not self._ctx or not self._interrupt:
            return

        # Forward interrupt to omni engine when active（epoch/SM 与 lecture 分流同纪律）
        if self._omni_active and self._omni_engine is not None:
            await self._interrupt.interrupt(str(msg.payload.reason))
            await self._omni_engine.interrupt(str(msg.payload.reason))
            return

        # Forward interrupt to lecture engine when active
        if self._lecture_active and self._lecture_engine is not None:
            # epoch 必须递增（与三段式 _do_interrupt 同款）：事件泵的
            # should_yield(_response_epoch) 守卫靠它作废在途 response——缺失则被打断
            # 的 TEXT/AUDIO 会继续下发（设计 §6.4 / D7「保留手动 interrupt 停播」）
            await self._interrupt.interrupt(str(msg.payload.reason))
            await self._lecture_engine.interrupt(str(msg.payload.reason))
            return

        logger.info(
            "client interrupt received",
            extra={
                "session_id": self._ctx.session_id,
                "reason": str(msg.payload.reason),
                "current_state": self._ctx.state.value,
            },
        )
        await self._do_interrupt(str(msg.payload.reason))

    async def _do_interrupt(self, reason: str) -> None:
        """Execute the full interrupt sequence (design §4.4).

        Called by both client-initiated interrupt and server-side VAD guard (N5).
        """
        if not self._ctx or not self._interrupt:
            return

        from_status = self._ctx.state.value  # read before interrupt() modifies it (N8)
        logger.info(
            "interrupt sequence start",
            extra={
                "session_id": self._ctx.session_id,
                "reason": reason,
                "from_state": from_status,
                "has_pipeline_task": self._pipeline_task is not None and not self._pipeline_task.done(),
                "has_asr_poll_task": self._asr_poll_task is not None and not self._asr_poll_task.done(),
            },
        )
        await self._interrupt.interrupt(reason)

        # Cancel the in-flight pipeline task and ASR poll task
        await self._cancel_pipeline()
        await self._cancel_asr_poll()

        # Reset ASR stream for new turn (design §4.4 step 6)
        if self._asr_stream:
            try:
                await self._asr_stream.reset()
            except Exception:
                logger.warning(
                    "ASR stream reset failed during interrupt",
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )

        # Reset TTS stream for new turn
        if self._tts_stream:
            try:
                await self._tts_stream.reset()
            except Exception:
                logger.warning(
                    "TTS stream reset failed during interrupt",
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )

        # Reset per-turn state (M1)
        # （_asr_interim_pending 不在复位之列：打断同样没有向客户端送达 final，
        #  残影仍需靠后续空收尾的清除帧兜底）
        self._current_transcription_id = ""
        self._barge_in_turn_started = False
        self._asr_final_sent = False
        self._last_detected_language = None
        self._audio_out_buf.reset()
        if self._turn_detector:
            self._turn_detector.reset()

        await self._send_status("listening", "interrupted")

    # ------------------------------------------------------------------
    # video — design §4.6
    # ------------------------------------------------------------------

    async def _handle_video(self, msg: Any) -> None:
        if not self._ctx:
            return
        # 任何状态都入 buffer（K=1 只保留最新一帧）：SPEAKING 期间到达的帧
        # 同样是下一轮请求的最新候选。取出与清空只发生在 AF 快照
        # （_snapshot_turn_images），保证送 LLM 的图 == 落 image/ 的图 ==
        # final ai_text 回带的图。
        processed = True
        if msg.payload.data:
            try:
                # validate=True：非法字符与坏 padding 统一走 except——字母表非法
                # 在生产路径已被协议层 Base64String 正则拦截，此处兜底非 pydantic
                # 构造路径与坏 padding；宽松解码会把垃圾字节当合法帧采用
                jpeg_bytes = base64.b64decode(msg.payload.data, validate=True)
            except Exception:
                # 畸形 base64 不入 buffer；异常绝不可穿出 _dispatch（会杀死会话主循环）
                logger.warning(
                    "video frame invalid base64, not stored",
                    extra={"session_id": self._ctx.session_id, "frame_id": msg.payload.frame_id},
                    exc_info=True,
                )
                processed = False
            else:
                self._ctx.latest_video_frames.append(
                    (msg.payload.frame_id, jpeg_bytes, now_ms(),
                     msg.payload.width, msg.payload.height,
                     msg.payload.orientation,
                     msg.payload.camera_facing)
                )
                while len(self._ctx.latest_video_frames) > self._ctx.max_video_frames:
                    evicted = self._ctx.latest_video_frames.popleft()
                    logger.debug(
                        "video frame evicted (buffer full)",
                        extra={
                            "session_id": self._ctx.session_id,
                            "frame_id": msg.payload.frame_id,
                            "evicted_frame_id": evicted[0],
                            "max_frames": self._ctx.max_video_frames,
                        },
                    )
                self._last_video_frame_time = time.monotonic()
        else:
            logger.debug(
                "video frame empty payload, not stored",
                extra={"session_id": self._ctx.session_id, "frame_id": msg.payload.frame_id},
            )

        # 【v3】文本回合（docs/android_screen_recording.md §5.2）：payload.text 非空
        # 且过门控 → 复用 ASR final 同一条链路（快照 → 三件一体落盘 → 条件中断 → 起轮）。
        # 硬约束两条：
        # ① 上方"入 buffer → 本分支 snapshot"之间不得插入任何 await——poll 任务
        #    （路径 B，_poll_asr_results）并发时会在 await 窗口把刚入 buffer 的本帧取走，
        #    导致文本轮静默无帧（与 _poll_asr_results 的同步段纪律同款）；
        # ② 本分支整体 try/except：异常绝不可穿出 _dispatch（会杀死会话主循环），
        #    且 ack 在本分支之后——分支抛错会让 ack 一并丢失。
        text = (msg.payload.text or "").strip()
        # data 必须非空：空 data 的帧不入 buffer（processed 仍为 True），此时若 buffer
        # 里恰好有旧帧（相机帧/上一轮），不得把它当成本帧起回合（§4.5「空 data + text」）
        if (processed and msg.payload.data and text
                and _count_meaningful_chars(text) >= self._min_meaningful_chars
                and self._pipeline_mode not in ("translate", "transcribe", "asr_only", "omni")):
            try:
                frames = self._snapshot_turn_images()   # 唯一取图点：取走并清空 buffer
                if frames:
                    urls = _to_data_urls(frames)
                    ref_id = f"video-{msg.payload.frame_id}"
                    # 三件一体（D12：history / chat_store / recording tap）。kind 必须沿用
                    # "asr"——messages.kind 是契约值集且会话恢复（manager.py load_day 映射）
                    # 对未知值静默丢弃；指令来源由 ref_id 承载。先落盘后中断（与 AF
                    # 路径在写 history/chat_store 之后才中断同顺序），三者均同步非阻塞。
                    ts_ms = now_ms()
                    if self._history is not None:
                        self._history.add_message("user", text, images=urls)
                    if self._chat_store is not None:
                        self._chat_store.on_message(
                            device_uuid=self._ctx.device_id,
                            session_id=self._ctx.session_id,
                            kind="asr", role="user",
                            content=text,
                            ts_ms=ts_ms,
                            images=urls,
                            ref_id=ref_id,
                        )
                    # 回滚句柄（D5c，v3 令牌 = ref_id）
                    self._turn_user_record = {
                        "content": text,
                        "ts_ms": ts_ms,
                        "token": ref_id,
                        "device_uuid": self._ctx.device_id,
                        "session_id": self._ctx.session_id,
                    }
                    if self._recording_sink is not None:
                        try:
                            self._recording_sink.on_chat_text(
                                role="user", text=text, ts_ms=now_ms(),
                                ref_id=ref_id, chat_session_id=self._ctx.session_id,
                                frames=frames, source=None,
                            )
                        except Exception:
                            logger.warning(
                                "recording tap failed for video text turn",
                                extra={"session_id": self._ctx.session_id, "ref_id": ref_id},
                                exc_info=True,
                            )
                    # 条件中断（D4）：THINKING/SPEAKING 或管线在飞 → 走既有中断序列
                    # （SPEAKING→THINKING 非法且 transition_or_reject 静默吞，故必须先中断）
                    if (self._ctx.state in (SessionState.THINKING, SessionState.SPEAKING)
                            or (self._pipeline_task is not None and not self._pipeline_task.done())):
                        await self._do_interrupt("user_text")
                    # [P6] v3 文本回合非危险；[P6.8] 换装原语防"interrupt 窗口插队"孤儿
                    self._spawn_pipeline(
                        self._start_thinking(text, image_frames=frames, turn_token=ref_id),
                        danger=False,
                    )
                else:
                    logger.info(
                        "video text turn skipped: no frames",
                        extra={"session_id": self._ctx.session_id, "frame_id": msg.payload.frame_id},
                    )
            except Exception:
                # 已消费的帧不回收（本帧作废，下一点按重取）；仅保证主循环与 ack 存活
                logger.error(
                    "video text turn failed",
                    extra={"session_id": self._ctx.session_id, "frame_id": msg.payload.frame_id},
                    exc_info=True,
                )

        # 【§8】turn 首帧直起轮（docs/fd_pipeline_video_process.md §8）：turn_id 变化 = 新采集轮
        # 首帧（用户开相机/切镜头/开录屏）→ 立即起 LLM/TTS 回合，不等 ASR。硬约束：
        # ① 上方"入 buffer → 本分支 snapshot"之间不得插入任何 await（与 v3 文本回合
        #    同款同步段纪律，防 _poll_asr_results 并发抢帧）；
        # ② 无 turn_id（缺省）或 0（未启用哨兵，SDK 参数默认值）时本分支不可达——
        #    行为与现状逐位一致（真值判断，天然覆盖 None/0）；
        # ③ 带 text 的帧一律留给上方 v3 分支（turn id 照记，避免"text 帧吃掉 turn"）；
        # ④ 分支整体 try/except：异常绝不可穿出 _dispatch（会杀死会话主循环）。
        turn_id = msg.payload.turn_id
        if processed and msg.payload.data and turn_id:
            changed = turn_id != self._last_video_turn_id
            if changed:
                # 写回设备级记忆（§8.6.3）：与是否触发解耦（模式/危险门控下同样记录）
                self._session_mgr.record_video_turn(self._ctx.device_id, turn_id)
            self._last_video_turn_id = turn_id
            busy = self._pipeline_task is not None and not self._pipeline_task.done()
            if (changed and not text
                    and self._pipeline_mode not in ("translate", "transcribe", "asr_only", "omni")
                    # 危险在飞豁免（[P6] 同款）：不抢占安全回合；turn id 已记，机会一次
                    and not (busy and self._pipeline_danger)):
                try:
                    frames = self._snapshot_turn_images()   # 同步段：取走并清空 buffer
                    if frames:
                        urls = _to_data_urls(frames)
                        ref_id = f"cam-{msg.payload.frame_id}"
                        ts_ms = now_ms()
                        lang = resolve_proactive_language(self._ctx.language)
                        content = camera_label(lang)
                        event_note = append_reply_language_directive(
                            _VIDEO_TURN_NOTE, lang
                        )
                        # 三件一体（D12；kind 沿用 "asr" 契约值集，provenance 由 ref_id 承载）
                        if self._history is not None:
                            self._history.add_message("user", content, images=urls)
                        if self._chat_store is not None:
                            self._chat_store.on_message(
                                device_uuid=self._ctx.device_id,
                                session_id=self._ctx.session_id,
                                kind="asr", role="user",
                                content=content,
                                ts_ms=ts_ms,
                                images=urls,
                                ref_id=ref_id,
                            )
                        # 回滚句柄（D5c，令牌 = ref_id）
                        self._turn_user_record = {
                            "content": content,
                            "ts_ms": ts_ms,
                            "token": ref_id,
                            "device_uuid": self._ctx.device_id,
                            "session_id": self._ctx.session_id,
                        }
                        if self._recording_sink is not None:
                            try:
                                self._recording_sink.on_chat_text(
                                    role="user", text=content, ts_ms=now_ms(),
                                    ref_id=ref_id, chat_session_id=self._ctx.session_id,
                                    frames=frames, source=None,
                                )
                            except Exception:
                                logger.warning(
                                    "recording tap failed for video turn",
                                    extra={"session_id": self._ctx.session_id, "ref_id": ref_id},
                                    exc_info=True,
                                )
                        # 条件中断（D4，v3 文本回合同款）：THINKING/SPEAKING 非法直起轮
                        if (self._ctx.state in (SessionState.THINKING, SessionState.SPEAKING)
                                or (self._pipeline_task is not None and not self._pipeline_task.done())):
                            await self._do_interrupt("video_turn")
                        self._spawn_pipeline(
                            self._start_thinking("", image_frames=frames, turn_token=ref_id,
                                                 event_note=event_note, visual_turn=True),
                            danger=False,
                        )
                    else:
                        logger.info(
                            "video turn skipped: no frames",
                            extra={"session_id": self._ctx.session_id, "frame_id": msg.payload.frame_id},
                        )
                except Exception:
                    logger.error(
                        "video turn failed",
                        extra={"session_id": self._ctx.session_id, "frame_id": msg.payload.frame_id},
                        exc_info=True,
                    )

        # 全模态模式：本帧 best-effort 投喂给引擎（视觉上下文）。
        # 位置在 v3 文本回合分支之后：该分支的"入 buffer → 快照"同步段纪律不受 await 影响
        # （omni 下 v3 分支已被门控排除、且 ASR poll 已取消，不存在抢帧者）。
        if (self._omni_active and self._omni_engine is not None
                and processed and msg.payload.data):
            try:
                await self._omni_engine.feed_image(
                    jpeg_bytes, width=msg.payload.width, height=msg.payload.height
                )
            except Exception:
                # 引擎内部已吞 ProviderError（关流 + 冷却 + 下帧懒重开）；此处兜底防穿出
                logger.warning(
                    "omni feed_image failed",
                    extra={"session_id": self._ctx.session_id, "frame_id": msg.payload.frame_id},
                    exc_info=True,
                )

        # avdetector：非 text 帧入帧缓存（本地零网络，[R16]）+ 视频主通道触发（[R13]/[R14]/[R17]）。
        # 带 payload.text 的帧不进缓存、不触发（[R14]：归既有 v3 文本回合链路）。
        if (self._av_monitor is not None and processed and msg.payload.data and not text
                and self._pipeline_mode == "full"):
            try:
                self._av_monitor.feed_frame(
                    jpeg_bytes,
                    width=msg.payload.width,
                    height=msg.payload.height,
                    orientation=msg.payload.orientation,
                    camera_facing=msg.payload.camera_facing,
                )
                if (self._av_monitor.has_frames
                        and self._ctx.state == SessionState.LISTENING
                        and (self._pipeline_task is None or self._pipeline_task.done())
                        and not self._asr_interim_pending
                        # 主动发言冷却只在投递侧（同音频通道，[P3]/[R15]）
                        and self._av_monitor.request_video_check()):
                    # 竞态复检基准（同音频通道，§4 D2 三重门③）
                    self._av_trigger_interim_pending = self._asr_interim_pending
            except Exception:
                logger.warning(
                    "avdetector video trigger failed",
                    extra={"session_id": self._ctx.session_id, "frame_id": msg.payload.frame_id},
                    exc_info=True,
                )

        from livachat_protocol.generated.protocol_models import ServerAiVideoAckPayload
        from livachat_protocol.generated.server_message import ServerAiVideoAck

        frame = self._codec.build_typed(
            ServerAiVideoAckPayload(
                frame_id=msg.payload.frame_id,
                ack_frame_id=msg.payload.frame_id,
                processed=processed,
            ),
            msg_cls=ServerAiVideoAck,
        )
        await self._handler.enqueue_outbound(frame)

    def _snapshot_turn_images(self) -> list[ChatImageFrame]:
        """AF 时刻取 buffer 全部帧并清空（唯一取图点；调用方负责 meaningful/mode 门控）。

        enable_video=false / buffer 空 → []。丢弃 7-tuple 中的 frame_id / ts_ms
        （行内无元数据、文件命名用 uuid7，无消费者）。
        """
        if self._ctx is None or not self._ctx.enable_video or not self._ctx.latest_video_frames:
            return []
        frames = [
            ChatImageFrame(data=jpeg, width=width, height=height,
                           orientation=orientation, camera_facing=camera_facing)
            for _, jpeg, _, width, height, orientation, camera_facing
            in self._ctx.latest_video_frames
            if jpeg
        ]
        self._ctx.latest_video_frames.clear()
        return frames

    # ------------------------------------------------------------------
    # send helpers
    # ------------------------------------------------------------------

    async def _send_transcription(self, text: str, *, is_final: bool, transcription_id: str = "",
                                  images: list[ChatImageFrame] | None = None,
                                  record: bool = True) -> None:
        """images：AF 快照帧，仅 is_final 分支消费（interim 不带）。

        record=False 用于"空 final 清除帧"（云端撤回已上屏 interim 时补发）：
        只上屏清空、不落 chatlog——空文本不是用户发言。
        """
        from livachat_protocol.generated.protocol_models import ServerTranscriptionPayload
        from livachat_protocol.generated.server_message import ServerTranscription

        tid = transcription_id or generate_transcription_id()
        frame = self._codec.build_typed(
            ServerTranscriptionPayload(
                text=text,
                is_final=is_final,
                transcription_id=tid,
            ),
            msg_cls=ServerTranscription,
        )
        await self._handler.enqueue_outbound(frame)

        # -- recording tap (non-blocking) --
        # text 判空是双保险：空文本（撤回清除帧）永不落 chatlog
        if is_final and record and text and self._recording_sink is not None and self._ctx is not None:
            try:
                if self._pipeline_mode == "transcribe":
                    # transcribe 模式转录文本落 chat/transcribe.jsonl，不进 history.jsonl
                    self._recording_sink.on_transcribe_text(
                        text=text, ts_ms=now_ms(),
                        ref_id=tid, chat_session_id=self._ctx.session_id,
                    )
                else:
                    # translate 模式经 source="translate" 由 recording 层路由到
                    # chat/translate.jsonl；chat 不带 source 走 history.jsonl
                    self._recording_sink.on_chat_text(
                        role="user", text=text, ts_ms=now_ms(),
                        ref_id=tid, chat_session_id=self._ctx.session_id,
                        source="translate" if self._pipeline_mode == "translate" else None,
                        frames=images or None,
                    )
            except Exception:
                kind = "transcribe" if self._pipeline_mode == "transcribe" else "chat"
                logger.warning(
                    f"recording text tap failed kind={kind} session={self._ctx.session_id} ref_id={tid}",
                    exc_info=True,
                )

    async def _send_ai_text(self, text: str, *, message_id: str, index: int, is_final: bool,
                             image: Any | None = None) -> None:
        from livachat_protocol.generated.protocol_models import ServerAiTextPayload
        from livachat_protocol.generated.server_message import ServerAiText

        kwargs: dict[str, Any] = {"text": text, "message_id": message_id, "index": index, "is_final": is_final}
        if image is not None:
            kwargs["image"] = image
            logger.debug(
                "ai_text final with image",
                extra={"message_id": message_id, "image_w": image.width, "image_h": image.height},
            )
        payload = ServerAiTextPayload(**kwargs)
        frame = self._codec.build_typed(payload, msg_cls=ServerAiText)
        await self._handler.enqueue_outbound(frame)

    async def _send_ai_audio(self, data: bytes, *, message_id: str, index: int, is_final: bool, duration_ms: int = 20) -> None:
        from livachat_protocol.generated.protocol_models import ServerAiAudioPayload
        from livachat_protocol.generated.server_message import ServerAiAudio

        frame = self._codec.build_typed(
            ServerAiAudioPayload(
                data=base64.b64encode(data).decode() if data else "",
                codec="opus",
                message_id=message_id,
                index=index,
                duration_ms=duration_ms,
                is_final=is_final,
            ),
            msg_cls=ServerAiAudio,
        )
        await self._handler.enqueue_outbound(frame)

    async def _send_status(self, session_status: str, reason: str) -> None:
        frame = self._codec.make_status(
            session_status=session_status, reason=reason
        )
        await self._handler.enqueue_outbound(frame)

    def _build_translate_instruction(self) -> str:
        """Build language-aware translation instruction from user prefs."""
        my = self._my_language or "zh"
        loc = self._local_language or "en"
        # Quote ISO codes so short codes that are also English words (e.g. "it")
        # cannot be read as pronouns by the model.
        my_code = f'"{my}"'
        loc_code = f'"{loc}"'

        return (
            f"Translate the following text:\n"
            f"- If the source language is NOT {my_code}, translate the text to {my_code}.\n"
            f"- If the source language IS {my_code} and {my_code} differs from {loc_code}, "
            f"translate the text to {loc_code}.\n"
            f"- If {my_code} and {loc_code} are the same, translate to English.\n"
            f"Output only the translation, no explanations."
        )

    def _build_auto_translate_instruction(self) -> str:
        """loc-auto 模式指令：说话人身份未知，方向由逐句源语言判定，并要求
        LLM 回传严格 JSON（source_language + translation）供服务端更新 effective。

        目标语言：源≠my（对方）→ 译 my；源==my（我）→ 译当前 effective
        （最近一次对方语言，无样本时 en 初值）。此方法在每轮 prompt 组装时读取
        _effective_local_language 的最新值——上一轮翻译已覆盖则本轮即时生效。
        """
        my = self._my_language or "zh"
        eff = self._effective_local_language or "en"
        # Quote ISO codes (see _build_translate_instruction): "it" is both an
        # Italian code and an English pronoun.
        my_code = f'"{my}"'
        eff_code = f'"{eff}"'

        return (
            f"Translate real-time conversation speech. The speaker identity is unknown; "
            f"detect the source language of the text from its content.\n"
            f"- If the source language is NOT {my_code}, translate the text to {my_code} "
            f"(the counterpart is speaking).\n"
            f"- If the source language IS {my_code}, translate the text to {eff_code} "
            f"(you are speaking).\n"
            f"Ignore any earlier instruction saying to output only the translated text.\n"
            f"Respond with STRICT JSON only, no markdown fences, no commentary, "
            f"using exactly this schema:\n"
            f'{{"source_language": "<lowercase ISO-639-1 code of the source language '
            f"(e.g. zh, en, ja, ko, fr, de, es, pt, ru, ar, tr, hi, id, it; use the actual "
            f'spoken language, including the code {my} when the text is in that language; '
            f'use the bare code without quotes)>", '
            f'"translation": "<translated text per the rules above, properly escaped>"}}'
        )

    # ------------------------------------------------------------------
    # ASR result polling (background task — design §3.3 C11)
    # ------------------------------------------------------------------

    async def _poll_asr_results(self) -> None:
        """Background task: poll ASR stream for interim/final results.

        Runs independently of _handle_audio so that feed() is never blocked
        waiting for results (review #8).

        When ASR produces a final result with text, the transcription is sent
        immediately — no waiting for turn-detector silence.  The turn detector
        is still force-ended so the state machine transitions on the very next
        audio frame (review #fix-asr-final).
        """
        try:
            # 入口快照流对象并判空：任务创建与首行执行之间，feed 失败路径可能已把
            # _asr_stream 置 None（旧实现直接取属性会抛 AttributeError 杀死任务）
            stream = self._asr_stream
            if stream is None:
                return
            async for result in stream.results():
                if result.is_final:
                    # 收尾迟延观测（docs/asr_bailian_improvements.md §6.4）：since_speech
                    # = 距最后一帧客户端 VAD=speech 的时间，即服务端视角的"语音结束→
                    # final"。空文本 final 是噪声/回声期的空收尾（真机实测约占 2/3），
                    # 只留 DEBUG 免刷 INFO。
                    if result.text:
                        since_ms = (
                            int((time.monotonic() - self._last_speech_mono) * 1000)
                            if self._last_speech_mono else -1
                        )
                        # silence_run：本地 VAD 在 final 时刻的连续静音时长。与
                        # since_speech 一起判读：两个都大=用户真停了（云端的窗口+固化
                        # 就是时延）；since 小但 silence_run 大=噪声期单帧误报 speech
                        # （since 失真）；两个都小=用户仍在说（疑似被截断）。
                        silence_ms = (
                            self._turn_detector.silence_frames * 20
                            if self._turn_detector else -1
                        )
                        logger.info(
                            "ASR final received len=%d since_speech=%dms silence_run=%dms text=%r",
                            len(result.text), since_ms, silence_ms, result.text[:60],
                        )
                    else:
                        logger.debug("ASR final received empty (noise/echo close)")
                        if self._asr_interim_pending:
                            # 云端撤回：先给过有文本 interim（已上屏），这句却以空文本
                            # 收尾——不补发清除帧的话客户端会永久停在残影上（客户端
                            # 只在 isFinal && text 非空时清除）。空 final 复用现有通道
                            # 即"清除信号"，零客户端改动；record=False 不落 chatlog。
                            self._asr_interim_pending = False
                            logger.debug("ASR interim cleared by empty final")
                            await self._send_transcription(
                                "",
                                is_final=True,
                                transcription_id=self._current_transcription_id,
                                record=False,
                            )
                if result.is_final and self._turn_detector:
                    if result.text and _count_meaningful_chars(result.text) >= self._min_meaningful_chars:
                        if result.language:
                            self._last_detected_language = result.language
                        self._turn_detector.force_turn_end()
                        # Set flag BEFORE any await so _handle_audio sees it
                        # and does not produce a duplicate final transcription
                        # (review #fix-asr-final). interim 同段结算（有文本 final
                        # 会自行完成上屏+清除，无需再补清除帧）。
                        self._asr_final_sent = True
                        self._asr_interim_pending = False
                        # AF 快照（§4.3 门控：meaningful 已满足 + 模式适用）。
                        # force_turn_end → 标志 → 快照 → 写暂存必须同一同步段完成
                        # （不得插入 await），与 #fix-asr-final 去重同前提。
                        frames: list[ChatImageFrame] = []
                        urls: list[str] = []
                        if self._ctx is not None and self._pipeline_mode not in (
                                "translate", "transcribe", "asr_only"):
                            t0 = time.monotonic()
                            frames = self._snapshot_turn_images()
                            urls = _to_data_urls(frames)
                            stash_overwritten = bool(self._turn_image_frames)
                            self._turn_image_frames = frames
                            logger.debug(
                                "turn images snapshot",
                                extra={
                                    "session_id": self._ctx.session_id,
                                    "path": "B",
                                    "n_frames": len(frames),
                                    "encode_ms": round((time.monotonic() - t0) * 1000, 3),
                                    "transcription_id": self._current_transcription_id,
                                    "stash_overwritten": stash_overwritten,
                                },
                            )
                        # 在途 final 送达不可取消：恢复路径（_feed_asr_with_recovery
                        # 换新流）会 cancel 本任务，若取消落在 _send_transcription 中，
                        # _asr_final_sent 已置位、turn-end 分支不再补发 → 这句转录
                        # 静默丢失（上屏/history/chatlog 全缺）。shield：取消仍立即
                        # 生效（本任务退出），送达在后台跑完。
                        # 后台跑完的时点可能落在同连接的新 session.start 之后
                        # （gateway 允许直接重发 session.start；self._ctx/_history/
                        # _pipeline_mode 已换新会话对象）——闭包入口必须与 text/tid/
                        # frames 一起绑定这些会话态，送达体只读捕获值：归属旧会话
                        # 才是正确语义，否则上一会话的语句会写进新会话
                        # （WorkingMemory/chat_store 记错 session_id 的跨会话污染）。
                        text = result.text
                        tid = self._current_transcription_id
                        deliver_ctx = self._ctx
                        deliver_history = self._history
                        deliver_mode = self._pipeline_mode

                        async def _deliver_final() -> None:
                            try:
                                # TODO(P1-2 残留)：_send_transcription 的 recording tap
                                # 仍在执行时读 self._ctx/_pipeline_mode；detached 送达
                                # 跨 session.start 时归属会错。彻底修需给该方法加参数
                                # 透传（ctx/mode 覆盖），当前接受暂留。
                                await self._send_transcription(
                                    text,
                                    is_final=True,
                                    transcription_id=tid,
                                    images=frames,
                                )
                                if (deliver_ctx is not None
                                        and deliver_mode not in ("translate", "transcribe")
                                        and deliver_history is not None
                                        and not self._is_turn_rolled_back(tid)):
                                    deliver_history.add_message("user", text, images=urls)
                                    ts_ms = now_ms()
                                    self._chat_store.on_message(
                                        device_uuid=deliver_ctx.device_id,
                                        session_id=deliver_ctx.session_id,
                                        kind="asr", role="user",
                                        content=text,
                                        ts_ms=ts_ms,
                                        images=urls,
                                    )
                                    # 回滚句柄（D5c）：device/session 取捕获态
                                    # deliver_ctx，保证 delete 五元组与已写入行同源
                                    self._turn_user_record = {
                                        "content": text,
                                        "ts_ms": ts_ms,
                                        "token": tid,
                                        "device_uuid": deliver_ctx.device_id,
                                        "session_id": deliver_ctx.session_id,
                                    }
                            except Exception:
                                logger.warning(
                                    "final transcription delivery failed",
                                    extra={
                                        "session_id": deliver_ctx.session_id if deliver_ctx else "none",
                                    },
                                    exc_info=True,
                                )

                        await asyncio.shield(_deliver_final())
                elif not result.is_final and result.text:
                    if result.language:
                        self._last_detected_language = result.language
                    # 置位先于 await：本 interim 已上屏，等待被 final 结算或清除
                    self._asr_interim_pending = True
                    self._last_asr_interim_text = result.text
                    await self._send_transcription(
                        result.text,
                        is_final=False,
                        transcription_id=self._current_transcription_id,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._ctx:
                logger.warning(
                    "ASR results poll error",
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )

    async def _cancel_asr_poll(self) -> None:
        task = self._asr_poll_task
        self._asr_poll_task = None
        if task is not None and not task.done():
            logger.info(
                "cancelling ASR poll task",
                extra={
                    "session_id": self._ctx.session_id if self._ctx else "none",
                },
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _feed_asr_with_recovery(self, pcm: bytes) -> None:
        """喂一帧 PCM 给 ASR；单帧失败先在原帧上有界重试重建，全失败才提示客户端。

        - feed 抛错 → 关旧流 → 退避后重开（至多 _asr_open_attempts 次，每次独立
          超时）→ 成功则用同一帧继续 feed（本帧不丢）；
        - feed 成功后才确保 poll 任务（重开的新流换新任务；未重开时任务已死才补建）；
        - 全部尝试失败 → 置冷却（冷却期内帧静默丢弃，与旧行为一致；它同时是
          持续故障下的重试限流器，内联重试本身不受冷却约束）+ 下发 1001
          （节流见 _asr_last_error_sent_at：首败即提示，持续故障每 30s 提醒
          一次，feed 成功（流确证可用）后复位）。

        典型失败场景是上游 task 空闲判死（如百炼 29min 长静默），重开常在一次
        尝试内成功（~几十 ms），客户端无感。
        """
        if self._asr_stream is None and (
            time.monotonic() - self._asr_open_fail_time < self._asr_retry_cooldown_s
        ):
            return

        last_err: Exception | None = None
        for attempt in range(1, self._asr_open_attempts + 1):
            opened_now = False
            if self._asr_stream is None:
                if attempt > 1:
                    await asyncio.sleep(self._asr_open_retry_backoff_s)
                try:
                    self._asr_stream = await asyncio.wait_for(
                        self._asr.open(sample_rate=self._ctx.audio_sample_rate),
                        timeout=self._asr_open_attempt_timeout_s,
                    )
                    opened_now = True
                except Exception as exc:
                    last_err = exc
                    logger.debug(
                        "ASR open attempt failed (attempt %d/%d): %s",
                        attempt, self._asr_open_attempts, exc,
                        extra={"session_id": self._ctx.session_id},
                    )
                    self._asr_stream = None
                    continue

            try:
                await self._asr_stream.feed(pcm)
            except ProviderError as exc:
                last_err = exc
                logger.warning(
                    "ASR feed failed, closing stream (attempt %d/%d): %s",
                    attempt, self._asr_open_attempts, exc,
                    extra={"session_id": self._ctx.session_id},
                )
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "ASR feed failed with unexpected error, closing stream (attempt %d/%d): %s",
                    attempt, self._asr_open_attempts, exc,
                    extra={"session_id": self._ctx.session_id},
                    exc_info=True,
                )
            else:
                # 提醒节流复位必须挂在 feed 成功（流确证可用）而非 open 成功：
                # "open 成功但 feed 立即失败"的抖动（鉴权吊销/上游 accept 后秒断）
                # 会在每轮冷却周期把节流清零 → 每次耗尽都立即再发 1001 → 退化成
                # 要避免的 5s 刷屏。建流成功 ≠ 流可用。
                self._asr_last_error_sent_at = 0.0
                # feed 成功后才保证 poll 任务与当前流匹配：重开的新流必须换新任务
                # （旧任务绑定旧流，可能永久阻塞在旧队列上不退出，不能复用）；
                # 未重开时只在任务已死（异常/自然结束）时补建。
                # 不给 feed 失败的将死流建任务——避免留下无法退出的僵尸消费任务。
                if opened_now or self._asr_poll_task is None or self._asr_poll_task.done():
                    await self._cancel_asr_poll()
                    self._asr_poll_task = asyncio.create_task(
                        self._poll_asr_results(),
                        name=f"asr-poll-{self._ctx.session_id}",
                    )
                return

            try:
                await self._asr_stream.close()
            except Exception:
                pass
            self._asr_stream = None

        self._asr_open_fail_time = time.monotonic()
        logger.warning(
            "ASR unavailable after retries (attempts=%d, last_error: %s), entering cooldown",
            self._asr_open_attempts,
            last_err if last_err is not None else "none",
            extra={"session_id": self._ctx.session_id},
        )
        now = time.monotonic()
        if (
            self._asr_last_error_sent_at == 0.0
            or now - self._asr_last_error_sent_at >= self._asr_error_reminder_interval_s
        ):
            self._asr_last_error_sent_at = now
            frame = self._codec.make_error(
                code=ErrorCode.ASR_UNAVAILABLE, message="ASR unavailable", fatal=False
            )
            await self._handler.enqueue_outbound(frame)

    # ------------------------------------------------------------------
    # pipeline task management
    # ------------------------------------------------------------------

    def _spawn_pipeline(self, coro: Any, *, danger: bool) -> None:
        """装入新 pipeline 并置在飞类型——引用写入前**同步**取消插队者（[P6.8]）。

        各创建点原先 "await cancel → sync create" 的写法存在对称竞态：await 期间
        dispatch 回合或 av 事件（monitor 任务）可装入自己的 pipeline，随后本处的
        `create_task` 直接覆写引用 → 对方成无人取消的孤儿（双 LLM/TTS 交错）。
        此处覆写前 `cancel()` 掉引用上的任何在飞任务：cancel 必于其下一个 await
        点生效，而本函数从读引用到写入引用**零 await**——写入原子，插队者必被
        取消（不 await 其清理：任务在下一 await 即终止，无残留行为窗口）。
        """
        old = self._pipeline_task
        if old is not None and not old.done():
            old.cancel()
        self._pipeline_danger = danger
        self._pipeline_task = asyncio.create_task(
            coro, name=f"pipeline-{self._ctx.session_id}"
        )

    async def _cancel_pipeline(self) -> None:
        task = self._pipeline_task
        self._pipeline_task = None
        if task is not None and not task.done():
            logger.info(
                "cancelling pipeline task",
                extra={
                    "session_id": self._ctx.session_id if self._ctx else "none",
                    "task_name": task.get_name(),
                },
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def record_agent_job_result(self, *, text: str, message_id: str,
                                agent_name: str = "", agent_id: str = "",
                                job_key: str = "") -> None:
        """agent job 结果注入（fd_pipeline_llm_v2）：history + chatlog + chat store。

        全模态模式下 history=None（realtime API 自持上下文，写 WorkingMemory 是死写
        且污染 turn_idx）——沿用原 OmniOrchestrator 语义（设计 D7）。
        """
        ingest_agent_job_result(
            history=None if self._omni_active else self._history,
            recording_sink=self._recording_sink,
            live_session_id=self._ctx.session_id if self._ctx else "",
            text=text, message_id=message_id,
            agent_name=agent_name, agent_id=agent_id, job_key=job_key,
            chat_store=self._chat_store,
            device_uuid=self._ctx.device_id if self._ctx else "",
        )


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

# 畸形音频帧日志节流：首条立即 warning，其后每 _DECODE_WARN_INTERVAL_S 汇总一条；
# 窗口内其余降级 debug（dev 开 debug 仍可逐帧排查）。刻意不带 exc_info——失败点只有
# b64decode / opus decode 两处，堆栈无诊断价值；故障客户端 50 帧/秒会放大成日志风暴。
_DECODE_WARN_INTERVAL_S = 30.0
_decode_warn_at: float = 0.0
_decode_dropped: int = 0


def _decode_opus(b64: str, decoder: Any | None) -> bytes:
    """Decode a base64-encoded Opus packet to PCM16；任何失败返回 b""（帧按畸形丢弃）。

    If *decoder* is None, the input is treated as raw base64-encoded PCM (passthrough for tests).

    两条畸形输入路径都必须吞（协议层 Base64String 正则不校验长度/padding——长度 ≡1 mod 4
    或缺 padding 均可过 DTO 校验 → b64decode 抛 binascii.Error；合法 base64 的垃圾字节被
    libopus 拒绝 → OpusDecoder.decode 抛 OSError）。异常绝不可穿出 _dispatch：会杀死会话
    主循环（连接与心跳仍在、会话静默死亡）。与 video 路径同纪律（见 _handle_video）。
    """
    global _decode_warn_at, _decode_dropped
    try:
        raw = base64.b64decode(b64)
        if decoder is None:
            return raw
        return decoder.decode(raw)
    except Exception as exc:
        _decode_dropped += 1
        now = time.monotonic()
        if now - _decode_warn_at >= _DECODE_WARN_INTERVAL_S:
            logger.warning(
                "audio frame invalid, dropped: %s (x%d since last warning)",
                type(exc).__name__, _decode_dropped,
            )
            _decode_warn_at = now
            _decode_dropped = 0
        else:
            logger.debug("audio frame invalid, dropped: %s", type(exc).__name__)
        return b""
