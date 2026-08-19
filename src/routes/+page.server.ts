import { join } from "node:path";
import { error } from "@sveltejs/kit";
import { openDatabase } from "../db/database.ts";
import { JobsEngine } from "../jobs/engine.ts";
import { evidenceGaps } from "../jobs/filter.ts";
import { viewerStateDir } from "../view/state.ts";

export async function load() {
  const dbPath = join(viewerStateDir(), "linkedin-tools.db");
  if (!(await Bun.file(dbPath).exists())) error(500, `jobs viewer: no database at ${dbPath}`);
  const opened = openDatabase(dbPath);
  try {
    opened.database.exec("PRAGMA query_only = ON;");
    const jobs = new JobsEngine(opened.database)
      .listJobs({ withHiringTeam: true, fit: "kept" })
      .filter(
        (job) =>
          job.triageBucket !== "pending" || job.review !== "needs_review" || job.status === "sent",
      )
      .map((job) => ({ ...job, evidenceGaps: evidenceGaps(job) }));
    return { jobs };
  } finally {
    opened.database.close();
  }
}
