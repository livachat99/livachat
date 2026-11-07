from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SessionState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


# Legal transitions per design §4.2
_LEGAL: dict[SessionState, set[SessionState]] = {
    SessionState.IDLE: {SessionState.LISTENING},
    SessionState.LISTENING: {SessionState.THINKING, SessionState.IDLE},
    SessionState.THINKING: {SessionState.SPEAKING, SessionState.LISTENING, SessionState.IDLE},
    SessionState.SPEAKING: {SessionState.LISTENING, SessionState.IDLE},
}


class IllegalStateTransition(Exception):
    """Raised when attempting an invalid state migration."""


@dataclass
class SessionStateMachine:
    state: SessionState = SessionState.IDLE

    def transition(self, to: SessionState) -> None:
        allowed = _LEGAL.get(self.state, set())
        if to not in allowed:
            raise IllegalStateTransition(f"Cannot transition {self.state} → {to}")
        self.state = to

    def transition_or_reject(self, to: SessionState) -> None:
        try:
            self.transition(to)
        except IllegalStateTransition:
            # In prod: metric + reject silently
            pass
