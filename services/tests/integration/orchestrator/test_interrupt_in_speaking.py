from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.integration


class TestInterruptInSpeaking:
    async def test_interrupt_increments_epoch(self, fake_handler, fake_orchestrator):
        from livachat_protocol.generated.client_message import ClientInterrupt, ClientSessionStart
        from livachat_protocol.generated.protocol_models import (
            ClientInterruptPayload,
            ClientSessionStartConfig,
            ClientSessionStartPayload,
        )

        # Start session
        fake_handler._inbound.put_nowait(
            ClientSessionStart(
                seq=1,
                timestamp=1000,
                payload=ClientSessionStartPayload(
                    session_id=None,
                    config=ClientSessionStartConfig(
                        enable_video=False,
                        audio_sample_rate=24000,
                        vad_sensitivity="medium",
                        client_version="android-1.0.0",
                    ),
                ),
            )
        )

        task = asyncio.create_task(fake_orchestrator.run())
        await asyncio.sleep(0.1)

        # Send interrupt
        fake_handler._inbound.put_nowait(
            ClientInterrupt(
                seq=2,
                timestamp=2000,
                payload=ClientInterruptPayload(reason="manual"),
            )
        )
        await asyncio.sleep(0.1)

        # Should have status message for interrupt
        statuses = [json.loads(f) for f in fake_handler.outbound if json.loads(f)["type"] == "status"]
        assert any(s["payload"]["reason"] == "interrupted" for s in statuses)

        fake_orchestrator._handler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
