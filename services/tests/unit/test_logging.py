from __future__ import annotations

import json
import logging.handlers
from urllib.parse import parse_qsl, urlsplit

import pytest

from shared.observability.logging import (
    _redact_sensitive,
    device_id_ctx,
    epoch_ctx,
    get_logger,
    redact_url,
    seq_ctx,
    session_id_ctx,
    setup_logging,
)


class TestLoggingSetup:
    def test_json_logs_output(self, capsys):
        setup_logging(json_logs=True, log_level="INFO")
        logger = get_logger("test_json")
        logger.info("hello", key="val")

        captured = capsys.readouterr()
        # structlog JSON may go to stderr via ProcessorFormatter
        line = captured.out.strip() or captured.err.strip()
        assert line, f"Expected JSON output, got empty out='{captured.out}' err='{captured.err}'"
        record = json.loads(line)
        assert record["event"] == "hello"
        assert record["key"] == "val"
        assert "timestamp" in record
        assert record["level"] == "info"

    def test_contextvars_injected(self, capsys):
        setup_logging(json_logs=True, log_level="INFO")
        session_id_ctx.set("sess_abc123")
        device_id_ctx.set("dev-001")
        seq_ctx.set(42)
        epoch_ctx.set(3)

        logger = get_logger("test_ctx")
        logger.info("ctx test")

        session_id_ctx.set(None)
        device_id_ctx.set(None)
        seq_ctx.set(None)
        epoch_ctx.set(None)

        captured = capsys.readouterr()
        line = captured.out.strip() or captured.err.strip()
        record = json.loads(line)
        assert record["session_id"] == "sess_abc123"
        assert record["device_id"] == "dev-001"
        assert record["seq"] == 42
        assert record["epoch"] == 3

    def test_contextvars_omitted_when_unset(self, capsys):
        setup_logging(json_logs=True, log_level="INFO")
        # Ensure none are set
        session_id_ctx.set(None)
        device_id_ctx.set(None)

        logger = get_logger("test_noctx")
        logger.info("no context")

        captured = capsys.readouterr()
        line = captured.out.strip() or captured.err.strip()
        record = json.loads(line)
        assert "session_id" not in record
        assert "device_id" not in record

    def test_api_key_redacted(self, capsys):
        setup_logging(json_logs=True, log_level="INFO")
        logger = get_logger("test_redact")
        logger.info("auth", api_key="sk-super-secret")

        captured = capsys.readouterr()
        line = captured.out.strip() or captured.err.strip()
        record = json.loads(line)
        assert record["api_key"] == "[redacted]"

    def test_url_query_token_redacted(self, capsys):
        setup_logging(json_logs=True, log_level="INFO")
        logger = get_logger("test_url_redact")
        logger.info("connect", url="ws://hermes:9119/api/ws?token=super-secret&client=1")

        captured = capsys.readouterr()
        line = captured.out.strip() or captured.err.strip()
        record = json.loads(line)
        parsed = urlsplit(record["url"])
        query = dict(parse_qsl(parsed.query))
        assert query["token"] == "[redacted]"
        assert query["client"] == "1"
        assert "super-secret" not in record["url"]

    def test_nested_url_query_token_redacted(self, capsys):
        setup_logging(json_logs=True, log_level="INFO")
        logger = get_logger("test_nested_url_redact")
        logger.info("connect", extra={"url": "ws://hermes:9119/api/ws?token=super-secret"})

        captured = capsys.readouterr()
        line = captured.out.strip() or captured.err.strip()
        record = json.loads(line)
        assert "super-secret" not in record["extra"]["url"]
        assert "redacted" in record["extra"]["url"]

    def test_redact_url_preserves_non_sensitive_query(self):
        url = "ws://hermes:9119/api/ws?mode=chat&client=1"
        assert redact_url(url) == url

    def test_non_string_nested_keys_do_not_crash(self):
        event = _redact_sensitive(None, None, {"meta": {1: "value"}, "url": "ws://h?token=secret"})
        assert event["meta"][1] == "value"
        assert "secret" not in event["url"]

    def test_console_renderer_no_crash(self, capsys):
        """Non-JSON mode should not crash and should produce human-readable output."""
        setup_logging(json_logs=False, log_level="INFO")
        logger = get_logger("test_console")
        logger.info("console test", fruit="apple")

        captured = capsys.readouterr()
        # Console renderer output should contain the event and key
        assert "console test" in captured.err or "console test" in captured.out


class TestLogFile:
    """文件轮转（docs/server_logging.md）：默认 file=None 零副作用；启用后 RotatingFileHandler。"""

    def test_default_none_no_side_effect(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        setup_logging(json_logs=True, log_level="INFO")
        assert list(tmp_path.iterdir()) == []

    def test_enabled_creates_dir_and_writes_json(self, tmp_path):
        from shared.config.models import LogFileConfig

        cfg = LogFileConfig(dir=str(tmp_path / "logs"))
        setup_logging(json_logs=True, log_level="INFO", file=cfg)
        get_logger("test_file").info("file hello", key="v")

        log_file = tmp_path / "logs" / "livachat.log"
        assert log_file.exists()
        record = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert record["event"] == "file hello"
        assert record["key"] == "v"

    def test_rotation_and_backup_cap(self, tmp_path):
        from shared.config.models import LogFileConfig

        cfg = LogFileConfig(dir=str(tmp_path / "logs"), max_bytes=512, backup_count=2)
        setup_logging(json_logs=True, log_level="INFO", file=cfg)
        logger = get_logger("test_rot")
        for i in range(200):
            logger.info("rotate me", i=i)

        d = tmp_path / "logs"
        assert (d / "livachat.log").exists()
        assert (d / "livachat.log.1").exists()
        assert (d / "livachat.log.2").exists()
        assert not (d / "livachat.log.3").exists()  # 份数封顶：只保留 backup_count 份

    def test_disabled_no_file(self, tmp_path):
        from shared.config.models import LogFileConfig

        cfg = LogFileConfig(enabled=False, dir=str(tmp_path / "logs"))
        setup_logging(json_logs=True, log_level="INFO", file=cfg)
        assert not (tmp_path / "logs").exists()

    def test_unusable_dir_fails_fast(self, tmp_path):
        from shared.config.models import LogFileConfig

        blocker = tmp_path / "blocked"
        blocker.write_text("file, not dir", encoding="utf-8")
        cfg = LogFileConfig(dir=str(blocker))
        with pytest.raises(RuntimeError, match="log dir not usable"):
            setup_logging(json_logs=True, log_level="INFO", file=cfg)

    def test_file_enabled_writes_only_file(self, tmp_path, capsys):
        """运行态（file 激活）仅挂 RotatingFileHandler，stdout 零输出（docs/server_logging.md §8）。"""
        from shared.config.models import LogFileConfig

        setup_logging(json_logs=True, log_level="INFO",
                      file=LogFileConfig(dir=str(tmp_path / "logs")))
        root = logging.getLogger()
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
        assert not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
                       for h in root.handlers)

        get_logger("test_file_only").info("file only")
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""
        assert (tmp_path / "logs" / "livachat.log").exists()

    def test_stdout_fallback_when_file_inactive(self, capsys):
        """file=None → stdout 兜底（§8 D1 回归）。"""
        setup_logging(json_logs=True, log_level="INFO")
        root = logging.getLogger()
        assert any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
                   for h in root.handlers)

        get_logger("test_fallback").info("fallback")
        assert "fallback" in capsys.readouterr().out
