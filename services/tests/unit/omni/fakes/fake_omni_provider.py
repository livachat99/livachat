from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from omni.base import OmniEvent, OmniStream


class FakeOmniStream:
    """Scriptable omni stream that replays a predefined sequence of OmniEvents.

    Set ``inter_event_delay`` to add a sleep between events so external
    actors (e.g. interrupt) have a chance to run mid-stream.
    """

    def __init__(self, events: list[OmniEvent] | None = None, *, inter_event_delay: float = 0.0) -> None:
        self._events = events or []
        self._inter_event_delay = inter_event_delay
        self._fed_audio: list[bytes] = []
        self._fed_images: list[tuple[bytes, int, int]] = []
        self._cancel_count = 0
        self._reset_count = 0
        self._close_count = 0
        self._commit_count = 0

    async def feed_audio(self, pcm: bytes) -> None:
        self._fed_audio.append(pcm)

    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None:
        self._fed_images.append((jpeg, width, height))

    async def commit_user_turn(self) -> None:
        self._commit_count += 1

    async def events(self) -> AsyncIterator[OmniEvent]:
        for ev in self._events:
            yield ev
            if self._inter_event_delay > 0:
                await asyncio.sleep(self._inter_event_delay)
            else:
                await asyncio.sleep(0)

    async def cancel_response(self) -> None:
        self._cancel_count += 1

    async def reset(self) -> None:
        self._reset_count += 1

    async def close(self) -> None:
        self._close_count += 1


class FakeOmniProvider:
    """Provider that returns :class:`FakeOmniStream` instances.

    Set ``raise_on_open`` to simulate a provider that fails to connect.
    """

    def __init__(
        self,
        name: str = "fake_omni",
        events: list[OmniEvent] | None = None,
        *,
        raise_on_open: Exception | None = None,
        supports_vision: bool = False,
    ) -> None:
        self.name = name
        self.supports_vision = supports_vision
        self._events = events or []
        self._raise_on_open = raise_on_open
        self.last_open_kwargs: dict | None = None

    async def open(
        self,
        *,
        sample_rate: int,
        system_prompt: str = "",
        voice: str = "",
        modalities: tuple[str, ...] = ("text", "audio"),
        generation_opts: dict | None = None,
    ) -> OmniStream:
        if self._raise_on_open:
            raise self._raise_on_open
        self.last_open_kwargs = {
            "sample_rate": sample_rate,
            "system_prompt": system_prompt,
            "voice": voice,
            "modalities": modalities,
            "generation_opts": generation_opts,
        }
        return FakeOmniStream(list(self._events))
