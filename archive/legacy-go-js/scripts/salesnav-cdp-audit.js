#!/usr/bin/env bun
const fs = require("node:fs");
const { connectToTarget, navigate, parseArgs, readPort, waitFor } = require("./salesnav-cdp-lib.js");

async function main() {
  const args = parseArgs();
  const port = readPort(args.port, args.profile);
  const out = args.out || "/tmp/linkedin-network-run-cdp-audit.json";
  const cdp = await connectToTarget({ port, targetUrlIncludes: "linkedin.com" });
  try {
    await navigate(cdp, "https://www.linkedin.com/mynetwork/invitation-manager/sent/");
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const state = await waitFor(
        cdp,
        `(() => {
          const text = document.body?.innerText || "";
          const match = text.match(/People \\(([\\d,]+)\\)/);
          const peopleCount = match ? Number(match[1].replace(/,/g, "")) : null;
          const withdrawCount = (text.match(/\\bWithdraw\\b/g) || []).length;
          return {
            ready: Boolean(match) && (peopleCount > 0 || withdrawCount === 0),
            peopleCount,
            withdrawCount,
            sample: text.slice(0, 1200),
            url: location.href,
          };
        })()`,
        { timeout: 20000, interval: 1000 },
      ).catch((error) => ({ error: String(error) }));
      if (!state.error) {
        break;
      }
      if (attempt === 2) {
        throw new Error(state.error);
      }
      await cdp.send("Page.reload", {}, 30000);
    }
    const audit = await cdp.evaluate(`(() => {
      const text = document.body.innerText || "";
      const match = text.match(/People \\(([\\d,]+)\\)/);
      return {
        capturedAt: new Date().toISOString(),
        url: location.href,
        peopleCount: match ? Number(match[1].replace(/,/g, "")) : null,
        recentNames: Array.from(text.matchAll(/\\n\\n([^\\n]+)\\n\\n[^\\n]+\\n\\nSent /g)).slice(0, 10).map((item) => item[1]),
        sample: text.slice(0, 2000),
      };
    })()`);
    const withdrawCount = (audit.sample.match(/\bWithdraw\b/g) || []).length;
    if (!Number.isFinite(audit.peopleCount) || (audit.peopleCount === 0 && withdrawCount > 0)) {
      throw new Error(`Could not parse People count from sent invitations page: ${audit.sample}`);
    }
    fs.writeFileSync(out, JSON.stringify(audit, null, 2));
    console.log(JSON.stringify({ out, peopleCount: audit.peopleCount, url: audit.url }, null, 2));
  } finally {
    cdp.close();
  }
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
