# LivaChat Server — Troubleshooting Runbook

## Common Issues

### 1. WebSocket 401 Unauthorized

**Symptom**: Client gets HTTP 401 on WebSocket handshake.

**Check**:
```bash
# Verify dev token matches config
kubectl get secret livachat-secrets -n livachat -o jsonpath='{.data.APP_AUTH_TOKEN}' | base64 -d

# Check auth mode
kubectl get configmap livachat-config -n livachat -o yaml | grep "mode:"
```

**Fix**: Ensure the client sends the wire token `sha1_hex(APP_AUTH_TOKEN + "LivaChat")` — the plaintext secret must never be sent (docs/server_auth.md §3.1) — and that client and server hold the same secret. A client still sending the plaintext token gets 401 by design.

---

### 2. Provider 429 Rate Limit

**Symptom**: `ProviderError` with HTTP 429 in logs. `livachat_provider_calls_total{success="false"}` increasing.

**Check**:
```bash
# View recent provider errors
kubectl logs -l app=livachat-server -n livachat --tail=200 | grep "429\|rate.limit"
```

**Fix**:
1. Switch active provider via config: `kubectl edit configmap livachat-config -n livachat` → change `asr.active` / `llm.active` / `tts.active`
2. Restart deployment: `kubectl rollout restart deployment/livachat-server -n livachat`
3. Contact provider to increase quota

---

### 3. PostgreSQL Connection Pool Exhausted

**Symptom**: `/readyz` returns 503 with `postgres: error`. Logs show `asyncpg.exceptions.TooManyConnectionsError`.

**Check**:
```bash
# Active PG connections
kubectl exec -it deployment/livachat-server -n livachat -- \
  python -c "from sqlalchemy import text; ..."
```

**Fix**:
1. Increase `postgres.pool_max` in configmap (default: 20)
2. Check for connection leaks (ensure `persist_async` used for all PG writes)
3. Restart deployment to kill stale connections

---

### 4. Redis Connection Failure

**Symptom**: `/readyz` returns 503 with `redis: error`. Session resume broken.

**Check**:
```bash
kubectl exec -it deployment/redis -n livachat -- redis-cli PING
```

**Fix**:
1. Verify Redis service is running
2. Check Redis memory: `redis-cli INFO memory | grep used_memory_human`
3. If OOM, increase Redis memory limit or enable eviction

---

### 5. High E2E Turn Latency

**Symptom**: `livachat_stage_latency_seconds{stage="e2e_turn"}` p95 > 2s.

**Check**:
```bash
# Which stage is slow?
kubectl logs -l app=livachat-server -n livachat --tail=500 | grep "stage.latency"
```

**Fix by stage**:
- `asr_first_partial`: Check ASR provider latency; try switching active provider
- `llm_first_token`: Check LLM provider latency; verify model selection
- `tts_first_chunk`: Check TTS provider; verify sentence splitter isn't buffering too long

---

### 6. Pod CrashLoopBackOff

**Symptom**: Pod restarts repeatedly.

**Check**:
```bash
kubectl describe pod -l app=livachat-server -n livachat | grep -A5 "Last State"
kubectl logs -l app=livachat-server -n livachat --previous
```

**Common causes**:
- Missing required env vars (config validation fail-fast)
- libopus not found in container (check Dockerfile)
- OOMKilled (increase memory limit)

---

### 7. WebSocket Connection Leaks

**Symptom**: `livachat_active_connections` steadily increases without decreasing.

**Fix**:
1. Check heartbeat configuration (ensure idle timeout is enforced)
2. Verify client implements proper close handshake
3. Restart deployment to clear leaked connections

---

## Quick Diagnostic Commands

```bash
# Pod status
kubectl get pods -n livachat -o wide

# Recent logs
kubectl logs -l app=livachat-server -n livachat --tail=100 --timestamps

# Resource usage
kubectl top pods -n livachat

# Check /readyz on all pods
for pod in $(kubectl get pods -l app=livachat-server -n livachat -o name); do
  echo "$pod: $(kubectl exec -it $pod -n livachat -- curl -s localhost:8080/readyz)"
done
```
