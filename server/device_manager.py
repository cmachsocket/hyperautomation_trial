from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from datetime import datetime, timezone
from typing import Any, cast


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_id(value: Any) -> str | None:
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


class DeviceManager:
    def __init__(self) -> None:
        self.merged_by_id: dict[str, dict[str, Any]] = {}
        self.device_sockets: dict[str, set[int]] = {}
        self.socket_devices: dict[int, set[str]] = {}
        self.connections: dict[int, Any] = {}
        self.pending_commands: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.all_ws_clients: set[int] = set()

    def set_by_id(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_id = normalize_id(payload.get("id"))
        if normalized_id is None:
            raise ValueError("payload must include id")

        stored = {k: v for k, v in payload.items() if k != "id"}
        self.merged_by_id[normalized_id] = stored
        return stored

    def register_connection(self, ws: Any) -> int:
        connection_id = id(ws)
        self.connections[connection_id] = ws
        self.all_ws_clients.add(connection_id)
        return connection_id

    def register_socket_for_device(self, ws: Any, device_id: str) -> None:
        connection_id = self.register_connection(ws)
        self.device_sockets.setdefault(device_id, set()).add(connection_id)
        self.socket_devices.setdefault(connection_id, set()).add(device_id)

    def unregister_socket(self, ws: Any) -> None:
        connection_id = id(ws)
        self.all_ws_clients.discard(connection_id)
        self.connections.pop(connection_id, None)

        ids = self.socket_devices.pop(connection_id, set())
        for device_id in ids:
            sockets = self.device_sockets.get(device_id)
            if not sockets:
                continue
            sockets.discard(connection_id)
            if not sockets:
                self.device_sockets.pop(device_id, None)

    async def send_json(self, ws: Any, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        sender = getattr(ws, "send", None)
        if callable(sender):
            send_result = sender(text)
            if inspect.isawaitable(send_result):
                await cast(Any, send_result)
            return

        sender = getattr(ws, "send_str", None)
        if callable(sender):
            send_result = sender(text)
            if inspect.isawaitable(send_result):
                await cast(Any, send_result)
            return

        raise RuntimeError("websocket object does not support send()")

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self.all_ws_clients:
            return

        dead: list[int] = []
        for connection_id in list(self.all_ws_clients):
            ws = self.connections.get(connection_id)
            if ws is None:
                dead.append(connection_id)
                continue
            try:
                await self.send_json(ws, payload)
            except Exception:
                dead.append(connection_id)

        for connection_id in dead:
            ws = self.connections.pop(connection_id, None)
            if ws is not None:
                self.unregister_socket(ws)

    async def dispatch_device_command(self, device_id: str, command_request: dict[str, Any]) -> dict[str, Any]:
        sockets = self.device_sockets.get(device_id)
        if not sockets:
            return {"ok": False, "statusCode": 404, "message": "Target device is not connected"}

        target_id = next((connection_id for connection_id in sockets if connection_id in self.connections), None)
        if target_id is None:
            return {"ok": False, "statusCode": 409, "message": "Target device connection is not writable"}

        target = self.connections[target_id]
        request_id = f"{int(datetime.now().timestamp() * 1000)}-{uuid.uuid4().hex[:8]}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending_commands[request_id] = future

        command_payload = dict(command_request)
        old_payload = command_payload.get("payload")
        new_payload = old_payload if isinstance(old_payload, dict) else {}

        reserved_fields = {"type", "id", "requestId", "command", "client", "source", "payload"}
        extra_fields = {k: v for k, v in command_payload.items() if k not in reserved_fields}
        for key in extra_fields:
            command_payload.pop(key, None)
        new_payload = {**extra_fields, **new_payload}
        command_payload["payload"] = new_payload

        message = {
            **command_payload,
            "type": "device-command",
            "id": device_id,
            "requestId": request_id,
        }

        try:
            await self.send_json(target, message)
        except Exception:
            self.pending_commands.pop(request_id, None)
            self.unregister_socket(target)
            return {"ok": False, "statusCode": 409, "message": "Target device connection is not writable"}

        try:
            result = await asyncio.wait_for(future, timeout=5)
            return {"ok": True, "payload": result}
        except asyncio.TimeoutError:
            self.pending_commands.pop(request_id, None)
            return {"ok": False, "statusCode": 504, "message": "Target device response timeout"}
