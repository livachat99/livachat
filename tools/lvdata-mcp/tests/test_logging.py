"""_setup_logging：structlog→stdlib 收敛 + 文件轮转（docs/server_logging.md §7）。"""

from __future__ import annotations

import json
import logging
import logging.handlers

import pytest


def test_cli_default_attaches_no_file_handler():
    """CLI 路径（file 缺省）不挂文件 handler——reindex/eval/stage 行为零变更。"""
    from lvdata_mcp.main import _setup_logging

    _setup_logging("INFO")
    assert not any(isinstance(h, logging.FileHandler) for h in logging.getLogger().handlers)


def test_enabled_creates_dir_and_writes_json(tmp_path):
    import structlog

    from lvdata_mcp.config import LogFileConfig
    from lvdata_mcp.main import _setup_logging

    _setup_logging("INFO", file=LogFileConfig(dir=str(tmp_path / "logs")))
    structlog.get_logger("test_file").info("file hello", key="v")

    log_file = tmp_path / "logs" / "lvdata_mcp.log"
    assert log_file.exists()
    record = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["event"] == "file hello"
    assert record["key"] == "v"


def test_rotation_and_backup_cap(tmp_path):
    import structlog

    from lvdata_mcp.config import LogFileConfig
    from lvdata_mcp.main import _setup_logging

    _setup_logging("INFO", file=LogFileConfig(dir=str(tmp_path / "logs"), max_bytes=512, backup_count=2))
    logger = structlog.get_logger("test_rot")
    for i in range(200):
        logger.info("rotate me", i=i)

    d = tmp_path / "logs"
    assert (d / "lvdata_mcp.log").exists()
    assert (d / "lvdata_mcp.log.1").exists()
    assert (d / "lvdata_mcp.log.2").exists()
    assert not (d / "lvdata_mcp.log.3").exists()  # 份数封顶：只保留 backup_count 份


def test_disabled_no_file(tmp_path):
    from lvdata_mcp.config import LogFileConfig
    from lvdata_mcp.main import _setup_logging

    _setup_logging("INFO", file=LogFileConfig(enabled=False, dir=str(tmp_path / "logs")))
    assert not (tmp_path / "logs").exists()


def test_unusable_dir_fails_fast(tmp_path):
    from lvdata_mcp.config import LogFileConfig
    from lvdata_mcp.main import _setup_logging

    blocker = tmp_path / "blocked"
    blocker.write_text("file, not dir", encoding="utf-8")
    with pytest.raises(RuntimeError, match="log dir not usable"):
        _setup_logging("INFO", file=LogFileConfig(dir=str(blocker)))


def test_console_json_shape_preserved(capsys):
    """CLI stdout 字段集合保持：event/level/timestamp + 业务 kwargs（JSON 键序无语义）。"""
    import structlog

    from lvdata_mcp.main import _setup_logging

    _setup_logging("INFO")
    structlog.get_logger("test_shape").info("shape", k=1)
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["event"] == "shape"
    assert record["level"] == "info"
    assert record["k"] == 1
    assert "timestamp" in record


def test_serve_mode_writes_only_file(tmp_path, capsys):
    """serve 模式（file 激活）仅挂 RotatingFileHandler，stdout 零输出（docs/server_logging.md §8）。"""
    import structlog

    from lvdata_mcp.config import LogFileConfig
    from lvdata_mcp.main import _setup_logging

    _setup_logging("INFO", file=LogFileConfig(dir=str(tmp_path / "logs")))
    root = logging.getLogger()
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
    assert not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
                   for h in root.handlers)

    structlog.get_logger("test_serve_only").info("serve only")
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""

    log_file = tmp_path / "logs" / "lvdata_mcp.log"
    record = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["event"] == "serve only"
