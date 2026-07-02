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
  const warnings = [];
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
      salesNav,
      web: {
        query: null,
        results: [],
        warnings: ["accepted follow-up research records Sales Nav evidence only"],
      },
      warnings,
    };
  }
  try {
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
  }
  return {
    source: candidate.source,
    name: candidate.name,
    profileUrl,
    salesNav,
    web: {
      query: null,
      results: [],
      warnings: ["accepted follow-up research records Sales Nav evidence only"],
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
