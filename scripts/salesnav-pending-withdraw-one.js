const fs = require("node:fs");
const path = require("node:path");

function configValue(name, fallback = null) {
  const config = globalThis.salesNavPendingWithdrawConfig || state.salesNavPendingWithdrawConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function ageMonths(ageText) {
  const lower = String(ageText || "").toLowerCase();
  const number = Number(lower.match(/\b(\d+)\b/)?.[1] || "1");
  if (/year/.test(lower)) return number * 12;
  if (/month/.test(lower)) return number;
  if (/today|minute|hour|day|week/.test(lower)) return 0;
  return null;
}

function ageDays(ageText) {
  const lower = String(ageText || "").toLowerCase();
  if (/today|minute|hour/.test(lower)) return 0;
  const number = Number(lower.match(/\b(\d+)\b/)?.[1] || "1");
  if (/year/.test(lower)) return number * 365;
  if (/month/.test(lower)) return number * 30;
  if (/week/.test(lower)) return number * 7;
  if (/yesterday/.test(lower)) return 1;
  if (/day/.test(lower)) return number;
  return null;
}

function isTransientNavigationError(error) {
  return /net::ERR_ABORTED|execution context was destroyed|navigation|target page, context or browser has been closed/i.test(String(error?.message || error));
}

async function gotoSentInvitations(page) {
  await page.goto("https://www.linkedin.com/mynetwork/invitation-manager/sent/", {
    waitUntil: "domcontentloaded",
    timeout: 45000,
  }).catch(async (error) => {
    if (!isTransientNavigationError(error)) throw error;
    await page.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
  });
}

async function loadMoreOnce(page) {
  await page.evaluate(() => {
    const scroller = document.querySelector("main#workspace") || document.scrollingElement || document.documentElement;
    scroller.scrollTop += Math.floor(scroller.clientHeight * 2.5);
  });
  await page.waitForTimeout(500);
}

async function clickLoadMoreUntilFound(page, candidate, maxLoadMore) {
  let attempts = 0;
  const initial = await findCandidateRow(page, candidate).catch(async (error) => {
    if (!isTransientNavigationError(error)) throw error;
    await page.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
    return null;
  });
  if (initial) return initial;

  const candidateIndex = Number(candidate.index);
  const preload = Number.isFinite(candidateIndex)
    ? Math.min(maxLoadMore, Math.max(0, Math.floor(candidateIndex / 5) - 15))
    : 0;
  for (; attempts < preload; attempts += 1) {
    await loadMoreOnce(page);
    if (attempts > 0 && attempts % 50 === 0) {
      console.log(JSON.stringify({ phase: "preload-sent-invitations", attempts }));
    }
  }

  for (; attempts <= maxLoadMore; attempts += 1) {
    let found = null;
    try {
      found = await findCandidateRow(page, candidate);
    } catch (error) {
      if (!isTransientNavigationError(error)) throw error;
      await page.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
      await page.waitForTimeout(1000);
      continue;
    }
    if (found) return found;
    if (attempts === maxLoadMore) break;
    await loadMoreOnce(page);
  }
  return null;
}

async function findCandidateRow(page, candidate) {
  const ageText = candidate.age_text || candidate.ageText;
  const profileUrl = candidate.profile_url || candidate.profileUrl || "";
  const exactLabel = `Withdraw invitation sent to ${candidate.name}`;
  const candidates = [
    page.locator(`a[aria-label=${JSON.stringify(exactLabel)}]`).first(),
    page.locator(`a[aria-label^='Withdraw invitation sent to'][aria-label*=${JSON.stringify(candidate.name)}]`).first(),
  ];

  for (const withdrawLink of candidates) {
    if (!(await withdrawLink.count())) continue;
    const match = await withdrawLink.evaluate((link, input) => {
      const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
      const normalizedProfile = String(input.profileUrl || "").replace(/\/$/, "");
      let node = link;
      while (node && node !== document.body) {
        const text = clean(node.innerText || node.textContent || "");
        if (text.includes(input.ageText)) {
          const profileMatch = normalizedProfile && Array.from(node.querySelectorAll("a[href]")).some((anchor) => anchor.href.replace(/\/$/, "") === normalizedProfile);
          const nameMatch = text.toLowerCase().includes(input.name.toLowerCase()) || String(link.getAttribute("aria-label") || "").toLowerCase().includes(input.name.toLowerCase());
          if (profileMatch || nameMatch) return { text };
        }
        node = node.parentElement;
      }
      return null;
    }, { name: candidate.name, profileUrl, ageText });
    if (match) return { locator: null, withdrawLink, text: match.text };
  }

  return null;
}

async function clickWithdraw(found) {
  if (found.withdrawLink && await found.withdrawLink.count()) {
    await found.withdrawLink.click({ timeout: 8000 });
    return true;
  }
  if (!found.locator) return false;
  const link = found.locator.locator("a").filter({ hasText: /^Withdraw$/ }).first();
  if (await link.count()) {
    await link.click({ timeout: 8000 });
    return true;
  }
  const button = found.locator.locator("button").filter({ hasText: /^Withdraw$/ }).first();
  if (await button.count()) {
    await button.click({ timeout: 8000 });
    return true;
  }
  return false;
}

async function clickConfirmWithdraw(page) {
  await page.waitForTimeout(700);
  const dialog = page.locator("[role='dialog'], [aria-modal='true'], .artdeco-modal").filter({ hasText: /Withdraw/ }).last();
  if (await dialog.count()) {
    const dialogButton = dialog.locator("button").filter({ hasText: /^Withdraw$/ }).last();
    if (await dialogButton.count()) {
      await dialogButton.click({ timeout: 8000 });
      return true;
    }
  }

  const button = page.locator("button").filter({ hasText: /^Withdraw$/ }).last();
  if (await button.count()) {
    await button.click({ timeout: 8000 });
    return true;
  }
  return false;
}

async function main() {
  const candidate = configValue("candidate", null);
  const out = path.resolve(configValue("out", "/tmp/linkedin-pending-cleanup-withdraw-result.json"));
  const dryRun = Boolean(configValue("dryRun", true));
  const allowWithdraw = Boolean(configValue("allowWithdraw", false));
  const maxLoadMore = Number(configValue("maxLoadMore", 260));
  const configuredThresholdDays = configValue("thresholdDays", null);
  const configuredThresholdWeeks = configValue("thresholdWeeks", null);
  const configuredThresholdMonths = configValue("thresholdMonths", null);
  const thresholdDays = configuredThresholdDays !== null
    ? Number(configuredThresholdDays)
    : configuredThresholdWeeks !== null
      ? Number(configuredThresholdWeeks) * 7
      : configuredThresholdMonths !== null
        ? Number(configuredThresholdMonths) * 30
        : 14;
  fs.mkdirSync(path.dirname(out), { recursive: true });

  if (!candidate || !candidate.name || !(candidate.age_text || candidate.ageText)) {
    throw new Error("candidate with name and age_text is required in state.salesNavPendingWithdrawConfig");
  }
  if (!dryRun && !allowWithdraw) {
    throw new Error("real withdrawal requires allowWithdraw=true");
  }

  if (!state.pendingPage || !/linkedin\.com\/mynetwork\/invitation-manager\/sent/.test(state.pendingPage.url())) {
    state.pendingPage = await context.newPage();
    await gotoSentInvitations(state.pendingPage);
  }
  await state.pendingPage.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
  await state.pendingPage.waitForTimeout(2500);

  const beforeText = await state.pendingPage.locator("body").textContent({ timeout: 30000 });
  const beforeCount = Number(beforeText.match(/People \((\d+)\)/)?.[1]);
  if (/checkpoint|security verification|sign in|uas\/login/i.test(`${state.pendingPage.url()}\n${beforeText.slice(0, 1500)}`)) {
    const result = { status: "blocked", reason: "checkpoint-login-or-limit", candidate, url: state.pendingPage.url(), body: cleanText(beforeText).slice(0, 1500) };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  const candidateAgeDays = ageDays(candidate.age_text || candidate.ageText);
  const candidateAgeMonths = ageMonths(candidate.age_text || candidate.ageText);
  if (candidateAgeDays === null || candidateAgeDays < thresholdDays) {
    const result = { status: "not-eligible", reason: "candidate age is below stale threshold", candidate, ageDays: candidateAgeDays, ageMonths: candidateAgeMonths, thresholdDays };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  const found = await clickLoadMoreUntilFound(state.pendingPage, candidate, maxLoadMore);
  const result = {
    candidate,
    dryRun,
    url: state.pendingPage.url(),
    beforeCount: Number.isFinite(beforeCount) ? beforeCount : null,
    rowText: found?.text || null,
    status: "unknown",
    detail: null,
  };

  if (!found) {
    result.status = "row-not-found";
  } else if (dryRun) {
    result.status = "dry-run-withdrawable";
  } else {
    const clickedWithdraw = await clickWithdraw(found);
    if (!clickedWithdraw) {
      result.status = "withdraw-button-missing";
    } else {
      const clickedConfirm = await clickConfirmWithdraw(state.pendingPage);
      if (!clickedConfirm) {
        result.status = "confirm-button-missing";
      } else {
        await state.pendingPage.waitForTimeout(2000);
        const afterText = await state.pendingPage.locator("body").textContent({ timeout: 30000 });
        const afterCount = Number(afterText.match(/People \((\d+)\)/)?.[1]);
        result.afterCount = Number.isFinite(afterCount) ? afterCount : null;
        result.detail = { afterCount: result.afterCount };
        const rowStillVisible = afterText.includes(candidate.name) && afterText.includes(candidate.age_text || candidate.ageText);
        if ((Number.isFinite(beforeCount) && Number.isFinite(afterCount) && afterCount < beforeCount) || !rowStillVisible) {
          result.status = "withdrawn-verified";
        } else {
          result.status = "unverified";
        }
      }
    }
  }

  fs.writeFileSync(out, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
}

await main();
