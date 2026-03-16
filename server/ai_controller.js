/**
 * ai_controller.js
 *
 * Two modes (select via env var):
 *
 *   HTTP Chat Server (default):
 *     node server/ai_controller.js
 *     POST /api/chat  — SSE streaming chat backed by an OpenAI-compatible LLM.
 *                       The LLM can call file tools restricted to scripts/pages/widgets.
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
import { fileURLToPath } from "url";
import OpenAI from "openai";

// ---------------------------------------------------------------------------
// Allowed directory roots (shared by both modes)
// ---------------------------------------------------------------------------

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const ALLOWED_DIRS = {
  scripts: path.join(PROJECT_ROOT, "src", "scripts"),
  pages:   path.join(PROJECT_ROOT, "src", "pages"),
  widgets: path.join(PROJECT_ROOT, "src", "components", "dynamic"),
};

/** Resolve and validate a relative path is inside an allowed directory. */
function assertAllowed(relPath) {
  if (path.isAbsolute(relPath)) {
    throw new Error(`Access denied: absolute paths are not permitted ('${relPath}')`);
  }
  const resolved = path.resolve(PROJECT_ROOT, relPath);
  const ok = Object.values(ALLOWED_DIRS).some(
    (dir) => resolved === dir || resolved.startsWith(dir + path.sep)
  );
  if (!ok) {
    throw new Error(
      `Access denied: '${relPath}' is outside allowed directories (scripts, pages, widgets).`
    );
  }
  return resolved;
}

// ---------------------------------------------------------------------------
// Shared tool implementations
// ---------------------------------------------------------------------------

async function toolListFiles(directory) {
  const dir = ALLOWED_DIRS[directory];
  if (!dir) throw new Error(`Unknown directory: ${directory}`);
  const entries = await fs.readdir(dir, { withFileTypes: true });
  return entries.map((e) => ({ name: e.name, type: e.isDirectory() ? "directory" : "file" }));
}

async function toolReadFile(filePath) {
  const resolved = assertAllowed(filePath);
  return await fs.readFile(resolved, "utf-8");
}

async function toolWriteFile(filePath, content) {
  const resolved = assertAllowed(filePath);
  await fs.mkdir(path.dirname(resolved), { recursive: true });
  await fs.writeFile(resolved, content, "utf-8");
  return `OK: written '${filePath}'`;
}

async function toolDeleteFile(filePath) {
  const resolved = assertAllowed(filePath);
  await fs.unlink(resolved);
  return `OK: deleted '${filePath}'`;
}

async function toolRenameFile(fromPath, toPath) {
  const resolvedFrom = assertAllowed(fromPath);
  const resolvedTo   = assertAllowed(toPath);
  await fs.mkdir(path.dirname(resolvedTo), { recursive: true });
  await fs.rename(resolvedFrom, resolvedTo);
  return `OK: renamed '${fromPath}' -> '${toPath}'`;
}

async function dispatchTool(name, args) {
  switch (name) {
    case "list_files":  return toolListFiles(args.directory);
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
      description: "List files inside scripts, pages, or widgets directory.",
      parameters: {
        type: "object",
        properties: {
          directory: { type: "string", enum: ["scripts", "pages", "widgets"] },
        },
        required: ["directory"],
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
      description: "Create or overwrite a file (path relative to project root).",
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
      description: "Delete a file (path relative to project root).",
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
      description: "Rename or move a file within allowed directories.",
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

const SYSTEM_PROMPT = `You are an AI assistant for the Hyperautomation project.
You can read, write, rename, and delete files inside three directories only:
- src/scripts  (worker scripts)
- src/pages    (Vue page components)
- src/components/dynamic  (dynamic widgets, referred to as "widgets")

Always use the provided tools when the user asks about or wants to modify files.
Respond concisely in the same language the user uses.`;

// ---------------------------------------------------------------------------
// HTTP Chat Server
// ---------------------------------------------------------------------------

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type",
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
          toolResult = `Error: ${err.message}`;
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

  mcpServer.tool("list_files", "List files in scripts/pages/widgets.",
    { directory: z.enum(["scripts", "pages", "widgets"]) },
    async ({ directory }) => ({
      content: [{ type: "text", text: JSON.stringify(await toolListFiles(directory), null, 2) }],
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
