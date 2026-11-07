from __future__ import annotations

from llm.providers.openai_compat import OpenAICompatProvider


class DeepSeekProvider(OpenAICompatProvider):
    def __init__(self, base_url: str, api_key: str, model: str, extra_body: dict[str, object] | None = None) -> None:
        super().__init__(
            name="deepseek",
            base_url=base_url,
            api_key=api_key,
            model=model,
            supports_vision=False,
            extra_body=extra_body,
        )
