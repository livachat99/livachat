from __future__ import annotations

import os

from shared.config.loader import resolve_path
from shared.utils.errors import ConfigError


def load_system_prompt(path: str, *, env_override: str = "LIVACHAT__LLM__SYSTEM_PROMPT_PATH") -> str:
    """Load a system prompt from a text file.

    If the environment variable *env_override* is set, its value is used
    as the file path instead.
    """
    override = os.environ.get(env_override)
    if override:
        path = override

    prompt_path = resolve_path(path)
    if not prompt_path.exists():
        raise ConfigError(f"system prompt file not found: {path}")

    return prompt_path.read_text(encoding="utf-8").strip()
