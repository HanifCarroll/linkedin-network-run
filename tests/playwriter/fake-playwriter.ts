import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { isAbsolute, resolve, sep } from "node:path";
import {
  SEND_PREPARATION_STATE_KEY,
  SOURCE_CAPTURE_STATE_KEY,
} from "../../src/playwriter/types.ts";

const args = process.argv.slice(2);

if (process.env.FAKE_ARGS_FILE) {
  appendFileSync(process.env.FAKE_ARGS_FILE, `${JSON.stringify(args)}\n`);
}

if (args[0] === "session" && args[1] === "new") {
  console.log('Session 7 created. Use with: playwriter -s 7 -e "..."');
  console.log("");
  console.log(
    "Tip: Need stealth browsing, VPS control, or auto CAPTCHA solving? Run `playwriter cloud login` or set PLAYWRITER_API_KEY",
  );
  console.log("     to control a browser in the cloud instead of local Chrome.");
  process.exit(0);
}

if (args[0] === "session" && args[1] === "list") {
  if (process.env.FAKE_SESSION_EMPTY === "1") {
    console.log("No active sessions");
    process.exit(0);
  }
  const sessions = [
    {
      id: "7",
      browser: "Chrome",
      profile: "hanif@example.com",
      extensionId: "extension-abc",
      cwd: "/Users/hanifcarroll/Library/Application Support/linkedin-tools-next",
      stateKeys: [
        "networkCandidateResultsPage",
        SEND_PREPARATION_STATE_KEY,
        SOURCE_CAPTURE_STATE_KEY,
      ],
    },
    {
      id: "8",
      browser: "Chrome",
      profile: "-",
      extensionId: "-",
      cwd: "-",
      stateKeys: [],
    },
  ];
  const idWidth = Math.max(2, ...sessions.map((session) => session.id.length));
  const browserWidth = Math.max(7, ...sessions.map((session) => session.browser.length));
  const profileWidth = Math.max(7, ...sessions.map((session) => session.profile.length));
  const extensionWidth = Math.max(2, ...sessions.map((session) => session.extensionId.length));
  const cwdWidth = Math.max(3, ...sessions.map((session) => session.cwd.length));
  const stateWidth = Math.max(
    10,
    ...sessions.map((session) => session.stateKeys.join(", ").length || 1),
  );
  console.log(
    "ID".padEnd(idWidth) +
      "  " +
      "BROWSER".padEnd(browserWidth) +
      "  " +
      "PROFILE".padEnd(profileWidth) +
      "  " +
      "EXT".padEnd(extensionWidth) +
      "  " +
      "CWD".padEnd(cwdWidth) +
      "  " +
      "STATE KEYS",
  );
  console.log(
    "-".repeat(idWidth + browserWidth + profileWidth + extensionWidth + cwdWidth + stateWidth + 10),
  );
  for (const session of sessions) {
    const stateKeys = session.stateKeys.length > 0 ? session.stateKeys.join(", ") : "-";
    console.log(
      session.id.padEnd(idWidth) +
        "  " +
        session.browser.padEnd(browserWidth) +
        "  " +
        session.profile.padEnd(profileWidth) +
        "  " +
        session.extensionId.padEnd(extensionWidth) +
        "  " +
        session.cwd.padEnd(cwdWidth) +
        "  " +
        stateKeys,
    );
  }
  process.exit(0);
}

if (args[0] === "session" && args[1] === "reset") {
  if (args[2] !== "7") process.exit(3);
  console.log(
    "Connection reset successfully. 3 page(s) available. Current page URL: https://www.linkedin.com/sales/search/people?savedSearchId=42",
  );
  process.exit(0);
}

if (process.env.FAKE_TRANSPORT_FAIL_BEFORE_EXEC === "1") {
  console.error("Error: Cannot connect to relay server.");
  process.exit(1);
}

type Kind =
  | "page"
  | "row"
  | "marker"
  | "lead"
  | "trigger"
  | "menu"
  | "connect"
  | "dialog"
  | "toast"
  | "send"
  | "email"
  | "pending"
  | "anchor"
  | "entry"
  | "withdraw"
  | "results"
  | "pagination"
  | "current-page"
  | "next"
  | "progressbar"
  | "alert"
  | "workspace";

interface FixtureNode {
  readonly kind: Kind;
  readonly attrs?: Record<string, string>;
  readonly text?: string;
  readonly disabled?: boolean;
  readonly visible?: boolean;
}

const candidateUrl =
  process.env.FAKE_PAGE_URL ??
  "https://www.linkedin.com/sales/search/people?savedSearchId=1980870185";
const catalogUrl = "https://www.linkedin.com/sales/search/people";
const sentUrl = "https://www.linkedin.com/mynetwork/invitation-manager/sent/";
const fixture = {
  menuOpen: process.env.FAKE_MENU_OPEN === "1",
  menuPending: process.env.FAKE_MENU_PENDING === "1",
  modalOpen: process.env.FAKE_MODAL_OPEN === "1",
  pending: process.env.FAKE_PENDING === "1",
  pageCursor: process.env.FAKE_CURSOR ?? "Page 3",
  nextDisabled: process.env.FAKE_TERMINAL === "1",
};
const recordOrder = (entry: string) => {
  if (process.env.FAKE_ORDER_FILE) appendFileSync(process.env.FAKE_ORDER_FILE, `${entry}\n`);
};

const row: FixtureNode = { kind: "row" };
const results: FixtureNode = {
  kind: "results",
  attrs: { "aria-busy": process.env.FAKE_RESULTS_LOADING === "1" ? "true" : "false" },
  visible: process.env.FAKE_RESULTS_VISIBLE !== "0",
};
const marker: FixtureNode = {
  kind: "marker",
  attrs: {
    "data-scroll-into-view": process.env.FAKE_ROW_IDENTITY ?? "urn:li:fs_salesProfile:ACwAA123",
  },
};
const lead: FixtureNode = {
  kind: "lead",
  attrs: { href: process.env.FAKE_LEAD_HREF ?? "/sales/lead/ACwAA123" },
  text: "Ada Lovelace",
};
const trigger: FixtureNode = {
  kind: "trigger",
  attrs: {
    "aria-label": "See more actions for Ada Lovelace",
    "aria-controls": "menu-ada",
  },
};
const menu: FixtureNode = { kind: "menu" };
const connect: FixtureNode = { kind: "connect", text: "Connect" };
const dialog: FixtureNode = { kind: "dialog" };
const toast: FixtureNode = { kind: "toast", text: "Saved" };
const emailInput: FixtureNode = { kind: "email", attrs: { type: "email", name: "email" } };
const sendNodes: FixtureNode[] = Array.from(
  { length: Number(process.env.FAKE_SEND_COUNT ?? "1") },
  () => ({
    kind: "send",
    text: process.env.FAKE_SEND_NAME ?? "Send",
    disabled: process.env.FAKE_SEND_DISABLED === "1",
    visible: process.env.FAKE_SEND_VISIBLE !== "0",
  }),
);
const anchors: FixtureNode[] = [
  {
    kind: "anchor",
    text: "Consulting - HubSpot B2B RevOps",
    attrs: { href: "/sales/search/people?savedSearchId=1980870185" },
  },
  {
    kind: "anchor",
    text: "3 new results for Consulting - HubSpot B2B RevOps",
    attrs: { href: "/sales/search/people?savedSearchId=1980870185&lastViewedAt=1" },
  },
];
const pagination: FixtureNode = { kind: "pagination", text: "Pagination" };
const currentPage = (): FixtureNode => ({
  kind: "current-page",
  attrs: { "aria-current": "page", "aria-label": fixture.pageCursor },
});
const nextNode = (): FixtureNode => ({
  kind: "next",
  text: "Next",
  disabled: fixture.nextDisabled,
});
const paginationVisible = process.env.FAKE_TERMINAL === "1" || process.env.FAKE_PAGINATION === "1";
const sourceRows = process.env.FAKE_EMPTY_RESULTS === "1" ? [] : [row];
const progressbar: FixtureNode = { kind: "progressbar", text: "Loading search results" };
const alert: FixtureNode = { kind: "alert", text: "Account restriction" };
const workspace: FixtureNode = { kind: "workspace", text: "People (483)" };

function matches(node: FixtureNode, selector: string): boolean {
  if (selector === "#search-results-container") return node.kind === "results";
  if (selector === "li.artdeco-list__item:has(a[href*='/sales/lead/'])") return node.kind === "row";
  if (selector === "li.artdeco-list__item") return node.kind === "row";
  if (selector === "main#workspace") return node.kind === "workspace";
  if (selector === "[aria-label^='Withdraw invitation sent to ']") return node.kind === "withdraw";
  if (selector === "[aria-current='page']") return node.kind === "current-page";
  if (selector === "[data-scroll-into-view]") return node.kind === "marker";
  if (selector === "[role='dialog'], .artdeco-modal, [data-test-modal]")
    return (
      (node.kind === "dialog" && fixture.modalOpen) ||
      (node.kind === "toast" && process.env.FAKE_TOAST_DIALOG === "1")
    );
  if (selector === "button") return node.kind === "send";
  if (selector === "input[type='email'], input[name*='email' i]") {
    if (node.kind !== "email") return false;
    return process.env.FAKE_DIALOG_EMAIL === "1" || process.env.FAKE_PAGE_EMAIL === "1";
  }
  if (selector.startsWith("[data-scroll-into-view="))
    return (
      node.kind === "marker" && selector.includes(node.attrs?.["data-scroll-into-view"] ?? "!")
    );
  if (selector.includes("a[href*='/sales/lead/']")) return node.kind === "lead";
  if (selector.startsWith('a[href="/sales/lead/'))
    return node.kind === "lead" && selector.includes(node.attrs?.href ?? "!");
  if (
    selector === 'button[aria-label^="See more actions for"]' ||
    selector === "button[aria-label^='See more actions for']"
  )
    return node.kind === "trigger";
  if (selector.startsWith("button[aria-label="))
    return node.kind === "trigger" && selector.includes(node.attrs?.["aria-label"] ?? "!");
  if (selector === "#menu-ada") return node.kind === "menu" && fixture.menuOpen;
  if (selector === "button,a,[role=menuitem]")
    return node.kind === "connect" || node.kind === "pending";
  if (selector.includes("savedSearchId=")) return node.kind === "anchor";
  return false;
}

function children(node: FixtureNode): FixtureNode[] {
  if (node.kind === "page")
    return [
      results,
      ...sourceRows,
      ...anchors,
      { kind: "entry", text: "Ada Lovelace" },
      workspace,
      ...(paginationVisible ? [pagination] : []),
      ...(process.env.FAKE_RESULTS_LOADING === "1" ? [progressbar] : []),
      ...(process.env.FAKE_SOURCE_BLOCKER === "1" ? [alert] : []),
      ...(process.env.FAKE_PAGE_EMAIL === "1" ? [emailInput] : []),
      ...(process.env.FAKE_TOAST_DIALOG === "1" ? [toast] : []),
      ...(fixture.modalOpen ? [dialog] : []),
    ];
  if (node.kind === "results") return sourceRows;
  if (node.kind === "row")
    return [
      marker,
      lead,
      trigger,
      ...(fixture.pending ? [{ kind: "pending", text: "Pending" } as FixtureNode] : []),
    ];
  if (node.kind === "menu")
    return fixture.menuPending ? [{ kind: "pending", text: "Connect — Pending" }] : [connect];
  if (node.kind === "dialog")
    return [...sendNodes, ...(process.env.FAKE_DIALOG_EMAIL === "1" ? [emailInput] : [])];
  if (node.kind === "toast") return [];
  if (node.kind === "entry")
    return [
      {
        kind: "withdraw",
        text: "Withdraw",
        attrs: { "aria-label": "Withdraw invitation sent to Ada Lovelace" },
      },
    ];
  if (node.kind === "pagination") return [currentPage(), nextNode()];
  return [];
}

class Locator {
  constructor(readonly nodes: readonly FixtureNode[]) {}

  locator(selector: string): Locator {
    return new Locator(this.nodes.flatMap(children).filter((node) => matches(node, selector)));
  }

  getByRole(role: string, options?: { name: string; exact?: boolean }): Locator {
    let pool = this.nodes.flatMap(children);
    if (this.nodes.some((node) => node.kind === "page") && role === "dialog")
      pool = fixture.modalOpen ? [dialog] : [];
    if (
      this.nodes.some((node) => node.kind === "page") &&
      role === "button" &&
      options?.name === "Send"
    )
      pool = fixture.modalOpen ? sendNodes : [];
    const byRole: Readonly<Record<string, readonly Kind[]>> = {
      button: ["trigger", "connect", "send", "withdraw", "next"],
      link: ["lead", "anchor"],
      listitem: ["entry"],
      dialog: ["dialog"],
      navigation: ["pagination"],
      progressbar: ["progressbar"],
      alert: ["alert"],
    };
    return new Locator(
      pool.filter(
        (node) =>
          (byRole[role] ?? []).includes(node.kind) &&
          (options === undefined || node.text === options.name),
      ),
    );
  }

  getByText(text: string): Locator {
    return new Locator(this.nodes.flatMap(children).filter((node) => node.text === text));
  }

  async count(): Promise<number> {
    if (this.nodes.some((node) => node.kind === "send")) recordOrder("send_unique_validated");
    return this.nodes.length;
  }

  async all(): Promise<Locator[]> {
    return this.nodes.map((node) => new Locator([node]));
  }

  async evaluateAll(fn: (nodes: unknown[]) => unknown): Promise<unknown> {
    const nodes = this.nodes.map((node) => ({
      getAttribute: (key: string) => node.attrs?.[key] ?? null,
      textContent: node.text ?? "",
      closest: (_selector: string) => null,
      querySelector: (_selector: string) => null,
      querySelectorAll: (_selector: string) => [],
    }));
    return fn(nodes);
  }

  first(): Locator {
    return new Locator(this.nodes.slice(0, 1));
  }

  last(): Locator {
    return new Locator(this.nodes.slice(-1));
  }

  async getAttribute(key: string): Promise<string | null> {
    return this.nodes[0]?.attrs?.[key] ?? null;
  }

  async textContent(): Promise<string> {
    return this.nodes[0]?.text ?? "";
  }

  async isDisabled(): Promise<boolean> {
    if (this.nodes.some((node) => node.kind === "send")) recordOrder("send_enabled_validated");
    return this.nodes[0]?.disabled ?? false;
  }

  async isVisible(): Promise<boolean> {
    if (this.nodes.some((node) => node.kind === "send")) recordOrder("send_visible_validated");
    if (this.nodes.some((node) => node.kind === "toast")) return true;
    return this.nodes[0]?.visible ?? true;
  }

  async scrollIntoViewIfNeeded(): Promise<void> {}

  async click(): Promise<void> {
    const node = this.nodes[0];
    if (node === undefined) throw new Error("SELECTOR_CONTRACT");
    if (node.kind === "send" && process.env.FAKE_CLICK_THROW_BEFORE === "1")
      throw new Error("click transport failed before dispatch confirmation");
    recordOrder(`${node.kind}_click`);
    if (node.kind === "trigger") fixture.menuOpen = true;
    if (node.kind === "connect") fixture.modalOpen = true;
    if (node.kind === "send") {
      fixture.modalOpen = false;
      fixture.pending = true;
      if (process.env.FAKE_CLICK_THROW_AFTER === "1")
        throw new Error("click transport failed after dispatch");
    }
    if (node.kind === "next") {
      const match = /^Page ([1-9][0-9]*)$/.exec(fixture.pageCursor);
      const page = match ? Number(match[1]) + 1 : 2;
      fixture.pageCursor = `Page ${page}`;
      // Keep Next enabled for non-terminal pagination walks so capture stops on the page budget.
      if (process.env.FAKE_TERMINAL === "1") fixture.nextDisabled = true;
      recordOrder("pagination_next");
    }
  }
}

class FakePage {
  constructor(private current: string) {}

  readonly keyboard = { press: async (_key: string): Promise<void> => {} };

  /** Compiled scripts may arm response listeners (e.g. salesApiLeadSearch capture). */
  on(_event: string, _listener: unknown): void {}

  url(): string {
    return this.current;
  }

  async goto(url: string): Promise<null> {
    this.current = url;
    return null;
  }

  async waitForTimeout(_milliseconds: number): Promise<void> {}

  locator(selector: string): Locator {
    if (selector === "[role='dialog'], .artdeco-modal, [data-test-modal]") {
      const dialogs: FixtureNode[] = [];
      if (fixture.modalOpen) dialogs.push(dialog);
      if (process.env.FAKE_TOAST_DIALOG === "1") dialogs.push(toast);
      return new Locator(dialogs);
    }
    if (selector === "#menu-ada" && fixture.menuOpen) return new Locator([menu]);
    if (selector === "[aria-label^='Withdraw invitation sent to ']")
      return new Locator(children({ kind: "entry" }));
    if (
      selector === "input[type='email'], input[name*='email' i]" &&
      process.env.FAKE_PAGE_EMAIL === "1"
    )
      return new Locator([emailInput]);
    const roots = [{ kind: "page" } as FixtureNode, ...children({ kind: "page" })];
    return new Locator(roots.filter((node) => matches(node, selector)));
  }

  getByRole(role: string, options?: { name: string; exact?: boolean }): Locator {
    return new Locator([{ kind: "page" }]).getByRole(role, options);
  }

  getByText(text: string, options?: unknown): Locator {
    void options;
    return new Locator([{ kind: "page" }]).getByText(text);
  }

  async evaluate(
    _fn: unknown,
    _selector?: string,
  ): Promise<{ before: number; after: number; max: number }> {
    return { before: 0, after: 0, max: 0 };
  }
}

const candidatePage = new FakePage(candidateUrl);
const catalogPage = new FakePage(catalogUrl);
const sentPage = new FakePage(sentUrl);
const stateFile = process.env.FAKE_STATE_FILE;
const persisted =
  stateFile && existsSync(stateFile)
    ? (JSON.parse(readFileSync(stateFile, "utf8")) as Record<string, unknown>)
    : {};
const state: Record<string, unknown> = {
  networkCandidateResultsPage: candidatePage,
  networkSavedSearchCatalogPage: catalogPage,
  networkSentInvitationsPage: sentPage,
  ...(persisted[SEND_PREPARATION_STATE_KEY] === undefined
    ? {}
    : { [SEND_PREPARATION_STATE_KEY]: persisted[SEND_PREPARATION_STATE_KEY] }),
  ...(persisted[SOURCE_CAPTURE_STATE_KEY] === undefined
    ? {}
    : { [SOURCE_CAPTURE_STATE_KEY]: persisted[SOURCE_CAPTURE_STATE_KEY] }),
};
if (process.env.FAKE_SOURCE_STATE_MISMATCH === "1")
  state[SOURCE_CAPTURE_STATE_KEY] = {
    schemaVersion: 1,
    kind: "network_source_capture_state",
    bySource: {
      "hubspot-b2b-revops": {
        schemaVersion: 1,
        kind: "network_source_reload",
        sourceId: "hubspot-b2b-revops",
        searchUrl: candidateUrl,
        sourceContractFingerprint: "0".repeat(64),
        navigationInvocationId: "pw_wrong_reload",
        reloadIdentity: "pw_wrong_reload:reload",
        reloadGeneration: 1,
        navigatedAt: "2026-08-03T12:00:00.000Z",
      },
    },
  };
const context = { pages: () => [candidatePage, catalogPage, sentPage] };
const getLatestLogs = async () => {
  switch (process.env.FAKE_DIAGNOSTIC_MODE) {
    case "repeated-generic":
      return Array.from({ length: 5_000 }, () => ({
        level: "error",
        message: "Failed to load resource: net::ERR_FAILED",
      }));
    case "huge-generic":
      return [{ level: "error", message: `net::ERR_FAILED ${"x".repeat(1_000_000)}` }];
    case "terminal":
      return [
        { level: "error", message: "429 Too Many Requests" },
        { level: "error", message: "linkedin.com/checkpoint security verification" },
        { level: "error", message: "net::ERR_CONNECTION_REFUSED" },
      ];
    case "many-other":
      return Array.from({ length: 100 }, (_, index) => ({
        level: "info",
        message: `diagnostic ${index}`,
      }));
    case "oversized-terminal":
      return [{ level: "error", message: `checkpoint ${"x".repeat(140_000)}` }];
    case "malformed": {
      const cyclic: Record<string, unknown> = {};
      cyclic.self = cyclic;
      return [cyclic];
    }
    default:
      return [{ level: "info", message: "fixture log" }];
  }
};
const baseRequire = createRequire(import.meta.url);
const sessionCreationCwd = resolve(process.env.FAKE_SESSION_CREATION_CWD ?? process.cwd());
const allowedRoots = [sessionCreationCwd, resolve(tmpdir())];
const withinAllowedRoot = (path: string) => {
  const absolute = resolve(path);
  return (
    isAbsolute(path) &&
    allowedRoots.some((root) => absolute === root || absolute.startsWith(`${root}${sep}`))
  );
};
const runtimeRequire = (id: string) => {
  const loaded = baseRequire(id);
  if (id !== "node:fs" && id !== "fs") return loaded;
  return {
    ...loaded,
    appendFileSync(path: string, data: string) {
      if (!withinAllowedRoot(path)) throw new Error(`PLAYWRITER_FS_SANDBOX ${path}`);
      return appendFileSync(path, data);
    },
  };
};

const FakeDate = class extends Date {
  constructor(value?: string | number) {
    super(value ?? process.env.FAKE_NOW ?? Date.now());
  }

  static override now(): number {
    return process.env.FAKE_NOW === undefined ? Date.now() : Date.parse(process.env.FAKE_NOW);
  }
};

const consoleLogs: Array<{ method: string; args: unknown[] }> = [];
const runtimeConsole = {
  log: (...values: unknown[]) => consoleLogs.push({ method: "log", args: values }),
  info: (...values: unknown[]) => consoleLogs.push({ method: "info", args: values }),
  warn: (...values: unknown[]) => consoleLogs.push({ method: "warn", args: values }),
  error: (...values: unknown[]) => consoleLogs.push({ method: "error", args: values }),
  debug: (...values: unknown[]) => consoleLogs.push({ method: "debug", args: values }),
};

function formatLogs(prefix = "Console output"): string {
  if (consoleLogs.length === 0) return "";
  const lines = consoleLogs.map(
    ({ method, args: values }) =>
      `[${method}] ${values.map((value) => (typeof value === "string" ? value : JSON.stringify(value))).join(" ")}`,
  );
  return `${prefix}:\n${lines.join("\n")}\n\n`;
}

function persistState(): void {
  if (!stateFile) return;
  const serializable: Record<string, unknown> = {};
  if (state[SEND_PREPARATION_STATE_KEY] !== undefined)
    serializable[SEND_PREPARATION_STATE_KEY] = state[SEND_PREPARATION_STATE_KEY];
  if (state[SOURCE_CAPTURE_STATE_KEY] !== undefined)
    serializable[SOURCE_CAPTURE_STATE_KEY] = state[SOURCE_CAPTURE_STATE_KEY];
  writeFileSync(stateFile, `${JSON.stringify(serializable, null, 2)}\n`);
}

const source = args.at(args.indexOf("-e") + 1) ?? "";
if (process.env.FAKE_EVALUATION_CWD) process.chdir(process.env.FAKE_EVALUATION_CWD);
if (process.env.FAKE_MALFORMED_PROGRESS === "before") writeFileSync("progress.jsonl", "{bad\n");

try {
  const run = new Function(
    "page",
    "context",
    "state",
    "getLatestLogs",
    "console",
    "require",
    "Date",
    `return (async()=>{${source}})()`,
  );
  const returned = await run(
    candidatePage,
    context,
    state,
    getLatestLogs,
    runtimeConsole,
    runtimeRequire,
    FakeDate,
  );
  persistState();
  if (process.env.FAKE_TRANSPORT_FAIL_AFTER_EXEC === "1") {
    console.error("Error: relay transport closed after execution");
    process.exit(1);
  }
  if (process.env.FAKE_PLAYWRITER_EXIT !== undefined) {
    console.error(process.env.FAKE_BROWSER_ERROR ?? "injected relay failure");
    process.exit(Number(process.env.FAKE_PLAYWRITER_EXIT));
  }
  if (process.env.FAKE_OUTPUT_MODE === "malformed") {
    console.log("[return value] not-json");
    process.exit(0);
  }
  let output = formatLogs();
  if (returned !== undefined)
    output +=
      process.env.FAKE_RESULT_FRAME === "console"
        ? `Console output:\n[log] ${String(returned)}\n`
        : `[return value] ${String(returned)}\n`;
  if (output.trim().length === 0) output = "Code executed successfully (no output)\n";
  process.stdout.write(output);
} catch (error) {
  persistState();
  const message = error instanceof Error ? error.message : String(error);
  const logs = formatLogs("Console output (before error)");
  console.error(
    `${logs}\nError executing code: ${message}\n\n[HINT: If this is an internal Playwright error, page/browser closed, or connection issue, call reset to reconnect.]`,
  );
  process.exit(1);
}
