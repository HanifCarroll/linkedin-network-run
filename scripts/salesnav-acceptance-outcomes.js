const fs = require("node:fs");
const path = require("node:path");

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function configValue(name, fallback = null) {
  const config = globalThis.salesNavAcceptanceConfig || state.salesNavAcceptanceConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

function classifyMenu(labels) {
  const texts = labels.map((label) => label.text || label.aria).filter(Boolean);
  if (texts.some((text) => /^Connect\s*[—-]\s*Pending$/i.test(text) || /^Pending$/i.test(text))) {
    return "pending";
  }
  if (texts.some((text) => /^Connect$/i.test(text))) {
    return "connectable";
  }
  return "unknown";
}

function isHardBlocker(url, body) {
  return /checkpoint|security verification|weekly invitation limit|sign in|uas\/login/i.test(`${url}\n${body.slice(0, 1500)}`);
}

function relationshipFromBody(body) {
  const match = cleanText(body).match(/\b(1st|2nd|3rd)\b/);
  return match ? match[1] : null;
}

async function menuLabelsForCurrentLead(page) {
  await page.keyboard.press("Escape").catch(() => {});
  const trigger = page.locator('button[aria-label="Open actions overflow menu"]').first();
  await trigger.waitFor({ state: "visible", timeout: 8000 }).catch(() => {});
  if (!(await trigger.count())) {
    return { state: "missing-trigger", labels: [] };
  }
  const menuId = await trigger.getAttribute("aria-controls");
  await trigger.click({ timeout: 8000 });
  await page.waitForTimeout(500);
  const menu = menuId ? page.locator(`#${menuId}`) : page.locator("[data-popper-placement]").last();
  if (!(await menu.count())) {
    return { state: "missing-menu", labels: [] };
  }
  const labels = await menu.locator("button,a,[role=menuitem]").evaluateAll((items) =>
    items.map((item, index) => ({
      index,
      text: String(item.innerText || item.textContent || "").replace(/\s+/g, " ").trim(),
      aria: item.getAttribute("aria-label"),
      tag: item.tagName,
      href: item.href || null,
      disabled: item.hasAttribute("disabled"),
    })).filter((item) => item.text || item.aria),
  );
  return { state: classifyMenu(labels), labels, menuId };
}

async function classifyCandidate(page, candidate) {
  await page.goto(candidate.profile_url || candidate.profileUrl, {
    waitUntil: "domcontentloaded",
    timeout: 45000,
  });
  await page.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1800);

  const body = await page.locator("body").innerText({ timeout: 12000 }).catch(() => "");
  const relationship = relationshipFromBody(body);
  if (isHardBlocker(page.url(), body)) {
    return {
      status: "blocked",
      relationship,
      evidence: cleanText(body).slice(0, 800),
      note: "checkpoint, login, security, or limit page detected",
    };
  }
  if (relationship === "1st") {
    return {
      status: "accepted",
      relationship,
      evidence: cleanText(body).slice(0, 800),
      note: "lead page shows 1st-degree relationship",
    };
  }

  const menu = await menuLabelsForCurrentLead(page).catch((error) => ({
    state: "unknown",
    labels: [],
    error: String(error).slice(0, 500),
  }));
  if (menu.state === "pending") {
    return {
      status: "pending",
      relationship,
      evidence: JSON.stringify(menu.labels.slice(0, 8)),
      note: "lead overflow menu shows Connect - Pending",
    };
  }
  if (menu.state === "connectable") {
    return {
      status: "connectable",
      relationship,
      evidence: JSON.stringify(menu.labels.slice(0, 8)),
      note: "lead is connectable again; not accepted and not visibly pending",
    };
  }
  return {
    status: "unknown",
    relationship,
    evidence: JSON.stringify({ menuState: menu.state, labels: (menu.labels || []).slice(0, 8), body: cleanText(body).slice(0, 500) }),
    note: "could not classify acceptance state",
  };
}

async function main() {
  const input = path.resolve(configValue("in", "/tmp/linkedin-acceptance-candidates.json"));
  const out = path.resolve(configValue("out", "/tmp/linkedin-acceptance-outcomes.json"));
  const limit = Number(configValue("limit", 0));
  const offset = Number(configValue("offset", 0));
  const delayMs = Number(configValue("delayMs", 500));
  fs.mkdirSync(path.dirname(out), { recursive: true });

  const candidates = JSON.parse(fs.readFileSync(input, "utf8"));
  const selected = limit > 0 ? candidates.slice(offset, offset + limit) : candidates.slice(offset);
  const page = state.acceptancePage || context.pages().find((item) => item.url().includes("/sales/lead/")) || await context.newPage();
  state.acceptancePage = page;

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
    const checkedAt = new Date().toISOString();
    try {
      const outcome = await classifyCandidate(page, candidate);
      rows.push({
        source: candidate.source,
        name: candidate.name,
        profileUrl: candidate.profile_url || candidate.profileUrl || null,
        status: outcome.status,
        checkedAt,
        relationship: outcome.relationship,
        evidence: outcome.evidence,
        note: outcome.note,
      });
    } catch (error) {
      rows.push({
        source: candidate.source,
        name: candidate.name,
        profileUrl: candidate.profile_url || candidate.profileUrl || null,
        status: "failed",
        checkedAt,
        relationship: null,
        evidence: String(error).slice(0, 1000),
        note: "browser check failed",
      });
    }
    if (delayMs > 0) {
      await page.waitForTimeout(delayMs);
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
    statuses: rows.reduce((acc, row) => {
      acc[row.status] = (acc[row.status] || 0) + 1;
      return acc;
    }, {}),
  }, null, 2));
}

await main();
