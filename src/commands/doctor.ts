import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { activeIncident, type LinkedInIncident } from "../browser/incident.ts";
import { bootstrapDatabase } from "../db/database.ts";
import type { DoctorInput } from "./types.ts";

export type DoctorDependencies = {
  readonly bootstrapDatabase: typeof bootstrapDatabase;
  readonly createProbeId: () => string;
};

const defaultDependencies: DoctorDependencies = {
  bootstrapDatabase,
  createProbeId: () => crypto.randomUUID(),
};

export type DoctorResult = {
  readonly command: "doctor";
  readonly ready: boolean;
  readonly runtime: {
    readonly name: "bun";
    readonly version: string;
    readonly ok: true;
  };
  readonly browser: {
    readonly boundary: "codex_chrome";
    readonly callerOwned: true;
  };
  readonly state: {
    readonly root: string;
    readonly database: string;
    readonly reports: string;
    readonly downloads: string;
    readonly receipts: string;
    readonly logs: string;
    readonly writable: boolean;
    readonly error: string | null;
  };
  readonly sqlite: {
    readonly engine: "bun:sqlite";
    readonly ok: boolean;
    readonly migrationVersion: number | null;
    readonly applied: readonly string[];
    readonly error: string | null;
  };
  readonly incident: {
    readonly active: boolean;
    readonly path: string;
    readonly record: LinkedInIncident | null;
    readonly error: string | null;
  };
};

export async function doctor(
  input: DoctorInput,
  dependencies: DoctorDependencies = defaultDependencies,
): Promise<DoctorResult> {
  const paths = {
    database: join(input.stateDir, "linkedin-tools.db"),
    reports: join(input.stateDir, "reports"),
    downloads: join(input.stateDir, "downloads"),
    receipts: join(input.stateDir, "receipts"),
    logs: join(input.stateDir, "logs"),
  };

  let writable = false;
  let stateError: string | null = null;
  try {
    await Promise.all([
      mkdir(input.stateDir, { recursive: true }),
      mkdir(paths.reports, { recursive: true }),
      mkdir(paths.downloads, { recursive: true }),
      mkdir(paths.receipts, { recursive: true }),
      mkdir(paths.logs, { recursive: true }),
    ]);
    const probe = join(input.stateDir, `.doctor-write-probe-${dependencies.createProbeId()}`);
    await writeFile(probe, "", { flag: "wx" });
    await rm(probe);
    writable = true;
  } catch (error) {
    stateError = message(error);
  }

  let sqliteOk = false;
  let migrationVersion: number | null = null;
  let applied: readonly string[] = [];
  let sqliteError: string | null = null;
  if (writable) {
    try {
      const database = dependencies.bootstrapDatabase(paths.database);
      sqliteOk = true;
      migrationVersion = database.currentVersion;
      applied = [...database.applied];
    } catch (error) {
      sqliteError = message(error);
    }
  } else {
    sqliteError = "state directory is not writable";
  }

  let incidentActive = false;
  let incidentRecord: LinkedInIncident | null = null;
  let incidentError: string | null = null;
  const incidentFile = join(input.stateDir, "linkedin-incident.json");
  try {
    incidentRecord = await activeIncident(input.stateDir);
    incidentActive = incidentRecord !== null;
  } catch (error) {
    incidentError = message(error);
  }

  const ready = writable && sqliteOk && !incidentActive && incidentError === null;

  return {
    command: "doctor",
    ready,
    runtime: { name: "bun", version: Bun.version, ok: true },
    browser: { boundary: "codex_chrome", callerOwned: true },
    state: {
      root: input.stateDir,
      ...paths,
      writable,
      error: stateError,
    },
    sqlite: {
      engine: "bun:sqlite",
      ok: sqliteOk,
      migrationVersion,
      applied,
      error: sqliteError,
    },
    incident: {
      active: incidentActive,
      path: incidentFile,
      record: incidentRecord,
      error: incidentError,
    },
  };
}

function message(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).slice(0, 500);
}
