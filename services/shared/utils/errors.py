from __future__ import annotations

from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    """Protocol error codes (§4.3 of ws-protocol-design.md)."""

    # -- protocol-level (10xx) ------------------------------------------------
    MALFORMED_JSON = 1006
    UNSUPPORTED_MESSAGE_TYPE = 1006
    FRAME_TOO_LARGE = 1006

    # -- session-level (20xx) -------------------------------------------------
    AUTH_FAILED = 2001
    TOKEN_EXPIRED = 2002

    # -- media-level (30xx) ---------------------------------------------------
    UNSUPPORTED_CODEC = 3001
    UNSUPPORTED_SAMPLE_RATE = 3002
    FRAME_TOO_LARGE_MEDIA = 3003

    # -- connection-level (40xx) ----------------------------------------------
    UNAUTHORIZED = 4001
    HEARTBEAT_TIMEOUT = 4002
    IDLE_TIMEOUT = 4003

    # -- provider-level (10xx, fatal=false) -----------------------------------
    ASR_UNAVAILABLE = 1001
    TTS_UNAVAILABLE = 1002
    LLM_UNAVAILABLE = 1003
    INTERNAL_ERROR = 1004
    OVERLOAD = 1005
    OMNI_UNAVAILABLE = 1007
    TRANSLATE_UNAVAILABLE = 1008
    # session.change 被拒（该请求不会跟随 session.changed 回声）；客户端据此回滚模式开关
    SESSION_CHANGE_FAILED = 1009
    # avdetector 内部用（best-effort 旁路，失败静默降级——不向客户端下发 error）
    AVDETECTOR_UNAVAILABLE = 1010
    # session.change(mode="config") 被拒（独立于 1009；客户端不得按模式切换回滚）
    SESSION_CONFIG_FAILED = 1011


class AppError(Exception):
    """Base exception for all application-level errors."""

    def __init__(self, message: str, *, code: ErrorCode | None = None):
        super().__init__(message)
        self.code = code

    def to_protocol_error_payload(self) -> dict[str, Any]:
        """Return a dict suitable for :class:`ServerErrorPayload`."""
        return {
            "code": int(self.code) if self.code else 1004,
            "message": str(self),
            "fatal": False,
            "recoverable": True,
        }


class ConfigError(AppError):
    """Raised when configuration is invalid (fail-fast on startup)."""

    def __init__(self, message: str):
        super().__init__(message, code=None)


class AuthError(AppError):
    """Raised on authentication / authorization failures."""

    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.UNAUTHORIZED):
        super().__init__(message, code=code)


class ProtocolError(AppError):
    """Raised when an incoming message violates the wire protocol."""

    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.MALFORMED_JSON):
        super().__init__(message, code=code)


class ProviderError(AppError):
    """Raised when an external provider (ASR/LLM/TTS) fails.

    ``http_status``：provider 返回的 HTTP 状态码（无 HTTP 响应层时 None，
    如连接类/超时类错误）。区别于 ``code``（协议错误码）。
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        provider: str = "",
        http_status: int | None = None,
    ):
        super().__init__(message, code=code)
        self.provider = provider
        self.http_status = http_status
