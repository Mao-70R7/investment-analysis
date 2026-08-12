"use strict";

const https = require("node:https");
const { spawnSync } = require("node:child_process");

const addressCache = new Map();

function codedError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function resolveIpv4WithWindows(hostname, refresh = false) {
  if (!refresh && addressCache.has(hostname)) return addressCache.get(hostname);
  const script = [
    "$ErrorActionPreference='Stop'",
    "$rows=Resolve-DnsName -Name $env:QIEMAN_DNS_HOST -Type A -DnsOnly -QuickTimeout -ErrorAction Stop",
    "$rows | Where-Object { $_.IPAddress -match '^\\d+\\.\\d+\\.\\d+\\.\\d+$' } | Select-Object -ExpandProperty IPAddress -Unique",
  ].join(";");
  const completed = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    {
      encoding: "utf8",
      windowsHide: true,
      timeout: 20000,
      env: { ...process.env, QIEMAN_DNS_HOST: hostname },
    }
  );
  if (completed.status !== 0) {
    throw new Error(`Windows DNS resolution failed for ${hostname}: ${String(completed.stderr || "").trim()}`);
  }
  const addresses = String(completed.stdout || "")
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter((value) => /^\d+\.\d+\.\d+\.\d+$/.test(value));
  if (!addresses.length) throw new Error(`Windows DNS resolution returned no IPv4 address for ${hostname}`);
  addressCache.set(hostname, [...new Set(addresses)]);
  return addressCache.get(hostname);
}

function requestAddress(target, address, options, idleTimeoutMs, maxBytes, totalTimeoutMs = idleTimeoutMs + 1000) {
  return new Promise((resolve, reject) => {
    const headers = { ...(options.headers || {}), host: target.host };
    let request = null;
    let deadline = null;
    let settled = false;
    let responseEnded = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      if (deadline) clearTimeout(deadline);
      callback(value);
    };
    const fail = (error) => finish(reject, error instanceof Error ? error : new Error(String(error)));
    const succeed = (value) => finish(resolve, value);
    try {
      request = https.request(
        {
          protocol: "https:",
          hostname: address,
          port: target.port || 443,
          servername: target.hostname,
          path: `${target.pathname}${target.search}`,
          method: options.method || "GET",
          headers,
          agent: false,
        },
        (response) => {
          const chunks = [];
          let bytes = 0;
          response.on("data", (chunk) => {
            bytes += chunk.length;
            if (bytes > maxBytes) {
              const error = codedError("HTTPS_RESPONSE_TOO_LARGE", `response exceeded ${maxBytes} bytes`);
              request.destroy(error);
              fail(error);
              return;
            }
            chunks.push(chunk);
          });
          response.on("end", () => {
            responseEnded = true;
            succeed({
              status: Number(response.statusCode || 0),
              headers: response.headers,
              text: Buffer.concat(chunks).toString("utf8"),
            });
          });
          response.on("aborted", () => fail(codedError("HTTPS_RESPONSE_ABORTED", "HTTPS response aborted before completion")));
          response.on("error", fail);
          response.on("close", () => {
            if (!responseEnded) fail(codedError("HTTPS_RESPONSE_CLOSED", "HTTPS response closed before completion"));
          });
        }
      );
    } catch (error) {
      fail(error);
      return;
    }
    const idleTimeoutError = () => codedError("HTTPS_IDLE_TIMEOUT", `HTTPS idle timeout after ${idleTimeoutMs}ms`);
    request.setTimeout(idleTimeoutMs, () => {
      const error = idleTimeoutError();
      request.destroy(error);
      fail(error);
    });
    request.on("error", fail);
    request.on("close", () => {
      if (!responseEnded) fail(codedError("HTTPS_REQUEST_CLOSED", "HTTPS request closed before response completion"));
    });
    // request.setTimeout starts after a socket is assigned.  Keep a separate
    // wall-clock deadline so connect/close edge cases cannot leave the Promise
    // pending forever with no active network handle.
    deadline = setTimeout(() => {
      const error = codedError("HTTPS_TOTAL_TIMEOUT", `HTTPS total timeout after ${totalTimeoutMs}ms`);
      request.destroy(error);
      fail(error);
    }, totalTimeoutMs);
    try {
      if (options.body !== undefined && options.body !== null) request.write(options.body);
      request.end();
    } catch (error) {
      request.destroy();
      fail(error);
    }
  });
}

async function requestText(url, options = {}, config = {}) {
  const target = new URL(url);
  if (target.protocol !== "https:") throw new Error(`unsupported protocol: ${target.protocol}`);
  const timeoutMs = Number(config.timeoutMs || 30000);
  const totalTimeoutMs = Number(config.totalTimeoutMs || timeoutMs + 1000);
  const maxBytes = Number(config.maxBytes || 32 * 1024 * 1024);
  const maxRedirects = Number(config.maxRedirects ?? 3);
  const attempts = Number(config.attempts || 2);
  let lastError = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let addresses;
    try {
      addresses = resolveIpv4WithWindows(target.hostname, attempt > 0);
    } catch (error) {
      lastError = error;
      continue;
    }
    for (const address of addresses) {
      try {
        const response = await requestAddress(target, address, options, timeoutMs, maxBytes, totalTimeoutMs);
        if ([301, 302, 303, 307, 308].includes(response.status) && response.headers.location && maxRedirects > 0) {
          return requestText(new URL(response.headers.location, target).toString(), options, {
            ...config,
            maxRedirects: maxRedirects - 1,
          });
        }
        return response;
      } catch (error) {
        lastError = error;
      }
    }
  }
  throw lastError || new Error(`HTTPS request failed for ${target.hostname}`);
}

module.exports = { requestAddress, requestText, resolveIpv4WithWindows };
