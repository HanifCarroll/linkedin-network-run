const fs = require("node:fs");
const crypto = require("node:crypto");

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
const SALES_NAV_DIALOG_SELECTOR = "section[role='dialog'][aria-label^='Conversation with ']";
const SALES_NAV_COMPOSER_SELECTOR = "form[data-x-conversation-widget='compose-form'] textarea[name='message']";
const SALES_NAV_MESSAGE_SELECTOR = "article";
const SALES_NAV_OUTBOUND_MESSAGE_MARKER = "[aria-label='Message from you']";

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
  const intent = Array.isArray(record.attempts)
    ? [...record.attempts].reverse().find((attempt) => attempt.status === "send-intent-recorded")
    : null;
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
    transactionId: intent ? intent.transaction_id || intent.transactionId || null : null,
    messageSha256: intent
      ? intent.message_sha256 || intent.messageSha256 || null
      : crypto.createHash("sha256").update(draft, "utf8").digest("hex"),
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

function salesNavLeadId(urlValue) {
  try {
    const match = new URL(urlValue).pathname.match(/^\/sales\/lead\/([^,/]+)/);
    return match ? match[1] : null;
  } catch (_error) {
    return null;
  }
}

async function inspectProfileIdentity(page, expectedProfileUrl) {
  const expectedLeadId = salesNavLeadId(expectedProfileUrl);
  const loadedLeadId = salesNavLeadId(page.url());
  return {
    matched: Boolean(expectedLeadId) && loadedLeadId === expectedLeadId,
    expectedLeadId,
    loadedLeadId,
  };
}

async function waitForProfileMessageAction(page, expectedProfileUrl, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  const startedAt = Date.now();
  let visibleActions = [];
  let profileIdentity = null;
  while (Date.now() < deadline) {
    profileIdentity = await inspectProfileIdentity(page, expectedProfileUrl);
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
        profileIdentity.matched &&
        /^Message(?: .+)?$/.test(normalizeMessage(details.label))
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
        profileIdentity,
        elapsedMs: Date.now() - startedAt,
      };
    }
    if (exactActions.length > 1) {
      return {
        action: null,
        visibleActions,
        exactMatchCount: exactActions.length,
        profileIdentity,
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
    profileIdentity,
    elapsedMs: Date.now() - startedAt,
    reason: profileIdentity && !profileIdentity.matched
      ? "loaded Sales Navigator lead identity did not match candidate"
      : "exact Sales Navigator Message action did not become hittable",
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

async function conversationRootState(root) {
  return root.evaluate((node) => {
    const ariaLabel = (node.getAttribute("aria-label") || "").trim();
    const recipient = ariaLabel.replace(/^Conversation with /, "").trim();
    const recipientLeadIds = [];
    for (const link of node.querySelectorAll("a[href^='/sales/lead/']")) {
      const href = link.getAttribute("href") || "";
      const match = href.match(/^\/sales\/lead\/([^,/]+)/);
      if (match && !recipientLeadIds.includes(match[1])) recipientLeadIds.push(match[1]);
    }
    return {
      recipients: recipient ? [recipient] : [],
      recipientLeadIds,
    };
  });
}

async function visibleConversationRootForRecipient(page, expectedProfileUrl) {
  const roots = page.locator(SALES_NAV_DIALOG_SELECTOR);
  const count = await roots.count().catch(() => 0);
  const matches = [];
  for (let index = 0; index < count; index += 1) {
    const root = roots.nth(index);
    if (!(await root.isVisible().catch(() => false))) continue;
    const state = await conversationRootState(root).catch((error) => ({
      recipients: [],
      recipientLeadIds: [],
      observationError: error.message,
    }));
    if (recipientMatches(state, expectedProfileUrl)) matches.push({ root, state });
  }
  return matches.length === 1 ? matches[0] : null;
}

async function visibleComposerForRecipient(page, expectedProfileUrl) {
  const rootMatch = await visibleConversationRootForRecipient(page, expectedProfileUrl);
  if (!rootMatch) return null;
  const locator = rootMatch.root.locator(SALES_NAV_COMPOSER_SELECTOR);
  if ((await locator.count().catch(() => 0)) !== 1) return null;
  if (!(await locator.isVisible().catch(() => false))) return null;
  const composer = { locator, selector: SALES_NAV_COMPOSER_SELECTOR };
  const state = await composerState(composer, rootMatch.state);
  return { root: rootMatch.root, composer, state };
}

async function waitForComposerForRecipient(page, expectedProfileUrl, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  let match = null;
  while (Date.now() < deadline) {
    match = await visibleComposerForRecipient(page, expectedProfileUrl);
    if (match) return match;
    await page.waitForTimeout(250);
  }
  return match || await visibleComposerForRecipient(page, expectedProfileUrl);
}

async function composerState(composer, rootState) {
  const bodyText = await composer.locator.inputValue({ timeout: 8000 });
  return { ...rootState, bodyText };
}

function recipientMatches(state, expectedProfileUrl) {
  const expectedLeadId = salesNavLeadId(expectedProfileUrl);
  if (!expectedLeadId) return false;
  return (state.recipientLeadIds || []).includes(expectedLeadId);
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

async function fillAndReacquireComposer(page, expectedProfileUrl, draft, stepPrefix = "") {
  const before = await visibleComposerForRecipient(page, expectedProfileUrl);
  if (!before) {
    return {
      ok: false,
      status: "composer-observation-failed",
      reason: "recipient-bound composer was unavailable immediately before fill",
    };
  }
  await before.composer.locator.fill(draft, { timeout: 8000 });
  progress(`${stepPrefix}body-fill-command-complete`);
  const after = await waitForComposerForRecipient(page, expectedProfileUrl, 8000);
  if (!after) {
    return {
      ok: false,
      status: "composer-observation-failed",
      reason: "recipient-bound composer could not be reacquired after fill",
    };
  }
  let actual;
  try {
    actual = await after.composer.locator.inputValue({ timeout: 8000 });
  } catch (error) {
    return {
      ok: false,
      status: "body-fill-verification-failed",
      reason: `failed to read recipient-bound composer after fill: ${error.message}`,
    };
  }
  const bodyFill = bodyFillResult(after.composer, draft, actual);
  progress(`${stepPrefix}body-fill-verified`, bodyFill);
  if (!bodyFill.matched) {
    return {
      ok: false,
      status: "body-fill-verification-failed",
      reason: "recipient-bound composer did not contain the exact stored welcome after fill",
      bodyFill,
    };
  }
  return { ok: true, target: after, bodyFill };
}

async function recipientMessagingContract(root) {
  return root.evaluate((node) => {
    const visibleElement = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const subject = Array.from(node.querySelectorAll("input[aria-label='Subject (required)']"))
      .find(visibleElement);
    const secondDegree = node.querySelector("[aria-label='Second-degree connection']");
    const firstDegree = node.querySelector("[aria-label='First-degree connection']");
    const saveLead = Array.from(node.querySelectorAll("input[type='checkbox'][aria-label^='Save ']"))
      .find(visibleElement);
    const mode = subject ? "inmail" : "direct-message";
    const currentRelationship = secondDegree
      ? "second-degree"
      : firstDegree
        ? "first-degree"
        : "not-declared";
    return {
      allowed: mode === "direct-message" && currentRelationship !== "second-degree" && !saveLead,
      mode,
      currentRelationship,
      subjectRequired: Boolean(subject),
      saveLeadControl: saveLead
        ? {
            present: true,
            checked: Boolean(saveLead.checked),
            ariaLabel: saveLead.getAttribute("aria-label") || "",
          }
        : { present: false },
    };
  });
}

async function exactOutboundMessageState(root, draft) {
  return root.evaluate((node, { selector, outboundMarker, expected }) => {
    const normalize = (text) => String(text || "").replace(/\s+/g, " ").trim();
    const visibleElement = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const matches = [];
    for (const item of node.querySelectorAll(selector)) {
      if (!visibleElement(item) || !item.querySelector(outboundMarker)) continue;
      const exactParagraphs = Array.from(item.querySelectorAll("p"))
        .filter(visibleElement)
        .filter((paragraph) => normalize(paragraph.textContent) === normalize(expected));
      if (exactParagraphs.length !== 1) continue;
      matches.push({
        direction: "outbound",
        marker: outboundMarker,
        text: normalize(exactParagraphs[0].textContent),
      });
    }
    return {
      confirmed: matches.length === 1,
      selector,
      exactMatchCount: matches.length,
      matches,
    };
  }, {
    selector: SALES_NAV_MESSAGE_SELECTOR,
    outboundMarker: SALES_NAV_OUTBOUND_MESSAGE_MARKER,
    expected: draft,
  });
}

async function waitForExactOutboundMessage(page, expectedProfileUrl, draft, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let latest = {
    confirmed: false,
    selector: SALES_NAV_MESSAGE_SELECTOR,
    exactMatchCount: 0,
    reason: "recipient-bound conversation was unavailable after Send",
  };
  while (Date.now() < deadline) {
    const match = await visibleConversationRootForRecipient(page, expectedProfileUrl);
    if (match) {
      try {
        latest = await exactOutboundMessageState(match.root, draft);
      } catch (error) {
        latest = {
          confirmed: false,
          selector: SALES_NAV_MESSAGE_SELECTOR,
          exactMatchCount: 0,
          reason: `failed to inspect recipient-bound conversation after Send: ${error.message}`,
        };
      }
      if (latest.confirmed) return latest;
    }
    await page.waitForTimeout(250);
  }
  return latest;
}

async function sendVerifiedWelcome(page, expectedProfileUrl, draft, payload, actionPayload, conversationCleanup, stepPrefix = "") {
  const initialTarget = await visibleComposerForRecipient(page, expectedProfileUrl);
  if (!initialTarget) {
    return {
      ...payload,
      status: "composer-observation-failed",
      reason: "recipient-bound composer was unavailable before send validation",
      action: actionPayload,
      conversationCleanup,
    };
  }
  const composerContract = await recipientMessagingContract(initialTarget.root);
  if (!composerContract.allowed) {
    return {
      ...payload,
      status: "direct-message-unavailable",
      reason: "recipient composer is not an allowed first-degree direct-message contract",
      action: actionPayload,
      conversationCleanup,
      composerContract,
    };
  }
  const fill = await fillAndReacquireComposer(page, expectedProfileUrl, draft, stepPrefix);
  const subjectFill = { filled: false };
  const filledPayload = {
    ...payload,
    action: actionPayload,
    composerSelector: SALES_NAV_COMPOSER_SELECTOR,
    conversationCleanup,
    subjectFill,
    bodyFill: fill.bodyFill || null,
    composerContract,
  };
  if (!fill.ok) {
    return { ...filledPayload, status: fill.status, reason: fill.reason };
  }
  if (config.previewFill) {
    return { ...filledPayload, status: "preview-filled" };
  }
  const sendScan = await waitForVisibleActionInRoot(page, fill.target.root, /^(Send|Send message)$/i, 8000);
  progress(`${stepPrefix}send-scan-complete`, { found: Boolean(sendScan.action) });
  if (!sendScan.action) {
    return { ...filledPayload, status: "send-button-missing", sendButtons: sendScan.visibleActions };
  }
  if (!config.allowSend) {
    return { ...filledPayload, status: "blocked", reason: "real send requires allowSend" };
  }
  progress(`${stepPrefix}send-click-intent`, {
    transactionId: payload.transactionId,
    messageSha256: payload.messageSha256,
  });
  const sendClick = await clickAction(sendScan.action);
  progress(`${stepPrefix}send-clicked`, sendClick);
  const sendConfirmation = await waitForExactOutboundMessage(page, expectedProfileUrl, draft, 15000);
  progress(`${stepPrefix}send-confirmation-complete`, sendConfirmation);
  const send = { status: "clicked", action: "send-message", ...sendClick };
  if (!sendConfirmation.confirmed) {
    return {
      ...filledPayload,
      status: "send-confirmation-missing",
      reason: "Send was clicked but the exact welcome was not confirmed in the recipient-bound conversation",
      send,
      sendConfirmation,
    };
  }
  return {
    ...filledPayload,
    status: "sent-confirmed",
    send: { ...send, status: "confirmed" },
    sendConfirmation,
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
      element.closest("section[role='dialog'][aria-label^='Conversation with ']")
    );
    const rootSelector = "section[role='dialog'][aria-label^='Conversation with ']";
    const roots = new Set(Array.from(document.querySelectorAll(rootSelector)).filter(visibleElement));
    for (const composer of document.querySelectorAll("form[data-x-conversation-widget='compose-form'] textarea[name='message']")) {
      const root = rootFor(composer);
      if (root && visibleElement(root)) roots.add(root);
    }
    const describeRoot = (root, index) => {
      const recipients = [];
      const ariaLabel = normalize(root.getAttribute("aria-label") || "");
      const recipient = ariaLabel.replace(/^Conversation with /, "").trim();
      if (recipient) recipients.push(recipient);
      const composers = Array.from(root.querySelectorAll("form[data-x-conversation-widget='compose-form'] textarea[name='message']"))
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
      composerCount: Array.from(document.querySelectorAll("form[data-x-conversation-widget='compose-form'] textarea[name='message']")).filter(visibleElement).length,
    };
  }, name).catch((error) => ({
    targetName: name,
    error: `message diagnostics failed: ${error.message}`,
  }));
}

async function visibleConversationHistory(root, stateForRoot, expectedProfileUrl) {
  const matchedRecipient = recipientMatches(stateForRoot, expectedProfileUrl);
  if (!matchedRecipient) {
    return {
      exists: false,
      scoped: true,
      matchedRecipient,
      reason: "active message container did not match target recipient",
      recipients: stateForRoot.recipients,
    };
  }
  return root.evaluate((node, selector) => {
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
  }, SALES_NAV_MESSAGE_SELECTOR).catch(() => ({
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
  const existingTargetComposer = await visibleComposerForRecipient(page, profileUrl);
  if (existingTargetComposer) {
    progress("existing-target-composer-found");
    const existingComposer = existingTargetComposer.composer;
    const existingState = existingTargetComposer.state;
    const bodyFill = bodyFillResult(existingComposer, draft, existingState.bodyText);
    const matchedRecipient = recipientMatches(existingState, profileUrl);
    const actionPayload = {
      kind: "message",
      action_label: "Message",
      identity_label: record.name,
      source: "existing-compose",
      opened_page_url: page.url(),
    };
    const conversationCheck = await visibleConversationHistory(
      existingTargetComposer.root,
      existingState,
      profileUrl,
    );
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
    const composerContract = await recipientMessagingContract(existingTargetComposer.root);
    if (!composerContract.allowed) {
      fs.writeFileSync(config.out, `${JSON.stringify({
        ...payload,
        status: "direct-message-unavailable",
        reason: "recipient composer is not an allowed first-degree direct-message contract",
        action: actionPayload,
        composerSelector: existingComposer.selector,
        composerContract,
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
        composerContract,
        conversationCheck,
        conversationCleanup,
        existingComposer: {
          matchedRecipient,
          recipients: existingState.recipients,
          recipientLeadIds: existingState.recipientLeadIds,
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
      }, null, 2)}\n`);
      return;
    }
    const result = await sendVerifiedWelcome(
      page,
      profileUrl,
      draft,
      payload,
      actionPayload,
      conversationCleanup,
      "existing-composer-",
    );
    fs.writeFileSync(config.out, `${JSON.stringify(result, null, 2)}\n`);
    return;
  }

  const actionScan = await waitForProfileMessageAction(page, profileUrl, 15000);
  progress("profile-action-scan-complete", {
    found: Boolean(actionScan.action),
    exactMatchCount: actionScan.exactMatchCount,
    elapsedMs: actionScan.elapsedMs,
    reason: actionScan.reason,
    profileIdentity: actionScan.profileIdentity,
  });
  const action = actionScan.action;
  if (!action) {
    const ambiguous = actionScan.exactMatchCount > 1;
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: ambiguous ? "blocked" : "not-messageable",
      reason: actionScan.reason,
      visibleActions: actionScan.visibleActions,
      profileIdentity: actionScan.profileIdentity,
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
  let targetComposer = await waitForComposerForRecipient(page, profileUrl, 8000);
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
  targetComposer = await visibleComposerForRecipient(page, profileUrl) || targetComposer;
  const composer = targetComposer.composer;
  const stateForRoot = targetComposer.state;
  const conversationCheck = await visibleConversationHistory(
    targetComposer.root,
    stateForRoot,
    profileUrl,
  );
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

  const composerContract = await recipientMessagingContract(targetComposer.root);
  if (!composerContract.allowed) {
    fs.writeFileSync(config.out, `${JSON.stringify({
      ...payload,
      status: "direct-message-unavailable",
      reason: "recipient composer is not an allowed first-degree direct-message contract",
      action: actionPayload,
      composerSelector: composer.selector,
      composerContract,
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
      composerContract,
      conversationCheck,
      conversationCleanup,
    }, null, 2)}\n`);
    return;
  }
  const result = await sendVerifiedWelcome(
    page,
    profileUrl,
    draft,
    payload,
    actionPayload,
    conversationCleanup,
  );
  fs.writeFileSync(config.out, `${JSON.stringify(result, null, 2)}\n`);
}

await main();
