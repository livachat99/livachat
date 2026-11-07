"""Unit tests for AF 快照/暂存/多 final（docs/fd_pipeline_llm_v2.md）。

Locks the semantics:
- _snapshot_turn_images：唯一取图点，取后 buffer 清空；enable_video=false/空 → []；
  7-tuple → ChatImageFrame（丢 frame_id/ts）；
- _send_transcription：仅 is_final 分支透传 frames；
- 路径 B（poll 任务）多 final：每条 final 独立快照、互不重叠；暂存覆盖式写入
  （latest wins）；路径 A 下一音频帧消费暂存（跨任务闭环），参数为同步拷贝。
"""

from __future__ import annotations

import base64
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recording.sink import ChatImageFrame
from session.context import SessionContext
from session.history import WorkingMemory
from session.orchestrator import DialogOrchestrator
from session.state_machine import SessionState, SessionStateMachine
from tests.unit.session.conftest import FakeChatStore

_TID = "trans_abcd12345678"


def _frame(frame_id: int, data: bytes) -> tuple:
    return (frame_id, data, 1000, 640, 480, 90, "front")


def _cif(data: bytes) -> ChatImageFrame:
    return ChatImageFrame(data=data, width=640, height=480, orientation=90, camera_facing="front")


def _data_url(data: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}"


class _FakeSink:
    def __init__(self):
        self.chat_texts: list[dict] = []

    def on_chat_text(self, **kwargs) -> None:
        self.chat_texts.append(kwargs)

    def on_transcribe_text(self, **kwargs) -> None:
        pass


def _handler_mock() -> MagicMock:
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _make_orch(*, enable_video: bool = True, frames: list | None = None):
    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    ctx = SessionContext(
        session_id="test-sid", device_id="dev-1", user_id="user-1",
        sm=sm, enable_video=enable_video, audio_sample_rate=16000,
        latest_video_frames=deque(frames or []),
    )
    sink = _FakeSink()
    orch = DialogOrchestrator(
        handler=_handler_mock(), codec=MagicMock(), session_mgr=None,
        asr_service=None, llm_service=None, tts_service=None,
        opus_encoder=None, opus_decoder=None,
        system_prompt="You are a companion.", generation_opts={},
        chat_store=FakeChatStore(), pipeline_mode="full", recording_sink=sink,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._turn_detector = MagicMock()
    orch._interrupt = MagicMock()
    orch._interrupt.should_yield.return_value = True
    return orch, sink


def _user_rows(sink: _FakeSink) -> list[dict]:
    return [c for c in sink.chat_texts if c["role"] == "user"]


def _result(text: str):
    r = MagicMock()
    r.is_final = True
    r.text = text
    r.language = ""
    return r


# ---------------------------------------------------------------------------
# _snapshot_turn_images
# ---------------------------------------------------------------------------


class TestSnapshotTurnImages:
    def test_snapshot_clears_buffer_and_maps_fields(self):
        orch, _ = _make_orch(frames=[_frame(7, b"\xff\xd8one")])

        frames = orch._snapshot_turn_images()

        assert frames == [_cif(b"\xff\xd8one")]  # frame_id/ts 丢弃
        assert len(orch._ctx.latest_video_frames) == 0  # 取后清空

    def test_snapshot_all_frames_before_clear(self):
        """K>1 天然兼容：一次取走 buffer 全部帧。"""
        orch, _ = _make_orch(frames=[_frame(1, b"a"), _frame(2, b"b")])
        frames = orch._snapshot_turn_images()
        assert [f.data for f in frames] == [b"a", b"b"]
        assert len(orch._ctx.latest_video_frames) == 0

    def test_video_disabled_returns_empty(self):
        orch, _ = _make_orch(enable_video=False, frames=[_frame(1, b"a")])
        assert orch._snapshot_turn_images() == []
        assert len(orch._ctx.latest_video_frames) == 1  # 不消费

    def test_empty_buffer_returns_empty(self):
        orch, _ = _make_orch(frames=[])
        assert orch._snapshot_turn_images() == []


# ---------------------------------------------------------------------------
# _send_transcription(+images)
# ---------------------------------------------------------------------------


class TestSendTranscriptionImages:
    @pytest.mark.asyncio
    async def test_final_passes_frames_to_tap(self):
        orch, sink = _make_orch()
        await orch._send_transcription("文本", is_final=True,
                                       transcription_id=_TID, images=[_cif(b"a")])
        assert _user_rows(sink)[-1]["frames"] == [_cif(b"a")]

    @pytest.mark.asyncio
    async def test_interim_never_taps(self):
        """interim 不写 chatlog（现状），frames 参数无非 final 消费点。"""
        orch, sink = _make_orch()
        await orch._send_transcription("中间", is_final=False,
                                       transcription_id=_TID, images=[_cif(b"a")])
        assert sink.chat_texts == []

    @pytest.mark.asyncio
    async def test_final_without_frames_taps_none(self):
        orch, sink = _make_orch()
        await orch._send_transcription("文本", is_final=True, transcription_id=_TID)
        assert _user_rows(sink)[-1]["frames"] is None


# ---------------------------------------------------------------------------
# 路径 B：多 final + 暂存跨任务闭环
# ---------------------------------------------------------------------------


async def _drive_poll_finals(orch: DialogOrchestrator, finals: list[tuple[str, bytes | None]]) -> None:
    """驱动 _poll_asr_results：每条 final 处理前按需注入新帧（模拟两轮间到达）。"""
    orch._asr_stream = MagicMock()

    async def _gen():
        for text, inject in finals:
            if inject is not None:
                orch._ctx.latest_video_frames.append(_frame(99, inject))
            yield _result(text)

    orch._asr_stream.results = _gen
    orch._current_transcription_id = _TID
    await orch._poll_asr_results()


class TestPathBFinal:
    @pytest.mark.asyncio
    async def test_multi_final_each_row_gets_its_own_frames(self):
        """多 final：每条 final 行各带各时刻图、互不重叠；暂存 = 最新 final。"""
        orch, sink = _make_orch(frames=[_frame(1, b"\xff\xd8first")])
        await _drive_poll_finals(orch, [("第一句", None), ("第二句", b"\xff\xd8second")])

        rows = _user_rows(sink)
        assert [r["text"] for r in rows] == ["第一句", "第二句"]
        assert rows[0]["frames"] == [_cif(b"\xff\xd8first")]
        assert rows[1]["frames"] == [_cif(b"\xff\xd8second")]  # 与第一句互不重叠
        assert orch._ctx.latest_video_frames == deque()  # 每条 final 快照后清空
        assert orch._turn_image_frames == [_cif(b"\xff\xd8second")]  # latest wins

        entries = orch._history.get_recent()
        assert entries[0]["images"] == [_data_url(b"\xff\xd8first")]
        assert entries[1]["images"] == [_data_url(b"\xff\xd8second")]
        assert orch._chat_store.messages[0]["images"] == [_data_url(b"\xff\xd8first")]
        assert orch._chat_store.messages[1]["images"] == [_data_url(b"\xff\xd8second")]

    @pytest.mark.asyncio
    async def test_path_a_consumes_stash_for_thinking(self):
        """路径 B 写暂存 → 路径 A 下一音频帧消费 → 回图 = 最新 final 的图（闭环）。

        同一 final 双路径去重（§4.2/§4.3 门控 3）：只落一次图、行/images 只挂一次。
        """
        orch, sink = _make_orch(frames=[_frame(1, b"\xff\xd8b1")])
        await _drive_poll_finals(orch, [("问题", None)])
        assert orch._asr_final_sent is True

        captured: list = []
        thinking = AsyncMock()

        async def _capture(user_text, image_frames=None, turn_token=""):
            captured.append(image_frames)

        thinking.side_effect = _capture
        orch._start_thinking = thinking

        # 路径 A：同一 final 已由路径 B 处理 → 不重复写行/快照，仅消费暂存
        orch._asr_stream = MagicMock()
        orch._asr_stream.final_text = "问题"
        orch._asr_stream.reset = AsyncMock()
        orch._turn_detector.on_frame.return_value = True
        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
        msg.payload.vad_state.value = "speech"
        with patch("session.orchestrator.generate_transcription_id", return_value=_TID):
            await orch._handle_audio(msg)
        await orch._pipeline_task

        assert captured == [[_cif(b"\xff\xd8b1")]]
        assert orch._turn_image_frames == []  # 取走后清空

        # 同一 final 双路径：路径 A 不重复写行/挂图（§4.2 去重）
        assert len(sink.chat_texts) == 1
        assert sink.chat_texts[0]["frames"] == [_cif(b"\xff\xd8b1")]
        assert len(orch._history.get_recent()) == 1
        assert len(orch._chat_store.messages) == 1

    @pytest.mark.asyncio
    async def test_stash_overwrite_does_not_mutate_consumed_list(self):
        """barge-in：新一轮路径 B 覆盖式写暂存，不污染已消费任务的参数（同步拷贝）。"""
        orch, _ = _make_orch(frames=[_frame(1, b"\xff\xd8old")])
        await _drive_poll_finals(orch, [("旧轮", None)])

        captured: list = []
        thinking = AsyncMock()

        async def _capture(user_text, image_frames=None, turn_token=""):
            captured.append(image_frames)

        thinking.side_effect = _capture
        orch._start_thinking = thinking

        # 路径 A 消费旧轮暂存
        orch._asr_stream = MagicMock()
        orch._asr_stream.final_text = "旧轮"
        orch._asr_stream.reset = AsyncMock()
        orch._turn_detector.on_frame.return_value = True
        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
        msg.payload.vad_state.value = "speech"
        with patch("session.orchestrator.generate_transcription_id", return_value=_TID):
            await orch._handle_audio(msg)
        await orch._pipeline_task

        # 新一轮路径 B 写新暂存
        orch._ctx.latest_video_frames.append(_frame(2, b"\xff\xd8new"))
        await _drive_poll_finals(orch, [("新轮", None)])

        assert captured[0] == [_cif(b"\xff\xd8old")]  # 旧任务参数未被覆盖
        assert orch._turn_image_frames == [_cif(b"\xff\xd8new")]

    @pytest.mark.asyncio
    async def test_session_start_resets_stale_af_state(self):
        """gateway 允许同连接再发 session.start（不重连）→ 新会话不得继承 per-turn
        AF 状态：否则 `_asr_final_sent` 吞掉首句的转录/落行、`_turn_image_frames`
        把上一会话的帧挂到新会话回复上。"""
        from livachat_protocol.generated.client_message import ClientSessionStart
        from livachat_protocol.generated.protocol_models import (
            ClientSessionStartConfig,
            ClientSessionStartPayload,
        )

        from session.manager import SessionManager

        orch = DialogOrchestrator(
            handler=_handler_mock(), codec=MagicMock(),
            session_mgr=SessionManager(), asr_service=None, llm_service=None,
            tts_service=None, opus_encoder=None, opus_decoder=None,
            system_prompt="", generation_opts={},
            chat_store=FakeChatStore(), pipeline_mode="full",
        )
        orch._asr_final_sent = True
        orch._turn_image_frames = [_cif(b"\xff\xd8stale")]
        orch._current_transcription_id = _TID
        orch._barge_in_turn_started = True

        await orch._handle_session_start(ClientSessionStart(
            seq=1, timestamp=1000,
            payload=ClientSessionStartPayload(
                session_id=None,
                config=ClientSessionStartConfig(
                    enable_video=True, audio_sample_rate=24000,
                    vad_sensitivity="medium", client_version="android-1.0.0",
                ),
            ),
        ))

        assert orch._asr_final_sent is False
        assert orch._turn_image_frames == []
        assert orch._current_transcription_id == ""
        assert orch._barge_in_turn_started is False
