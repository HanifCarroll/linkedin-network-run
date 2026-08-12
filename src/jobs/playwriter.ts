import { assertNoActiveIncident } from "../browser/incident.ts";
import { CliError } from "../core/errors.ts";

export type JobsScriptOutcome = {
  readonly ok: true;
  readonly data: Record<string, unknown>;
};

/**
 * Run one jobs playwriter script. Spawns the playwriter binary directly with
 * the script as `-e` code, gates on the shared incident file, and parses the
 * final JSON stdout line as the result envelope. No descriptor machinery.
 */
export async function runJobsScript(options: {
  readonly playwriterBin: string;
  readonly sessionId: number;
  readonly script: string;
  readonly timeoutMs: number;
  readonly stateDir: string;
}): Promise<JobsScriptOutcome> {
  await assertNoActiveIncident(options.stateDir);
  const child = Bun.spawn(
    [
      options.playwriterBin,
      "--timeout",
      String(options.timeoutMs),
      "-s",
      String(options.sessionId),
      "-e",
      options.script,
    ],
    { stdout: "pipe", stderr: "pipe", stdin: "ignore" },
  );
  const [stdout, stderr] = await Promise.all([
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
  ]);
  const exitCode = await child.exited;
  const lastJson = lastJsonLine(stdout);
  if (exitCode !== 0 || lastJson === null) {
    const tail = (stderr.trim() || stdout.trim()).split(/\r?\n/).slice(-5).join("\n");
    throw new CliError(
      "JOBS_SCRIPT_FAILED",
      `playwriter jobs script failed (exit ${exitCode}): ${tail}`,
      { exitCode: 1 },
    );
  }
  if (lastJson.ok !== true || typeof lastJson.data !== "object" || lastJson.data === null) {
    throw new CliError(
      "JOBS_SCRIPT_FAILED",
      `playwriter jobs script returned a non-success envelope: ${stdout.trim().slice(0, 300)}`,
      { exitCode: 1 },
    );
  }
  return lastJson as JobsScriptOutcome;
}

function lastJsonLine(stdout: string): { ok: boolean; data?: unknown } | null {
  for (const line of stdout.split(/\r?\n/).reverse()) {
    const cleaned = line.trim().replace(/^\[log\]\s*/, "");
    if (!cleaned.startsWith("{")) continue;
    try {
      const value = JSON.parse(cleaned) as { ok?: unknown; data?: unknown };
      if (typeof value === "object" && value !== null) return value as { ok: boolean; data?: unknown };
    } catch {
      // not JSON; keep scanning
    }
  }
  return null;
}
