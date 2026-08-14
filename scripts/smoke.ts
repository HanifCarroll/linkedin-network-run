#!/usr/bin/env bun

import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { type CliIo, run } from "../src/cli.ts";
import type { CliOperations } from "../src/commands/types.ts";

const root = await mkdtemp(join(tmpdir(), "linkedin-tools-smoke-"));
const fakePlaywriter = join(root, "playwriter");
await writeFile(
  fakePlaywriter,
  `#!/usr/bin/env bun
const args=process.argv.slice(2);
if(args[0]==="--version"){console.log("playwriter/0.4.0");process.exit(0)}
if(args[0]==="session"&&args[1]==="list"){console.log("7  network\\n8  analytics");process.exit(0)}
console.error("unexpected fake Playwriter command");process.exit(2)
`,
);
await chmod(fakePlaywriter, 0o755);

try {
  const doctorOutput = await invoke(
    [
      "--json",
      "doctor",
      "--state-dir",
      join(root, "state"),
      "--playwriter-bin",
      fakePlaywriter,
      "--network-session",
      "7",
      "--analytics-session",
      "8",
    ],
    undefined,
  );
  if (doctorOutput.exitCode !== 0 || doctorOutput.value?.ok !== true) {
    throw new Error("doctor smoke failed");
  }

  const calls: string[] = [];
  const fakeOperations: CliOperations = {
    doctor: async () => ({ ready: true }),
    networkStatus: async () => {
      calls.push("network status");
      return { state: "not_started" };
    },
    networkReport: async () => {
      calls.push("network report");
      return { attempts: [] };
    },
    networkTick: async (input) => {
      if (input.batchSize !== 5 || input.maxRealSends !== 30 || input.sessionId !== "auto") {
        throw new Error("network smoke did not receive the final completion contract");
      }
      calls.push("network tick");
      return { state: "progress", sendsThisTick: 0 };
    },
    networkReconcile: async (input) => {
      if (input.sessionId !== "auto") throw new Error("network reconcile did not parse auto");
      calls.push("network reconcile");
      return { state: "progress" };
    },
    networkRunEnd: async (input) => {
      calls.push("network run-end");
      return { command: "network run-end", localDate: input.localDate, run: { status: "blocked" } };
    },
    networkSessionReset: async () => {
      calls.push("network session-reset");
      return { command: "network session-reset", reset: { ok: true } };
    },
    networkOpen: async (input) => {
      calls.push("network open");
      return { command: "network open", page: input.page, outcome: "succeeded" };
    },
    networkIncidentStatus: async () => {
      calls.push("network incident-status");
      return { active: false, incident: null };
    },
    networkIncidentClear: async () => {
      calls.push("network incident-clear");
      return { cleared: true };
    },
    analyticsExport: async (input) => {
      if (input.sessionId !== "auto") throw new Error("analytics export did not parse auto");
      calls.push("analytics export");
      return { status: "completed" };
    },
    migrationDryRun: async () => {
      calls.push("migration dry-run");
      return { proposalOnly: true };
    },
    jobsSearch: async () => {
      calls.push("jobs search");
      return { collected: 0 };
    },
    jobsCollect: async () => {
      calls.push("jobs collect");
      return { captured: 0 };
    },
    jobsEnrich: async () => {
      calls.push("jobs enrich");
      return { enriched: 0 };
    },
    jobsList: async () => {
      calls.push("jobs list");
      return { count: 0, jobs: [] };
    },
    jobsCheck: async () => {
      calls.push("jobs check");
      return { checked: 0, live: 0, dead: 0, unclear: 0 };
    },
    jobsFavorite: async () => {
      calls.push("jobs favorite");
      return { favorited: 0 };
    },
    jobsDraft: async () => {
      calls.push("jobs draft");
      return { job: null };
    },
    jobsSend: async (input) => {
      if (input.sessionId !== "auto") throw new Error("jobs send did not parse auto");
      calls.push("jobs send");
      return { sent: 0, skipped: 0, results: [] };
    },
  };
  const common = {
    operations: fakeOperations,
    now: () => new Date("2026-08-03T12:00:00-03:00"),
    env: {
      HOME: root,
      LINKEDIN_TOOLS_ANALYTICS_ACCOUNT: "Hanif",
    },
  };
  const commands: readonly (readonly string[])[] = [
    ["--json", "network", "status"],
    ["--json", "network", "report"],
    [
      "--json",
      "network",
      "tick",
      "--allow-send",
      "--batch-size",
      "5",
      "--max-real-sends",
      "30",
      "--session",
      "auto",
    ],
    ["--json", "network", "reconcile", "--session", "auto"],
    ["--json", "network", "session-reset"],
    [
      "--json",
      "analytics",
      "export",
      "--out",
      join(root, "analytics-{endDate}.xlsx"),
      "--download-root",
      join(root, "downloads"),
      "--period",
      "previous-7-days",
      "--session",
      "auto",
    ],
    ["--json", "migration", "dry-run", "--source-root", join(root, "legacy")],
  ];
  for (const command of commands) {
    const output = await invoke(command, common);
    if (output.exitCode !== 0 || output.value?.ok !== true) {
      throw new Error(`smoke command failed: ${command.join(" ")}`);
    }
  }
  const denied = await invoke(["--json", "network", "tick"], common);
  if (denied.exitCode !== 3 || denied.value?.error?.code !== "SEND_NOT_AUTHORIZED") {
    throw new Error("send authorization smoke failed");
  }
  if (calls.length !== commands.length) throw new Error("fake operation dispatch count mismatch");
  console.log(
    JSON.stringify({
      ok: true,
      data: {
        command: "smoke",
        doctor: "passed",
        fakeCommands: calls,
        liveBrowser: false,
        liveLinkedIn: false,
      },
    }),
  );
} finally {
  await rm(root, { recursive: true, force: true });
}

async function invoke(
  argv: readonly string[],
  dependencies:
    | {
        readonly operations: CliOperations;
        readonly now: () => Date;
        readonly env: Readonly<Record<string, string | undefined>>;
      }
    | undefined,
): Promise<{
  readonly exitCode: number;
  readonly value: {
    readonly ok?: boolean;
    readonly error?: { readonly code?: string };
  };
}> {
  const stdout: string[] = [];
  const stderr: string[] = [];
  const io: CliIo = {
    stdout: (value) => stdout.push(value),
    stderr: (value) => stderr.push(value),
  };
  const exitCode = await run(argv, { ...dependencies, io });
  if (stderr.length > 0) throw new Error(stderr.join("\n"));
  const value: unknown = JSON.parse(stdout.at(-1) ?? "null");
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("CLI smoke output was not a JSON object");
  }
  return { exitCode, value };
}
