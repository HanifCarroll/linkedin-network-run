const fs = require("node:fs");

const configPath = process.env.RECOVERY_CONFIG_PATH || state.linkedinToolsConfigPath;
const config = JSON.parse(fs.readFileSync(configPath, "utf8"));

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

async function main() {
  const pages = context.pages();
  const activePage =
    (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed() && state.linkedinToolsPage) ||
    pages.find((item) => item.url().includes("linkedin.com")) ||
    page;
  if (!activePage) throw new Error("no browser page available for recovery reload");

  // Reload the expected URL only (a leased recovery action). Never click
  // Connect, Send, message, save-to-list, withdraw, login, checkpoint, or
  // security-verification controls.
  await activePage.goto(config.expectedUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
  await activePage.waitForTimeout(4000);

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
    securityFrames: await visibleCount(
      activePage,
      "iframe#humanThirdPartyIframe,iframe[title='LinkedIn security verification'],iframe[src*='li.protechts.net']",
    ),
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
    operation: String(config.operation || "browser_recovery_reload"),
    expectedUrl: config.expectedUrl || null,
    currentUrl: activePage.url(),
    title: clean(await activePage.title().catch(() => "")) || null,
    probes,
    screenshotPath,
    screenshotError,
    recovered: probes.loginControls === 0 && probes.checkpointControls === 0 && probes.securityFrames === 0,
  };
  const tmp = `${config.out}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(artifact, null, 2)}\n`);
  fs.renameSync(tmp, config.out);
  console.log(`wrote recovery evidence to ${config.out}`);
}

await main();
