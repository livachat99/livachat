"""Integration tests: HermesAdapter proactive path against the mock server.

In-process (SQLite/none) coverage only: concurrent session start, streaming
straggler handling, turn interrupt, and restore edge cases. The legacy
real-Redis JobStore cases were removed with the Redis dependency.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from agent_relay.adapters.base import AgentContext, ChatlogLine, ChatlogUpdate
from agent_relay.adapters.hermes import HermesAdapter


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(session_id="psess-1", device_id="pdev-1", language="zh")


def _update(**overrides) -> ChatlogUpdate:
    kwargs = {
        "path": "/tmp/pdev-1/rec-1/chat/history.jsonl",
        "chat_session_id": "psess-1",
        "new_lines": [ChatlogLine(role="user", text="帮我查机票", ts_ms=1000)],
    }
    kwargs.update(overrides)
    return ChatlogUpdate(**kwargs)


class TestConcurrentSessionStart:
    async def test_concurrent_on_session_start_creates_once(self, mock_hermes, ctx):
        """The lifecycle task and the watcher's lazy start can race; only one
        session.create may happen (the loser must reuse the winner's session)."""
        from agent_relay.config import HermesAdapterConfig as _Cfg

        cfg = _Cfg(
            base_url=mock_hermes.url, api_key="", timeout_s=5, max_retries=1,
            mode="proactive", nudge_timeout_s=5,
        )
        a = HermesAdapter(cfg)
        await a.connect()
        try:
            await asyncio.gather(a.on_session_start(ctx), a.on_session_start(ctx))
            assert len(mock_hermes.sessions) == 1
            assert "pdev-1" in a._devices
            creates = [r for r in mock_hermes.requests if r["method"] == "session.create"]
            assert len(creates) == 1
        finally:
            await a.close()


class TestNudgeLanguageHint:
    async def test_mission_and_nudge_carry_language_hint(self, mock_hermes):
        """docs/agent_multilingual.md：mission 与每轮 nudge 都携带目标语言。"""
        from agent_relay.config import HermesAdapterConfig as _Cfg

        cfg = _Cfg(
            base_url=mock_hermes.url, api_key="", timeout_s=5, max_retries=1,
            mode="proactive", nudge_timeout_s=5,
        )
        a = HermesAdapter(cfg)
        await a.connect()
        try:
            ctx = AgentContext(session_id="psess-1", device_id="pdev-1", language="ja")
            mock_hermes.submit_handler = lambda params: '{"action": "noop"}'
            await a.on_session_start(ctx)
            assert [c async for c in a.nudge(_update(), ctx)] == []

            create = next(r for r in mock_hermes.requests if r["method"] == "session.create")
            mission = create["params"]["messages"][0]["content"]
            assert '面向用户的输出语言:`"ja"`' in mission

            submit = next(r for r in mock_hermes.requests if r["method"] == "prompt.submit")
            text = submit["params"]["text"]
            assert text.startswith('面向用户输出语言:`"ja"`')
            assert '{"type": "chatlog_update"' in text
        finally:
            await a.close()


class TestRestoreEdgeCases:
    async def test_session_end_closes_inflight_start(self, mock_hermes, ctx):
        """on_session_end 在 on_session_start mid-create 时到达：start_lock 串行
        保证 create 完成后按 D3 关连接（不 close hermes session）。"""
        from agent_relay.config import HermesAdapterConfig as _Cfg

        cfg = _Cfg(
            base_url=mock_hermes.url, api_key="", timeout_s=5, max_retries=1,
            mode="proactive", nudge_timeout_s=5,
        )
        a = HermesAdapter(cfg)
        await a.connect()
        try:
            mock_hermes.create_delay_s = 0.5
            start_task = asyncio.create_task(a.on_session_start(ctx))
            await asyncio.sleep(0.1)  # start is now inside session.create
            await a.on_session_end(ctx)
            await start_task

            # 无 active job → 连接被回收；hermes session 不被 close（D3）
            assert "pdev-1" not in a._devices
            assert not any(n.get("method") == "session.close" for n in mock_hermes.notifications)
        finally:
            await a.close()


    async def test_submit_rpc_timeout_marks_straggler(self, mock_hermes, ctx):
        """An RPC-level timeout on prompt.submit (cfg.timeout_s firing before
        nudge_timeout_s) must count as a nudge timeout and arm the straggler
        guard — the late complete must be dropped."""
        from agent_relay.config import HermesAdapterConfig as _Cfg

        cfg = _Cfg(
            base_url=mock_hermes.url, api_key="", timeout_s=1, max_retries=1,
            mode="proactive", nudge_timeout_s=5,
        )
        a = HermesAdapter(cfg)
        await a.connect()
        try:
            await a.on_session_start(ctx)
            sid = a._devices["pdev-1"].hermes_session_id

            mock_hermes.submit_no_response = True
            chunks = [c async for c in a.nudge(_update(), ctx)]
            assert chunks == []
            assert sid in a._devices["pdev-1"].turn_stragglers  # RPC timeout armed the guard

            # The straggler complete (if it ever arrived) is consumed on drop.
            mock_hermes.submit_no_response = False
            mock_hermes.emit_event("message.complete", {"text": "residue", "session_id": sid})
            await asyncio.sleep(0.1)
            assert sid not in a._devices["pdev-1"].turn_stragglers
        finally:
            await a.close()


class TestStragglerComplete:
    async def test_late_complete_after_timeout_is_dropped(self, mock_hermes, ctx):
        """P2-5: a message.complete from an interrupted turn arriving after a
        local nudge timeout must not be parsed as the next turn's contract."""
        from agent_relay.config import HermesAdapterConfig as _Cfg

        cfg = _Cfg(
            base_url=mock_hermes.url, api_key="", timeout_s=5, max_retries=1,
            mode="proactive", nudge_timeout_s=1,
        )
        a = HermesAdapter(cfg)
        await a.connect()
        try:
            await a.on_session_start(ctx)
            info = a._devices["pdev-1"]
            sid = info.hermes_session_id

            # First turn is slow (> nudge timeout) → local timeout, straggler
            # marked. The old turn's complete then arrives and must be dropped.
            mock_hermes.submit_delay_s = 1.5
            mock_hermes.submit_handler = lambda params: json.dumps(
                {"action": "message", "text": "OLD-TURN-RESIDUE"}
            )
            chunks1 = [c async for c in a.nudge(_update(), ctx)]
            assert chunks1 == []  # timed out locally
            assert sid in a._devices["pdev-1"].turn_stragglers
            await asyncio.sleep(1.0)  # straggler complete arrives and is dropped
            assert sid not in a._devices["pdev-1"].turn_stragglers  # consumed

            # Second turn completes normally → parsed as the new contract.
            mock_hermes.submit_delay_s = 0.0
            mock_hermes.submit_handler = lambda params: json.dumps(
                {"action": "message", "text": "NEW-TURN-OK"}
            )
            chunks2 = [c async for c in a.nudge(_update(), ctx)]
            assert len(chunks2) == 1
            assert chunks2[0].text == "NEW-TURN-OK"
        finally:
            await a.close()


async def _drain(agen) -> list:
    return [c async for c in agen]


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return True
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.01)


class TestStragglerWindow:
    async def test_expired_marker_releases_complete(self, mock_hermes, ctx):
        """跨会话残留的 straggler 标记过期（>2×nudge_timeout_s）后，
        新 turn 的 complete 必须放行——不跨 turn 误杀。"""
        from agent_relay.config import HermesAdapterConfig as _Cfg

        cfg = _Cfg(
            base_url=mock_hermes.url, api_key="", timeout_s=5, max_retries=1,
            mode="proactive", nudge_timeout_s=1,
        )
        a = HermesAdapter(cfg)
        await a.connect()
        try:
            await a.on_session_start(ctx)
            info = a._devices["pdev-1"]
            sid = info.hermes_session_id
            # 手工置位过期标记（模拟上一会话遗留未消费）
            info.turn_stragglers[sid] = time.time() - 10
            mock_hermes.submit_handler = lambda p: json.dumps(
                {"action": "message", "text": "FRESH-TURN"}
            )
            chunks = [c async for c in a.nudge(_update(), ctx)]
            assert len(chunks) == 1
            assert chunks[0].text == "FRESH-TURN"
            assert sid not in info.turn_stragglers  # 标记已一次性消费
        finally:
            await a.close()


class TestTurnInterrupt:
    async def test_timeout_sends_session_interrupt(self, mock_hermes, ctx):
        """nudge 超时说明主 agent 异常：发 session.interrupt 打断坏 turn。"""
        from agent_relay.config import HermesAdapterConfig as _Cfg

        cfg = _Cfg(
            base_url=mock_hermes.url, api_key="", timeout_s=5, max_retries=1,
            mode="proactive", nudge_timeout_s=1,
        )
        a = HermesAdapter(cfg)
        await a.connect()
        try:
            await a.on_session_start(ctx)
            sid = a._devices["pdev-1"].hermes_session_id
            mock_hermes.submit_delay_s = 1.5  # 慢 turn > nudge_timeout
            chunks = [c async for c in a.nudge(_update(), ctx)]
            assert chunks == []
            # notification 是异步送达 mock 的，需轮询等待
            assert await _wait_until(
                lambda: any(
                    n.get("method") == "session.interrupt"
                    for n in mock_hermes.notifications
                )
            )
            interrupts = [
                n for n in mock_hermes.notifications
                if n.get("method") == "session.interrupt"
            ]
            assert interrupts[0]["params"]["session_id"] == sid
            # 超时 turn 的补发 complete 在窗口内被丢弃（straggler 置位后
            # 被一次性消费），不会冒充新 turn 的契约。
            assert await _wait_until(
                lambda: sid not in a._devices["pdev-1"].turn_stragglers
            )
        finally:
            await a.close()

    async def test_interrupt_throttled(self, mock_hermes, ctx):
        """连续超时 10s 内只 interrupt 一次（防 interrupt 风暴）。"""
        from agent_relay.config import HermesAdapterConfig as _Cfg

        cfg = _Cfg(
            base_url=mock_hermes.url, api_key="", timeout_s=5, max_retries=1,
            mode="proactive", nudge_timeout_s=1,
        )
        a = HermesAdapter(cfg)
        await a.connect()
        try:
            await a.on_session_start(ctx)
            mock_hermes.submit_delay_s = 1.5
            await _drain(a.nudge(_update(), ctx))
            await _drain(a.nudge(_update(), ctx))
            assert await _wait_until(
                lambda: sum(
                    1 for n in mock_hermes.notifications
                    if n.get("method") == "session.interrupt"
                ) == 1
            )
        finally:
            await a.close()
