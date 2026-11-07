from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum


class Scenario(StrEnum):
    DEFAULT = "default"
    TEXT_ONLY = "text_only"
    TEXT_ON_START = "text_on_start"
    WITH_AUDIO = "with_audio"
    SLOW_NETWORK = "slow_network"
    SERVER_ERROR = "server_error"
    AUTH_FAILED = "auth_failed"
    IDLE_TIMEOUT = "idle_timeout"
    PING_TIMEOUT = "ping_timeout"
    RESUME_SUCCESS = "resume_success"
    RESUME_EXPIRED = "resume_expired"
    RESUME_DROP = "resume_drop"
    INTERRUPT = "interrupt"
    VIDEO_GALLERY = "video_gallery"


@dataclass
class ServerConfig:
    host: str = field(default_factory=lambda: os.getenv("MOCK_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("MOCK_PORT", "8080")))
    scenario: Scenario = field(
        default_factory=lambda: Scenario(os.getenv("MOCK_SCENARIO", "default"))
    )
    idle_timeout_s: int = field(
        default_factory=lambda: int(os.getenv("MOCK_IDLE_TIMEOUT_S", "60"))
    )
    ping_timeout_s: int = field(
        default_factory=lambda: int(os.getenv("MOCK_PING_TIMEOUT_S", "35"))
    )
    ping_interval_s: int = field(
        default_factory=lambda: int(os.getenv("MOCK_PING_INTERVAL_S", "15"))
    )
    keepalive_interval_s: int = 15
    session_ttl_s: int = 30
    dev_token: str = field(
        default_factory=lambda: os.getenv("MOCK_DEV_TOKEN", "dev-token")
    )
    slow_network_min_delay_ms: int = 500
    slow_network_max_delay_ms: int = 2000
    silence_threshold_ms: int = 600
    interrupt_transcript: str = "用户打断了AI回复"
    default_transcript: str = "你好，请问有什么可以帮助你的？"
    ai_text_chunks: list[str] = field(default_factory=lambda: [
        "你好！",
        "很高兴",
        "为你服务。",
        "请问有什么",
        "我可以",
        "帮助你的吗？",
    ])
