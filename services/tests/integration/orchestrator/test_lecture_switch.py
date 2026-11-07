"""Lecture 模式切换延迟：慢 close 不得拖慢 session.changed 回执。

根因与设计见 docs/translate_lecture_improvements.md §2：真机日志中 ASR close 10s +
TTS close 5s 串行阻塞派发循环，session.changed 延迟 15.1s。
本用例以"close 各 sleep 0.3s"放大差异：串行关闭 ≥0.6s，并发关闭 ≈0.3s。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import pytest

from session.manager import SessionManager
from session.orchestrator import DialogOrchestrator
from shared.config.models import TranslateConfig
from tests.integration.orchestrator.conftest import FakeChatStore, FakeLlmService

pytestmark = pytest.mark.integration

CLOSE_DELAY_S = 0.3
ACK_BUDGET_S = 0.5  # 串行关闭 = 2 × CLOSE_DELAY = 0.6s 必超；并发 ≈0.3s


class _SlowCloseAsrStream:
    def __init__(self) -> None:
        self._final_text = ""

    async def feed(self, pcm) -> None: ...

    async def results(self):
        return
        yield  # pragma: no cover —— 空结果流

    async def final(self) -> str:
        return self._final_text

    @property
    def final_text(self) -> str:
        return self._final_text

    async def reset(self) -> None:
        self._final_text = ""

    async def close(self) -> None:
        await asyncio.sleep(CLOSE_DELAY_S)


class _SlowCloseTtsStream:
    async def feed_text(self, text) -> None: ...

    def audio(self):
        async def _empty():
            if False:
                yield  # pragma: no cover

        return _empty()

    async def flush(self) -> None: ...

    async def close(self) -> None:
        await asyncio.sleep(CLOSE_DELAY_S)


class _SlowCloseAsrService:
    async def open(self, *, sample_rate, language=None, interim_results=True):
        return _SlowCloseAsrStream()


class _SlowCloseTtsService:
    async def open(self, *, voice, sample_rate, audio_format="pcm"):
        return _SlowCloseTtsStream()


class _FakeLectureStream:
    """lecture 引擎的假 OmniStream：events() 挂起模拟活流。

    空流（立即 StopAsyncIteration）= 流终结 → 泵退出即向客户端报错一次
    （失败不降级，docs/android_audio_capture.md §5.2），会引入与本用例断言无关的 error 帧；
    挂起则保持 lecture 存活，teardown 由 run() finally 的引擎 close 清理。
    """

    def events(self):
        async def _gen():
            await asyncio.Event().wait()  # 永不产出：保持 lecture 存活
            yield  # pragma: no cover

        return _gen()

    async def feed_audio(self, pcm) -> None: ...

    async def cancel_response(self) -> None: ...

    async def close(self) -> None: ...


class _FakeLiveTranslateProvider:
    def __init__(self, **kwargs) -> None: ...

    async def open(self, *, sample_rate, modalities=("text", "audio"), **kwargs):
        return _FakeLectureStream()


def _changed_frames(handler, since: int) -> list[dict]:
    out = []
    for raw in handler.outbound[since:]:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if msg.get("type") == "session.changed":
            out.append(msg)
    return out


async def test_lecture_switch_ack_not_delayed_by_slow_close(fake_handler, monkeypatch):
    """慢 close（各 0.3s）下 session.changed 仍须 ≤0.5s 到达（并发关闭）。"""
    monkeypatch.setattr(
        "omni.providers.alibaba_livetranslate.LiveTranslateProvider",
        _FakeLiveTranslateProvider,
    )

    from livachat_protocol.generated.client_message import (
        ClientSessionChange,
        ClientSessionStart,
    )
    from livachat_protocol.generated.protocol_models import (
        ClientSessionChangePayload,
        ClientSessionStartConfig,
        ClientSessionStartPayload,
    )

    cfg = TranslateConfig(
        enabled=True,
        base_url="wss://test.invalid",
        api_key="k",
        model="m",
        voice="v",
        source_language="en",
        target_language="zh",
        input_sample_rate=16000,
        output_sample_rate=24000,
    )
    orch = DialogOrchestrator(
        handler=fake_handler,
        codec=fake_handler._codec,
        session_mgr=SessionManager(),
        asr_service=_SlowCloseAsrService(),
        llm_service=FakeLlmService(),
        tts_service=_SlowCloseTtsService(),
        system_prompt="You are helpful.",
        translate_prompt="You are a translator.",
        chat_store=FakeChatStore(),
        translate_cfg=cfg,
    )

    run_task = asyncio.create_task(orch.run())
    try:
        fake_handler._inbound.put_nowait(
            ClientSessionStart(
                seq=1,
                timestamp=1000,
                payload=ClientSessionStartPayload(
                    session_id=None,
                    config=ClientSessionStartConfig(
                        enable_video=False,
                        audio_sample_rate=16000,
                        vad_sensitivity="medium",
                        client_version="android-1.0.0",
                    ),
                ),
            )
        )
        for _ in range(100):
            if any("session.ready" in f for f in fake_handler.outbound):
                break
            await asyncio.sleep(0.02)
        assert any("session.ready" in f for f in fake_handler.outbound), "session.ready 未到达"

        sent_at = len(fake_handler.outbound)
        t0 = time.monotonic()
        fake_handler._inbound.put_nowait(
            ClientSessionChange(
                seq=2,
                timestamp=2000,
                payload=ClientSessionChangePayload(
                    mode="translate",
                    my_language="zh",
                    local_language="en",
                    usecase="lecture",
                ),
            )
        )

        ack_latency: float | None = None
        deadline = t0 + 2.0
        while time.monotonic() < deadline:
            if _changed_frames(fake_handler, sent_at):
                ack_latency = time.monotonic() - t0
                break
            await asyncio.sleep(0.005)

        assert ack_latency is not None, "session.changed 未到达"
        assert ack_latency < ACK_BUDGET_S, (
            f"切换回执被慢 close 拖慢：{ack_latency:.2f}s "
            f"（并发应 ≈{CLOSE_DELAY_S}s，串行 ≥{2 * CLOSE_DELAY_S}s）"
        )
        assert orch._lecture_active is True
        assert orch._asr_stream is None
        assert orch._tts_stream is None
    finally:
        fake_handler.stop()
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
