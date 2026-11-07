from __future__ import annotations

import hashlib

import pytest

from gateway.auth import Authenticator, Principal, wire_digest
from shared.config.models import AuthConfig
from shared.utils.errors import AuthError


def _cfg(mode: str = "dev", dev_token: str = "secret-token") -> AuthConfig:
    return AuthConfig(mode=mode, dev_token=dev_token, jwt_secret="", jwt_algorithm="HS256")


class TestAuthenticator:
    def test_dev_mode_valid_digest(self):
        auth = Authenticator(_cfg(dev_token="tok"))
        p = auth.verify(wire_digest("tok"), "device-1")
        assert p.user_id == "dev-user"
        assert p.device_id == "device-1"

    def test_wire_digest_fixed_vector(self):
        """公式跨语言锚点（docs/server_auth.md §3.1）——sha1_hex(secret + "LivaChat")。"""
        expected = hashlib.sha1(b"dev-livachat-tokenLivaChat").hexdigest()
        assert expected == "1161f736057f8e087b6351a66254f32510d0121c"
        assert wire_digest("dev-livachat-token") == expected

    def test_dev_mode_plaintext_token_rejected(self):
        """硬切换：明文 secret 直接发 → 401（不再是合法 wire token）。"""
        auth = Authenticator(_cfg(dev_token="right"))
        with pytest.raises(AuthError):
            auth.verify("right", "device-1")

    def test_dev_mode_wrong_digest_rejected(self):
        auth = Authenticator(_cfg(dev_token="right"))
        with pytest.raises(AuthError):
            auth.verify(wire_digest("wrong"), "device-1")

    def test_dev_mode_non_ascii_token_401_not_500(self):
        """compare_digest 走 bytes，含非 ASCII 的入参不得抛 TypeError（500）。"""
        auth = Authenticator(_cfg(dev_token="right"))
        with pytest.raises(AuthError):
            auth.verify("中文token", "device-1")

    def test_empty_secret_fixture_semantics(self):
        """空 secret（仅测试夹具会构造）→ 仅空 token 放行。"""
        auth = Authenticator(_cfg(dev_token=""))
        assert auth.verify("", "device-1").device_id == "device-1"
        with pytest.raises(AuthError):
            auth.verify(wire_digest(""), "device-1")

    def test_jwt_mode_raises_not_implemented(self):
        auth = Authenticator(_cfg(mode="jwt"))
        with pytest.raises(AuthError, match="jwt mode"):
            auth.verify("anything", "device-1")

    def test_principal_immutable(self):
        p = Principal("u", "d")
        with pytest.raises(AttributeError):  # frozen dataclass FrozenInstanceError
            p.user_id = "x"  # type: ignore[misc]
