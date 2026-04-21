# 蜂鸣器设备报文规范

本文档定义蜂鸣器设备与服务端之间的 WebSocket JSON 报文格式。
如与通用标准冲突，以 [server/coe/standards/standard.md](server/coe/standards/standard.md) 为准。

## 1. 设备上报（send）

用途：设备周期性上报当前状态。

```json
{
  "id": "beeper-0",
  "client": "beeper-0",
  "seq": 12,
  "status": "ok",
  "payload": {
    "beeperOn": false,
    "pin": 10,
    "lastCommand": "set-switch"
  }
}
```

字段说明：

- id：设备唯一标识（string 或 number，建议 string）。
- client：设备客户端标识。
- seq：设备消息序号，建议单调递增。
- status：设备状态，典型值为 ok、offline、error。
- payload：业务状态载荷。
  - beeperOn：蜂鸣器当前是否开启。
  - pin：GPIO 引脚号。
  - lastCommand：最近一次执行的命令（可选）。
  - lastError：最近一次错误信息（可选）。

## 2. 设备接收命令（recv）

用途：服务端向蜂鸣器下发控制命令。

```json
{
  "type": "device-command",
  "id": "beeper-0",
  "command": "set-switch",
  "requestId": "1710000000000-ab12cd34",
  "payload": {
    "switchOn": true
  }
}
```

字段说明：

- type：固定为 device-command。
- id：目标设备 id，必须与设备自身 id 一致。
- command：命令类型。
- requestId：请求追踪 ID，设备回报时必须透传。
- payload：命令参数对象。

## 3. 命令执行回报（report）

用途：设备执行命令后回报结果。

```json
{
  "type": "device-state-report",
  "id": "beeper-0",
  "client": "beeper-0",
  "status": "ok",
  "source": "beeper-device",
  "requestId": "1710000000000-ab12cd34",
  "payload": {
    "beeperOn": true,
    "pin": 10,
    "command": "set-switch"
  }
}
```

字段说明：

- type：固定为 device-state-report。
- id：设备 id。
- client：设备客户端标识。
- status：执行状态，取值建议：ok、refused、failed。
- source：消息来源，建议固定 beeper-device。
- requestId：对应命令的 requestId。
- payload：执行后的状态。

## 4. 命令约定

蜂鸣器设备支持以下命令：

- toggle：切换当前开关状态。
- set-switch / set-beeper / set-buzzer / set-state：按参数设置状态。
  - payload 可使用 beeperOn、switchOn、on、enabled、value 任一布尔字段。
- beep / pulse / buzz：蜂鸣一段时间。
  - payload.durationMs：蜂鸣时长（毫秒），默认 300。
- on / open / start：打开蜂鸣器。
- off / close / stop：关闭蜂鸣器。

错误场景约定：

- 缺少 requestId：回报 status=refused。
- 不支持的 command：回报 status=refused，并在 payload.reason 说明原因。
- 执行异常：回报 status=failed，并在 payload.error 说明错误。

## 5. 校验规则

- 所有消息必须是合法 JSON 对象。
- id 必填且类型为 string 或 number。
- 命令消息必须包含 command 与 requestId。
- report 必须携带与命令一致的 requestId。
- payload 建议始终为对象。

## 6. 通信流程

1. 设备连接 WebSocket 后周期性发送 send。
2. 服务端按需下发 device-command。
3. 设备执行命令并回报 device-state-report。
4. 服务端基于 requestId 关联命令闭环并返回调用结果。
