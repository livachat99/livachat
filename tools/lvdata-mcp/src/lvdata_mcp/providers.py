"""LLM provider 客户端（§2.2/§4.3）：openai-compatible、streaming、json_object。

重试策略：429/超时/连接/5xx → 指数退避 `2^n + rand(0,1)`，max 3 次；
4xx（非 400 降级场景）→ ApiError（调用方按语义处理：audio ogg→wav 降级 /
vision enable_thinking 降级）。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

import openai
import structlog
from openai import AsyncOpenAI

from .config import ProviderConfig

log = structlog.get_logger("lvdata_mcp.providers")

MAX_ATTEMPTS = 3

_EMBEDDED_STATUS_RE = re.compile(r"<(\d{3})>")


def _parse_embedded_status(message: str) -> int | None:
    """SSE error 事件消息内嵌状态码（dashscope "<400> xxx"）→ int，无则 None。"""
    m = _EMBEDDED_STATUS_RE.search(message)
    return int(m.group(1)) if m else None


class ApiError(Exception):
    """Non-retryable API failure（400 等）；status 供降级判定。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RetryableError(Exception):
    """Transient API failure worth retrying（429 / 5xx / timeout / conn）。"""


def sanitize_json(content: str) -> str:
    """去 markdown code fence（模型偶尔包裹 JSON）。"""
    s = content.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else ""
    if s.endswith("```"):
        s = s.rsplit("\n", 1)[0]
    return s.strip()


def parse_json(content: str) -> dict:
    parsed = json.loads(sanitize_json(content))
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("model output is not a JSON object", content, 0)
    return parsed


def dashscope_headers(vendor: str, workspace_id: str | None) -> dict[str, str] | None:
    """X-DashScope-WorkSpace 头构造（唯一判定点）：仅 DashScope 语义。

    业务空间专属域名与 workspace_id 两值配套、互不派生（约定同 chat server 的
    asr/llm/tts）；未配置/非 DashScope 不带头。LlmClient 与 vec.py 的 embedding
    provider 共用——规则单点，防两处漂移。
    """
    if workspace_id and vendor == "dashscope":
        return {"X-DashScope-WorkSpace": workspace_id}
    return None


class LlmClient:
    def __init__(self, provider: ProviderConfig, timeout_s: float,
                 max_attempts: int = MAX_ATTEMPTS,
                 client: AsyncOpenAI | None = None):
        self.provider = provider
        self.model = provider.model
        self.max_attempts = max_attempts
        headers = dashscope_headers(provider.vendor, provider.workspace_id)
        self._client = client or AsyncOpenAI(
            api_key=provider.api_key, base_url=provider.base_url,
            timeout=timeout_s, default_headers=headers)

    async def chat(self, system_prompt: str, user_content: list,
                   *, enable_thinking: bool | None = None) -> tuple[str, dict | None]:
        """单次调用 + 瞬态错误重试；返回 (content, usage)。"""
        last_exc: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return await self._call(system_prompt, user_content,
                                        enable_thinking)
            except RetryableError as e:
                last_exc = e
                if attempt < self.max_attempts - 1:
                    delay = 2 ** attempt + random.uniform(0, 1)
                    log.warning("llm retry", model=self.model,
                                attempt=attempt + 1,
                                max_attempts=self.max_attempts,
                                error=str(e)[:300], sleep_s=round(delay, 1))
                    await asyncio.sleep(delay)
        raise ApiError(f"failed after {self.max_attempts} attempts: {last_exc}")

    async def _call(self, system_prompt: str, user_content: list,
                    enable_thinking: bool | None) -> tuple[str, dict | None]:
        kwargs: dict[str, Any] = {}
        if enable_thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": enable_thinking}
        if self.provider.vendor == "dashscope":
            # 百炼兼容端点要求 modalities；其余 vendor 可能 400（§14.4 本期
            # 不升级 vendor 抽象，仅在此收敛）
            kwargs["modalities"] = ["text"]
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                stream=True,
                stream_options={"include_usage": True},
                response_format={"type": "json_object"},
                **kwargs,
            )
        except openai.RateLimitError as e:
            raise RetryableError(f"rate limited: {e}") from e
        except openai.APITimeoutError as e:
            raise RetryableError(f"timeout: {e}") from e
        except openai.APIConnectionError as e:
            raise RetryableError(f"connection error: {e}") from e
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                raise RetryableError(f"server error {e.status_code}") from e
            raise ApiError(f"API error {e.status_code}: {e}",
                           status=e.status_code) from e
        except openai.APIError as e:
            # dashscope 的 4xx 以 SSE error 事件在 HTTP 200 响应体内到达，
            # openai SDK 3.x 对这类事件抛 base APIError（非 APIStatusError，
            # _streaming.py）。漏捕获会崩掉整个分析 stage、files 不计数 →
            # 无限重试（2026-08-29 空音频 400 事故）。
            status = _parse_embedded_status(str(e))
            if status is not None and status >= 500:
                raise RetryableError(f"server error {status}: {e}") from e
            raise ApiError(f"API error: {e}", status=status) from e

        content = ""
        usage = None
        # streaming 迭代中的服务端错误（如 dashscope 50002）以 APIStatusError 抛出，
        # 未捕获会绕过重试/失败计数直接崩掉整个 stage（2026-08-28 事故根因）。
        # 与 create() 的映射保持一致；RateLimitError 是 APIStatusError 子类，须先捕获。
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta \
                        and chunk.choices[0].delta.content:
                    content += chunk.choices[0].delta.content
                if chunk.usage:
                    usage = chunk.usage
        except openai.RateLimitError as e:
            raise RetryableError(f"rate limited: {e}") from e
        except openai.APITimeoutError as e:
            raise RetryableError(f"timeout: {e}") from e
        except openai.APIConnectionError as e:
            raise RetryableError(f"connection error: {e}") from e
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                raise RetryableError(f"server error {e.status_code}") from e
            raise ApiError(f"API error {e.status_code}: {e}",
                           status=e.status_code) from e
        except openai.APIError as e:
            # 同 create() 的 base APIError 映射（SSE error 事件，见上）。
            status = _parse_embedded_status(str(e))
            if status is not None and status >= 500:
                raise RetryableError(f"server error {status}: {e}") from e
            raise ApiError(f"API error: {e}", status=status) from e
        if usage is not None:
            usage = {"input_tokens": usage.prompt_tokens,
                     "output_tokens": usage.completion_tokens}
        return content, usage
