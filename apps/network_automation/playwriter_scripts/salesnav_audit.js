const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const SENT_INVITATIONS_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";
const SECURITY_VERIFICATION_SELECTOR =
  "iframe#humanThirdPartyIframe,iframe[title='LinkedIn security verification'],iframe[src*='li.protechts.net']";
const MAX_RECENT_INVITATIONS = 100;

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function nowIso() {
  return new Date().toISOString();
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) return state.linkedinToolsPage;
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((item) => item.url().includes("linkedin.com/mynetwork/invitation-manager/sent")) ||
    pages.find((item) => item.url() === "about:blank") ||
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

async function blockedReason(page) {
  const url = page.url();
  if (/\/login|\/uas\/login/i.test(url)) return "login required";
  if (/\/checkpoint/i.test(url)) return "checkpoint present";
  if ((await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']")) > 0) return "login required";
  if ((await visibleCount(page, "input[name='pin'], input[name='challengeId']")) > 0) return "checkpoint present";
  if ((await visibleCount(page, SECURITY_VERIFICATION_SELECTOR)) > 0) return "security verification present";
  return null;
}

async function invitationControlCount(page) {
  return page.locator("[aria-label^='Withdraw invitation sent to']").count().catch(() => 0);
}

async function waitForInvitationGrowth(page, previousCount) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    await page.waitForTimeout(300);
    const currentCount = await invitationControlCount(page);
    if (currentCount > previousCount) return currentCount;
  }
  return invitationControlCount(page);
}

async function scrollSentInvitationsPage(page) {
  await page.evaluate(() => {
    const targets = [
      document.scrollingElement,
      document.documentElement,
      document.body,
      document.querySelector("main#workspace"),
      document.querySelector("main"),
    ].filter(Boolean);
    for (const target of targets) {
      target.scrollTop = target.scrollHeight;
    }
    window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight));
  });
}

async function main() {
  const activePage = await getPage();
  await activePage.goto(SENT_INVITATIONS_URL, { waitUntil: "domcontentloaded", timeout: 45000 });
  await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
  const block = await blockedReason(activePage);
  if (block) throw new Error(`sent invitations audit blocked: ${block}`);
  const requestedLoadMore = Math.max(0, Number(config.loadMore || 0));
  let loadMoreClicks = 0;
  let scrollLoads = 0;
  let loadMoreExhausted = false;
  for (let index = 0; index < requestedLoadMore; index += 1) {
    const previousCount = await invitationControlCount(activePage);
    const button = activePage.getByRole("button", { name: /^Load more$/i }).first();
    const buttonAvailable = (await button.count().catch(() => 0)) > 0;
    if (buttonAvailable && !(await button.isDisabled().catch(() => false))) {
      await button.click({ timeout: 8000 });
      loadMoreClicks += 1;
    } else {
      await scrollSentInvitationsPage(activePage);
      scrollLoads += 1;
    }
    const currentCount = await waitForInvitationGrowth(activePage, previousCount);
    if (currentCount <= previousCount) {
      loadMoreExhausted = true;
      break;
    }
    if (currentCount >= MAX_RECENT_INVITATIONS) break;
  }
  const workspace = activePage.locator("main#workspace").first();
  const text = await workspace.textContent({ timeout: 10000 });
  const match = clean(text).match(/People \(([\d,]+)\)/);
  if (!match) throw new Error("could not parse People (N) count from sent invitations page");
  await activePage
    .locator("[aria-label^='Withdraw invitation sent to']")
    .first()
    .waitFor({ state: "attached", timeout: 10000 })
    .catch(() => null);
  const invitations = await activePage
    .locator("[aria-label^='Withdraw invitation sent to']")
    .evaluateAll((controls, maxInvitations) => {
      function cleanPageValue(value) {
        return String(value || "").replace(/\s+/g, " ").trim();
      }

      function normalizeProfileUrl(value) {
        try {
          const parsed = new URL(String(value || ""), "https://www.linkedin.com");
          if (!["linkedin.com", "www.linkedin.com"].includes(parsed.hostname)) return null;
          const parts = parsed.pathname.split("/").filter(Boolean);
          if (parts.length < 2 || parts[0] !== "in") return null;
          return `https://www.linkedin.com/in/${encodeURIComponent(decodeURIComponent(parts[1]))}`;
        } catch {
          return null;
        }
      }

      function profileIdentifier(value) {
        const normalized = normalizeProfileUrl(value);
        if (!normalized) return null;
        return decodeURIComponent(new URL(normalized).pathname.split("/").filter(Boolean)[1] || "");
      }

      function firstPublicProfileUrl(root, control) {
        const candidates = [];
        if (control && control.getAttribute("href")) candidates.push(control.getAttribute("href"));
        if (root) {
          for (const anchor of root.querySelectorAll("a[href]")) {
            candidates.push(anchor.getAttribute("href"));
          }
        }
        for (const candidate of candidates) {
          const normalized = normalizeProfileUrl(candidate);
          if (normalized) return normalized;
        }
        return null;
      }

      function invitationRow(control) {
        if (!control) return null;
        const declaredRow =
          control.closest("li") ||
          control.closest("[data-view-name]") ||
          control.closest("[data-chameleon-result-urn]");
        if (declaredRow) return declaredRow;
        for (let node = control.parentElement; node && node !== document.body; node = node.parentElement) {
          if (node.querySelector("a[href*='/in/'], a[href*='/sales/lead/']")) return node;
        }
        return control.parentElement;
      }

      function findUrn(root, prefix) {
        if (!root) return null;
        const nodes = [root, ...root.querySelectorAll("*")];
        const escapedPrefix = prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const urnPattern = new RegExp(`${escapedPrefix}[^)"'\\s<>,]*`);
        for (const node of nodes) {
          for (const attr of Array.from(node.attributes || [])) {
            const value = cleanPageValue(attr.value);
            const match = value.match(urnPattern);
            if (match) return match[0];
          }
        }
        return null;
      }

      const rows = [];
      for (const control of controls) {
        if (rows.length >= maxInvitations) break;
        const label = cleanPageValue(control.getAttribute("aria-label"));
        if (!label.startsWith("Withdraw invitation sent to ")) continue;
        const name = cleanPageValue(label.replace("Withdraw invitation sent to ", ""));
        if (!name) continue;
        const row = invitationRow(control);
        const publicProfileUrl = firstPublicProfileUrl(row, control);
        rows.push({
          name,
          publicProfileUrl,
          publicIdentifier: profileIdentifier(publicProfileUrl),
          invitationUrn: findUrn(row, "urn:li:fsd_invitation:"),
          profileUrn: findUrn(row, "urn:li:fsd_profile:"),
          rowIndex: rows.length,
        });
      }
      return rows;
    }, MAX_RECENT_INVITATIONS);
  const names = invitations.map((invitation) => invitation.name);
  const payload = {
    capturedAt: nowIso(),
    url: activePage.url(),
    peopleCount: Number(match[1].replace(/,/g, "")),
    requestedLoadMore,
    loadMoreClicks,
    scrollLoads,
    loadMoreExhausted,
    loadedCount: invitations.length,
    recentNames: names,
    invitations,
  };
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`wrote Sales Navigator audit to ${config.out}`);
}

await main();
