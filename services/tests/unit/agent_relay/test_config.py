from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_relay.config import AgentRelayConfig


class TestAgentRelayConfig:
    def test_defaults(self):
        cfg = AgentRelayConfig()
        assert cfg.enabled is False
        assert cfg.events_channel == "livachat:agent:events"
        assert cfg.push_channel_prefix == "livachat:push"
        assert cfg.max_reply_bytes == 16384
        assert cfg.default_adapters == ["hermes"]

    def test_legacy_session_history_turns_accepted(self):
        cfg = AgentRelayConfig.model_validate({"session_history_turns": 3})
        assert cfg.enabled is False

    def test_enabled_override(self, monkeypatch):
        monkeypatch.setenv("LIVACHAT__AGENT_RELAY__ENABLED", "true")
        cfg = AgentRelayConfig()
        assert cfg.enabled is True

    def test_adapter_nested_config(self, monkeypatch):
        monkeypatch.setenv("LIVACHAT__AGENT_RELAY__ADAPTERS__HERMES__BASE_URL", "http://hermes:8080")
        cfg = AgentRelayConfig()
        assert cfg.adapters.hermes.base_url == "http://hermes:8080"

    def test_missing_adapter_config_uses_defaults(self):
        cfg = AgentRelayConfig()
        assert cfg.adapters.hermes.base_url == "http://localhost:8080"
        assert cfg.adapters.hermes.timeout_s == 30

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("LIVACHAT__AGENT_RELAY__ADAPTERS__HERMES__API_KEY", "sk-test-key")
        cfg = AgentRelayConfig()
        assert cfg.adapters.hermes.api_key == "sk-test-key"

    def test_max_reply_bytes_zero_is_invalid(self):
        with pytest.raises(ValidationError):
            AgentRelayConfig(max_reply_bytes=0)  # type: ignore[call-arg]

    def test_timeout_s_zero_is_invalid(self):
        with pytest.raises(ValidationError):
            AgentRelayConfig(adapters={"hermes": {"timeout_s": 0}})  # type: ignore[call-arg]

    def test_proactive_debounce_default(self):
        cfg = AgentRelayConfig()
        assert cfg.proactive.debounce_s == 30
        assert cfg.proactive.min_segment_duration_s == 10
        assert cfg.proactive.enabled is True

    def test_openclaw_defaults(self):
        cfg = AgentRelayConfig()
        oc = cfg.adapters.openclaw
        assert oc.ws_url == "ws://127.0.0.1:18789"
        assert oc.mode == "proactive"
        assert oc.request_timeout_s == 30
        assert oc.nudge_timeout_s == 60
        assert oc.silence_timeout_s == 75.0
        assert oc.background_timeout_s == 172800    # 48h 与 JobStore TTL 对齐
        assert oc.job_parent_link is True

    def test_v2_hermes_defaults(self):
        """§6: v2 配置默认值（48h 看门狗、恢复/退避、结果存储）。"""
        cfg = AgentRelayConfig()
        h = cfg.adapters.hermes
        assert h.background_timeout_s == 172800          # 48h 与 JobStore TTL 对齐
        assert h.startup_recovery_enabled is True
        assert h.recovery_concurrency == 10
        assert h.session_resume_retries == 3
        assert h.reconnect_backoff_initial_s == 1.0
        assert h.reconnect_backoff_max_s == 30.0
        assert h.reconnect_jitter == 0.25
        assert h.max_stored_result_bytes == 16384
        assert h.deliver_pending_on_reconnect is True
        assert h.session_retention == "keep"
        assert h.device_conn_idle_reaper_s == 60.0
        assert h.max_device_connections == 1000
        assert h.max_concurrent_jobs_per_device == 10

    def test_liveness_probe_defaults_and_validation(self):
        """§5.8: 保活探测默认值 + 非法值拒绝（timeout=0 会恒超时 → 拆连循环）。"""
        h = AgentRelayConfig().adapters.hermes
        assert h.liveness_probe_interval_s == 30.0
        assert h.liveness_probe_timeout_s == 5.0
        assert h.liveness_probe_failures == 2

        with pytest.raises(ValidationError):
            AgentRelayConfig(adapters={"hermes": {"liveness_probe_interval_s": -1}})
        with pytest.raises(ValidationError):
            AgentRelayConfig(adapters={"hermes": {"liveness_probe_timeout_s": 0}})
        with pytest.raises(ValidationError):
            AgentRelayConfig(adapters={"hermes": {"liveness_probe_failures": 0}})

    def test_legacy_quota_alias_migrates(self):
        """§5.8: 仅旧键被设置时迁移到新键；新键优先。"""
        cfg = AgentRelayConfig(adapters={"hermes": {"max_concurrent_jobs_per_session": 3}})
        assert cfg.adapters.hermes.max_concurrent_jobs_per_device == 3

    def test_new_quota_key_takes_precedence(self):
        cfg = AgentRelayConfig(adapters={
            "hermes": {"max_concurrent_jobs_per_session": 3, "max_concurrent_jobs_per_device": 7},
        })
        assert cfg.adapters.hermes.max_concurrent_jobs_per_device == 7

    def test_watcher_params_live_in_proactive(self):
        """D11：watcher 旋钮上移 ProactiveConfig（hermes 旧键为 alias）。"""
        cfg = AgentRelayConfig()
        assert cfg.proactive.chatlog_poll_interval_s == 3.0
        assert cfg.proactive.nudge_coalesce_s == 2.0
        assert cfg.proactive.nudge_max_lines == 20

    def test_legacy_watcher_alias_migrates(self):
        """旧 hermes 键仅在被显式设置且新键未设置时迁移到 proactive.*。"""
        cfg = AgentRelayConfig(adapters={"hermes": {"nudge_max_lines": 7}})
        assert cfg.proactive.nudge_max_lines == 7

    def test_new_watcher_key_takes_precedence(self):
        cfg = AgentRelayConfig(
            adapters={"hermes": {"nudge_max_lines": 7}},
            proactive={"nudge_max_lines": 9},
        )
        assert cfg.proactive.nudge_max_lines == 9
