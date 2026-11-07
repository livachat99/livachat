"""Unit tests: JobStatusNotifier — §3.2 decision table, fingerprint semantics,
per-device serialization, offline dirty-keep, scan pre-check, tick resilience.

Design doc: docs/fd_job_status.md §7 (unit layer).
"""

from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis
import pytest

from agent_relay.job_status import JobStatusNotifier
from agent_relay.job_store import JOB_SUMMARY_MAX_CHARS, JOB_TTL_S, JobStore

# -- fakes -------------------------------------------------------------------


class FakePublisher:
    def __init__(self):
        self.published: list = []
        self.online: set[str] = set()
        self.instances: dict[str, str] = {}
        self.fail_publish = False

    async def publish(self, msg) -> bool:
        if self.fail_publish or msg.device_id not in self.online:
            return False
        self.published.append(msg)
        return True

    async def resolve_instance(self, device_id: str) -> str | None:
        return self.instances.get(device_id)


class CountingStore:
    """Wraps JobStore to count per-device reads (scan pre-check assertion)."""

    def __init__(self, store: JobStore):
        self._store = store
        self.device_reads = 0
        self.global_reads = 0

    async def list_active_by_device(self):
        self.global_reads += 1
        return await self._store.list_active_by_device()

    async def device_job_keys(self, device_id: str):
        self.device_reads += 1
        return await self._store.device_job_keys(device_id)

    async def list_active_for_device(self, device_id: str):
        self.device_reads += 1
        return await self._store.list_active_for_device(device_id)

    async def get(self, job_key: str):
        self.device_reads += 1
        return await self._store.get(job_key)


# -- fixtures ----------------------------------------------------------------


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    client.key_prefix = "livachat_test"
    yield client
    await client.aclose()


@pytest.fixture
async def job_store(redis):
    return JobStore(redis, key_prefix="livachat_test")


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
async def notifier(job_store, publisher):
    n = JobStatusNotifier(job_store, publisher)
    yield n
    await n.stop()


async def _make_running(job_store, *, device_id="d1", session_id="s1", summary="s"):
    job = await job_store.create(session_id=session_id, device_id=device_id, summary=summary)
    assert await job_store.mark_running(job.job_key, f"task-{job.job_key}")
    return job


# -- decision table (§3.2) ---------------------------------------------------


class TestDecisionTable:
    @pytest.mark.asyncio
    async def test_change_frame_sent(self, notifier, publisher, job_store):
        """cur 非空 且 cur != last → 发送变化帧。"""
        publisher.online.add("d1")
        await _make_running(job_store)

        await notifier.on_job_change("d1")

        assert len(publisher.published) == 1
        msg = publisher.published[0]
        assert msg.kind == "job_status"
        assert msg.device_id == "d1"
        assert len(msg.jobs) == 1
        assert msg.jobs[0].agent_name == "hermes"
        assert msg.jobs[0].running_time_ms >= 0

    @pytest.mark.asyncio
    async def test_clear_frame_on_nonempty_to_empty(self, notifier, publisher, job_store):
        """cur 空 且 last 非空 → 发送空列表（清除信号）。"""
        publisher.online.add("d1")
        job = await _make_running(job_store)
        await notifier.on_job_change("d1")
        await job_store.mark_done(job.job_key, "result")

        await notifier.on_job_change("d1")

        assert len(publisher.published) == 2
        assert publisher.published[1].jobs == []

    @pytest.mark.asyncio
    async def test_throttled_frame_when_timer_due(self, notifier, publisher, job_store):
        """cur 非空 且 cur == last 且 距上次发送 ≥ interval → 周期帧。"""
        publisher.online.add("d1")
        await _make_running(job_store)
        await notifier.on_job_change("d1")
        # 直接把上次发送时刻拨回 interval 之前
        fp, _ = notifier._last["d1"]
        notifier._last["d1"] = (fp, time.monotonic() - notifier._interval_s - 1)

        await notifier.on_job_change("d1")

        assert len(publisher.published) == 2
        assert publisher.published[1].jobs[0].job_id == publisher.published[0].jobs[0].job_id

    @pytest.mark.asyncio
    async def test_force_sends_ignoring_timer(self, notifier, publisher, job_store):
        """force（session.started 首报）且 cur 非空 → 无视计时立即发送。"""
        publisher.online.add("d1")
        await _make_running(job_store)
        await notifier.on_job_change("d1")

        await notifier.on_device_online("d1")  # 计时未到，force 仍发

        assert len(publisher.published) == 2

    @pytest.mark.asyncio
    async def test_no_send_when_timer_not_due(self, notifier, publisher, job_store):
        """其余：快照未变且周期未到 → 不发送。"""
        publisher.online.add("d1")
        await _make_running(job_store)
        await notifier.on_job_change("d1")

        await notifier.on_job_change("d1")

        assert len(publisher.published) == 1

    @pytest.mark.asyncio
    async def test_silent_when_empty_and_no_record(self, notifier, publisher, job_store):
        """不变式 2：cur 空 且 last 空/无记录 → 静默（无空帧噪声）。"""
        publisher.online.add("d1")

        await notifier.on_job_change("d1")
        await notifier.on_device_online("d1")

        assert publisher.published == []


# -- fingerprint semantics (D2) ----------------------------------------------


class TestFingerprint:
    @pytest.mark.asyncio
    async def test_running_time_ms_not_in_fingerprint(self, notifier, publisher, job_store):
        """仅 running_time_ms 变化（时间流逝）不触发变化帧。"""
        publisher.online.add("d1")
        await _make_running(job_store)
        await notifier.on_job_change("d1")
        await asyncio.sleep(0.02)  # 时间流逝，running_time_ms 会变

        await notifier.on_job_change("d1")

        assert len(publisher.published) == 1

    @pytest.mark.asyncio
    async def test_change_frame_refreshes_last_sent_at(self, notifier, publisher, job_store):
        """不变式 4：变化帧刷新 last_sent_at —— interval 内不再出节流帧。"""
        publisher.online.add("d1")
        j1 = await _make_running(job_store)
        await notifier.on_job_change("d1")
        # 第二个 job 进入 running → 变化帧（整帧含两个 job）
        j2 = await _make_running(job_store)
        await asyncio.sleep(0.01)  # 确保 started_at 可区分
        await notifier.on_job_change("d1")

        assert len(publisher.published) == 2
        assert [j.job_id for j in publisher.published[1].jobs] == [j1.job_key, j2.job_key]

        # interval 未到 → 无节流帧
        await notifier.on_job_change("d1")
        assert len(publisher.published) == 2

    @pytest.mark.asyncio
    async def test_legacy_record_started_at_falls_back_to_created_at(
        self, notifier, publisher, job_store
    ):
        """存量记录无 started_at → 指纹与 started_at_ms 回退 created_at。"""
        publisher.online.add("d1")
        job = await _make_running(job_store)
        await job_store._redis.hdel(job_store._job_key(job.job_key), "started_at")

        await notifier.on_job_change("d1")

        assert len(publisher.published) == 1
        item = publisher.published[0].jobs[0]
        assert item.started_at_ms == int(job.created_at * 1000)

    @pytest.mark.asyncio
    async def test_snapshot_sorted_by_started_at_ascending(
        self, notifier, publisher, job_store
    ):
        """快照按 started_at 升序输出（最早启动在前）。"""
        publisher.online.add("d1")
        j1 = await _make_running(job_store, summary="first")
        await asyncio.sleep(0.01)
        j2 = await _make_running(job_store, summary="second")

        await notifier.on_job_change("d1")

        assert [j.job_id for j in publisher.published[0].jobs] == [j1.job_key, j2.job_key]

    @pytest.mark.asyncio
    async def test_overlong_legacy_summary_truncated_on_wire(
        self, notifier, publisher, job_store
    ):
        """存量数据（截断上线前入库）summary 超长 → 载荷与指纹仍受硬上界约束。"""
        publisher.online.add("d1")
        job = await _make_running(job_store)
        # 模拟截断上线前的存量数据：直接写入超长 summary
        await job_store._redis.hset(
            job_store._job_key(job.job_key), "summary", "x" * (JOB_SUMMARY_MAX_CHARS * 2)
        )

        await notifier.on_job_change("d1")
        assert len(publisher.published) == 1
        assert len(publisher.published[0].jobs[0].summary) == JOB_SUMMARY_MAX_CHARS

        # 指纹同样截断：再次评估（仅时间流逝）不触发变化帧
        await notifier.on_job_change("d1")
        assert len(publisher.published) == 1

    @pytest.mark.asyncio
    async def test_requested_jobs_excluded(self, notifier, publisher, job_store):
        """requested 状态不通知：快照只含 running。"""
        publisher.online.add("d1")
        running = await _make_running(job_store, summary="running")
        await job_store.create(session_id="s1", device_id="d1", summary="requested-only")

        await notifier.on_job_change("d1")

        assert [j.job_id for j in publisher.published[0].jobs] == [running.job_key]


# -- offline / publish failure -----------------------------------------------


class TestOffline:
    @pytest.mark.asyncio
    async def test_offline_publish_false_keeps_dirty(self, notifier, publisher, job_store):
        """设备离线：跳过、保持脏（last 不更新），上线后由变化帧补齐。"""
        await _make_running(job_store)  # d1 不在 publisher.online

        await notifier.on_job_change("d1")

        assert publisher.published == []
        assert "d1" not in notifier._last

        publisher.online.add("d1")
        await notifier.on_job_change("d1")  # 脏标记 → 变化帧补齐

        assert len(publisher.published) == 1

    @pytest.mark.asyncio
    async def test_stale_local_conn_key_judged_offline(self, job_store, publisher):
        """conn key 指向本实例但进程内无 handler（残留 key）→ 判离线。"""
        publisher.instances["d1"] = "inst-A"
        n = JobStatusNotifier(
            job_store, publisher, online_check=lambda d: False
        )
        publisher.online.add("d1")  # 即使 publish 会成功，本地校验先拦下
        await _make_running(job_store)

        await n.on_job_change("d1")

        assert publisher.published == []
        assert "d1" not in n._last
        await n.stop()

    @pytest.mark.asyncio
    async def test_offline_check_skips_send(self, job_store, publisher):
        """§3.2.2：进程内 online_check=False（设备离线）→ 不发帧、不更新
        _last（脏标记保持，后续触发路径重试）。"""
        n = JobStatusNotifier(
            job_store, publisher, online_check=lambda d: False
        )
        await _make_running(job_store)

        await n.on_job_change("d1")

        assert publisher.published == []
        assert "d1" not in n._last
        await n.stop()

    @pytest.mark.asyncio
    async def test_empty_frame_success_removes_state_entry(
        self, notifier, publisher, job_store
    ):
        """不变式 3：空快照发送成功后删除状态条目。"""
        publisher.online.add("d1")
        job = await _make_running(job_store)
        await notifier.on_job_change("d1")
        assert "d1" in notifier._last

        await job_store.mark_done(job.job_key, "ok")
        await notifier.on_job_change("d1")

        assert publisher.published[1].jobs == []
        assert "d1" not in notifier._last


# -- scan tick ---------------------------------------------------------------


class TestScan:
    @pytest.mark.asyncio
    async def test_scan_precheck_no_per_device_reads_when_unchanged(
        self, job_store, publisher
    ):
        """扫描预判定：指纹未变且周期未到 → 无 per-device 读。"""
        publisher.online.add("d1")
        counting = CountingStore(job_store)
        n = JobStatusNotifier(counting, publisher)
        await _make_running(job_store)
        await n.on_job_change("d1")
        reads_before = counting.device_reads

        await n._scan_once()

        assert counting.device_reads == reads_before
        assert counting.global_reads == 1
        await n.stop()

    @pytest.mark.asyncio
    async def test_scan_tick_restart_self_heal(self, job_store, publisher):
        """relay 重启（内存状态清空）：首 tick 视为变化 → 补发（不依赖 session.started）。"""
        publisher.online.add("d1")
        n = JobStatusNotifier(job_store, publisher, scan_interval_s=0.01)
        await _make_running(job_store)

        await n.start()
        await asyncio.sleep(0.05)
        await n.stop()

        assert len(publisher.published) == 1

    @pytest.mark.asyncio
    async def test_offline_evaluate_skips_history_reads(self, job_store, publisher):
        """在线检查前置：离线脏设备评估只花 1 次 resolve_instance GET，
        不做 per-device 全量历史读（device_job_keys + HGETALL）。"""
        counting = CountingStore(job_store)
        n = JobStatusNotifier(
            counting, publisher, online_check=lambda d: False
        )
        await _make_running(job_store)  # d1 有 running job 但离线（残留/无 conn key）

        await n._scan_once()

        assert counting.device_reads == 0
        assert "d1" not in n._last  # 保持脏
        await n.stop()

    @pytest.mark.asyncio
    async def test_scan_prunes_stale_last_entries(self, job_store, publisher):
        """有界性兜底（不变式 3 补充）：条目比 job TTL 旧 → scan tick 淘汰；
        新条目保留（在线设备的 15s 帧保证条目常新）。"""
        n = JobStatusNotifier(job_store, publisher)
        n._last["device-stale"] = ((), time.monotonic() - JOB_TTL_S - 10)
        n._last["device-fresh"] = ((), time.monotonic())

        await n._scan_once()

        assert "device-stale" not in n._last
        assert "device-fresh" in n._last
        await n.stop()

    @pytest.mark.asyncio
    async def test_scan_tick_error_swallowed_loop_survives(self, job_store, publisher):
        """单 tick 异常记 error 后继续，scan 任务不退出。"""
        publisher.online.add("d1")
        await _make_running(job_store)
        n = JobStatusNotifier(job_store, publisher, scan_interval_s=0.01)

        original = job_store.list_active_by_device
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("redis jitter")
            return await original()

        job_store.list_active_by_device = flaky  # type: ignore[method-assign]
        await n.start()
        await asyncio.sleep(0.05)
        await n.stop()

        assert calls["n"] >= 2  # 首 tick 抛错后仍继续
        assert len(publisher.published) == 1  # 恢复后补发成功
