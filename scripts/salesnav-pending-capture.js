const fs = require("node:fs");
const path = require("node:path");

function configValue(name, fallback = null) {
  const config = globalThis.salesNavPendingCaptureConfig || state.salesNavPendingCaptureConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

async function clickLoadMore(page, loadMore) {
  for (let i = 0; i < loadMore; i += 1) {
    const button = page.locator("button").filter({ hasText: /^Load more$/ }).first();
    if (await button.count()) {
      await button.scrollIntoViewIfNeeded().catch(() => {});
      await button.click({ timeout: 8000 });
    } else {
      await page.evaluate(() => {
        const scroller = document.querySelector("main#workspace") || document.scrollingElement || document.documentElement;
        const before = scroller.scrollTop;
        scroller.scrollTop = before + Math.floor(scroller.clientHeight * 2.5);
      });
    }
    await page.waitForTimeout(500);
  }
}

async function main() {
  const out = path.resolve(configValue("out", "/tmp/linkedin-pending-cleanup-capture.json"));
  const loadMore = Number(configValue("loadMore", 0));
  const thresholdMonths = Number(configValue("thresholdMonths", 2));
  fs.mkdirSync(path.dirname(out), { recursive: true });

  state.pendingPage = state.pendingPage || await context.newPage();
  await state.pendingPage.goto("https://www.linkedin.com/mynetwork/invitation-manager/sent/", {
    waitUntil: "domcontentloaded",
    timeout: 45000,
  });
  await state.pendingPage.waitForTimeout(2500);
  await clickLoadMore(state.pendingPage, loadMore);

  const artifact = await state.pendingPage.evaluate((threshold) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const parseAgeMonths = (ageText) => {
      const lower = String(ageText || "").toLowerCase();
      const number = Number(lower.match(/\b(\d+)\b/)?.[1] || "1");
      if (/year/.test(lower)) return number * 12;
      if (/month/.test(lower)) return number;
      if (/today|minute|hour|day|week/.test(lower)) return 0;
      return null;
    };
    const bodyText = document.body.innerText || "";
    const peopleCount = Number(bodyText.match(/People \((\d+)\)/)?.[1]);
    const withdrawLinks = Array.from(document.querySelectorAll("a[aria-label^='Withdraw invitation sent to']"));
    const rowElements = withdrawLinks.map((link) => {
      let node = link;
      while (node && node !== document.body) {
        const text = node.innerText || node.textContent || "";
        const withdrawCount = (text.match(/\bWithdraw\b/g) || []).length;
        if (withdrawCount === 1 && /\bSent\b/i.test(text)) return { row: node, link };
        node = node.parentElement;
      }
      return { row: link, link };
    });
    const rows = rowElements
      .map(({ row, link }, index) => {
        const rowText = clean(row.innerText || row.textContent || "");
        const ageText = rowText.match(/Sent (?:today|yesterday|\d+ minutes? ago|\d+ hours? ago|\d+ days? ago|\d+ weeks? ago|\d+ months? ago|\d+ years? ago)/i)?.[0] || null;
        const age = parseAgeMonths(ageText);
        const profileLink = Array.from(row.querySelectorAll("a[href*='/in/'], a[href*='/sales/lead/']")).find((anchor) => anchor.href && !/^Withdraw\b/.test(anchor.innerText || "")) || null;
        const lines = (row.innerText || "").split("\n").map((line) => clean(line)).filter(Boolean);
        const ageIndex = lines.findIndex((line) => /^Sent\b/i.test(line));
        const ariaName = link.getAttribute("aria-label")?.replace(/^Withdraw invitation sent to\s+/i, "") || null;
        const name = ariaName || lines.find((line, lineIndex) => lineIndex < ageIndex && line !== "Withdraw" && !/^Sent\b/i.test(line)) || lines[0] || null;
        return {
          index,
          name,
          profileUrl: profileLink?.href || null,
          ageText,
          ageMonths: age,
          eligible: age !== null && age >= threshold,
          rowText,
        };
      })
      .filter((row, index, all) => row.name && row.ageText && all.findIndex((other) => other.name === row.name && other.ageText === row.ageText) === index);
    return {
      capturedAt: new Date().toISOString(),
      url: location.href,
      peopleCount: Number.isFinite(peopleCount) ? peopleCount : null,
      thresholdMonths: threshold,
      rows,
    };
  }, thresholdMonths);

  fs.writeFileSync(out, JSON.stringify(artifact, null, 2));
  console.log(JSON.stringify({
    out,
    url: artifact.url,
    peopleCount: artifact.peopleCount,
    rowCount: artifact.rows.length,
    eligibleCount: artifact.rows.filter((row) => row.eligible).length,
    oldestAgeMonths: Math.max(0, ...artifact.rows.map((row) => row.ageMonths || 0)),
    sample: artifact.rows.slice(0, 10).map((row) => ({ name: row.name, ageText: row.ageText, eligible: row.eligible })),
  }, null, 2));
}

await main();
