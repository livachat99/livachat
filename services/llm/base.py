from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str
    images: list[str] | None = None  # data_url 列表；mime 已在 "data:image/jpeg;base64," 前缀中


class LlmProvider(Protocol):
    """Async streaming LLM interface.

    Each provider wraps an OpenAI-compatible chat completions endpoint.
    """

    name: str
    supports_vision: bool

    async def stream(
        self,
        *,
        messages: list[ChatMessage],
        **gen_opts: object,
    ) -> AsyncIterator[str]:
        """Yield incremental text tokens.  Never yield empty strings."""
        ...
