from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from app.lifespan import get_shutdown_handler
from app.routes import router
from gateway.auth import Authenticator
from gateway.connection import ConnectionManager
from shared.config.loader import load_config
from shared.config.models import AppConfig
from shared.version import SERVER_VERSION

if TYPE_CHECKING:
    from session.agent_job_ingest import NamedAgent

logger = logging.getLogger("livachat.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    cfg = app.state.config

    from shared.observability.logging import setup_logging

    setup_logging(json_logs=cfg.observability.json_logs, log_level=cfg.server.log_level,
                  file=cfg.observability.log_file)

    # -- Chat server SQLite store（fail-fast：路径未配/父目录缺失/init 失败 → 启动
    #    报错退出，不做降级运行；chatserver_sqlite_store.md §3.1/§3.7）------------
    from shared.persistence.chat_store import ChatStore

    chat_store = ChatStore(
        cfg.sqlite.chat_server_db_path,
        max_queue=cfg.sqlite.max_queue_size,
    )
    await chat_store.start()

    conn_mgr = ConnectionManager()
    app.state.connection_manager = conn_mgr

    authenticator = Authenticator(cfg.auth)
    app.state.authenticator = authenticator

    # -- Provider services -------------------------------------------------
    asr_service = None
    llm_service = None
    tts_service = None
    omni_service = None
    avdetector_service = None
    system_prompt = ""
    omni_system_prompt = ""
    avdetector_system_prompt = ""
    translate_prompt = ""

    # 三段式一套无条件创建（chat/translate/transcribe 模式与 omni 模式并存，
    # 见 docs/fd_pipeline_omni_mode.md §4 D3）：omni.enabled 不再关闭三段式。
    try:
        from asr.factory import create_asr

        asr_service = create_asr(cfg.asr)
        logger.info("ASR service created", extra={"active": cfg.asr.active})
    except Exception:
        logger.warning("ASR service not available")

    try:
        from llm.factory import create_llm
        from llm.prompt import load_system_prompt

        llm_service = create_llm(cfg.llm)
        system_prompt = load_system_prompt(cfg.llm.system_prompt_path)
        system_prompt = _with_agent_job_declaration(system_prompt)
        logger.info("LLM service created", extra={"active": cfg.llm.active})
    except Exception:
        logger.warning("LLM service not available")

    try:
        translate_prompt = load_system_prompt(cfg.llm.translate_prompt_path)
    except Exception:
        logger.warning("translate prompt not available, translate mode will use built-in prompt")

    try:
        from tts.factory import create_tts

        tts_service = create_tts(cfg.tts)
        logger.info("TTS service created", extra={"active": cfg.tts.active})
    except Exception:
        logger.warning("TTS service not available")

    # omni 服务与**独立** prompt（cfg.omni.enabled = 允许客户端切全模态模式）。
    # system_prompt 供对话/翻译；omni_system_prompt 供全模态引擎开流——两者不可互换。
    if cfg.omni.enabled:
        try:
            from llm.prompt import load_system_prompt
            from omni.factory import create_omni

            omni_service = create_omni(cfg.omni)
            omni_system_prompt = load_system_prompt(cfg.omni.system_prompt_path)
            logger.info("Omni service created", extra={"active": cfg.omni.active})
        except Exception:
            logger.warning("Omni service not available", exc_info=True)

    # avdetector（chat 模式旁路监测，docs/fd_pipeline_avdetector.md §4 D4）：
    # enabled=false → 服务不创建、端到端路径全不激活（与现状完全一致）。
    if cfg.avdetector.enabled:
        try:
            from avdetector.factory import create_avdetector
            from llm.prompt import load_system_prompt

            avdetector_service = create_avdetector(cfg.avdetector)
            avdetector_system_prompt = load_system_prompt(cfg.avdetector.system_prompt_path)
            logger.info("AvDetector service created", extra={"active": cfg.avdetector.active})
        except Exception:
            logger.warning("AvDetector service not available", exc_info=True)

    # -- Session manager ---------------------------------------------------
    from session.manager import SessionManager
    from shared.timeutil import lvdata_db_path

    session_mgr = SessionManager(
        lvdata_db_path=lvdata_db_path(cfg.bff, cfg.recording),
        chat_store=chat_store,
    )

    # -- Agent event bus（进程内，chatserver_no_redis.md §3.2.1）---------------
    agent_publisher = None
    event_bus = None
    if cfg.agent_relay and cfg.agent_relay.get("enabled"):
        try:
            from agent_relay.event_bus import InProcessEventBus
            from session.agent_publisher import AgentEventPublisher

            event_bus = InProcessEventBus()
            agent_publisher = AgentEventPublisher(event_bus)
            logger.info("AgentEventPublisher created (in-process bus)")
        except Exception:
            logger.warning("AgentEventPublisher not available", exc_info=True)

    # -- Recording service --------------------------------------------------
    recording_service = None
    if cfg.recording.enabled:
        try:
            from recording.service import RecordingService

            recording_service = RecordingService(
                chat_store=chat_store,
                config=cfg.recording,
                agent_publisher=agent_publisher,
            )
            await recording_service.start()
            logger.info("Recording service started")
        except Exception:
            logger.warning("Recording service not available", exc_info=True)

    # -- Agent Relay integration ---------------------------------------------
    relay_cfg = None
    adapters = None
    job_store = None
    agent_session_mgr = None
    job_status_notifier = None
    if cfg.agent_relay and cfg.agent_relay.get("enabled"):
        from agent_relay.config import AgentRelayConfig

        relay_cfg = AgentRelayConfig.model_validate(cfg.agent_relay)
        # chatlog 路径解析 lookup（§3.7）：RecordingService.current_recording_info
        # （内存 registry 优先 + recording_sessions 表只读兜底）
        recording_lookup = (
            recording_service.current_recording_info
            if recording_service is not None else None
        )

        try:
            from agent_relay.adapters.loader import load_all
            from agent_relay.job_store_sqlite import SqliteJobStore
            from agent_relay.publisher import PushPublisher
            from agent_relay.session_manager import AgentSessionManager

            adapters = load_all(relay_cfg)
            if adapters:
                # v2: inject proactive-mode dependencies (JobStore, hermes
                # mapping store, chatlog path resolution) into the relay.
                from agent_relay.delivery import JobResultDeliverer

                # §3.5：job 状态真源 = chat_server.db agent_jobs 表（chat server
                # 单写者；create 时尽力解析 recording_session_id）
                job_store = SqliteJobStore(
                    cfg.sqlite.chat_server_db_path,
                    max_result_bytes=relay_cfg.adapters.hermes.max_stored_result_bytes,
                    recording_lookup=chat_store.get_recording_session,
                )
                hermes = adapters.get("hermes")
                if hermes is not None:
                    hermes.job_store = job_store
                    hermes.hermes_store = chat_store  # §3.6 device→session 映射
                    hermes.recording_lookup = recording_lookup
                    hermes.base_dir = cfg.recording.base_dir
                    # webroot（docs/webroot_feature.md §3.3）：未启用 = ""（prompt 不含 web 能力段）
                    hermes.webroot_dir = (
                        resolve_webroot_dir(cfg) if cfg.webroot.enabled else ""
                    )
                openclaw = adapters.get("openclaw")
                if openclaw is not None:
                    # docs/openclaw_adaptor_design.md §5.4：B 方案 job 链路
                    # （会话/订阅/runId 关联）复用同一 JobStore + deliverer
                    openclaw.job_store = job_store
                    openclaw.webroot_dir = (
                        resolve_webroot_dir(cfg) if cfg.webroot.enabled else ""
                    )

                for name, adapter in adapters.items():
                    try:
                        await adapter.connect()
                        logger.info(
                            "agent relay adapter connected",
                            extra={"adapter": name},
                        )
                    except Exception:
                        logger.warning(
                            "agent relay adapter connect failed, continuing",
                            extra={"adapter": name},
                            exc_info=True,
                        )

                publisher = PushPublisher(conn_mgr, relay_cfg)
                deliverer = JobResultDeliverer(
                    job_store,
                    publisher,
                    relay_cfg,
                    agent_ids={
                        "hermes": relay_cfg.adapters.hermes.nickname,
                        "openclaw": relay_cfg.adapters.openclaw.nickname,
                    },
                    on_delivered=hermes.maybe_reap_conn if hermes is not None else None,
                    online_check=conn_mgr.is_online,
                )
                if hermes is not None:
                    hermes.deliverer = deliverer
                if openclaw is not None:
                    openclaw.deliverer = deliverer
                if relay_cfg.job_status_enabled:
                    from agent_relay.job_status import JobStatusNotifier

                    job_status_notifier = JobStatusNotifier(
                        job_store,
                        publisher,
                        agent_name="hermes",
                        interval_s=relay_cfg.job_status_interval_s,
                        scan_interval_s=relay_cfg.job_status_scan_interval_s,
                        online_check=conn_mgr.is_online,
                    )
                    job_store.set_change_listener(job_status_notifier.on_job_change)
                    await job_status_notifier.start()
                    logger.info("job status notifier started")
                agent_session_mgr = AgentSessionManager(
                    adapters=adapters,
                    publisher=publisher,
                    cfg=relay_cfg,
                    recording_lookup=recording_lookup,
                    base_dir=cfg.recording.base_dir,
                    job_store=job_store,
                    deliverer=deliverer,
                    job_status_notifier=job_status_notifier,
                )
                if event_bus is not None:
                    event_bus.subscribe(agent_session_mgr.dispatch)
                    await event_bus.start()
                logger.info(
                    "agent relay started",
                    extra={"adapters": list(adapters.keys())},
                )
            else:
                logger.warning("agent relay: no adapters loaded, relay core skipped")
        except Exception:
            logger.warning("agent relay core not available", exc_info=True)

    # 后台 Agent 认知声明（docs/agent_relay_prompts.md §8）：按实际装载的 proactive
    # adapter 集合生成；dialog + omni 两处注入，translate 不注入。空 nickname / 重复名
    # 只告警不阻断（Q2/Q3）；connect 失败仍按已实例化计入（Q8）。
    named_agents = (
        _named_background_agents(adapters)
        if relay_cfg is not None and adapters else []
    )
    system_prompt = _with_agent_awareness(system_prompt, named_agents)
    omni_system_prompt = _with_agent_awareness(omni_system_prompt, named_agents)

    app.state.asr_service = asr_service
    app.state.llm_service = llm_service
    app.state.tts_service = tts_service
    app.state.omni_service = omni_service
    app.state.omni_enabled = cfg.omni.enabled
    app.state.avdetector_service = avdetector_service
    app.state.avdetector_system_prompt = avdetector_system_prompt
    app.state.avdetector_cfg = cfg.avdetector
    app.state.system_prompt = system_prompt
    app.state.omni_system_prompt = omni_system_prompt
    app.state.translate_prompt = translate_prompt
    app.state.session_mgr = session_mgr
    app.state.recording_service = recording_service
    app.state.agent_publisher = agent_publisher
    # BFF /agent-jobs/cancel 与 relay 同进程：暴露 job 真源与 adapter 映射
    # （docs/android_agent_job_status.md §7.2）。relay 未启用/装配失败时为
    # None/{}，BFF 返回 503 agent_relay_unavailable。
    app.state.job_store = job_store
    app.state.agent_adapters = adapters or {}

    # translate 配置恒可用：模式互斥由运行时（session.change）保证，
    # omni.enabled 不再剥夺 lecture 能力（docs/fd_pipeline_omni_mode.md §4 D3）
    app.state.translate_cfg = cfg.translate

    # Store on app.state for /readyz reuse
    app.state.chat_store = chat_store

    # Wire shutdown handler (uvicorn drives signal → lifespan teardown)
    handler = get_shutdown_handler()
    handler.attach(conn_mgr=conn_mgr)

    # Write startup marker
    _write_startup_marker()

    yield

    # Shutdown
    if event_bus is not None:
        # 先停总线（排空在途事件），再停消费端（SM/adapters）
        try:
            await event_bus.stop()
        except Exception:
            logger.warning("agent event bus stop failed", exc_info=True)
    if job_status_notifier is not None:
        try:
            await job_status_notifier.stop()
        except Exception:
            logger.warning("job status notifier stop failed", exc_info=True)
    if agent_session_mgr is not None:
        # SM.shutdown() stops watchers, counts done-undelivered jobs
        # (deferred metric, §5.6/§7) and closes adapters.
        try:
            await agent_session_mgr.shutdown()
        except Exception:
            logger.warning("agent session manager shutdown failed", exc_info=True)
    elif adapters is not None:
        for name, adapter in adapters.items():
            try:
                await adapter.close()
            except Exception:
                logger.warning("adapter close failed", extra={"adapter": name}, exc_info=True)
    await handler.shutdown()
    # 先停写入来源（连接随 conn_mgr.shutdown() 关闭），再排空 chat store writer——
    # 顺序不可反转：若先停 writer，shutdown 期间的写入入队无人消费，静默丢失
    # （chatserver_sqlite_store.md §3.7，对齐 job_pg_writer 顺序纪律）
    try:
        await chat_store.stop()
    except Exception:
        logger.warning("chat store stop failed", exc_info=True)


def _with_agent_job_declaration(system_prompt: str) -> str:
    """dialog 管线 system prompt 追加 agent job provenance 声明（fd_pipeline_llm_v2）；
    空 prompt 原样返回，translate/omni prompt 不走此函数。"""
    if not system_prompt:
        return system_prompt
    from session.agent_job_ingest import AGENT_JOB_PROMPT_DECLARATION

    return system_prompt.rstrip() + "\n\n" + AGENT_JOB_PROMPT_DECLARATION


def _named_background_agents(adapters: dict) -> list[NamedAgent]:
    """Proactive adapters with a non-empty nickname, deduped by nickname.

    Duplicate nicknames collapse into one entry whose adapter_names keeps all
    backends; render_agent_awareness uses the generic template for that case.
    Empty nicknames are warned and omitted (Q2); connect health is not
    considered (Q8)."""
    from session.agent_job_ingest import NamedAgent

    by_nickname: dict[str, list[str]] = {}
    for adapter_name, adapter in adapters.items():
        if not adapter.proactive_enabled:
            continue
        nickname = adapter.nickname.strip()
        if not nickname:
            logger.warning(
                "agent relay: proactive adapter has empty nickname; "
                "excluded from LivaChat awareness",
                extra={"adapter": adapter_name},
            )
            continue
        by_nickname.setdefault(nickname, []).append(adapter_name)

    named_agents: list[NamedAgent] = []
    for nickname, adapter_names in by_nickname.items():
        if len(adapter_names) > 1:
            logger.warning(
                "agent relay: duplicate agent nickname; using generic template",
                extra={"nickname": nickname, "adapters": adapter_names},
            )
        named_agents.append(
            NamedAgent(nickname=nickname, adapter_names=tuple(adapter_names))
        )
    return named_agents


def _with_agent_awareness(system_prompt: str, agents: list[NamedAgent]) -> str:
    """Append the background-agent awareness declaration to dialog/omni prompts.

    Empty prompt or no named agents -> unchanged (zero delta). translate prompt
    is a separate role and does not call this function."""
    if not system_prompt or not agents:
        return system_prompt
    from session.agent_job_ingest import render_agent_awareness

    declaration = render_agent_awareness(agents)
    if not declaration:
        return system_prompt
    return system_prompt.rstrip() + "\n\n" + declaration


def _write_startup_marker() -> None:
    try:
        from pathlib import Path
        Path("/tmp/livachat-ready").touch()
    except Exception:
        pass


def create_app(config: AppConfig | None = None) -> FastAPI:
    if config is None:
        config = load_config()
    # /docs、/redoc、/openapi.json 全关——不对公网暴露 API schema（2026-10-13，用户决策）
    app = FastAPI(
        title="LivaChat Server", version=SERVER_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.config = config
    from starlette.middleware.gzip import GZipMiddleware

    from app.access_log import access_log_middleware

    app.add_middleware(access_log_middleware())
    # 压 JSON 大响应（overview 池放大后最坏 ~2.2MB）；默认排除 content-type 为
    # image/*、audio/*、video/* 的响应，206 分片响应整体跳过 → media/Range 零影响
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.include_router(router)

    if config.bff.enabled:
        from bff.router import register as register_bff

        register_bff(app)

    _mount_webroot(app, config)

    return app


def resolve_webroot_dir(config: AppConfig) -> str:
    """webroot 静态目录唯一解析点（挂载与 hermes prompt 渲染共用；docs/webroot_feature.md §3.1）。"""
    from pathlib import Path

    return config.webroot.root or str(Path(config.recording.base_dir) / "webroot")


def _mount_webroot(app: FastAPI, config: AppConfig) -> None:
    """Serve agent-produced web pages at /webroot（docs/webroot_feature.md §3.1）。

    enabled=false → 不挂载、无任何 FS 副作用（能力与承诺一致：prompt 也不含 web 段）。
    enabled=true → 自动建目录；创建失败（父目录缺失/不可写）→ 启动 fail-fast——
    目录是 prompt 对 agent 的承诺，静默跳过会"agent 写了页面但 404"（§4 D2）。
    """
    if not config.webroot.enabled:
        return
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    root = Path(resolve_webroot_dir(config))
    try:
        root.mkdir(parents=False, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"webroot dir not creatable: {root} ({exc}); "
            "ensure the parent directory exists and is writable "
            "(install contract: /opt/lvdata must exist)"
        ) from exc
    app.mount("/webroot", StaticFiles(directory=str(root), html=True), name="webroot")
