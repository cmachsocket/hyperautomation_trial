from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from aiohttp import web


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_id(value: Any) -> str | None:
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


class DeviceManager:
    def __init__(self) -> None:
        self.merged_by_id: dict[str, dict[str, Any]] = {}
        self.device_sockets: dict[str, set[web.WebSocketResponse]] = {}
        self.socket_devices: dict[web.WebSocketResponse, set[str]] = {}
        self.pending_commands: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.all_ws_clients: set[web.WebSocketResponse] = set()

    def set_by_id(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_id = normalize_id(payload.get("id"))
        if normalized_id is None:
            raise ValueError("payload must include id")

        stored = {k: v for k, v in payload.items() if k != "id"}
        self.merged_by_id[normalized_id] = stored
        return stored

    def register_socket_for_device(self, ws: web.WebSocketResponse, device_id: str) -> None:
        self.device_sockets.setdefault(device_id, set()).add(ws)
        self.socket_devices.setdefault(ws, set()).add(device_id)

    def unregister_socket(self, ws: web.WebSocketResponse) -> None:
        ids = self.socket_devices.pop(ws, set())
        for device_id in ids:
            sockets = self.device_sockets.get(device_id)
            if not sockets:
                continue
            sockets.discard(ws)
            if not sockets:
                self.device_sockets.pop(device_id, None)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self.all_ws_clients:
            return

        text = json.dumps(payload, ensure_ascii=False)
        dead: list[web.WebSocketResponse] = []

        for client in self.all_ws_clients:
            if client.closed:
                dead.append(client)
                continue
            try:
                await client.send_str(text)
            except Exception:
                dead.append(client)

        for client in dead:
            self.all_ws_clients.discard(client)
            self.unregister_socket(client)

    async def dispatch_device_command(self, device_id: str, command_request: dict[str, Any]) -> dict[str, Any]:
        sockets = self.device_sockets.get(device_id)
        if not sockets:
            return {"ok": False, "statusCode": 404, "message": "Target device is not connected"}

        target = next((ws for ws in sockets if not ws.closed), None)
        if not target:
            return {"ok": False, "statusCode": 409, "message": "Target device connection is not writable"}

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

        await target.send_str(json.dumps(message, ensure_ascii=False))

        try:
            result = await asyncio.wait_for(future, timeout=5)
            return {"ok": True, "payload": result}
        except asyncio.TimeoutError:
            self.pending_commands.pop(request_id, None)
            return {"ok": False, "statusCode": 504, "message": "Target device response timeout"}
