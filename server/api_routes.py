from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from typing import cast

from quart import Quart, Response, g, request, websocket

from server.ai.ai_controller import setup_ai_routes
from server.coe.api_routes import setup_asset_routes
from server.device_manager import DeviceManager, normalize_id, utc_now_iso
from server.script_runner import ScriptRunner


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _base64url_decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode((encoded + padding).encode("ascii"))


def sign_auth_token(secret: str, username: str, expires_at: int) -> str:
    payload_obj = {"u": username, "exp": expires_at}
    payload_raw = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    payload_b64 = _base64url_encode(payload_raw)
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_auth_token(secret: str, token: str) -> dict[str, Any] | None:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
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


def build_json_response(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        content_type="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS",
            "Cache-Control": "no-store",
        },
    )


def create_app(
    *,
    auth_username: str,
    auth_password: str,
    auth_token_secret: str,
    auth_token_expire_seconds: int,
    device_manager: DeviceManager,
    script_runner: ScriptRunner,
) -> Quart:
    app = Quart(__name__)

    @app.after_request
    async def add_cors_headers(response: Response) -> Response:
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type, Authorization")
        response.headers.setdefault("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
        return response

    @app.before_request
    async def auth_middleware():
        if request.method == "OPTIONS":
            return None

        if not request.path.startswith("/api/"):
            return None

        if request.path == "/api/auth/login":
            return None

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return build_json_response({"message": "Unauthorized: missing bearer token"}, status=401)

        token = auth_header.removeprefix("Bearer ").strip()
        claims = verify_auth_token(auth_token_secret, token)
        if not claims:
            return build_json_response({"message": "Unauthorized: invalid or expired token"}, status=401)

        g.auth_claims = claims
        return None

    @app.route("/api/auth/login", methods=["POST"])
    async def auth_login() -> Response:
        try:
            payload = await request.get_json()
        except Exception:
            return build_json_response({"message": "Invalid JSON"}, status=400)

        if not isinstance(payload, dict):
            return build_json_response({"message": "Payload must be a JSON object"}, status=400)

        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return build_json_response({"message": "username and password are required"}, status=400)

        if username.strip() != auth_username or password != auth_password:
            return build_json_response({"message": "Invalid username or password"}, status=401)

        now_ts = int(datetime.now().timestamp())
        expires_at = now_ts + auth_token_expire_seconds
        token = sign_auth_token(auth_token_secret, auth_username, expires_at)
        return build_json_response(
            {
                "token": token,
                "tokenType": "Bearer",
                "expiresAt": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
                "username": auth_username,
            }
        )

    async def get_merged_map(device_id: str | None = None) -> Response:
        normalized_id = normalize_id(device_id or request.args.get("id"))
        if normalized_id is None or not normalized_id.strip():
            return build_json_response({"message": "id is required (path param)"}, status=400)

        entry = device_manager.merged_by_id.get(normalized_id)
        if entry is None:
            return build_json_response({"message": f"No map entry found for id: {normalized_id}"}, status=404)

        return build_json_response({"id": normalized_id, **entry, "updatedAt": utc_now_iso()})

    @app.route("/api/merged-map/<device_id>", methods=["GET"])
    async def get_merged_map_path(device_id: str) -> Response:
        return await get_merged_map(device_id)

    @app.route("/api/merged-map", methods=["GET"])
    async def get_merged_map_query() -> Response:
        return await get_merged_map(None)

    @app.route("/api/scripts", methods=["GET"])
    async def get_scripts() -> Response:
        return build_json_response({"scripts": script_runner.list_scripts(), "updatedAt": utc_now_iso()})

    @app.route("/api/scripts/start", methods=["POST"])
    async def scripts_start() -> Response:
        try:
            payload = await request.get_json()
        except Exception:
            return build_json_response({"message": "Invalid JSON"}, status=400)

        script_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(script_id, str) or not script_id.strip():
            return build_json_response({"message": "Payload must include script id (string)"}, status=400)

        result = script_runner.start(script_id)
        if not result.get("ok"):
            return build_json_response(
                {"message": result.get("message", "Start failed")}, status=int(result.get("statusCode", 400))
            )

        event = {
            "type": "script-started",
            "script": result.get("script"),
            "alreadyRunning": bool(result.get("alreadyRunning")),
            "updatedAt": utc_now_iso(),
        }
        await device_manager.broadcast(event)
        return build_json_response(event)

    @app.route("/api/scripts/stop", methods=["POST"])
    async def scripts_stop() -> Response:
        try:
            payload = await request.get_json()
        except Exception:
            return build_json_response({"message": "Invalid JSON"}, status=400)

        script_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(script_id, str) or not script_id.strip():
            return build_json_response({"message": "Payload must include script id (string)"}, status=400)

        result = script_runner.stop(script_id)
        if not result.get("ok"):
            return build_json_response(
                {"message": result.get("message", "Stop failed")}, status=int(result.get("statusCode", 400))
            )

        event = {"type": "script-stopped", "script": result.get("script"), "updatedAt": utc_now_iso()}
        await device_manager.broadcast(event)
        return build_json_response(event)

    @app.route("/api/device/command", methods=["POST"])
    async def device_command() -> Response:
        try:
            payload = await request.get_json()
        except Exception:
            return build_json_response({"message": "Invalid JSON"}, status=400)

        if not isinstance(payload, dict):
            return build_json_response({"message": "Payload must be a JSON object"}, status=400)

        device_id = normalize_id(payload.get("id"))
        command = payload.get("command")

        if device_id is None:
            return build_json_response({"message": "Payload must include id (string | number)"}, status=400)
        if not isinstance(command, str) or not command:
            return build_json_response({"message": "Payload must include command (string)"}, status=400)

        command_payload = {k: v for k, v in payload.items() if k != "id"}
        command_payload.setdefault("source", "api-command")

        result = await device_manager.dispatch_device_command(device_id, command_payload)

        if not result.get("ok"):
            return build_json_response(
                {"message": result.get("message", "Command failed")}, status=int(result.get("statusCode", 400))
            )

        return build_json_response(result["payload"])

    @app.route("/api/device/state", methods=["POST"])
    async def device_state() -> Response:
        try:
            payload = await request.get_json()
        except Exception:
            return build_json_response({"message": "Invalid JSON"}, status=400)

        if not isinstance(payload, dict):
            return build_json_response({"message": "Payload must be a JSON object"}, status=400)

        device_id = normalize_id(payload.get("id"))
        if device_id is None:
            return build_json_response({"message": "Payload must include id (string | number)"}, status=400)

        command_payload = {k: v for k, v in payload.items() if k not in {"id", "action"}}
        command_payload["command"] = "toggle" if payload.get("action") == "toggle" else "set-switch"
        old_payload = command_payload.get("payload")
        command_payload["payload"] = dict(old_payload) if isinstance(old_payload, dict) else {}
        command_payload.setdefault("source", "api-device-state")

        result = await device_manager.dispatch_device_command(device_id, command_payload)

        if not result.get("ok"):
            return build_json_response(
                {"message": result.get("message", "Command failed")}, status=int(result.get("statusCode", 400))
            )

        return build_json_response(result["payload"])

    @app.route("/api/seed-sample", methods=["POST"])
    async def seed_sample() -> Response:
        sample = {
            "id": "demo-switch-1",
            "payload": {"switchOn": False},
            "source": "server-seed",
            "updatedAt": utc_now_iso(),
        }
        updated = device_manager.set_by_id(sample)
        event = {
            "type": "state-updated",
            "id": sample["id"],
            "updated": updated,
            "updatedAt": sample["updatedAt"],
        }
        await device_manager.broadcast(event)
        return build_json_response(event)

    @app.websocket("/")
    async def ws_handler() -> None:
        connection = cast(Any, websocket)._get_current_object()
        device_manager.register_connection(connection)
        await device_manager.send_json(connection, {"type": "connected", "message": "WebSocket server ready"})

        try:
            while True:
                try:
                    message_data = await connection.receive()
                except Exception:
                    break

                if not message_data:
                    continue

                try:
                    payload = json.loads(message_data)
                except json.JSONDecodeError:
                    await device_manager.send_json(connection, {"type": "error", "message": "Invalid JSON"})
                    continue

                if not isinstance(payload, dict):
                    await device_manager.send_json(connection, {"type": "error", "message": "Payload must be a JSON object"})
                    continue

                device_id = normalize_id(payload.get("id"))
                if device_id is None:
                    await device_manager.send_json(
                        connection,
                        {"type": "error", "message": "Payload must include id (string | number)"},
                    )
                    continue

                device_manager.register_socket_for_device(connection, device_id)

                if payload.get("type") == "device-state-report":
                    report_payload = dict(payload)
                    report_payload["id"] = device_id
                    report_payload.setdefault("updatedAt", utc_now_iso())
                    updated = device_manager.set_by_id(report_payload)

                    event = {
                        "type": "state-updated",
                        "id": device_id,
                        "updated": updated,
                        "updatedAt": report_payload["updatedAt"],
                    }
                    await device_manager.broadcast(event)

                    request_id = payload.get("requestId")
                    if isinstance(request_id, str) and request_id in device_manager.pending_commands:
                        future = device_manager.pending_commands.pop(request_id)
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

                updated = device_manager.set_by_id(payload)
                event = {
                    "type": "state-updated",
                    "id": device_id,
                    "updated": updated,
                    "updatedAt": utc_now_iso(),
                }
                await device_manager.broadcast(event)

                await device_manager.send_json(
                    connection,
                    {
                        "type": "ack",
                        "id": device_id,
                        "client": updated.get("client"),
                        "seq": updated.get("seq"),
                        "status": updated.get("status"),
                        "payload": updated.get("payload") if isinstance(updated.get("payload"), dict) else {},
                    },
                )
        finally:
            device_manager.unregister_socket(connection)

    @app.route("/<path:tail>", methods=["OPTIONS"])
    async def options_handler(tail: str) -> Response:
        return build_json_response({}, status=204)

    @app.route("/", methods=["OPTIONS"])
    async def root_options() -> Response:
        return build_json_response({}, status=204)

    @app.errorhandler(404)
    async def not_found(_: Any) -> Response:
        return build_json_response({"error": "Not found"}, status=404)

    app.config["device_manager"] = device_manager
    app.config["script_runner"] = script_runner
    setup_ai_routes(app, prefix="/api/ai")
    setup_asset_routes(app)

    return app
