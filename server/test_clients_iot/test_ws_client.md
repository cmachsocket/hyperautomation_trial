# test_ws_client 消息说明

本文档说明 `test_ws_client.py` 在 WebSocket 通信中发送和接收的 JSON 内容及其作用。

## 1. Send: 周期性设备数据上报

客户端在 `sender()` 协程中按 `--messages` 和 `--interval` 周期发送：

```json
{
  "id": "device-0",
  "client": "client-0",
  "seq": 0,
  "temperature": 27,
  "status": "ok",
  "switchOn": false
}
```

字段说明：

- `id`：设备标识。服务端按该字段聚合/覆盖设备最新状态。
- `client`：客户端实例名，便于区分并发测试连接。
- `seq`：当前客户端内的消息序号，便于观察发送顺序。
- `temperature`：模拟温度数据（20~35 随机值）。
- `status`：设备状态，示例中固定为 `"ok"`。
- `switchOn`：设备开关状态（本地状态变量）。

作用：

- 模拟 IoT 设备持续上传遥测数据。
- 驱动服务端更新内存中的设备状态，用于前端轮询或广播展示。

## 2. Recv: 设备控制命令

客户端在 `receiver()` 协程中持续接收服务端消息，重点处理如下命令：

```json
{
  "type": "device-command",
  "id": "device-0",
  "command": "toggle",
  "requestId": "req-123"
}
```

或：

```json
{
  "type": "device-command",
  "id": "device-0",
  "command": "set-switch",
  "switchOn": true,
  "requestId": "req-456"
}
```

字段说明：

- `type`：消息类型，必须是 `"device-command"` 才会进入命令处理逻辑。
- `id`：目标设备 ID。仅当与本客户端设备 ID 相同才处理。
- `command`：命令类型。
  - `"toggle"`：本地开关状态取反。
  - `"set-switch"`：将本地开关状态设置为 `switchOn` 指定值。
- `switchOn`：仅在 `"set-switch"` 命令下使用。
- `requestId`：请求追踪 ID，用于回报时关联这次命令。

作用：

- 模拟设备接收并执行平台下发控制命令。
- 忽略无效 JSON、非命令消息、或非本设备消息。

## 3. Send: 命令执行后状态回报

命令执行成功后，客户端会立即发送执行结果：

```json
{
  "type": "device-state-report",
  "id": "device-0",
  "client": "client-0",
  "switchOn": true,
  "status": "ok",
  "source": "example-program",
  "requestId": "req-123"
}
```

字段说明：

- `type`：回报类型，固定为 `"device-state-report"`。
- `id`：设备 ID。
- `client`：客户端实例名。
- `switchOn`：命令执行后的开关最终状态。
- `status`：执行状态，示例中固定为 `"ok"`。
- `source`：消息来源，示例固定为 `"example-program"`。
- `requestId`：透传服务端命令中的 `requestId`，用于请求-响应关联。

作用：

- 让服务端和上层系统拿到设备执行后的权威状态。
- 完成一轮“下发命令 -> 设备执行 -> 状态回报”的闭环。

## 4. 通信流程简图

1. 客户端定时发送设备状态（遥测）。
2. 服务端下发 `device-command`。
3. 客户端更新本地 `switchOn` 状态。
4. 客户端发送 `device-state-report` 回报执行结果。
