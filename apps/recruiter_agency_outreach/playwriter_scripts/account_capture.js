const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function nowIso() {
  return new Date().toISOString();
}

function absoluteLinkedinUrl(href) {
  if (!href) return null;
  try {
    return new URL(href, "https://www.linkedin.com").href;
  } catch {
    return href;
  }
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) return state.linkedinToolsPage;
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((item) => item.url().includes("linkedin.com/sales/search/company")) ||
    pages.find((item) => item.url().includes("linkedin.com/sales/company")) ||
    pages.find((item) => item.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

async function waitForAccounts(activePage) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const count = await activePage.locator("a[href*='/sales/company/']").count().catch(() => 0);
    if (count > 0) return count;
    await activePage.waitForTimeout(1000);
  }
  return 0;
}

async function clickNext(activePage) {
  const button = activePage.getByRole("button", { name: /^Next$/i }).first();
  if (!(await button.count().catch(() => 0))) return false;
  if (await button.isDisabled().catch(() => false)) return false;
  const before = activePage.url();
  await button.click({ timeout: 8000 });
  await activePage.waitForTimeout(1500);
  return activePage.url() !== before;
}

async function captureRows(activePage, limit, pageNumber) {
  return await activePage.evaluate(
    ({ limit: rowLimit, pageNumber: currentPage }) => {
      const cleanLocal = (value) => String(value || "").replace(/\s+/g, " ").trim();
      const rows = Array.from(
        document.querySelectorAll("li.artdeco-list__item, div[role='listitem'], div[data-x--search-result]")
      ).filter((row) => row.querySelector("a[href*='/sales/company/']"));
      return rows.slice(0, rowLimit).map((row, index) => {
        const links = Array.from(row.querySelectorAll("a")).map((link, linkIndex) => ({
          index: linkIndex,
          text: cleanLocal(link.textContent || ""),
          aria: link.getAttribute("aria-label"),
          href: link.href || null,
          id: link.id || null,
        }));
        const accountLink =
          links.find((link) => link.href && link.href.includes("/sales/company/")) || null;
        const websiteLink =
          links.find((link) => {
            if (!link.href) return false;
            return /^https?:\/\//i.test(link.href) && !/linkedin\.com/i.test(link.href);
          }) || null;
        const name =
          cleanLocal(row.querySelector("[data-anonymize='company-name']")?.textContent || "") ||
          cleanLocal(accountLink?.text || "") ||
          cleanLocal(accountLink?.aria || "").replace(/^View company\s+/i, "") ||
          null;
        return {
          index,
          pageNumber: currentPage,
          name,
          text: row.textContent || "",
          accountUrl: accountLink?.href || null,
          accountId: accountLink?.href?.match(/\/sales\/company\/([^/?#]+)/)?.[1] || null,
          website: websiteLink?.href || null,
          industry: null,
          headcount: null,
          location: null,
          links,
        };
      });
    },
    { limit, pageNumber }
  );
}

async function main() {
  const activePage = await getPage();
  if (config.url) {
    await activePage.goto(config.url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
  }

  const allRows = [];
  const pageSummaries = [];
  const totalPages = Math.max(1, Number(config.pages || 1));
  const limit = Math.max(0, Number(config.limit || 25));
  for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
    await waitForAccounts(activePage);
    pageSummaries.push({ url: activePage.url(), pageLabel: null });
    const rows = await captureRows(activePage, limit, pageNumber);
    for (const row of rows) {
      row.globalIndex = allRows.length;
      row.accountUrl = absoluteLinkedinUrl(row.accountUrl);
      if (!row.accountId && row.accountUrl) {
        row.accountId = row.accountUrl.match(/\/sales\/company\/([^/?#]+)/)?.[1] || null;
      }
      allRows.push(row);
    }
    if (pageNumber < totalPages && !(await clickNext(activePage))) break;
  }

  const payload = {
    schemaVersion: 1,
    capturedAt: nowIso(),
    url: activePage.url(),
    resumeUrl: activePage.url(),
    source: config.source,
    page: pageSummaries.length ? pageSummaries[pageSummaries.length - 1] : null,
    pages: pageSummaries,
    captureOptions: { limit, pages: totalPages },
    rawRowCount: allRows.length,
    outputRowCount: allRows.length,
    rows: allRows,
  };
  fs.writeFileSync(config.out, JSON.stringify(payload, null, 2));
}

main();
