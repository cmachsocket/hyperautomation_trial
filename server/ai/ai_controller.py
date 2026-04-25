"""
ai_controller.py

HTTP Chat Server (standalone):
    python server/ai_controller.py
    POST /api/ai/chat  - SSE streaming chat backed by an OpenAI-compatible LLM.
                                        Read scope: whole project; Write scope: scripts/widgets only.
    OPTIONS /api/ai/chat - CORS preflight

Env vars:
    AI_PORT          HTTP port (default: 8082, standalone mode only)
    OPENAI_API_KEY   LLM API key
    OPENAI_BASE_URL  LLM base URL (default: https://api.openai.com/v1)
                                     Set to http://localhost:11434/v1 for Ollama, etc.
    AI_MODEL         Model name (default: gpt-4o-mini)

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
import os
import re
import time
from pathlib import Path
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from quart import Quart, Response, request

from server.env_loader import load_env_files

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_env_files(PROJECT_ROOT)

MAX_WRITABLE_FILE_SIZE_BYTES = 300 * 1024
MAX_TOOL_CALL_ROUNDS = int(os.getenv("AI_MAX_TOOL_CALL_ROUNDS", "20"))
AUTH_TOKEN_SECRET = os.getenv("AUTH_TOKEN_SECRET", "hyperautomation-dev-secret")
NOISY_DIR_NAMES = {".git", "node_modules", "__pycache__", "dist"}

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
- src/components/dynamic  (动态组件，也称为widgets)
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
            "name": "read_file",
            "description": "Read the full content of a file (path relative to project root).",
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
            "name": "write_file",
            "description": "Create or overwrite a file, but only inside src/scripts or src/components/dynamic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
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


async def tool_list_files(dir_path: str = ".") -> list[dict[str, str]]:
    directory = assert_readable_path(dir_path)
    entries: list[dict[str, str]] = []
    for item in sorted(directory.iterdir(), key=lambda p: p.name):
        if item.is_dir() and item.name in NOISY_DIR_NAMES and directory == PROJECT_ROOT:
            continue
        if item.resolve() in RESTRICTED_READ_PATHS:
            continue
        entries.append(
            {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "path": str(item.relative_to(PROJECT_ROOT)) if item != PROJECT_ROOT else ".",
            }
        )
    return entries


async def tool_read_file(file_path: str) -> str:
    resolved = assert_readable_path(file_path)
    return resolved.read_text(encoding="utf-8")


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
    resolved = assert_writable_path(file_path)
    existed_before = resolved.exists()
    previous_content = ""

    if existed_before:
        previous_content = resolved.read_text(encoding="utf-8")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")

    try:
        validate_written_code_safety(file_path, content)
    except Exception:
        if existed_before:
            resolved.write_text(previous_content, encoding="utf-8")
        else:
            if resolved.exists():
                resolved.unlink()
        raise

    return f"OK: written '{file_path}' (security-check: passed)"


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
        return await tool_list_files(args.get("dir_path", "."))
    if name == "read_file":
        return await tool_read_file(str(args["file_path"]))
    if name == "write_file":
        return await tool_write_file(str(args["file_path"]), str(args["content"]))
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
            f"ReadFileError: {msg}. "
            "Tip: use a project-relative file path, ensure the file exists, and avoid restricted files."
        )
    if name == "write_file" and isinstance(err, SecurityValidationError):
        return f"SecurityValidationError: {msg}"
    return f"Error: {msg}"


def sse_bytes(event: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _chunk_delta(chunk: Any) -> Any:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    first = choices[0]
    return getattr(first, "delta", None)


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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

    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    async def event_stream():
        if not api_key:
            yield sse_bytes(
                "error",
                {
                    "message": (
                        "OPENAI_API_KEY is not configured. "
                        "Please set it in .env/local.env or process environment."
                    )
                },
            )
            yield sse_bytes("done", {})
            return

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        model = os.getenv("AI_MODEL", "gpt-4o-mini")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *safe_history,
            {"role": "user", "content": user_message.strip()},
        ]
        tool_call_rounds = 0

        try:
            while True:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=cast(list[ChatCompletionMessageParam], messages),
                    tools=cast(list[ChatCompletionToolParam], LLM_TOOLS),
                    stream=True,
                )

                assistant_content = ""
                tool_calls_accum: dict[int, dict[str, str]] = {}

                async for chunk in stream:
                    delta = _chunk_delta(chunk)
                    if delta is None:
                        continue

                    content = _get_value(delta, "content")
                    if isinstance(content, str) and content:
                        assistant_content += content
                        yield sse_bytes("token", {"text": content})

                    tool_calls = _get_value(delta, "tool_calls")
                    if not tool_calls:
                        continue

                    for tool_call in tool_calls:
                        idx = _get_value(tool_call, "index", 0)
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {"id": "", "name": "", "arguments": ""}

                        tc_id = _get_value(tool_call, "id")
                        if isinstance(tc_id, str) and tc_id:
                            tool_calls_accum[idx]["id"] = tc_id

                        fn = _get_value(tool_call, "function", {})
                        fn_name = _get_value(fn, "name")
                        if isinstance(fn_name, str) and fn_name:
                            tool_calls_accum[idx]["name"] = fn_name

                        fn_arguments = _get_value(fn, "arguments")
                        if isinstance(fn_arguments, str) and fn_arguments:
                            tool_calls_accum[idx]["arguments"] += fn_arguments

                tool_calls = [tool_calls_accum[k] for k in sorted(tool_calls_accum)]

                if not tool_calls:
                    messages.append({"role": "assistant", "content": assistant_content})
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

                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_content or None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in tool_calls
                        ],
                    }
                )

                for tc in tool_calls:
                    try:
                        args = json.loads(tc["arguments"] or "{}")
                        if not isinstance(args, dict):
                            args = {}
                    except Exception:
                        args = {}

                    yield sse_bytes("tool_start", {"name": tc["name"], "args": args})

                    try:
                        raw = await dispatch_tool(tc["name"], args)
                        tool_result = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, indent=2)
                    except Exception as err:
                        tool_result = format_tool_failure(tc["name"], err)

                    yield sse_bytes("tool_end", {"name": tc["name"], "result": tool_result})
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_result})
        except Exception as err:
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
    print(f"[ai_controller] HTTP chat server -> http://localhost:{port}")
    print("[ai_controller] POST /api/ai/chat  (SSE streaming)")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    start_http_server()
