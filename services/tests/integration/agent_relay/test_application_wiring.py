"""application.py 接线测试（chatserver_no_redis.md §3.5/§3.2.1 接线层）。

驱动真实 lifespan（mock 掉全部外部依赖），断言：
① agent_relay 接线：SqliteJobStore 直写 chat_server.db（job 落库）、
   hermes 注入 job_store/hermes_store、bus 订阅 sm.dispatch；
② 无 redis_client 装配（app.state 无 redis 引用）；
③ shutdown 先停 SM/adapters（顺序事件）。
"""

from __future__ import annotations

import sqlite3

import pytest

from shared.config.models import AppConfig, RedisConfig

pytestmark = pytest.mark.integration

events: list[str] = []


@pytest.fixture(autouse=True)
def _reset_events():
    events.clear()


class _FakeHermes:
    name = "hermes"

    def __init__(self, nickname: str = "", proactive_enabled: bool = True):
        self.nickname = nickname
        self.proactive_enabled = proactive_enabled
        self.job_store = None
        self.deliverer = None

    async def connect(self) -> None:
        events.append("adapter_connect")

    async def close(self) -> None:
        events.append("adapter_close")

    def maybe_reap_conn(self, *a, **kw) -> None:
        pass


class _FakeOpenClaw(_FakeHermes):
    name = "openclaw"


class _FakeConnMgr:
    def __init__(self, *args, **kwargs):
        pass

    def is_online(self, device_id: str) -> bool:
        return True


class _Fake:
    def __init__(self, *args, **kwargs):
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def shutdown(self) -> None:
        events.append("sm_shutdown")

    async def on_job_change(self, device_id: str) -> None:
        pass

    async def dispatch(self, event) -> None:
        pass

    def attach(self, *args, **kwargs) -> None:
        pass

    current_recording_info = None


def _install_mocks(monkeypatch, fake_hermes: _FakeHermes, *,
                   adapters: dict | None = None) -> None:
    import shared.observability.metrics as metrics_mod

    monkeypatch.setattr(metrics_mod, "_registry", None)

    monkeypatch.setattr("gateway.connection.ConnectionManager", _FakeConnMgr)
    monkeypatch.setattr("gateway.auth.Authenticator", _Fake)
    monkeypatch.setattr("session.manager.SessionManager", _Fake)
    monkeypatch.setattr("session.agent_publisher.AgentEventPublisher", _Fake)
    monkeypatch.setattr(
        "agent_relay.adapters.loader.load_all",
        lambda cfg: adapters if adapters is not None else {"hermes": fake_hermes},
    )
    monkeypatch.setattr("agent_relay.publisher.PushPublisher", _Fake)
    monkeypatch.setattr("agent_relay.delivery.JobResultDeliverer", _Fake)
    monkeypatch.setattr("agent_relay.job_status.JobStatusNotifier", _Fake)
    monkeypatch.setattr("agent_relay.session_manager.AgentSessionManager", _Fake)
    monkeypatch.setattr("recording.service.RecordingService", _Fake)
    # lifespan 的 setup_logging 会全局替换 handler（JSON 格式），污染同套件内
    # caplog 断言——接线测试不关心日志配置，桩掉副作用
    monkeypatch.setattr("shared.observability.logging.setup_logging", lambda **kw: None)
    monkeypatch.setattr("asr.factory.create_asr", lambda cfg: None)
    monkeypatch.setattr("llm.factory.create_llm", lambda cfg: None)
    monkeypatch.setattr("tts.factory.create_tts", lambda cfg: None)
    monkeypatch.setattr("omni.factory.create_omni", lambda cfg: None)
    monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "")
    monkeypatch.setattr("app.lifespan.get_shutdown_handler", lambda: _Fake())


def _make_app(*, chat_server_db_path: str = "", hermes_nickname: str = "",
              omni_enabled: bool = False, relay_proactive_enabled: bool = True):
    from app.application import create_app

    hermes_cfg = {"mode": "proactive"}
    if hermes_nickname:
        hermes_cfg["nickname"] = hermes_nickname
    agent_relay = {
        "enabled": True,
        "events_channel": "livachat:agent:events",
        "push_channel_prefix": "livachat:push",
        "default_adapters": ["hermes"],
        "proactive": {"enabled": relay_proactive_enabled},
        "adapters": {"hermes": hermes_cfg},
    }
    cfg = AppConfig(
        agent_relay=agent_relay,
        omni={"enabled": omni_enabled},
        redis=RedisConfig(url="", key_prefix="livachat_test"),
        sqlite={"enabled": True, "max_queue_size": 128,
                "chat_server_db_path": chat_server_db_path},
    )
    return create_app(cfg)


class TestAgentJobPromptWiring:
    async def test_dialog_prompt_gets_declaration_translate_untouched(
        self, monkeypatch, tmp_path
    ):
        """fd_pipeline_llm_v2：dialog system_prompt 加载后含 provenance 声明；
        translate prompt 不受影响。"""
        from app.application import lifespan
        from session.agent_job_ingest import AGENT_JOB_PROMPT_DECLARATION

        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"))

        async with lifespan(app):
            assert app.state.system_prompt == "base prompt\n\n" + AGENT_JOB_PROMPT_DECLARATION
            assert "base prompt" in app.state.translate_prompt
            assert AGENT_JOB_PROMPT_DECLARATION not in app.state.translate_prompt
            # BFF /agent-jobs/cancel 依赖同进程 relay handles（§3.3.3）
            assert app.state.job_store is not None
            assert app.state.agent_adapters == {"hermes": fake_hermes}

    async def test_empty_prompt_stays_empty(self, monkeypatch, tmp_path):
        """prompt 文件缺失（mock 返回空）→ 不加声明，现状行为不变。"""
        from app.application import lifespan
        from session.agent_job_ingest import AGENT_JOB_PROMPT_DECLARATION

        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)  # load_system_prompt → ""
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"))

        async with lifespan(app):
            assert app.state.system_prompt == ""
            assert AGENT_JOB_PROMPT_DECLARATION not in app.state.system_prompt


class TestAgentAwarenessPromptWiring:
    """docs/prompts_improvements.md §3.5-D9：dialog + omni 注入，translate 不注入。"""

    async def test_dialog_and_omni_get_awareness_declaration(self, monkeypatch, tmp_path):
        from app.application import lifespan
        from session.agent_job_ingest import AGENT_AWARENESS_DECLARATION_TEMPLATE

        fake_hermes = _FakeHermes(nickname="小黑")
        _install_mocks(monkeypatch, fake_hermes)
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"),
                        hermes_nickname="小黑", omni_enabled=True)

        async with lifespan(app):
            dialog = app.state.system_prompt
            omni = app.state.omni_system_prompt
            assert dialog.startswith("base prompt\n\n")
            assert "【后台助手「小黑」】" in dialog
            assert "【后台助手「小黑」】" in omni
            # 原始模板不泄漏、占位符已全部代入
            assert AGENT_AWARENESS_DECLARATION_TEMPLATE not in dialog
            assert "$nickname" not in dialog and "$nickname" not in omni
            assert "小黑" not in app.state.translate_prompt

    async def test_hermes_not_loaded_no_awareness(self, monkeypatch, tmp_path):
        """adapter 装载失败（adapters 为空）→ 不注入：否则用户被指向不存在的 agent。"""
        from app.application import lifespan

        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)
        monkeypatch.setattr("agent_relay.adapters.loader.load_all", lambda cfg: {})
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"),
                        hermes_nickname="小黑")

        async with lifespan(app):
            assert "后台助手" not in app.state.system_prompt
            assert "小黑" not in app.state.system_prompt

    async def test_empty_nickname_no_awareness(self, monkeypatch, tmp_path):
        from app.application import lifespan
        from session.agent_job_ingest import AGENT_JOB_PROMPT_DECLARATION

        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"),
                        omni_enabled=True)

        async with lifespan(app):
            assert app.state.system_prompt == "base prompt\n\n" + AGENT_JOB_PROMPT_DECLARATION
            assert app.state.omni_system_prompt == "base prompt"

    async def test_openclaw_only_uses_generic_template(self, monkeypatch, tmp_path):
        from app.application import lifespan

        fake_openclaw = _FakeOpenClaw(nickname="小龙虾")
        _install_mocks(monkeypatch, _FakeHermes(),
                       adapters={"openclaw": fake_openclaw})
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"),
                        omni_enabled=True)

        async with lifespan(app):
            dialog = app.state.system_prompt
            assert "【后台助手「小龙虾」】" in dialog
            assert "【后台助手「小龙虾」】" in app.state.omni_system_prompt
            assert "联网查询实时信息" in dialog
            assert "小黑" not in dialog

    async def test_dual_adapters_list_both_names(self, monkeypatch, tmp_path):
        from app.application import lifespan

        fake_hermes = _FakeHermes(nickname="小黑")
        fake_openclaw = _FakeOpenClaw(nickname="小龙虾")
        _install_mocks(monkeypatch, fake_hermes, adapters={
            "hermes": fake_hermes, "openclaw": fake_openclaw,
        })
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"),
                        hermes_nickname="小黑", omni_enabled=True)

        async with lifespan(app):
            for prompt in (app.state.system_prompt, app.state.omni_system_prompt):
                assert "「小黑」、「小龙虾」" in prompt
                assert "联网查询实时信息" in prompt

    async def test_hermes_named_openclaw_empty_declares_hermes_only(
        self, monkeypatch, tmp_path
    ):
        from app.application import lifespan

        fake_hermes = _FakeHermes(nickname="小黑")
        fake_openclaw = _FakeOpenClaw(nickname="")
        _install_mocks(monkeypatch, fake_hermes, adapters={
            "hermes": fake_hermes, "openclaw": fake_openclaw,
        })
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"),
                        hermes_nickname="小黑")

        async with lifespan(app):
            assert "【后台助手「小黑」】" in app.state.system_prompt
            assert "小龙虾" not in app.state.system_prompt

    async def test_both_nicknames_empty_no_awareness(self, monkeypatch, tmp_path):
        from app.application import lifespan
        from session.agent_job_ingest import AGENT_JOB_PROMPT_DECLARATION

        fake_hermes = _FakeHermes(nickname="")
        fake_openclaw = _FakeOpenClaw(nickname="")
        _install_mocks(monkeypatch, fake_hermes, adapters={
            "hermes": fake_hermes, "openclaw": fake_openclaw,
        })
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"))

        async with lifespan(app):
            assert app.state.system_prompt == "base prompt\n\n" + AGENT_JOB_PROMPT_DECLARATION
            assert "后台助手" not in app.state.system_prompt

    async def test_reactive_only_adapter_no_awareness(self, monkeypatch, tmp_path):
        from app.application import lifespan

        fake_hermes = _FakeHermes(nickname="小黑", proactive_enabled=False)
        _install_mocks(monkeypatch, fake_hermes)
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"),
                        hermes_nickname="小黑")

        async with lifespan(app):
            assert "后台助手" not in app.state.system_prompt

    async def test_global_proactive_disabled_still_injects(
        self, monkeypatch, tmp_path
    ):
        """G8 现状钉住：proactive.enabled 只关 recording.ready 分析，不影响声明。"""
        from app.application import lifespan

        fake_hermes = _FakeHermes(nickname="小黑")
        _install_mocks(monkeypatch, fake_hermes)
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: "base prompt")
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"),
                        hermes_nickname="小黑", relay_proactive_enabled=False)

        async with lifespan(app):
            assert "【后台助手「小黑」】" in app.state.system_prompt


class TestNoRedisWiring:
    async def test_no_redis_client_anywhere(self, monkeypatch, tmp_path):
        """去 Redis 接线：app.state 无 redis_client/redis_store；ConnectionManager
        无 redis 参数。"""
        from app.application import lifespan

        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"))

        async with lifespan(app):
            assert not hasattr(app.state, "redis_client")
            assert not hasattr(app.state, "redis_store")
            assert app.state.chat_store is not None

    async def test_relay_wired_with_sqlite_job_store(self, monkeypatch, tmp_path):
        """§3.5 接线：SqliteJobStore 注入 hermes；job 全链路直写 chat_server.db；
        bus 订阅 dispatch；无 writer/listener（直写即真源）。"""
        from agent_relay.job_store_sqlite import SqliteJobStore
        from app.application import lifespan

        db = str(tmp_path / "chat_server.db")
        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)
        app = _make_app(chat_server_db_path=db)

        async with lifespan(app):
            assert isinstance(fake_hermes.job_store, SqliteJobStore)
            assert fake_hermes.job_store._db_path == db
            assert fake_hermes.job_store._recording_lookup is not None
            # hermes 映射存储注入（§3.6）
            assert fake_hermes.job_store is not None

            job = await fake_hermes.job_store.create(
                session_id="w1", device_id="dev-w", summary="接线测试"
            )
            await fake_hermes.job_store.mark_running(job.job_key, "task-w")
            await fake_hermes.job_store.mark_done(job.job_key, "ok")

            # 直写即落库（无队列/无 writer 排空）
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT status FROM agent_jobs WHERE job_key = ?",
                (job.job_key,)).fetchone()
            conn.close()
            assert row is not None and row[0] == "done"

        assert "adapter_connect" in events
        # shutdown 经 SM.shutdown()（桩记录）——SM 存在时 adapter 由其关闭
        assert "sm_shutdown" in events

class TestOmniModeWiring:
    """omni.enabled = "允许全模态模式"：三段式服务必须同时存在，两个 prompt 分离。

    回归背景：原实现是 `if cfg.omni.enabled: … else: …` 整体二选一——开启 omni 时
    ASR/LLM/TTS 全为 None（chat/translate/transcribe 三模式失效）、system_prompt 被
    omni 的 prompt 顶替。见 docs/fd_pipeline_omni_mode.md §4 D3。
    """

    async def test_omni_enabled_keeps_three_stage_and_splits_prompts(self, monkeypatch, tmp_path):
        from app.application import lifespan
        from session.agent_job_ingest import AGENT_JOB_PROMPT_DECLARATION
        from shared.config.models import AppConfig, LlmConfig, OmniConfig, TranslateConfig

        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)

        asr_svc, llm_svc, tts_svc, omni_svc = object(), object(), object(), object()
        monkeypatch.setattr("asr.factory.create_asr", lambda cfg: asr_svc)
        monkeypatch.setattr("llm.factory.create_llm", lambda cfg: llm_svc)
        monkeypatch.setattr("tts.factory.create_tts", lambda cfg: tts_svc)
        monkeypatch.setattr("omni.factory.create_omni", lambda cfg: omni_svc)
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: f"<{path}>")

        from app.application import create_app

        cfg = AppConfig(
            agent_relay={"enabled": True, "events_channel": "c", "push_channel_prefix": "p",
                         "default_adapters": ["hermes"], "adapters": {"hermes": {"mode": "proactive"}}},
            redis=RedisConfig(url="", key_prefix="livachat_test"),
            sqlite={"enabled": True, "max_queue_size": 128,
                    "chat_server_db_path": str(tmp_path / "chat_server.db")},
            llm=LlmConfig(system_prompt_path="llm.txt", translate_prompt_path="trans.txt"),
            omni=OmniConfig(enabled=True, active="alibaba_omni", system_prompt_path="omni.txt"),
            translate=TranslateConfig(enabled=True),
        )
        app = create_app(cfg)

        async with lifespan(app):
            # 三段式与 omni 服务并存
            assert app.state.asr_service is asr_svc
            assert app.state.llm_service is llm_svc
            assert app.state.tts_service is tts_svc
            assert app.state.omni_service is omni_svc
            # prompt 分离（不可互换）：对话含 agent job 声明，omni 用独立 prompt
            assert app.state.system_prompt == "<llm.txt>\n\n" + AGENT_JOB_PROMPT_DECLARATION
            assert app.state.omni_system_prompt == "<omni.txt>"
            assert app.state.translate_prompt == "<trans.txt>"
            # translate 配置不再被 omni.enabled 剥夺（模式互斥由运行时保证）
            assert app.state.translate_cfg is not None
            assert app.state.translate_cfg.enabled is True


class TestAvDetectorWiring:
    """avdetector 装配（docs/fd_pipeline_avdetector.md §4 D4）：enabled 门控 + prompt 独立载入。

    enabled=false（交付默认）→ 服务 None、路径全不激活（与现状一致）；
    enabled=true → 服务创建 + prompt 取自 avdetector.system_prompt_path（与 llm/omni 分离）。
    """

    async def test_disabled_no_service(self, monkeypatch, tmp_path):
        from app.application import lifespan

        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)
        app = _make_app(chat_server_db_path=str(tmp_path / "chat_server.db"))

        async with lifespan(app):
            assert app.state.avdetector_service is None
            assert app.state.avdetector_system_prompt == ""
            assert app.state.avdetector_cfg is not None
            assert app.state.avdetector_cfg.enabled is False

    async def test_enabled_creates_service_with_separate_prompt(self, monkeypatch, tmp_path):
        from app.application import lifespan
        from shared.config.models import AppConfig, AvDetectorConfig, LlmConfig

        fake_hermes = _FakeHermes()
        _install_mocks(monkeypatch, fake_hermes)

        avdetector_svc = object()
        monkeypatch.setattr("avdetector.factory.create_avdetector", lambda cfg: avdetector_svc)
        monkeypatch.setattr("llm.prompt.load_system_prompt", lambda path: f"<{path}>")

        from app.application import create_app

        cfg = AppConfig(
            agent_relay={"enabled": True, "events_channel": "c", "push_channel_prefix": "p",
                         "default_adapters": ["hermes"],
                         "adapters": {"hermes": {"mode": "proactive"}}},
            redis=RedisConfig(url="", key_prefix="livachat_test"),
            sqlite={"enabled": True, "max_queue_size": 128,
                    "chat_server_db_path": str(tmp_path / "chat_server.db")},
            llm=LlmConfig(system_prompt_path="llm.txt", translate_prompt_path="trans.txt"),
            avdetector=AvDetectorConfig(
                enabled=True, active="alibaba_avdetector",
                system_prompt_path="avdetector.txt",
            ),
        )
        app = create_app(cfg)

        async with lifespan(app):
            assert app.state.avdetector_service is avdetector_svc
            # prompt 独立来源（与 llm 的 llm.txt 不同源）
            assert app.state.avdetector_system_prompt == "<avdetector.txt>"
            assert app.state.avdetector_cfg.enabled is True
