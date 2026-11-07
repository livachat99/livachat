from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
import websockets

from agent_relay.adapters.base import AgentContext, RecordingInfo
from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.config import HermesAdapterConfig
from agent_relay.errors import AdapterConnectionError, AdapterTimeoutError

# -- helpers -----------------------------------------------------------------

@asynccontextmanager
async def _mock_ws_server(handler):
    """Start a local WS mock server on a random port. Yields the port number."""
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


def _cfg(port: int, **overrides) -> HermesAdapterConfig:
    kwargs = {
        "base_url": f"http://127.0.0.1:{port}",
        "api_key": "",
        "timeout_s": 5,
        "max_retries": 0,
        "mode": "reactive",  # these tests exercise the v1 ask() path
    }
    kwargs.update(overrides)
    return HermesAdapterConfig(**kwargs)


def test_nickname_from_config():
    adapter = HermesAdapter(HermesAdapterConfig(nickname="小黑"))
    assert adapter.nickname == "小黑"


async def _send_rpc(ws, result=None, error=None, req_id=None):
    """Send a JSON-RPC response."""
    msg = {}
    if result is not None:
        msg["result"] = result
    if error is not None:
        msg["error"] = error
    if req_id is not None:
        msg["id"] = req_id
    await ws.send(_to_json(msg))


async def _send_event(ws, event_type, payload=None):
    """Send a Hermes-style event (method="event", type in params.type)."""
    params = {"type": event_type}
    if payload is not None:
        params["payload"] = payload
    msg = {"method": "event", "params": params}
    await ws.send(_to_json(msg))


def _to_json(data: dict) -> str:
    return json.dumps({**data, "jsonrpc": "2.0"}, ensure_ascii=False)


# -- fixtures ----------------------------------------------------------------


@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(
        session_id="sess-1",
        device_id="dev-1",
        language="en",
    )


# -- tests -------------------------------------------------------------------


class TestHermesAdapterProperties:
    async def test_name(self):
        cfg = HermesAdapterConfig(base_url="http://localhost:8080")
        adapter = HermesAdapter(cfg)
        assert adapter.name == "hermes"


class TestHermesAsk:
    async def test_full_response(self, ctx):
        """Delta events are ignored; only message.complete text is yielded."""

        async def handler(ws):
            await _send_event(ws, "gateway.ready")
            # session.create
            raw = await ws.recv()
            req = json.loads(raw)
            await _send_rpc(ws, result={"session_id": "sess-1"}, req_id=req["id"])
            # prompt.submit
            raw = await ws.recv()
            req = json.loads(raw)
            await _send_rpc(ws, result={"status": "streaming"}, req_id=req["id"])
            # deltas (should be ignored)
            await _send_event(ws, "thinking.delta", {"text": "thinking..."})
            await _send_event(ws, "reasoning.delta", {"text": "reasoning..."})
            await _send_event(ws, "message.delta", {"text": "partial"})
            # message.complete
            await _send_event(ws, "message.complete", {"text": "Full response", "session_id": "sess-1"})
            # session.close (notification, consume and ignore)
            try:
                await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                pass

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port))
            await adapter.connect()
            chunks = [c async for c in adapter.ask("hi", ctx)]
            await adapter.close()

        assert len(chunks) == 1
        assert chunks[0].text == "Full response"
        assert chunks[0].is_final is True

    async def test_approval_auto_deny(self, ctx):
        """approval.request is auto-denied, then flow continues."""

        async def handler(ws):
            await _send_event(ws, "gateway.ready")
            raw = await ws.recv()  # session.create
            req = json.loads(raw)
            await _send_rpc(ws, result={"session_id": "sess-1"}, req_id=req["id"])
            raw = await ws.recv()  # prompt.submit
            req = json.loads(raw)
            await _send_rpc(ws, result={"status": "streaming"}, req_id=req["id"])
            await _send_event(ws, "approval.request", {"approval_id": "appr-1"})
            # Wait for auto-deny
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            deny_msg = json.loads(raw)
            assert deny_msg["method"] == "approval.respond"
            assert deny_msg["params"]["choice"] == "deny"
            assert deny_msg["params"]["all"] is True
            # Continue to completion
            await _send_event(ws, "message.complete", {"text": "Done after deny", "session_id": "sess-1"})
            try:
                await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                pass

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port))
            await adapter.connect()
            chunks = [c async for c in adapter.ask("hi", ctx)]
            await adapter.close()

        assert len(chunks) == 1
        assert chunks[0].text == "Done after deny"

    async def test_error_event(self, ctx):
        """Receiving an error event raises AdapterConnectionError."""

        async def handler(ws):
            await _send_event(ws, "gateway.ready")
            raw = await ws.recv()  # session.create
            req = json.loads(raw)
            await _send_rpc(ws, result={"session_id": "sess-1"}, req_id=req["id"])
            raw = await ws.recv()  # prompt.submit
            req = json.loads(raw)
            await _send_rpc(ws, result={"status": "streaming"}, req_id=req["id"])
            await _send_event(ws, "error", {"code": -32000, "message": "Server error"})
            try:
                await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                pass

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port))
            await adapter.connect()
            with pytest.raises(AdapterConnectionError) as exc_info:
                async for _ in adapter.ask("hi", ctx):
                    pass
            await adapter.close()

        assert exc_info.value.adapter_name == "hermes"

    async def test_jsonrpc_error(self, ctx):
        """JSON-RPC error response on session.create raises AdapterConnectionError."""

        async def handler(ws):
            await _send_event(ws, "gateway.ready")
            raw = await ws.recv()  # session.create
            req = json.loads(raw)
            await _send_rpc(ws, error={"code": -32603, "message": "Internal error"}, req_id=req["id"])

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port))
            await adapter.connect()
            with pytest.raises(AdapterConnectionError) as exc_info:
                async for _ in adapter.ask("hi", ctx):
                    pass
            await adapter.close()

        assert exc_info.value.adapter_name == "hermes"
        assert "Internal error" in str(exc_info.value)

    async def test_connection_refused(self, ctx):
        """Connecting to a closed port raises AdapterConnectionError.

        connect() no longer opens a WS (lazy per-device connections, D8);
        the failure surfaces when a device connection is first requested."""
        adapter = HermesAdapter(_cfg(19999, timeout_s=2))
        with pytest.raises(AdapterConnectionError):
            async for _ in adapter.ask("hi", ctx):
                pass

    async def test_connection_drop_mid_stream(self, ctx):
        """WS closing mid-stream raises AdapterConnectionError."""

        async def handler(ws):
            await _send_event(ws, "gateway.ready")
            raw = await ws.recv()  # session.create
            req = json.loads(raw)
            await _send_rpc(ws, result={"session_id": "sess-1"}, req_id=req["id"])
            raw = await ws.recv()  # prompt.submit
            req = json.loads(raw)
            await _send_rpc(ws, result={"status": "streaming"}, req_id=req["id"])
            await _send_event(ws, "thinking.delta", {"text": "thinking..."})
            # Drop connection
            await ws.close()

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port))
            await adapter.connect()
            with pytest.raises(AdapterConnectionError) as exc_info:
                async for _ in adapter.ask("hi", ctx):
                    pass
            await adapter.close()

        assert exc_info.value.adapter_name == "hermes"

    async def test_timeout_gateway_ready(self, ctx):
        """Server accepts but never sends gateway.ready → AdapterTimeoutError.

        Lazy per-device connect: the timeout surfaces on the first ask()."""
        async def handler(ws):
            await asyncio.sleep(10)

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port, timeout_s=1))
            with pytest.raises(AdapterTimeoutError) as exc_info:
                async for _ in adapter.ask("hi", ctx):
                    pass

        assert exc_info.value.adapter_name == "hermes"

    async def test_retry_on_connection_drop(self, ctx):
        """Connection drops on first attempt, retry succeeds on second."""
        attempts = 0

        async def handler(ws):
            nonlocal attempts
            attempts += 1
            await _send_event(ws, "gateway.ready")
            raw = await ws.recv()  # session.create
            req = json.loads(raw)
            await _send_rpc(ws, result={"session_id": f"sess-{attempts}"}, req_id=req["id"])
            raw = await ws.recv()  # prompt.submit
            req = json.loads(raw)
            await _send_rpc(ws, result={"status": "streaming"}, req_id=req["id"])

            if attempts == 1:
                await _send_event(ws, "thinking.delta", {"text": "..."})
                await ws.close()
            else:
                await _send_event(ws, "message.complete", {"text": "recovered", "session_id": "sess-2"})
                try:
                    await asyncio.wait_for(ws.recv(), timeout=1.0)
                except TimeoutError:
                    pass

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port, max_retries=1))
            await adapter.connect()
            chunks = [c async for c in adapter.ask("hi", ctx)]
            await adapter.close()

        assert len(chunks) == 1
        assert chunks[0].text == "recovered"
        assert attempts == 2

    async def test_empty_response(self, ctx):
        """message.complete with empty text yields an empty chunk."""

        async def handler(ws):
            await _send_event(ws, "gateway.ready")
            raw = await ws.recv()  # session.create
            req = json.loads(raw)
            await _send_rpc(ws, result={"session_id": "sess-1"}, req_id=req["id"])
            raw = await ws.recv()  # prompt.submit
            req = json.loads(raw)
            await _send_rpc(ws, result={"status": "streaming"}, req_id=req["id"])
            await _send_event(ws, "message.complete", {"text": "", "session_id": "sess-1"})
            try:
                await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                pass

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port))
            await adapter.connect()
            chunks = [c async for c in adapter.ask("hi", ctx)]
            await adapter.close()

        assert len(chunks) == 1
        assert chunks[0].text == ""

    async def test_sequential_asks(self, ctx):
        """Multiple ask() calls execute serially on the same WS connection."""

        async def handler(ws):
            await _send_event(ws, "gateway.ready")
            for i in range(3):
                raw = await ws.recv()  # session.create
                req = json.loads(raw)
                await _send_rpc(ws, result={"session_id": f"sess-{i}"}, req_id=req["id"])
                raw = await ws.recv()  # prompt.submit
                req = json.loads(raw)
                await _send_rpc(ws, result={"status": "streaming"}, req_id=req["id"])
                await _send_event(ws, "message.delta", {"text": f"partial {i}"})
                await _send_event(ws, "message.complete", {"text": f"response {i}", "session_id": f"sess-{i}"})
                raw = await ws.recv()  # session.close

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port))
            await adapter.connect()
            for i in range(3):
                chunks = [c async for c in adapter.ask(f"query {i}", ctx)]
                assert len(chunks) == 1
                assert chunks[0].text == f"response {i}"
            await adapter.close()

    async def test_passes_text_to_prompt_submit(self, ctx):
        """Verify the prompt.submit request carries the correct text."""
        captured_texts: list[str] = []

        async def handler(ws):
            await _send_event(ws, "gateway.ready")
            raw = await ws.recv()  # session.create
            req = json.loads(raw)
            await _send_rpc(ws, result={"session_id": "sess-1"}, req_id=req["id"])
            raw = await ws.recv()  # prompt.submit
            msg = json.loads(raw)
            captured_texts.append(msg["params"]["text"])
            await _send_rpc(ws, result={"status": "streaming"}, req_id=msg["id"])
            await _send_event(ws, "message.complete", {"text": "ok", "session_id": "sess-1"})
            try:
                await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                pass

        async with _mock_ws_server(handler) as port:
            adapter = HermesAdapter(_cfg(port))
            await adapter.connect()
            async for _ in adapter.ask("hello world", ctx):
                pass
            await adapter.close()

        assert len(captured_texts) == 1
        assert captured_texts[0].startswith("hello world\n\n")
        # docs/agent_multilingual.md：reactive ask 追加目标语言指令
        assert '面向用户输出语言:`"en"`' in captured_texts[0]
        assert "用户消息本身的语言不算要求" in captured_texts[0]


class TestHermesAnalyzeRecording:
    async def test_analyze_recording_noop(self, ctx):
        """analyze_recording is a no-op: yields nothing."""
        cfg = HermesAdapterConfig(base_url="http://localhost:8080")
        adapter = HermesAdapter(cfg)
        info = RecordingInfo(
            recording_id="rec-1",
            session_id="sess-1",
            device_id="dev-1",
            manifest_path="/tmp/manifest.json",
            duration_ms=60000,
            transcript_endpoint="http://localhost:8080/v1/recordings/rec-1/transcript",
        )
        chunks = [c async for c in adapter.analyze_recording(info, ctx)]
        assert len(chunks) == 0


class TestReconnectBackoff:
    """D11 / §8.3: per-device 指数退避 + jitter 分散重连，防风暴。"""

    def _conn(self, adapter, device_id="d1"):
        from agent_relay.adapters.hermes import _DeviceConnection

        conn = _DeviceConnection(device_id=device_id)
        adapter._devices[device_id] = conn
        return conn

    def test_exponential_growth_capped(self):
        cfg = HermesAdapterConfig(reconnect_backoff_initial_s=1, reconnect_backoff_max_s=30)
        adapter = HermesAdapter(cfg)
        conn = self._conn(adapter)
        delays = []
        for attempt in range(6):
            conn.reconnect_attempts = attempt
            delays.append(adapter._backoff_delay(conn))
        assert delays[0] < 2.0
        assert delays[-1] <= 30.0
        # 退避单调不下降（jitter 范围内）
        assert delays[5] >= delays[0] - 0.5

    def test_jitter_spreads_reconnect_times(self):
        """风暴防护：同刻度重连会被 jitter 分散（§8.3）。"""
        cfg = HermesAdapterConfig(
            reconnect_backoff_initial_s=1, reconnect_backoff_max_s=30, reconnect_jitter=0.25
        )
        adapter = HermesAdapter(cfg)
        # N=50 条连接同时第一次重连：延迟应有分散度，而非同一固定值
        conns = [self._conn(adapter, f"dev-{i}") for i in range(50)]
        delays = {round(adapter._backoff_delay(c), 3) for c in conns}
        assert len(delays) > 10  # ±25% jitter 在 1s 附近应产生明显分散

    def test_jitter_zero_is_deterministic(self):
        cfg = HermesAdapterConfig(
            reconnect_backoff_initial_s=2, reconnect_backoff_max_s=30, reconnect_jitter=0.0
        )
        adapter = HermesAdapter(cfg)
        conn = self._conn(adapter)
        conn.reconnect_attempts = 3
        assert adapter._backoff_delay(conn) == 16.0  # 2 * 2^3


class TestDeviceConnectionLimits:
    """D8: max_device_connections 超限 → idle LRU 驱逐（仅无 active job）。"""

    async def test_evicts_idle_lru_only(self):
        from agent_relay.adapters.hermes import _DeviceConnection

        cfg = HermesAdapterConfig(max_device_connections=2)
        adapter = HermesAdapter(cfg)
        adapter.job_store = None  # no job store → no active jobs

        a = _DeviceConnection("a", last_active_at=100.0)
        b = _DeviceConnection("b", last_active_at=200.0)
        c = _DeviceConnection("c", last_active_at=300.0)
        adapter._devices = {"a": a, "b": b, "c": c}

        # 超限 1 个且即将新建 1 条 → 驱逐最旧的 a、b，剩 max-1 条
        await adapter._evict_idle_lru()
        assert set(adapter._devices) == {"c"}

    async def test_active_job_device_never_evicted(self):
        from agent_relay.adapters.hermes import _DeviceConnection

        cfg = HermesAdapterConfig(max_device_connections=1)
        adapter = HermesAdapter(cfg)

        class _JS:
            async def outstanding_jobs_for_device(self, device_id):
                return [{"job_key": "jk", "task_id": "t", "summary": "s"}] if device_id == "busy" else []

        adapter.job_store = _JS()
        busy = _DeviceConnection("busy", last_active_at=1.0)
        idle = _DeviceConnection("idle", last_active_at=2.0)
        adapter._devices = {"busy": busy, "idle": idle}

        await adapter._evict_idle_lru()
        # busy 有 active job 不可驱逐；idle 被驱逐后仍超限？max=1 → 驱逐 idle
        assert "busy" in adapter._devices
        assert "idle" not in adapter._devices

    async def test_active_job_check_fails_closed_on_redis_error(self):
        """P1-6: 查询 active job 失败（Redis 不可用）→ fail-closed 保留连接，
        避免抖动期误回收有 running job 的连接。"""
        adapter = HermesAdapter(HermesAdapterConfig())

        class _BoomJS:
            async def outstanding_jobs_for_device(self, device_id):
                raise RuntimeError("redis down")

        adapter.job_store = _BoomJS()
        assert await adapter._device_has_active_jobs("d1") is True
        assert await adapter._keep_connection("d1") is True
