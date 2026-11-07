# LivaChat Server — Load Test Baseline

**Date**: 2026-06-15 (Phase 10)
**Status**: Placeholder — to be updated with real numbers from staging.

## Target

| Metric | Target |
|--------|--------|
| Concurrent connections | 1000 |
| Connection ramp-up rate | 50/s |
| Steady-state duration | 5 min |
| Error rate | < 1% |
| CPU (single instance) | < 80% |
| Memory | < 1GB |
| e2e turn p95 (fake providers) | < 1500ms |

## Baseline (Phase 10 placeholder)

_To be filled after running load tests against staging environment._

| Metric | Value |
|--------|-------|
| Max concurrent connections | - |
| e2e turn p50 | - ms |
| e2e turn p95 | - ms |
| e2e turn p99 | - ms |
| CPU avg / peak | -% / -% |
| Memory avg / peak | - MB / - MB |
| Error rate | -% |

## Run Command

```bash
cd services
locust -f tests/load-tests/locustfile.py --headless \
  -u 1000 -r 50 -t 5m --host ws://localhost:8080
```

## Notes

- The Locust skeleton uses HTTP users; WebSocket load testing requires
  a custom client (e.g., `websockets` library in a Locust `User` subclass).
- For production load testing, consider using `k6` with its WebSocket support.
- All provider calls should use fake/mock implementations during load tests.
