"""avdetector 旁路监测器 —— DialogOrchestrator 持有的 chat 模式子组件。

设计锚点：docs/fd_pipeline_avdetector.md §4 D2/D5（[R13]/[R14]/[R15]/[R16]/[R17]）。

纪律：
- 热路径（feed_audio / feed_frame / request_*_check）全部**同步投递**，绝不 await 网络；
  网络 IO 全在 monitor 自有任务（单管道 pipeline + 定时器）里做——检测流背压不阻塞
  dispatch 主循环；
- 单管道串行：音频 append / 静默 commit / 图片 append / 检查 依序执行——窗口顺序由管道序
  天然保证；"先音频后图"约束由 provider 静音自愈承担（[P6]：无窗口音频时先补 20ms 静音）；
- 窗口纪律（D5 ★）：周期静默 commit 给音频窗口划界；检查的转写否决只看本次检查窗口
  （provider.check 的 transcripts 即该窗口，静默 commit 的结算事件被丢弃）；
- 帧槽（[R16]，2026-10-09 修订为 K=1）：只保留最新一帧非 text 视频帧（零网络；
  text 帧由 orchestrator 侧门控，不入槽不触发）；
- 查询契约（[R13]→[P6.9] 修订）：**视频检查**上送"槽内最新帧 + 自上次提交的音频"；
  **音频补充检查纯声音**（不附帧——原"统一附帧"是双输：付画面 token、得画面描述，
  却按声音事件投递；画面变化归 10s 视频主通道）；
- 失败**静默降级**：冷却重试 + 连续 3 次熔断（[R10]；计数在**成功开流/成功检查**时
  清零——[P6.11]）；周期重建（历史限额兜底）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from avdetector.base import AvCheckTimeout

logger = logging.getLogger("livachat.session.av_detector")

_MAX_CONSECUTIVE_FAILURES = 3  # [R10] 拍板：会话级熔断阈值

OnAvEvent = Callable[[str, str, str, "tuple | None"], Awaitable[None]]

# [P6.1]/[P6.13] 转写否决判定：去标点/空白后**不同字符数**阈值。
# 真咳嗽/喷嚏等发声类非语音事件会被 ASR 转写成单字重复型拟声（'咳咳。'、'呵呵'）
# ——同一字符重复不算"在说话"，放行事件。阈值 2 → 5（[P6.13] 方案 A，2026-10-06）：
# 仅成句级（≥5 种字符）才算"在说话"，2–4 种字符的短转写（'好的'/'OK'/'哈喽'）放行
# ——说话误报兜底退化至投递侧（状态/在飞门 + 30s 冷却 + barge-in 链；doc §10 条目 25）。
# 标点剥离按 Unicode 类别（P*）而非手写表，封闭覆盖未见标点/全角空格（[P6.2] review 修复）。
_VETO_MIN_DISTINCT_CHARS = 5

# [P6.4] 常见非语音发声的两字拟声：整串精确匹配豁免（喷嚏 `'啊嚏'` 是产品目标场景之一；
# 笑声等边界发声同理；混合真实词句如 `'啊嚏，不好意思'` 不豁免，说话误报兜底不退化）。
# [P6.13] 阈值 ≥5 下本集合对现有条目已冗余（均 ≤2 种字符）——保留作显式语义与阈值回降防护。
_NON_SPEECH_ONOMATOPOEIA = frozenset({
    "啊嚏", "阿嚏", "呵嚏", "喷嚏",
    "哈哈", "呵呵", "嘿嘿", "嘻嘻",
    "咳咳", "嗯嗯", "啊啊", "呜呜",
})


def is_speech_like_text(text: str) -> bool:
    """"像在说话"的文本判定（转写否决与竞态复检共用，[P6.1]/[P6.3]/[P6.4]/[P6.13]）。"""
    norm = "".join(
        ch for ch in text
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )
    if norm in _NON_SPEECH_ONOMATOPOEIA:
        return False
    return len(set(norm)) >= _VETO_MIN_DISTINCT_CHARS


def is_danger_event(event: str) -> bool:
    """危险事件判定：event 固定枚举后精确匹配。"""
    return event == "danger"


def _parse_reply(text: str) -> dict[str, str] | None:
    """解析检测回复：`NONE` → None；一行 JSON（容忍 ``` 围栏/前后赘文）→ {event, desc}。

    event 只接受 normal/danger（大小写/空白归一）。normal 空描述丢弃；
    danger 空描述保留（content 走 label-only 安全兜底）。不可解析 → None。
    """
    t = text.strip()
    if not t or t.upper() == "NONE":
        return None
    obj: Any = None
    try:
        obj = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        s, e = t.find("{"), t.rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads(t[s : e + 1])
            except (json.JSONDecodeError, ValueError):
                return None
    if not isinstance(obj, dict):
        return None
    event = str(obj.get("event") or "").strip().lower()
    if event not in ("normal", "danger"):
        return None
    desc = str(obj.get("desc") or "").strip()
    if event == "normal" and not desc:
        return None
    return {"event": event, "desc": desc}


class AvDetectorMonitor:
    def __init__(
        self,
        *,
        service: Any,
        system_prompt: str = "",
        cfg: Any = None,
        sample_rate: int = 16000,
        on_event: OnAvEvent | None = None,
        session_id: str = "",
        fail_cooldown_s: float = 5.0,
        queue_maxsize: int = 256,
    ) -> None:
        self._service = service
        self._system_prompt = system_prompt
        self._sample_rate = sample_rate
        self._on_event = on_event
        self._sid = session_id
        self._fail_cooldown_s = fail_cooldown_s
        self._check_timeout_s = float(getattr(cfg, "check_timeout_s", 8.0))
        self._commit_interval_s = float(getattr(cfg, "audio_commit_interval_s", 15.0))
        self._check_min_interval_s = float(getattr(cfg, "check_min_interval_s", 10.0))
        self._video_interval_s = float(getattr(cfg, "video_check_interval_s", 10.0))
        self._image_disable_cooldown_s = float(getattr(cfg, "image_disable_cooldown_s", 300.0))
        self._reopen_interval_s = float(getattr(cfg, "reopen_interval_s", 600.0))
        gen_cfg = getattr(cfg, "generation", None)
        self._gen_opts = gen_cfg.model_dump() if gen_cfg is not None else None

        # 帧槽（K=1；[R16] 2026-10-09 修订——原 10 帧环形缓存的历史帧从未被读，删除）；
        # 元组：(jpeg, width, height, orientation, camera_facing)；零网络
        self._latest_frame: tuple | None = None
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=queue_maxsize)
        self._stream: Any = None
        self._pipeline_task: asyncio.Task[None] | None = None
        self._commit_task: asyncio.Task[None] | None = None
        self._reopen_task: asyncio.Task[None] | None = None
        self._closed = False
        self._fused = False
        self._started = False
        self._consecutive_failures = 0
        # 查询超时单独计数（D5 口径：超时≠连接故障，只丢结果；连续 2 次才降级）
        self._consecutive_timeouts = 0
        self._open_fail_time = 0.0
        self._window_bytes = 0
        self._last_check_at = 0.0          # 任意检查（音频补充通道的全局最小间隔）
        self._last_video_check_at = 0.0    # 视频主通道独立节拍（[R13]：音频让位于视频）
        # 服务端拒图（超限/非法）后的停图截止（monotonic；0=未停图）——[P6.14] 限时
        # `image_disable_cooldown_s`：到期后下一个视频检查惰性恢复附帧重试；保住音频
        # 检测不被熔断的目标不变
        self._images_disabled_until = 0.0
        self.dropped_audio = 0

    # -- 生命周期 -------------------------------------------------------------

    def start(self) -> None:
        if self._started or self._closed:
            return
        self._started = True
        self._pipeline_task = asyncio.create_task(self._pipeline(), name=f"av-pipeline-{self._sid}")
        self._commit_task = asyncio.create_task(self._commit_loop(), name=f"av-commit-{self._sid}")
        self._reopen_task = asyncio.create_task(self._reopen_loop(), name=f"av-reopen-{self._sid}")

    def ready(self) -> bool:
        return self._started and not self._closed and not self._fused

    @property
    def has_frames(self) -> bool:
        return self._latest_frame is not None

    @property
    def fused(self) -> bool:
        return self._fused

    async def close(self) -> None:
        self._closed = True
        tasks = (self._pipeline_task, self._commit_task, self._reopen_task)
        for t in tasks:
            if t is not None and not t.done():
                t.cancel()
        for t in tasks:
            if t is not None:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._pipeline_task = self._commit_task = self._reopen_task = None
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                await stream.close()
            except Exception:
                pass
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        logger.info("avdetector monitor closed", extra={"session_id": self._sid})

    async def reopen(self) -> None:
        """显式重建（模式切回 chat 时惰性重建由 ensure_stream 承担，本方法备用）。"""
        if self._stream is not None:
            self._enqueue(("reopen", None))

    # -- 热路径（同步投递，零 await 网络） -------------------------------------

    def feed_audio(self, pcm: bytes) -> None:
        if not self.ready():
            return
        self._window_bytes += len(pcm)
        self._enqueue(("audio", pcm))

    def feed_frame(
        self,
        jpeg: bytes,
        *,
        width: int,
        height: int,
        orientation: int = 0,
        camera_facing: str = "",
    ) -> None:
        """非 text 视频帧覆写本地帧槽（零网络；text 帧由 orchestrator 侧门控不进来）。"""
        if not self.ready():
            return
        self._latest_frame = (jpeg, width, height, orientation, camera_facing)

    def request_audio_check(self) -> bool:
        """音频补充通道触发（turn-end 空识别时由 orchestrator 调用）。"""
        if not self.ready():
            return False
        now = time.monotonic()
        if now - self._last_check_at < self._check_min_interval_s:
            return False
        self._last_check_at = now
        self._enqueue(("audio_check", None))
        return True

    def request_video_check(self, *, force: bool = False) -> bool:
        """视频主通道触发（无 text 帧到达、节流门控通过时由 orchestrator 调用）。

        独立节拍（[R13]"有图时音频让位于视频节拍"）：只受自身 `video_check_interval_s`
        约束——不被音频检查的时间戳挤压（真机联调实证：共享时间戳会饿死视频通道）。
        force=True 仅绕过节流（视觉回合抑制窗开窗时的基线检查）；ready 与帧槽非空仍必需，
        两个检查时间戳照常更新。
        """
        if not self.ready() or self._latest_frame is None:
            return False
        now = time.monotonic()
        if not force and now - self._last_video_check_at < self._video_interval_s:
            return False
        self._last_video_check_at = now
        self._last_check_at = now  # 同时推高全局间隔 → 音频补充自然让位
        self._enqueue(("video_check", None))
        return True

    def _enqueue(self, cmd: tuple[str, Any]) -> None:
        try:
            self._queue.put_nowait(cmd)
            return
        except asyncio.QueueFull:
            pass
        # 队列满（检测流背压）：丢最旧（大概率是音频帧）再入队——热路径绝不等待
        try:
            self._queue.get_nowait()
            self.dropped_audio += 1
        except asyncio.QueueEmpty:
            pass
        try:
            self._queue.put_nowait(cmd)
        except asyncio.QueueFull:
            pass

    # -- 单管道 ---------------------------------------------------------------

    async def _pipeline(self) -> None:
        try:
            while True:
                cmd, payload = await self._queue.get()
                if self._closed:
                    return
                try:
                    await self._handle(cmd, payload)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if not self._closed:
                        logger.warning(
                            "avdetector %s failed, degrading", cmd,
                            extra={"session_id": self._sid}, exc_info=True,
                        )
                        await self._degrade()
        except asyncio.CancelledError:
            return

    async def _handle(self, cmd: str, payload: Any) -> None:
        if cmd == "audio":
            stream = await self._ensure_stream()
            if stream is None:
                return
            try:
                await stream.feed_audio(payload)
            except Exception:
                await self._degrade()
        elif cmd == "commit":
            if self._stream is None:
                return
            await self._stream.commit_audio()  # 发送失败 provider 自行重连；重连失败抛错 → _pipeline 兜底降级
            self._window_bytes = 0
        elif cmd == "audio_check":
            await self._run_check(video=False)
        elif cmd == "video_check":
            await self._run_check(video=True)
        elif cmd == "reopen":
            if self._stream is None:
                return
            try:
                await self._stream.reopen()
                self._window_bytes = 0
            except Exception:
                await self._degrade()

    async def _ensure_stream(self) -> Any:
        if self._fused:
            return None
        if self._stream is not None:
            return self._stream
        now = time.monotonic()
        if now - self._open_fail_time < self._fail_cooldown_s:
            return None
        try:
            stream, provider = await self._service.open(
                sample_rate=self._sample_rate,
                system_prompt=self._system_prompt,
                generation_opts=self._gen_opts,
            )
        except Exception:
            self._open_fail_time = now
            self._consecutive_failures += 1
            if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                self._fused = True
            logger.warning(
                "avdetector open failed (failures=%d)", self._consecutive_failures,
                extra={"session_id": self._sid},
            )
            return None
        self._stream = stream
        self._open_fail_time = 0.0
        self._consecutive_timeouts = 0
        self._consecutive_failures = 0  # [P6.11] 开流成功 = 连接已恢复证据：清零
        logger.info(
            "avdetector stream opened provider=%s", provider,
            extra={"session_id": self._sid},
        )
        return stream

    async def _run_check(self, *, video: bool) -> None:
        """一次检测查询；结果经三重门（解析/转写否决/回调）后投递给 orchestrator。"""
        stream = await self._ensure_stream()
        if stream is None:
            return
        # 查询契约（[R13]→[P6.9] 修订）：附帧仅限**视频主通道**；音频补充检查纯声音。
        # 原"统一附帧"在音频通道双输：付画面 token（+400/次）、按提示词"有图画面为主"
        # 大概率回画面描述，投递却按声音语义（不豁免冷却、final ai_text 不回带图）、
        # 日志 kind="画面" 与投递自相矛盾。画面变化归 10s 视频通道。
        # [P6] 静音自愈仍承担"先音频后图"（provider 层）；拒图停图（[P6.14] 限时）只影响视频通道。
        frame = None
        if video and self._latest_frame is not None:
            now = time.monotonic()
            if now < self._images_disabled_until:
                # [P6.14] 停图期内：纯音频检查（到期后下一个视频检查惰性恢复附帧）
                logger.debug(
                    "avdetector video check without frame (images disabled %.0fs left)",
                    self._images_disabled_until - now,
                    extra={"session_id": self._sid},
                )
            else:
                if self._images_disabled_until:
                    logger.info(
                        "avdetector image attach re-enabled after cooldown",
                        extra={"session_id": self._sid},
                    )
                    self._images_disabled_until = 0.0
                frame = self._latest_frame
        elif video:
            # 联调可观测性：视频检查"没带图"时给出原因（帧槽为空）
            logger.debug(
                "avdetector video check without frame (empty slot)",
                extra={"session_id": self._sid},
            )
        if frame is not None:
            try:
                await stream.feed_image(frame[0], width=frame[1], height=frame[2])
            except Exception:
                # 上图失败不拖垮本轮：降级为纯音频检查（feed 内部已重连重试过）
                logger.warning(
                    "avdetector image feed failed, running audio-only check",
                    extra={"session_id": self._sid}, exc_info=True,
                )
                frame = None
        try:
            result = await stream.check(timeout_s=self._check_timeout_s)
        except AvCheckTimeout:
            # 超时 ≠ 连接故障（D5）：只丢本次结果、不计熔断失败；连续 2 次
            # 视为流僵死（挂起的 response 会持续卡住后续检查）→ 降级重开
            self._consecutive_timeouts += 1
            logger.warning(
                "avdetector check timeout (consecutive=%d)", self._consecutive_timeouts,
                extra={"session_id": self._sid},
            )
            if self._consecutive_timeouts >= 2:
                await self._degrade()
            return
        except Exception as exc:
            if frame is not None and "image" in str(exc).lower():
                # 服务端拒图（如超 256KB/1080p 限制）→ [P6.14] 停图限时
                # `image_disable_cooldown_s`（默认 300s，到期后下一个视频检查惰性恢复），
                # 保住音频检测不被熔断——停图期纯音频检查成功即清零失败计数，
                # 到期重试再拒也不累计到 3 次熔断
                self._images_disabled_until = time.monotonic() + self._image_disable_cooldown_s
                logger.warning(
                    "avdetector image rejected by provider; image attach disabled for %.0fs",
                    self._image_disable_cooldown_s,
                    extra={"session_id": self._sid},
                )
            await self._degrade()
            return
        self._consecutive_failures = 0
        self._consecutive_timeouts = 0
        parsed = _parse_reply(result.text)
        if parsed is None:
            raw = result.text.strip()
            if raw.upper() == "NONE":
                logger.debug(
                    "avdetector reply discarded (NONE) text=%r",
                    result.text[:80], extra={"session_id": self._sid},
                )
            else:
                logger.warning(
                    "avdetector reply discarded (invalid event/json) text=%r",
                    result.text[:200], extra={"session_id": self._sid},
                )
            return
        if parsed["event"] == "danger" and not parsed["desc"]:
            logger.warning(
                "avdetector danger event without desc, label-only fallback text=%r",
                result.text[:200], extra={"session_id": self._sid},
            )
        if any(is_speech_like_text(t) for t in result.transcripts):
            # 说话否决（§4 D2 三重门②；[P6.13] 阈值 ≥5 种字符：仅成句级才算"在说话"——
            # 单字重复拟声（'咳咳。'）与 2–4 种字符短转写（'好的'/'OK'）均放行）
            logger.info(
                "avdetector event vetoed by speech transcript event=%s desc=%r transcripts=%r",
                parsed["event"], parsed["desc"][:60], result.transcripts[:2],
                extra={"session_id": self._sid},
            )
            return
        cb = self._on_event
        if cb is None:
            return
        kind = "画面" if frame is not None else "声音"
        try:
            await cb(kind, parsed["desc"], parsed["event"], frame)
        except Exception:
            logger.warning("avdetector on_event failed", extra={"session_id": self._sid}, exc_info=True)

    async def _degrade(self) -> None:
        """降级计失败一次；计数为"自上次成功开流/成功检查以来的连续失败"——
        成功开流即清零（[P6.11]：跨成功开流的瞬时失败不累计误熔断；恒开流成功但
        恒使用失败的病态服务因此不熔断，重连受 5s 冷却节制，已知边界）。"""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                await stream.close()
            except Exception:
                pass
        self._window_bytes = 0
        self._consecutive_timeouts = 0
        self._open_fail_time = time.monotonic()
        self._consecutive_failures += 1
        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES and not self._fused:
            self._fused = True
            logger.warning(
                "avdetector fused after %d consecutive failures", self._consecutive_failures,
                extra={"session_id": self._sid},
            )

    # -- 定时器 ---------------------------------------------------------------

    async def _commit_loop(self) -> None:
        """窗口纪律（D5 ★）：每 audio_commit_interval_s 对未提交窗口发一次静默 commit。"""
        try:
            while True:
                await asyncio.sleep(self._commit_interval_s)
                if self._closed:
                    return
                if self._stream is not None and self._window_bytes > 0:
                    self._enqueue(("commit", None))
        except asyncio.CancelledError:
            return

    async def _reopen_loop(self) -> None:
        """周期重建（历史限额兜底；检测器无对话状态需保留）。"""
        try:
            while True:
                await asyncio.sleep(self._reopen_interval_s)
                if self._closed:
                    return
                if self._stream is not None:
                    self._enqueue(("reopen", None))
        except asyncio.CancelledError:
            return
