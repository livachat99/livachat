# Docker nginx proxy for LivaChat + LiveKit

This directory contains the Docker nginx entry point used to replace the host nginx:

```text
host 8444/TLS
  /rtc*      -> 127.0.0.1:7880   LiveKit signaling
  everything -> 127.0.0.1:8080   LivaChat Server
```

## Files

```text
infra/nginx/
├── docker-compose.yml
├── nginx.conf
├── conf.d/
│   └── livachat_livekit.conf
├── certs/       # local only, gitignored
└── logs/        # local only, gitignored
```

`livachat_livekit.conf.sample` is the portable/system-nginx reference and does not use
container paths.

## Prerequisites

- Docker engine with host networking support (tested with OrbStack)
- A trusted certificate/key for `<server-host>`
- Host LivaChat Server at `127.0.0.1:8080`
- Host LiveKit at `127.0.0.1:7880`

The compose image is pinned to `nginx:1.30.6`.

## Prepare local files

```bash
cd infra/nginx
mkdir -p certs logs
chmod 700 certs logs

cp /path/to/cert.pem certs/cert.pem
cp /path/to/key.pem  certs/key.pem
chmod 600 certs/key.pem
```

Do not commit `certs/` or `logs/`; both are gitignored.

`conf.d/livachat_livekit.conf` expects:

```text
/etc/nginx/certs/cert.pem
/etc/nginx/certs/key.pem
```

The certificate must cover `<server-host>`. The `server_name _;` is intentional for the
single-site deployment; use the real DNS name if more virtual hosts are added.

## Install / switch

```bash
cd infra/nginx

docker compose config
docker compose run --rm nginx nginx -t

# Stop the host nginx (macOS Homebrew example)
brew services stop nginx   # Linux: sudo systemctl stop nginx

# If the currently running local test container uses the same name, stop it first:
# docker rm -f livachat-nginx
# or from its old compose directory: docker compose down

docker compose up -d
docker compose ps
```
