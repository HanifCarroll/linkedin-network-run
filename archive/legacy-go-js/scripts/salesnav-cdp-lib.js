const fs = require("node:fs");

function parseArgs(argv = process.argv.slice(2)) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith("--")) {
      continue;
    }
    const key = item.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      index += 1;
    }
  }
  return args;
}

function readPort(explicitPort, profilePath) {
  if (explicitPort) {
    return Number(explicitPort);
  }
  const profile =
    profilePath ||
    "/Users/hanifcarroll/Library/Application Support/agent-browser/browser-profiles/linkedin-sales-nav";
  const activePort = fs.readFileSync(`${profile}/DevToolsActivePort`, "utf8").split(/\n/)[0];
  return Number(activePort);
}

async function normalizeWsData(data) {
  if (typeof data === "string") {
    return data;
  }
  if (data instanceof ArrayBuffer) {
    return new TextDecoder().decode(data);
  }
  if (data && typeof data.text === "function") {
    return await data.text();
  }
  return String(data);
}

async function connectToTarget({ port, targetUrlIncludes, preferBlank = false }) {
  const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) => response.json());
  const pages = targets.filter((target) => target.type === "page");
  const target =
    (targetUrlIncludes && pages.find((page) => page.url.includes(targetUrlIncludes))) ||
    (!preferBlank && pages.find((page) => page.url.includes("linkedin.com"))) ||
    pages.find((page) => page.url === "about:blank") ||
    pages[0];
  if (!target) {
    throw new Error(`No CDP page targets available on port ${port}`);
  }

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();

  ws.onmessage = async (event) => {
    const message = JSON.parse(await normalizeWsData(event.data));
    if (message.id && pending.has(message.id)) {
      pending.get(message.id).resolve(message);
      pending.delete(message.id);
    }
  };

  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
    setTimeout(() => reject(new Error("CDP websocket open timeout")), 5000);
  });

  function send(method, params = {}, timeout = 20000) {
    const messageId = ++id;
    ws.send(JSON.stringify({ id: messageId, method, params }));
    return new Promise((resolve, reject) => {
      pending.set(messageId, { resolve, reject });
      setTimeout(() => {
        if (pending.has(messageId)) {
          pending.delete(messageId);
          reject(new Error(`${method} timeout`));
        }
      }, timeout);
    });
  }

  async function evaluate(expression, timeout = 20000) {
    const response = await send(
      "Runtime.evaluate",
      { expression, returnByValue: true, awaitPromise: true, timeout },
      timeout + 1000,
    );
    if (response.result?.exceptionDetails) {
      throw new Error(JSON.stringify(response.result.exceptionDetails));
    }
    return response.result?.result?.value;
  }

  await send("Page.enable");
  await send("Runtime.enable");

  return {
    target,
    send,
    evaluate,
    close() {
      ws.close();
    },
  };
}

async function waitFor(cdp, expression, { timeout = 30000, interval = 500 } = {}) {
  const startedAt = Date.now();
  let lastValue = null;
  while (Date.now() - startedAt < timeout) {
    lastValue = await cdp.evaluate(expression);
    if (lastValue && (!Object.prototype.hasOwnProperty.call(lastValue, "ready") || lastValue.ready)) {
      return lastValue;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw new Error(`Timed out waiting for condition. Last value: ${JSON.stringify(lastValue)}`);
}

async function navigate(cdp, url) {
  await cdp.send("Page.navigate", { url }, 30000);
}

async function clickCenter(cdp, expression, timeout = 20000) {
  const target = await cdp.evaluate(`(() => {
    const element = (${expression});
    if (!element) return null;
    element.scrollIntoView({ block: "center", inline: "center" });
    const rect = element.getBoundingClientRect();
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      width: rect.width,
      height: rect.height,
      text: (element.innerText || element.textContent || "").replace(/\\s+/g, " ").trim(),
      aria: element.getAttribute("aria-label"),
    };
  })()`, timeout);
  if (!target || target.width <= 0 || target.height <= 0) {
    return null;
  }
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: target.x, y: target.y }, timeout);
  await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x: target.x, y: target.y, button: "left", clickCount: 1 }, timeout);
  await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: target.x, y: target.y, button: "left", clickCount: 1 }, timeout);
  return target;
}

module.exports = {
  clickCenter,
  connectToTarget,
  navigate,
  parseArgs,
  readPort,
  waitFor,
};
