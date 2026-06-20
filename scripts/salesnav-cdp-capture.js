#!/usr/bin/env bun
const fs = require("node:fs");
const path = require("node:path");
const { connectToTarget, navigate, parseArgs, readPort, waitFor } = require("./salesnav-cdp-lib.js");

function parseSalesProfileUrn(urn) {
  const match = String(urn || "").match(/\((.*)\)/);
  if (!match) return null;
  const [profileId, authType, authToken] = match[1].split(",");
  if (!profileId || !authType || !authToken) return null;
  return { profileId, authType, authToken };
}

function canonicalSalesLeadUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!url.pathname.includes("/sales/lead/")) return value;
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return value;
  }
}

function classifyVisibleRow(text, buttons) {
  const saved = /\bSaved\b/.test(text);
  const viewed = /\bViewed\b|You.ve already seen/.test(text);
  const hasMessage = buttons.some((button) => /^Message\b/.test(button.text) || /^Message\b/.test(button.aria || ""));
  const hasSave = buttons.some((button) => /^Save\b/.test(button.text) || /^Save\b/.test(button.aria || ""));
  return { saved, viewed, hasMessage, hasSave };
}

function classifyMenu(labels) {
  const texts = labels.map((item) => item.text).filter(Boolean);
  if (texts.some((text) => /^Connect\s*[—-]\s*Pending$/i.test(text) || /^Pending$/i.test(text))) return "already-pending";
  if (texts.some((text) => /^Connect$/i.test(text))) return "connectable";
  if (texts.some((text) => /email required|enter.*email/i.test(text))) return "email-required";
  return "unknown";
}

async function main() {
  const args = parseArgs();
  const port = readPort(args.port, args.profile);
  const outDir = path.resolve(args.out || "/tmp/linkedin-network-run-cdp-capture");
  const source = args.source || null;
  const url = args.url;
  const limit = Number(args.limit || 25);
  const pages = Math.max(1, Number(args.pages || 1));
  const stopAfterConnectable = Number(args.stopAfterConnectable || 0);
  const openMenus = args.openMenus !== false && args.openMenus !== "false";
  const onlyConnectable = args.onlyConnectable === true || args.onlyConnectable === "true";

  if (!url) {
    throw new Error("--url is required");
  }
  fs.mkdirSync(path.join(outDir, "rows"), { recursive: true });

  const cdp = await connectToTarget({ port, targetUrlIncludes: "linkedin.com" });
  const allRows = [];
  const pageSummaries = [];

  try {
    await navigate(cdp, url);
    for (let pageNumber = 1; pageNumber <= pages; pageNumber += 1) {
      await waitFor(
        cdp,
        `(() => {
          const text = document.body?.innerText || "";
          const rowCount = Array.from(document.querySelectorAll("li.artdeco-list__item")).filter((row) => /Add .+ to selection/.test(row.innerText || "")).length;
          return { ready: rowCount > 0 || /No results|0 results/i.test(text), rowCount, url: location.href, sample: text.slice(0, 1000) };
        })()`,
        { timeout: 45000, interval: 1000 },
      );
      await waitFor(
        cdp,
        `(() => {
          const text = document.body?.innerText || "";
          const cardActionCount = document.querySelectorAll("a[href*='/sales/lead/'], button[aria-label^='See more actions for']").length;
          return { ready: cardActionCount > 0 || /No results|0 results|technical difficulties|something has gone wrong/i.test(text), cardActionCount, url: location.href, sample: text.slice(0, 1000) };
        })()`,
        { timeout: 30000, interval: 1000 },
      ).catch(() => {});

      const pageMeta = await cdp.evaluate(`(() => {
        const bodyText = document.body.innerText || "";
        const pageLabel = bodyText.match(/Page \\d+ of \\d+/)?.[0] || null;
        const resultCount = bodyText.match(/\\b[\\d.K+]+ results(?: found)?\\b/)?.[0] || null;
        const sourceName = Array.from(document.querySelectorAll("main *"))
          .map((node) => node.textContent?.trim())
          .find((text) => text && (/^Network - /.test(text) || /^FO - /.test(text) || /^Ops-overwhelmed/.test(text))) || null;
        return { title: document.title, pageLabel, resultCount, sourceName, bodyText: bodyText.slice(0, 2000) };
      })()`);
      pageSummaries.push(pageMeta);

      const rows = await cdp.evaluate(`(async () => {
        const rowNodes = Array.from(document.querySelectorAll("li.artdeco-list__item"))
          .filter((row) => /Add .+ to selection/.test(row.innerText || ""))
          .slice(0, ${JSON.stringify(limit)});
        const out = [];
        for (let index = 0; index < rowNodes.length; index += 1) {
          const row = rowNodes[index];
          row.scrollIntoView({ block: "center" });
          await new Promise((resolve) => setTimeout(resolve, 150));
          const buttons = Array.from(row.querySelectorAll("button,[role=button]")).map((button, buttonIndex) => ({
            index: buttonIndex,
            text: (button.innerText || button.textContent || "").replace(/\\s+/g, " ").trim(),
            aria: button.getAttribute("aria-label"),
            id: button.id || null,
            disabled: button.hasAttribute("disabled"),
            data: Object.fromEntries(Array.from(button.attributes).filter((attr) => attr.name.startsWith("data-") || attr.name === "type").map((attr) => [attr.name, attr.value])),
          }));
          const links = Array.from(row.querySelectorAll("a")).map((link, linkIndex) => ({
            index: linkIndex,
            text: (link.innerText || link.textContent || "").replace(/\\s+/g, " ").trim(),
            aria: link.getAttribute("aria-label"),
            href: link.href || null,
            id: link.id || null,
            data: Object.fromEntries(Array.from(link.attributes).filter((attr) => attr.name.startsWith("data-")).map((attr) => [attr.name, attr.value])),
          }));
          const profileLink = links.find((link) => link.href && link.href.includes("/sales/lead/")) || null;
          const name = row.querySelector("[data-anonymize='person-name']")?.textContent?.trim() || (row.innerText || "").match(/Add (.+?) to selection/)?.[1] || null;
          const scrollUrn = row.querySelector("[data-scroll-into-view]")?.getAttribute("data-scroll-into-view") || null;
          const overflowButton = buttons.find((button) => /^See more actions for /.test(button.aria || "")) || null;
          out.push({
            index,
            name,
            text: row.innerText || "",
            scrollUrn,
            profileUrl: profileLink?.href || null,
            overflowButtonId: overflowButton?.id || null,
            overflowMenuId: overflowButton?.id ? row.querySelector("#" + CSS.escape(overflowButton.id))?.getAttribute("aria-controls") : null,
            visibleButtons: buttons,
            links,
          });
        }
        return out;
      })()`);

      for (const row of rows) {
        row.pageNumber = pageNumber;
        row.globalIndex = allRows.length;
        row.profileUrl = canonicalSalesLeadUrl(row.profileUrl);
        row.salesProfile = parseSalesProfileUrn(row.scrollUrn);
        row.visibleState = classifyVisibleRow(row.text, row.visibleButtons);
        row.menuLabels = [];
        row.menuState = "not-opened";
        allRows.push(row);
      }

      if (openMenus) {
        for (let index = 0; index < rows.length; index += 1) {
          const globalIndex = allRows.length - rows.length + index;
          const menuResult = await cdp.evaluate(`(async () => {
            const rows = Array.from(document.querySelectorAll("li.artdeco-list__item")).filter((row) => /Add .+ to selection/.test(row.innerText || ""));
            const row = rows[${index}];
            if (!row) return { menuState: "missing-row", labels: [] };
            row.scrollIntoView({ block: "center" });
            await new Promise((resolve) => setTimeout(resolve, 150));
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
            const trigger = row.querySelector("button[aria-label^='See more actions for']");
            if (!trigger) return { menuState: "missing-trigger", labels: [] };
            const menuId = trigger.getAttribute("aria-controls");
            trigger.click();
            await new Promise((resolve) => setTimeout(resolve, 450));
            const menu = menuId ? document.getElementById(menuId) : Array.from(document.querySelectorAll("[data-popper-placement]")).at(-1);
            if (!menu) return { menuState: "missing-menu", labels: [] };
            const labels = Array.from(menu.querySelectorAll("button,a,[role=menuitem]")).map((item, itemIndex) => ({
              index: itemIndex,
              text: (item.innerText || item.textContent || "").replace(/\\s+/g, " ").trim(),
              aria: item.getAttribute("aria-label"),
              tag: item.tagName,
              href: item.href || null,
              disabled: item.hasAttribute("disabled"),
            })).filter((item) => item.text || item.aria);
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
            return { menuState: "opened", labels };
          })()`);
          allRows[globalIndex].menuLabels = menuResult.labels || [];
          allRows[globalIndex].menuState = classifyMenu(allRows[globalIndex].menuLabels);
        }
      }

      const connectableCount = allRows.filter((row) => row.menuState === "connectable").length;
      if (stopAfterConnectable > 0 && connectableCount >= stopAfterConnectable) {
        break;
      }
      if (pageNumber < pages) {
        const moved = await cdp.evaluate(`(async () => {
          const before = location.href;
          const next = Array.from(document.querySelectorAll("button")).find((button) => /^Next$/.test((button.innerText || button.textContent || "").trim()));
          if (!next || next.disabled || next.getAttribute("aria-disabled") === "true") return false;
          next.scrollIntoView({ block: "center" });
          next.click();
          await new Promise((resolve) => setTimeout(resolve, 2500));
          return location.href !== before;
        })()`);
        if (!moved) break;
      }
    }

    const outputRows = onlyConnectable ? allRows.filter((row) => row.menuState === "connectable") : allRows;
    const stateCounts = allRows.reduce((acc, row) => {
      acc[row.menuState] = (acc[row.menuState] || 0) + 1;
      return acc;
    }, {});
    const capture = {
      schemaVersion: 1,
      capturedAt: new Date().toISOString(),
      url: await cdp.evaluate("location.href"),
      source: source || pageSummaries.find((summary) => summary.sourceName)?.sourceName || null,
      page: pageSummaries[pageSummaries.length - 1] || null,
      pages: pageSummaries,
      menuInspection: openMenus ? "opened-row-overflow-menus-cdp" : "visible-dom-only",
      filters: { onlyConnectable },
      stateCounts,
      rawRowCount: allRows.length,
      rows: outputRows,
    };
    fs.writeFileSync(path.join(outDir, "page.json"), JSON.stringify(capture, null, 2));
    console.log(JSON.stringify({
      out: path.join(outDir, "page.json"),
      url: capture.url,
      source: capture.source,
      rowCount: allRows.length,
      outputRowCount: outputRows.length,
      menuInspection: capture.menuInspection,
      filters: capture.filters,
      states: stateCounts,
    }, null, 2));
  } finally {
    cdp.close();
  }
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
