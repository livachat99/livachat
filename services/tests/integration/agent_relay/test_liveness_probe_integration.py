"""Integration tests: §5.8 WS 保活——应用层活性探测（tools.list）。

in-process 装配（无需 Redis）：live session 触发保留连接，探测循环对
MockHermesServer 短周期发探测；mock 的 ``swallow_methods`` /
``rpc_error_methods`` 模拟超时与 error 响应。半死连接（TCP/协议层活、
应用层不通）无法人工构造，"吞请求 = 完全无响应"是最逼近的模拟。
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from agent_relay.adapters.base import AgentContext
from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.config import HermesAdapterConfig

_HERMES_LOGGER = "livachat.agent_relay.adapter.hermes"


class _FakeHermesStore:
    """内存 hermes_device_sessions（同 v2 集成测试）。"""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}

    async def load_hermes_session(self, device_uuid: str) -> dict | None:
        return self._data.get(device_uuid.lower())

    async def save_hermes_session(self, *, device_uuid: str,
                                  hermes_session_id: str, stored_session_id: str) -> None:
        self._data[device_uuid.lower()] = {
            "hermes_session_id": hermes_session_id,
            "stored_session_id": stored_session_id,
            "updated_at": "2026-10-06T00:00:00.000Z",
        }

    async def delete_hermes_session(self, device_uuid: str) -> None:
        self._data.pop(device_uuid.lower(), None)


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return True
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.01)


def _probes(mock) -> list[dict]:
    return [r for r in mock.requests if r["method"] == "tools.list"]


def _ctx() -> AgentContext:
    return AgentContext(session_id="psess-live", device_id="pdev-live", language="zh")


@pytest.fixture
async def adapter_factory(mock_hermes):
    created: list[HermesAdapter] = []

    async def _make(**overrides) -> HermesAdapter:
        kwargs = {
            "base_url": mock_hermes.url,
            "api_key": "",
            "timeout_s": 5,
            "max_retries": 0,
            "mode": "proactive",
            "liveness_probe_interval_s": 0.05,
            "liveness_probe_timeout_s": 0.2,
            "liveness_probe_failures": 2,
            "reconnect_backoff_initial_s": 0.05,
            "reconnect_backoff_max_s": 0.1,
            "reconnect_jitter": 0.0,
        }
        kwargs.update(overrides)
        a = HermesAdapter(HermesAdapterConfig(**kwargs))
        a.hermes_store = _FakeHermesStore()
        a.base_dir = "/tmp"
        await a.connect()
        created.append(a)
        return a

    yield _make
    for a in created:
        await a.close()


async def test_liveness_probe_pings_idle_connection(adapter_factory, mock_hermes):
    a = await adapter_factory()
    await a.on_session_start(_ctx())

    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 2)
    probes = _probes(mock_hermes)
    assert probes[0]["params"] == {}          # 空 params：不带 session_id
    assert len(mock_hermes._conns) == 1       # 连接未断


async def test_liveness_probe_timeout_forces_reconnect(adapter_factory, mock_hermes, caplog):
    a = await adapter_factory()
    await a.on_session_start(_ctx())
    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 1)

    mock_hermes.swallow_methods.add("tools.list")
    with caplog.at_level(logging.WARNING, logger=_HERMES_LOGGER):
        # 连续 2 次超时（阈值）→ 拆连 → reader 既有路径重连 → mock 收到新连接
        assert await _wait_until(lambda: len(mock_hermes._conns) >= 2, timeout=5.0)
    assert any("forcing reconnect" in r.getMessage() for r in caplog.records)


async def test_liveness_probe_skipped_when_turn_or_rpc_in_flight(adapter_factory, mock_hermes):
    a = await adapter_factory()
    await a.on_session_start(_ctx())
    conn = a._devices["pdev-live"]
    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 1)

    loop = asyncio.get_running_loop()
    # 在飞 RPC：settle 后（设置前已发出的探测落地）必须停发
    conn.pending[10**9] = loop.create_future()
    await asyncio.sleep(0.1)
    base = len(_probes(mock_hermes))
    await asyncio.sleep(0.25)                 # ≥ 5 个探测周期
    assert len(_probes(mock_hermes)) == base
    conn.pending.clear()

    # 在飞 turn：同上
    conn.turn_futures["t-1"] = loop.create_future()
    await asyncio.sleep(0.1)
    base = len(_probes(mock_hermes))
    await asyncio.sleep(0.25)
    assert len(_probes(mock_hermes)) == base
    conn.turn_futures.clear()


async def test_liveness_probe_disabled_by_zero_interval(adapter_factory, mock_hermes):
    a = await adapter_factory(liveness_probe_interval_s=0)
    await a.on_session_start(_ctx())
    await asyncio.sleep(0.3)
    assert _probes(mock_hermes) == []
    assert a._devices["pdev-live"].liveness_task is None


async def test_liveness_probe_error_response_counts_as_alive(adapter_factory, mock_hermes, caplog):
    a = await adapter_factory()
    mock_hermes.rpc_error_methods["tools.list"] = (5000, "mock probe error")
    await a.on_session_start(_ctx())

    with caplog.at_level(logging.WARNING, logger=_HERMES_LOGGER):
        # error 响应按存活清零：远超阈值（≥4 次）仍不拆连
        assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 4, timeout=3.0)
        await asyncio.sleep(0.1)
    assert len(mock_hermes._conns) == 1
    assert not any("forcing reconnect" in r.getMessage() for r in caplog.records)


async def test_liveness_probe_failure_count_resets_on_success(adapter_factory, mock_hermes, caplog):
    a = await adapter_factory()
    mock_hermes.swallow_methods.add("tools.list")
    await a.on_session_start(_ctx())

    # p1 被吞（swallow 判定在分派时点）→ 超时计 1（未达阈值）；此后恢复响应
    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 1)
    mock_hermes.swallow_methods.discard("tools.list")
    # p2 可见即已应答（mock 同一分派内发响应）→ 其 alive 处理会清零计数
    # （处理先于 p3 发出：pending 栅栏使探测串行）
    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 2)
    mock_hermes.swallow_methods.add("tools.list")

    # p3 被吞 → 超时：计数已清零则 failures=1（不拆）；未清零则 =2 误拆。
    # p4 的前置是 p3 已结算（pending 栅栏）→ p4 可见即 p3 判决已落地
    with caplog.at_level(logging.WARNING, logger=_HERMES_LOGGER):
        assert await _wait_until(
            lambda: len(_probes(mock_hermes)) >= 4
            or len(mock_hermes._conns) >= 2,
            timeout=5.0,
        )
        assert not any("forcing reconnect" in r.getMessage() for r in caplog.records)
        assert len(mock_hermes._conns) == 1
        # p4 也超时 → 2 连超时才达阈值 → 此时才拆连
        assert await _wait_until(lambda: len(mock_hermes._conns) >= 2, timeout=5.0)


async def test_liveness_probe_failure_count_resets_on_reconnect(adapter_factory, mock_hermes, caplog):
    """计数按连接划线："连续超时"以同一连接为界，重连后第一条超时不得
    搭上前一条连接的旧计数（否则误拆刚重建的健康 ws）。"""
    a = await adapter_factory()
    mock_hermes.swallow_methods.add("tools.list")
    await a.on_session_start(_ctx())
    conn = a._devices["pdev-live"]

    # p1 超时（旧连接，failures=1）；p2 发出即 p1 已结算（pending 栅栏）
    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 1)
    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 2)

    # 测试强制拆连（模拟连接层故障）→ reader 重连，mock 收到新连接
    await a._teardown_ws(conn)
    assert await _wait_until(lambda: len(mock_hermes._conns) >= 2, timeout=5.0)
    assert await _wait_until(lambda: conn.ready.is_set(), timeout=5.0)

    # p3 在新连接上被吞 → 超时：计数已随换代归零 → failures=1，不拆。
    # 栅栏：p4 可见 ⇔ p3 已结算
    with caplog.at_level(logging.WARNING, logger=_HERMES_LOGGER):
        assert await _wait_until(
            lambda: len(_probes(mock_hermes)) >= 4
            or len(mock_hermes._conns) >= 3,
            timeout=5.0,
        )
        assert len(mock_hermes._conns) == 2  # 未因 p3 单次超时误拆
        assert not any("forcing reconnect" in r.getMessage() for r in caplog.records)
        # p4 也超时 → 新连接上的 2 连超时 → 此时才拆
        assert await _wait_until(lambda: len(mock_hermes._conns) >= 3, timeout=5.0)


async def test_liveness_probe_does_not_starve_idle_reaper(adapter_factory, mock_hermes):
    a = await adapter_factory(device_conn_idle_reaper_s=0.3)
    ctx = _ctx()
    await a.on_session_start(ctx)
    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 1)

    # 模拟会话意外消失（无 on_session_end）→ 全空闲，只剩 D8 reaper 兜底
    a._live_sessions.clear()
    conn = a._devices[ctx.device_id]
    await asyncio.sleep(0.2)                  # 探测循环多次机会，全部应跳过
    base = len(_probes(mock_hermes))

    conn.last_active_at = time.time() - 60    # 越过 idle_threshold（max(2×period, 30)）
    await asyncio.sleep(0.15)                 # 探测循环再有 ≥2 次机会
    assert len(_probes(mock_hermes)) == base  # 空闲期零探测（探测响应会刷新活动时间）
    assert await _wait_until(lambda: ctx.device_id not in a._devices, timeout=3.0)


async def test_liveness_probe_skipped_during_reconnect(adapter_factory, mock_hermes, caplog):
    a = await adapter_factory()
    await a.on_session_start(_ctx())
    conn = a._devices["pdev-live"]
    assert await _wait_until(lambda: len(_probes(mock_hermes)) >= 1)
    base = len(_probes(mock_hermes))

    # 模拟重连窗口：ready 未 set（真实场景 = reader 正在 backoff/重建；
    # 该窗口的等待超时按跳过处理，不计失败）
    conn.ready.clear()
    with caplog.at_level(logging.WARNING, logger=_HERMES_LOGGER):
        await asyncio.sleep(0.3)              # ≫ 阈值 × 探测周期
        assert len(mock_hermes._conns) == 1   # 未拆连
        assert not any("forcing reconnect" in r.getMessage() for r in caplog.records)
    window_count = len(_probes(mock_hermes))
    assert window_count - base <= 1           # 窗口内至多 1 个设置前在飞探测

    # 窗口结束 → 探测恢复
    conn.ready.set()
    assert await _wait_until(lambda: len(_probes(mock_hermes)) > window_count)
