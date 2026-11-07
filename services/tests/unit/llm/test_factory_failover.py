from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.base import ChatMessage
from llm.factory import create_llm
from llm.providers.alibaba_bailian import AlibabaBailianLLMProvider
from llm.providers.openai_compat import OpenAICompatProvider
from llm.service import LlmService
from shared.config.models import LlmConfig, LlmProviderConfig
from shared.utils.errors import ConfigError, ProviderError


def _make_llm_config(active: str, providers: dict) -> LlmConfig:
    return LlmConfig(active=active, fallback=[], providers=providers)


def _make_chunk(content: str) -> MagicMock:
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = content
    return chunk


async def _ok_stream():
    yield _make_chunk("ok")


async def _fallback_stream():
    yield _make_chunk("from fallback")


class TestFactory:
    def test_create_bailian(self):
        cfg = _make_llm_config(
            "alibaba_bailian",
            {
                "alibaba_bailian": LlmProviderConfig(
                    enabled=True, base_url="https://x", api_key="k", model="qwen-plus", supports_vision=True
                )
            },
        )
        svc = create_llm(cfg)
        assert isinstance(svc, LlmService)

    def test_create_bailian_passes_workspace_id(self):
        cfg = _make_llm_config(
            "alibaba_bailian",
            {
                "alibaba_bailian": LlmProviderConfig(
                    enabled=True, base_url="https://ws.example", api_key="k",
                    model="qwen3.8-max-0902", workspace_id="llm-1tmsqifo51cwfadr",
                )
            },
        )
        svc = create_llm(cfg)
        provider = svc._providers[0]
        assert provider._default_headers == {"X-DashScope-WorkSpace": "llm-1tmsqifo51cwfadr"}

    def test_create_bailian_without_workspace_sends_no_header(self):
        cfg = _make_llm_config(
            "alibaba_bailian",
            {"alibaba_bailian": LlmProviderConfig(enabled=True, base_url="https://x", api_key="k", model="m")},
        )
        svc = create_llm(cfg)
        assert svc._providers[0]._default_headers is None

    def test_unknown_provider_raises(self):
        cfg = _make_llm_config("foo", {})
        with pytest.raises(ConfigError):
            create_llm(cfg)


class TestFailover:
    async def test_active_succeeds(self):
        provider = OpenAICompatProvider(name="p1", base_url="x", api_key="k", model="m")
        svc = LlmService(active=provider, fallbacks=[])

        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_ok_stream())

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client):
            tokens = []
            async for t in svc.stream(messages=[ChatMessage(role="user", content="hi")]):
                tokens.append(t)
            assert tokens == ["ok"]

    async def test_first_provider_fails_fallback_wins(self):
        bad = OpenAICompatProvider(name="bad", base_url="x", api_key="k", model="m")
        good = OpenAICompatProvider(name="good", base_url="x", api_key="k", model="m")
        svc = LlmService(active=bad, fallbacks=[good])

        mock_bad = MagicMock()
        mock_bad.close = AsyncMock()
        mock_bad.chat.completions.create = AsyncMock(side_effect=Exception("down"))
        mock_good = MagicMock()
        mock_good.close = AsyncMock()
        mock_good.chat.completions.create = AsyncMock(return_value=_fallback_stream())

        with patch("llm.providers.openai_compat.AsyncOpenAI") as mock_cls:
            mock_cls.side_effect = [mock_bad, mock_good]
            tokens = []
            async for t in svc.stream(messages=[ChatMessage(role="user", content="hi")]):
                tokens.append(t)
            assert tokens == ["from fallback"]

    async def test_all_fail_raises(self):
        """Both providers throw ProviderError on first token → service raises."""

        class _FailingIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise ProviderError("down", provider="fail", code=1003)

        class _FailingProvider:
            name = "fail"
            supports_vision = False

            async def stream(self, *, messages, images=None, **gen_opts):
                return _FailingIter()

        svc = LlmService(active=_FailingProvider(), fallbacks=[_FailingProvider()])
        with pytest.raises(ProviderError):
            async for _ in svc.stream(messages=[ChatMessage(role="user", content="hi")]):
                pass


class TestWorkspaceHeader:
    async def _run_once(self, provider: AlibabaBailianLLMProvider):
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_ok_stream())

        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client) as mock_cls:
            tokens = []
            async for t in provider.stream(messages=[ChatMessage(role="user", content="hi")]):
                tokens.append(t)
        assert tokens == ["ok"]
        return mock_cls

    async def test_workspace_header_passed_to_client(self):
        provider = AlibabaBailianLLMProvider(
            base_url="https://llm-1tmsqifo51cwfadr.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            api_key="k",
            model="qwen3.8-max-0902",
            workspace_id="llm-1tmsqifo51cwfadr",
        )
        mock_cls = await self._run_once(provider)
        assert mock_cls.call_args.kwargs["default_headers"] == {
            "X-DashScope-WorkSpace": "llm-1tmsqifo51cwfadr"
        }

    async def test_no_workspace_no_header(self):
        provider = AlibabaBailianLLMProvider(base_url="https://x", api_key="k", model="m")
        mock_cls = await self._run_once(provider)
        assert mock_cls.call_args.kwargs["default_headers"] is None


class _MidFailProvider:
    """首 token 后抛错（async generator 形状，与生产 OpenAICompatProvider 一致）。"""

    name = "mid_fail"
    supports_vision = False

    def __init__(self) -> None:
        self.closed = False

    async def stream(self, *, messages, **gen_opts):
        try:
            yield "partial"
            raise ProviderError("boom", provider="mid_fail")
        finally:
            self.closed = True


class _CountingProvider:
    name = "good"
    supports_vision = False

    def __init__(self) -> None:
        self.called = False

    async def stream(self, *, messages, **gen_opts):
        self.called = True
        yield "from fallback"


class TestMidStreamFailover:
    async def test_midstream_failure_no_failover(self):
        """D2：首 token 后失败**不切换** provider（此前会静默切换产生 A 残句+B 全文拼接）。"""
        bad, good = _MidFailProvider(), _CountingProvider()
        svc = LlmService(active=bad, fallbacks=[good])

        tokens: list[str] = []
        with pytest.raises(ProviderError, match="mid-stream"):
            async for t in svc.stream(messages=[ChatMessage(role="user", content="hi")]):
                tokens.append(t)

        assert tokens == ["partial"]      # 只消费到 A 的残句
        assert good.called is False       # 未切换
        assert bad.closed is True         # provider generator 已终止、finally 执行（client 释放）

    async def test_http_status_propagates_pre_first_token(self):
        class _StatusFail:
            name = "s401"
            supports_vision = False

            async def stream(self, *, messages, **gen_opts):
                raise ProviderError("Error code: 401 - bad key", provider="s401", http_status=401)
                yield  # pragma: no cover

        svc = LlmService(active=_StatusFail(), fallbacks=[])
        with pytest.raises(ProviderError) as ei:
            async for _ in svc.stream(messages=[ChatMessage(role="user", content="hi")]):
                pass
        assert ei.value.http_status == 401

    async def test_http_status_propagates_midstream(self):
        class _MidStatusFail:
            name = "m401"
            supports_vision = False

            async def stream(self, *, messages, **gen_opts):
                yield "partial"
                raise ProviderError("Error code: 401 - mid", provider="m401", http_status=401)

        svc = LlmService(active=_MidStatusFail(), fallbacks=[])
        with pytest.raises(ProviderError) as ei:
            async for _ in svc.stream(messages=[ChatMessage(role="user", content="hi")]):
                pass
        assert ei.value.http_status == 401
