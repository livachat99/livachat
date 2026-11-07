from __future__ import annotations

from threading import Lock
from typing import Any, cast

from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.registry import CollectorRegistry


class MetricsRegistry:
    """Singleton Prometheus metric registry for the application.

    Creating a metric with the same name twice is idempotent — the second call
    returns the already-registered instance.
    """

    def __init__(self) -> None:
        self._reg = CollectorRegistry(auto_describe=True)
        self._lock = Lock()
        self._metrics: dict[str, Any] = {}

    @property
    def registry(self) -> CollectorRegistry:
        return self._reg

    # -- Counters -----------------------------------------------------------

    def messages_total(self) -> Counter:
        return cast(
            Counter,
            self._get_or_create(
                "livachat_messages_total",
                Counter,
                "Total messages processed, partitioned by direction and type.",
                ["direction", "type"],
            ),
        )

    def errors_total(self) -> Counter:
        return cast(
            Counter,
            self._get_or_create(
                "livachat_errors_total",
                Counter,
                "Total errors, partitioned by error code.",
                ["code"],
            ),
        )

    def provider_calls_total(self) -> Counter:
        return cast(
            Counter,
            self._get_or_create(
                "livachat_provider_calls_total",
                Counter,
                "Total provider calls, partitioned by kind, provider, and success.",
                ["kind", "provider", "success"],
            ),
        )

    def interrupts_total(self) -> Counter:
        return cast(
            Counter,
            self._get_or_create(
                "livachat_interrupts_total",
                Counter,
                "Total interrupts, partitioned by reason.",
                ["reason"],
            ),
        )

    # -- Histograms ---------------------------------------------------------

    def stage_latency_seconds(self) -> Histogram:
        return cast(
            Histogram,
            self._get_or_create(
                "livachat_stage_latency_seconds",
                Histogram,
                "Latency per pipeline stage.",
                ["stage"],
                buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            ),
        )

    def provider_latency_seconds(self) -> Histogram:
        return cast(
            Histogram,
            self._get_or_create(
                "livachat_provider_latency_seconds",
                Histogram,
                "Provider call latency, partitioned by kind and provider.",
                ["kind", "provider"],
                buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            ),
        )

    # -- Gauges -------------------------------------------------------------

    def active_connections(self) -> Gauge:
        return cast(
            Gauge,
            self._get_or_create(
                "livachat_active_connections",
                Gauge,
                "Current number of open WebSocket connections.",
            ),
        )

    def active_sessions(self) -> Gauge:
        return cast(
            Gauge,
            self._get_or_create(
                "livachat_active_sessions",
                Gauge,
                "Current number of active sessions.",
            ),
        )

    # -- Recording metrics --------------------------------------------------

    def recording_events_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_recording_events_total", Counter,
            "Total recording events enqueued.", ["kind"],
        ))

    def recording_dropped_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_recording_dropped_total", Counter,
            "Total recording events dropped.", ["kind", "reason"],
        ))

    def recording_bytes_written_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_recording_bytes_written_total", Counter,
            "Total bytes written to recording storage.", ["kind"],
        ))

    def recording_segments_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_recording_segments_total", Counter,
            "Total recording segments created.", ["kind"],
        ))

    def recording_session_active(self) -> Gauge:
        return cast(Gauge, self._get_or_create(
            "livachat_recording_session_active", Gauge,
            "Current number of recording sessions.",
        ))

    def recording_write_errors_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_recording_write_errors_total", Counter,
            "Total recording write errors.", ["stage"],
        ))

    def recording_queue_depth(self) -> Gauge:
        return cast(Gauge, self._get_or_create(
            "livachat_recording_queue_depth", Gauge,
            "Current recording queue depth.", ["device_hash"],
        ))

    # -- Agent Relay metrics --------------------------------------------------

    def agent_events_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_events_total", Counter,
            "Total agent events published, partitioned by kind and result.",
            ["kind", "result"],
        ))

    def agent_replies_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_replies_total", Counter,
            "Total agent replies handled, partitioned by adapter and result.",
            ["adapter", "result"],
        ))

    def agent_reply_latency_seconds(self) -> Histogram:
        return cast(Histogram, self._get_or_create(
            "livachat_agent_reply_latency_seconds", Histogram,
            "Agent reply end-to-end latency in seconds, partitioned by adapter.",
            ["adapter"],
            buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
        ))

    def agent_adapter_errors_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_adapter_errors_total", Counter,
            "Total agent adapter errors, partitioned by adapter and error type.",
            ["adapter", "error_type"],
        ))

    # -- Agent relay v2 (proactive) metrics ----------------------------------

    def agent_nudge_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_nudge_total", Counter,
            "Nudge prompts sent to proactive adapters, partitioned by result.",
            ["adapter", "result"],
        ))

    def agent_job_events_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_job_events_total", Counter,
            "Background job lifecycle events, partitioned by adapter and event.",
            ["adapter", "event"],
        ))

    def agent_interaction_autorespond_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_interaction_autorespond_total", Counter,
            "Automatic responses to blocking interaction requests, by kind.",
            ["kind"],
        ))

    def agent_chatlog_events_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_chatlog_events_total", Counter,
            "Chatlog watcher events, partitioned by result.",
            ["result"],
        ))

    def agent_job_status_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_job_status_total", Counter,
            "Job status snapshot sends, partitioned by result: sent | sent_throttled | skipped_offline | error.",
            ["result"],
        ))

    def agent_session_resumed_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_session_resumed_total", Counter,
            "Hermes sessions resumed after WS reconnect, by adapter.",
            ["adapter"],
        ))

    def agent_device_connections(self) -> Gauge:
        return cast(Gauge, self._get_or_create(
            "livachat_agent_device_connections", Gauge,
            "Current per-device WS connections (no device_id label — bounded cardinality).",
        ))

    def agent_job_pg_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_job_pg_total", Counter,
            "Agent job PG mirror writes, partitioned by result.",
            ["result"],
        ))

    def agent_job_ingest_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_job_ingest_total", Counter,
            "Agent job result ingest into history/chatlog, by path and result.",
            ["path", "result"],   # path: history|chatlog；result: ok|dedup_skip|no_history|failed
        ))

    def chat_store_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_chat_store_total", Counter,
            "Chat server SQLite writes, partitioned by result.",
            ["result"],   # result: written | queue_dropped | write_error | prune_ok | prune_error
        ))

    # -- 去 Redis 化指标（chatserver_no_redis.md §5）----------------------------

    def agent_event_bus_depth(self) -> Gauge:
        return cast(Gauge, self._get_or_create(
            "livachat_agent_event_bus_depth", Gauge,
            "InProcessEventBus queue depth (refreshed on each publish/consume).",
        ))

    def agent_event_bus_dropped_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_event_bus_dropped_total", Counter,
            "Agent events dropped on full event bus queue, by kind.",
            ["kind"],
        ))

    def agent_push_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_push_total", Counter,
            "Local push delivery results: ok | offline | backpressure_timeout | error.",
            ["result"],
        ))

    def agent_push_latency_seconds(self) -> Histogram:
        return cast(Histogram, self._get_or_create(
            "livachat_agent_push_latency_seconds", Histogram,
            "Push publish end-to-end latency including enqueue wait.",
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
        ))

    def agent_job_store_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_job_store_total", Counter,
            "SqliteJobStore writes, partitioned by result: written | cas_miss | error.",
            ["result"],
        ))

    # -- §5.8 WS 保活（hermes_adaptor_design_v2.md）---------------------------

    def agent_liveness_probe_sent_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_liveness_probe_sent_total", Counter,
            "Hermes WS idle-connection liveness probes dispatched (§5.8).",
        ))

    def agent_liveness_probe_timeout_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_liveness_probe_timeout_total", Counter,
            "Hermes WS liveness probes that timed out (no response) (§5.8).",
        ))

    def agent_liveness_force_reconnect_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_liveness_force_reconnect_total", Counter,
            "Hermes WS connections torn down by the liveness failure threshold (§5.8).",
        ))

    # -- OpenClaw Gateway 连接健康（openclaw_adaptor_design.md §5.6）----------

    def agent_gateway_reconnects_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_gateway_reconnects_total", Counter,
            "OpenClaw gateway WS reconnects (disconnect / silence / seq-gap driven).",
        ))

    def agent_gateway_seq_gaps_total(self) -> Counter:
        return cast(Counter, self._get_or_create(
            "livachat_agent_gateway_seq_gaps_total", Counter,
            "OpenClaw gateway event sequence gaps that forced a reconnect.",
        ))

    # -- internal -----------------------------------------------------------

    def _get_or_create(
        self,
        name: str,
        cls: type,
        doc: str,
        labelnames: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                return existing
            if labelnames is None:
                labelnames = []
            metric = cls(name, doc, labelnames, registry=self._reg, **kwargs)
            self._metrics[name] = metric
            return metric


# Process-level singleton
_registry: MetricsRegistry | None = None


def get_registry() -> MetricsRegistry:
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry
