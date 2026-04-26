# OLED 设备报文规范

本文档定义 OLED 设备与服务端之间的 WebSocket JSON 报文格式。
如与通用标准冲突，以 [server/coe/standards/standard.md](server/coe/standards/standard.md) 为准。

## 1. 设备上报（send）

用途：设备周期性上报当前屏幕文本状态。

```json
{
  "id": "oled-0",
  "client": "oled-0",
  "seq": 6,
  "status": "ok",
  "payload": {
    "rows": 4,
    "charsPerRow": 20,
    "lineHeight": 16,
    "i2cPort": 1,
    "i2cAddress": "0x3C",
    "sampledAt": "2026-04-26T10:00:00+00:00",
    "lines": [
      "Line1",
      "Line2",
      "Line3",
      "Line4"
    ],
    "lastCommand": "set-text"
  }
}
```

字段说明：

- id：设备唯一标识（string 或 number，建议 string）。
- client：设备客户端标识。
- seq：设备消息序号，建议单调递增。
- status：设备状态，典型值为 ok、offline、error。
- payload：业务状态载荷。
  - rows：屏幕行数。
  - charsPerRow：每行最大字符数，超过会截断。
  - lineHeight：像素行高。
  - i2cPort：I2C 端口号。
  - i2cAddress：I2C 地址（十六进制字符串）。
  - sampledAt：状态采样时间（UTC ISO8601）。
  - lines：当前每行文字数组，长度固定为 rows。
  - lastCommand：最近一次执行命令（可选）。
  - lastError：最近一次错误信息（可选）。

## 2. 设备接收命令（recv）

用途：服务端向 OLED 下发文本显示命令。

```json
{
  "type": "device-command",
  "id": "oled-0",
  "command": "set-text",
  "requestId": "req-oled-001",
  "payload": {
    "lines": [
      "Hyper",
      "Automation",
      "OLED Ready",
      "2026-04-26"
    ]
  }
}
```

字段说明：

- type：固定为 device-command。
- id：目标设备 id，必须与设备自身 id 一致。
- command：命令类型。
- requestId：请求追踪 ID，设备回报时必须透传。
- payload：命令参数对象。

支持命令：

- set-text
- show-text
- render-text

## 3. 命令参数约定

显示文本有两种等价写法，二选一：

1. payload.lines（推荐）

```json
{
  "lines": ["line1", "line2", "line3", "line4"]
}
```

约束：

- lines 必须是字符串数组。
- lines 长度必须小于等于 rows。
- 若长度小于 rows，缺省行会自动补空字符串。
- 每行会按 charsPerRow 自动截断。

2. payload.line1 到 payload.lineN

```json
{
  "line1": "line1",
  "line2": "line2",
  "line3": "line3",
  "line4": "line4"
}
```

约束：

- 每个 lineX 必须是字符串。
- 未提供的行默认为空字符串。
- 每行会按 charsPerRow 自动截断。

## 4. 命令执行回报（report）

用途：设备执行命令后回报结果。

```json
{
  "type": "device-state-report",
  "id": "oled-0",
  "client": "oled-0",
  "status": "ok",
  "source": "oled-device",
  "requestId": "req-oled-001",
  "updatedAt": "2026-04-26T10:00:00+00:00",
  "payload": {
    "command": "set-text",
    "rows": 4,
    "charsPerRow": 20,
    "lineHeight": 16,
    "lines": ["Hyper", "Automation", "OLED Ready", "2026-04-26"]
  }
}
```

字段说明：

- type：固定为 device-state-report。
- id：设备 id。
- client：设备客户端标识。
- status：执行状态，取值建议为 ok、refused、failed。
- source：消息来源，建议固定为 oled-device。
- requestId：对应命令中的 requestId。
- updatedAt：回报时间（UTC ISO8601）。
- payload：执行后的设备状态。

错误场景约定：

- 缺少 requestId：回报 status=refused，payload.reason="missing requestId"。
- 不支持的 command：回报 status=refused，并给出 payload.reason。
- 参数格式非法：回报 status=refused，并给出 payload.reason。
- 执行异常：回报 status=failed，并给出 payload.error。
