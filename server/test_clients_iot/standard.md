# send :

```json
{
  "id": "device-0",
  "client": "client-0",
  "seq": 0,
  "status": "ok",
  "payload" : {
    "temperature": 27,
    "switchOn": false
  }
}
```

`id` 设备型号
`client` 当前设备标识

# recv :

```json
{
  "type": "device-command",
  "id": "device-0",
  "client" : "client-0",
  "command": "set-switch",
  "requestId": "req-456",
  "payload" :{
    "switchOn": true,
  }
}
```
# report : 


```json
{
  "type": "device-state-report",
  "id": "device-0",
  "client": "client-0",
  "status": "ok",
  "source": "example-program",
  "requestId": "req-123"
  "payload" : {
    "switchOn": true,
  }
}
```