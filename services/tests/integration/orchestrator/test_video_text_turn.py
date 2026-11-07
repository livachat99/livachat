"""video 文本回合（docs/android_screen_recording.md §8.2）：payload.text 非空且过门控时，
服务端不等 ASR 直接起一轮；无 text / 无帧 / 模式不适用 → 行为与现状完全一致。

覆盖 §6.1 用例 1-7、9、11（用例 8 的"口述竞态"属 ASR turn-end 编排，
其不变量"图只被消费一次"由用例 9 的第二轮断言（各轮自带帧、buffer 不残留）
与既有 barge-in 测试共同覆盖；用例 10 的 >512 协议层拒绝见 unit/protocol 用例）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.integration

TEXT_INSTRUCTION = "描述图片内容"


def _session_start(seq: int = 1, enable_video: bool = True, session_id: str | None = None):
    from livachat_protocol.generated.client_message import ClientSessionStart
    from livachat_protocol.generated.protocol_models import (
        ClientSessionStartConfig,
        ClientSessionStartPayload,
    )

    return ClientSessionStart(
        seq=seq,
        timestamp=seq * 1000,
        payload=ClientSessionStartPayload(
            session_id=session_id,
            config=ClientSessionStartConfig(
                enable_video=enable_video,
                audio_sample_rate=24000,
                vad_sensitivity="medium",
                client_version="android-1.0.0",
            ),
        ),
    )


def _video(seq: int, frame_id: int, text: str | None = None, data: str = "AAAA",
           facing: str = "back"):
    from livachat_protocol.generated.client_message import ClientVideo
    from livachat_protocol.generated.protocol_models import ClientVideoPayload

    return ClientVideo(
        seq=seq,
        timestamp=seq * 1000,
        payload=ClientVideoPayload(
            data=data, frame_id=frame_id, width=640, height=480,
            orientation=90, camera_facing=facing, quality=70, text=text,
        ),
    )


def _acks(handler) -> list[dict]:
    return [json.loads(f) for f in handler.outbound if json.loads(f)["type"] == "ai_video_ack"]


def _statuses(handler) -> list[dict]:
    return [json.loads(f) for f in handler.outbound if json.loads(f)["type"] == "status"]


def _ai_texts(handler) -> list[dict]:
    return [json.loads(f) for f in handler.outbound if json.loads(f)["type"] == "ai_text"]


async def _start_session(orch, handler, *, enable_video: bool = True):
    handler._inbound.put_nowait(_session_start(enable_video=enable_video))
    task = asyncio.create_task(orch.run())
    await asyncio.sleep(0.1)
    return task


async def _shutdown(orch, handler, task):
    handler.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestVideoTextTurn:
    async def test_no_text_behaves_as_before(self, fake_handler, fake_orchestrator):
        """§6.1-1：text 缺失（相机帧语义）→ 只入 buffer + ack，不起回合。"""
        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=5))
            await asyncio.sleep(0.2)

            assert _acks(fake_handler)[-1]["payload"]["processed"] is True
            assert len(orch._ctx.latest_video_frames) == 1
            assert orch._pipeline_task is None
            assert [m for m in orch._chat_store.messages if m["role"] == "user"] == []
            assert orch._history.get_recent() == []
        finally:
            await _shutdown(orch, fake_handler, task)

    async def test_text_turn_writes_triple_and_starts_pipeline(
        self, fake_handler, fake_orchestrator_with_sink, fake_recording_sink,
    ):
        """§6.1-2：text 非空 + 有帧 → 三件一体（history/chat_store/recording tap）+ 起轮 + 回图。"""
        orch = fake_orchestrator_with_sink
        task = await _start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=9, text=TEXT_INSTRUCTION))
            await asyncio.sleep(0.4)

            # ① 快照被消费（buffer 清空）
            assert len(orch._ctx.latest_video_frames) == 0
            # ② history 写入 user 轮（带图 data_url）
            user_history = [m for m in orch._history.get_recent() if m["role"] == "user"]
            assert len(user_history) == 1
            assert user_history[0]["content"] == TEXT_INSTRUCTION
            assert user_history[0].get("images")
            # ③ chat_store 写入：kind 必须沿用 "asr"（契约值 + 恢复映射）
            user_rows = [m for m in orch._chat_store.messages if m["role"] == "user"]
            assert len(user_rows) == 1
            stored = user_rows[0]
            assert stored["kind"] == "asr"
            assert stored["role"] == "user"
            assert stored["content"] == TEXT_INSTRUCTION
            assert stored["ref_id"] == "video-9"
            assert stored["images"]
            # ④ recording tap（role=user、同轮 frames、落 history.jsonl 的 source=None）
            user_taps = [t for t in fake_recording_sink.chat_texts if t["role"] == "user"]
            assert len(user_taps) == 1
            tapped = user_taps[0]
            assert tapped["role"] == "user"
            assert tapped["text"] == TEXT_INSTRUCTION
            assert tapped["ref_id"] == "video-9"
            assert tapped["source"] is None
            assert tapped["frames"] and len(tapped["frames"]) == 1
            # ⑤ 管线启动 + final ai_text 回图
            assert orch._pipeline_task is not None
            finals = [t for t in _ai_texts(fake_handler) if t["payload"].get("is_final")]
            assert finals, "expected a final ai_text"
            assert finals[-1]["payload"].get("image"), "final ai_text 应带本轮回图"
            # ⑥ 回滚句柄落位（fd_pipeline_llm_v2.md）：五值全为写入时值、token=ref_id
            rec = orch._turn_user_record
            assert rec is not None
            assert rec["token"] == "video-9"
            assert rec["content"] == TEXT_INSTRUCTION
            assert rec["ts_ms"] == stored["ts_ms"]
            assert rec["device_uuid"] == orch._ctx.device_id
            assert rec["session_id"] == orch._ctx.session_id
            # ack 语义不变（帧已入 buffer 即 True）
            assert _acks(fake_handler)[-1]["payload"]["processed"] is True
        finally:
            await _shutdown(orch, fake_handler, task)

    async def test_gallery_facing_accepted_and_echoed(
        self, fake_handler, fake_orchestrator_with_sink,
    ):
        """v7：camera_facing=gallery 被接受，final ai_text 回显 gallery。"""
        orch = fake_orchestrator_with_sink
        task = await _start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(
                _video(seq=2, frame_id=12, text=TEXT_INSTRUCTION, facing="gallery")
            )
            await asyncio.sleep(0.4)

            assert _acks(fake_handler)[-1]["payload"]["processed"] is True
            assert len(orch._ctx.latest_video_frames) == 0
            finals = [t for t in _ai_texts(fake_handler) if t["payload"].get("is_final")]
            assert finals, "expected a final ai_text"
            image = finals[-1]["payload"].get("image")
            assert image and image["camera_facing"] == "gallery"
        finally:
            await _shutdown(orch, fake_handler, task)

    @pytest.mark.parametrize("text", ["", "   ", "!"])
    async def test_blank_or_too_short_text_skips_turn(
        self, fake_handler, fake_orchestrator, text: str,
    ):
        """§6.1-3：空串/空白/过短（< _min_meaningful_chars）→ 同"无 text"。"""
        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=3, text=text))
            await asyncio.sleep(0.2)

            assert orch._pipeline_task is None
            assert [m for m in orch._chat_store.messages if m["role"] == "user"] == []
            assert orch._history.get_recent() == []
            assert len(orch._ctx.latest_video_frames) == 1
        finally:
            await _shutdown(orch, fake_handler, task)

    async def test_enable_video_false_skips_turn(self, fake_handler, fake_orchestrator):
        """§6.1-4：session 未开视觉 → 快照返回 []、不起回合（日志 info）。"""
        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler, enable_video=False)
        try:
            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=4, text=TEXT_INSTRUCTION))
            await asyncio.sleep(0.2)

            assert orch._pipeline_task is None
            assert [m for m in orch._chat_store.messages if m["role"] == "user"] == []
            # 帧本身仍入 buffer（现状语义不变）
            assert len(orch._ctx.latest_video_frames) == 1
        finally:
            await _shutdown(orch, fake_handler, task)

    async def test_malformed_frame_with_text_skips_turn(self, fake_handler, fake_orchestrator):
        """§6.1-5：畸形 base64 + text → processed=False、不入 buffer、不起回合。"""
        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler)
        try:
            msg = _video(seq=2, frame_id=6, text=TEXT_INSTRUCTION)
            msg.payload.data = "A"  # 坏 padding：绕过构造校验直达 validate=True 兜底
            fake_handler._inbound.put_nowait(msg)
            await asyncio.sleep(0.2)

            assert _acks(fake_handler)[-1]["payload"]["processed"] is False
            assert len(orch._ctx.latest_video_frames) == 0
            assert orch._pipeline_task is None
            assert [m for m in orch._chat_store.messages if m["role"] == "user"] == []
        finally:
            await _shutdown(orch, fake_handler, task)

    @pytest.mark.parametrize("mode", ["translate", "transcribe", "asr_only"])
    async def test_non_chat_modes_skip_turn(self, fake_handler, fake_orchestrator, mode: str):
        """§6.1-6：translate/transcribe/asr_only 模式 → 不起回合（与 AF 门控一致）。"""
        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler)
        try:
            orch._pipeline_mode = mode
            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=7, text=TEXT_INSTRUCTION))
            await asyncio.sleep(0.2)

            assert orch._pipeline_task is None
            assert [m for m in orch._chat_store.messages if m["role"] == "user"] == []
            assert len(orch._ctx.latest_video_frames) == 1
        finally:
            await _shutdown(orch, fake_handler, task)

    async def test_speaking_interrupts_before_text_turn(self, fake_handler, fake_orchestrator):
        """§6.1-7：SPEAKING 中收到带 text 的 video → 先既有中断（user_text）再起新轮。"""
        from session.state_machine import SessionState

        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler)
        try:
            orch._ctx.sm.transition(SessionState.THINKING)
            orch._ctx.sm.transition(SessionState.SPEAKING)
            epoch_before = orch._ctx.epoch

            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=8, text=TEXT_INSTRUCTION))
            await asyncio.sleep(0.3)

            # 中断已执行（epoch 递增 + status interrupted）+ 状态回 LISTENING
            assert orch._ctx.epoch > epoch_before
            assert any(s["payload"]["reason"] == "interrupted" for s in _statuses(fake_handler))
            assert orch._ctx.state == SessionState.LISTENING
            # 仍写入 user 轮并起新轮（先落盘后中断）
            assert len([m for m in orch._chat_store.messages if m["role"] == "user"]) == 1
            assert orch._pipeline_task is not None
            assert len(orch._ctx.latest_video_frames) == 0
        finally:
            await _shutdown(orch, fake_handler, task)

    async def test_second_tap_interrupts_and_replaces_pipeline(
        self, fake_handler, fake_orchestrator_with_sink, fake_recording_sink,
    ):
        """§6.1-9：连续两次带 text 点按 → 第二次先中断、槽位不泄漏；各轮自带帧、buffer 不残留。"""
        orch = fake_orchestrator_with_sink
        task = await _start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=11, text=TEXT_INSTRUCTION))
            await asyncio.sleep(0.2)
            first_task = orch._pipeline_task
            assert first_task is not None

            fake_handler._inbound.put_nowait(_video(seq=3, frame_id=12, text=TEXT_INSTRUCTION))
            await asyncio.sleep(0.3)

            assert first_task.done(), "第二轮应先取消第一轮管线"
            assert orch._pipeline_task is not None and orch._pipeline_task is not first_task
            assert [m["ref_id"] for m in orch._chat_store.messages if m["role"] == "user"] == ["video-11", "video-12"]
            assert [t["ref_id"] for t in fake_recording_sink.chat_texts if t["role"] == "user"] == ["video-11", "video-12"]
            assert len(orch._ctx.latest_video_frames) == 0
        finally:
            await _shutdown(orch, fake_handler, task)

    async def test_empty_data_with_text_skips_turn(self, fake_handler, fake_orchestrator):
        """§6.1-11：payload.data 为空 + text → processed=True 但未入 buffer → 不起回合。"""
        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler)
        try:
            msg = _video(seq=2, frame_id=13, text=TEXT_INSTRUCTION)
            msg.payload.data = ""  # 绕过 Base64String minLength 构造校验
            fake_handler._inbound.put_nowait(msg)
            await asyncio.sleep(0.2)

            assert _acks(fake_handler)[-1]["payload"]["processed"] is True
            assert len(orch._ctx.latest_video_frames) == 0
            assert orch._pipeline_task is None
            assert [m for m in orch._chat_store.messages if m["role"] == "user"] == []
        finally:
            await _shutdown(orch, fake_handler, task)


class _RaisingChatStore:
    """chat_store 写失败注入（验证文本回合异常不穿出 _dispatch、ack 仍下发）。"""

    def on_device(self, **kwargs) -> None:
        pass

    def on_message(self, **kwargs) -> None:
        raise RuntimeError("chat store boom")


class TestVideoTextTurnResilience:
    async def test_branch_failure_does_not_kill_loop_and_ack_still_sent(
        self, fake_handler, fake_orchestrator,
    ):
        """复审修复：文本回合分支异常不得穿出 _dispatch（会话主循环存活）+ ack 仍下发。"""
        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler)
        try:
            orch._chat_store = _RaisingChatStore()   # 注入写失败
            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=21, text=TEXT_INSTRUCTION))
            await asyncio.sleep(0.2)

            # ack 仍到达（证明 _handle_video 跑完、异常已被兜底）
            assert len(_acks(fake_handler)) == 1
            assert _acks(fake_handler)[-1]["payload"]["processed"] is True
            # 主循环存活：后续消息照常处理（再点按 → 第二条 ack）
            fake_handler._inbound.put_nowait(_video(seq=3, frame_id=22, text=TEXT_INSTRUCTION))
            await asyncio.sleep(0.2)
            assert len(_acks(fake_handler)) == 2
        finally:
            await _shutdown(orch, fake_handler, task)

    async def test_empty_data_with_text_keeps_stale_buffer_untouched(
        self, fake_handler, fake_orchestrator,
    ):
        """复审修复：空 data + text 不得把 buffer 里的旧帧当成本帧起回合（§4.5/§6.1-11）。"""
        orch = fake_orchestrator
        task = await _start_session(orch, fake_handler)
        try:
            # 先放一帧旧帧进 buffer（相机语义：不带 text）
            fake_handler._inbound.put_nowait(_video(seq=2, frame_id=30))
            await asyncio.sleep(0.15)
            assert len(orch._ctx.latest_video_frames) == 1

            # 空 data + text：不得消费旧帧、不得起回合
            msg = _video(seq=3, frame_id=31, text=TEXT_INSTRUCTION)
            msg.payload.data = ""   # 绕过 Base64String minLength 构造校验
            fake_handler._inbound.put_nowait(msg)
            await asyncio.sleep(0.2)

            assert orch._pipeline_task is None
            assert [m for m in orch._chat_store.messages if m["role"] == "user"] == []
            # 旧帧仍在 buffer（未被快照取走），可被后续 AF/回合正常使用
            assert len(orch._ctx.latest_video_frames) == 1
            assert orch._ctx.latest_video_frames[0][0] == 30
        finally:
            await _shutdown(orch, fake_handler, task)
