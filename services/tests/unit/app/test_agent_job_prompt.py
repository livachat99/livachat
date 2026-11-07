"""Unit tests: dialog system prompt provenance 声明（fd_pipeline_llm_v2）。"""

from __future__ import annotations

from app.application import _with_agent_job_declaration
from session.agent_job_ingest import AGENT_JOB_PROMPT_DECLARATION


class TestWithAgentJobDeclaration:
    def test_appends_declaration_to_dialog_prompt(self):
        out = _with_agent_job_declaration("You are a companion.")
        assert out == "You are a companion.\n\n" + AGENT_JOB_PROMPT_DECLARATION

    def test_strips_trailing_whitespace_before_appending(self):
        out = _with_agent_job_declaration("You are a companion.  \n")
        assert out == "You are a companion.\n\n" + AGENT_JOB_PROMPT_DECLARATION

    def test_empty_prompt_unchanged(self):
        assert _with_agent_job_declaration("") == ""

    def test_declaration_content(self):
        assert "[Agent job result]" in AGENT_JOB_PROMPT_DECLARATION
        assert "不要重复播报" in AGENT_JOB_PROMPT_DECLARATION
        assert "…[truncated]…" not in AGENT_JOB_PROMPT_DECLARATION
