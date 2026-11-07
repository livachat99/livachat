"""Integration tests: HermesAdapter ↔ in-process mock Hermes server via WebSocket."""

from __future__ import annotations

import pytest

from agent_relay.adapters.base import AgentContext
from agent_relay.adapters.hermes import HermesAdapter
from agent_relay.config import HermesAdapterConfig


@pytest.fixture
async def hermes(mock_hermes):
    cfg = HermesAdapterConfig(
        base_url=mock_hermes.url,
        api_key="",
        timeout_s=10,
        max_retries=1,
        mode="reactive",
    )
    adapter = HermesAdapter(cfg)
    await adapter.connect()
    yield adapter
    await adapter.close()


@pytest.fixture
def ctx():
    return AgentContext(
        session_id="itest-sess-1",
        device_id="itest-dev-1",
        language="en",
    )


class TestHermesAdapterAskIntegration:
    async def test_ws_jsonrpc_from_mock_server(self, hermes, ctx):
        """Full WebSocket JSON-RPC flow against the mock agent server."""
        chunks = [c async for c in hermes.ask("hello", ctx)]
        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert chunks[0].text

    async def test_multiple_requests(self, hermes, ctx):
        """Multiple sequential requests on the same WS connection."""
        for i in range(3):
            chunks = [c async for c in hermes.ask(f"query {i}", ctx)]
            assert len(chunks) == 1
            assert chunks[0].is_final is True
            assert chunks[0].text

    async def test_empty_text_returns_response(self, hermes, ctx):
        """Even empty input should get a response from mock server."""
        chunks = [c async for c in hermes.ask("", ctx)]
        assert len(chunks) == 1

    async def test_context_fields_passed(self, hermes, ctx):
        """Alternate context works (response format verified)."""
        ctx2 = AgentContext(
            session_id="itest-sess-2",
            device_id="itest-dev-2",
            language="zh-CN",
        )
        chunks = [c async for c in hermes.ask("context test", ctx2)]
        assert len(chunks) == 1
        assert chunks[0].text
