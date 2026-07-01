const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const SECURITY_VERIFICATION_SELECTOR =
  "iframe#humanThirdPartyIframe,iframe[title='LinkedIn security verification'],iframe[src*='li.protechts.net']";

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function nowIso() {
  return new Date().toISOString();
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) return state.linkedinToolsPage;
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((item) => item.url().includes("linkedin.com/sales/search/people")) ||
    pages.find((item) => item.url().includes("linkedin.com/sales")) ||
    pages.find((item) => item.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

async function visibleCount(page, selector) {
  const locator = page.locator(selector);
  const count = await locator.count().catch(() => 0);
  let visible = 0;
  for (let index = 0; index < count; index += 1) {
    if (await locator.nth(index).isVisible().catch(() => false)) visible += 1;
  }
  return visible;
}

async function firstVisible(locator) {
  const count = await locator.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    const item = locator.nth(index);
    if (await item.isVisible().catch(() => false)) return item;
  }
  return null;
}

async function savedSearchesControl(page) {
  return (
    await firstVisible(page.getByRole("button", { name: /Saved searches/i })) ||
    await firstVisible(page.getByRole("tab", { name: /Saved searches/i })) ||
    await firstVisible(page.getByRole("link", { name: /Saved searches/i })) ||
    await firstVisible(page.locator("text=/^\\s*Saved searches\\s*$/i"))
  );
}

async function blockedReason(page) {
  const url = page.url();
  if (/\/login|\/uas\/login/i.test(url)) return "login required";
  if (/\/checkpoint/i.test(url)) return "checkpoint present";
  if ((await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']")) > 0) return "login required";
  if ((await visibleCount(page, "input[name='pin'], input[name='challengeId']")) > 0) return "checkpoint present";
  if ((await visibleCount(page, SECURITY_VERIFICATION_SELECTOR)) > 0) return "security verification present";
  return null;
}

async function main() {
  const activePage = await getPage();
  const navigationTimeoutMs = Number(config.navigationTimeoutMs || 45000);
  await activePage.goto(config.url, { waitUntil: "domcontentloaded", timeout: navigationTimeoutMs });
  await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
  const block = await blockedReason(activePage);
  if (block) throw new Error(`saved searches blocked: ${block}`);
  const control = await savedSearchesControl(activePage);
  if (!control) {
    throw new Error(
      "saved-searches control missing; verify the automation browser is logged into Sales Navigator with the expected LinkedIn profile"
    );
  }
  try {
    await control.click({ timeout: 10000 });
  } catch {
    await control.evaluate((element) => element.click());
  }
  await activePage.waitForTimeout(1500);
  const anchors = await activePage.locator("a[href*='savedSearchId=']").all();
  const byId = new Map();
  for (const anchor of anchors) {
    const href = await anchor.getAttribute("href").catch(() => null);
    if (!href) continue;
    let parsed;
    try {
      parsed = new URL(href, "https://www.linkedin.com");
    } catch {
      continue;
    }
    const savedSearchId = parsed.searchParams.get("savedSearchId");
    if (!savedSearchId) continue;
    const absoluteHref = parsed.href;
    const text = clean(await anchor.textContent().catch(() => ""));
    const aria = await anchor.getAttribute("aria-label").catch(() => "");
    const ariaName = clean(aria).match(/(?:View |results for )(.+?)(?: lead saved search| since|$)/);
    const textName = text.match(/(?:Go to \d+[,\dK+]* new results for |View )(.+?)(?: since | lead saved search|$)/);
    const knownName = (ariaName && ariaName[1] ? ariaName[1].trim() : null) ||
      (textName && textName[1] ? textName[1].trim() : null) ||
      text ||
      null;
    const existing = byId.get(savedSearchId) || {
      savedSearchId,
      name: knownName,
      viewUrl: null,
      freshUrl: null,
      freshText: null,
      rowText: text,
    };
    if (absoluteHref.includes("lastViewedAt=")) {
      existing.freshUrl = absoluteHref;
      existing.freshText = text;
    } else {
      existing.viewUrl = absoluteHref;
    }
    existing.name = existing.name || knownName;
    byId.set(savedSearchId, existing);
  }
  const payload = {
    capturedAt: nowIso(),
    url: activePage.url(),
    searches: Array.from(byId.values()),
  };
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`wrote Sales Navigator saved searches to ${config.out}`);
}

await main();
