"use strict";

const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const INSPECTOR_PORT = Number(process.env.QIEMAN_INSPECTOR_PORT || 9229);
const AUTH_PORT = Number(process.env.QIEMAN_AUTH_PORT || 43912);
const OUTPUT_PATH = String(process.env.QIEMAN_DPAPI_OUTPUT || "").trim();

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(2);
}

function persistWithDpapi(secret, outputPath) {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  const script = [
    "$ErrorActionPreference='Stop'",
    "[void][Reflection.Assembly]::LoadWithPartialName('System.Security')",
    "$secret=[Console]::In.ReadToEnd()",
    "$bytes=[Text.Encoding]::UTF8.GetBytes($secret)",
    "$entropy=[Text.Encoding]::UTF8.GetBytes('advisor-monitor:qieman-stargate:v1')",
    "$encrypted=[System.Security.Cryptography.ProtectedData]::Protect($bytes,$entropy,[System.Security.Cryptography.DataProtectionScope]::CurrentUser)",
    "[IO.File]::WriteAllText($env:QIEMAN_DPAPI_TARGET,[Convert]::ToBase64String($encrypted),[Text.Encoding]::ASCII)",
  ].join(";");
  const completed = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    {
      input: secret,
      encoding: "utf8",
      windowsHide: true,
      env: { ...process.env, QIEMAN_DPAPI_TARGET: outputPath },
    }
  );
  if (completed.status !== 0) {
    throw new Error(`DPAPI persistence failed: ${String(completed.stderr || "").trim()}`);
  }
  fs.writeFileSync(
    `${outputPath}.meta.json`,
    `${JSON.stringify({ provider: "Windows DPAPI CurrentUser", createdAt: new Date().toISOString(), secretPrinted: false }, null, 2)}\n`,
    "utf8"
  );
}

async function main() {
  if (!OUTPUT_PATH) fail("QIEMAN_DPAPI_OUTPUT is required");
  const targets = await fetch(`http://127.0.0.1:${INSPECTOR_PORT}/json/list`).then((response) => response.json());
  const target = targets.find((item) => item.webSocketDebuggerUrl);
  if (!target) fail("Node inspector target not found");

  const socket = new WebSocket(target.webSocketDebuggerUrl);
  let nextId = 1;
  const pending = new Map();
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });

  let persisted = false;
  socket.onmessage = async (event) => {
    const message = JSON.parse(String(event.data));
    if (message.id && pending.has(message.id)) {
      const waiter = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message.result);
      return;
    }
    if (message.method !== "Debugger.paused" || persisted) return;
    try {
      const frame = message.params.callFrames[0];
      const evaluated = await call("Debugger.evaluateOnCallFrame", {
        callFrameId: frame.callFrameId,
        expression: "apiKey",
        returnByValue: true,
      });
      const secret = evaluated && evaluated.result && evaluated.result.value;
      if (typeof secret !== "string" || secret.length < 16) throw new Error("API key was not available in the paused frame");
      persistWithDpapi(secret, OUTPUT_PATH);
      persisted = true;
      await call("Debugger.resume");
      process.stdout.write(`${JSON.stringify({ state: "dpapi_key_persisted", outputPath: OUTPUT_PATH, secretPrinted: false })}\n`);
      socket.close();
    } catch (error) {
      try {
        await call("Debugger.resume");
      } catch {}
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 2;
      socket.close();
    }
  };

  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = () => reject(new Error("inspector websocket connection failed"));
  });
  await call("Debugger.enable");
  await call("Debugger.setBreakpointByUrl", {
    lineNumber: 262,
    urlRegex: "qieman_stargate_sms_session\\.js$",
  });

  const trigger = net.createConnection({ host: "127.0.0.1", port: AUTH_PORT }, () => {
    trigger.end('{"action":"describe"}');
  });
  trigger.on("data", () => {});
  trigger.on("error", (error) => fail(`local session trigger failed: ${error.message}`));

  await new Promise((resolve) => socket.addEventListener("close", resolve, { once: true }));
  if (!persisted) fail("API key was not persisted");
}

main().catch((error) => fail(error.message));
