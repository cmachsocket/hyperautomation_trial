## WebSocket 服务器

- 安装依赖：`npm install`
- 启动服务器（Python，默认）：`npm run ws:server`
- 启动 Python 服务器（显式）：`npm run ws:server:py`
- 默认地址：`ws://localhost:8081`
- 自定义端口：`WS_PORT=9001 npm run ws:server`

Python 服务器依赖：

- `pip install -r server/requirements.txt`

客户端消息格式示例：

```json
{
	"id": "device-1",
	"temperature": 25,
	"status": "ok"
}
```

服务器行为：

- 接收多个客户端发送的 JSON 对象。
- `id` 为必填（`string` 或 `number`）。
- 按 `id` 更新内存中的 `Map`（相同 `id` 会覆盖旧值）。
- 按 id 提供地图数据接口：`GET /api/merged-map/{id}`（或 `GET /api/merged-map?id={id}`）
- 接收状态上报 JSON：`POST /api/device/state`
- 接收设备命令 JSON：`POST /api/device/command`
- 向所有 WS 客户端广播 `state-updated` JSON
- 提供脚本列表接口：`GET /api/scripts`
- 按 id 启动脚本：`POST /api/scripts/start`，请求体 `{ "id": "demo-worker-alpha" }`
- 按 id 停止脚本：`POST /api/scripts/stop`，请求体 `{ "id": "demo-worker-alpha" }`

## Python IoT 测试客户端

- 安装依赖：`pip install websockets`
- 运行示例：`python server/test_clients_iot/test_ws_client.py --clients 4 --messages 5`
- 可选参数：
	- `--url ws://localhost:8081`
	- `--interval 0.5`
	- `--listen-seconds 5`

该脚本支持并发收发（一个协程发送，另一个协程持续接收 ack/event）。
它同时也是示例程序：接收服务端 `device-command` 后更新本地开关状态，再回传 `device-state-report`。
该脚本用于 CPython 本地联调测试。
生产环境可使用 MicroPython，消息格式保持一致（`id` + 业务字段）。

## 前端演示组件

- 动态模块目录：`src/components/dynamic/`（页面与 widgets 已合并）
- 示例模块名：`UpdatedMapWidget`、`DashboardPage`、`ReportPage`
- 每 2 秒轮询 `http://localhost:8081/api/merged-map/{id}` 并显示最新数据。
- 带有开关按钮，点击后向 `POST /api/device/command` 发送 `{ "id": "demo-switch-1", "action": "toggle" }`。
- 服务器会在 `updated` 字段返回最新状态，页面从 `updated.switchOn` 读取开关值。
- 服务器也会广播 `state-updated` 事件供客户端接收。
- 如果你的服务地址不同，请设置 Vite 环境变量：
	- `VITE_API_BASE_URL=http://127.0.0.1:8081`
	- `VITE_AI_BASE_URL=http://127.0.0.1:8081`
	- `VITE_WS_URL=ws://127.0.0.1:8081`

## 脚本控制页

- 页面名称：`ScriptControlPage`
- 组件路径：`src/components/ScriptControlPage.vue`（已移出 `manual-pages`）
- 每行对应一个脚本，包含状态与启动/停止按钮。
- 可控脚本会从 `src/scripts/` 自动发现（`.ts` 与 `.js` 文件，若同名则优先 `.ts`）。
- 这些脚本独立运行（standalone），不经过服务端命令转发。

## AI 聊天（同端口挂载）

- AI 聊天接口挂载在主服务同源路径 `/api/ai/chat`。
- 如果 AI 聊天出现 `HTTP 502`，通常是主服务未启动，或前端仍在访问旧地址。
- 需要配置 Anthropic 相关环境变量：`ANTHROPIC_API_KEY`、`AI_MODEL`（可选：`ANTHROPIC_BASE_URL`）。
- 启动主服务与 MCP：`npm run servers:start`
- 单独启动 MCP：`npm run ai:controller`

## Android 打包（后端远程）

- 首次安装依赖：`npm install`
- 配置生产环境后端地址：编辑 `.env.production`
- 构建并同步到 Android 工程：`npm run build:android`
- 打开 Android Studio：`npm run cap:open:android`

建议：

- 生产环境优先使用 HTTPS + WSS。
- 后端需要允许移动端来源的 CORS 和鉴权请求头。

## GitHub Actions（Tag 自动构建 Android）

- 工作流文件：`.github/workflows/android-tag-build.yml`
- 触发条件：push tag（例如 `v1.0.0`）
- 产物：Debug APK（Actions Artifacts）

示例：

- 创建 tag：`git tag v1.0.0`
- 推送 tag：`git push origin v1.0.0`