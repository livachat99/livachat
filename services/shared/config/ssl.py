from __future__ import annotations

import ssl as _ssl
from pathlib import Path

from shared.config.loader import _project_root
from shared.config.models import ServerSslConfig

_CERT_REQS_MAP = {
    "none": _ssl.CERT_NONE,
    "optional": _ssl.CERT_OPTIONAL,
    "required": _ssl.CERT_REQUIRED,
}


def _abs(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str((_project_root() / p).resolve())


def build_ssl_kwargs(ssl_cfg: ServerSslConfig) -> dict:
    """Extract uvicorn-compatible SSL keyword arguments from server SSL config."""
    if not ssl_cfg.enabled:
        return {}

    kwargs: dict = {
        "ssl_certfile": _abs(ssl_cfg.certfile),
        "ssl_keyfile": _abs(ssl_cfg.keyfile),
    }
    if ssl_cfg.keyfile_password is not None:
        kwargs["ssl_keyfile_password"] = ssl_cfg.keyfile_password
    if ssl_cfg.cafile is not None:
        kwargs["ssl_ca_certs"] = _abs(ssl_cfg.cafile)
    if ssl_cfg.cert_reqs != "none":
        kwargs["ssl_cert_reqs"] = _CERT_REQS_MAP[ssl_cfg.cert_reqs]
    return kwargs
