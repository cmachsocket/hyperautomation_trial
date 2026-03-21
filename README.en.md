# Vue 3 + Vite

This template helps you get started with Vue 3 in Vite. It uses Vue 3 `<script setup>` SFCs. For details, see the [script setup docs](https://v3.vuejs.org/api/sfc-script-setup.html#sfc-script-setup).

Learn more about Vue IDE support in the [Vue Docs Scaling up Guide](https://vuejs.org/guide/scaling-up/tooling.html#ide-support).

## WebSocket Server

- Install dependencies: `npm install`
- Start server (Python, default): `npm run ws:server`
- Start Python server (explicit): `npm run ws:server:py`
- Start Node server (legacy): `npm run ws:server:node`
- Default address: `ws://localhost:8081`
- Custom port: `WS_PORT=9001 npm run ws:server`

Python server dependency:

- `pip install -r server/requirements.txt`

Expected client message format:

```json
{
  "id": "device-1",
  "temperature": 25,
  "status": "ok"
}
```

Server behavior:

- Accepts JSON objects from multiple clients.
- Requires `id` (`string` or `number`).
- Updates data by `id` in an in-memory `Map` (same `id` replaces previous value).
- Exposes map data by id: `GET /api/merged-map/{id}` (or `GET /api/merged-map?id={id}`)
- Accepts state update JSON: `POST /api/device/state`
- Accepts device command JSON: `POST /api/device/command`
- Broadcasts `state-updated` JSON to all WS clients.
- Exposes script list: `GET /api/scripts`
- Starts script by id: `POST /api/scripts/start` with `{ "id": "demo-worker-alpha" }`
- Stops script by id: `POST /api/scripts/stop` with `{ "id": "demo-worker-alpha" }`

## Python IoT Test Clients

- Install dependency: `pip install websockets`
- Run example: `python server/test_clients_iot/test_ws_client.py --clients 4 --messages 5`
- Optional args:
  - `--url ws://localhost:8081`
  - `--interval 0.5`
  - `--listen-seconds 5`

This script sends and receives concurrently (one coroutine sends, one coroutine keeps receiving ack/event).
It also serves as an example program: it receives `device-command` from the server, updates local switch state, and sends `device-state-report` back.
This script is intended for local integration testing in CPython.
Production can use MicroPython with the same JSON message format (`id` + payload fields).

## Frontend Demo Component

- Dynamic module directory: `src/components/dynamic/` (pages and widgets have been merged)
- Example module names: `UpdatedMapWidget`, `DashboardPage`, `ReportPage`
- Polls `http://localhost:8081/api/merged-map/{id}` every 2 seconds and displays latest data.
- Includes a switch button; clicking sends `{ "id": "demo-switch-1", "action": "toggle" }` to `POST /api/device/command`.
- Server returns latest state in `updated`; the page reads switch state from `updated.switchOn`.
- Server also broadcasts a `state-updated` event for clients.
- If your server address differs, use Vite env vars:
  - `VITE_API_BASE_URL=http://127.0.0.1:8081`
  - `VITE_AI_BASE_URL=http://127.0.0.1:8082`
  - `VITE_WS_URL=ws://127.0.0.1:8081`

## Script Control Page

- Page name: `ScriptControlPage`
- Component path: `src/components/ScriptControlPage.vue` (moved out of `manual-pages`)
- Each row is one script with status plus start/stop buttons.
- Controlled scripts are auto-discovered from `src/scripts/` (all `.js` files).
- These scripts run independently (standalone) and do not go through server command forwarding.

## AI Chat (HTTP 502 Troubleshooting)

- AI chat API uses same-origin frontend path `/api/chat`, proxied by Vite to `http://127.0.0.1:8082`.
- If AI chat returns `HTTP 502`, the AI service is usually not running.
- Start command: `npm run ai:controller`
- Start WS + AI together: `npm run servers:start`

## Android Build (Remote Backend)

- First-time dependency install: `npm install`
- Configure production backend address: edit `.env.production`
- Build and sync to Android project: `npm run build:android`
- Open Android Studio: `npm run cap:open:android`

Recommendations:

- Prefer HTTPS + WSS in production.
- Backend should allow mobile-origin CORS and auth headers.

## GitHub Actions (Auto Build Android on Tag)

- Workflow file: `.github/workflows/android-tag-build.yml`
- Trigger: push tag (for example `v1.0.0`)
- Artifact: Debug APK (Actions Artifacts)

Example:

- Create tag: `git tag v1.0.0`
- Push tag: `git push origin v1.0.0`
