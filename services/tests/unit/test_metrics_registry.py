from __future__ import annotations

from shared.observability.metrics import MetricsRegistry


class TestMetricsRegistry:
    def setup_method(self):
        self.reg = MetricsRegistry()

    def test_counter_creation(self):
        c = self.reg.messages_total()
        assert "livachat_messages" in str(c._name)

    def test_gauge_creation(self):
        g = self.reg.active_connections()
        assert g._name == "livachat_active_connections"

    def test_histogram_creation(self):
        h = self.reg.stage_latency_seconds()
        assert h._name == "livachat_stage_latency_seconds"

    def test_duplicate_name_idempotent(self):
        a = self.reg.messages_total()
        b = self.reg.messages_total()
        assert a is b

    def test_counter_increment(self):
        c = self.reg.errors_total()
        c.labels(code="4001").inc()
        c.labels(code="4001").inc()
        sample = c.labels(code="4001")._value.get()
        assert sample == 2.0

    def test_gauge_set(self):
        g = self.reg.active_connections()
        g.set(5)
        assert g._value.get() == 5.0

    def test_histogram_observe(self):
        h = self.reg.provider_latency_seconds()
        h.labels(kind="asr", provider="deepgram").observe(0.25)
        sample = h.labels(kind="asr", provider="deepgram")._sum.get()
        assert sample == 0.25

    def test_multiple_counters_independent(self):
        msgs = self.reg.messages_total()
        errs = self.reg.errors_total()
        msgs.labels(direction="in", type="audio").inc()
        errs.labels(code="1004").inc()
        assert msgs.labels(direction="in", type="audio")._value.get() == 1.0
        assert errs.labels(code="1004")._value.get() == 1.0
