import http from "node:http";
import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const defaultConfigPath = path.resolve(scriptDir, "..", "basic_data", "config", "模型服务配置.js");
const configPath = process.env.AI_STRATEGY_MODEL_CONFIG || defaultConfigPath;

function readBrowserConfig(file) {
  try {
    if (!existsSync(file)) return {};
    const code = readFileSync(file, "utf8");
    const sandbox = { window: {} };
    vm.runInNewContext(code, sandbox, { filename: file, timeout: 1000 });
    return sandbox.window.__AI_STRATEGY_LOCAL_CONFIG__ || {};
  } catch (error) {
    console.warn(`Failed to read AI strategy model config ${file}: ${error?.message || error}`);
    return {};
  }
}

const modelConfig = readBrowserConfig(configPath);
const bridgeConfig = modelConfig.codexBridge || {};
const host = process.env.AI_STRATEGY_PROXY_HOST || bridgeConfig.host || "127.0.0.1";
const port = Number(process.env.AI_STRATEGY_PROXY_PORT || bridgeConfig.port || 8787);
const defaultModel = process.env.AI_STRATEGY_CODEX_MODEL || bridgeConfig.model || modelConfig.model || "gpt-5.4-mini";
const serviceTier = process.env.AI_STRATEGY_CODEX_SERVICE_TIER || bridgeConfig.serviceTier || "fast";
const reasoningEffort = process.env.AI_STRATEGY_CODEX_REASONING || bridgeConfig.reasoningEffort || "low";
const requestTimeoutMs = Number(process.env.AI_STRATEGY_CODEX_TIMEOUT_MS || bridgeConfig.requestTimeoutMs || 120000);
const maxBodyBytes = Number(process.env.AI_STRATEGY_PROXY_MAX_BODY_BYTES || bridgeConfig.maxBodyBytes || 2 * 1024 * 1024);
const maxConcurrent = Number(process.env.AI_STRATEGY_PROXY_MAX_CONCURRENT || bridgeConfig.maxConcurrent || 1);
const cwd = process.env.AI_STRATEGY_CODEX_CWD || bridgeConfig.cwd || path.join(os.tmpdir(), "ai-strategy-codex-cwd");
const codexCmd = process.env.CODEX_CMD || bridgeConfig.codexCmd || (
  process.platform === "win32" && process.env.APPDATA
    ? path.join(process.env.APPDATA, "npm", "codex.cmd")
    : "codex"
);

let activeRequests = 0;

function jsonResponse(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(text),
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
  });
  res.end(text);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > maxBodyBytes) {
        reject(Object.assign(new Error("Request body too large"), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function messageText(message) {
  const content = message?.content;
  if (Array.isArray(content)) {
    return content.map((part) => {
      if (typeof part === "string") return part;
      return part?.text || part?.content || "";
    }).join("");
  }
  if (typeof content === "string") return content;
  if (content == null) return "";
  return JSON.stringify(content);
}

function buildPrompt(payload) {
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  const jsonMode = payload?.response_format?.type === "json_object";
  const transcript = messages.map((message, index) => {
    const role = message?.role || `message_${index}`;
    return `## ${role}\n${messageText(message)}`;
  }).join("\n\n");
  return [
    "You are a local bridge that emulates one OpenAI-compatible chat completion call.",
    "Follow the chat messages below and return only the assistant message content.",
    "Do not inspect files, run shell commands, modify files, or add explanations about Codex.",
    jsonMode ? "The final answer must be one valid JSON object and nothing else." : "",
    "",
    transcript,
  ].filter(Boolean).join("\n");
}

async function runCodex(payload) {
  if (!existsSync(cwd)) mkdirSync(cwd, { recursive: true });
  const requestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const outputFile = path.join(os.tmpdir(), `ai-strategy-codex-${requestId}.txt`);
  const model = payload.model && payload.model !== "codex" ? String(payload.model) : defaultModel;
  const args = [
    "exec",
    "--ignore-user-config",
    "--ignore-rules",
    "-c", `service_tier="${serviceTier}"`,
    "-c", `model_reasoning_effort="${reasoningEffort}"`,
    "-m", model,
    "--sandbox", "read-only",
    "--skip-git-repo-check",
    "--ephemeral",
    "--color", "never",
    "--output-last-message", outputFile,
    "-",
  ];

  return new Promise((resolve, reject) => {
    const child = spawn(codexCmd, args, {
      cwd,
      shell: process.platform === "win32",
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env },
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, requestTimeoutMs);

    child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", async (code) => {
      clearTimeout(timer);
      try {
        const finalText = existsSync(outputFile) ? await fs.readFile(outputFile, "utf8") : "";
        await fs.rm(outputFile, { force: true });
        if (timedOut) throw new Error(`Codex CLI timed out after ${requestTimeoutMs}ms`);
        if (code !== 0) {
          const detail = [stderr.trim(), stdout.trim()].filter(Boolean).join("\n").slice(0, 4000);
          throw new Error(detail || `Codex CLI exited with code ${code}`);
        }
        const content = finalText.trim() || stdout.trim();
        if (!content) throw new Error("Codex CLI returned an empty response");
        resolve({ content, model });
      } catch (error) {
        reject(error);
      }
    });
    child.stdin.end(buildPrompt(payload), "utf8");
  });
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type,Authorization",
    });
    res.end();
    return;
  }
  if (req.method === "GET" && (req.url === "/healthz" || req.url === "/readyz")) {
    jsonResponse(res, 200, {
      ok: true,
      provider: "codex-cli-local-proxy",
      codexCmd,
      model: defaultModel,
      cwd,
      activeRequests,
    });
    return;
  }
  if (req.method === "GET" && req.url === "/v1/models") {
    jsonResponse(res, 200, {
      object: "list",
      data: [{ id: defaultModel, object: "model", owned_by: "codex-cli-local-proxy" }],
    });
    return;
  }
  if (req.method !== "POST" || req.url !== "/v1/chat/completions") {
    jsonResponse(res, 404, { error: { message: "Not found" } });
    return;
  }
  if (activeRequests >= maxConcurrent) {
    jsonResponse(res, 429, { error: { message: "Codex proxy is busy; retry after the current parse completes." } });
    return;
  }

  activeRequests += 1;
  try {
    const rawBody = await readBody(req);
    const payload = rawBody ? JSON.parse(rawBody) : {};
    const result = await runCodex(payload);
    jsonResponse(res, 200, {
      id: `chatcmpl-ai-strategy-${Date.now()}`,
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model: result.model,
      choices: [{
        index: 0,
        message: { role: "assistant", content: result.content },
        finish_reason: "stop",
      }],
    });
  } catch (error) {
    jsonResponse(res, error.statusCode || 500, {
      error: {
        message: String(error?.message || error).slice(0, 4000),
        type: "codex_proxy_error",
      },
    });
  } finally {
    activeRequests -= 1;
  }
});

server.listen(port, host, () => {
  console.log(`AI strategy Codex proxy listening on http://${host}:${port}`);
});
