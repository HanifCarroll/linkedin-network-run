const fs = require("node:fs");
const path = require("node:path");

function configValue(name, fallback = null) {
  const config = globalThis.salesNavAuditConfig || state.salesNavAuditConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function isAbortedNavigation(error) {
  return /net::ERR_ABORTED|execution context was destroyed|navigation/i.test(String(error?.message || error));
}

async function gotoSentInvitations(page) {
  await page.goto("https://www.linkedin.com/mynetwork/invitation-manager/sent/", {
    waitUntil: "domcontentloaded",
    timeout: 45000,
  }).catch(async (error) => {
    if (!isAbortedNavigation(error)) throw error;
    await page.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
  });
}

async function main() {
  const out = path.resolve(configValue("out", "/tmp/linkedin-network-run-audit.json"));
  const loadMore = Number(configValue("loadMore", 0));

  state.auditPage = state.auditPage || await context.newPage();
  await gotoSentInvitations(state.auditPage);
  await state.auditPage.waitForTimeout(2500);

  for (let i = 0; i < loadMore; i += 1) {
    const button = state.auditPage.locator("button").filter({ hasText: /^Load more$/ }).first();
    if (!(await button.count())) {
      break;
    }
    await button.click({ timeout: 8000 });
    await state.auditPage.waitForTimeout(1500);
  }

  const text = await state.auditPage.locator("body").innerText({ timeout: 10000 });
  const peopleCountText = text.match(/People \(([\d,]+)\)/)?.[1];
  const peopleCount = Number(peopleCountText?.replace(/,/g, ""));
  if (!Number.isFinite(peopleCount)) {
    throw new Error("could not parse People (N) count");
  }

  const recentNames = [];
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  for (let index = 0; index < lines.length; index += 1) {
    if (/^Sent (today|\d+ minutes? ago|\d+ hours? ago)/i.test(lines[index + 2] || "")) {
      recentNames.push(lines[index]);
    }
  }

  const audit = {
    capturedAt: new Date().toISOString(),
    url: state.auditPage.url(),
    peopleCount,
    recentNames: [...new Set(recentNames)].slice(0, 100),
    sample: cleanText(text).slice(0, 1500),
  };
  fs.writeFileSync(out, JSON.stringify(audit, null, 2));
  console.log(JSON.stringify(audit, null, 2));
}

await main();
