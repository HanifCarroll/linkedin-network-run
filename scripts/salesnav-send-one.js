const fs = require("node:fs");
const path = require("node:path");

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizeName(value) {
  return cleanText(value).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function bodyContainsCandidateName(body, candidateName) {
  const normalizedBody = normalizeName(body);
  const normalizedName = normalizeName(candidateName);
  return Boolean(normalizedName) && normalizedBody.includes(normalizedName);
}

function configValue(name, fallback = null) {
  const config = globalThis.salesNavSendConfig || state.salesNavSendConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

function classifyMenu(labels) {
  const texts = labels.map((label) => label.text).filter(Boolean);
  if (texts.some((text) => /^Connect\s*[—-]\s*Pending$/i.test(text) || /^Pending$/i.test(text))) {
    return "already-pending";
  }
  if (texts.some((text) => /^Connect$/i.test(text))) {
    return "connectable";
  }
  return "unknown";
}

async function readBody(timeout = 10000) {
  return state.page.locator("body").innerText({ timeout }).catch(() => "");
}

function isHardBlocker(url, body) {
  return Boolean(hardBlockReason(url, body));
}

function hardBlockReason(url, body) {
  const text = `${url}\n${body.slice(0, 2500)}`;
  if (/checkpoint|security verification|verify your identity|uas\/login/i.test(text)) {
    return "checkpoint-login-or-security";
  }
  if (/sign in|sign back in|session expired/i.test(text)) {
    return "login-required";
  }
  if (/weekly invitation limit|invitation limit|limit.*invitations|too many invitations|temporarily restricted/i.test(text)) {
    return "invitation-limit-or-restriction";
  }
  if (/could(?:n’t|n't| not) send|unable to send|invitation (?:was )?not sent|something went wrong|try again later/i.test(text)) {
    return "send-rejected";
  }
  return null;
}

async function visibleDialogInfos() {
  return state.page.evaluate(() => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const visible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    return Array.from(document.querySelectorAll("[role='dialog'], .artdeco-modal, [data-test-modal]"))
      .filter(visible)
      .map((dialog, index) => ({
        index,
        text: clean(dialog.innerText || dialog.textContent).slice(0, 1500),
        buttons: Array.from(dialog.querySelectorAll("button"))
          .filter(visible)
          .map((button, buttonIndex) => ({
            index: buttonIndex,
            text: clean(button.innerText || button.textContent || button.getAttribute("aria-label")),
            disabled: button.disabled || button.hasAttribute("disabled") || button.getAttribute("aria-disabled") === "true",
          }))
          .filter((button) => button.text),
      }));
  });
}

async function visibleFeedbackInfos() {
  return state.page.evaluate(() => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const visible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    return Array.from(document.querySelectorAll("[role='alert'], .artdeco-toast-item, .artdeco-toast-item__message, .artdeco-inline-feedback"))
      .filter(visible)
      .map((element, index) => ({ index, text: clean(element.innerText || element.textContent).slice(0, 1000) }))
      .filter((item) => item.text);
  });
}

function summarizeDialogText(dialogs, feedbacks) {
  const parts = [];
  for (const dialog of dialogs || []) {
    if (dialog.text) {
      parts.push(`Dialog ${dialog.index}: ${dialog.text}`);
    }
  }
  for (const feedback of feedbacks || []) {
    if (feedback.text) {
      parts.push(`Feedback ${feedback.index}: ${feedback.text}`);
    }
  }
  return cleanText(parts.join(" ")).slice(0, 1500);
}

async function responseTextSnippet(response, timeoutMs = 1000) {
  return Promise.race([
    response.text().catch(() => ""),
    new Promise((resolve) => setTimeout(() => resolve(""), timeoutMs)),
  ]).then((text) => cleanText(text).slice(0, 1200));
}

function shouldCaptureSendNetwork(url, method, postData) {
  if (!/linkedin\.com\/(voyager|sales-api|salesApi|graphql|api)/i.test(url)) {
    return false;
  }
  const haystack = `${method}\n${url}\n${postData}`;
  return /invite|invitation|connect|connection|relationship|salesProfile/i.test(haystack);
}

async function collectSendNetworkDuring(action, waitMs = 2500) {
  const events = [];
  const listener = async (response) => {
    try {
      const request = response.request();
      const method = request.method();
      const url = response.url();
      const postData = request.postData() || "";
      if (!shouldCaptureSendNetwork(url, method, postData)) {
        return;
      }
      const event = {
        method,
        url,
        status: response.status(),
        request: cleanText(postData).slice(0, 800),
      };
      if (response.status() >= 400 || /invite|invitation|connect|connection/i.test(url)) {
        event.body = await responseTextSnippet(response);
      }
      events.push(event);
    } catch {
      // Best-effort diagnostics only; send safety is enforced by DOM/audit verification.
    }
  };
  state.page.on("response", listener);
  try {
    await action();
    await state.page.waitForTimeout(waitMs);
  } finally {
    state.page.off("response", listener);
  }
  return events.slice(-12);
}

function classifySendNetworkProblem(networkEvents) {
  if (!networkEvents || networkEvents.length === 0) {
    return null;
  }
  const combined = networkEvents.map((event) => `${event.status} ${event.url} ${event.request || ""} ${event.body || ""}`).join("\n");
  const blockReason = hardBlockReason("", combined);
  if (blockReason) {
    return { state: "blocked", reason: blockReason, body: cleanText(combined).slice(0, 1500) };
  }
  const rejected = networkEvents.find((event) => event.status === 403 || event.status === 429 || event.status >= 500);
  if (rejected) {
    return {
      state: "blocked",
      reason: `send-network-${rejected.status}`,
      body: cleanText(`${rejected.url} ${rejected.body || rejected.request || ""}`).slice(0, 1500),
    };
  }
  const errorBody = networkEvents.find((event) => /"errors?"\s*:|error|exception|fail/i.test(`${event.body || ""} ${event.request || ""}`));
  if (errorBody) {
    return {
      state: "send-not-accepted",
      reason: "send-network-error",
      body: cleanText(`${errorBody.url} ${errorBody.body || errorBody.request || ""}`).slice(0, 1500),
    };
  }
  return null;
}

function hasAcceptedConnectNetwork(networkEvents) {
  return (networkEvents || []).some((event) =>
    event.status >= 200 &&
    event.status < 300 &&
    /salesApiConnection/i.test(event.url || "") &&
    /connectV2/i.test(event.url || "")
  );
}

async function classifyPostClickProblem(options = {}) {
  const includeOpenInviteDialog = options.includeOpenInviteDialog !== false;
  const body = cleanText(await readBody(10000));
  const dialogs = await visibleDialogInfos();
  const feedbacks = await visibleFeedbackInfos();
  const focusedText = summarizeDialogText(dialogs, feedbacks);
  const text = `${focusedText}\n${body}`;
  if (/email address|required email|enter.*email/i.test(text)) {
    return { state: "email-required", labels: [], body: cleanText(text).slice(0, 1500), dialogs, feedbacks };
  }
  const blockReason = hardBlockReason(state.page.url(), text);
  if (blockReason) {
    return { state: "blocked", reason: blockReason, labels: [], body: cleanText(text).slice(0, 1500), dialogs, feedbacks };
  }
  if (includeOpenInviteDialog) {
    const inviteDialog = dialogs.find((dialog) =>
      /send invitation/i.test(dialog.text) ||
      dialog.buttons.some((button) => /^(Send Invitation|Send invite|Send now|Send)$/i.test(button.text))
    );
    if (inviteDialog) {
      return {
        state: "send-not-accepted",
        labels: inviteDialog.buttons,
        body: inviteDialog.text,
        dialogs,
        feedbacks,
      };
    }
  }
  return null;
}

async function menuLabelsForCurrentLead() {
  await state.page.keyboard.press("Escape").catch(() => {});
  const trigger = state.page.locator('button[aria-label="Open actions overflow menu"]').first();
  await trigger.waitFor({ state: "visible", timeout: 8000 }).catch(() => {});
  if (!(await trigger.count())) {
    return { state: "missing-trigger", labels: [] };
  }
  const menuId = await trigger.getAttribute("aria-controls");
  await trigger.click({ timeout: 8000 });
  await state.page.waitForTimeout(500);
  const menu = menuId ? state.page.locator(`#${menuId}`) : state.page.locator("[data-popper-placement]").last();
  if (!(await menu.count())) {
    return { state: "missing-menu", labels: [] };
  }
  const labels = await menu.locator("button,a,[role=menuitem]").evaluateAll((items) =>
    items.map((item, index) => ({
      index,
      text: (item.innerText || item.textContent || "").replace(/\s+/g, " ").trim(),
      aria: item.getAttribute("aria-label"),
      tag: item.tagName,
      href: item.href || null,
      disabled: item.hasAttribute("disabled"),
    })).filter((item) => item.text || item.aria),
  );
  return { state: classifyMenu(labels), labels, menuId };
}

async function verifyAfterSend() {
  const checks = [];
  let last = { state: "unknown", labels: [] };
  const attempts = [
    { phase: "menu-verify-1", delayMs: 1500, reload: false },
    { phase: "menu-verify-2", delayMs: 2500, reload: false },
    { phase: "reload-verify", delayMs: 1500, reload: true },
    { phase: "post-reload-verify", delayMs: 2500, reload: false },
  ];

  for (const attempt of attempts) {
    if (attempt.delayMs > 0) {
      await state.page.waitForTimeout(attempt.delayMs);
    }
    if (attempt.reload) {
      await state.page.reload({ waitUntil: "domcontentloaded", timeout: 45000 }).catch((error) => {
        checks.push({ phase: "reload-error", error: String(error).slice(0, 500) });
      });
      await state.page.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});
      await state.page.waitForTimeout(1000);
    }

    const problem = await classifyPostClickProblem();
    if (problem) {
      last = { ...problem, phase: attempt.phase };
      checks.push({ phase: attempt.phase, state: last.state });
      return { ...last, checks };
    }

    last = await menuLabelsForCurrentLead().catch((error) => ({
      state: "verify-error",
      labels: [],
      error: String(error).slice(0, 500),
    }));
    checks.push({
      phase: attempt.phase,
      state: last.state,
      labels: (last.labels || []).map((item) => item.text || item.aria).filter(Boolean).slice(0, 8),
    });
    if (last.state === "already-pending") {
      return { ...last, checks };
    }
  }

  return { ...last, checks };
}

async function clickCurrentMenuConnect(menuId) {
  const menu = menuId
    ? state.page.locator(`#${menuId}`)
    : state.page.locator("[data-popper-placement], [id^='hue-menu-']").filter({ hasText: "Connect" }).last();
  const clicked = await menu.evaluate((menuElement) => {
    const item = Array.from(menuElement.querySelectorAll("button,a,[role=menuitem]"))
      .find((element) => (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim() === "Connect");
    if (!item) {
      return false;
    }
    item.click();
    return true;
  });
  if (!clicked) {
    throw new Error("connect-menu-item-missing");
  }
}

async function clickSendInvitation(candidate) {
  const body = await state.page.locator("body").innerText({ timeout: 10000 });
  if (!bodyContainsCandidateName(body, candidate.name)) {
    return {
      status: "identity-mismatch",
      detail: "invite flow does not contain candidate name",
      body: cleanText(body).slice(0, 1000),
    };
  }
  if (/email address|required email|enter.*email/i.test(body)) {
    return { status: "email-required", detail: "email required in invite flow" };
  }
  const dialogs = await visibleDialogInfos();
  const feedbacks = await visibleFeedbackInfos();
  const focusedText = summarizeDialogText(dialogs, feedbacks);
  const blockReason = hardBlockReason(state.page.url(), `${focusedText}\n${body}`);
  if (blockReason) {
    return { status: "blocked", reason: blockReason, body: focusedText || cleanText(body).slice(0, 1500), dialogs, feedbacks };
  }
  const inviteDialogs = dialogs.filter((dialog) =>
    /send invitation/i.test(dialog.text) ||
    dialog.buttons.some((button) => /^(Send Invitation|Send invite|Send now|Send)$/i.test(button.text))
  );
  const candidateInviteDialogs = inviteDialogs.filter((dialog) => bodyContainsCandidateName(dialog.text, candidate.name));
  const selectedDialog = candidateInviteDialogs.at(-1) || inviteDialogs.at(-1);
  if (selectedDialog && !bodyContainsCandidateName(selectedDialog.text, candidate.name)) {
    return {
      status: "identity-mismatch",
      detail: "invite dialog does not contain candidate name",
      body: selectedDialog.text,
      dialogs,
      feedbacks,
    };
  }
  if (selectedDialog) {
    const sendButtonInfo = selectedDialog.buttons.find((button) => /^(Send Invitation|Send invite|Send now|Send)$/i.test(button.text));
    if (sendButtonInfo && sendButtonInfo.disabled) {
      return {
        status: "send-button-disabled",
        detail: selectedDialog.text,
        dialogs,
        feedbacks,
      };
    }
  }

  const clickTarget = await state.page.evaluate((candidateName) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const normalizeName = (value) => clean(value).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
    const bodyContainsCandidateName = (text, name) => {
      const normalizedName = normalizeName(name);
      return Boolean(normalizedName) && normalizeName(text).includes(normalizedName);
    };
    const visible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const dialogs = Array.from(document.querySelectorAll("[role='dialog'], .artdeco-modal, [data-test-modal]")).filter(visible);
    const inviteDialogs = dialogs.filter((dialog) => {
      const text = clean(dialog.innerText || dialog.textContent);
      return /send invitation/i.test(text) ||
        Array.from(dialog.querySelectorAll("button")).some((button) => /^(Send Invitation|Send invite|Send now|Send)$/i.test(clean(button.innerText || button.textContent)));
    });
    const candidateInviteDialogs = inviteDialogs.filter((dialog) => bodyContainsCandidateName(clean(dialog.innerText || dialog.textContent), candidateName));
    const dialog = candidateInviteDialogs.at(-1) || inviteDialogs.at(-1);
    if (!dialog) {
      return null;
    }
    const button = Array.from(dialog.querySelectorAll("button"))
      .filter(visible)
      .find((element) => /^(Send Invitation|Send invite|Send now|Send)$/i.test(clean(element.innerText || element.textContent)));
    if (!button) {
      return null;
    }
    const label = clean(button.innerText || button.textContent);
    const rect = button.getBoundingClientRect();
    return {
      label,
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }, candidate.name).catch(() => null);
  if (!clickTarget) {
    return { status: "send-button-missing", detail: focusedText || cleanText(body).slice(0, 1000), dialogs, feedbacks };
  }
  const network = await collectSendNetworkDuring(() => state.page.mouse.click(clickTarget.x, clickTarget.y));
  const networkProblem = classifySendNetworkProblem(network);
  if (networkProblem) {
    if (networkProblem.state === "blocked") {
      return { status: "blocked", label: clickTarget.label, reason: networkProblem.reason, body: networkProblem.body, network };
    }
    return { status: networkProblem.state, label: clickTarget.label, detail: networkProblem.body, network };
  }
  const postClickProblem = await classifyPostClickProblem({ includeOpenInviteDialog: false });
  if (postClickProblem) {
    if (postClickProblem.state === "blocked") {
      return { status: "blocked", label: clickTarget.label, reason: postClickProblem.reason, body: postClickProblem.body, dialogs: postClickProblem.dialogs, feedbacks: postClickProblem.feedbacks, network };
    }
    if (postClickProblem.state === "email-required") {
      if (!hasAcceptedConnectNetwork(network)) {
        return { status: "email-required", label: clickTarget.label, detail: postClickProblem.body, dialogs: postClickProblem.dialogs, feedbacks: postClickProblem.feedbacks, network };
      }
      return { status: "clicked-send", label: clickTarget.label, detail: postClickProblem.body, dialogs: postClickProblem.dialogs, feedbacks: postClickProblem.feedbacks, network };
    }
    return { status: postClickProblem.state, label: clickTarget.label, detail: postClickProblem.body, dialogs: postClickProblem.dialogs, feedbacks: postClickProblem.feedbacks, network };
  }
  return { status: "clicked-send", label: clickTarget.label, network };
}

async function main() {
  const candidate = configValue("candidate", null);
  const out = path.resolve(configValue("out", "/tmp/linkedin-network-run-send-result.json"));
  const dryRun = Boolean(configValue("dryRun", true));
  const allowSend = Boolean(configValue("allowSend", false));

  if (!candidate || !candidate.profile_url) {
    throw new Error("candidate with profile_url is required in state.salesNavSendConfig");
  }
  if (!dryRun && !allowSend) {
    throw new Error("real send requires allowSend=true");
  }

  state.page = state.page || context.pages().find((page) => page.url().includes("/sales/lead/")) || await context.newPage();
  let navigationError = null;
  try {
    await state.page.goto(candidate.profile_url, { waitUntil: "domcontentloaded", timeout: 15000 });
    await state.page.waitForLoadState("domcontentloaded");
  } catch (error) {
    navigationError = String(error).slice(0, 500);
    await state.page.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
  }
  await state.page.waitForTimeout(2500);

  const body = await readBody(10000);
  if (navigationError && !body) {
    const result = {
      status: "navigation-failed",
      reason: navigationError,
      candidate,
      url: state.page.url(),
    };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  if (isHardBlocker(state.page.url(), body)) {
    const result = {
      status: "blocked",
      reason: "checkpoint-login-or-limit",
      candidate,
      url: state.page.url(),
      body: cleanText(body).slice(0, 1500),
    };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  if (!bodyContainsCandidateName(body, candidate.name)) {
    const result = {
      status: "identity-mismatch",
      reason: "loaded lead page does not contain candidate name",
      candidate,
      url: state.page.url(),
      body: cleanText(body).slice(0, 1500),
    };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  const before = await menuLabelsForCurrentLead();
  const result = {
    candidate,
    dryRun,
    url: state.page.url(),
    before,
    send: null,
    after: null,
    status: "unknown",
  };

  if (before.state === "already-pending") {
    result.status = "already-pending";
  } else if (before.state !== "connectable") {
    result.status = `not-connectable:${before.state}`;
  } else if (dryRun) {
    result.status = "dry-run-connectable";
  } else {
    await clickCurrentMenuConnect(before.menuId);
    await state.page.waitForTimeout(1200);
    result.send = await clickSendInvitation(candidate);
    if (result.send.status === "email-required") {
      result.status = "email-required";
      result.after = { state: "email-required", labels: [], body: result.send.detail, dialogs: result.send.dialogs, feedbacks: result.send.feedbacks };
    } else if (result.send.status === "blocked") {
      result.status = "blocked";
      result.after = { state: "blocked", labels: [], reason: result.send.reason, body: result.send.body, dialogs: result.send.dialogs, feedbacks: result.send.feedbacks };
    } else if (result.send.status === "identity-mismatch") {
      result.status = "identity-mismatch";
      result.after = { state: "identity-mismatch", labels: [], body: result.send.body, dialogs: result.send.dialogs, feedbacks: result.send.feedbacks };
    } else if (result.send.status === "send-button-missing") {
      result.status = "unverified:send-button-missing";
      result.after = await menuLabelsForCurrentLead().catch((error) => ({
        state: "verify-error",
        labels: [],
        error: String(error).slice(0, 500),
      }));
    } else if (result.send.status === "send-button-disabled" || result.send.status === "send-not-accepted") {
      result.status = `unverified:${result.send.status}`;
      result.after = { state: result.send.status, labels: [], body: result.send.detail, dialogs: result.send.dialogs, feedbacks: result.send.feedbacks };
    } else {
      result.after = await verifyAfterSend();
      if (result.after.state === "already-pending") {
        result.status = "pending-verified";
      } else if (result.after.state === "blocked") {
        result.status = "blocked";
      } else if (result.after.state === "email-required") {
        result.status = "email-required";
      } else if (result.after.state === "send-not-accepted") {
        result.status = "unverified:send-not-accepted";
      } else {
        result.status = `unverified:${result.send.status}`;
      }
    }
  }

  await state.page.keyboard.press("Escape").catch(() => {});
  fs.writeFileSync(out, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
}

await main();
