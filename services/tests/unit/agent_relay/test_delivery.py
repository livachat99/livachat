"""Unit tests: JobResultDeliverer — deliver/sweep semantics, per-device lock
double-send protection, deterministic message_id, fail-fast ordering."""

from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from agent_relay.config import AgentRelayConfig
from agent_relay.delivery import JobResultDeliverer
from agent_relay.job_store import STATUS_DELIVERED, STATUS_DONE, JobStore


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    client.key_prefix = "livachat_test"
    yield client
    await client.aclose()


@pytest.fixture
async def job_store(redis):
    return JobStore(redis, key_prefix="livachat_test")


class FakePublisher:
    def __init__(self):
        self.published: list = []
        self.online: set[str] = set()
        self.instances: dict[str, str] = {}

    async def publish(self, msg) -> bool:
        if msg.device_id not in self.online:
            return False
        self.published.append(msg)
        return True

    async def resolve_instance(self, device_id: str) -> str | None:
        return self.instances.get(device_id)


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
def cfg():
    return AgentRelayConfig()


@pytest.fixture
async def deliverer(job_store, publisher, cfg):
    return JobResultDeliverer(job_store, publisher, cfg)


async def _make_done(job_store, *, device_id="d1", session_id="s1", text="result", summary="s"):
    job = await job_store.create(session_id=session_id, device_id=device_id, summary=summary)
    await job_store.mark_running(job.job_key, f"task-{job.job_key}")
    await job_store.mark_done(job.job_key, text)
    return job


class TestDeliver:
    @pytest.mark.asyncio
    async def test_delivers_and_marks_delivered(self, deliverer, publisher, job_store):
        publisher.online.add("d1")
        job = await _make_done(job_store)

        ok = await deliverer.deliver(job)

        assert ok is True
        assert len(publisher.published) == 1
        msg = publisher.published[0]
        assert msg.text == "result"
        assert msg.device_id == "d1"
        assert msg.agent_name == "hermes"
        assert msg.is_final is True
        assert msg.proactive is True
        fetched = await job_store.get(job.job_key)
        assert fetched.status == STATUS_DELIVERED

    @pytest.mark.asyncio
    async def test_deterministic_message_id(self, deliverer, publisher, job_store):
        publisher.online.add("d1")
        job = await _make_done(job_store)

        await deliverer.deliver(job)

        assert publisher.published[0].message_id == f"msg_agent_job_{job.job_key}"

    @pytest.mark.asyncio
    async def test_deliver_carries_job_key(self, deliverer, publisher, job_store):
        """PushMessage 携带 job_key——gateway 消费侧识别 agent job 结果的契约。"""
        publisher.online.add("d1")
        job = await _make_done(job_store)

        await deliverer.deliver(job)

        assert publisher.published[0].job_key == job.job_key

    @pytest.mark.asyncio
    async def test_deliver_carries_agent_identity(self, job_store, publisher, cfg):
        """PushMessage 携带 agent_name/agent_id（adapter nickname，doc §2.5）——
        随 publisher 透传落 chatlog 行，lvdata 据此铸造 agent_job 事件。"""
        deliverer = JobResultDeliverer(job_store, publisher, cfg,
                                       agent_name="hermes", agent_id="nick-1")
        publisher.online.add("d1")
        job = await _make_done(job_store)

        await deliverer.deliver(job)

        msg = publisher.published[0]
        assert msg.agent_name == "hermes"
        assert msg.agent_id == "nick-1"

    @pytest.mark.asyncio
    async def test_device_offline_stays_done(self, deliverer, publisher, job_store):
        job = await _make_done(job_store)  # d1 not online

        ok = await deliverer.deliver(job)

        assert ok is False
        assert publisher.published == []
        fetched = await job_store.get(job.job_key)
        assert fetched.status == STATUS_DONE  # 留待补投

    @pytest.mark.asyncio
    async def test_non_done_job_not_delivered(self, deliverer, publisher, job_store):
        publisher.online.add("d1")
        job = await job_store.create(session_id="s1", device_id="d1", summary="s")
        await job_store.mark_running(job.job_key, "task-x")

        ok = await deliverer.deliver(job)

        assert ok is False
        assert publisher.published == []

    @pytest.mark.asyncio
    async def test_delivered_job_not_redelivered(self, deliverer, publisher, job_store):
        publisher.online.add("d1")
        job = await _make_done(job_store)
        await deliverer.deliver(job)
        publisher.published.clear()

        ok = await deliverer.deliver(job)

        assert ok is False
        assert publisher.published == []

    @pytest.mark.asyncio
    async def test_offline_with_local_check_stays_done(
        self, job_store, publisher, cfg
    ):
        """设备离线（conn key 不存在）→ 不投递不 mark delivered，保持 done。"""
        local = {"d1"}
        deliverer = JobResultDeliverer(
            job_store, publisher, cfg,
            online_check=lambda d: d in local,
        )
        job = await _make_done(job_store)

        ok = await deliverer.deliver(job)

        assert ok is False
        assert publisher.published == []
        assert (await job_store.get(job.job_key)).status == STATUS_DONE

    @pytest.mark.asyncio
    async def test_stale_local_key_without_handler_stays_done(
        self, job_store, publisher, cfg
    ):
        """conn key 指向本实例但进程内无 handler（崩溃/teardown 残留）→ 假在线，
        不 mark delivered，保持 done 等补投。"""
        publisher.instances["d1"] = "inst-A"  # 残留 key
        deliverer = JobResultDeliverer(
            job_store, publisher, cfg,
            online_check=lambda d: False,
        )
        job = await _make_done(job_store)

        ok = await deliverer.deliver(job)

        assert ok is False
        assert publisher.published == []
        assert (await job_store.get(job.job_key)).status == STATUS_DONE

    @pytest.mark.asyncio
    async def test_local_handler_online_delivers(
        self, job_store, publisher, cfg
    ):
        """conn key 指向本实例且 handler 存在 → 正常投递并 mark delivered。"""
        publisher.instances["d1"] = "inst-A"
        publisher.online.add("d1")
        deliverer = JobResultDeliverer(
            job_store, publisher, cfg,
            online_check=lambda d: True,
        )
        job = await _make_done(job_store)

        ok = await deliverer.deliver(job)

        assert ok is True
        assert len(publisher.published) == 1
        assert (await job_store.get(job.job_key)).status == STATUS_DELIVERED

    @pytest.mark.asyncio
    async def test_offline_check_blocks_delivery(
        self, job_store, publisher, cfg
    ):
        """§3.2.2：进程内 online_check=False（设备离线）→ 不投递、不
        mark delivered，保持 done 等补投。"""
        deliverer = JobResultDeliverer(
            job_store, publisher, cfg,
            online_check=lambda d: False,
        )
        job = await _make_done(job_store)

        ok = await deliverer.deliver(job)

        assert ok is False
        assert publisher.published == []
        assert (await job_store.get(job.job_key)).status == STATUS_DONE

    @pytest.mark.asyncio
    async def test_concurrent_deliver_single_publish(self, deliverer, publisher, job_store):
        """D13: per-device lock + 锁内重读 —— 并发 deliver 同一 job 只发一次。"""
        publisher.online.add("d1")
        job = await _make_done(job_store)

        await asyncio.gather(
            deliverer.deliver(job), deliverer.deliver(job), deliverer.deliver(job)
        )

        assert len(publisher.published) == 1
        fetched = await job_store.get(job.job_key)
        assert fetched.status == STATUS_DELIVERED

    @pytest.mark.asyncio
    async def test_concurrent_sweep_and_deliver_no_double_send(
        self, deliverer, publisher, job_store
    ):
        """补投 sweep 与 live complete 竞态：同一 device 上并发不会双发。"""
        publisher.online.add("d1")
        job = await _make_done(job_store)

        await asyncio.gather(
            deliverer.deliver(job),
            deliverer.sweep_device("d1"),
        )

        assert len(publisher.published) == 1


class TestSweep:
    @pytest.mark.asyncio
    async def test_sweep_delivers_pending_in_order(self, deliverer, publisher, job_store):
        publisher.online.add("d1")
        await _make_done(job_store, text="first")
        await asyncio.sleep(0.01)
        j2 = await _make_done(job_store, text="second")
        await asyncio.sleep(0.01)
        await _make_done(job_store, text="third")
        await job_store.mark_delivered(j2.job_key)  # 已投递的不再投

        n = await deliverer.sweep_device("d1")

        assert n == 2
        assert [m.text for m in publisher.published] == ["first", "third"]

    @pytest.mark.asyncio
    async def test_sweep_stops_on_failure_keeps_order(self, deliverer, publisher, job_store):
        """设备中途掉线：保序 + fail-fast，剩余等下次上线。"""
        j1 = await _make_done(job_store, text="first")  # d1 offline → 失败
        await asyncio.sleep(0.01)
        j2 = await _make_done(job_store, text="second")

        n = await deliverer.sweep_device("d1")

        assert n == 0
        assert publisher.published == []
        assert (await job_store.get(j1.job_key)).status == STATUS_DONE
        assert (await job_store.get(j2.job_key)).status == STATUS_DONE

    @pytest.mark.asyncio
    async def test_sweep_empty(self, deliverer, job_store):
        assert await deliverer.sweep_device("no-jobs-device") == 0

    @pytest.mark.asyncio
    async def test_sweep_sets_session_id_on_messages(self, deliverer, publisher, job_store):
        publisher.online.add("d1")
        await _make_done(job_store)

        await deliverer.sweep_device("d1", session_id="new-sess")

        assert publisher.published[0].session_id == "new-sess"
