const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const limits = config.limits || {};
const captureUrl = String(config.captureUrl || "");
const maxResults = Number(limits.max_results_per_search || limits.maxResultsPerSearch || 50);
const maxScrolls = Number(limits.max_scrolls || limits.maxScrolls || 20);
const scrollPixels = Number(limits.scroll_pixels || limits.scrollPixels || 1800);
const navigationTimeoutMs = Number(limits.navigation_timeout_ms || limits.navigationTimeoutMs || 30000);
const actionTimeoutMs = Number(limits.action_timeout_ms || limits.actionTimeoutMs || 5000);
const settleMs = Number(limits.settle_ms || limits.settleMs || 1000);

const postMenuPattern = /open control menu for post by/i;
const copyLinkPattern = /^Copy link to post$/i;

function nowIso() {
  return new Date().toISOString();
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
    pages.find((candidate) => candidate.url().includes("linkedin.com/search/results/content")) ||
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

async function installCopyCapture(page) {
  await page.evaluate(() => {
    const clipboard = navigator.clipboard;
    if (!clipboard || typeof clipboard.writeText !== "function") {
      throw new Error("navigator.clipboard.writeText is unavailable");
    }
    const state = {
      writes: [],
      originalWriteText: clipboard.writeText.bind(clipboard),
    };
    Object.defineProperty(clipboard, "writeText", {
      configurable: true,
      writable: true,
      value: async (text) => {
        state.writes.push(String(text));
      },
    });
    window.__linkedinPostCopyCapture = state;
  });
}

async function readCopyCapture(page) {
  return page.evaluate(() => {
    const state = window.__linkedinPostCopyCapture;
    if (!state || !Array.isArray(state.writes) || state.writes.length === 0) return "";
    return state.writes.at(-1) || "";
  });
}

async function restoreCopyCapture(page) {
  await page.evaluate(() => {
    const state = window.__linkedinPostCopyCapture;
    if (state && state.originalWriteText && navigator.clipboard) {
      Object.defineProperty(navigator.clipboard, "writeText", {
        configurable: true,
        writable: true,
        value: state.originalWriteText,
      });
    }
    delete window.__linkedinPostCopyCapture;
  }).catch(() => null);
}

async function copyPostUrlFromMenu(page, menuButton) {
  await installCopyCapture(page);
  try {
    await menuButton.click({ timeout: 2000 }).catch(async () => {
      await menuButton.dispatchEvent("click");
    });
    await page.getByRole("menuitem", { name: copyLinkPattern }).click({ timeout: actionTimeoutMs });
    await page.waitForFunction(
      "() => window.__linkedinPostCopyCapture?.writes?.length > 0",
      { timeout: 1000 }
    );
    const copied = await readCopyCapture(page);
    if (!copied) throw new Error("LinkedIn copy action produced an empty post URL");
    return copied;
  } finally {
    await restoreCopyCapture(page);
  }
}

async function main() {
  const startedAt = nowIso();
  const page = await getPage();
  const warnings = [];
  const postUrls = [];
  const copyFailures = [];
  let processedMenuButtons = 0;
  let staleScrolls = 0;

  if (!captureUrl) {
    writeArtifact({
      status: "blocked",
      blocker: "missing_capture_url",
      capturedAt: nowIso(),
      postUrls,
      warnings,
    });
    return;
  }

  try {
    await page.goto(captureUrl, { waitUntil: "domcontentloaded", timeout: navigationTimeoutMs });
    await waitForPageLoad({ page, timeout: Math.min(navigationTimeoutMs, 10000) }).catch(() => null);
    if (settleMs > 0) await page.waitForTimeout(settleMs);
  } catch (error) {
    writeArtifact({
      status: "blocked",
      blocker: "navigation_failed",
      capturedAt: nowIso(),
      error: String(error && error.message ? error.message : error),
      postUrls,
      warnings,
    });
    return;
  }

  const blocker = await classifyBlocker(page);
  if (blocker) {
    writeArtifact({
      status: "blocked",
      blocker,
      capturedAt: nowIso(),
      url: page.url(),
      postUrls,
      warnings,
    });
    return;
  }

  for (let scrollIndex = 1; scrollIndex <= maxScrolls; scrollIndex += 1) {
    if (postUrls.length >= maxResults) break;
    const buttons = page.getByRole("button", { name: postMenuPattern });
    const buttonCount = await buttons.count().catch(() => 0);
    const before = postUrls.length;
    for (let index = processedMenuButtons; index < buttonCount; index += 1) {
      if (postUrls.length >= maxResults) break;
      try {
        const copied = await copyPostUrlFromMenu(page, buttons.nth(index));
        postUrls.push(copied);
      } catch (error) {
        copyFailures.push({
          index,
          error: String(error && error.message ? error.message : error),
        });
      }
    }
    processedMenuButtons = Math.max(processedMenuButtons, buttonCount);
    staleScrolls = postUrls.length === before ? staleScrolls + 1 : 0;
    if (staleScrolls >= 3) break;
    await page.evaluate((pixels) => window.scrollBy(0, pixels), scrollPixels);
    if (settleMs > 0) await page.waitForTimeout(settleMs);
  }

  if (copyFailures.length > 0) warnings.push("copy_failures_present");
  writeArtifact({
    status: "captured",
    startedAt,
    capturedAt: nowIso(),
    captureUrl,
    url: page.url(),
    menuButtonsProcessed: processedMenuButtons,
    postUrls,
    copyFailures,
    warnings,
  });
}

main().catch((error) => {
  writeArtifact({
    status: "failed",
    capturedAt: nowIso(),
    error: String(error && error.stack ? error.stack : error),
    postUrls: [],
    warnings: [],
  });
  throw error;
});
