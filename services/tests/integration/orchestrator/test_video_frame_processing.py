from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration


class TestVideoFrameProcessing:
    async def test_video_ack_in_listening(self, fake_handler, fake_orchestrator):
        from livachat_protocol.generated.client_message import ClientSessionStart, ClientVideo
        from livachat_protocol.generated.protocol_models import (
            ClientSessionStartConfig,
            ClientSessionStartPayload,
            ClientVideoPayload,
        )

        fake_handler._inbound.put_nowait(
            ClientSessionStart(
                seq=1,
                timestamp=1000,
                payload=ClientSessionStartPayload(
                    session_id=None,
                    config=ClientSessionStartConfig(
                        enable_video=True,
                        audio_sample_rate=24000,
                        vad_sensitivity="medium",
                        client_version="android-1.0.0",
                    ),
                ),
            )
        )

        task = asyncio.create_task(fake_orchestrator.run())
        await asyncio.sleep(0.1)

        # Send video in listening state → should be processed
        fake_handler._inbound.put_nowait(
            ClientVideo(
                seq=2,
                timestamp=2000,
                payload=ClientVideoPayload(
                    data="AAAA", frame_id=1, width=640, height=480,
                    orientation=90, camera_facing="back", quality=70,
                ),
            )
        )
        await asyncio.sleep(0.1)

        acks = [json.loads(f) for f in fake_handler.outbound if json.loads(f)["type"] == "ai_video_ack"]
        assert len(acks) >= 1
        assert acks[0]["payload"]["processed"] is True

        fake_orchestrator._handler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_video_ack_in_speaking(self, fake_handler, fake_orchestrator):
        """SPEAKING 状态到达的帧同样入 buffer 且 processed=True（任何状态可入 buffer）。"""
        from livachat_protocol.generated.client_message import ClientSessionStart, ClientVideo
        from livachat_protocol.generated.protocol_models import (
            ClientSessionStartConfig,
            ClientSessionStartPayload,
            ClientVideoPayload,
        )

        from session.state_machine import SessionState

        fake_handler._inbound.put_nowait(
            ClientSessionStart(
                seq=1,
                timestamp=1000,
                payload=ClientSessionStartPayload(
                    session_id=None,
                    config=ClientSessionStartConfig(
                        enable_video=True,
                        audio_sample_rate=24000,
                        vad_sensitivity="medium",
                        client_version="android-1.0.0",
                    ),
                ),
            )
        )

        task = asyncio.create_task(fake_orchestrator.run())
        await asyncio.sleep(0.1)

        # LISTENING → THINKING → SPEAKING（合法转换路径）
        fake_orchestrator._ctx.sm.transition(SessionState.THINKING)
        fake_orchestrator._ctx.sm.transition(SessionState.SPEAKING)

        fake_handler._inbound.put_nowait(
            ClientVideo(
                seq=2,
                timestamp=2000,
                payload=ClientVideoPayload(
                    data="AAAA", frame_id=7, width=640, height=480,
                    orientation=90, camera_facing="back", quality=70,
                ),
            )
        )
        await asyncio.sleep(0.1)

        acks = [json.loads(f) for f in fake_handler.outbound if json.loads(f)["type"] == "ai_video_ack"]
        assert len(acks) >= 1
        assert acks[0]["payload"]["processed"] is True
        # 帧已入 buffer（K=1，frame_id 即最新一帧）
        assert len(fake_orchestrator._ctx.latest_video_frames) == 1
        assert fake_orchestrator._ctx.latest_video_frames[0][0] == 7

        fake_orchestrator._handler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_invalid_base64_frame_does_not_kill_loop(self, fake_handler, fake_orchestrator):
        """畸形 base64 帧：ACK processed=False、不入 buffer、dispatch 循环存活。

        覆盖两种：坏 padding（"A"）与可解码垃圾（"A*B=C"——validate=False 会静默
        丢弃非法字符解码成垃圾字节，validate=True 统一拒绝）。
        """
        from livachat_protocol.generated.client_message import ClientSessionStart, ClientVideo
        from livachat_protocol.generated.protocol_models import (
            ClientSessionStartConfig,
            ClientSessionStartPayload,
            ClientVideoPayload,
        )

        fake_handler._inbound.put_nowait(
            ClientSessionStart(
                seq=1,
                timestamp=1000,
                payload=ClientSessionStartPayload(
                    session_id=None,
                    config=ClientSessionStartConfig(
                        enable_video=True,
                        audio_sample_rate=24000,
                        vad_sensitivity="medium",
                        client_version="android-1.0.0",
                    ),
                ),
            )
        )

        task = asyncio.create_task(fake_orchestrator.run())
        await asyncio.sleep(0.1)

        # 非法 padding + 非法字母表字符 → b64decode(validate=True) 抛错；不得杀死主循环。
        # 注：生产路径上字母表非法首先被协议层 pydantic Base64String 拦截；测试用
        # 赋值绕过构造校验，直达 _handle_video 的 validate=True 兜底层。
        for seq, bad_data in ((2, "A"), (3, "A*B=C")):
            msg = ClientVideo(
                seq=seq,
                timestamp=seq * 1000,
                payload=ClientVideoPayload(
                    data="AAAA", frame_id=seq - 1, width=640, height=480,
                    orientation=90, camera_facing="back", quality=70,
                ),
            )
            msg.payload.data = bad_data
            fake_handler._inbound.put_nowait(msg)
            await asyncio.sleep(0.1)

        acks = [json.loads(f) for f in fake_handler.outbound if json.loads(f)["type"] == "ai_video_ack"]
        assert len(acks) >= 2
        assert acks[0]["payload"]["processed"] is False
        assert acks[1]["payload"]["processed"] is False
        assert len(fake_orchestrator._ctx.latest_video_frames) == 0

        # 主循环存活：后续正常帧仍被处理
        fake_handler._inbound.put_nowait(
            ClientVideo(
                seq=4,
                timestamp=4000,
                payload=ClientVideoPayload(
                    data="AAAA", frame_id=3, width=640, height=480,
                    orientation=90, camera_facing="back", quality=70,
                ),
            )
        )
        await asyncio.sleep(0.1)

        acks = [json.loads(f) for f in fake_handler.outbound if json.loads(f)["type"] == "ai_video_ack"]
        assert len(acks) >= 3
        assert acks[-1]["payload"]["processed"] is True
        assert len(fake_orchestrator._ctx.latest_video_frames) == 1
        assert fake_orchestrator._ctx.latest_video_frames[0][0] == 3

        fake_orchestrator._handler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# §8 turn 首帧直起轮（docs/fd_pipeline_video_process.md §8.4）
# ---------------------------------------------------------------------------

TURN_TEXT = "描述图片内容"


def _turn_session_start(seq: int = 1, *, session_id: str | None = None,
                        enable_video: bool = True, language: str | None = None):
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
                language=language,
            ),
        ),
    )


def _turn_video(seq: int, frame_id: int, *, turn_id: int | None = None,
                text: str | None = None):
    from livachat_protocol.generated.client_message import ClientVideo
    from livachat_protocol.generated.protocol_models import ClientVideoPayload

    return ClientVideo(
        seq=seq,
        timestamp=seq * 1000,
        payload=ClientVideoPayload(
            data="AAAA", frame_id=frame_id, width=640, height=480,
            orientation=90, camera_facing="back", quality=70,
            turn_id=turn_id, text=text,
        ),
    )


def _jsons(handler, msg_type: str) -> list[dict]:
    return [json.loads(f) for f in handler.outbound if json.loads(f)["type"] == msg_type]


def _user_rows(orch) -> list[dict]:
    return [m for m in orch._chat_store.messages if m["role"] == "user"]


async def _turn_start_session(orch, handler, *, session_id: str | None = None,
                              language: str | None = None) -> asyncio.Task:
    handler._inbound.put_nowait(_turn_session_start(session_id=session_id, language=language))
    task = asyncio.create_task(orch.run())
    await asyncio.sleep(0.1)
    return task


async def _turn_shutdown(handler, task) -> None:
    handler.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestVideoTurnFirstFrame:
    """§8：turn_id 变化 = 新采集轮首帧 → orchestrator 立即起轮（不等 ASR）；
    无 turn_id 时新分支不可达——行为与现状逐位一致。"""

    async def test_turn_change_starts_immediate_turn(
        self, fake_handler, fake_orchestrator_with_sink, fake_recording_sink,
    ):
        """首帧直起轮：三件一体（content=[相机画面]、ref_id=cam-<id>）+ 起轮 + final 回图。"""
        orch = fake_orchestrator_with_sink
        task = await _turn_start_session(orch, fake_handler, language="zh-Hans")
        try:
            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=9, turn_id=1))
            await asyncio.sleep(0.4)

            # 快照消费首帧 + turn 跟踪
            assert len(orch._ctx.latest_video_frames) == 0
            assert orch._last_video_turn_id == 1
            # history / chat_store / recording tap 三件一体
            user_history = [m for m in orch._history.get_recent() if m["role"] == "user"]
            assert len(user_history) == 1
            assert user_history[0]["content"] == "[相机画面]"
            assert user_history[0].get("images")
            rows = _user_rows(orch)
            assert len(rows) == 1
            assert rows[0]["kind"] == "asr"
            assert rows[0]["content"] == "[相机画面]"
            assert rows[0]["ref_id"] == "cam-9"
            assert rows[0]["images"]
            user_taps = [t for t in fake_recording_sink.chat_texts if t["role"] == "user"]
            assert len(user_taps) == 1
            assert user_taps[0]["text"] == "[相机画面]"
            assert user_taps[0]["ref_id"] == "cam-9"
            assert user_taps[0]["frames"] and len(user_taps[0]["frames"]) == 1
            # 起轮 + final ai_text 回图
            assert orch._pipeline_task is not None
            finals = [t for t in _jsons(fake_handler, "ai_text") if t["payload"].get("is_final")]
            assert finals and finals[-1]["payload"].get("image")
            # 回滚句柄（D5c）：token = cam-9
            rec = orch._turn_user_record
            assert rec is not None and rec["token"] == "cam-9" and rec["content"] == "[相机画面]"
            # ack 语义不变
            assert _jsons(fake_handler, "ai_video_ack")[-1]["payload"]["processed"] is True
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_turn_first_frame_defaults_to_english(
        self, fake_handler, fake_orchestrator,
    ):
        """不传 language → 主动文本默认英文（docs/server_multilingual.md §4.1）。"""
        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=9, turn_id=1))
            await asyncio.sleep(0.3)
            rows = _user_rows(orch)
            assert rows and rows[0]["content"] == "[Camera view]"
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_turn_unchanged_no_turn(self, fake_handler, fake_orchestrator):
        """同 turn_id 连帧：仅入 buffer，不再起轮（回归现状逻辑）。"""
        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=5, turn_id=7))
            await asyncio.sleep(0.3)
            assert len(_user_rows(orch)) == 1  # 首帧触发

            fake_handler._inbound.put_nowait(_turn_video(seq=3, frame_id=6, turn_id=7))
            await asyncio.sleep(0.3)
            assert len(_user_rows(orch)) == 1  # 未变 → 不起新轮
            assert not any(s["payload"]["reason"] == "interrupted"
                           for s in _jsons(fake_handler, "status"))
            assert orch._last_video_turn_id == 7
            # 帧正常入 buffer（K=1 最新）
            assert [f[0] for f in orch._ctx.latest_video_frames] == [6]
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_cam_turn_completion_arms_visual_suppression(
        self, fake_handler, fake_orchestrator,
    ):
        """D8：cam 首帧直起轮未中断收尾后开窗，并强制补一次带图检查。"""
        orch = fake_orchestrator
        monitor = MagicMock()
        monitor.ready.return_value = True
        monitor.has_frames = True
        monitor.request_video_check.return_value = True
        monitor.close = AsyncMock()
        task = await _turn_start_session(orch, fake_handler)
        try:
            orch._av_monitor = monitor
            assert orch._av_visual_suppress_until == 0.0
            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=1, turn_id=1))
            await asyncio.sleep(0.4)
            first_deadline = orch._av_visual_suppress_until
            assert first_deadline > time.monotonic()
            monitor.request_video_check.assert_any_call(force=True)

            # 同 turn_id 后续帧：不起首帧直起轮，也不刷新抑制窗
            fake_handler._inbound.put_nowait(_turn_video(seq=3, frame_id=2, turn_id=1))
            await asyncio.sleep(0.2)
            assert orch._av_visual_suppress_until == first_deadline

            # 新 turn_id（切镜头/重开）：重新起轮并在收尾刷新窗口
            fake_handler._inbound.put_nowait(_turn_video(seq=4, frame_id=3, turn_id=5))
            await asyncio.sleep(0.5)
            assert orch._av_visual_suppress_until > first_deadline
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_non_visual_turn_does_not_arm_visual_suppression(
        self, fake_handler, fake_orchestrator,
    ):
        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            await orch._start_thinking("hello")
            assert orch._av_visual_suppress_until == 0.0
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_turn_change_with_text_keeps_v3(self, fake_handler, fake_orchestrator):
        """带 text 的帧 + turn 变化：仅走 v3 文本回合；turn id 照记。"""
        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(
                _turn_video(seq=2, frame_id=11, turn_id=3, text=TURN_TEXT))
            await asyncio.sleep(0.3)

            rows = _user_rows(orch)
            assert len(rows) == 1
            assert rows[0]["content"] == TURN_TEXT
            assert rows[0]["ref_id"] == "video-11"
            # turn id 照记（防"text 帧吃掉 turn 后下一帧又触发"）
            assert orch._last_video_turn_id == 3
            assert orch._pipeline_task is not None
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_turn_change_in_speaking_interrupts(self, fake_handler, fake_orchestrator):
        """SPEAKING 中首帧：先中断（video_turn）再起轮；先落盘后中断。"""
        from session.state_machine import SessionState

        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            orch._ctx.sm.transition(SessionState.THINKING)
            orch._ctx.sm.transition(SessionState.SPEAKING)
            epoch_before = orch._ctx.epoch

            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=8, turn_id=5))
            await asyncio.sleep(0.3)

            assert orch._ctx.epoch > epoch_before
            assert any(s["payload"]["reason"] == "interrupted"
                       for s in _jsons(fake_handler, "status"))
            assert orch._ctx.state == SessionState.LISTENING
            assert len(_user_rows(orch)) == 1
            assert len(orch._ctx.latest_video_frames) == 0
            assert orch._pipeline_task is not None
            assert orch._last_video_turn_id == 5
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_turn_change_dropped_when_danger_inflight(self, fake_handler, fake_orchestrator):
        """危险在飞豁免（[P6] 同款）：放弃触发、不消费帧；turn id 照记（机会一次）。"""
        async def _hang() -> None:
            await asyncio.sleep(30)

        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            orch._spawn_pipeline(_hang(), danger=True)
            danger_task = orch._pipeline_task

            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=4, turn_id=9))
            await asyncio.sleep(0.2)

            assert len(_user_rows(orch)) == 0
            assert len(orch._ctx.latest_video_frames) == 1  # 帧未被消费
            assert orch._last_video_turn_id == 9
            assert orch._pipeline_task is danger_task and not danger_task.done()
            assert not any(s["payload"]["reason"] == "interrupted"
                           for s in _jsons(fake_handler, "status"))
        finally:
            await _turn_shutdown(fake_handler, task)

    @pytest.mark.parametrize("mode", ["translate", "transcribe", "asr_only", "omni"])
    async def test_turn_change_gated_in_translate_transcribe_omni(
        self, fake_handler, fake_orchestrator, mode: str,
    ):
        """模式门控（与 v3 同元组）：不触发；帧照常入 buffer、turn id 只记录。"""
        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            orch._pipeline_mode = mode
            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=7, turn_id=2))
            await asyncio.sleep(0.2)

            assert orch._pipeline_task is None
            assert _user_rows(orch) == []
            assert len(orch._ctx.latest_video_frames) == 1
            assert orch._last_video_turn_id == 2
        finally:
            await _turn_shutdown(fake_handler, task)

    @pytest.mark.parametrize("turn_id", [None, 0])
    async def test_turn_id_absent_inert(self, fake_handler, fake_orchestrator, turn_id):
        """旧客户端（无 turn_id）或 turn_id=0（未启用哨兵）：新分支不可达，与现状逐位一致。"""
        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=3, turn_id=turn_id))
            await asyncio.sleep(0.2)

            assert orch._pipeline_task is None
            assert _user_rows(orch) == []
            assert orch._history.get_recent() == []
            assert len(orch._ctx.latest_video_frames) == 1
            assert orch._last_video_turn_id is None
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_first_frame_turn_change(self, fake_handler, fake_orchestrator):
        """§8.6.3：判定基准 = 设备级 turn 记忆——无记录首帧触发、同轮不触发、换轮触发；
        会话重开（同连接重发 session.start）以记忆播种基准，不再静默认领。"""
        orch = fake_orchestrator
        task = await _turn_start_session(orch, fake_handler)
        try:
            # 无记录（服务端未见任何 turn）→ 首帧即触发，并写回设备记忆
            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=1, turn_id=1))
            await asyncio.sleep(0.3)
            assert len(_user_rows(orch)) == 1
            assert orch._last_video_turn_id == 1
            assert orch._session_mgr.last_video_turn("device-1") == 1

            # 同连接重发 session.start（resume）：基准从设备记忆播种（=1，而非 None）
            fake_handler._inbound.put_nowait(
                _turn_session_start(seq=3, session_id="sess_abcdefghij12345678"))
            await asyncio.sleep(0.2)
            assert orch._last_video_turn_id == 1

            # 同轮帧（画面延续）→ 不触发
            fake_handler._inbound.put_nowait(_turn_video(seq=4, frame_id=2, turn_id=1))
            await asyncio.sleep(0.2)
            assert len(_user_rows(orch)) == 1  # 未新增
            assert orch._last_video_turn_id == 1

            # 换轮（用户开/切相机）→ 触发 + 记忆更新
            fake_handler._inbound.put_nowait(_turn_video(seq=5, frame_id=3, turn_id=5))
            await asyncio.sleep(0.3)
            assert len(_user_rows(orch)) == 2
            assert orch._last_video_turn_id == 5
            assert orch._session_mgr.last_video_turn("device-1") == 5
        finally:
            await _turn_shutdown(fake_handler, task)

    async def test_turn_change_after_reconnect_triggers(
        self, fake_handler, fake_orchestrator,
    ):
        """§8.6.3 跨连接回归（真机 17:12 场景）：两个 orchestrator（两条连接）共享一个
        SessionManager——连接 1 开相机（turn=5）触发；连接 2（重连）首帧同轮静默、
        换轮（重连后开相机 turn=6）立即触发（不再被 resume 静默吞掉）。"""
        from session.orchestrator import DialogOrchestrator
        from tests.integration.orchestrator.conftest import (
            FakeAsrService,
            FakeChatStore,
            FakeLlmService,
            FakeTtsService,
            _FakeHandler,
        )

        shared_mgr = fake_orchestrator._session_mgr
        task1 = await _turn_start_session(fake_orchestrator, fake_handler)
        try:
            # 连接 1：开相机 turn=5 → 触发；设备记忆写回
            fake_handler._inbound.put_nowait(_turn_video(seq=2, frame_id=1, turn_id=5))
            await asyncio.sleep(0.3)
            assert len(_user_rows(fake_orchestrator)) == 1
            assert shared_mgr.last_video_turn("device-1") == 5

            # 连接 2：重连（resume session_id），共享同一 SessionManager
            handler2 = _FakeHandler()
            orch2 = DialogOrchestrator(
                handler=handler2,
                codec=handler2._codec,
                session_mgr=shared_mgr,
                asr_service=FakeAsrService(),
                llm_service=FakeLlmService(["Hello", " world!"]),
                tts_service=FakeTtsService(),
                system_prompt="You are helpful.",
                chat_store=FakeChatStore(),
            )
            task2 = await _turn_start_session(
                orch2, handler2, session_id="sess_abcdefghij12345678"
            )
            try:
                # 首帧延续同一轮（flap 重连后画面延续）→ 静默
                handler2._inbound.put_nowait(_turn_video(seq=2, frame_id=1, turn_id=5))
                await asyncio.sleep(0.2)
                assert _user_rows(orch2) == []
                assert orch2._last_video_turn_id == 5

                # 重连后开相机（换轮）→ 立即触发
                handler2._inbound.put_nowait(_turn_video(seq=3, frame_id=2, turn_id=6))
                await asyncio.sleep(0.3)
                assert len(_user_rows(orch2)) == 1
                assert shared_mgr.last_video_turn("device-1") == 6
            finally:
                await _turn_shutdown(handler2, task2)
        finally:
            await _turn_shutdown(fake_handler, task1)
