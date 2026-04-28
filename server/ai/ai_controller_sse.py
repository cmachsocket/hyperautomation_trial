"""
MCP Server using improved stdio transport with proper buffering and error handling.

Run:
    python server/ai/ai_controller_sse.py

This is a rewrite with better robustness for stdio-based MCP communication.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# ============================================================================
# 配置
# ============================================================================

MAX_WRITABLE_FILE_SIZE_BYTES = 300 * 1024
MAX_READ_CHUNK_LINES = int(os.environ.get("AI_MAX_READ_CHUNK_LINES", "400"))

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
    force=True,
)
logger = logging.getLogger("mcp")
logger.setLevel(logging.DEBUG if DEBUG_MCP else logging.INFO)
logger.propagate = False

# 禁用其他库的日志噪音
logging.getLogger("fastmcp").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.INFO)

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


@mcp.tool(description="Write a file in chunks. Call repeatedly with the same 'upload_id' and increasing 'chunk_index'. Set 'finalize'=true on the last chunk to commit atomically.")
def write_file_chunk(upload_id: str, file_path: str, chunk_index: int, content: str, finalize: bool = False) -> str:
    logger.debug(f"→ write_file_chunk(upload_id={upload_id}, file_path={file_path}, chunk_index={chunk_index}, finalize={finalize})")
    try:
        if not isinstance(upload_id, str) or not upload_id:
            raise ValueError("upload_id is required and must be a non-empty string")
        if not isinstance(chunk_index, int) or chunk_index < 0:
            raise ValueError("chunk_index must be a non-negative integer")

        resolved = assert_writable_path(file_path)
        temp_dir = PROJECT_ROOT / ".ai_write_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        key = hashlib.sha256(f"{upload_id}:{file_path}".encode("utf-8")).hexdigest()
        chunk_path = temp_dir / f"{key}.chunk{chunk_index:06d}"
        chunk_path.write_text(content, encoding="utf-8")

        if not finalize:
            logger.debug(f"← write_file_chunk: stored chunk {chunk_index}")
            return f"OK: stored chunk {chunk_index} for upload_id={upload_id}"

        # finalize: assemble chunks
        chunks = sorted([p for p in temp_dir.iterdir() if p.name.startswith(key + ".chunk")])
        if not chunks:
            raise ValueError("No chunks found to finalize")

        parts = []
        total_bytes = 0
        for p in chunks:
            part = p.read_text(encoding="utf-8")
            total_bytes += len(part.encode("utf-8"))
            if total_bytes > MAX_WRITABLE_FILE_SIZE_BYTES:
                raise SecurityValidationError(f"assembled file too large ({total_bytes} bytes)")
            parts.append(part)

        assembled = "".join(parts)

        resolved.parent.mkdir(parents=True, exist_ok=True)
        target_tmp = resolved.with_suffix(resolved.suffix + ".tmp_ai")
        target_tmp.write_text(assembled, encoding="utf-8")

        try:
            validate_written_code_safety(file_path, assembled)
        except Exception as err:
            if target_tmp.exists():
                target_tmp.unlink()
            raise

        # backup and replace
        if resolved.exists():
            backup = resolved.with_suffix(resolved.suffix + ".backup_ai")
            resolved.rename(backup)
        target_tmp.rename(resolved)

        # cleanup
        for p in chunks:
            try:
                p.unlink()
            except Exception:
                pass

        logger.debug(f"← write_file_chunk: assembled {len(chunks)} chunks into {file_path}")
        return f"OK: written '{file_path}' (assembled from {len(chunks)} chunks)"
    except Exception as e:
        logger.error(f"✗ write_file_chunk failed: {type(e).__name__}: {e}")
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
