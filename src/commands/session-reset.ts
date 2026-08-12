import { join } from "node:path";
import type { Database } from "bun:sqlite";
import { CliError } from "../core/errors.ts";
import { type OpenDatabase, openDatabase } from "../db/database.ts";
import { NetworkEngine } from "../network/index.ts";
import { readSessionBinding } from "./sessions.ts";
import type { NetworkSessionResetInput } from "./types.ts";

type CommandReceipt = {
  readonly exitCode: number;
  readonly stdout: string;
  readonly stderr: string;
};

export type SessionResetDependencies = {
  readonly openDatabase: (path: string) => OpenDatabase;
  readonly run: (command: readonly string[]) => Promise<CommandReceipt>;
};

const defaultDependencies: SessionResetDependencies = {
  openDatabase,
  run: async (command) => {
    const child = Bun.spawn([...command], { stdout: "pipe", stderr: "pipe" });
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ]);
    return { stdout, stderr, exitCode };
  },
};

export async function networkSessionReset(
  input: NetworkSessionResetInput,
  dependencies: SessionResetDependencies = defaultDependencies,
): Promise<unknown> {
  const binding = await readSessionBinding(input.stateDir, "network");
  if (binding === null) {
    throw new CliError(
      "NETWORK_SESSION_NOT_BOUND",
      "no network Playwriter session is bound; run network tick once with --session auto to create one",
      { details: { nextAction: "network tick --session auto" }, exitCode: 4 },
    );
  }
  const sessionId = binding.sessionId;

  const receipt = await dependencies.run([
    input.playwriterBin,
    "session",
    "reset",
    String(sessionId),
  ]);
  if (receipt.exitCode !== 0) {
    throw new CliError(
      "PLAYWRITER_SESSION_RESET_FAILED",
      `Playwriter session reset exited ${receipt.exitCode}`,
      {
        details: {
          sessionId,
          stderr: receipt.stderr.trim().slice(0, 500),
        },
        exitCode: 4,
      },
    );
  }

  const opened = dependencies.openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const run = latestRunSummary(opened.database);
    return {
      command: "network session-reset",
      workflow: "network",
      sessionId,
      reset: {
        ok: true,
        output: receipt.stdout.trim(),
      },
      context: {
        bindingPath: join(input.stateDir, "sessions", "network.json"),
        run: run?.projection ?? null,
      },
    };
  } finally {
    opened.database.close();
  }
}

function latestRunSummary(
  database: Database,
): { readonly localDate: string; readonly projection: unknown } | null {
  const row = database
    .query<{ id: string; local_date: string }, []>(
      `SELECT id, local_date FROM daily_runs ORDER BY local_date DESC, created_at DESC LIMIT 1`,
    )
    .get();
  if (row === null) return null;
  const engine = new NetworkEngine(database);
  return {
    localDate: row.local_date,
    projection: engine.projection(row.id),
  };
}
