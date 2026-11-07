"""ASR 空收尾清除帧单测：云端"先给 interim、再以空文本收尾"的撤回补偿。

真机现象（2026-09-13）：interim "1" 已上屏，该句随后以 text_len=0 收尾；服务端
原实现对空 final 不做任何动作，客户端又只在 isFinal && text 非空时清除
（ConversationListenerAdapter）→ 残影 "1" 常驻不消失。

方案：_poll_asr_results 记录 `_asr_interim_pending`（有 interim 已下发且未被结算），
空收尾时补发一条 text="" is_final=True 的 transcription 作为清除信号——复用现有
通道、零客户端改动；且不落 chatlog（record=False）、不进 history、不触发 turn end。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from asr.base import AsrResult
from tests.unit.session.test_asr_recovery import _feed_frame, _make_orch

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class ScriptedStream:
    """按脚本依次产出 AsrResult，产出完毕自然结束（供 _poll_asr_results 消费）。"""

    def __init__(self, results: list[AsrResult]) -> None:
        self._results = list(results)
        self.final_text = ""

    async def feed(self, pcm: bytes) -> None:
        pass

    def results(self):
        async def _gen():
            for r in self._results:
                yield r

        return _gen()

    async def final(self) -> str:
        return self.final_text

    async def reset(self) -> None:
        pass

    async def close(self) -> None:
        pass


async def _run_poll(orch, results: list[AsrResult]) -> None:
    orch._asr_stream = ScriptedStream(results)
    await orch._poll_asr_results()


def _transcriptions(orch) -> list[tuple[str, bool]]:
    """从 codec.build_typed 的调用记录里抽出 (text, is_final) 序列。"""
    out: list[tuple[str, bool]] = []
    for call in orch._codec.build_typed.call_args_list:
        payload = call.args[0] if call.args else None
        if payload is not None and hasattr(payload, "is_final"):
            out.append((payload.text, payload.is_final))
    return out


# ---------------------------------------------------------------------------
# 用例 1 — 核心场景：interim 上屏后空收尾 → 补发清除帧
# ---------------------------------------------------------------------------


class TestEmptyFinalClear:
    @pytest.mark.asyncio
    async def test_interim_then_empty_final_sends_clear_frame(self):
        orch = _make_orch(None)
        sink = orch._recording_sink = MagicMock()

        await _run_poll(orch, [
            AsrResult(text="1", is_final=False),
            AsrResult(text="", is_final=True),
        ])

        assert _transcriptions(orch) == [("1", False), ("", True)]
        assert orch._asr_interim_pending is False
        # 清除帧不是用户发言：不触发 turn end、不落 chatlog、不进 history
        assert orch._asr_final_sent is False
        orch._turn_detector.force_turn_end.assert_not_called()
        assert sink.on_chat_text.call_count == 0
        assert sink.on_transcribe_text.call_count == 0
        assert len(orch._history.get_recent()) == 0

    @pytest.mark.asyncio
    async def test_text_final_settles_interim_without_clear_frame(self):
        """正常路径回归：有文本 final 自行完成上屏+清除，不得多发清除帧。"""
        orch = _make_orch(None)

        await _run_poll(orch, [
            AsrResult(text="你好", is_final=False),
            AsrResult(text="你好", is_final=True),
        ])

        assert _transcriptions(orch) == [("你好", False), ("你好", True)]
        assert orch._asr_interim_pending is False

    @pytest.mark.asyncio
    async def test_empty_final_without_interim_sends_nothing(self):
        """噪声期大量空 final（真机实测占 2/3）不得产生额外流量。"""
        orch = _make_orch(None)

        await _run_poll(orch, [
            AsrResult(text="", is_final=True),
            AsrResult(text="", is_final=True),
        ])

        assert _transcriptions(orch) == []

    @pytest.mark.asyncio
    async def test_repeated_empty_finals_send_single_clear_frame(self):
        orch = _make_orch(None)

        await _run_poll(orch, [
            AsrResult(text="1", is_final=False),
            AsrResult(text="", is_final=True),
            AsrResult(text="", is_final=True),
        ])

        assert _transcriptions(orch) == [("1", False), ("", True)]

    @pytest.mark.asyncio
    async def test_new_interim_rearms_pending(self):
        """清除后再来新 interim，仍会被后续空收尾结算（第二次也要清）。"""
        orch = _make_orch(None)

        await _run_poll(orch, [
            AsrResult(text="1", is_final=False),
            AsrResult(text="", is_final=True),
            AsrResult(text="2", is_final=False),
            AsrResult(text="", is_final=True),
        ])

        assert _transcriptions(orch) == [
            ("1", False), ("", True), ("2", False), ("", True),
        ]


# ---------------------------------------------------------------------------
# 用例 6/7 — 标记生命周期（防回归：真机 turn end 比云端空收尾早 ~440ms）
# ---------------------------------------------------------------------------


class TestPendingLifecycle:
    @pytest.mark.asyncio
    async def test_turn_end_keeps_pending_interim(self):
        """轮次结束 ≠ 残影已被结算：turn end 时 final_text 仍为空（云端空收尾还没到），
        此时若复位标记，后续空收尾就不会补发清除帧——残影 bug 原样复现。"""
        orch = _make_orch(None)
        orch._asr_stream = ScriptedStream([])  # final_text == ""（收尾尚未到达）
        orch._asr_interim_pending = True       # interim "1" 已上屏
        orch._turn_detector.on_frame.return_value = True  # 触发 turn end

        await _feed_frame(orch)

        assert orch._asr_interim_pending is True

    @pytest.mark.asyncio
    async def test_path_a_text_final_settles_pending(self):
        """路径 A 真正送达有文本 final 时才结算标记（残影已由该 final 清除）。"""
        orch = _make_orch(None)
        stream = ScriptedStream([])
        stream.final_text = "你好"
        orch._asr_stream = stream
        orch._asr_interim_pending = True
        orch._turn_detector.on_frame.return_value = True

        await _feed_frame(orch)

        assert orch._asr_interim_pending is False
        assert ("你好", True) in _transcriptions(orch)
