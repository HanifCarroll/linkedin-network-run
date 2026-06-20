const fs = require("node:fs");

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function ageMonths(ageText) {
  const lower = String(ageText || "").toLowerCase();
  const number = Number(lower.match(/\b(\d+)\b/)?.[1] || "1");
  if (/year/.test(lower)) return number * 12;
  if (/month/.test(lower)) return number;
  if (/today|yesterday|minute|hour|day|week/.test(lower)) return 0;
  return null;
}

async function findCandidateRow(page, candidate) {
  const match = await page.locator("a[aria-label^='Withdraw invitation sent to']").evaluateAll((links, input) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const expectedUrl = String(input.profile_url || "").replace(/\/$/, "");
    const expectedAge = input.age_text || input.ageText;
    const expectedName = String(input.name || "").toLowerCase();

    for (const [index, link] of links.entries()) {
      let node = link;
      while (node && node !== document.body) {
        const text = clean(node.innerText || node.textContent || "");
        const withdrawCount = (text.match(/\bWithdraw\b/g) || []).length;
        if (withdrawCount === 1 && text.includes(expectedAge)) {
          const profileMatch = expectedUrl && Array.from(node.querySelectorAll("a[href]")).some((anchor) => anchor.href.replace(/\/$/, "") === expectedUrl);
          const nameMatch = text.toLowerCase().includes(expectedName) || String(link.getAttribute("aria-label") || "").toLowerCase().includes(expectedName);
          if (profileMatch || nameMatch) return { index, text };
        }
        node = node.parentElement;
      }
    }
    return null;
  }, candidate);

  if (!match) return null;
  const withdrawLink = page.locator(`a[aria-label=${JSON.stringify(`Withdraw invitation sent to ${candidate.name}`)}]`).first();
  return { withdrawLink, text: match.text };
}

async function scrollUntilFound(page, candidate) {
  const workspace = page.locator("main#workspace");
  for (let i = 0; i < 160; i += 1) {
    const found = await findCandidateRow(page, candidate);
    if (found) return found;
    await workspace.hover().catch(() => {});
    await page.mouse.wheel(0, 850);
    await page.waitForTimeout(700);
  }
  return null;
}

async function main() {
  const candidate = JSON.parse(fs.readFileSync("/tmp/linkedin-pending-cleanup-next.json", "utf8"));
  const out = "/tmp/linkedin-pending-cleanup-manual-withdraw-result.json";
  state.pendingPage = state.pendingPage || page;
  if (!/linkedin\.com\/mynetwork\/invitation-manager\/sent/.test(state.pendingPage.url())) {
    await state.pendingPage.goto("https://www.linkedin.com/mynetwork/invitation-manager/sent/", { waitUntil: "domcontentloaded", timeout: 45000 });
    await state.pendingPage.waitForTimeout(2500);
  }

  const beforeText = await state.pendingPage.locator("body").innerText({ timeout: 10000 });
  const beforeCount = Number(beforeText.match(/People \((\d+)\)/)?.[1]);
  const candidateAge = ageMonths(candidate.age_text || candidate.ageText);
  const result = {
    candidate,
    dryRun: false,
    url: state.pendingPage.url(),
    beforeCount: Number.isFinite(beforeCount) ? beforeCount : null,
    rowText: null,
    status: "unknown",
    detail: null,
  };

  if (/checkpoint|security verification|sign in|uas\/login/i.test(`${state.pendingPage.url()}\n${beforeText.slice(0, 1500)}`)) {
    result.status = "blocked";
    result.detail = { reason: "checkpoint-login-or-limit" };
  } else if (candidateAge === null || candidateAge < 2) {
    result.status = "not-eligible";
    result.detail = { ageMonths: candidateAge };
  } else {
    const found = await scrollUntilFound(state.pendingPage, candidate);
    result.rowText = found?.text || null;
    if (!found) {
      result.status = "row-not-found";
    } else {
      await found.withdrawLink.click({ timeout: 8000 });
      await state.pendingPage.waitForTimeout(800);
      const confirm = state.pendingPage.locator("button,a").filter({ hasText: /^Withdraw$/ }).last();
      if (!(await confirm.count())) {
        result.status = "confirm-button-missing";
      } else {
        await confirm.click({ timeout: 8000 });
        await state.pendingPage.waitForTimeout(2200);
        const afterText = await state.pendingPage.locator("body").innerText({ timeout: 10000 });
        const afterCount = Number(afterText.match(/People \((\d+)\)/)?.[1]);
        result.afterCount = Number.isFinite(afterCount) ? afterCount : null;
        result.detail = { afterCount: result.afterCount };
        const rowStillVisible = afterText.includes(candidate.name) && afterText.includes(candidate.age_text || candidate.ageText);
        result.status = ((Number.isFinite(beforeCount) && Number.isFinite(afterCount) && afterCount < beforeCount) || !rowStillVisible)
          ? "withdrawn-verified"
          : "unverified";
      }
    }
  }

  fs.writeFileSync(out, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
}

await main();
