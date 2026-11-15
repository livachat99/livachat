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
# config.example.json is the production server-mode template
# (tokenEndpoint=/api/mylife/join_meeting, no LiveKit secret).
npm run dev
# http://localhost:5174/meeting/  (Vite proxies /api to https://localhost:8080)
```

For a client-mode demo, create a separate `public/config.json` with
`tokenMode=client`, a **wss://** LiveKit URL and the demo key pair; then use
`npm run build:demo`. `ws://` (including localhost) is rejected because WebRTC
does not work over it.

`public/config.json` is gitignored. `config.example.json` stays at the app root and is never
copied to `dist/`.

## URL and identity

- `/meeting/<8-digit-number>` (e.g. `/meeting/34479999`) prefills the meeting number;
  `/meeting/` or an invalid path leaves it empty. Manual entry always works.
- The display name is stored in `localStorage` under `livachat-meeting:display-name`;
  missing, invalid, or unreadable values are ignored.
- Inputs have no placeholder text. Touch devices do not autofocus; a fine-pointer
  desktop focuses the first empty field.
- Production must serve unknown `/meeting/*` navigation paths through the SPA fallback
  (`SpaStaticFiles` in `services/app/application.py`); missing assets stay 404.

## Build

```bash
# Demo build: explicitly allows a client-side secret
npm run build:demo

# Production build: public/config.json must be tokenMode=server (no secret)
npm run build
```

The Vite build gate fails if `public/config.json` contains a client-mode `apiSecret` and
`ALLOW_CLIENT_TOKEN=1` is not set. `livekitUrl` is optional in server mode; the BFF
returns `serverUrl` from `meeting.livekit_server` (always `wss://`).

## Test

```bash
npm run check
npm test
```

## Deploy

Serve `dist/` at `/meeting/` over HTTPS. `dist/config.json` is secret material in client mode;
do not upload it to public object storage or public image registries. For production, use
`tokenMode=server` and a same-origin token endpoint.
