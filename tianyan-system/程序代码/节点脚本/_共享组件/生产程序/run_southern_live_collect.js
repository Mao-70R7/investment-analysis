const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const { redactObject } = require("./southern_utils");

function findProjectRoot(startDir) {
  let current = path.resolve(startDir);
  while (true) {
    if (
      fs.existsSync(path.join(current, "AGENTS.md")) &&
      fs.existsSync(path.join(current, "本机配置", "runtime.local.json"))
    ) return current;
    const parent = path.dirname(current);
    if (parent === current) throw new Error(`Cannot locate AGENTS.md above ${startDir}`);
    current = parent;
  }
}

function resolveWorkspacePath(root, relativeValue, key) {
  const value = String(relativeValue || "").replace(/\\/g, "/");
  if (!value || path.isAbsolute(value) || /^[A-Za-z]:/.test(value) || value.split("/").includes("..")) {
    throw new Error(`Unsafe ${key}: ${relativeValue}`);
  }
  const resolved = path.resolve(root, ...value.split("/"));
  const relative = path.relative(root, resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error(`${key} escapes project root`);
  return resolved;
}

function loadRuntimeLayout(projectRoot) {
  const configPath = path.join(projectRoot, "本机配置", "runtime.local.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8").replace(/^\uFEFF/, ""));
  const rawRoot = resolveWorkspacePath(projectRoot, config.rawRoot || "采集数据/raw", "rawRoot");
  const outputRoot = resolveWorkspacePath(projectRoot, config.outputRoot || "运行状态/outputs", "outputRoot");
  const lockRoot = resolveWorkspacePath(projectRoot, config.lockRoot || "运行状态/locks", "lockRoot");
  const databaseRoot = resolveWorkspacePath(projectRoot, config.databaseRoot || "数据库", "databaseRoot");
  return { rawRoot, outputRoot, lockRoot, databaseRoot };
}

const PROJECT_ROOT = findProjectRoot(__dirname);
const RUNTIME = loadRuntimeLayout(PROJECT_ROOT);
const PROFILE_DIR = path.join(path.dirname(RUNTIME.outputRoot), "state", "southern-profile");
const OUT_DIR = path.join(RUNTIME.rawRoot, "southern", "live_collect");
const DEFAULT_INVENTORY_ROOT = path.join(RUNTIME.rawRoot, "southern", "public_h5");
const MAX_RESPONSE_BYTES = 50 * 1024 * 1024;

function nowId() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function parseArgs(argv) {
  const result = {
    all: false,
    dryRun: false,
    strategyIds: [],
    inventoryPath: null,
    resultPath: null,
    loginWaitSeconds: 60,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--all") result.all = true;
    else if (arg === "--dry-run") result.dryRun = true;
    else if (arg === "--strategy-ids") result.strategyIds = String(argv[++index] || "").split(",").map((x) => x.trim()).filter(Boolean);
    else if (arg === "--inventory") result.inventoryPath = path.resolve(argv[++index] || "");
    else if (arg === "--result-path") result.resultPath = path.resolve(argv[++index] || "");
    else if (arg === "--login-wait-seconds") result.loginWaitSeconds = Math.max(15, Math.min(720, Number(argv[++index] || 60)));
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (result.all && result.strategyIds.length) throw new Error("Use either --all or --strategy-ids, not both.");
  return result;
}

function assertNoProductionRun() {
  const candidates = [
    path.join(RUNTIME.lockRoot, "daily_update.lock"),
    path.join(RUNTIME.databaseRoot, "daily_update.lock"),
    path.join(RUNTIME.lockRoot, "main_db_write.lock"),
  ];
  const ownRunId = String(process.env.SOUTHERN_DAILY_RUN_ID || "").trim();
  const active = candidates.filter((candidate) => {
    if (!fs.existsSync(candidate)) return false;
    if (path.basename(candidate) !== "daily_update.lock" || !ownRunId) return true;
    try {
      const payload = JSON.parse(fs.readFileSync(candidate, "utf8").replace(/^\uFEFF/, ""));
      return String(payload.runId || "").trim() !== ownRunId;
    } catch (_) {
      return true;
    }
  });
  if (active.length) throw new Error(`Production lock is active; collection aborted: ${active.join(", ")}`);
}

function latestInventoryPath() {
  if (!fs.existsSync(DEFAULT_INVENTORY_ROOT)) throw new Error(`Southern inventory root is missing: ${DEFAULT_INVENTORY_ROOT}`);
  const matches = [];
  function walk(current) {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const child = path.join(current, entry.name);
      if (entry.isDirectory()) walk(child);
      else if (entry.isFile() && entry.name === "strategy_inventory.json") matches.push(child);
    }
  }
  walk(DEFAULT_INVENTORY_ROOT);
  if (!matches.length) throw new Error(`No strategy_inventory.json below ${DEFAULT_INVENTORY_ROOT}`);
  return matches.sort((left, right) => fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs)[0];
}

function loadPlanTargets(args) {
  const inventoryPath = args.inventoryPath || latestInventoryPath();
  const payload = JSON.parse(fs.readFileSync(inventoryPath, "utf8").replace(/^\uFEFF/, ""));
  const inventory = Array.isArray(payload.strategies) ? payload.strategies : [];
  const selectedIds = new Set(args.all ? inventory.map((row) => String(row.source_strategy_id)) : (args.strategyIds.length ? args.strategyIds : ["79"]));
  const targets = inventory
    .filter((row) => selectedIds.has(String(row.source_strategy_id)))
    .map((row) => ({
      source_strategy_id: String(row.source_strategy_id),
      strategy_name: row.strategy_name || null,
      sceneno: String(row.sceneno || ""),
    }));
  const found = new Set(targets.map((row) => row.source_strategy_id));
  const missing = [...selectedIds].filter((value) => !found.has(value));
  if (missing.length) throw new Error(`Strategy IDs missing from inventory: ${missing.join(", ")}`);
  if (targets.some((row) => !row.sceneno)) throw new Error("Every strategy target must have sceneno.");
  return { inventoryPath, targets };
}

async function saveArtifact(name, data) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const outPath = path.join(OUT_DIR, `${name}-${nowId()}.json`);
  fs.writeFileSync(outPath, JSON.stringify(redactObject(data), null, 2), "utf8");
  return outPath;
}

async function humanPause(page, minMs = 900, maxMs = 1800) {
  const waitMs = minMs + Math.floor(Math.random() * (maxMs - minMs + 1));
  await page.waitForTimeout(waitMs);
}

async function typeLikeUser(locator, text) {
  await locator.focus({ timeout: 15000 });
  await locator.fill(text, { timeout: 15000 });
}

async function fillSouthernLoginFromEnv(page) {
  const loginId = process.env.SOUTHERN_LOGIN_ID || "";
  const password = process.env.SOUTHERN_LOGIN_PASSWORD || "";
  if (!loginId || !password) return { attempted: false, reason: "missing_env" };

  console.log("[southern-login] login page loaded; waiting for the phone/ID login control");
  await page.bringToFront().catch(() => {});
  await humanPause(page, 1200, 2200);

  const loginInput = page.locator("#loginForm_lognumber").first();
  const passwordInput = page.locator("#loginForm_logpassword").first();
  await loginInput.waitFor({ state: "attached", timeout: 30000 });
  await passwordInput.waitFor({ state: "visible", timeout: 30000 });

  let loginDisabled = await loginInput.evaluate((node) => node.disabled).catch(() => true);
  if (loginDisabled) {
    // The official page intentionally ships the account field as disabled and
    // enables it only from the visible label's click handler.  Selecting the
    // already-current phone/ID login type returns early and does not enable it.
    await page.locator("#lableLT").click({ timeout: 15000 }).catch(() => {});
    await page.waitForFunction(
      () => {
        const input = document.querySelector("#loginForm_lognumber");
        return Boolean(input && !input.disabled);
      },
      { timeout: 30000 },
    ).catch(() => {});
    loginDisabled = await loginInput.evaluate((node) => node.disabled).catch(() => true);
  }
  if (loginDisabled) {
    console.log("[southern-login] phone/ID login control is still disabled; no credentials submitted");
    return { attempted: true, submitted: false, reason: "login_input_disabled" };
  }

  console.log("[southern-login] login control ready; entering the user-provided credentials");
  try {
    await typeLikeUser(loginInput, loginId);
  } catch (_) {
    throw new Error("login_account_entry_failed");
  }
  console.log("[southern-login] account field completed");
  await humanPause(page);
  try {
    await typeLikeUser(passwordInput, password);
  } catch (_) {
    throw new Error("login_password_entry_failed");
  }
  console.log("[southern-login] password field completed");
  await humanPause(page);

  const privacy = page.locator("#privacyPolicy").first();
  if ((await privacy.count().catch(() => 0)) > 0 && !(await privacy.isChecked().catch(() => false))) {
    try {
      await privacy.check({ timeout: 10000, force: true });
    } catch (_) {
      throw new Error("login_privacy_consent_failed");
    }
    await humanPause(page);
  }
  console.log("[southern-login] privacy consent ready");

  const submit = page.locator("#loginForm_submit").first();
  console.log("[southern-login] submitting login form once");
  await submit.click({ timeout: 15000 });
  await humanPause(page, 2500, 4200);

  const pageText = await page.locator("body").innerText().catch(() => "");
  const manualCheckRequired = /验证码|滑块|短信|人脸|扫码|安全验证|二次验证|请输入验证码/.test(pageText);
  console.log(`[southern-login] form submitted; manual verification required=${manualCheckRequired}`);
  return { attempted: true, submitted: true, manual_check_required: manualCheckRequired };
}

async function pageSummary(page, limit = 6000) {
  const bodyText = await page.locator("body").innerText().catch(() => "");
  const links = await page
    .locator("a, button, input[type=button], input[type=submit], input[type=checkbox], input[type=radio]")
    .evaluateAll((nodes) =>
      nodes.slice(0, 500).map((node) => ({
        tag: node.tagName,
        type: node.getAttribute("type"),
        text: (node.innerText || node.value || node.getAttribute("aria-label") || "").trim(),
        href: node.href || null,
        id: node.id || null,
        name: node.getAttribute("name"),
        class_name: node.className || null,
        checked: node.checked || false,
        onclick: node.getAttribute("onclick"),
      })),
    )
    .catch(() => []);
  const scripts = await page.locator("script[src]").evaluateAll((nodes) => nodes.map((node) => node.src)).catch(() => []);
  return {
    title: await page.title(),
    url: page.url(),
    text_sample: bodyText.slice(0, limit),
    links,
    scripts,
  };
}

async function waitForLogin(page, waitSeconds) {
  const startedAt = Date.now();
  const maxWaitMs = Math.max(15, Number(waitSeconds || 60)) * 1000;
  while (Date.now() - startedAt < maxWaitMs) {
    await page.waitForTimeout(2000);
    const url = page.url();
    const text = await page.locator("body").innerText().catch(() => "");
    const loggedIn = /安全退出/.test(text) || (await page.locator("#logout_btn, a.top_user_name").count().catch(() => 0)) > 0;
    if (loggedIn && !/account\/login/i.test(url)) return true;
  }
  return false;
}

async function ensureTokenPage(page) {
  const candidateUrls = [
    "https://trade.southernfund.com/new/go?menuId=10000",
    "https://trade.southernfund.com/new/account/main/init?menuId=10000",
    "https://trade.southernfund.com/new/account/main/init",
  ];
  for (const url of candidateUrls) {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(3500);
    if (/account\/login/i.test(page.url())) continue;
    const hasTokenLink = (await page.locator('a[href*="SECURE_TOKEN"]').count().catch(() => 0)) > 0;
    if (hasTokenLink) break;
  }

  const userHref = await page
    .locator('a.top_user_name[href*="SECURE_TOKEN"], a[href*="/usercenter/personinfo/init"][href*="SECURE_TOKEN"]')
    .first()
    .getAttribute("href")
    .catch(() => null);
  if (userHref) {
    await page.goto(new URL(userHref, page.url()).toString(), { waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(2500);
  }
}

async function fetchWebIaInfo(page) {
  return await page.evaluate(async () => {
    const token = window.$hs_secure_token || new URL(location.href).searchParams.get("SECURE_TOKEN") || "";
    async function post(path) {
      const url = `/new${path}${path.includes("?") ? "&" : "?"}SECURE_TOKEN=${encodeURIComponent(token)}`;
      const res = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      return {
        url,
        status: res.status,
        content_type: res.headers.get("content-type"),
        text: await res.text(),
      };
    }
    return {
      location: location.href,
      token_length: token.length,
      check: await post("/webia/account/checkwebIAaccount"),
      info: await post("/webia/account/webIAqueryinfo"),
    };
  });
}

async function clickAdvisorMenu(page) {
  const count = await page.locator("a.iainvestMenuBtn").count().catch(() => 0);
  if (count > 0) {
    await page.locator("a.iainvestMenuBtn").first().click({ timeout: 15000 });
    await page.waitForTimeout(6000);
    return true;
  }
  const tokenHref = await page.locator('a[href*="SECURE_TOKEN"]').first().getAttribute("href").catch(() => null);
  const token = tokenHref ? new URL(tokenHref, page.url()).searchParams.get("SECURE_TOKEN") : null;
  if (token) {
    await page.goto(`https://trade.southernfund.com/new/iainvest/init?menuId=80000&SECURE_TOKEN=${token}`, {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });
    await page.waitForTimeout(5000);
    return true;
  }
  return false;
}

async function safeExploreStart(page) {
  const startHref = await page.locator("#iainvestBtn, a[href*='iainvest/init_inner']").first().getAttribute("href").catch(() => null);
  if (!startHref) return { start_href: null, page: null };

  await page.goto(new URL(startHref, page.url()).toString(), { waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(5000);
  return {
    start_href: startHref,
    page: await pageSummary(page, 10000),
  };
}

function secureTokenFromPage(page) {
  const current = new URL(page.url());
  return current.searchParams.get("SECURE_TOKEN");
}

async function findSecureToken(page) {
  const fromUrl = secureTokenFromPage(page);
  if (fromUrl) return fromUrl;
  const tokenHref = await page.locator('a[href*="SECURE_TOKEN"]').first().getAttribute("href").catch(() => null);
  return tokenHref ? new URL(tokenHref, page.url()).searchParams.get("SECURE_TOKEN") : null;
}

async function capturePlanDetail(page, target, token, events) {
  const startIndex = events.length;
  const url = new URL("https://trade.southernfund.com/new/iainvest/scene6");
  url.searchParams.set("menuId", "80000");
  url.searchParams.set("combcode", target.source_strategy_id);
  url.searchParams.set("sceneno", target.sceneno);
  url.searchParams.set("SECURE_TOKEN", token);
  await page.goto(url.toString(), { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForTimeout(8000);
  const planEvents = events.slice(startIndex);
  const required = ["webIAqueryCombInfo", "webIAcombFundMarketQuery"];
  const missing = required.filter((name) => !planEvents.some((event) => String(event.url || "").includes(name) && event.response_text));
  const artifact = {
    captured_at: new Date().toISOString(),
    strategy: target,
    page_url: page.url(),
    page: await pageSummary(page, 3000),
    events: planEvents,
    validation: {
      required_response_names: required,
      missing_required_responses: missing,
      passed: missing.length === 0,
    },
  };
  const outPath = await saveArtifact(`southern_plan_detail-${target.source_strategy_id}`, artifact);
  return { source_strategy_id: target.source_strategy_id, output_path: outPath, validation: artifact.validation };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  assertNoProductionRun();
  const plan = loadPlanTargets(args);
  if (args.dryRun) {
    console.log(JSON.stringify({
      dry_run: true,
      project_root: PROJECT_ROOT,
      output_dir: OUT_DIR,
      inventory_path: plan.inventoryPath,
      target_count: plan.targets.length,
      targets: plan.targets,
    }, null, 2));
    return;
  }
  fs.mkdirSync(PROFILE_DIR, { recursive: true });
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const browserExecutable = String(process.env.SOUTHERN_BROWSER_EXECUTABLE || "").trim();
  if (browserExecutable && !fs.existsSync(browserExecutable)) {
    throw new Error(`Configured SOUTHERN_BROWSER_EXECUTABLE does not exist: ${browserExecutable}`);
  }
  const launchOptions = {
    headless: false,
    viewport: { width: 1365, height: 900 },
  };
  if (browserExecutable) launchOptions.executablePath = browserExecutable;
  const context = await chromium.launchPersistentContext(PROFILE_DIR, launchOptions);
  await context.route(/cconline|piwik|wap\.southernfund/, (route) => route.abort());
  // Persistent profiles may restore stale login/advisor tabs. Always bind the
  // collector to a fresh page so navigation cannot follow another restored tab.
  const page = await context.newPage();
  const events = [];

  page.on("response", async (response) => {
    const request = response.request();
    const url = response.url();
    if (!url.includes("southernfund.com")) return;
    if (!/webia|iainvest|comb|portfolio|fund|strategy|asset|risk|question|plan|scene|query/i.test(url)) return;
    const contentType = response.headers()["content-type"] || null;
    const item = {
      at: new Date().toISOString(),
      status: response.status(),
      method: request.method(),
      url,
      post_data: request.postData(),
      request_headers: {
        referer: request.headers().referer || null,
        "x-requested-with": request.headers()["x-requested-with"] || null,
        "content-type": request.headers()["content-type"] || null,
      },
      response_content_type: contentType,
      response_text: null,
    };
    const responseBodyAllowed = /webIAqueryCombInfo|webIAcombFundMarketQuery|webIAqueryTradeRate|ia_report/i.test(url);
    if (responseBodyAllowed && ((contentType || "").includes("json") || (contentType || "").includes("text"))) {
      try {
        const responseText = await response.text();
        item.response_bytes = Buffer.byteLength(responseText, "utf8");
        item.response_truncated = item.response_bytes > MAX_RESPONSE_BYTES;
        item.response_text = item.response_truncated ? null : responseText;
      } catch (_) {
        item.response_text = null;
      }
    }
    events.push(item);
  });

  const hasLoginCredentials = Boolean(process.env.SOUTHERN_LOGIN_ID && process.env.SOUTHERN_LOGIN_PASSWORD);
  const entryUrl = hasLoginCredentials
    ? "https://trade.southernfund.com/new/account/login/init?from=web&url=%2Fiainvest%2Finit%3FmenuId%3D80000"
    : "https://trade.southernfund.com/new/account/main/init?menuId=10000";
  await page.goto(entryUrl, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  }).catch(() => {});

  const autoLogin = await fillSouthernLoginFromEnv(page).catch((error) => ({
    attempted: true,
    submitted: false,
    reason: "autofill_error",
    error: error.message,
  }));
  if (autoLogin.attempted) {
    console.log(JSON.stringify(redactObject({
      auto_login_attempted: true,
      submitted: autoLogin.submitted || false,
      reason: autoLogin.reason || null,
      error_code: autoLogin.error || null,
      manual_check_required: autoLogin.manual_check_required || false,
    })));
    if (autoLogin.manual_check_required) {
      console.log("Manual verification is shown in the browser. Complete it there; the script will keep waiting for login state.");
    }
  }

  console.log("Log in in the browser if needed. I will continue in this same browser session.");
  const loggedIn = await waitForLogin(page, args.loginWaitSeconds);
  if (!loggedIn) {
    const failure = {
      status: "auth_required",
      captured_at: new Date().toISOString(),
      page: await pageSummary(page),
      login_attempt: redactObject(autoLogin),
      events,
    };
    const outPath = await saveArtifact("live_login_not_detected", failure);
    if (args.resultPath) {
      fs.mkdirSync(path.dirname(args.resultPath), { recursive: true });
      fs.writeFileSync(args.resultPath, JSON.stringify(redactObject(failure), null, 2), "utf8");
    }
    console.log(`Login was not detected. Saved: ${outPath}`);
    await context.close();
    process.exit(2);
  }

  await ensureTokenPage(page);
  const tokenPage = await pageSummary(page);
  let webIaInfo = null;
  try {
    webIaInfo = await fetchWebIaInfo(page);
  } catch (error) {
    webIaInfo = { error: error.message };
  }

  await clickAdvisorMenu(page);
  const advisorInitPage = await pageSummary(page, 10000);
  const inner = await safeExploreStart(page);
  await ensureTokenPage(page);
  const secureToken = await findSecureToken(page);
  if (!secureToken) throw new Error("Authenticated SECURE_TOKEN was not found; plan detail collection cannot continue.");
  const planResults = [];
  for (const target of plan.targets) {
    assertNoProductionRun();
    planResults.push(await capturePlanDetail(page, target, secureToken, events));
  }

  const result = {
    status: "success",
    captured_at: new Date().toISOString(),
    token_page: tokenPage,
    webia_info: webIaInfo,
    advisor_init_page: advisorInitPage,
    inner,
    inventory_path: plan.inventoryPath,
    plan_results: planResults,
    event_summary: events.map((event) => ({
      at: event.at,
      status: event.status,
      method: event.method,
      url: event.url,
      response_content_type: event.response_content_type,
      response_bytes: event.response_bytes || null,
      response_truncated: event.response_truncated || false,
    })),
  };
  const outPath = await saveArtifact("southern_live_collect", result);
  if (args.resultPath) {
    fs.mkdirSync(path.dirname(args.resultPath), { recursive: true });
    fs.writeFileSync(args.resultPath, JSON.stringify(redactObject(result), null, 2), "utf8");
  }
  console.log(JSON.stringify({ output_path: outPath, summary: redactObject({
    token_url: tokenPage.url,
    webia_info: webIaInfo,
    advisor_url: advisorInitPage.url,
    advisor_text: advisorInitPage.text_sample.slice(0, 1000),
    inner_url: inner.page && inner.page.url,
      inner_text: inner.page && inner.page.text_sample.slice(0, 2000),
      event_count: events.length,
      plan_results: planResults,
  })}, null, 2));

  await context.close();
}

main().catch(async (error) => {
  console.error(error);
  process.exit(1);
});
