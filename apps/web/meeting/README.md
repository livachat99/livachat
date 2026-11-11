# LivaChat Meeting

React + Vite + LiveKit web meeting SPA. Design: [`docs/webmeeting_design.md`](../../../docs/webmeeting_design.md).

## Security boundary

This app is for **demo / internal verification only**. In `tokenMode=client` the LiveKit API
secret is embedded in `public/config.json` and is visible to anyone who can open the page.
Do not expose it publicly and do not use a production LiveKit key. The production path is
`tokenMode=server` (see design §3.13.4).

## Development

```bash
npm install
cp config.example.json public/config.json
# Fill public/config.json with the real demo server URL / key pair.
npm run dev
# http://localhost:5174/meeting/
```

`public/config.json` is gitignored. `config.example.json` stays at the app root and is never
copied to `dist/`.

## Build

```bash
# Demo build: explicitly allows a client-side secret
npm run build:demo

# Production build: public/config.json must be tokenMode=server (no secret)
npm run build
```

The Vite build gate fails if `public/config.json` contains a client-mode `apiSecret` and
`ALLOW_CLIENT_TOKEN=1` is not set.

## Test

```bash
npm run check
npm test
```

## Deploy

Serve `dist/` at `/meeting/` over HTTPS. `dist/config.json` is secret material in client mode;
do not upload it to public object storage or public image registries. For production, use
`tokenMode=server` and a same-origin token endpoint.
