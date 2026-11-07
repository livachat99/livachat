"""RecordingSink Protocol — the only surface recording exposes to existing code."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, NamedTuple, Protocol


class ChatImageFrame(NamedTuple):
    """AF 快照帧（chatlog 携带 image 的最小载荷）。

    来源为 SessionContext.latest_video_frames 的 7-tuple，构造时丢弃
    frame_id / frame_ts_ms（行内无元数据、文件命名用 uuid7，无消费者）。
    """

    data: bytes
    width: int
    height: int
    orientation: int
    camera_facing: str


class RecordingSink(Protocol):
    """Non-blocking side-car recording interface.

    All on_* methods are synchronous and non-blocking (internal put_nowait).
    Callers must NOT await or try/except them — the sink swallows all errors.
    """

    async def start_for_connection(self, *, device_uuid: str) -> None: ...

    def on_session_start(self, *, chat_session_id: str) -> None:
        """Associate a new chat session with the current recording session."""
        ...

    def on_session_config(self, *, audio_sample_rate: int, timezone: str = "UTC") -> None:
        """Receive audio config from session.start (needed for OpusHead).

        timezone = 客户端上报时区的规范形（timezone-support.md §10）；仅存内存
        （session.json 静态化后不再落盘）。
        """
        ...

    def on_media(
        self,
        *,
        kind: Literal["audio", "video"],
        data: bytes,
        duration_ms: int = 0,
        ts_ms: int,
    ) -> None:
        """Non-blocking. data = raw Opus packet or JPEG bytes (already base64-decoded)."""
        ...

    def on_chat_text(
        self,
        *,
        role: Literal["user", "assistant"],
        text: str,
        ts_ms: int,
        ref_id: str = "",
        chat_session_id: str = "",
        interrupted: bool = False,
        source: str | None = None,
        frames: Sequence[ChatImageFrame] | None = None,
        agent_name: str = "",
        agent_id: str = "",
        job_key: str = "",
    ) -> None:
        """Non-blocking. ASR final / video 文本轮（role=user）或 LLM full_text。

        frames：快照的挂图帧（全量落 image/<uuid7>.jpg，行内 images 引用）——AF 时刻或
        video 文本回合（docs/android_screen_recording.md §5.2）；
        None/空 = 无图（模式不适用、无帧、或非 user 行）。
        agent_name/agent_id/job_key：仅 agent job 结果行（source="agent_job"）携带，
        落行供 lvdata 铸造 agent_job 事件（recording_data_process.md §2.5）。
        """
        ...

    def on_transcribe_text(
        self,
        *,
        text: str,
        ts_ms: int,
        ref_id: str = "",
        chat_session_id: str = "",
    ) -> None:
        """Non-blocking. Transcribe-mode ASR final (written to chat/transcribe.jsonl)."""
        ...

    async def on_connection_closed(self) -> None:
        """Flush and suspend writer; same-day reconnection reuses the session."""
        ...


class NoOpRecordingSink:
    """Zero-overhead sink when recording is disabled."""

    async def start_for_connection(self, *, device_uuid: str) -> None:
        pass

    def on_session_start(self, *, chat_session_id: str) -> None:
        pass

    def on_session_config(self, *, audio_sample_rate: int, timezone: str = "UTC") -> None:
        pass

    def on_media(
        self, *, kind: Literal["audio", "video"], data: bytes, duration_ms: int = 0, ts_ms: int = 0
    ) -> None:
        pass

    def on_chat_text(
        self, *, role: Literal["user", "assistant"], text: str, ts_ms: int = 0,
        ref_id: str = "", chat_session_id: str = "", interrupted: bool = False,
        source: str | None = None, frames: Sequence[ChatImageFrame] | None = None,
        agent_name: str = "", agent_id: str = "", job_key: str = "",
    ) -> None:
        pass

    def on_transcribe_text(
        self, *, text: str, ts_ms: int = 0,
        ref_id: str = "", chat_session_id: str = "",
    ) -> None:
        pass

    async def on_connection_closed(self) -> None:
        pass
