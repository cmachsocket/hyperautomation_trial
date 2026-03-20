# Vue 3 + Vite

This template should help get you started developing with Vue 3 in Vite. The template uses Vue 3 `<script setup>` SFCs, check out the [script setup docs](https://v3.vuejs.org/api/sfc-script-setup.html#sfc-script-setup) to learn more.

Learn more about IDE Support for Vue in the [Vue Docs Scaling up Guide](https://vuejs.org/guide/scaling-up/tooling.html#ide-support).

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
- Updates data by `id` in in-memory `Map` (same `id` replaces previous value).
- Exposes map data by id: `GET /api/merged-map/{id}` (or `GET /api/merged-map?id={id}`)
- Accepts state update JSON: `POST /api/device/state`
- Accepts device command JSON: `POST /api/device/command`
- Broadcasts `state-updated` JSON to all WS clients
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
It also acts as the example program: receives `device-command` from server, updates local switch state, and sends `device-state-report` back.
This script is for local integration testing in CPython.
Production can use MicroPython with the same JSON message format (`id` + payload fields).

## Frontend Demo Component

- Dynamic module directory: `src/components/dynamic/`（页面与 widgets 已合并）
- Example module names: `UpdatedMapWidget`, `DashboardPage`, `ReportPage`
- It polls `http://localhost:8081/api/merged-map/{id}` every 2 seconds and shows latest updated data.
- It has a switch button; clicking sends `{ "id": "demo-switch-1", "action": "toggle" }` to `POST /api/device/command`.
- The server returns latest state in `updated`; the page reads switch state from `updated.switchOn`.
- The server also broadcasts a `state-updated` event for clients to receive.
- If your server address is different, use Vite env vars:
	- `VITE_API_BASE_URL=http://127.0.0.1:8081`
	- `VITE_AI_BASE_URL=http://127.0.0.1:8082`
	- `VITE_WS_URL=ws://127.0.0.1:8081`

## Script Control Page

- Page name: `ScriptControlPage`
- Component path: `src/components/ScriptControlPage.vue`（已移出 `manual-pages`）
- Each row is one script with status + start/stop buttons.
- Controlled scripts are auto-discovered from `src/scripts/` (all `.js` files).
- These scripts run independently (standalone) and do not go through server command forwarding.

## AI Chat (HTTP 502 排查)

- AI 聊天接口默认走前端同源路径 `/api/chat`，由 Vite 代理到 `http://127.0.0.1:8082`。
- 如果 AI 聊天出现 `HTTP 502`，通常是 AI 服务未启动。
- 启动命令：`npm run ai:controller`
- 一键同时启动 WS + AI：`npm run servers:start`

## Android 打包（后端远程）

- 首次安装依赖：`npm install`
- 配置生产环境后端地址：编辑 `.env.production`
- 构建并同步到 Android 工程：`npm run build:android`
- 打开 Android Studio：`npm run cap:open:android`

建议：

- 生产环境优先使用 HTTPS + WSS。
- 后端需要允许移动端来源的 CORS 和鉴权请求头。

