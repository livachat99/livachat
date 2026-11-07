"""AgentSessionManager — 1:1 mapping to LivaChat chat sessions.

Manages agent-side session lifecycle, routes events to adapters in
parallel, and publishes responses back via PushPublisher.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from agent_relay.adapters.base import (
    AgentAdapter,
    AgentContext,
    AgentResponseChunk,
    RecordingInfo,
)
from agent_relay.chatlog_watcher import ChatlogWatcher
from agent_relay.config import AgentRelayConfig
from agent_relay.delivery import JobResultDeliverer
from agent_relay.job_store import JobStore
from agent_relay.metrics import (
    inc_adapter_error,
    inc_agent_event,
    inc_agent_reply,
    inc_job_event,
    observe_reply_latency,
)
from agent_relay.models import AgentEvent, PushMessage
from agent_relay.prompt_hints import normalize_language
from agent_relay.publisher import PushPublisher

if TYPE_CHECKING:
    from agent_relay.job_status import JobStatusNotifier

logger = logging.getLogger("livachat.agent_relay.session_manager")

_IDEMPOTENCY_MAX_SIZE = 10_000
_DEFAULT_LANGUAGE = "en"


def _generate_message_id() -> str:
    import uuid

    return f"msg_agent_{uuid.uuid4().hex[:16]}"


@dataclass
class _AgentSession:
    session_id: str
    device_id: str
    language: str = ""
    last_proactive_at: float = 0.0
    timezone: str = ""          # 设备本机时区（会话内沿用；见 docs/agent_relay_prompts.md）


class AgentSessionManager:
    """Manages agent-side session lifecycle, 1:1 with LivaChat chat sessions."""

    def __init__(
        self,
        adapters: dict[str, AgentAdapter],
        publisher: PushPublisher,
        cfg: AgentRelayConfig,
        *,
        recording_lookup=None,
        base_dir: str = "/opt/lvdata",
        job_store: JobStore | None = None,
        deliverer: JobResultDeliverer | None = None,
        job_status_notifier: JobStatusNotifier | None = None,
    ) -> None:
        self._adapters = adapters
        self._publisher = publisher
        self._cfg = cfg
        self._sessions: dict[str, _AgentSession] = {}
        self._processed_ids: OrderedDict[str, None] = OrderedDict()
        self._watchers: dict[str, ChatlogWatcher] = {}
        self._recording_lookup = recording_lookup
        self._base_dir = base_dir
        self._job_store = job_store
        self._deliverer = deliverer
        self._job_status_notifier = job_status_notifier

        for adapter in adapters.values():
            adapter.set_push_callback(partial(self._on_adapter_push, adapter.name))

    # -- event handlers -------------------------------------------------------

    async def dispatch(self, event: AgentEvent) -> None:
        """InProcessEventBus 订阅 handler：kind 分派（原 EventRouter._dispatch，
        §3.2.1）。异常由 bus consumer 兜底（log + 继续），不扩散。"""
        kind = event.kind
        if kind == "session.started":
            await self.on_session_started(event)
        elif kind == "session.ended":
            await self.on_session_ended(event)
        elif kind == "user.utterance":
            await self.on_user_utterance(event)
        elif kind == "recording.ready":
            await self.on_recording_ready(event)
        else:
            logger.warning("unknown agent event kind", extra={"kind": kind})
            inc_agent_event(kind, "error")

    async def on_session_started(self, event: AgentEvent) -> None:
        sid = event.session_id
        language = normalize_language(event.language)
        if sid in self._sessions:
            # 重复 session.started（同 session_id 重发/重连）：
            # 用最新 App 语言更新会话与已启动 watcher（D10）；有 watcher 时
            # 下一次 nudge 的 ensure_session 会据此触发 Hermes mission restate。
            # 不重跑首报逻辑（补投 sweep / job_status 首报 / adapter 生命周期）。
            sess = self._sessions[sid]
            sess.language = language
            watcher = self._watchers.get(sid)
            if watcher is not None:
                watcher.update_context(
                    AgentContext(
                        session_id=sid,
                        device_id=event.device_id,
                        language=language,
                        timezone=sess.timezone,
                    )
                )
            logger.debug(
                "agent session language updated",
                extra={"session_id": sid, "language": language},
            )
            return
        self._sessions[sid] = _AgentSession(
            session_id=sid,
            device_id=event.device_id,
            language=language,
            timezone=event.timezone or "",
        )
        logger.debug("agent session created", extra={"session_id": sid})

        proactive = [a for a in self._adapters.values() if a.proactive_enabled]
        if not proactive:
            return

        # R2: 设备上线 → 异步补投 done 未投递的 job 结果（D7，不阻塞事件主链路）。
        # session.started 到达时 conn: 已注册（先连 WS 后发 session.start），
        # PushPublisher 可解析实例。
        if (
            self._deliverer is not None
            and self._cfg.adapters.hermes.deliver_pending_on_reconnect
        ):
            asyncio.create_task(
                self._deliverer.sweep_device(event.device_id, session_id=sid),
                name=f"job-redelivery-sweep-{event.device_id}",
            )

        # R4（fd_job_status.md §4.5）：session.started 首报 —— 设备上线即
        # 发送当前 running 快照（force，无视节流计时）。无 per-device 任务
        # 生命周期，不需要 on_session_ended 配套。
        if self._job_status_notifier is not None:
            asyncio.create_task(
                self._job_status_notifier.on_device_online(event.device_id),
                name=f"job-status-first-{event.device_id}",
            )

        ctx = AgentContext(
            session_id=sid,
            device_id=event.device_id,
            language=language,
            timezone=event.timezone or "",
        )
        for adapter in proactive:
            asyncio.create_task(
                self._call_adapter_lifecycle(adapter.on_session_start(ctx), adapter.name, sid)
            )

        if self._recording_lookup is not None:
            await self._start_watcher(sid, event.device_id, proactive, ctx)

    async def _start_watcher(
        self,
        sid: str,
        device_id: str,
        adapters: list[AgentAdapter],
        ctx: AgentContext,
    ) -> None:
        if sid in self._watchers:
            return
        # watcher 是 adapter 无关组件（D11）：旋钮取自 proactive 段
        # （hermes 旧键在 AgentRelayConfig 里迁移为 alias）
        proactive_cfg = self._cfg.proactive
        watcher = ChatlogWatcher(
            session_id=sid,
            device_id=device_id,
            adapters=adapters,
            context=ctx,
            recording_lookup=self._recording_lookup,
            job_store=self._job_store,
            base_dir=self._base_dir,
            poll_interval_s=proactive_cfg.chatlog_poll_interval_s,
            nudge_coalesce_s=proactive_cfg.nudge_coalesce_s,
            nudge_min_interval_s=proactive_cfg.nudge_min_interval_s,
            nudge_max_lines=proactive_cfg.nudge_max_lines,
            nudge_max_bytes=proactive_cfg.nudge_max_bytes,
        )
        watcher.set_push_callback(self._on_adapter_push)
        self._watchers[sid] = watcher
        started = await watcher.start()
        if not started:
            self._watchers.pop(sid, None)

    async def on_user_utterance(self, event: AgentEvent) -> None:
        if not self._adapters:
            return

        uid = event.transcription_id or ""
        if uid and not self._mark_processed(uid):
            logger.debug("duplicate utterance, dropping", extra={"transcription_id": uid})
            return

        sid = event.session_id
        if sid not in self._sessions:
            # utterance 先于 session.started 到达：临时会话用默认语言 en；
            # session.started 到达时以 session.start.language 覆盖（D1/D6）。
            # 注：utterance 事件不带 timezone（只有 session.started 带）→ timezone 维持 ""，
            # 渲染走"未上报"文案；正常链路里 session.started 先到，不会走到这里
            self._sessions[sid] = _AgentSession(
                session_id=sid,
                device_id=event.device_id,
                language=_DEFAULT_LANGUAGE,
            )

        sess = self._sessions[sid]
        # event.language 是 ASR 诊断元数据，不作为 agent 目标语言（D1）

        # Watcher self-heal: after a relay restart the session's watcher is
        # gone and no new session.started will arrive — re-arm it (and the
        # Hermes session) on the next utterance (§7).
        proactive = [a for a in self._adapters.values() if a.proactive_enabled]
        if sid not in self._watchers and proactive and self._recording_lookup is not None:
            ctx = AgentContext(
                session_id=sid,
                device_id=event.device_id,
                language=sess.language,
                timezone=sess.timezone,
            )
            for adapter in proactive:
                asyncio.create_task(
                    self._call_adapter_lifecycle(adapter.on_session_start(ctx), adapter.name, sid)
                )
            await self._start_watcher(sid, event.device_id, proactive, ctx)

        ctx = AgentContext(
            session_id=sid,
            device_id=event.device_id,
            language=sess.language,
            timezone=sess.timezone,
        )

        for name, adapter in self._adapters.items():
            if not adapter.reactive_enabled:
                continue
            asyncio.create_task(
                self._call_adapter(name, adapter, event.text or "", event.transcription_id or "", ctx)
            )

    async def on_recording_ready(self, event: AgentEvent) -> None:
        if not self._adapters:
            return
        if not self._cfg.proactive.enabled:
            return

        rid = event.recording_id or ""
        if rid and not self._mark_processed(rid):
            logger.debug("duplicate recording event, dropping", extra={"recording_id": rid})
            return

        sid = event.session_id
        sess = self._sessions.get(sid)
        if sess is None:
            return

        now = time.time()
        if now - sess.last_proactive_at < self._cfg.proactive.debounce_s:
            return
        sess.last_proactive_at = now

        if event.duration_ms and event.duration_ms < self._cfg.proactive.min_segment_duration_s * 1000:
            return

        ctx = AgentContext(
            session_id=sid,
            device_id=event.device_id,
            language=sess.language,
            timezone=sess.timezone,
        )

        recording = RecordingInfo(
            recording_id=rid,
            session_id=sid,
            device_id=event.device_id,
            manifest_path=event.manifest_path or "",
            duration_ms=event.duration_ms or 0,
            transcript_endpoint="",
        )

        for name, adapter in self._adapters.items():
            asyncio.create_task(
                self._call_adapter_analyze(name, adapter, recording, ctx)
            )

    async def on_session_ended(self, event: AgentEvent) -> None:
        sid = event.session_id
        watcher = self._watchers.pop(sid, None)
        if watcher is not None:
            await watcher.stop()
        self._sessions.pop(sid, None)

        # D4: job 生命周期不再绑定 livachat session —— 不 orphan，结果 48h 内可补投。
        # orphan_session_jobs() 保留仅供运维清理，不再从本路径触发。

        if not self._adapters:
            return
        ctx = AgentContext(
            session_id=sid,
            device_id=event.device_id,
            language=normalize_language(event.language),
            timezone=event.timezone or "",
        )
        for adapter in self._adapters.values():
            if not adapter.proactive_enabled:
                continue
            asyncio.create_task(
                self._call_adapter_lifecycle(adapter.on_session_end(ctx), adapter.name, sid)
            )
        logger.debug("agent session removed", extra={"session_id": sid})

    async def shutdown(self) -> None:
        # Adapter close 前盘点：done && !delivered 的 job 结果留在 Redis，
        # 下次启动恢复/设备上线补投（§5.6 deferred）。
        if self._job_store is not None:
            try:
                pending = await self._job_store.list_pending_delivery_all()
                if pending:
                    inc_job_event("hermes", "deferred")
                    logger.warning(
                        "shutdown with undelivered job results (left for redelivery)",
                        extra={"count": len(pending)},
                    )
            except Exception:
                logger.warning("failed to count pending deliveries on shutdown", exc_info=True)

        for watcher in list(self._watchers.values()):
            try:
                await watcher.stop()
            except Exception:
                logger.warning("chatlog watcher stop failed", exc_info=True)
        self._watchers.clear()
        for name, adapter in self._adapters.items():
            try:
                await adapter.close()
            except Exception:
                logger.warning("adapter close failed", extra={"adapter": name}, exc_info=True)

    # -- internal -------------------------------------------------------------

    async def _call_adapter_lifecycle(self, coro, name: str, sid: str) -> None:
        """Run an adapter lifecycle callback (on_session_start/on_session_end)
        in a task, swallowing errors so the manager never crashes."""
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "adapter lifecycle callback failed",
                extra={"adapter": name, "session_id": sid},
                exc_info=True,
            )

    async def _on_adapter_push(
        self,
        adapter_name: str,
        device_id: str,
        session_id: str,
        chunk: AgentResponseChunk,
    ) -> None:
        """Push callback for out-of-band adapter events (live nudge pushes).

        Job results no longer pass through this path — they go via the
        JobResultDeliverer (which only needs `conn:{device_id}`, no live
        session). This path still requires the in-memory session (live
        nudge semantics).
        """
        if not chunk.text:
            return
        sess = self._sessions.get(session_id)
        if sess is None:
            logger.debug(
                "agent push for unknown session, dropping",
                extra={"device_id": device_id, "session_id": session_id},
            )
            return
        name = (chunk.metadata or {}).get("agent_name") or adapter_name
        await self._publish_reply(
            name,
            device_id=sess.device_id,
            session_id=session_id,
            text=chunk.text,
            proactive=True,
        )

    async def _publish_reply(
        self,
        name: str,
        *,
        device_id: str,
        session_id: str,
        text: str,
        reply_to: str | None = None,
        proactive: bool = False,
    ) -> None:
        msg = PushMessage(
            device_id=device_id,
            session_id=session_id,
            message_id=_generate_message_id(),
            text=text,
            index=0,
            is_final=True,
            agent_name=name,
            reply_to=reply_to,
            proactive=proactive,
        )
        delivered = await self._publisher.publish(msg)
        if delivered:
            inc_agent_reply(name, "delivered")
        else:
            inc_agent_reply(name, "dropped_offline")
            logger.debug(
                "agent reply dropped (device offline)",
                extra={"device_id": device_id, "agent": name},
            )

    async def _call_adapter(
        self,
        name: str,
        adapter: AgentAdapter,
        text: str,
        transcription_id: str,
        ctx: AgentContext,
    ) -> None:
        t0 = time.monotonic()
        timeout = self._cfg.adapter_call_timeout_s
        try:
            full_text = ""

            async def _consume() -> str:
                result = ""
                async for chunk in adapter.ask(text, ctx):
                    result += chunk.text
                return result

            full_text = await asyncio.wait_for(_consume(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "adapter ask timed out",
                extra={"adapter": name, "session_id": ctx.session_id, "timeout_s": timeout},
            )
            inc_adapter_error(name, "timeout")
            inc_agent_reply(name, "timeout")
            return
        except Exception:
            logger.warning(
                "adapter ask failed",
                extra={"adapter": name, "session_id": ctx.session_id},
                exc_info=True,
            )
            inc_adapter_error(name, _error_type_from_exc())
            inc_agent_reply(name, "error")
            return

        elapsed = time.monotonic() - t0
        observe_reply_latency(name, elapsed)

        if full_text:
            await self._publish_reply(
                name,
                device_id=ctx.device_id,
                session_id=ctx.session_id,
                text=full_text,
                reply_to=transcription_id,
            )

    async def _call_adapter_analyze(
        self,
        name: str,
        adapter: AgentAdapter,
        recording: RecordingInfo,
        ctx: AgentContext,
    ) -> None:
        t0 = time.monotonic()
        timeout = self._cfg.adapter_call_timeout_s
        try:
            full_text = ""

            async def _consume() -> str:
                result = ""
                async for chunk in adapter.analyze_recording(recording, ctx):
                    result += chunk.text
                return result

            full_text = await asyncio.wait_for(_consume(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "adapter analyze_recording timed out",
                extra={"adapter": name, "session_id": ctx.session_id, "timeout_s": timeout},
            )
            inc_adapter_error(name, "timeout")
            inc_agent_reply(name, "timeout")
            return
        except Exception:
            logger.warning(
                "adapter analyze_recording failed",
                extra={"adapter": name, "session_id": ctx.session_id},
                exc_info=True,
            )
            inc_adapter_error(name, _error_type_from_exc())
            inc_agent_reply(name, "error")
            return

        elapsed = time.monotonic() - t0
        observe_reply_latency(name, elapsed)

        if full_text:
            await self._publish_reply(
                name,
                device_id=ctx.device_id,
                session_id=ctx.session_id,
                text=full_text,
                proactive=True,
            )

    def _mark_processed(self, event_id: str) -> bool:
        if event_id in self._processed_ids:
            return False
        self._processed_ids[event_id] = None
        while len(self._processed_ids) > _IDEMPOTENCY_MAX_SIZE:
            self._processed_ids.popitem(last=False)
        return True


def _error_type_from_exc() -> str:
    import sys

    exc = sys.exc_info()[1]
    if exc is None:
        return "unknown"
    name = type(exc).__name__
    if "Timeout" in name:
        return "timeout"
    if "Connect" in name:
        return "connection"
    if "HTTP" in name or "Status" in name:
        return "http_error"
    return "unknown"
