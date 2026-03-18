# IoT Device Test Clients

本目录用于 IoT 设备测试客户端。

## 报文标准

本目录中 IoT 设备通信报文以 [server/test_clients_iot/standard.md](server/test_clients_iot/standard.md) 为唯一标准。
下文为标准内容的结构化说明，服务端与客户端联调请按该格式执行。

## 1. send（设备上报）

```json
{
	"id": "device-0",
	"client": "client-0",
	"seq": 0,
	"status": "ok",
	"payload": {
		"temperature": 27,
		"switchOn": false
	}
}
```

字段说明：

- `id`：设备型号。
- `client`：当前设备标识。
- `seq`：上报序号。
- `status`：设备状态。
- `payload`：业务数据载荷（例如温度、开关状态）。

## 2. recv（设备接收命令）

```json
{
	"type": "device-command",
	"id": "device-0",
	"client": "client-0",
	"command": "set-switch",
	"requestId": "req-456",
	"payload": {
		"switchOn": true
	}
}
```

字段说明：

- `type`：固定为 `device-command`。
- `id`：目标设备型号。
- `client`：目标设备标识。
- `command`：命令类型。
- `requestId`：请求追踪 ID。
- `payload`：命令参数载荷。

## 3. report（设备命令回报）

```json
{
	"type": "device-state-report",
	"id": "device-0",
	"client": "client-0",
	"status": "ok",
	"source": "example-program",
	"requestId": "req-123",
	"payload": {
		"switchOn": true
	}
}
```

字段说明：

- `type`：固定为 `device-state-report`。
- `id`：设备型号。
- `client`：当前设备标识。
- `status`：执行状态。
- `source`：消息来源。
- `requestId`：与命令请求关联的追踪 ID。
- `payload`：执行后的设备状态载荷。

## 相关文件

- `test_ws_client.py`：WebSocket 测试客户端实现。
- `test_ws_client.md`：历史消息说明文档（如与标准冲突，以 standard.md 为准）。
