from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from shared.config import loader as loader_mod
from shared.config.loader import _find_config_path, _resolve_env_vars, _resolve_placeholders, load_config
from shared.config.models import AppConfig
from shared.config.validation import validate_config
from shared.utils.errors import ConfigError


@pytest.fixture(autouse=True)
def _isolate_dotenv(tmp_path, monkeypatch):
    """单测不读开发者本机仓库根 .env（与 CI 无 .env 的行为保持一致）。"""
    monkeypatch.setattr(loader_mod, "_dotenv_path", lambda: tmp_path / "no-such.env")

# ---------------------------------------------------------------------------
# _resolve_env_vars
# ---------------------------------------------------------------------------


class TestResolveEnvVars:
    def test_plain_string_unchanged(self):
        assert _resolve_env_vars("hello") == "hello"

    def test_single_placeholder(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret123")
        assert _resolve_env_vars("${MY_KEY}") == "secret123"

    def test_mixed_placeholder(self, monkeypatch):
        monkeypatch.setenv("KEY", "bar")
        assert _resolve_env_vars("foo_${KEY}_baz") == "foo_bar_baz"

    def test_missing_placeholder_raises(self):
        with pytest.raises(ConfigError, match="MISSING_VAR"):
            _resolve_env_vars("${MISSING_VAR}")

    def test_multiple_placeholders(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _resolve_env_vars("${A}-${B}") == "1-2"


class TestResolvePlaceholders:
    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("K", "v")
        result = _resolve_placeholders({"a": {"b": "${K}"}, "c": [{"d": "${K}"}]})
        assert result == {"a": {"b": "v"}, "c": [{"d": "v"}]}

    def test_list_of_strings(self, monkeypatch):
        monkeypatch.setenv("X", "xval")
        assert _resolve_placeholders(["${X}", "static"]) == ["xval", "static"]


# ---------------------------------------------------------------------------
# _find_config_path
# ---------------------------------------------------------------------------


class TestFindConfigPath:
    def test_default_dev(self):
        path = _find_config_path(None)
        assert path.name == "server.yaml"
        assert "configs/dev" in str(path)

    def test_explicit_env(self):
        # 'dev' should resolve to configs/dev/server.yaml
        path = _find_config_path("dev")
        assert path.exists()

    def test_missing_env_raises(self):
        with pytest.raises(ConfigError, match="not found"):
            _find_config_path("nonexistent_env_xyz")

    def test_explicit_path_via_env(self, monkeypatch, tmp_path):
        yml = tmp_path / "custom.yaml"
        yml.write_text("server:\n  host: 0.0.0.0\n  port: 9999")
        monkeypatch.setenv("LIVA_CHAT_CONFIG", str(yml))
        path = _find_config_path(None)
        assert path == yml


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_load_dev_config(self, monkeypatch):
        """Minimal smoke: load dev config with required env vars injected."""
        monkeypatch.setenv("APP_AUTH_TOKEN", "test-token")
        monkeypatch.setenv("OPENCLAW_AUTH_TOKEN", "oc-test-token")
        monkeypatch.setenv("HERMES_AUTH_TOKEN", "hermes-test-token")
        monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "ds-key")
        monkeypatch.setenv("DASHSCOPE_API_HOST", "llm-test.cn-beijing.maas.aliyuncs.com")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "llm-test")
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "dsk-key")
        monkeypatch.setenv("CARTESIA_API_KEY", "cart-key")
        monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
        monkeypatch.setenv("LIVACHAT__REDIS__URL", "redis://localhost:6379/0")

        cfg = load_config("dev")
        assert cfg.server.port == 8080
        assert cfg.auth.mode == "dev"
        assert cfg.auth.dev_token == "test-token"
        # 与 configs/dev/server.yaml 当前 provider 选择一致
        assert cfg.asr.active == "alibaba_bailian"
        assert cfg.llm.active == "alibaba_bailian"
        assert cfg.tts.active == "alibaba_bailian"
        # 39e91cd: 音频采样率改为 16kHz
        assert cfg.media.audio.default_sample_rate == 16000

    def test_env_override(self, monkeypatch):
        """LIVACHAT__ env var overrides YAML value."""
        monkeypatch.setenv("APP_AUTH_TOKEN", "token")
        monkeypatch.setenv("OPENCLAW_AUTH_TOKEN", "oc-test-token")
        monkeypatch.setenv("HERMES_AUTH_TOKEN", "hermes-test-token")
        monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "ds-key")
        monkeypatch.setenv("DASHSCOPE_API_HOST", "llm-test.cn-beijing.maas.aliyuncs.com")
        monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "llm-test")
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "dsk-key")
        monkeypatch.setenv("CARTESIA_API_KEY", "cart-key")
        monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
        monkeypatch.setenv("LIVACHAT__REDIS__URL", "x")
        monkeypatch.setenv("LIVACHAT__ASR__ACTIVE", "alibaba_bailian")

        cfg = load_config("dev")
        assert cfg.asr.active == "alibaba_bailian"

    def test_missing_env_var_fails(self):
        """A ${MISSING} placeholder without a corresponding env var must fail."""
        with tempfile.TemporaryDirectory() as td:
            yml = Path(td) / "server.yaml"
            yml.write_text(
                'asr:\n  active: deepgram\n  fallback: []\n  language: zh-CN\n  providers:\n    deepgram:\n      enabled: true\n      api_key: "${MISSING_VAR}"\n      model: m\nserver:\n  host: 0.0.0.0\n  port: 8080'
            )
            # We can't use load_config because it looks in configs/, so test the resolver directly
            import yaml as _yaml

            raw = _yaml.safe_load(yml.read_text())
            with pytest.raises(ConfigError, match="MISSING_VAR"):
                _resolve_placeholders(raw)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


class TestValidation:
    def _base_config(self) -> AppConfig:
        cfg = AppConfig()
        cfg.auth.mode = "dev"
        cfg.auth.dev_token = "test-token"
        return cfg

    def test_active_not_in_providers(self):
        cfg = self._base_config()
        cfg.asr.active = "nonexistent"
        with pytest.raises(ConfigError, match="asr.active"):
            validate_config(cfg)

    def test_active_disabled(self):
        cfg = self._base_config()
        cfg.asr.active = "deepgram"
        cfg.asr.providers["deepgram"] = cfg.asr.providers.get(
            "deepgram",
            __import__("shared.config.models", fromlist=["AsrProviderConfig"]).AsrProviderConfig(
                enabled=False, base_url="", api_key="", model=""
            ),
        )
        from shared.config.models import AsrProviderConfig

        cfg.asr.providers["deepgram"] = AsrProviderConfig(enabled=False)
        with pytest.raises(ConfigError, match="disabled"):
            validate_config(cfg)

    def test_fallback_disabled(self):
        cfg = self._base_config()
        from shared.config.models import AsrProviderConfig

        cfg.asr.active = "deepgram"
        cfg.asr.providers["deepgram"] = AsrProviderConfig(enabled=True)
        cfg.asr.fallback = ["alibaba_bailian"]
        cfg.asr.providers["alibaba_bailian"] = AsrProviderConfig(enabled=False)
        with pytest.raises(ConfigError, match="disabled"):
            validate_config(cfg)

    def test_fallback_not_in_providers(self):
        cfg = self._base_config()
        from shared.config.models import AsrProviderConfig

        cfg.asr.active = "deepgram"
        cfg.asr.providers["deepgram"] = AsrProviderConfig(enabled=True)
        cfg.asr.fallback = ["ghost"]
        with pytest.raises(ConfigError, match="fallback"):
            validate_config(cfg)

    def test_invalid_sample_rate(self):
        cfg = self._base_config()
        cfg.media.audio.default_sample_rate = 8000
        with pytest.raises(ConfigError, match="sample_rate"):
            validate_config(cfg)

    def test_invalid_opus_frame_ms(self):
        cfg = self._base_config()
        cfg.media.audio.opus_frame_ms = 60
        with pytest.raises(ConfigError, match="opus_frame_ms"):
            validate_config(cfg)

    def test_invalid_audio_channels(self):
        cfg = self._base_config()
        cfg.media.audio.channels = 2
        with pytest.raises(ConfigError, match="channels"):
            validate_config(cfg)

    def test_auth_dev_missing_token(self):
        cfg = self._base_config()
        cfg.auth.mode = "dev"
        cfg.auth.dev_token = ""
        with pytest.raises(ConfigError, match="dev_token"):
            validate_config(cfg)

    def test_auth_jwt_missing_secret(self):
        cfg = self._base_config()
        cfg.auth.mode = "jwt"
        cfg.auth.jwt_secret = ""
        with pytest.raises(ConfigError, match="jwt_secret"):
            validate_config(cfg)

    def test_auth_unknown_mode(self):
        cfg = self._base_config()
        cfg.auth.mode = "oauth"
        with pytest.raises(ConfigError, match="auth.mode"):
            validate_config(cfg)


# ---------------------------------------------------------------------------
# dotenv 自动加载（仓库根 .env；docs/ali_bailian_provider.md §9）
# ---------------------------------------------------------------------------

_MINIMAL_SERVER_YAML = """
auth: {mode: dev, dev_token: t}
asr:
  active: p1
  providers:
    p1: {enabled: true, base_url: "wss://${DS_HOST}/asr", api_key: "${DS_KEY}"}
llm:
  active: p1
  providers:
    p1: {enabled: true, base_url: "https://${DS_HOST}/llm", api_key: k}
tts:
  active: p1
  providers:
    p1: {enabled: true, base_url: "wss://${DS_HOST}/tts", api_key: k}
"""


class TestDotenvLoading:
    def _project(self, tmp_path: Path, dotenv_text: str) -> Path:
        root = tmp_path / "proj"
        (root / "configs" / "dev").mkdir(parents=True)
        (root / "configs" / "dev" / "server.yaml").write_text(
            _MINIMAL_SERVER_YAML, encoding="utf-8")
        (root / ".env").write_text(dotenv_text, encoding="utf-8")
        return root

    def _use(self, monkeypatch, root: Path) -> None:
        monkeypatch.setattr(loader_mod, "_dotenv_path", lambda: root / ".env")
        monkeypatch.setenv("LIVA_CHAT_CONFIG", str(root / "configs" / "dev" / "server.yaml"))
        # _load_dotenv 直接写 os.environ（monkeypatch 不追踪）→ 逐例清理，防同进程串味
        for key in ("DS_HOST", "DS_KEY"):
            monkeypatch.delenv(key, raising=False)

    def test_dotenv_supplies_placeholders(self, tmp_path, monkeypatch):
        root = self._project(tmp_path, "# comment\n\nexport DS_HOST='host-1'\nDS_KEY=\"k-1\"\n")
        self._use(monkeypatch, root)
        cfg = load_config()
        assert cfg.asr.providers["p1"].base_url == "wss://host-1/asr"
        assert cfg.asr.providers["p1"].api_key == "k-1"

    def test_duplicate_key_last_wins_and_export_tab(self, tmp_path, monkeypatch):
        # 同键后行覆盖（对齐 set -a && source .env）；export 后接 Tab 亦容忍
        root = self._project(tmp_path, "DS_HOST=old\nexport\tDS_KEY='k-1'\nDS_HOST=new\n")
        self._use(monkeypatch, root)
        cfg = load_config()
        assert cfg.asr.providers["p1"].base_url == "wss://new/asr"
        assert cfg.asr.providers["p1"].api_key == "k-1"

    def test_shell_env_wins(self, tmp_path, monkeypatch):
        root = self._project(tmp_path, "DS_HOST=from-env\nDS_KEY=k\n")
        self._use(monkeypatch, root)
        monkeypatch.setenv("DS_HOST", "from-shell")
        cfg = load_config()
        assert cfg.asr.providers["p1"].base_url == "wss://from-shell/asr"

    def test_missing_dotenv_keeps_failfast(self, tmp_path, monkeypatch):
        root = self._project(tmp_path, "")
        (root / ".env").unlink()
        self._use(monkeypatch, root)
        with pytest.raises(ConfigError, match="DS_HOST"):
            load_config()
