"""ASR 流失败恢复单测：内联重试、错误提示节流、poll 竞态防护。

对照 orchestrator._feed_asr_with_recovery / _poll_asr_results。场景背景：
上游 ASR task 被空闲判死（如百炼 29min 长静默）后，下一帧 feed 失败——
旧实现立即下发 1001 弹窗；现为先在原帧上有界重试重建，全失败才提示。
"""

from __future__ import annotations

import asyncio
import base64
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from asr.base import AsrResult
from session.orchestrator import DialogOrchestrator
from session.state_machine import SessionState
from shared.utils.errors import ProviderError
from tests.unit.session.conftest import FakeChatStore

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _handler_mock():
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._inbound = MagicMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _make_orch(asr_service) -> DialogOrchestrator:
    from session.context import SessionContext
    from session.history import WorkingMemory
    from session.state_machine import SessionStateMachine

    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    ctx = SessionContext(
        session_id="test-sid",
        device_id="dev-1",
        user_id="user-1",
        sm=sm,
        enable_video=False,
        audio_sample_rate=16000,
    )
    orch = DialogOrchestrator(
        handler=_handler_mock(),
        codec=MagicMock(),
        session_mgr=None,
        asr_service=asr_service,
        llm_service=None,
        tts_service=None,
        opus_encoder=None,
        opus_decoder=None,
        system_prompt="You are a companion.",
        translate_prompt="",
        generation_opts={},
        chat_store=FakeChatStore(),
        pipeline_mode="full",
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._turn_detector = MagicMock()
    orch._turn_detector.on_frame.return_value = False
    orch._interrupt = MagicMock()
    orch._recording_sink = None
    orch._agent_publisher = None
    orch._codec.make_error.return_value = "ERR"
    # 测试默认缩短退避与尝试超时，避免真实 sleep/超时拖慢用例
    orch._asr_open_retry_backoff_s = 0.0
    orch._asr_open_attempt_timeout_s = 1.0
    return orch


def _error_frames(orch: DialogOrchestrator) -> list:
    return [
        c for c in orch._handler.enqueue_outbound.call_args_list
        if c.args and c.args[0] == "ERR"
    ]


async def _feed_frame(orch: DialogOrchestrator) -> None:
    msg = MagicMock()
    msg.type = "audio"
    msg.payload.data = base64.b64encode(b"\x00" * 320).decode()
    msg.payload.vad_state.value = "speech"
    await orch._handle_audio(msg)


class FakeAsrStream:
    def __init__(self, *, feed_error: Exception | None = None):
        self._feed_error = feed_error
        self.fed: list[bytes] = []
        self.closed = False
        self.final_text = ""

    async def feed(self, pcm: bytes) -> None:
        if self._feed_error is not None:
            raise self._feed_error
        self.fed.append(pcm)

    def results(self):
        async def _gen():
            return
            yield  # pragma: no cover - 空异步迭代器

        return _gen()

    async def final(self) -> str:
        return self.final_text

    async def reset(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeAsrService:
    """按脚本依次返回流/抛错；脚本耗尽后全部抛 ProviderError。"""

    def __init__(self, script: list):
        self._script = list(script)
        self.open_calls = 0

    async def open(self, *, sample_rate: int, **_kw):
        self.open_calls += 1
        item = (
            self._script.pop(0)
            if self._script
            else ProviderError("no more providers", provider="fake")
        )
        if isinstance(item, Exception):
            raise item
        return item


# ---------------------------------------------------------------------------
# 用例 1 — feed 首败 → 内联重开成功：无 1001、本帧进新流
# ---------------------------------------------------------------------------


class TestInlineRecovery:
    @pytest.mark.asyncio
    async def test_feed_failure_recovers_inline_without_client_error(self):
        dead = FakeAsrStream(feed_error=ProviderError("closed", provider="fake"))
        fresh = FakeAsrStream()
        svc = FakeAsrService([fresh])
        orch = _make_orch(svc)
        orch._asr_stream = dead

        await _feed_frame(orch)

        assert svc.open_calls == 1
        assert orch._asr_stream is fresh
        assert fresh.fed == [b"\x00" * 320]  # 触发帧补喂进新流，不丢
        assert dead.closed
        assert _error_frames(orch) == []
        assert orch._asr_last_error_sent_at == 0.0
        assert orch._asr_poll_task is not None
        await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_feed_failure_on_every_new_stream_exhausts_to_error(self):
        dead = FakeAsrStream(feed_error=ProviderError("closed", provider="fake"))
        svc = FakeAsrService([
            FakeAsrStream(feed_error=ProviderError("x", provider="fake")),
            FakeAsrStream(feed_error=ProviderError("x", provider="fake")),
        ])
        orch = _make_orch(svc)
        orch._asr_stream = dead

        await _feed_frame(orch)

        # 第 1 次尝试用旧流（不 open），其后 2 次各重开一次，全败 → 提示一次
        assert svc.open_calls == 2
        assert orch._asr_stream is None
        assert len(_error_frames(orch)) == 1

    @pytest.mark.asyncio
    async def test_recovery_replaces_zombie_poll_task(self):
        """旧流遗留的僵尸 poll 任务（永久阻塞在旧队列）不得挡住新流的消费。"""
        zombie_gate = asyncio.Event()

        async def _zombie():
            await zombie_gate.wait()

        dead = FakeAsrStream(feed_error=ProviderError("closed", provider="fake"))
        fresh = FakeAsrStream()
        svc = FakeAsrService([fresh])
        orch = _make_orch(svc)
        orch._asr_stream = dead
        zombie = asyncio.create_task(_zombie())
        orch._asr_poll_task = zombie

        await _feed_frame(orch)

        assert orch._asr_stream is fresh
        assert orch._asr_poll_task is not zombie
        assert zombie.cancelled()
        await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_dead_poll_task_recreated_after_successful_feed(self):
        """未重开（流仍活）时：poll 任务已死则补建。"""
        alive = FakeAsrStream()
        orch = _make_orch(FakeAsrService([]))
        orch._asr_stream = alive

        async def _noop():
            return

        done_task = asyncio.create_task(_noop())
        await done_task  # 确保任务已结束
        orch._asr_poll_task = done_task

        await _feed_frame(orch)

        assert orch._asr_stream is alive
        assert orch._asr_poll_task is not done_task
        assert alive.fed == [b"\x00" * 320]
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# 用例 2 — 全部尝试失败：单次 1001 + 冷却限流；成功后复位
# ---------------------------------------------------------------------------


class TestRetryExhaustion:
    @pytest.mark.asyncio
    async def test_retries_exhausted_sends_single_error_and_cooldown(self):
        svc = FakeAsrService([])
        orch = _make_orch(svc)

        await _feed_frame(orch)
        assert svc.open_calls == 3
        assert len(_error_frames(orch)) == 1  # 全失败才提示
        assert orch._asr_last_error_sent_at > 0.0

        # 冷却期内：不重试、不重复提示
        await _feed_frame(orch)
        assert svc.open_calls == 3
        assert len(_error_frames(orch)) == 1

        # 冷却到期仍全败：重试但不重复提示（30s 提醒间隔内）
        orch._asr_open_fail_time = 0.0
        await _feed_frame(orch)
        assert svc.open_calls == 6
        assert len(_error_frames(orch)) == 1

    @pytest.mark.asyncio
    async def test_periodic_reminder_during_sustained_outage(self):
        """持续故障期间：超过提醒间隔后再次下发 1001（ASR 关键服务需反复可见）。"""
        svc = FakeAsrService([])
        orch = _make_orch(svc)

        await _feed_frame(orch)
        assert len(_error_frames(orch)) == 1

        # 模拟距上次提示已过 31s（冷却也到期）→ 再提醒一次
        orch._asr_open_fail_time = 0.0
        orch._asr_last_error_sent_at = time.monotonic() - (
            orch._asr_error_reminder_interval_s + 1.0
        )
        await _feed_frame(orch)
        assert len(_error_frames(orch)) == 2

    @pytest.mark.asyncio
    async def test_open_success_feed_failure_flap_does_not_defeat_reminder_throttle(self):
        """open 成功但 feed 立即失败（鉴权吊销类抖动）不得每轮冷却都重发 1001。

        复位条件是 feed 成功（流确证可用）；若挂在 open 成功处，每轮冷却会把
        节流清零 → 每次耗尽都立即再发 → 退化成 5s 刷屏。
        """
        svc = FakeAsrService([
            FakeAsrStream(feed_error=ProviderError("accepted then dropped", provider="fake"))
            for _ in range(12)
        ])
        orch = _make_orch(svc)

        await _feed_frame(orch)  # 周期 1：3 次"能连上但 feed 失败" → 全败 → 首败提示
        assert len(_error_frames(orch)) == 1

        # 模拟冷却到期后的两轮抖动：节流不得被 open 成功击穿
        orch._asr_open_fail_time = 0.0
        await _feed_frame(orch)
        orch._asr_open_fail_time = 0.0
        await _feed_frame(orch)
        assert len(_error_frames(orch)) == 1  # 30s 窗口内不再提示

        # 超过提醒间隔后：提醒照常
        orch._asr_open_fail_time = 0.0
        orch._asr_last_error_sent_at = time.monotonic() - (
            orch._asr_error_reminder_interval_s + 1.0
        )
        await _feed_frame(orch)
        assert len(_error_frames(orch)) == 2

    @pytest.mark.asyncio
    async def test_notification_resets_after_successful_reopen(self):
        fresh = FakeAsrStream()
        svc = FakeAsrService([
            ProviderError("down", provider="fake"),
            ProviderError("down", provider="fake"),
            ProviderError("down", provider="fake"),
            fresh,
        ])
        orch = _make_orch(svc)

        await _feed_frame(orch)
        assert len(_error_frames(orch)) == 1
        assert orch._asr_stream is None

        # 冷却到期后恢复成功 → 提示节流复位
        orch._asr_open_fail_time = 0.0
        await _feed_frame(orch)
        assert orch._asr_stream is fresh
        assert orch._asr_last_error_sent_at == 0.0
        assert len(_error_frames(orch)) == 1

        # 新故障周期（feed 失败且重开全败）→ 立即重新提示
        fresh._feed_error = ProviderError("closed again", provider="fake")
        await _feed_frame(orch)
        assert len(_error_frames(orch)) == 2
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# 用例 3 — 尝试有界：open 挂起受单次超时约束
# ---------------------------------------------------------------------------


class TestBoundedAttempts:
    @pytest.mark.asyncio
    async def test_hanging_open_is_bounded_by_attempt_timeout(self):
        class HangingService:
            def __init__(self) -> None:
                self.calls = 0

            async def open(self, *, sample_rate: int, **_kw):
                self.calls += 1
                await asyncio.sleep(30)

        svc = HangingService()
        orch = _make_orch(svc)
        orch._asr_open_attempt_timeout_s = 0.05

        t0 = time.monotonic()
        await _feed_frame(orch)
        elapsed = time.monotonic() - t0

        assert svc.calls == 3
        assert elapsed < 1.0
        assert len(_error_frames(orch)) == 1


# ---------------------------------------------------------------------------
# 用例 4 — poll 任务入口判空（防 NoneType 竞态）
# ---------------------------------------------------------------------------


class TestPollGuard:
    @pytest.mark.asyncio
    async def test_poll_task_exits_quietly_when_stream_missing(self):
        orch = _make_orch(FakeAsrService([]))
        orch._asr_stream = None

        await orch._poll_asr_results()  # 不得抛 AttributeError


# ---------------------------------------------------------------------------
# 用例 5 — 在途 final 送达不可取消（shield）
# ---------------------------------------------------------------------------


class FinalOnceStream(FakeAsrStream):
    """results() 产出一次 meaningful final（消费端随之进入在途送达）。"""

    def results(self):
        async def _gen():
            yield AsrResult(text="你好世界", is_final=True, transcription_id="t1")

        return _gen()


class TestFinalDeliverySafety:
    @pytest.mark.asyncio
    async def test_inflight_final_delivery_survives_poll_task_cancel(self):
        """恢复路径强换 poll 任务时，在途 final 的上屏/落库必须完成——不得丢句。

        复现窗口：poll 任务正在 _send_transcription 中，同帧 feed 失败触发重开
        换新流 → cancel 旧 poll 任务。shield 保证送达在后台跑完。
        """
        started = asyncio.Event()
        release = asyncio.Event()
        delivered: list[str] = []

        final_stream = FinalOnceStream()
        fresh = FakeAsrStream()
        svc = FakeAsrService([fresh])
        orch = _make_orch(svc)
        orch._asr_stream = final_stream

        async def _slow_send(text, *, is_final, transcription_id="", images=None):
            started.set()
            await release.wait()
            delivered.append(text)

        orch._send_transcription = _slow_send

        await _feed_frame(orch)  # feed 成功 → 建 poll 任务
        await asyncio.wait_for(started.wait(), timeout=1.0)  # 已进入在途送达

        # 触发恢复：旧流 feed 失败 → 重开新流 → 强制换 poll 任务（cancel 在途任务）
        final_stream._feed_error = ProviderError("closed", provider="fake")
        await _feed_frame(orch)
        assert orch._asr_stream is fresh

        # 旧任务已取消，但在途送达被 shield 保护：释放后必须完成并落库
        release.set()
        await asyncio.sleep(0.05)
        assert delivered == ["你好世界"]
        assert any(m["content"] == "你好世界" for m in orch._chat_store.messages)
        await asyncio.sleep(0.01)

    @pytest.mark.asyncio
    async def test_detached_final_delivery_attributes_to_old_session(self):
        """后台补跑的送达必须归属旧会话：同连接新 session.start 换掉
        self._ctx/_history/_pipeline_mode 后，不得把上一会话的语句写进新会话。"""
        from session.context import SessionContext
        from session.history import WorkingMemory
        from session.state_machine import SessionStateMachine

        started = asyncio.Event()
        release = asyncio.Event()
        delivered: list[str] = []

        final_stream = FinalOnceStream()
        fresh = FakeAsrStream()
        orch = _make_orch(FakeAsrService([fresh]))
        orch._asr_stream = final_stream

        async def _slow_send(text, *, is_final, transcription_id="", images=None):
            started.set()
            await release.wait()
            delivered.append(text)

        orch._send_transcription = _slow_send
        old_history = orch._history

        await _feed_frame(orch)
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # 取消 poll 任务（恢复帧），送达转入后台
        final_stream._feed_error = ProviderError("closed", provider="fake")
        await _feed_frame(orch)
        assert orch._asr_stream is fresh

        # 模拟同连接新 session.start：会话态全部换新（mode 也换，验证模式捕获）
        sm = SessionStateMachine()
        sm.state = SessionState.LISTENING
        new_ctx = SessionContext(
            session_id="test-sid-2", device_id="dev-2", user_id="user-1",
            sm=sm, enable_video=False, audio_sample_rate=16000,
        )
        new_history = WorkingMemory(max_turns=10)
        orch._ctx = new_ctx
        orch._history = new_history
        orch._pipeline_mode = "translate"  # 若读运行时 mode，旧会话落库会被跳过

        release.set()
        await asyncio.sleep(0.05)

        # 归属旧会话：chat_store 记旧 session_id/device_id；旧 WM 有、新 WM 无
        assert delivered == ["你好世界"]
        assert orch._chat_store.messages[-1]["session_id"] == "test-sid"
        assert orch._chat_store.messages[-1]["device_uuid"] == "dev-1"
        assert [m["content"] for m in old_history.get_recent()] == ["你好世界"]
        assert new_history.get_recent() == []
        await asyncio.sleep(0.01)
