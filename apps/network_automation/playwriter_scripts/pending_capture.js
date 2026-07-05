const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const sentUrl = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";
const agePattern =
  /Sent\s+(?:(?:\d+\s+)?(?:minute|hour|day|week|month|year)s?\s+ago|today|yesterday)/i;

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
  if (/\bsent\s+today\b/.test(value)) return 0;
  if (/\bsent\s+yesterday\b/.test(value)) return 1;
  const match = value.match(/sent\s+(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago/);
  if (!match) return null;
  const amount = Number(match[1]);
  if (match[2] === "minute" || match[2] === "hour") return 0;
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
  const button = page.getByRole("button", { name: /^Load more$/i }).first();
  if ((await button.count().catch(() => 0)) > 0 && !(await button.isDisabled().catch(() => true))) {
    await button.click({ timeout: 8000 });
    await page.waitForTimeout(1000);
    return "load-more-click";
  }
  const scroll = await page.evaluate(() => {
    const workspace = document.querySelector("main#workspace");
    const candidates = [
      workspace,
      workspace?.parentElement,
      document.scrollingElement,
      document.documentElement,
      document.body,
    ].filter(Boolean);
    let target = candidates[0];
    for (const candidate of candidates) {
      if (candidate.scrollHeight > candidate.clientHeight) {
        target = candidate;
        break;
      }
    }
    const before = target.scrollTop;
    const step = Math.max(600, Math.floor(target.clientHeight * 0.85));
    target.scrollTop = Math.min(target.scrollHeight - target.clientHeight, before + step);
    return {
      before,
      after: target.scrollTop,
      max: target.scrollHeight - target.clientHeight,
    };
  });
  await page.waitForTimeout(1000);
  if (scroll.after > scroll.before) return "scroll";
  return "no-more-content";
}

async function captureVisibleRows(page) {
  return await page.locator("a[aria-label^='Withdraw invitation sent to']").evaluateAll((links, agePatternSource) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const pattern = new RegExp(agePatternSource, "i");
    return links.map((link, index) => {
      const label = link.getAttribute("aria-label") || "";
      const name = label.startsWith("Withdraw invitation sent to ")
        ? label.replace("Withdraw invitation sent to ", "").trim()
        : null;
      let cursor = link;
      let rowText = clean(link.textContent || "");
      while (cursor && cursor !== document.body) {
        const text = clean(cursor.textContent || "");
        if (name && text.includes(name) && pattern.test(text)) {
          rowText = text;
          break;
        }
        cursor = cursor.parentElement;
      }
      const ageMatch = rowText.match(pattern);
      const rowRoot = link.closest("[role='listitem']") || link.closest("li, div");
      const profile = rowRoot?.querySelector("a[href*='/in/']")?.href || null;
      return { index, name, profileUrl: profile, ageText: ageMatch ? ageMatch[0] : null, rowText };
    }).filter((row) => row.name);
  }, agePattern.source);
}

const page = await getPage();
await page.goto(sentUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
const block = await classifyBlock(page);
if (block) {
  fs.writeFileSync(config.out, `${JSON.stringify({ capturedAt: nowIso(), url: page.url(), status: block.status, rows: [] }, null, 2)}\n`);
} else {
  const rowsByKey = new Map();
  const loadActions = [];
  const rememberRows = async () => {
    for (const row of await captureVisibleRows(page)) {
      const key = row.profileUrl || `${row.name}\n${row.ageText || ""}\n${row.rowText || ""}`;
      if (!rowsByKey.has(key)) {
        rowsByKey.set(key, { ...row, index: rowsByKey.size });
      }
    }
  };
  await rememberRows();
  for (let index = 0; index < Number(config.loadMore || 0); index += 1) {
    const action = await loadMore(page);
    loadActions.push(action);
    await rememberRows();
    if (action === "no-more-content") break;
  }
  const rows = Array.from(rowsByKey.values());
  const enriched = rows.map((row) => {
    const ageDays = parseAgeDays(row.ageText);
    return {
      ...row,
      ageMonths: ageDays === null ? null : Math.floor(ageDays / 30),
      ageDays,
      eligible: ageDays !== null && ageDays >= Number(config.thresholdDays || 14),
    };
  });
  const warnings = [];
  if (enriched.length > 0 && enriched.every((row) => !row.ageText)) {
    warnings.push("visible withdraw links were found, but no invitation age text was readable");
  }
  fs.writeFileSync(config.out, `${JSON.stringify({
    capturedAt: nowIso(),
    url: page.url(),
    thresholdDays: Number(config.thresholdDays || 14),
    visibleWithdrawCount: enriched.length,
    loadActions,
    warnings,
    rows: enriched,
  }, null, 2)}\n`);
}
