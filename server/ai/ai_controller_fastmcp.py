"""
Standalone FastMCP controller for HyperAutomation.

Run (stdio transport):
    python server/ai/ai_controller_fastmcp.py

This file is intentionally independent from ai_controller.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

MAX_WRITABLE_FILE_SIZE_BYTES = 300 * 1024
MAX_READ_FILE_SIZE_BYTES = int(os.environ.get("AI_MAX_READ_FILE_SIZE_BYTES", str(120 * 1024)))
MAX_READ_CHUNK_LINES = 400

# 获取DEBUG标志 - 默认开启
_AI_MCP_DEBUG_ENV = os.environ.get("AI_MCP_DEBUG", "1").strip()
DEBUG_MCP = _AI_MCP_DEBUG_ENV not in ("0", "false", "False", "FALSE")

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


def _debug_log(event: str, payload: dict[str, Any] | None = None) -> None:
    if not DEBUG_MCP:
        return
    meta = payload or {}
    print(f"[mcp] {event} {meta}", file=sys.stderr, flush=True)


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
    try:
        _debug_log("tool_start", {"name": "list_files", "dir_path": dir_path})
        directory = assert_readable_path(dir_path)
        entries: list[dict[str, Any]] = []
        for item in sorted(directory.iterdir(), key=lambda p: p.name):
            if item.resolve() in RESTRICTED_READ_PATHS:
                continue
            size_bytes = item.stat().st_size if item.is_file() else None
            entries.append(
                {
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "path": str(item.relative_to(PROJECT_ROOT)) if item != PROJECT_ROOT else ".",
                    "size_bytes": size_bytes,
                }
            )
        result = json.dumps(entries, ensure_ascii=False, indent=2)
        _debug_log("tool_end", {"name": "list_files", "count": len(entries)})
        return result
    except Exception as e:
        _debug_log("tool_error", {"name": "list_files", "error": str(e)})
        sys.stderr.flush()
        raise


@mcp.tool(description="Read the full content of a file (path relative to project root).")
def read_file(file_path: str) -> str:
    try:
        _debug_log("tool_start", {"name": "read_file", "file_path": file_path})
        resolved = assert_readable_path(file_path)
        size = resolved.stat().st_size
        if size > MAX_READ_FILE_SIZE_BYTES:
            raise ValueError(
                " ".join(
                    [
                        f"file too large ({size} bytes > {MAX_READ_FILE_SIZE_BYTES} bytes)",
                        "please read a smaller file or use read_file_chunk",
                    ]
                )
            )
        content = resolved.read_text(encoding="utf-8")
        _debug_log("tool_end", {"name": "read_file", "size_bytes": size})
        return content
    except Exception as e:
        _debug_log("tool_error", {"name": "read_file", "file_path": file_path, "error": str(e)})
        sys.stderr.flush()
        raise


@mcp.tool(description="Read a file by line range (1-based, inclusive).")
def read_file_chunk(file_path: str, start_line: int, end_line: int) -> str:
    try:
        _debug_log(
            "tool_start",
            {"name": "read_file_chunk", "file_path": file_path, "start_line": start_line, "end_line": end_line},
        )
        resolved = assert_readable_path(file_path)

        if start_line < 1 or end_line < 1:
            raise ValueError("start_line and end_line must be >= 1")
        if end_line < start_line:
            raise ValueError("end_line must be >= start_line")

        line_count = end_line - start_line + 1
        if line_count > MAX_READ_CHUNK_LINES:
            raise ValueError(
                f"requested too many lines ({line_count} > {MAX_READ_CHUNK_LINES}), please narrow the range"
            )

        lines = resolved.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        if start_line > total:
            return ""

        safe_end = min(end_line, total)
        chunk = lines[start_line - 1 : safe_end]
        result = "\n".join(chunk)
        _debug_log("tool_end", {"name": "read_file_chunk", "lines": len(chunk)})
        return result
    except Exception as e:
        _debug_log("tool_error", {"name": "read_file_chunk", "file_path": file_path, "error": str(e)})
        sys.stderr.flush()
        raise


@mcp.tool(description="Create or overwrite a file, but only inside src/scripts or src/components/dynamic.")
def write_file(file_path: str, content: str) -> str:
    try:
        _debug_log("tool_start", {"name": "write_file", "file_path": file_path})
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

        _debug_log("tool_end", {"name": "write_file", "file_path": file_path})
        return f"OK: written '{file_path}' (security-check: passed)"
    except Exception as e:
        _debug_log("tool_error", {"name": "write_file", "file_path": file_path, "error": str(e)})
        sys.stderr.flush()
        raise


@mcp.tool(description="Delete a file, but only inside src/scripts or src/components/dynamic.")
def delete_file(file_path: str) -> str:
    try:
        _debug_log("tool_start", {"name": "delete_file", "file_path": file_path})
        resolved = assert_writable_path(file_path)
        resolved.unlink()
        _debug_log("tool_end", {"name": "delete_file", "file_path": file_path})
        return f"OK: deleted '{file_path}'"
    except Exception as e:
        _debug_log("tool_error", {"name": "delete_file", "file_path": file_path, "error": str(e)})
        sys.stderr.flush()
        raise


@mcp.tool(description="Rename or move a file, but only inside src/scripts or src/components/dynamic.")
def rename_file(from_path: str, to_path: str) -> str:
    try:
        _debug_log("tool_start", {"name": "rename_file", "from_path": from_path, "to_path": to_path})
        resolved_from = assert_writable_path(from_path)
        resolved_to = assert_writable_path(to_path)
        resolved_to.parent.mkdir(parents=True, exist_ok=True)
        resolved_from.rename(resolved_to)
        _debug_log("tool_end", {"name": "rename_file", "from_path": from_path, "to_path": to_path})
        return f"OK: renamed '{from_path}' -> '{to_path}'"
    except Exception as e:
        _debug_log("tool_error", {"name": "rename_file", "from_path": from_path, "to_path": to_path, "error": str(e)})
        sys.stderr.flush()
        raise


if __name__ == "__main__":
    # 始终打印启动信息到stderr（不受DEBUG_MCP控制）
    startup_msg = {
        "status": "starting",
        "debug_enabled": DEBUG_MCP,
        "debug_env": _AI_MCP_DEBUG_ENV,
        "project_root": str(PROJECT_ROOT),
    }
    print(f"[mcp] startup {startup_msg}", file=sys.stderr, flush=True)
    
    _debug_log("startup", startup_msg)
    try:
        mcp.run(transport="stdio")
    except Exception as e:
        _debug_log("fatal_error", {"exception": str(e), "type": type(e).__name__})
        print(f"[mcp] FATAL ERROR: {e}", file=sys.stderr, flush=True)
        sys.stderr.flush()
        raise
