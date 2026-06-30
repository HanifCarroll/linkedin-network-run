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

function firstMatch(text, regex) {
  const match = text.match(regex);
  return match ? match[1].trim() : null;
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
        warnings: ["public web research is not implemented in Playwriter backend"],
      },
      warnings,
    };
  }
  try {
    await page.goto(profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
    await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
    if (delayMs > 0) await page.waitForTimeout(Math.min(delayMs, 2000));
    const text = await page.locator("body").innerText({ timeout: 5000 }).catch(() => "");
    const lines = text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    salesNav = {
      name: lines[0] || candidate.name,
      title: firstMatch(text, /(?:Title|Role)\s*\n([^\n]+)/i) || lines[1] || null,
      company: firstMatch(text, /(?:Company|Current company)\s*\n([^\n]+)/i),
      location: firstMatch(text, /(?:Location)\s*\n([^\n]+)/i),
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
      query: config.publicWeb ? `${candidate.name} LinkedIn` : null,
      results: [],
      warnings: config.publicWeb
        ? ["public web research is not implemented in Playwriter backend"]
        : [],
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
