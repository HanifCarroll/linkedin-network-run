// Open the LinkedIn/Sales Navigator message composer for one profile.
// Step 1 of the manual DM pipeline: navigate -> close stale composers ->
// press Message (direct or overflow menu) -> locate composer (same tab or
// new tab) -> report handles in state for later fill/send steps.
//
// Inputs (state):
//   state.profileUrl        - required: sales/lead URL or public /in/ URL
//   state.composerStep      - optional: config path with same fields
//   state.composerPage      - optional: page handle to reuse (else auto-pick)
//   state.sendMode          - "opening" (default) | "followup". Opening mode
//                             hard-blocks when the composer already holds a
//                             conversation; followup mode continues.
//
// Outputs (state):
//   state.composer = {
//     status: "composer-ready" | "existing-conversation" | "no-message-action" |
//             "blocked" | "error",
//     profileUrl, composerLocation: "same-tab" | "new-tab" | null,
//     pageIndex, pageUrl, dialogLabel, reason?
//   }
//   state.composerPage        - page handle holding the composer (for later steps)

const fs = require("node:fs");

function nowIso() {
  return new Date().toISOString();
}

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

// --- selectors (current Sales Navigator UI, verified 2026-08-06) ---
const PROFILE_MESSAGE_SELECTOR = "button[data-anchor-send-inmail]";
const PROFILE_MESSAGE_TEXT_SELECTOR = "button";
const OVERFLOW_TRIGGER_SELECTOR =
  'button[aria-label="Open actions overflow menu"], button[aria-label^="See more actions for"]';
const MENU_CONTAINER_SELECTOR = "[id^='hue-menu-'], [data-popper-placement]";
const COMPOSER_DIALOG_SELECTOR =
  "section[role='dialog'][aria-label^='Conversation with ']";
const COMPOSER_TEXTAREA_SELECTOR =
  "form[data-x-conversation-widget='compose-form'] textarea[name='message'], textarea[name='message']";
const MESSAGE_MENU_ITEM = "Message";

function baseReport(profileUrl) {
  return {
    status: "unknown",
    profileUrl,
    composerLocation: null,
    pageIndex: null,
    pageUrl: null,
    dialogLabel: null,
    reason: null,
    at: nowIso(),
  };
}

function isLinkedInUrl(value) {
  try {
    const parsed = new URL(String(value || ""), "https://www.linkedin.com");
    return ["linkedin.com", "www.linkedin.com"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

function leadPath(value) {
  try {
    const parsed = new URL(String(value || ""), "https://www.linkedin.com");
    const parts = parsed.pathname.split("/").filter(Boolean);
    return parts.length >= 3 && parts[0] === "sales" && parts[1] === "lead"
      ? `/sales/lead/${parts[2].split(",", 1)[0]}`
      : null;
  } catch {
    return null;
  }
}

function publicPath(value) {
  try {
    const parsed = new URL(String(value || ""), "https://www.linkedin.com");
    const parts = parsed.pathname.split("/").filter(Boolean);
    return parts.length >= 2 && parts[0] === "in"
      ? `https://www.linkedin.com/in/${parts.slice(1).join("/")}`
      : null;
  } catch {
    return null;
  }
}

async function pickPage() {
  if (state.composerPage && !state.composerPage.isClosed()) return state.composerPage;
  const pages = context.pages();
  state.composerPage =
    pages.find((p) => p.url().includes("linkedin.com/sales/lead/")) ||
    pages.find((p) => p.url().includes("linkedin.com/sales")) ||
    pages.find((p) => p.url().includes("linkedin.com/in/")) ||
    pages.find((p) => p.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.composerPage;
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
  if (/\/login|\/uas\/login/i.test(url)) return { status: "blocked", reason: "login required" };
  if (/\/checkpoint/i.test(url)) return { status: "blocked", reason: "checkpoint present" };
  if (
    (await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']")) > 0
  ) {
    return { status: "blocked", reason: "login required" };
  }
  if ((await visibleCount(page, "input[name='pin'], input[name='challengeId']")) > 0) {
    return { status: "blocked", reason: "checkpoint present" };
  }
  if (
    (await visibleCount(
      page,
      "iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']",
    )) > 0
  ) {
    return { status: "blocked", reason: "security verification present" };
  }
  return { status: "ok", reason: null };
}

// --- composer close (safety: never stack composers) ---
async function hasOpenComposer(page) {
  // A conversation dialog counts as a composer when it is rendered with real
  // geometry and contains a message textarea. Sales Navigator renders the
  // composer inside an overlay whose parent can report zero height or an
  // offscreen top, so isVisible()/offsetParent are unreliable here — use the
  // dialog's own computed size instead.
  const dialogCount = await page.locator(COMPOSER_DIALOG_SELECTOR).count().catch(() => 0);
  for (let index = 0; index < dialogCount; index += 1) {
    const dialog = page.locator(COMPOSER_DIALOG_SELECTOR).nth(index);
    const textarea = dialog.locator(COMPOSER_TEXTAREA_SELECTOR);
    if (!((await textarea.count().catch(() => 0)) > 0)) continue;
    const rendered = await dialog
      .evaluate((node) => {
        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return (
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          rect.width > 100 &&
          rect.height > 100
        );
      })
      .catch(() => false);
    if (rendered) return true;
  }
  return false;
}

async function countExistingConversation(page) {
  // Count prior outbound/inbound messages in the conversation dialog. A fresh
  // opening-message composer has total === 0.
  return page
    .locator(COMPOSER_DIALOG_SELECTOR)
    .first()
    .evaluate(() => {
      const normalize = (text) => String(text || "").replace(/\s+/g, " ").trim();
      let outbound = 0;
      for (const el of document.querySelectorAll("[aria-label='Message from you']")) {
        const article = el.closest("article");
        if (normalize(article ? article.innerText : el.innerText)) outbound += 1;
      }
      const articles = Array.from(document.querySelectorAll("section[role='dialog'][aria-label^='Conversation with '] article"));
      const inbound = articles.filter((a) => {
        const text = normalize(a.innerText);
        return text.length > 0 && !a.querySelector("[aria-label='Message from you']");
      }).length;
      return { outbound, inbound, total: outbound + inbound };
    })
    .catch(() => ({ outbound: 0, inbound: 0, total: 0 }));
}

async function hardBlockExistingConversation(page, report) {
  // This pipeline sends OPENING messages. An existing thread means a prior
  // conversation — sending the opening pitch would double-send or reply to a
  // thread we did not intend to touch. Close the composer and block, unless
  // the caller explicitly declares a follow-up send.
  report.status = "existing-conversation";
  report.reason =
    "composer already holds a prior conversation; this pipeline sends opening messages only — " +
    "set state.sendMode='followup' to override";
  await page.keyboard.press("Escape").catch(() => null);
  await page.waitForTimeout(600);
  report.composerClosed = true;
  return report;
}

async function closeComposerInPage(page) {
  if (!(await hasOpenComposer(page))) return { closed: false, reason: "none-open" };
  // Prefer the dialog's own close control, then Escape.
  const closeBtn = page
    .locator(
      "button[aria-label*='Close'], button[data-control-name='overlay.close'], .artdeco-dismiss, button[aria-label^='Dismiss']",
    )
    .first();
  if ((await closeBtn.count().catch(() => 0)) > 0) {
    await closeBtn.click({ timeout: 3000 }).catch(() => null);
  } else {
    await page.keyboard.press("Escape").catch(() => null);
  }
  await page.waitForTimeout(600);
  const stillOpen = await hasOpenComposer(page);
  if (stillOpen) {
    await page.keyboard.press("Escape").catch(() => null);
    await page.waitForTimeout(600);
  }
  return { closed: true, reason: stillOpen ? "escape-fallback" : "closed" };
}

async function closeAllComposers() {
  const pages = context.pages();
  const closedOn = [];
  for (const p of pages) {
    if (p.isClosed()) continue;
    try {
      const result = await closeComposerInPage(p);
      if (result.closed) closedOn.push({ url: p.url().slice(0, 120), reason: result.reason });
    } catch (error) {
      closedOn.push({ url: p.url().slice(0, 120), reason: `error: ${error.message.slice(0, 80)}` });
    }
  }
  return closedOn;
}

// --- message button discovery ---
async function clickAction(locator, timeout) {
  try {
    await locator.click({ timeout });
    return "locator";
  } catch {
    try {
      await locator.click({ timeout: Math.min(timeout, 3000), force: true });
      return "force";
    } catch {
      await locator.evaluate((element) => element.click());
      return "dom";
    }
  }
}

const PROFILE_MESSAGE_LINK_SELECTOR = 'a[href*="/messaging/compose/"]';

async function tryDirectMessageButton(page) {
  const direct = page.locator(PROFILE_MESSAGE_SELECTOR).first();
  if ((await direct.count().catch(() => 0)) > 0) {
    await clickAction(direct, 8000);
    return "direct-inmail";
  }
  // Some profiles render a plain text "Message" button without the anchor.
  const textButtons = page.locator(PROFILE_MESSAGE_TEXT_SELECTOR);
  const count = await textButtons.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    const item = textButtons.nth(index);
    const text = clean(await item.textContent().catch(() => ""));
    if (text === "Message" && (await item.isVisible().catch(() => false))) {
      await clickAction(item, 8000);
      return "direct-text";
    }
  }
  return null;
}

async function tryMessageLink(page) {
  // 1st-degree connections render Message as an <a href="/messaging/compose/…">
  // link. The element can be overlapped by sticky profile controls, so click it
  // through the DOM; then detect the composer on the messaging page.
  const link = page.locator(PROFILE_MESSAGE_LINK_SELECTOR).filter({ hasText: /^Message$/i }).first();
  if (!((await link.count().catch(() => 0)) > 0)) return null;
  await page.waitForTimeout(300);
  await link.evaluate((element) => element.click()).catch(() => null);
  return "message-link";
}

async function tryOverflowMessageItem(page) {
  const trigger = page.locator(OVERFLOW_TRIGGER_SELECTOR).first();
  if (!((await trigger.count().catch(() => 0)) > 0)) return null;
  const menuId = await trigger.getAttribute("aria-controls").catch(() => null);
  await clickAction(trigger, 8000);
  // Poll for the exact popup (by aria-controls id) and its items; some UIs
  // hydrate menu items lazily after the popup appears.
  const menuSelector = menuId ? `#${menuId}` : "[data-popper-placement]";
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await page.waitForTimeout(400);
    const menu = page.locator(menuSelector).first();
    if (!((await menu.count().catch(() => 0)) > 0)) continue;
    const items = await menu.locator("button,a,[role=menuitem]").all();
    for (const item of items) {
      const text = clean(await item.textContent().catch(() => ""));
      const aria = clean(await item.getAttribute("aria-label").catch(() => ""));
      if (text === MESSAGE_MENU_ITEM || aria === MESSAGE_MENU_ITEM) {
        await clickAction(item, 8000);
        return "overflow-menu";
      }
    }
  }
  return null;
}

async function waitForComposerVisible(page, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await hasOpenComposer(page)) return true;
    await page.waitForTimeout(250);
  }
  return hasOpenComposer(page);
}

// --- main ---
(async () => {
// --- main ---
const profileUrlRaw =
  state.profileUrl ||
  (state.composerStep && state.composerStep.profileUrl) ||
  (state.composerStep && state.composerStep.profile_url) ||
  null;

if (!profileUrlRaw || !isLinkedInUrl(profileUrlRaw)) {
  state.composer = {
    ...baseReport(profileUrlRaw || null),
    status: "error",
    reason: "profileUrl missing or not a linkedin.com URL",
  };
  console.log(JSON.stringify(state.composer));
} else {
  const report = baseReport(profileUrlRaw);
  const targetPath = leadPath(profileUrlRaw) || publicPath(profileUrlRaw);

  try {
    const closedComposers = await closeAllComposers();
    report.closedComposers = closedComposers;

    const workPage = await pickPage();
    state.composerPage = workPage;
    const gotoUrl = targetPath.startsWith("http") ? targetPath : `https://www.linkedin.com${targetPath}`;
    await workPage.goto(gotoUrl, {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    await workPage.waitForTimeout(3000);

    const block = await classifyBlock(workPage);
    if (block.status !== "ok") {
      report.status = block.status;
      report.reason = block.reason;
    } else {
      const pagesBefore = context.pages().length;
      const beforeUrl = workPage.url();

      let clickSource = await tryDirectMessageButton(workPage);
      if (!clickSource) {
        clickSource = await tryMessageLink(workPage);
      }
      if (!clickSource) {
        clickSource = await tryOverflowMessageItem(workPage);
      }

      if (!clickSource) {
        report.status = "no-message-action";
        report.reason = "no Message button found (direct or overflow menu)";
      } else {
        await workPage.waitForTimeout(2500);
        const pagesAfter = context.pages().length;

        // Case A: composer opened in the same tab.
        if (await waitForComposerVisible(workPage, 6000)) {
          report.status = "composer-ready";
          report.composerLocation = "same-tab";
          report.pageIndex = context.pages().indexOf(workPage);
          report.pageUrl = workPage.url();
          report.clickSource = clickSource;
          const label = await workPage
            .locator(COMPOSER_DIALOG_SELECTOR)
            .first()
            .getAttribute("aria-label")
            .catch(() => null);
          report.dialogLabel = label || null;
          // Opening-message guard: block at open time when the composer already
          // holds a conversation, unless the caller declared a follow-up.
          report.existingConversation = await countExistingConversation(workPage);
          const sendMode = state.sendMode || (state.composerStep && state.composerStep.sendMode) || "opening";
          if (report.existingConversation.total > 0 && sendMode !== "followup") {
            await hardBlockExistingConversation(workPage, report);
            state.composer = report;
            console.log(JSON.stringify(report));
            return;
          }
          if (report.existingConversation.total > 0) {
            report.followup = true;
            report.reason = "existing conversation detected; continuing in followup mode";
          }
        } else if (pagesAfter > pagesBefore) {
          // Case B: a new tab appeared with the composer.
          const fresh = context.pages().find((p) => !p.isClosed() && p !== workPage);
          if (fresh) {
            await fresh.waitForTimeout(2500);
            if (await waitForComposerVisible(fresh, 6000)) {
              report.status = "composer-ready";
              report.composerLocation = "new-tab";
              report.pageIndex = context.pages().indexOf(fresh);
              report.pageUrl = fresh.url();
              report.clickSource = clickSource;
              const label = await fresh
                .locator(COMPOSER_DIALOG_SELECTOR)
                .first()
                .getAttribute("aria-label")
                .catch(() => null);
              report.dialogLabel = label || null;
              // Opening-message guard (new-tab variant): same block as Case A.
              report.existingConversation = await countExistingConversation(fresh);
              const sendMode = state.sendMode || (state.composerStep && state.composerStep.sendMode) || "opening";
              if (report.existingConversation.total > 0 && sendMode !== "followup") {
                await hardBlockExistingConversation(fresh, report);
                state.composer = report;
                console.log(JSON.stringify(report));
                return;
              }
              if (report.existingConversation.total > 0) {
                report.followup = true;
                report.reason = "existing conversation detected; continuing in followup mode";
              }
              state.composerPage = fresh;
            } else {
              report.status = "error";
              report.reason = "new tab opened but composer not found inside it";
            }
          } else {
            report.status = "error";
            report.reason = "page count grew but no usable new tab found";
          }
        } else {
          report.status = "error";
          report.reason = "Message clicked but no composer appeared and no new tab opened";
          report.afterUrl = workPage.url();
          report.beforeUrl = beforeUrl;
        }
      }
    }
  } catch (error) {
    report.status = "error";
    report.reason = `${error.name || "Error"}: ${error.message}`.slice(0, 300);
  }

  state.composer = report;
  console.log(JSON.stringify(report));
}

})();
