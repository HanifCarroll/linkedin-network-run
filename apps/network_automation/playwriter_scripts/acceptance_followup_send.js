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

const SALES_NAV_PROFILE_MESSAGE_SELECTOR = "button[data-anchor-send-inmail]";
const SALES_NAV_COMPOSER_SELECTOR = "textarea[name='message'][aria-label='Type your message here…']";

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
  const label = ariaLabel || text;
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
      });
    }
  }
  if (candidates.length === 1) {
    const selected = candidates[0];
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
  return {
    action: null,
    visibleActions,
    exactMatchCount: candidates.length,
    reason: candidates.length > 1 ? "multiple exact actions were visible" : "exact action missing",
  };
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

async function waitForProfileMessageAction(page, name, timeoutMs = 15000) {
  const expectedAriaLabel = `Message ${normalizeMessage(name)}`;
  const deadline = Date.now() + timeoutMs;
  const startedAt = Date.now();
  let visibleActions = [];
  while (Date.now() < deadline) {
    const locator = page.locator(SALES_NAV_PROFILE_MESSAGE_SELECTOR);
    const count = await locator.count().catch(() => 0);
    visibleActions = [];
    const exactActions = [];
    for (let index = 0; index < count; index += 1) {
      const item = locator.nth(index);
      if (!(await item.isVisible().catch(() => false))) continue;
      const details = await actionDetails(item);
      if (!details) continue;
      const hitTest = await hitTestAction(item);
      details.hittable = hitTest.hittable;
      if (!hitTest.hittable) details.blockedBy = hitTest;
      visibleActions.push(details);
      if (
        !details.disabled &&
        details.hittable &&
        normalizeMessage(details.ariaLabel) === expectedAriaLabel
      ) {
        exactActions.push({ item, details, hitTest });
      }
    }
    if (exactActions.length === 1) {
      const selected = exactActions[0];
      return {
        action: {
          locator: selected.item,
          label: "Message",
          kind: "message",
          hitTest: selected.hitTest,
          details: selected.details,
        },
        visibleActions,
        exactMatchCount: 1,
        elapsedMs: Date.now() - startedAt,
      };
    }
    if (exactActions.length > 1) {
      return {
        action: null,
        visibleActions,
        exactMatchCount: exactActions.length,
        elapsedMs: Date.now() - startedAt,
        reason: "multiple exact Sales Navigator Message actions were visible",
      };
    }
    await page.waitForTimeout(250);
  }
  return {
    action: null,
    visibleActions,
    exactMatchCount: 0,
    elapsedMs: Date.now() - startedAt,
    reason: "exact Sales Navigator Message action did not become hittable",
  };
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
  const locator = page.locator(SALES_NAV_COMPOSER_SELECTOR);
  const count = await locator.count().catch(() => 0);
  const matches = [];
  for (let index = 0; index < count; index += 1) {
    const item = locator.nth(index);
    if (!(await item.isVisible().catch(() => false))) continue;
    const composer = { locator: item, selector: SALES_NAV_COMPOSER_SELECTOR };
    if (!await composerRoot(page, composer)) continue;
    const stateForComposer = await composerState(composer);
    if (recipientMatches(stateForComposer, name)) {
      matches.push({ composer, state: stateForComposer });
    }
  }
  return matches.length === 1 ? matches[0] : null;
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
  const form = composer.locator.locator(
    "xpath=ancestor-or-self::form[@data-x-conversation-widget='compose-form'][1]",
  );
  if ((await form.count().catch(() => 0)) !== 1 || !await form.isVisible().catch(() => false)) {
    return null;
  }
  const selector = "xpath=ancestor-or-self::section[contains(concat(' ', normalize-space(@class), ' '), ' thread-container ')][1]";
  const root = composer.locator.locator(selector);
  if ((await root.count().catch(() => 0)) === 1 && await root.isVisible().catch(() => false)) {
    return root;
  }
  return null;
}

async function composerState(composer) {
  return composer.locator.evaluate((node) => {
    const form = node.closest("form[data-x-conversation-widget='compose-form']");
    const root = form ? form.closest("section.thread-container") : null;
    const recipients = [];
    for (const recipientNode of root ? root.querySelectorAll("h2[aria-label^='Conversation with ']") : []) {
      const ariaLabel = (recipientNode.getAttribute("aria-label") || "").trim();
      const recipient = ariaLabel.replace(/^Conversation with /, "").trim();
      if (recipient) recipients.push(recipient);
    }
    return {
      bodyText: node.value,
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
    source: "value",
  };
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
      element.closest("section.thread-container")
    );
    const rootSelector = "section.thread-container";
    const roots = new Set(Array.from(document.querySelectorAll(rootSelector)).filter(visibleElement));
    for (const composer of document.querySelectorAll("textarea[name='message'][aria-label='Type your message here…']")) {
      const root = rootFor(composer);
      if (root && visibleElement(root)) roots.add(root);
    }
    const describeRoot = (root, index) => {
      const recipients = [];
      for (const recipientNode of root.querySelectorAll("h2[aria-label^='Conversation with ']")) {
        const ariaLabel = normalize(recipientNode.getAttribute("aria-label") || "");
        const recipient = ariaLabel.replace(/^Conversation with /, "").trim();
        if (recipient) recipients.push(recipient);
      }
      const composers = Array.from(root.querySelectorAll("textarea[name='message'][aria-label='Type your message here…']"))
        .filter(visibleElement)
        .map((composer, composerIndex) => ({
          index: composerIndex,
          ariaLabel: composer.getAttribute("aria-label") || "",
          textLength: composer.value.length,
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
        hasTargetName: recipients.some((recipient) => recipient === normalize(targetName)),
        recipients,
        composers,
        actions,
      };
    };
    return {
      targetName,
      containers: Array.from(roots).map(describeRoot),
      composerCount: Array.from(document.querySelectorAll("textarea[name='message'][aria-label='Type your message here…']")).filter(visibleElement).length,
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
    const selector = "article [aria-label^='Message from ']";
    const visibleElement = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    let visible = 0;
    for (const item of node.querySelectorAll(selector)) {
      if (visibleElement(item)) visible += 1;
    }
    if (visible > 0) {
      return { exists: true, scoped: true, matchedRecipient: true, selector, visibleCount: visible };
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
    beforeOpen: { closed: [], kept: [], skipped: [] },
  };
  progress("conversation-cleanup-complete", conversationCleanup.beforeOpen);
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
      const subjectFill = { filled: false };
      if (!bodyFill.matched) {
        await existingComposer.locator.fill(draft, { timeout: 8000 });
        progress("existing-composer-body-fill-command-complete");
        const actual = await existingComposer.locator.evaluate((node) => node.value).catch(() => "");
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

  const actionScan = await waitForProfileMessageAction(page, record.name, 15000);
  progress("profile-action-scan-complete", {
    found: Boolean(actionScan.action),
    exactMatchCount: actionScan.exactMatchCount,
    elapsedMs: actionScan.elapsedMs,
    reason: actionScan.reason,
  });
  const action = actionScan.action;
  if (!action) {
    const ambiguous = actionScan.exactMatchCount > 1;
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: ambiguous ? "blocked" : "not-messageable",
      reason: actionScan.reason,
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
  const subjectFill = { filled: false };
  await composer.locator.fill(draft, { timeout: 8000 });
  progress("body-fill-command-complete");
  const actual = await composer.locator.evaluate((node) => node.value).catch(() => "");
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
