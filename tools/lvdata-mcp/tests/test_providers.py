"""Provider 异常映射（2026-08-29：SSE error 事件抛 base APIError 曾逃逸
崩掉整个分析 stage、files 不计数 → 无限重试）。"""

from __future__ import annotations

import asyncio

import openai
import pytest

from lvdata_mcp.config import ProviderConfig
from lvdata_mcp.providers import ApiError, LlmClient, RetryableError


class _Req:
    url = "http://x"


def _sse_error(message: str) -> openai.APIError:
    """模拟 openai SDK 3.x 对 SSE error 事件抛的 base APIError。"""
    return openai.APIError(message=message, request=_Req(), body=None)


class _FailStream:
    def __init__(self, exc):
        self.exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self.exc


class _FakeClient:
    def __init__(self, exc):
        self.exc = exc
        self.creates = 0

    class _Completions:
        def __init__(self, owner):
            self.owner = owner

        async def create(self, **kwargs):
            self.owner.creates += 1
            return _FailStream(self.owner.exc)

    @property
    def chat(self):
        return type(self)._Chat(self)

    class _Chat:
        def __init__(self, owner):
            self.completions = owner._Completions(owner)


def _client(exc, max_attempts: int = 3) -> LlmClient:
    provider = type("P", (), {"vendor": "dashscope", "api_key": "x",
                              "base_url": "http://x", "model": "m",
                              "workspace_id": None,
                              "resolved": lambda self, k: self})()
    return LlmClient(provider, 30, max_attempts=max_attempts,
                     client=_FakeClient(exc))


def test_sse_error_400_maps_to_api_error_no_retry():
    """dashscope 空音频 400（消息内嵌 <400>）→ ApiError(status=400)，
    立即失败不重试（audio 侧走失败计数/退避）。"""
    c = _client(_sse_error("<400> InternalError.Algo.InvalidParameter: "
                           "The audio is empty"))
    with pytest.raises(ApiError) as ei:
        asyncio.run(c.chat("s", [{"type": "text", "text": "u"}]))
    assert ei.value.status == 400
    assert c._client.creates == 1  # 4xx 不重试


def test_sse_error_500_retries_then_api_error():
    """SSE error 内嵌 <500> → RetryableError 重试，耗尽后 ApiError。"""
    c = _client(_sse_error("<500> InternalError"), max_attempts=3)
    with pytest.raises(ApiError) as ei:
        asyncio.run(c.chat("s", [{"type": "text", "text": "u"}]))
    assert ei.value.status is None
    assert c._client.creates == 3  # 重试 3 次


def test_workspace_id_sets_dashscope_header():
    """workspace_id → X-DashScope-WorkSpace 头（业务空间专属域名配套，
    与 chat server 的 asr/llm/tts 同一约定）。"""
    provider = ProviderConfig(vendor="dashscope", base_url="http://x",
                              api_key="k", model="m", workspace_id="llm-x")
    c = LlmClient(provider, 30)
    assert c._client.default_headers["X-DashScope-WorkSpace"] == "llm-x"


def test_no_workspace_no_header():
    """未配置 workspace_id → 不带头（跨业务空间 Key 场景不受影响）。"""
    provider = ProviderConfig(vendor="dashscope", base_url="http://x",
                              api_key="k", model="m")
    c = LlmClient(provider, 30)
    assert "X-DashScope-WorkSpace" not in c._client.default_headers


def test_workspace_id_ignored_for_non_dashscope_vendor():
    """workspace_id 仅 DashScope 语义：非 dashscope vendor 不携带该头
    （server 侧由 bailian 专属 provider 类承载，此处用 vendor 判定等价隔离）。"""
    provider = ProviderConfig(vendor="openai", base_url="http://x",
                              api_key="k", model="m", workspace_id="llm-x")
    c = LlmClient(provider, 30)
    assert "X-DashScope-WorkSpace" not in c._client.default_headers


def test_dashscope_headers_three_states():
    """头构造唯一判定点（LlmClient 与 embedding provider 共用，防规则漂移）。"""
    from lvdata_mcp.providers import dashscope_headers
    assert dashscope_headers("dashscope", "llm-x") == \
        {"X-DashScope-WorkSpace": "llm-x"}
    assert dashscope_headers("dashscope", None) is None
    assert dashscope_headers("openai", "llm-x") is None
