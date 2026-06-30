const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));

const SALES_NAV_PEOPLE_RESULT_ROW = "li.artdeco-list__item";
const SALES_NAV_PROFILE_LINK = "a[href*='/sales/lead/']";
const SALES_NAV_MORE_ACTIONS_BUTTON = 'button[aria-label^="See more actions for"]';
const SECURITY_VERIFICATION_SELECTOR =
  "iframe#humanThirdPartyIframe,iframe[title='LinkedIn security verification'],iframe[src*='li.protechts.net']";

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function nowIso() {
  return new Date().toISOString();
}

function absoluteLinkedinUrl(href) {
  if (!href) return null;
  try {
    return new URL(href, "https://www.linkedin.com").href;
  } catch {
    return href;
  }
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) return state.linkedinToolsPage;
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((item) => item.url().includes("linkedin.com/sales/search/people")) ||
    pages.find((item) => item.url().includes("linkedin.com/sales/lead/")) ||
    pages.find((item) => item.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

async function visibleCount(page, selector) {
  const locator = page.locator(selector);
  const count = await locator.count().catch(() => 0);
  let visible = 0;
  for (let index = 0; index < count; index += 1) {
    if (await locator.nth(index).isVisible().catch(() => false)) visible += 1;
  }
  return visible;
}

async function classifyPage(page) {
  const url = page.url();
  if (/\/login|\/uas\/login/i.test(url)) return "login required";
  if (/\/checkpoint/i.test(url)) return "checkpoint present";
  if ((await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']")) > 0) return "login required";
  if ((await visibleCount(page, "input[name='pin'], input[name='challengeId']")) > 0) return "checkpoint present";
  if ((await visibleCount(page, SECURITY_VERIFICATION_SELECTOR)) > 0) return "security verification present";
  return null;
}

async function menuLabels(menu) {
  const items = await menu.locator("button,a,[role=menuitem]").all();
  const labels = [];
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    const text = clean(await item.textContent().catch(() => ""));
    const aria = await item.getAttribute("aria-label").catch(() => null);
    const disabled =
      (await item.isDisabled().catch(() => false)) ||
      ((await item.getAttribute("aria-disabled").catch(() => null)) === "true");
    if (text || aria) labels.push({ index, text: text || null, aria, disabled });
  }
  return labels;
}

function classifyMenuLabels(labels) {
  const texts = labels.map((label) => clean(label.text || label.aria || ""));
  if (texts.some((text) => /^(Connect\s*[-–—]\s*)?Pending$/i.test(text))) return "already-pending";
  if (texts.some((text) => /^Connect$/i.test(text))) return "connectable";
  if (texts.some((text) => /email required|enter.*email/i.test(text))) return "email-required";
  return "unknown";
}

async function openRowMenu(page, row) {
  const trigger = row.locator(SALES_NAV_MORE_ACTIONS_BUTTON).first();
  if (!(await trigger.count().catch(() => 0))) return { state: "missing-trigger", labels: [] };
  const menuId = await trigger.getAttribute("aria-controls").catch(() => null);
  try {
    await trigger.click({ timeout: 5000 });
  } catch {
    await trigger.evaluate((element) => element.click());
  }
  await page.waitForTimeout(500);
  const menu = menuId ? page.locator(`#${menuId}`).first() : page.locator("[data-popper-placement]").last();
  if (!(await menu.count().catch(() => 0))) return { state: "missing-menu", labels: [], menu_id: menuId };
  const labels = await menuLabels(menu);
  await page.keyboard.press("Escape").catch(() => null);
  return { state: classifyMenuLabels(labels), labels, menu_id: menuId };
}

async function captureRow(row, index, globalIndex, pageNumber) {
  const profile = row.locator(SALES_NAV_PROFILE_LINK).first();
  const profileUrl =
    (await profile.count().catch(() => 0)) > 0
      ? absoluteLinkedinUrl(await profile.getAttribute("href").catch(() => null))
      : null;
  const nameLocator = row.locator("[data-anonymize='person-name']").first();
  let name =
    (await nameLocator.count().catch(() => 0)) > 0
      ? clean(await nameLocator.textContent().catch(() => ""))
      : null;
  const trigger = row.locator(SALES_NAV_MORE_ACTIONS_BUTTON).first();
  const triggerLabel =
    (await trigger.count().catch(() => 0)) > 0
      ? await trigger.getAttribute("aria-label").catch(() => null)
      : null;
  if (!name && triggerLabel && triggerLabel.startsWith("See more actions for ")) {
    name = triggerLabel.replace("See more actions for ", "").trim() || null;
  }
  const scrollLocator = row.locator("[data-scroll-into-view]").first();
  const scrollUrn =
    (await scrollLocator.count().catch(() => 0)) > 0
      ? await scrollLocator.getAttribute("data-scroll-into-view").catch(() => null)
      : null;
  return {
    index,
    globalIndex,
    pageNumber,
    name,
    profileUrl,
    scrollUrn,
    visibleState: {
      hasMessage: (await row.getByRole("button", { name: /^Message\b/i }).first().count().catch(() => 0)) > 0,
      hasSave: (await row.getByRole("button", { name: /^Save\b/i }).first().count().catch(() => 0)) > 0,
    },
    menuLabels: [],
    menuState: "not-opened",
    links: profileUrl ? [{ href: profileUrl }] : [],
  };
}

function countState(rows, stateName) {
  let count = 0;
  for (const row of rows) {
    if (row.menuState === stateName) count += 1;
  }
  return count;
}

function stateCounts(rows) {
  const counts = {};
  for (const row of rows) {
    const key = row.menuState || "unknown";
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

async function clickNext(page) {
  const button = page.getByRole("button", { name: /^Next$/i }).first();
  if (!(await button.count().catch(() => 0))) return false;
  if (await button.isDisabled().catch(() => false)) return false;
  const before = page.url();
  await button.click({ timeout: 8000 });
  await page.waitForTimeout(1500);
  return page.url() !== before;
}

async function main() {
  const activePage = await getPage();
  if (config.url) {
    await activePage.goto(config.url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
  }
  const blockReason = await classifyPage(activePage);
  if (blockReason) {
    throw new Error(`Sales Navigator capture blocked: ${blockReason}`);
  }
  const allRows = [];
  const pageSummaries = [];
  const totalPages = Math.max(1, Number(config.pages || 1));
  const limit = Math.max(0, Number(config.limit || 25));
  const stopAfterConnectable = Math.max(0, Number(config.stopAfterConnectable || 0));
  for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
    await activePage.waitForTimeout(500);
    pageSummaries.push({ url: activePage.url(), pageLabel: null });
    const rowLocators = await activePage.locator(SALES_NAV_PEOPLE_RESULT_ROW).all();
    const rowLimit = Math.min(limit, rowLocators.length);
    for (let rowIndex = 0; rowIndex < rowLimit; rowIndex += 1) {
      const row = rowLocators[rowIndex];
      await row.scrollIntoViewIfNeeded().catch(() => null);
      if (Number(config.rowScrollDelayMs || 0) > 0) {
        await activePage.waitForTimeout(Number(config.rowScrollDelayMs));
      }
      const item = await captureRow(row, rowIndex, allRows.length, pageNumber);
      const menu = await openRowMenu(activePage, row);
      item.menuLabels = menu.labels || [];
      item.menuState = classifyMenuLabels(item.menuLabels);
      allRows.push(item);
      if (stopAfterConnectable > 0 && countState(allRows, "connectable") >= stopAfterConnectable) break;
    }
    if (stopAfterConnectable > 0 && countState(allRows, "connectable") >= stopAfterConnectable) break;
    if (pageNumber < totalPages && !(await clickNext(activePage))) break;
  }
  const outputRows = [];
  for (const row of allRows) {
    if (!config.onlyConnectable || row.menuState === "connectable") outputRows.push(row);
  }
  const payload = {
    schemaVersion: 1,
    capturedAt: nowIso(),
    url: activePage.url(),
    resumeUrl: activePage.url(),
    source: config.source,
    page: pageSummaries.length ? pageSummaries[pageSummaries.length - 1] : null,
    pages: pageSummaries,
    menuInspection: "menu",
    filters: { onlyConnectable: Boolean(config.onlyConnectable) },
    captureOptions: {
      limit,
      pages: totalPages,
      stopAfterConnectable,
      rowScrollDelayMs: Number(config.rowScrollDelayMs || 0),
      openMenus: true,
      apiState: false,
    },
    apiState: { enabled: false, responses: 0, rows: 0, errors: ["Playwriter capture uses menu evidence"] },
    stateCounts: stateCounts(allRows),
    rawRowCount: allRows.length,
    outputRowCount: outputRows.length,
    rows: outputRows,
  };
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`wrote Sales Navigator capture to ${config.out}`);
}

await main();
