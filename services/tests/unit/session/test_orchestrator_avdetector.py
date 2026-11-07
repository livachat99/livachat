"""avdetector orchestrator 接线单测（docs/fd_pipeline_avdetector.md §6.1 / §4 D2/D3）。

锁定语义：
- 音频触发："turn_ended 且 ASR 空识别 + LISTENING + ASR 健康 + 冷却 OK + 窗口有 VAD speech"；
  纯静音 / 冷却中 / monitor 未就绪不触发；有意义 final 走标准链路不触发；
- 视频触发：非 text 帧入缓存并可触发；带 `payload.text` 的帧零调用（[R14]）；
  THINKING 等非 LISTENING 状态只缓存不触发；
- `_on_av_event` 门控：模式/状态/pipeline 在飞/冷却（危险 `danger` 豁免 [R15]）；
- 事件注记三种文案（关心/点评画面/危险提醒）；帧 → `ChatImageFrame`；
- `_start_thinking(event_note)` 并入 system、无 user 落库、assistant 行照常；
- 模式门控（切出 chat 关、切回重建）；关闭态（service=None）零行为差异。
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from livachat_protocol.generated.protocol_models import (
    AvDetectorSwitch,
    ClientSessionStartConfig,
)
from recording.sink import ChatImageFrame
from shared.utils.errors import ErrorCode
from session.context import SessionContext
from session.history import WorkingMemory
from session.orchestrator import DialogOrchestrator
from session.state_machine import SessionState, SessionStateMachine
from tests.unit.session.conftest import FakeChatStore

_JPEG = b"\xff\xd8fakejpeg"


def _handler_mock() -> MagicMock:
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _make_orch(*, avdetector_service: object | None = None, pipeline_mode: str = "full",
               proactive_cooldown_s: float = 30.0,
               visual_turn_suppress_s: float = 5.0,
               language: str = "zh-Hans"):
    from shared.config.models import AvDetectorConfig

    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    ctx = SessionContext(
        session_id="test-sid", device_id="dev-1", user_id="user-1",
        sm=sm, enable_video=True, audio_sample_rate=16000,
        latest_video_frames=deque(), language=language,
    )
    cfg = AvDetectorConfig(
        enabled=True, active="fake",
        proactive_cooldown_s=proactive_cooldown_s,
        visual_turn_suppress_s=visual_turn_suppress_s,
    )
    orch = DialogOrchestrator(
        handler=_handler_mock(),
        codec=MagicMock(),
        session_mgr=None,
        asr_service=None,
        llm_service=None,
        tts_service=None,
        opus_encoder=None,
        opus_decoder=None,
        system_prompt="You are a companion.",
        generation_opts={},
        chat_store=FakeChatStore(),
        pipeline_mode=pipeline_mode,
        avdetector_service=avdetector_service,
        avdetector_cfg=cfg,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._turn_detector = MagicMock()
    orch._interrupt = MagicMock()
    orch._interrupt.should_yield.return_value = True
    return orch


class _FakeSink:
    def __init__(self) -> None:
        self.chat_texts: list[dict] = []

    def on_chat_text(self, **kwargs) -> None:
        self.chat_texts.append(kwargs)


def _fake_monitor(*, ready: bool = True) -> MagicMock:
    m = MagicMock()
    m.ready.return_value = ready
    m.has_frames = True
    m.feed_audio = MagicMock()
    m.feed_frame = MagicMock()
    m.request_audio_check = MagicMock(return_value=True)
    m.request_video_check = MagicMock(return_value=True)
    m.close = AsyncMock()
    return m


async def _audio_turn(orch: DialogOrchestrator, *, final_text: str = "", vad: str = "speech") -> None:
    """驱动一帧 turn-end（turn detector 首次调用即返回 True；ASR 流为 fake）。"""
    orch._asr_stream = MagicMock()
    orch._asr_stream.final_text = final_text
    orch._asr_stream.reset = AsyncMock()
    orch._turn_detector.on_frame.return_value = True
    orch._asr_final_sent = False

    msg = MagicMock()
    msg.type = "audio"
    msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
    msg.payload.vad_state.value = vad
    with patch("session.orchestrator.generate_transcription_id",
               return_value="trans_abcd12345678"):
        await orch._handle_audio(msg)
    await asyncio.sleep(0.02)


def _video_msg(*, text: str | None = None) -> MagicMock:
    msg = MagicMock()
    msg.type = "video"
    msg.payload.data = base64.b64encode(_JPEG).decode()
    msg.payload.frame_id = 1
    msg.payload.width = 640
    msg.payload.height = 480
    msg.payload.orientation = 90
    msg.payload.camera_facing = "front"
    # turn_id 必须显式置 None（MagicMock 会凭空生成属性值 → 误入 §8 首帧直起轮分支）
    msg.payload.turn_id = None
    msg.payload.text = text
    return msg


def _change_msg(mode: str) -> MagicMock:
    msg = MagicMock()
    msg.type = "session.change"
    msg.payload.mode = mode
    msg.payload.my_language = None
    msg.payload.local_language = None
    msg.payload.usecase = None
    return msg


def _start_msg() -> MagicMock:
    msg = MagicMock()
    msg.type = "session.start"
    msg.payload.session_id = None
    msg.payload.config.enable_video = True
    msg.payload.config.audio_sample_rate = 16000
    msg.payload.config.vad_sensitivity = "medium"
    msg.payload.config.client_version = "test"
    msg.payload.config.timezone = ""
    msg.payload.config.language = ""
    return msg


def _config_msg(value: str | None = None) -> MagicMock:
    msg = MagicMock()
    msg.type = "session.change"
    msg.payload = SimpleNamespace(mode="config", usecase=None, avdetector=value)
    return msg


def _start_msg_with_config(config: ClientSessionStartConfig) -> MagicMock:
    msg = MagicMock()
    msg.type = "session.start"
    msg.payload = SimpleNamespace(session_id=None, config=config)
    return msg


class _FakeLlm:
    """async generator 形状（与生产 LlmService 一致），并捕获 messages。"""

    def __init__(self, tokens: list[str] | None = None):
        self.tokens = tokens if tokens is not None else ["你好"]
        self.captured_messages: list | None = None

    async def stream(self, *, messages, **opts):
        self.captured_messages = messages
        for t in self.tokens:
            yield t


class TestAudioTrigger:
    @pytest.mark.asyncio
    async def test_empty_turn_with_speech_window_triggers_check(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        await _audio_turn(orch, final_text="", vad="speech")
        orch._av_monitor.feed_audio.assert_called()  # 旁路 feed
        orch._av_monitor.request_audio_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_meaningful_final_goes_standard_flow(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        await _audio_turn(orch, final_text="你好呀今天天气", vad="speech")
        orch._av_monitor.request_audio_check.assert_not_called()  # 走标准三段式
        assert orch._pipeline_task is not None

    @pytest.mark.asyncio
    async def test_pure_silence_window_no_trigger(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        await _audio_turn(orch, final_text="", vad="silence")  # 无 VAD speech → 纯静音
        orch._av_monitor.request_audio_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_proactive_cooldown_does_not_block_detection(self) -> None:
        """[P3] 主动冷却只在投递侧（_on_av_event）——检测照跑，冷却窗内的危险
        行为才有 [R15] 豁免的机会（否则"危险 ≤ 一个检测节流窗"不成立）。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        orch._last_av_proactive = time.monotonic()  # 冷却中
        await _audio_turn(orch, final_text="", vad="speech")
        orch._av_monitor.request_audio_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_ready_monitor_no_trigger(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor(ready=False)
        await _audio_turn(orch, final_text="", vad="speech")
        orch._av_monitor.request_audio_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_records_interim_snapshot(self) -> None:
        """竞态复检基准：触发受理时记录 interim 挂起态快照（§4 D2 三重门③/[P6.2]）。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        orch._asr_interim_pending = True  # 触发前已挂起（如咳嗽 '咳' 的拟声 interim）
        await _audio_turn(orch, final_text="", vad="speech")
        assert orch._av_trigger_interim_pending is True


class TestVideoTrigger:
    @pytest.mark.asyncio
    async def test_non_text_frame_cached_and_triggered(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        await orch._handle_video(_video_msg(text=None))
        orch._av_monitor.feed_frame.assert_called_once_with(
            _JPEG, width=640, height=480, orientation=90, camera_facing="front",
        )
        orch._av_monitor.request_video_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_text_frame_zero_avdetector_calls(self) -> None:
        """[R14]：带 payload.text 的帧不进缓存、不触发（归既有 v3 链路）。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        await orch._handle_video(_video_msg(text="描述图片内容"))
        orch._av_monitor.feed_frame.assert_not_called()
        orch._av_monitor.request_video_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_frames_cached_in_speaking_but_not_triggered(self) -> None:
        """帧缓存全状态入（[R16]）；触发仅 LISTENING。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        orch._ctx.sm.state = SessionState.SPEAKING
        await orch._handle_video(_video_msg(text=None))
        orch._av_monitor.feed_frame.assert_called_once()
        orch._av_monitor.request_video_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_video_trigger_not_blocked_by_proactive_cooldown(self) -> None:
        """[P3] 视频主通道同样不受投递侧冷却冻结（检测照跑，投递再判）。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        orch._last_av_proactive = time.monotonic()  # 冷却中
        await orch._handle_video(_video_msg(text=None))
        orch._av_monitor.request_video_check.assert_called_once()


class TestOnAvEvent:
    async def _call(self, orch, kind, desc, label, frame=None):
        orch._start_thinking = AsyncMock()
        await orch._on_av_event(kind, desc, label, frame)
        await asyncio.sleep(0.01)  # create_task 的 AsyncMock 需要事件循环跑一拍

    @pytest.mark.asyncio
    async def test_image_event_note_and_frame_payload(self) -> None:
        """[P1] 注记不写死来源；[P5] 强制回应口径 + 事件以 user 行落库。"""
        orch = _make_orch(avdetector_service=object())
        await self._call(orch, "画面", "画面中出现一个机器人", "normal",
                         (_JPEG, 640, 480, 90, "front"))
        kwargs = orch._start_thinking.await_args.kwargs
        assert "画面中出现一个机器人" not in kwargs["event_note"]  # desc 只留 user 行
        assert "不要复述" in kwargs["event_note"]  # [P5] 收紧措辞
        assert "来源" not in kwargs["event_note"]
        assert "画面中出现一个机器人" in orch._chat_store.messages[-1]["content"]
        assert kwargs["image_frames"] == [
            ChatImageFrame(data=_JPEG, width=640, height=480, orientation=90, camera_facing="front")
        ]
        assert kwargs["turn_token"].startswith("avd-")  # [P5] 回滚令牌 = user 行 ref_id

    @pytest.mark.asyncio
    async def test_audio_event_note_generic_ask(self) -> None:
        """[P1]/[P5]：声音事件注记强制"回应上一条事件、勿复述历史"。"""
        orch = _make_orch(avdetector_service=object())
        await self._call(orch, "声音", "听到两声咳嗽", "normal")
        kwargs = orch._start_thinking.await_args.kwargs
        assert "听到两声咳嗽" not in kwargs["event_note"]  # desc 只留 user 行
        assert "回应上一条事件" in kwargs["event_note"]
        assert "不要复述、不要延续之前的对话内容" in kwargs["event_note"]
        assert "点评画面" not in kwargs["event_note"]
        assert "听到两声咳嗽" in orch._chat_store.messages[-1]["content"]
        assert kwargs["image_frames"] is None

    @pytest.mark.asyncio
    async def test_audio_event_persists_user_row_three_in_one(self) -> None:
        """[P5] 事件落 user 行（history/chat_store/录制；kind=asr + avd- ref_id）。"""
        orch = _make_orch(avdetector_service=object())
        sink = _FakeSink()
        orch._recording_sink = sink
        await self._call(orch, "声音", "听到两声咳嗽", "normal")
        assert orch._chat_store.messages, "应写 chat_store user 行"
        row = orch._chat_store.messages[-1]
        assert row["role"] == "user" and row["kind"] == "asr"
        assert row["content"] == "[环境事件]听到两声咳嗽"
        assert row["ref_id"].startswith("avd-")
        assert row["images"] == []
        rec = orch._turn_user_record
        assert rec is not None and rec["token"] == row["ref_id"]  # 4xx 回滚句柄
        assert sink.chat_texts and sink.chat_texts[-1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_default_language_is_english(self) -> None:
        orch = _make_orch(avdetector_service=object(), language="")
        await self._call(orch, "声音", "heard two coughs", "normal")
        row = orch._chat_store.messages[-1]
        assert row["content"] == "[Environment event] heard two coughs"
        note = orch._start_thinking.await_args.kwargs["event_note"]
        assert note.startswith("[环境事件·系统注入]")
        assert note.endswith("请使用 English (en) 回复。")

    @pytest.mark.asyncio
    async def test_zh_hant_label(self) -> None:
        orch = _make_orch(avdetector_service=object(), language="zh-Hant")
        await self._call(orch, "声音", "聽到兩聲咳嗽", "normal")
        assert orch._chat_store.messages[-1]["content"] == "[環境事件]聽到兩聲咳嗽"
        note = orch._start_thinking.await_args.kwargs["event_note"]
        assert note.endswith("请使用 Traditional Chinese (zh-Hant) 回复。")

    @pytest.mark.asyncio
    async def test_danger_empty_desc_label_only(self) -> None:
        orch = _make_orch(avdetector_service=object(), language="zh-Hans")
        await self._call(orch, "画面", "", "danger", (_JPEG, 8, 8, 0, ""))
        row = orch._chat_store.messages[-1]
        assert row["content"] == "[环境事件]"
        kwargs = orch._start_thinking.await_args.kwargs
        assert "注意安全" in kwargs["event_note"]
        assert "（非用户本人发言）。" in kwargs["event_note"]

    @pytest.mark.asyncio
    async def test_video_event_persists_image_in_row(self) -> None:
        """[P5] 视频事件：帧随 user 行落库（history/chat_store 带图，与 v3 同款）。"""
        orch = _make_orch(avdetector_service=object())
        await self._call(orch, "画面", "画面中出现一个机器人", "normal",
                         (_JPEG, 640, 480, 90, "front"))
        row = orch._chat_store.messages[-1]
        assert row["images"] and row["images"][0].startswith("data:image/jpeg;base64,")

    @pytest.mark.asyncio
    async def test_danger_bypasses_cooldown_with_urgent_note(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._last_av_proactive = time.monotonic()  # 冷却中——危险豁免 [R15]
        await self._call(orch, "画面", "有人摔倒了", "danger")
        kwargs = orch._start_thinking.await_args.kwargs
        assert "注意安全" in kwargs["event_note"]

    @pytest.mark.asyncio
    async def test_regular_event_blocked_by_cooldown(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._last_av_proactive = time.monotonic()
        await self._call(orch, "声音", "听到两声咳嗽", "normal")
        orch._start_thinking.assert_not_awaited()
        assert orch._chat_store.messages == []  # 被门控拦截 → 无任何落库（[P5]）

    @pytest.mark.asyncio
    async def test_successful_event_sets_proactive_cooldown(self) -> None:
        orch = _make_orch(avdetector_service=object())
        before = orch._last_av_proactive
        await self._call(orch, "声音", "听到咳嗽", "normal")
        assert orch._last_av_proactive > before

    @pytest.mark.asyncio
    async def test_image_event_dropped_when_pipeline_busy(self) -> None:
        """[P6] 普通画面 + LLM 在飞 → 直接丢弃（实时性优先），零落库。"""
        orch = _make_orch(avdetector_service=object())
        inflight = MagicMock()
        inflight.done.return_value = False
        orch._pipeline_task = inflight
        await self._call(orch, "画面", "画面中出现一个机器人", "normal",
                         (_JPEG, 640, 480, 90, "front"))
        orch._start_thinking.assert_not_awaited()
        assert orch._chat_store.messages == []

    @pytest.mark.asyncio
    async def test_image_event_exempt_from_proactive_cooldown(self) -> None:
        """[P6] 画面事件豁免主动冷却：冷却中仍发言（"满足即发言"）。"""
        orch = _make_orch(avdetector_service=object())
        orch._last_av_proactive = time.monotonic()  # 冷却中
        await self._call(orch, "画面", "画面中出现一个机器人", "normal",
                         (_JPEG, 640, 480, 90, "front"))
        orch._start_thinking.assert_awaited()
        assert orch._pipeline_danger is False

    @pytest.mark.asyncio
    async def test_image_event_dropped_by_visual_turn_suppression(self) -> None:
        """[D8] 抑制窗内普通画面事件：丢弃 + 零落库、不起主动回合。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_visual_suppress_until = time.monotonic() + 5.0
        cooldown_before = orch._last_av_proactive
        await self._call(orch, "画面", "画面中出现一个机器人", "normal",
                         (_JPEG, 640, 480, 90, "front"))
        orch._start_thinking.assert_not_awaited()
        assert orch._chat_store.messages == []
        assert orch._last_av_proactive == cooldown_before  # 被丢事件不消耗主动冷却

    @pytest.mark.asyncio
    async def test_image_event_delivered_after_visual_suppression_expires(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._av_visual_suppress_until = time.monotonic() - 0.01
        await self._call(orch, "画面", "画面中出现一个机器人", "normal",
                         (_JPEG, 640, 480, 90, "front"))
        orch._start_thinking.assert_awaited()

    @pytest.mark.asyncio
    async def test_danger_bypasses_visual_turn_suppression(self) -> None:
        """[D8] 危险事件豁免抑制窗：照常投递并走紧迫语气/抢占标记。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_visual_suppress_until = time.monotonic() + 5.0
        await self._call(orch, "画面", "有人摔倒了", "danger", (_JPEG, 8, 8, 0, ""))
        kwargs = orch._start_thinking.await_args.kwargs
        assert "注意安全" in kwargs["event_note"]
        assert orch._pipeline_danger is True

    @pytest.mark.asyncio
    async def test_sound_channel_event_not_blocked_by_visual_suppression(self) -> None:
        """[D8] 抑制窗只针对普通画面；无帧声音检查不受影响。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_visual_suppress_until = time.monotonic() + 5.0
        await self._call(orch, "声音", "听到两声咳嗽", "normal")
        orch._start_thinking.assert_awaited()

    @pytest.mark.asyncio
    async def test_video_check_sound_label_dropped_in_window(self) -> None:
        """[D8] 已知边界：带图检查返回声音标签时 kind=画面，窗口内一并丢弃。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_visual_suppress_until = time.monotonic() + 5.0
        await self._call(orch, "画面", "听到两声咳嗽", "normal", (_JPEG, 8, 8, 0, ""))
        orch._start_thinking.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_danger_preempts_non_danger_inflight_turn(self) -> None:
        """[P6] 危险 + 在飞非危险回合 → 抢占（_do_interrupt）+ 起危险回合。"""
        orch = _make_orch(avdetector_service=object())
        orch._do_interrupt = AsyncMock()
        inflight = MagicMock()
        inflight.done.return_value = False
        orch._pipeline_task = inflight
        orch._pipeline_danger = False
        await self._call(orch, "画面", "有人摔倒了", "danger",
                         (_JPEG, 640, 480, 90, "front"))
        orch._do_interrupt.assert_awaited_once_with("av_danger")
        orch._start_thinking.assert_awaited()
        assert orch._pipeline_danger is True
        assert orch._turn_user_record is not None
        assert orch._turn_user_record["content"].startswith("[环境事件]")

    @pytest.mark.asyncio
    async def test_preempt_window_slip_in_turn_cancelled_not_orphaned(self) -> None:
        """[P6.6] 抢占窗口竞态：`_do_interrupt` 的 await 期间 dispatch 受理了用户
        turn-end 并建了新 pipeline（模拟）——补刀必须取消它；否则下面的引用覆写
        让它成孤儿，与危险回合并发双 LLM/TTS、且无人能再取消。"""
        orch = _make_orch(avdetector_service=object())
        slipped: list[asyncio.Task] = []

        async def _interrupt_sim(_reason: str) -> None:
            async def _user_turn() -> None:
                await asyncio.sleep(10)

            t = asyncio.create_task(_user_turn())
            slipped.append(t)
            orch._pipeline_task = t  # 用户 turn-end 建轮（:1400 路径）
            orch._pipeline_danger = False
            await asyncio.sleep(0)  # interrupt 继续让出事件循环

        orch._do_interrupt = AsyncMock(side_effect=_interrupt_sim)
        inflight = MagicMock()
        inflight.done.return_value = False
        orch._pipeline_task = inflight
        orch._pipeline_danger = False

        await self._call(orch, "画面", "有人摔倒了", "danger",
                         (_JPEG, 640, 480, 90, "front"))

        assert slipped[0].cancelled()                 # 插队回合已被补刀取消
        assert orch._pipeline_task is not slipped[0]  # 引用未被孤儿覆写
        assert orch._pipeline_danger is True          # 在飞 = 危险回合
        orch._start_thinking.assert_awaited()

    @pytest.mark.asyncio
    async def test_spawn_pipeline_sync_cancels_slipped_task(self) -> None:
        """[P6.8] 换装原语：覆写引用前同步取消在飞插队者（不产生孤儿）。"""
        orch = _make_orch(avdetector_service=object())
        orch._start_thinking = AsyncMock()

        async def _slipped() -> None:
            await asyncio.sleep(10)

        old = asyncio.create_task(_slipped())
        orch._pipeline_task = old
        orch._spawn_pipeline(orch._start_thinking(), danger=True)
        await asyncio.sleep(0)  # cancel 于下一 await 点生效
        assert old.cancelled()
        assert orch._pipeline_task is not old
        assert orch._pipeline_danger is True

    @pytest.mark.asyncio
    async def test_user_turn_slip_in_during_interrupt_cancelled(self) -> None:
        """[P6.8] 反向插队（turn-end 侧）：barge-in 回合 `_do_interrupt("user_voice")`
        窗口内 av 事件装了 pipeline——被覆写前必须取消，防危险回合成孤儿。"""
        orch = _make_orch(avdetector_service=object())
        orch._start_thinking = AsyncMock()
        orch._ctx.sm.state = SessionState.SPEAKING  # barge-in 回合

        slipped: list[asyncio.Task] = []

        async def _interrupt_sim(_reason: str) -> None:
            async def _av_turn() -> None:
                await asyncio.sleep(10)

            t = asyncio.create_task(_av_turn())
            slipped.append(t)
            orch._pipeline_task = t  # av 危险事件在 interrupt 窗口内装轮
            orch._pipeline_danger = True
            await asyncio.sleep(0)

        orch._do_interrupt = AsyncMock(side_effect=_interrupt_sim)
        await _audio_turn(orch, final_text="你好呀今天", vad="speech")

        assert slipped[0].cancelled()
        assert orch._pipeline_task is not slipped[0]
        assert orch._pipeline_danger is False  # 在飞 = 用户标准回合
        orch._start_thinking.assert_awaited()

    @pytest.mark.asyncio
    async def test_v3_text_slip_in_during_interrupt_cancelled(self) -> None:
        """[P6.8] 反向插队（v3 文本侧）：条件中断窗口内 av 事件装了 pipeline——
        换装原语同步取消它。"""
        orch = _make_orch(avdetector_service=object())
        orch._start_thinking = AsyncMock()
        orch._ctx.sm.state = SessionState.SPEAKING  # 触发条件中断
        orch._ctx.latest_video_frames.append((1, _JPEG, 0, 640, 480, 90, "front"))

        slipped: list[asyncio.Task] = []

        async def _interrupt_sim(_reason: str) -> None:
            async def _av_turn() -> None:
                await asyncio.sleep(10)

            t = asyncio.create_task(_av_turn())
            slipped.append(t)
            orch._pipeline_task = t
            orch._pipeline_danger = True
            await asyncio.sleep(0)

        orch._do_interrupt = AsyncMock(side_effect=_interrupt_sim)
        await orch._handle_video(_video_msg(text="描述图片内容"))
        await asyncio.sleep(0.01)

        assert slipped[0].cancelled()
        assert orch._pipeline_task is not slipped[0]
        assert orch._pipeline_danger is False
        orch._start_thinking.assert_awaited()

    @pytest.mark.asyncio
    async def test_danger_dropped_when_danger_inflight(self) -> None:
        """[P6] 危险 + 在飞也是危险回合 → 丢弃 + log，不抢占、零落库（等下一轮检测）。"""
        orch = _make_orch(avdetector_service=object())
        orch._do_interrupt = AsyncMock()
        inflight = MagicMock()
        inflight.done.return_value = False
        orch._pipeline_task = inflight
        orch._pipeline_danger = True
        await self._call(orch, "画面", "又有人摔倒", "danger",
                         (_JPEG, 640, 480, 90, "front"))
        orch._do_interrupt.assert_not_awaited()
        orch._start_thinking.assert_not_awaited()
        assert orch._chat_store.messages == []

    @pytest.mark.asyncio
    async def test_danger_idle_still_subject_to_race_recheck(self) -> None:
        """[P6] 危险 + 无在飞但用户说话中（像话的 interim，[P6.13] 成句级 ≥5 种字符）→
        丢弃（不插话）。"""
        orch = _make_orch(avdetector_service=object())
        orch._asr_interim_pending = True
        orch._last_asr_interim_text = "我今天有点困"
        await self._call(orch, "画面", "有人摔倒了", "danger",
                         (_JPEG, 640, 480, 90, "front"))
        orch._start_thinking.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_danger_flag_bookkeeping(self) -> None:
        """[P6] 标记维护：声音起轮 False、危险起轮 True、标准回合起轮复位 False。"""
        orch = _make_orch(avdetector_service=object())
        await self._call(orch, "声音", "听到咳嗽", "normal")
        assert orch._pipeline_danger is False

        orch2 = _make_orch(avdetector_service=object())
        await self._call(orch2, "画面", "有人摔倒了", "danger", (_JPEG, 8, 8, 0, ""))
        assert orch2._pipeline_danger is True

        orch3 = _make_orch(avdetector_service=object())
        orch3._av_monitor = _fake_monitor()
        orch3._pipeline_danger = True  # 假装上一个危险回合
        await _audio_turn(orch3, final_text="你好呀今天", vad="speech")
        assert orch3._pipeline_danger is False

    @pytest.mark.asyncio
    async def test_race_recheck_blocks_on_speech_like_interim(self) -> None:
        """触发后新 interim 且文本像话（[P6.13] 成句级 ≥5 种字符）→ 拦截
        （用户真开口，让位标准流程）。"""
        orch = _make_orch(avdetector_service=object())
        orch._asr_interim_pending = True  # 触发受理时为假 → 属"触发后新转真"
        orch._last_asr_interim_text = "我今天有点困"
        await self._call(orch, "声音", "听到咳嗽", "normal")
        orch._start_thinking.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_race_recheck_onomatopoeia_interim_not_block(self) -> None:
        """[P6.3] 触发后新 interim 但文本是拟声（'咳'，1 种字符）→ 不拦——
        连续咳嗽的声学痕迹（VAD 帧 / 拟声 interim）不再自杀式拦截。"""
        orch = _make_orch(avdetector_service=object())
        orch._asr_interim_pending = True
        orch._last_asr_interim_text = "咳"
        await self._call(orch, "声音", "听到咳嗽", "normal")
        orch._start_thinking.assert_awaited()

    @pytest.mark.asyncio
    async def test_race_recheck_short_interim_not_block(self) -> None:
        """[P6.13] 触发后新 interim 但仅 2–4 种字符（'好的'）→ 不拦——
        阈值 ≥5 起，短转写不再构成"在说话"证据（接受退化：短句期间事件照投递）。"""
        orch = _make_orch(avdetector_service=object())
        orch._asr_interim_pending = True
        orch._last_asr_interim_text = "好的"
        await self._call(orch, "声音", "听到咳嗽", "normal")
        orch._start_thinking.assert_awaited()

    @pytest.mark.asyncio
    async def test_stale_interim_pending_before_trigger_not_block(self) -> None:
        """[P6.2] 触发受理时已挂起的 interim（多为事件自身拟声转写，如咳嗽 '咳'）不算
        新 ASR 活动 → 不拦（否则转写否决修好也白修；真实语音窗口由否决兜底）。"""
        orch = _make_orch(avdetector_service=object())
        orch._asr_interim_pending = True
        orch._av_trigger_interim_pending = True  # 触发受理时即已挂起
        await self._call(orch, "声音", "听到咳嗽", "normal")
        orch._start_thinking.assert_awaited()

    @pytest.mark.asyncio
    async def test_race_recheck_blocks_on_late_final(self) -> None:
        """触发后 ASR 迟到 meaningful final（竞态窗口）→ 丢弃。"""
        orch = _make_orch(avdetector_service=object())
        orch._asr_final_sent = True
        await self._call(orch, "声音", "听到咳嗽", "normal")
        orch._start_thinking.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_race_recheck_frames_only_not_block(self) -> None:
        """[P6.3] 去帧化：仅出现新 VAD speech 帧（无像话 interim / final）→ 不拦。"""
        orch = _make_orch(avdetector_service=object())
        await self._call(orch, "声音", "听到连续咳嗽", "normal")
        orch._start_thinking.assert_awaited()


    @pytest.mark.asyncio
    async def test_gates_mode_state_pipeline(self) -> None:
        orch = _make_orch(avdetector_service=object(), pipeline_mode="translate")
        await self._call(orch, "声音", "听到咳嗽", "normal")
        orch._start_thinking.assert_not_awaited()

        orch = _make_orch(avdetector_service=object())
        orch._ctx.sm.state = SessionState.SPEAKING
        await self._call(orch, "声音", "听到咳嗽", "normal")
        orch._start_thinking.assert_not_awaited()

        orch = _make_orch(avdetector_service=object())
        inflight = MagicMock()
        inflight.done.return_value = False
        orch._pipeline_task = inflight
        await self._call(orch, "声音", "听到咳嗽", "normal")
        orch._start_thinking.assert_not_awaited()


class TestVisualTurnArming:
    def test_arm_sets_deadline_and_forces_check(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        orch._arm_av_visual_suppression()
        assert orch._av_visual_suppress_until > time.monotonic()
        orch._av_monitor.request_video_check.assert_called_once_with(force=True)

    def test_arm_disabled_by_zero(self) -> None:
        orch = _make_orch(avdetector_service=object(), visual_turn_suppress_s=0.0)
        orch._av_monitor = _fake_monitor()
        orch._arm_av_visual_suppression()
        assert orch._av_visual_suppress_until == 0.0
        orch._av_monitor.request_video_check.assert_not_called()

    def test_arm_without_monitor_still_sets_deadline(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = None
        orch._arm_av_visual_suppression()
        assert orch._av_visual_suppress_until > time.monotonic()

    def test_arm_handles_monitor_not_ready(self) -> None:
        orch = _make_orch(avdetector_service=object())
        monitor = _fake_monitor(ready=False)
        monitor.request_video_check.return_value = False
        orch._av_monitor = monitor
        orch._arm_av_visual_suppression()
        assert orch._av_visual_suppress_until > time.monotonic()
        monitor.request_video_check.assert_called_once_with(force=True)


class TestStartThinkingEventNote:
    @pytest.mark.asyncio
    async def test_event_note_merged_into_system_no_user_rows(self) -> None:
        orch = _make_orch()
        llm = _FakeLlm(tokens=["你好呀"])
        orch._llm = llm
        orch._send_ai_text = AsyncMock()
        orch._send_status = AsyncMock()
        note = "【环境事件·不是用户发言】刚才检测到：听到两声咳嗽（来源：声音）。"
        await orch._start_thinking("", image_frames=None, turn_token="", event_note=note)

        msgs = llm.captured_messages
        assert msgs is not None and msgs[0].role == "system"
        assert msgs[0].content.startswith("You are a companion.")
        assert note in msgs[0].content
        # 事件不是用户发言：无 user 行；assistant 行照常
        kinds = [(m["role"], m["kind"]) for m in orch._chat_store.messages]
        assert ("user", "asr") not in kinds
        assert any(m["role"] == "assistant" and m["kind"] == "llm" for m in orch._chat_store.messages)


class TestLifecycleAndDisabled:
    @pytest.mark.asyncio
    async def test_mode_change_closes_and_recreates(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._close_av_monitor = AsyncMock()
        orch._ensure_av_monitor = MagicMock()
        orch._send_status = AsyncMock()

        await orch._handle_session_change(_change_msg("transcribe"))
        orch._close_av_monitor.assert_awaited()
        orch._ensure_av_monitor.assert_not_called()

        orch._close_av_monitor.reset_mock()
        await orch._handle_session_change(_change_msg("chat"))
        orch._ensure_av_monitor.assert_called_once()

    @pytest.mark.asyncio
    async def test_mode_change_closes_monitor_before_entering_omni(self) -> None:
        """[P6.5] 切出 chat 必须先关事件源——`_enter_omni_mode` 开流秒级期间
        `_pipeline_mode` 仍 "full"，存活 monitor 的事件会在切换窗口内起 chat 回合。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        order: list[str] = []

        async def _close() -> None:
            order.append("close")

        async def _enter() -> bool:
            order.append("enter")
            return True

        orch._close_av_monitor = AsyncMock(side_effect=_close)
        orch._enter_omni_mode = AsyncMock(side_effect=_enter)
        orch._send_status = AsyncMock()

        await orch._handle_session_change(_change_msg("omni"))
        assert order == ["close", "enter"]

    @pytest.mark.asyncio
    async def test_omni_enter_failure_reensures_monitor(self) -> None:
        """[P6.5] 切 omni 失败（1009 回滚，App 留在 chat）：先关的事件源要恢复，
        否则 chat 下检测静默死亡；原本无 monitor（未启用）则不做多余重建。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        orch._close_av_monitor = AsyncMock()
        orch._ensure_av_monitor = MagicMock()
        orch._enter_omni_mode = AsyncMock(return_value=False)
        orch._send_status = AsyncMock()

        await orch._handle_session_change(_change_msg("omni"))
        orch._close_av_monitor.assert_awaited()
        orch._ensure_av_monitor.assert_called_once()

        # 无 monitor（服务未启用）→ 失败回滚不凭空重建
        orch._av_monitor = None
        orch._ensure_av_monitor.reset_mock()
        await orch._handle_session_change(_change_msg("omni"))
        orch._ensure_av_monitor.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_end_closes_monitor_before_teardown(self) -> None:
        """[P6.5] session.end 先关事件源再做 teardown 长 await——否则窗口内事件
        会在 `_cancel_pipeline` 之后起一条无人取消的新 pipeline。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        order: list[str] = []

        async def _close() -> None:
            order.append("close")

        async def _cancel() -> None:
            order.append("cancel")

        orch._close_av_monitor = AsyncMock(side_effect=_close)
        orch._cancel_pipeline = AsyncMock(side_effect=_cancel)
        orch._session_mgr = MagicMock()
        orch._session_mgr.end = AsyncMock()
        end = MagicMock()
        end.payload.reason = "user"

        await orch._handle_session_end(end)
        assert order == ["close", "cancel"]

    @pytest.mark.asyncio
    async def test_session_start_closes_monitor_first(self) -> None:
        """[P6.5] 同连接重发 session.start：先关上一会话残留 monitor，再做
        session_mgr.start 长 await——否则旧事件源会在新会话边界起 stale 回合。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_monitor = _fake_monitor()
        order: list[str] = []

        async def _close() -> None:
            order.append("close")

        async def _mgr_start(**_kwargs):
            order.append("mgr_start")
            return orch._ctx, orch._history

        orch._close_av_monitor = AsyncMock(side_effect=_close)
        orch._session_mgr = MagicMock()
        orch._session_mgr.start = AsyncMock(side_effect=_mgr_start)
        orch._ensure_av_monitor = MagicMock()
        orch._send_status = AsyncMock()

        await orch._handle_session_start(_start_msg())
        assert order == ["close", "mgr_start"]

    @pytest.mark.asyncio
    async def test_session_start_cancels_inflight_pipeline(self) -> None:
        """[P6.12] 同连接重发 session.start：在飞 pipeline 必须取消——
        旧任务对 self._ctx/_history 是实时引用，残余会泄进新会话（epoch 兜底
        不完全：新旧 ctx 的 epoch 常同为 0）。"""
        orch = _make_orch(avdetector_service=object())
        orch._session_mgr = MagicMock()

        async def _mgr_start(**_kwargs):
            return orch._ctx, orch._history

        orch._session_mgr.start = AsyncMock(side_effect=_mgr_start)
        orch._close_av_monitor = AsyncMock()
        orch._ensure_av_monitor = MagicMock()
        orch._send_status = AsyncMock()

        async def _old_turn() -> None:
            await asyncio.sleep(10)

        old = asyncio.create_task(_old_turn())
        orch._pipeline_task = old

        await orch._handle_session_start(_start_msg())
        assert old.cancelled()
        assert orch._pipeline_task is None

    @pytest.mark.asyncio
    async def test_session_start_resets_visual_suppression(self) -> None:
        """[D8] 新会话复位抑制窗，避免跨会话残留。"""
        orch = _make_orch(avdetector_service=object())
        orch._av_visual_suppress_until = time.monotonic() + 5.0
        orch._session_mgr = MagicMock()

        async def _mgr_start(**_kwargs):
            return orch._ctx, orch._history

        orch._session_mgr.start = AsyncMock(side_effect=_mgr_start)
        orch._ensure_av_monitor = MagicMock()
        orch._send_status = AsyncMock()

        await orch._handle_session_start(_start_msg())
        assert orch._av_visual_suppress_until == 0.0

    @pytest.mark.asyncio
    async def test_session_end_closes_monitor(self) -> None:
        orch = _make_orch(avdetector_service=object())
        monitor = _fake_monitor()
        orch._av_monitor = monitor
        orch._session_mgr = MagicMock()
        orch._session_mgr.end = AsyncMock()
        end = MagicMock()
        end.payload.reason = "user"
        await orch._handle_session_end(end)
        monitor.close.assert_awaited()
        assert orch._av_monitor is None

    @pytest.mark.asyncio
    async def test_disabled_service_zero_behavior(self) -> None:
        """关闭态（service=None）：monitor 不建、音频/视频路径零额外调用（与现状一致）。"""
        orch = _make_orch(avdetector_service=None)
        orch._ensure_av_monitor()
        assert orch._av_monitor is None
        await _audio_turn(orch, final_text="", vad="speech")  # 不抛错、零 avdetector
        await orch._handle_video(_video_msg(text=None))
        assert orch._av_monitor is None


class TestMonitorPromptLanguage:
    @pytest.mark.parametrize("language,expect_clause", [
        ("zh-Hans", False),
        ("en", True),
        ("zh-Hant", True),
        ("", True),
    ])
    def test_monitor_prompt_gets_language_clause(self, language: str,
                                                 expect_clause: bool) -> None:
        orch = _make_orch(avdetector_service=object(), language=language)
        orch._avdetector_system_prompt = "BASE PROMPT"
        with patch("session.av_detector.AvDetectorMonitor") as monitor_cls:
            orch._ensure_av_monitor()
            prompt = monitor_cls.call_args.kwargs["system_prompt"]
        if expect_clause:
            expected = ("Traditional Chinese (zh-Hant)"
                        if language == "zh-Hant" else "English (en)")
            assert prompt.startswith("BASE PROMPT\n\n")
            assert expected in prompt
        else:
            assert prompt == "BASE PROMPT"


class TestSessionConfigSwitch:
    @pytest.mark.asyncio
    async def test_config_disable_closes_monitor_and_echoes(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._ctx.avdetector_enabled = True
        orch._close_av_monitor = AsyncMock()
        orch._ensure_av_monitor = MagicMock()

        await orch._handle_session_change(_config_msg("disable"))

        assert orch._ctx.avdetector_enabled is False
        orch._close_av_monitor.assert_awaited_once()
        orch._ensure_av_monitor.assert_not_called()
        coding = orch._codec.make_session_changed.call_args.kwargs
        assert coding == {"mode": "config", "avdetector": "disable"}

    @pytest.mark.asyncio
    async def test_config_enable_ensures_monitor_and_echoes(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._ctx.avdetector_enabled = False
        orch._close_av_monitor = AsyncMock()
        orch._ensure_av_monitor = MagicMock()

        await orch._handle_session_change(_config_msg("enable"))

        assert orch._ctx.avdetector_enabled is True
        orch._ensure_av_monitor.assert_called_once()
        orch._close_av_monitor.assert_not_awaited()
        assert orch._codec.make_session_changed.call_args.kwargs == {
            "mode": "config", "avdetector": "enable",
        }

    @pytest.mark.asyncio
    async def test_config_missing_or_invalid_uses_1011_not_1009(self) -> None:
        for value in (None, "ENABLE"):
            orch = _make_orch(avdetector_service=object())
            orch._close_av_monitor = AsyncMock()
            orch._ensure_av_monitor = MagicMock()
            await orch._handle_session_change(_config_msg(value))
            kwargs = orch._codec.make_error.call_args.kwargs
            assert kwargs["code"] == ErrorCode.SESSION_CONFIG_FAILED
            orch._codec.make_session_changed.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_before_session_start_uses_1011(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._ctx = None
        await orch._handle_session_change(_config_msg("disable"))
        assert orch._codec.make_error.call_args.kwargs["code"] == ErrorCode.SESSION_CONFIG_FAILED

    def test_ensure_monitor_noop_outside_chat(self) -> None:
        orch = _make_orch(avdetector_service=object(), pipeline_mode="translate")
        orch._avdetector_system_prompt = "BASE"
        with patch("session.av_detector.AvDetectorMonitor") as monitor_cls:
            orch._ensure_av_monitor()
        assert orch._av_monitor is None
        monitor_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_start_passes_avdetector_disable(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._session_mgr = MagicMock()
        orch._session_mgr.start = AsyncMock(return_value=(orch._ctx, orch._history))
        orch._close_av_monitor = AsyncMock()
        orch._cancel_pipeline = AsyncMock()
        orch._ensure_av_monitor = MagicMock()
        orch._send_status = AsyncMock()
        cfg = ClientSessionStartConfig(
            enable_video=True, audio_sample_rate=16000,
            vad_sensitivity="medium", client_version="android-1.0.0.1",
            avdetector=AvDetectorSwitch.DISABLE,
        )
        await orch._handle_session_start(_start_msg_with_config(cfg))
        assert orch._session_mgr.start.await_args.kwargs["avdetector_enabled"] is False

    @pytest.mark.asyncio
    async def test_session_start_omitted_avdetector_defaults_enable(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._session_mgr = MagicMock()
        orch._session_mgr.start = AsyncMock(return_value=(orch._ctx, orch._history))
        orch._close_av_monitor = AsyncMock()
        orch._cancel_pipeline = AsyncMock()
        orch._ensure_av_monitor = MagicMock()
        orch._send_status = AsyncMock()
        cfg = ClientSessionStartConfig(
            enable_video=True, audio_sample_rate=16000,
            vad_sensitivity="medium", client_version="android-1.0.0.1",
        )
        await orch._handle_session_start(_start_msg_with_config(cfg))
        assert orch._session_mgr.start.await_args.kwargs["avdetector_enabled"] is True

    @pytest.mark.asyncio
    async def test_session_start_explicit_null_is_1006_and_no_teardown(self) -> None:
        orch = _make_orch(avdetector_service=object())
        orch._session_mgr = MagicMock()
        orch._session_mgr.start = AsyncMock(return_value=(orch._ctx, orch._history))
        orch._close_av_monitor = AsyncMock()
        orch._cancel_pipeline = AsyncMock()
        cfg = ClientSessionStartConfig(
            enable_video=True, audio_sample_rate=16000,
            vad_sensitivity="medium", client_version="android-1.0.0.1",
            avdetector=None,
        )
        await orch._handle_session_start(_start_msg_with_config(cfg))
        assert orch._codec.make_error.call_args.kwargs["code"] == ErrorCode.MALFORMED_JSON
        orch._session_mgr.start.assert_not_awaited()
        orch._close_av_monitor.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dropped_event_has_zero_history_chat_recording(self) -> None:
        orch = _make_orch(avdetector_service=object())
        sink = _FakeSink()
        orch._recording_sink = sink
        orch._last_av_proactive = time.monotonic()
        orch._start_thinking = AsyncMock()
        await orch._on_av_event("声音", "听到咳嗽", "normal")
        assert orch._history is not None and orch._history.is_empty
        assert orch._chat_store.messages == []
        assert sink.chat_texts == []
        assert orch._turn_user_record is None
        orch._start_thinking.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_monitor_generation_dropped_zero_writes(self) -> None:
        orch = _make_orch(avdetector_service=object())
        sink = _FakeSink()
        orch._recording_sink = sink
        orch._start_thinking = AsyncMock()
        await orch._on_av_event(
            "声音", "听到咳嗽", "normal",
            generation=orch._av_monitor_generation + 1,
        )
        assert orch._history is not None and orch._history.is_empty
        assert orch._chat_store.messages == []
        assert sink.chat_texts == []
        assert orch._turn_user_record is None
        orch._start_thinking.assert_not_awaited()
