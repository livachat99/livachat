"""Abstract base for external agent adapters.

See §4.2 of the design doc for the interface specification.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class AgentContext:
    """Context information passed to an adapter."""

    session_id: str
    device_id: str
    language: str
    # 设备本机时区（session.start 上报的 canonical 值；未上报 = ""）——
    # 渲染进 mission/background prompt，供 agent 传给 lvdata MCP 的 timezone 参数
    # （docs/agent_relay_prompts.md §3.2）
    timezone: str = ""


@dataclass
class AgentResponseChunk:
    """A fragment of an agent's response."""

    text: str
    is_final: bool = False
    metadata: dict | None = None


@dataclass
class RecordingInfo:
    """Recording metadata passed to an adapter for analysis."""

    recording_id: str
    session_id: str
    device_id: str
    manifest_path: str
    duration_ms: int
    transcript_endpoint: str


@dataclass
class ChatlogLine:
    """A single line from the chatlog file."""

    role: str  # "user" | "assistant"
    text: str
    ts_ms: int


@dataclass
class ChatlogUpdate:
    """Incremental chatlog growth passed to adapter.nudge()."""

    path: str
    chat_session_id: str
    new_lines: list[ChatlogLine]
    truncated: bool = False
    # In-flight jobs from JobStore: [{job_key, task_id, summary, agent_name}]
    outstanding_jobs: list[dict] = field(default_factory=list)


class AgentAdapter(ABC):
    """Abstract adapter for an external agent.

    Each adapter encapsulates the communication protocol (HTTP/SSE/gRPC/...)
    of one external agent. The relay calls through this uniform interface
    regardless of the underlying transport.
    """

    # Instance flags derived from config.mode — determine which event paths
    # the session manager fans out to this adapter.
    reactive_enabled: bool = True
    proactive_enabled: bool = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter identifier, used as agent_name in ai_text messages."""
        ...

    @property
    def nickname(self) -> str:
        """User-facing name from adapter config; empty = not configured."""
        return ""

    async def connect(self) -> None:
        """Establish a long-lived connection (optional)."""

    async def close(self) -> None:
        """Tear down the connection and release resources."""

    @abstractmethod
    async def ask(
        self,
        text: str,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Send a user utterance and stream back the agent's response.

        Reactive scenario: triggered on every ASR final.
        Returns an async iterator to support both streaming (SSE) and
        batch response patterns.
        """
        ...

    async def analyze_recording(
        self,
        recording: RecordingInfo,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Analyze a completed recording segment.

        Proactive scenario: triggered on recording.ready events.
        Default implementation is a no-op (return without yielding).
        Subclasses override to provide analysis capability.
        """
        return
        yield  # type: ignore[unreachable]

    # -- proactive lifecycle (v2) --------------------------------------------

    async def on_session_start(self, context: AgentContext) -> None:
        """Called when a LivaChat session starts (session.create on agent side).

        Default implementation is a no-op.
        """

    async def on_session_end(self, context: AgentContext) -> None:
        """Called when a LivaChat session ends (session.close on agent side).

        Default implementation is a no-op.
        """

    async def nudge(
        self,
        update: ChatlogUpdate,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Notify the adapter of incremental chatlog growth.

        The adapter decides whether the agent needs to act; any yielded
        chunks are pushed back to the user as proactive messages.
        Default implementation is a no-op (return without yielding).
        """
        return
        yield  # type: ignore[unreachable]

    def set_push_callback(
        self,
        cb: Callable[[str, str, AgentResponseChunk], Awaitable[None]],
    ) -> None:
        """Register a callback for out-of-band agent pushes.

        Used for background job completions that arrive on the long-lived
        connection outside of any nudge()/ask() turn. The callback receives
        (device_id, session_id, chunk). Adapters must await the callback
        (not fire and forget) so the per-device completion-order guarantee
        holds.
        """
        self._push_callback = cb
