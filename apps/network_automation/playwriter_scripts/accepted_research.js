const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const candidates = config.candidates || [];
const offset = Number(config.offset || 0);
const requestedLimit = Number(config.limit || 0);
const selected = requestedLimit > 0
  ? candidates.slice(offset, offset + requestedLimit)
  : candidates.slice(offset);
const delayMs = Number(config.delayMs || 500);

function nowIso() {
  return new Date().toISOString();
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((page) => page.url().includes("linkedin.com/sales")) ||
    pages.find((page) => page.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim() || null;
}

function normalizePublicProfileUrl(value) {
  if (!value) return null;
  try {
    const parsed = new URL(String(value), "https://www.linkedin.com");
    if (!["www.linkedin.com", "linkedin.com"].includes(parsed.hostname)) return null;
    const parts = parsed.pathname.split("/").filter(Boolean);
    if (parts.length < 2 || parts[0] !== "in") return null;
    return `https://www.linkedin.com/in/${encodeURIComponent(decodeURIComponent(parts[1]))}`;
  } catch {
    return null;
  }
}

function publicProfileFromIdentifier(value) {
  const text = clean(value);
  if (!text || text.includes("/") || text.includes(" ")) return null;
  return `https://www.linkedin.com/in/${encodeURIComponent(text)}`;
}

function collectPublicProfileUrls(value, urls = []) {
  if (!value) return urls;
  if (Array.isArray(value)) {
    for (const item of value) collectPublicProfileUrls(item, urls);
    return urls;
  }
  if (typeof value !== "object") return urls;
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "string") {
      if (key === "publicIdentifier") {
        const fromIdentifier = publicProfileFromIdentifier(item);
        if (fromIdentifier) urls.push(fromIdentifier);
      }
      if (
        [
          "publicProfileUrl",
          "flagshipProfileUrl",
          "navigationUrl",
          "profileUrl",
          "url",
        ].includes(key)
      ) {
        const normalized = normalizePublicProfileUrl(item);
        if (normalized) urls.push(normalized);
      }
    } else if (item && typeof item === "object") {
      collectPublicProfileUrls(item, urls);
    }
  }
  return urls;
}

async function publicProfileUrlsFromAnchors(page) {
  return page.evaluate(() => {
    const normalize = (value) => {
      try {
        const parsed = new URL(String(value), "https://www.linkedin.com");
        if (!["www.linkedin.com", "linkedin.com"].includes(parsed.hostname)) return null;
        const parts = parsed.pathname.split("/").filter(Boolean);
        if (parts.length < 2 || parts[0] !== "in") return null;
        return `https://www.linkedin.com/in/${encodeURIComponent(decodeURIComponent(parts[1]))}`;
      } catch {
        return null;
      }
    };
    return [...document.querySelectorAll("a[href*='/in/']")]
      .map((anchor) => normalize(anchor.getAttribute("href")))
      .filter(Boolean);
  });
}

async function companyProfileUrlsFromAnchors(page) {
  return page.evaluate(() => {
    const normalize = (value) => {
      try {
        const parsed = new URL(String(value), "https://www.linkedin.com");
        if (!["www.linkedin.com", "linkedin.com"].includes(parsed.hostname)) return null;
        if (!parsed.pathname.startsWith("/sales/company/")) return null;
        return `https://www.linkedin.com${parsed.pathname}`.replace(/\/$/, "");
      } catch {
        return null;
      }
    };
    return [...document.querySelectorAll("a[href*='/sales/company/']")]
      .map((anchor) => normalize(anchor.getAttribute("href")))
      .filter(Boolean);
  });
}

async function externalUrlsFromAnchors(page) {
  return page.evaluate(() => {
    const blockedHosts = new Set(["linkedin.com", "www.linkedin.com", "lnkd.in"]);
    const urls = [];
    for (const anchor of document.querySelectorAll("a[href]")) {
      try {
        const parsed = new URL(anchor.getAttribute("href"), window.location.href);
        if (!["http:", "https:"].includes(parsed.protocol)) continue;
        if (blockedHosts.has(parsed.hostname)) continue;
        if (parsed.hostname.endsWith(".linkedin.com")) continue;
        parsed.hash = "";
        urls.push(parsed.toString().replace(/\/$/, ""));
      } catch {
        continue;
      }
    }
    return [...new Set(urls)];
  });
}

async function pageMetadata(page, url) {
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    await waitForPageLoad({ page, timeout: 7000 }).catch(() => null);
    return await page.evaluate(() => {
      const clean = (value) => String(value || "").replace(/\s+/g, " ").trim() || null;
      const metaDescription =
        document.querySelector("meta[name='description']")?.getAttribute("content") ||
        document.querySelector("meta[property='og:description']")?.getAttribute("content");
      return {
        title: clean(document.querySelector("title")?.textContent),
        description: clean(metaDescription),
      };
    });
  } catch (error) {
    return {
      title: null,
      description: null,
      warning: `website metadata fetch failed: ${String(error && error.message ? error.message : error)}`,
    };
  }
}

async function webSearch(query) {
  if (!query) {
    return { query: null, results: [], warnings: ["web search query unavailable"] };
  }
  const page = await getPage();
  const url = `https://duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    await waitForPageLoad({ page, timeout: 7000 }).catch(() => null);
    const results = await page.evaluate(() => {
      const clean = (value) => String(value || "").replace(/\s+/g, " ").trim() || null;
      return [...document.querySelectorAll(".result")]
        .map((result) => {
          const anchor = result.querySelector(".result__a");
          const snippet = result.querySelector(".result__snippet");
          return {
            title: clean(anchor?.textContent),
            url: anchor?.href || null,
            snippet: clean(snippet?.textContent),
          };
        })
        .filter((item) => item.title || item.url || item.snippet);
    });
    return {
      query,
      results,
      warnings: results.length ? [] : ["web search returned no parsed results"],
    };
  } catch (error) {
    return {
      query,
      results: [],
      warnings: [`web search failed: ${String(error && error.message ? error.message : error)}`],
    };
  }
}

async function textFromFirst(page, selectors) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if ((await locator.count().catch(() => 0)) === 0) continue;
    const text = clean(await locator.textContent({ timeout: 1500 }).catch(() => ""));
    if (text) return text;
  }
  return null;
}

async function researchCandidate(candidate) {
  const page = await getPage();
  const profileUrl = candidate.profile_url || candidate.profileUrl;
  const salesNavProfileUrl =
    candidate.sales_nav_profile_url || candidate.salesNavProfileUrl || profileUrl || null;
  const warnings = [];
  const apiPublicProfileUrls = [];
  const onResponse = async (response) => {
    try {
      const url = response.url();
      if (!url.includes("linkedin.com") || !url.includes("/sales-api/")) return;
      const payload = await response.json();
      apiPublicProfileUrls.push(...collectPublicProfileUrls(payload));
    } catch {
      return;
    }
  };
  let salesNav = {
    name: candidate.name,
    title: null,
    company: null,
    location: null,
    url: profileUrl || null,
    warnings,
  };
  let companyProfile = null;
  let companyWebsite = null;
  let web = {
    query: null,
    results: [],
    warnings: ["accepted follow-up research did not reach web search"],
  };
  if (!profileUrl) {
    warnings.push("missing profile URL");
    return {
      source: candidate.source,
      name: candidate.name,
      profileUrl,
      salesNavProfileUrl,
      publicProfileUrl: null,
      salesNav,
      companyProfile,
      companyWebsite,
      web,
      warnings,
    };
  }
  try {
    page.on("response", onResponse);
    await page.goto(profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
    await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
    if (delayMs > 0) await page.waitForTimeout(Math.min(delayMs, 2000));
    const title = await textFromFirst(page, [
      "[data-anonymize='headline']",
      "[data-anonymize='title']",
    ]);
    const company = await textFromFirst(page, [
      "[data-anonymize='company-name']",
      "[data-anonymize='company']",
    ]);
    if (!title && !company) {
      warnings.push("Sales Navigator title/company selectors did not produce evidence");
    }
    salesNav = {
      name: await textFromFirst(page, ["[data-anonymize='person-name']"]) || candidate.name,
      title,
      company,
      location: await textFromFirst(page, ["[data-anonymize='location']"]),
      url: page.url(),
      warnings,
    };
    const companyProfileUrl = (await companyProfileUrlsFromAnchors(page)).find(Boolean) || null;
    let companyWebsiteUrl = (await externalUrlsFromAnchors(page)).find(Boolean) || null;
    companyProfile = {
      name: company,
      url: companyProfileUrl,
      websiteUrl: companyWebsiteUrl,
      description: null,
      industry: null,
      size: null,
      warnings: [],
    };
    if (companyProfileUrl) {
      try {
        await page.goto(companyProfileUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
        await waitForPageLoad({ page, timeout: 7000 }).catch(() => null);
        const profileName = await textFromFirst(page, ["[data-anonymize='company-name']"]);
        const profileDescription = await textFromFirst(page, [
          "[data-test-company-about-description]",
          "[data-test-company-description]",
        ]);
        companyWebsiteUrl = companyWebsiteUrl || (await externalUrlsFromAnchors(page)).find(Boolean) || null;
        companyProfile = {
          name: profileName || company,
          url: companyProfileUrl,
          websiteUrl: companyWebsiteUrl,
          description: profileDescription,
          industry: null,
          size: null,
          warnings: profileDescription ? [] : ["company profile description was not extracted"],
        };
      } catch (error) {
        companyProfile.warnings.push(`company profile research failed: ${String(error && error.message ? error.message : error)}`);
      }
    } else {
      companyProfile.warnings.push("company profile URL was not extracted from Sales Nav");
    }
    if (companyWebsiteUrl) {
      const metadata = await pageMetadata(page, companyWebsiteUrl);
      companyWebsite = {
        url: companyWebsiteUrl,
        title: metadata.title || null,
        description: metadata.description || null,
        warnings: metadata.warning ? [metadata.warning] : [],
      };
    } else {
      companyWebsite = {
        url: null,
        title: null,
        description: null,
        warnings: ["company website URL was not extracted"],
      };
    }
    const queryParts = [candidate.name, title, company].filter(Boolean);
    web = await webSearch(queryParts.join(" "));
  } catch (error) {
    warnings.push(`Playwriter profile research failed: ${String(error && error.message ? error.message : error)}`);
  } finally {
    page.off("response", onResponse);
  }
  const publicProfileUrl =
    apiPublicProfileUrls.find(Boolean) ||
    (await publicProfileUrlsFromAnchors(page)).find(Boolean) ||
    null;
  if (!publicProfileUrl) {
    warnings.push("Public LinkedIn profile URL was not extracted from Sales Nav");
  }
  return {
    source: candidate.source,
    name: candidate.name,
    profileUrl,
    salesNavProfileUrl,
    publicProfileUrl,
    salesNav,
    companyProfile,
    companyWebsite,
    web,
    warnings,
  };
}

const rows = [];
for (const candidate of selected) {
  rows.push(await researchCandidate(candidate));
}

const artifact = {
  capturedAt: nowIso(),
  rows,
};

fs.writeFileSync(config.out, `${JSON.stringify(artifact, null, 2)}\n`);
console.log(`wrote ${rows.length} accepted research rows to ${config.out}`);
