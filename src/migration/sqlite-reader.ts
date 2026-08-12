import { Database } from "bun:sqlite";
import { closeSync, existsSync, openSync, readSync, statSync } from "node:fs";
import type { WalEvidence } from "./types.ts";

export type SendLedgerRow = {
  entry_id: string;
  attempt_key: string;
  run_id: string;
  run_date: string | null;
  source: string;
  profile_url: string | null;
  public_profile_url: string | null;
  attempted_at: string;
  status: string;
  durable: number;
};

export type AcceptanceRow = {
  key: string;
  profile_url: string | null;
  public_profile_url: string | null;
  latest_status: string;
  current_relationship_status: string;
};

export type LegacySqlite = {
  latestAttempts: SendLedgerRow[];
  connected: AcceptanceRow[];
  evidenceKeys: Set<string>;
  wal: WalEvidence;
};

function inspectWal(databasePath: string, journalMode: string): WalEvidence {
  const walPath = `${databasePath}-wal`;
  const walFilePresent = existsSync(walPath) && statSync(walPath).size >= 32;
  let walFrameCount = 0;
  if (walFilePresent) {
    const descriptor = openSync(walPath, "r");
    try {
      const header = Buffer.alloc(32);
      if (readSync(descriptor, header, 0, header.length, 0) === header.length) {
        const magic = header.readUInt32BE(0);
        const rawPageSize = header.readUInt32BE(8);
        const pageSize = rawPageSize === 1 ? 65_536 : rawPageSize;
        const validMagic = magic === 0x377f0682 || magic === 0x377f0683;
        if (validMagic && pageSize > 0) {
          walFrameCount = Math.floor((statSync(walPath).size - 32) / (pageSize + 24));
        }
      }
    } finally {
      closeSync(descriptor);
    }
  }
  return {
    journalMode,
    walPath,
    walFilePresent,
    walFrameCount,
    walRowsVisible: journalMode === "wal" && walFilePresent && walFrameCount > 0,
  };
}

export function readLegacySqlite(path: string): LegacySqlite {
  const database = new Database(path, { readonly: true });
  try {
    database.exec("PRAGMA query_only = ON;");
    const queryOnly = database.query("PRAGMA query_only").get() as { query_only?: number } | null;
    if (queryOnly?.query_only !== 1)
      throw new Error("Legacy database did not enter query-only mode");
    const journal = database.query("PRAGMA journal_mode").get() as { journal_mode?: string } | null;
    const journalMode = journal?.journal_mode?.toLowerCase() ?? "unknown";

    const latestAttempts = database
      .query<SendLedgerRow, []>(`
        WITH ranked AS (
          SELECT *, row_number() OVER (
            PARTITION BY attempt_key ORDER BY attempted_at DESC, rowid DESC
          ) AS rn
          FROM send_ledger_entries
        )
        SELECT entry_id, attempt_key, run_id, run_date, source, profile_url,
               public_profile_url, attempted_at, status, durable
        FROM ranked WHERE rn = 1
        ORDER BY attempt_key
      `)
      .all();

    const connected = database
      .query<AcceptanceRow, []>(`
        SELECT key, profile_url, public_profile_url, latest_status,
               current_relationship_status
        FROM acceptance_invitations
        WHERE lower(latest_status) IN ('accepted', 'first-degree', 'first_degree')
           OR lower(current_relationship_status) IN ('accepted', 'first-degree', 'first_degree')
        ORDER BY key
      `)
      .all();

    return {
      latestAttempts,
      connected,
      evidenceKeys: new Set([
        ...latestAttempts.map((row) => `send_ledger_entries:${row.entry_id}`),
        ...connected.map((row) => `acceptance_invitations:${row.key}`),
      ]),
      wal: inspectWal(path, journalMode),
    };
  } finally {
    database.close();
  }
}
