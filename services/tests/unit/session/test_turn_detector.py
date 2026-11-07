from __future__ import annotations

from session.turn_detector import TurnDetector


class TestTurnDetector:
    def test_turn_end_after_consecutive_silence(self):
        td = TurnDetector(sensitivity="medium")  # threshold = 30
        # Feed 29 silence frames → no turn end
        for _ in range(29):
            assert td.on_frame(False) is False
        # 30th silence frame → turn end
        assert td.on_frame(False) is True

    def test_speech_resets_counter(self):
        td = TurnDetector(sensitivity="medium")
        for _ in range(29):
            td.on_frame(False)
        td.on_frame(True)  # speech resets
        assert td.on_frame(False) is False  # counter restarted

    def test_turn_end_fires_only_once(self):
        td = TurnDetector(sensitivity="high")  # threshold = 20
        for _ in range(20):
            td.on_frame(False)
        assert td.on_frame(False) is False  # already fired, doesn't fire again

    def test_force_turn_end(self):
        td = TurnDetector(sensitivity="medium")
        td.force_turn_end()
        # Should behave as if threshold reached
        assert td.silence_frames >= 30

    def test_reset(self):
        td = TurnDetector(sensitivity="medium")
        for _ in range(25):
            td.on_frame(False)
        td.reset()
        assert td.silence_frames == 0

    def test_sensitivity_low_high_frames(self):
        td_low = TurnDetector(sensitivity="low")
        td_high = TurnDetector(sensitivity="high")
        for _ in range(19):
            td_low.on_frame(False)
            td_high.on_frame(False)
        # After 19 frames: high should fire (threshold 20)
        assert td_high.on_frame(False) is True
        # low should NOT fire (threshold 45)
        assert td_low.on_frame(False) is False
