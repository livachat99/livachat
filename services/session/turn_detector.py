from __future__ import annotations

import logging
from typing import ClassVar

logger = logging.getLogger("livachat.session.turn")


class TurnDetector:
    """Detects end-of-user-turn based on consecutive silence frames.

    Configurable VAD sensitivity maps to silence-frame thresholds:
    * low    → 45 frames (~900ms at 20ms/frame)
    * medium → 30 frames (~600ms)
    * high   → 20 frames (~400ms)
    """

    _THRESHOLDS: ClassVar[dict[str, int]] = {"low": 45, "medium": 30, "high": 20}

    def __init__(self, sensitivity: str = "medium") -> None:
        self._threshold = self._THRESHOLDS.get(sensitivity, 30)
        self._silence_count = 0
        self._turn_ended = False
        self._force_end = False

    def on_frame(self, is_speech: bool) -> bool:
        """Process a VAD frame. Returns True when a turn end is detected."""
        if self._force_end:
            self._force_end = False
            self._turn_ended = True
            return True
        if is_speech:
            self._silence_count = 0
            self._turn_ended = False
        else:
            self._silence_count += 1
            if self._silence_count >= self._threshold and not self._turn_ended:
                self._turn_ended = True
                logger.info(
                    "turn end detected by continuous silence",
                    extra={
                        "silence_frames": self._silence_count,
                        "threshold": self._threshold,
                    },
                )
                return True
        return False

    def force_turn_end(self) -> None:
        """ASR speech_final / endpoint event — trigger turn end on next call to on_frame()."""
        self._silence_count = self._threshold
        self._force_end = True

    def reset(self) -> None:
        self._silence_count = 0
        self._turn_ended = False
        self._force_end = False

    @property
    def silence_frames(self) -> int:
        return self._silence_count
