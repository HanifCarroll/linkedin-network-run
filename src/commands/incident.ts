import { readdir, readFile, rm, stat } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { activeIncident, clearIncident, type LinkedInIncident } from "../browser/incident.ts";
import { CliError } from "../core/errors.ts";
import type { NetworkIncidentClearInput, NetworkIncidentStatusInput } from "./types.ts";

export type RecentReceipt = {
  readonly invocationId: string;
  readonly command: string;
  readonly outcome: string;
  readonly exitCode: number;
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
  readonly blocker: { readonly kind: string; readonly evidence: string } | null;
  readonly stderrTail: string;
};

export type ExtensionHealth = {
  readonly relayLogPath: string;
  readonly relayLogExists: boolean;
  readonly connected: boolean | null;
  readonly lastConnectAt: string | null;
  readonly lastDisconnectAt: string | null;
  readonly recentDisconnects: number;
  readonly trackedTabCount: number | null;
  readonly error: string | null;
};

export type IncidentStatusResult = {
  readonly command: "network incident-status";
  readonly active: boolean;
  readonly incident: LinkedInIncident | null;
  readonly recentReceipt: RecentReceipt | null;
  readonly extension: ExtensionHealth;
  readonly pruned: {
    readonly enabled: boolean;
    readonly receiptsRemoved: number;
    readonly evidenceRemoved: number;
    readonly bytesFreed: number;
    readonly error: string | null;
  };
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
  const [recentReceipt, extension] = await Promise.all([
    mostRecentReceipt(input.stateDir),
    extensionHealth(),
  ]);
  const pruned =
    input.pruneDays === undefined
      ? { enabled: false, receiptsRemoved: 0, evidenceRemoved: 0, bytesFreed: 0, error: null }
      : await pruneStaleArtifacts(input.stateDir, input.pruneDays);
  return {
    command: "network incident-status",
    active: incident !== null,
    incident,
    recentReceipt,
    extension,
    pruned,
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

async function mostRecentReceipt(stateDir: string): Promise<RecentReceipt | null> {
  const root = join(stateDir, "receipts", "playwriter", "network");
  let entries: readonly import("node:fs").Dirent[];
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch {
    return null;
  }
  let newest: { readonly dir: string; readonly mtimeMs: number } | null = null;
  for (const entry of entries) {
    if (!entry.isDirectory() || !/^pw_[a-f0-9]{32}$/.test(entry.name)) continue;
    const receiptPath = join(root, entry.name, "receipt.json");
    try {
      const info = await stat(receiptPath);
      if (newest === null || info.mtimeMs > newest.mtimeMs) {
        newest = { dir: entry.name, mtimeMs: info.mtimeMs };
      }
    } catch {
      // no receipt.json yet; not a completed invocation
    }
  }
  if (newest === null) return null;
  const directory = join(root, newest.dir);
  let receipt: Record<string, unknown> | null = null;
  try {
    receipt = JSON.parse(await readFile(join(directory, "receipt.json"), "utf8"));
  } catch {
    receipt = null;
  }
  let stderrTail = "";
  try {
    const stderr = await readFile(join(directory, "stderr.log"), "utf8");
    stderrTail = stderr.trim().split("\n").filter(Boolean).slice(-3).join("\n").slice(0, 600);
  } catch {
    stderrTail = "";
  }
  const blockerValue = receipt?.blocker;
  const blocker =
    blockerValue !== null && typeof blockerValue === "object" && blockerValue !== undefined
      ? {
          kind: String((blockerValue as Record<string, unknown>).kind ?? ""),
          evidence: String((blockerValue as Record<string, unknown>).evidence ?? ""),
        }
      : null;
  return {
    invocationId: newest.dir,
    command: String(receipt?.command ?? ""),
    outcome: String(receipt?.outcome ?? ""),
    exitCode: Number(receipt?.exitCode ?? -1),
    startedAt: typeof receipt?.startedAt === "string" ? receipt.startedAt : null,
    finishedAt: typeof receipt?.finishedAt === "string" ? receipt.finishedAt : null,
    blocker,
    stderrTail,
  };
}

const RELAY_LOG_CANDIDATES = [() => join(homedir(), ".playwriter", "relay-server.log")];

export async function extensionHealth(): Promise<ExtensionHealth> {
  const relayLogCandidate = RELAY_LOG_CANDIDATES[0];
  const relayLogPath = relayLogCandidate === undefined ? "" : relayLogCandidate();
  let connected: boolean | null = null;
  let lastConnectAt: string | null = null;
  let lastDisconnectAt: string | null = null;
  let recentDisconnects = 0;
  let trackedTabCount: number | null = null;
  let error: string | null = null;
  let tail = "";
  try {
    const info = await stat(relayLogPath);
    if (!info.isFile()) {
      return {
        relayLogPath,
        relayLogExists: false,
        connected: null,
        lastConnectAt: null,
        lastDisconnectAt: null,
        recentDisconnects: 0,
        trackedTabCount: null,
        error: null,
      };
    }
    const handle = await Bun.file(relayLogPath).text();
    tail = handle;
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }
  if (tail.length > 0) {
    const lines = tail.split("\n").slice(-400);
    for (const line of lines) {
      const connectedMatch = /Extension connected \(([^)]+)\)/.exec(line);
      if (connectedMatch) {
        connected = true;
        lastConnectAt = timestampFromLogLine(line) ?? lastConnectAt;
        continue;
      }
      const disconnectedMatch = /Extension disconnected: code=\d+/.exec(line);
      if (disconnectedMatch) {
        recentDisconnects += 1;
        lastDisconnectAt = timestampFromLogLine(line) ?? lastDisconnectAt;
        // A later "Extension connected" resets connected back to true.
        connected = false;
        continue;
      }
      const tabsMatch = /\[Extension\] \[LOG\] (\{.*\})/.exec(line);
      const tabsJson = tabsMatch === null ? undefined : tabsMatch[1];
      if (tabsJson !== undefined && connected) {
        try {
          const parsed = JSON.parse(tabsJson) as {
            readonly tabs?: { readonly value?: readonly unknown[] };
          };
          const value = parsed.tabs?.value;
          if (Array.isArray(value)) trackedTabCount = value.length;
        } catch {
          // non-JSON log line; keep the last known count
        }
      }
    }
  }
  return {
    relayLogPath,
    relayLogExists: tail.length > 0 || error === null,
    connected,
    lastConnectAt,
    lastDisconnectAt,
    recentDisconnects,
    trackedTabCount,
    error,
  };
}

function timestampFromLogLine(line: string): string | null {
  const match = /^\S+\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z?)/.exec(line);
  const first = match === null ? undefined : match[1];
  if (first !== undefined) return first;
  const dateMatch = /\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\b/.exec(line);
  const second = dateMatch === null ? undefined : dateMatch[1];
  return second === undefined ? null : second;
}

type PruneResult = {
  readonly enabled: true;
  readonly receiptsRemoved: number;
  readonly evidenceRemoved: number;
  readonly bytesFreed: number;
  readonly error: string | null;
};

async function pruneStaleArtifacts(stateDir: string, pruneDays: number): Promise<PruneResult> {
  const cutoffMs = Date.now() - pruneDays * 24 * 60 * 60 * 1000;
  let receiptsRemoved = 0;
  let evidenceRemoved = 0;
  let bytesFreed = 0;
  let error: string | null = null;

  const receiptRoot = join(stateDir, "receipts", "playwriter", "network");
  try {
    const entries = await readdir(receiptRoot, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() || !/^pw_[a-f0-9]{32}$/.test(entry.name)) continue;
      const directory = join(receiptRoot, entry.name);
      try {
        const info = await stat(directory);
        if (info.mtimeMs >= cutoffMs) continue;
        bytesFreed += await directoryBytes(directory);
        await rmRecursive(directory);
        receiptsRemoved += 1;
      } catch {
        // unreadable or already removed; skip
      }
    }
  } catch (cause) {
    error = cause instanceof Error ? cause.message : String(cause);
  }

  // Preserved evidence handoffs live under the temp playwriter owner root
  // (createEvidenceHandoff in client.ts) as <invocationId>_<nonce> dirs.
  const ownerRoot = join(tmpdir(), "linkedin-tools-next-playwriter");
  try {
    const entries = await readdir(ownerRoot, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() || !/^pw_[a-f0-9]{32}_[a-f0-9]{32}$/.test(entry.name)) continue;
      const directory = join(ownerRoot, entry.name);
      try {
        const info = await stat(directory);
        if (info.mtimeMs >= cutoffMs) continue;
        bytesFreed += await directoryBytes(directory);
        await rmRecursive(directory);
        evidenceRemoved += 1;
      } catch {
        // skip
      }
    }
  } catch {
    // owner root may not exist; nothing to prune
  }

  return { enabled: true, receiptsRemoved, evidenceRemoved, bytesFreed, error };
}

async function directoryBytes(directory: string): Promise<number> {
  let total = 0;
  try {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) {
        total += await directoryBytes(path);
      } else if (entry.isFile()) {
        try {
          total += (await stat(path)).size;
        } catch {
          // skip
        }
      }
    }
  } catch {
    // skip
  }
  return total;
}

async function rmRecursive(path: string): Promise<void> {
  await rm(path, { recursive: true, force: true });
}
