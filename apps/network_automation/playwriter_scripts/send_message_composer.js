// Send the filled composer message for one lead, with immediate in-thread
// confirmation that the message landed in the EXPECTED recipient's dialog.
// Step 3 of the manual DM pipeline. Requires an already-filled composer on
// state.composerPage (from fill_message_composer.js) AND explicit send
// authorization: state.allowSend === true (or state.sendStep.allowSend).
//
// The durable Sales Navigator inbox audit (which message landed in which
// thread) is a separate end-of-batch step: audit_sent_batch.js.
//
// Inputs (state):
//   state.composerPage   - page holding the open, filled composer
//   state.allowSend      - MUST be true to send (hard gate)
//   state.lead           - { name, first, profileUrl, subject, message }
//
// Outputs (state):
//   state.send = {
//     status: "sent-confirmed" | "blocked" | "send-button-missing" |
//              "send-uncertain" | "no-composer" | "error",
//     expectedName, composerRecipient, verified,
//     sentMessageFound, sentThreadRecipient, recipientVerifiedAfterSend,
//     subject, bodyLength, reason?, confirmationState?
//   }

function nowIso() {
  return new Date().toISOString();
}

const COMPOSER_DIALOG_SELECTOR =
  "section[role='dialog'][aria-label^='Conversation with ']";
const OUTBOUND_MESSAGE_MARKER = "[aria-label='Message from you']";

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function baseReport(expectedName) {
  return {
    status: "unknown",
    expectedName,
    composerRecipient: null,
    verified: false,
    subject: null,
    bodyLength: null,
    sentMessageFound: false,
    reason: null,
    at: nowIso(),
  };
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

async function findSendButton(dialog) {
  const actions = dialog.locator("button,a,[role='button']");
  const count = await actions.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    const item = actions.nth(index);
    const text = clean(await item.textContent().catch(() => ""));
    const aria = clean(await item.getAttribute("aria-label").catch(() => ""));
    if (/^(Send|Send message)$/i.test(text) || /^(Send|Send message)$/i.test(aria)) {
      const disabled =
        (await item.isDisabled().catch(() => false)) ||
        (await item.getAttribute("aria-disabled").catch(() => null)) === "true";
      return { item, disabled, label: text || aria };
    }
  }
  return null;
}

function firstToken(value) {
  return String(value || "").trim().split(/\s+/)[0] || "";
}

function namesMatch(expected, actual) {
  if (!expected || !actual) return false;
  return firstToken(expected).toLowerCase() === firstToken(actual).toLowerCase();
}

async function scanConversationThreads(page) {
  // Scan open conversation dialogs on the current page (post-send state).
  return page.evaluate((marker) => {
    const normalize = (text) => String(text || "").replace(/\s+/g, " ").trim();
    const roots = Array.from(document.querySelectorAll("section[role='dialog'][aria-label^='Conversation with ']"));
    const threads = [];
    for (const root of roots) {
      const recipient = normalize(root.getAttribute("aria-label") || "")
        .replace(/^Conversation with /, "")
        .trim();
      const outbound = Array.from(root.querySelectorAll(marker));
      const bodies = [];
      for (const el of outbound) {
        const article = el.closest("article");
        const text = normalize(article ? article.innerText : el.innerText);
        if (text) bodies.push(text);
      }
      threads.push({ recipient, outboundCount: bodies.length, bodies });
    }
    return threads;
  }, OUTBOUND_MESSAGE_MARKER);
}

async function confirmOutboundMessage(page, expectedName, expectedBody, timeoutMs) {
  // Send-time verification: confirm the message appeared as an outbound
  // message in the EXPECTED recipient's conversation dialog on this page.
  // This is fast and immediate. The durable inbox audit happens once at the
  // end of the batch in audit_sent_batch.js.
  const deadline = Date.now() + timeoutMs;
  let lastState = null;
  while (Date.now() < deadline) {
    try {
      const threads = await scanConversationThreads(page);
      const expectedNorm = clean(expectedBody);
      const matchedThreads = threads
        .filter((t) => t.bodies.some((text) => text.includes(expectedNorm.slice(0, 120))))
        .map((t) => ({ recipient: t.recipient, outboundCount: t.outboundCount }));
      lastState = { threadMatches: matchedThreads };
      const inExpected = matchedThreads.filter((t) => namesMatch(expectedName, t.recipient));
      if (inExpected.length > 0) {
        return { confirmed: true, recipient: inExpected[0].recipient, source: "dialog", state: lastState };
      }
      if (matchedThreads.length > 0) {
        return {
          confirmed: false,
          recipient: null,
          mismatchedRecipients: matchedThreads.map((t) => t.recipient),
          state: lastState,
        };
      }
    } catch (error) {
      lastState = { error: error.message };
    }
    await page.waitForTimeout(500);
  }
  return { confirmed: false, recipient: null, state: lastState };
}

// --- main ---
(async () => {
const allowSend = state.allowSend === true || (state.sendStep && state.sendStep.allowSend === true);
const lead = state.lead || {};
const expectedName = lead.name || state.leadName || null;
const expectedBody = lead.message || state.messageBody || null;
const subject = lead.subject || state.messageSubject || null;

const report = baseReport(expectedName);

if (!expectedName || !expectedBody) {
  report.status = "error";
  report.reason = "lead name or message body missing";
  state.send = report;
  console.log(JSON.stringify(report));
} else if (!allowSend) {
  report.status = "blocked";
  report.reason = "real send requires state.allowSend === true";
  state.send = report;
  console.log(JSON.stringify(report));
} else {
  try {
    const page = state.composerPage && !state.composerPage.isClosed()
      ? state.composerPage
      : context.pages().find((p) => p.url().includes("linkedin.com")) || page;
    state.composerPage = page;

    const composer = await getComposer(page);
    if (!composer) {
      report.status = "no-composer";
      report.reason = "no visible conversation composer found on the page";
    } else {
      report.composerRecipient = composer.recipient;
      report.verified = clean(expectedName).toLowerCase().split(/\s+/)[0] ===
        composer.recipient.toLowerCase().split(/\s+/)[0];
      if (!report.verified) {
        report.status = "blocked";
        report.reason = `composer recipient "${composer.recipient}" does not match expected "${expectedName}" — refusing to send`;
      } else {
        const subjectField = composer.dialog.locator("input[placeholder*='Subject'], input[aria-label*='Subject']").first();
        report.subject = (await subjectField.inputValue().catch(() => "")) || subject || null;
        const msgField = composer.dialog.locator("textarea[name='message']").first();
        const body = await msgField.inputValue().catch(() => "");
        report.bodyLength = body.length;

        const sendBtn = await findSendButton(composer.dialog);
        if (!sendBtn) {
          report.status = "send-button-missing";
          report.reason = "Send button not found in composer";
        } else if (sendBtn.disabled) {
          report.status = "blocked";
          report.reason = "Send button is disabled — composer may be empty or invalid";
        } else {
          await sendBtn.item.click({ timeout: 8000 }).catch(async () => {
            await sendBtn.item.evaluate((el) => el.click());
          });
          report.sendClicked = true;
          const confirmation = await confirmOutboundMessage(page, expectedName, expectedBody, 15000);
          report.sentMessageFound = confirmation.confirmed;
          report.sentThreadRecipient = confirmation.confirmed ? confirmation.recipient : null;
          report.recipientVerifiedAfterSend = confirmation.confirmed;
          if (confirmation.confirmed) {
            report.status = "sent-confirmed";
          } else if (confirmation.mismatchedRecipients && confirmation.mismatchedRecipients.length) {
            report.status = "send-uncertain";
            report.reason = `Send was clicked; matching message found in thread(s) for: ${confirmation.mismatchedRecipients.join(", ")} — expected ${expectedName}`;
            report.confirmationState = confirmation.state;
          } else {
            report.status = "send-uncertain";
            report.reason = "Send was clicked but outbound message confirmation was not observed";
            report.confirmationState = confirmation.state;
          }
        }
      }
    }
  } catch (error) {
    report.status = "error";
    report.reason = `${error.name || "Error"}: ${error.message}`.slice(0, 300);
  }
  state.send = report;
  console.log(JSON.stringify(report));
}

})();
