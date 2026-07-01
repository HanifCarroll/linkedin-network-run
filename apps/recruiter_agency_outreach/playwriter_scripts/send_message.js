const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.recruiterAgencyMessageConfigPath, "utf8"));
const candidate = config.candidate || {};
const message = String(config.message || "").replace(/\r\n/g, "\n").trim();
const subject = String(config.subject || "").trim();
const dryRun = Boolean(config.dryRun);
const allowSend = Boolean(config.allowSend);

function resultBase(pageUrl) {
  return {
    candidate,
    dryRun,
    url: pageUrl,
    messageLength: message.length,
    status: "unknown",
  };
}

function writeResult(payload) {
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`wrote recruiter agency message result to ${config.out}`);
}

async function getPage() {
  if (state.recruiterAgencyMessagePage && !state.recruiterAgencyMessagePage.isClosed()) {
    return state.recruiterAgencyMessagePage;
  }
  const pages = context.pages();
  state.recruiterAgencyMessagePage =
    pages.find((candidatePage) => candidatePage.url().includes("linkedin.com/sales/lead/")) ||
    pages.find((candidatePage) => candidatePage.url().includes("linkedin.com/sales")) ||
    pages.find((candidatePage) => candidatePage.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.recruiterAgencyMessagePage;
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
  const login = await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']");
  const checkpoint = await visibleCount(page, "input[name='pin'], input[name='challengeId']");
  const security = await visibleCount(
    page,
    "iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']"
  );
  if (/\/login|\/uas\/login/i.test(url) || login > 0) return "login required";
  if (/\/checkpoint/i.test(url) || checkpoint > 0) return "checkpoint present";
  if (security > 0) return "security verification present";
  return null;
}

function salesProfileId(url) {
  const match = String(url || "").match(/\/sales\/lead\/([^/?#]+)/i);
  return match ? decodeURIComponent(match[1]) : null;
}

async function profileName(page) {
  const candidates = [
    "h1",
    "[data-anonymize='person-name']",
    "section dt:has-text('Name') + dd",
  ];
  for (const selector of candidates) {
    const text = await page.locator(selector).first().innerText({ timeout: 1500 }).catch(() => "");
    if (text.trim()) return text.trim();
  }
  return String(candidate.name || "");
}

async function findMessageAction(page) {
  const directSelectors = [
    "button[data-anchor-send-inmail]",
    "button[aria-label='Message']",
    "button[aria-label*='Message']",
    "button[aria-label*='InMail']",
  ];
  for (const selector of directSelectors) {
    const locator = page.locator(selector).first();
    if ((await locator.count().catch(() => 0)) && (await locator.isVisible().catch(() => false))) {
      return { locator, kind: selector.includes("inmail") ? "inmail" : "message", label: selector };
    }
  }
  const textAction = page
    .locator("button, a, [role='button']")
    .filter({ hasText: /^(Message|InMail)\b/i })
    .first();
  if ((await textAction.count().catch(() => 0)) && (await textAction.isVisible().catch(() => false))) {
    return { locator: textAction, kind: "message", label: "Message" };
  }
  return null;
}

async function fillEditable(locator, value) {
  await locator.fill(value, { timeout: 8000 }).catch(async (fillError) => {
    const setDirectly = await locator
      .evaluate((node, text) => {
        if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
          node.value = text;
          node.dispatchEvent(new Event("input", { bubbles: true }));
          node.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        }
        if (node instanceof HTMLElement && node.isContentEditable) {
          node.textContent = text;
          node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
          return true;
        }
        return false;
      }, value)
      .catch(() => false);
    if (setDirectly) return;
    await locator.click({ timeout: 8000 });
    await locator.press(process.platform === "darwin" ? "Meta+A" : "Control+A");
    await locator.type(value, { delay: 0 }).catch(() => {
      throw fillError;
    });
  });
}

async function findComposer(page) {
  const selectors = [
    "textarea[name='message']",
    "textarea",
    "[contenteditable='true'][role='textbox']",
    "[contenteditable='true']",
  ];
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if ((await locator.count().catch(() => 0)) && (await locator.isVisible().catch(() => false))) {
      return { locator, selector };
    }
  }
  return null;
}

async function waitForComposer(page) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const composer = await findComposer(page);
    if (composer) return composer;
    await page.waitForTimeout(500);
  }
  return findComposer(page);
}

async function fillSubject(page) {
  if (!subject) return null;
  const selectors = ["input[name='subject']", "input[placeholder*='Subject']"];
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if ((await locator.count().catch(() => 0)) && (await locator.isVisible().catch(() => false))) {
      await locator.fill(subject, { timeout: 8000 });
      return { selector, length: subject.length };
    }
  }
  return null;
}

async function findSendButton(page) {
  const locator = page
    .locator("button, [role='button']")
    .filter({ hasText: /^(Send|Send message)$/i })
    .first();
  if ((await locator.count().catch(() => 0)) && (await locator.isVisible().catch(() => false))) {
    return locator;
  }
  return null;
}

async function main() {
  if (!candidate.profileUrl) {
    writeResult({ ...resultBase(null), status: "send-failed", reason: "candidate with profileUrl is required" });
    return;
  }
  if (!message) {
    writeResult({ ...resultBase(null), status: "send-failed", reason: "message is required" });
    return;
  }
  if (!dryRun && !allowSend) {
    writeResult({ ...resultBase(null), status: "send-failed", reason: "real send requires allowSend=true" });
    return;
  }

  const pageForMessage = await getPage();
  await pageForMessage.goto(candidate.profileUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page: pageForMessage, timeout: 10000 }).catch(() => null);
  const payload = resultBase(pageForMessage.url());
  const blockReason = await classifyPage(pageForMessage);
  if (blockReason) {
    writeResult({ ...payload, status: "blocked", reason: blockReason });
    return;
  }
  if (salesProfileId(candidate.profileUrl) && salesProfileId(pageForMessage.url()) !== salesProfileId(candidate.profileUrl)) {
    writeResult({ ...payload, status: "identity-mismatch", reason: "loaded URL differs" });
    return;
  }

  const action = await findMessageAction(pageForMessage);
  const name = await profileName(pageForMessage);
  if (!action) {
    writeResult({ ...payload, status: "not-messageable" });
    return;
  }

  const actionPayload = {
    kind: action.kind,
    action_label: action.label,
    identity_label: name || String(candidate.name || ""),
    source: "profile-actions",
    opened_page_url: pageForMessage.url(),
    status: "ok",
  };

  if (dryRun) {
    writeResult({ ...payload, status: "dry-run-messageable", action: actionPayload });
    return;
  }

  await action.locator.click({ timeout: 8000 });
  const composer = await waitForComposer(pageForMessage);
  if (!composer) {
    writeResult({ ...payload, status: "composer-missing", action: actionPayload });
    return;
  }

  const subjectFill = await fillSubject(pageForMessage);
  await fillEditable(composer.locator, message);
  const sendButton = await findSendButton(pageForMessage);
  if (!sendButton) {
    writeResult({
      ...payload,
      status: "send-button-missing",
      action: actionPayload,
      composerSelector: composer.selector,
      subjectFill,
      bodyFill: { selector: composer.selector, length: message.length },
    });
    return;
  }

  await sendButton.click({ timeout: 8000 });
  await pageForMessage.waitForTimeout(500);
  writeResult({
    ...payload,
    status: "sent-clicked",
    action: actionPayload,
    composerSelector: composer.selector,
    subjectFill,
    bodyFill: { selector: composer.selector, length: message.length },
    send: {
      action: "send-message",
      label: "Send",
      candidate_id: String(candidate.id || ""),
      dry_run: false,
      approved: allowSend,
    },
  });
}

await main();
