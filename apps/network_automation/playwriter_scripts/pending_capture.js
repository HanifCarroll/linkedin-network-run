const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const sentUrl = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";

function nowIso() {
  return new Date().toISOString();
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((candidatePage) => candidatePage.url().includes("linkedin.com/mynetwork/invitation-manager/sent")) ||
    pages.find((candidatePage) => candidatePage.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

function parseAgeDays(ageText) {
  const value = String(ageText || "").toLowerCase();
  const match = value.match(/sent\s+(\d+)\s+(day|week|month|year)s?\s+ago/);
  if (!match) return null;
  const amount = Number(match[1]);
  if (match[2] === "day") return amount;
  if (match[2] === "week") return amount * 7;
  if (match[2] === "month") return amount * 30;
  return amount * 365;
}

async function classifyBlock(page) {
  const url = page.url();
  if (/\/login|\/uas\/login/i.test(url)) return { status: "login", reason: "login required" };
  if (/\/checkpoint/i.test(url)) return { status: "checkpoint", reason: "checkpoint present" };
  const security = await page.locator("iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']").count().catch(() => 0);
  if (security > 0) return { status: "security", reason: "security verification present" };
  return null;
}

async function loadMore(page) {
  for (let index = 0; index < Number(config.loadMore || 0); index += 1) {
    const button = page.getByRole("button", { name: /^Load more$/i }).first();
    if ((await button.count().catch(() => 0)) === 0) break;
    if (await button.isDisabled().catch(() => true)) break;
    await button.click({ timeout: 8000 });
    await page.waitForTimeout(1000);
  }
}

const page = await getPage();
await page.goto(sentUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
const block = await classifyBlock(page);
if (block) {
  fs.writeFileSync(config.out, `${JSON.stringify({ capturedAt: nowIso(), url: page.url(), status: block.status, rows: [] }, null, 2)}\n`);
} else {
  await loadMore(page);
  const rows = await page.locator("a[aria-label^='Withdraw invitation sent to']").evaluateAll((links, thresholdDays) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    return links.map((link, index) => {
      const label = link.getAttribute("aria-label") || "";
      const name = label.startsWith("Withdraw invitation sent to ")
        ? label.replace("Withdraw invitation sent to ", "").trim()
        : null;
      let cursor = link;
      let rowText = clean(link.textContent || "");
      while (cursor && cursor !== document.body) {
        const text = clean(cursor.textContent || "");
        if (name && text.includes(name) && /Sent\s+\d+\s+(day|week|month|year)s?\s+ago/i.test(text)) {
          rowText = text;
          break;
        }
        cursor = cursor.parentElement;
      }
      const ageMatch = rowText.match(/Sent\s+\d+\s+(?:day|week|month|year)s?\s+ago/i);
      const profile = link.closest("li, div")?.querySelector("a[href*='/in/']")?.href || null;
      return { index, name, profileUrl: profile, ageText: ageMatch ? ageMatch[0] : null, rowText };
    }).filter((row) => row.name && row.ageText);
  }, Number(config.thresholdDays || 14));
  const enriched = rows.map((row) => {
    const ageDays = parseAgeDays(row.ageText);
    return {
      ...row,
      ageMonths: ageDays === null ? null : Math.floor(ageDays / 30),
      ageDays,
      eligible: ageDays !== null && ageDays >= Number(config.thresholdDays || 14),
    };
  });
  fs.writeFileSync(config.out, `${JSON.stringify({ capturedAt: nowIso(), url: page.url(), thresholdDays: Number(config.thresholdDays || 14), rows: enriched }, null, 2)}\n`);
}
