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

function salesProfileId(profileUrl) {
  const match = String(profileUrl || "").match(/\/sales\/lead\/([^,/?#]+)/);
  return match ? match[1] : null;
}

function configValue(name, fallback = null) {
  const config = globalThis.recruiterAgencyMessageConfig || state.recruiterAgencyMessageConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

async function readBody(timeout = 10000) {
  return state.page.locator("body").innerText({ timeout }).catch(() => "");
}

function isHardBlocker(url, body) {
  return /checkpoint|security verification|weekly invitation limit|sign in|uas\/login/i.test(`${url}\n${body.slice(0, 1500)}`);
}

async function findMessageAction() {
  const result = await state.page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll("button,a,[role=button]"));
    let inmail = null;
    for (let index = 0; index < nodes.length; index += 1) {
      const element = nodes[index];
      const rect = element.getBoundingClientRect();
      const visible = rect.width > 0 && rect.height > 0;
      if (!visible || element.hasAttribute("disabled")) {
        continue;
      }
      const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
      const aria = element.getAttribute("aria-label") || "";
      const label = `${text} ${aria}`.trim();
      if (/^Message\b/i.test(text) || /^Message\b/i.test(aria)) {
        element.setAttribute("data-outreach-message-target", "true");
        return { found: true, kind: "message", index, text, aria, tag: element.tagName };
      }
      if (/^InMail\b/i.test(text) || /^InMail\b/i.test(aria)) {
        inmail = { kind: "inmail", index, text, aria, tag: element.tagName };
        element.setAttribute("data-outreach-inmail-target", "true");
      }
    }
    if (inmail) {
      return { found: true, ...inmail };
    }
    return { found: false, reason: "message-action-missing" };
  });
  return result || { found: false, reason: "message-action-missing" };
}

async function clickSearchRowMessageAction(candidate) {
  if (!candidate.searchUrl) {
    return { found: false, reason: "search-url-missing" };
  }
  await state.page.goto(candidate.searchUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  await state.page.waitForLoadState("domcontentloaded").catch(() => {});
  await state.page.waitForTimeout(2500);
  await state.page.waitForFunction(
    () => document.querySelectorAll("li.artdeco-list__item").length > 0 || /No results|0 results/i.test(document.body.innerText || ""),
    null,
    { timeout: 15000 },
  ).catch(() => {});

  const profileId = salesProfileId(candidate.profileUrl);
  return await state.page.evaluate(({ candidateName, profileId }) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const rows = Array.from(document.querySelectorAll("li.artdeco-list__item"));
    const row = rows.find((item) => {
      if (profileId && item.querySelector(`a[href*="/sales/lead/${profileId}"]`)) {
        return true;
      }
      return clean(item.innerText || item.textContent || "").includes(candidateName);
    });
    if (!row) {
      return { found: false, reason: "search-row-missing", profileId, candidateName };
    }
    const nodes = Array.from(row.querySelectorAll("button,a,[role=button]"));
    let inmail = null;
    for (let index = 0; index < nodes.length; index += 1) {
      const element = nodes[index];
      const rect = element.getBoundingClientRect();
      const visible = rect.width > 0 && rect.height > 0;
      if (!visible || element.hasAttribute("disabled")) {
        continue;
      }
      const text = clean(element.innerText || element.textContent || "");
      const aria = element.getAttribute("aria-label") || "";
      if (/^Message\b/i.test(text) || /^Message\b/i.test(aria)) {
        element.click();
        return { found: true, kind: "message", source: "search-row", index, text, aria, tag: element.tagName };
      }
      if (/^InMail\b/i.test(text) || /^InMail\b/i.test(aria)) {
        inmail = { element, action: { found: true, kind: "inmail", source: "search-row", index, text, aria, tag: element.tagName } };
      }
    }
    if (inmail) {
      inmail.element.click();
      return inmail.action;
    }
    return {
      found: false,
      reason: "search-row-message-action-missing",
      rowText: clean(row.innerText || row.textContent || "").slice(0, 800),
    };
  }, { candidateName: candidate.name, profileId });
}

async function clickMarkedMessageAction() {
  const clicked = await state.page.evaluate(() => {
    const element = document.querySelector("[data-outreach-message-target='true'],[data-outreach-inmail-target='true']");
    if (!element) {
      return false;
    }
    element.click();
    return true;
  });
  if (!clicked) {
    throw new Error("message-action-missing-after-mark");
  }
}

async function hasExistingConversation(candidateName) {
  const result = await state.page.evaluate((name) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const dialog =
      Array.from(document.querySelectorAll("[role='dialog'], .msg-overlay-conversation-bubble, .artdeco-modal")).pop()
      || document.body;
    const eventSelectors = [
      ".msg-s-message-list__event",
      ".msg-s-event-listitem",
      ".msg-s-message-group",
      "[data-event-urn]",
      ".msg-s-message-list-content li",
    ];
    const events = eventSelectors.flatMap((selector) =>
      Array.from(dialog.querySelectorAll(selector)).map((node) => clean(node.innerText || node.textContent)),
    ).filter(Boolean);
    const uniqueEvents = Array.from(new Set(events)).filter((text) => {
      if (/^new message$/i.test(text) || /^send$/i.test(text)) return false;
      if (text.length < 8) return false;
      return true;
    });
    const body = clean(dialog.innerText || dialog.textContent);
    const historyScopes = Array.from(dialog.querySelectorAll([
      ".msg-s-message-list",
      ".msg-s-message-list-content",
      ".msg-s-message-list__event",
      ".msg-s-event-listitem",
      ".msg-s-message-group",
    ].join(",")));
    const historyBody = clean(historyScopes.map((node) => node.innerText || node.textContent).join(" "));
    const hasHistoryText = historyBody.length > 0
      && /(Today|Yesterday|Mon|Tue|Wed|Thu|Fri|Sat|Sun|\b\d{1,2}:\d{2}\b|\bAM\b|\bPM\b|You:|sent you|replied)/i.test(historyBody);
    return {
      exists: uniqueEvents.length > 0 || hasHistoryText,
      eventCount: uniqueEvents.length,
      sample: uniqueEvents.slice(0, 3),
      bodySample: body.slice(0, 600),
      historySample: historyBody.slice(0, 600),
      candidateName: name,
    };
  }, candidateName).catch((error) => ({
    exists: false,
    error: String(error).slice(0, 500),
  }));
  return result;
}

async function findComposer() {
  const selectors = [
    "div[role='textbox'][contenteditable='true']",
    "div.msg-form__contenteditable[contenteditable='true']",
    "textarea[name='message']",
    "textarea",
  ];
  for (const selector of selectors) {
    const locator = state.page.locator(selector).last();
    if ((await locator.count()) && (await locator.isVisible().catch(() => false))) {
      return { selector, locator };
    }
  }
  return null;
}

async function waitForComposer(timeoutMS = 5000) {
  const deadline = Date.now() + timeoutMS;
  while (Date.now() < deadline) {
    const composer = await findComposer();
    if (composer) {
      return composer;
    }
    await state.page.waitForTimeout(250);
  }
  return null;
}

async function fillSubjectIfPresent(subject) {
  const selectors = [
    "input[name='subject']",
    "input[placeholder*='Subject' i]",
    "input[aria-label*='Subject' i]",
  ];
  for (const selector of selectors) {
    const locator = state.page.locator(selector).last();
    if ((await locator.count()) && (await locator.isVisible().catch(() => false))) {
      await locator.fill(subject, { timeout: 8000 }).catch(async () => {
        await locator.click({ timeout: 8000 });
        await state.page.keyboard.press("Meta+A");
        await state.page.keyboard.type(subject, { delay: 0 });
      });
      return { filled: true, selector, subject };
    }
  }
  return { filled: false };
}

async function fillComposer(composer, message) {
  await composer.locator.click({ timeout: 8000 });
  await composer.locator.fill(message, { timeout: 8000 }).catch(async () => {
    await state.page.keyboard.press("Meta+A");
    await state.page.keyboard.type(message, { delay: 0 });
  });
}

async function clickSendButton() {
  const clicked = await state.page.evaluate(() => {
    const dialogs = Array.from(document.querySelectorAll("[role='dialog'], .msg-overlay-conversation-bubble, .artdeco-modal"));
    const scopes = dialogs.length ? dialogs.reverse() : [document.body];
    for (const scope of scopes) {
      const buttons = Array.from(scope.querySelectorAll("button"));
      const button = buttons.find((element) => {
        const rect = element.getBoundingClientRect();
        const visible = rect.width > 0 && rect.height > 0;
        const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
        return visible && !element.disabled && /^(Send|Send message)$/i.test(text);
      });
      if (button) {
        const text = (button.innerText || button.textContent || "").replace(/\s+/g, " ").trim();
        button.click();
        return { clicked: true, text };
      }
    }
    return { clicked: false };
  });
  return clicked || { clicked: false };
}

async function openProfileAndClickMessageAction(candidate, profileApiResponses) {
  let navigationError = null;
  try {
    await state.page.goto(candidate.profileUrl, { waitUntil: "domcontentloaded", timeout: 20000 });
    await state.page.waitForLoadState("domcontentloaded");
  } catch (error) {
    navigationError = String(error).slice(0, 500);
    await state.page.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
  }
  await state.page.waitForTimeout(2000);

  const body = await readBody(10000);
  if (navigationError && !body) {
    return { ok: false, result: { status: "navigation-failed", reason: navigationError } };
  }
  if (isHardBlocker(state.page.url(), body)) {
    return {
      ok: false,
      result: { status: "blocked", reason: "checkpoint-login-or-limit", body: cleanText(body).slice(0, 1500) },
    };
  }

  const profileRateLimited = profileApiResponses.some((response) => response.status === 429);
  if (!bodyContainsCandidateName(body, candidate.name)) {
    if (!body && profileRateLimited) {
      return {
        ok: false,
        result: {
          status: "profile-rate-limited",
          reason: "lead profile API returned 429 after search-row message path failed",
          body: "",
        },
      };
    }
    return {
      ok: false,
      result: {
        status: "identity-mismatch",
        reason: "loaded lead page does not contain candidate name",
        body: cleanText(body).slice(0, 1500),
      },
    };
  }

  const action = await findMessageAction();
  if (!action.found) {
    return { ok: false, result: { status: "not-messageable", action } };
  }
  await clickMarkedMessageAction();
  await state.page.waitForTimeout(1500);
  const composer = await waitForComposer(5000);
  if (!composer) {
    const afterBody = await readBody(5000);
    return {
      ok: false,
      result: {
        status: "composer-missing",
        action,
        body: cleanText(afterBody).slice(0, 1500),
      },
    };
  }
  return { ok: true, action, composer };
}

async function main() {
  const candidate = configValue("candidate", null);
  const message = cleanText(configValue("message", ""));
  const subject = cleanText(configValue("subject", "Contract product engineering availability"));
  const out = path.resolve(configValue("out", "/tmp/recruiter-agency-message-result.json"));
  const dryRun = Boolean(configValue("dryRun", true));
  const allowSend = Boolean(configValue("allowSend", false));

  if (!candidate || !candidate.profileUrl) {
    throw new Error("candidate with profileUrl is required in state.recruiterAgencyMessageConfig");
  }
  if (!message) {
    throw new Error("message is required in state.recruiterAgencyMessageConfig");
  }
  if (!dryRun && !allowSend) {
    throw new Error("real send requires allowSend=true");
  }

  state.page = state.page
    || context.pages().find((page) => page.url().includes("/sales/search/people"))
    || context.pages().find((page) => page.url().includes("/sales/lead/"))
    || await context.newPage();
  const profileApiResponses = [];
  const responseHandler = async (response) => {
    if (/\/sales-api\/salesApiProfiles/i.test(response.url())) {
      profileApiResponses.push({ url: response.url().slice(0, 260), status: response.status() });
    }
  };
  state.page.on("response", responseHandler);

  const base = () => ({
    candidate,
    dryRun,
    url: state.page.url(),
    messageLength: message.length,
    status: "unknown",
  });
  const complete = (payload) => {
    const result = { ...base(), ...payload };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    state.page.off("response", responseHandler);
  };

  let action = null;
  let composer = null;
  let searchRowAction = null;

  if (candidate.searchUrl) {
    searchRowAction = await clickSearchRowMessageAction(candidate);
    if (searchRowAction.found) {
      action = searchRowAction;
      await state.page.waitForTimeout(1500);
      composer = await waitForComposer(5000);
      if (!composer) {
        searchRowAction = { ...searchRowAction, found: false, reason: "search-row-composer-missing" };
        action = null;
      }
    }
  }

  if (!action) {
    const fallback = await openProfileAndClickMessageAction(candidate, profileApiResponses);
    if (!fallback.ok) {
      return complete({ ...fallback.result, searchRowAction, profileApiResponses });
    }
    action = fallback.action;
    composer = fallback.composer;
  }

  const conversationCheck = action.kind === "message" ? await hasExistingConversation(candidate.name) : null;
  if (conversationCheck?.exists) {
    return complete({ status: "conversation-exists", action, conversationCheck, profileApiResponses, composerSelector: composer?.selector });
  }
  if (dryRun) {
    return complete({ status: "dry-run-messageable", action, conversationCheck, profileApiResponses, composerSelector: composer?.selector });
  }

  composer = composer || await waitForComposer(5000);
  if (!composer) {
    const afterBody = await readBody(5000);
    return complete({ status: "composer-missing", action, body: cleanText(afterBody).slice(0, 1500), profileApiResponses });
  }

  const subjectFill = action.kind === "inmail" ? await fillSubjectIfPresent(subject) : { filled: false };
  await fillComposer(composer, message);
  await state.page.waitForTimeout(500);
  const send = await clickSendButton();
  if (!send.clicked) {
    return complete({ status: "send-button-missing", action, composerSelector: composer.selector, subjectFill, profileApiResponses });
  }
  await state.page.waitForTimeout(2000);
  return complete({ status: "sent-clicked", action, composerSelector: composer.selector, subjectFill, send, profileApiResponses });
}

await main();
