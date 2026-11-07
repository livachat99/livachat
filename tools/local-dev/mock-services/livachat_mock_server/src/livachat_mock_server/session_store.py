from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from livachat_protocol.generated import SessionStatusCode

from .config import Scenario


@dataclass
class SessionContext:
    session_id: str
    device_id: str
    scenario: Scenario
    c2s_seq: int = 0
    s2c_seq: int = 0
    session_status: SessionStatusCode = SessionStatusCode.IDLE
    connected_at: float = field(default_factory=time.monotonic)
    last_msg_at: float = field(default_factory=time.monotonic)
    streaming_task: asyncio.Task[object] | None = None
    ack_frame_id: int = 0
    silence_count: int = 0
    silence_threshold_frames: int = 30
    transcript_text: str = ""
    recognised: bool = False
    interrupted: bool = False
    audio_recv_count: int = 0
    dropped_once: bool = False

    @property
    def next_s2c_seq(self) -> int:
        self.s2c_seq += 1
        return self.s2c_seq

    def update_c2s_seq(self, seq: int) -> bool:
        """Return True if seq is valid (monotonically increasing)."""
        if seq <= self.c2s_seq:
            return False
        self.c2s_seq = seq
        return True

    def touch(self) -> None:
        self.last_msg_at = time.monotonic()


class SessionStore:
    """In-memory session registry. Supports session resume within TTL."""

    def __init__(self, ttl_s: int = 30) -> None:
        self._sessions: dict[str, SessionContext] = {}
        self._ttl_s = ttl_s

    def put(self, ctx: SessionContext) -> None:
        self._sessions[ctx.session_id] = ctx

    def get(self, session_id: str) -> SessionContext | None:
        ctx = self._sessions.get(session_id)
        if ctx is None:
            return None
        elapsed = time.monotonic() - ctx.last_msg_at
        if elapsed > self._ttl_s:
            del self._sessions[session_id]
            return None
        return ctx

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
