from __future__ import annotations

import os
from pathlib import Path

from aiohttp import web

from server.api_routes import create_app
from server.device_manager import DeviceManager
from server.env_loader import load_env_files
from server.script_runner import ScriptRunner


ROOT_DIR = Path(__file__).resolve().parents[1]
load_env_files(ROOT_DIR)

PORT = int(os.getenv("WS_PORT", "8081"))
AUTH_USERNAME = os.getenv("APP_LOGIN_USERNAME", "admin").strip() or "admin"
AUTH_PASSWORD = os.getenv("APP_LOGIN_PASSWORD", "123456")
AUTH_TOKEN_SECRET = os.getenv("AUTH_TOKEN_SECRET", "hyperautomation-dev-secret")
AUTH_TOKEN_EXPIRE_SECONDS = int(os.getenv("AUTH_TOKEN_EXPIRE_SECONDS", "43200"))


device_manager = DeviceManager()
script_runner = ScriptRunner()


if __name__ == "__main__":
    print(f"WS server started on ws://localhost:{PORT}")
    print(f"Map API ready at http://localhost:{PORT}/api/merged-map/{{id}}")
    print(f"AI chat ready at http://localhost:{PORT}/api/ai/chat")
    app = create_app(
        auth_username=AUTH_USERNAME,
        auth_password=AUTH_PASSWORD,
        auth_token_secret=AUTH_TOKEN_SECRET,
        auth_token_expire_seconds=AUTH_TOKEN_EXPIRE_SECONDS,
        device_manager=device_manager,
        script_runner=script_runner,
    )
    web.run_app(app, port=PORT)
