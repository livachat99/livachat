"""全模态（omni）模式：会话内切换、失败语义、路由与引擎事件映射。

设计见 docs/fd_pipeline_omni_mode.md（§4 D3/D4/D6/D6b/D7）。原连接级 OmniOrchestrator
及其测试已删除（§4 D2）；本文件覆盖其行为在"引擎 + DialogOrchestrator 第四模式"下的等价性。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json

import pytest

from media.opus import OpusEncoder
from omni.service import OmniService
from session.context import SessionContext
from session.history import WorkingMemory
from session.interrupt import InterruptController
from session.manager import SessionManager
from session.omni_mode import OmniModeEngine
from session.orchestrator import DialogOrchestrator
from tests.integration.orchestrator.conftest import (
    FakeChatStore,
    FakeLlmService,
    FakeRecordingSink,
    _FakeHandler,
)
from tests.unit.omni.fakes.fake_omni_provider import FakeOmniProvider, FakeOmniStream

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _HangingStream(FakeOmniStream):
    """events() 永不结束：模拟一条活着的 omni 流（pump 保持运行）。"""

    def events(self):
        async def _gen():
            while True:
                await asyncio.sleep(3600)
                yield  # pragma: no cover —— 挂起不产出
        return _gen()


class _HangingProvider(FakeOmniProvider):
    def __init__(self, name: str = "fake_omni") -> None:
        super().__init__(name, events=[])
        self.open_count = 0
        self.stream = _HangingStream([])

    async def open(self, *, sample_rate, system_prompt="", voice="", modalities=("text", "audio"),
                   generation_opts=None):
        self.open_count += 1
        self.last_open_kwargs = {
            "sample_rate": sample_rate, "system_prompt": system_prompt, "voice": voice,
            "modalities": modalities, "generation_opts": generation_opts,
        }
        return self.stream


class _CountingScriptedProvider(FakeOmniProvider):
    """计数 open 次数 + 每次返回脚本化（会自然结束的）流 —— 流终结/懒重开用例用。"""

    def __init__(self, events: list) -> None:
        super().__init__("fake_omni", events=events)
        self.open_count = 0

    async def open(self, *, sample_rate, system_prompt="", voice="", modalities=("text", "audio"),
                   generation_opts=None):
        self.open_count += 1
        self.last_open_kwargs = {
            "sample_rate": sample_rate, "system_prompt": system_prompt, "voice": voice,
            "modalities": modalities, "generation_opts": generation_opts,
        }
        return FakeOmniStream(list(self._events))


class _FailingProvider(FakeOmniProvider):
    async def open(self, **kwargs):
        from shared.utils.errors import ProviderError
        raise ProviderError("boom", provider="fail")


class _CountingAsrStream:
    def __init__(self) -> None:
        self._final_text = ""

    async def feed(self, pcm) -> None: ...

    async def results(self):
        return
        yield  # pragma: no cover

    async def final(self) -> str:
        return self._final_text

    @property
    def final_text(self) -> str:
        return self._final_text

    async def reset(self) -> None:
        self._final_text = ""

    async def close(self) -> None: ...


class _CountingAsrService:
    def __init__(self) -> None:
        self.open_count = 0

    async def open(self, *, sample_rate, language=None, interim_results=True):
        self.open_count += 1
        return _CountingAsrStream()


class _NullTtsStream:
    async def feed_text(self, text) -> None: ...

    def audio(self):
        async def _empty():
            if False:
                yield  # pragma: no cover
        return _empty()

    async def flush(self) -> None: ...

    async def close(self) -> None: ...


class _CountingTtsService:
    def __init__(self) -> None:
        self.open_count = 0

    async def open(self, *, voice, sample_rate, audio_format="pcm"):
        self.open_count += 1
        return _NullTtsStream()


def _parse(handler: _FakeHandler, since: int = 0) -> list[dict]:
    out = []
    for raw in handler.outbound[since:]:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def _types(frames: list[dict]) -> list[str]:
    return [f.get("type", "?") for f in frames]


def _audio_msg(opus: bytes):
    from unittest.mock import MagicMock
    msg = MagicMock()
    msg.payload.data = base64.b64encode(opus).decode()
    msg.payload.vad_state.value = "speech"
    return msg


def _video_msg(jpeg: bytes, *, text: str = "", width: int = 640, height: int = 360):
    from unittest.mock import MagicMock
    msg = MagicMock()
    msg.payload.data = base64.b64encode(jpeg).decode()
    msg.payload.text = text
    msg.payload.frame_id = 7
    msg.payload.width = width
    msg.payload.height = height
    msg.payload.orientation = "PORTRAIT_0"
    msg.payload.camera_facing = "front"
    # turn_id 必须显式置 None（MagicMock 会凭空生成属性值 → 误入 §8 首帧直起轮分支）
    msg.payload.turn_id = None
    return msg


_DEFAULT_SERVICE = object()


def _make_orchestrator(handler: _FakeHandler, *, provider, asr=None, tts=None,
                       recording_sink=None, omni_service=_DEFAULT_SERVICE, pipeline_sample_rate=16000):
    svc = (OmniService(active=provider, fallbacks=[])
           if omni_service is _DEFAULT_SERVICE else omni_service)
    return DialogOrchestrator(
        handler=handler,
        codec=handler._codec,
        session_mgr=SessionManager(),
        asr_service=asr,
        llm_service=FakeLlmService(),
        tts_service=tts,
        system_prompt="You are helpful.",
        translate_prompt="You are a translator.",
        chat_store=FakeChatStore(),
        recording_sink=recording_sink,
        omni_service=svc,
        omni_system_prompt="You are omni.",
        omni_voice="Tina",
        omni_generation_opts={"temperature": 0.8},
        pipeline_sample_rate=pipeline_sample_rate,
    )


async def _start_session(orch: DialogOrchestrator, handler: _FakeHandler, *, enable_video=False) -> None:
    from livachat_protocol.generated.client_message import ClientSessionStart
    from livachat_protocol.generated.protocol_models import (
        ClientSessionStartConfig,
        ClientSessionStartPayload,
    )

    run_task = asyncio.create_task(orch.run())
    handler._orchestrator_run = run_task  # 供 finally 取消
    handler._inbound.put_nowait(
        ClientSessionStart(
            seq=1, timestamp=1000,
            payload=ClientSessionStartPayload(
                session_id=None,
                config=ClientSessionStartConfig(
                    enable_video=enable_video, audio_sample_rate=16000,
                    vad_sensitivity="medium", client_version="android-1.0.0",
                ),
            ),
        )
    )
    for _ in range(100):
        if any("session.ready" in f for f in handler.outbound):
            return
        await asyncio.sleep(0.02)
    raise AssertionError("session.ready 未到达")


async def _send_change(handler: _FakeHandler, mode: str, *, usecase: str | None = None,
                       my_language: str | None = None, local_language: str | None = None) -> None:
    from livachat_protocol.generated.client_message import ClientSessionChange
    from livachat_protocol.generated.protocol_models import ClientSessionChangePayload

    kwargs: dict = {"mode": mode}
    if usecase is not None:
        kwargs["usecase"] = usecase
    if my_language is not None:
        kwargs["my_language"] = my_language
    if local_language is not None:
        kwargs["local_language"] = local_language
    handler._inbound.put_nowait(
        ClientSessionChange(seq=2, timestamp=2000, payload=ClientSessionChangePayload(**kwargs))
    )


async def _wait_for(handler: _FakeHandler, pred, *, timeout: float = 2.0, since: int = 0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frames = _parse(handler, since)
        if pred(frames):
            return frames
        await asyncio.sleep(0.01)
    return _parse(handler, since)


async def _stop(handler: _FakeHandler) -> None:
    handler.stop()
    task = getattr(handler, "_orchestrator_run", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# ---------------------------------------------------------------------------
# 会话内切换
# ---------------------------------------------------------------------------


class TestEnterOmniMode:
    async def test_enter_success_echo_and_engine_open_kwargs(self) -> None:
        handler = _FakeHandler()
        provider = _HangingProvider()
        orch = _make_orchestrator(handler, provider=provider,
                                  asr=_CountingAsrService(), tts=_CountingTtsService())
        await _start_session(orch, handler)
        mark = len(handler.outbound)

        await _send_change(handler, "omni")
        frames = await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs), since=mark)

        assert orch._omni_active is True
        assert orch._pipeline_mode == "omni"
        assert orch._asr_stream is None and orch._tts_stream is None  # ASR/TTS 已释放
        assert provider.open_count == 1
        assert provider.last_open_kwargs["sample_rate"] == 16000
        assert provider.last_open_kwargs["voice"] == "Tina"
        assert provider.last_open_kwargs["system_prompt"] == "You are omni."
        assert provider.last_open_kwargs["generation_opts"] == {"temperature": 0.8}
        assert orch._ctx.llm_provider == "fake_omni"

        changed = [f for f in frames if f.get("type") == "session.changed"]
        assert changed and changed[0]["payload"]["mode"] == "omni"
        assert "my_language" not in changed[0]["payload"] or changed[0]["payload"].get("my_language") is None
        statuses = [f for f in frames if f.get("type") == "status"]
        assert statuses and statuses[-1]["payload"]["reason"] == "mode_changed"
        await _stop(handler)

    async def test_uses_pipeline_sample_rate_not_session_declared(self) -> None:
        """D5：开流采样率取编解码器口径（pipeline_sample_rate），不是客户端声明值。"""
        handler = _FakeHandler()
        provider = _HangingProvider()
        orch = _make_orchestrator(handler, provider=provider, pipeline_sample_rate=16000)
        await _start_session(orch, handler)

        # 伪造客户端声明 24 kHz（真实客户端默认 16 kHz；此处只验证取值来源）
        orch._ctx.audio_sample_rate = 24000
        await _send_change(handler, "omni")
        await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs))
        assert provider.last_open_kwargs["sample_rate"] == 16000
        await _stop(handler)

    async def test_service_missing_rejects_with_1009_and_no_echo(self) -> None:
        handler = _FakeHandler()
        orch = _make_orchestrator(handler, provider=_HangingProvider(), omni_service=None)
        await _start_session(orch, handler)
        mark = len(handler.outbound)

        await _send_change(handler, "omni")
        frames = await _wait_for(handler, lambda fs: any(f.get("type") == "error" for f in fs), since=mark)

        errs = [f for f in frames if f.get("type") == "error"]
        assert errs and errs[0]["payload"]["code"] == 1009
        assert not any(f.get("type") == "session.changed" for f in frames)
        assert orch._omni_active is False
        assert orch._pipeline_mode == "full"
        await _stop(handler)

    async def test_open_failure_rejects_1009_and_keeps_dispatch_alive(self) -> None:
        """enter-failure：open 抛错必须被吞在 _enter_omni_mode 内——否则 dispatch 主循环死亡。"""
        handler = _FakeHandler()
        orch = _make_orchestrator(handler, provider=_FailingProvider())
        await _start_session(orch, handler)
        mark = len(handler.outbound)

        await _send_change(handler, "omni")
        frames = await _wait_for(handler, lambda fs: any(f.get("type") == "error" for f in fs), since=mark)
        errs = [f for f in frames if f.get("type") == "error"]
        assert errs and errs[0]["payload"]["code"] == 1009
        assert not any(f.get("type") == "session.changed" for f in frames)
        assert orch._omni_active is False and orch._omni_engine is None
        assert orch._pipeline_mode == "full"

        # 主循环存活：后续 session.change(chat) 仍被处理
        mark2 = len(handler.outbound)
        await _send_change(handler, "chat")
        frames2 = await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs), since=mark2)
        assert [f for f in frames2 if f.get("type") == "session.changed"]
        await _stop(handler)

    async def test_resend_omni_is_idempotent(self) -> None:
        handler = _FakeHandler()
        provider = _HangingProvider()
        orch = _make_orchestrator(handler, provider=provider)
        await _start_session(orch, handler)

        await _send_change(handler, "omni")
        await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs))
        mark = len(handler.outbound)
        await _send_change(handler, "omni")
        frames = await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs), since=mark)

        assert provider.open_count == 1  # 不重建引擎
        assert [f for f in frames if f.get("type") == "session.changed"]
        await _stop(handler)

    async def test_language_fields_reset_on_enter(self) -> None:
        """从 translate 切 omni：回声不得带出旧语言对。"""
        handler = _FakeHandler()
        provider = _HangingProvider()
        orch = _make_orchestrator(handler, provider=provider)
        await _start_session(orch, handler)

        await _send_change(handler, "translate", my_language="zh", local_language="ja", usecase="dialogue")
        await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs))
        mark = len(handler.outbound)
        await _send_change(handler, "omni")
        frames = await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs), since=mark)

        changed = [f for f in frames if f.get("type") == "session.changed"][0]
        assert changed["payload"]["mode"] == "omni"
        assert changed["payload"].get("my_language") in (None, "")
        assert changed["payload"].get("local_language") in (None, "")
        await _stop(handler)


class TestCutOutOmni:
    async def test_omni_to_chat_reopens_asr_lazily(self) -> None:
        handler = _FakeHandler()
        provider = _HangingProvider()
        asr = _CountingAsrService()
        orch = _make_orchestrator(handler, provider=provider, asr=asr, tts=_CountingTtsService())
        await _start_session(orch, handler)
        asr_opens_after_start = asr.open_count  # session.start 已 eager open 一次

        await _send_change(handler, "omni")
        await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs))
        assert orch._omni_active is True

        mark = len(handler.outbound)
        await _send_change(handler, "chat")
        await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs), since=mark)
        assert orch._omni_active is False and orch._omni_engine is None
        assert orch._pipeline_mode == "full"

        # 退出后 ASR 懒重开（含 poll 任务重建）：喂一帧音频即触发
        orch._turn_detector = orch._turn_detector or __import__(
            "session.turn_detector", fromlist=["TurnDetector"]).TurnDetector(sensitivity="medium")
        enc = OpusEncoder(sample_rate=16000, frame_ms=20, bitrate=32000, channels=1)
        await orch._handle_audio(_audio_msg(enc.encode(b"\x00" * 640)))
        assert asr.open_count == asr_opens_after_start + 1
        assert orch._asr_poll_task is not None and not orch._asr_poll_task.done()
        await _stop(handler)

    async def test_omni_to_transcribe_sets_mode(self) -> None:
        handler = _FakeHandler()
        provider = _HangingProvider()
        orch = _make_orchestrator(handler, provider=provider, asr=_CountingAsrService())
        await _start_session(orch, handler)
        await _send_change(handler, "omni")
        await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs))
        mark = len(handler.outbound)
        await _send_change(handler, "transcribe")
        await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs), since=mark)

        assert orch._pipeline_mode == "transcribe"
        assert orch._omni_active is False
        await _stop(handler)


class TestOmniRouting:
    async def _enter(self, *, recording_sink=None, enable_video=True):
        handler = _FakeHandler()
        provider = _HangingProvider()
        orch = _make_orchestrator(handler, provider=provider, asr=_CountingAsrService(),
                                  recording_sink=recording_sink)
        await _start_session(orch, handler, enable_video=enable_video)
        await _send_change(handler, "omni")
        await _wait_for(handler, lambda fs: any(f.get("type") == "session.changed" for f in fs))
        return handler, orch, provider

    async def test_audio_routed_to_engine(self) -> None:
        handler, orch, provider = await self._enter(enable_video=False)
        enc = OpusEncoder(sample_rate=16000, frame_ms=20, bitrate=32000, channels=1)
        await orch._handle_audio(_audio_msg(enc.encode(b"\x00" * 640)))
        assert provider.stream._fed_audio, "音频必须喂给 omni 引擎"
        await _stop(handler)

    async def test_video_image_routed_to_engine(self) -> None:
        handler, orch, provider = await self._enter()
        await orch._handle_video(_video_msg(b"\xff\xd8\xff\xd9"))
        assert provider.stream._fed_images and provider.stream._fed_images[0][1:] == (640, 360)
        await _stop(handler)

    async def test_video_text_does_not_start_three_stage_turn(self) -> None:
        """D6b：omni 下 payload.text 必须被忽略（否则触发三段式 LLM+TTS 抢答）。"""
        sink = FakeRecordingSink()
        handler, orch, provider = await self._enter(recording_sink=sink)
        mark = len(handler.outbound)

        await orch._handle_video(_video_msg(b"\xff\xd8\xff\xd9", text="描述图片内容"))
        frames = _parse(handler, mark)

        assert provider.stream._fed_images, "图片仍须作为视觉上下文投喂"
        assert not any(f.get("type") == "ai_text" for f in frames)
        assert orch._pipeline_task is None  # 未起三段式轮
        assert orch._history is None or all(m.get("role") != "user" for m in _messages(orch))
        assert not sink.chat_texts  # 三件一体未写
        await _stop(handler)

    async def test_interrupt_routed_to_engine(self) -> None:
        from unittest.mock import MagicMock

        handler, orch, provider = await self._enter(enable_video=False)
        msg = MagicMock()
        msg.payload.reason = "user_voice"
        await orch._handle_interrupt(msg)
        assert provider.stream._cancel_count == 1
        frames = _parse(handler)
        statuses = [f for f in frames if f.get("type") == "status"]
        assert statuses and statuses[-1]["payload"]["reason"] == "interrupted"
        await _stop(handler)


def _messages(orch: DialogOrchestrator) -> list[dict]:
    hist = orch._history
    if hist is None:
        return []
    return list(getattr(hist, "_turns", []))


# ---------------------------------------------------------------------------
# 引擎级：事件映射与流终结
# ---------------------------------------------------------------------------


class TestOmniModeEnginePump:
    def _engine(self, handler: _FakeHandler, events: list, *, provider=None) -> tuple[OmniModeEngine, FakeOmniProvider]:
        provider = provider if provider is not None else FakeOmniProvider("fake_omni", events=events)
        ctx = SessionContext(session_id="s-eng", device_id="d1", user_id="u1", audio_sample_rate=16000)
        engine = OmniModeEngine(
            handler=handler, codec=handler._codec, ctx=ctx,
            history=WorkingMemory(), interrupt=InterruptController(ctx),
            omni_service=OmniService(active=provider, fallbacks=[]),
            system_prompt="sys", voice="Tina", sample_rate=16000,
            opus_encoder=OpusEncoder(sample_rate=16000, frame_ms=20, bitrate=32000, channels=1),
            chat_store=FakeChatStore(),
        )
        return engine, provider

    async def test_turn_maps_to_chat_like_s2c_sequence(self) -> None:
        from omni.base import OmniEvent, OmniEventType
        events = [
            OmniEvent(type=OmniEventType.INPUT_TRANSCRIPTION_DELTA, text="你"),
            OmniEvent(type=OmniEventType.INPUT_TRANSCRIPTION_DONE, text="你好", is_final=True),
            OmniEvent(type=OmniEventType.RESPONSE_STARTED, response_id="r1"),
            OmniEvent(type=OmniEventType.TEXT_DELTA, text="嗨", response_id="r1"),
            OmniEvent(type=OmniEventType.AUDIO_DELTA, audio=b"\x00" * 640, response_id="r1"),
            OmniEvent(type=OmniEventType.RESPONSE_DONE, response_id="r1"),
        ]
        handler = _FakeHandler()
        engine, _ = self._engine(handler, events)
        await engine.open()
        await asyncio.sleep(0.05)

        types = _types(_parse(handler))
        assert "transcription" in types and "ai_text" in types and "ai_audio" in types
        assert types.index("status") < types.index("ai_text")
        # 轮末回到 listening（tts_finish）
        statuses = [f for f in _parse(handler) if f.get("type") == "status"]
        assert statuses[-1]["payload"]["session_status"] == "listening"
        assert statuses[-1]["payload"]["reason"] == "tts_finish"
        # 落库：user + assistant 各一条
        hist = list(getattr(engine._history, "_turns", []))
        assert [m.get("role") for m in hist] == ["user", "assistant"]
        await engine.close()

    async def test_stream_end_reopens_on_next_audio(self) -> None:
        from omni.base import OmniEvent, OmniEventType
        handler = _FakeHandler()
        provider = _CountingScriptedProvider([OmniEvent(type=OmniEventType.SPEECH_STOPPED)])
        engine, provider = self._engine(handler, [], provider=provider)
        await engine.open()
        await asyncio.sleep(0.05)
        # 流终结（events 迭代结束）→ 置空 + 冷却
        assert engine._stream is None

        engine._open_fail_time = 0.0  # 冷却到期
        await engine.feed_pcm(b"\x00" * 640)
        assert provider.open_count == 2
        assert engine._stream is not None
        await engine.close()


# ---------------------------------------------------------------------------
# 引擎级：响应图像快照竞态回归锁（docs/fd_pipeline_video_process.md §5.1/§7.1/§7.2）
# ---------------------------------------------------------------------------


class _ScriptedStream:
    """队列驱动事件流：测试逐条 push；cancel_response 可被 gate 卡住。

    三个竞态锁原在 tests/integration/omni/（随 bdfc622 删除）。bdfc622 后
    OmniModeEngine 独立成类——fake stream + 可控出站/取消时序即可在引擎单元层
    确定性复现，无需重建集成目录。
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self._fed_images: list[tuple[bytes, int, int]] = []
        self._cancel_count = 0
        # 非 None 时：cancel_response 置位 cancelled 后阻塞，直到 gate 被 set
        self.cancel_gate: asyncio.Event | None = None
        self.cancelled = asyncio.Event()
        self._closed = False

    def push(self, ev) -> None:
        self.queue.put_nowait(ev)

    async def feed_audio(self, pcm: bytes) -> None: ...

    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None:
        self._fed_images.append((jpeg, width, height))

    async def commit_user_turn(self) -> None: ...

    async def events(self):
        while True:
            ev = await self.queue.get()
            if ev is None:
                return
            yield ev

    async def cancel_response(self) -> None:
        self._cancel_count += 1
        if self.cancel_gate is not None:
            self.cancelled.set()
            await self.cancel_gate.wait()

    async def reset(self) -> None: ...

    async def close(self) -> None:
        self._closed = True


class _ScriptedProvider:
    name = "fake_omni"
    supports_vision = True

    def __init__(self) -> None:
        self.stream = _ScriptedStream()
        self.open_count = 0
        self.last_open_kwargs: dict | None = None

    async def open(self, *, sample_rate, system_prompt="", voice="",
                   modalities=("text", "audio"), generation_opts=None):
        self.open_count += 1
        self.last_open_kwargs = {
            "sample_rate": sample_rate, "system_prompt": system_prompt, "voice": voice,
            "modalities": modalities, "generation_opts": generation_opts,
        }
        return self.stream


class _GatedThinkingHandler(_FakeHandler):
    """把首个 thinking status 的出站闸住：给测试一个"快照已完成、分支停在首个
    await"的确定性注帧窗口（§7.2 场景）。"""

    def __init__(self) -> None:
        super().__init__()
        self.gate_next_thinking = False
        self.thinking_parked = asyncio.Event()
        self.release = asyncio.Event()

    async def enqueue_outbound(self, frame: str) -> None:
        if self.gate_next_thinking:
            try:
                msg = json.loads(frame)
            except ValueError:
                msg = {}
            if (msg.get("type") == "status"
                    and msg.get("payload", {}).get("session_status") == "thinking"):
                self.gate_next_thinking = False
                self.thinking_parked.set()
                await self.release.wait()
        await super().enqueue_outbound(frame)


class TestResponseImageSnapshotLocks:
    """终态不变量：final ai_text 回图 == RESPONSE_STARTED 请求边界快照（F1），
    响应期间到达的帧（F2）属下一轮。三个时序问题各经一轮 review 才挖出——
    对 omni_mode.py 事件泵的任何重构都必须先过这三把锁。
    """

    def _engine(self, handler: _FakeHandler, provider) -> OmniModeEngine:
        ctx = SessionContext(session_id="s-lock", device_id="d1", user_id="u1",
                             audio_sample_rate=16000)
        ctx.enable_video = True
        return OmniModeEngine(
            handler=handler, codec=handler._codec, ctx=ctx,
            history=WorkingMemory(), interrupt=InterruptController(ctx),
            omni_service=OmniService(active=provider, fallbacks=[]),
            system_prompt="sys", voice="Tina", sample_rate=16000,
            opus_encoder=OpusEncoder(sample_rate=16000, frame_ms=20, bitrate=32000, channels=1),
            chat_store=FakeChatStore(),
        )

    @staticmethod
    def _put_frame(ctx: SessionContext, jpeg: bytes) -> None:
        ctx.latest_video_frames.append((1, jpeg, 0, 640, 360, 0, "front"))

    @staticmethod
    def _final_image(frames: list[dict]) -> dict | None:
        for f in frames:
            if f.get("type") == "ai_text" and f.get("payload", {}).get("is_final"):
                return f["payload"].get("image")
        return None

    @staticmethod
    async def _wait_until(pred, *, timeout: float = 2.0) -> bool:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return True
            await asyncio.sleep(0.01)
        return pred()

    async def test_reply_carries_request_boundary_snapshot(self) -> None:
        """§5.1：STARTED 与 DONE 之间注入 F2 → 回图 = F1（边界快照），且 DONE
        不清 buffer（F2 留给下一轮快照）。"""
        from omni.base import OmniEvent, OmniEventType

        handler = _FakeHandler()
        provider = _ScriptedProvider()
        engine = self._engine(handler, provider)
        f1, f2 = b"JPEG-ONE", b"JPEG-TWO"
        self._put_frame(engine._ctx, f1)

        await engine.open()
        provider.stream.push(OmniEvent(type=OmniEventType.RESPONSE_STARTED, response_id="r1"))
        parked = await _wait_for(handler, lambda fs: any(
            f.get("type") == "status" and f.get("payload", {}).get("session_status") == "thinking"
            for f in fs))
        assert parked, "thinking status 未到达"

        self._put_frame(engine._ctx, f2)
        provider.stream.push(OmniEvent(type=OmniEventType.TEXT_DELTA, text="嗨", response_id="r1"))
        await _wait_for(handler, lambda fs: any(f.get("type") == "ai_text" for f in fs))
        provider.stream.push(OmniEvent(type=OmniEventType.RESPONSE_DONE, response_id="r1"))
        frames = await _wait_for(handler, lambda fs: self._final_image(fs) is not None)

        image = self._final_image(frames)
        assert image is not None
        assert image["data"] == base64.b64encode(f1).decode(), "回图必须是 STARTED 边界快照 F1"
        assert engine._ctx.latest_video_frames[-1][1] == f2, "DONE 不消费 buffer，F2 留给下一轮"
        await engine.close()

    async def test_snapshot_precedes_branch_awaits(self) -> None:
        """§7.2：thinking status 出站（STARTED 分支内首个 await）期间注入 F2 →
        回图仍为 F1（快照先于分支任何 await 取）。"""
        from omni.base import OmniEvent, OmniEventType

        handler = _GatedThinkingHandler()
        provider = _ScriptedProvider()
        engine = self._engine(handler, provider)
        f1, f2 = b"JPEG-ONE", b"JPEG-TWO"
        self._put_frame(engine._ctx, f1)

        await engine.open()
        handler.gate_next_thinking = True
        provider.stream.push(OmniEvent(type=OmniEventType.RESPONSE_STARTED, response_id="r1"))
        await asyncio.wait_for(handler.thinking_parked.wait(), timeout=2.0)

        # 泵停在分支内首个 await：此刻注帧不得被事后快照错捕
        self._put_frame(engine._ctx, f2)
        handler.release.set()

        provider.stream.push(OmniEvent(type=OmniEventType.TEXT_DELTA, text="嗨", response_id="r1"))
        provider.stream.push(OmniEvent(type=OmniEventType.RESPONSE_DONE, response_id="r1"))
        frames = await _wait_for(handler, lambda fs: self._final_image(fs) is not None)

        image = self._final_image(frames)
        assert image is not None
        assert image["data"] == base64.b64encode(f1).decode(), "快照必须先于分支 await（F2 不得错捕）"
        await engine.close()

    async def test_interrupt_cleanup_does_not_kill_new_response_snapshot(self) -> None:
        """§7.1：cancel_response 被 WS 往返卡住期间泵入 R2 STARTED（快照 F2）；
        interrupt 完成后 R2 的 DONE 仍回带 F2——清理（若滞留在 interrupt 尾部）
        会误杀新响应快照的回归。"""
        from omni.base import OmniEvent, OmniEventType

        handler = _FakeHandler()
        provider = _ScriptedProvider()
        engine = self._engine(handler, provider)
        f1, f2 = b"JPEG-ONE", b"JPEG-TWO"
        self._put_frame(engine._ctx, f1)

        await engine.open()
        provider.stream.push(OmniEvent(type=OmniEventType.RESPONSE_STARTED, response_id="r1"))
        started = await _wait_for(handler, lambda fs: any(
            f.get("type") == "status" and f.get("payload", {}).get("session_status") == "thinking"
            for f in fs))
        assert started, "R1 thinking status 未到达"

        # 编排器次序（_handle_interrupt omni 分支）：epoch/SM 推进 → 引擎停播复位
        gate = asyncio.Event()
        provider.stream.cancel_gate = gate

        async def _drive_interrupt() -> None:
            await engine._interrupt.interrupt("user_voice")
            await engine.interrupt("user_voice")

        interrupt_task = asyncio.create_task(_drive_interrupt())
        await asyncio.wait_for(provider.stream.cancelled.wait(), timeout=2.0)

        # cancel_response 在飞（gate 卡住）期间：R2 STARTED 赋好新快照（F2）
        self._put_frame(engine._ctx, f2)
        provider.stream.push(OmniEvent(type=OmniEventType.RESPONSE_STARTED, response_id="r2"))
        assert await self._wait_until(lambda: engine._response_image_payload is not None), \
            "R2 快照未在 cancel_response 等待期间赋值"

        gate.set()
        await asyncio.wait_for(interrupt_task, timeout=2.0)

        provider.stream.push(OmniEvent(type=OmniEventType.TEXT_DELTA, text="嗨", response_id="r2"))
        await _wait_for(handler, lambda fs: any(f.get("type") == "ai_text" for f in fs))
        provider.stream.push(OmniEvent(type=OmniEventType.RESPONSE_DONE, response_id="r2"))
        frames = await _wait_for(handler, lambda fs: self._final_image(fs) is not None)

        image = self._final_image(frames)
        assert image is not None, "打断后的新响应不得丢图（清理误杀 R2 快照的回归）"
        assert image["data"] == base64.b64encode(f2).decode()
        assert provider.stream._cancel_count == 1
        await engine.close()
