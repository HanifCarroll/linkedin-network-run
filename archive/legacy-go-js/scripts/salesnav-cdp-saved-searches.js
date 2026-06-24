#!/usr/bin/env bun
const fs = require("node:fs");
const { clickCenter, connectToTarget, navigate, parseArgs, readPort, waitFor } = require("./salesnav-cdp-lib.js");

async function main() {
  const args = parseArgs();
  const port = readPort(args.port, args.profile);
  const out = args.out || "/tmp/linkedin-network-run-cdp-saved-searches.json";
  const url = args.url || "https://www.linkedin.com/sales/search/people";

  const cdp = await connectToTarget({ port, targetUrlIncludes: "linkedin.com" });
  try {
    await navigate(cdp, url);
    await waitFor(
      cdp,
      `(() => {
        const text = document.body?.innerText || "";
        return { ready: /Saved searches|Lead filters|Sales Navigator|sign in|checkpoint|security verification/i.test(text), url: location.href, sample: text.slice(0, 1200) };
      })()`,
      { timeout: 45000, interval: 1000 },
    );
    const body = await cdp.evaluate(`(() => ({ url: location.href, text: (document.body.innerText || "").slice(0, 1500) }))()`);
    if (/checkpoint|security verification|sign in|uas\/login/i.test(`${body.url}\n${body.text}`)) {
      const result = { status: "blocked", reason: "checkpoint-login", url: body.url, body: body.text.replace(/\s+/g, " ").trim() };
      fs.writeFileSync(out, JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
      return;
    }

    const clicked = await clickCenter(cdp, `(() => {
      return Array.from(document.querySelectorAll("button")).find((button) => /Saved searches/i.test(button.innerText || button.textContent || button.getAttribute("aria-label") || "")) || null;
    })()`);
    if (!clicked) {
      throw new Error("saved-searches-button-missing");
    }
    await new Promise((resolve) => setTimeout(resolve, 2500));

    const searches = await cdp.evaluate(`(() => {
      const anchors = Array.from(document.querySelectorAll("a[href*='savedSearchId=']"));
      const byId = new Map();
      for (const anchor of anchors) {
        const href = anchor.href;
        const savedSearchId = new URL(href).searchParams.get("savedSearchId");
        if (!savedSearchId) continue;
        const rowText = anchor.closest("li,div")?.innerText || anchor.innerText || "";
        const text = rowText.replace(/\\s+/g, " ").trim();
        const aria = anchor.getAttribute("aria-label") || "";
        const knownName = text.match(/(?:Go to \\d+[,\\dK+]* new results for |View )(.+?)(?: since | lead saved search|$)/)?.[1]
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
          entry.freshText = anchor.innerText.replace(/\\s+/g, " ").trim();
        } else {
          entry.viewUrl = href;
        }
        entry.name = entry.name || knownName;
        byId.set(savedSearchId, entry);
      }
      return Array.from(byId.values());
    })()`);

    const artifact = {
      capturedAt: new Date().toISOString(),
      url: await cdp.evaluate("location.href"),
      searches,
    };
    fs.writeFileSync(out, JSON.stringify(artifact, null, 2));
    console.log(JSON.stringify({ out, count: searches.length, searches: searches.slice(0, 20) }, null, 2));
  } finally {
    cdp.close();
  }
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
