const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const SENT_INVITATIONS_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";
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
    pages.find((item) => item.url().includes("linkedin.com/mynetwork/invitation-manager/sent")) ||
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
  await activePage.goto(SENT_INVITATIONS_URL, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
  const block = await blockedReason(activePage);
  if (block) throw new Error(`sent invitations audit blocked: ${block}`);
  for (let index = 0; index < Math.max(0, Number(config.loadMore || 0)); index += 1) {
    const button = activePage.getByRole("button", { name: /^Load more$/i }).first();
    if (!(await button.count().catch(() => 0))) break;
    if (await button.isDisabled().catch(() => false)) break;
    await button.click({ timeout: 8000 });
    await activePage.waitForTimeout(1500);
  }
  const workspace = activePage.locator("main#workspace").first();
  const text = await workspace.textContent({ timeout: 10000 });
  const match = clean(text).match(/People \(([\d,]+)\)/);
  if (!match) throw new Error("could not parse People (N) count from sent invitations page");
  const links = await activePage.locator("a[aria-label^='Withdraw invitation sent to']").all();
  const names = [];
  for (const link of links) {
    const label = await link.getAttribute("aria-label").catch(() => null);
    if (label && label.startsWith("Withdraw invitation sent to ") && names.length < 100) {
      names.push(label.replace("Withdraw invitation sent to ", "").trim());
    }
  }
  const payload = {
    capturedAt: nowIso(),
    url: activePage.url(),
    peopleCount: Number(match[1].replace(/,/g, "")),
    recentNames: names,
  };
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`wrote Sales Navigator audit to ${config.out}`);
}

await main();
