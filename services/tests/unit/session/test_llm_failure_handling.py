"""LLM 失败处理 + 4xx 回滚（docs/fd_pipeline_llm_v2.md）单测。

锁定语义：
- 失败路径统一门控：error 透出 final ai_text（1k 截断、带图、partial 以 "\\n" 分隔）、
  不进 history/chat_store/recording、不喂 TTS/不迁 SPEAKING；
- 4xx 不可恢复回滚：两步守卫（① 令牌无条件登记 → ② record.token == turn_token 才删），
  删 WorkingMemory 条目与 messages 行（5 元组全取 record 写入时值）、read-and-clear；
- 非 4xx（500/429/None 状态）不回滚、不加令牌；token 不匹配只拦不删；
- 开流同步抛错 finally 判空守卫（无 NameError）；⑦ 非 ProviderError 兜底透出；
- 路径 B 迟到写拦截（令牌集合命中跳写、转录照发）、session.start 清 record 不清令牌集。
"""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from recording.sink import ChatImageFrame
from session.context import SessionContext
from session.history import WorkingMemory
from session.orchestrator import DialogOrchestrator
from session.state_machine import SessionState, SessionStateMachine
from shared.utils.errors import ErrorCode, ProviderError
from tests.unit.session.conftest import FakeChatStore

_JPEG = b"\xff\xd8fakejpeg"
_TID = "trans_abcd12345678"


def _chat_image_frame() -> ChatImageFrame:
    return ChatImageFrame(data=_JPEG, width=640, height=480, orientation=90, camera_facing="front")


class _FakeSink:
    def __init__(self):
        self.chat_texts: list[dict] = []

    def on_chat_text(self, **kwargs) -> None:
        self.chat_texts.append(kwargs)

    def on_transcribe_text(self, **kwargs) -> None:  # pragma: no cover
        pass


class _FakeLlm:
    """async generator 形状（与生产 LlmService 一致）：产 tokens 后按需抛 error。"""

    def __init__(self, tokens: list[str] | None = None, error: Exception | None = None):
        self.tokens = tokens or []
        self.error = error

    async def stream(self, *, messages, **opts):
        for t in self.tokens:
            yield t
        if self.error is not None:
            raise self.error


class _SyncRaiseLlm:
    """stream() 调用本身同步抛错（覆盖 finally 的 llm_iter 判空守卫）。"""

    def __init__(self, exc: Exception):
        self._exc = exc

    def stream(self, *, messages, **opts):
        raise self._exc


class _FakeTtsStream:
    def __init__(self):
        self.feed_text = AsyncMock()
        self.flush = AsyncMock()
        self.reset = AsyncMock()


class _FakeTtsService:
    def __init__(self):
        self.stream = _FakeTtsStream()

    async def open(self, *, voice, sample_rate):
        return self.stream


def _handler_mock() -> MagicMock:
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _make_orch(*, pipeline_mode: str = "full", with_tts: bool = False):
    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    ctx = SessionContext(
        session_id="test-sid", device_id="dev-1", user_id="user-1",
        sm=sm, enable_video=True, audio_sample_rate=16000,
        latest_video_frames=deque(),
    )
    sink = _FakeSink()
    orch = DialogOrchestrator(
        handler=_handler_mock(),
        codec=MagicMock(),
        session_mgr=None,
        asr_service=None,
        llm_service=None,
        tts_service=(_FakeTtsService() if with_tts else None),
        opus_encoder=None,
        opus_decoder=None,
        system_prompt="",
        generation_opts={},
        chat_store=FakeChatStore(),
        pipeline_mode=pipeline_mode,
        recording_sink=sink,
    )
    orch._ctx = ctx
    orch._history = WorkingMemory(max_turns=10)
    orch._turn_detector = MagicMock()
    orch._interrupt = MagicMock()
    orch._interrupt.should_yield.return_value = True
    orch._llm = _FakeLlm(tokens=["Hello"])
    if with_tts:
        # session 级复用：流已就位（失败路径断言不 feed/flush、finally reset）
        orch._tts_stream = orch._tts.stream
    return orch, sink


def _seed_turn_records(orch: DialogOrchestrator, *, token: str = _TID,
                       content: str = "问题") -> int:
    """模拟路径 A 已写：history + chat_store 行 + 回滚句柄（五值全写入时值）。"""
    ts = 123456
    orch._history.add_message("user", content, images=["data:image/jpeg;base64,AAA"])
    orch._chat_store.on_message(
        device_uuid="dev-1", session_id="test-sid", kind="asr", role="user",
        content=content, ts_ms=ts, images=["data:image/jpeg;base64,AAA"])
    orch._turn_user_record = {
        "content": content, "ts_ms": ts, "token": token,
        "device_uuid": "dev-1", "session_id": "test-sid",
    }
    return ts


def _capture_ai_text(orch: DialogOrchestrator) -> AsyncMock:
    mock = AsyncMock()
    orch._send_ai_text = mock
    return mock


def _err(message: str = "Error code: 401 - bad key",
         status: int | None = 401) -> ProviderError:
    return ProviderError(message, provider="p", code=ErrorCode.LLM_UNAVAILABLE,
                         http_status=status)


def _final_calls(mock: AsyncMock) -> list:
    return [c for c in mock.call_args_list if c.kwargs.get("is_final")]


class TestFailurePathGating:
    @pytest.mark.asyncio
    async def test_midstream_401_error_final_with_image_no_tts_rollback(self):
        """中途 4xx：final = partial + "\\n" + error、带图；无 TTS/无 assistant 三写；
        回滚本轮 history 条目与 messages 行（delete 5 元组取 record）。"""
        orch, sink = _make_orch(with_tts=True)
        orch._llm = _FakeLlm(tokens=["你好"], error=_err())
        ts = _seed_turn_records(orch)
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("问题", image_frames=[_chat_image_frame()],
                                   turn_token=_TID)

        finals = _final_calls(ai_text)
        assert len(finals) == 1
        # partial 以非 final 帧已上屏；final 只带 "\n"+error（客户端按 message_id
        # 累积为 "你好\nError code..."——分隔口径见 fd_pipeline_llm_v2.md）
        nonfinals = [c for c in ai_text.call_args_list if not c.kwargs.get("is_final")]
        assert [c.args[0] for c in nonfinals] == ["你好"]
        assert finals[-1].args[0] == "\nError code: 401 - bad key"
        assert finals[-1].kwargs["image"] is not None
        # 无 TTS：不 feed/flush；收尾 reset 回收；终态回到 LISTENING（未迁 SPEAKING）
        assert orch._tts.stream.feed_text.await_count == 0
        assert orch._tts.stream.flush.await_count == 0
        assert orch._tts.stream.reset.await_count == 1
        assert orch._ctx.sm.state == SessionState.LISTENING
        # 无 assistant 三写（chat_store / recording tap / history）
        assert all(m["role"] != "assistant" for m in orch._chat_store.messages)
        assert all(c["role"] != "assistant" for c in sink.chat_texts)
        # 回滚生效
        assert orch._history.get_recent() == []
        assert orch._chat_store.messages == []
        assert orch._chat_store.deleted_messages == [{
            "device_uuid": "dev-1", "session_id": "test-sid",
            "kind": "asr", "role": "user", "ts_ms": ts,
        }]
        assert orch._is_turn_rolled_back(_TID) is True

    @pytest.mark.asyncio
    async def test_pre_token_401_error_final_no_prefix(self):
        """首 token 前全败（async generator 形状）：final = error 原文、index=0、无前缀。"""
        orch, _ = _make_orch()
        orch._llm = _FakeLlm(tokens=[], error=_err())
        _seed_turn_records(orch)
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("问题", turn_token=_TID)

        finals = _final_calls(ai_text)
        assert len(finals) == 1
        assert finals[-1].args[0] == "Error code: 401 - bad key"
        assert finals[-1].kwargs["index"] == 0
        assert orch._history.get_recent() == []
        assert orch._chat_store.messages == []

    @pytest.mark.asyncio
    async def test_error_over_1k_truncated(self):
        orch, _ = _make_orch()
        orch._llm = _FakeLlm(tokens=[], error=_err("x" * 3000))
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("问题", turn_token=_TID)

        assert _final_calls(ai_text)[-1].args[0] == "x" * 1000

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 429, None])
    async def test_non_unrecoverable_no_rollback_no_token_mark(self, status):
        """500/429/http_status=None：透出 error 但不回滚、不加令牌、记录保留。"""
        orch, _ = _make_orch()
        orch._llm = _FakeLlm(tokens=[], error=_err("boom", status=status))
        _seed_turn_records(orch)
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("问题", turn_token=_TID)

        assert _final_calls(ai_text)                      # error 照常透出
        assert orch._is_turn_rolled_back(_TID) is False   # 集合无令牌（非 4xx 不加）
        assert orch._chat_store.deleted_messages == []
        assert len(orch._history.get_recent()) == 1
        assert len(orch._chat_store.messages) == 1

    @pytest.mark.asyncio
    async def test_401_record_token_mismatch_only_blocks(self):
        """stale holder（token 不匹配）：① 令牌仍登记 ② 只拦不删、record read-and-clear。"""
        orch, _ = _make_orch()
        orch._llm = _FakeLlm(tokens=[], error=_err())
        _seed_turn_records(orch, token="trans_other", content="别的话")
        _capture_ai_text(orch)

        await orch._start_thinking("问题", turn_token=_TID)

        assert orch._is_turn_rolled_back(_TID) is True
        assert orch._chat_store.deleted_messages == []
        assert len(orch._history.get_recent()) == 1       # 不误删同文/他人条目
        assert len(orch._chat_store.messages) == 1
        assert orch._turn_user_record is None

    @pytest.mark.asyncio
    async def test_unexpected_exception_7_surfaces_without_rollback(self):
        """⑦ 兜底：RuntimeError → 包装透出（http_status=None）、不回滚、不加令牌。"""
        orch, _ = _make_orch()
        orch._llm = _FakeLlm(tokens=["hi"], error=RuntimeError("cpu exploded"))
        _seed_turn_records(orch)
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("问题", turn_token=_TID)

        nonfinals = [c for c in ai_text.call_args_list if not c.kwargs.get("is_final")]
        assert [c.args[0] for c in nonfinals] == ["hi"]
        assert _final_calls(ai_text)[-1].args[0] == "\nLLM pipeline error: cpu exploded"
        assert orch._is_turn_rolled_back(_TID) is False
        assert orch._chat_store.deleted_messages == []

    @pytest.mark.asyncio
    async def test_sync_raise_no_nameerror_and_error_surfaced(self):
        """开流同步抛错：finally 判空守卫无 NameError；ProviderError 透出且照常回滚。"""
        orch, _ = _make_orch()
        orch._llm = _SyncRaiseLlm(_err("Error code: 401 - sync"))
        _seed_turn_records(orch)
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("问题", turn_token=_TID)   # 不得抛 NameError

        assert _final_calls(ai_text)[-1].args[0] == "Error code: 401 - sync"
        assert orch._history.get_recent() == []
        assert orch._chat_store.messages == []

    @pytest.mark.asyncio
    async def test_sync_raise_generic_wrapped(self):
        orch, _ = _make_orch()
        orch._llm = _SyncRaiseLlm(RuntimeError("wiring bug"))
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("问题", turn_token=_TID)

        assert _final_calls(ai_text)[-1].args[0] == "LLM pipeline error: wiring bug"

    @pytest.mark.asyncio
    async def test_loc_auto_failure_skips_json_replay(self):
        """loc_auto 轮失败：跳过 JSON 解析/重放（残片不上屏），error final 以 index=0 原样下发。"""
        orch, _ = _make_orch(pipeline_mode="translate")
        orch._loc_auto = True
        orch._my_language = "zh"
        orch._llm = _FakeLlm(tokens=['{"source_language": "en", "transl'], error=_err())
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("hello", turn_token=_TID)

        finals = _final_calls(ai_text)
        assert len(finals) == 1
        assert finals[-1].args[0] == "Error code: 401 - bad key"
        assert finals[-1].kwargs["index"] == 0
        assert [c for c in ai_text.call_args_list if not c.kwargs.get("is_final")] == []

    @pytest.mark.asyncio
    async def test_rollback_runs_even_when_interrupted_after_failure(self):
        """失败同刻被打断：回滚仍已同步完成（回滚先于 should_yield 检查，D5c）；
        被中断的轮不发 final error 文本（可接受——客户端已收到 interrupted 状态）。"""
        orch, _ = _make_orch()
        orch._llm = _FakeLlm(tokens=[], error=_err())
        _seed_turn_records(orch)
        orch._interrupt.should_yield.return_value = False
        ai_text = _capture_ai_text(orch)

        await orch._start_thinking("问题", turn_token=_TID)

        assert orch._history.get_recent() == []      # 回滚已完成
        assert orch._chat_store.messages == []
        assert _final_calls(ai_text) == []           # 打断 → 不发 final

    @pytest.mark.asyncio
    async def test_v3_video_token_rollback(self):
        """v3 视频文本轮令牌（ref_id）同机制回滚。"""
        orch, _ = _make_orch()
        orch._llm = _FakeLlm(tokens=[], error=_err())
        ref_id = "video-42"
        ts = 789
        orch._history.add_message("user", "看下这个", images=["data:image/jpeg;base64,AAA"])
        orch._chat_store.on_message(
            device_uuid="dev-1", session_id="test-sid", kind="asr", role="user",
            content="看下这个", ts_ms=ts, images=["data:image/jpeg;base64,AAA"],
            ref_id=ref_id)
        orch._turn_user_record = {
            "content": "看下这个", "ts_ms": ts, "token": ref_id,
            "device_uuid": "dev-1", "session_id": "test-sid",
        }
        _capture_ai_text(orch)

        await orch._start_thinking("看下这个", turn_token=ref_id)

        assert orch._history.get_recent() == []
        assert orch._chat_store.messages == []
        assert orch._is_turn_rolled_back(ref_id) is True


class TestRollbackTokenSet:
    def test_bounded_eviction(self):
        orch, _ = _make_orch()
        for i in range(9):
            orch._mark_turn_rolled_back(f"t{i}")
        assert orch._is_turn_rolled_back("t0") is False   # 超界淘汰最旧
        assert orch._is_turn_rolled_back("t8") is True
        assert orch._is_turn_rolled_back("") is False     # 空令牌永不命中

    @pytest.mark.asyncio
    async def test_path_b_late_delivery_blocked_but_transcription_sent(self):
        """令牌命中：路径 B 迟到写不落 history/chat_store；转录照发、recording 不拦
        （jsonl 不在回滚范围）。"""
        orch, sink = _make_orch()
        orch._mark_turn_rolled_back(_TID)
        orch._current_transcription_id = _TID

        async def _gen():
            r = MagicMock()
            r.is_final = True
            r.text = "迟到句"
            r.language = "zh"
            yield r

        orch._asr_stream = MagicMock()
        orch._asr_stream.results = _gen

        await orch._poll_asr_results()

        assert orch._history.get_recent() == []
        assert orch._chat_store.messages == []
        assert orch._handler.enqueue_outbound.await_count >= 1   # 转录照发
        user_rows = [c for c in sink.chat_texts if c["role"] == "user"]
        assert user_rows and user_rows[0]["text"] == "迟到句"

    @pytest.mark.asyncio
    async def test_path_b_delivery_writes_then_401_rollback_end_to_end(self):
        """路径 B 全链路：真实送达写入（含句柄）→ 4xx 回滚按 deliver 值删除
        （device/session/ts 取写入时值，端到端锁定 _deliver_final 的句柄代码）。"""
        orch, _ = _make_orch()
        orch._llm = _FakeLlm(tokens=[], error=_err())
        orch._current_transcription_id = _TID

        async def _gen():
            r = MagicMock()
            r.is_final = True
            r.text = "路径B句"
            r.language = "zh"
            yield r

        orch._asr_stream = MagicMock()
        orch._asr_stream.results = _gen
        await orch._poll_asr_results()

        # 送达已写：history + chat_store + 回滚句柄（token=tid、deliver_ctx 值）
        assert len(orch._history.get_recent()) == 1
        assert len(orch._chat_store.messages) == 1
        rec = orch._turn_user_record
        assert rec is not None and rec["token"] == _TID
        assert rec["device_uuid"] == "dev-1" and rec["session_id"] == "test-sid"
        ts = orch._chat_store.messages[0]["ts_ms"]
        assert rec["ts_ms"] == ts

        ai_text = _capture_ai_text(orch)
        await orch._start_thinking("路径B句", turn_token=_TID)

        assert _final_calls(ai_text)                      # 4xx 照常透出
        assert orch._history.get_recent() == []
        assert orch._chat_store.messages == []
        assert orch._chat_store.deleted_messages == [{
            "device_uuid": "dev-1", "session_id": "test-sid",
            "kind": "asr", "role": "user", "ts_ms": ts,
        }]

    @pytest.mark.asyncio
    async def test_session_start_clears_record_keeps_tokens(self):
        """session.start：清回滚句柄（防跨会话 stale 命中）；令牌集合不清（拦迟到写）。"""
        from livachat_protocol.generated.client_message import ClientSessionStart
        from livachat_protocol.generated.protocol_models import (
            ClientSessionStartConfig,
            ClientSessionStartPayload,
        )

        from session.manager import SessionManager

        orch = DialogOrchestrator(
            handler=_handler_mock(), codec=MagicMock(),
            session_mgr=SessionManager(), asr_service=None, llm_service=None,
            tts_service=None, opus_encoder=None, opus_decoder=None,
            system_prompt="", generation_opts={},
            chat_store=FakeChatStore(), pipeline_mode="full",
        )
        orch._turn_user_record = {
            "content": "x", "ts_ms": 1, "token": _TID,
            "device_uuid": "dev-1", "session_id": "test-sid",
        }
        orch._mark_turn_rolled_back(_TID)

        await orch._handle_session_start(ClientSessionStart(
            seq=1, timestamp=1000,
            payload=ClientSessionStartPayload(
                session_id=None,
                config=ClientSessionStartConfig(
                    enable_video=True, audio_sample_rate=24000,
                    vad_sensitivity="medium", client_version="android-1.0.0",
                ),
            ),
        ))

        assert orch._turn_user_record is None
        assert orch._is_turn_rolled_back(_TID) is True
