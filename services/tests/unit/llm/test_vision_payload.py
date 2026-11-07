from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from llm.base import ChatMessage
from llm.providers.openai_compat import OpenAICompatProvider, _build_oai_messages


def _provider(*, supports_vision: bool = True, vision_model: str | None = None) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name="t", base_url="x", api_key="k", model="m",
        supports_vision=supports_vision, vision_model=vision_model,
    )


class TestVisionPayload:
    def test_build_messages_without_images(self):
        msgs = [ChatMessage(role="user", content="hello")]
        provider = _provider()
        oai = _build_oai_messages(msgs, provider)
        assert oai == [{"role": "user", "content": "hello"}]

    def test_single_user_message_multiple_images(self):
        msgs = [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="What do you see?", images=[
                "data:image/jpeg;base64,img1",
                "data:image/jpeg;base64,img2",
            ]),
        ]
        provider = _provider()
        oai = _build_oai_messages(msgs, provider)

        # System message unchanged
        assert oai[0] == {"role": "system", "content": "You are helpful."}
        # User message with images → text + image_url parts
        user_content = oai[1]["content"]
        assert isinstance(user_content, list)
        assert len(user_content) == 3
        assert user_content[0] == {"type": "text", "text": "What do you see?"}
        assert user_content[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,img1", "detail": "auto"}}
        assert user_content[2] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,img2", "detail": "auto"}}

    def test_mixed_user_messages_rendered_individually(self):
        """History-image regression: one user message with images, another plain —
        each renders correctly (plain text stays a plain string, not parts)."""
        msgs = [
            ChatMessage(role="user", content="previous with image", images=["data:image/jpeg;base64,old"]),
            ChatMessage(role="assistant", content="previous answer"),
            ChatMessage(role="user", content="current question"),
        ]
        provider = _provider()
        oai = _build_oai_messages(msgs, provider)

        assert oai[0]["content"] == [{"type": "text", "text": "previous with image"},
                                     {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,old", "detail": "auto"}}]
        assert oai[1] == {"role": "assistant", "content": "previous answer"}
        assert oai[2] == {"role": "user", "content": "current question"}

    def test_images_ignored_when_no_vision(self):
        msgs = [ChatMessage(role="user", content="hi", images=["data:image/jpeg;base64,xyz"])]
        provider = _provider(supports_vision=False)
        oai = _build_oai_messages(msgs, provider)
        assert oai == [{"role": "user", "content": "hi"}]

    def test_non_list_images_rendered_as_plain_text(self):
        """Corrupt non-list images must not produce garbage parts."""
        msgs = [ChatMessage(role="user", content="hi", images="data:image/jpeg;base64,xyz")]
        provider = _provider(supports_vision=True)
        oai = _build_oai_messages(msgs, provider)
        assert oai == [{"role": "user", "content": "hi"}]

    def test_non_user_images_rendered_as_plain_text(self):
        """OpenAI only allows image_url parts on user messages — an assistant entry
        carrying images (e.g. future multimodal replies in history) must stay plain,
        otherwise the provider rejects the request with 400."""
        msgs = [
            ChatMessage(role="assistant", content="a photo", images=["data:image/jpeg;base64,xyz"]),
            ChatMessage(role="user", content="what is this"),
        ]
        provider = _provider(supports_vision=True)
        oai = _build_oai_messages(msgs, provider)
        assert oai[0] == {"role": "assistant", "content": "a photo"}
        assert oai[1] == {"role": "user", "content": "what is this"}

    def test_warning_only_once_when_no_vision(self, caplog):
        """Consecutive requests with images → warning fires once (_vision_warned)."""
        msgs = [ChatMessage(role="user", content="hi", images=["data:image/jpeg;base64,xyz"])]
        provider = _provider(supports_vision=False)
        with caplog.at_level("WARNING", logger="livachat.llm.openai_compat"):
            _build_oai_messages(msgs, provider)
            _build_oai_messages(msgs, provider)
        warnings = [r for r in caplog.records if "does not support vision" in r.getMessage()]
        assert len(warnings) == 1

    def test_no_vision_model_switch_when_no_vision(self):
        """supports_vision=False + vision_model configured → model NOT switched
        (locks the gate added in fd_pipeline_llm_v2)."""
        msgs = [ChatMessage(role="user", content="hi", images=["data:image/jpeg;base64,xyz"])]
        provider = _provider(supports_vision=False, vision_model="vision-model")
        oai = _build_oai_messages(msgs, provider)
        assert oai == [{"role": "user", "content": "hi"}]


def _make_chunk(content: str | None) -> MagicMock:
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = content
    return chunk


async def _async_iter(chunks: list) -> MagicMock:
    for c in chunks:
        yield c


class TestVisionModelSelection:
    """Lock model switching in stream(): only when supports_vision + vision_model
    + at least one message carries images (fd_pipeline_llm_v2)."""

    async def _run_stream(self, msgs: list[ChatMessage], *, supports_vision: bool, vision_model: str | None):
        provider = _provider(supports_vision=supports_vision, vision_model=vision_model)
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_async_iter([_make_chunk("ok")]))
        with patch("llm.providers.openai_compat.AsyncOpenAI", return_value=mock_client):
            async for _ in provider.stream(messages=msgs):
                pass
        return mock_client.chat.completions.create.call_args.kwargs["model"]

    async def test_switches_to_vision_model_with_images(self):
        msgs = [ChatMessage(role="user", content="what", images=["data:image/jpeg;base64,x"])]
        model = await self._run_stream(msgs, supports_vision=True, vision_model="vision-model")
        assert model == "vision-model"

    async def test_keeps_text_model_without_images(self):
        msgs = [ChatMessage(role="user", content="hi")]
        model = await self._run_stream(msgs, supports_vision=True, vision_model="vision-model")
        assert model == "m"

    async def test_keeps_text_model_when_no_vision(self):
        msgs = [ChatMessage(role="user", content="hi", images=["data:image/jpeg;base64,x"])]
        model = await self._run_stream(msgs, supports_vision=False, vision_model="vision-model")
        assert model == "m"

    async def test_keeps_text_model_when_only_non_user_has_images(self):
        """Assistant-carried images must not trigger the vision model either —
        same user-only rule as rendering."""
        msgs = [ChatMessage(role="assistant", content="hi", images=["data:image/jpeg;base64,x"])]
        model = await self._run_stream(msgs, supports_vision=True, vision_model="vision-model")
        assert model == "m"
