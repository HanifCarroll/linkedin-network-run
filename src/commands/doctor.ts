import { constants } from "node:fs";
import { access, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { activeIncident, type LinkedInIncident } from "../browser/incident.ts";
import { bootstrapDatabase } from "../db/database.ts";
import { SOURCES } from "../network/config.ts";
import { extensionHealth } from "./incident.ts";
import type { DoctorInput } from "./types.ts";

type CommandReceipt = {
  readonly exitCode: number;
  readonly stdout: string;
  readonly stderr: string;
};

export type DoctorDependencies = {
  readonly bootstrapDatabase: typeof bootstrapDatabase;
  readonly run: (command: readonly string[]) => Promise<CommandReceipt>;
  readonly createProbeId: () => string;
};

const defaultDependencies: DoctorDependencies = {
  bootstrapDatabase,
  run: async (command) => {
    const child = Bun.spawn([...command], { stdout: "pipe", stderr: "pipe" });
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
      child.exited,
    ]);
    return { stdout, stderr, exitCode };
  },
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
    readonly boundary: "playwriter";
    readonly noDirectPlaywright: true;
    readonly noDirectCdp: true;
    readonly noLease: true;
    readonly binary: {
      readonly path: string;
      readonly exists: boolean;
      readonly executable: boolean;
      readonly version: string | null;
      readonly error: string | null;
    };
    readonly sessions: {
      readonly network: SessionCheck;
      readonly analytics: SessionCheck;
      readonly listingChecked: boolean;
      readonly error: string | null;
    };
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
  readonly playwriter: {
    readonly extensionConnected: boolean | null;
    readonly trackedTabCount: number | null;
    readonly lastDisconnectAt: string | null;
    readonly recentDisconnects: number;
    readonly hasNetworkPage: boolean | null;
    readonly guidance: readonly string[];
    readonly error: string | null;
  };
  readonly contract: {
    readonly automationPromptPath: string | null;
    readonly promptFound: boolean;
    readonly configuredSources: readonly string[];
    readonly promptSources: readonly string[];
    readonly drift: boolean;
    readonly error: string | null;
  };
};

type SessionCheck = {
  readonly configured: boolean;
  readonly id: number | null;
  readonly active: boolean | null;
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

  let binaryExists = false;
  let binaryExecutable = false;
  let binaryVersion: string | null = null;
  let binaryError: string | null = null;
  try {
    const details = await stat(input.playwriterBin);
    binaryExists = details.isFile();
    if (binaryExists) {
      await access(input.playwriterBin, constants.X_OK);
      binaryExecutable = true;
      const version = await dependencies.run([input.playwriterBin, "--version"]);
      if (version.exitCode === 0) binaryVersion = firstNonemptyLine(version);
      else binaryError = conciseCommandError(version);
    }
  } catch (error) {
    binaryError = message(error);
  }

  let activeSessions: readonly number[] = [];
  let sessionListingChecked = false;
  let sessionError: string | null = null;
  const configuredSessions = [input.networkSessionId, input.analyticsSessionId].filter(
    (value): value is number => value !== undefined,
  );
  if (binaryExecutable && configuredSessions.length > 0) {
    try {
      const listing = await dependencies.run([input.playwriterBin, "session", "list"]);
      sessionListingChecked = true;
      if (listing.exitCode !== 0) sessionError = conciseCommandError(listing);
      else activeSessions = parseSessionIds(listing.stdout);
    } catch (error) {
      sessionError = message(error);
    }
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

  const network = sessionCheck(input.networkSessionId, activeSessions, sessionListingChecked);
  const analytics = sessionCheck(input.analyticsSessionId, activeSessions, sessionListingChecked);
  const sessionsReady =
    network.configured &&
    analytics.configured &&
    network.active === true &&
    analytics.active === true;
  const ready =
    writable &&
    sqliteOk &&
    binaryExists &&
    binaryExecutable &&
    binaryVersion !== null &&
    sessionError === null &&
    sessionsReady &&
    !incidentActive &&
    incidentError === null;

  const playwriter = await extensionHealth();
  const hasNetworkPage = (() => {
    if (playwriter.connected !== true || playwriter.trackedTabCount === null) return null;
    return playwriter.trackedTabCount > 0;
  })();
  const guidance: string[] = [];
  if (playwriter.connected !== true) {
    guidance.push(
      "playwriter extension is not connected; open the Sales Navigator search tab and click the playwriter extension icon on it",
    );
  } else if (hasNetworkPage !== true) {
    guidance.push(
      "extension is connected but no tracked tabs remain; open a Sales Navigator search tab so the walk has a page to reuse",
    );
  }
  if (playwriter.recentDisconnects > 0) {
    guidance.push(
      `extension disconnected ${playwriter.recentDisconnects} time(s) recently; a fresh relay restart may stabilize the CDP transport`,
    );
  }

  return {
    command: "doctor",
    ready,
    runtime: { name: "bun", version: Bun.version, ok: true },
    browser: {
      boundary: "playwriter",
      noDirectPlaywright: true,
      noDirectCdp: true,
      noLease: true,
      binary: {
        path: input.playwriterBin,
        exists: binaryExists,
        executable: binaryExecutable,
        version: binaryVersion,
        error: binaryError,
      },
      sessions: {
        network,
        analytics,
        listingChecked: sessionListingChecked,
        error: sessionError,
      },
    },
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
    playwriter: {
      extensionConnected: playwriter.connected,
      trackedTabCount: playwriter.trackedTabCount,
      lastDisconnectAt: playwriter.lastDisconnectAt,
      recentDisconnects: playwriter.recentDisconnects,
      hasNetworkPage,
      guidance,
      error: playwriter.error,
    },
    contract: await automationContractCheck(input.automationPromptPath),
  };
}

async function automationContractCheck(
  automationPromptPath: string | undefined,
): Promise<DoctorResult["contract"]> {
  const configuredSources = SOURCES.map((source) => source.name);
  if (automationPromptPath === undefined) {
    return {
      automationPromptPath: null,
      promptFound: false,
      configuredSources,
      promptSources: [],
      drift: false,
      error: "automation prompt path not configured for drift checks",
    };
  }
  let text: string;
  try {
    text = await readFile(automationPromptPath, "utf8");
  } catch (error) {
    const code = (error as { code?: string }).code;
    if (code === "ENOENT") {
      return {
        automationPromptPath,
        promptFound: false,
        configuredSources,
        promptSources: [],
        drift: false,
        error: null,
      };
    }
    return {
      automationPromptPath,
      promptFound: false,
      configuredSources,
      promptSources: [],
      drift: false,
      error: message(error),
    };
  }
  const promptSources = configuredSources.filter((name) => text.includes(name));
  const drift = promptSources.length !== configuredSources.length;
  return {
    automationPromptPath,
    promptFound: true,
    configuredSources,
    promptSources,
    drift,
    error: null,
  };
}

function sessionCheck(
  configuredId: number | undefined,
  activeIds: readonly number[],
  checked: boolean,
): SessionCheck {
  return {
    configured: configuredId !== undefined,
    id: configuredId ?? null,
    active: configuredId === undefined || !checked ? null : activeIds.includes(configuredId),
  };
}

function parseSessionIds(stdout: string): readonly number[] {
  return stdout
    .split(/\r?\n/)
    .map((line) => /^\s*(\d+)\b/.exec(line)?.[1])
    .filter((value): value is string => value !== undefined)
    .map(Number)
    .filter((value) => Number.isSafeInteger(value) && value > 0)
    .sort((left, right) => left - right);
}

function conciseCommandError(receipt: CommandReceipt): string {
  return (receipt.stderr.trim() || receipt.stdout.trim() || `exit ${receipt.exitCode}`).slice(
    0,
    500,
  );
}

function firstNonemptyLine(receipt: CommandReceipt): string | null {
  const output = receipt.stdout.trim() || receipt.stderr.trim();
  return (
    output
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line.length > 0) ?? null
  );
}

function message(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).slice(0, 500);
}
