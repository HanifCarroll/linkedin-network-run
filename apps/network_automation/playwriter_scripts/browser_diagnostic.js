const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const SECURITY_VERIFICATION_SELECTOR =
  "iframe#humanThirdPartyIframe,iframe[title='LinkedIn security verification'],iframe[src*='li.protechts.net']";

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

async function visibleCount(activePage, selector) {
  const locator = activePage.locator(selector);
  const count = await locator.count().catch(() => 0);
  let visible = 0;
  for (let index = 0; index < count; index += 1) {
    if (await locator.nth(index).isVisible().catch(() => false)) visible += 1;
  }
  return visible;
}

async function pageClassification(activePage) {
  const url = activePage.url();
  if (/\/login|\/uas\/login/i.test(url)) return "login_required";
  if (/\/checkpoint/i.test(url)) return "checkpoint_present";
  if ((await visibleCount(activePage, "input[name='session_key'], form[action*='/uas/login']")) > 0) {
    return "login_required";
  }
  if ((await visibleCount(activePage, "input[name='pin'], input[name='challengeId']")) > 0) {
    return "checkpoint_present";
  }
  if ((await visibleCount(activePage, SECURITY_VERIFICATION_SELECTOR)) > 0) {
    return "security_verification_present";
  }
  return "ordinary_page";
}

async function dialogEvidence(activePage) {
  return activePage
    .locator("[role='dialog'],[role='alertdialog'],[role='alert']")
    .evaluateAll((elements) =>
      elements.map((element, index) => ({
        index,
        role: element.getAttribute("role"),
        ariaLabel: element.getAttribute("aria-label"),
        text: String(element.textContent || "").replace(/\s+/g, " ").trim(),
      })),
    )
    .catch(() => []);
}

async function main() {
  const pages = context.pages();
  const activePage =
    (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed() && state.linkedinToolsPage) ||
    pages.find((item) => item.url().includes("linkedin.com")) ||
    page;
  if (!activePage) throw new Error("no browser page available for diagnostic capture");

  const probes = {
    salesNavRows: await visibleCount(
      activePage,
      "li.artdeco-list__item:has(a[href*='/sales/lead/'])",
    ),
    salesNavActionButtons: await visibleCount(
      activePage,
      "button[aria-label^='See more actions for']",
    ),
    sentInvitationControls: await visibleCount(
      activePage,
      "[aria-label^='Withdraw invitation sent to']",
    ),
    loginControls: await visibleCount(
      activePage,
      "input[name='session_key'], form[action*='/uas/login']",
    ),
    checkpointControls: await visibleCount(
      activePage,
      "input[name='pin'], input[name='challengeId']",
    ),
    securityFrames: await visibleCount(activePage, SECURITY_VERIFICATION_SELECTOR),
  };

  let screenshotPath = null;
  let screenshotError = null;
  if (config.screenshotOut) {
    try {
      await activePage.screenshot({ path: config.screenshotOut, fullPage: true });
      screenshotPath = config.screenshotOut;
    } catch (error) {
      screenshotError = String(error && error.message ? error.message : error);
    }
  }

  const artifact = {
    capturedAt: new Date().toISOString(),
    operation: String(config.operation || "unknown"),
    expectedUrl: config.expectedUrl || null,
    currentUrl: activePage.url(),
    title: clean(await activePage.title().catch(() => "")) || null,
    pageClassification: await pageClassification(activePage),
    tabs: await Promise.all(
      pages.map(async (item, index) => ({
        index,
        url: item.url(),
        title: clean(await item.title().catch(() => "")) || null,
        owned: item === state.linkedinToolsPage,
      })),
    ),
    probes,
    dialogs: await dialogEvidence(activePage),
    screenshotPath,
    screenshotError,
    error: String(config.error || "unknown browser error"),
  };
  const tmp = `${config.out}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(artifact, null, 2)}\n`);
  fs.renameSync(tmp, config.out);
  console.log(`wrote browser diagnostic to ${config.out}`);
}

await main();
