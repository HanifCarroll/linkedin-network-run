const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const candidate = config.candidate || {};
const sentUrl = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";

function basePayload(url) {
  return {
    candidate: {
      name: candidate.name,
      profileUrl: candidate.profile_url || candidate.profileUrl || null,
      ageText: candidate.age_text || candidate.ageText || "",
    },
    dryRun: Boolean(config.dryRun),
    url,
    status: "unknown",
  };
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((candidatePage) => candidatePage.url().includes("linkedin.com/mynetwork/invitation-manager/sent")) ||
    pages.find((candidatePage) => candidatePage.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

async function classifyBlock(page) {
  const url = page.url();
  if (/\/login|\/uas\/login/i.test(url)) return { status: "login", reason: "login required" };
  if (/\/checkpoint/i.test(url)) return { status: "checkpoint", reason: "checkpoint present" };
  const security = await page.locator("iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']").count().catch(() => 0);
  if (security > 0) return { status: "security", reason: "security verification present" };
  return null;
}

async function rowTextFor(link) {
  return await link.evaluate((node, input) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    let cursor = node;
    while (cursor && cursor !== document.body) {
      const text = clean(cursor.textContent || "");
      if (text.includes(input.name) && (!input.ageText || text.includes(input.ageText))) return text;
      cursor = cursor.parentElement;
    }
    return clean(node.textContent || "");
  }, { name: candidate.name, ageText: candidate.age_text || candidate.ageText || "" });
}

async function findWithdrawLink(page) {
  const label = `Withdraw invitation sent to ${candidate.name}`;
  for (let attempt = 0; attempt <= Number(config.maxLoadMore || 260); attempt += 1) {
    const link = page.locator(`a[aria-label=${JSON.stringify(label)}]`).first();
    if ((await link.count().catch(() => 0)) > 0) {
      const text = await rowTextFor(link);
      const ageText = candidate.age_text || candidate.ageText || "";
      if (!ageText || text.includes(ageText)) return { link, rowText: text };
    }
    if (attempt === Number(config.maxLoadMore || 260)) break;
    await page.evaluate(() => {
      const node = document.querySelector("main#workspace") || document.scrollingElement || document.documentElement;
      node.scrollTop += Math.floor(node.clientHeight * 2.5);
    });
    await page.waitForTimeout(500);
  }
  return null;
}

async function clickConfirm(page) {
  const button = page.getByRole("button", { name: /^Withdraw$/i }).last();
  if ((await button.count().catch(() => 0)) === 0) return false;
  await button.click({ timeout: 8000 });
  return true;
}

const page = await getPage();
await page.goto(sentUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
let payload = basePayload(page.url());
const block = await classifyBlock(page);
if (block) {
  payload = { ...payload, status: block.status, detail: { reason: block.reason } };
} else if (!candidate.eligible) {
  payload = { ...payload, status: "not-eligible", detail: { reason: "candidate is not marked eligible" } };
} else {
  const found = await findWithdrawLink(page);
  if (!found) {
    payload = { ...payload, status: "row-not-found" };
  } else if (config.dryRun) {
    payload = { ...payload, status: "dry-run-withdrawable", detail: { rowText: found.rowText } };
  } else if (!config.allowWithdraw) {
    payload = { ...payload, status: "blocked", detail: { reason: "real withdrawal requires allowWithdraw" } };
  } else {
    await found.link.click({ timeout: 8000 });
    await page.waitForTimeout(500);
    const confirmed = await clickConfirm(page);
    if (!confirmed) {
      payload = { ...payload, status: "confirm-button-missing", detail: { rowText: found.rowText, confirmed } };
    } else {
      await page.waitForTimeout(1000);
      const stillVisible = await found.link.isVisible().catch(() => false);
      payload = {
        ...payload,
        status: stillVisible ? "unverified" : "withdrawn-verified",
        detail: { rowText: found.rowText, confirmed },
      };
    }
  }
}

fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
