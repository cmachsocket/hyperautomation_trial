"""
MCP Server using improved stdio transport with proper buffering and error handling.

Run:
    python server/ai/ai_controller_sse.py

This is a rewrite with better robustness for stdio-based MCP communication.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# ============================================================================
# 配置
# ============================================================================

MAX_WRITABLE_FILE_SIZE_BYTES = 300 * 1024
MAX_READ_FILE_SIZE_BYTES = int(
    os.environ.get("AI_MAX_READ_FILE_SIZE_BYTES", str(120 * 1024))
)
MAX_READ_CHUNK_LINES = 400

# DEBUG 标志
_AI_MCP_DEBUG_ENV = os.environ.get("AI_MCP_DEBUG", "1").strip()
DEBUG_MCP = _AI_MCP_DEBUG_ENV not in ("0", "false", "False", "FALSE")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 日志配置 - 使用标准logging库，输出到stderr避免干扰MCP协议
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MCP else logging.INFO,
    format="[mcp] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("mcp")

# 禁用其他库的日志噪音
logging.getLogger("fastmcp").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

DANGEROUS_CODE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:require|import)\s*\(?\s*[\"'](?:node:)?child_process[\"']\s*\)?",
            re.MULTILINE,
        ),
        "forbidden module: child_process",
    ),
    (
        re.compile(
            r"\b(?:exec|execSync|spawn|spawnSync|fork)\s*\(",
            re.MULTILINE,
        ),
        "forbidden process execution API",
    ),
    (re.compile(r"\b(?:eval|Function)\s*\(", re.MULTILINE), "dynamic code execution is forbidden"),
    (re.compile(r"\bprocess\.exit\s*\(", re.MULTILINE), "process termination is forbidden"),
]


class SecurityValidationError(Exception):
    pass


RESTRICTED_READ_PATHS = {
    (PROJECT_ROOT / "local.env").resolve(),
    (PROJECT_ROOT / ".env.production").resolve(),
}

ALLOWED_DIRS = {
    "scripts": (PROJECT_ROOT / "src" / "scripts").resolve(),
    "widgets": (PROJECT_ROOT / "src" / "components" / "dynamic").resolve(),
}


# ============================================================================
# MCP 创建
# ============================================================================

mcp = FastMCP("hyperautomation-ai-controller")


# ============================================================================
# 路径验证
# ============================================================================


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


# ============================================================================
# MCP 工具函数 - 完整异常处理和日志
# ============================================================================


@mcp.tool(description="List files in any project directory (read scope is whole project).")
def list_files(dir_path: str = ".") -> str:
    """列出目录中的文件和文件夹."""
    logger.debug(f"→ list_files({dir_path})")
    try:
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
        logger.debug(f"← list_files: {len(entries)} entries")
        return result
    except Exception as e:
        logger.error(f"✗ list_files failed: {type(e).__name__}: {e}")
        raise


@mcp.tool(description="Read the full content of a file (path relative to project root).")
def read_file(file_path: str) -> str:
    """读取文件的完整内容."""
    logger.debug(f"→ read_file({file_path})")
    try:
        resolved = assert_readable_path(file_path)
        size = resolved.stat().st_size
        if size > MAX_READ_FILE_SIZE_BYTES:
            raise ValueError(
                f"file too large ({size} bytes > {MAX_READ_FILE_SIZE_BYTES} bytes), "
                "please read a smaller file or use read_file_chunk"
            )
        content = resolved.read_text(encoding="utf-8")
        logger.debug(f"← read_file: {size} bytes")
        return content
    except Exception as e:
        logger.error(f"✗ read_file({file_path}) failed: {type(e).__name__}: {e}")
        raise


@mcp.tool(description="Read a file by line range (1-based, inclusive).")
def read_file_chunk(file_path: str, start_line: int, end_line: int) -> str:
    """按行范围读取文件（1-based,包含结束行）."""
    logger.debug(f"→ read_file_chunk({file_path}, {start_line}, {end_line})")
    try:
        resolved = assert_readable_path(file_path)

        if start_line < 1 or end_line < 1:
            raise ValueError("start_line and end_line must be >= 1")
        if end_line < start_line:
            raise ValueError("end_line must be >= start_line")

        line_count = end_line - start_line + 1
        if line_count > MAX_READ_CHUNK_LINES:
            raise ValueError(
                f"requested too many lines ({line_count} > {MAX_READ_CHUNK_LINES}), "
                "please narrow the range"
            )

        lines = resolved.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        if start_line > total:
            logger.debug(f"← read_file_chunk: start_line {start_line} > total {total}, returning empty")
            return ""

        safe_end = min(end_line, total)
        chunk = lines[start_line - 1 : safe_end]
        result = "\n".join(chunk)
        logger.debug(f"← read_file_chunk: {len(chunk)} lines")
        return result
    except Exception as e:
        logger.error(
            f"✗ read_file_chunk({file_path}, {start_line}, {end_line}) failed: {type(e).__name__}: {e}"
        )
        raise


@mcp.tool(description="Create or overwrite a file, but only inside src/scripts or src/components/dynamic.")
def write_file(file_path: str, content: str) -> str:
    """创建或覆盖文件（仅限scripts和widgets目录）."""
    logger.debug(f"→ write_file({file_path}, size={len(content)})")
    try:
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

        logger.debug(f"← write_file: OK")
        return f"OK: written '{file_path}' (security-check: passed)"
    except Exception as e:
        logger.error(f"✗ write_file({file_path}) failed: {type(e).__name__}: {e}")
        raise


@mcp.tool(description="Delete a file, but only inside src/scripts or src/components/dynamic.")
def delete_file(file_path: str) -> str:
    """删除文件（仅限scripts和widgets目录）."""
    logger.debug(f"→ delete_file({file_path})")
    try:
        resolved = assert_writable_path(file_path)
        resolved.unlink()
        logger.debug(f"← delete_file: OK")
        return f"OK: deleted '{file_path}'"
    except Exception as e:
        logger.error(f"✗ delete_file({file_path}) failed: {type(e).__name__}: {e}")
        raise


@mcp.tool(description="Rename or move a file, but only inside src/scripts or src/components/dynamic.")
def rename_file(from_path: str, to_path: str) -> str:
    """重命名或移动文件（仅限scripts和widgets目录）."""
    logger.debug(f"→ rename_file({from_path} → {to_path})")
    try:
        resolved_from = assert_writable_path(from_path)
        resolved_to = assert_writable_path(to_path)
        resolved_to.parent.mkdir(parents=True, exist_ok=True)
        resolved_from.rename(resolved_to)
        logger.debug(f"← rename_file: OK")
        return f"OK: renamed '{from_path}' -> '{to_path}'"
    except Exception as e:
        logger.error(f"✗ rename_file({from_path} → {to_path}) failed: {type(e).__name__}: {e}")
        raise


# ============================================================================
# 启动
# ============================================================================


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("MCP Server Starting (stdio transport)")
    logger.info("=" * 70)
    logger.info(f"Project root: {PROJECT_ROOT}")
    logger.info(f"Debug mode: {DEBUG_MCP}")
    logger.info("=" * 70)

    try:
        # 配置stdin/stdout为无缓冲，确保及时通信
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(line_buffering=False)  # type: ignore
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(line_buffering=False)  # type: ignore

        logger.info("Starting stdio loop...")
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"MCP startup failed: {type(e).__name__}: {e}", exc_info=True)
        sys.exit(1)
