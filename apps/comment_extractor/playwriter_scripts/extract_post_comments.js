const fs = require("node:fs");
const path = require("node:path");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const input = config.input || {};
const limits = config.limits || {};
const selectors = config.selectors || {};

const maxScrolls = Number(limits.max_scrolls || limits.maxScrolls || 6);
const maxCommentClicks = Number(limits.max_comment_control_clicks || limits.maxCommentControlClicks || 12);
const maxReplyClicks = Number(limits.max_reply_control_clicks || limits.maxReplyControlClicks || 8);
const navigationTimeoutMs = Number(limits.navigation_timeout_ms || limits.navigationTimeoutMs || 30000);
const actionTimeoutMs = Number(limits.action_timeout_ms || limits.actionTimeoutMs || 5000);
const settleMs = Number(limits.settle_ms || limits.settleMs || 750);
const maxRuntimeSeconds = Number(limits.max_runtime_seconds || limits.maxRuntimeSeconds || 90);
const maxNoProgressPasses = Math.max(
  1,
  Number(limits.max_no_progress_passes || limits.maxNoProgressPasses || 2)
);

const moreCommentsPattern = /^(load|show|view|see) (more|previous) comments?$/i;
const moreRepliesPattern = /^(load|show|view|see) (more|previous)? ?repl(?:y|ies)$/i;
const scrollProgressThresholdPx = 8;

function nowIso() {
  return new Date().toISOString();
}

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function writeArtifact(payload) {
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((candidate) => candidate.url().includes("linkedin.com/posts/")) ||
    pages.find((candidate) => candidate.url().includes("linkedin.com/feed/update/")) ||
    pages.find((candidate) => candidate.url().includes("linkedin.com")) ||
    pages.find((candidate) => candidate.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
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

async function classifyBlocker(page) {
  const url = page.url();
  const login = await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']");
  const checkpoint = await visibleCount(page, "input[name='pin'], input[name='challengeId']");
  const security = await visibleCount(
    page,
    "iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']"
  );
  if (/\/login|\/uas\/login/i.test(url) || login > 0) return "login_required";
  if (/\/checkpoint/i.test(url) || checkpoint > 0) return "checkpoint_present";
  if (security > 0) return "security_verification_present";
  return "";
}

async function countComments(page) {
  return page.locator(selectors.commentRoot).count().catch(() => 0);
}

async function scrollState(page) {
  return page.evaluate(() => {
    const root = document.documentElement;
    const body = document.body;
    return {
      scrollY: window.scrollY || window.pageYOffset || 0,
      scrollHeight: Math.max(root?.scrollHeight || 0, body?.scrollHeight || 0),
      innerHeight: window.innerHeight || root?.clientHeight || 0,
    };
  }).catch(() => ({ scrollY: 0, scrollHeight: 0, innerHeight: 0 }));
}

function scrollChanged(previous, current) {
  return (
    Math.abs(Number(current.scrollY || 0) - Number(previous.scrollY || 0)) >= scrollProgressThresholdPx ||
    Math.abs(Number(current.scrollHeight || 0) - Number(previous.scrollHeight || 0)) >= scrollProgressThresholdPx
  );
}

async function clickControls(page, pattern, limit) {
  if (limit <= 0) return 0;
  const locator = page.getByRole("button", { name: pattern });
  const count = Math.min(await locator.count().catch(() => 0), limit);
  let clicked = 0;
  for (let index = 0; index < count; index += 1) {
    const button = locator.nth(index);
    const visible = await button.isVisible().catch(() => false);
    const enabled = await button.isEnabled().catch(() => false);
    if (visible && enabled) {
      await button.click({ timeout: actionTimeoutMs });
      clicked += 1;
      await page.waitForTimeout(250);
    }
  }
  return clicked;
}

async function expandComments(page) {
  const started = Date.now();
  const deadline = started + maxRuntimeSeconds * 1000;
  let remainingCommentClicks = Math.max(0, maxCommentClicks);
  let remainingReplyClicks = Math.max(0, maxReplyClicks);
  let commentClicks = 0;
  let replyClicks = 0;
  let scrolls = 0;
  let noProgressPasses = 0;
  let stopReason = "max_scrolls_reached";
  let previousComments = await countComments(page);
  let previousScroll = await scrollState(page);
  let visibleComments = previousComments;

  for (let passNumber = 1; passNumber <= Math.max(0, maxScrolls); passNumber += 1) {
    if (Date.now() >= deadline) {
      stopReason = "max_runtime_reached";
      break;
    }
    let commentClicked = 0;
    let replyClicked = 0;
    try {
      commentClicked = await clickControls(page, moreCommentsPattern, remainingCommentClicks);
      remainingCommentClicks -= commentClicked;
      commentClicks += commentClicked;
      replyClicked = await clickControls(page, moreRepliesPattern, remainingReplyClicks);
      remainingReplyClicks -= replyClicked;
      replyClicks += replyClicked;
      await page.evaluate(() => window.scrollBy(0, 1800));
      scrolls += 1;
      if (settleMs > 0) await page.waitForTimeout(settleMs);
    } catch (error) {
      stopReason = "action_timeout";
      break;
    }

    visibleComments = await countComments(page);
    const currentScroll = await scrollState(page);
    const newComments = Math.max(0, visibleComments - previousComments);
    const changed = scrollChanged(previousScroll, currentScroll);
    noProgressPasses =
      newComments > 0 || commentClicked > 0 || replyClicked > 0 || changed
        ? 0
        : noProgressPasses + 1;
    previousComments = visibleComments;
    previousScroll = currentScroll;
    if (Date.now() >= deadline) {
      stopReason = "max_runtime_reached";
      break;
    }
    if (noProgressPasses >= maxNoProgressPasses) {
      stopReason = "no_more_content";
      break;
    }
  }

  return {
    stop_reason: stopReason,
    scrolls_performed: scrolls,
    comment_control_clicks: commentClicks,
    reply_control_clicks: replyClicks,
    visible_comment_nodes: visibleComments,
    runtime_seconds: Number(((Date.now() - started) / 1000).toFixed(3)),
    no_progress_passes: noProgressPasses,
    max_no_progress_passes: maxNoProgressPasses,
  };
}

async function extractRows(page) {
  return page.locator(selectors.commentRoot).evaluateAll(
    (nodes, passedSelectors) => {
      const cleanText = (value) => (value || "").replace(/\s+/g, " ").trim();
      const nodeText = (node) => {
        if (!node) return "";
        const parts = [];
        const visit = (current) => {
          if (current.nodeType === Node.TEXT_NODE) {
            parts.push(current.nodeValue || "");
            return;
          }
          if (current.nodeType !== Node.ELEMENT_NODE) return;
          if (current.tagName === "BR") {
            parts.push("\n");
            return;
          }
          current.childNodes.forEach(visit);
        };
        visit(node);
        return cleanText(parts.join(""));
      };
      const selectText = (node, selector) => nodeText(node.querySelector(selector));
      const [profileSelector, textSelector, nameSelector, headlineSelector] = passedSelectors;
      return nodes.map((node) => {
        const profile = node.querySelector(profileSelector);
        const time = node.querySelector("time");
        const name = selectText(node, nameSelector) || cleanText(profile?.textContent);
        return {
          comment_id: node.getAttribute("data-id") || node.getAttribute("componentkey") || "",
          commenter_headline: selectText(node, headlineSelector),
          commenter_name: name,
          commenter_profile_url: profile?.getAttribute("href") || "",
          comment_text: selectText(node, textSelector),
          commented_at: time?.getAttribute("datetime") || cleanText(time?.textContent),
        };
      });
    },
    [
      selectors.commentProfile,
      selectors.commentText,
      selectors.commentName,
      selectors.commentHeadline,
    ]
  );
}

async function extractPostMetadata(page) {
  return page.evaluate((passedSelectors) => {
    const cleanText = (value) => (value || "").replace(/\s+/g, " ").trim();
    const nodeText = (node) => {
      if (!node) return "";
      const parts = [];
      const visit = (current) => {
        if (current.nodeType === Node.TEXT_NODE) {
          parts.push(current.nodeValue || "");
          return;
        }
        if (current.nodeType !== Node.ELEMENT_NODE) return;
        if (current.tagName === "BR") {
          parts.push("\n");
          return;
        }
        current.childNodes.forEach(visit);
      };
      visit(node);
      return cleanText(parts.join(""));
    };
    const [authorSelector, textSelector] = passedSelectors;
    return {
      author_name: nodeText(document.querySelector(authorSelector)),
      text: nodeText(document.querySelector(textSelector)),
    };
  }, [selectors.postAuthor, selectors.postText]);
}

async function main() {
  fs.mkdirSync(config.runDir, { recursive: true });
  const page = await getPage();
  const htmlPath = path.join(config.runDir, "post.html");
  const screenshotPath = path.join(config.runDir, "post-comments.png");
  const warnings = [];

  if (!input.post_url) {
    writeArtifact({
      status: "blocked",
      blocker: "missing_post_url",
      stop_reason: "missing_post_url",
      warnings,
    });
    return;
  }

  try {
    await page.goto(input.post_url, { waitUntil: "domcontentloaded", timeout: navigationTimeoutMs });
    await waitForPageLoad({ page, timeout: Math.min(navigationTimeoutMs, 10000) }).catch(() => null);
  } catch (error) {
    writeArtifact({
      status: "blocked",
      blocker: "navigation_failed",
      stop_reason: "navigation_timeout",
      error: String(error && error.message ? error.message : error),
      warnings,
    });
    return;
  }

  const blocker = await classifyBlocker(page);
  if (blocker) {
    writeArtifact({
      status: "blocked",
      blocker,
      stop_reason: blocker,
      url: page.url(),
      warnings,
    });
    return;
  }

  const expansion = await expandComments(page);
  const html = await page.content();
  fs.writeFileSync(htmlPath, html);
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch((error) => {
    warnings.push(`screenshot_capture_failed:${clean(error && error.name ? error.name : "Error")}`);
  });
  const rows = await extractRows(page);
  const postMetadata = await extractPostMetadata(page);
  if (!Array.isArray(rows) || rows.length === 0) warnings.push("no_live_linkedin_comment_nodes_found");

  writeArtifact({
    status: "extracted",
    captured_at: nowIso(),
    url: page.url(),
    html_path: htmlPath,
    screenshot_path: fs.existsSync(screenshotPath) ? screenshotPath : "",
    post_metadata: postMetadata,
    rows,
    expansion,
    warnings,
  });
}

main().catch((error) => {
  writeArtifact({
    status: "failed",
    stop_reason: "playwriter_script_failed",
    error: String(error && error.stack ? error.stack : error),
    warnings: [],
  });
  throw error;
});
