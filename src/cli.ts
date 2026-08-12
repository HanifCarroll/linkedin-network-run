#!/usr/bin/env bun

import {
  ActiveIncidentError,
  INCIDENT_ACTIVE_CODE,
  INCIDENT_ACTIVE_EXIT_CODE,
  IncidentDetectedError,
} from "./browser/incident.ts";
import { extractJsonMode, parseInvocation } from "./commands/arguments.ts";
import { createDefaultOperations } from "./commands/operations.ts";
import type { CliOperations, ParsedInvocation } from "./commands/types.ts";
import { failure, success } from "./core/envelope.ts";
import { CliError } from "./core/errors.ts";
export type CliIo = {
  readonly stdout: (value: string) => void;
  readonly stderr: (value: string) => void;
};

export type CliDependencies = {
  readonly operations: CliOperations;
  readonly io: CliIo;
  readonly now: () => Date;
  readonly env: Readonly<Record<string, string | undefined>>;
};

const defaultIo: CliIo = {
  stdout: (value) => console.log(value),
  stderr: (value) => console.error(value),
};

export async function run(
  argv: readonly string[],
  overrides: Partial<CliDependencies> = {},
): Promise<number> {
  const io = overrides.io ?? defaultIo;
  let json = argv.includes("--json");
  try {
    const global = extractJsonMode(argv);
    json = global.json;
    const invocation = parseInvocation(global.argv, {
      now: (overrides.now ?? (() => new Date()))(),
      env: overrides.env ?? process.env,
    });
    if (invocation.kind === "help") {
      io.stdout(invocation.text.trimEnd());
      return 0;
    }
    if (invocation.kind === "version") {
      io.stdout("0.1.0");
      return 0;
    }
    const operations = overrides.operations ?? createDefaultOperations();
    const data = await execute(invocation, operations);
    io.stdout(json ? JSON.stringify(success(data)) : JSON.stringify(data, null, 2));
    return 0;
  } catch (error) {
    const cliError = normalizeError(error);
    const envelope = failure({
      code: cliError.code,
      message: cliError.message,
      ...(cliError.details === undefined ? {} : { details: cliError.details }),
    });
    if (json) io.stdout(JSON.stringify(envelope));
    else io.stderr(`Error [${cliError.code}]: ${cliError.message}`);
    return cliError.exitCode;
  }
}

async function execute(invocation: ParsedInvocation, operations: CliOperations): Promise<unknown> {
  if (invocation.kind !== "command") {
    throw new CliError("INTERNAL_ERROR", "non-command invocation reached command execution");
  }
  switch (invocation.command) {
    case "doctor":
      return operations.doctor(invocation.input);
    case "network status":
      return operations.networkStatus(invocation.input);
    case "network report":
      return operations.networkReport(invocation.input);
    case "network tick":
      return operations.networkTick(invocation.input);
    case "network reconcile":
      return operations.networkReconcile(invocation.input);
    case "network run-end":
      return operations.networkRunEnd(invocation.input);
    case "network session-reset":
      return operations.networkSessionReset(invocation.input);
    case "network incident-status":
      return operations.networkIncidentStatus(invocation.input);
    case "network incident-clear":
      return operations.networkIncidentClear(invocation.input);
    case "analytics export":
      return operations.analyticsExport(invocation.input);
    case "migration dry-run":
      return operations.migrationDryRun(invocation.input);
  }
}

function normalizeError(error: unknown): CliError {
  if (error instanceof CliError) return error;
  if (error instanceof ActiveIncidentError || error instanceof IncidentDetectedError) {
    return new CliError(INCIDENT_ACTIVE_CODE, error.message, {
      details: {
        kind: error.incident.kind,
        reason: error.incident.reason,
        detail: error.incident.detail,
        opened_at: error.incident.opened_at,
      },
      exitCode: INCIDENT_ACTIVE_EXIT_CODE,
    });
  }
  return new CliError(
    "OPERATION_FAILED",
    error instanceof Error ? error.message : "unknown operation failure",
    { exitCode: 1 },
  );
}

if (import.meta.main) process.exitCode = await run(process.argv.slice(2));
