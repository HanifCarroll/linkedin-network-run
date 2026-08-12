import { join } from "node:path";
import { CliError } from "../core/errors.ts";
import { openDatabase } from "../db/database.ts";
import { JobsEngine, buildSearchScript, buildSearchUrl, buildSendScript, runJobsScript } from "../jobs/index.ts";
import type { CollectedJob } from "../jobs/types.ts";
import { resolvePlaywriterSession, type SessionResolutionRequest } from "./sessions.ts";
import type {
  JobsDraftInput,
  JobsFavoriteInput,
  JobsListInput,
  JobsSearchInput,
  JobsSendInput,
} from "./types.ts";

export type JobsDependencies = {
  readonly resolveSession?: (request: SessionResolutionRequest) => Promise<number>;
  readonly runScript?: typeof runJobsScript;
  readonly now?: () => string;
};

const nowDefault = () => new Date().toISOString();

const defaultDependencies: JobsDependencies = {
  resolveSession: resolvePlaywriterSession,
  runScript: runJobsScript,
  now: nowDefault,
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
  const searchUrl = buildSearchUrl(input);
  const { script, timeoutMs } = buildSearchScript({
    searchUrl,
    pages: input.pages,
    hiringTeamLimit: input.hiringTeamLimit,
  });
  const outcome = await runScript({
    playwriterBin: input.playwriterBin,
    sessionId,
    script,
    timeoutMs,
    stateDir: input.stateDir,
  });
  const parsed = parseSearchResult(outcome.data);
  const opened = openDatabase(join(input.stateDir, "linkedin-tools.db"));
  let collected = 0;
  try {
    collected = new JobsEngine(opened.database).upsertJobs(parsed.jobs, now());
  } finally {
    opened.database.close();
  }
  return {
    command: "jobs search",
    keywords: input.keywords,
    location: input.location,
    postedWithinDays: input.postedWithinDays ?? null,
    remote: input.remote ?? false,
    cardsTotal: parsed.cardsTotal,
    pagesCollected: parsed.pagesCollected,
    collected,
    withHiringTeam: parsed.jobs.filter((job) => job.hasHiringTeam).length,
    jobs: parsed.jobs,
  };
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
  let targets;
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
    if (job.hiringTeam.length === 0)
      throw new CliError("JOBS_NO_HIRING_TEAM", `job ${job.id} has no hiring team member to message`, {
        exitCode: 2,
      });
    if (job.message === null || job.message.trim().length === 0)
      throw new CliError("JOBS_NO_DRAFT", `job ${job.id} has no drafted message`, { exitCode: 2 });
    const member = job.hiringTeam[0]!;
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

function parseSearchResult(data: Record<string, unknown>): { jobs: CollectedJob[]; pagesCollected: number; cardsTotal: number } {
  const jobsRaw = Array.isArray(data.jobs) ? data.jobs : [];
  const jobs: CollectedJob[] = [];
  for (const raw of jobsRaw) {
    if (!isRecord(raw)) continue;
    const id = String(raw.id ?? "");
    if (id.length === 0) continue;
    const hiringTeam = Array.isArray(raw.hiringTeam)
      ? raw.hiringTeam.filter(isRecord).map((member) => ({
          name: String(member.name ?? ""),
          profileUrl: String(member.profileUrl ?? ""),
          degree: String(member.degree ?? ""),
          headline: String(member.headline ?? ""),
        }))
      : [];
    jobs.push({
      id,
      title: String(raw.title ?? ""),
      company: String(raw.company ?? ""),
      location: String(raw.location ?? ""),
      postingUrl: String(raw.postingUrl ?? `https://www.linkedin.com/jobs/view/${id}/`),
      hiringTeam,
      hasHiringTeam: raw.hasHiringTeam === true || hiringTeam.length > 0,
    });
  }
  const pagesCollected = Number.isSafeInteger(data.pagesCollected) ? Number(data.pagesCollected) : 0;
  const cardsTotal = Number.isSafeInteger(data.cardsTotal) ? Number(data.cardsTotal) : 0;
  return { jobs, pagesCollected, cardsTotal };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
