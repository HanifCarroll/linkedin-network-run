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
  if (!profileUrl) {
    warnings.push("missing profile URL");
    return {
      source: candidate.source,
      name: candidate.name,
      profileUrl,
      salesNavProfileUrl,
      publicProfileUrl: null,
      salesNav,
      web: {
        query: null,
        results: [],
        warnings: ["accepted follow-up research records LinkedIn profile URL and Sales Nav evidence only"],
      },
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
    web: {
      query: null,
      results: [],
      warnings: ["accepted follow-up research records LinkedIn profile URL and Sales Nav evidence only"],
    },
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
