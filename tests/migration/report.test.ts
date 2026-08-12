import { Database } from "bun:sqlite";
import { afterEach, describe, expect, test } from "bun:test";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ALLISON_INCIDENT,
  AUGUST_2_RUN,
  buildMigrationReport,
  readLegacySqlite,
  type SnapshotExpectation,
} from "../../src/migration/index.ts";

const roots: string[] = [];
const writers: Database[] = [];

afterEach(() => {
  for (const writer of writers.splice(0)) writer.close();
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

const SYNTHETIC_EXPECTED: SnapshotExpectation = {
  snapshotId: "synthetic-migration-fixture",
  documentedAt: "2026-08-03T00:00:00Z",
  durable: 2,
  connected: 1,
  aliases: 2,
  unresolved: 3,
  august2Unresolved: 2,
  crossWorkflowSuppressions: 2,
  sourceDocuments: [
    {
      fields: ["durable", "unresolved", "august2Unresolved", "connected"],
      source: "synthetic network.sqlite",
      contract: "test fixture rows",
    },
    {
      fields: ["aliases", "crossWorkflowSuppressions"],
      source: "synthetic lead-ledger.json",
      contract: "test fixture records",
    },
  ],
};

type Fixture = { root: string; databasePath: string; writer: Database | null };

function fixture(options: { wal?: boolean } = {}): Fixture {
  const wal = options.wal ?? true;
  const root = mkdtempSync(join(tmpdir(), "linkedin-migration-"));
  roots.push(root);
  mkdirSync(join(root, "parked-network-runs"));
  const databasePath = join(root, "network.sqlite");
  const db = new Database(databasePath, { create: true });
  if (wal) {
    db.exec("PRAGMA journal_mode = WAL; PRAGMA wal_autocheckpoint = 0;");
  }
  db.exec(`
    CREATE TABLE send_ledger_entries (entry_id TEXT PRIMARY KEY, attempt_key TEXT, run_id TEXT, run_date TEXT, source TEXT, name TEXT, profile_url TEXT, public_profile_url TEXT, attempted_at TEXT, confirmed_at TEXT, status TEXT, durable INTEGER, reason TEXT, event_kind TEXT, result_path TEXT, raw_json TEXT);
    CREATE TABLE acceptance_invitations (key TEXT PRIMARY KEY, profile_url TEXT, public_profile_url TEXT, latest_status TEXT, current_relationship_status TEXT);
  `);
  if (wal) db.exec("PRAGMA wal_checkpoint(TRUNCATE);");
  const insert = db.query(
    "INSERT INTO send_ledger_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, 'test', NULL, '{}')",
  );
  db.transaction(() => {
    for (let index = 0; index < 3; index++) {
      insert.run(
        `unresolved-${index}`,
        `attempt-${index}`,
        index < 2 ? AUGUST_2_RUN : "older",
        "2026-08-02",
        "source",
        "Synthetic",
        `https://www.linkedin.com/sales/lead/SYN${index}`,
        null,
        `2026-08-02T00:00:0${index}Z`,
        "pending-provisional",
        0,
      );
    }
    for (let index = 0; index < 2; index++) {
      insert.run(
        `durable-${index}`,
        `durable-attempt-${index}`,
        "durable-run",
        "2026-08-01",
        "source",
        "Synthetic",
        `https://www.linkedin.com/sales/lead/DURABLE${index}`,
        null,
        `2026-08-01T00:00:0${index}Z`,
        index === 0 ? "pending" : "accepted",
        1,
      );
    }
    insert.run(
      "failed",
      "failed",
      "run",
      "2026-01-01",
      "source",
      "Synthetic",
      "https://www.linkedin.com/sales/lead/FAILED",
      null,
      "2026-01-01T00:00:00Z",
      "failed",
      0,
    );
    db.query("INSERT INTO acceptance_invitations VALUES (?, ?, ?, ?, ?)").run(
      "connected-1",
      "https://www.linkedin.com/sales/lead/CONNECTED",
      null,
      "accepted",
      "first-degree",
    );
  })();

  const leads: Record<string, unknown> = {};
  for (let index = 0; index < 2; index++) {
    leads[`lead-${index}`] = {
      lead_key: `lead-${index}`,
      profile_url: `https://www.linkedin.com/sales/lead/CROSS${index}`,
      public_profile_url: `https://www.linkedin.com/in/synthetic-${index}`,
      status: "skipped",
      status_reason: `cross-workflow suppression: outreach message already sent to Synthetic ${index} (sent); evidence=test`,
    };
  }
  writeFileSync(join(root, "lead-ledger.json"), JSON.stringify({ leads }));
  writeFileSync(
    join(root, "active.json"),
    JSON.stringify({
      browser_incidents: [
        {
          id: ALLISON_INCIDENT,
          possible_send: true,
          status: "audit_required",
          profile_url: "https://www.linkedin.com/sales/lead/SYN0",
        },
      ],
    }),
  );
  if (wal) {
    writers.push(db);
    return { root, databasePath, writer: db };
  }
  db.close();
  return { root, databasePath, writer: null };
}

describe("migration report", () => {
  test("reads committed rows that exist only in an active WAL", () => {
    const { root, databasePath } = fixture();
    const mainOnly = join(root, "main-only.sqlite");
    copyFileSync(databasePath, mainOnly);
    const withoutWal = new Database(mainOnly, { create: true });
    try {
      expect(
        (
          withoutWal.query("SELECT count(*) AS count FROM send_ledger_entries").get() as {
            count: number;
          }
        ).count,
      ).toBe(0);
    } finally {
      withoutWal.close();
    }

    const read = readLegacySqlite(databasePath);
    expect(read.latestAttempts).toHaveLength(6);
    expect(read.wal.journalMode).toBe("wal");
    expect(read.wal.walFilePresent).toBeTrue();
    expect(read.wal.walFrameCount).toBeGreaterThan(0);
    expect(read.wal.walRowsVisible).toBeTrue();
  });

  test("reports a non-WAL fixture without claiming WAL visibility", () => {
    const { databasePath } = fixture({ wal: false });
    const read = readLegacySqlite(databasePath);
    expect(read.wal.journalMode).not.toBe("wal");
    expect(read.wal.walRowsVisible).toBeFalse();
  });

  test("enforces exact totals, valid evidence, and linked Allison evidence", () => {
    const { root } = fixture();
    const report = buildMigrationReport(root, SYNTHETIC_EXPECTED);
    expect(report.assertions.passed).toBeTrue();
    expect(report.assertions.observed).toMatchObject({
      durable: 2,
      connected: 1,
      aliases: 2,
      unresolved: 3,
      august2Unresolved: 2,
      crossWorkflowSuppressions: 2,
      allisonPossibleSend: true,
      allisonLinked: true,
      warnings: 0,
      orphanIdentities: 0,
      invalidEvidenceReferences: 0,
      forbiddenCategories: 0,
    });
    const allison = report.proposals.find(
      (proposal) =>
        proposal.kind === "unresolved_send" && proposal.identity.canonicalKey === "sales:SYN0",
    );
    expect(allison?.evidence).toHaveLength(2);
    expect(
      allison?.evidence.some((item) => item.key.startsWith("send_ledger_entries:")),
    ).toBeTrue();
    expect(allison?.evidence.some((item) => item.key.includes(ALLISON_INCIDENT))).toBeTrue();
    expect(
      report.proposals.every((proposal) =>
        proposal.evidence.every((item) => existsSync(item.path)),
      ),
    ).toBeTrue();
    expect(
      report.proposals.some((proposal) => proposal.identity.canonicalKey === "sales:FAILED"),
    ).toBeFalse();
  });

  test("fails when Allison cannot link to the exact August 2 unresolved proposal", () => {
    const { root } = fixture();
    writeFileSync(
      join(root, "active.json"),
      JSON.stringify({
        browser_incidents: [
          {
            id: ALLISON_INCIDENT,
            possible_send: true,
            status: "audit_required",
            profile_url: "https://www.linkedin.com/sales/lead/DIFFERENT",
          },
        ],
      }),
    );
    const report = buildMigrationReport(root, SYNTHETIC_EXPECTED);
    expect(report.assertions.passed).toBeFalse();
    expect(report.assertions.observed.allisonLinked).toBeFalse();
    expect(report.assertions.failures).toContain(
      "Allison incident is not linked to the exact August 2 unresolved proposal",
    );
    expect(report.assertions.failures).toContain("Expected zero warnings; observed 1");
  });

  test("fails on exact-count drift, warnings, or orphan identities", () => {
    const { root, writer } = fixture();
    writer
      ?.query(
        "INSERT INTO send_ledger_entries VALUES ('orphan', 'orphan', 'run', '2026-08-01', 'source', 'Synthetic', NULL, NULL, '2026-08-01T01:00:00Z', NULL, 'pending', 1, NULL, 'test', NULL, '{}')",
      )
      .run();
    const report = buildMigrationReport(root, SYNTHETIC_EXPECTED);
    expect(report.assertions.passed).toBeFalse();
    expect(report.assertions.observed.orphanIdentities).toBe(1);
    expect(report.assertions.failures).toContain("Expected zero orphan identities; observed 1");
  });

  test("fails when an approved-category total drifts from the documented snapshot", () => {
    const { root, writer } = fixture();
    writer
      ?.query(
        "INSERT INTO send_ledger_entries VALUES ('extra', 'extra', 'run', '2026-08-01', 'source', 'Synthetic', 'https://www.linkedin.com/sales/lead/EXTRA', NULL, '2026-08-01T01:00:00Z', NULL, 'pending', 1, NULL, 'test', NULL, '{}')",
      )
      .run();
    const report = buildMigrationReport(root, SYNTHETIC_EXPECTED);
    expect(report.assertions.passed).toBeFalse();
    expect(report.assertions.observed.durable).toBe(3);
    expect(report.assertions.failures).toContain("Expected 2 durable; observed 3");
  });

  test("is deterministic", () => {
    const { root } = fixture();
    expect(JSON.stringify(buildMigrationReport(root, SYNTHETIC_EXPECTED))).toBe(
      JSON.stringify(buildMigrationReport(root, SYNTHETIC_EXPECTED)),
    );
  });
});
