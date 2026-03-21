from __future__ import annotations

import json
import os
from pathlib import Path

from aiohttp import web

from api_routes import create_app
from device_manager import DeviceManager
from script_runner import ScriptRunner


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


def get_app_version() -> str:
    return CURRENT_APP_VERSION


def set_app_version(next_version: str) -> None:
    global CURRENT_APP_VERSION
    CURRENT_APP_VERSION = next_version


device_manager = DeviceManager()
script_runner = ScriptRunner()


if __name__ == "__main__":
    print(f"WS server started on ws://localhost:{PORT}")
    print(f"Map API ready at http://localhost:{PORT}/api/merged-map/{{id}}")
    app = create_app(
        auth_username=AUTH_USERNAME,
        auth_password=AUTH_PASSWORD,
        auth_token_secret=AUTH_TOKEN_SECRET,
        auth_token_expire_seconds=AUTH_TOKEN_EXPIRE_SECONDS,
        get_app_version=get_app_version,
        set_app_version=set_app_version,
        device_manager=device_manager,
        script_runner=script_runner,
    )
    web.run_app(app, port=PORT)
