"""agent job cancel service：归属/状态校验 → adapter.cancel_job → 以存储终态为准。

docs/android_agent_job_status.md §7.2：

- `cancel_job` 的布尔返回值不可作为成功判据（Hermes 在 mark_failed 失败时仍
  返回 True）；取消后必须重读 JobRecord，仍 active → 503 `cancel_incomplete`。
- 取消与自然完成存在竞态：done/delivered → `status="completed"`，结果仍可能
  已投递；API 如实反映，不承诺「取消后一定无结果」。
"""

from __future__ import annotations

import logging

from agent_relay.job_store import (
    ACTIVE_STATUSES,
    STATUS_DELIVERED,
    STATUS_DONE,
    STATUS_FAILED,
)
from bff.common.deps import AgentRelayControl, Principal
from bff.common.store import BffError

log = logging.getLogger(__name__)

_CANCEL_FAIL_REASONS = {"cancelled", "interrupted", "aborted"}


async def build_cancel(
    relay: AgentRelayControl,
    principal: Principal,
    job_id: str,
) -> dict:
    if not principal.device_id:
        raise BffError("bad_request", "device_id is required")

    try:
        job = await relay.job_store.get(job_id)
    except Exception as exc:
        raise BffError("upstream_unavailable", "job store read failed") from exc
    # 不存在与不属于该设备合并同码，不泄漏存在性（镜像 remove 口径）
    if job is None or job.device_id != principal.device_id:
        raise BffError("not_found", "job not found")
    if job.status not in ACTIVE_STATUSES:
        raise BffError("conflict", "job is not active")

    adapter = relay.adapters.get(job.agent_name)
    cancel = getattr(adapter, "cancel_job", None)
    if cancel is None:
        raise BffError(
            "agent_relay_unavailable",
            f"agent {job.agent_name!r} does not support cancel",
        )

    # adapter 可能在 mark_failed 成功后才抛（例如兜底清理异常）：不能直接 502，
    # 先重读终态；已收敛则按 200 返回，仍 active 才是真失败（§7 评审 #2）。
    cancel_error: Exception | None = None
    try:
        await cancel(job_id, "cancelled")
    except Exception as exc:  # noqa: BLE001 — 需要区分「抛前已收敛」与真失败
        cancel_error = exc

    try:
        current = await relay.job_store.get(job_id)
    except Exception as exc:
        raise BffError("upstream_unavailable", "agent cancel failed") from exc

    if current is not None and current.status in ACTIVE_STATUSES:
        if cancel_error is not None:
            raise BffError("upstream_unavailable", "agent cancel failed") from cancel_error
        raise BffError("cancel_incomplete", "job is still active")
    if cancel_error is not None:
        log.warning(
            "agent cancel raised after job converged",
            extra={"job_id": job_id, "error": str(cancel_error)[:200]},
        )

    status = _terminal_status(current)
    log.info("agent job cancel requested", extra={"job_id": job_id, "status": status})
    return {"schema_version": 1, "job_id": job_id, "status": status}


def _terminal_status(current) -> str:
    if current is None:
        return "cancelled"
    if current.status in (STATUS_DONE, STATUS_DELIVERED):
        return "completed"
    if current.status == STATUS_FAILED and current.fail_reason in _CANCEL_FAIL_REASONS:
        return "cancelled"
    return "failed"
