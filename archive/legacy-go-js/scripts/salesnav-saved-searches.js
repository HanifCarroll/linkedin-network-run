const fs = require("node:fs");
const path = require("node:path");

function configValue(name, fallback = null) {
  const config = globalThis.salesNavSavedSearchConfig || state.salesNavSavedSearchConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

async function main() {
  const out = path.resolve(configValue("out", "/tmp/linkedin-network-run-saved-searches.json"));
  const url = configValue("url", "https://www.linkedin.com/sales/search/people");

  state.page = state.page || context.pages().find((page) => page.url().includes("/sales/search/people")) || await context.newPage();
  await state.page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
  await state.page.waitForTimeout(2000);
  await state.page.getByRole("button", { name: /Saved searches/i }).click({ timeout: 10000 });
  await state.page.waitForTimeout(2500);

  const searches = await state.page.evaluate(() => {
    const anchors = Array.from(document.querySelectorAll("a[href*='savedSearchId=']"));
    const byId = new Map();
    for (const anchor of anchors) {
      const href = anchor.href;
      const savedSearchId = new URL(href).searchParams.get("savedSearchId");
      if (!savedSearchId) continue;
      const rowText = anchor.closest("li,div")?.innerText || anchor.innerText || "";
      const text = rowText.replace(/\s+/g, " ").trim();
      const aria = anchor.getAttribute("aria-label") || "";
      const knownName = text.match(/(?:Go to \d+[,\dK+]* new results for |View )(.+?)(?: since | lead saved search|$)/)?.[1]
        || aria.match(/(?:View |results for )(.+?)(?: lead saved search| since|$)/)?.[1]
        || anchor.innerText?.trim();
      const entry = byId.get(savedSearchId) || {
        savedSearchId,
        name: knownName,
        viewUrl: null,
        freshUrl: null,
        freshText: null,
        rowText: text,
      };
      if (href.includes("lastViewedAt=")) {
        entry.freshUrl = href;
        entry.freshText = anchor.innerText.replace(/\s+/g, " ").trim();
      } else {
        entry.viewUrl = href;
      }
      entry.name = entry.name || knownName;
      byId.set(savedSearchId, entry);
    }
    return Array.from(byId.values());
  });

  const artifact = {
    capturedAt: new Date().toISOString(),
    url: state.page.url(),
    searches,
  };
  fs.writeFileSync(out, JSON.stringify(artifact, null, 2));
  console.log(JSON.stringify({ out, count: searches.length, searches: searches.slice(0, 10) }, null, 2));
}

await main();
