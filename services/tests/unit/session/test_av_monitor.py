from __future__ import annotations

import asyncio
import time

import pytest

from avdetector.base import AvCheckResult, AvCheckTimeout
from avdetector.service import AvDetectorService
from session.av_detector import AvDetectorMonitor
from shared.config.models import AvDetectorConfig
from shared.utils.errors import ProviderError
from tests.unit.avdetector.fakes.fake_avdetector_provider import (
    FakeAvDetectorProvider,
    FakeAvDetectorStream,
)


class BlockingStream(FakeAvDetectorStream):
    """feed_audio 阻塞在 gate 上：验证热路径投递与队列满丢弃（不靠时序运气）。"""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def feed_audio(self, pcm: bytes) -> None:
        self.calls.append("audio")
        await self.gate.wait()
        self.feeds.append(pcm)


def _svc(provider: FakeAvDetectorProvider) -> AvDetectorService:
    """monitor 的 service 契约 = AvDetectorService（open 返回 (stream, name) 元组）。"""
    return AvDetectorService(active=provider, fallbacks=[])


def _cfg(**over: object) -> AvDetectorConfig:
    base: dict = {
        "enabled": True,
        "active": "fake",
        "check_timeout_s": 2.0,
        "audio_commit_interval_s": 60.0,   # 默认不触发静默 commit（用例单独覆盖）
        "check_min_interval_s": 0.0,       # 默认不节流（间隔用例单独覆盖）
        "video_check_interval_s": 0.0,
        "reopen_interval_s": 3600.0,
    }
    base.update(over)
    return AvDetectorConfig(**base)


async def _wait_until(cond, timeout: float = 10.0) -> bool:
    # 10s 预算：重负载（热重载/并行 pytest/CPU 争抢）下 2s 与 5s 均出现过
    # 等待预算耗尽（测试等待，非语义——忙循环实测可复现）
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


class TestPipeline:
    async def test_feed_pipeline_order_and_single_pipe(self) -> None:
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(service=_svc(provider), session_id="s1", cfg=_cfg())
        m.start()
        try:
            m.feed_audio(b"a1")
            m.feed_frame(b"jpegdata", width=640, height=480, orientation="landscape",
                         camera_facing="back")
            assert m.request_video_check() is True
            assert await _wait_until(lambda: provider.stream.checks > 0)
            calls = provider.stream.calls
            assert calls.index("audio") < calls.index("image") < calls.index("check")  # 先音频后图
            assert provider.stream.feeds == [b"a1"]
            assert provider.stream.images[0][0] == b"jpegdata"
        finally:
            await m.close()

    async def test_audio_check_pure_sound_no_frame(self) -> None:
        """[P6.9] 音频补充检查纯声音：即使有缓存帧也不附帧（省画面 token；
        回复/投递语义一致——附帧却按声音投递是双输中间态）。"""
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg())
        m.start()
        try:
            m.feed_audio(b"a")
            m.feed_frame(b"J", width=8, height=8)
            assert m.request_audio_check() is True
            assert await _wait_until(lambda: provider.stream.checks >= 1)
            assert provider.stream.images == []  # 音频通道零上图
        finally:
            await m.close()

    async def test_flood_drops_oldest_not_blocking(self) -> None:
        stream = BlockingStream()
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg(), queue_maxsize=4)
        m.start()
        try:
            for i in range(12):
                m.feed_audio(bytes([i]))  # 同步投递，绝不被阻塞
            assert m.dropped_audio >= 5
            stream.gate.set()
        finally:
            await m.close()


class TestEventDelivery:
    async def test_video_event_delivered_with_frame(self) -> None:
        stream = FakeAvDetectorStream(results=[
            AvCheckResult(text='{"event":"normal","desc":"画面中出现一个机器人"}',
                          transcripts=[""]),
        ])
        provider = FakeAvDetectorProvider(stream=stream)
        events: list[tuple] = []

        async def on_event(kind, desc, label, frame):
            events.append((kind, desc, label, frame))

        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg(), on_event=on_event)
        m.start()
        try:
            m.feed_audio(b"a")  # 满足"先音频后图"窗口约束
            m.feed_frame(b"J", width=8, height=8, orientation="landscape", camera_facing="back")
            assert m.request_video_check() is True
            assert await _wait_until(lambda: len(events) == 1)
            kind, desc, label, frame = events[0]
            assert kind == "画面"
            assert label == "normal"
            assert "机器人" in desc
            assert frame == (b"J", 8, 8, "landscape", "back")
        finally:
            await m.close()

    async def test_danger_event_passed_through(self) -> None:
        stream = FakeAvDetectorStream(results=[
            AvCheckResult(text='{"event":"danger","desc":"有人摔倒了"}', transcripts=[""]),
        ])
        provider = FakeAvDetectorProvider(stream=stream)
        events: list[tuple] = []

        async def on_event(kind, desc, label, frame):
            events.append((kind, desc, label, frame))

        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg(), on_event=on_event)
        m.start()
        try:
            m.feed_audio(b"a")  # 满足"先音频后图"窗口约束
            m.feed_frame(b"J", width=8, height=8)
            m.request_video_check()
            assert await _wait_until(lambda: len(events) == 1)
            assert events[0][2] == "danger"  # [R15] 冷却豁免由 orchestrator 依此判定
        finally:
            await m.close()

    async def test_none_and_unparsable_dropped(self) -> None:
        stream = FakeAvDetectorStream(results=[
            AvCheckResult(text="NONE"),
            AvCheckResult(text="garbage 123 {incomplete"),
        ])
        provider = FakeAvDetectorProvider(stream=stream)
        events: list[tuple] = []

        async def on_event(kind, desc, label, frame):
            events.append((kind, desc, label, frame))

        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg(), on_event=on_event)
        m.start()
        try:
            m.request_audio_check()
            m.request_audio_check()
            assert await _wait_until(lambda: stream.checks == 2)
            await asyncio.sleep(0.05)
            assert events == []
        finally:
            await m.close()

    async def test_onomatopoeia_transcript_passes_event(self) -> None:
        """[P6.1] 真咳嗽被转写成单字重复拟声（'咳咳。'）→ 不再误杀，事件放行。"""
        stream = FakeAvDetectorStream(results=[
            AvCheckResult(text='{"event":"normal","desc":"听到明显的咳嗽声"}',
                          transcripts=["咳咳。"]),
        ])
        provider = FakeAvDetectorProvider(stream=stream)
        events: list[tuple] = []

        async def on_event(kind, desc, label, frame):
            events.append((kind, desc, label, frame))

        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg(), on_event=on_event)
        m.start()
        try:
            m.request_audio_check()
            assert await _wait_until(lambda: len(events) == 1)
            assert events[0][2] == "normal"
        finally:
            await m.close()

    async def test_speech_transcript_veto(self) -> None:
        stream = FakeAvDetectorStream(results=[
            AvCheckResult(text='{"event":"normal","desc":"听到咳嗽"}',
                          transcripts=["你好，这是一个测试"]),
        ])
        provider = FakeAvDetectorProvider(stream=stream)
        events: list[tuple] = []

        async def on_event(kind, desc, label, frame):
            events.append((kind, desc, label, frame))

        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg(), on_event=on_event)
        m.start()
        try:
            m.request_audio_check()
            assert await _wait_until(lambda: stream.checks == 1)
            await asyncio.sleep(0.05)
            assert events == []  # 说话窗口 → 否决
        finally:
            await m.close()

    async def test_fenced_json_tolerated(self) -> None:
        stream = FakeAvDetectorStream(results=[
            AvCheckResult(text='```json\n{"event":"normal","desc":"听到两声咳嗽"}\n```',
                          transcripts=[""]),
        ])
        provider = FakeAvDetectorProvider(stream=stream)
        events: list[tuple] = []

        async def on_event(kind, desc, label, frame):
            events.append((kind, desc, label, frame))

        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg(), on_event=on_event)
        m.start()
        try:
            m.request_audio_check()
            assert await _wait_until(lambda: len(events) == 1)
            assert events[0][2] == "normal"
            assert events[0][0] == "声音"  # 无缓存帧 → 声音来源
        finally:
            await m.close()


class TestTranscriptVetoJudgement:
    """[P6.1]/[P6.13] 否决判定纯函数：单字重复拟声与 2–4 种字符短转写放行、成句级转写否决。"""

    @pytest.mark.parametrize("text,expected", [
        ("", False), ("咳咳。", False), ("咳", False), ("呵呵", False), ("啊啊啊", False),
        ("咳·", False), ("咳　", False), ("咳？！", False),  # [P6.2] 未见标点/全角空格/组合标点
        ("啊嚏", False), ("阿嚏。", False), ("哈哈", False), ("嗯嗯", False),  # [P6.4] 两字拟声豁免
        ("好的", False), ("咳咳，你好", False), ("OK", False), ("哈喽", False),  # [P6.13] 2–4 种字符放行
        ("一二三四", False), ("一二三四五", True),  # [P6.13] 阈值边界：4 种放行 / 5 种否决
        ("你好，这是一个测试", True), ("啊嚏，不好意思", True),  # 成句级转写仍否决（混合词句不豁免）
    ])
    def test_is_speech_like_text(self, text: str, expected: bool) -> None:
        from session.av_detector import is_speech_like_text

        assert is_speech_like_text(text) is expected


class TestParseReply:
    """event 固定枚举 normal/danger；大小写归一；danger 空 desc 安全兜底。"""

    def test_normal_and_danger(self) -> None:
        from session.av_detector import _parse_reply

        assert _parse_reply('{"event":"normal","desc":"咳嗽"}') == {
            "event": "normal", "desc": "咳嗽",
        }
        assert _parse_reply('{"event":"danger","desc":"有人摔倒"}') == {
            "event": "danger", "desc": "有人摔倒",
        }

    @pytest.mark.parametrize("event", ["DANGER", "Danger", " danger "])
    def test_danger_case_whitespace_normalized(self, event: str) -> None:
        from session.av_detector import _parse_reply

        assert _parse_reply(f'{{"event":"{event}","desc":"x"}}') == {
            "event": "danger", "desc": "x",
        }

    @pytest.mark.parametrize("text", [
        "", "NONE", "none", "garbage", "{}",
        '{"event":"咳嗽","desc":"听到咳嗽"}',
        '{"event":"Dangerous behavior","desc":"有人摔倒"}',
    ])
    def test_invalid_replies_dropped(self, text: str) -> None:
        from session.av_detector import _parse_reply

        assert _parse_reply(text) is None

    def test_normal_empty_desc_dropped(self) -> None:
        from session.av_detector import _parse_reply

        assert _parse_reply('{"event":"normal","desc":"  "}') is None

    def test_danger_empty_desc_kept_for_label_only_fallback(self) -> None:
        from session.av_detector import _parse_reply

        assert _parse_reply('{"event":"danger","desc":""}') == {
            "event": "danger", "desc": "",
        }


class TestInvalidReplyLogging:
    async def test_invalid_event_warns_with_raw_reply(self, caplog) -> None:
        raw = '{"event":"咳嗽","desc":"听到咳嗽"}'
        stream = FakeAvDetectorStream(results=[AvCheckResult(text=raw, transcripts=[""])])
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(service=_svc(provider), session_id="sid-log", cfg=_cfg())
        m.start()
        try:
            with caplog.at_level("WARNING", logger="livachat.session.av_detector"):
                m.request_audio_check()
                assert await _wait_until(lambda: stream.checks == 1)
                await asyncio.sleep(0.05)
            assert "invalid event/json" in caplog.text
            assert raw in caplog.text
            assert any(
                getattr(r, "session_id", "") == "sid-log" for r in caplog.records
            )
        finally:
            await m.close()

    async def test_danger_empty_desc_warns_but_delivers(self, caplog) -> None:
        stream = FakeAvDetectorStream(results=[
            AvCheckResult(text='{"event":"danger","desc":""}', transcripts=[""]),
        ])
        provider = FakeAvDetectorProvider(stream=stream)
        events: list[tuple] = []

        async def on_event(kind, desc, label, frame):
            events.append((kind, desc, label, frame))

        m = AvDetectorMonitor(service=_svc(provider), session_id="sid-danger",
                              cfg=_cfg(), on_event=on_event)
        m.start()
        try:
            with caplog.at_level("WARNING", logger="livachat.session.av_detector"):
                m.request_audio_check()
                assert await _wait_until(lambda: len(events) == 1)
            assert events[0][2] == "danger" and events[0][1] == ""
            assert "danger event without desc" in caplog.text
        finally:
            await m.close()


class TestScheduling:
    async def test_audio_check_interval_gate(self) -> None:
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s", cfg=_cfg(check_min_interval_s=0.2)
        )
        m.start()
        try:
            assert m.request_audio_check() is True
            assert m.request_audio_check() is False  # 间隔内被压
            assert await _wait_until(lambda: provider.stream.checks == 1)
            await asyncio.sleep(0.25)
            assert m.request_audio_check() is True
        finally:
            await m.close()

    async def test_video_keeps_own_cadence_not_starved_by_audio_checks(self) -> None:
        """[第四轮修复] 视频主通道独立节拍（[R13]）：音频检查不挤压视频；音频仍受全局间隔。"""
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s",
            cfg=_cfg(check_min_interval_s=0.5, video_check_interval_s=0.1),
        )
        m.start()
        try:
            m.feed_audio(b"a")
            m.feed_frame(b"J", width=8, height=8)
            assert m.request_audio_check() is True   # 音频检查受理（推进全局时间戳）
            await asyncio.sleep(0.15)                # > video 0.1，< audio 0.5
            assert m.request_video_check() is True   # 视频不被音频时间戳压制
            assert m.request_audio_check() is False  # 音频补充仍受全局最小间隔约束
            assert await _wait_until(lambda: provider.stream.checks >= 2)
        finally:
            await m.close()

    async def test_video_check_requires_frame_slot(self) -> None:
        """[R16] 2026-10-09 修订为 K=1 帧槽：槽空不触发；入槽后可触发，检查**只取槽内最新帧**。"""
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg())
        m.start()
        try:
            assert m.request_video_check() is False  # 槽空不触发
            m.feed_frame(b"J1", width=8, height=8)
            assert m.request_video_check() is True
            m.feed_frame(b"J2", width=8, height=8)   # 覆写槽（K=1）
            assert await _wait_until(lambda: provider.stream.checks >= 1)
            assert provider.stream.images == [(b"J2", 8, 8)]  # 只上行最新一帧
        finally:
            await m.close()

    async def test_forced_video_check_bypasses_throttle(self) -> None:
        """D8：开窗基线检查强制绕过节流；ready + 帧槽仍必需，时间戳照常更新。"""
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s",
            cfg=_cfg(check_min_interval_s=60.0, video_check_interval_s=60.0),
        )
        m.start()
        try:
            assert m.request_video_check(force=True) is False  # 槽空仍不触发
            m.feed_frame(b"J", width=8, height=8)
            assert m.request_video_check() is True
            assert m.request_video_check() is False           # 常规路径被 60s 节流
            assert m.request_video_check(force=True) is True  # 强制绕过
            assert m._last_check_at == m._last_video_check_at
            assert await _wait_until(lambda: provider.stream.checks >= 2)
        finally:
            await m.close()

    async def test_silent_commit_window_discipline(self) -> None:
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s", cfg=_cfg(audio_commit_interval_s=0.05)
        )
        m.start()
        try:
            m.feed_audio(b"x" * 16)
            assert await _wait_until(lambda: provider.stream.commits >= 1)
            # 窗口清零后不再重复提交（无新音频）
            await asyncio.sleep(0.15)
            assert provider.stream.commits == 1
        finally:
            await m.close()


class TestImageAttach:
    """[P6] 附帧不再受窗口音频拦截（"先音频后图"由 provider 静音自愈承担）；
    [P6.14] 仅拒图**限时停图**（默认 300s，到期后下一个视频检查自动恢复）拦截附帧。"""

    async def test_image_attached_without_window_audio(self) -> None:
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg())
        m.start()
        try:
            # 全新流、从未喂过音频 → 仍附帧（自愈在 provider 层：静音补充）
            m.feed_frame(b"J", width=8, height=8)
            assert m.request_video_check() is True
            assert await _wait_until(lambda: provider.stream.checks >= 1)
            assert "image" in provider.stream.calls
        finally:
            await m.close()

    async def test_image_rejection_disables_images_not_fuse(self) -> None:
        """[P6.14] 拒图 → 停图限时（用例缩短为 0.2s）：停图期内视频检查纯音频、
        不熔断；到期后下一个视频检查自动恢复附帧、时限清零。"""
        class ImageRejectingStream(FakeAvDetectorStream):
            async def check(self, *, timeout_s):
                self.calls.append("check")
                self.checks += 1
                if self.images and self.checks == 1:
                    raise ProviderError(
                        "avdetector check error: Invalid image, check input image format",
                        provider="alibaba_avdetector",
                    )
                return AvCheckResult(text="NONE")

        stream = ImageRejectingStream()
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s",
            cfg=_cfg(image_disable_cooldown_s=0.2), fail_cooldown_s=0.0,
        )
        m.start()
        try:
            m.feed_audio(b"a")
            m.feed_frame(b"J", width=8, height=8)
            assert m.request_video_check() is True
            assert await _wait_until(lambda: stream.checks == 1)
            assert await _wait_until(lambda: m._images_disabled_until > 0.0)  # 停图时限置位
            assert not m.fused  # 单次失败不熔断
            # 停图期内：视频检查退化纯音频、不上图
            n_images = len(stream.images)
            m.feed_frame(b"J2", width=8, height=8)
            assert m.request_video_check() is True
            assert await _wait_until(lambda: stream.checks == 2)
            assert len(stream.images) == n_images
            # 越过时限：下一个视频检查恢复附帧、时限清零
            await asyncio.sleep(0.25)
            m.feed_frame(b"J3", width=8, height=8)
            assert m.request_video_check() is True
            assert await _wait_until(lambda: stream.checks == 3)
            assert len(stream.images) == n_images + 1
            assert m._images_disabled_until == 0.0
            assert not m.fused
        finally:
            await m.close()

    async def test_image_disable_retry_rejected_retimes_not_fuse(self) -> None:
        """[P6.14] 到期恢复重试再被拒 → 停图时限重新计算、仍不熔断
        （失败计数 1↔0 振荡，永不累计到熔断阈值）。"""
        class AlwaysRejectingStream(FakeAvDetectorStream):
            async def check(self, *, timeout_s):
                self.calls.append("check")
                self.checks += 1
                if self.images:
                    raise ProviderError(
                        "avdetector check error: Invalid image, check input image format",
                        provider="alibaba_avdetector",
                    )
                return AvCheckResult(text="NONE")

        stream = AlwaysRejectingStream()
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s",
            cfg=_cfg(image_disable_cooldown_s=0.2), fail_cooldown_s=0.0,
        )
        m.start()
        try:
            m.feed_audio(b"a")
            m.feed_frame(b"J", width=8, height=8)
            assert m.request_video_check() is True
            assert await _wait_until(lambda: stream.checks == 1)
            assert await _wait_until(lambda: m._images_disabled_until > 0.0)
            first_until = m._images_disabled_until
            n_images = len(stream.images)
            # 越过时限 → 恢复附帧重试 → 再拒 → 时限重新置位（非旧值、非清零）
            await asyncio.sleep(0.25)
            m.feed_frame(b"J2", width=8, height=8)
            assert m.request_video_check() is True
            assert await _wait_until(lambda: stream.checks == 2)
            assert len(stream.images) == n_images + 1  # 重试确实附了新帧
            assert await _wait_until(
                lambda: m._images_disabled_until not in (0.0, first_until)
            )
            assert not m.fused  # 振荡不熔断
        finally:
            await m.close()

    async def test_image_feed_failure_runs_audio_only_check(self) -> None:
        class ImageFeedFailStream(FakeAvDetectorStream):
            async def feed_image(self, jpeg, *, width, height):
                raise ProviderError("image send failed after reconnect", provider="x")

        stream = ImageFeedFailStream()
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg())
        m.start()
        try:
            m.feed_audio(b"a")
            m.feed_frame(b"J", width=8, height=8)
            assert m.request_video_check() is True
            assert await _wait_until(lambda: stream.checks == 1)  # 上图失败不拖垮本轮
            assert m._stream is not None  # 未降级
        finally:
            await m.close()


class TestTimeoutPolicy:
    """[P2] review 修复：超时≠连接故障（D5）——只丢结果不计熔断；连续 2 次才降级。"""

    async def test_single_timeout_drops_without_degrade(self) -> None:
        stream = FakeAvDetectorStream(raise_on_check=AvCheckTimeout("t", provider="x"))
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg())
        m.start()
        try:
            m.request_audio_check()
            assert await _wait_until(lambda: stream.checks == 1)
            await asyncio.sleep(0.05)
            assert m._stream is not None           # 未降级
            assert m._consecutive_failures == 0    # 不计熔断失败
            assert m._consecutive_timeouts == 1
            assert not m.fused
        finally:
            await m.close()

    async def test_two_consecutive_timeouts_degrade_but_not_fuse(self) -> None:
        stream = FakeAvDetectorStream(raise_on_check=AvCheckTimeout("t", provider="x"))
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s", cfg=_cfg(), fail_cooldown_s=0.01
        )
        m.start()
        try:
            m.request_audio_check()
            assert await _wait_until(lambda: stream.checks == 1)
            m.request_audio_check()
            assert await _wait_until(lambda: stream.checks == 2)
            assert await _wait_until(lambda: m._stream is None)  # 第 2 次超时 → 降级
            assert m._consecutive_failures == 1                  # 仅一次降级失败
            assert not m.fused
        finally:
            await m.close()


class TestFailurePolicy:
    async def test_successful_open_resets_failure_count(self) -> None:
        """[P6.11] 成功开流 = 连接已恢复证据 → 失败计数清零（防跨开流累计误熔断）。"""
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg())
        m._consecutive_failures = 2  # 模拟此前两次瞬时失败（均已各自恢复）
        stream = await m._ensure_stream()
        assert stream is not None
        assert m._consecutive_failures == 0
        assert not m.fused

    async def test_failure_count_not_accumulated_across_reopens(self) -> None:
        """[P6.11] 跨成功开流的失败不累计（用户 review 中危 5 序列）：feed 失败 →
        降级(1) → 重开成功（清零）→ 再失败仍计 1——而非累计逼近熔断。"""
        class FeedFailStream(FakeAvDetectorStream):
            async def feed_audio(self, pcm: bytes) -> None:
                raise ProviderError("send failed", provider="x")

        stream = FeedFailStream()
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s", cfg=_cfg(), fail_cooldown_s=0.01
        )
        m.start()
        try:
            m.feed_audio(b"a")
            assert await _wait_until(lambda: m._consecutive_failures == 1)
            await asyncio.sleep(0.03)  # 过冷却窗口
            m.feed_audio(b"b")
            assert await _wait_until(
                lambda: provider.open_calls >= 2 and m._stream is None
            )
            assert m._consecutive_failures == 1  # 旧累计语义此处会是 2
            assert not m.fused
        finally:
            await m.close()

    async def test_open_failure_cooldown_then_fuse(self) -> None:
        provider = FakeAvDetectorProvider(raise_on_open=ProviderError("boom", provider="x"))
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s", cfg=_cfg(), fail_cooldown_s=0.01
        )
        m.start()
        try:
            for _ in range(3):
                m.feed_audio(b"x")
                await asyncio.sleep(0.05)
            assert await _wait_until(lambda: m.fused)
            assert provider.open_calls == 3  # [R10] N=3
            m.feed_audio(b"x")
            m.request_audio_check()
            await asyncio.sleep(0.1)
            assert provider.open_calls == 3  # 熔断后不再尝试
            assert m.ready() is False
        finally:
            await m.close()

    async def test_check_failure_degrades_and_recovers_counter(self) -> None:
        stream = FakeAvDetectorStream(raise_on_check=ProviderError("net", provider="x"))
        provider = FakeAvDetectorProvider(stream=stream)
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s", cfg=_cfg(), fail_cooldown_s=0.01
        )
        m.start()
        try:
            m.request_audio_check()
            assert await _wait_until(lambda: stream.checks == 1)
            assert await _wait_until(lambda: m._stream is None)  # 失败 → 降级置空流
            assert not m.fused  # 单次失败不熔断
        finally:
            await m.close()

    async def test_reopen_loop(self) -> None:
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(
            service=_svc(provider), session_id="s", cfg=_cfg(reopen_interval_s=0.05)
        )
        m.start()
        try:
            m.feed_audio(b"x")
            assert await _wait_until(lambda: provider.stream.reopened >= 1)
        finally:
            await m.close()

    async def test_close_releases_stream_and_tasks(self) -> None:
        provider = FakeAvDetectorProvider()
        m = AvDetectorMonitor(service=_svc(provider), session_id="s", cfg=_cfg())
        m.start()
        m.feed_audio(b"x")
        assert await _wait_until(lambda: provider.open_calls == 1)
        await m.close()
        assert provider.stream.closed
        assert m._pipeline_task is None
        assert m.ready() is False
