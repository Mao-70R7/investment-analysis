"use strict";

const crypto = require("node:crypto");
const net = require("node:net");
const { spawnSync } = require("node:child_process");
const { requestText } = require("./windows_dns_https");

const PHONE = String(process.env.QIEMAN_PHONE || "").trim();
const DPAPI_INPUT = String(process.env.QIEMAN_DPAPI_INPUT || "").trim();
const PORT = Number(process.env.QIEMAN_AUTH_PORT || 43912);
const SESSION_TIMEOUT_MS = Number(process.env.QIEMAN_AUTH_TIMEOUT_MS || 15 * 60 * 1000);
const QIEMAN_BASE = "https://qieman.com/pmdj/v1";
const STARGATE_BASE = "https://stargate.yingmi.com/api";

const READ_ONLY_OPERATION_IDS = new Set([
  "SearchPortfolioStrategies",
  "StrategySearchByKeyword",
  "GetStrategyDetails",
  "BatchGetStrategiesComposition",
  "GetStrategyNavHistory",
  "GetStrategyBenchmark",
  "GetStrategyAdjustments",
]);

// The public OpenAPI document exposes these read-only history endpoints, but
// some API keys omit them from the key-scoped docs.json catalog.  A caller may
// probe only these exact documented paths with normal Bearer authentication;
// service-side authorization and quota checks remain authoritative.
const DOCUMENTED_HISTORY_OPERATIONS = Object.freeze({
  GetStrategyNavHistory: {
    method: "POST",
    path: "/oap/api/v1/strategy/nav-history",
  },
  GetStrategyAdjustments: {
    method: "POST",
    path: "/oap/api/v1/strategy/adjustments",
  },
});

const API_KEY_APPLICATION_PAYLOAD = {
  name: "CLI申请的占位符",
  organization: "CLI申请的占位符",
  email: "",
  usagePurposes: ["探索更多合作可能"],
  usageDescription: "",
  agreementIds: ["AGREEMENT34-V20250425"],
  positionId: "user_position_other",
  position: "CLI申请的占位符",
  organizationType: "其他",
};

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function readDpapiSecret(inputPath) {
  const script = [
    "$ErrorActionPreference='Stop'",
    "[void][Reflection.Assembly]::LoadWithPartialName('System.Security')",
    "$encrypted=[Convert]::FromBase64String([IO.File]::ReadAllText($env:QIEMAN_DPAPI_SOURCE,[Text.Encoding]::ASCII))",
    "$entropy=[Text.Encoding]::UTF8.GetBytes('advisor-monitor:qieman-stargate:v1')",
    "$plain=[System.Security.Cryptography.ProtectedData]::Unprotect($encrypted,$entropy,[System.Security.Cryptography.DataProtectionScope]::CurrentUser)",
    "[Console]::Out.Write([Text.Encoding]::UTF8.GetString($plain))",
  ].join(";");
  const completed = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    {
      encoding: "utf8",
      windowsHide: true,
      env: { ...process.env, QIEMAN_DPAPI_SOURCE: inputPath },
    }
  );
  if (completed.status !== 0) {
    throw new Error(`DPAPI key load failed: ${String(completed.stderr || "").trim()}`);
  }
  const value = String(completed.stdout || "").trim();
  if (value.length < 16) throw new Error("DPAPI key load returned an invalid secret");
  return value;
}

function requestId() {
  return `qieman-isolated.${Date.now().toString(16).toUpperCase()}${crypto.randomBytes(8).toString("hex").toUpperCase()}`;
}

function headers(xSign, accessToken, apiKey) {
  return {
    accept: "application/json",
    "content-type": "application/json",
    "x-request-id": requestId(),
    ...(xSign ? { "x-sign": xSign } : {}),
    ...(accessToken ? { authorization: `Bearer ${accessToken}` } : {}),
    ...(apiKey ? { authorization: `Bearer ${apiKey}` } : {}),
  };
}

async function fetchJson(url, options) {
  const response = await requestText(url, options, { timeoutMs: 30000, attempts: 3 });
  const text = response.text;
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (response.status < 200 || response.status >= 300) {
    const message = payload && typeof payload === "object"
      ? payload.message || payload.errorMessage || payload.error || null
      : null;
    const error = new Error(message ? `HTTP ${response.status}: ${message}` : `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return { status: response.status, payload };
}

async function getXSign() {
  const { chromium } = require("playwright");
  let browser;
  try {
    try {
      browser = await chromium.launch({ channel: "chrome", headless: true });
    } catch {
      browser = await chromium.launch({ headless: true });
    }
    const page = await browser.newPage();
    const firstFetch = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("x-sign fetch timeout")), 30000);
      const handler = (request) => {
        if (request.resourceType() !== "fetch") return;
        clearTimeout(timer);
        page.off("request", handler);
        resolve(request);
      };
      page.on("request", handler);
    });
    await page.goto("https://qieman.com", { waitUntil: "domcontentloaded", timeout: 30000 });
    const request = await firstFetch;
    const xSign = String(request.headers()["x-sign"] || "").trim();
    if (!/^\d+[A-Fa-f0-9]{40}$/.test(xSign)) {
      throw new Error("x-sign missing or invalid");
    }
    return xSign;
  } finally {
    if (browser) await browser.close();
  }
}

async function preparePhone(phone, xSign) {
  const { payload } = await fetchJson(`${QIEMAN_BASE}/user/register/phone/prepare`, {
    method: "POST",
    headers: headers(xSign),
    body: JSON.stringify({ phone }),
  });
  const token = payload && typeof payload.token === "string" ? payload.token.trim() : "";
  if (!token) throw new Error("prepare response missing token");
  return token;
}

async function confirmPhone(phone, prepareToken, verifyCode, xSign) {
  const { payload } = await fetchJson(`${QIEMAN_BASE}/user/register/phone/confirm`, {
    method: "POST",
    headers: headers(xSign),
    body: JSON.stringify({
      phone,
      poManagerName: null,
      token: prepareToken,
      verifyCode,
      autoLogin: true,
    }),
  });
  const accessToken = payload && typeof payload.accessToken === "string" ? payload.accessToken.trim() : "";
  if (!accessToken) throw new Error("confirm response missing access token");
  return accessToken;
}

function extractApiKey(payload) {
  if (!payload || typeof payload !== "object") return "";
  const value = payload.key || payload.apiKey;
  return typeof value === "string" ? value.trim() : "";
}

async function obtainApiKey(xSign, accessToken) {
  const current = await fetchJson(`${QIEMAN_BASE}/stargate/api-key-application`, {
    method: "GET",
    headers: headers(xSign, accessToken),
  });
  let apiKey = extractApiKey(current.payload);
  if (apiKey) return { apiKey, applicationState: "existing_key_reused" };
  if (current.payload && Object.keys(current.payload).length > 0) {
    throw new Error(`existing API key application has no usable key; status=${current.payload.status || "unknown"}`);
  }
  const applied = await fetchJson(`${QIEMAN_BASE}/stargate/api-key-application`, {
    method: "POST",
    headers: headers(xSign, accessToken),
    body: JSON.stringify(API_KEY_APPLICATION_PAYLOAD),
  });
  apiKey = extractApiKey(applied.payload);
  if (!apiKey) throw new Error("API key application response missing key");
  return { apiKey, applicationState: "new_key_applied" };
}

async function fetchAuthenticatedOperationCatalog(apiKey) {
  const url = `${STARGATE_BASE}/docs.json?apiKey=${encodeURIComponent(apiKey)}`;
  const { payload: document } = await fetchJson(url, {
    method: "GET",
    headers: { accept: "application/json" },
  });
  if (!document || typeof document !== "object" || !document.paths) {
    throw new Error("authenticated OpenAPI document is missing paths");
  }
  const serverBase = document.servers && document.servers[0] && document.servers[0].url
    ? String(document.servers[0].url).replace(/\/$/, "")
    : STARGATE_BASE;
  const operations = {};
  for (const [path, pathItem] of Object.entries(document.paths)) {
    if (!pathItem || typeof pathItem !== "object") continue;
    for (const method of ["get", "post", "put", "patch", "delete"]) {
      const definition = pathItem[method];
      const operationId = definition && definition.operationId;
      if (!READ_ONLY_OPERATION_IDS.has(operationId)) continue;
      const existing = operations[operationId];
      if (existing && existing.method === "POST") continue;
      operations[operationId] = { method: method.toUpperCase(), path };
    }
  }
  const missing = [...READ_ONLY_OPERATION_IDS].filter((operationId) => !operations[operationId]);
  return { serverBase, operations, missing, allOperationCount: Object.keys(document.paths).length };
}

async function proxyOperation(apiKey, operationCatalog, message) {
  const operation = operationCatalog.operations[message.operationId];
  if (!operation) throw new Error("operation is not in the read-only allowlist");
  const query = new URLSearchParams(message.query || {}).toString();
  const url = `${operationCatalog.serverBase}${operation.path}${query ? `?${query}` : ""}`;
  return fetchJson(url, {
    method: operation.method,
    headers: headers(null, null, apiKey),
    ...(operation.method === "GET" ? {} : { body: JSON.stringify(message.body || {}) }),
  });
}

async function probeDocumentedHistoryOperation(apiKey, operationCatalog, message) {
  const operation = DOCUMENTED_HISTORY_OPERATIONS[message.operationId];
  if (!operation) throw new Error("operation is not in the documented history allowlist");
  const url = `${operationCatalog.serverBase}${operation.path}`;
  return fetchJson(url, {
    method: operation.method,
    headers: headers(null, null, apiKey),
    body: JSON.stringify(message.body || {}),
  });
}

function reply(socket, payload) {
  socket.end(`${JSON.stringify(payload)}\n`);
}

async function main() {
  if (!DPAPI_INPUT && !/^1\d{10}$/.test(PHONE)) throw new Error("phone is missing or invalid");
  if (!Number.isInteger(PORT) || PORT < 1024 || PORT > 65535) throw new Error("invalid local port");

  let xSign = "";
  let prepareToken = "";
  let apiKey = DPAPI_INPUT ? readDpapiSecret(DPAPI_INPUT) : "";
  let operationCatalog = apiKey ? await fetchAuthenticatedOperationCatalog(apiKey) : null;
  if (!apiKey) {
    xSign = await getXSign();
    prepareToken = await preparePhone(PHONE, xSign);
  }
  let verificationInProgress = false;

  // Keep the writable side open after the local client half-closes its send
  // side. Proxy calls are asynchronous and must be able to return after the
  // upstream StarGate response arrives.
  const server = net.createServer({ allowHalfOpen: true }, (socket) => {
    socket.setEncoding("utf8");
    socket.setTimeout(30000);
    let buffer = "";
    socket.on("data", (chunk) => {
      buffer += chunk;
      if (buffer.length > 2 * 1024 * 1024) socket.destroy();
    });
    socket.on("end", async () => {
      try {
        const message = JSON.parse(buffer.trim() || "{}");
        if (message.action === "verify") {
          const verifyCode = String(message.verifyCode || "").trim();
          if (!/^\d{4,8}$/.test(verifyCode)) throw new Error("verification code format is invalid");
          if (apiKey) return reply(socket, { state: "api_key_ready" });
          if (verificationInProgress) throw new Error("verification is already in progress");
          verificationInProgress = true;
          try {
            const accessToken = await confirmPhone(PHONE, prepareToken, verifyCode, xSign);
            const result = await obtainApiKey(xSign, accessToken);
            const authenticatedCatalog = await fetchAuthenticatedOperationCatalog(result.apiKey);
            apiKey = result.apiKey;
            operationCatalog = authenticatedCatalog;
            emit({
              state: "api_key_ready",
              applicationState: result.applicationState,
              apiKeyPersisted: false,
              authenticatedOperationCount: Object.keys(authenticatedCatalog.operations).length,
              missingOperations: authenticatedCatalog.missing,
            });
            return reply(socket, {
              state: "api_key_ready",
              applicationState: result.applicationState,
              authenticatedOperationCount: Object.keys(authenticatedCatalog.operations).length,
              missingOperations: authenticatedCatalog.missing,
            });
          } finally {
            verificationInProgress = false;
          }
        }
        if (message.action === "proxy") {
          if (!apiKey || !operationCatalog) throw new Error("API key is not ready");
          const result = await proxyOperation(apiKey, operationCatalog, message);
          return reply(socket, { state: "ok", status: result.status, payload: result.payload });
        }
        if (message.action === "probe_documented_history") {
          if (!apiKey || !operationCatalog) throw new Error("API key is not ready");
          const result = await probeDocumentedHistoryOperation(apiKey, operationCatalog, message);
          return reply(socket, { state: "ok", status: result.status, payload: result.payload });
        }
        if (message.action === "describe") {
          if (!apiKey || !operationCatalog) throw new Error("API key is not ready");
          return reply(socket, {
            state: "ok",
            serverBase: operationCatalog.serverBase,
            operations: operationCatalog.operations,
            missingOperations: operationCatalog.missing,
            documentedHistoryOperations: DOCUMENTED_HISTORY_OPERATIONS,
          });
        }
        if (message.action === "close") {
          reply(socket, { state: "closing" });
          server.close(() => process.exit(0));
          return;
        }
        throw new Error("unsupported action");
      } catch (error) {
        reply(socket, { state: "error", message: String(error && error.message ? error.message : error) });
      }
    });
  });

  server.on("error", (error) => {
    emit({ state: "error", stage: "local_server", message: error.message });
    process.exitCode = 2;
  });
  server.listen(PORT, "127.0.0.1", () => {
    emit({
      state: apiKey ? "dpapi_key_loaded" : "sms_sent",
      localPort: PORT,
      apiKeyPersisted: Boolean(DPAPI_INPUT),
      sessionPersisted: false,
      ...(operationCatalog ? { authenticatedOperationCount: Object.keys(operationCatalog.operations).length } : {}),
    });
  });

  if (SESSION_TIMEOUT_MS > 0) {
    const timeout = setTimeout(() => {
      emit({ state: "expired", apiKeyPersisted: Boolean(DPAPI_INPUT), sessionPersisted: false });
      server.close(() => process.exit(3));
    }, SESSION_TIMEOUT_MS);
    timeout.unref();
  }
}

main().catch((error) => {
  emit({ state: "error", stage: "prepare", message: String(error && error.message ? error.message : error) });
  process.exitCode = 2;
});
