"""畸形音频帧后会话主循环必须存活（集成回归锁）。

修复前：`_decode_opus` 抛 binascii.Error/OSError 穿出 `_dispatch` → `orchestrator.run()`
终结，而 gateway 只记 "dialog task error"（连接/心跳继续）→ 会话静默死亡。
本用例断言：畸形帧之后派发循环仍在处理消息（后续 session.change 能收到回执）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from session.manager import SessionManager
from session.orchestrator import DialogOrchestrator
from tests.integration.orchestrator.conftest import FakeAsrService, FakeChatStore, FakeLlmService, FakeTtsService

pytestmark = pytest.mark.integration


def _type_in(handler, msg_type: str) -> list[dict]:
    out = []
    for raw in handler.outbound:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if msg.get("type") == msg_type:
            out.append(msg)
    return out


async def test_loop_survives_malformed_audio(fake_handler):
    from livachat_protocol.generated.client_message import (
        ClientAudio,
        ClientSessionChange,
        ClientSessionStart,
    )
    from livachat_protocol.generated.protocol_models import (
        ClientAudioPayload,
        ClientSessionChangePayload,
        ClientSessionStartConfig,
        ClientSessionStartPayload,
    )

    orch = DialogOrchestrator(
        handler=fake_handler,
        codec=fake_handler._codec,
        session_mgr=SessionManager(),
        asr_service=FakeAsrService(),
        llm_service=FakeLlmService(),
        tts_service=FakeTtsService(),
        system_prompt="You are helpful.",
        chat_store=FakeChatStore(),
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
            if _type_in(fake_handler, "session.ready"):
                break
            await asyncio.sleep(0.02)
        assert _type_in(fake_handler, "session.ready"), "session.ready 未到达"

        # 畸形音频帧（长度 ≡1 mod 4：过协议正则、b64decode 必抛）
        fake_handler._inbound.put_nowait(
            ClientAudio(
                seq=2,
                timestamp=2000,
                payload=ClientAudioPayload(
                    data="AAAAA", codec="opus", duration_ms=20, vad_state="speech",
                ),
            )
        )
        await asyncio.sleep(0.2)

        # 主循环必须还活着
        assert not run_task.done(), "主循环被畸形帧杀死（修复前即此症状）"

        # 活性探测：后续消息仍能被处理并产生回执
        n_before = len(fake_handler.outbound)
        fake_handler._inbound.put_nowait(
            ClientSessionChange(
                seq=3,
                timestamp=3000,
                payload=ClientSessionChangePayload(mode="chat"),
            )
        )
        for _ in range(100):
            if _type_in(fake_handler, "session.changed"):
                break
            await asyncio.sleep(0.02)
        assert _type_in(fake_handler, "session.changed"), "畸形帧后派发循环不再处理消息"
        assert len(fake_handler.outbound) > n_before
    finally:
        fake_handler.stop()
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
