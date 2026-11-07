from __future__ import annotations

import pytest

from session.state_machine import IllegalStateTransition, SessionState, SessionStateMachine


class TestStateMachine:
    def test_legal_transitions(self):
        sm = SessionStateMachine(state=SessionState.IDLE)
        sm.transition(SessionState.LISTENING)
        assert sm.state == SessionState.LISTENING
        sm.transition(SessionState.THINKING)
        assert sm.state == SessionState.THINKING
        sm.transition(SessionState.SPEAKING)
        assert sm.state == SessionState.SPEAKING
        sm.transition(SessionState.LISTENING)
        assert sm.state == SessionState.LISTENING

    def test_illegal_idle_to_speaking(self):
        sm = SessionStateMachine(state=SessionState.IDLE)
        with pytest.raises(IllegalStateTransition, match="idle → speaking"):
            sm.transition(SessionState.SPEAKING)

    def test_illegal_listening_to_speaking(self):
        sm = SessionStateMachine(state=SessionState.LISTENING)
        with pytest.raises(IllegalStateTransition):
            sm.transition(SessionState.SPEAKING)

    def test_transition_or_reject_silent(self):
        sm = SessionStateMachine(state=SessionState.IDLE)
        sm.transition_or_reject(SessionState.SPEAKING)  # illegal → no-op
        assert sm.state == SessionState.IDLE

    def test_transition_or_reject_legal(self):
        sm = SessionStateMachine(state=SessionState.IDLE)
        sm.transition_or_reject(SessionState.LISTENING)
        assert sm.state == SessionState.LISTENING

    def test_any_to_idle(self):
        for state in [SessionState.LISTENING, SessionState.THINKING, SessionState.SPEAKING]:
            sm = SessionStateMachine(state=state)
            sm.transition(SessionState.IDLE)
            assert sm.state == SessionState.IDLE
