"""Integration tests: R2 结果补投 — job done while device offline → device
reconnects (session.started) → sweep delivers the ai_text result.

去 Redis 后（chatserver_no_redis.md §3.2.2）：PushPublisher 直接经
ConnectionManager 进程内投递；「设备上线」= handler 注册进 conn_mgr。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_relay.adapters.loader import load_adapter
from agent_relay.config import AgentRelayConfig, HermesAdapterConfig
from agent_relay.delivery import JobResultDeliverer
from agent_relay.job_store_sqlite import SqliteJobStore
from agent_relay.models import make_session_started_event
from agent_relay.publisher import PushPublisher
from agent_relay.session_manager import AgentSessionManager
from gateway.connection import ConnectionHandler
from recording.session import RecordingSession
from session.history import WorkingMemory
from session.orchestrator import DialogOrchestrator
from shared.persistence.chat_store import ChatStore


class FakeHandler:
    """在线 handler 替身：记录入队帧与入史调用。"""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.enqueued: list[str] = []
        self.job_results: list[dict] = []

    async def enqueue_outbound(self, frame: str) -> None:
        self.enqueued.append(frame)

    def record_agent_job_result(self, *, text: str, message_id: str,
                                agent_name: str = "", agent_id: str = "",
                                job_key: str = "") -> None:
        self.job_results.append({"text": text, "message_id": message_id})


class _ConnMgr:
    def __init__(self):
        self._handlers: dict[str, FakeHandler | ConnectionHandler] = {}

    def set_handler(self, device_id: str, handler) -> None:
        self._handlers[device_id] = handler

    def get_handler(self, device_id: str):
        return self._handlers.get(device_id)

    def is_online(self, device_id: str) -> bool:
        return device_id in self._handlers


async def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return True
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.02)


@pytest.fixture
async def relay_components(mock_hermes, tmp_path):
    cfg = AgentRelayConfig()
    cfg.events_channel = "livachat_test:agent:events"  # deprecated，不读
    cfg.push_channel_prefix = "livachat_test:push"      # deprecated，不读
    cfg.adapters.hermes = HermesAdapterConfig(
        base_url=mock_hermes.url,
        api_key="",
        timeout_s=5,
        max_retries=1,
        mode="proactive",
        nudge_timeout_s=5,
    )
    cs = ChatStore(str(tmp_path / "chat_server.db"))
    await cs.start()
    job_store = SqliteJobStore(cs._db_path)
    conn_mgr = _ConnMgr()
    adapter = load_adapter("hermes", cfg)
    adapter.job_store = job_store
    adapter.hermes_store = cs
    adapter.base_dir = "/tmp"
    publisher = PushPublisher(conn_mgr, cfg)
    deliverer = JobResultDeliverer(
        job_store, publisher, cfg, online_check=conn_mgr.is_online)
    adapter.deliverer = deliverer

    async def _no_recording(device_uuid=None, **kw):
        return None

    sm = AgentSessionManager(
        adapters={"hermes": adapter},
        publisher=publisher,
        cfg=cfg,
        recording_lookup=_no_recording,
        base_dir="/tmp",
        job_store=job_store,
        deliverer=deliverer,
    )
    await adapter.connect()
    yield {"cfg": cfg, "job_store": job_store, "adapter": adapter,
           "publisher": publisher, "deliverer": deliverer, "sm": sm,
           "conn_mgr": conn_mgr}
    await sm.shutdown()
    await cs.stop()


class TestRedelivery:
    async def test_offline_complete_then_reconnect_redelivers(
        self, mock_hermes, relay_components
    ):
        """设备离线时 job 完成 → 结果留 done → 上线后 sweep 补投 ai_text。"""
        comp = relay_components
        device = "redel-dev-1"

        # 设备离线：job 在 JobStore 里创建并完成（无 handler → publish 失败）
        job = await comp["job_store"].create(
            session_id="redel-sess-1", device_id=device, summary="查天气"
        )
        await comp["job_store"].mark_running(job.job_key, "task-redel-1")
        await comp["job_store"].mark_done(job.job_key, "明天晴,22 度")
        pending = await comp["job_store"].list_pending_delivery(device)
        assert len(pending) == 1

        # 设备上线：handler 注册进 conn_mgr + session.started → SM 触发异步 sweep
        handler = FakeHandler(device)
        comp["conn_mgr"].set_handler(device, handler)
        ev = make_session_started_event(
            device_id=device, session_id="redel-sess-2", ts_ms=1000
        )
        await comp["sm"].on_session_started(ev)

        # sweep 是 fire-and-forget task：等 job 进入 delivered 终态
        async def _delivered() -> bool:
            fetched = await comp["job_store"].get(job.job_key)
            return fetched is not None and fetched.status == "delivered"

        assert await _wait_until(_delivered)
        assert await _wait_until(lambda: len(handler.enqueued) >= 1)

        frame = json.loads(handler.enqueued[0])
        assert frame["type"] == "ai_text"
        assert frame["payload"]["text"] == "明天晴,22 度"
        assert frame["payload"]["proactive"] is True
        assert frame["payload"]["message_id"] == f"msg_agent_job_{job.job_key}"
        # 投递后标记 delivered，不再补投
        fetched = await comp["job_store"].get(job.job_key)
        assert fetched.status == "delivered"

    async def test_sweep_after_reconnect_no_double_send(
        self, mock_hermes, relay_components
    ):
        """补投已投递后再次上线不重投（delivered 终态）。"""
        comp = relay_components
        device = "redel-dev-2"

        job = await comp["job_store"].create(
            session_id="s1", device_id=device, summary="s"
        )
        await comp["job_store"].mark_running(job.job_key, "task-2")
        await comp["job_store"].mark_done(job.job_key, "ok")
        await comp["job_store"].mark_delivered(job.job_key)

        handler = FakeHandler(device)
        comp["conn_mgr"].set_handler(device, handler)
        ev = make_session_started_event(device_id=device, session_id="s2", ts_ms=1000)
        await comp["sm"].on_session_started(ev)
        await asyncio.sleep(0.5)  # 让 sweep 跑完

        assert handler.enqueued == []  # delivered 终态不重投

    async def test_watchdog_does_not_reap_done_undelivered(
        self, mock_hermes, relay_components
    ):
        """done 未投递 job 不因看门狗收敛（48h 窗口内有效）。"""
        comp = relay_components
        job = await comp["job_store"].create(
            session_id="s1", device_id="redel-dev-3", summary="s"
        )
        await comp["job_store"].mark_running(job.job_key, "task-3")
        await comp["job_store"].mark_done(job.job_key, "result")

        stale = await comp["job_store"].scan_stale(
            asyncio.get_running_loop().time() + 1000,
            running_timeout_s=60,
            requested_timeout_s=60,
        )
        assert stale == []
        fetched = await comp["job_store"].get(job.job_key)
        assert fetched.status == "done"


class TestFullChainIngest:
    """fd_pipeline_llm_v2：done→deliver→push 消费全链——WorkingMemory + chatlog
    各一条；崩溃重投（同 message_id 第二次 push）→ WorkingMemory 仍一条、
    chatlog 物理重复行同 ref_id（§3.4 已知行为）。

    使用真实 RecordingSession（queue → writer task → ChatLogWriter 落盘），
    避免 fake sink 掩盖真实 sink 的 source 透传回归。"""

    RESULT = "查好了，明天晴 22 度"

    async def _make_recording(self, tmp_path, device: str) -> RecordingSession:
        rec = RecordingSession(
            recording_session_id="rec-fullchain",
            device_uuid=device,
            session_date="2026-08-31",
            storage_path=tmp_path,
            config={},
        )
        await rec.start()
        return rec

    async def test_deliver_consumes_into_history_and_chatlog(
        self, mock_hermes, relay_components, tmp_path
    ):
        comp = relay_components
        device = "fullchain-dev-1"
        chatlog_path = tmp_path / "chat" / "history.jsonl"

        job = await comp["job_store"].create(
            session_id="s1", device_id=device, summary="查天气"
        )
        await comp["job_store"].mark_running(job.job_key, "task-full-1")
        await comp["job_store"].mark_done(job.job_key, self.RESULT)

        rec = await self._make_recording(tmp_path, device)
        orch = DialogOrchestrator(
            handler=SimpleNamespace(enqueue_outbound=AsyncMock()),
            codec=None, recording_sink=rec,
        )
        orch._ctx = SimpleNamespace(session_id="live-full-1", device_id=device)
        orch._history = WorkingMemory()
        handler = ConnectionHandler(
            ws=None,
            principal=SimpleNamespace(device_id=device),
            codec=None, seq_guard=None, outbound_seq=None,
            heartbeat=None, decode_guard=None,
            orchestrator=orch, sink=None,
        )
        comp["conn_mgr"].set_handler(device, handler)
        try:
            ok = await comp["deliverer"].deliver(job)
            assert ok is True

            async def _ingested() -> bool:
                return len(orch._history) == 1

            assert await _wait_until(_ingested)

            # ① WorkingMemory：role=assistant、provenance 前缀、source/ref_id
            entry = orch._history.get_recent()[0]
            assert entry["role"] == "assistant"
            assert entry["content"].startswith("[Agent job result]\n")
            assert self.RESULT in entry["content"]
            assert entry["source"] == "agent_job"
            assert entry["ref_id"] == f"msg_agent_job_{job.job_key}"

            # ② chatlog（真实 RecordingSession 队列 → writer → 落盘）：
            #    全文、无前缀、ref_id 与 message_id 一致
            async def _written(n: int) -> bool:
                return chatlog_path.exists() and len(chatlog_path.read_text().strip().splitlines()) >= n

            assert await _wait_until(lambda: _written(1))
            rows = [json.loads(line) for line in chatlog_path.read_text().strip().splitlines()]
            assert len(rows) == 1
            row = rows[0]
            assert row["role"] == "assistant"
            assert row["text"] == self.RESULT
            assert not row["text"].startswith("[Agent job result]")
            assert row["ref_id"] == f"msg_agent_job_{job.job_key}"
        finally:
            await rec.finalize()

    async def test_crash_redelivery_dedups_history_accepts_chatlog_rows(
        self, mock_hermes, relay_components, tmp_path
    ):
        """崩溃重投（同 message_id 第二次 push）：WorkingMemory 仍一条（dedup），
        chatlog 物理重复行同 ref_id（§3.4 已知行为）。"""
        comp = relay_components
        device = "fullchain-dev-2"
        chatlog_path = tmp_path / "chat" / "history.jsonl"

        job = await comp["job_store"].create(
            session_id="s1", device_id=device, summary="查天气"
        )
        await comp["job_store"].mark_running(job.job_key, "task-full-2")
        await comp["job_store"].mark_done(job.job_key, self.RESULT)

        rec = await self._make_recording(tmp_path, device)
        orch = DialogOrchestrator(
            handler=SimpleNamespace(enqueue_outbound=AsyncMock()),
            codec=None, recording_sink=rec,
        )
        orch._ctx = SimpleNamespace(session_id="live-full-2", device_id=device)
        orch._history = WorkingMemory()
        handler = ConnectionHandler(
            ws=None,
            principal=SimpleNamespace(device_id=device),
            codec=None, seq_guard=None, outbound_seq=None,
            heartbeat=None, decode_guard=None,
            orchestrator=orch, sink=None,
        )
        comp["conn_mgr"].set_handler(device, handler)
        try:
            # 第一次投递
            assert await comp["deliverer"].deliver(job) is True
            async def _ingested() -> bool:
                return len(orch._history) == 1
            assert await _wait_until(_ingested)

            # 崩溃重投：直接 publish 同 message_id 帧（模拟 publisher 路径重发）
            msg = await _publish_job_result(comp, orch, handler, job)
            assert msg is True

            # WorkingMemory 仍一条（ref_id dedup）
            assert len(orch._history) == 1
            # chatlog 物理重复行（同 ref_id）
            async def _written(n: int) -> bool:
                return chatlog_path.exists() and len(chatlog_path.read_text().strip().splitlines()) >= n
            assert await _wait_until(lambda: _written(2))
            rows = [json.loads(line) for line in chatlog_path.read_text().strip().splitlines()]
            assert len(rows) == 2
            assert all(r["ref_id"] == f"msg_agent_job_{job.job_key}" for r in rows)
        finally:
            await rec.finalize()


async def _publish_job_result(comp, orch, handler, job) -> bool:
    """模拟崩溃重投：PushMessage(job_key) 经 publisher 直接投递（§3.2.2
    路径——帧 + 入史）。从 store 重读 fresh record（result_text 在 mark_done
    时落库，内存 job 对象不含）。"""
    from agent_relay.models import PushMessage
    fresh = await comp["job_store"].get(job.job_key)
    assert fresh is not None and fresh.result_text
    msg = PushMessage(
        device_id=fresh.device_id,
        session_id=orch._ctx.session_id,
        message_id=f"msg_agent_job_{fresh.job_key}",
        text=fresh.result_text,
        index=0,
        is_final=True,
        agent_name="hermes",
        proactive=True,
        job_key=fresh.job_key,
    )
    return await comp["publisher"].publish(msg)
