import { isAbsolute, join, resolve } from "node:path";
import { CliError } from "../core/errors.ts";
import {
  CLASSIFICATION_MAX_LENGTH,
  DRAFT_MAX_LENGTH,
  SUBJECT_MAX_LENGTH,
  SUMMARY_MAX_LENGTH,
} from "../jobs/types.ts";
import type {
  AnalyticsExportInput,
  DoctorInput,
  JobsCaptureFinishInput,
  JobsCaptureIngestInput,
  JobsCaptureStartInput,
  JobsCheckInput,
  JobsClassifyInput,
  JobsDetailInput,
  JobsDraftInput,
  JobsEnrichInput,
  JobsFavoriteInput,
  JobsHubSpotNextInput,
  JobsHubSpotRecordInput,
  JobsListInput,
  JobsNormalizeInput,
  JobsRemoveInput,
  JobsSendInput,
  JobsTriageNextInput,
  JobsTriageRecordInput,
  MigrationDryRunInput,
  NetworkIncidentClearInput,
  NetworkIncidentStatusInput,
  NetworkOpenInput,
  NetworkReadInput,
  NetworkReconcileInput,
  NetworkRunEndInput,
  NetworkSessionResetInput,
  NetworkTickInput,
  ParsedInvocation,
} from "./types.ts";

const PLAYWRITER_DEFAULT = "/Users/hanifcarroll/.bun/bin/playwriter";
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export const HELP = `linkedin-tools — deterministic LinkedIn operations

Usage:
  linkedin-tools [--json] <command> [options]

Commands:
  doctor                 Check Bun, Playwriter, sessions, state paths, and SQLite
  network status         Read the current local-day network state
  network report         Read the deterministic current local-day report
  network tick           Finish today's guarded 30-request run; requires --allow-send
  network reconcile      Audit possible sends and finish only after complete reconciliation
  network run-end        End an active day's run so the next day can start cleanly
  network incident-status  Show the active LinkedIn browser incident, if any
  network open            Navigate the bound network session to sent or search
  network incident-clear Clear the active incident after dual human confirmation
  analytics export       Export and validate one exact seven-day analytics workbook
  migration dry-run      Build a read-only, proposal-only legacy migration report
  jobs capture-start     Start a durable Chrome Jobs capture run
  jobs capture-ingest    Ingest one captured Jobs XHR response into SQLite
  jobs capture-finish    Complete or fail a capture run with final checkpoint
  jobs normalize         Normalize captured pages into deduplicated jobs
  jobs filter            Filter one run with explicit title terms
  jobs enrich            Enrich captured postings into enriched rows (company/hiring team)
  jobs detail            Pull full posting-page detail (description + structured fields)
  jobs list              List collected jobs from the local store
  jobs check             Verify stored postings are still live and drop removed ones
  jobs favorite          Mark collected jobs for review
  jobs draft             Store a drafted subject + message for a job
  jobs send              Send approved drafted messages to hiring team members (--allow-send)
  jobs classify          Set work-focus and product-system phrases for a job
  jobs triage-next       Hand one eligible kept job to agent triage
  jobs triage-record     Store an agent triage result before human review
  jobs hubspot-next      Prepare or resume one approved prospect for HubSpot
  jobs hubspot-record    Record HubSpot object and association receipts locally

Browser boundary:
  LinkedIn Jobs CAPTURE ONLY uses the caller-owned Codex Chrome handoff helper; the CLI only
  ingests captured JSON. Capture commands require no Playwriter or browser session. Enrichment,
  live-job checks, networking, analytics, and sending stay on their existing paths.

Global options:
  --json                  Emit one stable JSON envelope to stdout
  -h, --help              Show help
  -v, --version           Show version
`;

const NETWORK_HELP = `Usage:
  linkedin-tools [--json] network status [--date YYYY-MM-DD] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] network report [--date YYYY-MM-DD] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] network tick --allow-send [--batch-size 1..5]
    [--max-real-sends 1..30]
    [--date YYYY-MM-DD] [--state-dir ABSOLUTE_PATH] [--session ID|auto]
    [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] network reconcile [--date YYYY-MM-DD]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] network run-end --date YYYY-MM-DD --reason "..."
    [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] network session-reset [--state-dir ABSOLUTE_PATH]
    [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] network open --page sent|search [--source SOURCE_ID]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] network incident-status [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] network incident-clear
    --account-access-confirmed --warning-cleared-confirmed --reason "..."
    [--state-dir ABSOLUTE_PATH]

One tick continues serially through audited microbatches until Done or a typed
checkpoint/terminal blocker. Audits cannot be disabled. The daily target is
fixed at 30 and total durable commit starts can never exceed 30. At a new local
day, older active runs without planned or possible sends are parked as missed;
older runs with either require an exact-date reconciliation first. An active
incident blocks all browser automation until cleared with dual confirmation.
`;

const ANALYTICS_HELP = `Usage:
  linkedin-tools [--json] analytics export --out ABSOLUTE_PATH.xlsx
    --account NAME --download-root ABSOLUTE_PATH --session ID|auto
    (--period previous-7-days | --start-date YYYY-MM-DD --end-date YYYY-MM-DD)
    [--receipt ABSOLUTE_PATH.json] [--recovery-state ABSOLUTE_PATH.json]
    [--poll-interval-ms 100..60000] [--max-polls 1..120]
    [--state-dir ABSOLUTE_PATH] [--playwriter-bin ABSOLUTE_PATH]

  --download-root may be repeated. Output and receipt paths may contain
  {startDate} and {endDate}; expansion occurs before path validation.
`;

const MIGRATION_HELP = `Usage:
  linkedin-tools [--json] migration dry-run --source-root ABSOLUTE_PATH

This command reads legacy state and returns proposals. It has no apply mode.
`;

const JOBS_HELP = `Usage:
  linkedin-tools [--json] jobs capture-start --run-id ID --source-url URL
    [--search-config JSON] [--checkpoint JSON] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs capture-ingest --run-id ID --page PAGE_ID
    --payload - --source-url URL --response-url URL [--cursor CURSOR]
    [--captured-at ISO] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs capture-finish --run-id ID --state complete|failed
    [--checkpoint JSON] [--error TEXT] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs normalize --run-id ID [--limit N]
    [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs filter --run-id ID --terms '["term"]' --policy-version ID
    [--max-age-days N]
    [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs enrich [--run-id ID] [--limit N]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs detail [--run-id ID] [--limit N]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs triage-next [--run-id ID] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs triage-record --id ID --bucket strong|possible|weak
    --company-summary TEXT --work-summary TEXT --responsibilities JSON_ARRAY
    --skill-matches JSON_ARRAY --skill-gaps JSON_ARRAY --reason TEXT
    --policy-version jobs-triage-v1-20260819 [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs list [--status captured|collected|favorite|drafted|sent]
    [--with-hiring-team] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs check [--status captured|collected|favorite|drafted|sent]
    [--with-hiring-team] [--limit N] [--state-dir ABSOLUTE_PATH]
    [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs favorite --id JOB_ID [--id JOB_ID ...] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs remove --id JOB_ID [--id JOB_ID ...] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs draft --id JOB_ID --message "..." [--subject "..."]
    [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs send --allow-send [--id JOB_ID]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs classify --id JOB_ID --work-focus "..." --product-system "..."
    --work-summary "..." --product-summary "..."
    [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs hubspot-next [--id JOB_ID]
    [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs hubspot-record --prospect-id ID
    [--company-id ID] [--contact-id ID] [--deal-id ID] [--task-id ID]
    [--associations-complete | --error TEXT] [--state-dir ABSOLUTE_PATH]

jobs capture-start records source/search metadata for a run. Use the
importable scripts/linkedin-jobs-chrome-helper.mjs from the Codex Chrome
runtime to capture a matching Jobs XHR, then pipe its raw body to
jobs capture-ingest --payload -. No per-page artifact is required. The CLI
owns SQLite durability; it never controls Chrome. Run jobs normalize after
capture to create deduplicated jobs rows. It processes one page transactionally,
records run/page provenance, and resumes by skipping completed pages.
Ingest is idempotent with conflict detection, rejects malformed/non-JSON or
non-Jobs payloads before write, and updates a resume cursor. Capture-finish is
retry-safe and records complete/failed state plus final checkpoint/error.
No location filter is added; search configuration is metadata only.
jobs check verifies stored postings are still live by loading each direct
view and reading only the title; removed postings are dropped from the store.
It is much cheaper than enrich (no hiring-team extraction) and reports
{checked, live, dead, unclear}.
jobs send opens the first listed hiring team member's profile,
composes the approved drafted message, and sends it — it requires the explicit
--allow-send flag. Only approved drafted jobs (review = approved) are selected;
if --id is given that single job must itself be an approved draft.
--subject fills the composer's subject field only when the composer exposes
one; normal no-subject DM composers are unaffected.
jobs draft stores a draft for review: --message is the body (interior blank
lines are preserved) and --subject is an optional subject line. Storing or
redrafting returns the job to needs-review. Draft once per recipient — when
several postings list the same person, draft the best-fitting role; sibling
roles are context and at most one approved/sent message per person holds. The
local review queue (bun run
view) is where drafts are reviewed and approved or skipped. The body is three
paragraphs separated by one blank line (\\n\\n):

  Hi [first name] — I saw the [role] opening at [company]. One plain, specific
  detail about the product, users, or problem that caught my attention.

  One relevant experience or proof statement, in ordinary language, connected
  directly to the detail in the first paragraph.

  Would you be open to contract help while you're hiring?

The proof must connect to the detail in the first paragraph, not stand alone.
The subject is normally "[Role] at [Company]" or "About the [Role] opening".
jobs classify stores the two brief review phrases for a posting: --work-focus
(the functional area the role centers on) and --product-system (the tool or
platform it is built around), plus two longer summaries: --work-summary (what
you'd do) and --product-summary (what you'd build). All four are required,
trimmed, and length-bounded (phrases 80 chars, summaries 320 chars).
jobs hubspot-next returns one deterministic, lookup-before-create packet for an
approved kept person. An agent performs the packet through the official HubSpot
connection, then records each returned ID with jobs hubspot-record. The local
receipt makes interruption and retry safe; neither command sends outreach.
`;

type OptionKind = "boolean" | "value" | "repeatable";
type OptionSpec = Readonly<Record<string, OptionKind>>;
type ParsedOptions = {
  readonly booleans: ReadonlySet<string>;
  readonly values: ReadonlyMap<string, string>;
  readonly repeated: ReadonlyMap<string, readonly string[]>;
};

export type ParseContext = {
  readonly now: Date;
  readonly env: Readonly<Record<string, string | undefined>>;
};

export function extractJsonMode(argv: readonly string[]): {
  readonly json: boolean;
  readonly argv: readonly string[];
} {
  const count = argv.filter((argument) => argument === "--json").length;
  if (count > 1) invalid("--json may be provided only once");
  return { json: count === 1, argv: argv.filter((argument) => argument !== "--json") };
}

export function parseInvocation(argv: readonly string[], context: ParseContext): ParsedInvocation {
  if (argv.length === 0 || isHelp(argv[0])) return { kind: "help", text: HELP };
  if (argv[0] === "--version" || argv[0] === "-v") {
    if (argv.length !== 1) invalid("--version cannot be combined with another argument");
    return { kind: "version" };
  }

  if (argv[0] === "doctor") {
    if (isHelp(argv[1])) return { kind: "help", text: doctorHelp() };
    const options = parseOptions(argv.slice(1), {
      "--state-dir": "value",
      "--playwriter-bin": "value",
      "--network-session": "value",
      "--analytics-session": "value",
    });
    return { kind: "command", command: "doctor", input: doctorInput(options, context) };
  }

  if (argv[0] === "network") {
    if (argv.length === 1 || isHelp(argv[1])) return { kind: "help", text: NETWORK_HELP };
    const verb = argv[1];
    if (
      ![
        "status",
        "report",
        "tick",
        "reconcile",
        "run-end",
        "session-reset",
        "open",
        "incident-status",
        "incident-clear",
      ].includes(verb ?? "")
    ) {
      invalid(`unknown network command: ${verb ?? "(missing)"}`);
    }
    if (isHelp(argv[2])) return { kind: "help", text: NETWORK_HELP };
    if (verb === "status" || verb === "report") {
      const options = parseOptions(argv.slice(2), {
        "--date": "value",
        "--state-dir": "value",
      });
      return {
        kind: "command",
        command: `network ${verb}`,
        input: networkReadInput(options, context),
      };
    }
    if (verb === "incident-status") {
      const options = parseOptions(argv.slice(2), {
        "--state-dir": "value",
        "--prune-days": "value",
      });
      const pruneDaysValue = options.values.get("--prune-days");
      const input: NetworkIncidentStatusInput = {
        stateDir: stateDir(options, context),
        ...(pruneDaysValue === undefined
          ? {}
          : { pruneDays: boundedInteger(pruneDaysValue, "--prune-days", 1, 365) }),
      };
      return { kind: "command", command: "network incident-status", input };
    }
    if (verb === "incident-clear") {
      const options = parseOptions(argv.slice(2), {
        "--state-dir": "value",
        "--reason": "value",
        "--account-access-confirmed": "boolean",
        "--warning-cleared-confirmed": "boolean",
      });
      if (!options.booleans.has("--account-access-confirmed")) {
        invalid("incident-clear requires --account-access-confirmed");
      }
      if (!options.booleans.has("--warning-cleared-confirmed")) {
        invalid("incident-clear requires --warning-cleared-confirmed");
      }
      const reason = options.values.get("--reason");
      if (reason === undefined || reason.trim().length === 0) {
        invalid("incident-clear requires a non-empty --reason");
      }
      const input: NetworkIncidentClearInput = {
        stateDir: stateDir(options, context),
        reason: reason.trim(),
        accountAccessConfirmed: true,
        warningClearedConfirmed: true,
      };
      return { kind: "command", command: "network incident-clear", input };
    }
    if (verb === "tick") {
      const options = parseOptions(argv.slice(2), {
        "--allow-send": "boolean",
        "--batch-size": "value",
        "--max-real-sends": "value",
        "--date": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      if (!options.booleans.has("--allow-send")) {
        throw new CliError(
          "SEND_NOT_AUTHORIZED",
          "network tick requires the explicit --allow-send flag",
          { exitCode: 3 },
        );
      }
      return {
        kind: "command",
        command: "network tick",
        input: networkTickInput(options, context),
      };
    }
    if (verb === "run-end") {
      const options = parseOptions(argv.slice(2), {
        "--date": "value",
        "--state-dir": "value",
        "--reason": "value",
      });
      const reason = options.values.get("--reason");
      if (reason === undefined || reason.trim().length === 0) {
        invalid("run-end requires a non-empty --reason");
      }
      const input: NetworkRunEndInput = {
        stateDir: stateDir(options, context),
        localDate: parseDate(required(options, "--date"), "--date"),
        reason: reason.trim(),
      };
      return { kind: "command", command: "network run-end", input };
    }
    if (verb === "session-reset") {
      const options = parseOptions(argv.slice(2), {
        "--state-dir": "value",
        "--playwriter-bin": "value",
      });
      const input: NetworkSessionResetInput = {
        stateDir: stateDir(options, context),
        playwriterBin: playwriterBin(options, context),
      };
      return { kind: "command", command: "network session-reset", input };
    }
    if (verb === "open") {
      const options = parseOptions(argv.slice(2), {
        "--page": "value",
        "--source": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      const pageRaw = required(options, "--page");
      if (pageRaw !== "sent" && pageRaw !== "search") {
        invalid("--page must be sent or search");
      }
      const sourceRaw = options.values.get("--source");
      let sourceId: NetworkOpenInput["sourceId"];
      if (sourceRaw !== undefined) {
        if (
          sourceRaw !== "b2b-saas-founders" &&
          sourceRaw !== "b2b-saas-engineering-product-leaders"
        ) {
          invalid("--source must be b2b-saas-founders or b2b-saas-engineering-product-leaders");
        }
        sourceId = sourceRaw;
      }
      if (pageRaw === "sent" && sourceRaw !== undefined) {
        invalid("--source is only valid with --page search");
      }
      const input: NetworkOpenInput = {
        stateDir: stateDir(options, context),
        playwriterBin: playwriterBin(options, context),
        sessionId: requiredWorkflowSession(
          options.values.get("--session"),
          context.env.LINKEDIN_TOOLS_NETWORK_SESSION,
          "--session",
        ),
        page: pageRaw,
        ...(sourceId === undefined ? {} : { sourceId }),
      };
      return { kind: "command", command: "network open", input };
    }
    const options = parseOptions(argv.slice(2), {
      "--date": "value",
      "--state-dir": "value",
      "--session": "value",
      "--playwriter-bin": "value",
    });
    return {
      kind: "command",
      command: "network reconcile",
      input: networkReconcileInput(options, context),
    };
  }

  if (argv[0] === "analytics") {
    if (argv.length === 1 || isHelp(argv[1]) || isHelp(argv[2])) {
      return { kind: "help", text: ANALYTICS_HELP };
    }
    if (argv[1] !== "export") invalid(`unknown analytics command: ${argv[1]}`);
    const options = parseOptions(argv.slice(2), {
      "--out": "value",
      "--receipt": "value",
      "--recovery-state": "value",
      "--account": "value",
      "--start-date": "value",
      "--end-date": "value",
      "--period": "value",
      "--download-root": "repeatable",
      "--poll-interval-ms": "value",
      "--max-polls": "value",
      "--state-dir": "value",
      "--session": "value",
      "--playwriter-bin": "value",
    });
    return {
      kind: "command",
      command: "analytics export",
      input: analyticsInput(options, context),
    };
  }

  if (argv[0] === "migration") {
    if (argv.length === 1 || isHelp(argv[1]) || isHelp(argv[2])) {
      return { kind: "help", text: MIGRATION_HELP };
    }
    if (argv[1] !== "dry-run") invalid(`unknown migration command: ${argv[1]}`);
    const options = parseOptions(argv.slice(2), { "--source-root": "value" });
    const input: MigrationDryRunInput = {
      sourceRoot: absolutePath(required(options, "--source-root"), "--source-root"),
    };
    return { kind: "command", command: "migration dry-run", input };
  }

  if (argv[0] === "jobs") {
    if (argv.length === 1 || isHelp(argv[1])) return { kind: "help", text: JOBS_HELP };
    const verb = argv[1];
    if (
      ![
        "capture-start",
        "capture-ingest",
        "capture-finish",
        "normalize",
        "filter",
        "enrich",
        "detail",
        "list",
        "check",
        "favorite",
        "draft",
        "send",
        "remove",
        "classify",
        "triage-next",
        "triage-record",
        "hubspot-next",
        "hubspot-record",
      ].includes(verb ?? "")
    ) {
      invalid(`unknown jobs command: ${verb ?? "(missing)"}`);
    }
    if (isHelp(argv[2])) return { kind: "help", text: JOBS_HELP };
    if (verb === "triage-next") {
      const options = parseOptions(argv.slice(2), { "--run-id": "value", "--state-dir": "value" });
      const runId = options.values.get("--run-id");
      const input: JobsTriageNextInput = {
        stateDir: stateDir(options, context),
        ...(runId === undefined ? {} : { runId: boundedText(runId, "--run-id", 200) }),
      };
      return { kind: "command", command: "jobs triage-next", input };
    }
    if (verb === "triage-record") {
      const options = parseOptions(argv.slice(2), {
        "--id": "value",
        "--bucket": "value",
        "--company-summary": "value",
        "--work-summary": "value",
        "--responsibilities": "value",
        "--skill-matches": "value",
        "--skill-gaps": "value",
        "--reason": "value",
        "--policy-version": "value",
        "--state-dir": "value",
      });
      const array = (name: string, maxCount: number): string[] => {
        let value: unknown;
        try {
          value = JSON.parse(required(options, name));
        } catch {
          invalid(`${name} must be a JSON array of strings`);
        }
        if (
          !Array.isArray(value) ||
          value.some((item) => typeof item !== "string") ||
          value.length > maxCount
        )
          invalid(`${name} must be a JSON array of at most ${maxCount} strings`);
        return value.map((item) => boundedText(item, name, 200));
      };
      const bucket = required(options, "--bucket");
      if (bucket !== "strong" && bucket !== "possible" && bucket !== "weak")
        invalid("--bucket must be strong, possible, or weak");
      const policyVersion = boundedText(
        required(options, "--policy-version"),
        "--policy-version",
        80,
      );
      const input: JobsTriageRecordInput = {
        stateDir: stateDir(options, context),
        id: boundedText(required(options, "--id"), "--id", 200),
        bucket,
        companySummary: boundedText(
          required(options, "--company-summary"),
          "--company-summary",
          500,
        ),
        workSummary: boundedText(required(options, "--work-summary"), "--work-summary", 500),
        responsibilities: array("--responsibilities", 5),
        skillMatches: array("--skill-matches", 8),
        skillGaps: array("--skill-gaps", 8),
        reason: boundedText(required(options, "--reason"), "--reason", 500),
        policyVersion,
      };
      return { kind: "command", command: "jobs triage-record", input };
    }
    if (verb === "hubspot-next") {
      const options = parseOptions(argv.slice(2), {
        "--id": "value",
        "--state-dir": "value",
      });
      const id = options.values.get("--id");
      const input: JobsHubSpotNextInput = {
        stateDir: stateDir(options, context),
        ...(id === undefined ? {} : { id: boundedText(id, "--id", 200) }),
      };
      return { kind: "command", command: "jobs hubspot-next", input };
    }
    if (verb === "hubspot-record") {
      const options = parseOptions(argv.slice(2), {
        "--prospect-id": "value",
        "--company-id": "value",
        "--contact-id": "value",
        "--deal-id": "value",
        "--task-id": "value",
        "--associations-complete": "boolean",
        "--error": "value",
        "--state-dir": "value",
      });
      const prospectId = required(options, "--prospect-id").trim();
      if (!/^co:need-led:v1:[a-f0-9]{64}$/.test(prospectId)) {
        invalid("--prospect-id must be a co:need-led:v1 SHA-256 identifier");
      }
      const companyId = hubSpotId(options.values.get("--company-id"), "--company-id");
      const contactId = hubSpotId(options.values.get("--contact-id"), "--contact-id");
      const dealId = hubSpotId(options.values.get("--deal-id"), "--deal-id");
      const taskId = hubSpotId(options.values.get("--task-id"), "--task-id");
      const associationsComplete = options.booleans.has("--associations-complete");
      const error = options.values.get("--error");
      const hasReceipt =
        companyId !== undefined ||
        contactId !== undefined ||
        dealId !== undefined ||
        taskId !== undefined ||
        associationsComplete;
      if (!hasReceipt && error === undefined) {
        invalid("jobs hubspot-record requires a HubSpot ID, --associations-complete, or --error");
      }
      if (hasReceipt && error !== undefined) {
        invalid("--error conflicts with HubSpot receipt options");
      }
      const input: JobsHubSpotRecordInput = {
        stateDir: stateDir(options, context),
        prospectId,
        ...(companyId === undefined ? {} : { companyId }),
        ...(contactId === undefined ? {} : { contactId }),
        ...(dealId === undefined ? {} : { dealId }),
        ...(taskId === undefined ? {} : { taskId }),
        ...(associationsComplete ? { associationsComplete: true as const } : {}),
        ...(error === undefined ? {} : { error: boundedText(error, "--error", 2000) }),
      };
      return { kind: "command", command: "jobs hubspot-record", input };
    }
    if (verb === "capture-start") {
      const options = parseOptions(argv.slice(2), {
        "--run-id": "value",
        "--source-url": "value",
        "--search-config": "value",
        "--checkpoint": "value",
        "--state-dir": "value",
      });
      const input: JobsCaptureStartInput = {
        stateDir: stateDir(options, context),
        runId: required(options, "--run-id"),
        sourceUrl: required(options, "--source-url"),
        ...(options.values.get("--search-config") === undefined
          ? {}
          : { searchConfigJson: options.values.get("--search-config") }),
        ...(options.values.get("--checkpoint") === undefined
          ? {}
          : { checkpointJson: options.values.get("--checkpoint") }),
      };
      return { kind: "command", command: "jobs capture-start", input };
    }
    if (verb === "capture-ingest") {
      const options = parseOptions(argv.slice(2), {
        "--run-id": "value",
        "--page": "value",
        "--payload": "value",
        "--source-url": "value",
        "--response-url": "value",
        "--cursor": "value",
        "--captured-at": "value",
        "--state-dir": "value",
      });
      const input: JobsCaptureIngestInput = {
        stateDir: stateDir(options, context),
        runId: required(options, "--run-id"),
        pageIdentity: required(options, "--page"),
        payloadPath:
          required(options, "--payload") === "-"
            ? "-"
            : absolutePath(required(options, "--payload"), "--payload"),
        sourceUrl: required(options, "--source-url"),
        responseUrl: required(options, "--response-url"),
        ...(options.values.get("--cursor") === undefined
          ? {}
          : { cursor: options.values.get("--cursor") }),
        ...(options.values.get("--captured-at") === undefined
          ? {}
          : { capturedAt: options.values.get("--captured-at") }),
      };
      return { kind: "command", command: "jobs capture-ingest", input };
    }
    if (verb === "capture-finish") {
      const options = parseOptions(argv.slice(2), {
        "--run-id": "value",
        "--state": "value",
        "--checkpoint": "value",
        "--error": "value",
        "--state-dir": "value",
      });
      const state = required(options, "--state");
      if (state !== "complete" && state !== "failed") invalid("--state must be complete or failed");
      const input: JobsCaptureFinishInput = {
        stateDir: stateDir(options, context),
        runId: required(options, "--run-id"),
        state,
        ...(options.values.get("--checkpoint") === undefined
          ? {}
          : { checkpointJson: options.values.get("--checkpoint") }),
        ...(options.values.get("--error") === undefined
          ? {}
          : { error: options.values.get("--error") }),
      };
      return { kind: "command", command: "jobs capture-finish", input };
    }
    if (verb === "normalize") {
      const options = parseOptions(argv.slice(2), {
        "--run-id": "value",
        "--limit": "value",
        "--state-dir": "value",
      });
      const limitRaw = options.values.get("--limit");
      const input: JobsNormalizeInput = {
        stateDir: stateDir(options, context),
        runId: required(options, "--run-id"),
        ...(limitRaw === undefined ? {} : { limit: boundedInteger(limitRaw, "--limit", 1, 500) }),
      };
      return { kind: "command", command: "jobs normalize", input };
    }
    if (verb === "filter") {
      const options = parseOptions(argv.slice(2), {
        "--run-id": "value",
        "--terms": "value",
        "--policy-version": "value",
        "--max-age-days": "value",
        "--state-dir": "value",
      });
      let parsed: unknown;
      try {
        parsed = JSON.parse(required(options, "--terms"));
      } catch {
        invalid("--terms must be a JSON array of strings");
      }
      if (
        !Array.isArray(parsed) ||
        parsed.length === 0 ||
        parsed.some((term) => typeof term !== "string" || term.trim() === "")
      )
        invalid("--terms must be a non-empty JSON array of strings");
      const policyVersion = required(options, "--policy-version").trim();
      if (policyVersion === "") invalid("--policy-version must be non-empty");
      const maxAgeRaw = options.values.get("--max-age-days");
      return {
        kind: "command",
        command: "jobs filter",
        input: {
          stateDir: stateDir(options, context),
          runId: required(options, "--run-id"),
          terms: parsed as string[],
          policyVersion,
          ...(maxAgeRaw === undefined
            ? {}
            : { maxAgeDays: boundedInteger(maxAgeRaw, "--max-age-days", 1, 365) }),
        },
      };
    }
    if (verb === "enrich") {
      const options = parseOptions(argv.slice(2), {
        "--limit": "value",
        "--run-id": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      const limitRaw = options.values.get("--limit");
      const runId = options.values.get("--run-id");
      const input: JobsEnrichInput = {
        stateDir: stateDir(options, context),
        playwriterBin: playwriterBin(options, context),
        sessionId: requiredWorkflowSession(
          options.values.get("--session"),
          context.env.LINKEDIN_TOOLS_JOBS_SESSION,
          "--session",
        ),
        ...(limitRaw === undefined ? {} : { limit: boundedInteger(limitRaw, "--limit", 1, 200) }),
        ...(runId === undefined ? {} : { runId }),
      };
      return { kind: "command", command: "jobs enrich", input };
    }
    if (verb === "detail") {
      const options = parseOptions(argv.slice(2), {
        "--limit": "value",
        "--run-id": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      const limitRaw = options.values.get("--limit");
      const runId = options.values.get("--run-id");
      const input: JobsDetailInput = {
        stateDir: stateDir(options, context),
        playwriterBin: playwriterBin(options, context),
        sessionId: requiredWorkflowSession(
          options.values.get("--session"),
          context.env.LINKEDIN_TOOLS_JOBS_SESSION,
          "--session",
        ),
        ...(limitRaw === undefined ? {} : { limit: boundedInteger(limitRaw, "--limit", 1, 500) }),
        ...(runId === undefined ? {} : { runId }),
      };
      return { kind: "command", command: "jobs detail", input };
    }
    if (verb === "list") {
      const options = parseOptions(argv.slice(2), {
        "--status": "value",
        "--with-hiring-team": "boolean",
        "--state-dir": "value",
      });
      const status = options.values.get("--status");
      if (
        status !== undefined &&
        !["captured", "collected", "favorite", "drafted", "sent"].includes(status)
      ) {
        invalid("--status must be captured, collected, favorite, drafted, or sent");
      }
      const input: JobsListInput = {
        stateDir: stateDir(options, context),
        ...(status === undefined ? {} : { status: status as NonNullable<JobsListInput["status"]> }),
        withHiringTeam: options.booleans.has("--with-hiring-team"),
      };
      return { kind: "command", command: "jobs list", input };
    }
    if (verb === "check") {
      const options = parseOptions(argv.slice(2), {
        "--status": "value",
        "--with-hiring-team": "boolean",
        "--limit": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      const status = options.values.get("--status");
      if (
        status !== undefined &&
        !["captured", "collected", "favorite", "drafted", "sent"].includes(status)
      ) {
        invalid("--status must be captured, collected, favorite, drafted, or sent");
      }
      const limitRaw = options.values.get("--limit");
      const input: JobsCheckInput = {
        stateDir: stateDir(options, context),
        playwriterBin: playwriterBin(options, context),
        sessionId: requiredWorkflowSession(
          options.values.get("--session"),
          context.env.LINKEDIN_TOOLS_JOBS_SESSION,
          "--session",
        ),
        ...(status === undefined
          ? {}
          : { status: status as NonNullable<JobsCheckInput["status"]> }),
        withHiringTeam: options.booleans.has("--with-hiring-team"),
        ...(limitRaw === undefined ? {} : { limit: boundedInteger(limitRaw, "--limit", 1, 500) }),
      };
      return { kind: "command", command: "jobs check", input };
    }
    if (verb === "favorite") {
      const options = parseOptions(argv.slice(2), {
        "--id": "repeatable",
        "--state-dir": "value",
      });
      const ids = options.repeated.get("--id") ?? [];
      if (ids.length === 0) invalid("jobs favorite requires at least one --id");
      const input: JobsFavoriteInput = { stateDir: stateDir(options, context), ids };
      return { kind: "command", command: "jobs favorite", input };
    }
    if (verb === "remove") {
      const options = parseOptions(argv.slice(2), {
        "--id": "repeatable",
        "--state-dir": "value",
      });
      const ids = options.repeated.get("--id") ?? [];
      if (ids.length === 0) invalid("jobs remove requires at least one --id");
      const input: JobsRemoveInput = { stateDir: stateDir(options, context), ids };
      return { kind: "command", command: "jobs remove", input };
    }
    if (verb === "draft") {
      const options = parseOptions(argv.slice(2), {
        "--id": "value",
        "--subject": "value",
        "--message": "value",
        "--state-dir": "value",
      });
      const message = options.values.get("--message");
      if (message === undefined || message.trim().length === 0) {
        invalid("jobs draft requires a non-empty --message");
      }
      if (message.trim().length > DRAFT_MAX_LENGTH) {
        invalid(`--message must be at most ${DRAFT_MAX_LENGTH} characters`);
      }
      const subject = (options.values.get("--subject") ?? "").trim();
      if (subject.length > SUBJECT_MAX_LENGTH) {
        invalid(`--subject must be at most ${SUBJECT_MAX_LENGTH} characters`);
      }
      const input: JobsDraftInput = {
        stateDir: stateDir(options, context),
        id: required(options, "--id"),
        subject,
        message: message.trim(),
      };
      return { kind: "command", command: "jobs draft", input };
    }
    if (verb === "classify") {
      const options = parseOptions(argv.slice(2), {
        "--id": "value",
        "--work-focus": "value",
        "--product-system": "value",
        "--work-summary": "value",
        "--product-summary": "value",
        "--state-dir": "value",
      });
      const workFocus = options.values.get("--work-focus");
      const productSystem = options.values.get("--product-system");
      const workSummary = options.values.get("--work-summary");
      const productSummary = options.values.get("--product-summary");
      if (
        workFocus === undefined ||
        productSystem === undefined ||
        workSummary === undefined ||
        productSummary === undefined
      ) {
        invalid(
          "jobs classify requires --work-focus, --product-system, --work-summary, and --product-summary",
        );
      }
      const input: JobsClassifyInput = {
        stateDir: stateDir(options, context),
        id: required(options, "--id"),
        workFocus: boundedText(workFocus, "--work-focus", CLASSIFICATION_MAX_LENGTH),
        productSystem: boundedText(productSystem, "--product-system", CLASSIFICATION_MAX_LENGTH),
        workSummary: boundedText(workSummary, "--work-summary", SUMMARY_MAX_LENGTH),
        productSummary: boundedText(productSummary, "--product-summary", SUMMARY_MAX_LENGTH),
      };
      return { kind: "command", command: "jobs classify", input };
    }
    const options = parseOptions(argv.slice(2), {
      "--allow-send": "boolean",
      "--id": "value",
      "--state-dir": "value",
      "--session": "value",
      "--playwriter-bin": "value",
    });
    if (!options.booleans.has("--allow-send")) {
      throw new CliError(
        "SEND_NOT_AUTHORIZED",
        "jobs send requires the explicit --allow-send flag",
        { exitCode: 3 },
      );
    }
    const input: JobsSendInput = {
      stateDir: stateDir(options, context),
      playwriterBin: playwriterBin(options, context),
      sessionId: requiredWorkflowSession(
        options.values.get("--session"),
        context.env.LINKEDIN_TOOLS_JOBS_SESSION,
        "--session",
      ),
      allowSend: true,
      ...(options.values.get("--id") === undefined
        ? {}
        : { id: options.values.get("--id") as string }),
    };
    return { kind: "command", command: "jobs send", input };
  }

  invalid(`unknown command: ${argv[0]}`);
}

function parseOptions(argv: readonly string[], spec: OptionSpec): ParsedOptions {
  const booleans = new Set<string>();
  const values = new Map<string, string>();
  const repeated = new Map<string, string[]>();
  for (let index = 0; index < argv.length; index += 1) {
    const option = argv[index];
    if (option === undefined || !option.startsWith("--")) {
      invalid(`unexpected positional argument: ${option ?? "(missing)"}`);
    }
    if (option.includes("=")) invalid(`use a separate value after ${option.split("=")[0]}`);
    const kind = spec[option];
    if (kind === undefined) invalid(`unknown option: ${option}`);
    if (kind === "boolean") {
      if (booleans.has(option)) invalid(`${option} may be provided only once`);
      booleans.add(option);
      continue;
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) invalid(`${option} requires a value`);
    index += 1;
    if (kind === "value") {
      if (values.has(option)) invalid(`${option} may be provided only once`);
      values.set(option, value);
    } else {
      const existing = repeated.get(option) ?? [];
      existing.push(value);
      repeated.set(option, existing);
    }
  }
  return { booleans, values, repeated };
}

function doctorInput(options: ParsedOptions, context: ParseContext): DoctorInput {
  const networkSession = optionalSession(
    options.values.get("--network-session"),
    context.env.LINKEDIN_TOOLS_NETWORK_SESSION,
    "--network-session",
  );
  const analyticsSession = optionalSession(
    options.values.get("--analytics-session"),
    context.env.LINKEDIN_TOOLS_ANALYTICS_SESSION,
    "--analytics-session",
  );
  return {
    stateDir: stateDir(options, context),
    playwriterBin: playwriterBin(options, context),
    ...(networkSession === undefined ? {} : { networkSessionId: networkSession }),
    ...(analyticsSession === undefined ? {} : { analyticsSessionId: analyticsSession }),
    ...(context.env.HOME === undefined
      ? {}
      : {
          automationPromptPath: join(
            context.env.HOME,
            ".codex",
            "automations",
            "linkedin-network",
            "automation.toml",
          ),
        }),
  };
}

function networkReadInput(options: ParsedOptions, context: ParseContext): NetworkReadInput {
  return {
    stateDir: stateDir(options, context),
    localDate: parseDate(options.values.get("--date") ?? localDate(context.now), "--date"),
  };
}

function networkTickInput(options: ParsedOptions, context: ParseContext): NetworkTickInput {
  const read = networkReadInput(options, context);
  return {
    ...read,
    allowSend: true,
    batchSize: boundedInteger(options.values.get("--batch-size") ?? "5", "--batch-size", 1, 5),
    maxRealSends: boundedInteger(
      options.values.get("--max-real-sends") ?? "30",
      "--max-real-sends",
      1,
      30,
    ),
    playwriterBin: playwriterBin(options, context),
    sessionId: requiredWorkflowSession(
      options.values.get("--session"),
      context.env.LINKEDIN_TOOLS_NETWORK_SESSION,
      "--session",
    ),
  };
}

function networkReconcileInput(
  options: ParsedOptions,
  context: ParseContext,
): NetworkReconcileInput {
  return {
    ...networkReadInput(options, context),
    playwriterBin: playwriterBin(options, context),
    sessionId: requiredWorkflowSession(
      options.values.get("--session"),
      context.env.LINKEDIN_TOOLS_NETWORK_SESSION,
      "--session",
    ),
  };
}

function analyticsInput(options: ParsedOptions, context: ParseContext): AnalyticsExportInput {
  const period = options.values.get("--period");
  const explicitStart = options.values.get("--start-date");
  const explicitEnd = options.values.get("--end-date");
  if (period !== undefined && (explicitStart !== undefined || explicitEnd !== undefined)) {
    invalid("--period conflicts with --start-date and --end-date");
  }
  let startDate: string;
  let endDate: string;
  if (period !== undefined) {
    if (period !== "previous-7-days") invalid("--period must be previous-7-days");
    ({ startDate, endDate } = previousSevenDays(context.now));
  } else {
    if (explicitStart === undefined || explicitEnd === undefined) {
      invalid("analytics export requires --period or both --start-date and --end-date");
    }
    startDate = parseDate(explicitStart, "--start-date");
    endDate = parseDate(explicitEnd, "--end-date");
    assertSevenInclusiveDays(startDate, endDate);
  }

  const state = stateDir(options, context);
  const template = { startDate, endDate };
  const outputPath = analyticsPath(
    expandPathTemplate(required(options, "--out"), template),
    "--out",
    ".xlsx",
  );
  const receiptValue =
    options.values.get("--receipt") ??
    join(state, "receipts", "analytics", `content-analytics-${startDate}-${endDate}.json`);
  const recoveryValue = options.values.get("--recovery-state");
  const roots =
    options.repeated.get("--download-root") ??
    splitPathList(context.env.LINKEDIN_TOOLS_ANALYTICS_DOWNLOAD_ROOTS);
  if (roots.length === 0) invalid("analytics export requires at least one --download-root");
  const account = options.values.get("--account") ?? context.env.LINKEDIN_TOOLS_ANALYTICS_ACCOUNT;
  if (account === undefined || account.trim().length === 0) {
    invalid("analytics export requires a non-empty --account");
  }
  const poll = options.values.get("--poll-interval-ms");
  const maxPolls = options.values.get("--max-polls");
  return {
    stateDir: state,
    playwriterBin: playwriterBin(options, context),
    sessionId: requiredWorkflowSession(
      options.values.get("--session"),
      context.env.LINKEDIN_TOOLS_ANALYTICS_SESSION,
      "--session",
    ),
    downloadRoots: roots.map((path) => absolutePath(path, "--download-root")),
    outputPath,
    receiptPath: analyticsPath(expandPathTemplate(receiptValue, template), "--receipt", ".json"),
    ...(recoveryValue === undefined
      ? {}
      : {
          recoveryStatePath: analyticsPath(
            expandPathTemplate(recoveryValue, template),
            "--recovery-state",
            ".json",
          ),
        }),
    expectedAccount: account.trim(),
    expectedStartDate: startDate,
    expectedEndDate: endDate,
    ...(poll === undefined
      ? {}
      : { pollIntervalMs: boundedInteger(poll, "--poll-interval-ms", 100, 60_000) }),
    ...(maxPolls === undefined
      ? {}
      : { maxPolls: boundedInteger(maxPolls, "--max-polls", 1, 120) }),
  };
}

function stateDir(options: ParsedOptions, context: ParseContext): string {
  const configured =
    options.values.get("--state-dir") ??
    context.env.LINKEDIN_TOOLS_STATE_DIR ??
    join(requiredHome(context.env), "Library", "Application Support", "linkedin-tools-next");
  return absolutePath(configured, "--state-dir");
}

function playwriterBin(options: ParsedOptions, context: ParseContext): string {
  return absolutePath(
    options.values.get("--playwriter-bin") ??
      context.env.LINKEDIN_TOOLS_PLAYWRITER_BIN ??
      PLAYWRITER_DEFAULT,
    "--playwriter-bin",
  );
}

function required(options: ParsedOptions, name: string): string {
  const value = options.values.get(name);
  if (value === undefined) invalid(`${name} is required`);
  return value;
}

function requiredWorkflowSession(
  explicit: string | undefined,
  configured: string | undefined,
  label: string,
): number | "auto" {
  const value = explicit ?? configured;
  if (value === undefined) {
    invalid(`${label} is required or its workflow session environment variable must be set`);
  }
  if (value === "auto") return value;
  return boundedInteger(value, label, 1, Number.MAX_SAFE_INTEGER);
}

function optionalSession(
  explicit: string | undefined,
  configured: string | undefined,
  label: string,
): number | undefined {
  const value = explicit ?? configured;
  return value === undefined ? undefined : boundedInteger(value, label, 1, Number.MAX_SAFE_INTEGER);
}

function boundedInteger(value: string, label: string, minimum: number, maximum: number): number {
  if (!/^[1-9]\d*$/.test(value)) invalid(`${label} must be an integer`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    invalid(`${label} must be between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function boundedText(value: string, label: string, maximum: number): string {
  const trimmed = value.trim();
  if (trimmed.length === 0) invalid(`${label} requires a non-empty value`);
  if (trimmed.length > maximum) invalid(`${label} must be at most ${maximum} characters`);
  return trimmed;
}

function hubSpotId(value: string | undefined, label: string): string | undefined {
  if (value === undefined) return undefined;
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) invalid(`${label} must be a numeric HubSpot object ID`);
  return trimmed;
}

function absolutePath(value: string, label: string): string {
  if (value.includes("\0") || value.trim().length === 0) invalid(`${label} is invalid`);
  if (!isAbsolute(value)) invalid(`${label} must be an absolute path`);
  const normalized = resolve(value);
  if (normalized === "/") invalid(`${label} must not be the filesystem root`);
  return normalized;
}

function analyticsPath(value: string, label: string, extension: string): string {
  const path = absolutePath(value, label);
  if (!path.toLowerCase().endsWith(extension)) {
    invalid(`${label} must end with ${extension}`);
  }
  return path;
}

function expandPathTemplate(
  value: string,
  fields: { readonly startDate: string; readonly endDate: string },
): string {
  const expanded = value
    .replaceAll("{startDate}", fields.startDate)
    .replaceAll("{endDate}", fields.endDate);
  if (/[{}]/.test(expanded)) invalid("path contains an unknown template field");
  return expanded;
}

function splitPathList(value: string | undefined): readonly string[] {
  return value
    ? value
        .split(":")
        .map((item) => item.trim())
        .filter(Boolean)
    : [];
}

function parseDate(value: string, label: string): string {
  if (!DATE.test(value)) invalid(`${label} must use YYYY-MM-DD`);
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 0, (month ?? 0) - 1, day ?? 0));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() + 1 !== month ||
    date.getUTCDate() !== day
  ) {
    invalid(`${label} is not a valid calendar date`);
  }
  return value;
}

function assertSevenInclusiveDays(startDate: string, endDate: string): void {
  const difference = Date.parse(`${endDate}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`);
  if (difference !== 6 * 24 * 60 * 60 * 1_000) {
    invalid("analytics date range must contain exactly seven inclusive days");
  }
}

function previousSevenDays(now: Date): { readonly startDate: string; readonly endDate: string } {
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(today - 24 * 60 * 60 * 1_000);
  const start = new Date(today - 7 * 24 * 60 * 60 * 1_000);
  return { startDate: utcDate(start), endDate: utcDate(end) };
}

function localDate(now: Date): string {
  return [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((value, index) =>
      index === 0 ? String(value).padStart(4, "0") : String(value).padStart(2, "0"),
    )
    .join("-");
}

function utcDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function requiredHome(env: Readonly<Record<string, string | undefined>>): string {
  const value = env.HOME;
  if (value === undefined || !isAbsolute(value)) invalid("HOME must be an absolute path");
  return value;
}

function isHelp(value: string | undefined): boolean {
  return value === "--help" || value === "-h";
}

function doctorHelp(): string {
  return `Usage:
  linkedin-tools [--json] doctor [--state-dir ABSOLUTE_PATH]
    [--playwriter-bin ABSOLUTE_PATH] [--network-session ID] [--analytics-session ID]

Doctor performs local prerequisite checks only. It does not navigate to or open LinkedIn.
`;
}

function invalid(message: string): never {
  throw new CliError("INVALID_ARGUMENT", message, { exitCode: 2 });
}
