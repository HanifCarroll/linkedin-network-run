const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const SENT_INVITATIONS_URL = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";
const CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/";
const CONTRACT_VERSION = "acceptance-list-reconciliation-v1";
const MAX_LOAD_ACTIONS = Math.max(1, Number(config.maxLoadActions || 100));
const WATERMARK_SIZE = Math.max(1, Number(config.watermarkSize || 25));
const previousWatermark = new Set(
  Array.isArray(config.previousWatermark) ? config.previousWatermark : []
);
const SECURITY_SELECTOR =
  "iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']";

function nowIso() {
  return new Date().toISOString();
}

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizePublicProfileUrl(value) {
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

function publicIdentifier(value) {
  const normalized = normalizePublicProfileUrl(value);
  if (!normalized) return null;
  return decodeURIComponent(new URL(normalized).pathname.split("/").filter(Boolean)[1] || "");
}

function findUrn(root, prefix) {
  if (!root) return null;
  const nodes = [root, ...root.querySelectorAll("*")];
  const escapedPrefix = prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const urnPattern = new RegExp(`${escapedPrefix}[^)"'\\s<>,]*`);
  for (const node of nodes) {
    for (const attr of Array.from(node.attributes || [])) {
      const match = clean(attr.value).match(urnPattern);
      if (match) return match[0];
    }
  }
  return null;
}

function rowIdentities(row) {
  const result = [];
  if (row.memberId) result.push(`member:${row.memberId}`);
  if (row.publicIdentifier) result.push(`public:${row.publicIdentifier}`);
  return result;
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((candidate) => candidate.url().includes("linkedin.com/mynetwork/")) ||
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

async function exactVisibleTextCount(page, selector, expected) {
  return page.locator(selector).evaluateAll((items, text) => {
    const cleanText = (value) => String(value || "").replace(/\s+/g, " ").trim();
    return items.filter((item) => {
      const style = window.getComputedStyle(item);
      const visible =
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        item.getClientRects().length > 0;
      const label = cleanText(item.getAttribute("aria-label") || item.textContent);
      return visible && label === text;
    }).length;
  }, expected).catch(() => 0);
}

async function fatalPageReason(page) {
  const url = page.url();
  if (/\/login|\/uas\/login/i.test(url)) return "login required";
  if (/\/checkpoint/i.test(url)) return "checkpoint present";
  if ((await visibleCount(page, "input[name='session_key'], form[action*='/uas/login']")) > 0) {
    return "login required";
  }
  if ((await visibleCount(page, "input[name='pin'], input[name='challengeId']")) > 0) {
    return "checkpoint present";
  }
  if ((await visibleCount(page, SECURITY_SELECTOR)) > 0) {
    return "security verification present";
  }
  const unusual = await page
    .getByRole("heading", {
      name: "We noticed some unusual activity on your account",
      exact: true,
    })
    .count()
    .catch(() => 0);
  if (unusual > 0) return "We noticed some unusual activity on your account";
  return null;
}

async function navigateWithRateLimitGuard(page, url) {
  let rateLimitedUrl = null;
  const observeResponse = (response) => {
    if (response.status() !== 429) return;
    try {
      const parsed = new URL(response.url());
      if (parsed.hostname === "www.linkedin.com" || parsed.hostname === "linkedin.com") {
        rateLimitedUrl = response.url();
      }
    } catch {
      return;
    }
  };
  page.on("response", observeResponse);
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await waitForPageLoad({ page, timeout: 10000 }).catch(() => null);
  } finally {
    page.off("response", observeResponse);
  }
  if (rateLimitedUrl) throw new Error(`LinkedIn returned HTTP 429 for ${rateLimitedUrl}`);
  const fatal = await fatalPageReason(page);
  if (fatal) throw new Error(fatal);
}

async function performLoadAction(page) {
  const loadMore = page.getByRole("button", { name: "Load more", exact: true });
  const loadMoreCount = await loadMore.count().catch(() => 0);
  if (loadMoreCount === 1 && !(await loadMore.isDisabled().catch(() => true))) {
    await loadMore.click({ timeout: 8000 });
    return "load-more-click";
  }
  const scroll = await page.evaluate(() => {
    const candidates = [
      document.querySelector("main#workspace"),
      document.querySelector("main"),
      document.scrollingElement,
      document.documentElement,
      document.body,
    ].filter(Boolean);
    const target =
      candidates.find((candidate) => candidate.scrollHeight > candidate.clientHeight) ||
      candidates[0];
    const before = target ? target.scrollTop : 0;
    if (target) target.scrollTop = target.scrollHeight;
    return {
      before,
      after: target ? target.scrollTop : 0,
      max: target ? target.scrollHeight - target.clientHeight : 0,
    };
  });
  return scroll.after > scroll.before ? "scroll" : "no-more-content";
}

async function capturePending(page) {
  await navigateWithRateLimitGuard(page, SENT_INVITATIONS_URL);
  const rowsByIdentity = new Map();
  const loadActions = [];
  let unidentifiedRows = [];

  const rememberRows = async () => {
    const rows = await page
      .locator(
        "a[aria-label^='Withdraw invitation sent to '], button[aria-label^='Withdraw invitation sent to ']"
      )
      .evaluateAll((controls) => {
        const cleanPageValue = (value) =>
          String(value || "").replace(/\s+/g, " ").trim();
        const normalizeProfile = (value) => {
          try {
            const parsed = new URL(String(value || ""), "https://www.linkedin.com");
            if (!["linkedin.com", "www.linkedin.com"].includes(parsed.hostname)) return null;
            const parts = parsed.pathname.split("/").filter(Boolean);
            if (parts.length < 2 || parts[0] !== "in") return null;
            return `https://www.linkedin.com/in/${encodeURIComponent(
              decodeURIComponent(parts[1])
            )}`;
          } catch {
            return null;
          }
        };
        const findProfileUrn = (root) => {
          if (!root) return null;
          const nodes = [root, ...root.querySelectorAll("*")];
          const pattern = /urn:li:(?:fsd_profile|member):[^)"'\s<>,]*/;
          for (const node of nodes) {
            for (const attr of Array.from(node.attributes || [])) {
              const match = cleanPageValue(attr.value).match(pattern);
              if (match) return match[0];
            }
          }
          return null;
        };
        return controls.map((control, index) => {
          const label = cleanPageValue(control.getAttribute("aria-label"));
          const row = control.closest("li") || control.closest("[data-view-name]");
          const anchors = row ? Array.from(row.querySelectorAll("a[href]")) : [];
          let publicProfileUrl = null;
          for (const anchor of anchors) {
            publicProfileUrl = normalizeProfile(anchor.getAttribute("href"));
            if (publicProfileUrl) break;
          }
          const profileUrn = findProfileUrn(row);
          const memberId = profileUrn
            ? profileUrn.replace(/^urn:li:(?:fsd_profile|member):/, "")
            : null;
          const identifier = publicProfileUrl
            ? decodeURIComponent(
                new URL(publicProfileUrl).pathname.split("/").filter(Boolean)[1] || ""
              )
            : null;
          return {
            name: label.replace(/^Withdraw invitation sent to\s+/, "") || null,
            publicProfileUrl,
            publicIdentifier: identifier,
            profileUrn,
            memberId,
            rowIndex: index,
          };
        });
      });
    unidentifiedRows = rows.filter((row) => rowIdentities(row).length === 0);
    for (const row of rows) {
      const identities = rowIdentities(row);
      const key = identities[0];
      if (key && !rowsByIdentity.has(key)) rowsByIdentity.set(key, row);
    }
  };

  await rememberRows();
  let complete = false;
  let stopReason = null;
  for (let attempt = 0; attempt < MAX_LOAD_ACTIONS; attempt += 1) {
    const before = rowsByIdentity.size;
    const action = await performLoadAction(page);
    loadActions.push(action);
    if (action === "no-more-content") {
      complete = true;
      stopReason = "end-of-list";
      break;
    }
    await page.waitForTimeout(750);
    const fatal = await fatalPageReason(page);
    if (fatal) throw new Error(fatal);
    await rememberRows();
    if (rowsByIdentity.size <= before) {
      stopReason = "sent-invitations list did not grow after a load action";
    }
  }
  if (!complete) stopReason = "maximum load actions reached before end of list";
  const rows = [...Array.from(rowsByIdentity.values()), ...unidentifiedRows];
  const identityMissingCount = unidentifiedRows.length;
  if (identityMissingCount > 0) {
    complete = false;
    stopReason = "sent-invitation rows lacked exact profile identity";
  }
  return {
    url: page.url(),
    complete,
    loadedCount: rows.length,
    loadActions,
    stopReason,
    identityMissingCount,
    rows,
    warnings: identityMissingCount
      ? [`${identityMissingCount} sent-invitation row(s) lacked exact identity`]
      : [],
  };
}

async function captureConnections(page) {
  await navigateWithRateLimitGuard(page, CONNECTIONS_URL);
  const recentlyAddedCount = await exactVisibleTextCount(
    page,
    "button, [role='button']",
    "Recently added"
  );
  if (recentlyAddedCount < 1) {
    return {
      url: page.url(),
      complete: false,
      loadedCount: 0,
      loadActions: [],
      stopReason: "Recently added sort control was not visibly selected",
      sortOrder: null,
      identityMissingCount: 0,
      rows: [],
      warnings: ["connections list sort could not be verified"],
    };
  }

  const rowsByIdentity = new Map();
  const loadActions = [];
  let boundaryFound = false;
  let unidentifiedRows = [];

  const rememberRows = async () => {
    const rows = await page.locator("main a[href*='/in/']").evaluateAll((anchors) => {
      const cleanPageValue = (value) =>
        String(value || "").replace(/\s+/g, " ").trim();
      const normalizeProfile = (value) => {
        try {
          const parsed = new URL(String(value || ""), "https://www.linkedin.com");
          if (!["linkedin.com", "www.linkedin.com"].includes(parsed.hostname)) return null;
          const parts = parsed.pathname.split("/").filter(Boolean);
          if (parts.length < 2 || parts[0] !== "in") return null;
          return `https://www.linkedin.com/in/${encodeURIComponent(
            decodeURIComponent(parts[1])
          )}`;
        } catch {
          return null;
        }
      };
      const findProfileUrn = (root) => {
        if (!root) return null;
        const nodes = [root, ...root.querySelectorAll("*")];
        const pattern = /urn:li:(?:fsd_profile|member):[^)"'\s<>,]*/;
        for (const node of nodes) {
          for (const attr of Array.from(node.attributes || [])) {
            const match = cleanPageValue(attr.value).match(pattern);
            if (match) return match[0];
          }
        }
        return null;
      };
      const result = [];
      for (const anchor of anchors) {
        const publicProfileUrl = normalizeProfile(anchor.getAttribute("href"));
        if (!publicProfileUrl) continue;
        const row = anchor.closest("li");
        if (!row) continue;
        const actions = Array.from(row.querySelectorAll("button, a[role='button']"));
        const isConnectionRow = actions.some((action) => {
          const label = cleanPageValue(
            action.getAttribute("aria-label") || action.textContent
          );
          return label === "Message" || label.startsWith("Send a message to ");
        });
        if (!isConnectionRow) continue;
        const profileUrn = findProfileUrn(row);
        const memberId = profileUrn
          ? profileUrn.replace(/^urn:li:(?:fsd_profile|member):/, "")
          : null;
        const identifier = decodeURIComponent(
          new URL(publicProfileUrl).pathname.split("/").filter(Boolean)[1] || ""
        );
        result.push({
          name: cleanPageValue(anchor.textContent) || null,
          publicProfileUrl,
          publicIdentifier: identifier,
          profileUrn,
          memberId,
          rowIndex: result.length,
        });
      }
      return result;
    });
    unidentifiedRows = rows.filter((row) => rowIdentities(row).length === 0);
    for (const row of rows) {
      const identities = rowIdentities(row);
      const key = identities[0];
      if (!key || rowsByIdentity.has(key)) continue;
      rowsByIdentity.set(key, row);
      if (identities.some((identity) => previousWatermark.has(identity))) {
        boundaryFound = true;
      }
    }
  };

  await rememberRows();
  const baselineOnly = previousWatermark.size === 0;
  let complete = baselineOnly && rowsByIdentity.size >= WATERMARK_SIZE;
  let stopReason = complete ? "baseline captured" : null;
  if (!baselineOnly && boundaryFound) {
    complete = true;
    stopReason = "prior watermark reached";
  }
  for (
    let attempt = 0;
    !complete && attempt < MAX_LOAD_ACTIONS;
    attempt += 1
  ) {
    const before = rowsByIdentity.size;
    const action = await performLoadAction(page);
    loadActions.push(action);
    if (action === "no-more-content") {
      if (baselineOnly && rowsByIdentity.size > 0) {
        complete = true;
        stopReason = "baseline captured at end of connections list";
      } else {
        stopReason = "end of connections list reached before prior watermark";
      }
      break;
    }
    await page.waitForTimeout(750);
    const fatal = await fatalPageReason(page);
    if (fatal) throw new Error(fatal);
    await rememberRows();
    if (baselineOnly && rowsByIdentity.size >= WATERMARK_SIZE) {
      complete = true;
      stopReason = "baseline captured";
      break;
    }
    if (boundaryFound) {
      complete = true;
      stopReason = "prior watermark reached";
      break;
    }
    if (rowsByIdentity.size <= before) {
      stopReason = "connections list stopped growing before prior watermark";
      break;
    }
  }
  if (!complete && !stopReason) {
    stopReason = "maximum load actions reached before prior watermark";
  }
  const rows = [...Array.from(rowsByIdentity.values()), ...unidentifiedRows];
  const identityMissingCount = unidentifiedRows.length;
  if (identityMissingCount > 0) {
    complete = false;
    stopReason = "connection rows lacked exact profile identity";
  }
  return {
    url: page.url(),
    complete,
    loadedCount: rows.length,
    loadActions,
    stopReason,
    sortOrder: "recently-added",
    identityMissingCount,
    rows,
    warnings: identityMissingCount
      ? [`${identityMissingCount} connection row(s) lacked exact identity`]
      : [],
    baselineOnly,
    connectionDeltaComplete: !baselineOnly && boundaryFound,
  };
}

const page = await getPage();
const pending = await capturePending(page);
const connections = await captureConnections(page);
const nextWatermark = [];
for (const row of connections.rows) {
  const identity = rowIdentities(row)[0];
  if (identity && !nextWatermark.includes(identity)) nextWatermark.push(identity);
  if (nextWatermark.length >= WATERMARK_SIZE) break;
}
const artifact = {
  capturedAt: nowIso(),
  contractVersion: CONTRACT_VERSION,
  baselineOnly: Boolean(connections.baselineOnly),
  connectionDeltaComplete: Boolean(connections.connectionDeltaComplete),
  previousWatermark: Array.from(previousWatermark),
  nextWatermark,
  pending,
  connections: {
    url: connections.url,
    complete: connections.complete,
    loadedCount: connections.loadedCount,
    loadActions: connections.loadActions,
    stopReason: connections.stopReason,
    sortOrder: connections.sortOrder,
    identityMissingCount: connections.identityMissingCount,
    rows: connections.rows,
    warnings: connections.warnings,
  },
  warnings: [...pending.warnings, ...connections.warnings],
};
fs.writeFileSync(config.out, `${JSON.stringify(artifact, null, 2)}\n`);
console.log(
  `wrote acceptance list reconciliation: pending=${pending.loadedCount}, connections=${connections.loadedCount}`
);
