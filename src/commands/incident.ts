import { activeIncident, clearIncident, type LinkedInIncident } from "../browser/incident.ts";
import { CliError } from "../core/errors.ts";
import type { NetworkIncidentClearInput, NetworkIncidentStatusInput } from "./types.ts";

export type IncidentStatusResult =
  | {
      readonly command: "network incident-status";
      readonly active: false;
      readonly incident: null;
    }
  | {
      readonly command: "network incident-status";
      readonly active: true;
      readonly incident: LinkedInIncident;
    };

export type IncidentClearResult = {
  readonly command: "network incident-clear";
  readonly cleared: true;
  readonly incident: LinkedInIncident;
};

export async function networkIncidentStatus(
  input: NetworkIncidentStatusInput,
): Promise<IncidentStatusResult> {
  const incident = await activeIncident(input.stateDir);
  if (incident === null) {
    return {
      command: "network incident-status",
      active: false,
      incident: null,
    };
  }
  return {
    command: "network incident-status",
    active: true,
    incident,
  };
}

export async function networkIncidentClear(
  input: NetworkIncidentClearInput,
): Promise<IncidentClearResult> {
  if (!input.accountAccessConfirmed || !input.warningClearedConfirmed) {
    throw new CliError(
      "INVALID_ARGUMENT",
      "incident-clear requires --account-access-confirmed and --warning-cleared-confirmed",
      { exitCode: 2 },
    );
  }
  if (input.reason.trim().length === 0) {
    throw new CliError("INVALID_ARGUMENT", "incident-clear requires a non-empty --reason", {
      exitCode: 2,
    });
  }
  try {
    const incident = await clearIncident(input.stateDir, {
      reason: input.reason,
      accountAccessConfirmed: input.accountAccessConfirmed,
      warningClearedConfirmed: input.warningClearedConfirmed,
    });
    return {
      command: "network incident-clear",
      cleared: true,
      incident,
    };
  } catch (error) {
    if (error instanceof CliError) throw error;
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes("no active LinkedIn incident")) {
      throw new CliError("INCIDENT_NOT_ACTIVE", message, { exitCode: 2 });
    }
    throw new CliError("INVALID_ARGUMENT", message, { exitCode: 2 });
  }
}
