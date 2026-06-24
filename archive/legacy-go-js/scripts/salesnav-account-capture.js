const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_OUT_DIR = "/tmp/recruiter-agency-outreach-account-capture";

function configValue(name, fallback = null) {
  const config = globalThis.salesNavAccountCaptureConfig || state.salesNavAccountCaptureConfig;
  if (config) {
    const key = name.replace(/^--/, "").replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (Object.prototype.hasOwnProperty.call(config, key)) {
      return config[key];
    }
  }
  if (typeof process === "undefined") {
    return fallback;
  }
  const index = process.argv.indexOf(name);
  if (index === -1 || index + 1 >= process.argv.length) {
    return fallback;
  }
  return process.argv[index + 1];
}

function configFlag(name) {
  const config = globalThis.salesNavAccountCaptureConfig || state.salesNavAccountCaptureConfig;
  if (config) {
    const key = name.replace(/^--/, "").replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (Object.prototype.hasOwnProperty.call(config, key)) {
      return Boolean(config[key]);
    }
  }
  if (typeof process === "undefined") {
    return false;
  }
  return process.argv.includes(name);
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizeLinkedInUrl(value) {
  const raw = String(value || "");
  if (!raw) {
    return null;
  }
  if (/^https?:\/\//i.test(raw)) {
    return raw;
  }
  return `https://www.linkedin.com${raw.startsWith("/") ? "" : "/"}${raw}`;
}

function parseCompanyId(value) {
  const match = String(value || "").match(/\/sales\/company\/([^/?#]+)/);
  return match ? match[1] : null;
}

async function captureRows(maxRows, pageNumber, outDir, saveHtml) {
  return state.page.evaluate(({ maxRows: rowLimit, pageNumber: pageNo }) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const rows = Array.from(document.querySelectorAll("li.artdeco-list__item, div[role='listitem'], div[data-x--search-result]"))
      .filter((row) => row.querySelector("a[href*='/sales/company/']"))
      .slice(0, rowLimit)
      .map((row, index) => {
        const links = Array.from(row.querySelectorAll("a")).map((link, linkIndex) => ({
          index: linkIndex,
          text: clean(link.innerText || link.textContent || ""),
          aria: link.getAttribute("aria-label"),
          href: link.href || null,
          id: link.id || null,
          data: Object.fromEntries(
            Array.from(link.attributes)
              .filter((attr) => attr.name.startsWith("data-"))
              .map((attr) => [attr.name, attr.value]),
          ),
        }));
        const accountLink = links.find((link) => link.href && link.href.includes("/sales/company/")) || null;
        const websiteLink = links.find((link) => {
          if (!link.href) return false;
          return /^https?:\/\//i.test(link.href) && !/linkedin\.com/i.test(link.href);
        }) || null;
        const name =
          clean(row.querySelector("[data-anonymize='company-name']")?.textContent || "")
          || clean(accountLink?.text || "")
          || clean(accountLink?.aria || "").replace(/^View company\s+/i, "")
          || null;
        return {
          index,
          pageNumber: pageNo,
          name,
          text: row.innerText || "",
          html: row.outerHTML,
          accountUrl: accountLink?.href || null,
          accountId: accountLink?.href?.match(/\/sales\/company\/([^/?#]+)/)?.[1] || null,
          website: websiteLink?.href || null,
          industry: null,
          headcount: null,
          location: null,
          links,
        };
      });
    return rows;
  }, { maxRows, pageNumber }).then((rows) => {
    for (const row of rows) {
      if (saveHtml) {
        const fileName = `page-${String(pageNumber).padStart(2, "0")}-row-${String(row.index).padStart(2, "0")}.html`;
        fs.writeFileSync(path.join(outDir, "rows", fileName), row.html);
        row.rowHtmlPath = path.join(outDir, "rows", fileName);
      }
      delete row.html;
    }
    return rows;
  });
}

async function main() {
  const outDir = path.resolve(configValue("--out", DEFAULT_OUT_DIR));
  const source = configValue("--source", null);
  const url = configValue("--url", null);
  const limit = Number(configValue("--limit", "25"));
  const pages = Math.max(1, Number(configValue("--pages", "1")));
  const rowScrollDelayMs = Math.max(0, Number(configValue("--row-scroll-delay-ms", "250")));
  const saveHtml = configFlag("--save-html");

  fs.mkdirSync(path.join(outDir, "rows"), { recursive: true });
  state.page = state.page || context.pages().find((page) => page.url().includes("/sales/search/company")) || await context.newPage();
  if (url) {
    await state.page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await state.page.waitForLoadState("domcontentloaded");
  }

  const allRows = [];
  const pageSummaries = [];

  for (let pageNumber = 1; pageNumber <= pages; pageNumber += 1) {
    await state.page.waitForFunction(
      () => /Search Results|No results|0 results/i.test(document.body.innerText || ""),
      null,
      { timeout: 20000 },
    ).catch(() => {});
    await state.page.waitForFunction(
      () => document.querySelectorAll("a[href*='/sales/company/']").length > 0,
      null,
      { timeout: 30000 },
    ).catch(() => {});
    await state.page.waitForTimeout(1000);

    const pageMeta = await state.page.evaluate(() => {
      const bodyText = document.body.innerText || "";
      return {
        url: window.location.href,
        title: document.title,
        pageLabel: bodyText.match(/Page \d+ of \d+/)?.[0] || null,
        resultCount: bodyText.match(/\b[\d.K+]+ results(?: found)?\b/)?.[0] || null,
        bodyText: bodyText.slice(0, 2000),
      };
    });
    pageSummaries.push(pageMeta);

    const rows = await captureRows(limit, pageNumber, outDir, saveHtml);
    for (const row of rows) {
      row.globalIndex = allRows.length;
      row.accountUrl = normalizeLinkedInUrl(row.accountUrl);
      row.accountId = row.accountId || parseCompanyId(row.accountUrl);
      allRows.push(row);
    }

    if (pageNumber < pages) {
      const nextButton = state.page.getByRole("button", { name: /^Next$/ }).first();
      if (!(await nextButton.count()) || await nextButton.isDisabled().catch(() => false)) {
        break;
      }
      const beforeUrl = state.page.url();
      await nextButton.scrollIntoViewIfNeeded().catch(() => {});
      await nextButton.click({ timeout: 8000 });
      await state.page.waitForTimeout(Math.max(2500, rowScrollDelayMs));
      if (state.page.url() === beforeUrl) {
        await state.page.waitForTimeout(1500);
      }
    }
  }

  const capture = {
    schemaVersion: 1,
    capturedAt: new Date().toISOString(),
    url: state.page.url(),
    resumeUrl: state.page.url(),
    source,
    page: pageSummaries[pageSummaries.length - 1] || null,
    pages: pageSummaries,
    captureOptions: { limit, pages, rowScrollDelayMs },
    rawRowCount: allRows.length,
    outputRowCount: allRows.length,
    rows: allRows,
  };

  const outPath = path.join(outDir, "page.json");
  fs.writeFileSync(outPath, JSON.stringify(capture, null, 2));
  console.log(JSON.stringify({
    out: outPath,
    url: capture.url,
    source: capture.source,
    rowCount: capture.rawRowCount,
    outputRowCount: capture.outputRowCount,
  }, null, 2));
}

await main();
