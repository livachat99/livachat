"""MeetingConfig 校验（docs/livachat_meeting.md §6.1）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.config.models import AppConfig
from shared.config.validation import _validate_meeting
from shared.utils.errors import ConfigError


def _meeting(**overrides):
    values = {
        "enabled": True,
        "external_server_addr": "https://chat.example.test:8080",
        "livekit_key": "key",
        "livekit_secret": "secret",
        "livekit_server": "wss://livekit.example.test:7880",
        "web_dir": "",
    }
    values.update(overrides)
    return values


def _cfg(tmp_path: Path, *, index: bool = True, **overrides) -> AppConfig:
    web = tmp_path / "meeting"
    web.mkdir(exist_ok=True)
    index_file = web / "index.html"
    if index:
        index_file.write_text("ok", encoding="utf-8")
    else:
        index_file.unlink(missing_ok=True)
    values = _meeting(**overrides)
    if "web_dir" not in overrides:
        values["web_dir"] = str(web)
    return AppConfig(auth={"mode": "dev", "dev_token": "t"}, meeting=values)


def test_disabled_passes_with_empty_fields():
    cfg = AppConfig(meeting={"enabled": False})
    _validate_meeting(cfg)  # no exception


def test_enabled_valid(tmp_path):
    cfg = _cfg(tmp_path)
    _validate_meeting(cfg)


def test_enabled_requires_bff(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.bff.enabled = False
    with pytest.raises(ConfigError, match="bff.enabled"):
        _validate_meeting(cfg)


@pytest.mark.parametrize("addr", ["", "chat.example.test", "https://chat.example.test/",
                                  "https://chat.example.test/path", "https://chat.example.test?x=1",
                                  "https://chat.example.test:abc", "https://[::1", "https://:8080",
                                  "https://user:pass@chat.example.test:8080",
                                  "http://evil.example\\@127.0.0.1",
                                  "https://chat.example.test\\evil",
                                  "https://chat example.test"])
def test_bad_external_server_addr(tmp_path, addr):
    cfg = _cfg(tmp_path, external_server_addr=addr)
    with pytest.raises(ConfigError, match="external_server_addr"):
        _validate_meeting(cfg)


@pytest.mark.parametrize("server", ["", "ws://localhost:7880", "https://livekit.example.test",
                                    "wss://", "wss://livekit.example.test:abc",
                                    "wss://[::1", "wss://:7880",
                                    "wss://user:pass@livekit.example.test:7880",
                                    "wss://livekit.example.test\\evil",
                                    "wss://livekit example.test"])
def test_livekit_server_must_be_wss(tmp_path, server):
    cfg = _cfg(tmp_path, livekit_server=server)
    with pytest.raises(ConfigError, match="livekit_server"):
        _validate_meeting(cfg)


def test_missing_key_or_secret(tmp_path):
    for field in ("livekit_key", "livekit_secret"):
        for value in ("", "   "):
            cfg = _cfg(tmp_path, **{field: value})
            with pytest.raises(ConfigError, match=field):
                _validate_meeting(cfg)


def test_web_dir_required_and_index_required(tmp_path):
    cfg = _cfg(tmp_path, web_dir="")
    with pytest.raises(ConfigError, match="web_dir"):
        _validate_meeting(cfg)
    cfg = _cfg(tmp_path, index=False)
    with pytest.raises(ConfigError, match="index.html"):
        _validate_meeting(cfg)


def test_http_localhost_is_allowed(tmp_path):
    cfg = _cfg(tmp_path, external_server_addr="http://localhost:8080")
    _validate_meeting(cfg)


def test_http_non_localhost_is_rejected(tmp_path):
    cfg = _cfg(tmp_path, external_server_addr="http://chat.example.test:8080")
    with pytest.raises(ConfigError, match="https"):
        _validate_meeting(cfg)
