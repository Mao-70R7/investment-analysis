const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const { redactObject } = require("./southern_utils");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const PROFILE_DIR = path.join(PROJECT_ROOT, "data", "state", "southern-profile");
const OUT_DIR = path.join(PROJECT_ROOT, "data", "raw", "southern", "live_collect");

function nowId() {
  return new Date().toISOString().replace(/[:.]/g, "-");
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
  await locator.click({ timeout: 15000 });
  await locator.press(process.platform === "darwin" ? "Meta+A" : "Control+A").catch(() => {});
  await locator.type(text, { delay: 120 + Math.floor(Math.random() * 90) });
}

async function fillSouthernLoginFromEnv(page) {
  const loginId = process.env.SOUTHERN_LOGIN_ID || "";
  const password = process.env.SOUTHERN_LOGIN_PASSWORD || "";
  if (!loginId || !password) return { attempted: false, reason: "missing_env" };

  await page.bringToFront().catch(() => {});
  await humanPause(page, 1200, 2200);

  const loginInput = page.locator("#loginForm_lognumber").first();
  const passwordInput = page.locator("#loginForm_logpassword").first();
  await loginInput.waitFor({ state: "attached", timeout: 30000 });
  await passwordInput.waitFor({ state: "visible", timeout: 30000 });

  const loginDisabled = await loginInput.evaluate((node) => node.disabled).catch(() => true);
  if (loginDisabled) {
    return { attempted: true, submitted: false, reason: "login_input_disabled" };
  }

  await typeLikeUser(loginInput, loginId);
  await humanPause(page);
  await typeLikeUser(passwordInput, password);
  await humanPause(page);

  const privacy = page.locator("#privacyPolicy").first();
  if ((await privacy.count().catch(() => 0)) > 0 && !(await privacy.isChecked().catch(() => false))) {
    await privacy.check({ timeout: 10000 });
    await humanPause(page);
  }

  const submit = page.locator("#loginForm_submit").first();
  await submit.click({ timeout: 15000 });
  await humanPause(page, 2500, 4200);

  const pageText = await page.locator("body").innerText().catch(() => "");
  const manualCheckRequired = /验证码|滑块|短信|人脸|扫码|安全验证|二次验证|请输入验证码/.test(pageText);
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

async function waitForLogin(page) {
  const startedAt = Date.now();
  const maxWaitMs = 12 * 60 * 1000;
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

async function main() {
  fs.mkdirSync(PROFILE_DIR, { recursive: true });
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,
    viewport: { width: 1365, height: 900 },
  });
  await context.route(/cconline|piwik|wap\.southernfund/, (route) => route.abort());
  const page = context.pages()[0] || (await context.newPage());
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
    if ((contentType || "").includes("json") || (contentType || "").includes("text")) {
      try {
        item.response_text = (await response.text()).slice(0, 8000);
      } catch (_) {
        item.response_text = null;
      }
    }
    events.push(item);
  });

  await page.goto("https://trade.southernfund.com/new/account/login/init?from=web&url=%2Fiainvest%2Finit%3FmenuId%3D80000", {
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
      manual_check_required: autoLogin.manual_check_required || false,
    })));
    if (autoLogin.manual_check_required) {
      console.log("Manual verification is shown in the browser. Complete it there; the script will keep waiting for login state.");
    }
  }

  console.log("Log in in the browser if needed. I will continue in this same browser session.");
  const loggedIn = await waitForLogin(page);
  if (!loggedIn) {
    const outPath = await saveArtifact("live_login_not_detected", {
      captured_at: new Date().toISOString(),
      page: await pageSummary(page),
      events,
    });
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

  const result = {
    captured_at: new Date().toISOString(),
    token_page: tokenPage,
    webia_info: webIaInfo,
    advisor_init_page: advisorInitPage,
    inner,
    events,
  };
  const outPath = await saveArtifact("southern_live_collect", result);
  console.log(JSON.stringify({ output_path: outPath, summary: redactObject({
    token_url: tokenPage.url,
    webia_info: webIaInfo,
    advisor_url: advisorInitPage.url,
    advisor_text: advisorInitPage.text_sample.slice(0, 1000),
    inner_url: inner.page && inner.page.url,
    inner_text: inner.page && inner.page.text_sample.slice(0, 2000),
    event_count: events.length,
  })}, null, 2));

  await context.close();
}

main().catch(async (error) => {
  console.error(error);
  process.exit(1);
});
