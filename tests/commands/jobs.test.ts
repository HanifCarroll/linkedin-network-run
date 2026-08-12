import { describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseInvocation } from "../../src/commands/arguments.ts";
import { jobsList, jobsSearch, jobsSend } from "../../src/commands/jobs.ts";
import { createDefaultOperations } from "../../src/commands/operations.ts";
import { CliError } from "../../src/core/errors.ts";
import { openDatabase } from "../../src/db/database.ts";
import { JobsEngine } from "../../src/jobs/engine.ts";
import type { CollectedJob } from "../../src/jobs/types.ts";

const NOW = "2026-08-11T12:00:00Z";

function sampleJobs(): readonly CollectedJob[] {
  return [
    {
      id: "4450256825",
      title: "Senior Full-Stack Product Engineer",
      company: "Search BizAthletes",
      location: "United States",
      postingUrl: "https://www.linkedin.com/jobs/view/4450256825/",
      hiringTeam: [
        {
          name: "Greg Letow",
          profileUrl: "https://www.linkedin.com/in/gregbizathlete",
          degree: "2nd",
          headline: "Championship talent. Business dynasties.",
        },
      ],
      hasHiringTeam: true,
    },
    {
      id: "4450337999",
      title: "Founding Product Engineer",
      company: "Some Co",
      location: "United States",
      postingUrl: "https://www.linkedin.com/jobs/view/4450337999/",
      hiringTeam: [],
      hasHiringTeam: false,
    },
  ];
}

function tempStateDir(): string {
  return mkdtempSync(join(tmpdir(), "linkedin-jobs-"));
}

describe("jobs engine", () => {
  test("upserts jobs, filters by hiring team, and walks the status flow", async () => {
    const dir = tempStateDir();
    try {
      const opened = openDatabase(join(dir, "linkedin-tools.db"));
      const engine = new JobsEngine(opened.database);
      engine.upsertJobs(sampleJobs(), NOW);

      const all = engine.listJobs({ withHiringTeam: false });
      expect(all).toHaveLength(2);
      const withTeam = engine.listJobs({ withHiringTeam: true });
      expect(withTeam).toHaveLength(1);
      expect(withTeam[0]?.hiringTeam[0]?.name).toBe("Greg Letow");

      // Re-upsert refreshes enrichment but keeps status.
      engine.favoriteJobs(["4450256825"], NOW);
      const first = sampleJobs()[0];
      if (first === undefined) throw new Error("sample job missing");
      const refreshed: CollectedJob = {
        ...first,
        company: "BizAthletes (updated)",
      };
      engine.upsertJobs([refreshed], NOW);
      const favorite = engine.requireJob("4450256825");
      expect(favorite.status).toBe("favorite");
      expect(favorite.company).toBe("BizAthletes (updated)");

      engine.storeDraft("4450256825", "Hi Greg, I build product engineering teams.", NOW);
      expect(engine.draftedJobs()).toHaveLength(1);
      expect(engine.requireJob("4450256825").message).toContain("Hi Greg");

      engine.markSent("4450256825", NOW);
      const sent = engine.requireJob("4450256825");
      expect(sent.status).toBe("sent");
      expect(sent.sentAt).toBe(NOW);
      expect(engine.draftedJobs()).toHaveLength(0);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test("rejects favorite on unknown id", async () => {
    const dir = tempStateDir();
    try {
      const opened = openDatabase(join(dir, "linkedin-tools.db"));
      const engine = new JobsEngine(opened.database);
      expect(() => engine.requireJob("nope")).toThrow(CliError);
      expect(() => engine.favoriteJobs(["nope"], NOW)).toThrow(CliError);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("jobs commands", () => {
  test("search runs the playwriter script and persists the parsed result", async () => {
    const dir = tempStateDir();
    try {
      const ran: string[] = [];
      const result = await jobsSearch(
        {
          stateDir: dir,
          playwriterBin: "/fake/playwriter",
          sessionId: 7,
          keywords: "product engineer",
          location: "United States",
          postedWithinDays: 7,
          remote: true,
          pages: 1,
          hiringTeamLimit: 10,
        },
        {
          resolveSession: async () => 7,
          runScript: async (options) => {
            ran.push(options.script);
            return {
              ok: true,
              data: {
                jobs: sampleJobs(),
                pagesCollected: 1,
                cardsTotal: 1445,
              },
            };
          },
          now: () => NOW,
        },
      );
      expect(ran).toHaveLength(1);
      expect(ran[0]).toContain("voyagerJobsDashJobCards");
      expect(result).toMatchObject({
        command: "jobs search",
        cardsTotal: 1445,
        pagesCollected: 1,
        collected: 2,
        withHiringTeam: 1,
      });

      const listed = await jobsList({ stateDir: dir, withHiringTeam: true });
      expect(listed).toMatchObject({ count: 1 });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test("send requires a drafted message and only marks sent statuses as sent", async () => {
    const dir = tempStateDir();
    try {
      const opened = openDatabase(join(dir, "linkedin-tools.db"));
      const engine = new JobsEngine(opened.database);
      engine.upsertJobs(sampleJobs(), NOW);
      engine.storeDraft("4450256825", "Hi Greg, let's talk.", NOW);
      opened.database.close();

      const ran: string[] = [];
      const result = await jobsSend(
        {
          stateDir: dir,
          playwriterBin: "/fake/playwriter",
          sessionId: 7,
          allowSend: true,
        },
        {
          resolveSession: async () => 7,
          runScript: async (options) => {
            ran.push(options.script);
            const isFirst = ran.length === 1;
            return {
              ok: true,
              data: {
                jobId: "4450256825",
                memberName: "Greg Letow",
                status: isFirst ? "sent" : "no_message_button",
                detail: isFirst ? "message visible in thread" : "no Message CTA on profile",
                confirmed: isFirst,
              },
            };
          },
          now: () => NOW,
        },
      );
      expect(ran).toHaveLength(1);
      expect(result).toMatchObject({ sent: 1, skipped: 0 });
      const reloaded = openDatabase(join(dir, "linkedin-tools.db"));
      expect(new JobsEngine(reloaded.database).requireJob("4450256825").status).toBe("sent");
      reloaded.database.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test("send refuses jobs without a hiring team or without a draft", async () => {
    const dir = tempStateDir();
    try {
      const opened = openDatabase(join(dir, "linkedin-tools.db"));
      const engine = new JobsEngine(opened.database);
      engine.upsertJobs(sampleJobs(), NOW);
      opened.database.close();
      await expect(
        jobsSend(
          {
            stateDir: dir,
            playwriterBin: "/fake",
            sessionId: 7,
            allowSend: true,
            id: "4450337999",
          },
          { resolveSession: async () => 7 },
        ),
      ).rejects.toThrow(/hiring team/);
      await expect(
        jobsSend(
          {
            stateDir: dir,
            playwriterBin: "/fake",
            sessionId: 7,
            allowSend: true,
            id: "4450256825",
          },
          { resolveSession: async () => 7 },
        ),
      ).rejects.toThrow(/no drafted message/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("jobs argument parsing", () => {
  const context = { now: new Date(), env: { HOME: "/tmp" } };

  test("jobs search requires keywords and validates posted-within", () => {
    expect(() => parseInvocation(["jobs", "search"], context)).toThrow(/--keywords/);
    expect(() =>
      parseInvocation(["jobs", "search", "--keywords", "x", "--posted-within", "5"], context),
    ).toThrow(/--posted-within/);
    const parsed = parseInvocation(
      [
        "jobs",
        "search",
        "--keywords",
        "product engineer",
        "--location",
        "United States",
        "--session",
        "9",
      ],
      context,
    );
    expect(parsed).toMatchObject({ kind: "command", command: "jobs search" });
  });

  test("jobs send gates on --allow-send", () => {
    expect(() => parseInvocation(["jobs", "send"], context)).toThrow(/--allow-send/);
  });

  test("jobs favorite requires ids", () => {
    expect(() => parseInvocation(["jobs", "favorite"], context)).toThrow(/--id/);
  });

  test("every jobs command is routed through the CLI operations", async () => {
    const operations = createDefaultOperations();
    for (const name of ["jobsSearch", "jobsList", "jobsFavorite", "jobsDraft", "jobsSend"]) {
      expect(typeof (operations as unknown as Record<string, unknown>)[name]).toBe("function");
    }
  });
});
