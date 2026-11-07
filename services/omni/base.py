from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class OmniEventType(StrEnum):
    INPUT_TRANSCRIPTION_DELTA = "input_transcription_delta"
    INPUT_TRANSCRIPTION_DONE = "input_transcription_done"
    SPEECH_STARTED = "speech_started"
    SPEECH_STOPPED = "speech_stopped"
    RESPONSE_STARTED = "response_started"
    TEXT_DELTA = "text_delta"
    AUDIO_DELTA = "audio_delta"
    RESPONSE_DONE = "response_done"
    ERROR = "error"


@dataclass
class OmniEvent:
    type: OmniEventType
    text: str = ""
    audio: bytes = b""
    is_final: bool = False
    response_id: str = ""
    error_message: str = ""


class OmniStream(Protocol):
    async def feed_audio(self, pcm: bytes) -> None: ...
    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None: ...
    async def commit_user_turn(self) -> None: ...
    def events(self) -> AsyncIterator[OmniEvent]: ...
    async def cancel_response(self) -> None: ...
    async def reset(self) -> None: ...
    async def close(self) -> None: ...


class OmniProvider(Protocol):
    name: str
    supports_vision: bool

    async def open(
        self,
        *,
        sample_rate: int,
        system_prompt: str = "",
        voice: str = "",
        modalities: tuple[str, ...] = ("text", "audio"),
        generation_opts: dict | None = None,
    ) -> OmniStream: ...
