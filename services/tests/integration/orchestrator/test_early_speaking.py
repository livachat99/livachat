"""THINKING→SPEAKING 提前迁移回归测试（docs/session_states.md §3 方案、§5 用例）。

生产改动：orchestrator._start_thinking 在 final ai_text 发出后立即迁移 SPEAKING
并置 first_audio_sent（不等首帧）。本文件覆盖 §5 全部 5 条：

1. final 文本后、首帧到达前已发 speaking/tts_start；首帧后不重复发（回归锁）；
2. 窗口内 barge-in → pipeline 取消、listening/interrupted、wire 上 finish-task
   恰好一次（真实 bailian duplex provider，复用 unit/tts/providers/test_bailian.py
   的脚本化 server 断言模式）；
3. feed_text 抛 ProviderError → speaking→listening(tts_finish)，无挂起、无二次 speaking；
4. final text 后 interrupt（epoch++，白盒）→ 不迁移、不发 speaking（guard 回归锁）；
5. tts_stream=None / 空 full_text → 无 speaking status（条件 a 回归锁）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets
from websockets.exceptions import ConnectionClosed

from gateway.auth import Principal
from gateway.codec import WireCodec
from gateway.seq import OutboundSeqAllocator
from session.manager import SessionManager
from session.orchestrator import DialogOrchestrator
from session.state_machine import SessionState
from shared.utils.errors import ProviderError
from tests.integration.orchestrator.conftest import FakeChatStore

pytestmark = pytest.mark.integration

_AUDIO_FRAME = b"\x00\x01" * 480  # 960 bytes = 480 samples = 20 ms @ 24 kHz


def _make_msg(msg_type: str, seq: int, timestamp: int, **kw):
    """Build client messages for the inbound queue (same shape as sibling tests)."""
    from livachat_protocol.generated.client_message import (
        ClientAudio,
        ClientSessionStart,
    )
    from livachat_protocol.generated.protocol_models import (
        ClientAudioPayload,
        ClientSessionStartConfig,
        ClientSessionStartPayload,
    )

    if msg_type == "session.start":
        return ClientSessionStart(
            seq=seq,
            timestamp=timestamp,
            payload=ClientSessionStartPayload(
                session_id=None,
                config=ClientSessionStartConfig(
                    enable_video=kw.get("enable_video", False),
                    audio_sample_rate=kw.get("audio_sample_rate", 24000),
                    vad_sensitivity=kw.get("vad_sensitivity", "high"),
                    client_version="android-1.0.0",
                ),
            ),
        )
    elif msg_type == "audio":
        return ClientAudio(
            seq=seq,
            timestamp=timestamp,
            payload=ClientAudioPayload(
                data=kw.get("data", "AAAA"),
                codec="opus",
                duration_ms=20,
                vad_state=kw.get("vad_state", "silence"),
            ),
        )
    raise ValueError(f"unknown msg_type: {msg_type}")


class _FakeHandler:
    """Minimal ConnectionHandler stub that captures outbound frames."""

    def __init__(self, gate_final_ai_text: asyncio.Event | None = None):
        alloc = OutboundSeqAllocator()
        self._codec = WireCodec(alloc)
        self._stopped = False
        self._inbound: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.principal = Principal(user_id="u", device_id="test-device")
        self.outbound: list[str] = []
        self.gate_final_ai_text = gate_final_ai_text

    async def enqueue_outbound(self, frame: str) -> None:
        self.outbound.append(frame)
        if self.gate_final_ai_text is not None:
            d = json.loads(frame)
            if d["type"] == "ai_text" and d["payload"].get("is_final"):
                await self.gate_final_ai_text.wait()

    def stop(self) -> None:
        self._stopped = True


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class _FixedAsrService:
    """ASR 恒产出 meaningful 文本——barge-in 回合（第二轮 turn end）也需要
    meaningful final 才能触发 interrupt（772-780 行），conftest 的 fake reset
    后变空串不可用。"""

    async def open(self, **kw):
        return _FixedAsrStream()


class _FixedAsrStream:
    def __init__(self):
        self._final_text = "user utterance"

    async def feed(self, pcm):
        pass

    async def results(self):
        if False:
            yield

    async def final(self):
        return self._final_text

    @property
    def final_text(self) -> str:
        return self._final_text

    async def reset(self):
        pass  # 保持恒定 meaningful（barge-in 回合仍需）

    async def close(self):
        pass


class _TokenLlmService:
    def __init__(self, tokens: list[str] | None = None):
        self._tokens = tokens if tokens is not None else ["Hello", " world!"]

    async def stream(self, *, messages, images=None, **opts):
        for t in self._tokens:
            yield t


class _RecordingTtsService:
    """TTS fake：feed/audio 事件记入共享 log，供 status 与 TTS 时序断言。"""

    def __init__(self, chunks: list[bytes], log: list[tuple], pre_yield_s: float = 0.05):
        self._chunks = chunks
        self._log = log
        self._pre_yield_s = pre_yield_s

    async def open(self, **kw):
        return _RecordingTtsStream(self._chunks, self._log, self._pre_yield_s)


class _RecordingTtsStream:
    def __init__(self, chunks: list[bytes], log: list[tuple], pre_yield_s: float):
        self._chunks = chunks
        self._log = log
        self._pre_yield_s = pre_yield_s

    async def feed_text(self, text):
        self._log.append(("tts", "feed"))

    async def flush(self):
        pass

    def audio(self):
        async def _gen():
            for i, c in enumerate(self._chunks):
                if self._pre_yield_s:
                    await asyncio.sleep(self._pre_yield_s)
                self._log.append(("tts", "audio", i))
                yield c

        return _gen()

    async def reset(self):
        self._log.append(("tts", "reset"))

    async def close(self):
        pass


class _FailingFeedTtsService:
    """feed_text 双重重试路径直接抛 ProviderError（doc §5-3）。"""

    async def open(self, **kw):
        return _FailingFeedTtsStream()


class _FailingFeedTtsStream:
    async def feed_text(self, text):
        raise ProviderError("tts feed failed", provider="fake_tts")

    async def flush(self):
        pass

    def audio(self):
        async def _empty():
            if False:
                yield

        return _empty()

    async def reset(self):
        pass

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# 驱动帮助函数
# ---------------------------------------------------------------------------


def _statuses(outbound: list[str]) -> list[dict]:
    return [
        json.loads(f)["payload"]
        for f in outbound
        if json.loads(f)["type"] == "status"
    ]


async def _drive_turn_end(handler: _FakeHandler, start_seq: int, start_ts: int, *,
                          vad: str = "silence", n: int = 30) -> None:
    for i in range(n):
        handler._inbound.put_nowait(
            _make_msg("audio", start_seq + i, start_ts + i * 20, vad_state=vad)
        )


def _new_orchestrator(handler: _FakeHandler, **overrides) -> DialogOrchestrator:
    defaults = {
        "handler": handler,
        "codec": handler._codec,
        "session_mgr": SessionManager(),
        "asr_service": _FixedAsrService(),
        "llm_service": _TokenLlmService(),
        "chat_store": FakeChatStore(),
        "system_prompt": "You are helpful.",
    }
    defaults.update(overrides)
    return DialogOrchestrator(**defaults)


async def _start_session_and_turn(orch: DialogOrchestrator, handler: _FakeHandler) -> asyncio.Task:
    task = asyncio.create_task(orch.run())
    handler._inbound.put_nowait(_make_msg("session.start", 1, 1000))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if any("session.ready" in f for f in handler.outbound):
            break
    await _drive_turn_end(handler, 2, 2000)
    return task


async def _teardown(handler: _FakeHandler, task: asyncio.Task) -> None:
    handler.stop()
    if handler.gate_final_ai_text is not None:
        handler.gate_final_ai_text.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestEarlySpeakingStatus:
    async def test_speaking_before_feed_and_exactly_once(self):
        """final ai_text 后、feed_text 前已发 speaking/tts_start；首帧不再重复发。"""
        log: list[tuple] = []
        handler = _FakeHandler()
        orch = _new_orchestrator(
            handler,
            tts_service=_RecordingTtsService(
                [_AUDIO_FRAME, _AUDIO_FRAME], log, pre_yield_s=0.05
            ),
        )

        # status 帧同时记入 log，供与 TTS 事件的跨对象时序断言
        orig_enqueue = handler.enqueue_outbound

        async def _enqueue_logging(frame: str) -> None:
            d = json.loads(frame)
            p = d["payload"]
            if d["type"] == "status":
                log.append(("status", p["session_status"], p["reason"]))
            elif d["type"] == "ai_audio":
                log.append(("audio", p["index"]))
            await orig_enqueue(frame)

        handler.enqueue_outbound = _enqueue_logging  # type: ignore[method-assign]

        task = await _start_session_and_turn(orch, handler)
        try:
            # 等整轮走完（speaking → 两帧 audio → listening/tts_finish）
            for _ in range(100):
                await asyncio.sleep(0.05)
                statuses = [e for e in log if e[0] == "status"]
                if statuses and statuses[-1][1] == "listening":
                    break

            speaking = [i for i, e in enumerate(log) if e[:2] == ("status", "speaking")]
            feeds = [i for i, e in enumerate(log) if e == ("tts", "feed")]
            assert speaking, f"expected early speaking status, log={log}"
            assert feeds, f"expected tts feed, log={log}"
            assert speaking[0] < feeds[0], (
                f"speaking status must precede tts feed (early migration), log={log}"
            )
            assert len(speaking) == 1, f"speaking status must be sent exactly once, log={log}"

            audios = [e for e in log if e[0] == "audio"]
            assert len(audios) >= 1, f"expected ai_audio frames, log={log}"

            statuses = [e for e in log if e[0] == "status"]
            assert statuses[-1] == ("status", "listening", "tts_finish"), (
                f"expected clean tts_finish ending, log={log}"
            )
        finally:
            await _teardown(handler, task)


class TestBargeInDuringEarlySpeakingWindow:
    async def test_barge_in_cancels_pipeline_and_finishes_task_once(self):
        """特性主路径：首帧前（零音频）窗口内 barge-in（SPEAKING 期 VAD 回合结束 +
        meaningful ASR）→ pipeline 取消、listening/interrupted、无任何 ai_audio 泄漏、
        真实 bailian 链路上 finish-task 恰好一次（pipeline finally 与 _do_interrupt
        双 reset 幂等；reset 在 active-unflushed 时补发 finish 的单向语义由
        tests/unit/tts/providers/test_bailian.py 锁定）。"""
        from tts.providers.alibaba_bailian import AlibabaBailianTTSProvider

        events: list[tuple] = []
        saw_finish = asyncio.Event()   # 服务端已收到 flush 的 finish-task
        release_audio = asyncio.Event()  # 测试在 barge-in 确认后放行音频

        async def handler(ws):
            # 协议期断言异常不捕获——handler 异常会让 websockets.serve 在退出时报错，
            # 协议错位不被静默掩盖
            m = json.loads(await ws.recv())
            assert m["header"]["action"] == "run-task", m
            tid = m["header"]["task_id"]
            events.append(("run", tid))
            await ws.send(json.dumps({"header": {"event": "task-started", "task_id": tid}}))
            m = json.loads(await ws.recv())
            assert m["header"]["action"] == "continue-task", m
            events.append(("continue", tid))
            m = json.loads(await ws.recv())
            assert m["header"]["action"] == "finish-task", m
            events.append(("finish", tid))
            # 音频按住不放：orchestrator 的 drain 停在首帧前——这正是特性窗口
            # （speaking 已发、零音频播放）。等测试完成 barge-in 后再放行，
            # 验证取消后迟到的音频不得泄漏成 ai_audio。
            saw_finish.set()
            await release_audio.wait()
            await ws.send(_AUDIO_FRAME)
            try:
                while True:
                    await ws.recv()
            except ConnectionClosed:
                pass  # 客户端（orchestrator teardown）关连接属预期

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            provider = AlibabaBailianTTSProvider(
                base_url=f"ws://127.0.0.1:{port}",
                api_key="k",
                model="m",
                voice="v",
            )
            handler_stub = _FakeHandler()
            orch = _new_orchestrator(handler_stub, tts_service=provider)
            task = await _start_session_and_turn(orch, handler_stub)
            try:
                # 等提前迁移的 speaking status（final 文本后即发，先于任何音频）
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if any(
                        s["session_status"] == "speaking" and s["reason"] == "tts_start"
                        for s in _statuses(handler_stub.outbound)
                    ):
                        break
                statuses = _statuses(handler_stub.outbound)
                assert any(
                    s["session_status"] == "speaking" and s["reason"] == "tts_start"
                    for s in statuses
                ), f"expected early speaking, statuses={statuses}"

                # feed+flush 已完成（服务端确认 finish-task）→ drain 停在首帧前
                for _ in range(100):
                    if saw_finish.is_set():
                        break
                    await asyncio.sleep(0.05)
                assert saw_finish.is_set(), f"bailian turn never flushed, events={events}"
                assert not any(
                    json.loads(f)["type"] == "ai_audio" for f in handler_stub.outbound
                ), "early-window test must start with zero ai_audio"

                # SPEAKING 期注入 VAD speech + 尾部静音 → meaningful turn end → barge-in。
                # 静音逐帧喂并在 interrupted 出现时立即停：turn end 只须 ~20 帧静音，
                # 一次塞满会留下排队尾部——interrupt 后 state 已回 LISTENING，残留静音会
                # 自成新回合（第二次 thinking/speaking），干扰断言。
                await _drive_turn_end(handler_stub, 100, 9000, vad="speech", n=4)
                interrupted = False
                for i in range(80):
                    handler_stub._inbound.put_nowait(
                        _make_msg("audio", 104 + i, 9200 + i * 20, vad_state="silence")
                    )
                    await asyncio.sleep(0.02)
                    if any(s["reason"] == "interrupted"
                           for s in _statuses(handler_stub.outbound)):
                        interrupted = True
                        break
                assert interrupted, (
                    "barge-in did not fire — statuses="
                    + str(_statuses(handler_stub.outbound))
                )
                await asyncio.sleep(0.1)  # 等 interrupt 序列收尾（reset/状态机）

                statuses = _statuses(handler_stub.outbound)
                speaking_idx = [
                    i for i, s in enumerate(statuses)
                    if s["session_status"] == "speaking" and s["reason"] == "tts_start"
                ]
                interrupted_idx = [
                    i for i, s in enumerate(statuses) if s["reason"] == "interrupted"
                ]
                assert speaking_idx and interrupted_idx, (
                    f"expected speaking before interrupted, statuses={statuses}"
                )
                # barge-in 回合的用户话语会作为新回合立即再答（788-793 行产品语义，
                # interrupted 后允许第二次 speaking）；窗口轮必须恰好一次——
                # interrupted 之前不得有第二次 speaking。
                assert len([i for i in speaking_idx if i < interrupted_idx[0]]) == 1, (
                    f"exactly one speaking before interrupted, statuses={statuses}"
                )

                # 双 reset（pipeline finally + _do_interrupt）幂等：wire 上
                # finish-task 恰好一次
                release_audio.set()  # 取消后放行"迟到"音频——必须被丢弃，不得成帧
                await asyncio.sleep(0.4)
                assert [e[0] for e in events] == ["run", "continue", "finish"], (
                    f"expected exactly one finish-task on the wire, events={events}"
                )
                assert not any(
                    json.loads(f)["type"] == "ai_audio" for f in handler_stub.outbound
                ), "audio arriving after cancel must not leak to client"
            finally:
                release_audio.set()
                await _teardown(handler_stub, task)


class TestFeedFailureAfterEarlySpeaking:
    async def test_feed_error_no_hang_no_second_speaking(self):
        """feed_text 双失败（ProviderError）→ speaking 已发后走 listening/tts_finish，
        无挂起、无二次 speaking、无音频（doc §5-3）。"""
        handler = _FakeHandler()
        orch = _new_orchestrator(handler, tts_service=_FailingFeedTtsService())
        task = await _start_session_and_turn(orch, handler)
        try:
            for _ in range(100):
                await asyncio.sleep(0.05)
                statuses = _statuses(handler.outbound)
                if any(s["reason"] == "tts_finish" for s in statuses):
                    break

            statuses = _statuses(handler.outbound)
            speaking = [s for s in statuses if s["session_status"] == "speaking"]
            assert len(speaking) == 1, f"expected exactly one speaking, statuses={statuses}"
            assert speaking[0]["reason"] == "tts_start"
            tail = [s for s in statuses if s["session_status"] == "listening" and s["reason"] == "tts_finish"]
            assert tail, f"expected listening/tts_finish tail, statuses={statuses}"
            assert not any(json.loads(f)["type"] == "ai_audio" for f in handler.outbound), (
                "feed failed so no audio frames expected"
            )
            assert orch._ctx.state == SessionState.LISTENING, (
                f"no hang: state must settle to listening, state={orch._ctx.state}"
            )
        finally:
            await _teardown(handler, task)


class TestEpochGuardBeforeMigration:
    async def test_interrupt_after_final_text_blocks_migration(self):
        """final ai_text 发出后 interrupt（epoch++）→ 迁移守卫拒绝：不发 speaking
        （doc §5-4 回归锁）。白盒 interrupt()（只 bump epoch + SM→LISTENING，不
        cancel 任务）——cancel 路径会让任务在迁移前就消亡，测不到 guard。"""
        gate = asyncio.Event()
        handler = _FakeHandler(gate_final_ai_text=gate)
        orch = _new_orchestrator(
            handler,
            tts_service=_RecordingTtsService([_AUDIO_FRAME], [], pre_yield_s=0),
        )
        task = await _start_session_and_turn(orch, handler)
        try:
            # pipeline 悬停在 final ai_text 的 enqueue（append 先行，frame 已可见）
            for _ in range(100):
                await asyncio.sleep(0.02)
                finals = [
                    json.loads(f)
                    for f in handler.outbound
                    if json.loads(f)["type"] == "ai_text"
                    and json.loads(f)["payload"].get("is_final")
                ]
                if finals:
                    break
            assert finals, "pipeline did not reach final ai_text"

            # 白盒：interrupt 落在 final text 之后、迁移点之前（epoch++，无 cancel）
            await orch._interrupt.interrupt("user_voice")

            gate.set()
            await asyncio.sleep(0.3)

            statuses = _statuses(handler.outbound)
            assert not any(s["session_status"] == "speaking" for s in statuses), (
                f"speaking status must be suppressed by epoch guard, statuses={statuses}"
            )
            assert not any(json.loads(f)["type"] == "ai_audio" for f in handler.outbound), (
                "no audio expected after interrupt"
            )
            assert orch._ctx.state == SessionState.LISTENING, (
                f"state must settle to listening, state={orch._ctx.state}"
            )
        finally:
            await _teardown(handler, task)


class TestNoSpeakingWithoutSpeech:
    async def test_no_speaking_when_tts_unavailable(self):
        """tts_service=None（等价 asr_llm/text-only）→ 不迁移、无 speaking（doc §5-5a）。"""
        handler = _FakeHandler()
        orch = _new_orchestrator(handler, tts_service=None)
        task = await _start_session_and_turn(orch, handler)
        try:
            for _ in range(100):
                await asyncio.sleep(0.05)
                statuses = _statuses(handler.outbound)
                if any(s["reason"] == "tts_finish" for s in statuses):
                    break

            statuses = _statuses(handler.outbound)
            assert not any(s["session_status"] == "speaking" for s in statuses), (
                f"no speaking expected without TTS, statuses={statuses}"
            )
            assert any(s["reason"] == "tts_finish" for s in statuses), (
                f"expected clean ending, statuses={statuses}"
            )
        finally:
            await _teardown(handler, task)

    async def test_no_speaking_when_text_empty(self):
        """LLM 空输出 → full_text 为空 → 不迁移、无 speaking（doc §5-5b）。"""
        handler = _FakeHandler()
        orch = _new_orchestrator(
            handler,
            llm_service=_TokenLlmService(tokens=[]),
            tts_service=_RecordingTtsService([], [], pre_yield_s=0),
        )
        task = await _start_session_and_turn(orch, handler)
        try:
            for _ in range(100):
                await asyncio.sleep(0.05)
                statuses = _statuses(handler.outbound)
                if any(s["reason"] == "tts_finish" for s in statuses):
                    break

            statuses = _statuses(handler.outbound)
            assert not any(s["session_status"] == "speaking" for s in statuses), (
                f"no speaking expected with empty text, statuses={statuses}"
            )
            assert any(s["reason"] == "tts_finish" for s in statuses), (
                f"expected clean ending, statuses={statuses}"
            )
        finally:
            await _teardown(handler, task)
