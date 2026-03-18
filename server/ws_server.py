from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from aiohttp import WSMsgType, web

from script_controller import ScriptController


PORT = int(os.getenv("WS_PORT", "8081"))

merged_by_id: dict[str, dict[str, Any]] = {}
device_sockets: dict[str, set[web.WebSocketResponse]] = {}
socket_devices: dict[web.WebSocketResponse, set[str]] = {}
pending_commands: dict[str, asyncio.Future[dict[str, Any]]] = {}
all_ws_clients: set[web.WebSocketResponse] = set()

script_controller = ScriptController()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_id(value: Any) -> str | None:
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def set_by_id(payload: dict[str, Any]) -> dict[str, Any]:
    normalized_id = normalize_id(payload.get("id"))
    if normalized_id is None:
        raise ValueError("payload must include id")

    stored = {k: v for k, v in payload.items() if k != "id"}
    merged_by_id[normalized_id] = stored
    return stored


def register_socket_for_device(ws: web.WebSocketResponse, device_id: str) -> None:
    device_sockets.setdefault(device_id, set()).add(ws)
    socket_devices.setdefault(ws, set()).add(device_id)


def unregister_socket(ws: web.WebSocketResponse) -> None:
    ids = socket_devices.pop(ws, set())
    for device_id in ids:
        sockets = device_sockets.get(device_id)
        if not sockets:
            continue
        sockets.discard(ws)
        if not sockets:
            device_sockets.pop(device_id, None)


def json_response(payload: Any, status: int = 200) -> web.Response:
    return web.json_response(
        payload,
        status=status,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Cache-Control": "no-store",
        },
    )


async def broadcast(payload: dict[str, Any]) -> None:
    if not all_ws_clients:
        return

    text = json.dumps(payload, ensure_ascii=False)
    dead: list[web.WebSocketResponse] = []

    for client in all_ws_clients:
        if client.closed:
            dead.append(client)
            continue
        try:
            await client.send_str(text)
        except Exception:
            dead.append(client)

    for client in dead:
        all_ws_clients.discard(client)
        unregister_socket(client)


async def dispatch_device_command(device_id: str, command_request: dict[str, Any]) -> dict[str, Any]:
    sockets = device_sockets.get(device_id)
    if not sockets:
        return {"ok": False, "statusCode": 404, "message": "Target device is not connected"}

    target = next((ws for ws in sockets if not ws.closed), None)
    if not target:
        return {"ok": False, "statusCode": 409, "message": "Target device connection is not writable"}

    request_id = f"{int(datetime.now().timestamp() * 1000)}-{uuid.uuid4().hex[:8]}"
    future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    pending_commands[request_id] = future

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
        pending_commands.pop(request_id, None)
        return {"ok": False, "statusCode": 504, "message": "Target device response timeout"}


async def options_handler(_: web.Request) -> web.Response:
    return json_response({}, status=204)


async def get_merged_map(request: web.Request) -> web.Response:
    device_id = request.match_info.get("id") or request.query.get("id")
    normalized_id = normalize_id(device_id)

    if normalized_id is None or not normalized_id.strip():
        return json_response({"message": "id is required (path param or query param)"}, status=400)

    entry = merged_by_id.get(normalized_id)
    if entry is None:
        return json_response({"message": f"No map entry found for id: {normalized_id}"}, status=404)

    return json_response({"id": normalized_id, **entry, "updatedAt": utc_now_iso()})


async def get_scripts(_: web.Request) -> web.Response:
    return json_response({"scripts": script_controller.list_scripts(), "updatedAt": utc_now_iso()})


async def scripts_start(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"message": "Invalid JSON"}, status=400)

    script_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(script_id, str) or not script_id.strip():
        return json_response({"message": "Payload must include script id (string)"}, status=400)

    result = script_controller.start_script_by_id(script_id)
    if not result.get("ok"):
        return json_response({"message": result.get("message", "Start failed")}, status=int(result.get("statusCode", 400)))

    event = {
        "type": "script-started",
        "script": result.get("script"),
        "alreadyRunning": bool(result.get("alreadyRunning")),
        "updatedAt": utc_now_iso(),
    }
    await broadcast(event)
    return json_response(event)


async def scripts_stop(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"message": "Invalid JSON"}, status=400)

    script_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(script_id, str) or not script_id.strip():
        return json_response({"message": "Payload must include script id (string)"}, status=400)

    result = script_controller.stop_script_by_id(script_id)
    if not result.get("ok"):
        return json_response({"message": result.get("message", "Stop failed")}, status=int(result.get("statusCode", 400)))

    event = {"type": "script-stopped", "script": result.get("script"), "updatedAt": utc_now_iso()}
    await broadcast(event)
    return json_response(event)


async def device_command(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"message": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return json_response({"message": "Payload must be a JSON object"}, status=400)

    device_id = normalize_id(payload.get("id"))
    command = payload.get("command")

    if device_id is None:
        return json_response({"message": "Payload must include id (string | number)"}, status=400)
    if not isinstance(command, str) or not command:
        return json_response({"message": "Payload must include command (string)"}, status=400)

    command_payload = {k: v for k, v in payload.items() if k != "id"}
    command_payload.setdefault("source", "api-command")

    result = await dispatch_device_command(device_id, command_payload)

    if not result.get("ok"):
        return json_response({"message": result.get("message", "Command failed")}, status=int(result.get("statusCode", 400)))

    return json_response(result["payload"])


async def device_state(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"message": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return json_response({"message": "Payload must be a JSON object"}, status=400)

    device_id = normalize_id(payload.get("id"))
    if device_id is None:
        return json_response({"message": "Payload must include id (string | number)"}, status=400)

    command_payload = {k: v for k, v in payload.items() if k not in {"id", "action"}}
    command_payload["command"] = "toggle" if payload.get("action") == "toggle" else "set-switch"
    old_payload = command_payload.get("payload")
    command_payload["payload"] = dict(old_payload) if isinstance(old_payload, dict) else {}
    command_payload.setdefault("source", "api-device-state")

    result = await dispatch_device_command(device_id, command_payload)

    if not result.get("ok"):
        return json_response({"message": result.get("message", "Command failed")}, status=int(result.get("statusCode", 400)))

    return json_response(result["payload"])


async def seed_sample(_: web.Request) -> web.Response:
    sample = {
        "id": "demo-switch-1",
        "payload": {"switchOn": False},
        "source": "server-seed",
        "updatedAt": utc_now_iso(),
    }
    updated = set_by_id(sample)
    event = {
        "type": "state-updated",
        "id": sample["id"],
        "updated": updated,
        "updatedAt": sample["updatedAt"],
    }
    await broadcast(event)
    return json_response(event)


async def ws_handler(request: web.Request) -> web.StreamResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    all_ws_clients.add(ws)

    await ws.send_json({"type": "connected", "message": "WebSocket server ready"})

    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue

            try:
                payload = json.loads(message.data)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            if not isinstance(payload, dict):
                await ws.send_json({"type": "error", "message": "Payload must be a JSON object"})
                continue

            device_id = normalize_id(payload.get("id"))
            if device_id is None:
                await ws.send_json({"type": "error", "message": "Payload must include id (string | number)"})
                continue

            register_socket_for_device(ws, device_id)

            if payload.get("type") == "device-state-report":
                report_payload = dict(payload)
                report_payload["id"] = device_id
                report_payload.setdefault("updatedAt", utc_now_iso())
                updated = set_by_id(report_payload)

                event = {
                    "type": "state-updated",
                    "id": device_id,
                    "updated": updated,
                    "updatedAt": report_payload["updatedAt"],
                }
                await broadcast(event)

                request_id = payload.get("requestId")
                if isinstance(request_id, str) and request_id in pending_commands:
                    future = pending_commands.pop(request_id)
                    if not future.done():
                        future.set_result(
                            {
                                "type": "device-command-result",
                                "id": device_id,
                                "updated": updated,
                                "updatedAt": event["updatedAt"],
                                "requestId": request_id,
                            }
                        )
                continue

            updated = set_by_id(payload)
            event = {
                "type": "state-updated",
                "id": device_id,
                "updated": updated,
                "updatedAt": utc_now_iso(),
            }
            await broadcast(event)

            await ws.send_json({"type": "ack", "id": device_id, "updated": updated})
    finally:
        all_ws_clients.discard(ws)
        unregister_socket(ws)

    return ws


def create_app() -> web.Application:
    app = web.Application()

    app.router.add_route("OPTIONS", "/{tail:.*}", options_handler)
    app.router.add_get("/", ws_handler)
    app.router.add_get("/api/merged-map", get_merged_map)
    app.router.add_get("/api/merged-map/{id}", get_merged_map)
    app.router.add_get("/api/scripts", get_scripts)
    app.router.add_post("/api/scripts/start", scripts_start)
    app.router.add_post("/api/scripts/stop", scripts_stop)
    app.router.add_post("/api/device/command", device_command)
    app.router.add_post("/api/device/state", device_state)
    app.router.add_post("/api/seed-sample", seed_sample)

    return app


if __name__ == "__main__":
    print(f"WS server started on ws://localhost:{PORT}")
    print(f"Map API ready at http://localhost:{PORT}/api/merged-map/{{id}} or /api/merged-map?id={{id}}")
    web.run_app(create_app(), port=PORT)
