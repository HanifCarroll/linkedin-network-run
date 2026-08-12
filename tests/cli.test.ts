import { describe, expect, test } from "bun:test";
import { type CliIo, run } from "../src/cli.ts";
import type { CliOperations } from "../src/commands/types.ts";
import { CliError } from "../src/core/errors.ts";

function harness(override: Partial<CliOperations> = {}) {
  const calls: Array<{ name: keyof CliOperations; input: unknown }> = [];
  const operation =
    <T extends keyof CliOperations>(name: T) =>
    async (input: unknown) => {
      calls.push({ name, input });
      return { command: name, input };
    };
  const operations: CliOperations = {
    doctor: operation("doctor"),
    networkStatus: operation("networkStatus"),
    networkReport: operation("networkReport"),
    networkTick: operation("networkTick"),
    networkReconcile: operation("networkReconcile"),
    networkRunEnd: operation("networkRunEnd"),
    networkSessionReset: operation("networkSessionReset"),
    networkIncidentStatus: operation("networkIncidentStatus"),
    networkIncidentClear: operation("networkIncidentClear"),
    analyticsExport: operation("analyticsExport"),
    migrationDryRun: operation("migrationDryRun"),
    jobsSearch: operation("jobsSearch"),
    jobsList: operation("jobsList"),
    jobsFavorite: operation("jobsFavorite"),
    jobsDraft: operation("jobsDraft"),
    jobsSend: operation("jobsSend"),
    ...override,
  };
  const stdout: string[] = [];
  const stderr: string[] = [];
  const io: CliIo = {
    stdout: (value) => stdout.push(value),
    stderr: (value) => stderr.push(value),
  };
  return { calls, operations, stdout, stderr, io };
}

const environment = {
  HOME: "/Users/test",
  LINKEDIN_TOOLS_NETWORK_SESSION: "7",
  LINKEDIN_TOOLS_ANALYTICS_SESSION: "8",
  LINKEDIN_TOOLS_ANALYTICS_ACCOUNT: "Hanif",
} as const;

const now = () => new Date("2026-08-03T15:00:00-03:00");

describe("CLI", () => {
  test("help declares every command and the Playwriter-only no-lease boundary", async () => {
    const context = harness();
    expect(await run(["--help"], { ...context, now, env: environment })).toBe(0);
    expect(context.stdout[0]).toContain("network reconcile");
    expect(context.stdout[0]).toContain("Playwriter is the only browser boundary");
    expect(context.stdout[0]).toContain("no browser lease");
    expect(context.calls).toHaveLength(0);
  });

  test("routes doctor and emits one stable JSON envelope", async () => {
    const context = harness();
    const exit = await run(
      [
        "--json",
        "doctor",
        "--state-dir",
        "/tmp/linkedin-state",
        "--network-session",
        "11",
        "--analytics-session",
        "12",
      ],
      { ...context, now, env: environment },
    );
    expect(exit).toBe(0);
    expect(context.stderr).toEqual([]);
    expect(JSON.parse(context.stdout[0] ?? "")).toMatchObject({
      ok: true,
      data: { command: "doctor" },
    });
    expect(context.calls).toEqual([
      {
        name: "doctor",
        input: expect.objectContaining({
          stateDir: "/tmp/linkedin-state",
          networkSessionId: 11,
          analyticsSessionId: 12,
        }),
      },
    ]);
  });

  test("defaults network reads to the current local date", async () => {
    const context = harness();
    expect(await run(["--json", "network", "status"], { ...context, now, env: environment })).toBe(
      0,
    );
    expect(context.calls[0]).toMatchObject({
      name: "networkStatus",
      input: { localDate: "2026-08-03" },
    });
  });

  test("requires explicit send authorization before dispatching a tick", async () => {
    const context = harness();
    const exit = await run(["--json", "network", "tick"], {
      ...context,
      now,
      env: environment,
    });
    expect(exit).toBe(3);
    expect(context.calls).toHaveLength(0);
    expect(JSON.parse(context.stdout[0] ?? "")).toEqual({
      ok: false,
      error: {
        code: "SEND_NOT_AUTHORIZED",
        message: "network tick requires the explicit --allow-send flag",
      },
    });
  });

  test("passes bounded tick options and configured session", async () => {
    const context = harness();
    expect(
      await run(
        [
          "--json",
          "network",
          "tick",
          "--allow-send",
          "--batch-size",
          "4",
          "--target",
          "30",
          "--max-real-sends",
          "29",
        ],
        { ...context, now, env: environment },
      ),
    ).toBe(0);
    expect(context.calls[0]).toMatchObject({
      name: "networkTick",
      input: {
        allowSend: true,
        target: 30,
        batchSize: 4,
        maxRealSends: 29,
        sessionId: 7,
        localDate: "2026-08-03",
      },
    });
  });

  test("defaults a scheduled tick to completion-capable 30 with five-send microbatches", async () => {
    const context = harness();
    expect(
      await run(["--json", "network", "tick", "--allow-send"], {
        ...context,
        now,
        env: environment,
      }),
    ).toBe(0);
    expect(context.calls[0]).toMatchObject({
      name: "networkTick",
      input: { target: 30, batchSize: 5, maxRealSends: 30 },
    });
  });

  test("parses network run-end with date and reason", async () => {
    const context = harness();
    expect(
      await run(
        ["--json", "network", "run-end", "--date", "2026-08-04", "--reason", "wrapping up"],
        { ...context, now, env: environment },
      ),
    ).toBe(0);
    expect(context.calls[0]).toMatchObject({
      name: "networkRunEnd",
      input: {
        localDate: "2026-08-04",
        reason: "wrapping up",
        stateDir: expect.stringMatching(/linkedin-tools-next/),
      },
    });
  });

  test("rejects network run-end without a reason", async () => {
    const context = harness();
    const exit = await run(["--json", "network", "run-end", "--date", "2026-08-04"], {
      ...context,
      now,
      env: environment,
    });
    expect(exit).not.toBe(0);
    expect(context.calls).toHaveLength(0);
    expect(JSON.parse(context.stdout[0] ?? "")).toMatchObject({
      ok: false,
      error: { code: "INVALID_ARGUMENT" },
    });
  });

  test("parses network session-reset with state dir and playwriter bin", async () => {
    const context = harness();
    expect(
      await run(["--json", "network", "session-reset"], {
        ...context,
        now,
        env: environment,
      }),
    ).toBe(0);
    expect(context.calls[0]).toMatchObject({
      name: "networkSessionReset",
      input: {
        stateDir: expect.stringMatching(/linkedin-tools-next/),
        playwriterBin: "/Users/hanifcarroll/.bun/bin/playwriter",
      },
    });
  });

  test("parses auto sessions for browser-capable commands without resolving them", async () => {
    const context = harness();
    const commands: readonly (readonly string[])[] = [
      ["--json", "network", "tick", "--allow-send", "--session", "auto"],
      ["--json", "network", "reconcile", "--session", "auto"],
      [
        "--json",
        "analytics",
        "export",
        "--session",
        "auto",
        "--period",
        "previous-7-days",
        "--download-root",
        "/tmp/downloads",
        "--out",
        "/tmp/analytics.xlsx",
      ],
    ];

    for (const command of commands) {
      expect(await run(command, { ...context, now, env: environment })).toBe(0);
    }
    expect(context.calls.map(({ input }) => input)).toEqual([
      expect.objectContaining({ sessionId: "auto", target: 30, batchSize: 5 }),
      expect.objectContaining({ sessionId: "auto" }),
      expect.objectContaining({ sessionId: "auto" }),
    ]);
  });

  test("maps previous seven complete days and expands analytics path templates", async () => {
    const context = harness();
    const exit = await run(
      [
        "--json",
        "analytics",
        "export",
        "--out",
        "/tmp/analytics-{startDate}-{endDate}.xlsx",
        "--receipt",
        "/tmp/receipt-{endDate}.json",
        "--download-root",
        "/tmp/downloads",
        "--period",
        "previous-7-days",
      ],
      { ...context, now, env: environment },
    );
    expect(exit).toBe(0);
    expect(context.calls[0]).toMatchObject({
      name: "analyticsExport",
      input: {
        expectedStartDate: "2026-07-27",
        expectedEndDate: "2026-08-02",
        expectedAccount: "Hanif",
        outputPath: "/tmp/analytics-2026-07-27-2026-08-02.xlsx",
        receiptPath: "/tmp/receipt-2026-08-02.json",
        sessionId: 8,
      },
    });
  });

  test("rejects conflicting analytics date options", async () => {
    const context = harness();
    const exit = await run(
      [
        "--json",
        "analytics",
        "export",
        "--out",
        "/tmp/a.xlsx",
        "--download-root",
        "/tmp/downloads",
        "--period",
        "previous-7-days",
        "--start-date",
        "2026-07-27",
      ],
      { ...context, now, env: environment },
    );
    expect(exit).toBe(2);
    expect(context.calls).toHaveLength(0);
    expect(JSON.parse(context.stdout[0] ?? "")).toMatchObject({
      error: { code: "INVALID_ARGUMENT" },
    });
  });

  test("rejects unknown, duplicate, invalid date, integer, and relative path options", async () => {
    const cases: readonly (readonly string[])[] = [
      ["network", "status", "--wat"],
      ["network", "status", "--date", "2026-02-30"],
      ["network", "tick", "--allow-send", "--batch-size", "6"],
      ["network", "tick", "--allow-send", "--target", "29"],
      ["network", "tick", "--allow-send", "--target", "31"],
      ["network", "tick", "--allow-send", "--max-real-sends", "31"],
      ["network", "tick", "--allow-send", "--no-audit"],
      ["network", "tick", "--allow-send", "--max-sends", "5"],
      ["migration", "dry-run", "--source-root", "relative"],
      ["doctor", "--state-dir", "/tmp/a", "--state-dir", "/tmp/b"],
    ];
    for (const args of cases) {
      const context = harness();
      expect(await run(["--json", ...args], { ...context, now, env: environment })).toBe(2);
      expect(context.calls).toHaveLength(0);
      expect(JSON.parse(context.stdout[0] ?? "")).toMatchObject({
        error: { code: "INVALID_ARGUMENT" },
      });
    }
  });

  test("migration exposes dry-run only", async () => {
    const context = harness();
    expect(
      await run(["--json", "migration", "dry-run", "--source-root", "/tmp/legacy"], {
        ...context,
        now,
        env: environment,
      }),
    ).toBe(0);
    expect(context.calls[0]).toEqual({
      name: "migrationDryRun",
      input: { sourceRoot: "/tmp/legacy" },
    });
    const rejected = harness();
    expect(
      await run(["--json", "migration", "apply", "--source-root", "/tmp/legacy"], {
        ...rejected,
        now,
        env: environment,
      }),
    ).toBe(2);
    expect(rejected.calls).toHaveLength(0);
  });

  test("operation errors retain deterministic codes and details", async () => {
    const context = harness({
      networkReport: async () => {
        throw new CliError("NETWORK_BLOCKED", "blocked", {
          details: { reason: "checkpoint" },
          exitCode: 4,
        });
      },
    });
    expect(await run(["--json", "network", "report"], { ...context, now, env: environment })).toBe(
      4,
    );
    expect(JSON.parse(context.stdout[0] ?? "")).toEqual({
      ok: false,
      error: {
        code: "NETWORK_BLOCKED",
        message: "blocked",
        details: { reason: "checkpoint" },
      },
    });
  });
});
