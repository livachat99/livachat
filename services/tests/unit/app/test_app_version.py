"""server 版本单一来源：pyproject 打出的包元数据。"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

from app.application import create_app
from shared.config.models import AppConfig
from shared.version import SERVER_VERSION


def test_server_version_from_package_metadata():
    """SERVER_VERSION 取自安装元数据；必须对照 pyproject 才能发现未重装的 editable。"""
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert SERVER_VERSION == declared == version("livachat-server")
    assert create_app(config=AppConfig()).version == SERVER_VERSION
