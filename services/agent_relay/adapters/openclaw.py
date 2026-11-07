"""OpenClawAdapter — LivaChat relay ↔ OpenClaw Gateway（B 方案）。

Design: docs/openclaw_adaptor_design.md（协议事实与 P0 实测见
docs/openclaw_websocket_api.md）。

核心形态：
- **单条 WS 连接**服务全部 device（D3；与 hermes 的 per-device 相反），懒建 +
  指数退避重连 + tick 静默/seq 空洞判死（D10）。
- 会话键确定性派生（D8）：监听 `livachat-dev-<device_id>`、reactive
  `livachat-req-<device_id>`、job `livachat-job-<job_key>`——键即恢复凭据，无映射表。
- **Job = relay 自建会话 + 一次 run（B 方案，D4）**：`sessions.create`（不传
  idempotencyKey——P0-Q1：后端路径带它会被拒）→ `sessions.messages.subscribe`
  → `chat.send`（runId == idempotencyKey，客户端掌控，P0-Q2）→ 订阅内按 runId
  关联 `chat` final/error/aborted → JobStore 收敛 → JobResultDeliverer 投递。
- 取消（D6）：watchdog 超时 / 对账不决时尽力 `sessions.abort {runId}`。
- 启动/重连对账（D12）：`sessions.describe`（hasActiveRun/lastRunId/
  lastRunError，P0-Q3）+ `chat.history` 取回终稿；requested job 幂等重发
  （P0-Q6：同键重发不产生重复 run）。
- NUDGE 自包含（D7）：契约块 + 数据 JSON 两段式（sessions.create 无 messages
  种子通道，注入方式见 design §4.3）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import string
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from agent_relay.adapters.base import (
    AgentAdapter,
    AgentContext,
    AgentResponseChunk,
    ChatlogUpdate,
)
from agent_relay.config import OpenClawAdapterConfig
from agent_relay.contract import parse_contract
from agent_relay.errors import AdapterConnectionError, AdapterTimeoutError
from agent_relay.job_store import STATUS_REQUESTED, STATUS_RUNNING, JobStore
from agent_relay.metrics import (
    inc_gateway_reconnect,
    inc_gateway_seq_gap,
    inc_interaction_autorespond,
    inc_job_event,
    inc_nudge,
)
from agent_relay.prompt_hints import (
    agent_name_hint,
    language_hint,
    timezone_hint,
    webroot_hint,
)

logger = logging.getLogger("livachat.agent_relay.adapter.openclaw")

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_MONITOR_TEMPLATE = _PROMPTS_DIR / "openclaw_monitor.md"
_BACKGROUND_TEMPLATE = _PROMPTS_DIR / "openclaw_background_task.md"

_NOOP_OUTPUT = "NOOP"
_CLIENT_ID = "gateway-client"     # 后端免配对路径要求字面量（协议文档 §2.2）
_CLIENT_MODE = "backend"
_CLIENT_VERSION = "0.1.0"
_PROTOCOL = 4

# 会话键前缀（确定性键 = 恢复凭据，D8）
_LISTENING_PREFIX = "livachat-dev-"
_REACTIVE_PREFIX = "livachat-req-"
_JOB_PREFIX = "livachat-job-"

# 交互兜底（§4.5）：仅处理属于 relay 自建会话的审批/提问（防误伤用户其它会话）
_RELAY_KEY_MARKERS = (_LISTENING_PREFIX, _REACTIVE_PREFIX, _JOB_PREFIX)

# _wait_ready 的短轮询周期：连接失败/停止在 ≤该值内被等待者感知（成功路径
# ready.set 立即唤醒，不受影响）。
_READY_POLL_S = 0.5


class _ReconnectRequested(Exception):
    """Internal: force the reader to tear down and reconnect."""


@dataclass
class _Connection:
    """Single gateway connection state (D3)."""

    ws: ClientConnection | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    reader_task: asyncio.Task[None] | None = None
    silence_task: asyncio.Task[None] | None = None
    req_id: int = 0
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    # runId → 等待中的 turn future（nudge/ask；重连时弹出失败）
    run_waits: dict[str, asyncio.Future[str | None]] = field(default_factory=dict)
    # runId → 累积 delta（最终文本优先取 final 的 message 快照，deltas 兜底）
    run_deltas: dict[str, str] = field(default_factory=dict)
    # runId → job_key（**跨重连保留**：重连后终态事件仍要能路由到 job）
    job_runs: dict[str, str] = field(default_factory=dict)
    # job_key → 规范会话键（unsubscribe 用）
    job_sessions: dict[str, str] = field(default_factory=dict)
    subscribed: set[str] = field(default_factory=set)
    sub_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    seq_last: int | None = None
    last_event_at: float = field(default_factory=time.time)
    tick_interval_ms: int = 30_000
    reconnect_attempts: int = 0
    connect_error: Exception | None = None


class OpenClawAdapter(AgentAdapter):
    """Adapter for the OpenClaw Gateway WebSocket protocol (B 方案)."""

    name = "openclaw"

    @property
    def nickname(self) -> str:
        return self._cfg.nickname

    def __init__(self, cfg: OpenClawAdapterConfig) -> None:
        self._cfg = cfg
        self._conn: _Connection | None = None
        # per-device 锁：监听 turn / reactive turn 串行化（hermes conn.lock 同语义）
        self._device_locks: dict[str, asyncio.Lock] = {}
        # (kind, device_id) → 规范会话键缓存（会话服务端持久，键不变）
        self._session_keys: dict[tuple[str, str], str] = {}
        self._watchdog_task: asyncio.Task[None] | None = None
        self._reconcile_task: asyncio.Task[None] | None = None
        # 对账去重（P2）：在跑时的新请求合并为"补跑一轮"，防启动/重连路径并发
        self._reconcile_inflight = False
        self._reconcile_pending = False
        self._closed = False

        self.reactive_enabled = cfg.mode in ("reactive", "both")
        self.proactive_enabled = cfg.mode in ("proactive", "both")

        # 注入点（application wiring；单测可留 None）
        self.job_store: JobStore | None = None
        self.webroot_dir: str = ""   # webroot 静态目录；未启用 = ""（prompt 不含 web 段）

        self._monitor_template = self._load_template(cfg.monitor_prompt_path, _MONITOR_TEMPLATE)
        self._task_template = self._load_template(cfg.task_prompt_path, _BACKGROUND_TEMPLATE)

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        """Start the watchdog + startup reconcile. Opens no WS (懒建，R1 同款)."""
        self._closed = False
        if self.proactive_enabled and self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(
                self._watchdog_loop(), name="openclaw-job-watchdog"
            )
        if self._cfg.startup_recovery_enabled and self._reconcile_task is None:
            self._reconcile_task = asyncio.create_task(
                self._reconcile_jobs(), name="openclaw-recover"
            )

    async def close(self) -> None:
        self._closed = True
        for task in (self._watchdog_task, self._reconcile_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._watchdog_task = None
        self._reconcile_task = None
        conn = self._conn
        self._conn = None
        if conn is not None:
            conn.stopped.set()
            conn.ready.clear()
            if conn.reader_task is not None:
                conn.reader_task.cancel()
                try:
                    await conn.reader_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._fail_all_pending(conn, AdapterConnectionError(
                "OpenClaw adapter closed", adapter_name=self.name))
            await self._teardown_ws(conn)

    # -- connection ----------------------------------------------------------

    async def _ensure_ready(self) -> _Connection:
        """Lazily create the (single) connection and wait until hello-ok."""
        if self._closed:
            # close() 之后残留的后台任务（如收尾中的 job complete）不得
            # 再建新连接（会泄漏一条无人关闭的 ws）。
            raise AdapterConnectionError("OpenClaw adapter closed", adapter_name=self.name)
        conn = self._conn
        if conn is None or conn.stopped.is_set():
            conn = _Connection()
            self._conn = conn
            conn.reader_task = asyncio.create_task(
                self._reader_loop(conn), name="openclaw-ws-reader"
            )
        await self._wait_ready(conn)
        return conn

    async def _wait_ready(self, conn: _Connection) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.request_timeout_s
        while True:
            if conn.ready.is_set():
                return
            if conn.stopped.is_set():
                raise AdapterConnectionError(
                    "OpenClaw connection stopped", adapter_name=self.name
                )
            if conn.connect_error is not None:
                # 最近一次连接尝试失败 → 快速失败。**不消费**错误：reader 在
                # 连接成功时统一清除（成功路径 connect_error=None 后 ready.set），
                # 因此断连期间每个 waiter 都在首个轮询周期内失败，而不是各自
                # 等满 request_timeout_s（2026-10-14 实测：空 token 拒连场景
                # nudge 白等 30s）。
                raise conn.connect_error
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                # 短轮询而非一次等满：不阻塞感知 connect_error/stopped 的上限
                # = _READY_POLL_S（成功路径 ready.set 立即唤醒，无额外延迟）。
                await asyncio.wait_for(
                    conn.ready.wait(), timeout=min(remaining, _READY_POLL_S)
                )
            except TimeoutError:
                continue
        raise AdapterTimeoutError(
            f"OpenClaw connection not ready after {self._cfg.request_timeout_s}s",
            adapter_name=self.name,
        )

    async def _connect_ws(self, conn: _Connection) -> None:
        """Handshake: WS → connect.challenge → connect req → hello-ok (P0 实测顺序)."""
        await self._teardown_ws(conn)
        logger.info("connecting to openclaw gateway", extra={"url": self._cfg.ws_url})
        try:
            conn.ws = await asyncio.wait_for(
                websockets.connect(
                    self._cfg.ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=2**24,
                ),
                timeout=self._cfg.connect_timeout_s,
            )
        except TimeoutError:
            raise AdapterTimeoutError(
                f"OpenClaw WS connect timed out after {self._cfg.connect_timeout_s}s",
                adapter_name=self.name,
            )
        except OSError as exc:
            raise AdapterConnectionError(
                f"OpenClaw WS connect failed: {exc}", adapter_name=self.name
            )

        # 1) 服务端 challenge（连接后立即下发；单帧读带超时防"只挂不答"）
        nonce = ""
        deadline = asyncio.get_running_loop().time() + self._cfg.connect_timeout_s
        while not nonce:
            try:
                raw = await asyncio.wait_for(
                    self._recv_raw(conn), timeout=self._cfg.connect_timeout_s,
                )
            except TimeoutError:
                raise AdapterTimeoutError(
                    "OpenClaw handshake timed out (no connect.challenge)",
                    adapter_name=self.name,
                ) from None
            msg = json.loads(raw)
            if msg.get("type") == "event" and msg.get("event") == "connect.challenge":
                nonce = str((msg.get("payload") or {}).get("nonce") or "")
                break
            if asyncio.get_running_loop().time() > deadline:
                raise AdapterConnectionError(
                    "OpenClaw connect.challenge not received", adapter_name=self.name
                )

        # 2) connect 请求（backend 免配对路径；P0 实测）
        conn.req_id += 1
        rid = f"conn-{conn.req_id}"
        params: dict[str, Any] = {
            "minProtocol": _PROTOCOL,
            "maxProtocol": _PROTOCOL,
            "client": {
                "id": _CLIENT_ID,
                "version": _CLIENT_VERSION,
                "platform": sys.platform,
                "mode": _CLIENT_MODE,
            },
            "role": "operator",
            "scopes": self._connect_scopes(),
            "caps": [],
            "auth": {"token": self._cfg.token},
            "userAgent": "livachat-agent-relay/0.1",
        }
        await conn.ws.send(json.dumps({
            "type": "req", "id": rid, "method": "connect", "params": params,
        }))

        # 3) 等 hello-ok（此阶段 reader 尚未开始，直接读帧；单帧读带超时）
        while True:
            try:
                raw = await asyncio.wait_for(
                    self._recv_raw(conn), timeout=self._cfg.connect_timeout_s,
                )
            except TimeoutError:
                raise AdapterTimeoutError(
                    "OpenClaw handshake timed out (no hello-ok)",
                    adapter_name=self.name,
                ) from None
            msg = json.loads(raw)
            if msg.get("type") == "res" and str(msg.get("id")) == rid:
                if not msg.get("ok"):
                    err = msg.get("error") or {}
                    raise AdapterConnectionError(
                        "OpenClaw connect rejected: "
                        f"{err.get('code')}: {err.get('message')}",
                        adapter_name=self.name,
                    )
                payload = msg.get("payload") or {}
                policy = payload.get("policy") or {}
                tick = policy.get("tickIntervalMs")
                if isinstance(tick, int) and tick > 0:
                    conn.tick_interval_ms = tick
                logger.info(
                    "openclaw gateway connected",
                    extra={
                        "protocol": payload.get("protocol"),
                        "server": (payload.get("server") or {}).get("version"),
                        "tick_interval_ms": conn.tick_interval_ms,
                    },
                )
                return
            # 忽略 challenge 之后的其它预 hello 帧

    def _connect_scopes(self) -> list[str]:
        scopes = ["operator.read", "operator.write"]
        if self._cfg.approvals_autorespond:
            scopes.append("operator.approvals")
        if self._cfg.question_autorespond:
            scopes.append("operator.questions")
        return scopes

    async def _teardown_ws(self, conn: _Connection) -> None:
        if conn.ws is not None:
            try:
                await conn.ws.close()
            except Exception:
                pass
            conn.ws = None

    async def _recv_raw(self, conn: _Connection) -> str:
        if conn.ws is None:
            raise AdapterConnectionError("WebSocket is not connected", adapter_name=self.name)
        raw = await conn.ws.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return raw

    def _backoff_delay(self, conn: _Connection) -> float:
        """Exponential backoff + jitter（对齐 hermes D11）。"""
        base = self._cfg.reconnect_backoff_initial_s * (2 ** conn.reconnect_attempts)
        jitter = base * self._cfg.reconnect_jitter
        return min(
            self._cfg.reconnect_backoff_max_s,
            max(0.0, base + random.uniform(-jitter, jitter)),
        )

    def _effective_silence_s(self, conn: _Connection) -> float:
        """入站静默判死阈值：2×tickInterval + 余量，且不低于配置值（D10）。"""
        return max(self._cfg.silence_timeout_s, 2 * conn.tick_interval_ms / 1000.0 + 15.0)

    # -- reader --------------------------------------------------------------

    async def _reader_loop(self, conn: _Connection) -> None:
        while not self._closed:
            try:
                await self._connect_ws(conn)
                conn.connect_error = None
                was_reconnect = conn.reconnect_attempts > 0
                conn.reconnect_attempts = 0
                conn.seq_last = None
                conn.subscribed.clear()
                conn.last_event_at = time.time()
                conn.ready.set()
                if was_reconnect:
                    inc_gateway_reconnect()
                    asyncio.create_task(
                        self._reconcile_jobs(), name="openclaw-reconnect-reconcile"
                    )
                conn.silence_task = asyncio.create_task(
                    self._silence_loop(conn), name="openclaw-ws-silence"
                )
                try:
                    while True:
                        raw = await self._recv_raw(conn)
                        conn.last_event_at = time.time()
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            # 单个畸形帧不得拖垮承载全部 device 的连接（P2）：
                            # log 截断预览 + 跳过该帧，连接继续。
                            logger.warning(
                                "openclaw listener: malformed frame skipped",
                                extra={"preview": raw[:120]},
                            )
                            continue
                        self._handle_message(conn, msg)
                finally:
                    if conn.silence_task is not None:
                        conn.silence_task.cancel()
                        try:
                            await conn.silence_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        conn.silence_task = None
            except asyncio.CancelledError:
                return
            except _ReconnectRequested:
                conn.ready.clear()
                self._fail_all_pending(conn, AdapterConnectionError(
                    "OpenClaw connection recycled", adapter_name=self.name))
                await self._teardown_ws(conn)
                conn.reconnect_attempts += 1
                await asyncio.sleep(self._backoff_delay(conn))
            except (ConnectionClosed, OSError) as exc:
                logger.warning(
                    "openclaw ws connection lost, reconnecting",
                    extra={"error": str(exc)[:200]},
                )
                conn.ready.clear()
                self._fail_all_pending(conn, exc)
                await self._teardown_ws(conn)
                conn.reconnect_attempts += 1
                await asyncio.sleep(self._backoff_delay(conn))
            except Exception as exc:
                if isinstance(exc, (AdapterConnectionError, AdapterTimeoutError)):
                    conn.ready.clear()
                    conn.connect_error = exc
                    self._fail_all_pending(conn, exc)
                    await self._teardown_ws(conn)
                    conn.reconnect_attempts += 1
                    await asyncio.sleep(self._backoff_delay(conn))
                else:
                    # 非预期的 reader 异常（handler bug / 协议外输入）：与
                    # _ReconnectRequested 对齐——完整拆连接（清 ready、弹在飞
                    # pending、设 connect_error 供 waiters 快速失败、退避重连），
                    # 而非 sleep 1s 后在 ready 未清/pending 未弹的状态下裸重连。
                    logger.exception("openclaw reader error, recycling connection")
                    error = AdapterConnectionError(
                        f"OpenClaw reader error: {exc}", adapter_name=self.name
                    )
                    conn.ready.clear()
                    conn.connect_error = error
                    self._fail_all_pending(conn, error)
                    await self._teardown_ws(conn)
                    conn.reconnect_attempts += 1
                    await asyncio.sleep(self._backoff_delay(conn))

    async def _silence_loop(self, conn: _Connection) -> None:
        """半死判死（D10）：入站静默超阈值 → 关 ws 触发既有重连路径。"""
        interval = max(5.0, min(15.0, self._effective_silence_s(conn) / 3))
        while True:
            await asyncio.sleep(interval)
            silence = time.time() - conn.last_event_at
            if silence > self._effective_silence_s(conn):
                logger.warning(
                    "openclaw ws silence exceeded threshold, forcing reconnect",
                    extra={"silence_s": round(silence, 1)},
                )
                if conn.ws is not None:
                    try:
                        await conn.ws.close()
                    except Exception:
                        pass
                return

    def _handle_message(self, conn: _Connection, msg: Any) -> None:
        if not isinstance(msg, dict):
            # 合法 JSON 但非对象（列表/字符串/数字）：跳过，不进 handler
            logger.warning("openclaw listener: non-object frame skipped")
            return
        mtype = msg.get("type")
        if mtype == "res":
            fut = conn.pending.pop(str(msg.get("id")), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        if mtype != "event":
            return
        seq = msg.get("seq")
        if isinstance(seq, int):
            if conn.seq_last is not None and seq > conn.seq_last + 1:
                inc_gateway_seq_gap()
                logger.warning(
                    "openclaw event seq gap, forcing reconnect",
                    extra={"expected": conn.seq_last + 1, "received": seq},
                )
                # 关闭连接（recv 随即抛 ConnectionClosed → 既有重连路径）
                if conn.ws is not None:
                    asyncio.create_task(conn.ws.close())
                return
            conn.seq_last = seq
        event = msg.get("event", "")
        payload = msg.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if event == "chat":
            self._handle_chat_event(conn, payload)
        elif event in ("exec.approval.requested", "question.requested"):
            task = asyncio.create_task(self._handle_interaction(conn, event, payload))
            task.add_done_callback(_log_task_exception)
        # 其它事件（presence/tick/health/session.*…）: 仅刷新活性（reader 已做）

    # -- rpc -----------------------------------------------------------------

    async def _rpc(
        self,
        conn: _Connection,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a request frame and await the response payload.

        Error responses raise AdapterConnectionError (message embeds
        code/message); timeouts raise AdapterTimeoutError（对齐 hermes 语义）。
        """
        await self._wait_ready(conn)
        if conn.ws is None:
            raise AdapterConnectionError("WebSocket is not connected", adapter_name=self.name)
        conn.req_id += 1
        rid = f"r{conn.req_id}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        conn.pending[rid] = future
        frame: dict[str, Any] = {"type": "req", "id": rid, "method": method}
        if params is not None:
            frame["params"] = params
        try:
            try:
                await conn.ws.send(json.dumps(frame))
            except (ConnectionClosed, OSError) as exc:
                # 发送中途断连：统一收敛为 AdapterConnectionError——调用方
                # （job 派发等）按连接错误收敛，而不是漏成裸 ConnectionClosed。
                raise AdapterConnectionError(
                    f"OpenClaw request ({method}) send failed: {exc}",
                    adapter_name=self.name,
                ) from exc
            res = await asyncio.wait_for(
                future,
                timeout=self._cfg.request_timeout_s if timeout is None else timeout,
            )
        except TimeoutError:
            raise AdapterTimeoutError(
                f"OpenClaw request ({method}) timed out", adapter_name=self.name
            ) from None
        finally:
            conn.pending.pop(rid, None)
        if not res.get("ok"):
            err = res.get("error") or {}
            raise AdapterConnectionError(
                f"OpenClaw request ({method}) failed: "
                f"{err.get('code', '?')}: {err.get('message', '')}",
                adapter_name=self.name,
            )
        return res.get("payload") or {}

    def _fail_all_pending(self, conn: _Connection, exc: Exception) -> None:
        for fut in list(conn.pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        conn.pending.clear()
        for fut in list(conn.run_waits.values()):
            if not fut.done():
                fut.set_exception(exc)
        conn.run_waits.clear()
        conn.run_deltas.clear()
        # job_runs / job_sessions 跨重连保留（终态事件仍需路由到 job）

    # -- sessions & subscriptions --------------------------------------------

    def _with_agent(self, params: dict[str, Any]) -> dict[str, Any]:
        """多 agent 纪律（设计 §4.2）：会话类调用统一显式携带 agentId。

        裸键 + agentId 才能路由到目标 agent；规范全键（agent:<id>:<key>）下
        agentId 冗余但无害（须与键内 id 一致——本函数取的即同一配置值）。
        对账/派发/订阅/取消全路径共用，防某一路径漏带导致静默落到默认 agent。
        """
        if self._cfg.agent_id:
            params.setdefault("agentId", self._cfg.agent_id)
        return params

    def _session_key(self, kind_prefix: str, ident: str) -> str:
        return f"{kind_prefix}{ident}"

    async def _ensure_session(self, conn: _Connection, cache_key: tuple[str, str], raw_key: str) -> str:
        """Create-or-adopt the deterministic session and subscribe it."""
        cached = self._session_keys.get(cache_key)
        if cached is not None:
            await self._ensure_subscribed(conn, cached)
            return cached
        params = self._with_agent({"key": raw_key})
        payload = await self._rpc(conn, "sessions.create", params)
        canonical = str(payload.get("key") or raw_key)
        self._session_keys[cache_key] = canonical
        await self._ensure_subscribed(conn, canonical)
        return canonical

    async def _ensure_subscribed(self, conn: _Connection, key: str) -> str:
        """确保订阅；返回订阅集内记录的**规范键**（服务端 ack 的 key 为准）。

        订阅集与 job_sessions 只存规范全键（agent:<id>:<key>）——防裸键与规范键
        作为两个字符串进集合、同一会话被重复订阅（设计 §4.2 对齐基准）。
        """
        if key in conn.subscribed:
            return key
        async with conn.sub_lock:
            if key in conn.subscribed:
                return key
            payload = await self._rpc(
                conn, "sessions.messages.subscribe", self._with_agent({"key": key})
            )
            canonical = str(payload.get("key") or key)
            conn.subscribed.add(canonical)
            return canonical

    async def _unsubscribe(self, conn: _Connection, key: str) -> None:
        conn.subscribed.discard(key)
        try:
            await self._rpc(
                conn, "sessions.messages.unsubscribe", self._with_agent({"key": key})
            )
        except Exception:
            logger.debug("openclaw unsubscribe failed (best-effort)", extra={"key": key})

    async def _abort_run(self, conn: _Connection, run_id: str) -> None:
        """尽力取消 run（不抛出；失败仅告警）。"""
        if not run_id:
            return
        try:
            payload = await self._rpc(conn, "sessions.abort", {"runId": run_id})
            logger.info(
                "openclaw run aborted",
                extra={"run_id": run_id, "status": payload.get("status")},
            )
        except Exception as exc:
            logger.warning(
                "openclaw abort failed (best-effort)",
                extra={"run_id": run_id, "error": str(exc)[:200]},
            )

    # -- turns (nudge / ask) --------------------------------------------------

    def _register_run_wait(self, conn: _Connection, run_id: str) -> asyncio.Future[str | None]:
        """runId 由 relay 掌控（=幂等键）→ 发送前注册，ack 丢失不破坏关联。"""
        fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        conn.run_waits[run_id] = fut
        return fut

    async def _send_turn(
        self,
        conn: _Connection,
        session_key: str,
        message: str,
        *,
        run_id: str,
        queue_mode: str | None = None,
    ) -> asyncio.Future[str | None]:
        future = self._register_run_wait(conn, run_id)
        params: dict[str, Any] = {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": run_id,
        }
        if queue_mode:
            params["queueMode"] = queue_mode
        self._with_agent(params)
        try:
            payload = await self._rpc(conn, "chat.send", params)
        except Exception:
            conn.run_waits.pop(run_id, None)
            raise
        ack_run = str(payload.get("runId") or "")
        if ack_run and ack_run != run_id:
            # 防御：理论不会发生（runId == idempotencyKey，P0 实测），仍对齐
            popped = conn.run_waits.pop(run_id, None)
            if popped is not None:
                conn.run_waits[ack_run] = popped
        return future

    def _handle_chat_event(self, conn: _Connection, payload: dict[str, Any]) -> None:
        run_id = str(payload.get("runId") or "")
        state = payload.get("state")
        if state == "delta":
            text = payload.get("deltaText")
            if run_id and isinstance(text, str):
                if payload.get("replace"):
                    conn.run_deltas[run_id] = text
                else:
                    conn.run_deltas[run_id] = conn.run_deltas.get(run_id, "") + text
            return
        if state not in ("final", "error", "aborted"):
            return
        text: str | None = None
        if state == "final":
            text = self._final_text(conn, run_id, payload)
        conn.run_deltas.pop(run_id, None)
        fut = conn.run_waits.pop(run_id, None)
        if fut is not None and not fut.done():
            fut.set_result(text if state == "final" else None)
        job_key = conn.job_runs.pop(run_id, None)
        if job_key is not None:
            task = asyncio.create_task(
                self._complete_job(conn, job_key, run_id, state, text),
                name=f"openclaw-job-complete-{job_key}",
            )
            task.add_done_callback(_log_task_exception)

    def _final_text(self, conn: _Connection, run_id: str, payload: dict[str, Any]) -> str:
        """终稿文本：final 的 message 快照优先（P0 实测形状），deltas 兜底。"""
        text = _extract_message_text(payload.get("message"))
        if text:
            return text
        return conn.run_deltas.get(run_id, "")

    async def _wait_run(
        self,
        conn: _Connection,
        future: asyncio.Future[str | None],
        *,
        timeout_s: float,
    ) -> str | None:
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError:
            raise AdapterTimeoutError(
                "OpenClaw run timed out", adapter_name=self.name
            ) from None

    # -- proactive (nudge) ----------------------------------------------------

    async def nudge(
        self,
        update: ChatlogUpdate,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Send the self-contained NUDGE and parse the contract reply（D7）."""
        if not self.proactive_enabled:
            return
        if False:  # pragma: no cover — keep async generator
            yield
        conn = await self._ensure_ready()
        lock = self._device_locks.setdefault(context.device_id, asyncio.Lock())
        async with lock:
            try:
                session = await self._ensure_session(
                    conn,
                    ("dev", context.device_id),
                    self._session_key(_LISTENING_PREFIX, context.device_id),
                )
            except (AdapterConnectionError, AdapterTimeoutError):
                inc_nudge(self.name, "error")
                logger.warning("openclaw nudge: session ensure failed", exc_info=True)
                return
            run_id = f"livachat:nudge:{context.device_id}:{uuid.uuid4().hex}"
            message = self._render_nudge(
                update, context.timezone, language=context.language,
            )
            try:
                future = await self._send_turn(conn, session, message, run_id=run_id)
            except (AdapterConnectionError, AdapterTimeoutError):
                inc_nudge(self.name, "error")
                logger.warning("openclaw nudge: chat.send failed", exc_info=True)
                return
            try:
                reply = await self._wait_run(conn, future, timeout_s=self._cfg.nudge_timeout_s)
            except AdapterTimeoutError:
                inc_nudge(self.name, "timeout")
                # wait_for 已取消 future；清掉登记防泄漏（终态迟到时按已 done 跳过）
                conn.run_waits.pop(run_id, None)
                logger.warning(
                    "openclaw nudge turn timed out, aborting run",
                    extra={"device_id": context.device_id, "run_id": run_id},
                )
                await self._abort_run(conn, run_id)
                return
            except AdapterConnectionError:
                inc_nudge(self.name, "error")
                return
            if reply is None:
                # run 以 error/aborted 收场
                inc_nudge(self.name, "error")
                return
            contract = parse_contract(reply)
            action = contract.get("action") if isinstance(contract, dict) else None
            if action == "message":
                msg_text = (contract.get("text") or "").strip()
                if msg_text:
                    inc_nudge(self.name, "message")
                    yield AgentResponseChunk(
                        text=msg_text, is_final=True,
                        metadata={"agent_name": self.name},
                    )
                else:
                    inc_nudge(self.name, "noop")
            elif action == "job_request":
                inc_nudge(self.name, "job_request")
                async for chunk in self._dispatch_job(
                    conn, context, contract, update.path,
                ):
                    yield chunk
            elif action == "noop":
                inc_nudge(self.name, "noop")
            else:
                inc_nudge(self.name, "parse_error")
                reply_preview = (reply or "").replace("\n", "\\n")[:500]
                logger.warning(
                    f"openclaw contract parse failed, treated as noop; "
                    f"device={context.device_id} reply={reply_preview}",
                )

    # -- job lifecycle (B 方案) -----------------------------------------------

    async def _dispatch_job(
        self,
        conn: _Connection,
        context: AgentContext,
        contract: dict[str, Any],
        chatlog_path: str = "",
    ) -> AsyncIterator[AgentResponseChunk]:
        """Two-stage dispatch: validate caps → JobStore → create session → send."""
        spec = contract.get("spec")
        summary = (contract.get("summary") or "").strip()
        if not isinstance(spec, dict) or not str(spec.get("task") or "").strip():
            logger.warning("openclaw job_request without valid spec, treated as noop")
            return
        if False:  # pragma: no cover — keep async generator
            yield
        if self.job_store is None:
            logger.warning("openclaw job_request received but no job store configured")
            return

        outstanding = await self.job_store.outstanding_jobs_for_device(context.device_id)
        if len(outstanding) >= self._cfg.max_concurrent_jobs_per_device:
            inc_job_event(self.name, "rejected")
            logger.info(
                "openclaw job rejected: per-device cap reached",
                extra={"device_id": context.device_id},
            )
            return
        if await self.job_store.in_flight_count() >= self._cfg.max_in_flight_jobs:
            inc_job_event(self.name, "rejected")
            logger.info("openclaw job rejected: global in-flight cap reached")
            return

        job = await self.job_store.create(
            session_id=context.session_id,
            device_id=context.device_id,
            summary=summary,
            agent_name=self.name,
        )
        inc_job_event(self.name, "requested")

        canonical = ""
        try:
            # 1) 自建 job 会话（不传 idempotencyKey——P0-Q1；同 key 重创建 = adopt）
            params = self._with_agent({
                "key": self._session_key(_JOB_PREFIX, job.job_key),
            })
            if self._cfg.job_parent_link:
                parent = self._session_keys.get(("dev", context.device_id))
                if parent:
                    params["parentSessionKey"] = parent
                    params["spawnDepth"] = 1
            payload = await self._rpc(conn, "sessions.create", params)
            canonical = str(payload.get("key") or params["key"])
        except (AdapterConnectionError, AdapterTimeoutError) as exc:
            await self.job_store.mark_failed(job.job_key, "create_error")
            inc_job_event(self.name, "create_error")
            logger.warning(
                "openclaw job session create failed",
                extra={"job_key": job.job_key, "error": str(exc)[:200]},
            )
            return

        try:
            # 2) 先订阅后发送（杜绝漏帧）
            await self._ensure_subscribed(conn, canonical)
        except (AdapterConnectionError, AdapterTimeoutError) as exc:
            await self.job_store.mark_failed(job.job_key, "subscribe_error")
            inc_job_event(self.name, "subscribe_error")
            logger.warning(
                "openclaw job subscribe failed",
                extra={"job_key": job.job_key, "error": str(exc)[:200]},
            )
            return

        task_desc = self._render_background_task(
            spec["task"], context.session_id, chatlog_path,
            timezone=context.timezone, language=context.language,
        )
        run_id = f"livachat:job:{job.job_key}"
        # **发送前登记路由**：reader 在 ack 后可能立刻处理同一批帧里的
        # delta/final（事件循环尚未恢复到本协程），登记晚了会丢终态。
        conn.job_runs[run_id] = job.job_key
        conn.job_sessions[job.job_key] = canonical
        try:
            await self._send_turn(conn, canonical, task_desc, run_id=run_id)
        except (AdapterConnectionError, AdapterTimeoutError) as exc:
            conn.job_runs.pop(run_id, None)
            conn.job_sessions.pop(job.job_key, None)
            await self.job_store.mark_failed(job.job_key, "rpc_error")
            inc_job_event(self.name, "rpc_error")
            logger.warning(
                "openclaw job chat.send failed",
                extra={"job_key": job.job_key, "error": str(exc)[:200]},
            )
            await self._unsubscribe(conn, canonical)
            return

        if not await self.job_store.mark_running(job.job_key, run_id):
            # 已被 cancel/watchdog 收敛：先尽力 abort 已提交到 gateway 的 run，
            # 再清理路由/订阅——否则该 run 会在无人订阅的情况下继续跑完
            await self._abort_run(conn, run_id)
            conn.job_runs.pop(run_id, None)
            conn.job_sessions.pop(job.job_key, None)
            await self._unsubscribe(conn, canonical)
            return
        inc_job_event(self.name, "started")
        if self._cfg.notify_job_started and summary:
            yield AgentResponseChunk(
                text=summary, is_final=True,
                metadata={"agent_name": self.name},
            )

    async def _complete_job(
        self,
        conn: _Connection,
        job_key: str,
        run_id: str,
        state: str,
        text: str | None,
    ) -> None:
        """Route a run terminal to the JobStore + deliverer（D5/§4.4.2）."""
        if self.job_store is None:
            return
        job = await self.job_store.get(job_key)
        if job is not None and job.status == STATUS_REQUESTED:
            # 派发竞态容忍：终态事件可能先于 mark_running 落库（reader 处理
            # ack 后的同批帧时派发协程尚未恢复）——有界等待其转为 running。
            for _ in range(20):
                await asyncio.sleep(0.05)
                job = await self.job_store.get(job_key)
                if job is None or job.status != STATUS_REQUESTED:
                    break
        if job is None or job.status != STATUS_RUNNING:
            logger.info(
                "openclaw terminal for non-running job, dropping",
                extra={"job_key": job_key, "state": state},
            )
            await self._cleanup_job_session(conn, job_key)
            return
        try:
            if state == "final":
                stripped = (text or "").strip()
                if not stripped:
                    await self.job_store.mark_done(job_key)
                    await self.job_store.mark_delivered(job_key)
                    inc_job_event(self.name, "completed")
                elif stripped.upper() == _NOOP_OUTPUT:
                    # D14：NOOP 不入 result_text，视为已消费
                    await self.job_store.mark_done(job_key)
                    await self.job_store.mark_delivered(job_key)
                    inc_job_event(self.name, "completed")
                    inc_job_event(self.name, "noop_output")
                else:
                    await self.job_store.mark_done(job_key, text)
                    inc_job_event(self.name, "completed")
                    await self._deliver(job_key)
            elif state == "error":
                await self.job_store.mark_failed(job_key, "run_error")
                inc_job_event(self.name, "run_error")
            else:  # aborted
                await self.job_store.mark_failed(job_key, "aborted")
                inc_job_event(self.name, "aborted")
        finally:
            await self._cleanup_job_session(conn, job_key)

    async def _deliver(self, job_key: str) -> None:
        """Deliver via the shared JobResultDeliverer（hermes 同款 getattr 注入）。"""
        if self.job_store is None:
            return
        job = await self.job_store.get(job_key)
        if job is None:
            return
        deliverer = getattr(self, "deliverer", None)
        if deliverer is not None:
            try:
                await deliverer.deliver(job)
            except Exception:
                logger.warning(
                    "openclaw job delivery failed",
                    extra={"job_key": job_key}, exc_info=True,
                )
            return
        # 无 deliverer（隔离单测）：走 push callback 兜底
        try:
            cb = getattr(self, "_push_callback", None)
            if cb is not None:
                await cb(
                    job.device_id, job.session_id,
                    AgentResponseChunk(text=job.result_text, is_final=True,
                                       metadata={"agent_name": self.name}),
                )
        except Exception:
            logger.warning("openclaw job push failed", extra={"job_key": job_key}, exc_info=True)

    async def _cleanup_job_session(self, conn: _Connection, job_key: str) -> None:
        canonical = conn.job_sessions.pop(job_key, None)
        if canonical:
            await self._unsubscribe(conn, canonical)

    async def cancel_job(self, job_key: str, reason: str = "cancelled") -> bool:
        """Cancel an active OpenClaw job (adapter capability; hermes 同语义)。

        Returns True when the job was active and cancellation converged or was
        initiated; False for terminal/unknown jobs or failed store writes.
        """
        if self.job_store is None:
            return False
        try:
            job = await self.job_store.get(job_key)
        except Exception:
            logger.warning("openclaw cancel_job: job store read failed", exc_info=True)
            return False
        if job is None or job.status not in (STATUS_REQUESTED, STATUS_RUNNING):
            return False

        # requested 尚未 mark_running 时 hermes_task_id 为空，但 runId 确定性可构造
        run_id = job.hermes_task_id or f"livachat:job:{job_key}"
        conn: _Connection | None = None
        try:
            conn = await self._ensure_ready()
            # 已有 dispatch/reconcile 写入的规范键时直接复用，避免裸键重复订阅
            canonical = conn.job_sessions.get(job_key)
            if canonical:
                canonical = await self._ensure_subscribed(conn, canonical)
            else:
                canonical = await self._ensure_subscribed(
                    conn, self._session_key(_JOB_PREFIX, job_key)
                )
            conn.job_sessions[job_key] = canonical
            conn.job_runs[run_id] = job_key   # aborted 终态必须能路由回 job
            # 统一的「用户取消已发出」标签（与 Hermes 对齐）；终态 aborted/
            # cancelled 仍表示收敛原因
            inc_job_event(self.name, "cancel_sent")
            await self._abort_run(conn, run_id)
        except Exception:
            # 连接/订阅失败：能连上就仍尽力 abort 远端 run；随后立即收敛、不等宽限
            logger.warning("openclaw cancel_job: abort failed", exc_info=True)
            if conn is not None:
                inc_job_event(self.name, "cancel_sent")
                await self._abort_run(conn, run_id)
            record = await self._mark_failed_safe(job_key, reason)
            if conn is not None:
                if conn.job_runs.get(run_id) == job_key:
                    conn.job_runs.pop(run_id, None)
                try:
                    await self._cleanup_job_session(conn, job_key)
                except Exception:
                    logger.warning(
                        "openclaw cancel_job: cleanup failed", exc_info=True
                    )
            inc_job_event(self.name, reason)
            return record is not None

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.job_cancel_grace_s
        while loop.time() < deadline:
            await asyncio.sleep(0.2)
            try:
                current = await self.job_store.get(job_key)
            except Exception:
                break
            if current is None or current.status not in (STATUS_REQUESTED, STATUS_RUNNING):
                # aborted 事件已由 _complete_job 收敛；若终态来自 watchdog
                # （无终态事件），顺手清路由与订阅，避免会话订阅泄漏
                if conn.job_runs.get(run_id) == job_key:
                    conn.job_runs.pop(run_id, None)
                try:
                    await self._cleanup_job_session(conn, job_key)
                except Exception:
                    logger.warning(
                        "openclaw cancel_job: cleanup failed", exc_info=True
                    )
                return True

        record = await self._mark_failed_safe(job_key, reason)
        if conn.job_runs.get(run_id) == job_key:
            conn.job_runs.pop(run_id, None)
        try:
            await self._cleanup_job_session(conn, job_key)
        except Exception:
            logger.warning("openclaw cancel_job: cleanup failed", exc_info=True)
        inc_job_event(self.name, reason)
        return record is not None

    async def _mark_failed_safe(self, job_key: str, reason: str):
        """mark_failed that never raises (cancel_job 收敛兜底)."""
        try:
            return await self.job_store.mark_failed(job_key, reason)
        except Exception:
            logger.warning("openclaw cancel_job: mark_failed failed", exc_info=True)
            return None

    # -- reactive (ask) -------------------------------------------------------

    async def ask(
        self,
        text: str,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponseChunk]:
        """Reactive turn on the dedicated per-device reactive session."""
        if not self.reactive_enabled:
            return
        if False:  # pragma: no cover — keep async generator
            yield
        conn = await self._ensure_ready()
        lock = self._device_locks.setdefault(context.device_id, asyncio.Lock())
        async with lock:
            session = await self._ensure_session(
                conn,
                ("req", context.device_id),
                self._session_key(_REACTIVE_PREFIX, context.device_id),
            )
            run_id = f"livachat:ask:{context.device_id}:{uuid.uuid4().hex}"
            future = await self._send_turn(
                conn, session,
                text.rstrip() + "\n\n" + language_hint(context.language, verbose=False),
                run_id=run_id,
            )
            try:
                reply = await self._wait_run(conn, future, timeout_s=self._cfg.ask_timeout_s)
            except AdapterTimeoutError:
                conn.run_waits.pop(run_id, None)   # wait_for 已取消未来；防登记泄漏
                await self._abort_run(conn, run_id)
                raise
            if reply:
                yield AgentResponseChunk(text=reply, is_final=True)

    # -- watchdog & reconcile -------------------------------------------------

    async def _watchdog_loop(self) -> None:
        """Converge stale openclaw jobs（D6）：timeout 前尽力 abort（hermes.py 同构）
        ——hermes 版扫描全量属存量行为，本版按 agent_name 过滤。"""
        while not self._closed:
            await asyncio.sleep(self._cfg.watchdog_interval_s)
            if self.job_store is None:
                continue
            try:
                active = [
                    j for j in await self.job_store.list_active()
                    if j.agent_name == self.name
                ]
                now = time.time()
                for job in active:
                    age = now - job.updated_at
                    if job.status == STATUS_RUNNING and age > self._cfg.background_timeout_s:
                        await self._abort_run_best_effort(job.hermes_task_id)
                        await self.job_store.mark_failed(job.job_key, "timeout")
                        inc_job_event(self.name, "timeout")
                        logger.warning(
                            "openclaw job converged by watchdog (timeout)",
                            extra={"job_key": job.job_key},
                        )
                    elif job.status == STATUS_REQUESTED and age > self._cfg.job_requested_timeout_s:
                        await self.job_store.mark_failed(job.job_key, "orphaned")
                        inc_job_event(self.name, "orphaned")
                        logger.info(
                            "openclaw job converged by watchdog (orphaned)",
                            extra={"job_key": job.job_key},
                        )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("openclaw watchdog scan failed", exc_info=True)

    async def _abort_run_best_effort(self, run_id: str) -> None:
        if not run_id:
            return
        try:
            conn = await self._ensure_ready()
            await self._abort_run(conn, run_id)
        except Exception as exc:
            logger.warning(
                "openclaw watchdog abort failed (best-effort)",
                extra={"run_id": run_id, "error": str(exc)[:200]},
            )

    async def _reconcile_jobs(self) -> None:
        """启动/重连对账（D12/§4.4.5）：重订阅 + describe/history 收敛 + 幂等重发。

        重入去重（P2）：已有对账在跑时，本次请求合并为"补跑一轮"（置 pending，
        由在跑循环紧接着再跑一遍）——既不并发同一批 job 的对账，也不丢重连后
        必须的重订阅。
        """
        if self._reconcile_inflight:
            self._reconcile_pending = True
            return
        self._reconcile_inflight = True
        try:
            while True:
                self._reconcile_pending = False
                await self._reconcile_pass()
                if not self._reconcile_pending:
                    break
        finally:
            self._reconcile_inflight = False

    async def _reconcile_pass(self) -> None:
        if self.job_store is None:
            return
        try:
            active = [
                j for j in await self.job_store.list_active()
                if j.agent_name == self.name
            ]
        except Exception:
            logger.warning("openclaw reconcile: list_active failed", exc_info=True)
            return
        if not active:
            return
        logger.info("openclaw reconcile: %d active job(s)", len(active))
        sem = asyncio.Semaphore(max(1, self._cfg.recovery_concurrency))

        async def _bounded(job):
            async with sem:
                await self._reconcile_one(job)

        await asyncio.gather(*(_bounded(j) for j in active))

    async def _reconcile_one(self, job) -> None:
        try:
            conn = await self._ensure_ready()
            # 裸键 + agentId 发起订阅，服务端 ack 返回规范全键后全程以它为对齐
            # 基准（设计 §4.2；防对账路径与派发路径产生两种键变体）
            canonical = await self._ensure_subscribed(
                conn, self._session_key(_JOB_PREFIX, job.job_key)
            )
            conn.job_sessions[job.job_key] = canonical
            if job.status == STATUS_REQUESTED:
                await self._reconcile_requested(conn, job, canonical)
                return
            # running
            run_id = job.hermes_task_id
            if run_id:
                conn.job_runs[run_id] = job.job_key   # 终态事件路由（跨重启）
            await self._reconcile_running(conn, job, canonical)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "openclaw reconcile failed for job",
                extra={"job_key": job.job_key},
                exc_info=True,
            )

    async def _reconcile_requested(self, conn: _Connection, job, canonical: str) -> None:
        """requested = relay 在 create/mark_running 窗口崩溃的残骸。

        幂等重发（P0-Q6：同键重发不产生重复 run）：
        in_flight/started → mark_running 采纳；终态响应 → 走 running 判定。
        重发异常 → 保持 requested，由看门狗 60s 收敛（hermes parity）。
        """
        run_id = f"livachat:job:{job.job_key}"
        task_desc = self._render_background_task(
            job.summary or "（恢复重发：任务说明请结合 chatlog 自行判断）",
            job.session_id,
            chatlog_path="",  # 原 chatlog 路径不可知；任务书提示按 session 过滤
            timezone="",
        )
        # 先登记路由：重发命中的既有 run 若随后收尾，终态事件需能路由到 job
        conn.job_runs[run_id] = job.job_key
        try:
            payload = await self._rpc(conn, "chat.send", self._with_agent({
                "sessionKey": canonical,
                "message": task_desc,
                "idempotencyKey": run_id,
            }))
        except (AdapterConnectionError, AdapterTimeoutError) as exc:
            logger.warning(
                "openclaw reconcile: idempotent resend failed, leaving requested",
                extra={"job_key": job.job_key, "error": str(exc)[:200]},
            )
            return
        status = str(payload.get("status") or "")
        if status in ("in_flight", "started"):
            conn.job_runs[run_id] = job.job_key
            await self.job_store.mark_running(job.job_key, run_id)
            logger.info(
                "openclaw reconcile: requested job adopted via resend",
                extra={"job_key": job.job_key, "status": status},
            )
            return
        # 终态缓存响应（如 status:"ok"）：按 running 判定取回结果
        if not await self.job_store.mark_running(job.job_key, run_id):
            # 已被看门狗收敛（CAS 失败）：放弃采纳
            conn.job_runs.pop(run_id, None)
            return
        job = await self.job_store.get(job.job_key) or job
        conn.job_runs[run_id] = job.job_key
        await self._reconcile_running(conn, job, canonical)

    async def _reconcile_running(self, conn: _Connection, job, canonical: str) -> None:
        """describe 判定（P0-Q3 字段）；settle 重试；不决 → interrupted。"""
        row = None
        for attempt in range(3):
            try:
                payload = await self._rpc(
                    conn, "sessions.describe", self._with_agent({"key": canonical})
                )
            except (AdapterConnectionError, AdapterTimeoutError) as exc:
                logger.warning(
                    "openclaw reconcile: describe failed",
                    extra={"job_key": job.job_key, "error": str(exc)[:200]},
                )
                return
            row = payload.get("session")
            if row is None:
                await asyncio.sleep(1.0)
                continue
            if row.get("hasActiveRun") is True:
                logger.info(
                    "openclaw reconcile: job still running",
                    extra={"job_key": job.job_key},
                )
                return
            last_run = str(row.get("lastRunId") or "")
            if last_run == job.hermes_task_id:
                break
            # run 未反映到会话行（settle 未完成）：短暂重试
            await asyncio.sleep(1.0)
        if row is None:
            await self._reconcile_fail(conn, job, "interrupted", "session row missing")
            return
        if row.get("hasActiveRun") is True:
            return
        if str(row.get("lastRunId") or "") != job.hermes_task_id:
            await self._reconcile_fail(conn, job, "interrupted", "lastRunId mismatch after settle")
            return
        if row.get("lastRunError"):
            await self._reconcile_fail(
                conn, job, "run_error", str(row.get("lastRunError"))[:200],
            )
            return
        text = await self._read_last_assistant(conn, canonical)
        if text is None:
            await self._reconcile_fail(conn, job, "interrupted", "terminal run without transcript")
            return
        stripped = text.strip()
        if not stripped:
            await self.job_store.mark_done(job.job_key)
            await self.job_store.mark_delivered(job.job_key)
            inc_job_event(self.name, "completed")
        elif stripped.upper() == _NOOP_OUTPUT:
            await self.job_store.mark_done(job.job_key)
            await self.job_store.mark_delivered(job.job_key)
            inc_job_event(self.name, "completed")
            inc_job_event(self.name, "noop_output")
        else:
            await self.job_store.mark_done(job.job_key, text)
            inc_job_event(self.name, "completed")
            await self._deliver(job.job_key)
        await self._cleanup_job_session(conn, job.job_key)
        logger.info(
            "openclaw reconcile: completed job recovered from transcript",
            extra={"job_key": job.job_key},
        )

    async def _reconcile_fail(self, conn: _Connection, job, reason: str, detail: str) -> None:
        await self._abort_run_best_effort(job.hermes_task_id)
        await self.job_store.mark_failed(job.job_key, reason)
        inc_job_event(self.name, reason)
        await self._cleanup_job_session(conn, job.job_key)
        logger.warning(
            "openclaw reconcile: job failed fast",
            extra={"job_key": job.job_key, "reason": reason, "detail": detail},
        )

    async def _read_last_assistant(self, conn: _Connection, session_key: str) -> str | None:
        """chat.history 末条 assistant 的文本（无 assistant 行 → None）。"""
        try:
            payload = await self._rpc(
                conn, "chat.history",
                self._with_agent({"sessionKey": session_key, "limit": 20}),
            )
        except (AdapterConnectionError, AdapterTimeoutError) as exc:
            logger.warning(
                "openclaw reconcile: chat.history failed",
                extra={"session_key": session_key, "error": str(exc)[:200]},
            )
            return None
        messages = payload.get("messages") or []
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return _extract_message_text(msg) or ""
        return None

    # -- unattended interaction fallback (§4.5) -------------------------------

    async def _handle_interaction(
        self, conn: _Connection, event: str, payload: dict[str, Any],
    ) -> None:
        """无人值守兜底：仅处理属于 relay 自建会话的审批/提问。

        P0-Q7：本机未触发审批事件（main agent permissionMode=full），事件
        载荷的会话绑定字段未实测——取 payload 的 sessionKey/sessionId 文本
        指纹做归属判断，**无法确认归属时一律跳过**（防误伤用户其它会话）。
        """
        if event == "exec.approval.requested" and self._cfg.approvals_autorespond:
            if not self._belongs_to_relay(payload):
                return
            rid = str(payload.get("id") or payload.get("approvalId") or "")
            if not rid:
                logger.warning("exec.approval.requested without id, skipping")
                return
            try:
                await self._rpc(conn, "exec.approval.resolve", {"id": rid, "decision": "deny"})
                inc_interaction_autorespond("approval")
                logger.info("openclaw auto-denied exec approval", extra={"id": rid})
            except (AdapterConnectionError, AdapterTimeoutError) as exc:
                logger.warning(
                    "openclaw exec.approval.resolve failed", extra={"error": str(exc)[:200]},
                )
        elif event == "question.requested" and self._cfg.question_autorespond:
            if not self._belongs_to_relay(payload):
                return
            rid = str(payload.get("id") or "")
            if not rid:
                logger.warning("question.requested without id, skipping")
                return
            try:
                # 无人值守取消（不编造答案）；参数 schema：{id, cancel:true}
                await self._rpc(conn, "question.resolve", {"id": rid, "cancel": True})
                inc_interaction_autorespond("question")
                logger.info("openclaw auto-cancelled question", extra={"id": rid})
            except (AdapterConnectionError, AdapterTimeoutError) as exc:
                logger.warning(
                    "openclaw question.resolve failed", extra={"error": str(exc)[:200]},
                )

    @staticmethod
    def _belongs_to_relay(payload: dict[str, Any]) -> bool:
        blob = f"{payload.get('sessionKey', '')} {payload.get('sessionId', '')}"
        return any(marker in blob for marker in _RELAY_KEY_MARKERS)

    # -- prompts --------------------------------------------------------------

    def _render_nudge(
        self, update: ChatlogUpdate, timezone: str, language: str = "",
    ) -> str:
        """两段式载荷：契约块（每轮自包含）+ 数据 JSON（design §4.3/D7）。"""
        contract = string.Template(self._monitor_template).safe_substitute(
            agent_name_hint=agent_name_hint(self._cfg.nickname, verbose=True),
            timezone_hint=timezone_hint(timezone, verbose=True),
            webroot_hint=webroot_hint(self.webroot_dir, verbose=True),
        )
        contract = contract.rstrip() + "\n\n" + language_hint(language, verbose=True)
        data = {
            "type": "chatlog_update",
            "chatlog_path": update.path,
            "chat_session_id": update.chat_session_id,
            "new_lines": [
                {"role": line.role, "text": line.text}
                for line in update.new_lines
            ],
            "truncated": update.truncated,
            "outstanding_jobs": update.outstanding_jobs,
            "timezone": timezone,
        }
        return contract.rstrip() + "\n\n" + json.dumps(data, ensure_ascii=False)

    def _render_background_task(
        self, task: str, chat_session_id: str, chatlog_path: str,
        timezone: str = "", language: str | None = None,
    ) -> str:
        """`language=None` = 未知（崩溃恢复重发；不注入语言指令，沿用原文本）。"""
        rendered = string.Template(self._task_template).safe_substitute(
            task=task,
            chat_session_id=chat_session_id,
            chatlog_path=chatlog_path or "(无)",
            timezone_hint=timezone_hint(timezone, verbose=False),
            agent_name_hint=agent_name_hint(self._cfg.nickname, verbose=False),
            webroot_hint=webroot_hint(self.webroot_dir, verbose=False),
        )
        if language is None:
            return rendered
        return rendered.rstrip() + "\n\n" + language_hint(language, verbose=False)

    @staticmethod
    def _load_template(override_path: str, default: Path) -> str:
        if override_path:
            path = Path(override_path)
            if path.exists():
                return path.read_text(encoding="utf-8")
            logger.warning(
                "openclaw prompt path not found, using default",
                extra={"path": override_path},
            )
        return default.read_text(encoding="utf-8")


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    """消费 fire-and-forget 后台任务的异常（避免 unretrieved task 告警）。"""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.warning("openclaw background task raised", exc_info=exc)


def _extract_message_text(message: Any) -> str:
    """从 chat message 快照提取文本（P0 实测形状：content 为块数组）。

    - dict {role, content:[{type:"text", text}, {type:"thinking",...}], ...} →
      拼接全部 text 块；
    - str → 原样返回；其余 → ""。
    """
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)
