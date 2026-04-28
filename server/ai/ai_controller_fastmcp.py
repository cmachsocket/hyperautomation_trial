"""
Standalone FastMCP controller for HyperAutomation.

Run (stdio transport):
    python server/ai/ai_controller_fastmcp.py

This file is intentionally independent from ai_controller.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastmcp import FastMCP

MAX_WRITABLE_FILE_SIZE_BYTES = 300 * 1024

DANGEROUS_CODE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b(?:require|import)\s*\(?\s*[\"'](?:node:)?child_process[\"']\s*\)?", re.MULTILINE),
        "forbidden module: child_process",
    ),
    (
        re.compile(r"\b(?:exec|execSync|spawn|spawnSync|fork)\s*\(", re.MULTILINE),
        "forbidden process execution API",
    ),
    (re.compile(r"\b(?:eval|Function)\s*\(", re.MULTILINE), "dynamic code execution is forbidden"),
    (re.compile(r"\bprocess\.exit\s*\(", re.MULTILINE), "process termination is forbidden"),
]


class SecurityValidationError(Exception):
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESTRICTED_READ_PATHS = {
    (PROJECT_ROOT / "local.env").resolve(),
    (PROJECT_ROOT / ".env.production").resolve(),
}

ALLOWED_DIRS = {
    "scripts": (PROJECT_ROOT / "src" / "scripts").resolve(),
    "widgets": (PROJECT_ROOT / "src" / "components" / "dynamic").resolve(),
}


mcp = FastMCP("hyperautomation-ai-controller")


def assert_readable_path(rel_path: str = ".") -> Path:
    candidate = Path(rel_path)
    if candidate.is_absolute():
        raise ValueError(f"Access denied: absolute paths are not permitted ('{rel_path}')")

    resolved = (PROJECT_ROOT / candidate).resolve()
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise ValueError(f"Access denied: '{rel_path}' is outside project root.")

    if resolved in RESTRICTED_READ_PATHS:
        raise ValueError(f"Access denied: '{rel_path}' is a restricted file.")

    return resolved


def assert_writable_path(rel_path: str) -> Path:
    resolved = assert_readable_path(rel_path)
    ok = any(resolved == d or d in resolved.parents for d in ALLOWED_DIRS.values())
    if not ok:
        raise ValueError(
            f"Access denied: '{rel_path}' is outside writable directories (scripts, widgets)."
        )
    return resolved


def validate_written_code_safety(file_path: str, content: str) -> None:
    ext = Path(file_path).suffix.lower()
    if ext not in {".js", ".ts", ".vue"}:
        raise SecurityValidationError("only .js/.ts/.vue are writable")

    size = len(content.encode("utf-8"))
    if size > MAX_WRITABLE_FILE_SIZE_BYTES:
        raise SecurityValidationError(
            f"file too large ({size} bytes > {MAX_WRITABLE_FILE_SIZE_BYTES} bytes)"
        )

    for regex, reason in DANGEROUS_CODE_PATTERNS:
        if regex.search(content):
            raise SecurityValidationError(reason)


@mcp.tool(description="List files in any project directory (read scope is whole project).")
def list_files(dir_path: str = ".") -> str:
    directory = assert_readable_path(dir_path)
    entries: list[dict[str, str]] = []
    for item in sorted(directory.iterdir(), key=lambda p: p.name):
        if item.resolve() in RESTRICTED_READ_PATHS:
            continue
        entries.append(
            {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "path": str(item.relative_to(PROJECT_ROOT)) if item != PROJECT_ROOT else ".",
            }
        )
    return json.dumps(entries, ensure_ascii=False, indent=2)


@mcp.tool(description="Read the full content of a file (path relative to project root).")
def read_file(file_path: str) -> str:
    resolved = assert_readable_path(file_path)
    return resolved.read_text(encoding="utf-8")


@mcp.tool(description="Create or overwrite a file, but only inside src/scripts or src/components/dynamic.")
def write_file(file_path: str, content: str) -> str:
    resolved = assert_writable_path(file_path)
    existed_before = resolved.exists()
    previous_content = ""

    if existed_before:
        previous_content = resolved.read_text(encoding="utf-8")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")

    try:
        validate_written_code_safety(file_path, content)
    except Exception as err:
        if existed_before:
            resolved.write_text(previous_content, encoding="utf-8")
        elif resolved.exists():
            resolved.unlink()
        raise SecurityValidationError(
            f"Security check failed for '{file_path}': {err}. Write has been rolled back."
        ) from err

    return f"OK: written '{file_path}' (security-check: passed)"


@mcp.tool(description="Delete a file, but only inside src/scripts or src/components/dynamic.")
def delete_file(file_path: str) -> str:
    resolved = assert_writable_path(file_path)
    resolved.unlink()
    return f"OK: deleted '{file_path}'"


@mcp.tool(description="Rename or move a file, but only inside src/scripts or src/components/dynamic.")
def rename_file(from_path: str, to_path: str) -> str:
    resolved_from = assert_writable_path(from_path)
    resolved_to = assert_writable_path(to_path)
    resolved_to.parent.mkdir(parents=True, exist_ok=True)
    resolved_from.rename(resolved_to)
    return f"OK: renamed '{from_path}' -> '{to_path}'"


if __name__ == "__main__":
    mcp.run(transport="stdio")
