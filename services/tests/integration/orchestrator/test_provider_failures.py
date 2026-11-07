from __future__ import annotations

import asyncio
import json

import pytest

from shared.utils.errors import ErrorCode, ProviderError

pytestmark = pytest.mark.integration


class _FailingProvider:
    """首 token 前全败（async generator 形状，与生产一致），携带 http_status。"""

    name = "fail"
    supports_vision = False

    async def stream(self, *, messages, **opts):
        raise ProviderError("Error code: 401 - down", provider="fail",
                            code=ErrorCode.LLM_UNAVAILABLE, http_status=401)
        yield  # pragma: no cover


class TestProviderFailures:
    async def test_llm_401_error_text_no_error_frame_rollback(self, fake_handler):
        """全败（401）：ai_text 透出 error 文本（fd_pipeline_llm_v2.md）、不发错误帧
        （1003 已拍板不发）、4xx 回滚本轮 user 记录（D5）。

        真实装配：WireCodec/OutboundSeqAllocator + SessionManager + 真实 LlmService
        （active provider 首 token 即抛，无 fallback → 服务层包装上抛）。"""
        from llm.service import LlmService
        from session.manager import SessionManager
        from session.orchestrator import DialogOrchestrator
        from tests.integration.orchestrator.conftest import FakeChatStore

        store = FakeChatStore()
        orch = DialogOrchestrator(
            handler=fake_handler,
            codec=fake_handler._codec,
            session_mgr=SessionManager(),
            asr_service=None,
            llm_service=LlmService(active=_FailingProvider(), fallbacks=[]),
            tts_service=None,
            system_prompt="You are helpful.",
            chat_store=store,
        )

        from livachat_protocol.generated.client_message import ClientSessionStart
        from livachat_protocol.generated.protocol_models import (
            ClientSessionStartConfig,
            ClientSessionStartPayload,
        )

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

        task = asyncio.create_task(orch.run())
        await asyncio.sleep(0.1)

        ready = json.loads(fake_handler.outbound[0])
        assert ready["type"] == "session.ready"

        # 模拟路径 A 已写本轮 user 记录（history + messages 行 + 回滚句柄）
        ts = 123456
        orch._history.add_message("user", "问题", images=["data:image/jpeg;base64,AAA"])
        store.on_message(device_uuid=orch._ctx.device_id, session_id=orch._ctx.session_id,
                         kind="asr", role="user", content="问题", ts_ms=ts,
                         images=["data:image/jpeg;base64,AAA"])
        orch._turn_user_record = {
            "content": "问题", "ts_ms": ts, "token": "t-1",
            "device_uuid": orch._ctx.device_id, "session_id": orch._ctx.session_id,
        }

        await orch._start_thinking("问题", turn_token="t-1")

        frames = [json.loads(f) for f in fake_handler.outbound]
        # ① error 文本经 ai_text final 透出（无 partial → 原样 str(ProviderError)，
        #    即服务层包装后的 "all LLM providers failed: ..." 全文）
        finals = [fr for fr in frames if fr["type"] == "ai_text"
                  and fr["payload"]["is_final"]]
        assert finals and finals[-1]["payload"]["text"] == \
            "all LLM providers failed: Error code: 401 - down"
        # ② 无 error 帧（1003 已拍板不发）
        assert all(fr["type"] != "error" for fr in frames)
        # ③ 4xx 回滚：history 条目与 messages 行删除
        assert orch._history.get_recent() == []
        assert store.messages == []
        assert store.deleted_messages == [{
            "device_uuid": orch._ctx.device_id, "session_id": orch._ctx.session_id,
            "kind": "asr", "role": "user", "ts_ms": ts,
        }]

        fake_handler.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
