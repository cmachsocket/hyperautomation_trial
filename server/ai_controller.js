/**
 * ai_controller.js
 *
 * Two modes (select via env var):
 *
 *   HTTP Chat Server (default):
 *     node server/ai_controller.js
 *     POST /api/chat  — SSE streaming chat backed by an OpenAI-compatible LLM.
 *                       Read scope: whole project; Write scope: scripts/widgets only.
 *     OPTIONS /api/chat — CORS preflight
 *
 *   MCP Stdio Server:
 *     MCP_STDIO=1 node server/ai_controller.js
 *     Exposes the same file tools via stdio transport (for Claude Desktop etc.)
 *
 * Env vars:
 *   AI_PORT          HTTP port (default: 8082)
 *   OPENAI_API_KEY   LLM API key
 *   OPENAI_BASE_URL  LLM base URL (default: https://api.openai.com/v1)
 *                    Set to http://localhost:11434/v1 for Ollama, etc.
 *   AI_MODEL         Model name (default: gpt-4o-mini)
 *   MCP_STDIO        Set to "1" to run as MCP stdio server instead
 *
 * Chat request body:  { message: string, history?: {role,content}[] }
 * SSE events:
 *   token      — streaming text chunk  { text }
 *   tool_start — tool invocation start { name, args }
 *   tool_end   — tool result           { name, result }
 *   done       — turn finished         { }
 *   error      — error occurred        { message }
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import fs from "fs/promises";
import path from "path";
import http from "http";
import crypto from "crypto";
import { fileURLToPath } from "url";
import OpenAI from "openai";

const MAX_WRITABLE_FILE_SIZE_BYTES = 300 * 1024;
const AUTH_TOKEN_SECRET = process.env.AUTH_TOKEN_SECRET ?? "hyperautomation-dev-secret";

const DANGEROUS_CODE_PATTERNS = [
  { regex: /\b(?:require|import)\s*\(?\s*["'](?:node:)?child_process["']\s*\)?/m, reason: "forbidden module: child_process" },
  { regex: /\b(?:exec|execSync|spawn|spawnSync|fork)\s*\(/m, reason: "forbidden process execution API" },
  { regex: /\b(?:eval|Function)\s*\(/m, reason: "dynamic code execution is forbidden" },
  { regex: /\bprocess\.exit\s*\(/m, reason: "process termination is forbidden" },
];

class SecurityValidationError extends Error {
  constructor(filePath, reason) {
    super(
      [
        `Security check failed for '${filePath}': ${reason}.`,
        "Write has been rolled back.",
        "Please remove risky code (child_process / exec/spawn/fork / eval/Function / process.exit), then retry.",
      ].join(" ")
    );
    this.name = "SecurityValidationError";
  }
}

// ---------------------------------------------------------------------------
// Permission roots (shared by both modes)
// ---------------------------------------------------------------------------

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RESTRICTED_READ_PATHS = [
  path.join(PROJECT_ROOT, "local.env"),
];

const ALLOWED_DIRS = {
  scripts: path.join(PROJECT_ROOT, "src", "scripts"),
  widgets: path.join(PROJECT_ROOT, "src", "components", "dynamic"),
};

function decodeBase64Url(input) {
  const normalized = input.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  return Buffer.from(padded, "base64").toString("utf-8");
}

function verifyAuthToken(token) {
  const [payloadB64, signature] = token.split(".");
  if (!payloadB64 || !signature) {
    return null;
  }

  const expected = crypto
    .createHmac("sha256", AUTH_TOKEN_SECRET)
    .update(payloadB64)
    .digest("hex");

  if (signature !== expected) {
    return null;
  }

  let payload;
  try {
    payload = JSON.parse(decodeBase64Url(payloadB64));
  } catch {
    return null;
  }

  if (!payload || typeof payload !== "object") {
    return null;
  }

  const exp = payload.exp;
  if (!Number.isInteger(exp) || exp <= Math.floor(Date.now() / 1000)) {
    return null;
  }

  return payload;
}

function requireAuth(req, res) {
  const authHeader = req.headers.authorization || "";
  if (!authHeader.startsWith("Bearer ")) {
    res.writeHead(401, { "Content-Type": "application/json", ...CORS_HEADERS });
    res.end(JSON.stringify({ error: "Unauthorized: missing bearer token" }));
    return null;
  }

  const token = authHeader.slice("Bearer ".length).trim();
  const claims = verifyAuthToken(token);
  if (!claims) {
    res.writeHead(401, { "Content-Type": "application/json", ...CORS_HEADERS });
    res.end(JSON.stringify({ error: "Unauthorized: invalid or expired token" }));
    return null;
  }

  return claims;
}

/** Resolve and validate a relative path is inside project root (read scope). */
function assertReadablePath(relPath = ".") {
  if (path.isAbsolute(relPath)) {
    throw new Error(`Access denied: absolute paths are not permitted ('${relPath}')`);
  }
  const resolved = path.resolve(PROJECT_ROOT, relPath);
  const isInsideProject = resolved === PROJECT_ROOT || resolved.startsWith(PROJECT_ROOT + path.sep);
  if (!isInsideProject) {
    throw new Error(`Access denied: '${relPath}' is outside project root.`);
  }
  if (RESTRICTED_READ_PATHS.includes(resolved)) {
    throw new Error(`Access denied: '${relPath}' is a restricted file.`);
  }
  return resolved;
}

/** Resolve and validate a relative path is inside writable directories only. */
function assertWritablePath(relPath) {
  const resolved = assertReadablePath(relPath);
  const ok = Object.values(ALLOWED_DIRS).some(
    (dir) => resolved === dir || resolved.startsWith(dir + path.sep)
  );
  if (!ok) {
    throw new Error(
      `Access denied: '${relPath}' is outside writable directories (scripts, widgets).`
    );
  }
  return resolved;
}

// ---------------------------------------------------------------------------
// Shared tool implementations
// ---------------------------------------------------------------------------

async function toolListFiles(dirPath = ".") {
  const dir = assertReadablePath(dirPath);
  const entries = await fs.readdir(dir, { withFileTypes: true });
  return entries
    .filter((e) => !RESTRICTED_READ_PATHS.includes(path.join(dir, e.name)))
    .map((e) => ({
      name: e.name,
      type: e.isDirectory() ? "directory" : "file",
      path: path.relative(PROJECT_ROOT, path.join(dir, e.name)) || ".",
    }));
}

async function toolReadFile(filePath) {
  const resolved = assertReadablePath(filePath);
  return await fs.readFile(resolved, "utf-8");
}

function validateWrittenCodeSafety(filePath, content) {
  const ext = path.extname(filePath).toLowerCase();
  if (![".js", ".vue"].includes(ext)) {
    throw new SecurityValidationError(filePath, "only .js/.vue are writable");
  }

  const sizeInBytes = Buffer.byteLength(content, "utf-8");
  if (sizeInBytes > MAX_WRITABLE_FILE_SIZE_BYTES) {
    throw new SecurityValidationError(
      filePath,
      `file too large (${sizeInBytes} bytes > ${MAX_WRITABLE_FILE_SIZE_BYTES} bytes)`
    );
  }

  for (const rule of DANGEROUS_CODE_PATTERNS) {
    if (rule.regex.test(content)) {
      throw new SecurityValidationError(filePath, rule.reason);
    }
  }
}

function formatToolFailure(name, err) {
  if (name === "list_files") {
    return [
      `ListFilesError: ${err?.message ?? "unable to list files"}.`,
      "Tip: use a project-relative directory path, and avoid restricted paths.",
    ].join(" ");
  }
  if (name === "read_file") {
    return [
      `ReadFileError: ${err?.message ?? "unable to read file"}.`,
      "Tip: use a project-relative file path, ensure the file exists, and avoid restricted files.",
    ].join(" ");
  }
  if (name === "write_file" && err?.name === "SecurityValidationError") {
    return `SecurityValidationError: ${err.message}`;
  }
  return `Error: ${err?.message ?? "unknown tool error"}`;
}

async function toolWriteFile(filePath, content) {
  const resolved = assertWritablePath(filePath);
  let existedBefore = true;
  let previousContent = "";

  try {
    previousContent = await fs.readFile(resolved, "utf-8");
  } catch (err) {
    if (err?.code === "ENOENT") {
      existedBefore = false;
    } else {
      throw err;
    }
  }

  await fs.mkdir(path.dirname(resolved), { recursive: true });
  await fs.writeFile(resolved, content, "utf-8");

  try {
    validateWrittenCodeSafety(filePath, content);
  } catch (err) {
    if (existedBefore) {
      await fs.writeFile(resolved, previousContent, "utf-8");
    } else {
      await fs.unlink(resolved).catch(() => {});
    }
    throw err;
  }

  return `OK: written '${filePath}' (security-check: passed)`;
}

async function toolDeleteFile(filePath) {
  const resolved = assertWritablePath(filePath);
  await fs.unlink(resolved);
  return `OK: deleted '${filePath}'`;
}

async function toolRenameFile(fromPath, toPath) {
  const resolvedFrom = assertWritablePath(fromPath);
  const resolvedTo   = assertWritablePath(toPath);
  await fs.mkdir(path.dirname(resolvedTo), { recursive: true });
  await fs.rename(resolvedFrom, resolvedTo);
  return `OK: renamed '${fromPath}' -> '${toPath}'`;
}

async function dispatchTool(name, args) {
  switch (name) {
    case "list_files":  return toolListFiles(args.dir_path ?? ".");
    case "read_file":   return toolReadFile(args.file_path);
    case "write_file":  return toolWriteFile(args.file_path, args.content);
    case "delete_file": return toolDeleteFile(args.file_path);
    case "rename_file": return toolRenameFile(args.from_path, args.to_path);
    default: throw new Error(`Unknown tool: ${name}`);
  }
}

// ---------------------------------------------------------------------------
// OpenAI tool definitions
// ---------------------------------------------------------------------------

const LLM_TOOLS = [
  {
    type: "function",
    function: {
      name: "list_files",
      description: "List files in any project directory (read scope is whole project).",
      parameters: {
        type: "object",
        properties: {
          dir_path: { type: "string", description: "Project-relative directory path, e.g. '.', 'src', 'server'" },
        },
        required: [],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "read_file",
      description: "Read the full content of a file (path relative to project root).",
      parameters: {
        type: "object",
        properties: { file_path: { type: "string" } },
        required: ["file_path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description: "Create or overwrite a file, but only inside src/scripts or src/components/dynamic.",
      parameters: {
        type: "object",
        properties: {
          file_path: { type: "string" },
          content:   { type: "string" },
        },
        required: ["file_path", "content"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "delete_file",
      description: "Delete a file, but only inside src/scripts or src/components/dynamic.",
      parameters: {
        type: "object",
        properties: { file_path: { type: "string" } },
        required: ["file_path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "rename_file",
      description: "Rename or move a file, but only inside src/scripts or src/components/dynamic.",
      parameters: {
        type: "object",
        properties: {
          from_path: { type: "string" },
          to_path:   { type: "string" },
        },
        required: ["from_path", "to_path"],
      },
    },
  },
];

const SYSTEM_PROMPT = `这是一个超自动化项目，你的任务是为这个项目提供AI能力，协助开发者编写脚本和动态组件（widgets）。你可以调用预定义的工具来操作项目文件，但请注意权限限制：
在standard.md中，有通用的iot网络报文的格式规范，以及一些示例报文。你可以参考这些内容来生成符合规范的报文。
在/server/docs目录下，有一些文档文件，包含了已有的iot设备的具体报文格式和功能说明以及服务器api接口文档。这些文档可以帮助你更好地理解设备的功能和如何与它们交互。
如果不存在上述文件或目录，请先检查项目结构是否正确，发出警告。
请务必遵守权限限制，避免访问或修改不允许的文件路径。你可以读取项目中的任何文件来获取信息，但只能修改特定目录下的文件。
你可以阅读项目中的任何文件来获取信息，但只能修改以下目录中的文件：
- src/scripts  (worker脚本)
- src/components/dynamic  (动态组件，也称为widgets)
请确保你对这些权限限制有清晰的理解，并在操作文件时严格遵守这些规则，注意代码安全。
`;

// ---------------------------------------------------------------------------
// HTTP Chat Server
// ---------------------------------------------------------------------------

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try { resolve(JSON.parse(raw)); }
      catch { reject(new Error("Invalid JSON")); }
    });
    req.on("error", reject);
  });
}

function sseWrite(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

async function handleChat(req, res) {
  const claims = requireAuth(req, res);
  if (!claims) {
    return;
  }

  let body;
  try {
    body = await readBody(req);
  } catch {
    res.writeHead(400, { "Content-Type": "application/json", ...CORS_HEADERS });
    res.end(JSON.stringify({ error: "Invalid JSON body" }));
    return;
  }

  const userMessage = typeof body?.message === "string" ? body.message.trim() : "";
  if (!userMessage) {
    res.writeHead(400, { "Content-Type": "application/json", ...CORS_HEADERS });
    res.end(JSON.stringify({ error: "message is required" }));
    return;
  }

  // Sanitise history: only allow known roles and string content
  const history = Array.isArray(body.history) ? body.history : [];
  const safeHistory = history
    .filter((m) => ["user", "assistant"].includes(m?.role) && typeof m.content === "string")
    .map((m) => ({ role: m.role, content: m.content }));

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    ...CORS_HEADERS,
  });

  const openai = new OpenAI({
    apiKey:  process.env.OPENAI_API_KEY  ?? "no-key",
    baseURL: process.env.OPENAI_BASE_URL ?? "https://api.openai.com/v1",
  });
  const model = process.env.AI_MODEL ?? "gpt-4o-mini";

  const messages = [
    { role: "system", content: SYSTEM_PROMPT },
    ...safeHistory,
    { role: "user", content: userMessage },
  ];

  try {
    // Agentic loop: keep calling LLM until no more tool calls
    while (true) {
      const stream = await openai.chat.completions.create({
        model,
        messages,
        tools: LLM_TOOLS,
        stream: true,
      });

      let assistantContent = "";
      const toolCallsAccum = {}; // index → { id, name, arguments }

      for await (const chunk of stream) {
        const delta = chunk.choices[0]?.delta;
        if (!delta) continue;

        if (delta.content) {
          assistantContent += delta.content;
          sseWrite(res, "token", { text: delta.content });
        }

        if (delta.tool_calls) {
          for (const tc of delta.tool_calls) {
            if (!toolCallsAccum[tc.index]) {
              toolCallsAccum[tc.index] = { id: "", name: "", arguments: "" };
            }
            if (tc.id)                   toolCallsAccum[tc.index].id = tc.id;
            if (tc.function?.name)       toolCallsAccum[tc.index].name = tc.function.name;
            if (tc.function?.arguments)  toolCallsAccum[tc.index].arguments += tc.function.arguments;
          }
        }
      }

      const toolCalls = Object.values(toolCallsAccum);

      if (toolCalls.length === 0) {
        messages.push({ role: "assistant", content: assistantContent });
        break;
      }

      messages.push({
        role: "assistant",
        content: assistantContent || null,
        tool_calls: toolCalls.map((tc) => ({
          id: tc.id,
          type: "function",
          function: { name: tc.name, arguments: tc.arguments },
        })),
      });

      for (const tc of toolCalls) {
        let args;
        try { args = JSON.parse(tc.arguments || "{}"); }
        catch { args = {}; }

        sseWrite(res, "tool_start", { name: tc.name, args });

        let toolResult;
        try {
          const raw = await dispatchTool(tc.name, args);
          toolResult = typeof raw === "string" ? raw : JSON.stringify(raw, null, 2);
        } catch (err) {
          toolResult = formatToolFailure(tc.name, err);
        }

        sseWrite(res, "tool_end", { name: tc.name, result: toolResult });
        messages.push({ role: "tool", tool_call_id: tc.id, content: toolResult });
      }
    }
  } catch (err) {
    sseWrite(res, "error", { message: err.message ?? "LLM error" });
  }

  sseWrite(res, "done", {});
  res.end();
}

function startHttpServer() {
  const port = parseInt(process.env.AI_PORT ?? "8082", 10);

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://localhost:${port}`);

    if (req.method === "OPTIONS") {
      res.writeHead(204, CORS_HEADERS);
      res.end();
      return;
    }

    if (req.method === "POST" && url.pathname === "/api/chat") {
      await handleChat(req, res);
      return;
    }

    res.writeHead(404, { "Content-Type": "application/json", ...CORS_HEADERS });
    res.end(JSON.stringify({ error: "Not found" }));
  });

  server.listen(port, () => {
    console.error(`[ai_controller] HTTP chat server → http://localhost:${port}`);
    console.error(`[ai_controller] POST /api/chat  (SSE streaming)`);
  });
}

// ---------------------------------------------------------------------------
// MCP Stdio Server
// ---------------------------------------------------------------------------

function startMcpStdio() {
  const mcpServer = new McpServer({ name: "hyperautomation-ai-controller", version: "1.0.0" });

  mcpServer.tool("list_files", "List files in any project directory (read scope).",
    { dir_path: z.string().optional() },
    async ({ dir_path }) => ({
      content: [{ type: "text", text: JSON.stringify(await toolListFiles(dir_path ?? "."), null, 2) }],
    })
  );

  mcpServer.tool("read_file", "Read a file.",
    { file_path: z.string() },
    async ({ file_path }) => ({
      content: [{ type: "text", text: await toolReadFile(file_path) }],
    })
  );

  mcpServer.tool("write_file", "Write a file.",
    { file_path: z.string(), content: z.string() },
    async ({ file_path, content }) => ({
      content: [{ type: "text", text: await toolWriteFile(file_path, content) }],
    })
  );

  mcpServer.tool("delete_file", "Delete a file.",
    { file_path: z.string() },
    async ({ file_path }) => ({
      content: [{ type: "text", text: await toolDeleteFile(file_path) }],
    })
  );

  mcpServer.tool("rename_file", "Rename a file.",
    { from_path: z.string(), to_path: z.string() },
    async ({ from_path, to_path }) => ({
      content: [{ type: "text", text: await toolRenameFile(from_path, to_path) }],
    })
  );

  const transport = new StdioServerTransport();
  mcpServer.connect(transport);
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

if (process.env.MCP_STDIO === "1") {
  startMcpStdio();
} else {
  startHttpServer();
}
