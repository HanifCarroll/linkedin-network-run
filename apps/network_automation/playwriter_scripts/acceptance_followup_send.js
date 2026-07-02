const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const record = config.record || {};

function nowIso() {
  return new Date().toISOString();
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((candidatePage) => candidatePage.url().includes("linkedin.com/sales")) ||
    pages.find((candidatePage) => candidatePage.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

function basePayload(url) {
  const draft = String(record.draft || "");
  return {
    candidate: {
      id: record.id,
      key: record.key,
      name: record.name,
      profileUrl: record.profile_url || record.profileUrl,
      salesNavProfileUrl: record.sales_nav_profile_url || record.salesNavProfileUrl || null,
      source: record.source,
    },
    dryRun: Boolean(config.dryRun),
    url,
    messageLength: draft.length,
    status: "unknown",
    previewFill: Boolean(config.previewFill),
    checkedAt: nowIso(),
  };
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

async function classifyBlock(page) {
  const url = page.url();
  if (/\/login|\/uas\/login/i.test(url) || await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']")) {
    return { status: "login", reason: "login required" };
  }
  if (/\/checkpoint/i.test(url) || await visibleCount(page, "input[name='pin'], input[name='challengeId']")) {
    return { status: "checkpoint", reason: "checkpoint present" };
  }
  if (await visibleCount(page, "iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']")) {
    return { status: "security", reason: "security verification present" };
  }
  return null;
}

async function actionDetails(item) {
  const text = (
    (await item.textContent().catch(() => "")) ||
    (await item.getAttribute("aria-label").catch(() => "")) ||
    ""
  ).trim();
  const ariaLabel = ((await item.getAttribute("aria-label").catch(() => "")) || "").trim();
  const label = text || ariaLabel;
  if (!label) return null;
  return {
    label,
    ariaLabel,
    disabled: await item.isDisabled().catch(() => false),
    tagName: await item.evaluate((node) => node.tagName.toLowerCase()).catch(() => null),
    role: await item.getAttribute("role").catch(() => null),
  };
}

async function scanVisibleActions(page, pattern) {
  const locator = page.locator("button,a,[role='button']");
  const count = await locator.count().catch(() => 0);
  const visibleActions = [];
  for (let index = 0; index < count; index += 1) {
    const item = locator.nth(index);
    if (!(await item.isVisible().catch(() => false))) continue;
    const details = await actionDetails(item);
    if (!details) continue;
    visibleActions.push(details);
    if (!details.disabled && pattern.test(details.label)) {
      return {
        action: {
          locator: item,
          label: details.label,
          kind: /^InMail\b/i.test(details.label) ? "inmail" : "message",
        },
        visibleActions,
      };
    }
  }
  return { action: null, visibleActions };
}

async function visibleAction(page, pattern) {
  return (await scanVisibleActions(page, pattern)).action;
}

async function visibleComposer(page) {
  for (const selector of [
    "div.msg-form__contenteditable[contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
  ]) {
    const locator = page.locator(selector).last();
    if ((await locator.count().catch(() => 0)) > 0 && await locator.isVisible().catch(() => false)) {
      return { locator, selector };
    }
  }
  return null;
}

async function fillSubjectIfPresent(page) {
  for (const selector of [
    "input[name='subject']",
    "input[placeholder*='Subject' i]",
    "input[aria-label*='Subject' i]",
  ]) {
    const locator = page.locator(selector).last();
    if ((await locator.count().catch(() => 0)) > 0 && await locator.isVisible().catch(() => false)) {
      await locator.fill("", { timeout: 8000 });
      return { filled: true, selector, subject: "" };
    }
  }
  return { filled: false };
}

async function main() {
  const profileUrl = record.profile_url || record.profileUrl;
  if (!profileUrl) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...basePayload(null), status: "blocked", reason: "missing profile_url" }, null, 2)}\n`);
    return;
  }

  const page = await getPage();
  await page.goto(profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
  const payload = basePayload(page.url());

  const block = await classifyBlock(page);
  if (block) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...payload, status: block.status, reason: block.reason }, null, 2)}\n`);
    return;
  }

  const actionScan = await scanVisibleActions(page, /^(Message|InMail)\b/i);
  const action = actionScan.action;
  if (!action) {
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "not-messageable",
      reason: "no visible Message or InMail action",
      visibleActions: actionScan.visibleActions,
    }, null, 2)}\n`);
    return;
  }
  const actionPayload = {
    kind: action.kind,
    action_label: action.label,
    identity_label: record.name,
    source: "profile-actions",
    opened_page_url: page.url(),
  };
  if (config.dryRun && !config.previewFill) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...payload, status: "dry-run-messageable", action: actionPayload }, null, 2)}\n`);
    return;
  }

  await action.locator.click({ timeout: 8000 });
  await page.waitForTimeout(1000);
  const composer = await visibleComposer(page);
  if (!composer) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...payload, status: "composer-missing", action: actionPayload }, null, 2)}\n`);
    return;
  }
  const draft = String(record.draft || "");
  const subjectFill = await fillSubjectIfPresent(page);
  await composer.locator.fill(draft, { timeout: 8000 });
  const actual = await composer.locator.textContent().catch(() => "");
  const bodyFill = {
    matched: actual.replace(/\s+/g, " ").trim() === draft.replace(/\s+/g, " ").trim(),
    selector: composer.selector,
    expectedLength: draft.length,
    actualLength: actual.length,
    lineBreakCount: (draft.match(/\n/g) || []).length,
  };
  const filledPayload = {
    ...payload,
    action: actionPayload,
    composerSelector: composer.selector,
    subjectFill,
    bodyFill,
  };
  if (config.previewFill) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "preview-filled" }, null, 2)}\n`);
    return;
  }

  const send = await visibleAction(page, /^(Send|Send message)$/i);
  if (!send) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "send-button-missing" }, null, 2)}\n`);
    return;
  }
  if (!config.allowSend) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "blocked", reason: "real send requires allowSend" }, null, 2)}\n`);
    return;
  }
  await send.locator.click({ timeout: 8000 });
  await page.waitForTimeout(1000);
  fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "sent-clicked", send: { status: "clicked", action: "send-message" } }, null, 2)}\n`);
}

await main();
