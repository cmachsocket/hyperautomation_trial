from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from aiohttp import WSMsgType, web

from script_controller import ScriptController


PORT = int(os.getenv("WS_PORT", "8081"))
ROOT_DIR = Path(__file__).resolve().parents[1]
AUTH_USERNAME = os.getenv("APP_LOGIN_USERNAME", "admin").strip() or "admin"
AUTH_PASSWORD = os.getenv("APP_LOGIN_PASSWORD", "123456")
AUTH_TOKEN_SECRET = os.getenv("AUTH_TOKEN_SECRET", "hyperautomation-dev-secret")
AUTH_TOKEN_EXPIRE_SECONDS = int(os.getenv("AUTH_TOKEN_EXPIRE_SECONDS", "43200"))


def load_default_app_version() -> str:
    package_json_path = ROOT_DIR / "package.json"
    try:
        payload = json.loads(package_json_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    version = payload.get("version") if isinstance(payload, dict) else None
    return version.strip() if isinstance(version, str) else ""


CURRENT_APP_VERSION = os.getenv("APP_VERSION", "").strip() or load_default_app_version()

merged_by_id: dict[str, dict[str, Any]] = {}
device_sockets: dict[str, set[web.WebSocketResponse]] = {}
socket_devices: dict[web.WebSocketResponse, set[str]] = {}
pending_commands: dict[str, asyncio.Future[dict[str, Any]]] = {}
all_ws_clients: set[web.WebSocketResponse] = set()

script_controller = ScriptController()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _base64url_decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode((encoded + padding).encode("ascii"))


def sign_auth_token(username: str, expires_at: int) -> str:
    payload_obj = {"u": username, "exp": expires_at}
    payload_raw = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    payload_b64 = _base64url_encode(payload_raw)
    signature = hmac.new(AUTH_TOKEN_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_auth_token(token: str) -> dict[str, Any] | None:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(
        AUTH_TOKEN_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        payload = json.loads(_base64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    username = payload.get("u")
    expires_at = payload.get("exp")
    if not isinstance(username, str) or not isinstance(expires_at, int):
        return None
    if expires_at <= int(datetime.now().timestamp()):
        return None
    return payload


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
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
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


async def auth_login(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return json_response({"message": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return json_response({"message": "Payload must be a JSON object"}, status=400)

    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return json_response({"message": "username and password are required"}, status=400)

    if username.strip() != AUTH_USERNAME or password != AUTH_PASSWORD:
        return json_response({"message": "Invalid username or password"}, status=401)

    now_ts = int(datetime.now().timestamp())
    expires_at = now_ts + AUTH_TOKEN_EXPIRE_SECONDS
    token = sign_auth_token(AUTH_USERNAME, expires_at)
    return json_response(
        {
            "token": token,
            "tokenType": "Bearer",
            "expiresAt": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "username": AUTH_USERNAME,
        }
    )


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return await handler(request)

    if not request.path.startswith("/api/"):
        return await handler(request)

    if request.path == "/api/auth/login":
        return await handler(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return json_response({"message": "Unauthorized: missing bearer token"}, status=401)

    token = auth_header.removeprefix("Bearer ").strip()
    claims = verify_auth_token(token)
    if not claims:
        return json_response({"message": "Unauthorized: invalid or expired token"}, status=401)

    request["auth_claims"] = claims
    return await handler(request)


async def get_merged_map(request: web.Request) -> web.Response:
    device_id = request.match_info.get("id")
    normalized_id = normalize_id(device_id)

    if normalized_id is None or not normalized_id.strip():
        return json_response({"message": "id is required (path param)"}, status=400)

    entry = merged_by_id.get(normalized_id)
    if entry is None:
        return json_response({"message": f"No map entry found for id: {normalized_id}"}, status=404)

    return json_response({"id": normalized_id, **entry, "updatedAt": utc_now_iso()})


async def get_scripts(_: web.Request) -> web.Response:
    return json_response({"scripts": script_controller.list_scripts(), "updatedAt": utc_now_iso()})


async def get_app_version(_: web.Request) -> web.Response:
    return json_response({"version": CURRENT_APP_VERSION, "updatedAt": utc_now_iso()})


async def publish_app_version(request: web.Request) -> web.Response:
    global CURRENT_APP_VERSION

    payload: dict[str, Any] = {}
    if request.can_read_body:
        try:
            maybe_payload = await request.json()
            if isinstance(maybe_payload, dict):
                payload = maybe_payload
        except Exception:
            return json_response({"message": "Invalid JSON"}, status=400)

    requested_version = payload.get("version") if isinstance(payload, dict) else None
    if requested_version is not None and not isinstance(requested_version, str):
        return json_response({"message": "version must be a string"}, status=400)

    next_version = (requested_version or "").strip() or f"release-{int(datetime.now().timestamp())}"
    CURRENT_APP_VERSION = next_version

    event = {
        "type": "app-version-published",
        "version": CURRENT_APP_VERSION,
        "updatedAt": utc_now_iso(),
    }
    await broadcast(event)
    return json_response(event)


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

            await ws.send_json(
                {
                    "type": "ack",
                    "id": device_id,
                    "client": updated.get("client"),
                    "seq": updated.get("seq"),
                    "status": updated.get("status"),
                    "payload": updated.get("payload") if isinstance(updated.get("payload"), dict) else {},
                }
            )
    finally:
        all_ws_clients.discard(ws)
        unregister_socket(ws)

    return ws


def create_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])

    app.router.add_route("OPTIONS", "/{tail:.*}", options_handler)
    app.router.add_get("/", ws_handler)
    app.router.add_post("/api/auth/login", auth_login)
    app.router.add_get("/api/merged-map/{id}", get_merged_map)
    app.router.add_get("/api/app-version", get_app_version)
    app.router.add_post("/api/app-version/publish", publish_app_version)
    app.router.add_get("/api/scripts", get_scripts)
    app.router.add_post("/api/scripts/start", scripts_start)
    app.router.add_post("/api/scripts/stop", scripts_stop)
    app.router.add_post("/api/device/command", device_command)
    app.router.add_post("/api/device/state", device_state)
    app.router.add_post("/api/seed-sample", seed_sample)

    return app


if __name__ == "__main__":
    print(f"WS server started on ws://localhost:{PORT}")
    print(f"Map API ready at http://localhost:{PORT}/api/merged-map/{{id}}")
    web.run_app(create_app(), port=PORT)
