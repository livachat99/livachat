"""Unit tests for chatlog 携带 image（docs/fd_pipeline_llm_v2.md §9）。

Locks the semantics:
- 发言即入：图在 ASR final（AF）分发处理时刻快照并三写（chatlog tap / WorkingMemory /
  SQLite chat_store），与请求成败解耦——失败轮图仍入历史；
- 请求图由 history 条目携带：_start_thinking 组装按 §9.4 裁剪——仅最新
  _HISTORY_IMAGE_LIMIT 张进请求，其余条目不组装（history 本身全量保留）；
- 回图（final ai_text 带图，F5）：仅成功路径发送，载荷来自 AF 快照帧；
- 门控（§9.2）：translate / transcribe / asr_only / filler / enable_video=false 不挂图。
"""

from __future__ import annotations

import base64
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recording.sink import ChatImageFrame
from session.context import SessionContext
from session.history import WorkingMemory
from session.orchestrator import DialogOrchestrator, _select_recent_images
from session.state_machine import SessionState, SessionStateMachine
from shared.utils.errors import ErrorCode, ProviderError
from tests.unit.session.conftest import FakeChatStore

_JPEG = b"\xff\xd8fakejpeg"
_TID = "trans_abcd12345678"


def _frame(frame_id: int = 1, data: bytes = _JPEG) -> tuple:
    return (frame_id, data, 1000, 640, 480, 90, "front")


def _chat_image_frame(data: bytes = _JPEG) -> ChatImageFrame:
    return ChatImageFrame(data=data, width=640, height=480, orientation=90, camera_facing="front")


def _data_url(data: bytes = _JPEG) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}"


class _FakeSink:
    """RecordingSink：记录 on_chat_text / on_transcribe_text 调用（不落盘）。"""

    def __init__(self):
        self.chat_texts: list[dict] = []
        self.transcribe_texts: list[dict] = []

    def on_chat_text(self, **kwargs) -> None:
        self.chat_texts.append(kwargs)

    def on_transcribe_text(self, **kwargs) -> None:
        self.transcribe_texts.append(kwargs)


class _RecordingLLM:
    """Records messages; yields canned tokens (never raises)."""

    def __init__(self, tokens: list[str] | None = None):
        self.tokens = tokens or ["Hello"]
        self.calls: list[list] = []

    async def stream(self, *, messages, **opts):
        self.calls.append(messages)

        async def _gen():
            for t in self.tokens:
                yield t

        return _gen()


class _FailingOpenLLM:
    """stream() 开流即失败（ProviderError）——请求未提交（对应 :1003-1013 早退）。"""

    async def stream(self, *, messages, **opts):
        raise ProviderError("all LLM providers failed", provider="llm", code=ErrorCode.LLM_UNAVAILABLE)


def _user_rows(sink: _FakeSink) -> list[dict]:
    """chatlog tap 的 user 行（assistant 行由 _start_thinking 另写，不参与挂图断言）。"""
    return [c for c in sink.chat_texts if c["role"] == "user"]


def _handler_mock() -> MagicMock:
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _make_orch(*, pipeline_mode: str = "full", enable_video: bool = True,
               frames: list | None = None):
    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    ctx = SessionContext(
        session_id="test-sid", device_id="dev-1", user_id="user-1",
        sm=sm, enable_video=enable_video, audio_sample_rate=16000,
        latest_video_frames=deque(frames or []),
    )
    sink = _FakeSink()
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
        recording_sink=sink,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._turn_detector = MagicMock()
    orch._interrupt = MagicMock()
    orch._interrupt.should_yield.return_value = True
    orch._llm = _RecordingLLM()
    return orch, sink


async def _drive_af_turn(orch: DialogOrchestrator, final_text: str) -> None:
    """路径 A（turn-end 分发）：绕过 ASR feed，_asr_stream 伪作 final 文本读取源。"""
    orch._asr_stream = MagicMock()
    orch._asr_stream.final_text = final_text
    orch._asr_stream.reset = AsyncMock()
    orch._turn_detector.on_frame.return_value = True

    msg = MagicMock()
    msg.type = "audio"
    msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
    msg.payload.vad_state.value = "speech"
    with patch("session.orchestrator.generate_transcription_id", return_value=_TID):
        await orch._handle_audio(msg)
    if orch._pipeline_task is not None:
        await orch._pipeline_task


# ---------------------------------------------------------------------------
# AF 挂图（路径 A）：三写一致 + buffer 清空
# ---------------------------------------------------------------------------


class TestAfAttach:
    @pytest.mark.asyncio
    async def test_af_turn_writes_images_to_history_and_store_and_tap(self):
        """成功轮：一条 user 行三处同刻落图，且三处同源（同一快照 bytes）。"""
        orch, sink = _make_orch(frames=[_frame()])

        await _drive_af_turn(orch, "看看这个东西")

        # buffer 已清空（快照副作用）
        assert len(orch._ctx.latest_video_frames) == 0

        # chatlog tap：frames 透传（bytes 一致，丢弃 frame_id/ts）
        rows = _user_rows(sink)
        assert len(rows) == 1
        tap = rows[0]
        assert tap["role"] == "user" and tap["text"] == "看看这个东西"
        assert tap["frames"] == [_chat_image_frame()]

        # WorkingMemory：user 条目带 data_url
        entries = orch._history.get_recent()
        assert entries[-2]["role"] == "user"
        assert entries[-2]["images"] == [_data_url()]

        # SQLite chat_store：INSERT 随行带图
        store_users = [m for m in orch._chat_store.messages if m["role"] == "user"]
        assert len(store_users) == 1
        assert store_users[0]["images"] == [_data_url()]

    @pytest.mark.asyncio
    async def test_request_carries_current_turn_image(self):
        """本轮图经 history 条目进入 LLM 请求（组装裁剪保留最新张数内，§9.4）。"""
        orch, _ = _make_orch(frames=[_frame()])
        llm = _RecordingLLM()
        orch._llm = llm

        await _drive_af_turn(orch, "看看这个东西")

        call = llm.calls[-1]
        user_msgs = [m for m in call if m.role == "user"]
        assert user_msgs[-1].content == "看看这个东西"
        assert user_msgs[-1].images == [_data_url()]

    @pytest.mark.asyncio
    async def test_request_assembles_only_latest_two_images(self):
        """3 轮带图 → 第 3 轮请求仅组装最新 2 张；更早条目文本保留、images 不进请求
        （fd_pipeline_llm_v2.md §9.4）；history 本身不裁剪。"""
        orch, _ = _make_orch(frames=[_frame(1, b"\xff\xd8first")])
        llm = _RecordingLLM()
        orch._llm = llm

        await _drive_af_turn(orch, "第一问")
        orch._ctx.latest_video_frames = deque([_frame(2, b"\xff\xd8second")])
        await _drive_af_turn(orch, "第二问")
        orch._ctx.latest_video_frames = deque([_frame(3, b"\xff\xd8third")])
        await _drive_af_turn(orch, "第三问")

        user_msgs = [m for m in llm.calls[-1] if m.role == "user"]
        assert [m.content for m in user_msgs] == ["第一问", "第二问", "第三问"]
        assert user_msgs[0].images is None  # 第 3 新及以前不组装
        assert user_msgs[1].images == [_data_url(b"\xff\xd8second")]
        assert user_msgs[2].images == [_data_url(b"\xff\xd8third")]

        # history 条目全量保留（三写/恢复语义不变）
        entries = [e for e in orch._history.get_recent() if e["role"] == "user"]
        assert all(e.get("images") for e in entries)

    @pytest.mark.asyncio
    async def test_provider_error_turn_still_has_images_in_history(self):
        """D1 反转：请求失败（开流 ProviderError）→ 图仍入 history / chat store。"""
        orch, sink = _make_orch(frames=[_frame()])
        orch._llm = _FailingOpenLLM()

        await _drive_af_turn(orch, "看看这个东西")

        entries = orch._history.get_recent()
        assert entries[-1]["role"] == "user"
        assert entries[-1]["images"] == [_data_url()]
        assert orch._chat_store.messages[-1]["images"] == [_data_url()]
        assert _user_rows(sink)[-1]["frames"] == [_chat_image_frame()]

    @pytest.mark.asyncio
    async def test_next_turn_replays_failed_turn_images(self):
        """失败轮的图仍在最近 2 张内 → 随后续轮请求重放（裁剪上限见 §9.4 用例）。"""
        orch, _ = _make_orch(frames=[_frame()])
        orch._llm = _FailingOpenLLM()
        await _drive_af_turn(orch, "第一问")

        orch._ctx.latest_video_frames = deque([_frame(2, b"\xff\xd8second")])
        llm = _RecordingLLM()
        orch._llm = llm
        await _drive_af_turn(orch, "第二问")

        user_msgs = [m for m in llm.calls[-1] if m.role == "user"]
        assert user_msgs[0].images == [_data_url()]  # 失败轮图仍在请求中
        assert user_msgs[-1].images == [_data_url(b"\xff\xd8second")]

    @pytest.mark.asyncio
    async def test_filler_turn_sends_text_without_images(self):
        """filler（< min chars）：不触发 thinking、不挂图；文本行照写（无 frames）。"""
        orch, sink = _make_orch(frames=[_frame()])

        orch._asr_stream = MagicMock()
        orch._asr_stream.final_text = "嗯"
        orch._asr_stream.reset = AsyncMock()
        orch._turn_detector.on_frame.return_value = True
        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
        msg.payload.vad_state.value = "speech"
        with patch("session.orchestrator.generate_transcription_id", return_value=_TID):
            await orch._handle_audio(msg)

        assert _user_rows(sink)[-1]["frames"] is None  # 文本行照写、无图
        assert len(orch._history) == 0  # filler 不进 history（现状）
        assert orch._pipeline_task is None  # 不触发 thinking
        assert len(orch._ctx.latest_video_frames) == 1  # 未消费（留给后续轮）


# ---------------------------------------------------------------------------
# 组装裁剪纯函数（§9.4）
# ---------------------------------------------------------------------------


class TestSelectRecentImages:
    def test_keeps_only_latest_two(self):
        entries = [
            {"role": "user", "content": "a", "images": ["A"]},
            {"role": "user", "content": "b", "images": ["B"]},
            {"role": "user", "content": "c", "images": ["C"]},
        ]
        assert _select_recent_images(entries) == [None, ["B"], ["C"]]

    def test_fewer_than_limit_keeps_all(self):
        entries = [
            {"role": "user", "content": "a", "images": ["A"]},
            {"role": "user", "content": "b"},
        ]
        assert _select_recent_images(entries) == [["A"], None]

    def test_text_only_entries_do_not_consume_budget(self):
        entries = [
            {"role": "user", "content": "a", "images": ["A"]},
            {"role": "assistant", "content": "x"},
            {"role": "user", "content": "b"},
            {"role": "user", "content": "c", "images": ["C"]},
        ]
        assert _select_recent_images(entries) == [["A"], None, None, ["C"]]

    def test_within_entry_takes_tail_frames(self):
        entries = [{"role": "user", "content": "a", "images": ["A1", "A2", "A3"]}]
        assert _select_recent_images(entries) == [["A2", "A3"]]

    def test_dirty_non_list_ignored(self):
        entries = [
            {"role": "user", "content": "a", "images": "not-a-list"},
            {"role": "user", "content": "b", "images": ["B"]},
        ]
        assert _select_recent_images(entries) == [None, ["B"]]

    def test_no_images_at_all(self):
        assert _select_recent_images([{"role": "user", "content": "a"}]) == [None]

    def test_result_not_aliasing_history_list(self):
        imgs = ["A"]
        out = _select_recent_images([{"role": "user", "content": "a", "images": imgs}])
        assert out[0] == ["A"] and out[0] is not imgs


# ---------------------------------------------------------------------------
# 门控（§9.2）：模式 / 无视频 / 空 buffer 不挂图
# ---------------------------------------------------------------------------


class TestAttachGating:
    @pytest.mark.asyncio
    async def test_translate_mode_never_attaches(self):
        orch, sink = _make_orch(pipeline_mode="translate", frames=[_frame()])

        await _drive_af_turn(orch, "你好")

        assert _user_rows(sink)[-1]["frames"] is None
        assert _user_rows(sink)[-1]["source"] == "translate"
        assert len(orch._history) == 0  # translate 用户文本不进 history（现状）
        assert orch._chat_store.messages == []

    @pytest.mark.asyncio
    async def test_transcribe_mode_never_attaches(self):
        orch, sink = _make_orch(pipeline_mode="transcribe", frames=[_frame()])

        await _drive_af_turn(orch, "现场内容")

        assert sink.chat_texts == []
        assert sink.transcribe_texts[-1]["text"] == "现场内容"
        assert len(orch._history) == 0
        assert orch._chat_store.messages == []

    @pytest.mark.asyncio
    async def test_asr_only_mode_never_attaches(self):
        orch, sink = _make_orch(pipeline_mode="asr_only", frames=[_frame()])

        await _drive_af_turn(orch, "旁白文本")

        assert _user_rows(sink)[-1]["frames"] is None
        assert "images" not in orch._history.get_recent()[-1]
        assert not orch._chat_store.messages[-1]["images"]  # 空 list ≡ 不挂（真 store 落 NULL）
        assert orch._pipeline_task is None

    @pytest.mark.asyncio
    async def test_video_disabled_never_attaches(self):
        orch, sink = _make_orch(enable_video=False, frames=[_frame()])

        await _drive_af_turn(orch, "问题")

        assert _user_rows(sink)[-1]["frames"] is None
        assert "images" not in orch._history.get_recent()[-1]

    @pytest.mark.asyncio
    async def test_empty_buffer_never_attaches(self):
        orch, sink = _make_orch(frames=[])

        await _drive_af_turn(orch, "问题")

        assert _user_rows(sink)[-1]["frames"] is None
        assert "images" not in orch._history.get_recent()[-1]


# ---------------------------------------------------------------------------
# 回图（final ai_text 带图，F5）
# ---------------------------------------------------------------------------


def _capture_ai_text(orch: DialogOrchestrator) -> AsyncMock:
    mock = AsyncMock()
    orch._send_ai_text = mock
    return mock


class TestFinalAiTextImage:
    @pytest.mark.asyncio
    async def test_final_image_payload_equals_af_frame(self):
        """成功路径：final ai_text 回图字节/元数据 == AF 快照帧。"""
        orch, _ = _make_orch()
        mock = _capture_ai_text(orch)

        await orch._start_thinking("问题", image_frames=[_chat_image_frame()])

        finals = [c for c in mock.call_args_list if c.kwargs.get("is_final")]
        assert len(finals) == 1
        image = finals[-1].kwargs["image"]
        assert image.data == base64.b64encode(_JPEG).decode()
        assert (image.width, image.height, image.orientation, image.camera_facing) \
            == (640, 480, 90, "front")

    @pytest.mark.asyncio
    async def test_no_frames_no_image_payload(self):
        orch, _ = _make_orch()
        mock = _capture_ai_text(orch)

        await orch._start_thinking("问题", image_frames=[])

        finals = [c for c in mock.call_args_list if c.kwargs.get("is_final")]
        assert finals and finals[-1].kwargs["image"] is None

    @pytest.mark.asyncio
    async def test_stream_open_failure_sends_error_final_with_image(self):
        """开流失败（coroutine 风格 service）走统一失败路径（fd_pipeline_llm_v2.md）：
        final ai_text 透出 error message（无 partial → 无 "\\n" 前缀）并携带本轮快照图。"""
        orch, _ = _make_orch()
        orch._llm = _FailingOpenLLM()
        mock = _capture_ai_text(orch)

        await orch._start_thinking("问题", image_frames=[_chat_image_frame()])

        finals = [c for c in mock.call_args_list if c.kwargs.get("is_final")]
        assert len(finals) == 1
        assert finals[-1].args[0] == "all LLM providers failed"  # 原样透出（未超 1k）
        assert finals[-1].kwargs["image"] is not None            # 带图（需求更新）
        assert [c for c in mock.call_args_list if not c.kwargs.get("is_final")] == []

    @pytest.mark.asyncio
    async def test_interrupt_before_final_sends_no_image(self):
        """流中被 barge-in 打断 → return，不发 final ai_text（F5）。"""
        orch, _ = _make_orch()
        orch._interrupt.should_yield.return_value = False
        mock = _capture_ai_text(orch)

        await orch._start_thinking("问题", image_frames=[_chat_image_frame()])

        assert [c for c in mock.call_args_list if c.kwargs.get("is_final")] == []


# ---------------------------------------------------------------------------
# SPEAKING 打断带新图（§5 风险 1：送 LLM 帧 == 落盘帧 == 回图帧）
# ---------------------------------------------------------------------------


class TestSpeakingBargeIn:
    @pytest.mark.asyncio
    async def test_barge_in_frame_bytes_consistent_across_request_disk_and_reply(self):
        orch, sink = _make_orch(frames=[_frame(1, b"\xff\xd8speaking")])
        orch._ctx.sm.transition(SessionState.THINKING)
        orch._ctx.sm.transition(SessionState.SPEAKING)
        orch._interrupt.interrupt = AsyncMock()
        llm = _RecordingLLM()
        orch._llm = llm
        mock_send = _capture_ai_text(orch)

        await _drive_af_turn(orch, "这是什么")

        # 快照消费 + buffer 清空
        assert len(orch._ctx.latest_video_frames) == 0
        # 三处同源：chatlog 落盘帧 / 请求图 / 回图帧，同一 bytes
        assert _user_rows(sink)[-1]["frames"] == [_chat_image_frame(b"\xff\xd8speaking")]
        user_msgs = [m for m in llm.calls[-1] if m.role == "user"]
        assert user_msgs[-1].images == [_data_url(b"\xff\xd8speaking")]
        finals = [c for c in mock_send.call_args_list if c.kwargs.get("is_final")]
        assert finals[-1].kwargs["image"].data == base64.b64encode(b"\xff\xd8speaking").decode()


# ---------------------------------------------------------------------------
# 组装期不再原地挂图（提交点机制已删除）
# ---------------------------------------------------------------------------


class TestAssemblyNoLongerMutatesHistory:
    @pytest.mark.asyncio
    async def test_thinking_does_not_write_images_into_history(self):
        """_start_thinking 全程不改 history：无图轮条目保持无图。"""
        orch, _ = _make_orch()
        orch._history.add_message("user", "问题")

        await orch._start_thinking("问题", image_frames=[_chat_image_frame()])

        entries = orch._history.get_recent()
        user_entry = next(e for e in entries if e["role"] == "user")
        assert "images" not in user_entry

    @pytest.mark.asyncio
    async def test_barge_in_new_entry_gets_no_images(self):
        """barge-in 期间新落的 user 条目不会被本轮图串挂（图属于 AF 时刻的条目）。"""
        orch, _ = _make_orch()
        orch._history.add_message("user", "第一问", images=[_data_url()])
        orch._llm = _RecordingLLM()
        await orch._start_thinking("第一问", image_frames=[_chat_image_frame()])
        orch._history.add_message("user", "打断新问")

        entries = orch._history.get_recent()
        first = next(e for e in entries if e["content"] == "第一问")
        barge = next(e for e in entries if e["content"] == "打断新问")
        assert first["images"] == [_data_url()]
        assert "images" not in barge
