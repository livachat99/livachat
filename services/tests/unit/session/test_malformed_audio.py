"""畸形音频帧不得杀死会话主循环（回归锁）。

两条可达路径：① 协议层 Base64String 正则不校验长度/padding——长度 ≡1 mod 4 或缺 padding
均过 DTO 校验，`base64.b64decode` 抛 binascii.Error；② 合法 base64 的垃圾字节被 libopus
拒绝，`OpusDecoder.decode` 抛 OSError。两者都必须被 `_decode_opus` 吞掉并丢弃该帧——
异常一旦穿出 `_dispatch`，`run()` 终结而连接/心跳仍在（会话静默死亡，客户端无感）。
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from session.orchestrator import _decode_opus as _decode_opus_dialog


class _RaisingDecoder:
    """模拟 libopus 拒绝垃圾包（OpusDecoder.decode 抛 OSError）。"""

    def decode(self, raw: bytes) -> bytes:
        raise OSError("opus_decode failed: -4")


class TestDecodeOpusRobustness:
    def test_invalid_base64_length_returns_empty(self) -> None:
        """长度 ≡1 mod 4（可过 DTO 正则）不得抛 binascii.Error。"""
        assert _decode_opus_dialog("A", _RaisingDecoder()) == b""
        assert _decode_opus_dialog("AAAAA", _RaisingDecoder()) == b""

    def test_bad_padding_returns_empty(self) -> None:
        """缺 padding（长度 ≡2 mod 4 且无 '='）同样丢弃。"""
        assert _decode_opus_dialog("AB", _RaisingDecoder()) == b""

    def test_decoder_error_returns_empty(self) -> None:
        """合法 base64 + 垃圾 opus 包（libopus OSError）同样丢弃。"""
        assert _decode_opus_dialog("AAAA", _RaisingDecoder()) == b""

    def test_valid_passthrough_without_decoder(self) -> None:
        assert _decode_opus_dialog("AAAA", None) == b"\x00\x00\x00"


# ---------------------------------------------------------------------------
# orchestrator 行为：畸形帧被丢弃（不进 TurnDetector/ASR/引擎）
# ---------------------------------------------------------------------------


def _handler_mock():
    h = MagicMock()
    h.enqueue_outbound = AsyncMock()
    h._inbound = MagicMock()
    h._stopped = False
    h.principal.device_id = "dev-1"
    h.principal.user_id = "user-1"
    return h


def _mk_orch(*, lecture: bool):
    from session.orchestrator import DialogOrchestrator
    from tests.unit.session.conftest import FakeChatStore

    orch = DialogOrchestrator(
        handler=_handler_mock(),
        codec=MagicMock(),
        session_mgr=None,
        asr_service=None,
        llm_service=None,
        tts_service=None,
        opus_encoder=None,
        opus_decoder=None,  # 畸形 base64 在解码器之前就失败
        system_prompt="s",
        generation_opts={},
        chat_store=FakeChatStore(),
        pipeline_mode="full",
    )

    from session.context import SessionContext
    from session.state_machine import SessionState, SessionStateMachine

    sm = SessionStateMachine()
    sm.state = SessionState.LISTENING
    orch._ctx = SessionContext(
        session_id="malformed-sid", device_id="dev-1", user_id="u",
        sm=sm, enable_video=False, audio_sample_rate=16000,
    )
    orch._turn_detector = MagicMock()
    orch._turn_detector.on_frame = MagicMock(return_value=False)
    orch._history = None
    if lecture:
        orch._lecture_active = True
        engine = MagicMock()
        engine.feed_pcm = AsyncMock()
        orch._lecture_engine = engine
    return orch


def _audio_msg(data: str):
    msg = MagicMock()
    msg.type = "audio"
    msg.payload.data = data
    msg.payload.vad_state.value = "speech"
    return msg


@pytest.mark.asyncio
async def test_malformed_frame_dropped_in_listening():
    """chat 路径：畸形帧被丢弃——不喂 ASR、不驱动 TurnDetector、无异常。"""
    orch = _mk_orch(lecture=False)

    await orch._handle_audio(_audio_msg("A"))  # 不得抛异常

    orch._turn_detector.on_frame.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_frame_dropped_in_lecture():
    """lecture 路径：畸形帧被丢弃——不喂 LiveTranslate 引擎、无异常。"""
    orch = _mk_orch(lecture=True)

    await orch._handle_audio(_audio_msg("AAAAA"))

    orch._lecture_engine.feed_pcm.assert_not_called()
    orch._lecture_engine.close.assert_not_called()  # 不得误触发降级关闭


@pytest.mark.asyncio
async def test_valid_frame_still_processed():
    """对照组：合法帧照常进 TurnDetector（确认丢弃逻辑不误伤）。"""
    orch = _mk_orch(lecture=False)

    await orch._handle_audio(_audio_msg("AAAA"))  # 合法 base64 → b"\\x00\\x00\\x00"

    orch._turn_detector.on_frame.assert_called_once_with(True)


# ---------------------------------------------------------------------------
# 日志节流：畸形帧不得放大成日志风暴（50 帧/秒 → 封顶每 30s 一条 warning）
# ---------------------------------------------------------------------------


class TestDroppedFrameLogThrottle:
    @pytest.fixture(autouse=True)
    def _reset_throttle(self, monkeypatch):
        import session.orchestrator as orch_mod

        monkeypatch.setattr(orch_mod, "_decode_warn_at", 0.0, raising=False)
        monkeypatch.setattr(orch_mod, "_decode_dropped", 0, raising=False)

    def test_first_warning_then_debug_without_stack(self, caplog) -> None:
        """5 帧畸形 → 1 条 warning（无 exc_info）+ 4 条 debug；消息含异常类型。"""
        with caplog.at_level(logging.DEBUG, logger="livachat.orchestrator"):
            for _ in range(5):
                assert _decode_opus_dialog("A", None) == b""

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(warnings) == 1
        assert warnings[0].exc_info is None
        # binascii.Error 的 __name__ 即 "Error"（与 opus 路径的 OSError 可区分）
        assert "dropped: Error" in warnings[0].getMessage()
        assert len(debugs) == 4

    def test_warning_per_interval_summarizes_count(self, caplog, monkeypatch) -> None:
        """窗口过期后（≥interval）再次 warning，并汇总窗口内累计条数。"""
        import session.orchestrator as orch_mod

        clock = {"t": 100.0}

        class _Clock:
            @staticmethod
            def monotonic() -> float:
                return clock["t"]

        monkeypatch.setattr(orch_mod, "time", _Clock)

        with caplog.at_level(logging.DEBUG, logger="livachat.orchestrator"):
            for _ in range(40):  # 故障客户端一窗口 40 帧
                assert _decode_opus_dialog("A", None) == b""
            clock["t"] += 31.0  # 越过节流窗口
            assert _decode_opus_dialog("A", None) == b""

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
        # 第一条 = 窗口首帧（计数 x1）；第二条 = 越过 30s 窗口后汇总窗口内 40 帧
        assert "(x1 since last warning)" in warnings[0].getMessage()
        assert "(x40 since last warning)" in warnings[1].getMessage()
        assert all(r.exc_info is None for r in warnings)
