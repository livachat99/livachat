# LivaChat Server — Deployment Runbook

## Prerequisites

- Kubernetes cluster (1.27+)
- nginx-ingress controller with WebSocket support
- PostgreSQL 17 + Redis 8 (or cloud equivalents)
- kubectl configured with target cluster access
- Docker registry access for `livachat-server` image

## Quick Deploy

```bash
# 1. Build and push image
docker build -f infra/docker/server.Dockerfile -t registry.example.com/livachat-server:latest .
docker push registry.example.com/livachat-server:latest

# 2. Create namespace
kubectl create namespace livachat

# 3. Create secrets from example (edit secret.example.yaml first!)
cp infra/kubernetes/secret.example.yaml infra/kubernetes/secret.yaml
# Fill in real API keys in secret.yaml, then:
kubectl apply -f infra/kubernetes/secret.yaml -n livachat

# 4. Apply config
kubectl apply -f infra/kubernetes/configmap.yaml -n livachat

# 5. Deploy
kubectl apply -f infra/kubernetes/deployment.yaml -n livachat
kubectl apply -f infra/kubernetes/service.yaml -n livachat
kubectl apply -f infra/kubernetes/ingress.yaml -n livachat
kubectl apply -f infra/kubernetes/hpa.yaml -n livachat

# 6. Verify
kubectl get pods -n livachat -w
kubectl logs -f deployment/livachat-server -n livachat
```

## Rolling Update

```bash
kubectl set image deployment/livachat-server \
  server=registry.example.com/livachat-server:${VERSION} \
  -n livachat

kubectl rollout status deployment/livachat-server -n livachat
```

## Rollback

```bash
kubectl rollout undo deployment/livachat-server -n livachat
```

## Health Checks

```bash
# Liveness
curl -fsS http://<pod-ip>:8080/healthz

# Readiness
curl -fsS http://<pod-ip>:8080/readyz
```

## Pre-Flight Checklist

- [ ] PG DSN correct and reachable from cluster
- [ ] Redis URL correct and reachable from cluster
- [ ] All provider API keys set (DEEPGRAM, DASHSCOPE, OPENROUTER, DEEPSEEK, CARTESIA, ELEVENLABS)
- [ ] 日志采集路径确认：依赖 stdout 采集（12-factor）时**显式**设 `LIVACHAT__OBSERVABILITY__LOG_FILE__ENABLED=false` 与 `LVDATA_MCP__SERVER__LOG_FILE__ENABLED=false`（默认只写容器内文件；docs/server_logging.md §8.6）
- [ ] TLS certificate provisioned (cert-manager or manual)
- [ ] DNS record for `api.livachat.example.com` → ingress LB
- [ ] Grafana dashboard imported
- [ ] Alerting rules configured for error rate > 1% and latency p95 > 2s

## Staging → Production Promotion

1. Run E2E test suite against staging
2. Verify provider health in Grafana (all green for 5+ minutes)
3. Run load test: `locust -f tests/load-tests/locustfile.py --headless -u 100 -r 10 -t 2m`
4. Review error rate and latency in Grafana
5. Tag image: `docker tag ... livachat-server:prod-YYYYMMDD`
6. Apply to prod namespace
7. Monitor for 15 minutes post-deploy
