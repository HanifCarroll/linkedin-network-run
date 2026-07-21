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

function profileUrlFor(item) {
  return item.profile_url || item.profileUrl || null;
}

function candidateKey(item) {
  return [item.source || "", item.name || "", profileUrlFor(item) || ""].join("\u0000");
}

function artifactFor(rows) {
  return {
    capturedAt: nowIso(),
    input: config.input,
    count: rows.length,
    offset,
    limit: requestedLimit,
    totalCandidates: candidates.length,
    complete: rows.length === selected.length,
    rows,
  };
}

function writeArtifact(rows) {
  const artifact = artifactFor(rows);
  const tmp = `${config.out}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(artifact, null, 2)}\n`);
  fs.renameSync(tmp, config.out);
}

function writeProgress(event) {
  if (!config.progressOut) return;
  fs.appendFileSync(
    config.progressOut,
    `${JSON.stringify({ at: nowIso(), ...event })}\n`
  );
}

function loadExistingRows() {
  if (!config.out || !fs.existsSync(config.out)) return [];
  let existing;
  try {
    existing = JSON.parse(fs.readFileSync(config.out, "utf8"));
  } catch {
    return [];
  }
  if (
    existing.input !== config.input ||
    existing.offset !== offset ||
    existing.limit !== requestedLimit ||
    existing.totalCandidates !== candidates.length ||
    !Array.isArray(existing.rows)
  ) {
    return [];
  }

  const rows = [];
  for (let index = 0; index < Math.min(existing.rows.length, selected.length); index += 1) {
    const row = existing.rows[index];
    if (candidateKey(row) !== candidateKey(selected[index])) break;
    rows.push(row);
  }
  return rows;
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

async function visibleLabels(locator) {
  if (!locator || typeof locator.all !== "function") return [];
  const items = await locator.all();
  const labels = [];
  for (const item of items) {
    if (!(await item.isVisible().catch(() => false))) continue;
    const text = (await item.textContent().catch(() => "")).trim();
    const aria = (await item.getAttribute("aria-label").catch(() => "")) || "";
    const label = text || aria.trim();
    if (label) labels.push(label);
  }
  return Array.from(new Set(labels));
}

async function profileActionEvidence(page) {
  const sections = page.locator("main section");
  const topCard = sections && typeof sections.first === "function" ? sections.first() : null;
  if (!topCard) return { firstDegree: false, topCardLabels: [], menuLabels: [] };

  const firstDegree = await topCard
    .locator("span")
    .evaluateAll((items) =>
      items.some((item) => String(item.textContent || "").replace(/\s+/g, " ").trim() === "1st")
    )
    .catch(() => false);
  const topCardLabels = (
    await visibleLabels(topCard.locator("button, a, [role='button']"))
  ).filter((label) => /^(Connect|Follow|Message|More|Pending|Withdraw)$/i.test(label));
  let menuLabels = [];
  if (typeof topCard.getByRole === "function") {
    const moreButtons = await topCard.getByRole("button", { name: /^More$/i }).all();
    for (const button of moreButtons) {
      if (!(await button.isVisible().catch(() => false))) continue;
      await button.click({ timeout: 8000 });
      await page.waitForTimeout(500);
      menuLabels = await visibleLabels(
        page.locator(
          "[role='menu'] button, [role='menu'] a, [role='menuitem'], " +
            ".artdeco-dropdown__content button, .artdeco-dropdown__content a"
        )
      );
      await page.keyboard.press("Escape").catch(() => null);
      if (menuLabels.length > 0) break;
    }
  }
  return { firstDegree, topCardLabels, menuLabels };
}

async function classifyCandidate(candidate) {
  const page = await getPage();
  const profileUrl = profileUrlFor(candidate);
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
    const profileEvidence = await profileActionEvidence(page);
    const actionLabels = [
      ...profileEvidence.topCardLabels,
      ...profileEvidence.menuLabels,
    ];

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
    } else if (profileEvidence.firstDegree) {
      status = "accepted";
      relationship = "1st";
      note = "profile top card shows first-degree relationship evidence";
    } else if (actionLabels.some((label) => /^Remove connection$/i.test(label))) {
      status = "accepted";
      relationship = "1st";
      note = "profile action controls show Remove connection";
    } else if (
      actionLabels.some((label) => /^(Connect\s*[-–—]\s*)?Pending$|^Withdraw$/i.test(label))
    ) {
      status = "pending";
      note = "profile action controls show pending invitation evidence";
    } else if (actionLabels.some((label) => /^Connect$/i.test(label))) {
      status = "connectable";
      note = "profile action controls show Connect";
    }

    return {
      source: candidate.source,
      name: candidate.name,
      profileUrl,
      status,
      checkedAt: nowIso(),
      relationship,
      evidence: JSON.stringify({ url, profileActions: profileEvidence }),
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

const rows = loadExistingRows();
writeArtifact(rows);

for (let index = rows.length; index < selected.length; index += 1) {
  const candidate = selected[index];
  writeProgress({
    step: "acceptance-candidate",
    phase: "start",
    name: candidate.name,
    completed: index,
    total: selected.length,
  });
  const row = await classifyCandidate(candidate);
  rows.push(row);
  writeArtifact(rows);
  writeProgress({
    step: "acceptance-candidate",
    phase: "complete",
    name: candidate.name,
    status: row.status,
    completed: index + 1,
    total: selected.length,
  });
}
console.log(`wrote ${rows.length} acceptance outcomes to ${config.out}`);
