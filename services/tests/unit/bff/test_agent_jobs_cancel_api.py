"""BFF POST /api/mylife/agent-jobs/cancel（docs/android_agent_job_status.md §7.2）。

口径：cancel_job 的布尔返回不可信，BFF 以取消后重读的 JobRecord 终态为准；
仍 active → 503 cancel_incomplete；自然完成竞态 → 200 completed。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gateway.auth import wire_digest
from tests.unit.bff.conftest import DEVICE_A

SECRET = "test-secret"
TOKEN = wire_digest(SECRET)


@dataclass
class _Job:
    job_key: str = "job-1"
    device_id: str = DEVICE_A
    status: str = "running"
    agent_name: str = "hermes"
    fail_reason: str = ""


class _FakeStore:
    def __init__(self, job: _Job) -> None:
        self.job = job
        self.get_calls = 0

    async def get(self, job_id: str):
        self.get_calls += 1
        return self.job if job_id == self.job.job_key else None


class _FakeAdapter:
    def __init__(self, store: _FakeStore, *, new_status: str | None = "failed",
                 fail_reason: str = "cancelled", result: bool = True,
                 raises: bool = False,
                 converge_then_raise: bool = False) -> None:
        self.store = store
        self.new_status = new_status
        self.fail_reason = fail_reason
        self.result = result
        self.raises = raises
        self.converge_then_raise = converge_then_raise
        self.calls: list[tuple[str, str]] = []

    async def cancel_job(self, job_id: str, reason: str = "cancelled") -> bool:
        self.calls.append((job_id, reason))
        if self.raises:
            if self.converge_then_raise:
                self.store.job = replace(
                    self.store.job, status="failed", fail_reason=self.fail_reason
                )
            raise RuntimeError("cancel exploded")
        if self.new_status is not None:
            self.store.job = replace(
                self.store.job, status=self.new_status, fail_reason=self.fail_reason
            )
        return self.result


def _setup(make_client, *, job: _Job | None = None, adapter: _FakeAdapter | None = None,
           dev_token: str = "", with_relay: bool = True):
    client = make_client(dev_token=dev_token)
    store = _FakeStore(job or _Job())
    adapter = adapter or _FakeAdapter(store)
    adapter.store = store
    if with_relay:
        client.app.state.job_store = store
        client.app.state.agent_adapters = {"hermes": adapter}
    return client, store, adapter


def _post(client, job_id: str = "job-1", *, device_id: str | None = DEVICE_A):
    params = {"device_id": device_id} if device_id is not None else {}
    return client.post(
        "/api/mylife/agent-jobs/cancel",
        params=params,
        json={"job_id": job_id},
    )


def test_cancel_active_job_success(make_client):
    client, _, adapter = _setup(make_client)
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"schema_version": 1, "job_id": "job-1", "status": "cancelled"}
    assert adapter.calls == [("job-1", "cancelled")]


def test_natural_completion_race_returns_completed(make_client):
    client, store, _ = _setup(make_client)
    store.job = _Job(status="running")
    adapter = _FakeAdapter(store, new_status="done", fail_reason="")
    client.app.state.agent_adapters = {"hermes": adapter}
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"


def test_still_active_after_cancel_returns_503(make_client):
    """Hermes 在 mark_failed 失败时仍 return True，BFF 必须重读终态。"""
    client, _, adapter = _setup(make_client)
    adapter.new_status = None
    resp = _post(client)
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "cancel_incomplete"


def test_adapter_exception_returns_502(make_client):
    client, _, adapter = _setup(make_client)
    adapter.raises = True
    resp = _post(client)
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "upstream_unavailable"


def test_adapter_raises_after_convergence_returns_200(make_client):
    """adapter 在 mark_failed 成功后抛（兜底清理异常）→ 重读终态按 200 返回。"""
    client, _, adapter = _setup(make_client)
    adapter.raises = True
    adapter.converge_then_raise = True
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


def test_terminal_job_conflict_without_adapter_call(make_client):
    client, _, adapter = _setup(make_client, job=_Job(status="failed", fail_reason="timeout"))
    resp = _post(client)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "conflict"
    assert adapter.calls == []


def test_device_mismatch_is_not_found(make_client):
    client, _, adapter = _setup(make_client)
    resp = _post(client, device_id="99999999-9999-4999-8999-999999999999")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"
    assert adapter.calls == []


def test_missing_device_id_bad_request(make_client):
    client, _, _ = _setup(make_client)
    resp = _post(client, device_id=None)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "bad_request"


def test_unknown_adapter_unavailable(make_client):
    client, store, _ = _setup(make_client, job=_Job(agent_name="other"))
    client.app.state.agent_adapters = {"hermes": _FakeAdapter(store)}
    resp = _post(client)
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "agent_relay_unavailable"


def test_missing_relay_state_unavailable(make_client):
    client, _, _ = _setup(make_client, with_relay=False)
    resp = _post(client)
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "agent_relay_unavailable"


def test_routes_by_agent_name(make_client):
    client, store, hermes = _setup(make_client, job=_Job(agent_name="openclaw"))
    openclaw = _FakeAdapter(store)
    client.app.state.agent_adapters = {"hermes": hermes, "openclaw": openclaw}
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert openclaw.calls == [("job-1", "cancelled")]
    assert hermes.calls == []


def test_wrong_token_401(make_client):
    client, _, _ = _setup(make_client, dev_token=SECRET)
    resp = client.post(
        "/api/mylife/agent-jobs/cancel",
        params={"device_id": DEVICE_A, "token": "wrong"},
        json={"job_id": "job-1"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_invalid_body_bad_request(make_client):
    client, _, _ = _setup(make_client)
    for body in ({}, {"job_id": 1}, {"job_id": " "}):
        resp = client.post(
            "/api/mylife/agent-jobs/cancel",
            params={"device_id": DEVICE_A},
            json=body,
        )
        assert resp.status_code == 400, (body, resp.text)
        assert resp.json()["error"]["code"] == "bad_request"


def test_valid_token_ok(make_client):
    client, _, _ = _setup(make_client, dev_token=SECRET)
    resp = client.post(
        "/api/mylife/agent-jobs/cancel",
        params={"device_id": DEVICE_A, "token": TOKEN},
        json={"job_id": "job-1"},
    )
    assert resp.status_code == 200, resp.text
