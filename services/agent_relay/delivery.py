"""JobResultDeliverer — single funnel for all background job result delivery.

Design doc §5.5 (fd_agent_relay_service_v2.md). Both delivery paths (live
job completion and the on-reconnect redelivery sweep) go through this
class, serialized per device so the "sweep and live complete race" cannot
double-send (D13). The message_id is deterministic (`msg_agent_job_{job_key}`,
D6) so clients can dedupe a redelivery after a relay crash.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from agent_relay.config import AgentRelayConfig
from agent_relay.job_store import STATUS_DONE, JobRecord, JobStore
from agent_relay.metrics import inc_job_event
from agent_relay.models import PushMessage
from agent_relay.publisher import PushPublisher

logger = logging.getLogger("livachat.agent_relay.delivery")


class JobResultDeliverer:
    """Delivers done job results to the device via PushPublisher."""

    def __init__(
        self,
        job_store: JobStore,
        publisher: PushPublisher,
        cfg: AgentRelayConfig,
        *,
        agent_name: str = "hermes",
        agent_id: str = "",
        agent_ids: dict[str, str] | None = None,
        on_delivered: Callable[[str], Awaitable[None]] | None = None,
        online_check: Callable[[str], bool] | None = None,
    ) -> None:
        """on_delivered(device_id) is invoked after a successful delivery
        (D8/B3): the adapter uses it to reap the device connection once no
        active job / live session needs it to stay up.

        online_check（conn_mgr.is_online）：设备级在线校验（§3.2.2）。relay
        与 gateway 同进程——进程内无 handler 即离线，job 保持 done 等补投，
        避免假 delivered（原 conn key 指向本实例但无 handler 的残留判断在
        单进程下逻辑塌陷为一次 is_online）。

        agent_name/agent_id 为记录缺失时的回退；多 adapter 共存时投递以
        `JobRecord.agent_name` 为准（agent_ids: name → nickname 映射）。"""
        self._job_store = job_store
        self._publisher = publisher
        self._cfg = cfg
        self._agent_name = agent_name
        self._agent_id = agent_id
        self._agent_ids = agent_ids or {}
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._on_delivered = on_delivered
        self._online_check = online_check

    def _identity(self, current: JobRecord) -> tuple[str, str]:
        """(agent_name, agent_id)：以 JobRecord 归属为准，构造参数兜底。"""
        name = current.agent_name or self._agent_name
        agent_id = self._agent_ids.get(name, "")
        if not agent_id and name == self._agent_name:
            agent_id = self._agent_id
        return name, agent_id

    async def deliver(self, job: JobRecord, *, session_id: str = "") -> bool:
        """Deliver one done job. Success → mark_delivered; failure (device
        offline) → stays done for the next sweep.

        Per-device lock + re-read inside the lock: a concurrent sweep and a
        live job completion for the same job both see the fresh state, so
        only one of them publishes (D13 check-then-act).
        """
        lock = self._device_locks.setdefault(job.device_id, asyncio.Lock())
        async with lock:
            current = await self._job_store.get(job.job_key)
            if current is None or current.status != STATUS_DONE:
                return False
            if self._online_check is not None and not self._online_check(current.device_id):
                # 设备离线（进程内无 handler）：不投递也不 mark delivered，
                # 保持 done 等补投。
                logger.debug(
                    "delivery skipped: device offline",
                    extra={"job_key": current.job_key, "device_id": current.device_id},
                )
                inc_job_event(self._identity(current)[0], "delivery_pending")
                return False
            name, agent_id = self._identity(current)
            msg = PushMessage(
                device_id=current.device_id,
                session_id=session_id,  # 不下发（push_subscriber 不透传）
                message_id=f"msg_agent_job_{current.job_key}",  # 确定性幂等键（D6）
                text=current.result_text,
                index=0,
                is_final=True,
                agent_name=name,
                agent_id=agent_id,
                proactive=True,
                job_key=current.job_key,
            )
            if await self._publisher.publish(msg):
                await self._job_store.mark_delivered(current.job_key)
                inc_job_event(name, "delivered")
                if self._on_delivered is not None:
                    try:
                        await self._on_delivered(current.device_id)
                    except Exception:
                        logger.warning(
                            "on_delivered callback failed",
                            extra={"device_id": current.device_id},
                            exc_info=True,
                        )
                return True
            inc_job_event(name, "delivery_pending")
            return False

    async def sweep_device(self, device_id: str, *, session_id: str = "") -> int:
        """Device-online redelivery (R2): deliver all done-undelivered jobs in
        completed_at order; stop at the first failure (order + fail-fast).

        Exceptions (e.g. Redis outage) are caught and logged: the sweep is
        spawned as a bare task from on_session_started and must not leak an
        unretrieved task exception (A4); undelivered jobs stay done and the
        next device-online sweep picks them up."""
        delivered = 0
        try:
            for job in await self._job_store.list_pending_delivery(device_id):
                if await self.deliver(job, session_id=session_id):
                    delivered += 1
                else:
                    break  # 设备又掉线：保序，剩余等下次上线
        except Exception:
            logger.warning(
                "redelivery sweep failed",
                extra={"device_id": device_id},
                exc_info=True,
            )
        return delivered
