const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const candidate = config.candidate || {};
const dryRun = Boolean(config.dryRun);
const allowSend = Boolean(config.allowSend);

const SALES_NAV_PEOPLE_RESULT_ROW = "li.artdeco-list__item";
const SALES_NAV_PROFILE_LINK = "a[href*='/sales/lead/']";
const SALES_NAV_MORE_ACTIONS_BUTTON = 'button[aria-label^="See more actions for"]';
const SALES_NAV_OPEN_ACTIONS_BUTTON = 'button[aria-label="Open actions overflow menu"]';
const LINKEDIN_DIALOG = "[role='dialog'], .artdeco-modal, [data-test-modal]";
const SECURITY_VERIFICATION_SELECTOR =
  "iframe#humanThirdPartyIframe,iframe[title='LinkedIn security verification'],iframe[src*='li.protechts.net']";

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function nowIso() {
  return new Date().toISOString();
}

function normalizePublicProfileUrl(value) {
  try {
    const parsed = new URL(String(value || ""), "https://www.linkedin.com");
    if (!["linkedin.com", "www.linkedin.com"].includes(parsed.hostname)) return null;
    const parts = parsed.pathname.split("/").filter(Boolean);
    if (parts.length < 2 || parts[0] !== "in") return null;
    return `https://www.linkedin.com/in/${encodeURIComponent(decodeURIComponent(parts[1]))}`;
  } catch {
    return null;
  }
}

function basePayload(url) {
  return {
    candidate: {
      source: candidate.source,
      name: candidate.name,
      profileUrl: candidate.profile_url || candidate.profileUrl || null,
    },
    dryRun,
    url,
    status: "unknown",
    publicProfileUrl: candidate.public_profile_url || candidate.publicProfileUrl || null,
    searchUrl: candidate.search_url || candidate.searchUrl || null,
  };
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
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
  if (/\/login|\/uas\/login/i.test(url)) return { blocked: true, reason: "login required" };
  if (/\/checkpoint/i.test(url)) return { blocked: true, reason: "checkpoint present" };
  if ((await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']")) > 0) {
    return { blocked: true, reason: "login required" };
  }
  if ((await visibleCount(page, "input[name='pin'], input[name='challengeId']")) > 0) {
    return { blocked: true, reason: "checkpoint present" };
  }
  if ((await visibleCount(page, SECURITY_VERIFICATION_SELECTOR)) > 0) {
    return { blocked: true, reason: "security verification present" };
  }
  return { blocked: false, reason: null };
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
  if (texts.some((text) => /^(Connect\s*[-–—]\s*)?Pending$/i.test(text))) {
    return "already-pending";
  }
  if (texts.some((text) => /^Connect$/i.test(text))) return "connectable";
  if (texts.some((text) => /email required|enter.*email/i.test(text))) return "email-required";
  return "unknown";
}

async function clickReadonly(locator, timeout) {
  try {
    await locator.click({ timeout });
  } catch {
    await locator.evaluate((element) => element.click());
  }
}

async function openMenuFromTrigger(page, trigger, closeAfter) {
  if (!(await trigger.count().catch(() => 0))) return { state: "missing-trigger", labels: [] };
  const menuId = await trigger.getAttribute("aria-controls").catch(() => null);
  await clickReadonly(trigger, 8000);
  await page.waitForTimeout(500);
  const menu = menuId ? page.locator(`#${menuId}`).first() : page.locator("[data-popper-placement]").last();
  if (!(await menu.count().catch(() => 0))) {
    return { state: "missing-menu", labels: [], menu_id: menuId };
  }
  const labels = await menuLabels(menu);
  if (closeAfter) await page.keyboard.press("Escape").catch(() => null);
  return { state: classifyMenuLabels(labels), labels, menu_id: menuId };
}

async function openProfileActionsMenu(page) {
  let trigger = page.locator(SALES_NAV_OPEN_ACTIONS_BUTTON).first();
  if (!(await trigger.count().catch(() => 0))) {
    trigger = page.locator(SALES_NAV_MORE_ACTIONS_BUTTON).first();
  }
  return openMenuFromTrigger(page, trigger, false);
}

async function findMenuItem(page, menuId, label) {
  const menu = menuId ? page.locator(`#${menuId}`).first() : page.locator("[data-popper-placement]").last();
  const items = await menu.locator("button,a,[role=menuitem]").all();
  for (const item of items) {
    const text = clean(await item.textContent().catch(() => ""));
    const aria = clean(await item.getAttribute("aria-label").catch(() => ""));
    if (new RegExp(`^${label}$`, "i").test(text) || new RegExp(`^${label}$`, "i").test(aria)) {
      return item;
    }
  }
  return null;
}

function salesLeadPath(value) {
  try {
    const parsed = new URL(String(value || ""), "https://www.linkedin.com");
    const parts = parsed.pathname.split("/").filter(Boolean);
    if (parts.length < 3 || parts[0] !== "sales" || parts[1] !== "lead") return null;
    const leadId = parts[2].split(",", 1)[0];
    return leadId ? `/sales/lead/${leadId}` : null;
  } catch {
    return null;
  }
}

async function waitForSearchRows(page) {
  for (let attempt = 0; attempt < 45; attempt += 1) {
    const count = await page.locator(SALES_NAV_PEOPLE_RESULT_ROW).count().catch(() => 0);
    if (count > 0) return count;
    await page.waitForTimeout(1000);
  }
  return 0;
}

async function findSearchResultRow(page, profileUrl) {
  const targetPath = salesLeadPath(profileUrl);
  if (!targetPath) return null;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const rows = await page.locator(SALES_NAV_PEOPLE_RESULT_ROW).all();
    for (const row of rows) {
      const links = await row.locator(SALES_NAV_PROFILE_LINK).all();
      for (const link of links) {
        const href = await link.getAttribute("href").catch(() => null);
        if (salesLeadPath(href) === targetPath) return row;
      }
    }
    await page.mouse.wheel(0, 900).catch(() => null);
    await page.waitForTimeout(500);
  }
  return null;
}

async function openSearchRowMenu(page, row) {
  const trigger = row.locator(SALES_NAV_MORE_ACTIONS_BUTTON).first();
  return openMenuFromTrigger(page, trigger, false);
}

async function openNormalProfileMoreMenu(page) {
  const buttons = await page.getByRole("button", { name: /^More$/i }).all();
  for (const button of buttons) {
    if (!(await button.isVisible().catch(() => false))) continue;
    await clickReadonly(button, 8000);
    await page.waitForTimeout(500);
    const labels = await normalProfileMenuLabels(page);
    if (labels.length > 0) return { state: classifyMenuLabels(labels), labels };
    await page.keyboard.press("Escape").catch(() => null);
  }
  return { state: "missing-trigger", labels: [] };
}

async function normalProfileMenuLabels(page) {
  const items = await page
    .locator("[role='menu'] button,[role='menu'] a,[role='menuitem'],.artdeco-dropdown__content button,.artdeco-dropdown__content a")
    .all();
  const labels = [];
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (!(await item.isVisible().catch(() => false))) continue;
    const text = clean(await item.textContent().catch(() => ""));
    const aria = await item.getAttribute("aria-label").catch(() => null);
    const href = await item.getAttribute("href").catch(() => null);
    const disabled =
      (await item.isDisabled().catch(() => false)) ||
      ((await item.getAttribute("aria-disabled").catch(() => null)) === "true");
    if (text || aria || href) labels.push({ index, text: text || null, aria, href, disabled });
  }
  return labels;
}

async function findNormalProfileMenuItem(page, label) {
  const items = await page
    .locator("[role='menu'] button,[role='menu'] a,[role='menuitem'],.artdeco-dropdown__content button,.artdeco-dropdown__content a")
    .all();
  for (const item of items) {
    if (!(await item.isVisible().catch(() => false))) continue;
    const text = clean(await item.textContent().catch(() => ""));
    const aria = clean(await item.getAttribute("aria-label").catch(() => ""));
    if (new RegExp(`^${label}$`, "i").test(text) || new RegExp(`^${label}$`, "i").test(aria)) {
      return item;
    }
  }
  return null;
}

async function publicProfileUrlFromMenuItemHrefs(page, menuId) {
  const menu = menuId ? page.locator(`#${menuId}`).first() : page.locator("[data-popper-placement]").last();
  const items = await menu.locator("button,a,[role=menuitem]").all();
  for (const item of items) {
    const text = clean(await item.textContent().catch(() => ""));
    const aria = clean(await item.getAttribute("aria-label").catch(() => ""));
    if (!/^View LinkedIn profile$/i.test(text) && !/^View LinkedIn profile$/i.test(aria)) {
      continue;
    }
    const href = await item.getAttribute("href").catch(() => null);
    const normalized = normalizePublicProfileUrl(href);
    if (normalized) return normalized;
  }
  return null;
}

async function publicProfileUrlFromCopyAction(page, menuId) {
  const copy = await findMenuItem(page, menuId, "Copy LinkedIn.com URL");
  if (!copy) return null;
  await copy.click({ timeout: 8000 }).catch(async () => copy.evaluate((element) => element.click()));
  await page.waitForTimeout(500);
  const clipboardText = await page.evaluate(async () => navigator.clipboard.readText()).catch(() => null);
  return normalizePublicProfileUrl(clipboardText);
}

async function capturePublicProfileUrl(page, menuId) {
  const fromHref = await publicProfileUrlFromMenuItemHrefs(page, menuId);
  if (fromHref) return fromHref;
  return publicProfileUrlFromCopyAction(page, menuId);
}

async function clickSendInvitation(page) {
  if ((await page.locator("input[type='email'], input[name*='email' i]").first().count().catch(() => 0)) > 0) {
    return { status: "email-required" };
  }
  let sendButton = null;
  const dialogs = await page.locator(LINKEDIN_DIALOG).all();
  for (const dialog of dialogs) {
    if (!(await dialog.isVisible().catch(() => false))) continue;
    const buttons = await dialog.locator("button").all();
    for (const button of buttons) {
      if (!(await button.isVisible().catch(() => false))) continue;
      const text = clean(await button.textContent().catch(() => ""));
      const aria = clean(await button.getAttribute("aria-label").catch(() => ""));
      if (/^(Send Invitation|Send invite|Send now|Send without a note|Send)$/i.test(text || aria)) {
        sendButton = button;
      }
    }
  }
  if (!sendButton) return { status: "send-button-missing" };
  if (await sendButton.isDisabled().catch(() => false)) return { status: "send-button-disabled" };
  if (!allowSend) return { status: "blocked", reason: "real send requires allowSend" };
  await sendButton.click({ timeout: 8000 }).catch(async () => sendButton.evaluate((element) => element.click()));
  return { status: "clicked-send", label: "Send Invitation" };
}

function statusFromSend(status) {
  if (["email-required", "blocked", "identity-mismatch"].includes(status)) return status;
  return `unverified:${status}`;
}

async function sendFromCurrentPage(page) {
  const payload = basePayload(page.url());
  const block = await classifyPage(page);
  if (block.blocked) {
    payload.status = "blocked";
    payload.reason = block.reason;
    return payload;
  }
  const menu = await openProfileActionsMenu(page);
  payload.before = menu;
  payload.publicProfileUrl =
    payload.publicProfileUrl || (await capturePublicProfileUrl(page, menu.menu_id).catch(() => null));
  const menuState = classifyMenuLabels(menu.labels || []);
  if (menuState === "already-pending") {
    payload.status = "already-pending";
  } else if (menuState !== "connectable") {
    if (payload.publicProfileUrl) {
      return sendFromPublicProfile(page, payload, `sales-nav:${menuState}`);
    }
    if (payload.searchUrl) {
      return sendFromSearchRow(page, payload, `sales-nav:${menuState}`);
    }
    payload.status = `not-connectable:${menuState}`;
  } else if (dryRun) {
    payload.status = "dry-run-connectable";
  } else {
    await page.keyboard.press("Escape").catch(() => null);
    const sendMenu = await openProfileActionsMenu(page);
    const connect = await findMenuItem(page, sendMenu.menu_id, "Connect");
    if (!connect) {
      payload.status = "not-connectable:missing-connect-menu";
      payload.after = { state: "missing-connect-menu" };
      return payload;
    }
    await connect.click({ timeout: 8000 });
    await page.waitForTimeout(500);
    const send = await clickSendInvitation(page);
    payload.send = { ...send, guard: { action: "send_connection", allowed: allowSend } };
    if (send.status !== "clicked-send") {
      payload.status = statusFromSend(send.status);
      payload.after = { state: send.status };
    } else {
      await page.waitForTimeout(1500);
      const after = await openProfileActionsMenu(page);
      payload.after = after;
      payload.status =
        classifyMenuLabels(after.labels || []) === "already-pending"
          ? "pending-provisional"
          : "unverified:clicked-send";
    }
  }
  await page.keyboard.press("Escape").catch(() => null);
  return payload;
}

async function sendFromPublicProfile(page, salesPayload, reason) {
  const publicUrl = normalizePublicProfileUrl(salesPayload.publicProfileUrl);
  const payload = {
    ...salesPayload,
    status: "unknown",
    salesNavUrl: salesPayload.url,
    url: publicUrl || salesPayload.publicProfileUrl,
    fallback: { type: "linkedin-profile", reason },
  };
  if (!publicUrl) {
    payload.status = "not-connectable:linkedin-profile-url-invalid";
    return payload;
  }
  await page.goto(publicUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
  await page.waitForTimeout(1500);
  payload.url = page.url();
  const block = await classifyPage(page);
  if (block.blocked) {
    payload.status = "blocked";
    payload.reason = block.reason;
    return payload;
  }
  const menu = await openNormalProfileMoreMenu(page);
  payload.fallback.before = menu;
  const connect = await findNormalProfileMenuItem(page, "Connect");
  if (!connect) {
    payload.status =
      classifyMenuLabels(menu.labels || []) === "already-pending"
        ? "already-pending"
        : "not-connectable:linkedin-profile-missing-connect";
    return payload;
  }
  if (dryRun) {
    payload.status = "dry-run-connectable";
    await page.keyboard.press("Escape").catch(() => null);
    return payload;
  }
  if (!allowSend) {
    payload.status = "blocked";
    payload.reason = "real send requires allowSend";
    return payload;
  }
  await connect.click({ timeout: 8000 }).catch(async () => connect.evaluate((element) => element.click()));
  await page.waitForTimeout(750);
  const send = await clickSendInvitation(page);
  payload.send = { ...send, guard: { action: "send_connection", allowed: allowSend } };
  if (send.status !== "clicked-send") {
    payload.status = statusFromSend(send.status);
    payload.after = { state: send.status };
  } else {
    payload.status = "pending-provisional";
    payload.after = { state: "clicked-send-from-linkedin-profile" };
  }
  await page.keyboard.press("Escape").catch(() => null);
  return payload;
}

async function sendFromSearchRow(page, salesPayload, reason) {
  const searchUrl = salesPayload.searchUrl;
  const payload = {
    ...salesPayload,
    status: "unknown",
    salesNavUrl: salesPayload.url,
    url: searchUrl,
    fallback: { type: "sales-nav-search-row", reason },
  };
  if (!searchUrl) {
    payload.status = "not-connectable:search-row-url-missing";
    return payload;
  }
  await page.goto(searchUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
  await waitForSearchRows(page);
  payload.url = page.url();
  const block = await classifyPage(page);
  if (block.blocked) {
    payload.status = "blocked";
    payload.reason = block.reason;
    return payload;
  }
  const row = await findSearchResultRow(page, salesPayload.candidate.profileUrl);
  if (!row) {
    payload.status = "not-connectable:search-row-not-found";
    return payload;
  }
  const menu = await openSearchRowMenu(page, row);
  payload.fallback.before = menu;
  const connect = await findMenuItem(page, menu.menu_id, "Connect");
  if (!connect) {
    payload.status =
      classifyMenuLabels(menu.labels || []) === "already-pending"
        ? "already-pending"
        : "not-connectable:search-row-missing-connect";
    await page.keyboard.press("Escape").catch(() => null);
    return payload;
  }
  if (dryRun) {
    payload.status = "dry-run-connectable";
    await page.keyboard.press("Escape").catch(() => null);
    return payload;
  }
  if (!allowSend) {
    payload.status = "blocked";
    payload.reason = "real send requires allowSend";
    await page.keyboard.press("Escape").catch(() => null);
    return payload;
  }
  await connect.click({ timeout: 8000 }).catch(async () => connect.evaluate((element) => element.click()));
  await page.waitForTimeout(750);
  const send = await clickSendInvitation(page);
  payload.send = { ...send, guard: { action: "send_connection", allowed: allowSend } };
  if (send.status !== "clicked-send") {
    payload.status = statusFromSend(send.status);
    payload.after = { state: send.status };
  } else {
    await page.waitForTimeout(1500);
    const after = await openSearchRowMenu(page, row);
    payload.after = after;
    payload.status =
      classifyMenuLabels(after.labels || []) === "already-pending"
        ? "pending-provisional"
        : "unverified:clicked-send";
  }
  await page.keyboard.press("Escape").catch(() => null);
  return payload;
}

async function main() {
  if (!dryRun && !allowSend) throw new Error("real send requires allowSend");
  const profileUrl = candidate.profile_url || candidate.profileUrl;
  if (!profileUrl) throw new Error("candidate profile_url is required for browser send");
  const activePage = await getPage();
  await context
    .grantPermissions(["clipboard-read", "clipboard-write"], { origin: "https://www.linkedin.com" })
    .catch(() => null);
  await activePage.goto(profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
  await activePage.waitForTimeout(1500);
  const payload = await sendFromCurrentPage(activePage);
  payload.capturedAt = nowIso();
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`wrote Sales Navigator send result to ${config.out}`);
}

await main();
