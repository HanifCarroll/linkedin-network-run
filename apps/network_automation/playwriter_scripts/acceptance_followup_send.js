const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const record = config.record || {};

function nowIso() {
  return new Date().toISOString();
}

function progress(step, extra = {}) {
  const out = config.progressOut || (config.out ? `${config.out}.progress.jsonl` : null);
  if (!out) return;
  fs.appendFileSync(
    out,
    `${JSON.stringify({ at: nowIso(), step, ...extra })}\n`,
  );
}

function normalizeMessage(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
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

async function gotoProfilePage(page, profileUrl) {
  try {
    await page.goto(profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
    return page;
  } catch (error) {
    if (!/(Frame has been detached|page has been closed|Target page, context or browser has been closed)/i.test(error.message || "")) {
      throw error;
    }
    const freshPage = await context.newPage();
    state.linkedinToolsPage = freshPage;
    await freshPage.goto(profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
    return freshPage;
  }
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
    href: await item.getAttribute("href").catch(() => null),
    rect: await item.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      return {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      };
    }).catch(() => null),
  };
}

async function hitTestAction(item) {
  return item.evaluate((node) => {
    const elementFromPointIgnoringAutomationOverlay = (x, y) => {
      const hidden = [];
      let hit = document.elementFromPoint(x, y);
      const seen = new Set();
      while (hit && hit.id === "interop-outlet" && !seen.has(hit) && hidden.length < 5) {
        seen.add(hit);
        hidden.push([hit, hit.style.pointerEvents]);
        hit.style.pointerEvents = "none";
        hit = document.elementFromPoint(x, y);
      }
      for (const [element, pointerEvents] of hidden.reverse()) {
        element.style.pointerEvents = pointerEvents;
      }
      return hit;
    };
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      return { hittable: false, reason: "zero-size" };
    }
    const points = [
      { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 },
      { x: rect.left + Math.min(rect.width - 1, 12), y: rect.top + rect.height / 2 },
      { x: rect.right - Math.min(rect.width - 1, 12), y: rect.top + rect.height / 2 },
    ];
    for (const point of points) {
      const hit = elementFromPointIgnoringAutomationOverlay(point.x, point.y);
      if (!hit) continue;
      const interactive = hit.closest("a,button,[role='button']");
      if (hit === node || node.contains(hit) || interactive === node) {
        return { hittable: true };
      }
    }
    const center = points[0];
    const hit = elementFromPointIgnoringAutomationOverlay(center.x, center.y);
    if (hit && hit.id === "interop-outlet") {
      return { hittable: true, reason: "automation-overlay", blockerId: "interop-outlet" };
    }
    const interactive = hit ? hit.closest("a,button,[role='button']") : null;
    const label = interactive
      ? (interactive.getAttribute("aria-label") || interactive.textContent || "")
          .trim()
          .replace(/\s+/g, " ")
      : null;
    return {
      hittable: false,
      reason: "covered",
      blockerTag: hit ? hit.tagName.toLowerCase() : null,
      blockerId: hit ? hit.id || null : null,
      blockerLabel: label,
    };
  }).catch((error) => ({ hittable: false, reason: `hit-test-failed: ${error.message}` }));
}

async function scanVisibleActionsFromLocator(locator, pattern) {
  const count = await locator.count().catch(() => 0);
  const visibleActions = [];
  const candidates = [];
  for (let index = 0; index < count; index += 1) {
    const item = locator.nth(index);
    if (!(await item.isVisible().catch(() => false))) continue;
    const details = await actionDetails(item);
    if (!details) continue;
    const hitTest = await hitTestAction(item);
    details.hittable = hitTest.hittable;
    if (!hitTest.hittable) {
      details.blockedBy = hitTest;
    }
    visibleActions.push(details);
    if (!details.disabled && details.hittable && pattern.test(details.label)) {
      candidates.push({
        item,
        details,
        hitTest,
        score: actionCandidateScore(details),
      });
    }
  }
  candidates.sort((left, right) => right.score - left.score);
  const selected = candidates[0];
  if (selected) {
    return {
      action: {
        locator: selected.item,
        label: selected.details.label,
        kind: /^InMail\b/i.test(selected.details.label) ? "inmail" : "message",
        hitTest: selected.hitTest,
        details: selected.details,
      },
      visibleActions,
    };
  }
  return { action: null, visibleActions };
}

function actionCandidateScore(details) {
  let score = 0;
  const rect = details.rect || {};
  if (rect.y > 80) score += 100;
  if (details.href && details.href.includes("/messaging/compose")) score += 20;
  if (/^(Message|InMail)\b/i.test(details.label) && rect.y < 80) score -= 500;
  if (/^(Send|Send message)$/i.test(details.label)) score += 200;
  if (/\b(close|dismiss|discard)\b/i.test(details.label)) score += 200;
  return score;
}

async function clickAction(action, timeout = 8000) {
  if (
    (action.hitTest && action.hitTest.reason === "automation-overlay") ||
    (action.details && action.details.href && action.details.href.includes("/messaging/compose"))
  ) {
    await action.locator.evaluate((node) => node.click());
    return {
      method: "dom-click",
      reason: action.hitTest && action.hitTest.reason === "automation-overlay"
        ? "automation-overlay"
        : "compose-link",
    };
  }
  await action.locator.click({ timeout });
  return { method: "locator-click" };
}

async function scanVisibleActions(page, pattern) {
  return scanVisibleActionsFromLocator(page.locator("button,a,[role='button']"), pattern);
}

async function waitForVisibleActionInRoot(page, root, pattern, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  let scan = null;
  while (Date.now() < deadline) {
    scan = await scanVisibleActionsFromLocator(root.locator("button,a,[role='button']"), pattern);
    if (scan.action) return scan;
    await page.waitForTimeout(250);
  }
  return scan || await scanVisibleActionsFromLocator(root.locator("button,a,[role='button']"), pattern);
}

async function visibleComposerForRecipient(page, name) {
  for (const selector of [
    "div.msg-form__contenteditable[contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
  ]) {
    const locator = page.locator(selector);
    const count = await locator.count().catch(() => 0);
    for (let index = count - 1; index >= 0; index -= 1) {
      const item = locator.nth(index);
      if (!(await item.isVisible().catch(() => false))) continue;
      const composer = { locator: item, selector };
      if (!await composerRoot(page, composer)) continue;
      const stateForComposer = await composerState(composer);
      if (recipientMatches(stateForComposer, name)) {
        return { composer, state: stateForComposer };
      }
    }
  }
  return null;
}

async function waitForComposerForRecipient(page, name, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  let match = null;
  while (Date.now() < deadline) {
    match = await visibleComposerForRecipient(page, name);
    if (match) return match;
    await page.waitForTimeout(250);
  }
  return match || await visibleComposerForRecipient(page, name);
}

async function composerRoot(page, composer) {
  for (const selector of [
    "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' msg-overlay-conversation-bubble ')][1]",
    "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' msg-convo-wrapper ')][1]",
    "xpath=ancestor-or-self::*[@role='dialog'][1]",
    "xpath=ancestor-or-self::form[contains(concat(' ', normalize-space(@class), ' '), ' msg-form ')][1]",
  ]) {
    const root = composer.locator.locator(selector);
    if ((await root.count().catch(() => 0)) > 0 && await root.isVisible().catch(() => false)) {
      return root;
    }
  }
  return null;
}

async function composerState(composer) {
  return composer.locator.evaluate((node) => {
    let root = null;
    for (const selector of [
      ".msg-overlay-conversation-bubble",
      ".msg-convo-wrapper",
      "[role='dialog']",
    ]) {
      root = node.closest(selector);
      if (root) break;
    }
    root = root || node.closest(".msg-form") || node.parentElement || node;
    const recipientSelectors = [
      ".msg-connections-typeahead__top-fixed-section",
      ".msg-connections-typeahead__added-recipients",
      ".msg-connections-typeahead-container",
      ".msg-overlay-bubble-header__title",
      ".msg-thread__link-to-profile",
      ".msg-entity-lockup__entity-title",
    ];
    const recipients = [];
    for (const selector of recipientSelectors) {
      for (const recipientNode of root.querySelectorAll(selector)) {
        const text = (recipientNode.innerText || recipientNode.textContent || "")
          .trim()
          .replace(/\s+/g, " ");
        if (text) recipients.push(text);
      }
    }
    return {
      bodyText: node.innerText || node.textContent || "",
      recipients,
    };
  }).catch(() => ({ bodyText: "", recipients: [] }));
}

async function messageBubbleState(root) {
  return root.evaluate((node) => {
    const recipientSelectors = [
      ".msg-connections-typeahead__top-fixed-section",
      ".msg-connections-typeahead__added-recipients",
      ".msg-connections-typeahead-container",
      ".msg-overlay-bubble-header__title",
      ".msg-thread__link-to-profile",
      ".msg-entity-lockup__entity-title",
    ];
    const recipients = [];
    for (const selector of recipientSelectors) {
      for (const recipientNode of node.querySelectorAll(selector)) {
        const text = (recipientNode.innerText || recipientNode.textContent || "")
          .trim()
          .replace(/\s+/g, " ");
        if (text) recipients.push(text);
      }
    }
    return {
      bodyText: "",
      recipients,
    };
  }).catch(() => ({ bodyText: "", recipients: [] }));
}

function recipientMatches(state, name) {
  const normalizedName = normalizeMessage(name);
  if (!normalizedName) return false;
  return (state.recipients || []).some((recipient) => normalizeMessage(recipient).includes(normalizedName));
}

function bodyFillResult(composer, draft, bodyText) {
  return {
    matched: normalizeMessage(bodyText) === normalizeMessage(draft),
    selector: composer.selector,
    expectedLength: draft.length,
    actualLength: String(bodyText || "").length,
    lineBreakCount: (draft.match(/\n/g) || []).length,
    source: "innerText",
  };
}

async function closeOtherConversationBubbles(page, name) {
  const result = await page.evaluate((targetName) => {
    const normalize = (text) => String(text || "").replace(/\s+/g, " ").trim();
    const visibleElement = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const labelFor = (element) => (
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      element.innerText ||
      element.textContent ||
      ""
    ).trim().replace(/\s+/g, " ");
    const rootSelector = ".msg-overlay-conversation-bubble,.msg-convo-wrapper";
    const roots = new Set(Array.from(document.querySelectorAll(rootSelector)).filter(visibleElement));
    for (const button of document.querySelectorAll("button,a,[role='button']")) {
      const label = labelFor(button);
      if (!/\b(close|dismiss|discard)\b/i.test(label)) continue;
      const root = button.closest(rootSelector);
      if (root && visibleElement(root)) roots.add(root);
    }
    const target = normalize(targetName);
    const closed = [];
    const kept = [];
    const skipped = [];
    let index = 0;
    for (const root of roots) {
      const text = normalize(root.innerText || root.textContent || "");
      const buttons = Array.from(root.querySelectorAll("button,a,[role='button']"))
        .filter(visibleElement)
        .map((button, buttonIndex) => {
          const rect = button.getBoundingClientRect();
          return {
            button,
            index: buttonIndex,
            label: labelFor(button),
            rect,
          };
        });
      if (target && text.includes(target)) {
        kept.push({ index, text: text.slice(0, 180) });
        index += 1;
        continue;
      }
      const close = buttons.find((candidate) => /\b(close|dismiss|discard)\b/i.test(candidate.label));
      if (!close) {
        skipped.push({
          index,
          text: text.slice(0, 180),
          reason: "close action missing",
          visibleActions: buttons.map((candidate) => ({
            index: candidate.index,
            label: candidate.label,
            rect: {
              x: Math.round(candidate.rect.x),
              y: Math.round(candidate.rect.y),
              w: Math.round(candidate.rect.width),
              h: Math.round(candidate.rect.height),
            },
          })),
        });
        index += 1;
        continue;
      }
      close.button.click();
      closed.push({ index, text: text.slice(0, 180), close: { label: close.label, method: "dom-click-close" } });
      index += 1;
    }
    return { closed, kept, skipped };
  }, name).catch((error) => ({
    closed: [],
    kept: [],
    skipped: [{ reason: `message cleanup failed: ${error.message}` }],
  }));
  if (result.closed.length > 0) {
    await page.waitForTimeout(300);
  }
  return result;
}

async function messageContainerDiagnostics(page, name) {
  return page.evaluate((targetName) => {
    const normalize = (text) => String(text || "").replace(/\s+/g, " ").trim();
    const visibleElement = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const labelFor = (element) => normalize(
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      element.innerText ||
      element.textContent ||
      "",
    );
    const describeRect = (element) => {
      const rect = element.getBoundingClientRect();
      return {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      };
    };
    const rootFor = (element) => (
      element.closest(".msg-overlay-conversation-bubble,.msg-convo-wrapper") ||
      element.closest(".msg-overlay-conversation-bubble__content-wrapper")?.parentElement ||
      element.closest("form.msg-form") ||
      element.closest("[role='dialog']")
    );
    const rootSelector = ".msg-overlay-conversation-bubble,.msg-convo-wrapper";
    const roots = new Set(Array.from(document.querySelectorAll(rootSelector)).filter(visibleElement));
    for (const wrapper of document.querySelectorAll(".msg-overlay-conversation-bubble__content-wrapper")) {
      const root = wrapper.closest(rootSelector) || wrapper.parentElement;
      if (root && visibleElement(root)) roots.add(root);
    }
    for (const composer of document.querySelectorAll("div.msg-form__contenteditable[contenteditable='true'],[contenteditable='true'][role='textbox']")) {
      const root = rootFor(composer);
      if (root && visibleElement(root)) roots.add(root);
    }
    const recipientSelectors = [
      ".msg-connections-typeahead__top-fixed-section",
      ".msg-connections-typeahead__added-recipients",
      ".msg-connections-typeahead-container",
      ".msg-overlay-bubble-header__title",
      ".msg-thread__link-to-profile",
      ".msg-entity-lockup__entity-title",
    ];
    const describeRoot = (root, index) => {
      const text = normalize(root.innerText || root.textContent || "");
      const recipients = [];
      for (const selector of recipientSelectors) {
        for (const recipientNode of root.querySelectorAll(selector)) {
          const recipient = normalize(recipientNode.innerText || recipientNode.textContent || "");
          if (recipient) recipients.push(recipient);
        }
      }
      const composers = Array.from(root.querySelectorAll("div.msg-form__contenteditable[contenteditable='true'],[contenteditable='true'][role='textbox']"))
        .filter(visibleElement)
        .map((composer, composerIndex) => ({
          index: composerIndex,
          ariaLabel: composer.getAttribute("aria-label") || "",
          textLength: String(composer.innerText || composer.textContent || "").length,
          rect: describeRect(composer),
        }));
      const actions = Array.from(root.querySelectorAll("button,a,[role='button']"))
        .filter(visibleElement)
        .map((action, actionIndex) => ({
          index: actionIndex,
          label: labelFor(action),
          disabled: Boolean(action.disabled) || action.getAttribute("aria-disabled") === "true",
          rect: describeRect(action),
        }))
        .filter((action) => /^(Send|Send message)$|close|dismiss|discard/i.test(action.label));
      return {
        index,
        className: String(root.className || ""),
        role: root.getAttribute("role") || null,
        ariaLabel: root.getAttribute("aria-label") || null,
        rect: describeRect(root),
        textPreview: text.slice(0, 240),
        hasTargetName: normalize(targetName) !== "" && text.includes(normalize(targetName)),
        recipients,
        composers,
        actions,
      };
    };
    return {
      targetName,
      containers: Array.from(roots).map(describeRoot),
      composerCount: Array.from(document.querySelectorAll("div.msg-form__contenteditable[contenteditable='true'],[contenteditable='true'][role='textbox']")).filter(visibleElement).length,
    };
  }, name).catch((error) => ({
    targetName: name,
    error: `message diagnostics failed: ${error.message}`,
  }));
}

async function visibleConversationHistory(root, stateForRoot, name) {
  const matchedRecipient = recipientMatches(stateForRoot, name);
  if (!matchedRecipient) {
    return {
      exists: false,
      scoped: true,
      matchedRecipient,
      reason: "active message container did not match target recipient",
      recipients: stateForRoot.recipients,
    };
  }
  return root.evaluate((node) => {
    const selectors = [
      ".msg-s-message-list__event",
      ".msg-s-event-listitem",
      ".msg-s-message-group",
      "[data-view-name='message-list'] li",
      ".msg-s-message-list-content li",
    ];
    const visibleElement = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    for (const selector of selectors) {
      let visible = 0;
      for (const item of node.querySelectorAll(selector)) {
        if (visibleElement(item)) visible += 1;
      }
      if (visible > 0) {
        return { exists: true, scoped: true, matchedRecipient: true, selector, visibleCount: visible };
      }
    }
    return { exists: false, scoped: true, matchedRecipient: true, visibleCount: 0 };
  }).catch(() => ({
    exists: false,
    scoped: true,
    matchedRecipient,
    reason: "failed to inspect active message container",
    recipients: stateForRoot.recipients,
  }));
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
  progress("start", { id: record.id, name: record.name });
  const profileUrl = record.profile_url || record.profileUrl;
  if (!profileUrl) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...basePayload(null), status: "blocked", reason: "missing profile_url" }, null, 2)}\n`);
    return;
  }

  let page = await getPage();
  progress("page-selected", { url: page.url() });
  page = await gotoProfilePage(page, profileUrl);
  progress("profile-loaded", { url: page.url() });
  await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
  progress("page-load-wait-complete", { url: page.url() });
  const payload = basePayload(page.url());

  const block = await classifyBlock(page);
  if (block) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...payload, status: block.status, reason: block.reason }, null, 2)}\n`);
    return;
  }

  const conversationCleanup = {
    beforeOpen: await closeOtherConversationBubbles(page, record.name),
  };
  progress("conversation-cleanup-complete", conversationCleanup.beforeOpen);
  if (conversationCleanup.beforeOpen.skipped.length > 0) {
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "blocked",
      reason: "could not close all non-target message containers",
      conversationCleanup,
    }, null, 2)}\n`);
    return;
  }
  const draft = String(record.draft || "");
  const existingTargetComposer = await visibleComposerForRecipient(page, record.name);
  if (existingTargetComposer) {
    progress("existing-target-composer-found");
    const existingComposer = existingTargetComposer.composer;
    const existingState = existingTargetComposer.state;
    const existingRoot = await composerRoot(page, existingComposer);
    const bodyFill = bodyFillResult(existingComposer, draft, existingState.bodyText);
    const matchedRecipient = recipientMatches(existingState, record.name);
    const actionPayload = {
      kind: "message",
      action_label: "Message",
      identity_label: record.name,
      source: "existing-compose",
      opened_page_url: page.url(),
    };
    if (existingRoot) {
      const conversationCheck = await visibleConversationHistory(existingRoot, existingState, record.name);
      progress("existing-composer-conversation-check-complete", conversationCheck);
      if (conversationCheck.exists) {
        fs.writeFileSync(config.out, `${JSON.stringify({
          ...payload,
          status: "conversation-exists",
          reason: "existing LinkedIn conversation history is visible",
          action: actionPayload,
          conversationCheck,
          conversationCleanup,
        }, null, 2)}\n`);
        return;
      }
      if (config.dryRun && !config.previewFill) {
        fs.writeFileSync(config.out, `${JSON.stringify({
          ...payload,
          status: "dry-run-messageable",
          action: actionPayload,
          composerSelector: existingComposer.selector,
          conversationCheck,
          conversationCleanup,
          existingComposer: {
            matchedRecipient,
            recipients: existingState.recipients,
          },
        }, null, 2)}\n`);
        return;
      }
      const hasUnexpectedText = normalizeMessage(existingState.bodyText) !== "" && !bodyFill.matched;
      if (hasUnexpectedText) {
        fs.writeFileSync(config.out, `${JSON.stringify({
          ...payload,
          status: "blocked",
          reason: "target message composer has unexpected existing text",
          action: actionPayload,
          composerSelector: existingComposer.selector,
          bodyFill,
          conversationCleanup,
          existingComposer: {
            matchedRecipient,
            recipients: existingState.recipients,
          },
        }, null, 2)}\n`);
        return;
      }
      let finalBodyFill = bodyFill;
      let subjectFill = { filled: false };
      if (!bodyFill.matched) {
        subjectFill = await fillSubjectIfPresent(page);
        progress("existing-composer-subject-fill-complete", subjectFill);
        await existingComposer.locator.fill(draft, { timeout: 8000 });
        progress("existing-composer-body-fill-command-complete");
        const actual = await existingComposer.locator.evaluate((node) => node.innerText || node.textContent || "").catch(() => "");
        finalBodyFill = bodyFillResult(existingComposer, draft, actual);
        progress("existing-composer-body-fill-verified", finalBodyFill);
      }
      const sendScan = await waitForVisibleActionInRoot(page, existingRoot, /^(Send|Send message)$/i, 8000);
      progress("existing-composer-send-scan-complete", { found: Boolean(sendScan.action) });
      const send = sendScan.action;
      const filledPayload = {
        ...payload,
        action: actionPayload,
        composerSelector: existingComposer.selector,
        subjectFill,
        bodyFill: finalBodyFill,
        conversationCleanup,
        existingComposer: {
          matchedRecipient,
          recipients: existingState.recipients,
        },
      };
      if (!send) {
        fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "send-button-missing", sendButtons: sendScan.visibleActions }, null, 2)}\n`);
        return;
      }
      if (!config.allowSend) {
        fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "blocked", reason: "real send requires allowSend" }, null, 2)}\n`);
        return;
      }
      const sendClick = await clickAction(send);
      await page.waitForTimeout(1000);
      progress("existing-composer-send-clicked", sendClick);
      fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "sent-clicked", send: { status: "clicked", action: "send-message", ...sendClick } }, null, 2)}\n`);
      return;
    }
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "blocked",
      reason: "message composer was already open before the scripted Message action",
      composerSelector: existingComposer.selector,
      conversationCleanup,
      existingComposer: {
        matchedRecipient,
        recipients: existingState.recipients,
      },
      bodyFill,
    }, null, 2)}\n`);
    return;
  }

  const actionScan = await scanVisibleActions(page, /^(Message|InMail)\b/i);
  progress("profile-action-scan-complete", { found: Boolean(actionScan.action) });
  const action = actionScan.action;
  if (!action) {
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "not-messageable",
      reason: "no hittable Message or InMail action",
      visibleActions: actionScan.visibleActions,
      conversationCleanup,
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

  const actionClick = await clickAction(action);
  await page.waitForTimeout(1000);
  progress("profile-action-clicked", { label: action.label, ...actionClick });
  let targetComposer = await waitForComposerForRecipient(page, record.name, 8000);
  progress("target-composer-wait-complete", { found: Boolean(targetComposer) });
  if (!targetComposer) {
    const messageContainers = await messageContainerDiagnostics(page, record.name);
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "composer-missing",
      action: actionPayload,
      conversationCleanup,
      messageContainers,
    }, null, 2)}\n`);
    return;
  }
  conversationCleanup.afterOpen = await closeOtherConversationBubbles(page, record.name);
  progress("post-open-conversation-cleanup-complete", conversationCleanup.afterOpen);
  if (conversationCleanup.afterOpen.skipped.length > 0) {
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "blocked",
      reason: "could not close all non-target message containers after opening target composer",
      action: actionPayload,
      conversationCleanup,
    }, null, 2)}\n`);
    return;
  }
  targetComposer = await visibleComposerForRecipient(page, record.name) || targetComposer;
  const composer = targetComposer.composer;
  const root = await composerRoot(page, composer);
  if (!root) {
    const messageContainers = await messageContainerDiagnostics(page, record.name);
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "composer-missing",
      reason: "active message container missing",
      action: actionPayload,
      composerSelector: composer.selector,
      conversationCleanup,
      messageContainers,
    }, null, 2)}\n`);
    return;
  }
  const stateForRoot = targetComposer.state;
  const conversationCheck = await visibleConversationHistory(root, stateForRoot, record.name);
  progress("conversation-check-complete", conversationCheck);
  if (conversationCheck.exists) {
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "conversation-exists",
      reason: "existing LinkedIn conversation history is visible",
      action: actionPayload,
      conversationCheck,
      conversationCleanup,
    }, null, 2)}\n`);
    return;
  }

  if (config.dryRun && !config.previewFill) {
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "dry-run-messageable",
      action: actionPayload,
      composerSelector: composer.selector,
      conversationCheck,
      conversationCleanup,
    }, null, 2)}\n`);
    return;
  }
  const subjectFill = await fillSubjectIfPresent(page);
  progress("subject-fill-complete", subjectFill);
  await composer.locator.fill(draft, { timeout: 8000 });
  progress("body-fill-command-complete");
  const actual = await composer.locator.evaluate((node) => node.innerText || node.textContent || "").catch(() => "");
  const bodyFill = bodyFillResult(composer, draft, actual);
  progress("body-fill-verified", bodyFill);
  const filledPayload = {
    ...payload,
    action: actionPayload,
    composerSelector: composer.selector,
    conversationCleanup,
    subjectFill,
    bodyFill,
  };
  if (config.previewFill) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "preview-filled" }, null, 2)}\n`);
    return;
  }

  const sendScan = await waitForVisibleActionInRoot(page, root, /^(Send|Send message)$/i, 8000);
  progress("send-scan-complete", { found: Boolean(sendScan.action) });
  const send = sendScan.action;
  if (!send) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "send-button-missing", sendButtons: sendScan.visibleActions }, null, 2)}\n`);
    return;
  }
  if (!config.allowSend) {
    fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "blocked", reason: "real send requires allowSend" }, null, 2)}\n`);
    return;
  }
  const sendClick = await clickAction(send);
  await page.waitForTimeout(1000);
  progress("send-clicked", sendClick);
  fs.writeFileSync(config.out, `${JSON.stringify({ ...filledPayload, status: "sent-clicked", send: { status: "clicked", action: "send-message", ...sendClick } }, null, 2)}\n`);
}

await main();
