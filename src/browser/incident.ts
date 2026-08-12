import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";

export const INCIDENT_FILE_NAME = "linkedin-incident.json";
export const INCIDENT_ACTIVE_CODE = "INCIDENT_ACTIVE";
export const INCIDENT_ACTIVE_EXIT_CODE = 7;

export type IncidentKind =
  | "weekly_limit"
  | "rate_limit"
  | "unusual_activity"
  | "checkpoint"
  | "security_verification"
  | "login_required"
  | "unknown";

export type LinkedInIncident = {
  readonly kind: IncidentKind;
  readonly reason: string;
  readonly detail: string;
  readonly opened_at: string;
  readonly active: boolean;
  readonly cleared_at?: string;
  readonly clearance_reason?: string;
  readonly account_access_confirmed?: boolean;
  readonly warning_cleared_confirmed?: boolean;
};

export class ActiveIncidentError extends Error {
  readonly code = INCIDENT_ACTIVE_CODE;
  readonly exitCode = INCIDENT_ACTIVE_EXIT_CODE;
  readonly incident: LinkedInIncident;

  constructor(incident: LinkedInIncident) {
    const summary = `${incident.kind}: ${incident.reason}`;
    super(`LinkedIn browser work is paused by active incident (${summary})`);
    this.name = "ActiveIncidentError";
    this.incident = incident;
  }
}

export class IncidentDetectedError extends Error {
  readonly code = INCIDENT_ACTIVE_CODE;
  readonly exitCode = INCIDENT_ACTIVE_EXIT_CODE;
  readonly incident: LinkedInIncident;

  constructor(incident: LinkedInIncident) {
    const summary = `${incident.kind}: ${incident.reason}`;
    super(`LinkedIn browser operation stopped and opened incident (${summary})`);
    this.name = "IncidentDetectedError";
    this.incident = incident;
  }
}

const FATAL_RULES: readonly {
  readonly kind: IncidentKind;
  readonly needles: readonly string[];
  readonly reason: string;
}[] = [
  {
    kind: "unusual_activity",
    needles: ["unusual activity", "we noticed some unusual activity on your account"],
    reason: "LinkedIn unusual-activity warning",
  },
  {
    kind: "rate_limit",
    needles: ["http 429", "status 429", "returned http 429", "429"],
    reason: "LinkedIn returned HTTP 429",
  },
  {
    kind: "checkpoint",
    needles: ["checkpoint present", "/checkpoint", "checkpoint"],
    reason: "LinkedIn checkpoint present",
  },
  {
    kind: "security_verification",
    needles: ["security verification", "security challenge", "quick security check"],
    reason: "LinkedIn security verification present",
  },
  {
    kind: "login_required",
    needles: ["session_key", "authwall", "login required", "/uas/login", "linkedin.com/login"],
    reason: "LinkedIn login required",
  },
  {
    kind: "weekly_limit",
    needles: [
      "weekly limit",
      "weekly invitation limit",
      "weekly connection limit",
      "you've reached the weekly",
      "you’ve reached the weekly",
    ],
    reason: "LinkedIn weekly limit reached",
  },
];

/** Ordinary automation blockers must never open the shared incident gate. */
const ORDINARY_BLOCKER_MARKERS = [
  "WRONG_PAGE",
  "WORKFLOW_PAGE_MISSING",
  "WORKFLOW_PAGE_AMBIGUOUS",
  "SELECTOR_CONTRACT",
  "ALREADY_PENDING",
  "EMAIL_REQUIRED",
  "MISSING_MORE_ACTIONS",
  "MISSING_CONNECT_MENU",
  "MISSING_SEND",
  "DISABLED_SEND",
  "CANDIDATE_ABSENT",
  "ROW_LOAD_TIMEOUT",
  "NO_ROWS",
  "STALLED_NAVIGATION",
  "SOURCE_EXHAUSTED",
  "SOURCE_MISMATCH",
  "UNCLEAR_CONFIRMATION",
  "PREPARATION_MISMATCH",
  "PREPARATION_STALE",
  "COMMIT_SEND_UNCERTAIN",
  "EVIDENCE_CORRUPT",
  "EVIDENCE_FINALIZATION",
  "EVIDENCE_HANDOFF",
] as const;

export function incidentPath(stateDir: string): string {
  return join(stateDir, INCIDENT_FILE_NAME);
}

export async function loadIncident(stateDir: string): Promise<LinkedInIncident | null> {
  const path = incidentPath(stateDir);
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    if (typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT") {
      return null;
    }
    throw error;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      !("kind" in parsed) ||
      !("reason" in parsed) ||
      !("detail" in parsed) ||
      !("opened_at" in parsed) ||
      !("active" in parsed) ||
      typeof parsed.kind !== "string" ||
      typeof parsed.reason !== "string" ||
      typeof parsed.detail !== "string" ||
      typeof parsed.opened_at !== "string" ||
      typeof parsed.active !== "boolean"
    ) {
      return null;
    }
    return parsed as LinkedInIncident;
  } catch {
    return null;
  }
}

export async function activeIncident(stateDir: string): Promise<LinkedInIncident | null> {
  const incident = await loadIncident(stateDir);
  return incident !== null && incident.active ? incident : null;
}

export async function assertNoActiveIncident(stateDir: string): Promise<void> {
  const incident = await activeIncident(stateDir);
  if (incident !== null) throw new ActiveIncidentError(incident);
}

export async function openIncident(
  stateDir: string,
  input: {
    readonly kind: IncidentKind;
    readonly reason: string;
    readonly detail: string;
    readonly openedAt?: string;
  },
): Promise<LinkedInIncident> {
  const existing = await activeIncident(stateDir);
  if (existing !== null) return existing;
  const incident: LinkedInIncident = {
    kind: input.kind,
    reason: input.reason,
    detail: input.detail.slice(0, 2000),
    opened_at: input.openedAt ?? new Date().toISOString(),
    active: true,
  };
  await writeIncident(stateDir, incident);
  return incident;
}

export async function clearIncident(
  stateDir: string,
  input: {
    readonly reason: string;
    readonly accountAccessConfirmed: boolean;
    readonly warningClearedConfirmed: boolean;
    readonly clearedAt?: string;
  },
): Promise<LinkedInIncident> {
  const incident = await activeIncident(stateDir);
  if (incident === null) {
    throw new Error("no active LinkedIn incident exists");
  }
  if (!input.accountAccessConfirmed || !input.warningClearedConfirmed) {
    throw new Error(
      "clearing the LinkedIn incident requires confirmed account access and confirmed warning clearance",
    );
  }
  const reason = input.reason.trim();
  if (reason.length === 0) {
    throw new Error("clearing the LinkedIn incident requires a reason");
  }
  const cleared: LinkedInIncident = {
    ...incident,
    active: false,
    cleared_at: input.clearedAt ?? new Date().toISOString(),
    clearance_reason: reason,
    account_access_confirmed: true,
    warning_cleared_confirmed: true,
  };
  await writeIncident(stateDir, cleared);
  return cleared;
}

export function detectFatalIncident(
  value: unknown,
): { readonly kind: IncidentKind; readonly reason: string; readonly detail: string } | null {
  const strings = flattenScalarStrings(value);
  if (strings.length === 0) return null;
  const joined = strings.join("\n");
  const normalized = joined.toLowerCase();
  const matched = FATAL_RULES.find((rule) =>
    rule.needles.some((needle) => normalized.includes(needle.toLowerCase())),
  );
  if (matched === undefined) return null;
  // Ordinary automation blockers alone never open the shared gate; a true fatal
  // needle still wins when both appear in the same failure text.
  const primary = matched.needles[0] ?? matched.reason;
  const index = normalized.indexOf(primary.toLowerCase());
  const detail =
    index < 0
      ? joined.slice(0, 400)
      : joined
          .slice(Math.max(0, index - 80), Math.min(joined.length, index + primary.length + 200))
          .replace(/\s+/g, " ")
          .trim();
  return { kind: matched.kind, reason: matched.reason, detail };
}

/**
 * Scan failure text and open the shared gate when a fatal LinkedIn signal is present.
 * Ordinary blockers alone never open an incident.
 */
export async function maybeOpenIncidentFromFailure(
  stateDir: string,
  value: unknown,
): Promise<LinkedInIncident | null> {
  if (
    typeof value === "string" &&
    ORDINARY_BLOCKER_MARKERS.some((marker) => value.includes(marker))
  ) {
    const hasFatal = FATAL_RULES.some((rule) =>
      rule.needles.some((needle) => value.toLowerCase().includes(needle.toLowerCase())),
    );
    if (!hasFatal) return null;
  }
  const detected = detectFatalIncident(value);
  if (detected === null) return null;
  return openIncident(stateDir, detected);
}

async function writeIncident(stateDir: string, incident: LinkedInIncident): Promise<void> {
  await mkdir(stateDir, { recursive: true, mode: 0o700 });
  const destination = incidentPath(stateDir);
  const temporary = join(stateDir, `.${INCIDENT_FILE_NAME}.${process.pid}.tmp`);
  const body = `${JSON.stringify(incident, null, 2)}\n`;
  try {
    await writeFile(temporary, body, { encoding: "utf8", mode: 0o600 });
    await rename(temporary, destination);
  } catch (error) {
    try {
      await unlink(temporary);
    } catch {
      // best-effort cleanup
    }
    throw error;
  }
}

function flattenScalarStrings(value: unknown): string[] {
  const result: string[] = [];
  const visit = (item: unknown): void => {
    if (typeof item === "string") {
      result.push(item);
      return;
    }
    if (typeof item === "number" || typeof item === "boolean") {
      result.push(String(item));
      return;
    }
    if (item === null || item === undefined) return;
    if (Array.isArray(item)) {
      for (const child of item) visit(child);
      return;
    }
    if (typeof item === "object") {
      for (const [key, child] of Object.entries(item)) {
        result.push(key);
        visit(child);
      }
    }
  };
  visit(value);
  return result;
}
