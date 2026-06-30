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

async function clickSendInvitation(page) {
  if ((await page.locator("input[type='email'], input[name*='email' i]").first().count().catch(() => 0)) > 0) {
    return { status: "email-required" };
  }
  const buttons = await page.locator(`${LINKEDIN_DIALOG} button`).all();
  let sendButton = null;
  for (const button of buttons) {
    const text = clean(await button.textContent().catch(() => ""));
    const aria = clean(await button.getAttribute("aria-label").catch(() => ""));
    if (/^(Send Invitation|Send invite|Send now|Send)$/i.test(text || aria)) sendButton = button;
  }
  if (!sendButton) return { status: "send-button-missing" };
  if (await sendButton.isDisabled().catch(() => false)) return { status: "send-button-disabled" };
  if (!allowSend) return { status: "blocked", reason: "real send requires allowSend" };
  await sendButton.click({ timeout: 8000 });
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
  const menuState = classifyMenuLabels(menu.labels || []);
  if (menuState === "already-pending") {
    payload.status = "already-pending";
  } else if (menuState !== "connectable") {
    payload.status = `not-connectable:${menuState}`;
  } else if (dryRun) {
    payload.status = "dry-run-connectable";
  } else {
    const connect = await findMenuItem(page, menu.menu_id, "Connect");
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

async function main() {
  if (!dryRun && !allowSend) throw new Error("real send requires allowSend");
  const profileUrl = candidate.profile_url || candidate.profileUrl;
  if (!profileUrl) throw new Error("candidate profile_url is required for browser send");
  const activePage = await getPage();
  await activePage.goto(profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
  await activePage.waitForTimeout(1500);
  const payload = await sendFromCurrentPage(activePage);
  payload.capturedAt = nowIso();
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`wrote Sales Navigator send result to ${config.out}`);
}

await main();
