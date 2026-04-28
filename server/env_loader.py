from __future__ import annotations

import os
from pathlib import Path


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()

    if not key:
        return None

    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()

    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]

    return key, value


def load_env_files(project_root: Path) -> None:
    for name in (".env", "local.env"):
        path = project_root / name
        if not path.exists() or not path.is_file():
            continue

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(raw_line)
            if not parsed:
                continue
            key, value = parsed
            # Allow later files to override earlier ones so local.env can win over .env.
            if key in os.environ and name != "local.env":
                continue
            os.environ[key] = value
