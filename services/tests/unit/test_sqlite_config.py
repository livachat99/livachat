"""SqliteConfig 顶层段（agent_relay.job_pg 提升为顶层 sqlite，lvdata §6.2.0）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.config.models import AppConfig, SqliteConfig


class TestSqliteConfig:
    def test_defaults(self):
        cfg = SqliteConfig()
        assert cfg.enabled is True
        assert cfg.lvdata_db_path == ""
        assert cfg.max_queue_size == 1024

    def test_app_config_defaults(self):
        cfg = AppConfig()
        assert cfg.sqlite.enabled is True
        assert cfg.sqlite.lvdata_db_path == ""
        assert cfg.sqlite.max_queue_size == 1024

    def test_yaml_shape_roundtrip(self):
        cfg = AppConfig(
            sqlite={"enabled": True, "lvdata_db_path": "/opt/lvdata/lvdata_inventory.db",
                    "max_queue_size": 128},
        )
        assert cfg.sqlite.lvdata_db_path == "/opt/lvdata/lvdata_inventory.db"
        assert cfg.sqlite.max_queue_size == 128

    def test_env_override(self, monkeypatch):
        # pydantic-settings 大小写不敏感、下划线位置敏感：LVdata_db_path 小写
        # 后匹配 lvdata_db_path（与 redis 段 LIVACHAT__REDIS__KEY_PREFIX 同规则）
        monkeypatch.setenv("LIVACHAT__SQLITE__LVdata_db_path", "/tmp/x.db")
        monkeypatch.setenv("LIVACHAT__SQLITE__MAX_QUEUE_SIZE", "64")
        cfg = AppConfig()
        assert cfg.sqlite.lvdata_db_path == "/tmp/x.db"
        assert cfg.sqlite.max_queue_size == 64

    def test_max_queue_size_zero_invalid(self):
        with pytest.raises(ValidationError):
            SqliteConfig(max_queue_size=0)
