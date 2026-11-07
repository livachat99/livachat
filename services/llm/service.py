from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator

from llm.base import ChatMessage, LlmProvider
from shared.utils.errors import ErrorCode, ProviderError

logger = logging.getLogger("livachat.llm.service")


async def _aclose_gen(it: object) -> None:
    """Safely close an async generator, swallowing all exceptions."""
    if it is not None and inspect.isasyncgen(it):
        try:
            await it.aclose()
        except Exception:
            pass


class LlmService:
    """Failover-aware LLM service.

    On :meth:`stream`, tries the active provider first.  Failures **before the
    first token is yielded** fall over to the next provider.  After the first
    token has been yielded there is **no mid-stream failover** — the error is
    wrapped and propagated（中途切换会把 A 的残句与 B 的全文拼接下发，
    见 docs/fd_pipeline_llm_v2.md）。
    """

    def __init__(self, active: LlmProvider, fallbacks: list[LlmProvider]) -> None:
        self._providers = [active] + fallbacks

    async def stream(
        self,
        *,
        messages: list[ChatMessage],
        **gen_opts: object,
    ) -> AsyncIterator[str]:
        last_err: Exception | None = None

        for idx, provider in enumerate(self._providers):
            it: object = None
            first_yielded = False
            try:
                stream = provider.stream(messages=messages, **gen_opts)
                # The provider returns an async generator — get it directly or await it.
                if hasattr(stream, "__anext__"):
                    it = stream
                else:
                    it = await stream  # type: ignore[assignment]
                # Peek at the first token — if this fails we try the next provider.
                first = await it.__anext__()  # type: ignore[union-attr]
                first_yielded = True
                yield first
                # First token succeeded — continue yielding from this provider.
                async for token in it:  # type: ignore[attr-defined,union-attr]
                    yield token
                return  # stream completed normally
            except StopAsyncIteration:
                return
            except Exception as exc:
                # ProviderError ⊂ Exception；failover 判据只看 first_yielded。
                if not first_yielded and idx < len(self._providers) - 1:
                    logger.warning(
                        "llm provider failed before first token, trying next",
                        extra={"provider": provider.name, "error": str(exc)},
                    )
                    last_err = exc
                    await _aclose_gen(it)
                    continue
                # 中途失败（或末位 provider 首 token 前失败）：不 failover，包装后
                # 直接上抛；_aclose_gen 释放 provider generator 及其 http client
                # （orchestrator 的 finally 只关 service 层 generator）。
                await _aclose_gen(it)
                http_status = getattr(exc, "http_status", None)
                if first_yielded:
                    logger.warning(
                        "llm stream failed mid-stream, propagating (no failover)",
                        extra={"provider": provider.name, "error": str(exc)},
                    )
                    raise ProviderError(
                        f"LLM stream failed mid-stream ({provider.name}): {exc}",
                        provider=provider.name,
                        code=ErrorCode.LLM_UNAVAILABLE,
                        http_status=http_status,
                    ) from exc
                if isinstance(exc, ProviderError):
                    raise ProviderError(
                        f"all LLM providers failed: {exc}",
                        provider="llm",
                        code=ErrorCode.LLM_UNAVAILABLE,
                        http_status=http_status,
                    ) from exc
                raise ProviderError(
                    str(exc),
                    provider=provider.name,
                    code=ErrorCode.LLM_UNAVAILABLE,
                    http_status=http_status,
                ) from exc

        # 防御路径：providers 为空时可达（非空列表的末位迭代必在循环内 return/raise）。
        # 保留不删：删除会让空列表从防御性报错变为静默正常结束（空 final），恰是本
        # 设计要消灭的不可区分故障（fd_pipeline_llm_v2.md 实施注记）。
        raise ProviderError(
            f"all LLM providers failed: {last_err}",
            provider="llm",
            code=ErrorCode.LLM_UNAVAILABLE,
        ) from last_err
