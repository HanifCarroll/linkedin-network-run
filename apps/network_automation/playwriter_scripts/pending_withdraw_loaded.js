const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const sentUrl = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";
const withdrawSelector =
  "a[aria-label^='Withdraw invitation sent to'],button[aria-label^='Withdraw invitation sent to']";
const agePattern =
  /Sent\s+(?:(?:\d+\s+)?(?:minute|hour|day|week|month|year)s?\s+ago|today|yesterday)/i;

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
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

async function ensureSentPage(activePage) {
  if (!activePage.url().includes("linkedin.com/mynetwork/invitation-manager/sent")) {
    await activePage.goto(sentUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  }
  await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
}

async function classifyBlock(activePage) {
  const url = activePage.url();
  if (/\/login|\/uas\/login/i.test(url)) return { status: "login", reason: "login required" };
  if (/\/checkpoint/i.test(url)) return { status: "checkpoint", reason: "checkpoint present" };
  const security = await activePage
    .locator("iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']")
    .count()
    .catch(() => 0);
  if (security > 0) return { status: "security", reason: "security verification present" };
  return null;
}

async function peopleCount(activePage) {
  const text = await activePage.locator("body").textContent({ timeout: 10000 }).catch(() => "");
  const match = String(text || "").match(/People\s+\(([\d,]+)\)/);
  return match ? Number(match[1].replace(/,/g, "")) : null;
}

async function bodyText(activePage) {
  return clean(await activePage.locator("body").textContent({ timeout: 10000 }).catch(() => ""));
}

async function loadedRows(activePage) {
  return await activePage.locator(withdrawSelector).evaluateAll((links, source) => {
    const pattern = new RegExp(source, "i");
    const cleanValue = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const visible = (node) => {
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    };
    const inDialog = (node) => Boolean(node.closest("dialog,[role='dialog'],[aria-modal='true']"));
    return links
      .map((link, linkIndex) => {
        if (!visible(link) || inDialog(link)) return null;
        const label = link.getAttribute("aria-label") || "";
        const name = label.replace(/^Withdraw invitation sent to\s+/i, "").trim();
        let cursor = link;
        let rowText = cleanValue(link.textContent || "");
        while (cursor && cursor !== document.body) {
          const text = cleanValue(cursor.textContent || "");
          if (name && text.includes(name) && pattern.test(text)) {
            rowText = text;
            break;
          }
          cursor = cursor.parentElement;
        }
        const rowRoot = link.closest("[role='listitem']") || cursor || link.parentElement;
        const profileUrl = rowRoot?.querySelector("a[href*='/in/']")?.href || null;
        const ageMatch = rowText.match(pattern);
        return {
          linkIndex,
          name,
          profileUrl,
          ageText: ageMatch ? ageMatch[0] : "",
          rowText,
        };
      })
      .filter(Boolean);
  }, agePattern.source);
}

function candidate(row) {
  return {
    name: row.name,
    profileUrl: row.profileUrl || null,
    ageText: row.ageText || "",
  };
}

function dryRunResult(row) {
  return {
    candidate: candidate(row),
    status: "dry-run-withdrawable",
    detail: { rowText: row.rowText, source: "loaded-page-bottom" },
  };
}

async function clickConfirm(activePage) {
  const dialog = activePage.locator("dialog,[role='dialog'],[aria-modal='true']").filter({
    hasText: "Withdraw invitation",
  });
  const count = await dialog.count().catch(() => 0);
  if (count === 0) return false;
  const root = dialog.first();
  const button = root.locator("button[aria-label^='Withdraw invitation sent to']").first();
  await button.click({ timeout: 10000 });
  for (let index = 0; index < 40; index += 1) {
    if ((await dialog.count().catch(() => 0)) === 0) return true;
    await activePage.waitForTimeout(250);
  }
  return false;
}

async function withdrawRow(activePage, row) {
  const before = await peopleCount(activePage);
  const rowLocator = activePage.locator(withdrawSelector).nth(row.linkIndex);
  await rowLocator.click({ timeout: 10000, force: true });
  await activePage.waitForTimeout(500);
  const confirmed = await clickConfirm(activePage);
  if (!confirmed) {
    return {
      candidate: candidate(row),
      status: "confirm-button-missing",
      detail: { rowText: row.rowText, before },
    };
  }
  await activePage.waitForTimeout(1500);
  const after = await peopleCount(activePage);
  const text = await bodyText(activePage);
  const verified = (before !== null && after !== null && after < before) || !text.includes(row.name);
  return {
    candidate: candidate(row),
    status: verified ? "withdrawn-verified" : "unverified",
    detail: { rowText: row.rowText, before, after, source: "loaded-page-bottom" },
  };
}

function eligibleRows(rows) {
  return rows
    .map((row) => ({ ...row, ageDays: parseAgeDays(row.ageText) }))
    .filter((row) => row.ageDays !== null && row.ageDays >= Number(config.thresholdDays || 14));
}

const activePage = await getPage();
await ensureSentPage(activePage);
const block = await classifyBlock(activePage);
const limit = Number(config.limit || 1);
let payload = { status: "unknown", results: [] };

if (block) {
  payload = { status: block.status, results: [], detail: { reason: block.reason } };
} else if (config.dryRun) {
  const rows = eligibleRows(await loadedRows(activePage));
  payload = {
    status: rows.length > 0 ? "dry-run-withdrawable" : "no-loaded-eligible",
    results: rows.reverse().map(dryRunResult),
    detail: { loadedEligibleCount: rows.length },
  };
} else if (!config.allowWithdraw) {
  payload = {
    status: "blocked",
    results: [],
    detail: { reason: "real withdrawal requires allowWithdraw" },
  };
} else {
  const results = [];
  const startedAt = Date.now();
  const timeoutMs = Math.max(1000, Number(config.timeoutSeconds || 90) * 1000);
  for (let index = 0; index < limit; index += 1) {
    if (Date.now() - startedAt >= timeoutMs) break;
    const rows = eligibleRows(await loadedRows(activePage));
    const row = rows.pop();
    if (!row) break;
    const result = await withdrawRow(activePage, row);
    results.push(result);
    if (result.status !== "withdrawn-verified") break;
  }
  payload = {
    status: results.some((result) => result.status === "withdrawn-verified")
      ? "withdrawn"
      : "no-loaded-eligible",
    results,
    detail: {
      requestedLimit: limit,
      timeoutSeconds: Number(config.timeoutSeconds || 90),
      timedOut: Date.now() - startedAt >= timeoutMs,
    },
  };
}

fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
