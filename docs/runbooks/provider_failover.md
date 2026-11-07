# LivaChat Server — Provider Failover Runbook

## Overview

LivaChat supports automatic failover across multiple ASR / LLM / TTS providers.
Failover behavior:

| Stage | Failover Window | Behavior |
|-------|----------------|----------|
| ASR `open()` | Per-turn | Tries [active, *fallback] in order; first success wins |
| LLM `stream()` | Before first token only | Before first token: switches to next provider. After first token: **no failover** — error surfaced to the client as an ai_text error message (≤1k chars); all failures show as error text (no error frame). 4xx unrecoverable (400-499 except 408/429) additionally rolls back the turn's user record (WorkingMemory + messages row). LLM read timeout = 60s per chunk gap. See `docs/fd_pipeline_llm_v2.md` |
| TTS `open()` | Per-turn | Tries [active, *fallback] in order |

## Checking Current Provider Health

Grafana: "Provider Calls" panel shows success/failure per provider.

## Manual Failover (Emergency)

### Option A: ConfigMap Update (rolling restart)

```bash
# 1. Edit config
kubectl edit configmap livachat-config -n livachat

# Change e.g. asr.active from "deepgram" to "alibaba_bailian"
# Or swap fallback order

# 2. Restart to pick up new config
kubectl rollout restart deployment/livachat-server -n livachat

# 3. Verify
kubectl rollout status deployment/livachat-server -n livachat
kubectl logs -l app=livachat-server -n livachat --tail=20 | grep "provider"
```

### Option B: Environment Variable Override

```bash
kubectl set env deployment/livachat-server -n livachat \
  LIVACHAT__ASR__ACTIVE=alibaba_bailian
```

This overrides the YAML config value; takes effect after pod restart.

## Provider-Specific Failover Commands

### ASR: Deepgram → Alibaba Bailian

```bash
kubectl set env deployment/livachat-server -n livachat \
  LIVACHAT__ASR__ACTIVE=alibaba_bailian
kubectl rollout restart deployment/livachat-server -n livachat
```

### LLM: Bailian → OpenRouter

```bash
kubectl set env deployment/livachat-server -n livachat \
  LIVACHAT__LLM__ACTIVE=openrouter
kubectl rollout restart deployment/livachat-server -n livachat
```

### TTS: Cartesia → ElevenLabs

```bash
kubectl set env deployment/livachat-server -n livachat \
  LIVACHAT__TTS__ACTIVE=elevenlabs
kubectl rollout restart deployment/livachat-server -n livachat
```

## Impact Assessment

| Action | User Impact |
|--------|------------|
| ConfigMap update + restart | ~5s of 503 (rolling update, 0 unavailable pods) |
| ENV override + restart | Same as above |
| Active provider change mid-session | In-flight turns may get error(100x); client retries |

## Rollback

```bash
# Revert env override
kubectl set env deployment/livachat-server -n livachat \
  LIVACHAT__ASR__ACTIVE-

# Or revert configmap change
kubectl rollout undo deployment/livachat-server -n livachat
```

## Adding a New Provider

1. Implement `AsrProvider` / `LlmProvider` / `TtsProvider` interface in `services/<module>/providers/`
2. Register in factory (`services/<module>/factory.py`)
3. Add to config YAML with `enabled: true`
4. Add to `fallback` list if desired
5. Deploy and verify via `/readyz`
