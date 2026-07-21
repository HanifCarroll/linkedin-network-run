const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(state.linkedinToolsConfigPath, "utf8"));
const record = config.record || {};

function nowIso() {
  return new Date().toISOString();
}

function normalize(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function salesNavLeadId(value) {
  try {
    const parsed = new URL(String(value || ""));
    if (
      parsed.hostname !== "linkedin.com" &&
      !parsed.hostname.endsWith(".linkedin.com")
    ) {
      return null;
    }
    const match = parsed.pathname.match(/^\/sales\/lead\/([^/,]+)(?:,|\/|$)/);
    return match ? match[1] : null;
  } catch (_error) {
    return null;
  }
}

function basePayload(url) {
  return {
    candidate: {
      id: record.id,
      key: record.key,
      name: record.name,
      profileUrl: record.profile_url || record.profileUrl || null,
      salesNavProfileUrl:
        record.sales_nav_profile_url || record.salesNavProfileUrl || null,
      source: record.source,
    },
    listName: record.sales_nav_list_name || record.salesNavListName || "",
    status: "save_failed",
    url,
    checkedAt: nowIso(),
  };
}

function write(payload) {
  fs.writeFileSync(config.out, `${JSON.stringify(payload, null, 2)}\n`);
}

async function getPage() {
  if (state.linkedinToolsPage && !state.linkedinToolsPage.isClosed()) {
    return state.linkedinToolsPage;
  }
  const pages = context.pages();
  state.linkedinToolsPage =
    pages.find((candidatePage) => candidatePage.url().includes("linkedin.com/sales")) ||
    pages.find((candidatePage) => candidatePage.url() === "about:blank") ||
    page ||
    (await context.newPage());
  return state.linkedinToolsPage;
}

async function gotoProfilePage(activePage, profileUrl) {
  try {
    await activePage.goto(profileUrl, {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    return activePage;
  } catch (error) {
    if (
      !/(Frame has been detached|page has been closed|Target page, context or browser has been closed)/i.test(
        error.message || "",
      )
    ) {
      throw error;
    }
    const freshPage = await context.newPage();
    state.linkedinToolsPage = freshPage;
    await freshPage.goto(profileUrl, {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    return freshPage;
  }
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

async function classifyBlock(activePage) {
  const url = activePage.url();
  if (
    /\/login|\/uas\/login/i.test(url) ||
    (await visibleCount(
      activePage,
      "input[name='session_key'], form[action*='/uas/login']",
    ))
  ) {
    return { status: "blocked", reason: "login required" };
  }
  if (
    /\/checkpoint/i.test(url) ||
    (await visibleCount(activePage, "input[name='pin'], input[name='challengeId']"))
  ) {
    return { status: "blocked", reason: "checkpoint present" };
  }
  if (
    await visibleCount(
      activePage,
      "iframe#humanThirdPartyIframe, iframe[title='LinkedIn security verification'], iframe[src*='li.protechts.net']",
    )
  ) {
    return { status: "blocked", reason: "security verification present" };
  }
  return null;
}

async function findProfileSaveAction(activePage) {
  const buttons = activePage.locator("button[aria-label]");
  const count = await buttons.count().catch(() => 0);
  const visible = [];
  const matches = [];
  for (let index = 0; index < count; index += 1) {
    const button = buttons.nth(index);
    if (!(await button.isVisible().catch(() => false))) continue;
    const ariaLabel = normalize(await button.getAttribute("aria-label").catch(() => ""));
    if (ariaLabel) visible.push(ariaLabel);
    const saved = ariaLabel.match(/^(.+) saved\. Add to a custom list\.$/);
    const save = ariaLabel.match(/^Save (.+) as a lead\. Save to list\.$/);
    if (saved) matches.push({ button, kind: "saved", ariaLabel, profileName: saved[1] });
    if (save) matches.push({ button, kind: "save", ariaLabel, profileName: save[1] });
  }
  if (matches.length === 1) {
    return { ...matches[0], visible, ambiguous: false };
  }
  return {
    button: null,
    kind: null,
    ariaLabel: null,
    profileName: null,
    visible,
    ambiguous: matches.length > 1,
    matchingAriaLabels: matches.map((match) => match.ariaLabel),
  };
}

async function waitForProfileSaveAction(activePage, timeoutMs = 15000) {
  const startedAt = Date.now();
  let attempts = 0;
  let lastAction = null;
  while (Date.now() - startedAt < timeoutMs) {
    attempts += 1;
    const block = await classifyBlock(activePage);
    if (block) {
      return {
        action: null,
        block,
        attempts,
        elapsedMs: Date.now() - startedAt,
      };
    }
    lastAction = await findProfileSaveAction(activePage);
    if (lastAction.button || lastAction.ambiguous) {
      return {
        action: lastAction,
        block: null,
        attempts,
        elapsedMs: Date.now() - startedAt,
      };
    }
    await activePage.waitForTimeout(500);
  }
  return {
    action: lastAction || (await findProfileSaveAction(activePage)),
    block: null,
    attempts,
    elapsedMs: Date.now() - startedAt,
  };
}

async function listSelectionState(activePage, listName, profileName) {
  return activePage.evaluate(({ targetListName, expectedProfileName }) => {
    const normalizeText = (text) => String(text || "").replace(/\s+/g, " ").trim();
    const escapeRegExp = (text) =>
      String(text || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const visibleElement = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.display !== "none" &&
        style.visibility !== "hidden"
      );
    };
    const menuOutlet = document.querySelector("#hue-web-menu-outlet");
    if (!menuOutlet) {
      return { found: false, checked: false, visibleLists: [], control: null };
    }
    const exact = Array.from(
      menuOutlet.querySelectorAll("label,[role='option'],[role='menuitem'],li,span,div,strong"),
    )
      .filter((element) => visibleElement(element))
      .filter((element) => normalizeText(element.textContent) === targetListName)
      .sort((left, right) => left.childElementCount - right.childElementCount);
    if (exact.length === 0) {
      const visibleLists = Array.from(
        menuOutlet.querySelectorAll("label,[role='option'],[role='menuitem'],li"),
      )
        .filter((element) => visibleElement(element))
        .map((element) => normalizeText(element.textContent))
        .filter(Boolean);
      return { found: false, checked: false, visibleLists };
    }
    const textNode = exact[0];
    const container =
      textNode.closest("label,[role='option'],[role='menuitem'],li") ||
      textNode.parentElement ||
      textNode;
    const checkbox =
      container.querySelector("input[type='checkbox']") ||
      container.closest("label")?.querySelector("input[type='checkbox']") ||
      null;
    const button = textNode.closest("button");
    const buttonAriaLabel = normalizeText(button?.getAttribute("aria-label"));
    const selectedByRemoveAction = new RegExp(
      `^Remove ${escapeRegExp(expectedProfileName)} from ${escapeRegExp(targetListName)} list with [0-9]+ leads?$`,
    ).test(buttonAriaLabel);
    const describe = (element) =>
      element
        ? {
            tagName: element.tagName,
            role: element.getAttribute("role"),
            ariaChecked: element.getAttribute("aria-checked"),
            ariaSelected: element.getAttribute("aria-selected"),
            ariaPressed: element.getAttribute("aria-pressed"),
            dataSelected: element.getAttribute("data-selected"),
          }
        : null;
    const ancestors = [];
    let ancestor = textNode;
    for (let depth = 0; ancestor && depth < 5; depth += 1) {
      ancestors.push(describe(ancestor));
      ancestor = ancestor.parentElement;
    }
    return {
      found: true,
      checked:
        checkbox?.checked === true ||
        container.getAttribute("aria-checked") === "true" ||
        container.getAttribute("aria-selected") === "true" ||
        button?.getAttribute("aria-checked") === "true" ||
        button?.getAttribute("aria-selected") === "true" ||
        button?.getAttribute("aria-pressed") === "true" ||
        button?.getAttribute("data-selected") === "true" ||
        selectedByRemoveAction,
      visibleLists: [targetListName],
      control: {
        textNode: describe(textNode),
        container: describe(container),
        checkbox: checkbox
          ? { ...describe(checkbox), checked: checkbox.checked === true }
          : null,
        button: describe(button),
        buttonAriaLabel,
        selectedByRemoveAction,
        buttonOuterHtml: button?.outerHTML || null,
        ancestors,
      },
    };
  }, { targetListName: listName, expectedProfileName: profileName });
}

async function clickExactListSelection(activePage, listName, profileName) {
  const match = await activePage.evaluate(({ targetListName, expectedProfileName }) => {
    const normalizeText = (text) => String(text || "").replace(/\s+/g, " ").trim();
    const escapeRegExp = (text) =>
      String(text || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const visibleElement = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.display !== "none" &&
        style.visibility !== "hidden"
      );
    };
    const menuOutlet = document.querySelector("#hue-web-menu-outlet");
    if (!menuOutlet) {
      return { buttonIndexes: [], buttonCount: 0, matchingAriaLabels: [] };
    }
    const allButtons = Array.from(menuOutlet.querySelectorAll("button"));
    const exactAddAction = new RegExp(
      `^Add ${escapeRegExp(expectedProfileName)} to ${escapeRegExp(targetListName)} list with [0-9]+ leads?$`,
    );
    const matchingButtons = allButtons.filter((button) => {
      const ariaLabel = normalizeText(button.getAttribute("aria-label"));
      return visibleElement(button) && exactAddAction.test(ariaLabel);
    });
    const customListButtons = matchingButtons.filter((button) => {
      const containingMenu = button.closest("ul[role='menu']");
      return Boolean(containingMenu?.parentElement?.closest("li[role='menuitem']"));
    });
    const selectableButtons =
      customListButtons.length > 0 ? customListButtons : matchingButtons;
    return {
      buttonIndexes: selectableButtons.map((button) => allButtons.indexOf(button)),
      buttonCount: selectableButtons.length,
      totalButtonCount: matchingButtons.length,
      selectionScope:
        customListButtons.length > 0 ? "custom_lists" : "visible_exact_match",
      matchingAriaLabels: selectableButtons.map((button) =>
        normalizeText(button.getAttribute("aria-label")),
      ),
    };
  }, { targetListName: listName, expectedProfileName: profileName });
  if (match.buttonCount === 0) {
    return {
      clicked: false,
      ambiguous: false,
      candidateCount: 0,
      totalCandidateCount: match.totalButtonCount,
      selectionScope: match.selectionScope,
      matchingAriaLabels: match.matchingAriaLabels,
    };
  }
  if (match.buttonCount !== 1 || match.buttonIndexes[0] < 0) {
    return {
      clicked: false,
      ambiguous: true,
      candidateCount: match.buttonCount,
      totalCandidateCount: match.totalButtonCount,
      selectionScope: match.selectionScope,
      matchingAriaLabels: match.matchingAriaLabels,
    };
  }
  await activePage
    .locator("#hue-web-menu-outlet button")
    .nth(match.buttonIndexes[0])
    .click({ timeout: 8000 });
  return {
    clicked: true,
    ambiguous: false,
    candidateCount: 1,
    totalCandidateCount: match.totalButtonCount,
    selectionScope: match.selectionScope,
    matchingAriaLabels: match.matchingAriaLabels,
  };
}

async function clickVisibleCompletionButton(activePage) {
  for (const label of ["Done", "Save", "Apply"]) {
    const buttons = activePage.getByRole("button", { name: label, exact: true });
    const count = await buttons.count().catch(() => 0);
    for (let index = 0; index < count; index += 1) {
      const button = buttons.nth(index);
      if (
        (await button.isVisible().catch(() => false)) &&
        !(await button.isDisabled().catch(() => true))
      ) {
        await button.click({ timeout: 8000 });
        return label;
      }
    }
  }
  return null;
}

async function main() {
  const listName = record.sales_nav_list_name || record.salesNavListName;
  const profileUrl = record.sales_nav_profile_url || record.salesNavProfileUrl;
  if (!config.allowSave) {
    write({
      ...basePayload(profileUrl || null),
      status: "blocked",
      reason: "real Sales Navigator lead-list save requires allowSave",
    });
    return;
  }
  if (!listName) {
    write({
      ...basePayload(profileUrl || null),
      status: "list_missing",
      reason: "record has no target Sales Navigator lead-list name",
    });
    return;
  }
  if (!profileUrl || !/linkedin\.com\/sales\/lead\//i.test(profileUrl)) {
    write({
      ...basePayload(profileUrl || null),
      status: "not_saveable",
      reason: "record has no Sales Navigator lead profile URL",
    });
    return;
  }
  const expectedLeadId = salesNavLeadId(profileUrl);
  if (!expectedLeadId) {
    write({
      ...basePayload(profileUrl),
      status: "blocked",
      reason: "record has no parseable Sales Navigator lead identity",
    });
    return;
  }

  let activePage = await getPage();
  activePage = await gotoProfilePage(activePage, profileUrl);
  await waitForPageLoad({ page: activePage, timeout: 10000 }).catch(() => null);
  const payload = basePayload(activePage.url());
  const block = await classifyBlock(activePage);
  if (block) {
    write({ ...payload, ...block });
    return;
  }

  const loadedLeadId = salesNavLeadId(activePage.url());
  if (loadedLeadId !== expectedLeadId) {
    write({
      ...payload,
      status: "blocked",
      reason: "Sales Navigator profile identity did not match the accepted candidate",
      identity: { expectedLeadId, loadedLeadId },
    });
    return;
  }

  const saveActionWait = await waitForProfileSaveAction(activePage);
  if (saveActionWait.block) {
    write({
      ...payload,
      ...saveActionWait.block,
      identity: { expectedLeadId, loadedLeadId },
      readiness: {
        attempts: saveActionWait.attempts,
        elapsedMs: saveActionWait.elapsedMs,
      },
    });
    return;
  }
  const saveAction = saveActionWait.action;
  if (!saveAction.button) {
    write({
      ...payload,
      status: saveAction.ambiguous ? "blocked" : "not_saveable",
      reason: saveAction.ambiguous
        ? "multiple Sales Navigator Save or Saved actions were visible"
        : "exact Sales Navigator Save or Saved action was not visible",
      identity: { expectedLeadId, loadedLeadId },
      action: {
        visibleAriaLabels: saveAction.visible,
        matchingAriaLabels: saveAction.matchingAriaLabels,
      },
      readiness: {
        attempts: saveActionWait.attempts,
        elapsedMs: saveActionWait.elapsedMs,
      },
    });
    return;
  }
  await saveAction.button.click({ timeout: 8000 });
  await activePage.waitForTimeout(500);

  let selection = await listSelectionState(activePage, listName, record.name);
  if (!selection.found) {
    write({
      ...payload,
      status: "list_missing",
      reason: `target lead list was not visible: ${listName}`,
      action: { openedFrom: saveAction.kind, ariaLabel: saveAction.ariaLabel },
      visibleLists: selection.visibleLists,
    });
    return;
  }
  const initiallyChecked = selection.checked;
  if (!selection.checked) {
    const listClick = await clickExactListSelection(
      activePage,
      listName,
      record.name,
    );
    if (!listClick.clicked) {
      write({
        ...payload,
        status: listClick.ambiguous ? "blocked" : "list_missing",
        reason: listClick.ambiguous
          ? `target lead list selection was ambiguous: ${listName}`
          : `target lead list text was present but not clickable: ${listName}`,
        visibleLists: selection.visibleLists,
        action: { listClick },
        selection,
      });
      return;
    }
    await activePage.waitForTimeout(300);
    selection = await listSelectionState(activePage, listName, record.name);
  }
  if (!selection.checked) {
    write({
      ...payload,
      status: "save_failed",
      reason: `Sales Navigator did not show ${listName} as selected`,
      visibleLists: selection.visibleLists,
      selection,
    });
    return;
  }

  const completionButton = await clickVisibleCompletionButton(activePage);
  if (completionButton) await activePage.waitForTimeout(500);
  write({
    ...payload,
    status: initiallyChecked ? "already_saved" : "saved",
    action: {
      openedFrom: saveAction.kind,
      ariaLabel: saveAction.ariaLabel,
      profileName: saveAction.profileName,
      completionButton,
    },
    identity: { expectedLeadId, loadedLeadId },
    readiness: {
      attempts: saveActionWait.attempts,
      elapsedMs: saveActionWait.elapsedMs,
    },
    visibleLists: selection.visibleLists,
  });
}

await main();
