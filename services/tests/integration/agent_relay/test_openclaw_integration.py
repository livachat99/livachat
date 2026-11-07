"""Integration tests: OpenClawAdapter against the mock gateway.

Covers the B-plan path (docs/openclaw_adaptor_design.md): handshake/scopes,
nudge contract handling, job dispatch via relay-created sessions, runId
correlation, completion/failure routing, idempotent resend, reconcile
(describe/history), reconnect resubscribe, caps, ask, approvals, seq gap.

Uses SqliteJobStore on a tmp DB (no Redis — mirrors production wiring).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from agent_relay.adapters.base import AgentContext, ChatlogLine, ChatlogUpdate
from agent_relay.adapters.openclaw import OpenClawAdapter
from agent_relay.config import AgentRelayConfig, OpenClawAdapterConfig
from agent_relay.delivery import JobResultDeliverer
from agent_relay.errors import AdapterConnectionError
from agent_relay.job_store import STATUS_DONE, STATUS_RUNNING
from agent_relay.job_store_sqlite import SqliteJobStore
from shared.persistence.chat_store import CHAT_STORE_SCHEMA_SQL
from tests.integration.agent_relay.mock_openclaw_server import MockOpenClawServer


class FakePublisher:
    def __init__(self, *, online: bool = True) -> None:
        self.published: list[dict] = []
        self._online = online

    async def publish(self, msg) -> bool:
        if not self._online:
            return False
        self.published.append({
            "device_id": msg.device_id,
            "message_id": msg.message_id,
            "text": msg.text,
            "agent_name": msg.agent_name,
            "agent_id": msg.agent_id,
            "proactive": msg.proactive,
            "job_key": msg.job_key,
        })
        return True


@pytest.fixture
async def mock_openclaw():
    server = MockOpenClawServer()
    await server.start()
    yield server
    await server.stop()


def _store(tmp_path) -> SqliteJobStore:
    db = str(tmp_path / "chat_server.db")
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(CHAT_STORE_SCHEMA_SQL)
    return SqliteJobStore(db)


@pytest.fixture
def openclaw_cfg(mock_openclaw) -> OpenClawAdapterConfig:
    return OpenClawAdapterConfig(
        ws_url=mock_openclaw.url,
        token="test-token",
        agent_id="main",
        mode="proactive",
        request_timeout_s=5,
        nudge_timeout_s=5,
        connect_timeout_s=5,
        silence_timeout_s=3600,          # 测试内不触发静默判死
        reconnect_backoff_initial_s=0.05,
        reconnect_backoff_max_s=0.2,
        watchdog_interval_s=3600,        # 测试内不跑 watchdog（除专门用例）
        notify_job_started=True,
    )


@pytest.fixture
async def publisher():
    return FakePublisher()


@pytest.fixture
async def adapter(mock_openclaw, openclaw_cfg, tmp_path, publisher):
    a = OpenClawAdapter(openclaw_cfg)
    a.job_store = _store(tmp_path)
    deliverer = JobResultDeliverer(
        a.job_store, publisher, AgentRelayConfig(),
        agent_ids={"openclaw": "小开"},
    )
    a.deliverer = deliverer
    await a.connect()
    yield a
    await a.close()


def _ctx(device: str = "dev-1", session: str = "psess-1") -> AgentContext:
    return AgentContext(session_id=session, device_id=device, language="zh",
                        timezone="Asia/Shanghai")


def _update(session: str = "psess-1") -> ChatlogUpdate:
    return ChatlogUpdate(
        path="/tmp/dev-1/rec-1/chat/history.jsonl",
        chat_session_id=session,
        new_lines=[ChatlogLine(role="user", text="帮我查机票", ts_ms=1000)],
    )


async def _collect(agen) -> list:
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


async def _wait_until(pred, *, timeout: float = 5.0, interval: float = 0.05):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await pred() if asyncio.iscoroutinefunction(pred) else pred():
            return True
        await asyncio.sleep(interval)
    return False


def _contract_handler(mapping: dict[str, str]):
    """chat.send handler：按 sessionKey 尾巴路由回复文案。"""
    def _handler(params: dict) -> str:
        key = str(params.get("sessionKey", ""))
        for suffix, reply in mapping.items():
            if suffix in key:
                return reply
        return '{"action": "noop"}'
    return _handler


JOB_CONTRACT = json.dumps({
    "action": "job_request",
    "summary": "在查东京机票",
    "spec": {"task": "查询东京机票并给出建议"},
}, ensure_ascii=False)


class TestHandshake:
    async def test_connect_params_and_scopes(self, adapter, mock_openclaw):
        await adapter._ensure_ready()
        params = mock_openclaw.connect_params
        assert params is not None
        assert params["client"]["id"] == "gateway-client"
        assert params["client"]["mode"] == "backend"
        assert params["role"] == "operator"
        assert params["auth"]["token"] == "test-token"
        assert params["minProtocol"] == params["maxProtocol"] == 4
        assert params["scopes"] == [
            "operator.read", "operator.write",
            "operator.approvals", "operator.questions",
        ]


class TestNudge:
    async def test_nudge_noop(self, adapter, mock_openclaw):
        mock_openclaw.run_handler = _contract_handler({})
        chunks = await _collect(adapter.nudge(_update(), _ctx()))
        assert chunks == []
        methods = mock_openclaw.methods()
        assert "sessions.messages.subscribe" in methods
        assert methods.index("sessions.messages.subscribe") < methods.index("chat.send")
        create = mock_openclaw.requests_for("sessions.create")[0]
        assert create["key"] == "livachat-dev-dev-1"

    async def test_nudge_message_yields_chunk_with_agent_name(self, adapter, mock_openclaw):
        mock_openclaw.run_handler = _contract_handler(
            {"livachat-dev-dev-1": '{"action": "message", "text": "补充信息"}'}
        )
        chunks = await _collect(adapter.nudge(_update(), _ctx()))
        assert [c.text for c in chunks] == ["补充信息"]
        assert chunks[0].metadata == {"agent_name": "openclaw"}

    async def test_nudge_carries_contract_and_data(self, adapter, mock_openclaw):
        mock_openclaw.run_handler = _contract_handler({})
        await _collect(adapter.nudge(_update(), _ctx()))
        sent = mock_openclaw.requests_for("chat.send")[0]
        assert sent["idempotencyKey"].startswith("livachat:nudge:dev-1:")
        message = sent["message"]
        # 两段式：契约块 + 数据 JSON（D7）——数据段以固定前缀起
        assert "只允许三种响应" in message
        marker = '{"type": "chatlog_update"'
        assert marker in message          # 契约块样例不含该串
        data = json.loads(message[message.index(marker):])
        assert data["type"] == "chatlog_update"
        assert data["chat_session_id"] == "psess-1"
        assert data["timezone"] == "Asia/Shanghai"
        assert data["new_lines"][0]["text"] == "帮我查机票"


class TestJobFlow:
    async def test_job_dispatch_complete_and_deliver(
        self, adapter, mock_openclaw, publisher,
    ):
        mock_openclaw.run_handler = _contract_handler({
            "livachat-dev-dev-1": JOB_CONTRACT,
            "livachat-job-": "东京机票建议：建议周二出发。",
        })
        chunks = await _collect(adapter.nudge(_update(), _ctx()))
        # notify_job_started → 即时摘要通知
        assert [c.text for c in chunks] == ["在查东京机票"]

        store = adapter.job_store
        # job 会话创建：谱系（parentSessionKey/spawnDepth）+ 确定性键
        # （mock 同步完成，不能按 active 状态查询——以 create 请求反推 job_key）
        create = [p for p in mock_openclaw.requests_for("sessions.create")
                  if "livachat-job-" in str(p.get("key"))][0]
        job_key = str(create["key"]).removeprefix("livachat-job-")
        job = await store.get(job_key)
        assert job is not None
        assert create["key"] == f"livachat-job-{job.job_key}"
        assert create["parentSessionKey"] == "agent:main:livachat-dev-dev-1"
        assert create["spawnDepth"] == 1
        assert "idempotencyKey" not in create      # P0-Q1：后端路径不可传
        # chat.send：runId == 幂等键
        job_send = [p for p in mock_openclaw.requests_for("chat.send")
                    if "livachat-job-" in str(p.get("sessionKey"))][0]
        assert job_send["idempotencyKey"] == f"livachat:job:{job.job_key}"

        async def _done() -> bool:
            rec = await store.get(job.job_key)
            # 投递可能在轮询间隔内已经推进到 delivered
            return rec is not None and rec.status in (STATUS_DONE, "delivered")

        assert await _wait_until(_done), "job not completed"

        async def _delivered() -> bool:
            rec = await store.get(job.job_key)
            return rec is not None and rec.status == "delivered"

        assert await _wait_until(_delivered), "job not delivered"
        rec = await store.get(job.job_key)
        assert rec.result_text == "东京机票建议：建议周二出发。"
        assert rec.agent_name == "openclaw"
        assert rec.hermes_task_id == f"livachat:job:{job.job_key}"
        # 投递：确定性 message_id + openclaw 归属 + nickname
        assert len(publisher.published) == 1
        pub = publisher.published[0]
        assert pub["message_id"] == f"msg_agent_job_{job.job_key}"
        assert pub["agent_name"] == "openclaw"
        assert pub["agent_id"] == "小开"
        assert pub["job_key"] == job.job_key
        # 完成后释放订阅
        assert "sessions.messages.unsubscribe" in mock_openclaw.methods()

    async def test_job_noop_output_consumed(self, adapter, mock_openclaw, publisher):
        mock_openclaw.run_handler = _contract_handler({
            "livachat-dev-dev-1": JOB_CONTRACT,
            "livachat-job-": "NOOP",
        })
        await _collect(adapter.nudge(_update(), _ctx()))
        store = adapter.job_store
        create = [p for p in mock_openclaw.requests_for("sessions.create")
                  if "livachat-job-" in str(p.get("key"))][0]
        job_key = str(create["key"]).removeprefix("livachat-job-")

        async def _delivered() -> bool:
            rec = await store.get(job_key)
            return rec is not None and rec.status == "delivered"

        assert await _wait_until(_delivered)
        rec = await store.get(job_key)
        assert rec.result_text == ""
        assert publisher.published == []

    async def test_job_run_error_fails_without_push(
        self, adapter, mock_openclaw, publisher,
    ):
        # 只延迟 job 会话的 run（listening turn 正常完成以派发 job）
        mock_openclaw.defer_when = lambda p: "livachat-job-" in str(p.get("sessionKey"))
        mock_openclaw.run_handler = _contract_handler({
            "livachat-dev-dev-1": JOB_CONTRACT,
        })
        await _collect(adapter.nudge(_update(), _ctx()))
        store = adapter.job_store
        jobs = await store.list_active_for_device("dev-1")
        job = jobs[0]
        await mock_openclaw.fail_run_async(f"livachat:job:{job.job_key}")

        async def _failed() -> bool:
            rec = await store.get(job.job_key)
            return rec is not None and rec.status == "failed"

        assert await _wait_until(_failed)
        rec = await store.get(job.job_key)
        assert rec.fail_reason == "run_error"
        assert publisher.published == []

    async def test_nudge_timeout_aborts_run(self, adapter, mock_openclaw):
        adapter._cfg.nudge_timeout_s = 0.3
        mock_openclaw.defer_completion = True
        mock_openclaw.run_handler = _contract_handler({})
        chunks = await _collect(adapter.nudge(_update(), _ctx()))
        assert chunks == []
        aborts = mock_openclaw.requests_for("sessions.abort")
        assert aborts and aborts[0]["runId"].startswith("livachat:nudge:dev-1:")

    async def test_per_device_cap_rejects_new_job(self, adapter, mock_openclaw):
        adapter._cfg.max_concurrent_jobs_per_device = 1
        store = adapter.job_store
        # 预置一个在途 job（另一 chat session 派出，device 维度共享配额）
        await store.create(session_id="s-old", device_id="dev-1",
                           summary="旧任务", agent_name="openclaw")
        mock_openclaw.run_handler = _contract_handler({"livachat-dev-dev-1": JOB_CONTRACT})
        await _collect(adapter.nudge(_update(), _ctx()))
        job_creates = [p for p in mock_openclaw.requests_for("sessions.create")
                       if "livachat-job-" in str(p.get("key"))]
        assert job_creates == []


class TestReconnectAndReconcile:
    async def test_reconnect_resubscribes_and_delivers(
        self, adapter, mock_openclaw, publisher,
    ):
        mock_openclaw.defer_when = lambda p: "livachat-job-" in str(p.get("sessionKey"))
        mock_openclaw.run_handler = _contract_handler({
            "livachat-dev-dev-1": JOB_CONTRACT,
        })
        await _collect(adapter.nudge(_update(), _ctx()))
        store = adapter.job_store
        job = (await store.list_active_for_device("dev-1"))[0]
        run_id = f"livachat:job:{job.job_key}"
        job_session = f"livachat-job-{job.job_key}"

        # 断开 → 重连 → 重订阅（订阅随连接失效，§4.8）
        await mock_openclaw.drop_connections()

        async def _reconnected() -> bool:
            subs = [p for p in mock_openclaw.requests_for("sessions.messages.subscribe")
                    if job_session in str(p.get("key"))]
            return mock_openclaw.connection_count() >= 2 and len(subs) >= 2

        assert await _wait_until(_reconnected, timeout=8.0)
        # 重连后终态仍能路由到 job（job_runs 跨重连保留）
        await mock_openclaw.complete_run_async(run_id, "重连后拿到的结果")

        async def _done() -> bool:
            rec = await store.get(job.job_key)
            # 投递可能在轮询间隔内已经推进到 delivered
            return rec is not None and rec.status in (STATUS_DONE, "delivered")

        assert await _wait_until(_done)
        assert publisher.published[-1]["text"] == "重连后拿到的结果"

    async def test_reconcile_running_recovers_from_transcript(
        self, adapter, mock_openclaw, publisher,
    ):
        store = adapter.job_store
        job = await store.create(session_id="s1", device_id="dev-1",
                                 summary="恢复任务", agent_name="openclaw")
        run_id = f"livachat:job:{job.job_key}"
        await store.mark_running(job.job_key, run_id)
        # 伪造已完成会话（relay 停机期间跑完）
        key = f"livachat-job-{job.job_key}"
        mock_openclaw.sessions[key] = {
            "sessionId": "sid-x", "active_runs": set(), "lastRunError": "",
            "lastRunId": run_id,
            "messages": [
                {"role": "user", "content": "任务"},
                {"role": "assistant",
                 "content": [{"type": "thinking", "thinking": "hmm"},
                             {"type": "text", "text": "停机前完成的结果"}],
                 "timestamp": 1},
            ],
        }
        await adapter._reconcile_jobs()

        async def _done() -> bool:
            rec = await store.get(job.job_key)
            # 投递可能在轮询间隔内已经推进到 delivered
            return rec is not None and rec.status in (STATUS_DONE, "delivered")

        assert await _wait_until(_done)
        rec = await store.get(job.job_key)
        assert rec.result_text == "停机前完成的结果"
        assert publisher.published[-1]["text"] == "停机前完成的结果"
        # 对账路径统一带 agentId（设计 §4.2）
        describes = mock_openclaw.requests_for("sessions.describe")
        assert describes and describes[-1]["agentId"] == "main"
        histories = mock_openclaw.requests_for("chat.history")
        assert histories and histories[-1]["agentId"] == "main"
        # 规范键唯一化：订阅集/清理用服务端返回的全键，而非裸键
        unsubs = mock_openclaw.requests_for("sessions.messages.unsubscribe")
        assert unsubs[-1]["key"] == f"agent:main:livachat-job-{job.job_key}"

    async def test_reconcile_requested_resends_idempotently(self, adapter, mock_openclaw):
        store = adapter.job_store
        job = await store.create(session_id="s1", device_id="dev-1",
                                 summary="请求窗口任务", agent_name="openclaw")
        mock_openclaw.resend_terminal_status = "in_flight"
        await adapter._reconcile_jobs()

        async def _running() -> bool:
            rec = await store.get(job.job_key)
            return rec is not None and rec.status == STATUS_RUNNING

        assert await _wait_until(_running)
        rec = await store.get(job.job_key)
        assert rec.hermes_task_id == f"livachat:job:{job.job_key}"
        sends = mock_openclaw.requests_for("chat.send")
        assert sends[-1]["idempotencyKey"] == rec.hermes_task_id
        # 设计 §4.2 纪律：对账路径统一带 agentId，并以服务端规范全键发送
        assert sends[-1]["agentId"] == "main"
        assert sends[-1]["sessionKey"] == f"agent:main:livachat-job-{job.job_key}"

    async def test_reconcile_requested_terminal_resend_recovers(
        self, adapter, mock_openclaw, publisher,
    ):
        """重发命中终态缓存（status:"ok"）：走 describe/history 取回结果并投递。"""
        store = adapter.job_store
        job = await store.create(session_id="s1", device_id="dev-1",
                                 summary="窗口任务", agent_name="openclaw")
        run_id = f"livachat:job:{job.job_key}"
        key = f"livachat-job-{job.job_key}"
        # mock 侧：run 已终态（重发将返回缓存终态），会话行与 transcript 就绪
        mock_openclaw.runs[run_id] = {
            "sessionKey": key, "status": "done", "text": "窗口后取回的成品", "task": "",
        }
        mock_openclaw.sessions[key] = {
            "sessionId": "sid-z", "active_runs": set(), "lastRunError": "",
            "lastRunId": run_id,
            "messages": [
                {"role": "user", "content": "任务"},
                {"role": "assistant",
                 "content": [{"type": "text", "text": "窗口后取回的成品"}],
                 "timestamp": 1},
            ],
        }
        mock_openclaw.resend_terminal_status = "ok"
        await adapter._reconcile_jobs()

        async def _delivered() -> bool:
            rec = await store.get(job.job_key)
            return rec is not None and rec.status == "delivered"

        assert await _wait_until(_delivered)
        rec = await store.get(job.job_key)
        assert rec.result_text == "窗口后取回的成品"
        assert publisher.published[-1]["text"] == "窗口后取回的成品"

    async def test_reconcile_mismatch_fails_interrupted(self, adapter, mock_openclaw):
        adapter._cfg.background_timeout_s = 10**9
        store = adapter.job_store
        job = await store.create(session_id="s1", device_id="dev-1",
                                 summary="失联任务", agent_name="openclaw")
        await store.mark_running(job.job_key, "livachat:job:nonexistent")
        mock_openclaw.sessions[f"livachat-job-{job.job_key}"] = {
            "sessionId": "sid-y", "active_runs": set(), "lastRunError": "",
            "lastRunId": "some-other-run", "messages": [],
        }
        await adapter._reconcile_jobs()

        async def _failed() -> bool:
            rec = await store.get(job.job_key)
            return rec is not None and rec.status == "failed"

        assert await _wait_until(_failed, timeout=8.0)
        rec = await store.get(job.job_key)
        assert rec.fail_reason == "interrupted"
        # 对账失败也释放订阅（cleanup 覆盖失败路径）
        unsubs = [p for p in mock_openclaw.requests_for("sessions.messages.unsubscribe")
                  if f"livachat-job-{job.job_key}" in str(p.get("key"))]
        assert unsubs


class TestAsk:
    async def test_ask_returns_final_text(self, adapter, mock_openclaw):
        adapter.reactive_enabled = True
        mock_openclaw.run_handler = _contract_handler({"livachat-req-dev-1": "直接回答"})
        chunks = await _collect(adapter.ask("问题", _ctx()))
        assert [c.text for c in chunks] == ["直接回答"]
        create = [p for p in mock_openclaw.requests_for("sessions.create")
                  if "livachat-req-" in str(p.get("key"))]
        assert create and create[0]["key"] == "livachat-req-dev-1"


class TestInteractions:
    async def test_approval_denied_for_relay_session(self, adapter, mock_openclaw):
        await adapter._ensure_ready()
        mock_openclaw.emit_event("exec.approval.requested", {
            "id": "ap-1", "sessionKey": "agent:main:livachat-job-abc",
        })

        async def _resolved() -> bool:
            return bool(mock_openclaw.requests_for("exec.approval.resolve"))

        assert await _wait_until(_resolved)
        resolve = mock_openclaw.requests_for("exec.approval.resolve")[0]
        assert resolve == {"id": "ap-1", "decision": "deny"}

    async def test_approval_skipped_for_foreign_session(self, adapter, mock_openclaw):
        await adapter._ensure_ready()
        mock_openclaw.emit_event("exec.approval.requested", {
            "id": "ap-2", "sessionKey": "agent:main:main",
        })
        await asyncio.sleep(0.3)
        assert mock_openclaw.requests_for("exec.approval.resolve") == []

    async def test_question_cancelled_for_relay_session(self, adapter, mock_openclaw):
        await adapter._ensure_ready()
        mock_openclaw.emit_event("question.requested", {
            "id": "q-1", "sessionKey": "agent:main:livachat-dev-dev-1",
        })

        async def _resolved() -> bool:
            return bool(mock_openclaw.requests_for("question.resolve"))

        assert await _wait_until(_resolved)
        assert mock_openclaw.requests_for("question.resolve")[0] == {
            "id": "q-1", "cancel": True,
        }


class TestSeqGap:
    async def test_seq_gap_forces_reconnect(self, adapter, mock_openclaw):
        await adapter._ensure_ready()
        # 先收一个正常序号事件（建立 seq 基线），再跳号 → 主动重连（协议文档 §3.3）
        mock_openclaw.emit_event("tick", {})
        await asyncio.sleep(0.2)
        mock_openclaw.emit_raw({
            "type": "event", "event": "tick", "payload": {},
            "seq": mock_openclaw._conn_seq + 10,
        })

        async def _reconnected() -> bool:
            return mock_openclaw.connection_count() >= 2

        assert await _wait_until(_reconnected, timeout=8.0)


class TestWaitReadyFastFail:
    async def test_connect_rejected_fails_fast_without_consuming(self, mock_openclaw):
        """连接被拒（如空 token → NOT_PAIRED）后 _wait_ready 立即失败：

        - 首个 waiter 在 ≤ 轮询周期（_READY_POLL_S）内抛错，而非等满
          request_timeout_s（2026-10-14 实测 issue：nudge 白等 30s）；
        - 错误**不被消费**：断连期间重复调用同样快速失败（reader 连接成功
          时才清除，其余 waiter 无需逐个等满超时）。
        """
        mock_openclaw.connect_error = ("NOT_PAIRED", "device identity required")
        cfg = OpenClawAdapterConfig(
            ws_url=mock_openclaw.url, token="", agent_id="main",
            request_timeout_s=5, connect_timeout_s=5, silence_timeout_s=3600,
            reconnect_backoff_initial_s=0.05, reconnect_backoff_max_s=0.2,
            watchdog_interval_s=3600,
        )
        a = OpenClawAdapter(cfg)
        try:
            loop = asyncio.get_event_loop()
            t0 = loop.time()
            with pytest.raises(AdapterConnectionError) as ei:
                await a._ensure_ready()
            first = loop.time() - t0
            assert "NOT_PAIRED" in str(ei.value)
            assert first < 2.5, f"first wait took {first:.2f}s（应远小于 5s 超时）"

            t0 = loop.time()
            with pytest.raises(AdapterConnectionError):
                await a._ensure_ready()
            second = loop.time() - t0
            assert second < 1.0, f"repeat wait took {second:.2f}s（错误应未被消费）"
        finally:
            await a.close()


class TestMalformedFrames:
    async def test_malformed_frame_skipped_connection_survives(self, adapter, mock_openclaw):
        """单个畸形帧只跳过，不拆承载全部 device 的连接（P2-1）。

        旧行为：json.loads 抛错 → generic 分支 sleep(1) 后经循环顶重连
        （connection_count 变 2）。"""
        await adapter._ensure_ready()
        mock_openclaw.emit_text("this is { not json")
        await asyncio.sleep(1.3)          # 旧行为 ~1s 后已重连
        assert mock_openclaw.connection_count() == 1
        # 连接仍健康：随后 nudge 全链路正常
        mock_openclaw.run_handler = _contract_handler({})
        assert await _collect(adapter.nudge(_update(), _ctx())) == []

    async def test_non_object_frame_skipped(self, adapter, mock_openclaw):
        """合法 JSON 但非对象（列表/字符串）的帧同样跳过、不断连。"""
        await adapter._ensure_ready()
        mock_openclaw.emit_text('["not", "an", "object"]')
        mock_openclaw.emit_text('"just a string"')
        await asyncio.sleep(0.5)
        assert mock_openclaw.connection_count() == 1

    async def test_reader_error_recycles_and_fails_inflight_rpc(
        self, adapter, mock_openclaw, monkeypatch,
    ):
        """非预期 reader 异常：与 _ReconnectRequested 对齐——拆连接重连，
        且**在飞 RPC 立即失败弹出**（旧行为：pending 未弹、等各自超时）。"""
        conn = await adapter._ensure_ready()
        mock_openclaw.swallow_methods = {"sessions.describe"}   # 造一个悬挂 RPC
        task = asyncio.create_task(
            adapter._rpc(conn, "sessions.describe", {"key": "x"}, timeout=10)
        )
        await asyncio.sleep(0.1)
        assert not task.done()

        orig = adapter._handle_message
        fired = {"done": False}

        def _patched(c, m):
            if not fired["done"]:
                fired["done"] = True
                raise RuntimeError("boom")
            return orig(c, m)

        monkeypatch.setattr(adapter, "_handle_message", _patched)
        mock_openclaw.emit_event("tick", {})

        with pytest.raises(AdapterConnectionError):
            await asyncio.wait_for(task, timeout=2.0)   # 立刻弹出而非等 10s 超时

        async def _reconnected() -> bool:
            return mock_openclaw.connection_count() >= 2

        assert await _wait_until(_reconnected, timeout=5.0)


class TestReconcileDedupe:
    async def test_concurrent_reconcile_requests_merge(self, adapter, monkeypatch):
        """并发对账请求合并为补跑一轮，而非并发跑同一批 job（P2-2）。"""
        # 先排空 fixture connect() 启动的对账任务（否则它占用 inflight，
        # 本测试的 t1 会直接走"合并"分支，实验无从开展）
        if adapter._reconcile_task is not None:
            await adapter._reconcile_task
        log: list[str] = []
        orig_pass = adapter._reconcile_pass

        async def _slow_pass():
            log.append("start")
            await asyncio.sleep(0.2)
            await orig_pass()
            log.append("end")

        monkeypatch.setattr(adapter, "_reconcile_pass", _slow_pass)
        t1 = asyncio.create_task(adapter._reconcile_jobs())
        await asyncio.sleep(0.05)
        await adapter._reconcile_jobs()      # 与在跑的重叠 → 合并（立即返回）
        await t1
        assert log == ["start", "end", "start", "end"]


class TestWatchdog:
    async def test_timeout_aborts_run_and_fails(self, mock_openclaw, tmp_path, publisher):
        """看门狗超时（D6）：先 abort run，再收敛 failed(timeout)。"""
        cfg = OpenClawAdapterConfig(
            ws_url=mock_openclaw.url, token="t", agent_id="main",
            request_timeout_s=5, connect_timeout_s=5, silence_timeout_s=3600,
            reconnect_backoff_initial_s=0.05, reconnect_backoff_max_s=0.2,
            watchdog_interval_s=1,           # int 字段（hermes 同名键语义）
            background_timeout_s=0,          # age > 0 即超时（配合下面的短暂等待）
            notify_job_started=False,
        )
        a = OpenClawAdapter(cfg)
        a.job_store = _store(tmp_path)
        deliverer = JobResultDeliverer(
            a.job_store, publisher, AgentRelayConfig(), agent_ids={"openclaw": ""})
        a.deliverer = deliverer
        job = await a.job_store.create(
            session_id="s1", device_id="dev-1", summary="超时任务", agent_name="openclaw")
        run_id = f"livachat:job:{job.job_key}"
        await a.job_store.mark_running(job.job_key, run_id)
        await a.connect()
        try:
            async def _failed() -> bool:
                rec = await a.job_store.get(job.job_key)
                return rec is not None and rec.status == "failed"

            assert await _wait_until(_failed), "job not converged by watchdog"
            rec = await a.job_store.get(job.job_key)
            assert rec.fail_reason == "timeout"
            # 超时前尽力 abort（upstream 取消是 B 方案的净增益）
            aborts = mock_openclaw.requests_for("sessions.abort")
            assert any(p.get("runId") == run_id for p in aborts)
        finally:
            await a.close()
