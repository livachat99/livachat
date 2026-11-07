from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from llm.base import ChatMessage
from llm.providers.openai_compat import OpenAICompatProvider
from shared.utils.errors import ProviderError


def _make_chunk(content: str | None) -> MagicMock:
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = content
    return chunk


async def _async_iter(chunks: list) -> MagicMock:
    """Simulate an OpenAI Stream — async iterable of chunks."""
    for c in chunks:
        yield c


class TestOpenAICompatStream:
    async def test_yields_tokens(self):
        provider = OpenAICompatProvider(name="test", base_url="https://test.api", api_key="k", model="test-model")
        messages = [ChatMessage(role="user", content="hello")]

        stream = _async_iter([_make_chunk("Hello"), _make_chunk(" world"), _make_chunk("!"), _make_chunk(None)])
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=stream)

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client):
            tokens = []
            async for token in provider.stream(messages=messages):
                tokens.append(token)
            assert "".join(tokens) == "Hello world!"

    async def test_first_token_timeout(self):
        provider = OpenAICompatProvider(name="test", base_url="https://test.api", api_key="k", model="test-model")
        messages = [ChatMessage(role="user", content="hi")]

        async def _slow_stream():
            await asyncio.sleep(1)
            yield _make_chunk("late")

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_slow_stream())

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client), \
             patch("llm.providers.openai_compat._DEFAULT_TIMEOUT_FIRST_TOKEN", 0.01), \
             pytest.raises(ProviderError, match="first token timeout"):
            async for _ in provider.stream(messages=messages):
                pass

    async def test_empty_stream(self):
        provider = OpenAICompatProvider(name="test", base_url="https://test.api", api_key="k", model="test-model")
        messages = [ChatMessage(role="user", content="hi")]

        async def _empty_stream():
            return
            yield  # pragma: no cover

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_empty_stream())

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client):
            tokens = []
            async for token in provider.stream(messages=messages):
                tokens.append(token)
            assert tokens == []


_CHUNK_JSON = (
    '{"id":"1","object":"chat.completion.chunk","created":0,"model":"m",'
    '"choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}'
)


class _FakeApiStatusError(Exception):
    """模拟 openai.APIStatusError（携带 status_code）——测试时替换模块符号避免
    构造 vendored httpx2 的 request/response 对象。"""

    def __init__(self, status_code: int = 401):
        super().__init__(f"Error code: {status_code} - fake body")
        self.status_code = status_code


class TestTimeoutAndHttpStatus:
    async def test_client_timeout_configured(self):
        """D1：client 构造带 httpx.Timeout(60, connect=5)（SDK 默认 600s 收紧为 60s）。"""
        provider = OpenAICompatProvider(name="test", base_url="https://test.api", api_key="k", model="test-model")
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_async_iter([_make_chunk("Hi")]))

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client) as mock_cls:
            async for _ in provider.stream(messages=[ChatMessage(role="user", content="hi")]):
                pass
        assert mock_cls.call_args.kwargs["timeout"] == httpx.Timeout(60.0, connect=5.0)

    async def test_create_status_error_sets_http_status(self):
        """D3：create() 阶段 APIStatusError → ProviderError.http_status 填充。"""
        provider = OpenAICompatProvider(name="test", base_url="https://test.api", api_key="k", model="test-model")
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_FakeApiStatusError(401))

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client), \
             patch("llm.providers.openai_compat.APIStatusError", _FakeApiStatusError), \
             pytest.raises(ProviderError) as ei:
            async for _ in provider.stream(messages=[ChatMessage(role="user", content="hi")]):
                pass
        assert ei.value.http_status == 401

    async def test_create_generic_error_http_status_none(self):
        provider = OpenAICompatProvider(name="test", base_url="https://test.api", api_key="k", model="test-model")
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=ConnectionError("down"))

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client), \
             pytest.raises(ProviderError) as ei:
            async for _ in provider.stream(messages=[ChatMessage(role="user", content="hi")]):
                pass
        assert ei.value.http_status is None

    async def test_midstream_generic_error_detail(self):
        provider = OpenAICompatProvider(name="test", base_url="https://test.api", api_key="k", model="test-model")

        async def _failing_after_first():
            yield _make_chunk("Hi")
            raise RuntimeError("boom")

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_failing_after_first())

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client), \
             pytest.raises(ProviderError, match="LLM stream error: boom"):
            async for _ in provider.stream(messages=[ChatMessage(role="user", content="hi")]):
                pass

    async def test_midstream_read_timeout_distinct_message(self):
        """D1 可观测性：本地假 SSE server 首 token 后静默 > read timeout（monkeypatch
        1s）→ 停流判死文案与其他中途错误可区分，且 ~1s 即判死（非 600s）。"""

        async def _handler(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n\r\n")
                writer.write(f"data: {_CHUNK_JSON}\n\n".encode())
                await writer.drain()
                await reader.read()  # 静默至客户端断连（模拟停流）
            except Exception:  # noqa: BLE001, S110 — 测试假服务器：断连竞态容忍
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        provider = OpenAICompatProvider(
            name="stall", base_url=f"http://127.0.0.1:{port}/v1", api_key="k", model="m"
        )
        tokens: list[str] = []
        t0 = time.monotonic()
        try:
            with patch("llm.providers.openai_compat._READ_TIMEOUT_S", 1.0), \
                 pytest.raises(ProviderError, match="LLM stream timeout"):
                async for t in provider.stream(messages=[ChatMessage(role="user", content="hi")]):
                    tokens.append(t)
        finally:
            server.close()
            await server.wait_closed()
        assert tokens == ["Hi"]
        assert time.monotonic() - t0 < 5.0
