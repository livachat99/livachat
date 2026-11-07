from __future__ import annotations

# ---------------------------------------------------------------------------
# Placeholder for OpenTelemetry tracing.
#
# Tracing is disabled by default (observability.tracing_enabled=false).
# When enabled, this module will configure an OTel TracerProvider with an
# OTLP exporter.  For now this file exists so that future wiring does not
# need to invent a new import path.
# ---------------------------------------------------------------------------


def setup_tracing(*, enabled: bool = False) -> None:
    """Configure OpenTelemetry tracing (no-op until observability.tracing_enabled=true)."""
    if not enabled:
        return
    # TODO(Phase 11): wire OTel SDK when infrastructure is ready.
    raise NotImplementedError("Tracing is not yet implemented")
