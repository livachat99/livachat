"""P0：lvdata_mcp.yaml 加载（§4.1）+ schema（§4.3）+ fail-fast（§13）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from lvdata_mcp.config import (
    SUPPORTED_LANGUAGES,
    LvdataMcpConfig,
    effective_summary,
    load_config,
)


def _write_yaml(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolate_dotenv(tmp_path, monkeypatch):
    """单测不读开发者本机仓库根 .env（与 CI 无 .env 的行为保持一致）。"""
    monkeypatch.setattr("lvdata_mcp.config._dotenv_path", lambda: tmp_path / "no-such.env")


# §13：load_config 校验 provider api_key（embedding 整块为空 = 关闭，不校验）
_PROVIDERS_YAML = """
providers:
  audio:
    api_key: sk-audio
  vision:
    api_key: sk-vision
  summary:
    api_key: sk-summary
"""


# ── 加载链 ───────────────────────────────────────────────────────────────

def test_load_from_explicit_path(tmp_path):
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", """
server:
  host: "0.0.0.0"
  port: 8091
  token: "t"
data:
  root_dir: /tmp/lvdata-x
watcher:
  timezone: America/New_York
  concurrency: 2
providers:
  audio:
    api_key: "${DASHSCOPE_API_KEY}"
  vision:
    api_key: sk-vision
  summary:
    api_key: sk-summary
""")
    os.environ["DASHSCOPE_API_KEY"] = "sk-test"
    try:
        cfg = load_config(explicit=cfg_path)
    finally:
        del os.environ["DASHSCOPE_API_KEY"]
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8091
    assert cfg.data.root_dir == Path("/tmp/lvdata-x")
    assert cfg.watcher.timezone == "America/New_York"
    assert cfg.watcher.concurrency == 2
    assert cfg.providers.audio.api_key == "sk-test"


def test_load_config_env_var_path(tmp_path, monkeypatch):
    cfg_path = _write_yaml(tmp_path / "custom.yaml",
                           "server:\n  port: 9000\n"
                           + _PROVIDERS_YAML)
    monkeypatch.setenv("LVDATA_MCP_CONFIG", str(cfg_path))
    cfg = load_config()
    assert cfg.server.port == 9000


def test_load_missing_file_fails(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="does not exist"):
        load_config(explicit=tmp_path / "nope.yaml")
    monkeypatch.setenv("LVDATA_MCP_CONFIG", str(tmp_path / "nope.yaml"))
    with pytest.raises(ValueError, match="does not exist"):
        load_config()


def test_no_config_anywhere_fails(monkeypatch, tmp_path):
    monkeypatch.delenv("LVDATA_MCP_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)  # CWD 也无 lvdata_mcp.yaml
    monkeypatch.setattr("lvdata_mcp.config._project_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="not found"):
        load_config()


class TestLanguage:
    def test_default_is_en(self):
        assert LvdataMcpConfig().language == "en"

    @pytest.mark.parametrize("code", SUPPORTED_LANGUAGES)
    def test_supported_codes_pass_through(self, code):
        assert LvdataMcpConfig(language=code).language == code

    @pytest.mark.parametrize("value", ["", "  ", None, "tr", "zh", "en-US", 42])
    def test_invalid_falls_back_to_en(self, value):
        assert LvdataMcpConfig(language=value).language == "en"

    def test_invalid_logs_warning(self, caplog):
        with caplog.at_level("WARNING", logger="lvdata_mcp.config"):
            LvdataMcpConfig(language="tr")
        assert "falling back to en" in caplog.text

    def test_effective_summary_exposes_language(self):
        assert effective_summary(LvdataMcpConfig(language="ja"))["language"] == "ja"


def test_env_placeholder_unset_fails(tmp_path):
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", """
providers:
  audio:
    api_key: "${MISSING_VAR_XYZ}"
""")
    with pytest.raises(ValueError, match="MISSING_VAR_XYZ"):
        load_config(explicit=cfg_path)


def test_dotenv_supplies_placeholders(tmp_path, monkeypatch):
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", """
providers:
  audio:
    api_key: "${DS_KEY}"
  vision:
    api_key: sk-vision
  summary:
    api_key: sk-summary
""")
    dotenv = tmp_path / ".env"
    dotenv.write_text("# comment\n\nexport DS_KEY='k-1'\n", encoding="utf-8")
    monkeypatch.setattr("lvdata_mcp.config._dotenv_path", lambda: dotenv)
    monkeypatch.delenv("DS_KEY", raising=False)  # _load_dotenv 直写 os.environ，逐例清理
    cfg = load_config(explicit=cfg_path)
    assert cfg.providers.audio.api_key == "k-1"


def test_dotenv_duplicate_key_last_wins(tmp_path, monkeypatch):
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", """
providers:
  audio:
    api_key: "${DS_KEY}"
  vision:
    api_key: sk-vision
  summary:
    api_key: sk-summary
""")
    (tmp_path / ".env").write_text(
        "DS_KEY=old\nexport\tDS_KEY='k-new'\n", encoding="utf-8")
    monkeypatch.setattr("lvdata_mcp.config._dotenv_path", lambda: tmp_path / ".env")
    monkeypatch.delenv("DS_KEY", raising=False)
    cfg = load_config(explicit=cfg_path)
    assert cfg.providers.audio.api_key == "k-new"


def test_shell_env_wins_over_dotenv(tmp_path, monkeypatch):
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", """
providers:
  audio:
    api_key: "${DS_KEY}"
  vision:
    api_key: sk-vision
  summary:
    api_key: sk-summary
""")
    (tmp_path / ".env").write_text("DS_KEY=from-env\n", encoding="utf-8")
    monkeypatch.setattr("lvdata_mcp.config._dotenv_path", lambda: tmp_path / ".env")
    monkeypatch.setenv("DS_KEY", "from-shell")
    cfg = load_config(explicit=cfg_path)
    assert cfg.providers.audio.api_key == "from-shell"


def test_env_override_nested(tmp_path, monkeypatch):
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", """
server:
  port: 8090
watcher:
  poll_interval_s: 60
  timezone: Asia/Shanghai
""" + _PROVIDERS_YAML)
    monkeypatch.setenv("LVDATA_MCP__SERVER__PORT", "8443")
    monkeypatch.setenv("LVDATA_MCP__WATCHER__POLL_INTERVAL_S", "30")
    cfg = load_config(explicit=cfg_path)
    assert cfg.server.port == 8443
    assert cfg.watcher.poll_interval_s == 30
    assert cfg.watcher.timezone == "Asia/Shanghai"


def test_defaults_when_yaml_minimal(tmp_path):
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", _PROVIDERS_YAML)
    cfg = load_config(explicit=cfg_path)
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8090
    assert cfg.watcher.poll_interval_s == 60
    assert cfg.watcher.timezone == "Asia/Shanghai"
    assert cfg.data.resolved_db_path == Path("/opt/lvdata") / "lvdata_inventory.db"


def test_log_file_defaults(tmp_path):
    """docs/server_logging.md §7：默认 project_root/logs、50MB、4 份、enabled。"""
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", _PROVIDERS_YAML)
    cfg = load_config(explicit=cfg_path)
    assert cfg.server.log_file.enabled is True
    assert cfg.server.log_file.dir == ""
    assert cfg.server.log_file.max_bytes == 50 * 1024 * 1024
    assert cfg.server.log_file.backup_count == 3


def test_log_file_env_override(tmp_path, monkeypatch):
    """yaml 未写 log_file 时经 pydantic 强转；写出时走 _set_nested 同型强转。"""
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", _PROVIDERS_YAML)
    monkeypatch.setenv("LVDATA_MCP__SERVER__LOG_FILE__ENABLED", "false")
    monkeypatch.setenv("LVDATA_MCP__SERVER__LOG_FILE__MAX_BYTES", "1024")
    cfg = load_config(explicit=cfg_path)
    assert cfg.server.log_file.enabled is False
    assert cfg.server.log_file.max_bytes == 1024


def test_log_file_invalid_values_fail(tmp_path):
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", _PROVIDERS_YAML + """
server:
  log_file:
    max_bytes: 0
""")
    with pytest.raises(ValueError, match="max_bytes"):
        load_config(explicit=cfg_path)


def test_load_config_missing_api_key_fails(tmp_path):
    """§13：load_config 启动即校验 provider api_key（fail-fast）。"""
    cfg_path = _write_yaml(tmp_path / "lvdata_mcp.yaml", """
providers:
  audio:
    model: qwen3.5-omni-plus
""")
    with pytest.raises(ValueError, match="audio.*api_key"):
        load_config(explicit=cfg_path)


# ── provider 解析（§4.3 vendor 默认值）──────────────────────────────────

def test_provider_vendor_defaults():
    cfg = LvdataMcpConfig(
        providers={"audio": {"api_key": "k"}, "vision": {"api_key": "k"},
                   "summary": {"api_key": "k"},
                   "embedding": {"api_key": "k", "dim": 1024}})
    r = cfg.providers.resolved()
    assert r.audio.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert r.audio.model == "qwen3.8-omni-flash"
    assert r.vision.model == "qwen3.7-flash"
    assert r.summary.model == "qwen3.7-flash"
    assert r.embedding.model == "text-embedding-v4"
    assert r.embedding.dim == 1024
    # dim 缺省 None（§4.3 schema）；yaml 显式配 1024（§4.2 示例）
    cfg2 = LvdataMcpConfig(
        providers={"audio": {"api_key": "k"}, "vision": {"api_key": "k"},
                   "summary": {"api_key": "k"}})
    assert cfg2.providers.embedding.dim is None


def test_provider_workspace_id_passthrough():
    """workspace_id 必须经 resolved() 透传——它是白名单构造，漏传即静默丢头
    （chat server 同款教训：services/tests/unit/asr/test_factory.py）。
    全能力覆盖：vision/summary/embedding 同属 workspace 接入范围。"""
    cfg = LvdataMcpConfig(
        providers={"audio": {"api_key": "k", "workspace_id": "llm-x"},
                   "vision": {"api_key": "k", "workspace_id": "llm-v"},
                   "summary": {"api_key": "k", "workspace_id": "llm-s"},
                   "embedding": {"api_key": "k", "dim": 1024,
                                 "workspace_id": "llm-e"}})
    r = cfg.providers.resolved()
    assert r.audio.workspace_id == "llm-x"
    assert r.vision.workspace_id == "llm-v"
    assert r.summary.workspace_id == "llm-s"
    assert r.embedding.workspace_id == "llm-e"
    # 缺省 None（全能力）
    cfg2 = LvdataMcpConfig(
        providers={"audio": {"api_key": "k"}, "vision": {"api_key": "k"},
                   "summary": {"api_key": "k"}, "embedding": {"api_key": "k"}})
    r2 = cfg2.providers.resolved()
    assert all(getattr(r2, cap).workspace_id is None
               for cap in ("audio", "vision", "summary", "embedding"))


def test_provider_explicit_overrides_vendor():
    cfg = LvdataMcpConfig(
        providers={"audio": {"vendor": "openai", "model": "gpt-4o-audio",
                             "base_url": "https://example.com/v1", "api_key": "k"}})
    r = cfg.providers.resolved()
    assert r.audio.base_url == "https://example.com/v1"
    assert r.audio.model == "gpt-4o-audio"


def test_api_key_missing_fail_fast():
    cfg = LvdataMcpConfig(providers={"audio": {"model": "m"}})
    with pytest.raises(ValueError, match="audio.*api_key"):
        cfg.providers.validate_required()


def test_embedding_empty_block_disabled():
    cfg = LvdataMcpConfig(
        providers={"audio": {"api_key": "k"}, "vision": {"api_key": "k"},
                   "summary": {"api_key": "k"}})
    assert cfg.providers.embedding_enabled() is False
    cfg.providers.validate_required()  # 不抛：embedding 整块为空 = 关闭
    # embedding 有 model 但无 api_key → fail-fast
    cfg2 = LvdataMcpConfig(
        providers={"audio": {"api_key": "k"}, "vision": {"api_key": "k"},
                   "summary": {"api_key": "k"},
                   "embedding": {"model": "text-embedding-v4"}})
    with pytest.raises(ValueError, match="embedding.*api_key"):
        cfg2.providers.validate_required()


# ── 安全校验（§4.1.4）───────────────────────────────────────────────────

def test_non_loopback_requires_token():
    cfg = LvdataMcpConfig(server={"host": "0.0.0.0"})
    with pytest.raises(ValueError, match="LVDATA_MCP_TOKEN"):
        cfg.validate_security()
    cfg2 = LvdataMcpConfig(server={"host": "0.0.0.0", "token": "t"})
    cfg2.validate_security()


def test_loopback_no_token_ok():
    LvdataMcpConfig(server={"host": "127.0.0.1"}).validate_security()


def test_effective_summary_omits_secrets_and_endpoints():
    """启动摘要只打非敏感生效值：base_url/api_key/workspace_id 一律不打（2026-09-28）。

    日志会被采集/转发——api_key 即使打码也泄前后缀，base_url/workspace_id 是内部端点与
    账号标识（互为副本）。回归护栏：三者不得以任何形式（含打码）回到摘要里。
    """
    import json as _json

    cfg = LvdataMcpConfig(
        providers={"audio": {"api_key": "sk-abcdefgh12345678",
                             "base_url": "https://internal.example.com/v1",
                             "workspace_id": "llm-x", "model": "m"},
                   "embedding": {"api_key": "sk-abcdefgh12345678",
                                 "base_url": "https://internal.example.com/v1",
                                 "workspace_id": "llm-x", "model": "e"}})
    s = effective_summary(cfg)
    dump = _json.dumps(s, ensure_ascii=False)
    for leak in ("sk-abcdefgh12345678", "sk-a…5678", "internal.example.com", "llm-x"):
        assert leak not in dump, leak
    a = s["providers"]["audio"]
    assert set(a) == {"vendor", "model"}          # 只剩非敏感项
    assert s["providers"]["embedding"]["model"] == "e"


def test_min_vec_score_survives_resolve(tmp_path, monkeypatch):
    """§15 白名单护栏：yaml 的 `min_vec_score` 必须穿过 `ProviderConfig.resolved()`。

    `resolved()` 是**手工白名单构造**（config.py 自带"新增字段必须显式透传，否则静默丢"
    的教训注释）——漏传时工具读到的是字段默认值 0.50，yaml 里复标的新 τ **静默失效**。
    本用例覆盖 yaml → load_config → resolved() 全链，与工具实际读取位置一致。
    """
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dummy")
    path = _write_yaml(tmp_path / "lvdata_mcp.yaml", """
data: {root_dir: /tmp/x}
providers:
  audio:     {model: a, api_key: "${DASHSCOPE_API_KEY}"}
  vision:    {model: v, api_key: "${DASHSCOPE_API_KEY}"}
  summary:   {model: s, api_key: "${DASHSCOPE_API_KEY}"}
  embedding: {model: m, api_key: "${DASHSCOPE_API_KEY}", dim: 64, min_vec_score: 0.33}
""")
    cfg = load_config(path)
    p = cfg.providers.embedding
    assert p.min_vec_score == 0.33              # 工具读的就是这个位置（store.settings）
    assert p.resolved("embedding").min_vec_score == 0.33   # 白名单必须显式透传
    assert p.resolved("embedding").dim == 64


def test_effective_summary_exposes_embedding_retrieval_params():
    """§15：embedding 的生效检索参数在启动摘要里可见（dim / 相似度下限）。

    背景：providers 段是手工白名单，曾漏掉这两个"会改变检索结果"的字段——
    复标 τ 后无法从日志核对生效值。其余能力不应出现这两个 embedding 专用字段。
    """
    cfg = LvdataMcpConfig(providers={"embedding": {"model": "m", "api_key": "k",
                                                   "dim": 1024}})
    s = effective_summary(cfg)
    assert s["providers"]["embedding"]["dim"] == 1024
    assert s["providers"]["embedding"]["min_vec_score"] == 0.50
    assert "min_vec_score" not in s["providers"]["audio"]
    assert "dim" not in s["providers"]["vision"]
