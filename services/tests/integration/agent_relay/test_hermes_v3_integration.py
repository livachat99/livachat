"""Integration tests for HermesAdapter v3 job-session carrier (SQLite store)."""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3

import pytest

from agent_relay.adapters.base import AgentContext, ChatlogLine, ChatlogUpdate
from agent_relay.adapters.hermes import HermesAdapter, ResumeOutcome, _JobSession
from agent_relay.config import HermesAdapterConfig
from agent_relay.job_store import (
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_REQUESTED,
    STATUS_RUNNING,
)
from agent_relay.job_store_sqlite import SqliteJobStore
from shared.persistence.chat_store import CHAT_STORE_SCHEMA_SQL


class _FakeHermesStore:
    def __init__(self):
        self._data: dict[str, dict] = {}

    async def load_hermes_session(self, device_uuid: str):
        return self._data.get(device_uuid.lower())

    async def save_hermes_session(self, *, device_uuid: str,
                                  hermes_session_id: str, stored_session_id: str):
        self._data[device_uuid.lower()] = {
            "hermes_session_id": hermes_session_id,
            "stored_session_id": stored_session_id,
        }

    async def delete_hermes_session(self, device_uuid: str):
        self._data.pop(device_uuid.lower(), None)


class _FakeDeliverer:
    def __init__(self, store: SqliteJobStore):
        self._store = store
        self.jobs = []

    async def deliver(self, job, **kwargs):
        current = await self._store.get(job.job_key)
        if current is None or current.status != "done":
            return False
        await self._store.mark_delivered(current.job_key)
        delivered = await self._store.get(current.job_key)
        self.jobs.append(delivered or current)
        return True


def _make_store(tmp_path) -> SqliteJobStore:
    db = str(tmp_path / "chat_server.db")
    with sqlite3.connect(db) as conn:
        conn.executescript(CHAT_STORE_SCHEMA_SQL)
    return SqliteJobStore(db)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(session_id="psess-v3", device_id="pdev-v3", language="zh")


@pytest.fixture
def job_request_reply() -> str:
    return json.dumps({
        "action": "job_request",
        "summary": "查机票",
        "spec": {"task": "JOB_TASK"},
    })


@pytest.fixture
async def adapter(mock_hermes, tmp_path, job_request_reply):
    def submit_handler(params):
        text = str(params.get("text") or "")
        if "JOB_TASK" in text:
            return {"text": "机票 2400 元", "status": "complete"}
        return job_request_reply

    mock_hermes.submit_handler = submit_handler
    cfg = HermesAdapterConfig(
        base_url=mock_hermes.url,
        api_key="",
        timeout_s=5,
        max_retries=1,
        mode="proactive",
        nudge_timeout_s=5,
        nudge_min_interval_s=0.0,
        notify_job_started=False,
        job_cancel_grace_s=0.3,
        job_resume_recheck_s=0.2,
        session_resume_retries=2,
        session_resume_backoff_s=0.01,
    )
    store = _make_store(tmp_path)
    a = HermesAdapter(cfg)
    a.job_store = store
    a.hermes_store = _FakeHermesStore()
    a.base_dir = "/tmp"
    a.deliverer = _FakeDeliverer(store)
    await a.connect()
    yield a
    await a.close()


def _update() -> ChatlogUpdate:
    return ChatlogUpdate(
        path="/tmp/pdev-v3/rec/chat/history.jsonl",
        chat_session_id="psess-v3",
        new_lines=[ChatlogLine(role="user", text="帮我查机票", ts_ms=1000)],
    )


async def _dispatch(adapter: HermesAdapter, ctx: AgentContext) -> None:
    async for _ in adapter.nudge(_update(), ctx):
        pass


async def _wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return True
        await asyncio.sleep(0.02)
    return False


async def _active_job(adapter: HermesAdapter, device_id: str):
    jobs = await adapter.job_store.list_active_for_device(device_id)
    return jobs[0] if jobs else None


class TestJobSessionCarrier:
    async def test_dispatch_complete_delivers(self, adapter, mock_hermes, ctx):
        await adapter.on_session_start(ctx)
        await _dispatch(adapter, ctx)

        async def delivered():
            return bool(adapter.deliverer.jobs)

        assert await _wait_for(delivered)
        job = adapter.deliverer.jobs[0]
        assert job.result_text == "机票 2400 元"
        assert job.status == STATUS_DELIVERED

        # A dedicated job session was created, then closed after terminal.
        assert len(mock_hermes.sessions) >= 2  # device + job
        job_sessions = [
            sid for sid, info in mock_hermes.sessions.items()
            if info.get("params", {}).get("close_on_disconnect") is False
        ]
        assert job_sessions
        closes = [
            n for n in mock_hermes.notifications
            if n.get("method") == "session.close"
        ]
        assert any(
            c.get("params", {}).get("session_id") in job_sessions for c in closes
        )
        # In-memory maps are cleaned after terminal.
        conn = adapter._devices[ctx.device_id]
        assert not conn.job_sessions
        assert not conn.job_sid_to_key

    async def test_noop_is_consumed_without_push(self, adapter, mock_hermes, ctx):
        def submit_handler(params):
            text = str(params.get("text") or "")
            if "JOB_TASK" in text:
                return {"text": "NOOP", "status": "complete"}
            return json.dumps({
                "action": "job_request",
                "summary": "查机票",
                "spec": {"task": "JOB_TASK"},
            })

        mock_hermes.submit_handler = submit_handler
        await adapter.on_session_start(ctx)
        await _dispatch(adapter, ctx)

        async def done():
            job = await _active_job(adapter, ctx.device_id)
            return job is None

        assert await _wait_for(done)
        assert adapter.deliverer.jobs == []
        jobs = await adapter.job_store.device_job_keys(ctx.device_id)
        record = await adapter.job_store.get(jobs[-1])
        assert record.status == STATUS_DELIVERED
        assert record.result_text == ""

    async def test_cancel_interrupts_and_kills_processes(self, adapter, mock_hermes, ctx):
        def submit_handler(params):
            text = str(params.get("text") or "")
            if "JOB_TASK" in text:
                return {"text": "", "status": "complete", "hold": True}
            return json.dumps({
                "action": "job_request",
                "summary": "查机票",
                "spec": {"task": "JOB_TASK"},
            })

        mock_hermes.submit_handler = submit_handler
        await adapter.on_session_start(ctx)
        await _dispatch(adapter, ctx)
        job = await _active_job(adapter, ctx.device_id)
        assert job is not None and job.status == STATUS_RUNNING
        js = adapter._devices[ctx.device_id].job_sessions[job.job_key]
        mock_hermes.processes[js.hermes_session_id] = [
            {"session_id": "proc_1", "command": "sleep 120", "status": "running"}
        ]

        assert await adapter.cancel_job(job.job_key)
        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_FAILED
        assert current.fail_reason == "interrupted"
        assert adapter.deliverer.jobs == []
        assert mock_hermes.killed_processes == ["proc_1"]
        assert any(
            r.get("session_id") == js.hermes_session_id
            for r in mock_hermes.interrupt_requests
        )

    async def test_error_status_fails_without_delivery(self, adapter, mock_hermes, ctx):
        def submit_handler(params):
            text = str(params.get("text") or "")
            if "JOB_TASK" in text:
                return {"text": "Error: boom", "status": "error"}
            return json.dumps({
                "action": "job_request",
                "summary": "查机票",
                "spec": {"task": "JOB_TASK"},
            })

        mock_hermes.submit_handler = submit_handler
        await adapter.on_session_start(ctx)
        await _dispatch(adapter, ctx)

        async def converged():
            job = await _active_job(adapter, ctx.device_id)
            return job is None

        assert await _wait_for(converged)
        keys = await adapter.job_store.device_job_keys(ctx.device_id)
        record = await adapter.job_store.get(keys[-1])
        assert record.status == STATUS_FAILED
        assert record.fail_reason == "run_error"
        assert adapter.deliverer.jobs == []

    async def test_steer_queued(self, adapter, mock_hermes, ctx):
        def submit_handler(params):
            text = str(params.get("text") or "")
            if "JOB_TASK" in text:
                return {"text": "", "status": "complete", "hold": True}
            return json.dumps({
                "action": "job_request",
                "summary": "查机票",
                "spec": {"task": "JOB_TASK"},
            })

        mock_hermes.submit_handler = submit_handler
        await adapter.on_session_start(ctx)
        await _dispatch(adapter, ctx)
        job = await _active_job(adapter, ctx.device_id)
        assert job is not None
        assert await adapter.steer_job(job.job_key, "改成查高铁")
        assert mock_hermes.steer_requests
        assert "改成查高铁" in mock_hermes.steer_requests[-1]["text"]


class TestJobSessionRecovery:
    async def test_recover_completed_from_messages(self, adapter, mock_hermes, ctx):
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-recover-1"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)

        mock_hermes.resume_handler = lambda params: {
            "session_id": "sess-recovered-1",
            "stored_session_id": params["session_id"],
            "messages": [
                {"role": "user", "text": "task"},
                {"role": "assistant", "text": "JOB_RECOVERED"},
            ],
            "running": False,
            "status": "idle",
        }
        await adapter._recover_job_sessions(conn)

        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_DELIVERED
        assert current.result_text == "JOB_RECOVERED"
        assert adapter.deliverer.jobs[-1].job_key == job.job_key

    async def test_recover_working_status_reread_failure_keeps_session(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        """Store failure while re-reading status must not close a working session."""
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-working-reread"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        stale = await adapter.job_store.get(job.job_key)
        assert stale is not None

        live = "sess-working-reread"
        mock_hermes.sessions[live] = {
            "params": {}, "messages": [], "stored_session_id": "stored-working-reread",
        }
        mock_hermes.session_status[live] = "working"
        mock_hermes.resume_handler = lambda params: {
            "session_id": live,
            "stored_session_id": params["session_id"],
            "messages": [],
            "running": True,
            "status": "working",
        }

        async def no_mark(job_key, ref):
            return False

        async def boom(job_key):
            raise RuntimeError("store down")

        monkeypatch.setattr(adapter.job_store, "mark_running", no_mark)
        monkeypatch.setattr(adapter.job_store, "get", boom)
        await adapter._resume_job_session(conn, stale)

        assert job.job_key in conn.job_sessions
        assert not any(
            n.get("method") == "session.close"
            and n.get("params", {}).get("session_id") == live
            for n in mock_hermes.notifications
        )

    async def test_recover_text_status_reread_failure_keeps_session(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-text-reread"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        stale = await adapter.job_store.get(job.job_key)
        assert stale is not None

        live = "sess-text-reread"
        mock_hermes.resume_handler = lambda params: {
            "session_id": live,
            "stored_session_id": params["session_id"],
            "messages": [
                {"role": "user", "text": "task"},
                {"role": "assistant", "text": "JOB_TEXT"},
            ],
            "running": False,
            "status": "idle",
        }

        async def no_mark(job_key, ref):
            return False

        async def boom(job_key):
            raise RuntimeError("store down")

        monkeypatch.setattr(adapter.job_store, "mark_running", no_mark)
        monkeypatch.setattr(adapter.job_store, "get", boom)
        await adapter._resume_job_session(conn, stale)

        assert job.job_key in conn.job_sessions
        assert adapter.deliverer.jobs == []
        assert not any(
            n.get("method") == "session.close"
            and n.get("params", {}).get("session_id") == live
            for n in mock_hermes.notifications
        )

    async def test_recover_text_mark_running_miss_skips_delivery(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        """mark_running CAS miss + re-get non-running → no delivery/metric."""
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-recover-miss"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        live = "sess-recover-miss"
        mock_hermes.resume_handler = lambda params: {
            "session_id": live,
            "stored_session_id": params["session_id"],
            "messages": [
                {"role": "user", "text": "task"},
                {"role": "assistant", "text": "JOB_TEXT_MISS"},
            ],
            "running": False,
            "status": "idle",
        }
        from shared.observability.metrics import get_registry

        counter = get_registry().agent_job_events_total().labels(
            adapter="hermes", event="recovered_from_history"
        )
        before = counter._value.get()

        async def no_mark(job_key, ref):
            return False

        monkeypatch.setattr(adapter.job_store, "mark_running", no_mark)
        await adapter._recover_job_sessions(conn)

        assert adapter.deliverer.jobs == []
        assert (await adapter.job_store.get(job.job_key)).status == STATUS_REQUESTED
        assert counter._value.get() == before  # not counted as recovered
        assert any(
            n.get("method") == "session.close"
            and n.get("params", {}).get("session_id") == live
            for n in mock_hermes.notifications
        )

    async def test_recover_text_mark_running_error_but_running_proceeds(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        """mark_running raises but the row is actually running → continue."""
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-recover-running"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        # Keep the requested snapshot (with ref) while the store is running.
        stale_job = await adapter.job_store.get(job.job_key)
        assert stale_job is not None
        assert await adapter.job_store.mark_running(job.job_key, ref)
        live = "sess-recover-running"
        mock_hermes.resume_handler = lambda params: {
            "session_id": live,
            "stored_session_id": params["session_id"],
            "messages": [
                {"role": "user", "text": "task"},
                {"role": "assistant", "text": "JOB_RUNNING"},
            ],
            "running": False,
            "status": "idle",
        }

        async def boom(job_key, ref):
            raise RuntimeError("transient mark_running failure")

        monkeypatch.setattr(adapter.job_store, "mark_running", boom)
        await adapter._resume_job_session(conn, stale_job)

        assert adapter.deliverer.jobs
        assert adapter.deliverer.jobs[-1].result_text == "JOB_RUNNING"
        assert (await adapter.job_store.get(stale_job.job_key)).status == STATUS_DELIVERED

    async def test_recover_messages_omitted_uses_history(self, adapter, mock_hermes, ctx):
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-recover-2"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)

        mock_hermes.resume_handler = lambda params: {
            "session_id": "sess-recovered-2",
            "stored_session_id": params["session_id"],
            "messages": [],
            "messages_omitted": True,
            "running": False,
            "status": "idle",
        }
        mock_hermes.session_history["sess-recovered-2"] = [
            {"role": "user", "text": "task"},
            {"role": "assistant", "text": "JOB_FROM_HISTORY"},
        ]
        await adapter._recover_job_sessions(conn)

        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_DELIVERED
        assert current.result_text == "JOB_FROM_HISTORY"

    async def test_recover_noop_is_consumed(self, adapter, mock_hermes, ctx):
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-recover-3"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)
        mock_hermes.resume_handler = lambda params: {
            "session_id": "sess-recovered-3",
            "stored_session_id": params["session_id"],
            "messages": [
                {"role": "user", "text": "task"},
                {"role": "assistant", "text": "NOOP"},
            ],
            "running": False,
            "status": "idle",
        }
        await adapter._recover_job_sessions(conn)

        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_DELIVERED
        assert current.result_text == ""
        assert adapter.deliverer.jobs == []

    async def test_working_resume_arms_recheck_and_converges(self, adapter, mock_hermes, ctx):
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-recheck"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)

        live = "sess-recheck-1"
        mock_hermes.sessions[live] = {
            "params": {"close_on_disconnect": False},
            "messages": [],
            "stored_session_id": "stored-recheck",
        }
        mock_hermes.session_status[live] = "working"
        mock_hermes.resume_handler = lambda params: {
            "session_id": live,
            "stored_session_id": params["session_id"],
            "messages": [],
            "running": True,
            "status": "working",
        }
        await adapter._recover_job_sessions(conn)
        js = conn.job_sessions[job.job_key]
        assert js.recheck_task is not None
        assert (await adapter.job_store.get(job.job_key)).status == STATUS_RUNNING

        # Turn completes; the original completion event is lost. D17 recheck
        # must pick it up from the transcript.
        mock_hermes.session_status[live] = "idle"
        mock_hermes.session_history[live] = [
            {"role": "user", "text": "task"},
            {"role": "assistant", "text": "JOB_RECHECK"},
        ]
        mock_hermes.resume_handler = lambda params: {
            "session_id": live,
            "stored_session_id": params["session_id"],
            "messages": list(mock_hermes.session_history[live]),
            "running": False,
            "status": "idle",
        }

        assert await _wait_for(lambda: bool(adapter.deliverer.jobs))
        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_DELIVERED
        assert current.result_text == "JOB_RECHECK"

    async def test_device_session_reap_does_not_fail_session_job(
        self, adapter, mock_hermes, ctx
    ):
        await adapter.on_session_start(ctx)
        conn = adapter._devices[ctx.device_id]
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-device-reap"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)

        # The device session resume returns a new sid (DB rebuild). The job
        # has its own session and must not be failed by the device reap.
        outcome = await adapter._resume_conn_session(conn)
        assert outcome is ResumeOutcome.REAPED

        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_RUNNING


class TestDispatchAndRecoveryGuards:
    async def test_ws_not_ready_fails_job_immediately(self, adapter, ctx):
        await adapter.on_session_start(ctx)
        conn = adapter._devices[ctx.device_id]
        await adapter._stop_conn(conn)  # stays in _devices, but no longer ready

        contract = {"summary": "s", "spec": {"task": "t"}}
        chunks = [c async for c in adapter._handle_job_request(
            conn, ctx.session_id, contract
        )]
        assert chunks == []
        keys = await adapter.job_store.session_job_keys(ctx.session_id)
        record = await adapter.job_store.get(keys[-1])
        assert record.status == STATUS_FAILED
        assert record.fail_reason == "create_error"

    async def test_submit_4090_maps_to_session_cap(
        self, adapter, mock_hermes, ctx
    ):
        await adapter.on_session_start(ctx)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        mock_hermes.rpc_error_methods["prompt.submit"] = (4090, "active session limit")
        conn = adapter._devices[ctx.device_id]

        notice = await adapter._dispatch_job_session(
            conn, job, "task", ctx.session_id, "", ""
        )

        assert notice is None
        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_FAILED
        assert current.fail_reason == "session_cap"
        assert not conn.job_sessions

    async def test_missing_stored_id_closes_created_session(
        self, adapter, mock_hermes, ctx
    ):
        await adapter.on_session_start(ctx)
        mock_hermes.create_omit_stored = True
        await _dispatch(adapter, ctx)

        keys = await adapter.job_store.session_job_keys(ctx.session_id)
        record = await adapter.job_store.get(keys[-1])
        assert record.status == STATUS_FAILED
        assert record.fail_reason == "create_error"

        job_sid = next(
            sid for sid, info in mock_hermes.sessions.items()
            if info["params"].get("title") == "查机票"
        )
        assert any(
            n.get("method") == "session.close"
            and n.get("params", {}).get("session_id") == job_sid
            for n in mock_hermes.notifications
        )
        assert not any(
            r["method"] == "prompt.submit"
            and r["params"].get("session_id") == job_sid
            for r in mock_hermes.requests
        )

    async def test_stopped_conn_recovery_does_not_fail_job(
        self, adapter, mock_hermes, ctx
    ):
        await adapter.on_session_start(ctx)
        conn = adapter._devices[ctx.device_id]
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-shutdown-guard"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)

        await adapter._stop_conn(conn)
        await adapter._resume_job_session(conn, job)

        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_RUNNING
        assert not any(
            r["method"] == "session.resume"
            and r["params"].get("session_id") == "stored-shutdown-guard"
            for r in mock_hermes.requests
        )

    async def test_dispatch_across_reconnect_arms_recheck(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        await adapter.on_session_start(ctx)
        conn = adapter._devices[ctx.device_id]
        real_ws = conn.ws
        fake_ws = object()
        original_rpc = adapter._rpc

        async def rpc(c, method, params=None, **kwargs):
            result = await original_rpc(c, method, params, **kwargs)
            if (
                method == "prompt.submit"
                and params
                and params.get("session_id") != c.hermes_session_id
            ):
                c.ws = fake_ws  # simulate a transport swap across dispatch
            return result

        monkeypatch.setattr(adapter, "_rpc", rpc)
        mock_hermes.submit_handler = (
            lambda p: json.dumps({
                "action": "job_request",
                "summary": "查机票",
                "spec": {"task": "JOB_TASK"},
            })
            if "帮我查机票" in str(p.get("text"))
            else {"text": "", "status": "complete", "hold": True}
        )
        try:
            await _dispatch(adapter, ctx)
        finally:
            conn.ws = real_ws

        job = await _active_job(adapter, ctx.device_id)
        assert job is not None
        recheck = conn.job_sessions[job.job_key].recheck_task
        assert recheck is not None
        recheck.cancel()

    async def test_completion_store_error_arms_recheck(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        await adapter.on_session_start(ctx)
        mock_hermes.submit_handler = (
            lambda p: json.dumps({
                "action": "job_request",
                "summary": "查机票",
                "spec": {"task": "JOB_TASK"},
            })
            if "帮我查机票" in str(p.get("text"))
            else {"text": "", "status": "complete", "hold": True}
        )
        await _dispatch(adapter, ctx)
        job = await _active_job(adapter, ctx.device_id)
        conn = adapter._devices[ctx.device_id]
        js = conn.job_sessions[job.job_key]

        original_get = adapter.job_store.get

        async def boom(job_key):
            raise RuntimeError("store down")

        monkeypatch.setattr(adapter.job_store, "get", boom)
        try:
            await adapter._handle_job_message_complete(
                conn, job.job_key, js.hermes_session_id,
                {"status": "complete", "text": "result"},
            )
        finally:
            monkeypatch.setattr(adapter.job_store, "get", original_get)

        recheck = js.recheck_task
        assert recheck is not None
        recheck.cancel()
        assert (await adapter.job_store.get(job.job_key)).status == STATUS_RUNNING

    async def test_recheck_delivers_done_job(self, adapter, mock_hermes, ctx):
        """A committed-but-undelivered job is idempotently re-delivered."""
        await adapter.on_session_start(ctx)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-done-redeliver"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)
        await adapter.job_store.mark_done(job.job_key, "late result")

        conn = adapter._devices[ctx.device_id]
        await adapter._job_resume_recheck(conn, job.job_key)

        assert adapter.deliverer.jobs
        assert adapter.deliverer.jobs[-1].result_text == "late result"
        assert (await adapter.job_store.get(job.job_key)).status == STATUS_DELIVERED

    async def test_recheck_consumes_empty_done_job_without_push(
        self, adapter, mock_hermes, ctx
    ):
        await adapter.on_session_start(ctx)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-done-empty"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)
        await adapter.job_store.mark_done(job.job_key, "")

        conn = adapter._devices[ctx.device_id]
        await adapter._job_resume_recheck(conn, job.job_key)

        assert adapter.deliverer.jobs == []
        assert (await adapter.job_store.get(job.job_key)).status == STATUS_DELIVERED

    async def test_dispatch_cancelled_before_submit_fails_job(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        await adapter.on_session_start(ctx)
        conn = adapter._devices[ctx.device_id]
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        reached = asyncio.Event()
        job_sid = {"sid": ""}
        original_rpc = adapter._rpc

        async def rpc(c, method, params=None, **kwargs):
            if (
                method == "prompt.submit"
                and params
                and params.get("session_id") != c.hermes_session_id
            ):
                job_sid["sid"] = params["session_id"]
                reached.set()
                await asyncio.Event().wait()  # block until the task is cancelled
            return await original_rpc(c, method, params, **kwargs)

        monkeypatch.setattr(adapter, "_rpc", rpc)
        task = asyncio.create_task(adapter._dispatch_job_session(
            conn, job, "task", ctx.session_id, "", ""
        ))
        await asyncio.wait_for(reached.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_FAILED
        assert current.fail_reason == "dispatch_aborted"
        assert await _wait_for(lambda: any(
            n.get("method") == "session.close"
            and n.get("params", {}).get("session_id") == job_sid["sid"]
            for n in mock_hermes.notifications
        ))

    async def test_stale_job_without_mapping_resumes_and_interrupts(
        self, adapter, mock_hermes, ctx
    ):
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        ref = "session:stored-stale"
        await adapter.job_store.bind_external_ref(job.job_key, ref)
        assert await adapter.job_store.mark_running(job.job_key, ref)
        await adapter.job_store.mark_failed(job.job_key, "timeout")
        stale = await adapter.job_store.get(job.job_key)
        assert stale is not None

        live = "sess-stale-live"
        mock_hermes.sessions[live] = {
            "params": {}, "messages": [], "stored_session_id": "stored-stale",
        }
        mock_hermes.session_status[live] = "working"
        mock_hermes.resume_handler = lambda params: {
            "session_id": live,
            "stored_session_id": params["session_id"],
            "messages": [],
            "running": True,
            "status": "working",
        }

        await adapter._interrupt_stale_job(stale)

        assert any(
            r.get("session_id") == live for r in mock_hermes.interrupt_requests
        )
        assert conn.job_sessions.get(job.job_key) is None

    async def test_job_error_store_failure_arms_recheck(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        await adapter.on_session_start(ctx)
        mock_hermes.submit_handler = (
            lambda p: json.dumps({
                "action": "job_request",
                "summary": "查机票",
                "spec": {"task": "JOB_TASK"},
            })
            if "帮我查机票" in str(p.get("text"))
            else {"text": "", "status": "complete", "hold": True}
        )
        await _dispatch(adapter, ctx)
        job = await _active_job(adapter, ctx.device_id)
        conn = adapter._devices[ctx.device_id]
        js = conn.job_sessions[job.job_key]

        original = adapter.job_store.mark_failed

        async def boom(job_key, reason):
            raise RuntimeError("store down")

        monkeypatch.setattr(adapter.job_store, "mark_failed", boom)
        try:
            await adapter._handle_job_error(
                conn, job.job_key, js.hermes_session_id, {"message": "boom"}
            )
        finally:
            monkeypatch.setattr(adapter.job_store, "mark_failed", original)

        recheck = js.recheck_task
        assert recheck is not None
        recheck.cancel()
        assert (await adapter.job_store.get(job.job_key)).status == STATUS_RUNNING

    async def test_completion_requested_reread_failure_keeps_session(
        self, adapter, mock_hermes, ctx, monkeypatch
    ):
        conn = await adapter._ensure_device(ctx.device_id)
        job = await adapter.job_store.create(
            session_id=ctx.session_id, device_id=ctx.device_id, summary="s"
        )
        live = "sess-defensive-reread"
        conn.job_sessions[job.job_key] = _JobSession(
            job.job_key, live, "stored-defensive-reread"
        )
        conn.job_sid_to_key[live] = job.job_key

        original_get = adapter.job_store.get
        calls = {"n": 0}

        async def flaky_get(job_key):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("store down")
            return await original_get(job_key)

        async def no_mark(job_key, ref):
            return False

        monkeypatch.setattr(adapter.job_store, "get", flaky_get)
        monkeypatch.setattr(adapter.job_store, "mark_running", no_mark)
        await adapter._handle_job_message_complete(
            conn, job.job_key, live, {"status": "complete", "text": "late"}
        )

        js = conn.job_sessions.get(job.job_key)
        assert js is not None
        recheck = js.recheck_task
        assert recheck is not None
        recheck.cancel()
        assert not any(
            n.get("method") == "session.close"
            and n.get("params", {}).get("session_id") == live
            for n in mock_hermes.notifications
        )

    async def test_live_clarify_autorespond_includes_request_id(
        self, adapter, mock_hermes, ctx
    ):
        await adapter.on_session_start(ctx)
        conn = adapter._devices[ctx.device_id]
        mock_hermes.emit_event("clarify.request", {
            "session_id": conn.hermes_session_id,
            "request_id": "rid-live",
            "question": "color?",
            "choices": ["red"],
        })
        assert await _wait_for(lambda: any(
            n.get("method") == "clarify.respond"
            and n.get("params", {}).get("request_id") == "rid-live"
            for n in mock_hermes.notifications
        ))

    async def test_recovered_clarify_uses_resume_request_id(
        self, adapter, mock_hermes, ctx
    ):
        conn = await adapter._ensure_device(ctx.device_id)
        await adapter._autorespond_job_waiting(
            conn, "sess-waiting", {"pending_clarify": {"request_id": "rid-resume"}}
        )
        assert await _wait_for(lambda: any(
            n.get("method") == "clarify.respond"
            and n.get("params", {}).get("request_id") == "rid-resume"
            for n in mock_hermes.notifications
        ))

    async def test_cancel_after_connection_loss_interrupts_remote(
        self, adapter, mock_hermes, ctx
    ):
        await adapter.on_session_start(ctx)
        mock_hermes.submit_handler = (
            lambda p: json.dumps({
                "action": "job_request",
                "summary": "查机票",
                "spec": {"task": "JOB_TASK"},
            })
            if "帮我查机票" in str(p.get("text"))
            else {"text": "", "status": "complete", "hold": True}
        )
        await _dispatch(adapter, ctx)
        job = await _active_job(adapter, ctx.device_id)
        conn = adapter._devices.pop(ctx.device_id)
        js = conn.job_sessions[job.job_key]
        live_sid = js.hermes_session_id
        await adapter._stop_conn(conn)

        mock_hermes.resume_handler = lambda params: {
            "session_id": live_sid,
            "stored_session_id": params["session_id"],
            "messages": [],
            "running": True,
            "status": "working",
        }
        assert await adapter.cancel_job(job.job_key)

        current = await adapter.job_store.get(job.job_key)
        assert current.status == STATUS_FAILED
        assert any(
            r.get("session_id") == live_sid
            for r in mock_hermes.interrupt_requests
        )
