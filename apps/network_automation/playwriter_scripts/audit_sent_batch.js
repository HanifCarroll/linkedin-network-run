// End-of-batch audit: compare the Sales Navigator inbox against the expected
// sends, and report which message landed in which thread.
//
// Reads the SN inbox list once (each row = one thread: "<Recipient> <preview>"),
// maps every thread's recipient + message preview, then classifies each
// expected send as correct / wrong-recipient / not-found. The summary lists
// wrong-recipient and not-found cases with the recipient's profile URL so the
// user can open the composers and delete mistaken messages.
//
// Inputs (state):
//   state.sentBatchPath - path to JSON array of expected sends, each:
//       { name, first, profileUrl, subject, message }
//   or state.sentBatch  - same array inline.
//   state.inboxUrl      - optional; defaults to https://www.linkedin.com/sales/inbox
//
// Outputs (state):
//   state.audit = {
//     status: "audit-complete" | "inbox-unreadable" | "error",
//     expectedCount, inboxThreadCount,
//     correct: [...], wrongRecipient: [...], notFound: [...],
//     summary: "sent N correct; M wrong-recipient; K not found",
//     cleanup: [ { expectedName, expectedFirst, foundInThread, profileUrl } ],
//     at
//   }

const fs = require("node:fs");

function nowIso() {
  return new Date().toISOString();
}

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function firstToken(value) {
  return String(value || "").trim().split(/\s+/)[0] || "";
}

function namesMatch(expected, actual) {
  if (!expected || !actual) return false;
  return firstToken(expected).toLowerCase() === firstToken(actual).toLowerCase();
}

async function scanInboxList(page) {
  // Each row renders as "<Recipient Name> <message preview> <timestamp>".
  // Extract recipient + preview per thread.
  return page.evaluate(() => {
    const normalize = (text) => String(text || "").replace(/\s+/g, " ").trim();
    const links = Array.from(document.querySelectorAll("a[href*='/sales/inbox/']"));
    const rows = [];
    for (const link of links) {
      const text = normalize(link.innerText);
      if (!text) continue;
      // The row is "<Recipient Name> <message preview>". The preview may start
      // with our outbound "Hey <First>," OR an inbound reply ("Hi Hanif, ...").
      // Parse the recipient as the leading name segment in either case.
      const previewMatch = text.match(/^(.*?)\s+(Hey [A-Za-z].{0,80}|Hi Hanif.{0,80})/);
      let recipient = previewMatch ? previewMatch[1].trim() : null;
      if (recipient) {
        // Strip activity markers like "was last active 3 hours ago".
        recipient = recipient.replace(/was last active .*$/i, "").trim();
      }
      rows.push({
        href: link.getAttribute("href"),
        text,
        recipient: recipient,
        preview: previewMatch ? previewMatch[2].trim() : text.slice(0, 120),
      });
    }
    return rows;
  });
}

async function main() {
  const report = {
    status: "error",
    expectedCount: null,
    inboxThreadCount: null,
    correct: [],
    wrongRecipient: [],
    notFound: [],
    replies: [],
    summary: null,
    cleanup: [],
    at: nowIso(),
  };

  let expectedSends = state.sentBatch || null;
  if (!expectedSends && state.sentBatchPath) {
    try {
      expectedSends = JSON.parse(fs.readFileSync(state.sentBatchPath, "utf8"));
    } catch (error) {
      report.reason = `cannot read sentBatchPath: ${error.message}`;
      state.audit = report;
      console.log(JSON.stringify(report));
      return;
    }
  }
  if (!Array.isArray(expectedSends) || expectedSends.length === 0) {
    report.reason = "expected sends missing (state.sentBatch or state.sentBatchPath)";
    state.audit = report;
    console.log(JSON.stringify(report));
    return;
  }

  try {
    const page = state.composerPage && !state.composerPage.isClosed()
      ? state.composerPage
      : context.pages().find((p) => p.url().includes("linkedin.com")) || page;
    state.composerPage = page;

    const inboxUrl = state.inboxUrl || "https://www.linkedin.com/sales/inbox";
    await page.goto(inboxUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
    // The inbox is an SPA; poll until conversation rows render.
    let inboxRows = [];
    for (let attempt = 0; attempt < 12; attempt += 1) {
      await page.waitForTimeout(1500);
      inboxRows = await scanInboxList(page);
      if (inboxRows.length > 0) break;
    }

    if (inboxRows.length === 0) {
      report.status = "inbox-unreadable";
      report.reason = "no conversation rows found on the Sales Navigator inbox";
      state.audit = report;
      console.log(JSON.stringify(report));
      return;
    }

    report.inboxThreadCount = inboxRows.length;
    report.expectedCount = expectedSends.length;

    // For each expected send, find the thread(s) containing its message.
    for (const send of expectedSends) {
      const expectedName = send.name;
      const expectedFirst = send.first || firstToken(expectedName);
      const expectedBody = clean(send.message || "");
      const expectedPreview = `Hey ${expectedFirst}`;
      const expectedKey = `${expectedFirst.toLowerCase()}|${expectedBody.slice(0, 60)}`;

      // Which inbox threads contain this exact message? The greeting is the
      // discriminator: batch messages share the same body except for
      // "Hey <First>", so a body-prefix match alone is ambiguous. Require the
      // preview greeting to match the expected first name.
      const containing = inboxRows.filter((row) => {
        if (!row.preview) return false;
        const previewFirst = firstToken(row.preview.replace(/^Hey /, ""))
          .replace(/[,;:!?.]+$/, "");
        const greetingOk = previewFirst.toLowerCase() === expectedFirst.toLowerCase();
        const bodyOk = row.text.includes(expectedBody.slice(0, 60));
        return greetingOk && bodyOk;
      });

      // Any thread matching the recipient name at all (regardless of content)?
      const recipientThreads = inboxRows.filter((row) =>
        namesMatch(expectedName, row.recipient),
      );

      if (containing.length > 0) {
        const inRightThread = containing.some((row) =>
          namesMatch(expectedName, row.recipient),
        );
        if (inRightThread) {
          report.correct.push({
            name: expectedName,
            recipient: containing.find((r) => namesMatch(expectedName, r.recipient)).recipient,
            href: containing.find((r) => namesMatch(expectedName, r.recipient)).href,
          });
        } else {
          // The expected message is in a thread for someone else.
          const wrong = containing[0];
          report.wrongRecipient.push({
            name: expectedName,
            expectedIn: expectedName,
            foundInThread: wrong.recipient,
            href: wrong.href,
            preview: wrong.preview.slice(0, 80),
          });
          report.cleanup.push({
            expectedName,
            expectedFirst,
            foundInThread: wrong.recipient,
            profileUrl: send.profileUrl || null,
            action: "open the foundInThread composer and delete the message",
          });
        }
      } else if (recipientThreads.length > 0) {
        // The recipient has a thread, but our message is not in the row preview.
        // The preview shows the LATEST message; if the recipient replied, the
        // inbound reply supersedes our outbound text. Treat an inbound reply in
        // the recipient's thread as confirmation the message was received.
        const row = recipientThreads[0];
        const text = row.text || "";
        const isReply = /(Hi Hanif|Hanif,)/i.test(text) || /^Hi\s/i.test(text);
        if (isReply) {
          report.correct.push({
            name: expectedName,
            recipient: row.recipient,
            href: row.href,
            replied: true,
            replyPreview: row.preview.slice(0, 80),
          });
          const tone = /not interested|not a fit|no thanks|unsubscribe/i.test(text)
            ? "declined"
            : /interested|sure|let's|let us|happy to|great/i.test(text)
              ? "positive"
              : "reply";
          (report.replies = report.replies || []).push({
            name: expectedName,
            recipient: row.recipient,
            href: row.href,
            tone,
            preview: row.preview.slice(0, 100),
          });
        } else {
          report.notFound.push({
            name: expectedName,
            recipient: row.recipient,
            href: row.href,
            preview: row.preview.slice(0, 80),
          });
          report.cleanup.push({
            expectedName,
            expectedFirst,
            foundInThread: row.recipient,
            profileUrl: send.profileUrl || null,
            action: "expected message not found in inbox; check whether it sent",
          });
        }
      } else {
        report.notFound.push({ name: expectedName, recipient: null, href: null, preview: null });
        report.cleanup.push({
          expectedName,
          expectedFirst,
          foundInThread: null,
          profileUrl: send.profileUrl || null,
          action: "no thread for this recipient in the inbox; verify the send",
        });
      }
    }

    // Orphan scan: any inbox thread whose message preview greets a DIFFERENT
    // first name than the thread's recipient means a message went to the wrong
    // person (e.g., fill bug filled the wrong composer). Flag for cleanup.
    const orphans = inboxRows.filter((row) => {
      if (!row.preview || !row.recipient) return false;
      // Only OUTBOUND messages can be misrouted. Inbound replies start with
      // "Hi Hanif" / the recipient's name and are not orphan candidates.
      if (!row.preview.startsWith("Hey ")) return false;
      const previewFirst = firstToken(row.preview.replace(/^Hey /, ""))
        .replace(/[,;:!?.]+$/, "").toLowerCase();
      const recipientFirst = firstToken(row.recipient).toLowerCase();
      return previewFirst !== recipientFirst;
    });
    for (const orphan of orphans) {
      report.wrongRecipient.push({
        name: orphan.recipient,
        expectedIn: orphan.recipient,
        foundInThread: orphan.recipient,
        messageGreets: firstToken(orphan.preview.replace(/^Hey /, "")).replace(/[,;:!?.]+$/, ""),
        href: orphan.href,
        preview: orphan.preview.slice(0, 80),
      });
      report.cleanup.push({
        expectedName: orphan.recipient,
        expectedFirst: firstToken(orphan.recipient),
        foundInThread: orphan.recipient,
        profileUrl: null,
        action: `open the ${orphan.recipient} composer and delete the message that greets "${firstToken(orphan.preview.replace(/^Hey /, "")).replace(/[,;:!?.]+$/, "")}"`,
      });
    }

    report.summary =
      `sent ${report.correct.length} correct; ` +
      `${report.wrongRecipient.length} wrong-recipient; ` +
      `${report.notFound.length} not found`;
    report.status = "audit-complete";
  } catch (error) {
    report.status = "error";
    report.reason = `${error.name || "Error"}: ${error.message}`.slice(0, 300);
  }

  state.audit = report;
  console.log(JSON.stringify(report));
}

(async () => {
  await main();
})();
