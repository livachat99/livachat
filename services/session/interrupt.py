from __future__ import annotations

import logging

from session.context import SessionContext
from session.state_machine import SessionState

logger = logging.getLogger("livachat.session.interrupt")


class InterruptController:
    """Executes the 5-step interrupt action per design §4.4.

    Target: internal cancellation < 50ms.
    """

    def __init__(self, ctx: SessionContext) -> None:
        self._ctx = ctx

    async def interrupt(self, reason: str = "user_voice") -> None:
        """Execute the interrupt sequence."""
        # 1. Increment epoch (invalidates in-flight LLM/TTS tasks)
        self._ctx.epoch += 1
        epoch_snapshot = self._ctx.epoch

        # 2. Cancel in-flight TTS stream (orchestrator handles this via epoch check)
        # 3. Cancel in-flight LLM stream (orchestrator handles this via epoch check)
        # 4. Transition state back to LISTENING (design §4.2: any non-IDLE → listening)
        if self._ctx.state in (SessionState.THINKING, SessionState.SPEAKING):
            self._ctx.sm.transition_or_reject(SessionState.LISTENING)

        # 5. Reset ASR for new turn (orchestrator handles this)
        logger.info("interrupt executed", extra={"reason": reason, "epoch": epoch_snapshot})

    def should_yield(self, epoch_snapshot: int) -> bool:
        """Return True if the calling task's epoch snapshot matches current epoch.

        If epochs don't match, the task has been superseded by an interrupt.
        """
        ok = self._ctx.epoch == epoch_snapshot
        if not ok:
            logger.info(
                "pipeline work abandoned due to interrupt",
                extra={
                    "reason": "epoch_mismatch",
                    "captured_epoch": epoch_snapshot,
                    "current_epoch": self._ctx.epoch,
                },
            )
        return ok
