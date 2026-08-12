import { afterEach, describe, expect, test } from "bun:test";
import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  realpath,
  rm,
  stat,
  symlink,
  unlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { parseSourceCapture } from "../../src/network/results.ts";
import {
  ANALYTICS_COMMANDS,
  assertInvocationEvidence,
  assertSourceExhaustionEvidence,
  captureNetworkSource,
  commandTimeoutMs,
  COMMAND_TIMEOUT_MS,
  commitNetworkSend,
  compileGenericAdapter,
  compileNetworkScript,
  detectBlocker,
  isAllowedWorkflowUrl,
  materializeCompiledScript,
  NETWORK_COMMANDS,
  PLAYWRITER_DEFAULT_TIMEOUT_MS,
  PlaywriterClient,
  type PlaywriterClientOptions,
  prepareNetworkSend,
  resolveNetworkSourceContract,
  SEND_PREPARATION_STATE_KEY,
  SOURCE_CAPTURE_STATE_KEY,
  SOURCE_CAPTURE_TIMEOUT_MS,
} from "../../src/playwriter/index.ts";

const fake = new URL("./fake-playwriter.ts", import.meta.url).pathname;
const dirs: string[] = [];
const candidate = {
  sourceName: "Consulting - HubSpot B2B RevOps",
  savedSearchId: "1980870185",
  searchUrl: "https://www.linkedin.com/sales/search/people?savedSearchId=1980870185",
  salesLeadUrl: "https://www.linkedin.com/sales/lead/ACwAA123",
  salesLeadId: "ACwAA123",
  name: "Ada Lovelace",
  rowIdentity: "urn:li:fs_salesProfile:ACwAA123",
} as const;

async function root(): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), "pw-contract-"));
  const canonical = await realpath(directory);
  dirs.push(canonical);
  return canonical;
}

afterEach(async () => {
  await Promise.all(
    dirs.splice(0).map(async (directory) => {
      for (const entry of await readdir(directory).catch(() => []))
        await chmod(join(directory, entry), 0o755).catch(() => {});
      await chmod(directory, 0o755).catch(() => {});
      await rm(directory, { recursive: true, force: true });
    }),
  );
});

function client(
  invocationRoot: string,
  prefix: string,
  env: Record<string, string | undefined> = {},
  options: Pick<PlaywriterClientOptions, "beforeHandoffValidation" | "createHandoffNonce"> = {},
): PlaywriterClient {
  let sequence = 0;
  const fixedNow = env.FAKE_NOW;
  return new PlaywriterClient({
    executable: process.execPath,
    executableArgs: [fake],
    invocationRoot,
    createInvocationId: () => `pw_${prefix}_${String(++sequence).padStart(2, "0")}`,
    env: {
      FAKE_STATE_FILE: join(invocationRoot, "fake-session.json"),
      ...env,
    },
    ...options,
    ...(fixedNow === undefined ? {} : { now: () => new Date(fixedNow) }),
  });
}

const ordinaryCommands = NETWORK_COMMANDS.filter(
  (command) => !["click-send", "prepare-send", "commit-send"].includes(command),
);

const inputFor = (command: (typeof ordinaryCommands)[number]) => {
  const base = {
    url: command.includes("sent")
      ? "https://www.linkedin.com/mynetwork/invitation-manager/sent/"
      : candidate.searchUrl,
    ...([
      "capture-candidate",
      "click-connect-menu-item",
      "observe-connect-modal",
      "observe-post-send",
    ].includes(command)
      ? { candidate }
      : {}),
  };
  if (command === "walk-list") {
    return {
      ...base,
      sourceContract: resolveNetworkSourceContract(candidate.searchUrl),
      budget: 5,
      pacingMs: 0,
    };
  }
  if (command === "navigate-candidate-results" || command === "capture-candidate-results") {
    return {
      ...base,
      sourceContract: resolveNetworkSourceContract(candidate.searchUrl),
    };
  }
  return base;
};

async function assertSealed(directory: string): Promise<void> {
  expect((await readdir(directory)).sort()).toEqual([
    "config.json",
    "control.json",
    "diagnostics.json",
    "progress.jsonl",
    "receipt.json",
    "stderr.log",
    "stdout.log",
  ]);
  for (const name of await readdir(directory))
    expect((await stat(join(directory, name))).mode & 0o222).toBe(0);
  expect((await stat(directory)).mode & 0o222).toBe(0);
}

async function readOrder(path: string): Promise<string[]> {
  return (await readFile(path, "utf8")).trim().split("\n").filter(Boolean);
}

describe("real Playwriter 0.4.0 CLI framing", () => {
  test("parses verbose session create/list/reset output", async () => {
    const directory = await root();
    const playwriter = client(directory, "sessions");
    expect(await playwriter.createSession()).toBe(7);
    expect(await playwriter.listSessions()).toEqual([
      {
        id: 7,
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
        id: 8,
        browser: "Chrome",
        profile: null,
        extensionId: null,
        cwd: null,
        stateKeys: [],
      },
    ]);
    await expect(playwriter.resetSession(7)).resolves.toBeUndefined();
  });

  test("accepts the documented empty session list", async () => {
    const directory = await root();
    expect(await client(directory, "empty", { FAKE_SESSION_EMPTY: "1" }).listSessions()).toEqual(
      [],
    );
  });
});

describe("controlled compiler contract", () => {
  test("uses exact URLs and source-row/menu selectors", () => {
    expect(isAllowedWorkflowUrl("candidateResults", candidate.searchUrl)).toBeTrue();
    expect(
      isAllowedWorkflowUrl("savedSearchCatalog", "https://www.linkedin.com/sales/search/people"),
    ).toBeTrue();
    expect(isAllowedWorkflowUrl("candidateResults", `${candidate.searchUrl}&x=1`)).toBeFalse();
    const source = compileNetworkScript("click-connect-menu-item", { candidate }).source;
    expect(source).toContain("li.artdeco-list__item");
    expect(source).toContain("data-scroll-into-view");
    expect(source).toContain("a[href*='/sales/lead/']");
    expect(source).toContain('rowSalesLeadId==="ACwAA123"');
    expect(source).toContain("See more actions for");
    expect(source).not.toContain("data-scroll-urn");
  });

  test("generic adapter accepts only a structured single-action plan", () => {
    expect(() =>
      compileGenericAdapter({
        id: "analytics.capture.v1",
        command: "analytics-capture",
        action: "none",
        phases: [],
        plan: [{ op: "logs" }],
      }),
    ).not.toThrow();
    expect(() =>
      compileGenericAdapter({
        id: "bad.source.v1",
        command: "bad-source",
        action: "none",
        phases: [],
        plan: [{ op: "click_role", role: "button", name: "Export" }, { op: "logs" }],
      }),
    ).toThrow("plan/action mismatch");
    expect(() =>
      compileGenericAdapter({
        id: "bad.send.v1",
        command: "bad-send",
        action: "send",
        phases: [],
        plan: [{ op: "logs" }],
      }),
    ).toThrow("sensitive");
  });
  test("walk-list script is API-driven with lead-page sends and pacing", () => {
    const source = compileNetworkScript("walk-list", {
      url: candidate.searchUrl,
      sourceContract: resolveNetworkSourceContract(candidate.searchUrl),
      budget: 5,
      pacingMs: 5000,
    }).source;
    // API-driven: reads the salesApiLeadSearch response, no list scrolling.
    expect(source).toContain("salesApiLeadSearch");
    expect(source).toContain("pendingInvitation");
    expect(source).toContain("replaySearch");
    expect(source).toContain("context.newPage()");
    expect(source).not.toContain("exhaustResultsScroll");
    // Sends on the lead page via the profile actions menu.
    expect(source).toContain("/sales/lead/");
    expect(source).toContain("Open actions overflow menu");
    expect(source).toContain("See more actions for");
    expect(source).toContain("waitForTimeout(pacingMs)");
    expect(source).toContain("sent.push({rowIdentity,name:candidate.name})");
    expect(source).toContain("already_pending");
    expect(source).toContain("email_required");
  });

  test("every command has an explicit Playwriter timeout contract entry", () => {
    for (const command of NETWORK_COMMANDS) {
      expect(COMMAND_TIMEOUT_MS[command], `missing timeout entry for ${command}`).toBeDefined();
    }
    for (const command of ANALYTICS_COMMANDS) {
      expect(COMMAND_TIMEOUT_MS[command], `missing timeout entry for ${command}`).toBeDefined();
    }
    expect(commandTimeoutMs("walk-list")).toBe(SOURCE_CAPTURE_TIMEOUT_MS);
    expect(commandTimeoutMs("capture-candidate-results")).toBe(SOURCE_CAPTURE_TIMEOUT_MS);
    expect(commandTimeoutMs("capture-sent-list")).toBe(SOURCE_CAPTURE_TIMEOUT_MS);
    expect(commandTimeoutMs("capture-candidate")).toBe(SOURCE_CAPTURE_TIMEOUT_MS);
    expect(commandTimeoutMs("prepare-send")).toBe(SOURCE_CAPTURE_TIMEOUT_MS);
    expect(commandTimeoutMs("commit-send")).toBe(SOURCE_CAPTURE_TIMEOUT_MS);
    expect(commandTimeoutMs("navigate-candidate-results")).toBe(PLAYWRITER_DEFAULT_TIMEOUT_MS);
    expect(commandTimeoutMs("navigate-sent-list")).toBe(PLAYWRITER_DEFAULT_TIMEOUT_MS);
    // Unknown commands must fail closed rather than inherit a silent timeout.
    expect(commandTimeoutMs("not-a-command")).toBeNull();
  });

  test("removes the unsupported gate and retires direct click-send", () => {
    const prepare = compileNetworkScript("prepare-send", {
      candidate,
      attemptId: "attempt_0001",
    }).source;
    expect(prepare).not.toMatch(
      /process\.stdin|__LINKEDIN_TOOLS_GATE__|sendClickStartedHook|PERSISTENCE_HOOK|await import/,
    );
    expect(prepare).not.toContain("await send.click()");
    expect(() => compileNetworkScript("click-send", { candidate })).toThrow(
      "prepareNetworkSend then commitNetworkSend",
    );
  });

  test("page email outside the send dialog does not fail prepare", async () => {
    const directory = await root();
    const prepared = await prepareNetworkSend(
      client(directory, "page_email", {
        FAKE_MODAL_OPEN: "1",
        FAKE_PAGE_EMAIL: "1",
        FAKE_MENU_OPEN: "1",
      }),
      7,
      { candidate, attemptId: "attempt_page_email" },
    );
    expect(prepared.invocation.receipt.outcome).toBe("succeeded");
  });

  test("toast dialog without send control does not fail prepare", async () => {
    const directory = await root();
    const prepared = await prepareNetworkSend(
      client(directory, "toast_dialog", {
        FAKE_MODAL_OPEN: "1",
        FAKE_TOAST_DIALOG: "1",
        FAKE_MENU_OPEN: "1",
      }),
      7,
      { candidate, attemptId: "attempt_toast" },
    );
    expect(prepared.invocation.receipt.outcome).toBe("succeeded");
  });

  test("email input inside the send dialog yields EMAIL_REQUIRED", async () => {
    const directory = await root();
    const prepared = await prepareNetworkSend(
      client(directory, "dialog_email", {
        FAKE_MODAL_OPEN: "1",
        FAKE_DIALOG_EMAIL: "1",
        FAKE_MENU_OPEN: "1",
      }),
      7,
      { candidate, attemptId: "attempt_dialog_email" },
    );
    expect(prepared.invocation.receipt.outcome).toBe("failed");
    expect(prepared.invocation.receipt.blocker?.kind).toBe("email_required");
  });

  test("scopes send dialog email checks and ignores toast dialogs", () => {
    const prepare = compileNetworkScript("prepare-send", {
      candidate,
      attemptId: "attempt_0001",
    }).source;
    expect(prepare).toContain("sendDialog");
    expect(prepare).toContain(
      "sendDialog.locator(\"input[type='email'], input[name*='email' i]\")",
    );
    expect(prepare).not.toContain(
      "p.locator(\"input[type='email'], input[name*='email' i]\").first()",
    );
    expect(prepare).toContain("if(modalSend===null)continue");
  });

  test("source capture walks pagination with a bounded page budget", () => {
    const sourceContract = resolveNetworkSourceContract(candidate.searchUrl);
    const capture = compileNetworkScript("capture-candidate-results", {
      url: candidate.searchUrl,
      sourceContract,
    }).source;
    expect(capture).toContain("pageAttempt<10");
    expect(capture).toContain("stagnantPasses");
    expect(capture).toContain('name:"Next"');
    expect(capture).toContain("SOURCE_CAPTURE_INCOMPLETE");
    expect(capture).toContain("visitedCursors");
  });

  test("candidate identity is single-source", async () => {
    const directory = await root();
    await expect(
      client(directory, "identity").invoke({
        sessionId: 7,
        descriptor: compileNetworkScript("capture-candidate", { candidate }),
        candidate: { ...candidate, name: "Grace" },
      }),
    ).rejects.toThrow("identity mismatch");
  });
});

describe("ordinary network commands", () => {
  test("creates a missing configured invocation root before writing evidence", async () => {
    const stateDirectory = await root();
    const invocationRoot = join(stateDirectory, "receipts", "playwriter", "network");
    const invocation = await client(invocationRoot, "missing_root").invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("capture-candidate", { candidate }),
    });

    try {
      expect(invocation.directory).toBe(join(invocationRoot, "pw_missing_root_01"));
      await assertSealed(invocation.directory);
    } finally {
      await chmod(invocation.directory, 0o755);
    }
  });

  test("rejects an existing symlink in an intermediate invocation-root component", async () => {
    const stateDirectory = await root();
    const outside = await root();
    await symlink(outside, join(stateDirectory, "receipts"));

    await expect(
      client(join(stateDirectory, "receipts", "playwriter", "network"), "intermediate_link").invoke(
        {
          sessionId: 7,
          descriptor: compileNetworkScript("capture-candidate", { candidate }),
        },
      ),
    ).rejects.toThrow("must be a real directory");
  });

  test("rejects an existing symlink at the configured invocation root", async () => {
    const stateDirectory = await root();
    const outside = await root();
    await mkdir(join(stateDirectory, "receipts", "playwriter"), { recursive: true });
    await symlink(outside, join(stateDirectory, "receipts", "playwriter", "network"));

    await expect(
      client(join(stateDirectory, "receipts", "playwriter", "network"), "final_link").invoke({
        sessionId: 7,
        descriptor: compileNetworkScript("capture-candidate", { candidate }),
      }),
    ).rejects.toThrow("must be a real directory");
  });

  test("rejects a non-directory invocation-root component", async () => {
    const stateDirectory = await root();
    await writeFile(join(stateDirectory, "receipts"), "not a directory");

    await expect(
      client(join(stateDirectory, "receipts", "playwriter", "network"), "file_component").invoke({
        sessionId: 7,
        descriptor: compileNetworkScript("capture-candidate", { candidate }),
      }),
    ).rejects.toThrow("must be a real directory");
  });

  test("enforces restrictive permissions on the configured invocation root", async () => {
    const stateDirectory = await root();
    const invocationRoot = join(stateDirectory, "receipts", "playwriter", "network");
    await mkdir(invocationRoot, { recursive: true, mode: 0o755 });
    await chmod(invocationRoot, 0o755);
    const invocation = await client(invocationRoot, "permissions").invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("capture-candidate", { candidate }),
    });

    try {
      expect((await stat(invocationRoot)).mode & 0o777).toBe(0o700);
      await assertSealed(invocation.directory);
    } finally {
      await chmod(invocation.directory, 0o755);
    }
  });

  test("keeps browser phase evidence isolated when Playwriter evaluates outside the invocation cwd", async () => {
    const invocationRoot = await root();
    const evaluationDirectory = await root();
    const handoffs: Array<{ path: string; directoryMode: number; fileMode: number }> = [];
    const playwriter = client(
      invocationRoot,
      "isolated",
      {
        FAKE_EVALUATION_CWD: evaluationDirectory,
      },
      {
        beforeHandoffValidation: async ({ directory, progressPath }) => {
          handoffs.push({
            path: directory,
            directoryMode: (await stat(directory)).mode & 0o777,
            fileMode: (await stat(progressPath)).mode & 0o777,
          });
        },
      },
    );
    const first = await playwriter.invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("capture-candidate", { candidate }),
    });
    const second = await playwriter.invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("capture-candidate", { candidate }),
    });

    try {
      expect(await readdir(evaluationDirectory)).toEqual([]);
      for (const invocation of [first, second]) {
        const events = (await readFile(join(invocation.directory, "progress.jsonl"), "utf8"))
          .trim()
          .split("\n")
          .map((line) => JSON.parse(line) as { invocationId: string; state: string });
        expect(
          events.every((event) => event.invocationId === invocation.receipt.invocationId),
        ).toBeTrue();
        expect(events.map((event) => event.state)).toEqual([
          "invocation_created",
          "process_started",
          "observation_before",
          "candidate_observed",
          "observation_after",
          "logs_captured",
          "process_succeeded",
        ]);
        await assertSealed(invocation.directory);
      }
      expect(first.receipt.invocationId).not.toBe(second.receipt.invocationId);
      expect(handoffs).toHaveLength(2);
      expect(handoffs[0]?.path).not.toBe(handoffs[1]?.path);
      for (const handoff of handoffs) {
        expect(handoff.path).toStartWith(resolve(tmpdir()));
        expect(handoff.directoryMode).toBe(0o700);
        expect(handoff.fileMode).toBe(0o600);
        expect(
          await lstat(handoff.path)
            .then(() => true)
            .catch(() => false),
        ).toBeFalse();
      }
    } finally {
      await Promise.all([first.directory, second.directory].map((path) => chmod(path, 0o755)));
    }
  });

  test("fake Playwriter rejects browser evidence writes outside documented allowed roots", async () => {
    const workingDirectory = await root();
    const source = materializeCompiledScript(
      compileNetworkScript("capture-candidate", { candidate }),
      "pw_sandbox_rejection_01",
      7,
      "/Users/hanifcarroll/Library/Application Support/linkedin-tools-next/forbidden.jsonl",
      "0123456789abcdef0123456789abcdef",
    );
    const child = Bun.spawn([process.execPath, fake, "-s", "7", "-e", source], {
      cwd: workingDirectory,
      env: {
        ...process.env,
        FAKE_SESSION_CREATION_CWD: workingDirectory,
      },
      stdout: "pipe",
      stderr: "pipe",
    });
    const [stderr, exitCode] = await Promise.all([new Response(child.stderr).text(), child.exited]);

    expect(exitCode).not.toBe(0);
    expect(stderr).toContain("PLAYWRITER_FS_SANDBOX");
  });

  test("uses the macOS lexical temp alias and rejects its canonical spelling", async () => {
    const lexicalTempRoot = resolve(tmpdir());
    const canonicalTempRoot = await realpath(lexicalTempRoot);
    expect(lexicalTempRoot).not.toBe(canonicalTempRoot);
    expect(lexicalTempRoot).toStartWith("/var/");
    expect(canonicalTempRoot).toStartWith("/private/var/");
    const lexicalDirectory = await mkdtemp(join(lexicalTempRoot, "pw-lexical-sandbox-"));
    dirs.push(lexicalDirectory);
    const workingDirectory = await root();
    const run = async (progressPath: string, invocationId: string) => {
      const source = materializeCompiledScript(
        compileNetworkScript("capture-candidate", { candidate }),
        invocationId,
        7,
        progressPath,
        "0123456789abcdef0123456789abcdef",
      );
      const child = Bun.spawn([process.execPath, fake, "-s", "7", "-e", source], {
        cwd: workingDirectory,
        env: {
          ...process.env,
          FAKE_SESSION_CREATION_CWD: workingDirectory,
        },
        stdout: "pipe",
        stderr: "pipe",
      });
      return Promise.all([
        new Response(child.stdout).text(),
        new Response(child.stderr).text(),
        child.exited,
      ]);
    };

    const lexicalPath = join(lexicalDirectory, "lexical.jsonl");
    const [, lexicalStderr, lexicalExit] = await run(lexicalPath, "pw_lexical_allowed_01");
    expect(lexicalExit).toBe(0);
    expect(lexicalStderr).not.toContain("PLAYWRITER_FS_SANDBOX");
    expect(await readFile(lexicalPath, "utf8")).toContain('"invocationId":"pw_lexical_allowed_01"');

    const canonicalPath = join(await realpath(lexicalDirectory), "canonical.jsonl");
    const [, canonicalStderr, canonicalExit] = await run(canonicalPath, "pw_canonical_rejected_01");
    expect(canonicalExit).not.toBe(0);
    expect(canonicalStderr).toContain("PLAYWRITER_FS_SANDBOX");
  });

  test("fails closed and preserves substituted or invalid temporary handoffs", async () => {
    const cases: ReadonlyArray<{
      name: string;
      mutate: (handoff: {
        readonly directory: string;
        readonly progressPath: string;
        readonly nonce: string;
      }) => Promise<void>;
    }> = [
      {
        name: "symlink",
        mutate: async ({ progressPath }) => {
          const outside = join(await root(), "outside.jsonl");
          await writeFile(outside, "outside\n");
          await unlink(progressPath);
          await symlink(outside, progressPath);
        },
      },
      {
        name: "file-substitution",
        mutate: async ({ progressPath }) => {
          const original = await readFile(progressPath);
          await unlink(progressPath);
          await writeFile(progressPath, original, { mode: 0o600 });
        },
      },
      {
        name: "oversized",
        mutate: async ({ progressPath }) => {
          await writeFile(progressPath, "x".repeat(256 * 1024 + 1));
        },
      },
      {
        name: "malformed",
        mutate: async ({ progressPath }) => {
          await writeFile(progressPath, "{bad\n");
        },
      },
      {
        name: "mismatched",
        mutate: async ({ progressPath }) => {
          const lines = (await readFile(progressPath, "utf8")).trim().split("\n");
          const event = JSON.parse(lines[0] ?? "{}") as Record<string, unknown>;
          event.invocationId = "pw_mismatched_identity_01";
          await writeFile(progressPath, `${JSON.stringify(event)}\n`);
        },
      },
      {
        name: "partial",
        mutate: async ({ progressPath }) => {
          const line = (await readFile(progressPath, "utf8")).split("\n")[0] ?? "";
          await writeFile(progressPath, `${line}\n`);
        },
      },
      {
        name: "unexpected-file",
        mutate: async ({ directory }) => {
          await writeFile(join(directory, "unexpected.txt"), "unexpected", { mode: 0o600 });
        },
      },
    ];

    for (const scenario of cases) {
      const invocationRoot = await root();
      let preservedDirectory = "";
      const invocation = await client(
        invocationRoot,
        `invalid_${scenario.name.replaceAll("-", "")}`,
        {},
        {
          beforeHandoffValidation: async (handoff) => {
            preservedDirectory = handoff.directory;
            await scenario.mutate(handoff);
          },
        },
      ).invoke({
        sessionId: 7,
        descriptor: compileNetworkScript("capture-candidate", { candidate }),
      });

      try {
        expect(invocation.receipt.outcome).toBe("failed");
        expect(invocation.receipt.blocker?.kind).toBe("evidence_corrupt");
        expect(invocation.receipt.blocker?.evidence).toContain("EVIDENCE_HANDOFF");
        expect(invocation.receipt.blocker?.evidence).toContain(preservedDirectory);
        expect(
          await lstat(preservedDirectory)
            .then(() => true)
            .catch(() => false),
        ).toBeTrue();
        expect(invocation.progress.map((event) => event.state)).toEqual([
          "invocation_created",
          "process_started",
          "process_failed",
        ]);
      } finally {
        await chmod(invocation.directory, 0o755);
        await rm(preservedDirectory, { recursive: true, force: true });
      }
    }
  });

  for (const command of ordinaryCommands)
    test(command, async () => {
      const directory = await root();
      const env: Record<string, string> = {};
      if (command === "click-connect-menu-item" || command === "walk-list")
        env.FAKE_MENU_OPEN = "1";
      if (command === "observe-connect-modal" || command === "walk-list") env.FAKE_MODAL_OPEN = "1";
      if (command === "observe-post-send") env.FAKE_PENDING = "1";
      const invocation = await client(directory, command.replaceAll("-", ""), env).invoke({
        sessionId: 7,
        descriptor: compileNetworkScript(command, inputFor(command)),
      });
      expect(invocation.receipt.outcome).toBe("succeeded");
      expect(invocation.receipt.result?.command).toBe(command);
      expect(invocation.stdout).toStartWith("[return value] ");
      assertInvocationEvidence(invocation.config, invocation.receipt, invocation.progress);
      await assertSealed(invocation.directory);
    });

  test("accepts Playwriter's buffered Console output frame", async () => {
    const directory = await root();
    const invocation = await client(directory, "console_frame", {
      FAKE_RESULT_FRAME: "console",
    }).invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("capture-candidate", { candidate }),
    });
    expect(invocation.receipt.outcome).toBe("succeeded");
    expect(invocation.stdout).toStartWith("Console output:\n[log] ");
  });

  test("keeps structured stdout bounded across repeated and huge generic browser logs", async () => {
    for (const [prefix, mode, expectedCount] of [
      ["repeated_logs", "repeated-generic", 5_000],
      ["huge_log", "huge-generic", 1],
    ] as const) {
      const directory = await root();
      const invocation = await client(directory, prefix, {
        FAKE_DIAGNOSTIC_MODE: mode,
      }).invoke({
        sessionId: 7,
        descriptor: compileNetworkScript("capture-candidate", { candidate }),
      });
      expect(invocation.receipt.outcome).toBe("succeeded");
      expect(Buffer.byteLength(invocation.stdout)).toBeLessThan(1_024);
      const diagnostics = JSON.parse(
        await readFile(join(invocation.directory, "diagnostics.json"), "utf8"),
      ) as Record<string, unknown>;
      expect(diagnostics.sourceCount).toBe(expectedCount);
      expect(diagnostics.genericNetErrFailedCount).toBe(expectedCount);
      expect(diagnostics.genericNetErrFailedSample).toHaveLength(1);
      expect(JSON.stringify(diagnostics)).not.toContain("x".repeat(1_000));
      await assertSealed(invocation.directory);
    }
  });

  test("retains exact terminal diagnostics with a typed stdout summary and secure reference", async () => {
    const directory = await root();
    const invocation = await client(directory, "terminal_logs", {
      FAKE_DIAGNOSTIC_MODE: "terminal",
    }).invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("capture-candidate", { candidate }),
    });
    expect(invocation.receipt.outcome).toBe("failed");
    expect(invocation.receipt.blocker?.kind).toBe("rate_limit_429");
    expect(Buffer.byteLength(invocation.stdout)).toBeLessThan(1_024);
    const control = JSON.parse(
      await readFile(join(invocation.directory, "control.json"), "utf8"),
    ) as { logs: Record<string, unknown> };
    expect(control.logs).toMatchObject({
      kind: "playwriter_diagnostic_summary",
      relevantCount: 3,
      artifact: "diagnostics.json",
    });
    const diagnosticsText = await readFile(join(invocation.directory, "diagnostics.json"), "utf8");
    expect(diagnosticsText).toContain("429 Too Many Requests");
    expect(diagnosticsText).toContain("linkedin.com/checkpoint security verification");
    expect(diagnosticsText).toContain("net::ERR_CONNECTION_REFUSED");
  });

  test("uses an explicit bounded sample for non-terminal diagnostics", async () => {
    const directory = await root();
    const invocation = await client(directory, "other_logs", {
      FAKE_DIAGNOSTIC_MODE: "many-other",
    }).invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("capture-candidate", { candidate }),
    });
    expect(invocation.receipt.outcome).toBe("succeeded");
    const diagnostics = JSON.parse(
      await readFile(join(invocation.directory, "diagnostics.json"), "utf8"),
    ) as Record<string, unknown>;
    expect(diagnostics.selectionStep).toBe("terminal-preserving-diagnostic-selection-v2");
    expect(diagnostics.sampleLimit).toBe(32);
    expect(diagnostics.otherCount).toBe(100);
    expect(diagnostics.otherSample).toHaveLength(32);
  });

  test("fails closed on malformed or oversized diagnostic evidence", async () => {
    for (const [prefix, mode] of [
      ["malformed_logs", "malformed"],
      ["oversized_logs", "oversized-terminal"],
    ] as const) {
      const directory = await root();
      const invocation = await client(directory, prefix, {
        FAKE_DIAGNOSTIC_MODE: mode,
      }).invoke({
        sessionId: 7,
        descriptor: compileNetworkScript("capture-candidate", { candidate }),
      });
      expect(invocation.receipt.outcome).toBe("failed");
      expect(invocation.receipt.blocker?.kind).toBe("evidence_corrupt");
      expect(invocation.receipt.result).toBeNull();
      expect(await readdir(invocation.directory)).not.toContain("control.json");
    }
  });
});

describe("two-invocation Send protocol", () => {
  test("prepare strictly validates and returns an immutable receipt without clicking", async () => {
    const directory = await root();
    const orderFile = join(directory, "prepare-order.log");
    const prepareSource = compileNetworkScript("prepare-send", {
      attemptId: "attempt_0001",
      candidate,
    }).source;
    expect(prepareSource).toContain(
      `page:{stateKey:"networkCandidateResultsPage",url:${JSON.stringify(candidate.searchUrl)}`,
    );
    expect(prepareSource).not.toContain(
      'page:{stateKey:"networkCandidateResultsPage",url:p.url()}',
    );
    const prepared = await prepareNetworkSend(
      client(directory, "prepare", {
        FAKE_MODAL_OPEN: "1",
        FAKE_ORDER_FILE: orderFile,
      }),
      7,
      { attemptId: "attempt_0001", candidate },
    );
    expect(prepared.invocation.receipt.outcome).toBe("succeeded");
    expect(prepared.receipt).not.toBeNull();
    expect(prepared.receipt?.receiptId).toMatch(/^pwprep:pw_prepare_01:[a-f0-9]{32}:[a-f0-9]{64}$/);
    expect(Object.isFrozen(prepared.receipt)).toBeTrue();
    expect(Object.isFrozen(prepared.receipt?.candidate)).toBeTrue();
    expect(await readOrder(orderFile)).toEqual([
      "send_visible_validated",
      "send_enabled_validated",
    ]);
    expect(await readFile(orderFile, "utf8")).not.toContain("send_click");
    expect(prepared.invocation.progress.map((event) => event.state)).toEqual([
      "invocation_created",
      "process_started",
      "observation_before",
      "send_prepared",
      "observation_after",
      "logs_captured",
      "process_succeeded",
    ]);
    expect(prepared.invocation.receipt.result?.data).toEqual(prepared.receipt);
    await assertSealed(prepared.invocation.directory);
  });

  test("candidate revalidation accepts matching Sales Navigator tracking metadata", async () => {
    const directory = await root();
    const prepared = await prepareNetworkSend(
      client(directory, "decorated_candidate", {
        FAKE_MODAL_OPEN: "1",
        FAKE_ROW_IDENTITY: "urn:li:fs_salesProfile:(ACwAA123,NAME_SEARCH,WgyU)",
        FAKE_LEAD_HREF: "/sales/lead/ACwAA123,NAME_SEARCH,Skvp?_ntb=tracking",
      }),
      7,
      { attemptId: "attempt_decorated", candidate },
    );
    expect(prepared.invocation.receipt.outcome).toBe("succeeded");
    expect(prepared.receipt?.candidate).toEqual(candidate);
  });

  test("commit revalidates the exact preparation, clicks once, and returns post-click evidence", async () => {
    const directory = await root();
    const orderFile = join(directory, "commit-order.log");
    const playwriter = client(directory, "success", {
      FAKE_MODAL_OPEN: "1",
      FAKE_ORDER_FILE: orderFile,
    });
    const prepared = await prepareNetworkSend(playwriter, 7, {
      attemptId: "attempt_0002",
      candidate,
    });
    if (prepared.receipt === null) throw new Error("expected preparation receipt");
    const commitSource = compileNetworkScript("commit-send", {
      candidate,
      sendPreparation: prepared.receipt,
    }).source;
    expect(commitSource).not.toMatch(
      /process\.stdin|__LINKEDIN_TOOLS_GATE__|sendClickStartedHook|PERSISTENCE_HOOK|await import/,
    );
    expect(commitSource.match(/await send\.click\(\)/g)).toHaveLength(1);
    const committed = await commitNetworkSend(playwriter, 7, prepared.receipt);
    expect(committed.receipt.outcome).toBe("succeeded");
    expect(await readOrder(orderFile)).toEqual([
      "send_visible_validated",
      "send_enabled_validated",
      "send_visible_validated",
      "send_enabled_validated",
      "send_click",
      "trigger_click",
    ]);
    expect(committed.progress.map((event) => event.state)).toEqual([
      "invocation_created",
      "process_started",
      "observation_before",
      "send_commit_started",
      "send_click_dispatched",
      "send_post_click_observed",
      "observation_after",
      "logs_captured",
      "process_succeeded",
    ]);
    expect(committed.receipt.result?.data).toEqual({
      schemaVersion: 1,
      kind: "network_send_commit",
      receiptId: prepared.receipt.receiptId,
      attemptId: prepared.receipt.attemptId,
      candidate,
      clickDispatched: true,
      postClickEvidence: {
        observedUrl: candidate.searchUrl,
        modalCount: 0,
        sendControlCount: 0,
        pendingCount: 1,
        capturedAt: expect.any(String),
      },
    });
    expect(committed.stdout).toStartWith("[return value] ");
    await assertSealed(committed.directory);
    expect(JSON.parse(await readFile(join(directory, "fake-session.json"), "utf8"))).toEqual({});
  });

  test("accepts LinkedIn's session URL after Send and captures pending evidence", async () => {
    const directory = await root();
    const observedUrl = `${candidate.searchUrl}&sessionId=7`;
    const playwriter = client(directory, "session_url_after_send", {
      FAKE_MODAL_OPEN: "1",
      FAKE_PAGE_URL: observedUrl,
    });
    const prepared = await prepareNetworkSend(playwriter, 7, {
      attemptId: "attempt_session_url",
      candidate,
    });
    if (prepared.receipt === null) throw new Error("expected preparation receipt");
    const committed = await commitNetworkSend(playwriter, 7, prepared.receipt);
    expect(committed.receipt.outcome).toBe("succeeded");
    expect(committed.receipt.result?.data).toMatchObject({
      postClickEvidence: {
        observedUrl,
        pendingCount: 1,
      },
    });
  });

  test("recognizes an already-pending row before any Connect click", async () => {
    const directory = await root();
    const orderFile = join(directory, "pending-menu-order.log");
    const invocation = await client(directory, "pending_menu", {
      FAKE_MENU_PENDING: "1",
      FAKE_ORDER_FILE: orderFile,
    }).invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("click-connect-menu-item", { candidate }),
    });
    expect(invocation.receipt.outcome).toBe("failed");
    expect(invocation.receipt.blocker?.kind).toBe("already_pending");
    expect(await readFile(orderFile, "utf8")).not.toContain("connect_click");
  });

  test("commit cannot start without a structurally and cryptographically valid receipt", async () => {
    const directory = await root();
    expect(() => compileNetworkScript("commit-send", { candidate })).toThrow(
      "requires send preparation receipt",
    );
    await expect(
      commitNetworkSend(client(directory, "invalid"), 7, {
        schemaVersion: 1,
        kind: "network_send_prepared",
        receiptId: `pwprep:pw_prepare_01:${"a".repeat(32)}:${"b".repeat(64)}`,
        attemptId: "attempt_0003",
        preparedAt: "2026-08-03T12:00:00.000Z",
        candidate,
      }),
    ).rejects.toThrow("fingerprint mismatch");
    expect(await readdir(directory)).toEqual([]);
  });

  test("a replaced preparation token fails before click", async () => {
    const directory = await root();
    const orderFile = join(directory, "replaced-order.log");
    const playwriter = client(directory, "replaced", {
      FAKE_MODAL_OPEN: "1",
      FAKE_ORDER_FILE: orderFile,
    });
    const first = await prepareNetworkSend(playwriter, 7, {
      attemptId: "attempt_0004",
      candidate,
    });
    await prepareNetworkSend(playwriter, 7, { attemptId: "attempt_0005", candidate });
    if (first.receipt === null) throw new Error("expected preparation receipt");
    const committed = await commitNetworkSend(playwriter, 7, first.receipt);
    expect(committed.receipt.outcome).toBe("critical_uncertainty");
    expect(committed.receipt.blocker).toEqual({
      kind: "preparation_mismatch",
      evidence: "PREPARATION_MISMATCH",
      retryability: "possible_send",
    });
    expect((await readFile(orderFile, "utf8")).match(/send_click/g)).toBeNull();
  });

  test("an expired preparation fails before click and remains possible", async () => {
    const directory = await root();
    const orderFile = join(directory, "stale-order.log");
    const prepared = await prepareNetworkSend(
      client(directory, "stale_prepare", {
        FAKE_MODAL_OPEN: "1",
        FAKE_ORDER_FILE: orderFile,
        FAKE_NOW: "2026-08-03T12:00:00.000Z",
      }),
      7,
      { attemptId: "attempt_0006", candidate },
    );
    if (prepared.receipt === null) throw new Error("expected preparation receipt");
    const committed = await commitNetworkSend(
      client(directory, "stale_commit", {
        FAKE_MODAL_OPEN: "1",
        FAKE_ORDER_FILE: orderFile,
        FAKE_NOW: "2026-08-03T12:02:00.001Z",
      }),
      7,
      prepared.receipt,
    );
    expect(committed.receipt.outcome).toBe("critical_uncertainty");
    expect(committed.receipt.blocker?.kind).toBe("preparation_stale");
    expect(committed.receipt.blocker?.retryability).toBe("possible_send");
    expect((await readFile(orderFile, "utf8")).match(/send_click/g)).toBeNull();
  });

  for (const fixture of [
    [
      "wrong page",
      { FAKE_PAGE_URL: "https://www.linkedin.com/sales/search/people?savedSearchId=99" },
    ],
    ["wrong candidate", { FAKE_ROW_IDENTITY: "urn:li:fs_salesProfile:wrong" }],
    ["wrong source link", { FAKE_LEAD_HREF: "/sales/lead/other" }],
    ["missing modal", { FAKE_MODAL_OPEN: "0" }],
    ["duplicate control", { FAKE_SEND_COUNT: "2" }],
    ["hidden control", { FAKE_SEND_VISIBLE: "0" }],
    ["disabled control", { FAKE_SEND_DISABLED: "1" }],
  ] as const)
    test(`commit revalidation rejects ${fixture[0]} before click`, async () => {
      const directory = await root();
      const orderFile = join(directory, `${fixture[0].replaceAll(" ", "-")}.log`);
      const prepared = await prepareNetworkSend(
        client(directory, `prepare_${fixture[0].replaceAll(" ", "_")}`, {
          FAKE_MODAL_OPEN: "1",
          FAKE_ORDER_FILE: orderFile,
        }),
        7,
        { attemptId: "attempt_0007", candidate },
      );
      if (prepared.receipt === null) throw new Error("expected preparation receipt");
      const committed = await commitNetworkSend(
        client(directory, `commit_${fixture[0].replaceAll(" ", "_")}`, {
          FAKE_MODAL_OPEN: "1",
          FAKE_ORDER_FILE: orderFile,
          ...fixture[1],
        }),
        7,
        prepared.receipt,
      );
      expect(committed.receipt.outcome).toBe("critical_uncertainty");
      expect(committed.receipt.blocker?.retryability).toBe("possible_send");
      expect((await readFile(orderFile, "utf8")).match(/send_click/g)).toBeNull();
    });

  for (const failure of [
    ["transport before execution", { FAKE_TRANSPORT_FAIL_BEFORE_EXEC: "1" }, false],
    ["transport after execution", { FAKE_TRANSPORT_FAIL_AFTER_EXEC: "1" }, true],
    ["malformed output", { FAKE_OUTPUT_MODE: "malformed" }, true],
    ["click transport after dispatch", { FAKE_CLICK_THROW_AFTER: "1" }, true],
  ] as const)
    test(`commit ${failure[0]} is always uncertain`, async () => {
      const directory = await root();
      const orderFile = join(directory, `${failure[0].replaceAll(" ", "-")}.log`);
      const prepared = await prepareNetworkSend(
        client(directory, `prepare_${failure[0].replaceAll(" ", "_")}`, {
          FAKE_MODAL_OPEN: "1",
          FAKE_ORDER_FILE: orderFile,
        }),
        7,
        { attemptId: "attempt_0008", candidate },
      );
      if (prepared.receipt === null) throw new Error("expected preparation receipt");
      const committed = await commitNetworkSend(
        client(directory, `commit_${failure[0].replaceAll(" ", "_")}`, {
          FAKE_MODAL_OPEN: "1",
          FAKE_ORDER_FILE: orderFile,
          ...failure[1],
        }),
        7,
        prepared.receipt,
      );
      expect(committed.receipt.outcome).toBe("critical_uncertainty");
      expect(committed.receipt.blocker?.retryability).toBe("possible_send");
      expect((await readFile(orderFile, "utf8")).includes("send_click")).toBe(failure[2]);
    });

  test("prepare failures remain pre-commit failures and never click", async () => {
    const directory = await root();
    const orderFile = join(directory, "prepare-failure.log");
    const prepared = await prepareNetworkSend(
      client(directory, "prepare_failure", {
        FAKE_MODAL_OPEN: "0",
        FAKE_ORDER_FILE: orderFile,
      }),
      7,
      { attemptId: "attempt_0009", candidate },
    );
    expect(prepared.receipt).toBeNull();
    expect(prepared.invocation.receipt.outcome).toBe("failed");
    expect(prepared.invocation.receipt.blocker?.retryability).not.toBe("possible_send");
    expect(await readFile(orderFile, "utf8").catch(() => "")).not.toContain("send_click");
  });
});

test("expanded blockers include preparation and commit uncertainty", () => {
  const samples = {
    rate_limit_429: "429",
    weekly_limit: "weekly invitation limit",
    unusual_activity: "unusual activity",
    login: "login required",
    checkpoint: "checkpoint",
    security_verification: "security verification",
    session_lost: "Session 2 not found",
    source_mismatch: "SOURCE_MISMATCH",
    wrong_page: "WRONG_PAGE",
    selector_contract: "SELECTOR_CONTRACT",
    preparation_mismatch: "PREPARATION_MISMATCH",
    preparation_stale: "PREPARATION_STALE",
    commit_uncertainty: "COMMIT_SEND_UNCERTAIN",
  } as const;
  for (const [kind, text] of Object.entries(samples))
    expect(detectBlocker(text)?.kind).toBe(kind as keyof typeof samples);
  expect(
    detectBlocker(
      JSON.stringify([
        {
          text: "https://www.google.com/recaptcha/api2/anchor violates the following Content Security Policy directive: \"script-src 'self'\". The policy is report-only, so the violation has been logged but no further action has been taken.",
        },
        "EMAIL_REQUIRED",
      ]),
    )?.kind,
  ).toBe("email_required");
  expect(
    detectBlocker(
      'https://www.gstatic.com/recaptcha/releases/x/recaptcha__en.js violates the following Content Security Policy directive: "script-src". The policy is report-only, so the violation has been logged but no further action has been taken.',
    ),
  ).toBeUndefined();
});

describe("strict saved-search terminal capture", () => {
  const sourceContract = resolveNetworkSourceContract(candidate.searchUrl);

  test("explicit terminal capture binds source, rows, page, cursor, and reload without an action", async () => {
    const directory = await root();
    const captured = await captureNetworkSource(
      client(directory, "terminal", {
        FAKE_TERMINAL: "1",
        FAKE_NOW: "2026-08-03T12:00:00.000Z",
      }),
      7,
      sourceContract,
    );
    if (captured.capture === null || captured.data === null)
      throw new Error("expected source capture");
    expect(captured.navigation.config.action).toBe("navigate");
    expect(captured.capture.config.action).toBe("none");
    expect(captured.data).toEqual({
      schemaVersion: 1,
      kind: "network_source_capture",
      captureInvocationId: captured.capture.receipt.invocationId,
      capturedAt: "2026-08-03T12:00:00.000Z",
      sourceContract,
      url: candidate.searchUrl,
      items: [
        {
          rowIdentity: candidate.rowIdentity,
          salesLeadUrl: candidate.salesLeadUrl,
          name: candidate.name,
        },
      ],
      reload: {
        navigationInvocationId: captured.navigation.receipt.invocationId,
        reloadIdentity: `${captured.navigation.receipt.invocationId}:reload`,
        reloadGeneration: 1,
        navigatedAt: "2026-08-03T12:00:00.000Z",
      },
      page: {
        stateKey: "networkCandidateResultsPage",
        url: candidate.searchUrl,
        resultsContainerCount: 1,
        resultsContainerVisible: true,
        ariaBusy: "false",
        progressbarCount: 0,
        alertCount: 0,
        dialogCount: 0,
        fullyLoaded: true,
        blockerFree: true,
        cursorIdentity: "Page 3",
        pageIdentity: `salesnav-saved-search:${candidate.savedSearchId}:Page 3`,
      },
      pagination: {
        navigationCount: 1,
        currentPageCount: 1,
        nextControlCount: 1,
        nextDisabled: true,
      },
      terminalEvidence: {
        schemaVersion: 1,
        kind: "network_source_terminal_observation",
        captureInvocationId: captured.capture.receipt.invocationId,
        observedAt: "2026-08-03T12:00:00.000Z",
        sourceId: sourceContract.sourceId,
        sourceName: sourceContract.sourceName,
        savedSearchId: sourceContract.savedSearchId,
        searchUrl: sourceContract.searchUrl,
        sourceContractVersion: 1,
        sourceContractFingerprint: sourceContract.contractFingerprint,
        terminalFingerprint: expect.stringMatching(/^[a-f0-9]{64}$/),
        pageIdentity: `salesnav-saved-search:${candidate.savedSearchId}:Page 3`,
        cursorIdentity: "Page 3",
        stableRowIds: [candidate.rowIdentity],
        rowCount: 1,
        nextControl: "disabled",
        navigationInvocationId: captured.navigation.receipt.invocationId,
        reloadIdentity: `${captured.navigation.receipt.invocationId}:reload`,
        reloadGeneration: 1,
        navigatedAt: "2026-08-03T12:00:00.000Z",
      },
    });
    expect(Object.isFrozen(captured.data)).toBeTrue();
    const integrated = parseSourceCapture(captured.data, {
      id: sourceContract.sourceId,
      name: sourceContract.sourceName,
      savedSearchId: sourceContract.savedSearchId,
      url: sourceContract.searchUrl,
      sourceContractVersion: sourceContract.contractVersion,
    });
    const terminal = captured.data.terminalEvidence;
    if (terminal === undefined) throw new Error("expected terminal evidence");
    expect(integrated.terminal).toEqual({
      sourceId: sourceContract.sourceId,
      sourceContractVersion: 1,
      stableTerminalFingerprint: terminal.terminalFingerprint,
      pageIdentity: `salesnav-saved-search:${candidate.savedSearchId}:Page 3`,
      stableRowIds: [candidate.rowIdentity],
      nextControl: "disabled",
      pageCursor: "Page 3",
      reloadGeneration: 1,
      observedAt: "2026-08-03T12:00:00.000Z",
    });
    const captureSource = compileNetworkScript("capture-candidate-results", {
      url: candidate.searchUrl,
      sourceContract,
    }).source;
    expect(captureSource).not.toMatch(/\.goto\(|document\.title|No results found/);
    expect(captureSource).toContain("See more actions for");
    expect(captureSource).toContain('getByRole("navigation",{name:"Pagination",exact:true})');
  });

  test("canonicalizes Sales Navigator row identities and lead URLs with tracking metadata", async () => {
    const directory = await root();
    const captured = await captureNetworkSource(
      client(directory, "real_identity_shape", {
        FAKE_ROW_IDENTITY: "urn:li:fs_salesProfile:(ACwAA123,NAME_SEARCH,WgyU)",
        FAKE_LEAD_HREF:
          "https://www.linkedin.com/sales/lead/ACwAA123,NAME_SEARCH,Skvp?_ntb=tracking",
      }),
      7,
      sourceContract,
    );
    expect(captured.data?.items).toEqual([
      {
        rowIdentity: "urn:li:fs_salesProfile:ACwAA123",
        salesLeadUrl: "https://www.linkedin.com/sales/lead/ACwAA123",
        name: candidate.name,
      },
    ]);
  });

  test("excludes rows whose action menu already says Connect - Pending", async () => {
    const directory = await root();
    const captured = await captureNetworkSource(
      client(directory, "pending_row", { FAKE_MENU_PENDING: "1" }),
      7,
      sourceContract,
    );
    expect(captured.data?.items).toEqual([]);
  });

  test("same capture replay cannot satisfy the two-observation contract", async () => {
    const directory = await root();
    const captured = await captureNetworkSource(
      client(directory, "replay", { FAKE_TERMINAL: "1" }),
      7,
      sourceContract,
    );
    const terminal = captured.data?.terminalEvidence;
    if (terminal === undefined) throw new Error("expected terminal evidence");
    expect(() =>
      assertSourceExhaustionEvidence({
        sourceContract,
        observations: [terminal, terminal],
      }),
    ).toThrow("distinct ordered reloads");
  });

  test("two matching reload generations form valid exhaustion evidence", async () => {
    const directory = await root();
    const first = await captureNetworkSource(
      client(directory, "reload_a", {
        FAKE_TERMINAL: "1",
        FAKE_NOW: "2026-08-03T12:00:00.000Z",
      }),
      7,
      sourceContract,
    );
    const second = await captureNetworkSource(
      client(directory, "reload_b", {
        FAKE_TERMINAL: "1",
        FAKE_NOW: "2026-08-03T12:01:00.000Z",
      }),
      7,
      sourceContract,
    );
    const firstTerminal = first.data?.terminalEvidence;
    const secondTerminal = second.data?.terminalEvidence;
    if (firstTerminal === undefined || secondTerminal === undefined)
      throw new Error("expected two terminal observations");
    expect(firstTerminal.reloadGeneration).toBe(1);
    expect(secondTerminal.reloadGeneration).toBe(2);
    expect(firstTerminal.terminalFingerprint).toBe(secondTerminal.terminalFingerprint);
    expect(() =>
      assertSourceExhaustionEvidence({
        sourceContract,
        observations: [firstTerminal, secondTerminal],
      }),
    ).not.toThrow();
  });

  for (const fixture of [
    {
      name: "transient empty",
      env: { FAKE_TERMINAL: "1", FAKE_EMPTY_RESULTS: "1" },
      assertion: "empty",
    },
    {
      name: "loading",
      env: { FAKE_TERMINAL: "1", FAKE_RESULTS_LOADING: "1" },
      assertion: "loading",
    },
    {
      name: "blocker",
      env: { FAKE_TERMINAL: "1", FAKE_SOURCE_BLOCKER: "1" },
      assertion: "blocker",
    },
    { name: "missing pagination", env: {}, assertion: "missing" },
  ] as const)
    test(`${fixture.name} page never emits terminal evidence`, async () => {
      const directory = await root();
      const captured = await captureNetworkSource(
        client(directory, `nonterminal_${fixture.assertion}`, fixture.env),
        7,
        sourceContract,
      );
      if (captured.data === null) throw new Error("expected nonterminal capture");
      expect("terminalEvidence" in captured.data).toBeFalse();
      if (fixture.assertion === "empty") expect(captured.data.items).toHaveLength(0);
      if (fixture.assertion === "loading") expect(captured.data.page.fullyLoaded).toBeFalse();
      if (fixture.assertion === "blocker") expect(captured.data.page.blockerFree).toBeFalse();
      if (fixture.assertion === "missing") expect(captured.data.pagination.navigationCount).toBe(0);
    });

  test("enabled pagination is explicitly nonterminal", async () => {
    const directory = await root();
    const captured = await captureNetworkSource(
      client(directory, "pagination", { FAKE_PAGINATION: "1" }),
      7,
      sourceContract,
    );
    if (captured.data === null) throw new Error("expected paginated capture");
    expect(captured.data.pagination).toEqual({
      navigationCount: 1,
      currentPageCount: 1,
      nextControlCount: 1,
      nextDisabled: false,
    });
    expect("terminalEvidence" in captured.data).toBeFalse();
  });

  test("compile-time and staged runtime source mismatches fail closed", async () => {
    const marketing = resolveNetworkSourceContract(
      "https://www.linkedin.com/sales/search/people?savedSearchId=1980844577",
    );
    expect(() =>
      compileNetworkScript("capture-candidate-results", {
        url: candidate.searchUrl,
        sourceContract: marketing,
      }),
    ).toThrow("source contract mismatch");

    const directory = await root();
    const invocation = await client(directory, "source_mismatch", {
      FAKE_SOURCE_STATE_MISMATCH: "1",
    }).invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("capture-candidate-results", {
        url: candidate.searchUrl,
        sourceContract,
      }),
    });
    expect(invocation.receipt.outcome).toBe("failed");
    expect(invocation.receipt.blocker?.kind).toBe("source_mismatch");
  });

  test("malformed captured rows fail instead of being skipped", async () => {
    const directory = await root();
    const captured = await captureNetworkSource(
      client(directory, "invalid_row", { FAKE_ROW_IDENTITY: "not-a-sales-profile-urn" }),
      7,
      sourceContract,
    );
    expect(captured.capture?.receipt.outcome).toBe("failed");
    expect(captured.capture?.receipt.blocker?.kind).toBe("source_mismatch");
    expect(captured.data).toBeNull();
  });
});
