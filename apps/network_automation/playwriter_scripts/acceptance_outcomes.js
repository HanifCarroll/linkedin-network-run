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

async function visibleCount(page, selector) {
  const locator = page.locator(selector);
  const count = await locator.count().catch(() => 0);
  let visible = 0;
  for (let index = 0; index < count; index += 1) {
    if (await locator.nth(index).isVisible().catch(() => false)) visible += 1;
  }
  return visible;
}

async function menuLabels(page) {
  const labels = await page
    .locator("button, a, [role='button'], [role='menuitem']")
    .evaluateAll((items) =>
      items
        .map((item) => (item.innerText || item.getAttribute("aria-label") || "").trim())
        .filter(Boolean)
    )
    .catch(() => []);
  return Array.from(new Set(labels));
}

async function classifyCandidate(candidate) {
  const page = await getPage();
  const profileUrl = candidate.profile_url || candidate.profileUrl;
  if (!profileUrl) {
    return {
      source: candidate.source,
      name: candidate.name,
      profileUrl,
      status: "failed",
      checkedAt: nowIso(),
      evidence: "",
      note: "missing profile URL",
    };
  }

  try {
    await page.goto(profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
    await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
    if (delayMs > 0) await page.waitForTimeout(Math.min(delayMs, 2000));

    const url = page.url();
    const login = await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']");
    const checkpoint = await visibleCount(page, "input[name='pin'], input[name='challengeId']");
    const security = await visibleCount(
      page,
      "iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']"
    );
    const bodyText = await page.locator("body").innerText({ timeout: 5000 }).catch(() => "");
    const labels = await menuLabels(page);
    const combined = `${bodyText}\n${labels.join("\n")}`;

    let status = "unknown";
    let relationship = null;
    let note = "no definitive acceptance state found";
    if (/\/login|\/uas\/login/i.test(url) || login > 0) {
      status = "blocked";
      note = "login required";
    } else if (/\/checkpoint/i.test(url) || checkpoint > 0) {
      status = "blocked";
      note = "checkpoint present";
    } else if (security > 0) {
      status = "blocked";
      note = "security verification present";
    } else if (/\b1st\b|\bMessage\b/i.test(combined)) {
      status = "accepted";
      relationship = "1st";
      note = "profile shows first-degree/message evidence";
    } else if (/Pending|Withdraw/i.test(combined)) {
      status = "pending";
      note = "profile still shows pending invitation evidence";
    } else if (/\bConnect\b/i.test(combined)) {
      status = "connectable";
      note = "lead is connectable again";
    }

    return {
      source: candidate.source,
      name: candidate.name,
      profileUrl,
      status,
      checkedAt: nowIso(),
      relationship,
      evidence: JSON.stringify({ url, labels: labels.slice(0, 30) }),
      note,
    };
  } catch (error) {
    return {
      source: candidate.source,
      name: candidate.name,
      profileUrl,
      status: "failed",
      checkedAt: nowIso(),
      evidence: String(error && error.stack ? error.stack : error),
      note: "Playwriter acceptance check failed",
    };
  }
}

const rows = [];
for (const candidate of selected) {
  rows.push(await classifyCandidate(candidate));
}

const artifact = {
  capturedAt: nowIso(),
  input: config.input,
  count: rows.length,
  offset,
  limit: requestedLimit,
  totalCandidates: candidates.length,
  complete: rows.length === selected.length,
  rows,
};

fs.writeFileSync(config.out, `${JSON.stringify(artifact, null, 2)}\n`);
console.log(`wrote ${rows.length} acceptance outcomes to ${config.out}`);
