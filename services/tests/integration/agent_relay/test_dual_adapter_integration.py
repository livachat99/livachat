"""Integration tests: hermes + openclaw adapters running simultaneously.

Verifies the dual-adapter coexistence contract
(docs/openclaw_adaptor_design.md §5.3/D11):
- both adapters dispatch jobs against ONE SqliteJobStore; jobs carry
  per-record agent_name; delivery attributes name/nickname correctly;
- the shared ChatlogWatcher fans one chatlog update out to both adapters;
- per-device job caps count BOTH adapters' in-flight jobs (device budget);
- per-adapter watchdogs only converge their own jobs (scan_stale filter).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import pytest

from agent_relay.adapters.base import AgentContext, ChatlogLine, ChatlogUpdate
from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.adapters.openclaw import OpenClawAdapter
from agent_relay.chatlog_watcher import ChatlogWatcher
from agent_relay.config import (
    AgentRelayConfig,
    HermesAdapterConfig,
    OpenClawAdapterConfig,
)
from agent_relay.delivery import JobResultDeliverer
from agent_relay.job_store_sqlite import SqliteJobStore
from shared.persistence.chat_store import CHAT_STORE_SCHEMA_SQL
from tests.integration.agent_relay.mock_hermes_server import MockHermesServer
from tests.integration.agent_relay.mock_openclaw_server import MockOpenClawServer

JOB_CONTRACT = json.dumps({
    "action": "job_request",
    "summary": "在查东京机票",
    "spec": {"task": "查询东京机票并给出建议"},
}, ensure_ascii=False)


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, msg) -> bool:
        self.published.append({
            "message_id": msg.message_id,
            "text": msg.text,
            "agent_name": msg.agent_name,
            "agent_id": msg.agent_id,
            "job_key": msg.job_key,
        })
        return True


async def _wait_until(pred, *, timeout: float = 6.0, interval: float = 0.05):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await pred():
            return True
        await asyncio.sleep(interval)
    return False


def _store(tmp_path) -> SqliteJobStore:
    db = str(tmp_path / "chat_server.db")
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(CHAT_STORE_SCHEMA_SQL)
    return SqliteJobStore(db)


@pytest.fixture
async def mock_hermes2():
    server = MockHermesServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def mock_openclaw2():
    server = MockOpenClawServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def dual(mock_hermes2, mock_openclaw2, tmp_path):
    """两个 adapter + 一个 JobStore + 一个 deliverer（生产双开装配同构）。"""
    store = _store(tmp_path)
    publisher = FakePublisher()
    deliverer = JobResultDeliverer(
        store, publisher, AgentRelayConfig(),
        agent_ids={"hermes": "小黑", "openclaw": "小开"},
    )

    hermes = HermesAdapter(HermesAdapterConfig(
        base_url=mock_hermes2.url, api_key="", timeout_s=5, max_retries=0,
        mode="proactive", nudge_timeout_s=5, notify_job_started=False,
    ))
    hermes.job_store = store
    hermes.deliverer = deliverer

    openclaw = OpenClawAdapter(OpenClawAdapterConfig(
        ws_url=mock_openclaw2.url, token="t", agent_id="main", mode="proactive",
        request_timeout_s=5, nudge_timeout_s=5, connect_timeout_s=5,
        silence_timeout_s=3600, watchdog_interval_s=3600,
        reconnect_backoff_initial_s=0.05, reconnect_backoff_max_s=0.2,
        notify_job_started=False,
    ))
    openclaw.job_store = store
    openclaw.deliverer = deliverer

    await hermes.connect()
    await openclaw.connect()
    yield hermes, openclaw, store, publisher
    await hermes.close()
    await openclaw.close()


def _ctx() -> AgentContext:
    return AgentContext(session_id="psess-dual", device_id="dev-dual",
                        language="zh", timezone="Asia/Shanghai")


def _update() -> ChatlogUpdate:
    return ChatlogUpdate(
        path="/tmp/dev-dual/rec-1/chat/history.jsonl",
        chat_session_id="psess-dual",
        new_lines=[ChatlogLine(role="user", text="帮我查机票", ts_ms=1000)],
    )


async def _collect(agen) -> list:
    return [c async for c in agen]


class TestDualJobFlow:
    async def test_both_adapters_jobs_attributed_and_delivered(
        self, dual, mock_hermes2, mock_openclaw2,
    ):
        hermes, openclaw, store, publisher = dual

        # hermes：nudge → job_request → job 会话 → final
        mock_hermes2.submit_handler = (
            lambda p: "hermes 结果"
            if "查询东京机票并给出建议" in str(p.get("text"))
            else JOB_CONTRACT
        )
        await _collect(hermes.nudge(_update(), _ctx()))

        # openclaw：nudge → job_request → job 会话 → final
        mock_openclaw2.run_handler = (
            lambda p: JOB_CONTRACT if "livachat-dev-" in str(p.get("sessionKey"))
            else "openclaw 结果"
        )
        await _collect(openclaw.nudge(_update(), _ctx()))

        async def _two_delivered() -> bool:
            pubs = publisher.published
            return len(pubs) == 2

        assert await _wait_until(_two_delivered), f"published={publisher.published}"

        # 同一 store 内两条 job，各自归属正确
        jobs = []
        for key in await store.device_job_keys("dev-dual"):
            jobs.append(await store.get(key))
        assert {j.agent_name for j in jobs} == {"hermes", "openclaw"}
        assert all(j.status == "delivered" for j in jobs)

        # 投递归属：确定性 message_id + 各自 nickname 互不串台
        by_name = {p["agent_name"]: p for p in publisher.published}
        assert by_name["hermes"]["agent_id"] == "小黑"
        assert by_name["hermes"]["text"] == "hermes 结果"
        assert by_name["openclaw"]["agent_id"] == "小开"
        assert by_name["openclaw"]["text"] == "openclaw 结果"
        hermes_job = next(j for j in jobs if j.agent_name == "hermes")
        openclaw_job = next(j for j in jobs if j.agent_name == "openclaw")
        assert by_name["hermes"]["message_id"] == f"msg_agent_job_{hermes_job.job_key}"
        assert by_name["openclaw"]["message_id"] == f"msg_agent_job_{openclaw_job.job_key}"


class TestSharedWatcherFanout:
    async def test_one_chatlog_update_nudges_both_adapters(
        self, dual, mock_hermes2, mock_openclaw2,
    ):
        hermes, openclaw, store, _ = dual

        async def _recording_lookup(**kwargs):
            return {"recording_session_id": "rec-1", "base_path": "/tmp"}

        watcher = ChatlogWatcher(
            session_id="psess-dual",
            device_id="dev-dual",
            adapters=[hermes, openclaw],
            context=_ctx(),
            recording_lookup=_recording_lookup,
            job_store=store,
            base_dir="/tmp",
            poll_interval_s=0.1, nudge_coalesce_s=0.05, nudge_min_interval_s=0.0,
        )
        # 直接驱动 flush（跳过文件 tail）——同一载荷分发给两个 adapter
        mock_hermes2.submit_handler = lambda p: '{"action": "noop"}'
        mock_openclaw2.run_handler = lambda p: '{"action": "noop"}'
        watcher._path = "/tmp/dev-dual/rec-1/chat/history.jsonl"
        watcher._pending = list(_update().new_lines)
        await watcher._flush()

        hermes_submits = [r["params"] for r in mock_hermes2.requests
                          if r["method"] == "prompt.submit"]
        assert len(hermes_submits) == 1
        openclaw_sends = [p for p in mock_openclaw2.requests_for("chat.send")
                          if "livachat-dev-" in str(p.get("sessionKey"))]
        assert len(openclaw_sends) == 1
        # 两个 adapter 的 nudge 都只读到同一份数据
        assert "帮我查机票" in hermes_submits[0]["text"]
        assert "帮我查机票" in openclaw_sends[0]["message"]


class TestDualQuota:
    async def test_per_device_cap_counts_both_adapters(
        self, dual, mock_openclaw2,
    ):
        """设备级配额共享（design §5.3 注记）：hermes 的在途 job 会占掉
        openclaw 的 per-device 名额——总量语义，预期行为。"""
        hermes, openclaw, store, _ = dual
        openclaw._cfg.max_concurrent_jobs_per_device = 1
        await store.create(session_id="s-old", device_id="dev-dual",
                           summary="在途", agent_name="hermes")
        mock_openclaw2.run_handler = lambda p: JOB_CONTRACT
        await _collect(openclaw.nudge(_update(), _ctx()))
        job_creates = [p for p in mock_openclaw2.requests_for("sessions.create")
                       if "livachat-job-" in str(p.get("key"))]
        assert job_creates == []          # 配额被 hermes job 占满 → 拒绝派发


class TestWatchdogIsolation:
    async def test_scan_stale_only_converges_own_agent(self, dual):
        """hermes watchdog（scan_stale agent_name=hermes）不误伤 openclaw job。"""
        hermes, openclaw, store, _ = dual
        h_job = await store.create(session_id="s1", device_id="dev-x",
                                   summary="h", agent_name="hermes")
        o_job = await store.create(session_id="s1", device_id="dev-x",
                                   summary="o", agent_name="openclaw")
        stale = await store.scan_stale(
            time.time() + 10000,     # epoch（JobRecord.updated_at 语义）
            running_timeout_s=10, requested_timeout_s=10,
            agent_name="hermes",
        )
        assert [j.job_key for j in stale] == [h_job.job_key]
        assert (await store.get(o_job.job_key)).status == "requested"
