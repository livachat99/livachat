from __future__ import annotations

from session.context import SessionContext
from session.interrupt import InterruptController
from session.state_machine import SessionState, SessionStateMachine


class TestInterruptController:
    async def test_epoch_increments(self):
        ctx = SessionContext(session_id="sess_test", device_id="d", user_id="u")
        ctx.sm = SessionStateMachine(state=SessionState.SPEAKING)  # override directly
        ctrl = InterruptController(ctx)

        before = ctx.epoch
        await ctrl.interrupt("user_voice")
        assert ctx.epoch == before + 1

    async def test_state_transitions_to_listening(self):
        ctx = SessionContext(session_id="sess_test", device_id="d", user_id="u")
        ctx.sm = SessionStateMachine(state=SessionState.SPEAKING)
        ctrl = InterruptController(ctx)

        await ctrl.interrupt("manual")
        assert ctx.state == SessionState.LISTENING
        assert ctx.epoch > 0

    async def test_should_yield_epoch_gate(self):
        ctx = SessionContext(session_id="sess_test", device_id="d", user_id="u")
        ctx.sm = SessionStateMachine(state=SessionState.SPEAKING)
        ctrl = InterruptController(ctx)

        snap = ctx.epoch
        assert ctrl.should_yield(snap) is True

        await ctrl.interrupt("user_voice")
        assert ctrl.should_yield(snap) is False  # old epoch rejected
        assert ctrl.should_yield(ctx.epoch) is True  # new epoch ok

    async def test_double_interrupt_epoch_increments_twice(self):
        ctx = SessionContext(session_id="sess_test", device_id="d", user_id="u")
        ctx.sm = SessionStateMachine(state=SessionState.SPEAKING)
        ctrl = InterruptController(ctx)

        e0 = ctx.epoch
        await ctrl.interrupt("first")
        e1 = ctx.epoch
        ctx.sm = SessionStateMachine(state=SessionState.SPEAKING)  # simulate next turn
        await ctrl.interrupt("second")
        e2 = ctx.epoch

        assert e1 == e0 + 1
        assert e2 == e1 + 1
