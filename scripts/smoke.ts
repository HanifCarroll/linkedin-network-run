#!/usr/bin/env bun

import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { type CliIo, run } from "../src/cli.ts";
import { jobsSend } from "../src/commands/jobs.ts";
import type { CliOperations } from "../src/commands/types.ts";
import { CliError } from "../src/core/errors.ts";
import { openDatabase } from "../src/db/database.ts";
import type { JobRow } from "../src/jobs/index.ts";
import { JobsEngine, normalizeProfileUrl } from "../src/jobs/index.ts";
import {
  bucketFor,
  draftActionFor,
  groupJobs,
  groupOutreachKind,
  outreachKindFor,
  primaryRoleFor,
} from "../src/view/grouping.ts";

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

  // Bounded temp-state check: migration 8 lands and a classify round-trips
  // through the engine into the row mapping (no browser, no live DB).
  {
    const opened = openDatabase(join(root, "classify.db"));
    try {
      if (!opened.migrations.applied.includes("jobs_classification_summaries")) {
        throw new Error("classification summaries migration (8) not applied");
      }
      const engine = new JobsEngine(opened.database);
      engine.storeCapturedJobs([{ id: "111", title: "Platform Engineer" }], "2026-08-03T00:00:00Z");
      const job = engine.classifyJob(
        "111",
        "  Infrastructure  ",
        " Salesforce ",
        "  Build the core platform  ",
        " Salesforce CPQ ",
        "2026-08-03T00:00:00Z",
      );
      if (
        job.workFocus !== "Infrastructure" ||
        job.productSystem !== "Salesforce" ||
        job.workSummary !== "Build the core platform" ||
        job.productSummary !== "Salesforce CPQ"
      ) {
        throw new Error("classification roundtrip smoke failed");
      }
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: migration 9 lands; drafts round-trip with blank
  // lines; approval is guarded (non-draft, no profile, duplicate, sent); and
  // recipient uniqueness holds across normalization and sent jobs (no browser).
  {
    const opened = openDatabase(join(root, "review.db"));
    try {
      if (!opened.migrations.applied.includes("jobs_review")) {
        throw new Error("review migration (9) not applied");
      }
      const engine = new JobsEngine(opened.database);
      const member = {
        name: "Ana",
        profileUrl: "https://www.linkedin.com/in/ana",
        degree: "2nd",
        headline: "Hiring manager",
      };
      const memberVariant = {
        name: "Ana",
        profileUrl: "https://www.LINKEDIN.com/in/ANA/?miniProfileUrn=xyz#frag",
        degree: "2nd",
        headline: "Hiring manager",
      };
      const ben = {
        name: "Ben",
        profileUrl: "https://www.linkedin.com/in/ben",
        degree: "2nd",
        headline: "Hiring manager",
      };
      const job = (
        id: string,
        title: string,
        company: string,
        team: readonly { name: string; profileUrl: string; degree: string; headline: string }[],
      ) => ({
        id,
        title,
        company,
        location: "US",
        postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
        hiringTeam: team,
        hasHiringTeam: team.length > 0,
      });
      engine.upsertJobs(
        [
          job("a1", "Platform Engineer", "Acme", [member]),
          job("b2", "Staff Engineer", "Beta", [memberVariant]),
          job("c3", "Backend Engineer", "Gamma", [ben]),
          job("d4", "No Team", "Delta", []),
          job("e5", "Infra Engineer", "Epsilon", [member]),
        ],
        "2026-08-03T00:00:00Z",
      );

      // Migration 9 CHECK is enforced by SQLite (not only by JobsEngine).
      let checkEnforced = false;
      try {
        opened.database.exec(`UPDATE jobs SET review = 'bogus' WHERE id = 'a1'`);
      } catch {
        checkEnforced = true;
      }
      if (!checkEnforced) throw new Error("review CHECK constraint smoke failed");

      if (
        normalizeProfileUrl(memberVariant.profileUrl) !== normalizeProfileUrl(member.profileUrl)
      ) {
        throw new Error("profile URL normalization smoke failed");
      }

      const body =
        "Hi Ana.\n\nIf we work together one outcome is a clean handoff.\n\nRelevant proof sentence.\n\nWorth a quick conversation?";
      const draft = engine.storeDraft("a1", "Contract work", body, "2026-08-03T00:00:01Z");
      if (draft.subject !== "Contract work")
        throw new Error("draft subject roundtrip smoke failed");
      if (draft.message !== body) throw new Error("draft blank-line roundtrip smoke failed");
      if (draft.review !== "needs_review" || draft.status !== "drafted") {
        throw new Error("new draft should be needs_review + drafted");
      }

      // Approval guard: a collected (non-drafted) job cannot be approved.
      let notDrafted = false;
      try {
        engine.setReview("c3", "approved", "2026-08-03T00:00:02Z");
      } catch (error) {
        notDrafted = error instanceof CliError && error.code === "JOBS_NOT_DRAFTED";
      }
      if (!notDrafted) throw new Error("approve non-draft guard smoke failed");

      // Approval guard: a drafted job without a usable profile URL cannot be approved.
      engine.storeDraft("d4", "", body, "2026-08-03T00:00:03Z");
      let noProfile = false;
      try {
        engine.setReview("d4", "approved", "2026-08-03T00:00:04Z");
      } catch (error) {
        noProfile = error instanceof CliError && error.code === "JOBS_NO_HIRING_TEAM";
      }
      if (!noProfile) throw new Error("approve no-profile guard smoke failed");

      const approved = engine.setReview("a1", "approved", "2026-08-03T00:00:05Z");
      if (approved.review !== "approved") throw new Error("approve smoke failed");

      const drafts = engine.approvedDrafts();
      if (drafts.length !== 1 || drafts[0]?.id !== "a1") {
        throw new Error("approvedDrafts selection smoke failed");
      }

      // Duplicate guard normalizes the URL: the variant profile conflicts with a1.
      engine.storeDraft("b2", "", body, "2026-08-03T00:00:06Z");
      let conflict = false;
      try {
        engine.setReview("b2", "approved", "2026-08-03T00:00:07Z");
      } catch (error) {
        conflict = error instanceof CliError && error.code === "DUPLICATE_APPROVED_PROFILE";
      }
      if (!conflict) throw new Error("duplicate approved profile smoke failed");

      // Cross-run guard: after a1 is sent, a fresh draft for the same recipient
      // conflicts with the sent job, and a sent job cannot return to review.
      engine.markSent("a1", "2026-08-03T00:00:08Z");

      // Re-upserting a sent row must not reset it to collected (the CASE keeps
      // favorite/drafted/sent; only captured is promoted).
      engine.upsertJobs([job("a1", "Platform Engineer", "Acme", [member])], "2026-08-03T00:00:09Z");
      if (engine.requireJob("a1").status !== "sent") {
        throw new Error("upsert preserves sent status smoke failed");
      }

      engine.storeDraft("e5", "", body, "2026-08-03T00:00:09Z");
      let sentConflict = false;
      try {
        engine.setReview("e5", "approved", "2026-08-03T00:00:10Z");
      } catch (error) {
        sentConflict = error instanceof CliError && error.code === "DUPLICATE_APPROVED_PROFILE";
      }
      if (!sentConflict) throw new Error("approve-vs-sent recipient conflict smoke failed");

      let sentReturn = false;
      try {
        engine.setReview("a1", "needs_review", "2026-08-03T00:00:11Z");
      } catch (error) {
        sentReturn = error instanceof CliError && error.code === "JOBS_ALREADY_SENT";
      }
      if (!sentReturn) throw new Error("sent return-to-review guard smoke failed");

      let sentRedraft = false;
      try {
        engine.storeDraft("a1", "", body, "2026-08-03T00:00:12Z");
      } catch (error) {
        sentRedraft = error instanceof CliError && error.code === "JOBS_ALREADY_SENT";
      }
      if (!sentRedraft) throw new Error("sent redraft guard smoke failed");
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: approval replacement is one transaction — the
  // exact conflicting id demotes the old approval and approves the current
  // draft, while stale/wrong targets and sent conflicts stay blocked.
  {
    const opened = openDatabase(join(root, "replace.db"));
    try {
      if (!opened.migrations.applied.includes("jobs_review")) {
        throw new Error("replace review migration (9) not applied");
      }
      const engine = new JobsEngine(opened.database);
      const alice = {
        name: "Alice",
        profileUrl: "https://www.linkedin.com/in/alice",
        degree: "2nd",
        headline: "HM",
      };
      const carol = {
        name: "Carol",
        profileUrl: "https://www.linkedin.com/in/carol",
        degree: "2nd",
        headline: "HM",
      };
      const job = (
        id: string,
        title: string,
        team: readonly { name: string; profileUrl: string; degree: string; headline: string }[],
      ) => ({
        id,
        title,
        company: "Co",
        location: "US",
        postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
        hiringTeam: team,
        hasHiringTeam: team.length > 0,
      });
      engine.upsertJobs(
        [
          job("f1", "Role One", [alice]),
          job("f2", "Role Two", [alice]),
          job("f3", "Role Three", [carol]),
        ],
        "2026-08-03T00:00:00Z",
      );
      const body = "Hi.\n\nBody.";
      engine.storeDraft("f1", "", body, "t1");
      engine.storeDraft("f2", "", body, "t1");
      engine.storeDraft("f3", "", body, "t1");
      engine.setReview("f1", "approved", "t1");

      // Normal duplicate rejection carries structured details.
      let duplicate = false;
      let duplicateDetails: Record<string, unknown> | undefined;
      try {
        engine.setReview("f2", "approved", "t1");
      } catch (error) {
        duplicate = error instanceof CliError && error.code === "DUPLICATE_APPROVED_PROFILE";
        duplicateDetails = error instanceof CliError ? error.details : undefined;
      }
      if (
        !duplicate ||
        duplicateDetails?.jobId !== "f1" ||
        duplicateDetails?.status !== "drafted"
      ) {
        throw new Error("replace: normal duplicate rejection smoke failed");
      }

      // Atomic replacement: f2 replaces f1's approval in one transaction.
      const replaced = engine.setReview("f2", "approved", "t1", "f1");
      if (replaced.review !== "approved") throw new Error("replace: approval smoke failed");
      if (
        engine.requireJob("f1").review !== "needs_review" ||
        engine.requireJob("f1").status !== "drafted"
      ) {
        throw new Error("replace: old approval not demoted");
      }
      if (engine.approvedDrafts().length !== 1 || engine.approvedDrafts()[0]?.id !== "f2") {
        throw new Error("replace: approvals not exclusive");
      }

      // Stale/wrong replacement: a target that is not the current conflict is rejected.
      engine.storeDraft("f1", "", body, "t1");
      let stale = false;
      try {
        engine.setReview("f1", "approved", "t1", "f3");
      } catch (error) {
        stale = error instanceof CliError && error.code === "DUPLICATE_REPLACE_STALE";
      }
      if (!stale) throw new Error("replace: stale/wrong replacement smoke failed");
      if (engine.requireJob("f1").review !== "needs_review") {
        throw new Error("replace: stale changed current draft");
      }
      if (engine.requireJob("f3").review !== "needs_review") {
        throw new Error("replace: stale unapproved an arbitrary job");
      }

      // Sent conflict stays blocked: a replaceId never replaces a sent record.
      engine.markSent("f2", "t1");
      let sentBlocked = false;
      try {
        engine.setReview("f1", "approved", "t1", "f2");
      } catch (error) {
        sentBlocked =
          error instanceof CliError &&
          error.code === "DUPLICATE_APPROVED_PROFILE" &&
          error.details?.status === "sent";
      }
      if (!sentBlocked) throw new Error("replace: sent conflict not blocked");
      if (
        engine.requireJob("f2").status !== "sent" ||
        engine.requireJob("f1").review !== "needs_review"
      ) {
        throw new Error("replace: sent conflict mutated state");
      }
    } finally {
      opened.database.close();
    }
  }

  // Bounded temp-state check: the recipient-group review operation atomically
  // skips or returns every non-sent role that shares the anchor's normalized
  // profile, never mutates a sent role, and leaves unrelated recipients alone.
  {
    const opened = openDatabase(join(root, "group.db"));
    try {
      const engine = new JobsEngine(opened.database);
      const alice = {
        name: "Alice",
        profileUrl: "https://www.linkedin.com/in/alice",
        degree: "2nd",
        headline: "HM",
      };
      const bob = {
        name: "Bob",
        profileUrl: "https://www.linkedin.com/in/bob",
        degree: "2nd",
        headline: "HM",
      };
      const job = (id: string, title: string, team: readonly (typeof alice)[]) => ({
        id,
        title,
        company: "Co",
        location: "US",
        postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
        hiringTeam: team,
        hasHiringTeam: team.length > 0,
      });
      engine.upsertJobs(
        [
          job("h1", "Role One", [alice]),
          job("h2", "Role Two", [alice]),
          job("h3", "Role Three", [alice]),
          job("h4", "Role Four", [bob]),
        ],
        "t0",
      );
      const body = "Hi.\n\nBody.";
      engine.storeDraft("h1", "", body, "t1");
      engine.storeDraft("h2", "", body, "t1");
      engine.storeDraft("h3", "", body, "t1");
      engine.storeDraft("h4", "", body, "t1");

      // Contact-level approval uniqueness: only one approved role per profile.
      engine.setReview("h1", "approved", "t2");
      let dup = false;
      try {
        engine.setReview("h3", "approved", "t3");
      } catch (error) {
        dup = error instanceof CliError && error.code === "DUPLICATE_APPROVED_PROFILE";
      }
      if (!dup) throw new Error("group: contact-level approval uniqueness smoke failed");
      if (engine.approvedDrafts().length !== 1 || engine.approvedDrafts()[0]?.id !== "h1") {
        throw new Error("group: approvals not exclusive across a recipient");
      }

      // Group skip: every non-sent sibling for the same profile is skipped;
      // the unrelated recipient is untouched.
      engine.setGroupReview("h3", "skipped", "t4");
      for (const id of ["h1", "h2", "h3"]) {
        if (engine.requireJob(id).review !== "skipped") {
          throw new Error(`group skip: ${id} not skipped`);
        }
      }
      if (engine.requireJob("h4").review !== "needs_review") {
        throw new Error("group skip: unrelated recipient mutated");
      }

      // Group return: every non-sent sibling returns to needs_review.
      engine.setGroupReview("h2", "needs_review", "t5");
      for (const id of ["h1", "h2", "h3"]) {
        if (engine.requireJob(id).review !== "needs_review") {
          throw new Error(`group return: ${id} not returned`);
        }
      }

      // Sent immutability: when any grouped role is sent, the whole group
      // rejects skip/return before mutation and unsent siblings stay put.
      engine.setReview("h2", "approved", "t6");
      engine.markSent("h2", "t7");
      let skipRejected = false;
      try {
        engine.setGroupReview("h1", "skipped", "t8");
      } catch (error) {
        skipRejected = error instanceof CliError && error.code === "JOBS_ALREADY_SENT";
      }
      if (!skipRejected) throw new Error("group skip on sent group not rejected");
      if (engine.requireJob("h1").review !== "needs_review") {
        throw new Error("group skip on sent group mutated unsent sibling h1");
      }
      if (engine.requireJob("h3").review !== "needs_review") {
        throw new Error("group skip on sent group mutated unsent sibling h3");
      }
      if (
        engine.requireJob("h2").status !== "sent" ||
        engine.requireJob("h2").review !== "approved"
      ) {
        throw new Error("group skip on sent group mutated the sent role");
      }

      let returnRejected = false;
      try {
        engine.setGroupReview("h1", "needs_review", "t9");
      } catch (error) {
        returnRejected = error instanceof CliError && error.code === "JOBS_ALREADY_SENT";
      }
      if (!returnRejected) throw new Error("group return on sent group not rejected");
      if (engine.requireJob("h1").review !== "needs_review") {
        throw new Error("group return on sent group mutated unsent sibling h1");
      }
      if (engine.requireJob("h3").review !== "needs_review") {
        throw new Error("group return on sent group mutated unsent sibling h3");
      }
      if (
        engine.requireJob("h2").status !== "sent" ||
        engine.requireJob("h2").review !== "approved"
      ) {
        throw new Error("group return on sent group mutated the sent role");
      }
    } finally {
      opened.database.close();
    }
  }

  // Bounded pure-function check: the viewer grouping logic folds several roles
  // for one profile into one group (including a variant URL), gives a job with
  // no usable recipient its own fallback group, and picks bucket/primary per
  // the contact-centric precedence rules.
  {
    const alice = {
      name: "Alice",
      profileUrl: "https://www.linkedin.com/in/alice",
      degree: "2nd",
      headline: "HM",
    };
    const variant = {
      name: "Alice",
      profileUrl: "https://www.LINKEDIN.com/in/alice/?miniProfileUrn=xyz#frag",
      degree: "2nd",
      headline: "HM",
    };
    const row = (id: string, overrides: Partial<JobRow>): JobRow => ({
      id,
      title: "",
      company: "",
      location: "",
      postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
      hiringTeam: [],
      hasHiringTeam: false,
      status: "collected",
      message: null,
      collectedAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
      sentAt: null,
      description: "",
      workplaceType: "",
      employmentType: "",
      applyMethod: "",
      promoted: false,
      activelyReviewing: false,
      postedAt: "",
      applicantCount: "",
      benefits: [],
      workFocus: "",
      productSystem: "",
      workSummary: "",
      productSummary: "",
      subject: "",
      review: "needs_review",
      ...overrides,
    });
    const role = (id: string, review: JobRow["review"], updatedAt: string, team = alice) =>
      row(id, {
        title: `Role ${id}`,
        company: "Co",
        status: "drafted",
        review,
        updatedAt,
        hiringTeam: [team],
        hasHiringTeam: true,
      });
    const r1 = role("g1", "needs_review", "2026-08-03T00:00:01Z");
    const r2 = role("g2", "approved", "2026-08-03T00:00:02Z");
    const r3 = role("g3", "needs_review", "2026-08-03T00:00:03Z");
    const r4 = role("g4", "needs_review", "2026-08-03T00:00:04Z", variant);
    const noTeam = row("g5", { title: "No Recipient", status: "collected" });

    const groups = groupJobs([r1, r2, r3, r4, noTeam]);
    if (groups.length !== 2) throw new Error("grouping: expected 2 groups");
    const aliceGroup = groups.find((g) => g.jobs.length === 4);
    const fallback = groups.find((g) => g.jobs.length === 1 && g.jobs[0]?.id === "g5");
    if (aliceGroup === undefined || fallback === undefined) {
      throw new Error("grouping: expected alice group and fallback group");
    }
    if (bucketFor(aliceGroup.jobs) !== "approved") {
      throw new Error("grouping: approved bucket precedence");
    }
    if (primaryRoleFor(aliceGroup.jobs, undefined).id !== "g2") {
      throw new Error("grouping: primary defaults to approved role");
    }
    if (primaryRoleFor(aliceGroup.jobs, "g3").id !== "g3") {
      throw new Error("grouping: in-memory selection overrides approved");
    }
    const allSkipped = aliceGroup.jobs.map((j) => ({ ...j, review: "skipped" as const }));
    if (bucketFor(allSkipped) !== "skipped") {
      throw new Error("grouping: skipped only when every role skipped");
    }
    const withSent = aliceGroup.jobs.map((j) =>
      j.id === "g1" ? { ...j, status: "sent" as const } : j,
    );
    if (bucketFor(withSent) !== "sent") throw new Error("grouping: sent bucket precedence");
    if (primaryRoleFor(withSent, undefined).id !== "g1") {
      throw new Error("grouping: primary sent precedence over selection");
    }

    // Replacement UI: in an approved group, selecting a needs-review sibling is
    // editable and replacement-capable, while the approved owner stays read-only.
    const ownerAction = draftActionFor(aliceGroup.jobs, "g2");
    if (ownerAction.editable || ownerAction.replace || !ownerAction.canReturn) {
      throw new Error("grouping: approved owner should be read-only and returnable");
    }
    const siblingAction = draftActionFor(aliceGroup.jobs, "g3");
    if (!siblingAction.editable || !siblingAction.replace) {
      throw new Error(
        "grouping: needs-review sibling in approved group must be editable + replace",
      );
    }
    const plainAction = draftActionFor(
      aliceGroup.jobs.filter((j) => j.id !== "g2"),
      "g3",
    );
    if (!plainAction.editable || plainAction.replace) {
      throw new Error("grouping: needs-review group with no owner should approve, not replace");
    }
    const sentAction = draftActionFor(withSent, "g3");
    if (sentAction.editable || sentAction.canReturn) {
      throw new Error("grouping: sent group must be fully read-only");
    }
  }

  // Bounded pure-function check: section classification splits direct
  // outreach from application follow-up across the explicit contract
  // employment types, the two mislabeled full-time engagements, and
  // contract-mention false positives.
  {
    const kind = (employmentType: string, title: string, description: string) =>
      outreachKindFor({ employmentType, title, description });

    const contractIds = [
      "4451934421",
      "4452720575",
      "4451696307",
      "4452709098",
      "4454035786",
      "4447404037",
      "4452986849",
      "4451904629",
      "4443987736",
    ];
    for (const id of contractIds) {
      if (kind("Contract", `Role ${id}`, "") !== "application_followup") {
        throw new Error(`classification: contract role ${id} not application-follow-up`);
      }
    }
    if (kind("Full-time", "6 Month Contract to Hire", "") !== "application_followup") {
      throw new Error("classification: contract-to-hire not application-follow-up");
    }
    if (
      kind("Full-time", "", "Open to W2 /Individual C2C or 1099 candidates only") !==
      "application_followup"
    ) {
      throw new Error("classification: positive C2C/1099 engagement not application-follow-up");
    }
    const falsePositives = [
      "This is not a contract role.",
      "We advertise on contractor portals.",
      "test contract coverage",
      "customer contract values",
      // C2C/1099 with clear negation must stay direct.
      "This is not a C2C or contract role.",
      "Only W2, no C2C.",
      "This role is not open to C2C.",
    ];
    for (const phrase of falsePositives) {
      if (kind("Full-time", "", phrase) !== "direct") {
        throw new Error(`classification: false positive ${JSON.stringify(phrase)}`);
      }
    }
    if (kind("Full-time", "Platform Engineer", "W2 full-time employee") !== "direct") {
      throw new Error("classification: bare W2 must stay direct");
    }
    if (
      kind("Full-time", "", "We accept C2C and 1099 on this engagement") !== "application_followup"
    ) {
      throw new Error("classification: accepts C2C must be application-follow-up");
    }
  }

  // Bounded pure-function check: a mixed recipient group stays one queue item
  // in the application-follow-up section and defaults its primary selection to
  // the contract role unless sent/approved/override owns the decision.
  {
    const member = {
      name: "Sam",
      profileUrl: "https://www.linkedin.com/in/sam",
      degree: "2nd",
      headline: "HM",
    };
    const row = (id: string, overrides: Partial<JobRow>): JobRow => ({
      id,
      title: "",
      company: "",
      location: "",
      postingUrl: `https://www.linkedin.com/jobs/view/${id}/`,
      hiringTeam: [],
      hasHiringTeam: false,
      status: "collected",
      message: null,
      collectedAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
      sentAt: null,
      description: "",
      workplaceType: "",
      employmentType: "",
      applyMethod: "",
      promoted: false,
      activelyReviewing: false,
      postedAt: "",
      applicantCount: "",
      benefits: [],
      workFocus: "",
      productSystem: "",
      workSummary: "",
      productSummary: "",
      subject: "",
      review: "needs_review",
      ...overrides,
    });
    const role = (
      id: string,
      review: JobRow["review"],
      updatedAt: string,
      employmentType: string,
    ) =>
      row(id, {
        title: `Role ${id}`,
        company: "Co",
        status: "drafted",
        review,
        updatedAt,
        hiringTeam: [member],
        hasHiringTeam: true,
        employmentType,
      });
    const contract = role("k1", "needs_review", "2026-08-03T00:00:01Z", "Contract");
    const direct = role("k2", "needs_review", "2026-08-03T00:00:02Z", "Full-time");

    const mixed = groupJobs([contract, direct]);
    if (mixed.length !== 1 || mixed[0]?.jobs.length !== 2) {
      throw new Error("classification: mixed group must stay one recipient group");
    }
    const group = mixed[0];
    if (group === undefined) throw new Error("classification: mixed group missing");
    if (groupOutreachKind(group.jobs) !== "application_followup") {
      throw new Error("classification: mixed group must be application-follow-up");
    }
    if (primaryRoleFor(group.jobs, undefined).id !== "k1") {
      throw new Error("classification: mixed group defaults primary to contract role");
    }
    if (primaryRoleFor(group.jobs, "k2").id !== "k2") {
      throw new Error("classification: explicit selection overrides contract default");
    }
    const approvedDirect = group.jobs.map((j) =>
      j.id === "k2" ? { ...j, review: "approved" as const } : j,
    );
    if (primaryRoleFor(approvedDirect, undefined).id !== "k2") {
      throw new Error("classification: approved direct role owns the decision");
    }
    const sentDirect = group.jobs.map((j) =>
      j.id === "k2" ? { ...j, status: "sent" as const } : j,
    );
    if (primaryRoleFor(sentDirect, undefined).id !== "k2") {
      throw new Error("classification: sent role owns the decision");
    }
    const directOnly = groupJobs([direct]);
    if (directOnly.length !== 1 || groupOutreachKind(directOnly[0]?.jobs ?? []) !== "direct") {
      throw new Error("classification: direct-only group must be direct");
    }
    const contractOnly = groupJobs([contract]);
    if (
      contractOnly.length !== 1 ||
      groupOutreachKind(contractOnly[0]?.jobs ?? []) !== "application_followup"
    ) {
      throw new Error("classification: contract-only group must be application-follow-up");
    }
  }

  // Bounded check: jobs send with no approved targets fails before resolving a
  // browser session (no live browser).
  {
    let sessionResolved = false;
    try {
      await jobsSend(
        {
          stateDir: join(root, "send"),
          playwriterBin: fakePlaywriter,
          sessionId: "auto",
          allowSend: true,
        },
        {
          resolveSession: async () => {
            sessionResolved = true;
            return 1;
          },
          runScript: async () => {
            throw new Error("jobs send should not reach the browser");
          },
          now: () => "2026-08-03T12:00:00Z",
        },
      );
      throw new Error("jobs send should have thrown for no approved drafts");
    } catch (error) {
      if (!(error instanceof CliError) || error.code !== "JOBS_NOTHING_TO_SEND") throw error;
    }
    if (sessionResolved) throw new Error("jobs send resolved a session before target validation");
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
    jobsDetail: async () => {
      calls.push("jobs detail");
      return { detailed: 0, remaining: 0 };
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
    jobsRemove: async () => {
      calls.push("jobs remove");
      return { removed: 0 };
    },
    jobsDraft: async (input) => {
      if (input.subject !== "Hi" || input.message !== "Body") {
        throw new Error("jobs draft did not parse --subject");
      }
      calls.push("jobs draft");
      return { job: { id: input.id } };
    },
    jobsSend: async (input) => {
      if (input.sessionId !== "auto") throw new Error("jobs send did not parse auto");
      calls.push("jobs send");
      return { sent: 0, skipped: 0, results: [] };
    },
    jobsClassify: async () => {
      calls.push("jobs classify");
      return { job: { id: "111" } };
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
    [
      "--json",
      "jobs",
      "classify",
      "--id",
      "111",
      "--work-focus",
      "Infrastructure",
      "--product-system",
      "Salesforce",
      "--work-summary",
      "Build the core platform",
      "--product-summary",
      "Salesforce CPQ",
    ],
    ["--json", "jobs", "draft", "--id", "111", "--subject", "Hi", "--message", "Body"],
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
  const jobsSendDenied = await invoke(["--json", "jobs", "send"], common);
  if (
    jobsSendDenied.exitCode !== 3 ||
    jobsSendDenied.value?.error?.code !== "SEND_NOT_AUTHORIZED"
  ) {
    throw new Error("jobs send authorization smoke failed");
  }
  const classifyRejected = await invoke(
    [
      "--json",
      "jobs",
      "classify",
      "--id",
      "111",
      "--work-focus",
      "   ",
      "--product-system",
      "Salesforce",
      "--work-summary",
      "Build",
      "--product-summary",
      "Salesforce CPQ",
    ],
    common,
  );
  if (
    classifyRejected.exitCode !== 2 ||
    classifyRejected.value?.error?.code !== "INVALID_ARGUMENT"
  ) {
    throw new Error("classification validation smoke failed");
  }
  const summaryRejected = await invoke(
    [
      "--json",
      "jobs",
      "classify",
      "--id",
      "111",
      "--work-focus",
      "Infrastructure",
      "--product-system",
      "Salesforce",
      "--work-summary",
      "   ",
      "--product-summary",
      "Salesforce CPQ",
    ],
    common,
  );
  if (summaryRejected.exitCode !== 2 || summaryRejected.value?.error?.code !== "INVALID_ARGUMENT") {
    throw new Error("classification summary validation smoke failed");
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
