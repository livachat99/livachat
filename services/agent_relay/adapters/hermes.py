"""HermesAdapter — per-device Hermes agent connection via WebSocket JSON-RPC 2.0.

v2/v3 changes (fd_agent_relay_service_v2.md §5.2, hermes_adaptor_design_v3.md):
- one WS connection + one Hermes session per device (`_DeviceConnection`),
  created lazily (`_ensure_device`) instead of one eager global connection
- per-connection reader task with exponential backoff + jitter reconnect
- sessions survive chat session end (D3/D4): connection is closed on idle,
  the Hermes session stays on the server and is resumed via session.resume
- startup recovery (R1): recover_active_devices() rebuilds connections for
  devices with running jobs (bounded concurrency, running-priority)
- every job runs in its own dedicated Hermes session (v3 carrier); completion
  is routed by session_id via message.complete and delivered through
  JobResultDeliverer (§5.5)
- blocking interaction fallback matrix (approval/clarify/sudo/secret)
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import random
import string
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, InvalidURI

from agent_relay.adapters.base import (
    AgentAdapter,
    AgentContext,
    AgentResponseChunk,
    ChatlogUpdate,
    RecordingInfo,
)
from agent_relay.chatlog_watcher import resolve_chatlog_path
from agent_relay.config import HermesAdapterConfig
from agent_relay.contract import parse_contract
from agent_relay.errors import AdapterConnectionError, AdapterTimeoutError
from agent_relay.job_store import (
    STATUS_DONE,
    STATUS_REQUESTED,
    STATUS_RUNNING,
    JobStore,
)
from agent_relay.metrics import (
    inc_interaction_autorespond,
    inc_job_event,
    inc_liveness_force_reconnect,
    inc_liveness_probe_sent,
    inc_liveness_probe_timeout,
    inc_nudge,
    inc_session_resumed,
    set_device_connections,
)
from agent_relay.prompt_hints import (
    agent_name_hint,
    language_hint,
    timezone_hint,
    webroot_hint,
)
from shared.observability.logging import redact_url

logger = logging.getLogger("livachat.agent_relay.adapter.hermes")

# Real Hermes JSON-RPC wraps all server→client pushes in method="event" with
# the actual event type in params.type.
_EVENT_METHOD = "event"
_EVENT_TYPE_GATEWAY_READY = "gateway.ready"
_EVENT_TYPE_MESSAGE_DELTA = "message.delta"
_EVENT_TYPE_THINKING_DELTA = "thinking.delta"
_EVENT_TYPE_REASONING_DELTA = "reasoning.delta"
_EVENT_TYPE_MESSAGE_COMPLETE = "message.complete"
_EVENT_TYPE_APPROVAL_REQUEST = "approval.request"
_EVENT_TYPE_CLARIFY_REQUEST = "clarify.request"
_EVENT_TYPE_SUDO_REQUEST = "sudo.request"
_EVENT_TYPE_SECRET_REQUEST = "secret.request"
_EVENT_TYPE_ERROR = "error"


# Delta events to ignore (scheme B)
_DELTA_EVENT_TYPES = frozenset({
    _EVENT_TYPE_MESSAGE_DELTA,
    _EVENT_TYPE_THINKING_DELTA,
    _EVENT_TYPE_REASONING_DELTA,
})

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_MISSION_TEMPLATE = _PROMPTS_DIR / "hermes_monitor.md"
_JOB_TASK_TEMPLATE = _PROMPTS_DIR / "job_task.md"

# Unattended-answering copy for clarify requests (§4.5), kept in the mission
# template family so it is managed in one place.
_CLARIFY_AUTORESPONSE = "(无人值守) 无需澄清,按你的最佳判断继续,不要再询问"

_NOOP_OUTPUT = "NOOP"

# nudge 超时后 session.interrupt 的节流窗口：主 agent 持续异常时避免
# 每个排队 nudge 都打断一次（interrupt 风暴）。
_INTERRUPT_THROTTLE_S = 10.0

# v3 job-session carrier: hermes_task_id carries the durable session id with a
# prefix so recovery can distinguish it from openclaw runIds without any schema
# migration.
_JOB_REF_SESSION_PREFIX = "session:"
# Transcript markers persisted by v0.20.2 for interrupted/failed turns. These
# are a different data source from message.complete.text (live `error:` prefix).
_INTERRUPTED_SENTINEL = "Operation interrupted."
_TRANSCRIPT_ERROR_PREFIX = "Error:"


def classify_job_result(text: str) -> tuple[str, str]:
    """Classify a completed job's final text for delivery/consumption.

    Returns ``(kind, out_text)`` where kind is one of:
    - ``noop``: literal NOOP (done+delivered, no push)
    - ``rpc_error``: legacy ``error:`` prefix (done+delivered, no push)
    - ``empty``: empty output (done+delivered, no push)
    - ``deliver``: normal result (mark_done + deliver)
    Shared by the live message.complete path and transcript recovery.
    """
    stripped = (text or "").strip()
    if not stripped:
        return "empty", ""
    if stripped.upper() == _NOOP_OUTPUT:
        return "noop", ""
    if stripped.startswith("error:"):
        return "rpc_error", ""
    return "deliver", text or ""


def _extract_job_result(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Extract the job's final assistant text from a persisted transcript.

    The job session is dedicated, so the first user message is the task prompt.
    Returns ``(kind, text)`` with kind interrupted|run_error|text|none.
    """
    first_user = None
    for idx, msg in enumerate(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            first_user = idx
            break
    if first_user is None:
        return "none", ""
    assistant_texts = [
        msg.get("text")
        for msg in (messages or [])[first_user + 1:]
        if isinstance(msg, dict)
        and msg.get("role") == "assistant"
        and isinstance(msg.get("text"), str)
    ]
    if not assistant_texts:
        return "none", ""
    last = assistant_texts[-1]
    if last.strip() == _INTERRUPTED_SENTINEL:
        return "interrupted", ""
    if last.lstrip().startswith(_TRANSCRIPT_ERROR_PREFIX):
        return "run_error", ""
    if not last.strip():
        return "text", ""
    return "text", last


def _is_session_job_ref(ref: str) -> bool:
    return str(ref or "").startswith(_JOB_REF_SESSION_PREFIX)


def _stored_session_from_ref(ref: str) -> str:
    value = str(ref or "")
    if not value.startswith(_JOB_REF_SESSION_PREFIX):
        return ""
    return value[len(_JOB_REF_SESSION_PREFIX):]


class ResumeOutcome(enum.Enum):
    """session.resume tri-state (A1/A2, §5.4/§8.2).

    OK:     返回同一 sid —— 20s 宽限内 live 复用成功。
    REAPED: 返回新 sid —— 旧 device session 已 reap；job 各自有独立会话，不受影响。
    FAILED: RPC 瞬时失败 —— 映射保留，调用方决定重试（recover/下次重连）或重建。
    """

    OK = "ok"
    REAPED = "reaped"
    FAILED = "failed"


@dataclass
class _JobSession:
    """One relay-facing job carried by its own Hermes session."""

    job_key: str
    hermes_session_id: str
    stored_session_id: str
    created_at: float = field(default_factory=time.time)
    recheck_task: asyncio.Task[None] | None = None


@dataclass
class _DeviceConnection:
    """One Hermes WS connection + one Hermes session for a device."""

    device_id: str
    ws: ClientConnection | None = None
    hermes_session_id: str = ""
    stored_session_id: str = ""
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    reader_task: asyncio.Task[None] | None = None
    resume_task: asyncio.Task[None] | None = None
    liveness_task: asyncio.Task[None] | None = None
    pending: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    turn_futures: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    # Sessions whose previous turn timed out locally: the Hermes-side turn
    # may still complete later (P0: interrupted turns do emit
    # message.complete), and that straggler must not masquerade as the
    # next turn's contract (P2-5). Value = arm timestamp; a complete is
    # only dropped when it arrives within 2×nudge_timeout_s of the arm —
    # older markers belong to a previous session/turn and are released
    # instead of being mis-dropped (cross-session straggler bug). Cleared
    # on reconnect.
    turn_stragglers: dict[str, float] = field(default_factory=dict)
    # v3 job-session carrier: job_key -> live session; live sid -> job_key.
    job_sessions: dict[str, _JobSession] = field(default_factory=dict)
    job_sid_to_key: dict[str, str] = field(default_factory=dict)
    # Per-job resume dedupe (startup recovery / reconnect can race).
    job_resume_inflight: set[str] = field(default_factory=set)
    job_recovery_task: asyncio.Task[None] | None = None
    # 最近一次渲染 mission 的 context（重连 resume 后补发用；None =
    # 尚无活跃 livachat 会话，跳过补发）。
    mission_ctx: AgentContext | None = None
    # session.interrupt 节流时间戳（_INTERRUPT_THROTTLE_S）。
    last_interrupt_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)          # turn 串行化
    start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)    # 建连/session 串行化
    last_active_at: float = field(default_factory=time.time)
    reconnect_attempts: int = 0
    connect_error: Exception | None = None
    # Set by _stop_conn: a waiter (e.g. _ensure_device holding a stale
    # reference) fails fast instead of waiting out the timeout (C1).
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    # hermes_session_id 来自 Redis 映射（跨进程恢复）：须 resume 验证，
    # 不能直接信任（R3/§5.2 步骤 2）。
    session_recovered: bool = False


class HermesAdapter(AgentAdapter):
    """Adapter for the Hermes agent platform.

    Communicates via WebSocket JSON-RPC 2.0 at /api/ws. One WS + one Hermes
    session per device (D1/D2); connections are created lazily and reaped
    when idle. Prompts are serialized per device (device lock).
    """

    name = "hermes"

    @property
    def nickname(self) -> str:
        return self._cfg.nickname

    def __init__(self, cfg: HermesAdapterConfig) -> None:
        self._cfg = cfg
        self._req_id = 0
        self._devices: dict[str, _DeviceConnection] = {}
        self._device_locks: dict[str, asyncio.Lock] = {}
        # per-device live LivaChat session ids (B1): a device with an
        # active chat session keeps its WS even when idle with no jobs.
        # Set semantics keep the counter idempotent — the session-manager
        # self-heal path may call on_session_start twice for one session.
        self._live_sessions: dict[str, set[str]] = {}
        # Serializes per-device on_session_start/end: without it a session
        # A's end handler can reap the WS before the concurrent session B's
        # start handler registers, failing B's first ensure_session.
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}
        self._watchdog_task: asyncio.Task[None] | None = None
        self._recover_task: asyncio.Task[None] | None = None
        self._reaper_task: asyncio.Task[None] | None = None
        self._closed = False

        self.reactive_enabled = cfg.mode in ("reactive", "both")
        self.proactive_enabled = cfg.mode in ("proactive", "both")

        # Injected by application wiring (optional; unit tests may leave None)
        self.job_store: JobStore | None = None
        # hermes_store：device→session 映射持久层（ChatStore，§3.6）；同步直写
        self.hermes_store: Any | None = None
        # recording_lookup：chatlog 路径解析（RecordingService.current_recording_info）
        self.recording_lookup: Any | None = None
        self.base_dir: str = "/opt/lvdata"
        # webroot 静态目录（docs/webroot_feature.md §3.1/§3.3）；未启用 = ""（prompt 不含 web 段）
        self.webroot_dir: str = ""

        self._mission_template = self._load_template(cfg.monitor_prompt_path, _MISSION_TEMPLATE)

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        """Start the watchdog, startup recovery and idle reaper.

        Opens no WS: device connections are created lazily on demand
        (R1「无 job 不建连」, D8).
        """
        self._closed = False
        if self.proactive_enabled:
            if self._watchdog_task is None:
                self._watchdog_task = asyncio.create_task(
                    self._watchdog_loop(), name="hermes-job-watchdog"
                )
            if self._cfg.startup_recovery_enabled and self._recover_task is None:
                self._recover_task = asyncio.create_task(
                    self.recover_active_devices(), name="hermes-recover"
                )
            if self._reaper_task is None:
                self._reaper_task = asyncio.create_task(
                    self._idle_reaper_loop(), name="hermes-device-reaper"
                )

    async def close(self) -> None:
        self._closed = True
        for conn in list(self._devices.values()):
            await self._stop_conn(conn)
        self._devices.clear()
        self._live_sessions.clear()
        set_device_connections(0)
        self._device_locks.clear()
        self._lifecycle_locks.clear()
        for task in (self._watchdog_task, self._recover_task, self._reaper_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._watchdog_task = None
        self._recover_task = None
        self._reaper_task = None

    async def _watchdog_loop(self) -> None:
        """Converge stale jobs (§4.4): running→timeout, requested→orphaned."""
        while not self._closed:
            await asyncio.sleep(self._cfg.watchdog_interval_s)
            if self.job_store is None:
                continue
            try:
                stale = await self.job_store.scan_stale(
                    time.time(),
                    running_timeout_s=self._cfg.background_timeout_s,
                    requested_timeout_s=self._cfg.job_requested_timeout_s,
                    agent_name=self.name,   # 双 adapter 共存：各自只收敛自己的 job
                )
                for job in stale:
                    event = "timeout" if job.fail_reason == "timeout" else "orphaned"
                    inc_job_event(self.name, event)
                    logger.warning(
                        "job converged by watchdog",
                        extra={"job_key": job.job_key, "reason": job.fail_reason},
                    )
                    if _is_session_job_ref(job.hermes_task_id):
                        try:
                            await self._interrupt_stale_job(job)
                        except Exception:
                            logger.warning(
                                "stale job interrupt failed",
                                extra={"job_key": job.job_key},
                                exc_info=True,
                            )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("job watchdog scan failed", exc_info=True)

    async def _interrupt_stale_job(self, job: Any) -> None:
        """Best-effort interrupt/close for a session-carrier job already failed
        by scan_stale. Missing connections are skipped; a missing in-memory
        mapping is rebuilt via ``session.resume`` so the remote turn is still
        stopped after a relay restart."""
        conn = self._devices.get(job.device_id)
        if conn is None:
            return
        js = conn.job_sessions.get(job.job_key)
        if js is None:
            try:
                await self._resume_job_session(conn, job, arm_recheck=False)
            except Exception:
                logger.warning(
                    "stale job: resume before interrupt failed",
                    extra={"job_key": job.job_key},
                    exc_info=True,
                )
                return
            js = conn.job_sessions.get(job.job_key)
            if js is None:
                return
        try:
            await self._interrupt_job_session(conn, js)
        finally:
            await self._cleanup_job_session(
                conn, job.job_key, js.hermes_session_id, close=True
            )

    # -- device connection management (D8) -----------------------------------

    async def _ensure_device(self, device_id: str) -> _DeviceConnection:
        """Lazily create (or return the existing) device connection.

        Idempotent under the per-device creation lock; waits for the WS to
        be ready before returning.
        """
        conn = self._devices.get(device_id)
        if conn is not None:
            await self._wait_ready(conn)
            return conn
        lock = self._device_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            conn = self._devices.get(device_id)
            if conn is not None:
                await self._wait_ready(conn)
                return conn
            if len(self._devices) >= self._cfg.max_device_connections:
                await self._evict_idle_lru()
            conn = _DeviceConnection(device_id=device_id)
            await self._load_device_mapping(conn)
            self._devices[device_id] = conn
            set_device_connections(len(self._devices))
            self._start_conn(conn)
            await self._wait_ready(conn)
            return conn

    async def _evict_idle_lru(self) -> None:
        """D8: 连接超限 → 按 last_active_at 驱逐最久未活动的无 active job 连接
        （驱逐 = 关 WS，不 close hermes session）。"""
        # +1：调用方即将新建一条连接，需为其腾出坑位（len == max 时驱逐 1 条）。
        to_evict = len(self._devices) - self._cfg.max_device_connections + 1
        if to_evict <= 0:
            return
        candidates: list[tuple[float, str, _DeviceConnection]] = []
        for dev, conn in self._devices.items():
            if await self._device_has_active_jobs(dev):
                continue
            candidates.append((conn.last_active_at, dev, conn))
        candidates.sort()
        for _, dev, conn in candidates:
            if to_evict <= 0:
                break
            if self._devices.get(dev) is conn:
                self._devices.pop(dev, None)
                to_evict -= 1
                await self._stop_conn(conn)
                logger.info(
                    "evicted idle device connection (LRU)",
                    extra={"device_id": dev},
                )

    def _start_conn(self, conn: _DeviceConnection) -> None:
        conn.reader_task = asyncio.create_task(
            self._reader_loop(conn), name=f"hermes-ws-reader-{conn.device_id}"
        )
        if self._cfg.liveness_probe_interval_s > 0:
            conn.liveness_task = asyncio.create_task(
                self._liveness_loop(conn), name=f"hermes-ws-liveness-{conn.device_id}"
            )

    async def _stop_conn(self, conn: _DeviceConnection) -> None:
        """Tear down a device connection.

        D3 default (session_retention=keep): the Hermes session is NOT
        closed (close_on_disconnect=false keeps it on the server, the
        mapping stays in Redis for resume). With
        session_retention=close_on_idle the Hermes session is closed and
        the mapping dropped instead (B2).
        """
        task = conn.reader_task
        conn.reader_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        live = conn.liveness_task
        conn.liveness_task = None
        if live is not None:
            live.cancel()
            try:
                await live
            except (asyncio.CancelledError, Exception):
                pass
        recovery = conn.job_recovery_task
        conn.job_recovery_task = None
        if recovery is not None:
            recovery.cancel()
            try:
                await recovery
            except (asyncio.CancelledError, Exception):
                pass
        for js in list(conn.job_sessions.values()):
            if js.recheck_task is not None:
                js.recheck_task.cancel()
                js.recheck_task = None
        conn.job_sessions.clear()
        conn.job_sid_to_key.clear()
        conn.job_resume_inflight.clear()
        conn.ready.clear()
        conn.stopped.set()
        self._fail_all_pending(
            conn, AdapterConnectionError("Hermes connection closed", adapter_name=self.name)
        )
        if (
            self._cfg.session_retention == "close_on_idle"
            and (conn.hermes_session_id or conn.stored_session_id)
        ):
            await self._close_hermes_session(conn)
        await self._teardown_ws(conn)
        set_device_connections(len(self._devices))
        logger.info(
            "hermes device connection closed",
            extra={"device_id": conn.device_id},
        )

    async def _close_hermes_session(self, conn: _DeviceConnection) -> None:
        """session_retention=close_on_idle: close the Hermes session and
        drop the device mapping (D3 fallback)."""
        sid = conn.hermes_session_id or conn.stored_session_id
        if sid:
            try:
                await self._send_notification(conn, "session.close", {"session_id": sid})
            except Exception:
                logger.warning(
                    "failed to close hermes session on idle",
                    extra={"device_id": conn.device_id},
                )
        conn.hermes_session_id = ""
        conn.stored_session_id = ""
        await self._delete_device_mapping(conn)

    async def _delete_device_mapping(self, conn: _DeviceConnection) -> None:
        if self.hermes_store is None:
            return
        try:
            await self.hermes_store.delete_hermes_session(conn.device_id)
        except Exception:
            logger.warning("failed to delete hermes device mapping", exc_info=True)

    async def _idle_reaper_loop(self) -> None:
        """Periodic sweep: close WS of device connections with no active job,
        no live chat session and no recent activity (D8/B1 reaper backstop)."""
        idle_threshold = max(2 * self._cfg.device_conn_idle_reaper_s, 30.0)
        while not self._closed:
            await asyncio.sleep(self._cfg.device_conn_idle_reaper_s)
            for device_id, conn in list(self._devices.items()):
                if await self._keep_connection(device_id):
                    continue
                if time.time() - conn.last_active_at < idle_threshold:
                    continue
                async with conn.start_lock:
                    if await self._keep_connection(device_id):
                        continue
                    if self._devices.get(device_id) is conn:
                        self._devices.pop(device_id, None)
                        await self._stop_conn(conn)
                logger.info(
                    "hermes device connection reaped (idle)",
                    extra={"device_id": device_id},
                )

    async def _keep_connection(self, device_id: str) -> bool:
        """D8/B1: keep the device WS while the device has active jobs OR at
        least one live chat session (a silent but connected chat keeps its
        connection; only fully idle devices are reaped)."""
        if self._live_sessions.get(device_id):
            return True
        return await self._device_has_active_jobs(device_id)

    async def maybe_reap_conn(self, device_id: str) -> None:
        """B3/D8: after a job delivery the device connection may no longer
        need to stay up (no active jobs, no live session). Called by the
        JobResultDeliverer; the reap is scheduled off the current task —
        the live-complete path runs on the reader task, which _stop_conn
        would cancel (self-cancel)."""
        conn = self._devices.get(device_id)
        if conn is None:
            return
        if await self._keep_connection(device_id):
            return
        asyncio.create_task(
            self._reap_conn(device_id),
            name=f"hermes-device-reap-{device_id}",
        )

    async def _reap_conn(self, device_id: str) -> None:
        conn = self._devices.get(device_id)
        if conn is None:
            return
        async with conn.start_lock:
            if self._devices.get(device_id) is not conn:
                return
            if await self._keep_connection(device_id):
                return
            self._devices.pop(device_id, None)
            await self._stop_conn(conn)
        logger.info(
            "hermes device connection reaped (idle, after delivery)",
            extra={"device_id": device_id},
        )

    async def _device_has_active_jobs(self, device_id: str) -> bool:
        if self.job_store is None:
            return False
        try:
            return bool(await self.job_store.outstanding_jobs_for_device(device_id))
        except Exception:
            # Redis 不可用时 fail-closed：无法确认无 active job，就保留连接。
            # 否则 Redis 抖动会触发大面积误回收，running job 结果随之丢失。
            logger.warning(
                "failed to query active jobs, keeping device connection",
                extra={"device_id": device_id},
                exc_info=True,
            )
            return True

    # -- connection plumbing --------------------------------------------------

    async def _connect_ws(self, conn: _DeviceConnection) -> None:
        """Connect the device WebSocket and wait for gateway.ready (reader's job)."""
        await self._teardown_ws(conn)
        logger.info(
            "connecting to hermes ws",
            extra={"device_id": conn.device_id, "url": redact_url(self._ws_url)},
        )
        try:
            conn.ws = await asyncio.wait_for(
                websockets.connect(
                    self._ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                ),
                timeout=self._cfg.timeout_s,
            )
        except TimeoutError:
            raise AdapterTimeoutError(
                f"Hermes WS connection timed out after {self._cfg.timeout_s}s",
                adapter_name=self.name,
            )
        except InvalidURI:
            # InvalidURI 的 str() 含完整 URI（含 token）；from None 同时断开异常链，
            # 避免后续 logger.exception 把原始异常打进 traceback。
            raise AdapterConnectionError(
                "Hermes WS URI is invalid",
                adapter_name=self.name,
            ) from None
        except OSError as exc:
            raise AdapterConnectionError(
                f"Hermes WS connection failed: {exc}",
                adapter_name=self.name,
            )

        while True:
            event = await self._recv_event(conn)
            if self._is_event(event, _EVENT_TYPE_GATEWAY_READY):
                break
        logger.info("hermes ws connected and ready", extra={"device_id": conn.device_id})

    async def _teardown_ws(self, conn: _DeviceConnection) -> None:
        if conn.ws is not None:
            try:
                await conn.ws.close()
            except Exception:
                pass
            conn.ws = None

    async def _wait_ready(self, conn: _DeviceConnection) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.timeout_s
        while True:
            if conn.ready.is_set():
                return
            if conn.stopped.is_set():
                # 连接已被 reaper/on_session_end 回收：持有过期引用的 waiter
                # 快速失败，下次 _ensure_device 重建，而不是等满超时（C1）。
                raise AdapterConnectionError(
                    "Hermes connection stopped", adapter_name=self.name
                )
            if conn.connect_error is not None:
                error = conn.connect_error
                conn.connect_error = None
                raise error
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(conn.ready.wait(), timeout=remaining)
            except TimeoutError:
                continue
        raise AdapterTimeoutError(
            f"Hermes WS connection timed out after {self._cfg.timeout_s}s",
            adapter_name=self.name,
        )

    # -- reader loop ----------------------------------------------------------

    async def _reader_loop(self, conn: _DeviceConnection) -> None:
        while not self._closed:
            try:
                await self._connect_ws(conn)
                conn.connect_error = None
                was_reconnect = conn.reconnect_attempts > 0
                conn.reconnect_attempts = 0
                conn.ready.set()
                # 仅断线重连时自动 resume（§8.2）；首次懒建连的 session 恢复
                # 由 ensure_session / recover_active_devices 负责，避免双 resume。
                if was_reconnect:
                    self._spawn_resume(conn)
                    self._spawn_job_resume(conn)
                while True:
                    msg = await self._recv_event(conn)
                    conn.last_active_at = time.time()
                    await self._handle_message(conn, msg)
            except asyncio.CancelledError:
                return
            except (ConnectionClosed, OSError) as exc:
                logger.warning(
                    "hermes ws connection lost, reconnecting",
                    extra={"device_id": conn.device_id, "error": str(exc)[:200]},
                )
                conn.ready.clear()
                self._fail_all_pending(conn, exc)
                await self._teardown_ws(conn)
                conn.reconnect_attempts += 1
                await asyncio.sleep(self._backoff_delay(conn))
            except Exception as exc:
                if isinstance(exc, (AdapterConnectionError, AdapterTimeoutError)):
                    # Connection-level failure: surface it to waiters.
                    conn.ready.clear()
                    conn.connect_error = exc
                    self._fail_all_pending(conn, exc)
                    await self._teardown_ws(conn)
                    conn.reconnect_attempts += 1
                    await asyncio.sleep(self._backoff_delay(conn))
                else:
                    logger.exception(
                        "hermes reader error",
                        extra={"device_id": conn.device_id},
                    )
                    await asyncio.sleep(1.0)

    # -- liveness probe (§5.8) ------------------------------------------------

    @staticmethod
    def _liveness_skip(conn: _DeviceConnection) -> bool:
        """§5.8 同步跳过条件：连接生命周期状态与在飞请求。

        在飞 turn/RPC 本身就是活性证据，且规避上游"长 turn 卡事件循环"
        窗口，防误杀。"""
        return (
            conn.ws is None
            or conn.stopped.is_set()
            or not conn.ready.is_set()
            or bool(conn.pending)
            or bool(conn.turn_futures)
        )

    @staticmethod
    def _liveness_probe_verdict(
        exc: Exception | None,
    ) -> Literal["alive", "timeout", "connection", "unexpected"]:
        """§5.8 计数规则：一次探测的结局。

        alive = 任意 JSON-RPC 响应——含 error 响应（`_resolve_rpc` 以带
        `status_code` 的 AdapterConnectionError 表达），同样证明分派层活着；
        timeout = 探测发出后无响应；connection = 连接层异常（status_code
        None / ConnectionClosed / OSError），不计失败不刷日志（reader 负责）；
        unexpected = 其余异常，仅记日志。"""
        if exc is None:
            return "alive"
        if isinstance(exc, AdapterTimeoutError):
            return "timeout"
        if isinstance(exc, AdapterConnectionError) and exc.status_code is not None:
            return "alive"
        if isinstance(exc, (AdapterConnectionError, ConnectionClosed, OSError)):
            return "connection"
        return "unexpected"

    async def _liveness_loop(self, conn: _DeviceConnection) -> None:
        """§5.8 应用层活性探测：保留连接空闲时周期发 `tools.list`、短超时
        （半死连接 TCP/协议层健康但应用层不通，无从被动检测）。

        连续 `liveness_probe_failures` 次超时 → 仅 `_teardown_ws`（不 pop
        `_devices`、不关 hermes 会话、不置 stopped），reader 随即按既有
        `ConnectionClosed` 路径重连。全空闲连接不探测：探测响应会刷新
        `last_active_at`，使 D8 idle reaper 永不回收。"""
        failures = 0
        counted_ws: ClientConnection | None = None
        while not self._closed:
            try:
                await asyncio.sleep(self._cfg.liveness_probe_interval_s)
                if self._liveness_skip(conn):
                    logger.debug(
                        "hermes liveness probe skipped",
                        extra={"device_id": conn.device_id},
                    )
                    continue
                if not await self._keep_connection(conn.device_id):
                    # 全空闲连接交给 D8 idle reaper（§5.8.2(2)）；Redis 抖动期
                    # _device_has_active_jobs fail-closed 返 True，退化为多探测
                    # 几次，无害。
                    logger.debug(
                        "hermes liveness probe skipped: device fully idle",
                        extra={"device_id": conn.device_id},
                    )
                    continue
                try:
                    # 计数 try 之外：重连风暴窗口此处抛的 AdapterTimeoutError
                    # 按"跳过本轮"处理，不计失败（§5.8.2 计数规则）。
                    await self._wait_ready(conn)
                except Exception:
                    continue
                if conn.ws is not counted_ws:
                    # 连接换代（重连）：上一条连接的失败不延续——"连续超时"
                    # 以同一连接为界，否则重连后第一条超时会搭上前一条的旧
                    # 计数，误拆刚重建的健康 ws（并污染 force_reconnect 信号）。
                    failures = 0
                    counted_ws = conn.ws
                probe_exc: Exception | None = None
                try:
                    inc_liveness_probe_sent()
                    await self._rpc(
                        conn, "tools.list", timeout=self._cfg.liveness_probe_timeout_s
                    )
                except Exception as exc:
                    probe_exc = exc
                verdict = self._liveness_probe_verdict(probe_exc)
                if verdict == "alive":
                    failures = 0
                elif verdict == "timeout":
                    failures += 1
                    inc_liveness_probe_timeout()
                    if failures >= self._cfg.liveness_probe_failures:
                        failures = 0
                        inc_liveness_force_reconnect()
                        logger.warning(
                            "hermes ws liveness probe failed, forcing reconnect",
                            extra={"device_id": conn.device_id},
                        )
                        await self._teardown_ws(conn)
                elif verdict == "unexpected":
                    logger.warning(
                        "hermes liveness probe unexpected error",
                        exc_info=probe_exc,
                        extra={"device_id": conn.device_id},
                    )
                # "connection" → 不计失败、不刷日志（reader 负责）；
                # 换代清零由上方 ws 身份检测负责
            except asyncio.CancelledError:
                return
            except Exception:
                # 单次意外异常不得杀死探测循环，否则保活静默停摆。
                logger.exception(
                    "hermes liveness probe loop error",
                    extra={"device_id": conn.device_id},
                )

    def _backoff_delay(self, conn: _DeviceConnection) -> float:
        """Exponential backoff + jitter (D11): initial→max, ±jitter ratio
        (jitter 后整体封顶 reconnect_backoff_max_s)。"""
        base = self._cfg.reconnect_backoff_initial_s * (2 ** conn.reconnect_attempts)
        jitter = base * self._cfg.reconnect_jitter
        return min(
            self._cfg.reconnect_backoff_max_s,
            max(0.0, base + random.uniform(-jitter, jitter)),
        )

    def _spawn_resume(self, conn: _DeviceConnection) -> None:
        """Resume the device's surviving session after reconnect, off the
        reader loop (session.resume is an RPC needing the reader). Runs
        under start_lock so it cannot race ensure_session/recover (C2)."""
        if conn.resume_task is not None and not conn.resume_task.done():
            return
        if not (conn.hermes_session_id or conn.stored_session_id):
            return
        conn.resume_task = asyncio.create_task(
            self._resume_task(conn),
            name=f"hermes-session-resume-{conn.device_id}",
        )

    async def _resume_task(self, conn: _DeviceConnection) -> None:
        async with conn.start_lock:
            outcome = await self._resume_conn_session(conn)
            if outcome in (ResumeOutcome.OK, ResumeOutcome.REAPED):
                # 重连后重申 mission（system prompt 不随 resume 恢复）；
                # 无活跃 livachat 会话（mission_ctx None）时跳过，
                # 等下一个 on_session_start 补发。
                await self._restate_mission(conn, conn.mission_ctx)

    async def _resume_conn_session(self, conn: _DeviceConnection) -> ResumeOutcome:
        """Resume the device's Hermes session via stored_session_id.

        session.resume resolves against the *stored* session id (DB key);
        the server may return the same id (20s grace-window live reuse) or
        a new one (old session reaped, DB rebuild). Jobs run in their own
        Hermes sessions and are unaffected by the device session's reap.

        Tri-state (A1/A2):
        - OK:     same sid, live reuse succeeded
        - REAPED: new sid — the returned device session is live (DB rebuild)
                  and can be used onward
        - FAILED: transient RPC failure — the mapping is KEPT so callers
                  can retry (recovery loop, next reconnect); it is only
                  dropped once a fresh session replaces it or the recovery
                  budget is exhausted
        """
        if not (conn.hermes_session_id or conn.stored_session_id):
            return ResumeOutcome.FAILED
        resume_id = conn.stored_session_id or conn.hermes_session_id
        old_sid = conn.hermes_session_id
        try:
            result = await self._rpc(conn, "session.resume", {"session_id": resume_id})
        except Exception as exc:
            # 瞬时失败：保留映射供调用方重试（recover / 下次断线重连）；
            # 标记 session_recovered 使 ensure_session 不直接复用存疑的 sid，
            # 而是先 resume 验证、失败则重建（A1）。
            logger.warning(
                "hermes session resume failed, will retry",
                extra={"device_id": conn.device_id, "error": str(exc)[:200]},
            )
            conn.session_recovered = True
            return ResumeOutcome.FAILED
        new_sid = result.get("session_id") or ""
        if new_sid:
            conn.hermes_session_id = new_sid
        if result.get("stored_session_id"):
            conn.stored_session_id = result["stored_session_id"]
        await self._save_device_mapping(conn)
        inc_session_resumed(self.name)
        conn.session_recovered = False
        if old_sid and new_sid and new_sid != old_sid:
            # 冷唤醒（设备空闲 >20s 后回来）resume 必走 DB 重建（§8.2 常规
            # 路径），新 sid 只说明旧 live 路由已死。job 各自有独立会话，
            # 由 _recover_job_sessions 独立收敛。
            logger.info(
                "hermes device session reaped (new session id)",
                extra={"device_id": conn.device_id, "old": old_sid, "new": new_sid},
            )
            return ResumeOutcome.REAPED
        logger.info(
            "hermes session resumed",
            extra={"device_id": conn.device_id, "hermes_session_id": conn.hermes_session_id},
        )
        return ResumeOutcome.OK

    # -- startup recovery (R1, D9) --------------------------------------------

    async def recover_active_devices(self) -> None:
        """R1: after relay restart, rebuild connections for devices with
        running jobs so their sessions can be resumed and reconciled.

        - bounded concurrency (recovery_concurrency)
        - running-priority: requested-only devices are skipped (no task_id
          to correlate; the 60s watchdog converges them)
        - per-device failure is isolated (return_exceptions=True); surviving
          running jobs are left for the watchdog backstop
        """
        if not self.proactive_enabled:
            return
        if self.job_store is None:
            return
        try:
            groups = await self.job_store.list_active_by_device()
        except Exception:
            logger.warning("recovery scan failed", exc_info=True)
            return
        def _needs_recovery(job: Any) -> bool:
            if job.agent_name != self.name:
                return False  # 双 adapter：各自只恢复自己的 job
            if job.status == STATUS_RUNNING:
                return True
            # A requested session-carrier job may have survived a crash between
            # bind and mark_running; its stored id is enough to recover/fail it.
            return (
                job.status == STATUS_REQUESTED
                and _is_session_job_ref(job.hermes_task_id)
            )

        targets = {
            d: jobs for d, jobs in groups.items()
            if any(_needs_recovery(j) for j in jobs)
        }
        if not targets:
            return
        logger.info(
            "recovering active devices",
            extra={"count": len(targets)},
        )
        sem = asyncio.Semaphore(self._cfg.recovery_concurrency)
        await asyncio.gather(
            *(self._recover_one(d, sem) for d in targets),
            return_exceptions=True,
        )

    async def _recover_one(
        self, device_id: str, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            try:
                conn = await self._ensure_device(device_id)
            except Exception as exc:
                logger.warning(
                    "recovery: device connect failed, leaving running jobs to watchdog",
                    extra={"device_id": device_id, "error": str(exc)[:200]},
                )
                return
            await self._resume_with_retries(conn)
            if self._cfg.job_recovery_enabled:
                await self._recover_job_sessions(conn)

    async def _resume_with_retries(self, conn: _DeviceConnection) -> ResumeOutcome:
        """Resume the device's Hermes session with the D10 retry budget
        (session_resume_retries × session_resume_backoff_s).

        Caller must NOT hold conn.start_lock — each attempt acquires it so
        concurrent ensure_session / recover callers interleave safely.
        Returns OK / REAPED / FAILED. Job sessions are recovered independently
        by _recover_job_sessions; a device-session resume outcome never fails
        a job.
        """
        if not (conn.hermes_session_id or conn.stored_session_id):
            return ResumeOutcome.FAILED
        for _ in range(self._cfg.session_resume_retries):
            async with conn.start_lock:
                outcome = await self._resume_conn_session(conn)
            if outcome is ResumeOutcome.OK:
                return outcome  # 20s 窗口内 live 复用成功
            if outcome is ResumeOutcome.REAPED:
                return outcome  # 已确认新 sid，不再重试
            # FAILED: 瞬时 RPC 失败，保留映射重试（窗口纪律：只对抗瞬时失败）
            await asyncio.sleep(self._cfg.session_resume_backoff_s)
        return ResumeOutcome.FAILED

    # -- v3 job-session recovery ---------------------------------------------

    def _spawn_job_resume(self, conn: _DeviceConnection) -> None:
        """After a WS reconnect, rebind every active job session to the new
        transport (off the reader loop)."""
        if not self._cfg.job_recovery_enabled or self.job_store is None:
            return
        if conn.job_recovery_task is not None and not conn.job_recovery_task.done():
            return
        conn.job_recovery_task = asyncio.create_task(
            self._recover_job_sessions(conn),
            name=f"hermes-job-recovery-{conn.device_id}",
        )

    async def _recover_job_sessions(self, conn: _DeviceConnection) -> None:
        if (
            not self._cfg.job_recovery_enabled
            or self.job_store is None
            or conn.stopped.is_set()
        ):
            return
        try:
            jobs = await self.job_store.list_active_for_device(conn.device_id)
        except Exception:
            logger.warning(
                "job recovery scan failed",
                extra={"device_id": conn.device_id},
                exc_info=True,
            )
            return
        for job in jobs:
            if not _is_session_job_ref(job.hermes_task_id):
                continue
            if job.job_key in conn.job_resume_inflight:
                continue
            try:
                await self._resume_job_session(conn, job)
            except Exception:
                logger.warning(
                    "job session recovery failed for one job",
                    extra={"job_key": job.job_key, "device_id": conn.device_id},
                    exc_info=True,
                )

    async def _resume_job_session(
        self, conn: _DeviceConnection, job: Any, *, arm_recheck: bool = True
    ) -> None:
        """Resume one job session and converge it from its transcript/status."""
        if self.job_store is None or conn.stopped.is_set():
            return
        ref = str(getattr(job, "hermes_task_id", "") or "")
        if not _is_session_job_ref(ref):
            return
        job_key = job.job_key
        if job_key in conn.job_resume_inflight:
            return
        stored = _stored_session_from_ref(ref)
        conn.job_resume_inflight.add(job_key)
        try:
            r = await self._resume_job_session_rpc(conn, stored)
            if r is None:
                if conn.stopped.is_set():
                    return
                await self._fail_job_recovery(conn, job_key, "session_lost")
                return
            live = str(r.get("session_id") or "")
            if not live:
                if conn.stopped.is_set():
                    return
                await self._fail_job_recovery(conn, job_key, "session_lost")
                return
            new_stored = str(r.get("stored_session_id") or stored)

            js = conn.job_sessions.get(job_key)
            old_live = js.hermes_session_id if js is not None else ""
            if js is None:
                js = _JobSession(job_key, live, new_stored or stored)
                conn.job_sessions[job_key] = js
            else:
                js.hermes_session_id = live
                js.stored_session_id = new_stored or js.stored_session_id
            if old_live and old_live != live and conn.job_sid_to_key.get(old_live) == job_key:
                conn.job_sid_to_key.pop(old_live, None)
            conn.job_sid_to_key[live] = job_key
            if new_stored and new_stored != stored:
                logger.info(
                    "job session stored id rotated; in-memory mapping updated",
                    extra={"job_key": job_key, "old": stored, "new": new_stored},
                )

            try:
                active = await self._rpc(
                    conn, "session.active_list", {"current_session_id": live}
                )
            except Exception:
                active = {}
            row = self._job_active_row(active, live, new_stored or stored)
            status = str((row or {}).get("status") or "")
            if not status:
                if r.get("running"):
                    status = "working"
                else:
                    status = str(r.get("status") or "idle")

            if status == "working":
                if job.status == STATUS_REQUESTED:
                    try:
                        marked = await self.job_store.mark_running(job_key, ref)
                    except Exception:
                        marked = False
                    if not marked:
                        try:
                            current = await self.job_store.get(job_key)
                        except Exception:
                            # Status unknown (store failure): do NOT close a
                            # possibly-working session. Keep the mapping for
                            # live completion / next recovery.
                            logger.warning(
                                "job recovery: status re-read failed, keeping session",
                                extra={"job_key": job_key, "session_id": live},
                                exc_info=True,
                            )
                            return
                        if current is None or current.status != STATUS_RUNNING:
                            await self._cleanup_job_session(conn, job_key, live, close=True)
                            return
                if arm_recheck:
                    self._arm_job_resume_recheck(conn, job_key)
                return

            if status == "waiting":
                await self._autorespond_job_waiting(conn, live, r)
                await asyncio.sleep(0.5)
                try:
                    active2 = await self._rpc(
                        conn, "session.active_list", {"current_session_id": live}
                    )
                except Exception:
                    active2 = {}
                row2 = self._job_active_row(active2, live, new_stored or stored)
                if str((row2 or {}).get("status") or "") == "waiting":
                    try:
                        await self._send_notification(
                            conn, "session.interrupt", {"session_id": live}
                        )
                    except Exception:
                        pass
                if arm_recheck:
                    self._arm_job_resume_recheck(conn, job_key)
                return

            if status == "starting":
                if arm_recheck:
                    self._arm_job_resume_recheck(conn, job_key)
                return
            if r.get("running"):
                if arm_recheck:
                    self._arm_job_resume_recheck(conn, job_key)
                return

            # idle: recover a terminal result from the persisted transcript.
            messages = await self._job_messages(conn, r, live)
            kind, text = _extract_job_result(messages)
            if kind == "none":
                # Transcript flush settles shortly after the turn ends.
                for delay in (0.5, 1.0, 2.0):
                    await asyncio.sleep(delay)
                    try:
                        h = await self._rpc(
                            conn, "session.history", {"session_id": live}
                        )
                    except Exception:
                        h = {}
                    kind, text = _extract_job_result(h.get("messages") or [])
                    if kind != "none":
                        break
            if conn.stopped.is_set():
                # Local shutdown/reap: leave the job for startup recovery
                # instead of failing it because our transport went away.
                return
            try:
                if kind == "interrupted":
                    await self.job_store.mark_failed(job_key, "interrupted")
                    inc_job_event(self.name, "interrupted")
                    await self._cleanup_job_session(conn, job_key, live, close=True)
                elif kind == "run_error":
                    await self.job_store.mark_failed(job_key, "run_error")
                    inc_job_event(self.name, "run_error")
                    await self._cleanup_job_session(conn, job_key, live, close=True)
                elif kind == "text":
                    if job.status == STATUS_REQUESTED:
                        try:
                            marked = await self.job_store.mark_running(job_key, ref)
                        except Exception:
                            marked = False
                        if not marked:
                            try:
                                current = await self.job_store.get(job_key)
                            except Exception:
                                # Status unknown (store failure): keep the
                                # session; live completion / next recovery will
                                # converge instead of killing a working turn.
                                logger.warning(
                                    "job recovery: status re-read failed, keeping session",
                                    extra={"job_key": job_key, "session_id": live},
                                    exc_info=True,
                                )
                                return
                            if current is None or current.status != STATUS_RUNNING:
                                await self._cleanup_job_session(
                                    conn, job_key, live, close=True
                                )
                                return
                    result_kind, out_text = classify_job_result(text)
                    if result_kind == "deliver":
                        await self.job_store.mark_done(job_key, out_text)
                        await self._cleanup_job_session(conn, job_key, live, close=True)
                        inc_job_event(self.name, "recovered_from_history")
                        await self._deliver_job(job_key)
                    else:
                        await self.job_store.mark_done(job_key)
                        await self.job_store.mark_delivered(job_key)
                        await self._cleanup_job_session(conn, job_key, live, close=True)
                        inc_job_event(self.name, "recovered_from_history")
                        inc_job_event(self.name, result_kind)
                else:
                    await self.job_store.mark_failed(job_key, "orphaned")
                    inc_job_event(self.name, "orphaned")
                    await self._cleanup_job_session(conn, job_key, live, close=True)
            except Exception:
                inc_job_event(self.name, "store_error")
                logger.warning(
                    "job recovery convergence failed; leaving recoverable",
                    extra={"job_key": job_key, "session_id": live},
                    exc_info=True,
                )
        finally:
            conn.job_resume_inflight.discard(job_key)

    async def _resume_job_session_rpc(
        self, conn: _DeviceConnection, stored: str
    ) -> dict[str, Any] | None:
        """Resume with the shared retry budget, but fail only this job."""
        attempts = max(1, int(self._cfg.session_resume_retries))
        for attempt in range(attempts):
            try:
                return await self._rpc(
                    conn, "session.resume", {"session_id": stored, "lazy": True}
                )
            except AdapterConnectionError as exc:
                if exc.status_code == 4007:
                    logger.warning(
                        "job session.resume: session not found",
                        extra={"stored_session_id": stored},
                    )
                    return None
                logger.warning(
                    "job session.resume failed, retrying",
                    extra={
                        "stored_session_id": stored,
                        "attempt": attempt + 1,
                        "error": str(exc)[:200],
                    },
                )
            except AdapterTimeoutError as exc:
                logger.warning(
                    "job session.resume timed out, retrying",
                    extra={
                        "stored_session_id": stored,
                        "attempt": attempt + 1,
                        "error": str(exc)[:200],
                    },
                )
            except Exception as exc:
                logger.warning(
                    "job session.resume unexpected error, retrying",
                    extra={
                        "stored_session_id": stored,
                        "attempt": attempt + 1,
                        "error": str(exc)[:200],
                    },
                )
            if attempt + 1 < attempts:
                await asyncio.sleep(self._cfg.session_resume_backoff_s)
        return None

    async def _fail_job_recovery(
        self, conn: _DeviceConnection, job_key: str, reason: str
    ) -> None:
        if conn.stopped.is_set():
            return
        if self.job_store is not None:
            try:
                await self.job_store.mark_failed(job_key, reason)
            except Exception:
                logger.warning(
                    "failed to mark job recovery failure",
                    extra={"job_key": job_key, "reason": reason},
                    exc_info=True,
                )
        inc_job_event(self.name, reason)
        await self._cleanup_job_session(conn, job_key, "", close=False)

    @staticmethod
    def _job_active_row(
        active: dict[str, Any], live: str, stored: str
    ) -> dict[str, Any] | None:
        for row in active.get("sessions") or []:
            if not isinstance(row, dict):
                continue
            if row.get("id") == live or (stored and row.get("session_key") == stored):
                return row
        return None

    async def _job_messages(
        self, conn: _DeviceConnection, resume_result: dict[str, Any], live: str
    ) -> list[dict[str, Any]]:
        messages = list(resume_result.get("messages") or [])
        if resume_result.get("messages_omitted"):
            try:
                history = await self._rpc(
                    conn, "session.history", {"session_id": live}
                )
                messages = list(history.get("messages") or [])
            except Exception:
                logger.warning(
                    "job session.history fallback failed",
                    extra={"session_id": live},
                    exc_info=True,
                )
        return messages

    async def _autorespond_job_waiting(
        self, conn: _DeviceConnection, live: str, resume_result: dict[str, Any]
    ) -> None:
        """Best-effort unblock of a recovered job stuck on approval/clarify."""
        inc_interaction_autorespond("approval")
        try:
            await self._send_notification(conn, "approval.respond", {
                "session_id": live,
                "choice": "deny",
                "all": True,
            })
        except Exception:
            pass
        inc_interaction_autorespond("clarify")
        # The original clarify.request event went to the pre-reconnect
        # transport; the resume payload replays it (with request_id) so the
        # response can actually resolve the pending request.
        pending = resume_result.get("pending_clarify") or {}
        request_id = str(pending.get("request_id") or "")
        response: dict[str, Any] = {
            "session_id": live,
            "answer": _CLARIFY_AUTORESPONSE,
        }
        if request_id:
            response["request_id"] = request_id
        try:
            await self._send_notification(conn, "clarify.respond", response)
        except Exception:
            pass

    def _arm_job_resume_recheck(
        self, conn: _DeviceConnection, job_key: str
    ) -> None:
        if not self._cfg.job_recovery_enabled:
            return
        js = conn.job_sessions.get(job_key)
        if js is None:
            return
        if js.recheck_task is not None and not js.recheck_task.done():
            return
        js.recheck_task = asyncio.create_task(
            self._job_resume_recheck(conn, job_key),
            name=f"hermes-job-recheck-{job_key}",
        )
        inc_job_event(self.name, "resume_recheck")

    async def _job_resume_recheck(
        self, conn: _DeviceConnection, job_key: str
    ) -> None:
        try:
            await asyncio.sleep(self._cfg.job_resume_recheck_s)
        except asyncio.CancelledError:
            return
        if self.job_store is None or conn.stopped.is_set():
            return
        try:
            job = await self.job_store.get(job_key)
        except Exception:
            logger.warning(
                "job resume recheck read failed", extra={"job_key": job_key}, exc_info=True
            )
            return
        if job is None:
            return
        if job.status not in (STATUS_REQUESTED, STATUS_RUNNING):
            # The job reached a terminal state while this recheck was pending.
            # Cleanup/delivery are both idempotent; empty results (NOOP /
            # error: / blank) must be consumed, never pushed.
            js = conn.job_sessions.get(job_key)
            await self._cleanup_job_session(
                conn, job_key, js.hermes_session_id if js is not None else "", close=True
            )
            if job.status == STATUS_DONE:
                if (job.result_text or "").strip():
                    await self._deliver_job(job_key)
                else:
                    try:
                        await self.job_store.mark_delivered(job_key)
                    except Exception:
                        logger.warning(
                            "failed to consume empty done job",
                            extra={"job_key": job_key},
                            exc_info=True,
                        )
            return
        js = conn.job_sessions.get(job_key)
        if js is not None:
            # Avoid self-cancel if convergence calls _cleanup_job_session.
            js.recheck_task = None
        try:
            await self._resume_job_session(conn, job, arm_recheck=False)
        except Exception:
            logger.warning(
                "job resume recheck failed", extra={"job_key": job_key}, exc_info=True
            )

    # -- message dispatch -----------------------------------------------------

    async def _handle_message(self, conn: _DeviceConnection, msg: dict[str, Any]) -> None:
        if "id" in msg:
            self._resolve_rpc(conn, msg)
            return
        if msg.get("method") != _EVENT_METHOD:
            return
        etype = self._event_type(msg)
        params = msg.get("params", {})
        payload = params.get("payload", {}) or {}

        if etype in _DELTA_EVENT_TYPES:
            return
        elif etype == _EVENT_TYPE_MESSAGE_COMPLETE:
            sid = self._event_session_id(params)
            text = payload.get("text", "")
            if not sid:
                return
            job_key = conn.job_sid_to_key.get(sid)
            if job_key is not None:
                await self._handle_job_message_complete(conn, job_key, sid, payload)
                return
            straggle_ts = conn.turn_stragglers.get(sid)
            if straggle_ts is not None:
                # 一次性消费：丢弃或放行都清除标记。置位窗口
                # (2×nudge_timeout_s) 内的下一条 complete 属于被打断的 turn
                # （P0: redirected turns still emit complete），丢弃防误解析；
                # 窗口外是更晚的新 turn（跨会话残留的旧标记），放行——
                # 否则新会话第一次正常回复会被误杀。
                conn.turn_stragglers.pop(sid, None)
                age_s = time.time() - straggle_ts
                if age_s <= self._cfg.nudge_timeout_s * 2:
                    logger.debug(
                        "dropping straggler message.complete",
                        extra={"device_id": conn.device_id, "session_id": sid, "age_s": round(age_s, 2)},
                    )
                    return
                logger.debug(
                    "straggler marker expired, accepting complete",
                    extra={"device_id": conn.device_id, "session_id": sid, "age_s": round(age_s, 2)},
                )
            future = conn.turn_futures.get(sid)
            if future is not None and not future.done():
                future.set_result(text)
        elif etype == _EVENT_TYPE_APPROVAL_REQUEST:
            await self._autorespond_approval(conn, params)
        elif etype == _EVENT_TYPE_CLARIFY_REQUEST:
            await self._autorespond_clarify(conn, params)
        elif etype in (_EVENT_TYPE_SUDO_REQUEST, _EVENT_TYPE_SECRET_REQUEST):
            await self._autorespond_security(conn, params, etype)
        elif etype == _EVENT_TYPE_ERROR:
            sid = self._event_session_id(params)
            job_key = conn.job_sid_to_key.get(sid) if sid else None
            if job_key is not None:
                await self._handle_job_error(conn, job_key, sid, payload)
                return
            self._fail_current_turn(conn, params)
        # other events (session.info, message.start, reasoning.available, ...)
        # are silently ignored

    def _resolve_rpc(self, conn: _DeviceConnection, msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        future = conn.pending.get(req_id)
        if future is None:
            return
        if "error" in msg:
            err = msg["error"]
            future.set_exception(
                AdapterConnectionError(
                    f"Hermes JSON-RPC error: {err.get('message', 'unknown')}",
                    adapter_name=self.name,
                    status_code=err.get("code"),
                )
            )
        else:
            future.set_result(msg.get("result", {}))

    def _fail_current_turn(self, conn: _DeviceConnection, params: dict[str, Any]) -> None:
        payload = params.get("payload", {}) or {}
        sid = self._event_session_id(params)
        if sid and sid in conn.turn_futures and not conn.turn_futures[sid].done():
            conn.turn_futures[sid].set_exception(
                AdapterConnectionError(
                    f"Hermes error event: {payload.get('message', 'unknown')}",
                    adapter_name=self.name,
                    status_code=payload.get("code"),
                )
            )

    def _fail_all_pending(self, conn: _DeviceConnection, exc: Exception) -> None:
        for future in list(conn.pending.values()):
            if not future.done():
                future.set_exception(exc)
        for future in list(conn.turn_futures.values()):
            if not future.done():
                future.set_exception(exc)
        # Reconnect resets turn identity: straggler tracking no longer applies.
        conn.turn_stragglers.clear()

    @staticmethod
    def _event_session_id(params: dict[str, Any]) -> str:
        sid = params.get("session_id") or (params.get("payload") or {}).get("session_id")
        return str(sid) if sid else ""

    # -- job-session carrier --------------------------------------------------

    async def _handle_job_message_complete(
        self, conn: _DeviceConnection, job_key: str, sid: str, payload: dict[str, Any]
    ) -> None:
        """Converge one job-session turn's terminal message.complete.

        Runs inside the reader loop: never await an RPC here (the response is
        read by this same reader). Session close is a fire-and-forget
        notification; store errors are contained and leave the job recoverable.
        """
        if self.job_store is None:
            return
        try:
            job = await self.job_store.get(job_key)
        except Exception:
            inc_job_event(self.name, "store_error")
            self._arm_job_resume_recheck(conn, job_key)
            logger.warning(
                "job_store.get failed while completing job; keeping mapping",
                extra={"job_key": job_key, "session_id": sid},
                exc_info=True,
            )
            return
        if job is None or job.status not in (STATUS_REQUESTED, STATUS_RUNNING):
            inc_job_event(self.name, "complete_unknown_job")
            await self._cleanup_job_session(conn, job_key, sid, close=True)
            return

        if job.status == STATUS_REQUESTED:
            # Defensive: normal path marks running before prompt.submit.
            try:
                marked = await self.job_store.mark_running(job_key, job.hermes_task_id)
            except Exception:
                marked = False
            if not marked:
                try:
                    job = await self.job_store.get(job_key)
                except Exception:
                    # Store failure: the completion event is consumed but the
                    # job may still be running. Keep the mapping and reconcile
                    # from the transcript via the D17 recheck.
                    inc_job_event(self.name, "store_error")
                    self._arm_job_resume_recheck(conn, job_key)
                    logger.warning(
                        "job completion: status re-read failed, keeping session",
                        extra={"job_key": job_key, "session_id": sid},
                        exc_info=True,
                    )
                    return
                if job is None or job.status != STATUS_RUNNING:
                    inc_job_event(self.name, "complete_unknown_job")
                    await self._cleanup_job_session(conn, job_key, sid, close=True)
                    return

        status = str(payload.get("status") or "complete")
        text = payload.get("text") or ""
        try:
            if status == "complete":
                kind, out_text = classify_job_result(text)
                if kind == "deliver":
                    await self.job_store.mark_done(job_key, out_text)
                    await self._cleanup_job_session(conn, job_key, sid, close=True)
                    await self._deliver_job(job_key)
                    inc_job_event(self.name, "completed")
                else:
                    # noop / rpc_error / empty: consumed, never pushed.
                    await self.job_store.mark_done(job_key)
                    await self.job_store.mark_delivered(job_key)
                    await self._cleanup_job_session(conn, job_key, sid, close=True)
                    inc_job_event(self.name, kind)
            elif status == "interrupted":
                await self.job_store.mark_failed(job_key, "interrupted")
                await self._cleanup_job_session(conn, job_key, sid, close=True)
                inc_job_event(self.name, "interrupted")
            else:
                await self.job_store.mark_failed(job_key, "run_error")
                await self._cleanup_job_session(conn, job_key, sid, close=True)
                inc_job_event(self.name, "run_error")
        except Exception:
            # Keep the job mapping and trigger the D17 recheck so a transient
            # store failure does not strand the completed turn until watchdog.
            inc_job_event(self.name, "store_error")
            self._arm_job_resume_recheck(conn, job_key)
            logger.warning(
                "job completion transition failed; leaving job recoverable",
                extra={"job_key": job_key, "session_id": sid, "status": status},
                exc_info=True,
            )

    async def _handle_job_error(
        self, conn: _DeviceConnection, job_key: str, sid: str, payload: dict[str, Any]
    ) -> None:
        if self.job_store is None:
            return
        try:
            await self.job_store.mark_failed(job_key, "run_error")
        except Exception:
            inc_job_event(self.name, "store_error")
            self._arm_job_resume_recheck(conn, job_key)
            logger.warning(
                "failed to mark job run_error; keeping mapping",
                extra={"job_key": job_key, "session_id": sid},
                exc_info=True,
            )
            return
        inc_job_event(self.name, "run_error")
        logger.warning(
            "job session emitted error event",
            extra={
                "job_key": job_key,
                "session_id": sid,
                "error": str(payload.get("message") or payload.get("error") or "")[:200],
            },
        )
        await self._cleanup_job_session(conn, job_key, sid, close=True)

    async def _deliver_job(self, job_key: str) -> None:
        """Deliver a done job via the shared deliverer (or the test push fallback)."""
        if self.job_store is None:
            return
        try:
            job = await self.job_store.get(job_key)
        except Exception:
            logger.warning("job delivery re-read failed", extra={"job_key": job_key}, exc_info=True)
            return
        if job is None:
            return
        deliverer = getattr(self, "deliverer", None)
        if deliverer is not None:
            try:
                await deliverer.deliver(job)
            except Exception:
                logger.warning("job result delivery failed", extra={"job_key": job_key}, exc_info=True)
            return
        cb = getattr(self, "_push_callback", None)
        if cb is not None:
            try:
                await cb(
                    job.device_id,
                    job.session_id,
                    AgentResponseChunk(text=job.result_text, is_final=True),
                )
            except Exception:
                logger.warning("job result push failed", extra={"job_key": job_key}, exc_info=True)

    async def _cleanup_job_session(
        self,
        conn: _DeviceConnection,
        job_key: str,
        live_sid: str = "",
        *,
        close: bool = True,
    ) -> None:
        """Drop in-memory job state and (best-effort) close the Hermes session."""
        js = conn.job_sessions.pop(job_key, None)
        sid = live_sid or (js.hermes_session_id if js is not None else "")
        for candidate in {sid, js.hermes_session_id if js is not None else ""}:
            if candidate and conn.job_sid_to_key.get(candidate) == job_key:
                conn.job_sid_to_key.pop(candidate, None)
        if js is not None and js.recheck_task is not None:
            task = js.recheck_task
            js.recheck_task = None
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
        if not (close and sid and conn.ws is not None):
            return
        try:
            await self._send_notification(conn, "session.close", {"session_id": sid})
            inc_job_event(self.name, "session_closed")
        except Exception:
            logger.debug(
                "job session.close notification failed",
                extra={"job_key": job_key, "session_id": sid},
                exc_info=True,
            )

    # -- interaction fallback matrix (§4.5) -----------------------------------

    async def _autorespond_approval(self, conn: _DeviceConnection, params: dict[str, Any]) -> None:
        """Auto-deny approval requests (v1 semantics, session_id now passed)."""
        sid = self._event_session_id(params)
        inc_interaction_autorespond("approval")
        logger.info("hermes auto-denying approval request", extra={"device_id": conn.device_id})
        await self._send_notification(conn, "approval.respond", {
            "session_id": sid,
            "choice": "deny",
            "all": True,
        })

    async def _autorespond_clarify(self, conn: _DeviceConnection, params: dict[str, Any]) -> None:
        sid = self._event_session_id(params)
        payload = params.get("payload", {}) or {}
        request_id = str(payload.get("request_id") or "")
        inc_interaction_autorespond("clarify")
        logger.info("hermes auto-answering clarify request", extra={"device_id": conn.device_id})
        response: dict[str, Any] = {
            "session_id": sid,
            "answer": _CLARIFY_AUTORESPONSE,
        }
        if request_id:
            # v0.20.2 `clarify.respond` requires the pending request_id; without
            # it the server returns 4009 and the clarify stays blocked.
            response["request_id"] = request_id
        await self._send_notification(conn, "clarify.respond", response)

    async def _autorespond_security(
        self, conn: _DeviceConnection, params: dict[str, Any], etype: str
    ) -> None:
        """sudo/secret have no deny semantics: alarm and interrupt the turn."""
        kind = "sudo" if etype == _EVENT_TYPE_SUDO_REQUEST else "secret"
        sid = self._event_session_id(params)
        inc_interaction_autorespond(kind)
        logger.warning(
            "hermes %s request in unattended mode, interrupting turn",
            kind,
            extra={"device_id": conn.device_id},
        )
        await self._send_notification(conn, "session.interrupt", {"session_id": sid})
        if sid and sid in conn.job_sid_to_key:
            asyncio.create_task(
                self._converge_interrupted_job(conn, conn.job_sid_to_key[sid], sid),
                name=f"hermes-job-security-{conn.job_sid_to_key[sid]}",
            )
        elif sid and sid in conn.turn_futures and not conn.turn_futures[sid].done():
            conn.turn_futures[sid].set_exception(
                AdapterConnectionError(
                    f"hermes {kind} request interrupted the turn",
                    adapter_name=self.name,
                )
            )

    # -- proactive lifecycle --------------------------------------------------

    async def ensure_session(self, conn: _DeviceConnection, context: AgentContext) -> bool:
        """Ensure the device has a live Hermes session: reuse in-memory,
        resume from the Redis mapping, or create fresh (D3/R3).

        Serialized under the device's start_lock so create/resume/close
        never race (the lifecycle task and the watcher's lazy start can
        both arrive). Returns True on success.
        """
        restate_for_language = False
        async with conn.start_lock:
            if conn.hermes_session_id and not conn.session_recovered:
                # 复用（连接未断、session 未重建）：mission 不写死
                # chat_session_id（以 NUDGE 为准），通常无需重申；但 App 语言
                # 变化时 mission 内的语言指令已过期，需重申一次
                # （docs/agent_multilingual.md D7/D10）。
                if conn.mission_ctx is not None and conn.mission_ctx.language != context.language:
                    restate_for_language = True
                else:
                    return True
            need_resume = bool(conn.stored_session_id)
        if restate_for_language:
            await self._restate_mission(conn, context)
            return True
        if need_resume:
            outcome = await self._resume_with_retries(conn)
            if outcome is not ResumeOutcome.FAILED:
                # OK: 20s 窗口内 live 复用；REAPED: DB 重建。Hermes 的
                # system prompt 不随 resume/DB 重建恢复（P0 实测
                # "Stored system prompt is null"，A2 假设已修正）——relay
                # 无探测接口，resume 成功后无条件重申 mission。
                await self._restate_mission(conn, context)
                return True
            # 重试耗尽：device session 重建；job 会话由 job recovery 独立
            # 收敛，不随 device resume 失败（P0-1）。
        async with conn.start_lock:
            # 重读：并发路径（重连 resume / 其他 ensure_session）可能已
            # 建立会话。
            if conn.hermes_session_id and not conn.session_recovered:
                return True
            await self._wait_ready(conn)
            mission = await self._render_mission(context)
            result = await self._rpc(conn, "session.create", {
                "close_on_disconnect": False,
                "source": "livachat-relay",
                "title": conn.device_id,
                "messages": [{"role": "system", "content": mission}],
            })
            hermes_sid = result.get("session_id") or ""
            if not hermes_sid:
                raise AdapterConnectionError(
                    "session.create response missing session_id",
                    adapter_name=self.name,
                )
            conn.hermes_session_id = hermes_sid
            conn.stored_session_id = result.get("stored_session_id") or ""
            conn.session_recovered = False
            await self._save_device_mapping(conn)
            # create 已 seed mission（session.create messages），无需重申；
            # mission_ctx 供重连 resume 后补发使用。
            conn.mission_ctx = context
            logger.info(
                "hermes session created",
                extra={"device_id": conn.device_id, "hermes_session_id": hermes_sid},
            )
            return True

    async def _restate_mission(self, conn: _DeviceConnection, context: AgentContext | None) -> bool:
        """Resume/复用路径重申 mission（Hermes 的 system prompt 不随
        session.resume / DB 重建恢复，且 relay 无探测接口，故无条件补发）。

        以 NUDGE 同款 JSON 包装，期望主 agent 回 noop（秒级）；超时走
        _submit_turn 的 straggler+interrupt 收敛，不阻塞主流程。
        """
        if context is None:
            logger.debug(
                "mission restate skipped (no live session context)",
                extra={"device_id": conn.device_id},
            )
            return False
        mission = await self._render_mission(context)
        prompt = json.dumps(
            {"type": "mission_restate", "mission": mission},
            ensure_ascii=False,
        )
        try:
            reply = await self._submit_turn(conn, prompt, timeout_s=self._cfg.nudge_timeout_s)
        except Exception as exc:
            logger.warning(
                "mission restate failed",
                extra={"device_id": conn.device_id, "error": str(exc)[:200]},
            )
            return False
        if reply is None:
            logger.warning(
                "mission restate timed out (main agent unresponsive)",
                extra={"device_id": conn.device_id, "session_id": conn.hermes_session_id},
            )
            return False
        contract = parse_contract(reply)
        action = contract.get("action") if isinstance(contract, dict) else None
        if action not in ("noop", "message"):
            logger.warning(
                "mission restate unexpected contract reply, treated as noop",
                extra={"device_id": conn.device_id, "action": action, "reply_head": reply[:120]},
            )
        conn.mission_ctx = context
        logger.info(
            "mission restated",
            extra={
                "device_id": conn.device_id,
                "hermes_session_id": conn.hermes_session_id,
                "chat_session_id": context.session_id,
                "action": action,
            },
        )
        return True

    async def on_session_start(self, context: AgentContext) -> None:
        """Ensure the device connection + Hermes session (lazily created)."""
        if not self.proactive_enabled:
            return
        device_id = context.device_id
        async with self._lifecycle_locks.setdefault(device_id, asyncio.Lock()):
            self._live_sessions.setdefault(device_id, set()).add(context.session_id)
            conn = await self._ensure_device(device_id)
            conn.last_active_at = time.time()
            await self.ensure_session(conn, context)

    async def on_session_end(self, context: AgentContext) -> None:
        """Session ended: the Hermes session survives (D3/D4). The WS is
        closed only when the device has no active jobs AND no other live
        chat session (B1); otherwise it stays up so job completion events
        keep flowing."""
        if not self.proactive_enabled:
            return
        device_id = context.device_id
        async with self._lifecycle_locks.setdefault(device_id, asyncio.Lock()):
            sids = self._live_sessions.get(device_id)
            if sids is not None:
                sids.discard(context.session_id)
                if not sids:
                    self._live_sessions.pop(device_id, None)
            conn = self._devices.get(device_id)
            if conn is None:
                return
            async with conn.start_lock:
                if await self._keep_connection(device_id):
                    logger.info(
                        "device session ended, keeping connection (active jobs or live session)",
                        extra={"device_id": device_id},
                    )
                    return
                if self._devices.get(device_id) is conn:
                    self._devices.pop(device_id, None)
                    await self._stop_conn(conn)

    async def nudge(
        self,
        update: ChatlogUpdate,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Submit a NUDGE prompt and parse the contract reply.

        Yields chunks only for `message` actions (and optionally the
        job-started notice). noop / parse failures / job dispatches are
        handled internally and never pushed to the user.
        """
        if not self.proactive_enabled:
            return
        if False:  # pragma: no cover — keep async generator
            yield
        logger.debug(
            "nudge received",
            extra={
                "device_id": context.device_id,
                "session_id": context.session_id,
                "new_lines": len(update.new_lines),
            },
        )
        conn = await self._ensure_device(context.device_id)
        conn.last_active_at = time.time()
        await self.ensure_session(conn, context)
        async with conn.lock:
            # 每轮都在数据前重复语言规则：只靠 mission 里的指令时，中文用户
            # 消息会让 agent 忽略目标语言（真实探针 2026-10-28）。
            text = language_hint(context.language, verbose=False) + "\n\n" + json.dumps(
                self._build_nudge_prompt(update, context.timezone),
                ensure_ascii=False,
            )
            reply = await self._submit_turn(conn, text, timeout_s=self._cfg.nudge_timeout_s)
            if reply is None:
                return

            contract = parse_contract(reply)
            action = contract.get("action") if isinstance(contract, dict) else None

            if action == "message":
                msg_text = (contract.get("text") or "").strip()
                if msg_text:
                    inc_nudge(self.name, "message")
                    yield AgentResponseChunk(text=msg_text, is_final=True)
                else:
                    inc_nudge(self.name, "noop")
            elif action == "job_request":
                inc_nudge(self.name, "job_request")
                async for chunk in self._handle_job_request(
                    conn, context.session_id, contract, update.path,
                    timezone=context.timezone,
                    language=context.language,
                ):
                    yield chunk
            elif action == "noop":
                inc_nudge(self.name, "noop")
            else:
                inc_nudge(self.name, "parse_error")
                # 消息文本内嵌 sid/reply：stdlib logging 的 extra 不会透传到
                # structlog JSON 输出（foreign_pre_chain 丢弃），只能打进消息。
                # 保持 warning 级别便于 dev 期定位 hermes 契约违例。
                reply_preview = (reply or "").replace("\n", "\\n")[:500]
                logger.warning(
                    f"contract parse failed, treated as noop; device={conn.device_id} reply={reply_preview}",
                )

    async def _handle_job_request(
        self,
        conn: _DeviceConnection,
        session_id: str,
        contract: dict[str, Any],
        chatlog_path: str = "",
        timezone: str = "",
        language: str = "",
    ) -> AsyncIterator[AgentResponseChunk]:
        """Validate caps → JobStore → dedicated job-session dispatch."""
        spec = contract.get("spec")
        summary = (contract.get("summary") or "").strip()
        if not isinstance(spec, dict) or not str(spec.get("task") or "").strip():
            logger.warning("job_request without valid spec, treated as noop")
            return
        if False:  # pragma: no cover — keep async generator
            yield
        if self.job_store is None:
            logger.warning("job_request received but no job store configured")
            return

        outstanding = await self.job_store.outstanding_jobs_for_device(conn.device_id)
        if len(outstanding) >= self._cfg.max_concurrent_jobs_per_device:
            inc_job_event(self.name, "rejected")
            logger.info(
                "job rejected: per-device cap reached",
                extra={"device_id": conn.device_id},
            )
            return
        if await self.job_store.in_flight_count() >= self._cfg.max_in_flight_jobs:
            inc_job_event(self.name, "rejected")
            logger.info("job rejected: global in-flight cap reached")
            return

        job = await self.job_store.create(
            session_id=session_id,
            device_id=conn.device_id,
            summary=summary,
        )
        inc_job_event(self.name, "requested")

        notice = await self._dispatch_job_session(
            conn, job, spec["task"], session_id, chatlog_path, timezone, language
        )
        if notice:
            yield AgentResponseChunk(text=notice, is_final=True)

    async def _dispatch_job_session(
        self,
        conn: _DeviceConnection,
        job: Any,
        task: str,
        session_id: str,
        chatlog_path: str,
        timezone: str,
        language: str = "",
    ) -> str | None:
        """Create a dedicated job session and dispatch the task into it.

        Runs in the nudge task (not the reader), so RPC awaits are safe. The
        whole create→bind→running→submit sequence is serialized against
        reconnect/recovery by conn.start_lock; the job_key is also marked
        in-flight so a reconnect recovery skips a half-dispatched job.
        """
        if self.job_store is None:
            return None
        job_key = job.job_key
        notice = job.summary if (self._cfg.notify_job_started and job.summary) else None
        async with conn.start_lock:
            try:
                await self._wait_ready(conn)
            except Exception as exc:
                await self.job_store.mark_failed(job_key, "create_error")
                inc_job_event(self.name, "create_error")
                logger.warning(
                    "job dispatch aborted: ws not ready",
                    extra={"job_key": job_key, "error": str(exc)[:200]},
                )
                return None
            start_ws = conn.ws
            try:
                created = await self._rpc(conn, "session.create", {
                    "close_on_disconnect": False,
                    "source": "livachat-relay",
                    "title": (job.summary or f"job:{job_key}")[:80],
                })
            except Exception as exc:
                await self.job_store.mark_failed(job_key, "create_error")
                inc_job_event(self.name, "create_error")
                logger.warning(
                    "job session.create failed",
                    extra={"job_key": job_key, "error": str(exc)[:200]},
                )
                return None

            live_sid = str(created.get("session_id") or "")
            stored_sid = str(created.get("stored_session_id") or "")
            if not live_sid or not stored_sid:
                if live_sid:
                    # The session exists but cannot be recovered without a
                    # stored id: close it instead of leaking an idle session.
                    try:
                        await self._send_notification(
                            conn, "session.close", {"session_id": live_sid}
                        )
                    except Exception:
                        pass
                await self.job_store.mark_failed(job_key, "create_error")
                inc_job_event(self.name, "create_error")
                logger.warning(
                    "job session.create missing session ids",
                    extra={"job_key": job_key, "session_id": live_sid},
                )
                return None

            conn.job_sessions[job_key] = _JobSession(job_key, live_sid, stored_sid)
            conn.job_sid_to_key[live_sid] = job_key
            conn.job_resume_inflight.add(job_key)
            try:
                ref = f"{_JOB_REF_SESSION_PREFIX}{stored_sid}"
                try:
                    bound = await self.job_store.bind_external_ref(job_key, ref)
                except Exception:
                    bound = False
                if not bound:
                    await self._cleanup_job_session(conn, job_key, live_sid, close=True)
                    await self.job_store.mark_failed(job_key, "create_error")
                    inc_job_event(self.name, "create_error")
                    logger.warning("job external ref bind failed", extra={"job_key": job_key})
                    return None

                try:
                    running = await self.job_store.mark_running(job_key, ref)
                except Exception:
                    running = False
                if not running:
                    # Watchdog already converged (or store error): abandon.
                    await self._cleanup_job_session(conn, job_key, live_sid, close=True)
                    return None
                inc_job_event(self.name, "started")

                task_desc = self._render_job_task(
                    task, session_id, chatlog_path, timezone, language
                )
                try:
                    reply = await self._rpc(conn, "prompt.submit", {
                        "session_id": live_sid,
                        "text": task_desc,
                    })
                except AdapterTimeoutError:
                    await self._fail_job_dispatch(conn, job_key, live_sid, "submit_error")
                    return None
                except AdapterConnectionError as exc:
                    reason = "session_cap" if exc.status_code == 4090 else "rpc_error"
                    await self._fail_job_dispatch(conn, job_key, live_sid, reason)
                    return None
                except Exception:
                    await self._fail_job_dispatch(conn, job_key, live_sid, "submit_error")
                    return None

                submit_status = str(reply.get("status") or "")
                if submit_status not in ("streaming", "redirected", "queued"):
                    inc_job_event(self.name, "submit_unexpected_status")
                    logger.warning(
                        "job prompt.submit returned unexpected status",
                        extra={"job_key": job_key, "status": submit_status[:80]},
                    )
                    await self._fail_job_dispatch(conn, job_key, live_sid, "submit_error")
                    return None
                if submit_status != "streaming":
                    # A fresh idle session should ack "streaming"; other values
                    # are accepted defensively but surfaced as protocol drift.
                    inc_job_event(self.name, "submit_unexpected_status")
                    logger.warning(
                        "job prompt.submit returned non-streaming status",
                        extra={"job_key": job_key, "status": submit_status},
                    )
                if conn.ws is not start_ws:
                    # The WS was replaced between create and submit: a
                    # reconnect recovery may have skipped this job while it was
                    # in-flight, and the completion event can be lost on the
                    # old transport. Arm the D17 one-shot recheck as backstop.
                    logger.info(
                        "job dispatched across a reconnect; arming recheck",
                        extra={"job_key": job_key},
                    )
                    self._arm_job_resume_recheck(conn, job_key)
            except asyncio.CancelledError:
                # Cancelled after the job session exists (most commonly while
                # awaiting prompt.submit): the turn may have been accepted, so
                # fail the job and close the session instead of leaving a
                # running-no-turn job until the 48h watchdog.
                await self._abort_cancelled_dispatch(conn, job_key, live_sid)
                raise
            finally:
                conn.job_resume_inflight.discard(job_key)

        return notice

    async def _abort_cancelled_dispatch(
        self, conn: _DeviceConnection, job_key: str, live_sid: str
    ) -> None:
        """Fail a job whose dispatcher was cancelled before submit completed.

        ``mark_failed`` is CAS-guarded, so a job that completed concurrently is
        left alone. The job session is closed because a cancelled
        ``prompt.submit`` may already have started its turn.
        """
        if self.job_store is not None:
            try:
                record = await self.job_store.mark_failed(
                    job_key, "dispatch_aborted"
                )
            except Exception:
                record = None
                logger.warning(
                    "failed to mark cancelled dispatch aborted",
                    extra={"job_key": job_key},
                    exc_info=True,
                )
            if record is not None and record.fail_reason == "dispatch_aborted":
                inc_job_event(self.name, "dispatch_aborted")
        await self._cleanup_job_session(conn, job_key, live_sid, close=True)

    async def _fail_job_dispatch(
        self, conn: _DeviceConnection, job_key: str, live_sid: str, reason: str
    ) -> None:
        if self.job_store is not None:
            try:
                await self.job_store.mark_failed(job_key, reason)
            except Exception:
                logger.warning(
                    "failed to mark job dispatch failure",
                    extra={"job_key": job_key, "reason": reason},
                    exc_info=True,
                )
        inc_job_event(self.name, reason)
        await self._cleanup_job_session(conn, job_key, live_sid, close=True)

    async def cancel_job(self, job_key: str, reason: str = "cancelled") -> bool:
        """Cancel a session-carrier job (adapter capability).

        Returns True when the job was active and cancellation was initiated or
        converged; False for terminal/unknown jobs.
        """
        if self.job_store is None:
            return False
        try:
            job = await self.job_store.get(job_key)
        except Exception:
            logger.warning("cancel_job: job store read failed", extra={"job_key": job_key}, exc_info=True)
            return False
        if job is None or job.status not in (STATUS_REQUESTED, STATUS_RUNNING):
            return False

        conn = self._devices.get(job.device_id)
        if conn is None:
            # No local connection (relay restarted): reconnect so the remote
            # job session can actually be interrupted, not just marked failed.
            try:
                conn = await self._ensure_device(job.device_id)
            except Exception:
                logger.warning(
                    "cancel_job: reconnect for remote interrupt failed",
                    extra={"job_key": job_key, "device_id": job.device_id},
                    exc_info=True,
                )
                try:
                    await self.job_store.mark_failed(job_key, reason)
                except Exception:
                    logger.warning(
                        "cancel_job: mark_failed failed",
                        extra={"job_key": job_key},
                        exc_info=True,
                    )
                inc_job_event(self.name, reason)
                return True

        js = conn.job_sessions.get(job_key)
        if js is None:
            try:
                await self._resume_job_session(conn, job, arm_recheck=False)
            except Exception:
                logger.warning(
                    "cancel_job: resume before cancel failed",
                    extra={"job_key": job_key},
                    exc_info=True,
                )
            js = conn.job_sessions.get(job_key)
            if js is None:
                try:
                    await self.job_store.mark_failed(job_key, reason)
                except Exception:
                    logger.warning(
                        "cancel_job: mark_failed after resume failure failed",
                        extra={"job_key": job_key},
                        exc_info=True,
                    )
                inc_job_event(self.name, reason)
                return True

        # 统一的「用户取消已发出」标签（与 OpenClaw 对齐）；终态 interrupted/
        # cancelled 仍表示收敛原因（hermes_adaptor_design_v3.md §5.6）
        inc_job_event(self.name, "cancel_sent")
        try:
            await self._interrupt_job_session(conn, js)
        except Exception:
            logger.warning("cancel_job: interrupt failed", extra={"job_key": job_key}, exc_info=True)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.job_cancel_grace_s
        while loop.time() < deadline:
            await asyncio.sleep(0.2)
            try:
                current = await self.job_store.get(job_key)
            except Exception:
                break
            if current is None or current.status not in (STATUS_REQUESTED, STATUS_RUNNING):
                return True

        try:
            await self.job_store.mark_failed(job_key, reason)
        except Exception:
            logger.warning("cancel_job: final mark_failed failed", extra={"job_key": job_key}, exc_info=True)
        inc_job_event(self.name, reason)
        await self._cleanup_job_session(conn, job_key, js.hermes_session_id, close=True)
        return True

    async def steer_job(self, job_key: str, text: str) -> bool:
        """Inject steering text into a running session-carrier job."""
        if self.job_store is None or not (text or "").strip():
            return False
        try:
            job = await self.job_store.get(job_key)
        except Exception:
            logger.warning("steer_job: job store read failed", extra={"job_key": job_key}, exc_info=True)
            return False
        if job is None or job.status != STATUS_RUNNING:
            return False
        conn = self._devices.get(job.device_id)
        if conn is None:
            return False
        js = conn.job_sessions.get(job_key)
        if js is None:
            try:
                await self._resume_job_session(conn, job, arm_recheck=False)
            except Exception:
                logger.warning("steer_job: resume failed", extra={"job_key": job_key}, exc_info=True)
                return False
            js = conn.job_sessions.get(job_key)
            if js is None:
                return False
        try:
            reply = await self._rpc(conn, "session.steer", {
                "session_id": js.hermes_session_id,
                "text": text,
            })
        except Exception:
            logger.warning("steer_job: session.steer failed", extra={"job_key": job_key}, exc_info=True)
            return False
        status = str(reply.get("status") or "")
        if status == "queued":
            return True
        logger.warning(
            "steer_job: unexpected status",
            extra={"job_key": job_key, "status": status[:80]},
        )
        return False

    async def _interrupt_job_session(
        self, conn: _DeviceConnection, js: _JobSession
    ) -> None:
        """Interrupt a job turn and (optionally) kill its tracked processes."""
        await self._rpc(conn, "session.interrupt", {
            "session_id": js.hermes_session_id,
        })
        if not self._cfg.job_cancel_kill_processes:
            return
        try:
            listed = await self._rpc(conn, "process.list", {
                "session_id": js.hermes_session_id,
            })
        except Exception:
            logger.debug(
                "job process.list failed",
                extra={"job_key": js.job_key},
                exc_info=True,
            )
            return
        for proc in listed.get("processes") or []:
            if not isinstance(proc, dict):
                continue
            # process.list exposes the registry id in "session_id".
            proc_id = str(proc.get("session_id") or "")
            if not proc_id:
                continue
            try:
                await self._rpc(conn, "process.kill", {
                    "session_id": js.hermes_session_id,
                    "process_id": proc_id,
                })
            except Exception:
                logger.debug(
                    "job process.kill failed",
                    extra={"job_key": js.job_key, "process_id": proc_id},
                    exc_info=True,
                )

    async def _converge_interrupted_job(
        self, conn: _DeviceConnection, job_key: str, live_sid: str
    ) -> None:
        """Security autorespond grace: fail a job if no interrupted complete lands."""
        try:
            await asyncio.sleep(self._cfg.job_cancel_grace_s)
        except asyncio.CancelledError:
            return
        if self.job_store is None:
            return
        try:
            current = await self.job_store.get(job_key)
        except Exception:
            return
        if current is None or current.status not in (STATUS_REQUESTED, STATUS_RUNNING):
            return
        try:
            await self.job_store.mark_failed(job_key, "interrupted")
        except Exception:
            logger.warning(
                "failed to converge interrupted job",
                extra={"job_key": job_key},
                exc_info=True,
            )
            return
        inc_job_event(self.name, "interrupted")
        await self._cleanup_job_session(conn, job_key, live_sid, close=True)

    # -- reactive ------------------------------------------------------------

    async def ask(
        self,
        text: str,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Reactive turn: session.create → prompt.submit → message.complete → close."""
        if not self.reactive_enabled:
            return
        if False:  # pragma: no cover — keep async generator
            yield
        last_exc: Exception | None = None
        for attempt in range(self._cfg.max_retries + 1):
            try:
                async for chunk in self._ask_impl(text, context):
                    yield chunk
                return
            except AdapterConnectionError:
                raise
            except AdapterTimeoutError:
                raise
            except (TimeoutError, ConnectionClosed, OSError) as exc:
                last_exc = exc
                logger.warning(
                    "hermes ws connection lost, retrying",
                    extra={"attempt": attempt, "adapter": self.name},
                )
                # NOT a full close(): close() cancels the reader/watchdog and
                # fails every in-flight proactive nudge turn (mode="both").
                # Tear the socket down so the reader's recv fails immediately
                # and it reconnects — clearing _ready alone could wait forever
                # if the connection is actually healthy but the send failed
                # transiently.
                conn = self._devices.get(context.device_id)
                if conn is not None:
                    await self._teardown_ws(conn)
                    conn.ready.clear()
            except Exception as exc:
                raise AdapterConnectionError(
                    f"Hermes WS error: {exc}",
                    adapter_name=self.name,
                ) from exc

        if last_exc:
            if isinstance(last_exc, asyncio.TimeoutError):
                raise AdapterTimeoutError(
                    "Hermes WS request timed out",
                    adapter_name=self.name,
                ) from last_exc
            raise AdapterConnectionError(
                f"Hermes WS connection failed after retries: {last_exc}",
                adapter_name=self.name,
            ) from last_exc

    async def _ask_impl(
        self,
        text: str,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        conn = await self._ensure_device(context.device_id)
        conn.last_active_at = time.time()
        ws = conn.ws
        if ws is None:
            raise AdapterConnectionError("WebSocket is not connected", adapter_name=self.name)

        result = await self._rpc(conn, "session.create", {
            "close_on_disconnect": True,
            "source": "tool",
        })
        session_id = result.get("session_id") or ""
        if not session_id:
            raise AdapterConnectionError(
                "session.create response missing session_id",
                adapter_name=self.name,
            )
        try:
            future = await self._register_turn(conn, session_id)
            await self._rpc(conn, "prompt.submit", {
                "session_id": session_id,
                "text": text.rstrip() + "\n\n" + language_hint(context.language, verbose=False),
            })
            reply = await asyncio.wait_for(future, timeout=self._cfg.timeout_s)
            yield AgentResponseChunk(text=reply, is_final=True)
        except TimeoutError:
            raise AdapterTimeoutError(
                f"Hermes turn timed out after {self._cfg.timeout_s}s",
                adapter_name=self.name,
            ) from None
        finally:
            conn.turn_futures.pop(session_id, None)
            try:
                await self._send_notification(conn, "session.close", {
                    "session_id": session_id,
                })
            except Exception:
                logger.warning("hermes session.close failed", exc_info=True)

    async def analyze_recording(
        self,
        recording: RecordingInfo,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """No-op: Hermes WS has no /api/analyze equivalent."""
        return
        yield  # pragma: no cover — make this an async generator

    # -- contract parsing (§4.3) ----------------------------------------------
    # 解析实现已抽出到 agent_relay.contract（hermes / openclaw 共用同一契约）。

    def _build_nudge_prompt(
        self, update: ChatlogUpdate, timezone: str = ""
    ) -> dict[str, Any]:
        return {
            "type": "chatlog_update",
            "chatlog_path": update.path,
            "chat_session_id": update.chat_session_id,
            "timezone": timezone,     # 设备本机时区（每轮刷新；未上报 = ""）
            "new_lines": [
                {"role": line.role, "text": line.text}
                for line in update.new_lines
            ],
            "truncated": update.truncated,
            "outstanding_jobs": update.outstanding_jobs,
        }

    # -- templates ------------------------------------------------------------
    # 提示文案函数已抽出到 agent_relay.prompt_hints（hermes / openclaw 同文共用）。

    async def _render_mission(self, context: AgentContext) -> str:
        chatlog_path = ""
        if self.recording_lookup is not None:
            chatlog_path = await resolve_chatlog_path(
                self.recording_lookup,
                device_id=context.device_id,
                base_dir=self.base_dir,
            ) or ""
        path_hint = (
            "每次 NUDGE 通知都会带当前 chatlog 路径,以最新通知为准。"
            if not chatlog_path
            else ""
        )
        rendered = string.Template(self._mission_template).safe_substitute(
            chatlog_path=chatlog_path,
            chat_session_id=context.session_id,
            path_hint=path_hint,
            timezone_hint=timezone_hint(context.timezone, verbose=True),
            agent_name_hint=agent_name_hint(self._cfg.nickname, verbose=True),
            webroot_hint=webroot_hint(self.webroot_dir, verbose=True),
        )
        return rendered.rstrip() + "\n\n" + language_hint(context.language, verbose=True)

    def _render_job_task(
        self, task: str, chat_session_id: str, chatlog_path: str,
        timezone: str = "", language: str = "",
    ) -> str:
        template = _JOB_TASK_TEMPLATE.read_text(encoding="utf-8")
        rendered = string.Template(template).safe_substitute(
            task=task,
            chat_session_id=chat_session_id,
            chatlog_path=chatlog_path or "(无)",
            timezone_hint=timezone_hint(timezone, verbose=False),
            agent_name_hint=agent_name_hint(self._cfg.nickname, verbose=False),
            webroot_hint=webroot_hint(self.webroot_dir, verbose=False),
        )
        return rendered.rstrip() + "\n\n" + language_hint(language, verbose=False)

    @staticmethod
    def _load_template(override_path: str, default: Path) -> str:
        if override_path:
            path = Path(override_path)
            if path.exists():
                return path.read_text(encoding="utf-8")
            logger.warning("monitor prompt path not found, using default", extra={"path": override_path})
        return default.read_text(encoding="utf-8")

    # -- device session mapping persistence (D15 / §3.6) ----------------------
    #
    # 映射落 chat_server.db `hermes_device_sessions` 表（ChatStore 同步直写，
    # device_uuid lower 归一——防 casing 不一致静默 miss）。TTL 取消：旧行
    # 无害，新 create/resume 覆盖旧行。

    async def _load_device_mapping(self, conn: _DeviceConnection) -> None:
        if self.hermes_store is None:
            return
        try:
            data = await self.hermes_store.load_hermes_session(conn.device_id)
        except Exception:
            logger.warning(
                "failed to load hermes device mapping",
                extra={"device_id": conn.device_id},
            )
            return
        if not data:
            return
        conn.hermes_session_id = str(data.get("hermes_session_id") or "")
        conn.stored_session_id = str(data.get("stored_session_id") or "")
        # 恢复的 sid 必须经 session.resume 验证后才能使用（R3/§5.2）
        conn.session_recovered = True

    async def _save_device_mapping(self, conn: _DeviceConnection) -> None:
        if self.hermes_store is None:
            return
        try:
            await self.hermes_store.save_hermes_session(
                device_uuid=conn.device_id,
                hermes_session_id=conn.hermes_session_id,
                stored_session_id=conn.stored_session_id,
            )
        except Exception:
            logger.warning("failed to persist hermes device mapping", exc_info=True)

    # -- turn submission ------------------------------------------------------

    async def _submit_turn(
        self,
        conn: _DeviceConnection,
        text: str,
        *,
        timeout_s: float,
    ) -> str | None:
        """Send prompt.submit and wait for message.complete. Returns None on
        timeout / error (metrics are recorded by the caller). The whole turn
        (RPC + completion) is bounded by timeout_s, not just the wait."""
        future = await self._register_turn(conn, conn.hermes_session_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        logger.debug(
            "nudge turn submitted",
            extra={
                "device_id": conn.device_id,
                "session_id": conn.hermes_session_id,
                "timeout_s": timeout_s,
            },
        )
        try:
            await self._rpc(conn, "prompt.submit", {
                "session_id": conn.hermes_session_id,
                "text": text,
            })
        except AdapterTimeoutError:
            # RPC itself timed out (cfg.timeout_s may fire before
            # nudge_timeout_s). Hermes may still have received the submit —
            # treat it as a turn timeout so the straggler guard applies.
            conn.turn_futures.pop(conn.hermes_session_id, None)
            inc_nudge(self.name, "timeout")
            if not future.done():
                await self._arm_straggler(conn, stage="rpc_timeout")
            return None
        except AdapterConnectionError:
            conn.turn_futures.pop(conn.hermes_session_id, None)
            inc_nudge(self.name, "error")
            return None
        remaining = deadline - loop.time()
        if remaining <= 0:
            # The whole turn exceeded its deadline. If the complete already
            # arrived it was consumed; otherwise a late one may still come —
            # mark the straggler so it cannot be parsed as the next turn's
            # contract (P2-5).
            inc_nudge(self.name, "timeout")
            if not future.done():
                await self._arm_straggler(conn, stage="deadline")
            conn.turn_futures.pop(conn.hermes_session_id, None)
            return None
        try:
            reply = await asyncio.wait_for(future, timeout=remaining)
            logger.debug(
                "nudge turn complete",
                extra={
                    "device_id": conn.device_id,
                    "session_id": conn.hermes_session_id,
                    "reply_len": len(reply),
                },
            )
            return reply
        except TimeoutError:
            inc_nudge(self.name, "timeout")
            # The Hermes-side turn keeps running and may emit a late
            # message.complete; mark it so that complete cannot be mistaken
            # for the next turn's contract (P2-5). 主 agent 超时说明其异常
            # （如主 turn 卡死）——interrupt 打断坏 turn，防后续 nudge
            # 无限排队进坏 turn。
            await self._arm_straggler(conn, stage="wait_timeout")
            return None
        except AdapterConnectionError:
            inc_nudge(self.name, "error")
            return None
        finally:
            conn.turn_futures.pop(conn.hermes_session_id, None)

    async def _arm_straggler(self, conn: _DeviceConnection, *, stage: str) -> None:
        """nudge 超时收敛：置位 straggler（丢被打断 turn 的补发 complete），
        并 interrupt 坏 turn 防后续 nudge 无限排队（节流 10s 防风暴）。"""
        conn.turn_stragglers[conn.hermes_session_id] = time.time()
        logger.warning(
            "nudge turn timed out, interrupting hermes turn",
            extra={
                "device_id": conn.device_id,
                "session_id": conn.hermes_session_id,
                "stage": stage,
            },
        )
        now = time.time()
        if now - conn.last_interrupt_at < _INTERRUPT_THROTTLE_S:
            return
        conn.last_interrupt_at = now
        try:
            await self._send_notification(
                conn, "session.interrupt", {"session_id": conn.hermes_session_id}
            )
            logger.info(
                "interrupted stalled hermes turn",
                extra={"device_id": conn.device_id, "session_id": conn.hermes_session_id},
            )
        except Exception as exc:
            logger.warning(
                "session.interrupt failed",
                extra={"device_id": conn.device_id, "error": str(exc)[:200]},
            )

    async def _register_turn(self, conn: _DeviceConnection, session_id: str) -> asyncio.Future[str]:
        """Register the turn future *before* prompt.submit so a fast
        message.complete arriving right after the RPC response is not lost."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        conn.turn_futures[session_id] = future
        return future

    # -- JSON-RPC helpers -----------------------------------------------------

    @property
    def _ws_url(self) -> str:
        base = self._cfg.base_url.rstrip("/")
        if base.startswith("https://"):
            ws_base = "wss://" + base[8:]
        elif base.startswith("http://"):
            ws_base = "ws://" + base[7:]
        else:
            ws_base = base
        token = self._cfg.api_key
        if token:
            return f"{ws_base}/api/ws?token={token}"
        return f"{ws_base}/api/ws"

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    @staticmethod
    def _is_event(msg: dict[str, Any], event_type: str) -> bool:
        """Check if msg is an 'event' method with the given params.type."""
        return (
            msg.get("method") == _EVENT_METHOD
            and msg.get("params", {}).get("type") == event_type
        )

    @staticmethod
    def _event_type(msg: dict[str, Any]) -> str:
        """Return params.type for an 'event' method msg, or empty string."""
        if msg.get("method") == _EVENT_METHOD:
            return msg.get("params", {}).get("type", "")
        return ""

    async def _send_notification(
        self,
        conn: _DeviceConnection,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Send a JSON-RPC 2.0 notification (no id, no response expected)."""
        if conn.ws is None:
            raise AdapterConnectionError("WebSocket is not connected", adapter_name=self.name)
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            msg["params"] = params
        await conn.ws.send(json.dumps(msg, ensure_ascii=False))

    async def _rpc(
        self,
        conn: _DeviceConnection,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and wait for the response.

        `timeout` 缺省（None）走 `cfg.timeout_s`；§5.8 探测传短超时。"""
        await self._wait_ready(conn)
        if conn.ws is None:
            raise AdapterConnectionError("WebSocket is not connected", adapter_name=self.name)
        req_id = self._next_id()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        conn.pending[req_id] = future
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": req_id,
        }
        if params:
            msg["params"] = params
        try:
            await conn.ws.send(json.dumps(msg, ensure_ascii=False))
            return await asyncio.wait_for(
                future, timeout=self._cfg.timeout_s if timeout is None else timeout
            )
        except TimeoutError:
            raise AdapterTimeoutError(
                f"Hermes JSON-RPC request ({method}) timed out",
                adapter_name=self.name,
            ) from None
        finally:
            conn.pending.pop(req_id, None)

    async def _recv_event(self, conn: _DeviceConnection) -> dict[str, Any]:
        """Receive and parse a JSON-RPC 2.0 message from the WebSocket."""
        if conn.ws is None:
            raise AdapterConnectionError("WebSocket is not connected", adapter_name=self.name)
        raw = await conn.ws.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
