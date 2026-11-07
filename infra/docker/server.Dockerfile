# LivaChat Server — multi-stage Dockerfile
# Target: python:3.12-slim runtime, non-root user, < 350MB

# ------------------------------------------------------------------ builder
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libopus0 libopus-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY packages/sdk-python/ ./packages/sdk-python/
COPY services/ ./services/

RUN pip install --no-cache-dir -e packages/sdk-python \
    && pip wheel --no-cache-dir --wheel-dir=/wheels \
    fastapi uvicorn[standard] pydantic pydantic-settings \
    websockets httpx openai sqlalchemy asyncpg alembic \
    redis pyyaml structlog prometheus-client opentelemetry-api \
    soxr opuslib Pillow PyJWT \
    && pip wheel --no-cache-dir --wheel-dir=/wheels \
    packages/sdk-python/ \
    services/

# ------------------------------------------------------------------ runtime
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libopus0 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r livachat && useradd -r -g livachat livachat

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY configs/ ./configs/

RUN pip install --no-cache-dir /wheels/*.whl

RUN mkdir -p /app/logs && chown -R livachat:livachat /app
USER livachat

EXPOSE 8080 9090

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
