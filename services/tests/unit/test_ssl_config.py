from __future__ import annotations

import ssl as _ssl

import pytest

from shared.config.models import AppConfig, ServerSslConfig
from shared.config.validation import _validate_ssl, validate_config
from shared.utils.errors import ConfigError

# ---------------------------------------------------------------------------
# _validate_ssl
# ---------------------------------------------------------------------------


class TestValidateSsl:
    def _base_config(self) -> AppConfig:
        cfg = AppConfig()
        cfg.auth.mode = "dev"
        cfg.auth.dev_token = "test-token"
        return cfg

    def test_disabled_passes(self):
        cfg = self._base_config()
        cfg.server.ssl.enabled = False
        _validate_ssl(cfg)  # no exception

    def test_enabled_missing_certfile(self):
        cfg = self._base_config()
        cfg.server.ssl.enabled = True
        cfg.server.ssl.keyfile = "/path/to/key.pem"
        with pytest.raises(ConfigError, match="certfile"):
            _validate_ssl(cfg)

    def test_enabled_missing_keyfile(self):
        cfg = self._base_config()
        cfg.server.ssl.enabled = True
        cfg.server.ssl.certfile = "/path/to/cert.pem"
        with pytest.raises(ConfigError, match="keyfile"):
            _validate_ssl(cfg)

    def test_enabled_valid(self):
        cfg = self._base_config()
        cfg.server.ssl.enabled = True
        cfg.server.ssl.certfile = "/path/to/cert.pem"
        cfg.server.ssl.keyfile = "/path/to/key.pem"
        _validate_ssl(cfg)  # no exception

    def test_mtls_optional_missing_cafile(self):
        cfg = self._base_config()
        cfg.server.ssl.enabled = True
        cfg.server.ssl.certfile = "/path/to/cert.pem"
        cfg.server.ssl.keyfile = "/path/to/key.pem"
        cfg.server.ssl.cert_reqs = "optional"
        with pytest.raises(ConfigError, match="cafile"):
            _validate_ssl(cfg)

    def test_mtls_required_missing_cafile(self):
        cfg = self._base_config()
        cfg.server.ssl.enabled = True
        cfg.server.ssl.certfile = "/path/to/cert.pem"
        cfg.server.ssl.keyfile = "/path/to/key.pem"
        cfg.server.ssl.cert_reqs = "required"
        with pytest.raises(ConfigError, match="cafile"):
            _validate_ssl(cfg)

    def test_mtls_optional_with_cafile(self):
        cfg = self._base_config()
        cfg.server.ssl.enabled = True
        cfg.server.ssl.certfile = "/path/to/cert.pem"
        cfg.server.ssl.keyfile = "/path/to/key.pem"
        cfg.server.ssl.cert_reqs = "optional"
        cfg.server.ssl.cafile = "/path/to/ca.pem"
        _validate_ssl(cfg)  # no exception

    def test_integrated_in_validate_config(self):
        cfg = self._base_config()
        cfg.server.ssl.enabled = True
        with pytest.raises(ConfigError, match="certfile"):
            validate_config(cfg)


# ---------------------------------------------------------------------------
# _build_ssl_kwargs
# ---------------------------------------------------------------------------


class TestBuildSslKwargs:
    def test_disabled_returns_empty(self):
        from shared.config.ssl import build_ssl_kwargs

        ssl_cfg = ServerSslConfig(enabled=False)
        assert build_ssl_kwargs(ssl_cfg) == {}

    def test_minimal_enabled(self):
        from shared.config.ssl import build_ssl_kwargs

        ssl_cfg = ServerSslConfig(
            enabled=True,
            certfile="/path/to/cert.pem",
            keyfile="/path/to/key.pem",
        )
        kwargs = build_ssl_kwargs(ssl_cfg)
        assert kwargs == {
            "ssl_certfile": "/path/to/cert.pem",
            "ssl_keyfile": "/path/to/key.pem",
        }

    def test_with_keyfile_password(self):
        from shared.config.ssl import build_ssl_kwargs

        ssl_cfg = ServerSslConfig(
            enabled=True,
            certfile="/path/to/cert.pem",
            keyfile="/path/to/key.pem",
            keyfile_password="secret",
        )
        kwargs = build_ssl_kwargs(ssl_cfg)
        assert kwargs["ssl_keyfile_password"] == "secret"

    def test_with_cafile(self):
        from shared.config.ssl import build_ssl_kwargs

        ssl_cfg = ServerSslConfig(
            enabled=True,
            certfile="/path/to/cert.pem",
            keyfile="/path/to/key.pem",
            cafile="/path/to/ca.pem",
        )
        kwargs = build_ssl_kwargs(ssl_cfg)
        assert kwargs["ssl_ca_certs"] == "/path/to/ca.pem"

    def test_cert_reqs_none_omitted(self):
        from shared.config.ssl import build_ssl_kwargs

        ssl_cfg = ServerSslConfig(
            enabled=True,
            certfile="/path/to/cert.pem",
            keyfile="/path/to/key.pem",
            cert_reqs="none",
        )
        kwargs = build_ssl_kwargs(ssl_cfg)
        assert "ssl_cert_reqs" not in kwargs

    def test_cert_reqs_optional(self):
        from shared.config.ssl import build_ssl_kwargs

        ssl_cfg = ServerSslConfig(
            enabled=True,
            certfile="/path/to/cert.pem",
            keyfile="/path/to/key.pem",
            cert_reqs="optional",
        )
        kwargs = build_ssl_kwargs(ssl_cfg)
        assert kwargs["ssl_cert_reqs"] == _ssl.CERT_OPTIONAL

    def test_cert_reqs_required(self):
        from shared.config.ssl import build_ssl_kwargs

        ssl_cfg = ServerSslConfig(
            enabled=True,
            certfile="/path/to/cert.pem",
            keyfile="/path/to/key.pem",
            cert_reqs="required",
        )
        kwargs = build_ssl_kwargs(ssl_cfg)
        assert kwargs["ssl_cert_reqs"] == _ssl.CERT_REQUIRED

    def test_all_options(self):
        from shared.config.ssl import build_ssl_kwargs

        ssl_cfg = ServerSslConfig(
            enabled=True,
            certfile="/path/to/cert.pem",
            keyfile="/path/to/key.pem",
            keyfile_password="pw",
            cafile="/path/to/ca.pem",
            cert_reqs="required",
        )
        kwargs = build_ssl_kwargs(ssl_cfg)
        assert kwargs == {
            "ssl_certfile": "/path/to/cert.pem",
            "ssl_keyfile": "/path/to/key.pem",
            "ssl_keyfile_password": "pw",
            "ssl_ca_certs": "/path/to/ca.pem",
            "ssl_cert_reqs": _ssl.CERT_REQUIRED,
        }
