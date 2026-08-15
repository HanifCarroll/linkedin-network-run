import { isAbsolute, join, resolve } from "node:path";
import { CliError } from "../core/errors.ts";
import type {
  AnalyticsExportInput,
  DoctorInput,
  JobsCheckInput,
  JobsCollectInput,
  JobsDetailInput,
  JobsDraftInput,
  JobsEnrichInput,
  JobsFavoriteInput,
  JobsListInput,
  JobsSearchInput,
  JobsSendInput,
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
  jobs search            Collect jobs from a LinkedIn search and check hiring teams
  jobs collect           Collect raw job postings from a LinkedIn search (no enrichment)
  jobs enrich            Enrich captured postings into enriched rows (company/hiring team)
  jobs detail            Pull full posting-page detail (description + structured fields)
  jobs list              List collected jobs from the local store
  jobs check             Verify stored postings are still live and drop removed ones
  jobs favorite          Mark collected jobs for review
  jobs draft             Store a drafted outreach message for a job
  jobs send              Send drafted messages to listed hiring team members (--allow-send)

Browser boundary:
  Playwriter is the only browser boundary: no Playwright import, direct CDP,
  Chrome-control fallback, browser lease, or cross-automation lock; no browser lease is used.

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
  linkedin-tools [--json] jobs search --keywords "product engineer"
    --location "United States" [--posted-within 1|7|14|30] [--remote]
    [--pages 1..10] [--hiring-team-limit 1..200] [--hiring-team-target 1..50]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs collect --keywords "product engineer"
    --location "United States" [--posted-within 1|7|14|30] [--remote] [--pages 1..10]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs enrich [--limit N]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs detail [--limit N]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs list [--status captured|collected|favorite|drafted|sent]
    [--with-hiring-team] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs check [--status captured|collected|favorite|drafted|sent]
    [--with-hiring-team] [--limit N] [--state-dir ABSOLUTE_PATH]
    [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]
  linkedin-tools [--json] jobs favorite --id JOB_ID [--id JOB_ID ...] [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs draft --id JOB_ID --message "..." [--state-dir ABSOLUTE_PATH]
  linkedin-tools [--json] jobs send --allow-send [--id JOB_ID]
    [--state-dir ABSOLUTE_PATH] [--session ID|auto] [--playwriter-bin ABSOLUTE_PATH]

jobs search collects postings from a LinkedIn jobs search (via the
voyagerJobsDashJobCards XHR), loads each posting's direct view to read the
"Meet the hiring team" section, and stores the jobs locally. With
--hiring-team-target N it keeps enriching until N postings with a listed
hiring team are found. Roughly 1 in 4 postings lists one. Each run collects
up to --pages of results (default 5, 25 postings per page) and skips
already-seen postings, so it auto-advances past exhausted pages; pagination
through the Playwriter relay is render-variable, so a run may collect fewer
pages than asked. Re-runs skip already-found teams, so the target converges.
--targetMet in the result says whether the target
was reached.
The collect/enrich split does the same work in two explicit phases: jobs
collect stores raw postings as 'captured'; jobs enrich drains the captured
pool into enriched 'collected' rows. Both are resumable and budget-capped.
jobs check verifies stored postings are still live by loading each direct
view and reading only the title; removed postings are dropped from the store.
It is much cheaper than enrich (no hiring-team extraction) and reports
{checked, live, dead, unclear}.
jobs send opens the first listed hiring team member's profile,
composes the drafted message, and sends it — it requires the explicit
--allow-send flag.
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
        if (sourceRaw !== "hubspot-agency-ops" && sourceRaw !== "hubspot-b2b-revops") {
          invalid("--source must be hubspot-agency-ops or hubspot-b2b-revops");
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
        "search",
        "collect",
        "enrich",
        "detail",
        "list",
        "check",
        "favorite",
        "draft",
        "send",
      ].includes(verb ?? "")
    ) {
      invalid(`unknown jobs command: ${verb ?? "(missing)"}`);
    }
    if (isHelp(argv[2])) return { kind: "help", text: JOBS_HELP };
    if (verb === "search") {
      const options = parseOptions(argv.slice(2), {
        "--keywords": "value",
        "--location": "value",
        "--posted-within": "value",
        "--remote": "boolean",
        "--pages": "value",
        "--hiring-team-limit": "value",
        "--hiring-team-target": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      return { kind: "command", command: "jobs search", input: jobsSearchInput(options, context) };
    }
    if (verb === "collect") {
      const options = parseOptions(argv.slice(2), {
        "--keywords": "value",
        "--location": "value",
        "--posted-within": "value",
        "--remote": "boolean",
        "--pages": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      return {
        kind: "command",
        command: "jobs collect",
        input: jobsCollectInput(options, context),
      };
    }
    if (verb === "enrich") {
      const options = parseOptions(argv.slice(2), {
        "--limit": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      const limitRaw = options.values.get("--limit");
      const input: JobsEnrichInput = {
        stateDir: stateDir(options, context),
        playwriterBin: playwriterBin(options, context),
        sessionId: requiredWorkflowSession(
          options.values.get("--session"),
          context.env.LINKEDIN_TOOLS_JOBS_SESSION,
          "--session",
        ),
        ...(limitRaw === undefined ? {} : { limit: boundedInteger(limitRaw, "--limit", 1, 200) }),
      };
      return { kind: "command", command: "jobs enrich", input };
    }
    if (verb === "detail") {
      const options = parseOptions(argv.slice(2), {
        "--limit": "value",
        "--state-dir": "value",
        "--session": "value",
        "--playwriter-bin": "value",
      });
      const limitRaw = options.values.get("--limit");
      const input: JobsDetailInput = {
        stateDir: stateDir(options, context),
        playwriterBin: playwriterBin(options, context),
        sessionId: requiredWorkflowSession(
          options.values.get("--session"),
          context.env.LINKEDIN_TOOLS_JOBS_SESSION,
          "--session",
        ),
        ...(limitRaw === undefined ? {} : { limit: boundedInteger(limitRaw, "--limit", 1, 500) }),
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
    if (verb === "draft") {
      const options = parseOptions(argv.slice(2), {
        "--id": "value",
        "--message": "value",
        "--state-dir": "value",
      });
      const message = options.values.get("--message");
      if (message === undefined || message.trim().length === 0) {
        invalid("jobs draft requires a non-empty --message");
      }
      const input: JobsDraftInput = {
        stateDir: stateDir(options, context),
        id: required(options, "--id"),
        message: message.trim(),
      };
      return { kind: "command", command: "jobs draft", input };
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

function jobsSearchInput(options: ParsedOptions, context: ParseContext): JobsSearchInput {
  const keywords = required(options, "--keywords");
  const postedRaw = options.values.get("--posted-within");
  const postedWithinDays =
    postedRaw === undefined ? undefined : boundedInteger(postedRaw, "--posted-within", 1, 30);
  if (postedWithinDays !== undefined && ![1, 7, 14, 30].includes(postedWithinDays)) {
    invalid("--posted-within must be 1, 7, 14, or 30");
  }
  const targetRaw = options.values.get("--hiring-team-target");
  const target =
    targetRaw === undefined ? 0 : boundedInteger(targetRaw, "--hiring-team-target", 1, 50);
  return {
    stateDir: stateDir(options, context),
    playwriterBin: playwriterBin(options, context),
    sessionId: requiredWorkflowSession(
      options.values.get("--session"),
      context.env.LINKEDIN_TOOLS_JOBS_SESSION,
      "--session",
    ),
    keywords,
    location: options.values.get("--location") ?? "",
    ...(postedWithinDays === undefined ? {} : { postedWithinDays }),
    ...(options.booleans.has("--remote") ? { remote: true } : {}),
    pages: boundedInteger(options.values.get("--pages") ?? "5", "--pages", 1, 10),
    // With a target, scale the default view cap so the pool is large enough
    // to find it (roughly 1 in 4 postings lists a hiring team).
    hiringTeamLimit: boundedInteger(
      options.values.get("--hiring-team-limit") ??
        (target === 0 ? "25" : String(Math.min(target * 4, 200))),
      "--hiring-team-limit",
      1,
      200,
    ),
    ...(target === 0 ? {} : { hiringTeamTarget: target }),
  };
}

function jobsCollectInput(options: ParsedOptions, context: ParseContext): JobsCollectInput {
  const keywords = required(options, "--keywords");
  const postedRaw = options.values.get("--posted-within");
  const postedWithinDays =
    postedRaw === undefined ? undefined : boundedInteger(postedRaw, "--posted-within", 1, 30);
  if (postedWithinDays !== undefined && ![1, 7, 14, 30].includes(postedWithinDays)) {
    invalid("--posted-within must be 1, 7, 14, or 30");
  }
  return {
    stateDir: stateDir(options, context),
    playwriterBin: playwriterBin(options, context),
    sessionId: requiredWorkflowSession(
      options.values.get("--session"),
      context.env.LINKEDIN_TOOLS_JOBS_SESSION,
      "--session",
    ),
    keywords,
    location: options.values.get("--location") ?? "",
    ...(postedWithinDays === undefined ? {} : { postedWithinDays }),
    ...(options.booleans.has("--remote") ? { remote: true } : {}),
    pages: boundedInteger(options.values.get("--pages") ?? "5", "--pages", 1, 10),
  };
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
