from __future__ import annotations

import json

import pytest
from livachat_protocol.generated.client_message import ClientSessionStart

pytestmark = pytest.mark.integration


class TestFullTurn:
    async def test_session_start_sends_ready(self, fake_handler, fake_orchestrator):
        # Inject session.start into the inbound queue
        from livachat_protocol.generated.protocol_models import ClientSessionStartConfig, ClientSessionStartPayload

        msg = ClientSessionStart(
            seq=1,
            timestamp=1000,
            payload=ClientSessionStartPayload(
                session_id=None,
                config=ClientSessionStartConfig(
                    enable_video=False,
                    audio_sample_rate=24000,
                    vad_sensitivity="medium",
                    client_version="android-1.0.0",
                    language="zh-Hans",
                ),
            ),
        )
        fake_handler._inbound.put_nowait(msg)

        # Run orchestrator for a tick
        import asyncio

        task = asyncio.create_task(fake_orchestrator.run())
        await asyncio.sleep(0.2)
        fake_orchestrator._handler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should have session.ready
        assert len(fake_handler.outbound) >= 1
        ready = json.loads(fake_handler.outbound[0])
        assert ready["type"] == "session.ready"
        assert ready["payload"]["session_restored"] is False
        assert fake_orchestrator._ctx.language == "zh-Hans"

    async def test_complete_turn_flow(self, fake_handler, fake_orchestrator):
        """Simulate a full conversation turn: session.start → audio → thinking → speaking → listening."""
        from livachat_protocol.generated.client_message import ClientAudio, ClientSessionStart
        from livachat_protocol.generated.protocol_models import (
            ClientAudioPayload,
            ClientSessionStartConfig,
            ClientSessionStartPayload,
        )

        # 1. Start session
        fake_handler._inbound.put_nowait(
            ClientSessionStart(
                seq=1,
                timestamp=1000,
                payload=ClientSessionStartPayload(
                    session_id=None,
                    config=ClientSessionStartConfig(
                        enable_video=False,
                        audio_sample_rate=24000,
                        vad_sensitivity="high",
                        client_version="android-1.0.0",
                    ),
                ),
            )
        )

        import asyncio

        task = asyncio.create_task(fake_orchestrator.run())
        await asyncio.sleep(0.1)

        # Verify session.ready
        ready = json.loads(fake_handler.outbound[0])
        assert ready["type"] == "session.ready"

        # 2. Send audio frames enough to trigger turn end (high sensitivity = 20 frames)
        for i in range(30):
            fake_handler._inbound.put_nowait(
                ClientAudio(
                    seq=i + 2,
                    timestamp=2000 + i * 20,
                    payload=ClientAudioPayload(data="AAAA", codec="opus", duration_ms=20, vad_state="speech"),
                )
            )

        await asyncio.sleep(0.1)
        # Send silence to trigger turn end
        for i in range(25):
            fake_handler._inbound.put_nowait(
                ClientAudio(
                    seq=32 + i,
                    timestamp=3000 + i * 20,
                    payload=ClientAudioPayload(data="AAAA", codec="opus", duration_ms=20, vad_state="silence"),
                )
            )

        await asyncio.sleep(0.3)

        # Verify outbound contains ai_text messages
        texts = [json.loads(f) for f in fake_handler.outbound if json.loads(f)["type"] == "ai_text"]
        assert len(texts) >= 1

        fake_orchestrator._handler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
