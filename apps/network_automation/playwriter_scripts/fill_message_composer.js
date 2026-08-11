// Verify the open composer's recipient and fill the approved message.
// Step 2 of the manual DM pipeline. Reads state from open_message_composer.js
// plus the per-lead fill block, verifies the recipient name matches, then
// fills subject + body. Does NOT send.
//
// Inputs (state):
//   state.composerPage   - page holding the open composer (from step 1)
//   state.lead           - required object:
//       { name, first, profileUrl, subject, message }
//   or flat state fields: state.leadName, state.leadFirst, state.profileUrl,
//                         state.messageSubject, state.messageBody
//   state.sendMode        - "opening" (default) | "followup". Filling an
//                           existing conversation requires "followup".
//
// Outputs (state):
//   state.fill = {
//     status: "filled" | "recipient-mismatch" | "no-composer" | "blocked" | "error",
//     expectedName, composerRecipient, verified: true|false,
//     subject, subjectLength, body, bodyLength, reason?
//   }

const fs = require("node:fs");

function nowIso() {
  return new Date().toISOString();
}

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function firstToken(name) {
  return String(name || "").trim().split(/\s+/)[0] || "";
}

const COMPOSER_DIALOG_SELECTOR =
  "section[role='dialog'][aria-label^='Conversation with ']";
const SUBJECT_SELECTOR =
  "input#compose-form-subject, input[placeholder*='Subject'], input[aria-label*='Subject']";
const MESSAGE_SELECTOR =
  "textarea[name='message'], textarea#compose-form-text, textarea[aria-label*='message']";

function baseReport(expectedName, profileUrl) {
  return {
    status: "unknown",
    expectedName,
    profileUrl,
    composerRecipient: null,
    verified: false,
    subject: null,
    subjectLength: null,
    body: null,
    bodyLength: null,
    reason: null,
    at: nowIso(),
  };
}

function recipientMatches(expected, actual) {
  if (!expected || !actual) return false;
  const expectedFirst = firstToken(expected).toLowerCase();
  const actualClean = actual.toLowerCase();
  // The dialog label is "Conversation with <First Last>". Match on first name
  // (case-insensitive, prefix-safe) to tolerate middle initials/suffixes.
  return actualClean.includes(expectedFirst);
}

async function classifyBlock(page) {
  const url = page.url();
  if (/\/login|\/uas\/login/i.test(url)) return { status: "blocked", reason: "login required" };
  if (/\/checkpoint/i.test(url)) return { status: "blocked", reason: "checkpoint present" };
  return { status: "ok", reason: null };
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

async function getComposer(page) {
  const dialogs = page.locator(COMPOSER_DIALOG_SELECTOR);
  const count = await dialogs.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    const dialog = dialogs.nth(index);
    const label = await dialog.getAttribute("aria-label").catch(() => null);
    if (!label) continue;
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
    if (rendered) {
      return { dialog, label, recipient: label.replace(/^Conversation with /, "").trim() };
    }
  }
  return null;
}

async function countExistingMessages(dialog) {
  // A fresh first-contact composer has NO prior messages. If the dialog
  // already contains message articles (outbound "Message from you" or inbound
  // replies), this is an EXISTING conversation — sending the cold pitch would
  // double-send or follow up on a thread we did not intend to touch.
  return dialog.evaluate(() => {
    const normalize = (text) => String(text || "").replace(/\s+/g, " ").trim();
    let outbound = 0;
    let inbound = 0;
    for (const el of document.querySelectorAll("[aria-label='Message from you']")) {
      const article = el.closest("article");
      const text = normalize(article ? article.innerText : el.innerText);
      if (text) outbound += 1;
    }
    // Inbound messages are articles NOT marked "Message from you" inside the
    // conversation dialog. Count distinct message articles as a proxy.
    const articles = Array.from(document.querySelectorAll("section[role='dialog'][aria-label^='Conversation with '] article"));
    const inboundArticles = articles.filter((a) => {
      const text = normalize(a.innerText);
      return text.length > 0 && !a.querySelector("[aria-label='Message from you']");
    });
    inbound = inboundArticles.length;
    return { outbound, inbound, total: outbound + inbound };
  });
}

// --- main ---
const lead = state.lead || {};
const expectedName = lead.name || lead.first || state.leadName || null;
const expectedFirst = lead.first || firstToken(expectedName);
const profileUrl =
  lead.profileUrl || state.profileUrl || (state.composer && state.composer.profileUrl) || null;
const subject = lead.subject || state.messageSubject || null;
const messageBody = lead.message || state.messageBody || null;

if (!expectedName) {
  state.fill = {
    ...baseReport(null, profileUrl),
    status: "error",
    reason: "expected lead name missing (state.lead.name or state.leadName)",
  };
  console.log(JSON.stringify(state.fill));
} else if (!subject || !messageBody) {
  state.fill = {
    ...baseReport(expectedName, profileUrl),
    status: "error",
    reason: "subject or message body missing (state.lead.subject / state.lead.message)",
  };
  console.log(JSON.stringify(state.fill));
} else {
  const report = baseReport(expectedName, profileUrl);

  try {
    const page = state.composerPage && !state.composerPage.isClosed()
      ? state.composerPage
      : context.pages().find((p) => p.url().includes("linkedin.com")) || page;
    state.composerPage = page;

    const block = await classifyBlock(page);
    if (block.status !== "ok") {
      report.status = block.status;
      report.reason = block.reason;
    } else {
      const composer = await getComposer(page);
      if (!composer) {
        report.status = "no-composer";
        report.reason = "no visible conversation composer found on the page";
      } else {
        report.composerRecipient = composer.recipient;
        report.verified = recipientMatches(expectedName, composer.recipient);

        if (!report.verified) {
          report.status = "recipient-mismatch";
          report.reason = `composer recipient "${composer.recipient}" does not match expected "${expectedName}"`;
        } else {
          // Existing-conversation guard: a fresh cold-DM composer has zero
          // prior messages. If the dialog already holds a thread (we messaged
          // them before, or they replied), refuse to fill the pitch unless
          // explicitly overridden.
          const existing = await countExistingMessages(composer.dialog);
          report.existingConversation = existing;
          // Override is allowed ONLY for a declared follow-up message. An
          // opening message must never fill an existing thread.
          const sendMode = state.sendMode || (state.fillStep && state.fillStep.sendMode) || "opening";
          const isFollowup = sendMode === "followup";
          if (existing.total > 0 && !isFollowup) {
            report.status = "existing-conversation";
            report.reason =
              `composer for "${composer.recipient}" already has ${existing.total} message(s) ` +
              `(${existing.outbound} outbound, ${existing.inbound} inbound) — refusing to fill the opening message; ` +
              "set state.sendMode='followup' only for a follow-up send";
          } else if (existing.total > 0 && isFollowup) {
            report.followup = true;
            report.status = "continuing-existing-conversation";
            report.reason = `follow-up into existing conversation (${existing.total} prior message(s)); filling`;
          }
          if (report.status !== "recipient-mismatch" && report.status !== "existing-conversation") {
            // Fill subject
            const subjectField = composer.dialog.locator(SUBJECT_SELECTOR).first();
            if ((await subjectField.count().catch(() => 0)) > 0) {
              await subjectField.fill(subject, { timeout: 8000 });
            } else {
              report.status = "error";
              report.reason = "subject field not found in composer";
            }

          if (report.status !== "error") {
            // Fill message body
            const messageField = composer.dialog.locator(MESSAGE_SELECTOR).first();
            if ((await messageField.count().catch(() => 0)) > 0) {
              await messageField.fill(messageBody, { timeout: 8000 });
              const actualSubject = await subjectField.inputValue().catch(() => "");
              const actualBody = await messageField.inputValue().catch(() => "");
              report.status = "filled";
              report.subject = actualSubject;
              report.subjectLength = actualSubject.length;
              report.body = actualBody;
              report.bodyLength = actualBody.length;
              // Sales Navigator auto-appends the saved signature once the body
              // has content. Detect it so the caller knows whether to expect a
              // signature and can avoid duplicating one in the body.
              const sigPresent = await composer.dialog
                .locator("[class*='compose-signature']")
                .count()
                .then((n) => n > 0)
                .catch(() => false);
              report.autoSignature = sigPresent;
              report.reason =
                actualSubject === subject && actualBody === messageBody
                  ? null
                  : "filled but readback differs from expected input";
            } else {
              report.status = "error";
              report.reason = "message textarea not found in composer";
            }
          }
        }
      }
    }
    }
  } catch (error) {
    report.status = "error";
    report.reason = `${error.name || "Error"}: ${error.message}`.slice(0, 300);
  }

  state.fill = report;
  console.log(JSON.stringify(report));
}
