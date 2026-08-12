"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { requestText } = require("./windows_dns_https");

const BASE_URL = "https://qieman.com/pmdj/v1";
const MAX_PAGES = 100;
const DEFAULT_REQUEST_IDLE_TIMEOUT_MS = 120_000;
const DEFAULT_REQUEST_TOTAL_TIMEOUT_MS = 600_000;
const DEFAULT_REQUEST_ATTEMPTS = 4;
const DEFAULT_SIGNAL_PAGE_SIZE = 25;
const DEFAULT_REGULAR_PAGE_SIZE = 100;
const SHANGHAI_DATE_FORMATTER = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function parseArgs(argv) {
  const out = {};
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) continue;
    const key = value.slice(2);
    const next = argv[index + 1];
    if (next && !next.startsWith("--")) {
      out[key] = next;
      index += 1;
    } else {
      out[key] = true;
    }
  }
  return out;
}

function requiredPath(value, label) {
  if (!value) throw new Error(`${label} is required`);
  return path.resolve(String(value));
}

function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(minimum, Math.min(maximum, Math.trunc(parsed)));
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
  }).formatToParts(date).reduce((result, item) => {
    result[item.type] = item.value;
    return result;
  }, {});
  return `${parts.year}${parts.month}${parts.day}T${parts.hour}${parts.minute}${parts.second}+0800`;
}

function todayShanghai() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function requestId() {
  return `qieman-catalog.${Date.now().toString(16).toUpperCase()}${crypto.randomBytes(8).toString("hex").toUpperCase()}`;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function atomicJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value)}\n`, "utf8");
  fs.renameSync(temporary, filePath);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
}

function readJsonl(filePath) {
  return fs.readFileSync(filePath, "utf8")
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

function activeLocks(lockDir) {
  if (!lockDir || !fs.existsSync(lockDir)) return [];
  const allowedDailyRunId = String(process.env.QIEMAN_ALLOWED_DAILY_RUN_ID || "").trim();
  const allowDeviceLock = String(process.env.QIEMAN_ALLOW_DEVICE_LOCK || "").trim() === "1";
  return fs.readdirSync(lockDir, { withFileTypes: true })
    .filter((item) => item.isFile() && item.name.endsWith(".lock") && !item.name.includes(".stale."))
    .filter((item) => {
      if (item.name === "device.lock" && allowDeviceLock) return false;
      if (item.name !== "daily_update.lock" || !allowedDailyRunId) return true;
      try {
        const payload = readJson(path.join(lockDir, item.name));
        return String(payload.runId || "").trim() !== allowedDailyRunId;
      } catch {
        return true;
      }
    })
    .map((item) => item.name)
    .sort();
}

function ensureNoProductionLock(lockDir) {
  const locks = activeLocks(lockDir);
  if (locks.length) {
    const error = new Error(`active production lock: ${locks.join(", ")}`);
    error.code = "ACTIVE_PRODUCTION_LOCK";
    throw error;
  }
}

async function generateSignedHeaders() {
  // The public Qieman web bundle defines x-sign as:
  //   nowMs + SHA256(floor(1.01 * nowMs)).upper().slice(0, 32)
  // Reproduce that deterministic public-web header directly.  The history
  // endpoints do not require a browser session or an authenticated token, so
  // launching Playwright here only added an undeclared runtime dependency.
  const capturedAtMs = Date.now();
  const signTail = crypto
    .createHash("sha256")
    .update(String(Math.floor(1.01 * capturedAtMs)))
    .digest("hex")
    .toUpperCase()
    .slice(0, 32);
  return {
    xSign: `${capturedAtMs}${signTail}`,
    sensorsAnonymousId: "",
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0 Safari/537.36",
    capturedAtMs,
  };
}

class SignedFetcher {
  constructor({
    lockDir,
    minimumDelayMs = 80,
    requestIdleTimeoutMs = DEFAULT_REQUEST_IDLE_TIMEOUT_MS,
    requestTotalTimeoutMs = DEFAULT_REQUEST_TOTAL_TIMEOUT_MS,
    requestAttempts = DEFAULT_REQUEST_ATTEMPTS,
  }) {
    this.lockDir = lockDir;
    this.minimumDelayMs = minimumDelayMs;
    this.requestIdleTimeoutMs = requestIdleTimeoutMs;
    this.requestTotalTimeoutMs = Math.max(requestIdleTimeoutMs, requestTotalTimeoutMs);
    this.requestAttempts = requestAttempts;
    this.headers = null;
    this.refreshPromise = null;
    this.lastRequestAt = 0;
  }

  async refresh(force = false) {
    if (!force && this.headers && Date.now() - this.headers.capturedAtMs < 4 * 60 * 1000) {
      return this.headers;
    }
    if (!this.refreshPromise) {
      this.refreshPromise = generateSignedHeaders().finally(() => {
        this.refreshPromise = null;
      });
    }
    this.headers = await this.refreshPromise;
    return this.headers;
  }

  async request(url, attempt = 0) {
    ensureNoProductionLock(this.lockDir);
    const signedHeaders = await this.refresh(attempt > 0);
    const delay = Math.max(0, this.minimumDelayMs - (Date.now() - this.lastRequestAt));
    if (delay) await sleep(delay);
    this.lastRequestAt = Date.now();
    let response;
    try {
      response = await requestText(url, {
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
      }, {
        timeoutMs: this.requestIdleTimeoutMs,
        totalTimeoutMs: this.requestTotalTimeoutMs,
        attempts: 1,
      });
    } catch (error) {
      if (attempt + 1 < this.requestAttempts) {
        await sleep(Math.min(5000, 1000 * (attempt + 1)));
        return this.request(url, attempt + 1);
      }
      throw error;
    }
    const text = response.text;
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = null;
    }
    if (
      [401, 403, 408, 425, 429, 500, 502, 503, 504].includes(response.status)
      && attempt + 1 < this.requestAttempts
    ) {
      await sleep(response.status === 429 ? 2000 * (attempt + 1) : 500 * (attempt + 1));
      return this.request(url, attempt + 1);
    }
    return {
      status: response.status,
      bodyBytes: Buffer.byteLength(text, "utf8"),
      payload,
      parseError: text && payload === null ? "non_json_response" : null,
    };
  }
}

function contentRows(payload) {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload?.content) ? payload.content : [];
}

async function fetchPaged(fetcher, makeUrl, pageSize = DEFAULT_REGULAR_PAGE_SIZE) {
  const pages = [];
  let expectedPages = null;
  let expectedElements = null;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await fetcher.request(makeUrl(page));
    if (response.status !== 200 || !response.payload || typeof response.payload !== "object") {
      return {
        status: response.status,
        bodyBytes: pages.reduce((sum, item) => sum + item.bodyBytes, 0) + response.bodyBytes,
        pages: pages.map((item) => item.payload),
        content: pages.flatMap((item) => contentRows(item.payload)),
        totalPages: expectedPages,
        totalElements: expectedElements,
        complete: false,
        parseError: response.parseError,
        failedPage: page,
      };
    }
    pages.push(response);
    expectedPages = Number.isInteger(response.payload.totalPages)
      ? response.payload.totalPages
      : expectedPages;
    expectedElements = Number.isInteger(response.payload.totalElements)
      ? response.payload.totalElements
      : expectedElements;
    const rows = contentRows(response.payload);
    const isLast = response.payload.last === true
      || (expectedPages !== null && page + 1 >= expectedPages)
      || (expectedPages === null && rows.length < pageSize);
    if (isLast) break;
  }
  const content = pages.flatMap((item) => contentRows(item.payload));
  const complete = pages.length < MAX_PAGES
    && (expectedElements === null || expectedElements === content.length);
  return {
    status: pages.every((item) => item.status === 200) ? 200 : pages.at(-1)?.status,
    bodyBytes: pages.reduce((sum, item) => sum + item.bodyBytes, 0),
    pages: pages.map((item) => item.payload),
    content,
    totalPages: expectedPages,
    totalElements: expectedElements,
    complete,
    parseError: null,
    failedPage: null,
  };
}

function historyBusinessKey(row) {
  if (!row || typeof row !== "object") return null;
  for (const field of ["adjustmentId", "id", "signalId", "sigId", "dedupKey"]) {
    const value = row[field];
    if (value !== null && value !== undefined && String(value).trim()) {
      return `${field}:${String(value).trim()}`;
    }
  }
  return `sha256:${crypto.createHash("sha256").update(JSON.stringify(row)).digest("hex")}`;
}

function mergeRowsByKey(baselineRows, incomingRows, keyFunction, sorter = null) {
  const merged = new Map();
  for (const row of baselineRows || []) {
    const key = keyFunction(row);
    if (key !== null && key !== undefined) merged.set(String(key), row);
  }
  for (const row of incomingRows || []) {
    const key = keyFunction(row);
    if (key !== null && key !== undefined) merged.set(String(key), row);
  }
  const rows = [...merged.values()];
  if (sorter) rows.sort(sorter);
  return rows;
}

function navDateMillis(row) {
  const value = Number(row?.navDate);
  return Number.isFinite(value) ? value : null;
}

function navDateText(row) {
  const value = navDateMillis(row);
  if (value === null) return null;
  try {
    return SHANGHAI_DATE_FORMATTER.format(new Date(value));
  } catch {
    return null;
  }
}

function latestNavDate(rows) {
  return (rows || [])
    .map(navDateText)
    .filter(Boolean)
    .sort()
    .at(-1) || null;
}

function incrementalStartDate(baselineRows, overlapDays) {
  const dates = (baselineRows || []).map(navDateMillis).filter((value) => value !== null);
  if (!dates.length) return "1900-01-01";
  const start = new Date(Math.max(...dates) - Math.max(0, overlapDays) * 86400000);
  return start.toISOString().slice(0, 10);
}

async function fetchPagedIncremental(
  fetcher,
  makeUrl,
  baselineRows,
  pageSize = DEFAULT_REGULAR_PAGE_SIZE,
) {
  const baselineKeys = new Set((baselineRows || []).map(historyBusinessKey).filter(Boolean));
  if (!baselineKeys.size) return fetchPaged(fetcher, makeUrl, pageSize);
  const pages = [];
  let expectedPages = null;
  let expectedElements = null;
  let stoppedAtKnownBoundary = false;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await fetcher.request(makeUrl(page));
    if (response.status !== 200 || !response.payload || typeof response.payload !== "object") {
      return {
        status: response.status,
        bodyBytes: pages.reduce((sum, item) => sum + item.bodyBytes, 0) + response.bodyBytes,
        pages: pages.map((item) => item.payload),
        content: pages.flatMap((item) => contentRows(item.payload)),
        totalPages: expectedPages,
        totalElements: expectedElements,
        complete: false,
        incrementalBoundaryComplete: false,
        parseError: response.parseError,
        failedPage: page,
      };
    }
    pages.push(response);
    expectedPages = Number.isInteger(response.payload.totalPages) ? response.payload.totalPages : expectedPages;
    expectedElements = Number.isInteger(response.payload.totalElements) ? response.payload.totalElements : expectedElements;
    const rows = contentRows(response.payload);
    if (rows.some((row) => baselineKeys.has(historyBusinessKey(row)))) {
      stoppedAtKnownBoundary = true;
      break;
    }
    const isLast = response.payload.last === true
      || (expectedPages !== null && page + 1 >= expectedPages)
      || (expectedPages === null && rows.length < pageSize);
    if (isLast) break;
  }
  const content = pages.flatMap((item) => contentRows(item.payload));
  const reachedOfficialEnd = pages.length < MAX_PAGES && (
    pages.at(-1)?.payload?.last === true
    || (expectedPages !== null && pages.length >= expectedPages)
    || (expectedPages === null && contentRows(pages.at(-1)?.payload).length < pageSize)
  );
  return {
    status: pages.every((item) => item.status === 200) ? 200 : pages.at(-1)?.status,
    bodyBytes: pages.reduce((sum, item) => sum + item.bodyBytes, 0),
    pages: pages.map((item) => item.payload),
    content,
    totalPages: expectedPages,
    totalElements: expectedElements,
    complete: stoppedAtKnownBoundary || reachedOfficialEnd,
    incrementalBoundaryComplete: stoppedAtKnownBoundary || reachedOfficialEnd,
    stoppedAtKnownBoundary,
    parseError: null,
    failedPage: null,
  };
}

function resultFromFile(filePath, entity) {
  if (!fs.existsSync(filePath)) return null;
  try {
    const payload = readJson(filePath);
    const rows = entity === "nav_history" ? payload : payload.content;
    if (
      entity !== "nav_history"
      && (
        payload.complete === false
        || payload.retainedBaseline === true
        || payload.refreshComplete === false
      )
    ) {
      return null;
    }
    return {
      entity,
      status: 200,
      rowCount: Array.isArray(rows) ? rows.length : 0,
      latestDate: entity === "nav_history" && Array.isArray(rows) ? latestNavDate(rows) : null,
      complete: payload.complete !== false,
      resumed: true,
      file: filePath,
    };
  } catch {
    return null;
  }
}

async function collectStrategy({
  code,
  name,
  fetcher,
  runDir,
  endDate,
  baselineRunDir,
  overlapDays,
  signalPageSize,
  regularPageSize,
}) {
  ensureNoProductionLock(fetcher.lockDir);
  const encoded = encodeURIComponent(code);
  const navPath = path.join(runDir, "raw", "nav", `${code}.json`);
  const historyType = code.startsWith("SI") ? "signal_adjustments" : "regular_adjustments";
  const historyPath = path.join(runDir, "raw", historyType, `${code}.json`);
  const baselineNavPath = baselineRunDir ? path.join(baselineRunDir, "raw", "nav", `${code}.json`) : null;
  const baselineHistoryPath = baselineRunDir ? path.join(baselineRunDir, "raw", historyType, `${code}.json`) : null;
  const baselineNav = baselineNavPath && fs.existsSync(baselineNavPath) ? readJson(baselineNavPath) : [];
  const baselineHistoryPayload = baselineHistoryPath && fs.existsSync(baselineHistoryPath)
    ? readJson(baselineHistoryPath)
    : null;
  const baselineHistory = Array.isArray(baselineHistoryPayload?.content) ? baselineHistoryPayload.content : [];
  let navResult = resultFromFile(navPath, "nav_history");
  if (!navResult) {
    const startDate = incrementalStartDate(baselineNav, overlapDays);
    const url = `${BASE_URL}/pomodels/${encoded}/nav-history?start=${encodeURIComponent(startDate)}&end=${encodeURIComponent(endDate)}`;
    const response = await fetcher.request(url);
    const downloaded = Array.isArray(response.payload) ? response.payload : [];
    const merged = mergeRowsByKey(
      baselineNav,
      downloaded,
      navDateMillis,
      (left, right) => (navDateMillis(left) || 0) - (navDateMillis(right) || 0),
    );
    if (response.status === 200 && Array.isArray(response.payload)) atomicJson(navPath, merged);
    navResult = {
      entity: "nav_history",
      status: response.status,
      rowCount: merged.length,
      latestDate: latestNavDate(merged),
      downloadedRowCount: downloaded.length,
      baselineRowCount: baselineNav.length,
      incrementalStartDate: startDate,
      incremental: baselineNav.length > 0,
      complete: response.status === 200 && Array.isArray(response.payload),
      bodyBytes: response.bodyBytes,
      parseError: response.parseError,
      resumed: false,
      file: response.status === 200 && Array.isArray(response.payload) ? navPath : null,
    };
  }
  let historyResult = resultFromFile(historyPath, historyType);
  if (!historyResult) {
    const pageSize = code.startsWith("SI") ? signalPageSize : regularPageSize;
    const makeUrl = code.startsWith("SI")
      ? (page) => `${BASE_URL}/pomodels/${encoded}/sig-adjustments?page=${page}&size=${pageSize}`
      : (page) => `${BASE_URL}/pomodels/${encoded}/adjustments?page=${page}&size=${pageSize}&format=openapi&isDesc=true`;
    const baselineIsComplete = baselineHistoryPayload
      && baselineHistoryPayload.complete !== false
      && Array.isArray(baselineHistoryPayload.content);
    const retainBaseline = (error) => {
      if (!baselineIsComplete) throw error;
      const retained = {
        ...baselineHistoryPayload,
        strategyCode: code,
        historyType,
        complete: true,
        refreshComplete: false,
        retainedBaseline: true,
        retainedFromRunDir: baselineRunDir,
        retainedAt: new Date().toISOString(),
        refreshErrorCode: error?.code || null,
        refreshError: String(error?.message || error),
        content: baselineHistory,
      };
      atomicJson(historyPath, retained);
      historyResult = {
        entity: historyType,
        status: 200,
        rowCount: baselineHistory.length,
        downloadedRowCount: 0,
        baselineRowCount: baselineHistory.length,
        incremental: true,
        complete: true,
        refreshComplete: false,
        retainedBaseline: true,
        retainedFromRunDir: baselineRunDir,
        refreshErrorCode: error?.code || null,
        refreshError: String(error?.message || error),
        resumed: false,
        file: historyPath,
      };
    };
    let response = null;
    try {
      response = await fetchPagedIncremental(fetcher, makeUrl, baselineHistory, pageSize);
    } catch (error) {
      retainBaseline(error);
    }
    if (response && (response.status !== 200 || !response.complete) && baselineIsComplete) {
      const error = new Error(
        `history refresh incomplete: status=${response.status}, failedPage=${response.failedPage}`,
      );
      error.code = "HISTORY_REFRESH_INCOMPLETE";
      retainBaseline(error);
      response = null;
    }
    if (response) {
      const mergedContent = mergeRowsByKey(
        baselineHistory,
        response.content,
        historyBusinessKey,
        (left, right) => {
          const leftDate = String(left.adjustedOn || left.adjustedDate || left.createdTime || "");
          const rightDate = String(right.adjustedOn || right.adjustedDate || right.createdTime || "");
          return rightDate.localeCompare(leftDate);
        },
      );
      const persisted = {
        strategyCode: code,
        historyType,
        totalPages: response.totalPages,
        totalElements: response.totalElements,
        complete: response.complete,
        refreshComplete: response.status === 200 && response.complete,
        retainedBaseline: false,
        incremental: baselineHistory.length > 0,
        baselineRowCount: baselineHistory.length,
        downloadedRowCount: response.content.length,
        stoppedAtKnownBoundary: response.stoppedAtKnownBoundary === true,
        content: mergedContent,
      };
      if (response.status === 200) atomicJson(historyPath, persisted);
      historyResult = {
        entity: historyType,
        status: response.status,
        rowCount: mergedContent.length,
        downloadedRowCount: response.content.length,
        baselineRowCount: baselineHistory.length,
        incremental: baselineHistory.length > 0,
        totalPages: response.totalPages,
        totalElements: response.totalElements,
        complete: response.status === 200 && response.complete,
        refreshComplete: response.status === 200 && response.complete,
        retainedBaseline: false,
        bodyBytes: response.bodyBytes,
        parseError: response.parseError,
        failedPage: response.failedPage,
        resumed: false,
        file: response.status === 200 ? historyPath : null,
      };
    }
  }
  return {
    strategyCode: code,
    strategyName: name,
    strategyKind: code.startsWith("SI") ? "signal" : "regular",
    nav: navResult,
    history: historyResult,
    complete: navResult.complete && historyResult.complete,
  };
}

async function runPool(items, concurrency, worker, onResult) {
  let nextIndex = 0;
  async function runner() {
    while (true) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= items.length) return;
      let result;
      try {
        result = await worker(items[index], index);
      } catch (error) {
        result = {
          strategyCode: items[index].code,
          strategyName: items[index].name,
          complete: false,
          errorCode: error?.code || null,
          error: String(error?.message || error),
        };
      }
      await onResult(result, index);
      if (result.errorCode === "ACTIVE_PRODUCTION_LOCK") return;
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, () => runner()));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const catalogPath = requiredPath(args["catalog-path"], "--catalog-path");
  const outputRoot = requiredPath(args["output-root"], "--output-root");
  const lockDir = requiredPath(args["lock-dir"], "--lock-dir");
  const concurrency = boundedInteger(args.concurrency, 3, 1, 8);
  const signalPageSize = boundedInteger(
    args["signal-page-size"],
    DEFAULT_SIGNAL_PAGE_SIZE,
    10,
    100,
  );
  const regularPageSize = boundedInteger(
    args["regular-page-size"],
    DEFAULT_REGULAR_PAGE_SIZE,
    10,
    100,
  );
  const requestIdleTimeoutMs = boundedInteger(
    args["request-idle-timeout-ms"],
    DEFAULT_REQUEST_IDLE_TIMEOUT_MS,
    30_000,
    900_000,
  );
  const requestTotalTimeoutMs = boundedInteger(
    args["request-total-timeout-ms"],
    DEFAULT_REQUEST_TOTAL_TIMEOUT_MS,
    requestIdleTimeoutMs,
    1_800_000,
  );
  const requestAttempts = boundedInteger(
    args["request-attempts"],
    DEFAULT_REQUEST_ATTEMPTS,
    1,
    8,
  );
  const limit = args.limit ? Math.max(1, Number(args.limit)) : null;
  const endDate = String(args["end-date"] || todayShanghai());
  ensureNoProductionLock(lockDir);

  const catalog = readJsonl(catalogPath);
  const unique = new Map();
  for (const row of catalog) {
    const code = String(row.source_strategy_id || "").trim();
    if (!code || unique.has(code)) continue;
    unique.set(code, { code, name: String(row.strategy_name || "").trim() || null });
  }
  let strategies = [...unique.values()].sort((a, b) => a.code.localeCompare(b.code));
  if (limit) strategies = strategies.slice(0, limit);
  if (!strategies.length) throw new Error("catalog contains no source_strategy_id values");

  const runId = args["resume-run-dir"]
    ? path.basename(path.resolve(String(args["resume-run-dir"])))
    : String(args["run-id"] || "").trim() || `${timestamp()}-signed-history-catalog`;
  const runDir = args["resume-run-dir"]
    ? path.resolve(String(args["resume-run-dir"]))
    : path.join(outputRoot, runId);
  fs.mkdirSync(path.join(runDir, "raw", "nav"), { recursive: true });
  fs.mkdirSync(path.join(runDir, "raw", "regular_adjustments"), { recursive: true });
  fs.mkdirSync(path.join(runDir, "raw", "signal_adjustments"), { recursive: true });

  const fallbackRunDir = args["fallback-run-dir"]
    ? path.resolve(String(args["fallback-run-dir"]))
    : null;
  const baselineRunDir = args["baseline-run-dir"]
    ? path.resolve(String(args["baseline-run-dir"]))
    : fallbackRunDir;
  const overlapDays = Math.max(0, Math.min(60, Number(args["incremental-overlap-days"] || 7)));
  const fallbackRecovered = [];
  if (fallbackRunDir && fs.existsSync(fallbackRunDir)) {
    for (const strategy of strategies) {
      const historyType = strategy.code.startsWith("SI") ? "signal_adjustments" : "regular_adjustments";
      for (const relativePath of [
        path.join("raw", "nav", `${strategy.code}.json`),
        path.join("raw", historyType, `${strategy.code}.json`),
      ]) {
        const target = path.join(runDir, relativePath);
        const source = path.join(fallbackRunDir, relativePath);
        if (!fs.existsSync(target) && fs.existsSync(source)) {
          atomicJson(target, readJson(source));
          fallbackRecovered.push({ strategyCode: strategy.code, relativePath, sourceRunDir: fallbackRunDir });
        }
      }
    }
  }

  const fetcher = new SignedFetcher({
    lockDir,
    minimumDelayMs: Math.max(0, Number(args["minimum-delay-ms"] || 80)),
    requestIdleTimeoutMs,
    requestTotalTimeoutMs,
    requestAttempts,
  });
  const resultByCode = new Map();
  const checkpointPath = path.join(runDir, "checkpoint.json");
  if (fs.existsSync(checkpointPath)) {
    const checkpoint = readJson(checkpointPath);
    for (const result of checkpoint.results || []) resultByCode.set(result.strategyCode, result);
  }
  let completed = 0;
  const startedAt = new Date().toISOString();
  await runPool(
    strategies,
    concurrency,
    (strategy) => collectStrategy({
      code: strategy.code,
      name: strategy.name,
      fetcher,
      runDir,
      endDate,
      baselineRunDir,
      overlapDays,
      signalPageSize,
      regularPageSize,
    }),
    async (result) => {
      resultByCode.set(result.strategyCode, result);
      completed += 1;
      const results = strategies.map((item) => resultByCode.get(item.code)).filter(Boolean);
      atomicJson(checkpointPath, {
        state: "running",
        runId,
        catalogPath,
        endDate,
        requestedStrategyCount: strategies.length,
        completedStrategyCount: completed,
        results,
      });
      if (
        !result.nav?.resumed
        || !result.history?.resumed
        || !result.complete
        || result.history?.retainedBaseline
      ) {
        process.stdout.write(`${JSON.stringify({
          progress: `${completed}/${strategies.length}`,
          code: result.strategyCode,
          complete: result.complete,
          historyRetainedBaseline: result.history?.retainedBaseline === true,
          error: result.error || result.history?.refreshError || null,
        })}\n`);
      }
    },
  );
  const results = strategies.map((item) => resultByCode.get(item.code)).filter(Boolean);
  const stoppedForLock = results.some((item) => item.errorCode === "ACTIVE_PRODUCTION_LOCK");
  const navLatestDates = results.map((item) => item.nav?.latestDate).filter(Boolean).sort();
  const sourceLatestNavDate = navLatestDates.at(-1) || null;
  const nonEmptyNavStrategyCount = navLatestDates.length;
  const navAtSourceLatestDateStrategyCount = sourceLatestNavDate
    ? results.filter((item) => item.nav?.latestDate === sourceLatestNavDate).length
    : 0;
  const retainedHistoryStrategyIds = results
    .filter((item) => item.history?.retainedBaseline === true)
    .map((item) => item.strategyCode)
    .sort();
  const summary = {
    state: stoppedForLock ? "stopped_for_active_production_lock" : "signed_history_catalog_complete",
    runId,
    startedAt,
    finishedAt: new Date().toISOString(),
    catalogPath,
    catalogBoundary: "The supplied production keyword-union catalog is a lower bound, not a proven official total.",
    endDate,
    requestedStrategyCount: strategies.length,
    resultStrategyCount: results.length,
    completeStrategyCount: results.filter((item) => item.complete).length,
    failedStrategyCount: results.filter((item) => !item.complete).length,
    signalStrategyCount: strategies.filter((item) => item.code.startsWith("SI")).length,
    regularStrategyCount: strategies.filter((item) => !item.code.startsWith("SI")).length,
    authenticationPersisted: false,
    signedHeadersPersisted: false,
    productionDatabaseWritten: false,
    dailyUpdatePipelineTouched: false,
    fallbackRunDir,
    baselineRunDir,
    incrementalOverlapDays: overlapDays,
    signalPageSize,
    regularPageSize,
    requestIdleTimeoutMs,
    requestTotalTimeoutMs,
    requestAttempts,
    sourceLatestNavDate,
    nonEmptyNavStrategyCount,
    navAtSourceLatestDateStrategyCount,
    navAtSourceLatestDateRatio: nonEmptyNavStrategyCount
      ? navAtSourceLatestDateStrategyCount / nonEmptyNavStrategyCount
      : 0,
    retainedHistoryStrategyCount: retainedHistoryStrategyIds.length,
    retainedHistoryStrategyIds,
    incrementalStrategyCount: results.filter((item) => item.nav?.incremental || item.history?.incremental).length,
    bootstrapStrategyCount: results.filter((item) => !item.nav?.incremental && !item.history?.incremental).length,
    downloadedNavRows: results.reduce((sum, item) => sum + Number(item.nav?.downloadedRowCount || 0), 0),
    downloadedHistoryRows: results.reduce((sum, item) => sum + Number(item.history?.downloadedRowCount || 0), 0),
    fallbackRecovered,
    results,
    runDir,
  };
  atomicJson(path.join(runDir, "summary.json"), summary);
  atomicJson(checkpointPath, { ...summary, state: summary.state });
  process.stdout.write(`${JSON.stringify({
    state: summary.state,
    runId: summary.runId,
    requestedStrategyCount: summary.requestedStrategyCount,
    completeStrategyCount: summary.completeStrategyCount,
    failedStrategyCount: summary.failedStrategyCount,
    sourceLatestNavDate: summary.sourceLatestNavDate,
    navAtSourceLatestDateStrategyCount: summary.navAtSourceLatestDateStrategyCount,
    nonEmptyNavStrategyCount: summary.nonEmptyNavStrategyCount,
    retainedHistoryStrategyCount: summary.retainedHistoryStrategyCount,
    retainedHistoryStrategyIds: summary.retainedHistoryStrategyIds,
    failedStrategies: summary.results.filter((item) => !item.complete).map((item) => ({
      strategyCode: item.strategyCode,
      error: item.error || null,
      nav: item.nav || null,
      history: item.history || null,
    })),
    runDir: summary.runDir,
  }, null, 2)}\n`);
  if (stoppedForLock) process.exitCode = 3;
  else if (summary.failedStrategyCount) process.exitCode = 2;
}

function runMainWithKeepAlive(mainFunction, onError) {
  // A pending Promise alone does not keep Node alive.  On Windows the custom
  // HTTPS path can briefly have no referenced socket while a request is being
  // retried, which previously let the process exit 0 with only a checkpoint.
  const keepAlive = setInterval(() => {}, 60_000);
  return Promise.resolve()
    .then(mainFunction)
    .catch(onError)
    .finally(() => clearInterval(keepAlive));
}

if (require.main === module) {
  runMainWithKeepAlive(main, (error) => {
    process.stderr.write(`${JSON.stringify({ state: "error", code: error?.code || null, message: String(error?.message || error) })}\n`);
    process.exitCode = error?.code === "ACTIVE_PRODUCTION_LOCK" ? 3 : 2;
  });
}

module.exports = {
  runMainWithKeepAlive,
  fetchPaged,
  fetchPagedIncremental,
  collectStrategy,
  latestNavDate,
  resultFromFile,
  boundedInteger,
};
