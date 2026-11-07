"""带视频一问一答：落盘图 == 送 provider 的图 == 回图（fd_pipeline_llm_v2 integration）。

用真实 RecordingSession（tmp 落盘）作 sink 直构 DialogOrchestrator，驱动一轮 AF：
断言 history.jsonl user 行 images 路径可解析、文件 bytes == 请求 data_url 解码字节
== final ai_text 回图字节。
"""

from __future__ import annotations

import base64
import json
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from recording.session import RecordingSession
from session.context import SessionContext
from session.history import WorkingMemory
from session.orchestrator import DialogOrchestrator
from session.state_machine import SessionState, SessionStateMachine
from tests.integration.orchestrator.conftest import FakeChatStore

pytestmark = pytest.mark.integration

_JPEG = b"\xff\xd8integration-jpeg"
_TID = "trans_abcd12345678"


class _RecordingLLM:
    def __init__(self):
        self.calls: list[list] = []

    async def stream(self, *, messages, **opts):
        self.calls.append(messages)

        async def _gen():
            yield "我看到了"

        return _gen()


async def test_turn_image_bytes_consistent_across_disk_request_and_reply(tmp_path):
    base = tmp_path / "rec-1"
    rec = RecordingSession(
        recording_session_id="rec-1", device_uuid="dev-1",
        session_date="2026-09-11", storage_path=base, config={},
    )
    await rec.start()

    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    ctx = SessionContext(
        session_id="sess-1", device_id="dev-1", user_id="u1", sm=sm,
        enable_video=True, audio_sample_rate=16000,
        latest_video_frames=deque([(1, _JPEG, 1000, 640, 480, 90, "front")]),
    )
    handler = MagicMock()
    handler.enqueue_outbound = AsyncMock()
    handler._stopped = False
    handler.principal.device_id = "dev-1"
    handler.principal.user_id = "u1"

    llm = _RecordingLLM()
    store = FakeChatStore()
    orch = DialogOrchestrator(
        handler=handler, codec=MagicMock(), session_mgr=None,
        asr_service=None, llm_service=llm, tts_service=None,
        opus_encoder=None, opus_decoder=None,
        system_prompt="You are helpful.", generation_opts={},
        chat_store=store, pipeline_mode="full", recording_sink=rec,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._turn_detector = MagicMock()
    orch._turn_detector.on_frame.return_value = True
    orch._interrupt = MagicMock()
    orch._interrupt.should_yield.return_value = True

    sent_ai_text: list[tuple[bool, object]] = []
    real_send = orch._send_ai_text

    async def _capture(text, *, message_id, index, is_final, image=None):
        sent_ai_text.append((is_final, image))
        await real_send(text, message_id=message_id, index=index, is_final=is_final, image=image)

    orch._send_ai_text = _capture

    try:
        orch._asr_stream = MagicMock()
        orch._asr_stream.final_text = "看看这个"
        orch._asr_stream.reset = AsyncMock()
        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
        msg.payload.vad_state.value = "speech"
        with patch("session.orchestrator.generate_transcription_id", return_value=_TID):
            await orch._handle_audio(msg)
        await orch._pipeline_task
    finally:
        await rec.finalize()

    # 1) chatlog：user 行 images 路径可解析且文件 bytes 一致
    lines = (base / "chat" / "history.jsonl").read_text(encoding="utf-8").strip().splitlines()
    user_row = next(json.loads(line) for line in lines if json.loads(line)["role"] == "user")
    (rel,) = user_row["images"]
    assert rel.startswith("../image/") and rel.endswith(".jpg")
    assert ((base / "chat" / rel).resolve()).read_bytes() == _JPEG

    # 2) LLM 输入图 == 落盘图（history 条目天然携带）
    user_msgs = [m for m in llm.calls[-1] if m.role == "user"]
    assert base64.b64decode(user_msgs[-1].images[-1].split(",", 1)[1]) == _JPEG

    # 3) SQLite 随行图与 history 同源
    store_user = [m for m in store.messages if m["role"] == "user"]
    assert store_user[-1]["images"] == [f"data:image/jpeg;base64,{base64.b64encode(_JPEG).decode()}"]

    # 4) final ai_text 回图 == AF 快照帧
    finals = [img for is_final, img in sent_ai_text if is_final]
    assert len(finals) == 1
    assert base64.b64decode(finals[0].data) == _JPEG
    assert (finals[0].width, finals[0].height, finals[0].orientation, finals[0].camera_facing) \
        == (640, 480, 90, "front")


async def test_translate_turn_produces_no_images_anywhere(tmp_path):
    """回归：translate 轮不产生 image/ 新文件、行无 images、请求无图（§4.6 矩阵）。"""
    base = tmp_path / "rec-1"
    rec = RecordingSession(
        recording_session_id="rec-1", device_uuid="dev-1",
        session_date="2026-09-11", storage_path=base, config={},
    )
    await rec.start()

    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    ctx = SessionContext(
        session_id="sess-1", device_id="dev-1", user_id="u1", sm=sm,
        enable_video=True, audio_sample_rate=16000,
        latest_video_frames=deque([(1, _JPEG, 1000, 640, 480, 90, "front")]),
    )
    handler = MagicMock()
    handler.enqueue_outbound = AsyncMock()
    handler._stopped = False
    handler.principal.device_id = "dev-1"
    handler.principal.user_id = "u1"
    orch = DialogOrchestrator(
        handler=handler, codec=MagicMock(), session_mgr=None,
        asr_service=None, llm_service=_RecordingLLM(), tts_service=None,
        opus_encoder=None, opus_decoder=None,
        system_prompt="", translate_prompt="Translate.",
        generation_opts={}, chat_store=FakeChatStore(),
        pipeline_mode="translate", recording_sink=rec,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._turn_detector = MagicMock()
    orch._turn_detector.on_frame.return_value = True
    orch._interrupt = MagicMock()
    orch._interrupt.should_yield.return_value = True

    try:
        orch._asr_stream = MagicMock()
        orch._asr_stream.final_text = "你好"
        orch._asr_stream.reset = AsyncMock()
        msg = MagicMock()
        msg.type = "audio"
        msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
        msg.payload.vad_state.value = "speech"
        with patch("session.orchestrator.generate_transcription_id", return_value=_TID):
            await orch._handle_audio(msg)
        await orch._pipeline_task
    finally:
        await rec.finalize()

    rows = [json.loads(line)
            for line in (base / "chat" / "translate.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert rows and all("images" not in r for r in rows)
    image_dir = base / "image"
    assert not image_dir.exists() or not any(image_dir.iterdir())
