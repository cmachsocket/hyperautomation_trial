"""
ai_controller.py

HTTP Chat Server (standalone):
    python server/ai/ai_controller.py
    POST /api/ai/chat  - SSE streaming chat backed by an Anthropic-compatible LLM.
                                        Read scope: whole project; Write scope: scripts/widgets only.
    OPTIONS /api/ai/chat - CORS preflight

Env vars:
    AI_PORT          HTTP port (default: 8082, standalone mode only)
    ANTHROPIC_API_KEY   LLM API key
    ANTHROPIC_BASE_URL  LLM base URL (optional)
    AI_MODEL            Model name (default: claude-3-5-sonnet-20241022)
    AI_MAX_TOKENS       Max tokens per response (default: 2048, MiniMax Anthropic-compatible cap)
    AI_TEMPERATURE      Sampling temperature (default: 0.2)
    AI_DEBUG            Enable debug logs (default: 1)
    AI_DEBUG_LOG_PATH   Optional debug log file path (default: empty)

Env loading order:
    .env is loaded first, then local.env overrides it.

Chat request body:  { message: string, history?: {role,content}[] }
SSE events:
    token      - streaming text chunk  { text }
    tool_start - tool invocation start { name, args }
    tool_end   - tool result           { name, result }
    done       - turn finished         { }
    error      - error occurred        { message }
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, cast
import uuid
from collections import OrderedDict

from anthropic import AsyncAnthropic
from quart import Quart, Response, request

from server.env_loader import load_env_files

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_env_files(PROJECT_ROOT)

MAX_WRITABLE_FILE_SIZE_BYTES = 300 * 1024
MAX_READ_FILE_SIZE_BYTES = int(os.getenv("AI_MAX_READ_FILE_SIZE_BYTES", str(120 * 1024)))
MAX_READ_CHUNK_LINES = int(os.getenv("AI_MAX_READ_CHUNK_LINES", "400"))
MAX_TOOL_CALL_ROUNDS = int(os.getenv("AI_MAX_TOOL_CALL_ROUNDS", "20"))
MAX_MINIMAX_ANTHROPIC_TOKENS = 2048
AUTH_TOKEN_SECRET = os.getenv("AUTH_TOKEN_SECRET", "hyperautomation-dev-secret")
NOISY_DIR_NAMES = {".git", "node_modules", "__pycache__", "dist"}
AI_DEBUG = os.getenv("AI_DEBUG", "1").strip() not in {"0", "false", "False", "FALSE"}
AI_DEBUG_LOG_PATH = os.getenv("AI_DEBUG_LOG_PATH", "").strip()

LOGGER = logging.getLogger("ai_controller")
if not LOGGER.handlers:
    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setFormatter(logging.Formatter("[ai_controller] %(levelname)s %(message)s"))
    LOGGER.addHandler(_stderr_handler)
    if AI_DEBUG_LOG_PATH:
        try:
            _file_handler = logging.FileHandler(AI_DEBUG_LOG_PATH, encoding="utf-8")
            _file_handler.setFormatter(
                logging.Formatter("%(asctime)s [ai_controller] %(levelname)s %(message)s")
            )
            LOGGER.addHandler(_file_handler)
        except Exception as err:
            LOGGER.warning("cannot open AI_DEBUG_LOG_PATH=%r: %s", AI_DEBUG_LOG_PATH, err)
LOGGER.setLevel(logging.DEBUG if AI_DEBUG else logging.INFO)
LOGGER.propagate = False

# In-memory store for tool results so we avoid embedding large tool outputs
# directly into the messages list (which becomes part of the LLM context/tokens).
# Store is small and FIFO; contents are retrievable via the `fetch_tool_result_chunk` tool.
TOOL_RESULT_STORE: "OrderedDict[str, dict]" = OrderedDict()
TOOL_RESULT_STORE_MAX = int(os.getenv("AI_TOOL_RESULT_STORE_MAX", "20"))


def _store_tool_result(content: str) -> str:
    rid = uuid.uuid4().hex
    TOOL_RESULT_STORE[rid] = {
        "content": content,
        "len_chars": len(content),
        "lines": content.splitlines(),
        "created": time.time(),
    }
    while len(TOOL_RESULT_STORE) > TOOL_RESULT_STORE_MAX:
        TOOL_RESULT_STORE.popitem(last=False)
    return rid


def _fetch_tool_result_chunk(result_id: str, start_line: int, end_line: int) -> str:
    info = TOOL_RESULT_STORE.get(result_id)
    if info is None:
        raise ValueError(f"unknown result_id: {result_id}")
    lines = info["lines"]
    total = len(lines)
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    if start_line > total:
        return ""
    safe_end = min(end_line, total)
    return "\n".join(lines[start_line - 1 : safe_end])

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
    def __init__(self, file_path: str, reason: str) -> None:
        super().__init__(
            " ".join(
                [
                    f"Security check failed for '{file_path}': {reason}.",
                    "Write has been rolled back.",
                    "Please remove risky code (child_process / exec/spawn/fork / eval/Function / process.exit), then retry.",
                ]
            )
        )


RESTRICTED_READ_PATHS = {
    (PROJECT_ROOT / "local.env").resolve(),
    (PROJECT_ROOT / ".env.production").resolve(),
}

ALLOWED_DIRS = {
    "scripts": (PROJECT_ROOT / "src" / "scripts").resolve(),
    "widgets": (PROJECT_ROOT / "src" / "components" / "dynamic").resolve(),
}

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
}

SYSTEM_PROMPT = """这是一个超自动化项目，你的任务是为这个项目提供AI能力，协助开发者编写脚本和动态组件（widgets）。你可以调用预定义的工具来操作项目文件，但请注意权限限制：
在 server/coe/standards/standard.md 中，有通用的iot网络报文的格式规范，以及一些示例报文。你可以参考这些内容来生成符合规范的报文。
在 server/coe/docs/ 目录下，有一些文档文件，包含了已有的iot设备的具体报文格式和功能说明以及服务器api接口文档。这些文档可以帮助你更好地理解设备的功能和如何与它们交互。
如果不存在上述文件或目录，请先检查项目结构是否正确，发出警告。
请务必遵守权限限制，避免访问或修改不允许的文件路径。你可以读取项目中的任何文件来获取信息，但只能修改特定目录下的文件。
你可以阅读项目中的任何文件来获取信息，但只能修改以下目录中的文件：
- src/scripts  (worker脚本)
- src/components/dynamic  (动态组件，也称为widgets，使用vue3编写)
当文件较大或文档较长时，优先使用 read_file_chunk 分段读取，而不是一次性读取全文。
请确保你对这些权限限制有清晰的理解，并在操作文件时严格遵守这些规则，注意代码安全。
不要在每轮对话中遍历整个项目；仅在回答当前问题确有必要时才读取最小范围文件。
"""

LLM_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in any project directory (read scope is whole project).",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {
                        "type": "string",
                        "description": "Project-relative directory path, e.g. '.', 'src', 'server'",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_chunk",
            "description": "Read a file by line range (1-based, inclusive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["file_path", "start_line", "end_line"],
            },
        },
    },
    
    {
        "type": "function",
        "function": {
            "name": "write_file_chunk",
            "description": "Write a file in chunks. Call repeatedly with the same 'upload_id' and increasing 'chunk_index'. Set 'finalize'=true on the last chunk to commit atomically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "upload_id": {"type": "string", "description": "Client-generated id for this upload stream."},
                    "file_path": {"type": "string"},
                    "chunk_index": {"type": "integer", "minimum": 0},
                    "content": {"type": "string"},
                    "finalize": {"type": "boolean"},
                },
                "required": ["upload_id", "file_path", "chunk_index", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_tool_result_chunk",
            "description": "Fetch a stored tool result by id, return a line-range (1-based inclusive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "result_id": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1}
                },
                "required": ["result_id", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file, but only inside src/scripts or src/components/dynamic.",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_file",
            "description": "Rename or move a file, but only inside src/scripts or src/components/dynamic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_path": {"type": "string"},
                    "to_path": {"type": "string"},
                },
                "required": ["from_path", "to_path"],
            },
        },
    },
]


def decode_base64url(input_str: str) -> str:
    padding = "=" * (-len(input_str) % 4)
    raw = base64.urlsafe_b64decode((input_str + padding).encode("ascii"))
    return raw.decode("utf-8")


def verify_auth_token(token: str) -> dict[str, Any] | None:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = hmac.new(
        AUTH_TOKEN_SECRET.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(decode_base64url(payload_b64))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    exp = payload.get("exp")
    if not isinstance(exp, int):
        return None

    if exp <= int(time.time()):
        return None

    return payload


def json_response(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        content_type="application/json",
        headers={**CORS_HEADERS},
    )


def require_auth() -> dict[str, Any] | None:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :].strip()
    return verify_auth_token(token)


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


async def tool_list_files(dir_path: str = ".") -> list[dict[str, Any]]:
    directory = assert_readable_path(dir_path)
    entries: list[dict[str, Any]] = []
    for item in sorted(directory.iterdir(), key=lambda p: p.name):
        if item.is_dir() and item.name in NOISY_DIR_NAMES and directory == PROJECT_ROOT:
            continue
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
    return entries


async def tool_read_file(file_path: str) -> str:
    # Full-file reads are intentionally disabled to avoid large token usage.
    # Consumers must call `read_file_chunk` with a line range instead.
    raise ValueError(
        "Full file reads are disabled. Use 'read_file_chunk' with 'start_line' and 'end_line' instead."
    )


async def tool_read_file_chunk(file_path: str, start_line: int, end_line: int) -> str:
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
    return "\n".join(chunk)


def validate_written_code_safety(file_path: str, content: str) -> None:
    ext = Path(file_path).suffix.lower()
    if ext not in {".js", ".ts", ".vue"}:
        raise SecurityValidationError(file_path, "only .js/.ts/.vue are writable")

    size = len(content.encode("utf-8"))
    if size > MAX_WRITABLE_FILE_SIZE_BYTES:
        raise SecurityValidationError(
            file_path,
            f"file too large ({size} bytes > {MAX_WRITABLE_FILE_SIZE_BYTES} bytes)",
        )

    for regex, reason in DANGEROUS_CODE_PATTERNS:
        if regex.search(content):
            raise SecurityValidationError(file_path, reason)


async def tool_write_file(file_path: str, content: str) -> str:
    # Full-file writes are disabled. Use `write_file_chunk` to upload in pieces and commit atomically.
    raise ValueError(
        "Full file writes are disabled. Use 'write_file_chunk' (upload_id, chunk_index, content, finalize) instead."
    )


async def tool_write_file_chunk(upload_id: str, file_path: str, chunk_index: int, content: str, finalize: bool = False) -> str:
    # Basic validation
    if not isinstance(upload_id, str) or not upload_id:
        raise ValueError("upload_id is required and must be a non-empty string")
    if not isinstance(chunk_index, int) or chunk_index < 0:
        raise ValueError("chunk_index must be a non-negative integer")

    resolved = assert_writable_path(file_path)
    temp_dir = PROJECT_ROOT / ".ai_write_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    key = hashlib.sha256(f"{upload_id}:{file_path}".encode("utf-8")).hexdigest()
    chunk_path = temp_dir / f"{key}.chunk{chunk_index:06d}"

    # write chunk (overwrite if same index re-sent)
    chunk_path.write_text(content, encoding="utf-8")

    if not finalize:
        return f"OK: stored chunk {chunk_index} for upload_id={upload_id}"

    # finalize: assemble all chunks for this upload
    chunks = sorted(p for p in temp_dir.iterdir() if p.name.startswith(key) and p.name.endswith(".chunk" + p.name.split(".chunk")[-1]))
    # Fallback: collect by prefix
    chunks = sorted([p for p in temp_dir.iterdir() if p.name.startswith(key + ".chunk")])
    if not chunks:
        raise ValueError("No chunks found to finalize")

    # read and concatenate, while enforcing size limit
    parts: list[str] = []
    total_bytes = 0
    for p in chunks:
        part = p.read_text(encoding="utf-8")
        total_bytes += len(part.encode("utf-8"))
        if total_bytes > MAX_WRITABLE_FILE_SIZE_BYTES:
            raise SecurityValidationError(file_path, f"assembled file too large ({total_bytes} bytes)")
        parts.append(part)

    assembled = "".join(parts)

    # atomic write: write to temp file then rename
    resolved.parent.mkdir(parents=True, exist_ok=True)
    target_tmp = resolved.with_suffix(resolved.suffix + ".tmp_ai")
    target_tmp.write_text(assembled, encoding="utf-8")

    try:
        validate_written_code_safety(file_path, assembled)
    except Exception:
        if target_tmp.exists():
            target_tmp.unlink()
        raise

    # backup existing content in case validation elsewhere needs rollback
    if resolved.exists():
        backup = resolved.with_suffix(resolved.suffix + ".backup_ai")
        resolved.rename(backup)
    target_tmp.rename(resolved)

    # cleanup chunks
    for p in chunks:
        try:
            p.unlink()
        except Exception:
            pass

    return f"OK: written '{file_path}' (assembled from {len(chunks)} chunks)"


async def tool_delete_file(file_path: str) -> str:
    resolved = assert_writable_path(file_path)
    resolved.unlink()
    return f"OK: deleted '{file_path}'"


async def tool_rename_file(from_path: str, to_path: str) -> str:
    resolved_from = assert_writable_path(from_path)
    resolved_to = assert_writable_path(to_path)
    resolved_to.parent.mkdir(parents=True, exist_ok=True)
    resolved_from.rename(resolved_to)
    return f"OK: renamed '{from_path}' -> '{to_path}'"


async def dispatch_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "list_files":
        dir_path = args.get("dir_path") or args.get("path") or args.get("directory") or "."
        return await tool_list_files(str(dir_path))

    if name == "read_file":
        # Explicitly reject any attempt to call the removed full-file read tool.
        raise ValueError(
            "ReadFullFileDisabled: full file reads are not permitted. Use 'read_file_chunk' with 'start_line' and 'end_line'."
        )

    if name == "read_file_chunk":
        file_path = args.get("file_path") or args.get("path") or args.get("filename")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        if file_path is None:
            raise ValueError("read_file_chunk requires 'file_path' parameter")
        if start_line is None or end_line is None:
            raise ValueError("read_file_chunk requires 'start_line' and 'end_line' parameters")
        return await tool_read_file_chunk(str(file_path), int(start_line), int(end_line))

    if name == "fetch_tool_result_chunk":
        result_id = args.get("result_id")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        if result_id is None or start_line is None or end_line is None:
            raise ValueError("fetch_tool_result_chunk requires 'result_id','start_line','end_line'")
        return _fetch_tool_result_chunk(str(result_id), int(start_line), int(end_line))

    if name == "write_file":
        # Reject direct full-file writes; instruct to use chunked writes instead.
        raise ValueError(
            "WriteFullFileDisabled: full file writes are not permitted. Use 'write_file_chunk' to upload in pieces and finalize."
        )
    if name == "write_file_chunk":
        upload_id = args.get("upload_id") or args.get("id")
        file_path = args.get("file_path") or args.get("path")
        chunk_index = args.get("chunk_index")
        content = args.get("content")
        finalize = bool(args.get("finalize", False))
        if upload_id is None or file_path is None or chunk_index is None or content is None:
            raise ValueError("write_file_chunk requires 'upload_id','file_path','chunk_index','content'")
        return await tool_write_file_chunk(str(upload_id), str(file_path), int(chunk_index), str(content), finalize)
    if name == "delete_file":
        return await tool_delete_file(str(args["file_path"]))
    if name == "rename_file":
        return await tool_rename_file(str(args["from_path"]), str(args["to_path"]))
    raise ValueError(f"Unknown tool: {name}")


def format_tool_failure(name: str, err: Exception) -> str:
    msg = str(err) or "unknown tool error"
    if name == "list_files":
        return (
            f"ListFilesError: {msg}. "
            "Tip: use a project-relative directory path, and avoid restricted paths."
        )
    if name == "read_file":
        return (
            f"ReadFileDisabled: {msg}. "
            "Full file reads are disabled; use 'read_file_chunk' with a specific line range."
        )
    if name == "write_file":
        return (
            f"WriteFileDisabled: {msg}. "
            "Full file writes are disabled; use 'write_file_chunk' with an 'upload_id' and finalize when complete."
        )
    if name == "write_file_chunk":
        return (
            f"WriteFileChunkError: {msg}. "
            "Tip: call with 'upload_id','file_path','chunk_index','content' and set 'finalize'=true on the last chunk."
        )
    if name == "read_file_chunk":
        return (
            f"ReadFileChunkError: {msg}. "
            "Tip: use 1-based inclusive line range and keep chunk size small."
        )
    if name == "write_file" and isinstance(err, SecurityValidationError):
        return f"SecurityValidationError: {msg}"
    return f"Error: {msg}"


def sse_bytes(event: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _mask_secret(value: str, head: int = 4, tail: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= head + tail:
        return "*" * len(value)
    return f"{value[:head]}...{value[-tail:]}"




async def handle_chat() -> Response:
    if request.method == "OPTIONS":
        return Response(status=204, headers={**CORS_HEADERS})

    claims = require_auth()
    if not claims:
        return json_response({"error": "Unauthorized: invalid or expired token"}, status=401)

    try:
        body = await request.get_json()
    except Exception:
        return json_response({"error": "Invalid JSON body"}, status=400)

    user_message = body.get("message", "") if isinstance(body, dict) else ""
    if not isinstance(user_message, str) or not user_message.strip():
        return json_response({"error": "message is required"}, status=400)

    history = body.get("history", []) if isinstance(body, dict) else []
    safe_history: list[dict[str, str]] = []
    if isinstance(history, list):
        for item in history:
            if (
                isinstance(item, dict)
                and item.get("role") in {"user", "assistant"}
                and isinstance(item.get("content"), str)
            ):
                safe_history.append({"role": item["role"], "content": item["content"]})

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()

    async def event_stream():
        if not api_key:
            yield sse_bytes(
                "error",
                {
                    "message": (
                        "ANTHROPIC_API_KEY is not configured. "
                        "Please set it in .env/local.env or process environment."
                    )
                },
            )
            yield sse_bytes("done", {})
            return

        client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url or None,
        )
        model = os.getenv("AI_MODEL", "claude-3-5-sonnet-20241022")
        configured_max_tokens = int(os.getenv("AI_MAX_TOKENS", str(MAX_MINIMAX_ANTHROPIC_TOKENS)))
        max_tokens = min(configured_max_tokens, MAX_MINIMAX_ANTHROPIC_TOKENS)
        if configured_max_tokens > MAX_MINIMAX_ANTHROPIC_TOKENS:
            LOGGER.warning(
                "AI_MAX_TOKENS=%s exceeds MiniMax Anthropic-compatible cap=%s, clamped",
                configured_max_tokens,
                MAX_MINIMAX_ANTHROPIC_TOKENS,
            )
        temperature = float(os.getenv("AI_TEMPERATURE", "0.2"))
        LOGGER.info(
            "upstream request config: base_url=%r, api_key=%r, model=%r, max_tokens=%r, temperature=%r",
            base_url,
            _mask_secret(api_key),
            model,
            max_tokens,
            temperature,
        )

        messages: list[dict[str, Any]] = [
            *safe_history,
            {"role": "user", "content": user_message.strip()},
        ]
        tool_call_rounds = 0

        try:
            while True:
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=SYSTEM_PROMPT,
                    messages=cast(Any, messages),
                    tools=[
                        {
                            "name": tool["function"]["name"],
                            "description": tool["function"]["description"],
                            "input_schema": tool["function"]["parameters"],
                        }
                        for tool in LLM_TOOLS
                    ],
                )
                stop_reason = getattr(response, "stop_reason", None)
                LOGGER.debug("anthropic response stop_reason=%r", stop_reason)

                assistant_text_parts: list[str] = []
                tool_uses: list[dict[str, Any]] = []
                for block in response.content or []:
                    if block.type == "text":
                        assistant_text_parts.append(block.text)
                    elif block.type == "tool_use":
                        tool_uses.append(
                            {"id": block.id, "name": block.name, "input": block.input}
                        )

                assistant_content = "".join(assistant_text_parts)
                if assistant_content:
                    LOGGER.debug("assistant text length=%d", len(assistant_content))
                    yield sse_bytes("token", {"text": assistant_content})

                if stop_reason == "max_tokens":
                    LOGGER.warning(
                        "response truncated by max_tokens=%s (tool_uses=%d)",
                        max_tokens,
                        len(tool_uses),
                    )
                    if "[TOOL_CALL]" in assistant_content and "[/TOOL_CALL]" not in assistant_content:
                        yield sse_bytes(
                            "error",
                            {
                                "message": (
                                    "AI output truncated while generating tool call content. "
                                    "Current MiniMax Anthropic-compatible max is 2048. "
                                    "Please split the operation into smaller write steps and retry."
                                )
                            },
                        )
                        break

                if not tool_uses:
                    messages.append({"role": "assistant", "content": response.content})
                    break

                if len(tool_uses) > 1:
                    yield sse_bytes(
                        "error",
                        {
                            "message": (
                                "Multiple tool calls in one assistant turn are blocked. "
                                "Please use exactly one tool call per request, then continue with a follow-up if needed."
                            )
                        },
                    )
                    break

                tool_call_rounds += 1
                if tool_call_rounds > MAX_TOOL_CALL_ROUNDS:
                    yield sse_bytes(
                        "error",
                        {
                            "message": (
                                f"AI tool calls exceeded limit ({MAX_TOOL_CALL_ROUNDS}). "
                                "Please try a more specific request."
                            )
                        },
                    )
                    break

                messages.append({"role": "assistant", "content": response.content})

                for tc in tool_uses:
                    raw_args = tc.get("input")
                    args: dict[str, Any] = cast(dict[str, Any], raw_args) if isinstance(raw_args, dict) else {}
                    LOGGER.debug("tool_start name=%s args_keys=%s", tc["name"], sorted(args.keys()))
                    yield sse_bytes("tool_start", {"name": tc["name"], "args": args})

                    try:
                        raw = await dispatch_tool(tc["name"], args)
                        tool_result = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, indent=2)
                        LOGGER.debug("tool_end name=%s status=ok", tc["name"])
                    except Exception as err:
                        LOGGER.exception("tool_end name=%s status=error", tc["name"])
                        tool_result = format_tool_failure(tc["name"], err)

                    yield sse_bytes("tool_end", {"name": tc["name"], "result": tool_result})
                    # Store the full tool result server-side and only inject a compact
                    # reference and short summary into the messages fed to the LLM to
                    # avoid re-injecting large tool outputs into subsequent requests.
                    try:
                        result_id = _store_tool_result(tool_result)
                        # Build a plain-text, LLM-safe reference message (avoid structured objects)
                        lines = tool_result.splitlines()
                        total_lines = len(lines)
                        summary = tool_result if len(tool_result) <= 400 else tool_result[:400] + "..."
                        ref_text = (
                            f"[TOOL_RESULT_REF] id={result_id} lines={total_lines}\n"
                            f"SUMMARY:\n{summary}\n"
                            "To retrieve details, call the tool: fetch_tool_result_chunk(result_id=\"<id>\", start_line=<n>, end_line=<m>)."
                        )
                        messages.append({"role": "user", "content": ref_text})
                    except Exception:
                        # If storing fails for some reason, fall back to including the text inline
                        fallback = tool_result if isinstance(tool_result, str) else json.dumps(tool_result, ensure_ascii=False)
                        messages.append({"role": "user", "content": fallback})
        except Exception as err:
            LOGGER.exception("event_stream failed")
            yield sse_bytes("error", {"message": str(err) or "LLM error"})

        yield sse_bytes("done", {})

    return Response(
        event_stream(),
        status=200,
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            **CORS_HEADERS,
        },
    )


async def not_found(**_: Any) -> Response:
    return json_response({"error": "Not found"}, status=404)


def setup_ai_routes(app: Quart, prefix: str = "/api/ai") -> None:
    cleaned = (prefix or "").rstrip("/")
    if not cleaned:
        cleaned = "/api/ai"
    chat_path = f"{cleaned}/chat"
    endpoint = f"ai_chat_{cleaned.strip('/').replace('/', '_') or 'root'}"
    app.add_url_rule(chat_path, endpoint=endpoint, view_func=handle_chat, methods=["POST", "OPTIONS"])


def start_http_server() -> None:
    app = Quart(__name__)
    setup_ai_routes(app, prefix="/api/ai")
    app.add_url_rule("/<path:tail>", endpoint="ai_not_found", view_func=not_found, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    app.add_url_rule("/", endpoint="ai_root_not_found", view_func=not_found, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])

    port = int(os.getenv("AI_PORT", "8082"))
    LOGGER.info("HTTP chat server -> http://localhost:%s", port)
    LOGGER.info("POST /api/ai/chat (SSE streaming)")
    if AI_DEBUG_LOG_PATH:
        LOGGER.info("debug logs file -> %s", AI_DEBUG_LOG_PATH)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    start_http_server()
