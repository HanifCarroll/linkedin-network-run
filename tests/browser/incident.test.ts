import { afterEach, describe, expect, test } from "bun:test";
import { chmod, mkdtemp, readdir, readFile, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ActiveIncidentError,
  activeIncident,
  assertNoActiveIncident,
  clearIncident,
  detectFatalIncident,
  INCIDENT_ACTIVE_CODE,
  INCIDENT_ACTIVE_EXIT_CODE,
  IncidentDetectedError,
  incidentPath,
  openIncident,
} from "../../src/browser/incident.ts";
import { type CliIo, run } from "../../src/cli.ts";
import { doctor } from "../../src/commands/doctor.ts";
import { networkIncidentClear, networkIncidentStatus } from "../../src/commands/incident.ts";
import { createDefaultOperations } from "../../src/commands/operations.ts";
import { bootstrapDatabase } from "../../src/db/database.ts";
import { compileNetworkScript, PlaywriterClient } from "../../src/playwriter/index.ts";

const fake = new URL("../playwriter/fake-playwriter.ts", import.meta.url).pathname;
const dirs: string[] = [];

async function tempDir(prefix: string): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), prefix));
  const canonical = await realpath(directory);
  dirs.push(canonical);
  return canonical;
}

afterEach(async () => {
  await Promise.all(
    dirs.splice(0).map(async (directory) => {
      for (const entry of await readdir(directory).catch(() => [])) {
        const path = join(directory, entry);
        await chmod(path, 0o755).catch(() => {});
        for (const nested of await readdir(path).catch(() => [])) {
          await chmod(join(path, nested), 0o755).catch(() => {});
        }
      }
      await chmod(directory, 0o755).catch(() => {});
      await rm(directory, { recursive: true, force: true });
    }),
  );
});

const environment = {
  HOME: "/Users/test",
  LINKEDIN_TOOLS_NETWORK_SESSION: "7",
  LINKEDIN_TOOLS_ANALYTICS_SESSION: "8",
} as const;

function cliHarness() {
  const stdout: string[] = [];
  const stderr: string[] = [];
  const io: CliIo = {
    stdout: (value) => stdout.push(value),
    stderr: (value) => stderr.push(value),
  };
  return { stdout, stderr, io };
}

describe("incident gate module", () => {
  test("detects weekly-limit fatal text and ignores ordinary blockers", () => {
    expect(detectFatalIncident("You've reached the weekly invitation limit")).toMatchObject({
      kind: "weekly_limit",
    });
    expect(detectFatalIncident("SELECTOR_CONTRACT: button missing")).toBeNull();
    expect(detectFatalIncident("WRONG_PAGE expected candidate results")).toBeNull();
    expect(detectFatalIncident("ALREADY_PENDING for this lead")).toBeNull();
  });

  test("open/clear dual confirmation and status shape", async () => {
    const stateDir = await tempDir("ltn-incident-");
    expect(await activeIncident(stateDir)).toBeNull();
    const opened = await openIncident(stateDir, {
      kind: "weekly_limit",
      reason: "LinkedIn weekly limit reached",
      detail: "weekly limit excerpt",
      openedAt: "2026-08-03T12:00:00.000Z",
    });
    expect(opened.active).toBe(true);
    const raw = JSON.parse(await readFile(incidentPath(stateDir), "utf8")) as {
      kind: string;
      active: boolean;
    };
    expect(raw).toMatchObject({ kind: "weekly_limit", active: true });

    await expect(
      clearIncident(stateDir, {
        reason: "fixed",
        accountAccessConfirmed: true,
        warningClearedConfirmed: false,
      }),
    ).rejects.toThrow(/confirmed/);

    await expect(
      clearIncident(stateDir, {
        reason: "   ",
        accountAccessConfirmed: true,
        warningClearedConfirmed: true,
      }),
    ).rejects.toThrow(/reason/);

    const cleared = await clearIncident(stateDir, {
      reason: "human verified account access and warning cleared",
      accountAccessConfirmed: true,
      warningClearedConfirmed: true,
      clearedAt: "2026-08-03T13:00:00.000Z",
    });
    expect(cleared.active).toBe(false);
    expect(await activeIncident(stateDir)).toBeNull();
  });
});

describe("playwriter client incident gate", () => {
  test("blocks every invocation while an incident file is present", async () => {
    const stateDir = await tempDir("ltn-incident-block-");
    const invocationRoot = join(stateDir, "invocations");
    await openIncident(stateDir, {
      kind: "checkpoint",
      reason: "LinkedIn checkpoint present",
      detail: "/checkpoint",
      openedAt: "2026-08-03T12:00:00.000Z",
    });
    const client = new PlaywriterClient({
      executable: process.execPath,
      executableArgs: [fake],
      invocationRoot,
      stateDir,
      createInvocationId: () => "pw_incident_blocked",
    });
    await expect(client.listSessions()).rejects.toBeInstanceOf(ActiveIncidentError);
    await expect(
      client.invoke({
        sessionId: 7,
        descriptor: compileNetworkScript("navigate-sent-list"),
      }),
    ).rejects.toMatchObject({
      code: INCIDENT_ACTIVE_CODE,
      exitCode: INCIDENT_ACTIVE_EXIT_CODE,
    });
    expect(await readdir(invocationRoot).catch(() => [])).toEqual([]);
  });

  test("weekly-limit failure opens an incident and blocks the next command", async () => {
    const stateDir = await tempDir("ltn-incident-open-");
    const invocationRoot = join(stateDir, "invocations");
    const client = new PlaywriterClient({
      executable: process.execPath,
      executableArgs: [fake],
      invocationRoot,
      stateDir,
      createInvocationId: () => "pw_incident_weekly",
      env: {
        FAKE_STATE_FILE: join(invocationRoot, "fake-session.json"),
        FAKE_PLAYWRITER_EXIT: "1",
        FAKE_BROWSER_ERROR: "You've reached the weekly invitation limit for connections",
      },
    });
    await expect(
      client.invoke({
        sessionId: 7,
        descriptor: compileNetworkScript("navigate-sent-list"),
      }),
    ).rejects.toBeInstanceOf(IncidentDetectedError);

    const incident = await activeIncident(stateDir);
    expect(incident).toMatchObject({ kind: "weekly_limit", active: true });

    const blocked = new PlaywriterClient({
      executable: process.execPath,
      executableArgs: [fake],
      invocationRoot,
      stateDir,
      createInvocationId: () => "pw_incident_next",
    });
    await expect(blocked.listSessions()).rejects.toBeInstanceOf(ActiveIncidentError);
  });

  test("ordinary blocker failure does not open an incident", async () => {
    const stateDir = await tempDir("ltn-incident-ordinary-");
    const invocationRoot = join(stateDir, "invocations");
    const client = new PlaywriterClient({
      executable: process.execPath,
      executableArgs: [fake],
      invocationRoot,
      stateDir,
      createInvocationId: () => "pw_incident_ordinary",
      env: {
        FAKE_STATE_FILE: join(invocationRoot, "fake-session.json"),
        FAKE_PLAYWRITER_EXIT: "1",
        FAKE_BROWSER_ERROR: "SELECTOR_CONTRACT: expected one lead anchor",
      },
    });
    const result = await client.invoke({
      sessionId: 7,
      descriptor: compileNetworkScript("navigate-sent-list"),
    });
    expect(result.receipt.outcome).toBe("failed");
    expect(result.receipt.blocker?.kind).toBe("selector_contract");
    expect(await activeIncident(stateDir)).toBeNull();
  });
});

describe("incident CLI commands", () => {
  test("incident-status reports none or active shape", async () => {
    const stateDir = await tempDir("ltn-incident-status-");
    expect(await networkIncidentStatus({ stateDir })).toEqual({
      command: "network incident-status",
      active: false,
      incident: null,
    });
    await openIncident(stateDir, {
      kind: "rate_limit",
      reason: "LinkedIn returned HTTP 429",
      detail: "status 429",
      openedAt: "2026-08-03T12:00:00.000Z",
    });
    expect(await networkIncidentStatus({ stateDir })).toMatchObject({
      command: "network incident-status",
      active: true,
      incident: {
        kind: "rate_limit",
        active: true,
      },
    });
  });

  test("incident-clear requires both flags and a non-empty reason (exit 2)", async () => {
    const stateDir = await tempDir("ltn-incident-clear-");
    await openIncident(stateDir, {
      kind: "unusual_activity",
      reason: "LinkedIn unusual-activity warning",
      detail: "unusual activity",
      openedAt: "2026-08-03T12:00:00.000Z",
    });

    const partial = cliHarness();
    const partialExit = await run(
      ["--json", "network", "incident-clear", "--state-dir", stateDir, "--reason", "ok"],
      { ...partial, env: environment, now: () => new Date("2026-08-03T15:00:00-03:00") },
    );
    expect(partialExit).toBe(2);
    expect(JSON.parse(partial.stdout[0] ?? "")).toMatchObject({
      ok: false,
      error: { code: "INVALID_ARGUMENT" },
    });
    expect(await activeIncident(stateDir)).not.toBeNull();

    const missingReason = cliHarness();
    const missingReasonExit = await run(
      [
        "--json",
        "network",
        "incident-clear",
        "--state-dir",
        stateDir,
        "--account-access-confirmed",
        "--warning-cleared-confirmed",
      ],
      {
        ...missingReason,
        env: environment,
        now: () => new Date("2026-08-03T15:00:00-03:00"),
      },
    );
    expect(missingReasonExit).toBe(2);

    const cleared = await networkIncidentClear({
      stateDir,
      reason: "reviewed LinkedIn UI; warning gone",
      accountAccessConfirmed: true,
      warningClearedConfirmed: true,
    });
    expect(cleared).toMatchObject({
      command: "network incident-clear",
      cleared: true,
      incident: { active: false },
    });
    expect(await activeIncident(stateDir)).toBeNull();
  });

  test("CLI surfaces INCIDENT_ACTIVE envelope and exit code 7", async () => {
    const stateDir = await tempDir("ltn-incident-envelope-");
    await openIncident(stateDir, {
      kind: "login_required",
      reason: "LinkedIn login required",
      detail: "authwall",
      openedAt: "2026-08-03T12:00:00.000Z",
    });
    const harness = cliHarness();
    // Use a real default operation path through doctor (no browser) is not enough;
    // assertNoActiveIncident is client-side. Validate envelope mapping via direct throw path
    // by invoking incident-status after manually calling assert.
    await expect(assertNoActiveIncident(stateDir)).rejects.toBeInstanceOf(ActiveIncidentError);

    // Route a thrown ActiveIncidentError through CLI normalize via a custom operation.
    const operations = createDefaultOperations();
    const exit = await run(["--json", "network", "incident-status", "--state-dir", stateDir], {
      ...harness,
      operations: {
        ...operations,
        networkIncidentStatus: async () => {
          await assertNoActiveIncident(stateDir);
          return { unreachable: true };
        },
      },
      env: environment,
      now: () => new Date("2026-08-03T15:00:00-03:00"),
    });
    expect(exit).toBe(INCIDENT_ACTIVE_EXIT_CODE);
    expect(JSON.parse(harness.stdout[0] ?? "")).toMatchObject({
      ok: false,
      error: {
        code: INCIDENT_ACTIVE_CODE,
        details: { kind: "login_required" },
      },
    });
  });
});

describe("doctor incident check", () => {
  test("reports active incident as not ready", async () => {
    const stateDir = await tempDir("ltn-doctor-incident-");
    await openIncident(stateDir, {
      kind: "security_verification",
      reason: "LinkedIn security verification present",
      detail: "security verification",
      openedAt: "2026-08-03T12:00:00.000Z",
    });
    const result = await doctor(
      {
        stateDir,
        playwriterBin: process.execPath,
        networkSessionId: 7,
        analyticsSessionId: 8,
      },
      {
        bootstrapDatabase,
        createProbeId: () => "test",
        run: async (command) => {
          if (command.at(-2) === "session") {
            return { exitCode: 0, stdout: "7  network\n8  analytics\n", stderr: "" };
          }
          return { exitCode: 0, stdout: "playwriter/0.4.0\nUsage: ignored\n", stderr: "" };
        },
      },
    );
    expect(result.ready).toBe(false);
    expect(result.incident).toMatchObject({
      active: true,
      record: { kind: "security_verification", active: true },
    });
  });
});
