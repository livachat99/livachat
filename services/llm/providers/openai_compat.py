from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from openai import APIStatusError, APITimeoutError, AsyncOpenAI

from llm.base import ChatMessage
from shared.utils.errors import ErrorCode, ProviderError

logger = logging.getLogger("livachat.llm.openai_compat")

_DEFAULT_TIMEOUT_FIRST_TOKEN = 5.0  # seconds
_READ_TIMEOUT_S = 60.0  # 相邻数据块间隔上限（httpx read timeout，含等响应头阶段）

_SDK_TIMEOUT_EXC_NAMES = frozenset(
    {"TimeoutException", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout"}
)


def _error_detail(exc: Exception) -> str:
    """异常摘要：部分 SDK/vendor 异常 str() 为空（实测 httpx2.ReadTimeout），退回类名。"""
    return str(exc) or type(exc).__name__


def _is_stream_timeout(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return True
    # openai 3.1.0 流式 body 迭代中途的超时不经 SDK 包装（_streaming.__stream__ 无
    # except；实测 httpx2.ReadTimeout 直达、str 为空）——按 MRO 类名识别 vendored
    # httpx2 超时，不 import httpx2（避免耦合 vendor 内部包名）。
    return any(c.__name__ in _SDK_TIMEOUT_EXC_NAMES for c in type(exc).__mro__)


class OpenAICompatProvider:
    """Reusable OpenAI-compatible streaming LLM provider.

    Construct with the desired ``base_url``, ``api_key``, and ``model``.
    Set ``supports_vision`` to wire the vision-injection path.

    Each call to :meth:`stream` creates a fresh ``AsyncOpenAI`` client so
    that connection failures surface cleanly.
    """

    name = "openai_compat"

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        supports_vision: bool = False,
        vision_model: str | None = None,
        extra_body: dict[str, object] | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._vision_model = vision_model
        self.supports_vision = supports_vision
        self._extra_body = extra_body
        self._default_headers = default_headers
        self._vision_warned = False

    async def stream(
        self,
        *,
        messages: list[ChatMessage],
        **gen_opts: Any,
    ) -> AsyncIterator[str]:
        client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            default_headers=self._default_headers,
            # read/write/pool=60s（默认 600s 中途停流要挂 10 分钟）；connect 保持 SDK
            # 默认 5s——每轮新建 client，连接失败要快速 failover（fd_pipeline_llm_v2.md）。
            timeout=httpx.Timeout(_READ_TIMEOUT_S, connect=5.0),
        )

        try:
            oai_messages = _build_oai_messages(messages, self)

            model = self._model
            if self.supports_vision and self._vision_model and any(_message_has_images(m) for m in messages):
                model = self._vision_model

            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=oai_messages,  # type: ignore[arg-type]
                    stream=True,
                    extra_body=self._extra_body,
                    **gen_opts,
                )
            except Exception as exc:
                raise ProviderError(
                    f"LLM stream creation failed: {exc}",
                    provider=self.name,
                    code=ErrorCode.LLM_UNAVAILABLE,
                    http_status=exc.status_code if isinstance(exc, APIStatusError) else None,
                ) from exc

            # Skip thinking-mode chunks that have no choices; wait for first real token
            try:
                first = await asyncio.wait_for(stream.__anext__(), timeout=_DEFAULT_TIMEOUT_FIRST_TOKEN)  # type: ignore[union-attr]
                while not first.choices:
                    first = await asyncio.wait_for(stream.__anext__(), timeout=_DEFAULT_TIMEOUT_FIRST_TOKEN)
                delta = first.choices[0].delta
                if delta.content:
                    yield delta.content
            except TimeoutError:
                raise ProviderError(
                    "first token timeout",
                    provider=self.name,
                    code=ErrorCode.LLM_UNAVAILABLE,
                )
            except StopAsyncIteration:
                return

            try:
                async for chunk in stream:  # type: ignore[union-attr]
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield delta.content
            except Exception as exc:
                # 停流判死（read timeout）单独文案：与其它中途错误在日志可区分，
                # 是 60s 阈值回滚的判断信号（fd_pipeline_llm_v2.md 可观测性注记）。
                if _is_stream_timeout(exc):
                    raise ProviderError(
                        f"LLM stream timeout (no chunk within {_READ_TIMEOUT_S:.0f}s): {_error_detail(exc)}",
                        provider=self.name,
                        code=ErrorCode.LLM_UNAVAILABLE,
                    ) from exc
                raise ProviderError(
                    f"LLM stream error: {_error_detail(exc)}",
                    provider=self.name,
                    code=ErrorCode.LLM_UNAVAILABLE,
                    http_status=exc.status_code if isinstance(exc, APIStatusError) else None,
                ) from exc
        finally:
            await client.close()


def _message_has_images(m: ChatMessage) -> bool:
    """OpenAI 规范仅允许 user 消息带 image_url parts——非 user 角色即使带图也不渲染"""
    return m.role == "user" and isinstance(m.images, list) and bool(m.images)


def _build_oai_messages(
    messages: list[ChatMessage],
    provider: OpenAICompatProvider,
) -> list[dict[str, Any]]:
    has_images = any(_message_has_images(m) for m in messages)

    if has_images and not provider.supports_vision:
        if not provider._vision_warned:
            provider._vision_warned = True
            logger.warning(
                "images provided but provider does not support vision, ignoring",
                extra={"provider": provider.name},
            )
        has_images = False

    oai: list[dict[str, Any]] = []
    for m in messages:
        if has_images and _message_has_images(m):
            # 按条目注入：带图 user 消息 content 转 parts；无图消息保持纯字符串（无回归）
            parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
            for url in m.images:
                parts.append({"type": "image_url", "image_url": {"url": url, "detail": "auto"}})
            oai.append({"role": m.role, "content": parts})
        else:
            oai.append({"role": m.role, "content": m.content})

    return oai
