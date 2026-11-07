"""Agent relay metrics — thin helpers over the shared MetricsRegistry."""

from __future__ import annotations

from shared.observability.metrics import get_registry


def inc_agent_event(kind: str, result: str = "ok") -> None:
    get_registry().agent_events_total().labels(kind=kind, result=result).inc()


def inc_agent_reply(adapter: str, result: str) -> None:
    get_registry().agent_replies_total().labels(adapter=adapter, result=result).inc()


def inc_adapter_error(adapter: str, error_type: str) -> None:
    get_registry().agent_adapter_errors_total().labels(adapter=adapter, error_type=error_type).inc()


def observe_reply_latency(adapter: str, seconds: float) -> None:
    get_registry().agent_reply_latency_seconds().labels(adapter=adapter).observe(seconds)


# -- v2 proactive metrics ------------------------------------------------------


def inc_nudge(adapter: str, result: str) -> None:
    """result: sent | noop | message | job_request | parse_error | timeout | error"""
    get_registry().agent_nudge_total().labels(adapter=adapter, result=result).inc()


def inc_job_event(adapter: str, event: str) -> None:
    """event: requested | started | completed | rejected | timeout | orphaned |
    rpc_error | noop_output | cancel_sent（用户取消已发出；终态 interrupted/
    aborted/cancelled 是收敛原因）"""
    get_registry().agent_job_events_total().labels(adapter=adapter, event=event).inc()


def inc_interaction_autorespond(kind: str) -> None:
    """kind: approval | clarify | sudo | secret"""
    get_registry().agent_interaction_autorespond_total().labels(kind=kind).inc()


def inc_chatlog_event(result: str) -> None:
    """result: resolved | resolve_failed | nudge | skip | rollover"""
    get_registry().agent_chatlog_events_total().labels(result=result).inc()


def inc_job_status(result: str) -> None:
    """job_status 快照发送结果：sent（变化/首报/清除帧）| sent_throttled
    （周期帧）| skipped_offline（离线保持脏）| error（tick/评估异常）"""
    get_registry().agent_job_status_total().labels(result=result).inc()


def inc_session_resumed(adapter: str) -> None:
    """A surviving Hermes session was resumed after WS reconnect."""
    get_registry().agent_session_resumed_total().labels(adapter=adapter).inc()


def set_device_connections(count: int) -> None:
    """Current per-device WS connection count (aggregate, no device label)."""
    get_registry().agent_device_connections().set(count)


def inc_job_pg(result: str) -> None:
    """result: written | upsert_error | queue_dropped | reconcile_ok | reconcile_error"""
    get_registry().agent_job_pg_total().labels(result=result).inc()


def inc_job_store(result: str) -> None:
    """SqliteJobStore 写结果（chatserver_no_redis.md §5）：written | cas_miss | error"""
    get_registry().agent_job_store_total().labels(result=result).inc()


def inc_push(result: str) -> None:
    """LocalPushPublisher 投递结果（§5）：ok | offline | backpressure_timeout | error"""
    get_registry().agent_push_total().labels(result=result).inc()


def observe_push_latency(seconds: float) -> None:
    get_registry().agent_push_latency_seconds().observe(seconds)


# -- §5.8 WS 保活（hermes_adaptor_design_v2.md §5.8）-------------------------


def inc_liveness_probe_sent() -> None:
    get_registry().agent_liveness_probe_sent_total().inc()


def inc_liveness_probe_timeout() -> None:
    get_registry().agent_liveness_probe_timeout_total().inc()


def inc_liveness_force_reconnect() -> None:
    get_registry().agent_liveness_force_reconnect_total().inc()


# -- OpenClaw Gateway 连接健康（openclaw_adaptor_design.md §5.6）---------------


def inc_gateway_reconnect() -> None:
    get_registry().agent_gateway_reconnects_total().inc()


def inc_gateway_seq_gap() -> None:
    get_registry().agent_gateway_seq_gaps_total().inc()
