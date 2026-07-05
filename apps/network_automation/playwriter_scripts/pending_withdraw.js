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

async function ensureSentPage(page) {
  if (page.url().includes("linkedin.com/mynetwork/invitation-manager/sent")) {
    await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
    return;
  }
  await page.goto(sentUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
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

async function scrollToTop(page) {
  await page.evaluate(() => {
    const workspace = document.querySelector("main#workspace");
    const candidates = [
      workspace,
      workspace?.parentElement,
      document.scrollingElement,
      document.documentElement,
      document.body,
    ].filter(Boolean);
    for (const node of candidates) {
      if (node.scrollHeight > node.clientHeight) {
        node.scrollTop = 0;
      }
    }
  });
  await page.waitForTimeout(500);
}

async function loadMore(page) {
  const button = page.getByRole("button", { name: /^Load more$/i }).first();
  if ((await button.count().catch(() => 0)) > 0 && !(await button.isDisabled().catch(() => true))) {
    await button.click({ timeout: 8000 });
    await page.waitForTimeout(1000);
    return "load-more-click";
  }
  const scroll = await page.evaluate(() => {
    const workspace = document.querySelector("main#workspace");
    const candidates = [
      workspace,
      workspace?.parentElement,
      document.scrollingElement,
      document.documentElement,
      document.body,
    ].filter(Boolean);
    let target = candidates[0];
    for (const candidateNode of candidates) {
      if (candidateNode.scrollHeight > candidateNode.clientHeight) {
        target = candidateNode;
        break;
      }
    }
    const before = target.scrollTop;
    const step = Math.max(600, Math.floor(target.clientHeight * 0.85));
    target.scrollTop = Math.min(target.scrollHeight - target.clientHeight, before + step);
    return {
      before,
      after: target.scrollTop,
    };
  });
  await page.waitForTimeout(750);
  if (scroll.after > scroll.before) return "scroll";
  return "no-more-content";
}

async function findWithdrawLink(page) {
  const label = `Withdraw invitation sent to ${candidate.name}`;
  const loadActions = [];
  for (let attempt = 0; attempt <= Number(config.maxLoadMore || 260); attempt += 1) {
    const link = page.locator(`a[aria-label=${JSON.stringify(label)}]`).first();
    if ((await link.count().catch(() => 0)) > 0) {
      const text = await rowTextFor(link);
      const ageText = candidate.age_text || candidate.ageText || "";
      if (!ageText || text.includes(ageText)) return { link, rowText: text, loadActions };
    }
    if (attempt === Number(config.maxLoadMore || 260)) break;
    const action = await loadMore(page);
    loadActions.push(action);
    if (action === "no-more-content") break;
  }
  return { link: null, rowText: "", loadActions };
}

async function findWithdrawLinkFromPage(page) {
  const current = await findWithdrawLink(page);
  if (current.link) return current;
  await scrollToTop(page);
  const fromTop = await findWithdrawLink(page);
  return {
    ...fromTop,
    loadActions: [...current.loadActions, "retry-from-top", ...fromTop.loadActions],
  };
}

function withdrawLabel() {
  return `Withdraw invitation sent to ${candidate.name}`;
}

async function collectConfirmControls(page) {
  return await page.evaluate(() => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const visible = (node) => {
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    return Array.from(document.querySelectorAll("button, [role='button']"))
      .filter(visible)
      .map((node) => ({
        tag: node.tagName.toLowerCase(),
        text: clean(node.textContent || ""),
        ariaLabel: node.getAttribute("aria-label") || "",
      }));
  });
}

async function clickConfirm(page) {
  const label = withdrawLabel();
  const button = page.locator(`button[aria-label=${JSON.stringify(label)}]`).first();
  try {
    await button.waitFor({ state: "visible", timeout: 10000 });
  } catch {
    return { confirmed: false, controls: await collectConfirmControls(page) };
  }
  await button.click({ timeout: 8000 });
  return { confirmed: true };
}

const page = await getPage();
await ensureSentPage(page);
let payload = basePayload(page.url());
const block = await classifyBlock(page);
if (block) {
  payload = { ...payload, status: block.status, detail: { reason: block.reason } };
} else if (!candidate.eligible) {
  payload = { ...payload, status: "not-eligible", detail: { reason: "candidate is not marked eligible" } };
} else {
  const found = await findWithdrawLinkFromPage(page);
  if (!found.link) {
    payload = { ...payload, status: "row-not-found", detail: { loadActions: found.loadActions } };
  } else if (config.dryRun) {
    payload = { ...payload, status: "dry-run-withdrawable", detail: { rowText: found.rowText } };
  } else if (!config.allowWithdraw) {
    payload = { ...payload, status: "blocked", detail: { reason: "real withdrawal requires allowWithdraw" } };
  } else {
    await found.link.click({ timeout: 8000 });
    const confirm = await clickConfirm(page);
    if (!confirm.confirmed) {
      payload = {
        ...payload,
        status: "confirm-button-missing",
        detail: { rowText: found.rowText, confirmed: false, controls: confirm.controls || [] },
      };
    } else {
      await page.waitForTimeout(1000);
      const stillVisible = await found.link.isVisible().catch(() => false);
      payload = {
        ...payload,
        status: stillVisible ? "unverified" : "withdrawn-verified",
        detail: { rowText: found.rowText, confirmed: true },
      };
    }
  }
}

fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
