# WS Server API Spec

本文档描述 server/ws_server.py 的对外接口与消息语义，供前端、设备端、AI 调用方统一参考。

## 1. 服务概览

- 默认端口: 8081 (环境变量 WS_PORT)
- WebSocket 入口: GET /
- HTTP API 前缀: /api
- CORS:
  - Access-Control-Allow-Origin: *
  - Access-Control-Allow-Methods: GET,POST,OPTIONS
  - Access-Control-Allow-Headers: Content-Type, Authorization
- 通用响应头: Cache-Control: no-store

## 2. 鉴权与环境变量

### 2.1 鉴权规则

- 所有 /api/* 接口默认要求 Bearer Token
- 放行接口:
  - POST /api/auth/login
  - OPTIONS /{tail:.*} (预检)
- WebSocket GET / 当前不做 token 校验

### 2.2 登录与 Token

登录成功后返回签名 token，客户端后续请求需带:

```http
Authorization: Bearer <token>
```

401 典型返回:

```json
{ "message": "Unauthorized: missing bearer token" }
```

```json
{ "message": "Unauthorized: invalid or expired token" }
```

### 2.3 相关环境变量

- WS_PORT: 服务端口，默认 8081
- APP_LOGIN_USERNAME: 登录用户名，默认 admin
- APP_LOGIN_PASSWORD: 登录密码，默认 123456
- AUTH_TOKEN_SECRET: token 签名密钥，默认 hyperautomation-dev-secret
- AUTH_TOKEN_EXPIRE_SECONDS: token 有效期秒数，默认 43200
- APP_VERSION: 当前版本号，未设置时回退 package.json 的 version

## 3. 数据模型

### 3.1 设备 ID

- 字段名: id
- 类型: string | number (服务端统一转为 string)
- 说明: 设备状态、命令转发、映射查询均依赖 id

### 3.2 merged map (内存状态)

- 存储结构: merged_by_id[id] = payload_without_id
- 写入规则: 同一 id 后写覆盖前写
- 查询接口: GET /api/merged-map/{id}

## 4. HTTP API

### 4.1 OPTIONS /{tail:.*}

用途: CORS 预检

- 响应: 204 {}

### 4.2 POST /api/auth/login

用途: 用户登录并获取 Bearer Token

请求体:

```json
{
  "username": "admin",
  "password": "123456"
}
```

成功示例:

```json
{
  "token": "<signed-token>",
  "tokenType": "Bearer",
  "expiresAt": "2026-03-21T08:00:00+00:00",
  "username": "admin"
}
```

错误:

- 400: Invalid JSON / 非对象 / 缺少 username 或 password
- 401: Invalid username or password

### 4.3 GET /api/merged-map/{id}

用途: 按设备 id 获取最新状态

成功示例:

```json
{
  "id": "device-0",
  "payload": {
    "temperature": 27,
    "switchOn": false
  },
  "updatedAt": "2026-03-18T10:00:00+00:00"
}
```

错误:

- 400: id is required (path param)
- 401: 未登录或 token 无效/过期
- 404: No map entry found for id: <id>

### 4.4 GET /api/app-version

用途: 返回当前应用版本号

成功示例:

```json
{
  "version": "0.0.0",
  "updatedAt": "2026-03-18T10:00:00+00:00"
}
```

错误:

- 401: 未登录或 token 无效/过期

### 4.5 POST /api/app-version/publish

用途: 发布新版本号; 不传 version 时自动生成 release-时间戳

请求体 (可选):

```json
{
  "version": "0.0.1"
}
```

成功示例:

```json
{
  "type": "app-version-published",
  "version": "release-1760000000",
  "updatedAt": "2026-03-18T10:00:01+00:00"
}
```

错误:

- 400: Invalid JSON / version must be a string
- 401: 未登录或 token 无效/过期

### 4.6 GET /api/scripts

用途: 获取脚本列表与运行状态

成功示例:

```json
{
  "scripts": [
    {
      "id": "demo-worker-alpha",
      "name": "Demo Worker Alpha",
      "running": false,
      "pid": null,
      "lastStartedAt": null,
      "lastExitedAt": null,
      "lastExitCode": null,
      "stopRequestedAt": null
    }
  ],
  "updatedAt": "2026-03-18T10:00:00+00:00"
}
```

错误:

- 401: 未登录或 token 无效/过期

### 4.7 POST /api/scripts/start

用途: 启动指定脚本

请求体:

```json
{
  "id": "demo-worker-alpha"
}
```

成功示例:

```json
{
  "type": "script-started",
  "script": {
    "id": "demo-worker-alpha",
    "name": "Demo Worker Alpha",
    "running": true,
    "pid": 12345,
    "lastStartedAt": "2026-03-18T10:00:00+00:00",
    "lastExitedAt": null,
    "lastExitCode": null,
    "stopRequestedAt": null
  },
  "alreadyRunning": false,
  "updatedAt": "2026-03-18T10:00:00+00:00"
}
```

错误:

- 400: Payload must include script id (string)
- 401: 未登录或 token 无效/过期
- 404: Script not found

### 4.8 POST /api/scripts/stop

用途: 停止指定脚本

请求体:

```json
{
  "id": "demo-worker-alpha"
}
```

成功示例:

```json
{
  "type": "script-stopped",
  "script": {
    "id": "demo-worker-alpha",
    "running": true,
    "stopRequestedAt": "2026-03-18T10:00:01+00:00"
  },
  "updatedAt": "2026-03-18T10:00:01+00:00"
}
```

错误:

- 400: Payload must include script id (string)
- 401: 未登录或 token 无效/过期
- 404: Script not found
- 409: Script is not running

### 4.9 POST /api/device/command

用途: 向设备转发命令并等待设备回报 (超时 5 秒)

最小请求体:

```json
{
  "id": "device-0",
  "command": "toggle"
}
```

完整请求体示例:

```json
{
  "id": "device-0",
  "command": "set-switch",
  "source": "frontend-widget",
  "client": "optional-client",
  "payload": {
    "switchOn": true
  },
  "switchOn": true
}
```

说明:

- 保留字段: type, id, requestId, command, client, source, payload
- 非保留字段会并入 payload
- 服务端会自动注入 requestId 并下发给设备

成功响应示例:

```json
{
  "type": "device-command-result",
  "id": "device-0",
  "updated": {
    "type": "device-state-report",
    "client": "client-0",
    "status": "ok",
    "source": "example-program",
    "requestId": "1710000000000-ab12cd34",
    "payload": {
      "switchOn": true
    },
    "updatedAt": "2026-03-18T10:00:02+00:00"
  },
  "updatedAt": "2026-03-18T10:00:02+00:00",
  "requestId": "1710000000000-ab12cd34"
}
```

错误:

- 400: JSON 无效 / 缺少 id / 缺少 command
- 401: 未登录或 token 无效/过期
- 404: Target device is not connected
- 409: Target device connection is not writable
- 504: Target device response timeout

### 4.10 POST /api/device/state

用途: 便捷状态接口，映射为命令转发

- action == "toggle" -> command = "toggle"
- 其他情况 -> command = "set-switch"

请求体示例 1:

```json
{
  "id": "device-0",
  "action": "toggle"
}
```

请求体示例 2:

```json
{
  "id": "device-0",
  "payload": {
    "switchOn": true
  }
}
```

响应与错误码同 4.9

### 4.11 POST /api/seed-sample

用途: 写入示例数据并广播 state-updated

成功示例:

```json
{
  "type": "state-updated",
  "id": "demo-switch-1",
  "updated": {
    "payload": {
      "switchOn": false
    },
    "source": "server-seed",
    "updatedAt": "2026-03-18T10:00:00+00:00"
  },
  "updatedAt": "2026-03-18T10:00:00+00:00"
}
```

错误:

- 401: 未登录或 token 无效/过期

## 5. WebSocket 协议

连接地址: ws://host:port/

### 5.1 连接后首条消息

```json
{
  "type": "connected",
  "message": "WebSocket server ready"
}
```

### 5.2 客户端普通状态上报

示例:

```json
{
  "id": "device-0",
  "client": "client-0",
  "seq": 12,
  "status": "ok",
  "payload": {
    "temperature": 27,
    "switchOn": false
  }
}
```

服务端行为:

- 写入 merged map
- 广播 state-updated 给所有 WS 客户端
- 向发送方回 ack

ACK 示例:

```json
{
  "type": "ack",
  "id": "device-0",
  "client": "client-0",
  "seq": 12,
  "status": "ok",
  "payload": {
    "temperature": 27,
    "switchOn": false
  }
}
```

### 5.3 服务端下发设备命令 (由 4.9 触发)

设备收到:

```json
{
  "type": "device-command",
  "id": "device-0",
  "command": "toggle",
  "source": "api-command",
  "requestId": "1710000000000-ab12cd34",
  "payload": {}
}
```

### 5.4 设备命令回报 (device-state-report)

设备发送:

```json
{
  "type": "device-state-report",
  "id": "device-0",
  "client": "client-0",
  "status": "ok",
  "source": "example-program",
  "requestId": "1710000000000-ab12cd34",
  "payload": {
    "switchOn": true
  }
}
```

服务端行为:

- 写入 merged map
- 广播 state-updated
- 若 requestId 命中 pending command，唤醒对应 HTTP 调用并返回结果

注意:

- 对 device-state-report 不返回 ack

### 5.5 服务端广播事件

state-updated:

```json
{
  "type": "state-updated",
  "id": "device-0",
  "updated": {
    "payload": {
      "switchOn": true
    },
    "updatedAt": "2026-03-18T10:00:02+00:00"
  },
  "updatedAt": "2026-03-18T10:00:02+00:00"
}
```

script-started:

```json
{
  "type": "script-started",
  "script": { "id": "demo-worker-alpha", "running": true },
  "alreadyRunning": false,
  "updatedAt": "2026-03-18T10:00:00+00:00"
}
```

script-stopped:

```json
{
  "type": "script-stopped",
  "script": { "id": "demo-worker-alpha", "running": true, "stopRequestedAt": "2026-03-18T10:00:01+00:00" },
  "updatedAt": "2026-03-18T10:00:01+00:00"
}
```

## 6. 错误处理

### 6.1 HTTP 错误格式

```json
{
  "message": "..."
}
```

### 6.2 WS 输入错误

当 JSON 非法或消息缺少 id 时，服务端向该连接回:

```json
{
  "type": "error",
  "message": "..."
}
```

## 7. 联调建议

1. 先调用 4.2 登录拿 token
2. 所有 /api/* 请求加 Authorization: Bearer <token>
3. 设备侧至少先发一次包含 id 的 WS 消息，保证可路由
4. 调用 4.9 控制设备时，设备回报必须透传相同 requestId
5. 出现 504 时先检查设备是否发送 device-state-report 与 requestId

## 8. 快速排查清单

- 401 missing bearer token: 请求未带 Authorization
- 401 invalid or expired token: token 签名错误、过期或密钥不一致
- 404 Target device is not connected: 设备未连接或未注册该 id
- 409 Target device connection is not writable: 连接存在但不可写
- 504 Target device response timeout: 设备未在 5 秒内回报
