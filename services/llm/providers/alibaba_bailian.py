from __future__ import annotations

from llm.providers.openai_compat import OpenAICompatProvider


class AlibabaBailianLLMProvider(OpenAICompatProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        vision_model: str | None = None,
        extra_body: dict[str, object] | None = None,
        workspace_id: str | None = None,
    ) -> None:
        # workspace_id 存在时附加 X-DashScope-WorkSpace 头（业务空间专属域名配套，
        # 与 asr/tts bailian 同一约定）；Authorization 由 SDK 从 api_key 生成
        headers = {"X-DashScope-WorkSpace": workspace_id} if workspace_id else None
        super().__init__(
            name="alibaba_bailian",
            base_url=base_url,
            api_key=api_key,
            model=model,
            supports_vision=True,
            vision_model=vision_model,
            extra_body=extra_body,
            default_headers=headers,
        )
