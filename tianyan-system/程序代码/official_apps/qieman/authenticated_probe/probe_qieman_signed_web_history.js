"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const BASE_URL = "https://qieman.com/pmdj/v1";
const DEFAULT_CODES = ["ZH013136", "ZH112601", "SI000193", "ZH043937"];

function requestId() {
  return `qieman-signed-probe.${Date.now().toString(16).toUpperCase()}${crypto.randomBytes(8).toString("hex").toUpperCase()}`;
}

function timestamp() {
  const date = new Date();
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date).reduce((out, part) => ({ ...out, [part.type]: part.value }), {});
  return `${parts.year}${parts.month}${parts.day}T${parts.hour}${parts.minute}${parts.second}+0800`;
}

async function captureSignedHeaders() {
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
      const timer = setTimeout(() => reject(new Error("signed request capture timeout")), 30000);
      const handler = (request) => {
        if (request.resourceType() !== "fetch") return;
        clearTimeout(timer);
        page.off("request", handler);
        resolve(request.headers());
      };
      page.on("request", handler);
    });
    await page.goto("https://qieman.com", { waitUntil: "domcontentloaded", timeout: 30000 });
    const sourceHeaders = await firstFetch;
    const xSign = String(sourceHeaders["x-sign"] || "").trim();
    if (!/^\d+[A-Fa-f0-9]{40}$/.test(xSign)) throw new Error("x-sign missing or invalid");
    return {
      xSign,
      sensorsAnonymousId: String(sourceHeaders["sensors-anonymous-id"] || "").trim(),
      userAgent: await page.evaluate(() => navigator.userAgent),
    };
  } finally {
    if (browser) await browser.close();
  }
}

async function fetchPayload(url, signedHeaders) {
  const response = await fetch(url, {
    headers: {
      accept: "application/json",
      "cache-control": "no-store",
      "user-agent": signedHeaders.userAgent,
      "x-request-id": requestId(),
      "x-sign": signedHeaders.xSign,
      ...(signedHeaders.sensorsAnonymousId
        ? { "sensors-anonymous-id": signedHeaders.sensorsAnonymousId }
        : {}),
    },
    signal: AbortSignal.timeout(30000),
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  return {
    status: response.status,
    contentType: response.headers.get("content-type"),
    bodyBytes: Buffer.byteLength(text, "utf8"),
    payload,
    parseError: text && payload === null ? "non_json_response" : null,
  };
}

async function main() {
  const args = process.argv.slice(2);
  const outputRootIndex = args.indexOf("--output-root");
  const codesIndex = args.indexOf("--strategy-codes");
  const outputRoot = outputRootIndex >= 0
    ? path.resolve(args[outputRootIndex + 1])
    : path.resolve(__dirname, "runs");
  const codes = codesIndex >= 0
    ? String(args[codesIndex + 1] || "").split(",").map((value) => value.trim()).filter(Boolean)
    : DEFAULT_CODES;
  if (!codes.length) throw new Error("no strategy codes supplied");
  const runId = `${timestamp()}-signed-web-history`;
  const runDir = path.join(outputRoot, runId);
  fs.mkdirSync(path.join(runDir, "raw"), { recursive: true });

  const signedHeaders = await captureSignedHeaders();
  const results = [];
  for (const code of codes) {
    const encoded = encodeURIComponent(code);
    const endpoints = {
      nav_history: `${BASE_URL}/pomodels/${encoded}/nav-history?start=1900-01-01&end=2026-08-09`,
      adjustments: `${BASE_URL}/pomodels/${encoded}/adjustments?page=0&size=100&format=openapi&isDesc=true`,
    };
    for (const [entity, url] of Object.entries(endpoints)) {
      const response = await fetchPayload(url, signedHeaders);
      if (response.payload !== null) {
        fs.writeFileSync(
          path.join(runDir, "raw", `${code}_${entity}.json`),
          `${JSON.stringify(response.payload, null, 2)}\n`,
          "utf8"
        );
      }
      results.push({
        strategyCode: code,
        entity,
        status: response.status,
        bodyBytes: response.bodyBytes,
        payloadType: Array.isArray(response.payload) ? "array" : typeof response.payload,
        rowCount: Array.isArray(response.payload)
          ? response.payload.length
          : Array.isArray(response.payload?.content)
            ? response.payload.content.length
            : null,
        parseError: response.parseError,
      });
    }
  }
  const summary = {
    state: "signed_anonymous_history_probe_complete",
    runId,
    strategyCodes: codes,
    authenticationPersisted: false,
    signedHeadersPersisted: false,
    results,
    qualityBoundary: "A signed anonymous response must contain structured rows before it can be treated as history.",
    runDir,
  };
  fs.writeFileSync(path.join(runDir, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ state: "error", message: String(error?.message || error) })}\n`);
  process.exitCode = 2;
});
