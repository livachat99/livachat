from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from shared.config.models import AuthConfig
from shared.utils.errors import AuthError, ErrorCode

#: wire token 的固定盐（协议常量，客户端同值；docs/server_auth.md §3.1）
AUTH_SALT = "LivaChat"


def wire_digest(secret: str) -> str:
    """明文 secret → 线上传输的固定摘要 sha1_hex(secret + AUTH_SALT)。"""
    return hashlib.sha1((secret + AUTH_SALT).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    user_id: str
    device_id: str


class Authenticator:
    """Validates WebSocket handshake tokens.

    ``mode=dev``: compares the query ``token`` against ``sha1_hex(dev_token + "LivaChat")``
    (docs/server_auth.md — 明文 secret 不上网)。
    ``mode=jwt``: validates a JWT (reserved for later).
    """

    def __init__(self, config: AuthConfig) -> None:
        self._cfg = config
        # 空 secret 仅测试夹具会构造（validation.py 保证运行态非空）：
        # 维持"仅空 token 放行"的夹具语义。
        self._expected = wire_digest(config.dev_token) if config.dev_token else ""

    def verify(self, token: str, device_id: str) -> Principal:
        if self._cfg.mode == "dev":
            # bytes 比较：compare_digest 对含非 ASCII 的 str 会抛 TypeError（会变 500）
            if not hmac.compare_digest(token.encode("utf-8"), self._expected.encode("utf-8")):
                raise AuthError("invalid dev token", code=ErrorCode.UNAUTHORIZED)
            return Principal(user_id="dev-user", device_id=device_id)

        # jwt mode (reserved)
        raise AuthError("jwt mode is not implemented", code=ErrorCode.UNAUTHORIZED)
