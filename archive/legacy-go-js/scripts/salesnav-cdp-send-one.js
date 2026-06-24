#!/usr/bin/env bun
const fs = require("node:fs");
const { clickCenter, connectToTarget, navigate, parseArgs, readPort, waitFor } = require("./salesnav-cdp-lib.js");

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function classifyMenu(labels) {
  const texts = labels.map((label) => label.text).filter(Boolean);
  if (texts.some((text) => /^Connect\s*[—-]\s*Pending$/i.test(text) || /^Pending$/i.test(text))) return "already-pending";
  if (texts.some((text) => /^Connect$/i.test(text))) return "connectable";
  return "unknown";
}

function parseCandidate(args) {
  if (args.candidateJson) return JSON.parse(args.candidateJson);
  if (args.candidateFile) return JSON.parse(fs.readFileSync(args.candidateFile, "utf8"));
  if (args.profileUrl || args.profile_url) {
    return {
      source: args.source,
      name: args.name,
      profile_url: args.profileUrl || args.profile_url,
    };
  }
  throw new Error("candidate required via --candidate-json, --candidate-file, or --profile-url");
}

async function menuLabelsForCurrentLead(cdp) {
  const out = await cdp.evaluate(`(async () => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    const trigger =
      document.querySelector('button[aria-label="Open actions overflow menu"]') ||
      document.querySelector('button[aria-label*="Open actions"]') ||
      Array.from(document.querySelectorAll("button")).find((button) => /Open actions/i.test(button.getAttribute("aria-label") || ""));
    if (!trigger) return { state: "missing-trigger", labels: [] };
    const menuId = trigger.getAttribute("aria-controls");
    trigger.click();
    await new Promise((resolve) => setTimeout(resolve, 650));
    const menu = menuId ? document.getElementById(menuId) : Array.from(document.querySelectorAll("[data-popper-placement], [role=menu], [id^='hue-menu']")).at(-1);
    if (!menu) return { state: "missing-menu", labels: [] };
    const labels = Array.from(menu.querySelectorAll("button,a,[role=menuitem]")).map((item, index) => ({
      index,
      text: (item.innerText || item.textContent || "").replace(/\\s+/g, " ").trim(),
      aria: item.getAttribute("aria-label"),
      tag: item.tagName,
      href: item.href || null,
      disabled: item.hasAttribute("disabled"),
    })).filter((item) => item.text || item.aria);
    return { state: "opened", labels, menuId };
  })()`);
  return { ...out, state: classifyMenu(out.labels || []), rawState: out.state };
}

async function clickCurrentMenuConnect(cdp, menuId) {
  const clicked = await clickCenter(cdp, `(() => {
    const menu = ${JSON.stringify(menuId)} ? document.getElementById(${JSON.stringify(menuId)}) : Array.from(document.querySelectorAll("[data-popper-placement], [role=menu], [id^='hue-menu']")).at(-1);
    if (!menu) return null;
    return Array.from(menu.querySelectorAll("button,a,[role=menuitem]"))
      .find((element) => (element.innerText || element.textContent || "").replace(/\\s+/g, " ").trim() === "Connect") || null;
  })()`);
  if (!clicked) throw new Error("connect-menu-item-missing");
}

async function clickSendInvitation(cdp) {
  const bodyState = await cdp.evaluate(`(() => {
    const body = document.body.innerText || "";
    return { body: body.slice(0, 1500), emailRequired: /email address|required email|email\\s+is\\s+required|required\\s+to\\s+connect|enter.*email/i.test(body) };
  })()`);
  if (bodyState.emailRequired) {
    return { status: "email-required", detail: "email required in invite flow" };
  }
  const clicked = await clickCenter(cdp, `(() => {
    const candidates = Array.from(document.querySelectorAll("button"));
    return candidates.find((element) => {
      const text = (element.innerText || element.textContent || "").replace(/\\s+/g, " ").trim();
      const rect = element.getBoundingClientRect();
      const visible = rect.width > 0 && rect.height > 0;
      return visible && /^(Send Invitation|Send invite|Send now|Send)$/.test(text);
    }) || null;
  })()`);
  if (!clicked) {
    return { status: "send-button-missing", detail: cleanText(bodyState.body).slice(0, 1000) };
  }
  return { status: "clicked-send", label: clicked.text };
}

async function main() {
  const args = parseArgs();
  const port = readPort(args.port, args.profile);
  const candidate = parseCandidate(args);
  const out = args.out || "/tmp/linkedin-network-run-cdp-send-result.json";
  const dryRun = args.dryRun === true || args.dryRun === "true" || (!args.allowSend && args.allowSend !== "true");
  const allowSend = args.allowSend === true || args.allowSend === "true";
  if (!dryRun && !allowSend) throw new Error("real send requires --allow-send true");
  if (!candidate.profile_url) throw new Error("candidate.profile_url is required");

  const cdp = await connectToTarget({ port, targetUrlIncludes: "linkedin.com" });
  try {
    await navigate(cdp, candidate.profile_url);
    await waitFor(
      cdp,
      `(() => {
        const text = document.body?.innerText || "";
        return {
          ready: /Open actions|Connect|Message|Sales Navigator|sign in|checkpoint|security verification|weekly invitation limit/i.test(text),
          url: location.href,
          sample: text.slice(0, 1200),
        };
      })()`,
      { timeout: 45000, interval: 1000 },
    );
    const body = await cdp.evaluate(`(() => ({ url: location.href, text: (document.body.innerText || "").slice(0, 1500) }))()`);
    if (/checkpoint|security verification|weekly invitation limit|sign in|uas\/login/i.test(`${body.url}\n${body.text}`)) {
      const result = { status: "blocked", reason: "checkpoint-login-or-limit", candidate, url: body.url, body: cleanText(body.text) };
      fs.writeFileSync(out, JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
      return;
    }

    const before = await menuLabelsForCurrentLead(cdp);
    const result = { candidate, dryRun, url: body.url, before, send: null, after: null, status: "unknown" };
    if (before.state === "already-pending") {
      result.status = "already-pending";
    } else if (before.state !== "connectable") {
      result.status = `not-connectable:${before.state}`;
    } else if (dryRun) {
      result.status = "dry-run-connectable";
    } else {
      await clickCurrentMenuConnect(cdp, before.menuId);
      await new Promise((resolve) => setTimeout(resolve, 1200));
      result.send = await clickSendInvitation(cdp);
      await new Promise((resolve) => setTimeout(resolve, 3000));
      result.after = await menuLabelsForCurrentLead(cdp);
      if (result.after.state === "already-pending") {
        result.status = "pending-verified";
      } else if (result.send.status === "email-required") {
        result.status = "email-required";
      } else {
        result.status = `unverified:${result.send.status}`;
      }
    }
    await cdp.evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))`).catch(() => {});
    fs.writeFileSync(out, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } finally {
    cdp.close();
  }
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
