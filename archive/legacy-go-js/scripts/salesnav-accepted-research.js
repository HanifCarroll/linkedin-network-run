const fs = require("node:fs");
const path = require("node:path");

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function configValue(name, fallback = null) {
  const config = globalThis.salesNavAcceptedResearchConfig || state.salesNavAcceptedResearchConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

function profileUrl(candidate) {
  return candidate.profile_url || candidate.profileUrl || null;
}

function takeFirst(items, maxItems) {
  if (!maxItems || maxItems <= 0) {
    return items;
  }
  const selected = [];
  for (const item of items) {
    if (selected.length >= maxItems) {
      break;
    }
    selected.push(item);
  }
  return selected;
}

async function textFromFirst(page, selectors) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (!(await locator.count().catch(() => 0))) {
      continue;
    }
    const text = await locator.textContent({ timeout: 1500 }).catch(() => "");
    const cleaned = cleanText(text);
    if (cleaned) {
      return cleaned;
    }
  }
  return null;
}

async function extractSalesNav(page, candidate) {
  const url = profileUrl(candidate);
  const warnings = [];
  if (!url) {
    return { warnings: ["candidate has no Sales Nav profile URL"] };
  }

  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1200);

  const bodyText = cleanText(await page.locator("body").textContent({ timeout: 8000 }).catch(() => ""));
  if (/checkpoint|security verification|weekly invitation limit|sign in|uas\/login/i.test(`${page.url()}\n${bodyText}`)) {
    warnings.push("Sales Nav page appears blocked by checkpoint, login, security, or limit page");
  }

  const name = await textFromFirst(page, [
    '[data-anonymize="person-name"]',
    '[data-anonymize="name"]',
  ]);
  const title = await textFromFirst(page, [
    '[data-anonymize="headline"]',
    '[data-anonymize="title"]',
  ]);
  const company = await textFromFirst(page, [
    '[data-anonymize="company-name"]',
    '[data-anonymize="company"]',
  ]);
  const location = await textFromFirst(page, [
    '[data-anonymize="location"]',
  ]);

  if (!title && !company) {
    warnings.push("Sales Nav title/company selectors did not produce evidence");
  }

  return {
    name,
    title,
    company,
    location,
    url: page.url(),
    warnings,
  };
}

function duckDuckGoUrl(query) {
  const params = new URLSearchParams({ q: query });
  return `https://duckduckgo.com/html/?${params.toString()}`;
}

function normalizeDuckDuckGoHref(href) {
  if (!href) {
    return null;
  }
  try {
    const parsed = new URL(href, "https://duckduckgo.com");
    const uddg = parsed.searchParams.get("uddg");
    return uddg ? decodeURIComponent(uddg) : parsed.href;
  } catch {
    return href;
  }
}

async function publicWebResearch(page, candidate, salesNav, enabled, maxWebResults) {
  const warnings = [];
  if (!enabled) {
    return { query: null, results: [], warnings: ["public web research disabled"] };
  }

  const queryParts = [
    candidate.name,
    salesNav.company,
    salesNav.title,
    "contract hiring product engineering AI workflow",
  ].filter(Boolean);
  const query = cleanText(queryParts.join(" "));
  if (!query) {
    return { query: null, results: [], warnings: ["not enough evidence to build public web query"] };
  }

  try {
    await page.goto(duckDuckGoUrl(query), { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1000);
    const results = await page.locator(".result").evaluateAll((items, maxItems) => {
      const selected = [];
      for (const item of items) {
        if (maxItems > 0 && selected.length >= maxItems) {
          break;
        }
        const link = item.querySelector(".result__a");
        const snippet = item.querySelector(".result__snippet");
        const row = {
          title: String(link?.textContent || "").replace(/\s+/g, " ").trim() || null,
          url: link?.getAttribute("href") || null,
          snippet: String(snippet?.textContent || "").replace(/\s+/g, " ").trim() || null,
        };
        if (row.title || row.url || row.snippet) {
          selected.push(row);
        }
      }
      return selected;
    }, maxWebResults);
    if (!results.length) {
      warnings.push("public web search returned no structured results");
    }
    return {
      query,
      results: results.map((result) => ({
        ...result,
        url: normalizeDuckDuckGoHref(result.url),
      })),
      warnings,
    };
  } catch (error) {
    return {
      query,
      results: [],
      warnings: [`public web research failed: ${String(error)}`],
    };
  }
}

async function main() {
  const input = path.resolve(configValue("in", "/tmp/linkedin-accepted-candidates.json"));
  const out = path.resolve(configValue("out", "/tmp/linkedin-accepted-research.json"));
  const limit = Number(configValue("limit", 0));
  const offset = Number(configValue("offset", 0));
  const maxWebResults = Number(configValue("maxWebResults", 5));
  const delayMs = Number(configValue("delayMs", 500));
  const publicWeb = Boolean(configValue("publicWeb", true));
  fs.mkdirSync(path.dirname(out), { recursive: true });

  const candidates = JSON.parse(fs.readFileSync(input, "utf8"));
  const selected = takeFirst(candidates.slice(offset), limit);
  const salesPage = state.acceptedResearchSalesPage || context.pages().find((item) => item.url().includes("/sales/lead/")) || await context.newPage();
  const webPage = state.acceptedResearchWebPage || await context.newPage();
  state.acceptedResearchSalesPage = salesPage;
  state.acceptedResearchWebPage = webPage;

  const rows = [];
  const writeArtifact = (complete) => {
    const artifact = {
      capturedAt: new Date().toISOString(),
      input,
      count: rows.length,
      offset,
      limit,
      totalCandidates: candidates.length,
      complete,
      rows,
    };
    fs.writeFileSync(out, JSON.stringify(artifact, null, 2));
  };

  for (const candidate of selected) {
    const warnings = [];
    let salesNav = { warnings: ["Sales Nav research did not run"] };
    let web = { query: null, results: [], warnings: ["public web research did not run"] };
    try {
      salesNav = await extractSalesNav(salesPage, candidate);
    } catch (error) {
      salesNav = {
        warnings: [`Sales Nav research failed: ${String(error)}`],
      };
    }
    try {
      web = await publicWebResearch(webPage, candidate, salesNav, publicWeb, maxWebResults);
    } catch (error) {
      web = {
        query: null,
        results: [],
        warnings: [`public web research failed: ${String(error)}`],
      };
    }
    rows.push({
      source: candidate.source,
      name: candidate.name,
      profileUrl: profileUrl(candidate),
      salesNav,
      web,
      warnings,
    });
    if (delayMs > 0) {
      await salesPage.waitForTimeout(delayMs);
    }
    writeArtifact(false);
  }

  writeArtifact(true);
  console.log(JSON.stringify({
    out,
    count: rows.length,
    offset,
    limit,
    totalCandidates: candidates.length,
    publicWeb,
  }, null, 2));
}

await main();
