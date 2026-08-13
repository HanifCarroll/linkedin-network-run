import { join } from "node:path";
import { CliError } from "../core/errors.ts";
import { openDatabase } from "../db/database.ts";
import {
  buildCaptureScript,
  buildEnrichPoolScript,
  buildEnrichScript,
  buildSearchUrl,
  buildSendScript,
  JobsEngine,
  runJobsScript,
} from "../jobs/index.ts";
import type { JobsScriptOutcome } from "../jobs/playwriter.ts";
import type { CapturedJob, CollectedJob, JobRow } from "../jobs/types.ts";
import { PlaywriterClient } from "../playwriter/client.ts";
import { resolvePlaywriterSession, type SessionResolutionRequest } from "./sessions.ts";
import type {
  JobsCollectInput,
  JobsDraftInput,
  JobsEnrichInput,
  JobsFavoriteInput,
  JobsListInput,
  JobsSearchInput,
  JobsSendInput,
} from "./types.ts";

export type JobsDependencies = {
  readonly resolveSession?: (request: SessionResolutionRequest) => Promise<number>;
  readonly runScript?: typeof runJobsScript;
  readonly resetSession?: (sessionId: number) => Promise<void>;
  readonly now?: () => string;
};

const nowDefault = () => new Date().toISOString();

const defaultDependencies: JobsDependencies = {
  resolveSession: resolvePlaywriterSession,
  runScript: runJobsScript,
  now: nowDefault,
};

const defaultResetSession = (input: {
  readonly stateDir: string;
  readonly playwriterBin: string;
}): ((sessionId: number) => Promise<void>) => {
  const client = new PlaywriterClient({
    executable: input.playwriterBin,
    invocationRoot: join(input.stateDir, "receipts", "playwriter", "jobs"),
    stateDir: input.stateDir,
  });
  return async (sessionId: number) => {
    await client.resetSession(sessionId);
  };
};

export async function jobsSearch(
  input: JobsSearchInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? resolvePlaywriterSession;
  const runScript = dependencies.runScript ?? runJobsScript;
  const now = dependencies.now ?? nowDefault;
  const sessionId = await resolveSession({
    workflow: "jobs",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
  });
  const target = input.hiringTeamTarget ?? 0;
  const searchUrl = buildSearchUrl(input);
  const resetSession = dependencies.resetSession ?? defaultResetSession(input);
  const dbPath = join(input.stateDir, "linkedin-tools.db");
  const phase = (script: string, timeoutMs: number) =>
    runScript({
      playwriterBin: input.playwriterBin,
      sessionId,
      script,
      timeoutMs,
      stateDir: input.stateDir,
    });
  const dbRows = (): JobRow[] => {
    const opened = openDatabase(dbPath);
    try {
      return new JobsEngine(opened.database).listJobs({ withHiringTeam: false });
    } finally {
      opened.database.close();
    }
  };
  const countTeams = (rows: readonly JobRow[] = dbRows()) =>
    rows.filter((row) => row.hasHiringTeam).length;
  // Resume: skip jobs already enriched (found a team OR landed a company) so an
  // interrupted/session-walled run continues with new work instead of revisiting.
  const visitedIds = (rows: readonly JobRow[] = dbRows()) =>
    rows.filter((row) => row.hasHiringTeam || row.company.length > 0).map((row) => row.id);
  const checkpoint = (jobs: readonly CollectedJob[]): void => {
    if (jobs.length === 0) return;
    const opened = openDatabase(dbPath);
    try {
      new JobsEngine(opened.database).upsertJobs(jobs, now());
    } finally {
      opened.database.close();
    }
  };

  const startRows = dbRows();
  const startIds = new Set(startRows.map((row) => row.id));
  const startTeams = countTeams(startRows);
  let targetMet = target > 0 ? startTeams >= target : false;
  let cardsTotal = 0;
  let pagesCollected = 0;
  const maxCycles = 8;
  const maxEnrichPerCycle = Math.ceil((input.pages * 25) / 9) + 2;
  // Soft wall-clock budget: stop starting new phases once a run approaches the
  // ~5-min relay-degradation threshold. Progress is checkpointed, so a timed-out
  // run resumes cleanly on the next invocation (visited IDs are skipped).
  const SEARCH_BUDGET_MS = 240_000;
  const startedAt = Date.now();
  let timedOut = false;
  const overBudget = (): boolean => Date.now() - startedAt >= SEARCH_BUDGET_MS;

  // Sessions drop for a few reasons: the playwriter CLI (<=0.4.0) ends long
  // execute requests at Node/Undici's fixed 300s response-header timeout
  // (remorses/playwriter#74), and the relay/extension disconnects under
  // sustained load. Each completed job is checkpointed to the DB as it lands,
  // so on a session failure we reset the connection, re-capture a fresh pool,
  // and continue — every cycle does new work until the team target is met or
  // the pool is exhausted. runPhase returns null when the session was reset
  // (caller retries with fresh state).
  const runPhase = async (script: string, timeoutMs: number): Promise<JobsScriptOutcome | null> => {
    try {
      return await phase(script, timeoutMs);
    } catch (error) {
      if (isSessionFailure(error)) {
        await resetSession(sessionId).catch(() => {});
        return null;
      }
      throw error;
    }
  };

  for (let cycle = 0; cycle < maxCycles && !targetMet && !timedOut; cycle += 1) {
    if (overBudget()) {
      timedOut = true;
      break;
    }
    const skipIds = target > 0 ? visitedIds() : [];
    const captureScript = buildCaptureScript({
      searchUrl,
      pages: input.pages,
      hiringTeamLimit: input.hiringTeamLimit,
      skipIds,
    });
    const capture = await runPhase(captureScript.script, captureScript.timeoutMs);
    if (capture === null) continue;
    cardsTotal = Number.isSafeInteger(capture.data?.cardsTotal)
      ? Number(capture.data.cardsTotal)
      : cardsTotal;
    pagesCollected = Number.isSafeInteger(capture.data?.pagesCollected)
      ? Number(capture.data.pagesCollected)
      : pagesCollected;
    let remaining = Number.isSafeInteger(capture.data?.pool) ? Number(capture.data.pool) : 0;
    for (
      let enrichCall = 0;
      enrichCall < maxEnrichPerCycle && remaining > 0 && !targetMet && !timedOut;
      enrichCall += 1
    ) {
      if (overBudget()) {
        timedOut = true;
        break;
      }
      const enrichScript = buildEnrichScript({ batchSize: 3, workers: 1 });
      const enrich = await runPhase(enrichScript.script, enrichScript.timeoutMs);
      if (enrich === null) break;
      const completed = Array.isArray(enrich.data?.completed) ? enrich.data.completed : [];
      // A real job view always yields a company; empty rows mean extraction
      // failed (page never rendered — usually a degrading/looming-relay-drop
      // session). Discard those (they're retried next cycle) and treat an
      // entirely-empty batch as a session-quality failure: reset and re-capture
      // instead of recording zeros as progress.
      const real = completed.filter(
        (job): job is CollectedJob => isRecord(job) && String(job.company ?? "").length > 0,
      );
      if (completed.length > 0 && real.length === 0) {
        await resetSession(sessionId).catch(() => {});
        break;
      }
      checkpoint(real);
      remaining = Number.isSafeInteger(enrich.data?.remaining) ? Number(enrich.data.remaining) : 0;
      if (target > 0 && countTeams() >= target) targetMet = true;
    }
    if (target > 0 && countTeams() >= target) targetMet = true;
  }

  const finalRows = dbRows();
  const collected = finalRows.filter((row) => !startIds.has(row.id)).length;
  const withHiringTeam = countTeams(finalRows);
  return {
    command: "jobs search",
    keywords: input.keywords,
    location: input.location,
    postedWithinDays: input.postedWithinDays ?? null,
    remote: input.remote ?? false,
    hiringTeamTarget: target,
    targetMet: target === 0 ? true : withHiringTeam >= target,
    timedOut,
    cardsTotal,
    pagesCollected,
    collected,
    withHiringTeam,
    jobs: finalRows,
  };
}

export async function jobsCollect(
  input: JobsCollectInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? resolvePlaywriterSession;
  const runScript = dependencies.runScript ?? runJobsScript;
  const now = dependencies.now ?? nowDefault;
  const sessionId = await resolveSession({
    workflow: "jobs",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
  });
  const searchUrl = buildSearchUrl(input);
  const resetSession = dependencies.resetSession ?? defaultResetSession(input);
  const dbPath = join(input.stateDir, "linkedin-tools.db");
  const runPhase = async (script: string, timeoutMs: number): Promise<JobsScriptOutcome | null> => {
    try {
      return await runScript({
        playwriterBin: input.playwriterBin,
        sessionId,
        script,
        timeoutMs,
        stateDir: input.stateDir,
      });
    } catch (error) {
      if (isSessionFailure(error)) {
        await resetSession(sessionId).catch(() => {});
        return null;
      }
      throw error;
    }
  };
  const knownIds = (): string[] => {
    const opened = openDatabase(dbPath);
    try {
      return new JobsEngine(opened.database).listJobs({ withHiringTeam: false }).map((r) => r.id);
    } finally {
      opened.database.close();
    }
  };

  let cardsTotal = 0;
  let pagesCollected = 0;
  let captured = 0;
  for (let cycle = 0; cycle < 8; cycle += 1) {
    const { script, timeoutMs } = buildCaptureScript({
      searchUrl,
      pages: input.pages,
      hiringTeamLimit: Math.min(input.pages * 25, 200),
      skipIds: knownIds(),
    });
    const result = await runPhase(script, timeoutMs);
    if (result === null) continue;
    cardsTotal = Number.isSafeInteger(result.data?.cardsTotal)
      ? Number(result.data.cardsTotal)
      : cardsTotal;
    pagesCollected = Number.isSafeInteger(result.data?.pagesCollected)
      ? Number(result.data.pagesCollected)
      : pagesCollected;
    const jobs: CapturedJob[] = [];
    const raw = result.data?.jobs;
    if (Array.isArray(raw)) {
      for (const item of raw) {
        if (isRecord(item) && typeof item.id === "string" && typeof item.title === "string") {
          jobs.push({ id: item.id, title: item.title });
        }
      }
    }
    if (jobs.length > 0) {
      const opened = openDatabase(dbPath);
      try {
        captured = new JobsEngine(opened.database).storeCapturedJobs(jobs, now());
      } finally {
        opened.database.close();
      }
    }
    break;
  }
  return {
    command: "jobs collect",
    keywords: input.keywords,
    location: input.location,
    cardsTotal,
    pagesCollected,
    captured,
  };
}

export async function jobsEnrich(
  input: JobsEnrichInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? resolvePlaywriterSession;
  const runScript = dependencies.runScript ?? runJobsScript;
  const now = dependencies.now ?? nowDefault;
  const sessionId = await resolveSession({
    workflow: "jobs",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
  });
  const resetSession = dependencies.resetSession ?? defaultResetSession(input);
  const dbPath = join(input.stateDir, "linkedin-tools.db");
  const batchSize = 3;
  const BUDGET_MS = 240_000;
  const startedAt = Date.now();
  const runPhase = async (script: string, timeoutMs: number): Promise<JobsScriptOutcome | null> => {
    try {
      return await runScript({
        playwriterBin: input.playwriterBin,
        sessionId,
        script,
        timeoutMs,
        stateDir: input.stateDir,
      });
    } catch (error) {
      if (isSessionFailure(error)) {
        await resetSession(sessionId).catch(() => {});
        return null;
      }
      throw error;
    }
  };
  const capturedRows = (): JobRow[] => {
    const opened = openDatabase(dbPath);
    try {
      return new JobsEngine(opened.database).listJobs({
        status: "captured",
        withHiringTeam: false,
      });
    } finally {
      opened.database.close();
    }
  };
  const upsert = (jobs: CollectedJob[]): void => {
    if (jobs.length === 0) return;
    const opened = openDatabase(dbPath);
    try {
      new JobsEngine(opened.database).upsertJobs(jobs, now());
    } finally {
      opened.database.close();
    }
  };

  const pool = capturedRows();
  const toEnrich = input.limit === undefined ? pool : pool.slice(0, input.limit);
  let enriched = 0;
  for (let i = 0; i < toEnrich.length && Date.now() - startedAt < BUDGET_MS; i += batchSize) {
    const batch = toEnrich.slice(i, i + batchSize).map((r) => ({ id: r.id, title: r.title }));
    const { script, timeoutMs } = buildEnrichPoolScript({ jobs: batch });
    const result = await runPhase(script, timeoutMs);
    if (result === null) break;
    const completed = Array.isArray(result.data?.completed) ? result.data.completed : [];
    const real = completed.filter(
      (job): job is CollectedJob => isRecord(job) && String(job.company ?? "").length > 0,
    );
    if (completed.length > 0 && real.length === 0) {
      await resetSession(sessionId).catch(() => {});
      break;
    }
    upsert(real);
    enriched += real.length;
  }
  const remaining = capturedRows().length;
  return { command: "jobs enrich", enriched, remaining };
}

function isSessionFailure(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /fetch failed|session lost|extension not connected|context was destroyed|page closed|relay|disconnect|econn|timed out|execution timeout/i.test(
    message,
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function jobsList(input: JobsListInput): Promise<unknown> {
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const rows = new JobsEngine(opened.database).listJobs({
      ...(input.status === undefined ? {} : { status: input.status }),
      withHiringTeam: input.withHiringTeam,
    });
    return { command: "jobs list", count: rows.length, jobs: rows };
  } finally {
    opened.database.close();
  }
}

export async function jobsFavorite(
  input: JobsFavoriteInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const rows = new JobsEngine(opened.database).favoriteJobs(input.ids, now());
    return { command: "jobs favorite", favorited: rows.length, jobs: rows };
  } finally {
    opened.database.close();
  }
}

export async function jobsDraft(
  input: JobsDraftInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const now = dependencies.now ?? nowDefault;
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  try {
    const row = new JobsEngine(opened.database).storeDraft(input.id, input.message, now());
    return { command: "jobs draft", job: row };
  } finally {
    opened.database.close();
  }
}

export async function jobsSend(
  input: JobsSendInput,
  dependencies: JobsDependencies = defaultDependencies,
): Promise<unknown> {
  const resolveSession = dependencies.resolveSession ?? resolvePlaywriterSession;
  const runScript = dependencies.runScript ?? runJobsScript;
  const now = dependencies.now ?? nowDefault;
  const sessionId = await resolveSession({
    workflow: "jobs",
    selection: input.sessionId,
    stateDir: input.stateDir,
    playwriterBin: input.playwriterBin,
  });
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  let targets: ReturnType<JobsEngine["draftedJobs"]>;
  try {
    const engine = new JobsEngine(opened.database);
    targets = input.id === undefined ? engine.draftedJobs() : [engine.requireJob(input.id)];
  } finally {
    opened.database.close();
  }
  if (targets.length === 0)
    throw new CliError("JOBS_NOTHING_TO_SEND", "no drafted jobs to send", { exitCode: 2 });

  const results: unknown[] = [];
  for (const job of targets) {
    const member = job.hiringTeam[0];
    if (member === undefined)
      throw new CliError(
        "JOBS_NO_HIRING_TEAM",
        `job ${job.id} has no hiring team member to message`,
        {
          exitCode: 2,
        },
      );
    if (job.message === null || job.message.trim().length === 0)
      throw new CliError("JOBS_NO_DRAFT", `job ${job.id} has no drafted message`, { exitCode: 2 });
    const { script, timeoutMs } = buildSendScript({
      jobId: job.id,
      memberName: member.name,
      profileUrl: member.profileUrl,
      message: job.message,
    });
    const outcome = await runScript({
      playwriterBin: input.playwriterBin,
      sessionId,
      script,
      timeoutMs,
      stateDir: input.stateDir,
    });
    const data = outcome.data as Record<string, unknown>;
    const status = data.status;
    const openedAgain = openDatabase(join(input.stateDir, "linkedin-tools.db"));
    try {
      if (status === "sent") new JobsEngine(openedAgain.database).markSent(job.id, now());
    } finally {
      openedAgain.database.close();
    }
    results.push({ jobId: job.id, title: job.title, member: member.name, ...data });
  }
  return {
    command: "jobs send",
    sent: results.filter((r) => (r as Record<string, unknown>).status === "sent").length,
    skipped: results.filter((r) => (r as Record<string, unknown>).status !== "sent").length,
    results,
  };
}
