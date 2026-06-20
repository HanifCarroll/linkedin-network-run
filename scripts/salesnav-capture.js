const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_OUT_DIR = "/tmp/linkedin-network-run-capture";

function argValue(name, fallback = null) {
  const config = globalThis.salesNavCaptureConfig || state.salesNavCaptureConfig;
  if (config) {
    const key = name.replace(/^--/, "").replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (Object.prototype.hasOwnProperty.call(config, key)) {
      return config[key];
    }
  }
  if (typeof process === "undefined") {
    return fallback;
  }
  const index = process.argv.indexOf(name);
  if (index === -1 || index + 1 >= process.argv.length) {
    return fallback;
  }
  return process.argv[index + 1];
}

function flag(name) {
  const config = globalThis.salesNavCaptureConfig || state.salesNavCaptureConfig;
  if (config) {
    const key = name.replace(/^--/, "").replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (Object.prototype.hasOwnProperty.call(config, key)) {
      return Boolean(config[key]);
    }
  }
  if (typeof process === "undefined") {
    return false;
  }
  return process.argv.includes(name);
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function parseSalesProfileUrn(urn) {
  const match = String(urn || "").match(/\((.*)\)/);
  if (!match) {
    return null;
  }
  const [profileId, authType, authToken] = match[1].split(",");
  if (!profileId || !authType || !authToken) {
    return null;
  }
  return { profileId, authType, authToken };
}

function salesProfileLeadUrl(profile) {
  if (!profile?.profileId || !profile?.authType || !profile?.authToken) {
    return null;
  }
  return `https://www.linkedin.com/sales/lead/${profile.profileId},${profile.authType},${profile.authToken}`;
}

function normalizeSalesLeadUrl(url) {
  const value = String(url || "");
  if (!value.includes("/sales/lead/")) {
    return null;
  }
  if (/^https?:\/\//i.test(value)) {
    return value;
  }
  return `https://www.linkedin.com${value.startsWith("/") ? "" : "/"}${value}`;
}

function fillProfileUrl(row) {
  if (row.profileUrl) {
    return;
  }
  row.profileUrl =
    normalizeSalesLeadUrl(row.apiState?.openLink)
    || salesProfileLeadUrl(row.salesProfile);
}

function classifyVisibleRow(text, buttons) {
  const saved = /\bSaved\b/.test(text);
  const viewed = /\bViewed\b|You.ve already seen/.test(text);
  const hasMessage = buttons.some((button) => /^Message\b/.test(button.text) || /^Message\b/.test(button.aria || ""));
  const hasSave = buttons.some((button) => /^Save\b/.test(button.text) || /^Save\b/.test(button.aria || ""));
  return { saved, viewed, hasMessage, hasSave };
}

function classifyMenu(labels) {
  const normalized = labels.map((item) => item.text).filter(Boolean);
  if (normalized.some((text) => /^Connect\s*[—-]\s*Pending$/i.test(text) || /^Pending$/i.test(text))) {
    return "already-pending";
  }
  if (normalized.some((text) => /^Connect$/i.test(text))) {
    return "connectable";
  }
  if (normalized.some((text) => /email required|enter.*email/i.test(text))) {
    return "email-required";
  }
  return "unknown";
}

function applyApiState(row, apiRowsByUrn) {
  const api = row.scrollUrn ? apiRowsByUrn.get(row.scrollUrn) : null;
  if (!api) {
    return false;
  }
  row.apiState = api;
  if (api.pendingInvitation === true) {
    row.menuState = "already-pending";
    row.menuLabels = [{ index: 0, text: "Connect - Pending (API pendingInvitation)", aria: null, tag: "API" }];
    return true;
  }
  if (api.pendingInvitation === false && (!api.degree || api.degree === 2)) {
    row.menuState = "connectable";
    row.menuLabels = [{ index: 0, text: "Connect (API pendingInvitation=false)", aria: null, tag: "API" }];
    return true;
  }
  return false;
}

function connectableCount(rows) {
  return rows.filter((item) => item.menuState === "connectable").length;
}

async function extractVisibleRow(rowLocator, index) {
  return rowLocator.evaluate((row, rowIndex) => {
    const buttons = Array.from(row.querySelectorAll("button,[role=button]")).map((button, buttonIndex) => ({
      index: buttonIndex,
      text: (button.innerText || button.textContent || "").replace(/\s+/g, " ").trim(),
      aria: button.getAttribute("aria-label"),
      id: button.id || null,
      disabled: button.hasAttribute("disabled"),
      data: Object.fromEntries(
        Array.from(button.attributes)
          .filter((attr) => attr.name.startsWith("data-") || attr.name === "type")
          .map((attr) => [attr.name, attr.value]),
      ),
    }));
    const links = Array.from(row.querySelectorAll("a")).map((link, linkIndex) => ({
      index: linkIndex,
      text: (link.innerText || link.textContent || "").replace(/\s+/g, " ").trim(),
      aria: link.getAttribute("aria-label"),
      href: link.href || null,
      id: link.id || null,
      data: Object.fromEntries(
        Array.from(link.attributes)
          .filter((attr) => attr.name.startsWith("data-"))
          .map((attr) => [attr.name, attr.value]),
      ),
    }));
    const profileLink = links.find((link) => link.href && link.href.includes("/sales/lead/")) || null;
    const name =
      row.querySelector("[data-anonymize='person-name']")?.textContent?.trim()
      || (row.innerText || "").match(/Add (.+?) to selection/)?.[1]
      || null;
    const scrollUrn = row.querySelector("[data-scroll-into-view]")?.getAttribute("data-scroll-into-view") || null;
    const overflowButton = buttons.find((button) => /^See more actions for /.test(button.aria || "")) || null;
    return {
      index: rowIndex,
      name,
      text: row.innerText || "",
      html: row.outerHTML,
      scrollUrn,
      profileUrl: profileLink?.href || null,
      overflowButtonId: overflowButton?.id || null,
      overflowMenuId: overflowButton?.id ? row.querySelector(`#${overflowButton.id}`)?.getAttribute("aria-controls") : null,
      visibleButtons: buttons,
      links,
    };
  }).catch(() => null);
}

async function main() {
  const outDir = path.resolve(argValue("--out", DEFAULT_OUT_DIR));
  const source = argValue("--source", null);
  const url = argValue("--url", null);
  const openMenus = flag("--open-menus");
  const saveHtml = flag("--save-html");
  const limit = Number(argValue("--limit", "25"));
  const pages = Math.max(1, Number(argValue("--pages", "1")));
  const stopAfterConnectable = Number(argValue("--stop-after-connectable", "0"));
  const onlyConnectable = flag("--only-connectable");
  const rowScrollDelayMs = Math.max(0, Number(argValue("--row-scroll-delay-ms", "250")));
  const useApiState = String(argValue("--api-state", "true")) !== "false";

  fs.mkdirSync(path.join(outDir, "rows"), { recursive: true });

  state.page = state.page || context.pages().find((page) => page.url().includes("/sales/search/people")) || await context.newPage();
  const apiRowsByUrn = new Map();
  const apiState = { enabled: useApiState, responses: 0, rows: 0, errors: [] };
  const responseHandler = async (response) => {
    if (!useApiState || !/\/sales-api\/salesApiLeadSearch/i.test(response.url())) {
      return;
    }
    try {
      const data = await response.json();
      apiState.responses += 1;
      for (const element of data.elements || []) {
        if (!element.entityUrn) continue;
        apiRowsByUrn.set(element.entityUrn, {
          entityUrn: element.entityUrn,
          fullName: element.fullName || null,
          pendingInvitation: element.pendingInvitation,
          degree: element.degree,
          saved: element.saved,
          viewed: element.viewed,
          openLink: element.openLink || null,
        });
      }
      apiState.rows = apiRowsByUrn.size;
    } catch (error) {
      apiState.errors.push(cleanText(error?.message || error).slice(0, 300));
    }
  };
  state.page.on("response", responseHandler);
  if (url) {
    await state.page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await state.page.waitForLoadState("domcontentloaded");
  }
  const allRows = [];
  const pageSummaries = [];
  let stopReason = null;

  for (let pageNumber = 1; pageNumber <= pages; pageNumber += 1) {
    await state.page.waitForFunction(
      () => /Search Results|No results|0 results/i.test(document.body.innerText || ""),
      null,
      { timeout: 20000 },
    ).catch(() => {});
    await state.page.waitForFunction(
      () => Array.from(document.querySelectorAll("li.artdeco-list__item"))
        .some((row) => /Add .+ to selection/.test(row.innerText || "")),
      null,
      { timeout: 12000 },
    ).catch(() => {});
    await state.page.waitForFunction(
      () => document.querySelectorAll("a[href*='/sales/lead/'], button[aria-label^='See more actions for']").length > 0,
      null,
      { timeout: 30000 },
    ).catch(() => {});
    await state.page.waitForTimeout(1000);

    const pageMeta = await state.page.evaluate(() => {
    const bodyText = document.body.innerText || "";
    const pageLabel = bodyText.match(/Page \d+ of \d+/)?.[0] || null;
    const resultCount = bodyText.match(/\b[\d.K+]+ results(?: found)?\b/)?.[0] || null;
    const sourceName = Array.from(document.querySelectorAll("main *"))
      .map((node) => node.textContent?.trim())
      .find((text) => text && /^Network - |^FO - |Ops-overwhelmed/.test(text)) || null;
    return {
      url: window.location.href,
      title: document.title,
      pageLabel,
      resultCount,
      sourceName,
      bodyText: bodyText.slice(0, 2000),
    };
    });
    pageSummaries.push(pageMeta);

    const rows = await state.page.evaluate((maxRows) => {
    return Array.from(document.querySelectorAll("li.artdeco-list__item"))
      .filter((row) => /Add .+ to selection/.test(row.innerText || ""))
      .slice(0, maxRows)
      .map((row, index) => {
        const buttons = Array.from(row.querySelectorAll("button,[role=button]")).map((button, buttonIndex) => ({
          index: buttonIndex,
          text: (button.innerText || button.textContent || "").replace(/\s+/g, " ").trim(),
          aria: button.getAttribute("aria-label"),
          id: button.id || null,
          disabled: button.hasAttribute("disabled"),
          data: Object.fromEntries(
            Array.from(button.attributes)
              .filter((attr) => attr.name.startsWith("data-") || attr.name === "type")
              .map((attr) => [attr.name, attr.value]),
          ),
        }));
        const links = Array.from(row.querySelectorAll("a")).map((link, linkIndex) => ({
          index: linkIndex,
          text: (link.innerText || link.textContent || "").replace(/\s+/g, " ").trim(),
          aria: link.getAttribute("aria-label"),
          href: link.href || null,
          id: link.id || null,
          data: Object.fromEntries(
            Array.from(link.attributes)
              .filter((attr) => attr.name.startsWith("data-"))
              .map((attr) => [attr.name, attr.value]),
          ),
        }));
        const profileLink = links.find((link) => link.href && link.href.includes("/sales/lead/")) || null;
        const name =
          row.querySelector("[data-anonymize='person-name']")?.textContent?.trim()
          || (row.innerText || "").match(/Add (.+?) to selection/)?.[1]
          || null;
        const scrollUrn = row.querySelector("[data-scroll-into-view]")?.getAttribute("data-scroll-into-view") || null;
        const overflowButton = buttons.find((button) => /^See more actions for /.test(button.aria || "")) || null;
        return {
          index,
          name,
          text: row.innerText || "",
          html: row.outerHTML,
          scrollUrn,
          profileUrl: profileLink?.href || null,
          overflowButtonId: overflowButton?.id || null,
          overflowMenuId: overflowButton?.id ? row.querySelector(`#${overflowButton.id}`)?.getAttribute("aria-controls") : null,
          visibleButtons: buttons,
          links,
        };
      });
    }, limit);

    for (const row of rows) {
      row.pageNumber = pageNumber;
      row.globalIndex = allRows.length;
      row.salesProfile = parseSalesProfileUrn(row.scrollUrn);
      row.visibleState = classifyVisibleRow(row.text, row.visibleButtons);
      row.menuLabels = [];
      row.menuState = "not-opened";
      applyApiState(row, apiRowsByUrn);
      fillProfileUrl(row);

      if (saveHtml) {
        const fileName = `page-${String(pageNumber).padStart(2, "0")}-row-${String(row.index).padStart(2, "0")}.html`;
        fs.writeFileSync(path.join(outDir, "rows", fileName), row.html);
        row.rowHtmlPath = path.join(outDir, "rows", fileName);
      }
      delete row.html;
    }

    if (openMenus) {
      const rowLocators = await state.page.locator("li.artdeco-list__item").filter({ hasText: /Add .+ to selection/ }).all();
      for (let index = 0; index < Math.min(rows.length, rowLocators.length); index += 1) {
        const row = rows[index];
        const rowLocator = rowLocators[index];
        if (applyApiState(row, apiRowsByUrn)) {
          if (stopAfterConnectable > 0 && connectableCount(allRows) + connectableCount(rows) >= stopAfterConnectable) {
            stopReason = `stopAfterConnectable reached by API state at page ${pageNumber}, row ${index}`;
            break;
          }
          continue;
        }
        await state.page.keyboard.press("Escape").catch(() => {});
        await rowLocator.scrollIntoViewIfNeeded().catch(() => {});
        if (rowScrollDelayMs > 0) {
          await state.page.waitForTimeout(rowScrollDelayMs);
        }
        const fresh = await extractVisibleRow(rowLocator, row.index);
        if (fresh) {
          const meta = {
            pageNumber: row.pageNumber,
            globalIndex: row.globalIndex,
            rowHtmlPath: row.rowHtmlPath,
          };
          Object.assign(row, fresh, meta);
          row.salesProfile = parseSalesProfileUrn(row.scrollUrn);
          row.visibleState = classifyVisibleRow(row.text, row.visibleButtons);
          row.menuLabels = [];
          row.menuState = "not-opened";
          delete row.html;
          if (applyApiState(row, apiRowsByUrn)) {
            fillProfileUrl(row);
            if (stopAfterConnectable > 0 && connectableCount(allRows) + connectableCount(rows) >= stopAfterConnectable) {
              stopReason = `stopAfterConnectable reached by API state at page ${pageNumber}, row ${index}`;
              break;
            }
            continue;
          }
          fillProfileUrl(row);
        }

        const trigger = rowLocator.locator("button[aria-label^=\"See more actions for\"]").first();
        await trigger.waitFor({ state: "visible", timeout: 1500 }).catch(() => {});
        if (!(await trigger.count()) || !(await trigger.isVisible().catch(() => false))) {
          row.menuState = "missing-trigger";
          continue;
        }
        const menuId = await trigger.getAttribute("aria-controls");
        row.overflowMenuId = menuId || row.overflowMenuId;
        try {
          await trigger.click({ timeout: 5000 });
        } catch (error) {
          await trigger.click({ timeout: 5000, force: true }).catch((forcedError) => {
            row.menuState = "missing-menu";
            row.menuClickError = cleanText(forcedError?.message || error?.message || "menu click failed").slice(0, 500);
          });
          if (row.menuState === "missing-menu") {
            continue;
          }
        }
        await state.page.waitForTimeout(400);
        const menu = menuId ? state.page.locator(`#${menuId}`) : state.page.locator("[data-popper-placement]").last();
        if (!(await menu.count())) {
          row.menuState = "missing-menu";
          continue;
        }
        const labels = await menu.locator("button,a,[role=menuitem]").evaluateAll((items) =>
          items.map((item, itemIndex) => ({
            index: itemIndex,
            text: (item.innerText || item.textContent || "").replace(/\s+/g, " ").trim(),
            aria: item.getAttribute("aria-label"),
            tag: item.tagName,
            href: item.href || null,
            disabled: item.hasAttribute("disabled"),
          })).filter((item) => item.text || item.aria),
        );
        row.menuLabels = labels;
        row.menuState = classifyMenu(labels);
        if (stopAfterConnectable > 0) {
          const currentConnectableCount = connectableCount(allRows) + connectableCount(rows);
          if (currentConnectableCount >= stopAfterConnectable) {
            stopReason = `stopAfterConnectable reached at page ${pageNumber}, row ${index}`;
            break;
          }
        }
      }
      await state.page.keyboard.press("Escape");
    }

    allRows.push(...rows);
    const totalConnectableCount = connectableCount(allRows);
    if (stopAfterConnectable > 0 && totalConnectableCount >= stopAfterConnectable) {
      break;
    }
    if (pageNumber < pages) {
      const nextButton = state.page.getByRole("button", { name: /^Next$/ }).first();
      if (!(await nextButton.count()) || await nextButton.isDisabled().catch(() => false)) {
        break;
      }
      const beforeUrl = state.page.url();
      await nextButton.scrollIntoViewIfNeeded().catch(() => {});
      await nextButton.click({ timeout: 8000 });
      await state.page.waitForTimeout(2500);
      if (state.page.url() === beforeUrl) {
        await state.page.waitForTimeout(1500);
      }
    }
  }

  for (let rowIndex = 0; rowIndex < allRows.length; rowIndex += 1) {
    const row = allRows[rowIndex];
    if (!Number.isInteger(row.index)) {
      row.index = Number.isInteger(row.globalIndex) ? row.globalIndex : rowIndex;
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
    url: state.page.url(),
    resumeUrl: state.page.url(),
    source: source || pageSummaries.find((summary) => summary.sourceName)?.sourceName || null,
    page: pageSummaries[pageSummaries.length - 1] || null,
    pages: pageSummaries,
    menuInspection: useApiState ? (openMenus ? "api-state-with-menu-fallback" : "api-state-only") : (openMenus ? "opened-row-overflow-menus" : "visible-dom-only"),
    filters: { onlyConnectable },
    captureOptions: { limit, pages, stopAfterConnectable, rowScrollDelayMs, openMenus, apiState: useApiState },
    apiState,
    stateCounts,
    rawRowCount: allRows.length,
    outputRowCount: outputRows.length,
    stopReason,
    rows: outputRows,
  };

  fs.writeFileSync(path.join(outDir, "page.json"), JSON.stringify(capture, null, 2));
  state.page.off("response", responseHandler);
  console.log(JSON.stringify({
    out: path.join(outDir, "page.json"),
    url: capture.url,
    source: capture.source,
    rowCount: allRows.length,
    outputRowCount: outputRows.length,
    menuInspection: capture.menuInspection,
    filters: capture.filters,
    apiState,
    states: stateCounts,
    stopReason,
  }, null, 2));
}

await main();
