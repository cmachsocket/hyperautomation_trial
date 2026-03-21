from __future__ import annotations

from typing import Any

from script_controller import ScriptController


class ScriptRunner:
    def __init__(self) -> None:
        self.controller = ScriptController()

    def list_scripts(self) -> list[dict[str, Any]]:
        return self.controller.list_scripts()

    def start(self, script_id: str) -> dict[str, Any]:
        return self.controller.start_script_by_id(script_id)

    def stop(self, script_id: str) -> dict[str, Any]:
        return self.controller.stop_script_by_id(script_id)
