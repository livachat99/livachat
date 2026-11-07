from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from llm.prompt import load_system_prompt
from shared.utils.errors import ConfigError


class TestLoadSystemPrompt:
    def test_loads_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("You are helpful.\nBe concise.\n")
            path = f.name

        try:
            prompt = load_system_prompt(path, env_override="")
            assert prompt == "You are helpful.\nBe concise."
        finally:
            Path(path).unlink()

    def test_file_not_found_raises(self):
        with pytest.raises(ConfigError, match="not found"):
            load_system_prompt("/nonexistent/path.txt", env_override="")

    def test_env_override(self, monkeypatch, tmp_path):
        override = tmp_path / "override.txt"
        override.write_text("Override prompt")

        # Point to a bad path but override via env
        monkeypatch.setenv("TEST_OVERRIDE_PATH", str(override))
        prompt = load_system_prompt("/nonexistent", env_override="TEST_OVERRIDE_PATH")
        assert prompt == "Override prompt"
