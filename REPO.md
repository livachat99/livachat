# LivaChat

LivaChat is a monorepo for a multimodal real-time voice/video conversation product, covering iOS / Android clients and Python backend services, with a unified end-to-end communication protocol maintained in JSON Schema.

See [`docs/mono-repo.md`](docs/mono-repo.md) for the detailed directory structure and documentation.

## Directory overview

| Top-level directory | Description |
| -------- | ---- |
| `apps/` | iOS / Android / Web client applications |
| `services/` | Backend microservices (gateway, asr, llm, tts, session, memory, user-profile, shared) |
| `packages/` | Cross-platform shared packages: `api-schema` (JSON Schema protocol) and protocol DTOs for each language; SDKs for each platform live under `apps/<platform>/` |
| `3rdparty/` | Third-party prebuilt libraries (Opus, WebRTC VAD) and build scripts |
| `infra/` | Terraform / Kubernetes / Docker infrastructure configuration |
| `configs/` | dev / staging / production environment configuration |
| `scripts/` | Bootstrap, build, deploy, and code-generation scripts |
| `tools/` | Code generators, Schema validators, local dev Mock Server; `tools/lvdata-mcp/` (long-running MCP service) |
| `agents/` | AI agent prompts and collaboration rules |
| `docs/` | PRD / UX / protocol / architecture design documents |
| `tests/` | End-to-end, integration, and load tests |

## Key documents

- Product requirements: [`docs/livachat_prd.md`](docs/livachat_prd.md)
- Interaction design: [`docs/livachat_ux.md`](docs/livachat_ux.md)
- WS protocol (Phase 1): [`docs/ws-protocol-design.md`](docs/ws-protocol-design.md)
- Monorepo structure: [`docs/mono-repo.md`](docs/mono-repo.md)
- API Schema documentation: [`packages/api-schema/README.md`](packages/api-schema/README.md)
- Mock Server design/usage:
  [`tools/local-dev/mock-services/livachat_mock_server/DESIGN.md`](tools/local-dev/mock-services/livachat_mock_server/DESIGN.md)
  ·
  [`README.md`](tools/local-dev/mock-services/livachat_mock_server/README.md)
- MCP service design and startup guide: [`tools/lvdata-mcp/README.md`](tools/lvdata-mcp/README.md)

## Common commands

Protocol DTOs are generated from the JSON Schema under `packages/api-schema/`; see the Makefile.

## Development setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e packages/sdk-python -e "services[test,dev]" -e "tools/lvdata-mcp[image,vec,dev]"
```

Environment variables (required; startup fails fast if missing)

`cp .env.example .env` and fill in the required fields.

```bash
.venv/bin/livachat-server     # https://localhost:8080 (hot reload on by default, watches services/ only—restart manually after configs/ changes; set LIVACHAT_SERVER_RELOAD=0 to disable)
.venv/bin/lvdata-mcp serve    # http://127.0.0.1:8090 (/mcp /healthz /readyz)
```

Verification:

```bash
curl -k https://localhost:8080/healthz
curl -s http://127.0.0.1:8090/healthz
```
