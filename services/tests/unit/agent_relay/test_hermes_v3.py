"""Unit tests for the v3 job-session carrier helpers/config."""

from __future__ import annotations

from agent_relay.adapters.hermes import (
    _extract_job_result,
    _is_session_job_ref,
    _stored_session_from_ref,
    classify_job_result,
)
from agent_relay.config import HermesAdapterConfig


class TestClassifyJobResult:
    def test_noop_case_insensitive(self):
        assert classify_job_result(" NOOP ") == ("noop", "")

    def test_error_prefix(self):
        assert classify_job_result("error: boom") == ("rpc_error", "")

    def test_empty(self):
        assert classify_job_result("") == ("empty", "")
        assert classify_job_result("   \n") == ("empty", "")

    def test_deliver_preserves_text(self):
        assert classify_job_result("机票 2400 元") == ("deliver", "机票 2400 元")


class TestExtractJobResult:
    def test_completed(self):
        messages = [
            {"role": "user", "text": "task"},
            {"role": "tool", "text": "..."},
            {"role": "assistant", "text": "JOB_DONE"},
        ]
        assert _extract_job_result(messages) == ("text", "JOB_DONE")

    def test_interrupted_sentinel(self):
        messages = [
            {"role": "user", "text": "task"},
            {"role": "assistant", "text": "Operation interrupted."},
        ]
        assert _extract_job_result(messages) == ("interrupted", "")

    def test_error_sentinel(self):
        messages = [
            {"role": "user", "text": "task"},
            {"role": "assistant", "text": "Error: provider 400"},
        ]
        assert _extract_job_result(messages) == ("run_error", "")

    def test_error_sentinel_with_leading_whitespace(self):
        messages = [
            {"role": "user", "text": "task"},
            {"role": "assistant", "text": "\nError: provider 400"},
        ]
        assert _extract_job_result(messages) == ("run_error", "")

    def test_empty_assistant_becomes_text_empty(self):
        messages = [
            {"role": "user", "text": "task"},
            {"role": "assistant", "text": ""},
        ]
        assert _extract_job_result(messages) == ("text", "")

    def test_no_assistant(self):
        assert _extract_job_result([{"role": "user", "text": "task"}]) == ("none", "")

    def test_no_user(self):
        assert _extract_job_result([{"role": "assistant", "text": "x"}]) == ("none", "")


class TestJobRef:
    def test_session_ref(self):
        assert _is_session_job_ref("session:20260101_000000_abcdef")
        assert _stored_session_from_ref("session:20260101_000000_abcdef") == "20260101_000000_abcdef"

    def test_non_session_ref_is_rejected(self):
        assert not _is_session_job_ref("task-abc123")
        assert _stored_session_from_ref("task-abc123") == ""


class TestV3Config:
    def test_defaults(self):
        cfg = HermesAdapterConfig()
        assert cfg.job_cancel_kill_processes is True
        assert cfg.job_cancel_grace_s == 5.0
        assert cfg.job_recovery_enabled is True
        assert cfg.job_resume_recheck_s == 10.0
