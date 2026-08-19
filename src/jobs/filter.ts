import type { Database } from "bun:sqlite";
import { CliError } from "../core/errors.ts";

const DAY = 86_400_000;

export function normalizeFilterText(value: string): string {
  return value
    .normalize("NFKC")
    .replace(/[“”‘’"']/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function freshness(value: string, now: number, maxAgeDays: number): "fresh" | "stale" | "unknown" {
  const text = normalizeFilterText(value);
  if (!text) return "unknown";
  const relative = /^(\d+)\+?\s+(day|week|month)s?\s+ago$/.exec(text);
  if (relative) {
    const days =
      Number(relative[1]) * (relative[2] === "week" ? 7 : relative[2] === "month" ? 30 : 1);
    return days <= maxAgeDays ? "fresh" : "stale";
  }
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return "unknown";
  return now - parsed <= maxAgeDays * DAY && parsed <= now + DAY ? "fresh" : "stale";
}

export function evidenceGaps(
  job: Pick<JobRowLike, "employmentType" | "postedAt" | "description" | "workSummary">,
): string[] {
  return [
    job.employmentType.trim() ? "" : "employment type",
    job.postedAt.trim() ? "" : "posting date",
    job.description.trim() ? "" : "description",
    job.workSummary.trim() ? "" : "work summary",
  ].filter(Boolean);
}
type JobRowLike = {
  employmentType: string;
  postedAt: string;
  description: string;
  workSummary: string;
};

export type FilterResult = {
  command: "jobs filter";
  runId: string;
  kept: number;
  dropped: number;
  unknownFreshness: number;
  reasons: Record<string, number>;
};

export function filterRun(
  database: Database,
  input: {
    runId: string;
    terms: readonly string[];
    policyVersion: string;
    maxAgeDays?: number;
    now: string;
  },
): FilterResult {
  if (input.terms.length === 0)
    throw new CliError("INVALID_ARGUMENT", "jobs filter requires at least one term");
  const terms = [...new Set(input.terms.map(normalizeFilterText).filter(Boolean))];
  if (terms.length === 0)
    throw new CliError("INVALID_ARGUMENT", "jobs filter requires at least one usable term");
  const policyVersion = input.policyVersion.trim();
  if (policyVersion === "")
    throw new CliError("INVALID_ARGUMENT", "jobs filter requires a policy version");
  const maxAgeDays = input.maxAgeDays ?? 30;
  const rows = database
    .query<
      { id: string; title: string; posted_at: string; review: string; status: string },
      [string]
    >(
      `SELECT DISTINCT j.id, j.title, j.posted_at, j.review, j.status FROM jobs j JOIN job_observations o ON o.job_id=j.id WHERE o.run_id=? ORDER BY j.id`,
    )
    .all(input.runId);
  const update = database.prepare(
    `UPDATE jobs SET fit=?, filter_reason=?, matched_term=?, filter_policy_version=?, filtered_at=?, updated_at=? WHERE id=?`,
  );
  const counts: Record<string, number> = {};
  let kept = 0,
    dropped = 0,
    unknownFreshness = 0;
  const tx = database.transaction(() => {
    for (const row of rows) {
      if (row.review === "approved" || row.review === "skipped" || row.status === "sent") continue;
      const title = normalizeFilterText(row.title);
      const matched = terms.find((term) => title.includes(term)) ?? "";
      const age = freshness(row.posted_at, Date.parse(input.now), maxAgeDays);
      const fit = matched && age !== "stale" ? "kept" : "dropped";
      const reason = !matched
        ? "title_no_match"
        : age === "stale"
          ? "stale"
          : age === "unknown"
            ? "freshness_unknown"
            : "title_match";
      update.run(fit, reason, matched, policyVersion, input.now, input.now, row.id);
      counts[reason] = (counts[reason] ?? 0) + 1;
      if (fit === "kept") kept++;
      else dropped++;
      if (age === "unknown") unknownFreshness++;
    }
  });
  tx();
  return {
    command: "jobs filter",
    runId: input.runId,
    kept,
    dropped,
    unknownFreshness,
    reasons: counts,
  };
}
