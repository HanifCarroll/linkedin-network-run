const fs = require("node:fs");
const path = require("node:path");

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizeName(value) {
  return cleanText(value).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function bodyContainsCandidateName(body, candidateName) {
  const normalizedBody = normalizeName(body);
  const normalizedName = normalizeName(candidateName);
  return Boolean(normalizedName) && normalizedBody.includes(normalizedName);
}

function configValue(name, fallback = null) {
  const config = globalThis.salesNavSendConfig || state.salesNavSendConfig || {};
  return Object.prototype.hasOwnProperty.call(config, name) ? config[name] : fallback;
}

function classifyMenu(labels) {
  const texts = labels.map((label) => label.text).filter(Boolean);
  if (texts.some((text) => /^Connect\s*[—-]\s*Pending$/i.test(text) || /^Pending$/i.test(text))) {
    return "already-pending";
  }
  if (texts.some((text) => /^Connect$/i.test(text))) {
    return "connectable";
  }
  return "unknown";
}

async function readBody(timeout = 10000) {
  return state.page.locator("body").innerText({ timeout }).catch(() => "");
}

function isHardBlocker(url, body) {
  return /checkpoint|security verification|weekly invitation limit|sign in|uas\/login/i.test(`${url}\n${body.slice(0, 1500)}`);
}

async function menuLabelsForCurrentLead() {
  await state.page.keyboard.press("Escape").catch(() => {});
  const trigger = state.page.locator('button[aria-label="Open actions overflow menu"]').first();
  await trigger.waitFor({ state: "visible", timeout: 8000 }).catch(() => {});
  if (!(await trigger.count())) {
    return { state: "missing-trigger", labels: [] };
  }
  const menuId = await trigger.getAttribute("aria-controls");
  await trigger.click({ timeout: 8000 });
  await state.page.waitForTimeout(500);
  const menu = menuId ? state.page.locator(`#${menuId}`) : state.page.locator("[data-popper-placement]").last();
  if (!(await menu.count())) {
    return { state: "missing-menu", labels: [] };
  }
  const labels = await menu.locator("button,a,[role=menuitem]").evaluateAll((items) =>
    items.map((item, index) => ({
      index,
      text: (item.innerText || item.textContent || "").replace(/\s+/g, " ").trim(),
      aria: item.getAttribute("aria-label"),
      tag: item.tagName,
      href: item.href || null,
      disabled: item.hasAttribute("disabled"),
    })).filter((item) => item.text || item.aria),
  );
  return { state: classifyMenu(labels), labels, menuId };
}

async function verifyAfterSend() {
  const checks = [];
  let last = { state: "unknown", labels: [] };
  const attempts = [
    { phase: "menu-verify-1", delayMs: 1500, reload: false },
    { phase: "menu-verify-2", delayMs: 2500, reload: false },
    { phase: "reload-verify", delayMs: 1500, reload: true },
    { phase: "post-reload-verify", delayMs: 2500, reload: false },
  ];

  for (const attempt of attempts) {
    if (attempt.delayMs > 0) {
      await state.page.waitForTimeout(attempt.delayMs);
    }
    if (attempt.reload) {
      await state.page.reload({ waitUntil: "domcontentloaded", timeout: 45000 }).catch((error) => {
        checks.push({ phase: "reload-error", error: String(error).slice(0, 500) });
      });
      await state.page.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});
      await state.page.waitForTimeout(1000);
    }

    const body = await readBody(10000);
    if (/email address|required email|enter.*email/i.test(body)) {
      last = { state: "email-required", labels: [], phase: attempt.phase, body: cleanText(body).slice(0, 1000) };
      checks.push({ phase: attempt.phase, state: last.state });
      return { ...last, checks };
    }
    if (isHardBlocker(state.page.url(), body)) {
      last = { state: "blocked", labels: [], phase: attempt.phase, body: cleanText(body).slice(0, 1000) };
      checks.push({ phase: attempt.phase, state: last.state });
      return { ...last, checks };
    }

    last = await menuLabelsForCurrentLead().catch((error) => ({
      state: "verify-error",
      labels: [],
      error: String(error).slice(0, 500),
    }));
    checks.push({
      phase: attempt.phase,
      state: last.state,
      labels: (last.labels || []).map((item) => item.text || item.aria).filter(Boolean).slice(0, 8),
    });
    if (last.state === "already-pending") {
      return { ...last, checks };
    }
  }

  return { ...last, checks };
}

async function clickCurrentMenuConnect(menuId) {
  const menu = menuId
    ? state.page.locator(`#${menuId}`)
    : state.page.locator("[data-popper-placement], [id^='hue-menu-']").filter({ hasText: "Connect" }).last();
  const clicked = await menu.evaluate((menuElement) => {
    const item = Array.from(menuElement.querySelectorAll("button,a,[role=menuitem]"))
      .find((element) => (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim() === "Connect");
    if (!item) {
      return false;
    }
    item.click();
    return true;
  });
  if (!clicked) {
    throw new Error("connect-menu-item-missing");
  }
}

async function clickSendInvitation(candidate) {
  const body = await state.page.locator("body").innerText({ timeout: 10000 });
  if (!bodyContainsCandidateName(body, candidate.name)) {
    return {
      status: "identity-mismatch",
      detail: "invite flow does not contain candidate name",
      body: cleanText(body).slice(0, 1000),
    };
  }
  if (/email address|required email|enter.*email/i.test(body)) {
    return { status: "email-required", detail: "email required in invite flow" };
  }
  const clicked = await state.page.evaluate(() => {
    const candidates = Array.from(document.querySelectorAll("button"));
    const button = candidates.find((element) => {
      const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
      const rect = element.getBoundingClientRect();
      const visible = rect.width > 0 && rect.height > 0;
      return visible && /^(Send Invitation|Send invite|Send now|Send)$/.test(text);
    });
    if (!button) {
      return null;
    }
    const label = (button.innerText || button.textContent || "").replace(/\s+/g, " ").trim();
    button.click();
    return label;
  });
  if (!clicked) {
    return { status: "send-button-missing", detail: cleanText(body).slice(0, 1000) };
  }
  return { status: "clicked-send", label: clicked };
}

async function main() {
  const candidate = configValue("candidate", null);
  const out = path.resolve(configValue("out", "/tmp/linkedin-network-run-send-result.json"));
  const dryRun = Boolean(configValue("dryRun", true));
  const allowSend = Boolean(configValue("allowSend", false));

  if (!candidate || !candidate.profile_url) {
    throw new Error("candidate with profile_url is required in state.salesNavSendConfig");
  }
  if (!dryRun && !allowSend) {
    throw new Error("real send requires allowSend=true");
  }

  state.page = state.page || context.pages().find((page) => page.url().includes("/sales/lead/")) || await context.newPage();
  let navigationError = null;
  try {
    await state.page.goto(candidate.profile_url, { waitUntil: "domcontentloaded", timeout: 15000 });
    await state.page.waitForLoadState("domcontentloaded");
  } catch (error) {
    navigationError = String(error).slice(0, 500);
    await state.page.waitForLoadState("domcontentloaded", { timeout: 10000 }).catch(() => {});
  }
  await state.page.waitForTimeout(2500);

  const body = await readBody(10000);
  if (navigationError && !body) {
    const result = {
      status: "navigation-failed",
      reason: navigationError,
      candidate,
      url: state.page.url(),
    };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  if (isHardBlocker(state.page.url(), body)) {
    const result = {
      status: "blocked",
      reason: "checkpoint-login-or-limit",
      candidate,
      url: state.page.url(),
      body: cleanText(body).slice(0, 1500),
    };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  if (!bodyContainsCandidateName(body, candidate.name)) {
    const result = {
      status: "identity-mismatch",
      reason: "loaded lead page does not contain candidate name",
      candidate,
      url: state.page.url(),
      body: cleanText(body).slice(0, 1500),
    };
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  const before = await menuLabelsForCurrentLead();
  const result = {
    candidate,
    dryRun,
    url: state.page.url(),
    before,
    send: null,
    after: null,
    status: "unknown",
  };

  if (before.state === "already-pending") {
    result.status = "already-pending";
  } else if (before.state !== "connectable") {
    result.status = `not-connectable:${before.state}`;
  } else if (dryRun) {
    result.status = "dry-run-connectable";
  } else {
    await clickCurrentMenuConnect(before.menuId);
    await state.page.waitForTimeout(1200);
    result.send = await clickSendInvitation(candidate);
    if (result.send.status === "email-required") {
      result.status = "email-required";
      result.after = { state: "email-required", labels: [] };
    } else if (result.send.status === "identity-mismatch") {
      result.status = "identity-mismatch";
      result.after = { state: "identity-mismatch", labels: [], body: result.send.body };
    } else if (result.send.status === "send-button-missing") {
      result.status = "unverified:send-button-missing";
      result.after = await menuLabelsForCurrentLead().catch((error) => ({
        state: "verify-error",
        labels: [],
        error: String(error).slice(0, 500),
      }));
    } else {
      result.after = await verifyAfterSend();
      if (result.after.state === "already-pending") {
        result.status = "pending-verified";
      } else if (result.after.state === "blocked") {
        result.status = "blocked";
      } else if (result.after.state === "email-required") {
        result.status = "email-required";
      } else {
        result.status = `unverified:${result.send.status}`;
      }
    }
  }

  await state.page.keyboard.press("Escape").catch(() => {});
  fs.writeFileSync(out, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
}

await main();
