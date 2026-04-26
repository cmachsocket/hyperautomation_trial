# BME280 设备上报规范

本文档定义 BME280 设备与服务端之间的 WebSocket JSON 报文格式。
本设备为上报型设备，只发送 send 报文，不接收控制命令。
如与通用标准冲突，以 [server/coe/standards/standard.md](server/coe/standards/standard.md) 为准。

## 1. 设备上报（send）

用途：设备周期性上报温度、湿度、气压。

```json
{
  "id": "bme280-0",
  "client": "bme280-0",
  "seq": 16,
  "status": "ok",
  "payload": {
    "temperatureC": 26.73,
    "pressureHpa": 1008.12,
    "humidityPct": 48.25,
    "i2cPort": 1,
    "i2cAddress": "0x76",
    "sampledAt": "2026-04-26T10:00:00+00:00"
  }
}
```

字段说明：

- id：设备唯一标识（string 或 number，建议 string）。
- client：设备客户端标识。
- seq：消息序号，建议单调递增。
- status：设备状态，典型值为 ok、error、offline。
- payload：业务载荷。
  - temperatureC：温度（单位摄氏度）。
  - pressureHpa：气压（单位 hPa）。
  - humidityPct：相对湿度（单位 %）。
  - i2cPort：I2C 端口号。
  - i2cAddress：I2C 地址（十六进制字符串，如 0x76）。
  - sampledAt：采样时间（UTC ISO8601）。
  - lastError：最近错误信息（可选，仅异常时建议携带）。

## 2. 设备行为约定

- 设备启动后应立即开始周期上报。
- 默认上报周期为 2 秒（可通过 BME280_REPORT_INTERVAL 调整）。
- 采样异常时：status 置为 error，并在 payload.lastError 写入错误信息。
- 采样异常后会重置 I2C 句柄并在下个周期重试。

## 3. 非目标行为（本设备不实现）

- 不处理 device-command。
- 不发送 device-state-report。
- 不参与命令 requestId 闭环。
