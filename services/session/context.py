from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from session.state_machine import SessionState, SessionStateMachine


@dataclass
class SessionContext:
    """Runtime state for a single LivaChat session (design §3.3)."""

    session_id: str
    device_id: str
    user_id: str

    # State machine
    sm: SessionStateMachine = field(default_factory=SessionStateMachine)

    # Config carried from session.start
    enable_video: bool = False
    audio_sample_rate: int = 16000
    vad_sensitivity: str = "medium"
    client_version: str = ""
    # AVDetector 会话级开关（session.start.config.avdetector；缺省 enable）
    avdetector_enabled: bool = True
    # App 当前 UI 语言（canonical code；未上报/空 = ""）
    language: str = ""
    # 规范化后的客户端时区（timezone-support.md §5.2）；无效/缺失为空串
    timezone: str = ""

    # Epoch — every TTS/LLM task snapshots this at start; must match on yield
    epoch: int = 0

    # Latest video frames (K=1 by default)
    max_video_frames: int = 1
    # (frame_id, jpeg_bytes, timestamp_ms, width, height, orientation_degrees, camera_facing)
    latest_video_frames: deque[tuple[int, bytes, int, int, int, int, str]] = field(default_factory=deque)

    # Restored flag
    restored: bool = False
    resumed_count: int = 0

    # ASR / LLM / TTS provider names used for this session
    asr_provider: str = ""
    llm_provider: str = ""
    tts_provider: str = ""

    @property
    def state(self) -> SessionState:
        return self.sm.state
