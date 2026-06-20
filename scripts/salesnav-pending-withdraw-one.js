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

async function clickLoadMoreUntilFound(page, candidate, maxLoadMore) {
  for (let i = 0; i <= maxLoadMore; i += 1) {
    const found = await findCandidateRow(page, candidate);
    if (found) return found;
    const button = page.locator("button").filter({ hasText: /^Load more$/ }).first();
    if (i === maxLoadMore) break;
    if (await button.count()) {
      await button.scrollIntoViewIfNeeded().catch(() => {});
      await button.click({ timeout: 8000 });
    } else {
      await page.locator("main#workspace").hover().catch(() => {});
      await page.mouse.wheel(0, 850);
    }
    await page.waitForTimeout(700);
  }
  return null;
}

async function findCandidateRow(page, candidate) {
  const handles = await page.locator("a[aria-label^='Withdraw invitation sent to']").evaluateAll((links, input) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const normalizedProfile = String(input.profileUrl || "").replace(/\/$/, "");
    return links
      .map((link, index) => {
        const aria = link.getAttribute("aria-label") || "";
        let node = link;
        while (node && node !== document.body) {
          const text = clean(node.innerText || node.textContent || "");
          const withdrawCount = (text.match(/\bWithdraw\b/g) || []).length;
          if (withdrawCount === 1 && text.includes(input.ageText)) {
            const profileMatch = normalizedProfile && Array.from(node.querySelectorAll("a[href]")).some((anchor) => anchor.href.replace(/\/$/, "") === normalizedProfile);
            const nameMatch = aria.toLowerCase().includes(input.name.toLowerCase()) || text.toLowerCase().includes(input.name.toLowerCase());
            if (!profileMatch && !nameMatch) return null;
            return { index, text };
          }
          node = node.parentElement;
        }
        return null;
      })
      .filter(Boolean);
  }, { name: candidate.name, profileUrl: candidate.profile_url || candidate.profileUrl, ageText: candidate.age_text || candidate.ageText });
  if (!handles.length) return null;
  const withdrawLink = page.locator(`a[aria-label=${JSON.stringify(`Withdraw invitation sent to ${candidate.name}`)}]`).first();
  const locator = page.locator("div").filter({ has: withdrawLink }).filter({ hasText: candidate.age_text || candidate.ageText }).first();
  return { locator, withdrawLink, text: handles[0].text };
}

async function clickWithdraw(found) {
  if (found.withdrawLink && await found.withdrawLink.count()) {
    await found.withdrawLink.click({ timeout: 8000 });
    return true;
  }
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

async function main() {
  const candidate = configValue("candidate", null);
  const out = path.resolve(configValue("out", "/tmp/linkedin-pending-cleanup-withdraw-result.json"));
  const dryRun = Boolean(configValue("dryRun", true));
  const allowWithdraw = Boolean(configValue("allowWithdraw", false));
  const maxLoadMore = Number(configValue("maxLoadMore", 110));
  fs.mkdirSync(path.dirname(out), { recursive: true });

  if (!candidate || !candidate.name || !(candidate.age_text || candidate.ageText)) {
    throw new Error("candidate with name and age_text is required in state.salesNavPendingWithdrawConfig");
  }
  if (!dryRun && !allowWithdraw) {
    throw new Error("real withdrawal requires allowWithdraw=true");
  }

  state.pendingPage = state.pendingPage || await context.newPage();
  await state.pendingPage.goto("https://www.linkedin.com/mynetwork/invitation-manager/sent/", {
    waitUntil: "domcontentloaded",
    timeout: 45000,
  });
  await state.pendingPage.waitForTimeout(2500);

  const beforeText = await state.pendingPage.locator("body").innerText({ timeout: 10000 });
  const beforeCount = Number(beforeText.match(/People \((\d+)\)/)?.[1]);
  if (/checkpoint|security verification|sign in|uas\/login/i.test(`${state.pendingPage.url()}\n${beforeText.slice(0, 1500)}`)) {
    const result = { status: "blocked", reason: "checkpoint-login-or-limit", candidate, url: state.pendingPage.url(), body: cleanText(beforeText).slice(0, 1500) };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  const candidateAge = ageMonths(candidate.age_text || candidate.ageText);
  if (candidateAge === null || candidateAge < 2) {
    const result = { status: "not-eligible", reason: "candidate age is below stale threshold", candidate, ageMonths: candidateAge };
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
      await state.pendingPage.waitForTimeout(700);
      const confirm = state.pendingPage.locator("button,a").filter({ hasText: /^Withdraw$/ }).last();
      if (!(await confirm.count())) {
        result.status = "confirm-button-missing";
      } else {
        await confirm.click({ timeout: 8000 });
        await state.pendingPage.waitForTimeout(2000);
        const afterText = await state.pendingPage.locator("body").innerText({ timeout: 10000 });
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
