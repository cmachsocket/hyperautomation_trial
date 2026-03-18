from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "src" / "scripts"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ScriptDef:
    id: str
    name: str
    file_path: Path


def to_script_id(file_path: Path) -> str:
    return file_path.stem.replace("_", "-")


def to_script_name(file_path: Path) -> str:
    tokens = file_path.stem.replace("-", "_").split("_")
    return " ".join(token.capitalize() for token in tokens if token)


def discover_script_defs(scripts_dir: Path = SCRIPTS_DIR) -> list[ScriptDef]:
    if not scripts_dir.exists() or not scripts_dir.is_dir():
        return []

    script_files = sorted(path for path in scripts_dir.glob("*.js") if path.is_file())
    return [
        ScriptDef(
            id=to_script_id(script_file),
            name=to_script_name(script_file),
            file_path=script_file,
        )
        for script_file in script_files
    ]


class ScriptController:
    def __init__(self) -> None:
        self._defs: dict[str, ScriptDef] = {}
        self._ordered_ids: list[str] = []
        self._proc_map: dict[str, subprocess.Popen[str]] = {}
        self._status_map: dict[str, dict[str, Any]] = {}
        self._sync_defs()

    def _base_status(self, script_def: ScriptDef) -> dict[str, Any]:
        return {
            "id": script_def.id,
            "name": script_def.name,
            "running": False,
            "pid": None,
            "lastStartedAt": None,
            "lastExitedAt": None,
            "lastExitCode": None,
            "stopRequestedAt": None,
        }

    def _sync_defs(self) -> None:
        script_defs = discover_script_defs()
        current_ids = {item.id for item in script_defs}

        self._defs = {item.id: item for item in script_defs}
        self._ordered_ids = [item.id for item in script_defs]

        for item in script_defs:
            prev = self._status_map.get(item.id)
            if prev is None:
                self._status_map[item.id] = self._base_status(item)
                continue

            self._status_map[item.id] = {
                **prev,
                "id": item.id,
                "name": item.name,
            }

        # Remove statuses for scripts that no longer exist and are not running.
        for script_id in list(self._status_map.keys()):
            if script_id in current_ids:
                continue

            proc = self._proc_map.get(script_id)
            if proc and proc.poll() is None:
                continue

            self._status_map.pop(script_id, None)
            self._proc_map.pop(script_id, None)

    def list_scripts(self) -> list[dict[str, Any]]:
        self._sync_defs()
        self._refresh_states()
        return [dict(self._status_map[script_id]) for script_id in self._ordered_ids]

    def start_script_by_id(self, script_id: str) -> dict[str, Any]:
        self._sync_defs()
        self._refresh_states()
        script_def = self._defs.get(script_id)
        if not script_def:
            return {"ok": False, "statusCode": 404, "message": "Script not found"}

        status = self._status_map[script_id]
        process = self._proc_map.get(script_id)
        if process and process.poll() is None and status.get("running"):
            return {"ok": True, "alreadyRunning": True, "script": dict(status)}

        node_bin = os.getenv("NODE_BIN", "node")
        proc = subprocess.Popen(
            [node_bin, str(script_def.file_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        started_at = utc_now_iso()
        next_status = {
            **status,
            "running": True,
            "pid": proc.pid,
            "lastStartedAt": started_at,
            "stopRequestedAt": None,
        }

        self._status_map[script_id] = next_status
        self._proc_map[script_id] = proc

        return {"ok": True, "alreadyRunning": False, "script": dict(next_status)}

    def stop_script_by_id(self, script_id: str) -> dict[str, Any]:
        self._sync_defs()
        self._refresh_states()
        script_def = self._defs.get(script_id)
        if not script_def:
            return {"ok": False, "statusCode": 404, "message": "Script not found"}

        proc = self._proc_map.get(script_id)
        status = self._status_map[script_id]

        if not proc or proc.poll() is not None or not status.get("running"):
            return {"ok": False, "statusCode": 409, "message": "Script is not running"}

        if os.name == "nt":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)

        next_status = {**status, "stopRequestedAt": utc_now_iso()}
        self._status_map[script_id] = next_status

        return {"ok": True, "script": dict(next_status)}

    def _refresh_states(self) -> None:
        for script_id, proc in list(self._proc_map.items()):
            code = proc.poll()
            if code is None:
                continue

            prev = self._status_map[script_id]
            self._status_map[script_id] = {
                **prev,
                "running": False,
                "pid": None,
                "lastExitedAt": utc_now_iso(),
                "lastExitCode": code,
            }
            self._proc_map.pop(script_id, None)
