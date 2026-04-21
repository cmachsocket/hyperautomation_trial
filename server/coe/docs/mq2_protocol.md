# MQ2 设备上报规范

本文档定义 MQ2 设备与服务端之间的上报报文格式。
本设备为上报型设备，只发送 send 报文，不接收控制命令。
如与通用标准冲突，以 [server/coe/standards/standard.md](server/coe/standards/standard.md) 为准。

## 1. 设备上报（send）

用途：设备周期性上报可燃气体检测状态。

```json
{
  "id": "mq2-0",
  "client": "mq2-0",
  "seq": 8,
  "status": "ok",
  "payload": {
    "gasDetected": false,
    "rawValue": 1,
    "pin": 17,
    "sampledAt": "2026-04-21T10:00:00+00:00"
  }
}
```

字段说明：

- id：设备唯一标识（string 或 number，建议 string）。
- client：设备客户端标识。
- seq：消息序号，建议单调递增。
- status：设备状态，典型值为 ok、error、offline。
- payload：业务载荷。
  - gasDetected：是否检测到可燃气体（布尔值）。
  - rawValue：MQ2 数字口原始值（0 或 1）。
  - pin：GPIO 引脚号。
  - sampledAt：采样时间（UTC ISO8601）。
  - lastError：最近错误信息（可选，仅 status=error 时建议携带）。

## 2. 设备行为约定

- 设备启动后应立即开始周期上报。
- 建议上报周期为 1 秒（可通过配置调整）。
- MQ2 常见数字口语义：rawValue=0 表示触发报警，rawValue=1 表示未触发。
- 采样异常时：status 置为 error，并在 payload.lastError 写入错误信息。

## 3. 非目标行为（本设备不实现）

- 不处理 device-command。
- 不发送 device-state-report。
- 不参与命令 requestId 闭环。
